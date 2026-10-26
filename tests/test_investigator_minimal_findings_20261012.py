"""v8 Investigator Finding: science only, with optional protected identity.

Tests are deterministic; no real LLM, SMTP, or scientific Cycle execution.
"""
import json
import hashlib
import tempfile
from pathlib import Path
import unittest

from theory_master.contracts import role_contract
from theory_master.contract_closure import close_agent_output
from theory_master.evidence import EvidenceRegistry
from theory_master.prompt_stages import build_initial_prompt
from theory_master.native_goal import NativeGoalResult, read_goal_findings
from theory_master.workflow_investigation import InvestigationMixin


class MinimalFindingV10Test(unittest.TestCase):
    def test_v10_preserves_optional_scientific_method_result_and_provenance(self):
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            with self.subTest(stage=stage):
                contract = role_contract("investigator", phase=stage)
                self.assertEqual(contract.version, 10)
                self.assertEqual(set(contract.prompt_schema["findings"][0]), {"statement", "method", "result", "provenance"})
                output = {"findings": [
                    {"statement": "Under frozen input conditions, the exact bound is nonnegative."},
                    {"statement": "On three exposed networks the numerical intervals span zero; no independent prediction is established."},
                ]}
                output["findings"][0]["method"] = "Analytic bound from conservation"
                output["findings"][0]["result"] = {"lower_bound": 0, "exact": True}
                contract.validate(output, {})
                closed = close_agent_output("investigator", stage, output, {}, {})
                self.assertEqual(len(closed["evidence"]), 2)
                self.assertEqual([r["summary"] for r in closed["evidence"]],
                                 [r["statement"] for r in output["findings"]])
                self.assertEqual(set(closed["evidence"][0]), {"summary", "method", "result"})
                self.assertEqual(closed["evidence"][0]["method"],output["findings"][0]["method"])
                self.assertEqual(closed["evidence"][0]["result"],output["findings"][0]["result"])
                self.assertEqual(set(closed["evidence"][1]), {"summary"})
                for deleted in ("scope", "limitations", "evidence_kind", "source_refs"):
                    with self.assertRaisesRegex(ValueError, "obsolete output fields"):
                        contract.validate({"findings": [{"statement": "Claim with conditions",
                                                       deleted: ["X"] if deleted == "source_refs" else "X"}]}, {})
                objective = {"frozen_objective": {"hypothesis": "H", "scope": "fixed scope",
                                                 "decisive_test": "check sign"}}
                initial = build_initial_prompt(
                    role="investigator", stage=stage,
                    contract_ref=contract.ref,
                    contract_schema=contract.prompt_projection(objective),
                    context=objective, prefix="cycle", write_rule="workspace",
                )
                self.assertIn("Finding count", initial)
                self.assertIn("statement", initial)

    def test_minimal_scientific_handoff_not_an_extra_model_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"findings.json"
            path.write_text(json.dumps({"findings":[
                {"claim":"Frozen first-shell bound follows from shared source."},
                {"claim":"On three exposed networks all intervals still span zero."},
            ]}))
            raw=read_goal_findings(NativeGoalResult("complete","tid",1,1),path)
            self.assertEqual(json.loads(raw),{"findings":[
                {"statement":"Frozen first-shell bound follows from shared source."},
                {"statement":"On three exposed networks all intervals still span zero."},
            ]})

    def test_registration_and_judge_use_sdk_producer_without_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "61"
            ws = cycle / "workspace"
            ws.mkdir(parents=True)
            reg = EvidenceRegistry(cycle, ws)
            invocation = {"invocation_id": "inv-1", "role": "investigator", "stage": "investigate"}
            findings = {"evidence": [
                {"summary": "Joint source constraint gives exact interval.",
                 "method": "Conservation plus independent linear program",
                 "result": {"joint_interval": [-1.4, 36.8], "source": "pre-existing discovery"}},
                {"summary": "Three already-exposed networks remain sign indeterminate."},
            ]}
            refs = reg.register_role_output("investigator", findings, epoch="post_lock_test",
                                            producer_invocation=invocation)
            self.assertEqual(len(refs), 2)
            self.assertEqual(refs, reg.register_role_output(
                "investigator", findings, epoch="post_lock_test", producer_invocation=invocation))
            records = reg.context()["evidence_manifest"]["payload"]["records"]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["method"],findings["evidence"][0]["method"])
            self.assertEqual(records[0]["result"],findings["evidence"][0]["result"])
            self.assertNotIn("method",records[1])
            self.assertTrue(all(r["adjudicable"] for r in records))
            self.assertTrue(all(r["producer_invocation"]["role"] == "investigator" for r in records))
            self.assertTrue(all("evidence_kind" not in r and "source_refs" not in r for r in records))
            self.assertTrue(all(r["freshness"] == "origin_unverified" for r in records))
            self.assertTrue(all("origin_cycle" not in r for r in records))
            self.assertTrue(all(r["analysis_cycle"] == 61 for r in records))
            selected = InvestigationMixin._adjudicable_judge_records(reg.context())
            self.assertEqual([r["evidence_id"] for r in selected], refs)

    def test_judge_receives_original_method_and_result(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            cycle=Path(tmp)/"61"
            cycle.mkdir()
            record={"evidence_id":"E-61-one","producer_invocation":{"role":"investigator"},
                    "lifecycle_status":"active","adjudicable":True,
                    "summary":"A precise scientific claim",
                    "method":"Exact argument and finite-size cross-check",
                    "result":{"bound":1.0, "samples":[2,4,8], "holds":True}}
            packet={"evidence_manifest":{"payload":{"records":[record]}}}
            stub=SimpleNamespace(
                cycle_dir=cycle,
                _adjudicable_judge_records=InvestigationMixin._adjudicable_judge_records,
            )
            state={"investigation_attempt":1,"investigation_round_count":1}
            audit={}
            delivery=InvestigationMixin._materialize_investigation_result(stub,state,audit,packet)
            self.assertEqual(len(delivery["findings"]),1)
            self.assertEqual(delivery["findings"][0]["method"],record["method"])
            self.assertEqual(delivery["findings"][0]["result"],record["result"])
            self.assertEqual(delivery["findings"][0]["statement"],record["summary"])
            self.assertEqual(json.loads((cycle/"investigation_attempts"/"attempt-0001"/"investigation_result.json").read_text()),delivery)

    def test_old_kind_based_evidence_id_is_reused_without_duplicate_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "19"
            ws = cycle / "workspace"
            ws.mkdir(parents=True)
            reg = EvidenceRegistry(cycle, ws)
            invocation = {"invocation_id": "inv-old-v7", "role": "investigator"}
            old_output = {"evidence": [
                {"summary": "old numerical science", "evidence_kind": "numerical"},
            ]}
            # Exact pre-v8 digest shape. This represents an existing immutable
            # Registry row after a completed old model-authored type turn.
            old_canonical = {
                "cycle_id": "19", "hypothesis_ref": "",
                "evidence_kind": "numerical",
                "source": "evidence", "source_index": 0,
                "body": "old numerical science", "producer_invocation": invocation,
                "lineage": {"origin_cycle": 19, "analysis_cycle": 19, "freshness": "current_cycle"},
            }
            digest = hashlib.sha256(json.dumps(
                old_canonical, ensure_ascii=False, sort_keys=True, default=str
            ).encode()).hexdigest()[:16]
            original_id = f"E-19-{digest}"
            reg.evidence_dir.mkdir(parents=True)
            old_row = {
                "schema": "theory-master/evidence-registry/v4",
                "evidence_id": original_id,
                "cycle_id": "19",
                "evidence_kind": "numerical",
                "producer_invocation": invocation,
                "summary": "old numerical science",
                "artifact_pointers": [],
                "integrity": {"artifact_paths_exist": True,
                              "artifact_hashes_recorded": True,
                              "artifact_roles_valid": True},
            }
            raw_line = json.dumps(old_row, ensure_ascii=False) + "\n"
            reg.records_path.write_text(raw_line)
            refs = reg.register_role_output(
                "investigator", old_output, epoch="post_lock_test", producer_invocation=invocation)
            self.assertEqual(refs, [original_id])
            self.assertEqual(reg.records_path.read_text(), raw_line)
            projected = reg.context()["evidence_manifest"]["payload"]["records"]
            self.assertEqual(len(projected), 1)
            self.assertEqual(projected[0]["evidence_id"], original_id)

    def test_challenger_cannot_become_scientific_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "11"
            ws = cycle / "workspace"
            ws.mkdir(parents=True)
            reg = EvidenceRegistry(cycle, ws)
            reg.register_role_output(
                "challenger", {"evidence": [{"summary": "candidate qualified"}]},
                epoch="qualification",
                producer_invocation={"role": "challenger", "invocation_id": "challenge-1"})
            records = reg.context()["evidence_manifest"]["payload"]["records"]
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]["adjudicable"])
            self.assertEqual(InvestigationMixin._adjudicable_judge_records(reg.context()), [])

    def test_literature_still_needs_verified_source_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "12"
            ws = cycle / "workspace"
            ws.mkdir(parents=True)
            reg = EvidenceRegistry(cycle, ws)
            reg.register_role_output(
                "literature", {"evidence_records": [
                    {"scientific_insight": "One external paper asserts X",
                     "paper_title": "Unverified paper",
                     "source_access": "full_text"}]},
                epoch="literature", producer_invocation={"role": "literature", "invocation_id": "lit-1"})
            records = reg.context()["evidence_manifest"]["payload"]["records"]
            self.assertFalse(records[0]["adjudicable"])
            self.assertNotIn("evidence_kind", records[0])
            self.assertEqual(InvestigationMixin._adjudicable_judge_records(reg.context()), [])

    def test_provenance_gate_restriction_survives_type_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            cycle = Path(tmp) / "13"
            ws = cycle / "workspace"
            ws.mkdir(parents=True)
            reg = EvidenceRegistry(cycle, ws)
            with self.assertRaisesRegex(ValueError, "agent-generated validation cannot claim holdout or gate identity"):
                reg.register_role_output(
                    "investigator", {"evidence": [{"summary": "Theorem supported by discovery data",
                                                   "provenance": {"source": "agent_generated", "split": "gate"}}]},
                    epoch="post_lock_test", producer_invocation={"role": "investigator", "invocation_id": "inv-bad"})
            self.assertEqual(reg.context()["evidence_manifest"]["payload"]["records"], [])

    def test_current_investigator_contracts_are_phase_specific_and_minimal(self):
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            contract = role_contract("investigator", phase=stage)
            self.assertTrue(contract.contract_id.startswith("theory-master/role/investigator/"))
            self.assertIn("result", contract.prompt_schema["findings"][0])
            self.assertNotIn("evidence_kind", contract.prompt_schema["findings"][0])
            self.assertNotIn("source_refs", contract.prompt_schema["findings"][0])

    def test_documented_authority_and_test_gates(self):
        doc = (Path(__file__).resolve().parents[1] / "design" / "SDK设计文档.md").read_text()
        for name in ("Hypothesis Lock", "decisive_test", "C-1",
                     "自包含 Findings", "机器身份和产物溯源", "Judge"):
            self.assertIn(name, doc)


if __name__ == "__main__":
    unittest.main()
