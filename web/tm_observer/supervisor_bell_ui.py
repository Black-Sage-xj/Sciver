"""Global Supervisor notification bell in the Workspace header."""

BELL_HTML = """
<div class="tm-supervisor-notices" data-supervisor-notices>
  <button type="button" class="tm-notices-toggle" data-notices-toggle
    aria-label="系统消息" aria-controls="tm-supervisor-notice-panel"
    aria-expanded="false" title="系统消息">
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/>
      <path d="M10 21h4"/>
    </svg>
    <span class="tm-notices-count" data-notices-count hidden></span>
  </button>
  <section id="tm-supervisor-notice-panel" class="tm-notices-panel" aria-label="待处理通知"
    data-notices-panel hidden>
    <header class="tm-notices-head"><strong>待处理通知</strong>
      <span data-notices-summary>加载中…</span></header>
    <div class="tm-notices-list" data-notices-list role="list"></div>
    <button type="button" class="tm-notices-more" data-notices-more hidden>查看更多</button>
  </section>
</div>
"""

BELL_STYLE = """
<style>
.tm-topbar { z-index:20; }
.tm-topbar-actions { display:flex; align-items:center; gap:12px; margin-left:12px; }
.tm-supervisor-notices { position:relative; flex:none; }
.tm-notices-toggle { width:35px; height:35px; padding:0; box-sizing:border-box;
  border:1px solid #3f3f46; background:#18181b; display:flex; align-items:center;
  justify-content:center; color:#c4c4cc; border-radius:10px; position:relative;
  line-height:0; cursor:pointer; }
.tm-notices-toggle svg { display:block; width:19px; height:19px; flex:none; pointer-events:none; }
.tm-notices-toggle:hover,.tm-notices-toggle[aria-expanded="true"] { background:#27272a; color:#fff; }
.tm-notices-toggle:focus-visible,.tm-notices-more:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-notices-count { position:absolute; top:-8px; right:-9px; min-width:18px; height:18px;
  padding:0 4px; display:flex; align-items:center; justify-content:center;
  box-sizing:border-box; background:#dc2626; color:#fff; border:2px solid #09090b;
  border-radius:999px; font-size:10px; font-weight:760; font-variant-numeric:tabular-nums; }
.tm-notices-count[hidden],.tm-notices-panel[hidden],.tm-notices-more[hidden] { display:none; }
.tm-notices-panel { position:absolute; z-index:90; top:44px; right:0; width:min(415px,calc(100vw - 28px));
  max-height:min(70dvh,640px); background:#19191c; border:1px solid #41414a; border-radius:12px;
  box-shadow:0 18px 42px #000a; display:flex; flex-direction:column; overflow:hidden; }
.tm-notices-head { padding:13px 15px; border-bottom:1px solid #36363e; display:flex; gap:10px;
  align-items:baseline; justify-content:space-between; color:#f4f4f5; font-size:13px; }
.tm-notices-head span { color:#a1a1aa; font-size:11px; font-weight:450; }
.tm-notices-list { overflow-y:auto; min-height:0; overscroll-behavior:contain; }
.tm-notices-item { display:flex; align-items:center; border-bottom:1px solid #303037; color:#e4e4e7; }
.tm-notices-item:hover,.tm-notices-item:focus-within { background:#282830; }
.tm-notices-link { display:block; flex:1; min-width:0; padding:11px 15px; text-decoration:none; color:inherit; }
.tm-notices-clear { flex:none; margin-right:10px; padding:5px 7px; border:1px solid #52525b;
  border-radius:6px; background:#27272a; color:#d4d4d8; cursor:pointer; font-size:11px; }
.tm-notices-clear:hover { background:#3f3f46; color:#fff; }
.tm-notices-link:focus-visible,.tm-notices-clear:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-notices-item[data-read="true"] .tm-notices-item-top span { color:#a1a1aa; }
.tm-notices-item-top { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:11px; }
.tm-notices-item-top strong { font-weight:650; color:#fafafa; overflow-wrap:anywhere; }
.tm-notices-item-top span { white-space:nowrap; color:#a1a1aa; font-size:10px; }
.tm-notices-item-meta { font-size:10px; color:#9ca3af; margin-top:4px; overflow-wrap:anywhere; }
.tm-notices-item-summary { font-size:11px; line-height:1.5; color:#b4b4be; margin-top:5px;
  display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; overflow-wrap:anywhere; }
.tm-notices-item[data-needs-attention="true"][data-read="false"] { border-left:3px solid #ef4444; }
.tm-notices-item[data-needs-attention="true"][data-read="false"] .tm-notices-item-top span { color:#fca5a5; }
.tm-notices-empty { padding:25px 15px; font-size:12px; color:#a1a1aa; text-align:center; }
.tm-notices-more { border:0; border-top:1px solid #3f3f46; background:#24242b;
  padding:10px; color:#bfdbfe; cursor:pointer; font-size:11px; }
@media(max-width:520px) {
  .tm-topbar-actions { margin-left:8px; }
  .tm-notices-panel { right:-4px; top:41px; }
}
</style>
"""

