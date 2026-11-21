"""HTML and plain text renderings of the same review trace."""
from __future__ import annotations

from html import escape
from urllib.parse import urlencode


def url(trace, **params):
    return "?" + urlencode(dict(tab="trace", cycle=trace["cycle"], **params))


def _stage_info(stage):
    states = {"committed": "已提交", "completed": "已完成", "running": "执行中",
              "prepared": "已准备", "failed": "失败", "interrupted": "已中断"}
    parts = [stage["time"][11:19], states.get(stage["state"], stage["state"])]
    parts.append(stage["prompt"])
    return " · ".join(part for part in parts if part)


def _empty(trace):
    return ("没有记录此阶段的 Prompt。" if trace["view"] == "prompt" else
            "尚无模型输出或阶段交付。")


def as_text(trace):
    view = trace["view"]
    lines = [f"{trace['task']} | Cycle {trace['cycle'] or '—'} | 轨迹审查"]
    if not view:
        lines += [f"{trace['total']} 个阶段 · 第 {trace['page']}/{trace['pages']} 页",
                  trace["billing"], *trace["role_costs"]]
        if trace["stages"]:
            lines.append("按需读取本阶段原文：在此 URL 加 &stage=<下列 ID>&view=prompt 或 &view=output")
    lines += trace["warnings"]
    for stage in trace["stages"]:
        if stage["handoff"] and not view:
            lines.append("\n交接 " + stage["handoff"])
        lines += [f"\n{stage['role']} / {stage['stage']} [{stage['id']}]",
                  _stage_info(stage)]
        if not view and stage["omitted"]:
            lines.append(f"前 {stage['omitted']} 条已省略")
        lines += [f"{label}｜{content}" for label, content in stage["lines"]] or [_empty(trace)]
    if not trace["stages"]:
        lines.append("此 Cycle 尚无阶段记录。" if trace["cycle"] else "此任务尚无 Cycle 记录。")
    if not view and trace["page"] < trace["pages"]:
        lines.append("下一页 " + url(trace, page=trace["page"]+1, format="txt"))
    return "\n".join(filter(None, lines)) + "\n"


STYLE = """<style>
.tm-run-view:has(.tm-review) .tm-page-head{display:none}
.tm-review{max-width:64rem;margin:0 auto;padding:clamp(12px,3vw,40px);color:var(--tm-text);overflow-wrap:anywhere}
.tm-review h1{font-size:clamp(1.65rem,3vw,2.4rem);line-height:1.2;margin:0 0 .5rem}
.tm-review h2{font-size:1.18rem;margin:0;line-height:1.5}
.tm-review h2 span{font-size:.9em;font-weight:400;color:var(--tm-muted)}
.tm-review p{margin:.45rem 0;line-height:1.65}
.tm-review header,.tm-review footer,.tm-review .trace-toolbar,.tm-review .trace-meta,.tm-review .trace-links{
 display:flex;align-items:center;gap:.65rem 1.2rem;flex-wrap:wrap}
.tm-review header,.tm-review footer{justify-content:space-between}
.tm-review a{color:var(--tm-link,var(--tm-accent));text-underline-offset:.2em}
.tm-review :is(a,button,select,summary):focus-visible{outline:2px solid var(--tm-accent);outline-offset:3px}
.tm-review .trace-toolbar{margin:.8rem 0 1.2rem}
.tm-review button,.tm-review select{font:inherit;color:inherit;background:var(--tm-panel);border:1px solid var(--tm-border);padding:.32rem .65rem;border-radius:.35rem}
.tm-review button,.tm-review summary{cursor:pointer}
.tm-review .trace-billing{margin:.9rem 0;border-block:1px solid var(--tm-border);padding:.7rem 0}
.tm-review .trace-billing summary{font-variant-numeric:tabular-nums}
.tm-review .trace-meta,.tm-review .trace-muted,.tm-review .trace-links,.tm-review .trace-handoff{
 font-size:.87rem;color:var(--tm-muted);font-variant-numeric:tabular-nums}
.tm-review article{padding:1.35rem 0;scroll-margin-top:4rem}
.tm-review article+article{border-top:1px solid var(--tm-border)}
.tm-review .trace-handoff{padding:.55rem 0 .2rem;border-top:1px solid var(--tm-border)}
.tm-review .trace-meta{margin:.2rem 0 .75rem}
.tm-review .trace-line{max-width:76ch;white-space:pre-wrap;line-height:1.7}
.tm-review .trace-label{color:var(--tm-muted);font-size:.82em;margin-right:.7rem}
.tm-review .trace-links{margin-top:.7rem}
.tm-review footer{border-top:1px solid var(--tm-border);padding:1rem 0}
@media(max-width:620px){.tm-review{padding:12px 4px}.tm-review .trace-toolbar{gap:.6rem}.tm-review article{padding:1rem 0}}
</style>"""


