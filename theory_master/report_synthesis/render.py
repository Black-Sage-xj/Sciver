"""Offline report reading view, with the report's linear research story."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import shutil
from typing import Any

from .reading import cycle_reading
from .story import STORY_CAPTION, STORY_WIDTH, story_svg


def lineage_svg(report: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Compatibility entry point for the report's linear narrative visual."""
    return story_svg(report, snapshot)


def render_report(report: dict[str, Any], snapshot: dict[str, Any], output_dir: Path,
                  result: dict[str, Any]) -> Path:
    """Reuse the bundled Markdown/math/sanitizer assets; no remote requests."""
    vendor = Path(__file__).parent / "assets/vendor"
    target = output_dir / "assets"
    target.mkdir(exist_ok=True)
    for name in ("marked.min.js", "purify.min.js", "katex.min.js", "katex.min.css",
                 "prism.min.js", "prism-python.min.js", "prism-bash.min.js", "prism-json.min.js",
                 "marked-LICENSE.txt", "dompurify-LICENSE.txt", "katex-LICENSE.txt", "prism-LICENSE.txt"):
        shutil.copyfile(vendor / name, target / name)
    shutil.copyfile(Path(__file__).parent / "assets/markdown_reader.js", target / "markdown_reader.js")
    shutil.copytree(vendor / "fonts", target / "fonts", dirs_exist_ok=True)
    diagram = lineage_svg(report, snapshot)
    if diagram:
        (output_dir / "research-story.svg").write_text(diagram, encoding="utf-8")
    reading = cycle_reading(report, snapshot)
    sources = reading["cycles"]
    frozen_files = {}
    for row in snapshot.get("documents", []) + snapshot.get("figures", []):
        if row.get("frozen_path"):
            for ref in (row.get("reference"), row.get("source_ref"), row.get("id"), row.get("source_path")):
                if ref:
                    frozen_files[ref] = "sources/" + row["frozen_path"]
    details = [row for key in ("outcomes", "preservations")
               for row in snapshot.get(key, [])]
    source_parts = []
    for ref, row in sources.items():
        label = html.escape(row["title"])
        link = frozen_files.get(row.get("source_ref"))
        if link:
            reference = '<a href="' + html.escape(link, quote=True) + '">' + label + '</a>'
        else:
            records = [record for record in details
                       if str(record.get("cycle_id", "")) == row["cycle_id"]]
            reference = ('<details><summary>' + label + '</summary><pre>'
                         + html.escape(json.dumps(records, ensure_ascii=False, indent=2)) + '</pre></details>') if records else label
        source_parts.append('<li id="src-' + hashlib.sha256(ref.encode()).hexdigest()[:16] + '">' + reference + '</li>')
    source_list = "".join(source_parts)
    state = "审核通过" if result["state"] == "ready" else "审核待修订"
    data = json.dumps({"markdown": reading["markdown"], "sources": {ref: "src-" + hashlib.sha256(ref.encode()).hexdigest()[:16] for ref in sources},
        "source_labels": {ref: row["title"] for ref, row in sources.items()}}, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(report["title"])
    graph = '<details class="trajectory" open><summary>研究主线概览</summary><div class="diagram-scroll" tabindex="0" aria-label="研究主线图"><img width="' + str(STORY_WIDTH) + '" src="research-story.svg" alt="研究关键阶段的线性主线"></div><p class="caption">' + html.escape(STORY_CAPTION) + '</p></details>' if diagram else ""
    page = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + title + r'''</title>
<link rel="stylesheet" href="assets/katex.min.css"><style>
:root{color-scheme:light;--paper:#fff;--ink:#22313d;--muted:#526674;--line:#dbe2e7;--blue:#245b85}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.9 "Noto Sans CJK SC","Microsoft YaHei",sans-serif}::selection{background:#d9eafa;color:#153d5c}a{color:var(--blue);text-underline-offset:3px}a:focus-visible,summary:focus-visible,button:focus-visible{outline:2px solid var(--blue);outline-offset:5px}header{max-width:1160px;margin:auto;padding:24px 30px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;font-size:13px;color:var(--muted)}button{background:transparent;border:1px solid var(--line);padding:7px 14px;color:var(--blue);border-radius:4px;cursor:pointer}button:hover{background:#edf4f9}.layout{display:grid;grid-template-columns:200px minmax(0,800px);gap:44px;max-width:1160px;margin:0 auto;padding:44px 30px}nav{position:sticky;top:28px;align-self:start;font-size:13px;line-height:1.65}nav a{display:block;padding:7px 0;text-decoration:none;color:var(--muted)}nav a:hover{color:var(--blue)}main{min-width:0}h1{font-size:30px;line-height:1.4;letter-spacing:-.025em;margin:0 0 24px;font-weight:650}h2{font-size:23px;line-height:1.5;margin:46px 0 17px}h3{font-size:19px;line-height:1.5;margin:30px 0 12px}p{margin:14px 0}img{max-width:100%;height:auto}table{display:block;overflow:auto;border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}th{background:#f3f6f8}pre{background:#f3f6f8;padding:16px;overflow:auto;font-size:13px}code{font-size:.87em;overflow-wrap:anywhere}blockquote{margin:20px 0;padding:0 18px;border-left:1px solid var(--line);color:var(--muted)}.caption{font-size:12px;line-height:1.7;color:var(--muted)}details{margin-top:28px;border-top:1px solid var(--line);padding-top:14px}summary{cursor:pointer;font-weight:550}details img{margin-top:20px}.sources{font-size:12px;overflow-wrap:anywhere}.status{color:var(--muted)}.katex-display{overflow:auto}#report{overflow-wrap:anywhere}@media(max-width:800px){header{padding:18px 22px}.layout{display:block;padding:26px 22px}nav{position:static;border-bottom:1px solid var(--line);margin-bottom:28px;padding-bottom:16px}nav a{display:inline-block;padding:4px 12px 4px 0}h1{font-size:26px}body{font-size:15px}.trajectory img{min-width:0}}@media print{header,nav,button{display:none}.layout{display:block;padding:0}body{font-size:11pt}h2,h3{break-after:avoid}img,figure,tr{break-inside:avoid}details{display:block}.caption{font-size:9pt}a{color:inherit}.sources{font-size:8pt}}
</style><header><span>科研总报告 · <span class="status">''' + state + r'''</span></span><button onclick="window.print()">打印 / 保存 PDF</button></header><div class="layout"><nav id="toc" aria-label="报告目录"></nav><main><article id="report"></article>''' + graph + r'''<details class="sources"><summary>研究轮次</summary><ul>''' + source_list + r'''</ul></details></main></div>
<script type="application/json" id="report-data">''' + data + r'''</script><script src="assets/marked.min.js"></script><script src="assets/purify.min.js"></script><script src="assets/katex.min.js"></script><script data-manual src="assets/prism.min.js"></script><script src="assets/prism-python.min.js"></script><script src="assets/prism-bash.min.js"></script><script src="assets/prism-json.min.js"></script><script src="assets/markdown_reader.js"></script><script>
const data=JSON.parse(document.getElementById('report-data').textContent);const root=document.getElementById('report');
const markdownReader=window.SciverMarkdown.create({resolve(target){let ref;try{ref=decodeURIComponent(target.split('|')[0].split('#')[0])}catch{return null}return data.sources[ref]?{href:'#'+data.sources[ref],label:data.source_labels[ref]}:null},imageUrl:path=>path});
root.innerHTML=markdownReader.render(data.markdown,'report.md');
const sourceByAnchor=Object.fromEntries(Object.entries(data.sources).map(([ref,id])=>[id,ref]));root.querySelectorAll('a[href^="#src-"]').forEach(a=>{const ref=sourceByAnchor[a.getAttribute('href').slice(1)];if(ref){a.classList.add('citation');a.title=data.source_labels[ref]}});
root.querySelectorAll('table').forEach(table=>{const wrap=document.createElement('div');wrap.className='table-wrap';wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','结果表格，可横向滚动');table.replaceWith(wrap);wrap.append(table)});
const toc=document.getElementById('toc');root.querySelectorAll('h2').forEach((h,i)=>{h.id='section-'+i;const a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;toc.append(a)});document.querySelectorAll('a[href^="#src-"]').forEach(a=>a.addEventListener('click',()=>{document.querySelector('.sources').open=true}));window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>d.open=true));
</script></html>'''
    page = page.replace("</style>", """
.citation{font-size:.78em;vertical-align:super;white-space:nowrap;margin:0 2px;text-decoration:none}
.citation:hover{text-decoration:underline}.table-wrap{max-width:100%;overflow-x:auto;margin:18px 0;scrollbar-color:#91a8b8 #f3f6f8}
.table-wrap:focus-visible,.diagram-scroll:focus-visible{outline:2px solid var(--blue);outline-offset:4px}
.table-wrap table{display:table;min-width:720px;overflow-wrap:normal}.table-wrap td{white-space:normal}
.diagram-scroll{max-width:100%;overflow-x:auto;margin-top:20px;scrollbar-color:#91a8b8 #f3f6f8}
.diagram-scroll img{display:block;max-width:100%;height:auto;margin-top:0}
@media print{.table-wrap{overflow:visible}.table-wrap table{min-width:0;font-size:9pt}.diagram-scroll img{max-width:100%}}
</style>""", 1)
    path = output_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path
