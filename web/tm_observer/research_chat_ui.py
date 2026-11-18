"""Research Chat page shell; message/thread interaction is powered by assistant-ui."""
from __future__ import annotations

import html
import json
import hashlib
from pathlib import Path
from functools import lru_cache
from typing import Any

from .appearance_ui import LIGHT_STYLE, THEME_BOOTSTRAP_SCRIPT
from .research_chat_chrome import CHROME_STYLE

_STYLE = """
.tm-research_chat-view .tm-main{height:100vh;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.tm-research_chat-view .tm-topbar{flex:0 0 62px}
.tm-research_chat-view .tm-content{display:flex;flex:1;flex-direction:column;min-height:0;height:auto;max-width:none;overflow:hidden;padding:8px 18px 8px}
.tm-research_chat-view .tm-page-head{display:none}
.tm-research-chat{max-width:none;width:100%;margin:0;min-height:0;height:100%;flex:1;display:flex;flex-direction:column;color:#f4f4f5;--loquix-text-color:#f4f4f5}
.tm-research-toolbar{display:flex;align-items:center;gap:10px;min-height:38px;flex:0 0 auto;margin:0 0 8px}
.tm-research-toolbar h2{font-size:15px;font-weight:650;margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm-research-toolbar select{min-height:34px;border:1px solid #41414a;border-radius:8px;background:#18181c;color:#e4e4e7;padding:5px 9px}
.tm-research-model{max-width:210px;font-size:12px}
.tm-research-layout{display:grid;grid-template-columns:minmax(0,1fr) 258px;grid-template-rows:minmax(0,1fr);gap:16px;align-items:stretch;flex:1;min-height:0;overflow:hidden}
.tm-research-main{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden}
#tm-research-chat-app{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;height:100%;overflow:hidden}
#tm-research-chat-app .tm-aui-shell{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;height:100%;overflow:hidden}
#tm-research-chat-app .tm-aui-thread{flex:1;min-width:0;min-height:0;height:100%}
#tm-research-chat-app .tm-aui-viewport{min-height:0}
#tm-research-chat-app .tm-aui-composer-wrap{position:relative;bottom:auto;flex:0 0 auto}
.tm-research-history{position:sticky;top:0;align-self:stretch;min-width:0;min-height:0;height:100%;max-height:100%;overflow:hidden;border-left:1px solid #303038;padding:6px 0 8px 16px;display:flex;flex-direction:column;gap:10px}
.tm-research-history-head{display:flex;flex-direction:row;align-items:center;justify-content:space-between;gap:8px;flex:0 0 auto}
.tm-research-history-toggle{display:inline-flex;align-items:center;justify-content:center;flex:0 0 30px;width:30px;height:30px;padding:0;border:1px solid #41414a;border-radius:8px;background:#1b1b20;color:#e4e4e7;font-size:18px;line-height:1;cursor:pointer}
.tm-research-history-toggle:hover{background:#303039;border-color:#666671}
.tm-research-history-toggle:focus-visible{outline:2px solid #b4a4ff;outline-offset:2px}
.tm-research-history-head h3{font-size:12px;letter-spacing:.03em;color:#d4d4dc;margin:0;font-weight:650}
.tm-research-new{display:flex;align-items:center;justify-content:center;white-space:nowrap;min-height:36px;padding:8px 11px;border:1px solid #6d5bd0;border-radius:9px;background:#392b66;color:#fff;font-size:12px;font-weight:600;text-decoration:none;transition:background .15s ease,border-color .15s ease}
.tm-research-new:hover{background:#4c3887;border-color:#9580ff}
.tm-research-new:focus-visible{outline:2px solid #b4a4ff;outline-offset:2px}
.tm-research-history-list{display:flex;flex:1;flex-direction:column;gap:5px;min-height:0;overflow:auto;max-height:none;padding-right:4px;overscroll-behavior:contain}
.tm-research-history-list a{display:block;min-width:0;padding:10px 10px;border:1px solid transparent;border-radius:9px;color:#d4d4dc;text-decoration:none;font-size:12px;line-height:1.45;overflow-wrap:anywhere}
.tm-research-history-list a:hover{background:#202026;border-color:#383841}
.tm-research-history-list a.active{background:#282431;border-color:#51406e;color:#fff}
.tm-research-history-list a span{display:block;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tm-research-history-list small{display:block;color:#8f8f9c;font-size:10px;margin-top:5px}
.tm-research-empty{margin:0 0 18px;text-align:center;color:#d4d4dc}
.tm-research-empty h3{font-size:22px;color:#fafafa;font-weight:650;margin:10px 0}
.tm-research-empty p{font-size:13px;color:#a1a1aa;margin:0}
.tm-research-entry{width:min(820px,100%);max-height:100%;min-height:0;overflow:auto;margin:auto;display:flex;flex-direction:column;gap:16px;padding:24px 0}
.tm-research-task-picker{display:flex;flex:1;min-width:0;max-width:440px;margin-left:auto}
.tm-research-task-picker select{width:100%;min-width:0;font:inherit;font-size:12px;text-overflow:ellipsis}
.tm-research-project-context{flex:0 1 42%;min-width:0;color:#939dad;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm-research-starts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.tm-research-start{position:relative;border:1px solid #393941;border-radius:16px;background:#1a1a20;color:#f4f4f5;text-align:left;padding:19px;cursor:pointer;transition:background .15s,border-color .15s}
.tm-research-start:hover{border-color:#72727f;background:#24242b}
.tm-research-start:focus-visible{outline:2px solid #a692f3;outline-offset:3px}
.tm-research-start.primary{min-height:152px;background:linear-gradient(135deg,#252530,#1b1b23)}
.tm-research-start-icon{display:block;width:23px;height:23px;margin-bottom:17px;color:#b6a1f5;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.tm-research-start strong{display:block;font-size:17px;font-weight:650;line-height:1.4;margin-bottom:8px}
.tm-research-start-description{display:block;color:#a8a8b8;font-size:13px;line-height:1.65}
.tm-research-secondary-starts{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.tm-research-secondary-starts .tm-research-start{padding:15px 16px;border-radius:13px;min-height:100px}
.tm-research-secondary-starts strong{font-size:14px;margin-bottom:6px}
.tm-research-secondary-starts .tm-research-start-description{font-size:12px}
.tm-research-chat [hidden]{display:none!important}
.tm-research-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
html[data-sciver-theme="white"] .tm-research-start-description{color:#61616c}
html[data-sciver-theme="white"] .tm-research-start-icon{color:#7553bd}
html[data-sciver-theme="white"] .tm-research-project-context{color:#61616c}
html[data-sciver-theme="white"] .tm-research-chat .tm-research-start{background:#fff;color:#18181b;border-color:#d4d4d8}
html[data-sciver-theme="white"] .tm-research-chat .tm-research-start:hover{background:#f4f4f5}
html[data-sciver-theme="white"] .tm-research-chat .tm-research-start[aria-pressed="true"]{border-color:#8b72e8}
.tm-research-title-rename{flex:0 0 auto;min-height:30px;border:1px solid #41414a;border-radius:8px;background:#202026;color:#c9c9d3;padding:5px 9px;font-size:11px;cursor:pointer}
.tm-research-title-rename:hover{border-color:#777780;color:#fff}
.tm-research-title-form{display:flex;align-items:center;gap:5px;min-width:0}
.tm-research-title-form[hidden]{display:none}
.tm-research-title-form input{width:min(280px,35vw);min-width:100px;min-height:30px;border:1px solid #555561;border-radius:8px;background:#18181c;color:#f4f4f5;padding:5px 8px;font:inherit;font-size:12px}
.tm-research-title-form button{min-height:30px;border:1px solid #51406e;border-radius:8px;background:#30254c;color:#f4f4f5;padding:5px 8px;font-size:11px;cursor:pointer}
.tm-research-start[aria-pressed="true"]{border-color:#8b72e8;box-shadow:0 0 0 1px #8b72e855 inset}
.tm-research-recommend-label{font-size:12px;color:#a1a1aa;font-weight:600;letter-spacing:.02em}
.tm-research-direct{display:flex;flex:0 0 auto;flex-direction:column;gap:10px;padding:13px 14px 11px;border:1px solid #484852;border-radius:18px;background:#1b1b20;box-shadow:0 8px 28px #0003;box-sizing:border-box}
.tm-research-direct:focus-within{border-color:#8b72e8;box-shadow:0 0 0 2px #6d5bd022,0 8px 28px #0003}
.tm-research-direct-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.tm-research-direct-head label{font-size:14px;font-weight:650;color:#f4f4f5}
.tm-research-direct-head span,.tm-research-direct-hint{font-size:11px;color:#9696a3;line-height:1.5}
.tm-research-direct textarea{display:block;width:100%;min-width:0;min-height:76px;max-height:220px;resize:vertical;box-sizing:border-box;border:0;outline:0;background:transparent;color:#f4f4f5;font:inherit;font-size:14px;line-height:1.65;padding:4px 0}
.tm-research-direct textarea::placeholder{color:#a6a6b2;opacity:1}
.tm-research-direct-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;padding-top:6px}
.tm-research-direct-settings{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.tm-research-direct-settings label{display:inline-flex;align-items:center;gap:5px;font-size:0;color:#a8a8b4}
.tm-research-direct select{max-width:190px;min-height:30px;border:1px solid #41414a;border-radius:8px;background:#24242b;color:#e4e4e7;padding:4px 7px;font:inherit;font-size:11px}
.tm-research-prompt-count{font-size:10px;color:#888895;font-variant-numeric:tabular-nums}
.tm-research-direct-send{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:82px;min-height:35px;border:0;border-radius:10px;background:#7553d7;color:#fff;padding:7px 13px;font:inherit;font-size:12px;font-weight:650;cursor:pointer}
.tm-research-direct-send:hover{background:#8869e7}
.tm-research-direct-send:disabled{opacity:.38;cursor:not-allowed}
.tm-research-direct-hint{margin:0;padding:0 3px}
.tm-research-direct-clear{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border:0;border-radius:7px;background:transparent;color:#9393a1;padding:5px;cursor:pointer}.tm-research-direct-clear svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.tm-research-direct-clear:hover{border-color:#777780;color:#fff}
.tm-research-entry .tm-research-error{margin:0}
.tm-research-migrate{font-size:12px;color:#a1a1aa}
.tm-chat-history-controls{display:grid;gap:6px}.tm-chat-history-controls input,.tm-chat-history-controls select{width:100%;min-width:0;background:#202026;color:inherit;border:1px solid #41414a;border-radius:6px;padding:6px;font-size:11px}.tm-chat-history-row{position:relative;padding-right:22px}.tm-chat-history-menu{position:absolute;right:0;top:8px;font-size:12px;z-index:2}.tm-chat-history-menu summary{cursor:pointer;list-style:none}.tm-chat-history-menu button{display:block;padding:5px 9px;border:1px solid #444;background:#24242c;color:inherit;white-space:nowrap;cursor:pointer}.tm-chat-export{display:flex;gap:10px;font-size:11px}.history-collapsed .tm-chat-history-controls,.history-collapsed .tm-chat-export{display:none}[data-chat-first-attach]{font-size:11px;background:transparent;border:1px solid #555;color:inherit;border-radius:7px;padding:7px;cursor:pointer}.tm-research-migrate summary{cursor:pointer;width:max-content}
.tm-research-migrate button{border:1px solid #41414a;border-radius:8px;background:#1c1c21;color:#d4d4d8;padding:10px;margin-top:12px;cursor:pointer;text-align:left}
.tm-research-action{border:1px solid #78643e;background:#30291d;border-radius:12px;padding:14px;margin:0 0 12px;color:#f4f4f5;max-height:45vh;overflow:auto;flex:0 1 auto}
.tm-research-action h3{font-size:14px;margin:0 0 8px}.tm-research-action p{font-size:12px;line-height:1.6;color:#e5dcc8}
.tm-research-action pre,.tm-research-action textarea{box-sizing:border-box;width:100%;max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#1b1916;color:#f4f4f5;border:1px solid #514735;border-radius:8px;padding:10px;font:12px/1.6 ui-monospace,monospace}
.tm-research-action button{border:1px solid #5d5548;border-radius:8px;background:#3a3429;color:#f4f4f5;padding:8px 10px;margin:8px 8px 0 0;cursor:pointer}
.tm-research-action button[data-decision=confirm]{background:#365d43;border-color:#4b8a60}.tm-research-action button:disabled{opacity:.5;cursor:not-allowed}
.tm-research-error{min-height:18px;color:#fecdd3;font-size:12px;margin:8px 2px}
.tm-research-error:empty{display:none}
.tm-research-chat.history-collapsed .tm-research-layout{grid-template-columns:minmax(0,1fr) 46px;gap:8px}
.tm-research-chat.history-collapsed .tm-research-history{padding:6px 4px;align-items:center}
.tm-research-chat.history-collapsed .tm-research-history-head{flex-direction:column;gap:8px;align-items:center}
.tm-research-chat.history-collapsed .tm-research-history-head h3{display:none}
.tm-research-chat.history-collapsed .tm-research-history-list{display:none}
.tm-research-chat.history-collapsed .tm-research-new{display:inline-flex;align-items:center;justify-content:center;width:32px;min-height:32px;height:32px;padding:0;font-size:0}
.tm-research-chat.history-collapsed .tm-research-new::before{content:"+";font-size:16px}
@media(max-width:980px){.tm-research-layout{grid-template-columns:minmax(0,1fr) 220px;gap:12px}.tm-research-history{padding-left:10px}.tm-research-history-head{gap:6px}.tm-research-new{padding:7px 8px;font-size:11px}}
@media(max-width:760px){.tm-research_chat-view .tm-content{padding:6px 10px 8px}.tm-research-layout{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr) auto;gap:8px}.tm-research-main{grid-row:1;min-height:0}.tm-research-history{grid-row:2;position:relative;height:auto;min-height:0;border-left:0;border-top:1px solid #303038;padding:8px 0 0;max-height:112px}.tm-research-history-head{gap:8px}.tm-research-history-list{flex-direction:row;max-height:68px;overflow-x:auto;overflow-y:hidden}.tm-research-history-list a{flex:0 0 170px}.tm-research-toolbar{gap:7px}.tm-research-model{max-width:135px}.tm-research-secondary-starts{grid-template-columns:1fr}.tm-research-start.primary{min-height:140px;padding:16px}.tm-research-start strong{font-size:15px}.tm-research-start-description{font-size:12px}.tm-research-start-icon{margin-bottom:12px}.tm-research-secondary-starts .tm-research-start{min-height:0;padding:12px 14px}.tm-research-entry{padding:14px 0;gap:12px}.tm-research-direct{padding:11px;border-radius:14px}.tm-research-direct textarea{min-height:68px}.tm-research-direct-settings{gap:6px}.tm-research-direct select{max-width:135px}.tm-research-direct-send{min-width:70px}.tm-research-title-form{flex-wrap:wrap}.tm-research-title-form input{width:130px}.tm-research-toolbar h2{font-size:14px}.tm-research-chat.history-collapsed .tm-research-layout{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr) 36px;gap:6px}.tm-research-chat.history-collapsed .tm-research-history{display:flex;flex-direction:row;justify-content:flex-end;max-height:36px;padding:0;border:0}.tm-research-chat.history-collapsed .tm-research-history-head{flex-direction:row;gap:8px}.tm-research-chat.history-collapsed .tm-research-new{width:auto;min-height:32px;height:32px;padding:0 10px;font-size:12px}.tm-research-chat.history-collapsed .tm-research-new::before{content:none}}
"""

