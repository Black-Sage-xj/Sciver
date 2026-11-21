"""Resume a frozen Investigator attempt after disproving a missing-file report."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .invocations import stable_hash
from .native_goal import (
    FROZEN_CONTEXT_SHA_MARKER,
    NativeGoalFixtureAvailabilityConflict,
    NativeGoalResult,
    execution_limit_fixture_inputs,
    execution_limit_setup_binding,
    read_execution_limit_handoff,
    verify_native_goal_context_reference,
)
from .public_submission import read_bounded_regular_file
from .scientific_objective import frozen_science_digest, serialize_frozen_science

VALIDATOR = "native-goal-fixture-conflict-reentry/v1"
FAILURE_KIND = "native_goal_fixture_availability_conflict"
_MAX_LOG_BYTES = 16 * 1024 * 1024


def is_fixture_conflict(state: dict) -> bool:
    return (state.get("native_goal") or {}).get("runtime_failure_kind") == FAILURE_KIND


def _thread(cycle: Path, state: dict) -> tuple[dict, dict]:
    native = state.get("native_goal") or {}
    conflict = native.get("fixture_availability_conflict") or {}
    recovery = state.get("recovery") or {}
    last = state.get("last_invocation_failure") or {}
    invocation = conflict.get("invocation_id")
    thread = native.get("thread_id")
    attempt = state.get("investigation_attempt")
    lock = state.get("hypothesis_lock") or {}
    control = cycle / "recovery_control.json"
    if (not is_fixture_conflict(state) or state.get("status") != "failed"
            or state.get("protocol_health") != "valid" or state.get("scientific_verdict") is not None
            or str(state.get("cycle_id")) != cycle.name or not state.get("last_safe_checkpoint")
            or lock.get("status") != "locked" or type(attempt) is not int or attempt < 1
            or not isinstance(thread, str) or not thread or (state.get("threads") or {}).get("investigator") != thread
            or native.get("status") not in {"blocked", "complete"} or bool(native.get("origin_binding"))
            or recovery.get("kind") != "NativeGoalRuntimeFailure" or recovery.get("action") != "error"
            or not isinstance(invocation, str) or not re.fullmatch(r"inv-[A-Za-z0-9_-]+", invocation)
            or invocation != state.get("last_invocation_id")
            or last != {"invocation_id": invocation, "role": "investigator", "stage": "investigate",
                        "failure_phase": "invoke", "error_type": "NativeGoalRuntimeFailure"}
            or any(state.get(key) for key in (
                "abandoned_cycle", "execution_active", "active_invocation_id", "execution_limit_handoff",
                "goal_completion", "goal_submission_gap", "operator_guidance_pending",
            )) or (state.get("role_outputs") or {}).get("investigator")
            or control.exists() or control.is_symlink()):
        raise ValueError("no untouched terminal fixture-availability conflict")
    return native, conflict


def _log_rows(path: Path) -> list[dict]:
    rows = [json.loads(row) for row in read_bounded_regular_file(path, _MAX_LOG_BYTES).splitlines() if row.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("the fixture-availability audit log contains a non-object")
    return rows


def _audit(cycle: Path, state: dict) -> tuple[dict, dict]:
    from .goal_terminal import GoalSealedError, assert_goal_open
    from .investigator_budget import budget_status
    from .research_inputs import registered_fixture_inputs
    from .specification import TaskSpec
    from .worker_store import _graph_path

    native, conflict = _thread(cycle, state)
    budget = budget_status(cycle, cycle.parent, state)
    if budget.get("exhausted") or budget.get("rejected"):
        raise ValueError("the original Investigator budget cannot authorize a retry")
    invocation, thread = conflict["invocation_id"], native["thread_id"]
    snapshots = {}
    for row in _log_rows(cycle / "invocations.jsonl"):
        if row.get("invocation_id"):
            snapshots.setdefault(row["invocation_id"], {}).update(row)
    failed = snapshots.get(invocation, {})
    expected = {"role": "investigator", "stage": "investigate", "state": "failed",
                "commit_state": "not_committed", "error_type": "NativeGoalRuntimeFailure",
                "failure_phase": "invoke", "thread_id": thread}
    identity = failed.get("native_goal_identity") or {}
    if (any(failed.get(key) != value for key, value in expected.items())
            or not failed.get("logical_key") or identity.get("thread_id") != thread
            or identity.get("status") not in {"active", "paused"}
            or failed.get("native_goal_origin") or failed.get("closure_hash")
            or any(row.get("logical_key") == failed["logical_key"]
                   and row.get("invocation_id") != invocation
                   and row.get("state") in {"running", "interrupted", "superseded"}
                   and row.get("commit_state") == "not_committed" for row in snapshots.values())):
        raise ValueError("the original failed Investigator invocation is not isolated")

    rows = [row for row in _log_rows(cycle / "runtime_events.jsonl")
            if row.get("event") == "AGENT_INPUT" and row.get("invocation_id") == invocation
            and row.get("thread_id") == thread and row.get("stage") == failed["stage"]
            and row.get("role") == "investigator"]
    setups = [row["content"] for row in rows if row.get("input_kind") == "native_goal_setup_turn"]
    objectives = [row["content"] for row in rows if row.get("input_kind") == "native_goal_objective"]
    if (len(setups) != 1 or len(objectives) != 1
            or not all(isinstance(value, str) and value.strip() for value in (setups[0], objectives[0]))):
        raise ValueError("the failed invocation has no unique native Goal Setup and objective")
    setup = setups[0].partition("\n")[2]
    objective = objectives[0]
    verify_native_goal_context_reference(objective, setup)
    if hashlib.sha256(objective.strip().encode()).hexdigest() != identity.get("objective_sha256"):
        raise ValueError("failed Goal lost its original objective identity")

    handoff = cycle / "workspace" / ".tm-handoff" / (invocation + ".json")
    binding = execution_limit_setup_binding(setup, handoff)
    science = state["hypothesis_lock"]
    digest = frozen_science_digest(science)
    if (not binding or binding["invocation_id"] != invocation or binding["frozen_objective_sha256"] != digest
            or (FROZEN_CONTEXT_SHA_MARKER not in objective and serialize_frozen_science(science) not in objective)):
        raise ValueError("the failed handoff does not match the frozen hypothesis")
    pins = execution_limit_fixture_inputs(setup)
    spec = TaskSpec.from_task_root(cycle.parent)
    try:
        assert_goal_open(cycle.parent, _graph_path(spec), spec=spec)
    except GoalSealedError as exc:
        raise ValueError("the task Goal already completed") from exc
    fields = ("reference", "path", "size", "sha256")
    current = {row["reference"]: {key: row[key] for key in fields}
               for row in registered_fixture_inputs(spec)}
    original = {row["reference"]: {key: row[key] for key in fields} for row in pins or []}
    if not pins or current != original:
        raise ValueError("the task's registered fixture declarations changed")
    try:
        read_execution_limit_handoff(
            NativeGoalResult(native["status"], thread, 0, 0), handoff,
            invocation_id=invocation, frozen_objective_sha256=digest,
            workspace_path=cycle / "workspace", fixture_inputs=pins,
        )
    except NativeGoalFixtureAvailabilityConflict as error:
        if (error.references != conflict.get("references")
                or error.handoff_sha256 != conflict.get("handoff_sha256")):
            raise ValueError("the original contradictory report or pinned files changed") from error
    else:
        raise ValueError("the recorded fixture availability contradiction no longer reproduces")

    return {
        "operation": stable_hash([cycle.name, state.get("worker_id"), state["investigation_attempt"], digest]),
        "invocation_id": invocation, "thread_id": thread, "status": native["status"],
        "frozen_objective_sha256": digest, "setup_sha256": stable_hash(setup),
        "budget": {key: budget[key] for key in ("allowance_tokens", "observed_tokens")},
        "handoff_sha256": conflict["handoff_sha256"],
        "available_fixture_pins": [current[reference] for reference in conflict["references"]],
    }, identity


def _verify_terminal_goal(cycle: Path, proof: dict, identity: dict) -> dict:
    from .native_goal_recovery import verify_existing_goal
    from .supervisor_native_goal_recovery import read_existing_goal

    goal = read_existing_goal(cycle, identity, require_idle=True)
    terminal = verify_existing_goal(goal, identity, previous=identity)
    if terminal["status"] != proof["status"]:
        raise ValueError("the original native Goal is no longer terminal")
    return terminal


def validate_fixture_conflict(cycle: Path, state: dict, *, engine: Path) -> dict | None:
    if not is_fixture_conflict(state):
        return None
    from .runtime_supervisor_review import resume_blocker
    from .supervisor_repair_receipts import _digest, _record_validation, source_manifest

    if resume_blocker(state, for_repair=True):
        return None
    manifest = source_manifest(engine)
    proof, identity = _audit(cycle, state)
    if proof["operation"] in (state.get("supervisor_fixture_conflict_operations") or []):
        return None
    proof["terminal_goal"] = _verify_terminal_goal(cycle, proof, identity)
    if source_manifest(engine) != manifest:
        return None
    return _record_validation(cycle, state, engine, validator=VALIDATOR,
        module="supervisor_fixture_conflict_recovery.py", change_id="existing-source:" + _digest(manifest),
        tests=json.dumps(proof, sort_keys=True), manifest=manifest, disposition="already_fixed")


def recheck_receipt_inputs(receipt: dict, state: dict) -> None:
    proof, _ = _audit(Path(receipt["cycle"]), state)
    recorded = json.loads(receipt["tests"])
    if proof != {key: value for key, value in recorded.items() if key != "terminal_goal"}:
        raise ValueError("authenticated fixture availability evidence changed")


def consume_fixture_conflict(cycle: Path, state: dict) -> None:
    """Issue one fresh Native Goal on the same guarded frozen attempt."""
    if not is_fixture_conflict(state):
        return
    from .runtime import now
    from .supervisor_repair_receipts import inspect_repair_receipt, repair_credit_key

    receipt = inspect_repair_receipt(cycle, state)
    if (receipt["validator"] != VALIDATOR
            or repair_credit_key(state) not in (state.get("supervisor_consumed_repairs") or [])):
        raise ValueError("fixture conflict requires a verified and authorized Supervisor retry")
    recorded = json.loads(receipt["tests"])
    proof, identity = _audit(cycle, state)
    spent = state.setdefault("supervisor_fixture_conflict_operations", [])
    if proof["operation"] in spent or _verify_terminal_goal(cycle, proof, identity) != recorded["terminal_goal"]:
        raise ValueError("the original terminal Goal or one-use recovery authority changed")
    spent.append(proof["operation"])
    state["supervisor_fixture_conflict_continuation"] = {
        **recorded, "at": now(), "receipt_sha256": state["supervisor_repair_receipt"]["sha256"],
        "original_native_goal": copy.deepcopy(state["native_goal"]),
    }
    state["threads"].pop("investigator")
    (state.get("runtime_thread_providers") or {}).pop("investigator", None)
