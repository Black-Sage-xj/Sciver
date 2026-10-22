from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.scientific_model import (
    SCIENTIFIC_MODEL_SCHEMA,
    authored_content,
    commit_authored_model,
    load,
)
from theory_master.task import bootstrap


def make_store(base: Path):
    root = base / "task"
    root.mkdir()
    plan = root / "research_plan.md"
    plan.write_text(
        "# Goal\nG\n\n"
        "# Research Contract\nC\n\n"
        "# Final Gate\nF\n\n"
        "# Required Context\n- stable/context.md\n",
        encoding="utf-8",
    )
    return bootstrap(plan)


class ScientificModelStoreTest(unittest.TestCase):
    def test_initial_model_is_current_summary_only_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = make_store(Path(temp))
            model = load(binding.research_root, goal_id=binding.goal_id)
            self.assertEqual(model["schema"], SCIENTIFIC_MODEL_SCHEMA)
            self.assertEqual(model["summary"], "")
            self.assertEqual(authored_content(model), {"summary": ""})
            self.assertNotIn("established_knowledge", model)
            self.assertNotIn("current_frontier", model)

    def test_replace_rewrites_one_summary_and_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = make_store(Path(temp))
            updated = commit_authored_model(
                binding.research_root,
                {"summary": "Current result.   One unresolved boundary."},
                cycle_id="1",
                action="replace",
            )
            self.assertEqual(updated["version"], 1)
            self.assertEqual(updated["summary"], "Current result. One unresolved boundary.")
            text = binding.config["plan_path"]
            self.assertIn("Current result. One unresolved boundary.", Path(text).read_text(encoding="utf-8"))

    def test_keep_is_exact_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = make_store(Path(temp))
            initial = load(binding.research_root, goal_id=binding.goal_id)
            kept = commit_authored_model(
                binding.research_root, None, cycle_id="1", action="keep"
            )
            self.assertEqual(kept, initial)

    def test_old_claim_ledger_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = make_store(Path(temp))
            with self.assertRaisesRegex(ValueError, "exactly one field: summary"):
                commit_authored_model(
                    binding.research_root,
                    {"established_knowledge": [], "current_scientific_picture": "old"},
                    cycle_id="1",
                    action="replace",
                )

    def test_commit_writes_append_only_version_history(self):
        with tempfile.TemporaryDirectory() as temp:
            binding = make_store(Path(temp))
            commit_authored_model(
                binding.research_root,
                {"summary": "Durable current synthesis."},
                cycle_id="1",
                action="replace",
            )
            history = binding.research_root / "scientific_model_history"
            self.assertTrue((history / "v1.json").is_file())
            self.assertTrue(any((history / "updates").glob("v1--1--*.json")))


if __name__ == "__main__":
    unittest.main()
