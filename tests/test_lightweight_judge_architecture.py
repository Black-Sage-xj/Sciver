from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.core import Workflow
from theory_master.invocation_artifacts import InvocationArtifactTracker


class InvocationArtifactManifestTest(unittest.TestCase):
    def test_sdk_records_created_modified_deleted_files_without_agent_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0001"
            workspace = cycle / "workspace"
            workspace.mkdir(parents=True)
            modified = workspace / "modified.txt"
            deleted = workspace / "deleted.txt"
            modified.write_text("before", encoding="utf-8")
            deleted.write_text("remove", encoding="utf-8")

            tracker = InvocationArtifactTracker.start(
                cycle_dir=cycle,
                workspace=workspace,
                invocation_id="inv-1",
                role="investigator",
                stage="investigate",
            )
            modified.write_text("after", encoding="utf-8")
            deleted.unlink()
            created = workspace / "results" / "result.json"
            created.parent.mkdir()
            created.write_text('{"value": 3}', encoding="utf-8")

            manifest_path = tracker.finalize(status="completed")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "theory-master/invocation-artifacts/v1")
            self.assertEqual(manifest["invocation_id"], "inv-1")
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual([row["relative_path"] for row in manifest["created"]], ["results/result.json"])
            self.assertEqual([row["relative_path"] for row in manifest["modified"]], ["modified.txt"])
            self.assertEqual(manifest["deleted"], ["deleted.txt"])
            self.assertEqual(
                manifest["created"][0]["sha256"],
                hashlib.sha256(created.read_bytes()).hexdigest(),
            )
            self.assertEqual(tracker.finalize(status="failed"), manifest_path)


class LightweightJudgeBoundaryTest(unittest.TestCase):
    def test_investigation_result_strips_registry_and_artifact_bookkeeping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = Workflow.__new__(Workflow)
            workflow.cycle_dir = root / "cycle-0001"
            workflow.cycle_dir.mkdir()
            state = {"investigation_attempt": 1, "investigation_round_count": 2}
            record = {
                "outputs": [{"output": {
                    "remaining_gap": {"kind": "none", "description": ""},
                }}],
            }
            packet_context = {
                "evidence_manifest": {"payload": {"records": [{
                    "evidence_id": "E-secret-machine-id",
                    "producer_invocation": {"role": "investigator"},
                    "lifecycle_status": "active",
                    "adjudicable": True,
                    "summary": "The controlled calculation favors H in the tested regime.",
                    "body": "raw body not needed by Judge",
                    "relation": "supports",
                    "criterion_ids": ["M-S1"],
                    "objection_ids": [],
                    "scope": "tested regime",
                    "limitations": "finite sample",
                    "evidence_kind": "numerical",
                    "origin_cycle": 95,
                    "analysis_cycle": 96,
                    "freshness": "historical_reanalysis",
                    "artifact_pointers": ["/tmp/raw/result.csv"],
                    "artifact_roles": [{"path": "/tmp/raw/result.csv", "role": "result"}],
                    "integrity": {"artifact_hashes_recorded": True},
                    "judge_assessment": {"status": "verified"},
                    "provenance": {
                        "source": "benchmark_provided",
                        "split": "holdout",
                        "declaration_ref": "/tmp/private/spec.json",
                        "frozen_before_current_cycle": True,
                    },
                }]}}}

            result = workflow._materialize_investigation_result(state, record, packet_context)
            self.assertEqual(result["schema"], "theory-master/investigation-result/v1")
            self.assertEqual(result["findings"][0]["statement"], "The controlled calculation favors H in the tested regime.")
            self.assertEqual(result["findings"][0]["origin_cycle"], 95)
            self.assertEqual(result["findings"][0]["analysis_cycle"], 96)
            self.assertEqual(result["findings"][0]["freshness"], "historical_reanalysis")
            self.assertEqual(result["findings"][0]["provenance"], {
                "declared_source": "benchmark_provided",
                "declared_split": "holdout",
                "freeze_status": "legacy_unverified",
            })
            serialized = json.dumps(result, ensure_ascii=False)
            for forbidden in (
                "E-secret-machine-id", "artifact_pointers", "artifact_roles", "integrity",
                "judge_assessment", "declaration_ref", "/tmp/raw/result.csv", "/tmp/private/spec.json",
            ):
                self.assertNotIn(forbidden, serialized)
            result_path = Path(record["investigation_result_path"])
            self.assertTrue(result_path.is_file())
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)

    def test_judge_identity_does_not_reopen_sdk_evidence_audit_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        identity_paths = (
            root / "identities" / "judge.md",
            root / "theory_master" / "resources" / "identities" / "judge.md",
        )
        for identity_path in identity_paths:
            with self.subTest(identity_path=identity_path):
                identity = identity_path.read_text(encoding="utf-8")
                self.assertIn("只裁决当前 Stage 指定的科学对象", identity)
                self.assertIn("不重做 Investigation", identity)
                self.assertIn("来源校验、预算、路由与状态提交由 SDK 负责", identity)
                for forbidden in ("cycle_progress_criteria", "Evidence Manifest", "Coverage Matrix", "Judge Packet"):
                    self.assertNotIn(forbidden, identity)


if __name__ == "__main__":
    unittest.main()
