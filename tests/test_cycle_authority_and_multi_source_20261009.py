"""SQLite-authoritative Cycle ownership, multi-parent lineage and Goal cache tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.core import Workflow
from theory_master.science_graph_store import ScienceGraphStore


class MultiSourceSQLiteTest(unittest.TestCase):
    def test_multiple_parents_are_preserved_without_closing_source_frontiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ScienceGraphStore(Path(tmp) / "science_graph.sqlite3") as store:
                for ident in ("H-0001", "H-0002", "H-0003"):
                    store.add_hypothesis(ident, title=ident)
                store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
                store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-0002")
                store.register_cycle_hypothesis(cycle_id="3", hypothesis_id="H-0003")
                store.add_evolution(edge_id="H-0001-H-0003", source="H-0001", target="H-0003", operator="combine")
                store.add_evolution(edge_id="H-0002-H-0003", source="H-0002", target="H-0003", operator="combine")
                graph = store.graph_view()
                parents = {edge["source_hypothesis"] for edge in graph["edges"]
                           if edge["target_hypothesis"] == "H-0003"}
                self.assertEqual(parents, {"H-0001", "H-0002"})
                for ident in ("H-0001", "H-0002"):
                    row = store.db.execute(
                        "SELECT status FROM frontier_branches WHERE hypothesis_id=?", (ident,),
                    ).fetchone()
                    self.assertEqual(row["status"], "active")

    def test_cycle_ownership_prevents_reusing_a_stale_h_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ScienceGraphStore(Path(tmp) / "science_graph.sqlite3") as store:
                store.add_hypothesis("H-0001", title="Old proposition")
                store.add_hypothesis("H-0002", title="Current proposition")
                store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
                store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-0002")
                with self.assertRaisesRegex(ValueError, "already owned"):
                    store.register_cycle_hypothesis(cycle_id="3", hypothesis_id="H-0001")
                owner = store.db.execute(
                    "SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id='H-0001'"
                ).fetchone()
                self.assertEqual(owner["cycle_id"], "1")


class GoalAuthoritySQLiteTest(unittest.TestCase):
    def test_goal_completion_digest_uses_plan_and_sqlite_not_canvas_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "research_plan.md"
            canvas = root / "exploration.canvas"
            plan.write_text("# Goal\nOriginal gate\n", encoding="utf-8")
            canvas.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
            database = root / "science_graph.sqlite3"
            with ScienceGraphStore(database) as store:
                store.add_hypothesis("H-0001", title="Scientific proposition")
                store.set_frontier(
                    branch_id="frontier:H-0001", hypothesis_id="H-0001",
                    status="closed", reason="Goal completion verified", preferred=False,
                )
            workflow = Workflow.__new__(Workflow)
            workflow.spec = type("Spec", (), {
                "document": plan, "branch_canvas": canvas,
                "research_root": root, "task_mode": True,
            })()
            workflow.branch_canvas = canvas
            cycle = root / "1"
            cycle.mkdir()
            state = {"goal_completion": {"verdict": "passed", "source_cycle": "1",
                                          "authority_digest": workflow._goal_authority_digest()}}
            (cycle / "research_state.json").write_text(json.dumps(state), encoding="utf-8")
            (cycle / "goal_completion_out.json").write_text(json.dumps({"verdict": "passed"}), encoding="utf-8")
            with patch("theory_master.specification.latest_cycle_dir", return_value=cycle):
                self.assertIsNotNone(workflow._latest_goal_completion_state())
                canvas.write_text(json.dumps({"nodes": [{"id": "display-only-change"}], "edges": []}), encoding="utf-8")
                self.assertIsNotNone(workflow._latest_goal_completion_state())
                plan.write_text("# Goal\nChanged gate\n", encoding="utf-8")
                self.assertIsNone(workflow._latest_goal_completion_state())
                plan.write_text("# Goal\nOriginal gate\n", encoding="utf-8")
                with ScienceGraphStore(database) as store:
                    store.add_hypothesis("H-0002", title="New active branch")
                self.assertIsNone(workflow._latest_goal_completion_state())


class NormalCycleBoundaryEquivalenceTest(unittest.TestCase):
    @staticmethod
    def _spec(root: Path, cycles: int):
        from theory_master.core import TaskSpec
        plan = root / "research_plan.md"
        plan.write_text("# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nReproducible conclusion\n\n# Required Context\n\n- r\n", encoding="utf-8")
        spec = TaskSpec.load(plan)
        config = json.loads(spec.config_path.read_text(encoding="utf-8"))
        config["max_cycles"] = cycles
        config["max_judge_reworks_per_cycle"] = 0
        spec.config_path.write_text(json.dumps(config), encoding="utf-8")
        return TaskSpec.from_config(spec.config_path)

    def test_continuous_run_separate_run_and_master_resume(self):
        from theory_master.core import FakeBackend
        results = []
        for launch in ("continuous", "separate_run", "master_resume"):
            with self.subTest(launch=launch), tempfile.TemporaryDirectory() as tmp:
                spec = self._spec(Path(tmp), 2 if launch == "continuous" else 1)
                if launch == "continuous":
                    Workflow(spec, FakeBackend(reworks=0)).run()
                else:
                    Workflow(spec, FakeBackend(reworks=0)).run()
                    # A legacy global Frontier cache with a bogus pointer must not affect science.
                    (spec.research_root / "frontier_state.json").write_text(
                        json.dumps({"continuation_anchor_decision_id": "bogus"}), encoding="utf-8")
                    if launch == "separate_run":
                        Workflow(spec, FakeBackend(reworks=0)).run()
                    else:
                        Workflow(spec, FakeBackend(reworks=0)).resume(spec.master_id)
                first = json.loads((spec.research_root / "1" / "research_state.json").read_text())
                second = json.loads((spec.research_root / "2" / "research_state.json").read_text())
                self.assertEqual(first["status"], "over")
                self.assertEqual(second["status"], "over")
                provenance = second["hypothesis_lock"]["candidate_provenance"]
                results.append((second["hypothesis_lock"]["id"],
                                provenance.get("lineage_anchor_node_id"),
                                provenance.get("evolution_operator")))
        self.assertEqual(results, [results[0]] * 3)


if __name__ == "__main__":
    unittest.main()
