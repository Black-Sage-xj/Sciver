"""Bounded native Goal thread-busy recovery after proven model capacity failure.

All calls are fake backend objects and a fake clock; no real Runner/provider.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_provider_capacity_reconnect_20261012 import FakeClock, backend_fixture, CAPACITY
from theory_master.native_goal import NativeGoalResult
from theory_master.provider_capacity import is_native_goal_thread_busy, CapacityRecoveryTimeout

THREAD = "scientific-thread"
BUSY = f"JSON-RPC error -32600: thread must be idle before starting a goal: {THREAD}"


class ThreadIdleRecoveryTests(unittest.TestCase):
    def test_only_exact_thread_busy_qualifies(self):
        self.assertTrue(is_native_goal_thread_busy(BUSY, THREAD))
        for error, thread in [
            (BUSY, "different-scientific-thread"),
            ("JSON-RPC error -32600: thread must be idle before starting a goal: other", THREAD),
            ("JSON-RPC error -32600: thread must be idle before starting a turn: scientific-thread", THREAD),
            ("thread must be idle before starting a goal: scientific-thread", THREAD),
            ("authentication failed", THREAD),
        ]:
            with self.subTest(error=error):
                self.assertFalse(is_native_goal_thread_busy(error, thread))

    def test_capacity_then_three_thread_busy_rejections_reuse_same_goal_and_thread(self):
        clock=FakeClock()
        backend,thread,cleared=backend_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            handoff=Path(tmp)/".tm-handoff"/"submission.json"
            handoff.parent.mkdir()
            calls=[]
            def run(existing, objective, *, on_event):
                calls.append((existing.id,objective))
                if len(calls)==1:
                    on_event(SimpleNamespace(method="error"))
                    return NativeGoalResult("blocked",existing.id,1,0)
                if len(calls)<=4:
                    raise RuntimeError(BUSY)
                handoff.write_text('{"findings":[{"claim":"continued result"}]}')
                return NativeGoalResult("complete",existing.id,1,1)
            def mapped(event,**kwargs):
                return {"event":"LLM_FAILED","error":CAPACITY}
            with patch("theory_master.native_goal.run_native_goal",side_effect=run), \
                 patch("theory_master.observability.codex_events.map_notification",side_effect=mapped), \
                 patch("theory_master.provider_capacity.time.time",side_effect=clock.time), \
                 patch("time.sleep",side_effect=clock.sleep):
                identifier,result=backend.invoke_goal("investigator",
                    goal_objective="FROZEN SCIENTIFIC OBJECTIVE",
                    context_prompt="",handoff_path=str(handoff),
                    thread_id=THREAD,model="gpt-6-sol",provider="CodexHTTPS")
            self.assertEqual(identifier,THREAD)
            self.assertEqual(len(calls),5)
            self.assertEqual(set(calls),{(THREAD,"FROZEN SCIENTIFIC OBJECTIVE")})
            self.assertEqual(json.loads(result),{"findings":[{"statement":"continued result"}]})
            self.assertEqual(clock.delays,[5.0,10.0,20.0,40.0])
            self.assertEqual(cleared,[THREAD,THREAD])
            self.assertFalse(list(handoff.parent.glob("capacity-retry-*.json")))
            names=[x.args[0].get("event") for x in backend.collector.on_codex.call_args_list]
            self.assertEqual(names.count("CAPACITY_THREAD_BUSY_WAIT"),3)
            self.assertEqual(names.count("CAPACITY_RETRY_EXHAUSTED"),0)

    def test_unrelated_busy_cannot_repeat_goal_or_wait(self):
        backend,thread,cleared=backend_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("theory_master.native_goal.run_native_goal",
                       side_effect=RuntimeError(BUSY)) as runner, \
                 patch("time.sleep") as sleeper:
                with self.assertRaisesRegex(RuntimeError,"thread must be idle"):
                    backend.invoke_goal("investigator",goal_objective="frozen",
                        context_prompt="",handoff_path=str(Path(tmp)/"submission.json"),
                        thread_id=THREAD,model="gpt-6-sol",provider="CodexHTTPS")
                runner.assert_called_once()
                sleeper.assert_not_called()
            self.assertEqual(cleared,[])

    def test_persistently_busy_escalates_as_runtime_failure_after_bounded_window(self):
        clock=FakeClock()
        backend,thread,cleared=backend_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            handoff=Path(tmp)/".tm-handoff"/"one.json"
            attempts=[]
            def run(existing,objective,*,on_event):
                attempts.append(objective)
                if len(attempts)==1:
                    on_event(SimpleNamespace(method="error"))
                    return NativeGoalResult("blocked",existing.id,1,0)
                raise RuntimeError(BUSY)
            with patch("theory_master.native_goal.run_native_goal",side_effect=run), \
                 patch("theory_master.observability.codex_events.map_notification",
                       return_value={"event":"LLM_FAILED","error":CAPACITY}), \
                 patch("theory_master.provider_capacity.time.time",side_effect=clock.time), \
                 patch("time.sleep",side_effect=clock.sleep):
                with self.assertRaises(CapacityRecoveryTimeout) as caught:
                    backend.invoke_goal("investigator",goal_objective="frozen",
                        context_prompt="",handoff_path=str(handoff),
                        thread_id=THREAD,model="gpt-6-sol",provider="CodexHTTPS")
            self.assertIn("Provider capacity recovery timed out",str(caught.exception))
            self.assertGreater(len(attempts),5)
            self.assertLessEqual(sum(clock.delays),1800)
            self.assertGreaterEqual(sum(clock.delays),1740)
            self.assertEqual(cleared,[THREAD])
            self.assertEqual(backend.last_native_goal["capacity_reconnect"]["status"],"timeout")
            self.assertGreater(backend.last_native_goal["capacity_reconnect"]["busy_retries"],5)
            self.assertFalse(list(handoff.parent.glob("capacity-retry-*.json")))
            events=[x.args[0]["event"] for x in backend.collector.on_codex.call_args_list]
            self.assertEqual(events.count("CAPACITY_RETRY_EXHAUSTED"),1)


if __name__=="__main__":
    unittest.main()
