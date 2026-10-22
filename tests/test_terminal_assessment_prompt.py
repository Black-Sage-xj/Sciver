import unittest

from theory_master.identity import identity_block
from theory_master.stage_registry import stage_card_text


class TerminalAssessmentPromptTest(unittest.TestCase):
    def test_terminal_assessment_is_bounded_to_locked_hypothesis(self):
        card = stage_card_text("judge", "terminal_assessment")

        self.assertIn(
            "Assess terminality only for the exact locked Hypothesis and its frozen scope",
            card,
        )
        self.assertIn(
            "not for whether the overall research topic still has future questions",
            card,
        )
        self.assertIn(
            "Use milestone_advanced only when",
            card,
        )
        self.assertIn(
            "logically required by the current locked Hypothesis itself",
            card,
        )
        self.assertIn(
            "stronger hypotheses, broader scope, or optional scientific strengthening",
            card,
        )

    def test_optional_strengthening_does_not_justify_milestone(self):
        card = stage_card_text("judge", "terminal_assessment")

        self.assertIn("Do not block closure or justify milestone_advanced for stronger hypotheses", card)
        self.assertIn("optional scientific strengthening (extra robustness, higher confidence, future propositions)", card)
        self.assertIn("unless the locked Hypothesis and frozen scope logically require them", card)

    def test_terminal_scope_boundary_lives_in_stage_card_not_shared_role_core(self):
        card = stage_card_text("judge", "terminal_assessment")
        identity = identity_block("judge")

        self.assertIn("Assess terminality only for the exact locked Hypothesis and its frozen scope", card)
        for phrase in (
            "frozen scope",
            "future questions",
            "未来问题",
            "milestone_advanced",
            "strengthening",
            "train/test split",
        ):
            self.assertNotIn(phrase, identity)


if __name__ == "__main__":
    unittest.main()
