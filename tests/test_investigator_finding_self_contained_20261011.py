"""Finding output v10: scientific qualifications live in statement, not duplicate fields.

No real Provider/model is called. Frozen Hypothesis.scope and historical v6
record readability must remain unchanged.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.contracts import role_contract
from theory_master.evidence_provenance import normalize_provenance
from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt, stage_context
from theory_master.stage_registry import stage_card_text
from theory_master.identity import identity_files


STAGES = ("investigate", "judge_rework", "integrate_literature")


class SelfContainedFindingTests(unittest.TestCase):
    def test_every_investigator_stage_v10_preserves_scientific_method_result_and_provenance(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                contract = role_contract("investigator", phase=stage)
                self.assertEqual(contract.version, 10)
                finding = contract.prompt_schema["findings"][0]
                self.assertEqual(set(finding), {"statement", "method", "result", "provenance"})
                self.assertIn("frozen_before_current_cycle", finding["provenance"])
                contract.validate({
                    "findings": [{
                        "statement": "For three exposed networks under the given test, a conditional interval spans zero; this does not establish a new independent prediction.",
                    }]
                }, {})
                self.assertIn("method and result are optional authored content", contract.prompt_note)
                self.assertIn("Use only the schema fields", contract.prompt_note)
                for field in ("scope", "limitations", "evidence_kind", "source_refs"):
                    with self.assertRaisesRegex(ValueError, "the complete scientific result belongs in statement"):
                        contract.validate({
                            "findings": [{"statement": "One conditional result", field: "not separate"}]
                        }, {})
                for is_continuation in (False, True):
                    context = {"frozen_objective": {"hypothesis": "H", "scope": "H scope remains",
                                                    "decisive_test": "Check bounded prediction"}}
                    kw = dict(role="investigator", stage=stage, contract_ref=contract.ref,
                              contract_schema=contract.prompt_projection(context), context=context)
                    prompt = (build_continuation_prompt(**kw) if is_continuation else
                              build_initial_prompt(prefix="test", write_rule="Only workspace writes", **kw))
                    self.assertIn("optional", prompt.lower())
                    self.assertNotIn('"scope":"optional;', prompt)
                    self.assertNotIn('"limitations":"optional;', prompt)
                    self.assertIn("H scope remains", prompt)
                    self.assertNotIn('"evidence_kind":', prompt)

    def test_retired_investigator_schema_versions_are_not_registered(self):
        from theory_master.contract_stages import contract_by_id
        for stage in STAGES:
            current = role_contract("investigator", phase=stage)
            self.assertIsNone(contract_by_id(current.contract_id, 6))
            self.assertIsNone(contract_by_id(current.contract_id, 7))
            self.assertEqual(set(current.prompt_schema["findings"][0]), {"statement", "method", "result", "provenance"})

    def test_hypothesis_scope_and_old_judge_findings_are_not_deleted(self):
        frozen = {"hypothesis": "H", "scope": "F must be from 8 to 10",
                  "decisive_test": "Test original signed energy"}
        projected = stage_context("judge", "terminal_assessment", {
            "hypothesis_lock": frozen,
            "investigation_result": {"findings": [{
                "statement": "legacy result", "evidence_kind": "numerical",
                "scope": "old network", "limitations": "not tested on a new network",
                "evidence_id": "INTERNAL",
            }]},
        })
        self.assertEqual(projected["hypothesis_lock"]["scope"], frozen["scope"])
        old = projected["investigation_result"]["findings"][0]
        self.assertEqual(old["scope"], "old network")
        self.assertEqual(old["limitations"], "not tested on a new network")
        self.assertNotIn("evidence_id", old)

    def test_provenance_security_is_unchanged_and_not_newly_required(self):
        with tempfile.TemporaryDirectory() as temp:
            w = Path(temp)
            self.assertIsNone(normalize_provenance(
                {"statement": "Result"},
                workspace=w, resolve_local_ref=lambda x: [],
            ))
            with self.assertRaisesRegex(ValueError, "cannot claim holdout or gate identity"):
                normalize_provenance({
                    "provenance": {"source": "agent_generated", "split": "gate"}},
                    workspace=w, resolve_local_ref=lambda x: [],
                )
            self.assertEqual(
                normalize_provenance({
                    "provenance": {"source": "agent_generated", "split": "none"}},
                    workspace=w, resolve_local_ref=lambda x: [],
                ), {"source": "agent_generated", "split": "none"},
            )

    def test_role_identity_and_stage_are_aligned(self):
        for filename in identity_files("investigator"):
            if filename.name == "investigator.md":
                text = filename.read_text(encoding="utf-8")
                self.assertIn("科学结论必须准确反映实际方法", text)
                self.assertIn("statement", text)
        for stage in STAGES:
            self.assertIn("findings", stage_card_text("investigator", stage))
        self.assertIn("limits in each scientific statement",
                      stage_card_text("investigator", "investigate"))
        self.assertNotIn("findings[].limitations", stage_card_text("investigator", "investigate"))

    def test_sdk_design_documents_the_exact_reason_and_gates(self):
        doc = (Path(__file__).resolve().parents[1] / "design" / "SDK设计文档.md").read_text(encoding="utf-8")
        for phrase in ("Gate G01", "Gate G02", "成立条件", "冗余呈现",
                       "Hypothesis.scope", "不单列 scope/limitations"):
            self.assertIn(phrase, doc)


if __name__ == "__main__":
    unittest.main()
