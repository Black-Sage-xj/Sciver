import unittest

from theory_master.contract_stages import role_contract
from theory_master.identity import identities_root
from theory_master.stage_registry import procedure_card_text, stage_card_text


class LeadFrontierStatusPromptTest(unittest.TestCase):
    def setUp(self):
        self.procedure = procedure_card_text("research_lead", "frontier_proposal")
        self.stage = stage_card_text("research_lead", "frontier_proposal")
        self.maintenance = stage_card_text("research_lead", "frontier_maintenance")
        self.identity = (identities_root() / "research_lead.md").read_text(encoding="utf-8")

    def test_frontier_is_independent_of_graph_leaf(self):
        self.assertIn("Frontier labels describe opportunities independently of graph leaves and Judge verdicts", self.procedure)
        self.assertIn("independently of graph degree, descendants", self.maintenance)
        self.assertIn("without clearing non-leaf labels", self.maintenance)

    def test_outcome_and_frontier_are_different(self):
        self.assertIn("Cycle Outcome", self.identity)
        self.assertIn("Goal Completion", self.identity)
        self.assertIn("local supported/rejected/open Outcomes", self.maintenance)
        self.assertIn("Local support, whole-H scope, frontier state and Goal completion are distinct", self.procedure)

    def test_parked_continuation_requires_trigger(self):
        self.assertIn("reopen closed H, or continue parked H unchanged", self.stage)
        self.assertIn("retesting an active or resolved claim needs new science or a new source of uncertainty", self.stage)

    def test_closed_can_derive_new_h_without_reopening(self):
        self.assertIn("A new Candidate from historical work must be scientifically distinct", self.stage)
        self.assertIn("including pending/parked claims and unadjudicated work", self.procedure)
        contract = role_contract("research_lead", phase="frontier_proposal")
        self.assertIn("evolution_operator", str(contract.prompt_schema))

    def test_history_not_a_second_outcome(self):
        self.assertIn("search formal research history beyond declared parents and recent Cycles across H, Outcomes and External records", self.procedure)
        self.assertIn("Separate proposals, provisional results and Judge conclusions", self.procedure)
        self.assertIn("SQLite", self.identity)

    def test_goal_aligned_historical_pivot(self):
        self.assertIn("justify historical pivots by Goal impact", self.procedure)
        self.assertIn("Consider relevant active H without treating their labels as commands", self.procedure)
        self.assertIn("lineage_anchor_node_ids", str(role_contract(
            "research_lead", phase="frontier_proposal").prompt_schema))

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
        self.assertIn("canonical H/Outcome details and multi-parent lineage", procedure)
        self.assertIn("independently of graph degree, descendants", card)
        self.assertIn("Choose active/parked/closed", card)
        self.assertIn("change scientific_status", card)

    def test_no_active_frontier_does_not_mean_complete(self):
        procedure = procedure_card_text("research_lead", "frontier_maintenance")
        card = stage_card_text("research_lead", "frontier_maintenance")
        self.assertIn("remaining Goal opportunities", procedure)
        self.assertIn("infer Task completion from zero active H or all closed frontiers", card)


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
        self.assertIn("不改变候选、Scientific Model、Judge verdict、Hypothesis 科学状态或前沿状态", identity)
        self.assertIn("下一 Cycle 的候选由 Research Lead 提出", identity)
        card = stage_card_text("research_lead", "frontier_maintenance")
        self.assertIn("existing-H frontier updates", card)
        self.assertIn("Choose active/parked/closed", card)


if __name__ == "__main__":
    unittest.main()
