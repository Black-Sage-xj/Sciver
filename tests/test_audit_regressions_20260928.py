from __future__ import annotations

import unittest
from pathlib import Path

from theory_master.prompt_stages import stage_context
from theory_master.scholarly_transport import _query_variants, _targeted_relevance
from theory_master.workflow import Workflow


class AuditRegression20260928Test(unittest.TestCase):
    def test_preparation_only_candidate_guard_is_now_a_lead_invariant(self):
        from theory_master.stage_registry import stage_card_text
        card = stage_card_text("research_lead", "frontier_proposal")
        self.assertIn("Do not create filler or preparation-only candidates", card)
        self.assertIn("uncertain scientific prerequisites remain Investigator work", card)
        self.assertFalse(hasattr(Workflow, "_guard_local_source_only_candidate"))
        self.assertFalse(hasattr(Workflow, "_guard_terminal_existing_review_replay"))

    def test_gap_prose_cannot_authorize_same_role_ping_pong(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True, "evidence_kind": "numerical", "producer_role": "investigator"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_new_numerical_gap_remains_in_findings_not_router(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True, "evidence_kind": "numerical", "producer_role": "investigator"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_repeated_gap_does_not_select_another_investigator_call(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True, "evidence_kind": "numerical", "producer_role": "investigator"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_external_artifact_unavailable_cannot_hide_adjudicable_evidence(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True, "evidence_kind": "numerical", "producer_role": "investigator"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_round_count_cannot_override_judge_evidence_authority(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when criterion-bound active Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True, "evidence_kind": "numerical", "producer_role": "investigator"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_incomplete_evidence_cannot_be_adjudicable_without_scientific_kind(self):
        # Incomplete qualification metadata must not masquerade as Evidence.
        packet = {"evidence_manifest": {"payload": {"records": [{
            "evidence_id": "E-incomplete", "criterion_ids": ["C-1"],
            "lifecycle_status": "active", "adjudicable": True,
        }]}}}
        self.assertFalse(Workflow._adjudicable_judge_records(packet))

    def test_cross_role_handoff_does_not_truncate_scientific_delta(self):
        statement = "finding " + ("detail " * 240) + " F-END"
        scope = "scope " + ("detail " * 120) + " S-END"
        limitations = "limitations " + ("detail " * 120) + " L-END"
        reason = "reason " + ("detail " * 140) + " R-END"
        gap = "gap " + ("detail " * 200) + " G-END"
        record = {"outputs": [{
            "role": "investigator", "round": 2, "invocation_id": "inv-t",
            "output": {
                "evidence": [{"evidence_id": "E-T1", "summary": statement, "scope": scope, "limitations": limitations}],
                "criterion_updates": {"M-S1": {"status": "unresolved", "reason": reason}},
                "objection_updates": {"J-1": {"status": "open", "reason": reason}},
                "remaining_gap": {"kind": "numerical", "description": gap},
            },
        }]}
        delta = Workflow._previous_scientific_delta(record)
        self.assertEqual(delta["findings"][0]["statement"], statement)
        self.assertEqual(delta["findings"][0]["scope"], scope)
        self.assertEqual(delta["findings"][0]["limitations"], limitations)
        self.assertNotIn("remaining_scientific_question", delta)
        self.assertNotIn("criterion_updates", delta)
        self.assertNotIn("objection_updates", delta)
        # Old gap/routing metadata is no longer a scientific handoff field.
        self.assertEqual(delta["findings"][0]["limitations"], limitations)

    def test_cross_role_handoff_summarizes_previous_scientific_delta(self):
        record = {"outputs": [{
            "role": "investigator", "round": 2, "invocation_id": "inv-t",
            "output": {
                "evidence": [{
                    "evidence_id": "E-T1", "summary": "scaffold convention is fixed",
                    "scope": "benchmark", "limitations": "operational convention",
                    "artifact_pointers": ["analysis/convention.md"],
                }],
                "criterion_updates": {"M-S1": {"status": "unresolved", "reason": "identity still open"}},
                "objection_updates": {},
                "remaining_gap": {"kind": "numerical", "description": "verify row identity"},
            },
        }]}
        delta = Workflow._previous_scientific_delta(record)
        self.assertEqual(set(delta), {"findings"})
        self.assertEqual(delta["findings"][0]["statement"], "scaffold convention is fixed")
        self.assertEqual(delta["findings"][0]["limitations"], "operational convention")
        self.assertNotIn("executor_role", delta)
        self.assertNotIn("scientific_artifacts", delta)
        self.assertNotIn("artifact_pointers", delta["findings"][0])
        self.assertNotIn("evidence_id", delta["findings"][0])

    def test_cross_role_delta_keeps_all_findings_from_previous_turn(self):
        evidence = [{
            "evidence_id": f"E-{i}", "summary": f"finding-{i}", "scope": "scope", "limitations": "limit"
        } for i in range(7)]
        record = {"outputs": [{
            "role": "numerical", "round": 3, "invocation_id": "inv-n",
            "output": {"evidence": evidence, "criterion_updates": {}, "objection_updates": {},
                       "remaining_gap": {"kind": "theoretical", "description": "derive closure"}},
        }]}
        delta = Workflow._previous_scientific_delta(record)
        self.assertEqual([row["statement"] for row in delta["findings"]], [f"finding-{i}" for i in range(7)])
        self.assertTrue(all("evidence_id" not in row for row in delta["findings"]))

    def test_repeated_external_gap_requires_new_search_lead(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            workflow = Workflow.__new__(Workflow)
            workflow.spec = SimpleNamespace(research_root=Path(temp))
            workflow.cycle_dir = None
            state = {}
            task = {
                "gap_id": "gap-x",
                "source_gap_id": "gap-x",
                "task_type": "targeted_source",
                "search_target": "source X",
                "why_external_needed": "not local",
                "expected_external_evidence": "primary source",
                "new_search_lead": "same DOI hint",
                "triggered_by": "investigator",
            }
            queued, _ = workflow._queue_literature_task(state, task)
            self.assertTrue(queued)
            state["literature_task_history"][0]["status"] = "completed"
            queued, reason = workflow._queue_literature_task(state, task)
            self.assertFalse(queued)
            self.assertEqual(reason, "duplicate_external_gap_without_new_search_lead")
            task["new_search_lead"] = "new author hint"
            queued, _ = workflow._queue_literature_task(state, task)
            self.assertTrue(queued)

    def test_cross_candidate_er_dag_is_not_part_of_current_qualification(self):
        from theory_master.contracts import role_contract
        self.assertFalse(hasattr(Workflow, "_guard_unfolded_local_prerequisite"))
        contract = role_contract("research_lead", phase="frontier_proposal")
        bad = {"candidates": [{
            "hypothesis": "H",
            "why_now": "now",
            "goal_advance": "advance",
            "decisive_test": "test H",
            "evidence_requirements": [{"kind": "local_source_review", "requirement": "old prerequisite"}],
        }]}
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            contract.validate(bad, {})

    def test_sqlite_cycle_history_preserves_evidence_refs_across_outcome_transition(self):
        import tempfile
        from theory_master.science_graph_store import ScienceGraphStore
        with tempfile.TemporaryDirectory() as temp:
            with ScienceGraphStore(Path(temp) / "science_graph.sqlite3") as store:
                store.add_hypothesis("H-0001", title="Frozen claim", summary="Bounded claim")
                store.append_outcome(
                    outcome_id="cycle-1-outcome", hypothesis_id="H-0001", cycle_id="1",
                    verdict="open", summary="Initial scoped result", evidence_refs=["E-old"],
                )
                store.append_outcome(
                    outcome_id="cycle-2-outcome", hypothesis_id="H-0001", cycle_id="2",
                    verdict="supported", summary="Later scoped result", evidence_refs=["E-new"],
                )
                detail = store.agent_hypothesis_detail("H-0001", recent_outcomes=5)
                by_cycle = {str(row["cycle_id"]): row for row in detail["cycle_outcomes"]}
                self.assertEqual(by_cycle["1"]["evidence_refs"], ["E-old"])
                self.assertEqual(by_cycle["2"]["evidence_refs"], ["E-new"])
                self.assertEqual(detail["whole_h_assessment"]["verdict"], "supported")

    def test_frontier_proposal_receives_compact_model_not_full_authoring_view(self):
        model = {
            "version": 42,
            "current_scientific_picture": "current picture",
            "established_knowledge": [
                {"id": f"k-{i}", "statement": "x" * 200}
                for i in range(10)
            ],
        }
        projected = stage_context(
            "research_lead",
            "frontier_proposal",
            {
                "scientific_model": model,
                "frontier_state": {"selected_candidate_id": "next"},
                "research_reading": {"path": "/tmp/reading.md"},
                "agent_graph": {"nodes": [], "edges": [], "external_edges": []},
            },
        )
        self.assertNotIn("scientific_model", projected)
        self.assertNotIn("frontier_scientific_context", projected)
        summary = projected["scientific_model_summary"]
        self.assertEqual(summary, {"version": 42, "summary": ""})

    def test_targeted_source_query_expands_domain_and_rejects_surname_only_false_positive(self):
        variants = _query_variants(
            "targeted_source",
            "scanlan_case_periodic_membership_criterion",
            "Scanlan–Case phantom network theory 中 elastically effective chains 的原始定义",
            "trace citations to the Scanlan–Case original source",
        )
        self.assertIn("polymer", variants[0].lower())
        focus = " ".join(variants[:2])
        name_source = "Scanlan–Case phantom network theory elastically effective chains"
        relevant = {"title": "Elastically Effective Chains in a Phantom Polymer Network", "authors": ["J. Scanlan"], "abstract": "polymer network chains and phantom elasticity"}
        false_positive = {"title": "Matching Network Design Studies for Microwave Transistor Amplifiers", "authors": ["S. O. Scanlan"], "abstract": "microwave matching network"}
        self.assertGreater(
            _targeted_relevance(relevant, focus, name_source=name_source),
            _targeted_relevance(false_positive, focus, name_source=name_source),
        )

    def test_targeted_source_query_inherits_scientific_domain_context(self):
        variants = _query_variants(
            "targeted_source",
            "dili-cluster-provenance",
            "What scientific procedure produced cluster and should it define validation groups?",
            "",
            "Drug-Induced Liver Injury DILI molecular descriptors KNN standardised_smiles compound identity",
        )
        self.assertIn("dili", variants[0].lower())
        self.assertIn("molecular", variants[0].lower())
        self.assertLessEqual(len(variants), 2)
        self.assertTrue(all("dili" in value.lower() for value in variants))
        specific = _query_variants(
            "targeted_source",
            "dili-prediction-row-provenance",
            "Map all 55 test.csv rows to benchmark compound records and labeled partitions",
            "",
            "DILI KNN myname_predictions_no1_TEMPLATE.txt mol_descriptors_ambiguous.csv MCNC MCLCNC test.csv models_MD.py",
        )
        self.assertIn("myname_predictions_no1_template.txt", specific[0].lower())
        self.assertIn("mol_descriptors_ambiguous.csv", specific[0].lower())
        self.assertIn("mcnc", specific[0].lower())
        polluted = _query_variants(
            "targeted_source",
            "dili-prediction-row-provenance",
            "Map prediction rows to benchmark records",
            "",
            "local_source_review_ER2.json local_source_review_ER3.json myname_predictions_no1_TEMPLATE.txt mol_descriptors_ambiguous.csv DILI MCNC",
        )
        self.assertNotIn("local_source_review_er2.json", polluted[0].lower())
        self.assertNotIn("local_source_review_er3.json", polluted[0].lower())
        self.assertIn("myname_predictions_no1_template.txt", polluted[0].lower())
        self.assertIn("mol_descriptors_ambiguous.csv", polluted[0].lower())
        focus = " ".join(variants[:2])
        relevant = {
            "title": "Molecular descriptor validation for drug-induced liver injury prediction",
            "authors": ["A. Chemist"],
            "abstract": "DILI molecular descriptors compound identity grouping and cross-validation",
        }
        unrelated = {
            "title": "What Holds a Regional Cluster Together in its Inmost Folds?",
            "authors": ["A. Economist"],
            "abstract": "regional industrial cluster identity and organization",
        }
        self.assertGreater(
            _targeted_relevance(relevant, focus, name_source=variants[0]),
            _targeted_relevance(unrelated, focus, name_source=variants[0]),
        )

    def test_frontier_handoff_uses_sqlite_graph_not_preselected_reading_packet(self):
        projected = stage_context(
            "research_lead",
            "frontier_proposal",
            {
                "goal": "goal",
                "research_contract": "contract",
                "scientific_model": {"version": 1, "established_knowledge": [], "current_scientific_picture": "p"},
                "branch_canvas": "/tmp/full-branch.canvas",
                "agent_graph": {"schema": "theorymaster/test-agent-graph/v1", "hypotheses": [],
                                "current_frontiers": [], "lineage": [], "external_sources": []},
                "research_reading": {
                    "control_canvas": "/tmp/full-control.canvas",
                    "branch_canvas": "/tmp/full-branch.canvas",
                    "scientific_model": "/tmp/full-model.json",
                    "research_plan": "/tmp/research_plan.md",
                    "control_focus": {"status": "read", "nodes": [{"id": "goal", "text_excerpt": "G"}]},
                    "branch_focus": {"status": "read", "nodes": [{"id": "h", "text_excerpt": "H"}]},
                    "linked_reports": ["/tmp/specific-report.md"],
                },
            },
        )
        self.assertNotIn("research_reading", projected)
        self.assertEqual(projected["agent_graph"]["schema"], "theorymaster/test-agent-graph/v1")
        self.assertNotIn("canvas_access", projected)
        self.assertNotIn("branch_canvas", projected)
        blob = repr(projected)
        self.assertNotIn("/tmp/full-control.canvas", blob)
        self.assertNotIn("/tmp/full-model.json", blob)
        self.assertNotIn("/tmp/research_plan.md", blob)
        self.assertNotIn("/tmp/specific-report.md", blob)



if __name__ == "__main__":
    unittest.main()
