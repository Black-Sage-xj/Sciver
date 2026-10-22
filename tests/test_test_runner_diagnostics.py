"""Regression checks for categorical test runner discovery and failure honesty."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import run_tests


class TestRunnerDiagnosticsTests(unittest.TestCase):
    def make_cases(self, root: Path) -> None:
        (root / f"test_one_pass_{root.name}.py").write_text(
            "import unittest\nclass Good(unittest.TestCase):\n"
            "    def test_pass(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (root / f"test_one_broken_{root.name}.py").write_text(
            "import deliberately_missing_test_dependency_xyz\n",
            encoding="utf-8",
        )

    def test_discovery_preserves_real_tests_and_import_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_cases(root)
            tests, errors = run_tests.load_all_tests(tests_root=root)
            self.assertEqual(len(errors), 1)
            self.assertEqual(len(run_tests.discovery_failures(tests)), 1)
            self.assertEqual(len(tests), 2)
            self.assertEqual(run_tests.filtered_suite(tests, "all").countTestCases(), 1)
            summary, diagnostics = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(summary):
                run_tests.print_inventory(tests)
            with contextlib.redirect_stderr(diagnostics):
                run_tests.print_import_diagnostics(errors)
            self.assertIn("total loaded: 1", summary.getvalue())
            self.assertIn("discovery/import failures: 1", summary.getvalue())
            self.assertIn("ModuleNotFoundError", diagnostics.getvalue())

    def test_filtering_does_not_claim_green_when_another_module_fails_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_cases(root)
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(sys, "argv", ["run_tests.py", "--category", "invariant", "-q"]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(run_tests.main(), 1)
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(sys, "argv", ["run_tests.py", "--list"]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(run_tests.main(), 1)


if __name__ == "__main__":
    unittest.main()
