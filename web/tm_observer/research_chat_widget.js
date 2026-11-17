/* One same-origin Research Chat runtime, shared by the compact and full pages. */
(() => {
  'use strict';
  const store = (kind, key, value) => {
    try { const storage=window[kind]; if (value === undefined) return storage.getItem(key); storage.setItem(key, value); } catch (_) {}
    return null;
  };
  const embedded = document.body.classList.contains('tm-chat-embedded');
  if (embedded) {
    const root = document.querySelector('.tm-research-chat');
    if (!root || window.parent === window) return;
    const notify = () => window.parent.postMessage({type:'sciver-chat-state',
      session:root.dataset.session || '', title:root.querySelector('[data-research-title-label]')?.textContent || 'Research Chat',
      streaming:Boolean(root.querySelector('.tm-aui-run-activity'))}, location.origin);
    root.addEventListener('click', event => {
      const link=event.target.closest('a[href]');
      if (!link) return;
      const url=new URL(link.href,location.origin);
      if (url.origin===location.origin && url.pathname==='/research-chat/') return;
      if (url.origin===location.origin && url.pathname===location.pathname && url.hash) return;
      link.target='_blank';link.rel='noopener noreferrer';
    });
    let timer;
    new MutationObserver(() => { clearTimeout(timer); timer=setTimeout(notify, 120); }).observe(root, {childList:true,subtree:true});
    notify();
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        window.parent.postMessage({type:'sciver-chat-minimize'}, location.origin);
      }
    });
    // Preserve an unsent active-session draft across navigation as well as minimization.
    const draftKey = 'sciver-chat-composer-v1:' + root.dataset.session;
    let restored = false;
    const restore = () => {
      const input=root.querySelector('.tm-aui-input');
      if (!input || restored || !root.dataset.session) return;
      restored=true;
      const draft=store('sessionStorage',draftKey);
      if (draft && !input.value) {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,draft);
        input.dispatchEvent(new Event('input',{bubbles:true}));
      }
    };
    new MutationObserver(restore).observe(root,{childList:true,subtree:true});
    restore();
    window.addEventListener('pagehide', () => {
      const input=root.querySelector('.tm-aui-input');
      if (input && root.dataset.session) store('sessionStorage',draftKey,input.value);
    });
    return;
  }
  const root=document.getElementById('sciver-chat-widget');
  if (!root) return;
  const $=id=>document.getElementById(id), panel=$('sc-chat-panel'), launcher=$('sc-chat-launcher'),
    frame=$('sc-chat-frame'), loading=$('sc-chat-loading'), retry=$('sc-chat-retry'), startOver=$('sc-chat-start-over'), full=$('sc-chat-full'), max=$('sc-chat-maximize');
  const params=new URLSearchParams(location.search), routeTask=location.pathname.match(/^\/run\/([a-f0-9]{16})\//)?.[1];
  const syncFrameTheme=()=>{
    try {
      frame.contentDocument?.documentElement.setAttribute('data-sciver-theme',
        document.documentElement.getAttribute('data-sciver-theme')||'black');
    } catch (_) {}
  };
  new MutationObserver(syncFrameTheme).observe(document.documentElement,
    {attributes:true,attributeFilter:['data-sciver-theme']});
  const task=params.get('observer_id') || routeTask || root.dataset.taskId || '';
  const context=/^[a-f0-9]{16}$/.test(task)?task:'';
  const contextKey='sciver-chat-session-v1:'+context, openKey='sciver-chat-open-v1', geometryKey='sciver-chat-geometry-v1';
  let currentSession=store('sessionStorage',contextKey)||'', loaded=false, loadTimer, maximized=false, busy=false, unread=false;
  const viewportHeight=()=>window.visualViewport?.height||innerHeight;
  let rect={x:innerWidth-454,y:viewportHeight()-700,w:430,h:610};
  try { const saved=JSON.parse(store('localStorage',geometryKey)); if (saved && ['x','y','w','h'].every(k=>Number.isFinite(saved[k]))) rect=saved; } catch (_) {}
  const mobile=()=>innerWidth<=600;
  const clamp = value => {
    const gap=12, w=Math.max(Math.min(340,innerWidth-gap*2),Math.min(value.w,innerWidth-gap*2)),
      h=Math.max(Math.min(360,viewportHeight()-gap*2),Math.min(value.h,viewportHeight()-gap*2));
    return {x:Math.max(gap,Math.min(value.x,innerWidth-w-gap)),y:Math.max(gap,Math.min(value.y,viewportHeight()-h-gap)),w,h};
  };
  const layout=()=>{
    if (!mobile()&&!maximized) rect=clamp(rect);
    const r=mobile()||maximized?{x:8,y:8,w:innerWidth-16,h:viewportHeight()-16}:rect;
    Object.assign(panel.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});
    panel.dataset.maximized=String(maximized);
  };
  const remember=()=>store('localStorage',geometryKey,JSON.stringify(rect));
  const chatURL=(embed=true)=>{
    const url=new URL('/research-chat/',location.origin);
    if (embed) url.searchParams.set('embed','1');
    if (/^[a-f0-9]{32}$/.test(currentSession)) url.searchParams.set('session',currentSession);
    else url.searchParams.set('new','1');
    if (context) url.searchParams.set('task_id',context);
    return url.pathname+url.search;
  };
  const updateBadge=()=>{
    launcher.dataset.busy=String(busy);
    launcher.querySelector('.sc-chat-dot').hidden=!(busy||unread);
    launcher.setAttribute('aria-label',panel.hidden?(unread?'Sciver 回复已就绪，打开对话':busy?'Sciver 正在回复，打开对话':'打开 Sciver 科研对话'):'收起 Sciver 科研对话');
  };
  const load=()=>{
    loaded=true;loading.hidden=false;retry.hidden=true;startOver.hidden=true;frame.hidden=true;
    loading.querySelector('span').textContent='正在打开科研对话…';
    clearTimeout(loadTimer);
    loadTimer=setTimeout(()=>{ loading.querySelector('span').textContent='暂时无法打开对话，请重新加载。';retry.hidden=false; },15000);
    frame.src=chatURL();full.href=chatURL(false);
  };
  const open=()=>{
    panel.hidden=false;launcher.setAttribute('aria-expanded','true');unread=false;
    store('sessionStorage',openKey,'1');layout();updateBadge();
    if (!loaded) load();
    $('sc-chat-minimize').focus({preventScroll:true});
  };
  const minimize=()=>{
    panel.hidden=true;launcher.setAttribute('aria-expanded','false');store('sessionStorage',openKey,'0');updateBadge();
    launcher.focus({preventScroll:true});
  };
  launcher.addEventListener('click',()=>panel.hidden?open():minimize());
  $('sc-chat-minimize').addEventListener('click',minimize);
  retry.addEventListener('click',load);
  startOver.addEventListener('click',()=>{ currentSession='';store('sessionStorage',contextKey,'');load(); });
  frame.addEventListener('load',()=>{
    syncFrameTheme();
    clearTimeout(loadTimer);
    let valid=false;
    try { valid=Boolean(frame.contentDocument.querySelector('.tm-chat-embedded .tm-research-chat')); } catch (_) {}
    if (valid) { loading.hidden=true;frame.hidden=false; }
    else { loading.hidden=false;loading.querySelector('span').textContent='对话未能加载，请重新打开。';retry.hidden=false;startOver.hidden=!currentSession; }
  });
  max.addEventListener('click',()=>{
    maximized=!maximized;max.setAttribute('aria-pressed',String(maximized));
    max.setAttribute('aria-label',maximized?'还原对话窗口':'展开对话窗口');max.title=maximized?'还原窗口':'展开窗口';layout();
  });
  window.addEventListener('message',event=>{
    if (event.origin!==location.origin || event.source!==frame.contentWindow) return;
    const data=event.data;
    if (data?.type==='sciver-chat-minimize') { minimize();return; }
    if (data?.type!=='sciver-chat-state') return;
    if (data.session!==''&&!/^[a-f0-9]{32}$/.test(data.session)) return;
    currentSession=data.session;store('sessionStorage',contextKey,currentSession);full.href=chatURL(false);
    if (busy && !data.streaming && panel.hidden) unread=true;
    busy=Boolean(data.streaming);updateBadge();
  });
  document.addEventListener('keydown',event=>{
    if (event.key==='Escape'&&!panel.hidden&&!event.defaultPrevented) { event.preventDefault();minimize(); }
  });
  const interact=(event,kind)=>{
    if (mobile()||maximized||event.button!==0) return;
    if (kind==='drag'&&event.target.closest('button,a')) return;
    event.preventDefault();const start={...rect},x=event.clientX,y=event.clientY,handle=event.currentTarget;
    panel.dataset.interacting='true';handle.setPointerCapture(event.pointerId);
    const move=ev=>{
      const dx=ev.clientX-x,dy=ev.clientY-y;
      if (kind==='drag') rect=clamp({...start,x:start.x+dx,y:start.y+dy});
      else {
        const left=kind.includes('w')?Math.max(12,Math.min(start.x+dx,start.x+start.w-340)):start.x;
        const top=kind.includes('n')?Math.max(12,Math.min(start.y+dy,start.y+start.h-360)):start.y;
        rect=clamp({x:left,y:top,w:start.w+start.x-left,h:start.h+start.y-top});
      }
      layout();
    };
    const end=()=>{
      panel.dataset.interacting='false';remember();handle.removeEventListener('pointermove',move);
      handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);handle.removeEventListener('lostpointercapture',end);
    };
    handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);
    handle.addEventListener('pointercancel',end);handle.addEventListener('lostpointercapture',end);
  };
  root.querySelector('[data-sc-chat-drag]').addEventListener('pointerdown',event=>interact(event,'drag'));
  root.querySelectorAll('[data-sc-chat-resize]').forEach(handle=>{
    handle.addEventListener('pointerdown',event=>interact(event,handle.dataset.scChatResize));
    handle.addEventListener('keydown',event=>{
      if (mobile()||maximized||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      event.preventDefault();const step=event.shiftKey?40:16;
      const dw=event.key==='ArrowLeft'?step:event.key==='ArrowRight'?-step:0,
        dh=event.key==='ArrowUp'?step:event.key==='ArrowDown'?-step:0;
      rect=clamp({x:rect.x-dw,y:rect.y-dh,w:rect.w+dw,h:rect.h+dh});layout();remember();
    });
  });
  window.addEventListener('resize',layout);
  window.visualViewport?.addEventListener('resize',layout);
  document.addEventListener('fullscreenchange',()=>{
    const destination=document.fullscreenElement||document.body;
    if (destination!==root.parentElement) {
      if (destination.moveBefore) destination.moveBefore(root,null);
      else {
        let url;try { url=frame.contentWindow?.location.href; } catch (_) {}
        destination.appendChild(root);
        if (loaded) frame.src=url&&url!=='about:blank'?url:chatURL();
      }
    }
    layout();
  });
  layout();updateBadge();
  if (store('sessionStorage',openKey)==='1') open();
})();
