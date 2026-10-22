from __future__ import annotations

import unittest
import time
from pathlib import Path
from types import SimpleNamespace

from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    Turn,
    TurnCompletedNotification,
    TurnStatus,
)
from theory_master.backends import CodexBackend
from theory_master.codex_backend import ToolApprovalUnavailable


class _Handle:
    def stream(self):
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response="{}")),
        )


class _TerminalBeforeItemHandle:
    def stream(self):
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response=None)),
        )
        yield SimpleNamespace(
            method="item/completed",
            payload=SimpleNamespace(
                item=SimpleNamespace(root=SimpleNamespace(type="agentMessage", text="{}"))
            ),
        )


class _CompletedWithoutTextHandle:
    def stream(self):
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response=None)),
        )


class _DeltaOnlyHandle:
    def stream(self):
        yield SimpleNamespace(
            method="item/agentMessage/delta",
            payload=SimpleNamespace(delta='{"candidates": '),
        )
        yield SimpleNamespace(
            method="item/agentMessage/delta",
            payload=SimpleNamespace(delta="[]}"),
        )
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response=None)),
        )


class _Thread:
    def turn(self, _payload):
        return _Handle()


class _ClosedTransportThread:
    def turn(self, _payload):
        raise RuntimeError("Codex process is not running")


class _TerminalBeforeItemThread:
    def turn(self, _payload):
        return _TerminalBeforeItemHandle()


class _CompletedWithoutTextThread:
    def turn(self, _payload):
        return _CompletedWithoutTextHandle()


class _DeltaOnlyThread:
    def turn(self, _payload):
        return _DeltaOnlyHandle()


class _HistoryFallbackThread:
    def turn(self, _payload):
        return _CompletedWithoutTextHandle()

    def read(self, *, include_turns=False):
        assert include_turns is True
        return SimpleNamespace(thread=SimpleNamespace(turns=[SimpleNamespace(
            id="turn-history",
            items=[SimpleNamespace(root=SimpleNamespace(type="agentMessage", text="{}"))],
        )]))


class _DelayedHistoryFallbackThread:
    def __init__(self):
        self.read_count = 0

    def turn(self, _payload):
        return _CompletedWithoutTextHandle()

    def read(self, *, include_turns=False):
        assert include_turns is True
        self.read_count += 1
        items = [] if self.read_count < 2 else [SimpleNamespace(
            root=SimpleNamespace(type="agentMessage", text="{}"),
        )]
        return SimpleNamespace(thread=SimpleNamespace(turns=[SimpleNamespace(
            id="turn-history-delayed",
            items=items,
        )]))


class _ResponseShapeHandle:
    def stream(self):
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(
                turn=SimpleNamespace(
                    final_response=None,
                    items=[SimpleNamespace(
                        type="agent_message",
                        content=[SimpleNamespace(type="input_text", text="{}")],
                    )],
                )
            ),
        )


class _ResponseShapeThread:
    def turn(self, _payload):
        return _ResponseShapeHandle()


class _UnknownItemShapeHandle:
    def stream(self):
        yield SimpleNamespace(
            method="item/completed",
            payload=SimpleNamespace(
                params=SimpleNamespace(
                    item=SimpleNamespace(
                        root=SimpleNamespace(
                            type="agent_message",
                            content=[SimpleNamespace(root=SimpleNamespace(text="{}"))],
                        )
                    )
                )
            ),
        )
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response=None)),
        )


class _UnknownItemShapeThread:
    def turn(self, _payload):
        return _UnknownItemShapeHandle()


class _OfficialSDKShapeHandle:
    def stream(self):
        item = AgentMessageThreadItem(id="item-1", type="agentMessage", text="{}")
        yield SimpleNamespace(
            method="item/completed",
            payload=ItemCompletedNotification(
                completedAtMs=1, item=item, threadId="thread-official", turnId="turn-official"
            ),
        )
        yield SimpleNamespace(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="thread-official",
                turn=Turn(id="turn-official", status=TurnStatus.completed, items=[item]),
            ),
        )


class _OfficialSDKShapeThread:
    def turn(self, _payload):
        return _OfficialSDKShapeHandle()


class _NestedTurnOutputHandle:
    def stream(self):
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(
                turn=SimpleNamespace(
                    final_response=None,
                    output=SimpleNamespace(
                        data=[SimpleNamespace(type="agentMessage", text="{}")]
                    ),
                )
            ),
        )


class _NestedTurnOutputThread:
    def turn(self, _payload):
        return _NestedTurnOutputHandle()


class _ApprovalUnavailableHandle:
    def stream(self):
        yield SimpleNamespace(
            method="item/autoApprovalReview/completed",
            payload=SimpleNamespace(
                review=SimpleNamespace(rationale="Automatic approval review failed: unexpected status 503 Service Unavailable: No available channel for model codex-auto-review"),
                action=SimpleNamespace(root=SimpleNamespace(server="theory_master_gateway", tool_name="exec")),
            ),
        )
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response='{"findings":[],"remaining_scientific_question":"unavailable"}')),
        )


