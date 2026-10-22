from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import theory_master.__main__ as cli


class ForegroundCliTest(unittest.TestCase):
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
