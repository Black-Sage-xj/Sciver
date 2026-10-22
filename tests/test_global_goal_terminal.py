"""A task-wide Goal verdict must survive out-of-order Cycle completion."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.core import Workflow
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.worker_runtime import _master_complete, schedule_once


class TaskWideGoalTerminalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        plan = root / "research_plan.md"
        plan.write_text("# Goal\nOriginal Final Gate\n", encoding="utf-8")
        canvas = root / "exploration.canvas"
        canvas.write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
        self.db = root / "science_graph.sqlite3"
        with ScienceGraphStore(self.db) as store:
            store.add_hypothesis("H-0001", title="Test scientific claim")
            store.set_frontier(branch_id="frontier:H-0001", hypothesis_id="H-0001",
                               status="closed", reason="Goal passed", preferred=False)
        self.spec = SimpleNamespace(research_root=root, document=plan,
                                    branch_canvas=canvas, task_mode=True)
        self.workflow = Workflow.__new__(Workflow)
        self.workflow.spec = self.spec
        self.workflow.branch_canvas = canvas

    def cycle(self, number, completion=None, receipt=None):
        target = self.spec.research_root / str(number)
        target.mkdir()
        (target / '.theory-master-cycle').write_text('theory-master/v2\ntest\n', encoding='utf-8')
        (target / 'research_state.json').write_text(json.dumps(
            {'status': 'over', **({'goal_completion': completion} if completion else {})}), encoding='utf-8')
        if receipt is not None:
            (target / 'goal_completion_out.json').write_text(json.dumps(receipt), encoding='utf-8')
        return target

    def passed(self):
        return {'verdict': 'passed', 'source_cycle': '1',
                'authority_digest': self.workflow._goal_authority_digest()}

    def scheduler_complete(self):
        with patch('theory_master.specification.TaskSpec.from_task_root', return_value=self.spec):
            return _master_complete(self.spec.research_root)

    def test_historical_verified_pass_survives_newer_unfinished_cycle(self):
        self.cycle(1, self.passed(), {'verdict': 'passed'})
        self.cycle(2)
        self.assertEqual(self.workflow._latest_goal_completion_state()['goal_completion']['source_cycle'], '1')
        self.assertTrue(self.scheduler_complete())

    def test_scheduler_refuses_new_workers_after_verified_goal_completion(self):
        self.cycle(1, self.passed(), {'verdict': 'passed'})
        self.cycle(2)
        with patch('theory_master.specification.TaskSpec.from_task_root', return_value=self.spec), \
             patch('theory_master.worker_runtime.runtime_health', return_value={'active': False}):
            result = schedule_once(self.spec, resource_slots=2)
        self.assertEqual(result, {'started': [], 'reason': 'master_complete'})

    def test_historical_pass_survives_newer_not_passed_cycle(self):
        self.cycle(1, self.passed(), {'verdict': 'passed'})
        self.cycle(2, {'verdict': 'not_passed', 'source_cycle': '2'}, {'verdict': 'not_passed'})
        self.assertTrue(self.scheduler_complete())

    def test_unverified_pass_does_not_stop_scheduler(self):
        self.cycle(1, {'verdict': 'passed', 'source_cycle': '1'}, {'verdict': 'passed'})
        self.cycle(2)
        self.assertIsNone(self.workflow._latest_goal_completion_state())
        self.assertFalse(self.scheduler_complete())

    def test_missing_or_forged_receipt_does_not_stop_scheduler(self):
        self.cycle(1, self.passed(), {'verdict': 'not_passed'})
        self.assertFalse(self.scheduler_complete())
        (self.spec.research_root / '1' / 'goal_completion_out.json').unlink()
        self.assertIsNone(self.workflow._latest_goal_completion_state())

    def test_changed_goal_or_graph_invalidates_old_authority(self):
        self.cycle(1, self.passed(), {'verdict': 'passed'})
        self.assertTrue(self.scheduler_complete())
        self.spec.document.write_text('# Goal\nChanged Final Gate\n', encoding='utf-8')
        self.assertFalse(self.scheduler_complete())
        self.spec.document.write_text('# Goal\nOriginal Final Gate\n', encoding='utf-8')
        with ScienceGraphStore(self.db) as store:
            store.add_hypothesis('H-0002', title='New hypothesis')
        self.assertFalse(self.scheduler_complete())


if __name__ == '__main__':
    unittest.main()
