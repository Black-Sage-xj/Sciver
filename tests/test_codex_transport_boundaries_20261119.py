"""Offline delivery, setup, cleanup and pinned SDK capability regressions."""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from theory_master.codex_backend import CodexBackend, CodexTurnFailed, CodexTurnCancelled
from theory_master.codex_sdk_compat import approval_handler_scope, verify_sdk
from theory_master.codex_turn_transport import TurnControl
from theory_master.native_goal import NativeGoalBudgetExceeded
from theory_master.provider_errors import ProviderQuotaExhausted


def terminal(status="completed", text="ready", error=None):
    return NS(method="turn/completed", payload=NS(thread_id="t", turn=NS(
        id="physical-1", status=status, final_response=text, error=error)))


class Handle:
    def __init__(self, events):
        self.events, self.interrupted = events, False

    def interrupt(self):
        self.interrupted = True

    def stream(self):
        yield from self.events


def backend_for(events):
    handle = Handle(events)
    thread = NS(id="t", turn=Mock(return_value=handle), read=Mock(side_effect=AssertionError("unexpected recovery read")))
    backend = object.__new__(CodexBackend)
    backend.threads = {"t": thread}
    backend.thread_accounts = {}
    backend.client = NS(_client=NS(_approval_handler=None))
    backend._new_thread_ids = set()
    backend.ensure_thread = Mock(return_value="t")
    backend._logger = logging.getLogger(__name__)
    backend.collector = NS(on_codex=Mock(), record_native_goal_usage=Mock())
    backend.task_config_path = None
    backend.last_native_goal = {}
    backend.OUTER_TURN_RETRIES = 0
    return backend, thread, handle


@pytest.mark.parametrize("events", [
    [terminal("failed", '{"valid":"partial"}', "backend failure")],
    [terminal("failed", '{"valid":"partial"}')],
    [terminal("interrupted", '{"valid":"partial"}')],
    [terminal(None, '{"valid":"partial"}')],
    [NS(method="item/completed",payload=NS(item=NS(type="agentMessage",text='{"valid":"partial"}')))],
    [NS(method="error",payload=NS(error="backend failure")), terminal(text='{"valid":"partial"}')],
])
def test_failed_or_unterminated_turn_never_delivers_partial_json(events):
    backend, thread, _ = backend_for(events)
    with pytest.raises(CodexTurnFailed):
        backend.invoke("research_lead", "context", "t", "model", "provider")
    thread.read.assert_not_called()
    assert thread.turn.call_count == 1


def test_optional_handle_callback_cannot_abort_or_skip_transport_cleanup():
    backend, _, handle = backend_for([terminal()])
    controls = []
    original_close = TurnControl.close
    def close(control):
        original_close(control)
        controls.append(control)
    broken = Mock(side_effect=RuntimeError("UI disconnected"))
    with patch.object(TurnControl, "close", close):
        assert backend.invoke("research_chat", "context", "t", "m", "p", on_handle=broken) == ("t", "ready")
    assert [call.args[0] for call in broken.call_args_list] == [handle, None]
    assert controls[0].done.is_set()
    assert not controls[0].watcher.is_alive()
    assert controls[0].timer.finished.is_set()


def test_optional_handle_callback_does_not_mask_explicit_cancellation():
    import threading
    cancelled = threading.Event(); cancelled.set()
    backend, _, _ = backend_for([terminal()])
    with pytest.raises(CodexTurnCancelled):
        backend.invoke("research_chat", "context", "t", "m", "p", cancel_event=cancelled,
                       on_handle=Mock(side_effect=RuntimeError("UI disconnected")))


@pytest.mark.parametrize("initial", [None, "missing"])
def test_approval_handler_scope_restores_none_and_missing(initial):
    inner = NS() if initial == "missing" else NS(_approval_handler=None)
    with pytest.raises(RuntimeError):
        with approval_handler_scope(NS(_client=inner), lambda original: lambda *args: {}):
            assert callable(inner._approval_handler)
            raise RuntimeError("turn failed")
    assert not hasattr(inner, "_approval_handler") if initial == "missing" else inner._approval_handler is None


def invoke_setup(backend, path, **kwargs):
    return backend.invoke_goal("investigator", goal_objective="frozen science", context_prompt="immutable context",
                               handoff_path=str(path), thread_id="t", model="m", provider="p", **kwargs)


def test_setup_quota_is_typed_and_never_starts_goal(tmp_path):
    backend, _, _ = backend_for([NS(method="error", payload=NS(error="usage limit reached"))])
    with patch("theory_master.native_goal.run_native_goal") as goal:
        with pytest.raises(ProviderQuotaExhausted):
            invoke_setup(backend, tmp_path / "findings.json")
        goal.assert_not_called()


def test_failed_setup_cannot_start_goal_with_valid_looking_text(tmp_path):
    backend, _, _ = backend_for([terminal("failed", '{"setup":"ready"}')])
    with patch("theory_master.native_goal.run_native_goal") as goal:
        with pytest.raises(CodexTurnFailed):
            invoke_setup(backend, tmp_path / "findings.json")
        goal.assert_not_called()


