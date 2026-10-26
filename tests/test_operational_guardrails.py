from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from theory_master.backends import FakeBackend
from theory_master.prompt_stages import procedure_card_text, stage_card_text, stage_definition_text
from theory_master.skills import developer_instructions
from theory_master.scholarly_transport import retrieve_scholarly
from theory_master.specification import TaskSpec, latest_cycle_dir
from theory_master.workflow import Workflow


def make_spec(root: Path) -> TaskSpec:
    control = root / "control.canvas"
    control.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    plan = root / "research_plan.md"
    plan.write_text(
        '# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nA reproducible result exists.\n\n# Required Context\n\nr\n',
        encoding="utf-8",
    )
    return TaskSpec.load(plan)


class NoEvidenceLiteratureBackend(FakeBackend):
    def __init__(self):
        super().__init__(reworks=0)
        self.reporter_calls = 0

    def invoke(self, role, prompt, thread_id, model, provider):
        context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0] if "Context:\n" in prompt else {}
        thread = self.ensure_thread(role, thread_id, model, provider)
        if role == "research_lead" and context.get("mode") == "cycle_start" and context.get("phase") != "finalization":
            return thread, json.dumps({
                "candidates": [{
                    "hypothesis": "an authoritative external definition is available",
                    "why_now": "whether that definition exists is the current scientific question",
                    "goal_advance": "resolve the definition dependency before downstream measurement",
                    "decisive_test": "determine whether an authoritative scholarly definition can be verified",
                }],
            })
        if role == "investigator":
            # Modern Investigator must use only the minimal findings contract.
            # A scientific-source dependency must not force an old Router detour.
            return thread, json.dumps({"findings": []})
        if role == "literature":
            self._last_literature_prompt = prompt
            return thread, json.dumps({"evidence_records": []})
        if role == "reporter":
            self.reporter_calls += 1
        return super().invoke(role, prompt, thread_id, model, provider)