BELL_SCRIPT = r"""
<script data-supervisor-bell>
(function(){
  "use strict";
  const wrapper=document.querySelector("[data-supervisor-notices]");
  if(!wrapper)return;
  const toggle=wrapper.querySelector("[data-notices-toggle]");
  const panel=wrapper.querySelector("[data-notices-panel]");
  const badge=wrapper.querySelector("[data-notices-count]");
  const summary=wrapper.querySelector("[data-notices-summary]");
  const list=wrapper.querySelector("[data-notices-list]");
  const more=wrapper.querySelector("[data-notices-more]");
  let nextOffset=null, loading=false, markOnLoad=false;
  const storageKey="tm.supervisor.notices.v1";
  let read=new Set(),dismissed=new Set(),activeKeys=[];
  function restore(){
    try{
      const saved=JSON.parse(localStorage.getItem(storageKey)||"{}");
      read=new Set(Array.isArray(saved.read)?saved.read.filter(x=>typeof x==="string"):[]);
      dismissed=new Set(Array.isArray(saved.dismissed)?saved.dismissed.filter(x=>typeof x==="string"):[]);
    }catch(_error){read=new Set();dismissed=new Set();}
  }
  function persist(){
    try{localStorage.setItem(storageKey,JSON.stringify({
      read:[...read].slice(-1000),dismissed:[...dismissed].slice(-1000)
    }));}
    catch(_error){}
  }
  function updateCounts(){
    const visible=activeKeys.filter(key=>!dismissed.has(key));
    const unread=visible.filter(key=>!read.has(key)).length;
    badge.hidden=unread===0;
    badge.textContent=unread>99?"99+":String(unread);
    toggle.setAttribute("aria-label","待处理通知，"+unread+" 项未读");
    summary.textContent=visible.length+" 项通知 · "+unread+" 项未读 · 异常历史在任务控制台";
  }
  function emptyIfNeeded(){
    if(list.querySelector(".tm-notices-item")||nextOffset!==null)return;
    list.replaceChildren();
    const empty=document.createElement("div");
    empty.className="tm-notices-empty";empty.textContent="暂无需要你处理的通知";
    list.appendChild(empty);
  }
  function item(entry){
    const key=String(entry.notice_key||"");
    const row=document.createElement("div");
    row.className="tm-notices-item";
    row.setAttribute("role","listitem");
    row.dataset.noticeKey=key;
    row.dataset.read=String(read.has(key));
    row.dataset.needsAttention=String(entry.needs_attention===true);
    const a=document.createElement("a");
    a.className="tm-notices-link";
    const runMatch=String(entry.url||"").match(/\/run\/([0-9a-f]{16})(?:\/|$)/);
    if(runMatch){
      const params=new URLSearchParams();
      params.set("cycle",String(entry.cycle||""));
      params.set("notification_id",String(entry.session_id||entry.id||entry.code||"current-stop"));
      a.href="/supervisor-notification/"+runMatch[1]+"?"+params.toString();
    }else{a.href="/run/";}
    const top=document.createElement("div");top.className="tm-notices-item-top";
    const name=document.createElement("strong");
    name.textContent=(entry.task||"Sciver")+" · Cycle "+(entry.cycle||"");
    const state=document.createElement("span");
    state.textContent=read.has(key)?"已查阅":"未查阅";
    top.append(name,state);
    const meta=document.createElement("div");meta.className="tm-notices-item-meta";
    const timestamp=String(entry.at||"").trim();
    const raisedAt=timestamp?timestamp.replace("T"," ").slice(0,19):"未知";
    meta.textContent=(entry.profile==="runtime"?"运行状态":"Supervisor")+" · "+(entry.code||"")+
      " · 提出时间："+raisedAt;
    const details=document.createElement("div");details.className="tm-notices-item-summary";
    details.textContent=entry.summary||"查看 Supervisor 处理记录";
    a.append(top,meta,details);
    a.addEventListener("click",()=>{read.add(key);persist();updateCounts();});
    const clear=document.createElement("button");
    clear.type="button";clear.className="tm-notices-clear";
    clear.textContent="清除";clear.setAttribute("aria-label","清除通知："+name.textContent);
    clear.addEventListener("click",event=>{
      // Removing a clicked row detaches event.target before the event bubbles
      // to document. Its outside-click handler must not close this panel.
      event.stopPropagation();
      dismissed.add(key);persist();row.remove();updateCounts();emptyIfNeeded();
    });
    row.append(a,clear);return row;
  }
  async function load(append){
    if(loading)return;
    loading=true; more.disabled=true;
    try{
      const offset=append&&nextOffset!==null?nextOffset:0;
      const response=await fetch("/api/supervisor-notifications?offset="+offset+"&limit=40",
        {credentials:"same-origin",cache:"no-store"});
      if(!response.ok)throw new Error("HTTP "+response.status);
      const data=await response.json();
      const markViewed=markOnLoad||!panel.hidden;
      markOnLoad=false;
      activeKeys=Array.isArray(data.active_keys)?data.active_keys.filter(x=>typeof x==="string"):[];
      if(!append)list.replaceChildren();
      for(const entry of data.items||[]){
        if(dismissed.has(entry.notice_key))continue;
        if(markViewed)read.add(entry.notice_key);
        list.appendChild(item(entry));
      }
      nextOffset=data.next_offset;
      more.hidden=nextOffset===null;
      persist();updateCounts();emptyIfNeeded();
    }catch(_error){
      markOnLoad=false;
      if(!append) {
        summary.textContent="暂时无法读取";
        list.replaceChildren();
        const empty=document.createElement("div");
        empty.className="tm-notices-empty";empty.textContent="消息读取失败，可稍后重试";
        list.appendChild(empty);
      }
    }finally{loading=false;more.disabled=false;}
  }
  function close(){panel.hidden=true;toggle.setAttribute("aria-expanded","false");}
  toggle.addEventListener("click",()=>{
    if(!panel.hidden){close();return;}
    panel.hidden=false;toggle.setAttribute("aria-expanded","true");markOnLoad=true;
    for(const row of list.querySelectorAll("[data-notice-key]")){
      read.add(row.dataset.noticeKey);row.dataset.read="true";
      const state=row.querySelector(".tm-notices-item-top span");
      if(state)state.textContent="已查阅";
    }
    persist();updateCounts();load(false);
  });
  more.addEventListener("click",()=>{if(nextOffset!==null)load(true);});
  document.addEventListener("click",event=>{
    // A composed click path keeps the original inside/outside distinction even
    // when a child is removed from the DOM during its click handler.
    const inside=event.composedPath?.().includes(wrapper)??wrapper.contains(event.target);
    if(!inside)close();
  });
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!panel.hidden){close();toggle.focus();}});
  window.addEventListener("storage",event=>{if(event.key===storageKey){restore();load(false);}});
  restore();
  load(false);
  setInterval(()=>{if(panel.hidden&&!document.hidden)load(false);},20000);
})();
</script>
"""
