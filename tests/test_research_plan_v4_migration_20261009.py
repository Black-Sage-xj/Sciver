"""TMA-0001 safe positive/negative fixtures for current Research Plan contract."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from theory_master.specification import TaskSpec, scientific_sections
from theory_master.task import bootstrap


VALID = (
    "# Goal\nPredict one synthetic response.\n\n"
    "# Research Contract\nUse only local source data.\n\n"
    "# Final Gate\nIndependent prediction is reproducible.\n\n"
    "# Required Context\n- local source fixture\n\n"
    "# Scientific Model\nSee the SDK-maintained scientific model.\n"
)


class ResearchPlanV4MigrationTest(unittest.TestCase):
    def test_canonical_five_sections_are_authoritative_without_yaml(self):
        goal, contract, gate, context = scientific_sections(VALID)
        self.assertIn("synthetic response", goal)
        self.assertIn("local source", contract)
        self.assertIn("Independent prediction", gate)
        self.assertIn("source fixture", context)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = root / "research_plan.md"
            plan.write_text(VALID)
            spec = TaskSpec.from_config(bootstrap(plan).config_path)
            self.assertEqual(spec.goal, goal)
            self.assertEqual(spec.document, plan.resolve())
            self.assertTrue(spec.config_path.is_file())
            self.assertEqual(TaskSpec.load(plan).goal_id, spec.goal_id)

    def test_legacy_frontmatter_does_not_gain_implicit_authority(self):
        legacy = "---\nschema: theory-master/v2\n---\n" + VALID
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            scientific_sections(legacy)
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "research_plan.md"
            plan.write_text(legacy)
            with self.assertRaisesRegex(ValueError, "legacy frontmatter"):
                TaskSpec.load(plan)

    def test_missing_final_gate_is_not_a_relaxed_legacy_compatibility(self):
        missing = VALID.replace("# Final Gate\nIndependent prediction is reproducible.\n\n", "")
        with self.assertRaisesRegex(ValueError, "top-level headings"):
            scientific_sections(missing)


if __name__ == "__main__":
    unittest.main()
