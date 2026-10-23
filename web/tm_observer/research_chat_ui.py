"""Research Chat page shell; message/thread interaction is powered by assistant-ui."""
from __future__ import annotations

import html
import json
from typing import Any

from .project_setup_ui import setup_panel


_STYLE = """
.tm-research_chat-view .tm-main{height:100vh;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.tm-research_chat-view .tm-topbar{flex:0 0 62px}
.tm-research_chat-view .tm-content{display:flex;flex:1;flex-direction:column;min-height:0;height:auto;max-width:none;overflow:hidden;padding:8px 18px 8px}
.tm-research_chat-view .tm-page-head{display:none}
.tm-research-chat{max-width:none;width:100%;margin:0;min-height:0;height:100%;flex:1;display:flex;flex-direction:column;color:#f4f4f5;--loquix-text-color:#f4f4f5}
.tm-research-toolbar{display:flex;align-items:center;gap:10px;min-height:38px;flex:0 0 auto;margin:0 0 8px}
.tm-research-toolbar h2{font-size:15px;font-weight:650;margin:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
.tm-research-entry{width:min(960px,100%);max-height:100%;min-height:0;overflow:auto;margin:auto;display:flex;flex-direction:column;gap:18px;padding:20px 0}
.tm-research-task-picker{display:flex;flex-direction:column;gap:8px;font-size:12px;color:#a1a1aa}
.tm-research-task-picker select{min-height:42px;border:1px solid #41414a;border-radius:9px;background:#18181c;color:#e4e4e7;padding:8px 10px}
.tm-research-starts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.tm-research-start{border:1px solid #41414a;border-radius:14px;background:#1c1c21;color:#f4f4f5;text-align:left;padding:17px;cursor:pointer;min-height:125px}
.tm-research-start:hover{border-color:#777780;background:#24242a}
.tm-research-start.primary{border-color:#64748b;background:#242a34}
.tm-research-start strong{display:block;font-size:14px;margin-bottom:9px}
.tm-research-start span{display:block;color:#b0b0bb;font-size:12px;line-height:1.55}
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
.tm-research-direct-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;border-top:1px solid #303038;padding-top:9px}
.tm-research-direct-settings{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.tm-research-direct-settings label{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:#a8a8b4}
.tm-research-direct select{max-width:190px;min-height:30px;border:1px solid #41414a;border-radius:8px;background:#24242b;color:#e4e4e7;padding:4px 7px;font:inherit;font-size:11px}
.tm-research-prompt-count{font-size:10px;color:#888895;font-variant-numeric:tabular-nums}
.tm-research-direct-send{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:82px;min-height:35px;border:0;border-radius:10px;background:#7553d7;color:#fff;padding:7px 13px;font:inherit;font-size:12px;font-weight:650;cursor:pointer}
.tm-research-direct-send:hover{background:#8869e7}
.tm-research-direct-send:disabled{opacity:.38;cursor:not-allowed}
.tm-research-direct-hint{margin:0;padding:0 3px}
.tm-research-direct-clear{border:1px solid #41414a;border-radius:8px;background:#222228;color:#b4b4c0;padding:5px 7px;font:inherit;font-size:10px;cursor:pointer}
.tm-research-direct-clear:hover{border-color:#777780;color:#fff}
.tm-research-entry .tm-research-error{margin:0}
.tm-research-migrate{font-size:12px;color:#a1a1aa}
.tm-research-migrate summary{cursor:pointer;width:max-content}
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
@media(max-width:760px){.tm-research_chat-view .tm-content{padding:6px 10px 8px}.tm-research-layout{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr) auto;gap:8px}.tm-research-main{grid-row:1;min-height:0}.tm-research-history{grid-row:2;position:relative;height:auto;min-height:0;border-left:0;border-top:1px solid #303038;padding:8px 0 0;max-height:112px}.tm-research-history-head{gap:8px}.tm-research-history-list{flex-direction:row;max-height:68px;overflow-x:auto;overflow-y:hidden}.tm-research-history-list a{flex:0 0 170px}.tm-research-toolbar{gap:7px}.tm-research-model{max-width:135px}.tm-research-starts{grid-template-columns:1fr}.tm-research-start{min-height:0}.tm-research-entry{padding:14px 0;gap:12px}.tm-research-direct{padding:11px;border-radius:14px}.tm-research-direct textarea{min-height:68px}.tm-research-direct-settings{gap:6px}.tm-research-direct select{max-width:135px}.tm-research-direct-send{min-width:70px}.tm-research-title-form{flex-wrap:wrap}.tm-research-title-form input{width:130px}.tm-research-toolbar h2{font-size:14px}.tm-research-chat.history-collapsed .tm-research-layout{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr) 36px;gap:6px}.tm-research-chat.history-collapsed .tm-research-history{display:flex;flex-direction:row;justify-content:flex-end;max-height:36px;padding:0;border:0}.tm-research-chat.history-collapsed .tm-research-history-head{flex-direction:row;gap:8px}.tm-research-chat.history-collapsed .tm-research-new{width:auto;min-height:32px;height:32px;padding:0 10px;font-size:12px}.tm-research-chat.history-collapsed .tm-research-new::before{content:none}}
"""

