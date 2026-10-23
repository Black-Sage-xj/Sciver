"""Read-only task browsing for the local-first Sciver Run workspace.

This module only formats already discovered Task/Cycle projections. It does not
implement task controls or the independently owned ``canvas-md`` interface.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from . import run_monitor
from .run_controls_ui import control_panel, quota_auto_switch, settings_panel, can_resume_state
from .important_events import read_important_events, is_milestone, incident_cases
from .important_review_ui import script as important_review_script
from .budget_review_ui import read_budget_reviews, render_budget_review_template
from .budget_review_trace_ui import script as budget_trace_script
from .quota_auto_ui import quota_auto_card
from .run_events_ui import ROLE_NAMES
from .run_stage_ui import shell as stage_shell, script as stage_script
from .goal_ui import trigger as native_goal_trigger, modal as native_goal_modal
from .judge_review_ui import read_judge_reviews, render_judge_review_template, dialog as judge_review_dialog
from .supervisor_view import (SUPERVISOR_STYLE, banner_html as supervisor_banner,
                              quota_wait_banner, _quota_pause, index_chip as supervisor_index_chip)

PAGE_SIZE = 20
def _e(value: Any) -> str:
    return html.escape(str(value) if value is not None and value != "" else "—", quote=True)


def _relative_time(value: Any) -> str:
    if not value:
        return "未知"
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        delta = max(0, int((datetime.now(timezone.utc) - instant).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return "未知"
    if delta < 60:
        return f"{delta}秒前"
    if delta < 3600:
        return f"{delta // 60}分钟前"
    if delta < 86400:
        return f"{delta // 3600}小时前"
    return f"{delta // 86400}天前"


def _safe_page(value: str | int | None) -> int:
    try:
        return min(100_000, max(1, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _state_class(status: Any) -> str:
    status = str(status or "").upper()
    if status in {"LIVE", "RUNNING", "STARTING", "RECOVERING"}:
        return "running"
    if status in {"FAILED", "FAILED_RECOVERY", "ORPHANED", "UNRESPONSIVE"}:
        return "error"
    if status in {"NEEDS_ATTENTION", "PAUSED", "INTERRUPTED", "WAITING_QUOTA"}:
        return "attention"
    return "neutral"


def _task_url(run: dict[str, Any]) -> str:
    # Never substitute a Master ID or a task path for this stable identifier.
    ident = str(run.get("observer_id") or "")
    if len(ident) != 16 or any(c not in "0123456789abcdef" for c in ident):
        return ""
    return f"/run/{ident}/"


def _index_url(query: str, status: str, page: int) -> str:
    data: dict[str, Any] = {}
    if query:
        data["q"] = query
    if status:
        data["status"] = status
    if page != 1:
        data["page"] = page
    return "/run/" + ("?" + urlencode(data) if data else "")


def run_index_body(state: dict[str, Any], *, query: str = "", status: str = "", page: int = 1) -> str:
    """Render a bounded tm-ls-like page, without scanning task files again."""
    query = str(query or "")[:160].strip()
    status = str(status or "")[:48].strip().upper()
    all_runs = [row for row in state.get("runs", []) if isinstance(row, dict) and _task_url(row)]
    live_runs = [row for row in all_runs if str(row.get("state") or "").upper() == "LIVE"]
    live_href = (_task_url(live_runs[0]) if len(live_runs) == 1
                 else "/run/?status=LIVE" if live_runs else "")
    live_link = (f'<a class="tm-run-refresh tm-run-live-link" href="{html.escape(live_href, quote=True)}">'
                 + ("● 打开正在运行的任务" if len(live_runs) == 1
                    else f"● 查看 {len(live_runs)} 个正在运行的任务") + '</a>') if live_href else ""
    statuses = sorted({str(row.get("state") or "UNKNOWN").upper() for row in all_runs})
    if status not in statuses:
        status = ""
    filtered = [
        row for row in all_runs
        if (not query or query.casefold() in " ".join(str(row.get(key) or "") for key in ("run_id", "task_name", "cycle")).casefold())
        and (not status or str(row.get("state") or "UNKNOWN").upper() == status)
    ]
    total_pages = max(1, math.ceil(len(filtered) / PAGE_SIZE))
    page = min(_safe_page(page), total_pages)
    visible = filtered[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    options = '<option value="">所有状态</option>' + "".join(
        f'<option value="{_e(item)}"{" selected" if status == item else ""}>{_e(item)}</option>'
        for item in statuses
    )
    rows = "".join(
        f'<tr class="tm-run-clickable" data-task-url="{html.escape(_task_url(row), quote=True)}" tabindex="0" role="link" aria-label="打开任务：{_e(row.get("task_name"))}">'
        f'<td><a class="tm-run-task-link" href="{_task_url(row)}">{_e(row.get("task_name"))}</a>'
        f'<div class="tm-run-secondary">Master {_e(row.get("run_id"))}</div></td>'
        f'<td>{_e(row.get("cycle"))}</td>'
        f'<td><span class="tm-run-state tm-run-state-{_state_class(row.get("state"))}">{_e(row.get("state"))}</span>{supervisor_index_chip(row)}</td>'
        f'<td>{_e(row.get("active_agent"))}</td>'
        f'<td>{_e(_relative_time(row.get("last_activity_at")))}</td>'
        '</tr>'
        for row in visible
    ) or '<tr><td colspan="5" class="tm-run-empty">没有符合条件的任务。</td></tr>'
    pages = (
        f'<a href="{html.escape(_index_url(query, status, page - 1), quote=True)}" rel="prev">上一页</a>' if page > 1 else '<span>上一页</span>'
    ) + f'<span>{page} / {total_pages}</span>' + (
        f'<a href="{html.escape(_index_url(query, status, page + 1), quote=True)}" rel="next">下一页</a>' if page < total_pages else '<span>下一页</span>'
    )
    return f'''
<style>{SUPERVISOR_STYLE}</style>
<section class="tm-run-workspace" aria-label="Sciver tasks">
  <div class="tm-run-heading"><div><strong>任务清单</strong><p>{len(filtered)} 个任务 · 每页 {PAGE_SIZE} 个 · 只读监控</p></div>{live_link}<a class="tm-run-refresh" href="/research-chat/?new=1">＋ 建立科研项目</a><a class="tm-run-refresh" href="{html.escape(_index_url(query, status, page), quote=True)}">⟳ 刷新</a></div>
  <form class="tm-run-filters" method="get" action="/run/">
    <input name="q" value="{_e(query) if query else ''}" placeholder="搜索 Task / Master ID / Cycle" aria-label="搜索任务">
    <select name="status" aria-label="筛选状态">{options}</select>
    <button type="submit">筛选</button>
    <a href="/run/">清除</a>
  </form>
  <div class="tm-run-table-wrap"><table class="tm-run-table">
    <thead><tr><th>Task / Master</th><th>Cycle ID</th><th>State</th><th>Agent</th><th>最近活动</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  <nav class="tm-run-pagination" aria-label="任务分页">{pages}</nav>
</section>
<script>
(()=>{{
  const table=document.querySelector('.tm-run-table');
  if(!table)return;
  table.addEventListener('click',event=>{{
    if(event.target.closest('a,button,input,select,textarea'))return;
    const row=event.target.closest('tr[data-task-url]');
    if(row)window.location.assign(row.dataset.taskUrl);
  }});
  table.addEventListener('keydown',event=>{{
    const row=event.target.closest('tr[data-task-url]');
    if(row&&['Enter',' '].includes(event.key)){{event.preventDefault();window.location.assign(row.dataset.taskUrl);}}
  }});
}})();
</script>
'''


def _read_run_state(run: dict[str, Any]) -> dict[str, Any]:
    """Read only the known current-Cycle projection from the discovered task.

    The client never chooses a path. Reject links, directory escapes and large
    files. A missing/unreadable projection stays missing rather than synthetic.
    """
    cycle = run.get("cycle_dir")
    research_root = run.get("research_root")
    if not cycle or not research_root:
        return {}
    try:
        root = Path(str(research_root)).resolve(strict=True)
        current = Path(str(cycle)).resolve(strict=True)
        if not current.is_dir() or current.parent != root or not run_monitor.CYCLE_NAME.fullmatch(current.name):
            return {}
        path = current / "run_state.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            return {}
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return {}


_TUI_SECONDARY = {"events": "EVENTS · Stage Conversation", "research": "冻结 Hypothesis"}



def _frozen_hypothesis_card(raw: Any, *, worker_id: str | None = None) -> str:
    """Current Cycle's navigable scientific lock on the Run overview."""
    frozen = raw if isinstance(raw, dict) else {}
    locked = bool(frozen.get("locked"))
    title = str(frozen.get("hypothesis") or "") if locked else "当前 Cycle 尚无已冻结的 Hypothesis"
    identifier = str(frozen.get("id") or "") if locked else "尚未冻结"
    target = '?' + urlencode({**({'worker': worker_id} if worker_id else {}), 'tab': 'research'})
    return (
        '<a class="tm-hypothesis-card" href="' + _e(target) + '" data-tui-pane="hypothesis"'
        ' aria-label="查看当前冻结的 Hypothesis、Scope 和 decisive_test">'
        '<span class="tm-hypothesis-card-label">Research Lead <span>当前冻结研究对象</span></span>'
        f'<strong>{_e(identifier)}</strong>'
        f'<span class="tm-hypothesis-card-text">{_e(title)}</span>'
        '<span class="tm-hypothesis-card-action">查看 Hypothesis / Scope / decisive_test →</span>'
        '</a>'
    )


def _frozen_research_view(raw: Any) -> str:
    """Expose the authoritative frozen scientific object, not terminal prose."""
    frozen = raw if isinstance(raw, dict) else {}
    if not frozen.get("locked"):
        return ('<div class="tm-research-frozen"><p class="tm-insight-empty">'
                '当前 Cycle 尚未完成 Hypothesis Lock，暂时没有冻结的科研对象。'
                '</p></div>')
    def section(name: str, desc: str, value: Any, extra: str) -> str:
        return (
            f'<section class="tm-research-block {extra}">'
            f'<header class="tm-research-block-header"><h3>{_e(name)}</h3><span>{_e(desc)}</span></header>'
            f'<div class="tm-research-prose">{_e(str(value or "").strip() or "尚未记录")}</div>'
            '</section>'
        )
    qualification = str(frozen.get("qualification_status") or "pending")
    reason = str(frozen.get("challenger_reason") or "")
    selection = str(frozen.get("selection_rationale") or "")
    background = (
        '<details class="tm-research-background">'
        '<summary>资格审查与锁定记录'
        f'<span>Challenger · {_e(qualification)} · locked</span></summary>'
        '<div class="tm-research-background-inner">'
        f'<div><strong>Challenger 审查理由</strong><p>{_e(reason or "未记录")}</p></div>'
        f'<div><strong>候选选择说明</strong><p>{_e(selection or "未记录")}</p></div>'
        '</div></details>'
    )
    return (
        '<div class="tm-research-frozen">'
        '<header class="tm-research-intro"><div>'
        '<div class="tm-research-eyebrow">FROZEN SCIENTIFIC OBJECTIVE</div>'
        '<h2>当前冻结的 Hypothesis</h2>'
        '<p>本 Cycle 的科研命题、适用范围与决定性检验。这里只读，不修改科研状态。</p>'
        '</div>'
        f'<span class="tm-research-id">{_e(frozen.get("id") or "已冻结")}</span>'
        '</header>'
        + section("Hypothesis", "科研命题 · 本轮研究什么", frozen.get("hypothesis"), "tm-research-main")
        + section("Scope", "适用范围 · 在什么条件下检验", frozen.get("scope"), "tm-research-scope")
        + section("decisive_test", "决定性检验 · 根据什么裁决结果", frozen.get("decisive_test"), "tm-research-test")
        + background + '</div>'
    )


def _codex_quota_metric_card(snapshot: dict[str, Any] | None = None, *, worker_account: str | None = None) -> str:
    """Show last verified history on first paint (even before any network GET)."""
    data = snapshot if isinstance(snapshot, dict) else {}
    active = f'account-{worker_account}' if worker_account in {'1', '2'} else data.get('active_account')
    name = {'account-1': 'Codex 1', 'account-2': 'Codex 2'}.get(active, 'Codex 额度 · 查询中…')
    current = next((item for item in data.get('accounts', [])
                    if isinstance(item, dict) and item.get('account_home') == active
                    and item.get('ok') is True), None)
    def quota_window(window: Any) -> tuple[str, int | float] | None:
        if not isinstance(window, dict):
            return None
        value = window.get('remaining_percent')
        if type(value) not in (int, float) or not 0 <= value <= 100:
            return None
        duration = window.get('window_duration_mins')
        label = ('5 小时' if duration == 300 else '7 天' if duration == 10080 else
                 f'{duration} 分钟' if type(duration) is int and duration > 0 else '时长未知窗口')
        return label, value
    windows = [item for key in ('primary', 'secondary')
               if (item := quota_window(current.get(key) if current else None))]
    first_label, first_value = windows[0] if windows else ('额度窗口', None)
    second = windows[1] if len(windows) > 1 else None
    first = f'{first_value}%' if first_value is not None else ('历史暂无记录' if data else '查询中…')
    queried_at = current.get('queried_at') if current else None
    if type(queried_at) is int and queried_at > 0:
        clock = datetime.fromtimestamp(queried_at).strftime('%H:%M')
        age = data.get('cache_age_s')
        status = f'历史记录 · 更新于 {clock}' + (' · 待后台更新' if age is not None and age >= 300 else '')
    else:
        status = '暂无历史快照 · 后台低频查询' if data else ''
    return f'''<div class="tm-tui-kv tm-run-quota" data-codex-quota aria-label="当前 Codex 账号额度">
        <a class="tm-run-quota-card-link" href="/services/" aria-label="打开 Codex 额度管理">
        <div class="tm-run-quota-head">
            <dt class="tm-run-quota-account" data-quota-account>{_e(name)}</dt>
        </div>
        <div class="tm-run-quota-usage-row">
            <dd class="tm-run-quota-value" data-quota-value>{_e(first_label)}剩余：{_e(first)}</dd>
            <div class="tm-run-quota-week" data-quota-week{' hidden' if second is None else ''}>{_e(second[0]) if second else ''}剩余：{_e(str(second[1]) + '%') if second else ''}</div>
        </div>
        <div class="tm-run-quota-track" role="progressbar" aria-label="{_e(first_label)}剩余额度"
             aria-valuemin="0" aria-valuemax="100"{f' aria-valuenow="{first_value}"' if first_value is not None else ''}><span data-quota-fill style="width:{first_value if first_value is not None else 0}%"></span></div>
        <div class="tm-run-quota-status" data-quota-status role="status" aria-live="polite">{_e(status) if status else ''}</div>
        </a>
    </div>'''


def _tui_summary(raw: str, *, attention: dict[str, Any] | None = None,
                 show_codex_quota: bool = False,
                 quota_snapshot: dict[str, Any] | None = None,
                 worker_account: str | None = None,
                 explore_map_card: str | None = None,
                 resume_available: bool = False,
                 run_available: bool = False,
                 worker_controls: str = "",
                 allow_process_actions: bool = True,
                 effective_status: str | None = None,
                 worker_state_actions: bool = False,
                 worker_cycle_progress: tuple[int, int] | None = None) -> str:
    """Present Textual Cycle metadata, using verified Worker process state when supplied."""
    import re

    # Suppress raw terminal-only `selected=1 failed=1` diagnostics: quota
    # state is independently rendered from structured Controller data.
    lines = [line for line in str(raw).splitlines()
             if not line.strip().startswith('Quota auto ')]
    def fallback() -> str:
        plain = '<pre class="tm-tui-text">' + _e('\n'.join(lines)) + '</pre>'
        if show_codex_quota or explore_map_card is not None:
            explore = ('<div class="tm-tui-kv tm-explore-map-card">' +
                       explore_map_card + '</div>') if explore_map_card is not None else ''
            quota = _codex_quota_metric_card(quota_snapshot, worker_account=worker_account) if show_codex_quota else ''
            return ('<div class="tm-tui-header-stats" data-tui-pane="header">'
                    '<dl class="tm-tui-kv-grid">' + explore + quota +
                    '</dl>' + plain + '</div>')
        return '<pre class="tm-tui-text" data-tui-pane="header">' + _e('\n'.join(lines)) + '</pre>'
    if len(lines) < 3:
        return fallback()
    first = re.fullmatch(r'Process\s+(\S+)\s+State\s+(.+?)\s*', lines[0])
    second = re.fullmatch(r'Cycle\s+(\S+)\s*/\s*(\S+)\s+Phase\s+(.+?)\s{2,}Elapsed\s+(\S+)\s*', lines[1])
    third = re.fullmatch(r'Started\s+(\S+)\s+Cycle start\s+(\S+)\s+Cost\s+(.+?)\s*', lines[2])
    if not (first and second and third):
        return fallback()
    # State is the authoritative scientific lifecycle status. The former
    # Process badge and separate State card conveyed the same information
    # during ordinary Run/PAUSED; Phase remains available in scientific logs.
    values = [
        ('State', effective_status if effective_status is not None else first.group(2).strip()),
        ('Cycle', (f'{worker_cycle_progress[0]} / {worker_cycle_progress[1]}'
                   if worker_cycle_progress is not None
                   else f'{second.group(1)} / {second.group(2)}')),
        ('Cost', third.group(3).strip()),
        ('Elapsed', second.group(4)), ('Started', third.group(1)),
        ('Cycle start', third.group(2)),
    ]
    if explore_map_card is not None:
        values.insert(2, ('ExploreMap', ''))
    if show_codex_quota:
        values.insert(3 if explore_map_card is not None else 2, ('CodexQuota', ''))
    process_active = first.group(1).upper() == 'RUNNING'
    def metric(label: str, value: str) -> str:
        if label == "ExploreMap":
            return '<div class="tm-tui-kv tm-explore-map-card">' + (explore_map_card or '') + '</div>'
        if label == "CodexQuota":
            return _codex_quota_metric_card(quota_snapshot, worker_account=worker_account)
        if label == "State":
            key = str(value).upper().replace(" ", "_")
            tone = "tm-tui-process-green" if key in {"RUNNING", "ACTIVE", "ALIVE"} else (
                "tm-tui-process-red" if key in {"STOPPED", "FAILED", "FAILED_RECOVERY", "NEEDS_ATTENTION", "ORPHANED", "DEAD", "EXITED", "CANCELLED"} else "tm-tui-process-amber")
            if worker_state_actions:
                content = (f'<button type="button" data-worker-state-open '
                           f'class="tm-tui-process {tone}" title="管理当前 Agent" '
                           f'aria-haspopup="dialog">{_e(value)}'
                           '<span aria-hidden="true" class="tm-process-chevron">▾</span></button>')
            elif allow_process_actions and key == "RUNNING" and process_active:
                content = (f'<button type="button" data-tui-process-actions '
                           f'class="tm-tui-process {tone}" title="打开 Workflow 操作弹窗"'
                           f' aria-haspopup="dialog">{_e(value)}'
                           '<span aria-hidden="true" class="tm-process-chevron">▾</span></button>')
            elif (allow_process_actions and key in {"PAUSED", "INTERRUPTED", "NEEDS_ATTENTION", "FAILED",
                         "FAILED_RECOVERY", "ORPHANED", "CHECKPOINT_REACHED",
                         "RECOVERABLE", "UNRESPONSIVE"} and resume_available):
                content = (f'<button type="button" data-tui-process-actions '
                           f'class="tm-tui-process {tone}" title="打开 Resume 恢复任务弹窗"'
                           f' aria-haspopup="dialog">{_e(value)}'
                           '<span aria-hidden="true" class="tm-process-chevron">▾</span></button>')
            elif allow_process_actions and run_available and key in {"OVER", "CANCELLED", "STOPPED", "NEW", "IDLE"}:
                content = (f'<button type="button" data-tui-process-actions '
                           f'class="tm-tui-process {tone}" title="启动一个新的 Cycle"'
                           f' aria-haspopup="dialog">{_e(value)}'
                           '<span aria-hidden="true" class="tm-process-chevron">▾</span></button>')
            else:
                content = f'<span class="tm-tui-process {tone}">{_e(value)}</span>'
            content += worker_controls
        elif label == "Cycle":
            raw_ordinal, raw_limit = value.split(' / ', 1)
            numeric_limit = raw_limit if raw_limit.isascii() and raw_limit.isdigit() else "1"
            # Worker mode reads the durable per-Worker batch counter, not the
            # per-invocation ordinal. Global Cycle ID stays in the titlebar.
            explanation = ("当前 Agent 本批次已启动 Cycle 数" if worker_cycle_progress is not None
                           else "本次运行的 Cycle 序号")
            limit_explanation = ("修改本批次每个 Worker 的 Cycle 上限" if worker_cycle_progress is not None
                                 else "修改任务 Cycle 上限")
            content = ('<span title="' + _e(explanation) + '">' + _e(raw_ordinal) + '</span>'
                       ' / <span data-cycle-max-current>' + _e(raw_limit) + '</span>'
                       '<button type="button" class="tm-tui-cycle-edit" data-tui-op="max_cycles"'
                       ' title="' + _e(limit_explanation) + '" aria-expanded="false">修改上限</button>'
                       '<form data-cycle-max-form hidden aria-label="修改 Cycle 上限">'
                       '<label>上限 <input data-cycle-max-input type="number" min="1" max="1000000"'
                       ' step="1" required value="' + _e(numeric_limit) + '"></label>'
                       '<button type="submit" data-cycle-max-save>保存</button>'
                       '<button type="button" data-cycle-max-cancel>取消</button>'
                       '<small data-cycle-max-message role="status" aria-live="polite"></small>'
                       '</form>')
        elif label in {"Started", "Cycle start"}:
            # Textual's local MM-DDTHH:mm:ss is not ISO in a browser card:
            # change separators only; do NOT shift the clock or guess a timezone.
            import re
            content = _e(re.sub(r"(?<=\d)T(?=\d{2}:\d{2})", " ", value))
        else:
            content = _e(value)
        return f'<div class="tm-tui-kv"><dt>{_e(label)}</dt><dd>{content}</dd></div>'
    cells = ''.join(metric(label, value) for label, value in values)
    if isinstance(attention, dict):
        reason = str(attention.get("reason") or "—")
        action = str(attention.get("requested_action") or "—")
        resume = str(attention.get("resume_behavior") or "—")
        takeover = str(attention.get("takeover") or "—")
        warning = f'''<details class="tm-tui-attention" open>
            <summary>需要处理 · NEEDS ATTENTION <span>查看完整原因与恢复说明</span></summary>
            <dl><dt>Reason</dt><dd>{_e(reason)}</dd>
              <dt>Action</dt><dd>{_e(action)}</dd>
              <dt>Resume</dt><dd>{_e(resume)}</dd>
              <dt>Takeover</dt><dd>{_e(takeover)}</dd></dl>
        </details>'''
    else:
        rest = '\n'.join(lines[3:]).strip()
        warning = f'<pre class="tm-tui-summary-extra">{_e(rest)}</pre>' if rest else ''
    return f'<div class="tm-tui-header-stats" data-tui-pane="header"><dl class="tm-tui-kv-grid">{cells}</dl>{warning}</div>'


