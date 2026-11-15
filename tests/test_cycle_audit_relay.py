from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from theory_master.observability import cycle_audit_relay
from theory_master import __main__ as cli
from theory_master.audit_adapter import AuditAdapter, AuditAdapterError, ENV_NAMES, resolve_audit_adapter
from audit_adapter_fixture import create_audit_adapter


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_cycle_then_audit_relay.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_cycle_then_audit_relay", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CycleAuditRelayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.adapter, environment = create_audit_adapter(Path(temporary.name))
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_package_audit_handoff_uses_configured_skill_only(self):
        self.assertEqual(resolve_audit_adapter(), self.adapter)
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
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[1], str(self.adapter.relay))
            self.assertEqual(command[command.index("--workdir") + 1], str(self.adapter.workdir))
            self.assertEqual(run.call_args.kwargs["cwd"], self.adapter.workdir)
            self.assertEqual(command[command.index("--origin") + 1], "codex")
            self.assertNotIn("--codex-session-id", command)
            self.assertEqual(command[command.index("--window-name") + 1], "TM审计-cycle-0025")
            prompt = command[command.index("--prompt") + 1]
            self.assertIn(str(self.adapter.skill), prompt)
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
            self.assertIn(str(self.adapter.skill), prompt)
            self.assertNotIn("task_end", prompt)
            self.assertNotIn("A. 长期", prompt)

    def test_cli_cycle_audit_is_disabled_by_default_and_can_be_enabled(self):
        spec = SimpleNamespace(
            goal_id="g", max_cycles=1, max_rounds=12,
            control_canvas=Path("/tmp/control.canvas"), branch_canvas=Path("/tmp/branch.canvas"),
            notes_dir=Path("/tmp/nodes"), research_root=Path("/tmp/research"), task_root=Path("/tmp/task"),
        )
        with mock.patch.dict(os.environ, dict(zip(ENV_NAMES, map(str, (self.adapter.skill, self.adapter.relay, self.adapter.workdir)))), clear=True):
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
        self.assertIn(str(self.adapter.relay), command)
        self.assertEqual(command[command.index("--origin") + 1], "codex")
        self.assertEqual(command[command.index("--tmux-session") + 1], "agent")
        self.assertNotIn("--relay-file", command)
        prompt = command[command.index("--prompt") + 1]
        self.assertIn(str(self.adapter.skill), prompt)
        self.assertIn("/tmp/cycle-25", prompt)
        self.assertNotIn("audi_list", prompt)

    def test_validate_inputs_rejects_missing_skill_before_run(self):
        module = load_script()
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {
            "THEORY_MASTER_AUDIT_SKILL": str(Path(temp) / "missing.md"),
        }), mock.patch.object(module.subprocess, "run") as run:
            with self.assertRaisesRegex(FileNotFoundError, "Cycle audit skill file not found"):
                module.validate_inputs(Path(temp), "agent")
            run.assert_not_called()

    def test_configured_script_preflight_checks_tmux_and_loads_task_without_running_cycle(self):
        module = load_script()
        self.assertEqual(module.PACKAGE_ROOT, SCRIPT.parents[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = SimpleNamespace(research_root=root)
            with mock.patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
                 mock.patch.object(module.TaskSpec, "from_task_root", return_value=task) as load, \
                 mock.patch.object(module, "run_cycle") as research:
                self.assertIs(module.validate_inputs(root, "fixture-session"), task)
            run.assert_called_once_with(["tmux", "has-session", "-t", "fixture-session"],
                                        text=True, capture_output=True, check=False)
            load.assert_called_once_with(root.resolve())
            research.assert_not_called()
            self.assertFalse((root / ".theory-master").exists())

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


class AuditAdapterResolutionTests(unittest.TestCase):
    def test_unconfigured_standalone_install_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(AuditAdapterError, "disabled by default"):
                resolve_audit_adapter(environ={}, package_root=Path(temporary) / "standalone")

    def test_explicit_paths_resolve_complete_adapter_and_reject_missing_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            adapter, environment = create_audit_adapter(Path(temporary))
            self.assertEqual(resolve_audit_adapter(environ=environment), adapter)
            adapter.relay.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "relay file not found"):
                resolve_audit_adapter(environ=environment)
            adapter.skill.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "skill file not found"):
                resolve_audit_adapter(environ=environment)

    def test_every_explicit_path_must_be_absolute(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, environment = create_audit_adapter(Path(temporary))
            for name in ENV_NAMES:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(AuditAdapterError, name + " must be an absolute path"):
                        resolve_audit_adapter(environ={**environment, name: "relative-path"})
            with self.assertRaisesRegex(NotADirectoryError, "working directory not found"):
                resolve_audit_adapter(environ={**environment,
                    "THEORY_MASTER_AUDIT_WORKDIR": str(Path(temporary) / "absent")})

    def test_legacy_pair_is_used_only_when_both_resources_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            package = repo / "tool/theory_master"
            package.mkdir(parents=True)
            skill = repo / "tool/.codex/skills/tm-audit-review/SKILL.md"
            relay = repo / "tool/python_script/start_origin_tmux_relay.py"
            skill.parent.mkdir(parents=True)
            relay.parent.mkdir(parents=True)
            skill.write_text("# Test-only legacy adapter\n")
            relay.write_text("# Test-only legacy launcher\n")
            self.assertEqual(resolve_audit_adapter(environ={}, package_root=package),
                             AuditAdapter(skill, relay, repo))
            with self.assertRaisesRegex(AuditAdapterError, "incomplete"):
                resolve_audit_adapter(environ={ENV_NAMES[0]: str(skill)}, package_root=package)
            for name in ENV_NAMES:
                for empty in ("", "   "):
                    with self.subTest(name=name, empty=empty):
                        with self.assertRaisesRegex(AuditAdapterError, "incomplete"):
                            resolve_audit_adapter(environ={name: empty}, package_root=package)
            relay.unlink()
            with self.assertRaisesRegex(AuditAdapterError, "not configured"):
                resolve_audit_adapter(environ={}, package_root=package)

    def test_missing_configuration_is_rejected_before_any_audit_artifact_or_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cycle = root / "cycle"
            cycle.mkdir()
            def unavailable():
                return resolve_audit_adapter(environ={}, package_root=root / "standalone")
            with mock.patch.object(cycle_audit_relay, "resolve_audit_adapter", side_effect=unavailable), \
                 mock.patch.object(cycle_audit_relay.subprocess, "run") as run:
                with self.assertRaisesRegex(AuditAdapterError, "not configured"):
                    cycle_audit_relay.spawn_cycle_audit_agent({"run_dir": str(cycle)})
            self.assertFalse((cycle / "audit").exists())
            run.assert_not_called()
            script = load_script()
            with mock.patch.object(script, "resolve_audit_adapter", side_effect=unavailable), \
                 mock.patch.object(script, "run_cycle") as run_cycle, \
                 mock.patch.object(script.subprocess, "run") as relay:
                with self.assertRaisesRegex(AuditAdapterError, "not configured"):
                    script.main(["--task-root", str(root)])
            self.assertFalse((root / ".theory-master").exists())
            run_cycle.assert_not_called()
            relay.assert_not_called()

    def test_cli_rejects_missing_adapter_only_when_cycle_audit_is_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            spec = SimpleNamespace(research_root=Path(temporary))
            def unavailable():
                return resolve_audit_adapter(environ={}, package_root=Path(temporary) / "standalone")
            with mock.patch("theory_master.audit_adapter.resolve_audit_adapter", side_effect=unavailable) as resolver:
                with self.assertRaisesRegex(AuditAdapterError, "not configured"):
                    cli._collector(spec, cycle_audit=True)
                resolver.assert_called_once()
                self.assertFalse(cli._collector(spec, cycle_audit=False).cycle_audit_actions)
                resolver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
