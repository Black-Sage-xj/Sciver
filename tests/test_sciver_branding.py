"""Offline safeguards for Sciver branding and legacy runtime compatibility."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from theory_master.task import (
    SYSTEM_CONFIG_NAME,
    TASK_DIR_NAME,
    TASK_SCHEMA,
)

ROOT = Path(__file__).resolve().parents[1]


class SciverBrandingTest(unittest.TestCase):
    def test_distribution_and_legacy_console_entry_points(self):
        content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for line in (
            'name = "sciver"',
            'sciver = "theory_master.__main__:main"',
            'theory_master = "theory_master.__main__:main"',
            'TheoryMaster = "theory_master.__main__:main"',
            'tm = "theory_master.__main__:main"',
        ):
            self.assertIn(line, content)

    def test_python_module_help_has_sciver_brand(self):
        result = subprocess.run(
            [sys.executable, "-m", "theory_master", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^usage: sciver ")
        self.assertIn("resume", result.stdout)

    def test_persisted_task_identifiers_are_unchanged(self):
        self.assertEqual(TASK_DIR_NAME, ".theory-master")
        self.assertEqual(SYSTEM_CONFIG_NAME, "theory-master.config.json")
        self.assertEqual(TASK_SCHEMA, "theory-master/task/v2")


if __name__ == "__main__":
    unittest.main()