def as_html(trace):
    view = trace["view"]
    selected = trace["stages"][0]["id"] if view else ""
    options = "".join(f'<option value="{escape(c)}"{" selected" if c == trace["cycle"] else ""}>'
                      f'Cycle {escape(c)}</option>' for c in trace["cycles"])
    form = (f'<form method="get" class="trace-toolbar"><input type="hidden" name="tab" value="trace">'
            f'<select name="cycle" aria-label="选择 Cycle">{options}</select>'
            '<button type="submit">读取周期</button></form>') if options and not view else ""
    text_url = url(trace, stage=selected, view=view, format="txt") if view else url(trace, page=trace["page"], format="txt")
    title = {"prompt": "阶段 Prompt", "output": "阶段输出"}.get(view, "轨迹审查")
    base = escape(url(trace, page=trace["page"]))
    back = f'<a href="{base}#{escape(selected)}">返回周期摘要</a>' if view else ""
    count = f' · {trace["total"]} 个阶段' if not view else ""
    body = [STYLE, '<section class="tm-review" aria-labelledby="trace-title">',
            f'<header><div><h1 id="trace-title">{title}</h1><p>{escape(trace["task"])} · '
            f'Cycle {escape(trace["cycle"] or "—")}{count}</p></div>{form}</header>',
            f'<nav class="trace-toolbar" aria-label="轨迹操作">{back}'
            f'<a data-trace-text href="{escape(text_url)}">Agent 纯文本</a>'
            '<button type="button" data-trace-copy hidden>复制纯文本</button>'
            '<span role="status" data-trace-status></span></nav>']
    if trace["billing"]:
        body.append('<details class="trace-billing"><summary>本轮费用 · ' + escape(trace["billing"]) +
                    '</summary>' + "".join(f'<p>{escape(c)}</p>' for c in trace["role_costs"]) +
                    ('' if trace["role_costs"] else '<p>尚无角色费用明细。</p>') + '</details>')
    for warning in trace["warnings"]:
        body.append(f'<p class="trace-muted" role="status">{escape(warning)}</p>')
    for stage in trace["stages"]:
        ident = stage["id"]
        if stage["handoff"] and not view:
            body.append(f'<div class="trace-handoff">交接　{escape(stage["handoff"])}</div>')
        body.append(f'<article id="{escape(ident)}"><h2>{escape(stage["role"])} '
                    f'<span>/ {escape(stage["stage"])}</span></h2>'
                    f'<div class="trace-meta">{escape(_stage_info(stage))}</div>')
        for label, content in stage["lines"]:
            body.append(f'<p class="trace-line"><span class="trace-label">{escape(label)}</span>{escape(content)}</p>')
        if not stage["lines"]:
            body.append(f'<p class="trace-muted">{_empty(trace)}</p>')
        if not view and stage["omitted"]:
            body.append(f'<p class="trace-muted">另有 {stage["omitted"]} 条较早输出</p>')
        links = []
        for mode, label in (("output", "完整输出"), ("prompt", "查看 Prompt")):
            if mode != view:
                links.append(f'<a href="{escape(url(trace, stage=ident, view=mode))}">{label}</a>')
        raw = "?" + urlencode(dict(tab="events", cycle=trace["cycle"], stage=ident))
        links.append(f'<a href="{escape(raw)}">原始 Events</a>')
        body.append('<nav class="trace-links" aria-label="阶段记录">' + "".join(links) + '</nav></article>')
    if not trace["stages"]:
        body.append('<p>此 Cycle 尚无阶段记录。</p>' if trace["cycle"] else '<p>此任务尚无 Cycle 记录。</p>')
    if trace["pages"] > 1 and not view:
        body.append(f'<footer><span>第 {trace["page"]} / {trace["pages"]} 页</span>')
        for delta, label in ((-1, "上一页"), (1, "下一页")):
            target = trace["page"] + delta
            if 1 <= target <= trace["pages"]:
                body.append(f'<a href="{escape(url(trace, page=target))}">{label}</a>')
        body.append('</footer>')
    body.append("""</section><script>(() => {
      const button = document.querySelector('[data-trace-copy]');
      if (!navigator.clipboard?.writeText) return;
      button.hidden = false;
      button.addEventListener('click', async () => {
        const status = document.querySelector('[data-trace-status]');
        try {
          const response = await fetch(document.querySelector('[data-trace-text]').href);
          if (!response.ok || !response.headers.get('content-type')?.startsWith('text/plain')) throw Error();
          await navigator.clipboard.writeText(await response.text());
          status.textContent = '已复制';
        } catch { status.textContent = '请打开 Agent 纯文本后复制。'; }
      });
    })();</script>""")
    return "".join(body)
