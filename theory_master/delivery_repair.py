"""Deterministic delivery revisions; scientific authorities remain unchanged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .runtime import write_json


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _without_goal_assessments(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_goal_assessments(item) for key, item in value.items()
                if key not in {"goal_gate", "goal_gate_history"}}
    if isinstance(value, list):
        return [_without_goal_assessments(item) for item in value]
    return value


def science_authority_digest(spec: Any, graph_path: Path) -> str:
    """Exclude delivery/Goal projections, include the actual scientific records."""
    from .science_graph_store import ScienceGraphStore
    from .specification import list_cycle_dirs
    root = Path(spec.research_root)
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = _without_goal_assessments(store.graph_view())
    files = {str(Path(spec.document).resolve()): hashlib.sha256(Path(spec.document).read_bytes()).hexdigest()}
    locks = {}
    for cycle in list_cycle_dirs(root, task_mode=spec.task_mode):
        state_path = cycle / "research_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            locks[cycle.name] = state.get("hypothesis_lock")
        for name in ("registry.jsonl", "lifecycle.jsonl", "registration_batches.jsonl",
                     "criteria.json", "assessments.jsonl"):
            path = cycle / "evidence" / name
            if path.is_file():
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(_canonical({"graph": graph, "files": files, "hypothesis_locks": locks})).hexdigest()


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable delivery artifact conflicts: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Caller holds the task's exclusive science barrier.
    write_json(path, payload)


def _report_versions(spec: Any, graph_path: Path) -> list[dict[str, str]]:
    """Only registered report links, never an unrestricted workspace scan."""
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = store.graph_view()
    refs = {str(outcome[key]) for node in graph.get("nodes") or []
            for outcome in node.get("cycle_outcomes") or []
            for key in ("report_ref", "document_ref") if outcome.get(key)}
    rows = []
    for reference in sorted(refs):
        path = Path(reference)
        if not path.is_absolute():
            path = Path(spec.branch_canvas).parent / path
        row = {"reference": reference, "path": str(path.resolve())}
        if path.is_file() and not path.is_symlink():
            row.update(status="available", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            row["status"] = "missing"
        rows.append(row)
    return rows


def current_delivery_payload(spec: Any, graph_path: Path, cycle_dir: Path) -> dict[str, Any]:
    """Read the current delivery identity without creating any revision files."""
    from .delivery_materials import task_delivery_material_index
    return {"schema": "theory-master/delivery-revision/v1", "cycle_id": cycle_dir.name,
            "science_digest": science_authority_digest(spec, graph_path),
            "material_index": task_delivery_material_index(Path(spec.research_root)),
            "report_versions": _report_versions(spec, graph_path)}


def freeze_delivery_revision(spec: Any, graph_path: Path, cycle_dir: Path, *,
                             require_unchanged_science: bool = False) -> dict[str, str]:
    """Caller holds the exclusive barrier and has checked closed/support/unsealed."""
    root = Path(spec.research_root)
    payload = current_delivery_payload(spec, graph_path, cycle_dir)
    digest = payload["science_digest"]
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        history = store.goal_gate_history(cycle_dir.name)
    if any(row.get("science_digest") and row["science_digest"] != digest for row in history):
        raise ValueError("delivery repair cannot change scientific authority; stored assessment has a different science_digest")
    pointer = cycle_dir / "delivery_revision.json"
    if require_unchanged_science and pointer.is_file():
        previous = json.loads(pointer.read_text(encoding="utf-8"))
        if previous.get("science_digest") != digest:
            raise ValueError("delivery repair cannot change scientific authority; use the scientific workflow")
    index = payload["material_index"]
    index_path = root / "delivery_material_index.json"
    revision = hashlib.sha256(_canonical(payload)).hexdigest()
    revision_path = root / "delivery_revisions" / f"{revision}.json"
    _write_immutable(revision_path, payload)
    write_json(index_path, index)
    access = {"index_path": str(index_path), "revision_path": str(revision_path),
              "delivery_revision": revision, "science_digest": digest}
    write_json(pointer, access)
    return access


def repair_delivery(workflow: Any, *, cycle: str | int | None = None,
                    reassess: bool = False) -> dict[str, Any]:
    """Repair only delivery identity in an existing closed, supported Cycle."""
    from .goal_terminal import GoalSealedError, goal_sealed, science_commit_barrier
    from .science_graph_store import ScienceGraphStore
    from .specification import find_cycle, list_cycle_dirs
    spec = workflow.spec
    graph = workflow._graph_database_path()
    with science_commit_barrier(spec.research_root, "exclusive"):
        if goal_sealed(spec.research_root, graph, spec=spec) is not None:
            raise GoalSealedError("sealed Goal cannot be reopened by delivery repair")
        candidates = ([find_cycle(spec.research_root, str(cycle), task_mode=spec.task_mode)]
                      if cycle is not None else list_cycle_dirs(spec.research_root, task_mode=spec.task_mode))
        selected = None
        for candidate in candidates:
            state = json.loads((candidate / "research_state.json").read_text(encoding="utf-8"))
            if str(state.get("status") or "").lower() not in {"over", "completed"}:
                pending = state.get("goal_delivery_pending") or {}
                pointer = candidate / "delivery_revision.json"
                frozen = json.loads(pointer.read_text(encoding="utf-8")) if pointer.is_file() else {}
                if (pending.get("cycle_id") == candidate.name
                        and pending.get("closed_status") in {"over", "completed"}
                        and pending.get("delivery_revision") == frozen.get("delivery_revision")):
                    state.update(status="over", phase="complete")
                else:
                    if cycle is not None:
                        raise ValueError("delivery repair requires a closed Cycle")
                    continue
            with ScienceGraphStore(graph, readonly=True) as store:
                outcome = store.db.execute("SELECT verdict FROM cycle_evaluations WHERE cycle_id=?",
                                           (candidate.name,)).fetchone()
            if outcome is not None and outcome["verdict"] == "supported":
                selected = candidate, state
                break
        if selected is None:
            raise ValueError("delivery repair requires an existing closed, supported Cycle")
        candidate, state = selected
        access = freeze_delivery_revision(spec, graph, candidate, require_unchanged_science=True)
        if not reassess:
            return {"cycle_id": candidate.name, "reassessed": False, **access}
        workflow._bind_cycle(candidate, create=False)
        workflow._restore_resumed_cycle_routes(state)
        try:
            result = workflow._run_goal_completion(state, {"verdict": "supported"}, delivery_repair=True)
        except Exception:
            # A Goal-delivery invocation does not reopen the committed scientific
            # Cycle. Keep its completed invocation available for exact replay.
            state.update(status="over", phase="complete", execution_active=False, resumable=False)
            workflow._persist(state)
            raise
        workflow._persist(state)
        return {"cycle_id": candidate.name, "reassessed": True, **access, "assessment": result}
