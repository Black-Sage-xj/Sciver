"""Offline Workflow regressions for Literature requests after a consumed detour."""
from __future__ import annotations

import json

import pytest

from theory_master.backends import FakeBackend
from theory_master.attention_guidance import write_attention_guidance
from theory_master.scholarly_transport import fake_scholarly_retrieval
from theory_master.specification import TaskSpec
from theory_master.workflow import Workflow
from theory_master.workflow_invocation import InvocationMixin
from theory_master.workflow_qualification import QualificationMixin


class RequestingLead(FakeBackend):
    def __init__(self, request_calls, *, reject_first=False):
        super().__init__(reworks=0)
        self.request_calls = set(request_calls)
        self.reject_first = reject_first
        self.frontier_calls = 0
        self.review_calls = 0
        self.roles = []
        self.frontier_prompts = []

    def invoke(self, role, prompt, thread_id, model, provider):
        self.roles.append(role)
        if role == "research_lead" and "Current task: frontier_proposal" in prompt:
            self.frontier_calls += 1
            self.frontier_prompts.append(prompt)
            if self.frontier_calls in self.request_calls:
                return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                    "needs_literature": True,
                    "literature_request": {
                        "task_type": "landscape",
                        "search_target": f"Unresolved scholarly literature question {self.frontier_calls}",
                        "why_external_needed": "Compare the source assumptions before choosing a candidate",
                        "expected_external_evidence": "Traceable scientific alternatives and limitations",
                    },
                })
        if role == "challenger" and "Current task: candidate_review" in prompt:
            self.review_calls += 1
            if self.reject_first and self.review_calls == 1:
                context = json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0]
                return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                    "reviews": [{
                        "candidate_id": row["id"], "verdict": "reject",
                        "reason": "The scientific target needs a different, source-grounded comparison.",
                    } for row in context["candidate"]],
                })
        return super().invoke(role, prompt, thread_id, model, provider)


def make_workflow(tmp_path, request_calls, *, reject_first=False, transport_ok=True):
    plan = tmp_path / "research_plan.md"
    plan.write_text(
        "# Goal\n\nTest a bounded claim\n\n# Research Contract\n\nUse task inputs\n\n"
        "# Final Gate\n\nEstablish reproducible evidence\n\n# Required Context\n\n- benchmark\n",
        encoding="utf-8",
    )
    spec = TaskSpec.load(plan)
    config = json.loads(spec.config_path.read_text(encoding="utf-8"))
    config["max_cycles"] = 1
    spec.config_path.write_text(json.dumps(config), encoding="utf-8")
    spec = TaskSpec.from_config(spec.config_path)
    backend = RequestingLead(request_calls, reject_first=reject_first)
    transports = []

    def retrieve(task, workspace):
        transports.append(task)
        return {
            **fake_scholarly_retrieval(task, workspace),
            "transport_ok": transport_ok,
            **({"reason": "Offline simulated transport failure"} if not transport_ok else {}),
        }

    workflow = Workflow(spec, backend, scholarly_retriever=retrieve, stop_before="hypothesis_lock")
    workflow.supervisor_enabled = lambda: False
    return workflow, backend, transports


