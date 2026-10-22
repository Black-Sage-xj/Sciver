"""Provider-capacity terminal-turn cleanup: offline, no live model calls."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.provider_capacity import settle_failed_capacity_turn
from theory_master.native_goal import NativeGoalResult
from test_provider_capacity_reconnect_20261012 import (
    FakeClock, backend_fixture, CAPACITY,
)


class FakeClient:
    def __init__(self, thread_id="scientific-thread", active_id="turn-failed"):
        self.thread_id = thread_id
        self.active_id = active_id
        self.actions = []
        self.status = "active"

    def thread_read(self, thread_id, *, include_turns=False):
        assert include_turns and thread_id == self.thread_id
        return SimpleNamespace(thread=SimpleNamespace(
            id=self.thread_id, status=SimpleNamespace(
                root=SimpleNamespace(type=self.status)),
            turns=[SimpleNamespace(id=self.active_id)],
        ))

    def pause_goal(self, thread_id):
        self.actions.append(("pause", thread_id))

    def turn_interrupt(self, thread_id, turn_id):
        self.actions.append(("interrupt", thread_id, turn_id))
        self.status = "idle"

    def thread_goal_clear(self, thread_id):
        self.actions.append(("clear", thread_id))


class TerminalCapacityCleanup(unittest.TestCase):
    def test_exact_failed_turn_is_settled(self):
        client = FakeClient()
        thread = SimpleNamespace(id=client.thread_id, _client=client)
        self.assertTrue(settle_failed_capacity_turn(thread, "turn-failed"))
        self.assertEqual(client.actions, [
            ("pause", client.thread_id),
            ("interrupt", client.thread_id, "turn-failed"),
        ])
        self.assertEqual(client.status, "idle")

    def test_never_interrupt_another_turn_or_idle_thread(self):
        for active_id, status, supplied in (
            ("turn-other", "active", "turn-failed"),
            ("turn-failed", "idle", "turn-failed"),
            ("turn-failed", "active", ""),
        ):
            with self.subTest(active_id=active_id, status=status, supplied=supplied):
                client = FakeClient(active_id=active_id)
                client.status = status
                thread = SimpleNamespace(id=client.thread_id, _client=client)
                self.assertFalse(settle_failed_capacity_turn(thread, supplied))
                self.assertEqual(client.actions, [])

    def test_native_goal_retries_after_exact_terminal_turn_settlement(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            client = FakeClient()
            thread._client = client
            clock = FakeClock()
            calls = []

            def run(_thread, objective, *, on_event):
                calls.append(objective)
                if len(calls) == 1:
                    on_event(object())
                    return NativeGoalResult("blocked", thread.id, 1, 0)
                handoff.write_text('{"findings":[{"claim":"evidence"}]}')
                return NativeGoalResult("complete", thread.id, 1, 0)

            def map_event(event, **kwargs):
                return {"event": "LLM_FAILED", "error": CAPACITY,
                        "turn_id": "turn-failed"}

            handoff = Path(td) / "handoff.json"
            with patch("theory_master.native_goal.run_native_goal", side_effect=run), \
                 patch("theory_master.observability.codex_events.map_notification", side_effect=map_event), \
                 patch("theory_master.provider_capacity.time.time", side_effect=clock.time), \
                 patch("time.sleep", side_effect=clock.sleep):
                _, _result = backend.invoke_goal(
                    "investigator", goal_objective="frozen", context_prompt="",
                    handoff_path=str(handoff), thread_id=thread.id,
                    model="gpt-6-sol", provider="CodexHTTPS")
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.actions[:2],
                [("pause", thread.id), ("interrupt", thread.id, "turn-failed")])
            self.assertEqual([a[0] for a in client.actions].count("interrupt"), 1)
            self.assertEqual([a[0] for a in client.actions].count("clear"), 2)


if __name__ == "__main__":
    unittest.main()
