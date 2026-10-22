from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from theory_master.evidence import EvidenceRegistry
import theory_master.evidence as evidence_module


class EvidenceRegistryTest(unittest.TestCase):
    def make_registry(self, root: Path) -> EvidenceRegistry:
        cycle = root / "cycle-0007"
        workspace = cycle / "workspace"
        workspace.mkdir(parents=True)
        return EvidenceRegistry(cycle, workspace)

    def test_register_builds_complete_manifest_and_coverage_without_judge_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            artifact = registry.workspace / "result" / "check.tsv"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("ok\n", encoding="utf-8")
            refs = registry.register_role_output("numerical", {"evidence": [{
                "summary": "counterexample", "body": "x" * 2500, "relation": "contradicts",
                "criterion_ids": ["criterion-1"], "objection_ids": ["J-1"], "issue_ids": ["issue-2"],
                "scope": "small strain", "limitations": "validated only for the frozen comparison",
                "verification": "verified", "artifact_pointers": ["result/check.tsv"],
            }]}, epoch="post_lock_test", producer_invocation={"invocation_id": "inv-1", "kind": "agent"},
                known_criterion_ids=["criterion-1", "criterion-2"])
            self.assertEqual(len(refs), 1)
            manifest = json.loads(registry.manifest_path.read_text(encoding="utf-8"))
            record = manifest["records"][0]
            self.assertEqual(record["evidence_id"], refs[0])
            self.assertEqual(record["body"], "x" * 2500)
            self.assertEqual(record["epoch"], "post_lock_test")
            self.assertEqual(record["objection_ids"], ["J-1"])
            coverage = json.loads(registry.coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(coverage["criteria"]["criterion-1"], refs)
            self.assertEqual(coverage["criteria"]["criterion-2"], [])
            self.assertEqual(coverage["issues"]["issue-2"], refs)
            self.assertEqual(coverage["contradictions"], refs)
            self.assertFalse((registry.workspace / "judge_packet.json").exists())
            self.assertEqual(record["artifact_pointers"], [str(artifact.resolve())])
            self.assertEqual(record["limitations"], "validated only for the frozen comparison")

    def test_local_artifact_pointer_resolves_across_task_and_repo_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0007"
            workspace = cycle / "workspace"
            task_local = root / "task"
            repo = root / "repo"
            workspace.mkdir(parents=True)
            task_source = task_local / "benchmark" / "source.py"
            repo_source = repo / "reference" / "note.md"
            task_source.parent.mkdir(parents=True)
            repo_source.parent.mkdir(parents=True)
            task_source.write_text("source\n", encoding="utf-8")
            repo_source.write_text("note\n", encoding="utf-8")
            registry = EvidenceRegistry(cycle, workspace, task_local_root=task_local, repo_root=repo)

            registry.register_role_output(
                "investigator",
                {"evidence": [{
                    "summary": "local sources",
                    "evidence_kind": "local_source",
                    "artifact_pointers": ["benchmark/source.py", "reference/note.md"],
                    "artifact_roles": [
                        {"path": "benchmark/source.py", "role": "implementation"},
                        {"path": "reference/note.md", "role": "documentation"},
                    ],
                }]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-local", "role": "investigator"},
            )
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["artifact_pointers"], [str(task_source.resolve()), str(repo_source.resolve())])
            self.assertEqual(record["artifact_roles"], [
                {"path": str(task_source.resolve()), "role": "implementation"},
                {"path": str(repo_source.resolve()), "role": "documentation"},
            ])

    def test_historical_reanalysis_lineage_propagates_across_one_scientific_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_home = root / "task"
            research_root = task_home / ".theory-master" / "research"
            legacy_root = root / "legacy-research"
            source = legacy_root / "95" / "workspace" / "cohort.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"networks": 16, "comparisons": 404}', encoding="utf-8")
            cycle = research_root / "96"
            workspace = cycle / "workspace"
            workspace.mkdir(parents=True)
            registry = EvidenceRegistry(
                cycle,
                workspace,
                task_local_root=task_home,
                research_root=research_root,
                legacy_research_root=legacy_root,
            )
            refs = registry.register_role_output(
                "investigator",
                {"evidence": [
                    {
                        "summary": "reuse the frozen C95 cohort",
                        "artifact_pointers": [str(source)],
                    },
                    {
                        "summary": "reanalyze all 404 comparisons",
                        "evidence_kind": "numerical",
                    },
                ]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-96", "role": "investigator"},
            )
            self.assertEqual(len(refs), 2)
            manifest = registry.context()["evidence_manifest"]["payload"]
            rows = [row for row in manifest["records"] if row["evidence_id"] in refs]
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["origin_cycle"], 95)
                self.assertEqual(row["analysis_cycle"], 96)
                self.assertEqual(row["freshness"], "historical_reanalysis")
                self.assertEqual(row["producer_invocation"]["invocation_id"], "inv-96")

    def test_legacy_registry_lineage_is_projected_without_rewriting_historical_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy_root = root / "legacy-research"
            source = legacy_root / "95" / "workspace" / "cohort.json"
            source.parent.mkdir(parents=True)
            source.write_text("old cohort", encoding="utf-8")
            cycle = legacy_root / "96"
            workspace = cycle / "workspace"
            workspace.mkdir(parents=True)
            registry = EvidenceRegistry(
                cycle,
                workspace,
                research_root=root / "task" / ".theory-master" / "research",
                legacy_research_root=legacy_root,
            )
            raw_rows = [
                {
                    "schema": "theory-master/evidence-registry/v4",
                    "evidence_id": "E-96-a",
                    "cycle_id": "96",
                    "evidence_kind": "local_source",
                    "producer_invocation": {"invocation_id": "inv-96", "role": "investigator"},
                    "source_refs": [str(source)],
                    "artifact_pointers": [],
                    "integrity": {"artifact_paths_exist": True, "artifact_hashes_recorded": True, "artifact_roles_valid": True},
                },
                {
                    "schema": "theory-master/evidence-registry/v4",
                    "evidence_id": "E-96-b",
                    "cycle_id": "96",
                    "evidence_kind": "numerical",
                    "producer_invocation": {"invocation_id": "inv-96", "role": "investigator"},
                    "artifact_pointers": [],
                    "integrity": {"artifact_paths_exist": True, "artifact_hashes_recorded": True, "artifact_roles_valid": True},
                },
            ]
            registry.evidence_dir.mkdir(parents=True)
            original = "\n".join(json.dumps(row) for row in raw_rows) + "\n"
            registry.records_path.write_text(original, encoding="utf-8")
            projected = registry.rebuild()["manifest"]["records"]
            self.assertEqual(registry.records_path.read_text(encoding="utf-8"), original)
            for row in projected:
                self.assertEqual(row["origin_cycle"], 95)
                self.assertEqual(row["analysis_cycle"], 96)
                self.assertEqual(row["freshness"], "historical_reanalysis")

    def test_invalid_pointer_is_quarantined_while_valid_artifacts_are_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cycle = root / "cycle-0007"
            workspace = cycle / "workspace"
            task_local = root / "task"
            workspace.mkdir(parents=True)
            analysis = workspace / "analysis"
            analysis.mkdir()
            result = analysis / "result.json"
            result.write_text('{"ok": true}\n', encoding="utf-8")
            readme = analysis / "README.md"
            readme.write_text("valid documentation\n", encoding="utf-8")
            registry = EvidenceRegistry(cycle, workspace, task_local_root=task_local)

            registry.register_role_output(
                "investigator",
                {"evidence": [{"summary": "result", "evidence_kind": "numerical",
                               "artifact_pointers": [
                                   "analysis/result.json",
                                   "gs_study/gs_study/task/cycle/workspace/analysis/README.md",
                                   "analysis/missing.txt",
                               ]}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-mixed", "role": "investigator"},
            )
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["artifact_pointers"], [str(result.resolve()), str(readme.resolve())])
            self.assertEqual(record["rejected_artifact_pointers"], [{
                "pointer": "analysis/missing.txt",
                "reason": f"local artifact pointer does not exist; tried: {workspace / 'analysis/missing.txt'}, {task_local / 'analysis/missing.txt'}",
            }])
            self.assertEqual(registry.last_registration_audit, [{
                "pointer": "gs_study/gs_study/task/cycle/workspace/analysis/README.md",
                "status": "corrected_unique_workspace_basename",
                "resolved_path": str(readme.resolve()),
            }, {
                "pointer": "analysis/missing.txt", "status": "rejected",
                "reason": f"local artifact pointer does not exist; tried: {workspace / 'analysis/missing.txt'}, {task_local / 'analysis/missing.txt'}",
            }])

    def test_all_invalid_pointers_are_audited_without_registering_unreproducible_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = self.make_registry(root)
            refs = registry.register_role_output(
                "investigator",
                {"evidence": [{"summary": "claim", "evidence_kind": "numerical",
                               "artifact_pointers": ["analysis/missing.json"]}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-invalid", "role": "investigator"},
            )
            self.assertEqual(len(refs), 1)
            self.assertTrue(refs[0].startswith("E-cycle-0007-"))
            self.assertFalse(registry.records_path.exists())
            self.assertEqual([item["status"] for item in registry.last_registration_audit],
                             ["rejected", "all_artifacts_rejected"])

    def test_external_artifact_references_are_preserved_without_local_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            pointers = ["https://example.org/paper", "doi:10.1000/example", "10.1000/bare-doi"]
            registry.register_role_output(
                "literature",
                {"evidence": [{"summary": "external references", "artifact_pointers": pointers}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-lit", "role": "literature"},
            )
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["artifact_pointers"], pointers)

    def test_literature_is_complete_registry_evidence_without_a_second_role_field(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            registry.register_role_output(
                "literature",
                {"evidence_records": [{
                    "paper_title": "Registry-only paper", "authors": ["A. Author"], "year": 2026,
                    "doi_or_url": "10.1000/registry-only", "scientific_insight": "A bounded result.",
                    "relation_to_hypothesis": "supports", "source_access": "abstract",
                    "exact_quote": "A bounded result.", "locator": {"section": "Abstract"},
                    "interpretation": "Directly addresses the criterion.", "limitations": "Abstract only.",
                }]},
                epoch="post_lock_test", producer_invocation={"invocation_id": "inv-lit", "role": "literature"},
            )
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["source"], {"field": "evidence_records", "index": 0})
            self.assertEqual(record["producer_invocation"]["role"], "literature")
            self.assertEqual(record["literature_source"]["paper_title"], "Registry-only paper")
            self.assertEqual(record["exact_quote"], "A bounded result.")
            self.assertEqual(record["locator"], {"section": "Abstract"})
            # A model declaration without a matching SDK retrieval record is
            # preserved for audit, but is not adjudicable scientific Evidence.
            context = registry.context()
            self.assertNotIn("judge_packet", context)
            self.assertEqual(context["coverage_matrix"]["payload"]["active_evidence_ids"], [])

    def test_literature_unavailable_access_is_audit_only_not_adjudicable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = self.make_registry(root)
            retrieval = root / "papers.json"
            retrieval.write_text(json.dumps([{
                "doi": "10.1000/unavailable", "title": "Unavailable paper",
            }]), encoding="utf-8")
            evidence_id = registry.register_role_output(
                "literature", {"evidence_records": [{
                    "paper_title": "Unavailable paper", "doi_or_url": "10.1000/unavailable",
                    "scientific_insight": "The source could not be inspected.",
                    "relation_to_hypothesis": "motivates", "source_access": "unavailable",
                    "interpretation": "Audit-only source gap.", "criterion_ids": ["M-S1"],
                }]}, epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-unavailable", "role": "literature"},
                source_transport={"papers_json": str(retrieval)},
            )[0]
            context = registry.context()
            record = next(item for item in context["evidence_manifest"]["payload"]["records"]
                          if item["evidence_id"] == evidence_id)
            self.assertEqual(record["source_access"]["verified"], "unavailable")
            self.assertEqual(record["source_access"]["verification_status"], "verified")
            self.assertFalse(record["adjudicable"])
            self.assertNotIn(evidence_id, context["coverage_matrix"]["payload"]["active_evidence_ids"])
            self.assertNotIn("judge_packet", context)

    def test_literature_full_text_access_is_bound_to_sdk_reading_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = self.make_registry(root)
            retrieval = root / "papers.json"
            retrieval.write_text(json.dumps([{
                "doi": "10.1000/read", "title": "Read paper", "abstract": "An abstract.",
            }]), encoding="utf-8")
            manifest = root / "pdf_manifest.csv"
            manifest.write_text(
                "key,title,doi,status,markdown_path\nread-1,Read paper,10.1000/read,read,/tmp/read.md\n",
                encoding="utf-8",
            )
            registry.register_role_output(
                "literature", {"evidence_records": [{
                    "paper_title": "Read paper", "doi_or_url": "10.1000/read",
                    "scientific_insight": "Observed in the paper.", "relation_to_hypothesis": "supports",
                    "source_access": "full_text", "exact_quote": "Observed in the paper.",
                    "locator": {"page": "1"}, "interpretation": "Bound to reading output.",
                }]}, epoch="post_lock_test", producer_invocation={"invocation_id": "inv-read", "role": "literature"},
                source_transport={"papers_json": str(retrieval), "reading": {"pdf_manifest": str(manifest)}},
            )
            record = json.loads(registry.records_path.read_text(encoding="utf-8").splitlines()[0])
            access = record["source_access"]
            self.assertEqual(access["verified"], "full_text")
            self.assertEqual(access["verification_status"], "verified")
            self.assertEqual(access["reading_ref"]["key"], "read-1")

    def test_changed_local_artifact_is_lifecycle_drift_and_not_sent_to_judge(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            artifact = registry.workspace / "result.txt"
            artifact.write_text("first", encoding="utf-8")
            evidence_id = registry.register_role_output(
                "investigator", {"evidence": [{
                    "summary": "local result", "evidence_kind": "numerical", "artifact_pointers": ["result.txt"],
                }]}, epoch="post_lock_test", producer_invocation={"invocation_id": "inv-local", "role": "investigator"},
            )[0]
            artifact.write_text("changed", encoding="utf-8")
            context = registry.context()
            manifest = context["evidence_manifest"]["payload"]
            row = next(item for item in manifest["records"] if item["evidence_id"] == evidence_id)
            self.assertEqual(row["lifecycle_status"], "integrity_drifted")
            self.assertTrue(any(item["action"] == "integrity_drift" for item in manifest["lifecycle_events"]))
            self.assertNotIn(evidence_id, context["coverage_matrix"]["payload"]["active_evidence_ids"])
            self.assertNotIn("judge_packet", context)

    def test_stable_id_deduplicates_and_lifecycle_is_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            invocation = {"invocation_id": "inv-1", "kind": "agent"}
            old_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "claim", "evidence_kind": "theoretical"}]}, epoch="post_lock_test", producer_invocation=invocation
            )[0]
            duplicate = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "claim", "evidence_kind": "theoretical"}]}, epoch="post_lock_test", producer_invocation=invocation
            )[0]
            new_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "corrected claim", "evidence_kind": "theoretical"}]}, epoch="rework",
                producer_invocation={"invocation_id": "inv-2", "kind": "agent"},
            )[0]
            self.assertEqual(old_id, duplicate)
            self.assertEqual(len(registry.records_path.read_text(encoding="utf-8").splitlines()), 2)
            registry.append_lifecycle("supersede", old_id, reason="corrected", superseding_evidence_id=new_id)
            manifest = json.loads(registry.manifest_path.read_text(encoding="utf-8"))
            records = {item["evidence_id"]: item for item in manifest["records"]}
            self.assertEqual(records[old_id]["lifecycle_status"], "superseded")
            self.assertEqual(records[new_id]["lifecycle_status"], "active")
            raw = [json.loads(line) for line in registry.records_path.read_text(encoding="utf-8").splitlines()]
            self.assertNotIn("lifecycle_status", raw[0])
            self.assertEqual(len(registry.lifecycle_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_investigator_relation_does_not_preclassify_science(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            refs = registry.register_role_output(
                "investigator", {"evidence": [{
                    "summary": "bounded result", "relation": "limits_scope",
                    "criterion_ids": ["criterion-1"], "evidence_kind": "numerical",
                }]}, epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-1", "kind": "agent"},
                known_criterion_ids=["criterion-1"],
            )
            coverage = json.loads(registry.coverage_path.read_text(encoding="utf-8"))
            self.assertEqual(coverage["contradictions"], [])
            self.assertEqual(coverage["scope_limitations"], [])
            self.assertEqual(len(refs), 1)


    def test_investigator_science_uses_sdk_producer_identity_without_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            refs = registry.register_role_output(
                "investigator",
                {"evidence": [
                    {"summary": "closed-form derivation"},
                    {"summary": "controlled computation"},
                ]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-investigator", "role": "investigator", "stage": "investigate"},
            )
            self.assertEqual(len(refs), 2)
            manifest = json.loads(registry.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual([row["producer_invocation"]["role"] for row in manifest["records"]], ["investigator", "investigator"])
            self.assertTrue(all("producer_role" not in row for row in manifest["records"]))
            self.assertTrue(all("evidence_kind" not in row for row in manifest["records"]))
            self.assertTrue(all(row["adjudicable"] for row in manifest["records"]))
            self.assertFalse((registry.workspace / "judge_packet.json").exists())

    def test_evidence_registration_batch_is_atomic_and_retry_completes(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            output = {"evidence": [
                {"summary": "result A", "evidence_kind": "theoretical"},
                {"summary": "result B", "evidence_kind": "theoretical"},
            ]}
            invocation = {"invocation_id": "inv-evidence-batch", "role": "investigator", "stage": "investigate"}
            original_append = evidence_module._append
            row_writes = 0

            def fail_on_second_evidence_row(path, value):
                nonlocal row_writes
                if path == registry.records_path and value.get("evidence_id"):
                    row_writes += 1
                    if row_writes == 2:
                        raise OSError("simulated second evidence append failure")
                return original_append(path, value)

            with patch.object(evidence_module, "_append", side_effect=fail_on_second_evidence_row):
                with self.assertRaisesRegex(OSError, "simulated second evidence append failure"):
                    registry.register_role_output(
                        "investigator", output, epoch="post_lock_test", producer_invocation=invocation,
                    )

            raw_partial = [json.loads(line) for line in registry.records_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(raw_partial), 1)
            self.assertTrue(raw_partial[0].get("evidence_batch_id"))
            self.assertEqual(registry.rebuild()["manifest"]["records"], [])
            self.assertFalse(registry.registration_batches_path.exists())

            refs = registry.register_role_output(
                "investigator", output, epoch="post_lock_test", producer_invocation=invocation,
            )
            manifest = registry.rebuild()["manifest"]
            self.assertEqual([row["evidence_id"] for row in manifest["records"]], refs)
            committed_rows = [json.loads(line) for line in registry.records_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(committed_rows), 2)
            self.assertEqual(len({row["evidence_batch_id"] for row in committed_rows}), 1)
            commits = [json.loads(line) for line in registry.registration_batches_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(commits), 1)
            self.assertEqual(commits[0]["event"], "batch_committed")
            self.assertEqual(commits[0]["evidence_ids"], refs)

    def test_coverage_matrix_uses_only_adjudicable_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            refs = registry.register_role_output(
                "investigator",
                {"evidence": [
                    {"summary": "contradiction", "evidence_kind": "theoretical", "criterion_ids": ["M-S1"],
                     "issue_ids": ["I-1"], "relation": "contradicts"},
                    {"summary": "scope bound", "evidence_kind": "theoretical", "criterion_ids": ["M-S1"],
                     "issue_ids": ["I-2"], "relation": "limits_scope"},
                    {"summary": "active support", "evidence_kind": "theoretical", "criterion_ids": ["M-S1"],
                     "relation": "supports"},
                ]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-coverage", "role": "investigator"},
                known_criterion_ids=["M-S1"],
            )
            registry.append_lifecycle("retract", refs[0], reason="superseded contradiction")
            registry.append_lifecycle("retract", refs[1], reason="scope result withdrawn")
            context = registry.context()
            coverage = context["coverage_matrix"]["payload"]
            self.assertEqual(coverage["criteria"]["M-S1"], [refs[2]])
            self.assertEqual(coverage["issues"], {})
            self.assertEqual(coverage["contradictions"], [])
            self.assertEqual(coverage["scope_limitations"], [])
            self.assertEqual(coverage["active_evidence_ids"], [refs[2]])
            self.assertNotIn("judge_packet", context)

    def test_investigator_evidence_needs_no_model_authored_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            refs = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "conditional scientific claim"}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-investigator", "role": "investigator"},
            )
            self.assertEqual(len(refs), 1)
            record = registry.context()["evidence_manifest"]["payload"]["records"][0]
            self.assertNotIn("evidence_kind", record)
            self.assertEqual(record["producer_invocation"]["role"], "investigator")
            self.assertTrue(record["adjudicable"])

    def test_current_judge_assessment_api_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            evidence_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "bounded result", "evidence_kind": "theoretical"}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-investigator", "role": "investigator"},
            )[0]
            raw_before = registry.records_path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                registry.append_judge_assessments(
                    [{"evidence_id": evidence_id, "status": "partial", "reason": "legacy shape"}],
                    judge_invocation={"invocation_id": "inv-judge", "role": "judge", "stage": "terminal_assessment"},
                )
            self.assertEqual(registry.records_path.read_text(encoding="utf-8"), raw_before)
            self.assertFalse(registry.assessments_path.exists())
            manifest = registry.rebuild()["manifest"]
            self.assertNotIn("judge_assessment", manifest["records"][0])

    def test_historical_v2_committed_assessment_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            evidence_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "historical bounded result", "evidence_kind": "theoretical"}]},
                epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-investigator", "role": "investigator"},
            )[0]
            batch_id = "EAB-historical"
            assessment_id = "EA-historical-v2"
            registry.assessments_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "schema": evidence_module.ASSESSMENT_SCHEMA,
                    "assessment_id": assessment_id,
                    "assessment_batch_id": batch_id,
                    "evidence_id": evidence_id,
                    "status": "verified",
                    "reason": "historical committed assessment",
                    "judge_invocation": {"invocation_id": "inv-old-judge", "role": "judge"},
                },
                {
                    "schema": evidence_module.ASSESSMENT_BATCH_COMMIT_SCHEMA,
                    "event": "batch_committed",
                    "assessment_batch_id": batch_id,
                    "assessment_ids": [assessment_id],
                },
            ]
            registry.assessments_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            manifest = registry.rebuild()["manifest"]
            self.assertEqual(manifest["records"][0]["judge_assessment"]["assessment_id"], assessment_id)

    def test_legacy_v1_judge_assessment_remains_implicitly_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            evidence_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "legacy claim", "evidence_kind": "theoretical"}]},
                epoch="post_lock_test", producer_invocation={"invocation_id": "inv-investigator", "role": "investigator"},
            )[0]
            registry.assessments_path.parent.mkdir(parents=True, exist_ok=True)
            registry.assessments_path.write_text(json.dumps({
                "schema": evidence_module.LEGACY_ASSESSMENT_SCHEMA,
                "assessment_id": "EA-legacy",
                "evidence_id": evidence_id,
                "status": "partial",
                "reason": "historical assessment",
                "judge_invocation": {"invocation_id": "inv-old-judge", "role": "judge"},
            }) + "\n", encoding="utf-8")
            manifest = registry.rebuild()["manifest"]
            self.assertEqual(manifest["records"][0]["judge_assessment"]["assessment_id"], "EA-legacy")

    def test_legacy_producer_role_is_only_a_read_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            registry.records_path.parent.mkdir(parents=True, exist_ok=True)
            registry.records_path.write_text(json.dumps({
                "evidence_id": "E-legacy", "producer_role": "investigator",
                "producer_invocation": {}, "summary": "legacy", "body": "legacy",
            }) + "\n", encoding="utf-8")
            manifest = registry.rebuild()["manifest"]
            self.assertEqual(manifest["records"][0]["producer_invocation"]["role"], "investigator")




    def test_supersede_rejects_unknown_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self.make_registry(Path(temp))
            evidence_id = registry.register_role_output(
                "investigator", {"evidence": [{"summary": "claim", "evidence_kind": "numerical"}]}, epoch="post_lock_test",
                producer_invocation={"invocation_id": "inv-1"},
            )[0]
            with self.assertRaisesRegex(ValueError, "registered superseding"):
                registry.append_lifecycle("supersede", evidence_id, reason="bad", superseding_evidence_id="missing")


if __name__ == "__main__":
    unittest.main()
