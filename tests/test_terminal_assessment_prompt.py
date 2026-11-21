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
            "Future questions and optional strengthening do not block scientific closure",
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
            "A contrary result refutes a claim if it contradicts a necessary prediction",
            card,
        )

    def test_optional_strengthening_does_not_justify_milestone(self):
        card = stage_card_text("judge", "terminal_assessment")

        self.assertIn("optional strengthening do not block scientific closure", card)
        self.assertIn("Ground each objection in a specific necessary frozen requirement", card)
        self.assertIn("If necessary work is still executable within the frozen scope", card)

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