class OperationalGuardrailTest(unittest.TestCase):
    def test_evidence_gate_uses_current_manifest_criterion_ids(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_candidate_level_source_gap_inheritance_is_removed_from_qualification(self):
        from theory_master.contracts import role_contract
        self.assertFalse(hasattr(Workflow, "_apply_inherited_source_gap"))
        contract = role_contract("research_lead", phase="frontier_proposal")
        output = {"candidates": [{
            "hypothesis": "H",
            "why_now": "now",
            "goal_advance": "advance goal",
            "decisive_test": "observe discriminator",
            "evidence_requirements": [{"kind": "literature_targeted", "requirement": "old plan"}],
        }]}
        with self.assertRaisesRegex(ValueError, "SDK-owned"):
            contract.validate(output, {})


    def test_judge_readiness_does_not_require_semantic_criterion_binding(self):
        packet = {
            "evidence_manifest": {
                "payload": {
                    "records": [{
                        "evidence_id": "E-op",
                        "producer_invocation": {"role": "investigator"},
                        "evidence_kind": "theoretical",
                        "adjudicable": True,
                        "criterion_ids": [],
                        "lifecycle_status": "active",
                    }]
                }
            }
        }
        self.assertEqual(len(Workflow._adjudicable_judge_records(packet)), 1)
        packet["evidence_manifest"]["payload"]["records"][0]["adjudicable"] = False
        self.assertEqual(Workflow._adjudicable_judge_records(packet), [])

    def test_objection_linkage_retained_for_judge_without_router_status(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "objection_ids": ["J-1"], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)
        self.assertEqual(accepted[0]["objection_ids"], ["J-1"])


    def test_unrelated_evidence_cannot_claim_objection_resolved(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "objection_ids": [], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)
        self.assertEqual(accepted[0]["objection_ids"], [])


    def test_prior_attempt_provenance_cannot_claim_current_objection_resolved(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-prior", "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "objection_ids": ["J-1"], "producer_invocation": {"role": "investigator", "investigation_attempt": 1}, "lifecycle_status": "active"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)
        self.assertEqual(accepted[0]["producer_invocation"]["investigation_attempt"], 1)
        self.assertNotIn("response_evidence_recorded", accepted[0])


    def test_legacy_resolved_label_without_bound_evidence_does_not_suffice(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-legacy", "status": "resolved", "response_recorded": True, "criterion_ids": [], "lifecycle_status": "active"}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), False)
        self.assertEqual(rows[0]["status"], "resolved")


    def test_same_role_request_cannot_bypass_judge_evidence_gate(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_old_b_layer_unresolved_does_not_block_independent_judge(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_non_scientific_closure_reason_does_not_claim_evidence_is_absent(self):
        readiness = {"reason": "readiness_incomplete", "unanswered_objections": ["J-1"]}
        reason = Workflow._non_scientific_closure_reason(
            readiness, [{"evidence_id": "E-1", "criterion_ids": ["M-S1"]}],
        )
        self.assertIn("Evidence exists", reason)
        self.assertIn("Judge objections have no linked response finding", reason)
        self.assertNotIn("No criterion-bound", reason)

    def test_external_artifact_gap_with_evidence_preserves_judge_authority(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = [{"evidence_id": "E-current", "producer_invocation": {"role": "investigator"}, "evidence_kind": "theoretical", "criterion_ids": ["C-1"], "lifecycle_status": "active", "adjudicable": True}]
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), True)


    def test_external_artifact_without_evidence_never_fabricates_judge_input(self):
        # Legacy Router/gap decisions are retired. The SDK only selects the
        # independent Judge when technically valid scientific Evidence is present.
        self.assertFalse(hasattr(Workflow, "_route_investigation"))
        rows = []
        packet = {"evidence_manifest": {"payload": {"records": rows}}}
        accepted = Workflow._adjudicable_judge_records(packet)
        self.assertEqual(bool(accepted), False)
        self.assertFalse(accepted)


    def test_misclassified_pending_literature_artifact_is_reclassified_before_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow = Workflow(make_spec(Path(temp)), FakeBackend(reworks=0))
            emitted = []
            workflow._emit = lambda _state, kind, payload: emitted.append((kind, payload))
            task = {
                "gap_id": "gap-benchmark", "source_gap_id": "gap-benchmark", "task_type": "targeted_source",
                "search_target": "Does the external benchmark repository map test.csv rows to compounds?",
                "why_external_needed": "the remaining question requires external literature",
                "expected_external_evidence": "authoritative benchmark repository row mapping",
                "domain_context": "models_MD.py reads myname_predictions_no1_TEMPLATE.txt and mol_descriptors_ambiguous.csv",
                "status": "queued",
            }
            state = {
                "pending_literature_task": dict(task),
                "literature_task_history": [dict(task)],
                "recovery": {"action": "resume"},
            }
            queued, reason = workflow._validate_pending_literature_task(state)
            self.assertFalse(queued)
            self.assertEqual(reason, "external_artifact_source_unavailable")
            self.assertIsNone(state["pending_literature_task"])
            self.assertEqual(state["literature_task_history"][0]["status"], "reclassified_external_artifact")
            self.assertEqual(emitted[-1][0], "literature.task_reclassified_external_artifact")



    def test_sqlite_canvas_projection_contains_no_model_or_frontier_state_cards(self):
        from theory_master.science_canvas_export import canvas_projection
        from theory_master.science_graph_store import ScienceGraphStore
        with tempfile.TemporaryDirectory() as temp:
            with ScienceGraphStore(Path(temp) / "science_graph.sqlite3") as store:
                store.add_hypothesis("H-0001", title="Scientific origin", summary="Frozen scoped claim")
                projected = canvas_projection(store)
            ids = {node["id"] for node in projected["nodes"]}
            self.assertEqual(ids, {"H-0001"})
            self.assertFalse(any(str(node.get("id", "")).startswith("tm-") for node in projected["nodes"]))
            self.assertFalse(any(edge.get("fromNode", "").startswith("tm-") or
                                 edge.get("toNode", "").startswith("tm-") for edge in projected["edges"]))

    def test_host_scholarly_transport_ready_with_partial_connector_error(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                output_dir = Path(command[command.index("--output-dir") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                if command[1].endswith("lit_retrieve.py"):
                    (output_dir / "papers.json").write_text(json.dumps([{
                        "title": "Relevant paper",
                        "year": 2024,
                        "doi": "10.1000/test",
                        "url": "https://doi.org/10.1000/test",
                    }]), encoding="utf-8")
                    (output_dir / "errors.log").write_text("semanticscholar: HTTP 429\n", encoding="utf-8")
                else:
                    (output_dir / "reading_index.md").write_text("# reading\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            result = retrieve_scholarly({
                "source_gap_id": "gap-a",
                "task_type": "targeted_source",
                "search_target": "definition",
                "new_search_lead": "author name",
            }, workspace, runner=runner)
            self.assertTrue(result["transport_ok"])
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["paper_count"], 1)
            self.assertGreaterEqual(len(calls), 2)

    def test_source_lock_forces_exact_acquisition_instead_of_broad_rediscovery(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                output_dir = Path(command[command.index("--output-dir") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                if command[1].endswith("lit_retrieve.py"):
                    self.assertEqual(command[command.index("--query") + 1], "The effect of network flaws on the elastic properties of vulcanizates")
                    (output_dir / "papers.json").write_text(json.dumps([{
                        "title": "The effect of network flaws on the elastic properties of vulcanizates",
                        "authors": ["J. Scanlan"],
                        "year": 1960,
                        "doi": "10.1002/pol.1960.1204314219",
                        "url": "https://doi.org/10.1002/pol.1960.1204314219",
                        "is_open_access": False,
                    }]), encoding="utf-8")
                elif command[1].endswith("read_papers.py"):
                    self.assertEqual(command[command.index("--reading-selection") + 1], "manual")
                    self.assertEqual(command[command.index("--paper-ids") + 1], "10.1002/pol.1960.1204314219")
                    (output_dir / "reading_index.md").write_text("# reading\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            result = retrieve_scholarly({
                "source_gap_id": "scanlan_case_periodic_membership_criterion",
                "task_type": "targeted_source",
                "search_target": "do not broaden",
                "new_search_lead": "ignored once locked",
                "source_lock": {
                    "status": "locked",
                    "title": "The effect of network flaws on the elastic properties of vulcanizates",
                    "doi": "10.1002/pol.1960.1204314219",
                    "authors": ["J. Scanlan"],
                    "year": 1960,
                    "is_open_access": False,
                },
            }, workspace, runner=runner)
            self.assertEqual(result["discovery_mode"], "source_lock")
            self.assertEqual(result["query_variants"], ["The effect of network flaws on the elastic properties of vulcanizates"])
            papers = json.loads(Path(result["papers_json"]).read_text(encoding="utf-8"))
            self.assertEqual(papers[0]["doi"], "10.1002/pol.1960.1204314219")
            self.assertTrue(papers[0]["source_locked"])
            retrieve_calls = [cmd for cmd in calls if cmd[1].endswith("lit_retrieve.py")]
            self.assertEqual(len(retrieve_calls), 1)

    def test_literature_queue_inherits_locked_scientific_domain_context(self):
        with tempfile.TemporaryDirectory() as temp:
            workflow = Workflow(make_spec(Path(temp)), FakeBackend(reworks=0))
            state = {
                "hypothesis_lock": {
                    "hypothesis": "Drug-Induced Liver Injury DILI molecular descriptor grouping is source-defined",
                    "cycle_milestone": "establish compound identity and validation grouping",
                },
                "investigation_attempt": 1,
                "investigation_attempts": {"attempt-0001": {"outputs": [{
                    "role": "investigator",
                    "output": {
                        "evidence": [{
                            "summary": "Upstream predictions read myname_predictions_no1_TEMPLATE.txt and mol_descriptors_ambiguous.csv.",
                            "artifact_pointers": ["benchmark/public_upstream/models_MD.py"],
                        }],
                        "remaining_gap": {"kind": "literature_targeted", "description": "Trace MCNC and MCLCNC prediction-row provenance."},
                    },
                }]}},
                "literature_task_history": [],
            }
            queued, _ = workflow._queue_literature_task(state, {
                "gap_id": "gap-dili", "source_gap_id": "gap-dili", "task_type": "targeted_source",
                "search_target": "What procedure produced cluster?", "triggered_by": "investigator",
            })
            self.assertTrue(queued)
            task = state["pending_literature_task"]
            self.assertIn("Drug-Induced Liver Injury", task["domain_context"])
            self.assertIn("compound identity", task["domain_context"])
            self.assertIn("myname_predictions_no1_TEMPLATE.txt", task["domain_context"])
            self.assertIn("mol_descriptors_ambiguous.csv", task["domain_context"])
            self.assertIn("models_MD.py", task["domain_context"])

    def test_literature_domain_context_extracts_source_hints_from_small_json_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = Workflow(make_spec(root), FakeBackend(reworks=0))
            artifact = root / "local_source_review_ER1.json"
            artifact.write_text(json.dumps({
                "observations": [
                    "upstream loads mol_descriptors_ambiguous.csv and myname_predictions_no1_TEMPLATE.txt",
                    "MCNC and MCLCNC are named training partitions",
                ]
            }), encoding="utf-8")
            state = {
                "hypothesis_lock": {"hypothesis": "DILI prediction provenance", "cycle_milestone": "trace prediction rows"},
                "investigation_attempt": 1,
                "investigation_attempts": {"attempt-0001": {"outputs": [{
                    "role": "investigator",
                    "output": {
                        "evidence": [{"summary": "local audit found an upstream template", "artifact_pointers": [str(artifact)]}],
                        "remaining_gap": {"kind": "literature_targeted", "description": "find authoritative row mapping"},
                    },
                }]}},
            }
            context = workflow._literature_domain_context(state)
            self.assertIn("myname_predictions_no1_TEMPLATE.txt", context)
            self.assertIn("mol_descriptors_ambiguous.csv", context)
            self.assertIn("MCNC", context)
            self.assertIn("MCLCNC", context)

    def test_host_scholarly_transport_detects_all_connectors_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)

            def runner(command, **kwargs):
                output_dir = Path(command[command.index("--output-dir") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "papers.json").write_text("[]", encoding="utf-8")
                (output_dir / "errors.log").write_text(
                    "openalex: Operation not permitted\n"
                    "crossref: Operation not permitted\n"
                    "semanticscholar: Operation not permitted\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = retrieve_scholarly({
                "source_gap_id": "gap-b",
                "task_type": "targeted_source",
                "search_target": "definition",
            }, workspace, runner=runner)
            self.assertFalse(result["transport_ok"])
            self.assertEqual(result["status"], "blocked")

    def test_no_scientific_evidence_closes_without_judge_reporter_or_literature_detour(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = make_spec(root)
            backend = NoEvidenceLiteratureBackend()
            state = Workflow(spec, backend)._execute_cycle()
            self.assertEqual(state["status"], "over")
            self.assertEqual(state["assessment_state"], "not_adjudicated")
            self.assertEqual(state["closure_reason"], "no_scientific_evidence")
            self.assertEqual(backend.judges, 0)
            self.assertEqual(backend.reporter_calls, 0)
            self.assertEqual(state["scientific_model_version_in"], state["scientific_model_version_out"])
            cycle = latest_cycle_dir(spec.research_root, task_mode=spec.task_mode)
            self.assertIsNotNone(cycle)
            manifest = json.loads((cycle / "workspace" / "evidence_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["records"], [])
            event_types = [
                json.loads(line)["type"]
                for line in (cycle / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("operational.closure_recorded", event_types)
            self.assertNotIn("decision.recorded", event_types)
            self.assertFalse(hasattr(backend, "_last_literature_prompt"))
            self.assertNotIn("scholarly.transport_prepared", event_types)
            self.assertNotIn("scholarly.transport_reused", event_types)

    def test_frontier_proposal_prioritizes_remaining_goal_gap_over_open_science(self):
        lead = stage_definition_text("research_lead", "frontier_proposal")
        self.assertIn("consequential unmet Goal, Research Contract, Final Gate or necessary prerequisite", lead)
        self.assertNotIn("remaining_goal_gap", lead)
        self.assertNotIn("remaining_gap", lead)
        self.assertIn("Avoid umbrella programs, filler, preparation-only work", lead)
        self.assertIn("without treating their labels as commands", lead)
        self.assertNotIn("unresolved questions are the default frontier", lead)

    def test_agent_prompts_treat_injected_runtime_state_as_authoritative_without_tool_blacklists(self):
        lead = stage_card_text("research_lead", "frontier_proposal")
        challenger = procedure_card_text("challenger", "candidate_review")
        lead_identity = developer_instructions("research_lead")
        challenger_identity = developer_instructions("challenger")
        for text in (lead_identity, challenger_identity):
            self.assertIn("不要重新发现 SDK 已经提供的 workflow/runtime state", text)
            self.assertNotIn("rg --files", text)
            self.assertNotIn("git rev-parse", text)
        self.assertIn("SQLite 为唯一权威数据源", lead_identity)
        self.assertIn("科学图谱以 SQLite 为权威", challenger_identity)


if __name__ == "__main__":
    unittest.main()
