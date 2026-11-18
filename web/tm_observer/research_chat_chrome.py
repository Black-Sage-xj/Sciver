"""Shared Codex-style Research Chat chrome, layered over the legacy shell."""

CHROME_STYLE = """
.tm-research_chat-view .tm-content:has(.tm-chat-codex){padding:0 12px 12px;background:#191919}
.tm-research-chat.tm-chat-codex{--chat-bg:#191919;--chat-panel:#292929;--chat-input:#333333;--chat-line:#404040;--chat-text:#ededed;--chat-muted:#a0a0a0;--chat-hover:#303030;--chat-effort:#b889f5;background:var(--chat-bg);color:var(--chat-text);position:relative;isolation:isolate}
.tm-chat-codex button,.tm-chat-codex input,.tm-chat-codex select,.tm-chat-codex textarea{font-family:inherit}
.tm-chat-codex :focus-visible{outline:2px solid #a3a3a3;outline-offset:3px}
.tm-chat-codex .tm-research-toolbar{min-height:46px;margin:0;padding:0 14px;gap:10px;border-bottom:1px solid #ffffff08}
.tm-chat-codex .tm-research-toolbar h2{font-weight:500;font-size:13px;color:var(--chat-muted)}
.tm-chat-codex .tm-research-toolbar .tm-research-model{display:none}
.tm-chat-codex .tm-research-title-rename{border:0;background:transparent;color:var(--chat-muted);font-size:11px;padding:5px 7px}
.tm-chat-codex .tm-research-title-rename:hover{background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-research-project-context{margin-left:auto;color:var(--chat-muted)}
.tm-chat-codex .tm-research-layout{grid-template-columns:220px minmax(0,1fr);gap:0;position:relative}
.tm-chat-codex .tm-research-main{grid-column:2;grid-row:1;padding:0 24px;min-width:0;border:0}
.tm-chat-codex .tm-research-history{grid-column:1;grid-row:1;border:0;border-right:1px solid #ffffff09;padding:16px 12px 10px 0;gap:13px;background:var(--chat-bg)}
.tm-chat-codex .tm-research-history-head{display:grid;grid-template-columns:1fr 28px;gap:8px}
.tm-chat-codex .tm-research-history-head h3{font-size:11px;font-weight:500;letter-spacing:0;color:var(--chat-muted);padding-left:10px}
.tm-chat-codex .tm-research-history-toggle{grid-column:2;grid-row:1;width:28px;height:28px;flex-basis:28px;border:0;background:transparent;color:var(--chat-muted);font-size:19px}
.tm-chat-codex .tm-research-history-toggle:hover{background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-research-new{grid-column:1/-1;justify-content:flex-start;gap:8px;min-height:35px;border:0;border-radius:7px;background:transparent;color:var(--chat-text);font-size:12px;font-weight:450;padding:8px 10px}
.tm-chat-codex .tm-research-new:hover{background:var(--chat-panel)}
.tm-chat-codex .tm-chat-history-controls{gap:7px;padding:0 3px}
.tm-chat-codex .tm-chat-history-controls input{background:#ffffff05;border:1px solid #ffffff0d;border-radius:7px;min-height:31px;padding:6px 9px;color:var(--chat-text);font-size:11px}
.tm-chat-codex .tm-chat-history-controls input::placeholder{color:var(--chat-muted)}
.tm-chat-codex .tm-research-history-list{gap:2px;padding:0 2px;scrollbar-width:thin;scrollbar-color:#555 transparent}
.tm-chat-codex .tm-research-history-list a{border:0;border-radius:7px;padding:8px 9px;font-size:12px;color:var(--chat-muted);line-height:1.5}
.tm-chat-codex .tm-research-history-list a:hover{background:#ffffff06;color:var(--chat-text)}
.tm-chat-codex .tm-research-history-list a.active{background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-research-history-list a span{-webkit-line-clamp:1}
.tm-chat-codex .tm-research-history-list small{font-size:10px;color:var(--chat-muted);margin-top:3px;opacity:.65}
.tm-chat-codex .tm-chat-history-row{padding-right:22px;min-width:0}
.tm-chat-codex .tm-chat-history-menu{top:6px;right:0;z-index:auto}
.tm-chat-codex .tm-chat-history-menu summary{display:grid;place-items:center;width:22px;height:25px;border-radius:5px;color:var(--chat-muted);font-size:16px;opacity:.4}
.tm-chat-codex .tm-chat-history-row:hover .tm-chat-history-menu summary,.tm-chat-codex .tm-chat-history-menu:focus-within summary,.tm-chat-codex .tm-chat-history-menu[open] summary{opacity:1}
.tm-chat-codex .tm-chat-history-menu summary:hover{background:var(--chat-panel)}
.tm-chat-codex .tm-research-entry{width:min(800px,100%);margin:auto;gap:22px;padding:36px 0 56px;overflow:auto;scrollbar-width:thin}
.tm-chat-codex .tm-chat-welcome{text-align:center;padding:0 12px;margin-bottom:5px}
.tm-chat-codex .tm-chat-welcome h1{font-size:clamp(23px,2.3vw,30px);font-weight:500;letter-spacing:-.035em;line-height:1.4;color:var(--chat-text);margin:0}
.tm-chat-codex .tm-chat-welcome p{font-size:13px;line-height:1.7;color:var(--chat-muted);margin:12px 0 0}
.tm-chat-codex .tm-chat-first-composer{min-width:0;padding-top:1px}
.tm-chat-codex .tm-chat-project-bar{display:flex;align-items:center;gap:10px;min-height:46px;margin:0 13px -8px;padding:0 17px 8px;border-radius:20px 20px 0 0;background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-chat-project-bar>svg{width:18px;height:18px;flex:none;fill:none;stroke:var(--chat-muted);stroke-width:1.5;stroke-linejoin:round}
.tm-chat-codex .tm-chat-project-bar .tm-research-task-picker{margin:0;max-width:none;flex:1}
.tm-chat-codex .tm-chat-project-bar select{min-height:36px;width:100%;max-width:100%;padding:5px 5px 5px 0;background:transparent;color:var(--chat-text);font-size:12px;border:0;outline-offset:0;cursor:pointer;text-overflow:ellipsis}
.tm-chat-codex .tm-chat-project-bar select option{background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-research-direct{gap:4px;padding:16px 17px 12px;background:var(--chat-input);border:1px solid #ffffff0c;border-radius:25px;box-shadow:none;overflow:visible}
.tm-chat-codex .tm-research-direct:focus-within{border-color:#ffffff24;box-shadow:none}
.tm-chat-codex .tm-research-direct textarea{font-size:15px;line-height:1.65;min-height:91px;max-height:270px;padding:0 3px;color:var(--chat-text);resize:none}
.tm-chat-codex .tm-research-direct textarea::placeholder{color:#929292}
.tm-chat-codex .tm-research-direct-footer{gap:8px;padding-top:4px;min-height:34px;align-items:center}
.tm-chat-codex .tm-research-direct-settings{margin-left:auto;flex-wrap:nowrap;gap:9px;min-width:0}
.tm-chat-codex .tm-research-direct-send{min-width:34px;min-height:34px;width:34px;height:34px;border:0;border-radius:50%;padding:7px;background:#eeeeee;color:#252525;flex:none}
.tm-chat-codex .tm-research-direct-send:hover{background:#fff}
.tm-chat-codex .tm-research-direct-send:disabled{opacity:.3}
.tm-chat-codex .tm-research-direct-send svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.tm-chat-codex .tm-research-prompt-count{margin-left:2px;color:var(--chat-muted);font-size:10px}
.tm-chat-codex details[data-chat-popover]{position:relative}
.tm-chat-codex details[data-chat-popover]>summary,.tm-chat-codex .tm-chat-history-menu>summary{list-style:none;cursor:pointer;user-select:none}
.tm-chat-codex details[data-chat-popover]>summary::-webkit-details-marker,.tm-chat-codex .tm-chat-history-menu>summary::-webkit-details-marker{display:none}
.tm-chat-codex .tm-chat-add-menu>summary{display:grid;place-items:center;width:30px;height:30px;border-radius:7px;color:var(--chat-text)}
.tm-chat-codex .tm-chat-add-menu>summary:hover,.tm-chat-codex .tm-chat-model-menu>summary:hover{background:#ffffff0c}
.tm-chat-codex .tm-chat-add-menu>summary svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round}
.tm-chat-codex .tm-chat-model-menu>summary{display:flex;align-items:center;gap:6px;border-radius:7px;min-height:30px;padding:3px 5px;color:var(--chat-text);font-size:12px;white-space:nowrap}
.tm-chat-codex .tm-chat-model-menu>summary svg{width:14px;height:14px;fill:none;stroke:var(--chat-muted);stroke-width:1.7;flex:none}
.tm-chat-codex .tm-chat-effort-value{color:var(--chat-effort)}
.tm-chat-codex .tm-chat-popover-panel{position:absolute;bottom:calc(100% + 9px);left:0;z-index:25;display:flex;flex-direction:column;gap:3px;min-width:180px;max-width:min(290px,85vw);padding:6px;background:var(--chat-panel);border:1px solid var(--chat-line);border-radius:11px;box-shadow:0 12px 35px #0005;color:var(--chat-text)}
.tm-chat-codex details:not([open])>.tm-chat-popover-panel{display:none}
.tm-chat-codex .tm-chat-popover-panel button,.tm-chat-codex .tm-chat-popover-panel a{display:block;padding:8px 10px;margin:0;border:0;border-radius:5px;background:transparent;color:var(--chat-text);text-align:left;text-decoration:none;white-space:nowrap;font-size:12px;line-height:1.5;cursor:pointer}
.tm-chat-codex .tm-chat-popover-panel button:hover,.tm-chat-codex .tm-chat-popover-panel a:hover{background:#ffffff0b}
.tm-chat-codex .tm-chat-popover-panel label{display:flex;flex-direction:column;align-items:stretch;gap:7px;padding:7px 8px;font-size:11px;color:var(--chat-muted)}
.tm-chat-codex .tm-chat-popover-panel select{width:100%;max-width:none;min-height:32px;padding:5px 8px;border:1px solid var(--chat-line);border-radius:6px;background:var(--chat-bg);color:var(--chat-text);font-size:12px}
.tm-chat-codex .tm-chat-model-menu .tm-chat-popover-panel{right:0;left:auto;min-width:235px}
.tm-chat-codex .tm-chat-history-filter>summary{font-size:10px;color:var(--chat-muted);padding:0 5px;text-align:right}
.tm-chat-codex .tm-chat-history-filter .tm-chat-popover-panel{top:calc(100% + 6px);bottom:auto;left:0;min-width:190px}
.tm-chat-codex .tm-chat-history-menu .tm-chat-popover-panel{top:calc(100% + 4px);bottom:auto;left:auto;right:0;min-width:110px}
.tm-chat-codex .tm-chat-export{display:block;flex:none}
.tm-chat-codex .tm-chat-export>summary{display:grid;place-items:center;min-width:27px;min-height:27px;font-size:19px;color:var(--chat-muted);border-radius:6px}
.tm-chat-codex .tm-chat-export>summary:hover{background:var(--chat-panel)}
.tm-chat-codex .tm-chat-export .tm-chat-popover-panel{top:calc(100% + 5px);bottom:auto;right:0;left:auto}
.tm-chat-codex .tm-chat-entry-tools{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:center;gap:10px 25px}
.tm-chat-codex .tm-chat-entry-tools>details{font-size:12px;color:var(--chat-muted)}
.tm-chat-codex .tm-chat-entry-tools>details>summary{list-style:none;cursor:pointer;padding:4px 3px;text-align:center;white-space:nowrap}
.tm-chat-codex .tm-chat-entry-tools>details>summary::-webkit-details-marker{display:none}
.tm-chat-codex .tm-chat-project-options>summary{border:1px solid var(--chat-border,#52525b);border-radius:8px;padding:7px 12px!important;color:var(--chat-text,#f4f4f5);font-weight:600}
.tm-chat-codex .tm-chat-project-options>summary:focus-visible{outline:2px solid #93c5fd;outline-offset:2px}
.tm-chat-codex .tm-chat-entry-tools>details>summary span{margin-left:5px;color:var(--chat-muted)}
.tm-chat-codex .tm-chat-entry-tools>details[open]{width:100%}
.tm-chat-codex .tm-chat-entry-tools>details[open]>summary{margin-bottom:14px}
.tm-chat-codex .tm-research-starts{gap:8px;margin-bottom:8px}
.tm-chat-codex .tm-chat-intent-starts{grid-template-columns:repeat(3,minmax(0,1fr))}
.tm-chat-codex .tm-research-start,.tm-chat-codex .tm-research-start.primary{min-height:0;border:1px solid #ffffff0b;border-radius:10px;padding:13px 14px;background:transparent;color:var(--chat-text)}
.tm-chat-codex .tm-research-start:hover{border-color:#ffffff1c;background:#ffffff04}
.tm-chat-codex .tm-research-start[aria-pressed="true"]{border-color:#ffffff3b;box-shadow:none;background:#ffffff05}
.tm-chat-codex .tm-research-start-icon{display:none}
.tm-chat-codex .tm-research-start strong{font-size:12px;font-weight:500;margin-bottom:5px}
.tm-chat-codex .tm-research-start-description,.tm-chat-codex .tm-research-secondary-starts .tm-research-start-description{font-size:11px;line-height:1.6;color:var(--chat-muted)}
.tm-chat-codex .tm-prep-entry{margin:0;padding:14px;border:1px solid #ffffff0b;border-radius:10px;background:transparent;color:var(--chat-text)}
.tm-chat-codex .tm-prep-entry h2{font-size:13px;font-weight:500}
.tm-chat-codex .tm-prep-entry p{font-size:12px;color:var(--chat-muted)}
.tm-chat-codex .tm-prep-entry-options button{border:1px solid #ffffff10;border-radius:7px;padding:12px;background:var(--chat-panel);color:var(--chat-text)}
.tm-chat-codex .tm-prep-entry-options strong{font-size:12px;font-weight:500}
.tm-chat-codex .tm-prep-entry-options span{font-size:11px;color:var(--chat-muted)}
.tm-chat-codex .tm-research-migrate{margin:13px 0 0;color:var(--chat-muted);font-size:11px}
.tm-chat-codex .tm-research-migrate button{background:var(--chat-panel);border-color:var(--chat-line);color:var(--chat-text)}
.tm-chat-codex.history-collapsed .tm-research-layout{grid-template-columns:44px minmax(0,1fr);gap:0}
.tm-chat-codex.history-collapsed .tm-research-history{padding:16px 6px 10px 0;align-items:center}
.tm-chat-codex.history-collapsed .tm-research-history-head{display:flex;flex-direction:column;gap:8px}
.tm-chat-codex.history-collapsed .tm-research-new{width:30px;height:30px;min-height:30px;padding:0;justify-content:center}
.tm-chat-codex.history-collapsed .tm-research-history-head h3,.tm-chat-codex.history-collapsed .tm-chat-history-controls,.tm-chat-codex.history-collapsed .tm-research-history-list{display:none}
.tm-chat-codex .tm-research-error{color:#f3a8a8}
html[data-sciver-theme="white"] .tm-research_chat-view .tm-content:has(.tm-chat-codex){background:#fff}
html[data-sciver-theme="white"] .tm-research-chat.tm-chat-codex{--chat-bg:#fff;--chat-panel:#f2f2f2;--chat-input:#f4f4f4;--chat-line:#d9d9d9;--chat-text:#222;--chat-muted:#737373;--chat-hover:#eee;--chat-effort:#8050bb}
html[data-sciver-theme="white"] .tm-chat-codex :is(.tm-research-toolbar,.tm-research-main,.tm-research-history,.tm-research-entry){background:var(--chat-bg);color:var(--chat-text);border-color:#00000009}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-direct{background:var(--chat-input);border-color:#0000000a;color:var(--chat-text);box-shadow:none}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-direct:focus-within{border-color:#00000026;box-shadow:none}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-project-bar{background:var(--chat-panel)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-project-bar select{background:transparent;color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex :is(.tm-research-start,.tm-prep-entry){background:transparent;border-color:#00000012;color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-start[aria-pressed="true"]{border-color:#00000045;background:#00000003}
html[data-sciver-theme="white"] .tm-chat-codex .tm-prep-entry-options button{background:var(--chat-panel);border-color:#00000012;color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-history-list a{background:transparent;color:var(--chat-muted)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-history-list a.active,html[data-sciver-theme="white"] .tm-chat-codex .tm-research-history-list a:hover{background:var(--chat-panel);color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-history-controls input{background:#00000003;border-color:#00000012;color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-popover-panel{background:var(--chat-panel);color:var(--chat-text);box-shadow:0 10px 30px #0002}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-popover-panel :is(button,a){background:transparent;color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-popover-panel :is(button,a):hover{background:#00000008}
html[data-sciver-theme="white"] .tm-chat-codex .tm-chat-popover-panel select{background:var(--chat-bg);border-color:var(--chat-line);color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-direct-send{background:#292929;color:#fff}
html[data-sciver-theme="white"] .tm-chat-codex .tm-research-direct textarea{color:var(--chat-text)}
html[data-sciver-theme="white"] .tm-chat-codex :is(.tm-chat-welcome h1,.tm-research-toolbar h2,.tm-research-start strong){color:var(--chat-text)}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-toolbar{min-height:42px;flex-basis:42px;padding:0 2px;border-color:#ffffff09}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-layout,.tm-chat-embedded .tm-research-chat.tm-chat-codex.history-collapsed .tm-research-layout{display:grid;grid-template-columns:0 minmax(0,1fr);gap:0}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-main{grid-column:2;padding:0;min-width:0}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-history{display:flex;position:absolute;inset:0 auto 0 0;width:min(255px,calc(100% - 18px));height:100%;max-height:100%;z-index:30;border:1px solid var(--chat-line);border-radius:0 12px 12px 0;padding:12px;background:var(--chat-bg);box-shadow:12px 0 35px #0006}
.tm-chat-embedded .tm-research-chat.tm-chat-codex.history-collapsed .tm-research-history{display:none}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-entry{margin:auto;flex:0 1 auto;gap:20px;padding:28px 0;max-height:100%}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-chat-welcome h1{font-size:23px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-direct{margin:0;padding:13px 13px 10px;border-radius:22px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-direct textarea{min-height:84px;font-size:14px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-direct-send{width:32px;height:32px;min-width:32px;min-height:32px;padding:6px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-direct-footer{align-items:center;gap:4px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-direct-settings{flex-wrap:nowrap;gap:6px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-chat-model-menu>summary{gap:4px;font-size:11px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-chat-popover-panel label{display:flex;flex-direction:column;align-items:stretch;gap:7px;font-size:11px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-chat-popover-panel select{width:100%;max-width:none;min-height:32px;padding:5px 8px;font-size:12px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-start.primary{min-height:0;padding:13px 14px;border-radius:10px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-chat-intent-starts{grid-template-columns:1fr}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-start strong{font-size:12px;font-weight:500;margin-bottom:5px}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-start-description{font-size:11px;line-height:1.6}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-secondary-starts{grid-template-columns:1fr}
.tm-chat-embedded .tm-research-chat.tm-chat-codex .tm-research-migrate{display:block}
@media(max-width:1100px){.tm-chat-codex .tm-research-main{padding:0 18px}.tm-chat-codex .tm-research-entry{width:min(740px,100%)}}
@media(max-width:900px){
 .tm-research_chat-view .tm-app{display:flex;flex-direction:column;height:100vh;height:100dvh;min-height:0;overflow:hidden}
 .tm-research_chat-view .tm-sidebar{flex:0 0 auto}
 .tm-research_chat-view .tm-main{height:auto;flex:1;min-height:0}
}
@media(max-width:760px){
 .tm-research_chat-view .tm-content:has(.tm-chat-codex){padding:0 8px 8px}
 .tm-chat-codex .tm-research-toolbar{padding:0 4px;gap:7px;min-height:42px}
 .tm-chat-codex .tm-research-toolbar h2{font-size:12px}
 .tm-chat-codex .tm-research-layout,.tm-chat-codex.history-collapsed .tm-research-layout{grid-template-columns:34px minmax(0,1fr);grid-template-rows:minmax(0,1fr);gap:0}
 .tm-chat-codex .tm-research-main{grid-column:2;grid-row:1;padding:0 3px 0 6px}
 .tm-chat-codex .tm-research-history{position:absolute;inset:0 auto 0 0;width:min(260px,calc(100% - 20px));height:100%;max-height:100%;padding:12px;z-index:30;border:1px solid var(--chat-line);border-radius:0 12px 12px 0;background:var(--chat-bg);box-shadow:12px 0 35px #0006}
 .tm-chat-codex.history-collapsed .tm-research-history{position:relative;inset:auto;width:34px;max-height:100%;height:100%;padding:12px 0 0;flex-direction:column;justify-content:flex-start;box-shadow:none;border:0;border-radius:0;background:transparent}
 .tm-chat-codex.history-collapsed .tm-research-history-head{flex-direction:column;gap:6px}
 .tm-chat-codex.history-collapsed .tm-research-new{font-size:0;width:28px;height:28px;min-height:28px;padding:0}
 .tm-chat-codex.history-collapsed .tm-research-new::before{content:"+";font-size:18px}
 .tm-chat-codex .tm-research-history-list{flex-direction:column;max-height:none;overflow:auto}
 .tm-chat-codex .tm-research-history-list a{flex:none}
 .tm-chat-codex .tm-research-entry{gap:20px;padding:28px 0 38px}
 .tm-chat-codex .tm-chat-welcome h1{font-size:23px}
 .tm-chat-codex .tm-chat-project-bar{margin:0 10px -7px;padding:0 12px 7px;min-height:42px;border-radius:17px 17px 0 0}
 .tm-chat-codex .tm-research-direct{padding:13px 12px 10px;border-radius:22px}
 .tm-chat-codex .tm-research-direct textarea{font-size:14px;min-height:90px}
 .tm-chat-codex .tm-chat-model-menu>summary{font-size:11px;gap:4px}
 .tm-chat-codex .tm-research-direct-settings{gap:4px}
 .tm-chat-codex .tm-research-direct-send{min-width:32px;min-height:32px;width:32px;height:32px;padding:6px}
 .tm-chat-codex .tm-research-starts{grid-template-columns:1fr}
 .tm-chat-codex .tm-research-title-form{flex-wrap:wrap}
}
"""
