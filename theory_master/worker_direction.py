"""Research direction wake conditions and operator guidance."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def research_direction_fingerprint(spec: Any) -> dict[str, Any]:
    """G6 deterministic wake signal: committed Outcomes, Scientific Model version, plan hash."""
    from . import multi_worker, worker_state, worker_store
    import hashlib
    import sqlite3
    root = Path(spec.research_root).resolve()
    ids: list[str] = []
    graph = worker_store._graph_path(spec)
    if graph.is_file():
        from .science_graph_store import ScienceGraphStore
        with ScienceGraphStore(graph, readonly=True) as store:
            try:
                ids = sorted(str(row[0]) for row in store.db.execute("SELECT id FROM cycle_evaluations"))
            except sqlite3.OperationalError:
                ids = []
    from .scientific_model import state_path
    model_state = state_path(root)
    version = multi_worker._read_json(model_state).get("version") if model_state.is_file() else None
    plan = Path(spec.document)
    return {
        "schema": worker_state.DIRECTION_FINGERPRINT_SCHEMA,
        "committed_outcome_count": len(ids),
        "committed_outcome_ids_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "scientific_model_version": version if isinstance(version, int) else None,
        "research_plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest() if plan.is_file() else None,
    }


def _direction_values(fingerprint: Any) -> tuple[Any, ...] | None:
    from . import worker_state
    if not isinstance(fingerprint, dict) or not all(k in fingerprint for k in worker_state.DIRECTION_FINGERPRINT_FIELDS):
        return None
    return tuple(fingerprint[k] for k in worker_state.DIRECTION_FINGERPRINT_FIELDS)


def _retry_direction_path(root: Path, worker_id: str) -> Path:
    from . import worker_store
    return worker_store.worker_dir(root, worker_id) / "retry_direction.json"


def _pending_retry_direction(root: Path, worker_id: str) -> dict[str, Any] | None:
    from . import multi_worker
    request = multi_worker._read_json(_retry_direction_path(root, worker_id))
    return request if request.get("status") == "pending" else None


def _waiting_local_replan_reason(root: Path, worker: dict[str, Any]) -> str | None:
    """Read a historical waiting attempt without reopening or editing its Cycle."""
    from . import multi_worker
    cycle = str(worker.get("current_cycle") or "")
    if (worker.get("claim_id") or worker.get("epoch") is not None
            or not cycle.isascii() or not cycle.isdecimal() or int(cycle) < 1):
        return None
    directory, path = root / cycle, root / cycle / "research_state.json"
    try:
        if (directory.is_symlink() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > 1_048_576):
            return None
        state = multi_worker._read_json(path)
    except (OSError, ValueError):
        return None
    if (state.get("worker_id") != worker.get("worker_id")
            or str(state.get("cycle_id") or "") != cycle):
        return None
    from .task_progression import local_replan_reason
    return local_replan_reason(state)


def _partition_waiting_workers(spec: Any, workers: list[dict[str, Any]]
                               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Wake waiting Workers for a new direction, explicit retry or verified local replan.

    An unknown fingerprint supplies no wake signal. A canonical completed local
    failure may independently return to Lead under the daemon's remaining quota.
    """
    from . import worker_state
    root = Path(spec.research_root).resolve()
    waiting = [w for w in workers if w.get("status") == worker_state.WAITING_RESEARCH_DIRECTION
               and not w.get("dismissed") and not w.get("process_alive")]
    if not waiting:
        return [], [], None
    try:
        current = research_direction_fingerprint(spec)
    except (OSError, ValueError):
        current = None
    woken, dormant = [], []
    for worker in waiting:
        retry = _pending_retry_direction(root, worker["worker_id"])
        local_replan = _waiting_local_replan_reason(root, worker)
        stored = _direction_values(worker.get("wake_fingerprint"))
        changed = (current is not None and stored is not None
                   and stored != _direction_values(current))
        if retry or changed or local_replan:
            woken.append({**worker, "wake_reason": "retry_direction" if retry
                          else "direction_fingerprint_changed" if changed
                          else "local_replan:" + local_replan})
        else:
            dormant.append(worker)
    return woken, dormant, current


def _notify_all_waiting(root: Path, dormant: list[dict[str, Any]],
                        current: dict[str, Any] | None) -> dict[str, Any]:
    """One important event per all-waiting episode (deduplicated by registry key)."""
    import hashlib
    from .observability.incident_registry import register_cycle_incident
    ids = sorted(w["worker_id"] for w in dormant)
    episode = json.dumps({"workers": ids, "fingerprint": _direction_values(current),
                          "waiting": sorted(str(w.get("waiting_since") or "") for w in dormant)},
                         sort_keys=True, default=str)
    incident, created = register_cycle_incident(
        root, severity="important", kind="all_workers_waiting_research_direction",
        source_event="sdk.worker_runtime",
        summary=("All living Workers are waiting for a research direction: " + ", ".join(ids)
                 + ". Change the Research Plan, add operator guidance or request retry_direction."),
        code="all_workers_waiting_research_direction",
        discriminator=hashlib.sha256(episode.encode("utf-8")).hexdigest()[:20],
        fields={"worker_ids": ids, "event_class": "research_direction_notice"},
    )
    return {"incident_id": incident.get("incident_id"), "created": created, "worker_ids": ids}


def detect_critical_knowledge_change(spec: Any, *, plan_digest: str,
                                     hypothesis_id: str | None = None,
                                     cycle_id: str | None = None,
                                     entry_node_id: str | None = None) -> dict[str, Any] | None:
    """G10: only a Research Plan change, or this Cycle's OWN bound H deleted/closed.

    Parent H or other frontier changes are ordinary science (I-06). Closure by
    this Cycle's own committed Outcome or by the Goal seal is not a notice.
    """
    from . import worker_store
    import hashlib
    hypothesis_id = hypothesis_id or entry_node_id
    plan = Path(spec.document)
    if hashlib.sha256(plan.read_bytes()).hexdigest() != plan_digest:
        return {"reason": "research_plan_changed", "hypothesis_id": hypothesis_id}
    if not hypothesis_id:
        return None
    graph_path = worker_store._graph_path(spec).resolve()
    if not graph_path.is_file():
        raise FileNotFoundError(f"Canonical SQLite graph is required: {graph_path}")
    from .science_graph_store import GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON, ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        if store.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone() is None:
            return {"reason": "own_hypothesis_deleted", "hypothesis_id": hypothesis_id}
        frontier = store.frontier_view().get(hypothesis_id)
        if not frontier or frontier["frontier_status"] != "closed":
            return None
        branch = frontier["branches"][0] if frontier["branches"] else {}
        if branch.get("reason") in {GOAL_COMPLETED_STOP, GOAL_CLOSE_REASON}:
            return None
        if cycle_id and store.db.execute(
                "SELECT 1 FROM cycle_evaluations WHERE cycle_id=? AND hypothesis_id=?",
                (str(cycle_id), hypothesis_id)).fetchone() is not None:
            return None
    return {"reason": "own_hypothesis_closed", "hypothesis_id": hypothesis_id}


def _restore_cycle_operator_guidance(state: dict[str, Any], cycle_dir: Path) -> dict[str, Any]:
    """Inject pending human guidance when Worker resumes an existing Cycle."""
    from .resume_preparation import restore_operator_guidance
    return restore_operator_guidance(state, cycle_dir)
