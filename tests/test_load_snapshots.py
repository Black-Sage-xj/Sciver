from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.load_snapshots import create_load_snapshot
from theory_master.scientific_model import commit_authored_model
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap


class LoadSnapshotTest(unittest.TestCase):
    def test_each_load_snapshots_markdown_plan_and_machine_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\nG\n\n# Research Contract\nC\n\n"
                "# Final Gate\nF\n\n# Required Context\n- none\n",
                encoding="utf-8",
            )
            binding = bootstrap(plan)
            spec = TaskSpec.from_config(binding.config_path)
            commit_authored_model(
                spec.research_root, {"summary": "Scientific Model v1."},
                cycle_id="1", action="replace",
            )
            first = create_load_snapshot(spec, reason="resume")
            commit_authored_model(
                spec.research_root, {"summary": "Scientific Model v2."},
                cycle_id="2", action="replace",
            )
            second = create_load_snapshot(spec, reason="resume")

            self.assertNotEqual(first, second)
            self.assertIn("Scientific Model v1.", (first / "research_plan.md").read_text(encoding="utf-8"))
            self.assertIn("Scientific Model v2.", (second / "research_plan.md").read_text(encoding="utf-8"))
            first_meta = json.loads((first / "scientific_model_state.json").read_text(encoding="utf-8"))
            second_meta = json.loads((second / "scientific_model_state.json").read_text(encoding="utf-8"))
            self.assertEqual(first_meta["version"], 1)
            self.assertEqual(second_meta["version"], 2)
            manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["scientific_model_version"], 2)


if __name__ == "__main__":
    unittest.main()
