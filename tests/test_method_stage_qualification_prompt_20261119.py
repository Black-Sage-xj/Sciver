"""The live qualification prompt must respect a staged research contract."""

from theory_master.prompt_stages import build_initial_prompt


def _prompt(role: str, stage: str) -> str:
    return build_initial_prompt(
        prefix="Test",
        role=role,
        stage=stage,
        contract_ref="stage-contract",
        contract_schema="{}",
        context={
            "research_contract": (
                "第一轮仅核对资料、设计方案和估算成本，不执行数值实验。"
                "后续数值阶段须另行授权。"
            ),
            "final_gate": "第一轮交付可审阅数值方案，包含原文核对、三组对照与成本估算。",
        },
        write_rule="",
    )


def test_lead_can_propose_current_method_milestone_without_numeric_run():
    prompt = _prompt("research_lead", "frontier_proposal")
    assert "method-only milestone" in prompt
    assert "current decisive result is the reviewable method deliverable" in prompt
    assert "proposed finite parameters and sample/propagation counts" in prompt
    assert "no-benchmark time/memory estimate" in prompt
    assert "unadjudicated numerical H" in prompt
    assert "plausibly cover both" in prompt
    assert "periodic wrap and displacement aliasing" in prompt
    assert "real-versus-complex FLOP convention" in prompt
    assert "Values from another model in the same paper" in prompt


def test_challenger_reviews_current_stage_cost_instead_of_requiring_smoke():
    review = _prompt("challenger", "candidate_review")
    repair = _prompt("challenger", "repair_verification")
    assert "feasibility of the currently authorized Cycle deliverable" in review
    assert "do not demand a smoke run" in review
    assert "An adjacent analytic claim is insufficient" in review
    assert "cheap grid wholly outside that region" in review
    assert "Independently recompute trajectory counts" in review
    assert "A nearby row for a different class or model" in review
    assert "do not convert a paper cost estimate" in repair
    assert "preserve any unexecuted numerical claim as unadjudicated" in repair
    assert "blocker closure and the binding revised test must agree" in repair
    assert "same model, symmetry class and disorder channel" in repair


def test_judge_authority_is_not_relaxed_by_method_stage_guidance():
    judge = _prompt("judge", "terminal_assessment")
    assert "Adjudicate the exact locked Hypothesis" in judge
    assert "method-only milestone" not in judge
    assert "later numerical hypothesis as unadjudicated" not in judge
