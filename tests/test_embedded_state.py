from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.embedded_state import FORMAT_VERSION, parse
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.scientific_model import load as load_model
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap

HUMAN = (
    "# Goal\nG\n\n"
    "# Research Contract\nC\n\n"
    "# Final Gate\nDone when F is established.\n\n"
    "# Required Context\n- stable/context.md\n"
)


class EmbeddedStateTest(unittest.TestCase):
    def new_task(self, base: Path, name: str = "goal") -> TaskSpec:
        root = base / name
        root.mkdir()
        plan = root / "research_plan.md"
        plan.write_text(HUMAN, encoding="utf-8")
        binding = bootstrap(plan)
        return TaskSpec.from_config(binding.config_path)

    def test_new_task_has_pure_markdown_model_and_external_machine_state(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.new_task(Path(temp))
            self.assertEqual(spec.final_gate, "Done when F is established.")
            text = spec.document.read_text(encoding="utf-8")
            self.assertEqual(text.count("# Scientific Model\n"), 1)
            self.assertNotIn("# Frontier State", text)
            self.assertNotIn("theory-master-state:", text)
            self.assertNotIn('"schema"', text)
            self.assertEqual(parse(text).summary, "")
            self.assertTrue((spec.research_root / "scientific_model_state.json").is_file())
            self.assertFalse((spec.research_root / "frontier_state.json").exists())

    def test_frontier_save_never_rewrites_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.new_task(Path(temp))
            before = spec.document.read_bytes()
            database = spec.branch_canvas.resolve().parent / "science_graph.sqlite3"
            with ScienceGraphStore(database) as store:
                store.add_hypothesis("H-0001", title="A bounded hypothesis", summary="主张：检验命题；条件：固定范围；状态：待检验。")
                store.set_frontier(
                    branch_id="frontier:H-0001", hypothesis_id="H-0001",
                    status="active", reason="next scientific frontier", preferred=True,
                )
                row = store.db.execute(
                    "SELECT status FROM frontier_branches WHERE hypothesis_id='H-0001'"
                ).fetchone()
                self.assertEqual(row["status"], "active")
            self.assertEqual(spec.document.read_bytes(), before)

    def test_old_embedded_payload_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.new_task(Path(temp))
            text = spec.document.read_text(encoding="utf-8")
            polluted = text.replace(
                "尚未形成稳定的科学总结。",
                'Current summary.\n\n<!-- theory-master-state:scientific-model/v3\n{"schema":"old"}\n-->',
                1,
            )
            spec.document.write_text(polluted, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pure Markdown"):
                TaskSpec.from_config(spec.config_path)

    def test_human_markdown_edit_is_the_scientific_content(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = self.new_task(Path(temp))
            text = spec.document.read_text(encoding="utf-8")
            spec.document.write_text(
                text.replace("尚未形成稳定的科学总结。", "当前最新科学认识。", 1),
                encoding="utf-8",
            )
            model = load_model(spec.research_root, goal_id=spec.goal_id)
            self.assertEqual(model["summary"], "当前最新科学认识。")


if __name__ == "__main__":
    unittest.main()
