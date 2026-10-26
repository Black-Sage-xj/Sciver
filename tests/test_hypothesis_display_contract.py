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
    assert len(summary) <= 240
    assert is_compliant_hypothesis_summary(summary)


def test_fallback_displays_the_original_frozen_hypothesis_verbatim():
    long_hypothesis = "在固定制备规则和共同原生力域内，" + "具体命题" * 80
    title, summary = fallback_hypothesis_display(
        "H-0017", preferred_title="简短标题", long_text=long_hypothesis,
    )
    assert title == "简短标题"
    assert len(title) <= 40
    assert long_hypothesis in summary
    assert summary.startswith("完整命题（原文）：")
    assert summary.endswith("；状态：待检验。")
    assert "摘要暂不可用" not in summary
    assert is_compliant_hypothesis_summary(summary)
    assert summary_with_current_status(summary, "supported", adjudicated=True).endswith("；状态：已支持（仅限冻结范围）。")


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
    # The Stage owns faithful compression; the Contract owns field names.
    assert "concrete claim and indispensable applicability conditions separately" in card
    assert "frozen_hypothesis" in card
    assert "canvas_summary" not in contract.prompt_schema
    contract.validate({"canvas_title": "A title", "canvas_claim": "A specific claim",
                       "canvas_conditions": "A bounded scope"}, {})
    with __import__("pytest").raises(ValueError):
        contract.validate({"canvas_title": "A title", "canvas_summary": "Unstructured summary"}, {})


def test_display_limits_are_40_80_80_and_errors_report_actual_limit_and_excess():
    from theory_master.contract_stages import _hypothesis_display_structure, role_contract
    from theory_master.hypothesis_display import TITLE_LIMIT, CLAIM_LIMIT, CONDITIONS_LIMIT

    assert (TITLE_LIMIT, CLAIM_LIMIT, CONDITIONS_LIMIT) == (40, 80, 80)
    schema = role_contract("research_lead", phase="hypothesis_display").prompt_schema
    assert "40 characters" in schema["canvas_title"]
    assert "80 characters" in schema["canvas_claim"]
    assert "80 characters" in schema["canvas_conditions"]
    good = {"canvas_title": "t" * 40, "canvas_claim": "c" * 80, "canvas_conditions": "x" * 80}
    _hypothesis_display_structure(good, {})
    assert len(compose_hypothesis_summary(good["canvas_claim"], good["canvas_conditions"])) <= 240
    for field, limit in (("canvas_title", 40), ("canvas_claim", 80), ("canvas_conditions", 80)):
        with __import__("pytest").raises(ValueError) as exc:
            _hypothesis_display_structure({**good, field: "z" * (limit + 3)}, {})
        message = str(exc.value)
        assert f"{limit + 3} characters" in message
        assert f"limit is {limit}" in message
        assert "exceeds by 3 characters" in message
