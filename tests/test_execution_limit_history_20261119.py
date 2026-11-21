"""Input limitation history remains operational context, never scientific Evidence."""
from __future__ import annotations

import json

import pytest

from theory_master.execution_limit_history import recent_execution_limits
from theory_master.prompt_stages import stage_context
from theory_master.scientific_objective import frozen_science_digest


def archive(root, number, *, verdict=None, assessment="not_adjudicated",
            kind="input_support_unavailable"):
    cycle = root / str(number)
    cycle.mkdir(parents=True)
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\nfixture\n")
    science = {"hypothesis": "Response follows input", "scope": "Frozen source A",
               "decisive_test": "Fit only qualified observations"}
    report = {
        "kind": kind,
        "frozen_objective_sha256": frozen_science_digest(science),
        "reason": "No qualified observations in the authorized source.",
        "unexecuted_test": "The prescribed coefficient comparison was not executed.",
        "required_dependency": "Additional authorized qualified observations.",
    }
    if kind == "authorization_pending":
        report.update(
            authorization_clause="The numerical test requires separate operator authorization.",
            deliverable_clause="Deliver a reviewable method plan before that authorization.",
        )
    closure = {"kind": kind, "reason": report["reason"],
               "execution_limit": report}
    state = {"status": "over", "scientific_verdict": verdict, "assessment_state": assessment,
             "hypothesis_lock": {"id": "H-1", **science}, "operational_only_closure": closure}
    (cycle / "operational_closure.json").write_text(json.dumps(closure))
    (cycle / "research_state.json").write_text(json.dumps(state))
    return cycle, state, closure


def test_recent_limits_reach_lead_with_source_and_without_verdict(tmp_path):
    for number in range(1, 10):
        archive(tmp_path, number)
    rows = recent_execution_limits(tmp_path, current_cycle="9", limit=2)
    assert [row["cycle_id"] for row in rows] == ["8", "7"]
    assert rows[0]["hypothesis_id"] == "H-1"
    assert rows[0]["assessment_state"] == "not_adjudicated"
    assert rows[0]["source_path"] == str(tmp_path / "8/operational_closure.json")
    assert "Additional authorized" in rows[0]["required_dependency"]
    projected = stage_context("research_lead", "frontier_proposal", {
        "recent_execution_limits": rows, "agent_graph": {},
    })
    assert projected["recent_execution_limits"] == rows
    assert "recent_execution_limits" not in stage_context("judge", "terminal_assessment", {
        "recent_execution_limits": rows,
    })


def test_authorization_hold_reaches_next_lead_as_operational_context(tmp_path):
    archive(tmp_path, 1, kind="authorization_pending")
    rows = recent_execution_limits(tmp_path, current_cycle="2")
    assert len(rows) == 1
    assert rows[0]["kind"] == "authorization_pending"
    assert "no scientific verdict" in rows[0]["evidence_status"]


@pytest.mark.parametrize("defect", ["verdict", "assessment", "mismatch", "science", "symlink", "corrupt"])
def test_unbound_or_scientific_records_are_not_execution_history(tmp_path, defect):
    cycle, state, closure = archive(tmp_path, 1)
    state_path = cycle / "research_state.json"
    closure_path = cycle / "operational_closure.json"
    if defect == "verdict":
        state["scientific_verdict"] = "open"
    elif defect == "assessment":
        state["assessment_state"] = "terminal"
    elif defect == "mismatch":
        state["operational_only_closure"] = {}
    elif defect == "science":
        state["hypothesis_lock"]["scope"] = "Changed scope"
    elif defect == "symlink":
        other = tmp_path / "other.json"
        other.write_text(closure_path.read_text())
        closure_path.unlink()
        closure_path.symlink_to(other)
    elif defect == "corrupt":
        closure_path.write_text("[]")
    state_path.write_text(json.dumps(state))
    assert recent_execution_limits(tmp_path, current_cycle="2") == []


def test_next_cycle_serializes_limitation_into_actual_lead_prompt(tmp_path):
    from theory_master.backends import FakeBackend
    from theory_master.specification import TaskSpec
    from theory_master.task import bootstrap
    from theory_master.workflow import Workflow

    class CaptureBackend(FakeBackend):
        def __init__(self):
            super().__init__(reworks=0)
            self.lead_inputs = []

        def invoke(self, role, prompt, thread_id, model, provider):
            if role == "research_lead" and "Current task: frontier_proposal" in prompt:
                self.lead_inputs.append(json.JSONDecoder().raw_decode(prompt.split("Context:\n", 1)[1])[0])
            return super().invoke(role, prompt, thread_id, model, provider)

    plan = tmp_path / "research_plan.md"
    plan.write_text("# Goal\nIdentify the response\n\n# Research Contract\nFrozen sources\n\n"
                    "# Final Gate\nA discriminating independent test\n\n# Required Context\nLocal inputs\n")
    binding = bootstrap(plan)
    spec = TaskSpec.from_config(binding.config_path)
    archive(spec.research_root, 1)
    backend = CaptureBackend()
    state = Workflow(spec, backend, stop_before="investigator")._execute_cycle()
    assert state["status"] == "checkpoint_reached"
    assert state["cycle_id"] == "2"
    report = backend.lead_inputs[0]["recent_execution_limits"][0]
    assert report["cycle_id"] == "1" and report["assessment_state"] == "not_adjudicated"
    assert report["required_dependency"] == "Additional authorized qualified observations."
