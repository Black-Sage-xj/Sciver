"""Compact, read-only Supervisor quota-controller presentation for Run overview."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any


_STATUS = {
    "waiting_refresh": "等待额度恢复",
    "idle": "自动管理已开启",
    "disabled": "已关闭",
    "unsupported_provider": "当前 Provider 不支持",
    "switching": "正在切换账号",
    "switched": "已切换账号",
    "resuming": "正在恢复任务",
    "checking": "正在检查额度",
    "cycle_boundary_checked": "已检查 Codex 额度",
}


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _help_tooltip(key: str, description: str) -> str:
    """Accessible informational tooltip; contains no switch/click target."""
    tooltip_id = "tm-setting-help-" + key
    return (
        '<span class="tm-setting-help-wrap">'
        '<span class="tm-setting-help" tabindex="0" aria-label="查看说明"'
        ' aria-describedby="' + _escape(tooltip_id) + '">?</span>'
        '<span id="' + _escape(tooltip_id) + '" role="tooltip"'
        ' class="tm-setting-tooltip">' + _escape(description) + '</span>'
        '</span>'
    )


def _account(value: Any) -> str:
    return "Codex " + str(value) if str(value) in {"1", "2"} else "未记录"


def _percentage(value: Any) -> str:
    if type(value) in (float, int) and 0 <= value <= 100:
        return f"{value:g}%"
    return "未获取"


def _reset_time(value: Any) -> str:
    if type(value) is not int or not 0 < value < 5_000_000_000:
        return "未记录"
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, OverflowError, ValueError):
        return "未记录"


def _account_card(number: str, data: dict[str, Any]) -> str:
    selected = data.get("_selected") is True
    usable = data.get("ordinary_usage_allowed")
    if usable is True:
        check_label = "服务端检测：可用"
    elif usable is False:
        check_label = "服务端检测：受限"
    else:
        check_label = "服务端检测：未知"
    windows = []
    for key, label in (("primary", "5 小时"), ("secondary", "7 天")):
        values = data.get(key)
        values = values if isinstance(values, dict) else {}
        percent = values.get("remaining_percent")
        width = f"{percent:g}%" if type(percent) in (int, float) and 0 <= percent <= 100 else "0%"
        windows.append(
            '<div class="tm-quota-auto-window"><div class="tm-quota-auto-meter-label">'
            '<span>' + label + '额度剩余</span><strong>' + _escape(_percentage(percent)) +
            '</strong></div><div class="tm-quota-auto-meter"><span style="width:' +
            width + '"></span></div><div class="tm-quota-auto-reset">'
            '预计重置：' + _escape(_reset_time(values.get("resets_at"))) +
            '</div></div>'
        )
    return (
        '<div class="tm-quota-auto-account">'
        '<div class="tm-quota-auto-account-header"><strong>Codex ' + number + '</strong>'
        + ('<span>当前选中</span>' if selected else '')
        + '</div><p class="tm-quota-auto-check">' + check_label + '</p>'
        + "".join(windows) + '</div>'
    )


def quota_auto_card(raw: Any, *, settings_html: str = "", quota_switch_html: str = "") -> str:
    """Render only structured, bounded controller facts; never parse TUI prose."""
    if not isinstance(raw, dict):
        if not settings_html:
            return ""
        raw = {}
    status = str(raw.get("status") or "").strip()
    enabled = bool(raw.get("enabled"))
    if not status and not enabled and not settings_html:
        return ""
    # The Task toggle is authoritative; a stale controller snapshot must not
    # claim active quota management after the user has disabled it.
    if not enabled and (status or raw.get("enabled") is not None):
        status = "disabled"
    name = _STATUS.get(status, "状态待确认" if not status else "状态待确认")
    tone = ("tm-quota-auto-wait" if status == "waiting_refresh" else
            "tm-quota-auto-off" if status in {"disabled", "unsupported_provider"} else
            "tm-quota-auto-ready")
    selected = raw.get("selected_account")
    failed = raw.get("failed_account")
    chips = []
    if str(selected) in {"1", "2"}:
        chips.append('<span>当前账号 <strong>' + _escape(_account(selected)) + '</strong></span>')
    if str(failed) in {"1", "2"}:
        chips.append('<span>触发额度暂停 <strong>' + _escape(_account(failed)) + '</strong></span>')
    provider = raw.get("selected_provider")
    if provider == "Codex" and status != "disabled":
        chips.append('<span>当前提供商 <strong>Codex</strong></span>')
    blocked = raw.get("provider_fallback_blocked")
    if blocked == "in_flight_investigator_native_goal":
        chips.append('<span>切换保护 <strong>Investigator Native Goal 进行中</strong></span>')
    poll = raw.get("next_check_in_s")
    interval = ('<span>自动检查间隔 <strong>约 ' + str(poll) + ' 秒</strong></span>'
                if status == "waiting_refresh" and type(poll) is int and 1 <= poll <= 86400 else '')
    return (
        '<section class="tm-quota-auto ' + tone + '" data-tui-pane="quota-settings" '
        'aria-label="任务运行开关与额度状态">'
        '<div class="tm-quota-auto-toolbar">'
        '<div class="tm-quota-auto-overview tm-setting-card-surface" data-setting-card="quota_auto_adjust">'
        '<div class="tm-setting-compact-copy">'
        '<div class="tm-quota-auto-head"><span class="tm-quota-auto-dot" aria-hidden="true"></span>'
        '<button type="button" class="tm-setting-title-action" data-setting-open="quota_auto_adjust" '
        'aria-controls="tm-setting-detail-box" aria-expanded="false">额度自调</button>'
        + _help_tooltip("quota-status",
                        "Supervisor 优先使用有额度的 Codex 账号；两个账号均耗尽时进入额度等待，额度恢复后自动续跑。只有拨动开关才能启停 Codex 额度管理，不会消费额度重置卡。")
        + '</div>'
        '<strong class="tm-quota-auto-state">' + _escape(name) + '</strong>'
        '<div class="tm-quota-auto-facts">' + "".join(chips) + interval + '</div>'
        '</div>' + quota_switch_html + '</div>'
        + settings_html + '</div></section>'
    )
