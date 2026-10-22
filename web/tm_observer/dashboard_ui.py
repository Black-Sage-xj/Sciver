"""Four-view Sciver shell using shadcn Dashboard visual conventions.

No Javascript framework is required: tm_observer keeps the existing Python HTML
and interactive scientific diagram, while the shell provides consistent tokens,
navigation, cards, and responsive behaviour.
"""
from __future__ import annotations

import html

THEME_BOOTSTRAP_SCRIPT = """<script data-sciver-theme-bootstrap>
(function() {
  "use strict";
  let theme="black";
  try { if(window.localStorage.getItem("sciver-theme-v1")==="blue")theme="blue"; } catch(_err) {}
  document.documentElement.setAttribute("data-sciver-theme",theme);
})();
</script>"""

STYLE = """
<style>
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-synthesis: none;
  --tm-bg: #09090b;
  --tm-panel: #101013;
  --tm-side: #0c0c0e;
  --tm-hover: #1b1b1f;
  --tm-border: #223b5c;
  --tm-text: #f4f8ff;
  --tm-muted: #a9bad0;
  --tm-accent: #80bdff;
  --tm-positive: #34d399;
}
* { box-sizing: border-box; }
html { background: var(--tm-bg); }
body {
  background: var(--tm-bg); color: var(--tm-text);
  margin: 0; padding: 0; min-height: 100vh; max-width: none;
  font-size: 14px; line-height: 1.55;
}
button, input, textarea, select { font: inherit; }
button, select { cursor: pointer; }
button { color: var(--tm-text); background: #18181b; border: 1px solid #304967; border-radius: 8px; padding: 7px 10px; }
button:hover { background: #27272a; }
a { color: #a5d1ff; text-underline-offset: 3px; }
a:hover { color: white; }
:focus-visible { outline: 2px solid #80bdff; outline-offset: 3px; }
h1, h2, h3, h4 { letter-spacing: -.025em; line-height: 1.25; }
h1 { font-size: 25px; margin: 0 0 7px; font-weight: 680; }
h2 { font-size: 18px; margin: 26px 0 12px; font-weight: 650; }
h3 { font-size: 15px; }
p { margin: 7px 0 13px; }
code { font-size: .87em; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th, td { text-align: left; padding: 10px; border-bottom: 1px solid var(--tm-border); vertical-align: top; }
th { position: sticky; top: 0; background: var(--tm-panel); font-weight: 550; color: var(--tm-muted); }
.small, .muted { color: var(--tm-muted); font-size: 12px; }
.warning { color: #fca5a5; margin: 5px 0; }
.badge { display:inline-flex; align-items:center; gap:6px; border:1px solid #3f3f46; border-radius:999px; padding:3px 9px; font-size:11px; font-weight:550; white-space:nowrap; }
.grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(230px,1fr)); gap:15px; }
.card { border:1px solid var(--tm-border); border-radius:12px; padding:20px; background:var(--tm-panel); }
.diagram { width:100%; height:min(80vh, 920px); min-height:520px; border:1px solid var(--tm-border); border-radius:12px; background:white; }
.diagram + .diagram { margin-top:18px; }
.tm-app { min-height:100vh; }
.tm-sidebar { width:246px; height:100vh; position:fixed; inset:0 auto 0 0; z-index:40; background:var(--tm-side); border-right:1px solid var(--tm-border); display:flex; flex-direction:column; }
.tm-brand { min-height:73px; display:flex; align-items:center; gap:11px; padding:20px; border-bottom:1px solid var(--tm-border); }
.tm-brand-icon { display:block; width:35px; height:35px; flex:0 0 35px; overflow:hidden; border-radius:10px; background:#07162f; }
.tm-brand-icon img { display:block; width:100%; height:100%; object-fit:cover; }
.tm-brand-name { font-size:14px; font-weight:680; line-height:1.2; }
.tm-brand-sub { font-size:11px; color:var(--tm-muted); line-height:1.5; }
.tm-nav-label { font-size:10px; text-transform:uppercase; letter-spacing:.12em; color:#71717a; font-weight:650; padding:26px 22px 10px; }
.tm-nav { display:flex; flex-direction:column; gap:5px; padding:0 11px; }
.tm-nav-link { display:flex; align-items:center; gap:12px; min-height:41px; border-radius:8px; padding:8px 12px; font-size:13px; font-weight:510; color:#a1a1aa; text-decoration:none; transition:background .15s, color .15s; }
.tm-nav-link:hover { background:var(--tm-hover); color:var(--tm-text); }
.tm-nav-link[aria-current="page"] { background:#252529; color:#fff; font-weight:640; box-shadow:inset 3px 0 0 #80bdff; }
.tm-nav-icon { width:17px; height:17px; flex:0 0 17px; stroke:currentColor; fill:none; stroke-linecap:round; stroke-linejoin:round; stroke-width:1.7; }
.tm-sidebar-foot { margin-top:auto; padding:20px; border-top:1px solid var(--tm-border); font-size:11px; color:#71717a; display:flex; align-items:center; gap:8px; }
.tm-indicator { width:7px; height:7px; border-radius:50%; background:var(--tm-positive); box-shadow:0 0 0 3px rgba(52,211,153,.1); }
.tm-main { margin-left:246px; min-width:0; }
/* Observatory global workspace sidebar: fully hidden state reserves no space. */
.tm-sidebar-toggle { display:grid; place-items:center; margin-left:auto; padding:0; width:29px; height:29px; flex:none; border:1px solid #3f3f46; border-radius:8px; background:#242429; color:#d4d4d8; cursor:pointer; }
.tm-sidebar-toggle:hover { background:#343439; color:white; }
/* The phone header uses a collapsed menu instead of a page-wide horizontal nav. */
.tm-mobile-nav-toggle { display:none; align-items:center; justify-content:center; gap:8px; margin-left:auto; padding:7px 11px; min-height:38px; border-color:#3f3f46; background:#19191d; color:var(--tm-text); font-size:13px; }
.tm-mobile-nav-toggle svg { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.9; stroke-linecap:round; }
.tm-sidebar-toggle:focus-visible { outline:2px solid #d4d4d8; outline-offset:3px; }
.tm-sidebar-toggle svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.tm-sidebar-toggle .tm-sidebar-pane { fill:rgba(212,212,216,.18); stroke:none; transition:fill .14s ease; }
/* Edge hover summons the full navigation without reserving page width. */
.tm-sidebar-edge { display:none; }
.tm-breadcrumb-workspace { appearance:none; border:0; background:transparent;
  padding:3px 0; font:inherit; color:#a1a1aa; cursor:pointer; border-radius:3px; }
.tm-breadcrumb-workspace:hover { color:#e4e4e7; }
.tm-breadcrumb-workspace:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
@media(min-width:901px) {
 body.tm-sidebar-collapsed .tm-sidebar { transform:translateX(calc(-100% - 2px));
   visibility:hidden; pointer-events:none; }
 body.tm-sidebar-collapsed .tm-main { margin-left:0; }
 body.tm-sidebar-collapsed.tm-sidebar-peek .tm-sidebar { transform:none;
   visibility:visible; pointer-events:auto; box-shadow:12px 0 34px #0009; }
 body.tm-sidebar-collapsed .tm-sidebar-edge { position:fixed; top:0; bottom:0;
   left:0; width:10px; z-index:39; display:block; }
}
body.stage-height-focus .tm-sidebar-edge { display:none!important; }


/* Parent navigation is in the ordinary breadcrumb row, never a floating bar. */
.tm-secondary-back { display:inline-flex; align-items:center; gap:7px; flex-shrink:0;
  padding:6px 10px; min-height:33px; box-sizing:border-box;
  border:1px solid #42424a; border-radius:8px; background:#222226;
  color:#f0f0f4; text-decoration:none; font-size:12px; font-weight:600; }
.tm-secondary-back:hover { background:#33333a; color:#fff; text-decoration:none; }
.tm-secondary-back:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-secondary-back svg { width:16px; height:16px; flex:none; stroke:currentColor;
  fill:none; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
.tm-topbar { position:relative; z-index:1; height:62px;
  border-bottom:1px solid var(--tm-border); background:var(--tm-bg);
  display:flex; align-items:center; justify-content:space-between; padding:0 30px; }
.tm-breadcrumb { display:flex; align-items:center; gap:11px; font-size:12px; font-weight:550; color:#71717a; }
.tm-breadcrumb strong { color:#e4e4e7; font-weight:580; }
.tm-breadcrumb-link { color:#a1a1aa; text-decoration:none; }
.tm-breadcrumb-link:hover { color:#93c5fd; text-decoration:underline; }
.tm-breadcrumb-link:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; border-radius:3px; }
.tm-breadcrumb-divider { color:#71717a; }

.tm-breadcrumb { flex-wrap:wrap; row-gap:4px; min-width:0; }
.tm-topbar-right { font-size:11px; color:var(--tm-muted); border:1px solid var(--tm-border); border-radius:999px; padding:4px 10px; }
.tm-content { width:100%; max-width:1800px; margin:0 auto; padding:32px 30px 45px; min-width:0; }
.tm-page-head { margin-bottom:23px; display:flex; align-items:center; justify-content:space-between; gap:12px; }
.tm-page-head p { color:var(--tm-muted); margin:0; font-size:13px; }
.tm-page-label { font-size:10px; text-transform:uppercase; letter-spacing:.13em; color:#71717a; font-weight:650; margin-bottom:8px; }
.tm-quota-section { margin-bottom:32px; }
.tm-quota-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin:0 0 16px; }
.tm-quota-heading h2 { font-size:21px; margin:0 0 5px; }
.tm-quota-heading p { margin:0; }
.tm-quota-section #switch-message:empty { display:none; }
.tm-quota-section #switch-message { margin:10px 0; padding:10px 12px; border:1px solid var(--tm-border); border-radius:8px; background:#19191d; color:#d4d4d8; }
.tm-quota-section .tm-account-card { border:1px solid var(--tm-border); }
.tm-quota-section .tm-account-card.is-active { border-color:#18734a; background:#111e19; }
/* Historical quota percentages only: real reset operations await a live read. */
.tm-quota-section .tm-quota-historical { border-style:dashed; }
.tm-quota-section .tm-quota-cached-label { color:#a1b4c8; margin:4px 0 12px; font-size:11px; }
.tm-quota-heading .tm-usage-sync { color:#9ca3af; font-size:11px; font-weight:500; }

.tm-quota-section .tm-account-header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; margin-bottom:9px; }
.tm-quota-section .tm-account-header h2 { margin:0; }
.tm-quota-section .tm-active-indicator { padding:7px 11px; border:1px solid #18734a; border-radius:999px; background:#123b29; color:#86efac; font-size:12px; font-weight:700; white-space:nowrap; }
.tm-quota-section .tm-account-switch { padding:7px 11px; background:#242429; border:1px solid #52525b; border-radius:8px; font-size:12px; white-space:nowrap; cursor:pointer; }
.tm-quota-section .tm-account-switch:hover:not(:disabled) { background:#34343b; border-color:#a1a1aa; }
.tm-quota-section .tm-account-switch:disabled { opacity:.5; cursor:not-allowed; }
.tm-quota-section .tm-account-switch:focus-visible { outline:2px solid #86efac; outline-offset:2px; }
.tm-quota-section .tm-account-meta { color:#d4d4d8; }
.tm-quota-section .tm-reset-count { margin-bottom:9px; }
.tm-quota-section #usage { grid-template-columns:repeat(2,minmax(0,1fr)); align-items:stretch; }
.tm-quota-section #usage > .card { min-width:0; }
.tm-quota-section #usage .card h2 { font-size:17px; margin:0 0 8px; }
.tm-quota-section #usage .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.tm-quota-section #usage .grid > div { padding:14px; background:#19191d; border:1px solid var(--tm-border); border-radius:9px; min-width:0; }
.tm-quota-section .tm-quota-percent { font-size:30px; line-height:1.2; font-weight:720; letter-spacing:-.05em; margin:10px 0 3px; font-variant-numeric:tabular-nums; }
.tm-quota-section .tm-quota-progress { height:7px; background:#3f3f46; border-radius:999px; overflow:hidden; margin:12px 0; }
.tm-quota-section .tm-quota-progress span { display:block; height:100%; background:#d4d4d8; border-radius:999px; }
.tm-quota-section .reset-credit-button { margin-top:3px; }
.tm-quota-section .reset-credit-button:disabled { opacity:.42; cursor:not-allowed; }
.tm-quota-section #reset-message { margin:12px 0; }
.tm-quota-section .tm-reset-note { margin:12px 0 8px; }
.tm-quota-heading .tm-update-status { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:10px; }
.tm-quota-heading .tm-update-age { display:inline-flex; align-items:center; gap:8px; min-height:36px; padding:7px 12px; background:#18181b; border:1px solid #3f3f46; border-radius:8px; color:#d4d4d8; font-size:12px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.tm-quota-heading .tm-update-dot { display:block; width:7px; height:7px; flex:none; border-radius:50%; background:#34d399; }
.tm-quota-heading .tm-refresh-button { min-height:36px; padding:7px 15px; background:#fafafa; border:1px solid #fafafa; color:#18181b; font-weight:650; border-radius:8px; white-space:nowrap; }
.tm-quota-heading .tm-refresh-button:hover { background:#e4e4e7; border-color:#e4e4e7; }
.tm-quota-heading .tm-refresh-button:focus-visible { outline:2px solid #a1a1aa; outline-offset:3px; }
@media (max-width:1300px) { .tm-quota-section #usage .grid { grid-template-columns:1fr; } }
@media (max-width:680px) { .tm-quota-section #usage { grid-template-columns:1fr; } .tm-quota-heading { flex-direction:column; align-items:stretch; gap:10px; } .tm-quota-heading .tm-update-status { justify-content:space-between; } }
.tm-workflow-view .tm-nav-label, .tm-workflow-view .tm-sidebar-foot { display:none; }
.tm-workflow-view .tm-content { max-width:none; padding:18px 22px 32px; }
.tm-workflow-view .tm-page-head { margin:0 0 13px; }
.tm-workflow-view .tm-page-head h1 { margin:0; font-size:24px; }
.tm-workflow-view .tm-page-label, .tm-workflow-view .tm-page-head p { display:none; }
.tm-workflow-view .stage-preview-head { background:transparent; border:0; border-radius:0; padding:0; display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 6px; }
.tm-workflow-view .workflow-version-form { margin:0; flex:0 0 auto; }
.tm-workflow-view .workflow-version-form label { display:inline-flex; align-items:center; gap:9px; font-size:12px; color:var(--tm-muted); }
.tm-workflow-view .workflow-version-form select { border:1px solid #3f3f46; padding:8px 10px; min-height:37px; border-radius:8px; font-size:12px; }
.tm-workflow-view .stage-view-toggle { display:flex; align-items:center; gap:3px; border:1px solid #34343a; background:#131316; border-radius:9px; padding:3px; }
.tm-workflow-view .stage-view-toggle a { padding:6px 13px; border-radius:7px; color:#a1a1aa; text-decoration:none; font-size:12px; white-space:nowrap; }
.tm-workflow-view .stage-view-toggle a[aria-current="page"] { background:#e4e4e7; color:#18181b; font-weight:700; }
.tm-workflow-view .stage-help-link { font-size:12px; color:var(--tm-muted); text-decoration:none; }
.tm-workflow-view .stage-view-hint { font-size:11px; color:var(--tm-muted); margin:0 0 10px; }
.tm-workflow-view .stage-legend-details { margin:0 0 9px; font-size:11px; color:var(--tm-muted); }
.tm-workflow-view .stage-legend-details > summary { cursor:pointer; display:inline-block; }
.tm-workflow-view .stage-legend { margin:8px 0; }
.tm-workflow-view .stage-legend span { font-size:11px; padding:2px 8px; }
.tm-workflow-view .stage-canvas { height:calc(100vh - 180px); min-height:520px; }
.tm-workflow-view .tm-workflow-diagnostics { margin-top:12px; padding:12px 15px; border:1px solid var(--tm-border); border-radius:10px; color:var(--tm-muted); font-size:12px; }
.tm-workflow-view .tm-workflow-diagnostics summary { cursor:pointer; }
.tm-workflow-view .tm-workflow-diagnostics li { padding:4px 0; }
@media (max-width:900px) { .tm-workflow-view .stage-preview-head { justify-content:flex-start; flex-wrap:wrap; } }
@media (max-width:680px) { .tm-workflow-view .tm-content { padding:15px 12px 28px; } .tm-workflow-view .stage-preview-head { gap:10px; } .tm-workflow-view .stage-help-link { margin-left:auto; } .tm-workflow-view .stage-canvas { height:70vh; min-height:500px; } }
/* Run task list and initial read-only TUI cockpit. */
.tm-run-view .tm-content { max-width:1560px; padding:22px 28px 36px; }
.tm-run-view .tm-page-head { margin-bottom:16px; }
.tm-run-view .tm-page-label { display:none; }
.tm-run-view .tm-page-head h1 { font-size:24px; margin:0; }
.tm-run-view .tm-page-head p { display:none; }
.tm-run-workspace { display:flex; flex-direction:column; gap:16px; }
.tm-run-heading { display:flex; align-items:center; justify-content:space-between; gap:14px; }
.tm-run-heading strong { font-size:16px; }
.tm-run-heading h2 { font-size:20px; margin:4px 0 0; font-weight:650; }
.tm-run-heading p { font-size:12px; color:var(--tm-muted); margin:4px 0 0; }
.tm-run-refresh, .tm-run-back { border:1px solid #3f3f46; background:#18181b; border-radius:8px; padding:8px 12px; text-decoration:none; font-size:12px; white-space:nowrap; }
.tm-run-back { padding:0; background:transparent; border:0; color:var(--tm-muted); }
.tm-run-filters { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.tm-run-filters input { min-width:180px; flex:1; background:#18181b; border:1px solid #3f3f46; border-radius:8px; color:var(--tm-text); padding:9px 12px; }
.tm-run-filters select { min-height:39px; max-width:220px; background:#18181b; border:1px solid #3f3f46; border-radius:8px; color:var(--tm-text); padding:8px 11px; }
.tm-run-filters a { color:var(--tm-muted); text-decoration:none; font-size:12px; padding:8px; }
.tm-run-table-wrap { overflow-x:auto; border:1px solid var(--tm-border); border-radius:12px; }
.tm-run-table { border-collapse:collapse; width:100%; table-layout:auto; text-align:left; font-size:13px; }
.tm-run-table th { padding:12px 16px; border-bottom:1px solid var(--tm-border); color:var(--tm-muted); font-weight:550; font-size:11px; white-space:nowrap; background:#141417; }
.tm-run-table td { padding:14px 16px; border-bottom:1px solid #232327; vertical-align:middle; }
.tm-run-table tbody tr:last-child td { border-bottom:0; }
.tm-run-table tbody tr:hover { background:#17171a; }
.tm-run-clickable { cursor:pointer; }
.tm-run-clickable:hover, .tm-run-clickable:focus-visible { background:#202024; }
.tm-run-detail-title { display:flex; align-items:center; flex-wrap:wrap; gap:10px; }
.tm-run-detail-title h2 { margin:0; }
.tm-run-tui-label { color:#18181b; background:#e4e4e7; font-size:10px; padding:4px 8px; border-radius:5px; font-weight:700; }
.tm-run-no-cycle { color:#fcd34d; padding:10px 14px; background:#2a2214; border:1px solid #5b471b; border-radius:8px; margin:0; font-size:12px; }
.tm-run-task-link { text-decoration:none; font-weight:620; overflow-wrap:anywhere; }
.tm-run-secondary, .tm-run-hint { font-size:11px; color:var(--tm-muted); }
.tm-run-secondary { margin-top:3px; }
.tm-run-empty { padding:38px 12px !important; text-align:center; color:var(--tm-muted); }
.tm-run-state { font-size:11px; font-weight:650; letter-spacing:.02em; border:1px solid #3f3f46; border-radius:999px; padding:4px 9px; display:inline-flex; white-space:nowrap; background:#242429; }
.tm-run-state-running { color:#86efac; background:#082b20; border-color:#176647; }
.tm-run-state-attention { color:#fcd34d; background:#2c230e; border-color:#68501e; }
.tm-run-state-error { color:#fda4af; background:#37141b; border-color:#7f2935; }
.tm-run-pagination { display:flex; justify-content:flex-end; align-items:center; gap:17px; font-size:12px; color:var(--tm-muted); }
.tm-run-pagination a { text-decoration:none; color:var(--tm-text); }
.tm-run-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
.tm-run-stat, .tm-run-panel, .tm-run-placeholder { background:var(--tm-panel); border:1px solid var(--tm-border); border-radius:12px; padding:17px 18px; min-width:0; }
.tm-run-stat { display:flex; flex-direction:column; gap:9px; }
.tm-run-stat span { font-size:11px; color:var(--tm-muted); }
.tm-run-stat strong { font-size:17px; overflow-wrap:anywhere; }
.tm-run-panels { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:12px; }
.tm-run-panel h3 { font-size:14px; margin:0 0 15px; }
.tm-run-agents { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }
.tm-run-agent { background:#18181b; border:1px solid #2b2b30; border-radius:8px; padding:12px; display:flex; flex-direction:column; gap:5px; min-width:0; }
.tm-run-agent span { font-size:11px; color:var(--tm-muted); }
.tm-run-agent strong { font-size:13px; }
.tm-run-agent small { color:var(--tm-muted); overflow-wrap:anywhere; }
.tm-run-activity { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.7; }
.tm-run-live { display:flex; flex-direction:column; gap:14px; }
.tm-run-live > .tm-run-panel { min-width:0; }
.tm-run-tabs { display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:16px; border-bottom:1px solid var(--tm-border); padding-bottom:11px; }
.tm-run-tab { font-size:12px; text-decoration:none; color:var(--tm-muted); padding:7px 10px; border-radius:7px; }
.tm-run-tab[aria-current="page"] { background:#27272a; color:var(--tm-text); font-weight:700; }
.tm-run-output { max-height:500px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.65; color:#ddd; background:#18181b; border:1px solid #2b2b30; border-radius:8px; padding:16px; }
.tm-run-events { max-height:500px; overflow:auto; padding:0 0 0 22px; font-size:12px; color:#ddd; }
.tm-run-events li { margin:4px 0; padding:8px 4px; white-space:pre-wrap; overflow-wrap:anywhere; border-bottom:1px solid #222; }
.tm-run-warning { padding:12px; border-radius:8px; background:#32210f; color:#fcd34d; }

/* Current scientific stage: soft green ribbon across every cell, not just the state label. */
.tm-tui-agent-table tbody tr.tm-agent-row-running > td {
  background:rgba(22,101,69,.26);
  border-top:1px solid rgba(75,185,126,.33);
  border-bottom:1px solid rgba(75,185,126,.33);
}
.tm-tui-agent-table tbody tr.tm-agent-row-running:hover > td {
  background:rgba(25,115,77,.33);
}
.tm-tui-agent-table tbody tr.tm-agent-row-running select {
  background:#152b25; border-color:#43735a; color:#f0fff6;
}
/* Run detail: Textual's information hierarchy, Observatory's visual language. */
.tm-run-view:has(.tm-tui-screen) .tm-page-head { display:none; }
.tm-run-view:has(.tm-tui-screen) .tm-content { max-width:1720px; padding:23px 28px 40px; }
.tm-web-tui { gap:16px; }
.tm-web-tui .tm-tui-screen { display:flex; flex-direction:column; gap:15px; min-width:0; min-height:0; background:transparent; color:var(--tm-text); border:0; font:inherit; overflow:visible; }
.tm-tui-primary, .tm-tui-secondary { display:flex; flex-direction:column; gap:15px; min-width:0; }
.tm-tui-titlebar { display:flex; align-items:center; gap:16px; padding:0 1px 3px; min-width:0; }
.tm-tui-title-main { min-width:0; flex:1; display:flex; flex-direction:column; gap:5px; }
.tm-tui-eyebrow { color:var(--tm-muted); font-weight:650; font-size:10px; letter-spacing:.11em; }
.tm-tui-title-main > strong { font-size:23px; font-weight:680; line-height:1.3; letter-spacing:-.025em; overflow-wrap:anywhere; }
.tm-tui-text { display:block; margin:0; padding:0; min-width:0; color:var(--tm-text); background:transparent; font:inherit; white-space:pre-wrap; overflow-wrap:anywhere; }
.tm-tui-title-main .tm-tui-identity { color:var(--tm-muted); font-size:12px; line-height:1.6; }
.tm-tui-state-badge { padding:5px 11px; font-size:11px; flex-shrink:0; }
.tm-run-freshness { color:var(--tm-muted); font-size:10px; line-height:1.35; max-width:185px; text-align:right; }
@media(max-width:720px) { .tm-run-freshness { max-width:130px; font-size:9px; } }
.tm-tui-title-actions { display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-shrink:0; margin-left:auto; }
.tm-run-settings-link { display:inline-flex; justify-content:center; align-items:center;
  width:36px; height:36px; flex:none; border:1px solid #516c8c; border-radius:11px;
  background:#233750; color:#d6e8ff; text-decoration:none; box-sizing:border-box; }
.tm-run-settings-link:hover { background:#315078; border-color:#8fb5e8; }
.tm-run-settings-link:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-run-settings-link svg { display:block; width:21px; height:21px; }
.tm-task-settings-page { max-width:940px; margin:0 auto; padding:4px 0 38px; min-width:0; }
.tm-task-settings-head { display:flex; justify-content:space-between; gap:15px;
  align-items:center; margin:0 0 18px; flex-wrap:wrap; }
.tm-task-settings-head h1 { margin:7px 0 5px; color:#f1f5f9; font-size:23px; }
.tm-task-settings-head p { margin:0; color:#aebdd0; font-size:12px; overflow-wrap:anywhere; }
.tm-task-settings-back { color:#cfe3ff; border:1px solid #485f7f; background:#213049;
  border-radius:8px; text-decoration:none; padding:9px 12px; font-size:12px; }
.tm-task-settings-back:hover { background:#2c4161; }
.tm-settings-task-picker { display:flex; flex-wrap:wrap; align-items:center; gap:9px;
  margin:0 0 16px; padding:13px 14px; background:#1b2637; border:1px solid #435974;
  border-radius:10px; min-width:0; }
.tm-settings-task-picker label { color:#d4e0f1; font-size:12px; font-weight:650; }
.tm-settings-task-picker select { min-width:0; flex:1 1 230px; max-width:100%;
  padding:9px 11px; border:1px solid #586d8a; border-radius:7px;
  background:#141d2b; color:#eff6ff; font-size:12px; }
.tm-settings-task-picker button { padding:9px 13px; border:1px solid #6286b6; border-radius:7px;
  background:#294a73; color:#eff6ff; font-size:12px; cursor:pointer; }
.tm-settings-task-picker button:hover { background:#365d89; }
.tm-task-settings-panel { padding:20px; border:1px solid #3e536e; background:#171e2b;
  border-radius:13px; min-width:0; }
.tm-task-settings-panel > h2 { color:#e0edff; margin:0 0 10px; font-size:16px; }
.tm-task-settings-hint { font-size:12px; line-height:1.75; color:#aebdd0; margin:0 0 18px; }
.tm-settings-audit .tm-setting-gear-submenu { border-radius:11px; padding:16px; background:#1e2d42; }
.tm-settings-audit .tm-setting-gear-submenu > summary { font-size:13px; color:#e7f1ff; }
.tm-settings-audit .tm-setting-gear-audit-controls { max-width:460px; margin:16px 0; }
.tm-settings-audit .tm-setting-detail-item { max-width:720px; }
.tm-settings-audit .tm-setting-model button { cursor:pointer; }
@media(max-width:540px) { .tm-task-settings-panel { padding:14px; } .tm-task-settings-head h1 { font-size:19px; } }
/* A navigational child card belongs inside the Run overview, not its footer. */
.tm-explore-map { display:flex; align-items:center; gap:11px; min-width:0;
  margin:12px 0 17px; padding:12px 15px; border:1px solid #3f5069;
  background:linear-gradient(100deg,#18283b,#171b24 78%);
  border-radius:10px; color:#e2edff; text-decoration:none; }
a.tm-explore-map:hover { border-color:#81b7fa; background:#1c3049; text-decoration:none; }
a.tm-explore-map:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-explore-map-icon { display:grid; place-items:center; width:38px; height:38px;
  border-radius:9px; background:#253f5d; color:#93c5fd; flex:none; }
.tm-explore-map-icon svg { width:21px; height:21px; fill:none; stroke:currentColor;
  stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.tm-explore-map-body { display:flex; flex-direction:column; gap:3px; min-width:0; flex:1; }
.tm-explore-map-body strong { font-size:13px; font-weight:680; color:#dbeafe; }
.tm-explore-map-body > span { font-size:11px; color:#b0bdd1; overflow-wrap:anywhere; }
.tm-explore-map-arrow { color:#93c5fd; font-size:17px; flex:none; }
.tm-explore-map-unavailable { border-color:#343841; background:#1b1c21; cursor:default; }
.tm-explore-map-unavailable .tm-explore-map-body strong { color:#c4c9d2; }
/* Explore map is a peer of State/Cycle/Codex on the overview grid's first row. */
.tm-tui-kv-grid .tm-explore-map-card { padding:0; overflow:hidden; }
.tm-tui-kv-grid .tm-explore-map { height:100%; min-height:69px; margin:0;
  padding:8px 10px; border:0; border-radius:inherit; background:transparent;
  gap:8px; }
.tm-tui-kv-grid a.tm-explore-map:hover { background:#1c3049; }
.tm-tui-kv-grid .tm-explore-map-icon { width:29px; height:29px; border-radius:7px; }
.tm-tui-kv-grid .tm-explore-map-icon svg { width:17px; height:17px; }
.tm-tui-kv-grid .tm-explore-map-body strong { font-size:12px; }
.tm-tui-kv-grid .tm-explore-map-body > span { font-size:10px; line-height:1.35; }
.tm-tui-kv-grid .tm-explore-map-arrow { font-size:13px; }

.tm-tui-panel { min-width:0; border:1px solid var(--tm-border); background:var(--tm-panel); border-radius:12px; padding:14px 17px; }
.tm-tui-section-title { color:var(--tm-text); font-size:13px; font-weight:680; letter-spacing:-.01em; margin:0 0 10px; }
.tm-tui-section-title > span { margin-left:10px; color:var(--tm-muted); font-size:11px; font-weight:400; }
.tm-tui-header { display:flex; flex-direction:column; gap:0; }
.tm-tui-header-stats { min-width:0; }
/* Run overview: one compact five-hour usage bar for the currently selected Codex account. */
/* Codex quota lives IN the same responsive grid as other Run overview metrics. */
.tm-run-quota { min-width:0; }
.tm-run-quota-head { display:flex; align-items:baseline; justify-content:space-between; gap:5px; }
.tm-run-quota .tm-run-quota-account { color:var(--tm-muted); font-weight:550; font-size:11px; min-width:0; margin-bottom:2px; }
.tm-run-quota .tm-run-quota-value { color:var(--tm-text); font-size:12px; font-weight:640; line-height:1.45; font-variant-numeric:tabular-nums; }
/* Entire quota tile is one native navigation link, matching Explore map. */
.tm-tui-kv-grid .tm-run-quota { padding:0; overflow:hidden; }
.tm-run-quota-card-link { display:block; height:100%; min-height:100%; padding:8px 11px;
  text-decoration:none; color:inherit; border-radius:inherit; }
.tm-run-quota-card-link:hover { text-decoration:none; background:#1c3049; }
.tm-run-quota-card-link:focus-visible { outline:2px solid #93c5fd; outline-offset:-3px; }
.tm-run-quota-track { height:4px; margin:5px 0 4px; border-radius:999px; background:#3f3f46; overflow:hidden; }
.tm-run-quota-track > span { display:block; height:100%; width:0; background:#818cf8; border-radius:999px; transition:width .25s ease; }
.tm-run-quota-week { font-size:11px; color:var(--tm-muted); font-variant-numeric:tabular-nums; line-height:1.4; }
.tm-run-quota-usage-row { display:flex; align-items:baseline; justify-content:space-between;
  gap:6px 12px; flex-wrap:wrap; margin-top:3px; }
.tm-run-quota-usage-row > * { min-width:0; white-space:nowrap; }



/* Human-readable automatic Codex quota-controller status, separate from usage. */
.tm-quota-auto { margin:10px 0 2px; padding:10px 13px;
  border:1px solid #38475b; border-radius:9px; background:#171e2b; min-width:0; }
.tm-quota-auto-toolbar { display:grid; grid-template-columns:minmax(158px,1fr) minmax(0,2fr);
  align-items:stretch; gap:7px; }
.tm-quota-auto-overview { display:flex; align-items:center; justify-content:space-between; gap:7px; min-width:0; }
.tm-quota-auto-head { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.tm-quota-auto-dot { align-self:center; display:block; width:7px; height:7px; border-radius:50%;
  flex:none; background:#7dd3fc; }
.tm-quota-auto-wait .tm-quota-auto-dot { background:#fbbf24; }
.tm-quota-auto-off .tm-quota-auto-dot { background:#a1a1aa; }
.tm-quota-auto-name { color:#e1e9f8; font-size:11px; font-weight:650; }
.tm-quota-auto-state { display:block; font-size:11px; color:#b8d8ff; }
.tm-quota-auto-wait .tm-quota-auto-state { color:#fcd34d; }
.tm-quota-auto-off .tm-quota-auto-state { color:#c3c7cd; }
.tm-quota-auto-facts { margin:0; display:flex; flex-wrap:wrap; gap:3px 9px;
  font-size:10px; color:#9eacc0; }
.tm-quota-auto-facts strong { color:#e0e8f4; font-variant-numeric:tabular-nums; }
.tm-quota-auto-more { margin:7px 0 0 15px; }
.tm-quota-auto-more > summary { cursor:pointer; width:max-content; max-width:100%;
  font-size:11px; color:#9fc5fa; }
.tm-quota-auto-more > summary:focus-visible { outline:2px solid #60a5fa; outline-offset:2px; }
.tm-quota-auto-accounts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  gap:9px; margin-top:10px; }
.tm-quota-auto-account { min-width:0; border:1px solid #344255; background:#192435;
  border-radius:8px; padding:10px; }
.tm-quota-auto-account-header { display:flex; justify-content:space-between;
  align-items:baseline; gap:8px; }
.tm-quota-auto-account-header strong { font-size:12px; color:#d6e5ff; }
.tm-quota-auto-account-header span { font-size:10px; color:#93c5fd; }
.tm-quota-auto-check { margin:5px 0 9px; font-size:10px; color:#abb8c8; }
.tm-quota-auto-window + .tm-quota-auto-window { margin-top:8px; }
.tm-quota-auto-meter-label { display:flex; justify-content:space-between;
  flex-wrap:wrap; gap:5px; font-size:10px; color:#a8b4c5; }
.tm-quota-auto-meter-label strong { font-variant-numeric:tabular-nums; color:#e1e9f8; }
.tm-quota-auto-meter { height:5px; border-radius:999px; overflow:hidden;
  margin-top:5px; background:#354156; }
.tm-quota-auto-meter > span { display:block; height:100%; border-radius:999px; background:#7baaf4; }
.tm-quota-auto-reset { margin-top:4px; font-size:10px; color:#8c9daf; }
.tm-quota-auto-note { font-size:10px; line-height:1.6; margin:8px 0; color:#a6b4c6; }
.tm-quota-auto-link { font-size:11px; text-decoration:none; color:#9fc5fa; }
.tm-quota-auto-link:hover { text-decoration:underline; }
@media(max-width:650px) { .tm-quota-auto-accounts { grid-template-columns:minmax(0,1fr); } }

.tm-tui-kv-grid { margin:0; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; }
.tm-tui-kv { min-width:0; padding:8px 11px; border:1px solid var(--tm-border); background:#19191d; border-radius:9px; }
.tm-tui-kv dt { color:var(--tm-muted); font-size:11px; font-weight:500; margin-bottom:2px; }
.tm-tui-kv dd { margin:0; color:var(--tm-text); font-size:13px; font-weight:640; line-height:1.45; overflow-wrap:anywhere; font-variant-numeric:tabular-nums; }
.tm-tui-kv:nth-child(4) dd, .tm-tui-kv:nth-child(6) dd { font-weight:550; font-size:12px; }
.tm-tui-process { display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border-radius:6px; font-size:12px; font-weight:700; letter-spacing:.015em; }
.tm-tui-process:before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }
/* RUNNING launches a native modal; action buttons are never embedded
   inside the narrow Process metric tile. */
.tm-tui-process[data-tui-process-actions] { appearance:none; font:inherit; font-weight:700;
  cursor:pointer; text-align:left; border-radius:7px; }
.tm-tui-process[data-tui-process-actions]:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-tui-process[data-tui-process-actions]:hover { filter:brightness(1.18); }
.tm-process-chevron { margin-left:3px; font-size:10px; }
.tm-tui-process-red { color:#fda4af; background:#37141b; border:1px solid #7f2935; }
.tm-tui-process-green { color:#86efac; background:#082b20; border:1px solid #176647; }
.tm-tui-process-amber { color:#fcd34d; background:#2c230e; border:1px solid #68501e; }
.tm-tui-cycle-edit { margin-left:9px; color:#d4d4d8; border-color:#3f3f46; background:#242429; padding:4px 7px; border-radius:6px; font-weight:500; font-size:11px; }
.tm-tui-cycle-edit:hover { background:#333338; }

.tm-tui-summary-extra { color:#e4e4e7; margin:7px 0 0; padding:8px 3px; font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.55; }
.tm-tui-summary-details { background:#242015; border:1px solid #5b471e; border-radius:8px; padding:8px 11px; margin:9px 0 0; color:#fcd9a1; }
.tm-tui-summary-details > summary { font-size:11px; cursor:pointer; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.tm-tui-workflow-title { margin:12px 0 8px; padding-top:12px; border-top:1px solid var(--tm-border); }
.tm-tui-table-scroll { overflow-x:auto; }
.tm-tui-agent-table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:12px; min-width:790px; }
.tm-tui-agent-table .tm-col-role { width:145px; }
.tm-tui-agent-table .tm-col-model { width:155px; }
.tm-tui-agent-table .tm-col-effort { width:96px; }
.tm-tui-agent-table .tm-col-cost { width:115px; }
.tm-tui-agent-table .tm-col-tokens { width:174px; }
.tm-tui-agent-table .tm-col-time { width:104px; }
.tm-tui-agent-table th { position:static; background:#19191d; color:var(--tm-muted); font-size:11px; font-weight:550; white-space:nowrap; border-bottom:1px solid var(--tm-border); text-align:left; padding:7px 11px; }
.tm-tui-agent-table td { border-bottom:1px solid var(--tm-border); padding:7px 11px; white-space:nowrap; font-variant-numeric:tabular-nums; }
.tm-tui-agent-table tbody tr:last-child td { border-bottom:0; }
.tm-tui-agent-table tbody tr:hover { background:#1d1d21; }
.tm-tui-agent-table td:first-child { font-weight:600; }
.tm-tui-agent-table .tm-tui-agent-identity > strong { display:block; }
.tm-tui-agent-table .tm-tui-agent-identity .tm-tui-agent-state { margin-top:6px; }
.tm-tui-agent-table select { color:var(--tm-text); background:#19191d; border:1px solid #3f3f46; border-radius:7px; padding:6px 9px; max-width:155px; width:100%; font-size:12px; }
.tm-tui-model-select { min-width:0; }
.tm-model-preset-tier { display:inline-block; margin-left:6px; padding:2px 6px; border:1px solid var(--tm-line,#475569); border-radius:5px; font-size:11px; white-space:nowrap; opacity:.88; vertical-align:middle; }
/* The preset is for the entire 6-stage configuration and belongs under
   the MODEL heading. Wrap long overlay/pending labels inside this one header
   cell instead of spilling into the EFFORT heading or each Agent row. */
.tm-tui-agent-table thead [data-model-preset-tier] {
  display:block; width:fit-content; max-width:100%; box-sizing:border-box;
  margin:4px 0 0; white-space:normal; overflow-wrap:anywhere; line-height:1.35;
}


.tm-tui-agent-table th:nth-child(2), .tm-tui-agent-table td:nth-child(2),
.tm-tui-agent-table th:nth-child(3), .tm-tui-agent-table td:nth-child(3) { padding-left:6px; padding-right:6px; }
.tm-tui-agent-table [data-route-effort] { display:block; margin-left:auto; max-width:82px; padding-left:4px; padding-right:4px; }
.tm-tui-pending { display:block; color:#fcd34d; margin:4px 0 0; white-space:normal; font-size:10px; }
.tm-tui-default, .tm-tui-route-set { display:inline-block; margin:5px 0 0; padding:2px 6px; border:1px solid #3f3f46; border-radius:6px; background:#242429; color:#a1a1aa; font-size:10px; }
.tm-tui-route-set { color:#c4b5fd; border-color:#55446a; }

.tm-tui-route-apply { color:#e4e4e7; background:#242429; border:1px solid #3f3f46; border-radius:7px; padding:6px 10px; font-size:11px; }
.tm-tui-route-apply:disabled { opacity:.6; }
.tm-tui-agent-table th:last-child, .tm-tui-agent-table td:last-child { text-align:right; }

.tm-tui-agent-table th:nth-child(3), .tm-tui-agent-table td:nth-child(3), .tm-tui-agent-table th:nth-child(4), .tm-tui-agent-table td:nth-child(4) { text-align:right; }
.tm-tui-agent-table th:nth-child(5), .tm-tui-agent-table td:nth-child(5),
.tm-tui-agent-table th:nth-child(6), .tm-tui-agent-table td:nth-child(6) { text-align:right; }
.tm-usage-cell { width:174px; min-width:0; }
.tm-usage-value { display:block; color:#ddd6fe; font-weight:700; font-variant-numeric:tabular-nums; }
.tm-usage-cycle { display:block; color:#a1a1aa; margin-top:2px; font-size:10px; }
.tm-usage-cost { white-space:nowrap; color:#e4e4e7; font-variant-numeric:tabular-nums; }
.tm-usage-hit { display:block; width:100%; max-width:160px; margin-left:auto; box-sizing:border-box; cursor:pointer; padding:5px 8px; border:1px solid #414354; border-radius:8px; background:#23232e; color:#d4d4d8; text-align:right; font:inherit; }
.tm-usage-hit:not(.tm-usage-hit-empty):hover, .tm-usage-hit[aria-expanded="true"] { background:#30304a; border-color:#7c85b5; }
.tm-usage-hit-empty { cursor:default; }
.tm-usage-hit-empty .tm-usage-value { color:#aaa4c9; }
.tm-usage-hit:focus-visible { outline:2px solid #a5b4fc; outline-offset:2px; }
.tm-usage-popover { position:fixed; z-index:2200; width:310px; max-width:calc(100vw - 24px); max-height:calc(100vh - 24px); overflow-y:auto; border:1px solid #636580; box-shadow:0 16px 44px #000b; background:#24242c; color:#e4e4e7; border-radius:11px; padding:12px; font-size:12px; }
.tm-usage-popover-head { display:flex; align-items:center; justify-content:space-between; gap:15px; margin-bottom:9px; }
.tm-usage-popover-head button { padding:3px 7px; background:#32323e; border:1px solid #4d4f65; color:#e4e4e7; border-radius:6px; cursor:pointer; }
.tm-usage-popover dl { margin:0; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:7px 12px; font-variant-numeric:tabular-nums; }
.tm-usage-popover dt { color:#a1a1aa; }
.tm-usage-popover dd { margin:0; text-align:right; font-weight:600; }
.tm-usage-popover-note { margin:10px 0 0; padding-top:8px; border-top:1px solid #444451; color:#a1a1aa; font-size:10px; line-height:1.5; }

.tm-usage-empty { color:#80808a; font-size:11px; }
.tm-tui-agent-state { display:inline-flex; min-width:63px; justify-content:center; padding:2px 8px; border:1px solid #3f3f46; background:#242429; border-radius:999px; color:#d4d4d8; font-size:11px; }
.tm-tui-state-running { color:#86efac; background:#082b20; border-color:#176647; }
.tm-tui-state-waiting, .tm-tui-state-rework, .tm-tui-state-blocked { color:#fcd34d; background:#2c230e; border-color:#68501e; }
.tm-tui-state-failed { color:#fda4af; background:#37141b; border-color:#7f2935; }
.tm-tui-state-done { color:#e4e4e7; }
.tm-tui-state-idle { color:#a1a1aa; }
/* Restore the original left activity/history and right stacked science cards. */
.tm-recent-event-scroll { height:320px; max-height:50dvh; overflow-y:auto; overscroll-behavior:contain; scrollbar-width:thin; scrollbar-color:#52525b transparent; }
.tm-tui-activity-col .tm-log-item:has(> .tm-recent-select) { display:block; padding:0; }
.tm-recent-select { text-decoration:none; }
.tm-recent-select { display:grid; grid-template-columns:65px minmax(0,1fr); gap:10px; align-items:start; width:100%; text-align:left; border:0; border-radius:6px; padding:8px 7px 8px 16px; color:inherit; background:transparent; font:inherit; cursor:pointer; }
.tm-recent-select:hover { background:#292934; }
.tm-recent-select[aria-pressed="true"] { background:#303249; box-shadow:inset 2px 0 #a5b4fc; }
.tm-recent-select:focus-visible { outline:2px solid #818cf8; outline-offset:-2px; }
.tm-run-event-disclosure { border-color:#3e435a; }
.tm-run-event-disclosure > summary { font-size:12px; font-weight:600; cursor:pointer; color:var(--tm-muted); line-height:1.6; }
.tm-run-event-disclosure > summary span { font-size:11px; font-weight:400; margin-left:9px; }
.tm-run-event-disclosure[open] > summary { margin-bottom:12px; color:var(--tm-text); }
.tm-recent-event-status { font-size:11px; line-height:1.55; color:var(--tm-muted); padding:0 0 10px; }
.tm-recent-event-scroll { padding:0 9px 10px 0; }
.tm-recent-event-card { background:#17191f; border:1px solid #31333c; border-radius:9px; padding:11px 12px; margin-bottom:10px; }
.tm-recent-event-target { border-color:#818cf8; background:#252943; }
.tm-recent-event-header { display:flex; flex-wrap:wrap; justify-content:space-between; gap:5px 11px; align-items:baseline; margin-bottom:8px; font-size:11px; }
.tm-recent-event-header strong { color:#d8dffc; font-weight:670; }
.tm-recent-event-header span { color:#9ca3af; font-variant-numeric:tabular-nums; }
.tm-recent-event-card pre { font:11px/1.65 ui-monospace,Menlo,Consolas,monospace; color:#d4d4dd; white-space:pre-wrap; overflow-wrap:anywhere; margin:0; }
/* Exact Events deep links spotlight their persisted source message,
   while keeping the original Stage Conversation dialogue layout. */
.tm-stage-event-item.tm-stage-jump-target {
  border:2px solid #818cf8; border-radius:10px; padding:7px 8px;
  margin:8px 0; background:rgba(99,102,241,.10);
  box-shadow:0 0 0 3px rgba(129,140,248,.16);
}
.tm-stage-event-item.tm-stage-jump-target .tm-stage-milestone {
  background:rgba(129,140,248,.13);
}
.tm-stage-sdk-node.tm-stage-jump-target {
  border:2px solid #818cf8; background:rgba(99,102,241,.10);
}
.tm-tui-mid { display:grid; grid-template-columns:minmax(0,1.3fr) minmax(310px,.86fr); gap:15px; align-items:start; min-width:0; }
.tm-tui-activity-col, .tm-tui-center { display:flex; flex-direction:column; gap:15px; min-width:0; }
.tm-tui-history-panel { min-width:0; }

/* Important-events ledger and ordinary Recent Progress are sibling readers
   inside the same fixed-height history card; never duplicate screen regions. */
.tm-history-switch { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:0 0 11px; }
.tm-history-tab { font-size:12px; font-weight:650; line-height:1.35; font-family:inherit; border:1px solid transparent; border-radius:7px; padding:7px 11px; color:#9da8b9; background:transparent; cursor:pointer; }
.tm-history-tab[aria-selected="true"] { border-color:#405070; color:#bcd7ff; background:#1c2431; }
.tm-history-tab:hover { color:#e1e9fa; background:#222935; }
.tm-history-tab:focus-visible { outline:2px solid #60a5fa; outline-offset:2px; }
.tm-tui-history-panel [data-history-panel][hidden] { display:none; }
/* Important Events uses a single fluid column: type -> metadata -> reason.
   Never inherit the two-column time/content grid from ordinary progress rows. */
.tm-log-item.tm-important-item { display:block; grid-template-columns:none; padding:12px 8px 13px 16px; min-width:0; --important-accent:#94a3b8; }
.tm-important-item + .tm-important-item { border-top:1px solid #272e39; }
.tm-important-item.tm-important-minor { --important-accent:#a5b4fc; }
.tm-important-item.tm-important-attention { --important-accent:#fbbf24; }
.tm-important-item.tm-important-error { --important-accent:#fb7185; }
.tm-important-item.tm-important-important { --important-accent:#60a5fa; }
.tm-important-item::before { background:var(--important-accent); }
.tm-important-item .tm-important-content { min-width:0; max-width:100%; overflow-wrap:anywhere; }
.tm-important-item .tm-important-heading { display:block; margin:0 0 5px; min-width:0; }
.tm-important-item .tm-important-kind { display:block; color:#e8edf5; font-size:13px; font-weight:700; line-height:1.45; }
.tm-important-item .tm-important-meta { display:flex; align-items:baseline; flex-wrap:wrap; gap:3px 12px; margin:0 0 4px; min-width:0; }
.tm-important-item .tm-important-meta time { font-size:11px; color:#a3afbd; font-variant-numeric:tabular-nums; white-space:normal; }
.tm-important-item .tm-important-meta span { font-size:11px; color:#9ba7b9; overflow-wrap:anywhere; }
.tm-important-item .tm-important-meta .tm-important-lifecycle { color:var(--important-accent); }
.tm-important-item .tm-important-reason { margin:4px 0 0; color:#c9d9ee; font-size:11px; line-height:1.55; }
.tm-important-item .tm-important-summary { margin:5px 0 0; font-size:11px; color:#c8d0df; line-height:1.6; white-space:pre-wrap; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; overflow:hidden; }
.tm-important-item .tm-important-more { margin-top:7px; min-width:0; }
.tm-important-item .tm-important-more > summary { width:max-content; max-width:100%; cursor:pointer; font-size:11px; color:#92b6df; }
.tm-important-item .tm-important-more > pre { margin:6px 0 0; padding:10px; background:#111822; border:1px solid #343e50; border-radius:7px; font:10px/1.55 ui-monospace,Menlo,monospace; color:#b9c8da; white-space:pre-wrap; overflow-wrap:anywhere; max-height:230px; overflow:auto; }
.tm-important-review-link { display:inline-flex; align-items:center; margin-top:7px; padding:5px 9px; border:1px solid #526783; border-radius:6px; color:#c7dcf9; background:#26354c; font-size:11px; line-height:1.45; font-family:inherit; cursor:pointer; }
.tm-important-review-link:hover,.tm-important-review-link:focus-visible { background:#354a67; outline-color:#a7c8f4; }
#tm-important-review-dialog { max-height:85vh; overflow:auto; }
#tm-important-review-dialog [data-important-review-close] { padding:6px 12px; cursor:pointer; }
#tm-important-review-dialog pre { max-height:42vh; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; }
.tm-important-sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
.tm-insight-focus { border-left:3px solid #64748b; }
.tm-insight-focus.tm-insight-alert { border-left-color:#fb7185; }
.tm-focus-top { display:flex; align-items:center; flex-wrap:wrap; gap:9px 12px; margin:4px 0 15px; }
.tm-focus-mark { display:inline-flex; padding:3px 9px; border:1px solid #4b5563; border-radius:7px; background:#252630; color:#d4d4d8; font:600 11px/1.5 ui-monospace,Menlo,Consolas,monospace; }
.tm-insight-alert .tm-focus-mark { color:#fda4af; border-color:#823643; background:#3a1c25; }
.tm-focus-sub { color:var(--tm-muted); font-size:11px; }
.tm-focus-label { color:var(--tm-muted); font-size:11px; margin-bottom:5px; }
.tm-focus-message { margin:0; font-size:13px; line-height:1.65; color:var(--tm-text); overflow-wrap:anywhere; }
.tm-focus-latest { display:block; margin-top:10px; padding:8px 10px; border:1px solid #3f3f46; border-radius:8px; background:#202024; color:#fafafa; text-decoration:none; }
.tm-focus-latest span { display:block; color:#a1a1aa; font-size:11px; }
.tm-focus-latest strong { display:block; margin-top:3px; font-size:12px; font-weight:550; line-height:1.5; overflow-wrap:anywhere; }
.tm-focus-latest:hover { background:#2a2a32; }
.tm-insight-alert .tm-focus-message { color:#fecdd3; }
.tm-run-events-scroll { height:320px; max-height:50dvh; overflow-y:auto; overflow-x:hidden; overscroll-behavior-y:contain; scrollbar-width:thin; scrollbar-color:#52525b transparent; min-width:0; padding-right:8px; }
.tm-run-events-scroll:focus-visible { outline:2px solid #818cf8; outline-offset:2px; border-radius:6px; }
.tm-run-events-recent { list-style:none; margin:4px 0 0; padding:0 0 0 5px; }
/* Compact Cycle tree: status via text hierarchy, not loud banner fills. */
.tm-run-cycle-groups { display:flex; flex-direction:column; gap:5px; padding:2px 2px 10px 0; }
.tm-cycle-group { border:1px solid #373c48; border-radius:8px; background:#171a21; overflow:hidden; min-width:0; }
.tm-cycle-group[open] { border-color:#485876; }
.tm-cycle-summary { display:flex; align-items:center; gap:8px; min-height:36px; padding:7px 10px; background:#1b1e27; color:#e4e4e7; cursor:pointer; list-style:none; }
.tm-cycle-summary::-webkit-details-marker { display:none; }
.tm-cycle-summary:focus-visible { outline:2px solid #7ca8eb; outline-offset:-2px; }
.tm-cycle-summary:hover { background:#232a35; }
.tm-cycle-summary::before { content:""; width:5px; height:5px; border-radius:50%; flex:none; background:#6587b4; }
.tm-cycle-group[open] > .tm-cycle-summary { background:#202633; }
.tm-cycle-group[open] > .tm-cycle-summary::before { background:#60a5fa; }
.tm-cycle-history-placeholder { margin:0; padding:10px 15px; color:#9baac2; font-size:11px; line-height:1.6; }
.tm-cycle-history-total { margin:1px 2px 8px; font-size:11px; color:#9baac2; }
.tm-cycle-label { font-size:12px; font-weight:690; color:#dbeafe; white-space:nowrap; }
.tm-cycle-summary time { color:#8996a8; font-size:10px; font-variant-numeric:tabular-nums; }
.tm-cycle-verdict { color:#93c5fd; font-size:10px; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.tm-cycle-count { margin-left:auto; color:#a3aab7; font-size:10px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.tm-cycle-chevron { font-size:14px; color:#9098a6; transition:transform .15s ease; }
.tm-cycle-group[open] .tm-cycle-chevron { transform:rotate(180deg); }
.tm-cycle-group > .tm-run-events-recent { margin:0; padding:4px 10px 7px 15px; }
.tm-cycle-reentered { color:#9facbf; }
.tm-log-item { position:relative; display:grid; grid-template-columns:65px minmax(0,1fr); gap:10px; padding:9px 0 9px 16px; border-left:1px solid #45454e; font-size:12px; line-height:1.5; }
.tm-log-item::before { content:""; position:absolute; width:7px; height:7px; border-radius:50%; background:#71717a; border:2px solid var(--tm-panel); left:-4px; top:14px; }
.tm-log-item.tm-log-milestone::before { background:#818cf8; }
.tm-log-item.tm-log-attention::before { background:#fb7185; }
.tm-log-item time { color:#a1a1aa; font-variant-numeric:tabular-nums; font-size:11px; }
.tm-log-item span { color:#dedee5; overflow-wrap:anywhere; }
.tm-log-attention span { color:#fda4af; }
/* Reuse the Agent table palette for explicitly attributed timeline events. */
.tm-log-item[class*="tm-role-"] { border-left-color:var(--role-accent); background:linear-gradient(90deg,var(--role-background),transparent 68%); }
.tm-log-item[class*="tm-role-"]::before { background:var(--role-accent); }
.tm-log-item .tm-log-role { color:var(--role-accent); font-weight:680; }
.tm-cycle-tail-divider { display:flex; align-items:center; flex-wrap:wrap; gap:5px 9px; padding:8px 10px; margin:5px 0 3px; border-top:1px dashed #505d6b; color:#93a9c4; font-size:11px; }
.tm-cycle-tail-divider span { font-weight:660; }
.tm-cycle-tail-divider small { color:#8492a4; font-size:10px; }
.tm-log-item.tm-log-attention { border-left-color:#fb7185; }
.tm-log-item.tm-log-attention::before { background:#fb7185; }
.tm-log-item.tm-log-attention > span { color:#fda4af; }
.tm-insight-more { margin-top:13px; border-top:1px solid var(--tm-border); padding-top:10px; font-size:11px; color:var(--tm-muted); }
.tm-insight-more > summary { cursor:pointer; list-style:none; display:flex; align-items:center; gap:6px; width:max-content; max-width:100%; }
.tm-insight-more > summary::before { content:"⌄"; display:inline-block; transition:transform .15s ease; }
.tm-insight-more[open] > summary::before { transform:rotate(180deg); }
.tm-insight-more pre { color:#c4c4cc; white-space:pre-wrap; overflow-wrap:anywhere; font:11px/1.7 ui-monospace,Menlo,Consolas,monospace; max-height:330px; overflow:auto; margin:11px 0 0; padding:11px; background:#17171b; border:1px solid #35353d; border-radius:7px; }
.tm-insight-empty { color:#92929d; font-size:12px; line-height:1.6; margin:6px 0; }
.tm-insight-stats { display:flex; gap:13px; flex-wrap:wrap; margin:12px 0 14px; }
.tm-insight-stat { display:flex; align-items:baseline; gap:7px; }
.tm-insight-stat span { color:var(--tm-muted); font-size:11px; }
.tm-insight-stat strong { font-size:14px; font-weight:650; }
.tm-budget-label { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; color:var(--tm-muted); font-size:11px; margin:0 0 9px; }
.tm-budget-label strong { font-weight:550; color:#d4d4d8; font-variant-numeric:tabular-nums; }
/* Budget review is a native, read-only modal; it never modifies allowances. */
.tm-budget-review-count { color:#d6b5fd; background:rgba(99,102,241,.15);
  padding:3px 7px; border:1px solid #69609c; border-radius:6px;
  font-size:10px; font-weight:650; line-height:1.5; font-family:inherit;
  white-space:nowrap; cursor:pointer; }
.tm-budget-review-count:hover { background:rgba(192,132,252,.20); color:#f0dcff; }
.tm-budget-review-count:focus-visible { outline:2px solid #c084fc; outline-offset:2px; }
.tm-budget-review-dialog { box-sizing:border-box; width:min(760px,calc(100vw - 32px));
  max-width:calc(100vw - 32px); max-height:min(78dvh,820px); margin:auto;
  padding:22px; background:#171e2b; color:#e8edf5;
  border:1px solid #62748b; border-radius:14px; box-shadow:0 22px 75px #000a;
  overflow-y:auto; overscroll-behavior:contain; }
.tm-budget-review-dialog::backdrop { background:rgba(0,0,0,.74); }
.tm-budget-review-dialog .tm-budget-modal-header { display:flex; justify-content:space-between;
  align-items:flex-start; gap:16px; margin:0 0 17px; }
.tm-budget-modal-header h2 { margin:0 0 6px; font-size:17px; color:#c084fc; }
.tm-budget-modal-header p { margin:0; color:#a7b4c7; font-size:11px; }
.tm-budget-modal-close { background:#283248; border:1px solid #556783; color:#dce7f8;
  border-radius:8px; padding:7px 13px; font:inherit; font-size:12px; cursor:pointer; }
.tm-budget-modal-close:focus-visible,.tm-budget-modal-choices button:focus-visible {
  outline:2px solid #c084fc; outline-offset:2px; }
.tm-budget-modal-choices { display:flex; gap:7px; flex-wrap:wrap; margin:0 0 15px; }
.tm-budget-modal-choices button { padding:6px 11px; border-radius:7px;
  color:#b9c6d8; border:1px solid #455369; background:#202b3e;
  font:inherit; font-size:11px; cursor:pointer; }
.tm-budget-modal-choices button[aria-pressed="true"] { color:#eedcff;
  border-color:#c084fc; background:#332341; }
.tm-budget-modal-review[hidden] { display:none; }
.tm-budget-modal-verdict { display:flex; justify-content:space-between;
  flex-wrap:wrap; align-items:baseline; gap:8px; margin-bottom:15px; }
.tm-budget-modal-verdict strong { font-size:14px; }
.tm-budget-verdict-approved { color:#86efac; }
.tm-budget-verdict-rejected { color:#fb7185; }
.tm-budget-modal-verdict time { color:#b3bbcd; font-size:11px; }
.tm-budget-modal-figures { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  gap:10px; }
.tm-budget-modal-figures > div { min-width:0; display:flex; flex-direction:column;
  gap:5px; padding:11px 12px; background:#202a3a; border:1px solid #39475d; border-radius:8px; }
.tm-budget-modal-figures span { font-size:11px; color:#a5b5c8; }
.tm-budget-modal-figures strong { font-variant-numeric:tabular-nums;
  font-size:15px; color:#e6eafd; overflow-wrap:anywhere; }
.tm-budget-modal-formula { color:#9faec2; font-size:11px; line-height:1.6; }
.tm-budget-modal-variance { margin:7px 0; padding:8px 10px;
  border-left:2px solid #fbbf24; background:#33291e; border-radius:5px;
  color:#f2d2a4; font-size:11px; line-height:1.6; }
.tm-budget-modal-findings { margin:15px 0 11px; }
.tm-budget-modal-findings h3 { margin:14px 0 5px; font-size:12px; color:#bcd0f0; }
.tm-budget-modal-findings p { margin:0; color:#d0d9e8; line-height:1.75;
  font-size:12px; overflow-wrap:anywhere; white-space:pre-wrap; }
.tm-budget-modal-disclosure { margin:9px 0; background:#1b2535;
  border:1px solid #374459; border-radius:8px; padding:10px 13px; }
.tm-budget-modal-disclosure summary { cursor:pointer; color:#bbd0ed; font-size:12px; }
.tm-budget-modal-disclosure h4 { margin:9px 0 5px; color:#9eabc8; font-size:11px; }
.tm-budget-modal-refs { list-style:none; margin:0; padding:0; max-height:180px; overflow:auto; }
.tm-budget-modal-refs li { margin:4px 0; overflow-wrap:anywhere; }
.tm-budget-modal-refs code { font:10px/1.6 ui-monospace,Menlo,monospace; color:#a9c6ed; }
.tm-budget-modal-reference-columns { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.tm-budget-modal-disclosure pre { margin:9px 0 0; padding:10px;
  background:#101822; color:#d0dcf1; border-radius:6px;
  white-space:pre-wrap; overflow-wrap:anywhere; max-height:260px;
  overflow:auto; font:10px/1.55 ui-monospace,Menlo,monospace; }
.tm-budget-trace-section h3 { color:#d9c7f0; font-size:13px; margin:5px 0 9px; }
.tm-budget-trace-section > button,.tm-budget-trace-more,.tm-budget-trace-item button {
  padding:7px 11px; margin:8px 0; border:1px solid #65738c; border-radius:7px;
  background:#26344b; color:#d5e4fa; cursor:pointer; font:inherit; font-size:12px; }
.tm-budget-trace-info { color:#a8b9d2; font-size:11px; overflow-wrap:anywhere; }
.tm-budget-trace-warning { color:#f6b6ad; font-size:11px; white-space:pre-wrap; }
.tm-budget-trace-block { margin:12px 0; padding:11px; border:1px solid #3e506c;
  border-radius:8px; background:#151e2c; }
.tm-budget-trace-block > strong,.tm-budget-trace-item > strong { font-size:12px; color:#dbe8f8; }
.tm-budget-trace-block pre,.tm-budget-trace-item pre { font:12px/1.65 ui-monospace,monospace;
  white-space:pre-wrap; overflow-wrap:anywhere; max-height:430px; overflow:auto;
  padding:10px; border-radius:6px; background:#101822; }
.tm-budget-trace-list { display:flex; flex-direction:column; gap:12px; margin-top:15px; }
.tm-budget-trace-item { max-width:100%; box-sizing:border-box; }
.tm-budget-trace-item pre { margin:8px 0; }
.tm-budget-trace-content { min-width:0; }
.tm-budget-modal-empty,.tm-budget-modal-footnote { font-size:11px;
  color:#a9b5c6; line-height:1.6; }
@media(max-width:560px) {
  .tm-budget-review-dialog { padding:14px; }
  .tm-budget-modal-reference-columns { grid-template-columns:minmax(0,1fr); }
}

.tm-budget-track { height:6px; width:100%; border-radius:6px; overflow:hidden; background:#31313a; }
.tm-budget-track > span { display:block; height:100%; background:#818cf8; border-radius:6px; }
/* Shared flat card geometry, with each Agent review using its own role palette. */
.tm-tui-center { --tm-science-card-bg:#1c2431; --tm-science-card-border:#60a5fa; --tm-science-card-heading:#60a5fa; --tm-science-card-value:#dbeafe; --tm-science-card-action:#93c5fd; }
.tm-tui-center > .tm-agent-review { border:1px solid var(--role-accent); border-radius:10px; background:var(--role-background); padding:14px 17px; }
.tm-tui-center > .tm-agent-review > .tm-tui-section-title { color:var(--role-accent); }
.tm-tui-center > .tm-agent-review > .tm-tui-section-title span { color:var(--tm-muted); }
.tm-tui-center > .tm-agent-review .tm-insight-stat strong { color:var(--role-accent); }
.tm-tui-center > .tm-agent-review .tm-budget-track > span { background:var(--role-accent); }
.tm-judge-item { margin:10px 0; overflow-wrap:anywhere; }
.tm-judge-item > span { color:var(--tm-muted); font-size:11px; }
.tm-judge-item p { margin:4px 0 0; font-size:12px; line-height:1.6; color:var(--tm-text); }
.tm-judge-rework { margin-left:auto!important; color:#fbbf24!important; }
.tm-token-section { padding:0; overflow:hidden; }
.tm-token-disclosure > summary { list-style:none; display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; cursor:pointer; padding:13px 17px; }
.tm-token-disclosure > summary::-webkit-details-marker { display:none; }
.tm-token-disclosure > summary > strong { font-size:13px; color:var(--tm-text); }
.tm-token-disclosure > summary > span:not(.tm-summary-chevron) { color:var(--tm-muted); font-size:11px; }
.tm-summary-chevron { margin-left:auto; font-size:16px; color:var(--tm-muted); }
.tm-token-disclosure[open] .tm-summary-chevron { transform:rotate(180deg); }
.tm-token-disclosure > .tm-insight-more, .tm-token-scopes, .tm-token-hint { margin-left:17px; margin-right:17px; }
.tm-token-hint { font-size:11px; color:var(--tm-muted); line-height:1.6; }
.tm-token-scopes { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
.tm-token-scope { min-width:0; padding:10px; border:1px solid #373740; background:#19191f; border-radius:8px; }
.tm-token-scope h4 { font-size:11px; margin:0 0 9px; color:#d4d4d8; }
.tm-token-scope > div { display:flex; justify-content:space-between; gap:5px; margin:5px 0; font-size:10px; }
.tm-token-scope > div span { color:#a1a1aa; }
.tm-token-scope > div strong { color:#e4e4e7; font-size:10px; font-weight:600; font-variant-numeric:tabular-nums; }
.tm-token-disclosure > .tm-insight-more { margin-bottom:13px; }
@media(max-width:1280px) { .tm-token-scopes { grid-template-columns:1fr; } }

.tm-tui-footer { display:flex; flex-wrap:wrap; align-items:center; gap:10px 16px; border:1px solid var(--tm-border); background:var(--tm-panel); border-radius:12px; padding:11px 13px; color:var(--tm-muted); }
.tm-tui-shortcuts { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.tm-tui-shortcuts-control { padding-left:14px; border-left:1px solid var(--tm-border); }
.tm-tui-footer button, .tm-tui-footer a { display:inline-flex; align-items:center; gap:7px; color:var(--tm-muted); border:1px solid transparent; background:transparent; border-radius:7px; font:inherit; font-size:11px; text-decoration:none; padding:6px 8px; white-space:nowrap; cursor:pointer; }
.tm-tui-footer button:hover, .tm-tui-footer a:hover { color:var(--tm-text); background:#27272a; border-color:#3f3f46; text-decoration:none; }
.tm-tui-footer kbd { display:inline-grid; place-items:center; padding:1px 5px; min-width:20px; border:1px solid #45454e; background:#27272a; color:#d4d4d8; border-radius:5px; font:600 10px/1.7 ui-monospace,Menlo,Consolas,monospace; }
/* Frozen scientific question: overview entry and dedicated Research reader. */
.tm-hypothesis-card { display:grid; grid-template-columns:auto 1fr auto; align-items:center; column-gap:13px; row-gap:5px; padding:13px 16px; margin:11px 0 12px; border:1px solid var(--tm-science-card-border,#405070); border-radius:10px; background:var(--tm-science-card-bg,#1c2431); color:var(--tm-text); text-decoration:none; min-width:0; }
.tm-hypothesis-card:hover { background:#253146; border-color:#7d9cd6; text-decoration:none; }
.tm-hypothesis-card:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-hypothesis-card-label { grid-column:1/-1; color:var(--tm-science-card-heading,#a8c7fa); font-size:10px; font-weight:700; letter-spacing:.055em; }
.tm-hypothesis-card-label span { font-weight:450; letter-spacing:0; color:var(--tm-muted); margin-left:9px; }
.tm-hypothesis-card > strong { font-size:13px; font-weight:690; white-space:nowrap; color:var(--tm-science-card-value,#dbeafe); align-self:start; }
.tm-hypothesis-card-text { min-width:0; color:#e3e4eb; font-size:12px; line-height:1.65; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere; }
.tm-hypothesis-card-action { color:var(--tm-science-card-action,#9dbbf4); font-size:11px; font-weight:560; white-space:nowrap; }
/* The frozen object belongs to the scientific review column, above Investigation. */
.tm-tui-center > .tm-hypothesis-card { display:flex; flex-direction:column; align-items:stretch; gap:7px; margin:0; padding:14px 17px; }
.tm-tui-center > .tm-hypothesis-card > strong { align-self:flex-start; white-space:normal; }
.tm-tui-center > .tm-hypothesis-card .tm-hypothesis-card-text { -webkit-line-clamp:3; }
.tm-tui-center > .tm-hypothesis-card .tm-hypothesis-card-action { white-space:normal; overflow-wrap:anywhere; margin-top:2px; }
.tm-research-frozen { display:flex; flex-direction:column; gap:16px; padding:3px 0 9px; min-width:0; }
.tm-research-intro { display:flex; gap:16px; justify-content:space-between; align-items:flex-start; padding:3px 2px 5px; }
.tm-research-eyebrow { color:#a5b4fc; letter-spacing:.09em; font-size:10px; font-weight:700; }
.tm-research-intro h2 { font-size:19px; line-height:1.45; margin:6px 0; font-weight:690; color:var(--tm-text); }
.tm-research-intro p { margin:0; line-height:1.6; color:var(--tm-muted); font-size:12px; }
.tm-research-id { white-space:nowrap; border:1px solid #4c5478; border-radius:7px; padding:6px 11px; color:#c7d2fe; font-size:12px; font-weight:650; }
.tm-research-block { padding:19px 20px; border:1px solid var(--tm-border); border-radius:11px; background:#191b23; min-width:0; }
.tm-research-main { border-left:4px solid #818cf8; }
.tm-research-scope { border-left:4px solid #2dd4bf; }
.tm-research-test { border-left:4px solid #fbbf24; }
.tm-research-block-header { display:flex; gap:6px 14px; align-items:baseline; flex-wrap:wrap; margin-bottom:11px; }
.tm-research-block-header h3 { font-size:14px; font-weight:690; color:var(--tm-text); margin:0; }
.tm-research-block-header span { color:var(--tm-muted); font-size:11px; }
.tm-research-prose { font-size:13px; color:#e1e1e8; line-height:1.9; white-space:pre-wrap; overflow-wrap:anywhere; }
.tm-research-background { border:1px solid var(--tm-border); border-radius:10px; background:#19191e; padding:0 16px; }
.tm-research-background > summary { padding:13px 0; color:var(--tm-text); cursor:pointer; font-size:12px; }
.tm-research-background > summary span { color:var(--tm-muted); margin-left:12px; font-size:11px; }
.tm-research-background-inner { display:grid; gap:13px; border-top:1px solid var(--tm-border); padding:12px 0 16px; }
.tm-research-background-inner strong { font-size:11px; color:#c8c8cf; }
.tm-research-background-inner p { font-size:12px; line-height:1.75; color:var(--tm-muted); margin:5px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; }
@media(max-width:900px) {
  .tm-hypothesis-card { grid-template-columns:auto minmax(0,1fr); }
  .tm-hypothesis-card-action { grid-column:1/-1; }
  .tm-research-intro { flex-wrap:wrap; }
  .tm-research-block { padding:14px 13px; }
}
.tm-tui-secondary-panel { display:flex; flex-direction:column; min-height:0; }
.tm-tui-secondary-text { max-height:min(68vh,750px); min-height:240px; overflow:auto; padding:1px 3px 8px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.7; }
.tm-tui-secondary .tm-tui-footer > span { margin-left:auto; font-size:11px; }
.tm-tui-unavailable { border:1px solid #68501e; border-radius:8px; background:#2c230e; color:#fcd34d; padding:11px 13px; margin:0; font-size:12px; }
.tm-tui-extras { border:1px solid var(--tm-border); background:var(--tm-panel); border-radius:12px; padding:0 16px; }
.tm-tui-extras > summary { padding:14px 2px; color:var(--tm-muted); font-size:12px; font-weight:550; cursor:pointer; }
.tm-tui-extras[open] { display:flex; flex-direction:column; gap:12px; padding-bottom:15px; }
.tm-tui-extras[open] > summary { display:list-item; }
.tm-tui-extras .tm-operator { flex-shrink:0; }
.tm-web-tui > .tm-run-placeholder { margin:0; }
@media(max-width:1180px) {
  .tm-tui-kv-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .tm-tui-shortcuts-control { border-left:0; padding-left:0; }
}
@media(max-width:850px) {
  .tm-run-view:has(.tm-tui-screen) .tm-content { padding:17px 16px 32px; }
  .tm-tui-mid { grid-template-columns:1fr; row-gap:15px; }
  .tm-token-scopes { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .tm-tui-titlebar { flex-wrap:wrap; }
  .tm-tui-title-main { flex-basis:100%; }
}
@media(max-width:540px) {
  .tm-tui-kv-grid { gap:7px; }
  .tm-tui-kv { padding:10px; }
  .tm-tui-panel { padding:14px 13px; }
  .tm-tui-title-main > strong { font-size:19px; }
  .tm-token-scopes { grid-template-columns:1fr; }
  .tm-log-item { grid-template-columns:58px minmax(0,1fr); gap:6px; }
  .tm-tui-footer { padding:9px; gap:7px; }
  .tm-tui-footer button, .tm-tui-footer a { padding:6px; }
}
/* Stable Agent role accents reused in table and event timeline. */
.tm-role-research_lead { --role-accent:#60a5fa; --role-background:#17253c; }
.tm-role-challenger { --role-accent:#fbbf24; --role-background:#382a14; }
.tm-role-investigator { --role-accent:#c084fc; --role-background:#30203f; }
.tm-role-literature { --role-accent:#2dd4bf; --role-background:#12342f; }
.tm-role-judge { --role-accent:#fb7185; --role-background:#3a1c27; }
.tm-role-reporter { --role-accent:#86efac; --role-background:#173326; }
.tm-role-system { --role-accent:#a1a1aa; --role-background:#27272a; }
.tm-tui-agent-table tbody tr[class*="tm-role-"] td:first-child { border-left:3px solid var(--role-accent); padding-left:10px; }
.tm-tui-agent-table tbody tr[class*="tm-role-"] td:first-child strong { color:var(--role-accent); }
.tm-tui-attention { background:#2b2013; color:#fcd9a1; border:1px solid #86622e; border-radius:9px; padding:11px 14px; margin-top:10px; }
.tm-tui-attention summary { cursor:pointer; font-size:12px; font-weight:650; }
.tm-tui-attention summary span { color:#d4b588; font-size:11px; margin-left:6px; font-weight:400; }
.tm-tui-attention dl { display:grid; grid-template-columns:90px minmax(0,1fr); column-gap:12px; row-gap:12px; margin:15px 0 4px; }
.tm-tui-attention dt { font-size:11px; color:#d4b588; }
.tm-tui-attention dd { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; color:#f4e8d7; font-size:12px; line-height:1.7; }
.tm-event { border:1px solid var(--tm-border); border-left:3px solid var(--role-accent); background:#19191d; border-radius:9px; padding:13px 15px; min-width:0; }
.tm-event[hidden] { display:none; }
.tm-event + .tm-event { margin-top:10px; }
.tm-event-head { display:flex; align-items:center; flex-wrap:wrap; gap:10px; }
.tm-event-head strong { font-size:12px; font-weight:650; }
.tm-event-head time { margin-left:auto; color:var(--tm-muted); font-size:10px; font-variant-numeric:tabular-nums; }
.tm-role-chip { color:var(--role-accent); background:var(--role-background); border:1px solid var(--role-accent); border-radius:5px; font-size:11px; font-weight:650; padding:3px 8px; }
.tm-event-stage { color:var(--tm-muted); font-size:11px; margin-top:7px; }
.tm-event-text, .tm-event-body pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:9px 0 0; color:#d4d4d8; line-height:1.65; font-size:12px; }
.tm-event-body { margin-top:10px; }
.tm-event-body summary { color:#e4e4e7; font-size:11px; cursor:pointer; }
.tm-event-body pre { max-height:520px; overflow:auto; padding:10px 12px; border-radius:7px; background:#111114; }
.tm-events-toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end; margin-bottom:12px; }
.tm-events-toolbar label { display:flex; flex-direction:column; gap:5px; font-size:11px; color:var(--tm-muted); }
.tm-events-toolbar select, .tm-events-toolbar input { background:#19191d; border:1px solid #3f3f46; border-radius:8px; padding:8px 10px; color:var(--tm-text); font:inherit; font-size:12px; min-width:150px; }
.tm-events-search { flex:1; min-width:190px; }
.tm-events-search input { width:100%; }
.tm-event-count { color:var(--tm-muted); font-size:11px; margin:2px 0 12px; }
.tm-tui-settings { display:contents; }
.tm-setting-compact-row { display:grid; grid-column:2; grid-row:1; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
.tm-setting-gear { position:absolute; top:8px; left:12px; z-index:30; max-width:calc(100% - 24px); }
.tm-setting-gear > summary { list-style:none; display:flex; align-items:center; justify-content:center;
  width:30px; height:28px; border:1px solid #4b617c; border-radius:8px;
  background:#233248; color:#dceafe; font-size:19px; cursor:pointer; user-select:none; }
.tm-setting-gear > summary::-webkit-details-marker { display:none; }
.tm-setting-gear[open] > summary { background:#304b68; border-color:#82aee0; }
.tm-setting-gear summary:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-setting-gear-menu { position:absolute; top:calc(100% + 7px); left:0; width:300px;
  max-width:calc(100vw - 42px); padding:13px 14px; border:1px solid #536f96;
  border-radius:10px; background:#152337; box-shadow:0 14px 36px #000a;
  color:#e0ebfc; font-size:11px; }
.tm-setting-gear-menu > strong { display:block; margin-bottom:10px; font-size:12px; }
.tm-setting-gear-submenu { border:1px solid #475c76; border-radius:8px; padding:9px; }
.tm-setting-gear-submenu > summary { cursor:pointer; font-size:11px; font-weight:650; }
.tm-setting-gear-menu-title { display:inline-flex; gap:6px; align-items:center; }
.tm-setting-gear-status { display:block; color:#adc0d9; font-size:10px; font-weight:400; margin-top:5px; }
.tm-setting-gear-audit-controls { display:flex; align-items:center; justify-content:space-between;
  gap:12px; margin:10px 0 4px; }
.tm-setting-gear-submenu .tm-setting-detail-item { padding-top:7px; border-top:1px solid #354960; }
.tm-setting-gear-submenu .tm-setting-detail-item > strong { display:none; }
.tm-setting-gear-submenu .tm-setting-detail-item p { color:#c1d0e5; }
.tm-setting-gear-submenu .tm-setting-model { align-items:flex-start; }
.tm-setting-gear-submenu .tm-setting-model label { flex-wrap:wrap; }

.tm-setting-card-surface { min-height:70px; padding:9px 10px; border:1px solid #394961;
  border-radius:9px; background:#202b3c; min-width:0; }
.tm-setting-compact { display:flex; align-items:center; justify-content:space-between; gap:6px; }
.tm-setting-compact[data-setting-card], .tm-quota-auto-overview[data-setting-card] { cursor:pointer; }
.tm-setting-compact[data-setting-card]:hover, .tm-quota-auto-overview[data-setting-card]:hover {
  background:#25344a; border-color:#617999; }
.tm-setting-compact:has([data-setting-open][aria-expanded="true"]),
.tm-quota-auto-overview:has([data-setting-open][aria-expanded="true"]) {
  background:#293d53; border-color:#69a7de; }
.tm-setting-title-action { appearance:none; background:transparent; padding:0; margin:0;
  border:0; border-radius:3px; font-family:inherit; font-size:11px; line-height:1.3; font-weight:650; color:#e1e9f8; cursor:pointer; text-align:left; }
.tm-setting-title-action:focus-visible { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-setting-compact-copy { display:flex; flex-direction:column; min-width:0; gap:5px; }
.tm-setting-compact-title { display:flex; align-items:center; gap:5px; min-width:0; }
.tm-setting-compact-copy strong { color:#e1e9f8; font-size:11px; line-height:1.3; white-space:nowrap; }
.tm-setting-compact-copy small { color:#a3b1c5; font-size:9px; line-height:1.3; white-space:nowrap; }
.tm-setting-switch-control { position:relative; display:block; flex:0 0 36px; width:36px;
  height:22px; border-radius:13px; cursor:pointer; }
.tm-setting-switch-control input { position:absolute; inset:0; z-index:1;
  width:100%; height:100%; opacity:0; margin:0; cursor:pointer; }
.tm-setting-switch-track { display:block; pointer-events:none; width:36px; height:22px;
  border-radius:13px; background:#535966; border:1px solid #727a87; transition:background .15s; }
.tm-setting-switch-track::before { content:""; display:block; width:16px; height:16px;
  margin:2px; border-radius:50%; background:#f4f4f5; transition:transform .15s; }
.tm-setting-switch-control input:checked + .tm-setting-switch-track { background:#197e65; border-color:#32b993; }
.tm-setting-switch-control input:checked + .tm-setting-switch-track::before { transform:translateX(14px); }
.tm-setting-switch-control input:focus-visible + .tm-setting-switch-track { outline:2px solid #93c5fd; outline-offset:3px; }
.tm-setting-compact:has(input:disabled) { opacity:.65; }
.tm-setting-switch-control:has(input:disabled) { cursor:wait; }
.tm-setting-help-wrap { position:relative; display:inline-flex; flex:none; align-items:center; }
.tm-setting-help { display:inline-flex; align-items:center; justify-content:center; width:15px; height:15px;
  border:1px solid #8198b8; border-radius:50%; color:#c8daf1; font:bold 10px/1 ui-sans-serif,system-ui,sans-serif;
  cursor:help; }
.tm-setting-help:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-setting-tooltip { position:absolute; z-index:50; top:calc(100% + 9px); left:0; right:auto;
  width:max-content; max-width:min(245px,calc(100vw - 64px)); padding:9px 11px; border-radius:8px;
  background:#101b2e; color:#e6efff; border:1px solid #6884aa; box-shadow:0 9px 28px #000a;
  font-size:11px; line-height:1.55; font-weight:400; white-space:normal;
  visibility:hidden; opacity:0; pointer-events:none; }
.tm-setting-help-wrap:hover .tm-setting-tooltip,
.tm-setting-help-wrap:focus-within .tm-setting-tooltip { visibility:visible; opacity:1; }
.tm-setting-compact:nth-child(2) .tm-setting-tooltip { left:auto; right:0; }
.tm-setting-detail-panel { grid-column:1/-1; grid-row:2; padding:12px 15px;
  border:1px solid #475e7a; border-radius:10px; background:#192538; color:#a8bdd7;
  font-size:11px; min-width:0; }
.tm-setting-detail-panel[hidden], .tm-setting-detail-item[hidden] { display:none !important; }
.tm-setting-detail-item > strong { display:block; font-size:12px; color:#dceaff; }
.tm-setting-detail-item p { margin:7px 0 0; color:#b9c9dd; line-height:1.65; }
.tm-setting-detail-item .tm-setting-model { max-width:720px; }
.tm-setting-detail-item .tm-setting-advanced-note { font-size:10px; }
.tm-setting-advanced > summary { cursor:pointer; width:max-content; max-width:100%; }
.tm-setting-advanced > summary:focus-visible { outline:2px solid #93c5fd; }
.tm-setting-advanced-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  gap:9px; margin-top:10px; }
.tm-setting-advanced-item { min-width:0; padding:10px 11px; background:#192334;
  border:1px solid #344255; border-radius:9px; }
.tm-setting-advanced-item > strong { font-size:11px; color:#dceaff; }
.tm-setting-advanced-item p { margin:6px 0 0; color:#b1bfd2; line-height:1.55; }
.tm-setting-advanced-note { color:#98aac0; line-height:1.5; }
.tm-setting-model { border-top:1px solid var(--tm-border); padding-top:10px; margin-top:12px; display:flex; gap:7px 10px; align-items:center; flex-wrap:wrap; }
.tm-setting-model label { display:flex; align-items:center; gap:8px; font-size:11px; }
.tm-setting-model select { background:#242429; color:var(--tm-text); border:1px solid #3f3f46; border-radius:7px; padding:6px 9px; font-size:11px; }
.tm-setting-model span, .tm-setting-model-note { color:var(--tm-muted); font-size:10px; }
.tm-setting-model button { margin-left:auto; padding:7px 11px; border:1px solid #3f3f46; background:#27272a; border-radius:7px; font-size:11px; color:var(--tm-text); }
.tm-setting-feedback { grid-column:1/-1; grid-row:3; font-size:11px; color:#cbd5e1; margin:0; }
.tm-setting-feedback:empty { display:none; }
.tm-operator-explainer { color:var(--tm-muted); background:#1c1c20; border:1px solid var(--tm-border); border-radius:8px; font-size:11px; line-height:1.8; padding:10px 12px; margin:4px 0 0; }
.tm-operator-explainer strong { color:var(--tm-text); }
.tm-quota-auto-more { clear:both; }
@media(max-width:1000px) {
  .tm-quota-auto-toolbar { grid-template-columns:minmax(0,1fr); }
  .tm-quota-auto-overview { min-height:66px; }
  .tm-setting-compact-row { grid-column:1; grid-row:2; }
  .tm-setting-advanced { grid-row:3; }
  .tm-setting-feedback { grid-row:4; }
}
@media(max-width:900px) {
  .tm-setting-compact-row { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .tm-setting-compact:nth-child(2n) .tm-setting-tooltip { left:auto; right:0; }
  .tm-setting-advanced-grid { grid-template-columns:1fr; }
}
@media(max-width:780px) { .tm-tui-attention dl { grid-template-columns:1fr; row-gap:4px; } }
/* Stage Conversation: familiar chat layout, scientific provenance intact. */
.tm-stage-reader { display:flex; flex-direction:column; gap:12px; color:var(--tm-text); min-width:0; }
.tm-stage-toolbar { display:flex; flex-wrap:wrap; gap:12px; padding:4px 0 11px; border-bottom:1px solid var(--tm-border); align-items:end; }
.tm-stage-toolbar label { display:flex; flex-direction:column; gap:4px; color:var(--tm-muted); font-size:11px; }
.tm-stage-toolbar select,.tm-stage-toolbar input { background:#202024; border:1px solid #414149; border-radius:8px; color:#fafafa; padding:8px 10px; font-size:12px; min-width:145px; }
.tm-stage-toolbar .tm-stage-search { flex:1; min-width:200px; }
.tm-stage-search input { width:100%; }
.tm-stage-help { margin:0; color:var(--tm-muted); font-size:12px; }
.tm-stage-status { color:var(--tm-muted); font-size:11px; }
.tm-stage-current-notice { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; border:1px solid #67551e; border-radius:9px; padding:9px 13px; background:#2a2618; color:#fcdba2; font-size:12px; }
.tm-stage-current-notice[hidden] { display:none; }
.tm-stage-current-notice button { padding:7px 11px; border:1px solid #856e2a; border-radius:7px; background:#3c321a; color:#fde68a; font-size:11px; }

.tm-stage-timeline { display:flex; flex-direction:column; gap:18px; min-width:0; }
.tm-stage-section { border:1px solid var(--tm-border); border-left:6px solid var(--role-accent,#a1a1aa); border-right:6px solid var(--role-accent,#a1a1aa); border-radius:12px; overflow:hidden; background:#151519; }
/* Explicit per-role Stage rails: the Agent color is stable even if a
   nested chip/row or another component overrides its own accent. */
.tm-stage-section.tm-role-research_lead { --role-accent:#60a5fa; }
.tm-stage-section.tm-role-challenger { --role-accent:#fbbf24; }
.tm-stage-section.tm-role-investigator { --role-accent:#c084fc; }
.tm-stage-section.tm-role-literature { --role-accent:#2dd4bf; }
.tm-stage-section.tm-role-judge { --role-accent:#fb7185; }
.tm-stage-section.tm-role-reporter { --role-accent:#86efac; }

.tm-stage-heading { display:flex; align-items:center; text-align:left; flex-wrap:wrap; gap:9px; background:#1c1c21; width:100%; border:0; border-left:4px solid var(--role-accent); border-radius:0; padding:13px 16px; }
.tm-stage-heading:hover { background:#28282e; }
.tm-stage-heading > strong { font-size:13px; }
.tm-stage-heading > span:nth-last-child(2) { margin-left:auto; color:var(--tm-muted); font-size:11px; }
.tm-stage-heading .tm-role-chip { color:var(--role-accent); background:var(--role-background,#26262a); border-color:var(--role-accent); }
.tm-stage-round { font-size:11px; color:var(--tm-muted); }
.tm-stage-iteration { color:#e4e4e7; background:#303037; border-radius:7px; font-size:10px; padding:3px 7px; }
.tm-stage-meta { display:flex; flex-wrap:wrap; gap:6px 14px; margin:0; padding:9px 17px; border-top:1px solid var(--tm-border); color:var(--tm-muted); font-size:10px; }
.tm-stage-context { margin:10px 16px; padding:11px 14px; background:#261f18; border:1px solid #685034; border-radius:9px; font-size:11px; line-height:1.7; }
.tm-stage-context strong { color:#fcd9a1; }
.tm-stage-context p { margin:4px 0 7px; }
.tm-stage-judge-request { margin:4px 0 9px; padding:8px 11px; border-radius:7px; white-space:pre-wrap; overflow-wrap:anywhere; color:#fae1c1; }
.tm-stage-messages { padding:16px 14px 24px; border-top:1px solid var(--tm-border); }
.tm-stage-dialogue { display:flex; flex-direction:column; gap:16px; }
.tm-stage-bubble { padding:13px 15px; border-radius:12px; max-width:min(90%,850px); min-width:0; border:1px solid var(--tm-border); }
.tm-stage-prompt { margin-left:auto; background:#19365b; border:1px solid #3b82f6; box-shadow:inset 0 0 0 1px rgba(96,165,250,.16); width:82%; color:#eef6ff; }
.tm-stage-agent-message { margin-right:auto; background:#1a1b21; border-left:3px solid var(--role-accent,#a1a1aa); width:88%; padding:6px 12px 7px; }
.tm-stage-reply-time { float:right; margin:1px 0 0 12px; color:#71717a; font-size:10px; line-height:1.6; }
.tm-stage-system-event { max-width:100%; width:100%; margin:0 auto; background:#17171a; color:#d4d4d8; }
.tm-stage-heading { border-left:0; }
.tm-stage-heading > strong { color:var(--role-accent,#e4e4e7); }
.tm-stage-messages { position:relative; background:linear-gradient(90deg,color-mix(in srgb,var(--role-accent,#a1a1aa) 5%,transparent),transparent 8%,transparent 92%,color-mix(in srgb,var(--role-accent,#a1a1aa) 5%,transparent)); }
.tm-stage-tool-message { margin-right:auto; width:88%; padding:5px 10px; border:0; border-radius:8px; background:transparent; color:#7d7d88; }
.tm-stage-tool-fold { width:88%; margin-right:auto; color:#7d7d88; font-size:11px; }
.tm-stage-tool-fold > summary { cursor:pointer; padding:2px 0 4px; }
.tm-stage-tool-fold .tm-stage-tool-message { width:100%; padding:4px 0 6px; }
.tm-stage-milestone { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:1px auto; padding:6px 10px; border:1px dashed #383840; border-radius:8px; background:#17171c; color:#a1a1aa; font-size:10px; max-width:100%; }
.tm-stage-milestone strong { font-size:11px; font-weight:600; color:var(--role-accent,#d4d4d8); }
.tm-stage-milestone time { font-variant-numeric:tabular-nums; margin-left:auto; color:#a1a1aa; }
.tm-stage-milestone .tm-stage-raw { width:100%; }
.tm-stage-milestone pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:350px; overflow:auto; font:11px/1.5 ui-monospace,monospace; }
.tm-stage-tool-note { margin-top:2px; color:#6f6f7a; font-size:11px; }
.tm-stage-tool-note summary { cursor:pointer; }
.tm-stage-coverage { margin-bottom:14px; color:#b8bbc8; font-size:11px; padding:9px 12px; border-radius:7px; border:1px solid #393e4a; background:#20232d; }
.tm-stage-canvas-changes { display:flex; flex-direction:column; gap:10px; }
.tm-stage-canvas-title { font-size:12px; font-weight:700; color:var(--role-accent,#d4d4d8); }
.tm-stage-canvas-change { padding:9px 11px; border:1px solid #41414a; border-radius:8px; background:#202029; }
.tm-stage-canvas-change > div { display:flex; flex-wrap:wrap; align-items:center; gap:9px; }
.tm-stage-canvas-change strong { font-size:12px; overflow-wrap:anywhere; }
.tm-stage-canvas-change p { margin:7px 0 0; font-size:12px; line-height:1.6; white-space:pre-wrap; overflow-wrap:anywhere; }
.tm-stage-canvas-status { color:#d4d4d8; background:#32323c; border-radius:6px; padding:2px 7px; font-size:10px; }
.tm-stage-canvas-changes .tm-stage-raw summary { cursor:pointer; color:#cbd5e1; font-size:11px; }
.tm-stage-context-brief { background:#1c2424; border-color:#3b5555; color:#d1e4e2; }

.tm-stage-bubble-header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding-bottom:8px; border-bottom:1px solid #3f3f46; margin-bottom:8px; }
.tm-stage-bubble-header strong { font-size:12px; }
.tm-stage-bubble-header small { color:var(--tm-muted); font-size:10px; white-space:nowrap; }
.tm-stage-tool-message .tm-stage-bubble-header { padding-bottom:0; border-bottom:0; margin-bottom:1px; }
.tm-stage-tool-message .tm-stage-bubble-header strong,
.tm-stage-tool-message .tm-stage-bubble-header small { color:#7d7d88; font-size:11px; font-weight:450; }
.tm-stage-bubble pre { margin:0; padding:0; color:#ebebeb; font:12px/1.75 ui-sans-serif,system-ui,sans-serif; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; max-width:100%; }
.tm-stage-agent-message pre { color:#fafafa; }
.tm-stage-json-viewer { border:1px solid #393b48; border-radius:9px; overflow:hidden; background:#111115; margin:6px 0; min-width:0; }
.tm-stage-json-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:6px 10px; border-bottom:1px solid #383944; color:#a1a1aa; font-size:11px; }
.tm-stage-json-toolbar button { border:1px solid #505467; border-radius:6px; background:#272935; color:#e2e8f0; padding:3px 11px; cursor:pointer; font-size:11px; }
.tm-stage-json-toolbar button:hover { background:#35394b; }
.tm-stage-json-viewer pre { padding:10px 12px; margin:0; font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; max-height:540px; overflow:auto; }
.tm-stage-json-viewer pre[hidden] { display:none; }
.tm-stage-bubble .tm-stage-long > pre { margin-top:10px; padding:12px; background:#111114; border-radius:8px; max-height:520px; overflow:auto; }
.tm-stage-long { color:#ddd; font-size:11px; cursor:pointer; }
.tm-stage-preview { font-size:11px!important; color:#aaa!important; }
.tm-stage-tool-message pre,
.tm-stage-tool-message .tm-stage-preview,
.tm-stage-tool-message .tm-stage-json-toolbar,
.tm-stage-tool-message .tm-stage-json-viewer,
.tm-stage-tool-message .tm-stage-json-viewer pre { color:#7d7d88!important; font-size:11px!important; font-weight:400; line-height:1.45; background:transparent; border-color:#2e2e34; }
.tm-stage-bubble .tm-stage-raw { border-top:1px solid #3f3f46; margin-top:12px; padding-top:8px; }
.tm-stage-raw summary { font-size:11px; color:#c4c4cf; cursor:pointer; }
.tm-stage-raw pre { margin-top:9px; font:11px/1.6 ui-monospace,Menlo,Consolas,monospace; max-height:440px; overflow:auto; }
.tm-stage-warning { margin-top:9px; padding:8px; border-radius:7px; background:#30281a; color:#fcdba2; font-size:10px; }
.tm-stage-messages > button { display:block; margin:20px auto 0; padding:10px 18px; }
.tm-stage-jump { position:fixed; right:16px; bottom:24px; z-index:40; display:flex; flex-direction:column; gap:8px; }
.tm-stage-jump[hidden] { display:none; }
.tm-stage-jump button { width:36px; height:36px; border-radius:999px; border:1px solid #52525b; background:#27272a; color:#fafafa; font-size:16px; line-height:1; cursor:pointer; box-shadow:0 6px 18px #0008; }
.tm-stage-jump button:hover { background:#3f3f46; }
.tm-stage-sdk-node { display:flex; align-items:center; flex-wrap:wrap; gap:8px 12px; padding:10px 14px; background:#222226; border:1px dashed #4d4d55; border-radius:10px; color:#d4d4d8; font-size:11px; }
.tm-stage-sdk-node > span { font-weight:650; }
.tm-stage-sdk-node time { margin-left:auto; color:var(--tm-muted); font-size:10px; }
.tm-stage-sdk-node details { width:100%; }
.tm-stage-sdk-node summary { color:#c4c4cf; cursor:pointer; }
.tm-stage-sdk-node pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:300px; overflow:auto; padding:8px; background:#161619; border-radius:7px; }
.tm-stage-empty { color:var(--tm-muted); }
@media(max-width:780px) {
  .tm-stage-prompt,.tm-stage-agent-message { width:95%; max-width:95%; }
  .tm-stage-messages { padding:10px 8px; }
  .tm-stage-toolbar select { min-width:120px; }
  .tm-stage-heading { padding:11px; }
}
.tm-run-placeholder { display:flex; align-items:center; justify-content:space-between; gap:12px; border-style:dashed; }
.tm-run-placeholder strong { font-size:14px; }
.tm-run-placeholder p { margin:3px 0 0; font-size:12px; color:var(--tm-muted); }
.tm-run-placeholder > span { font-size:11px; color:var(--tm-muted); white-space:nowrap; }
@media (max-width:1050px) { .tm-run-stats { grid-template-columns:repeat(2,minmax(0,1fr)); } .tm-run-panels { grid-template-columns:1fr; } }
@media (max-width:680px) { .tm-run-view .tm-content { padding:14px 12px 28px; } .tm-run-agents { grid-template-columns:repeat(2,minmax(0,1fr)); } .tm-run-table { min-width:620px; } }
.tm-content .stage-layout { display:block; width:100%; }
.tm-content .stage-preview-head { border:0; background:transparent; padding:0; border-radius:0; }

.tm-content .stage-legend { margin:14px 0; }
.tm-content .stage-canvas { border-color:var(--tm-border); background:#0c0c0e; }
.tm-content .stage-toolbar { background:#18181b; border-color:#3f3f46; }
.tm-content .stage-meta { background:var(--tm-panel); border:1px solid var(--tm-border); border-radius:12px; padding:15px; margin-top:16px; }
.tm-content .stage-request-panel { background:var(--tm-panel); border-color:var(--tm-border); }
.tm-content .stage-canvas-shell { min-width:0; }
.tm-content .workflow-version-form select { background:#18181b; color:var(--tm-text); border-color:#3f3f46; }
.tm-content .stage-preview-actions a { border-color:#3f3f46; background:#18181b; }
body.stage-height-focus .tm-sidebar, body.stage-height-focus .tm-topbar, body.stage-height-focus .tm-page-head { display:none; }
body.stage-height-focus .tm-main { margin-left:0; }
body.stage-height-focus.tm-sidebar-collapsed .tm-main { margin-left:0; }
body.stage-height-focus .tm-content { padding:0; max-width:none; }
@media (max-width:900px) {
  /* Contain unavoidably wide research tables to their own scrollers, not the page. */
  html, body { max-width:100%; overflow-x:clip; }
  .tm-app { width:100%; max-width:100%; min-width:0; overflow-x:clip; }
  .tm-sidebar { position:relative; width:100%; max-width:100%; min-width:0; height:auto; border-right:0; border-bottom:1px solid var(--tm-border); }
  .tm-sidebar-toggle { display:none; }
  .tm-mobile-nav-toggle { display:inline-flex; }
  .tm-brand { min-height:58px; padding:11px 17px; border-bottom:0; min-width:0; }
  .tm-brand-text { min-width:0; }
  .tm-nav-label, .tm-sidebar-foot { display:none; }
  .tm-nav { display:none; flex-direction:column; width:100%; min-width:0; gap:3px; padding:2px 12px 12px; }
  .tm-sidebar.tm-mobile-nav-open .tm-nav { display:flex; }
  .tm-nav-link { min-height:41px; padding:8px 12px; gap:11px; width:100%; min-width:0; }
  .tm-main { margin-left:0; width:100%; min-width:0; max-width:100%; }
  .tm-topbar { height:50px; padding:0 17px; }
  .tm-content { padding:22px 17px 35px; }
  .tm-content .stage-layout { min-width:0; }
  .tm-content .stage-canvas { min-height:460px; }
}
@media (max-width:520px) {
  .tm-topbar-right { display:none; }
  .tm-brand-icon { width:30px; height:30px; }
  .tm-content .stage-preview-head { padding:14px; }
}

/* Workspace-wide appearance preference. Both palettes share one layout. */
html[data-sciver-theme="black"] {
  --tm-bg:#09090b; --tm-panel:#101013; --tm-side:#0c0c0e;
  --tm-hover:#1b1b1f; --tm-border:#27272a; --tm-text:#fafafa;
  --tm-muted:#a1a1aa; --tm-accent:#e4e4e7;
  --tm-button-bg:#18181b; --tm-button-hover:#27272a; --tm-button-border:#3f3f46;
  --tm-link:#e4e4e7;
}
html[data-sciver-theme="blue"] {
  --tm-bg:#07162f; --tm-panel:#0b1e38; --tm-side:#061226;
  --tm-hover:#112b4b; --tm-border:#223b5c; --tm-text:#f4f8ff;
  --tm-muted:#a9bad0; --tm-accent:#80bdff;
  --tm-button-bg:#10233d; --tm-button-hover:#183858; --tm-button-border:#304967;
  --tm-link:#a5d1ff;
}
button { background:var(--tm-button-bg); border-color:var(--tm-button-border); }
button:hover { background:var(--tm-button-hover); }
a { color:var(--tm-link); }
html[data-sciver-theme="black"] .tm-nav-link[aria-current="page"] { background:#252529; }
html[data-sciver-theme="blue"] .tm-nav-link[aria-current="page"] { background:#14345a; }
html[data-sciver-theme="black"] .tm-brand-icon { background:#09090b; }
html[data-sciver-theme="blue"] .tm-brand-icon { background:#07162f; }
html[data-sciver-theme="blue"] .tm-content .stage-canvas { background:#061226; }
.tm-appearance-panel .tm-appearance-row { display:flex; align-items:center; flex-wrap:wrap; gap:12px; margin-top:14px; }
.tm-appearance-panel label { font-size:13px; font-weight:600; color:var(--tm-text); }
.tm-appearance-panel select { min-width:170px; max-width:100%; padding:9px 12px; border:1px solid var(--tm-border); border-radius:8px; color:var(--tm-text); background:var(--tm-panel); }
.tm-appearance-panel select:focus-visible { outline:2px solid var(--tm-accent); outline-offset:2px; }
</style>
"""

