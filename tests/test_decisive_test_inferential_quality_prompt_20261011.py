"""Characterization tests for Goal-aware scientific test-design prompting.

These assert actual stage-prompt composition and authority boundaries, not
that a language model will follow a prompt in a future live research Cycle.
"""
from __future__ import annotations

import math
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
                self.assertIn("Scientific incremental value", prompt)
                self.assertIn("simpler explanation", prompt)
                self.assertIn("existing method", prompt)
                self.assertIn("appropriate contrast", prompt)
                self.assertIn("necessary local prediction", prompt)
                self.assertIn("Do not require irrelevant baselines", prompt)
                self.assertIn("Current task: frontier_proposal", prompt)
                self.assertIn("Context:", prompt)

    def test_lead_must_define_informative_precision_relative_to_inference(self):
        card = stage_card_text("research_lead", "frontier_proposal")
        self.assertIn("Informative precision", card)
        self.assertIn("the scientific distinction and inference claimed", card)
        self.assertIn("in principle reach it", card)
        self.assertIn("nonzero, finite or converged result", card)
        self.assertIn("Do not impose universal sample sizes or thresholds", card)
        for other_role in ("investigator", "judge", "reporter"):
            self.assertNotIn("Scientific incremental value", stage_card_text(other_role, "investigate"))
            self.assertNotIn("Informative precision", stage_card_text(other_role, "investigate"))

    def test_challenger_independent_capacity_audit_and_qualified_local_results(self):
        for prompt in self._turn_prompts(
            "challenger", "candidate_review",
            {"candidate": [{"hypothesis": "Prediction", "decisive_test": "Preplanned evaluation"}]},
        ):
            self.assertIn("Then check five dimensions", prompt)
            self.assertIn("Inferential capacity and incremental value", prompt)
            self.assertIn("logically or statistically unreachable outcomes", prompt)
            self.assertIn("an 'informative' bound", prompt)
            self.assertIn("relevant simple baseline", prompt)
            self.assertIn("necessary local prediction", prompt)
            self.assertIn("An inconclusive or exploratory outcome is legitimate", prompt)
            self.assertIn("An unresolved prerequisite is not a qualification blocker", prompt)
            self.assertIn("Do not demand an optional stronger claim", prompt)
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
        self.assertEqual(candidate, sample)
        self.assertNotIn("power", candidate)
        self.assertNotIn("statistical_design", candidate)
        self.assertNotIn("baseline", candidate)
        self.assertNotIn("test_info_gain", candidate)
        # This change belongs to proposal/qualification, never native Goal or
        # the Judge's already-frozen terminal scientific criteria.
        self.assertNotIn("Inferential capacity and incremental value",
                         stage_card_text("investigator", "investigate"))
        self.assertNotIn("Inferential capacity and incremental value",
                         stage_card_text("judge", "terminal_assessment"))

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
        self.assertIn("do not require a binary verdict", challenger)
        self.assertIn("Do not demand an optional stronger claim", challenger)

    def test_cannot_repair_and_investigator_duties_are_unchanged(self):
        repair = stage_card_text("research_lead", "selection_repair")
        self.assertIn("cannot_repair", repair)
        self.assertIn("does not enter Investigator", repair)
        scientific = stage_card_text("investigator", "investigate")
        self.assertIn("Complete the full frozen decisive research", scientific)
        self.assertNotIn("Scientific incremental value", scientific)


if __name__ == "__main__":
    unittest.main()
