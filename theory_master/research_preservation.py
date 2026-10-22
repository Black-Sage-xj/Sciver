"""Conservative, idempotent preservation of unadjudicated Investigator work.

Separate the native Goal's incomplete execution from a reviewer/operator's
permission to archive.  This module never issues a Judge verdict or opens
physical cut responses.  No model calls occur here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .contract_stages import _preserve_research_structure
from .runtime import write_json


class PreservationGuard(ValueError):
    """A protected research state or provenance constraint was not satisfied."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preservation_blockage(cycle_dir: Path, run: Mapping[str, Any],
                          scientific: Mapping[str, Any]) -> dict[str, str]:
    """Verify a nonterminal scientific blocker without authorizing new research."""
    attention = run.get("attention") or {}
    code = str(attention.get("code") or "")
    phase = str(run.get("phase") or "")
    if any(str(row.get("status") or "").lower() != "needs_attention"
           or row.get("execution_active") is not False
           or str(row.get("phase") or "") != phase
           for row in (run, scientific)):
        raise PreservationGuard("a quiescent, matching Investigator blocked checkpoint is required")
    if (run.get("scientific_verdict") is not None
            or scientific.get("scientific_verdict") is not None):
        raise PreservationGuard("preservation cannot replace a scientific verdict")
    if code == "investigator_native_goal_blocked" and phase == "investigator:goal_suspended":
        if attention.get("goal_status") != "blocked":
            raise PreservationGuard("Native Goal has no verified blocked status")
        return {"code": code, "phase": phase, "judge_objection": ""}
    if code == "investigator_preservation_failed" and phase == "investigator:goal_suspended":
        return {"code": code, "phase": phase, "judge_objection": ""}
    if code == "investigator_budget_review_rejected" and phase in {
        "judge:rework_blocked", "investigator:budget_denied",
        "judge:ready_after_budget_denial",
    }:
        from .investigator_budget import budget_status
        if not budget_status(cycle_dir, cycle_dir.parent, dict(scientific))["rejected"]:
            raise PreservationGuard("SDK budget ledger has no committed reject")
        if not (run.get("investigator_budget") or {}).get("rejected"):
            raise PreservationGuard("budget rejection is absent from run projection")
    elif code in {"investigator_budget_supervisor_declined",
                  "investigator_budget_cap_reached"} and phase == "investigator:budget_denied":
        from .investigator_budget import budget_status, BUDGET_POLICY_3_PLUS_3
        budget = budget_status(cycle_dir, cycle_dir.parent, dict(scientific))
        if (budget["policy_version"] != BUDGET_POLICY_3_PLUS_3
                or (code == "investigator_budget_supervisor_declined"
                    and not budget["supervisor_declined"])
                or (code == "investigator_budget_cap_reached"
                    and not budget["hard_cap_reached"])):
            raise PreservationGuard("SDK budget terminal receipt is absent or invalid")
        if not (run.get("investigator_budget") or {}).get(
                "supervisor_declined" if code == "investigator_budget_supervisor_declined"
                else "hard_cap_reached"):
            raise PreservationGuard("budget terminal is absent from run projection")
    elif code == "judge_rework_budget_exhausted" and phase == "judge:rework_blocked":
        if scientific.get("assessment_state") != "rework_requested":
            raise PreservationGuard("Judge rework exhaustion lacks nonterminal assessment")
    else:
        raise PreservationGuard("a quiescent, genuine Investigator blocked checkpoint is required: unsupported Attention code")
    attempts = scientific.get("investigation_attempts") or {}
    prior = [
        row["judge_decision"] for row in attempts.values()
        if isinstance(row, dict) and isinstance(row.get("judge_decision"), dict)
        and row["judge_decision"].get("assessment_state") in {
            "rework_requested", "insufficient_evidence",
        }
    ]
    if phase == "judge:rework_blocked" and not prior:
        raise PreservationGuard("blocked Judge rework lacks original nonterminal Judge decision")
    previous = prior[-1] if prior else {}
    return {
        "code": code, "phase": phase,
        "judge_objection": str(previous.get("missing_evidence")
                               or previous.get("rationale") or "")[:2500],
    }


