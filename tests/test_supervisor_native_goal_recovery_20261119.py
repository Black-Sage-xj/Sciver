"""Original-Goal reentry proof: synthetic audits and GET-only fake providers."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import supervisor_native_goal_recovery as recovery
from theory_master import supervisor_repair_receipts as receipts
from theory_master.contract_stages import role_contract
from theory_master.native_goal import native_goal_setup_turn
from theory_master.native_goal_recovery import (
    verify_existing_goal,
    NativeGoalRecoveryConflict,
)
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import (
    review_runtime_failure,
    resume_blocker,
)
from theory_master.scientific_objective import serialize_frozen_science


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    engine = Path(receipts.__file__).resolve().parent
    cycle = tmp_path / "research/9"
    for path in (
        cycle / "checkpoints",
        cycle / "workspace/.tm-handoff",
        cycle.parent / ".theory-master",
    ):
        path.mkdir(parents=True)
    original, failed, thread = (
        "inv-9-investigator-original",
        "inv-9-investigator-failed",
        "original-thread",
    )
    start, resumed, finish = (
        "2026-11-18T20:00:00+08:00",
        "2026-11-19T09:58:24+08:00",
        "2026-11-19T09:58:28+08:00",
    )
    lock = {
        "id": "H-9",
        "status": "locked",
        "hypothesis": "Unchanged hypothesis",
        "scope": "Original scope",
        "decisive_test": "Original decisive test",
    }
    checkpoint = cycle / "checkpoints/000020-hypothesis-lock.json"
    write_json(checkpoint, {"cycle_id": "9", "hypothesis_lock": lock})
    message = recovery.ERROR_PREFIX + thread
    state = {
        "cycle_id": "9",
        "status": "failed",
        "phase": "failed",
        "resumable": True,
        "protocol_health": "valid",
        "scientific_verdict": None,
        "execution_active": False,
        "active_invocation_id": None,
        "last_invocation_id": failed,
        "hypothesis_lock": lock,
        "last_safe_checkpoint": str(checkpoint),
        "threads": {"investigator": thread},
        "investigator_budget": {
            "allowance_tokens": 1000,
            "observed_tokens": 120,
            "remaining_tokens": 880,
            "exhausted": False,
            "rejected": False,
        },
        "recovery": {
            "action": "error",
            "kind": "InvalidRequestError",
            "category": "runtime",
            "message": message,
            "at": finish,
        },
        "last_invocation_failure": {
            "invocation_id": failed,
            "role": "investigator",
            "stage": "investigate",
            "failure_phase": "invoke",
            "error_type": "InvalidRequestError",
        },
    }
    contract = role_contract("investigator", phase="investigate").descriptor()
    prepared = {
        "state": "prepared",
        "role": "investigator",
        "stage": "investigate",
        "logical_key": "same-frozen-slot",
        "contract_ref": contract,
        "commit_state": "not_committed",
    }
    write_rows(
        cycle / "invocations.jsonl",
        [
            {**prepared, "invocation_id": original},
            {"invocation_id": original, "state": "running", "thread_id": thread},
            {
                "invocation_id": original,
                "state": "interrupted",
                "successor_invocation_id": failed,
            },
            {
                **prepared,
                "invocation_id": failed,
                "predecessor_invocation_id": original,
            },
            {"invocation_id": failed, "state": "running", "thread_id": thread},
            {
                "invocation_id": failed,
                "state": "failed",
                "failure_phase": "invoke",
                "error_type": "InvalidRequestError",
                "error": message,
            },
        ],
    )
    write_rows(
        cycle / "events.jsonl",
        [
            {
                "type": "role.started",
                "at": start,
                "payload": {
                    "invocation_id": original,
                    "role": "investigator",
                    "stage": "investigate",
                    "thread_id": thread,
                },
            },
            {
                "type": "role.started",
                "at": resumed,
                "payload": {
                    "invocation_id": failed,
                    "role": "investigator",
                    "stage": "investigate",
                    "thread_id": thread,
                },
            },
        ],
    )
    handoff = cycle / "workspace/.tm-handoff" / f"{original}.json"
    context = json.dumps({"delivery": str(handoff)})
    objective = "Finish the original science: " + serialize_frozen_science(lock)
    write_rows(
        cycle / "runtime_events.jsonl",
        [
            {
                "event": "AGENT_INPUT",
                "ts": start,
                "role": "investigator",
                "stage": "investigate",
                "input_kind": "native_goal_setup_turn",
                "content": native_goal_setup_turn(context, objective),
            },
            {
                "event": "AGENT_INPUT",
                "ts": start,
                "role": "investigator",
                "stage": "investigate",
                "input_kind": "native_goal_objective",
                "content": objective,
            },
            {
                "event": "RUN_FAILED",
                "ts": finish,
                "error": message,
                "error_kind": "InvalidRequestError",
                "failure_category": "runtime",
                "resumable": True,
            },
        ],
    )
    write_json(cycle / "research_state.json", state)
    goal = SimpleNamespace(
        thread_id=thread,
        objective=objective,
        status="active",
        created_at=int(datetime.fromisoformat(start).timestamp()) + 5,
        tokens_used=150,
        time_used_seconds=300,
        token_budget=None,
    )
    monkeypatch.setattr(
        recovery, "read_existing_goal", lambda cycle, origin: copy.deepcopy(goal)
    )
    return engine, cycle, state, goal


def validate(stopped):
    engine, cycle, state, _ = stopped
    return recovery.validate_existing_native_goal_reattachment(
        cycle, state, engine=engine
    )


def edit(path, operation):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    operation(rows)
    write_rows(path, rows)


def test_receipt_preserves_original_goal_science_and_unfinished_delivery(stopped):
    _, cycle, state, goal = stopped
    before = copy.deepcopy(state)
    files = {path: path.read_bytes() for path in cycle.rglob("*") if path.is_file()}
    record = validate(stopped)
    assert record is not None
    assert record["validator"] == recovery.VALIDATOR
    assert (
        record["runtime_verification"] == "pending"
        and record["resume_performed"] is False
    )
    evidence = json.loads(record["tests"])
    assert evidence["origin"]["invocation_id"] == "inv-9-investigator-original"
    assert evidence["goal_identity"]["created_at"] == goal.created_at
    assert (
        evidence["resume_preparation"]
        == "monitor_original_native_goal_without_setup_or_start"
    )
    assert set(evidence["evidence_paths"]) == set(record["extra_inputs"])
    assert receipts.inspect_repair_receipt(cycle, state) == record
    assert {
        key: value for key, value in state.items() if not key.startswith("supervisor_")
    } == before
    assert all(path.read_bytes() == value for path, value in files.items())
    assert not Path(evidence["origin"]["handoff_path"]).exists()
    assert resume_blocker(before).startswith("An existing native Goal")
    assert (
        resume_blocker(
            state, runtime_revalidated=True, contract_projection_repair=record
        )
        is None
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "different_error",
        "foreign_thread",
        "other_failure_phase",
        "committed",
        "newer_invocation",
        "foreign_contract",
        "no_predecessor",
        "duplicate_origin",
        "foreign_setup",
        "duplicate_setup",
        "changed_objective",
        "changed_frozen_science",
        "foreign_goal",
        "recreated_goal",
        "terminal_goal",
        "paused_goal",
        "over_budget",
        "native_budget",
        "budget_guard",
        "provider_failure",
        "projection_time",
        "new_goal_window",
        "projection_message",
        "pause",
        "pending_pause",
        "abandoned",
        "protocol",
        "verdict",
        "worker",
        "active",
        "non_resumable",
        "no_checkpoint",
        "symlink_audit",
        "oversize_audit",
        "used_reentry",
    ],
)
def test_proof_rejects_other_faults_or_identity_guard_changes(stopped, mutation):
    _, cycle, state, goal = stopped
    if mutation == "different_error":
        state["recovery"]["message"] += " unrelated"
    elif mutation == "foreign_thread":
        state["threads"]["investigator"] = "foreign"
    elif mutation == "other_failure_phase":
        state["last_invocation_failure"]["failure_phase"] = "semantic_closure"
    elif mutation in {
        "committed",
        "newer_invocation",
        "foreign_contract",
        "no_predecessor",
        "duplicate_origin",
    }:

        def mutate(rows):
            if mutation == "committed":
                rows[-1].update(state="committed", commit_state="committed")
            elif mutation == "newer_invocation":
                rows.append({"invocation_id": "inv-newer", "state": "prepared"})
            elif mutation == "foreign_contract":
                rows[3]["contract_ref"] = {"foreign": True}
            elif mutation == "no_predecessor":
                rows[3].pop("predecessor_invocation_id")
            else:
                rows.insert(
                    0,
                    {
                        **rows[0],
                        "invocation_id": "inv-9-investigator-ambiguous",
                        "thread_id": goal.thread_id,
                        "state": "interrupted",
                    },
                )

        edit(cycle / "invocations.jsonl", mutate)
    elif mutation in {
        "foreign_setup",
        "duplicate_setup",
        "changed_objective",
        "provider_failure",
        "projection_time",
        "projection_message",
    }:

        def mutate(rows):
            if mutation == "foreign_setup":
                rows[0]["invocation_id"] = "inv-foreign"
            elif mutation == "duplicate_setup":
                rows.insert(1, copy.deepcopy(rows[0]))
            elif mutation == "changed_objective":
                rows[1]["content"] = "Different frozen science"
            elif mutation == "provider_failure":
                rows.insert(
                    -1,
                    {
                        "ts": rows[-1]["ts"],
                        "event": "LLM_TURN_FAILED",
                        "error": "provider failed",
                    },
                )
            elif mutation == "projection_time":
                rows[-1]["ts"] = rows[0]["ts"]
            else:
                rows[-1]["error"] = "Other fault"

        edit(cycle / "runtime_events.jsonl", mutate)
    elif mutation == "changed_frozen_science":
        state["hypothesis_lock"]["hypothesis"] = "Changed"
    elif mutation == "foreign_goal":
        goal.objective = "Foreign Goal"
    elif mutation == "recreated_goal":
        goal.created_at = "not a timestamp"
    elif mutation == "new_goal_window":
        goal.created_at = int(
            datetime.fromisoformat(state["recovery"]["at"]).timestamp()
        )
    elif mutation == "terminal_goal":
        goal.status = "complete"
    elif mutation == "paused_goal":
        goal.status = "paused"
    elif mutation == "over_budget":
        goal.tokens_used = 1000
    elif mutation == "native_budget":
        goal.token_budget = 100
    elif mutation == "budget_guard":
        state["investigator_budget"]["exhausted"] = True
    elif mutation == "pause":
        state["recovery"]["action"] = "pause"
    elif mutation == "pending_pause":
        write_json(cycle / "recovery_control.json", {"action": "pause"})
    elif mutation == "abandoned":
        state["abandoned_cycle"] = True
    elif mutation == "protocol":
        state["protocol_health"] = "violation"
    elif mutation == "verdict":
        state["scientific_verdict"] = "supported"
    elif mutation == "worker":
        state["worker_id"] = "worker-1"
    elif mutation == "active":
        state["execution_active"] = True
    elif mutation == "non_resumable":
        state["resumable"] = False
    elif mutation == "no_checkpoint":
        state["last_safe_checkpoint"] = None
    elif mutation == "used_reentry":
        state["supervisor_native_goal_reentries"] = [recovery.reentry_key(state)]
    elif mutation == "symlink_audit":
        path = cycle / "events.jsonl"
        backup = cycle / "backup.jsonl"
        path.rename(backup)
        path.symlink_to(backup)
    elif mutation == "oversize_audit":
        (cycle / "events.jsonl").write_bytes(b" " * (16 * 1024 * 1024 + 1))
    write_json(cycle / "research_state.json", state)
    assert validate(stopped) is None


def test_recheck_accepts_accumulating_usage_but_rejects_recreated_same_objective(
    stopped,
):
    _, cycle, state, goal = stopped
    record = validate(stopped)
    goal.tokens_used += 10
    goal.time_used_seconds += 5
    assert receipts.inspect_repair_receipt(cycle, state) == record
    goal.created_at += 1
    with pytest.raises(NativeGoalRecoveryConflict, match="identity.*reset"):
        receipts.inspect_repair_receipt(cycle, state)


def test_canonical_consumption_is_once_and_carries_identity_to_attach(stopped):
    _, cycle, state, goal = stopped
    record = validate(stopped)
    write_json(cycle / "research_state.json", state)
    result = review_runtime_failure(
        backend=None,
        research_root=cycle.parent,
        cycle_dir=cycle,
        state=state,
        state_path=cycle / "research_state.json",
        incident_id="synthetic",
        scheduler_locked=True,
        existing_receipt_only=True,
    )
    assert result["action"] == "resume"
    assert state["supervisor_native_goal_reentries"] == [recovery.reentry_key(state)]
    assert state["supervisor_consumed_repairs"] == [receipts.repair_credit_key(state)]
    evidence = json.loads(record["tests"])
    expected = recovery.receipt_goal_identity(state, cycle, evidence["origin"])
    assert expected == evidence["goal_identity"]
    goal.created_at += 1
    with pytest.raises(NativeGoalRecoveryConflict):
        verify_existing_goal(goal, evidence["origin"], previous=expected)
    assert resume_blocker(
        state, runtime_revalidated=True, contract_projection_repair=record
    ).startswith("The original native Goal")


def test_unconsumed_receipt_cannot_supply_attach_identity(stopped):
    _, cycle, state, _ = stopped
    record = validate(stopped)
    with pytest.raises(ValueError, match="not consumed"):
        recovery.receipt_goal_identity(
            state, cycle, json.loads(record["tests"])["origin"]
        )


def test_goal_created_at_milliseconds_is_bound_to_original_start_window(stopped):
    _, _, _, goal = stopped
    goal.created_at *= 1000
    assert validate(stopped) is not None


def test_second_failed_monitor_without_active_predecessor_still_binds_unique_original(
    stopped,
):
    _, cycle, state, _ = stopped
    first = state["last_invocation_id"]
    second = "inv-9-investigator-second-failed"

    def ledger(rows):
        rows.extend(
            [
                {**rows[3], "invocation_id": second, "predecessor_invocation_id": None},
                {**rows[4], "invocation_id": second},
                {**rows[5], "invocation_id": second},
            ]
        )

    edit(cycle / "invocations.jsonl", ledger)
    edit(
        cycle / "events.jsonl",
        lambda rows: rows.append(
            {
                "type": "role.started",
                "at": "2026-11-19T10:02:00+08:00",
                "payload": {
                    **rows[-1]["payload"],
                    "invocation_id": second,
                },
            }
        ),
    )
    state["last_invocation_id"] = second
    state["last_invocation_failure"]["invocation_id"] = second
    state["recovery"]["at"] = "2026-11-19T10:02:04+08:00"
    edit(
        cycle / "runtime_events.jsonl",
        lambda rows: rows.append(
            {
                **rows[-1],
                "ts": state["recovery"]["at"],
            }
        ),
    )
    write_json(cycle / "research_state.json", state)
    record = validate(stopped)
    assert record is not None
    assert json.loads(record["tests"])["origin"]["invocation_id"] != first
    assert json.loads(record["tests"])["goal_created_window"][1] == int(
        datetime.fromisoformat("2026-11-19T09:58:24+08:00").timestamp()
    )


def test_real_workflow_resume_keeps_consumed_identity_without_error_message(
    stopped, monkeypatch
):
    from theory_master.backends import FakeBackend
    from theory_master.invocations import InvocationLedger
    from theory_master.native_goal_recovery import find_native_goal_origin
    from theory_master.specification import TaskSpec
    from theory_master.workflow import Workflow
    from theory_master.science_graph_store import ScienceGraphStore

    _, cycle, state, goal = stopped
    monkeypatch.setenv("THEORY_MASTER_SUPERVISOR", "0")
    monkeypatch.setenv(
        "THEORY_MASTER_DATA_ROOT", str(cycle.parent / "isolated-registry")
    )
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\noriginal\n")
    plan = cycle.parent / "plan.md"
    plan.write_text("# Goal\n\nOriginal science.\n")
    spec = TaskSpec(
        plan,
        "original",
        1,
        1,
        1,
        cycle.parent,
        cycle.parent / "graph.canvas",
        None,
        None,
        None,
        "Original science",
        "Contract",
        "Final Gate",
        "Inputs",
        task_mode=True,
        master_id=13,
        graph_database=cycle.parent / "science.sqlite3",
    )
    workflow = Workflow(spec, FakeBackend())
    with ScienceGraphStore(spec.graph_database):
        pass
    record = validate(stopped)
    assert record is not None
    result = review_runtime_failure(
        backend=None,
        research_root=cycle.parent,
        cycle_dir=cycle,
        state=state,
        state_path=cycle / "research_state.json",
        incident_id="synthetic",
        scheduler_locked=True,
        existing_receipt_only=True,
    )
    assert result["action"] == "resume"
    observed = []

    def run_cycle(resumed_state):
        assert resumed_state["recovery"]["action"] == "resume"
        resumed_state["recovery"].pop("message", None)
        session = SimpleNamespace(
            state=resumed_state,
            owner=workflow,
            invocation_ledger=InvocationLedger(cycle / "invocations.jsonl"),
            invocation_id="inv-9-investigator-new-monitor",
            stage="investigate",
            logical_key="same-frozen-slot",
            identifier=goal.thread_id,
            contract=role_contract("investigator", phase="investigate"),
            handoff_path=cycle
            / "workspace/.tm-handoff/inv-9-investigator-new-monitor.json",
        )
        origin = find_native_goal_origin(session)
        assert origin["invocation_id"] == "inv-9-investigator-original"
        assert origin["persistent_goal"] == json.loads(record["tests"])["goal_identity"]
        observed.append(origin)
        return {**resumed_state, "status": "needs_attention", "execution_active": False}

    monkeypatch.setattr(workflow, "_run_cycle", run_cycle)
    workflow.resume(13)
    assert len(observed) == 1


def test_read_probe_uses_get_only_without_resume_or_goal_mutation(stopped, monkeypatch):
    import openai_codex
    from theory_master import codex_accounts, codex_sdk_compat

    _, cycle, state, goal = stopped
    record = validate(stopped)
    origin = json.loads(record["tests"])["origin"]
    home = cycle.parent / "original-runtime"
    home.mkdir()
    (home / "config.toml").write_text("# existing runtime")
    calls = []

    class Client:
        def __init__(self, config):
            calls.append(("config", config))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append(("close",))

    # Call the real implementation, bypassing only this fixture's fake getter.
    monkeypatch.undo()
    with (
        patch.object(codex_accounts, "runtime_home", return_value=home),
        patch.object(openai_codex, "Codex", Client),
        patch.object(
            codex_sdk_compat,
            "read_goal",
            side_effect=lambda client, thread: calls.append(("GET", thread)) or goal,
        ),
    ):
        assert recovery.read_existing_goal(cycle, origin) is goal
    assert [row[0] for row in calls] == ["config", "GET", "close"]
