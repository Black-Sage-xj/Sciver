"""Offline regression for retained-author submission Turns and token authority."""
from __future__ import annotations

import copy
import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from theory_master.codex_errors import CodexProviderRequestRejected
from theory_master.codex_turn_transport import CodexTurnTransport
from theory_master.invocation_context import InvocationContextPreparation
from theory_master.invocation_preparation import InvocationPreparation
from theory_master.invocation_transport import InvocationTransport
from theory_master.provider_errors import ProviderQuotaExhausted
from theory_master.submission_rework import delivery_usage_guard
from theory_master.workflow_support import RecoveryStop


THREAD = "original-investigator-thread"


def usage(total: int, last: int = 10):
    return NS(method="thread/tokenUsage/updated", payload=NS(
        threadId=THREAD, turnId="delivery-turn", tokenUsage={
            "total": {"inputTokens": total, "outputTokens": 0},
            "last": {"inputTokens": last, "outputTokens": 0},
        }))


def completed(tokens: int | None = None):
    turn = NS(id="delivery-turn", status="completed", final_response="{}")
    if tokens is not None:
        turn.usage = {"input_tokens": tokens, "output_tokens": 0}
    return NS(method="turn/completed", payload=NS(threadId=THREAD, turn=turn))


class Handle:
    def __init__(self, steps):
        self.steps = steps
        self.interrupt = Mock()

    def stream(self):
        for step in self.steps:
            if isinstance(step, BaseException):
                raise step
            if callable(step):
                step()
            else:
                yield step


class Thread:
    def __init__(self, scripts):
        self.handles = [Handle(script) for script in scripts]
        self.payloads = []
        self.start_goal = Mock(side_effect=AssertionError("ordinary delivery cannot start a Goal"))
        self.clear_goal = Mock(side_effect=AssertionError("ordinary delivery cannot clear a Goal"))

    def turn(self, payload):
        self.payloads.append(payload)
        return self.handles[len(self.payloads) - 1]


def transport(scripts):
    thread = Thread(scripts)
    client = NS(close=Mock())
    backend = NS(threads={THREAD: thread}, _new_thread_ids=set(), client=client,
        client_account="1", thread_accounts={THREAD: "1"}, collector=None,
        OUTER_TURN_RETRIES=2, OUTER_RETRY_DELAYS=(0,),
        _logger=logging.getLogger(__name__), _watchdog_seconds=lambda: 60,
        _run_network_guard=lambda *a, **k: None,
        invoke_goal=Mock(side_effect=AssertionError("ordinary delivery cannot invoke Goal")))

    def ensure(role, thread_id, model, provider):
        assert role == "investigator" and thread_id == THREAD
        backend.threads[THREAD] = thread
        return THREAD

    backend.ensure_thread = Mock(side_effect=ensure)
    return CodexTurnTransport(backend), thread, backend, client


def run(owner, **kwargs):
    return owner.run("investigator", "Supplement the public JSON only", THREAD, "model", "provider", **kwargs)


@pytest.mark.parametrize("error_type", [RecoveryStop, RuntimeError])
def test_control_authority_with_network_words_never_retries(error_type):
    owner, thread, backend, client = transport([[completed()]])
    failure = error_type("operator pause: connection timeout troubleshooting")
    check = Mock(side_effect=failure)
    with pytest.raises(error_type) as caught:
        run(owner, control_check=check)
    assert caught.value is failure
    assert check.call_count == 1
    backend.ensure_thread.assert_not_called()
    client.close.assert_not_called()
    assert not thread.payloads


@pytest.mark.parametrize("error_type", [RecoveryStop, RuntimeError])
def test_resource_guard_exception_never_becomes_network_retry(error_type):
    owner, thread, _, client = transport([[usage(1_020), completed()]])
    failure = error_type("resource guard: connection timeout")

    def guard(used):
        raise failure

    guard.provider_usage_baseline = 1_000
    with pytest.raises(error_type) as caught:
        run(owner, usage_guard=guard)
    assert caught.value is failure
    assert owner.last_usage_tokens == 20
    assert len(thread.payloads) == 1
    thread.handles[0].interrupt.assert_called_once()
    client.close.assert_not_called()


