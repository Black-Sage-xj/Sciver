from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from theory_master.contract_stages import _validate_lead_literature_detour
from theory_master.specification import TaskSpec
from theory_master.prompt_stages import stage_context as _stage_context
from theory_master.contracts import role_contract
from theory_master.backends import FakeBackend
from theory_master.workflow import Workflow
from theory_master.stage_registry import stage_card_text
from theory_master.workflow_qualification import QualificationMixin


_TEST_AGENT_GRAPH = {
    "schema": "theorymaster/test-agent-graph/v1",
    "hypotheses": [], "current_frontiers": [], "lineage": [], "external_sources": [],
}
_GRAPH_REQUIRED_STAGES = {
    ("research_lead", "frontier_proposal"), ("research_lead", "frontier_maintenance"),
    ("challenger", "candidate_review"), ("judge", "goal_completion"),
}

def stage_context(role, stage, context, *, continuation=False):
    values = dict(context)
    if ((role, stage) in _GRAPH_REQUIRED_STAGES
            and "agent_graph" not in values and "agent_graph_reuse" not in values):
        values["agent_graph"] = dict(_TEST_AGENT_GRAPH)
    return _stage_context(role, stage, values, continuation=continuation)


class StaticReviewFixesTest(unittest.TestCase):
    def test_pending_qualification_preserves_committed_working_set(self):
        review = {"candidate_id": "C1", "verdict": "qualified"}
        state = {
            "qualification_status": "pending",
            "hypothesis_lock": {"status": "unlocked"},
            "qualification_working_set": {
                "candidates": [{"id": "C1", "hypothesis": "H"}],
                "reviews": [review], "pending_ids": ["C1"], "repair_count": 0,
            },
            "role_outputs": {
                "research_lead_frontier": {"candidates": [{"id": "C1", "hypothesis": "H"}]},
                "candidate_review": {"reviews": [review]},
            },
            "frontier_review": {"reviews": [review]},
        }
        before = copy.deepcopy(state)
        self.assertFalse(QualificationMixin()._restart_unqualified_qualification(state))
        self.assertEqual(state, before)

    def test_unqualified_can_still_restart(self):
        state = {
            "qualification_status": "unqualified",
            "hypothesis_lock": {"status": "unlocked"},
            "role_outputs": {"research_lead_frontier": {"candidates": [{"id": "C1"}]}},
            "frontier_review": {"reviews": [{"candidate_id": "C1", "verdict": "reject"}]},
            "recovery": {},
        }
        self.assertTrue(QualificationMixin()._restart_unqualified_qualification(state))
        self.assertEqual(state["qualification_status"], "pending")
        self.assertNotIn("research_lead_frontier", state["role_outputs"])
        self.assertEqual(len(state["qualification_resume_history"]), 1)

    def test_investigator_active_stage_omits_legacy_turn_instruction(self):
        for stage in ("investigate", "judge_rework", "integrate_literature"):
            card = stage_card_text("investigator", stage)
            self.assertNotIn("an empty remaining_scientific_question", card)
            self.assertNotIn("actionable_no_progress_correction", card)
            self.assertIn("findings", str(role_contract("investigator", phase=stage).prompt_schema))
            self.assertNotIn("Report unresolved scientific prerequisites in findings[].limitations", card)
            projected = stage_context("investigator", stage, {"hypothesis_lock": {"hypothesis": "H"}, "actionable_no_progress_correction": "OLD"})
            self.assertNotIn("actionable_no_progress_correction", projected)

    def test_novelty_check_is_a_valid_on_demand_lead_detour(self):
        request = {
            "needs_literature": True,
            "literature_request": {
                "task_type": "novelty_check",
                "search_target": "A specific prior-work priority issue blocking the Final Gate",
                "why_external_needed": "The Final Gate requires checking priority of the central claim",
                "expected_external_evidence": "The closest prior published mechanism",
            },
        }
        self.assertTrue(_validate_lead_literature_detour(request, normal_fields={"candidates"}))

    def test_literature_to_investigator_preserves_source_verification(self):
        record = {"outputs": [{"output": {"evidence_records": [{
            "scientific_insight": "A mechanism is reported",
            "limitations": "Abstract-only claim, not a verified proof",
            "source_ref": "paper:4",
            "source_access": "abstract",
            "locator": {"section": "Abstract"},
            "provenance": {"source": "external_source"},
        }]}}]}
        delta = Workflow._previous_scientific_delta(record)
        finding = delta["findings"][0]
        self.assertEqual(finding["statement"], "A mechanism is reported")
        self.assertEqual(finding["source_ref"], "paper:4")
        self.assertEqual(finding["source_access"], "abstract")
        self.assertEqual(finding["locator"], {"section": "Abstract"})
        self.assertEqual(finding["provenance"], {"source": "external_source"})
        handoff = stage_context("investigator", "integrate_literature", {
            "hypothesis_lock": {"hypothesis": "H", "decisive_test": "test"},
            "previous_executor": "literature",
            "previous_scientific_delta": delta,
        })
        self.assertEqual(handoff["previous_scientific_delta"]["findings"][0], finding)

    def test_lead_literature_result_preserves_novelty_and_source_locator(self):
        context = stage_context("research_lead", "frontier_proposal", {
            "lead_literature_result": {
                "evidence_records": [{
                    "source_ref": "paper:3",
                    "scientific_insight": "Earlier result overlaps",
                    "source_access": "full_text",
                    "locator": {"section": "Results"},
                }],
                "positioning": {
                    "assessment": "partially_overlapping",
                    "closest_prior_work": [{"source_ref": "paper:3", "overlap": "mechanism"}],
                    "priority_risks": ["non-novel mechanism"],
                },
            },
        })
        result = context["lead_literature_result"]
        self.assertEqual(result["positioning"]["assessment"], "partially_overlapping")
        self.assertEqual(result["positioning"]["closest_prior_work"][0]["source_ref"], "paper:3")
        self.assertEqual(result["evidence_records"][0]["source_ref"], "paper:3")
        self.assertEqual(result["evidence_records"][0]["locator"], {"section": "Results"})

    def test_reporter_has_no_canvas_maintenance_contract(self):
        report = role_contract("reporter", phase="report")
        self.assertEqual(set(report.prompt_schema), {"title", "summary"})
        report.validate({"title": "Evidence report", "summary": "Scoped scientific result"}, {})
        with self.assertRaisesRegex(ValueError, "no phase contract"):
            role_contract("reporter", phase="canvas_maintenance")

    def test_declared_frontier_maintenance_context_covers_runtime_projection(self):
        from theory_master.prompt_stages import STAGE_CONTEXT_FIELDS
        projection = stage_context("research_lead", "frontier_maintenance", {
            "goal": "G", "research_contract": "R", "final_gate": "F",
            "active_hypothesis": {"hypothesis": "H"},
            "branch_canvas": "exploration.canvas",
        })
        self.assertIn("current_hypothesis", projection)
        self.assertFalse(set(projection) - set(STAGE_CONTEXT_FIELDS["research_lead", "frontier_maintenance"]))

    def test_legacy_investigator_repair_feedback_does_not_suggest_deleted_field(self):
        with self.assertRaises(ValueError) as error:
            role_contract("investigator", phase="investigate").validate({
                "findings": [], "critical_issue_updates": [],
            }, {})
        self.assertIn("self-contained scientific statements", str(error.exception))
        self.assertNotIn("remaining_scientific_question", str(error.exception))

    def test_lead_and_challenger_redundancy_reduction_keeps_scientific_guards(self):
        from theory_master.stage_registry import procedure_card_text
        from theory_master.contracts import role_contract
        lead = stage_card_text("research_lead", "frontier_proposal")
        procedure = procedure_card_text("research_lead", "frontier_proposal")
        challenger = stage_card_text("challenger", "candidate_review")
        self.assertLess(len(lead) + len(procedure), 10500)
        for phrase in ("unmet Goal, Research Contract, Final Gate or necessary prerequisite",
                       "discriminating consequence", "local contrary result",
                       "new source of uncertainty", "blocking external scholarly gap"):
            self.assertIn(phrase, lead)
        for phrase in ("Frontier labels describe opportunities independently of graph leaves", "unmarked is unknown, not closed",
                       "search formal research history beyond declared parents and recent Cycles across H, Outcomes and External records"):
            self.assertIn(phrase, procedure)
        self.assertIn("lineage_anchor_node_ids", str(role_contract(
            "research_lead", phase="frontier_proposal").prompt_schema))
        self.assertIn("eventual negative/open result is not itself a blocker", challenger)

    def test_lead_second_literature_request_gets_one_correction_no_new_search(self):
        class RepeatedRequestBackend(FakeBackend):
            def __init__(self):
                super().__init__(reworks=0)
                self.frontier_calls = 0

            def invoke(self, role, prompt, thread_id, model, provider):
                if role == "research_lead" and "Current task: frontier_proposal" in prompt:
                    self.frontier_calls += 1
                    if self.frontier_calls <= 2:
                        return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                            "needs_literature": True,
                            "literature_request": {
                                "task_type": "landscape",
                                "search_target": "A specific external prerequisite",
                                "why_external_needed": "Not verified in the task scientific context",
                                "expected_external_evidence": "A relevant primary source",
                            },
                        })
                return super().invoke(role, prompt, thread_id, model, provider)

        with tempfile.TemporaryDirectory() as temporary:
            plan = Path(temporary) / "research_plan.md"
            plan.write_text(
                "# Goal\n\nTest hypothesis\n\n# Research Contract\n\nOnly task inputs\n\n"
                "# Final Gate\n\nEstablish reproducible result\n\n"
                "# Required Context\n\n- benchmark\n", encoding="utf-8",
            )
            spec = TaskSpec.load(plan)
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_cycles"] = 1
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            spec = TaskSpec.from_config(spec.config_path)
            backend = RepeatedRequestBackend()
            workflow = Workflow(spec, backend)
            detours = []

            def fake_detour(state, request, *, lead_stage):
                detours.append(request)
                state.setdefault("lead_literature_detours", {})[lead_stage] = 1
                return {"evidence_records": [], "landscape": {"field_scope": "reviewed field"}}

            workflow._run_lead_literature_detour = fake_detour
            result = workflow.run()
            self.assertEqual(len(detours), 1)
            self.assertEqual(backend.frontier_calls, 3)
            self.assertEqual(result.get("qualification_status"), "qualified")
            self.assertNotEqual(result.get("status"), "needs_attention")
            events = (workflow.cycle_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("qualification.lead_literature_correction", events)

    def test_stale_round_config_is_read_only_and_does_not_block_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = root / "research_plan.md"
            plan.write_text(
                "# Goal\n\nTest Goal\n\n# Research Contract\n\nTask input\n\n"
                "# Final Gate\n\nVerified result\n\n# Required Context\n\n- input\n",
                encoding="utf-8",
            )
            spec = TaskSpec.load(plan)
            config = json.loads(spec.config_path.read_text(encoding="utf-8"))
            config["max_rounds_per_investigation_attempt"] = "obsolete-invalid"
            config["max_total_investigation_rounds_per_cycle"] = -10
            spec.config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded = TaskSpec.from_config(spec.config_path)
            self.assertEqual(loaded.max_rounds, 1)
            self.assertGreater(loaded.max_total_investigation_rounds_per_cycle, 0)

    def test_current_frontier_contract_allows_bounded_literature_detour(self):
        current = role_contract("research_lead", phase="frontier_proposal")
        request = {
            "needs_literature": True,
            "literature_request": {
                "task_type": "novelty_check",
                "search_target": "Priority claim needed by Final Gate",
                "why_external_needed": "Missing verified prior work",
                "expected_external_evidence": "Closest precedent",
            },
        }
        current.validate(request, {})
        compact = stage_context("research_lead", "frontier_proposal", {
            "lead_literature_result": {
                "positioning": {"assessment": "partially_overlapping",
                                "closest_prior_work": [{"source_ref": "paper:0", "overlap": "X"}],
                                "priority_risks": ["prior claim"], "required_citations": ["paper:0"]},
            },
        })
        self.assertEqual(compact["lead_literature_result"]["positioning"]["assessment"], "partially_overlapping")

    def test_historical_round_limits_do_not_block_current_spec(self):
        spec = TaskSpec(Path("/tmp/research_plan.md"), "test", 1, 1, 0,
                        Path("/tmp/runtime"), None, None, None, None,
                        "goal", "contract", "gate", "context",
                        max_total_investigation_rounds_per_cycle=-4)
        self.assertEqual(spec.max_rounds, 1)
        self.assertGreater(spec.max_total_investigation_rounds_per_cycle, 0)
        with self.assertRaisesRegex(ValueError, "Judge rework"):
            TaskSpec(Path("/tmp/research_plan.md"), "test", 1, 1, 1,
                     Path("/tmp/runtime"), None, None, None, None,
                     "goal", "contract", "gate", "context",
                     max_judge_reworks_per_cycle=-1)


if __name__ == "__main__":
    unittest.main()
