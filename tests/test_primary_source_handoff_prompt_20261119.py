"""Offline source-acquisition delivery and existing routing; no model adherence claim."""
from __future__ import annotations

import json

import pytest

from theory_master.contract_stages import role_contract
from theory_master.prompt_stages import build_continuation_prompt, build_initial_prompt, stage_context
from theory_master.skills import developer_instructions
from theory_master.stage_registry import stage_definition_text
from tests.test_literature_request_exhaustion import make_workflow


GRAPH = {"schema": "theorymaster/agent-science-graph/v1", "hypotheses": [],
         "current_frontiers": [], "lineage": [], "external_sources": []}
SOURCE_ROUTE_RULE = (
    "A DOI/URL alone is no acquisition route for a local-only Investigator; "
    "verify necessary source passages or a scoped route available to the actual executor."
)


def assembled(role, stage):
    context = stage_context(role, stage, {
        "goal": "Compare two source-defined lattice ensembles.",
        "research_contract": "Verify the source definitions before numerical work.",
        "final_gate": "Resolve the comparison within the authorized inputs.",
        "agent_graph": GRAPH,
    })
    contract = role_contract(role, phase=stage)
    args = {"role": role, "stage": stage, "contract_ref": contract.ref,
            "contract_schema": contract.prompt_projection(context), "context": context}
    return (build_initial_prompt(prefix="offline", write_rule="read only", **args),
            build_continuation_prompt(**args))


def test_lead_acquires_indispensable_external_definitions_before_proposal():
    for prompt in assembled("research_lead", "frontier_proposal"):
        assert "For missing indispensable external definitions, request targeted_source" in prompt
        assert "via needs_literature=true, with its DOI/URL in new_search_lead" in prompt
        assert "Investigator has no general external Literature route" in prompt
        assert "Use returned passages/access limits, not metadata as input" in prompt
        assert "never repeat an exhausted detour" in prompt
        assert "Local sources sufficient for this decision do not require an external search" in prompt
        assert "targeted_source for a known claim" in prompt
        assert "decide the proposition yourself" in prompt
        assert "make Literature mandatory each Cycle" in prompt
        assert "full proof/test remains Investigator work" in prompt


@pytest.mark.parametrize("stage", ["candidate_review", "repair_verification"])
def test_challenger_checks_the_actual_executor_source_route(stage):
    for prompt in assembled("challenger", stage):
        assert prompt.count(SOURCE_ROUTE_RULE) == 1
        assert "Do not execute the full decisive test" in prompt or "perform the full decisive test" in prompt
        assert "unfinished science with a credible route" in prompt or "eventual negative/open result" in prompt


def test_investigator_authority_and_current_delivery_budgets_remain_intact():
    identity = developer_instructions("investigator")
    assert "外部学术检索属于 Literature；本地材料调查属于 Investigator" in identity
    assert "使用当前获准的 Literature 路径" in identity
    assert len(stage_definition_text("research_lead", "frontier_proposal")) < 12500
    assert len(stage_definition_text("challenger", "candidate_review")) < 10500


def test_existing_targeted_source_detour_runs_before_qualification(tmp_path, monkeypatch):
    workflow, backend, requests = make_workflow(tmp_path, {1})
    original = backend.invoke
    observed = []

    def invoke(role, prompt, thread_id, model, provider):
        observed.append((role, prompt))
        thread, raw = original(role, prompt, thread_id, model, provider)
        if role == "research_lead" and "Current task: frontier_proposal" in prompt:
            output = json.loads(raw)
            if output.get("needs_literature"):
                output["literature_request"].update({
                    "task_type": "targeted_source",
                    "search_target": "Verify the paper's source-defined model and observable.",
                    "why_external_needed": "Only an abstract is supplied; model definitions are indispensable.",
                    "expected_external_evidence": "Traceable primary passages or explicit access limits.",
                    "new_search_lead": "DOI 10.0000/example",
                })
                raw = json.dumps(output)
        return thread, raw

    monkeypatch.setattr(backend, "invoke", invoke)
    result = workflow.run()
    assert result["status"] == "checkpoint_reached"
    assert result["qualification_status"] == "qualified"
    assert len(requests) == 1 and requests[0]["task_type"] == "targeted_source"
    assert requests[0]["new_search_lead"] == "DOI 10.0000/example"
    assert result["lead_literature_detours"] == {"frontier_proposal": 1}
    assert backend.roles.count("literature") == 1
    assert backend.roles.index("literature") < backend.roles.index("challenger")
    assert "investigator" not in backend.roles and "judge" not in backend.roles
    assert any("Use returned passages/access limits" in prompt for role, prompt in observed
               if role == "research_lead" and "Current task: frontier_proposal" in prompt)
    assert any(SOURCE_ROUTE_RULE in prompt for role, prompt in observed if role == "challenger")


def test_local_inputs_can_still_reach_qualification_without_a_detour(tmp_path):
    workflow, backend, requests = make_workflow(tmp_path, set())
    result = workflow.run()
    assert result["status"] == "checkpoint_reached"
    assert result["qualification_status"] == "qualified"
    assert requests == []
    assert "literature" not in backend.roles
    assert "investigator" not in backend.roles and "judge" not in backend.roles
