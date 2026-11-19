"""Host-side scholarly retrieval for TheoryMaster Literature tasks.

External transport belongs to the SDK/runner, not to the LLM Agent sandbox.
The Literature Agent receives local artifacts produced here and performs
scientific verification/interpretation without opening network sockets itself.
"""
from __future__ import annotations

import json
import subprocess
import csv
from datetime import datetime, timezone
import os
import signal
import time
import sys
from pathlib import Path
from typing import Any, Callable

CONDA_PYTHON = Path(os.environ.get("THEORYMASTER_SCHOLARLY_PYTHON") or sys.executable)
from .skills import skills_root
from .scholarly_request import (
    pin_scholarly_transport, retrieval_directory, scholarly_request_identity,
)

# Installed wheels contain the bundled resource mirror, not the source-tree skills/ directory.
SCRIPTS_DIR = skills_root() / "tm-scholar" / "scripts"
RETRIEVE_SCRIPT = SCRIPTS_DIR / "lit_retrieve.py"
READ_SCRIPT = SCRIPTS_DIR / "read_papers.py"
TRACE_SCRIPT = SCRIPTS_DIR / "trace_paper.py"

from .scholarly_selection import (
    _ascii_focus, _load_json, _paper_key, _proper_name_tokens, _query_variants,
    _slug, _source_lock_record, _targeted_relevance, _write_targeted_index,
    _domain_alignment, _write_evidence_index,
    _reconcile_locked_source, _clean_metadata_record, _landscape_relevance,
    _identity_helpers,
)


def _run_scholarly_command(command, *, capture_output=True, text=True, timeout=150, check=False, cancel_event=None):
    """Bound a whole reader/converter tree, including inherited output pipes."""
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Scholarly acquisition cancelled")
    process = subprocess.Popen(command, stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None, text=text, start_new_session=True)

    def kill_tree():
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass

    try:
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Scholarly acquisition cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        kill_tree()
        process.communicate(timeout=5)
        raise
    # A successful CLI must also leave no background converter descendants.
    kill_tree()
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _reading_timeout(timeout_s: int) -> int:
    timeout = max(timeout_s, 180)
    from .document_conversion import load_settings
    settings = load_settings()
    if settings.get("enabled"):
        # The acquisition clock stops new URL attempts; one already-started
        # conversion is still entitled to its explicitly configured timeout.
        timeout += int(settings.get("timeout_s") or 240) + 30
    return timeout