_SCRIPT = """<script>
(() => {
  const root = document.querySelector('.tm-research-chat');
  if (!root) return;
  const workspace = document.querySelector('meta[name="sciver-workspace"]')?.content || '';
  const storageKey = key => workspace ? 'sciver-workspace:' + workspace + ':' + key : key;
  const localStorage = {
    getItem: key => window.localStorage.getItem(storageKey(key)),
    setItem: (key, value) => window.localStorage.setItem(storageKey(key), value),
    removeItem: key => window.localStorage.removeItem(storageKey(key)),
  };
  const error = root.querySelector('.tm-research-error');
  const embedded = root.dataset.embedded === 'true';
  const chatHref = path => {
    const url = new URL(path, location.origin);
    if (embedded) {
      url.searchParams.set('embed', '1');
      const task = root.dataset.taskId || new URLSearchParams(location.search).get('task_id');
      if (task) url.searchParams.set('task_id', task);
    }
    return url.pathname + url.search;
  };
  const navigateChat = path => location.assign(chatHref(path));
  if (embedded) root.querySelectorAll('a[href^="/research-chat/"]').forEach(link => link.href = chatHref(link.getAttribute('href')));

  const entryChoices = root.querySelectorAll('[data-entry-choice]');
  entryChoices.forEach(button => button.addEventListener('click', () => {
    const choice = button.dataset.entryChoice;
    entryChoices.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    root.querySelectorAll('[data-entry-panel]').forEach(panel => { panel.hidden = panel.dataset.entryPanel !== choice; });
  }));

  const historyToggle = root.querySelector('[data-research-history-toggle]');
  if (historyToggle) {
    const historyKey = 'tm-research-chat-history-collapsed-v1';
    const setHistoryCollapsed = (collapsed) => {
      root.classList.toggle('history-collapsed', collapsed);
      historyToggle.setAttribute('aria-expanded', String(!collapsed));
      const label = collapsed ? '展开历史对话' : '收起历史对话';
      historyToggle.setAttribute('aria-label', label);
      historyToggle.title = label;
      historyToggle.textContent = collapsed ? '›' : '‹';
      root.querySelector('[data-research-embedded-history]')?.setAttribute('aria-expanded', String(!collapsed));
    };
    let collapsed = false;
    try {
      const saved = localStorage.getItem(historyKey);
      collapsed = embedded || (saved === null ? matchMedia('(max-width:1100px)').matches : saved === '1');
    } catch (_err) { collapsed = embedded || matchMedia('(max-width:1100px)').matches; }
    setHistoryCollapsed(collapsed);
    const toggleHistory = () => {
      const next = !root.classList.contains('history-collapsed');
      setHistoryCollapsed(next);
      if (!embedded) { try { localStorage.setItem(historyKey, next ? '1' : '0'); } catch (_err) {} }
    };
    historyToggle.addEventListener('click', toggleHistory);
    root.querySelector('[data-research-embedded-history]')?.addEventListener('click', toggleHistory);
    root.addEventListener('click', event => {
      if ((embedded || matchMedia('(max-width:760px)').matches) && !event.target.closest('.tm-research-history,[data-research-embedded-history]')) setHistoryCollapsed(true);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !root.querySelector('details[data-chat-popover][open],details.tm-chat-history-menu[open]') && (embedded || matchMedia('(max-width:760px)').matches) && !root.classList.contains('history-collapsed')) {
        event.preventDefault();setHistoryCollapsed(true);
      }
    });
  }
  async function request(path, body) {
    const response = await fetch(path, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify(body || {})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || ('请求失败 (' + response.status + ')'));
    return data;
  }
  const titleLabel = root.querySelector('[data-research-title-label]');
  const renameButton = root.querySelector('[data-research-rename]');
  const titleForm = root.querySelector('[data-research-title-form]');
  const titleInput = root.querySelector('[data-research-title-input]');
  const titleCancel = root.querySelector('[data-research-title-cancel]');
  if (renameButton && titleForm && titleInput && titleLabel) {
    const setTitleEditing = (editing) => {
      titleLabel.hidden = editing;
      renameButton.hidden = editing;
      titleForm.hidden = !editing;
      if (editing) { titleInput.value = titleLabel.textContent || ''; titleInput.focus(); titleInput.select(); }
    };
    renameButton.addEventListener('click', () => setTitleEditing(true));
    if (titleCancel) titleCancel.addEventListener('click', () => setTitleEditing(false));
    titleForm.addEventListener('submit', async event => {
      event.preventDefault();
      const title = titleInput.value.trim();
      if (!title) { if (error) error.textContent = '标题不能为空。'; titleInput.focus(); return; }
      const save = titleForm.querySelector('[type="submit"]');
      if (save) save.disabled = true;
      try {
        await request('/api/research-chat/' + encodeURIComponent(root.dataset.session || '') + '/title', {title});
        location.reload();
      } catch (err) {
        if (error) error.textContent = String(err.message || err);
        if (save) save.disabled = false;
      }
    });
  }
  const taskSelect = root.querySelector('[data-research-task]');
  const modeKey = 'tm-research-chat-selected-mode-v1';
  const skillKey = 'tm-research-chat-selected-skill-v1';
  const newModelKey = 'tm-research-chat-new-model-v1';
  const newEffortKey = 'tm-research-chat-new-effort-v1';
  const starters = {
    'research-discussion': {mode:'project', prompt:'我想讨论一个具体问题或新想法。请围绕我接下来提供的内容，结合项目资料解释现象、核查结论或推敲思路；需要判断结论时，请追溯证据并说明适用边界。我的问题或想法是：'},
    'research-progress': {mode:'frontier', prompt:'请帮我了解当前研究进展：目标是什么，主要路径分别做了什么，得到了哪些有证据支持的结果，目前还有哪些问题或卡点？请结合最新真实运行状态和研究记录，区分已完成工作、正在进行的工作与尚未验证的猜想。'},
    'research-intervention': {mode:'project', prompt:'我想调整当前研究方向。请先结合科研图谱和 Research Plan，帮我定位当前路径及其问题，再根据我接下来说明的修改意图和干预程度，选择合适的调整策略：修改计划、调整优先级、关闭分支，或从新猜想创建研究节点并推进。请具体说明调整会影响哪些节点和后续工作。我的调整想法是：'},
    'research-evidence': {mode:'project', prompt:'请核查当前项目最关键的科学结论：逐项追溯到原始证据、实验或推导，说明适用条件、误差边界、反例与尚缺的验证。优先检查会影响当前研究路径的结论，并给出对应记录链接。'},
    'research-next-steps': {mode:'project', prompt:'请结合当前目标、已有结果和资源约束，比较接下来可行的研究方案，推荐优先顺序、要验证的问题与完成标准。如果需要调整计划或研究方向，请说明具体改动及其对当前工作的影响。'}
  };
  const defaultSkill = mode => mode === 'frontier' ? 'research-progress' : 'research-discussion';
  try {
    const savedMode = localStorage.getItem(modeKey);
    if (savedMode === 'project' || savedMode === 'frontier') root.dataset.nextChatMode = savedMode;
    const savedSkill = localStorage.getItem(skillKey);
    root.dataset.nextChatSkill = starters[savedSkill]?.mode === root.dataset.nextChatMode ? savedSkill : defaultSkill(root.dataset.nextChatMode);
  } catch (_err) { root.dataset.nextChatSkill = defaultSkill(root.dataset.nextChatMode); }
  const selectedSkill = () => root.dataset.nextChatSkill || defaultSkill(root.dataset.nextChatMode);
  // Keep legacy skill IDs in draft keys; only their visible category is merged.
  const displayedSkill = () => ({'research-evidence':'research-discussion', 'research-intervention':'research-next-steps'}[selectedSkill()] || selectedSkill());
  const refreshSelectedCard = () => root.querySelectorAll('[data-research-skill]').forEach(card => card.setAttribute('aria-pressed', String(card.dataset.researchSkill === displayedSkill())));
  refreshSelectedCard();
  if (taskSelect) {
    const contextTask = root.dataset.taskId;
    if (contextTask && Array.from(taskSelect.options).some(option => option.value === contextTask)) taskSelect.value = contextTask;
    const showProject = () => { root.dataset.projectLabel = taskSelect.value ? taskSelect.selectedOptions[0]?.textContent || '' : ''; };
    showProject();
    taskSelect.addEventListener('change', showProject);
  }
  const directForm = root.querySelector('[data-research-direct]');
  const directInput = root.querySelector('[data-research-prompt]');
  const directSend = root.querySelector('[data-research-send]');
  const directCounter = root.querySelector('[data-research-prompt-count]');
  const clearDraftButton = root.querySelector('[data-research-clear-draft]');
  const newModelSelect = root.querySelector('[data-new-chat-model]');
  const newEffortSelect = root.querySelector('[data-new-chat-effort]');
  try {
    const savedModel = localStorage.getItem(newModelKey);
    if (newModelSelect && savedModel) {
      if (!Array.from(newModelSelect.options).some(option => option.value === savedModel)) newModelSelect.add(new Option(savedModel + '（当前）', savedModel));
      newModelSelect.value = savedModel;
    }
    const savedEffort = localStorage.getItem(newEffortKey);
    if (newEffortSelect && savedEffort) {
      if (!Array.from(newEffortSelect.options).some(option => option.value === savedEffort)) newEffortSelect.add(new Option(savedEffort + '（当前）', savedEffort));
      newEffortSelect.value = savedEffort;
    }
  } catch (_err) {}
  let pendingSessionId = '';
  let configuredSessionId = '';
  let configuredModel = '';
  let configuredEffort = '';
  let pendingTaskId = '';
  let activeDraftKey = '';
  let directSources = [];
  const sourceBox = document.createElement('div');
  sourceBox.className = 'tm-research-direct-sources';
  sourceBox.style.cssText = 'max-height:210px;overflow:auto';
  const renderDirectSources = () => {
    sourceBox.replaceChildren();
    for (const [index, source] of directSources.entries()) {
      const card = document.createElement('div'), link = document.createElement('a'), remove = document.createElement('button');
      card.style.cssText = 'padding:7px;margin:5px 0;border:1px solid #6668;border-radius:7px;font-size:12px';
      link.href = source.href; link.textContent = source.label + (source.section ? ' · ' + source.section : '');
      remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', '移除来源：' + source.label);
      remove.style.cssText = 'float:right;background:transparent;border:0;color:inherit';
      remove.addEventListener('click', () => {
        directSources.splice(index, 1); persistDirectSources(); renderDirectSources();
      });
      card.append(link, remove);
      const meta = document.createElement('small');
      meta.style.cssText = 'display:block;margin-top:3px;opacity:.75';
      meta.textContent = [source.hypothesis_id,source.cycle_id ? 'Cycle ' + source.cycle_id : '',source.source_version.slice(0,8)].filter(Boolean).join(' · ');
      card.append(meta);
      if (source.quote) { const text = document.createElement('blockquote'); text.textContent = source.quote; card.append(text); }
      sourceBox.append(card);
    }
  };
  const persistDirectSources = () => {
    try { localStorage.setItem(activeDraftKey + ':sources', JSON.stringify(directSources)); } catch (_err) {}
  };
  const directSourceKey = source => JSON.stringify([source.kind,source.id,source.section,source.source_version,source.quote]);
  const finishDirectSources = (submitted, session) => {
    const sent = new Set(submitted.map(directSourceKey));
    directSources = directSources.filter(source => !sent.has(directSourceKey(source)));
    persistDirectSources();
    // Sources added while the first send was pending belong to the next draft.
    try { localStorage.setItem('sciver-chat-sources-v1:research:' + session, JSON.stringify(directSources)); } catch (_err) {}
  };
  root.addEventListener('sciver-chat-carry-draft', event => {
    const session = event.detail?.session_id;
    if (!/^[a-f0-9]{32}$/.test(session || '')) return;
    try {
      localStorage.setItem('sciver-chat-sources-v1:research:' + session, JSON.stringify(directSources));
      localStorage.setItem('sciver-chat-composer-v2:research:' + session, directInput?.value || '');
    } catch (_err) {}
  });
  const draftKeyForCurrentContext = () => 'tm-research-chat-draft-v1:' + encodeURIComponent(JSON.stringify([
    taskSelect ? taskSelect.value : '', taskSelect?.value ? root.dataset.nextChatMode || 'project' : 'preparation',
    newModelSelect ? newModelSelect.value : 'gpt-6-sol',
    newEffortSelect ? newEffortSelect.value : 'medium',
  ])) + (!taskSelect?.value || selectedSkill() === defaultSkill(root.dataset.nextChatMode) ? '' : ':skill:' + selectedSkill());
  const persistDraftAt = key => {
    if (!key || !directInput) return;
    try {
      if (directInput.value) localStorage.setItem(key, directInput.value.slice(0, 120000));
      else localStorage.removeItem(key);
    } catch (_err) {}
  };
  const clearActiveDraft = () => {
    try { if (activeDraftKey) localStorage.removeItem(activeDraftKey); } catch (_err) {}
  };
  const refreshDirectInput = () => {
    if (directCounter && directInput) { directCounter.textContent = directInput.value.length + '/120000'; directCounter.hidden = directInput.value.length < 110000; }
    if (clearDraftButton && directInput) clearDraftButton.hidden = !directInput.value;
    if (directSend && directInput) directSend.disabled = !directInput.value.trim() || directInput.value.length > 120000 || directSend.dataset.busy === '1';
  };
  const loadDraftForCurrentContext = fallback => {
    activeDraftKey = draftKeyForCurrentContext();
    let saved = '';
    try { saved = localStorage.getItem(activeDraftKey) || ''; } catch (_err) {}
    if (directInput) directInput.value = saved || fallback || '';
    try { directSources = JSON.parse(localStorage.getItem(activeDraftKey + ':sources') || '[]'); } catch (_err) { directSources = []; }
    if (!Array.isArray(directSources)) directSources = [];
    directSources = directSources.filter(source => source && source.task_id === taskSelect?.value).slice(0, 4);
    renderDirectSources();
    refreshDirectInput();
    return Boolean(saved);
  };
  if (directInput) {
    directInput.before(sourceBox);
    loadDraftForCurrentContext('');
    window.addEventListener('message', event => {
      if (event.origin !== location.origin || event.source !== window.parent || window.parent === window
          || event.data?.type !== 'sciver-chat-add-source' || event.data.task_id !== taskSelect?.value) return;
      const source = event.data.source;
      if (!source || source.task_id !== taskSelect.value) return;
      const duplicate = directSources.some(item => item.kind === source.kind && item.id === source.id &&
        item.section === source.section && item.quote === source.quote && item.source_version === source.source_version);
      const failure = !duplicate && directSources.length >= 4 ? '每条消息最多附加 4 个来源，请先移除一个。' : '';
      if (failure) { if (error) error.textContent = failure; }
      else if (!duplicate) { directSources.push(source); persistDirectSources(); renderDirectSources(); }
      window.parent.postMessage({type:'sciver-chat-source-result',task_id:taskSelect.value,
        request_id:event.data.request_id,error:failure}, location.origin);
    });
    if (window.parent !== window) window.parent.postMessage({type:'sciver-chat-ready', task_id:taskSelect?.value || ''}, location.origin);
    directInput.addEventListener('input', () => { refreshDirectInput(); persistDraftAt(activeDraftKey); });
    directInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        let shortcut = 'enter';
        try { shortcut = localStorage.getItem('sciver-chat-send-shortcut') || 'enter'; } catch (_err) {}
        if (shortcut === 'ctrlEnter' && !event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        if (directForm && directInput.value.trim()) directForm.requestSubmit();
      }
    });
    if (clearDraftButton) clearDraftButton.addEventListener('click', () => {
      clearActiveDraft();
      directInput.value = '';
      refreshDirectInput();
      if (error) error.textContent = '';
      directInput.focus();
    });
    if (taskSelect) taskSelect.addEventListener('change', () => {
      persistDraftAt(activeDraftKey);
      loadDraftForCurrentContext('');
      if (error) error.textContent = '';
    });
    if (newModelSelect) newModelSelect.addEventListener('change', () => {
      persistDraftAt(activeDraftKey);
      try { localStorage.setItem(newModelKey, newModelSelect.value); } catch (_err) {}
      loadDraftForCurrentContext('');
    });
    if (newEffortSelect) newEffortSelect.addEventListener('change', () => {
      persistDraftAt(activeDraftKey);
      try { localStorage.setItem(newEffortKey, newEffortSelect.value); } catch (_err) {}
      loadDraftForCurrentContext('');
    });
  }
  if (directForm && directInput && directSend) {
    directForm.addEventListener('submit', async event => {
      event.preventDefault();
      const content = directInput.value.trim();
      const submittedSources = directSources.slice();
      const taskId = taskSelect ? taskSelect.value : '';
      const mode = taskId ? root.dataset.nextChatMode || 'project' : 'preparation';
      if (!content) { directInput.focus(); return; }
      if (content.length > 120000) { if (error) error.textContent = '消息不能超过 120000 字符。'; return; }
      if (pendingSessionId && taskId !== pendingTaskId) {
        if (error) error.textContent = '当前草稿已创建会话并绑定了原任务。请完成发送，或刷新页面重新开始。';
        return;
      }
      if (error) error.textContent = '';
      directSend.dataset.busy = '1';
      directSend.disabled = true;
      directSend.textContent = '正在发送…';
      directInput.disabled = true;
      if (taskSelect) taskSelect.disabled = true;
      root.querySelectorAll('[data-research-mode]').forEach(control => { control.disabled = true; });
      root.querySelectorAll('[data-new-chat-model],[data-new-chat-effort]').forEach(control => { control.disabled = true; });
      try {
        if (!pendingSessionId) {
          const created = await request('/api/research-chat/session', taskId
            ? {mode, task_id:taskId, skill:selectedSkill()}
            : {mode:'preparation', entry:'exploratory'});
          pendingSessionId = String(created.session_id || '');
          pendingTaskId = taskId;
          if (!pendingSessionId) throw new Error('未能创建聊天会话，请重试。');
        }
        const modelSelect = root.querySelector('[data-new-chat-model]');
        const effortSelect = root.querySelector('[data-new-chat-effort]');
        const desiredModel = modelSelect ? modelSelect.value : 'gpt-6-sol';
        const desiredEffort = effortSelect ? effortSelect.value : 'medium';
        if (configuredSessionId !== pendingSessionId || configuredModel !== desiredModel || configuredEffort !== desiredEffort) {
          await request('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/model', {model:desiredModel});
          await request('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/effort', {effort:desiredEffort});
          configuredSessionId = pendingSessionId;
          configuredModel = desiredModel;
          configuredEffort = desiredEffort;
        }
        await request('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/message', {
          content, ...(taskId ? {skill:selectedSkill()} : {}), sources:submittedSources,
        });
        finishDirectSources(submittedSources, pendingSessionId);
        clearActiveDraft();
        navigateChat('/research-chat/?session=' + encodeURIComponent(pendingSessionId));
      } catch (err) {
        // A timed-out POST may still have been accepted; inspect persisted history before inviting a duplicate send.
        if (pendingSessionId) {
          try {
            const response = await fetch('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/state', {credentials:'same-origin',cache:'no-store'});
            const state = await response.json();
            if (response.ok && Array.isArray(state.messages) && state.messages.some(message => message.role === 'user' && message.content === content)) {
              finishDirectSources(submittedSources, pendingSessionId);
              clearActiveDraft();
              navigateChat('/research-chat/?session=' + encodeURIComponent(pendingSessionId));
              return;
            }
          } catch (_readErr) {}
        }
        if (error) error.textContent = String(err.message || err) + (pendingSessionId ? ' 已保留会话；可再次发送。' : '');
        root.querySelectorAll('[data-research-mode]').forEach(control => { control.disabled = Boolean(pendingSessionId); });
        root.querySelectorAll('[data-new-chat-model],[data-new-chat-effort]').forEach(control => { control.disabled = false; });
        directSend.dataset.busy = '0';
        directSend.textContent = '发送 ↗';
        directInput.disabled = false;
        if (taskSelect) taskSelect.disabled = Boolean(pendingSessionId);
        refreshDirectInput();
        directInput.focus();
      }
    });
  }
  root.querySelectorAll('[data-research-model]').forEach(select => select.addEventListener('change', async () => {
    try { await request('/api/research-chat/' + encodeURIComponent(root.dataset.session || '') + '/model', {model:select.value}); }
    catch (err) { if (error) error.textContent = String(err.message || err); }
  }));
  root.querySelectorAll('[data-research-mode]').forEach(button => button.addEventListener('click', async () => {
    const mode = button.dataset.researchMode;
    if (mode === 'project' || mode === 'frontier') {
      const prompt = root.querySelector('[data-research-prompt]');
      if (!prompt) return;
      if (!taskSelect?.value) {
        if (error) error.textContent = '先选择一个已有项目，再使用项目科研工具。';
        taskSelect?.focus(); return;
      }
      const skill = button.dataset.researchSkill || defaultSkill(mode);
      persistDraftAt(activeDraftKey);
      root.dataset.nextChatMode = mode;
      root.dataset.nextChatSkill = skill;
      try { localStorage.setItem(modeKey, mode); localStorage.setItem(skillKey, skill); } catch (_err) {}
      const restored = loadDraftForCurrentContext(starters[skill].prompt);
      if (!restored) persistDraftAt(activeDraftKey);
      refreshSelectedCard();
      prompt.dispatchEvent(new Event('input', {bubbles:true}));
      prompt.focus();
      if (error) error.textContent = '';
      return;
    }
    const taskId = taskSelect ? taskSelect.value : '';
    if (mode !== 'migration' && !taskId) {
      if (error) error.textContent = '请先选择要讨论的 Sciver 项目任务。';
      if (taskSelect) taskSelect.focus();
      return;
    }
    button.disabled = true;
    try {
      const data = await request('/api/research-chat/session', {mode,task_id:mode === 'migration' ? undefined : taskId || undefined});
      navigateChat('/research-chat/?session=' + encodeURIComponent(data.session_id));
    } catch (err) {
      button.disabled = false;
      if (error) error.textContent = String(err.message || err);
    }
  }));
  root.querySelectorAll('[data-research-action]').forEach(button => button.addEventListener('click', async () => {
    const tip = root.querySelector('[data-research-tip]');
    if (button.dataset.decision === 'confirm' && tip && !tip.value) {
      if (error) error.textContent = '请选择一条当前 active 的科学 tip。';
      tip.focus(); return;
    }
    const plan = root.querySelector('[data-research-plan]');
    if (button.dataset.decision === 'confirm' && plan && plan.value !== plan.defaultValue) {
      if (error) error.textContent = '草稿有未保存的修改，请先点「更新预览」，检查后再次确认。';
      return;
    }
    button.disabled = true;
    try {
      const result = await request('/api/research-chat/' + encodeURIComponent(root.dataset.session || '') + '/action',
        {action_id:button.dataset.researchAction,decision:button.dataset.decision,
         tip_id:tip && tip.value || undefined,
         details:plan && button.dataset.decision !== 'reject' ? plan.value : undefined});
      if(result.redirect)location.assign(result.redirect);else location.reload();
    } catch (err) {
      button.disabled = false;
      if (error) error.textContent = String(err.message || err);
    }
  }));
})();
</script>"""


