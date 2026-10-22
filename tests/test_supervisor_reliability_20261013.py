"""Offline Supervisor runtime-versus-science incident regressions (no model calls)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from theory_master.supervisor_incident import incident_context, NativeGoalRuntimeFailure
from theory_master.supervisor import _verified_model_activity
from theory_master.supervisor_intervention import append_supervisor_verification
from theory_master.observability.email_notify import _attention_copy
from theory_master.native_goal import NativeGoalIncomplete, NativeGoalResult
from test_provider_capacity_reconnect_20261012 import backend_fixture


class SupervisorReliabilityTests(unittest.TestCase):
    def test_incident_is_scoped_to_last_scientific_invocation_not_stale_error(self):
        with tempfile.TemporaryDirectory() as td:
            cycle = Path(td)
            events = [
                {"type": "role.started", "at": "2026-10-13T12:35:33+08:00",
                 "payload": {"role": "investigator", "invocation_id": "inv-first"}},
                {"type": "role.started", "at": "2026-10-13T13:06:34+08:00",
                 "payload": {"role": "investigator", "invocation_id": "inv-resumed"}},
            ]
            (cycle/"events.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in events), encoding="utf-8")
            stream = [
                {"ts": "2026-10-13T12:40:07+08:00", "role": "investigator",
                 "thread_id": "t", "event": "LLM_FAILED",
                 "error": "stream disconnected before completion: password=hidden"},
                {"ts": "2026-10-13T13:08:00+08:00", "role": "investigator",
                 "thread_id": "t", "event": "TOOL_ACTIVITY"},
                {"ts": "2026-10-13T13:20:33+08:00", "role": "investigator",
                 "thread_id": "t", "event": "LLM_MESSAGE", "content": "scientific blocked"},
                {"ts": "2026-10-13T13:20:34+08:00", "role": "investigator",
                 "thread_id": "t", "event": "LLM_TURN_COMPLETED"},
            ]
            (cycle/"runtime_events.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in stream), encoding="utf-8")
            state = {"status": "needs_attention", "phase": "investigator:goal_suspended",
                     "attention": {"code": "investigator_native_goal_blocked", "thread_id": "t"},
                     "native_goal": {"status": "blocked", "thread_id": "t"},
                     "next_executor": "investigator", "active_role": "challenger"}
            evidence = incident_context(cycle, state)
            self.assertEqual(evidence["invocation_id"], "inv-resumed")
            self.assertEqual(evidence["role"], "investigator")
            self.assertEqual(evidence["last_terminal_error"], None)
            self.assertEqual(evidence["reconnection_events_in_retained_tail"], 0)
            self.assertEqual(evidence["recent_events"][-1]["event"], "LLM_TURN_COMPLETED")
            # Switch to first attempt; the stale password must never leak.
            events.pop()
            (cycle/"events.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in events), encoding="utf-8")
            first = incident_context(cycle, state)
            self.assertEqual(first["last_terminal_error"]["event"], "LLM_FAILED")
            self.assertIn("password=[redacted]", first["last_terminal_error"]["error"])
            self.assertNotIn("hidden", first["last_terminal_error"]["error"])

    def test_non_capacity_provider_failure_routes_runtime_without_native_auto_retry(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            def fail(_thread, _objective, *, on_event):
                on_event(SimpleNamespace(method="error", payload=None))
                return NativeGoalResult("blocked", thread.id, 1, 0)
            with patch("theory_master.native_goal.run_native_goal", side_effect=fail) as runner, \
                 patch("theory_master.observability.codex_events.map_notification",
                       return_value={"event": "LLM_FAILED", "error": "stream disconnected before completion"}), \
                 patch("time.sleep") as sleeper:
                with self.assertRaises(NativeGoalRuntimeFailure) as exc:
                    backend.invoke_goal(
                        "investigator", goal_objective="frozen goal", context_prompt="",
                        handoff_path=str(Path(td)/"handoff.json"), thread_id=thread.id,
                        model="gpt-6-sol", provider="CodexHTTPS")
            self.assertEqual(exc.exception.thread_id, thread.id)
            self.assertEqual(exc.exception.provider_event, "LLM_FAILED")
            self.assertIn("stream disconnected", backend.last_native_goal["provider_failure"])
            runner.assert_called_once()
            sleeper.assert_not_called()
            self.assertEqual(cleared, [])

    def test_premature_native_block_recovers_same_scientific_thread(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            handoff = Path(td) / "handoff.json"
            objectives = []
            def goal(_thread, objective, *, on_event):
                objectives.append(objective)
                if len(objectives) == 1:
                    return NativeGoalResult("blocked", thread.id, 1, 0)
                handoff.write_text(json.dumps({"findings": [{"claim": "Checked evidence."}]}))
                return NativeGoalResult("complete", thread.id, 1, 0)
            with patch("theory_master.native_goal.run_native_goal", side_effect=goal):
                identifier, raw = backend.invoke_goal(
                    "investigator", goal_objective="frozen goal", context_prompt="",
                    handoff_path=str(handoff), thread_id=thread.id,
                    model="gpt-6-sol", provider="CodexHTTPS")
            self.assertEqual(identifier, thread.id)
            self.assertEqual(objectives, ["frozen goal", "frozen goal"])
            self.assertEqual(cleared, [thread.id, thread.id])
            self.assertEqual(json.loads(raw)["findings"][0]["statement"], "Checked evidence.")

    def test_repeated_self_block_is_runtime_fault_not_scientific_exit(self):
        with tempfile.TemporaryDirectory() as td:
            backend, thread, cleared = backend_fixture()
            events = [
                {"event": "LLM_FAILED", "error": "stream disconnected before completion"},
                {"event": "LLM_MESSAGE", "content": "scientific analysis resumed"},
            ]
            calls = []
            def block(_thread, objective, *, on_event):
                calls.append(objective)
                if len(calls) == 1:
                    for event in events:
                        on_event(SimpleNamespace(method="any", payload=event))
                return NativeGoalResult("blocked", thread.id, 1, 1)
            with patch("theory_master.native_goal.run_native_goal", side_effect=block), \
                 patch("theory_master.observability.codex_events.map_notification",
                       side_effect=lambda event, **_: event.payload):
                with self.assertRaises(NativeGoalRuntimeFailure) as exc:
                    backend.invoke_goal("investigator", goal_objective="frozen goal",
                        context_prompt="", handoff_path=str(Path(td)/"handoff.json"),
                        thread_id=thread.id, model="gpt-6-sol", provider="CodexHTTPS")
            self.assertIn("unauthorized blocked", str(exc.exception))
            self.assertNotIn("provider event LLM_FAILED", str(exc.exception))
            self.assertEqual(exc.exception.provider_event, "")
            self.assertEqual(calls, ["frozen goal"] * 3)
            self.assertEqual(cleared, [thread.id] * 2)
            self.assertEqual(backend.last_native_goal["unexpected_block_retries"], 2)
            self.assertEqual(backend.last_native_goal["runtime_failure_kind"], "repeated_unexpected_block")

    def test_runtime_verification_needs_new_model_activity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root/"runtime_events.jsonl"
            at = "2026-10-13T13:06:34+08:00"
            path.write_text(
                json.dumps({"event": "LLM_MESSAGE", "ts": "2026-10-13T12:40:00+08:00"}) + "\n" +
                json.dumps({"event": "LLM_RECONNECTING", "ts": "2026-10-13T13:07:00+08:00"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(_verified_model_activity(root, at), "")
            with path.open("a") as handle:
                handle.write(json.dumps({"event": "TOOL_ACTIVITY", "thread_id": "t",
                                          "ts": "2026-10-13T13:08:00+08:00"}) + "\n")
            self.assertIn("TOOL_ACTIVITY", _verified_model_activity(root, at))
            (root/"supervisor").mkdir()
            (root/"supervisor"/"session.jsonl").write_text(json.dumps({
                "schema": "theory-master/supervisor-session/v1", "seq": 1,
                "session_id": "SUP-1111111111111111", "profile": "runtime", "kind": "decision",
            }) + "\n", encoding="utf-8")
            append_supervisor_verification(root, session_id="SUP-1111111111111111",
                                           verified=True, detail="TOOL_ACTIVITY")
            rows=[json.loads(line) for line in (root/"supervisor"/"session.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["kind"], "verification")
            self.assertEqual(rows[-1]["status"], "verified")
            self.assertEqual(rows[-1]["session_id"], "SUP-1111111111111111")

    def test_runtime_resume_fails_closed_on_bad_checkpoint_protocol_budget(self):
        from theory_master.runtime_supervisor_review import resume_blocker
        safe = {"status": "failed", "resumable": True,
                "role_outputs": {"research_lead": {"committed": True}}}
        self.assertIsNone(resume_blocker(safe))
        self.assertIn("No existing safe checkpoint", resume_blocker({**safe, "role_outputs": {}}))
        self.assertIn("not resumable", resume_blocker({**safe, "resumable": False}))
        self.assertIn("Protocol/contract", resume_blocker({**safe, "protocol_health": "violation"}))
        self.assertIn("budget guard", resume_blocker({**safe,
            "investigator_budget": {"exhausted": True}}))
        self.assertIn("Provider configuration", resume_blocker({**safe,
            "recovery": {"message": "Selected model is not supported"}}))
        from theory_master.supervisor_intervention import MAX_RUNTIME_RESUMES
        self.assertIn("authorized Supervisor", resume_blocker({**safe,
            "supervisor_runtime_resume_count": MAX_RUNTIME_RESUMES}))

    def test_resume_clears_only_stale_attention_review_not_scientific_lock(self):
        from test_cycle_anomalies import _workflow
        from theory_master.backends import FakeBackend
        from theory_master.workflow_support import RecoveryStop
        with tempfile.TemporaryDirectory() as td:
            wf, state = _workflow(Path(td), FakeBackend(reworks=0))
            wf._ensure_cycle()
            state.update({
                "status": "needs_attention", "resumable": True, "cycle_id": wf.cycle_dir.name,
                "cycle_dir": str(wf.cycle_dir), "hypothesis_lock": {"status": "locked",
                    "id": "H-0009", "decisive_test": "frozen"},
                "internal_attention_review": {"action": "hold", "reason": "previous"},
                "next_executor": "investigator",
            })
            original_lock = dict(state["hypothesis_lock"])
            with patch.object(wf, "_restore_locked_cycle",
                              side_effect=RecoveryStop("test-safe-stop")):
                result = wf._execute_cycle(resume_state=state)
            self.assertNotIn("internal_attention_review", result)
            self.assertEqual(result["hypothesis_lock"], original_lock)

    def test_generic_attention_does_not_claim_repairs_or_success(self):
        state = {"status": "needs_attention", "resumable": True}
        subject, what, reason, next_action = _attention_copy(
            state, {"attention_code": "investigator_native_goal_blocked",
                    "reason": "missing permissible joint prior"},
            {"code": "investigator_native_goal_blocked"})
        self.assertIn("需要人工处理", subject)
        self.assertIn("不表示已经自动检修或重试", what)
        self.assertIn("missing permissible joint prior", reason)
        self.assertIn("tm resume", next_action)


if __name__ == "__main__":
    unittest.main()
