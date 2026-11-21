from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from theory_master.backends import json_object
from theory_master.contracts import PHASE_CONTRACTS, contract_by_id, role_contract
from theory_master.identity import ROLES as IDENTITY_ROLES
from theory_master.observability.models import DISPLAY_ROLE, ROLES as OBSERVABILITY_ROLES
from theory_master.output_normalization import normalize_agent_output
from theory_master.prompt_stages import ROLE_STAGE_LABELS, STAGE_CARDS, stage_context, stage_for
from theory_master.reporting import ReportingMixin
from theory_master.skills import ROLE_DISPLAY_NAMES, role_display_name
from theory_master.stage_registry import stage_card_text, procedure_card_text, stage_definition_text


class RefactorGuardrailsTest(unittest.TestCase):
    """Characterization tests for behavior-preserving module decomposition."""

    def test_stage_registry_and_stage_cards_are_the_same_runtime_surface(self):
        self.assertLessEqual(set(PHASE_CONTRACTS), set(STAGE_CARDS))
        for role_stage in sorted(PHASE_CONTRACTS):
            role, stage = role_stage
            with self.subTest(role=role, stage=stage):
                card = STAGE_CARDS[role_stage]
                required = {"objective", "may_do", "must_not", "exit"}
                self.assertLessEqual(required, set(card))
                self.assertLessEqual(set(card), required | {"procedure"})
                for key in required:
                    self.assertIsInstance(card[key], str)
                    self.assertTrue(card[key].strip())
                if "procedure" in card:
                    self.assertIsInstance(card["procedure"], (tuple, list))
                    self.assertTrue(all(isinstance(step, str) and step.strip()
                                        for step in card["procedure"]))
                rendered = stage_definition_text(role, stage)
                stage_text = stage_card_text(role, stage)
                procedure = procedure_card_text(role, stage)
                self.assertEqual(rendered, stage_text + procedure)
                self.assertEqual(rendered.count(stage_text), 1)
                if procedure:
                    self.assertEqual(rendered.count(procedure), 1)
                    for index, step in enumerate(card["procedure"], 1):
                        self.assertIn(f"{index}. {step}\n", procedure)

    def test_every_runtime_stage_has_a_display_label(self):
        missing = {
            stage
            for _, stage in STAGE_CARDS
            if stage not in ROLE_STAGE_LABELS or not str(ROLE_STAGE_LABELS[stage]).strip()
        }
        self.assertEqual(missing, set())

    def test_every_phase_contract_round_trips_by_public_lookup(self):
        contract_ids: set[str] = set()
        for (role, stage), registered in PHASE_CONTRACTS.items():
            with self.subTest(role=role, stage=stage):
                resolved = role_contract(role, phase=stage)
                self.assertEqual(resolved.descriptor(), registered.descriptor())
                by_id = contract_by_id(registered.contract_id, registered.version)
                self.assertIsNotNone(by_id)
                self.assertEqual(by_id.descriptor(), registered.descriptor())
                contract_ids.add(registered.contract_id)

        # A runtime stage owns one stable contract identity even when schemas are shared.
        self.assertEqual(len(contract_ids), len(PHASE_CONTRACTS))

    def test_stage_router_can_reach_only_registered_contract_stages(self):
        cases = [
            ("research_lead", {"mode": "cycle_start"}, None, "frontier_proposal"),
            ("research_lead", {"mode": "cycle_start", "phase": "finalization"}, None, "candidate_selection"),
            (
                "research_lead",
                {"mode": "cycle_start", "phase": "finalization", "qualification_error": {"code": "revise"}},
                None,
                "selection_repair",
            ),
            ("research_lead", {"mode": "cycle_end"}, None, "cycle_synthesis"),
            ("research_lead", {"mode": "hypothesis_display"}, None, "hypothesis_display"),
            ("research_lead", {"mode": "frontier_maintenance"}, None, "frontier_maintenance"),
            ("challenger", {}, None, "candidate_review"),
            ("challenger", {
                "mode": "repair_verification",
                "original_candidate": {"id": "parent", "hypothesis": "H", "scope": "S", "decisive_test": "T"},
                "repaired_candidate": {"id": "repaired", "hypothesis": "H", "scope": "S", "decisive_test": "T clarified"},
                "original_review": {"blocking_issues": [{"blocker_id": "B-1", "problem": "Ambiguous T", "required_change": "Clarify T"}]},
            }, None, "repair_verification"),
            ("investigator", {"round": 1}, None, "investigate"),
            ("investigator", {"mode": "preserve_research"}, None, "preserve_research"),
            ("investigator", {"round": 2, "routing_source": "literature"}, None, "integrate_literature"),
            ("investigator", {"round": 2, "judge_objections": [{"objection_id": "J-1"}]}, None, "judge_rework"),
            ("literature", {"literature_task": {"task_type": "landscape"}}, None, "landscape"),
            ("literature", {"literature_task": {"task_type": "targeted_source"}}, None, "targeted_source"),
            ("literature", {"literature_task": {"task_type": "novelty_check"}}, None, "novelty_check"),
            ("judge", {}, None, "terminal_assessment"),
            ("judge", {"mode": "goal_completion"}, None, "goal_completion"),
            ("reporter", {}, None, "report"),
        ]

        reached: set[tuple[str, str]] = set()
        for role, context, contract_role, expected in cases:
            with self.subTest(role=role, expected=expected):
                stage = stage_for(role, {}, context, contract_role=contract_role)
                self.assertEqual(stage, expected)
                self.assertIn((role, stage), PHASE_CONTRACTS)
                self.assertIn((role, stage), STAGE_CARDS)
                role_contract(role, phase=stage)
                projection_context = dict(context)
                if (role, stage) in {
                    ("research_lead", "frontier_proposal"),
                    ("research_lead", "frontier_maintenance"),
                    ("challenger", "candidate_review"),
                    ("challenger", "repair_verification"),
                    ("judge", "goal_completion"),
                }:
                    projection_context["agent_graph"] = {
                        "schema": "theorymaster/test-agent-graph/v1",
                        "hypotheses": [], "current_frontiers": [], "lineage": [], "external_sources": [],
                    }
                projected = stage_context(role, stage, projection_context)
                self.assertIsInstance(projected, dict)
                json.dumps(projected, ensure_ascii=False)
                if (role, stage) == ("challenger", "repair_verification"):
                    self.assertEqual(projected["original_blockers"], context["original_review"]["blocking_issues"])
                    self.assertEqual(projected["original_candidate"]["id"], "parent")
                    self.assertEqual(projected["repaired_candidate"]["id"], "repaired")
                reached.add((role, stage))

        self.assertEqual(reached, set(PHASE_CONTRACTS))

    def test_retired_frontier_planning_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "frontier_planning is retired"):
            stage_for("research_lead", {}, {"mode": "frontier_planning"})

    def test_role_metadata_views_do_not_drift(self):
        self.assertEqual(tuple(OBSERVABILITY_ROLES), tuple(IDENTITY_ROLES))
        self.assertLessEqual(set(IDENTITY_ROLES), set(ROLE_DISPLAY_NAMES))
        self.assertLessEqual(set(IDENTITY_ROLES), set(DISPLAY_ROLE))
        for role in IDENTITY_ROLES:
            with self.subTest(role=role):
                expected = ROLE_DISPLAY_NAMES[role]
                self.assertEqual(DISPLAY_ROLE[role], expected)
                self.assertEqual(role_display_name(role), expected)

    def test_reporter_context_projects_evidence_without_machine_payloads(self):
        evidence_manifest = {"payload": {"records": [
            {
                "evidence_id": "E-new", "lifecycle_status": "active", "adjudicable": True, "relation": "supports",
                "verification": "verified", "summary": "controlled result", "criterion_ids": ["M-S1"],
                "scope": "frozen comparison", "limitations": "not established outside this regime",
                "artifact_pointers": ["/tmp/result.json"], "body": "large raw body",
                "producer_invocation": {"invocation_id": "inv-1"}, "provenance": {"tool": "python"},
                "origin_cycle": 95, "analysis_cycle": 96, "freshness": "historical_reanalysis",
            },
            {
                "evidence_id": "E-old", "lifecycle_status": "active", "adjudicable": False, "relation": "neutral",
                "verification": "partial", "summary": "legacy evidence without limitations",
                "scope": "legacy scope", "criterion_ids": ["M-F1"], "artifact_pointers": [],
            },
        ]}}
        projected = stage_context("reporter", "report", {
            "evidence_manifest": evidence_manifest,
            "evolution_candidates": [], "authorized_next_frontier_candidate_id": None,
        })
        digest = projected["evidence_scope_digest"]
        self.assertEqual(len(digest), 1)
        self.assertEqual(digest[0]["criterion_ids"], ["M-S1"])
        self.assertEqual(digest[0]["limitations"], "not established outside this regime")
        self.assertEqual(digest[0]["provenance"], {"tool": "python"})
        self.assertEqual(digest[0]["origin_cycle"], 95)
        self.assertEqual(digest[0]["analysis_cycle"], 96)
        self.assertEqual(digest[0]["freshness"], "historical_reanalysis")
        for forbidden in ("body", "producer_invocation", "artifact_pointers"):
            self.assertNotIn(forbidden, digest[0])

    def test_reporter_evidence_digest_preserves_all_records_beyond_twelve(self):
        records = [
            {"evidence_id": f"E-{i:02d}", "adjudicable": True,
             "summary": f"finding {i}", "relation": "neutral"}
            for i in range(19)
        ]
        # The last record is the decisive counterexample. Neither the digest
        # nor the report-stage projection may silently discard it by position.
        records[-1].update({"relation": "contradicts", "summary": "last-record counterexample",
                            "limitations": "valid in the locked scope"})
        projected = stage_context("reporter", "report", {
            "evidence_manifest": {"payload": {"records": records}},
        })
        digest = projected["evidence_scope_digest"]
        self.assertEqual(len(digest), 19)
        self.assertEqual([row["evidence_id"] for row in digest], [f"E-{i:02d}" for i in range(19)])
        self.assertEqual(digest[-1]["relation"], "contradicts")
        self.assertEqual(digest[-1]["limitations"], "valid in the locked scope")

    def test_judge_context_preserves_evidence_lineage_without_machine_ids(self):
        projected = stage_context("judge", "terminal_assessment", {
            "investigation_result": {"findings": [{
                "statement": "re-analysis confirms the bounded effect",
                "evidence_kind": "numerical",
                "origin_cycle": 95,
                "analysis_cycle": 96,
                "freshness": "historical_reanalysis",
                "evidence_id": "E-machine-only",
                "artifact_pointers": ["/tmp/raw.json"],
            }]},
        })
        finding = projected["investigation_result"]["findings"][0]
        self.assertEqual(finding["origin_cycle"], 95)
        self.assertEqual(finding["analysis_cycle"], 96)
        self.assertEqual(finding["freshness"], "historical_reanalysis")
        self.assertNotIn("evidence_id", finding)
        self.assertNotIn("artifact_pointers", finding)

    def test_cycle_synthesis_uses_scientific_access_and_reporter_drops_non_adjudicable_evidence(self):
        manifest = {"path": "/tmp/evidence.json", "payload": {"records": [
            {"evidence_id": "E-good", "lifecycle_status": "active", "adjudicable": True,
             "summary": "decisive", "criterion_ids": ["M-S1"]},
            {"evidence_id": "E-audit", "lifecycle_status": "active", "adjudicable": False,
             "summary": "audit only", "criterion_ids": ["M-S1"]},
        ]}}
        lead = stage_context("research_lead", "cycle_synthesis", {
            "evidence_manifest": manifest, "active_hypothesis": {"id": "H"},
            "judge_decision": {"verdict": "supported"},
            "scientific_model_path": "/tmp/scientific_model.json",
            "cycle_dir": "/tmp/cycle-1",
        })
        self.assertNotIn("active_evidence_refs", lead)
        self.assertNotIn("evidence_scope_digest", lead)
        self.assertEqual(lead["scientific_access"], {
            "scientific_model_path": "/tmp/scientific_model.json",
            "evidence_manifest_path": "/tmp/evidence.json",
            "cycle_dir": "/tmp/cycle-1",
        })

        reporter = stage_context("reporter", "report", {
            "evidence_manifest": manifest, "evidence_refs": ["E-good", "E-audit"],
        })
        self.assertNotIn("evidence_refs", reporter)
        self.assertEqual([item["evidence_id"] for item in reporter["evidence_scope_digest"]], ["E-good"])

    def test_reporter_persists_only_adjudicable_evidence_in_sqlite_outcome(self):
        from theory_master.science_graph_store import ScienceGraphStore

        class DummyReporter(ReportingMixin):
            def __init__(self, root: Path):
                self.cycle_dir = root / "1"
                self.cycle_dir.mkdir()
                (self.cycle_dir / "checkpoints").mkdir()
                self.notes_dir = root / "notes"
                self.branch_canvas = None
                self.database = root / "science_graph.sqlite3"
                self.spec = SimpleNamespace(
                    notes_mirror_dir=None, document=root / "research_plan.md", goal_id="g",
                    branch_canvas=root / "exploration.canvas",
                )
                self.calls = []
                with ScienceGraphStore(self.database) as store:
                    store.add_hypothesis("H-0001", title="H", summary="Claim")
                    store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")

            def _graph_database_path(self):
                return self.database

            def _evidence_context(self):
                return {"evidence_manifest": {"payload": {"records": [
                    {"evidence_id": "E-good", "lifecycle_status": "active", "adjudicable": True,
                     "summary": "decisive"},
                    {"evidence_id": "E-audit", "lifecycle_status": "active", "adjudicable": False,
                     "summary": "audit only"},
                ]}}}

            def _call(self, state, role, context, **kwargs):
                self.calls.append(context)
                return {"title": "Report", "summary": "Scoped result"}

            def _path_anchors(self):
                return {"repo_root": str(root)}

            def _emit(self, *args, **kwargs):
                return None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reporter = DummyReporter(root)
            state = {
                "role_outputs": {}, "scientific_model_version_in": 1, "scientific_model_version_out": 2,
                "last_seq": 1, "scientific_synthesis": {},
                "hypothesis_lock": {"id": "H-0001", "decisive_test": "Test the bounded prediction"},
            }
            branch = {"id": "1", "title": "H", "hypothesis": "h", "hypothesis_ref": "H-0001",
                      "decisive_test": "Test the bounded prediction", "evidence_refs": ["E-good", "E-audit"]}
            judge = {"verdict": "supported", "rationale": "r"}
            reporter._write_cycle_report(state, branch, judge, "H", "h", "supported")

            self.assertEqual(len(reporter.calls), 1)
            self.assertEqual(reporter.calls[0]["evidence_refs"], ["E-good"])
            self.assertEqual(state["cycle_outcome_record"]["evidence_refs"], ["E-good"])
            self.assertEqual(state["cycle_outcome_record"]["id"], "cycle-outcome-1")

    def test_legacy_json_object_matches_canonical_normalizer_on_shared_repairs(self):
        cases = [
            '{"a":1,"b":[2]}',
            r'{"hypothesis":"use \\delta_g and C:\\temp\\x","title":"ok"}',
            (
                '{"mode":"cycle_end","scientific_synthesis":{"learned":[]}},'
                '"evolution_candidates":[{"id":"c1"}],"next_frontier_candidate_id":"c1"}'
            ),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(json_object(raw), normalize_agent_output(raw).value)


class ReporterPresentationTest(unittest.TestCase):
    def test_large_report_prompt_retains_verified_manifest_and_editorial_sources(self):
        from theory_master.prompt_stages import build_initial_prompt
        from theory_master.report_materials import REPORT_PROMPT_MATERIAL_LIMIT, compact_report_materials

        def asset(relative, kind="document"):
            return {"reference": "/tmp/report/" + relative, "relative_path": relative,
                    "kind": kind, "status": "available", "sha256": "d" * 64}

        arrays = [asset(f"replicates/{index}/原始数据.json") for index in range(4013)]
        figure = {**asset("图.svg", "figure"), "image_ref": "/tmp/report/%E5%9B%BE.svg"}
        summary = asset("summary.md")
        script = asset("run.py", "script")
        group = {"evidence_batch_id": "E-batch", "evidence_ids": ["E-1"], "relation": "same_invocation",
                 "producer_invocation": {"invocation_id": "inv-6", "role": "investigator"},
                 "manifest_path": "/task/research/6/invocation_artifacts/inv-6.json", "manifest_sha256": "a" * 64,
                 "assets": [*arrays, figure, summary, script],
                 "integrity": {"status": "verified", "checked_hashes": {row["reference"]: "d" * 64 for row in arrays}}}
        materials = [group]
        compact = compact_report_materials(materials)
        entry = compact[0]
        self.assertEqual(entry["manifest_path"], group["manifest_path"])
        self.assertEqual(entry["manifest_sha256"], group["manifest_sha256"])
        self.assertEqual(entry["asset_count"], len(group["assets"]))
        self.assertEqual(entry["omitted_asset_count"], len(group["assets"]) - len(entry["assets"]))
        self.assertTrue(all(item in entry["assets"] for item in (figure, summary, script)))
        self.assertEqual(entry["integrity"]["checked_hashes_count"], len(arrays))
        self.assertIn("checked_hashes", group["integrity"])
        self.assertEqual(len(group["assets"]), len(arrays) + 3)
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False).encode()), REPORT_PROMPT_MATERIAL_LIMIT)

        from theory_master.report_material_tools import prepare_catalog
        with tempfile.TemporaryDirectory() as temp:
            catalog = prepare_catalog(Path(temp) / "materials.json", materials)
            context = stage_context("reporter", "report", {
                "judge": {"verdict": "open", "rationale": "保留裁定"}, "report_material_catalog": catalog})
            self.assertIn(figure["image_ref"], Path(catalog["path"]).read_text())
        self.assertIn("command_prefix", context["report_materials"])
        self.assertIn("read", context["report_materials"]["operations"])
        self.assertEqual(context["judge_decision"]["rationale"], "保留裁定")
        contract = role_contract("reporter", phase="report")
        prompt = build_initial_prompt(prefix="Cycle 6", role="reporter", stage="report",
                                      contract_ref=contract.contract_id, contract_schema=contract.prompt_projection(),
                                      context=context, write_rule="No file writes.")
        self.assertLess(len(prompt), 1_048_576)
        self.assertIn("report_material_tools", prompt)

        many_batches = [{**group, "assets": [summary], "evidence_batch_id": f"E-{index}"}
                        for index in range(160)]
        packed = compact_report_materials(many_batches)
        self.assertLessEqual(len(json.dumps(packed, ensure_ascii=False).encode()), REPORT_PROMPT_MATERIAL_LIMIT)
        self.assertEqual(packed[-1]["omitted_material_row_count"], len(many_batches) - len(packed) + 1)

    def test_registered_materials_preserve_eligibility_and_reporter_only_access(self):
        from theory_master.report_materials import report_materials
        from theory_master.prompt_stages import build_initial_prompt, build_continuation_prompt
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            figure = root / "图 (a).svg"
            figure.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            table = root / "full.csv"
            table.write_text("private_raw_array,87654321\n")
            context = {"cycle_workspace": str(root), "evidence_manifest": {"records": [
                {"evidence_id": "E-good", "adjudicable": True,
                 "summary": "A: 1 ms; B: 2 ms; uncertainty is SEM",
                 "artifact_pointers": [str(figure), str(table)],
                 "artifact_roles": [{"path": str(table), "role": "result"}]},
                {"evidence_id": "E-excluded", "adjudicable": False,
                 "artifact_pointers": ["secret.svg"]},
            ]}}
            projected = stage_context("reporter", "report", context)
            assets = report_materials(context)
            self.assertEqual(projected["report_materials"]["overview"]["total_files"], 2)
            self.assertNotIn("assets", projected["report_materials"])
            self.assertEqual([row["evidence_id"] for row in assets], ["E-good"])
            self.assertEqual([a["kind"] for a in assets[0]["assets"]], ["figure", "table"])
            self.assertEqual(assets[0]["assets"][0]["status"], "available")
            self.assertIn("%28a%29.svg", assets[0]["assets"][0]["image_ref"])
            self.assertNotIn("87654321", json.dumps(projected))
            self.assertNotIn("secret.svg", json.dumps(projected))
            self.assertNotIn("report_materials", stage_context("judge", "terminal_assessment", context))
            self.assertNotIn("report_materials", stage_context("research_lead", "cycle_synthesis", context))
            contract = role_contract("reporter", phase="report")
            shared = dict(role="reporter", stage="report", contract_ref=contract.contract_id,
                          contract_schema=contract.prompt_projection(), context=projected)
            for prompt in (build_initial_prompt(prefix="Test", write_rule="No file writes.", **shared),
                           build_continuation_prompt(**shared)):
                self.assertIn('"report_materials"', prompt)
                self.assertIn("Do not write a reproduction index", prompt)
                self.assertIn("three ordered sections", prompt)
                self.assertIn("do not write a second conclusion summary", prompt)
                self.assertNotIn(assets[0]["assets"][0]["image_ref"], prompt)
                self.assertIn("progressive disclosure", prompt)

    def test_missing_ambiguous_and_external_materials_are_explicit(self):
        from theory_master.report_materials import report_materials, normalize_report_images
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second = root / "a", root / "b"
            first.mkdir(); second.mkdir()
            for folder in (first, second):
                (folder / "same.svg").write_text("different image")
            context = {"cycle_workspace": str(first), "research_root": str(second),
                       "evidence_manifest": {"records": [{"adjudicable": True,
                           "evidence_id": "E-one", "artifact_pointers": [
                               "same.svg", "missing.png", "https://example.org/image.png",
                               "https://example.org/image%20(a).png"]}]}}
            materials = report_materials(context)
            self.assertEqual([a["status"] for a in materials[0]["assets"]],
                             ["ambiguous", "missing", "external", "external"])
            body, warnings = normalize_report_images(
                "![Missing](missing.png) ![[same.svg|480]] ![External](https://example.org/image.png) "
                "![Encoded](https://example.org/image%20(a).png) "
                "![Invented](not-registered.svg)", materials, context)
            self.assertNotIn("![Missing]", body)
            self.assertNotIn("![[same.svg", body)
            self.assertIn("![External](https://example.org/image.png)", body)
            self.assertIn("![Encoded](https://example.org/image%20%28a%29.png)", body)
            self.assertEqual(warnings, ["missing.png", "same.svg", "not-registered.svg"])

    def test_image_paths_relocate_without_rewriting_prose_code_or_files(self):
        from theory_master.report_materials import report_materials, normalize_report_images
        from urllib.parse import unquote
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            figure = root / "图 (a).svg"
            figure.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            context = {"cycle_workspace": str(root), "evidence_manifest": {"records": [
                {"evidence_id": "E-1", "adjudicable": True, "artifact_pointers": [str(figure)]}]}}
            materials = report_materials(context)
            ref = materials[0]["assets"][0]["image_ref"]
            code = '\n```md\n![Example](unregistered.svg)\n```\n`![[literal.svg]]`'
            body = f'Bounded result.\n![Curve]({ref} "Title")\n![[图 (a).svg|480]]' + code
            stable, warnings = normalize_report_images(body, materials, context)
            self.assertEqual(warnings, [])
            self.assertIn(code, stable)
            self.assertIn(f'![[{figure}|480]]', stable)
            note_body, warnings = normalize_report_images(stable, materials, context, note_dir=root / "notes")
            self.assertIn('![Curve](../', note_body)
            self.assertIn('![[../图 (a).svg|480]]', note_body)
            self.assertIn('../图 (a).svg "Title")', unquote(note_body))
            self.assertIn(code, note_body)
            self.assertEqual(warnings, [])
            self.assertEqual(figure.read_text(), '<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_saved_numerical_theoretical_and_missing_asset_reports(self):
        from theory_master.science_graph_store import ScienceGraphStore
        from theory_master.report_materials import report_materials
        from urllib.parse import unquote

        class Reporter(ReportingMixin):
            def _graph_database_path(self):
                return self.database

            def _evidence_context(self):
                return self.evidence_context

            def _path_anchors(self):
                return {"repo_root": str(self.root), "task_home": str(self.root),
                        "cycle_workspace": str(self.cycle_dir / "workspace")}

            def _call(self, *args, **kwargs):
                raise AssertionError("The deterministic fixture must never invoke a model")

            def _emit(self, *args, **kwargs):
                pass

        for sample in ("numerical", "theoretical", "missing"):
            with self.subTest(sample=sample), tempfile.TemporaryDirectory() as temp:
                reporter = Reporter()
                reporter.root = root = Path(temp)
                reporter.cycle_dir = root / "research" / "1"
                workspace = reporter.cycle_dir / "workspace"
                workspace.mkdir(parents=True)
                (reporter.cycle_dir / "checkpoints").mkdir()
                reporter.notes_dir = root / "notes"
                reporter.branch_canvas = None
                reporter.spec = SimpleNamespace(notes_mirror_dir=None)
                reporter.database = root / "science_graph.sqlite3"
                with ScienceGraphStore(reporter.database) as store:
                    store.add_hypothesis("H-0001", title="Frozen claim")
                    store.register_cycle_hypothesis(cycle_id="1", hypothesis_id="H-0001")
                before_graph = reporter.database.read_bytes()
                figure = workspace / "对比 图.svg"
                figure.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"/>')
                table = workspace / "full.csv"
                table.write_text("raw_private,987654321\n")
                script = workspace / "analysis.py"
                script.write_text("# original analysis\n")
                evidence = {"evidence_id": "E-1", "adjudicable": True,
                            "summary": "A=1 ms, B=2 ms in the tested domain",
                            "scope": "tested domain", "limitations": "one supplied sample",
                            "artifact_pointers": [] if sample == "theoretical" else [str(figure), str(table), str(script)]}
                reporter.evidence_context = {"evidence_manifest": {"records": [evidence]}}
                materials = report_materials({**reporter._path_anchors(), **reporter.evidence_context})
                results = "The conditional derivation establishes the frozen bound."
                if sample != "theoretical":
                    results = f"![Comparison]({materials[0]['assets'][0]['image_ref']})\n\n" \
                              "图 1：比较 A 与 B 的耗时；结果来自 E-1，同一测试条件。\n\n" \
                              "|条件|耗时（ms）|\n|---|---|\n|A|1|\n|B|2|"
                if sample == "missing":
                    figure.unlink()
                body = "## 结论摘要\nResult within the tested domain.\n\n" \
                       "## 问题与必要条件\nSame supplied conditions.\n\n" \
                       f"## 主要结果\n{results}\n\n" \
                       "## 解释与限制\nOne supplied sample; no extrapolation."
                report = {"title": "Comparison of conditional results", "summary": body, "graph_summary": {
                    "conclusion": "Result within the tested domain.", "basis": "Supplied bounded evidence.",
                    "scope": "One supplied sample; no extrapolation.", "open_question": "",
                }}
                role_contract("reporter", phase="report").validate(report, {})
                state = {"role_outputs": {"reporter": report}, "last_seq": 1,
                         "scientific_model_version_in": 1, "scientific_model_version_out": 2,
                         "scientific_synthesis": {"conclusions": ["bounded result"]}}
                branch = {"title": "Frozen claim", "hypothesis_ref": "H-0001", "decisive_test": "Frozen test"}
                reporter._write_cycle_report(state, branch, {"verdict": "supported", "rationale": "Bounded evidence"},
                                             "Frozen claim", "Frozen statement", "supported")
                note = Path(branch["note_path"]).read_text()
                headings = ["## 结论摘要", "## 问题与必要条件", "## 主要结果", "## 解释与限制", "## 复现索引"]
                self.assertEqual([note.index(h) for h in headings], sorted(note.index(h) for h in headings))
                self.assertEqual(note.count("## 复现索引"), 1)
                self.assertEqual(reporter.database.read_bytes(), before_graph)
                self.assertEqual(state["cycle_outcome_record"]["verdict"], "supported")
                self.assertEqual(state["cycle_outcome_record"]["summary"], branch["summary"])
                self.assertEqual(state["role_outputs"]["reporter"]["summary"], body)
                self.assertNotIn("987654321", note)
                self.assertNotIn("## 复现索引", state["cycle_outcome_record"]["summary"])
                if sample == "numerical":
                    self.assertIn("![Comparison](../../research/1/workspace/", note)
                    self.assertIn("图 1：", note)
                    self.assertIn("|A|1|", note)
                    self.assertIn(str(figure), unquote(report["summary"]))
                    self.assertIn(str(table), note)
                    self.assertIn(str(script), note)
                elif sample == "theoretical":
                    self.assertNotIn("![", note)
                    self.assertNotIn("|---|", note)
                else:
                    self.assertNotIn("![Comparison]", note)
                    self.assertIn("文件缺失", note)
                    self.assertIn("图片引用不可用", note)


if __name__ == "__main__":
    unittest.main()
