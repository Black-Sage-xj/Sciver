from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theory_master.delivery_materials import (
    bind_invocation_materials, execution_materials, task_delivery_material_index,
)
from theory_master.evidence import EvidenceRegistry
from theory_master.invocation_artifacts import InvocationArtifactTracker
from theory_master.report_materials import material_index, report_materials
from theory_master.workflow_state import WorkflowStateMixin


class DeliveryMaterialsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "research"
        self.cycle = self.root / "1"
        self.workspace = self.cycle / "workspace"
        self.workspace.mkdir(parents=True)
        self.registry = EvidenceRegistry(self.cycle, self.workspace, research_root=self.root)
        self.producer = {"invocation_id": "inv-1", "role": "investigator", "stage": "investigate"}
        self.tracker = InvocationArtifactTracker.start(
            cycle_dir=self.cycle, workspace=self.workspace, **self.producer,
        )

    def finish(self):
        self.manifest_path = self.tracker.finalize(status="completed")
        return self.manifest_path

    def register(self, *, manifest=True, findings=None):
        return self.registry.register_role_output(
            "investigator", {"evidence": findings or [{"summary": "self-contained finding"}]},
            epoch="post_lock_test", producer_invocation=self.producer,
            invocation_artifact_manifest=self.manifest_path if manifest else None,
        )

    def rows(self):
        return self.registry.context()["evidence_manifest"]["payload"]["records"]

    def test_ignored_outputs_bridge_once_per_batch_without_scientific_support_inference(self):
        (self.workspace / ".gitignore").write_text("*.json\n*.svg\n")
        (self.workspace / "result.json").write_text('{"private_raw": 987654321}')
        (self.workspace / "figure.svg").write_text("<svg/>")
        (self.workspace / "analysis.py").write_text("print('analysis')\n")
        (self.workspace / ".tm-handoff").mkdir()
        (self.workspace / ".tm-handoff" / "inv-1.json").write_text("{}")
        for name in ["evidence_manifest.json", "coverage_matrix.json", "scratch.tmp"]:
            (self.workspace / name).write_text("SDK bookkeeping")
        self.finish()
        refs = self.register(findings=[{"summary": "first finding"}, {"summary": "second finding"}])
        records = self.rows()
        self.assertEqual(len(records), 2)
        for row in records:
            self.assertEqual(row["artifact_pointers"], [])
            self.assertEqual(row["execution_materials"]["relation"], "same_invocation")
            self.assertEqual(row["freshness"], "origin_unverified")
            self.assertEqual(row["relation"], None)
        materials = report_materials({"cycle_dir": str(self.cycle), **self.registry.context()})
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["evidence_ids"], refs)
        assets = materials[0]["assets"]
        paths = {row["relative_path"] for row in assets}
        self.assertIn("result.json", paths)
        self.assertIn("figure.svg", paths)
        self.assertNotIn(".tm-handoff/inv-1.json", paths)
        self.assertNotIn("evidence_manifest.json", paths)
        self.assertNotIn("coverage_matrix.json", paths)
        self.assertNotIn("scratch.tmp", paths)
        self.assertTrue(all(row["status"] == "available" for row in assets))
        self.assertIn("不表示每个文件支持每条 Finding", "\n".join(material_index(materials, [])))
        index = task_delivery_material_index(self.root)
        self.assertEqual(index["materials"][0]["assets"], assets)
        self.assertNotIn("987654321", json.dumps(index))

    def test_empty_artifact_integrity_does_not_block_theoretical_findings(self):
        self.finish()
        self.register()
        row = self.rows()[0]
        self.assertEqual(row["integrity_current"]["status"], "none_registered")
        self.assertEqual(row["integrity_current"]["checked_hashes"], {})
        self.assertTrue(row["adjudicable"])
        self.assertFalse(row["integrity"]["artifact_hashes_recorded"])
        material = report_materials(self.registry.context())[0]
        self.assertEqual(material["integrity"]["status"], "none_registered")
        self.assertEqual(material["assets"], [])
        self.assertFalse(self.registry.lifecycle_path.exists())

    def test_remote_only_is_not_local_hash_verification(self):
        self.registry.register_role_output(
            "investigator", {"evidence": [{"summary": "science", "artifact_pointers": ["https://example.org/data"]}]},
            epoch="post_lock_test", producer_invocation=self.producer,
        )
        row = self.rows()[0]
        self.assertEqual(row["integrity_current"]["status"], "none_registered")
        self.assertTrue(row["adjudicable"])
        self.assertEqual(report_materials(self.registry.context())[0]["assets"][0]["status"], "external")

    def test_execution_material_drift_is_not_a_scientific_rejection(self):
        result = self.workspace / "result.json"
        result.write_text("original")
        self.finish()
        self.register()
        result.write_text("changed")
        row = self.rows()[0]
        self.assertTrue(row["adjudicable"])
        self.assertEqual(row["lifecycle_status"], "active")
        self.assertEqual(row["integrity_current"]["status"], "none_registered")
        material = report_materials(self.registry.context())[0]
        self.assertEqual(material["integrity"]["status"], "drifted")
        self.assertEqual(material["assets"][0]["status"], "drifted")
        self.assertIn(str(result), material["integrity"]["changed_paths"])
        result.unlink()
        material = report_materials(self.registry.context())[0]
        self.assertEqual(material["assets"][0]["status"], "missing")
        self.assertFalse(self.registry.lifecycle_path.exists())

    def test_manifest_drift_does_not_expose_replaced_material_paths(self):
        (self.workspace / "result.json").write_text("original")
        self.finish()
        self.register()
        self.manifest_path.write_text('{"tampered": true}')
        material = task_delivery_material_index(self.root)["materials"][0]
        self.assertEqual(material["integrity"]["status"], "drifted")
        self.assertEqual(material["assets"], [])
        self.assertIn(str(self.manifest_path), material["integrity"]["changed_paths"])
        self.assertTrue(self.rows()[0]["adjudicable"])

    def test_mismatching_failed_and_wrong_workspace_manifests_cannot_bind(self):
        self.finish()
        original = json.loads(self.manifest_path.read_text())
        for field, value in [("status", "failed"), ("role", "reporter"),
                             ("invocation_id", "someone-else"), ("workspace", str(self.root / "elsewhere"))]:
            with self.subTest(field=field):
                self.manifest_path.write_text(json.dumps({**original, field: value}))
                with self.assertRaises(ValueError):
                    self.register()
                self.assertFalse(self.registry.records_path.exists())
        self.manifest_path.write_text(json.dumps(original))
        moved = self.root / "copied.json"
        moved.write_bytes(self.manifest_path.read_bytes())
        with self.assertRaises(ValueError):
            bind_invocation_materials(moved, cycle_dir=self.cycle, workspace=self.workspace,
                                      producer_invocation=self.producer)

    def test_symlink_escape_never_becomes_a_material(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("must not read")
        (self.workspace / "escape.json").symlink_to(outside)
        self.finish()
        self.assertEqual(json.loads(self.manifest_path.read_text())["created"], [])
        self.register()
        self.assertEqual(report_materials(self.registry.context())[0]["assets"], [])

    def test_manifest_path_substitution_by_symlink_is_drift(self):
        self.finish()
        binding = bind_invocation_materials(self.manifest_path, cycle_dir=self.cycle,
                                           workspace=self.workspace, producer_invocation=self.producer)
        backup = self.root / "backup.json"
        backup.write_bytes(self.manifest_path.read_bytes())
        self.manifest_path.unlink()
        self.manifest_path.symlink_to(backup)
        material = execution_materials(binding, self.producer, cycle_dir=self.cycle)
        self.assertEqual(material["integrity"]["status"], "drifted")
        self.assertEqual(material["assets"], [])

    def test_unsafe_captured_path_is_not_read_or_exposed(self):
        self.finish()
        manifest = json.loads(self.manifest_path.read_text())
        manifest["created"] = [{"path": str(Path(self.temp.name) / "private.json"),
                                 "relative_path": "../private.json", "sha256": "a" * 64, "size": 1}]
        self.manifest_path.write_text(json.dumps(manifest))
        binding = bind_invocation_materials(self.manifest_path, cycle_dir=self.cycle,
                                           workspace=self.workspace, producer_invocation=self.producer)
        material = execution_materials(binding, self.producer, cycle_dir=self.cycle)
        # Traversal is excluded as metadata, never followed as a file reference.
        self.assertEqual(material["assets"], [])
        self.assertEqual(material["integrity"]["checked_hashes"], {})
        self.assertEqual(material["integrity"]["status"], "drifted")

    def test_task_index_reports_retracted_scientific_authority_separately_from_file_availability(self):
        (self.workspace / "results.json").write_text("{}")
        self.finish()
        evidence_id = self.register()[0]
        self.registry.lifecycle_path.write_text(json.dumps({"action": "retract", "evidence_id": evidence_id}) + "\n")
        material = task_delivery_material_index(self.root)["materials"][0]
        self.assertEqual(material["evidence_states"][evidence_id], {"lifecycle_status": "retracted", "adjudicable": False})
        self.assertEqual(material["assets"][0]["status"], "available")
        self.assertEqual(report_materials(self.registry.context()), [])

    def test_registration_replay_is_idempotent_and_historical_rows_are_not_backfilled(self):
        (self.workspace / "result.json").write_text("result")
        self.finish()
        refs = self.register()
        before = self.registry.records_path.read_bytes()
        batches = self.registry.registration_batches_path.read_bytes()
        self.assertEqual(self.register(), refs)
        self.assertEqual(self.registry.records_path.read_bytes(), before)
        self.assertEqual(self.registry.registration_batches_path.read_bytes(), batches)
        self.registry.records_path.write_bytes(before.replace(
            json.dumps(json.loads(before.splitlines()[0])["execution_materials"], ensure_ascii=False, sort_keys=True).encode(), b"null"))
        legacy_before = self.registry.records_path.read_bytes()
        index_before = task_delivery_material_index(self.root)
        self.assertEqual(index_before["materials"], [])
        self.assertEqual(self.registry.records_path.read_bytes(), legacy_before)

    def test_task_index_excludes_uncommitted_rows_and_does_not_write_projections(self):
        self.finish()
        self.register()
        before = {p: p.read_bytes() for p in self.cycle.rglob("*") if p.is_file()}
        index = task_delivery_material_index(self.root)
        self.assertEqual(index["cycles"][0]["registered_material_groups"], 1)
        self.assertEqual({p: p.read_bytes() for p in self.cycle.rglob("*") if p.is_file()}, before)
        self.registry.registration_batches_path.unlink()
        self.assertEqual(task_delivery_material_index(self.root)["materials"], [])

    def test_workflow_registration_consumes_completed_invocation_manifest(self):
        (self.workspace / "results.json").write_text("{}")
        self.finish()
        registry = self.registry
        class Harness(WorkflowStateMixin):
            cycle_dir = self.cycle
            def _evidence_registry(self):
                return registry
            def _thread_key(self, state, role):
                return role
        state = {"last_invocation_id": "inv-1", "last_invocation_artifact_manifest": str(self.manifest_path),
                 "hypothesis_lock": {"id": "H-0001", "status": "locked"}, "threads": {}}
        refs = Harness()._register_evidence(state, "investigator", {"evidence": [{"summary": "science"}]},
                                            stage="investigate")
        self.assertEqual(state["evidence_refs"], refs)
        self.assertIn("execution_materials", self.rows()[0])


if __name__ == "__main__":
    unittest.main()
