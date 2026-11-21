"""The live qualification prompt must respect a staged research contract."""

from theory_master.prompt_stages import build_initial_prompt


METHOD = "第一轮仅核对资料、设计方案和估算成本，不执行数值实验。后续数值阶段须另行授权。"
GATE = "第一轮交付可审阅数值方案，包含原文核对、三组对照与成本估算。"


def _prompt(role: str, stage: str, contract: str = METHOD, gate: str = GATE) -> str:
    return build_initial_prompt(
        prefix="Test",
        role=role,
        stage=stage,
        contract_ref="stage-contract",
        contract_schema="{}",
        context={"research_contract": contract, "final_gate": gate},
        write_rule="",
    )


def test_lead_can_propose_current_method_milestone_without_numeric_run():
    prompt = _prompt("research_lead", "frontier_proposal")
    assert METHOD in prompt and GATE in prompt
    assert "method-only milestone" in prompt
    assert "sample/propagation counts" not in prompt
    assert "real-versus-complex FLOP" not in prompt


def test_challenger_reviews_current_stage_cost_instead_of_requiring_smoke():
    for stage in ("candidate_review", "repair_verification"):
        prompt = _prompt("challenger", stage)
        assert METHOD in prompt and GATE in prompt
        for detail in ("trajectory counts", "symmetry class", "periodic aliasing"):
            assert detail not in prompt
        physics = "Verify periodic aliasing at the smallest specified lattice size."
        explicit = _prompt("challenger", stage, physics, "Return that finite-size check.")
        assert physics in explicit


def test_judge_authority_is_not_relaxed_by_method_stage_guidance():
    judge = _prompt("judge", "terminal_assessment")
    assert "Adjudicate the exact locked Hypothesis" in judge
    assert "A terminal open requires that the test has actually been executed" in judge
    assert "method-only milestone" not in judge
    assert "later numerical hypothesis as unadjudicated" not in judge
