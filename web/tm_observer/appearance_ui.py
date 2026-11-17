"""Shared light palette for the dashboard and its embedded Research Chat."""

THEME_BOOTSTRAP_SCRIPT = """<script data-sciver-theme-bootstrap>
(function() {
  "use strict";
  let theme="black";
  try {
    const saved=window.localStorage.getItem("sciver-theme-v1");
    if(["black","blue","white"].includes(saved))theme=saved;
  } catch(_err) {}
  document.documentElement.setAttribute("data-sciver-theme",theme);
  window.addEventListener("storage",event=>{
    if(event.key==="sciver-theme-v1" || event.key===null)
      document.documentElement.setAttribute("data-sciver-theme",
        ["black","blue","white"].includes(event.newValue)?event.newValue:"black");
  });
})();
</script>"""

# Neutral light surfaces follow https://ui.shadcn.com/docs/theming.
# Keep these overrides scoped: black/blue retain their existing palettes.
LIGHT_STYLE = """<style data-sciver-light-theme>
html[data-sciver-theme="white"] {
  color-scheme:light;
  --tm-bg:#ffffff; --tm-panel:#ffffff; --tm-side:#fafafa;
  --tm-hover:#f4f4f5; --tm-border:#e4e4e7; --tm-text:#18181b;
  --tm-muted:#52525b; --tm-accent:#2563eb; --tm-positive:#15803d;
  --tm-button-bg:#ffffff; --tm-button-hover:#f4f4f5; --tm-button-border:#d4d4d8;
  --tm-link:#1d4ed8;
  --text:var(--tm-text); --panel:var(--tm-panel); --line:var(--tm-border); --accent:var(--tm-accent);
}
html[data-sciver-theme="white"] body { background:var(--tm-bg); color:var(--tm-text); }
html[data-sciver-theme="white"] body :is(button,input,textarea,select) {
  color:var(--tm-text); background-color:var(--tm-panel); border-color:var(--tm-border);
}
html[data-sciver-theme="white"] :is(button,a):hover { color:var(--tm-text); }
html[data-sciver-theme="white"] button:hover { background-color:var(--tm-hover); }
html[data-sciver-theme="white"] :focus-visible { outline-color:var(--tm-accent); }
html[data-sciver-theme="white"] ::placeholder { color:#71717a; opacity:1; }
html[data-sciver-theme="white"] a { color:var(--tm-link); }
html[data-sciver-theme="white"] .tm-nav-link { color:var(--tm-muted); }
html[data-sciver-theme="white"] .tm-nav-link[aria-current="page"] {
  background:#eff6ff; color:#1d4ed8; box-shadow:inset 3px 0 0 #2563eb;
}
html[data-sciver-theme="white"] .tm-brand-icon { background:#fafafa; }
html[data-sciver-theme="white"] :is(.tm-breadcrumb strong,.tm-breadcrumb-workspace,.tm-breadcrumb-link) { color:#3f3f46; }
html[data-sciver-theme="white"] :is(.tm-sidebar-toggle,.tm-mobile-nav-toggle,.tm-secondary-back,
  .tm-run-refresh,.tm-run-back,.tm-run-agent,.tm-run-output,.tm-run-state,.tm-run-tui-label,
  .tm-run-tab[aria-current="page"],.tm-operator-explainer,.tm-research-block,.tm-research-background,
  .tm-setting-card,.tm-setting-dialog,.tm-setting-summary,.tm-setting-advanced-item,
  .tm-quota-auto-overview,.tm-quota-auto-account,.tm-quota-auto-more,
  .tm-stage-section,.tm-stage-heading,.tm-stage-system-event,.tm-stage-milestone,
  .tm-stage-canvas-change,.tm-stage-json-viewer,.tm-stage-json-toolbar,.tm-stage-iteration,
  .tm-stage-canvas-status,.tm-stage-sdk-node,.tm-stage-sdk-node pre,.tm-stage-bubble .tm-stage-long > pre,
  .tm-notices-toggle,.tm-notices-panel,.tm-notices-clear,.tm-notices-more,
  .tm-quota-heading .tm-update-age,.tm-quota-section #usage .grid > div,
  .sc-chat-panel,.sc-chat-header,.sc-chat-frame,.sc-chat-loading,.sc-chat-launcher-label) {
  background:var(--tm-hover); color:var(--tm-text); border-color:var(--tm-border);
}
html[data-sciver-theme="white"] :is(.tm-run-table th,.tm-setting-dialog,.tm-notices-panel,.sc-chat-panel) { background:#fff; }
html[data-sciver-theme="white"] .tm-run-table td { border-color:var(--tm-border); }
html[data-sciver-theme="white"] :is(.tm-run-table tbody tr:hover,.tm-run-clickable:focus-visible,
  .tm-notices-item:hover,.tm-notices-item:focus-within,.tm-stage-heading:hover) { background:#f4f4f5; }
html[data-sciver-theme="white"] :is(.tm-run-events,.tm-research-prose,.tm-research-background-inner strong,
  .tm-setting-card strong,.tm-setting-feedback,.tm-setting-advanced-item p,.tm-setting-advanced-note,
  .tm-quota-auto-state,.tm-quota-auto-facts strong,.tm-quota-auto-account-header strong,
  .tm-quota-auto-meter-label strong,.tm-notices-head,.tm-notices-item,.sc-chat-heading strong,
  .tm-stage-bubble pre,.tm-stage-agent-message pre,.tm-stage-context strong,
  .tm-stage-judge-request,.tm-stage-raw summary,.tm-stage-sdk-node summary) { color:var(--tm-text); }
html[data-sciver-theme="white"] :is(.tm-quota-auto-facts,.tm-quota-auto-check,.tm-quota-auto-note,
  .tm-quota-auto-reset,.tm-quota-auto-meter-label,.tm-setting-dialog p,.sc-chat-heading span,
  .sc-chat-action,.tm-stage-tool-message,.tm-stage-tool-fold,.tm-stage-reply-time) { color:var(--tm-muted); }
html[data-sciver-theme="white"] :is(.tm-quota-auto-meter,.tm-quota-section .tm-quota-progress) { background:#e4e4e7; }
html[data-sciver-theme="white"] .tm-quota-section .tm-quota-progress span { background:#2563eb; }
html[data-sciver-theme="white"] :is(.tm-quota-section .tm-account-card.is-active,.tm-quota-section .tm-active-indicator,
  .tm-run-state-running) { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }
html[data-sciver-theme="white"] :is(.warning,.tm-run-state-error) { color:#b91c1c; }
html[data-sciver-theme="white"] .tm-run-state-error { background:#fef2f2; border-color:#fecaca; }
html[data-sciver-theme="white"] :is(.tm-run-no-cycle,.tm-run-warning,.tm-run-state-attention,
  .tm-stage-warning,.tm-stage-context,.tm-stage-current-notice,.tm-stage-coverage) {
  background:#fffbeb; color:#92400e; border-color:#fde68a;
}
html[data-sciver-theme="white"] .tm-stage-prompt { background:#eff6ff; color:#1e3a8a; border-color:#bfdbfe; }
html[data-sciver-theme="white"] .tm-stage-agent-message { background:#fafafa; }
html[data-sciver-theme="white"] .tm-stage-context-brief { background:#f0fdfa; color:#115e59; border-color:#99f6e4; }
html[data-sciver-theme="white"] .tm-stage-preview { color:var(--tm-muted)!important; }
html[data-sciver-theme="white"] .tm-stage-tool-message .tm-stage-json-viewer pre { color:var(--tm-muted)!important; }
html[data-sciver-theme="white"] :is(.tm-content .stage-canvas,.tm-content .stage-toolbar,
  .tm-workflow-view .stage-view-toggle,.tm-content .stage-preview-actions a) { background:var(--tm-hover); border-color:var(--tm-border); }
html[data-sciver-theme="white"] .tm-workflow-view .stage-view-toggle a[aria-current="page"] { background:#18181b; color:#fff; }
html[data-sciver-theme="white"] :is(.tm-notices-panel,.sc-chat-panel,.tm-setting-dialog) { box-shadow:0 12px 40px #18181b18; }
html[data-sciver-theme="white"] :is(.sc-chat-frame,.sc-chat-loading) { background:#fff; color-scheme:light; }
html[data-sciver-theme="white"] .sc-chat-launcher { border-color:#d4d4d8; background:#fff; box-shadow:0 4px 18px #18181b20; }
html[data-sciver-theme="white"] .sc-chat-action:hover { background:#e4e4e7; color:#18181b; }

/* Shared by the full chat, the lazy iframe and assistant-ui messages. */
html[data-sciver-theme="white"] :is(.tm-research-chat,.tm-chat-embedded) { color:#18181b; --loquix-text-color:#18181b; }
html[data-sciver-theme="white"] :is(.tm-research-toolbar,.tm-research-history,.tm-research-direct,
  .tm-research-entry,.tm-research-action,.tm-research-start,.tm-research-context,
  .tm-research-history-list a,.tm-aui-user-bubble,.tm-aui-composer,.tm-aui-supervisor-context,
  .tm-aui-scroll-bottom,.tm-aui-assistant-content pre,.tm-aui-user-bubble pre) {
  background:#fafafa; color:#18181b; border-color:#e4e4e7;
}
html[data-sciver-theme="white"] :is(.tm-research-toolbar h2,.tm-research-empty h3,
  .tm-research-start strong,.tm-research-direct-head label,.tm-research-history-head h3,
  .tm-aui-assistant-content,.tm-aui-user-bubble,.tm-aui-assistant-content code,.tm-aui-user-bubble code) { color:#18181b; }
html[data-sciver-theme="white"] :is(.tm-aui-action,.tm-aui-composer-note,.tm-aui-loading,
  .tm-aui-effort-picker,.tm-aui-select-wrap svg,.tm-aui-model-picker svg,
  .tm-research-empty p,.tm-research-context,.tm-research-compact-action,
  .tm-research-direct-head span,.tm-research-task-picker,.tm-research-history-list small) { color:#52525b; }
html[data-sciver-theme="white"] .tm-aui-input { color:#18181b!important; }
html[data-sciver-theme="white"] .tm-aui-composer-wrap { background:linear-gradient(transparent,#fff 12px); }
html[data-sciver-theme="white"] :is(.tm-aui-user-bubble,.tm-research-history-list a[aria-current="page"]) { background:#eff6ff; border-color:#bfdbfe; }
html[data-sciver-theme="white"] :is(.tm-aui-assistant-content code,.tm-aui-user-bubble code,
  .tm-aui-assistant-content th,.tm-aui-user-bubble th) { background:#f4f4f5; color:#18181b; }
html[data-sciver-theme="white"] :is(.tm-aui-assistant-content td,.tm-aui-user-bubble td,
  .tm-aui-assistant-content th,.tm-aui-user-bubble th) { border-color:#d4d4d8; }
html[data-sciver-theme="white"] :is(.tm-aui-send,.tm-research-direct-send) { background:#2563eb; color:#fff; border-color:#2563eb; }
html[data-sciver-theme="white"] :is(.tm-aui-send,.tm-research-direct-send):hover { background:#1d4ed8; color:#fff; }
html[data-sciver-theme="white"] :is(.tm-aui-error,.tm-research-error) { color:#b91c1c; background:#fef2f2; border-color:#fecaca; }
html[data-sciver-theme="white"] .tm-aui-retry-row { background:#fffbeb; color:#92400e; border-color:#fde68a; }

/* Task settings and the Console's compact cards use legacy fixed colors. */
html[data-sciver-theme="white"] :is(.tm-task-settings-panel,.tm-settings-task-picker,
  .tm-settings-audit .tm-setting-gear-submenu,.tm-setting-card-surface,.tm-setting-gear-submenu,
  .tm-setting-tooltip,.tm-tui-kv,.tm-tui-agent-table th,.tm-tui-agent-state,.tm-tui-default,
  .tm-tui-route-set,.tm-usage-hit,.tm-usage-popover,.tm-focus-mark,.tm-focus-latest,
  .tm-insight-more pre,.tm-history-tab,.tm-cycle-group,.tm-cycle-summary,
  .tm-recent-event-card,.tm-event,.tm-worker-tab,.tm-quota-auto,.tm-budget-track,
  .tm-native-goal-preview,.tm-budget-modal,.tm-budget-modal-header,.tm-budget-modal-review,
  .tm-budget-modal-figures > div,.tm-onboarding pre) {
  background:#fafafa; color:var(--tm-text); border-color:var(--tm-border);
}
html[data-sciver-theme="white"] :is(.tm-task-settings-panel,.tm-usage-popover,.tm-budget-modal) { background:#fff; }
html[data-sciver-theme="white"] :is(.tm-task-settings-head h1,.tm-task-settings-panel > h2,
  .tm-settings-task-picker label,.tm-settings-audit .tm-setting-gear-submenu > summary,
  .tm-setting-gear-menu-title,.tm-setting-tooltip,.tm-tui-summary-extra,.tm-usage-cost,
  .tm-budget-modal h2,.tm-budget-modal h3,.tm-native-goal-preview strong,.tm-onboarding) { color:var(--tm-text); }
html[data-sciver-theme="white"] :is(.tm-task-settings-head p,.tm-task-settings-hint,
  .tm-setting-gear-status,.tm-setting-compact small,.tm-worker-tab small,.tm-tui-pending,
  .tm-usage-hit-empty .tm-usage-value,.tm-focus-latest span,.tm-cycle-summary time,
  .tm-budget-modal-empty,.tm-budget-modal-subtitle) { color:var(--tm-muted); }
html[data-sciver-theme="white"] .tm-usage-value { color:#6d28d9; }
html[data-sciver-theme="white"] :is(.tm-worker-tab.is-selected,.tm-history-tab[aria-selected="true"],
  .tm-recent-select[aria-pressed="true"],.tm-recent-event-target) { background:#eff6ff; color:#1d4ed8; border-color:#bfdbfe; }
html[data-sciver-theme="white"] :is(.tm-cycle-summary:hover,.tm-cycle-group[open] > .tm-cycle-summary,
  .tm-tui-agent-table tbody tr:hover,.tm-focus-latest:hover) { background:#f4f4f5; }
html[data-sciver-theme="white"] :is(.tm-tui-agent-table tbody tr.tm-agent-row-running > td,
  .tm-tui-state-running,.tm-tui-process-green) { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }
html[data-sciver-theme="white"] :is(.tm-tui-state-failed,.tm-tui-process-red,.tm-supervisor-hold,
  .tm-insight-alert .tm-focus-mark) { background:#fef2f2; color:#b91c1c; border-color:#fecaca; }
html[data-sciver-theme="white"] :is(.tm-tui-state-waiting,.tm-tui-state-rework,.tm-tui-state-blocked,
  .tm-tui-process-amber,.tm-tui-summary-details) { background:#fffbeb; color:#92400e; border-color:#fde68a; }
html[data-sciver-theme="white"] .tm-insight-alert .tm-focus-message { color:#b91c1c; }
html[data-sciver-theme="white"] .tm-native-goal-preview { background:#f5f3ff; border-color:#ddd6fe; }
html[data-sciver-theme="white"] .tm-role-research_lead { --role-accent:#1d4ed8; --role-background:#eff6ff; }
html[data-sciver-theme="white"] .tm-role-challenger { --role-accent:#92400e; --role-background:#fffbeb; }
html[data-sciver-theme="white"] .tm-role-investigator { --role-accent:#7e22ce; --role-background:#faf5ff; }
html[data-sciver-theme="white"] .tm-role-literature { --role-accent:#0f766e; --role-background:#f0fdfa; }
html[data-sciver-theme="white"] .tm-role-judge { --role-accent:#be123c; --role-background:#fff1f2; }
html[data-sciver-theme="white"] .tm-role-reporter { --role-accent:#15803d; --role-background:#f0fdf4; }
html[data-sciver-theme="white"] .tm-role-system { --role-accent:#52525b; --role-background:#f4f4f5; }
html[data-sciver-theme="white"] :is(.tm-hypothesis-card,.tm-token-scope,.tm-investigator-goal-intro,
  .tm-native-goal-dialog,.tm-native-goal-original,.tm-budget-modal-disclosure pre,
  .rp-field,.rp-field-head,.rp-field-body pre) { background:#fafafa; color:var(--tm-text); border-color:var(--tm-border); }
html[data-sciver-theme="white"] :is(.tm-hypothesis-card > strong,.tm-hypothesis-card-text,
  .tm-investigator-goal-intro strong,.tm-token-scope h4,.tm-token-scope > div strong,
  .tm-budget-label strong,.tm-native-goal-dialog p,.rp-field-title,.rp-field-body,.rp-field-body blockquote,
  .tm-explore-map-body strong,.tm-model-preset-trigger,.tm-cycle-summary strong) { color:var(--tm-text); }
html[data-sciver-theme="white"] :is(.tm-investigator-goal-intro p,.tm-explore-map-body > span,
  .tm-token-scope > div span,.tm-quota-auto-check,.tm-cycle-chevron) { color:var(--tm-muted); }
html[data-sciver-theme="white"] :is(.tm-hypothesis-card-label,.tm-hypothesis-card-action,
  .tm-cycle-outcome,.tm-explore-map-arrow) { color:#1d4ed8; }
html[data-sciver-theme="white"] .tm-native-goal-warning { background:#fffbeb; color:#92400e!important; }
html[data-sciver-theme="white"] .tm-native-goal-source { color:var(--tm-muted)!important; }
html[data-sciver-theme="white"] dialog::backdrop { background:#18181b55; }
html[data-sciver-theme="white"] :is(.tm-content .workflow-version-form select,
  button.tm-model-preset-tier[data-model-preset-open],.tm-route-save-dialog,
  .tm-route-save-dialog p,.rp-history,.rp-note,.ex-reader,.ex-selected,.ex-tooltip,
  .ex-toolbar,.ex-graph,.ex-status,.ex-legend,.ex-help,.ex-help pre) {
  background:#fafafa; color:var(--tm-text); border-color:var(--tm-border);
}
html[data-sciver-theme="white"] :is(.tm-cycle-label,.tm-cycle-verdict,.rp-history pre,
  .ex-reader pre,.ex-reader blockquote,.ex-reader code) { color:var(--tm-text); }
html[data-sciver-theme="white"] :is(.rp-head p,.rp-picker,.rp-toolbar,.rp-field-owner,
  .ex-reader .ex-meta,.ex-node-time,.ex-node-tags,.ex-node-tags summary) { color:var(--tm-muted); }
html[data-sciver-theme="white"] :is(.stage-canvas .edgeLabel,.stage-canvas .labelBkg,
  .stage-canvas .edgeLabel p) { background:#f4f4f5!important; color:#3f3f46!important; }
html[data-sciver-theme="white"] .stage-canvas .flowchart-link { stroke:#71717a!important; }
html[data-sciver-theme="white"] .ex-reader :is(pre,code,blockquote) { background:#f4f4f5; border-color:var(--tm-border); }
html[data-sciver-theme="white"] :is(.ex-legend-more-panel,.ex-tip-panel,.ex-tip-panel .ex-tip-entry,
  .ex-workspace:fullscreen) { background:#fff; color:var(--tm-text); border-color:var(--tm-border); }
html[data-sciver-theme="white"] :is(.ex-overview,.ex-type-key,.ex-overview-title,
  .ex-legend-more summary,.ex-tip-trigger,.ex-help code) { color:#3f3f46; }
html[data-sciver-theme="white"] #ex-g6 { background-image:radial-gradient(circle,#d4d4d8 .65px,transparent .8px); }

/* Legacy components must change foreground and surface together in light mode. */
html[data-sciver-theme="white"] :is(.tm-quota-section .tm-account-meta,
  .tm-quota-heading .tm-update-age,.ex-toolbar-main #ex-stats,.ex-tip-sub) { color:var(--tm-muted); }
html[data-sciver-theme="white"] .tm-quota-section .tm-account-switch {
  background:#2563eb; color:#fff; border-color:#2563eb;
}
html[data-sciver-theme="white"] .tm-quota-section .tm-account-switch:hover:not(:disabled) {
  background:#1d4ed8; color:#fff; border-color:#1d4ed8;
}
html[data-sciver-theme="white"] :is(.tm-explore-map,.tm-tui-kv-grid a.tm-explore-map) {
  background:#eff6ff; color:#1d4ed8; border-color:#bfdbfe;
}
html[data-sciver-theme="white"] :is(a.tm-explore-map:hover,.tm-tui-kv-grid a.tm-explore-map:hover,
  a.tm-explore-map:active) { background:#dbeafe; border-color:#60a5fa; }
html[data-sciver-theme="white"] .tm-explore-map-icon { background:#dbeafe; color:#1d4ed8; }
html[data-sciver-theme="white"] :is(.tm-setting-gear > summary,.tm-setting-gear-menu,
  .ex-actions .ex-zoom-percent,.ex-node-tags-list,.ex-ref,.ex-search) {
  background:#fff; color:var(--tm-text); border-color:#d4d4d8;
}
html[data-sciver-theme="white"] :is(.tm-setting-gear[open] > summary,
  .ex-legend-more[open] summary,.ex-actions button[aria-pressed="true"],
  .ex-layout-controls button[aria-pressed="true"]) {
  background:#dbeafe; color:#1e40af; border-color:#60a5fa;
}
html[data-sciver-theme="white"] :is(.tm-setting-gear > summary:hover,
  .ex-tip-trigger:hover,.ex-tip-trigger:focus-visible,.ex-tip-panel .ex-tip-entry button.ex-tip-item:hover,
  .ex-tip-panel .ex-tip-entry button.ex-tip-item:focus-visible) { background:#eff6ff; color:#1e40af; }
html[data-sciver-theme="white"] :is(.tm-run-settings-link,.ex-md a,.ex-node-tag-key,
  .ex-node-tag-value,.ex-tip-panel .ex-tip-entry button.ex-tip-item) { color:var(--tm-link); }
html[data-sciver-theme="white"] .ex-actions button.ex-color-mode {
  background:#fff; color:var(--tm-text); border-color:#a1a1aa; box-shadow:0 2px 8px #18181b18;
}
html[data-sciver-theme="white"] .ex-actions button.ex-color-mode:hover { background:#eff6ff; }
html[data-sciver-theme="white"] .ex-tip-panel .ex-tip-worker {
  background:#f0fdfa; color:#115e59; border-color:#5eead4;
}
html[data-sciver-theme="white"] :is(.ex-tip-panel-note,.ex-index-warning) { color:#854d0e; }
html[data-sciver-theme="white"] .ex-error { background:#fef2f2; color:#991b1b; border-color:#fca5a5; }
html[data-sciver-theme="white"] .ex-splitter:hover::before { background:#2563eb; }
html[data-sciver-theme="white"] :is(button,a,summary):focus-visible {
  outline:2px solid #2563eb; outline-offset:3px;
}
html[data-sciver-theme="white"] .tm-run-settings-link { background:#eff6ff; border-color:#bfdbfe; }
html[data-sciver-theme="white"] .tm-run-settings-link:hover { background:#dbeafe; }
html[data-sciver-theme="white"] .tm-run-quota-card-link:hover { background:#eff6ff; }
html[data-sciver-theme="white"] .ex-outcome-card .ex-outcome-part { color:#fff; }
html[data-sciver-theme="white"] .ex-node-source-strip .ex-source-link { color:#dbeafe; }
html[data-sciver-theme="white"] .tm-runtime-notice { --notice-accent:#1d4ed8; }
html[data-sciver-theme="white"] .tm-runtime-notice[data-tone="warning"] { --notice-accent:#92400e; }
html[data-sciver-theme="white"] .tm-runtime-notice[data-tone="error"] { --notice-accent:#b91c1c; }
html[data-sciver-theme="white"] .tm-tui-attention { background:#fffbeb; color:#92400e; border-color:#fcd34d; }
html[data-sciver-theme="white"] .tm-tui-attention :is(dt,dd,summary span) { color:#92400e; }
</style>"""
