import unittest

from theory_master.contract_stages import role_contract
from theory_master.identity import identities_root
from theory_master.stage_registry import procedure_card_text, stage_card_text


class LeadFrontierStatusPromptTest(unittest.TestCase):
    def setUp(self):
        self.procedure = procedure_card_text("research_lead", "frontier_proposal")
        self.stage = stage_card_text("research_lead", "frontier_proposal")

    def test_frontier_is_independent_of_graph_leaf(self):
        self.assertIn("independent of graph leaves", self.stage)
        self.assertIn("A descendant does not make its parent ineligible", self.stage)
        self.assertIn("focused-H recent Outcomes", self.procedure)

    def test_outcome_and_frontier_are_different(self):
        self.assertIn("one scientific Outcome", self.stage)
        self.assertIn("active/parked/closed", self.stage)
        self.assertIn("supported/rejected/open", self.stage)

    def test_parked_continuation_requires_trigger(self):
        self.assertIn("reconsideration_trigger", self.stage)
        self.assertIn("Terminal supported/rejected H", self.stage)

    def test_closed_can_derive_new_h_without_reopening(self):
        self.assertIn("closed H cannot be silently continued as same H", self.stage)
        self.assertIn("historical origin of a genuinely new H", self.stage)
        self.assertIn("typed evolution_operator", self.stage)

    def test_history_not_a_second_outcome(self):
        self.assertIn("Historical Cycle decisions/Evidence", self.procedure)
        self.assertIn("one scientific Outcome", self.stage)

    def test_goal_aligned_historical_pivot(self):
        self.assertIn("historical H pivot", self.stage)
        self.assertIn("historical H pivot", self.stage)
        self.assertIn("lineage_anchor_node_ids", self.stage)

    def test_frontier_proposal_contract_pairs_operator_and_anchor(self):
        contract = role_contract("research_lead", phase="frontier_proposal")
        base = {
            "hypothesis": "derived claim", "scope": "frozen scope",
            "why_now": "global pivot is scientifically preferable",
            "goal_advance": "advances the Final Gate",
            "decisive_test": "a discriminating test",
        }
        with self.assertRaisesRegex(ValueError, "required for NEW H"):
            contract.validate({"candidates": [{**base, "evolution_operator": "derive"}]}, {})
        contract.validate({"candidates": [{
            **base, "lineage_anchor_node_id": "H-0002", "evolution_operator": "derive",
        }]}, {"_known_hypothesis_ids": ["H-0002"]})
        contract.validate({"candidates": [{
            **base, "lineage_anchor_node_ids": ["H-0002", "H-0003"],
            "evolution_operator": "derive",
        }]}, {"_known_hypothesis_ids": ["H-0002", "H-0003"]})

    def test_maintenance_manages_h_not_tips(self):
        procedure = procedure_card_text("research_lead", "frontier_maintenance")
        card = stage_card_text("research_lead", "frontier_maintenance")
        self.assertIn("every relevant H can be a scientific origin", procedure)
        self.assertIn("regardless of derived children", card)
        self.assertIn("active/parked/closed", card)
        self.assertIn("Do not modify scientific_status", card)

    def test_no_active_frontier_does_not_mean_complete(self):
        procedure = procedure_card_text("research_lead", "frontier_maintenance")
        card = stage_card_text("research_lead", "frontier_maintenance")
        self.assertIn("zero active H does not imply Goal Completion", procedure)
        self.assertIn("Task COMPLETE", card)


class FrontierMaintenanceContractTest(unittest.TestCase):
    def test_lead_frontier_maintenance_uses_h_identity_and_sqlite_status(self):
        contract = role_contract("research_lead", phase="frontier_maintenance")
        contract.validate({"updates": [{
            "hypothesis_id": "H-0001", "status": "parked", "reason": "await a discriminating test",
        }]}, {"_known_hypothesis_ids": ["H-0001"]})
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            contract.validate({"updates": [{
                "node_id": "H-0001", "frontier_status": "parked", "reason": "old Canvas shape",
            }]}, {"_known_hypothesis_ids": ["H-0001"]})
        with self.assertRaisesRegex(ValueError, "unknown H id in canonical SQLite graph"):
            contract.validate({"updates": [{
                "hypothesis_id": "H-0099", "status": "active", "reason": "unknown ID fixture",
            }]}, {"_known_hypothesis_ids": ["H-0001"]})

    def test_reporter_has_no_scientific_state_authority_or_maintenance_stage(self):
        report = role_contract("reporter", phase="report")
        self.assertEqual(set(report.prompt_schema), {"title", "summary"})
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")
        identity = (identities_root() / "reporter.md").read_text(encoding="utf-8")
        self.assertIn("scientific_status", identity)
        self.assertIn("frontier_status", identity)
        self.assertIn("frontier_status", identity)
        self.assertIn("归 Research Lead", identity)
        card = stage_card_text("research_lead", "frontier_maintenance")
        self.assertIn("hypothesis_id", card)
        self.assertIn("status=closed|parked|active", card)


if __name__ == "__main__":
    unittest.main()
