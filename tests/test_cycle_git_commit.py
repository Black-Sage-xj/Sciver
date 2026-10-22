from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.cycle_git_commit import ENV_SWITCH, commit_cycle_dir


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


class CycleGitCommitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / ".gitignore").write_text("*\n!**/\n!**/*.py\n!**/*.md\n!.gitignore\n**/replay_checkpoints/**\n")
        (self.repo / "base.md").write_text("base\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.cycle = self.repo / "tasks" / "research" / "7"
        (self.cycle / "workspace").mkdir(parents=True)
        (self.cycle / "replay_checkpoints").mkdir()
        (self.cycle / "workspace" / "analysis.py").write_text("print(1)\n")
        (self.cycle / "report.md").write_text("# report\n")
        (self.cycle / "workspace" / "data.npz").write_bytes(b"\0" * 64)
        (self.cycle / "replay_checkpoints" / "mirror.md").write_text("copy\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_commits_only_cycle_text_and_leaves_other_staged_paths(self) -> None:
        (self.repo / "other.py").write_text("x = 1\n")
        _git(self.repo, "add", "other.py")
        with patch.dict(os.environ, {ENV_SWITCH: "1"}):
            result = commit_cycle_dir(self.cycle, status="OVER")
        self.assertEqual(result["result"], "committed")
        committed = set(_git(self.repo, "show", "--name-only", "--format=", "HEAD").split())
        self.assertEqual(committed, {"tasks/research/7/report.md", "tasks/research/7/workspace/analysis.py"})
        self.assertIn("other.py", _git(self.repo, "diff", "--cached", "--name-only"))
        with patch.dict(os.environ, {ENV_SWITCH: "1"}):
            self.assertEqual(commit_cycle_dir(self.cycle, status="OVER")["result"], "nothing_to_commit")

    def test_disabled_and_non_repo_are_noops(self) -> None:
        head = _git(self.repo, "rev-parse", "HEAD")
        with patch.dict(os.environ, {ENV_SWITCH: "0"}):
            self.assertEqual(commit_cycle_dir(self.cycle)["result"], "disabled")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), head)
        with tempfile.TemporaryDirectory() as plain, patch.dict(os.environ, {ENV_SWITCH: "1"}):
            self.assertEqual(commit_cycle_dir(Path(plain))["result"], "skipped")
        self.assertEqual(commit_cycle_dir(None)["result"], "skipped")


if __name__ == "__main__":
    unittest.main()
