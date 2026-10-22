from __future__ import annotations

import unittest

from theory_master.contracts import role_contract
from theory_master.core import Workflow
from theory_master.observability.collector import Collector
from theory_master.stage_registry import STAGE_CARDS
from theory_master.tui.render import render_investigation, render_last


class InvestigatorRoundSemanticsTest(unittest.TestCase):
    """Current semantic boundary: one Investigator submission, then Judge.

    Former same-round scientific-turn tests belonged to the superseded
    workflow; archived Cycle data is still displayable without relaunching it.
    """

    def test_investigator_exit_is_full_submission_for_judge(self):
        exit_rule = STAGE_CARDS[("investigator", "investigate")]["exit"].lower()
        self.assertIn("judge", exit_rule)
        self.assertIn("findings", exit_rule)
        contract = role_contract("investigator", phase="investigate")
        self.assertEqual(list(contract.prompt_schema), ["findings"])
        self.assertEqual(contract.version, 10)

    def test_old_same_round_routing_is_no_longer_executable(self):
        self.assertFalse(hasattr(Workflow, "_should_continue_investigator_same_round"))
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        self.assertFalse(hasattr(Workflow, "_is_actionable_internal_no_progress"))

    def test_observability_can_read_archived_turns_without_exposing_workflow_turns(self):
        collector = Collector()
        sci = {
            "status": "active", "phase": "investigator:running",
            "investigation_attempt": 1, "investigation_round_count": 0,
            "total_investigation_round_count": 0,
            "judge_rework_count": 0, "investigation_attempts": {},
        }
        started = {
            "role": "investigator", "thread_id": "thr-investigator",
            "investigation_attempt": 1, "investigation_round_count": 1,
            "internal_turn": 1,
        }
        completed = {"role": "investigator", "output": {"findings": []}}
        for i in (1, 2):
            collector._apply_scientific("role.started", {**started, "internal_turn": i}, dict(sci))
            collector._apply_scientific("role.completed", dict(completed), dict(sci))
        snap = collector.snapshot()
        self.assertEqual(len(snap["investigation_rounds"]), 1)
        self.assertNotIn("turns", render_last(snap))
        self.assertNotIn("turns", render_investigation(snap))


if __name__ == "__main__":
    unittest.main()
