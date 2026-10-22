"""Lazy read-only display of per-approval Reviewer SDK conversation archives."""
from __future__ import annotations
import json


def script(observer_id: str) -> str:
    return r'''<script>(function(){
"use strict";
const run=TM_OBSERVER_ID;
const dialog=document.getElementById('tm-investigator-budget-dialog');
function add(parent,tag,cls,text){
  const element=document.createElement(tag);
  if(cls)element.className=cls;
  if(text!==undefined)element.textContent=String(text);
  parent.append(element);return element;
}
function block(parent,title,value){
  const section=add(parent,'section','tm-budget-trace-block');
  add(section,'strong','',title);
  add(section,'pre','',value||'未记录正文');
}
async function load(button,offset=0,full=null){
  if(!dialog?.open)return;
  const panel=button.closest('[data-budget-review-panel]');
  const box=panel?.querySelector('[data-budget-trace-box]');
  const first=box?.querySelector('[data-budget-trace-load]');
  const target=box?.querySelector('[data-budget-trace-content]');
  if(!first||!target||panel.hidden)return;
  button.disabled=true;
  if(full===null&&offset===0){target.replaceChildren();add(target,'p','tm-budget-modal-empty','正在读取 Reviewer 对话…');}
  try{
    const url=new URL('/api/run/'+run+'/budget-review-trace',location.origin);
    url.searchParams.set('cycle',first.dataset.budgetTraceCycle);
    url.searchParams.set('review',first.dataset.budgetTraceReview);
    url.searchParams.set('cursor',String(offset));
    if(full!==null)url.searchParams.set('item',String(full));
    const response=await fetch(url,{cache:'no-store'});
    if(!response.ok)throw Error('HTTP '+response.status);
    const data=await response.json();
    if(!box.isConnected||!dialog.open)return;
    if(full!==null){
      const holder=button.closest('[data-budget-trace-item]');
      if(!holder||!data.messages?.[0])throw Error('归档条目已不可读');
      holder.querySelector('pre').textContent=data.messages[0].content;
      holder.querySelector('small')?.remove();button.remove();
      return;
    }
    if(offset===0){
      target.replaceChildren();
      if(!data.available){
        add(target,'p','tm-budget-trace-warning',data.notice||'该次审查没有可读取的 Reviewer 轨迹。');
        if(data.partial){
          const partial=add(target,'details','tm-budget-modal-disclosure');
          add(partial,'summary','','现存部分审查元数据（不代表完整工具执行轨迹）');
          add(partial,'p','tm-budget-trace-info',
            '模型 '+(data.partial.model||'未记录')+' · Provider '+(data.partial.provider||'未记录')+
            ' · 推理档位 '+(data.partial.reasoning_effort||'未记录'));
          block(partial,'SDK 报告已读取的文件路径（不包含工具命令或输出）',
            (data.partial.reported_read_paths||[]).join('\n')||'未登记读取路径');
        }
        first.hidden=true;
        return;
      }
      add(target,'p','tm-budget-trace-info',
        'Reviewer · '+(data.model||'未知模型')+' · '+(data.provider||'未知 Provider')+
        ' · Thread '+(data.thread_id||'未记录'));
      block(target,'实际启动 Prompt（SDK 提交原文）',data.prompt);
      block(target,'最终模型输出（SDK 据此审批）',data.model_output);
      if(data.prompt_truncated||data.model_output_truncated)
        add(target,'p','tm-budget-trace-warning','超长正文超过接口显示上限，原始归档留存于服务器。');
      add(target,'p','tm-budget-trace-info',data.native_notice||'');
      if(data.native_archive_truncated)
        add(target,'p','tm-budget-trace-warning','SDK 归档存在容量截断/大项目摘要，不应视为完整原始输出。');
      add(target,'div','tm-budget-trace-list');
    }
    const list=target.querySelector('.tm-budget-trace-list');
    target.querySelector('[data-budget-trace-more]')?.remove();
    for(const msg of data.messages||[]){
      const cls=msg.type==='usermessage'?'tm-stage-prompt':
        ['commandexecution','functioncalloutput','mcptoolcall','dynamictoolcall','filechange','websearch'].includes(msg.type)
        ?'tm-stage-tool-message':'tm-stage-agent-message';
      const card=add(list,'article','tm-stage-bubble tm-budget-trace-item '+cls);
      card.dataset.budgetTraceItem=String(msg.index);
      add(card,'strong','','Turn '+msg.turn+' · '+msg.type+' · Item '+msg.item_index);
      add(card,'pre','',msg.content||'源记录未提供正文');
      if(msg.truncated){
        add(card,'small','tm-budget-trace-warning','正文仅显示预览，可继续读取该条完整归档。');
        const raw=add(card,'button','','查看此条完整归档');
        raw.type='button';raw.dataset.budgetTraceFull=String(msg.index);
      }
    }
    if(data.next_cursor!==null&&data.next_cursor!==undefined){
      const more=add(target,'button','tm-budget-trace-more','加载下一批消息及工具结果');
      more.type='button';more.dataset.budgetTraceMore=String(data.next_cursor);
    }else if(offset===0&&!(data.messages||[]).length){
      add(target,'p','tm-budget-modal-empty','没有留存原生工具消息；目前仅有 Prompt 与最终模型输出。');
    }
    first.hidden=true;
  }catch(error){
    add(target,'p','tm-budget-trace-warning','读取审查轨迹失败：'+String(error.message||error));
  }finally{if(button.isConnected)button.disabled=false;}
}
dialog?.addEventListener('click',event=>{
  const first=event.target.closest('[data-budget-trace-load]');
  if(first){load(first);return;}
  const next=event.target.closest('[data-budget-trace-more]');
  if(next){load(next,Number(next.dataset.budgetTraceMore));return;}
  const raw=event.target.closest('[data-budget-trace-full]');
  if(raw){load(raw,0,Number(raw.dataset.budgetTraceFull));}
});
})();</script>'''.replace('TM_OBSERVER_ID',json.dumps(observer_id))
