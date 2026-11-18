"""Shared Sciver launcher; the lazy iframe uses the existing Research Chat runtime."""
from __future__ import annotations

import html

WIDGET_STYLE = """<style>
#sciver-chat-widget{font:13px/1.5 system-ui,sans-serif;color:#f4f4f5}
#sciver-chat-widget [hidden]{display:none!important}
.sc-chat-launcher{position:fixed;right:24px;bottom:22px;z-index:85;display:grid;place-items:center;width:52px;height:52px;padding:0;border:1px solid #516985;border-radius:17px;background:#07162f;box-shadow:0 6px 24px #0006;cursor:pointer;transition:transform .15s,border-color .15s}
.sc-chat-launcher{touch-action:none;user-select:none}
.sc-chat-launcher:hover{border-color:#a8cfff}
.sc-chat-launcher[data-dragging="true"]{cursor:grabbing}
.sc-chat-launcher img{width:42px;height:42px;border-radius:12px}
.sc-chat-launcher-label{position:absolute;right:64px;padding:6px 10px;white-space:nowrap;border:1px solid #3c4350;border-radius:8px;background:#181b23;color:#f4f4f5;font-size:12px;opacity:0;pointer-events:none}
.sc-chat-launcher:hover .sc-chat-launcher-label,.sc-chat-launcher:focus-visible .sc-chat-launcher-label{opacity:1}
.sc-chat-dot{position:absolute;right:0;top:0;width:10px;height:10px;border:2px solid #11151e;border-radius:50%;background:#93c5fd}
.sc-chat-launcher[data-busy="true"] .sc-chat-dot{animation:sc-chat-pulse 1.4s infinite}
.sc-chat-panel{position:fixed;z-index:86;display:flex;flex-direction:column;box-sizing:border-box;overflow:hidden;border:1px solid #414957;border-radius:16px;background:#111114;box-shadow:0 18px 70px #0009;isolation:isolate}
.sc-chat-header{display:flex;flex:0 0 54px;align-items:center;gap:9px;padding:0 10px 0 15px;border-bottom:1px solid #303039;background:#181b22;cursor:grab;touch-action:none;user-select:none}
.sc-chat-header:active{cursor:grabbing}
.sc-chat-header img{width:29px;height:29px;border-radius:8px}
.sc-chat-heading{min-width:0;flex:1;line-height:1.3}
.sc-chat-heading strong{display:block;font-size:13px;font-weight:650}
.sc-chat-heading span{display:block;font-size:10px;color:#9ca7b8;margin-top:2px}
.sc-chat-actions{display:flex;gap:3px}
.sc-chat-action{display:grid;place-items:center;box-sizing:border-box;width:30px;height:30px;padding:0;border:0;border-radius:8px;background:transparent;color:#b6bfce;cursor:pointer;text-decoration:none}
.sc-chat-action:hover{background:#2b303c;color:#fff}
.sc-chat-action svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
#sciver-chat-widget button:focus-visible,#sciver-chat-widget a:focus-visible,[data-sc-chat-resize]:focus-visible{outline:2px solid #93c5fd;outline-offset:-3px}
.sc-chat-body{position:relative;display:flex;flex:1;min-height:0}
.sc-chat-frame{width:100%;height:100%;flex:1;border:0;background:#111114;color-scheme:dark}
.sc-chat-loading{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:#111114;color:#a7b1c1;font-size:12px}
.sc-chat-loading button{border:1px solid #47566c;border-radius:8px;background:#202a39;color:#e0edff;padding:6px 14px;font:inherit;cursor:pointer}
.sc-chat-panel[data-interacting="true"] .sc-chat-frame{pointer-events:none}
.sc-chat-resize{position:absolute;z-index:2;touch-action:none}
.sc-chat-resize-n{left:16px;right:16px;top:0;height:5px;cursor:ns-resize}
.sc-chat-resize-w{left:0;top:16px;bottom:16px;width:5px;cursor:ew-resize}
.sc-chat-resize-nw{left:0;top:0;width:17px;height:17px;cursor:nwse-resize;border-radius:15px 0 0 0}
.sc-chat-resize-nw::after{content:"";position:absolute;left:5px;top:5px;width:5px;height:5px;border-left:1px solid #75839a;border-top:1px solid #75839a;border-radius:2px 0 0}
.sc-chat-panel[data-maximized="true"] .sc-chat-resize{display:none}
@keyframes sc-chat-pulse{50%{opacity:.3}}
@media(max-width:600px){.sc-chat-launcher{right:16px;bottom:max(16px,env(safe-area-inset-bottom));width:48px;height:48px;border-radius:15px}.sc-chat-launcher img{width:39px;height:39px}.sc-chat-header{padding-left:12px;cursor:default}.sc-chat-resize{display:none}.sc-chat-launcher-label{display:none}}
@media(prefers-reduced-motion:reduce){.sc-chat-launcher{transition:none}.sc-chat-launcher[data-busy="true"] .sc-chat-dot{animation:none}}
</style>"""


def widget_html(task_id: str = "") -> str:
    return f'''<div id="sciver-chat-widget" data-task-id="{html.escape(task_id, quote=True)}">
<button type="button" class="sc-chat-launcher" id="sc-chat-launcher" aria-label="打开 Sciver 科研对话" aria-controls="sc-chat-panel" aria-expanded="false" aria-haspopup="dialog">
  <img src="/favicon.svg" alt=""><span class="sc-chat-launcher-label">和 Sciver 聊聊</span><span class="sc-chat-dot" hidden aria-hidden="true"></span>
</button>
<section class="sc-chat-panel" id="sc-chat-panel" role="dialog" aria-modal="false" aria-labelledby="sc-chat-title" hidden>
  <header class="sc-chat-header" data-sc-chat-drag><img src="/favicon.svg" alt=""><div class="sc-chat-heading"><strong id="sc-chat-title">Sciver</strong><span>Research Chat</span></div>
    <div class="sc-chat-actions">
      <a class="sc-chat-action" id="sc-chat-full" href="/research-chat/" target="_blank" rel="noopener" aria-label="在完整页面打开对话" title="在完整页面打开"><svg viewBox="0 0 24 24"><path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></svg></a>
      <button class="sc-chat-action" id="sc-chat-maximize" type="button" aria-label="展开对话窗口" title="展开窗口" aria-pressed="false"><svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 4v16"/></svg></button>
      <button class="sc-chat-action" id="sc-chat-minimize" type="button" aria-label="最小化对话" title="最小化，保留对话"><svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg></button>
    </div>
  </header>
  <div class="sc-chat-body"><iframe class="sc-chat-frame" id="sc-chat-frame" title="Sciver Research Chat" hidden></iframe>
    <div class="sc-chat-loading" id="sc-chat-loading" role="status"><span>正在打开科研对话…</span><button id="sc-chat-retry" type="button" hidden>重新加载</button><button id="sc-chat-start-over" type="button" hidden>开始新对话</button></div>
  </div>
  <div class="sc-chat-resize sc-chat-resize-n" data-sc-chat-resize="n" aria-hidden="true"></div>
  <div class="sc-chat-resize sc-chat-resize-w" data-sc-chat-resize="w" aria-hidden="true"></div>
  <div class="sc-chat-resize sc-chat-resize-nw" data-sc-chat-resize="nw" role="button" tabindex="0" aria-label="调整窗口大小，使用方向键" title="拖动调整大小"></div>
</section></div><script src="/research-chat/widget.js" defer></script>'''
