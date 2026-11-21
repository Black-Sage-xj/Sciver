"""Handwritten pre-lock feasibility oracles; no archived research is bundled.

These checks validate generic prompt and transport rules, not LLM verdicts.
"""
from __future__ import annotations

import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.stage_registry import (
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

    def test_exact_input_matching_has_a_cheap_eligibility_gate(self):
        # Handwritten identifiers have no matched pair; no scientific data is used.
        encodings = [("fixture-category", f"unique-{i}") for i in range(5)]
        pairs = [
            (i, j) for i in range(5) for j in range(i + 1, 5)
            if encodings[i] == encodings[j]
        ]
        self.assertEqual(pairs, [])

        for body in self.prompt_bodies:
            for domain_specific in ("fixture-category", "unique-0", "synthetic-record"):
                self.assertNotIn(domain_specific.lower(), body.lower())

        self.assertIn("existing inputs/metadata", self.lead_procedure)
        self.assertIn("comparison eligibility", self.lead_procedure)
        self.assertIn("eligible independent units", self.lead_procedure)
        self.assertIn("required comparator/contrast", self.review_procedure)
        self.assertIn("credible essential route", self.review)
        self.assertIn("comparison eligibility, matching or controls", self.lead_procedure)
        self.assertIn("do not impose matching on a design that does not require it", self.review_procedure)

    def test_uncertainty_and_common_support(self):
        # Illustrative overlapping intervals: point estimates exist, but they
        # cannot resolve the frozen signed contrast.
        estimate_a, err_a = 1.0, 0.8
        estimate_b, err_b = 1.5, 0.8
        contrast_lower_bound = abs(estimate_a - estimate_b) - err_a - err_b
        self.assertLessEqual(contrast_lower_bound, 0)

        self.assertIn("uncertainty", self.lead.lower())
        self.assertIn("uncertainty and proof precision", self.lead.lower())
        self.assertIn("for empirical designs also assess independent units", self.review)
        self.assertIn("statistically unreachable outcomes under the stated design", self.review)
        self.assertIn("independent units", self.lead_procedure)
        self.assertIn("uncertainty/error budget", self.lead_procedure)
        self.assertIn("joint/conditional support", self.lead_procedure)
        self.assertIn("can in principle distinguish the stated outcomes", self.lead_procedure)
        self.assertIn("credible in-scope route", self.review)
        self.assertIn("Do not demand the research answer at qualification", self.review)

    def test_local_implication_and_negative_result_remain_eligible(self):
        # A necessary local consequence can refute a universal claim, but
        # support in one local window does not establish the whole claim.
        local_predictions = {"window_a": True, "window_b": False}
        self.assertTrue(local_predictions["window_a"])
        self.assertFalse(all(local_predictions.values()))
        self.assertIn(
            "a local contrary result refutes H only if it contradicts a necessary local prediction",
            self.lead,
        )
        self.assertIn("local result bears on a broader claim only within its actual logical implication", self.review)
        self.assertIn("negative", self.lead.lower())
        self.assertIn("eventual negative/open result is not itself a blocker", self.review)
        self.assertIn("uncertain effect", self.review.lower())
        self.assertIn("credible in-scope route and meaningful test remain", self.review)
        self.assertIn("Reject the current design, not its general topic", self.review)

    def test_stage_contract_separation_and_single_delivery(self):
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
                self.assertNotIn("fixture-category", card + procedure)
                self.assertNotIn("synthetic-record", (card + procedure).lower())
                self.assertNotIn("same-input", (card + procedure).lower())
                self.assertNotIn("candidate_id", card)
                self.assertNotIn("blocking_issues", card)
                self.assertNotIn("required_change", card)
                self.assertNotIn("qualified/revise/reject", procedure)
                self.assertEqual(assembled.count(card + procedure), 1)

        self.assertNotIn("lineage_anchor_node_ids", self.lead)
        self.assertNotIn("evolution_operator", self.lead)
        self.assertEqual(role_contract("challenger", phase="candidate_review").version, 6)
        self.assertIn(("challenger", "repair_verification"), STAGE_CARDS)
        self.assertIn("procedure", STAGE_CARDS[("research_lead", "frontier_proposal")])
        self.assertIn("procedure", STAGE_CARDS[("challenger", "candidate_review")])

    def test_math_and_algorithm_scenarios_use_generic_rules(self):
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
                self.assertIn("matching, contrary and inconclusive", prompt.lower())
                self.assertIn("purely theoretical or existence proofs need no data matching or statistical sample requirement", prompt)
                for domain_specific in ("fixture-category", "unique-0", "synthetic-record"):
                    self.assertNotIn(domain_specific.lower(), prompt.lower())
        self.assertIn("do not impose matching on a design that does not require it", self.review_procedure)

    def test_synthetic_scientific_definitions_reach_challenger_without_rewriting(self):
        candidates = (
            {"hypothesis": "A finite rewrite system terminates for strings of length at most eight.",
             "scope": "The three explicitly declared rewrite rules only.",
             "decisive_test": "Exhaustively enumerate the bounded state graph or exhibit a cycle."},
            {"hypothesis": "Estimator A improves on the median for the declared synthetic samples.",
             "scope": "A frozen generated table, with paired error comparison.",
             "decisive_test": "Compare held-out paired errors and report uncertainty."},
            {"hypothesis": "The proposed integer recurrence preserves parity.",
             "scope": "Nonnegative integers with the stated base condition.",
             "decisive_test": "Prove the invariant by induction or return an exact counterexample."},
        )
        for candidate in candidates:
            with self.subTest(hypothesis=candidate["hypothesis"]):
                context = stage_context("challenger", "candidate_review", {
                    "candidate": [candidate], "agent_graph": TEST_AGENT_GRAPH,
                })
                self.assertEqual(context["candidate"][0]["frozen_objective"], candidate)
                prompt = self._challenger_prompt(candidate)
                for value in candidate.values():
                    self.assertIn(value, prompt)


if __name__ == "__main__":
    unittest.main()
