"""Supervisor handoff to Goal Judge on a new, committed evidence revision."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .delivery_repair import _canonical, current_delivery_payload
from .goal_terminal import goal_sealed
from .science_graph_store import ScienceGraphStore
from .specification import list_cycle_dirs


def _read_object(path: Path, root: Path, *, limit: int) -> dict:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe Goal reassessment input")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Goal reassessment input exceeds its size limit")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Goal reassessment input is not an object")
    return result


def _sources(delivery: dict) -> dict[str, str]:
    # Directory entries and missing reports are navigation only.
    return {
        row["reference"]: row["sha256"]
        for field in ("goal_material_sources", "report_versions")
        for row in delivery.get(field) or []
        if isinstance(row, dict) and row.get("status") == "available"
        and row.get("reference") and row.get("sha256")
    }


def _goal_reassessment_plan(spec: Any, graph: Path, *, delivery_only: bool = False,
                            verified_archive: dict | None = None) -> dict | None:
    root = Path(spec.research_root)
    cycles = list_cycle_dirs(root, task_mode=spec.task_mode)
    if not cycles or not graph.is_file() or goal_sealed(root, graph, spec=spec):
        return None
    latest = cycles[0]
    control = latest / "recovery_control.json"
    if control.exists() or control.is_symlink():
        return None
    stopped = _read_object(latest / "research_state.json", root, limit=4_000_000)
    from .task_progression import local_replan_reason

    stopped_recovery = stopped.get("recovery") or {}
    if (local_replan_reason(stopped) != "no_qualified_candidate" or stopped.get("execution_active")
            or stopped.get("abandoned_cycle") or not isinstance(stopped_recovery, dict)
            or stopped_recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
        return None

    with ScienceGraphStore(graph, readonly=True) as store:
        previous = store.latest_goal_gate_assessment()
        if not previous or previous["verdict"] != "not_passed":
            return None
        if delivery_only:
            anchor_outcome = store.cycle_evaluation(previous["cycle_id"])
            if (not anchor_outcome or anchor_outcome["verdict"] != "supported"
                    or anchor_outcome["id"] != previous["outcome_id"]):
                return None
            if store.db.execute(
                "SELECT 1 FROM cycle_evaluations WHERE recorded_at = ? AND id <> ? LIMIT 1",
                (previous["recorded_at"], previous["outcome_id"]),
            ).fetchone():
                return None  # Crossing scientific commits cannot be ordered within a legacy second.
        received = [
            dict(row) for row in store.db.execute(
                "SELECT id,cycle_id,verdict FROM cycle_evaluations "
                "WHERE recorded_at > ? AND verdict IN ('supported','open','rejected') ORDER BY recorded_at,id",
                (previous["recorded_at"],),
            )
        ]
    if delivery_only and received:
        return None

    revision = previous.get("delivery_revision") or ""
    anchor = next((cycle for cycle in cycles if cycle.name == previous["cycle_id"]), None)
    if anchor is None:
        return None
    state = _read_object(anchor / "research_state.json", root, limit=4_000_000)
    pending = state.get("goal_delivery_pending") or {}
    recoverable = (pending.get("cycle_id") == anchor.name
                   and pending.get("closed_status") in {"over", "completed"})
    if state.get("status") not in {"over", "completed"} and not recoverable:
        return None
    control = anchor / "recovery_control.json"
    recovery = state.get("recovery") or {}
    if (control.exists() or control.is_symlink() or state.get("abandoned_cycle")
            or (state.get("execution_active") and not recoverable)
            or not isinstance(recovery, dict)
            or recovery.get("action") in {"pause", "interrupt", "kill", "abandon_cycle"}):
        return None

    current = (current_delivery_payload(spec, graph, anchor) if verified_archive is None else
               current_delivery_payload(spec, graph, anchor, verified_archive=verified_archive))
    archival = current.get("archival_goal_delivery")
    archive_ready = (not getattr(spec, "submission_contract_ref", None) and isinstance(archival, dict)
                     and archival["goal_assessment"] == previous["assessment_id"]
                     and archival["stopped_cycle"] == latest.name)
    if delivery_only and not archive_ready:
        from .public_submission import check_current_submission

        check = check_current_submission(current)
        if not check or check["status"] != "valid":
            return None
    changed_files = []
    if previous.get("legacy"):
        # Without a frozen legacy delivery, accept newly committed Outcomes
        # or a newly verified supplement explicitly bound to that verdict.
        changed_files = sorted(
            asset["path"] for receipt in current.get("supervisor_deliveries", [])
            if receipt["goal_assessment"] == previous["assessment_id"]
            for asset in receipt["assets"]
        )
        if not received and not changed_files:
            return None
    else:
        if not re.fullmatch("[a-f0-9]{64}", revision):
            return None
        frozen_path = root / "delivery_revisions" / (revision + ".json")
        frozen = _read_object(frozen_path, root, limit=16_000_000)
        if hashlib.sha256(_canonical(frozen)).hexdigest() != revision:
            return None
        receipt = _read_object(root / previous["receipt_path"], root, limit=2_000_000)
        if (frozen.get("cycle_id") != anchor.name
                or receipt.get("assessment_id") != previous["assessment_id"]
                or receipt.get("delivery_revision") != revision
                or receipt.get("verdict") != "not_passed"):
            return None
        if delivery_only and not archive_ready:
            prior_submission = (receipt.get("submission_validation") or {}).get("submission") or {}
            if prior_submission.get("sha256") == check["submission"]["sha256"]:
                return None
        prior_files = _sources(frozen)
        if archive_ready:
            # Canonical aliases expose the same frozen source bytes to the Judge.
            for asset in archival["source_assets"]:
                prior_files.setdefault(asset["path"], asset["sha256"])
        changed_files = sorted(
            reference for reference, sha256 in _sources(current).items()
            if prior_files.get(reference) != sha256
        )
        if not received and not changed_files:
            return None

    if delivery_only:
        material = archival["material"] if archive_ready else check["submission"]
        if not any(row.get("reference") in changed_files and row.get("path") == material["path"]
                   and row.get("sha256") == material["sha256"]
                   for row in current.get("goal_material_sources", [])):
            return None

    delivery_revision = hashlib.sha256(_canonical(current)).hexdigest()
    with ScienceGraphStore(graph, readonly=True) as store:
        if store.goal_gate_assessment(anchor.name, delivery_revision=delivery_revision):
            return None

    return {
        "anchor_cycle": anchor.name,
        "stopped_cycle": latest.name,
        "reassessment_of": previous["assessment_id"],
        "delivery_revision": delivery_revision,
        "committed_outcomes": received,
        "changed_material_refs": changed_files,
    }


def goal_reassessment_plan(workflow: Any) -> dict | None:
    """Read authoritative incremental inputs without creating a new Cycle."""

    try:
        return _goal_reassessment_plan(workflow.spec, workflow._graph_database_path())
    except (OSError, ValueError):
        return None


def delivery_reassessment_plan(spec: Any) -> dict | None:
    """Wake an exhausted frontier for a checked submission or archival delivery."""
    from .archival_goal_delivery import SELECTION
    from .notebook import graph_database_path

    try:
        if not getattr(spec, "submission_contract_ref", None):
            selection = Path(spec.research_root) / SELECTION
            if not selection.is_file() or selection.is_symlink():
                return None
        graph = spec.graph_database or graph_database_path(Path(spec.branch_canvas).resolve().parent)
        return _goal_reassessment_plan(spec, graph, delivery_only=True)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def attach_archival_goal_delivery(spec: Any, selection: dict) -> dict:
    """Publish a verified report for an idle task's existing Goal Judge."""
    from .archival_goal_delivery import SELECTION, SELECTION_LIMIT, validate_archival_goal_delivery
    from .goal_terminal import science_commit_barrier
    from .operator_public_submission import _check_operator_control, _require_idle_owner
    from .public_submission import read_bounded_regular_file, strict_json
    from .runtime import atomic
    from .runtime_admission import admission_lock, assert_no_worker_claims
    from .specification import latest_cycle_dir
    from .supervisor_intervention import supervisor_enabled

    root = Path(spec.research_root)
    graph = spec.graph_database
    if not spec.task_mode or spec.submission_contract_ref or not graph:
        raise ValueError("Archival Goal delivery requires a registered task without a public submission contract")
    if not isinstance(selection, dict):
        raise ValueError("Archival Goal selection must be an object")
    raw = json.dumps(selection, ensure_ascii=False, indent=2) + "\n"
    data = raw.encode("utf-8")
    if len(data) > SELECTION_LIMIT:
        raise ValueError("Archival Goal selection exceeds its size limit")
    with admission_lock(root), science_commit_barrier(root, "exclusive"):
        if not supervisor_enabled(root):
            raise ValueError("Archival Goal delivery requires an enabled Supervisor")
        latest = latest_cycle_dir(root, task_mode=True)
        if latest is None or latest.name != selection.get("stopped_cycle"):
            raise ValueError("Archival Goal delivery names a stale stopped Cycle")
        assert_no_worker_claims(root)
        _require_idle_owner(root, latest, _check_operator_control(latest))
        with ScienceGraphStore(graph, readonly=True) as store:
            previous = store.latest_goal_gate_assessment()
        if not previous or previous["assessment_id"] != selection.get("goal_assessment"):
            raise ValueError("Archival Goal delivery names a stale assessment")
        anchor = root / previous["cycle_id"]
        if _check_operator_control(anchor).get("execution_active"):
            raise ValueError("An active Goal Cycle blocks archival Goal delivery")
        verified = validate_archival_goal_delivery(spec, graph, anchor, selection,
                                                  selection_id=hashlib.sha256(data).hexdigest())
        plan = _goal_reassessment_plan(spec, graph, delivery_only=True, verified_archive=verified)
        if not plan:
            raise ValueError("Archival Goal report does not qualify for a delivery-only reassessment")
        path = root / SELECTION
        if path.parent.resolve() != path.parent or path.is_symlink():
            raise ValueError("Unsafe archival Goal selector path")
        if path.exists():
            existing = read_bounded_regular_file(path, SELECTION_LIMIT)
            if existing == data:
                return plan
            retained = strict_json(existing)
            if not isinstance(retained, dict) or retained.get("goal_assessment") == previous["assessment_id"]:
                raise ValueError("An archival Goal report is already selected for this assessment")
        atomic(path, raw, sync_directory=True)
        return plan


