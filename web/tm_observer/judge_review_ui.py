"""Read-only Judge decision detail/history sourced from canonical Cycle SDK events."""
from __future__ import annotations

import html
import json
from typing import Any

from .important_events import _scrub
from .run_monitor import _safe_cycle

MAX_LOG_BYTES = 12_000_000
MAX_RECORD_BYTES = 180_000
MAX_JUDGE_ROWS = 25


def read_judge_reviews(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Only explicit role.completed/terminal_assessment rows count as reviews.

    Never infer scientific verdict from TUI status=done or a rework counter.
    """
    cycle = _safe_cycle(run)
    if cycle is None:
        return []
    source = cycle / "events.jsonl"
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_LOG_BYTES:
            return []
        result = []
        with source.open('rb') as handle:
            for line in handle:
                if len(line) > MAX_RECORD_BYTES:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(record, dict) or record.get('type') != 'role.completed':
                    continue
                payload = record.get('payload')
                if not isinstance(payload, dict) or payload.get('role') != 'judge':
                    continue
                output = payload.get('output')
                if not isinstance(output, dict):
                    continue
                result.append({
                    'at': _scrub(record.get('at') or ''),
                    'stage': _scrub(payload.get('stage') or ''),
                    'output': _scrub(output),
                    'seq': record.get('seq') if type(record.get('seq')) is int else None,
                })
        return result[-MAX_JUDGE_ROWS:]
    except (OSError, UnicodeError):
        return []


def _e(value: Any) -> str:
    return html.escape(str(value) if value is not None else '未登记', quote=True)


def render_judge_review_template(rows: list[dict[str, Any]], cycle: Any) -> str:
    """Static modal template; the active Run pane only carries read-only markup."""
    out = ['<template data-judge-review-template>'
           '<header class="tm-budget-modal-header"><div><h2>Judge · 裁决详情</h2>'
           '<p>Cycle ' + _e(cycle) + ' · SDK 科学裁决记录（只读）</p></div>'
           '<button type="button" class="tm-budget-modal-close" data-judge-modal-close>关闭</button></header>']
    if not rows:
        out.append('<p class="tm-budget-modal-empty" role="status">当前 Cycle 尚未读取到正式 Judge 裁决记录。'
                   '执行状态 done 或 Rework 计数不等于已经作出裁决；可到 Stage Conversation 查看原始流程。</p>')
    else:
        if len(rows) > 1:
            out.append('<nav class="tm-budget-modal-choices" role="group" aria-label="选择 Judge 裁决记录">')
            for i in range(len(rows)):
                out.append('<button type="button" data-judge-review-select="' + str(i) +
                           '" aria-pressed="' + ('true' if i == len(rows)-1 else 'false') +
                           '">第 ' + str(i+1) + ' 次 · ' +
                           ('目标完成审查' if rows[i].get('stage') == 'goal_completion' else '分支裁决') + '</button>')
            out.append('</nav>')
        for i, entry in enumerate(rows):
            data = entry['output']
            verdict = data.get('verdict')
            label = str(verdict) if verdict not in (None, '', 'null') else '未登记（不可从 done 推断）'
            rationale = str(data.get('rationale') or data.get('reason') or '裁决理由未登记')
            out.append('<section data-judge-review-panel="' + str(i) + '"' +
                       ('' if i == len(rows)-1 else ' hidden') + '>'
                       '<div class="tm-judge-modal-verdict"><span>裁决结果</span><strong>' + _e(label) + '</strong></div>'
                       '<p class="tm-judge-modal-source">' + _e(entry.get('at')) +
                       ' · ' + _e(entry.get('stage')) + ' · SDK role.completed</p>'
                       '<h3>科学裁决理由</h3><div class="tm-judge-modal-reason">' + _e(rationale) + '</div>')
            for key, title in [('objection', '未解决异议'), ('requested_resolution', '要求解决的问题')]:
                value = data.get(key)
                if value not in (None, '', [], {}):
                    out.append('<h3>' + title + '</h3><pre class="tm-judge-modal-raw">' +
                               _e(json.dumps(value, ensure_ascii=False, indent=2, default=str)) + '</pre>')
            out.append('<details class="tm-judge-modal-full"><summary>Judge 结构化输出（只读、安全展示）</summary>'
                       '<pre class="tm-judge-modal-raw">' +
                       _e(json.dumps(data, ensure_ascii=False, indent=2, default=str)) +
                       '</pre></details></section>')
    return ''.join(out) + '</template>'


def dialog() -> str:
    return '''<style>
.tm-judge-review-count { border:1px solid #876ca4; background:#392747; color:#e9d5ff; border-radius:7px;
  padding:5px 10px; font:inherit; font-size:11px; font-weight:650; cursor:pointer; }
.tm-judge-review-count:hover { background:#493057; }
.tm-judge-review-count:focus-visible { outline:2px solid #c084fc; outline-offset:2px; }
.tm-judge-review-dialog { box-sizing:border-box; width:min(820px,calc(100vw - 32px)); max-width:calc(100vw - 32px);
  max-height:80dvh; margin:auto; overflow:auto; background:#171e2b; color:#e8edf5; border:1px solid #62748b;
  border-radius:14px; padding:22px; box-shadow:0 22px 75px #000a; }
.tm-judge-review-dialog::backdrop { background:rgba(0,0,0,.74); }
.tm-judge-review-dialog .tm-budget-modal-header { display:flex; justify-content:space-between; gap:12px; margin-bottom:17px; }
.tm-judge-review-dialog h3 { color:#c4b5fd; font-size:12px; margin:16px 0 6px; }
.tm-judge-review-dialog .tm-judge-modal-verdict { display:flex; justify-content:space-between; gap:12px; align-items:baseline; }
.tm-judge-modal-verdict strong { color:#f5d0fe; overflow-wrap:anywhere; }
.tm-judge-modal-source { font-size:11px; color:#9ca3af; }
.tm-judge-modal-reason { font-size:13px; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
.tm-judge-modal-raw { white-space:pre-wrap; overflow-wrap:anywhere; font:11px/1.7 ui-monospace,monospace;
  padding:12px; border:1px solid #43516a; border-radius:7px; background:#0f172a; }
.tm-judge-review-dialog [hidden] { display:none!important; }
.tm-run-quota-status { min-height:12px; color:#9ca3af; font-size:10px; line-height:1.3;
 white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tm-judge-modal-full { margin-top:17px; }
.tm-judge-modal-full summary { cursor:pointer; color:#c4b5fd; }
</style><dialog id="tm-judge-review-dialog" class="tm-judge-review-dialog" aria-label="Judge 裁决详情">
<div data-judge-modal-content></div></dialog>'''
