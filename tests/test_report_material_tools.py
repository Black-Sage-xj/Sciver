"""Large invocation manifests stay outside Reporter prompts; reads stay attributable."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from theory_master.evidence import EvidenceRegistry
from theory_master.invocation_artifacts import InvocationArtifactTracker
from theory_master.prompt_stages import stage_context
from theory_master.report_materials import report_materials
from theory_master.report_material_tools import execute, material_access, prepare_catalog


class ReporterMaterialToolsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cycle = Path(temporary.name) / "research" / "2"
        self.workspace = self.cycle / "workspace"
        self.workspace.mkdir(parents=True)
        self.registry = EvidenceRegistry(self.cycle, self.workspace, research_root=self.cycle.parent)
        self.producer = {"invocation_id": "inv-2", "role": "investigator", "stage": "investigate"}
        self.tracker = InvocationArtifactTracker.start(cycle_dir=self.cycle, workspace=self.workspace, **self.producer)

    def file(self, name, content):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def finish(self):
        manifest = self.tracker.finalize(status="completed")
        self.registry.register_role_output("investigator", {"evidence": [{"summary": "Bounded result"}]},
                                          epoch="test", producer_invocation=self.producer,
                                          invocation_artifact_manifest=manifest)
        self.context = {"cycle_dir": str(self.cycle), "cycle_workspace": str(self.workspace), **self.registry.context()}
        self.materials = report_materials(self.context)
        self.catalog = prepare_catalog(self.cycle / "report_material_catalog.json", self.materials)
        self.context.update(report_material_rows=self.materials, report_material_catalog=self.catalog)

    def call(self, operation, reference="", **kwargs):
        return execute(Path(self.catalog["path"]), self.catalog["sha256"], operation, reference, **kwargs)

    def test_large_manifest_initial_context_is_small_and_science_is_preserved(self):
        for index in range(3216):
            self.file(f"replicates/{index}/result.json", '{"result": 1}')
        self.file("result/analysis.md", "Established comparison, with its necessary scope.")
        self.finish()
        synthesis = {"conclusions": ["comparison"], "open_questions": ["uncertainty"]}
        self.context["scientific_synthesis"] = synthesis
        projected = stage_context("reporter", "report", self.context)
        text = json.dumps(projected, ensure_ascii=False)
        self.assertLess(len(text), 8000)
        self.assertEqual(projected["scientific_synthesis"], synthesis)
        self.assertEqual(projected["report_materials"]["overview"]["total_files"], 3217)
        self.assertNotIn("checked_hashes", text)
        self.assertNotIn("replicates/3215/result.json", text)
        self.assertEqual(len(self.materials[0]["assets"]), 3217)
        self.assertEqual(self.call("list", directory="result/")["total_matches"], 1)
        self.assertIn("Established comparison", self.call("read", "result/analysis.md")["content"])

    def test_discovery_cleans_empty_logs_but_retains_unavailable_and_all_formats(self):
        self.file("result/summary.md", "Established science")
        self.file("result/table.csv", "value,uncertainty\n1,0.1")
        self.file("figure.svg", "<svg/>")
        self.file("analysis.py", "# implementation")
        self.file("worker.log", "log contents")
        self.file("empty.json", "")
        self.finish()
        overview = material_access(self.context)["overview"]
        self.assertEqual(overview["empty_files"], 1)
        self.assertEqual(overview["log_files"], 1)
        listed = self.call("list")["assets"]
        self.assertEqual({a["kind"] for a in listed}, {"document", "table", "figure", "script"})
        self.assertEqual(self.call("list", include_auxiliary=True)["total_matches"], 6)
        self.assertEqual(self.call("read", "empty.json")["content"], "")
        self.assertEqual(self.call("list", query="SUMMARY")["total_matches"], 1)
        self.assertEqual(self.call("read", "figure.svg")["status"], "figure")
        self.assertIn("image_ref", self.call("read", "figure.svg")["asset"])

    def test_unicode_content_and_listing_are_paginated_without_loss(self):
        content = "数值与范围αβ\n" * 1000
        self.file("analysis.md", content)
        for index in range(45):
            self.file(f"result/{index:03}.json", "{}")
        self.finish()
        first = self.call("list", directory="result/", limit=30)
        second = self.call("list", directory="result/", limit=30, offset=first["next_offset"])
        self.assertEqual(len(first["assets"]) + len(second["assets"]), 45)
        self.assertIsNone(second["next_offset"])
        parts, offset = [], 0
        while True:
            page = self.call("read", "analysis.md", offset=offset, max_chars=1100)
            parts.append(page["content"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(parts), content)
        self.assertLessEqual(len(self.call("read", "analysis.md", max_chars=10**9)["content"]), 24000)

    def test_reads_refuse_drift_missing_symlink_unregistered_and_catalog_tampering(self):
        target = self.file("result.json", "original")
        self.finish()
        registry = self.registry.records_path.read_bytes()
        target.write_text("changed")
        self.assertEqual(self.call("read", "result.json")["status"], "drifted")
        target.unlink()
        self.assertEqual(self.call("read", "result.json")["status"], "missing_or_symlink")
        private = self.cycle / "private.json"
        private.write_text("not registered")
        self.assertEqual(self.call("read", str(private))["status"], "unregistered_or_ambiguous")
        target.symlink_to(private)
        self.assertEqual(self.call("read", "result.json")["status"], "missing_or_symlink")
        self.assertEqual(self.registry.records_path.read_bytes(), registry)
        Path(self.catalog["path"]).write_text("{}")
        self.assertEqual(self.call("list")["status"], "catalog_unavailable_or_changed")

    def test_missing_drifted_and_retracted_materials_do_not_become_evidence(self):
        target = self.file("changed.json", "original")
        missing = self.file("missing.json", "original")
        self.finish()
        target.write_text("changed")
        missing.unlink()
        materials = report_materials(self.context)
        catalog = prepare_catalog(self.cycle / "updated_catalog.json", materials)
        result = execute(Path(catalog["path"]), catalog["sha256"], "list")
        self.assertEqual({a["status"] for a in result["assets"]}, {"drifted", "missing"})
        evidence_id = self.materials[0]["evidence_ids"][0]
        self.registry.lifecycle_path.write_text(json.dumps({"action": "retract", "evidence_id": evidence_id}) + "\n")
        self.assertEqual(report_materials({**self.context, **self.registry.context()}), [])

    def test_cli_and_theoretical_no_material_case(self):
        self.finish()
        self.assertEqual(material_access(self.context)["overview"]["total_files"], 0)
        command = [sys.executable, "-m", "theory_master.report_material_tools", "--catalog", self.catalog["path"],
                   "--sha256", self.catalog["sha256"], "list"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["assets"], [])

    def test_directory_overview_is_bounded_and_legacy_references_are_pinned(self):
        target = self.file("legacy.md", "registered explanation")
        materials = [{"evidence_id": "E-legacy", "assets": [
            {"reference": str(target), "path": str(target), "status": "available", "kind": "document"},
            *[{"reference": f"https://example.invalid/{i}/figure.svg", "relative_path": f"group-{i}/figure.svg",
               "status": "external", "kind": "figure"} for i in range(25)],
        ]}]
        self.catalog = prepare_catalog(self.cycle / "legacy_catalog.json", materials)
        first = self.call("overview")
        second = self.call("overview", offset=first["next_offset"])
        third = self.call("overview", offset=second["next_offset"])
        self.assertEqual(len(first["directories"]), 12)
        self.assertEqual(len(first["directories"]) + len(second["directories"]) + len(third["directories"]), 26)
        self.assertEqual(self.call("read", str(target))["content"], "registered explanation")
        target.write_text("replaced")
        self.assertEqual(self.call("read", str(target))["status"], "drifted")


if __name__ == "__main__":
    unittest.main()
