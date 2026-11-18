"""Server-side page composition, independent of HTTP routing."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

from .archify_views import StaticViewManager
from .change_requests import load_change_requests
from .current_flow import current_flow_body, flow_node_ids
from .field_notes import sync_machine_defaults
from .sequence_views import SequenceBuildResult
from .stage_flow import field_note_catalog, stage_workflow_body, stage_workflow_guide_body
from .agent_workflow_architecture import agent_workflow_architecture_body
from .onboarding_ui import first_use_panel
from .services_worker_accounts import section_html as worker_account_section_html
from .navigation_model import task_console_url


from .dashboard_ui import dashboard_shell


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


def _workflow_unavailable_html(title: str, error: Exception, *, active: str) -> str:
    return _page(
        title,
        '<h1>' + html.escape(title) + '</h1>'
        '<div class="card warning" role="alert">无法读取当前工作流的源文件。'
        '请检查 Sciver 安装是否完整，重新安装后刷新页面。</div>'
        '<details><summary>错误详情</summary><pre>' + html.escape(str(error)) + '</pre></details>'
        '<p><a href="/">返回 Task Console</a> · <a href="/services/">查看 Services</a></p>',
        active=active,
    )


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
    include_change_requests: bool = True,
) -> str:
    loaded = load_change_requests(allowed_flow_nodes=flow_node_ids()) if include_change_requests else None
    requests = _tm_system_requests(loaded.requests) if loaded is not None else ()
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
    if loaded is not None and loaded.warnings:
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
                 codex_usage_body(quota_snapshot)
                 + '<details class="card"><summary>首次配置与账号登录步骤</summary>'
                 + first_use_panel() + '</details>', active="services",
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
    const login = account ? '<p class="small">首次使用或登录过期时，在运行 Sciver 的终端执行 <code>sciver codex-account login-' +
      (account === 'account-1' ? '1' : '2') + '</code>，再执行 <code>sciver codex-account status</code>，完成后点击“立即刷新”。</p>' : '';
    return base + '<p class="warning" role="alert">额度查询失败：' + escapeHtml(a.error || '未知错误') + '</p>' + login + '</div>';
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
    if (!validCodexAccounts.length && !hasCodexSnapshot) {
      target.innerHTML = accounts.length
        ? accounts.map(a => accountCard(a, selectedService?.active_account || payload.active_account, lastUsageCached)).join('')
        : '<div class="card">尚未配置可用账号，请展开下方“首次配置与账号登录步骤”，完成登录后点击“立即刷新”。</div>';
      if (sync) sync.textContent = '· 尚无可验证额度，请完成登录或重试';
      return;
    }
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
