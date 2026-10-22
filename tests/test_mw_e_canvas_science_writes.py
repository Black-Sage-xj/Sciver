"""MW-E: Canvas/SQLite science writes use barrier -> Canvas lock -> Cycle claim fence.

Covers I-07 (late writes after the Goal seal fail) and idempotent Cycle Outcomes.
"""
from __future__ import annotations

import fcntl
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.canvas_projection import CanvasProjectionMixin, CycleOutcomeConflictError
from theory_master.goal_terminal import GoalSealedError, science_commit_barrier
from theory_master.multi_worker import CycleClaims
from theory_master.science_graph_store import ScienceGraphStore


def _barrier_state(root: Path) -> str:
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
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                result.append("shared")
                return
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            result.append("free")

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(10)
    return result[0]


class Harness(CanvasProjectionMixin):
    def __init__(self, root: Path, cycle: str):
        canvas = root / "exploration.canvas"
        if not canvas.exists():
            canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        self.spec = SimpleNamespace(research_root=root, branch_canvas=canvas, notes_mirror_dir=None)
        self.branch_canvas = canvas
        self.cycle_dir = root / cycle
        self.cycle_dir.mkdir(exist_ok=True)
        self.barrier_seen: list[str] = []

    def _graph_database_path(self) -> Path:
        return self.spec.branch_canvas.with_name("science_graph.sqlite3")


def _seed(root: Path) -> Harness:
    harness = Harness(root, "1")
    with ScienceGraphStore(harness._graph_database_path()) as store:
        store.add_hypothesis("H-0001", title="first")
        store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
    return harness


def _outcome_state(verdict: str = "supported", summary: str = "done") -> dict:
    return {
        "hypothesis_lock": {"id": "H-0001", "status": "locked"},
        "cycle_outcome_record": {
            "id": "cycle-outcome-1", "hypothesis_id": "H-0001", "cycle_id": "1",
            "verdict": verdict, "summary": summary, "record_only": False,
        },
    }


def _counts(harness: Harness) -> tuple[int, int]:
    with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
        evaluations = store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0]
        edges = store.db.execute("SELECT COUNT(*) FROM scientific_edges").fetchone()[0]
    return evaluations, edges


def _seal(harness: Harness) -> None:
    with ScienceGraphStore(harness._graph_database_path()) as store:
        store.seal_goal(goal_id="goal-1", source_cycle="9", research_plan_sha256="0" * 64,
                        judge_receipt_ref=None, detail={})


def _new_h_args(h_id: str = "H-0002", cycle: str = "2"):
    state = {"hypothesis_lock": {"id": h_id, "status": "locked"}}
    branch = {"id": cycle, "hypothesis": "refined claim", "decisive_test": "test it"}
    selected = {"evolution_operator": "refine", "lineage_anchor_node_ids": ["H-0001"],
                "canvas_title": "Refined", "canvas_summary": "Refined claim",
                "decisive_test": "test it"}
    return state, branch, selected


class OutcomeIdempotencyTest(unittest.TestCase):
    def test_retry_after_crash_returns_existing_outcome_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = _seed(Path(temp))
            harness._render_canvas(_outcome_state())
            first = _counts(harness)
            # Retry with the same (cycle, H, verdict); a reworded summary must not
            # create rows, edges or a second Outcome.
            state = _outcome_state(summary="done (replayed)")
            harness._render_canvas(state)
            self.assertEqual(_counts(harness), first)
            self.assertEqual(first[0], 1)
            self.assertNotIn("cycle_outcome_record", state)
            with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
                self.assertEqual(store.cycle_evaluation("1")["summary"], "done")

    def test_conflicting_verdict_for_same_cycle_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = _seed(Path(temp))
            harness._render_canvas(_outcome_state("supported"))
            state = _outcome_state("rejected")
            with self.assertRaises(CycleOutcomeConflictError):
                harness._render_canvas(state)
            self.assertIn("cycle_outcome_record", state)
            with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
                self.assertEqual(store.cycle_evaluation("1")["verdict"], "supported")

    def test_outcome_write_holds_shared_barrier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = _seed(root)
            seen: list[str] = []
            original = harness._commit_cycle_science

            def spy(*args, **kwargs):
                seen.append(_barrier_state(root))
                self.assertTrue(harness._canvas_write_locked)
                return original(*args, **kwargs)

            harness._commit_cycle_science = spy
            harness._render_canvas(_outcome_state())
            self.assertEqual(seen, ["shared"])
            self.assertEqual(_barrier_state(root), "free")


