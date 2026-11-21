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


def science_authority_digest(spec: Any, graph_path: Path, *, empty_after_cycle: str | None = None,
                             frontier_baseline: dict | None = None) -> str:
    """Exclude delivery/Goal projections, include the actual scientific records."""
    from .science_graph_store import ScienceGraphStore
    from .specification import list_cycle_dirs
    root = Path(spec.research_root)
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = _without_goal_assessments(store.graph_view())
    if frontier_baseline is not None:
        from .operator_delivery import retain_frontier_projection_times
        retain_frontier_projection_times(graph, frontier_baseline)
    files = {str(Path(spec.document).resolve()): hashlib.sha256(Path(spec.document).read_bytes()).hexdigest()}
    locks = {}
    cycles = list_cycle_dirs(root, task_mode=spec.task_mode)
    if empty_after_cycle is not None:
        from .operator_delivery import retained_scientific_cycles
        cycles = retained_scientific_cycles(cycles, empty_after_cycle)
    for cycle in cycles:
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
    from .goal_materials import resolve_material
    with ScienceGraphStore(graph_path, readonly=True) as store:
        graph = store.graph_view()
    refs = {str(outcome[key]) for node in graph.get("nodes") or []
            for outcome in node.get("cycle_outcomes") or []
            for key in ("report_ref", "document_ref") if outcome.get(key)}
    return [resolve_material(spec, reference) for reference in sorted(refs)]


def current_delivery_payload(spec: Any, graph_path: Path, cycle_dir: Path, *,
                             verified_archive: dict | None = None) -> dict[str, Any]:
    """Read the current delivery identity without creating any revision files."""
    from .delivery_materials import task_delivery_material_index
    from .delivery_publish import read_publications
    from .goal_materials import goal_material_sources
    from .science_graph_store import ScienceGraphStore
    from .scientific_model import load_from_paths
    model = load_from_paths(Path(spec.research_root), Path(spec.document), goal_id=spec.goal_id)
    material_index = task_delivery_material_index(Path(spec.research_root))
    from .operator_delivery import delivery_additions
    authority_digest, additions = delivery_additions(spec, graph_path, cycle_dir)
    if additions:
        material_index["operator_delivery_supplements"] = additions
    reports = _report_versions(spec, graph_path)
    publications = read_publications(spec)
    with ScienceGraphStore(graph_path, readonly=True) as store:
        agent_view = store.agent_goal_completion_view()
    payload = {"schema": "theory-master/delivery-revision/v1", "cycle_id": cycle_dir.name,
            "science_digest": authority_digest or science_authority_digest(spec, graph_path),
            "scientific_model_sha256": hashlib.sha256(_canonical(model)).hexdigest(),
            "material_index": material_index,
            "report_versions": reports}
    from .supervisor_delivery import supervisor_delivery_materials
    supplements = supervisor_delivery_materials(spec, graph_path, cycle_dir)
    if supplements:
        payload["supervisor_deliveries"] = supplements
    from .archival_goal_delivery import read_archival_goal_delivery

    archival = verified_archive
    if archival is None:
        archival = read_archival_goal_delivery(spec, graph_path, cycle_dir)
    if archival is not None:
        payload["archival_goal_delivery"] = archival
    from .public_submission import load_submission_contract
    from .operator_public_submission import current_operator_submission
    declaration = load_submission_contract(spec)
    operator_submission = current_operator_submission(graph_path, cycle_dir, declaration)
    if operator_submission:
        payload["operator_public_submission"] = operator_submission
    if publications:
        payload["delivery_publications"] = publications
    payload["goal_material_sources"] = goal_material_sources(
        spec, [agent_view, material_index, reports, supplements, publications])
    if archival is not None:
        material = archival["material"]
        archival_rows = {asset["reference"]: asset for asset in archival["source_assets"]}
        archival_rows.update({asset["path"]: {**asset, "reference": asset["path"]}
                              for asset in archival["source_assets"]})
        archival_rows[material["reference"]] = material
        payload["goal_material_sources"] = [row for row in payload["goal_material_sources"]
                                            if row["reference"] not in archival_rows]
        payload["goal_material_sources"].extend(archival_rows.values())
    if operator_submission:
        # Verified literal local paths can contain characters that denote URI
        # fragments or escapes for ordinary linked materials.
        operator_rows = {asset["path"]: {**asset, "reference": asset["path"]}
                         for role in ("receipt", "submission", "verification")
                         if (asset := operator_submission.get(role)) is not None}
        payload["goal_material_sources"] = [row for row in payload["goal_material_sources"]
                                            if row["reference"] not in operator_rows]
        payload["goal_material_sources"].extend(operator_rows.values())
    if declaration is not None:
        payload["submission_contract"] = declaration
        # Explicit config grants this one public file, without scanning or
        # admitting unrelated benchmark/private files to the snapshot.
        references = {declaration["path"], declaration["reference"]}
        payload["goal_material_sources"] = [row for row in payload["goal_material_sources"]
            if row.get("reference") not in references]
        payload["goal_material_sources"].extend({"reference": reference,
            "path": declaration["path"], "sha256": declaration["sha256"], "status": "available"}
            for reference in sorted(references))
    return payload


