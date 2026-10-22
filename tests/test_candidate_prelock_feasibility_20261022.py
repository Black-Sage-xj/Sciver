"""Offline design oracles for C186–191 pre-lock feasibility and prompt generality.

These tests do not predict LLM verdicts. H8-specific structures stay in test
fixtures and never become universal Stage/Procedure requirements.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.stage_registry import (
    PROCEDURE_CARDS,
    STAGE_CARDS,
    procedure_card_text,
    stage_card_text,
)


TEST_AGENT_GRAPH = {
    "schema": "theorymaster/test-agent-graph/v1",
    "hypotheses": [],
    "current_frontiers": [],
    "lineage": [],
    "focus_details": [],
    "external_sources": [],
    "external_references": [],
    "source_relations": [],
}


class PrelockScientificFeasibilityOracle(unittest.TestCase):
    def setUp(self):
        self.lead = stage_card_text("research_lead", "frontier_proposal")
        self.review = stage_card_text("challenger", "candidate_review")
        self.lead_procedure = procedure_card_text("research_lead", "frontier_proposal")
        self.review_procedure = procedure_card_text("challenger", "candidate_review")
        self.prompt_bodies = (
            self.lead,
            self.review,
            self.lead_procedure,
            self.review_procedure,
        )

    def _challenger_prompt(self, candidate: dict) -> str:
        contract = role_contract("challenger", phase="candidate_review")
        context = stage_context(
            "challenger",
            "candidate_review",
            {"candidate": [candidate], "agent_graph": TEST_AGENT_GRAPH},
        )
        return build_initial_prompt(
            prefix="test",
            role="challenger",
            stage="candidate_review",
            contract_ref=contract.ref,
            contract_schema=contract.prompt_projection({}),
            context=context,
            write_rule="read only",
        )

    def test_c189_exact_input_matching_has_a_cheap_eligibility_gate(self):
        # The H8-specific fixture mirrors the reported condition; these are
        # illustrative encodings, not a fresh inventory of archived assets.
        encodings = [("P(d)", f"unique-{i:02d}") for i in range(52)]
        pairs = [
            (i, j) for i in range(52) for j in range(i + 1, 52)
            if encodings[i] == encodings[j]
        ]
        self.assertEqual(pairs, [])

        for body in self.prompt_bodies:
            for domain_specific in ("P(d)", "52 networks", "network-level", "same-input", "H8"):
                self.assertNotIn(domain_specific.lower(), body.lower())

        self.assertIn("registry/metadata", self.lead_procedure)
        self.assertIn("eligible comparison set", self.lead_procedure)
        self.assertIn("non-empty", self.lead_procedure)
        self.assertIn("required comparison set", self.review)
        self.assertIn("feasible and non-empty", self.review)
        self.assertIn("if the decisive test explicitly requires a matched", self.review_procedure.lower())
        self.assertIn("do not impose matching when the science does not require it", self.review_procedure)

    def test_c186_uncertainty_and_c187_188_common_support(self):
        # Illustrative overlapping intervals: point estimates exist, but they
        # cannot resolve the frozen signed contrast.
        estimate_a, err_a = 1.0, 0.8
        estimate_b, err_b = 1.5, 0.8
        contrast_lower_bound = abs(estimate_a - estimate_b) - err_a - err_b
        self.assertLessEqual(contrast_lower_bound, 0)

        self.assertIn("uncertainty", self.lead.lower())
        self.assertIn("uncertainty and proof precision", self.lead.lower())
        self.assertIn("independent-unit sample size", self.review.lower())
        self.assertIn("not merely whether calculations run", self.review.lower())
        self.assertIn("independent units", self.lead_procedure)
        self.assertIn("uncertainty/error budget", self.lead_procedure)
        self.assertIn("contrast/common support", self.lead_procedure)
        self.assertIn("rather than merely yield estimates", self.lead_procedure)
        self.assertIn("credible in-scope route", self.review)
        self.assertIn("treat it as Investigation work rather than a revise blocker", self.review)

    def test_c190_local_protocol_bridge_and_c191_negative_result_remain_eligible(self):
        self.assertIn(
            "A matching local result may advance the Cycle without supporting the whole hypothesis",
            self.lead,
        )
        self.assertIn("a contrary result refutes H only when its local prediction is necessary", self.lead)
        self.assertIn("negative", self.lead.lower())
        self.assertIn("negative results can be valuable", self.review.lower())
        self.assertIn("uncertain effect", self.review.lower())
        self.assertIn("as a design failure when the test remains executable", self.review.lower())
        self.assertIn("Reject the design, not the broader research direction", self.review)

    def test_stage_contract_procedure_separation_and_brevity(self):
        for role, stage, card, procedure in (
            ("research_lead", "frontier_proposal", self.lead, self.lead_procedure),
            ("challenger", "candidate_review", self.review, self.review_procedure),
        ):
            with self.subTest(role=role, stage=stage):
                contract = role_contract(role, phase=stage)
                context = stage_context(
                    role,
                    stage,
                    {
                        "goal": "Explain a scoped scientific outcome",
                        "research_contract": "Reuse admissible existing objects",
                        "final_gate": "Resolve the stated inference",
                        "agent_graph": TEST_AGENT_GRAPH,
                        "candidate": [{
                            "candidate_id": "cand-1",
                            "hypothesis": "H",
                            "scope": "S",
                            "decisive_test": "T",
                            "why_now": "Gap",
                            "goal_advance": "Discrimination",
                        }],
                    },
                )
                assembled = build_initial_prompt(
                    prefix="test",
                    role=role,
                    stage=stage,
                    contract_ref=contract.ref,
                    contract_schema=contract.prompt_projection({}),
                    context=context,
                    write_rule="read only",
                )
                self.assertIn("Current task: " + stage, assembled)
                self.assertIn("Output contract:", assembled)
                self.assertNotIn("P(d)", card + procedure)
                self.assertNotIn("network-level", (card + procedure).lower())
                self.assertNotIn("same-input", (card + procedure).lower())
                self.assertNotIn("candidate_id", card)
                self.assertNotIn("blocking_issues", card)
                self.assertNotIn("required_change", card)
                self.assertNotIn("qualified/revise/reject", procedure)
                limit = 6000 if role == "research_lead" else 4500
                self.assertLess(len(card + procedure), limit)

        self.assertNotIn("lineage_anchor_node_ids", self.lead)
        self.assertNotIn("evolution_operator", self.lead)
        self.assertEqual(role_contract("challenger", phase="candidate_review").version, 5)
        self.assertIn(("challenger", "repair_verification"), STAGE_CARDS)
        self.assertIn(("research_lead", "frontier_proposal"), PROCEDURE_CARDS)
        self.assertIn(("challenger", "candidate_review"), PROCEDURE_CARDS)

    def test_non_h8_math_and_ai4s_scenarios_use_generic_rules(self):
        scenarios = (
            {
                "candidate_id": "math-proof-1",
                "hypothesis": "A spectral inequality holds for a bounded family of sparse symmetric matrices.",
                "scope": "Finite matrix families with explicit dimension and degree bounds.",
                "why_now": "A proof or certified counterexample would establish the conjecture's boundary in this class.",
                "goal_advance": "Resolve the scoped conjecture and constrain the broader theorem.",
                "decisive_test": "Attempt a rigorous derivation and a bounded search for a certified counterexample; if unresolved, report the exact obstruction and scope limits.",
            },
            {
                "candidate_id": "ai4s-algorithm-1",
                "hypothesis": "A graph-neural surrogate reduces held-out molecular-property error relative to a relevant baseline in a specified scaffold regime.",
                "scope": "Two held-out scaffold strata under a frozen split; no claim of universal superiority.",
                "why_now": "The comparison tests whether the architecture adds information beyond the established baseline.",
                "goal_advance": "A useful reduction or a bounded null effect will constrain the method's value.",
                "decisive_test": "Freeze the evaluation protocol, compare errors on held-out scaffold groups, and report the effect estimate with uncertainty and meaningful contrary or inconclusive interpretations.",
            },
        )
        for candidate in scenarios:
            with self.subTest(candidate=candidate["candidate_id"]):
                prompt = self._challenger_prompt(candidate)
                self.assertIn(candidate["hypothesis"], prompt)
                self.assertIn("credible in-scope route", prompt)
                self.assertIn("matching, contrary and genuinely inconclusive", prompt.lower())
                for domain_specific in ("P(d)", "network-level", "same-input", "52 networks", "H8"):
                    self.assertNotIn(domain_specific.lower(), prompt.lower())
        self.assertIn("do not impose matching when the science does not require it", self.review_procedure)



if __name__ == "__main__":
    unittest.main()
