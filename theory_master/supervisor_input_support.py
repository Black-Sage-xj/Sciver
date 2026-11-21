"""Own a closed input-limited Cycle until an authorized input becomes available."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SCHEMA = "theory-master/input-support-baseline/v1"
_INPUT_IDS = "verified_research_input_ids"


def _identity(state: dict) -> dict:
    report = state["operational_only_closure"]["execution_limit"]
    identity = {"schema": SCHEMA, "cycle_id": str(state["cycle_id"]), "invocation_id": report["invocation_id"],
                "frozen_objective_sha256": report["frozen_objective_sha256"]}
    if state.get("worker_id"):
        identity["worker_id"] = state["worker_id"]
    return identity


def _available_input_ids(spec: Any) -> list[str]:
    from .research_inputs import verified_research_inputs

    return sorted({item["sha256"] for item in verified_research_inputs(spec)
                   if item["kind"] in {"fixture_input", "supplemental_input"}})


def _verify_baseline(record: Any, identity: dict) -> set[str] | None:
    from .worker_direction import _verified_input_ids

    if not isinstance(record, dict) or any(record.get(key) != value for key, value in identity.items()):
        return None
    ids = _verified_input_ids(record)
    return set(ids) if ids is not None else None


def capture_input_support_baseline(spec: Any, state: dict) -> None:
    """Keep the available inputs at closure in the new Cycle's original checkpoint."""
    if (getattr(spec, "task_mode", False)
            and (state.get("operational_only_closure") or {}).get("kind") == "input_support_unavailable"):
        try:
            available = set(_available_input_ids(spec))
            limit = state["operational_only_closure"]["execution_limit"]
            audit = limit.get("input_support_audit") or {}
            if audit.get("mode") == "missing_fixture_files":
                # The SDK validated absence before ending the native Goal. A
                # file restored during archive publication is a new input.
                pins = (state.get("execution_limit_handoff") or {}).get("fixture_audit_pins") or []
                available.difference_update(row["sha256"] for row in pins)
            state["input_support_baseline"] = {**_identity(state), _INPUT_IDS: sorted(available)}
        except (OSError, ValueError):
            # An unreadable inventory provides no baseline; a later owner can
            # establish one without granting immediate permission to relaunch.
            pass


def _legacy_input_baseline(cycle: Path, identity: dict, current: list[str], *, establish: bool) -> set[str] | None:
    from .invocations import stable_hash
    from .public_submission import read_bounded_regular_file, strict_json
    from .runtime import write_json

    base = cycle.parent / ".theory-master" / "research_input_wait_baselines"
    path = base / (stable_hash(identity) + ".json")
    if any(part.is_symlink() for part in (path, base, base.parent)):
        return None
    if path.exists():
        return _verify_baseline(strict_json(read_bounded_regular_file(path, 64_000)), identity)
    if establish:
        write_json(path, {**identity, _INPUT_IDS: current})
    return None


def input_support_recovery(cycle: Path, state: dict, *, establish_baseline: bool = False,
                           spec: Any = None, worker_id: str | None = None) -> dict | None:
    """Require a verified archive and positive input addition, independent of Plan edits."""
    from .invocations import stable_hash
    from .public_submission import read_bounded_regular_file, strict_json
    from .specification import TaskSpec
    from .task_progression import local_replan_reason

    control = cycle / "recovery_control.json"
    if (control.exists() or control.is_symlink() or (state.get("worker_id") or None) != worker_id
            or str(state.get("cycle_id") or "") != cycle.name
            or local_replan_reason(state) != "input_support_unavailable"):
        return None
    closure = state["operational_only_closure"]
    try:
        archived = strict_json(read_bounded_regular_file(cycle / "operational_closure.json", 1_048_576))
    except (OSError, ValueError):
        return None
    if archived != closure:
        return None

    report = closure["execution_limit"]
    identity = _identity(state)
    waiting = {
        "route": "input_support_wait",
        "reason": str(report.get("reason") or closure.get("reason") or "The prior Cycle lacked required input.")[:2000],
        "wake_condition": str(report.get("required_dependency") or
                              "A newly verified task-authorized input that addresses the recorded limitation.")[:2000],
        "invocation_id": identity["invocation_id"],
        "dependency_identity": stable_hash(identity),
    }
    try:
        if spec is None:
            spec = TaskSpec.from_task_root(cycle.parent)
        elif Path(spec.research_root).resolve() != cycle.parent.resolve():
            return None
        if not getattr(spec, "task_mode", False):
            return None
        current = _available_input_ids(spec)
        if "input_support_baseline" in state:
            previous = _verify_baseline(state["input_support_baseline"], identity)
        else:
            previous = _legacy_input_baseline(cycle, identity, current, establish=establish_baseline)
    except (OSError, ValueError):
        return waiting
    additions = sorted(set(current) - previous) if previous is not None else []
    if additions:
        return {**waiting, "route": "input_support", "added_input_ids": additions,
                "wake_identity": stable_hash([identity, additions])}
    return waiting
