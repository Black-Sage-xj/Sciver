from __future__ import annotations

import unittest
from pathlib import Path

from suite_policy import (
    CATEGORIES,
    INVARIANT_TEST_IDS,
    MODULE_CATEGORIES,
    categories_for_test_id,
    registry_errors,
)
import test_core as core_tests


class TestSuitePolicy(unittest.TestCase):
    def test_every_test_module_has_exactly_one_primary_category(self):
        root = Path(__file__).resolve().parent
        self.assertEqual(registry_errors(root), [])
        self.assertTrue(MODULE_CATEGORIES)
        self.assertTrue(set(MODULE_CATEGORIES.values()) <= set(CATEGORIES))

    def test_core_invariant_overlay_is_explicit_and_architecture_owned(self):
        self.assertEqual(MODULE_CATEGORIES["test_core"], "architecture")
        self.assertTrue(INVARIANT_TEST_IDS)
        for test_id in INVARIANT_TEST_IDS:
            with self.subTest(test_id=test_id):
                self.assertTrue(test_id.startswith("test_core."))
                self.assertEqual(
                    categories_for_test_id(test_id),
                    frozenset({"architecture", "invariant"}),
                )

    def test_invariant_overlay_points_to_real_core_tests(self):
        prefix = "test_core.TheoryMasterTest."
        for test_id in INVARIANT_TEST_IDS:
            with self.subTest(test_id=test_id):
                self.assertTrue(test_id.startswith(prefix))
                method_name = test_id.removeprefix(prefix)
                self.assertTrue(hasattr(core_tests.TheoryMasterTest, method_name))

    def test_architecture_test_does_not_silently_become_invariant(self):
        test_id = "test_abc_pipeline.ABCPipelineTest.test_specialist_a_has_no_machine_status_fields"
        self.assertEqual(categories_for_test_id(test_id), frozenset({"architecture"}))

    def test_regression_and_ui_categories_are_not_invariant_by_default(self):
        self.assertEqual(
            categories_for_test_id("test_tui.TuiTest.test_placeholder"),
            frozenset({"ui"}),
        )


if __name__ == "__main__":
    unittest.main()
