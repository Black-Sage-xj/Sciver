"""The read-only, automatically laid out research graph view.

Obsidian Canvas geometry is intentionally not consulted by this view.
"""
from __future__ import annotations

from pathlib import Path

G6_VERSION = "5.1.1"

GRAPH_CSS = r"""
<style>
/* Reserve only the global topbar; the research graph fills the remaining viewport. */
.tm-explorer-view .tm-content{max-width:none;padding:10px 16px 12px;height:calc(100vh - 62px);height:calc(100dvh - 62px);display:flex;flex-direction:column;overflow:hidden}
.tm-explorer-view .tm-page-head{flex:none;margin:0 0 8px}
.tm-explorer-view .tm-page-label{display:none}
.tm-explorer-view .tm-page-head h1{font-size:20px;line-height:26px;margin:0}
.ex-toolbar{display:flex;gap:8px 10px;align-items:center;flex-wrap:wrap;margin-bottom:8px;flex-shrink:0;padding:8px 12px;background:#171923;border:1px solid #313746;border-radius:9px}
.ex-toolbar input{min-width:185px;flex:2 1 255px;max-width:520px;background:#18181b;border:1px solid #3f3f46;border-radius:7px;padding:10px;color:#f4f4f5}
.ex-toolbar select{background:#18181b;color:#f4f4f5;padding:9px;border-radius:7px;border:1px solid #3f3f46}
.ex-layout-demo-link{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;padding:7px 10px;border:1px solid #b08935;border-radius:7px;background:#292316;color:#fde68a;text-decoration:none;font-size:12px;font-weight:650}
.ex-layout-demo-link:hover,.ex-layout-demo-link:focus-visible{background:#3b301a;border-color:#fbbf24;color:#fef3c7}
.ex-overview{display:flex;align-items:center;justify-content:flex-start;flex:1 1 auto;flex-wrap:wrap;gap:8px 14px;margin:0;padding:0;background:transparent;border:0;font-size:12px;color:#dbeafe;min-width:0}
.ex-overview-group{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.ex-overview-title{color:#aeb9ce;margin-right:2px;font-weight:600}
.ex-tip-trigger{background:#252a38;color:#e2e8f0;border:1px solid #536176;border-radius:7px;padding:5px 9px;font-size:12px;cursor:pointer}
.ex-tip-trigger:hover,.ex-tip-trigger:focus-visible{border-color:#93c5fd;background:#30405a}
.ex-tip-trigger[data-frontier="active"]{border-color:#34d399;color:#bbf7d0}
.ex-tip-trigger[data-frontier="parked"]{border-color:#fbbf24;color:#fde68a}
.ex-tip-trigger[data-frontier="closed"]{border-color:#94a3b8;color:#cbd5e1}
.ex-type-key{display:flex;align-items:center;gap:4px;color:#d6deed;white-space:nowrap}
.ex-type-key .ex-type-dot{width:11px;height:11px;border-radius:3px;border:1px solid #f8fafc70;display:inline-block}
.ex-tip-panel{position:absolute;z-index:8;top:60px;right:12px;width:min(340px,calc(100% - 24px));max-height:calc(100% - 85px);overflow:auto;padding:12px;background:#191f2c;border:1px solid #64748b;border-radius:11px;box-shadow:0 16px 45px #000c}
.ex-tip-panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;gap:8px}
.ex-tip-panel-head button{background:transparent;color:#e2e8f0;border:1px solid #596579;border-radius:5px;padding:3px 8px;cursor:pointer}
.ex-tip-panel .ex-tip-entry{margin:5px 0;border:1px solid #374151;border-left:4px solid #5b697c;border-radius:6px;background:#222a38;overflow:hidden}
.ex-tip-panel .ex-tip-entry button.ex-tip-item{display:block;width:100%;text-align:left;border:0;border-radius:0;background:transparent;color:#e2e8f0;margin:0;padding:8px;cursor:pointer;font-size:12px}
.ex-tip-panel .ex-tip-entry button.ex-tip-item:hover,.ex-tip-panel .ex-tip-entry button.ex-tip-item:focus-visible{background:#30445f}
.ex-tip-panel .ex-tip-workers{display:flex;gap:6px;flex-wrap:wrap;padding:0 8px 8px}
.ex-tip-panel .ex-tip-worker{display:inline-flex;align-items:center;padding:4px 8px;border:1px solid #54a9a0;border-radius:5px;background:#153d3b;color:#c8f7ed;text-decoration:none;font-size:11px;font-weight:700;white-space:nowrap}
.ex-tip-panel .ex-tip-worker:hover,.ex-tip-panel .ex-tip-worker:focus-visible{background:#24605a;color:white;outline:1px solid #b5fff0}
.ex-tip-panel .ex-tip-sub{display:block;font-size:11px;color:#94a3b8;margin-top:3px}
.ex-tip-panel-note{color:#facc15;font-size:11px;margin:7px 2px}
.ex-workspace{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}
.tm-explorer-view .ex-layout{display:grid;grid-template-columns:minmax(0,var(--ex-side-size,63%)) 12px minmax(0,1fr);gap:0;flex:1;min-height:0;height:auto;min-width:0}
.ex-splitter{position:relative;align-self:stretch;justify-self:stretch;border:0;background:transparent;cursor:col-resize;touch-action:none;user-select:none;z-index:7;padding:0}
.ex-splitter::before{content:'';position:absolute;top:10px;bottom:10px;left:4px;width:4px;border-radius:9px;background:#536176;transition:background .12s}
.ex-splitter:hover::before,.ex-splitter:focus-visible::before,.ex-splitter[data-dragging="true"]::before{background:#93c5fd}
.ex-splitter:focus-visible{outline:2px solid #93c5fd;outline-offset:-2px;border-radius:6px}
.ex-layout[data-resizing="true"]{user-select:none}
.ex-layout[data-resizing="true"] .ex-graph{pointer-events:none}
.ex-layout-controls{display:flex;gap:5px;align-items:center}
.ex-layout-controls button{font-size:12px;padding:7px 10px;white-space:nowrap}
.ex-layout-controls button[aria-pressed="true"]{background:#1e3a5f;border-color:#60a5fa;color:#dbeafe}
.ex-workspace:fullscreen{box-sizing:border-box;width:100vw;height:100vh;background:#101014;padding:12px 16px;display:flex;flex-direction:column;overflow:hidden}
.ex-workspace:fullscreen .ex-layout{flex:1;min-height:0;height:auto}
.ex-workspace:fullscreen .ex-toolbar{flex-shrink:0}
.ex-workspace:fullscreen .ex-overview{flex-shrink:0}
/* Separate painting surfaces: stale G6 canvas layers must never bleed into the reader. */
.ex-graph,.ex-reader{position:relative;background:#111115;border:1px solid #313139;border-radius:12px;overflow:hidden;min-width:0;contain:paint;isolation:isolate}
.ex-graph{touch-action:none}
#ex-g6{height:100%;width:100%;overflow:hidden;background:radial-gradient(circle,#272731 0.65px,transparent 0.8px);background-size:21px 21px}
.ex-actions{position:absolute;top:12px;left:12px;right:12px;display:flex;align-items:center;flex-wrap:wrap;gap:6px;z-index:3;pointer-events:none}
.ex-actions>*{pointer-events:auto}
.ex-actions button{padding:7px 10px;font-size:12px;background:#23232c;color:#f1f5f9;border:1px solid #484851}
.ex-actions button[aria-pressed="true"]{border-color:#60a5fa;background:#1e3a5f;color:#dbeafe}
.ex-actions .ex-zoom-percent{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;min-width:58px;height:30px;padding:0 7px;border:1px solid #565c69;border-radius:6px;background:#171c28;color:#e2e8f0;font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.ex-actions .ex-divider{height:17px;border-left:1px solid #52525b}
.ex-actions .ex-spacer{flex:1}
.ex-actions .ex-history{font-weight:600;min-width:56px}
.ex-actions button:disabled{opacity:.36;cursor:not-allowed}
.ex-outcome-cards{position:absolute;inset:0;z-index:2;pointer-events:none;overflow:hidden}
.ex-compact-labels{position:absolute;inset:0;z-index:2;pointer-events:none;overflow:hidden}
.ex-compact-labels[hidden]{display:none}
.ex-compact-label{position:absolute;box-sizing:border-box;display:flex;align-items:center;justify-content:center;overflow:hidden;pointer-events:none;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif;font-weight:800;line-height:1;white-space:nowrap;font-variant-numeric:tabular-nums}
.ex-outcome-card{position:absolute;box-sizing:border-box;display:flex;flex-direction:column;transform-origin:top left;pointer-events:auto;overflow:hidden;border-radius:7px}
.ex-outcome-card .ex-outcome-part{touch-action:manipulation;appearance:none;display:flex;flex:1;min-height:0;flex-direction:column;justify-content:center;align-items:center;gap:4px;width:100%;padding:6px 14px;margin:0;border:0;border-radius:0;color:#f1f5f9;cursor:pointer;overflow:hidden;text-align:center;box-shadow:none}
.ex-outcome-card .ex-outcome-hypothesis{background:#245f9c}
.ex-outcome-card .ex-outcome-outcome{background:#20665e;border-top:2px solid #e0f2feaf}
.ex-outcome-card .ex-outcome-verdict-rejected{background:#79394d}
.ex-outcome-card .ex-outcome-verdict-open{background:#605079}
.ex-outcome-card .ex-outcome-part:hover,.ex-outcome-card .ex-outcome-part:focus-visible{filter:brightness(1.26);outline:2px solid #f8fafc;outline-offset:-2px}
.ex-outcome-card .ex-outcome-part.is-selected{box-shadow:inset 0 0 0 2px #bfdbfe}
.ex-outcome-card .ex-outcome-kicker{font-size:12px;font-weight:750;line-height:1.25}
.ex-outcome-card .ex-outcome-copy{font-size:12px;font-weight:550;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow-wrap:anywhere}
.ex-source-rail{position:absolute;inset:0;z-index:6;pointer-events:none;overflow:hidden}
.ex-node-source-strip{position:absolute;box-sizing:border-box;background:#101723d8;border-right:1px solid #64748b80;display:flex;flex-direction:column;padding:12px 0;border-radius:9px 0 0 9px;pointer-events:auto;transform-origin:top left;overflow:hidden}
.ex-node-source-strip .ex-source-link{appearance:none!important;display:flex;justify-content:center;align-items:center;width:100%;flex:1;min-height:0;margin:0!important;padding:0 1px!important;box-sizing:border-box;border:0!important;border-left:2px solid transparent!important;border-radius:0!important;box-shadow:none!important;background:transparent!important;outline:0;color:#dbeafe;font-size:11px;font-weight:750;text-align:center;line-height:1;white-space:nowrap;overflow:hidden;cursor:pointer}
.ex-node-source-strip .ex-source-link+.ex-source-link{border-top:1px solid #64748b40!important}
.ex-node-source-strip .ex-source-link:hover,.ex-node-source-strip .ex-source-link:focus-visible{background:#a8b6ce28!important;outline:1px solid #e2e8f0b0;outline-offset:-1px}
.ex-error{position:absolute;bottom:12px;right:12px;max-width:430px;background:#2c191e;border:1px solid #b45362;border-radius:8px;padding:10px 14px;color:#fecdd3;z-index:4}
.ex-error a{color:#93c5fd}
.ex-reader{padding:22px;overflow-y:auto;overflow-wrap:anywhere}
.ex-reader h2{margin:0 0 7px;font-size:20px}
.ex-reader .ex-meta{font-size:12px;color:#a1a1aa;margin-bottom:15px}
.ex-node-time{font-size:12px;color:#a1a1aa;margin:0 0 12px;overflow-wrap:anywhere}
.ex-node-tags{margin:0 0 13px;color:#94a3b8;font-size:11px}
.ex-node-tags summary{display:inline-flex;align-items:center;gap:4px;list-style:none;cursor:pointer;font-size:11px;color:#94a3b8;user-select:none;padding:2px 0}
.ex-node-tags summary::-webkit-details-marker{display:none}
.ex-node-tags summary::before{content:'▸';font-size:9px;line-height:1;transition:transform 120ms ease}
.ex-node-tags[open] summary::before{transform:rotate(90deg)}
.ex-node-tags summary:hover,.ex-node-tags summary:focus-visible{color:#dbeafe}
.ex-node-tags-count{opacity:.65;font-size:10px}
.ex-node-tags-list{display:flex;flex-direction:column;gap:3px;margin:6px 0 4px;padding:7px 10px;border-left:1px solid #475569;background:#1a1e27;border-radius:0 5px 5px 0}
.ex-node-tag-line{line-height:1.45;overflow-wrap:anywhere;white-space:pre-wrap;font-size:11px}
.ex-node-tag-key{color:#a5b4cc}
.ex-node-tag-value{color:#d2dbea}
.ex-reader .ex-md{line-height:1.75}
.ex-md pre{overflow:auto;background:#08080a;padding:12px;border-radius:8px}
.ex-md img{max-width:100%}
.ex-md a{color:#93c5fd}
.ex-md blockquote{border-left:3px solid #64748b;padding-left:13px;color:#cbd5e1}
.ex-md table{display:block;overflow:auto}
.ex-section{border-top:1px solid #333;padding-top:10px;margin-top:18px}
.ex-ref{display:inline-block;margin:4px;padding:5px 9px;border:1px solid #414557;border-radius:7px;cursor:pointer;background:#222633;color:#dbeafe}
.ex-search{position:absolute;z-index:5;top:53px;left:12px;right:12px;max-height:60%;overflow:auto;background:#19191f;border:1px solid #454550;border-radius:8px;padding:6px;box-shadow:0 12px 32px #0009}
.ex-search button{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid #333;border-radius:0}
.ex-search small{display:block;color:#a1a1aa}
.ex-tooltip{position:absolute;z-index:4;pointer-events:none;background:#1c202c;border:1px solid #5e687b;border-radius:8px;padding:10px;max-width:300px;color:#eee;font-size:12px;box-shadow:0 10px 28px #0009}
.ex-tooltip strong{display:block;margin-bottom:5px}
.ex-tooltip small{display:block;color:#a1a1aa;margin-top:5px}
.ex-selected{border-left:3px solid #60a5fa;padding:7px 10px;margin:0 0 14px;background:#1b263a;border-radius:6px;font-size:12px}
@media(max-width:860px){.ex-layout{min-height:500px;height:calc(100vh - 255px)}.ex-layout[data-layout="side"]{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,var(--ex-stack-size,50%)) 12px minmax(0,1fr)}.ex-layout[data-layout="side"] .ex-splitter{cursor:row-resize}.ex-layout[data-layout="side"] .ex-splitter::before{top:4px;bottom:auto;left:10px;right:10px;width:auto;height:4px}.ex-layout .ex-reader{min-height:0}}
@media(max-width:900px){.tm-explorer-view .tm-content{height:auto;min-height:calc(100dvh - 108px);overflow:visible}.tm-explorer-view .ex-workspace{min-height:650px}.tm-explorer-view .ex-layout{min-height:500px}.tm-explorer-view .ex-workspace:fullscreen{min-height:0}.tm-explorer-view .ex-workspace:fullscreen .ex-layout{min-height:0}}
</style>
"""