def test_terminal_only_usage_reaches_budget_guard_and_interrupts():
    owner, thread, _, client = transport([[completed(60)]])
    seen = []

    def guard(used):
        seen.append(used)
        if used >= 50:
            raise RecoveryStop("original allowance exhausted")

    with pytest.raises(RecoveryStop, match="allowance exhausted"):
        run(owner, usage_guard=guard)
    assert seen == [60] and owner.last_usage_tokens == 60
    thread.handles[0].interrupt.assert_called_once()
    client.close.assert_not_called()


def test_terminal_and_live_representations_are_not_double_charged():
    owner, _, _, _ = transport([[usage(1_030, 30), completed(30)]])
    seen = []

    def guard(used):
        seen.append(used)

    guard.provider_usage_baseline = 1_000
    assert run(owner, usage_guard=guard) == (THREAD, "{}")
    assert seen == [30, 30]
    assert owner.last_usage_tokens == 30


@pytest.mark.parametrize("explicit_baseline", [True, False])
def test_reconnect_retains_baseline_and_counts_missing_request_notifications(explicit_baseline):
    owner, thread, backend, client = transport([
        [usage(1_010), ConnectionError("connection lost")],
        # Twenty additional tokens happened while notifications were lost.
        [usage(1_040), completed(10)],
    ])
    seen = []

    def guard(used):
        seen.append(used)

    if explicit_baseline:
        guard.provider_usage_baseline = 1_000
    assert run(owner, usage_guard=guard) == (THREAD, "{}")
    assert seen == [10, 40, 40]
    assert owner.last_usage_tokens == 40
    assert len(thread.payloads) == 2
    assert all(call.args[1] == THREAD for call in backend.ensure_thread.call_args_list)
    client.close.assert_called_once()


def test_persisted_baseline_counts_usage_lost_before_first_notification():
    owner, _, _, _ = transport([
        [ConnectionError("connection lost before usage notification")],
        [usage(1_040), completed(10)],
    ])
    seen = []

    def guard(used):
        seen.append(used)

    guard.provider_usage_baseline = 1_000
    run(owner, usage_guard=guard)
    assert seen == [40, 40]


def test_setup_guard_without_explicit_baseline_does_not_charge_thread_history():
    owner, _, _, _ = transport([[usage(50_010), completed(10)]])
    seen = []
    run(owner, usage_guard=seen.append)
    assert seen == [10, 10] and owner.last_usage_tokens == 10


def test_terminal_usage_before_reconnect_is_retained_when_cumulative_events_begin():
    owner, _, _, _ = transport([
        [completed(100), ConnectionError("connection closed after terminal usage")],
        [usage(1_130, 30), completed(30)],
    ])
    seen = []
    run(owner, usage_guard=seen.append)
    assert seen == [100, 130, 130]
    assert owner.last_usage_tokens == 130


def test_stream_failure_preserves_already_observed_usage():
    owner, thread, _, client = transport([[usage(1_030, 30), ValueError("invalid stream")]])
    seen = []

    def guard(used):
        seen.append(used)

    guard.provider_usage_baseline = 1_000
    with pytest.raises(ValueError, match="invalid stream"):
        run(owner, usage_guard=guard)
    assert seen == [30] and owner.last_usage_tokens == 30
    thread.handles[0].interrupt.assert_called_once()
    client.close.assert_not_called()


@pytest.mark.parametrize("terminal", [False, True])
def test_pause_after_usage_notification_persists_charge_before_control(tmp_path, monkeypatch, terminal):
    monkeypatch.setenv("THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS", "2000")
    state = {"agents": {"investigator": {"raw_usage": {"input_tokens": 1000, "output_tokens": 0}}},
             "thread_usage_baselines": {THREAD: {"input_tokens": 1000, "output_tokens": 0}},
             "investigator_budget": {"observed_tokens": 1000}}
    paused = []
    saved = []

    def check(value):
        if paused:
            raise RecoveryStop("operator pause for connection checks")

    workflow = NS(cycle_dir=tmp_path / "research" / "1", spec=NS(research_root=tmp_path / "research"),
        _persist=lambda value: saved.append(copy.deepcopy(value)), _check_recovery_control=check)
    guard = delivery_usage_guard(NS(owner=workflow, state=state,
        context={"submission_delivery": {"thread_id": THREAD}}))
    owner, thread, _, client = transport([[lambda: paused.append(True), completed(20) if terminal else usage(1_020)]])
    with pytest.raises(RecoveryStop, match="operator pause"):
        run(owner, usage_guard=guard, control_check=lambda: check(state))
    assert state["investigator_budget"]["observed_tokens"] == 1020
    assert saved[-1]["investigator_budget"]["observed_tokens"] == 1020
    assert owner.last_usage_tokens == 20
    thread.handles[0].interrupt.assert_called_once()
    client.close.assert_not_called()


