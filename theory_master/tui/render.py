"""Pure-text dashboard rendering. Textual widgets only display these strings."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from theory_master.runtime import compact_ts
from theory_master.investigator_budget import format_k_tokens
from theory_master.observability.models import DISPLAY_ROLE, ROLES, investigation_progress

STATE_MARK = {
    "idle": "o idle",
    "waiting": "o waiting",
    "running": "* running",
    "rework": "R rework",
    "done": "v done",
    "blocked": "! blocked",
    "failed": "x failed",
    "paused": "|| paused",
}


def _fmt_tokens(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_time(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        return "-"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


_MODEL_LABELS = {
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "deepseek-flash": "DeepSeek Flash",
}
_MODEL_COLUMN = 24


def _fmt_model(value: Any, width: int = 16, effort: Any = None) -> str:
    text = str(value or "-").strip()
    label = _MODEL_LABELS.get(text.lower(), text)
    effort_text = str(effort or "").strip().lower()
    if effort_text and effort_text not in {"-", "none", "null"}:
        label = f"{label} · {effort_text}"
    return _clip(label, width)


def _fmt_money_amount(amount: Any) -> str:
    value = float(amount)
    absolute = abs(value)
    if 0 < absolute < 0.01:
        return f"{value:.4f}"
    if absolute < 1:
        return f"{value:.3f}"
    return f"{value:.2f}"


def _fmt_compact_cost(value: Any) -> str:
    cost = value if isinstance(value, dict) else {}
    amount, currency = cost.get("amount"), cost.get("currency")
    if amount is None or not currency:
        return "—"
    symbol = {"CNY": "¥", "USD": "$"}.get(str(currency).upper(), f"{currency} ")
    prefix = "~" if str(cost.get("source") or "") == "estimated" else ""
    return f"{prefix}{symbol}{_fmt_money_amount(amount)}"


def _fmt_cost(value: Any) -> str:
    cost = value if isinstance(value, dict) else {}
    amount, currency, source = cost.get("amount"), cost.get("currency"), str(cost.get("source") or "unavailable")
    if amount is None or not currency:
        return "—"
    symbol = {"CNY": "¥", "USD": "$"}.get(str(currency).upper(), f"{currency} ")
    prefix = "~" if source == "estimated" else ""
    label = {"provider_reported": "provider", "provider_billing_api": "billing", "calculated": "calculated", "estimated": "estimated"}.get(source, source)
    return f"{prefix}{symbol}{_fmt_money_amount(amount)} {label}"


def _fmt_metric_cost(value: Any, tokens: Any, *, detailed: bool = False) -> str:
    """Show monetary cost when available, otherwise expose observed token usage."""
    cost = value if isinstance(value, dict) else {}
    amount, currency = cost.get("amount"), cost.get("currency")
    if amount is not None and currency:
        if detailed:
            return _fmt_cost(value)
        symbol = {"CNY": "¥", "USD": "$"}.get(str(currency).upper(), f"{currency} ")
        prefix = "~" if str(cost.get("source") or "") == "estimated" else ""
        return f"{prefix}{symbol}{_fmt_money_amount(amount)}"
    if tokens is not None:
        token_text = _fmt_tokens(tokens)
        return f"— · Tokens {token_text}" if detailed else f"— · {token_text} tok"
    return "—"


def _project_metric(state: dict[str, Any], cost_key: str = "project_cost", token_key: str = "project_tokens_total") -> tuple[Any, Any]:
    project_cost = state.get(cost_key)
    project_tokens = state.get(token_key)
    if not isinstance(project_cost, dict) or project_cost.get("amount") is None or not project_cost.get("currency"):
        project_cost = state.get("cost")
    if project_tokens is None:
        project_tokens = state.get("tokens_total")
    return project_cost, project_tokens


def _agent(state: dict[str, Any], role: str) -> dict[str, Any]:
    return (state.get("agents") or {}).get(role) or {}


def _resume_command(state: dict[str, Any]) -> str:
    master_id = state.get("master_id", state.get("master_run_id"))
    if state.get("task_root") and master_id is not None:
        return f"TheoryMaster resume {master_id}"
    cycle_id = str(state.get("cycle_id") or state.get("run_id") or "").strip()
    if cycle_id:
        return f"theory-master resume {cycle_id}"
    return "resume this Cycle"


def _attention_fields(state: dict[str, Any]) -> tuple[str, str, str]:
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    reason = str(attention.get("reason") or state.get("human_attention") or state.get("qualification_rationale") or "Human attention is required.")
    action = str(attention.get("requested_action") or "Review the request and decide whether to resume.")
    resume = str(attention.get("resume_behavior") or "Resume continues from the persisted safe checkpoint.")
    return reason, action, resume


def render_attention(state: dict[str, Any]) -> str:
    status = str(state.get("status") or "").upper()
    if status != "NEEDS_ATTENTION" and not state.get("human_attention") and not state.get("attention"):
        return "(none)"
    reason, action, resume = _attention_fields(state)
    lines = [
        f"Reason\n{reason}",
        "",
        f"Requested action\n{action}",
        "",
        f"Resume behavior\n{resume}",
    ]
    takeover = state.get("attention_takeover_handoff") if isinstance(state.get("attention_takeover_handoff"), dict) else {}
    if takeover and takeover.get("status") not in {None, "", "disabled"}:
        lines.extend(["", f"AI takeover\nstatus={takeover.get('status') or '-'}  target={takeover.get('target') or '-'}"])
    guidance = state.get("attention_guidance") if isinstance(state.get("attention_guidance"), dict) else {}
    if guidance:
        lines.extend(["", f"Operator guidance\nstatus={guidance.get('status') or '-'}  source={guidance.get('source') or '-'}"])
    lines.extend(["", f"Resume command\n{_resume_command(state)}"])
    return "\n".join(lines)


def render_header(state: dict[str, Any]) -> str:
    # cycle_index is an invocation ordinal, NOT the task-global cycle_id.
    # Avoid turning an unavailable ordinal into an invented Cycle 0.
    raw_cycle = state.get("cycle_index")
    cycle = raw_cycle if type(raw_cycle) is int and raw_cycle >= 1 else "—"
    max_cycles = state.get("max_cycles") or 1
    project_cost, project_tokens = _project_metric(state)
    status = str(state.get("status") or "-").upper()
    # A supervisor/managed-runtime heartbeat can coexist with a paused
    # Cycle (e.g. while awaiting provider quota). It does not prove an
    # active scientific invocation. Prefer the workflow lifecycle status.
    if status in {"PAUSED", "INTERRUPTED"}:
        process = status
    elif status in {"FAILED", "FAILED_RECOVERY", "NEEDS_ATTENTION",
                    "OVER", "COMPLETE", "COMPLETED", "CANCELLED",
                    "STOPPED", "ORPHANED", "UNRESPONSIVE"}:
        process = "STOPPED"
    else:
        process = "RUNNING" if state.get("execution_active") else "STOPPED"
    state_label = status.replace("_", " ")
    lines = [
        f"Process {process:<8}  State {state_label:<17}",
        f"Cycle {cycle} / {max_cycles:<4}  Phase {_clip(state.get('phase') or '-', 25):<25}  Elapsed {_fmt_time(state.get('project_elapsed_s', state.get('elapsed_s'))):>5}",
        f"Started  {_fmt_stamp(state.get('total_started_at') or state.get('started_at'))}  Cycle start {_fmt_stamp(state.get('cycle_started_at') or state.get('started_at'))}  Cost {_fmt_metric_cost(project_cost, project_tokens, detailed=True)}",
    ]
    if status == "NEEDS_ATTENTION":
        reason, action, resume = _attention_fields(state)
        lines.extend([
            "",
            "NEEDS ATTENTION",
            f"Reason  {_clip(reason, 76)}",
            f"Action  {_clip(action, 76)}",
            f"Resume  {_clip(resume, 76)}",
            f"Command {_clip(_resume_command(state), 76)}",
            f"Takeover {_clip((state.get('attention_takeover_handoff') or {}).get('status') or ('enabled' if state.get('attention_takeover_enabled') else 'disabled'), 76)}",
        ])
    elif (
        not state.get("execution_active")
        and state.get("resumable")
        and status in {"CHECKPOINT_REACHED", "PAUSED", "RECOVERABLE", "FAILED", "INTERRUPTED", "UNRESPONSIVE", "ORPHANED"}
    ):
        lines.append(f"Recovery {state.get('recovery_phase') or state.get('phase') or 'available'}")
        if state.get("last_safe_checkpoint"):
            lines.append(f"Checkpoint {_clip(state.get('last_safe_checkpoint'), 72)}")
    quota = state.get("quota_auto_adjust") if isinstance(state.get("quota_auto_adjust"), dict) else {}
    quota_status = str(quota.get("status") or "").strip()
    if quota_status not in {"", "idle", "disabled"}:
        selected = quota.get("selected_account") or "-"
        failed = quota.get("failed_account") or "-"
        lines.append(f"Quota auto {quota_status}  selected={selected}  failed={failed}")
    return "\n".join(lines)


def quota_waiting_active(state: dict[str, Any]) -> bool:
    """True only for Supervisor-controlled automatic quota refresh waiting."""
    quota = state.get("quota_auto_adjust")
    return bool(state.get("quota_auto_adjust_enabled")) and isinstance(quota, dict) and quota.get("status") == "waiting_refresh"


def _quota_reset_label(value: Any) -> str:
    if value is None or value == "":
        return "未知"
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).astimezone().strftime("%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return "未知"
    return str(value)


def _quota_window_label(label: str, window: Any) -> str:
    if not isinstance(window, dict):
        return f"{label}: 数据不可用"
    remaining = window.get("remaining_percent")
    if not isinstance(remaining, (int, float)):
        used = window.get("used_percent")
        if isinstance(used, (int, float)):
            remaining = max(0.0, min(100.0, 100.0 - float(used)))
    pct = f"{float(remaining):g}%" if isinstance(remaining, (int, float)) else "未知"
    period = window.get("window_duration_mins")
    period_text = f"（窗口 {period} 分钟）" if isinstance(period, (int, float)) and period > 0 else ""
    return f"{label}{period_text}: 剩余 {pct}；重置 {_quota_reset_label(window.get('resets_at'))}"


def render_quota_wait_popup(state: dict[str, Any]) -> str:
    """Present only persisted quota probe snapshots; never fetch accounts or auth."""
    quota = state.get("quota_auto_adjust")
    if not isinstance(quota, dict):
        quota = {}
    selected = str(quota.get("selected_account") or "-")
    failed = str(quota.get("failed_account") or "-")
    accounts = quota.get("accounts") if isinstance(quota.get("accounts"), dict) else {}
    exhausted = quota.get("exhausted_accounts") if isinstance(quota.get("exhausted_accounts"), dict) else {}
    rows = [
        "额度自调已进入等待：目前没有找到可安全继续的 Codex 账号。",
        f"当前选中账号: account-{selected}" if selected in {"1", "2"} else "当前选中账号: 未知",
        f"最近触发额度暂停: account-{failed}" if failed in {"1", "2"} else "最近触发额度暂停: 未知",
        "",
        "以下是 Supervisor 上次查询的额度快照（不是实时保证）：",
    ]
    for number in ("1", "2"):
        snapshot = accounts.get(number)
        name = f"account-{number}"
        rows.append("")
        rows.append(f"{name}{'  ← 当前选中' if selected == number else ''}")
        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            rows.append("  查询状态: 不可用或尚未取得快照")
            if isinstance(snapshot, dict) and snapshot.get("error"):
                error = " ".join(str(snapshot["error"]).split())
                rows.append(f"  诊断: {error[:140]}")
        else:
            ordinary = snapshot.get("ordinary_usage_allowed")
            reached = snapshot.get("rate_limit_reached_type")
            exhausted_flag = number in exhausted or ordinary is False or reached not in (None, "", "none", "None")
            rows.append(f"  可用性: {'已耗尽或已受限' if exhausted_flag else '尚未报告用尽（仍在等待核实）'}")
            rows.append("  " + _quota_window_label("短周期", snapshot.get("primary")))
            rows.append("  " + _quota_window_label("长周期", snapshot.get("secondary")))
            queried = snapshot.get("queried_at")
            if queried is not None:
                rows.append("  查询时间: " + _quota_reset_label(queried))
    interval = quota.get("next_check_in_s")
    if isinstance(interval, (int, float)) and interval > 0:
        rows.extend(["", f"预计每 {interval:g} 秒重新检查一次（非倒计时）。"])
    rows.append("Supervisor 将自动检查可用额度并恢复原任务；无需重复手动 resume。")
    return "\n".join(rows)


def render_identity(state: dict[str, Any]) -> str:
    master_id = state.get("master_id", state.get("master_run_id"))
    supervisor_on = state.get("supervisor_enabled")
    if supervisor_on is None:
        supervisor_on = state.get("attention_takeover_enabled")
    supervisor = "开启" if supervisor_on else "关闭"
    cycle_audit = "开启" if state.get("cycle_audit_actions_enabled") else "关闭"
    quota_auto = "开启" if state.get("quota_auto_adjust_enabled") else "关闭"
    job = state.get('supervisor_recovery') or {}
    recovery = ("\n恢复: " + ("执行中" if job.get('executing') else str(job.get('status') or '无任务'))
                + " · 负责人 " + str(job.get('recovery_owner') or '-')
                + " · 下一动作 " + str(job.get('current_action') or '-')
                + " · 唤醒 " + str(job.get('next_wakeup_epoch') or '-')
                + " · 等待 " + str(job.get('blocking_dependency') or '-')
                + " · 最后验证 " + str(job.get('last_verified_progress') or '-')) if job else ""
    return (
        f"Task ID {master_id if master_id is not None else '-'}    "
        f"Cycle ID {state.get('cycle_id') or '-'}    "
        f"Supervisor: {supervisor}    自动审计: {cycle_audit}    额度自调: {quota_auto}" + recovery
    )


def render_research(state: dict[str, Any]) -> str:
    """Secondary research view: scientific content stays out of the cockpit."""
    return "\n\n".join([
        "CHALLENGE",
        render_challenges(state),
        "CURRENT HYPOTHESIS",
        render_hypothesis(state, full=True),
    ])


def _fmt_stamp(value: Any) -> str:
    if not value:
        return "-"
    text = str(value)
    return compact_ts(text) if "T" in text else text


def _clip(text: Any, width: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)] + "…"


def render_hypothesis(state: dict[str, Any], *, full: bool = False) -> str:
    title = str(state.get("hypothesis_title") or "").strip()
    body = str(state.get("hypothesis") or "").strip()
    if full:
        if title and body and title != body:
            return f"{title}\n{body}"
        return body or title or "(no hypothesis yet)"
    history = state.get("hypothesis_history") or []
    if not title and not body and not history:
        return "(no hypothesis yet)"
    lines = []
    if title:
        lines.append(title)
    if body and body != title:
        lines.append(body)
    if history:
        lines.extend(["", "Previous"])
        for item in history:
            if isinstance(item, dict):
                stamp = compact_ts(item.get("ts")) or str(item.get("ts") or "")
                heading = str(item.get("title") or "")
                text = str(item.get("body") or "")
            else:
                stamp, heading, text = "", str(item), ""
            lines.append(f"{stamp}  {heading}".strip())
            if text:
                lines.append(text)
            lines.append("")
    lines.append("[h] full current hypothesis")
    return "\n".join(lines)


def render_workflow(state: dict[str, Any]) -> str:
    # The cockpit should expose role-to-model routing at a glance. Provider,
    # thread, raw usage, and other billing detail remain in secondary panes.
    lines = [
        f"{'AGENT/STAGE':<13}  {'MODEL':<{_MODEL_COLUMN}}  {'COST':>8}  {'TIME':>8}  {'STATE':>8}"
    ]
    project_agents = state.get("project_agents") or {}
    for role in ROLES:
        total = project_agents.get(role) or _agent(state, role)
        task = _agent(state, role)
        name = DISPLAY_ROLE.get(role, role)
        model = _fmt_model(
            task.get("model") or total.get("model"),
            _MODEL_COLUMN,
            task.get("reasoning_effort") or total.get("reasoning_effort"),
        )
        status = str(task.get("state") or "-")
        lines.append(
            f"{name:<13}  {model:<{_MODEL_COLUMN}}  {_fmt_compact_cost(total.get('cost')):>8}  "
            f"{_fmt_time(total.get('elapsed_s')):>8}  {status:>8}"
        )
    return "\n".join(lines)


def render_investigation(state: dict[str, Any]) -> str:
    rounds = state.get("investigation_rounds") or []
    progress = investigation_progress(state)
    current = "-"
    running = [item for item in rounds if item.get("state") == "running"]
    if running:
        executor = running[-1].get("executor_role") or running[-1].get("role")
        current = DISPLAY_ROLE.get(executor, executor)
    elif state.get("current_agent") in {"investigator", "literature"}:
        current = DISPLAY_ROLE.get(state["current_agent"], state["current_agent"])
    lines = [
        f"Attempt          {progress['investigation_attempt']}",
        f"Round            {progress['investigation_round_count']} (history; no round cap)",
        f"Total rounds     {progress['total_investigation_round_count']} (history)",
        f"Inv tokens       {format_k_tokens(((state.get('agents') or {}).get('investigator') or {}).get('tokens_total') or 0)} / {format_k_tokens((state.get('investigator_budget') or {}).get('allowance_tokens')) if (state.get('investigator_budget') or {}).get('allowance_tokens') is not None else 'review at budget boundary'}",
        f"Current executor {current}",
        f"Judge reworks    {progress['judge_rework_count']} / {state.get('max_judge_reworks_per_cycle', 3)}",
        "",
        "History",
    ]
    if not rounds:
        lines.append("(none)")
    for item in rounds:
        mark = "v" if item.get("state") == "done" else "*" if item.get("state") == "running" else "o"
        executor = item.get("executor_role") or item.get("role")
        role = DISPLAY_ROLE.get(executor, executor)
        attempt = f"A{item['investigation_attempt']} " if item.get("investigation_attempt") else ""
        lines.append(f"{attempt}R{item.get('round')} {role:<12} {mark} {item.get('state')}")
    readiness = state.get("investigation_readiness") or {}
    if readiness:
        lines.extend(["", "Router readiness", str(readiness)])
    counts: dict[str, int] = {}
    for item in rounds:
        role = str(item.get("executor_role") or item.get("role") or "")
        counts[role] = counts.get(role, 0) + 1
    lines.append("")
    lines.append("Executor rounds")
    for role in ("investigator", "literature"):
        lines.append(f"{DISPLAY_ROLE[role]:<12} {counts.get(role, 0)}")
    return "\n".join(lines)


def render_challenges(state: dict[str, Any]) -> str:
    qualification = str(state.get("qualification_status") or "pending")
    lock = state.get("hypothesis_lock") if isinstance(state.get("hypothesis_lock"), dict) else {}
    locked = bool(state.get("hypothesis_locked") or lock.get("status") == "locked")
    rationale = state.get("qualification_rationale") or lock.get("qualification_rationale")
    lines = [
        "Hypothesis Qualification",
        f"Status  {qualification}",
        f"Rationale  {_clip(rationale, 68) if rationale else '-'}",
        "",
        f"Hypothesis Lock  {'locked' if locked else 'pending'}",
        f"Next executor  {state.get('next_executor') or state.get('initial_executor') or '-'}",
    ]

    latest_gap = {}
    attempts = state.get("investigation_attempts") if isinstance(state.get("investigation_attempts"), dict) else {}
    for attempt in reversed(list(attempts.values())):
        outputs = attempt.get("outputs") if isinstance(attempt, dict) and isinstance(attempt.get("outputs"), list) else []
        if outputs and isinstance(outputs[-1], dict):
            closed = outputs[-1].get("output") if isinstance(outputs[-1].get("output"), dict) else {}
            gap = closed.get("remaining_gap") if isinstance(closed.get("remaining_gap"), dict) else {}
            if gap:
                latest_gap = gap
                break
    if latest_gap:
        lines.append(f"Current gap  {_clip(latest_gap.get('description') or latest_gap.get('kind'), 68)}")
    return "\n".join(lines)


def _challenge_parts(key: Any) -> tuple[str, str]:
    text = " ".join(str(key or "").split())
    match = re.match(r"^(c\d+)\b\s*(.*)$", text, re.I)
    if match:
        ident = match.group(1).lower()
        rest = match.group(2)
        title = rest.split("：", 1)[0].split(":", 1)[0].strip() or ident
        return ident, title
    return _clip(text, 12) or "c?", text


def render_judge(state: dict[str, Any]) -> str:
    agent = _agent(state, "judge")
    progress = investigation_progress(state)
    return "\n".join([
        "Branch Judge (local milestone)",
        f"Status:            {agent.get('state') or 'idle'}",
        f"Reworks:           {progress['judge_rework_count']} / {state.get('max_judge_reworks_per_cycle', 3)}",
        f"Previous verdict:  {state.get('last_verdict') or '-'}",
        f"Suggested start:   {state.get('last_target') or '-'}",
        f"Reason:            {state.get('last_missing') or '-'}",
        f"Objections:        {state.get('judge_objections') or '(none)'}",
    ])


def _activity_tool_line(state: dict[str, Any], agent: dict[str, Any], role: Any) -> str:
    tool = state.get("current_tool") or agent.get("current_tool")
    activity = state.get("current_activity") or agent.get("activity") or "idle"
    failed = str(state.get("status") or "").upper() in {"FAILED", "INTERRUPTED"}
    if failed:
        return tool or str(state.get("last_event") or "-")
    if not tool and role and agent.get("state") in {"running", "rework"}:
        return activity or "Agent reasoning / generating response"
    return tool or activity or "-"


def render_activity(state: dict[str, Any]) -> str:
    role = state.get("current_agent")
    agent = _agent(state, role) if role else {}
    output = (state.get("role_outputs") or {}).get(role) if role else None
    if isinstance(output, dict):
        output = output.get("summary") or output.get("title") or output.get("decision") or output.get("verdict")
    phase = state.get("phase") or "-"
    status = agent.get("state") or state.get("status") or "idle"
    return "\n".join([
        f"Agent       {DISPLAY_ROLE.get(role, role or '-')}",
        f"State       {status}   | phase: {_clip(phase, 34)}",
        f"Purpose     {_clip(state.get('current_activity') or agent.get('activity') or 'waiting for work', 58)}",
        f"Last action {_clip(agent.get('last_event') or _activity_tool_line(state, agent, role), 58)}",
        f"Output      {_clip(output or 'not delivered yet', 58)}",
        f"Handoff     {_clip(_handoff_for(role, state), 58)}",
        f"Elapsed     {_fmt_time(agent.get('elapsed_s'))}   turns: {agent.get('turn_count') or 0}",
    ])


def _handoff_for(role: Any, state: dict[str, Any]) -> str:
    timeline = state.get("agent_timeline") or []
    for item in reversed(timeline):
        if item.get("role") == role:
            target = item.get("handoff_to")
            if target:
                return f"completed -> {DISPLAY_ROLE.get(target, target)}"
            if item.get("active"):
                return "in progress; result will feed the next stage"
    return "no handoff recorded"


def render_error(state: dict[str, Any]) -> str:
    """Render the current terminal workflow error, if any."""
    status = str(state.get("status") or "").upper()
    if status not in {"FAILED", "INTERRUPTED"}:
        return "(no current error)"
    detail = state.get("current_activity") or state.get("last_event") or status.lower()
    return f"{status}: {detail}"


def render_last(state: dict[str, Any], *, full_actions: bool = False) -> str:
    """Render workflow milestones and Agent/handoff stages as one timeline.

    High-frequency tool/turn activity is intentionally excluded here and shown by
    ``render_last_action`` so the history stays readable while the bottom action
    line can refresh live.
    """
    timeline = state.get("agent_timeline") or []
    actions = state.get("action_history") or []
    if not timeline and not actions:
        return "(none)"

    entries: list[tuple[dict[str, Any], str]] = []
    # Web retains every bounded action so earlier Cycle-start boundaries are
    # available for grouping; the compact terminal keeps its 24-action limit.
    for item in actions[-(100 if full_actions else 24):]:
        message = item.get("text") or item.get("event") or "-"
        entries.append((item, str(message) if full_actions else _clip(message, 72)))
    for item in timeline[-100:]:
        role = DISPLAY_ROLE.get(item.get("role"), item.get("role") or "-")
        handoff = item.get("handoff_to")
        suffix = f" -> {DISPLAY_ROLE.get(handoff, handoff)}" if handoff else ""
        state_label = "running" if item.get("active") else "done"
        entries.append((
            {"at": item.get("started_at")},
            f"{role:<16} {state_label:<7} {_fmt_time(item.get('elapsed_s'))}{suffix}",
        ))

    ordered = sorted(
        enumerate(entries),
        key=lambda indexed: _event_sort_key((indexed[0], indexed[1][0])),
    )
    return "\n".join(
        f"{_event_clock(event)}  {line}"
        for _, (event, line) in ordered
    )


def render_last_action(state: dict[str, Any]) -> str:
    """Pinned live action line for the LAST pane."""
    action = state.get("last_action") if isinstance(state.get("last_action"), dict) else None
    if not action:
        return "Last action  (none)"
    clock = _event_clock(action)
    text = _clip(action.get("text") or action.get("event") or "-", 88)
    return f"Last action  {clock}  {text}"


def _fmt_last_line(line: Any) -> str:
    text = str(line or "").rstrip()
    if not text:
        return text
    match = _LOG_LINE.match(text)
    if match:
        stamp, _level, message = match.group(1), match.group(2), match.group(3)
        return f"{stamp}  {message}"
    if re.match(r"^\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\b", text):
        return text
    return f"--:--:--  {text}"


def render_tokens(state: dict[str, Any]) -> str:
    project_cost, project_tokens = _project_metric(state)
    turn_usage = state.get("current_turn_usage") if isinstance(state.get("current_turn_usage"), dict) else {}
    turn_input = int(turn_usage.get("input_tokens") or 0)
    turn_cached = min(turn_input, int(turn_usage.get("cached_input_tokens") or 0))
    turn_uncached = max(0, turn_input - turn_cached)
    lines = [
        "CURRENT TURN",
        f"Input        {_fmt_tokens(turn_usage.get('input_tokens'))}",
        f"  Cached     {_fmt_tokens(turn_cached) if turn_usage.get('input_tokens') is not None else 'n/a'}" + (f"  {turn_cached / turn_input:.1%}" if turn_input else ""),
        f"  Uncached   {_fmt_tokens(turn_uncached) if turn_usage.get('input_tokens') is not None else 'n/a'}",
        f"Output       {_fmt_tokens(turn_usage.get('output_tokens'))}",
        f"Reasoning    {_fmt_tokens(turn_usage.get('reasoning_tokens'))}",
        "",
        "CYCLE USAGE", "",
        f"Time         {_fmt_time(state.get('cycle_elapsed_s', state.get('elapsed_s')))}",
        f"Cost         {_fmt_metric_cost(state.get('cost'), state.get('tokens_total'))}",
        f"Tokens       {_fmt_tokens(state.get('tokens_total'))}", "",
    ]
    for role in ROLES:
        agent = _agent(state, role)
        lines.append(f"{DISPLAY_ROLE[role]:<12} {_fmt_tokens(agent.get('tokens_total'))}")
    lines.append("-" * 24)
    lines.append(f"{'Cycle total':<12} {_fmt_tokens(state.get('tokens_total'))}")
    lines.append(f"Input        {_fmt_tokens(state.get('tokens_in'))}")
    cycle_input = int(state.get("tokens_in") or 0)
    cycle_cached = min(cycle_input, int(state.get("cached_input_tokens") or 0))
    cycle_uncached = max(0, cycle_input - cycle_cached)
    lines.append(f"  Cached     {_fmt_tokens(cycle_cached) if state.get('tokens_in') is not None else 'n/a'}" + (f"  {cycle_cached / cycle_input:.1%}" if cycle_input else ""))
    lines.append(f"  Uncached   {_fmt_tokens(cycle_uncached) if state.get('tokens_in') is not None else 'n/a'}")
    lines.append(f"Output       {_fmt_tokens(state.get('tokens_out'))}")
    lines.append(f"Reasoning    {_fmt_tokens(state.get('reasoning_tokens'))}")
    lines.extend([
        "", "PROJECT TOTAL",
        f"Time         {_fmt_time(state.get('project_elapsed_s', state.get('elapsed_s')))}",
        f"Cost         {_fmt_metric_cost(project_cost, project_tokens)}",
        f"Tokens       {_fmt_tokens(project_tokens)}",
        f"Input        {_fmt_tokens(state.get('project_tokens_in', project_tokens))}",
    ])
    project_input_value = state.get("project_tokens_in", project_tokens)
    project_input = int(project_input_value or 0)
    project_cached = min(project_input, int(state.get("project_cached_input_tokens") or 0))
    project_uncached = max(0, project_input - project_cached)
    lines.append(f"  Cached     {_fmt_tokens(project_cached) if project_input_value is not None else 'n/a'}" + (f"  {project_cached / project_input:.1%}" if project_input else ""))
    lines.append(f"  Uncached   {_fmt_tokens(project_uncached) if project_input_value is not None else 'n/a'}")
    lines.append(f"Output       {_fmt_tokens(state.get('project_tokens_out'))}")
    lines.append(f"Reasoning    {_fmt_tokens(state.get('project_reasoning_tokens'))}")
    labeled = {
        _fmt_model(agent.get("model"), 40, agent.get("reasoning_effort"))
        for agent in (state.get("agents") or {}).values()
        if agent.get("model")
    }
    if len(labeled) == 1:
        lines.append(f"Model        {next(iter(labeled))}")
    return "\n".join(lines)


def render_thread(state: dict[str, Any]) -> str:
    role = state.get("current_agent")
    agent = _agent(state, role) if role else {}
    if not role:
        for key, item in (state.get("agents") or {}).items():
            if item.get("state") in {"running", "rework"}:
                role, agent = key, item
                break
        if not role:
            for key, item in (state.get("agents") or {}).items():
                if item.get("thread_id"):
                    role, agent = key, item
    return "\n".join([
        f"Agent       {DISPLAY_ROLE.get(role, role or '-')}",
        f"Thread ID   {agent.get('thread_id') or '-'}",
        f"Cycle       {state.get('cycle_index') or 0}",
        f"Turns       {agent.get('turn_count') or 'unknown'}",
        f"Tokens      {_fmt_tokens(agent.get('tokens_total'))}",
        f"Cost        {_fmt_cost(agent.get('cost'))}",
        f"State       {agent.get('state') or '-'}",
        f"Model       {_fmt_model(agent.get('model'), 40, agent.get('reasoning_effort'))}",
        f"Provider    {agent.get('provider') or '-'}",
        f"Turn ID     {agent.get('turn_id') or 'unknown'}",
        "",
        "[o] copy thread_id   Codex App is the thread microscope.",
    ])


def render_artifacts(state: dict[str, Any]) -> str:
    items = state.get("artifacts") or []
    lines = ["ARTIFACTS", ""]
    if state.get("report_path"):
        lines.append(f"Cycle Report  {state['report_path']}")
    if state.get("branch_canvas"):
        lines.append(f"Branch        {state['branch_canvas']}")
    extra = [item for item in items if item not in {state.get("report_path"), state.get("branch_canvas")}]
    for item in extra:
        lines.append(str(item))
    if len(lines) == 2:
        lines.append("(none registered)")
    return "\n".join(lines)


def render_canvas(state: dict[str, Any]) -> str:
    return "\n".join([
        "CANVAS",
        "",
        f"Branch:    {state.get('branch_canvas') or '-'}",
        f"Active:    {state.get('branch_id') or '-'}",
        f"Hypothesis:{state.get('hypothesis_title') or '-'}",
        f"Status:    {state.get('status') or '-'}",
        f"Report:    {state.get('report_path') or '-'}",
        "",
        "[o] copy exploration canvas path",
    ])


def _event_stamp(event: dict[str, Any]) -> str:
    return str(event.get("ts") or event.get("at") or event.get("recorded_at") or event.get("created_at") or "")


def _event_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
    index, event = item
    stamp = _event_stamp(event)
    if stamp:
        try:
            return (datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp(), index)
        except ValueError:
            pass
    return (float("inf"), index)


def _event_clock(event: dict[str, Any]) -> str:
    stamp = _event_stamp(event)
    if not stamp:
        return "--:--:--"
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return stamp


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    stamp = _event_stamp(event)
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def render_events(events: list[dict[str, Any]], filt: str = "all") -> str:
    """Render a lossless model conversation as a readable chronological transcript.

    The persisted runtime event stream remains the raw audit source.  This TUI
    view intentionally hides debug metadata (thread/turn ids and token updates),
    while showing the exact prompts sent to Agents, every visible Agent message,
    reconnect/failure notices, and stage boundaries.  Private model reasoning is
    neither recorded nor reconstructed.
    """
    ordered = [event for _, event in sorted(enumerate(events), key=_event_sort_key)]
    run_id = next((str(event.get("run_id")) for event in ordered if event.get("run_id")), "")
    dates = []
    for event in ordered:
        parsed = _event_datetime(event)
        if parsed is not None:
            value = parsed.strftime("%Y-%m-%d")
            if value not in dates:
                dates.append(value)
    meta = "chronological · oldest → newest"
    if run_id:
        meta = f"{run_id} · {meta}"
    if len(dates) == 1:
        meta += f" · {dates[0]}"

    allowed = {
        "AGENT_STARTED", "AGENT_INPUT", "LLM_TURN_STARTED", "LLM_TURN_COMPLETED",
        "LLM_MESSAGE", "LLM_FAILED", "LLM_TURN_FAILED", "LLM_RECONNECTING", "AGENT_COMPLETED",
        "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED",
        "JUDGE_VERDICT", "TOOL_ACTIVITY", "COMPACTION_STARTED", "COMPACTION_COMPLETED", "COMPACTION_FAILED",
        "SKILL_LOADED",
    }
    if filt == "errors":
        allowed = {"LLM_FAILED", "LLM_TURN_FAILED", "LLM_RECONNECTING", "COMPACTION_FAILED"}
    elif filt == "agent":
        allowed = {"AGENT_STARTED", "AGENT_INPUT", "LLM_TURN_STARTED", "LLM_TURN_COMPLETED", "LLM_MESSAGE"}

    blocks: list[str] = [meta]
    stage_by_role: dict[str, str] = {}
    current_section: tuple[str, str] | None = None
    turn_started: dict[tuple[str, str], datetime] = {}

    def ensure_section(role: str, stage: str) -> None:
        nonlocal current_section
        key = (role, stage)
        if current_section == key:
            return
        current_section = key
        role_label = DISPLAY_ROLE.get(role, role.replace("_", " ").title())
        stage_label = stage or "conversation"
        blocks.append(f"──────────────── {role_label} · {stage_label} ────────────────")

    for event in ordered:
        kind = str(event.get("event") or event.get("type") or event.get("action") or "UNKNOWN")
        if kind == "TOKEN_USAGE" or kind not in allowed:
            continue
        role = str(event.get("agent") or event.get("role") or "system")
        stage = str(event.get("stage") or stage_by_role.get(role) or "")
        clock = _event_clock(event)

        if kind == "AGENT_STARTED":
            stage_by_role[role] = stage
            ensure_section(role, stage)
            blocks.append(f"{clock}  {'RESUME' if event.get('resume_thread_id') else 'START'}")
            continue

        if kind == "AGENT_INPUT":
            if stage:
                stage_by_role[role] = stage
            ensure_section(role, stage_by_role.get(role, stage))
            role_label = DISPLAY_ROLE.get(role, role.replace("_", " ").title()).upper()
            input_kind = str(event.get("input_kind") or "task").replace("_", " ")
            content = str(event.get("content") or "")
            blocks.append(f"{clock}  THEORYMASTER → {role_label} · {input_kind}\n\n{content}")
            continue

        if kind == "LLM_TURN_STARTED":
            ensure_section(role, stage_by_role.get(role, stage))
            turn_key = (role, str(event.get("turn_id") or "active"))
            parsed = _event_datetime(event)
            if parsed is not None:
                turn_started[turn_key] = parsed
            # AGENT_STARTED is the human-facing START line.  Keep this event only
            # as an internal duration marker so thread/turn metadata stays hidden.
            continue

        if kind == "LLM_MESSAGE":
            ensure_section(role, stage_by_role.get(role, stage))
            role_label = DISPLAY_ROLE.get(role, role.replace("_", " ").title()).upper()
            blocks.append(f"{clock}  {role_label}\n\n{str(event.get('content') or '')}")
            continue

        if kind == "TOOL_ACTIVITY":
            ensure_section(role, stage_by_role.get(role, stage))
            tool = str(event.get("tool") or "tool")
            detail = " ".join(str(event.get("detail") or "").split())
            if len(detail) > 180:
                detail = detail[:179].rstrip() + "…"
            suffix = f" · {detail}" if detail else ""
            blocks.append(f"{clock}  TOOL · {tool}{suffix}")
            continue

        if kind == "SKILL_LOADED":
            ensure_section(role, stage_by_role.get(role, stage))
            skill = str(event.get("skill") or "unknown")
            detail = [f"load #{int(event.get('load_index') or 1)}"]
            if event.get("first_load_in_turn"):
                detail.append("first in turn")
            if event.get("detection"):
                detail.append(str(event["detection"]).replace("_", " "))
            path = str(event.get("skill_path") or "")
            suffix = f"\n          path: {path}" if path else ""
            blocks.append(f"{clock}  SKILL LOADED · {skill} · {' · '.join(detail)}{suffix}")
            continue

        if kind == "COMPACTION_STARTED":
            ensure_section(role, stage_by_role.get(role, stage))
            before = event.get("context_before")
            window = event.get("context_window")
            threshold = event.get("compact_threshold_tokens")
            scope = event.get("compact_scope")
            detail = []
            if before is not None and window is not None:
                detail.append(f"{int(before) / 1000:.1f}k / {int(window) / 1000:.1f}k")
            elif before is not None:
                detail.append(f"{int(before) / 1000:.1f}k context")
            if threshold is not None:
                detail.append(f"threshold {int(threshold) / 1000:.0f}k")
            if scope:
                detail.append(str(scope))
            if event.get("inferred_from_completed"):
                detail.append("start inferred from confirmed completion")
            suffix = f" · {' · '.join(detail)}" if detail else ""
            blocks.append(f"{clock}  COMPACT START{suffix}")
            continue

        if kind == "COMPACTION_COMPLETED":
            ensure_section(role, stage_by_role.get(role, stage))
            duration = event.get("duration_s")
            after = event.get("context_after")
            detail = []
            if duration is not None:
                detail.append(_fmt_time(float(duration)))
            detail.append("same episode")
            if after is not None:
                detail.append(f"context after {int(after) / 1000:.1f}k")
            else:
                detail.append("context after: next-turn measurement")
            blocks.append(f"{clock}  COMPACT DONE · {' · '.join(detail)}")
            continue

        if kind == "COMPACTION_FAILED":
            ensure_section(role, stage_by_role.get(role, stage))
            blocks.append(f"{clock}  ✗ COMPACT FAILED · {str(event.get('error') or 'native compaction failed')}")
            continue

        if kind == "LLM_RECONNECTING":
            ensure_section(role, stage_by_role.get(role, stage))
            blocks.append(f"{clock}  ⚠ RECONNECTING · {str(event.get('error') or 'waiting for network')}")
            continue

        if kind in {"LLM_FAILED", "LLM_TURN_FAILED"}:
            ensure_section(role, stage_by_role.get(role, stage))
            blocks.append(f"{clock}  ✗ FAILED · {str(event.get('error') or 'LLM failure')}")
            continue

        if kind == "LLM_TURN_COMPLETED":
            ensure_section(role, stage_by_role.get(role, stage))
            turn_key = (role, str(event.get("turn_id") or "active"))
            started = turn_started.pop(turn_key, None)
            ended = _event_datetime(event)
            suffix = ""
            if started is not None and ended is not None:
                suffix = f" · {_fmt_time((ended - started).total_seconds())}"
            blocks.append(f"{clock}  TURN END{suffix}")
            continue

        if kind in {"AGENT_COMPLETED", "INVESTIGATION_ROUND_COMPLETED", "JUDGE_COMPLETED"}:
            ensure_section(role, stage_by_role.get(role, stage))
            label = {
                "AGENT_COMPLETED": "STAGE COMPLETE",
                "INVESTIGATION_ROUND_COMPLETED": "INVESTIGATION COMPLETE",
                "JUDGE_COMPLETED": "JUDGE COMPLETE",
            }[kind]
            blocks.append(f"{clock}  {label}")
            continue

        if kind == "JUDGE_VERDICT":
            verdict = str(event.get("verdict") or "")
            blocks.append(f"{clock}  VERDICT · {verdict}")

    return "\n\n".join(blocks) if len(blocks) > 1 else "(no conversation events)"


_LOG_LINE = re.compile(
    r"^(?:\d{4}-)?(\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:\d{2}|Z)?(?: \[(\w+)\])?\s+(.*)$"
)


def _fmt_log_line(line: str) -> str:
    match = _LOG_LINE.match(line)
    if not match:
        return line
    stamp, level, message = match.group(1), match.group(2), match.group(3)
    if level and level != "INFO":
        return f"{stamp}  [{level}] {message}"
    return f"{stamp}  {message}"


def render_logs(lines: list[str]) -> str:
    return "\n".join(_fmt_log_line(line) for line in lines) or "(no logs)"


def current_thread_id(state: dict[str, Any]) -> str:
    if state.get("current_thread_id"):
        return str(state["current_thread_id"])
    role = state.get("current_agent")
    agent = _agent(state, role) if role else {}
    return str(agent.get("thread_id") or "")
