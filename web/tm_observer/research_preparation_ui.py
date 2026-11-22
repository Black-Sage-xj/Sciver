"""Project preparation UI, independent of an existing scientific task."""
from __future__ import annotations

import html
import json


PREPARATION_STYLE = """<style>
.tm-prep-entry{flex:none;padding:19px;border:1px solid #4b5871;border-radius:14px;background:#192337;margin-bottom:16px}
.tm-prep-entry h2{font-size:18px;margin:0 0 7px}.tm-prep-entry p{font-size:13px;line-height:1.65;margin:0 0 13px;color:#bec9dc}
.tm-prep-entry-options{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tm-prep-entry-options button{padding:13px;text-align:left;border:1px solid #657a9b;border-radius:9px;background:#263751;color:#f1f5fb;cursor:pointer}
.tm-prep-entry-options strong{display:block;font-size:14px;margin-bottom:5px}.tm-prep-entry-options span{font-size:12px;line-height:1.5;display:block;color:#c8d4e6}
.tm-prep-transport{display:none}.tm-prep-banner{padding:14px 16px;margin:16px 0 6px;border:1px solid #526984;border-radius:12px;background:#192337;font-size:12px}
.tm-prep-banner[hidden],.tm-prep-secondary[hidden]{display:none}
.tm-prep-banner strong{font-size:13px}.tm-prep-banner p{margin:6px 0;line-height:1.5}.tm-prep-banner button,.tm-prep-dialog button{border:1px solid #657a9b;border-radius:7px;background:#263751;color:#f1f5fb;padding:7px 11px;cursor:pointer}
.tm-prep-banner a{color:#b7d5ff;text-decoration:underline}.tm-prep-created{margin-top:8px}
.tm-prep-secondary{border:0;background:transparent;color:inherit;padding:6px 8px;font:inherit;font-size:12px;cursor:pointer;white-space:nowrap}
.tm-prep-secondary:hover{background:#8882;border-radius:7px}
.tm-prep-dialog{width:min(760px,92vw);max-height:89vh;box-sizing:border-box;overflow:auto;padding:22px;border:1px solid #62728d;border-radius:14px;background:#171d28;color:#e6edf7}
.tm-prep-dialog::backdrop{background:#0008}.tm-prep-dialog h2{font-size:19px;margin:0 0 8px}.tm-prep-dialog p{font-size:13px;line-height:1.6}
.tm-prep-dialog label{display:block;font-size:13px;font-weight:600;margin:14px 0 5px}
.tm-prep-dialog input,.tm-prep-dialog textarea{display:block;width:100%;box-sizing:border-box;border:1px solid #596780;border-radius:8px;background:#101620;color:#e6edf7;padding:10px;font:13px/1.6 system-ui,sans-serif}
.tm-prep-dialog textarea{resize:vertical}.tm-prep-dialog textarea[data-prep-draft]{font-family:ui-monospace,monospace;min-height:230px}
.tm-prep-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}.tm-prep-actions [data-prep-create]{background:#315c99;border-color:#7aa8e8}
.tm-prep-status{min-height:20px;white-space:pre-wrap}.tm-prep-dialog button:disabled,.tm-prep-entry button:disabled{opacity:.5;cursor:wait}
html[data-sciver-theme="white"] .tm-prep-entry,html[data-sciver-theme="white"] .tm-prep-banner{background:#edf4ff;border-color:#b8cce8;color:#243952}
html[data-sciver-theme="white"] .tm-prep-entry p{color:#475569}html[data-sciver-theme="white"] .tm-prep-entry-options button{background:#fff;color:#183a67;border-color:#b8cce8}
html[data-sciver-theme="white"] .tm-prep-entry-options span{color:#475569}html[data-sciver-theme="white"] .tm-prep-banner a{color:#21599a}
html[data-sciver-theme="white"] .tm-prep-dialog{background:#fff;color:#243952}html[data-sciver-theme="white"] .tm-prep-dialog input,html[data-sciver-theme="white"] .tm-prep-dialog textarea{background:#f8fafc;color:#243952;border-color:#b8cce8}
@media(max-width:600px){.tm-prep-entry-options{grid-template-columns:1fr}.tm-prep-entry{padding:14px}.tm-prep-dialog{padding:15px}.tm-prep-dialog textarea[data-prep-draft]{min-height:180px}}
</style>"""


