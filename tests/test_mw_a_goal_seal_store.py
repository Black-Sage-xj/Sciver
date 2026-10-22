"""MW-A: SQLite Goal seal, sealed-write rejection and the science commit barrier."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from theory_master.goal_terminal import (
    GoalSealedError, assert_goal_open, goal_sealed, science_commit_barrier,
)
from theory_master.science_graph_store import GOAL_COMPLETED_STOP, ScienceGraphStore

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PLAN_SHA = "a" * 64


def _child(code: str, *args: str, timeout: float = 30.0) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)}
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


class GoalSealStoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "science_graph.sqlite3"
        with ScienceGraphStore(self.db) as store:
            store.add_hypothesis("H-0001", title="supported claim")
            store.add_hypothesis("H-0002", title="running claim")
            store.add_hypothesis("H-0003", title="legacy broad claim")
            store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
            store.register_cycle_hypothesis(cycle_id="2", hypothesis_id="H-0002")
            store.append_outcome(outcome_id="cycle-outcome-1", hypothesis_id="H-0001",
                                 cycle_id="1", verdict="supported")

    def seal(self, store, **overrides):
        kwargs = dict(goal_id="goal", source_cycle="1", research_plan_sha256=PLAN_SHA,
                      judge_receipt_ref="1/goal_completion_out.json",
                      detail={"rationale": "Final Gate satisfied"})
        kwargs.update(overrides)
        return store.seal_goal(**kwargs)

    def test_goal_sealed_error_is_not_value_error(self):
        self.assertFalse(issubclass(GoalSealedError, ValueError))

    def test_seal_is_idempotent_and_not_overwritten(self):
        with ScienceGraphStore(self.db) as store:
            self.assertIsNone(store.goal_terminal())
            first = self.seal(store)
            second = self.seal(store, source_cycle="9", detail={"rationale": "other"})
        self.assertTrue(first["newly_sealed"])
        self.assertFalse(second["newly_sealed"])
        self.assertEqual(second["source_cycle"], "1")
        self.assertEqual(second["sealed_at"], first["sealed_at"])
        self.assertEqual(second["detail"], {"rationale": "Final Gate satisfied"})
        with ScienceGraphStore(self.db, readonly=True) as store:
            count = store.db.execute("SELECT COUNT(*) FROM task_goal_terminal").fetchone()[0]
            terminal = store.goal_terminal()
        self.assertEqual(count, 1)
        self.assertEqual(terminal["verdict"], "passed")
        self.assertEqual(goal_sealed(self.root, self.db)["source"], "sqlite")

    def test_seal_closes_frontiers_with_g8_reason_for_running_h(self):
        with ScienceGraphStore(self.db) as store:
            sealed = self.seal(store)
            rows = {row["hypothesis_id"]: dict(row) for row in
                    store.db.execute("SELECT * FROM frontier_branches")}
            status = {row["id"]: row["scientific_status"] for row in
                      store.db.execute("SELECT id, scientific_status FROM hypotheses")}
            verdicts = {row["hypothesis_id"]: row["verdict"] for row in
                        store.db.execute("SELECT hypothesis_id, verdict FROM outcomes")}
        self.assertEqual(sorted(sealed["closed_frontier_ids"]), ["H-0001", "H-0002", "H-0003"])
        self.assertEqual(sealed["goal_completed_stop_ids"], ["H-0002"])
        self.assertTrue(all(row["status"] == "closed" for row in rows.values()))
        self.assertEqual(rows["H-0002"]["reason"], GOAL_COMPLETED_STOP)
        self.assertNotEqual(rows["H-0001"]["reason"], GOAL_COMPLETED_STOP)
        self.assertNotEqual(rows["H-0003"]["reason"], GOAL_COMPLETED_STOP)
        # A stopped H receives no scientific verdict.
        self.assertEqual(status["H-0002"], "none")
        self.assertEqual(verdicts["H-0002"], "open")

    def test_sealed_db_rejects_writes_and_rolls_back(self):
        with ScienceGraphStore(self.db) as store:
            self.seal(store)
            with self.assertRaises(GoalSealedError):
                store.add_hypothesis("H-0004", title="late")
            with self.assertRaises(GoalSealedError):
                with store.transaction():
                    with store.transaction():
                        store.db.execute("DELETE FROM task_goal_terminal")
            self.assertFalse(store.db.in_transaction)
            self.assertIsNone(store.db.execute(
                "SELECT 1 FROM hypotheses WHERE id='H-0004'").fetchone())
            self.assertIsNotNone(store.goal_terminal())
        with self.assertRaises(GoalSealedError):
            assert_goal_open(self.root, self.db)

    def test_allow_after_goal_seal_connection_can_write_derived_index(self):
        with ScienceGraphStore(self.db) as store:
            self.seal(store)
        with ScienceGraphStore(self.db, allow_after_goal_seal=True) as store:
            with store.transaction():
                store.db.execute(
                    "INSERT INTO cycle_memory_index(cycle_id,execution_mode,status,cycle_dir,indexed_at)"
                    " VALUES ('3','single_worker','over','/tmp/3','now')")
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM cycle_memory_index").fetchone()[0], 1)

    def test_readonly_old_db_without_terminal_table_is_not_sealed(self):
        raw = sqlite3.connect(self.db)
        raw.execute("DROP TABLE task_goal_terminal")
        raw.commit()
        raw.close()
        with ScienceGraphStore(self.db, readonly=True) as store:
            self.assertIsNone(store.goal_terminal())
        self.assertIsNone(goal_sealed(self.root, self.db))
        assert_goal_open(self.root, self.db)

    def test_missing_graph_is_not_sealed(self):
        self.assertIsNone(goal_sealed(self.root, self.root / "absent.sqlite3"))

    def test_late_write_from_second_process_is_rejected(self):
        code = """
            import sys
            from pathlib import Path
            from theory_master.goal_terminal import GoalSealedError, science_commit_barrier
            from theory_master.science_graph_store import ScienceGraphStore
            root, db = Path(sys.argv[1]), Path(sys.argv[2])
            print("waiting", flush=True)
            with science_commit_barrier(root, "shared"):
                try:
                    with ScienceGraphStore(db) as store:
                        store.add_hypothesis("H-0009", title="late")
                except GoalSealedError:
                    print("GoalSealedError", flush=True)
                else:
                    print("written", flush=True)
        """
        with science_commit_barrier(self.root, "exclusive"):
            child = _child(code, str(self.root), str(self.db))
            self.assertEqual(child.stdout.readline().strip(), "waiting")
            time.sleep(0.3)
            self.assertIsNone(child.poll())
            with ScienceGraphStore(self.db) as store:
                self.seal(store)
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "GoalSealedError")
        with ScienceGraphStore(self.db, readonly=True) as store:
            self.assertIsNone(store.db.execute(
                "SELECT 1 FROM hypotheses WHERE id='H-0009'").fetchone())


class ScienceCommitBarrierTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_exclusive_blocks_shared_acquirer_in_other_process(self):
        code = """
            import sys, time
            from pathlib import Path
            from theory_master.goal_terminal import science_commit_barrier
            print("start", flush=True)
            with science_commit_barrier(Path(sys.argv[1]), "shared"):
                print("acquired", flush=True)
        """
        with science_commit_barrier(self.root, "exclusive"):
            child = _child(code, str(self.root))
            self.assertEqual(child.stdout.readline().strip(), "start")
            time.sleep(0.5)
            self.assertIsNone(child.poll(), "shared acquirer must wait for the exclusive holder")
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "acquired")
        self.assertTrue((self.root / ".theory-master" / "science_commit.lock").is_file())

    def test_shared_holders_do_not_block_each_other(self):
        code = """
            import sys
            from pathlib import Path
            from theory_master.goal_terminal import science_commit_barrier
            with science_commit_barrier(Path(sys.argv[1]), "shared"):
                print("acquired", flush=True)
        """
        with science_commit_barrier(self.root, "shared"):
            out, err = _child(code, str(self.root)).communicate(timeout=30)
        self.assertEqual(out.strip(), "acquired", err)

    def test_same_thread_reentrancy_does_not_deadlock(self):
        done = threading.Event()

        def nested():
            with science_commit_barrier(self.root, "exclusive"):
                with science_commit_barrier(self.root, "shared"):
                    with science_commit_barrier(self.root, "exclusive"):
                        pass
            with science_commit_barrier(self.root, "shared"):
                with science_commit_barrier(self.root, "shared"):
                    pass
            done.set()

        worker = threading.Thread(target=nested, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertTrue(done.is_set(), "re-entrant barrier deadlocked")

    def test_shared_to_exclusive_upgrade_is_refused(self):
        with science_commit_barrier(self.root, "shared"):
            with self.assertRaises(RuntimeError):
                with science_commit_barrier(self.root, "exclusive"):
                    pass
        # The outer shared hold is released normally afterwards.
        with science_commit_barrier(self.root, "exclusive"):
            pass

    def test_other_thread_is_excluded_like_a_process(self):
        acquired = threading.Event()

        def contender():
            with science_commit_barrier(self.root, "shared"):
                acquired.set()

        with science_commit_barrier(self.root, "exclusive"):
            thread = threading.Thread(target=contender, daemon=True)
            thread.start()
            self.assertFalse(acquired.wait(0.3))
        self.assertTrue(acquired.wait(10))
        thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
