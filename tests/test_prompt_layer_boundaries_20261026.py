"""Instruction delivery and SDK boundaries; no model adherence is simulated."""
from pathlib import Path

import pytest

from theory_master.contract_stages import role_contract
from theory_master.hypothesis_display import TITLE_LIMIT, CLAIM_LIMIT, CONDITIONS_LIMIT
from theory_master.identity import identity_block, identities_root
from theory_master.prompt_stages import build_contract_repair_prompt
from theory_master.stage_registry import STAGE_CARDS, stage_definition_text


SCIENCE = {"hypothesis": "A defined claim", "scope": "A bounded domain", "decisive_test": "A discriminating test"}


@pytest.mark.parametrize("stage", ["frontier_proposal", "candidate_review", "selection_repair", "repair_verification"])
def test_prelock_repair_does_not_preserve_stale_judge_or_invent_lock(stage):
    prompt = build_contract_repair_prompt(stage, [{"path": "/", "message": "missing field"}], "{}", {
        "judge_decision": {"verdict": "supported"}, "hypothesis_lock": SCIENCE,
    })
    assert "Do not invent a Judge verdict or a hypothesis lock" in prompt
    assert "The supplied Judge decision is established" not in prompt
    assert "The supplied locked scientific object is established" not in prompt


def test_postlock_repair_preserves_actual_sources_without_creating_new_adjudication():
    prompt = build_contract_repair_prompt("cycle_synthesis", [], "{}", {
        "active_hypothesis": SCIENCE, "judge_decision": {"verdict": "rejected"},
    })
    assert "The supplied locked scientific object is established" in prompt
    assert "The supplied Judge decision is established source material" in prompt
    assert "without emitting a new adjudication" in prompt
    empty = build_contract_repair_prompt("cycle_synthesis", [], "{}", {"active_hypothesis": {}, "judge_decision": {}})
    assert "The supplied locked scientific object is established" not in empty
    assert "The supplied Judge decision is established" not in empty


def test_candidate_repair_binds_projected_shortlist_not_older_context():
    prompt = build_contract_repair_prompt("candidate_review", [], "{}", {
        "candidate": [{"id": "current-candidate", "frozen_objective": SCIENCE}],
        "frontier_state": {"candidates": [{"id": "obsolete-candidate"}]},
    })
    assert '"candidate_id": "current-candidate"' in prompt
    assert "obsolete-candidate" not in prompt


def test_display_stage_does_not_maintain_limit_or_fallback_protocol():
    stage = stage_definition_text("research_lead", "hypothesis_display")
    contract = role_contract("research_lead", phase="hypothesis_display")
    for field, limit in (("canvas_title", TITLE_LIMIT), ("canvas_claim", CLAIM_LIMIT), ("canvas_conditions", CONDITIONS_LIMIT)):
        assert f"maximum {limit} characters" in contract.prompt_schema[field]
        assert f"{limit} chars" not in stage
    assert "fallback" not in stage.lower()
    assert "repair fails" not in stage.lower()
    assert "repair attempts" not in contract.prompt_projection()


def test_preservation_receives_no_research_execution_prelude():
    stage = stage_definition_text("investigator", "preserve_research")
    assert "Do not run new experiments, derivations or research computations" in stage
    assert "Investigator skill routing:" not in stage
    assert "Investigator content boundary:" not in stage
    assert "唯一 Native Goal" not in identity_block("investigator")
    contract = role_contract("investigator", phase="preserve_research")
    assert "observations" in contract.prompt_schema
    assert "findings" not in contract.prompt_schema


def test_role_override_copies_match_packaged_identities():
    root = Path(__file__).resolve().parents[1]
    for name in ("research_lead", "challenger", "investigator", "literature", "judge", "reporter", "_investigator_handoff"):
        assert (root / "identities" / f"{name}.md").read_bytes() == (identities_root() / f"{name}.md").read_bytes()


def test_observer_projects_current_embedded_procedures_and_shared_display_limits():
    from web.tm_observer.stage_flow import _source_catalog, _source_stage_details

    contracts, cards, _ = _source_catalog()
    assert cards == STAGE_CARDS
    display = contracts[("research_lead", "hypothesis_display")]["prompt_schema"]
    assert display == role_contract("research_lead", phase="hypothesis_display").prompt_schema
    details = _source_stage_details()
    for pair, item in details.items():
        assert item["procedure"] == list(STAGE_CARDS[pair].get("procedure", ()))


def test_research_chat_distinguishes_authorities():
    prompt = identity_block("research_chat")
    assert "Research Plan for the Goal" in prompt
    assert "canonical SQLite science graph and formal Judge/Goal Gate records" in prompt
    assert "Canvas is a browsing and navigation projection" in prompt


def test_common_scientific_identity_allows_nonconstructive_existence_proofs():
    # An existence proof need not construct a witness; failed finite search
    # still cannot refute existence over the whole admissible family.
    prompt = identity_block("research_lead")
    assert "有效证明或合格实例支持" in prompt
    assert "非构造性存在证明同样有效" in prompt
    assert "有限搜索无结果不能充当一般性反证" in prompt
    assert "存在性命题只能由合格实例支持" not in prompt
