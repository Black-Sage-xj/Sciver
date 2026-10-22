"""TMCR-20261009-007: cumulative Investigator usage must survive state projections."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.backends import FakeBackend
from theory_master.observability.collector import Collector
from theory_master.specification import TaskSpec
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


class BudgetSnapshotSyncTests(unittest.TestCase):
    def make(self, path):
        plan = path / "research_plan.md"
        plan.write_text(
            "# Goal\nCheck a small synthetic hypothesis.\n\n"
            "# Research Contract\nOnly local synthetic data.\n\n"
            "# Final Gate\nJudge reaches an independent verdict.\n\n"
            "# Required Context\nNone.\n", encoding="utf-8")
        spec = TaskSpec.from_config(bootstrap(plan).config_path)
        collector = Collector(spec)
        workflow = Workflow(spec, FakeBackend(), collector=collector)
        workflow._ensure_cycle()
        collector.run_dir = workflow.cycle_dir
        state = workflow._new_state()
        state["investigator_budget"] = {
            "tranche_tokens": 50_000_000, "allowance_tokens": 50_000_000,
            "observed_tokens": 0, "remaining_tokens": 50_000_000,
            "exhausted": False, "rejected": False, "review_index": 1, "reviews": 0,
        }
        return workflow, collector, state

    def update_usage(self, collector, total, *, cached=0):
        agent = collector.state["agents"]["investigator"]
        agent.update(tokens_total=total, tokens_in=total-10,
                     tokens_out=10, cached_input_tokens=cached)

    def assert_synced(self, workflow, state, expected):
        workflow._persist(state)
        research = json.loads((workflow.cycle_dir / "research_state.json").read_text())
        runtime = json.loads((workflow.cycle_dir / "run_state.json").read_text())
        self.assertEqual(research["investigator_budget"]["observed_tokens"], expected)
        self.assertEqual(runtime["investigator_budget"]["observed_tokens"], expected)
        self.assertEqual(runtime["investigator_budget"]["remaining_tokens"], 50_000_000 - expected)

    def test_latest_usage_including_cached_input_without_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf, collector, state = self.make(Path(tmp))
            self.update_usage(collector, 8000, cached=6500)
            self.assert_synced(wf, state, 8000)
            self.assertEqual(state["investigator_budget"]["review_index"], 1)
            self.assertEqual(state["investigator_budget"]["reviews"], 0)

    def test_resume_and_judge_rework_only_advance_budget_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            wf, collector, state = self.make(Path(tmp))
            self.update_usage(collector, 7000, cached=6000)
            self.assert_synced(wf, state, 7000)
            state["judge_rework_count"] = 1
            self.update_usage(collector, 10000, cached=8500)
            self.assert_synced(wf, state, 10000)
            # Older collector baseline after a partial resume must not lower usage.
            self.update_usage(collector, 9000, cached=8000)
            self.assert_synced(wf, state, 10000)
            self.update_usage(collector, 12300, cached=11100)
            self.assert_synced(wf, state, 12300)
            self.assertEqual(state["judge_rework_count"], 1)

    def test_budget_boundary_uses_latest_completed_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"THEORY_MASTER_INVESTIGATOR_BUDGET_TOKENS": "100"}):
                wf, collector, state = self.make(Path(tmp))
                self.update_usage(collector, 140, cached=110)
                wf._persist(state)
                self.assertEqual(state["investigator_budget"]["observed_tokens"], 140)
                self.assertTrue(state["investigator_budget"]["exhausted"])
                self.assertEqual(state["investigator_budget"]["remaining_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