_ICONS = {
    "research_chat": '<path d="M4 5h16v11H9l-5 4V5z"/><path d="M8 9h8M8 12h5"/>',
    "run": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 5 5-5 5"/>',
    "console": '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M7 8h4M7 12h10M8 21h8M12 18v3"/>',
    "research_plan": '<path d="M4 3h13l3 3v15H4z"/><path d="M17 3v4h3M8 11h8M8 15h8M8 19h5"/>',
    "explorer": '<circle cx="12" cy="12" r="3"/><path d="M12 3v6m0 6v6M3 12h6m6 0h6M5.5 5.5l4 4m5 5 4 4"/>',
    "workflow": '<path d="M6 3v12m0 0-3-3m3 3 3-3M18 9v12m0-12-3 3m3-3 3 3M6 3h8a4 4 0 0 1 4 4v2"/>',
    "architecture": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="8.5" y="14" width="7" height="7" rx="1"/><path d="M6.5 10v2h11v-2M12 12v2"/>',
    "services": '<rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01M11 7h.01M11 17h.01"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M10 2h4l.6 2.4 1.7 1 2.4-.7 2 3.4-1.8 1.7v2.3l1.8 1.7-2 3.4-2.4-.7-1.7 1L14 20h-4l-.6-2.5-1.7-1-2.4.7-2-3.4 1.8-1.7V9.8L3.3 8.1l2-3.4 2.4.7 1.7-1z"/>',
}
_VIEWS = (
    ("research_chat", "Research Chat", "/research-chat/"),
    ("run", "Task List", "/run/"),
    ("console", "Task Console", "/"),
    ("research_plan", "Research Plan", "/research-plan/"),
    ("workflow", "Workflow", "/workflow-stage-preview/"),
    ("architecture", "Architecture", "/architecture/"),
    ("services", "Services", "/services/"),
    ("settings", "Settings", "/settings/"),
)
_SUBTITLES = {
    "research_chat": "与正在运行的科研项目对话，预览并确认研究方向操作",
    "run": "任务列表与实时科研监控",
    "console": "当前任务的只读科研控制台",
    "research_plan": "查看与编辑当前任务的长期科学计划",
    "explorer": "",
    "workflow": "Research workflow, agent stages and scientific contracts",
    "architecture": "System components and dependencies",
    "services": "Codex account quotas and controls",
    "settings": "Workspace 设置与任务级自动审计选项",
}


