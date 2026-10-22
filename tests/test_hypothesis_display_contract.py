from __future__ import annotations

from theory_master.hypothesis_display import (
    compose_hypothesis_summary,
    fallback_hypothesis_display,
    is_compliant_hypothesis_summary,
    normalize_hypothesis_display,
    summary_with_current_status,
)
from theory_master.science_graph_store import ScienceGraphStore


def test_summary_requires_separate_claim_conditions_and_sdk_owned_status():
    summary = compose_hypothesis_summary(
        "到达时间差能否解释负谷加深",
        "同制备律网络、共同原生力域及冻结第6至8代检验",
    )
    assert summary.startswith("主张：到达时间差能否解释负谷加深；条件：")
    assert summary.endswith("；状态：待检验。")
    assert len(summary) <= 160
    assert is_compliant_hypothesis_summary(summary)


def test_safe_fallback_never_substitutes_the_full_hypothesis():
    long_hypothesis = "在固定制备规则和共同原生力域内，" + "具体命题" * 80
    title, summary = fallback_hypothesis_display(
        "H-0017", preferred_title=long_hypothesis,
    )
    assert title == "H-0017 · 摘要暂不可用"
    assert len(title) <= 48
    assert len(summary) <= 160
    assert "摘要暂不可用" in summary
    assert "状态：待检验" in summary
    assert long_hypothesis not in summary


def test_current_whole_h_verdict_updates_only_the_epistemic_label():
    summary = compose_hypothesis_summary("结构量预测绝对能量差", "仅限冻结网络族和检验范围")
    updated = summary_with_current_status(summary, "supported", adjudicated=True)
    assert updated.startswith("主张：结构量预测绝对能量差；条件：仅限冻结网络族和检验范围；")
    assert updated.endswith("状态：已支持（仅限冻结范围）。")
    assert "已反驳" not in updated


def test_graph_view_reflects_judge_status_without_llm_rewriting_claim(tmp_path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        summary = compose_hypothesis_summary("预测负谷的绝对幅值", "固定制备协议与冻结力域")
        store.add_hypothesis("H-0001", title="负谷绝对幅值预测", summary=summary)
        before = next(n for n in store.graph_view()["nodes"] if n["id"] == "H-0001")
        assert before["summary"].endswith("状态：待检验。")
        store.append_outcome(
            outcome_id="1-decision", hypothesis_id="H-0001", cycle_id="1",
            verdict="supported", summary="该预测在冻结范围内得到支持",
            decisive_test="检查冻结的绝对幅值判据",
            judge_rationale="判据满足", record_only=False,
        )
        after = next(n for n in store.graph_view()["nodes"] if n["id"] == "H-0001")
        assert after["summary"].startswith("主张：预测负谷的绝对幅值；条件：固定制备协议与冻结力域；")
        assert after["summary"].endswith("状态：已支持（仅限冻结范围）。")


def test_stage_prompt_and_schema_require_claim_conditions_not_freeform_summary():
    from theory_master.contract_stages import role_contract
    from theory_master.stage_registry import stage_card_text

    contract = role_contract("research_lead", phase="hypothesis_display")
    assert set(contract.prompt_schema) == {"canvas_title", "canvas_claim", "canvas_conditions"}
    card = stage_card_text("research_lead", "hypothesis_display")
    assert "canvas_claim" in card
    assert "canvas_conditions" in card
    assert "free-form canvas_summary" in card