def explicit_repair_payload(spec: Any, graph_path: Path, cycle_dir: Path,
                            assessment: dict[str, Any]) -> dict[str, Any]:
    """Bind administrative equivalence to one canonical explicit repair only."""
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        history = store.goal_gate_history(cycle_dir.name)
        outcome = store.db.execute("SELECT verdict FROM cycle_evaluations WHERE cycle_id=?",
                                   (cycle_dir.name,)).fetchone()
    canonical = history[-1] if history else None
    if (not canonical or not outcome or outcome["verdict"] not in {"supported", "rejected", "open"}
            or canonical.get("verdict") != "not_passed"
            or any(canonical.get(key) != assessment.get(key) for key in
                   ("assessment_id", "science_digest", "receipt_path"))
            or not canonical.get("science_digest") or not canonical.get("receipt_path")):
        raise ValueError("Explicit delivery repair requires the current canonical Goal assessment")
    payload = current_delivery_payload(spec, graph_path, cycle_dir)
    expected = canonical["science_digest"]
    from .specification import cycle_number
    number = cycle_number(cycle_dir.name)
    index = payload["material_index"]
    later = ([row for row in index["cycles"] if cycle_number(row["cycle_id"]) is None
              or cycle_number(row["cycle_id"]) > number] if number is not None else [])
    if payload["science_digest"] != expected or later:
        from .operator_delivery import assessment_authority_digest
        if assessment_authority_digest(spec, graph_path, cycle_dir, canonical) != expected:
            raise ValueError("delivery repair cannot change scientific authority")
        if any(row != {"cycle_id": row["cycle_id"], "evidence_ids": [],
                       "registered_material_groups": 0} for row in later):
            raise ValueError("Later Cycle has delivery materials; administrative equivalence refused")
        excluded = {row["cycle_id"] for row in later}
        if any(row.get("cycle_id") in excluded for row in index["materials"]):
            raise ValueError("Later Cycle has delivery materials; administrative equivalence refused")
        payload["material_index"] = {**index, "cycles": [row for row in index["cycles"]
                                                         if row["cycle_id"] not in excluded]}
        payload["science_digest"] = expected
    return payload


def freeze_delivery_revision(spec: Any, graph_path: Path, cycle_dir: Path, *,
                             require_unchanged_science: bool = False,
                             reassessment_of: str = "", expected_revision: str = "",
                             repair_assessment: dict[str, Any] | None = None) -> dict[str, str]:
    """Caller holds the exclusive barrier and has checked closed/terminal/unsealed."""
    root = Path(spec.research_root)
    if repair_assessment is not None and not require_unchanged_science:
        raise ValueError("Assessment-bound delivery repair must retain unchanged scientific authority")
    payload = (explicit_repair_payload(spec, graph_path, cycle_dir, repair_assessment)
               if repair_assessment is not None else current_delivery_payload(spec, graph_path, cycle_dir))
    digest = payload["science_digest"]
    from .science_graph_store import ScienceGraphStore
    with ScienceGraphStore(graph_path, readonly=True) as store:
        history = store.goal_gate_history(cycle_dir.name)
    latest = history[-1] if history else None
    if reassessment_of:
        if not latest or latest["assessment_id"] != reassessment_of:
            raise ValueError("Supervisor reassessment lost its prior Goal verdict")
    elif latest and latest.get("science_digest") and latest["science_digest"] != digest:
        raise ValueError("delivery repair cannot change scientific authority; stored assessment has a different science_digest")
    pointer = cycle_dir / "delivery_revision.json"
    if require_unchanged_science and pointer.is_file():
        previous = json.loads(pointer.read_text(encoding="utf-8"))
        if previous.get("science_digest") != digest:
            raise ValueError("delivery repair cannot change scientific authority; use the scientific workflow")
    index = payload["material_index"]
    index_path = root / "delivery_material_index.json"
    revision = hashlib.sha256(_canonical(payload)).hexdigest()
    if expected_revision and revision != expected_revision:
        raise ValueError("Goal reassessment delivery changed before capture")
    revision_path = root / "delivery_revisions" / f"{revision}.json"
    _write_immutable(revision_path, payload)
    write_json(index_path, index)
    access = {"index_path": str(index_path), "revision_path": str(revision_path),
              "delivery_revision": revision, "science_digest": digest}
    write_json(pointer, access)
    return access


def repair_delivery(workflow: Any, *, cycle: str | int | None = None,
                    reassess: bool = False, author_request: Path | None = None) -> dict[str, Any]:
    """Repair only delivery identity in an existing closed, terminal scientific Cycle."""
    if author_request is not None:
        from .operator_delivery import author_delivery
        return author_delivery(workflow, author_request, cycle=cycle, reassess=reassess)
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
            if outcome is not None and outcome["verdict"] in {"supported", "rejected", "open"}:
                selected = candidate, state, outcome["verdict"]
                break
        if selected is None:
            raise ValueError("delivery repair requires an existing closed, terminal scientific Cycle")
        candidate, state, source_verdict = selected
        if ((candidate / "operator_delivery" / "request.json").exists()
                and not (candidate / "operator_delivery" / "supplement.json").exists()):
            raise ValueError("Author delivery remains pending; resume the explicit author request before reassessment")
        with ScienceGraphStore(graph, readonly=True) as store:
            history = store.goal_gate_history(candidate.name)
        repair_assessment = history[-1] if history and history[-1].get("science_digest") else None
        access = freeze_delivery_revision(spec, graph, candidate, require_unchanged_science=True,
                                          repair_assessment=repair_assessment)
        if not reassess:
            return {"cycle_id": candidate.name, "reassessed": False, **access}
    workflow._bind_cycle(candidate, create=False)
    workflow._restore_resumed_cycle_routes(state)
    try:
        result = workflow._run_goal_completion(state, {"verdict": source_verdict}, delivery_repair=True,
                                               repair_assessment=repair_assessment)
    except Exception:
        # A Goal-delivery invocation does not reopen the committed scientific
        # Cycle. Keep its completed invocation available for exact replay.
        state.update(status="over", phase="complete", execution_active=False, resumable=False)
        workflow._persist(state)
        raise
    workflow._persist(state)
    return {"cycle_id": candidate.name, "reassessed": True, **access, "assessment": result}
