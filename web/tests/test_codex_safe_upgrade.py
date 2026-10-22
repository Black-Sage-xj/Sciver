"""Managed-upgrade validation uses mocks; never upgrades or invokes paid Codex turns."""
from __future__ import annotations

import importlib.util
import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "codex_safe_upgrade.py"
_SPEC = importlib.util.spec_from_file_location("codex_safe_upgrade", _PATH)
_upgrade = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_upgrade)


class ManagedUpgradeTests(unittest.TestCase):
    VERSION = {"status": "running", "cliVersion": "0.161.0",
               "managedCodexVersion": "0.161.0", "appServerVersion": "0.161.0"}

    def test_readonly_check_succeeds_when_owned_and_idle(self):
        with mock.patch.object(_upgrade.usage, "_account_switch_process_lock") as lock, \
             mock.patch.object(_upgrade.usage, "_assert_no_active_codex_sessions"), \
             mock.patch.object(_upgrade.usage, "_assert_managed_app_server"), \
             mock.patch.object(_upgrade, "_daemon_version", return_value=self.VERSION), \
             mock.patch.object(_upgrade, "_run") as run:
            lock.return_value.__enter__.return_value = None
            with redirect_stdout(io.StringIO()):
                result = _upgrade.main(["--check"])
            self.assertEqual(result, 0)
            run.assert_not_called()

    def test_active_sessions_block_upgrade_before_any_install(self):
        with mock.patch.object(_upgrade.usage, "_account_switch_process_lock"), \
             mock.patch.object(_upgrade.usage, "_assert_no_active_codex_sessions",
                               side_effect=OSError("busy")), \
             mock.patch.object(_upgrade, "_run") as run:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(_upgrade.main(["--upgrade"]), 1)
            run.assert_not_called()

    def test_version_mismatch_must_not_report_success(self):
        with self.assertRaisesRegex(RuntimeError, "versions differ"):
            _upgrade._assert_version_aligned({**self.VERSION, "managedCodexVersion": "0.150.0"})


if __name__ == "__main__":
    unittest.main()
