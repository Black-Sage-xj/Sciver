import hashlib
import json
from types import SimpleNamespace

import pytest

from theory_master import retained_goal_quota
from theory_master.boot_native_goal import (
    native_goal_boot_pin,
    pending_boot_native_goal,
    require_boot_native_origin,
)
from theory_master.contract_stages import role_contract
from theory_master.invocations import stable_hash
from theory_master.native_goal import EXECUTION_LIMIT_SCHEMA, FROZEN_CONTEXT_SHA_MARKER, native_goal_setup_turn
from theory_master.native_goal_recovery import NativeGoalRecoveryConflict
from theory_master.scientific_objective import frozen_science_digest, serialize_frozen_science


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def active_goal(tmp_path, monkeypatch):
    cycle = tmp_path / "12"
    workspace = cycle / "workspace"
    directory = workspace / ".tm-handoff"
    directory.mkdir(parents=True)
    invocation_id = "inv-12-investigator-original"
    handoff = directory / f"{invocation_id}.json"
    stage = "investigate"
    thread_id = "01a1f19f-664d-7d51-a281-8cc8720f4719"
    descriptor = role_contract("investigator", phase=stage).descriptor()
    science = {"hypothesis": "The sequence converges", "scope": "Positive integers",
               "decisive_test": "Find its limit"}
    setup = json.dumps({"frozen_objective": science, "sdk_execution_limit_handoff": {
        "schema": EXECUTION_LIMIT_SCHEMA, "invocation_id": invocation_id,
        "frozen_objective_sha256": frozen_science_digest(science),
        "handoff_path": str(handoff), "workspace_path": str(workspace),
    }}) + "\nSDK delivery guidance."
    objective = "Frozen science: " + serialize_frozen_science(science)
    identity = {"thread_id": thread_id, "created_at": 1795055156, "status": "active",
                "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
                "tokens_used": 42, "time_used_seconds": 10, "token_budget": None}
    audit = {"role": "investigator", "stage": stage, "thread_id": thread_id, "invocation_id": invocation_id}
    started = {"type": "role.started", "at": "2026-11-21T10:00:00+08:00", "payload": {
        **audit, "contract_id": descriptor["contract_id"], "contract_version": descriptor["contract_version"],
        "contract_schema_hash": descriptor["schema_hash"],
    }}
    evidence = [{**audit, "ts": "2026-11-21T10:00:01+08:00", "event": "AGENT_INPUT",
                 "input_kind": kind, "content": content}
                for kind, content in (("native_goal_setup_turn", native_goal_setup_turn(setup, objective)),
                                      ("native_goal_objective", objective))]
    write_rows(cycle / "events.jsonl", [started])
    write_rows(cycle / "runtime_events.jsonl", evidence)
    state = {"active_invocation_id": invocation_id, "active_role": "investigator",
             "threads": {"investigator": thread_id}, "hypothesis_lock": {**science, "status": "locked"}}
    row = {**audit, "kind": "agent", "logical_key": stable_hash("frozen workflow slot"),
           "state": "running", "commit_state": "not_committed", "contract_ref": descriptor,
           "native_goal_identity": identity}
    account = {"provider": "codex", "model": "gpt-5.6"}
    captured = []

    def account_binding(bound_cycle, bound_state, origin):
        assert bound_cycle == cycle and bound_state is state
        captured.append(origin)
        return account

    monkeypatch.setattr(retained_goal_quota, "boot_goal_account_binding", account_binding, raising=False)
    return SimpleNamespace(cycle=cycle, handoff=handoff, state=state, row=row,
                           invocations={invocation_id: row}, account=account, captured=captured,
                           started=started, evidence=evidence)


def test_bind_native_goal_before_boot_with_its_existing_regular_handoff(active_goal):
    case = active_goal
    case.handoff.write_text('{"findings":[]}')
    pin = native_goal_boot_pin(case.cycle, case.state, case.invocations)
    origin, = case.captured
    assert pin == {"invocation_id": case.row["invocation_id"], "origin_invocation_id": case.row["invocation_id"],
                   "thread_id": case.row["thread_id"], "stage": case.row["stage"],
                   "logical_key": case.row["logical_key"], "contract_ref": case.row["contract_ref"],
                   "origin_sha256": stable_hash(origin), "account_binding": case.account}
    route = {"role": case.row["role"], "stage": case.row["stage"],
             "logical_key": case.row["logical_key"], "thread_id": case.row["thread_id"], **case.account}
    require_boot_native_origin(pin, origin, **route)
    with pytest.raises(NativeGoalRecoveryConflict):
        require_boot_native_origin(pin, origin, **{**route, "logical_key": stable_hash("another slot")})
    with pytest.raises(NativeGoalRecoveryConflict):
        require_boot_native_origin(pin, {**origin, "objective": "changed"}, **route)
    with pytest.raises(NativeGoalRecoveryConflict):
        require_boot_native_origin(pin, origin, **{**route, "provider": "different"})
    backend = SimpleNamespace(boot_native_goal=pin)
    assert pending_boot_native_goal(backend) == pin
    backend.retained_goal_delivered = True
    assert pending_boot_native_goal(backend) is None
    assert pending_boot_native_goal(SimpleNamespace()) is None


