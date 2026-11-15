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
.tm-explorer-view .tm-page-head{display:none}
.tm-explorer-view .tm-page-label{display:none}
.tm-explorer-view .tm-page-head h1{font-size:20px;line-height:26px;margin:0}
.ex-page-head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;flex:none;margin:0 0 8px}
.ex-page-head h1{font-size:20px;line-height:26px;margin:0}
.ex-page-head button{font-size:12px;padding:5px 10px}
.ex-help{box-sizing:border-box;width:min(780px,calc(100vw - 32px));max-height:85vh;max-height:85dvh;overflow:auto;padding:22px;background:#191f2c;color:#e2e8f0;border:1px solid #64748b;border-radius:12px;line-height:1.7;box-shadow:0 18px 60px #000a}
.ex-help::backdrop{background:#0009}
.ex-help header{display:flex;align-items:center;justify-content:space-between;gap:12px}
.ex-help h2{font-size:20px;margin:0}
.ex-help h3{font-size:16px;margin:20px 0 8px}
.ex-help form{margin:0}
.ex-help table{width:100%;border-collapse:collapse;font-size:13px}
.ex-help th,.ex-help td{padding:8px;text-align:left;border:1px solid #465267;vertical-align:top;overflow-wrap:anywhere}
.ex-help code{color:#bfdbfe;white-space:nowrap}
.ex-toolbar{display:flex;flex-direction:column;gap:10px;align-items:stretch;margin-bottom:8px;flex-shrink:0;padding:8px 12px;background:#171923;border:1px solid #313746;border-radius:9px}
.ex-toolbar-main{display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-width:0}
.ex-toolbar-main #ex-stats{margin-left:auto;font-size:11px;color:#98a4b8;white-space:normal}
.ex-index-warning{color:#b9a776;cursor:help;font-size:13px}
.ex-index-warning[hidden]{display:none}
.ex-toolbar input{min-width:160px;flex:2 1 240px;max-width:520px;background:#18181b;border:1px solid #3f3f46;border-radius:7px;padding:10px;color:#f4f4f5}
.ex-toolbar select{background:#18181b;color:#f4f4f5;padding:9px;border-radius:7px;border:1px solid #3f3f46}
.ex-overview{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px 20px;margin:0;padding:0;background:transparent;border:0;font-size:12px;color:#dbeafe;min-width:0}
.ex-overview-group{display:flex;gap:6px 10px;align-items:center;flex-wrap:wrap;min-width:0}
.ex-overview #ex-tip-summary{flex:0 1 auto;max-width:100%}
.ex-overview #ex-type-legend{justify-content:flex-end;max-width:100%}
.ex-overview-title{white-space:nowrap}
.ex-overview .ex-type-key{font-size:11px}
.ex-overview .ex-type-dot{width:9px;height:9px;flex:none}
.ex-legend-more{position:relative;font-size:11px}
.ex-legend-more summary{display:flex;align-items:center;gap:5px;list-style:none;cursor:pointer;color:#b8c4d6;border:1px solid #3b4555;border-radius:6px;padding:4px 7px;white-space:nowrap}
.ex-legend-more summary::-webkit-details-marker{display:none}
.ex-legend-more summary::after{content:"▾";font-size:10px}
.ex-legend-more[open] summary{border-color:#93c5fd;background:#25344a}
.ex-legend-more-panel{position:absolute;z-index:12;top:calc(100% + 7px);right:0;display:flex;flex-direction:column;gap:10px;min-width:190px;padding:12px;background:#191f2c;border:1px solid #526176;border-radius:8px;box-shadow:0 10px 24px #0009}
.ex-legend-more:not([open]) .ex-legend-more-panel{display:none}
.ex-overview-types{display:flex;gap:9px;align-items:center;border-left:1px solid #3b4555;padding-left:10px}
@media(max-width:1250px){.ex-overview #ex-type-legend{justify-content:flex-start}.ex-toolbar-main #ex-stats{margin-left:0}}
.ex-overview-title{color:#aeb9ce;margin-right:2px;font-weight:600}
.ex-tip-trigger{display:inline-flex;align-items:center;gap:5px;background:#202735;color:#e2e8f0;border:1px solid #536176;border-radius:7px;padding:4px 7px;font-size:11px;cursor:pointer}
.ex-tip-trigger[data-frontier]::before{content:"";display:inline-block;width:9px;height:9px;flex:none;border:1px solid #dbeafe80;border-radius:3px}
.ex-tip-trigger[data-frontier="active"]::before{background:#186F98}
.ex-tip-trigger[data-frontier="parked"]::before{background:#996723}
.ex-tip-trigger[data-frontier="closed"]::before{background:#4B586A}
.ex-tip-trigger[data-frontier="unknown"]::before{background:#384152}
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
.ex-edge-menu{position:absolute;z-index:9;width:24px;height:24px;pointer-events:auto}
.ex-edge-menu[hidden]{display:none}
.ex-edge-menu button{display:flex;align-items:center;justify-content:center;box-sizing:border-box;width:24px;height:24px;padding:0;border:1px solid #93c5fd;border-radius:50%;background:#17283d;color:#e0f2fe;font:600 16px/1 system-ui;box-shadow:0 2px 7px #0008;cursor:pointer}
.ex-edge-menu button:hover,.ex-edge-menu button:focus-visible{background:#2c527b;border-color:#e0f2fe;outline:2px solid #93c5fd;outline-offset:2px}
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
.ex-md img{max-width:100%;height:auto}
.ex-md a{color:#93c5fd}
.ex-md blockquote{border-left:3px solid #64748b;padding-left:13px;color:#cbd5e1}
.ex-md table{display:block;overflow:auto}
.ex-section{border-top:1px solid #333;padding-top:10px;margin-top:18px}
.ex-ref{display:inline-block;margin:4px;padding:5px 9px;border:1px solid #414557;border-radius:7px;cursor:pointer;background:#222633;color:#dbeafe}
.ex-search{position:absolute;z-index:5;top:53px;left:12px;right:12px;max-height:60%;overflow:auto;background:#19191f;border:1px solid #454550;border-radius:8px;padding:6px;box-shadow:0 12px 32px #0009}
.ex-search button{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid #333;border-radius:0}
.ex-search small{display:block;color:#a1a1aa}
.ex-search-section-title{margin:6px 8px;color:#c7d2fe;font-size:12px;font-weight:700}
.ex-search-section+.ex-search-section{border-top:1px solid #454550;margin-top:8px;padding-top:5px}
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
<header class="ex-page-head">
 <h1>图谱</h1>
 <button id="ex-help-open" type="button" aria-haspopup="dialog" aria-controls="ex-help">图谱使用说明</button>
</header>
<dialog id="ex-help" class="ex-help" aria-labelledby="ex-help-title">
 <header><h2 id="ex-help-title">图谱使用说明</h2><form method="dialog"><button type="submit" aria-label="关闭图谱使用说明" autofocus>关闭</button></form></header>
 <p>图谱记录研究如何从猜想走向证据和结论。先看猜想 H 提出了什么，再看对应 Cycle 的 Outcome 得到了什么结论，最后沿连线追踪后续猜想与外部资料。</p>
 <h3>猜想、Cycle 与 Outcome</h3>
 <p><strong>猜想（Hypothesis，H）</strong>是一个可检验的科学命题，包含适用条件和判别标准。每个新科研 Cycle 冻结一个新的 H；同轮恢复、重试和返工沿用该 H。</p>
 <p><strong>Outcome</strong>记录本轮 Judge 对冻结命题的终局科学裁决，连同理由、发现、未解决问题、适用边界、证据与报告。每个有终局科学裁决的 Cycle 恰有一个 Outcome，嵌在 H 卡片内；历史宽泛 H 可以包含多个 Cycle 的 Outcome。局部结论的效力以该轮冻结范围为准。</p>
 <h3>Outcome 的三种科学裁决</h3>
 <table><thead><tr><th>状态</th><th>含义</th></tr></thead><tbody>
 <tr><td>支持 · <code>supported</code></td><td>证据支持本轮冻结命题，在所列条件和检验范围内成立。</td></tr>
 <tr><td>否决 · <code>rejected</code></td><td>证据反驳本轮冻结命题。反例、失败机制和适用边界仍是可积累的研究成果。</td></tr>
 <tr><td>Open · <code>open</code></td><td>Judge 已完成本轮裁决，认定现有证据仍无法判定命题成立或不成立；需阅读其理由、边界和未解决问题。</td></tr>
 </tbody></table>
 <h3>页面中的其他结论标签</h3>
 <table><thead><tr><th>标签</th><th>阅读方法</th></tr></thead><tbody>
 <tr><td>待裁决 · <code>none</code></td><td>尚无可展示的科学裁决。查看 Cycle 运行状态了解进度；它与 Judge 已裁决的 Open 有区别。</td></tr>
 <tr><td>研究中止 · 未裁决<br><code>unadjudicated</code></td><td>展示中止研究的阶段性保全材料，执行未完成，未经终局 Judge 科学裁决。普通运行失败、中断或取消也不自动产生 Open。</td></tr>
 <tr><td>多轮结论不一 · <code>mixed</code></td><td>历史 H 的多轮 Outcome 展示分类不一致，需逐轮检查冻结命题与范围；这一汇总标签不代表整体 H 已获新裁决。</td></tr>
 <tr><td>历史归一 · <code>legacy</code></td><td>历史非标准裁决经兼容映射显示。以详情中的原始裁决为准，不能当作正式 Judge Open。</td></tr>
 </tbody></table>
 <h3>上半部的前沿状态：下一步是否继续研究</h3>
 <p><strong>活跃（active）</strong>：可作为后续研究来源；<strong>搁置（parked）</strong>：暂缓推进；<strong>已终止（closed）</strong>：退出默认研究前沿；<strong>未标记（unknown）</strong>：当前前沿状态缺失或待核验。</p>
 <p>前沿状态与科学裁决分别记录。例如，支持后的 H 可以保留为活跃来源以衍生新问题；否决后的分支也可继续追查失败机制。活跃不代表正在运行，已终止也不代表猜想已被否决。</p>
 <h3>怎样读图和查看原文</h3>
 <ul>
 <li>放大后，H 卡片上半部显示猜想与前沿状态，下半部显示 Outcome；分别点击可阅读详情、逐轮结论和完整 Markdown 报告。</li>
 <li>缩小到 ≤60% 时显示数字概览，默认按前沿着色；“远景颜色”按钮可切换到结论着色。颜色用于导航，结论以详情中的裁决和范围为准。</li>
 <li>顶部前沿与 Outcome 统计均按 H 去重，计数表示猜想数量。一个历史 H 的多条 Outcome 在概览中汇总一次；“更多”展开少见标签。外部资料另计。</li>
 <li>H→H 连线表示有来源的科学演化；外部资料（External，E）保存文献、人工输入或历史归档材料，其引用连线用于追溯来源。外部资料本身没有 Judge 裁决权威。</li>
 <li>点击顶部前沿状态定位分支；用搜索查找节点与 Markdown，拖动分隔条调整阅读区，悬停分支连线可折叠或展开。出现后代的 H 仍可保留活跃资格。</li>
 </ul>
 <p>单轮 Outcome 回答“这一轮命题得到了什么结论”；任务是否达成总体目标由独立的 Goal Completion 判断。历史 Canvas 视图可能保留 Decision、Finding、StageSummary 节点，以各记录来源和裁决范围为准。</p>
</dialog>
<script id="ex-help-script">
(()=>{
 const button=document.getElementById('ex-help-open'),dialog=document.getElementById('ex-help');
 button.addEventListener('click',()=>dialog.showModal());
 dialog.addEventListener('click',event=>{
  if(event.target!==dialog)return;
  const bounds=dialog.getBoundingClientRect();
  if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)dialog.close();
 });
})();
</script>
<div class="ex-toolbar">
 <div class="ex-toolbar-main">
  <label>科研任务 <select id="ex-task">TM_CANVAS_TASK_OPTIONS</select></label>
  <input id="ex-query" type="search" placeholder="搜索科研节点与 Markdown…" aria-label="Search scientific documents"/>
  <button id="ex-refresh" title="重新读取当前 Canvas 与 Markdown">刷新文件</button>
  <div class="ex-layout-controls" aria-label="工作区操作">
    <button id="ex-fullscreen" type="button" aria-pressed="false" title="切换工作区全屏（Esc 退出）">⛶ 全屏</button>
  </div>
  <span class="small" id="ex-stats">读取中…</span>
  <span id="ex-index-warning" class="ex-index-warning" tabindex="0" aria-label="文档索引未覆盖全部内容" title="文档索引达到读取预算，部分链接和搜索内容可能尚未索引。" hidden>ⓘ</span>
 </div>
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
      <button id="ex-all" title="展开所有分支并显示整张图谱">完整图谱</button>
      <span class="ex-divider"></span>
      <button id="ex-fit">适应画布</button><button id="ex-plus">＋</button><output id="ex-zoom-percent" class="ex-zoom-percent" aria-label="图谱绝对缩放比例" title="100% 为固定节点基础尺寸，与节点数量和适应画布无关">100%</output><button id="ex-minus">－</button>
      <span class="ex-spacer"></span>
      <button id="ex-expand-all" title="取消所有折叠">全部展开</button>
    </div>
    <div class="ex-search" id="ex-results" role="status" aria-live="polite" hidden></div>
    <aside class="ex-tip-panel" id="ex-tip-panel" hidden aria-label="科研分支末端导航"></aside>
    <div class="ex-edge-menu" id="ex-edge-menu" role="group" aria-label="子分支操作" hidden>
      <button id="ex-edge-menu-action" type="button" aria-label="折叠子分支">−</button>
    </div>
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
    tree_script = Path(__file__).with_name("research_explorer_layout_c.js").read_text(encoding="utf-8")
    return GRAPH_CSS + GRAPH_HTML.replace("TM_GRAPH_JS", tree_script + "\n" + script)
