"""Exercise Supervisor routing through real exhausted qualification outputs."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master.backends import FakeBackend
from theory_master.specification import TaskSpec, list_cycle_dirs
from theory_master.supervisor_intervention import KEY
from theory_master.task import bootstrap
from theory_master.workflow import Workflow


class QualificationBackend(FakeBackend):
    def __init__(self, *, accept_retry: bool = False, decision: str = "retry_qualification"):
        super().__init__(reworks=0)
        self.accept_retry = accept_retry
        self.decision = decision
        self.proposals = 0
        self.reviews: list[list[str]] = []
        self.supervisor_prompts: list[str] = []
        self.supervisor_entry_counts: list[tuple[int, int]] = []

    def invoke(self, role, prompt, thread_id, model, provider):
        if role == "attention_reviewer":
            self.supervisor_prompts.append(prompt)
            self.supervisor_entry_counts.append((self.proposals, len(self.reviews)))
            return self.ensure_thread(role, thread_id, model, provider), json.dumps({
                "action": self.decision,
                "reason": "One unused Goal-aligned route can address the rejected candidate's test gap.",
                "guidance": "Propose a fresh Goal-aligned candidate with an independently discriminating test under the original allowed inputs.",
            })
        thread, raw = super().invoke(role, prompt, thread_id, model, provider)
        value = json.loads(raw)
        if role == "research_lead" and value.get("candidates"):
            self.proposals += 1
            for item in value["candidates"]:
                item["hypothesis"] += f" proposal {self.proposals}"
        if role == "challenger" and value.get("reviews"):
            self.reviews.append([item["candidate_id"] for item in value["reviews"]])
            if not self.accept_retry or self.proposals <= 3:
                for review in value["reviews"]:
                    review.update(verdict="reject", reason="The proposed test does not distinguish the Goal-aligned alternatives.")
        return thread, json.dumps(value)


def workflow(tmp_path: Path, backend: QualificationBackend) -> Workflow:
    plan = tmp_path / "research_plan.md"
    plan.write_text(
        "# Goal\nFind a scientific mechanism\n\n"
        "# Research Contract\nUse frozen inputs only\n\n"
        "# Final Gate\nIndependent bounded test\n\n"
        "# Required Context\n- local\n", encoding="utf-8",
    )
    binding = bootstrap(plan)
    return Workflow(TaskSpec.from_config(binding.config_path), backend, stop_before="investigator")


def test_exhausted_qualification_reaches_supervisor_and_fresh_lead(tmp_path):
    backend = QualificationBackend(accept_retry=True)
    wf = workflow(tmp_path, backend)
    with patch.dict("os.environ", {KEY: "1"}):
        state = wf._execute_cycle()

    assert state["status"] == "checkpoint_reached"
    assert state["hypothesis_locked"] is True
    assert state["qualification_status"] == "qualified"
    assert backend.proposals == 4 and len(backend.reviews) == 4
    assert len(backend.supervisor_prompts) == 1
    assert state["internal_attention_retry_count"] == 1
    assert state["qualification_proposal_rounds"] == 3
    assert len(state["qualification_attempts"]) == 3
    rejected = {candidate for attempt in state["qualification_attempts"] for candidate in attempt["candidate_ids"]}
    assert rejected.isdisjoint(backend.reviews[-1])
    assert state["hypothesis_lock"]["candidate_id"] in backend.reviews[-1]
    assert state["operator_guidance"]["source"] == "sdk_internal"
    assert len(state["qualification_resume_history"]) == 3
    assert wf.invocation_cycle_index == 1
    assert len(list_cycle_dirs(wf.spec.research_root)) == 1
    assert len(list((wf.cycle_dir / "attention").glob("sdk_internal_review_*.json"))) == 1


def test_repeated_rejection_stops_after_one_authorized_proposal(tmp_path):
    backend = QualificationBackend()
    wf = workflow(tmp_path, backend)
    with patch.dict("os.environ", {KEY: "1"}):
        state = wf._execute_cycle()

    assert state["status"] == "no_qualified_candidate"
    assert state["attention"]["code"] == "no_qualified_candidate"
    assert state["hypothesis_locked"] is False
    assert state["resumable"] is False and state["execution_active"] is False
    assert backend.proposals == 4 and len(backend.reviews) == 4
    assert len(backend.supervisor_prompts) == 1
    assert state["internal_attention_retry_count"] == 1
    assert len(state["qualification_attempts"]) == 4
    assert wf.invocation_cycle_index == 1


@pytest.mark.parametrize("enabled,decision,expected_reviews", [("0", "retry_qualification", 0), ("1", "hold", 1)])
def test_disabled_or_hold_supervisor_never_retries(tmp_path, enabled, decision, expected_reviews):
    backend = QualificationBackend(decision=decision)
    wf = workflow(tmp_path, backend)
    with patch.dict("os.environ", {KEY: enabled}):
        state = wf._execute_cycle()

    assert state["status"] == "no_qualified_candidate"
    assert state["qualification_status"] == "no_qualified_candidate"
    assert state["hypothesis_locked"] is False and state["resumable"] is False
    assert backend.proposals == 3 and len(backend.reviews) == 3
    assert len(backend.supervisor_prompts) == expected_reviews
    assert not state.get("internal_attention_retry_count")
    if expected_reviews:
        assert state["supervisor_attention_review"]["action"] == "hold"
        assert state["supervisor_attention_review"]["session_id"]
    else:
        assert "supervisor_attention_review" not in state


@pytest.mark.parametrize("initial_enabled,initial_decision", [("0", "retry_qualification"), ("1", "hold")])
def test_resume_stopped_qualification_reviews_before_new_scientific_call(tmp_path, initial_enabled, initial_decision):
    backend = QualificationBackend(decision=initial_decision)
    wf = workflow(tmp_path, backend)
    with patch.dict("os.environ", {KEY: initial_enabled}):
        stopped = wf._execute_cycle()
    cycle_id = stopped["cycle_id"]
    assert stopped["status"] == "no_qualified_candidate"
    assert backend.proposals == 3 and len(backend.reviews) == 3
    previous_review_count = len(backend.supervisor_prompts)
    backend.decision = "retry_qualification"
    backend.accept_retry = True
    resumed = Workflow(wf.spec, backend, stop_before="investigator")
    with patch.dict("os.environ", {KEY: "1"}):
        state = resumed.resume(cycle_id)

    assert len(backend.supervisor_prompts) == previous_review_count + 1
    assert backend.supervisor_entry_counts[-1] == (3, 3)
    assert state["cycle_id"] == cycle_id
    assert state["status"] == "checkpoint_reached"
    assert state["internal_attention_retry_count"] == 1
    assert backend.proposals == 4 and len(backend.reviews) == 4
    assert len(state["qualification_attempts"]) == 3
    assert len(list_cycle_dirs(wf.spec.research_root)) == 1


def test_resume_after_used_retry_does_not_call_models_again(tmp_path):
    backend = QualificationBackend()
    wf = workflow(tmp_path, backend)
    with patch.dict("os.environ", {KEY: "1"}):
        stopped = wf._execute_cycle()
    assert stopped["internal_attention_retry_count"] == 1
    assert backend.proposals == 4 and len(backend.reviews) == 4
    resumed = Workflow(wf.spec, backend, stop_before="investigator")
    with patch.dict("os.environ", {KEY: "1"}):
        state = resumed.resume(stopped["cycle_id"])

    assert state["status"] == "no_qualified_candidate"
    assert state["cycle_id"] == stopped["cycle_id"]
    assert state["internal_attention_retry_count"] == 1
    assert state["supervisor_attention_review"]["action"] == "hold"
    assert len(backend.supervisor_prompts) == 1
    assert backend.proposals == 4 and len(backend.reviews) == 4
    assert len(state["qualification_attempts"]) == 4