def blocked_checkpoint(cycle_dir: Path) -> dict[str, Any]:
    """Read the frozen interrupted Cycle; never infer scientific rejection."""
    path = cycle_dir / "run_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    scientific_path = cycle_dir / "research_state.json"
    if scientific_path.is_symlink():
        raise PreservationGuard("authoritative science checkpoint is unsafe")
    scientific = (json.loads(scientific_path.read_text(encoding="utf-8"))
                  if scientific_path.is_file() else state)
    # Historical blocked-preservation recovery tests and native-only Cycles
    # predate research_state.json. New budget/Judge paths require both records.
    if not scientific_path.is_file() and (state.get("attention") or {}).get("code") not in {
        "investigator_native_goal_blocked", "investigator_preservation_failed",
    }:
        raise PreservationGuard("authoritative science checkpoint is unavailable")
    attention = state.get("attention") or {}
    code = attention.get("code")
    blockage = preservation_blockage(cycle_dir, state, scientific)
    if code == "investigator_preservation_failed":
        # A failed SDK archive overwrites the original Attention code. Recover
        # only if the original native block and its authorized preserve decision
        # are recorded, and the exact Stage response is already available.
        events = cycle_dir / "important_events.jsonl"
        approval = cycle_dir / "preservation" / "supervisor_authorization.json"
        receipt = cycle_dir / "supervisor" / "status.json"
        output = cycle_dir / "preservation" / "last_model_output.txt"
        try:
            original_block = any(
                (entry := json.loads(line)).get("code") == "investigator_native_goal_blocked"
                and entry.get("kind") == "needs_attention"
                and entry.get("lifecycle") == "registered"
                and entry.get("cycle_id") == cycle_dir.name
                for line in events.read_text(encoding="utf-8").splitlines()
            )
            authorized = json.loads(approval.read_text(encoding="utf-8"))
            reviewed = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PreservationGuard("failed-preservation recovery lacks original authorization evidence") from exc
        if (not original_block or not output.is_file() or output.is_symlink()
                or authorized.get("source") != "supervisor_attention"
                or authorized.get("action") != "preserve"
                or reviewed.get("action") != "preserve"
                or reviewed.get("code") != "investigator_native_goal_blocked"
                or reviewed.get("status") != "returned"
                or reviewed.get("session_id") != authorized.get("session_id")):
            raise PreservationGuard("failed-preservation recovery lacks original authorization evidence")
    lock = state.get("hypothesis_lock") or {}
    hid = str(lock.get("id") or "")
    if lock.get("status") != "locked" or not re.fullmatch(r"H-\d{4,}", hid):
        raise PreservationGuard("missing frozen Hypothesis identity")
    if not all(str(lock.get(k) or "").strip() for k in ("hypothesis", "scope", "decisive_test")):
        raise PreservationGuard("incomplete frozen science")
    if ((scientific.get("hypothesis_lock") or {}).get("id") != hid):
        raise PreservationGuard("run and scientific Hypothesis identities differ")
    state["preservation_blockage"] = blockage
    return state


def verified_sources(cycle_dir: Path, output: Mapping[str, Any]) -> list[dict[str, str]]:
    """Accept only existing files inside this Cycle, never invented remote citations."""
    sources = []
    seen: set[str] = set()
    root = cycle_dir.resolve()
    for row in output["observations"]:
        for origin in row["evidence_paths"]:
            rel = Path(origin)
            source = rel if rel.is_absolute() else cycle_dir / rel
            # Investigator's local workspace is also the base for nested
            # evidence paths (e.g. full_shell_smoke/report.json). Prefer an
            # existing Cycle-relative path; never override symlinks, '..' or
            # absolute references when a source cannot be found.
            if (not rel.is_absolute() and ".." not in rel.parts
                    and rel.parts and rel.parts[0] != "workspace"
                    and not source.exists() and not source.is_symlink()):
                source = cycle_dir / "workspace" / rel
            candidate = source.resolve()
            if (not candidate.is_relative_to(root) or not source.is_file()
                    or source.is_symlink() or not candidate.is_file()):
                raise PreservationGuard(f"source must be an existing Cycle-local regular file: {origin}")
            if candidate.stat().st_size > 100_000_000:
                raise PreservationGuard("unbounded source asset must be indexed rather than copied")
            relative = candidate.relative_to(root).as_posix()
            if relative not in seen:
                seen.add(relative)
                sources.append({"path": relative, "sha256": _sha(candidate)})
    return sources


def _render_note(cycle_id: str, hypothesis_id: str, output: Mapping[str, Any],
                 sources: list[dict[str, str]], status: str,
                 objection: str = "") -> str:
    lines = [
        f"# Cycle {cycle_id} · {output['canvas_title']}",
        "",
        "> 研究过程保全 · 未裁决（unadjudicated） · 非 Judge Finding / Decision",
        f"> Hypothesis: {hypothesis_id} · Native Goal: {status} · decisive_test: 未完成",
        "",
        "## 暂存的科学观察（仅限所给证据与条件）",
    ]
    for i, row in enumerate(output["observations"], 1):
        lines.extend([
            f"### {i}. {row['statement']}", "",
            f"- 条件：{row['conditions']}",
            f"- 限制：{row['limitations']}",
            "- 来源：" + ", ".join(f"`{v}`" for v in row["evidence_paths"]), "",
        ])
    lines.extend(["## 方法局限"])
    for row in output["methods_exhausted"]:
        lines.append(f"- **{row['method']}**：{row['limitation']}")
    lines.extend([
        "", "## 尚未解决", output["remaining_gap"], "",
        "## 冻结检验未执行的部分", output["unexecuted_test"], "",
        "## 来源 SHA-256",
    ])
    for row in sources:
        lines.append(f"- `{row['path']}`: `{row['sha256']}`")
    lines.extend([
        "", "本记录不等于对冻结 Hypothesis 的 supported/rejected/inconclusive 裁决。",
    ])
    if objection:
        lines.extend(["", "## 历史 Judge 返工要求（未完成）", objection,
                      "该要求为非终局历史意见；本次保全没有重新裁决。"])
    return "\n".join(lines) + "\n"