def entry_panel() -> str:
    return """<div class="tm-research-starts tm-chat-intent-starts" aria-label="新建项目方式">
<button type="button" class="tm-research-start" data-preparation-entry="clear"><strong>已有明确问题</strong><span class="tm-research-start-description">讨论目标、相关工作与可行路径，逐步确定 Research Plan。</span></button>
<button type="button" class="tm-research-start" data-preparation-entry="exploratory"><strong>从兴趣或现象开始</strong><span class="tm-research-start-description">从观察和疑问出发，了解领域背景，找到值得检验的问题。</span></button>
<button type="button" class="tm-research-start" data-research-mode="migration"><strong>迁移已有研究</strong><span class="tm-research-start-description">导入已有工作，整理研究资料并初始化项目。</span></button>
</div><p class="tm-chat-entry-status" data-prep-entry-status role="status"></p>"""


def session_panel(session: dict) -> str:
    preparation = session.get("preparation") or {}
    escape = lambda value: html.escape(str(value or ""), quote=True)
    created = escape(json.dumps(preparation.get("created_task"), ensure_ascii=False))
    ready = "true" if preparation.get("plan_ready") is True else "false"
    has_draft = "true" if str(preparation.get("draft") or "").strip() else "false"
    return f"""<div class="tm-prep-transport" data-prep-transport data-prep-initial-created="{created}" data-prep-ready="{ready}" data-prep-has-draft="{has_draft}">
<section class="tm-prep-banner" aria-label="研究计划已形成" data-prep-inline hidden>
<strong>研究计划已形成</strong><p data-prep-summary>目标、研究约束与完成标准已有草稿。可以审阅后创建项目，也可以继续讨论完善。</p>
<button type="button" data-preparation-review>审阅计划并创建项目</button>
<div class="tm-prep-created" data-prep-created hidden role="status"></div></section>
<button type="button" class="tm-prep-secondary" data-preparation-review data-prep-secondary hidden>研究计划草稿</button></div>
<dialog class="tm-prep-dialog" data-preparation-dialog aria-labelledby="tm-prep-review-title">
<form data-prep-form><h2 id="tm-prep-review-title">审阅研究计划</h2>
<p>可以先保存未完成的草稿，回到对话继续讨论。准备立项时，确认下面的目标、研究约束与完成标准。</p>
<label for="tm-prep-title">项目名称（最多 48 字符）</label><input id="tm-prep-title" data-prep-title maxlength="48" value="{escape(preparation.get('title'))}">
<label for="tm-prep-intent">研究意图</label><textarea id="tm-prep-intent" data-prep-intent rows="3" maxlength="12000">{escape(preparation.get('intent'))}</textarea>
<label for="tm-prep-draft">Research Plan 草稿（Markdown）</label>
<p>正式创建需要四个一级标题：<code># Goal</code>、<code># Research Contract</code>、<code># Final Gate</code>、<code># Required Context</code>。草稿可暂时留空；Scientific Model 将在正式研究中逐步建立。</p>
<textarea id="tm-prep-draft" data-prep-draft rows="12" maxlength="100000">{escape(preparation.get('draft'))}</textarea>
<p class="tm-prep-status" data-prep-status role="status"></p>
<div class="tm-prep-actions"><button type="submit" data-prep-save>保存草稿</button>
<button type="button" data-prep-create>确认创建项目</button>
<button type="button" data-prep-reload>读取最新草稿</button>
<button type="button" data-prep-close>返回讨论</button></div>
<div class="tm-prep-created" data-prep-created hidden role="status"></div></form></dialog>"""


