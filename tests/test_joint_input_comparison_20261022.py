"""Offline counterexamples for the strength of qualification input evidence.

The arithmetic is a design oracle. Prompt checks establish production delivery,
not that a real model will detect the counterexample or issue a correct verdict.
No model, response fit, scientific experiment or production task is run.
"""
from __future__ import annotations

import unittest

from tests import test_input_qualification_evidence_20261022 as input_fixtures


class JointInputComparison(unittest.TestCase):
    def test_marginal_range_pass_can_have_no_joint_support(self):
        # In this fixture eligibility explicitly requires the same joint
        # relation y=x, not merely each coordinate in the training range.
        calibration = [(0, 0), (1, 1)]
        evaluation = [(0, 1), (1, 0)]
        for row in evaluation:
            self.assertTrue(all(min(x[j] for x in calibration) <= row[j] <=
                                max(x[j] for x in calibration) for j in (0, 1)))
        train_relations = {y - x for x, y in calibration}
        eligible = [row for row in evaluation if row[1] - row[0] in train_relations]
        self.assertEqual(eligible, [])
        # The same cheap check also recognizes a genuinely supported object.
        supported = (0.5, 0.5)
        self.assertIn(supported[1] - supported[0], train_relations)

    def test_local_conditional_contrast_can_exist_without_exact_full_input_matches(self):
        # The claim uses a stated coarse control, not exact feature equality.
        # Joint support should be assessed in that declared representation.
        controls = lambda row: row[0] // 10
        calibration = [(1, 0), (2, 1), (11, 0), (12, 1)]
        evaluation = [(3, 0), (4, 1), (13, 0), (14, 1)]
        self.assertTrue(set(calibration).isdisjoint(evaluation))
        train = {(controls(row), row[1]) for row in calibration}
        self.assertTrue(all((controls(row), row[1]) in train for row in evaluation))
        for control in (0, 1):
            for rows in (calibration, evaluation):
                self.assertEqual({row[1] for row in rows if controls(row) == control}, {0, 1})

    def test_production_initial_and_continuation_bind_evidence_to_inference(self):
        helper = input_fixtures.InputQualificationEvidence()
        instructions = {
            ("research_lead", "frontier_proposal"): (
                "If decisive_test depends on comparison eligibility, matching or controls",
                "joint/conditional support; marginal ranges and catalog counts do not establish it",
                "Do not impose matching or joint-support tests on questions that do not require them",
                "cite sources and observed results", "credible scoped route and informative failure outcome",
                "do not fit responses, select samples from outcomes",
            ),
            ("challenger", "candidate_review"): (
                "Joint/conditional support checks apply only to actual empirical comparisons needing those controls",
                "do not impose matching on a design that does not require it",
                "cite the source and observed result", "credible scoped route and an informative failure outcome",
                "purely theoretical or existence proofs need no data matching or statistical sample requirement",
                "no response fitting, outcome-based sample selection",
            ),
            ("challenger", "repair_verification"): (
                "Apply full-sequence restrictions and joint/conditional support only when established",
                "Purely theoretical or existence proofs need no empirical matching or statistical sample requirement",
                "Apply frozen eligibility restrictions without relaxing them for coverage",
                "cite source/observed result", "no response fitting, outcome-based sample selection",
            ),
        }
        for (role, stage), rules in instructions.items():
            for prompt in helper.prompts(role, stage):
                with self.subTest(role=role, stage=stage):
                    for rule in rules:
                        self.assertIn(rule, prompt)
                    self.assertIn("Investigator", prompt)

    def test_empty_data_escape_is_not_a_blanket_qualification_license(self):
        # Assembly boundary only: no LLM is asked to reject this empty plan.
        candidate = {"id": "cand-empty-plan", "hypothesis": "Find a new explanation",
                     "scope": "unspecified inputs", "decisive_test":
                     "Check later whether data exists; if absent call it indeterminate without a scientific inference",
                     "why_now": "No actual comparison supplied", "goal_advance": "unspecified"}
        helper = input_fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("research_lead", "frontier_proposal", {"candidate": [candidate]}):
            self.assertIn("Avoid umbrella programs, filler, preparation-only work", prompt)
            self.assertIn("expose missing indispensable checks", prompt)
            self.assertIn("credible scoped route and informative failure outcome", prompt)
        for prompt in helper.prompts("challenger", "candidate_review", {"candidate": [candidate]}):
            self.assertIn(candidate["decisive_test"], prompt)
            self.assertIn("without a material blocker", prompt)
            self.assertIn("check-later plan do not establish necessary eligibility", prompt)
            self.assertIn("unavailable material history is a qualification gap", prompt)

    def test_new_scientific_results_are_not_required_to_establish_input_qualification(self):
        helper = input_fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("research_lead", "frontier_proposal"):
            self.assertIn("negative result or high-risk claim can advance the Goal", prompt)
            self.assertIn("unknown derivations and the decisive experiment remain Investigator work", prompt)
            self.assertIn("require the new scientific result", prompt)
        for prompt in helper.prompts("challenger", "candidate_review"):
            self.assertIn("eventual negative/open result is not itself a blocker", prompt)
            self.assertIn("Do not demand optional stronger claims", prompt)
            self.assertIn("Do not demand the research answer at qualification", prompt)
            self.assertIn("verify cheap necessary input/metadata conditions", prompt)


if __name__ == "__main__":
    unittest.main()