@pytest.mark.parametrize("failure", [
    ProviderQuotaExhausted("quota exhausted: connection timeout", provider="provider", account="1"),
    CodexProviderRequestRejected("invalid request: connection timeout"),
])
def test_typed_provider_failures_preserve_identity_and_do_not_retry(failure):
    owner, thread, _, client = transport([[failure]])
    with pytest.raises(type(failure)) as caught:
        run(owner)
    assert caught.value is failure
    assert len(thread.payloads) == 1
    client.close.assert_not_called()


def preparation_session(tmp_path, *, provider="provider", fresh=False):
    lock = {"status": "locked", "candidate_id": "H1", "hypothesis": "Synthetic H",
            "scope": "Synthetic scope", "decisive_test": "Retained bounded test"}
    state = {"threads": {"investigator": THREAD}, "runtime_thread_providers": {"investigator": "provider"},
             "hypothesis_lock": lock, "recovery": {"allow_thread_fallback": True}}
    backend = NS(drop_thread=Mock(), ensure_thread=Mock(return_value=THREAD), invoke_goal=Mock())
    workflow = NS(spec=NS(goal="Goal", contract="Contract", final_gate="Gate", required_context="", submission_contract_ref=None),
        backend=backend, routes={"investigator": NS(model="model", provider=provider)},
        cycle_dir=tmp_path / "1", _thread_key=lambda state, role: role,
        _apply_pending_task_route=lambda *a: None, _path_anchors=lambda: {},
        _agent_graph_for_stage=lambda *a: None, _emit=Mock(), _persist=Mock())
    return NS(owner=workflow, state=state, role="investigator", actual="investigator", stage="investigate",
        fresh_thread=fresh, context={"hypothesis_lock": lock, "submission_delivery": {"thread_id": THREAD}})


@pytest.mark.parametrize("provider,fresh", [("changed-provider", False), ("provider", True)])
def test_original_thread_is_not_dropped_for_provider_change_or_fresh_flag(tmp_path, provider, fresh):
    session = preparation_session(tmp_path, provider=provider, fresh=fresh)
    with pytest.raises(RecoveryStop):
        InvocationPreparation(session)._prepare_thread()
    assert session.state["threads"] == {"investigator": THREAD}
    session.owner.backend.drop_thread.assert_not_called()
    session.owner.backend.ensure_thread.assert_not_called()


@pytest.mark.parametrize("replacement", [RuntimeError("resume transport failed"), "foreign-thread"])
def test_original_thread_resume_never_falls_back_to_a_new_thread(tmp_path, replacement):
    session = preparation_session(tmp_path)
    InvocationPreparation(session)._prepare_thread()
    if isinstance(replacement, BaseException):
        session.owner.backend.ensure_thread.side_effect = replacement
    else:
        session.owner.backend.ensure_thread.return_value = replacement
    with pytest.raises(RecoveryStop):
        InvocationContextPreparation(session)._prepare_context()
    assert session.state["threads"]["investigator"] == THREAD
    assert session.owner.backend.ensure_thread.call_count == 1
    assert session.owner.backend.ensure_thread.call_args.args[1] == THREAD
    session.owner.backend.drop_thread.assert_not_called()


@pytest.mark.parametrize("failure", [RecoveryStop("pause"), ProviderQuotaExhausted("quota exhausted"),
                                     CodexProviderRequestRejected("bad request")])
def test_preparation_does_not_relabel_typed_stops_as_submission_failure(tmp_path, failure):
    session = preparation_session(tmp_path)
    InvocationPreparation(session)._prepare_thread()
    session.owner.backend.ensure_thread.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        InvocationContextPreparation(session)._prepare_context()
    assert caught.value is failure
    assert session.state["threads"]["investigator"] == THREAD
    assert session.owner.backend.ensure_thread.call_count == 1


