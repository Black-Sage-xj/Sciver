"""No-provider tests for capped native Goal re-entry after confirmed model capacity."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.codex_backend import CodexBackend
from theory_master.native_goal import NativeGoalIncomplete, NativeGoalResult
from theory_master.provider_errors import ProviderQuotaExhausted
from theory_master.provider_capacity import (
    CapacityReconnectWindow, CAPACITY_WAIT_LIMIT_SECONDS, is_model_at_capacity,
)


CAPACITY = "Selected model is at capacity. Please try a different model."


class FakeClock:
    def __init__(self, start=1800000000.0):
        self.t = start
        self.delays = []

    def time(self):
        return self.t

    def sleep(self, value):
        self.delays.append(value)
        self.t += value


def backend_fixture():
    cleared = []
    client = SimpleNamespace(thread_goal_clear=lambda tid: cleared.append(tid))
    thread = SimpleNamespace(id="scientific-thread", _client=client)
    backend = CodexBackend.__new__(CodexBackend)
    backend.threads = {thread.id: thread}
    backend._new_thread_ids = set()
    backend.last_native_goal = {}
    backend.ensure_thread = MagicMock(return_value=thread.id)
    backend._logger = MagicMock()
    backend.collector = SimpleNamespace(on_codex=MagicMock(), record_native_goal_usage=MagicMock())
    return backend, thread, cleared


class ModelCapacityReconnectTests(unittest.TestCase):
    def test_classifier_is_strict_and_does_not_retry_quota_or_other_blocks(self):
        self.assertTrue(is_model_at_capacity(CAPACITY))
        self.assertTrue(is_model_at_capacity("model_at_capacity"))
        for error in ("You’ve hit your usage limit", "model is not supported",
                      "the network is at capacity for every physical edge",
                      "blocked", "429 quota exhausted", "Timed out connecting"):
            with self.subTest(error=error):
                self.assertFalse(is_model_at_capacity(error))

    def test_same_thread_exact_goal_backoff_and_success(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock()
            handoff = Path(td)/".tm-handoff"/"submission.json"
            backend, thread, cleared = backend_fixture()
            goals = []
            def run(_thread, objective, *, on_event):
                goals.append((_thread.id, objective))
                if len(goals) <= 2:
                    on_event(SimpleNamespace(method="error", payload=None))
                    return NativeGoalResult("blocked", _thread.id, 1, 0)
                handoff.write_text('{"findings":[{"claim":"scientific result"}]}')
                return NativeGoalResult("complete", _thread.id, 1, 1)
            def mapped(e, **_):
                return {"event":"LLM_FAILED", "error":CAPACITY}
            with patch("theory_master.native_goal.run_native_goal", side_effect=run),                  patch("theory_master.observability.codex_events.map_notification", side_effect=mapped),                  patch("theory_master.provider_capacity.time.time", side_effect=clock.time),                  patch("time.sleep", side_effect=clock.sleep):
                ident, response = backend.invoke_goal(
                    "investigator", goal_objective="UNCHANGED FROZEN GOAL",
                    context_prompt="", handoff_path=str(handoff),
                    thread_id=thread.id, model="gpt-6-sol",
                    provider="CodexHTTPS",
                )
            self.assertEqual(ident, thread.id)
            self.assertEqual(json.loads(response), {"findings":[{"statement":"scientific result"}]})
            self.assertEqual(goals, [(thread.id, "UNCHANGED FROZEN GOAL")]*3)
            self.assertEqual(cleared, [thread.id]*3)
            self.assertEqual(clock.delays, [5.0, 10.0])
            self.assertFalse(list(handoff.parent.glob("capacity-retry-*.json")))
            events = [call.args[0].get("event") for call in backend.collector.on_codex.call_args_list]
            self.assertEqual(events.count("CAPACITY_RETRY_WAIT"), 2)

    def test_30_min_max_and_need_attention_detail(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock()
            backend, thread, cleared = backend_fixture()
            calls = []
            def blocked(_thread, objective, *, on_event):
                calls.append(objective)
                on_event(SimpleNamespace(method="error", payload=None))
                return NativeGoalResult("blocked", thread.id, 1, 0)
            with patch("theory_master.native_goal.run_native_goal", side_effect=blocked),                  patch("theory_master.observability.codex_events.map_notification",
                       return_value={"event":"LLM_FAILED", "error":CAPACITY}),                  patch("theory_master.provider_capacity.time.time", side_effect=clock.time),                  patch("time.sleep", side_effect=clock.sleep):
                from theory_master.provider_capacity import CapacityRecoveryTimeout
                with self.assertRaises(CapacityRecoveryTimeout) as error:
                    backend.invoke_goal(
                        "investigator", goal_objective="frozen goal",
                        context_prompt="", handoff_path=str(Path(td)/"receipt.json"),
                        thread_id=thread.id, model="gpt-6-sol", provider="CodexHTTPS",
                    )
            self.assertIn("Provider capacity recovery timed out", str(error.exception))
            self.assertLessEqual(sum(clock.delays), CAPACITY_WAIT_LIMIT_SECONDS)
            self.assertGreaterEqual(sum(clock.delays), CAPACITY_WAIT_LIMIT_SECONDS-60)
            info = backend.last_native_goal["capacity_reconnect"]
            self.assertEqual(info["status"], "timeout")
            self.assertEqual(info["limit_seconds"], 1800)
            self.assertIn("at capacity", info["error"])
            self.assertGreater(len(calls), 3)
            self.assertFalse(list(Path(td).glob("capacity-retry-*.json")))

    def test_explicit_native_goal_quota_exception_routes_to_quota_pause(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            backend.thread_accounts = {thread.id: "2"}
            message = "You’ve hit your usage limit. Try again after the quota resets."
            with patch("theory_master.native_goal.run_native_goal", side_effect=RuntimeError(message)), \
                 patch("time.sleep") as sleep:
                with self.assertRaises(ProviderQuotaExhausted) as caught:
                    backend.invoke_goal(
                        "investigator", goal_objective="frozen goal",
                        context_prompt="", handoff_path=str(Path(td)/"receipt.json"),
                        thread_id=thread.id, model="gpt-6-sol", provider="CodexHTTPS",
                    )
            self.assertEqual(caught.exception.provider, "CodexHTTPS")
            self.assertEqual(caught.exception.account, "2")
            self.assertEqual(backend.last_native_goal["status"], "usageLimited")
            self.assertEqual(backend.last_native_goal["runtime_failure_kind"], "provider_quota_exhausted")
            self.assertEqual(cleared, [])
            sleep.assert_not_called()

    def test_quota_scientific_and_unsupported_errors_do_not_retry(self):
        for status, message in (
            ("usageLimited", "You’ve hit your usage limit"),
            ("blocked", "Selected model is not supported"),
            ("blocked", "Scientific conditions not satisfied"),
        ):
            with self.subTest(status=status, message=message), tempfile.TemporaryDirectory() as td:
                backend, thread, cleared = backend_fixture()
                def return_goal(_thread, _objective, *, on_event):
                    on_event(SimpleNamespace(method="error", payload=None))
                    return NativeGoalResult(status,thread.id,1,0)
                with patch("theory_master.native_goal.run_native_goal", side_effect=return_goal) as runner,                      patch("theory_master.observability.codex_events.map_notification",
                           return_value={"event":"LLM_FAILED","error":message}),                      patch("time.sleep") as sleep:
                    from theory_master.supervisor_incident import NativeGoalRuntimeFailure
                    failure_type = (NativeGoalRuntimeFailure if status == "blocked"
                                    else NativeGoalIncomplete)
                    with self.assertRaises(failure_type) as error:
                        backend.invoke_goal(
                            "investigator", goal_objective="frozen goal",
                            context_prompt="", handoff_path=str(Path(td)/"receipt.json"),
                            thread_id=thread.id, model="gpt-6-sol", provider="CodexHTTPS",
                        )
                if status == "blocked":
                    self.assertIn(message, str(error.exception))
                else:
                    self.assertEqual(error.exception.status, status)
                # Noncapacity provider failures move to Supervisor's separate
                # Runtime assessment; never spin the native Goal retry loop.
                runner.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(cleared, [])

    def test_capacity_warning_then_new_science_treats_block_as_runtime_fault(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            def science_after_old_warning(_thread, objective, *, on_event):
                on_event(SimpleNamespace(method="error", payload=None))
                on_event(SimpleNamespace(method="item/completed", payload=None))
                return NativeGoalResult("blocked",thread.id,1,1)
            def mapping(e, **_):
                if e.method == "error":
                    return {"event":"LLM_FAILED","error":CAPACITY}
                return {"event":"TOOL_ACTIVITY", "detail":"actual new science"}
            with patch("theory_master.native_goal.run_native_goal",side_effect=science_after_old_warning) as runner, \
                 patch("theory_master.observability.codex_events.map_notification",side_effect=mapping), \
                 patch("time.sleep") as sleeper:
                from theory_master.supervisor_incident import NativeGoalRuntimeFailure
                with self.assertRaises(NativeGoalRuntimeFailure):
                    backend.invoke_goal(
                        "investigator", goal_objective="frozen",
                        context_prompt="",handoff_path=str(Path(td)/"item.json"),
                        thread_id=thread.id,model="gpt-6-sol",provider="CodexHTTPS",
                    )
            self.assertEqual(runner.call_count,3)
            sleeper.assert_not_called()
            self.assertEqual(cleared,[thread.id,thread.id])

    def test_persisted_elapsed_window_expires_before_new_provider_call(self):
        with tempfile.TemporaryDirectory() as td:
            clock=FakeClock()
            handoff=Path(td)/"handoff.json"
            with patch("theory_master.provider_capacity.time.time",side_effect=clock.time):
                window=CapacityReconnectWindow(handoff,thread_id="scientific-thread",
                                               model="gpt-6-sol",provider="CodexHTTPS")
                window.record_failure(CAPACITY)
                clock.t += 1801
                backend, thread, cleared = backend_fixture()
                thread.run = MagicMock()
                backend._new_thread_ids.add(thread.id)
                with patch("theory_master.native_goal.run_native_goal") as runner:
                    from theory_master.provider_capacity import CapacityRecoveryTimeout
                    with self.assertRaises(CapacityRecoveryTimeout):
                        backend.invoke_goal(
                            "investigator",goal_objective="frozen",
                            context_prompt="",handoff_path=str(handoff),
                            thread_id=thread.id,model="gpt-6-sol",provider="CodexHTTPS",
                        )
                    runner.assert_not_called()
                thread.run.assert_not_called()
                self.assertEqual(backend.last_native_goal["capacity_reconnect"]["status"],"timeout")
                self.assertFalse(window.path.exists())

    def test_persisted_retry_window_survives_runner_restart(self):
        with tempfile.TemporaryDirectory() as td:
            clock = FakeClock()
            handoff = Path(td)/".tm-handoff"/"one.json"
            with patch("theory_master.provider_capacity.time.time", side_effect=clock.time):
                first = CapacityReconnectWindow(handoff,thread_id="id",model="gpt-6-sol",provider="CodexHTTPS")
                a=first.record_failure(CAPACITY)
                clock.t += 1450
                other = CapacityReconnectWindow(handoff,thread_id="id",model="gpt-6-sol",provider="CodexHTTPS")
                b=other.record_failure(CAPACITY)
                self.assertEqual(b["first_failure_epoch"],a["first_failure_epoch"])
                self.assertAlmostEqual(other.remaining(b),350)
                newer = CapacityReconnectWindow(handoff,thread_id="id",model="gpt-6.1-sol",provider="CodexHTTPS")
                self.assertIsNone(newer.snapshot())


if __name__ == "__main__":
    unittest.main()
