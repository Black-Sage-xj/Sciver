"""Semantic event timeline with stable stage colors; no Web SDK imports."""
from __future__ import annotations

import html
from typing import Any

ROLE_NAMES = {
    "research_lead": "Research Lead", "challenger": "Challenger",
    "investigator": "Investigator", "literature": "Literature",
    "judge": "Judge", "reporter": "Reporter",
}

def e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)

def render(projection: dict[str, Any]) -> str:
    events = projection.get("events") or []
    options = '<option value="all">所有 Agent</option>' + ''.join(
        f'<option value="{role}">{name}</option>' for role, name in ROLE_NAMES.items()
    ) + '<option value="system">System</option>'
    cards = []
    for event in events:
        if not isinstance(event, dict):
            continue
        role = str(event.get("role") or "system").lower()
        if role not in ROLE_NAMES:
            role = "system"
        kind = str(event.get("kind") or "UNKNOWN")
        category = ("error" if any(word in kind for word in ("FAILED","ERROR","STOPPED","ATTENTION"))
                    else "message" if kind in {"LLM_MESSAGE","AGENT_INPUT"} else "activity")
        text = str(event.get("content") or "")
        if len(text) > 600:
            content = ('<details class="tm-event-body"><summary>展开详细内容</summary>'
                       f'<pre>{e(text)}</pre></details>')
        else:
            content = f'<p class="tm-event-text">{e(text)}</p>' if text else ""
        stage = str(event.get("stage") or "")
        cards.append(
            f'<article class="tm-event tm-role-{role}" data-event-role="{role}" data-event-category="{category}">'
            f'<div class="tm-event-head"><span class="tm-role-chip tm-role-{role}">'
            f'{e(ROLE_NAMES.get(role,"System"))}</span><strong>{e(kind.replace("_"," ").title())}</strong>'
            f'<time>{e(event.get("time"))}</time></div>'
            + (f'<div class="tm-event-stage">{e(stage)}</div>' if stage else "")
            + content + '</article>'
        )
    note = ('仅显示日志末尾 256 KiB 中的事件，早期记录未加载'
            if projection.get("events_partial") else
            '仅显示最近 100 条相关事件' if projection.get("events_capped") else
            '当前 Cycle 的相关事件')
    return f'''<div class="tm-events-browser" data-tui-pane="secondary">
      <div class="tm-events-toolbar">
        <label>Agent <select data-event-filter="role">{options}</select></label>
        <label>类型 <select data-event-filter="category">
          <option value="all">全部类型</option><option value="message">对话 / 输入</option>
          <option value="activity">阶段 / 工具</option><option value="error">异常 / Attention</option>
        </select></label>
        <label class="tm-events-search">搜索 <input type="search" data-event-filter="search" placeholder="内容、阶段、关键词…"/></label>
      </div>
      <div class="tm-event-count" data-event-count>已加载 {len(cards)} 条 · {e(note)}</div>
      <div class="tm-events-list">{''.join(cards) if cards else '<p class="tm-run-empty">没有可显示的事件。</p>'}</div>
    </div>'''
