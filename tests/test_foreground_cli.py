from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import theory_master.__main__ as cli


class ForegroundCliTest(unittest.TestCase):
    def test_protected_resume_enters_bound_sdk_before_any_local_execution(self):
        root = Path("/fixture/current-task")
        config = root / "theory-master.config.json"
        spec = SimpleNamespace(task_mode=True, research_root=root, config_path=config, master_id=47)
        target = Path("/fixture/reviewed-sdk")
        env = {"PATH": "/fixture/helper:/usr/bin", "PYTHONPATH": str(target)}
        binding = SimpleNamespace(sdk_root=target, environment=lambda *, protected: env)
        argv = ["sciver", "resume", "47", "--foreground", "--no-error-repair"]

        with (patch.object(sys, "argv", argv),
              patch.object(cli, "spec_from_task", return_value=spec),
              patch("theory_master.operator_host_binding.protected_launch_binding", return_value=binding) as bind,
              patch.object(cli, "create_load_snapshot") as snapshot,
              patch.object(os, "execve", side_effect=SystemExit(19)) as execute):
            with self.assertRaises(SystemExit) as switched:
                cli.main()

        self.assertEqual(switched.exception.code, 19)
        bind.assert_called_once_with(config)
        snapshot.assert_not_called()
        execute.assert_called_once_with(sys.executable,
                                        [sys.executable, "-P", "-m", "theory_master", *argv[1:]], env)

    def test_local_protected_cli_preserves_working_directory_and_provides_helper(self):
        working_directory = Path.cwd()
        source = Path(cli.__file__).resolve().parents[1]
        env = {"PATH": "/fixture/helper:/usr/bin", "PYTHONSAFEPATH": "1"}
        binding = SimpleNamespace(sdk_root=source, environment=lambda *, protected: env)
        with (patch("theory_master.operator_host_binding.protected_launch_binding", return_value=binding),
              patch.object(os, "execve") as execute,
              patch.dict(os.environ, {"PATH": "/usr/bin"})):
            cli._prepare_protected_cli(SimpleNamespace(config_path=Path("config.json")))
            self.assertEqual(os.environ["PATH"], "/fixture/helper:/usr/bin")
            self.assertEqual(os.environ["PYTHONSAFEPATH"], "1")
            self.assertEqual(Path.cwd(), working_directory)
        execute.assert_not_called()

    def test_task_mode_foreground_uses_headless_runner_in_current_process(self):
        with tempfile.TemporaryDirectory() as temp:
            research_root = Path(temp) / "research"
            research_root.mkdir()
            spec = SimpleNamespace(task_mode=True, research_root=research_root, master_id=12345)
            argv = [
                "theory-master", "run", "--foreground",
                "--no-cycle-audit", "--no-error-repair",
                "--model", "fixture-model",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(cli, "spec_from_task", return_value=spec),
                patch.object(cli, "create_load_snapshot"),
                patch.object(cli, "_refresh_pricing_daily"),
                patch.object(cli, "resolve_routes", return_value={}),
                patch("theory_master.runner.main", return_value=17) as runner,
                patch("theory_master.runtime_manager.launch_managed_runtime") as managed,
            ):
                self.assertEqual(cli.main(), 17)
            managed.assert_not_called()
            args = runner.call_args.args[0]
            self.assertEqual(args[:6], [
                "--task-root", str(research_root.resolve()),
                "--operation", "run",
                "--identifier", "12345",
            ])
            self.assertIn("--no-cycle-audit", args)
            self.assertIn("--no-error-repair", args)
            self.assertEqual(args[args.index("--model") + 1], "fixture-model")

    def test_default_task_mode_run_still_uses_managed_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            research_root = Path(temp) / "research"
            research_root.mkdir()
            spec = SimpleNamespace(task_mode=True, research_root=research_root, master_id=12345)
            argv = ["theory-master", "run"]
            with (
                patch.object(sys, "argv", argv),
                patch.object(cli, "spec_from_task", return_value=spec),
                patch.object(cli, "create_load_snapshot"),
                patch.object(cli, "_refresh_pricing_daily"),
                patch.object(cli, "resolve_routes", return_value={}),
                patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started"}) as managed,
                patch("theory_master.runtime_manager.reconnect_tui"),
                patch("theory_master.runner.main") as runner,
            ):
                self.assertEqual(cli.main(), 0)
            managed.assert_called_once()
            runner.assert_not_called()

    def test_noninteractive_managed_run_skips_tui_attach(self):
        with tempfile.TemporaryDirectory() as temp:
            research_root = Path(temp) / "research"
            research_root.mkdir()
            spec = SimpleNamespace(task_mode=True, research_root=research_root, master_id=12345)
            argv = ["theory-master", "run"]
            with (
                patch.object(sys, "argv", argv),
                patch.object(cli, "spec_from_task", return_value=spec),
                patch.object(cli, "create_load_snapshot"),
                patch.object(cli, "_refresh_pricing_daily"),
                patch.object(cli, "resolve_routes", return_value={}),
                patch.object(cli, "_interactive_terminal", return_value=False),
                patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started"}),
                patch("theory_master.runtime_manager.reconnect_tui") as reconnect,
            ):
                self.assertEqual(cli.main(), 0)
            reconnect.assert_not_called()

    def test_tui_attach_failure_is_warning_when_runtime_is_healthy(self):
        with tempfile.TemporaryDirectory() as temp:
            research_root = Path(temp) / "research"
            research_root.mkdir()
            spec = SimpleNamespace(task_mode=True, research_root=research_root, master_id=12345)
            argv = ["theory-master", "run"]
            with (
                patch.object(sys, "argv", argv),
                patch.object(cli, "spec_from_task", return_value=spec),
                patch.object(cli, "create_load_snapshot"),
                patch.object(cli, "_refresh_pricing_daily"),
                patch.object(cli, "resolve_routes", return_value={}),
                patch.object(cli, "_interactive_terminal", return_value=True),
                patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started"}),
                patch("theory_master.runtime_manager.reconnect_tui", side_effect=RuntimeError("TUI exited during startup")),
                patch("theory_master.runtime_manager.runtime_health", return_value={"active": True, "state": "RUNNING"}),
            ):
                self.assertEqual(cli.main(), 0)

    def test_tui_attach_failure_propagates_when_runtime_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as temp:
            research_root = Path(temp) / "research"
            research_root.mkdir()
            spec = SimpleNamespace(task_mode=True, research_root=research_root, master_id=12345)
            argv = ["theory-master", "run"]
            with (
                patch.object(sys, "argv", argv),
                patch.object(cli, "spec_from_task", return_value=spec),
                patch.object(cli, "create_load_snapshot"),
                patch.object(cli, "_refresh_pricing_daily"),
                patch.object(cli, "resolve_routes", return_value={}),
                patch.object(cli, "_interactive_terminal", return_value=True),
                patch("theory_master.runtime_manager.launch_managed_runtime", return_value={"status": "started"}),
                patch("theory_master.runtime_manager.reconnect_tui", side_effect=RuntimeError("TUI exited during startup")),
                patch("theory_master.runtime_manager.runtime_health", return_value={"active": False, "state": "ORPHANED"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "TUI exited during startup"):
                    cli.main()


if __name__ == "__main__":
    unittest.main()
