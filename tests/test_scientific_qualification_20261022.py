"""Generic qualification design oracles using handwritten synthetic examples.

No private research archive or live model is used.
"""
from __future__ import annotations

import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_initial_prompt, stage_context
from theory_master.stage_registry import stage_card_text, stage_definition_text


class ScientificQualification20261022(unittest.TestCase):
    def setUp(self):
        self.lead = stage_definition_text("research_lead", "frontier_proposal")
        self.challenger = stage_definition_text("challenger", "candidate_review")

    def test_lead_compares_scientific_value_not_gate_bookkeeping(self):
        # Stage owns scientific criteria; Contract owns the two named output fields.
        projection = role_contract("research_lead", phase="frontier_proposal").prompt_projection()
        self.assertIn("goal_advance", projection)
        self.assertIn("why_now", projection)

    def test_positive_local_result_is_still_eligible(self):
        # A bounded implication can be useful before the full theorem is resolved.
        self.assertIn("claim can advance the Goal without completing it", self.lead)
        self.assertIn("necessary local prediction", self.lead)
        self.assertIn("eventual negative/open result is not itself a blocker", self.challenger)

    def test_finite_examples_do_not_establish_universal_bound(self):
        candidate = {"hypothesis": "Normalization preserves the checksum of every finite string.",
                     "scope": "All finite strings.", "decisive_test": "Verify the invariant on ten examples."}
        context = stage_context("challenger", "candidate_review", {
            "candidate": [candidate], "agent_graph": {},
        })
        self.assertEqual(context["candidate"][0]["frozen_objective"], candidate)
        self.assertIn("Match uncertainty and proof precision to the scientific distinction claimed", self.lead)

    def test_negative_prediction_still_informs_goal(self):
        # A counterexample can delimit a synthetic recurrence claim.
        self.assertIn("negative", self.challenger)
        self.assertNotIn("all hypotheses must pass", self.challenger)

    def test_compound_claims_receive_independent_blocker_review(self):
        # An undefined estimator and missing evaluation control are separate blockers.
        blockers = [
            {"problem": "Estimator undefined", "required_change": "Specify the stated estimator."},
            {"problem": "Needed control absent", "required_change": "Identify an admissible control."},
        ]
        review = {"candidate_id": "compound", "verdict": "revise",
                  "reason": "The current test depends on both conditions.", "blocking_issues": blockers}
        contract = role_contract("challenger", phase="candidate_review")
        contract.validate({"reviews": [review]}, {"candidates": [{"id": "compound"}]})
        projected = stage_context("challenger", "repair_verification", {
            "original_review": review, "agent_graph": {},
        })
        self.assertEqual(projected["original_blockers"], blockers)
        self.assertIn("all visible independent blockers", self.challenger)

    def test_unavailable_essential_precondition_is_a_design_issue(self):
        # A required lemma without a credible route cannot be fixed by wording alone.
        self.assertIn("essential route is demonstrably unavailable", self.challenger)
        self.assertIn("unchanged hypothesis/scope/estimand", self.challenger)

    def test_contract_and_prompt_assembly_unchanged(self):
        lead_contract = role_contract("research_lead", phase="frontier_proposal")
        challenger_contract = role_contract("challenger", phase="candidate_review")
        self.assertEqual(challenger_contract.version, 6)
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
                "goal": "Verify string normalization invariants", "research_contract": "Known inputs only",
                "final_gate": "Resolve all declared input classes",
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
            self.assertIn("Verify string normalization invariants", prompt)
            self.assertIn("credible", prompt)
            self.assertIn("Output contract:", prompt)
            self.assertIn("Known inputs only", prompt)
            self.assertIn("Resolve all declared input classes", prompt)
            self.assertNotIn("descriptive reproduction alone is insufficient", prompt.casefold())
        self.assertIn("Do not reopen qualification", stage_card_text("research_lead", "candidate_selection"))
        verification = role_contract("challenger", phase="repair_verification")
        self.assertIn("confirmed", verification.prompt_projection())
        self.assertLess(len(self.lead), 12500)
        self.assertLess(len(self.challenger), 10500)

    def test_synthetic_candidate_science_is_preserved_in_review_context(self):
        examples = (
            ("Normalization is idempotent.", "Finite strings under the stated rules.",
             "Provide a proof or an exact string counterexample."),
            ("The proposed estimator lowers absolute error.", "The declared synthetic paired table only.",
             "Compare paired errors with a frozen uncertainty rule."),
            ("A bounded rewrite system terminates.", "Declared rules and strings of at most eight characters.",
             "Enumerate reachable states and detect any directed cycle."),
        )
        for hypothesis, scope, decisive_test in examples:
            candidate = {"hypothesis": hypothesis, "scope": scope, "decisive_test": decisive_test}
            with self.subTest(hypothesis=hypothesis):
                context = stage_context("challenger", "candidate_review", {
                    "candidate": [candidate], "agent_graph": {
                        "schema": "theorymaster/test-agent-graph/v1", "hypotheses": [],
                        "current_frontiers": [], "lineage": [], "external_sources": [],
                    },
                })
                self.assertEqual(context["candidate"][0]["frozen_objective"], candidate)
                contract = role_contract("challenger", phase="candidate_review")
                prompt = build_initial_prompt(
                    prefix="test", role="challenger", stage="candidate_review", contract_ref=contract.ref,
                    contract_schema=contract.prompt_projection(context), context=context, write_rule="read only",
                )
                for value in candidate.values():
                    self.assertIn(value, prompt)


if __name__ == "__main__":
    unittest.main()
