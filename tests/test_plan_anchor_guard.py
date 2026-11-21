import pytest

from theory_master.plan_anchor_guard import require_explicit_plan_grid


CONTEXT = {
    "research_contract": "拟议 W 网格须对照原文独立无序相界的量级覆盖两条边界附近。",
    "required_context": (
        "所选 BDI 式 (5) 的原文独立无序参考相界约为 "
        "Wc(x)=27.24、Wc(z)=29.45；同一论文 AIII 式 (8) 另有 "
        "Wc(x)≈9.14、Wc(z)≈9.8，不能移作本轮 BDI 相界。"
    ),
}


def test_selected_model_reference_pair_is_required():
    with pytest.raises(ValueError, match="Wc\\(x\\)=27.24, Wc\\(z\\)=29.45"):
        require_explicit_plan_grid({"decisive_test": "Use W=8.00, 8.25, ..., 11.00."}, CONTEXT)
    require_explicit_plan_grid({"decisive_test": "Use W=25,25.5,…,32."}, CONTEXT)


def test_valid_interval_in_the_authors_notation_is_left_to_the_reviewer():
    require_explicit_plan_grid(
        {"decisive_test": "Sample W in [25, 32], increment 0.25."}, CONTEXT,
    )


def test_other_plans_do_not_inherit_this_numeric_gate():
    require_explicit_plan_grid(
        {"decisive_test": "Use W=8…11."},
        {**CONTEXT, "research_contract": "Study a local method; no full grid required."},
    )
