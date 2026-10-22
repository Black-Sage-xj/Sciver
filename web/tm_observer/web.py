from __future__ import annotations

import hashlib
import html
import json
import sys
from datetime import datetime, timezone
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from .archify_views import StaticViewManager, default_archify_root, default_state_dir
from .auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthRealm,
    authorize_header,
    issue_session_token,
    load_auth_file,
    match_account,
    session_from_cookie,
)
from .collector import SCHEMA, collect_git_info, collect_state
from .codex_usage import AccountSwitchConflict, collect_codex_usage, consume_reset_credit, switch_active_account
from .quota_snapshot_store import QuotaHistory
from .change_requests import create_request, load_change_requests
from .current_flow import current_flow_body, flow_node_ids
from .field_notes import save_override, sync_machine_defaults
from .sequence_views import SequenceBuildResult, build_run_sequence
from .stage_flow import field_note_catalog, field_note_keys, stage_workflow_body, stage_workflow_guide_body
from .agent_workflow_architecture import agent_workflow_architecture_body
from .run_views import run_index_body, run_detail_body
from .task_settings_ui import workspace_settings_body
from .goal_drafts import GoalDraftError, view_goal, save_goal_draft
from .research_plan_editor import ResearchPlanError, read_plan, save_section, history_plan
from .research_plan_ui import page_html as research_plan_html
from .run_control import execute as execute_run_action, OperatorError
from .services_worker_accounts import section_html as worker_account_section_html, snapshot as services_workers_snapshot
from .canvas_md import ExplorerError, vault_root
from .canvas_md_routes import canvas_md_get
from .research_explorer_ui import explorer_html
from .research_explorer_layout_demo import explorer_layout_demo_html
from .research_explorer_layout_demo_a0 import explorer_layout_a0_demo_html
from .research_explorer_layout_demo_v0 import explorer_layout_v0_demo_html
from .research_explorer_layout_b import explorer_layout_b_html
from .research_explorer_layout_c import explorer_layout_c_html
from .research_chat import ResearchChatError, snapshot as research_chat_snapshot
from .navigation_model import task_console_url


from .dashboard_ui import STYLE, THEME_BOOTSTRAP_SCRIPT, dashboard_shell


def _page(title: str, body: str, *, active: str = "workflow",
          back_href: str | None = None, breadcrumb_leaf: str | None = None) -> str:
    return dashboard_shell(title, body, active=active, back_href=back_href,
                           breadcrumb_leaf=breadcrumb_leaf)


def _fmt(value: object) -> str:
    if value is None or value == "":
        return '<span class="muted">unknown</span>'
    return html.escape(str(value))


def _run_link(run: dict) -> str:
    ident = run.get("observer_id") or run.get("run_id") or run.get("cycle") or run.get("task_name")
    return f"/runs/{quote(str(ident), safe='')}/"


def overview_html(state: dict) -> str:
    rows = []
    for run in state.get("runs", []):
        warnings = "<br>".join(
            f'<span class="warning">⚠ {_fmt(item.get("code"))}: {_fmt(item.get("message"))}</span>'
            for item in run.get("warnings", [])
        )
        warnings_cell = warnings or '<span class="muted">none</span>'
        rows.append(
            "<tr>"
            f'<td><a href="{_run_link(run)}">{_fmt(run.get("run_id"))}</a></td>'
            f"<td>{_fmt(run.get('task_name'))}</td>"
            f"<td>{_fmt(run.get('cycle'))}</td>"
            f'<td><span class="badge">{_fmt(run.get("state"))}</span></td>'
            f"<td>{_fmt(run.get('phase'))}</td>"
            f"<td>{_fmt(run.get('active_agent'))}</td>"
            f"<td>{_fmt(run.get('pid'))} / {_fmt(run.get('pid_alive'))}</td>"
            f"<td>{_fmt(run.get('last_activity_at'))}</td>"
            f"<td>{_fmt(run.get('tokens_total'))}</td>"
            f"<td>{_fmt(run.get('current_round'))}</td>"
            f"<td>{warnings_cell}</td>"
            "</tr>"
        )
    system = state.get("system", {})
    body = f"""
<h1>Sciver Observatory</h1>
<p class="small">Runtime/scientific projection is read-only. Formal engineering change requests are managed separately and cannot start/stop/resume Sciver.</p>
<div class="grid">
  <div class="card"><strong>Git</strong><br><code>{_fmt(system.get('git_head'))}</code><br>{_fmt(system.get('git_branch'))}</div>
  <div class="card"><strong>Observer</strong><br>v{_fmt(system.get('observer_version'))}<br>{_fmt(state.get('generated_at'))}</div>
  <div class="card"><strong>Tasks</strong><br>{_fmt(system.get('task_count'))}<br>worktree dirty: {_fmt(system.get('git_dirty'))}</div>
</div>
<h2>Runs</h2>
<table>
<thead><tr><th>Run ID</th><th>Task</th><th>Cycle</th><th>State</th><th>Phase</th><th>Agent</th><th>PID/alive</th><th>Last activity</th><th>Tokens</th><th>Round</th><th>Warnings</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<script>
setTimeout(() => location.reload(), 5000);
</script>
"""
    return _page("Sciver Observatory", body)


def placeholder_html(title: str, phase: str, description: str) -> str:
    return _page(
        f"Sciver Observatory — {title}",
        f"<h1>{html.escape(title)}</h1><div class='card'><strong>{html.escape(phase)}</strong><p>{html.escape(description)}</p></div>",
    )


def login_html(error: str | None = None) -> str:
    message = ""
    if error:
        message = f"<div class='card warning'>{html.escape(error)}</div>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
{THEME_BOOTSTRAP_SCRIPT}<title>Sciver Observatory Login</title>{STYLE}</head>
<body>
<h1>Sciver Observatory</h1>
<p class="small">Runtime/scientific observability plus formal engineering change requests. Sign in to continue.</p>
{message}
<form method="post" action="/login" class="card" style="max-width:420px">
  <label>Username<br><input name="username" autocomplete="username" required style="width:100%;box-sizing:border-box;padding:10px;margin:6px 0 14px"></label>
  <label>Password<br><input name="password" type="password" autocomplete="current-password" required style="width:100%;box-sizing:border-box;padding:10px;margin:6px 0 14px"></label>
  <button type="submit" style="padding:10px 18px">Sign in</button>
</form>
</body></html>"""


def _static_warning(manager: StaticViewManager | None) -> str:
    if manager is None or manager.last_result is None or manager.last_result.ok:
        return ""
    return (
        "<div class='card warning'><strong>Static view unavailable</strong><br>"
        + _fmt(manager.last_result.warning)
        + "</div>"
    )


def architecture_html(manager: StaticViewManager | None) -> str:
    # The public Architecture page is a curated, source-backed Agent workflow map.
    # It deliberately does not depend on the third-party Archify renderer.
    body = agent_workflow_architecture_body()
    return _page("Sciver Observatory — Agent Workflow Architecture", body, active="architecture")


def workflow_html(manager: StaticViewManager | None) -> str:
    workflow = manager is not None and (manager.state_dir / "static" / "workflow.html").is_file()
    body = "<h1>Scientific Workflow</h1>"
    body += "<p class='small'>Per-Worker scientific workflow: each independent Worker repeats these six Agent lanes. The SDK enforces Master-level gates; historical Theorist/Numerical roles are omitted.</p>"
    body += _static_warning(manager)
    if workflow:
        body += "<iframe class='diagram' src='/artifacts/workflow.html' title='Sciver Scientific Workflow'></iframe>"
    else:
        body += "<div class='card'>Archify Scientific Workflow artifact has not been built yet.</div>"
    return _page("Sciver Observatory — Scientific Workflow", body)


def _tm_system_requests(requests: tuple[dict, ...]) -> tuple[dict, ...]:
    return tuple(item for item in requests if item.get("record_scope", "tm_system") == "tm_system")


def current_flow_html(selected_request_id: str | None = None, view: str = "current") -> str:
    loaded = load_change_requests(allowed_flow_nodes=flow_node_ids())
    requests = _tm_system_requests(loaded.requests)
    warning = "".join(f"<div class='card warning'>{_fmt(item)}</div>" for item in loaded.warnings)
    return _page("Sciver Observatory — Workflow", warning + current_flow_body(requests, selected_request_id, view=view))


def _synced_field_notes(state_dir: Path) -> dict[str, dict]:
    defaults = {item["key"]: item["default_description"] for item in field_note_catalog()}
    return sync_machine_defaults(state_dir, defaults)


def stage_workflow_html(
    selected_request_id: str | None = None,
    *,
    field_notes_state_dir: Path | None = None,
    workflow_version: str | None = None,
    display_mode: str = "overview",
) -> str:
    loaded = load_change_requests(allowed_flow_nodes=flow_node_ids())
    requests = _tm_system_requests(loaded.requests)
    overrides = _synced_field_notes(field_notes_state_dir) if field_notes_state_dir is not None else {}
    body = stage_workflow_body(
        requests,
        selected_request_id,
        field_note_overrides=overrides,
        workflow_version=workflow_version,
        display_mode=display_mode,
    )
    # Historical TMCR manifests are not part of active scientific routing.
    # Keep diagnostics accessible, but never allow them to occupy the Workflow
    # landing screen ahead of the diagram.
    if loaded.warnings:
        items = "".join(f"<li>{_fmt(item)}</li>" for item in loaded.warnings)
        body += (
            f'<details class="tm-workflow-diagnostics"><summary>历史提请校验提示（{len(loaded.warnings)}）</summary>'
            f'<p>旧 TMCR 元数据校验结果；不影响当前 Workflow 流程图。</p><ul>{items}</ul></details>'
        )
    is_secondary = bool(workflow_version or selected_request_id or display_mode not in {"", "overview"})
    return _page("Sciver Observatory — Workflow", body,
                 back_href="/workflow-stage-preview/" if is_secondary else None,
                 breadcrumb_leaf=("Workflow Details" if is_secondary else None))


def stage_workflow_guide_html() -> str:
    return _page("Sciver Observatory — Workflow Stage Preview Guide", stage_workflow_guide_body(),
                 back_href="/workflow-stage-preview/", breadcrumb_leaf="Guide")


def _csv_or_lines(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def change_requests_html(error: str | None = None) -> str:
    loaded = load_change_requests(allowed_flow_nodes=flow_node_ids())
    warnings = list(loaded.warnings)
    if error:
        warnings.insert(0, error)
    warning_html = "".join(f"<div class='card warning'>{_fmt(item)}</div>" for item in warnings)
    requests = _tm_system_requests(loaded.requests)
    rows = "".join(
        "<tr>"
        f"<td><a href='/workflow-stage-preview/?request={quote(str(item.get('request_id')), safe='')}'><code>{_fmt(item.get('request_id'))}</code></a></td>"
        f"<td>{_fmt(item.get('priority'))}</td><td>{_fmt(item.get('status'))}</td>"
        f"<td>{_fmt(item.get('title'))}</td>"
        f"<td>{'<br>'.join(html.escape(x) for x in item.get('affected_flow_nodes', [])) or '<span class=muted>none</span>'}</td>"
        "</tr>"
        for item in requests
    )
    nodes = " ".join(flow_node_ids())
    body = f"""
