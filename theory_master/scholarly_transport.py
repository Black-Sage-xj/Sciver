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
import sys
from pathlib import Path
from typing import Any, Callable

CONDA_PYTHON = Path(os.environ.get("THEORYMASTER_SCHOLARLY_PYTHON") or sys.executable)
from .skills import skills_root

# Installed wheels contain the bundled resource mirror, not the source-tree skills/ directory.
SCRIPTS_DIR = skills_root() / "tm-scholar" / "scripts"
RETRIEVE_SCRIPT = SCRIPTS_DIR / "lit_retrieve.py"
READ_SCRIPT = SCRIPTS_DIR / "read_papers.py"
TRACE_SCRIPT = SCRIPTS_DIR / "trace_paper.py"

from .scholarly_selection import (
    _ascii_focus, _load_json, _paper_key, _proper_name_tokens, _query_variants,
    _slug, _source_lock_record, _targeted_relevance, _write_targeted_index,
    _domain_alignment, _write_evidence_index,
)

def retrieve_scholarly(
    task: dict[str, Any],
    workspace: Path,
    *,
    timeout_s: int = 150,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Execute reproducible scholarly retrieval in the host runner process."""
    source_gap_id = str(task.get("source_gap_id") or task.get("gap_id") or "external-gap")
    task_type = str(task.get("task_type") or "targeted_source")
    search_target = str(task.get("search_target") or "").strip()
    new_lead = str(task.get("new_search_lead") or "").strip()
    domain_context = str(task.get("domain_context") or "").strip()
    source_lock = task.get("source_lock") if isinstance(task.get("source_lock"), dict) else {}
    if task_type == "targeted_source" and (source_lock.get("doi") or source_lock.get("title")):
        exact = str(source_lock.get("title") or source_lock.get("doi") or "").strip()
        query_variants = [exact]
    else:
        query_variants = _query_variants(task_type, source_gap_id, search_target, new_lead, domain_context)
    query = query_variants[0]

    root = workspace / "literature" / "sdk_retrieval" / _slug(source_gap_id)
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
    merged: dict[str, dict[str, Any]] = {}
    fetched_keys: set[str] = set()
    query_runs: list[dict[str, Any]] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    requested_sources = [item for item in sources.split(",") if item]
    blocked_runs = 0
    returncodes: list[int] = []
    locked_key = ""
    if source_lock:
        locked_record = _source_lock_record(source_lock)
        locked_key = _paper_key(locked_record)
        if locked_key:
            merged[locked_key] = locked_record
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
        except Exception as exc:
            returncode_i = 1
            stdout_i = ""
            stderr_i = f"{type(exc).__name__}: {exc}"
        returncodes.append(returncode_i)
        all_stdout.append(stdout_i)
        all_stderr.append(stderr_i)
        rows = _load_json(run_dir / "papers.json", [])
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
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _paper_key(row)
            fetched_keys.add(key)
            previous = merged.get(key)
            if previous is None or float(row.get("score") or 0) > float(previous.get("score") or 0):
                merged[key] = dict(row)
            if locked_key and key == locked_key:
                enriched = dict(merged.get(key) or {})
                for field, value in dict(row).items():
                    if value not in (None, "", [], {}):
                        enriched[field] = value
                enriched["source_locked"] = True
                enriched["source_lock_status"] = str(source_lock.get("status") or "locked")
                if "is_open_access" in source_lock:
                    enriched["is_open_access"] = bool(source_lock.get("is_open_access"))
                    enriched["open_access_url"] = str(source_lock.get("open_access_url") or "")
                merged[key] = enriched

    papers = list(merged.values())
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
        papers.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
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
    retrieved_papers = [row for row in papers if _paper_key(row) in fetched_keys]
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
            *(["--paper-ids", str(source_lock.get("doi") or source_lock.get("title"))] if source_lock else []),
            "--pdf-policy", "open-access-only",
            "--max-pages", "20",
        ]
        try:
            read_proc = runner(read_command, capture_output=True, text=True, timeout=max(timeout_s, 180), check=False)
            reading_result = {
                "returncode": int(getattr(read_proc, "returncode", 0) or 0),
                "stdout": str(getattr(read_proc, "stdout", "") or "")[-3000:],
                "stderr": str(getattr(read_proc, "stderr", "") or "")[-3000:],
                "reading_index": str(reading_dir / "reading_index.md"),
                "pdf_manifest": str(reading_dir / "pdf_manifest.csv"),
            }
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
            except Exception as exc:
                trace_runs.append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})

    evidence_index = _write_evidence_index(
        retrieval_dir, papers, query_runs,
        reading_count=fulltext_read_count, citation_runs=trace_runs)

    result = {
        "schema": "theory-master/scholarly-transport/v1",
        "status": status, "transport_ok": transport_ok,
        "source_gap_id": source_gap_id, "task_type": task_type, "query": query,
        "domain_context": domain_context,
        "source_lock": json.loads(json.dumps(source_lock)),
        "discovery_mode": "source_lock" if source_lock else "discovery",
        "query_variants": query_variants, "query_runs": query_runs,
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
    return result


def fake_scholarly_retrieval(task: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Deterministic no-network transport used with TheoryMaster fake backends."""
    return {
        "schema": "theory-master/scholarly-transport/v1",
        "status": "test_stub", "transport_ok": True,
        "source_gap_id": str(task.get("source_gap_id") or task.get("gap_id") or "external-gap"),
        "task_type": str(task.get("task_type") or "targeted_source"),
        "query": str(task.get("search_target") or ""),
        "paper_count": 0,
        "output_dir": str(workspace / "literature" / "sdk_retrieval" / "test-stub"),
    }
