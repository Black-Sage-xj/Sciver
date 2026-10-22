from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.backends import FakeBackend
from theory_master.contracts import role_contract
from theory_master.output_validation import validate_agent_output, validate_references
from theory_master.prompt_stages import (build_continuation_prompt, build_contract_repair_prompt,
    build_initial_prompt, stage_context as _stage_context, stage_for)
from theory_master.scientific_model import commit_authored_model, empty_model
from theory_master.specification import TaskSpec
from theory_master.stage_registry import procedure_card_text, stage_card_text
from theory_master.workflow import RecoveryStop, Workflow


TEST_AGENT_GRAPH = {
    "schema": "theorymaster/test-agent-graph/v1",
    "hypotheses": [], "current_frontiers": [], "lineage": [], "external_sources": [],
}
_GRAPH_STAGES = {
    ("research_lead", "frontier_proposal"),
    ("research_lead", "frontier_maintenance"),
    ("challenger", "candidate_review"),
    ("judge", "goal_completion"),
}

def stage_context(role, stage, context, *, continuation=False):
    values = dict(context)
    if ((role, stage) in _GRAPH_STAGES
            and "agent_graph" not in values and "agent_graph_reuse" not in values):
        values["agent_graph"] = dict(TEST_AGENT_GRAPH)
    return _stage_context(role, stage, values, continuation=continuation)


def synthesis(*, claims=None):
    return {
        "scientific_synthesis": {
            "conclusions": ["terminal result"],
            "open_questions": [],
        },
        "scientific_model_update": {
            "action": "replace",
            "model": {
                "summary": "terminal result" if claims is None else claims,
            },
        },
    }


def valid_claim():
    return {"id": "claim-1", "statement": "result", "scope": "test", "evidence_refs": [],
            "limitations": "fixture", "status": "supported"}


