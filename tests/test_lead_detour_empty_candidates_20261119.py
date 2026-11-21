"""An empty frontier placeholder must not reverse a Literature request."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from theory_master.contract_closure import close_agent_output
from theory_master.contract_stages import role_contract
from tests.test_literature_request_exhaustion import make_workflow


def request():
    return {
        "needs_literature": True,
        "literature_request": {
            "task_type": "targeted_source",
            "search_target": "Verify the source-defined model before proposing a dependent claim.",
            "why_external_needed": "The local input contains only the abstract.",
            "expected_external_evidence": "Traceable primary equations or explicit access limits.",
            "new_search_lead": "DOI 10.0000/example",
        },
    }


def test_empty_candidates_validate_and_close_to_the_requested_detour():
    contract = role_contract("research_lead", phase="frontier_proposal")
    output = {**request(), "candidates": []}
    authored = copy.deepcopy(output)
    contract.validate(output, {})
    closed = close_agent_output("research_lead", "frontier_proposal", output, {}, {})
    assert output == authored
    assert closed == request()
    contract.validate(closed, {})


@pytest.mark.parametrize("candidates", [[{"hypothesis": "A competing proposal"}], None, {}, "", False])
def test_actual_frontier_content_and_wrong_types_remain_mutually_exclusive(candidates):
    contract = role_contract("research_lead", phase="frontier_proposal")
    with pytest.raises(ValueError, match="mutually exclusive"):
        contract.validate({**request(), "candidates": candidates}, {})


def test_empty_placeholder_does_not_skip_other_contract_validation():
    contract = role_contract("research_lead", phase="frontier_proposal")
    output = {**request(), "candidates": []}
    output["literature_request"].pop("new_search_lead")
    with pytest.raises(ValueError, match="new_search_lead"):
        contract.validate(output, {})
    output = {**request(), "candidates": [], "unowned_route": "investigator"}
    with pytest.raises(ValueError, match="unsupported fields"):
        contract.validate(output, {})
    with pytest.raises(ValueError, match="1..3 candidates"):
        contract.validate({"needs_literature": False, "candidates": []}, {})


def test_empty_placeholder_routes_to_literature_before_qualification_without_repair(tmp_path, monkeypatch):
    workflow, backend, requests = make_workflow(tmp_path, {1})
    original = backend.invoke

    def invoke(role, prompt, thread_id, model, provider):
        thread, raw = original(role, prompt, thread_id, model, provider)
        if role == "research_lead" and "Current task: frontier_proposal" in prompt:
            output = json.loads(raw)
            if output.get("needs_literature"):
                output["candidates"] = []
                raw = json.dumps(output)
        return thread, raw

    monkeypatch.setattr(backend, "invoke", invoke)
    result = workflow.run()
    assert result["status"] == "checkpoint_reached"
    assert result["qualification_status"] == "qualified"
    assert len(requests) == 1
    assert result["lead_literature_detours"] == {"frontier_proposal": 1}
    assert backend.frontier_calls == 2
    assert backend.roles.count("literature") == 1
    assert backend.roles.index("literature") < backend.roles.index("challenger")
    assert "investigator" not in backend.roles
    events = [
        json.loads(line)
        for line in (workflow.cycle_dir / "events.jsonl").read_text().splitlines()
    ]
    assert not any(event["type"] == "json.contract_violation" for event in events)
    assert not any(event["payload"].get("kind") == "contract_repair_retry" for event in events)
    completed = next(event["payload"] for event in events if event["type"] == "role.completed")
    closure = json.loads(Path(completed["semantic_closure"]).read_text())
    assert closure["closed_output"]["needs_literature"] is True
    assert "candidates" not in closure["closed_output"]
