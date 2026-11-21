"""Completion authority belongs to the Native Goal and delivery to the Contract."""
from pathlib import Path
import unittest

from theory_master.contracts import role_contract
from theory_master.identity import identity_files, identity_block
from theory_master.native_goal import investigator_goal
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt
from theory_master.stage_registry import stage_card_text

OBJECTIVE = {"hypothesis": "Test P", "decisive_test": "Compare independent A and B", "scope": "Frozen"}


class CompleteHandoffPromptTest(unittest.TestCase):
    @staticmethod
    def _context(stage):
        context = {"frozen_objective": OBJECTIVE}
        if stage == "judge_rework":
            context["judge_objections"] = [{"requested_resolution": "Independent B comparison required"}]
        return context

    def test_investigator_identity_mirrors_and_core_semantics(self):
        root = Path(__file__).resolve().parents[1]
        src = root / "identities" / "investigator.md"
        bundled = root / "theory_master/resources/identities/investigator.md"
        self.assertEqual(src.read_bytes(), bundled.read_bytes())
        self.assertIn(bundled, identity_files("investigator"))
        text = identity_block("investigator")
        for phrase in ("Native Goal 或 Stage", "Judge", "SDK Setup 和 Contract", "科学限制"):
            self.assertIn(phrase, text)
        self.assertNotIn("剩余工作清单", text)

    def test_investigate_goal_prioritizes_full_test_before_submit(self):
        goal = investigator_goal("investigate", self._context("investigate"), layout="v2")
        for phrase in ("execute the complete decisive test", "partial work is not completion",
                       "perform independent checks", "Never invent results", "Unperformed tests are not inconclusive"):
            self.assertIn(phrase, goal)
        card = stage_card_text("investigator", "investigate")
        self.assertIn("whole frozen test", card)
        self.assertIn("this observer/legacy Stage does not replace", card)
        self.assertIn("limits in each scientific statement", card)

    def test_public_submission_follows_declared_format(self):
        from theory_master.public_submission import SCHEMA, PYTHON_SCHEMA

        for declaration in (
            {"schema": SCHEMA, "json_schema": {"type": "object"}},
            {"schema": PYTHON_SCHEMA, "filename": "model.py",
             "callable": {"name": "predict", "parameters": ["x"]}, "allowed_imports": []},
        ):
            context = {**self._context("investigate"), "public_submission": declaration}
            for layout in ("v1", "v2"):
                with self.subTest(schema=declaration["schema"], layout=layout):
                    goal = investigator_goal("investigate", context, layout=layout)
                    self.assertIn("format declared by Setup Context.public_submission", goal)
                    self.assertNotIn("public JSON submission", goal)

    def test_rework_requires_actual_resolution_not_partial_findings(self):
        goal = investigator_goal("judge_rework", self._context("judge_rework"), layout="v2")
        self.assertIn("Independent B comparison required", goal)
        self.assertIn("Neither a weaker surrogate test nor a partial Finding resolves the Judge request", goal)
        card = stage_card_text("investigator", "judge_rework")
        for phrase in ("actual requested_resolution", "exact Judge request", "materially resolve",
                       "If actionable required work remains, continue"):
            self.assertIn(phrase, card)

    def test_literature_stage_is_not_early_delivery(self):
        card = stage_card_text("investigator", "integrate_literature")
        self.assertIn("Literature integration alone is not research completion", card)
        self.assertIn("complete the remaining frozen research", card)
        self.assertIn("Native Goal determines completion", card)

    def test_real_assembled_first_and_rework_prompts_retain_exact_contract(self):
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            contract = role_contract("investigator", phase=stage)
            context = self._context(stage)
            kwargs = {"role": "investigator", "stage": stage, "contract_ref": contract.ref,
                      "contract_schema": contract.prompt_projection(context), "context": context}
            prompts = (build_initial_prompt(prefix="Cycle C48", write_rule="Only assigned workspace", **kwargs),
                       build_continuation_prompt(**kwargs))
            for prompt in prompts:
                self.assertIn(OBJECTIVE["decisive_test"], prompt)
                self.assertIn("findings", prompt)
                self.assertIn("Native Goal", prompt)
                self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", prompt)
                if stage == "judge_rework":
                    self.assertIn("Independent B comparison required", prompt)
            self.assertEqual(set(contract.prompt_schema), {"findings"})
            # Completion and immutable science are checked at the actual runtime
            # owner, rather than requiring retired wording in every Stage.
            goal = investigator_goal(stage, context, layout="v2")
            self.assertIn(OBJECTIVE["decisive_test"], goal)
            self.assertIn("Native Goal completion does not replace independent Judge adjudication", goal)


if __name__ == "__main__":
    unittest.main()
