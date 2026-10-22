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


class RefactorGuardrailsTest(unittest.TestCase):
    """Characterization tests for behavior-preserving module decomposition."""

    def test_stage_registry_and_stage_cards_are_the_same_runtime_surface(self):
        self.assertLessEqual(set(PHASE_CONTRACTS), set(STAGE_CARDS))
        for role_stage in sorted(PHASE_CONTRACTS):
            role, stage = role_stage
            with self.subTest(role=role, stage=stage):
                card = STAGE_CARDS[role_stage]
                self.assertEqual(set(card), {"objective", "may_do", "must_not", "exit"})
                self.assertTrue(all(str(value).strip() for value in card.values()))

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
                    ("judge", "goal_completion"),
                }:
                    projection_context["agent_graph"] = {
                        "schema": "theorymaster/test-agent-graph/v1",
                        "hypotheses": [], "current_frontiers": [], "lineage": [], "external_sources": [],
                    }
                projected = stage_context(role, stage, projection_context)
                self.assertIsInstance(projected, dict)
                json.dumps(projected, ensure_ascii=False)
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


if __name__ == "__main__":
    unittest.main()
