"""Verify policy delivery and science preservation; no simulated quality verdicts."""
import unittest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context
from theory_master.identity import identity_block
from theory_master.stage_registry import stage_definition_text


class SpecificScientificClaimTests(unittest.TestCase):
    def test_scientific_commitment_is_checked_before_selection_not_repeated_in_every_stage(self):
        obligations = {
            ("research_lead", "frontier_proposal"): (
                "specific proposed answer", "discriminating consequence", "Goal restatements",
                "material definitions", "remain Investigator work"),
            ("challenger", "candidate_review"): (
                "specific scientific commitment", "discriminating consequence", "not a Goal restatement",
                "finite failed search is not general refutation", "Do not demand the research answer"),
            ("research_lead", "candidate_selection"): (
                "already-qualified set", "Do not reopen qualification", "select one unchanged"),
            ("research_lead", "selection_repair"): (
                "frozen Challenger blocker ledger", "preserving the parent hypothesis",
                "COMPLETE revised decisive_test", "cannot_repair"),
            ("challenger", "repair_verification"): (
                "original blocker ledger", "all original blockers", "complete decisive_test",
                "Do not renew the full novelty or Goal-priority review"),
        }
        for (role, stage), rules in obligations.items():
            contract = role_contract(role, phase=stage)
            for continuation in (False, True):
                with self.subTest(role=role, stage=stage, continuation=continuation):
                    context = stage_context(role, stage, {
                        "goal": "Explain the entire response and its limiting behavior.",
                        "agent_graph": {"hypotheses": [], "current_frontiers": [], "lineage": []},
                    }, continuation=continuation)
                    args = dict(role=role, stage=stage, contract_ref=contract.ref,
                                contract_schema=contract.prompt_projection(context), context=context)
                    prompt = (build_continuation_prompt(**args) if continuation else
                              build_initial_prompt(prefix="offline", write_rule="read only", **args))
                    for rule in rules:
                        self.assertIn(rule, prompt)
                    self.assertEqual(prompt.count("Stage: " + stage), 1)
        # Universal scientific honesty remains in the persistent Identity.
        self.assertIn("有限搜索无结果不能充当一般性反证", identity_block("research_lead"))
        selection = stage_definition_text("research_lead", "candidate_selection")
        self.assertNotIn("specific proposed answer", selection)
        self.assertNotIn("full-sequence", selection)

    def test_selection_and_repair_contracts_enforce_bounded_authority(self):
        selection = role_contract("research_lead", phase="candidate_selection")
        context = {"candidates": [{"id": "qualified-1"}], "banned_candidate_ids": ["unqualified"]}
        valid = {"selected_candidate_id": "qualified-1", "selection_reason": "Goal information."}
        selection.structural_validator(valid, context)
        for invalid in (
            {**valid, "selected_candidate_id": "unqualified"},
            {**valid, "hypothesis": "Rewritten scientific claim"},
        ):
            with self.assertRaises(ValueError):
                selection.structural_validator(invalid, context)
        repair = role_contract("research_lead", phase="selection_repair")
        repair_context = {"qualification_error": {"verdict": "revise"},
                          "candidates": [{"id": "revise-1", "decisive_test": "Original test"}]}
        cannot = {"outcome": "cannot_repair", "cannot_repair_reason": "Would change the estimand."}
        repair.structural_validator(cannot, repair_context)
        for invalid in (
            {**cannot, "hypothesis": "Replacement scientific target"},
            {"outcome": "resolved", "repair": {"blocker_closures": []}},
        ):
            with self.assertRaises(ValueError):
                repair.structural_validator(invalid, repair_context)

    def test_review_inputs_preserve_broad_and_specific_claims_without_sdk_classification(self):
        # Negative and positive review cases; only a scientific reviewer may classify them.
        cases = (
            ("Derive a law meeting all Goal requirements.", "Predict the full curve, conserve totals and prove the limit."),
            ("Some model with at most four parameters explains every response.", "Find a model and assess its accuracy."),
            ("The state evolves by a generation-independent linear operator on a fixed two-component state.",
             "Test the resulting semigroup relation on complete trajectories against the prior unrestricted operator."),
            ("Every positive definite matrix in the stated class satisfies the proposed bound.",
             "Prove the bound or exhibit a member of that class violating it."),
            ("There exists a degree-two polynomial satisfying the stated endpoint and positivity conditions.",
             "Construct a qualifying polynomial or prove infeasibility over the specified coefficient domain."),
        )
        for hypothesis, test in cases:
            candidate = dict(id="review-case", hypothesis=hypothesis,
                             scope="Explicit domain and independent comparison units.",
                             decisive_test=test, why_now="Closest prior result and remaining unknown.",
                             goal_advance="Outcome changes the proposed explanation.")
            for continuation in (False, True):
                context = stage_context("challenger", "candidate_review", {"candidate": [candidate],
                                        "agent_graph": {"hypotheses": [], "current_frontiers": [], "lineage": []}},
                                        continuation=continuation)
                self.assertEqual(context["candidate"][0]["frozen_objective"],
                                 {k: candidate[k] for k in ("hypothesis", "scope", "decisive_test")})


if __name__ == "__main__":
    unittest.main()