def test_reconnect_rejects_backend_thread_replacement_before_second_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS", "2000")
    session = preparation_session(tmp_path)
    session.owner.spec.research_root = tmp_path
    session.owner._check_recovery_control = lambda state: None
    session.state["agents"] = {"investigator": {"raw_usage": {"input_tokens": 1000, "output_tokens": 0}}}
    guard = delivery_usage_guard(session)
    owner, thread, backend, _ = transport([[ConnectionError("connection lost")], [completed()]])
    identifiers = iter([THREAD, "foreign-thread"])

    def ensure(*args):
        identifier = next(identifiers)
        backend.threads[identifier] = thread
        return identifier

    backend.ensure_thread.side_effect = ensure
    with pytest.raises(RecoveryStop):
        run(owner, usage_guard=guard)
    assert len(thread.payloads) == 1
    assert session.state["threads"]["investigator"] == THREAD
    assert session.state["attention"]["code"] == "public_submission_incomplete"
    assert session.state["investigator_budget"]["observed_tokens"] == 1000


def test_next_delivery_uses_durable_thread_total_instead_of_terminal_request_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS", "2000")
    session = preparation_session(tmp_path)
    session.owner.spec.research_root = tmp_path
    session.owner._check_recovery_control = lambda state: None
    session.state.update(
        investigator_budget={"observed_tokens": 1030},
        agents={"investigator": {"thread_id": THREAD, "latest_cumulative_usage": {},
                                 "raw_usage": {"input_tokens": 30, "output_tokens": 0}}},
        thread_usage_baselines={THREAD: {"input_tokens": 1030, "output_tokens": 0}})
    guard = delivery_usage_guard(session)
    assert guard.provider_usage_baseline == 1030
    owner, _, _, _ = transport([[usage(1040), completed(10)]])
    assert run(owner, usage_guard=guard) == (THREAD, "{}")
    assert session.state["investigator_budget"]["observed_tokens"] == 1040
    assert session.state["investigator_budget"]["remaining_tokens"] == 960


def test_submission_uses_ordinary_original_thread_turn_without_goal_operations(tmp_path, monkeypatch):
    session = preparation_session(tmp_path)
    session.state["native_goal"] = {"status": "complete", "thread_id": THREAD,
        "origin_identity": {"created_at": 12345, "objective_sha256": "retained-objective"}}
    native_goal = copy.deepcopy(session.state["native_goal"])
    InvocationPreparation(session)._prepare_thread()
    InvocationContextPreparation(session)._prepare_context()
    assert session.native_preflight_goal is None
    session.identifier = THREAD
    session.owner.collector = None
    session.owner.spec.research_root = tmp_path
    session.owner.backend.invoke = Mock(return_value=(THREAD, "{}"))
    session.owner._check_recovery_control = Mock()
    session.owner.backend.start_goal = Mock(side_effect=AssertionError("Goal reset"))
    session.owner.backend.clear_goal = Mock(side_effect=AssertionError("Goal cleared"))
    monkeypatch.setattr("theory_master.submission_rework.delivery_usage_guard", lambda session: lambda used: None)
    original = tmp_path / "original-handoff.json"
    original.write_bytes(b'{"findings":["original authored statement"]}')
    before = original.read_bytes()
    scope = copy.deepcopy(session.state["hypothesis_lock"])
    transport_owner = InvocationTransport(session)
    transport_owner._prepare_native_delivery()
    assert session.use_native_goal is False
    assert transport_owner.invoke_with_audit("Supplement registered JSON only", "continuation") == (THREAD, "{}")
    assert session.owner.backend.invoke.call_args.args[2] == THREAD
    assert callable(session.owner.backend.invoke.call_args.kwargs["usage_guard"])
    assert callable(session.owner.backend.invoke.call_args.kwargs["control_check"])
    session.owner.backend.invoke_goal.assert_not_called()
    session.owner.backend.start_goal.assert_not_called()
    session.owner.backend.clear_goal.assert_not_called()
    assert original.read_bytes() == before
    assert session.state["hypothesis_lock"] == scope
    assert session.state["native_goal"] == native_goal
