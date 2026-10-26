"""Characterization tests for Goal-aware scientific test-design prompting.

These assert actual stage-prompt composition and authority boundaries, not
that a language model will follow a prompt in a future live research Cycle.
"""
from __future__ import annotations

import unittest

from theory_master.contracts import role_contract
from theory_master.prompt_stages import (
    build_continuation_prompt, build_initial_prompt, stage_context,
)
from theory_master.stage_registry import stage_card_text


class DecisiveTestInferentialQualityPromptTests(unittest.TestCase):
    def _turn_prompts(self, role: str, stage: str, context: dict):
        common = {
            "role": role,
            "stage": stage,
            "contract_ref": "theory-master/role/test-only",
            "contract_schema": '{"answer": "string"}',
            "context": context,
        }
        return (
            build_initial_prompt(prefix="[test]", write_rule="No writes.", **common),
            build_continuation_prompt(**common),
        )

    def test_lead_must_consider_scientific_increment_only_when_relevant(self):
        for prompt in self._turn_prompts(
            "research_lead", "frontier_proposal",
            {"goal": "predict energy", "final_gate": "independent physical mechanism"},
        ):
            with self.subTest(prompt_kind=prompt.splitlines()[0]):
                self.assertIn("substantive change from prior claims", prompt)
                self.assertIn("simpler explanation", prompt)
                self.assertIn("existing method", prompt)
                self.assertIn("appropriate contrast", prompt)
                self.assertIn("necessary local prediction", prompt)
                self.assertIn("do not require irrelevant baselines", prompt)
                self.assertIn("Current task: frontier_proposal", prompt)
                self.assertIn("Context:", prompt)

    def test_lead_must_define_informative_precision_relative_to_inference(self):
        card = stage_card_text("research_lead", "frontier_proposal")
        self.assertIn("uncertainty and proof precision", card)
        self.assertIn("the scientific distinction claimed", card)
        self.assertIn("logically attainable", card)
        self.assertIn("for statistical tests, statistically reachable", card)
        self.assertIn("nonzero, finite or converged estimate", card)
        self.assertIn("Avoid universal sample sizes or thresholds", card)
        for other_role, other_stage in (
            ("investigator", "investigate"), ("judge", "terminal_assessment"),
            ("reporter", "report"),
        ):
            # Qualification's comparison of proposed directions must not be
            # injected into execution, adjudication or reporting.
            other = stage_card_text(other_role, other_stage)
            self.assertNotIn("compare with a simpler explanation or existing method", other)
            self.assertNotIn("substantive change from prior claims", other)

    def test_challenger_independent_capacity_audit_and_qualified_local_results(self):
        for prompt in self._turn_prompts(
            "challenger", "candidate_review",
            {"candidate": [{"hypothesis": "Prediction", "decisive_test": "Preplanned evaluation"}]},
        ):
            for instruction in (
                "relevant simpler explanations or existing methods",
                "Check logical reachability of the claimed decision",
                "for empirical designs also assess independent units",
                "statistically unreachable outcomes under the stated design",
                "informative estimate or bound with uncertainty",
                "local result bears on a broader claim only within its actual logical implication",
                "eventual negative/open result is not itself a blocker",
                "credible in-scope route and meaningful test remain",
                "Do not demand optional stronger claims, irrelevant baselines",
                "purely theoretical or existence proofs need no data matching",
            ):
                self.assertIn(instruction, prompt)
            self.assertNotIn("R_eff", prompt)
            self.assertNotIn("p=0.025", prompt)

    def test_no_new_candidate_fields_or_verdicts_and_no_goal_rewriting(self):
        lead_contract = role_contract("research_lead", phase="frontier_proposal")
        challenger_contract = role_contract("challenger", phase="candidate_review")
        self.assertIn("candidates", lead_contract.prompt_schema)
        self.assertIn("reviews", challenger_contract.prompt_schema)
        self.assertIn("qualified", str(challenger_contract.prompt_schema).lower()
                      + stage_card_text("challenger", "candidate_review").lower())
        self.assertIn("revise", stage_card_text("challenger", "candidate_review"))
        self.assertIn("reject", stage_card_text("challenger", "candidate_review"))
        sample = {
            "hypothesis": "A hard-to-prove exact statement",
            "scope": "finite deterministic domain",
            "why_now": "Goal",
            "goal_advance": "Useful theorem",
            "decisive_test": "Find a rigorous analytic counterexample or a proof.",
        }
        candidate = stage_context("challenger", "candidate_review", {
            "candidate": [sample],
            "agent_graph": {"schema": "theorymaster/agent-science-graph/v1", "hypotheses": [],
                            "current_frontiers": [], "lineage": [], "focus_details": [],
                            "external_sources": [], "external_references": [], "source_relations": []},
        })["candidate"][0]
        # The current projection separates binding science from proposal
        # rationale; all original scientific content must survive that split.
        self.assertEqual(candidate["frozen_objective"], {
            key: sample[key] for key in ("hypothesis", "scope", "decisive_test")
        })
        self.assertEqual(candidate["proposal_context"], {
            key: sample[key] for key in ("why_now", "goal_advance")
        })
        self.assertNotIn("power", candidate)
        self.assertNotIn("statistical_design", candidate)
        self.assertNotIn("baseline", candidate)
        self.assertNotIn("test_info_gain", candidate)
        # This change belongs to proposal/qualification, never native Goal or
        # the Judge's already-frozen terminal scientific criteria.
        for role, stage in (("investigator", "investigate"), ("judge", "terminal_assessment")):
            self.assertNotIn("Independently qualify each supplied candidate",
                             stage_card_text(role, stage))

    def test_cycle53_is_counterexample_to_unreachable_significance_exit(self):
        # A deliberately simple math fixture grounding the generic constraint.
        # It is NOT encoded in the stage prompt as a universal sample-size rule.
        n = 3
        smallest_two_sided_p = 2 / (2 ** n)
        asserted_alpha = 0.025
        self.assertEqual(smallest_two_sided_p, .25)
        self.assertGreater(smallest_two_sided_p, asserted_alpha)
        challenger = stage_card_text("challenger", "candidate_review")
        self.assertIn("under the stated design", challenger)
        self.assertIn("statistically unreachable outcomes", challenger)
        self.assertNotIn("0.025", challenger)
        self.assertNotIn("three networks", challenger)
        # Finite exact counterexamples, one-sided exploratory tests, and valid
        # uncertain results must remain viable without arbitrary universal bars.
        self.assertIn("Matching, contrary and inconclusive results must retain scientific meaning", challenger)
        self.assertIn("Do not demand optional stronger claims", challenger)

    def test_cannot_repair_and_investigator_duties_are_unchanged(self):
        repair = stage_card_text("research_lead", "selection_repair")
        self.assertIn("cannot_repair", repair)
        self.assertIn("cannot_repair remains unqualified, does not lock H and does not enter Investigator", repair)
        scientific = stage_card_text("investigator", "investigate")
        self.assertIn("Native Goal supplies the operative scientific completion conditions", scientific)
        self.assertIn("combined findings must address the whole frozen test", scientific)
        self.assertIn("Do not submit a partial theorem", scientific)
        self.assertNotIn("Independently qualify each supplied candidate", scientific)


if __name__ == "__main__":
    unittest.main()
