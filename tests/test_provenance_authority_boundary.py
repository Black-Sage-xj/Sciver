"""Offline regressions for declared versus verified pre-Cycle provenance."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.evidence import EvidenceRegistry
from theory_master.workflow import Workflow


class ProvenanceAuthorityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cycle = self.root / "61"
        self.workspace = self.cycle / "workspace"
        self.workspace.mkdir(parents=True)
        self.registry = EvidenceRegistry(self.cycle, self.workspace)
        self.declaration = self.root / "untrusted_outside_workspace" / "design.md"
        self.declaration.parent.mkdir()

    def register(self, *, source="pre_specified_synthetic", split="holdout"):
        self.registry.register_role_output(
            "investigator",
            {"evidence": [{
                "summary": "Synthetic test result.",
                "provenance": {
                    "source": source,
                    "split": split,
                    "declaration_ref": str(self.declaration),
                },
            }]},
            epoch="post_lock_test",
            producer_invocation={"invocation_id": "inv-provenance-1", "role": "investigator", "stage": "investigate"},
        )
        return json.loads(self.registry.records_path.read_text(encoding="utf-8").splitlines()[0])

    def manifest_record(self):
        payload = json.loads(self.registry.manifest_path.read_text(encoding="utf-8"))
        return payload["records"][0]

    def judge_view(self, record):
        workflow = Workflow.__new__(Workflow)
        workflow.cycle_dir = self.cycle
        result = workflow._materialize_investigation_result(
            {"investigation_attempt": 1, "investigation_round_count": 1},
            {"outputs": []},
            {"evidence_manifest": {"payload": {"records": [record]}}},
        )
        return result["findings"][0]["provenance"]

    def test_posthoc_unrelated_external_file_does_not_certify_holdout(self):
        # Created AFTER the Cycle workspace, with no data/split declaration.
        self.declaration.write_text("unrelated file, no held-out data specification\n", encoding="utf-8")
        raw = self.register()
        self.assertEqual(raw["provenance"]["split"], "holdout")  # Claim retained for audit.
        self.assertEqual(raw["provenance"]["freeze_status"], "unverified")
        self.assertFalse(raw["provenance"]["protected_identity_verified"])
        self.assertNotIn("frozen_before_current_cycle", raw["provenance"])
        view = self.judge_view(self.manifest_record())
        self.assertEqual(view["declared_source"], "pre_specified_synthetic")
        self.assertNotIn("source", view)
        self.assertEqual(view["declared_split"], "holdout")
        self.assertEqual(view["freeze_status"], "unverified")
        self.assertNotIn("split", view)
        self.assertNotIn("frozen_before_current_cycle", view)

    def test_even_meaningful_benchmark_declaration_is_not_proof_of_freeze(self):
        self.declaration.write_text("prediction_split = gate\n", encoding="utf-8")
        raw = self.register(source="benchmark_provided", split="gate")
        self.assertEqual(raw["provenance"]["freeze_status"], "unverified")
        self.assertEqual(self.judge_view(self.manifest_record())["declared_split"], "gate")

    def test_mutating_registered_declaration_invalidates_evidence_integrity(self):
        self.declaration.write_text("dataset: original\n", encoding="utf-8")
        raw = self.register()
        self.assertEqual(len(raw["provenance"]["declaration_sha256"]), 64)
        self.assertTrue(self.manifest_record()["adjudicable"])
        self.declaration.write_text("dataset: replaced after seeing results\n", encoding="utf-8")
        self.registry.rebuild()
        row = self.manifest_record()
        self.assertFalse(row["adjudicable"])
        self.assertEqual(row["integrity_current"]["status"], "drifted")
        self.assertIn(str(self.declaration), row["integrity_current"]["changed_paths"])

    def test_deleting_registered_declaration_invalidates_integrity(self):
        self.declaration.write_text("dataset: original\n", encoding="utf-8")
        self.register()
        self.declaration.unlink()
        self.registry.rebuild()
        row = self.manifest_record()
        self.assertFalse(row["adjudicable"])
        self.assertIn(str(self.declaration), row["integrity_current"]["missing_paths"])

    def test_legacy_frozen_boolean_is_never_promoted_in_read_projection(self):
        self.declaration.write_text("old declaration\n", encoding="utf-8")
        raw = self.register()
        original = self.registry.records_path.read_text(encoding="utf-8")
        raw["provenance"] = {
            "source": "benchmark_provided", "split": "holdout",
            "declaration_ref": str(self.declaration),
            "frozen_before_current_cycle": True,
        }
        serialized_legacy = json.dumps(raw) + "\n"
        self.registry.records_path.write_text(serialized_legacy, encoding="utf-8")
        self.registry.rebuild()
        row = self.manifest_record()
        self.assertEqual(row["provenance"]["freeze_status"], "legacy_unverified")
        self.assertFalse(row["provenance"]["protected_identity_verified"])
        self.assertNotIn("frozen_before_current_cycle", row["provenance"])
        self.assertEqual(self.judge_view(row)["declared_split"], "holdout")
        self.assertEqual(self.judge_view(row)["freeze_status"], "legacy_unverified")
        self.assertTrue(row["adjudicable"])  # Historical science remains usable as ordinary Evidence.
        self.assertEqual(self.registry.records_path.read_text(encoding="utf-8"), serialized_legacy)

    def test_ordinary_investigator_finding_is_not_affected(self):
        self.registry.register_role_output(
            "investigator",
            {"evidence": [{"summary": "A reproducible numerical statement."}]},
            epoch="post_lock_test",
            producer_invocation={"invocation_id": "inv-normal", "role": "investigator", "stage": "investigate"},
        )
        self.assertNotIn("provenance", self.manifest_record())
        self.assertTrue(self.manifest_record()["adjudicable"])

    def test_agent_generated_holdout_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "agent-generated validation cannot claim holdout"):
            self.register(source="agent_generated")


if __name__ == "__main__":
    unittest.main()
