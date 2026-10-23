from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from theory_master.onboarding import prepare_test0, launch_web
from theory_master.regression_cases import RegressionCaseError


class OnboardingTests(unittest.TestCase):
    def test_test0_initialization_is_offline_bounded_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with mock.patch("theory_master.codex_backend.CodexBackend.__init__", side_effect=AssertionError("no models")):
                first = prepare_test0(workspace)
                self.assertEqual(json.loads(first.config_path.read_text())["max_cycles"], 1)
                before = first.config_path.read_bytes()
                second = prepare_test0(workspace)
            self.assertEqual(first.config_path, second.config_path)
            self.assertEqual(first.config_path.read_bytes(), before)
            self.assertTrue((workspace / "test0/benchmark/datasets/brownian_2d/trajectories.csv").is_file())
            self.assertFalse((workspace / "test0/expected.json").exists())

    def test_existing_unrelated_task_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "test0"
            root.mkdir()
            (root / "research_plan.md").write_text("my research")
            with self.assertRaises(RegressionCaseError):
                prepare_test0(Path(temp))
            self.assertEqual((root / "research_plan.md").read_text(), "my research")

    def test_launcher_preserves_explicit_compute_environment(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": "/custom/python"}), mock.patch("tm_observer.web.serve") as serve:
            launch_web(Path(temp), port=8788, open_browser=False)
            serve.assert_called_once_with(Path(temp).resolve(), host="127.0.0.1", port=8788, open_browser=False)
            import os
            self.assertEqual(os.environ["THEORYMASTER_COMPUTE_PYTHON"], "/custom/python")

    def test_compute_interpreter_preserves_virtualenv_symlink(self):
        from theory_master.codex_backend import CodexBackend
        import sys
        with tempfile.TemporaryDirectory() as temp:
            interpreter = Path(temp) / "bin/python"
            interpreter.parent.mkdir()
            interpreter.symlink_to(sys.executable)
            with mock.patch.dict("os.environ", {"THEORYMASTER_COMPUTE_PYTHON": str(interpreter)}):
                environment = CodexBackend._sdk_environment()
            self.assertEqual(environment["PYTHON"], str(interpreter))
            self.assertEqual(environment["THEORYMASTER_COMPUTE_PYTHON"], str(interpreter))
            self.assertTrue(environment["PATH"].startswith(str(interpreter.parent) + ":"))

    def test_browser_waits_for_http_readiness(self):
        from tm_observer.web import _open_ready_browser
        from urllib.error import URLError
        with mock.patch("urllib.request.urlopen", side_effect=[URLError("starting"), mock.MagicMock()]) as probe, mock.patch("webbrowser.open", return_value=True) as browser, mock.patch("tm_observer.web.time.sleep"):
            _open_ready_browser("http://127.0.0.1:8787/")
            self.assertEqual(probe.call_count, 2)
            browser.assert_called_once_with("http://127.0.0.1:8787/")

    def test_browser_is_not_opened_when_server_never_becomes_ready(self):
        from tm_observer.web import _open_ready_browser
        from urllib.error import URLError
        with mock.patch("urllib.request.urlopen", side_effect=URLError("offline")), mock.patch("webbrowser.open") as browser, mock.patch("tm_observer.web.time.sleep"):
            _open_ready_browser("http://127.0.0.1:8787/")
            browser.assert_not_called()
