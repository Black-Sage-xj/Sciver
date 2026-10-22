from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from theory_master.backend_fakes import FakeBackend
from theory_master.invocations import InvocationLedger, stable_hash
from theory_master.output_validation import validate_scientific_model_update
from theory_master.scientific_model import empty_model, validate_model
from theory_master.specification import TaskSpec, latest_cycle_dir
from theory_master.workflow import Workflow
from theory_master.workflow_investigation import InvestigationMixin
import theory_master.workflow_science as workflow_science


class ScientificModelSummaryContractTest(unittest.TestCase):
    def _current_model(self) -> dict[str, object]:
        model = empty_model("goal")
        model["summary"] = "Historical scientific summary, including the source record E-old."
        validate_model(model)
        return model

    @staticmethod
    def _update(summary: str, **extra: object) -> dict[str, object]:
        model: dict[str, object] = {"summary": summary, **extra}
        return {"action": "replace", "model": model}

    def test_accepts_current_summary_only_model_replacement(self):
        result = validate_scientific_model_update(
            self._current_model(), self._update("The frozen-range result is now established."),
            cycle_id="1", evidence_manifest={"payload": {"cycle_id": "1", "records": []}},
        )
        self.assertTrue(result.valid, result.as_dict())

    def test_rejects_legacy_structured_knowledge_fields(self):
        result = validate_scientific_model_update(
            self._current_model(), self._update(
                "A bounded summary.", established_knowledge=[{"statement": "old schema"}],
                current_scientific_picture="old schema",
            ), cycle_id="1",
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("exactly one field: summary" in item.message
                            for item in result.errors))

    def test_rejects_non_string_or_oversized_summary(self):
        for summary in ([], "x" * 2401):
            with self.subTest(summary_type=type(summary).__name__, length=len(summary) if isinstance(summary, str) else None):
                result = validate_scientific_model_update(
                    self._current_model(), {"action": "replace", "model": {"summary": summary}},
                    cycle_id="1",
                )
                self.assertFalse(result.valid)

    def test_keep_must_not_smuggle_a_second_authored_document(self):
        result = validate_scientific_model_update(
            self._current_model(), {"action": "keep", "model": {"summary": "ignored"}},
            cycle_id="1",
        )
        self.assertFalse(result.valid)


