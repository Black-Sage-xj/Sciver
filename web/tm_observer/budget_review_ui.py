"""Read-only, bounded Investigator budget-review ledger projection for the Web UI."""
from __future__ import annotations

import json
from typing import Any

from .important_events import _scrub
from .run_monitor import _safe_cycle

MAX_LEDGER_BYTES = 900_000
MAX_REVIEWS = 40
EXPECTED_SCHEMA = "theory-master/investigator-budget-review/v1"


def read_budget_reviews(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Read current Cycle approvals without invoking Reviewer or changing quota."""
    cycle = _safe_cycle(run)
    if cycle is None:
        return []
    ledger = cycle / "budget" / "investigator_reviews.jsonl"
    try:
        if ledger.is_symlink() or not ledger.is_file():
            return []
        if ledger.stat().st_size > MAX_LEDGER_BYTES:
            return []
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    reviews: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip() or len(line) > 60000:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict) or row.get("schema") != EXPECTED_SCHEMA:
            continue
        if row.get("decision") not in {"approve", "reject"}:
            continue
        if not all(type(row.get(k)) is int and row[k] >= 0
                   for k in ("tranche_tokens", "prior_allowance",
                             "observed_tokens", "new_allowance")):
            continue
        # Retain historic ledger amounts verbatim: older approvals may have
        # included an overshoot adjustment beyond the nominal tranche.
        if row["decision"] == "approve" and row["new_allowance"] < row["prior_allowance"]:
            continue
        if row["decision"] == "reject" and row["new_allowance"] != row["prior_allowance"]:
            continue
        # Bound the displayed object, including raw JSON. Keep the original
        # file untouched, and avoid exposing secret values through the UI.
        reviews.append(_scrub(row))
    return reviews[-MAX_REVIEWS:]



def _esc(value: Any) -> str:
    from html import escape
    return escape(str(value) if value is not None else "—", quote=True)


def _tokens(value: Any) -> str:
    return f"{value:,}" if type(value) is int else "—"


def _references(row: dict[str, Any], field: str) -> str:
    refs = row.get(field)
    if not isinstance(refs, list) or not refs:
        return '<p class="tm-budget-modal-empty">无该类登记引用</p>'
    return ('<ul class="tm-budget-modal-refs">'
            + "".join('<li><code>' + _esc(ref) + '</code></li>'
                      for ref in refs if isinstance(ref, str))
            + '</ul>')


def render_budget_review_template(reviews: list[dict[str, Any]], cycle: Any) -> str:
    """Safe static review panels; a native dialog clones the template on click."""
    result = ['<template data-budget-review-template>'
              '<header class="tm-budget-modal-header">'
              '<div><h2 id="tm-budget-modal-title">Investigator 额度审批</h2>'
              '<p>Cycle ' + _esc(cycle) + ' · 审批历史（只读）</p></div>'
              '<button type="button" class="tm-budget-modal-close" data-budget-modal-close '
              'aria-label="关闭 Investigator 额度审批">关闭</button></header>']
    if not reviews:
        result.append('<p class="tm-budget-modal-empty" role="status">'
                      '额度追加已登记，但当前 Cycle 的详细审批账本暂不可读取。'
                      '请从任务工作区核对 budget/investigator_reviews.jsonl。</p>')
    else:
        if len(reviews) > 1:
            result.append('<nav class="tm-budget-modal-choices" role="group" '
                          'aria-label="选择额度审批批次">')
            for ix, review in enumerate(reviews):
                label = "第 " + str(ix+1) + " 次 · " + (
                    "通过" if review.get("decision") == "approve" else "未批准")
                result.append('<button type="button" data-budget-review-select="' +
                              str(ix) + '" aria-pressed="' +
                              ("true" if ix == len(reviews)-1 else "false") +
                              '">' + label + '</button>')
            result.append('</nav>')
        for ix, row in enumerate(reviews):
            approved = row["decision"] == "approve"
            prior, after = row["prior_allowance"], row["new_allowance"]
            added = after-prior
            raw = json.dumps(row, ensure_ascii=False, indent=2, default=str)
            result.append(
                '<section class="tm-budget-modal-review" data-budget-review-panel="' +
                str(ix) + '"' + ("" if ix == len(reviews)-1 else " hidden") + '>'
                '<div class="tm-budget-modal-verdict"><strong class="' +
                ("tm-budget-verdict-approved" if approved else "tm-budget-verdict-rejected") +
                '">' + ("审批通过" if approved else "审批未通过") + '</strong>'
                '<time>' + _esc(row.get("at") or "时间未登记") +
                '</time></div>'
                '<div class="tm-budget-modal-figures">'
                '<div><span>审批前额度</span><strong>' + _tokens(prior) + '</strong></div>'
                '<div><span>审批时已用</span><strong>' +
                _tokens(row["observed_tokens"]) + '</strong></div>'
                '<div><span>实际额度增量</span><strong>' +
                ("+" + _tokens(added) if approved else "0") + '</strong></div>'
                '<div><span>审批后额度</span><strong>' + _tokens(after) + '</strong></div>'
                '</div><p class="tm-budget-modal-formula">名义追加批次：' +
                _tokens(row["tranche_tokens"]) +
                ' Token。增量按账本 new_allowance − prior_allowance 计算。</p>' +
                ('<p class="tm-budget-modal-variance">账本提示：审批后额度与「审批前额度 '
                 '+ 名义追加批次」不完全一致，界面保留实际登记值，不修改额度。</p>'
                 if approved and after != prior + row["tranche_tokens"] else '') +
                '<div class="tm-budget-modal-findings"><h3>审批依据摘录</h3>'
                '<p>' + _esc((str(row.get("reason") or "账本未登记理由")[:180] +
                    ("…" if len(str(row.get("reason") or "")) > 180 else ""))) +
                '</p></div>'
                '<details class="tm-budget-modal-disclosure">'
                '<summary>完整审批理由及下一项有界检验</summary>'
                '<div class="tm-budget-modal-findings"><h3>科学审查理由（账本原文）</h3>'
                '<p>' + _esc(row.get("reason") or "账本未登记理由") + '</p>'
                '<h3>下一项有界检验</h3>'
                '<p>' + _esc(row.get("next_bounded_test") or "账本未登记下一项检验") +
                '</p></div></details>'
                '<details class="tm-budget-modal-disclosure"><summary>Evidence 与研究进展索引</summary>'
                '<div class="tm-budget-modal-reference-columns">'
                '<div><h4>Evidence ID</h4>' + _references(row, "evidence_ids") + '</div>'
                '<div><h4>Investigator 进展引用</h4>' +
                _references(row, "progress_refs") + '</div></div></details>'
                '<section class="tm-budget-modal-disclosure tm-budget-trace-section" data-budget-trace-box>'
                '<h3>本次审查 Agent · 完整执行轨迹</h3>'
                '<p class="tm-budget-modal-empty">查看 Reviewer 实际收到的 Prompt、模型消息、工具调用及留存结果；'
                'Reviewer 轨迹与被审查的 Investigator 对话不同。</p>'
                '<button type="button" data-budget-trace-load data-budget-trace-cycle="' + _esc(cycle) +
                '" data-budget-trace-review="' + _esc(row.get("review_index") or ix + 1) +
                '">查看本次审查轨迹</button>'
                '<div class="tm-budget-trace-content" data-budget-trace-content aria-live="polite"></div>'
                '</section>'
                '<details class="tm-budget-modal-disclosure"><summary>完整审批账本原文（JSON）</summary>'
                '<pre>' + _esc(raw) + '</pre></details>'
                '<p class="tm-budget-modal-footnote">此处展示已登记的审批依据与结果，'
                '不表示已完成科学裁决，也不重建模型内部推理。</p>'
                '</section>'
            )
    result.append('</template>')
    return "".join(result)
