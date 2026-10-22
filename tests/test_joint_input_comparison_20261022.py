"""Offline counterexamples for the strength of qualification input evidence.

The arithmetic is a design oracle. Prompt checks establish production delivery,
not that a real model will detect the counterexample or issue a correct verdict.
No model, response fit, scientific experiment or production task is run.
"""
from __future__ import annotations

import unittest

from theory_master.stage_registry import INPUT_COMPARISON_BOUNDARY
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
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review"),
                            ("challenger", "repair_verification")):
            for prompt in helper.prompts(role, stage):
                with self.subTest(role=role, stage=stage):
                    self.assertEqual(prompt.count(INPUT_COMPARISON_BOUNDARY), 1)
                    for text in ("actual comparison units", "input/control representation",
                                 "Marginal ranges alone do not establish joint or conditional support",
                                 "state only what the observed check establishes",
                                 "Do not require universal exact matching",
                                 "If eligibility needs new science",
                                 "credible scoped route",
                                 "scientifically informative insufficient-support outcome",
                                 "not a check-later plan or claimed proof"):
                        self.assertIn(text, prompt)
                    self.assertIn("response fitting", prompt)
                    self.assertIn("outcome-based sample selection", prompt)
                    self.assertIn("Investigator", prompt)

    def test_empty_data_escape_is_not_a_blanket_qualification_license(self):
        # Assembly boundary only: no LLM is asked to reject this empty plan.
        candidate = {"id": "cand-empty-plan", "hypothesis": "Find a new explanation",
                     "scope": "unspecified inputs", "decisive_test":
                     "Check later whether data exists; if absent call it indeterminate without a scientific inference",
                     "why_now": "No actual comparison supplied", "goal_advance": "unspecified"}
        helper = input_fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("research_lead", "frontier_proposal", {"candidate": [candidate]}):
            self.assertIn("Do not create filler or preparation-only candidates", prompt)
            self.assertIn("scientifically informative insufficient-support outcome", prompt)
            self.assertIn("not a check-later plan or claimed proof", prompt)
        for prompt in helper.prompts("challenger", "candidate_review", {"candidate": [candidate]}):
            self.assertIn(candidate["decisive_test"], prompt)
            self.assertIn("advances an unmet Goal with no material blocker", prompt)
            self.assertIn("missing cheap necessary checks leave a qualification gap", prompt)
            self.assertIn("not a check-later plan or claimed proof", prompt)

    def test_new_scientific_results_are_not_required_to_establish_input_qualification(self):
        helper = input_fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("research_lead", "frontier_proposal"):
            self.assertIn("Local, negative, high-risk or otherwise credible work remains eligible", prompt)
            self.assertIn("new derivations and the full decisive test remain Investigator work", prompt)
            self.assertIn("Do not create filler or preparation-only candidates", prompt)
        for prompt in helper.prompts("challenger", "candidate_review"):
            self.assertIn("Bounded mechanisms, counterexamples and negative results can be valuable", prompt)
            self.assertIn("Do not demand optional stronger claims", prompt)
            self.assertIn("Do not demand the new research result at qualification", prompt)
            self.assertIn("Omitted cheap input checks remain qualification gaps", prompt)


if __name__ == "__main__":
    unittest.main()