def reassessment_launch_identity(plan: dict, launch_id: str) -> dict:
    return {"launch_id": launch_id, **{
        key: plan[key] for key in ("stopped_cycle", "anchor_cycle", "reassessment_of", "delivery_revision")
    }}


def recorded_reassessment(root: Path, attempt: dict) -> dict | None:
    """Recognize a committed verdict even if its Runner exit was not observed."""
    from .specification import TaskSpec

    try:
        spec = TaskSpec.from_task_root(root)
        if spec.graph_database is None or not attempt.get("delivery_revision"):
            return None
        with ScienceGraphStore(spec.graph_database, readonly=True) as graph:
            record = graph.goal_gate_assessment(attempt["anchor_cycle"], delivery_revision=attempt["delivery_revision"])
        if record and record.get("supersedes") == attempt.get("reassessment_of"):
            return {"assessment_id": record["assessment_id"], "goal_verdict": record["verdict"]}
    except (OSError, ValueError, KeyError, sqlite3.Error):
        return None
    return None


def run_goal_reassessment(workflow: Any, plan: dict, *, watch_stopped: bool = False,
                          recovery_pin: dict | None = None) -> dict | None:
    """Run the existing Goal Gate while the managed caller owns execution."""

    if not plan or not workflow.supervisor_enabled() or goal_reassessment_plan(workflow) != plan:
        return None
    root = Path(workflow.spec.research_root)
    cycle = root / plan["anchor_cycle"]
    state = _read_object(cycle / "research_state.json", root, limit=4_000_000)
    closed_fields = ("status", "phase", "execution_active", "resumable", "active_invocation_id")
    closed_state = {key: state[key] for key in closed_fields if key in state}
    pending = state.get("goal_delivery_pending") or {}
    if pending.get("closed_status") in {"over", "completed"}:
        state.update(status=pending["closed_status"], phase="complete")
    workflow._bind_cycle(cycle, create=False)
    workflow._restore_resumed_cycle_routes(state)
    from .goal_gate import GoalGate

    def check_stopped_cycle() -> None:
        from .runtime_manager import latest_cycle_name
        from .workflow_support import RecoveryStop

        control = root / plan["stopped_cycle"] / "recovery_control.json"
        if (not workflow.supervisor_enabled() or latest_cycle_name(root) != plan["stopped_cycle"]
                or control.exists() or control.is_symlink()):
            raise RecoveryStop("Stopped Cycle withdrew its delivery reassessment authority")

    if watch_stopped:
        workflow._delivery_reassessment_control = check_stopped_cycle
    try:
        assessment = GoalGate(workflow, state, reassessment=plan, recovery_pin=recovery_pin).run()
    finally:
        if watch_stopped:
            del workflow._delivery_reassessment_control
        # Preserve any resumable frozen Judge snapshot, but do not reopen the
        # scientific Cycle after an interrupted reassessment.
        if state.get("goal_delivery_pending"):
            if watch_stopped and closed_state.get("status") in {"over", "completed"}:
                for key in closed_fields:
                    state.pop(key, None)
                state.update(closed_state)
            else:
                state.update(status="over", phase="complete", execution_active=False, resumable=False)
        workflow._persist(state)
    return {"cycle_id": cycle.name, "assessment": assessment,
            "delivery_revision": plan["delivery_revision"]}
