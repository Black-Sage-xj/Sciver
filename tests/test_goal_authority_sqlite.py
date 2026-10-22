"""SQLite-backed Goal Completion authority must ignore display-only Canvas changes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.core import Workflow
from theory_master.science_graph_store import ScienceGraphStore

class GoalAuthoritySQLiteTest(unittest.TestCase):
    def test_goal_completion_fingerprint_uses_plan_and_sqlite_not_canvas_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "research_plan.md"
            canvas = root / "exploration.canvas"
            plan.write_text("# Goal\nOriginal gate\n", encoding="utf-8")
            canvas.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
            with ScienceGraphStore(root / "science_graph.sqlite3") as store:
                store.add_hypothesis("H-0001", title="命题")
                store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                                   status="closed", reason="Goal completion verified", preferred=False)
            workflow = Workflow.__new__(Workflow)
            workflow.spec = SimpleNamespace(document=plan, branch_canvas=canvas,
                                            research_root=root, task_mode=True)
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
                with ScienceGraphStore(root / "science_graph.sqlite3") as store:
                    store.add_hypothesis("H-0002", title="New active branch")
                self.assertIsNone(workflow._latest_goal_completion_state())
