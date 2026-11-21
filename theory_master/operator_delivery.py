"""Explicit, bounded author correction of an existing public-interface delivery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "theory-master/operator-delivery-request/v1"
AUTHORITY = "operator_delivery_structure_only"


def retained_scientific_cycles(cycles: list[Path], source: str) -> list[Path]:
    """Ignore only later closed, empty qualification attempts, never science."""
    from .specification import cycle_number
    from .task_progression import local_replan_reason
    number = cycle_number(source)
    if source not in [cycle.name for cycle in cycles] or number is None:
        raise ValueError("Delivery source Cycle does not exist")
    later = [cycle for cycle in cycles if cycle_number(cycle.name) is None
             or cycle_number(cycle.name) > number]
    for cycle in later:
        state = json.loads((cycle / "research_state.json").read_text())
        if (local_replan_reason(state) != "no_qualified_candidate"
                or state.get("hypothesis_lock") not in (None, {}, {"status": "unlocked"})
                or state.get("scientific_verdict") is not None
                or state.get("scientific_phase") not in (None, "qualification")):
            raise ValueError("Later Cycle has active or scientific authority; delivery correction refused")
        if any(path.is_file() and path.stat().st_size for path in (cycle / "evidence").glob("*")):
            raise ValueError("Later Cycle has Evidence authority; delivery correction refused")
    return [cycle for cycle in cycles if cycle not in later]


def _read(path: Path, limit: int = 128 * 1024) -> dict:
    from .public_submission import read_bounded_regular_file, strict_json
    value = strict_json(read_bounded_regular_file(path, limit))
    if not isinstance(value, dict):
        raise ValueError("Operator delivery metadata must be an object")
    return value


def _request_path(cycle: Path) -> Path:
    return cycle / "operator_delivery" / "request.json"


def retain_frontier_projection_times(graph: dict, baseline: dict) -> None:
    """A projection timestamp is replaceable only when every other field agrees."""
    previous = {node.get("id"): node for node in baseline.get("nodes", [])}
    for node in graph.get("nodes", []):
        old = previous.get(node.get("id"), {}).get("frontiers", [])
        current = node.get("frontiers", [])
        if len(old) != len(current):
            continue
        for row, original in zip(current, old):
            if ({key: value for key, value in row.items() if key != "changed_at"}
                    == {key: value for key, value in original.items() if key != "changed_at"}):
                if "changed_at" in original:
                    row["changed_at"] = original["changed_at"]


def assessment_authority_digest(spec: Any, graph: Path, cycle: Path, assessment: dict) -> str:
    from .delivery_repair import _canonical, science_authority_digest
    from .goal_snapshot import GoalAuthoritySnapshot
    receipt = _read(Path(assessment["receipt_path"]))
    snapshot = GoalAuthoritySnapshot.restore(Path(receipt["snapshot_manifest"]), receipt["snapshot_manifest_sha256"])
    from .scientific_model import load_from_paths
    original_delivery = _read(snapshot.root / "delivery_revision.json", 16 * 1024 * 1024)
    current_model = load_from_paths(spec.research_root, Path(spec.document), goal_id=spec.goal_id)
    if hashlib.sha256(_canonical(current_model)).hexdigest() != original_delivery["scientific_model_sha256"]:
        raise ValueError("Scientific Model changed after the original Goal assessment")
    baseline = json.loads(snapshot.graph_path.read_text())
    return science_authority_digest(spec, graph, empty_after_cycle=cycle.name, frontier_baseline=baseline)


def delivery_additions(spec: Any, graph: Path, cycle: Path) -> tuple[str | None, list[dict]]:
    """Expose only SDK-bound delivery files, without admitting new Evidence."""
    path = _request_path(cycle)
    if not path.exists():
        return None, []
    from .delivery_repair import _canonical
    from .science_graph_store import ScienceGraphStore
    marker = _read(path)
    request = marker.get("request") or {}
    if (request.get("schema") != REQUEST_SCHEMA or request.get("cycle_id") != cycle.name
            or marker.get("request_sha256") != hashlib.sha256(_canonical(request)).hexdigest()):
        raise ValueError("Operator delivery request identity changed")
    with ScienceGraphStore(graph, readonly=True) as store:
        history = store.goal_gate_history(cycle.name)
    original = next((row for row in history if row.get("assessment_id") == request.get("assessment_id")), None)
    if (not original or original.get("verdict") != "not_passed"
            or original.get("science_digest") != marker.get("science_digest")
            or assessment_authority_digest(spec, graph, cycle, original) != marker.get("science_digest")):
        raise ValueError("Original scientific authority or Goal gap changed")
    from .goal_materials import resolve_material
    requirement = resolve_material(spec, request["required_material_ref"])
    if requirement != marker.get("required_material"):
        raise ValueError("The explicit public interface requirement changed")
    additions = [{"cycle_id": cycle.name, "authority": "operator_public_interface_requirement",
                  "assets": [{**requirement, "role": "input"}]}]
    result_path = cycle / "operator_delivery" / "supplement.json"
    if not result_path.exists():
        return marker["science_digest"], additions
    from .delivery_materials import execution_materials
    result = _read(result_path)
    if result.get("request_sha256") != marker["request_sha256"]:
        raise ValueError("Delivery supplement belongs to another request")
    materials = execution_materials(result["binding"], result["producer_invocation"], cycle_dir=cycle)
    if materials.get("integrity", {}).get("status") != "verified":
        raise ValueError("Authored delivery supplement is missing or changed")
    additions.append({"cycle_id": cycle.name, "authority": AUTHORITY,
                      "source_assessment_id": original["assessment_id"], **materials})
    return marker["science_digest"], additions


def _load_request(workflow: Any, path: Path, cycle: str | int | None):
    from .goal_materials import resolve_material
    from .science_graph_store import ScienceGraphStore
    from .specification import find_cycle
    root = Path(workflow.spec.research_root).resolve()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Author request must be SDK-private, outside the Agent workspace")
    try:
        private = (root / ".theory-master").resolve(strict=True)
        contained = path.resolve(strict=True).is_relative_to(private)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Unsafe or unreadable SDK-private author request") from exc
    if not contained:
        raise ValueError("Author request must be SDK-private, outside the Agent workspace")
    request = _read(path)
    if (set(request) != {"schema", "cycle_id", "assessment_id", "required_material_ref", "instructions"}
            or request.get("schema") != REQUEST_SCHEMA
            or not isinstance(request.get("cycle_id"), str)
            or (cycle is not None and str(cycle) != request["cycle_id"])
            or not isinstance(request.get("instructions"), str)
            or not 1 <= len(request["instructions"].strip()) <= 8000):
        raise ValueError("Invalid explicit public-interface correction request")
    selected = find_cycle(root, request["cycle_id"], task_mode=workflow.spec.task_mode)
    state = _read(selected / "research_state.json", 16 * 1024 * 1024)
    retrying = ((state.get("goal_submission_gap") or {}).get("authority") == AUTHORITY)
    if state.get("status") not in {"over", "completed"} and not retrying:
        raise ValueError("Author correction requires an existing closed supported Cycle")
    if state.get("scientific_verdict") != "supported" or state.get("protocol_health", "valid") != "valid":
        raise ValueError("Author correction cannot repair scientific authority or a protocol violation")
    with ScienceGraphStore(workflow._graph_database_path(), readonly=True) as store:
        outcome = store.db.execute("SELECT verdict FROM cycle_evaluations WHERE cycle_id=?", (selected.name,)).fetchone()
        history = store.goal_gate_history(selected.name)
    if (not outcome or outcome["verdict"] != "supported" or not history
            or history[-1].get("verdict") != "not_passed"
            or history[-1].get("assessment_id") != request["assessment_id"]):
        raise ValueError("Author correction must bind the current supported Cycle's not_passed assessment")
    reference = request["required_material_ref"]
    if not isinstance(reference, str) or not reference.startswith("benchmark/"):
        raise ValueError("A public benchmark interface requirement must be explicit")
    material = resolve_material(workflow.spec, reference)
    if material.get("status") != "available":
        raise ValueError("The referenced public interface requirement is unavailable")
    return request, selected, state, history[-1], material


def _snapshot(workflow: Any, state: dict):
    from .delivery_repair import _canonical, current_delivery_payload, freeze_delivery_revision
    from .goal_snapshot import GoalSnapshotSeed, materialize_goal_snapshot
    from .science_graph_store import ScienceGraphStore
    from .scientific_model import load_from_paths
    spec, graph, cycle = workflow.spec, workflow._graph_database_path(), workflow.cycle_dir
    access = freeze_delivery_revision(spec, graph, cycle, require_unchanged_science=True)
    payload = current_delivery_payload(spec, graph, cycle)
    with ScienceGraphStore(graph, readonly=True) as store:
        view, agent = store.graph_view(), store.agent_goal_completion_view()
    model = load_from_paths(spec.research_root, Path(spec.document), goal_id=spec.goal_id)
    seed = GoalSnapshotSeed(Path(spec.document).read_bytes(), _canonical(view), _canonical(agent),
                            _canonical(payload), _canonical(access), _canonical(model))
    return materialize_goal_snapshot(spec, cycle, seed), access


def author_delivery(workflow: Any, request_path: Path, *, cycle=None, reassess=False) -> dict:
    """An explicit normal-task repair; never an automatic not_passed bypass."""
    from .runtime_manager import _file_lock, launch_lock_path, owner_lock_path, runtime_health
    root = Path(workflow.spec.research_root)
    with _file_lock(launch_lock_path(root), nonblocking=True), _file_lock(owner_lock_path(root), nonblocking=True):
        if runtime_health(root).get("active"):
            raise ValueError("Author delivery requires an idle Task with no active Runner or Supervisor")
        return _author_idle(workflow, Path(request_path), cycle=cycle, reassess=reassess)


def _author_idle(workflow: Any, request_path: Path, *, cycle, reassess) -> dict:
    from .delivery_repair import _canonical, _write_immutable, repair_delivery
    from .goal_terminal import GoalSealedError, goal_sealed, science_commit_barrier
    from .submission_rework import correct_submission_delivery
    spec, graph = workflow.spec, workflow._graph_database_path()
    with science_commit_barrier(spec.research_root, "exclusive"):
        if goal_sealed(spec.research_root, graph, spec=spec) is not None:
            raise GoalSealedError("Sealed Goal cannot accept an author correction")
        request, selected, state, assessment, requirement = _load_request(workflow, request_path, cycle)
        digest = assessment_authority_digest(spec, graph, selected, assessment)
        if digest != assessment.get("science_digest"):
            raise ValueError("Real scientific authority changed after the original Goal assessment")
        marker = {"request": request, "request_sha256": hashlib.sha256(_canonical(request)).hexdigest(),
                  "science_digest": digest, "required_material": requirement}
        _write_immutable(_request_path(selected), marker)
        workflow._bind_cycle(selected, create=False)
        workflow._restore_resumed_cycle_routes(state)
        state.update(status="over", phase="complete")
        snapshot, access = _snapshot(workflow, state)
    supplement = selected / "operator_delivery" / "supplement.json"
    if not supplement.exists():
        instructions = (request["instructions"] + "\nPreserve all original scientific files and their exact "
            "method/parameter identities. Author only supplemental delivery files and technical interface checks; "
            "no new research, scientific values, parameter selection, Hypothesis or verdict. Report unresolved "
            "requirements honestly. SDK captures supplemental execution provenance without adding scientific Evidence.")
        state["goal_submission_gap"] = {"authority": AUTHORITY,
            "delivery_revision": access["delivery_revision"], "validation": {
                "code": "explicit_operator_public_interface_gap", "contract_sha256": marker["request_sha256"],
                "assessment_id": assessment["assessment_id"], "required_material_ref": request["required_material_ref"],
                "instructions": instructions}}
        workflow._emit(state, "delivery.author_correction_requested", {"request_sha256": marker["request_sha256"],
            "source_assessment_id": assessment["assessment_id"], "authority": AUTHORITY})
        correct_submission_delivery(workflow, state, snapshot)
        with science_commit_barrier(spec.research_root, "exclusive"):
            if assessment_authority_digest(spec, graph, selected, assessment) != digest:
                raise ValueError("Author correction changed scientific authority")
            from .delivery_materials import bind_invocation_materials, execution_materials
            manifest_path = Path(state.get("last_invocation_artifact_manifest") or "")
            manifest = _read(manifest_path)
            producer = {key: manifest[key] for key in ("invocation_id", "role", "stage")}
            binding = bind_invocation_materials(manifest_path, cycle_dir=selected,
                workspace=selected / "workspace", producer_invocation=producer)
            materials = execution_materials(binding, producer, cycle_dir=selected)
            if not materials.get("assets") or materials.get("integrity", {}).get("status") != "verified":
                raise ValueError("Original author did not deliver an intact supplemental artifact")
            _write_immutable(supplement, {"request_sha256": marker["request_sha256"],
                "binding": binding, "producer_invocation": producer})
            state.pop("goal_submission_gap", None)
            workflow._persist(state)
    result = repair_delivery(workflow, cycle=selected.name, reassess=reassess)
    return {**result, "author_corrected": True, "operator_request_sha256": marker["request_sha256"],
            "supplement_receipt": str(supplement)}
