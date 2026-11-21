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
  <section id="tm-supervisor-notice-panel" class="tm-notices-panel" aria-label="任务异常通知"
    data-notices-panel hidden>
    <header class="tm-notices-head"><strong>任务异常</strong>
      <span data-notices-summary>加载中…</span></header>
    <p class="tm-notices-intro">按问题性质查看，处理状态以 Supervisor 验证结果为准。</p>
    <div class="tm-notices-filters" aria-label="按异常类型筛选">
      <button type="button" data-notices-filter="all" aria-pressed="true">全部</button>
      <button type="button" data-notices-filter="system" aria-pressed="false">系统错误 <span data-notices-system-count>0</span></button>
      <button type="button" data-notices-filter="scientific" aria-pressed="false">科研需关注 <span data-notices-scientific-count>0</span></button>
    </div>
    <div class="tm-notices-list" data-notices-list role="list"></div>
    <button type="button" class="tm-notices-more" data-notices-more hidden>查看更多</button>
    <section class="tm-notices-resolved" data-notices-resolved hidden aria-label="已修复或恢复的异常">
      <h3>已修复与最近恢复</h3><div data-notices-resolved-list></div>
    </section>
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
.tm-notices-panel { position:absolute; z-index:90; top:44px; right:0; width:min(520px,calc(100vw - 28px));
  max-height:min(78dvh,740px); background:#19191c; border:1px solid #41414a; border-radius:12px;
  box-shadow:0 18px 42px #000a; display:flex; flex-direction:column; overflow:hidden; }
.tm-notices-head { padding:16px 18px 2px; display:flex; gap:10px;
  align-items:baseline; justify-content:space-between; color:#f4f4f5; font-size:16px; }
.tm-notices-head span { color:#b8b8c2; font-size:11px; font-weight:450; }
.tm-notices-intro { margin:0; padding:0 18px 12px; color:#b8b8c2; font-size:11px; line-height:1.5; }
.tm-notices-filters { display:flex; gap:6px; padding:0 18px 12px; border-bottom:1px solid #36363e; overflow-x:auto; }
.tm-notices-filters button { display:inline-flex; align-items:center; gap:5px; white-space:nowrap; border:1px solid #4b4b55; background:#24242a;
  color:#d4d4dc; border-radius:7px; padding:6px 9px; font:600 11px/1.3 inherit; cursor:pointer; }
.tm-notices-filters button[aria-pressed="true"] { background:#3b394a; border-color:#8b82ad; color:#fff; }
.tm-notices-filters button:focus-visible { outline:2px solid #b3a5f2; outline-offset:2px; }
.tm-notices-filters span { font-variant-numeric:tabular-nums; opacity:.8; }
.tm-notices-list { overflow-y:auto; min-height:0; overscroll-behavior:contain; }
.tm-notices-item { border-bottom:1px solid #303037; color:#e4e4e7; }
.tm-notices-item[hidden] { display:none; }
.tm-notices-item:hover,.tm-notices-item:focus-within { background:#282830; }
.tm-notices-link { display:block; min-width:0; padding:12px 18px 8px; text-decoration:none; color:inherit; }
.tm-notices-clear { margin:0 0 10px 18px; padding:4px 8px; border:1px solid #52525b;
  border-radius:6px; background:transparent; color:#b8b8c2; cursor:pointer; font-size:11px; }
.tm-notices-clear:hover { background:#3f3f46; color:#fff; }
.tm-notices-link:focus-visible,.tm-notices-clear:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-notices-item-top { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:11px; }
.tm-notices-item-top strong { font-weight:650; color:#fafafa; overflow-wrap:anywhere; }
.tm-notices-item-top span { white-space:nowrap; color:#b8b8c2; font-size:10px; }
.tm-notices-item-badges { display:flex; flex-wrap:wrap; gap:6px; margin:7px 0; }
.tm-notices-type,.tm-notices-state { display:inline-flex; align-items:center; padding:3px 7px;
  border:1px solid; border-radius:5px; font-size:11px; line-height:1.25; font-weight:650; }
.tm-notices-type[data-category="system"] { background:#392126; border-color:#a65c67; color:#ffd5dc; }
.tm-notices-type[data-category="scientific"] { background:#3b3020; border-color:#ac864b; color:#ffe3ae; }
.tm-notices-state { background:#282f3c; border-color:#607caa; color:#d8e6ff; }
.tm-notices-state[data-status="waiting"],.tm-notices-state[data-status="stopped"] { background:#383026; border-color:#9e784d; color:#f4d5a7; }
.tm-notices-state[data-status="attention"] { background:#3b3020; border-color:#ac864b; color:#ffe3ae; }
.tm-notices-state[data-status="disabled"] { background:#303035; border-color:#777783; color:#e1e1e7; }
.tm-notices-state[data-status="repaired"],.tm-notices-state[data-status="recovered"] { background:#1d382c; border-color:#4b9873; color:#bdf1d2; }
.tm-notices-item-meta { font-size:10px; color:#b8b8c2; margin-top:4px; overflow-wrap:anywhere; }
.tm-notices-item-summary { font-size:12px; line-height:1.5; color:#e0e0e7; margin-top:5px; overflow-wrap:anywhere; }
.tm-notices-item-dependency { font-size:11px; line-height:1.5; color:#f4d5a7; margin:7px 18px 3px; overflow-wrap:anywhere; }
.tm-notices-technical { margin:1px 18px 8px; color:#b8b8c2; font-size:11px; }
.tm-notices-technical summary { cursor:pointer; width:max-content; max-width:100%; }
.tm-notices-technical p { margin:5px 0; line-height:1.5; overflow-wrap:anywhere; }
.tm-notices-empty { padding:25px 15px; font-size:12px; color:#a1a1aa; text-align:center; }
.tm-notices-more { border:0; border-top:1px solid #3f3f46; background:#24242b;
  padding:10px; color:#bfdbfe; cursor:pointer; font-size:11px; }
.tm-notices-resolved { border-top:1px solid #41414a; max-height:150px; overflow:auto; }
.tm-notices-resolved[hidden] { display:none; }
.tm-notices-resolved h3 { margin:0; padding:10px 18px 3px; color:#c9d7cb; font-size:11px; }
.tm-notices-resolved a { display:flex; justify-content:space-between; gap:10px; padding:7px 18px;
  color:#d9e5dc; text-decoration:none; font-size:11px; }
.tm-notices-resolved a:hover { background:#26352c; }
.tm-notices-resolved a span:first-child { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
@media(max-width:520px) {
  .tm-topbar-actions { margin-left:8px; }
  .tm-notices-panel { right:-4px; top:41px; }
  .tm-notices-head { padding-left:13px; padding-right:13px; }
  .tm-notices-intro,.tm-notices-filters { padding-left:13px; padding-right:13px; }
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
  const filters=[...wrapper.querySelectorAll("[data-notices-filter]")];
  const systemCount=wrapper.querySelector("[data-notices-system-count]");
  const scientificCount=wrapper.querySelector("[data-notices-scientific-count]");
  const resolved=wrapper.querySelector("[data-notices-resolved]");
  const resolvedList=wrapper.querySelector("[data-notices-resolved-list]");
  let nextOffset=null, loading=false, markOnLoad=false;
  let activeFilter="all";
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
    summary.textContent=visible.length+" 项待处理 · "+unread+" 项未读";
  }
  function applyFilter(){
    let shown=0;
    for(const row of list.querySelectorAll(".tm-notices-item")){
      row.hidden=activeFilter!=="all"&&row.dataset.category!==activeFilter;
      if(!row.hidden)shown++;
    }
    for(const button of filters){
      button.setAttribute("aria-pressed",String(button.dataset.noticesFilter===activeFilter));
    }
    const old=list.querySelector(".tm-notices-empty");
    if(old)old.remove();
    if(!shown&&nextOffset===null){
      const empty=document.createElement("div");empty.className="tm-notices-empty";
      empty.textContent=activeFilter==="all"?"当前没有待处理异常":
        activeFilter==="scientific"?"当前没有科研需关注事项":"当前没有系统错误";
      list.appendChild(empty);
    }
  }
  function emptyIfNeeded(){
    applyFilter();
  }
  function explanation(entry){
    if(entry.supervisor_enabled===false)return "Supervisor 未开启；该任务不会自动修复或恢复。";
    if(entry.cause_code==="contract_repair_failed"||entry.code==="contract_repair_failed")return "角色输出未通过协议检查，Supervisor 需要排查交付链路。";
    if(entry.code==="run_failed")return "任务运行失败，Supervisor 需要核查故障并验证恢复。";
    if(entry.category==="scientific")return "科研流程暂停，等待核查当前科学判断与可继续的依据。";
    return "任务已停下，查看诊断和恢复进展。";
  }
  function dependencyText(raw){
    if(raw.includes("Codex executable unavailable"))return "自动修复工具不可用，Supervisor 暂时无法完成工程修复。";
    return raw;
  }
  function item(entry){
    const key=String(entry.notice_key||"");
    const row=document.createElement("div");
    row.className="tm-notices-item";
    row.setAttribute("role","listitem");
    row.dataset.noticeKey=key;
    row.dataset.read=String(read.has(key));
    row.dataset.needsAttention=String(entry.needs_attention===true);
    row.dataset.category=entry.category==="scientific"?"scientific":"system";
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
    name.textContent=entry.task||"Sciver";
    const state=document.createElement("span");
    state.textContent=read.has(key)?"已查阅":"未查阅";
    top.append(name,state);
    const badges=document.createElement("div");badges.className="tm-notices-item-badges";
    const type=document.createElement("span");type.className="tm-notices-type";
    type.dataset.category=row.dataset.category;
    type.textContent=entry.category_label||(row.dataset.category==="scientific"?"科研需关注":"系统错误");
    const handling=document.createElement("span");handling.className="tm-notices-state";
    handling.dataset.status=entry.handling_status||"stopped";
    handling.textContent=entry.handling_label||"等待处理";
    badges.append(type,handling);
    const meta=document.createElement("div");meta.className="tm-notices-item-meta";
    const timestamp=String(entry.at||"").trim();
    const raisedAt=timestamp?timestamp.replace("T"," ").slice(0,19):"未知";
    meta.textContent="Cycle "+(entry.cycle||"?")+" · 提出时间："+raisedAt;
    const details=document.createElement("div");details.className="tm-notices-item-summary";
    details.textContent=explanation(entry);
    a.append(top,badges,details,meta);
    a.addEventListener("click",()=>{read.add(key);persist();updateCounts();});
    row.appendChild(a);
    const dependency=String(entry.blocking_dependency||"").trim();
    if(dependency){
      const wait=document.createElement("p");wait.className="tm-notices-item-dependency";
      wait.textContent="当前阻碍："+dependencyText(dependency);
      row.appendChild(wait);
    }
    const technical=document.createElement("details");technical.className="tm-notices-technical";
    const technicalLabel=document.createElement("summary");technicalLabel.textContent="诊断信息";
    const technicalText=document.createElement("p");
    technicalText.textContent=(entry.cause_code||entry.code||"unknown")+" · "+(entry.summary||"暂无详细诊断")+
      (dependency?" · 恢复依赖："+dependency:"");
    technical.append(technicalLabel,technicalText);
    const clear=document.createElement("button");
    clear.type="button";clear.className="tm-notices-clear";
    clear.textContent="隐藏通知";clear.setAttribute("aria-label","隐藏通知："+name.textContent);
    clear.addEventListener("click",event=>{
      // Removing a clicked row detaches event.target before the event bubbles
      // to document. Its outside-click handler must not close this panel.
      event.stopPropagation();
      dismissed.add(key);persist();row.remove();updateCounts();emptyIfNeeded();
    });
    row.append(technical,clear);return row;
  }
  function showResolved(entries){
    if(!resolved||!resolvedList)return;
    resolvedList.replaceChildren();
    for(const entry of entries||[]){
      const a=document.createElement("a");a.href=entry.url||"/run/";
      const name=document.createElement("span");name.textContent=(entry.task||"Sciver")+" · Cycle "+(entry.cycle||"?");
      const status=document.createElement("span");status.className="tm-notices-state";
      status.dataset.status=entry.status||"recovered";
      status.textContent=entry.status_label||"已恢复";
      a.append(name,status);resolvedList.appendChild(a);
    }
    resolved.hidden=!resolvedList.children.length;
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
      if(systemCount)systemCount.textContent=String(data.category_counts?.system||0);
      if(scientificCount)scientificCount.textContent=String(data.category_counts?.scientific||0);
      if(!append)list.replaceChildren();
      for(const entry of data.items||[]){
        if(dismissed.has(entry.notice_key))continue;
        if(markViewed)read.add(entry.notice_key);
        list.appendChild(item(entry));
      }
      nextOffset=data.next_offset;
      more.hidden=nextOffset===null;
      if(!append)showResolved(data.resolved_items);
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
  for(const button of filters)button.addEventListener("click",()=>{
    activeFilter=button.dataset.noticesFilter||"all";applyFilter();
  });
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