<h1>TM 系统修改提请</h1>
<p class='small'>正式的 SDK-first 系统修改提请。创建提请只会写入工程 manifest，不会修改科学状态，也不会自动开始实现。</p>
{warning_html}
<h2>已有提请</h2>
<table><thead><tr><th>ID</th><th>优先级</th><th>状态</th><th>标题</th><th>流程节点</th></tr></thead><tbody>{rows or '<tr><td colspan=5>暂无提请</td></tr>'}</tbody></table>
<h2>新建正式提请</h2>
<p class='small'>必须先更新 SDK 设计。可用的稳定 Current Flow ID：<br><code>{html.escape(nodes)}</code></p>
<form method='post' action='/change-requests/create' class='card'>
<div class='grid'>
<label>标题<br><input name='title' required style='width:100%'></label>
<label>优先级<br><input name='priority' value='P1' required style='width:100%'></label>
<label>改动类型<br><input name='change_types' placeholder='prompt, scale, routing' required style='width:100%'></label>
<label>受影响流程节点<br><input name='affected_flow_nodes' placeholder='CHALLENGER, INV_CTX' style='width:100%'></label>
</div>
<label>摘要<br><textarea name='summary' required style='width:100%;min-height:60px'></textarea></label>
<label>SDK 设计引用（每行一个）<br><textarea name='sdk_design_refs' required style='width:100%;min-height:55px'>tool/theory_master/design/SDK设计文档.md#</textarea></label>
<div class='grid'><label>Agent / SDK 归属<br><input name='owner' required style='width:100%'></label><label>具体环节<br><input name='stage' required style='width:100%'></label></div>
<label>问题<br><textarea name='issue' required style='width:100%;min-height:60px'></textarea></label>
<label>原设计动机<br><textarea name='design_motivation' required style='width:100%;min-height:60px'></textarea></label>
<label>当前失败机制<br><textarea name='current_failure_mode' required style='width:100%;min-height:60px'></textarea></label>
<label>方案<br><textarea name='proposal' required style='width:100%;min-height:75px'></textarea></label>
<div class='grid'><label>Token / 模型调用 / 流程成本<br><textarea name='cost' required style='width:100%;min-height:70px'></textarea></label><label>风险 / 跨流程副作用<br><textarea name='risk' required style='width:100%;min-height:70px'></textarea></label></div>
<label>受影响文件（每行一个）<br><textarea name='affected_files' required style='width:100%;min-height:60px'></textarea></label>
<label>验证要求（每行一个）<br><textarea name='validation' required style='width:100%;min-height:60px'></textarea></label>
<button type='submit' style='padding:10px 18px'>创建正式提请</button>
</form>
"""
    return _page("Sciver Observatory — Change Requests", body)




def _cached_services_accounts(snapshot: dict | None) -> tuple[str, int]:
    """Safe last-good quota HTML. Never present stale reset-card/control data."""
    data = snapshot if isinstance(snapshot, dict) else {}
    values = [a for a in data.get("accounts", []) if
              isinstance(a, dict) and a.get("ok") is True and
              a.get("account_home") in {"account-1", "account-2"}]
    cells = []
    latest = 0
    active_stamp = 0
    active = data.get("active_account")
    if not values:
        cells.append('<div class="card">暂无历史额度记录 · 正在后台查询…</div>')
    for a in values:
        account = a["account_home"]
        name = "Codex 1" if account == "account-1" else "Codex 2"
        stamp = a.get("queried_at") if type(a.get("queried_at")) is int else 0
        latest = max(latest, stamp)
        if account == active: active_stamp = stamp
        clock = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M:%S") if stamp else "未知"
        windows = []
        for key in ("primary", "secondary"):
            window = a.get(key)
            if not isinstance(window, dict):
                continue
            duration = window.get("window_duration_mins")
            label = ("5-hour window" if duration == 300 else
                     "7-day window" if duration == 10080 else
                     f"{duration}-minute window" if type(duration) is int and duration > 0 else
                     "Window duration unknown")
            remaining = window.get("remaining_percent")
            valid = type(remaining) in (int, float) and 0 <= remaining <= 100
            value = str(remaining) + "%" if valid else "unknown"
            windows.append('<div class="tm-quota-window"><span class="small">' + label +
                           '</span><div class="tm-quota-percent">' + html.escape(value) +
                           '</div><div class="small">Remaining · 上次确认</div>' +
                           '<div class="tm-quota-progress"><span style="width:' +
                           (str(remaining) if valid else '0') + '%"></span></div></div>')
        active_label = '<span class="tm-active-indicator">● 全局默认</span>' if account == active else ''
        cells.append('<div class="card tm-account-card tm-quota-historical' +
                     (' is-active' if account == active else '') + '">' +
                     '<div class="tm-account-header"><h2>' + name + '</h2>' + active_label +
                     '</div><p class="small tm-quota-cached-label">历史快照 · ' + clock +
                     '（完整操作信息等待实时查询）</p>' +
                     '<div class="grid">' + ''.join(windows) + '</div></div>')
    return ''.join(cells), active_stamp or latest


def services_html(state: dict, manager: StaticViewManager | None,
                  quota_snapshot: dict | None = None,
                  back_href: str | None = None) -> str:
    """Show last-good quota immediately while the live account controls load.

    back_href is the visitor's last still-existing Task Console when known.
    None keeps the workspace default parent (Workflow).
    """
    return _page("Sciver Observatory — Services",
                 codex_usage_body(quota_snapshot), active="services",
                 back_href=back_href)


def codex_usage_body(quota_snapshot: dict | None = None) -> str:
    """Use persisted quota-only history for first paint; never invent reset credits."""
    cached_cards, cached_at = _cached_services_accounts(quota_snapshot)
    initial_age = max(0, int(datetime.now().timestamp()) - cached_at) if cached_at else None
    initial_label = ('历史记录 · ' + str(initial_age) + '秒前更新'
                     if initial_age is not None and initial_age < 60
                     else ('历史记录 · ' + str(initial_age // 60) + '分钟前更新'
                           if initial_age is not None else '等待实时查询'))
    body = """<section class="tm-quota-section" aria-label="Codex usage"><div class="tm-quota-heading"><h2>Codex Usage</h2>
  <div class="tm-update-status" aria-label="额度刷新状态">
    <span class="tm-update-age"><span class="tm-update-dot" aria-hidden="true"></span><span id="usage-updated" aria-live="off">__QUOTA_CACHED_AGE__</span><span id="usage-sync" class="tm-usage-sync" role="status" aria-live="polite"></span></span>
    <button type="button" class="tm-refresh-button" onclick="loadUsage({force:true})" aria-label="立即刷新 Codex 额度">⟳ 立即刷新</button>
  </div>
