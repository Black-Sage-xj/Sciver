"""Prompt-only Investigator completion contract: do not resurrect SDK science gates."""
from pathlib import Path
import unittest

from theory_master.contracts import role_contract
from theory_master.identity import identity_files
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt
from theory_master.stage_registry import stage_card_text


class CompleteHandoffPromptTest(unittest.TestCase):
    @staticmethod
    def _schema(stage):
        contract = role_contract("investigator", phase=stage)
        assert set(contract.prompt_schema) == {"findings"}
        return contract

    def test_investigator_identity_mirrors_and_core_semantics(self):
        src = Path(__file__).resolve().parents[1] / "identities" / "investigator.md"
        bundled = Path(__file__).resolve().parents[1] / "theory_master" / "resources" / "identities" / "investigator.md"
        self.assertEqual(src.read_bytes(), bundled.read_bytes())
        self.assertTrue(any(path.name == "investigator.md" for path in identity_files("investigator")))
        text = src.read_text(encoding="utf-8")
        for phrase in ("唯一 Native Goal", "Judge", "SDK Setup", "claim", "limitations"):
            self.assertIn(phrase, text)
        self.assertNotIn("剩余工作清单", text)

    def test_investigate_stage_prioritizes_work_before_submit(self):
        stage = stage_card_text("investigator", "investigate")
        for phrase in (
            "Privately decompose decisive_test", "every necessary scientific prerequisite",
            "perform the required independent test", "combined evidence",
            "WHOLE Hypothesis", "KEEP RESEARCHING", "scientific statement",
            "support, falsification", "frozen comparison",
        ):
            self.assertIn(phrase, stage)
        self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", stage)
        self.assertNotIn("a criterion-linked finding is not a reason", stage)
        self.assertLess(len(stage), 3100)

    def test_rework_requires_actual_resolution_not_partial_findings(self):
        stage = stage_card_text("investigator", "judge_rework")
        for phrase in (
            "judge_objections[].requested_resolution", "binding scientific target",
            "missing prerequisites", "execute the necessary", "exact Judge request",
            "BEFORE submitting findings[]", "materially RESOLVES", "If the actionable Judge objection remains",
            "KEEP WORKING", "rigorous falsification/non-identifiability/impossibility",
        ):
            self.assertIn(phrase, stage)
        self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", stage)
        self.assertLess(len(stage), 3000)

    def test_literature_stage_is_not_early_delivery(self):
        stage = stage_card_text("investigator", "integrate_literature")
        self.assertIn("NOT a submission milestone", stage)
        self.assertIn("complete findings[]", stage)
        self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", stage)

    def test_real_assembled_first_and_rework_prompts_retain_exact_contract(self):
        objective = {"hypothesis": "Test P", "decisive_test": "Compare independent A and B", "scope": "Frozen"}
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            contract = self._schema(stage)
            context = {"frozen_objective": objective}
            if stage == "judge_rework":
                context["judge_objections"] = [{"requested_resolution": "Independent B comparison required"}]
            kwargs = {"role": "investigator", "stage": stage,
                      "contract_ref": contract.ref, "contract_schema": contract.prompt_projection(context),
                      "context": context}
            first = build_initial_prompt(prefix="Cycle C48", write_rule="Only assigned workspace",
                                         **kwargs)
            follow = build_continuation_prompt(**kwargs)
            for prompt in (first, follow):
                self.assertIn("Compare independent A and B", prompt)
                self.assertIn("findings", prompt)
                self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", prompt)
                if stage == "judge_rework":
                    self.assertIn("Independent B comparison required", prompt)
                    self.assertIn("KEEP WORKING", prompt)
                elif stage == "investigate":
                    self.assertIn("KEEP RESEARCHING", prompt)
            self.assertEqual(set(contract.prompt_schema), {"findings"})


if __name__ == "__main__":
    unittest.main()
