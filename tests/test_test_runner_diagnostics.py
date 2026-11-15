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
import suite_policy


class TestRunnerDiagnosticsTests(unittest.TestCase):
    def test_invariant_overlay_selects_only_the_guard_from_mixed_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "test_overlay_" + root.name
            marker = root / "guard-ran"
            (root / (name + ".py")).write_text(
                "from pathlib import Path\nimport unittest\n"
                "class Mixed(unittest.TestCase):\n"
                f"    def test_guard(self): Path({str(marker)!r}).write_text('guard')\n"
                "    def test_architecture(self): raise AssertionError('must be deselected')\n",
                encoding="utf-8",
            )
            overlay = {name + ".Mixed.test_guard"}
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(run_tests, "INVARIANT_TEST_IDS", overlay),
                patch.object(suite_policy, "INVARIANT_TEST_IDS", overlay),
                patch.dict(run_tests.MODULE_CATEGORIES, {name: "architecture"}),
                patch.object(sys, "argv", ["run_tests.py", "--category", "invariant", "-q"]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(run_tests.main(), 0)
            self.assertEqual(marker.read_text(), "guard")

    def test_category_runner_executes_pytest_functions_and_unittest_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            name = "test_mixed_" + root.name
            (root / (name + ".py")).write_text(
                "from pathlib import Path\nimport unittest\n"
                f"MARKER = Path({str(marker)!r})\n"
                "def test_function(): MARKER.write_text('function\\n')\n"
                "class More(unittest.TestCase):\n"
                "    def test_method(self):\n"
                "        with MARKER.open('a') as stream: stream.write('method\\n')\n",
                encoding="utf-8",
            )
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(run_tests, "INVARIANT_TEST_IDS", set()),
                patch.dict(run_tests.MODULE_CATEGORIES, {name: "regression"}),
                patch.object(sys, "argv", ["run_tests.py", "--category", "regression", "-q"]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(run_tests.main(), 0)
            self.assertEqual(set(marker.read_text().splitlines()), {"function", "method"})

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
            good = f"test_one_pass_{root.name}"
            broken = f"test_one_broken_{root.name}"
            output, diagnostics = io.StringIO(), io.StringIO()
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(run_tests, "INVARIANT_TEST_IDS", set()),
                patch.dict(run_tests.MODULE_CATEGORIES, {good: "invariant", broken: "architecture"}),
                patch.object(sys, "argv", ["run_tests.py", "--category", "invariant", "-q"]),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(diagnostics),
            ):
                self.assertEqual(run_tests.main(), 1)
            self.assertIn("ModuleNotFoundError", output.getvalue() + diagnostics.getvalue())
            output, diagnostics = io.StringIO(), io.StringIO()
            with (
                patch.object(run_tests, "TESTS_ROOT", root),
                patch.object(run_tests, "registry_errors", return_value=[]),
                patch.object(run_tests, "INVARIANT_TEST_IDS", set()),
                patch.dict(run_tests.MODULE_CATEGORIES, {good: "invariant", broken: "architecture"}),
                patch.object(sys, "argv", ["run_tests.py", "--list"]),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(diagnostics),
            ):
                self.assertEqual(run_tests.main(), 1)
            self.assertIn("ModuleNotFoundError", output.getvalue() + diagnostics.getvalue())

    def test_inventory_is_compact_and_still_checks_complete_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "test_inventory_" + root.name
            (root / (name + ".py")).write_text(
                "def test_alpha(): raise AssertionError('inventory must not execute')\n"
                "def test_beta(): raise AssertionError('inventory must not execute')\n",
                encoding="utf-8",
            )
            for flags in (["--list"], ["--list", "-q"]):
                output = io.StringIO()
                with (
                    patch.object(run_tests, "TESTS_ROOT", root),
                    patch.object(run_tests, "registry_errors", return_value=[]),
                    patch.object(run_tests, "INVARIANT_TEST_IDS", set()),
                    patch.dict(run_tests.MODULE_CATEGORIES, {name: "regression"}),
                    patch.object(sys, "argv", ["run_tests.py", *flags]),
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(run_tests.main(), 0)
                self.assertIn("total loaded: 2", output.getvalue())
                self.assertIn("regression: 2", output.getvalue())
                self.assertIn("invariant overlay from mixed modules:", output.getvalue())
                self.assertNotIn(name, output.getvalue())
                self.assertNotIn("test_alpha", output.getvalue())


if __name__ == "__main__":
    unittest.main()