</div>
<p id='services-tier-label' class='small' role='status' aria-live='polite'>读取当前档位与 Provider…</p>
<div id='usage' class='grid' aria-label='Codex 账号额度与切换' data-cached-at='__QUOTA_CACHED_AT__'>__QUOTA_CACHED_CARDS__</div>
<div id='switch-message' class='small tm-switch-feedback' role='status' aria-live='polite'></div>
<p class='small tm-reset-note'>重置卡操作仅适用于 Codex 1/2，需要连续确认 2 次；任一额度窗口剩余大于 30% 或数据未知时，需要确认 3 次。切换保持各 Worker 原有档位；新启动 Worker 在下一新 Cycle 应用。已运行的 Worker 进程可能需在安全检查点重新加载代码才会采用新来源；冻结 Cycle 不改，角色级显式覆盖仍单独有效。</p>
<div id='reset-message' class='small' role='status' aria-live='polite'></div>
</section>
<script>
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
}
function fmtReset(ts) {
  if (!ts) return 'unknown';
  return new Date(ts * 1000).toLocaleString();
}
function quotaWindowName(w) {
  const minutes = w?.window_duration_mins;
  if (minutes === 300) return '5-hour window';
  if (minutes === 10080) return '7-day window';
  return Number.isInteger(minutes) && minutes > 0 ? minutes + '-minute window' : 'Window duration unknown';
}
function windowCard(w) {
  const name = quotaWindowName(w);
  const pct = Number.isFinite(w.remaining_percent) && w.remaining_percent >= 0 && w.remaining_percent <= 100 ? w.remaining_percent : null;
  const remain = pct === null ? 'unknown' : pct + '%';
  return '<div class="tm-quota-window"><span class="small">' + name + '</span>' +
    '<div class="tm-quota-percent">' + remain + '</div><div class="small">Remaining</div>' +
    '<div class="tm-quota-progress"><span style="width:' + (pct === null ? 0 : pct) + '%"></span></div>' +
    '<div class="small">Resets: ' + fmtReset(w.resets_at) + '</div></div>';
}
let lastUsageAt = Number(document.getElementById('usage')?.dataset?.cachedAt) || null;
let hasCodexSnapshot = Number.isFinite(lastUsageAt) && lastUsageAt > 0;
let renderedUsageSignature = '';
let usageRequestId = 0;
let usageForceBusy = false;
let usagePollTimer = null;
let resetBusy = false;
let switchBusy = false;
let selectedService = null;
let lastRenderedAccounts = null;
let lastCodexActive = null;
let lastUsageCached = true;
function renderServiceAccounts() {
  if (!lastRenderedAccounts) return;
  const target = document.getElementById('usage');
  const active = selectedService?.active_account || lastCodexActive;
  const accounts = lastRenderedAccounts;
  const content = accounts.map(a => accountCard(a, active, lastUsageCached)).join('');
  if (content !== renderedUsageSignature) {
    target.innerHTML = content;
    renderedUsageSignature = content;
  }

}
async function loadServiceSelection() {
  const label = document.getElementById('services-tier-label');
  try {
    const response = await fetch('/services-selection.json', {cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    selectedService = data;
    if (label) label.textContent = '当前档位：' + data.tier + ' 档 · 当前模型提供商：' +
      'Codex' + ' · 切换后在下个 Worker Cycle 生效';
    renderServiceAccounts();
  } catch (error) {
    if (label) label.textContent = '当前档位/Provider 无法确认：' + String(error.message || error);
  }
}

function updateUsageAge() {
  const element = document.getElementById('usage-updated');
  if (!element) return;
  if (!Number.isFinite(lastUsageAt) || lastUsageAt <= 0) {
    element.textContent = '更新状态未知';
    return;
  }
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - lastUsageAt));
  const prefix = seconds < 300 ? '已核实额度 · ' : (hasCodexSnapshot ? '历史快照 · ' : '历史记录 · ');
  const clock = new Date(lastUsageAt * 1000).toLocaleTimeString();
  if (seconds < 60) element.textContent = prefix + clock + ' · ' + seconds + '秒前更新';
  else if (seconds < 3600) element.textContent = prefix + clock + ' · ' + Math.floor(seconds / 60) + '分钟前更新';
  else if (seconds < 86400) element.textContent = prefix + clock + ' · ' + Math.floor(seconds / 3600) + '小时前更新';
  else element.textContent = prefix + clock + ' · ' + Math.floor(seconds / 86400) + '天前更新';
}
function accountCard(a, activeAccount, cachedSnapshot = false) {
  const account = ['account-1', 'account-2'].includes(a.account_home) ? a.account_home : '';
  const isActive = account !== '' && activeAccount === account;
  const label = escapeHtml(a.label || (account === 'account-1' ? 'Codex 1' : 'Codex 2'));
  const action = isActive
    ? '<span class="tm-active-indicator">● 全局默认</span>'
    : (account ? '<button type="button" class="tm-account-switch" data-account="' + account +
        '" onclick="switchAccount(this.dataset.account)" ' + (switchBusy || resetBusy ? 'disabled' : '') +
        '>切换到此账号</button>' : '');
  const heading = '<div class="tm-account-header"><h2>' + label + '</h2>' + action + '</div>';
  const base = '<div class="card tm-account-card' + (isActive ? ' is-active' : '') + '">' + heading;
  if (!a.ok) {
    return base + '<p class="warning" role="alert">额度查询失败：' + escapeHtml(a.error || '未知错误') + '</p></div>';
  }
  const allowed = a.ordinary_usage_allowed === true ? 'Available' : (a.ordinary_usage_allowed === false ? 'Blocked' : 'Unknown');
  const count = Number.isInteger(a.reset_credits?.available_count) && a.reset_credits.available_count >= 0
    ? a.reset_credits.available_count : null;
  const queriedAt = Number(a.queried_at);
  const ageSeconds = Number.isFinite(queriedAt) && queriedAt > 0
    ? Math.max(0, Math.floor(Date.now() / 1000 - queriedAt)) : null;
  const staleSnapshot = cachedSnapshot && (ageSeconds === null || ageSeconds >= 300);
  const ageLabel = ageSeconds === null ? '更新时间未知' :
    ageSeconds < 60 ? ageSeconds + ' 秒前' :
    ageSeconds < 3600 ? Math.floor(ageSeconds / 60) + ' 分钟前' :
    ageSeconds < 86400 ? Math.floor(ageSeconds / 3600) + ' 小时前' :
    Math.floor(ageSeconds / 86400) + ' 天前';
  const freshness = cachedSnapshot
    ? '<p class="small ' + (staleSnapshot ? 'warning ' : '') + 'tm-quota-freshness" role="status">' +
      (staleSnapshot
        ? '历史快照 · 上次成功查询 ' + ageLabel + '；当前额度未确认，请点击“立即刷新”。'
        : '缓存快照 · 上次成功查询 ' + ageLabel + '；点击“立即刷新”可获取实时额度。') + '</p>'
    : '';
  return base +
    '<p class="tm-account-meta"><span class="badge">' + escapeHtml(a.plan_type || 'unknown plan') + '</span> · ' + allowed + '</p>' +
    freshness +
    '<div class="grid">' + [a.primary, a.secondary].filter(w => w && typeof w === 'object').map(windowCard).join('') + '</div>' +
    '<p class="tm-reset-count"><strong>Reset cards available: ' + (count === null ? 'Unknown' : count) + '</strong></p>' +
    '<button type="button" class="reset-credit-button" data-account="' + account + '" onclick="redeemResetCard(this.dataset.account)" ' +
    (count > 0 && account && !resetBusy && !switchBusy ? '' : 'disabled') + '>Use 1 reset card</button>' +
    '</div>';
}
async function loadUsage(options = {}) {
  if (switchBusy || usageForceBusy) return;
  const force = options.force === true;
  if (force) usageForceBusy = true;
  const requestId = ++usageRequestId;
  const target = document.getElementById('usage');
  const refresh = document.querySelector('.tm-refresh-button');
  const sync = document.getElementById('usage-sync');
  if (refresh) refresh.disabled = true;
  if (sync) sync.textContent = '· 后台刷新中…';
  try {
    // Automatic polls use the shared five-minute server snapshot; only an
    // explicit refresh performs a synchronous live Provider query.
    const endpoint = force ? '/codex-usage.json' : '/codex-usage-cached.json';
    const response = await fetch(endpoint, {cache: 'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const payload = await response.json();
    if (requestId !== usageRequestId) return;
    const accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    lastUsageCached = payload.historical === true || !force;
    const validCodexAccounts = accounts.filter(a => a?.ok === true);
    lastRenderedAccounts = accounts;
    lastCodexActive = payload.active_account;
    if (!validCodexAccounts.length)
      throw new Error('没有收到可验证的 Codex 账号额度，继续显示历史快照');
    const visibleAccounts = accounts;
    const content = visibleAccounts.map(a => accountCard(a, selectedService?.active_account || payload.active_account, lastUsageCached)).join('');
    // Keep the previous DOM if nothing relevant changed, instead of
    // blanking/rewriting card controls every scheduled poll.
    if (content !== renderedUsageSignature) {
      target.innerHTML = content;
      renderedUsageSignature = content;

    }
    hasCodexSnapshot = true;
    // Only provider queried_at proves when quota was actually checked. A fresh
    // HTTP response or generated_at timestamp must never make old quota look fresh.
    const stamp = Math.max(0, ...validCodexAccounts.map(a => Number(a.queried_at) || 0));
    if (stamp > 0) lastUsageAt = stamp;
    updateUsageAge();
    const stale = !Number.isFinite(lastUsageAt) || Date.now() / 1000 - lastUsageAt >= 300;
    if (sync) {
      if (payload.refreshing) sync.textContent = stale ? '· 额度数据已过期，后台刷新中…' : '· 已有近期额度，后台正在复核…';
      else if (payload.last_refresh_error) sync.textContent = '· 最近一次刷新失败，保留历史额度';
      else if (stale) sync.textContent = '· 当前为历史快照，尚未确认最新额度';
      else sync.textContent = '· 额度最近 5 分钟内已核实';
    }
  } catch (error) {
    if (requestId !== usageRequestId) return;
    // Never discard valid historical quotas on a network/RPC failure.
    updateUsageAge();
    if (!target.querySelector?.('.tm-account-card'))
      target.innerHTML = '<div class="card warning" role="alert">额度获取失败，请重试：' + escapeHtml(error.message || error) + '</div>';
    if (sync) sync.textContent = '· 查询失败，保留上次记录';
  } finally {
    if (force) usageForceBusy = false;
    if (requestId === usageRequestId && refresh) refresh.disabled = false;
  }
}
function scheduleUsagePoll(delay) {
  // Browser timers are always available; this guard keeps the inline logic
  // independently testable in minimal non-browser harnesses.
  if (typeof setTimeout !== 'function') return;
  if (usagePollTimer !== null && typeof clearTimeout === 'function') clearTimeout(usagePollTimer);
  usagePollTimer = setTimeout(async () => {
    await loadUsage();
    scheduleUsagePoll(hasCodexSnapshot ? 60000 : 5000);
  }, delay);
}
// A missing or stale quota window is high-risk: require a third confirmation.
function resetConfirmationCount(snapshot) {
  const remaining = [snapshot?.primary?.remaining_percent, snapshot?.secondary?.remaining_percent];
  return remaining.every(pct => Number.isFinite(pct) && pct >= 0 && pct <= 30) ? 2 : 3;
}
async function redeemResetCard(account) {
  if (resetBusy || switchBusy || !['account-1', 'account-2'].includes(account)) return;
  resetBusy = true;
  document.querySelectorAll('.reset-credit-button, .tm-account-switch').forEach(button => { button.disabled = true; });
  const message = document.getElementById('reset-message');
  const label = account === 'account-1' ? 'Codex 1' : 'Codex 2';
  try {
    // Re-check this account immediately before confirmation, not just on the 60-second display poll.
    let snapshot = null;
    try {
      const quotaResponse = await fetch('/codex-usage.json', {cache: 'no-store'});
      if (quotaResponse.ok) {
        const quotaPayload = await quotaResponse.json();
        snapshot = quotaPayload.accounts?.find(a => a.account_home === account && a.ok) || null;
      }
    } catch (_) {
      // Unknown status is treated as higher risk rather than assuming quota was exhausted.
    }
    const confirmations = resetConfirmationCount(snapshot);
    const remaining = name => {
      const value = snapshot?.[name]?.remaining_percent;
      return Number.isFinite(value) ? value + '%' : '未知';
    };
    for (let step = 1; step <= confirmations; step++) {
      const prefix = '第 ' + step + '/' + confirmations + ' 次确认：';
      const warning = confirmations === 3
        ? '目前剩余额度（5 小时：' + remaining('primary') + '，7 天：' +
          remaining('secondary') + '）存在高于 30% 或未知的情况。\\n'
        : '';
      if (!window.confirm(prefix + '\\n' + warning + '确定为 ' + label +
          ' 消耗 1 张真实重置卡吗？此操作不可撤销。')) {
        message.textContent = label + '：已取消，未消耗重置卡。';
        return;
      }
    }
    message.textContent = '正在为 ' + label + ' 兑换 1 张重置卡…';
    const attempt = crypto.randomUUID();
    const response = await fetch('/codex-account/reset-credit', {
      method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({account: account, idempotency_key: attempt})
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
    const outcomes = {
      reset: '额度重置成功，已兑换 1 张重置卡。',
      nothingToReset: '当前额度无需重置，未执行重置。',
      noCredit: '当前没有可用重置卡。',
      alreadyRedeemed: '本次请求已经兑换过，不会重复消耗。'
    };
    message.textContent = label + '：' + (outcomes[payload.outcome] || '结果未知，请检查额度。');
  } catch (error) {
    message.textContent = label + '：重置失败或结果不明（' + error + '），请先检查额度，避免重复消耗。';
  } finally {
    resetBusy = false;
    await loadUsage({force:true});
  }
}
async function switchAccount(account) {
  if (switchBusy || resetBusy || !['account-1', 'account-2'].includes(account)) return;
  switchBusy = true;
  const priorTier = selectedService?.tier;
  // Invalidate any in-flight GET so it cannot redraw an old active account after switching.
  usageRequestId++;
  const message = document.getElementById('switch-message');
  const label = account === 'account-1' ? 'Codex 1' : 'Codex 2';
  message.textContent = '正在切换到 ' + label + '，保留现有档位…';
  document.querySelectorAll('.tm-account-switch, .reset-credit-button').forEach(button => { button.disabled = true; });
  try {
    const response = await fetch('/services-selection/switch', {
      method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-TM-Operator-Intent': '1'}, credentials: 'same-origin',
      body: 'account=' + encodeURIComponent(account)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error || ('HTTP ' + response.status));
    if (payload?.active_account !== account || !Number.isInteger(payload?.tier) ||
        (priorTier != null && payload.tier !== priorTier)) {
      throw new Error('切换响应的账号或档位与请求不一致');
    }
    selectedService = payload;
    message.textContent = '已切换到 ' + label + ' · 保持 ' + payload.tier + ' 档（下一 Worker Cycle 生效）。';
  } catch (error) {
    // A proxy may drop the POST response after a successful server-side switch.
    // Never replay this state-changing request; reconcile via a fresh read only.
    const reason = String(error.message || error);
    let verified = null;
    try {
      const check = await fetch('/services-selection.json', {cache:'no-store'});
      if (check.ok) verified = await check.json();
    } catch (_) { /* Current selection cannot be established. */ }
    if (verified?.active_account === account && Number.isInteger(verified.tier) &&
        (priorTier == null || verified.tier === priorTier)) {
      selectedService = verified;
      message.textContent = '切换接口响应异常，但已核验当前账号为 ' + label +
        ' · 保持 ' + verified.tier + ' 档（下一 Worker Cycle 生效）。';
    } else if (verified?.active_account) {
      selectedService = verified;
      message.textContent = '未确认切换到 ' + label + '：' + reason +
        '；目前账号为 ' + verified.active_account + '，档位 ' + verified.tier + '。';
    } else {
      message.textContent = '切换结果不明：' + reason + '；无法核验实际账号，请刷新页面确认后再操作。';
    }
  } finally {
    switchBusy = false;
    await loadServiceSelection();
    await loadUsage({force:true});
  }
}
loadServiceSelection().finally(() => loadUsage().finally(() => scheduleUsagePoll(hasCodexSnapshot ? 60000 : 5000)));
setInterval(updateUsageAge, 1000);
</script>"""
    return (body.replace('__QUOTA_CACHED_AGE__', html.escape(initial_label))
                .replace('__QUOTA_CACHED_AT__', str(cached_at))
                .replace('__QUOTA_CACHED_CARDS__', cached_cards) + worker_account_section_html())


def codex_usage_html() -> str:
    """Legacy Python entrypoint (the standalone browser route stays retired)."""
    return _page("Sciver Observatory — Codex Usage", codex_usage_body(), active="services",
                 back_href="/services/", breadcrumb_leaf="Codex Usage")


def _find_run(state: dict, identifier: str) -> dict | None:
    wanted = unquote(identifier)
    for run in state.get("runs", []):
        keys = {
            str(run.get("observer_id")),
            str(run.get("run_id")),
            str(run.get("cycle")),
            str(run.get("task_name")),
        }
        if wanted in keys:
            return run
    return None


def run_detail_html(
    state: dict,
    identifier: str,
    sequence_result: SequenceBuildResult | None = None,
) -> str:
    wanted = unquote(identifier)
    run = _find_run(state, identifier)
    if run is None:
        return _page("Run not found", f"<h1>Run not found</h1><p><code>{html.escape(wanted)}</code></p>")
    cards = []
    for key in [
        "observer_id",
        "run_id",
        "task_name",
        "task_root",
        "research_root",
        "cycle",
        "state",
        "raw_status",
        "phase",
        "active_agent",
        "pid",
        "pid_alive",
        "started_at",
        "last_activity_at",
        "tokens_total",
        "current_round",
    ]:
        source = run.get("provenance", {}).get(key, {})
        cards.append(
            f"<div class='card'><strong>{html.escape(key)}</strong><br>{_fmt(run.get(key))}"
            f"<p class='small'>source: {_fmt(source.get('source'))}<br>field: {_fmt(source.get('field'))}<br>method: {_fmt(source.get('method'))}</p></div>"
        )
    warnings = "".join(
        f"<li><strong>{_fmt(item.get('code'))}</strong> — {_fmt(item.get('message'))}<br><span class='small'>{_fmt(item.get('source'))}</span></li>"
        for item in run.get("warnings", [])
    )
    sequence = ""
    if sequence_result is not None and sequence_result.ok and sequence_result.output_path.is_file():
        sequence = (
            "<h2>Run Sequence</h2>"
            "<p class='small'>Derived only from persisted runtime lifecycle facts. Full scientific prompts/messages are not copied.</p>"
            f"<iframe class='diagram' src='/run-artifacts/{html.escape(str(run.get('observer_id')), quote=True)}/sequence.html' "
            "title='Sciver Run Sequence'></iframe>"
        )
    elif sequence_result is not None and sequence_result.warning:
        sequence = (
            "<h2>Run Sequence</h2><div class='card warning'><strong>Sequence unavailable</strong><br>"
            + _fmt(sequence_result.warning)
            + "</div>"
        )
    return _page(
        f"TM Run {wanted}",
        f"<h1>Run {_fmt(run.get('run_id'))}</h1><p class='small'>Read-only fact view.</p>"
        f"{sequence}<div class='grid'>{''.join(cards)}</div><h2>Warnings</h2><ul>{warnings or '<li>none</li>'}</ul>",
    )



_LAST_CONSOLE_COOKIE = "tm_last_task_console"


def _remembered_console(cookie_header: str | None, state: dict) -> str | None:
    """Resolve the visitor's last task without allowing arbitrary redirects."""
    import re
    from http.cookies import CookieError, SimpleCookie

    if not isinstance(cookie_header, str) or len(cookie_header) > 8192:
        return None
    try:
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        item = cookies.get(_LAST_CONSOLE_COOKIE)
    except (CookieError, ValueError):
        return None
    ident = item.value if item else ""
    if not re.fullmatch(r"[0-9a-f]{16}", ident):
        return None
    if not any(row.get("observer_id") == ident for row in state.get("runs", [])):
        return None
    return f"/run/{ident}/"


def _console_landing(cookie_header: str | None, state: dict) -> str:
    """Prefer a verified live task over the last visited (possibly old) task.

    With multiple live tasks, respect a live remembered selection; otherwise
    show the live-only task list rather than silently choosing one worker/task.
    """
    remembered = _remembered_console(cookie_header, state)
    live_urls = [
        url for row in state.get("runs", []) if isinstance(row, dict)
        if str(row.get("state") or "").upper() == "LIVE"
        if (url := task_console_url(row.get("observer_id"))) is not None
    ]
    if len(live_urls) == 1:
        return live_urls[0]
    if len(live_urls) > 1:
        return remembered if remembered in live_urls else "/run/?status=LIVE"

    # A Worker may have just exited between polling intervals. Keep its task
    # reachable as the *recent* task, but never mislabel it LIVE. A very old
    # browser cookie must not win over a fresh ORPHANED/paused task.
    now = datetime.now(timezone.utc)
    recent_urls: list[str] = []
    for row in state.get("runs", []):
        if not isinstance(row, dict):
            continue
        url = task_console_url(row.get("observer_id"))
        if not url:
            continue
        try:
            timestamp = datetime.fromisoformat(str(row.get("last_activity_at") or "").replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                continue
            seconds_ago = (now - timestamp).total_seconds()
        except (ValueError, TypeError, OverflowError):
            continue
        if -120 <= seconds_ago <= 3600:
            recent_urls.append(url)
    if len(recent_urls) == 1:
        return recent_urls[0]
    if len(recent_urls) > 1:
        return remembered if remembered in recent_urls else "/run/"
    return remembered or "/run/"


def _console_cookie(ident: str) -> str:
    """Browser-scoped, 90-day task preference; not a login credential."""
    return (f"{_LAST_CONSOLE_COOKIE}={ident}; Path=/; Max-Age=7776000; "
            "HttpOnly; Secure; SameSite=Lax")


class StateCache:
    """Single-flight stale-while-revalidate cache for task state and slow Git metadata.

    Once a first snapshot exists, HTTP requests never wait for a refresh. The cold
    start has a bounded wait; if collection fails, a clearly marked empty snapshot
    is returned while a later request retries after a short backoff.
    """

    def __init__(self, search_root: Path, ttl_s: float = 5.0, *, git_ttl_s: float = 60.0, cold_wait_s: float = 8.0):
        self.search_root = search_root
        self.ttl_s = max(0.2, float(ttl_s))
        self.git_ttl_s = max(5.0, float(git_ttl_s))
        self.cold_wait_s = max(0.1, float(cold_wait_s))
        self._condition = threading.Condition()
        self._state: dict | None = None
        self._at = 0.0
        self._refreshing = False
        self._retry_after = 0.0
        self._cold_waiter = False
        self._cold_wait_completed = False
        self._last_error: str | None = None
        self._git_snapshot: dict | None = None
        self._git_at = 0.0
        self._git_refreshing = False
        self._git_retry_after = 0.0

    def _start_state_refresh_locked(self) -> None:
        if self._refreshing or time.monotonic() < self._retry_after:
            return
        self._refreshing = True
        threading.Thread(target=self._refresh_state, name="tm-observer-state-refresh", daemon=True).start()

    def _start_git_refresh_locked(self) -> None:
        now = time.monotonic()
        if (self._git_refreshing or now < self._git_retry_after
                or (self._git_at and now - self._git_at < self.git_ttl_s)):
            return
        self._git_refreshing = True
        threading.Thread(target=self._refresh_git, name="tm-observer-git-refresh", daemon=True).start()

    def _refresh_state(self) -> None:
        try:
            state = collect_state(self.search_root, include_git_info=False)
        except Exception as exc:
            with self._condition:
                self._last_error = type(exc).__name__
                self._retry_after = time.monotonic() + 1.0
                self._refreshing = False
                self._condition.notify_all()
            return
        with self._condition:
            git_info = self._git_snapshot
            if git_info is None and self._state is not None:
                previous_system = self._state.get("system", {})
                git_info = {key: previous_system.get(key) for key in ("git_head", "git_branch", "git_dirty")}
                git_info["provenance"] = {
                    key: (previous_system.get("provenance") or {}).get(key)
                    for key in ("git_head", "git_branch", "git_dirty")
                }
            if git_info:
                state["system"].update({key: value for key, value in git_info.items() if key != "provenance"})
                state["system"].setdefault("provenance", {}).update(git_info.get("provenance") or {})
            self._state = state
            self._at = time.monotonic()
            self._last_error = None
            self._retry_after = 0.0
            self._refreshing = False
            self._start_git_refresh_locked()
            self._condition.notify_all()

    def _refresh_git(self) -> None:
        try:
            git_info = collect_git_info(self.search_root)
        except Exception as exc:
            with self._condition:
                self._last_error = type(exc).__name__
                self._git_retry_after = time.monotonic() + self.git_ttl_s
                self._git_refreshing = False
                self._condition.notify_all()
            return
        with self._condition:
            self._git_snapshot = git_info
            self._git_at = time.monotonic()
            self._git_retry_after = 0.0
            self._git_refreshing = False
            if self._state is not None:
                # Replace, rather than mutate, a snapshot already returned to a request.
                state = dict(self._state)
                system = dict(state.get("system") or {})
                system.update({key: value for key, value in git_info.items() if key != "provenance"})
                provenance = dict(system.get("provenance") or {})
                provenance.update(git_info.get("provenance") or {})
                system["provenance"] = provenance
                state["system"] = system
                self._state = state
            self._condition.notify_all()

    def _empty_state(self, status: str) -> dict:
        return {
            "schema": SCHEMA, "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "search_root": str(self.search_root), "refresh_status": status,
            "system": {"git_head": None, "git_branch": None, "git_dirty": None,
                       "observer_version": "unknown", "task_count": 0, "provenance": {}},
            "runs": [],
        }

    def get(self) -> dict:
        now = time.monotonic()
        with self._condition:
            if self._state is None:
                self._start_state_refresh_locked()
                self._start_git_refresh_locked()
                # Only one cold-start request may wait. Other simultaneous page/API
                # requests get an explicit loading snapshot instead of queueing behind it.
                if self._refreshing and not self._cold_wait_completed and not self._cold_waiter:
                    self._cold_waiter = True
                    deadline = time.monotonic() + self.cold_wait_s
                    try:
                        while self._state is None and self._refreshing:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            self._condition.wait(remaining)
                    finally:
                        self._cold_waiter = False
                        self._cold_wait_completed = True
                if self._state is None:
                    return self._empty_state("loading" if self._refreshing else "unavailable")
            elif now - self._at >= self.ttl_s:
                self._start_state_refresh_locked()
            self._start_git_refresh_locked()
            return self._state


def make_handler(
    cache: StateCache,
    static_manager: StaticViewManager | None = None,
    auth: AuthRealm | None = None,
    *,
    field_notes_state_dir: Path | None = None,
):
    notes_state_dir = (
        field_notes_state_dir
        or (static_manager.state_dir if static_manager is not None else cache.search_root / ".tm-observer-state")
    ).expanduser()
    # Shared, persisted quota history for ALL Run tabs. Reading this is fast;
    # a background Codex RPC is launched no more than once per five minutes.
    quota_history = QuotaHistory(notes_state_dir / 'codex-quota-last-good.json')

    class Handler(BaseHTTPRequestHandler):
        server_version = "TMObservatory/0.2"

        def _send(self, payload: bytes, content_type: str,
                  status: HTTPStatus = HTTPStatus.OK, *, set_cookie: str | None = None,
                  cache_control: str = "no-store", etag: str | None = None,
                  retry_after: int | None = None) -> None:
            if etag and status == HTTPStatus.OK and self.headers.get("If-None-Match", "").strip() == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("Cache-Control", cache_control)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            if etag:
                self.send_header("ETag", etag)
            if retry_after is not None:
                self.send_header("Retry-After", str(max(1, int(retry_after))))
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            if auth is None:
                return True
            return authorize_header(self.headers.get("Authorization"), auth) or session_from_cookie(
                self.headers.get("Cookie"), auth
            )

        def _send_unauthorized(self) -> None:
            payload = b"authentication required\n"
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="Sciver Observatory", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/favicon.svg":
                icon_path = Path(__file__).resolve().parent / "static" / "sciver-mark.svg"
                try:
                    payload = icon_path.read_bytes()
                except OSError:
                    self._send(b"favicon not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                else:
                    self._send(payload, "image/svg+xml; charset=utf-8", cache_control="public, max-age=86400")
                return
            if auth is not None and path == "/login":
                payload = login_html().encode("utf-8")
                self._send(payload, "text/html; charset=utf-8")
                return
            if not self._authorized():
                if path in {"/healthz", "/observer_state.json"}:
                    self._send_unauthorized()
                else:
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/login")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                return
            if path.startswith("/research-chat/assets/"):
                relative = path.removeprefix("/research-chat/assets/")
                dist = Path(__file__).resolve().parent / "research_chat_dist"
                try:
                    target = (dist / relative).resolve(strict=True)
                    if (not relative or ".." in Path(relative).parts or dist.resolve() not in target.parents
                            or target.is_symlink() or not target.is_file()):
                        raise OSError("asset not found")
                    content_type = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                                    ".svg": "image/svg+xml", ".woff2": "font/woff2"}.get(
                                        target.suffix.lower(), "application/octet-stream")
                    payload = target.read_bytes()
                    etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
                    self._send(payload, content_type, cache_control="private, max-age=300, must-revalidate", etag=etag)
                except OSError:
                    self._send(b"chat asset not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            if path == "/vendor/loquix-incident.js":
                bundle = Path(__file__).resolve().parent / "vendor" / "loquix-incident.js"
                if not bundle.is_file() or bundle.is_symlink():
                    self._send(b"chat bundle missing\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                payload = bundle.read_bytes()
                etag = '"' + hashlib.sha256(payload).hexdigest() + '"'
                self._send(payload, "text/javascript; charset=utf-8",
                           cache_control="private, max-age=300, must-revalidate", etag=etag)
                return
            state = cache.get()
            if state.get("refresh_status") in {"loading", "unavailable"}:
                if path.startswith("/api/") or path in {"/healthz", "/observer_state.json"}:
                    self._send(b"Task state is initializing; retry shortly.\n",
                               "text/plain; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
                else:
                    # A cold-cache race can affect a browser navigation even while
                    # another request is successfully building the first snapshot.
                    # Do not strand the user on a raw-text 503 page: retry the same
                    # navigation automatically, with a manual link as a fallback.
                    retry_path = html.escape(self.path, quote=True)
                    page = (
                        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                        "<meta http-equiv=\"refresh\" content=\"1\">"
                        "<title>Sciver · 正在初始化</title></head><body>"
                        "<main><h1>任务状态正在初始化</h1>"
                        "<p>页面将在 1 秒后自动重试。</p>"
                        f"<p><a href=\"{retry_path}\">立即重试</a></p>"
                        "</main></body></html>"
                    )
                    self._send(page.encode("utf-8"), "text/html; charset=utf-8",
                               HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
                return
            if path in {"/research-chat", "/research-chat/"}:
                from .research_chat_ui import page_body
                query = parse_qs(self.path.partition("?")[2])
                selected = (query.get("session") or [None])[0]
                if (query.get("new") or [""])[0] == "1":
                    selected = "__new__"
                try:
                    view = research_chat_snapshot(notes_state_dir, state, selected)
                except ResearchChatError as exc:
                    self._send((json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8"),
                               "application/json; charset=utf-8", HTTPStatus(exc.status))
                    return
                self._send(_page("Research Chat", page_body(view), active="research_chat").encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path.startswith("/api/research-chat/") and path.endswith("/progress"):
                parts = path.strip("/").split("/")
                if len(parts) == 4:
                    params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                    try:
                        offset = int((params.get("offset") or ["0"])[0])
                        if offset < 0 or offset > 10_000_000:
                            raise ValueError("offset out of range")
                    except (TypeError, ValueError, OverflowError):
                        self._send(b'{"error":"invalid progress offset"}',
                                   "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                        return
                    try:
                        view = research_chat_snapshot(notes_state_dir, state, parts[2])
                        session = view.get("session") or {}
                        active_run = session.get("active_run") if isinstance(session.get("active_run"), dict) else {}
                        full_text = str(session.get("stream_text", "") or "")
                        reset = offset > len(full_text)
                        start = 0 if reset else offset
                        payload = json.dumps({"status": session.get("stream_status"),
                                              "streaming": session.get("streaming", False),
                                              "text": full_text[start:], "next_offset": len(full_text),
                                              "reset": reset, "error": active_run.get("error_type")},
                                             ensure_ascii=False).encode("utf-8")
                        self._send(payload, "application/json; charset=utf-8")
                    except ResearchChatError as exc:
                        self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                                   "application/json; charset=utf-8", HTTPStatus(exc.status))
                    return
            if path.startswith("/api/research-chat/") and path.endswith("/state"):
                parts = path.strip("/").split("/")
                if len(parts) == 4:
                    try:
                        view = research_chat_snapshot(notes_state_dir, state, parts[2])
                        session = view.get("session") or {}
                        payload = json.dumps({"id": session.get("id"), "title": session.get("title"),
                                              "mode": session.get("mode"), "model": session.get("model"),
                                              "messages": session.get("messages", []),
                                              "active_run": session.get("active_run"),
                                              "streaming": session.get("streaming", False),
                                              "stream_status": session.get("stream_status"),
                                              "stream_text": session.get("stream_text", ""),
                                              "pending_action": session.get("pending_action")},
                                             ensure_ascii=False).encode("utf-8")
                        self._send(payload, "application/json; charset=utf-8")
                    except ResearchChatError as exc:
                        self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                                   "application/json; charset=utf-8", HTTPStatus(exc.status))
                    return
            if path.startswith("/api/research-chat/") and path.endswith("/settings"):
                parts = path.strip("/").split("/")
                if len(parts) == 4:
                    try:
                        view = research_chat_snapshot(notes_state_dir, state, parts[2])
                        session = view.get("session") or {}
                        payload = json.dumps({
                            "model": session.get("model") or "gpt-6.1-sol",
                            "reasoning_effort": session.get("reasoning_effort") or "medium",
                            "available_models": view.get("available_models", []),
                            "available_reasoning_efforts": view.get("available_reasoning_efforts", []),
                        }, ensure_ascii=False).encode("utf-8")
                        self._send(payload, "application/json; charset=utf-8")
                    except ResearchChatError as exc:
                        self._send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                                   "application/json; charset=utf-8", HTTPStatus(exc.status))
                    return
            if path.startswith("/api/supervisor-session/") and path.endswith(("/state", "/progress")):
                parts = [item for item in path.strip("/").split("/") if item]
                if len(parts) == 4 and parts[:2] == ["api", "supervisor-session"] and parts[3] in {"state", "progress"}:
                    from theory_master.supervisor_incident_session import (
                        IncidentSessionError, follow_up_pending, locate_incident,
                    )
                    from .supervisor_incident_ui import research_roots
                    session_id = parts[2]
                    try:
                        incident_root, record, rows = locate_incident(research_roots(state.get("runs", [])), session_id)
                        streaming = bool(record.get("status") == "active" and follow_up_pending(incident_root, session_id))
                        if parts[3] == "progress":
                            payload = json.dumps({"status": "streaming" if streaming else "complete",
                                                  "streaming": streaming, "text": "", "error": None},
                                                 ensure_ascii=False).encode("utf-8")
                        else:
                            role_map = {"supervisor": "assistant", "human": "user", "tool": "system", "system": "system"}
                            messages = []
                            for row in rows:
                                role = str(row.get("role") or "system")
                                content = str(row.get("content") or "")
                                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                                for image in metadata.get("images") or []:
                                    if not isinstance(image, dict):
                                        continue
                                    image_id = str(image.get("id") or "")
                                    if len(image_id) != 16 or any(ch not in "0123456789abcdef" for ch in image_id):
                                        continue
                                    name = str(image.get("name") or "image").replace("]", r"\]")
                                    content += f"\n\n![{name}](/api/supervisor-session/{quote(session_id, safe='')}/image/{image_id})"
                                messages.append({"id": str(row.get("id") or ""),
                                                 "role": role_map.get(role, "system"),
                                                 "content": content, "created_at": row.get("at")})
                            payload = json.dumps({"id": session_id, "mode": "supervisor",
                                                  "status": str(record.get("status") or "active"),
                                                  "cycle_id": str(record.get("cycle_id") or ""),
                                                  "trigger_event": str(record.get("trigger_event") or ""),
                                                  "messages": messages, "streaming": streaming,
                                                  "stream_status": "streaming" if streaming else "complete",
                                                  "stream_text": "", "active_run": None, "pending_action": None,
                                                  "available_models": [], "available_reasoning_efforts": []},
                                                 ensure_ascii=False).encode("utf-8")
                        self._send(payload, "application/json; charset=utf-8")
                    except (IncidentSessionError, OSError, ValueError):
                        self._send(b'{"error":"supervisor incident session not found"}\n',
                                   "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
            if path.startswith("/api/supervisor-session/") and "/image/" in path:
                parts = [item for item in path.strip("/").split("/") if item]
                if len(parts) == 5 and parts[:2] == ["api", "supervisor-session"] and parts[3] == "image":
                    from theory_master.supervisor_incident_session import IncidentSessionError
                    from .supervisor_incident_ui import read_image
                    try:
                        payload, media = read_image(state.get("runs", []), parts[2], parts[4])
                    except IncidentSessionError:
                        self._send(b"image not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                        return
                    self._send(payload, media)
                    return
            if path == "/api/supervisor-notifications":
                from .supervisor_notifications import notification_feed
                params = parse_qs(self.path.partition("?")[2])
                try:
                    offset = int((params.get("offset") or ["0"])[0])
                    limit = int((params.get("limit") or ["40"])[0])
                    result = notification_feed(state.get("runs", []), offset=offset, limit=limit)
                except (ValueError, OverflowError):
                    self._send(b'{"error":"invalid notification page"}',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                payload = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path.startswith("/api/canvas-md/tasks/"):
                try:
                    data, status = canvas_md_get(path, self.path.partition("?")[2], cache.search_root, state)
                except ExplorerError as exc:
                    data = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
                    status = exc.status
                if status == HTTPStatus.OK and path.endswith("/index"):
                    # The index is task-scoped and validated by its file signature.
                    # Revalidate in-browser to avoid repeatedly transferring multi-MB JSON.
                    etag = '"' + hashlib.sha256(data).hexdigest() + '"'
                    self._send(data, "application/json; charset=utf-8", HTTPStatus(status),
                               cache_control="private, no-cache", etag=etag)
                else:
                    self._send(data, "application/json; charset=utf-8", HTTPStatus(status))
                return
            if path == "/healthz":
                system = state.get("system") if isinstance(state.get("system"), dict) else {}
                payload = (
                    json.dumps(
                        {
                            "ok": True,
                            "generated_at": state.get("generated_at"),
                            "task_count": system.get("task_count"),
                            "static_views_ok": None
                            if static_manager is None or static_manager.last_result is None
                            else bool(static_manager.last_result.ok),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/observer_state.json":
                payload = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/services-workers.json":
                # Sanitized read-only projection of discovered task Workers.
                data = services_workers_snapshot(state)
                self._send((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", cache_control="no-store")
                return
            if path == "/services-selection.json":
                from theory_master.service_provider_switch import SelectionConflict, service_selection
                try:
                    data, status = service_selection(), HTTPStatus.OK
                except (SelectionConflict, OSError, ValueError) as exc:
                    data, status = {"error": str(exc)}, HTTPStatus.CONFLICT
                self._send((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", status)
                return
            if path == "/codex-usage-cached.json":
                # History immediately, refresh only in a daemon background
                # thread when due. Never block the Run page on provider RPC.
                snapshot = quota_history.snapshot(request_update=True)
                payload = (json.dumps(snapshot, ensure_ascii=False) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/codex-usage.json":
                # Services keeps its explicit, live, uncached refresh semantics;
                # good results also refresh the shared Run history.
                live = collect_codex_usage()
                quota_history.observe(live)
                payload = (json.dumps(live, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/workflow-field-notes.json":
                items = field_note_catalog(_synced_field_notes(notes_state_dir))
                payload = (json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path.startswith("/artifacts/"):
                name = path[len("/artifacts/") :]
                allowed = {"architecture.html", "workflow.html", "lifecycle.html", "changes.html"}
                if static_manager is None or name not in allowed:
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                artifact = static_manager.state_dir / "static" / name
                try:
                    payload = artifact.read_bytes()
                except OSError:
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                self._send(payload, "text/html; charset=utf-8")
                return
            if path.startswith("/run-artifacts/") and path.endswith("/sequence.html"):
                parts = path.strip("/").split("/")
                if static_manager is None or len(parts) != 3 or parts[0] != "run-artifacts" or parts[2] != "sequence.html":
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                observer_id = parts[1]
                if len(observer_id) != 16 or any(ch not in "0123456789abcdef" for ch in observer_id):
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                artifact = static_manager.state_dir / "runs" / observer_id / "sequence.html"
                try:
                    payload = artifact.read_bytes()
                except OSError:
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                self._send(payload, "text/html; charset=utf-8")
                return
            if path.startswith("/api/run/") and path.endswith("/attention-review"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "attention-review" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .attention_review_trace import attention_review_trace
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                try:
                    result = attention_review_trace(
                        run, cycle=(params.get("cycle") or [""])[0],
                        incident=(params.get("incident") or [""])[0],
                        review_ref=(params.get("ref") or [""])[0],
                        cursor=int((params.get("cursor") or ["0"])[0]),
                        full_item=(int(params["item"][0]) if "item" in params else None),
                    )
                except (ValueError, OSError, OverflowError, KeyError):
                    self._send(b'{"error":"invalid or unavailable Attention Reviewer trace"}',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path.startswith("/supervisor-notification/"):
                parts = [item for item in path.strip("/").split("/") if item]
                ident = parts[1] if len(parts) == 2 and parts[0] == "supervisor-notification" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                cycle = (params.get("cycle") or [""])[0]
                notification_id = (params.get("notification_id") or [""])[0]
                from .supervisor_notifications import notification_feed
                feed = notification_feed(state.get("runs", []), offset=0, limit=100)
                item = next((row for row in feed.get("items", [])
                             if row.get("cycle") == cycle and
                             (row.get("session_id") or row.get("id") or row.get("code")) == notification_id and
                             row.get("url", "").find("/run/" + ident + "/") >= 0), None)
                if item is None:
                    self._send(b"notification not found or no longer actionable\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                code = str(item.get("code") or "notification")
                summary = str(item.get("summary") or "需要检查当前任务状态。")
                from .run_monitor import CYCLE_NAME, data as monitor_data
                from .supervisor_incident_ui import research_roots
                from theory_master.supervisor_incident_session import IncidentSessionError, open_notification_incident, locate_incident
                root = Path(str(run.get("research_root") or "")).resolve()
                cycle_dir = Path(str(run.get("cycle_dir") or "")).resolve()
                if (not CYCLE_NAME.fullmatch(cycle) or cycle != str(run.get("cycle") or "")
                        or cycle_dir.parent != root or cycle_dir.name != cycle
                        or not root.is_dir() or not cycle_dir.is_dir()):
                    self._send(b"invalid or stale notification cycle\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                state_now = monitor_data(run)
                if not isinstance(state_now, dict): state_now = {}
                task_id_value = run.get("task_id")
                if task_id_value is None or not str(task_id_value).strip():
                    task_id_value = run.get("master_id")
                if task_id_value is None or not str(task_id_value).strip():
                    task_id_value = run.get("task_name")
                if task_id_value is None or not str(task_id_value).strip():
                    task_id_value = root.name
                try:
                    record = open_notification_incident(
                        research_root=root, cycle_dir=cycle_dir,
                        task_id=str(task_id_value),
                        notification_id=notification_id, code=code, summary=summary,
                        source={"observer_id": ident, "run_status": str(state_now.get("status") or "")},
                    )
                    _root, record, messages = locate_incident(research_roots([run]), record["id"])
                except (IncidentSessionError, OSError, ValueError):
                    self._send(b"notification conversation unavailable\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .navigation_model import incident_owner_id
                owner_id = incident_owner_id(state.get("runs", []), root) or ident
                target = f"/run/{quote(owner_id, safe='')}/supervisor/?session={quote(str(record['id']), safe='')}"
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", target)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/supervisor-session/"):
                parts = [item for item in path.strip("/").split("/") if item]
                session_id = parts[1] if len(parts) == 2 and parts[0] == "supervisor-session" else ""
                from theory_master.supervisor_incident_session import IncidentSessionError, locate_incident
                from .supervisor_incident_ui import research_roots
                try:
                    incident_root, record, _messages = locate_incident(research_roots(state.get("runs", [])), session_id)
                except (IncidentSessionError, OSError):
                    self._send(b"supervisor incident session not found\n",
                               "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .navigation_model import incident_owner_id
                owner_id = incident_owner_id(state.get("runs", []), incident_root)
                if owner_id:
                    target = f"/run/{quote(owner_id, safe='')}/supervisor/?session={quote(str(record['id']), safe='')}"
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", target)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    from .supervisor_incident_ui import page_body as incident_page_body
                    payload = _page("Supervisor Incident", incident_page_body(record, _messages),
                                    active="run", breadcrumb_leaf="Supervisor Incident").encode("utf-8")
                    self._send(payload, "text/html; charset=utf-8")
                return
            if path.startswith("/api/run/") and path.endswith("/supervisor"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "supervisor" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .run_monitor import data as monitor_data
                from .supervisor_view import home_status, sessions as supervisor_sessions
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                try:
                    result = supervisor_sessions(run, (params.get("cycle") or [""])[0])
                    result["home"] = home_status(run, monitor_data(run))
                except (ValueError, OSError):
                    self._send(b'{"error":"invalid or unavailable Supervisor session"}',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path.startswith("/api/run/") and path.endswith("/budget-review-trace"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "budget-review-trace" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .budget_review_trace import review_trace
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                try:
                    result = review_trace(
                        run, cycle=(params.get("cycle") or [""])[0],
                        review_index=int((params.get("review") or ["0"])[0]),
                        cursor=int((params.get("cursor") or ["0"])[0]),
                        full_item=(int(params["item"][0]) if "item" in params else None),
                    )
                except (ValueError, OSError, OverflowError):
                    self._send(b'{"error":"invalid or unavailable Reviewer trace"}',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path.startswith("/api/run/") and path.endswith("/conversation"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "conversation" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .stage_conversation import overview, transcript, sdk_detail, recent_event_detail
                parameters = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                mode = (parameters.get("mode") or ["index"])[0]
                cycle = (parameters.get("cycle") or [""])[0]
                try:
                    if mode == "index":
                        result = overview(run, cycle=cycle)
                    elif mode == "stage":
                        cursor = int((parameters.get("cursor") or ["0"])[0])
                        if cursor < 0:
                            raise ValueError("negative cursor")
                        result = transcript(run, cycle=cycle, stage_id=(parameters.get("stage") or [""])[0], offset=cursor)
                    elif mode == "sdk":
                        result = sdk_detail(run, cycle=cycle,
                                            sequence=int((parameters.get("cursor") or ["-1"])[0]))
                    elif mode == "recent":
                        result = recent_event_detail(
                            run,
                            timestamp=(parameters.get("at") or [""])[0],
                            kind=(parameters.get("kind") or [""])[0],
                            role=(parameters.get("role") or [""])[0],
                            hint=(parameters.get("hint") or [""])[0])
                    else:
                        raise ValueError("invalid view mode")
                except (OSError, ValueError, OverflowError):
                    self._send(b'{"error":"invalid or unavailable Stage Conversation"}',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self._send((json.dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if path.startswith("/api/run/") and path.endswith("/research-plan/history"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 5 and parts[:2] == ["api", "run"] and parts[3:] == ["research-plan", "history"] else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
                try:
                    if run is None:
                        raise ResearchPlanError("task not found", 404)
                    requested = (parse_qs(self.path.partition("?")[2], keep_blank_values=True).get("version") or [""])[0]
                    if requested and (not requested.isdigit() or len(requested) > 12):
                        raise ResearchPlanError("invalid history version")
                    result, code = history_plan(cache.search_root, run, int(requested) if requested else None), HTTPStatus.OK
                except ResearchPlanError as exc:
                    result, code = {"error": str(exc)}, HTTPStatus(exc.status)
                except (OSError, ValueError):
                    result, code = {"error": "Research Plan 历史暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/research-plan"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "research-plan" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
                try:
                    if run is None:
                        raise ResearchPlanError("task not found", 404)
                    result, code = read_plan(cache.search_root, run), HTTPStatus.OK
                except ResearchPlanError as exc:
                    result, code = {"error": str(exc)}, HTTPStatus(exc.status)
                except (OSError, ValueError):
                    result, code = {"error": "Research Plan 暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/goal"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "goal" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
                if run is None:
                    result, code = {"error": "task not found"}, HTTPStatus.NOT_FOUND
                else:
                    try:
                        result, code = view_goal(run), HTTPStatus.OK
                    except GoalDraftError as exc:
                        result, code = {"error": str(exc), "code": exc.code}, HTTPStatus(exc.status)
                    except (OSError, ValueError):
                        result, code = {"error": "Native Goal 记录暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/cycle-page"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "cycle-page" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None or not run.get("research_root"):
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                from .cycle_history import cycle_summary_page
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                try:
                    page = cycle_summary_page(Path(str(run["research_root"])),
                                              (params.get("before") or [""])[0])
                except ValueError:
                    self._send(b"invalid Cycle cursor\n", "text/plain; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self._send((json.dumps(page, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", cache_control="private, no-store")
                return
            if path.startswith("/api/run/") and path.endswith("/view"):
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "view" else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(ch in "0123456789abcdef" for ch in ident) else None
                if run is None:
                    self._send(b"not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                tab = (params.get("tab") or ["activity"])[0]
                # Keep Explore map availability consistent if a Run cockpit
                # is rebuilt during polling (e.g. a Cycle/layout transition).
                try:
                    vault_root(cache.search_root, ident, state)
                    canvas_available = True
                except ExplorerError:
                    canvas_available = False
                self._send(run_detail_body(run, tab=tab, fragment=True,
                          history_cycle=(params.get("history_cycle") or [""])[0],
                          canvas_available=canvas_available,
                          worker_id=(params.get("worker") or [None])[0],
                          quota_snapshot=quota_history.snapshot(request_update=True)).encode("utf-8"),
                          "text/html; charset=utf-8")
                return
            if path == "/":
                destination = _console_landing(self.headers.get("Cookie"), state)
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", destination)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            remembered = None
            if path in {"/run", "/run/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                body = run_index_body(
                    state,
                    query=(query.get("q") or [""])[0],
                    status=(query.get("status") or [""])[0],
                    page=(query.get("page") or ["1"])[0],
                )
                payload = _page("Sciver Observatory — Task List", body, active="run").encode("utf-8")
            elif path in {"/settings", "/settings/"}:
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected_id = (params.get("observer_id") or [""])[0]
                selected_run = None
                if selected_id:
                    if len(selected_id) != 16 or not all(c in "0123456789abcdef" for c in selected_id):
                        self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                        return
                    selected_run = next((row for row in state.get("runs", [])
                                        if row.get("observer_id") == selected_id), None)
                    if selected_run is None:
                        self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                        return
                payload = _page("Sciver Observatory — Workspace Settings",
                                workspace_settings_body(state.get("runs", []), selected_run),
                                active="settings").encode("utf-8")
            elif path.startswith("/run/"):
                parts = path.strip("/").split("/")
                settings_page = len(parts) == 3 and parts[0] == "run" and parts[2] == "settings"
                supervisor_page = len(parts) == 3 and parts[0] == "run" and parts[2] == "supervisor"
                ident = parts[1] if (len(parts) == 2 and parts[0] == "run") or settings_page or supervisor_page else ""
                run = next((row for row in state.get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(char in "0123456789abcdef" for char in ident) else None
                if run is None:
                    self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                if settings_page:
                    # Existing deep links now navigate to Workspace > Settings.
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/settings/?observer_id=" + ident)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                if supervisor_page:
                    selected_session = (params.get("session") or [""])[0]
                    # Existing SUP-* links include a cycle but no SIS session. Keep them on the
                    # legacy log view; notification-created SIS sessions use the shared chat.
                    use_legacy = ((params.get("legacy") or [""])[0] == "1"
                                  or (bool((params.get("cycle") or [""])[0]) and not selected_session))
                    if use_legacy:
                        from .run_monitor import data as monitor_data
                        from .supervisor_view import page_body as supervisor_page_body
                        try:
                            body = supervisor_page_body(run, (params.get("cycle") or [""])[0], monitor_data(run))
                        except (ValueError, OSError):
                            self._send(b"supervisor session not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                            return
                        payload = _page("Sciver Observatory — Supervisor", body, active="run",
                                        back_href=f"/run/{ident}/", breadcrumb_leaf="Supervisor").encode("utf-8")
                    else:
                        from .research_chat_ui import page_body as shared_chat_page_body
                        from .supervisor_chat import snapshot as supervisor_chat_snapshot
                        from theory_master.supervisor_incident_session import IncidentSessionError
                        try:
                            view = supervisor_chat_snapshot(run, selected_session or None)
                            body = shared_chat_page_body(view, mode="supervisor")
                        except (IncidentSessionError, OSError, ValueError):
                            self._send(b"supervisor incident session not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                            return
                        payload = _page("Sciver Observatory — Supervisor", body, active="research_chat",
                                        back_href=f"/run/{ident}/", breadcrumb_leaf="Supervisor").encode("utf-8")
                    self._send(payload, "text/html; charset=utf-8")
                    return
                try:
                    vault_root(cache.search_root, ident, state)
                    canvas_available = True
                except ExplorerError:
                    canvas_available = False
                tab = (params.get("tab") or ["activity"])[0]
                task_href = f"/run/{ident}/"
                remembered = _console_cookie(ident)
                parent_href = (task_href if tab in {"research", "events", "thread", "artifacts"}
                               else "/run/")
                payload = _page(
                    "Sciver Observatory — Task Console",
                    run_detail_body(run, tab=tab, canvas_available=canvas_available,
                                    history_cycle=(params.get("history_cycle") or [""])[0],
                                    worker_id=(params.get("worker") or [None])[0],
                                    quota_snapshot=quota_history.snapshot(request_update=True)),
                    active="run", back_href=parent_href,
                    breadcrumb_leaf=(
                        "Events" if tab == "events" else
                        "Research" if tab in {"research", "thread", "artifacts"} else None),
                ).encode("utf-8")
            elif path in {"/research-plan", "/research-plan/"}:
                params = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (params.get("observer_id") or [""])[0]
                if not selected:
                    previous = _remembered_console(self.headers.get("Cookie"), state)
                    if previous:
                        selected = previous.strip("/").split("/")[1]
                try:
                    payload = research_plan_html(state, selected).encode("utf-8")
                except ValueError:
                    self._send(b"task not found\n", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
            elif path in {"/research-explorer-tmp-c", "/research-explorer-tmp-c/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                payload = explorer_layout_c_html(cache.search_root, state, selected).encode("utf-8")
            elif path in {"/research-explorer-tmp-b", "/research-explorer-tmp-b/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                payload = explorer_layout_b_html(cache.search_root, state, selected).encode("utf-8")
            elif path in {"/research-explorer-tmp", "/research-explorer-tmp/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                payload = explorer_layout_demo_html(cache.search_root, state, selected).encode("utf-8")
            elif path in {"/research-explorer-tmp-v0", "/research-explorer-tmp-v0/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                payload = explorer_layout_v0_demo_html(cache.search_root, state, selected).encode("utf-8")
            elif path in {"/research-explorer-tmp-a0", "/research-explorer-tmp-a0/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                payload = explorer_layout_a0_demo_html(cache.search_root, state, selected).encode("utf-8")
            elif path in {"/research-explorer", "/research-explorer/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected = (query.get("observer_id") or [""])[0]
                legacy_view = (query.get("view") or [""])[0] == "legacy"
                payload = explorer_html(cache.search_root, state, selected, legacy=legacy_view).encode("utf-8")
            elif path in {"/architecture", "/architecture/"}:
                payload = architecture_html(static_manager).encode("utf-8")
            elif path in {"/workflow", "/workflow/"}:
                # Older Workflow links/bookmarks must resolve to the current page.
                query_string = self.path.partition("?")[2]
                target = "/workflow-stage-preview/" + ("?" + query_string if query_string else "")
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", target)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            elif path in {"/workflow-stage-preview", "/workflow-stage-preview/"}:
                query = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
                selected_request_id = (query.get("request") or [None])[0]
                workflow_version = (query.get("workflow_version") or [None])[0]
                display_mode = (query.get("view") or ["overview"])[0]
                try:
                    page = stage_workflow_html(
                        selected_request_id,
                        field_notes_state_dir=notes_state_dir,
                        workflow_version=workflow_version,
                        display_mode=display_mode,
                    )
                except ValueError as exc:
                    payload = _page("Workflow version not found", f"<h1>404</h1><p>{html.escape(str(exc))}</p>").encode("utf-8")
                    self._send(payload, "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                payload = page.encode("utf-8")
            elif path in {"/services", "/services/"}:
                payload = services_html(
                    state, static_manager,
                    quota_snapshot=quota_history.snapshot(),
                    back_href=_remembered_console(self.headers.get("Cookie"), state),
                ).encode("utf-8")
            else:
                payload = _page("Not found", "<h1>404</h1>").encode("utf-8")
                self._send(payload, "text/html; charset=utf-8", HTTPStatus.NOT_FOUND)
                return
            self._send(payload, "text/html; charset=utf-8", set_cookie=remembered)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/research-chat/"):
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin, host = self.headers.get("Origin"), self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n',
                               "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                # The chat contract allows 4,000 Unicode characters; Chinese/emoji
                # messages can exceed 9 KB in UTF-8 despite respecting that limit.
                if size < 2 or size > 20000:
                    self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                from .research_chat import (create, begin_send, begin_retry, stop as stop_send,
                                             set_model, set_reasoning_effort, rename_session, apply_action)
                try:
                    data = json.loads(self.rfile.read(size).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ResearchChatError("无效 JSON")
                    parts = path.strip("/").split("/")
                    state = cache.get()
                    if state.get("refresh_status") in {"loading", "unavailable"}:
                        self._send(b"Task state is initializing; retry shortly.\n",
                                   "text/plain; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE, retry_after=1)
                        return
                    code = HTTPStatus.OK
                    if parts == ["api", "research-chat", "session"]:
                        result = create(notes_state_dir, state, data)
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "message":
                        result = begin_send(notes_state_dir, cache.search_root, state, parts[2], data)
                        code = HTTPStatus.ACCEPTED
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "retry":
                        result = begin_retry(notes_state_dir, cache.search_root, state, parts[2])
                        code = HTTPStatus.ACCEPTED
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "stop":
                        result = stop_send(notes_state_dir, parts[2])
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "model":
                        result = set_model(notes_state_dir, parts[2], data.get("model"))
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "effort":
                        result = set_reasoning_effort(notes_state_dir, parts[2], data.get("effort"))
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "title":
                        result = rename_session(notes_state_dir, parts[2], data.get("title"))
                    elif len(parts) == 4 and parts[:2] == ["api", "research-chat"] and parts[3] == "action":
                        result = apply_action(notes_state_dir, cache.search_root, state, parts[2], data)
                        cache._at = 0.0
                    else:
                        raise ResearchChatError("API 不存在", 404)
                except ResearchChatError as exc:
                    result, code = {"error": str(exc)}, HTTPStatus(exc.status)
                except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
                    result, code = {"error": "无效 JSON"}, HTTPStatus.BAD_REQUEST
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    result, code = {"error": "Research Chat 服务暂不可用"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/supervisor-session/"):
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n',
                               "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                parts = [item for item in path.strip("/").split("/") if item]
                action = parts[3] if len(parts) == 4 and parts[:2] == ["api", "supervisor-session"] else ""
                session_id = parts[2] if action in {"message", "resolve", "close"} else ""
                if not session_id:
                    self._send(b'{"error":"unknown supervisor incident session"}\n',
                               "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                limit = 9_000_000 if action == "message" else 8000
                if size < 2 or size > limit:
                    self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                from theory_master.supervisor_incident_session import IncidentSessionError
                from .supervisor_incident_ui import finish as finish_incident, submit_human
                try:
                    data = json.loads(self.rfile.read(size).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("invalid payload")
                    runs = cache.get().get("runs", [])
                    if action == "message":
                        content = data.get("content", "")
                        images = data.get("images") or []
                        if not isinstance(content, str) or not isinstance(images, list):
                            raise ValueError("content is required")
                        if not content.strip() and not images:
                            raise ValueError("content is required")
                        result = submit_human(runs, session_id, content, images)
                    else:
                        note = data.get("note") or ""
                        if not isinstance(note, str):
                            raise ValueError("invalid note")
                        result = finish_incident(runs, session_id, "resolve" if action == "resolve" else "close", note)
                    code = HTTPStatus.OK
                except IncidentSessionError as exc:
                    result, code = {"ok": False, "error": str(exc)}, HTTPStatus(exc.status)
                except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
                    result, code = {"ok": False, "error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
                except (OSError, RuntimeError):
                    result, code = {"ok": False, "error": "supervisor incident session unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/research-plan"):
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin, host = self.headers.get("Origin"), self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n',
                               "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(b'{"error":"expected JSON"}\n',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "research-plan" else ""
                run = next((r for r in cache.get().get("runs", []) if r.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}\n',
                               "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                if size < 2 or size > 350_000:
                    self._send(b'{"error":"invalid size"}\n',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    data = json.loads(self.rfile.read(size).decode("utf-8"))
                    result, code = save_section(cache.search_root, run, data), HTTPStatus.OK
                except ResearchPlanError as exc:
                    result, code = {"error": str(exc)}, HTTPStatus(exc.status)
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    result, code = {"error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
                except (OSError, RuntimeError):
                    result, code = {"error": "Research Plan 保存失败"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/goal"):
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "goal" else ""
                run = next((row for row in cache.get().get("runs", []) if row.get("observer_id") == ident), None) if len(ident) == 16 and all(c in "0123456789abcdef" for c in ident) else None
                if run is None:
                    self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                if size < 2 or size > 14000:
                    self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    payload = json.loads(self.rfile.read(size).decode("utf-8"))
                    result, code = save_goal_draft(run, payload), HTTPStatus.OK
                except GoalDraftError as exc:
                    result, code = {"error": str(exc), "code": exc.code}, HTTPStatus(exc.status)
                except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
                    result, code = {"error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
                except (OSError, RuntimeError):
                    result, code = {"error": "Goal 草稿保存失败，请检查记录再重试"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
                return
            if path.startswith("/api/run/") and path.endswith("/action"):
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    self._send(b'{"error":"expected JSON"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                parts = path.strip("/").split("/")
                ident = parts[2] if len(parts) == 4 and parts[:2] == ["api", "run"] and parts[3] == "action" else ""
                if len(ident) != 16 or any(c not in "0123456789abcdef" for c in ident):
                    self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                # Resolve only server-discovered task IDs; never accept file paths.
                run = next((item for item in cache.get().get("runs", []) if item.get("observer_id") == ident), None)
                if run is None:
                    self._send(b'{"error":"task not found"}\n', "application/json; charset=utf-8", HTTPStatus.NOT_FOUND)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                if size < 2 or size > 6000:
                    self._send(b'{"error":"invalid size"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    data = json.loads(self.rfile.read(size).decode("utf-8"))
                    if not isinstance(data, dict) or not set(data).issubset({"action", "value", "request_id", "expected_cycle", "expected_status", "expected_launch_id"}):
                        raise ValueError("invalid fields")
                    result = execute_run_action(run, data)
                    code = HTTPStatus.OK
                except OperatorError as exc:
                    result, code = {"ok": False, "error": str(exc)}, HTTPStatus(exc.status)
                except (UnicodeError, ValueError, json.JSONDecodeError):
                    result, code = {"ok": False, "error": "invalid JSON payload"}, HTTPStatus.BAD_REQUEST
                except (OSError, RuntimeError, TimeoutError):
                    result, code = {"ok": False, "error": "operator backend unavailable; inspect task before retry"}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"), "application/json; charset=utf-8", code)
                return
            if path == "/workflow-field-notes/update":
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
                    self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > 8192:
                    self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    request_payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    self._send(b'{"error":"invalid json"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                if not isinstance(request_payload, dict) or not isinstance(request_payload.get("key"), str) or not isinstance(request_payload.get("description"), str):
                    self._send(b'{"error":"key and description are required"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                key = request_payload["key"]
                try:
                    _synced_field_notes(notes_state_dir)
                    save_override(
                        notes_state_dir,
                        key=key,
                        description=request_payload["description"],
                        known_keys=field_note_keys(),
                    )
                    item = next(entry for entry in field_note_catalog(_synced_field_notes(notes_state_dir)) if entry["key"] == key)
                except (ValueError, OSError, StopIteration) as exc:
                    payload = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
                    self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                payload = (json.dumps({"ok": True, "item": item}, ensure_ascii=False) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/change-requests/create":
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
                    self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > 65536:
                    self._send(change_requests_html("Invalid request size.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
                one = lambda key: (fields.get(key) or [""])[0].strip()
                request_fields = {
                    "title": one("title"),
                    "status": "proposed",
                    "summary": one("summary"),
                    "change_types": _csv_or_lines(one("change_types")),
                    "sdk_design_refs": _csv_or_lines(one("sdk_design_refs")),
                    "problem": {"owner": one("owner"), "stage": one("stage"), "issue": one("issue")},
                    "design_motivation": one("design_motivation"),
                    "current_failure_mode": one("current_failure_mode"),
                    "proposal": one("proposal"),
                    "cost_audit": {"summary": one("cost")},
                    "risk_audit": {"summary": one("risk")},
                    "priority": one("priority"),
                    "affected_flow_nodes": _csv_or_lines(one("affected_flow_nodes")),
                    "affected_files": _csv_or_lines(one("affected_files")),
                    "validation": _csv_or_lines(one("validation")),
                }
                try:
                    created = create_request(request_fields, allowed_flow_nodes=flow_node_ids())
                except (ValueError, OSError) as exc:
                    self._send(change_requests_html(str(exc)).encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", f"/workflow-stage-preview/?request={quote(created['request_id'], safe='')}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/codex-account/reset-credit":
                if auth is None or not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if not origin or not host or origin not in {f"https://{host}", f"http://{host}"}:
                    self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > 1024:
                    self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
                account = (fields.get("account") or [""])[0]
                attempt = (fields.get("idempotency_key") or [""])[0]
                if set(fields) != {"account", "idempotency_key"} or len(fields["account"]) != 1 or len(fields["idempotency_key"]) != 1:
                    self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    outcome = consume_reset_credit(account, attempt)
                except ValueError as exc:
                    payload = (json.dumps({"error": str(exc)}) + "\n").encode("utf-8")
                    self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError):
                    self._send(b'{"error":"Codex reset request failed; check quota before retry"}\n', "application/json; charset=utf-8", HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                payload = (json.dumps({"ok": True, "outcome": outcome}) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if path == "/services-selection/switch":
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin, host = self.headers.get("Origin"), self.headers.get("Host")
                if (not origin or not host or origin not in {f"https://{host}", f"http://{host}"}
                        or self.headers.get("X-TM-Operator-Intent") != "1"):
                    self._send(b'{"error":"origin or operator intent rejected"}\n',
                               "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                    self._send(b'{"error":"invalid content type"}\n',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= 1024:
                    self._send(b'{"error":"invalid request length"}\n',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
                if set(fields) != {"account"} or len(fields["account"]) != 1:
                    self._send(b'{"error":"invalid request"}\n',
                               "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                from theory_master.service_provider_switch import SelectionConflict, switch_service_provider
                try:
                    result = switch_service_provider(fields["account"][0])
                    status = HTTPStatus.OK
                except ValueError as exc:
                    result, status = {"error": str(exc)}, HTTPStatus.BAD_REQUEST
                except SelectionConflict as exc:
                    result, status = {"error": str(exc)}, HTTPStatus.CONFLICT
                except (OSError, RuntimeError) as exc:
                    result, status = {"error": "切换未完成：" + type(exc).__name__}, HTTPStatus.SERVICE_UNAVAILABLE
                self._send((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"),
                           "application/json; charset=utf-8", status)
                return
            if path == "/codex-account/switch":
                if not self._authorized():
                    self._send_unauthorized()
                    return
                origin = self.headers.get("Origin")
                host = self.headers.get("Host")
                if origin and host and origin not in {f"https://{host}", f"http://{host}"}:
                    self._send(b'{"error":"origin rejected"}\n', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > 1024:
                    self._send(b'{"error":"invalid request"}\n', "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
                account = (fields.get("account") or [""])[0]
                try:
                    selected = switch_active_account(account)
                except AccountSwitchConflict as exc:
                    payload = (json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n").encode("utf-8")
                    self._send(payload, "application/json; charset=utf-8", HTTPStatus.CONFLICT)
                    return
                except (ValueError, OSError) as exc:
                    payload = (json.dumps({"error": str(exc)}) + "\n").encode("utf-8")
                    self._send(payload, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)
                    return
                payload = (json.dumps({"ok": True, "active_account": selected}) + "\n").encode("utf-8")
                self._send(payload, "application/json; charset=utf-8")
                return
            if auth is None or path != "/login":
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 8192:
                self._send(login_html("Invalid login request.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.BAD_REQUEST)
                return
            body = self.rfile.read(length).decode("utf-8", "replace")
            fields = parse_qs(body, keep_blank_values=True)
            username = (fields.get("username") or [""])[0]
            password = (fields.get("password") or [""])[0]
            account = match_account(auth, username, password)
            if account is None:
                self._send(login_html("Incorrect username or password.").encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.UNAUTHORIZED)
                return
            token = issue_session_token(account)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_S}; HttpOnly; Secure; SameSite=Strict",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_PUT(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        do_PATCH = do_PUT
        do_DELETE = do_PUT

        def handle_one_request(self) -> None:
            self._tm_request_started = time.monotonic()
            self._tm_response_status = 0
            try:
                super().handle_one_request()
            finally:
                elapsed_ms = (time.monotonic() - self._tm_request_started) * 1000.0
                status = int(getattr(self, "_tm_response_status", 0) or 0)
                if elapsed_ms >= 250.0 or status >= 400:
                    raw_path = (getattr(self, "path", "") or "-").split("?", 1)[0]
                    parts = raw_path.strip("/").split("/") if raw_path != "-" else []
                    if len(parts) >= 4 and parts[:3] == ["api", "canvas-md", "tasks"]:
                        route = "/api/canvas-md/tasks/:task/" + (parts[4] if len(parts) >= 5 else "root")
                    elif len(parts) >= 3 and parts[:2] in (["api", "research-chat"], ["api", "supervisor-session"]):
                        route = "/" + "/".join(parts[:2]) + "/:session/" + (parts[3] if len(parts) >= 4 else "root")
                    elif len(parts) >= 2 and parts[0] == "run":
                        route = "/run/:task/" + (parts[2] if len(parts) > 2 else "")
                    elif len(parts) >= 2 and parts[0] == "supervisor-notification":
                        route = "/supervisor-notification/:id"
                    else:
                        route = raw_path[:120]
                    record = {"event": "tm_http_slow_request" if elapsed_ms >= 250.0 else "tm_http_error",
                              "method": getattr(self, "command", "-"), "route": route,
                              "status": status, "duration_ms": round(elapsed_ms, 1)}
                    try:
                        sys.stderr.write("TM_HTTP " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                        sys.stderr.flush()
                    except OSError:
                        pass

        def send_response(self, code: int, message: str | None = None) -> None:
            self._tm_response_status = int(code)
            super().send_response(code, message)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def serve(
    search_root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    cache_ttl: float = 5.0,
    *,
    state_dir: Path | None = None,
    archify_root: Path | None = None,
    static_refresh: float = 30.0,
    auth_file: Path | None = None,
) -> None:
    search_root = search_root.resolve()
    auth = load_auth_file(auth_file) if auth_file is not None else None
    cache = StateCache(search_root, cache_ttl)
    manager = StaticViewManager(
        search_root,
        state_dir or default_state_dir(),
        archify_root=archify_root or default_archify_root(),
        interval_s=static_refresh,
    )
    manager.start()
    server = ThreadingHTTPServer((host, int(port)), make_handler(cache, manager, auth))
    from theory_master.provider_extensions import optional_account_quota_plugin
    monitor = optional_account_quota_plugin()
    monitor_stop = threading.Event()
    if monitor is not None:
        # Optional local account monitor; no network polling in public wheels.
        threading.Thread(target=monitor.watch, kwargs={"stop": monitor_stop},
                         name="tm-optional-provider-quota", daemon=True).start()
    try:
        server.serve_forever()
    finally:
        monitor_stop.set()
        server.server_close()
        manager.stop()
