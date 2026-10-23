from __future__ import annotations

import json

import tempfile
import subprocess
import sys
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.observability import cycle_audit_relay, error_relay
from theory_master.supervisor import _handle_failed_state


class ErrorRelayTest(unittest.TestCase):
    def test_legacy_relay_locations_are_metadata_only(self):
        expected = Path(__file__).resolve().parents[3]
        self.assertEqual(error_relay.REPO_ROOT, expected)
        self.assertEqual(error_relay.RELAY_STARTER, expected / "tool/python_script/start_origin_tmux_relay.py")
        # Optional operator launchers are not bundled with the public SDK.
        import inspect
        source = inspect.getsource(error_relay.spawn_repair_agent)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("RELAY_STARTER", source)
        self.assertNotIn("RESUME_STARTER", source)

    def test_legacy_repair_never_executes_an_external_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {"run_dir": temp, "run_id": "fixture", "status": "FAILED"}
            with patch("subprocess.run", side_effect=AssertionError("external launch forbidden")), \
                 patch("subprocess.Popen", side_effect=AssertionError("external launch forbidden")):
                target = error_relay.spawn_repair_agent(state, {"error": "fixture failure"})
            self.assertTrue(target.endswith(":sdk_internal_hold"))

    def test_failed_relay_compatibility_records_internal_hold_no_tmux(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "cycle-0023"
            cycle.mkdir()
            state = {"run_dir": str(cycle), "run_id": "cycle-0023", "status": "FAILED"}
            first = error_relay.spawn_repair_agent(state, {"error": "backend boom"})
            second = error_relay.spawn_repair_agent(state, {"error": "backend boom"})
            self.assertEqual(first, second)
            self.assertTrue(first.endswith(":sdk_internal_hold"))
            self.assertFalse((cycle / "theory_master_error_relay.md").exists())
            rows = [json.loads(line) for line in (cycle / "important_events.jsonl").read_text().splitlines()]
            self.assertEqual(len([x for x in rows if x.get("lifecycle") == "registered"]), 1)
            self.assertEqual(rows[-1]["repair"]["status"], "blocked")

    def test_supervisor_failed_fallback_holds_without_spawning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            cycle = root / "7"
            cycle.mkdir(parents=True)
            (cycle / ".theory-master-cycle").write_text("theory-master/v2\\ntest\\n")
            (cycle / "research_state.json").write_text(json.dumps({
                "status": "failed", "phase": "failed", "resumable": True,
                "recovery": {"action": "error", "kind": "RuntimeError", "message": "runner died"},
            }))
            (cycle / "run_state.json").write_text(json.dumps({"run_id": "7"}))
            with patch("theory_master.observability.error_relay.spawn_repair_agent") as spawn, \
                 patch("theory_master.supervisor._supervisor_backend", return_value=None):
                self.assertFalse(_handle_failed_state(root, repair_enabled=True))
                self.assertFalse(_handle_failed_state(root, repair_enabled=True))
            spawn.assert_not_called()
            state = json.loads((cycle / "run_state.json").read_text())
            self.assertEqual(state["repair_handoff"]["status"], "sdk_internal_hold")
            self.assertEqual(state["repair_handoff"]["target"], "sdk:runtime_supervisor")

    def test_cycle_audit_relay_uses_current_python_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "cycle-0023"
            cycle.mkdir(parents=True)
            state = {
                "run_dir": str(cycle), "run_id": "cycle-0023", "goal_id": "g",
                "status": "OVER", "phase": "complete",
            }
            with patch.object(cycle_audit_relay, "AUDIT_SKILL", Path(temp) / "SKILL.md"), \
                 patch("theory_master.observability.cycle_audit_relay.subprocess.run") as run:
                cycle_audit_relay.AUDIT_SKILL.write_text("# audit\n", encoding="utf-8")
                run.return_value = SimpleNamespace(returncode=0, stdout="started", stderr="")
                self.assertEqual(cycle_audit_relay.spawn_cycle_audit_agent(state), "started")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[0], sys.executable)
            self.assertEqual(Path(cmd[1]), cycle_audit_relay.RELAY_STARTER)


    def test_cycle_audit_relay_retries_one_transient_start_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "cycle-0023"
            cycle.mkdir(parents=True)
            state = {
                "run_dir": str(cycle), "run_id": "cycle-0023", "goal_id": "g",
                "status": "OVER", "phase": "complete",
            }
            first = SimpleNamespace(returncode=2, stdout="", stderr="transient marker failure")
            second = SimpleNamespace(returncode=0, stdout="agent:TM审计-cycle-0023.0\n", stderr="")
            with patch.object(cycle_audit_relay, "AUDIT_SKILL", Path(temp) / "SKILL.md"), \
                 patch("theory_master.observability.cycle_audit_relay.subprocess.run", side_effect=[first, second]) as run, \
                 patch("theory_master.observability.cycle_audit_relay.time.sleep"):
                cycle_audit_relay.AUDIT_SKILL.write_text("# audit\n", encoding="utf-8")
                result = cycle_audit_relay.spawn_cycle_audit_agent(state)
            self.assertEqual(result, "agent:TM审计-cycle-0023.0")
            self.assertEqual(run.call_count, 2)



if __name__ == "__main__":
    unittest.main()
