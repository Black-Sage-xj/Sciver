"""A shared report file entry for the task console and task list."""
from __future__ import annotations

import html
from typing import Any

from .final_report_ui import report_view


REPORT_ENTRY_CSS = r"""
.tm-tui-titlebar.tm-titlebar-report{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,380px) minmax(0,1fr);grid-template-areas:"identity report actions";gap:20px;align-items:center}
.tm-titlebar-report .tm-tui-title-main{grid-area:identity}
.tm-titlebar-report .tm-tui-title-actions{grid-area:actions;min-width:0;flex-wrap:wrap;justify-self:end;margin-left:0}
.tm-final-report-slot{min-width:0;max-width:100%}
.tm-final-report-slot[hidden]{display:none !important}
.tm-final-report-slot-console{grid-area:report;width:100%;justify-self:center}
.tm-final-report-entry{box-sizing:border-box;display:flex;align-items:center;gap:13px;min-width:0;max-width:100%;color:var(--tm-text);text-decoration:none}
.tm-final-report-console{min-height:92px;padding:16px 18px;border:1px solid var(--tm-link);border-radius:11px;background:var(--tm-panel)}
a.tm-final-report-entry:hover{background:color-mix(in srgb,var(--tm-link) 12%,var(--tm-panel));text-decoration:none}
a.tm-final-report-entry:focus-visible{outline:2px solid var(--tm-link);outline-offset:4px}
.tm-final-report-icon{width:29px;height:34px;flex:none;color:var(--tm-link)}
.tm-final-report-copy{display:flex;flex:1;min-width:0;flex-direction:column;gap:6px}
.tm-final-report-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap;line-height:1.4}
.tm-final-report-heading strong{color:var(--tm-text);font-size:17px;font-weight:650}
.tm-final-report-status{color:var(--tm-muted);font-size:11px;white-space:nowrap}
.tm-final-report-title{color:var(--tm-muted);font-size:12px;line-height:1.6;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tm-final-report-open{width:18px;height:18px;flex:none;color:var(--tm-link)}
.tm-final-report-entry[data-report-state="needs_revision"] .tm-final-report-status{color:var(--tm-warning,#fbbf24)}
.tm-final-report-entry[data-report-available="false"]{border-color:var(--tm-border)}
.tm-final-report-entry[data-report-available="false"] .tm-final-report-icon{color:var(--tm-muted)}
.tm-final-report-list{gap:8px;min-height:42px;padding:5px 0;white-space:normal}
.tm-final-report-list .tm-final-report-icon{width:18px;height:22px}
.tm-final-report-list .tm-final-report-heading strong{font-size:12px;color:var(--tm-link)}
.tm-final-report-list .tm-final-report-heading{gap:6px}
.tm-final-report-list .tm-final-report-copy{gap:3px}
.tm-final-report-list .tm-final-report-status{font-size:11px}
.tm-final-report-list .tm-final-report-open{width:14px;height:14px}
.tm-run-table td[data-label="最终总报告"]{min-width:130px}
@media(max-width:1180px){
 .tm-tui-titlebar.tm-titlebar-report{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"identity actions" "report report";gap:16px}
 .tm-final-report-slot-console{width:min(480px,100%)}
}
@media(max-width:600px){
 .tm-tui-titlebar.tm-titlebar-report{grid-template-columns:minmax(0,1fr);grid-template-areas:"identity" "report" "actions";gap:13px}
 .tm-titlebar-report .tm-tui-title-actions{width:100%;justify-content:flex-end}
 .tm-final-report-console{min-height:84px;padding:13px 14px}
 .tm-final-report-heading strong{font-size:16px}
 .tm-run-table td[data-label="最终总报告"]{grid-column:1 / -1;min-width:0}
 .tm-run-table td[data-label="最终总报告"]:has(>[data-final-report-entry][hidden]){display:none}
 .tm-final-report-list{min-height:44px}
}
.tm-tui-titlebar.tm-titlebar-report:has(>[data-final-report-entry="console"][hidden]){display:flex;gap:16px}
"""

_FILE_ICON = ('<svg class="tm-final-report-icon" viewBox="0 0 24 28" fill="none" '
              'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="M5 2h9l6 6v18H4V2h1Z"/>'
              '<path d="M14 2v7h6M8 14h8M8 18h8M8 22h5"/></svg>')
_OPEN_ICON = ('<svg class="tm-final-report-open" viewBox="0 0 20 20" fill="none" '
              'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg>')


def render_report_entry(run: dict[str, Any], *, placement: str = "list",
                        cache: dict | None = None) -> str:
    view = report_view(run, cache=cache)
    placement = "console" if placement == "console" else "list"
    slot = (f'<div class="tm-final-report-slot tm-final-report-slot-{placement}" '
            f'data-final-report-entry="{placement}"')
    if not view["visible"]:
        return slot + ' hidden></div>'
    escape = lambda value: html.escape(str(value or ""), quote=True)
    title = view["title"] if view["available"] else view["message"]
    label = view["label"]
    if view["available"] and view.get("latest_job_state") in {"queued", "running", "collecting", "composing", "reviewing"}:
        label += " · 新版生成中"
    elif view["available"] and view.get("latest_job_state") == "failed":
        label += " · 新版生成失败"
    heading = "最终总报告" if placement == "console" else "阅读总报告" if view["available"] else "总报告"
    content = ((_FILE_ICON if view["available"] else "") + '<span class="tm-final-report-copy"><span class="tm-final-report-heading">'
               f'<strong>{heading}</strong>'
               + (f'<span class="tm-final-report-status">{escape(label)}</span>' if placement == "console" else "")
               + '</span>'
               + (f'<span class="tm-final-report-title">{escape(title)}</span>' if placement == "console"
                  else f'<span class="tm-final-report-status">{escape(label)}</span>')
               + '</span>' + (_OPEN_ICON if view["href"] else ""))
    attributes = (f'class="tm-final-report-entry tm-final-report-{placement}" '
                  f'data-report-state="{escape(view["state"])}" '
                  f'data-report-available="{str(bool(view["available"])).lower()}"')
    entry = (f'<a {attributes} href="{escape(view["href"])}" '
             f'aria-label="{escape(heading + "：" + title + "，" + label)}" '
             f'title="{escape(title)}">{content}</a>' if view["href"] else
             f'<div {attributes} role="status">{content}</div>')
    return slot + f'>{entry}</div>'
