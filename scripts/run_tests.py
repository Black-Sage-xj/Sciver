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


class CategoryCollection:
    """Apply semantic categories after pytest has collected every module.

    Collection must include function-based pytest cases and must expose import
    errors even when the affected module is outside the requested category.
    """

    def __init__(self, category: str, inventory: bool = False):
        self.category = category
        self.inventory = inventory

    @staticmethod
    def test_id(item) -> str:
        return ".".join((Path(item.path).stem, *item.nodeid.split("::")[1:]))

    def pytest_collection_modifyitems(self, session, config, items):
        known_ids = {self.test_id(item) for item in items}
        if not session.testsfailed:
            stale = sorted(INVARIANT_TEST_IDS - known_ids)
            if stale:
                import pytest
                raise pytest.UsageError("stale invariant test ids: " + ", ".join(stale))
        if self.inventory:
            counts = Counter(MODULE_CATEGORIES.get(Path(item.path).stem) for item in items)
            invariant_overlay = sum(
                MODULE_CATEGORIES.get(Path(item.path).stem) != "invariant"
                and "invariant" in categories_for_test_id(self.test_id(item))
                for item in items
            )
            print(f"total loaded: {len(items)} (pytest functions and unittest cases)")
            for category in CATEGORIES:
                print(f"{category}: {counts[category]}")
            print(f"invariant overlay from mixed modules: {invariant_overlay}")
        if self.category != "all":
            selected, deselected = [], []
            for item in items:
                target = selected if self.category in categories_for_test_id(self.test_id(item)) else deselected
                target.append(item)
            items[:] = selected
            config.hook.pytest_deselected(items=deselected)


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
        help="use compact pytest output",
    )
    args = parser.parse_args()

    errors = registry_errors(TESTS_ROOT)
    if errors:
        for error in errors:
            print(f"test architecture error: {error}", file=sys.stderr)
        return 2

    try:
        import pytest
    except ImportError:
        print('Install test dependencies with: python -m pip install -e ".[test]"', file=sys.stderr)
        return 2
    web_root = str(THEORY_MASTER_ROOT / "web")
    if web_root not in sys.path:
        sys.path.insert(0, web_root)
    command = [str(TESTS_ROOT), "-q" if args.quiet else "-ra", "--tb=short"]
    if args.list:
        command += ["--collect-only", "-q"]
    plugins = [CategoryCollection(args.category, args.list)]
    if args.list:
        class CompactInventory:
            @pytest.hookimpl(wrapper=True, tryfirst=True)
            def pytest_collection_finish(self, session):
                # Keep complete discovery and its error reports, but suppress
                # pytest's per-test listing after our category inventory. The
                # original selection is restored before pytest computes exit.
                collected = session.items
                session.items = []
                try:
                    return (yield)
                finally:
                    session.items = collected

        plugins.append(CompactInventory())
    print(f"Sciver test category={args.category}", flush=True)
    result = pytest.main(command, plugins=plugins)
    # Keep the CLI's failure convention, including import/collection errors and
    # accidental empty selections. Pytest's full diagnostics remain visible.
    return 0 if result == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