_SCRIPT = """<script>
(() => {
  const root = document.querySelector('.tm-research-chat');
  if (!root) return;
  const error = root.querySelector('.tm-research-error');
  const historyToggle = root.querySelector('[data-research-history-toggle]');
  if (historyToggle) {
    const historyKey = 'tm-research-chat-history-collapsed-v1';
    const setHistoryCollapsed = (collapsed) => {
      root.classList.toggle('history-collapsed', collapsed);
      historyToggle.setAttribute('aria-expanded', String(!collapsed));
      const label = collapsed ? '展开历史对话' : '收起历史对话';
      historyToggle.setAttribute('aria-label', label);
      historyToggle.title = label;
      historyToggle.textContent = collapsed ? '‹' : '›';
    };
    let collapsed = false;
    try { collapsed = localStorage.getItem(historyKey) === '1'; } catch (_err) {}
    setHistoryCollapsed(collapsed);
    historyToggle.addEventListener('click', () => {
      const next = !root.classList.contains('history-collapsed');
      setHistoryCollapsed(next);
      try { localStorage.setItem(historyKey, next ? '1' : '0'); } catch (_err) {}
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
  const taskKey = 'tm-research-chat-selected-task-v1';
  const modeKey = 'tm-research-chat-selected-mode-v1';
  const newModelKey = 'tm-research-chat-new-model-v1';
  const newEffortKey = 'tm-research-chat-new-effort-v1';
  const starters = {
    project: '请结合当前任务的 Research Plan、Canvas、真实 Cycle 轨迹和证据记录，讨论当前研究问题。请区分已证实结论、假设、反例与待验证问题。',
    frontier: '请结合当前任务最近的真实运行轨迹，分析研究进展与科学性，核对实验结果、资格判定、误差边界和未解决问题；明确区分已完成、失败和中断的 Cycle，不要把历史状态当作当前状态。'
  };
  try {
    const savedMode = localStorage.getItem(modeKey);
    if (savedMode === 'project' || savedMode === 'frontier') root.dataset.nextChatMode = savedMode;
  } catch (_err) {}
  if (taskSelect) {
    try {
      const savedTask = localStorage.getItem(taskKey);
      if (savedTask && Array.from(taskSelect.options).some(option => option.value === savedTask)) taskSelect.value = savedTask;
    } catch (_err) {}
    if (!taskSelect.value && taskSelect.options.length > 1) taskSelect.selectedIndex = 1;
    const rememberTask = () => { try { localStorage.setItem(taskKey, taskSelect.value); } catch (_err) {} };
    rememberTask();
    taskSelect.addEventListener('change', rememberTask);
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
    if (newModelSelect && savedModel && Array.from(newModelSelect.options).some(option => option.value === savedModel)) newModelSelect.value = savedModel;
    const savedEffort = localStorage.getItem(newEffortKey);
    if (newEffortSelect && savedEffort && Array.from(newEffortSelect.options).some(option => option.value === savedEffort)) newEffortSelect.value = savedEffort;
  } catch (_err) {}
  let pendingSessionId = '';
  let configuredSessionId = '';
  let configuredModel = '';
  let configuredEffort = '';
  let pendingTaskId = '';
  let activeDraftKey = '';
  const draftKeyForCurrentContext = () => 'tm-research-chat-draft-v1:' + encodeURIComponent(JSON.stringify([
    taskSelect ? taskSelect.value : '', root.dataset.nextChatMode || 'project',
    newModelSelect ? newModelSelect.value : 'gpt-6-sol',
    newEffortSelect ? newEffortSelect.value : 'medium',
  ]));
  const persistDraftAt = key => {
    if (!key || !directInput) return;
    try {
      if (directInput.value) localStorage.setItem(key, directInput.value.slice(0, 4000));
      else localStorage.removeItem(key);
    } catch (_err) {}
  };
  const clearActiveDraft = () => {
    try { if (activeDraftKey) localStorage.removeItem(activeDraftKey); } catch (_err) {}
  };
  const refreshDirectInput = () => {
    if (directCounter && directInput) directCounter.textContent = directInput.value.length + '/4000';
    if (directSend && directInput) directSend.disabled = !directInput.value.trim() || directInput.value.length > 4000 || directSend.dataset.busy === '1';
  };
  const loadDraftForCurrentContext = fallback => {
    activeDraftKey = draftKeyForCurrentContext();
    let saved = '';
    try { saved = localStorage.getItem(activeDraftKey) || ''; } catch (_err) {}
    if (directInput) directInput.value = saved || fallback || '';
    refreshDirectInput();
    return Boolean(saved);
  };
  if (directInput) {
    loadDraftForCurrentContext('');
    directInput.addEventListener('input', () => { refreshDirectInput(); persistDraftAt(activeDraftKey); });
    directInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (directForm && directInput.value.trim()) directForm.requestSubmit();
      }
    });
    if (clearDraftButton) clearDraftButton.addEventListener('click', () => {
      clearActiveDraft();
      directInput.value = '';
      refreshDirectInput();
      if (error) error.textContent = '当前草稿已清空。';
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
      const taskId = taskSelect ? taskSelect.value : '';
      const mode = root.dataset.nextChatMode || 'project';
      if (!content) { directInput.focus(); return; }
      if (content.length > 4000) { if (error) error.textContent = '消息不能超过 4000 字符。'; return; }
      if (!taskId) { if (error) error.textContent = '请先选择要讨论的 Sciver 项目任务。'; if (taskSelect) taskSelect.focus(); return; }
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
          const created = await request('/api/research-chat/session', {mode, task_id:taskId});
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
        await request('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/message', {content});
        clearActiveDraft();
        location.assign('/research-chat/?session=' + encodeURIComponent(pendingSessionId));
      } catch (err) {
        // A timed-out POST may still have been accepted; inspect persisted history before inviting a duplicate send.
        if (pendingSessionId) {
          try {
            const response = await fetch('/api/research-chat/' + encodeURIComponent(pendingSessionId) + '/state', {credentials:'same-origin',cache:'no-store'});
            const state = await response.json();
            if (response.ok && Array.isArray(state.messages) && state.messages.some(message => message.role === 'user' && message.content === content)) {
              clearActiveDraft();
              location.assign('/research-chat/?session=' + encodeURIComponent(pendingSessionId));
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
      const previousMode = root.dataset.nextChatMode || 'project';
      const previousStarter = starters[previousMode];
      if (prompt.value.trim() && prompt.value.trim() !== previousStarter) {
        if (error) error.textContent = '输入框已有自定义内容，已为你保留。清空或恢复原推荐内容后，再切换推荐项。';
        prompt.focus();
        return;
      }
      persistDraftAt(activeDraftKey);
      root.dataset.nextChatMode = mode;
      try { localStorage.setItem(modeKey, mode); } catch (_err) {}
      const restored = loadDraftForCurrentContext(starters[mode]);
      if (!restored) persistDraftAt(activeDraftKey);
      root.querySelectorAll('[data-research-mode="project"],[data-research-mode="frontier"]').forEach(card => card.setAttribute('aria-pressed', String(card === button)));
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
      const data = await request('/api/research-chat/session', {mode,task_id:taskId || undefined});
      location.assign('/research-chat/?session=' + encodeURIComponent(data.session_id));
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


def page_body(snapshot: dict[str, Any], *, mode: str = "research") -> str:
    is_supervisor = mode == "supervisor"
    tasks = snapshot.get("tasks") or []
    sessions = snapshot.get("sessions") or []
    session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else None
    sid = str(session.get("id") or "") if session else ""
    model = str(session.get("model") or "gpt-6-sol") if session else "gpt-6-sol"
    models = tuple(snapshot.get("available_models") or ("gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol"))
    model_options = "".join(f'<option value="{_e(item)}"{" selected" if item == model else ""}>{_e(item)}</option>' for item in models)
    task_options = '<option value="">选择项目任务</option>' + "".join(
        f'<option value="{_e(task.get("id"))}">{_e(task.get("label") or task.get("id"))}</option>'
        for task in tasks if isinstance(task, dict) and task.get("id"))
    history = "".join(
        f'<a href="{_e(item.get("href") or ("/research-chat/?session=" + str(item.get("id") or "")))}" '
        f'class="{"active" if item.get("id") == sid else ""}"'
        f'{" aria-current=\"page\"" if item.get("id") == sid else ""}>'
        f'<span>{_e(item.get("title") or ("Supervisor 对话" if is_supervisor else "科研对话"))}</span>'
        f'<small>{_e(str(item.get("updated_at") or "")[:16].replace("T", " "))}</small></a>'
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
                completed + _action(session.get("pending_action"))
                + f'<div id="tm-research-chat-app" data-session-id="{_e(sid)}" data-mode="research" '
                'aria-label="Sciver 科研对话"></div>'
            )
    elif is_supervisor:
        body = (
            '<div class="tm-research-entry"><div class="tm-research-empty">'
            '<h3>暂无通知触发的 Supervisor 对话</h3>'
            '<p>从待处理通知打开一次 Supervisor 对话后，它会保存在右侧历史列表中。</p>'
            '</div></div>'
        )
    else:
        cards = (
            '<button type="button" class="tm-research-start primary" data-research-mode="project" aria-pressed="false">'
            '<strong>项目科研讨论</strong><span>结合真实 Cycle 轨迹、Research Plan、Canvas 和证据记录，讨论研究问题与下一步探索。</span></button>'
            '<button type="button" class="tm-research-start" data-research-mode="frontier" aria-pressed="false">'
            '<strong>研究进展与科学性</strong><span>核对实验结果、资格判定、误差边界和未解决问题，区分已完成与中断的 Cycle。</span></button>'
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
            + setup_panel(str(snapshot.get("workspace") or "")) +
            '<div class="tm-research-empty"><h3>和 Sciver 讨论科研</h3><p>选择推荐问题，或直接输入自己的问题；新对话会自动保存在右侧历史中。</p></div>'
            f'<label class="tm-research-task-picker">选择 Sciver 项目任务<select data-research-task aria-label="选择项目任务">{task_options}</select></label>'
            '<div class="tm-research-recommend-label">推荐的聊天内容</div>'
            f'<div class="tm-research-starts">{cards}</div>'
            '<form class="tm-research-direct" data-research-direct aria-label="直接开始科研聊天">'
            '<div class="tm-research-direct-head"><label for="tm-research-first-message">直接开始聊天</label>'
            '<span>Enter 发送 · Shift+Enter 换行</span></div>'
            '<textarea id="tm-research-first-message" data-research-prompt maxlength="4000" rows="3" '
            'aria-describedby="tm-research-first-message-hint tm-research-prompt-count" '
            'placeholder="直接输入任何科研问题，不必选择上面的推荐内容…"></textarea>'
            '<div class="tm-research-direct-footer"><div class="tm-research-direct-settings">'
            f'<label>模型<select data-new-chat-model aria-label="新对话模型">{new_model_options}</select></label>'
            f'<label>推理强度<select data-new-chat-effort aria-label="新对话推理强度">{new_effort_options}</select></label>'
            '<span class="tm-research-prompt-count" data-research-prompt-count>0/4000</span>'
            '<button type="button" class="tm-research-direct-clear" data-research-clear-draft>清空草稿</button></div>'
            '<button type="submit" class="tm-research-direct-send" data-research-send disabled>发送 ↗</button></div>'
            '<p class="tm-research-direct-hint" id="tm-research-first-message-hint">发送后自动创建会话。可以先编辑推荐内容，再提交。</p>'
            '</form>'
            '<details class="tm-research-migrate"><summary>旧版目录内初始化（兼容入口）</summary>'
            '<button type="button" data-research-mode="migration">迁移已有工作并初始化任务</button></details>'
            '</div>'
        )
    streaming = bool(session and session.get("streaming"))
    asset_tags = ('<link rel="stylesheet" href="/research-chat/assets/research-chat.css">'
                  '<script type="module" src="/research-chat/assets/research-chat.js"></script>' if session else "")
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
        toolbar_title = f'<h2>{"Supervisor 接管对话" if is_supervisor else "Research Chat"}</h2>'
    toolbar_control = ('<span class="tm-research-mode-label">Supervisor 对话</span>'
                       if is_supervisor else
                       f'<select class="tm-research-model" data-research-model aria-label="聊天模型"'
                       f'{(" disabled" if not session or streaming else "")}>{model_options}</select>')
    history_action = ('' if is_supervisor else
                      '<a class="tm-research-new" href="/research-chat/?new=1" aria-label="新对话" title="新对话">＋ 新对话</a>')
    return (
        f'{asset_tags}<style>{_STYLE}</style><section class="tm-research-chat{" supervisor-mode" if is_supervisor else ""} '
        f'data-session="{_e(sid)}" data-mode="{"supervisor" if is_supervisor else "research"}" '
        f'data-session-mode="{_e(session.get("mode") or "") if session else ""}" '
        f'data-streaming="{str(streaming).lower()}" data-next-chat-mode="project" '
        f'aria-label="{"Supervisor 接管对话" if is_supervisor else "Workspace Research Chat"}">'
        '<header class="tm-research-toolbar">'
        f'{toolbar_title}'
        f'{toolbar_control}'
        '</header>'
        '<div class="tm-research-layout">'
        f'<main class="tm-research-main">{body}</main>'
        '<aside class="tm-research-history" aria-label="历史对话">'
        f'<div class="tm-research-history-head"><h3>{"Supervisor 历史" if is_supervisor else "历史对话"}</h3>'
        '<button type="button" class="tm-research-history-toggle" data-research-history-toggle '
        'aria-controls="tm-research-history-list" aria-expanded="true" aria-label="收起历史对话" title="收起历史对话">›</button>'
        f'{history_action}</div>'
        f'<nav class="tm-research-history-list" id="tm-research-history-list" aria-label="历史对话记录">{history or "<small>暂无历史对话</small>"}</nav>'
        '</aside></div><p class="tm-research-error" role="alert"></p></section>'
        f'{_SCRIPT}'
    )
