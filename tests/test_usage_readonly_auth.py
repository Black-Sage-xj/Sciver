"""Usage recovery must never migrate a private runtime's authentication."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.observability.costs import codex_turn_usage


class ReadOnlyUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / "worker"
        self.worker.mkdir()
        self.auth = self.worker / "auth.json"
        self.auth.write_text('{"auth_mode":"chatgpt","tokens":{"account_id":"fake"}}')
        self.auth.chmod(0o600)
        self.before = self.auth.read_bytes()
        self.env = {"HOME": str(self.root),
                    "THEORYMASTER_WORKER_CODEX_HOME": str(self.worker),
                    "THEORYMASTER_CODEX_HOME": str(self.root / "other")}

    def session(self, home, amount=10):
        folder = home / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rollout-thread-1.jsonl").write_text("\n".join(map(json.dumps, [
            {"payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": amount, "cached_input_tokens": 4, "output_tokens": 3}}}},
            {"payload": {"type": "task_complete", "turn_id": "turn-1"}},
        ])) + "\n")

    def assert_auth_unchanged(self):
        self.assertFalse(self.auth.is_symlink())
        self.assertEqual(self.auth.read_bytes(), self.before)
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / ".codex-accounts").exists())

    def test_private_home_and_repeated_completion_reads_preserve_auth(self):
        self.session(self.worker)
        self.session(self.root / "other", amount=999)
        with patch.dict(os.environ, self.env), patch(
                "theory_master.codex_accounts.ensure_layout",
                side_effect=AssertionError("observability initialized accounts")):
            for _cycle in range(3):
                usage = codex_turn_usage("thread-1", "turn-1")
                self.assertEqual(usage["input_tokens"], 10)
                self.assertEqual(usage["cached_input_tokens"], 4)
                self.assert_auth_unchanged()

    def test_missing_worker_sessions_do_not_initialize_or_fall_back(self):
        self.session(self.root / "other")
        with patch.dict(os.environ, self.env):
            self.assertIsNone(codex_turn_usage("thread-1", "turn-1"))
        self.assert_auth_unchanged()

    def test_normal_runtime_path_is_read_without_account_setup(self):
        self.session(self.root / "other", amount=20)
        with patch.dict(os.environ, {**self.env, "THEORYMASTER_WORKER_CODEX_HOME": ""}), \
             patch("theory_master.codex_accounts.ensure_layout", side_effect=AssertionError):
            self.assertEqual(codex_turn_usage("thread-1", "turn-1")["input_tokens"], 20)
        self.assert_auth_unchanged()

    def test_explicit_sessions_root_wins_over_worker(self):
        self.session(self.worker)
        other = self.root / "explicit"
        self.session(other, amount=30)
        with patch.dict(os.environ, self.env):
            self.assertEqual(codex_turn_usage("thread-1", "turn-1", sessions_root=other / "sessions")["input_tokens"], 30)
        self.assert_auth_unchanged()


if __name__ == "__main__":
    unittest.main()