class _ApprovalUnavailableThread:
    def turn(self, _payload):
        return _ApprovalUnavailableHandle()


class _WatchdogProgressHandle:
    def __init__(self, *, include_delta: bool = False):
        self.interrupted = False
        self.include_delta = include_delta

    def interrupt(self):
        self.interrupted = True

    def stream(self):
        if self.include_delta:
            yield SimpleNamespace(method="item/agentMessage/delta", payload=SimpleNamespace(delta="partial delta"))
        else:
            yield SimpleNamespace(
                method="item/completed",
                payload=SimpleNamespace(item=SimpleNamespace(root=SimpleNamespace(type="agentMessage", text="progress only"))),
            )
        time.sleep(0.03)
        yield SimpleNamespace(
            method="turn/completed",
            payload=SimpleNamespace(turn=SimpleNamespace(final_response=None)),
        )


class _WatchdogRetryThread:
    def __init__(self, *, include_delta: bool = False):
        self.calls = 0
        self.payloads = []
        self.include_delta = include_delta

    def turn(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            return _WatchdogProgressHandle(include_delta=self.include_delta)
        return _Handle()


class TurnCompletionTest(unittest.TestCase):
    def test_closed_codex_process_rebuild_invalidates_threads_for_all_roles(self):
        backend = CodexBackend(Path("/tmp"))
        backend.OUTER_TURN_RETRIES = 1
        backend.OUTER_RETRY_DELAYS = (0,)
        stale_lead_thread = _Thread()
        backend.threads.update({
            "thread-challenger": _ClosedTransportThread(),
            "thread-lead": stale_lead_thread,
        })

        class _Client:
            closed = False

            def close(self):
                self.closed = True

        old_client = _Client()
        backend.client = old_client
        fresh_threads = {}

        def ensure(role, thread_id, model, provider):
            if thread_id not in backend.threads:
                backend.threads[thread_id] = _Thread()
                fresh_threads[thread_id] = backend.threads[thread_id]
            return thread_id

        backend.ensure_thread = ensure
        identifier, response = backend.invoke(
            "challenger", "return JSON", "thread-challenger", "model", "provider"
        )

        self.assertEqual((identifier, response), ("thread-challenger", "{}"))
        self.assertTrue(old_client.closed)
        self.assertNotIn("thread-lead", backend.threads)
        self.assertIs(backend.threads["thread-challenger"], fresh_threads["thread-challenger"])

        _, lead_response = backend.invoke(
            "research_lead", "continue", "thread-lead", "model", "provider"
        )
        self.assertEqual(lead_response, "{}")
        self.assertIsNot(backend.threads["thread-lead"], stale_lead_thread)

    def test_auto_review_service_failure_stops_before_scientific_output(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-approval"] = _ApprovalUnavailableThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-approval"

        with self.assertRaisesRegex(ToolApprovalUnavailable, "No available channel for model codex-auto-review"):
            backend.invoke("investigator", "return JSON", "thread-approval", "model", "provider")

    def test_terminal_event_does_not_reach_watchdog_retry(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-1"] = _Thread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-1"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-1", "model", "provider"
        )

        self.assertEqual(identifier, "thread-1")
        self.assertEqual(response, "{}")

    def test_watchdog_timeout_dominates_progress_text_and_interrupt_completion(self):
        backend = CodexBackend(Path("/tmp"))
        thread = _WatchdogRetryThread()
        backend.OUTER_TURN_RETRIES = 0
        backend._watchdog_seconds = lambda: 0.01
        backend.threads["thread-watchdog"] = thread
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-watchdog"

        with self.assertRaisesRegex(TimeoutError, "inactivity watchdog"):
            backend.invoke("investigator", "return JSON", "thread-watchdog", "model", "provider")
        self.assertEqual(thread.calls, 1)

    def test_watchdog_timeout_dominates_partial_delta_and_retries_with_continuation(self):
        backend = CodexBackend(Path("/tmp"))
        thread = _WatchdogRetryThread(include_delta=True)
        backend.OUTER_TURN_RETRIES = 1
        backend.OUTER_RETRY_DELAYS = (0,)
        backend._watchdog_seconds = lambda: 0.01

        def ensure(role, thread_id, model, provider):
            backend.threads["thread-watchdog-retry"] = thread
            return "thread-watchdog-retry"

        backend.ensure_thread = ensure
        identifier, response = backend.invoke(
            "investigator", "ORIGINAL FULL TASK", "thread-watchdog-retry", "model", "provider"
        )

        self.assertEqual(identifier, "thread-watchdog-retry")
        self.assertEqual(response, "{}")
        self.assertEqual(thread.calls, 2)
        first_payload = repr(thread.payloads[0])
        retry_payload = repr(thread.payloads[1])
        self.assertIn("ORIGINAL FULL TASK", first_payload)
        self.assertNotEqual(first_payload, retry_payload)
        self.assertIn("inactivity watchdog", retry_payload)
        self.assertIn("Continue the same assigned task", retry_payload)

    def test_terminal_event_is_not_reported_as_watchdog_timeout_without_text(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-2"] = _CompletedWithoutTextThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-2"

        backend.OUTER_TURN_RETRIES = 0
        with self.assertRaisesRegex(RuntimeError, "backend delivery error: turn completed without assistant final response"):
            backend.invoke("research_lead", "return JSON", "thread-2", "model", "provider")

    def test_budget_reviewer_empty_final_continues_same_thread_once(self):
        backend = CodexBackend(Path("/tmp"))
        backend.OUTER_TURN_RETRIES = 3
        backend.OUTER_RETRY_DELAYS = (0,)
        class _BudgetThread:
            def __init__(self):
                self.payloads = []
            def turn(self, payload):
                self.payloads.append(payload)
                return _CompletedWithoutTextHandle() if len(self.payloads) == 1 else _Handle()
        thread = _BudgetThread()
        def ensure(role, thread_id, model, provider):
            backend.threads["review-id"] = thread
            return "review-id"
        backend.ensure_thread = ensure
        identifier, response = backend.invoke(
            "budget_reviewer", "MASSIVE ORIGINAL REVIEW PROMPT", None, "model", "provider"
        )
        self.assertEqual((identifier, response), ("review-id", "{}"))
        self.assertEqual(len(thread.payloads), 2)
        self.assertIn("MASSIVE ORIGINAL REVIEW PROMPT", repr(thread.payloads[0]))
        self.assertNotIn("MASSIVE ORIGINAL REVIEW PROMPT", repr(thread.payloads[1]))
        self.assertIn("Do not repeat any research or tools", repr(thread.payloads[1]))

    def test_budget_reviewer_double_empty_final_fails_closed(self):
        backend = CodexBackend(Path("/tmp"))
        backend.OUTER_TURN_RETRIES = 3
        backend.OUTER_RETRY_DELAYS = (0,)
        class _BudgetThread:
            def __init__(self):
                self.payloads = []
            def turn(self, payload):
                self.payloads.append(payload)
                return _CompletedWithoutTextHandle()
        thread = _BudgetThread()
        def ensure(role, thread_id, model, provider):
            backend.threads["review-id"] = thread
            return "review-id"
        backend.ensure_thread = ensure
        with self.assertRaisesRegex(RuntimeError, "completed without assistant final response"):
            backend.invoke("budget_reviewer", "MASSIVE ORIGINAL REVIEW PROMPT", None, "model", "provider")
        self.assertEqual(len(thread.payloads), 2)

    def test_terminal_event_uses_agent_message_deltas_when_item_is_missing(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-delta"] = _DeltaOnlyThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-delta"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-delta", "model", "provider"
        )

        self.assertEqual(identifier, "thread-delta")
        self.assertEqual(response, '{"candidates": []}')

    def test_terminal_event_recovers_response_from_thread_history(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-history"] = _HistoryFallbackThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-history"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-history", "model", "provider"
        )

        self.assertEqual(identifier, "thread-history")
        self.assertEqual(response, "{}")

    def test_item_after_terminal_event_supplies_final_response(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-3"] = _TerminalBeforeItemThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-3"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-3", "model", "provider"
        )

        self.assertEqual(identifier, "thread-3")
        self.assertEqual(response, "{}")

    def test_terminal_event_waits_for_delayed_thread_history(self):
        backend = CodexBackend(Path("/tmp"))
        thread = _DelayedHistoryFallbackThread()
        backend.threads["thread-delayed-history"] = thread
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-delayed-history"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-delayed-history", "model", "provider"
        )

        self.assertEqual(identifier, "thread-delayed-history")
        self.assertEqual(response, "{}")
        self.assertEqual(thread.read_count, 2)

    def test_response_shape_agent_message_in_completed_turn_supplies_final_response(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-4"] = _ResponseShapeThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-4"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-4", "model", "provider"
        )

        self.assertEqual(identifier, "thread-4")
        self.assertEqual(response, "{}")

    def test_unknown_item_notification_supplies_final_response(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-unknown-item"] = _UnknownItemShapeThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-unknown-item"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-unknown-item", "model", "provider"
        )

        self.assertEqual(identifier, "thread-unknown-item")
        self.assertEqual(response, "{}")

    def test_official_sdk_item_notification_supplies_final_response(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-official"] = _OfficialSDKShapeThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-official"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-official", "model", "provider"
        )

        self.assertEqual(identifier, "thread-official")
        self.assertEqual(response, "{}")

    def test_nested_turn_output_supplies_final_response(self):
        backend = CodexBackend(Path("/tmp"))
        backend.threads["thread-nested-output"] = _NestedTurnOutputThread()
        backend.ensure_thread = lambda role, thread_id, model, provider: "thread-nested-output"

        identifier, response = backend.invoke(
            "research_lead", "return JSON", "thread-nested-output", "model", "provider"
        )

        self.assertEqual(identifier, "thread-nested-output")
        self.assertEqual(response, "{}")


if __name__ == "__main__":
    unittest.main()