@pytest.mark.parametrize("fault", ["partial_audit", "missing_identity", "other_job", "contract_drift",
                                   "changed_frozen_test", "foreign_start", "already_delivered", "handoff_symlink",
                                   "same_slot_committed"])
def test_boot_rejects_unverified_or_already_delivered_native_progress(active_goal, fault):
    case = active_goal
    if fault == "partial_audit":
        write_rows(case.cycle / "runtime_events.jsonl", case.evidence[:1])
    elif fault == "missing_identity":
        del case.row["native_goal_identity"]
    elif fault == "other_job":
        case.invocations = {"external-job": {"state": "prepared"}, **case.invocations}
    elif fault == "contract_drift":
        case.row["contract_ref"]["schema_hash"] = stable_hash("older schema")
    elif fault == "changed_frozen_test":
        case.state["hypothesis_lock"]["decisive_test"] = "Fit an unrelated model"
    elif fault == "foreign_start":
        case.started["payload"]["stage"] = "judge_rework"
        write_rows(case.cycle / "events.jsonl", [case.started])
    elif fault == "already_delivered":
        journal = case.cycle / "native_deliveries" / f'{case.row["invocation_id"]}.json'
        journal.parent.mkdir()
        journal.write_text("{}")
    elif fault == "handoff_symlink":
        case.handoff.symlink_to(case.cycle / "events.jsonl")
    elif fault == "same_slot_committed":
        prior = "inv-12-investigator-prior"
        case.invocations = {prior: {**case.row, "invocation_id": prior, "state": "committed",
                            "commit_state": "committed"}, **case.invocations}
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations) is None
    assert not case.captured


def test_interrupted_original_can_be_recovered_through_its_active_monitor(active_goal):
    case = active_goal
    original_pin = native_goal_boot_pin(case.cycle, case.state, case.invocations)
    origin, = case.captured
    original_id = original_pin["invocation_id"]
    monitor_id = "inv-12-investigator-monitor"
    case.row["state"] = "interrupted"
    case.invocations[monitor_id] = {**case.row, "invocation_id": monitor_id, "state": "running",
        "native_goal_origin": origin,
        "native_goal_identity": {**case.row["native_goal_identity"], "tokens_used": 55, "time_used_seconds": 12}}
    monitor_started = {**case.started, "at": "2026-11-21T10:05:00+08:00",
                       "payload": {**case.started["payload"], "invocation_id": monitor_id}}
    write_rows(case.cycle / "events.jsonl", [case.started, monitor_started])
    case.state["active_invocation_id"] = monitor_id
    pin = native_goal_boot_pin(case.cycle, case.state, case.invocations)
    assert pin["invocation_id"] == monitor_id and pin["origin_invocation_id"] == original_id
    assert pin["origin_sha256"] == stable_hash(case.captured[-1])
    assert case.captured[-1]["persistent_goal"]["tokens_used"] == 55
    case.invocations[monitor_id]["native_goal_identity"]["created_at"] += 1
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations) is None


def test_digest_reference_requires_the_same_frozen_science_in_setup(active_goal):
    case = active_goal
    setup_header, _, source = case.evidence[0]["content"].partition("\n")
    context, payload_end = json.JSONDecoder().raw_decode(source)
    science = case.state["hypothesis_lock"]
    context["frozen_objective"] = science
    context["sdk_execution_limit_handoff"]["frozen_objective_sha256"] = frozen_science_digest(science)
    objective = f"Original frozen science:\n{FROZEN_CONTEXT_SHA_MARKER}{frozen_science_digest(science)}\n"
    case.row["native_goal_identity"]["objective_sha256"] = hashlib.sha256(objective.strip().encode()).hexdigest()
    case.evidence[0]["content"] = setup_header + "\n" + json.dumps(context) + source[payload_end:]
    case.evidence[1]["content"] = objective
    write_rows(case.cycle / "runtime_events.jsonl", case.evidence)
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations)
    context["frozen_objective"] = {**science, "decisive_test": "Changed Setup test"}
    case.evidence[0]["content"] = setup_header + "\n" + json.dumps(context) + source[payload_end:]
    write_rows(case.cycle / "runtime_events.jsonl", case.evidence)
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations) is None


def test_legacy_audit_requires_a_unique_scoped_setup_and_objective_pair(active_goal):
    case = active_goal
    for item in case.evidence:
        del item["invocation_id"], item["thread_id"]
    write_rows(case.cycle / "runtime_events.jsonl", case.evidence)
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations)
    write_rows(case.cycle / "runtime_events.jsonl", [*case.evidence, case.evidence[1]])
    assert native_goal_boot_pin(case.cycle, case.state, case.invocations) is None