class StagePromptAndRepairTest(unittest.TestCase):
    def test_rejected_qualification_resume_explicitly_replays_challenger_feedback(self):
        reason = "The proposed test cannot adjudicate the downstream claim."

        class RejectOnceBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.lead_contexts = []
                self.review_count = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                if role == "research_lead" and "Current task: frontier_proposal" in prompt:
                    self.lead_contexts.append(context)
                if role == "challenger":
                    self.review_count += 1
                    if self.review_count == 1:
                        thread = self.ensure_thread(role, thread_id, model, provider)
                        return thread, json.dumps({"reviews": [{"candidate_id": context["candidate"][0]["id"],
                                                                 "verdict": "reject", "reason": reason}]})
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 1  # Assert one Cycle's qualification/repair, not later Cycles.
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            spec = TaskSpec.from_config(spec.config_path)
            backend = RejectOnceBackend()
            workflow = Workflow(spec, backend)
            workflow.run()
            # A rejected round retries inside the same Proposal Gate, carrying
            # the Challenger review into the next Lead context.
            self.assertGreaterEqual(backend.review_count, 2)
            self.assertGreaterEqual(len(backend.lead_contexts), 2)
            self.assertNotIn("qualification_feedback", backend.lead_contexts[0])
            feedback = backend.lead_contexts[1]["qualification_feedback"]
            self.assertEqual(feedback["candidate_reviews"][0]["verdict"], "reject")
            self.assertEqual(feedback["candidate_reviews"][0]["reason"], reason)

    def test_retired_previous_qualification_feedback_alias_is_not_a_runtime_input(self):
        projected = stage_context("research_lead", "frontier_proposal", {
            "previous_qualification_feedback": {"reason": "retired alias"},
        })
        self.assertNotIn("qualification_feedback", projected)
        self.assertNotIn("previous_qualification_feedback", projected)

    def test_rework_projections_keep_only_role_relevant_feedback(self):
        objection = {"objection_id": "J-1", "reason": "missing network-level uncertainty"}
        context = {
            "hypothesis_lock": {"hypothesis": "H"},
            "judge_feedback": {"assessment_state": "insufficient_evidence"},
            "judge_objections": [objection],
            "literature_task": {"task_type": "targeted_source", "search_target": "source claim"},
        }
        investigator = stage_context("investigator", "judge_rework", context, continuation=True)
        self.assertEqual(investigator["judge_objections"], [objection])
        self.assertNotIn("judge_feedback", investigator)
        literature = stage_context("literature", "targeted_source", context, continuation=True)
        self.assertEqual(literature["search_target"], "source claim")
        self.assertNotIn("hypothesis_lock", literature)
        self.assertNotIn("literature_task", literature)
        self.assertNotIn("judge_objections", literature)
        self.assertNotIn("judge_feedback", literature)

    def test_lead_repair_uses_only_new_feedback_in_existing_thread(self):
        decisive = "full decisive test " * 500
        blocker = "full challenger objection " * 400
        context = {
            "required_context": "benchmark/datasets/train.csv\nbenchmark/datasets/test.csv",
            "candidates": [{"id": "c1", "hypothesis": "H", "decisive_test": decisive}],
            "qualification_error": {"reason": blocker, "blocking_issues": [
                {"id": "b1", "problem": blocker, "required_change": "revise the test"}]},
        }
        fresh = stage_context("research_lead", "selection_repair", context)
        resumed = stage_context("research_lead", "selection_repair", context, continuation=True)
        self.assertEqual(fresh["repair_options"][0]["decisive_test"], decisive)
        self.assertEqual(resumed["repair_options"][0]["candidate_id"], "c1")
        self.assertEqual(resumed["repair_options"][0]["blockers"][0]["problem"], blocker)
        self.assertNotIn(decisive, json.dumps(resumed))
        self.assertNotIn("required_context", fresh)
        self.assertNotIn("required_context", resumed)
        self.assertNotIn("candidate_reviews", resumed)
        self.assertNotIn("review_reason", resumed)
        self.assertNotIn("blocking_issues", resumed)

    def test_challenger_receives_complete_scientific_candidate_without_evidence_plan(self):
        long_text = "scientific statement " * 500
        candidate = {"hypothesis": long_text, "scope": "scope",
                     "why_now": long_text, "goal_advance": long_text, "decisive_test": long_text}
        challenger = stage_context("challenger", "candidate_review", {
            "candidate": [candidate], "canvas_focus": {}, "linked_reports": [],
        })
        projected = challenger["candidate"][0]
        self.assertEqual(projected["hypothesis"], long_text)
        self.assertEqual(projected["decisive_test"], long_text)
        self.assertNotIn("required_evidence", projected)
        self.assertNotIn("evidence_requirements", projected)

    def test_challenger_uses_sqlite_graph_without_hypothesis_identity_pointer(self):
        candidate = {
            "hypothesis": "same H with refined wording",
            "scope": "refined scope",
            "hypothesis_id": "H-0007",  # legacy recovered state must not leak this pointer into the Prompt
            "why_now": "new discriminant is executable",
            "goal_advance": "advance the same scientific proposition",
            "decisive_test": "DT-B",
        }
        projected = stage_context("challenger", "candidate_review", {
            "candidate": [candidate],
            "branch_canvas": "/tmp/exploration.canvas",
            "canvas_entry_node_id": "H-0007",
            "workspace_dir": "/tmp",
            "hypothesis_identity_context": [{"hypothesis_id": "H-0007", "hypothesis": "old copy"}],
        })
        self.assertNotIn("hypothesis_id", projected["candidate"][0])
        self.assertEqual(projected["agent_graph"]["schema"], TEST_AGENT_GRAPH["schema"])
        self.assertNotIn("canvas_access", projected)
        self.assertNotIn("hypothesis_identity_context", projected)

    def test_judge_receives_only_compact_investigation_result_projection(self):
        result = {
            "schema": "theory-master/investigation-result/v1", "investigation_attempt": 2, "rounds": 4,
            "findings": [{
                "statement": "bounded contradiction", "criterion_ids": ["M-F1"], "objection_ids": ["J-1"],
                "relation": "contradicts", "scope": "test",
            }],
        }
        projected = stage_context("judge", "terminal_assessment", {
            "hypothesis_lock": {
                "id": "H-0001", "hypothesis": "H", "scope": "test",
                "decisive_test": "bounded decisive test",
                "cycle_progress_criteria": {"failure": [{"id": "M-F1"}]},
            },
            "branch": {"id": "B-1", "hypothesis": "H", "cycle_progress_criteria": {"failure": [{"id": "M-F1"}]}},
            "investigation_result": result,
            "judge_packet": {"payload": {"records": ["legacy"]}},
            "evidence_manifest": {"payload": {"records": ["duplicate"]}},
            "coverage_matrix": {"payload": {"criteria": {"M-F1": ["E-1"]}}},
        })
        self.assertEqual(projected["investigation_result"], {"findings": [{
            "statement": "bounded contradiction", "scope": "test",
        }]})
        self.assertEqual(projected["hypothesis_lock"], {
            "hypothesis": "H", "scope": "test", "decisive_test": "bounded decisive test",
        })
        serialized = json.dumps(projected, ensure_ascii=False)
        for key in (
            "judge_packet", "evidence_manifest", "coverage_matrix", "branch_ref", "hypothesis_id",
            "cycle_progress_criteria", "criterion_ids", "objection_ids", "investigation_attempt", "rounds",
        ):
            self.assertNotIn(key, serialized)

    def test_reporter_projection_strips_legacy_synthesis_frontier_advice(self):
        synthesis_payload = {
            "learned": ["result"],
            "new_open_questions": ["scope remains open"],
            "next_frontier_recommendation": "unapproved advisory direction",
        }
        candidates = [
            {"id": "cand-a", "title": "A", "hypothesis": "unapproved A"},
            {"id": "cand-b", "title": "B", "hypothesis": "authorized B"},
        ]
        without_frontier = stage_context("reporter", "report", {
            "scientific_synthesis": synthesis_payload,
            "evolution_candidates": candidates,
            "authorized_next_frontier_candidate_id": None,
        })
        self.assertNotIn("next_frontier_recommendation", without_frontier["scientific_synthesis"])
        self.assertNotIn("evolution_candidates", without_frontier)
        self.assertNotIn("authorized_next_frontier_candidate_id", without_frontier)
        self.assertNotIn("authorized_frontier", without_frontier)

        with_frontier = stage_context("reporter", "report", {
            "scientific_synthesis": synthesis_payload,
            "evolution_candidates": candidates,
            "authorized_next_frontier_candidate_id": "cand-b",
        })
        self.assertNotIn("authorized_frontier", with_frontier)
        self.assertNotIn("unapproved A", json.dumps(with_frontier, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")

    def make(self, root: Path) -> tuple[TaskSpec, Workflow]:
        plan = root / "research_plan.md"
        control = root / "control.canvas"
        research = root / "research"
        control.write_text('{"nodes": [], "edges": []}', encoding="utf-8")
        plan.write_text(
            '# Goal\n\ng\n\n# Research Contract\n\nc\n\n# Final Gate\n\nA reproducible scientific result exists.\n\n# Required Context\n\n- r\n',
            encoding="utf-8",
        )
        spec = TaskSpec.load(plan)
        return spec, Workflow(spec, FakeBackend(reworks=0))

    def test_stage_router_uses_current_role_state_not_call_count(self):
        state = {"total_investigation_round_count": 999}
        self.assertEqual(stage_for("research_lead", state, {"mode": "cycle_start"}), "frontier_proposal")
        self.assertEqual(stage_for("research_lead", state, {"mode": "cycle_start", "phase": "finalization"}), "candidate_selection")
        self.assertEqual(stage_for("research_lead", state, {"mode": "cycle_start", "phase": "finalization", "qualification_error": {"code": "x"}}), "selection_repair")
        self.assertEqual(stage_for("research_lead", state, {"mode": "cycle_end"}), "cycle_synthesis")
        self.assertEqual(stage_for("investigator", state, {"round": 1}), "investigate")
        self.assertEqual(stage_for("investigator", state, {"round": 9}), "investigate")
        self.assertEqual(stage_for("investigator", state, {"round": 9, "judge_objections": [{"objection_id": "J1"}]}), "judge_rework")
        self.assertEqual(stage_for("literature", state, {"literature_task": {"task_type": "targeted_source"}}), "targeted_source")
        self.assertEqual(stage_for("literature", state, {"literature_task": {"task_type": "landscape"}}), "landscape")
        self.assertEqual(stage_for("literature", state, {"literature_task": {"task_type": "novelty_check"}}), "novelty_check")


    def test_challenger_review_must_exhaust_visible_blockers_before_handoff(self):
        stage = stage_card_text("challenger", "candidate_review")
        procedure = procedure_card_text("challenger", "candidate_review")
        self.assertIn("every visible independent blocker", stage)
        self.assertIn("Stop reading once the decision cannot change", procedure)
        # Output fields and verdict enums belong to the Contract, not the Stage Card or Procedure.
        self.assertNotIn("problem + required_change", stage)
        self.assertNotIn("required_change", stage)
        self.assertNotIn("qualified/revise/reject", procedure)
        self.assertNotIn("exact candidate_id", procedure)
        lead_procedure = procedure_card_text("research_lead", "frontier_proposal")
        self.assertIn("SQLite-backed research graph is your scientific working surface", lead_procedure)
        self.assertIn("older defining or adverse results", lead_procedure)
        self.assertIn("closest prior model or claim", lead_procedure)
        self.assertIn("one keyword miss or first page does not establish novelty", lead_procedure)
        self.assertIn("without replaying the whole archive", lead_procedure)
        self.assertIn("wording changes", lead_procedure.lower())
        lead_stage = stage_card_text("research_lead", "frontier_proposal")
        challenger_stage = stage_card_text("challenger", "candidate_review")
        self.assertIn("add discriminating information", lead_stage)
        self.assertIn("pure confidence strengthening", lead_stage)
        self.assertIn("unless a new source of uncertainty exists", lead_stage)
        self.assertIn("information beyond already terminal-adjudicated tests", challenger_stage)
        self.assertIn("adjudicability alone is not sufficient for qualification", challenger_stage)
        self.assertIn("not a qualification blocker merely because it has not yet been derived or operationalized", challenger_stage)
        self.assertIn("treat it as Investigation work rather than a revise blocker", challenger_stage)
        self.assertIn("Do not convert an Investigator-owned prerequisite", challenger_stage)


    def test_lead_scientific_copy_and_model_synthesis_preserve_scientific_priority(self):
        frontier = stage_card_text("research_lead", "frontier_proposal")
        synthesis = stage_card_text("research_lead", "cycle_synthesis")
        self.assertIn("Do not author Canvas display title or summary", frontier)
        display = stage_card_text("research_lead", "hypothesis_display")
        self.assertIn("selected and frozen Hypothesis", display)
        self.assertIn("Do not revise or reinterpret", display)
        self.assertIn("across relevant Cycles", synthesis)
        self.assertIn("Preserve older still-valid premises", synthesis)
        self.assertIn("2400-character validator limit is a ceiling", synthesis)
        self.assertIn("Never strengthen a claim beyond its adjudicated evidence", synthesis)
        # Presentation changes cannot add required scientific fields or invent a B stage.
        proposal = role_contract("research_lead", phase="frontier_proposal").prompt_schema
        candidate = proposal["candidates"][0]
        self.assertNotIn("canvas_title", candidate)
        self.assertNotIn("canvas_summary", candidate)
        self.assertIn("decisive_test", candidate)
        self.assertEqual(set(role_contract("research_lead", phase="hypothesis_display").prompt_schema),
                         {"canvas_title", "canvas_claim", "canvas_conditions"})
        self.assertNotIn("display_rewrite_stage", proposal)
        model_contract = role_contract("research_lead", phase="cycle_synthesis").prompt_schema
        self.assertEqual(set(model_contract), {"scientific_synthesis", "scientific_model_update", "pending_dispositions"})

    def test_local_test_and_whole_hypothesis_have_distinct_qualification_boundaries(self):
        hypothesis = "Preparation changes structure, then load transfer, then release energy."
        local_test = "This Cycle measure the necessary structure prediction before measuring energy."
        candidate = {"hypothesis": hypothesis, "scope": "causal chain",
                     "why_now": "structure is unknown",
                     "goal_advance": "test the first causal link", "decisive_test": local_test}
        projected = stage_context("challenger", "candidate_review", {
            "candidate": [candidate], "canvas_focus": {}, "linked_reports": [],
        })
        self.assertEqual(projected["candidate"][0]["hypothesis"], hypothesis)
        self.assertEqual(projected["candidate"][0]["decisive_test"], local_test)

        lead = stage_card_text("research_lead", "frontier_proposal")
        challenger = stage_card_text("challenger", "candidate_review")
        procedure = procedure_card_text("challenger", "candidate_review")
        judge = stage_card_text("judge", "terminal_assessment")
        self.assertIn("matching, contrary, or inconclusive result", lead)
        self.assertIn("A matching local result may advance the Cycle without supporting the whole hypothesis", lead)
        self.assertIn("a necessary local prediction is acceptable without settling the whole hypothesis", challenger)
        self.assertIn("Goal-unrelated", challenger)
        self.assertIn("otherwise require effect estimation with uncertainty instead of an invented cutoff", challenger)
        self.assertIn("every blocker is repairable inside the unchanged hypothesis/scope/estimand", challenger)
        self.assertNotIn("qualified/revise/reject", procedure)
        self.assertIn("Use milestone_advanced only when the current decisive_test is resolved", judge)
        self.assertIn("Do not promote a positive local result into whole-hypothesis support", judge)

    def test_investigator_prompt_bounds_unknown_size_content_without_format_specific_rules(self):
        for stage_name in ("investigate", "integrate_literature", "judge_rework"):
            card = stage_card_text("investigator", stage_name)
            self.assertIn("first inspect its scale, structure, and relevant boundaries", card)
            self.assertIn("Do not inject unbounded raw content into context", card)
            self.assertIn("compact reproducible summary", card)
            self.assertIn("explore local files and directories as needed", card)
            self.assertIn("follow new file paths or search", card)
            self.assertNotIn("source_manifest is the authoritative list", card)
            self.assertNotIn("head -3", card)
            self.assertNotIn("CSV", card)
        self.assertNotIn(
            "Investigator content boundary",
            stage_card_text("judge", "terminal_assessment"),
        )

    def test_investigator_prompt_routes_method_skills_before_execution_and_closes_generated_artifacts(self):
        for stage_name in ("investigate", "integrate_literature", "judge_rework"):
            card = stage_card_text("investigator", stage_name)
            self.assertIn("Before the first related tool execution, load numerical-investigation", card)
            self.assertIn("code execution, numerical calculation, statistical analysis, data checking", card)
            self.assertIn("Load theory-investigation only when", card)
            self.assertIn("nontrivial definitions, theoretical derivation, closure", card)
            self.assertNotIn("must cite the generated file(s) in artifacts", card)
            self.assertNotIn("do not return an empty artifacts list", card)
            self.assertNotIn("Filesystem readability is not Evidence authorization", card)
            self.assertNotIn("empty remaining_scientific_question asserts", card)
            self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", card)
            self.assertIn("findings[]", card)
        judge_card = stage_card_text("judge", "terminal_assessment")
        self.assertNotIn("Investigator skill routing", judge_card)
        self.assertNotIn("Investigator evidence closure", judge_card)

    def test_challenger_candidate_review_projects_only_prepared_seed(self):
        candidate = [{
            "hypothesis": "current H", "scope": "bounded scope", "why_now": "now",
            "goal_advance": "advance", "decisive_test": "test",
        }]
        projected = stage_context("challenger", "candidate_review", {
            "mode": "candidate_review",
            "candidate": candidate,
            "branch_canvas": "/tmp/exploration.canvas",
            "canvas_entry_node_id": "h-current",
            "task_home": "/tmp/task",
            "note_area": "/tmp/task",
            "research_root": "/tmp/task/.theory-master/research",
            "cycle_workspace": "/tmp/cycle/workspace",
            "repo_root": "/tmp/repo",
            "canvas_focus": {"status": "read", "nodes": [{"id": "focus"}]},
            "linked_reports": [{"path": "/tmp/report.md"}],
            "candidates": [{"id": "cand-1", "hypothesis": "runtime-only H"}],
            "required_context": "runtime-only required context",
            "source_manifest": {"files": ["forbidden"]},
            "evidence_manifest": {"records": ["forbidden"]},
            "coverage_matrix": {"criteria": ["forbidden"]},
            "implementation_details": {"secret": True},
            "workspace_artifact_list": ["/tmp/hidden"],
        })
        self.assertEqual(set(projected), {"candidate", "agent_graph", "workspace_access"})
        self.assertEqual(projected["candidate"], candidate)
        self.assertEqual(
            set(projected["candidate"][0]),
            {"hypothesis", "scope", "why_now", "goal_advance", "decisive_test"},
        )

        prompt = build_initial_prompt(
            prefix="", role="challenger", stage="candidate_review",
            contract_ref="test-contract", contract_schema="{}", context=projected,
            write_rule="Read-only.",
        )
        prompt_context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
        self.assertEqual(set(prompt_context), {"candidate", "agent_graph", "workspace_access"})
        for forbidden in ("source_manifest", "evidence_manifest", "coverage_matrix", "required_context"):
            self.assertNotIn(forbidden, prompt_context)

    def test_required_context_is_not_preinjected_into_qualification_agents(self):
        required = "- benchmark/instruction.md\n- dataset.csv"
        challenger = stage_context("challenger", "candidate_review", {
            "candidate": [{"hypothesis": "H"}],
            "canvas_focus": {},
            "linked_reports": [],
            "required_context": required,
        })
        lead = stage_context("research_lead", "frontier_proposal", {
            "goal": "g",
            "research_contract": "c",
            "final_gate": "finish when artifact is complete and aligned",
            "required_context": required,
            "branch_canvas": "/tmp/task/exploration.canvas",
            "canvas_entry_node_id": "h-current",
            "task_home": "/tmp/task",
            "note_area": "/tmp/task",
            "research_root": "/tmp/task/.theory-master/research",
            "cycle_workspace": "/tmp/cycle/workspace",
            "repo_root": "/tmp/repo",
        })
        repair = stage_context("research_lead", "selection_repair", {
            "required_context": required,
            "qualification_error": {"repair_attempt": 1, "repair_limit": 1},
        })
        self.assertNotIn("required_context", challenger)
        self.assertNotIn("required_context", lead)
        self.assertEqual(lead["final_gate"], "finish when artifact is complete and aligned")
        self.assertNotIn("required_context", repair)
        self.assertEqual(lead["agent_graph"]["schema"], TEST_AGENT_GRAPH["schema"])
        self.assertNotIn("canvas_access", lead)
        self.assertNotIn("source_manifest", challenger)
        self.assertNotIn("source_manifest", lead)
        self.assertNotIn("source_manifest", repair)

    def test_qualification_scientific_text_is_never_character_clipped(self):
        hypothesis = "H-start " + ("hypothesis-detail " * 120) + " H-END"
        decisive = "T-start " + ("procedure-detail " * 180) + " T-END"
        why_now = "W-start " + ("reason-detail " * 80) + " W-END"
        review_reason = "R-start " + ("review-detail " * 80) + " R-END"
        blocker_problem = "P-start " + ("problem-detail " * 70) + " P-END"
        blocker_change = "C-start " + ("change-detail " * 70) + " C-END"
        candidate = {
            "id": "cand-long", "title": "long candidate", "hypothesis": hypothesis,
            "why_now": why_now, "goal_advance": "advance the scientific goal",
            "decisive_test": decisive,
            "evidence_requirements": [{"id": "ER-1", "kind": "numerical_evidence", "requirement": "measure the decisive response"}],
            "status": "provisional",
        }
        seed = [{key: candidate[key] for key in ("hypothesis", "why_now", "goal_advance", "decisive_test")}]
        challenger = stage_context("challenger", "candidate_review", {
            "candidate": seed, "canvas_focus": {}, "linked_reports": [],
        })
        seen = challenger["candidate"][0]
        self.assertEqual(seen["hypothesis"], hypothesis)
        self.assertEqual(seen["decisive_test"], decisive)
        self.assertEqual(seen["why_now"], why_now)
        self.assertEqual(seen["goal_advance"], candidate["goal_advance"])
        self.assertNotIn("…[clipped]", json.dumps(seen, ensure_ascii=False))

        repair = stage_context("research_lead", "selection_repair", {
            "candidates": [candidate],
            "qualification_error": {
                "repair_attempt": 1, "repair_limit": 4,
                "reason": review_reason,
                "blocking_issues": [{"id": "b1", "problem": blocker_problem, "required_change": blocker_change}],
            },
        })
        option = repair["repair_options"][0]
        self.assertEqual(option["hypothesis"], hypothesis)
        self.assertEqual(option["decisive_test"], decisive)
        self.assertEqual(option["blockers"][0]["problem"], blocker_problem)
        self.assertEqual(option["blockers"][0]["required_change"], blocker_change)
        self.assertNotIn("review_reason", repair)
        self.assertNotIn("candidate_reviews", repair)
        self.assertNotIn("blocking_issues", repair)
        self.assertNotIn("…[clipped]", json.dumps(repair, ensure_ascii=False))

    def test_locked_scientific_authority_is_compact_by_fields_not_truncated_by_chars(self):
        hypothesis = "H " + ("full-hypothesis " * 180) + " H-END"
        criterion = "criterion " + ("detail " * 220) + " C-END"
        lock = {
            "candidate_id": "cand-1", "title": "title", "hypothesis": hypothesis,
            "scope": "scope " + ("detail " * 140) + " S-END",
            "decisive_test": "verify label encoding before fitting the final model",
            "cycle_progress_criteria": {"success": [{"id": "M-S1", "criterion": criterion}], "failure": []},
            "candidate_provenance": {"huge": "x" * 10000},
            "qualification": {"huge": "y" * 10000},
        }
        projected = stage_context("investigator", "investigate", {"hypothesis_lock": lock, "round": 1})
        compact = projected["frozen_objective"]
        self.assertEqual(compact["hypothesis"], hypothesis)
        self.assertEqual(compact["decisive_test"], "verify label encoding before fitting the final model")
        self.assertNotIn("hypothesis_id", compact)
        self.assertNotIn("cycle_criteria", compact)
        self.assertNotIn("investigation_requirements", compact)
        self.assertNotIn("candidate_provenance", compact)
        self.assertNotIn("qualification", compact)
        self.assertNotIn(criterion, json.dumps(compact, ensure_ascii=False))
        self.assertNotIn("…[clipped]", json.dumps(compact, ensure_ascii=False))

    def test_frontier_receives_full_goal_contract_and_sqlite_graph_without_required_context(self):
        goal = "goal " + ("scientific-detail " * 300) + " G-END"
        contract = "contract " + ("scientific-detail " * 500) + " C-END"
        required = "required " + ("artifact-path-detail " * 300) + " R-END"
        projected = stage_context("research_lead", "frontier_proposal", {
            "goal": goal, "research_contract": contract, "required_context": required,
            "branch_canvas": "/tmp/exploration.canvas", "canvas_entry_node_id": "h-current",
        })
        self.assertEqual(projected["goal"], goal)
        self.assertEqual(projected["research_contract"], contract)
        self.assertEqual(projected["agent_graph"]["schema"], TEST_AGENT_GRAPH["schema"])
        self.assertNotIn("canvas_access", projected)
        self.assertNotIn("entry_node_id", projected["agent_graph"])
        self.assertNotIn("required_context", projected)
        self.assertNotIn("…[clipped]", json.dumps(projected, ensure_ascii=False))

    def test_frontier_proposal_does_not_import_prior_cycle_science(self):
        projected = stage_context("research_lead", "frontier_proposal", {
            "goal": "G",
            "research_contract": "C",
            "final_gate": "F",
            "frontier_state": {
                "selected_candidate": {
                    "hypothesis": "hidden selected hypothesis",
                    "goal_advance": "hidden advance",
                },
                "hypotheses": [{"id": "H-0001", "hypothesis": "hidden catalog hypothesis", "scope": "hidden"}],
                "continuation_context": {
                    "source_cycle": "1",
                    "hypothesis_ref": "H-0001",
                    "judge_verdict": "supported",
                    "judge_rationale": "the operational prediction-generation test was resolved",
                    "scientific_model_version": 0,
                    "scientific_synthesis": {
                        "conclusions": ["prediction generation is established"],
                        "open_questions": ["predictive performance remains unresolved"],
                    },
                    "cycle_scientific_state": {
                        "established": ["prediction generation is established"],
                        "unresolved": ["predictive performance remains unresolved"],
                        "boundary": ["Limitation: test labels are unavailable"],
                    },
                    "remaining_goal_gap": "hidden goal gap",
                },
            },
            "branch_canvas": "/tmp/exploration.canvas",
            "canvas_entry_node_id": "1-decision",
            "agent_graph": {"nodes": [], "edges": [], "external_edges": []},
        })
        self.assertNotIn("frontier_scientific_context", projected)
        card = stage_card_text("research_lead", "frontier_proposal")
        procedure = procedure_card_text("research_lead", "frontier_proposal")
        self.assertNotIn("frontier_scientific_context", card + procedure)
        self.assertNotIn("prior_cycle_state", card + procedure)
        self.assertNotIn("remaining_goal_gap", card)
        self.assertIn("do not read remaining_goal_gap", procedure)
        self.assertIn("do not force continuation along an active branch", procedure.lower())
        blob = json.dumps(projected, ensure_ascii=False)
        for forbidden in (
            "prediction generation is established",
            "predictive performance remains unresolved",
            "Limitation: test labels are unavailable",
            "hidden selected hypothesis",
            "hidden catalog hypothesis",
            "hidden goal gap",
            "the operational prediction-generation test was resolved",
            "remaining_goal_gap",
            "scientific_synthesis",
            "prior_cycle_state",
        ):
            self.assertNotIn(forbidden, blob)

    def test_current_qualification_shortlist_is_not_count_truncated(self):
        candidates = [{
            "id": f"cand-{i}",
            "title": f"candidate {i}",
            "hypothesis": f"H{i}",
            "why_now": f"why {i}",
            "goal_advance": f"advance {i}",
            "decisive_test": f"test {i}",
            "evidence_requirements": [],
            "status": "provisional",
        } for i in range(15)]
        reviews = [{
            "candidate_id": f"cand-{i}", "verdict": "qualified", "reason": f"reason {i}", "blocking_issues": []
        } for i in range(15)]
        seed = [{
            key: row[key] for key in ("hypothesis", "why_now", "goal_advance", "decisive_test")
        } for row in candidates]
        challenger = stage_context("challenger", "candidate_review", {
            "candidate": seed, "canvas_focus": {}, "linked_reports": [],
        })
        self.assertEqual([row["hypothesis"] for row in challenger["candidate"]], [f"H{i}" for i in range(15)])
        self.assertNotIn("title", challenger["candidate"][0])
        self.assertNotIn("title", challenger["candidate"][0])
        self.assertNotIn("status", challenger["candidate"][0])
        selection = stage_context("research_lead", "candidate_selection", {
            "candidates": candidates, "candidate_reviews": reviews,
        })
        self.assertEqual([row["id"] for row in selection["qualified_candidates"]], [f"cand-{i}" for i in range(15)])
        self.assertNotIn("candidate_reviews", selection)
        self.assertNotIn("previous_selection", selection)

    def test_workflow_invocation_does_not_reclip_stage_scientific_context(self):
        class CaptureChallenger(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.prompt = ""

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                if role == "challenger":
                    self.prompt = prompt
                    return thread, json.dumps({"reviews": [{
                        "candidate_id": "cand-long", "verdict": "qualified", "reason": "scientifically executable", "blocking_issues": []
                    }]})
                return super().invoke(role, prompt, thread_id, model, provider)

        decisive = "T " + ("full-procedure " * 260) + " T-END"
        candidate = {
            "id": "cand-long", "hypothesis": "H", "why_now": "now", "goal_advance": "advance",
            "decisive_test": decisive, "evidence_requirements": [], "status": "provisional",
        }
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = CaptureChallenger()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            workflow._call(state, "challenger", {
                "mode": "candidate_review",
                "candidates": [candidate],
                "candidate": [{
                    "hypothesis": candidate["hypothesis"], "why_now": candidate["why_now"],
                    "goal_advance": candidate["goal_advance"], "decisive_test": decisive,
                }],
                "canvas_focus": {}, "linked_reports": [],
            }, fresh_thread=True)
            ctx = json.loads(backend.prompt.split("Context:\n", 1)[1])
            self.assertEqual(ctx["candidate"][0]["decisive_test"], decisive)
            self.assertNotIn("…[clipped]", ctx["candidate"][0]["decisive_test"])

    def test_investigator_prompt_uses_only_minimal_scientific_context(self):
        lock = {
            "id": "H-0007", "candidate_id": "cand-1", "title": "H title",
            "hypothesis": "H", "scope": "scope", "decisive_test": "test H directly",
            "cycle_progress_criteria": {"success": [{"id": "M-S1", "criterion": "test H"}], "failure": []},
            "candidate_provenance": {"why_now": "x" * 5000, "decisive_test": "y" * 5000},
            "qualification": {"review": "z" * 5000},
            "hypothesis_terminal_criteria": {"success": [{"id": "H-S1", "criterion": "q" * 5000}]},
        }
        delta = {
            "findings": [{"statement": "scaffold convention is fixed", "evidence_kind": "local_source"}],
            "remaining_scientific_question": "verify row identity",
        }
        projected = stage_context("investigator", "investigate", {
            "hypothesis_lock": lock,
            "branch": {"hypothesis": "duplicate branch"},
            "investigation_attempt": 2, "round": 3, "internal_turn": 4,
            "max_rounds": 8, "rework_left": 1,
            "router_feedback": {"decision": "investigate"}, "routing_source": "sdk",
            "judge_feedback": {"assessment_state": "insufficient_evidence"},
            "judge_objections": [{"objection_id": "J-1", "reason": "not for normal investigate"}],
            "recent_gap_history": [{"gap": {"kind": "numerical", "description": "old gap"}}],
            "required_context": "benchmark/data.csv",
            "task_home": "/task", "note_area": "/task",
            "research_root": "/task/.theory-master/research",
            "cycle_workspace": "/task/.theory-master/research/7/workspace",
            "benchmark_root": "/task/benchmark", "repo_root": "/repo",
            "previous_executor": "literature", "previous_scientific_delta": delta,
            "evidence_refs": ["E-1"],
        }, continuation=True)
        self.assertEqual(set(projected), {"frozen_objective", "previous_scientific_delta", "workspace_access"})
        self.assertEqual(projected["frozen_objective"], {
            "hypothesis": "H", "scope": "scope", "decisive_test": "test H directly",
        })
        self.assertNotIn("hypothesis_id", projected["frozen_objective"])
        self.assertNotIn("cycle_criteria", projected["frozen_objective"])
        self.assertEqual(projected["previous_scientific_delta"], delta)
        self.assertEqual(projected["workspace_access"], {
            "task_home": "/task",
            "note_area": "/task",
            "research_root": "/task/.theory-master/research",
            "cycle_workspace": "/task/.theory-master/research/7/workspace",
            "benchmark_root": "/task/benchmark",
            "repo_root": "/repo",
        })
        for runtime_key in (
            "investigation_attempt", "round", "internal_turn", "max_rounds", "rework_left",
            "router_feedback", "routing_source", "judge_feedback", "judge_objections",
            "recent_gap_history", "required_context", "task_root", "repo_root", "branch",
            "previous_executor", "evidence_refs",
        ):
            self.assertNotIn(runtime_key, projected)

    def test_investigator_same_thread_continuation_does_not_replay_own_delta(self):
        context = {
            "hypothesis_lock": {"hypothesis": "H", "decisive_test": "test H"},
            "previous_executor": "investigator",
            "previous_scientific_delta": {
                "findings": [{"statement": "already known in this thread", "evidence_kind": "numerical"}],
                "remaining_scientific_question": "one more local check",
            },
        }
        continued = stage_context("investigator", "investigate", context, continuation=True)
        self.assertEqual(set(continued), {"frozen_objective"})
        fresh_recovery = stage_context("investigator", "investigate", context, continuation=False)
        self.assertIn("previous_scientific_delta", fresh_recovery)
        cross_executor = stage_context("investigator", "integrate_literature", {
            **context, "previous_executor": "literature",
        }, continuation=True)
        self.assertIn("previous_scientific_delta", cross_executor)

    def test_judge_context_projects_only_scientific_semantics(self):
        projected = stage_context("judge", "terminal_assessment", {
            "hypothesis_lock": {
                "id": "H-0001", "hypothesis": "H", "scope": "S", "decisive_test": "T",
                "cycle_criteria": [{"id": "C-1", "criterion": "T"}],
            },
            "investigation_result": {
                "schema": "theory-master/investigation-result/v1", "investigation_attempt": 3, "rounds": 5,
                "findings": [{
                    "statement": "result", "relation": "supports", "criterion_ids": ["C-1"],
                    "objection_ids": ["J-0001"], "scope": "S", "limitations": "L",
                    "evidence_kind": "numerical",
                }],
            },
            "prior_objection": {
                "objection_id": "J-0001", "criterion_id": "C-1", "reason": "still unresolved",
                "requested_resolution": "one more check", "executor_role": "investigator",
                "source_gap_id": "external-gap-x", "new_search_lead": "hidden routing hint",
            },
        })
        self.assertEqual(projected["hypothesis_lock"], {"hypothesis": "H", "scope": "S", "decisive_test": "T"})
        self.assertEqual(projected["investigation_result"], {"findings": [{
            "statement": "result", "scope": "S", "limitations": "L",
        }]})
        self.assertEqual(projected["prior_objection"], {
            "rationale": "still unresolved", "requested_resolution": "one more check",
        })
        serialized = json.dumps(projected, ensure_ascii=False)
        for forbidden in (
            "hypothesis_id", "cycle_criteria", "criterion_ids", "objection_ids", "objection_id",
            "criterion_id", "executor_role", "source_gap_id", "new_search_lead", "investigation_attempt", "rounds",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_judge_prompt_states_terminal_vs_rework_modes_explicitly(self):
        contract = role_contract("judge", phase="terminal_assessment")
        prompt = build_continuation_prompt(
            role="judge", stage="terminal_assessment", contract_ref=contract.ref,
            contract_schema=contract.prompt_projection(), context={"hypothesis_lock": {"hypothesis": "H"}},
        )
        self.assertIn("verdict=null requires exactly one objection", prompt)
        self.assertIn("terminal verdicts omit objection", prompt)
        self.assertIn("requested_resolution", prompt)
        self.assertIn("needs_external_scholarship", prompt)
        self.assertIn("Do not emit criterion IDs", prompt)

    def test_candidate_review_contract_repair_requires_sdk_id_instead_of_position(self):
        prompt = build_contract_repair_prompt(
            "candidate_review",
            [{"message": "candidate review count does not match input shortlist"}],
            '{"reviews":[{"verdict":"qualified","reason":"..."}]}',
            {"candidates": [{"id": "cand-1"}], "frontier_state": {"candidates": [{"id": "old-a"}]}},
        )
        self.assertIn('"candidate_id": "cand-1"', prompt)
        self.assertIn("one review per SDK-provided candidate_id", prompt)
        self.assertIn("copy each ID exactly", prompt)
        self.assertIn("Do not substitute candidates from Frontier State", prompt)
        self.assertNotIn('"candidate_id": "old-a"', prompt)

    def test_cycle_synthesis_delta_is_exactly_minimal_seed_plus_navigation(self):
        context = {
            "mode": "cycle_end", "scientific_model": empty_model("g"),
            "scientific_model_path": "/tmp/scientific_model_in.json",
            "cycle_dir": "/tmp/cycle-1",
            "active_hypothesis": {
                "id": "H-0001", "hypothesis": "h",
                "scope": "s", "decisive_test": "d", "cycle_criteria": [{"id": "C-1", "criterion": "c"}],
                "evidence_refs": ["E-1"],
            },
            "judge_decision": {"verdict": "supported", "rationale": "terminal rationale",
                               "assessment_state": "terminal", "resource_disposition": "complete"},
            "research_reading": {"huge": "x" * 50000},
            "candidate_reviews": [{"huge": "y" * 50000}],
            "evidence_manifest": {"path": "/tmp/evidence.json", "payload": {"records": [{"evidence_id": "E-1"}]}},
        }
        delta = stage_context("research_lead", "cycle_synthesis", context)
        self.assertEqual(set(delta), {"active_hypothesis", "judge_decision", "scientific_access"})
        self.assertEqual(delta["active_hypothesis"], {
            "hypothesis": "h", "scope": "s", "decisive_test": "d",
        })
        self.assertEqual(delta["judge_decision"], {"verdict": "supported", "rationale": "terminal rationale"})
        self.assertEqual(delta["scientific_access"], {
            "scientific_model_path": "/tmp/scientific_model_in.json",
            "evidence_manifest_path": "/tmp/evidence.json",
            "cycle_dir": "/tmp/cycle-1",
        })
        for forbidden in ("mode", "scientific_model", "evidence_scope_digest", "research_reading", "candidate_reviews"):
            self.assertNotIn(forbidden, delta)
        contract = role_contract("research_lead", phase="cycle_synthesis")
        prompt = build_continuation_prompt(role="research_lead", stage="cycle_synthesis", contract_ref=contract.ref,
                                           contract_schema=contract.prompt_projection(), context=delta)
        self.assertLess(len(prompt), 12000)
        self.assertNotIn("x" * 100, prompt)
        self.assertNotIn("y" * 100, prompt)
        self.assertIn("action=keep omits model", prompt)
        self.assertIn('"scientific_model_path": "/tmp/scientific_model_in.json"', prompt)

    def test_cycle_synthesis_keep_contract_matches_runtime_validator(self):
        contract = role_contract("research_lead", phase="cycle_synthesis")
        current_model = empty_model("g")
        keep = synthesis()
        keep["scientific_model_update"] = {"action": "keep"}
        valid = validate_agent_output(
            "research_lead", "cycle_synthesis", keep,
            {"cycle_id": "cycle-0069"}, {"scientific_model": current_model}, contract,
        )
        self.assertTrue(valid.valid, valid.as_dict())

        echoed = synthesis()
        echoed["scientific_model_update"] = {
            "action": "keep",
            "model": {
                "summary": current_model["summary"],
            },
        }
        invalid = validate_agent_output(
            "research_lead", "cycle_synthesis", echoed,
            {"cycle_id": "cycle-0069"}, {"scientific_model": current_model}, contract,
        )
        self.assertFalse(invalid.valid)
        self.assertTrue(any("must be omitted when action=keep" in item.message or "keep must omit" in item.message for item in invalid.errors))

    def test_cycle0069_style_extra_root_brace_is_normalized_without_llm_repair(self):
        class Cycle69SyntaxBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                self.calls.append((role, prompt, thread))
                if role == "research_lead":
                    raw = json.dumps(synthesis(), ensure_ascii=False, separators=(",", ":"))
                    raw = raw.replace(',"scientific_model_update"', '},"scientific_model_update"', 1)
                    return thread, raw
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = Cycle69SyntaxBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            output = workflow._call(
                state, "research_lead",
                {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)},
                fresh_thread=True,
            )
            self.assertEqual(output["mode"], "cycle_end")
            self.assertEqual(state["json_normalization_count"], 1)
            self.assertEqual(state["json_parse_repair_count"], 0)
            self.assertEqual(len(backend.calls), 1)
            artifacts = list(workflow.cycle_dir.glob("output_normalizations/*.json"))
            self.assertEqual(len(artifacts), 1)
            saved = json.loads(artifacts[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["classification"], "syntax_damage")
            self.assertIn("merge_premature_top_level_close", [item["kind"] for item in saved["repairs"]])
            events = [json.loads(line) for line in (workflow.cycle_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            normalized = [item for item in events if item.get("type") == "json.normalized"]
            self.assertEqual(len(normalized), 1)
            self.assertEqual(normalized[0]["payload"]["role"], "research_lead")
            anomalies = [json.loads(line) for line in (workflow.cycle_dir / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(anomalies), 1)
            self.assertEqual(anomalies[0]["kind"], "json_normalization")
            self.assertEqual(anomalies[0]["mechanism"], "deterministic_normalization")
            self.assertFalse(anomalies[0]["extra_llm_call"])
            self.assertEqual(state["minor_anomaly_count"], 1)

    def test_shared_normalizer_also_protects_challenger_output(self):
        class ChallengerSyntaxBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                if role == "challenger":
                    self.calls += 1
                    return thread, '{"reviews":[{"candidate_id":"c1","verdict":"qualified","reason":"ok"}],}'
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = ChallengerSyntaxBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            output = workflow._call(state, "challenger", {"candidates": [{"id": "c1", "hypothesis": "H", "scope": "test", "why_now": "Goal incomplete", "goal_advance": "Establish a reproducible scientific result for Final Gate", "decisive_test": "Test whether the result is reproducible"}]}, fresh_thread=True)
            self.assertEqual(output["reviews"][0]["verdict"], "qualified")
            self.assertEqual(backend.calls, 1)
            self.assertEqual(state["json_normalization_count"], 1)
            self.assertEqual(state["json_parse_repair_count"], 0)

    def test_selection_repair_gets_stage_procedure_and_only_repair_delta(self):
        context = {
            "phase": "finalization",
            "candidates": [{"id": "c1", "title": "broad", "hypothesis": "H", "decisive_test": "old test"}],
            "candidate_reviews": [{"candidate_id": "c1", "verdict": "revise", "reason": "too broad"}],
            "qualification_error": {
                "verdict": "revise", "candidate_id": "c1", "reason": "too broad", "repair_attempt": 1, "repair_limit": 4,
                "blocking_issues": [{"id": "c1:block-1", "problem": "two questions coupled", "required_change": "keep only one"}],
            },
            "scientific_model": empty_model("g"),
            "initial_review": {"huge": "x" * 50000},
        }
        delta = stage_context("research_lead", "selection_repair", context)
        option = delta["repair_options"][0]
        self.assertEqual(option["candidate_id"], "c1")
        self.assertEqual(option["blockers"][0]["id"], "c1:block-1")
        self.assertNotIn("repair_budget", delta)
        self.assertNotIn("repair_guidance", delta)
        self.assertNotIn("candidate_reviews", delta)
        self.assertNotIn("review_reason", delta)
        self.assertNotIn("blocking_issues", delta)
        self.assertNotIn("qualification_error", delta)
        self.assertNotIn("scientific_model", delta)
        contract = role_contract("research_lead", phase="selection_repair")
        prompt = build_continuation_prompt(role="research_lead", stage="selection_repair", contract_ref=contract.ref,
                                           contract_schema=contract.prompt_projection(), context=delta)
        self.assertIn("Procedure for this stage only", prompt)
        self.assertIn("repair_options", prompt)
        self.assertIn("blocker_closures", prompt)
        self.assertIn("cannot_repair", prompt)
        self.assertIn("unchanged H/scope/estimand", prompt)
        self.assertIn("cannot_repair remains unqualified", prompt)
        self.assertNotIn("repair_budget", prompt)

    def test_local_source_gap_keeps_investigator_stage_without_file_authorization_context(self):
        from theory_master.prompt_stages import stage_context
        from theory_master.stage_registry import stage_for
        context = {
            "previous_scientific_delta": {
                "remaining_scientific_question": "inspect supplied benchmark",
            },
            "previous_executor": "literature",
            "required_context": "- benchmark/instruction.md\n- benchmark/data.csv",
            "task_home": "/task",
            "note_area": "/task",
            "research_root": "/task/.theory-master/research",
            "cycle_workspace": "/task/.theory-master/research/96/workspace",
            "benchmark_root": "/task/benchmark",
            "repo_root": "/repo",
            "hypothesis_lock": {"status": "locked", "hypothesis": "H", "decisive_test": "inspect benchmark"},
            "round": 2,
        }
        stage = stage_for("investigator", {}, context)
        self.assertEqual(stage, "integrate_literature")
        projected = stage_context("investigator", stage, context, continuation=True)
        self.assertNotIn("active_evidence_requirement", projected)
        self.assertNotIn("required_context", projected)
        self.assertNotIn("repo_root", projected)
        self.assertNotIn("source_manifest", projected)
        self.assertEqual(projected["workspace_access"]["benchmark_root"], "/task/benchmark")
        self.assertEqual(projected["workspace_access"]["cycle_workspace"], "/task/.theory-master/research/96/workspace")
        self.assertEqual(
            projected["previous_scientific_delta"]["remaining_scientific_question"],
            "inspect supplied benchmark",
        )

    def test_initial_investigator_does_not_receive_file_authorization_manifest(self):
        from theory_master.prompt_stages import stage_context

        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / "task"
            workspace = Path(temp) / "cycle-workspace"
            (task / "benchmark" / "datasets").mkdir(parents=True)
            workspace.mkdir()
            instruction = task / "benchmark" / "instruction.md"
            dataset = task / "benchmark" / "datasets" / "data.csv"
            instruction.write_text("task statement", encoding="utf-8")
            dataset.write_text("x\n1\n", encoding="utf-8")

            projected = stage_context("investigator", "investigate", {
                "required_context": "- benchmark/instruction.md\n- benchmark/datasets/data.csv",
                "task_home": str(task),
                "note_area": str(task),
                "research_root": str(task / ".theory-master" / "research"),
                "cycle_workspace": str(workspace),
                "benchmark_root": str(task / "benchmark"),
                "hypothesis_lock": {"status": "locked", "hypothesis": "H"},
                "round": 1,
            })

            self.assertNotIn("source_manifest", projected)
            self.assertNotIn("required_context", projected)
            self.assertNotIn("task_root", projected)
            self.assertNotIn("workspace_dir", projected)
            self.assertEqual(projected["workspace_access"]["task_home"], str(task))
            self.assertEqual(projected["workspace_access"]["benchmark_root"], str(task / "benchmark"))
            self.assertEqual(projected["workspace_access"]["cycle_workspace"], str(workspace))

    def test_investigator_does_not_expand_tbh_manifest_into_file_permissions(self):
        from theory_master.prompt_stages import stage_context

        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / "task"
            workspace = Path(temp) / "cycle-workspace"
            benchmark = task / "benchmark"
            upstream = benchmark / "public_upstream"
            upstream.mkdir(parents=True)
            workspace.mkdir()
            source = upstream / "models_MD.py"
            source.write_text("labels=['MCNC','MCLCNC','all']\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = benchmark / "source_manifest.json"
            manifest.write_text(json.dumps({
                "schema": "tm-benchmark-harness/source-manifest/v1",
                "files": [{
                    "path": "benchmark/public_upstream/models_MD.py",
                    "role": "public_scientific_source",
                    "exists": True,
                    "size": source.stat().st_size,
                    "sha256": digest,
                    "source": "git:https://example.invalid/repo@deadbeef:models_MD.py",
                }],
            }), encoding="utf-8")
            context = {
                "required_context": "- benchmark/source_manifest.json",
                "task_home": str(task),
                "note_area": str(task),
                "research_root": str(task / ".theory-master" / "research"),
                "cycle_workspace": str(workspace),
                "benchmark_root": str(benchmark),
                "repo_root": str(Path(temp) / "repo"),
                "hypothesis_lock": {"status": "locked", "hypothesis": "H"},
                "round": 2,
                "judge_objections": [{"objection_id": "J-1", "reason": "verify labels"}],
            }

            projected = stage_context("investigator", "judge_rework", context)
            self.assertNotIn("source_manifest", projected)
            self.assertNotIn("task_root", projected)
            self.assertNotIn("workspace_dir", projected)
            self.assertNotIn("repo_root", projected)
            self.assertEqual(projected["workspace_access"]["benchmark_root"], str(benchmark))
            self.assertEqual(projected["workspace_access"]["repo_root"], str(Path(temp) / "repo"))

            source.write_text("tampered\n", encoding="utf-8")
            projected = stage_context("investigator", "judge_rework", context)
            self.assertNotIn("source_manifest", projected)

    def test_investigation_has_no_dynamic_requirement_object(self):
        self.assertFalse(hasattr(Workflow, "_dynamic_requirement_for_executor"))
        from theory_master.stage_registry import stage_for
        self.assertEqual(
            stage_for("investigator", {}, {"round": 3}),
            "investigate",
        )

    def test_open_ended_investigator_stage_does_not_receive_fixed_procedure(self):
        contract = role_contract("investigator", phase="investigate")
        prompt = build_continuation_prompt(role="investigator", stage="investigate", contract_ref=contract.ref,
                                           contract_schema=contract.prompt_projection(), context={"round": 1})
        self.assertNotIn("Procedure for this stage only", prompt)

    def test_lead_prompt_stages_do_not_replay_large_duplicate_state(self):
        huge_candidates = [{
            "id": f"c{i}", "title": f"candidate {i}", "hypothesis": "H" * 4000,
            "scope": "S" * 2000, "rationale": "R" * 3000, "status": "provisional",
            "evolution_operator": "derive",
        } for i in range(12)]
        model = empty_model("g")
        model["established_knowledge"] = [{
            "id": f"f{i}", "statement": "F" * 240, "scope": "test", "evidence_refs": [],
            "limitations": "fixture", "status": "supported",
        } for i in range(8)]
        model["current_scientific_picture"] = "P" * 1200
        model["current_frontier"] = "Q" * 800
        frontier = stage_context("research_lead", "frontier_proposal", {
            "goal": "g", "research_contract": "c", "final_gate": "fg", "required_context": "r",
            "scientific_model": model, "frontier": huge_candidates,
            "hypothesis_catalog": [{"id": "H-0001", "hypothesis": "old H"}],
            "prior_cycle": {"candidates": huge_candidates, "verdict": "open", "hypothesis": "prior H"},
            "research_reading": {"path": "/tmp/index", "loaded_reports": []},
            "branch_canvas": "/tmp/exploration.canvas", "canvas_entry_node_id": "h-current",
            "canvas_graph_overview": {"nodes": huge_candidates},
            "current_node_neighborhood": {"nodes": huge_candidates},
            "lead_literature_detour_used": True,
        })
        self.assertNotIn("scientific_model", frontier)
        self.assertIn("scientific_model_summary", frontier)
        self.assertEqual(frontier["scientific_model_summary"]["version"], model["version"])
        self.assertEqual(set(frontier["scientific_model_summary"]), {"version", "summary"})
        self.assertNotIn("F" * 100, json.dumps(frontier["scientific_model_summary"], ensure_ascii=False))
        self.assertEqual(frontier["goal"], "g")
        self.assertEqual(frontier["research_contract"], "c")
        self.assertEqual(frontier["final_gate"], "fg")
        self.assertEqual(frontier["agent_graph"]["schema"], TEST_AGENT_GRAPH["schema"])
        self.assertNotIn("canvas_access", frontier)
        for removed in (
            "required_context", "research_reading", "hypothesis_catalog", "prior_cycle_summary",
            "canvas_graph_overview", "current_node_neighborhood", "lead_literature_detour_used",
        ):
            self.assertNotIn(removed, frontier)
        self.assertNotIn("…[clipped]", json.dumps(frontier, ensure_ascii=False))

        reviews = [{"candidate_id": f"c{i}", "verdict": "qualified", "reason": "V" * 3000}
                   for i in range(12)]
        selection = stage_context("research_lead", "candidate_selection", {
            "phase": "finalization", "initial_review": {"huge": "X" * 50000},
            "scientific_model": model, "candidates": huge_candidates, "candidate_reviews": reviews,
        }, continuation=True)
        self.assertNotIn("initial_review", selection)
        self.assertNotIn("scientific_model_summary", selection)
        selection_blob = json.dumps(selection, ensure_ascii=False)
        self.assertNotIn("H" * 100, selection_blob)
        self.assertNotIn("S" * 100, selection_blob)
        self.assertNotIn("V" * 3000, selection_blob)
        self.assertEqual(set(selection), {"qualified_candidates"})
        self.assertTrue(all(set(row) <= {"id", "title"} for row in selection["qualified_candidates"]))
        self.assertLess(len(selection_blob), 5000)

    def test_investigator_semantic_findings_use_current_schema(self):
        contract = role_contract("investigator", phase="investigate")
        state = {"hypothesis_lock": {"cycle_progress_criteria": {
            "success": [{"id": "M-S1", "criterion": "s"}],
            "failure": [{"id": "M-F1", "criterion": "f"}],
        }}, "investigation_attempt": 1, "investigation_attempts": {"attempt-0001": {"objections": []}}}
        good = {"findings": [{"statement": "audit result"}]}
        result = validate_agent_output("investigator", "investigate", good, state,
                                       {"hypothesis_lock": state["hypothesis_lock"]}, contract)
        self.assertTrue(result.valid, result.as_dict())
        self.assertEqual(contract.version, 10)
        projection = contract.prompt_projection()
        self.assertIn('"method":', projection)
        self.assertIn('"result":', projection)
        self.assertIn("statement", projection)
        self.assertNotIn('"scope":', projection)
        self.assertNotIn('"limitations":', projection)
        self.assertNotIn('"evidence_kind":', projection)
        self.assertNotIn('"source_refs":', projection)
        self.assertIn("provenance", projection)

        with_deprecated_fields = {"findings": [{
            "statement": "bounded audit result from one benchmark with a single split",
            "scope": "one benchmark", "limitations": "single split",
        }]}
        result = validate_agent_output("investigator", "investigate", with_deprecated_fields, state,
                                       {"hypothesis_lock": state["hypothesis_lock"]}, contract)
        self.assertFalse(result.valid, result.as_dict())

        for obsolete_field in ("evidence_kind", "source_refs"):
            with self.subTest(obsolete_field=obsolete_field):
                bad = json.loads(json.dumps(good))
                bad["findings"][0][obsolete_field] = "obsolete"
                result = validate_agent_output("investigator", "investigate", bad, state,
                                               {"hypothesis_lock": state["hypothesis_lock"]}, contract)
                self.assertFalse(result.valid)

    def test_cycle23_string_claim_has_exact_json_pointer(self):
        contract = role_contract("research_lead", phase="cycle_synthesis")
        output = synthesis(claims=["bad claim", "another bad claim"])
        result = validate_agent_output(
            "research_lead", "cycle_synthesis", output,
            {"cycle_id": "cycle-0023"}, {"scientific_model": empty_model("g")}, contract,
        )
        self.assertFalse(result.valid)
        paths = [item.path for item in result.errors]
        self.assertIn("/scientific_model_update/model/summary", paths)
        first = next(item for item in result.errors if item.path == "/scientific_model_update/model/summary")
        self.assertEqual(first.code, "type_mismatch")
        self.assertEqual(first.expected, "string")

    def test_current_contracts_reject_removed_critical_issue_protocol(self):
        investigator = role_contract("investigator", phase="investigate")
        with self.assertRaisesRegex(ValueError, "critical_issue_updates is obsolete"):
            investigator.validate({"critical_issue_updates": {"c25fic-1": "unresolved"}}, {})
        literature = role_contract("literature", phase="targeted_source")
        literature_context = {"literature_task": {
            "gap_id": "EXT-1", "task_type": "targeted_source", "triggered_by": "investigator",
            "search_target": "external source", "why_external_needed": "not available locally",
            "expected_external_evidence": "primary source verification",
        }}
        with self.assertRaisesRegex(ValueError, "Literature semantic-state fields are obsolete"):
            literature.validate({"critical_issue_updates": {"c25fic-1": "unresolved"}}, literature_context)

    def test_cycle24_critical_issue_is_repaired_before_routing(self):
        class IssueRepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.issue_threads = []

            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "investigator":
                    thread, raw = super().invoke(role, prompt, thread_id, model, provider)
                    self.issue_threads.append(thread)
                    output = json.loads(raw)
                    repairing = "Contract validation failed" in prompt
                    if not repairing:
                        output["critical_issue_updates"] = {"c25fic-1": "unresolved"}
                    else:
                        output.pop("critical_issue_updates", None)
                    return thread, json.dumps(output)
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 1  # Assert one Cycle's qualification/repair, not later Cycles.
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            spec = TaskSpec.from_config(spec.config_path)
            backend = IssueRepairBackend()
            state = Workflow(spec, backend).run()
            self.assertEqual(state["status"], "over")
            self.assertEqual(state["contract_repair_count"], 1)
            self.assertEqual(state["scientific_rework_count"], 0)
            self.assertNotIn("critical_issues", state)
            self.assertEqual(len(backend.issue_threads), 2)
            self.assertEqual(len(set(backend.issue_threads)), 1)

    def test_contract_repair_uses_same_thread_and_does_not_consume_scientific_rework(self):
        class RepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                self.calls.append((prompt, thread))
                if "Contract validation failed" in prompt:
                    return thread, json.dumps(synthesis(claims="accepted bounded scientific summary"))
                return thread, json.dumps(synthesis(claims=["bad claim"]))

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = RepairBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            before = state["judge_rework_count"]
            output = workflow._call(state, "research_lead", {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)}, fresh_thread=True)
            self.assertEqual(output["scientific_model_update"]["model"]["summary"], "accepted bounded scientific summary")
            self.assertEqual(len(backend.calls), 2)
            self.assertEqual(backend.calls[0][1], backend.calls[1][1])
            self.assertEqual(state["contract_repair_count"], 1)
            self.assertEqual(state["judge_rework_count"], before)
            self.assertEqual(state["scientific_rework_count"], before)
            repair_prompt = backend.calls[1][0]
            self.assertIn("/scientific_model_update/model/summary", repair_prompt)
            self.assertNotIn("research_reading", repair_prompt)
            self.assertNotIn("# 身份", repair_prompt)
            anomalies = [json.loads(line) for line in (workflow.cycle_dir / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["kind"] for item in anomalies], ["contract_repair_retry"])
            self.assertTrue(anomalies[0]["extra_llm_call"])
            self.assertEqual(anomalies[0]["role"], "research_lead")
            self.assertEqual(anomalies[0]["stage"], "cycle_synthesis")
            self.assertEqual(anomalies[0]["attempt"], 1)
            self.assertEqual(state["minor_anomaly_count"], 1)

            update = output["scientific_model_update"]
            committed = commit_authored_model(
                spec.research_root, update["model"], cycle_id=workflow.cycle_dir.name, action=update["action"],
            )
            self.assertEqual(committed["summary"], "accepted bounded scientific summary")

    def test_json_parse_repair_is_once_and_same_thread(self):
        class JsonRepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                self.calls.append((prompt, thread))
                if "JSON parse repair" in prompt:
                    return thread, json.dumps(synthesis())
                return thread, "{not-json"

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = JsonRepairBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            output = workflow._call(state, "research_lead", {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)}, fresh_thread=True)
            self.assertEqual(output["mode"], "cycle_end")
            self.assertEqual(state["json_parse_repair_count"], 1)
            self.assertEqual(state["contract_repair_count"], 0)
            self.assertEqual(len(backend.calls), 2)
            self.assertEqual(backend.calls[0][1], backend.calls[1][1])
            self.assertIn("{not-json", backend.calls[1][0])
            self.assertIn("Parser error:", backend.calls[1][0])
            anomalies = [json.loads(line) for line in (workflow.cycle_dir / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["kind"] for item in anomalies], ["json_repair_retry"])
            self.assertEqual(anomalies[0]["mechanism"], "sdk_driven_llm_retry")
            self.assertTrue(anomalies[0]["extra_llm_call"])
            self.assertEqual(state["minor_anomaly_count"], 1)

    def test_contract_and_json_repairs_have_independent_one_shot_budgets(self):
        class MixedRepairBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.calls = []
            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                self.calls.append((prompt, thread))
                if "Contract validation failed" in prompt:
                    return thread, "{broken-contract-repair"
                if "JSON parse repair" in prompt:
                    return thread, json.dumps(synthesis(claims="accepted bounded scientific summary"))
                return thread, json.dumps(synthesis(claims=["bad claim"]))

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = MixedRepairBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            output = workflow._call(state, "research_lead", {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)}, fresh_thread=True)
            self.assertEqual(output["scientific_model_update"]["model"]["summary"], "accepted bounded scientific summary")
            self.assertEqual(state["contract_repair_count"], 1)
            self.assertEqual(state["json_parse_repair_count"], 1)
            self.assertEqual(len(backend.calls), 3)
            self.assertEqual(len({thread for _, thread in backend.calls}), 1)
            anomalies = [json.loads(line) for line in (workflow.cycle_dir / "important_events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["kind"] for item in anomalies], ["contract_repair_retry", "json_repair_retry"])
            self.assertTrue(all(item["extra_llm_call"] for item in anomalies))
            self.assertEqual(state["minor_anomaly_count"], 2)

    def test_cycle23_style_bad_claim_is_repaired_before_model_commit(self):
        class Cycle23Backend(FakeBackend):
            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "research_lead" and "Current task: cycle_synthesis" in prompt:
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    return thread, json.dumps(synthesis(claims=["cycle23 bad claim"]))
                if role == "research_lead" and "Contract validation failed for stage cycle_synthesis" in prompt:
                    thread = self.ensure_thread(role, thread_id, model, provider)
                    return thread, json.dumps(synthesis(claims="accepted bounded scientific summary"))
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            state = Workflow(spec, Cycle23Backend(reworks=0)).run()
            self.assertEqual(state["status"], "over")
            self.assertEqual(state["contract_repair_count"], 1)
            self.assertEqual(state["judge_rework_count"], 0)
            from theory_master.scientific_model import load as load_scientific_model
            model = load_scientific_model(spec.research_root, goal_id=spec.goal_id)
            self.assertEqual(model["summary"], "accepted bounded scientific summary")
            cycle = Path(state["cycle_dir"])
            self.assertTrue((cycle / "scientific_model_staged.json").is_file())

    def test_second_contract_failure_safely_closes_and_preserves_recovery_artifact(self):
        class BadBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.threads_seen = []
            def invoke(self, role, prompt, thread_id, model, provider):
                thread = self.ensure_thread(role, thread_id, model, provider)
                self.threads_seen.append(thread)
                return thread, json.dumps(synthesis(claims=["still bad"]))

        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            backend = BadBackend()
            workflow = Workflow(spec, backend)
            workflow._ensure_cycle()
            state = workflow._new_state()
            with self.assertRaises(RecoveryStop):
                workflow._call(state, "research_lead", {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)}, fresh_thread=True)
            self.assertEqual(state["status"], "needs_attention")
            self.assertEqual(state["closure_reason"], "contract_repair_failed")
            self.assertTrue(state["resumable"])
            self.assertEqual(state["contract_repair_count"], 3)
            self.assertEqual(len(set(backend.threads_seen)), 1)
            artifact = Path(state["recovery"]["artifact"])
            self.assertTrue(artifact.is_file())
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["raw_outputs"]), 4)
            self.assertTrue(any(item["path"] == "/scientific_model_update/model/summary" for item in saved["validation_errors"]))

    def test_output_reference_validation_does_not_preflight_artifact_paths(self):
        output = {"finding": {"artifact_roles": [{"path": "computation/not-yet-written.json"}]}}
        self.assertTrue(validate_references(output, {}, {}).valid)

    def test_prompt_metrics_record_stage_and_size(self):
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self.make(Path(temp))
            workflow = Workflow(spec, FakeBackend(reworks=0))
            workflow._ensure_cycle()
            state = workflow._new_state()
            workflow._call(state, "research_lead", {"mode": "cycle_end", "scientific_model": empty_model(spec.goal_id)}, fresh_thread=True)
            metric = state["prompt_metrics"][-1]
            self.assertEqual(metric["stage"], "cycle_synthesis")
            self.assertGreater(metric["prompt_chars"], 0)
            self.assertLess(metric["prompt_chars"], 12000)

    def test_frontier_maintenance_prompt_is_tip_labeling_only(self):
        self.assertEqual(stage_for("research_lead", {}, {"mode": "frontier_maintenance"}), "frontier_maintenance")
        card = stage_card_text("research_lead", "frontier_maintenance")
        procedure = procedure_card_text("research_lead", "frontier_maintenance")
        self.assertIn("not a Canvas pruning operation", card)
        self.assertIn("does not decide what to investigate next", card)
        self.assertIn("frontier_proposal", card)
        self.assertIn("Do not propose a Candidate", card)
        self.assertIn("modify scientific_status", card)
        self.assertIn("Scientific Model", card)
        for forbidden in ("remaining_goal_gap", "remaining_gap"):
            self.assertNotIn(forbidden, card + procedure)
        projected = stage_context("research_lead", "frontier_maintenance", {
            "goal": "G",
            "research_contract": "C",
            "final_gate": "F",
            "scientific_model": {"version": 2, "summary": "picture"},
            "judge_decision": {"verdict": "supported", "rationale": "local test passed", "rework_plan": "hidden"},
            "scientific_synthesis": {"conclusions": ["done"], "open_questions": [], "next_frontier_recommendation": "hidden"},
            "branch_canvas": "/tmp/exploration.canvas",
            "canvas_entry_node_id": "d1",
            "required_context": "hidden",
        })
        self.assertEqual(set(projected), {
            "goal", "research_contract", "final_gate", "scientific_model_summary",
            "judge_decision", "scientific_synthesis", "agent_graph",
        })
        self.assertEqual(projected["judge_decision"], {"verdict": "supported", "rationale": "local test passed"})
        self.assertNotIn("next_frontier_recommendation", projected["scientific_synthesis"])
        contract = role_contract("research_lead", phase="frontier_maintenance")
        self.assertEqual(set(contract.prompt_schema), {"updates"})
        valid = {"updates": [{"hypothesis_id": "H-0001", "status": "parked", "reason": "mechanism line is paused"}]}
        contract.validate(valid, {"_known_hypothesis_ids": ["H-0001"]})
        prompt = build_initial_prompt(
            prefix="", role="research_lead", stage="frontier_maintenance",
            contract_ref=contract.ref, contract_schema=contract.prompt_projection(),
            context=projected, write_rule="Return JSON only.",
        )
        self.assertIn("closed|parked|active", prompt)
        self.assertNotIn("remaining_goal_gap", prompt)
        self.assertNotIn("remaining_gap", prompt)
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            contract.validate({**valid, "candidates": []}, {"_known_hypothesis_ids": ["H-0001"]})
        with self.assertRaisesRegex(ValueError, "status"):
            contract.validate({"updates": [{"hypothesis_id": "H-0001", "status": "next", "reason": "no"}]}, {"_known_hypothesis_ids": ["H-0001"]})


if __name__ == "__main__":
    unittest.main()