_ROLE_ORDER = ("research_lead", "challenger", "investigator", "literature", "judge", "reporter")
_EFFORT_OPTIONS = ("low", "medium", "high", "xhigh")


def _tokens(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "未记录"


def _cache_hit_rate(data: Any) -> str:
    """Cached input is a subset of input tokens, not of total input + output."""
    if not isinstance(data, dict):
        return "未记录"
    tokens_in, cached = data.get("tokens_in"), data.get("cached_input_tokens")
    if (isinstance(tokens_in, int) and not isinstance(tokens_in, bool) and tokens_in > 0
            and isinstance(cached, int) and not isinstance(cached, bool)
            and 0 <= cached <= tokens_in):
        return f"{cached / tokens_in:.1%}"
    return "未记录"


def _cost_label(record: Any) -> str:
    cost = record if isinstance(record, dict) else {}
    amount = cost.get("amount")
    currency = cost.get("currency")
    if isinstance(amount, (float, int)) and not isinstance(amount, bool) and isinstance(currency, str) and currency:
        try:
            number = float(amount)
            if not math.isfinite(number) or number < 0:
                return "未计价"
            money = f"{number:.4f}" if 0 < number < 0.01 else f"{number:.3f}" if number < 1 else f"{number:.2f}"
        except (TypeError, ValueError, OverflowError):
            return "未计价"
        symbol = {"USD":"$", "CNY":"¥"}.get(currency.upper(), currency+" ")
        source = str(cost.get("source") or "").lower()
        prefix = "~" if source == "estimated" else ""
        return f"{prefix}{symbol}{money}" + (" 估" if source == "estimated" else "")
    if isinstance(cost.get("model_calls"), int) and cost["model_calls"] > 0:
        return "未计价"
    return "—"


def _usage_cell(agent_usage: Any) -> str:
    info = agent_usage if isinstance(agent_usage, dict) else {}
    project = info.get("project") if isinstance(info.get("project"), dict) else {}
    cycle = info.get("cycle") if isinstance(info.get("cycle"), dict) else {}
    total = project.get("tokens_total")
    cycle_total = cycle.get("tokens_total")
    if total is None:
        # Keep the same token-card layout for idle/unmeasured Agents. This is
        # intentionally not a button: no stored usage means no details to open.
        return ('<div class="tm-usage-hit tm-usage-hit-empty" aria-label="Token 用量未记录">'
                '<span class="tm-usage-value">未记录</span>'
                '<small class="tm-usage-cycle">本轮未记录</small></div>')
    detail = {
        "任务累计": _tokens(total),
        "累计缓存命中率": _cache_hit_rate(project),
        "当前 Cycle": _tokens(cycle_total),
        "本轮缓存命中率": _cache_hit_rate(cycle),
        "累计输入": _tokens(project.get("tokens_in")),
        "累计输出": _tokens(project.get("tokens_out")),
        "其中缓存输入": _tokens(project.get("cached_input_tokens")),
        "其中推理输出": _tokens(project.get("reasoning_tokens")),
        "本轮输入": _tokens(cycle.get("tokens_in")),
        "本轮输出": _tokens(cycle.get("tokens_out")),
        "本轮缓存输入": _tokens(cycle.get("cached_input_tokens")),
        "本轮推理输出": _tokens(cycle.get("reasoning_tokens")),
    }
    cycle_label = ('本轮 ' + _tokens(cycle_total)) if cycle_total is not None else '本轮未记录'
    payload = _e(json.dumps(detail, ensure_ascii=False))
    return ('<button type="button" class="tm-usage-hit" data-token-detail="' + payload +
            '" aria-expanded="false" aria-haspopup="dialog" aria-label="查看 Token 输入、输出、缓存与推理明细">'
            f'<span class="tm-usage-value">{_e(_tokens(total))}</span>'
            f'<small class="tm-usage-cycle">{_e(cycle_label)}</small>'
            '</button>')



def _model_preset_header(settings: dict[str, Any]) -> str:
    """One six-role preset badge in MODEL heading, not six per-role tags.

    The profile is an explicit task/Worker selection. Worker-local route edits
    are overlays and cannot be mistaken for a different named preset. The
    frozen Cycle may still use earlier routes until its next safe boundary.
    """
    titles = {"test": "Codex 一档", "codex": "Codex 二档", "advanced": "Codex 三档"}
    entries = [settings.get(role) for role in _ROLE_ORDER
               if isinstance(settings.get(role), dict)]
    profiles = {str(entry.get("preset_profile")) for entry in entries
                if entry.get("preset_profile") in titles}
    if len(profiles) != 1:
        return ""
    label = titles[next(iter(profiles))]
    tier_notes = [str(entry.get("preset_tier") or "") for entry in entries]
    if any("自定义" in note or "覆盖" in note for note in tier_notes):
        label += " · 自定义覆盖"
    if any("下轮生效" in note for note in tier_notes):
        label += " · 下轮生效"
    elif any("待安全调用" in note for note in tier_notes):
        label += " · 待安全调用"
    profile = next(iter(profiles))
    return (' <button type="button" class="tm-model-preset-tier" data-model-preset-tier '
            'data-model-preset-open data-model-preset-profile="' + _e(profile) + '" '
            'aria-haspopup="dialog" aria-label="查看与修改整套模型档位，当前' + _e(label) + '" '
            'title="点击查看六个科研 Agent 的模型配置；下一次尚未开始的角色调用安全生效">'
            + _e(label) + ' ▾</button>')



def _tui_workflow_table(raw: str, *, hot_route_capable: bool = False,
                        settings: dict[str, Any] | None = None,
                        catalog: list[dict[str, str]] | None = None,
                        role_usage: dict[str, Any] | None = None) -> str:
    """Semantic Web table; identity/elapsed/cost/state stay from canonical Textual renderer."""
    lines = str(raw).splitlines()
    if not lines or not lines[0].startswith('AGENT/STAGE'):
        return '<pre class="tm-tui-text" data-tui-pane="workflow">' + _e(raw) + '</pre>'
    settings = settings or {}
    catalog = catalog or []
    role_usage = role_usage or {}
    columns = ((0, 13), (15, 39), (41, 49), (51, 59), (61, None))
    headings = ('AGENT / STAGE', 'MODEL', 'EFFORT', 'COST', 'TOKENS（累计）', 'TIME')
    body = []
    for index, line in enumerate(lines[1:]):
        if not line.strip() or index >= len(_ROLE_ORDER):
            continue
        fields = [line[a:b].strip() for a, b in columns]
        role = _ROLE_ORDER[index]
        data = settings.get(role) if isinstance(settings.get(role), dict) else {}
        pending = data.get('pending') if isinstance(data.get('pending'), dict) else {}
        chosen_model = pending.get('model', data.get('model'))
        chosen_provider = pending.get('provider', data.get('provider'))
        chosen_effort = pending.get('reasoning_effort', data.get('reasoning_effort'))
        # Account-backed Codex routes resolve an unset effort to medium at
        # invocation. Display that actual default, without saving an override.
        if not chosen_effort and chosen_provider in {'Codex', 'ChatGPT', 'CodexHTTPS'}:
            chosen_effort = 'medium'
        chosen_value = str(chosen_model or '') + '@' + str(chosen_provider or '')
        model_opts = ''.join(
            '<option value="' + _e(item['model'] + '@' + item['provider']) + '"'
            + (' selected' if chosen_value == item['model'] + '@' + item['provider'] else '')
            + '>' + _e(item.get('label') or item['model']) + '</option>'
            for item in catalog if isinstance(item, dict) and isinstance(item.get('model'), str) and isinstance(item.get('provider'), str)
        )
        allowed = any(chosen_value == item.get('model') + '@' + item.get('provider') for item in catalog if isinstance(item, dict) and isinstance(item.get('model'), str) and isinstance(item.get('provider'), str))
        if not allowed:
            # Do not silently select the first catalog model when the current
            # route is absent or no longer in the advertised model catalog.
            label = f'{chosen_model} ({chosen_provider})' if chosen_model and chosen_provider else '未配置'
            model_opts = '<option value="" selected disabled>' + _e(label) + '</option>' + model_opts
        effort_opts = ''.join(
            f'<option value="{effort}"' + (' selected' if chosen_effort == effort else '') + f'>{effort}</option>'
            for effort in _EFFORT_OPTIONS
        )
        if chosen_effort not in _EFFORT_OPTIONS:
            effort_opts = '<option value="" selected disabled>未配置</option>' + effort_opts
        changed = bool(pending) and (pending.get('model'), pending.get('provider'), pending.get('reasoning_effort')) != (data.get('model'), data.get('provider'), data.get('reasoning_effort'))
        # Default labels belong to expanded choices, not beside the controls.
        timing = (pending or {}).get('activation', 'next_invocation')
        pending_reason = data.get('pending_reason')
        pending_label = (
            '已保存 · 当前调用结束后切换' if pending_reason == 'active_invocation' else
            '已保存 · 跨 Provider 需要安全重新建线程' if pending_reason == 'provider_deferred' else
            '已保存 · 仅下一 Cycle 生效' if timing == 'next_cycle' else
            '已保存 · 等待下次安全调用生效' if hot_route_capable else
            '已保存 · 当前 Runner 不支持热切换，下次 Run/Resume 生效')
        badge = ('<button type="button" class="tm-tui-pending" data-route-activation-edit '
                 'title="修改已保存的生效方式">' + pending_label + ' · 修改</button>') if changed else ''
        default_model = chosen_value if not pending and allowed else ''
        default_effort = chosen_effort if not pending and chosen_effort in _EFFORT_OPTIONS else ''
        meter = role_usage.get(role) if isinstance(role_usage.get(role), dict) else {}
        project_meter = meter.get("project") if isinstance(meter.get("project"), dict) else {}
        cost_data = project_meter.get("cost")
        cost_text = _cost_label(cost_data)
        cost_cell = ('<span class="tm-usage-cost" title="仅显示可验证或已计价的金额；未计价不等于零费用">'
                     + _e(cost_text) + '</span>')
        model_cell = ('<select class="tm-tui-model-select" aria-label="' + _e(fields[0]) +
                      ' 模型" data-route-model data-route-default-value="' + _e(default_model) + '">'
                      + model_opts + '</select>' + badge)
        effort_cell = ('<select aria-label="' + _e(fields[0]) + ' effort" data-route-effort'
                       + ' data-route-default-value="' + _e(default_effort) + '">'
                       + effort_opts + '</select>')
        status = fields[-1].lower()
        status_class = 'tm-tui-state-' + (status if status in {'running','done','idle','waiting','rework','failed','blocked','paused'} else 'neutral')
        body.append('<tr data-agent-role="' + role + '" class="tm-role-' + role
                    + (' tm-agent-row-running' if status == 'running' else '') + '"'
                    + ' data-saved-model="' + _e(chosen_value if allowed else '') + '"'
                    + ' data-saved-effort="' + _e(chosen_effort) + '"'
                    + ' data-saved-activation="' + _e(timing) + '">' +
                    '<td class="tm-tui-agent-identity"><strong>' + _e(fields[0]) + '</strong>'
                    '<span class="tm-tui-agent-state ' + status_class + '">' + _e(fields[4]) + '</span></td>' +
                    '<td>' + model_cell + '</td>' +
                    '<td>' + effort_cell + '</td>' +
                    '<td>' + cost_cell + '</td>' +
                    '<td class="tm-usage-cell">' + _usage_cell(meter) + '</td>' +
                    '<td>' + _e(fields[3]) + '</td>' +
                    '</tr>')
    head = ''.join('<th scope="col">' + column
                   + (_model_preset_header(settings) if column == 'MODEL' else '')
                   + '</th>' for column in headings)
    return ('<div class="tm-tui-workflow" data-tui-pane="workflow">'
            '<div class="tm-tui-table-scroll"><table class="tm-tui-agent-table">'
            '<colgroup><col class="tm-col-role"><col class="tm-col-model"><col class="tm-col-effort">'
            '<col class="tm-col-cost"><col class="tm-col-tokens"><col class="tm-col-time"></colgroup>'
            '<thead><tr>' + head + '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div></div>')


def _tui_pairs(raw: Any) -> dict[str, str]:
    """Read presentation labels only; never recompute canonical science state."""
    import re
    pairs = {}
    for line in str(raw or "").splitlines():
        bits = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if len(bits) == 2 and bits[0] and bits[1]:
            pairs[bits[0].rstrip(":")] = bits[1]
    return pairs


def _run_focus(parts: dict[str, Any], status: Any, activity_detail: Any,
               recent_events: Any = None, cycle: Any = "") -> str:
    info = _tui_pairs(parts.get("activity"))
    detail = activity_detail if isinstance(activity_detail, dict) else {}
    status_key = str(status or "").upper()
    alarm = status_key in {"FAILED", "FAILED_RECOVERY", "NEEDS_ATTENTION", "INTERRUPTED", "ORPHANED"}
    choices = ((detail.get("last_event"), detail.get("current_activity"))
               if alarm else (detail.get("current_activity"), detail.get("last_event")))
    choices += (info.get("Last action"), info.get("Purpose"))
    message = next((str(value).strip() for value in choices
                    if value and str(value).strip().lower() not in
                    {"-", "—", "idle", "waiting for work", "no handoff recorded"}), "")
    if not message and alarm:
        message = "状态已终止，具体原因未记录；可打开 Logs / Events 核查。"
    if not message:
        message = str(info.get("Purpose") or "暂无活动记录")
    role = str(info.get("Agent") or "").strip()
    phase = str(info.get("State") or "").split(" | phase:", 1)
    stage = phase[1].strip() if len(phase) > 1 else ""
    extra = []
    if role and role not in {"-", "—"}:
        extra.append("Agent · " + role)
    if stage and stage not in {"-", "—"}:
        extra.append("Phase · " + stage)
    label = "异常原因" if alarm else "最新状态"
    full = _e(parts.get("activity", "(none)"))
    dialogue_latest = None
    if detail.get("latest_dialogue_at") and detail.get("latest_dialogue_kind"):
        dialogue_latest = {
            "at": detail.get("latest_dialogue_at"),
            "kind": detail.get("latest_dialogue_kind"),
            "role": detail.get("latest_dialogue_role") or "",
            "hint": detail.get("latest_dialogue_hint") or "",
        }
    history_latest = recent_events[-1] if isinstance(recent_events, list) and recent_events and isinstance(recent_events[-1], dict) else None
    latest = dialogue_latest or history_latest
    latest_html = ""
    if latest and latest.get("at") and latest.get("kind"):
        preview = str(latest.get("hint") or latest.get("kind") or "").strip()
        if len(preview) > 220:
            preview = preview[:219].rstrip() + "…"
        query = {"tab": "events", "jump": "latest", "at": str(latest.get("at")),
                 "kind": str(latest.get("kind")), "role": str(latest.get("role") or ""),
                 "hint": str(latest.get("hint") or "")}
        if cycle:
            query["cycle"] = str(cycle)
        latest_html = ('<a class="tm-focus-latest" href="?' + _e(urlencode(query)) +
                       '" title="跳到当前对话的最新消息"><span>最新事件</span><strong>' +
                       _e(preview or latest.get("kind")) + '</strong></a>')
    return ('<section class="tm-tui-panel tm-insight-focus' + (' tm-insight-alert' if alarm else '') +
            '" data-tui-pane="activity"><div class="tm-tui-section-title">运行动态</div>'
            '<div class="tm-focus-top"><span class="tm-focus-mark">' + _e(status_key or "UNKNOWN") +
            '</span><span class="tm-focus-sub">' + _e(" · ".join(extra)) + '</span></div>'
            '<div class="tm-focus-label">' + label + '</div><p class="tm-focus-message">' +
            _e(message) + '</p>' + latest_html +
            '<details class="tm-insight-more"><summary>原始 Activity 字段</summary>'
            '<pre>' + full + '</pre></details></section>')


def _recent_timeline(raw: Any, source_events: Any = None, *,
                     cycle_catalog: list[dict[str, str]] | None = None,
                     selected_cycle_id: str = "", worker_id: str | None = None,
                     cycle_summaries: dict[str, dict[str, str]] | None = None) -> str:
    import re
    lines = []
    for line in str(raw or "").splitlines():
        match = re.match(r"^(\d{2}:\d{2}:\d{2})\s{2,}(.+)$", line.strip())
        if match:
            lines.append((match.group(1), match.group(2)))
    if not lines and not cycle_catalog:
        return '<div class="tm-insight-empty">暂无线索记录</div>'
    sources = source_events if isinstance(source_events, list) else []
    # Only make a row actionable when its canonical HH:MM:SS display aligns
    # with an exact persisted source identity. Never guess by nearby time.
    indexed = (sources if len(sources) == len(lines) and all(
        isinstance(meta, dict) and str(meta.get("at") or "")[11:19] == clock
        for (clock, _), meta in zip(lines, sources)) else [])
    signals = ("fail", "error", "attention", "verdict", "cycle started", "rework",
               "handoff", "complete", "paused", "interrupted", "quota")
    important = [i for i, (_, msg) in enumerate(lines)
                 if any(tag in msg.casefold() for tag in signals)]
    # Only a leading, explicitly named Agent attributes the recorded event.
    role_pattern = re.compile(
        r"^(Research Lead|Challenger|Investigator|Literature|Judge|Reporter)(?=\s|[·:：]|$)",
        re.IGNORECASE,
    )
    role_classes = {
        "research lead": "research_lead", "challenger": "challenger",
        "investigator": "investigator", "literature": "literature",
        "judge": "judge", "reporter": "reporter",
    }

    def cycle_summary_bits(cycle_id: str | None, fallback_at: str = "") -> tuple[str, str]:
        summary = (cycle_summaries or {}).get(str(cycle_id or ""), {})
        clock_value = str(summary.get("time") or fallback_at or "")
        clock_html = '<time>' + _e(clock_value) + '</time>' if clock_value else ""
        verdict = str(summary.get("verdict") or "").strip()
        verdict_html = (
            '<span class="tm-cycle-verdict" title="本 Cycle 已结束，但没有终局科学 Judge 裁决">'
            '未裁决</span>' if verdict == "unadjudicated" else
            '<span class="tm-cycle-verdict" title="本 Cycle 的终局科学 Judge 裁决">Judge · ' +
            _e(verdict) + '</span>' if verdict in {"supported", "rejected", "open"} else ""
        )
        return clock_html, verdict_html
    # All recorded entries are visible in the card's own scroll area.
    def entry(index: int, *, repeated_start: bool = False) -> str:
        clock, message = lines[index]
        bad = any(tag in message.lower() for tag in ("failed", "error", "attention"))
        category = "tm-log-attention" if bad else "tm-log-milestone" if index in important else ""
        role_match = role_pattern.match(message)
        role_class = (" tm-role-" + role_classes[role_match.group(1).casefold()]) if role_match else ""
        description = ('<span class="tm-cycle-reentered">再次进入运行</span>' if repeated_start else
                       _e(message) if not role_match else
                       '<strong class="tm-log-role">' + _e(role_match.group(1)) + '</strong>' +
                       _e(message[role_match.end():]))
        source = indexed[index] if indexed else None
        # One clock per row: the event's own timestamp. A done row stays at its
        # start clock; do not paint a later finish over events that happened
        # during that Agent.
        clock_html = '<time>' + _e(clock) + '</time>'
        if source and source.get("at") and source.get("kind"):
            attrs = ''.join(
                ' data-recent-' + name + '="' + _e(source.get(field) or "") + '"'
                for name, field in (("at","at"),("kind","kind"),
                                    ("role","role"),("hint","hint")))
            destination = "?" + urlencode({
                "tab": "events", "at": str(source.get("at")),
                "kind": str(source.get("kind")),
                "role": str(source.get("role") or ""),
                "hint": str(source.get("hint") or ""),
            })
            return ('<li class="tm-log-item ' + category + role_class + '">'
                    '<a class="tm-recent-select" href="' + _e(destination) +
                    '"'
                    ' title="在当前页面打开 Events 并定位此事件"'
                    + attrs + '>' +
                    clock_html + '<span>' + description +
                    '</span></a></li>')
        return ('<li class="tm-log-item ' + category + role_class + '">' + clock_html +
                '<span>' + description + '</span></li>')
    # Group by persisted Cycle identity rather than by CYCLE_STARTED calls:
    # start_run() also emits CYCLE_STARTED when a frozen Cycle resumes.
    # Earlier/truncated messages without a Cycle boundary remain unattributed.
    cycle_start = re.compile(r"^Cycle\s+started(?:\s*[·:：-]\s*(cycle-\d+|\d+))?$", re.I)
    def persisted_cycle(index: int) -> str | None:
        source = indexed[index] if indexed else None
        raw_id = str(source.get("cycle_id") or "") if source else ""
        return str(int(raw_id)) if raw_id.isascii() and raw_id.isdigit() else None

    groups: list[dict[str, Any]] = []
    by_cycle: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for index, (clock, message) in enumerate(lines):
        started = cycle_start.fullmatch(message.strip())
        trusted = persisted_cycle(index)
        if trusted:
            key = "cycle-" + trusted
            group = by_cycle.get(key)
            if group is None:
                group = {"key": key, "cycle": trusted,
                         "at": clock if started else None,
                         "started": bool(started), "entries": []}
                groups.append(group)
                by_cycle[key] = group
            current = group
            if started:
                if group["started"] and group["entries"]:
                    group["entries"].append((index, True))
                else:
                    group["started"] = True
                    group["at"] = clock
            else:
                group["entries"].append((index, False))
            continue
        if started:
            raw_id = started.group(1)
            raw_number = raw_id.removeprefix("cycle-") if raw_id else None
            # Normalize padded and plain numeric IDs for stable client keys.
            cycle_key = "cycle-" + str(int(raw_number)) if raw_number else None
            if cycle_key and cycle_key in by_cycle:
                # This is a new invocation of an existing Cycle, not a Cycle
                # termination. Keep the exact source identity on its row.
                current = by_cycle[cycle_key]
                current["entries"].append((index, True))
                continue
            source_at = indexed[index].get("at") if indexed else None
            key = cycle_key or "cycle-unknown-" + str(source_at or clock)
            current = {"key": key, "cycle": raw_number, "at": clock,
                       "started": True, "entries": []}
            groups.append(current)
            if cycle_key:
                by_cycle[cycle_key] = current
        else:
            if current is None:
                current = {"key": "unattributed", "cycle": None, "at": None,
                           "started": False, "entries": []}
                groups.append(current)
            current["entries"].append((index, False))

    def group_markup(group: dict[str, Any], latest: bool) -> str:
        cycle_label = ("Cycle " + str(group["cycle"]) if group["cycle"]
                       else "未编号 Cycle" if group["started"]
                       else "较早记录")
        count = len(group["entries"])
        open_attr = ' open' if latest else ''
        clock, verdict_html = cycle_summary_bits(group.get("cycle"), group.get("at") or "")
        rows_list = []
        judge_seen = False
        closing_seen = False
        for index, restarted in group["entries"]:
            source = indexed[index] if indexed else None
            stage = str(source.get("stage") or "") if source else ""
            if (judge_seen and not closing_seen and
                    stage in {"cycle_synthesis", "frontier_maintenance",
                              "report", "canvas_maintenance"}):
                rows_list.append(
                    '<li class="tm-cycle-tail-divider" aria-label="Cycle 收尾">'
                    '<span>Cycle 收尾</span><small>Judge 裁决后 · 同一 Cycle</small>'
                    '</li>')
                closing_seen = True
            rows_list.append(entry(index, repeated_start=restarted))
            if role_pattern.match(lines[index][1]) and lines[index][1].lower().startswith("judge"):
                judge_seen = True
        rows = "".join(rows_list)
        return ('<details class="tm-cycle-group" data-cycle-group-key="' +
                _e(group["key"]) + '"' + open_attr + '>'
                '<summary class="tm-cycle-summary">'
                '<span class="tm-cycle-label">' + _e(cycle_label) + '</span>' +
                clock + verdict_html + '<span class="tm-cycle-count">' + str(count) + ' 条</span>'
                '<span class="tm-cycle-chevron" aria-hidden="true">⌄</span>'
                '</summary><ol class="tm-run-events-recent">' +
                rows + '</ol></details>')

    if cycle_catalog is not None:
        # Keep the initial DOM small: show only the newest ten Cycle summaries.
        # An explicitly requested older Cycle is added for reliable deep links.
        display_catalog = list(cycle_catalog[-10:])
        display_ids = {item["id"] for item in display_catalog}
        if selected_cycle_id and selected_cycle_id not in display_ids:
            selected_item = next((item for item in cycle_catalog
                                  if item["id"] == selected_cycle_id), None)
            if selected_item is not None:
                display_catalog.append(selected_item)
                display_catalog.sort(key=lambda item: int(item["id"]))
        chosen = next((g for g in groups if g.get("cycle") == selected_cycle_id), None)
        recent_parts = []
        for item in display_catalog:
            cid = item["id"]
            if cid == selected_cycle_id:
                if chosen is not None:
                    selected_html = group_markup(chosen, True)
                else:
                    clock_html, verdict_html = cycle_summary_bits(cid)
                    selected_html = ('<details class="tm-cycle-group" open '
                                     'data-cycle-group-key="cycle-' + _e(cid) + '">'
                                     '<summary class="tm-cycle-summary"><span class="tm-cycle-label">Cycle '
                                     + _e(cid) + '</span>' + clock_html + verdict_html +
                                     '<span class="tm-cycle-count">记录暂不可用</span>'
                                     '</summary></details>')
                recent_parts.append('<div data-history-selected="' + _e(cid) + '">' + selected_html + '</div>')
            else:
                # Detailed events remain lazy; only compact, persisted Cycle
                # metadata (start clock and explicit final Judge verdict) is shown.
                clock_html, verdict_html = cycle_summary_bits(cid)
                recent_parts.append(
                    '<details class="tm-cycle-group" data-cycle-group-key="cycle-' + _e(cid) +
                    '" data-cycle-history-lazy="' + _e(cid) + '">'
                    '<summary class="tm-cycle-summary" title="点击展开本 Cycle 记录">'
                    '<span class="tm-cycle-label">Cycle ' + _e(cid) + '</span>' +
                    clock_html + verdict_html +
                    '<span class="tm-cycle-count">展开</span>'
                    '<span class="tm-cycle-chevron" aria-hidden="true">⌄</span>'
                    '</summary><div class="tm-cycle-history-placeholder" data-cycle-history-placeholder '
                    'aria-live="polite">展开后自动加载记录</div></details>')
        recent = "".join(recent_parts)
        recent_count = min(10, len(cycle_catalog))
        has_extra_selected = bool(selected_cycle_id and selected_cycle_id not in
                                  {item["id"] for item in cycle_catalog[-10:]})
        heading = ('<p class="tm-cycle-history-total">已显示 ' + str(len(display_catalog)) +
                   ' / ' + str(len(cycle_catalog)) + ' 个 Cycle' +
                   (' · 另含当前定位的历史 Cycle' if has_extra_selected else '') +
                   (' · 向上滚动到顶部继续加载更早 Cycle' if len(cycle_catalog) > recent_count else '') +
                   ' · 点击 Cycle 行展开记录</p>')
        # Deep-linked older selected Cycle is an extra row, not the cursor for
        # sequential pages traversing backwards from the latest ten.
        earliest = cycle_catalog[-recent_count]["id"] if recent_count else ""
        paging = (' data-cycle-earliest="' + _e(earliest) +
                  '" data-cycle-has-more="' + ('1' if len(cycle_catalog) > recent_count else '0') +
                  '" data-cycle-total="' + str(len(cycle_catalog)) + '"')
    else:
        recent = "".join(group_markup(group, False) for group in groups)
        heading = ""
        paging = ""
    return ('<div class="tm-run-events-scroll" tabindex="0" role="region" ' +
            'aria-label="最近进展按 Cycle 分组，可展开折叠和上下滚动"' + paging + '>' +
            heading + '<div class="tm-run-cycle-groups">' + recent + '</div></div>')



_SEVERITY_LABELS = {
    "minor": "轻微异常", "attention": "需要关注",
    "error": "系统错误", "important": "重要控制事件",
}
_LIFECYCLE_LABELS = {
    "registered": "首次登记", "repair_handoff": "修复交接",
    "resume_attempt": "恢复尝试", "recurred": "再次发生",
    "notification_sent": "通知已发送",
    "notification_failed": "通知失败",
}
_KIND_LABELS = {
    "needs_attention": "需要关注",
    "json_normalization": "JSON 规范化",
    "json_repair_retry": "JSON 修复重试",
    "contract_repair_retry": "合同修复重试",
    "system_failure": "系统执行失败",
    "quota_pause": "模型额度暂停",
    "runtime_model_tier_mismatch": "模型档位不匹配",
    "preferred_focus_started": "Preferred Focus 开始",
    "preferred_focus_exhausted": "Preferred Focus 结束",
}


# Only explanatory incident codes are shown outside the raw-detail disclosure.
# The chronological lifecycle remains independent from incident identity.
_IMPORTANT_REASON_LABELS = {
    "investigator_native_goal_usageLimited": "Investigator 模型原生使用额度受限",
    "investigator_native_goal_blocked": "Investigator 模型调用受阻",
    "investigator_native_goal_unexpected_blocked": "Investigator 原生 Goal 意外中断（非科研主动阻断）",
    "investigator_budget_review_required": "Investigator 预算等待审批",
    "judge_rework_budget_exhausted": "Judge 返工预算耗尽",
    "no_qualified_candidate": "未找到通过资格审查的研究候选",
    "provider_quota_exhausted": "Provider 模型额度耗尽",
    "codex_network_unreachable": "Codex 网络无法连接",
    "investigator_actionable_no_progress_repeated_twice": "Investigator 连续两次未取得可执行进展",
    "investigator_internal_turn_limit": "Investigator 内部回合达到限制",
    "task_profile_override_mismatch": "任务模型档位覆盖不匹配",
    "investigator_native_goal_usage_limited": "Investigator 模型原生使用额度受限",
}
_IMPORTANT_SUMMARY_PLACEHOLDERS = {
    "-", "—", "none", "null", "needs_attention", "failed", "run_failed",
    "paused", "interrupted", "run_paused", "run.failed", "执行需要关注",
}


def _important_timeline(events: Any, *, run: dict[str, Any] | None = None,
                        audit_details: bool = False, review_only: bool = False) -> str:
    events = ([row for row in events if isinstance(row, dict) and (audit_details or is_milestone(row))]
              if isinstance(events, list) else [])
    if not events:
        return ('<div class="tm-run-events-scroll" tabindex="0" role="region" '
                'aria-label="重要事件时间线，可上下滚动">'
                '<p class="tm-insight-empty">暂无重要科研或运行里程碑；异常请查看「异常记录」。</p></div>')
    # Tie budget-review entries to the EXACT saved review timestamp and Cycle.
    # No nearest-neighbour heuristic: a different approval must never open.
    budget_refs: dict[tuple[str, str], int] = {}
    if run:
        from .stage_conversation import _root
        wanted = {str(row.get("_cycle_name") or "") for row in events
                  if isinstance(row, dict)
                  and row.get("code") == "investigator_budget_review_required"}
        for cycle_name in wanted:
            try:
                folder = _root(run, cycle_name)
            except (ValueError, OSError, KeyError):
                continue
            for review in read_budget_reviews(dict(run, cycle_dir=str(folder))):
                ix = review.get("review_index")
                at = review.get("at")
                if type(ix) is int and 1 <= ix <= 9999 and isinstance(at, str):
                    budget_refs[(cycle_name, at)] = ix
    rows: list[str] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        severity = str(row.get("severity") or "").lower()
        if severity not in _SEVERITY_LABELS:
            severity = "minor"
        kind = str(row.get("kind") or "未分类")
        lifecycle = str(row.get("lifecycle") or "registered")
        timestamp = str(row.get("at") or "")
        cycle = str(row.get("_display_cycle") or "任务级")
        source = "任务级" if cycle == "任务级" else "Cycle " + cycle
        code = str(row.get("code") or "")
        note = str(row.get("summary") or "").strip()
        title = _KIND_LABELS.get(kind, kind.replace("_", " "))
        label = _LIFECYCLE_LABELS.get(lifecycle, lifecycle)

        # Cycle and time are the first visible fields. The generic
        # needs_attention type is already conveyed by the severity marker;
        # it is not a useful event title.
        heading = (('<div class="tm-important-heading">'
                    '<strong class="tm-important-kind">' + _e(title) + '</strong></div>')
                   if kind != "needs_attention" else "")
        meta = ('<div class="tm-important-meta">'
                '<span class="tm-important-cycle">' + _e(source) + '</span>'
                '<time datetime="' + _e(timestamp) + '">' +
                _e(timestamp[:19].replace("T", " ")) + '</time>'
                '<span class="tm-important-lifecycle">' + _e(label) + '</span>'
                '<span class="tm-important-sr-only">等级：' +
                _e(_SEVERITY_LABELS[severity]) + '</span></div>')
        reason = _IMPORTANT_REASON_LABELS.get(code)
        explanation = ('<p class="tm-important-reason">' + _e(reason) + '</p>'
                       if reason else "")
        # The SDK deliberately writes bare lifecycle-status placeholders and
        # repeats some summaries during repair/notification follow-ups.
        # Those remain in the detailed record, not as duplicate public text.
        meaningful = (note and note.casefold() not in _IMPORTANT_SUMMARY_PLACEHOLDERS
                      and note.casefold() not in {title.casefold(), code.casefold()}
                      and note.casefold() != str(reason or "").casefold())
        summary = ('<p class="tm-important-summary">' + _e(note) + '</p>'
                   if meaningful and lifecycle not in {"notification_sent"} else "")
        review_button = ""
        cycle_name = str(row.get("_cycle_name") or "")
        incident_id = str(row.get("incident_id") or "")
        repair = row.get("repair")
        if (run and run_monitor.CYCLE_NAME.fullmatch(cycle_name)
                and lifecycle == "repair_handoff"
                and isinstance(repair, dict)
                and repair.get("target") == "sdk:internal_attention"
                and re.fullmatch(r"INC-[0-9a-f]{16}", incident_id)):
            review_ref = str(repair.get("review_ref") or "")
            if not re.fullmatch(r"sdk_internal_review_[0-9]{2,4}(?:_[0-9a-f]{32})?\.json", review_ref):
                review_ref = ""
            review_button = ('<button type="button" class="tm-important-review-link"'
                             ' data-important-review-kind="attention" data-important-review-cycle="' +
                             _e(cycle_name) + '" data-important-review-incident="' +
                             _e(incident_id) + '" data-important-review-ref="' +
                             _e(review_ref) + '">查看本次接管审查详情</button>')
        elif (run and row.get("code") == "investigator_budget_review_required"
              and (cycle_name, timestamp) in budget_refs):
            review_button = ('<button type="button" class="tm-important-review-link"'
                             ' data-important-review-kind="budget" data-important-review-cycle="' +
                             _e(cycle_name) + '" data-important-review-index="' +
                             str(budget_refs[(cycle_name, timestamp)]) + '">查看本次预算审查对话</button>')
        if review_only:
            if review_button:
                rows.append(review_button)
            continue
        details = {k: v for k, v in row.items()
                   if k not in {"_display_cycle", "_cycle_name", "schema"}}
        raw = json.dumps(details, ensure_ascii=False, indent=2, default=str)
        identity = str(row.get("incident_id") or "") + ":" + lifecycle + ":" + timestamp
        rows.append(
            '<li class="tm-log-item tm-important-item tm-important-' + severity + '">'
            '<div class="tm-important-content">' + meta + heading + explanation + summary + review_button +
            '<details class="tm-important-more" data-important-key="' +
            _e(identity) + '"><summary>完整登记字段</summary>'
            '<pre>' + _e(raw) + '</pre></details></div></li>'
        )
    if review_only:
        return "".join(rows)
    return ('<div class="tm-run-events-scroll tm-important-scroll" tabindex="0" '
            'role="region" aria-label="重要事件时间线，可上下滚动">'
            '<ol class="tm-run-events-recent tm-important-timeline">' +
            "".join(rows) + '</ol></div>')



def _incident_timeline(events: Any, *, run: dict[str, Any] | None = None) -> str:
    """One visible case per incident; every append-only lifecycle row stays inspectable."""
    cases = incident_cases(events if isinstance(events, list) else [])
    if not cases:
        return ('<div class="tm-run-events-scroll tm-important-scroll" tabindex="0" '
                'role="region" aria-label="异常记录">'
                '<p class="tm-insight-empty">暂无系统异常登记。</p></div>')
    cards = []
    for case in cases:
        cycle = str(case["_display_cycle"])
        source = "任务级" if cycle == "任务级" else "Cycle " + cycle
        kind = str(case["kind"])
        title = _KIND_LABELS.get(kind, kind.replace("_", " "))
        code = str(case["code"])
        reason = _IMPORTANT_REASON_LABELS.get(code, "")
        note = str(case["summary"]).strip()
        lifecycle = str(case["lifecycle"])
        n = len(case["records"])
        count = (" · " + str(n) + " 条原始记录") if n > 1 else ""
        raw = json.dumps(case["records"], ensure_ascii=False, indent=2, default=str)
        identity = (str(case["incident_id"]) or kind + ":" + code + ":" + note[:40]) + ":" + cycle
        summary = ('<p class="tm-important-summary">' + _e(note) + '</p>'
                   if note and note.casefold() not in _IMPORTANT_SUMMARY_PLACEHOLDERS else "")
        cards.append(
            '<li class="tm-log-item tm-important-item tm-important-' +
            _e(case["severity"] if case["severity"] in _SEVERITY_LABELS else "minor") + '">'
            '<div class="tm-important-content">'
            '<div class="tm-important-meta"><span class="tm-important-cycle">' + _e(source) + '</span>'
            '<time datetime="' + _e(case["last_at"]) + '">' +
            _e(case["last_at"][:19].replace("T", " ")) + '</time>'
            '<span class="tm-important-lifecycle">最近记录：' +
            _e(_LIFECYCLE_LABELS.get(lifecycle, lifecycle)) + _e(count) + '</span></div>'
            '<div class="tm-important-heading"><strong class="tm-important-kind">' + _e(title) + '</strong></div>'
            + ('<p class="tm-important-reason">' + _e(reason) + '</p>' if reason else '') + summary +
            '<details class="tm-important-more" data-important-key="' + _e(identity) + '">'
            '<summary>查看完整生命周期（' + str(n) + ' 条）</summary><pre>' + _e(raw) +
            '</pre>' + _important_timeline(case["records"], run=run, audit_details=True,
                                          review_only=True) +
            '</details></div></li>'
        )
    return ('<div class="tm-run-events-scroll tm-important-scroll" tabindex="0" '
            'role="region" aria-label="异常记录（同一故障按案例合并）">'
            '<ol class="tm-run-events-recent tm-important-timeline">' +
            "".join(cards) + '</ol></div>')

def _investigator_goal_field() -> str:
    return ('<div class="tm-investigator-goal-field" aria-label="Investigator 当前科研 Goal">'
            '<div class="tm-investigator-goal-intro"><strong>当前 Goal</strong>'
            '<p>查看本次 Investigator 收到的科研任务与完成条件；可保存未生效修订草稿。</p></div>'
            + native_goal_trigger() + '</div>')


def _investigation_card(raw: Any, budget: Any = None,
                        budget_reviews: list[dict[str, Any]] | None = None,
                        *, cycle: Any = None) -> str:
    import re
    fields = _tui_pairs(raw)
    if not fields:
        return ('<section class="tm-tui-panel tm-agent-review tm-role-investigator" data-tui-pane="investigation">'
                '<div class="tm-tui-section-title">Investigation</div>'
                + _investigator_goal_field() +
                '<div class="tm-insight-empty">' + _e(raw) + '</div></section>')
    attempt = fields.get("Attempt")
    allowance = fields.get("Inv tokens")
    executor = fields.get("Current executor")
    if not executor:
        executor = next((line[len("Current executor "):].strip()
                         for line in str(raw or "").splitlines()
                         if line.startswith("Current executor ")), None)
    bits = []
    if attempt and attempt not in {"0", "-", "—"}:
        bits.append('<div class="tm-insight-stat"><span>Attempt</span><strong>' + _e(attempt) + '</strong></div>')
    if executor and executor not in {"-", "—"}:
        bits.append('<div class="tm-insight-stat"><span>执行者</span><strong>' + _e(executor) + '</strong></div>')
    budget_html = ""
    if allowance:
        parts = allowance.split("/", 1)
        reviews = budget.get("reviews") if isinstance(budget, dict) else None
        approvals = sum(row.get("decision") == "approve" for row in (budget_reviews or []))
        total_reviews = len(budget_reviews) if budget_reviews else (
            reviews if type(reviews) is int else 0)
        shown = approvals if budget_reviews and approvals else total_reviews
        count = ("一二三四五六七八九十"[shown-1] if 1 <= shown <= 10 else str(shown))
        label = ("已追加" if approvals or not budget_reviews else "已审查") + count + "次"
        badge = ('<button type="button" class="tm-budget-review-count" '
                 'data-budget-review-open aria-haspopup="dialog" '
                 'aria-controls="tm-investigator-budget-dialog" '
                 'aria-label="查看 Investigator 额度审批历史：' +
                 _e(label) + '">' + _e(label) + '</button>'
                 if shown > 0 else "")
        budget_html = ('<div class="tm-budget-label"><span>Investigator 额度</span>'
                       '<strong>' + _e(allowance) + '</strong>' + badge + '</div>')
        if len(parts) == 2:
            def parse_k(value: str) -> float | None:
                m = re.fullmatch(r"\s*([\d,.]+)\s*(k|m)?\s*", value, re.I)
                if not m:
                    return None
                return float(m.group(1).replace(",", "")) * {"k":1e3, "m":1e6}.get((m.group(2) or "").lower(), 1)
            used, granted = parse_k(parts[0]), parse_k(parts[1])
            if used is not None and granted and granted > 0:
                pct = max(0, min(100, used / granted * 100))
                budget_html += '<div class="tm-budget-track" role="meter" aria-label="Investigator 额度使用" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + f'{pct:.1f}' + '"><span style="width:' + f'{pct:.1f}' + '%"></span></div>'
    return ('<section class="tm-tui-panel tm-agent-review tm-role-investigator" data-tui-pane="investigation">'
            '<div class="tm-tui-section-title">Investigation <span>当前 Cycle</span></div>'
            '<div class="tm-insight-stats">' + "".join(bits) + '</div>' +
            _investigator_goal_field() + budget_html +
            (render_budget_review_template(budget_reviews or [], cycle) if badge else "") +
            '<details class="tm-insight-more"><summary>调查轮次与原始记录</summary><pre>' + _e(raw) +
            '</pre></details></section>')


def _judge_card(raw: Any, *, reviews: list[dict[str, Any]] | None = None, cycle: Any = None) -> str:
    fields = {}
    for line in str(raw or "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    entries = []
    def present(value: Any) -> bool:
        return bool(value) and str(value).strip().lower() not in {"-", "—", "idle", "(none)", "none", "[]"}
    for key, title in (("Previous verdict", "上次裁决"),
                       ("Reason", "待解决问题"), ("Objections", "Judge 异议"),
                       ("Suggested start", "建议起点")):
        value = fields.get(key)
        if present(value):
            entries.append('<div class="tm-judge-item"><span>' + title + '</span><p>' + _e(value) + '</p></div>')
    status = fields.get("Status")
    if present(status):
        entries.insert(0, '<div class="tm-judge-item"><span>当前状态</span><p>' + _e(status) + '</p></div>')
    if not entries:
        entries.append('<p class="tm-insight-empty">暂无可展示的本轮裁决结果</p>')
    reworks = fields.get("Reworks")
    extra = ('<span class="tm-judge-rework">Rework ' + _e(reworks) + '</span>') if reworks and not reworks.startswith("0 /") else ''
    reviewed = len(reviews or [])
    detail_label = '查看裁决详情' if reviewed == 0 else f'查看 {reviewed} 次裁决'
    detail = ('<button type="button" class="tm-judge-review-count" data-judge-review-open '
              'aria-haspopup="dialog" aria-controls="tm-judge-review-dialog">' +
              _e(detail_label) + ' ↗</button>')
    return ('<section class="tm-tui-panel tm-agent-review tm-role-judge" data-tui-pane="judge">'
            '<div class="tm-tui-section-title">Judge <span>Branch 评估</span>' + extra + '</div>' +
            '<div class="tm-judge-detail-link">' + detail + '</div>' +
            "".join(entries) + render_judge_review_template(reviews or [], cycle) +
            '<details class="tm-insight-more"><summary>全部 Judge 字段</summary><pre>' +
            _e(raw) + '</pre></details></section>')


def _token_breakdown(raw: Any, totals: Any) -> str:
    info = totals if isinstance(totals, dict) else {}
    turn_lines = str(raw or "").split("CYCLE USAGE", 1)[0].splitlines()
    turn = _tui_pairs("\n".join(turn_lines))
    def num(value: Any) -> str:
        return _tokens(value)
    groups = []
    for name, item in (("当前 Turn", None), ("当前 Cycle", info.get("cycle")), ("任务累计", info.get("project"))):
        if item is None:
            values = {"输入":turn.get("Input", "未记录"),
                      "缓存输入":turn.get("Cached", "未记录"),
                      "非缓存输入":turn.get("Uncached", "未记录"),
                      "输出":turn.get("Output", "未记录"),
                      "推理输出":turn.get("Reasoning", "未记录")}
        else:
            if not isinstance(item, dict):
                item = {}
            ins, cached = item.get("tokens_in"), item.get("cached_input_tokens")
            values = {"输入":num(ins),
                      "缓存输入":num(cached),
                      "非缓存输入":num(max(0, ins-cached)) if isinstance(ins,int) and isinstance(cached,int) else "未记录",
                      "输出":num(item.get("tokens_out")),
                      "推理输出":num(item.get("reasoning_tokens"))}
        cells = "".join('<div><span>' + _e(k) + '</span><strong>' + _e(v) + '</strong></div>'
                        for k,v in values.items())
        groups.append('<div class="tm-token-scope"><h4>' + name + '</h4>' + cells + '</div>')
    project = info.get("project")
    cycle = info.get("cycle")
    hint = "本轮缓存占比 " + _cache_hit_rate(cycle) + " · 累计 " + _cache_hit_rate(project)
    return ('<section class="tm-tui-panel tm-token-section" data-tui-pane="tokens">'
            '<details class="tm-token-disclosure"><summary><strong>Token 构成</strong>'
            '<span>' + _e(hint) + '</span><span class="tm-summary-chevron" aria-hidden="true">⌄</span></summary>'
            '<p class="tm-token-hint">缓存输入已包含在输入，推理输出已包含在输出，请勿重复相加。</p>'
            '<div class="tm-token-scopes">' + "".join(groups) + '</div>'
            '<details class="tm-insight-more"><summary>终端原始用量记录</summary><pre>' +
            _e(raw) + '</pre></details></details></section>')


def _tui_text(parts: dict[str, Any], key: str, *, cls: str = "") -> str:
    return f'<pre class="tm-tui-text {cls}" data-tui-pane="{key}">{_e(parts.get(key, "(none)"))}</pre>'


def _worker_state_hint(status: str) -> str:
    """Explain the safe operator choices for every Worker lifecycle state."""
    status = str(status or "").upper()
    return {
        "RUNNING": "Cycle 正在运行。暂停会等待安全边界；终止会在安全检查点执行且不可恢复。",
        "PAUSED": "Cycle 已暂停，可恢复原 Cycle，或释放前沿后让调度器选择其他前沿。",
        "FAILED": "当前 Cycle 失败；可尝试恢复现有 Cycle，或释放其前沿占用。",
        "NEEDS_ATTENTION": "当前 Cycle 需要处理；「保全成果并结束本轮」在核验科学阻断后，调用已有 preserve_research 整理阶段结果；仅成功后释放 Worker。不可恢复旧 Cycle。",
        "INTERRUPTED": "Cycle 中断；可恢复同一 Cycle，或释放前沿。",
        "ORPHANED": "登记进程未通过身份校验；释放操作会由后端再次核对进程是否存活。",
        "OVER": "当前 Cycle 已结束，不能 Resume；调度器会分配可用科研前沿并启动新 Cycle。",
        "RELEASED": "当前 Worker 已释放前沿占用，旧 Cycle 不会恢复；若是科研保全，报告和 Canvas 已存档。调度器会独立选择新 Cycle。",
        "PRESERVING": "正在运行独立的只读 preserve_research 归档 Stage。成功后才结束 Cycle、释放 Worker 和继续调度；失败时保留原占用及科研数据。",
        "CANCELLED": "当前 Cycle 已取消；可启动调度器继续寻找其他可用前沿。",
        "STOPPED": "Worker 已停止；可重新启动调度器分配新的 Cycle。",
        "FAILED_START": "Worker 启动失败；可重新启动调度器，让它重新分配可用前沿。",
        "COMPLETE": "任务已完成或达到终止条件，不自动启动新 Cycle；刷新以确认最终状态。",
        "COMPLETED": "任务已完成或达到终止条件，不自动启动新 Cycle；刷新以确认最终状态。",
        "RESERVED": "调度器已预留前沿，Worker 尚在启动；等待或刷新，不要重复发起调度。",
        "STARTING": "Worker 正在启动；等待启动结果，或刷新确认状态。",
        "PAUSE_REQUESTED": "暂停请求已提交，正在等待 SDK 安全边界；刷新查看最终状态。",
        "STOP_REQUESTED": "终止请求已提交，正在等待 SDK 安全边界；刷新查看最终状态。",
    }.get(status, "当前状态不在已知生命周期枚举中；为避免误操作，只提供刷新状态。")


def _worker_summary_controls(run: dict[str, Any], worker_id: str | None) -> str:
    """Exact Worker-scoped actions inside its State card, never Master controls."""
    if not worker_id:
        return ""
    status = str(run.get("state") or "").upper()
    available = {
        "RUNNING": ("pause", "kill"),
        "PAUSED": ("resume", "release"),
        "FAILED": ("resume", "release"),
        "NEEDS_ATTENTION": ("resume", "preserve_release", "release"),
        "INTERRUPTED": ("resume", "release"),
        "ORPHANED": ("release",),
        "PRESERVING": ("preserve_release",),
        "OVER": ("run_next",),
        "RELEASED": ("run_next",),
        "CANCELLED": ("run_next",),
        "STOPPED": ("run_next",),
        "FAILED_START": ("run_next",),
    }
    labels = {"run_next": "调度新 Cycle", "pause": "暂停 · 之后继续",
              "kill": "终止当前 Cycle · 不可恢复", "resume": "恢复",
              "release": "放弃当前占用（不归档）",
              "preserve_release": "保全成果并结束本轮"}
    buttons = "".join(f'<button type="button" data-worker-action="{_e(action)}">'
                      f'{_e(labels[action])}</button>' for action in available.get(status, ()))
    if not buttons:
        # Transitional, completed, and unknown states remain inspectable but
        # never gain a guessed mutation action.
        buttons = '<button type="button" data-worker-refresh>刷新状态</button>'
    return (f'<div class="tm-worker-controls tm-worker-inline" data-worker-id="{_e(worker_id)}" '
            f'data-cycle="{_e(run.get("cycle") or "")}" data-status="{_e(status)}" '
            f'aria-label="Agent {worker_id.split("-")[-1]} 独立操作">{buttons}</div>') if buttons else ""


def _startup_progress_dialog() -> str:
    """A single blocking progress surface shared by Task and Worker start actions."""
    return '''<dialog id="tm-start-progress-dialog" class="tm-start-progress-dialog"
      aria-labelledby="tm-start-progress-title" aria-describedby="tm-start-progress-description"
      aria-busy="true">
      <div class="tm-start-progress-spinner" data-start-spinner aria-hidden="true"></div>
      <h3 id="tm-start-progress-title">正在开启运行…</h3>
      <p id="tm-start-progress-description">正在提交启动请求，请勿重复点击。</p>
      <ol class="tm-start-progress-steps">
        <li data-start-step="submit" class="is-current"><span class="tm-start-progress-index">1</span><span>提交启动请求</span></li>
        <li data-start-step="verify" class="is-pending"><span class="tm-start-progress-index">2</span><span>核验进程身份与 Cycle</span></li>
        <li data-start-step="running" class="is-pending"><span class="tm-start-progress-index">3</span><span>确认 Worker / Task Runner 正在运行</span></li>
      </ol>
      <div class="tm-start-progress-actions">
        <button type="button" data-start-recheck hidden>重新检查状态</button>
        <button type="button" data-start-close hidden>关闭弹窗</button>
      </div>
    </dialog>'''


def _startup_progress_script(observer_id: str) -> str:
    """Poll the existing read-only view; only a server-verified RUNNING state is success."""
    return r"""<script>
(function() {
  const dialog = document.getElementById('tm-start-progress-dialog');
  if (!dialog || window.tmStartProgress) return;
  const runId = __RUN_ID__;
  const title = dialog.querySelector('#tm-start-progress-title');
  const description = dialog.querySelector('#tm-start-progress-description');
  const spinner = dialog.querySelector('[data-start-spinner]');
  const steps = [...dialog.querySelectorAll('[data-start-step]')];
  const recheck = dialog.querySelector('[data-start-recheck]');
  const close = dialog.querySelector('[data-start-close]');
  const stageOrder = {submit: 0, verify: 1, running: 2};
  let current = null;

  function render(phase, message) {
    title.textContent = phase === 'success' ? '运行已确认'
      : phase === 'unknown' ? '暂未确认运行' : '正在开启运行…';
    description.textContent = message;
    spinner.classList.toggle('is-success', phase === 'success');
    spinner.classList.toggle('is-unknown', phase === 'unknown');
    spinner.textContent = phase === 'success' ? '✓' : phase === 'unknown' ? '!' : '';
    dialog.setAttribute('aria-busy',
      ['submit', 'verify', 'running'].includes(phase) ? 'true' : 'false');
    const active = Object.prototype.hasOwnProperty.call(stageOrder, phase)
      ? stageOrder[phase] : -1;
    steps.forEach((step, index) => {
      step.classList.toggle('is-current', index === active);
      step.classList.toggle('is-done', phase === 'success' || (active >= 0 && index < active));
      step.classList.toggle('is-pending', phase === 'unknown' || (active >= 0 && index > active));
    });
    recheck.hidden = phase !== 'unknown';
    close.hidden = phase !== 'unknown';
  }

  function shouldHoldRefresh() {
    return !!current && ['request', 'polling', 'success'].includes(current.phase);
  }

  function showUnknown(op, message) {
    if (current !== op) return;
    if (op.timer) clearTimeout(op.timer);
    op.phase = 'unknown';
    render('unknown', message + ' 为避免重复执行，不会自动重新发送启动请求；可以重新检查状态（只读）。');
  }

  async function poll(op) {
    if (current !== op || op.phase !== 'polling' || op.checking) return;
    if (Date.now() - op.pollStartedAt >= 45000) {
      showUnknown(op, '45 秒内未能确认进程正在运行。请求可能已受理，但当前状态仍不确定。');
      return;
    }
    op.checking = true;
    let requestTimer = null;
    try {
      const controller = new AbortController();
      requestTimer = setTimeout(() => controller.abort(), 12000);
      const workerQuery = op.worker ? '&worker=' + encodeURIComponent(op.worker) : '';
      const response = await fetch('/api/run/' + encodeURIComponent(runId)
        + '/view?tab=activity' + workerQuery, {
          cache: 'no-store', headers: {'Accept': 'text/html'}, signal: controller.signal
        });
      if (!response.ok) throw new Error('状态查询返回 HTTP ' + response.status);
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const screen = parsed.querySelector('.tm-tui-screen');
      if (!screen) throw new Error('没有读取到当前运行状态');
      const status = (screen.dataset.tuiStatus || 'UNKNOWN').toUpperCase();
      const verified = screen.dataset.runtimeVerified === 'true';
      let confirmed = false;

      if (op.scope === 'scheduler') {
        // Worker tabs are built from the same PID/start-ticks/Cycle identity
        // verification used by the console. Do not trust a raw runtime.json flag.
        confirmed = [...parsed.querySelectorAll('.tm-worker-tabs .tm-worker-tab small')]
          .some(node => node.textContent.trim().toUpperCase() === 'RUNNING');
      } else {
        const correctWorker = !op.worker || screen.dataset.tuiWorker === op.worker;
        confirmed = correctWorker && verified && ['RUNNING', 'ACTIVE'].includes(status);
      }

      if (confirmed) {
        if (op.timer) clearTimeout(op.timer);
        op.phase = 'success';
        render('success', '系统已核验当前进程身份与运行状态。页面即将刷新以显示最新 Cycle。');
        op.timer = setTimeout(() => {
          if (current === op) {
            dialog.close();
            window.location.reload();
          }
        }, 1200);
        return;
      }

      let message;
      if (op.worker && screen.dataset.tuiWorker !== op.worker) {
        message = '返回的状态与目标 Worker 不一致，尚未确认运行。';
      } else if (status === 'ORPHANED') {
        message = '当前 Worker 仍为 ORPHANED：进程身份未通过核验。历史 Cycle 快照不代表正在运行。';
      } else if (['STARTING', 'RECOVERING', 'RESERVED'].includes(status)) {
        message = '当前状态：' + status + '。系统正在启动或恢复，等待进程身份与 RUNNING 状态确认。';
      } else if (['RUNNING', 'ACTIVE'].includes(status) && !verified) {
        message = '页面显示 ' + status + '，但服务器尚未确认进程身份；不会提前报告成功。';
      } else {
        message = '当前状态：' + status + '。启动请求已受理，但尚未确认进程正在运行。';
      }
      render(['STARTING', 'RECOVERING', 'RESERVED'].includes(status) ? 'running' : 'verify', message);
    } catch (_error) {
      render('verify', '暂时无法读取最新状态；系统尚未确认运行，继续等待。不会重复提交启动请求。');
    } finally {
      if (requestTimer) clearTimeout(requestTimer);
      op.checking = false;
    }
    if (current === op && op.phase === 'polling') {
      op.timer = setTimeout(() => poll(op), 3500);
    }
  }

  function start(options) {
    // One outstanding start/recovery request per page; no hidden POST retries.
    if (current) return null;
    const config = options || {};
    const op = {
      action: config.action || '',
      label: config.label || '启动/恢复',
      worker: config.worker || null,
      scope: config.scope || 'task',
      phase: 'request',
      checking: false,
      timer: null,
      pollStartedAt: 0
    };
    current = op;
    render('submit', '正在提交“' + op.label + '”请求，请勿重复点击。');
    if (!dialog.open) dialog.showModal();
    return {
      accepted() {
        if (current !== op) return;
        if (op.timer) clearTimeout(op.timer);
        op.phase = 'polling';
        op.pollStartedAt = Date.now();
        if (!dialog.open) dialog.showModal();
        render('verify', '请求已受理，正在读取最新状态并核验进程身份。');
        poll(op);
      },
      failed(message) {
        if (current !== op) return;
        if (op.timer) clearTimeout(op.timer);
        op.phase = 'unknown';
        render('unknown', '启动请求未能确认：' + String(message || '未知错误')
          + '。不会自动重试；重新检查只读取状态，不会再次提交启动请求。');
      }
    };
  }

  dialog.addEventListener('cancel', event => {
    if (current && !['unknown', 'success'].includes(current.phase)) event.preventDefault();
  });
  recheck.addEventListener('click', () => {
    if (!current || current.phase !== 'unknown') return;
    current.phase = 'polling';
    current.pollStartedAt = Date.now();
    current.checking = false;
    render('verify', '正在重新读取状态；本次只查询状态，不会再次提交启动请求。');
    if (!dialog.open) dialog.showModal();
    poll(current);
  });
  close.addEventListener('click', () => {
    if (current && current.phase === 'unknown') dialog.close();
  });
  window.tmStartProgress = {start, shouldHoldRefresh};
})();
</script>""".replace("__RUN_ID__", json.dumps(observer_id))


def run_detail_body(run: dict[str, Any], *, tab: str = "activity", fragment: bool = False,
                    history_cycle: str = "", canvas_available: bool = False,
                    quota_snapshot: dict[str, Any] | None = None,
                    worker_id: str | None = None) -> str:
    """Mirror Textual compose() exactly; browser-specific controls stay outside.

    All nine primary areas use the canonical CLI render_* methods through the
    isolated read-only SDK worker, not alternative Web-derived scientific text.
    """
    from .run_tui_bridge import read_tui
    from .worker_console import (select_worker, tab_bar, live_task_runner,
                                 worker_batch_cycle_progress, STYLE as WORKER_STYLE)
    from .worker_settings_ui import top_bar, script as multi_script, STYLE as MULTI_STYLE
    from .model_preset_ui import dialog_html as preset_dialog, script as preset_script, CSS as PRESET_STYLE
    master_run = run
    run, workers, selected_worker = select_worker(run, worker_id)
    worker_tabs = tab_bar(master_run, workers, selected_worker)
    worker_cycle_progress = worker_batch_cycle_progress(master_run, selected_worker)
    master_running = live_task_runner(master_run, workers)
    pinned_worker_account = next(
        (str(w.get("account")) for w in workers if w.get("worker_id") == selected_worker
         and str(w.get("account")) in {"1", "2"}), None)
    # A historical released Worker cannot control a separate active Task Runner.
    worker_actions = ("" if master_running and selected_worker else
                      _worker_summary_controls(run, selected_worker))
    multi_bar = top_bar(master_run)

    # Retain the dedicated Stage Conversation URL for precise cross-Cycle
    # deep links from Recent Progress. Logs remains retired from the Web.
    secondary = "research" if tab in {"thread", "artifacts"} else (tab if tab in _TUI_SECONDARY else "")
    projection = read_tui(run, secondary=secondary)
    parts = projection.get("parts") or {}
    history_events = read_important_events(run) if not secondary else []
    from .cycle_history import (cycle_catalog as load_cycle_catalog, selected_cycle,
                                cycle_events, cycle_summary)
    task_root = Path(str(master_run.get("research_root") or "."))
    catalog = load_cycle_catalog(task_root) if master_run.get("research_root") and not secondary else []
    chosen_cycle = selected_cycle(catalog, history_cycle) if catalog else ""
    requested_history_cycle = (str(int(history_cycle)) if history_cycle and history_cycle.isascii()
                               and history_cycle.isdecimal() and len(history_cycle) <= 8 else "")
    selected_history_cycle = (chosen_cycle if requested_history_cycle and
                              requested_history_cycle == chosen_cycle else "")
    cycle_raw, cycle_source = (cycle_events(task_root, catalog, selected_history_cycle)
                               if catalog and selected_history_cycle else ("", []))
    summary_catalog = list(catalog[-10:])
    if selected_history_cycle and selected_history_cycle not in {item["id"] for item in summary_catalog}:
        selected_item = next((item for item in catalog if item["id"] == selected_history_cycle), None)
        if selected_item is not None:
            summary_catalog.append(selected_item)
    cycle_summaries = {item["id"]: cycle_summary(task_root, catalog, item["id"])
                       for item in summary_catalog}
    # The native TUI bridge renders a persisted Cycle snapshot. For Worker
    # mode, never let its possibly stale RUNNING label override the verified
    # Worker process state also used by Task List and Worker tabs.
    monitor_state = run_monitor.data(run)
    pause_pending = (not selected_worker and run.get("state") == "LIVE"
                     and str(monitor_state.get("status") or "").upper() == "PAUSED"
                     and str(monitor_state.get("phase") or "").lower() == "control_requested")
    status = (run.get("state") if selected_worker else projection.get("status")) or run.get("state") or run.get("raw_status")
    if pause_pending:
        status = "PAUSE REQUESTED"
    cycle = projection.get("cycle_id") or run.get("cycle")
    state_note = (
        '<p class="tm-tui-unavailable">' + _e(projection.get("unavailable")) + '</p>'
        if projection.get("unavailable") else ""
    )
    if pause_pending:
        state_note += ('<p class="tm-tui-unavailable" role="status">'
                       '暂停请求已收到；当前 Agent 仍在执行，等待安全检查点后才会暂停。</p>')
    if selected_worker and master_running:
        state_note += ('<p class="tm-tui-unavailable" role="status">'
                       '当前 Task Runner 正在运行；此页是旧 Worker 的历史状态。'
                       f'<a href="/run/{_e(master_run.get("observer_id"))}/">查看当前运行</a></p>')
    if selected_worker and str(status or "").upper() == "ORPHANED":
        state_note += ('<p class="tm-tui-unavailable" role="status">'
                       'Worker 状态文件仍记录为运行中，但登记进程未通过身份校验；'
                       '下方 Cycle 内容为历史快照，并不代表当前仍在执行。</p>')
    # A Worker may be registered before its Cycle snapshot is written. Keep
    # its Worker-scoped controls available; never expose Master actions here.
    if selected_worker and not secondary and not parts.get("header") and worker_actions:
        state_note += '<button type="button" data-worker-state-open>Agent 操作</button>'
    status_key = str(status or "").upper()
    runtime_verified = ((not selected_worker and master_running) or
                        (bool(selected_worker) and status_key in {"RUNNING", "ACTIVE", "STARTING", "RECOVERING"}))
    short_identity = (str(parts.get("identity") or "").split("Supervisor:", 1)[0]
                      .split("错误排查:", 1)[0].strip())
    ident = str(run.get("observer_id") or "")
    titlebar = f'''<div class="tm-tui-titlebar">
        <div class="tm-tui-title-main">
            <span class="tm-tui-eyebrow">TASK CONSOLE</span>
            <strong>{_e(run.get("task_name"))}</strong>
            <span class="tm-tui-identity">{_e(short_identity)}</span>
        </div>
        <div class="tm-tui-title-actions">
          <span class="tm-run-freshness" data-run-freshness role="status" aria-live="polite">初始只读快照 · 8 秒自动检查</span>
          <a class="tm-plan-header-link" href="/research-plan/?observer_id={_e(ident)}" title="查看或编辑当前任务的 Research Plan" style="font-size:12px;white-space:nowrap;color:#bfdbfe">Research Plan ↗</a>
          <a class="tm-run-settings-link" href="/settings/?observer_id={_e(ident)}"
             aria-label="运行设置" title="运行设置">
             <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
               <path d="M12 8.7a3.3 3.3 0 1 0 0 6.6 3.3 3.3 0 0 0 0-6.6Z"/>
               <path d="M10.3 2.5h3.4l.5 2.1 1.6.9 2.1-.6 1.7 2.9-1.6 1.5v1.8l1.6 1.5-1.7 2.9-2.1-.6-1.6.9-.5 2.1h-3.4l-.5-2.1-1.6-.9-2.1.6-1.7-2.9 1.6-1.5V9.3L4.4 7.8l1.7-2.9 2.1.6 1.6-.9Z" transform="translate(0 2.2)"/>
             </svg>
          </a>
        </div>
    </div>'''
    if secondary:
        body = f'''<div class="tm-tui-secondary">
            <section class="tm-tui-panel tm-tui-secondary-panel">
                <div class="tm-tui-section-title">{_e(_TUI_SECONDARY[secondary])}</div>
                {(stage_shell() if secondary == "events" else
                  _frozen_research_view(projection.get("frozen_hypothesis")))}
            </section>
            <footer class="tm-tui-footer" aria-label="Secondary view actions">
                <button type="button" data-tui-view=""><kbd>Esc</kbd> 返回主屏</button>
                {'' if secondary == 'events' else '<button type="button" data-tui-copy="secondary"><kbd>O</kbd> 复制内容</button>'}
                <span>只读内容 · Cycle {_e(cycle)}</span>
            </footer>
        </div>'''
    else:
        if canvas_available:
            explore_map = (
                '<a class="tm-explore-map" data-explore-map '
                'href="/research-explorer/?observer_id=' + _e(ident) + '">'
                '<span class="tm-explore-map-icon" aria-hidden="true">'
                '<svg viewBox="0 0 24 24"><circle cx="7" cy="7" r="2"/>'
                '<circle cx="17" cy="9" r="2"/><circle cx="11" cy="18" r="2"/>'
                '<path d="M9 8l6 1M16 11l-4 5M8 9l2 7"/></svg></span>'
                '<span class="tm-explore-map-body"><strong>Explore map</strong>'
                '<span>探索当前任务的 Canvas 图谱与 Markdown</span></span>'
                '<span class="tm-explore-map-arrow" aria-hidden="true">→</span></a>'
            )
        else:
            explore_map = ('<div class="tm-explore-map tm-explore-map-unavailable" '
                           'data-explore-map><span class="tm-explore-map-body">'
                           '<strong>Explore map</strong>'
                           '<span>当前任务暂未提供可阅读的科研画布</span>'
                           '</span></div>')
        # A concise read-only projection of canonical Textual state, with
        # low-signal history accessible through disclosure rather than repeated panels.
        body = f'''<div class="tm-tui-primary">
            {(quota_wait_banner(run, run_monitor.data(run), quota_snapshot, pinned_worker_account)
              if _quota_pause(run, run_monitor.data(run)) else supervisor_banner(run, run_monitor.data(run)))}
            <section class="tm-tui-header tm-tui-panel">
                <div class="tm-tui-section-title">运行概况</div>
                {_tui_summary(parts.get("header", "(none)"),
                              attention=projection.get("attention"), show_codex_quota=True,
                              quota_snapshot=quota_snapshot, worker_account=pinned_worker_account, explore_map_card=explore_map,
                              resume_available=can_resume_state(run_monitor.data(run)),
                              run_available=(status_key in {"OVER", "CANCELLED", "STOPPED", "IDLE", "NEW"}),
                              allow_process_actions=not bool(selected_worker),
                              effective_status=status_key if selected_worker or pause_pending else None,
                              worker_state_actions=bool(selected_worker and worker_actions),
                              worker_cycle_progress=worker_cycle_progress)}
                {quota_auto_card(projection.get("quota_auto_adjust"),
                                 settings_html=settings_panel(run),
                                 quota_switch_html=quota_auto_switch(run))}
                <div class="tm-tui-section-title tm-tui-workflow-title">Agent 状态 <span>六个科研阶段</span></div>
                {_tui_workflow_table(parts.get("workflow", "(none)"),
                                     hot_route_capable=bool((run_monitor.data(run).get("sdk_runtime_capabilities") or {}).get("hot_role_route_v1")),
                                     settings=projection.get("role_settings"), catalog=projection.get("model_catalog"), role_usage=projection.get("role_usage"))}
            </section>
            <div class="tm-tui-mid">
                <div class="tm-tui-activity-col">
                    {_run_focus(parts, status, projection.get("activity_detail"),
                                projection.get("recent_events"), cycle)}
                    <section class="tm-tui-panel tm-tui-history-panel" data-tui-pane="last">
                        <div class="tm-tui-section-title tm-history-switch" role="tablist" aria-label="任务事件时间线">
                            <button type="button" class="tm-history-tab" role="tab" id="tm-history-tab-recent" aria-controls="tm-history-recent" aria-selected="true" data-history-view="recent">最近进展</button>
                            <button type="button" class="tm-history-tab" role="tab" id="tm-history-tab-important" aria-controls="tm-history-important" aria-selected="false" data-history-view="important">重要事件</button>
                            <button type="button" class="tm-history-tab" role="tab" id="tm-history-tab-incidents" aria-controls="tm-history-incidents" aria-selected="false" data-history-view="incidents">异常记录</button>
                        </div>
                        <div id="tm-history-recent" data-history-panel="recent" role="tabpanel" aria-labelledby="tm-history-tab-recent">
                            {_recent_timeline(cycle_raw if catalog else parts.get("last"),
                                              cycle_source if catalog else projection.get("recent_events"),
                                              cycle_catalog=catalog if catalog else None,
                                              selected_cycle_id=selected_history_cycle, worker_id=selected_worker,
                                              cycle_summaries=cycle_summaries)}
                        </div>
                        <div id="tm-history-important" data-history-panel="important" role="tabpanel" aria-labelledby="tm-history-tab-important" hidden>
                            {_important_timeline(history_events, run=run)}
                        </div>
                        <div id="tm-history-incidents" data-history-panel="incidents" role="tabpanel" aria-labelledby="tm-history-tab-incidents" hidden>
                            {_incident_timeline(history_events, run=run)}
                        </div>
                    </section>

                </div>
                <div class="tm-tui-center">
                    {_frozen_hypothesis_card(projection.get("frozen_hypothesis"), worker_id=selected_worker)}
                    {_investigation_card(parts.get("investigation", "(none)"),
                                        projection.get("investigator_budget"),
                                        read_budget_reviews(run), cycle=cycle)}
                    {_judge_card(parts.get("judge", "(none)"), reviews=read_judge_reviews(run), cycle=cycle)}
                    {_token_breakdown(parts.get("tokens", "(none)"), projection.get("usage_totals"))}
                </div>
            </div>
        </div>'''
    worker_dialog = (f'''<dialog id="tm-worker-state-dialog" class="tm-worker-dialog"
      aria-label="当前 Agent 操作"><h3>Agent {selected_worker.split("-")[-1]} · {_e(status_key)}</h3>
      <p>{_e(_worker_state_hint(status_key))}</p>
      <p>操作只作用于当前 Agent；终止将在安全检查点结束当前 Cycle，且不可恢复。</p>
      {worker_actions}<form method="dialog"><button type="submit">取消</button></form>
    </dialog>''' if worker_actions and not secondary else "")
    cockpit = f'''<section class="tm-tui-screen" data-tui-screen="{secondary or 'primary'}"
      aria-label="Sciver TUI" data-tui-cycle="{_e(cycle)}" data-tui-status="{_e(status)}"
      data-tui-worker="{_e(selected_worker or 'none')}" data-runtime-verified="{str(runtime_verified).lower()}">
      {multi_bar}{titlebar}{worker_tabs}{state_note}{body}{worker_dialog}
    </section>'''
    if fragment:
        return cockpit
    # State/Cycle cards own the visible controls. Keep only a nonvisual SDK
    # action adapter and dialogs (State, Run, Kill and Cycle use one API).
    management_html = control_panel(run) if not secondary and not selected_worker else ""
    return f'''<section class="tm-run-workspace tm-run-detail tm-web-tui" aria-label="Task monitoring">
      <div id="tm-run-live">{cockpit}</div>
      {management_html}
    </section>
    <style>
    {SUPERVISOR_STYLE}
    {WORKER_STYLE}
    {MULTI_STYLE}
    {PRESET_STYLE}
    /* Inline Cycle limit: editing lives inside the Cycle metric card. */
    [data-cycle-max-form][hidden] {{display:none !important;}}
    [data-cycle-max-form]:not([hidden]) {{display:inline-flex;align-items:center;flex-wrap:wrap;gap:6px;margin:7px 0 0;}}
    [data-cycle-max-form] label {{font-size:11px;color:#cbd5e1;}}
    [data-cycle-max-form] input {{width:76px;padding:5px;border-radius:6px;border:1px solid #64748b;background:#171c28;color:#fafafa;}}
    [data-cycle-max-form] button {{padding:5px 9px;border:1px solid #536178;border-radius:6px;background:#263144;color:#eef2fa;cursor:pointer;font-size:11px;}}
    [data-cycle-max-form] button[type=submit] {{background:#334f76;border-color:#567dac;}}
    [data-cycle-max-form] small {{flex-basis:100%;color:#fbbf24;font-size:11px;}}
    .tm-worker-inline {{display:flex;gap:6px;flex-wrap:wrap;padding:5px 0 0;}}
    .tm-worker-inline button {{padding:5px 9px;font-size:11px;}}
    .tm-route-save-dialog {{ box-sizing:border-box; width:min(510px,calc(100vw - 30px)); margin:auto;
      border-radius:12px; border:1px solid #637594; color:#e4eaf5; background:#1c2635;
      box-shadow:0 20px 65px #0009; padding:20px; }}
    .tm-route-save-dialog::backdrop {{ background:#000b; }}
    .tm-route-save-dialog strong {{ font-size:16px; }}
    .tm-route-save-dialog p {{ color:#bac6d7; font-size:12px; line-height:1.65; }}
    .tm-route-save-dialog label {{ display:flex; align-items:flex-start; gap:9px;
      margin:10px 0; padding:11px; border:1px solid #475569; border-radius:8px;
      font-size:12px; line-height:1.6; cursor:pointer; }}
    .tm-route-save-dialog label:has(input:checked) {{ border-color:#93c5fd; background:#273950; }}
    .tm-route-save-dialog footer {{ display:flex; justify-content:flex-end; gap:10px; margin-top:16px; }}
    .tm-route-save-dialog button {{ padding:8px 13px; border-radius:8px; background:#2b3649;
      border:1px solid #596b89; color:#e4eaf5; cursor:pointer; }}
    .tm-route-save-dialog button[value="save"] {{ color:#fff; background:#275490; border-color:#8ab5ec; }}
    button.tm-tui-pending[data-route-activation-edit] {{ appearance:none; border:0;
      padding:0; background:transparent; text-align:left; cursor:pointer; color:#fcd34d; }}
    button.tm-tui-pending[data-route-activation-edit]:hover {{ text-decoration:underline; }}
    .tm-start-progress-dialog {{ box-sizing:border-box; width:min(440px,calc(100vw - 28px)); max-width:440px;
      border:1px solid #5b7190; border-radius:16px; background:#171e2a; color:#edf4ff; padding:24px;
      box-shadow:0 28px 90px #000c; margin:auto; }}
    .tm-start-progress-dialog::backdrop {{ background:rgba(3,7,18,.76); backdrop-filter:blur(3px); }}
    .tm-start-progress-dialog h3 {{ margin:14px 0 7px; font-size:19px; }}
    .tm-start-progress-dialog p {{ color:#b7c4d7; font-size:13px; line-height:1.75; }}
    .tm-start-progress-spinner {{ width:42px; height:42px; border:3px solid #35465e; border-top-color:#86baff;
      border-radius:50%; animation:tm-start-spin .8s linear infinite; display:grid; place-items:center;
      color:#86efac; font-size:28px; font-weight:800; }}
    .tm-start-progress-spinner.is-success {{ animation:none; border-color:#287a56; background:#123d2d; }}
    .tm-start-progress-spinner.is-unknown {{ animation:none; border-color:#b7791f; background:#3f2f12; color:#fcd34d; }}
    @keyframes tm-start-spin {{ to {{ transform:rotate(360deg); }} }}
    .tm-start-progress-steps {{ list-style:none; padding:0; margin:18px 0 0; display:grid; gap:8px; }}
    .tm-start-progress-steps li {{ display:flex; align-items:center; gap:10px; padding:9px 10px; border:1px solid #334155;
      border-radius:9px; color:#8796aa; font-size:12px; }}
    .tm-start-progress-steps li.is-current {{ border-color:#648ab8; background:#23344b; color:#f8fafc; }}
    .tm-start-progress-steps li.is-done {{ border-color:#28654e; color:#a7e4c3; }}
    .tm-start-progress-index {{ display:grid; place-items:center; width:22px; height:22px; flex:0 0 22px;
      border:1px solid currentColor; border-radius:50%; font-size:11px; }}
    .tm-start-progress-actions {{ display:flex; justify-content:flex-end; gap:9px; flex-wrap:wrap; margin-top:16px; }}
    .tm-start-progress-actions button {{ padding:9px 12px; border:1px solid #52627a; border-radius:8px;
      background:#2b3649; color:#edf2fa; cursor:pointer; }}
    .tm-start-progress-actions [hidden] {{ display:none !important; }}
    </style>
    {preset_dialog()}
    <dialog id="tm-route-save-dialog" class="tm-route-save-dialog" aria-label="设置 Agent 模型生效时间">
      <form method="dialog">
        <strong>保存模型设置</strong>
        <p data-route-save-description></p>
        <label><input type="radio" name="tm-route-activation" value="next_invocation" checked>
          <span>当前 Cycle 的下一次安全模型调用（推荐）
          <small>不打断正在执行的 Native Goal 或 Turn；当前 Runner 不支持则等到新版 Run/Resume。</small></span></label>
        <label><input type="radio" name="tm-route-activation" value="next_cycle">
          <span>仅下一 Cycle 生效
          <small>不修改当前 Cycle 的模型，保留以前的生效方式。</small></span></label>
        <footer><button type="submit" value="cancel">取消</button>
          <button type="submit" value="save">保存模型及生效方式</button></footer>
      </form>
    </dialog>
    <dialog id="tm-investigator-budget-dialog" class="tm-budget-review-dialog"
            aria-label="Investigator 额度审批历史">
      <div data-budget-modal-content></div>
    </dialog>
    <dialog id="tm-important-review-dialog" class="tm-budget-review-dialog" aria-label="Agent 接管对话">
      <div class="tm-budget-modal-header"><h2>本次 Agent 接管记录</h2>
        <button type="button" data-important-review-close>关闭</button></div>
      <div data-important-review-content aria-live="polite"></div>
    </dialog>
    {_startup_progress_dialog()}
    {_startup_progress_script(ident)}
    {native_goal_modal(ident)}
    {judge_review_dialog()}
    {stage_script(ident)}
    {budget_trace_script(ident)}
    {important_review_script(ident)}
    {multi_script(ident)}
    {preset_script(ident, selected_worker)}
    <script>(function() {{
      const host=document.getElementById('tm-run-live');
      let lastRunSync=new Date().toLocaleTimeString();
      function runFreshness(text){{
        const target=host?.querySelector('[data-run-freshness]');
        if(target)target.textContent=text;
      }}
      runFreshness('页面快照 · '+lastRunSync+' · 每 8 秒检查');
      const selectedWorker={json.dumps(selected_worker)};
      const selectedHistoryCycle={json.dumps(selected_history_cycle)};
      const cycleHistoryLoads=new Set();
      if(selectedWorker) document.addEventListener('tm-cycle-max-save',async event=>{{
        // The Cycle limit belongs to the Master. Only the selected Worker's
        // scientific controls are Worker-scoped; use the Master guard here.
        const ctx=document.querySelector('[data-worker-request-context]');
        if(!ctx){{event.detail.complete(false,'任务状态不可用，请刷新页面');return;}}
        try{{
          const response=await fetch('/api/run/{ident}/action',{{method:'POST',
            headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action:'max_cycles',value:event.detail.value,
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:ctx.dataset.cycle,expected_status:ctx.dataset.status,
              expected_launch_id:ctx.dataset.launch}})}});
          const result=await response.json();
          if(!response.ok||!result.ok)throw new Error(result.error||'任务上限更新失败');
          event.detail.complete(true,'已更新任务 Cycle 上限');
        }}catch(error){{event.detail.complete(false,String(error.message||error));}}
      }});
      document.addEventListener('click',async event=>{{
        const refreshButton=event.target.closest?.('[data-worker-refresh]');
        if(refreshButton){{location.reload();return;}}
        const button=event.target.closest?.('[data-worker-action]');
        if(!button)return;
        const controls=button.closest('[data-worker-id]');
        if(!controls||!selectedWorker)return;
        const next=button.dataset.workerAction==='run_next';
        const context=next?document.querySelector('[data-worker-request-context]'):controls;
        if(!context){{alert('任务状态不可用，请刷新页面');return;}}
        const label=next?'启动调度器，分配新的科研 Cycle（不恢复已结束的 Cycle）':
          button.dataset.workerAction==='kill'?'不可恢复地终止 '+selectedWorker+' 当前 Cycle（将在安全检查点执行）':
          button.dataset.workerAction==='preserve_release'?'保全 '+selectedWorker+' 当前 Cycle 的阶段性成果并结束本轮。将使用一次独立只读 Investigator preserve_research 模型调用（可能计费）；成果报告和 Canvas 核验成功后才释放当前前沿。原 Cycle 不可恢复，不伪造 Judge 裁决。':
          button.dataset.workerAction==='release'?'只解除 '+selectedWorker+' 的占用，不会整理科研成果，也不能恢复原 Cycle':
          '确认操作 '+button.dataset.workerAction+' · '+selectedWorker;
        if(!confirm(label+'？'))return;
        const action=button.dataset.workerAction;
        const starting=next||action==='resume';
        const progress=starting?window.tmStartProgress?.start({{
          action:next?'start_worker_scheduler':'worker_control',
          label:next?'启动调度器':'恢复 '+selectedWorker,
          worker:next?null:selectedWorker,
          scope:next?'scheduler':'worker'
        }}):null;
        if(starting&&!progress){{alert('已有启动请求正在处理，请勿重复提交；请先检查当前状态。');return;}}
        button.disabled=true;
        try{{
          const reply=await fetch('/api/run/{ident}/action',{{method:'POST',
            headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action:next?'start_worker_scheduler':'worker_control',
              value:next?null:{{worker_id:selectedWorker,operation:action}},
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:next?context.dataset.cycle:controls.dataset.cycle,
              expected_status:next?context.dataset.status:controls.dataset.status,
              expected_launch_id:next?context.dataset.launch:''}})}});
          const response=await reply.json();
          if(!reply.ok||!response.ok)throw new Error(response.error||'Worker 操作被拒绝');
          if(starting)progress.accepted();
          else location.reload();
        }}catch(error){{
          if(starting)progress.failed(String(error.message||error));
          else{{alert(String(error.message||error));button.disabled=false;}}
        }}
      }});
      const views={{}};
      let view={json.dumps(secondary)};
      // Preserve exact Worker identity when navigating secondary readers,
      // including compatibility redirects from retired tab names.
      const currentParams=new URLSearchParams(location.search);
      const oldTab=currentParams.get('tab');
      const requestedWorker=currentParams.get('worker');
      if(typeof history.replaceState==='function' &&
          (['thread','artifacts','logs'].includes(oldTab) || requestedWorker!==selectedWorker && !!requestedWorker)) {{
        const fixed=new URLSearchParams();
        if(selectedWorker)fixed.set('worker',selectedWorker);
        const tabName=['thread','artifacts'].includes(oldTab)?'research':
          oldTab==='logs'?'':oldTab;
        if(tabName)fixed.set('tab',tabName);
        const query=fixed.toString();
        history.replaceState(null,'',location.pathname+(query?'?'+query:''));
      }}
      // First paint uses the last persisted, authentic provider percentages,
      // injected by the observer server. Cached endpoint is lightweight;
      // only the server initiates low-frequency (>=5m) provider RPCs.
      const workerQuotaAccount={json.dumps('account-'+pinned_worker_account if pinned_worker_account else None)};
      let codexQuotaSnapshot={json.dumps(quota_snapshot if isinstance(quota_snapshot, dict) else None, ensure_ascii=False, separators=(',', ':'))};
      const initialQuotaAccount=Array.isArray(codexQuotaSnapshot?.accounts)
        ?codexQuotaSnapshot.accounts.find(row=>row.account_home===(workerQuotaAccount||codexQuotaSnapshot.active_account)):null;
      let lastQuotaOkAt=Number.isFinite(initialQuotaAccount?.queried_at)
        ?initialQuotaAccount.queried_at*1000:null;
      let quotaStale=lastQuotaOkAt===null||Date.now()-lastQuotaOkAt>=300000;
      let quotaError=false,quotaPromise=null,quotaFollowupPending=false;
      function validQuotaPercent(value){{
        return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=100?value:null;
      }}
      function paintCodexQuota(){{
        const card=host.querySelector('[data-codex-quota]');
        if(!card)return;
        const account=card.querySelector('[data-quota-account]');
        const value=card.querySelector('[data-quota-value]');
        const week=card.querySelector('[data-quota-week]');
        const progress=card.querySelector('[role="progressbar"]');
        const fill=card.querySelector('[data-quota-fill]');
        const status=card.querySelector('[data-quota-status]');
        const active=workerQuotaAccount||codexQuotaSnapshot?.active_account;
        const label=active==='account-1'?'Codex 1':active==='account-2'?'Codex 2':'当前账号未知';
        const current=Array.isArray(codexQuotaSnapshot?.accounts)
          ?codexQuotaSnapshot.accounts.find(row=>row.account_home===active):null;
        // Match Services: the server computes remaining_percent from the
        // provider window. Do not invert or re-round a separate percentage.
        const windows=current?.ok===true?[current.primary,current.secondary].filter(w=>
          w&&validQuotaPercent(w.remaining_percent)!==null):[];
        const windowLabel=w=>w.window_duration_mins===300?'5 小时':
          w.window_duration_mins===10080?'7 天':
          Number.isInteger(w.window_duration_mins)&&w.window_duration_mins>0
            ?w.window_duration_mins+' 分钟':'时长未知窗口';
        const remaining=windows.length?windows[0].remaining_percent:null;
        const firstLabel=windows.length?windowLabel(windows[0]):'额度窗口';
        const fiveText=firstLabel+'剩余：'+(remaining===null?'暂不可用':remaining+'%');
        const weekText=windows.length>1?windowLabel(windows[1])+'剩余：'+windows[1].remaining_percent+'%':'';
        // Even painting identical textContent replaces Text nodes, which
        // causes unnecessary visual/MutationObserver churn every 8 seconds.
        if(account.textContent!==label)account.textContent=label;
        if(value.textContent!==fiveText)value.textContent=fiveText;
        if(week.textContent!==weekText)week.textContent=weekText;
        week.hidden=windows.length<2;
        if(progress.getAttribute?.('aria-label')!==firstLabel+'剩余额度')
          progress.setAttribute('aria-label',firstLabel+'剩余额度');
        const width=(remaining===null?0:remaining)+'%';
        if(fill.style.width!==width)fill.style.width=width;
        if(remaining===null)progress.removeAttribute('aria-valuenow');
        else if(progress.getAttribute?.('aria-valuenow')!==String(remaining))
          progress.setAttribute('aria-valuenow',String(remaining));
        // An old but accurate provider reading is explicitly a cached reading,
        // not a new 0% or 'querying' state after a transient API failure.
        const time=lastQuotaOkAt ? new Date(lastQuotaOkAt).toLocaleTimeString('zh-CN',
          {{hour:'2-digit',minute:'2-digit'}}) : '';
        const freshness=lastQuotaOkAt
          ?(quotaError?'刷新失败 · 显示上次成功 '+time:
            (quotaStale?'历史记录 · 更新于 ':'更新于 ')+time+
            (codexQuotaSnapshot?.refreshing?' · 后台更新中':''))
          :('暂无历史记录'+(codexQuotaSnapshot?.refreshing?' · 后台更新中':' · 等待低频更新'));
        if(status&&status.textContent!==freshness)status.textContent=freshness;
      }}
      function loadCodexQuota(){{
        if(document.hidden)return Promise.resolve();
        // A tab load and a periodic timer may collide. Share the in-flight
        // request rather than issuing another slow provider RPC.
        if(quotaPromise)return quotaPromise;
        quotaPromise=(async()=>{{
        try{{
          const response=await fetch('/codex-usage-cached.json',{{cache:'no-store'}});
          if(!response.ok)throw new Error('historical quota endpoint unavailable');
          const candidate=await response.json();
          if(!Array.isArray(candidate?.accounts)||
              !([null,'account-1','account-2'].includes(candidate.active_account)))
            throw new Error('invalid historical quota snapshot');
          const current=candidate.accounts.find(item=>item.account_home===(workerQuotaAccount||candidate.active_account));
          // Valid historical records may be old, but never fabricate fresh data.
          // If the selected account changes, discard the old account display.
          if(current&&(current.ok!==true||
              ![current.primary,current.secondary].some(w=>
                w&&validQuotaPercent(w.remaining_percent)!==null)))
            throw new Error('malformed historical rate windows');
          codexQuotaSnapshot=candidate;
          lastQuotaOkAt=current&&Number.isFinite(current.queried_at)
            ?current.queried_at*1000:null;
          quotaStale=lastQuotaOkAt===null||Date.now()-lastQuotaOkAt>=300000;
          quotaError=false;
          if(candidate.refreshing&&!quotaFollowupPending){{
            // One cheap follow-up to display a completed background update.
            // It never starts extra provider RPCs within the server's 5m TTL.
            quotaFollowupPending=true;
            setTimeout(()=>{{quotaFollowupPending=false;loadCodexQuota();}},25000);
          }}
        }}catch(_error){{quotaError=true;}}
        paintCodexQuota();
        }})().finally(()=>{{quotaPromise=null;}});
        return quotaPromise;
      }}
      let busy=false;
      const eventFilter={{role:'all',category:'all',search:''}};
      // Token details float outside the table and never change row height.
      let usagePopover=null,usageAnchor=null;
      // This native dialog lives OUTSIDE the 8-second refreshed Run panel.
      const budgetDialog=document.getElementById('tm-investigator-budget-dialog');
      let budgetOpener=null;
      function showBudgetReview(button){{
        if(!budgetDialog)return;
        const source=button.closest('[data-tui-pane="investigation"]')
          ?.querySelector('template[data-budget-review-template]');
        if(!source)return;
        const content=budgetDialog.querySelector('[data-budget-modal-content]');
        if(!content)return;
        content.replaceChildren(source.content.cloneNode(true));
        budgetOpener=button;
        if(!budgetDialog.open)budgetDialog.showModal();
        content.querySelector('[data-budget-modal-close]')?.focus();
      }}
      budgetDialog?.addEventListener('close',()=>{{
        if(budgetOpener?.isConnected)budgetOpener.focus();
        budgetOpener=null;
      }});
      budgetDialog?.addEventListener('click',event=>{{
        if(event.target===budgetDialog){{budgetDialog.close();return;}}
        if(event.target.closest('[data-budget-modal-close]')){{
          budgetDialog.close();return;
        }}
        const select=event.target.closest('[data-budget-review-select]');
        if(!select)return;
        const next=select.dataset.budgetReviewSelect;
        budgetDialog.querySelectorAll('[data-budget-review-select]').forEach(button=>{{
          button.setAttribute('aria-pressed',String(button===select));
        }});
        budgetDialog.querySelectorAll('[data-budget-review-panel]').forEach(panel=>{{
          panel.hidden=panel.dataset.budgetReviewPanel!==next;
        }});
      }});
      const judgeDialog=document.getElementById('tm-judge-review-dialog');
      let judgeOpener=null;
      function openJudgeDetails(button){{
        const source=button.closest('[data-tui-pane="judge"]')
          ?.querySelector('template[data-judge-review-template]');
        const content=judgeDialog?.querySelector('[data-judge-modal-content]');
        if(!source||!content)return;
        content.replaceChildren(source.content.cloneNode(true));
        judgeOpener=button;
        if(!judgeDialog.open)judgeDialog.showModal();
        content.querySelector('[data-judge-modal-close]')?.focus();
      }}
      judgeDialog?.addEventListener('close',()=>{{
        if(judgeOpener?.isConnected)judgeOpener.focus();
        judgeOpener=null;
      }});
      judgeDialog?.addEventListener('click',event=>{{
        if(event.target===judgeDialog||event.target.closest('[data-judge-modal-close]')){{
          judgeDialog.close();return;
        }}
        const selected=event.target.closest('[data-judge-review-select]');
        if(!selected)return;
        judgeDialog.querySelectorAll('[data-judge-review-select]').forEach(item=>{{
          item.setAttribute('aria-pressed',String(item===selected));
        }});
        judgeDialog.querySelectorAll('[data-judge-review-panel]').forEach(panel=>{{
          panel.hidden=panel.dataset.judgeReviewPanel!==selected.dataset.judgeReviewSelect;
        }});
      }});
      function closeUsagePopover(restoreFocus=false){{
        const target=usageAnchor;
        if(target?.isConnected)target.setAttribute('aria-expanded','false');
        usagePopover?.remove();
        usagePopover=null;usageAnchor=null;
        if(restoreFocus&&target?.isConnected)target.focus();
      }}
      function positionUsagePopover(){{
        if(!usagePopover||!usageAnchor?.isConnected){{closeUsagePopover();return;}}
        const a=usageAnchor.getBoundingClientRect();
        const width=usagePopover.getBoundingClientRect().width;
        const height=usagePopover.getBoundingClientRect().height;
        const gutter=12;
        let left=a.right+gutter;
        if(left+width+gutter>window.innerWidth)left=a.left-width-gutter;
        if(left<gutter)left=Math.max(gutter,Math.min(a.left,window.innerWidth-width-gutter));
        const top=Math.min(Math.max(gutter,a.top),Math.max(gutter,window.innerHeight-height-gutter));
        usagePopover.style.left=left+'px';
        usagePopover.style.top=top+'px';
      }}
      function toggleUsagePopover(button){{
        if(usageAnchor===button){{closeUsagePopover(true);return;}}
        closeUsagePopover();
        let values;
        try{{values=JSON.parse(button.dataset.tokenDetail);}}catch(_err){{return;}}
        if(!values||typeof values!=='object'||Array.isArray(values))return;
        const panel=document.createElement('section');
        panel.className='tm-usage-popover';
        panel.setAttribute('role','dialog');
        panel.setAttribute('aria-label','Token 用量明细');
        const head=document.createElement('div');
        head.className='tm-usage-popover-head';
        const title=document.createElement('strong');title.textContent='Token 用量明细';
        const close=document.createElement('button');
        close.type='button';close.textContent='关闭';close.setAttribute('aria-label','关闭 Token 明细');
        head.append(title,close);
        const list=document.createElement('dl');
        for(const [name,value] of Object.entries(values)){{
          const dt=document.createElement('dt'),dd=document.createElement('dd');
          dt.textContent=name;dd.textContent=String(value);
          list.append(dt,dd);
        }}
        const note=document.createElement('p');
        note.className='tm-usage-popover-note';
        note.textContent='缓存命中率 = 缓存输入 ÷ 输入 Token；缓存已包含在输入中，推理已包含在输出中，不能重复相加。';
        panel.append(head,list,note);
        document.body.append(panel);
        usagePopover=panel;usageAnchor=button;
        button.setAttribute('aria-expanded','true');
        positionUsagePopover();
        close.addEventListener('click',()=>closeUsagePopover(true));
        close.focus();
      }}
      // Native select choices only show (默认) when their menu is being opened.
      function routeChoiceLabels(field,expanded){{
        const selectedDefault=field.dataset.routeDefaultValue||'';
        for(const option of field.options){{
          if(option.dataset.routePlainLabel===undefined)
            option.dataset.routePlainLabel=option.textContent;
          option.textContent=option.dataset.routePlainLabel+
            (expanded&&selectedDefault&&option.value===selectedDefault?'（默认）':'');
        }}
      }}
      host.addEventListener('pointerdown',event=>{{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field)routeChoiceLabels(field,true);
      }});
      host.addEventListener('keydown',event=>{{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field&&['Enter',' ','ArrowDown','ArrowUp'].includes(event.key))routeChoiceLabels(field,true);
      }});
      // Begin at the most recent event; leave manual scroll positions alone.
      const firstTimeline=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
      if(firstTimeline){{
        const selected=firstTimeline.querySelector('[data-history-selected]');
        if(selected)firstTimeline.scrollTop+=selected.getBoundingClientRect().top-firstTimeline.getBoundingClientRect().top-8;
        else firstTimeline.scrollTop=firstTimeline.scrollHeight;
      }}
      // Historical headers are inserted above the first page; the request is
      // scoped to the task's persisted Cycle catalog and never opens raw paths.
      let olderCycleLoading=false;
      function makeOlderCycleRow(item) {{
        const detail=document.createElement('details');
        detail.className='tm-cycle-group';
        detail.dataset.cycleGroupKey='cycle-'+item.id;
        detail.dataset.cycleHistoryLazy=String(item.id);
        const summary=document.createElement('summary');
        summary.className='tm-cycle-summary';
        summary.title='点击展开本 Cycle 记录';
        const title=document.createElement('span');
        title.className='tm-cycle-label';
        title.textContent='Cycle '+item.id;
        summary.append(title);
        if(item.time) {{
          const clock=document.createElement('time');
          clock.textContent=String(item.time);
          summary.append(clock);
        }}
        if(['supported','rejected','open','unadjudicated'].includes(item.verdict)) {{
          const verdict=document.createElement('span');
          verdict.className='tm-cycle-verdict';
          verdict.title=item.verdict==='unadjudicated'?'本 Cycle 未形成最终科学裁决':'本 Cycle 的最终 Judge 裁决';
          verdict.textContent=item.verdict==='unadjudicated'?'未裁决':'Judge · '+item.verdict;
          summary.append(verdict);
        }}
        const count=document.createElement('span');
        count.className='tm-cycle-count';
        count.textContent='展开';
        const chevron=document.createElement('span');
        chevron.className='tm-cycle-chevron';
        chevron.setAttribute('aria-hidden','true');
        chevron.textContent='⌄';
        summary.append(count,chevron);
        const placeholder=document.createElement('div');
        placeholder.className='tm-cycle-history-placeholder';
        placeholder.dataset.cycleHistoryPlaceholder='';
        placeholder.setAttribute('aria-live','polite');
        placeholder.textContent='展开后自动加载记录';
        detail.append(summary,placeholder);
        return detail;
      }}
      async function loadOlderCycles(timeline) {{
        const cursor=timeline?.dataset.cycleEarliest;
        if(olderCycleLoading||!timeline||!cursor||timeline.dataset.cycleHasMore!=='1')return;
        olderCycleLoading=true;
        const heading=timeline.querySelector('.tm-cycle-history-total');
        try {{
          const url='/api/run/{ident}/cycle-page?before='+encodeURIComponent(cursor);
          const reply=await fetch(url,{{cache:'no-store'}});
          if(!reply.ok)throw new Error('HTTP '+reply.status);
          const page=await reply.json();
          if(!Array.isArray(page.cycles)||typeof page.has_more!=='boolean')
            throw new Error('Invalid Cycle page');
          // Use the current DOM after await: the 8-second poll may have
          // replaced the old history panel while this request was in flight.
          const current=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
          const list=current?.querySelector('.tm-run-cycle-groups');
          if(!current||!list||current.dataset.cycleEarliest!==cursor)return;
          const top=current.scrollTop, height=current.scrollHeight;
          const existing=new Set([...list.querySelectorAll('[data-cycle-group-key]')]
            .map(d=>d.dataset.cycleGroupKey));
          for(const item of page.cycles) {{
            const id=Number(item.id);
            if(!Number.isSafeInteger(id)||id<0||existing.has('cycle-'+id))continue;
            const row=makeOlderCycleRow(item);
            const sibling=[...list.children].find(element=>{{
              const detail=element.matches?.('[data-cycle-group-key]')?element:
                element.querySelector?.('[data-cycle-group-key]');
              return detail&&Number(detail.dataset.cycleGroupKey?.replace('cycle-',''))>id;
            }});
            list.insertBefore(row,sibling||null);
            existing.add('cycle-'+id);
          }}
          if(page.cycles.length)current.dataset.cycleEarliest=String(page.cycles[0].id);
          current.dataset.cycleHasMore=page.has_more?'1':'0';
          const count=existing.size, total=Number(page.total)||count;
          if(heading)heading.textContent='已显示 '+count+' / '+total+' 个 Cycle'+
            (page.has_more?' · 向上滚动到顶部继续加载更早 Cycle':' · 已加载全部历史 Cycle')+
            ' · 点击 Cycle 行展开记录';
          // Insert above, but do not shift the Cycle the reader is viewing.
          current.scrollTop=top+current.scrollHeight-height;
        }}catch(error) {{
          const current=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
          const note=current?.querySelector('.tm-cycle-history-total');
          if(note)note.textContent='加载更早 Cycle 失败；再次向上滚动可重试';
        }}finally{{olderCycleLoading=false;}}
      }}
      host.addEventListener('scroll',event=>{{
        const timeline=event.target;
        if(historyView==='recent'&&timeline?.matches?.(
          '[data-history-panel="recent"] .tm-run-events-scroll[data-cycle-has-more="1"]'
        )&&timeline.scrollTop<=24)loadOlderCycles(timeline);
      }},true);
      let historyView='recent';
      const visitedHistory=new Set(['recent']);
      function applyHistoryView(){{
        host.querySelectorAll('[data-history-view]').forEach(button=>{{
          const selected=button.dataset.historyView===historyView;
          button.setAttribute('aria-selected',String(selected));
          button.tabIndex=selected?0:-1;
        }});
        host.querySelectorAll('[data-history-panel]').forEach(panel=>{{
          panel.hidden=panel.dataset.historyPanel!==historyView;
        }});
      }}
      function chooseHistoryView(next){{
        if(!['recent','important','incidents'].includes(next))return;
        historyView=next;
        applyHistoryView();
        if(!visitedHistory.has(next)){{
          const timeline=host.querySelector('[data-history-panel="'+next+'"] .tm-run-events-scroll');
          if(timeline)timeline.scrollTop=timeline.scrollHeight;
          visitedHistory.add(next);
        }}
      }}
      // Load a historical Cycle into its own disclosure instead of navigating
      // away from the live Task Console. The API renders one trusted snapshot
      // through the same server-side projection used for direct history links.
      host.addEventListener('click',async event=>{{
        const summary=event.target.closest('summary');
        const detail=summary?.parentElement;
        if(!detail?.matches('details[data-cycle-history-lazy]')||
           detail.dataset.cycleHistoryLoaded==='1'||cycleHistoryLoads.has(detail.dataset.cycleHistoryLazy))return;
        if(detail.open)return; // Closing an already-open row must stay local.
        const id=detail.dataset.cycleHistoryLazy;
        const placeholder=detail.querySelector('[data-cycle-history-placeholder]');
        if(!id||!placeholder)return;
        cycleHistoryLoads.add(id);
        detail.dataset.cycleHistoryLoading='1';
        placeholder.textContent='正在加载本 Cycle 记录…';
        try{{
          const query=new URLSearchParams({{tab:view||'activity',history_cycle:id}});
          if(selectedWorker)query.set('worker',selectedWorker);
          const reply=await fetch('/api/run/{ident}/view?'+query.toString(),{{cache:'no-store'}});
          if(!reply.ok)throw new Error('Cycle history request failed: '+reply.status);
          const parsed=new DOMParser().parseFromString(await reply.text(),'text/html');
          const wrapper=[...parsed.querySelectorAll('[data-history-selected]')]
            .find(item=>item.dataset.historySelected===id);
          const source=wrapper?.querySelector('details.tm-cycle-group');
          if(!source)throw new Error('Cycle history was not present in the response');
          const current=[...host.querySelectorAll('details[data-cycle-history-lazy]')]
            .find(item=>item.dataset.cycleHistoryLazy===id);
          if(!current||current.dataset.cycleHistoryLoaded==='1')return;
          const loaded=source.cloneNode(true);
          loaded.dataset.cycleHistoryLoaded='1';
          loaded.open=current.open;
          current.replaceWith(loaded);
        }}catch(error){{
          const current=[...host.querySelectorAll('details[data-cycle-history-lazy]')]
            .find(item=>item.dataset.cycleHistoryLazy===id);
          const currentPlaceholder=current?.querySelector('[data-cycle-history-placeholder]');
          if(currentPlaceholder)currentPlaceholder.textContent='加载失败；收起后再次展开可重试';
          if(current)delete current.dataset.cycleHistoryLoading;
        }}finally{{
          cycleHistoryLoads.delete(id);
        }}
      }});
      host.addEventListener('click',event=>{{
        const tab=event.target.closest('[data-history-view]');
        if(tab)chooseHistoryView(tab.dataset.historyView);
      }});
      host.addEventListener('keydown',event=>{{
        const tab=event.target.closest('[data-history-view]');
        if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();
        const order=['recent','important','incidents'];
        const next=event.key==='Home'?'recent':event.key==='End'?'incidents':
          order[(order.indexOf(tab.dataset.historyView)+(event.key==='ArrowRight'?1:order.length-1))%order.length];
        chooseHistoryView(next);
        host.querySelector('[data-history-view="'+next+'"]')?.focus();
      }});
      host.addEventListener('focusout',event=>{{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field)routeChoiceLabels(field,false);
      }});
      document.addEventListener('pointerdown',event=>{{
        if(usagePopover&&!usagePopover.contains(event.target)&&
            !usageAnchor?.contains(event.target))closeUsagePopover();
      }});
      window.addEventListener('resize',positionUsagePopover);
      window.addEventListener('scroll',positionUsagePopover,true);
      function applyEventFilters(){{
        if(view!=='events')return;
        const pane=host.querySelector('.tm-events-browser');
        if(!pane)return;
        for(const name of ['role','category','search']){{
          const field=pane.querySelector('[data-event-filter="'+name+'"]');
          if(field&&field.value!==eventFilter[name])field.value=eventFilter[name];
        }}
        let count=0;
        pane.querySelectorAll('.tm-event').forEach(card=>{{
          const match=(eventFilter.role==='all'||card.dataset.eventRole===eventFilter.role)
             &&(eventFilter.category==='all'||card.dataset.eventCategory===eventFilter.category)
             &&(!eventFilter.search||card.textContent.toLowerCase().includes(eventFilter.search));
          card.hidden=!match;if(match)count++;
        }});
        const label=pane.querySelector('[data-event-count]');
        if(label)label.textContent='匹配 '+count+' 条 · 仅最近事件；过滤只改变显示，不修改日志';
      }}
      host.addEventListener('input',event=>{{
        if(event.target.matches('[data-event-filter="search"]')){{
          eventFilter.search=event.target.value.toLowerCase().trim();applyEventFilters();
        }}
      }});
      host.addEventListener('change',event=>{{
        const name=event.target.dataset.eventFilter;
        if(name&&name!=='search'){{eventFilter[name]=event.target.value;applyEventFilters();}}
      }});
      function reconcileHeaderPreservingQuota(current,fresh) {{
        // Elapsed changes almost every 8-second Run poll. Updating the whole
        // header via innerHTML would delete the live Codex quota card, create
        // placeholders, restart its progress-bar transition and then repaint.
        // Patch only changed sibling metrics: keep quota DOM connected.
        const oldGrid=current.querySelector(':scope > .tm-tui-kv-grid');
        const nextGrid=fresh.querySelector(':scope > .tm-tui-kv-grid');
        if(!oldGrid||!nextGrid||oldGrid.children.length!==nextGrid.children.length)
          return false; // Unrecognized header layout: use general safe path.
        const oldCards=[...oldGrid.children],newCards=[...nextGrid.children];
        const oldQuota=oldGrid.querySelector('[data-codex-quota]');
        const newQuota=nextGrid.querySelector('[data-codex-quota]');
        if(!oldQuota||!newQuota)return false;
        for(let i=0;i<oldCards.length;i++){{
          const oldIsQuota=oldCards[i]===oldQuota;
          const newIsQuota=newCards[i]===newQuota;
          if(oldIsQuota!==newIsQuota)return false;
        }}
        for(let i=0;i<oldCards.length;i++){{
          if(oldCards[i]===oldQuota)continue;
          if(oldCards[i].outerHTML!==newCards[i].outerHTML)
            oldCards[i].replaceWith(newCards[i]);
        }}
        // Attention/warning content may change independently of the metrics.
        const oldExtras=[...current.children].filter(item=>item!==oldGrid);
        const freshExtras=[...fresh.children].filter(item=>item!==nextGrid);
        for(let i=0;i<Math.max(oldExtras.length,freshExtras.length);i++){{
          if(!oldExtras[i])current.append(freshExtras[i]);
          else if(!freshExtras[i])oldExtras[i].remove();
          else if(oldExtras[i].outerHTML!==freshExtras[i].outerHTML)
            oldExtras[i].replaceWith(freshExtras[i]);
        }}
        return true;
      }}
      async function refresh() {{
        // Continue polling identity and process state while reading a long
        // Stage Conversation. Its inner conversation DOM remains untouched.
        if(!host||document.hidden||busy)return;
        busy=true;
        try {{
          const reply=await fetch('/api/run/{ident}/view?tab='+encodeURIComponent(view||'activity')+(selectedWorker?'&worker='+encodeURIComponent(selectedWorker):'')+(selectedHistoryCycle?'&history_cycle='+encodeURIComponent(selectedHistoryCycle):''),{{cache:'no-store'}});
          if(!reply.ok){{runFreshness('读取失败 · 保留 '+lastRunSync+' 的快照');return;}}
          lastRunSync=new Date().toLocaleTimeString();
          runFreshness('最近获取 '+lastRunSync+' · 8 秒自动检查');
          const parsed=new DOMParser().parseFromString(await reply.text(),'text/html');
          const incoming=parsed.querySelector('.tm-tui-screen');
          const present=host.querySelector('.tm-tui-screen');
          if(!incoming)return;
          // A selected Worker may be dismissed in another tab. Do not reuse
          // its stale control surface for a newly selected Worker.
          if(present && present.dataset.tuiWorker!==incoming.dataset.tuiWorker) {{
            window.location.reload(); return;
          }}
          // Master/Worker controls outside the live pane must follow the
          // freshest Cycle and State. Never leave a stale Pause after OVER.
          if(present&&(document.getElementById('tm-operator')||selectedWorker) &&
             !window.tmStartProgress?.shouldHoldRefresh() &&
             (present.dataset.tuiStatus!==incoming.dataset.tuiStatus ||
              present.dataset.tuiCycle!==incoming.dataset.tuiCycle)) {{
            window.location.reload();
            return;
          }}
          if(present) {{
            const oldTabs=present.querySelector('.tm-worker-tabs');
            const newTabs=incoming.querySelector('.tm-worker-tabs');
            // Keep the status of *other* Agents current, even when the selected
            // Worker's own State/Cycle is unchanged. Preserve pending click feedback.
            if(oldTabs&&newTabs&&!oldTabs.querySelector('button:disabled')
               &&oldTabs.querySelectorAll('[data-worker-feedback]:not([hidden])').length===0
               &&oldTabs.innerHTML!==newTabs.innerHTML)oldTabs.replaceWith(newTabs);
          }}
          if(!present||present.dataset.tuiScreen!==incoming.dataset.tuiScreen) {{
            closeUsagePopover();
            host.replaceChildren(incoming);
            paintCodexQuota();
            if(view==='events')window.tmStageConversationMount?.(incoming);
            return;
          }}
          const oldPanes=present.querySelectorAll('[data-tui-pane]');
          const newPanes=incoming.querySelectorAll('[data-tui-pane]');
          if(oldPanes.length!==newPanes.length && view!=='events'){{
            closeUsagePopover();
            host.replaceChildren(incoming);
            paintCodexQuota();
            return;
          }}
          // Never replace an active Stage reader, even if its server layout
          // differs; continue updating Task/Cycle identity and status only.
          for(let n=0;n<Math.min(oldPanes.length,newPanes.length);n++){{
            if(oldPanes[n].dataset.tuiPane==='header' &&
                reconcileHeaderPreservingQuota(oldPanes[n],newPanes[n]))continue;
            if(oldPanes[n].dataset.tuiPane==='workflow'&&oldPanes[n].querySelector('[data-route-dirty="1"]'))continue;
            // Keep a switch or model draft mounted until the API request settles.
            if(oldPanes[n].dataset.tuiPane==='quota-settings' &&
              (oldPanes[n].querySelector('[data-setting-saving="1"]') ||
               (document.activeElement && oldPanes[n].contains(document.activeElement) &&
                document.activeElement.matches('input,select,[data-setting-save-model]'))))continue;
            if(view==='events'&&oldPanes[n].dataset.tuiPane==='secondary')continue;
            // Never destroy a partially typed Cycle limit during live polling.
            if(oldPanes[n].dataset.tuiPane==='header'
               &&oldPanes[n].querySelector('[data-cycle-max-form]:not([hidden])'))continue;
            // Preserve prepended history when the 8-second live poll sends
            // only its default ten Cycle headers. Merge before DOM comparison
            // so polling cannot silently discard the user's older pages.
            const oldRecent=oldPanes[n].querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
            const newRecent=newPanes[n].querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
            if(oldRecent&&newRecent&&oldRecent.dataset.cycleEarliest) {{
              const newList=newRecent.querySelector('.tm-run-cycle-groups');
              const oldList=oldRecent.querySelector('.tm-run-cycle-groups');
              if(oldList&&newList) {{
                const keys=new Set([...newList.querySelectorAll('[data-cycle-group-key]')]
                  .map(row=>row.dataset.cycleGroupKey));
                for(const original of oldList.children) {{
                  const detail=original.matches?.('[data-cycle-group-key]')?original:
                    original.querySelector?.('[data-cycle-group-key]');
                  const key=detail?.dataset.cycleGroupKey;
                  if(!key||keys.has(key))continue;
                  const id=Number(key.replace('cycle-',''));
                  const next=[...newList.children].find(item=>{{
                    const d=item.matches?.('[data-cycle-group-key]')?item:
                      item.querySelector?.('[data-cycle-group-key]');
                    return d&&Number(d.dataset.cycleGroupKey?.replace('cycle-',''))>id;
                  }});
                  newList.insertBefore(original.cloneNode(true),next||null);
                  keys.add(key);
                }}
                newRecent.dataset.cycleEarliest=oldRecent.dataset.cycleEarliest;
                newRecent.dataset.cycleHasMore=oldRecent.dataset.cycleHasMore;
                const total=Number(newRecent.dataset.cycleTotal)||keys.size;
                const heading=newRecent.querySelector('.tm-cycle-history-total');
                if(heading)heading.textContent='已显示 '+keys.size+' / '+total+' 个 Cycle'+
                  (newRecent.dataset.cycleHasMore==='1'?' · 向上滚动到顶部继续加载更早 Cycle':
                  ' · 已加载全部历史 Cycle')+' · 点击 Cycle 行展开记录';
              }}
            }}
            if(oldPanes[n].innerHTML!==newPanes[n].innerHTML){{
              const previousCycleOpen=new Map([...oldPanes[n].querySelectorAll('details[data-cycle-group-key]')]
                .map(detail=>[detail.dataset.cycleGroupKey,detail.open]));
              const loadedCycleHtml=new Map([...oldPanes[n].querySelectorAll('details[data-cycle-group-key][data-cycle-history-loaded="1"]')]
                .map(detail=>[detail.dataset.cycleGroupKey,detail.innerHTML]));
              const openDetails=new Set([...oldPanes[n].querySelectorAll('details:not([data-cycle-group-key])')]
                .flatMap((detail,index)=>detail.open?[index]:[]));
              const historyScroll=new Map([...oldPanes[n].querySelectorAll('[data-history-panel]')]
                .map(panel=>{{
                  const timeline=panel.querySelector('.tm-run-events-scroll');
                  return [panel.dataset.historyPanel, timeline?{{
                    top:timeline.scrollTop,
                    nearLatest:!panel.hidden &&
                      timeline.scrollHeight-timeline.clientHeight-timeline.scrollTop<28
                  }}:null];
                }}));
              const expandedImportant=new Set([...oldPanes[n].querySelectorAll('details[data-important-key][open]')]
                .map(detail=>detail.dataset.importantKey));
              closeUsagePopover();
              oldPanes[n].innerHTML=newPanes[n].innerHTML;
              if(oldPanes[n].dataset.tuiPane==='quota-settings'){{
                showSettingDetail(oldPanes[n],oldPanes[n].dataset.activeSetting||'');
              }}
              applyHistoryView();
              [...oldPanes[n].querySelectorAll('details:not([data-cycle-group-key]):not([data-important-key])')].forEach((detail,index)=>{{
                if(openDetails.has(index))detail.open=true;
              }});
              oldPanes[n].querySelectorAll('details[data-important-key]').forEach(detail=>{{
                if(expandedImportant.has(detail.dataset.importantKey))detail.open=true;
              }});
              oldPanes[n].querySelectorAll('details[data-cycle-group-key]').forEach(detail=>{{
                const key=detail.dataset.cycleGroupKey;
                if(loadedCycleHtml.has(key)){{
                  detail.innerHTML=loadedCycleHtml.get(key);
                  detail.dataset.cycleHistoryLoaded='1';
                }}
                if(previousCycleOpen.has(key))detail.open=previousCycleOpen.get(key);
              }});
              oldPanes[n].querySelectorAll('[data-history-panel]').forEach(panel=>{{
                const timeline=panel.querySelector('.tm-run-events-scroll');
                const previous=historyScroll.get(panel.dataset.historyPanel);
                if(timeline&&previous)timeline.scrollTop=previous.nearLatest
                  ?timeline.scrollHeight:previous.top;
              }});
            }}
          }}
          // The Task/Cycle identity sits in the title bar, outside all
          // data-tui-pane nodes. Refresh it explicitly on every successful
          // state poll; do not rely on replacing the science transcript.
          const oldIdentity=present.querySelector('.tm-tui-identity');
          const newIdentity=incoming.querySelector('.tm-tui-identity');
          if(oldIdentity&&newIdentity&&oldIdentity.textContent!==newIdentity.textContent)
            oldIdentity.textContent=newIdentity.textContent;
          applyEventFilters();
          paintCodexQuota();
          present.dataset.tuiCycle=incoming.dataset.tuiCycle;
          present.dataset.tuiStatus=incoming.dataset.tuiStatus;
          if(view==='events')
            window.tmStageConversationNotifyCycle?.(incoming.dataset.tuiCycle);
        }} catch (_err) {{ runFreshness('网络异常 · 保留 '+lastRunSync+' 的快照'); }}
        finally {{busy=false;}}
      }}
      function select(viewName){{
        // Every secondary page is a separate focused reader. Full
        // navigation both removes home-only controls on entry and restores
        // them on return, without retaining stale Stage JS or layout.
        const params=new URLSearchParams();
        if(selectedWorker)params.set('worker',selectedWorker);
        if(viewName)params.set('tab',viewName);
        const query=params.toString();
        window.location.assign(location.pathname+(query?'?'+query:''));
      }}
      function openCycleEditor(button){{
        const tile=button.closest('.tm-tui-kv');
        const form=tile?.querySelector('[data-cycle-max-form]');
        if(!form)return;
        form.hidden=false;
        button.setAttribute('aria-expanded','true');
        const input=form.querySelector('[data-cycle-max-input]');
        if(input){{input.value=tile.querySelector('[data-cycle-max-current]')?.textContent||input.value;input.focus();input.select();}}
      }}
      function closeCycleEditor(tile){{
        const form=tile?.querySelector('[data-cycle-max-form]');
        if(!form)return;
        form.hidden=true;
        tile.querySelector('[data-tui-op="max_cycles"]')?.setAttribute('aria-expanded','false');
        tile.querySelector('[data-tui-op="max_cycles"]')?.focus();
      }}
      const supportsHotModelRoute={json.dumps(bool((run_monitor.data(run).get("sdk_runtime_capabilities") or {}).get("hot_role_route_v1")))};
      const routeSaveDialog=document.getElementById('tm-route-save-dialog');
      function chooseRouteActivation(role,model,effort,prior){{
        if(!routeSaveDialog)return Promise.resolve(null);
        routeSaveDialog.returnValue='';
        const description=routeSaveDialog.querySelector('[data-route-save-description]');
        description.textContent=role+' · '+model+' · '+effort+'；请选择何时应用新的模型配置。';
        for(const input of routeSaveDialog.querySelectorAll('input[name="tm-route-activation"]'))
          input.checked=input.value===(prior==='next_cycle'?'next_cycle':'next_invocation');
        return new Promise(resolve=>{{
          routeSaveDialog.addEventListener('close',()=>{{
            const selected=routeSaveDialog.querySelector('input[name="tm-route-activation"]:checked');
            resolve(routeSaveDialog.returnValue==='save'?selected?.value||null:null);
          }},{{once:true}});
          routeSaveDialog.showModal();
        }});
      }}
      async function submitAgentModel(field,editTimingOnly=false){{
        const row=field.closest('tr[data-agent-role]');
        if(!row)return;
        const modelSelect=row.querySelector('[data-route-model]');
        const effortSelect=row.querySelector('[data-route-effort]');
        const modelChoice=modelSelect.value,effort=effortSelect.value;
        const restore=()=>{{modelSelect.value=row.dataset.savedModel;effortSelect.value=row.dataset.savedEffort;}};
        const at=modelChoice.lastIndexOf('@');
        if(at<1||!['low','medium','high','xhigh'].includes(effort)){{restore();return;}}
        if(!editTimingOnly&&modelChoice===row.dataset.savedModel&&effort===row.dataset.savedEffort)return;
        const role=row.dataset.agentRole;
        const model=modelChoice.slice(0,at),provider=modelChoice.slice(at+1);
        const activation=await chooseRouteActivation(role,model,effort,
          selectedWorker&&!editTimingOnly?'next_invocation':row.dataset.savedActivation);
        if(!activation){{restore();return;}}
        if(editTimingOnly&&activation===row.dataset.savedActivation)return;
        const box=document.getElementById('tm-operator')||document.querySelector?.('.tm-worker-controls');
        if(!box){{restore();return;}}
        row.dataset.routeDirty='1';
        modelSelect.disabled=true;
        effortSelect.disabled=true;
        try{{
          const response=await fetch('/api/run/{ident}/action',{{method:'POST',
            headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action:selectedWorker?'set_worker_route':'set_role_route',
              value:selectedWorker?{{worker_id:selectedWorker,role,model,provider,reasoning_effort:effort,activation}}:
                  {{role,model,provider,reasoning_effort:effort,activation}},
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:box.dataset.cycle,expected_status:box.dataset.status,
              expected_launch_id:selectedWorker?'':box.dataset.launch}})}});
          const reply=await response.json();
          if(!response.ok||!reply.ok)throw new Error(reply.error||'模型配置保存失败');
          row.dataset.savedModel=modelChoice;
          row.dataset.savedEffort=effort;
          row.dataset.savedActivation=activation;
          row.dataset.routeDirty='0';
          await refresh();
        }}catch(error){{
          restore();
          row.dataset.routeDirty='0';
          window.alert('无法保存模型配置：'+String(error.message||error)+'。请刷新后重试。');
        }}finally{{
          modelSelect.disabled=false;
          effortSelect.disabled=false;
        }}
      }}
      function showSettingDetail(pane,key){{
        const details=pane?.querySelector('#tm-setting-detail-box');
        if(!details)return;
        const item=key?[...details.querySelectorAll('[data-setting-detail]')]
          .find(el=>el.dataset.settingDetail===key):null;
        const selected=item?key:'';
        pane.dataset.activeSetting=selected;
        details.hidden=!selected;
        details.querySelectorAll('[data-setting-detail]').forEach(el=>{{
          el.hidden=el.dataset.settingDetail!==selected;
        }});
        pane.querySelectorAll('[data-setting-open]').forEach(button=>{{
          button.setAttribute('aria-expanded',
            String(button.dataset.settingOpen===selected));
        }});
      }}
      async function commitSetting(action,value,input,prior){{
        const box=document.getElementById('tm-operator');
        const settings=document.getElementById('tm-web-settings');
        const info=settings?.querySelector('.tm-setting-feedback');
        if(!box||!settings)return;
        settings.dataset.settingSaving='1';
        if(input)input.disabled=true;
        if(info)info.textContent='正在保存设置…';
        try{{
          const response=await fetch('/api/run/{ident}/action',{{method:'POST',
            headers:{{'Content-Type':'application/json','X-TM-Operator-Intent':'1'}},
            body:JSON.stringify({{action,value,request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:box.dataset.cycle,expected_status:box.dataset.status,
              expected_launch_id:box.dataset.launch}})}});
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error(data.error||'写入失败');
          const key=input?.dataset?.settingKey;
          const caption=input?.closest('.tm-setting-compact')?.querySelector('small');
          if(caption){{
            caption.textContent=input.dataset.settingMode==='flag'
              ? '已配置'+(input.checked?'开启':'关闭')+' · 下次 Run/Resume'
              : (input.checked?'开启':'关闭')+' · 当前任务';
          }}
          if(key==='cycle_audit'){{
            const menuStatus=settings.querySelector('.tm-setting-gear-status');
            if(menuStatus)menuStatus.textContent='已配置'+(input.checked?'开启':'关闭')+' · 下次 Run/Resume';
          }}
          if(info)info.textContent='已保存至当前任务。部分配置在下次运行或审查时生效。';
        }}catch(error){{
          if(input&&prior!==undefined)input.checked=prior;
          if(info)info.textContent='保存失败：'+String(error.message||error)+'。请刷新后再试。';
        }}finally{{
          if(input)input.disabled=false;
          delete settings.dataset.settingSaving;
        }}
      }}
      // The quota-settings pane is re-rendered every eight seconds; listener
      // ownership belongs to the stable document, never a disposable input.
      document.addEventListener('change',event=>{{
        const input=event.target.closest?.('[data-tui-pane="quota-settings"] [data-setting-key]');
        if(!input)return;
        const key=input.dataset.settingKey,selected=input.checked;
        const action=input.dataset.settingMode==='hot'?key:'set_operator_preference';
        const value=input.dataset.settingMode==='hot'?selected:{{kind:'flag',key,value:selected}};
        commitSetting(action,value,input,!selected);
      }});
      document.addEventListener('click',event=>{{
        const card=event.target.closest?.('[data-tui-pane="quota-settings"] [data-setting-card]');
        if(!card)return;
        // Only the physical switch label may change settings; all other
        // card surfaces merely select or collapse this card's detail.
        if(event.target.closest('.tm-setting-switch-control'))return;
        const pane=card.closest('[data-tui-pane="quota-settings"]');
        const key=card.dataset.settingCard;
        showSettingDetail(pane,pane.dataset.activeSetting===key?'':key);
      }});
      document.addEventListener('keydown',event=>{{
        const help=event.target.closest?.('[data-tui-pane="quota-settings"] .tm-setting-help');
        if(help && (event.key==='Enter'||event.key===' ')){{
          event.preventDefault();
          help.click();
        }}
      }});
      document.addEventListener('click',event=>{{
        const button=event.target.closest?.('#tm-web-settings [data-setting-save-model]');
        if(!button)return;
        const settings=document.getElementById('tm-web-settings');
        const key=button.dataset.settingSaveModel;
        const value=settings?.querySelector('[data-setting-model="'+key+'"]')?.value;
        if(value===undefined)return;
        commitSetting('set_operator_preference',{{kind:'model',key,value}},button);
      }});
      host.addEventListener('click',event=>{{
        const button=event.target.closest('[data-route-activation-edit]');
        if(button){{
          const field=button.closest('tr[data-agent-role]')?.querySelector('[data-route-model]');
          if(field)submitAgentModel(field,true);
        }}
      }});
      host.addEventListener('change',event=>{{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field){{routeChoiceLabels(field,false);submitAgentModel(field);}}
      }});
      document.addEventListener('click', event=>{{
        const workerOpener=event.target.closest('[data-worker-state-open]');
        if(workerOpener){{
          const dialog=host.querySelector('#tm-worker-state-dialog');
          if(dialog&&!dialog.open)dialog.showModal();
          return;
        }}
        const opener=event.target.closest('[data-tui-process-actions]');
        const budgetButton=event.target.closest('[data-budget-review-open]');
        if(budgetButton){{showBudgetReview(budgetButton);return;}}
        const judgeButton=event.target.closest('[data-judge-review-open]');
        if(judgeButton){{openJudgeDetails(judgeButton);return;}}
        if(opener){{
          if(selectedWorker){{
            const state=host.querySelector('[data-worker-id]')?.dataset.status?.toUpperCase();
            const operation=['OVER','RELEASED','CANCELLED','STOPPED','FAILED_START'].includes(state)?'run_next':
              ['PAUSED','FAILED','INTERRUPTED','NEEDS_ATTENTION'].includes(state)?'resume':
              state==='RUNNING'?'pause':null;
            const button=operation?document.querySelector('[data-worker-action="'+operation+'"]'):null;
            if(button)button.click();
            else alert('当前 Worker 没有可执行的操作，请检查状态。');
            return;
          }}
          const dialog=document.getElementById('tm-operator-quick-dialog');
          if(dialog&&!dialog.open)dialog.showModal();
          return;
        }}
        const quick=event.target.closest('[data-tui-quick-action]');
        if(quick){{
          const menu=document.getElementById('tm-operator-quick-dialog');
          if(menu?.open)menu.close();
          const action=quick.dataset.tuiQuickAction;
          const existing=document.getElementById('tm-operator')?.querySelector(
            '[data-operator-action="'+action+'"]');
          // The original control owns confirmation, validation, and request
          // submission. Do not create a parallel operator API entrypoint.
          if(existing&&!existing.disabled)existing.click();
          else window.alert('当前 Cycle 不接受此控制请求；请刷新运行状态。');
          return;
        }}
        const link=event.target.closest('[data-tui-view]');
        const tokenButton=event.target.closest('[data-token-detail]');
        if(tokenButton){{toggleUsagePopover(tokenButton);return;}}
        if(link){{select(link.dataset.tuiView);return;}}
        if(event.target.closest('[data-tui-copy]')){{
          const value=host.querySelector('[data-tui-pane="secondary"]')?.textContent;
          if(value&&navigator.clipboard?.writeText)navigator.clipboard.writeText(value);
          return;
        }}
        const cancel=event.target.closest('[data-cycle-max-cancel]');
        if(cancel){{closeCycleEditor(cancel.closest('.tm-tui-kv'));return;}}
        const op=event.target.closest('[data-tui-op="max_cycles"]');
        if(op){{openCycleEditor(op);return;}}
      }});
      document.addEventListener('submit',event=>{{
        const form=event.target.closest('[data-cycle-max-form]');
        if(!form)return;
        event.preventDefault();
        const input=form.querySelector('[data-cycle-max-input]');
        const value=Number(input?.value);
        const info=form.querySelector('[data-cycle-max-message]');
        if(!Number.isSafeInteger(value)||value<1||value>1000000){{info.textContent='请输入 1–1000000 的整数';return;}}
        const tile=form.closest('.tm-tui-kv');
        const save=form.querySelector('[data-cycle-max-save]');
        save.disabled=true;
        info.textContent='正在保存…';
        document.dispatchEvent(new CustomEvent('tm-cycle-max-save',{{detail:{{
          value,complete(okay,message){{
            save.disabled=false;
            if(okay){{
              tile.querySelector('[data-cycle-max-current]').textContent=String(value);
              closeCycleEditor(tile);
            }}else info.textContent=message||'保存失败，请刷新后重试';
          }}
        }}}}));
      }});
      document.addEventListener('keydown', event=>{{
        if(event.key==='Escape'&&usagePopover){{
          event.preventDefault();closeUsagePopover(true);return;
        }}
        if(event.altKey||event.ctrlKey||event.metaKey||event.repeat)return;
        const el=event.target;
        if(el.closest('input, textarea, select, [contenteditable="true"]'))return;
        const key=event.key.toLowerCase();
        if(key==='escape'&&view){{event.preventDefault();select('');return;}}

        if(key==='q'){{event.preventDefault();location.assign('/run/');return;}}
        if(key==='o'&&view&&view!=='events'){{event.preventDefault();const value=host.querySelector('[data-tui-pane="secondary"]')?.textContent;
          if(value&&navigator.clipboard?.writeText)navigator.clipboard.writeText(value);return;}}
      }});
      paintCodexQuota(); // do not clear server-rendered last-good history
      loadCodexQuota();
      setInterval(loadCodexQuota,300000);
      setInterval(refresh,8000);
    }})();</script>'''
