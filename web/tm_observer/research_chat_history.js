(() => {
  const root = document.querySelector('.tm-research-chat');
  if (!root || root.dataset.mode === 'supervisor') return;
  const list = root.querySelector('#tm-research-history-list');
  const query = root.querySelector('[data-chat-history-query]');
  const task = root.querySelector('[data-chat-history-task]');
  const view = root.querySelector('[data-chat-history-view]');
  const error = root.querySelector('.tm-research-error');
  const popovers = () => root.querySelectorAll('details[data-chat-popover],details.tm-chat-history-menu');
  const closePopovers = except => popovers().forEach(menu => { if (menu !== except) menu.open = false; });
  root.addEventListener('toggle', event => {
    const menu = event.target;
    if (!menu.matches?.('details[data-chat-popover],details.tm-chat-history-menu') || !menu.open) return;
    closePopovers(menu);
    if (menu.matches('.tm-chat-history-menu')) {
      const trigger = menu.querySelector('summary').getBoundingClientRect();
      const panel = menu.querySelector('.tm-chat-popover-panel');
      if (panel) {
        panel.style.position = 'fixed';
        panel.style.left = Math.max(8, Math.min(trigger.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 8)) + 'px';
        panel.style.top = Math.max(8, Math.min(trigger.bottom + 5, innerHeight - panel.offsetHeight - 8)) + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
      }
    }
  }, true);
  document.addEventListener('click', event => {
    const menu = event.target.closest?.('details[data-chat-popover],details.tm-chat-history-menu');
    if (!menu || !root.contains(menu)) closePopovers();
    else if (event.target.closest('button,a') && !event.target.closest('summary')) menu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const opened = Array.from(popovers()).find(menu => menu.open);
    if (opened) { event.preventDefault();closePopovers();opened.querySelector('summary')?.focus(); }
  });
  list?.addEventListener('scroll', () => closePopovers(), {passive:true});
  const model = root.querySelector('[data-new-chat-model]');
  const effort = root.querySelector('[data-new-chat-effort]');
  const updateModelLabel = () => {
    const label = root.querySelector('[data-new-chat-model-label]');
    const strength = root.querySelector('[data-new-chat-effort-label]');
    if (label && model) label.textContent = model.value.replace(/^gpt-/, 'GPT-').replace(/-(sol|luna)$/, ' $1').replace(/\b(sol|luna)$/, text => text[0].toUpperCase() + text.slice(1));
    if (strength && effort) strength.textContent = effort.selectedOptions[0]?.textContent || effort.value;
  };
  updateModelLabel();
  model?.addEventListener('change', updateModelLabel);
  effort?.addEventListener('change', updateModelLabel);
  for (const control of [model, effort]) {
    if (control) new MutationObserver(updateModelLabel).observe(control, {
      childList:true, subtree:true, attributes:true, attributeFilter:['selected','value','label'],
    });
  }
  let history = [], timer, revision = 0;
  const sid = root.dataset.session;
  const href = (id, message) => {
    const url = new URL('/research-chat/', location.origin);
    url.searchParams.set('session', id);
    if (root.dataset.embedded === 'true') url.searchParams.set('embed', '1');
    if (message) url.hash = 'message-' + message;
    return url.pathname + url.search + url.hash;
  };
  const post = async (url, body) => {
    const response = await fetch(url, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '操作失败');
    return data;
  };
  const localTime = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value || '' : date.toLocaleString();
  };
  root.querySelectorAll('time[datetime]').forEach(element => element.textContent = localTime(element.dateTime));
  function render() {
    if (!list) return;
    list.replaceChildren();
    for (const row of history) {
      const wrap = document.createElement('div'); wrap.className = 'tm-chat-history-row';
      const link = document.createElement('a'); link.href = href(row.id, row.match_message_id);
      link.title = row.title || '科研对话'; link.className = row.id === sid ? 'active' : '';
      const title = document.createElement('span'); title.textContent = (row.pinned ? '· ' : '') + (row.title || '科研对话');
      const stamp = document.createElement('small'); stamp.textContent = localTime(row.updated_at);stamp.className = 'tm-chat-history-time';
      link.append(title, stamp);
      if (row.snippet) { const snippet = document.createElement('small'); snippet.textContent = row.snippet;link.append(snippet); }
      wrap.append(link);
      const menu = document.createElement('details'); menu.className = 'tm-chat-history-menu';
      const summary = document.createElement('summary'); summary.textContent = '⋯';summary.setAttribute('aria-label', '管理会话：' + (row.title || '科研对话'));menu.append(summary);
      const panel = document.createElement('div');panel.className = 'tm-chat-popover-panel';menu.append(panel);
      const actions = row.deleted ? [['restore','恢复']] : [['pin', row.pinned ? '取消置顶' : '置顶'], [row.archived ? 'restore' : 'archive',row.archived ? '恢复' : '归档'],['delete','删除']];
      for (const [action,label] of actions) {
        const button = document.createElement('button');button.type='button';button.textContent=label;
        button.onclick=async () => {
          if (action === 'delete' && !confirm('将此会话移到回收站？可以在回收站恢复。')) return;
          try {
            await post('/api/research-chat/' + row.id + '/manage',{action});
            if(row.id === sid && (action === 'delete' || action === 'archive')) { location.assign('/research-chat/?new=1' + (root.dataset.embedded === 'true' ? '&embed=1' : '')); return; }
            await refresh();
          }
          catch (reason) { if(error) error.textContent=String(reason.message || reason); }
        }; panel.append(button);
      }
      wrap.append(menu);list.append(wrap);
    }
    if (!history.length) {const empty=document.createElement('small');empty.textContent='没有匹配的对话';list.append(empty);}
  }
  async function refresh() {
    const current = ++revision;
    try {
      const params = new URLSearchParams({q:query?.value || '',task_id:task?.value || '',view:view?.value || 'active'});
      const response=await fetch('/api/research-chat/sessions?' + params,{credentials:'same-origin',cache:'no-store'});
      const data=await response.json();if(!response.ok)throw new Error(data.error || '读取历史失败');
      if(current!==revision)return;history=data.sessions || [];render();
    } catch (reason) {if(error) error.textContent=String(reason.message || reason);}
  }
  query?.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(refresh,220);});
  task?.addEventListener('change',refresh);view?.addEventListener('change',refresh);
  window.addEventListener('sciver-chat-history-changed',refresh);
  void refresh();

  // Adding the first attachment creates only a blank chat, never a model turn.
  const picker=root.querySelector('[data-chat-first-files]');
  const choose=root.querySelector('[data-chat-first-attach]');
  choose?.addEventListener('click',()=>picker?.click());
  let firstSession, firstTask;
  async function attachFirst(files) {
    if(!files.length)return;
    const taskId=root.querySelector('[data-research-task]')?.value || '';
    choose.disabled=true;
    try {
      if(files.length>10)throw new Error('每条消息最多添加 10 个文件。');
      for(const file of files){
        if(!/\.(png|jpe?g|webp|gif|pdf|docx|md|markdown|txt|csv)$/i.test(file.name))throw new Error('支持图片、PDF、DOCX、Markdown、TXT 和 CSV。');
        if(file.size<1 || file.size>20_000_000)throw new Error(file.name+'：文件大小须为 1 字节至 20 MB。');
      }
      if(firstSession && firstTask!==taskId)throw new Error('附件草稿已绑定原项目，请先打开已保存的聊天草稿。');
      if(!firstSession){
        const created=await post('/api/research-chat/session',taskId
          ? {mode:root.dataset.nextChatMode || 'project',task_id:taskId,skill:root.dataset.nextChatSkill}
          : {mode:'preparation',entry:'exploratory'});
        firstSession=created.session_id;firstTask=taskId;
      }
      const session=firstSession;
      root.dispatchEvent(new CustomEvent('sciver-chat-carry-draft',{detail:{session_id:session}}));
      localStorage.setItem('sciver-chat-composer-v2:research:'+session,root.querySelector('[data-research-prompt]')?.value || '');
      const model=root.querySelector('[data-new-chat-model]')?.value;
      const effort=root.querySelector('[data-new-chat-effort]')?.value;
      if(model)await post('/api/research-chat/'+session+'/model',{model});
      if(effort)await post('/api/research-chat/'+session+'/effort',{effort});
      const ids=JSON.parse(localStorage.getItem('sciver-chat-attachments-v1:research:'+session)||'[]');
      for(const file of files){
        if(error)error.textContent='正在添加附件：'+file.name;
        const response=await fetch('/api/research-chat/'+session+'/attachments',{method:'POST',credentials:'same-origin',headers:{'Content-Type':file.type || 'application/octet-stream','X-File-Name':encodeURIComponent(file.name),'X-TM-Operator-Intent':'1'},body:file});
        const data=await response.json();if(!response.ok)throw new Error(data.error || '附件上传失败');ids.push(data.attachment.id);
        localStorage.setItem('sciver-chat-attachments-v1:research:'+session,JSON.stringify(ids));
      }
      location.assign(href(session));
    } catch(reason){
      if(error){error.textContent=String(reason.message || reason);if(firstSession){const link=document.createElement('a');link.href=href(firstSession);link.textContent=' 打开已保存的聊天草稿';error.append(link);}}
      choose.disabled=false;
    }
  }
  picker?.addEventListener('change',()=>void attachFirst(Array.from(picker.files || [])));
  const composer=root.querySelector('[data-research-direct]');
  const expand=root.querySelector('[data-chat-first-expand]');
  expand?.addEventListener('click',()=>{const expanded=composer.classList.toggle('expanded');expand.textContent=expanded?'收起输入框':'展开输入框';const input=composer.querySelector('textarea');input.style.minHeight=expanded?'40vh':'';input.style.maxHeight=expanded?'65vh':'';});
  composer?.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault();});
  composer?.addEventListener('drop',event=>{if(event.dataTransfer?.files.length){event.preventDefault();void attachFirst(Array.from(event.dataTransfer.files));}});
  composer?.addEventListener('paste',event=>{if(event.clipboardData?.files.length){event.preventDefault();void attachFirst(Array.from(event.clipboardData.files));}});
})();
