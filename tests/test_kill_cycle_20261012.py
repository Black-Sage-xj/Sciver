"""Kill aborts a Cycle irreversibly; it is not the old resumable interrupt.

All fixtures are temporary; these tests NEVER control an actual Runner.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.core import Workflow
from theory_master.workflow_support import RecoveryStop
from theory_master.specification import make_run_id
import test_core


class KillCycleTests(unittest.TestCase):
    def fixture(self, root):
        spec, backend = test_core.TheoryMasterTest().make(root, reworks=0)
        workflow = Workflow(spec, backend)
        state = workflow.initialize()
        return workflow, spec, state, workflow.cycle_dir

    def test_running_kill_reuses_abandonment_and_requires_fresh_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, spec, state, cycle = self.fixture(Path(temp))
            before_canvas = spec.branch_canvas.read_bytes()
            state["sdk_runtime_capabilities"] = {"abandon_cycle_v1": True}
            workflow._persist(state)
            request = workflow.request_control("kill", reason="operator requested ending Cycle")
            self.assertEqual(request["status"], "interrupted")
            self.assertEqual(request["phase"], "abandon_requested")
            self.assertFalse(request["resumable"])
            record = json.loads((cycle / "recovery_control.json").read_text())
            self.assertEqual(record["action"], "abandon_cycle")
            self.assertEqual(record["source_action"], "kill")
            with self.assertRaisesRegex(ValueError, "abandonment is pending"):
                workflow.resume(cycle.name)
            with self.assertRaises(RecoveryStop):
                workflow._check_recovery_control(
                    json.loads((cycle / "research_state.json").read_text()))
            final = json.loads((cycle / "research_state.json").read_text())
            self.assertEqual(final["status"], "cancelled")
            self.assertEqual(final["phase"], "abandoned")
            self.assertFalse(final["resumable"])
            self.assertTrue(final["abandoned_cycle"]["requires_new_cycle"])
            self.assertFalse(final["execution_active"])
            self.assertEqual(spec.branch_canvas.read_bytes(), before_canvas)
            self.assertFalse((cycle / "recovery_control.json").exists())
            with self.assertRaisesRegex(ValueError, "not resumable"):
                workflow.resume(cycle.name)
            with self.assertRaisesRegex(ValueError, "explicit new Run"):
                workflow.resume(str(spec.master_id))
            self.assertNotEqual(make_run_id(spec.goal_id, spec.research_root,
                                            task_mode=spec.task_mode), cycle.name)
            event_types = [json.loads(row)["type"] for row in
                           (cycle/"events.jsonl").read_text().splitlines()]
            self.assertIn("run.cycle_abandoned", event_types)

    def test_paused_kill_finalizes_without_live_runner_or_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, spec, state, cycle = self.fixture(Path(temp))
            state.update({"status": "paused", "phase": "provider_quota_exhausted",
                          "resumable": True, "execution_active": False,
                          "recovery": {"code": "provider_quota_exhausted"}})
            state["role_outputs"] = {"investigator": {"note": "partial preserved"}}
            workflow._persist(state)
            before_canvas = spec.branch_canvas.read_bytes()
            final = workflow.request_control("kill", reason="cancel paused Cycle")
            self.assertEqual(final["status"], "cancelled")
            self.assertEqual(final["phase"], "abandoned")
            self.assertFalse(final["resumable"])
            self.assertFalse((cycle / "recovery_control.json").exists())
            persisted = json.loads((cycle/"research_state.json").read_text())
            monitor = json.loads((cycle/"run_state.json").read_text())
            self.assertEqual(monitor["status"], "CANCELLED")
            self.assertFalse(monitor["resumable"])
            self.assertEqual(persisted["role_outputs"]["investigator"]["note"],
                             "partial preserved")
            self.assertEqual(spec.branch_canvas.read_bytes(), before_canvas)
            with self.assertRaisesRegex(ValueError, "not resumable"):
                workflow.resume(cycle.name)
            with self.assertRaisesRegex(ValueError, "explicit new Run"):
                workflow.resume(str(spec.master_id))
            with self.assertRaisesRegex(ValueError, "terminal/stopped"):
                workflow.request_control("kill")

    def test_paused_kill_fails_closed_if_live_runner_or_pending_control(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, _, state, cycle = self.fixture(Path(temp))
            state.update({"status": "paused", "execution_active": False})
            workflow._persist(state)
            with patch("theory_master.runtime_manager.runtime_health",
                       return_value={"runner_alive": True}):
                with self.assertRaisesRegex(ValueError, "live Runner"):
                    workflow.request_control("kill")
            self.assertFalse((cycle/"recovery_control.json").exists())
            (cycle/"recovery_control.json").write_text('{"action":"pause"}')
            with self.assertRaisesRegex(ValueError, "already pending"):
                workflow.request_control("kill")

    def test_running_legacy_runner_must_not_receive_kill_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow, _, _, cycle = self.fixture(Path(temp))
            with self.assertRaisesRegex(ValueError, "not loaded"):
                workflow.request_control("kill")
            self.assertFalse((cycle/"recovery_control.json").exists())

    def test_supervisor_allows_normal_pause_resume_but_blocks_killed_cycle(self):
        from theory_master import supervisor
        for status in ("paused", "interrupted", "needs_attention"):
            with self.subTest(status=status), \
                 patch("theory_master.supervisor._latest_scientific_state",
                       return_value=("71", {"status": status, "resumable": True}, None)):
                self.assertFalse(supervisor._cycle_was_abandoned(Path("/unused/fixture")))
        with patch("theory_master.supervisor._latest_scientific_state",
                   return_value=("71", {"status": "cancelled",
                                         "abandoned_cycle": {"requires_new_cycle": True}}, None)):
            self.assertTrue(supervisor._cycle_was_abandoned(Path("/unused/fixture")))

    def test_supervisor_exits_quota_wait_when_paused_cycle_was_killed(self):
        from theory_master import supervisor
        with patch("theory_master.quota_auto_adjust.quota_auto_adjust_enabled",
                   return_value=True), \
             patch("theory_master.supervisor._quota_pause_context",
                   return_value=None), \
             patch("theory_master.supervisor._quota_snapshots") as query:
            self.assertIsNone(supervisor._wait_for_quota(Path("/unused/fixture")))
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