def assert_pending_source_request(workflow, result, source_number):
    assert result["status"] == "needs_attention"
    assert result["phase"] == "qualification"
    assert result["qualification_status"] == "pending"
    assert result["resumable"] is True
    assert result["execution_active"] is False
    assert result["hypothesis_lock"]["status"] == "unlocked"
    attention = result["attention"]
    assert attention["code"] == "lead_literature_detour_exhausted"
    assert attention["literature_request"]["search_target"] == f"Unresolved scholarly literature question {source_number}"
    assert result["role_outputs"]["research_lead_frontier"]["needs_literature"] is True
    run_state = json.loads((workflow.cycle_dir / "run_state.json").read_text(encoding="utf-8"))
    assert run_state["status"] != "FAILED"
    events = (workflow.cycle_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "lead.literature_detour_exhausted" in events
    assert "Research Lead frontier candidates require unique stable ids" not in events


@pytest.mark.parametrize("requests", [{1}, {1, 2}])
def test_available_result_and_one_correction_can_still_reach_qualification(tmp_path, requests):
    workflow, backend, transports = make_workflow(tmp_path, requests)
    result = workflow.run()
    assert result["status"] == "checkpoint_reached"
    assert result["qualification_status"] == "qualified"
    assert len(transports) == 1
    assert backend.roles.count("literature") == 1
    assert backend.frontier_calls == len(requests) + 1
    assert backend.review_calls == 1
    assert "investigator" not in backend.roles


def test_third_request_waits_with_exact_gap_instead_of_failing_as_empty_candidates(tmp_path):
    workflow, backend, transports = make_workflow(tmp_path, {1, 2, 3})
    result = workflow.run()
    assert_pending_source_request(workflow, result, 3)
    assert backend.frontier_calls == 3
    assert len(transports) == 1
    assert backend.review_calls == 0
    assert "investigator" not in backend.roles
    assert "judge" not in backend.roles
    assert "detour_limit_reached" in backend.frontier_prompts[-1]


@pytest.mark.parametrize("repeated", [False, True])
def test_reproposal_after_challenger_rejection_uses_exhaustion_handling(tmp_path, repeated):
    requests = {1, 3, 4} if repeated else {1, 3}
    workflow, backend, transports = make_workflow(tmp_path, requests, reject_first=True)
    result = workflow.run()
    if repeated:
        assert_pending_source_request(workflow, result, 4)
        assert backend.review_calls == 1
    else:
        assert result["status"] == "checkpoint_reached"
        assert result["qualification_status"] == "qualified"
        assert backend.review_calls == 2
    assert backend.frontier_calls == 4
    assert len(transports) == 1
    assert backend.roles.count("literature") == 1


@pytest.mark.parametrize("counter_only", [False, True])
def test_resume_preserves_consumed_detour_and_can_submit_a_candidate(tmp_path, counter_only):
    workflow, backend, transports = make_workflow(tmp_path, {1, 2, 3})
    paused = workflow.run()
    assert_pending_source_request(workflow, paused, 3)
    path = workflow.cycle_dir / "research_state.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    if counter_only:
        saved.pop("qualification_literature_used")
    path.write_text(json.dumps(saved), encoding="utf-8")
    write_attention_guidance(
        workflow.cycle_dir, "Use the supplied source and retain its limited domain.",
        attention_code="lead_literature_detour_exhausted",
    )
    backend.request_calls.add(4)
    resumed = workflow.resume(workflow.cycle_dir.name)
    assert resumed["status"] == "checkpoint_reached"
    assert resumed["qualification_status"] == "qualified"
    assert len(transports) == 1
    assert backend.roles.count("literature") == 1
    assert resumed["qualification_resume_history"][-1]["literature_request"] == paused["attention"]["literature_request"]
    assert "Use the supplied source" in backend.frontier_prompts[-2]


def test_plain_resume_retains_the_unresolved_request_without_another_search(tmp_path):
    workflow, backend, transports = make_workflow(tmp_path, {1, 2, 3, 4, 5})
    paused = workflow.run()
    assert_pending_source_request(workflow, paused, 3)
    resumed = workflow.resume(workflow.cycle_dir.name)
    assert resumed["status"] == "needs_attention"
    assert resumed["attention"]["code"] == "lead_literature_detour_exhausted"
    assert resumed["hypothesis_lock"]["status"] == "unlocked"
    assert len(transports) == 1
    assert backend.roles.count("literature") == 1


def test_transport_failure_does_not_allow_extra_search_or_empty_candidate_failure(tmp_path):
    workflow, backend, transports = make_workflow(tmp_path, {1, 2, 3}, transport_ok=False)
    paused = workflow.run()
    assert paused["status"] == "needs_attention"
    assert paused["attention"]["code"] == "lead_literature_unavailable"
    resumed = workflow.resume(workflow.cycle_dir.name)
    assert_pending_source_request(workflow, resumed, backend.frontier_calls)
    assert len(transports) == 1
    assert backend.roles.count("literature") == 0


def test_guided_restarts_remain_distinct_after_history_reaches_twenty_entries():
    state = {
        "cycle_id": "1", "qualification_status": "pending",
        "hypothesis_lock": {"status": "unlocked"},
        "qualification_literature_used": True,
        "qualification_resume_history": [{"reason": "older attempt"} for _ in range(20)],
        "operator_guidance_pending": True,
    }
    keys = []
    for generation in (21, 22):
        state["role_outputs"] = {"research_lead_frontier": {
            "needs_literature": True, "literature_request": {"search_target": "Required theorem"},
        }}
        state["operator_guidance"] = {"text": f"New source guidance {generation}"}
        assert QualificationMixin()._restart_unqualified_qualification(state)
        assert state["qualification_restart_generation"] == generation
        assert len(state["qualification_resume_history"]) == 20
        assert state["qualification_resume_history"][-1]["reason"] == (
            "new operator guidance for an exhausted Literature request"
        )
        keys.append(InvocationMixin._invocation_logical_key(
            state, "research_lead", "research_lead", "frontier_proposal",
            {"mode": "cycle_start"}, None,
        ))
    assert keys[0] != keys[1]


def test_explicit_generation_preserves_legacy_invocation_key_before_history_truncation():
    state = {"cycle_id": "1", "qualification_resume_history": [{}, {}, {}]}
    args = ("research_lead", "research_lead", "frontier_proposal", {"mode": "cycle_start"}, None)
    legacy_key = InvocationMixin._invocation_logical_key(state, *args)
    state["qualification_restart_generation"] = 3
    assert InvocationMixin._invocation_logical_key(state, *args) == legacy_key