class GoalSealTest(unittest.TestCase):
    def test_outcome_after_seal_raises_goal_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = _seed(Path(temp))
            _seal(harness)
            with self.assertRaises(GoalSealedError):
                harness._render_canvas(_outcome_state())

    def test_frontier_update_after_seal_raises_goal_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = _seed(Path(temp))
            _seal(harness)
            state = {"hypothesis_lock": {"id": "H-0001"},
                     "frontier_maintenance": {"updates": [
                         {"hypothesis_id": "H-0001", "status": "active", "reason": "late"}]}}
            with self.assertRaises(GoalSealedError):
                harness._render_canvas(state)

    def test_hypothesis_registration_after_seal_raises_goal_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = Harness(root, "2")
            _seal(harness)
            with self.assertRaises(GoalSealedError):
                harness._ensure_locked_hypothesis(*_new_h_args())
            with ScienceGraphStore(harness._graph_database_path(), readonly=True) as store:
                self.assertIsNone(store.db.execute(
                    "SELECT 1 FROM hypotheses WHERE id='H-0002'").fetchone())

    def test_pure_projection_after_seal_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = _seed(Path(temp))
            _seal(harness)
            harness._render_canvas({})
            self.assertIn("nodes", json.loads(harness.branch_canvas.read_text(encoding="utf-8")))


class CycleClaimFenceTest(unittest.TestCase):
    def _worker(self, root: Path, cycle: str, *, bind_h: str | None):
        claims = CycleClaims(root)
        claim = claims.claim_cycle("worker-1")
        claims.bind_cycle("worker-1", claim["claim_id"], claim["fencing_epoch"], cycle)
        if bind_h:
            claims.bind_hypothesis("worker-1", claim["claim_id"], claim["fencing_epoch"], bind_h)
        harness = Harness(root, cycle)
        harness.worker_id = "worker-1"
        harness.worker_claims = claims
        harness.worker_claim_id = claim["claim_id"]
        harness.worker_claim_epoch = claim["fencing_epoch"]
        return harness

    def test_stale_claim_token_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = self._worker(root, "1", bind_h="H-0001")
            harness.worker_claim_epoch -= 1
            with self.assertRaisesRegex(ValueError, "fencing token"):
                harness._render_canvas(_outcome_state())
            self.assertEqual(_counts(harness)[0], 0)
            harness.worker_claim_epoch += 1
            harness._render_canvas(_outcome_state())
            self.assertEqual(_counts(harness)[0], 1)

    def test_claim_for_other_cycle_cannot_write_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = self._worker(root, "3", bind_h=None)
            harness.cycle_dir = root / "1"
            with self.assertRaisesRegex(ValueError, "does not own this Cycle"):
                harness._render_canvas(_outcome_state())

    def test_registration_before_bind_then_bound_h_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = self._worker(root, "2", bind_h=None)
            self.assertEqual(harness._ensure_locked_hypothesis(*_new_h_args()), "H-0002")
            harness.worker_claims.bind_hypothesis(
                "worker-1", harness.worker_claim_id, harness.worker_claim_epoch, "H-0002")
            # Idempotent re-registration of the bound H passes; another H is refused.
            self.assertEqual(harness._ensure_locked_hypothesis(*_new_h_args()), "H-0002")
            with self.assertRaisesRegex(ValueError, "does not own this Hypothesis"):
                harness._ensure_locked_hypothesis(*_new_h_args("H-0003"))


class NestedBarrierTest(unittest.TestCase):
    def test_freeze_path_inside_shared_barrier_and_canvas_lock_does_not_deadlock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = Harness(root, "2")
            done: list[str] = []

            def freeze():
                from theory_master.multi_worker import canvas_write_lock
                with science_commit_barrier(root, "shared"):
                    with canvas_write_lock(root):
                        harness._canvas_write_locked = True
                        try:
                            done.append(harness._ensure_locked_hypothesis(*_new_h_args()))
                        finally:
                            harness._canvas_write_locked = False

            thread = threading.Thread(target=freeze)
            thread.start()
            thread.join(30)
            self.assertFalse(thread.is_alive(), "nested barrier/Canvas lock deadlocked")
            self.assertEqual(done, ["H-0002"])

    def test_freeze_path_inside_shared_barrier_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            harness = Harness(root, "2")
            with science_commit_barrier(root, "shared"):
                self.assertEqual(harness._ensure_locked_hypothesis(*_new_h_args()), "H-0002")
                self.assertEqual(_barrier_state(root), "shared")
            self.assertEqual(_barrier_state(root), "free")


if __name__ == "__main__":
    unittest.main()
