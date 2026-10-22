"""Offline Stage Card / contract regression anchored to archived Cycles 180–185.

These are design oracles, not LLM verdict predictors. No live model/Cycle is run.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.stage_registry import stage_card_text, procedure_card_text


class ScientificQualification20261022(unittest.TestCase):
    def setUp(self):
        self.lead = stage_card_text("research_lead", "frontier_proposal")
        self.challenger = stage_card_text("challenger", "candidate_review")

    def test_lead_compares_scientific_value_not_gate_bookkeeping(self):
        # Stage owns scientific criteria; Contract owns the two named output fields.
        for phrase in (
            "most consequential", "Final Gate", "adjudicated Outcomes",
            "plausible alternatives", "inconclusive result",
            "Scientific Model", "Indispensable premises need a credible in-scope route",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.lead)
        projection = role_contract("research_lead", phase="frontier_proposal").prompt_projection()
        self.assertIn("goal_advance", projection)
        self.assertIn("why_now", projection)

    def test_cycle_180_positive_local_mechanism_is_still_eligible(self):
        # H-0180: local graph-current prediction; terminal Judge supported.
        self.assertIn("A matching local result may advance the Cycle without supporting the whole hypothesis", self.lead)
        self.assertIn("a necessary local prediction is acceptable without settling the whole hypothesis", self.challenger)
        self.assertIn("Bounded mechanisms, counterexamples and negative results can be valuable", self.challenger)
        self.assertIn("open/negative result as a design failure", self.challenger)

    def test_cycle_182_finite_window_cannot_refute_infinite_bound(self):
        # Cycle 182 Challenger revised a second candidate's unjustified uniform-C test.
        # Generic proof precision and route feasibility replace H8's literal infinity example.
        self.assertIn("Match uncertainty and proof precision to the claimed inference", self.lead)
        self.assertIn("Indispensable premises need a credible in-scope route", self.lead)
        self.assertIn("Matching, contrary and genuinely inconclusive results", self.challenger)

    def test_cycle_183_negative_prediction_still_informs_goal(self):
        # H-0183: contrast of signed covariance law and marginal model.
        self.assertIn("A matching, contrary, or inconclusive result needs a meaningful interpretation", self.lead)
        self.assertIn("negative", self.challenger)
        self.assertNotIn("all hypotheses must pass", self.challenger)

    def test_cycle_184_flags_compound_claims_not_expensive_research(self):
        # H-0184 combined full signed-curve prediction, channel allocation and far-field limit.
        self.assertIn("keep each Cycle to one coherent adjudicable claim", self.lead)
        self.assertIn("bundled independent claims", self.challenger)
        self.assertIn("arbitrary cutoffs", self.challenger)

    def test_cycle_185_independent_limit_precondition_cannot_be_cosmetic_revise(self):
        # Candidate 185 was originally revised, then qualified after one test repair.
        # This is an oracle for the NEW policy, not a claim of a new model verdict.
        # Generalized: a crucial independent proof without a credible route is a design flaw.
        self.assertIn("Indispensable premises need a credible in-scope route", self.lead)
        self.assertIn("essential route is demonstrably unavailable", self.challenger)
        self.assertIn("unchanged hypothesis/scope/estimand", self.challenger)
        self.assertIn("Reject the design, not the broader research direction", self.challenger)

    def test_contract_and_prompt_assembly_unchanged(self):
        lead_contract = role_contract("research_lead", phase="frontier_proposal")
        challenger_contract = role_contract("challenger", phase="candidate_review")
        self.assertEqual(challenger_contract.version, 5)
        self.assertEqual(
            set(lead_contract.prompt_schema["candidates"][0]) & {
                "hypothesis", "scope", "why_now", "goal_advance", "decisive_test"
            },
            {"hypothesis", "scope", "why_now", "goal_advance", "decisive_test"},
        )
        for role, stage, contract in (
            ("research_lead", "frontier_proposal", lead_contract),
            ("challenger", "candidate_review", challenger_contract),
        ):
            candidate = {"id": "cand-example", "hypothesis": "H", "scope": "S",
                         "why_now": "Unresolved", "goal_advance": "Discriminate",
                         "decisive_test": "Test"}
            ctx = stage_context(role, stage, {
                "goal": "Predict signed release", "research_contract": "Known inputs only",
                "final_gate": "Out-of-network prediction",
                "agent_graph": {"schema": "theorymaster/test-agent-graph/v1",
                                "hypotheses": [], "current_frontiers": [],
                                "lineage": [], "external_sources": []},
                "candidates": [candidate], "candidate": [candidate],
            })
            prompt = build_initial_prompt(
                prefix="test", role=role, stage=stage, contract_ref=contract.ref,
                contract_schema=contract.prompt_projection({}), context=ctx, write_rule="read only",
            )
            self.assertIn("Current task: " + stage, prompt)
            self.assertIn("Predict signed release", prompt)
            self.assertIn("credible", prompt)
            self.assertIn("Output contract:", prompt)
        self.assertLess(len(self.lead + procedure_card_text("research_lead", "frontier_proposal")), 6000)
        self.assertLess(len(self.challenger + procedure_card_text("challenger", "candidate_review")), 4500)

        # These archive assertions establish only historical inputs, not new model behavior.


if __name__ == "__main__":
    unittest.main()