def _secondary_back_link(back_href: str | None) -> str:
    """A stable in-app parent link, never browser history or a Runner action."""
    import re

    target = str(back_href or "")
    allowed = (target in {"/run/", "/workflow-stage-preview/", "/services/"}
               or re.fullmatch(r"/run/[0-9a-f]{16}/", target) is not None)
    if not allowed:
        return ""
    name = ("任务概览" if re.fullmatch(r"/run/[0-9a-f]{16}/", target) else
            "Task List" if target == "/run/" else
            "Workflow" if target == "/workflow-stage-preview/" else "Services")
    return (f'<a class="tm-secondary-back" href="{html.escape(target, quote=True)}"'
            f' aria-label="返回{name}" title="返回{name}">'
            '<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M19 12H5m7 7-7-7 7-7"/></svg>'
            '<span>返回</span></a>')


def _breadcrumb(active: str, parent: str | None, leaf: str | None = None) -> tuple[str, str]:
    """Semantic navigation path. Only validated, known in-app parents are linked."""
    import re

    task_parent = parent if re.fullmatch(r"/run/[0-9a-f]{16}/", parent or "") else None
    segments: list[tuple[str, str | None]] = [("Workspace", None)]
    if active == "run":
        if task_parent:
            segments += [("Task List", "/run/"), ("Task Console", task_parent),
                         (leaf or "Research", None)]
        elif parent == "/run/":
            segments += [("Task List", "/run/"), ("Task Console", None)]
        else:
            segments.append(("Task List", None))
    elif active == "research_plan":
        if task_parent:
            segments += [("Task List", "/run/"), ("Task Console", task_parent)]
        segments.append(("Research Plan", None))
    elif active == "explorer":
        if task_parent:
            segments += [("Task List", "/run/"), ("Task Console", task_parent)]
        segments.append(("Explore map", None))
    elif active == "workflow":
        if parent == "/workflow-stage-preview/":
            segments += [("Workflow", "/workflow-stage-preview/"),
                         (leaf or "Workflow Details", None)]
        else:
            segments.append(("Workflow", None))
    elif active == "services" and parent == "/services/":
        segments += [("Services", "/services/"), (leaf or "Codex Usage", None)]
    else:
        segments.append((dict((key, label) for key, label, _ in _VIEWS)[active], None))

    rendered = []
    for idx, (label, href) in enumerate(segments):
        if idx:
            rendered.append('<span class="tm-breadcrumb-divider" aria-hidden="true">/</span>')
        if idx == 0:
            rendered.append('<button type="button" class="tm-breadcrumb-workspace"'
                            ' data-workspace-nav aria-controls="tm-main-nav"'
                            ' aria-expanded="true" title="显示 Workspace 导航">Workspace</button>')
            continue
        if href:
            rendered.append('<a class="tm-breadcrumb-link" href="' +
                            html.escape(href, quote=True) + '">' +
                            html.escape(label) + '</a>')
        else:
            rendered.append('<strong aria-current="page">' + html.escape(label) + '</strong>')
    return "".join(rendered), segments[-1][0]