GRAPH_HTML = r"""
<div class="ex-workspace" id="ex-workspace">
<div class="ex-toolbar">
  <label>科研任务 <select id="ex-task">TM_CANVAS_TASK_OPTIONS</select></label>
  <input id="ex-query" type="search" placeholder="搜索科研节点与 Markdown…" aria-label="Search scientific documents"/>
  <button id="ex-refresh" title="重新读取当前 Canvas 与 Markdown">刷新文件</button>
  <a id="ex-layout-demo-link" class="ex-layout-demo-link" href="/research-explorer-tmp/" target="_blank" rel="noopener noreferrer" title="打开保留的布局B视图">布局B</a>
  <a id="ex-layout-c-link" class="ex-layout-demo-link" href="/research-explorer-tmp-c/" target="_blank" rel="noopener noreferrer" title="打开新的只读科研探索树布局C">布局C</a>
  <div class="ex-layout-controls" aria-label="工作区操作">
    <button id="ex-fullscreen" type="button" aria-pressed="false" title="切换工作区全屏（Esc 退出）">⛶ 全屏</button>
  </div>
  <span class="small" id="ex-stats">读取中…</span>
  <div class="ex-overview" aria-label="科研节点类型与末端状态统计">
    <div class="ex-overview-group" id="ex-tip-summary"></div>
    <div class="ex-overview-group" id="ex-type-legend"></div>
  </div>
</div>
<div class="ex-layout" id="ex-layout" data-layout="side">
  <div class="ex-graph" id="ex-graph" aria-label="只读科研演化图谱">
    <div id="ex-g6"></div>
    <div class="ex-outcome-cards" id="ex-outcome-cards" aria-label="猜想与 Outcome 独立阅读区域"></div>
    <div class="ex-compact-labels" id="ex-compact-labels" aria-hidden="true" hidden></div>
    <div class="ex-actions">
      <button id="ex-back" class="ex-history" title="返回上一次稳定的图谱位置" aria-label="返回" disabled>← 返回</button>
      <button id="ex-forward" class="ex-history" title="前进到下一次浏览位置" aria-label="前进" disabled>前进 →</button>
      <span class="ex-divider"></span>
      <button id="ex-all" aria-pressed="false">完整图谱</button>
      <button id="ex-local" aria-pressed="false">局部关联</button>
      <span class="ex-divider"></span>
      <button id="ex-fit">适应画布</button><button id="ex-plus">＋</button><output id="ex-zoom-percent" class="ex-zoom-percent" aria-label="图谱绝对缩放比例" title="100% 为固定节点基础尺寸，与节点数量和适应画布无关">100%</output><button id="ex-minus">－</button>
      <span class="ex-spacer"></span>
      <button id="ex-collapse" title="折叠/展开所选分支">折叠分支</button>
      <button id="ex-expand-all" title="取消所有折叠">全部展开</button>
    </div>
    <div class="ex-search" id="ex-results" role="status" aria-live="polite" hidden></div>
    <aside class="ex-tip-panel" id="ex-tip-panel" hidden aria-label="科研分支末端导航"></aside>
    <div class="ex-source-rail" id="ex-source-rail" aria-label="科研节点左侧内嵌的前序节点索引"></div>
    <div class="ex-tooltip" id="ex-tooltip" hidden></div>
    <div class="ex-error" id="ex-error" hidden></div>
  </div>
  <div class="ex-splitter" id="ex-splitter" role="separator" tabindex="0" aria-label="拖动或按方向键调整图谱与笔记的分配比例" aria-controls="ex-graph ex-reader" aria-orientation="vertical" aria-valuemin="20" aria-valuemax="80" aria-valuenow="63" title="拖动调整大小 · 方向键微调 · 双击恢复默认"></div>
  <section class="ex-reader" id="ex-reader" aria-live="polite"><p class="muted">选择左侧科研节点以阅读完整内容。</p></section>
</div>
</div>
<script defer src="https://cdn.jsdelivr.net/npm/@antv/g6@5.1.1/dist/g6.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
<script>
TM_GRAPH_JS
</script>
"""


def graph_content() -> str:
    script = Path(__file__).with_name("research_explorer_g6.js").read_text(encoding="utf-8")
    return GRAPH_CSS + GRAPH_HTML.replace("TM_GRAPH_JS", script)