@lru_cache(maxsize=1)
def _asset_version() -> str:
    bundle = Path(__file__).resolve().parent / "research_chat_dist" / "research-chat.js"
    return hashlib.sha256(bundle.read_bytes()).hexdigest()[:12]


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _action(value: Any) -> str:
    if not isinstance(value, dict) or not value.get("id"):
        return ""
    kind = str(value.get("kind") or "")
    label = {"migration": "迁移初始化预览", "direction": "方向干预预览", "preference": "研究偏好预览"}.get(kind, "待确认操作")
    details = value.get("details")
    if isinstance(details, (dict, list)):
        details = json.dumps(details, ensure_ascii=False, indent=2)
    detail_html = (f'<textarea data-research-plan rows="12" aria-label="Research Plan 草稿">{_e(details)}</textarea>'
                   if kind in {"migration", "setup"} and details else f'<pre>{_e(details)}</pre>' if details else "")
    choices = value.get("choices") if kind in {"preference", "direction"} else None
    choice_html, confirm_disabled = "", ""
    if isinstance(choices, list) and choices:
        options = '<option value="">选择 active 科学 tip</option>' + "".join(
            f'<option value="{_e(item.get("id"))}">{_e(item.get("label") or item.get("id"))}</option>'
            for item in choices if isinstance(item, dict) and item.get("id"))
        explanation = ("将选中的 tip 标为 Preferred，并关联 human External ResearchFocus。"
                       if kind == "preference" else "将人类方向干预关联到选中的 active 科学 tip。")
        choice_html = f'<p>{explanation}</p><select data-research-tip aria-label="选择 active 科学 tip">{options}</select>'
    elif kind in {"preference", "direction"}:
        choice_html = '<p>当前没有可选的 active 科学 tip，请先在 Canvas 中形成可关联的科学 tip。</p>'
        confirm_disabled = " disabled"
    action_id = _e(value["id"])
    revise = (f'<button type="button" data-research-action="{action_id}" data-decision="revise">更新预览</button>'
              if kind in {"migration", "setup"} else "")
    return (
        '<section class="tm-research-action" aria-label="待确认操作">'
        f'<h3>{_e(value.get("title") or label)}</h3><p>{_e(value.get("summary") or "请检查预览后选择是否执行。")}</p>'
        f'{detail_html}{choice_html}{revise}'
        f'<button type="button" data-research-action="{action_id}" data-decision="confirm"{confirm_disabled}>{"确认建立项目" if kind == "setup" else "确认执行"}</button>'
        f'<button type="button" data-research-action="{action_id}" data-decision="reject">{"保留对话，暂不创建" if kind == "setup" else "暂不执行"}</button>'
        '</section>'
    )