PREPARATION_SCRIPT = r"""<script>
(()=>{
 'use strict';
 const root=document.querySelector('.tm-research-chat');if(!root)return;
 const request=async(path,body)=>{
   const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),20000);
   try{
     const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
       signal:abort.signal,headers:body===undefined?{}:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
       ...(body===undefined?{}:{body:JSON.stringify(body)})});
     const data=await response.json();
     if(!response.ok){const error=new Error(data.error||'操作未完成，请重试。');error.status=response.status;throw error;}
     return data;
   }finally{clearTimeout(timer);}
 };
 const navigate=href=>{const url=new URL(href,location.origin);if(root.dataset.embedded==='true')url.searchParams.set('embed','1');location.assign(url.pathname+url.search);};
 root.querySelectorAll('[data-preparation-entry]').forEach(button=>button.addEventListener('click',async()=>{
   const buttons=root.querySelectorAll('[data-preparation-entry]'),status=root.querySelector('[data-prep-entry-status]');
   buttons.forEach(item=>item.disabled=true);if(status)status.textContent='正在打开项目筹备对话…';
   try{
     const result=await request('/api/research-chat/session',{mode:'preparation',entry:button.dataset.preparationEntry});
     if(!result.session_id)throw new Error('未取得筹备会话，请重试。');
     navigate('/research-chat/?session='+encodeURIComponent(result.session_id));
   }catch(error){buttons.forEach(item=>item.disabled=false);if(status)status.textContent=error.name==='AbortError'?'打开请求超时，请重试。':error.message;}
 }));
 const dialog=root.querySelector('[data-preparation-dialog]');if(!dialog)return;
 const session=root.dataset.session,base='/api/research-chat/'+encodeURIComponent(session);
 const title=dialog.querySelector('[data-prep-title]'),intent=dialog.querySelector('[data-prep-intent]'),draft=dialog.querySelector('[data-prep-draft]');
 const status=dialog.querySelector('[data-prep-status]');let revision='',dirty=false,busy=false,created=null;
 const transport=root.querySelector('[data-prep-transport]'),inline=root.querySelector('[data-prep-inline]'),secondary=root.querySelector('[data-prep-secondary]');
 let ready=transport.dataset.prepReady==='true',hasDraft=transport.dataset.prepHasDraft==='true';
 const present=()=>{
   const slot=root.querySelector('[data-preparation-inline-slot]'),tools=root.querySelector('[data-preparation-secondary-slot]');
   if(slot&&inline.parentElement!==slot)slot.append(inline);
   if(tools&&secondary.parentElement!==tools)tools.append(secondary);
   const viewport=slot?.closest('.tm-aui-viewport'),wasHidden=inline.hidden;
   const nearBottom=viewport&&viewport.scrollHeight-viewport.scrollTop-viewport.clientHeight<80;
   inline.hidden=!slot||!created&&(!ready||slot.dataset.streaming==='true');
   secondary.hidden=!tools||!hasDraft&&!created;
   if(wasHidden&&!inline.hidden&&nearBottom)requestAnimationFrame(()=>viewport.scrollTo({top:viewport.scrollHeight}));
 };
 const layout=new MutationObserver(present);
 layout.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['data-streaming']});
 const say=text=>status.textContent=text;
 const controls=()=>dialog.querySelectorAll('button,input,textarea');
 const setBusy=value=>{busy=value;controls().forEach(control=>control.disabled=value||Boolean(created)&&control.matches('input,textarea,[data-prep-save]'));};
 const showCreated=data=>{
   const task=data.created_task||data;
   const href=data.task_href||task.task_href||task.href||(task.task_id?'/run/'+encodeURIComponent(task.task_id)+'/':'');
   if(!href)return;
   let url;try{url=new URL(href,location.origin);}catch{return;}
   if(url.origin!==location.origin||!/^\/run\//.test(url.pathname))return;
   created=data;
   root.querySelectorAll('[data-prep-created]').forEach(box=>{
     const link=document.createElement('a');link.href=url.pathname+url.search;link.textContent='打开正式项目 →';
     box.replaceChildren(document.createTextNode('项目已创建，尚未启动科研。'),link);box.hidden=false;
   });
   inline.querySelector('strong').textContent='项目已创建';
   inline.querySelector('[data-prep-summary]').textContent='筹备对话与研究计划已保留，可以打开正式项目。';
   inline.querySelector('[data-preparation-review]').textContent='查看研究计划';
   secondary.textContent='研究计划';
   dialog.querySelector('[data-prep-create]').textContent='查看已创建的项目';
   setBusy(busy);
   present();
 };
 const updatePresentation=data=>{
   data=data.preparation||data;
   ready=data.plan_ready===true;hasDraft=Boolean(String(data.draft||'').trim());
   if(data.created_task)showCreated(data);else present();
 };
 const apply=data=>{
   data=data.preparation||data;revision=data.revision||revision;
   title.value=data.title||'';intent.value=data.intent||'';draft.value=data.draft||'';dirty=false;
   updatePresentation(data);
 };
 const read=async()=>{const data=await request(base+'/preparation');apply(data);return data;};
 const save=async()=>{
   const data=await request(base+'/preparation',{title:title.value,intent:intent.value,draft:draft.value,expected_revision:revision});
   apply(data);say('草稿已保存，可以返回讨论继续完善。');return data;
 };
 const failed=error=>say(error.status===409&&error.message==='计划草稿已变化，请刷新并重新审阅'?
   error.message+' 当前输入已保留；请读取最新草稿后整合，再保存。':
   error.name==='AbortError'?'请求结果尚未确认。可读取最新草稿或重试；创建操作会返回同一个项目。':error.message);
 [title,intent,draft].forEach(field=>field.addEventListener('input',()=>{dirty=true;say('有未保存修改。');}));
 root.querySelectorAll('[data-preparation-review]').forEach(button=>button.addEventListener('click',async()=>{
   dialog.showModal();if(dirty||busy)return;setBusy(true);say('正在读取草稿…');
   try{await read();say(created?'项目已创建，下面的筹备草稿和对话仍保留。':'草稿可暂时留空，保存后返回讨论继续完善。');}
   catch(error){failed(error);}finally{setBusy(false);}
 }));
 dialog.querySelector('[data-prep-close]').addEventListener('click',()=>dialog.close());
 dialog.querySelector('[data-prep-form]').addEventListener('submit',async event=>{
   event.preventDefault();if(busy)return;setBusy(true);say('正在保存草稿…');
   try{await save();}catch(error){failed(error);}finally{setBusy(false);}
 });
 dialog.querySelector('[data-prep-reload]').addEventListener('click',async()=>{
   if(busy||dirty&&!confirm('读取最新草稿会替换当前未保存的修改，是否继续？'))return;
   setBusy(true);try{await read();say('已读取最新草稿。');}catch(error){failed(error);}finally{setBusy(false);}
 });
 dialog.querySelector('[data-prep-create]').addEventListener('click',async()=>{
   if(busy)return;
   if(created){showCreated(created);say('项目已创建，可通过下方链接打开。');return;}
   setBusy(true);say('正在创建正式项目…');
   try{
     if(dirty||!revision)await save();
     const result=await request(base+'/create-project',{expected_revision:revision,confirm:true});
     showCreated(result);if(!created)throw new Error('创建回执缺少正式项目链接，请读取最新状态后重试。');
     say('项目已创建，尚未启动科研。筹备对话与草稿会继续保留。');
   }catch(error){failed(error);}finally{setBusy(false);}
 });
 try{const initial=JSON.parse(transport.dataset.prepInitialCreated);if(initial)showCreated({created_task:initial});}catch(_error){}
 present();
 let refreshing=false;
 const refresh=async()=>{
   if(refreshing||document.hidden||created)return;refreshing=true;
   try{updatePresentation(await request(base+'/preparation'));}catch(_error){}
   finally{refreshing=false;}
 };
 const polling=setInterval(refresh,3000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh();});
 addEventListener('pagehide',()=>{clearInterval(polling);layout.disconnect();},{once:true});
 void refresh();
})();
</script>"""