@contextmanager
def _canvas_lock(canvas_path: Path):
    import fcntl
    lock_path = canvas_path.with_suffix(".preserve.lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _check_preservation_authorization(cycle_dir: Path, authorization: str,
                                      hypothesis_id: str) -> None:
    if authorization == "operator_approved":
        # The CLI must require its own explicit --approve-preservation flag.
        return
    if authorization != "supervisor_approved":
        raise PreservationGuard("explicit Supervisor or operator preservation authorization required")
    from .supervisor_intervention import supervisor_enabled
    state = blocked_checkpoint(cycle_dir)
    if not supervisor_enabled(Path(state.get("research_root") or cycle_dir.parent)):
        raise PreservationGuard("Supervisor is disabled; blocked requires ordinary NEEDS_ATTENTION")
    decision_file = cycle_dir / "preservation" / "supervisor_authorization.json"
    if not decision_file.is_file():
        raise PreservationGuard("Supervisor preservation approval record is missing")
    value = json.loads(decision_file.read_text(encoding="utf-8"))
    if (value.get("action") != "preserve"
            or value.get("hypothesis_id") != hypothesis_id
            or value.get("source") != "supervisor_attention"
            or not str(value.get("session_id") or "").startswith("SUP-")):
        raise PreservationGuard("Supervisor approval is not scoped to the frozen Hypothesis")
    supervisor_path = cycle_dir / "supervisor" / "status.json"
    try:
        receipt = json.loads(supervisor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PreservationGuard("matching Supervisor session receipt is unavailable") from exc
    if (receipt.get("session_id") != value["session_id"]
            or receipt.get("profile") != "attention"
            or receipt.get("code") != "investigator_native_goal_blocked"
            or receipt.get("action") != "preserve"
            or receipt.get("status") != "returned"):
        raise PreservationGuard("Supervisor receipt does not authorize this preservation")


def preserve_blocked_cycle(*, cycle_dir: Path, canvas_path: Path,
                           stage_output: dict[str, Any],
                           authorization: str,
                           dry_run: bool = False) -> dict[str, Any]:
    """Record authorized unfinished research in SQLite, then render its Canvas projection."""
    cycle_dir = Path(cycle_dir)
    canvas_path = Path(canvas_path)
    state = blocked_checkpoint(cycle_dir)
    _check_preservation_authorization(cycle_dir, authorization, state["hypothesis_lock"]["id"])
    _preserve_research_structure(stage_output, {})
    sources = verified_sources(cycle_dir, stage_output)
    cycle_id = str(state.get("cycle_id") or cycle_dir.name)
    hid = str(state["hypothesis_lock"]["id"])
    if canvas_path.resolve() != Path(state["branch_canvas"]).resolve():
        raise PreservationGuard("projection path does not match the frozen Cycle checkpoint")
    if canvas_path.is_symlink():
        raise PreservationGuard("refusing to write a Canvas projection through a symlink")
    graph_path = canvas_path.resolve().parent / "science_graph.sqlite3"
    if not graph_path.is_file():
        raise PreservationGuard(f"canonical SQLite graph is required: {graph_path}")
    note_path = (canvas_path.resolve().parent / "notes" / "scientific_reports"
                 / f"Cycle{cycle_id}-preserve_research.md")
    note_link = f"notes/scientific_reports/Cycle{cycle_id}-preserve_research"
    blockage = state["preservation_blockage"]
    note = _render_note(cycle_id, hid, stage_output, sources,
                        blockage["code"], blockage["judge_objection"])
    output_hash = hashlib.sha256(
        json.dumps(stage_output, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    with _canvas_lock(canvas_path) if not dry_run else _no_lock():
        from .science_graph_store import ScienceGraphStore
        from .science_canvas_export import canvas_projection, write_external_detail_documents
        with ScienceGraphStore(graph_path, readonly=True) as store:
            if store.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (hid,)).fetchone() is None:
                raise PreservationGuard(f"preserved H is missing from canonical SQLite: {hid}")
            existing = [row for row in store.cycle_outcomes(hid)
                        if str(row.get("cycle_id")) == cycle_id
                        and row.get("verdict") == "unadjudicated"]
            if len(existing) > 1:
                raise PreservationGuard("duplicate preserved Cycle Outcomes require manual reconciliation")
            if existing and existing[0].get("stage_output_sha256") != output_hash:
                raise PreservationGuard("prior SQLite preservation Outcome differs")
            created = not bool(existing)
        result = {
            "schema": "theory-master/preserve-research-result/v1",
            "cycle_id": cycle_id, "hypothesis_id": hid, "node_id": hid,
            "presentation": "hypothesis_outcome",
            "scientific_verdict": None, "decisive_test_completed": False,
            "source_files": sources, "stage_output_sha256": output_hash,
            "created": created, "dry_run": dry_run,
        }
        if dry_run:
            return result
        if note_path.exists() and (note_path.is_symlink() or note_path.read_text(encoding="utf-8") != note):
            raise PreservationGuard("existing preservation note differs; no overwrite")
        note_path.parent.mkdir(parents=True, exist_ok=True)
        if not note_path.exists():
            note_path.write_text(note, encoding="utf-8")
        with ScienceGraphStore(graph_path) as store:
            store.record_preserved_cycle(
                hypothesis_id=hid, cycle_id=cycle_id,
                summary=stage_output["canvas_summary"],
                report_ref=f"notes/scientific_reports/Cycle{cycle_id}-preserve_research.md",
                source_attention_code=blockage["code"],
                prior_judge_objection=blockage["judge_objection"],
                stage_output_sha256=output_hash,
            )
            current_frontier = store.frontier_view().get(hid) or {}
            if current_frontier.get("frontier_status") == "active":
                store.set_frontier(
                    branch_id="frontier:" + hid, hypothesis_id=hid,
                    status="parked", reason="unadjudicated preserved research",
                )
            projected = canvas_projection(store)
            write_external_detail_documents(store, notes_root=canvas_path.resolve().parent / "notes")
        write_json(canvas_path, projected)
        record = cycle_dir / "preservation" / "status.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        if record.exists():
            prior = json.loads(record.read_text(encoding="utf-8"))
            if (prior.get("hypothesis_id") != hid or prior.get("node_id") != hid
                    or prior.get("stage_output_sha256") != output_hash
                    or prior.get("source_files") != sources):
                raise PreservationGuard("existing preservation status or sources conflict")
        else:
            _atomic_write(record, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if not (record.parent / "stage_output.json").exists():
            _atomic_write(record.parent / "stage_output.json", json.dumps(stage_output, ensure_ascii=False, indent=2) + "\n")
        return result


def _cached_preservation_model_output(cycle_dir: Path, path: Path) -> str | None:
    """Prevent a paid model call when a verified cached-only replay is armed."""
    guard = cycle_dir / "preservation" / "cached_replay_required.json"
    if guard.exists() or guard.is_symlink():
        if not guard.is_file() or guard.is_symlink():
            raise PreservationGuard("cached preservation replay guard is unsafe")
        try:
            receipt = json.loads(guard.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PreservationGuard("cached preservation replay guard is invalid") from exc
        if (not isinstance(receipt, dict)
                or receipt.get("schema") != "theory-master/preservation-cached-replay/v1"
                or receipt.get("cycle") != cycle_dir.name
                or not isinstance(receipt.get("sha256"), str)
                or len(receipt["sha256"]) != 64):
            raise PreservationGuard("cached preservation replay identity is invalid")
        if not path.is_file() or path.is_symlink():
            raise PreservationGuard("cached preservation output missing; model call prohibited")
        raw = path.read_text(encoding="utf-8")
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != receipt["sha256"]:
            raise PreservationGuard("cached preservation output changed; model call prohibited")
        return raw
    if path.exists() or path.is_symlink():
        if not path.is_file() or path.is_symlink():
            raise PreservationGuard("existing preservation response is not a regular file")
        return path.read_text(encoding="utf-8")
    return None


def run_preservation_stage(*, cycle_dir: Path, canvas_path: Path, backend: Any,
                           model: str, provider: str, authorization: str,
                           dry_run: bool = False) -> dict[str, Any]:
    """Invoke a separate authorized Investigator preserve_research Stage.

    Original native science thread and paused checkpoint remain untouched.
    No new model call occurs until this function is explicitly invoked.
    """
    state = blocked_checkpoint(Path(cycle_dir))
    _check_preservation_authorization(Path(cycle_dir), authorization, state["hypothesis_lock"]["id"])
    # If a previous Stage succeeded but the process stopped before formal
    # Cycle closure, reuse its audited output: never spend a second model turn
    # merely to repeat the same archival summary.
    prior_record = Path(cycle_dir) / "preservation" / "status.json"
    prior_output = Path(cycle_dir) / "preservation" / "stage_output.json"
    if prior_record.is_file() != prior_output.is_file():
        raise PreservationGuard("partial preservation Stage receipt; reconcile before retrying")
    if prior_record.is_file():
        cached = json.loads(prior_output.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            raise PreservationGuard("stored preservation Stage output is not an object")
        return preserve_blocked_cycle(
            cycle_dir=cycle_dir, canvas_path=canvas_path,
            stage_output=cached, authorization=authorization, dry_run=dry_run,
        )
    from .contract_stages import role_contract
    from .prompt_stages import stage_context, build_initial_prompt
    contract = role_contract("investigator", phase="preserve_research")
    context = stage_context("investigator", "preserve_research", {
        "hypothesis_lock": state["hypothesis_lock"],
        "blocked_reason": str((state.get("attention") or {}).get("reason", ""))
                          + ("; prior Judge request: " + state["preservation_blockage"]["judge_objection"]
                             if state["preservation_blockage"]["judge_objection"] else ""),
        "source_cycle": state.get("cycle_id"),
        "cycle_dir": str(cycle_dir),
        "cycle_workspace": str(cycle_dir / "workspace"),
    })
    prompt = build_initial_prompt(
        prefix="Independent approved preservation only.",
        role="investigator", stage="preserve_research",
        contract_ref=contract.ref,
        contract_schema=contract.prompt_projection(context),
        context=context, write_rule=(
            "Read-only research preservation. Do not modify files, run new "
            "scientific calculations, or execute the frozen decisive_test. "
            "Within this ONE Investigator model call, do A first: evidence-qualified "
            "observations, attempted method limits, remaining scientific gap and "
            "unexecuted decisive_test. Then do B: compress A faithfully into a "
            "short title and unadjudicated Canvas summary, with no additional "
            "scientific claim. SDK alone performs C: provenance, MD, Canvas and "
            "Cycle OVER. No Reporter, Judge, Literature, Lead or second model call. "
            "Use existing Cycle-local evidence paths. No Judge verdict."
        ),
    )
    # The preservation Agent must have a read-only sandbox, even though the
    # main Investigator owns workspace-write access during research.
    attempt_path = Path(cycle_dir) / "preservation" / "last_model_output.txt"
    raw = _cached_preservation_model_output(Path(cycle_dir), attempt_path)
    if raw is None:
        previous_read_only = bool(getattr(backend, "_preservation_read_only", False))
        backend._preservation_read_only = True
        try:
            thread = backend.ensure_thread("investigator", None, model, provider)
            _, raw = backend.invoke("investigator", prompt, thread, model, provider)
        finally:
            backend._preservation_read_only = previous_read_only
        if not isinstance(raw, str):
            raise PreservationGuard("preservation output must be JSON text")
        # Save this response before validation so a failed archive can replay
        # without spending a second model call or revising scientific claims.
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(attempt_path, raw)
    try:
        output = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreservationGuard("preservation stage returned invalid JSON") from exc
    if not isinstance(output, dict):
        raise PreservationGuard("preservation stage output must be an object")
    contract.structural_validator(output, context)
    return preserve_blocked_cycle(
        cycle_dir=cycle_dir, canvas_path=canvas_path,
        stage_output=output, authorization=authorization, dry_run=dry_run,
    )



def finalize_preserved_cycle(*, cycle_dir: Path, authorization: str,
                             dry_run: bool = False) -> dict[str, Any]:
    """Close an already preserved, unadjudicated Cycle; zero model calls.

    The preservation Stage and the final status transition are distinct
    transactions.  We retain original stopped-state snapshots for forensics,
    and never create a Judge verdict, Reporter claim or a future Cycle.
    """
    import fcntl
    from .runtime import now

    cycle_dir = Path(cycle_dir).resolve()
    if authorization not in {"operator_approved", "supervisor_approved"}:
        raise PreservationGuard("explicit Operator/Supervisor closure authorization required")
    folder = cycle_dir / "preservation"
    status_path = folder / "status.json"
    output_path = folder / "stage_output.json"
    receipt_path = folder / "closure.json"
    research_path = cycle_dir / "research_state.json"
    run_path = cycle_dir / "run_state.json"
    if not all(p.is_file() and not p.is_symlink()
               for p in (status_path, output_path, research_path, run_path)):
        raise PreservationGuard("complete existing preservation and both Cycle state files are required")

    def read_json(path: Path) -> dict[str, Any]:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise PreservationGuard(f"invalid JSON object: {path.name}")
        return loaded

    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "closure.lock").open("a+") as locking:
        fcntl.flock(locking.fileno(), fcntl.LOCK_EX)
        try:
            run = read_json(run_path)
            science = read_json(research_path)
            archive = read_json(status_path)
            output = read_json(output_path)
            hid = str(archive.get("hypothesis_id") or "")
            if str(archive.get("cycle_id")) != cycle_dir.name:
                raise PreservationGuard("preservation record is for a different Cycle")
            if (str((run.get("hypothesis_lock") or {}).get("id") or "") != hid
                    or str((science.get("hypothesis_lock") or {}).get("id") or "") != hid):
                raise PreservationGuard("Hypothesis identity mismatch")
            if (archive.get("scientific_verdict") is not None
                    or archive.get("decisive_test_completed") is not False
                    or run.get("scientific_verdict") is not None
                    or science.get("scientific_verdict") is not None):
                raise PreservationGuard("preservation is not an unadjudicated unfinished test")
            prior_closure = all(str(st.get("status") or "").lower() == "over"
                                for st in (science, run))
            blockage = ({"code": "previously_preserved", "judge_objection": ""}
                        if prior_closure else preservation_blockage(cycle_dir, run, science))
            if "judge" in (science.get("role_outputs") or {}):
                if not (blockage.get("judge_objection")
                        or any(isinstance(x, dict)
                               and (x.get("judge_decision") or {}).get("assessment_state") == "rework_requested"
                               for x in (science.get("investigation_attempts") or {}).values())):
                    raise PreservationGuard("terminal or unexplained Judge output cannot be preserved")
            _preserve_research_structure(output, {})
            output_hash = hashlib.sha256(
                json.dumps(output, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if archive.get("stage_output_sha256") != output_hash:
                raise PreservationGuard("preservation Stage content hash mismatch")
            if verified_sources(cycle_dir, output) != archive.get("source_files"):
                raise PreservationGuard("preserved source hashes changed")

            canvas_path = Path(str(run.get("branch_canvas") or ""))
            if not str(canvas_path):
                raise PreservationGuard("frozen Cycle lacks its Canvas projection path")
            if canvas_path.is_symlink():
                raise PreservationGuard("refusing to trust a symlinked Canvas projection")
            if (science.get("branch_canvas") and
                    Path(str(science["branch_canvas"])).resolve() != canvas_path.resolve()):
                raise PreservationGuard("scientific and projected Canvas pointers differ")
            graph_path = canvas_path.resolve().parent / "science_graph.sqlite3"
            if not graph_path.is_file():
                raise PreservationGuard(f"canonical SQLite graph is missing: {graph_path}")
            node_id = str(archive.get("node_id") or "")
            is_h_outcome = archive.get("presentation") == "hypothesis_outcome"
            note_path = (canvas_path.resolve().parent / "notes" / "scientific_reports"
                         / f"Cycle{cycle_dir.name}-preserve_research.md")
            note_link = f"Cycle{cycle_dir.name}-preserve_research"
            if (not note_path.is_file() or note_path.is_symlink()
                    or hid not in note_path.read_text(encoding="utf-8")):
                raise PreservationGuard("preservation note or its H provenance is missing")
            from .science_graph_store import ScienceGraphStore
            with ScienceGraphStore(graph_path, readonly=True) as store:
                frontier = store.frontier_view().get(hid)
                if frontier is None or frontier.get("frontier_status") != "parked":
                    raise PreservationGuard("SQLite H must exist with parked frontier status")
                if is_h_outcome:
                    records = [row for row in store.cycle_outcomes(hid)
                               if str(row.get("cycle_id")) == cycle_dir.name
                               and row.get("verdict") == "unadjudicated"]
                    if (node_id != hid or len(records) != 1
                            or records[0].get("stage_output_sha256") != output_hash):
                        raise PreservationGuard("SQLite graph lacks the matching preserved Outcome")
                else:
                    external = store.db.execute(
                        "SELECT content_type,content FROM external_records WHERE id=?", (node_id,)
                    ).fetchone()
                    edge = store.db.execute(
                        "SELECT hypothesis_id,external_id FROM external_edges WHERE id=?",
                        (f"tm-preservation-{hid}-{cycle_dir.name}",),
                    ).fetchone()
                    if (external is None or external["content_type"] != "StageSummary"
                            or note_link not in str(external["content"] or "")
                            or edge is None or edge["hypothesis_id"] != hid
                            or edge["external_id"] != node_id):
                        raise PreservationGuard("SQLite graph lacks the linked historical StageSummary")
            already_closed = all(
                str(st.get("status") or "").lower() == "over"
                and st.get("closure_reason") == "investigator_preservation"
                and st.get("phase") == "investigator:preserved"
                for st in (science, run)
            )
            if already_closed:
                if not receipt_path.is_file():
                    raise PreservationGuard("terminal statuses lack a closure receipt; reconcile manually")
                receipt = read_json(receipt_path)
                if (receipt.get("stage_output_sha256") != output_hash
                        or receipt.get("hypothesis_id") != hid
                        or receipt.get("cycle_id") != cycle_dir.name):
                    raise PreservationGuard("closure receipt conflicts with Stage")
                return dict(receipt, already_closed=True)

            if (str(run.get("status") or "").lower() != "needs_attention"
                    or str(science.get("status") or "").lower() != "needs_attention"
                    or run.get("execution_active") is not False
                    or science.get("execution_active") is not False
                    or str(run.get("phase") or "") != str(science.get("phase") or "")):
                raise PreservationGuard("only a matched, fully stopped scientific-blocked Cycle can close")
            blocked_checkpoint(cycle_dir)
            _check_preservation_authorization(cycle_dir, authorization, hid)
            if dry_run:
                return {"cycle_id": cycle_dir.name, "hypothesis_id": hid,
                        "ready_to_close": True, "dry_run": True}
            if (folder / "preclose_run_state.json").exists() or (
                    folder / "preclose_research_state.json").exists():
                raise PreservationGuard("prior closure attempt has snapshots; manual reconciliation required")

            receipt = {
                "schema": "theory-master/preserve-research-closure/v1",
                "cycle_id": cycle_dir.name, "hypothesis_id": hid,
                "authority": authorization, "closed_at": now(),
                "source_attention_code": blockage["code"],
                "prior_judge_objection": blockage.get("judge_objection") or "",
                "original_thread_id": (run.get("attention") or {}).get("thread_id"),
                "stage_output_sha256": output_hash,
                "note": str(note_path), "canvas_node_id": node_id,
                "status": "OVER", "closure_reason": "investigator_preservation",
                "scientific_verdict": None, "decisive_test_completed": False,
                "next_cycle_created": False,
            }
            # Backups use exact original bytes and are immutable; neither
            # the unfinished native thread nor any scientific evidence is edited.
            _atomic_write(folder / "preclose_run_state.json",
                          run_path.read_text(encoding="utf-8"))
            _atomic_write(folder / "preclose_research_state.json",
                          research_path.read_text(encoding="utf-8"))
            for state, label in ((science, "over"), (run, "OVER")):
                state.update({
                    "status": label, "phase": "investigator:preserved",
                    "closure_reason": "investigator_preservation",
                    "resumable": False, "execution_active": False,
                    "owner_pid": None, "next_executor": None,
                    "attention": {}, "human_attention": None,
                    "scientific_verdict": None,
                    "preservation": {
                        "schema": "theory-master/preservation-handoff/v1",
                        "hypothesis_id": hid, "node_id": node_id,
                        "stage_output_sha256": output_hash,
                        "scientific_verdict": None,
                        "decisive_test_completed": False,
                    },
                })
            for k in ("current_agent", "current_thread_id", "current_turn_id",
                      "current_tool", "current_activity"):
                if k in run:
                    run[k] = None
            _atomic_write(research_path, json.dumps(science, ensure_ascii=False, indent=2) + "\n")
            _atomic_write(run_path, json.dumps(run, ensure_ascii=False, indent=2) + "\n")
            _atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            return receipt
        finally:
            fcntl.flock(locking.fileno(), fcntl.LOCK_UN)


def migrate_preserved_external_to_h_outcome(*, cycle_dir: Path,
                                             canvas_path: Path,
                                             dry_run: bool = False) -> dict[str, Any]:
    """One-time, backed-up presentation migration for a FINISHED preserve.

    Does not change original closure/Judge/evidence, create a Cycle, or call a model.
    The old StageSummary markdown file is retained as an audit artifact.
    """
    import sqlite3
    from .science_graph_store import ScienceGraphStore

    cycle_dir = Path(cycle_dir).resolve()
    canvas_path = Path(canvas_path).resolve()
    folder = cycle_dir / "preservation"
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    closure = json.loads((folder / "closure.json").read_text(encoding="utf-8"))
    stage = json.loads((folder / "stage_output.json").read_text(encoding="utf-8"))
    hid = str(status["hypothesis_id"])
    cid = str(status["cycle_id"])
    old_id = str(status["node_id"])
    stage_hash = hashlib.sha256(
        json.dumps(stage, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if (cid != cycle_dir.name or not old_id.startswith("external-c")
            or closure.get("cycle_id") != cid
            or closure.get("hypothesis_id") != hid
            or closure.get("status") != "OVER"
            or closure.get("scientific_verdict") is not None
            or closure.get("canvas_node_id") != old_id
            or stage_hash != status["stage_output_sha256"]
            or stage_hash != closure["stage_output_sha256"]
            or not str(stage.get("canvas_summary") or "").strip()):
        raise PreservationGuard("nonterminal or inconsistent legacy preservation")
    if verified_sources(cycle_dir, stage) != status["source_files"]:
        raise PreservationGuard("original preserved source files have changed")
    note_path = (canvas_path.parent / "notes" / "scientific_reports"
                 / f"Cycle{cid}-preserve_research.md")
    if not note_path.is_file() or note_path.is_symlink() or hid not in note_path.read_text():
        raise PreservationGuard("original full research note is unavailable")
    graph_path = canvas_path.with_name("science_graph.sqlite3")
    if not graph_path.is_file() or graph_path.is_symlink():
        raise PreservationGuard("canonical graph unavailable")
    run = json.loads((cycle_dir / "run_state.json").read_text(encoding="utf-8"))
    science = json.loads((cycle_dir / "research_state.json").read_text(encoding="utf-8"))
    if any(str(x.get("status") or "").lower() != "over"
           or x.get("scientific_verdict") is not None
           or x.get("closure_reason") != "investigator_preservation"
           for x in (run, science)):
        raise PreservationGuard("archived Cycle is not safely closed")
    if Path(str(run.get("branch_canvas") or "")).resolve() != canvas_path:
        raise PreservationGuard("Cycle has a different bound Canvas")
    code = str(closure.get("source_attention_code") or "")
    prior_objection = str(closure.get("prior_judge_objection") or "")
    if not code:
        raise PreservationGuard("missing original shutdown reason")
    manifest_path = folder / "outcome_presentation_migration.json"
    note_link = f"notes/scientific_reports/Cycle{cid}-preserve_research"
    with _canvas_lock(canvas_path):
        canvas_bytes = canvas_path.read_bytes()
        canvas = json.loads(canvas_bytes)
        old_nodes = [n for n in canvas.get("nodes", []) if n.get("id") == old_id]
        if len(old_nodes) > 1 or (old_nodes and "content_type: StageSummary" not in
                                  str(old_nodes[0].get("text") or "")):
            raise PreservationGuard("old preservation node identity is ambiguous")
        old_edges = [e for e in canvas.get("edges", [])
                     if old_id in (e.get("fromNode"),e.get("toNode"))]
        expected_edge = f"tm-preservation-{hid}-{cid}"
        if old_edges and (len(old_edges) != 1 or old_edges[0].get("id") != expected_edge
                          or old_edges[0].get("fromNode") != hid):
            raise PreservationGuard("old preservation node has extra Canvas links")
        canvas["nodes"] = [n for n in canvas.get("nodes", []) if n.get("id") != old_id]
        canvas["edges"] = [e for e in canvas.get("edges", []) if e.get("id") != expected_edge]
        canvas, node_id, _ = _hypothesis_outcome_projection(
            canvas, hid, cid, stage["canvas_summary"], note_link, code)
        manifest = {
            "schema": "theory-master/preservation-outcome-presentation-migration/v1",
            "cycle_id": cid, "hypothesis_id": hid,
            "original_node_id": old_id, "presented_node_id": node_id,
            "stage_output_sha256": stage_hash,
            "original_closure_untouched": True,
            "scientific_verdict": None, "full_report": str(note_path),
        }
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing != manifest:
                raise PreservationGuard("incompatible prior migration record")
        if dry_run:
            return dict(manifest, dry_run=True,
                        expected_remaining_nodes=len(canvas["nodes"]))
        canvas_backup = folder / "exploration.before-outcome-merge.json"
        graph_backup = folder / "science_graph.before-outcome-merge.sqlite3"
        if not canvas_backup.exists():
            _atomic_write(canvas_backup, canvas_bytes.decode("utf-8"))
        if not graph_backup.exists():
            with sqlite3.connect(f"file:{graph_path}?mode=ro", uri=True) as src:
                with sqlite3.connect(graph_backup) as dst:
                    src.backup(dst)
        with ScienceGraphStore(graph_path) as store:
            with store.transaction():
                store.record_preserved_cycle(
                    hypothesis_id=hid, cycle_id=cid,
                    summary=stage["canvas_summary"],
                    report_ref=f"notes/scientific_reports/Cycle{cid}-preserve_research.md",
                    source_attention_code=code,
                    prior_judge_objection=prior_objection,
                    stage_output_sha256=stage_hash,
                )
                store.discard_auto_preservation_external(
                    hypothesis_id=hid, cycle_id=cid, node_id=old_id)
        _atomic_write(canvas_path, json.dumps(canvas, ensure_ascii=False, indent=2)+"\n")
        _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
        return dict(manifest, dry_run=False,
                    expected_remaining_nodes=len(canvas["nodes"]))


@contextmanager
def _no_lock():
    yield


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