from .supervisor_bell_ui import BELL_HTML, BELL_STYLE, BELL_SCRIPT


def dashboard_shell(title: str, body: str, active: str = "workflow", *,
                    back_href: str | None = None, breadcrumb_leaf: str | None = None) -> str:
    """Wrap semantic content in the Workspace navigation shell."""
    import re

    active = active if active in _ICONS else "workflow"
    view_name = next((label for key, label, _ in _VIEWS if key == active), "Explore map")
    # Main Workflow is the workspace landing page. All other top-level
    # pages have a consistent workspace parent, while nested pages override it.
    parent = back_href if back_href is not None else (
        "/workflow-stage-preview/" if active != "workflow" else None)
    back_link = _secondary_back_link(parent)
    crumb_html, view_name = _breadcrumb(active, parent, breadcrumb_leaf)
    task_console = parent if re.fullmatch(r"/run/[0-9a-f]{16}/", parent or "") else None
    subtitle_html = f"<p>{html.escape(_SUBTITLES[active])}</p>" if _SUBTITLES[active] else ""
    viewing_console = active == "run" and (parent == "/run/" or task_console is not None)
    links = "".join(
        f'<a class="tm-nav-link" href="{html.escape(task_console if key == "console" and task_console else url, quote=True)}"'
        + (' aria-current="page"' if (
            (active == "explorer" and key == ("console" if task_console else "run"))
            or (viewing_console and key == "console")
            or (not viewing_console and active != "explorer" and key == active)
        ) else "")
        + f' aria-label="{html.escape(label, quote=True)}" title="{html.escape(label, quote=True)}">'
        + f'<svg class="tm-nav-icon" viewBox="0 0 24 24" aria-hidden="true">{_ICONS[key]}</svg>'
        + f'<span class="tm-nav-text">{label}</span></a>'
        for key, label, url in _VIEWS
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">{THEME_BOOTSTRAP_SCRIPT}<title>{html.escape(title)}</title>{STYLE}{BELL_STYLE}</head>
<body class="tm-{active}-view"><div class="tm-app">
<div class="tm-sidebar-edge" data-sidebar-edge aria-hidden="true"></div>
<aside class="tm-sidebar" aria-label="Primary navigation">
  <div class="tm-brand"><div class="tm-brand-icon" aria-hidden="true"><img src="/favicon.svg" alt=""></div><div class="tm-brand-text">
    <div class="tm-brand-name">Sciver</div><div class="tm-brand-sub">Research Observatory</div>
  </div><button class="tm-sidebar-toggle" type="button" data-sidebar-toggle
    aria-label="收起侧边栏" aria-expanded="true" title="收起侧边栏">
    <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path class="tm-sidebar-pane" d="M5 5h4v14H5z"/><path d="M10 3v18"/></svg>
  </button><button class="tm-mobile-nav-toggle" type="button" data-mobile-nav-toggle
    aria-controls="tm-main-nav" aria-label="打开导航菜单" aria-expanded="false" title="导航菜单">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
    <span>菜单</span>
  </button></div>
  <div class="tm-nav-label">Workspace</div><nav class="tm-nav" id="tm-main-nav" aria-label="Main navigation">{links}</nav>
  <div class="tm-sidebar-foot"><span class="tm-indicator" aria-hidden="true"></span><span class="tm-sidebar-foot-text">Observatory Console</span></div>
</aside>
<div class="tm-main">
  <header class="tm-topbar"><nav class="tm-breadcrumb" aria-label="页面导航">{back_link}{crumb_html}</nav><div class="tm-topbar-actions"><div class="tm-topbar-right">Sciver · Monitor</div>{BELL_HTML}</div></header>
  <div class="tm-content">
    <section class="tm-page-head" aria-label="Page introduction"><div>
      <div class="tm-page-label">Overview / {view_name}</div><h1>{view_name}</h1>
      {subtitle_html}
    </div></section>
    {body}
  </div>
</div></div><script data-mobile-nav-script>
(function() {{
  "use strict";
  const sidebar=document.querySelector(".tm-sidebar");
  const button=document.querySelector("[data-mobile-nav-toggle]");
  const workspace=document.querySelector("[data-workspace-nav]");
  if(!sidebar||!button)return;
  function setOpen(open) {{
    sidebar.classList.toggle("tm-mobile-nav-open",Boolean(open));
    button.setAttribute("aria-expanded",String(Boolean(open)));
    button.setAttribute("aria-label",open?"关闭导航菜单":"打开导航菜单");
    workspace?.setAttribute("aria-expanded",String(Boolean(open)));
  }}
  button.addEventListener("click",()=>setOpen(!sidebar.classList.contains("tm-mobile-nav-open")));
  document.addEventListener("keydown",event=>{{
    if(event.key==="Escape" && sidebar.classList.contains("tm-mobile-nav-open")) {{
      setOpen(false);
      button.focus();
    }}
  }});
  document.addEventListener("click",event=>{{
    if(!sidebar.contains(event.target))setOpen(false);
  }});
}})();
</script><script>
(function() {{
  "use strict";
  const key="tm-observer-sidebar-collapsed-v1";
  const toggle=document.querySelector("[data-sidebar-toggle]");
  const sidebar=document.querySelector(".tm-sidebar");
  const workspace=document.querySelector("[data-workspace-nav]");
  const edge=document.querySelector("[data-sidebar-edge]");
  const mobileMenu=document.querySelector("[data-mobile-nav-toggle]");
  if(!toggle||!sidebar||!workspace||!edge)return;
  let collapsed=false, peek=false;
  try {{ collapsed=localStorage.getItem(key)==="1"; }} catch(_err) {{}}
  const desktop=()=>!window.matchMedia||window.matchMedia("(min-width:901px)").matches;
  function sync() {{
    document.body.classList.toggle("tm-sidebar-collapsed",collapsed);
    document.body.classList.toggle("tm-sidebar-peek",collapsed&&peek);
    const visible=!collapsed||peek;
    toggle.setAttribute("aria-expanded",String(visible));
    toggle.setAttribute("aria-label",collapsed?"固定侧边栏":"收起侧边栏");
    toggle.title=collapsed?"固定侧边栏":"收起侧边栏";
    workspace.setAttribute("aria-expanded",String(desktop()?visible:sidebar.classList.contains("tm-mobile-nav-open")));
  }}
  function apply(next) {{ collapsed=Boolean(next);peek=false;sync(); }}
  function setPeek(open) {{ if(!collapsed||!desktop())return;peek=Boolean(open);sync(); }}
  apply(collapsed);
  toggle.addEventListener("click",()=>{{
    apply(!collapsed);
    try {{ localStorage.setItem(key,collapsed?"1":"0"); }} catch(_err) {{}}
  }});
  workspace.addEventListener("click",event=>{{
    if(!desktop()){{ mobileMenu?.click();return; }}
    if(!collapsed)return;
    setPeek(!peek);
    // Keyboard users can navigate without a pointer near the screen edge.
    if(peek&&event.detail===0)sidebar.querySelector(".tm-nav-link")?.focus();
  }});
  edge.addEventListener("mouseenter",()=>setPeek(true));
  sidebar.addEventListener("mouseleave",()=>setPeek(false));
  document.addEventListener("click",event=>{{
    if(peek&&!sidebar.contains(event.target)&&!workspace.contains(event.target))setPeek(false);
  }});
  document.addEventListener("keydown",event=>{{
    if(event.key==="Escape"&&peek){{setPeek(false);workspace.focus();}}
  }});
  window.addEventListener("resize",()=>{{if(peek&&!desktop()){{peek=false;sync();}}}});
}})();
</script><script data-sciver-theme-controls>
(function() {{
  "use strict";
  const key="sciver-theme-v1";
  const picker=document.querySelector("[data-sciver-theme-picker]");
  const active=()=>document.documentElement.getAttribute("data-sciver-theme")==="blue"?"blue":"black";
  function apply(theme) {{
    const next=theme==="blue"?"blue":"black";
    document.documentElement.setAttribute("data-sciver-theme",next);
    if(picker)picker.value=next;
  }}
  if(picker) {{
    picker.value=active();
    picker.addEventListener("change",()=>{{
      apply(picker.value);
      try {{ window.localStorage.setItem(key,active()); }} catch(_err) {{}}
    }});
  }}
  window.addEventListener("storage",event=>{{
    if(event.key===key)apply(event.newValue);
  }});
}})();
</script>{BELL_SCRIPT}</body></html>"""
