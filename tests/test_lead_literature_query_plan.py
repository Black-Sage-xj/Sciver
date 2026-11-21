"""Lead query plans survive the existing offline scholarly request pipeline."""
from copy import deepcopy

import pytest

from theory_master.contract_closure import close_agent_output
from theory_master.contract_stages import role_contract
from theory_master.scholarly_request import scholarly_request_identity
from theory_master.workflow import Workflow
from test_lead_literature_scoping_prompt_20261103 import prompts


def request():
    return {"needs_literature": True, "literature_request": {
        "task_type": "landscape", "search_target": "材料数据 ABC 的时间变化",
        "why_external_needed": "The supplied sources lack acquisition provenance.",
        "expected_external_evidence": "Primary acquisition and access records.",
        "search_queries": ["material sensor temporal drift dataset",
                           "independent repeated sensor measurements"],
        "relevance_terms": ["sensor", "drift", "acquisition"],
    }}


def test_query_plan_survives_validation_closure_queue_and_request_identity():
    output = request()
    role_contract("research_lead", phase="frontier_proposal").validate(output, {})
    closed = close_agent_output("research_lead", "frontier_proposal", output, {}, {})
    normalized = Workflow._normalize_literature_task(
        {**closed["literature_request"], "source_gap_id": "fixture-gap"},
        triggered_by="research_lead")
    frozen = scholarly_request_identity(normalized)["request"]
    for field in ("search_queries", "relevance_terms"):
        assert frozen[field] == output["literature_request"][field]
    closed["literature_request"]["search_queries"].append("local modification")
    assert len(output["literature_request"]["search_queries"]) == 2


@pytest.mark.parametrize("field,value", [
    ("search_queries", "one string"), ("search_queries", []),
    ("search_queries", ["query"] * 7), ("search_queries", ["x"]),
    ("search_queries", ["x" * 181]), ("search_queries", [False]),
    ("search_queries", ["query\x00"]),
    ("relevance_terms", "one string"), ("relevance_terms", []),
    ("relevance_terms", ["term"] * 13), ("relevance_terms", [" "]),
    ("relevance_terms", ["x" * 81]), ("relevance_terms", [3]),
])
def test_bad_query_plans_fail_before_transport(field, value):
    output = deepcopy(request())
    output["literature_request"][field] = value
    with pytest.raises(ValueError, match=field):
        role_contract("research_lead", phase="frontier_proposal").validate(output, {})


def test_historical_requests_without_query_plan_remain_valid():
    output = request()
    del output["literature_request"]["search_queries"]
    del output["literature_request"]["relevance_terms"]
    role_contract("research_lead", phase="frontier_proposal").validate(output, {})
    closed = close_agent_output("research_lead", "frontier_proposal", output, {}, {})
    assert "search_queries" not in closed["literature_request"]


def test_query_planning_instructions_reach_initial_and_continuation_prompts():
    for prompt in prompts("research_lead", "frontier_proposal"):
        assert "search_queries" in prompt and "relevance_terms" in prompt
        assert "English queries for English-language indexes" in prompt
        assert "not evidence, a scientific result or another Literature detour" in prompt
