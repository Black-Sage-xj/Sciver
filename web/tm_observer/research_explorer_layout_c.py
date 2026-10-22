"""Independent read-only layout C: a cycle-aligned scientific exploration tree."""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from .canvas_md import ExplorerError, vault_root
from .dashboard_ui import dashboard_shell

CSS = r"""
<style>
.tm-tree-c{color:#e2e8f0;max-width:none}
.tm-tree-c *{box-sizing:border-box}
.tm-tree-c .c-head{background:#161c28;border:1px solid #374155;padding:13px 16px;border-radius:11px;margin-bottom:10px}
.tm-tree-c .c-titlebar,.tm-tree-c .c-nav,.tm-tree-c .c-tools{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.tm-tree-c .c-titlebar{justify-content:space-between;align-items:flex-start}
.tm-tree-c .c-eyebrow{color:#a5b4fc;font-size:10px;letter-spacing:.15em;font-weight:800}
.tm-tree-c h1{font-size:21px;margin:4px 0 5px}
.tm-tree-c .c-caption{color:#9caec8;font-size:12px;line-height:1.6}
.tm-tree-c a,.tm-tree-c button{font:inherit;font-size:12px;border-radius:7px;cursor:pointer}
.tm-tree-c a{color:#c4b5fd;text-decoration:none;border:1px solid #49536b;padding:7px 10px}
.tm-tree-c a:hover,.tm-tree-c a:focus-visible{background:#25314b}
.tm-tree-c button{border:1px solid #475569;background:#222e41;color:#e2e8f0;padding:7px 10px}
.tm-tree-c button:hover,.tm-tree-c button:focus-visible{background:#33445e}
.tm-tree-c button[aria-pressed="true"]{border-color:#a78bfa;background:#40315d}
.tm-tree-c button:disabled{opacity:.4;cursor:default}
.tm-tree-c input{font:inherit;background:#0f1724;border:1px solid #465169;border-radius:7px;padding:8px;color:#e2e8f0;font-size:12px;min-width:180px;flex:1;max-width:390px}
.tm-tree-c .c-tools{margin-top:12px}
.tm-tree-c .c-info{color:#a1b0c7;font-size:11px;margin-left:auto}
.tm-tree-c .c-body{display:grid;grid-template-columns:195px minmax(0,1fr) 335px;gap:9px;height:calc(100dvh - 256px);min-height:520px}
.tm-tree-c .c-panel{background:#0c1320;border:1px solid #344052;border-radius:10px;min-width:0;min-height:0;overflow:hidden}
.tm-tree-c .c-side,.tm-tree-c .c-detail{overflow:auto;padding:12px}
.tm-tree-c h2{font-size:12px;margin:0 0 8px;color:#cbd5e1}
.tm-tree-c h3{font-size:12px;color:#c4b5fd;margin:14px 0 7px;padding-top:11px;border-top:1px solid #2d394d}
.tm-tree-c .c-side button{display:block;width:100%;text-align:left;margin:5px 0;white-space:normal;line-height:1.5}
.tm-tree-c .c-side small{font-size:10px;color:#9caec8;display:block;margin-top:2px}
.tm-tree-c .c-viewport{position:relative;min-width:0;overflow:hidden;cursor:grab;background-color:#0c1423;background-image:radial-gradient(#24334a .75px,transparent .75px);background-size:18px 18px;touch-action:none}
.tm-tree-c .c-viewport.is-panning{cursor:grabbing}
.tm-tree-c svg{width:100%;height:100%;display:block}
.tm-tree-c .c-node{cursor:pointer;outline:none}
.tm-tree-c .c-node:focus-visible .c-node-box{stroke:#fff;stroke-width:3}
.tm-tree-c .c-node-box{fill:#1b2535;stroke:#627287;stroke-width:1.5}
.tm-tree-c .c-node[data-status="active"] .c-node-box{stroke:#38bdf8}
.tm-tree-c .c-node[data-status="parked"] .c-node-box{stroke:#f5b845}
.tm-tree-c .c-node[data-status="closed"] .c-node-box{stroke:#738091}
.tm-tree-c .c-node.is-selected .c-node-box{stroke:#e0b4ff;stroke-width:3;fill:#2c2845}
.tm-tree-c .c-node.is-muted{opacity:.27}
.tm-tree-c .c-node text{fill:#e5edf8;font-size:12px;pointer-events:none}
.tm-tree-c .c-node .c-subtitle{fill:#a9b8cb;font-size:10px}
.tm-tree-c .c-node .c-frontier-mark{fill:#38bdf8}
.tm-tree-c .c-node[data-status="parked"] .c-frontier-mark{fill:#f5b845}
.tm-tree-c .c-node[data-status="closed"] .c-frontier-mark{fill:#738091}
.tm-tree-c .c-line{fill:none;stroke:#66778d;stroke-width:1.65;opacity:.75}
.tm-tree-c .c-line.is-path{stroke:#a78bfa;stroke-width:3;opacity:1}
.tm-tree-c .c-line.c-aux{stroke:#f6c565;stroke-width:1.7;stroke-dasharray:6 5;opacity:.8}
.tm-tree-c .c-fold{cursor:pointer}
.tm-tree-c .c-fold circle{fill:#344258;stroke:#8493a9}
.tm-tree-c .c-fold text{fill:#fff;font-size:14px;text-anchor:middle;pointer-events:none}
.tm-tree-c .c-root-label{fill:#8191ab;font-size:10px;font-weight:700}
.tm-tree-c .c-detail .c-meta,.tm-tree-c .c-detail p{font-size:12px;color:#b4c2d5;line-height:1.6;overflow-wrap:anywhere}
.tm-tree-c .c-detail .c-summary{white-space:pre-wrap;max-height:160px;overflow:auto}
.tm-tree-c .c-detail button{padding:5px 7px;text-align:left;margin:3px}
.tm-tree-c .c-chip{display:inline-block;font-size:10px;padding:3px 6px;border-radius:5px;background:#2a3548;margin:3px}
.tm-tree-c .c-chip.active{color:#86e0fb}.tm-tree-c .c-chip.parked{color:#f6cb7b}.tm-tree-c .c-chip.closed{color:#a7b3c3}
.tm-tree-c .c-warning{color:#f2ca81;font-size:11px;margin-top:7px}
.tm-tree-c .c-error{position:absolute;left:12px;right:12px;top:12px;padding:12px;background:#431f2b;color:#fecdd3;border:1px solid #be556c;border-radius:8px}
.tm-tree-c .c-help{position:absolute;bottom:10px;left:12px;background:#0a1022e3;padding:6px 8px;border-radius:6px;font-size:10px;color:#9cafc8;pointer-events:none}
@media(max-width:1120px){.tm-tree-c .c-body{grid-template-columns:165px minmax(0,1fr);height:75vh}.tm-tree-c .c-detail{grid-column:1 / -1;min-height:230px}.tm-tree-c .c-body{height:auto}.tm-tree-c .c-viewport{min-height:55vh}.tm-tree-c .c-side{max-height:55vh}}
@media(max-width:680px){.tm-tree-c .c-body{display:block}.tm-tree-c .c-side{max-height:200px}.tm-tree-c .c-viewport{height:53vh;margin:9px 0}.tm-tree-c .c-detail{max-height:65vh}}
</style>
"""

