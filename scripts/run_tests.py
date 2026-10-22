#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import unittest
from collections import Counter
from pathlib import Path

THEORY_MASTER_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = THEORY_MASTER_ROOT / "tests"

for path in (THEORY_MASTER_ROOT, TESTS_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from suite_policy import (
    CATEGORIES,
    INVARIANT_TEST_IDS,
    MODULE_CATEGORIES,
    categories_for_test_id,
    registry_errors,
)


def iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def load_all_tests(
    *, tests_root: Path = TESTS_ROOT,
) -> tuple[list[unittest.TestCase], list[str]]:
    # A fresh loader exposes import failures that unittest otherwise represents
    # as synthetic _FailedTest instances. Do not lose those failures in category
    # filtering (which cannot classify a module that failed to import).
    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_root), pattern="test_*.py")
    return list(iter_tests(suite)), list(loader.errors)


def discovery_failures(tests: list[unittest.TestCase]) -> list[unittest.TestCase]:
    return [test for test in tests if test.id().startswith("unittest.loader._FailedTest.")]


def print_import_diagnostics(errors: list[str]) -> None:
    for error in errors:
        lines = [line.strip() for line in error.splitlines() if line.strip()]
        if lines:
            print(
                f"IMPORT_OR_DISCOVERY_ERROR: {lines[0]} | {lines[-1]}",
                file=sys.stderr,
            )
    if errors:
        print(
            "Import/discovery errors may be missing interpreter dependencies or "
            "broken application imports; inspect the exception before assigning "
            "ENVIRONMENT_ERROR versus REAL_BUG.",
            file=sys.stderr,
        )


def filtered_suite(tests: list[unittest.TestCase], category: str) -> unittest.TestSuite:
    if category == "all":
        return unittest.TestSuite(
            test for test in tests if not test.id().startswith("unittest.loader._FailedTest.")
        )
    return unittest.TestSuite(
        test for test in tests if category in categories_for_test_id(test.id())
    )


def print_inventory(tests: list[unittest.TestCase]) -> None:
    primary_counts = Counter()
    invariant_overlay = 0
    for test in tests:
        module_name = test.id().split(".", 1)[0]
        if module_name not in MODULE_CATEGORIES:
            continue  # A synthetic failed-import test, reported separately.
        primary_counts[MODULE_CATEGORIES[module_name]] += 1
        if (
            MODULE_CATEGORIES[module_name] != "invariant"
            and "invariant" in categories_for_test_id(test.id())
        ):
            invariant_overlay += 1

    print(f"total loaded: {len(tests) - len(discovery_failures(tests))}")
    print(f"discovery/import failures: {len(discovery_failures(tests))}")
    for category in CATEGORIES:
        print(f"{category}: {primary_counts[category]}")
    if invariant_overlay:
        print(f"invariant overlay from mixed modules: {invariant_overlay}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Sciver tests by semantic test-architecture category."
    )
    parser.add_argument(
        "--category",
        choices=("all",) + CATEGORIES,
        default="all",
        help="test lens to run; default: all",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="show category inventory without running tests",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="use compact unittest output",
    )
    args = parser.parse_args()

    errors = registry_errors(TESTS_ROOT)
    if errors:
        for error in errors:
            print(f"test architecture error: {error}", file=sys.stderr)
        return 2

    tests, import_errors = load_all_tests(tests_root=TESTS_ROOT)
    print_import_diagnostics(import_errors)
    known_test_ids = {test.id() for test in tests}
    # A failed module import cannot prove that its invariant IDs were deleted.
    stale_invariant_ids = sorted(INVARIANT_TEST_IDS - known_test_ids) if not import_errors else []
    if stale_invariant_ids:
        for test_id in stale_invariant_ids:
            print(f"test architecture error: stale invariant test id: {test_id}", file=sys.stderr)
        return 2
    if args.list:
        print_inventory(tests)
        return 1 if import_errors else 0

    suite = filtered_suite(tests, args.category)
    selected = suite.countTestCases()
    print(
        f"TheoryMaster test category={args.category} selected={selected} "
        f"import_errors={len(import_errors)}", flush=True,
    )
    runner = unittest.TextTestRunner(verbosity=1 if args.quiet else 2)
    result = runner.run(suite)
    print(
        f"RESULT category={args.category} executed={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"skipped={len(result.skipped)} import_errors={len(import_errors)}",
        flush=True,
    )
    # Filtering a category must never silently hide failures to import another
    # registered test module. A green gate requires complete suite discovery.
    return 0 if result.wasSuccessful() and not import_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
