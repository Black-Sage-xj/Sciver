"""Offline design counterexamples and production prompt-delivery checks.

No model classifies a proposal here. These tests establish the distinctions the
prompt must preserve and its actual initial/continuation delivery, not scientific
novelty or compliance by a future Research Lead.
"""
import unittest

from theory_master.stage_registry import stage_card_text, procedure_card_text
from tests import test_input_qualification_evidence_20261022 as fixtures


class LeadHistoryQuestionScope(unittest.TestCase):
    def test_reparameterization_can_leave_every_prediction_unchanged(self):
        # A renamed parameter and a different-looking formula are no new test.
        for amplitude in (-3, 0, 2):
            for x in (-2, 0, 1, 4):
                original = amplitude * x
                renamed = (amplitude / 2) * (2 * x)
                self.assertEqual(original, renamed)

    def test_a_changed_physical_target_can_add_real_discrimination(self):
        # An old quadratic-channel test does not settle a linear-channel claim:
        # these mechanisms agree on the old observable but differ on the new one.
        quadratic = 2
        for x in (1, 2, 3):
            old_observations = [quadratic * x * x for linear in (-1, 1)]
            new_observations = [linear * x for linear in (-1, 1)]
            self.assertEqual(*old_observations)
            self.assertNotEqual(*new_observations)
        # Thus "alone does not establish novelty" is not a ban on new targets.

    def test_initial_and_continuation_require_history_difference_and_scientific_scale(self):
        helper = fixtures.InputQualificationEvidence()
        for prompt in helper.prompts("research_lead", "frontier_proposal"):
            for rule in (
                "Before proposing, search formal research history",
                "beyond declared parents and recent Cycles",
                "definitions, actual test, verdict, scope",
                "older defining or adverse results",
                "titles or citations do not suffice",
                "equivalent terms, mathematical descriptions and pagination",
                "one keyword miss or first page does not establish novelty",
                "inherited result, unresolved gap and new discriminating information",
                "Renaming, reparameterizing or changing targets/scope alone does not establish novelty",
                "justify what new knowledge the change enables",
                "expose the gap rather than claim novelty",
                "avoid umbrella programs and fragments that add no scientific information",
            ):
                self.assertIn(rule, prompt)
            for preserved in (
                "A useful counterexample, bounded mechanism, or theory boundary",
                "Local, negative, high-risk or otherwise credible work remains eligible",
                "new derivations and the full decisive test remain Investigator work",
                "Do not create filler or preparation-only candidates",
                "keep each Cycle to one coherent adjudicable claim",
                "never open or parse exploration.canvas",
            ):
                self.assertIn(preserved, prompt)
            self.assertIn("Output contract:", prompt)
        rendered = (stage_card_text("research_lead", "frontier_proposal")
                    + procedure_card_text("research_lead", "frontier_proposal"))
        self.assertLess(len(rendered), 6000)


if __name__ == "__main__":
    unittest.main()