HTML = r"""
<div class="tm-tree-c" id="tm-tree-c" data-observer-id="TM_OBSERVER_ID">
 <header class="c-head">
  <div class="c-titlebar"><div>
   <div class="c-eyebrow">SCIVER · EXPERIMENTAL LAYOUT C · READ ONLY</div>
   <h1>科研探索树 · 布局 C</h1>
   <div class="c-caption">真实演化边构成分支主干；X 轴按首次记录的 Cycle 排序，Y 轴按整棵子树分配。多父来源保留为可查看的辅助关系，不改变科学记录。</div>
  </div><nav class="c-nav">
    <a href="TM_ORIGINAL_HREF">原图谱</a>
    <a href="TM_LAYOUT_B_HREF">布局 B</a>
    <a href="TM_LAYOUT_C_HREF" aria-current="page">布局 C</a>
  </nav></div>
  <div class="c-tools">
   <input id="c-query" type="search" placeholder="搜索 H 编号或科学猜想标题…" aria-label="搜索猜想"/>
   <button id="c-reset" type="button">清除搜索</button>
   <button id="c-active" type="button">定位活跃前沿</button>
   <button id="c-fit" type="button">适应全图</button>
   <button id="c-plus" type="button">放大 +</button>
   <button id="c-minus" type="button">缩小 −</button>
   <button id="c-aux" type="button" aria-pressed="false">显示辅助来源</button>
   <button id="c-expand" type="button">全部展开</button>
   <span id="c-stats" class="c-info">读取只读科学图谱…</span>
  </div>
 </header>
 <section class="c-body">
  <aside class="c-panel c-side">
   <h2>研究导航</h2><div id="c-summary" class="c-caption">读取中…</div>
   <h3>活跃前沿</h3><div id="c-frontiers" class="c-caption">加载中…</div>
   <h3>搜索结果</h3><div id="c-results" class="c-caption">可按 H 编号或关键词查找。</div>
  </aside>
  <div class="c-panel c-viewport" id="c-viewport" aria-label="科研探索树，只读" tabindex="0">
   <svg id="c-svg" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Hypothesis 科研分支结构"></svg>
   <div id="c-error" class="c-error" hidden></div>
   <div class="c-help">拖拽平移 · 滚轮平移 · Ctrl/⌘ + 滚轮缩放 · 点击节点查看科学关系 · 点击 +/− 折叠</div>
  </div>
  <aside class="c-panel c-detail" id="c-detail" aria-live="polite"><h2>科研详情</h2><p>点击一个猜想，查看 Outcome、完整父子关系和研究路径。</p></aside>
 </section>
</div>
<script>
TM_TREE_C_JS
</script>
"""


