from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.abc_pipeline import DeterministicSemanticBackend, annotate_specialist
from theory_master.contract_stages import (
    MINIMAL_INVESTIGATOR_STAGE_SCHEMA,
    SCIENTIFIC_SYNTHESIS_SCHEMA,
    role_contract,
)
from theory_master.evidence import EvidenceRegistry
from theory_master.prompt_stages import stage_context
from theory_master.stage_registry import stage_card_text


class Task88RegressionTest(unittest.TestCase):
    def make_registry(self, root: Path) -> tuple[EvidenceRegistry, Path]:
        cycle = root / "cycle-0001"
        workspace = cycle / "workspace"
        workspace.mkdir(parents=True)
        return EvidenceRegistry(cycle, workspace), workspace

    def register(self, registry: EvidenceRegistry, item: dict) -> str:
        return registry.register_role_output(
            "investigator",
            {"evidence": [item]},
            epoch="post_lock_test",
            producer_invocation={
                "invocation_id": "inv-task88-regression",
                "role": "investigator",
                "stage": "investigate",
            },
        )[0]

    def test_ordinary_numerical_evidence_does_not_require_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, _workspace = self.make_registry(Path(temp))
            self.register(registry, {
                "summary": "Synthetic validation reproduces the expected sign.",
                "evidence_kind": "numerical",
            })
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertNotIn("provenance", record)

    def test_literature_can_describe_held_out_data_without_investigator_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, _workspace = self.make_registry(Path(temp))
            refs = registry.register_role_output(
                "literature",
                {"evidence": [{"summary": "The paper evaluates a held-out cohort."}]},
                epoch="literature_test",
                producer_invocation={
                    "invocation_id": "inv-literature-regression",
                    "role": "literature",
                    "stage": "targeted_source",
                },
            )
            self.assertEqual(len(refs), 1)
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["producer_invocation"]["role"], "literature")
            self.assertNotIn("evidence_kind", record)
            self.assertNotIn("provenance", record)

    def test_holdout_wording_does_not_create_protected_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, _workspace = self.make_registry(Path(temp))
            self.register(registry, {
                "summary": "The held-out parameter set has lower error.",
                "evidence_kind": "numerical",
            })
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertNotIn("provenance", record)

    def test_investigator_contract_exposes_optional_evidence_provenance(self):
        finding = MINIMAL_INVESTIGATOR_STAGE_SCHEMA["findings"][0]
        self.assertIn("optional", finding["provenance"]["source"])
        self.assertIn(
            "benchmark_provided|pre_specified_synthetic|agent_generated|external_source",
            finding["provenance"]["source"],
        )
        self.assertIn("optional", finding["provenance"]["split"])
        self.assertIn(
            "none|fit|validation|holdout|gate",
            finding["provenance"]["split"],
        )
        self.assertIn("required for", finding["provenance"]["declaration_ref"])

    def test_agent_generated_validation_cannot_be_upgraded_to_holdout(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, _workspace = self.make_registry(Path(temp))
            with self.assertRaisesRegex(ValueError, "agent-generated validation cannot claim holdout"):
                self.register(registry, {
                    "summary": "The held-out parameter set has lower error.",
                    "evidence_kind": "numerical",
                    "provenance": {"source": "agent_generated", "split": "holdout"},
                })

    def test_cluster_held_out_evaluation_is_not_a_benchmark_holdout_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            registry, _workspace = self.make_registry(Path(temp))
            self.register(registry, {
                "summary": (
                    "Balanced accuracy on compounds held out by cluster using "
                    "cluster-held-out folds was above chance."
                ),
                "evidence_kind": "numerical",
                "provenance": {"source": "agent_generated", "split": "none"},
            })
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["provenance"]["split"], "none")

    def test_benchmark_holdout_claim_is_unverified_without_precycle_attestation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, _workspace = self.make_registry(root)
            benchmark = root / "benchmark"
            benchmark.mkdir()
            declaration = benchmark / "instruction.md"
            declaration.write_text("prediction_split = holdout\n", encoding="utf-8")
            self.register(registry, {
                "summary": "The benchmark-provided holdout set has lower error.",
                "evidence_kind": "numerical",
                "provenance": {
                    "source": "benchmark_provided",
                    "split": "holdout",
                    "declaration_ref": str(declaration),
                },
            })
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["provenance"]["source"], "benchmark_provided")
            self.assertEqual(record["provenance"]["split"], "holdout")
            self.assertNotIn("frozen_before_current_cycle", record["provenance"])
            self.assertEqual(record["provenance"]["freeze_status"], "unverified")
            self.assertFalse(record["provenance"]["protected_identity_verified"])
            self.assertEqual(len(record["provenance"]["declaration_sha256"]), 64)
            self.assertEqual(record["provenance"]["declaration_ref"], str(declaration.resolve()))

    def test_current_cycle_file_cannot_backdate_prespecified_holdout_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry, workspace = self.make_registry(root)
            declaration = workspace / "posthoc_design.md"
            declaration.write_text("declare after seeing results\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be outside the current Cycle workspace"):
                self.register(registry, {
                    "summary": "The held-out synthetic set has lower error.",
                    "evidence_kind": "numerical",
                    "provenance": {
                        "source": "pre_specified_synthetic",
                        "split": "holdout",
                        "declaration_ref": str(declaration),
                    },
                })

    def test_specialist_closure_preserves_optional_provenance(self):
        artifact = {
            "findings": [{
                "statement": "Agent-generated validation gives the expected sign.",
                "scope": "synthetic validation only",
                "limitations": "not a frozen benchmark split",
                "evidence_kind": "numerical",
                "provenance": {"source": "agent_generated", "split": "validation"},
            }],
            "remaining_scientific_question": "",
        }
        closed, _audit = annotate_specialist(
            artifact,
            {"hypothesis_lock": {"cycle_progress_criteria": {"success": [], "failure": []}}},
            DeterministicSemanticBackend(),
        )
        self.assertEqual(
            closed["evidence"][0]["provenance"],
            {"source": "agent_generated", "split": "validation"},
        )

    def test_numerical_contract_guidance_requires_independent_correctness_without_new_artifact_role(self):
        investigator = role_contract("investigator", phase="investigate").prompt_projection()
        judge = role_contract("judge", phase="terminal_assessment").prompt_projection()
        judge_card = stage_card_text("judge", "terminal_assessment")
        self.assertNotIn("independent correctness check", investigator)
        self.assertNotIn("canary", investigator)
        self.assertIn("Include provenance only for strong evidence-identity claims", investigator)
        self.assertIn("compact scientific result", judge_card)
        self.assertIn("Do not reopen or request raw files", judge_card)
        self.assertIn("do not perform a second investigation", judge_card)
        self.assertIn("Do not emit criterion IDs", judge_card)
        self.assertIn("needs_external_scholarship", judge)
        self.assertNotIn("do not reopen", judge.lower())
        skill = (
            Path(__file__).resolve().parents[1]
            / "theory_master" / "resources" / "skills" / "numerical-investigation" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("独立正确性检查", skill)
        self.assertIn("canary", skill)
        self.assertIn("不能把 reproducibility 或 cross-file consistency 写成 correctness", skill)
        self.assertIn("不要求额外生成固定名称的 validation.json", skill)
        self.assertIn("不新增 validation artifact role", skill)
        self.assertIn("batch probe", skill)
        self.assertIn("紧凑 JSON", skill)

    def test_numerical_skill_gates_downstream_work_on_decisive_provenance(self):
        skill = (
            Path(__file__).resolve().parents[1]
            / "theory_master" / "resources" / "skills" / "numerical-investigation" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("决定性输入/provenance gate", skill)
        self.assertIn("source-recoverability audit", skill)
        self.assertIn("external_artifact", skill)
        self.assertIn("不得重新全面审计旧资产", skill)
        self.assertLess(skill.index("决定性输入/provenance gate"), skill.index("围绕冻结的 decisive test"))

    def test_active_cycle_synthesis_rejects_legacy_synthesis_and_frontier_fields(self):
        contract = role_contract("research_lead", phase="cycle_synthesis")
        with self.assertRaisesRegex(ValueError, "scientific_synthesis accepts exactly conclusions and open_questions"):
            contract.validate({
                "scientific_synthesis": {
                    "learned": [],
                    "next_frontier_recommendation": "obsolete advisory direction",
                },
                "scientific_model_update": {"action": "keep"},
            }, {})

    def test_active_synthesis_contract_and_prompt_projection_strip_legacy_frontier_advice(self):
        self.assertNotIn("next_frontier_recommendation", SCIENTIFIC_SYNTHESIS_SCHEMA)
        legacy = {
            "learned": ["result"],
            "new_open_questions": ["scope remains open"],
            "next_frontier_recommendation": "obsolete advisory direction",
        }
        projected = stage_context("reporter", "report", {
            "scientific_synthesis": legacy,
            "authorized_next_frontier_candidate_id": None,
        })
        self.assertEqual(projected["scientific_synthesis"]["learned"], ["result"])
        self.assertNotIn(
            "next_frontier_recommendation",
            projected["scientific_synthesis"],
        )

        from theory_master.contracts import role_contract
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")


if __name__ == "__main__":
    unittest.main()