class InvocationCommitReuseTest(unittest.TestCase):
    @staticmethod
    def _make_spec(root: Path, *, max_cycles: int | None = None) -> TaskSpec:
        from theory_master.task import bootstrap
        plan = root / "research_plan.md"
        plan.write_text(
            '# Goal\n\ng\n\n# Research Contract\n\nc\n\n'
            '# Final Gate\n\nA reproducible result exists.\n\n# Required Context\n\n- r\n\n'
            '# Scientific Model\n\n尚未形成稳定的科学总结。\n',
            encoding="utf-8",
        )
        binding = bootstrap(plan)
        if max_cycles is not None:
            config = json.loads(binding.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = max_cycles
            binding.config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return TaskSpec.from_config(binding.config_path)

    def test_committed_cycle_synthesis_is_reused_after_caller_crash(self):
        class CountingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.cycle_end_calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                context = (
                    json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                    if "Context:\n" in prompt else {}
                )
                if role == "research_lead" and "Current task: cycle_synthesis" in prompt:
                    self.cycle_end_calls += 1
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec = self._make_spec(Path(temp), max_cycles=1)
            first_backend = CountingBackend()
            real_write_json = workflow_science.write_json

            def crash_after_call(path, value):
                if Path(path).name == "cycle_synthesis_out.json":
                    raise RuntimeError("crash after committed cycle synthesis invocation")
                return real_write_json(path, value)

            with patch.object(workflow_science, "write_json", side_effect=crash_after_call):
                with self.assertRaisesRegex(RuntimeError, "crash after committed"):
                    Workflow(spec, first_backend).run()

            self.assertEqual(first_backend.cycle_end_calls, 1)
            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            assert cycle is not None
            self.assertFalse((cycle / "cycle_synthesis_out.json").exists())
            ledger = InvocationLedger(cycle / "invocations.jsonl")
            committed = [
                item for item in ledger.snapshots()
                if item.get("stage") == "cycle_synthesis" and item.get("state") == "committed"
            ]
            self.assertEqual(len(committed), 1)
            self.assertTrue(committed[0].get("closure_hash"))

            resume_backend = CountingBackend()
            resumed = Workflow(spec, resume_backend).resume(0)
            self.assertEqual(resumed["status"], "over")
            self.assertEqual(resume_backend.cycle_end_calls, 0)
            self.assertGreaterEqual(int(resumed.get("invocation_reuse_count") or 0), 1)
            self.assertTrue((cycle / "cycle_synthesis_out.json").is_file())

    def test_artifact_complete_completed_invocation_reconciles_without_model_replay(self):
        class CountingBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.cycle_end_calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                context = (
                    json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                    if "Context:\n" in prompt else {}
                )
                if role == "research_lead" and "Current task: cycle_synthesis" in prompt:
                    self.cycle_end_calls += 1
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec = self._make_spec(Path(temp), max_cycles=1)
            first_backend = CountingBackend()
            real_checkpoint = Workflow._checkpoint
            crashed = {"done": False}

            def crash_before_commit(workflow, state, name):
                if first_backend.cycle_end_calls and name == "research_lead" and not crashed["done"]:
                    crashed["done"] = True
                    raise RuntimeError("crash after closure before committed marker")
                return real_checkpoint(workflow, state, name)

            with patch.object(Workflow, "_checkpoint", new=crash_before_commit):
                with self.assertRaisesRegex(RuntimeError, "before committed marker"):
                    Workflow(spec, first_backend).run()

            self.assertEqual(first_backend.cycle_end_calls, 1)
            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            assert cycle is not None
            ledger = InvocationLedger(cycle / "invocations.jsonl")
            synthesis = [
                item for item in ledger.snapshots()
                if item.get("stage") == "cycle_synthesis"
            ]
            self.assertEqual(len(synthesis), 1)
            self.assertEqual(synthesis[0]["state"], "completed")
            self.assertTrue(synthesis[0].get("closure_hash"))

            resume_backend = CountingBackend()
            resumed = Workflow(spec, resume_backend).resume(0)
            self.assertEqual(resumed["status"], "over")
            self.assertEqual(resume_backend.cycle_end_calls, 0)
            reconciled = ledger.snapshot(str(synthesis[0]["invocation_id"]))
            self.assertEqual(reconciled["state"], "committed")
            self.assertEqual(reconciled.get("reconciliation"), "validated_closure_reuse")

    def test_historical_committed_invalid_artifact_closure_is_quarantined_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = self._make_spec(root)
            workflow = Workflow(spec, FakeBackend(reworks=0))
            cycle = spec.research_root / "cycle-0001"
            cycle.mkdir(parents=True)
            workflow.cycle_dir = cycle
            ledger = InvocationLedger(cycle / "invocations.jsonl")
            contract_ref = {"contract_id": "investigator-test", "version": 1}
            logical_key = "invalid-artifact-slot"
            invocation_id = "inv-invalid-artifact"
            closure_path = cycle / "semantic_closure" / f"{invocation_id}.json"
            closure_path.parent.mkdir(parents=True)
            closure = {
                "role": "investigator",
                "stage": "investigate",
                "agent_output": {"findings": []},
                "evidence_registration_audit": [{
                    "status": "all_artifacts_rejected", "evidence_id": "E-invalid",
                }],
                "closed_output": {"evidence": []},
            }
            closure_path.write_text(json.dumps(closure), encoding="utf-8")
            closure_hash = stable_hash(closure)
            ledger.prepare(
                invocation_id=invocation_id, kind="agent", input_payload={"x": 1},
                contract_ref=contract_ref, logical_key=logical_key,
                role="investigator", stage="investigate",
            )
            ledger.transition(
                invocation_id, "committed", role="investigator", stage="investigate",
                commit_state="committed", closure_artifact=str(closure_path),
                closure_hash=closure_hash, logical_key=logical_key,
            )
            state = {"threads": {}, "role_outputs": {}, "agent_outputs": {}}

            reused = workflow._reuse_committed_invocation(
                state, invocation_ledger=ledger, logical_key=logical_key,
                contract_ref=contract_ref, role="investigator", actual="investigator",
                stage="investigate", output_key=None,
            )
            self.assertIsNone(reused)
            snapshot = ledger.snapshot(invocation_id)
            self.assertEqual(snapshot["state"], "quarantined")
            self.assertEqual(snapshot["commit_state"], "not_committed")
            self.assertEqual(snapshot["reconciliation"], "rejected_closure_quarantined")
            self.assertIsNone(ledger.find_reusable(
                logical_key=logical_key, contract_ref=contract_ref,
                role="investigator", stage="investigate",
            ))

    def test_legacy_agent_artifact_fields_are_normalized_away_and_not_persisted_as_evidence_paths(self):
        class LegacyArtifactBackend(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                if role == "investigator":
                    value = json.loads(raw)
                    for finding in value.get("findings", []):
                        finding["artifacts"] = ["analysis/definitely-missing.json"]
                    raw = json.dumps(value)
                return thread, raw

        with tempfile.TemporaryDirectory() as temp:
            spec = self._make_spec(Path(temp))
            state = Workflow(spec, LegacyArtifactBackend(reworks=0)).run()
            self.assertEqual(state["status"], "over")

            cycle = latest_cycle_dir(spec.research_root)
            self.assertIsNotNone(cycle)
            assert cycle is not None
            ledger = InvocationLedger(cycle / "invocations.jsonl")
            investigator = [
                item for item in ledger.snapshots()
                if item.get("role") == "investigator" and item.get("stage") == "investigate"
            ]
            self.assertEqual(len(investigator), 1)
            snapshot = investigator[0]
            self.assertEqual(snapshot["state"], "committed")
            manifest = Path(snapshot["invocation_artifact_manifest"])
            self.assertTrue(manifest.is_file())
            normalized = state.get("agent_outputs", {}).get("investigator", {})
            for finding in normalized.get("findings", []):
                for field in ("artifacts", "artifact_pointers", "artifact_roles", "artifact_path", "path"):
                    self.assertNotIn(field, finding)
            normalization_files = list((cycle / "output_normalizations").glob("*.json"))
            self.assertTrue(normalization_files, "legacy artifact compatibility must be auditable")
            normalization_records = [json.loads(path.read_text(encoding="utf-8")) for path in normalization_files]
            compatibility_repairs = [
                repair
                for record in normalization_records
                for repair in record.get("repairs", [])
                if repair.get("kind") == "drop_legacy_investigator_artifact_fields"
            ]
            self.assertTrue(compatibility_repairs)
            self.assertIn("artifacts", compatibility_repairs[0]["fields"])
            compatibility_records = [
                record for record in normalization_records
                if any(repair.get("kind") == "drop_legacy_investigator_artifact_fields"
                       for repair in record.get("repairs", []))
            ]
            self.assertNotIn("definitely-missing.json", compatibility_records[0]["canonical_output"])
            closure = json.loads(Path(snapshot["closure_artifact"]).read_text(encoding="utf-8"))
            self.assertTrue(all(
                not ({"artifacts", "artifact_pointers", "artifact_roles", "artifact_path", "path"} & set(item))
                for item in closure["closed_output"].get("evidence", [])
            ))

    def test_ledger_snapshot_preserves_prepare_metadata_through_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = InvocationLedger(Path(temp) / "invocations.jsonl")
            contract = {"contract_id": "x", "version": 1}
            ledger.prepare(
                invocation_id="inv-1",
                kind="agent",
                input_payload={"x": 1},
                contract_ref=contract,
                logical_key="slot-1",
                role="research_lead",
                stage="cycle_synthesis",
            )
            ledger.transition("inv-1", "running", role="research_lead", thread_id="thr")
            ledger.transition(
                "inv-1", "committed",
                role="research_lead", stage="cycle_synthesis",
                commit_state="committed", logical_key="slot-1",
                closure_artifact="/tmp/closure.json", closure_hash="hash",
            )
            snapshot = ledger.snapshot("inv-1")
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertIn("input_hash", snapshot)
            self.assertEqual(snapshot["contract_ref"], contract)
            self.assertEqual(
                ledger.find_committed(
                    logical_key="slot-1",
                    contract_ref=contract,
                    role="research_lead",
                    stage="cycle_synthesis",
                )["invocation_id"],
                "inv-1",
            )


class ScholarlyTransportReuseTest(unittest.TestCase):
    def test_reuses_matching_test_transport_but_not_other_gap(self):
        state = {
            "last_sdk_scholarly_retrieval": {
                "schema": "theory-master/scholarly-transport/v1",
                "status": "test_stub",
                "transport_ok": True,
                "source_gap_id": "gap-1",
                "task_type": "targeted_source",
            }
        }
        reused = InvestigationMixin._reusable_scholarly_transport(
            state, {"source_gap_id": "gap-1", "task_type": "targeted_source"},
        )
        self.assertIsNotNone(reused)
        self.assertIsNone(InvestigationMixin._reusable_scholarly_transport(
            state, {"source_gap_id": "gap-2", "task_type": "targeted_source"},
        ))


if __name__ == "__main__":
    unittest.main()