def page_body(snapshot: dict[str, Any], *, mode: str = "research", embedded: bool = False, task_id: str = "") -> str:
    from .research_chat_literature_ui import literature_panel
    from .research_preparation_ui import (PREPARATION_SCRIPT, PREPARATION_STYLE, entry_panel, session_panel)
    is_supervisor = mode == "supervisor"
    tasks = snapshot.get("tasks") or []
    sessions = snapshot.get("sessions") or []
    session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else None
    sid = str(session.get("id") or "") if session else ""
    valid_tasks = {str(task.get("id")): str(task.get("label") or task.get("id")) for task in tasks if isinstance(task, dict)}
    task_id = str(session.get("task_id") or "") if session else (task_id if task_id in valid_tasks else "")
    model = str(session.get("model") or "gpt-6-sol") if session else "gpt-6-sol"
    models = tuple(snapshot.get("available_models") or ("gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"))
    model_options = "".join(f'<option value="{_e(item)}"{" selected" if item == model else ""}>{_e(item)}</option>' for item in models)
    task_options = '<option value="">新想法 · 不关联已有项目</option>' + "".join(
        f'<option value="{_e(task.get("id"))}">{_e(task.get("label") or task.get("id"))}</option>'
        for task in tasks if isinstance(task, dict) and task.get("id"))
    history = "".join(
        f'<a href="{_e(item.get("href") or ("/research-chat/?session=" + str(item.get("id") or "")))}" '
        f'class="{"active" if item.get("id") == sid else ""}"'
        f'{" aria-current=\"page\"" if item.get("id") == sid else ""} '
        f'title="{_e(item.get("title") or "科研对话")}"><span>{_e(item.get("title") or ("Supervisor 对话" if is_supervisor else "科研对话"))}</span>'
        f'<small><time datetime="{_e(str(item.get("updated_at") or ""))}">{_e(str(item.get("updated_at") or "")[:16].replace("T", " "))}</time></small></a>'
        for item in sessions if isinstance(item, dict) and item.get("id"))
    if session:
        if is_supervisor:
            body = (
                f'<div id="tm-research-chat-app" data-session-id="{_e(sid)}" data-mode="supervisor" '
                f'aria-label="Supervisor 接管对话"></div>'
            )
        else:
            created = session.get("completed_action") if session.get("mode") == "setup" else None
            result = created.get("result") if isinstance(created, dict) else None
            completed = (f'<div class="tm-research-action" role="status"><h3>项目已建立</h3>'
                         f'<p>研究计划已保存，项目已加入 Task List。可以稍后再决定何时开始研究。</p>'
                         f'<a href="/run/">打开 Task List</a></div>' if isinstance(result, dict) and result.get("task_id") else "")
            body = (
                completed +
                (session_panel(session) if session.get("mode") == "preparation" else "")
                + literature_panel(session)
                + _action(session.get("pending_action"))
                + f'<div id="tm-research-chat-app" data-session-id="{_e(sid)}" data-mode="research" '
                'aria-label="Sciver 科研对话"></div>'
            )
    elif is_supervisor:
        body = (
            '<div class="tm-research-entry"><div class="tm-research-empty">'
            '<h3>暂无通知触发的 Supervisor 对话</h3>'
            '<p>从待处理通知打开一次 Supervisor 对话后，它会保存在左侧历史列表中。</p>'
            '</div></div>'
        )
    else:
        cards = (
            '<button type="button" class="tm-research-start" data-research-mode="frontier" data-research-skill="research-progress" aria-pressed="false">'
            '<strong>了解研究进展</strong><span class="tm-research-start-description">快速了解做了什么、有哪些结果、目前卡在哪里。</span></button>'
            '<button type="button" class="tm-research-start" data-research-mode="project" data-research-skill="research-discussion" aria-pressed="false">'
            '<strong>讨论问题与想法</strong><span class="tm-research-start-description">解释现象、核查结论，或一起推敲你的新想法。</span></button>'
            '<button type="button" class="tm-research-start" data-research-mode="project" data-research-skill="research-next-steps" aria-pressed="false">'
            '<strong>规划下一步</strong><span class="tm-research-start-description">比较可行方案，确定验证顺序，按需要调整计划和方向。</span></button>'
        )
        models = tuple(snapshot.get("available_models") or ("gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"))
        efforts = tuple(snapshot.get("available_reasoning_efforts") or ("low", "medium", "high", "xhigh"))
        new_model_options = "".join(
            f'<option value="{_e(item)}"{" selected" if item == "gpt-6-sol" else ""}>{_e(item)}</option>'
            for item in models)
        effort_labels = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "XHigh"}
        new_effort_options = "".join(
            f'<option value="{_e(item)}"{" selected" if item == "medium" else ""}>{_e(effort_labels.get(item, item))}</option>'
            for item in efforts)
        body = (
            '<div class="tm-research-entry">'
            '<div class="tm-chat-welcome"><h1>有什么想一起研究的？</h1></div>'
            '<section class="tm-chat-entry-tools" aria-label="选择研究入口">'
            '<div class="tm-chat-entry-switch" aria-label="研究入口分类">'
            f'<button type="button" data-entry-choice="existing" aria-pressed="{str(bool(valid_tasks)).lower()}"' + ('' if valid_tasks else ' disabled') + '>已有项目</button>'
            f'<button type="button" data-entry-choice="new" aria-pressed="{str(not valid_tasks).lower()}">新建项目</button></div>'
            '<div data-entry-panel="existing"' + ('' if valid_tasks else ' hidden') + '>'
            f'<div class="tm-research-starts tm-chat-intent-starts" aria-label="常用科研对话">{cards}</div></div>'
            '<div data-entry-panel="new"' + (' hidden' if valid_tasks else '') + '>' + entry_panel() + '</div></section>'
            '<div class="tm-chat-first-composer">'
            '<div class="tm-chat-project-bar" aria-label="项目上下文"' + ('' if valid_tasks else ' hidden') + '>'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/></svg>'
            f'<label class="tm-research-task-picker"><span class="tm-research-sr-only">项目任务</span>'
            f'<select data-research-task aria-label="选择项目任务">{task_options}</select></label>'
            '</div>'
            '<form class="tm-research-direct" data-research-direct aria-label="直接开始科研聊天">'
            '<textarea id="tm-research-first-message" data-research-prompt maxlength="120000" rows="3" '
            'aria-label="科研问题" aria-describedby="tm-research-prompt-count" '
            'placeholder="随心输入"></textarea>'
            '<div class="tm-research-direct-footer">'
            '<input type="file" data-chat-first-files multiple hidden accept="image/png,image/jpeg,image/webp,image/gif,.pdf,.docx,.md,.markdown,.txt,.csv">'
            '<details class="tm-chat-add-menu" data-chat-popover><summary aria-label="添加附件或更多操作" title="添加附件或更多操作">'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></summary>'
            '<div class="tm-chat-popover-panel"><button type="button" data-chat-first-attach>添加图片或文档</button>'
            '<button type="button" data-chat-first-expand>展开输入框</button>'
            '<button type="button" data-research-clear-draft aria-label="清空草稿" hidden>清空草稿</button></div></details>'
            '<span class="tm-research-prompt-count" id="tm-research-prompt-count" data-research-prompt-count hidden>0/120000</span>'
            '<div class="tm-research-direct-settings">'
            '<details class="tm-chat-model-menu" data-chat-popover><summary aria-label="模型与推理强度">'
            '<span data-new-chat-model-label>GPT-6 Sol</span><span class="tm-chat-effort-value" data-new-chat-effort-label>Medium</span>'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg></summary>'
            '<div class="tm-chat-popover-panel">'
            f'<label>模型<select data-new-chat-model aria-label="新对话模型">{new_model_options}</select></label>'
            f'<label>推理强度<select data-new-chat-effort aria-label="新对话推理强度">{new_effort_options}</select></label>'
            '</div></details>'
            '<button type="submit" class="tm-research-direct-send" data-research-send aria-label="发送" title="发送" disabled>'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg></button></div></div>'
            '</form></div>'
            '</div>'
        )
    streaming = bool(session and session.get("streaming"))
    history_controls = ('' if is_supervisor else
        '<div class="tm-chat-history-controls"><input type="search" data-chat-history-query aria-label="搜索全部对话" placeholder="搜索标题或消息…">'
        '<details class="tm-chat-history-filter" data-chat-popover><summary>筛选 <span>⌄</span></summary>'
        '<div class="tm-chat-popover-panel"><label>项目<select data-chat-history-task aria-label="按项目筛选对话"><option value="">全部项目</option>'
        + ''.join(f'<option value="{_e(item.get("id"))}">{_e(item.get("label"))}</option>' for item in tasks)
        + '</select></label><label>范围<select data-chat-history-view aria-label="对话范围"><option value="active">近期对话</option>'
        '<option value="archived">已归档</option><option value="all">全部对话</option><option value="trash">回收站</option></select></label></div></details></div>')
    export_menu = (f'<details class="tm-chat-export" data-chat-popover><summary aria-label="导出对话" title="导出对话">⋯</summary>'
                   f'<div class="tm-chat-popover-panel"><a href="/api/research-chat/{_e(sid)}/export?format=markdown" download>导出 Markdown</a>'
                   f'<a href="/api/research-chat/{_e(sid)}/export?format=json" download>JSON</a></div></details>'
                   if sid and not is_supervisor else '')
    asset_tags = ('<link rel="stylesheet" href="/research-chat/assets/research-chat.css">'
                  f'<script type="module" src="/research-chat/assets/research-chat.js?v={_asset_version()}"></script>' if session or not is_supervisor else "")
    if session and not is_supervisor:
        title = str(session.get("title") or "Research Chat")
        toolbar_title = (
            f'<h2 data-research-title-label>{_e(title)}</h2>'
            '<button type="button" class="tm-research-title-rename" data-research-rename>重命名</button>'
            f'<form class="tm-research-title-form" data-research-title-form hidden>'
            f'<input data-research-title-input maxlength="80" required aria-label="会话标题" value="{_e(title)}">'
            '<button type="submit">保存</button><button type="button" data-research-title-cancel>取消</button></form>'
        )
    else:
        toolbar_title = f'<h2>{"Supervisor 接管对话" if is_supervisor else "新对话"}</h2>'
    toolbar_control = ('<span class="tm-research-mode-label">Supervisor 对话</span>'
                       if is_supervisor else
                       f'<select class="tm-research-model" data-research-model aria-label="聊天模型"'
                       f'{(" disabled" if not session or streaming else "")}>{model_options}</select>')
    history_action = ('' if is_supervisor else
                      '<a class="tm-research-new" href="/research-chat/?new=1" aria-label="新对话" title="新对话">＋ 新对话</a>')
    if embedded:
        toolbar_title = f'<h2 data-research-title-label>{_e(session.get("title") if session else "新对话")}</h2>'
        toolbar_control = ('<button type="button" class="tm-research-compact-action" data-research-embedded-history '
                           'aria-label="历史对话" aria-controls="tm-research-history-list" aria-expanded="false" title="历史对话">'
                           '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5m4-1v5l3 2"/></svg></button>'
                           '<a class="tm-research-compact-action" href="/research-chat/?new=1" aria-label="新对话" title="新对话">'
                           '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></a>')
    project_control = ""
    if not is_supervisor:
        if session and task_id:
            project_control = (f'<span class="tm-research-project-context" title="{_e(valid_tasks.get(task_id, task_id))}">'
                               f'{_e(valid_tasks.get(task_id, task_id))}</span>')
    if not session and not is_supervisor and not embedded:
        toolbar_control = ""
    return (
        f'{asset_tags}<style>{_STYLE}</style>{PREPARATION_STYLE if not is_supervisor else ""}<style>{CHROME_STYLE}</style><section class="tm-research-chat tm-chat-codex{" supervisor-mode" if is_supervisor else ""}" '
        f'data-chat-shell="codex" data-chat-state="{"active" if session else "new"}" data-project-label="{_e(valid_tasks.get(task_id, ""))}" '
        f'data-embedded="{str(embedded).lower()}" data-task-id="{_e(task_id)}" '
        f'data-session="{_e(sid)}" data-mode="{"supervisor" if is_supervisor else "research"}" '
        f'data-session-mode="{_e(session.get("mode") or "") if session else ""}" '
        f'data-streaming="{str(streaming).lower()}" data-next-chat-mode="project" '
        f'aria-label="{"Supervisor 接管对话" if is_supervisor else "Workspace Research Chat"}">'
        '<header class="tm-research-toolbar">'
        f'{toolbar_title}'
        f'{project_control}{export_menu}{toolbar_control}'
        '</header>'
        '<div class="tm-research-layout">'
        '<aside class="tm-research-history" aria-label="历史对话">'
        f'<div class="tm-research-history-head"><h3>{"Supervisor 历史" if is_supervisor else "历史对话"}</h3>'
        '<button type="button" class="tm-research-history-toggle" data-research-history-toggle '
        'aria-controls="tm-research-history-list" aria-expanded="true" aria-label="收起历史对话" title="收起历史对话">‹</button>'
        f'{history_action}</div>{history_controls}'
        f'<nav class="tm-research-history-list" id="tm-research-history-list" aria-label="历史对话记录">{history or "<small>暂无历史对话</small>"}</nav>'
        f'</aside><main class="tm-research-main">{body}</main></div><p class="tm-research-error" role="alert"></p></section>'
        f'{_SCRIPT}<script src="/research-chat/history.js" defer></script>'
        f'{PREPARATION_SCRIPT if not is_supervisor else ""}'
    )


