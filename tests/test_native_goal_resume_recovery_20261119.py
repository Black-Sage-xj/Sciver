"""Deterministic same-Goal attachment; all server traffic is offline SDK fakes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openai_codex._goal import _GoalOperationState
from openai_codex.generated.v2_all import (
    ActiveThreadStatus, IdleThreadStatus, ThreadGoal, ThreadGoalStatus,
    ThreadGoalUpdatedNotification, Turn, TurnStartedNotification,
    TurnCompletedNotification, TurnStatus,
)
from openai_codex.models import Notification

from theory_master.codex_goal_transport import NativeGoalRunner
from theory_master.native_goal import NativeGoalBudgetExceeded, NativeGoalIncomplete
from theory_master.native_goal_recovery import (
    ORIGIN_SCHEMA, NativeGoalRecoveryConflict, attach_existing_goal,
    bind_origin_context, find_native_goal_origin, validate_origin, verify_existing_goal,
    ensure_no_foreign_goal_owner,
    execution_limit_delivery_id,
)
from theory_master.provider_errors import ProviderQuotaExhausted
from theory_master.scientific_objective import frozen_science_digest, serialize_frozen_science
from theory_master.workflow_support import RecoveryStop

SCIENCE = {"hypothesis": "fixture hypothesis", "scope": "fixture input", "decisive_test": "fixture test"}


def origin_fixture(tmp_path):
    workspace = tmp_path / "workspace"
    handoff = workspace / ".tm-handoff" / "inv-1-investigator-original.json"
    handoff.parent.mkdir(parents=True)
    handoff.write_text(json.dumps({"findings": ["The original authored claim."]}))
    objective = "Original frozen science: " + serialize_frozen_science(SCIENCE) + "\n"
    context = json.dumps({"workspace_access": {"write": str(workspace)}}) + "\nWrite " + str(handoff)
    origin = {"schema": ORIGIN_SCHEMA, "invocation_id": "inv-1-investigator-original",
              "thread_id": "original-thread", "logical_key": "original-slot", "stage": "investigate",
              "contract_ref": {"role": "investigator", "version": "v10"},
              "workspace_path": str(workspace), "handoff_path": str(handoff),
              "objective": objective, "objective_sha256": hashlib.sha256(objective.strip().encode()).hexdigest(),
              "setup_context": context, "setup_sha256": hashlib.sha256(context.encode()).hexdigest(),
              "frozen_objective_sha256": frozen_science_digest(SCIENCE)}
    return origin, handoff


class OfflineServer:
    def __init__(self, origin, status="active", active=True):
        self.goal = ThreadGoal(thread_id=origin["thread_id"], objective=origin["objective"].strip(),
            status=ThreadGoalStatus(status), created_at=123, updated_at=456,
            tokens_used=12345, time_used_seconds=678, token_budget=99999)
        self.active = active
        self.calls = []
        self.turn = Turn(id="original-physical-turn", status=TurnStatus.in_progress, items=[])
        self.state = None
        self.events = []
        self.request_error = None
        self.rollout_path = None

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if self.request_error:
            raise self.request_error
        assert method == "thread/goal/get"
        return SimpleNamespace(goal=self.goal)

    def register_goal_operation(self, thread_id):
        self.calls.append(("register", thread_id))
        self.state = _GoalOperationState(thread_id)
        self.state.activate_turn_routing()
        return self.state

    def unregister_goal_operation(self, state):
        self.calls.append(("unregister", state.thread_id))

    def thread_read(self, thread_id, **kwargs):
        self.calls.append(("read", thread_id))
        status = ActiveThreadStatus(type="active", active_flags=[]) if self.active else IdleThreadStatus(type="idle")
        return SimpleNamespace(thread=SimpleNamespace(id=thread_id, turns=[self.turn] if self.active else [],
                                                      status=SimpleNamespace(root=status), path=self.rollout_path))

    def thread_goal_set(self, thread_id, **kwargs):
        self.calls.append(("set", kwargs))
        assert set(kwargs) == {"status"}  # Never objective/budget replacement.
        self.goal = self.goal.model_copy(update={"status": kwargs["status"]})
        self.state.observe(Notification("thread/goal/updated", ThreadGoalUpdatedNotification(
            thread_id=thread_id, goal=self.goal)))
        self.state.observe(Notification("turn/started", TurnStartedNotification(thread_id=thread_id, turn=self.turn)))
        return SimpleNamespace(goal=self.goal)

    def next_goal_notification(self, state):
        if not state._notifications.empty():
            return state.next_notification()
        event = self.events.pop(0)
        if isinstance(event.payload, ThreadGoalUpdatedNotification):
            self.goal = event.payload.goal
        state.observe(event)
        return state.next_notification()

    def cancel_goal_operation(self, state):
        self.calls.append(("cancel", state.thread_id))

    def pause_goal(self, thread_id):
        assert thread_id == self.goal.thread_id
        self.calls.append(("pause", thread_id))
        self.goal = self.goal.model_copy(update={"status": ThreadGoalStatus.paused})

    def turn_interrupt(self, thread_id, turn_id):
        assert thread_id == self.goal.thread_id and turn_id == self.turn.id
        self.calls.append(("interrupt", turn_id))
        self.active = False

    def start_goal_operation(self, *args):
        pytest.fail("must never start/recreate the original Goal")

    def thread_goal_clear(self, *args):
        pytest.fail("must never clear the original Goal")

    def complete(self, status="complete"):
        terminal = self.goal.model_copy(update={"status": ThreadGoalStatus(status), "tokens_used": 12350})
        self.events = [Notification("turn/completed", TurnCompletedNotification(
            thread_id=self.goal.thread_id, turn=self.turn.model_copy(update={"status": TurnStatus.completed}))),
            Notification("thread/goal/updated", ThreadGoalUpdatedNotification(thread_id=self.goal.thread_id, goal=terminal))]


def runner_fixture(tmp_path, status="active", active=True, **kwargs):
    origin, handoff = origin_fixture(tmp_path)
    server = OfflineServer(origin, status, active)
    thread = SimpleNamespace(id=origin["thread_id"], _client=server)
    backend = SimpleNamespace(threads={thread.id: thread}, _new_thread_ids=set(), collector=None,
        _logger=MagicMock(), ensure_thread=MagicMock(return_value=thread.id), last_native_goal={})
    runner = NativeGoalRunner(backend, "investigator", goal_objective=origin["objective"],
        context_prompt=bind_origin_context(origin), handoff_path=str(handoff), thread_id=thread.id,
        model="fixture-model", provider="codex", **kwargs)
    return runner, backend, server, origin, handoff


@pytest.mark.parametrize("status,active", [("active", True), ("active", False), ("paused", False)])
def test_same_goal_resume_keeps_identity_usage_context_and_original_handoff(tmp_path, status, active):
    with patch("theory_master.codex_goal_transport.NativeGoalRunner._setup_context") as setup:
        runner, backend, server, origin, handoff = runner_fixture(tmp_path, status, active)
        before = handoff.read_bytes()
        server.complete()
        identifier, raw = runner.run()
    assert identifier == origin["thread_id"] and "The original authored claim." in raw
    setup.assert_not_called()
    assert handoff.read_bytes() == before
    identity = backend.last_native_goal["persistent_goal"]
    assert identity["created_at"] == 123 and identity["tokens_used"] == 12350
    assert identity["time_used_seconds"] == 678 and identity["token_budget"] == 99999
    assert identity["objective_sha256"] == origin["objective_sha256"]
    assert backend.last_native_goal["origin_binding"]["invocation_id"] == origin["invocation_id"]
    assert sum(call[0] == "set" for call in server.calls) == (0 if active else 1)
    assert sum(call[0] == "unregister" for call in server.calls) == 1
    assert not any(call[0] == "cancel" for call in server.calls)


@pytest.mark.parametrize("original_transcript", [True, False])
def test_verified_quota_resume_wakes_the_same_usage_limited_goal(tmp_path, original_transcript):
    from theory_master.retained_goal_quota import goal_rollout_identity

    runner, backend, server, origin, handoff = runner_fixture(tmp_path, "usageLimited", False)
    rollout = tmp_path / "original-rollout.jsonl"
    rollout.write_text("original native Goal transcript fixture")
    backend.retained_goal_home = SimpleNamespace(
        thread_id=origin["thread_id"], account="2", provider="codex",
        binding=goal_rollout_identity(str(rollout)),
    )
    server.rollout_path = str(rollout if original_transcript else tmp_path / "another-rollout.jsonl")
    backend.retained_goal_delivered = False
    backend.client_account = "2"
    original = handoff.read_bytes()
    server.complete()

    if not original_transcript:
        with pytest.raises(NativeGoalRecoveryConflict, match="original native Goal transcript"):
            runner.run()
        assert not any(call[0] == "set" for call in server.calls)
        return
    identifier, raw = runner.run()

    assert identifier == origin["thread_id"]
    assert "The original authored claim." in raw
    assert handoff.read_bytes() == original
    assert [call for call in server.calls if call[0] == "set"] == [
        ("set", {"status": ThreadGoalStatus.active}),
    ]
    identity = backend.last_native_goal["persistent_goal"]
    assert identity["created_at"] == 123 and identity["tokens_used"] == 12350
    assert identity["objective_sha256"] == origin["objective_sha256"]


def test_already_complete_delivers_original_artifact_without_new_turn(tmp_path):
    runner, backend, server, origin, handoff = runner_fixture(tmp_path, "complete", False)
    assert "The original authored claim." in runner.run()[1]
    assert not any(call[0] in {"set", "cancel", "read"} for call in server.calls)
    assert backend.last_native_goal["status"] == "complete"


def test_authoritative_receipt_identity_rejects_same_text_replacement(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path)
    runner.origin["persistent_goal"] = verify_existing_goal(server.goal, runner.origin)
    server.goal = server.goal.model_copy(update={"created_at": 999})
    with pytest.raises(NativeGoalRecoveryConflict, match="identity or accumulated"):
        runner.run()
    assert not any(call[0] in {"register", "set", "cancel"} for call in server.calls)


def test_completion_during_read_does_not_reactivate_goal(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path, active=False)
    read = server.thread_read
    def complete_during_read(*args, **kwargs):
        result = read(*args, **kwargs)
        server.goal = server.goal.model_copy(update={"status": ThreadGoalStatus.complete})
        return result
    server.thread_read = complete_during_read
    assert "original authored claim" in runner.run()[1]
    assert not any(call[0] in {"set", "cancel"} for call in server.calls)


def test_start_attachment_identity_is_persisted_before_physical_completion(tmp_path):
    observed = []
    runner, _, server, _, _ = runner_fixture(tmp_path, goal_identity_observer=lambda row: observed.append(row))
    server.complete()
    runner.run()
    assert len(observed) == 1
    assert observed[0]["status"] == "active" and observed[0]["tokens_used"] == 12345
    assert observed[0]["created_at"] == 123


@pytest.mark.parametrize("status", ["blocked", "usageLimited", "budgetLimited"])
def test_native_terminal_resource_stops_are_never_reactivated(tmp_path, status):
    runner, backend, server, origin, handoff = runner_fixture(tmp_path, status, False)
    with pytest.raises(Exception):
        runner.run()
    assert not any(call[0] in {"set", "cancel", "read"} for call in server.calls)
    assert backend.last_native_goal["status"] == status


@pytest.mark.parametrize("field,value", [("objective", "another science"), ("thread_id", "foreign"),
    ("created_at", 124), ("token_budget", 1), ("tokens_used", 1), ("time_used_seconds", 1)])
def test_identity_and_usage_cannot_be_reset(tmp_path, field, value):
    origin, _ = origin_fixture(tmp_path)
    goal = OfflineServer(origin).goal
    initial = verify_existing_goal(goal, origin)
    with pytest.raises(NativeGoalRecoveryConflict):
        verify_existing_goal(goal.model_copy(update={field: value}), origin, previous=initial)


def test_foreign_goal_is_not_cancelled_or_changed(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path)
    server.goal = server.goal.model_copy(update={"objective": "foreign goal"})
    with pytest.raises(NativeGoalRecoveryConflict):
        runner.run()
    assert not any(call[0] in {"register", "set", "cancel"} for call in server.calls)


def test_paused_but_still_active_turn_fails_closed(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path, "paused", True)
    with pytest.raises(NativeGoalRecoveryConflict, match="still has an active"):
        runner.run()
    assert not any(call[0] in {"set", "cancel"} for call in server.calls)


def test_control_guard_prevents_activation(tmp_path):
    check = MagicMock(side_effect=RecoveryStop("operator pause"))
    with pytest.raises(RecoveryStop):
        runner_fixture(tmp_path, "paused", False, control_check=check)
    check.assert_called_once()


def test_control_after_verified_registration_pauses_owned_original_before_unregister(tmp_path):
    runner, _, server, origin, _ = runner_fixture(tmp_path)
    def control():
        if server.state is not None:
            raise RecoveryStop("operator pause")
    with pytest.raises(RecoveryStop):
        attach_existing_goal(runner.thread, origin, check_control=control)
    names = [call[0] for call in server.calls]
    assert names.count("cancel") == names.count("unregister") == 1
    assert names.index("cancel") < names.index("unregister")
    assert "set" not in names
    assert not server.active and server.goal.status == ThreadGoalStatus.paused


def test_control_during_stream_cancels_once_and_preserves_original_file(tmp_path):
    check = MagicMock()
    runner, _, server, _, handoff = runner_fixture(tmp_path, control_check=check)
    before = handoff.read_bytes()
    server.complete()
    def control():
        if server.events and len(server.events) == 1:
            raise RecoveryStop("operator pause")
    runner.control_check = control
    with pytest.raises(RecoveryStop):
        runner.run()
    assert sum(call[0] == "cancel" for call in server.calls) == 1
    assert sum(call[0] == "unregister" for call in server.calls) == 1
    assert handoff.read_bytes() == before


def test_budget_guard_prevents_any_resume(tmp_path):
    with pytest.raises(NativeGoalBudgetExceeded):
        runner_fixture(tmp_path, token_allowance=100, tokens_before_goal=100)


def test_typed_quota_is_not_downgraded_to_busy_or_capacity(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path)
    server.request_error = ProviderQuotaExhausted("quota exhausted", provider="codex", account="fixture")
    with pytest.raises(ProviderQuotaExhausted):
        runner.run()
    assert not any(call[0] in {"set", "cancel"} for call in server.calls)


def test_unrelated_provider_error_is_retained(tmp_path):
    runner, _, server, _, _ = runner_fixture(tmp_path)
    error = ConnectionError("provider transport disconnected")
    server.request_error = error
    with pytest.raises(ConnectionError) as raised:
        runner.run()
    assert raised.value is error
    assert not any(call[0] in {"set", "cancel"} for call in server.calls)


@pytest.mark.parametrize("mutation", ["handoff", "hash", "context", "symlink", "invocation"])
def test_invalid_origin_binding_is_rejected(tmp_path, mutation):
    origin, handoff = origin_fixture(tmp_path)
    if mutation == "handoff": origin["handoff_path"] = str(tmp_path / "other.json")
    elif mutation == "hash": origin["objective_sha256"] = "0" * 64
    elif mutation == "context": origin["setup_context"] += "changed"
    elif mutation == "invocation": origin["invocation_id"] = "../foreign"
    else:
        handoff.unlink()
        handoff.symlink_to(tmp_path / "foreign.json")
    with pytest.raises(NativeGoalRecoveryConflict):
        validate_origin(origin, handoff, origin["thread_id"])


def session_fixture(tmp_path):
    origin, handoff = origin_fixture(tmp_path)
    record = {"invocation_id": origin["invocation_id"], "role": "investigator", "stage": "investigate",
              "logical_key": "original-slot", "thread_id": "original-thread", "contract_ref": origin["contract_ref"],
              "commit_state": "not_committed", "state": "interrupted"}
    (tmp_path / "events.jsonl").write_text(json.dumps({"type": "role.started", "at": "2026-11-19T00:00:00",
        "payload": {"invocation_id": origin["invocation_id"], "thread_id": "original-thread"}}) + "\n")
    (tmp_path / "runtime_events.jsonl").write_text("".join(json.dumps({"event": "AGENT_INPUT", "role": "investigator",
        "stage": "investigate", "ts": "2026-11-19T00:00:01", "input_kind": kind, "content": content}) + "\n"
        for kind, content in [("native_goal_setup_turn", "SDK Goal setup\n" + origin["setup_context"]),
                              ("native_goal_objective", origin["objective"])]))
    session = SimpleNamespace(context={}, state={"recovery": {"action": "resume"}, "hypothesis_lock": SCIENCE},
        owner=SimpleNamespace(cycle_dir=tmp_path), invocation_ledger=SimpleNamespace(snapshots=lambda: [record]),
        invocation_id="inv-1-investigator-monitor", stage="investigate", logical_key="original-slot",
        identifier="original-thread", contract=SimpleNamespace(descriptor=lambda: origin["contract_ref"]),
        handoff_path=handoff.parent / "inv-1-investigator-monitor.json")
    return session, record, origin


def test_audited_original_setup_is_found_without_changing_science(tmp_path):
    session, _, expected = session_fixture(tmp_path)
    before = copy.deepcopy(session.state)
    assert find_native_goal_origin(session) == expected
    assert session.state == before


def test_origin_prefers_exact_bound_audit_over_same_second_inputs(tmp_path):
    session, record, expected = session_fixture(tmp_path)
    audit = tmp_path / "runtime_events.jsonl"
    original = [json.loads(row) for row in audit.read_text().splitlines()]
    for row in original:
        row.update(invocation_id=record["invocation_id"], thread_id=session.identifier)
    unrelated = {**original[1], "invocation_id": "inv-1-investigator-other", "content": "Other objective"}
    audit.write_text("".join(json.dumps(row) + "\n" for row in [*original, unrelated]))
    assert find_native_goal_origin(session) == expected


def test_preparation_reuses_only_explicit_original_path_and_records_binding(tmp_path):
    from theory_master.invocation_transport import InvocationTransport
    session, _, origin = session_fixture(tmp_path)
    session.role = "investigator"
    session.owner.backend = SimpleNamespace(invoke_goal=MagicMock())
    session.owner._persist = MagicMock()
    session.state["prompt_metrics"] = [{}]
    session.native_preflight_goal = "new renderer must not replace original"
    session.prompt_context = {"frozen_objective": SCIENCE}
    session.skill_addendum = ""
    session.contract.prompt_projection = lambda context: "fixture contract"
    session.invocation_ledger.transition = MagicMock()
    InvocationTransport(session)._prepare_native_delivery()
    assert session.native_objective == origin["objective"]
    assert session.handoff_path == Path(origin["handoff_path"])
    assert session.handoff_path.read_text().find("original authored") >= 0
    assert session.native_goal_origin == origin
    assert session.invocation_ledger.transition.call_args.kwargs["native_goal_origin"] == origin


def test_pinned_quota_cannot_launch_without_audited_original_goal(tmp_path):
    from theory_master.invocation_transport import InvocationTransport

    session, _, origin = session_fixture(tmp_path)
    session.role = "investigator"
    session.route = SimpleNamespace(provider="CodexHTTPS")
    goal = SimpleNamespace(thread_id=origin["thread_id"], provider="CodexHTTPS")
    session.owner.backend = SimpleNamespace(
        invoke_goal=MagicMock(), retained_goal_home=goal, retained_goal_delivered=False,
    )
    session.state["prompt_metrics"] = [{}]
    session.native_preflight_goal = "Replacement objective is forbidden"
    session.prompt_context = {"frozen_objective": SCIENCE}
    session.skill_addendum = ""
    session.contract.prompt_projection = lambda context: "fixture contract"
    session.invocation_ledger.snapshots = lambda: []
    with pytest.raises(NativeGoalRecoveryConflict, match="original Investigator Goal"):
        InvocationTransport(session)._prepare_native_delivery()
    session.owner.backend.invoke_goal.assert_not_called()


@pytest.mark.parametrize("source", ["human", "supervisor_attention"])
@pytest.mark.parametrize("reattaching", [False, True])
def test_native_goal_guidance_consumption_requires_a_setup_turn(tmp_path, source, reattaching):
    from theory_master.attention_guidance import load_attention_guidance, write_attention_guidance
    from theory_master.invocation_transport import InvocationTransport

    session, _, origin = session_fixture(tmp_path)
    if not reattaching:
        session.invocation_ledger.snapshots = lambda: []
    guidance = write_attention_guidance(tmp_path, "Use the verified source before the test.", source=source)
    session.pending_guidance = guidance
    session.state.update(
        prompt_metrics=[{}], operator_guidance=guidance, operator_guidance_pending=True,
        attention_guidance=guidance,
    )
    session.role = "investigator"
    session.route = SimpleNamespace(model="fixture-model", provider="CodexHTTPS")
    session.continuation = True
    session.prompt = "Ordinary continuation is not used for a Native Goal."
    session.skill_binding = None
    session.skill_addendum = ""
    session.native_preflight_goal = "Current authorized objective"
    session.prompt_context = {"frozen_objective": SCIENCE, "operator_guidance": {"text": guidance["text"]}}
    session.contract.prompt_projection = lambda context: "fixture contract"
    session.invocation_ledger.transition = MagicMock()
    invoke_goal = MagicMock(return_value=(session.identifier, '{"findings":["Fixture result."]}'))
    session.owner.backend = SimpleNamespace(invoke_goal=invoke_goal, last_native_goal={"status": "complete"})
    session.owner.spec = SimpleNamespace(research_root=tmp_path)
    session.owner.collector = None
    session.owner._persist = MagicMock()
    session.owner._emit = MagicMock()
    transport = InvocationTransport(session)
    transport._prepare_native_delivery()
    with patch("theory_master.invocation_transport.budget_status", return_value={
        "allowance_tokens": 100_000, "observed_tokens": 10_000,
    }):
        transport._invoke_initial()

    sent = invoke_goal.call_args.kwargs
    setup, _ = json.JSONDecoder().raw_decode(sent["context_prompt"])
    if reattaching:
        assert "operator_guidance" not in setup
        assert sent["goal_objective"] == origin["objective"]
        assert sent["handoff_path"] == origin["handoff_path"]
        assert load_attention_guidance(tmp_path) == guidance
        assert session.state["operator_guidance_pending"] is True
        assert session.state["attention_guidance"] == guidance
        session.owner._emit.assert_not_called()
    else:
        assert setup["operator_guidance"]["text"] == guidance["text"]
        assert sent["goal_objective"] == session.native_preflight_goal
        assert load_attention_guidance(tmp_path)["status"] == "consumed"
        assert session.state["operator_guidance_pending"] is False
        assert session.owner._emit.call_args.args[1] == "attention.guidance_consumed"


def test_inherited_monitor_origin_is_rechecked_against_original_audit(tmp_path):
    session, record, origin = session_fixture(tmp_path)
    monitor = {**record, "invocation_id": "inv-1-investigator-old-monitor", "native_goal_origin": origin}
    session.invocation_ledger.snapshots = lambda: [record, monitor]
    assert find_native_goal_origin(session) == origin
    monitor["native_goal_origin"] = {**origin, "setup_context": origin["setup_context"] + "tampered"}
    with pytest.raises(NativeGoalRecoveryConflict, match="differs from its original"):
        find_native_goal_origin(session)


def test_original_immediate_identity_survives_interruption(tmp_path):
    session, record, origin = session_fixture(tmp_path)
    record["native_goal_identity"] = verify_existing_goal(OfflineServer(origin).goal, origin)
    result = find_native_goal_origin(session)
    assert result["persistent_goal"] == record["native_goal_identity"]


@pytest.mark.parametrize("where", ["original", "monitor"])
def test_durable_sdk_identity_survives_pause_and_engine_hash_change(tmp_path, where):
    session, record, origin = session_fixture(tmp_path)
    server = OfflineServer(origin, "paused", False)
    identity = verify_existing_goal(server.goal, origin)
    if where == "original":
        record["native_goal_identity"] = identity
    else:
        monitor = {**record, "invocation_id": "inv-1-investigator-old-monitor",
                   "native_goal_origin": origin, "native_goal_identity": identity}
        session.invocation_ledger.snapshots = lambda: [record, monitor]
    # This represents the consumed receipt's old source hash after a code
    # upgrade. An independent SDK identity must no longer depend on it.
    with patch("theory_master.supervisor_native_goal_recovery.receipt_goal_identity",
               side_effect=ValueError("old receipt source manifest changed")) as old_receipt:
        bound = find_native_goal_origin(session)
        old_receipt.assert_not_called()
    thread = SimpleNamespace(id=origin["thread_id"], _client=server)
    attached = attach_existing_goal(thread, bound)
    assert attached.identity["created_at"] == identity["created_at"]
    assert attached.identity["tokens_used"] == identity["tokens_used"]
    server.unregister_goal_operation(attached.state)
    server.goal = server.goal.model_copy(update={"created_at": identity["created_at"] + 1})
    with pytest.raises(NativeGoalRecoveryConflict, match="identity or accumulated"):
        attach_existing_goal(thread, bound)


def test_legacy_origin_without_official_identity_still_requires_receipt_seed(tmp_path):
    session, _, _ = session_fixture(tmp_path)
    with patch("theory_master.supervisor_native_goal_recovery.receipt_goal_identity",
               side_effect=ValueError("old receipt source manifest changed")) as old_receipt:
        with pytest.raises(ValueError, match="source manifest changed"):
            find_native_goal_origin(session)
        old_receipt.assert_called_once()


@pytest.mark.parametrize("ownership", ["workspace", "rollout", "other_task", "own_server"])
def test_process_local_sdk_locks_do_not_authorize_foreign_physical_owner(tmp_path, ownership):
    origin, _ = origin_fixture(tmp_path)
    proc_root = tmp_path / "proc"
    proc = proc_root / "4242"
    (proc / "fd").mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"/sdk/codex\0app-server\0")
    (proc / "cwd").symlink_to(origin["workspace_path"] if ownership in {"workspace", "own_server"}
                               else tmp_path / "another-task")
    if ownership == "rollout":
        (proc / "fd/7").symlink_to(tmp_path / ("rollout-2026-" + origin["thread_id"] + ".jsonl"))
    if ownership in {"workspace", "rollout"}:
        with pytest.raises(NativeGoalRecoveryConflict, match="another local app-server"):
            ensure_no_foreign_goal_owner(origin, proc_root=proc_root)
    else:
        ensure_no_foreign_goal_owner(origin, proc_root=proc_root,
                                    current_server_pid=4242 if ownership == "own_server" else None)


def test_new_identity_observer_writes_only_operational_audit(tmp_path):
    from theory_master.invocation_transport import InvocationTransport
    session, _, origin = session_fixture(tmp_path)
    session.owner._persist = MagicMock()
    session.invocation_ledger.transition = MagicMock()
    before = copy.deepcopy(session.state["hypothesis_lock"])
    identity = verify_existing_goal(OfflineServer(origin).goal, origin)
    InvocationTransport(session)._record_goal_identity(identity)
    assert session.state["hypothesis_lock"] == before
    assert session.state["native_goal"]["origin_identity"] == identity
    assert session.invocation_ledger.transition.call_args.kwargs["native_goal_identity"] == identity


def test_resumed_original_execution_limit_closes_without_rewriting_author_id_or_science(tmp_path):
    import test_native_goal_quota_auto_adjust_20261010 as quota_fixture
    from test_native_goal_execution_limit_20261119 import _delivery
    from theory_master.native_goal import NativeGoalExecutionLimit
    from theory_master.observability.collector import Collector
    from theory_master.workflow import Workflow

    class PausedThenLimited(quota_fixture.GoalQuotaBackend):
        def invoke_goal(self, role, *, context_prompt, handoff_path, thread_id, **kwargs):
            self.started.append(thread_id)
            context, _ = json.JSONDecoder().raw_decode(context_prompt)
            binding = context["sdk_execution_limit_handoff"]
            if len(self.started) == 1:
                self.original_id = binding["invocation_id"]
                self.original_goal = kwargs["goal_objective"]
                self.last_native_goal = {"status": "paused", "thread_id": thread_id}
                raise NativeGoalIncomplete("paused", thread_id)
            assert context["sdk_native_goal_origin"]["invocation_id"] == self.original_id
            assert binding["invocation_id"] == self.original_id
            assert kwargs["goal_objective"] == self.original_goal
            path, limit, _ = _delivery(Path(binding["workspace_path"]), self.original_id,
                                       frozen)
            assert str(path) == handoff_path
            self.original_bytes = path.read_bytes()
            self.last_native_goal = {"status": "execution_limited", "thread_id": thread_id}
            raise NativeGoalExecutionLimit(thread_id, limit, path)

        def invoke(self, role, prompt, thread_id, model, provider):
            assert role not in {"investigator", "judge", "reporter"}
            return super().invoke(role, prompt, thread_id, model, provider)

    spec = quota_fixture.NativeGoalQuotaTests().make_spec(tmp_path)
    from dataclasses import replace
    spec = replace(spec, max_cycles=1)
    config = json.loads(spec.config_path.read_text())
    config["max_cycles"] = 1
    spec.config_path.write_text(json.dumps(config))
    backend = PausedThenLimited("paused")
    first_workflow = Workflow(spec, backend, collector=Collector(spec))
    first = first_workflow.run()
    frozen = copy.deepcopy(first["hypothesis_lock"])
    workflow = Workflow(spec, backend, collector=Collector(spec))
    state = workflow.resume(first["cycle_id"])
    assert state["status"] == "over" and state["scientific_verdict"] is None
    assert state["assessment_state"] == "not_adjudicated" and len(backend.started) == 2
    delivered = state["execution_limit_handoff"]
    assert delivered["invocation_id"] == backend.original_id
    assert delivered["execution_limit"]["invocation_id"] == backend.original_id
    assert delivered["monitor_invocation_id"] != backend.original_id
    assert Path(delivered["handoff_path"]).read_bytes() == backend.original_bytes
    assert state["hypothesis_lock"] == frozen
    assert state["scientific_model_version_in"] == state["scientific_model_version_out"]
    assert not state["role_outputs"].get("judge") and not state["role_outputs"].get("investigator")
    assert not (workflow.cycle_dir / "evidence/registry.jsonl").exists()
    from theory_master.invocations import InvocationLedger
    ledger = InvocationLedger(workflow.cycle_dir / "invocations.jsonl")
    monitor = ledger.snapshot(delivered["monitor_invocation_id"])
    assert monitor["state"] == "interrupted" and monitor["commit_state"] == "not_committed"
    assert monitor["delivery_origin_invocation_id"] == backend.original_id


@pytest.mark.parametrize("field,value", [("logical_key", "foreign-slot"), ("stage", "judge_rework"),
    ("identifier", "foreign-thread"), ("native_objective", "foreign science")])
def test_execution_limit_old_id_requires_exact_monitor_origin_binding(tmp_path, field, value):
    from theory_master.native_goal import EXECUTION_LIMIT_SCHEMA
    session, _, origin = session_fixture(tmp_path)
    session.native_goal_origin = origin
    session.native_objective = origin["objective"]
    session.handoff_path = Path(origin["handoff_path"])
    binding = {"schema": EXECUTION_LIMIT_SCHEMA, "invocation_id": origin["invocation_id"],
        "frozen_objective_sha256": origin["frozen_objective_sha256"],
        "workspace_path": origin["workspace_path"], "handoff_path": origin["handoff_path"]}
    origin["setup_context"] = json.dumps({"sdk_execution_limit_handoff": binding})
    origin["setup_sha256"] = hashlib.sha256(origin["setup_context"].encode()).hexdigest()
    session.setup_context = bind_origin_context(origin)
    session.limit_binding = binding
    assert execution_limit_delivery_id(session) == origin["invocation_id"]
    setattr(session, field, value)
    with pytest.raises(NativeGoalRecoveryConflict):
        execution_limit_delivery_id(session)


@pytest.mark.parametrize("mutation", ["foreign_thread", "foreign_slot", "committed", "science", "duplicate_setup"])
def test_audit_mismatch_cannot_adopt_original_goal(tmp_path, mutation):
    session, record, _ = session_fixture(tmp_path)
    if mutation == "foreign_thread": record["thread_id"] = "foreign"
    elif mutation == "foreign_slot": record["logical_key"] = "foreign"
    elif mutation == "committed": record["commit_state"] = "committed"
    elif mutation == "science": session.state["hypothesis_lock"] = {**SCIENCE, "scope": "foreign input"}
    else:
        path = tmp_path / "runtime_events.jsonl"
        path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    if mutation in {"science", "duplicate_setup", "foreign_thread"}:
        with pytest.raises(NativeGoalRecoveryConflict): find_native_goal_origin(session)
    else:
        assert find_native_goal_origin(session) is None
