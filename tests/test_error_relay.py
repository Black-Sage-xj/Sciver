from __future__ import annotations

import json
import os

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from audit_adapter_fixture import create_audit_adapter

from theory_master.observability import cycle_audit_relay, error_relay
from theory_master.supervisor import _handle_failed_state


class ErrorRelayTest(unittest.TestCase):
    def test_compatibility_handoff_delegates_only_to_internal_incident_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            cycle = Path(temporary)
            incident = {"incident_id": "fixture-incident"}
            with patch.object(error_relay, "register_cycle_incident", return_value=(incident, True)) as register, \
                 patch.object(error_relay, "record_repair_handoff") as record, \
                 patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
                result = error_relay.spawn_repair_agent(
                    {"run_dir": str(cycle), "phase": "investigating"},
                    {"error": "fixture failure", "event": "run.failed", "role": "investigator"},
                )
            self.assertEqual(result, "incident:fixture-incident:sdk_internal_hold")
            register.assert_called_once()
            self.assertEqual(register.call_args.args, (cycle,))
            self.assertEqual(register.call_args.kwargs["summary"], "fixture failure")
            self.assertEqual(register.call_args.kwargs["kind"], "system_failure")
            self.assertEqual(register.call_args.kwargs["fields"]["role"], "investigator")
            record.assert_called_once()
            self.assertEqual(record.call_args.kwargs["incident"], incident)
            self.assertEqual(record.call_args.kwargs["status"], "blocked")
            self.assertEqual(record.call_args.kwargs["target"], "sdk:internal_hold")
            self.assertEqual(record.call_args.kwargs["policy_version"], 4)
            run.assert_not_called()
            popen.assert_not_called()

    def test_missing_cycle_fails_before_creating_incident_or_external_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-cycle"
            with patch.object(error_relay, "register_cycle_incident") as register, \
                 patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
                with self.assertRaisesRegex(FileNotFoundError, "cycle directory not found"):
                    error_relay.spawn_repair_agent({"run_dir": str(missing)}, {"error": "fixture"})
            self.assertFalse(missing.exists())
            register.assert_not_called()
            run.assert_not_called()
            popen.assert_not_called()

    def test_compatibility_handoff_preserves_existing_scientific_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            cycle = Path(temporary)
            state = cycle / "research_state.json"
            original = '{"status":"failed","branches":[{"status":"supported","evidence":["fixture"]}]}\n'
            state.write_text(original, encoding="utf-8")
            evidence = cycle / "evidence.md"
            evidence.write_text("# Preserved evidence\n", encoding="utf-8")
            with patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
                result = error_relay.spawn_repair_agent(
                    {"run_dir": str(cycle), "status": "FAILED"}, {"error": "fixture failure"})
            self.assertTrue(result.endswith(":sdk_internal_hold"))
            self.assertEqual(state.read_text(encoding="utf-8"), original)
            self.assertEqual(evidence.read_text(encoding="utf-8"), "# Preserved evidence\n")
            run.assert_not_called()
            popen.assert_not_called()

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
            adapter, environment = create_audit_adapter(Path(temp))
            with patch.dict(os.environ, environment), \
                 patch("theory_master.observability.cycle_audit_relay.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout="started", stderr="")
                self.assertEqual(cycle_audit_relay.spawn_cycle_audit_agent(state), "started")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[0], sys.executable)
            self.assertEqual(Path(cmd[1]), adapter.relay)


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
            adapter, environment = create_audit_adapter(Path(temp))
            with patch.dict(os.environ, environment), \
                 patch("theory_master.observability.cycle_audit_relay.subprocess.run", side_effect=[first, second]) as run, \
                 patch("theory_master.observability.cycle_audit_relay.time.sleep"):
                result = cycle_audit_relay.spawn_cycle_audit_agent(state)
            self.assertEqual(result, "agent:TM审计-cycle-0023.0")
            self.assertEqual(run.call_count, 2)



if __name__ == "__main__":
    unittest.main()
