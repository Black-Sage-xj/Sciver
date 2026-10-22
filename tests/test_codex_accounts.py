from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from theory_master import codex_accounts


class CodexAccountSwitchTests(unittest.TestCase):
    def _patch_layout(self, base: Path):
        legacy = base / ".codex"
        root = base / ".codex-accounts"
        runtime = base / ".codex-theory-master"
        legacy.mkdir()
        return legacy, root, runtime, (
            mock.patch.object(codex_accounts, "account_root", return_value=root),
            mock.patch.object(codex_accounts, "_legacy_home", return_value=legacy),
            mock.patch.object(codex_accounts, "runtime_home", return_value=runtime),
        )

    def test_layout_migrates_to_identity_only_accounts_and_shared_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, root, runtime, patches = self._patch_layout(base)
            (legacy / "auth.json").write_text("account-one", encoding="utf-8")
            (legacy / "config.toml").write_text("model = 'daily'\n", encoding="utf-8")
            (legacy / "skills").mkdir()
            (legacy / "rules").mkdir()
            (legacy / "yun.config.toml").write_text("provider = 'tm'\n", encoding="utf-8")

            # Reproduce the old layout: account-1 was the whole daily Codex home.
            root.mkdir()
            (root / "account-1").symlink_to(legacy, target_is_directory=True)
            account2 = root / "account-2"
            account2.mkdir()
            (account2 / "auth.json").write_text("account-two", encoding="utf-8")
            (account2 / "config.toml").symlink_to(legacy / "config.toml")
            (account2 / "skills").symlink_to(legacy / "skills", target_is_directory=True)
            (root / "current").symlink_to("account-2", target_is_directory=True)

            with patches[0], patches[1], patches[2]:
                layout = codex_accounts.ensure_layout()

            self.assertTrue(layout["1"].is_dir())
            self.assertFalse(layout["1"].is_symlink())
            self.assertEqual((layout["1"] / "auth.json").read_text(encoding="utf-8"), "account-one")
            self.assertEqual((layout["2"] / "auth.json").read_text(encoding="utf-8"), "account-two")
            self.assertFalse((layout["2"] / "config.toml").exists())
            self.assertFalse((layout["2"] / "skills").exists())
            self.assertEqual(layout["current"].readlink(), Path("account-2"))
            self.assertEqual(layout["daily_runtime"], legacy)
            self.assertTrue((legacy / "auth.json").is_symlink())
            self.assertEqual((legacy / "auth.json").resolve(), (layout["2"] / "auth.json").resolve())
            self.assertEqual((legacy / "config.toml").read_text(encoding="utf-8"), "model = 'daily'\n")
            self.assertTrue((legacy / "skills").is_dir())

            self.assertEqual(layout["runtime"], runtime)
            runtime_config = (runtime / "config.toml").read_text(encoding="utf-8")
            self.assertIn('.theory-master-codex-root', runtime_config)
            self.assertIn("plugins = false", runtime_config)
            self.assertNotIn("model = 'daily'", runtime_config)
            self.assertTrue((runtime / "auth.json").is_symlink())
            self.assertEqual((runtime / "auth.json").resolve(), (layout["2"] / "auth.json").resolve())
            self.assertEqual((runtime / "yun.config.toml").read_text(encoding="utf-8"), "provider = 'tm'\n")
            self.assertTrue((runtime / "skills").is_dir())
            self.assertEqual(list((runtime / "skills").iterdir()), [])

    def test_switch_changes_auth_only_and_runtime_path_stays_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, root, runtime, patches = self._patch_layout(base)
            (legacy / "auth.json").write_text("account-one", encoding="utf-8")

            with patches[0], patches[1], patches[2]:
                layout = codex_accounts.ensure_layout()
                (layout["2"] / "auth.json").write_text("account-two", encoding="utf-8")
                config_before = (runtime / "config.toml").read_bytes()
                runtime_before = layout["runtime"]

                self.assertEqual(codex_accounts.selected_account(), "1")
                self.assertEqual((runtime / "auth.json").resolve(), (layout["1"] / "auth.json").resolve())
                self.assertEqual((legacy / "auth.json").resolve(), (layout["1"] / "auth.json").resolve())

                account2 = codex_accounts.use_account("2", restart_app=False)
                self.assertEqual(codex_accounts.selected_account(), "2")
                self.assertEqual((runtime / "auth.json").resolve(), (account2 / "auth.json").resolve())
                self.assertEqual((legacy / "auth.json").resolve(), (account2 / "auth.json").resolve())
                self.assertEqual(codex_accounts.ensure_layout()["runtime"], runtime_before)
                self.assertEqual((runtime / "config.toml").read_bytes(), config_before)

                codex_accounts.use_account("1", restart_app=False)
                self.assertEqual(codex_accounts.selected_account(), "1")
                self.assertEqual((runtime / "auth.json").resolve(), (layout["1"] / "auth.json").resolve())
                self.assertEqual((legacy / "auth.json").resolve(), (layout["1"] / "auth.json").resolve())
                self.assertEqual((runtime / "config.toml").read_bytes(), config_before)

    def test_account_status_reports_ambient_codex_home_as_non_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy, root, runtime, patches = self._patch_layout(base)
            (legacy / "auth.json").write_text("account-one", encoding="utf-8")
            with patches[0], patches[1], patches[2], mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/daily-codex"}, clear=False):
                status = codex_accounts.account_status()
            self.assertEqual(status["runtime_home"], str(runtime))
            self.assertEqual(status["ambient_codex_home"], "/tmp/daily-codex")

    def test_quota_availability_prefers_explicit_account_flag(self) -> None:
        self.assertTrue(codex_accounts.quota_available({
            "ok": True,
            "ordinary_usage_allowed": True,
            "rate_limit_reached_type": "hard_limit",
            "primary": {"used_percent": 100},
        }))
        self.assertFalse(codex_accounts.quota_available({
            "ok": True,
            "ordinary_usage_allowed": False,
            "primary": {"used_percent": 10},
        }))

    def test_quota_availability_is_conservative_without_explicit_flag(self) -> None:
        self.assertFalse(codex_accounts.quota_available({
            "ok": True,
            "ordinary_usage_allowed": None,
            "rate_limit_reached_type": "hard_limit",
        }))
        self.assertFalse(codex_accounts.quota_available({
            "ok": True,
            "ordinary_usage_allowed": None,
            "rate_limit_reached_type": None,
            "primary": {"used_percent": 100},
            "secondary": {"used_percent": 25},
        }))
        self.assertTrue(codex_accounts.quota_available({
            "ok": True,
            "ordinary_usage_allowed": None,
            "rate_limit_reached_type": None,
            "primary": {"used_percent": 50},
            "secondary": {"used_percent": 25},
        }))
        self.assertIsNone(codex_accounts.quota_available({"ok": False, "error": "timeout"}))


if __name__ == "__main__":
    unittest.main()