def retrieve_scholarly(
    task: dict[str, Any],
    workspace: Path,
    *,
    timeout_s: int = 150,
    runner: Callable[..., Any] = _run_scholarly_command,
    cancel_event=None,
) -> dict[str, Any]:
    """Execute reproducible scholarly retrieval in the host runner process."""
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Scholarly acquisition cancelled")
    if runner is _run_scholarly_command and cancel_event is not None:
        def run_with_cancellation(command, **kwargs):
            return _run_scholarly_command(command, cancel_event=cancel_event, **kwargs)
        runner = run_with_cancellation
    request_identity = scholarly_request_identity(task)
    request = request_identity["request"]
    source_gap_id = request["source_gap_id"]
    task_type = request["task_type"]
    search_target = request["search_target"]
    domain_context = request["domain_context"]
    source_lock = request["source_lock"]
    query_variants = request["search_queries"]
    query = query_variants[0]

    root = retrieval_directory(workspace, request_identity)
    retrieval_dir = root / "retrieval"
    reading_dir = root / "reading"
    retrieval_dir.mkdir(parents=True, exist_ok=True)

    if not CONDA_PYTHON.is_file() or not RETRIEVE_SCRIPT.is_file():
        return {
            "schema": "theory-master/scholarly-transport/v1",
            "status": "blocked", "transport_ok": False,
            "source_gap_id": source_gap_id, "task_type": task_type, "query": query,
            "reason": "scholarly runtime or retrieval script is unavailable",
            "output_dir": str(root),
        }

    sources = "openalex,crossref,semanticscholar"
    if task_type in {"landscape", "novelty_check"}:
        sources = "openalex,crossref,semanticscholar,pubmed,arxiv"
    limit = "30" if task_type == "landscape" else "20"
    merged: list[dict[str, Any]] = []
    query_runs: list[dict[str, Any]] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    requested_sources = [item for item in sources.split(",") if item]
    blocked_runs = 0
    returncodes: list[int] = []
    quality_rejections: list[dict[str, Any]] = []
    raw_metadata_count = 0
    identity = _identity_helpers()
    # A requested source is not a provider observation. Keep it out of the
    # candidate merge so a colliding title/identifier cannot overwrite its
    # identity before the explicit source-lock reconciliation below.
    locked_record = _source_lock_record(source_lock) if source_lock else None
    for index, query_item in enumerate(query_variants, 1):
        run_dir = retrieval_dir / f"query-{index:02d}"
        command = [
            str(CONDA_PYTHON), str(RETRIEVE_SCRIPT),
            "--query", query_item,
            "--from-year", "1900", "--to-year", str(datetime.now(timezone.utc).year),
            "--limit", limit,
            "--sources", sources,
            "--output-dir", str(run_dir),
            "--report-language", "en",
            "--report-style", "list",
            "--delay", "0.2",
        ]
        try:
            proc = runner(command, capture_output=True, text=True, timeout=timeout_s, check=False)
            returncode_i = int(getattr(proc, "returncode", 0) or 0)
            stdout_i = str(getattr(proc, "stdout", "") or "")
            stderr_i = str(getattr(proc, "stderr", "") or "")
        except InterruptedError:
            raise
        except Exception as exc:
            returncode_i = 1
            stdout_i = ""
            stderr_i = f"{type(exc).__name__}: {exc}"
        returncodes.append(returncode_i)
        all_stdout.append(stdout_i)
        all_stderr.append(stderr_i)
        raw_path = run_dir / "fetched.json"
        rows = _load_json(raw_path if raw_path.is_file() else run_dir / "papers.json", [])
        if not isinstance(rows, list):
            rows = []
        errors_i = (run_dir / "errors.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "errors.log").is_file() else ""
        error_lines_i = [line for line in errors_i.splitlines() if line.strip()]
        all_failed_i = bool(requested_sources) and len(error_lines_i) >= len(requested_sources)
        if returncode_i != 0 or all_failed_i:
            blocked_runs += 1
        query_runs.append({
            "query": query_item,
            "paper_count": len(rows),
            "returncode": returncode_i,
            "connector_error_count": len(error_lines_i),
            "output_dir": str(run_dir),
        })
        raw_metadata_count += len(rows)
        for row_index, raw_row in enumerate(rows):
            row, issue = _clean_metadata_record(raw_row)
            if issue:
                quality_rejections.append({"query_index": index, "row_index": row_index,
                    "reason": issue,
                    "title_excerpt": str(raw_row.get("title") or "")[:180]
                    if isinstance(raw_row, dict) else ""})
                continue
            assert row is not None
            for field in ("source_locked", "source_lock_status", "identity_resolution", "requested_source"):
                row.pop(field, None)
            row["discovery_queries"] = [query_item]
            # Keys are navigation aids, not identity evidence: even a shared
            # DOI can carry a conflicting arXiv ID. A weak record matching
            # several identities must remain independent, never bridge them.
            matches = [position for position, other in enumerate(merged)
                       if identity.same_paper(other, row)]
            if len(matches) == 1:
                position = matches[0]
                previous = merged[position]
                item = identity.merge_paper(previous, row)
                item["discovery_queries"] = list(dict.fromkeys(
                    list(previous.get("discovery_queries") or []) + [query_item]))
                merged[position] = item
            else:
                merged.append(row)

    papers = _reconcile_locked_source(
        [locked_record, *merged] if locked_record is not None else merged, source_lock)
    if task_type == "targeted_source":
        focus_query = " ".join(query_variants[:2])
        for paper in papers:
            paper["domain_alignment"] = _domain_alignment(paper, domain_context)
            paper["targeted_relevance"] = _targeted_relevance(
                paper, focus_query, name_source=f"{search_target} {domain_context}".strip()
            ) + paper["domain_alignment"]
        papers.sort(
            key=lambda row: (
                1 if row.get("source_locked") else 0,
                float(row.get("targeted_relevance") or 0),
                float(row.get("score") or 0),
                int(row.get("citation_count") or 0),
            ),
            reverse=True,
        )
    else:
        relevance_terms = request["relevance_terms"]
        for paper in papers:
            paper["landscape_relevance"] = _landscape_relevance(
                paper, query_variants, domain_context, relevance_terms)
            # Preserve the provider's score for diagnostics; downstream callers
            # use score for prioritization, so publish the same grounded order.
            paper["provider_score"] = paper.get("score", 0)
            paper["score"] = paper["landscape_relevance"]
        papers.sort(key=lambda row: (float(row.get("score") or 0),
                                     int(row.get("year") or 0)), reverse=True)
    candidate_count_before_limit = len(papers)
    candidate_limit = request["max_candidates"]
    papers = papers[:candidate_limit]
    quality_path = retrieval_dir / "metadata_quality.json"
    quality_path.write_text(json.dumps({"raw_metadata_count": raw_metadata_count,
        "rejected_metadata_count": len(quality_rejections), "rejections": quality_rejections,
        "candidate_count_before_limit": candidate_count_before_limit,
        "candidate_limit": candidate_limit, "retained_candidate_count": len(papers)},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    papers_path = retrieval_dir / "papers.json"
    papers_path.write_text(json.dumps(papers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_targeted_index(retrieval_dir, papers, query_runs)

    returncode = max(returncodes or [0])
    stdout = "\n".join(all_stdout)
    stderr = "\n".join(all_stderr)
    errors = "\n".join(
        (Path(run["output_dir"]) / "errors.log").read_text(encoding="utf-8", errors="replace")
        for run in query_runs if (Path(run["output_dir"]) / "errors.log").is_file()
    )
    transport_ok = bool(query_runs) and blocked_runs < len(query_runs)
    retrieved_papers = [row for row in papers if row is not locked_record]
    # User-provided source locks are search targets, not independent provider hits.
    status = "blocked" if not transport_ok else ("ready" if retrieved_papers else "empty")
    errors_path = retrieval_dir / "errors.log"
    if errors:
        errors_path.write_text(errors, encoding="utf-8")

    reading_result: dict[str, Any] = {}
    if papers and task_type == "targeted_source" and READ_SCRIPT.is_file():
        reading_dir.mkdir(parents=True, exist_ok=True)
        read_command = [
            str(CONDA_PYTHON), str(READ_SCRIPT),
            "--papers-json", str(papers_path),
            "--query", query or source_gap_id,
            "--output-dir", str(reading_dir),
            "--reading-limit", "1" if source_lock else "5",
            "--reading-selection", "manual" if source_lock else "top",
            *(["--paper-ids", str(next((paper.get("doi") or paper.get("title") for paper in papers
                                       if paper.get("source_locked")), "")
                                  or source_lock.get("doi") or source_lock.get("title"))] if source_lock else []),
            "--pdf-policy", "open-access-only",
            "--resolve-oa",
            "--timeout-s", str(max(10, min(timeout_s, 600))),
        ]
        try:
            read_timeout = _reading_timeout(timeout_s)
            read_proc = runner(read_command, capture_output=True, text=True, timeout=read_timeout, check=False)
            reading_result = {
                "returncode": int(getattr(read_proc, "returncode", 0) or 0),
                "stdout": str(getattr(read_proc, "stdout", "") or "")[-3000:],
                "stderr": str(getattr(read_proc, "stderr", "") or "")[-3000:],
                "reading_index": str(reading_dir / "reading_index.md"),
                "pdf_manifest": str(reading_dir / "pdf_manifest.csv"),
                "timeout_s": read_timeout,
            }
        except InterruptedError:
            raise
        except Exception as exc:
            reading_result = {"error": f"{type(exc).__name__}: {exc}"}

    acquisition_target_count = 0
    fulltext_read_count = 0
    manifest_path = Path(str(reading_result.get("pdf_manifest") or ""))
    if manifest_path.is_file():
        try:
            with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            acquisition_target_count = len(rows)
            fulltext_read_count = sum(1 for row in rows if str(row.get("status") or "") == "read")
        except Exception:
            pass
    reading_result["acquisition_target_count"] = acquisition_target_count
    reading_result["fulltext_read_count"] = fulltext_read_count

    trace_runs: list[dict[str, Any]] = []
    if papers and task_type == "targeted_source" and TRACE_SCRIPT.is_file():
        trace_root = root / "traces"
        trace_root.mkdir(parents=True, exist_ok=True)
        trace_candidates = papers[:1] if source_lock else papers[:2]
        for index, paper in enumerate(trace_candidates, 1):
            seed = str(paper.get("doi") or paper.get("url") or paper.get("title") or "").strip()
            if not seed:
                continue
            out_dir = trace_root / f"candidate-{index:02d}"
            trace_command = [
                str(CONDA_PYTHON), str(TRACE_SCRIPT),
                "--paper", seed,
                "--papers-json", str(papers_path),
                "--output-dir", str(out_dir),
                "--limit", "40" if source_lock else "15",
                "--openalex",
                "--delay", "0.2",
            ]
            try:
                trace_proc = runner(
                    trace_command, capture_output=True, text=True,
                    timeout=max(timeout_s, 180), check=False,
                )
                trace_runs.append({
                    "seed": seed,
                    "returncode": int(getattr(trace_proc, "returncode", 0) or 0),
                    "report": str(out_dir / "paper_trace_report.md"),
                    "json": str(out_dir / "paper_trace.json"),
                    "stderr": str(getattr(trace_proc, "stderr", "") or "")[-2000:],
                })
            except InterruptedError:
                raise
            except Exception as exc:
                trace_runs.append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})

    evidence_index = _write_evidence_index(
        retrieval_dir, papers, query_runs,
        reading_count=fulltext_read_count, citation_runs=trace_runs)

    result = {
        "schema": "theory-master/scholarly-transport/v1",
        "request_identity": request_identity,
        "status": status, "transport_ok": transport_ok,
        "source_gap_id": source_gap_id, "task_type": task_type, "query": query,
        "domain_context": domain_context,
        "source_lock": json.loads(json.dumps(source_lock)),
        "discovery_mode": "source_lock" if source_lock else "discovery",
        "query_variants": query_variants, "query_runs": query_runs,
        "raw_metadata_count": raw_metadata_count,
        "rejected_metadata_count": len(quality_rejections),
        "candidate_count_before_limit": candidate_count_before_limit,
        "metadata_quality_json": str(quality_path),
        # paper_count is retained for compatibility but is a metadata-hit
        # count, not a count of papers actually read.
        "paper_count": len(papers),
        "metadata_hit_count": len(retrieved_papers),
        "source_lock_only_count": len(papers) - len(retrieved_papers),
        "provider_coverage": "partial" if errors else ("ok" if transport_ok else "blocked"),
        "acquisition_target_count": acquisition_target_count,
        "fulltext_read_count": fulltext_read_count,
        "returncode": returncode,
        "stdout": stdout[-3000:], "stderr": stderr[-3000:],
        "output_dir": str(root), "retrieval_dir": str(retrieval_dir),
        "papers_json": str(papers_path), "evidence_index_md": str(evidence_index),
        "papers_top_json": str(retrieval_dir / "papers_top.json"),
        "paper_focus_json": str(retrieval_dir / "paper_focus.json")
        if (retrieval_dir / "paper_focus.json").is_file() else "",
        "report_md": str(retrieval_dir / "report.md"),
        "topic_brief_md": str(retrieval_dir / "topic_brief.md"),
        "search_log_md": str(retrieval_dir / "search_log.md"),
        "errors_log": str(errors_path) if errors_path.is_file() else "",
        "connector_errors": errors[-5000:], "reading": reading_result,
        "traces": trace_runs,
    }
    (root / "transport.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pin_scholarly_transport(task, result, workspace)


def fake_scholarly_retrieval(task: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Deterministic no-network transport used with TheoryMaster fake backends."""
    return pin_scholarly_transport(task, {
        "schema": "theory-master/scholarly-transport/v1",
        "status": "test_stub", "transport_ok": True,
        "source_gap_id": str(task.get("source_gap_id") or task.get("gap_id") or "external-gap"),
        "task_type": str(task.get("task_type") or "targeted_source"),
        "query": str(task.get("search_target") or ""),
        "paper_count": 0,
        "output_dir": str(workspace / "literature" / "sdk_retrieval" / "test-stub"),
    }, workspace)
