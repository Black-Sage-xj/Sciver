"""Dedicated Research Plan viewer/editor for one TM task."""
from __future__ import annotations

import html


def page_body(runs: list[dict], selected_id: str = "") -> str:
    available = sorted(
        (r for r in runs if isinstance(r.get("observer_id"), str)
         and len(r["observer_id"]) == 16
         and all(c in "0123456789abcdef" for c in r["observer_id"])),
        key=lambda r: (str(r.get("task_name") or "").casefold(), r["observer_id"]),
    )
    if selected_id and all(r["observer_id"] != selected_id for r in available):
        raise ValueError("unknown task")
    chosen = selected_id or (available[0]["observer_id"] if available else "")
    options = "".join(
        '<option value="' + html.escape(r["observer_id"], quote=True) + '"' +
        (' selected' if r["observer_id"] == chosen else "") + '>' +
        html.escape(str(r.get("task_name") or r["observer_id"])) + '</option>'
        for r in available
    )
    return r"""<style>
.tm-research_plan-view .tm-content { max-width:1170px; }
.rp-head {display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-bottom:14px}
.rp-head p {color:#a1a1aa;font-size:13px;margin:9px 0 0;max-width:720px}
.rp-picker {font-size:12px;display:flex;flex-direction:column;gap:5px;color:#a1a1aa}
.rp-picker select {min-width:220px;max-width:440px;padding:9px 11px;color:#f4f4f5;border-radius:8px;background:#19191c;border:1px solid #4b4b54}
.rp-toolbar {display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0 21px;flex-wrap:wrap;color:#a1a1aa;font-size:12px}
.rp-toolbar button {font-size:12px}
.rp-status {font-size:12px;min-height:20px;overflow-wrap:anywhere}
.rp-error {color:#fda4af} .rp-success {color:#86efac}
.rp-fields {display:grid;gap:18px;max-width:1080px}
.rp-field {background:#111115;border:1px solid #34343b;border-radius:12px;overflow:hidden}
.rp-field-head {display:flex;align-items:center;justify-content:space-between;gap:18px;padding:18px 20px;border-bottom:1px solid #2e2e34;flex-wrap:wrap}
.rp-field-title {font-weight:650;font-size:16px;color:#f5f5f5}
.rp-field-owner {font-size:11px;color:#a1a1aa;margin-top:4px}
.rp-field-body {padding:19px 21px;white-space:normal;line-height:1.75;overflow-wrap:anywhere;color:#d4d4d8;font-size:13px}
.rp-field-body>*:first-child {margin-top:0}
.rp-field-body>*:last-child {margin-bottom:0}
.rp-field-body h1,.rp-field-body h2,.rp-field-body h3 {margin:17px 0 9px}
.rp-field-body h1 {font-size:18px}.rp-field-body h2 {font-size:15px}
.rp-field-body ul,.rp-field-body ol {padding-left:23px}
.rp-field-body pre {padding:14px;overflow:auto;background:#09090b;border:1px solid #303036;border-radius:8px;font:12px/1.5 ui-monospace,monospace}
.rp-field-body blockquote {padding:7px 13px;margin:10px 0;border-left:3px solid #5b7eb8;color:#d4d4d8}
.rp-field-body table {display:block;overflow:auto}
.rp-raw {white-space:pre-wrap;font:12px/1.65 ui-monospace,monospace}
.rp-field-edit {padding:20px}
.rp-field-edit textarea {display:block;width:100%;min-height:190px;max-height:75vh;resize:vertical;
 font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;border:1px solid #606776;border-radius:8px;
 background:#0b0e15;color:#f4f4f5;padding:14px}
.rp-field-actions {display:flex;align-items:center;gap:9px;margin-top:12px;flex-wrap:wrap}
.rp-field-actions .rp-primary {background:#314b76;border-color:#6282b7}
.rp-field button {font-size:12px}
.rp-note {margin:0;padding:12px 20px;background:#161822;color:#aeb8cd;font-size:12px;border-bottom:1px solid #323848}
.rp-field .rp-message {min-height:20px;color:#fcd34d;font-size:12px}
.rp-fields [hidden] {display:none!important}
.rp-history {background:#111115;border:1px solid #34343b;border-radius:12px;padding:16px 18px;margin-bottom:20px}
.rp-history[hidden] {display:none!important}
.rp-history-list {display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.rp-history pre {font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap;max-height:360px;overflow:auto;color:#d4d4d8;border:1px solid #34343b;border-radius:8px;padding:12px}
.rp-reason {width:100%;min-height:70px!important;margin-top:12px;resize:vertical}
@media(max-width:660px){.rp-field-head,.rp-field-body,.rp-field-edit{padding:14px}}
</style>
<div class="rp-head"><div><h1>Research Plan</h1>
<p>每个字段来自当前任务的原始 <code>research_plan.md</code>。可独立编辑、保存，其他部分保持不变。</p></div>
<label class="rp-picker">科研任务<select id="rp-task">""" + options + r"""</select></label></div>
<div class="rp-toolbar">
<span>Goal / Research Contract / Final Gate / Required Context / Scientific Model</span>
<div><button type="button" id="rp-history-toggle">版本历史</button> <button type="button" id="rp-refresh">刷新文件</button> <a id="rp-console" href="/run/">Task Console →</a></div>
</div>
<section class="rp-history" id="rp-history" hidden>
<label>历史类型 <select id="rp-history-scope"><option value="plan">Research Plan</option><option value="model">Scientific Model</option></select></label>
<strong id="rp-history-title">Research Plan 版本历史</strong>
<p class="muted">时间均为北京时间。两套历史独立编号。Research Plan 记录目标、约束、完成标准与上下文；Scientific Model 记录科学认识。恢复只影响所选部分，旧记录全部保留。</p>
<div id="rp-history-list" class="rp-history-list"></div>
<p id="rp-history-meta" class="muted"></p>
<div class="rp-field-actions"><button type="button" id="rp-history-restore" hidden>恢复选中版本</button></div>
<pre id="rp-history-diff"></pre>
<details><summary>查看所选部分 Markdown</summary><pre id="rp-history-content"></pre></details>
</section>
<p class="rp-status" id="rp-status" role="status">正在读取 Research Plan…</p>
<div class="rp-fields" id="rp-fields"></div>
<p class="muted">编辑后点击该字段的“保存”。保存会修改原始 MD，供后续读取使用；不会改变当前 Cycle 已冻结的科研输入。Scientific Model 也可能由科研流程更新，出现冲突时需要重新审阅。</p>
<script src="https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
<script>
(function(){
'use strict';
const task=document.getElementById('rp-task'),fieldBox=document.getElementById('rp-fields'),
  status=document.getElementById('rp-status'),refresh=document.getElementById('rp-refresh'),
  consoleLink=document.getElementById('rp-console'),
  histToggle=document.getElementById('rp-history-toggle'),histPane=document.getElementById('rp-history'),
  histList=document.getElementById('rp-history-list'),histMeta=document.getElementById('rp-history-meta'),
  histDiff=document.getElementById('rp-history-diff'),histContent=document.getElementById('rp-history-content'),
  histRestore=document.getElementById('rp-history-restore'),
  histScope=document.getElementById('rp-history-scope'),histTitle=document.getElementById('rp-history-title');
let current=null,editing=new Set(),selectedHistory=null;
const chosen=()=>task.value;
const historyName=()=>histScope.value==='model'?'Scientific Model':'Research Plan';
const historyVersion=(data)=>histScope.value==='model'?data.model_version:data.version;
function historyTime(raw,dateOnly=false){
 const date=new Date(raw);
 if(Number.isNaN(date.getTime()))return raw||'时间未知';
 const options={timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',
   hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'};
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',options)
   .formatToParts(date).map(p=>[p.type,p.value]));
 const day=parts.year+'-'+parts.month+'-'+parts.day;
 return dateOnly?day:day+' '+parts.hour+':'+parts.minute+':'+parts.second+'（北京时间）';
}

const endpoint=()=>'/api/run/'+chosen()+'/research-plan';
const text=(node,str)=>{node.textContent=str;return node};
const button=(caption)=>{const b=document.createElement('button');b.type='button';b.textContent=caption;return b};
function note(message,bad=false){
 text(status,message);
 status.className='rp-status '+(bad?'rp-error':'rp-success');
}
function renderMd(el,content){
 if(window.marked&&window.DOMPurify){
   const math=[];
   const protectedText=(content||'').replace(/(\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g,raw=>{
      const block=raw.startsWith('$$');
      math.push({value:raw.slice(block?2:1,block?-2:-1),block});
      return 'TMPLANMATH'+(math.length-1)+'END';
   });
   el.innerHTML=window.DOMPurify.sanitize(window.marked.parse(protectedText));
   if(window.katex){
      const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),nodes=[];
      while(walker.nextNode())if(walker.currentNode.nodeValue.includes('TMPLANMATH'))nodes.push(walker.currentNode);
      for(const node of nodes){
         const fragment=document.createDocumentFragment(),re=/TMPLANMATH(\d+)END/g;
         let cursor=0,match;const raw=node.nodeValue;
         while((match=re.exec(raw))){
            fragment.append(document.createTextNode(raw.slice(cursor,match.index)));
            const spec=math[Number(match[1])],span=document.createElement(spec.block?'div':'span');
            try{span.innerHTML=window.katex.renderToString(spec.value,{displayMode:spec.block,throwOnError:false,trust:false})}
            catch{span.textContent=spec.value}
            fragment.append(span);cursor=re.lastIndex;
         }
         fragment.append(document.createTextNode(raw.slice(cursor)));node.replaceWith(fragment);
      }
   }
   el.querySelectorAll('a').forEach(a=>{
      const href=a.getAttribute('href')||'';
      if(/^https?:\/\//i.test(href)){a.target='_blank';a.rel='noopener noreferrer'}
      else if(!href.startsWith('#'))a.removeAttribute('href');
   });
 }else {el.classList.add('rp-raw');el.textContent=content||'(空)'}
}
async function getData(path){
 const response=await fetch(path,{credentials:'same-origin',cache:'no-store'});
 const result=await response.json();
 if(!response.ok)throw Error(result.error||'读取失败');
 return result;
}
async function loadHistory(){
  if(!current||!chosen())return;
  selectedHistory=null;histRestore.hidden=true;histMeta.textContent='';histContent.textContent='';histDiff.textContent='';
  const id=chosen(),scope=histScope.value;
  text(histTitle,historyName()+' 版本历史');
  const data=await getData(endpoint()+'/history?scope='+scope);
  if(chosen()!==id||histScope.value!==scope)return;
  histList.replaceChildren();
  for(const entry of data.history){
    const b=button('v'+entry.version+' · '+historyTime(entry.recorded_at,true)+' · '+entry.source);
    b.title=entry.reason;
    b.onclick=async()=>{
      try{
        const detail=await getData(endpoint()+'/history?scope='+scope+'&version='+entry.version);
        if(chosen()!==id||histScope.value!==scope)return;
        selectedHistory=entry.version;
        text(histMeta,'v'+entry.version+' · '+historyTime(entry.recorded_at)+' · '+entry.source+' · '+entry.section+' | 更正说明：'+entry.reason);
        text(histDiff,detail.diff||'与当前版本内容一致');
        text(histContent,detail.content||'');
        histRestore.hidden=detail.current_revision===entry.sha256;
      }catch(e){note('历史版本读取失败：'+e.message,true)}
    };
    histList.append(b);
  }
  if(!data.history.length)text(histMeta,'暂无版本');
}
histScope.onchange=()=>loadHistory().catch(e=>note(e.message,true));
histToggle.onclick=async()=>{
  histPane.hidden=!histPane.hidden;
  if(!histPane.hidden){try{await loadHistory()}catch(e){note(e.message,true)}}
};
histRestore.onclick=async()=>{
  if(!current||selectedHistory==null)return;
  if(editing.size){note('请先完成或取消未保存的编辑',true);return}
  const change_reason=prompt('请说明恢复该版本要更正的问题（必填）');
  if(!change_reason||!change_reason.trim())return;
  if(!confirm('将 '+historyName()+' 恢复为 v'+selectedHistory+'，另一部分保持当前内容。确定吗？'))return;
  try{
    const response=await fetch(endpoint(),{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
      body:JSON.stringify({operation:'restore',scope:histScope.value,version:selectedHistory,
        expected_revision:current.scope_revisions[histScope.value],change_reason})});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||'恢复失败');
    await load();
    note(historyName()+' 已恢复为新版本 v'+historyVersion(result));
  }catch(e){note('恢复失败：'+e.message,true)}
};
async function load(){
 const id=chosen(); if(!id){fieldBox.replaceChildren();note('暂无可选择的任务',true);return}
 if(editing.size&&!confirm('还有未保存的编辑，确定放弃并刷新吗？'))return;
 editing.clear();fieldBox.replaceChildren();note('正在读取原始 Markdown…');
 consoleLink.href='/run/'+id+'/';
 try{
   const data=await getData(endpoint());if(chosen()!==id)return;
   current=data;
   for(const sec of data.sections)buildSection(sec);
   if(!histPane.hidden)await loadHistory();
   note('Research Plan v'+data.version+' · Scientific Model v'+data.model_version+' · 已读取 '+data.sections.length+' 个字段');
 }catch(error){note(error.message,true)}
}
function buildSection(sec){
 const card=document.createElement('section');card.className='rp-field';
 const head=document.createElement('div');head.className='rp-field-head';
 const title=document.createElement('div');
 const name=document.createElement('div');name.className='rp-field-title';text(name,sec.name);
 const owner=document.createElement('div');owner.className='rp-field-owner';text(owner,'维护权：'+sec.owner);
 title.append(name,owner);
 const edit=button('编辑');head.append(title,edit);
 const warning=document.createElement('p');warning.className='rp-note';
 warning.textContent=sec.name==='Scientific Model'
   ? 'Scientific Model 可能在 Cycle 结束时由 SDK 更新；编辑前请核对当前科学认识。'
   : '此字段由人维护。网页保存不会覆盖当前 Cycle 的冻结输入。';
 warning.hidden=true;
 const view=document.createElement('div');view.className='rp-field-body';renderMd(view,sec.text);
 const form=document.createElement('div');form.className='rp-field-edit';form.hidden=true;
 const textarea=document.createElement('textarea');textarea.setAttribute('aria-label',sec.name+' Markdown 编辑器');
 textarea.spellcheck=false;textarea.value=sec.text;
 const actions=document.createElement('div');actions.className='rp-field-actions';
 const save=button('保存此字段');save.className='rp-primary';
 const cancel=button('取消');
 const message=document.createElement('span');message.className='rp-message';message.setAttribute('role','status');
 const reason=document.createElement('textarea');reason.className='rp-reason';
 reason.setAttribute('aria-label',sec.name+' 更正问题说明');
 reason.placeholder='必填：本次更正的问题、原因和预期效果（将留存在版本记录中）';
 actions.append(save,cancel,message);form.append(textarea,reason,actions);
 card.append(head,warning,view,form);fieldBox.append(card);
 function close(){editing.delete(sec.name);view.hidden=false;form.hidden=true;warning.hidden=true;edit.hidden=false}
 edit.onclick=()=>{editing.add(sec.name);view.hidden=true;form.hidden=false;warning.hidden=false;edit.hidden=true;textarea.focus()};
 cancel.onclick=()=>{textarea.value=sec.text;text(message,'');close()};
 save.onclick=async()=>{
   if(textarea.value===sec.text){close();return}
   if(!reason.value.trim()){text(message,'请填写更正问题说明');reason.focus();return}
   save.disabled=true;cancel.disabled=true;text(message,'保存中…');
   try{
      const response=await fetch(endpoint(),{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
        body:JSON.stringify({section:sec.name,text:textarea.value,expected_revision:sec.revision,change_reason:reason.value})
      });
      const result=await response.json();
      if(!response.ok)throw Error(result.error||'保存失败');
      const updated=result.sections.find(x=>x.name===sec.name);
      sec.text=updated.text;sec.revision=updated.revision;current.revision=result.revision;current.version=result.version;current.model_version=result.model_version;current.scope_revisions=result.scope_revisions;
      renderMd(view,sec.text);close();note(sec.name+' 已保存为 '+(sec.name==='Scientific Model'?'Scientific Model v'+result.model_version:'Research Plan v'+result.version));reason.value='';if(!histPane.hidden)await loadHistory();
      text(message,'');
   }catch(e){text(message,e.message);note('保存失败：'+e.message,true)}
   finally{save.disabled=false;cancel.disabled=false}
 };
}
task.onchange=()=>{const id=chosen();const before=new URLSearchParams(location.search).get('observer_id');
 if(editing.size&&!confirm('还有未保存的编辑，确定切换任务吗？')){task.value=before||current?.observer_id||'';return}
 history.replaceState(null,'','/research-plan/?observer_id='+encodeURIComponent(id));editing.clear();load();
};
refresh.onclick=load;
window.addEventListener('beforeunload',event=>{if(editing.size){event.preventDefault();event.returnValue=''}});
load();
})();
</script>
"""


def page_html(state: dict, selected_id: str = "") -> str:
    from .dashboard_ui import dashboard_shell
    runs = state.get("runs", [])
    body = page_body(runs, selected_id)
    active = selected_id or (runs[0].get("observer_id") if runs else "")
    back = "/run/" + active + "/" if active and any(r.get("observer_id") == active for r in runs) else None
    return dashboard_shell("Sciver Observatory — Research Plan", body, active="research_plan",
                           back_href=back, breadcrumb_leaf="Research Plan")
