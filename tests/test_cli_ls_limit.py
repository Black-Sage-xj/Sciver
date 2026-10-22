from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from theory_master.cli_discovery import print_runs, resumable_runs


class CliLsLimitTest(unittest.TestCase):
    def test_print_runs_shows_only_newest_twenty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(25):
                cycle = root / f"run-{index:02d}"
                cycle.mkdir()
                (cycle / "run_state.json").write_text(json.dumps({
                    "status": "FAILED",
                    "resumable": True,
                    "phase": "failed",
                    "execution_active": False,
                    "started_at": f"2026-10-04T{index % 24:02d}:{index:02d}:00+08:00",
                }), encoding="utf-8")

            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                self.assertEqual(print_runs(root), 0)

            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 21)  # header + 20 tasks
            self.assertIn("run-23", lines[1])
            self.assertNotIn("run-00", output.getvalue())

    def test_resumable_runs_limit_is_applied_after_sorting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, started_at in (
                ("old", "2026-10-01T00:00:00+08:00"),
                ("new", "2026-10-03T00:00:00+08:00"),
                ("middle", "2026-10-02T00:00:00+08:00"),
            ):
                cycle = root / name
                cycle.mkdir()
                (cycle / "run_state.json").write_text(json.dumps({
                    "status": "FAILED",
                    "resumable": True,
                    "phase": "failed",
                    "execution_active": False,
                    "started_at": started_at,
                }), encoding="utf-8")

            self.assertEqual(
                [item["resume_id"] for item in resumable_runs(root, limit=2)],
                ["new", "middle"],
            )


if __name__ == "__main__":
    unittest.main()