def explorer_layout_c_html(search_root: Path, state: dict, selected: str = "") -> str:
    """Read-only temporary view; never mutates Canvas, SQLite or scientific state."""
    observer_id = str(selected or "").strip()
    try:
        vault_root(search_root, observer_id, state)
    except ExplorerError as exc:
        body = (
            '<section><h1>布局 C · 科研探索树</h1><p>无法读取任务：'
            + html.escape(str(exc)) + '</p><a href="/research-explorer/">返回原图谱</a></section>'
        )
        return dashboard_shell("科研探索树 · 布局 C", body, active="run",
                               breadcrumb_leaf="布局 C · 科研探索树")
    encoded = quote(observer_id, safe="")
    original = "/research-explorer/?observer_id=" + encoded
    layout_b = "/research-explorer-tmp/?observer_id=" + encoded
    layout_c = "/research-explorer-tmp-c/?observer_id=" + encoded
    script = Path(__file__).with_name("research_explorer_layout_c.js").read_text(encoding="utf-8")
    body = (CSS + HTML
            .replace("TM_OBSERVER_ID", html.escape(observer_id, quote=True))
            .replace("TM_ORIGINAL_HREF", html.escape(original, quote=True))
            .replace("TM_LAYOUT_B_HREF", html.escape(layout_b, quote=True))
            .replace("TM_LAYOUT_C_HREF", html.escape(layout_c, quote=True))
            .replace("TM_TREE_C_JS", script))
    return dashboard_shell("科研探索树 · 布局 C", body, active="run",
                           back_href=original, breadcrumb_leaf="布局 C · 科研探索树")
