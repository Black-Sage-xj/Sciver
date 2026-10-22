"""MW-A: Goal Judge runs under the exclusive barrier; SQLite seal precedes projection."""
from __future__ import annotations

import fcntl
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.goal_terminal import goal_sealed
from theory_master.science_graph_store import GOAL_COMPLETED_STOP, ScienceGraphStore
from theory_master.workflow_goal import GoalCompletionMixin


def _barrier_mode(root: Path) -> str:
    """Probe the barrier from a fresh file description in another thread."""
    path = root / ".theory-master" / "science_commit.lock"
    result: list[str] = []

    def probe():
        with open(path, "a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                result.append("exclusive")
                return
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            result.append("free_or_shared")

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(10)
    return result[0]


class Harness(GoalCompletionMixin):
    def __init__(self, root: Path, verdict: str = "passed"):
        plan = root / "research_plan.md"
        plan.write_text("# Goal\nFinal Gate\n", encoding="utf-8")
        canvas = root / "exploration.canvas"
        canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        self.spec = SimpleNamespace(research_root=root, document=plan, branch_canvas=canvas,
                                    task_mode=True, goal_id="goal-1")
        self.branch_canvas = canvas
        self.cycle_dir = root / "1"
        self.cycle_dir.mkdir()
        (self.cycle_dir / ".theory-master-cycle").write_text("theory-master/v2\ntest\n", encoding="utf-8")
        self.verdict = verdict
        self.calls: list[dict] = []
        self.events: list[str] = []
        self.order: list[str] = []
        with ScienceGraphStore(self._graph_database_path()) as store:
            store.add_hypothesis("H-0001", title="supported")
            store.add_hypothesis("H-0002", title="running elsewhere")
            store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
            store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-0002")
            store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                                 cycle_id="1", verdict="supported")

    def _graph_database_path(self) -> Path:
        return self.spec.branch_canvas.with_name("science_graph.sqlite3")

    def _emit(self, state, kind, payload):
        self.events.append(kind)

    def _call(self, state, role, context, **_kw):
        self.calls.append({"role": role, "barrier": _barrier_mode(self.spec.research_root),
                           "sealed": goal_sealed(self.spec.research_root, self._graph_database_path())})
        return {"verdict": self.verdict, "rationale": "Final Gate satisfied"}

    def _close_open_frontier_tips(self):
        with ScienceGraphStore(self._graph_database_path(), readonly=True) as store:
            self.order.append("projection:sealed" if store.goal_terminal() else "projection:open")
        return super()._close_open_frontier_tips()


class GoalCompletionPathTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def run_gate(self, harness):
        state = {"status": "over"}
        return state, harness._run_goal_completion(state, {"verdict": "supported"})

    def test_judge_runs_under_exclusive_barrier_and_seal_precedes_projection(self):
        harness = Harness(self.root)
        state, result = self.run_gate(harness)
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual([c["barrier"] for c in harness.calls], ["exclusive"])
        self.assertIsNone(harness.calls[0]["sealed"])
        self.assertEqual(set(harness.order), {"projection:sealed"})
        self.assertEqual(_barrier_mode(self.root), "free_or_shared")
        self.assertEqual(state["goal_status"], "passed")
        self.assertEqual(state["phase"], "goal_complete")
        self.assertEqual(state["goal_completion"]["authority"], "sqlite")
        terminal = goal_sealed(self.root, harness._graph_database_path())
        self.assertEqual(terminal["source"], "sqlite")
        self.assertEqual(terminal["source_cycle"], "1")
        self.assertEqual(terminal["goal_id"], "goal-1")
        self.assertEqual(terminal["judge_receipt_ref"], "1/goal_completion_out.json")
        with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
            reasons = {r["hypothesis_id"]: r["reason"] for r in
                       store.db.execute("SELECT * FROM frontier_branches WHERE status='closed'")}
        self.assertEqual(reasons["H-0002"], GOAL_COMPLETED_STOP)
        self.assertIn("H-0001", reasons)
        canvas = json.loads(harness.branch_canvas.read_text(encoding="utf-8"))
        self.assertIn("nodes", canvas)
        self.assertIn("goal.completed", harness.events)
        self.assertIsNotNone(harness._latest_goal_completion_state())

    def test_projection_failure_keeps_seal_and_records_retryable_warning(self):
        harness = Harness(self.root)
        with patch("theory_master.science_canvas_export.canvas_projection",
                   side_effect=OSError("disk full")):
            state, result = self.run_gate(harness)
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual(state["goal_status"], "passed")
        warning = state["goal_projection_warnings"][0]
        self.assertTrue(warning["retryable"])
        self.assertIn("disk full", warning["error"])
        self.assertIn("goal.projection_failed", harness.events)
        self.assertIsNotNone(goal_sealed(self.root, harness._graph_database_path()))
        # Retrying the projection later succeeds without re-sealing.
        harness._close_open_frontier_tips()

    def test_already_sealed_goal_returns_passed_without_judge(self):
        harness = Harness(self.root)
        self.run_gate(harness)
        harness.calls.clear()
        state, result = self.run_gate(harness)
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual(result["source"], "sqlite")
        self.assertEqual(harness.calls, [])
        self.assertEqual(state["goal_status"], "passed")

    def test_not_passed_does_not_seal(self):
        harness = Harness(self.root, verdict="not_passed")
        state, result = self.run_gate(harness)
        self.assertEqual(result["verdict"], "not_passed")
        self.assertEqual(state["goal_status"], "active")
        self.assertEqual(harness.calls[0]["barrier"], "exclusive")
        self.assertIsNone(goal_sealed(self.root, harness._graph_database_path()))
        self.assertEqual(harness.order, [])

    def test_checkpoint_projection_after_seal_is_read_only(self):
        rendered: list[str] = []

        class Base:
            def _render_canvas(self, state):
                rendered.append("write_path")

        class Projecting(Harness, Base):
            pass

        harness = Projecting(self.root)
        state, _ = self.run_gate(harness)
        state["frontier_maintenance"] = {"updates": [
            {"hypothesis_id": "H-0002", "status": "active", "reason": "stale replay"}]}
        harness.order.clear()
        harness._render_canvas(state)
        self.assertEqual(rendered, [])
        self.assertEqual(set(harness.order), {"projection:sealed"})
        with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
            self.assertEqual(store.db.execute(
                "SELECT status FROM frontier_branches WHERE hypothesis_id='H-0002'").fetchone()[0], "closed")
        state["cycle_outcome_record"] = {"id": "cycle-outcome-late"}
        harness._render_canvas(state)
        self.assertEqual(rendered, ["write_path"])

    def test_legacy_verified_receipt_is_still_recognized(self):
        harness = Harness(self.root)
        from theory_master.workflow_goal import goal_authority_digest
        with ScienceGraphStore(harness._graph_database_path()) as store:
            store.close_all_frontiers_for_goal()
        digest = goal_authority_digest(harness.spec.document, harness._graph_database_path())
        (harness.cycle_dir / "research_state.json").write_text(json.dumps({
            "status": "over",
            "goal_completion": {"verdict": "passed", "source_cycle": "1", "authority_digest": digest},
        }), encoding="utf-8")
        (harness.cycle_dir / "goal_completion_out.json").write_text(
            json.dumps({"verdict": "passed"}), encoding="utf-8")
        graph = harness._graph_database_path()
        self.assertIsNone(goal_sealed(self.root, graph))
        legacy = goal_sealed(self.root, graph, spec=harness.spec)
        self.assertEqual(legacy["source"], "legacy_receipt")
        self.assertEqual(legacy["source_cycle"], "1")
        self.assertEqual(harness._latest_goal_completion_state()["goal_completion"]["source_cycle"], "1")
        state, result = self.run_gate(harness)
        self.assertEqual(result["source"], "legacy_receipt")
        self.assertEqual(harness.calls, [])


if __name__ == "__main__":
    unittest.main()
