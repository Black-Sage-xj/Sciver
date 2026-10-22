"""Lazy, incident/Cycle-bound read-only Agent review viewer for important events."""
from __future__ import annotations
import json


def script(observer_id: str) -> str:
    return r'''<script>(function(){
"use strict";
const host=document.getElementById('tm-run-live');
const dialog=document.getElementById('tm-important-review-dialog');
const content=dialog?.querySelector('[data-important-review-content]');
let opener=null, requestGeneration=0, current=null;
function add(parent,tag,cls,value){
  const el=document.createElement(tag);
  if(cls)el.className=cls;
  if(value!==undefined)el.textContent=String(value);
  parent.append(el);return el;
}
function field(title,value){
  const section=add(content,'section','tm-budget-trace-block');
  add(section,'strong','',title);
  add(section,'pre','',value||'未记录');
}
async function load(cursor=0,full=null){
  if(!dialog?.open||!current)return;
  const generation=++requestGeneration;
  if(cursor===0&&full===null){content.replaceChildren();add(content,'p','tm-budget-trace-info','正在读取该次接管记录…');}
  try{
    const endpoint=current.kind==='budget'?'budget-review-trace':'attention-review';
    const url=new URL('/api/run/'+TM_OBSERVER_ID+'/'+endpoint,location.origin);
    url.searchParams.set('cycle',current.cycle);
    if(current.kind==='budget')url.searchParams.set('review',current.index);
    else{
      url.searchParams.set('incident',current.incident);
      if(current.ref)url.searchParams.set('ref',current.ref);
    }
    url.searchParams.set('cursor',String(cursor));
    if(full!==null)url.searchParams.set('item',String(full));
    const response=await fetch(url,{cache:'no-store'});
    if(!response.ok)throw Error('HTTP '+response.status);
    const data=await response.json();
    if(!dialog.open||generation!==requestGeneration)return;
    if(full!==null){
      const holder=content.querySelector('[data-important-review-item="'+full+'"]');
      if(!holder||!data.messages?.[0])throw Error('归档条目已不可读');
      holder.querySelector('pre').textContent=data.messages[0].content;
      holder.querySelector('[data-important-review-full]')?.remove();
      return;
    }
    if(cursor===0){
      content.replaceChildren();
      add(content,'p','tm-budget-trace-info','Cycle '+data.cycle+' · '+(current.kind==='budget'?'预算审批 Reviewer':'Attention Reviewer')+
          (data.model?' · '+data.model:''));
      if(!data.available){
        add(content,'p','tm-budget-trace-warning',data.notice||'没有留存此次 Agent 对话');
        if(data.partial)field('仅有部分元数据',JSON.stringify(data.partial,null,2));
        return;
      }
      if(current.kind==='attention'){
        field('SDK 接管决策与理由',(data.action||'')+'\n'+(data.reason||'')+
              (data.guidance?'\n指导：'+data.guidance:''));
        add(content,'p',data.exchange_recorded?'tm-budget-trace-info':'tm-budget-trace-warning',data.notice);
        if(data.exchange_recorded){
          field('实际提交给 Agent 的 Prompt',data.prompt);
          field('Agent 原始模型答复',data.model_output);
        }
      }else{
        field('实际提交给 Reviewer 的 Prompt',data.prompt);
        field('Reviewer 模型输出',data.model_output);
      }
      add(content,'p','tm-budget-trace-info',data.native_notice||'');
      if(data.native_archive_truncated)
        add(content,'p','tm-budget-trace-warning','归档容量已截断；不能视为完整原始记录。');
      add(content,'div','tm-budget-trace-list');
    }
    const list=content.querySelector('.tm-budget-trace-list');
    content.querySelector('[data-important-review-more]')?.remove();
    for(const row of data.messages||[]){
      const card=add(list,'article','tm-stage-bubble tm-budget-trace-item');
      card.dataset.importantReviewItem=String(row.index);
      add(card,'strong','','Turn '+row.turn+' · '+row.type+' · Item '+row.item_index);
      add(card,'pre','',row.content||'此条没有保留正文');
      if(row.truncated){
        add(card,'p','tm-budget-trace-warning','本条为预览，可继续查看完整归档。');
        const fullButton=add(card,'button','','查看此条完整归档');
        fullButton.type='button';fullButton.dataset.importantReviewFull=String(row.index);
      }
    }
    if(data.next_cursor!==null&&data.next_cursor!==undefined){
      const more=add(content,'button','tm-budget-trace-more','加载下一批消息及工具结果');
      more.type='button';
      more.dataset.importantReviewMore=String(data.next_cursor);
    }else if(cursor===0&&!(data.messages||[]).length)
      add(content,'p','tm-budget-trace-info','该次 Reviewer 没有留存原生工具消息；以上是已归档的 Prompt 与模型回复。');
  }catch(error){
    if(dialog.open&&generation===requestGeneration){
      if(cursor===0)content.replaceChildren();
      add(content,'p','tm-budget-trace-warning','读取本次 Agent 记录失败：'+String(error.message||error));
    }
  }
}
host?.addEventListener('click',event=>{
  const button=event.target.closest('[data-important-review-kind]');
  if(!button||!dialog)return;
  const kind=button.dataset.importantReviewKind;
  if(!['attention','budget'].includes(kind))return;
  current={kind,cycle:button.dataset.importantReviewCycle,
           incident:button.dataset.importantReviewIncident,
           ref:button.dataset.importantReviewRef,
           index:button.dataset.importantReviewIndex};
  opener=button;
  if(!dialog.open)dialog.showModal();
  load();
});
dialog?.addEventListener('click',event=>{
  if(event.target===dialog||event.target.closest('[data-important-review-close]'))dialog.close();
  const more=event.target.closest('[data-important-review-more]');
  if(more)load(Number(more.dataset.importantReviewMore));
  const full=event.target.closest('[data-important-review-full]');
  if(full)load(0,Number(full.dataset.importantReviewFull));
});
dialog?.addEventListener('close',()=>{
  ++requestGeneration;
  current=null;
  if(opener?.isConnected)opener.focus();
  opener=null;
});
})();</script>'''.replace("TM_OBSERVER_ID",json.dumps(observer_id))
