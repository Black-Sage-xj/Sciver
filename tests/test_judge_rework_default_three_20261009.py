from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.workflow_support import REWORK_LIMIT
from theory_master.specification import TaskSpec
from theory_master.workflow_investigation import InvestigationMixin


class JudgeReworkDefaultThreeTest(unittest.TestCase):
    def test_sdk_default_three(self):
        self.assertEqual(REWORK_LIMIT, 3)
        self.assertEqual(TaskSpec.__dataclass_fields__["max_judge_reworks_per_cycle"].default, 3)

    def test_default_budget_allows_three_reworks(self):
        actor = InvestigationMixin()
        actor.spec = SimpleNamespace(max_judge_reworks_per_cycle=None)
        self.assertEqual(actor._investigation_budgets()["max_judge_reworks_per_cycle"], 3)

    def test_explicit_budget_override_still_supported(self):
        actor = InvestigationMixin()
        actor.spec = SimpleNamespace(max_judge_reworks_per_cycle=1)
        self.assertEqual(actor._investigation_budgets()["max_judge_reworks_per_cycle"], 1)
        actor.spec.max_judge_reworks_per_cycle = 3
        self.assertEqual(actor._investigation_budgets()["max_judge_reworks_per_cycle"], 3)


if __name__ == "__main__":
    unittest.main()
