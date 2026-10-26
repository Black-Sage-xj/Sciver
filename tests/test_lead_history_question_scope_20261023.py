"""Offline design counterexamples and production prompt-delivery checks.

No model classifies a proposal here. These tests establish the distinctions the
prompt must preserve and its actual initial/continuation delivery, not scientific
novelty or compliance by a future Research Lead.
"""
import unittest

from theory_master.identity import identity_block
from theory_master.stage_registry import stage_definition_text
from tests import test_input_qualification_evidence_20261022 as fixtures


class LeadHistoryQuestionScope(unittest.TestCase):
    def test_unadjudicated_history_reaches_both_roles_without_promotion(self):
        # Delivery regression: a missing verdict must not erase an old model
        # from novelty review or manufacture adjudicated support. No LLM judges it.
        history = [{"id": "H-prior", "hypothesis": "Previously proposed two-channel model",
                    "verdict": None, "frontier_status": "parked",
                    "investigation": "Provisional component comparison; not adjudicated"}]
        helper = fixtures.InputQualificationEvidence()
        for role, stage in (("research_lead", "frontier_proposal"),
                            ("challenger", "candidate_review")):
            for prompt in helper.prompts(role, stage, {"agent_graph": {**fixtures.GRAPH, "hypotheses": history}}):
                self.assertIn("Previously proposed two-channel model", prompt)
                self.assertIn("Provisional component comparison; not adjudicated", prompt)
                if role == "research_lead":
                    self.assertIn("pending/parked claims and unadjudicated work", prompt)
                    self.assertIn("proposals, provisional results and Judge conclusions", prompt)
                else:
                    self.assertIn("pending/parked claims and provisional work", prompt)
                    self.assertIn("separate them from Judge conclusions", prompt)
                self.assertNotIn("information beyond already terminal-adjudicated tests", prompt)
                self.assertIn("Output contract:", prompt)
        for prompt in helper.prompts("challenger", "candidate_review"):
            self.assertIn("active/resolved claim without new uncertainty", prompt)
            self.assertIn("topical overlap alone is insufficient", prompt)
            self.assertIn("Do not demand the research answer at qualification", prompt)
        self.assertIsNone(history[0]["verdict"])

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
                "definitions/formula, actual test, verdict, scope",
                "older defining or adverse results",
                "titles or citations do not suffice",
                "equivalent terms, mathematical descriptions and pagination",
                "one keyword miss or first page does not establish novelty",
                "what old results already imply, the remaining scientific unknown",
                "renaming, reparameterizing or changing targets/scope alone does not establish novelty",
                "what outcomes change understanding",
                "Expose material unavailable history rather than claim novelty",
                "Avoid umbrella programs, filler, preparation-only work and fragments that add no scientific information",
            ):
                self.assertIn(rule, prompt)
            for preserved in (
                "A useful counterexample, bounded mechanism",
                "negative result or high-risk claim can advance the Goal without completing it",
                "unknown derivations and the decisive experiment remain Investigator work",
                "one coherent, independently adjudicable scientific claim",
                "Indispensable premises need a credible in-scope route",
            ):
                self.assertIn(preserved, prompt)
            self.assertIn("Output contract:", prompt)
        self.assertIn("不得读取、解析或直接修改它", identity_block("research_lead"))
        rendered = stage_definition_text("research_lead", "frontier_proposal")
        self.assertLess(len(rendered), 6500)


if __name__ == "__main__":
    unittest.main()