def test_setup_observability_and_live_budget_guard_share_turn_transport(tmp_path):
    event = NS(method="thread/tokenUsage/updated", payload=None)
    backend, _, handle = backend_for([event, terminal()])
    mapped = {"event":"TOKEN_USAGE", "raw_usage":{"input_tokens":400,"output_tokens":20},
              "last_request_usage":{"input_tokens":30,"output_tokens":10}}
    with patch("theory_master.codex_turn_transport.map_notification", return_value=mapped), \
         patch("theory_master.native_goal.run_native_goal") as goal:
        with pytest.raises(NativeGoalBudgetExceeded) as exc:
            invoke_setup(backend, tmp_path / "findings.json", tokens_before_goal=70,token_allowance=100)
    assert exc.value.observed_tokens == 110
    assert handle.interrupted
    assert backend.last_native_goal["source"] == "native_goal_setup_token_guard"
    backend.collector.on_codex.assert_called_once()
    goal.assert_not_called()


def test_successful_setup_usage_counts_toward_later_goal_budget(tmp_path):
    event = NS(method="thread/tokenUsage/updated", payload=None)
    backend, _, _ = backend_for([event, terminal()])
    mapped = {"event":"TOKEN_USAGE", "raw_usage":{"input_tokens":400,"output_tokens":20},
              "last_request_usage":{"input_tokens":30,"output_tokens":10}}
    def running(thread, objective, *, on_event):
        on_event(NS(method="thread/tokenUsage/updated"))
        raise AssertionError("budget must stop before findings")
    with patch("theory_master.codex_turn_transport.map_notification", side_effect=[mapped, None]), \
         patch("theory_master.observability.codex_events.map_notification", return_value={
             "event":"TOKEN_USAGE", "raw_usage":{"input_tokens":450,"output_tokens":20},
             "last_request_usage":{"input_tokens":20,"output_tokens":10}}), \
         patch("theory_master.native_goal.run_native_goal", side_effect=running):
        with pytest.raises(NativeGoalBudgetExceeded) as exc:
            invoke_setup(backend, tmp_path / "findings.json", tokens_before_goal=40,token_allowance=100)
    assert exc.value.observed_tokens == 110


def test_unsupported_sdk_version_fails_before_client_or_model_creation():
    with patch("theory_master.codex_sdk_compat.version", return_value="0.156.0"):
        with pytest.raises(RuntimeError, match="only tested openai-codex==0.155.1"):
            verify_sdk()


def test_missing_private_capability_fails_preflight():
    from openai_codex.client import CodexClient
    with patch.object(CodexClient, "cancel_goal_operation", None):
        with pytest.raises(RuntimeError, match="cancel_goal_operation"):
            verify_sdk()


def test_exhausted_budget_blocks_even_setup_before_paid_turn(tmp_path):
    backend, thread, _ = backend_for([terminal()])
    with patch("theory_master.native_goal.run_native_goal") as goal:
        with pytest.raises(NativeGoalBudgetExceeded):
            invoke_setup(backend, tmp_path / "findings.json", tokens_before_goal=100, token_allowance=100)
    thread.turn.assert_not_called()
    goal.assert_not_called()


def test_terminal_error_is_authoritative_when_optional_mapping_fails():
    backend, _, _ = backend_for([terminal("completed", '{"partial":"json"}', "provider failure")])
    with patch("theory_master.codex_turn_transport.map_notification", side_effect=ValueError("unknown event shape")):
        with pytest.raises(CodexTurnFailed, match="provider failure"):
            backend.invoke("research_lead", "context", "t", "m", "p")


def test_setup_retry_counts_usage_across_both_monitored_attempts():
    from theory_master.codex_turn_transport import CodexTurnTransport
    usage = NS(method="thread/tokenUsage/updated", payload=None)
    first, second = Handle([usage,terminal("failed",None,"connection closed")]), Handle([usage,terminal()])
    backend, thread, _ = backend_for([])
    thread.turn.side_effect = [first, second]
    backend.ensure_thread.side_effect = lambda *args: backend.threads.setdefault("t",thread) and "t"
    backend.OUTER_TURN_RETRIES = 1
    backend.OUTER_RETRY_DELAYS = (0,)
    backend.client = None
    calls = []
    mapped = [
        {"event":"TOKEN_USAGE", "raw_usage":{"input_tokens":10}, "last_request_usage":{"input_tokens":10}},
        {"event":"LLM_TURN_COMPLETED", "error":"connection closed"},
        {"event":"TOKEN_USAGE", "raw_usage":{"input_tokens":30}, "last_request_usage":{"input_tokens":20}},
        {"event":"LLM_TURN_COMPLETED"},
    ]
    transport = CodexTurnTransport(backend)
    with patch("theory_master.codex_turn_transport.map_notification", side_effect=mapped):
        assert transport.run("investigator", "setup", "t", "m", "p", usage_guard=calls.append) == ("t","ready")
    assert calls == [10,30]
    assert transport.last_usage_tokens == 30
