from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from theory_master.observability import cycle_audit_relay
from theory_master import __main__ as cli


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_cycle_then_audit_relay.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_cycle_then_audit_relay", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CycleAuditRelayTest(unittest.TestCase):
    def test_package_audit_handoff_uses_canonical_skill_only(self):
        expected = Path(__file__).resolve().parents[3]
        self.assertEqual(cycle_audit_relay.REPO_ROOT, expected)
        self.assertEqual(cycle_audit_relay.AUDIT_SKILL, expected / "tool/.codex/skills/tm-audit-review/SKILL.md")
        self.assertTrue(cycle_audit_relay.AUDIT_SKILL.is_file())
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "cycle-0025"
            cycle.mkdir()
            state = {
                "run_dir": str(cycle), "run_id": "cycle-0025", "goal_id": "g",
                "status": "OVER", "phase": "complete",
            }
            with mock.patch("theory_master.observability.cycle_audit_relay.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout="agent:TM审计-cycle-0025.0", stderr="")
                target = cycle_audit_relay.spawn_cycle_audit_agent(state)
            self.assertEqual(target, "agent:TM审计-cycle-0025.0")
            self.assertFalse((cycle / "theory_master_cycle_audit_relay.md").exists())
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--origin") + 1], "codex")
            self.assertNotIn("--codex-session-id", command)
            self.assertEqual(command[command.index("--window-name") + 1], "TM审计-cycle-0025")
            prompt = command[command.index("--prompt") + 1]
            self.assertIn(str(cycle_audit_relay.AUDIT_SKILL), prompt)
            self.assertIn(str(cycle), prompt)
            self.assertIn(str(cycle / "audit" / "cycle_audit.md"), prompt)
            self.assertNotIn("audi_list", prompt)
            self.assertNotIn("A. 长期", prompt)
            self.assertNotIn("B. 短期", prompt)

    def test_task_end_uses_same_skill_only_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            cycle = Path(temp) / "cycle-0099"
            cycle.mkdir()
            state = {
                "run_dir": str(cycle), "run_id": "cycle-0099", "goal_id": "g",
                "status": "OVER", "phase": "complete",
            }
            with mock.patch("theory_master.observability.cycle_audit_relay.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout="agent:TM审计-cycle-0099.0", stderr="")
                cycle_audit_relay.spawn_cycle_audit_agent(state)
            prompt = run.call_args.args[0][run.call_args.args[0].index("--prompt") + 1]
            self.assertIn(str(cycle_audit_relay.AUDIT_SKILL), prompt)
            self.assertNotIn("task_end", prompt)
            self.assertNotIn("A. 长期", prompt)

    def test_cli_cycle_audit_is_disabled_by_default_and_can_be_enabled(self):
        spec = SimpleNamespace(
            goal_id="g", max_cycles=1, max_rounds=12,
            control_canvas=Path("/tmp/control.canvas"), branch_canvas=Path("/tmp/branch.canvas"),
            notes_dir=Path("/tmp/nodes"), research_root=Path("/tmp/research"), task_root=Path("/tmp/task"),
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(cli._collector(spec).cycle_audit_actions)
            self.assertTrue(cli._collector(spec).system_failure_actions)
            self.assertTrue(cli._collector(spec, cycle_audit=True).cycle_audit_actions)
            self.assertFalse(cli._collector(spec, error_repair=False).system_failure_actions)
        with mock.patch.dict(os.environ, {"THEORY_MASTER_CYCLE_AUDIT": "1", "THEORY_MASTER_ERROR_REPAIR": "0"}, clear=False):
            self.assertTrue(cli._collector(spec).cycle_audit_actions)
            self.assertFalse(cli._collector(spec).system_failure_actions)
            self.assertFalse(cli._collector(spec, cycle_audit=False).cycle_audit_actions)

    def test_build_relay_command_uses_fresh_codex_and_file_pointer(self):
        module = load_script()
        command = module.build_relay_command(
            cycle_dir=Path("/tmp/cycle-25"),
            report_file=Path("/tmp/cycle-25/audit/cycle_audit.md"),
            tmux_session="agent",
            window_name="TheoryMaster审计",
            model=None,
        )
        self.assertIn(str(module.RELAY_STARTER), command)
        self.assertEqual(command[command.index("--origin") + 1], "codex")
        self.assertEqual(command[command.index("--tmux-session") + 1], "agent")
        self.assertNotIn("--relay-file", command)
        prompt = command[command.index("--prompt") + 1]
        self.assertIn(str(module.AUDIT_SKILL), prompt)
        self.assertIn("/tmp/cycle-25", prompt)
        self.assertNotIn("audi_list", prompt)

    def test_validate_inputs_rejects_missing_skill_before_run(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(module, "AUDIT_SKILL", Path(temp) / "missing.md"):
            with self.assertRaises(FileNotFoundError):
                module.validate_inputs(Path(temp), "agent")

    def test_relay_is_not_attempted_when_cycle_raises(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_spec = mock.Mock(research_root=root, task_mode=True)
            completed = mock.Mock(returncode=0, stdout="agent:TheoryMaster审计.0\n", stderr="")
            with mock.patch.object(module, "validate_inputs", return_value=fake_spec), \
                 mock.patch.object(module, "latest_cycle_dir", return_value=None), \
                 mock.patch.object(module, "run_cycle", side_effect=RuntimeError("cycle failed")), \
                 mock.patch.object(module.subprocess, "run", return_value=completed) as run:
                code = module.main([
                    "--task-root", str(root),
                    "--tmux-session", "agent",
                ])
            self.assertEqual(code, 1)
            run.assert_not_called()
            state = root / ".theory-master/cycle_audit_relay_state.json"
            saved = state.read_text(encoding="utf-8")
            self.assertIn('"audit_eligible": false', saved)
            self.assertIn('"relay_status": "deferred_nonterminal_cycle"', saved)

    def test_relay_starts_only_after_over_cycle(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle_dir = root / "cycle-0024"
            cycle_dir.mkdir()
            fake_spec = mock.Mock(research_root=root, task_mode=True)
            completed = mock.Mock(returncode=0, stdout="agent:TheoryMaster审计.0\n", stderr="")
            with mock.patch.object(module, "validate_inputs", return_value=fake_spec), \
                 mock.patch.object(module, "latest_cycle_dir", return_value=cycle_dir), \
                 mock.patch.object(module, "run_cycle", return_value={"status": "over", "phase": "complete"}), \
                 mock.patch.object(module.subprocess, "run", return_value=completed) as run:
                code = module.main([
                    "--task-root", str(root),
                    "--tmux-session", "agent",
                ])
            self.assertEqual(code, 0)
            run.assert_called_once()
            saved = (root / ".theory-master/cycle_audit_relay_state.json").read_text(encoding="utf-8")
            self.assertIn('"audit_eligible": true', saved)
            self.assertIn('"relay_status": "started"', saved)

    def test_run_cycle_attaches_collector_after_backend_construction(self):
        module = load_script()
        fake_spec = mock.Mock(research_root=Path("/tmp/task"))
        backend = mock.Mock()
        workflow = mock.Mock()
        workflow.run.return_value = {"status": "over"}
        with mock.patch.object(module, "resolve_routes", return_value={"judge": mock.Mock()}), \
             mock.patch.object(module, "Collector", return_value="collector"), \
             mock.patch.object(module, "CodexBackend", return_value=backend) as backend_cls, \
             mock.patch.object(module, "Workflow", return_value=workflow):
            result = module.run_cycle(fake_spec)
        backend_cls.assert_called_once_with(fake_spec.research_root, routes={"judge": mock.ANY})
        self.assertEqual(backend.collector, "collector")
        self.assertEqual(result["status"], "over")


if __name__ == "__main__":
    unittest.main()