_EMBED_STYLE = """
html,body{height:100%;margin:0;overflow:hidden;background:#111114;color:#f4f4f5;color-scheme:dark;font:13px/1.5 system-ui,sans-serif}
*{box-sizing:border-box}button,input,textarea,select{font:inherit}button,a{touch-action:manipulation}button{cursor:pointer}
[hidden]{display:none!important}a{color:#bfdbfe}
.tm-chat-embedded{padding:0 12px 10px}
.tm-chat-embedded .tm-research-chat{position:relative}
.tm-chat-embedded .tm-research-toolbar{gap:7px;margin:0;min-height:42px;flex:0 0 42px;border-bottom:1px solid #27272f}
.tm-chat-embedded .tm-research-toolbar h2{flex:0 1 auto;max-width:36%;font-size:12px;font-weight:550;color:#c7c9d1}
.tm-research-compact-action{flex:0 0 28px;display:grid;place-items:center;width:28px;height:28px;border:0;border-radius:7px;background:transparent;color:#aab2c1;text-decoration:none}
.tm-research-compact-action:hover{background:#292c36;color:#fff}
.tm-research-compact-action svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.tm-chat-embedded .tm-research-layout,.tm-chat-embedded .tm-research-chat.history-collapsed .tm-research-layout{display:flex;flex:1;min-height:0;overflow:hidden}
.tm-chat-embedded .tm-research-main{width:100%;flex:1;min-width:0}
.tm-chat-embedded .tm-research-history{position:absolute;inset:42px 0 0 auto;z-index:5;width:min(280px,100%);height:auto;max-height:none;padding:12px;border:1px solid #41414a;border-radius:10px;background:#1b1b22;box-shadow:-14px 0 35px #0007}
.tm-chat-embedded .history-collapsed .tm-research-history{display:none}
.tm-chat-embedded .tm-research-history-list{flex-direction:column;max-height:none;overflow:auto}
.tm-chat-embedded .tm-research-history-list a{flex:none}
.tm-chat-embedded .tm-research-context{flex:0 0 auto;padding:8px 2px 0;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#939dad}
.tm-chat-embedded .tm-research-entry{gap:12px;padding:20px 0 8px;margin:0;flex:1;max-height:none;overflow:auto}
.tm-chat-embedded .tm-research-empty{margin:0;text-align:left}
.tm-chat-embedded .tm-research-empty h3{font-size:18px;margin:0 0 5px}
.tm-chat-embedded .tm-research-empty p{font-size:11px;line-height:1.5}
.tm-chat-embedded .tm-research-task-picker{flex:1;max-width:none;margin:0}
.tm-chat-embedded .tm-research-task-picker select{min-height:29px;border-color:transparent;background:transparent;padding:4px 0;font-size:11px}
.tm-chat-embedded .tm-research-project-context{margin-left:auto;font-size:10px}
.tm-chat-embedded .tm-research-migrate{display:none}
.tm-chat-embedded .tm-research-starts{gap:9px;flex:0 0 auto}
.tm-chat-embedded .tm-research-start.primary{min-height:156px;border-radius:13px;padding:15px 13px}
.tm-chat-embedded .tm-research-start strong{font-size:15px;line-height:1.5;margin-bottom:7px;white-space:normal}
.tm-chat-embedded .tm-research-start-description{display:block;font-size:12px;line-height:1.65}
.tm-chat-embedded .tm-research-start-icon{width:20px;height:20px;margin-bottom:13px}
.tm-chat-embedded .tm-research-secondary-starts{grid-template-columns:1fr;gap:7px}
.tm-chat-embedded .tm-research-secondary-starts .tm-research-start{min-height:0;padding:10px 12px;border-radius:10px}
.tm-chat-embedded .tm-research-secondary-starts strong{font-size:12px;margin-bottom:3px}
.tm-chat-embedded .tm-research-secondary-starts .tm-research-start-description{font-size:11px}
.tm-chat-embedded .tm-research-direct{margin-top:auto;border-radius:13px;padding:10px;gap:6px}
.tm-chat-embedded .tm-research-direct-head label{font-size:12px}.tm-chat-embedded .tm-research-direct-head span{display:none}
.tm-chat-embedded .tm-research-direct textarea{font-size:13px;min-height:76px;max-height:140px}
.tm-chat-embedded .tm-research-direct-footer{gap:8px;align-items:flex-end;flex-wrap:nowrap}
.tm-chat-embedded .tm-research-direct-settings{gap:5px;flex-wrap:wrap}
.tm-chat-embedded .tm-research-direct-settings label{font-size:0;gap:0}
.tm-chat-embedded .tm-research-direct select{max-width:148px;min-height:28px;font-size:10px;padding:4px}
.tm-chat-embedded .tm-research-prompt-count{font-size:9px}.tm-chat-embedded .tm-research-direct-clear{width:24px;height:24px}
.tm-chat-embedded .tm-research-direct-send{min-width:48px;min-height:30px;padding:5px 8px;font-size:11px}
.tm-chat-embedded #tm-research-chat-app{min-height:0}
.tm-chat-embedded #tm-research-chat-app .tm-aui-shell,.tm-chat-embedded #tm-research-chat-app .tm-aui-thread{min-height:0;height:100%}
.tm-chat-embedded #tm-research-chat-app .tm-aui-viewport{min-height:0}
.tm-chat-embedded .tm-aui-message-list{gap:16px;padding:14px 0 20px}
.tm-chat-embedded .tm-aui-assistant-content,.tm-chat-embedded .tm-aui-user-bubble{font-size:13px;line-height:1.7}
.tm-chat-embedded .tm-aui-user-bubble{max-width:92%;padding:9px 11px;border-radius:13px}
.tm-chat-embedded #tm-research-chat-app .tm-aui-composer-wrap{padding:6px 0 0}
.tm-chat-embedded .tm-aui-composer{padding:9px 10px;border-radius:13px;gap:7px}
.tm-chat-embedded .tm-aui-input{font-size:13px;min-height:40px;max-height:120px}
.tm-chat-embedded .tm-aui-model-picker select{max-width:137px;font-size:10px;padding:7px 23px 7px 7px}
.tm-chat-embedded .tm-aui-select-wrap select{min-width:72px;font-size:10px;padding:7px 23px 7px 7px}
.tm-chat-embedded .tm-aui-composer-toolbar{gap:5px}.tm-chat-embedded .tm-aui-composer-settings{gap:5px}
.tm-chat-embedded .tm-aui-effort-picker>span:first-child{display:none}
.tm-chat-embedded .tm-aui-composer-actions{gap:4px}.tm-chat-embedded .tm-aui-send,.tm-chat-embedded .tm-aui-stop{width:29px;height:29px}
.tm-chat-embedded .tm-aui-composer-note{display:none}
.tm-chat-embedded .tm-research-action{flex:0 0 auto;max-height:45%;overflow:auto;margin:8px 0}
.tm-chat-embedded .tm-research-error{flex:0 0 auto;max-height:70px;overflow:auto;margin:6px 0 0}
.tm-chat-embedded :focus-visible{outline:2px solid #93c5fd;outline-offset:2px}
"""


def embedded_page(snapshot: dict[str, Any], *, task_id: str = "") -> str:
    """Render the shared chat without the dashboard or a recursive launcher."""
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Sciver · Research Chat</title>' + THEME_BOOTSTRAP_SCRIPT + '</head><body class="tm-chat-embedded">'
            + page_body(snapshot, embedded=True, task_id=task_id)
            + f'<style>{_EMBED_STYLE}</style>' + LIGHT_STYLE + '<script src="/research-chat/widget.js" defer></script></body></html>')
