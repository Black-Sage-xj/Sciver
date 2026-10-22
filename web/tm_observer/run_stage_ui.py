"""Single-page Stage Conversation reading experience (read-only, lazy loading)."""
from __future__ import annotations
import html
import json

def shell() -> str:
    return '''<div class="tm-stage-reader" data-tui-pane="secondary" aria-label="Stage Conversation">
      <div class="tm-stage-toolbar">
        <label>Cycle <select data-stage-cycle aria-label="选择 Cycle"></select></label>
        <label>视图 <select data-stage-density><option value="normal">对话模式</option>
          <option value="detail">详细模式</option><option value="raw">原始审计</option></select></label>
        <label>Agent <select data-stage-role><option value="all">全部 Agent</option></select></label>
        <label class="tm-stage-search">搜索 Stage <input data-stage-search type="search" placeholder="角色、阶段、状态…" /></label>
        <button type="button" data-stage-refresh>刷新最新记录</button>
      </div>
      <p class="tm-stage-help">按实际 Invocation 执行顺序展开 Stage。右侧为 Agent 收到的 Prompt，左侧为模型回复和工具活动；SDK 路由作为独立过渡节点。</p>
      <div class="tm-stage-status" role="status" aria-live="polite">正在读取 Stage 与 Invocation 记录…</div>
      <div class="tm-stage-current-notice" data-stage-current-notice hidden role="status">
        <span data-stage-current-text></span>
        <button type="button" data-stage-switch-current>查看当前 Cycle</button>
      </div>
      <div data-stage-timeline class="tm-stage-timeline"></div>
    </div>'''

def script(ident: str) -> str:
    observer_id = json.dumps(ident)
    # HTML is text-only from JSON. No raw scientific prose is interpreted as HTML.
    return '''<script>(function(){
      "use strict";
      const observerId=TM_STAGE_OBSERVER;
      const roles={research_lead:"Research Lead",challenger:"Challenger",investigator:"Investigator",
        literature:"Literature",judge:"Judge",reporter:"Reporter",system:"SDK"};
      const esc=(v)=>String(v===null||v===undefined?"":v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
      const time=v=>{ const s=String(v||"");return esc(s.replace("T"," ").replace(/(?:Z|[+-]\\d\\d:\\d\\d)$/,"")); };
      let token=0;
      const params=()=>new URLSearchParams(location.search);
      async function request(mode,cycle,stage,cursor){
        const url=new URL("/api/run/"+observerId+"/conversation",location.origin);
        url.searchParams.set("mode",mode);
        if(cycle)url.searchParams.set("cycle",cycle);
        if(stage)url.searchParams.set("stage",stage);
        if(cursor!==undefined&&cursor!==null)url.searchParams.set("cursor",String(cursor));
        const r=await fetch(url,{cache:"no-store"});
        if(!r.ok)throw new Error("读取阶段失败 (HTTP "+r.status+")");
        return r.json();
      }
      function meta(stage){
        return '<div class="tm-stage-meta"><span>Invocation '+esc(stage.id)+'</span>'+
          '<span>Thread '+esc(stage.thread_id||"未记录")+'</span>'+
          '<span>Stage '+esc(stage.stage||"未记录")+'</span>'+
          '<span>State '+esc(stage.state||"unknown")+'</span>'+
          '<span>'+time(stage.start)+'</span></div>';
      }
      function continuation(stage){
        if(Number(stage.round||1)<=1)return "";
        const previous=String(stage.previous_stage_name||"");
        const current=String(stage.stage||"");
        // Different stages in the same Cycle are normal workflow progress,
        // not a Rework/Context Handoff based only on the Agent role.
        if(!stage.rework){
          if(current===previous && stage.context_mode==="same_thread")
            return '<div class="tm-stage-context tm-stage-context-brief">相同 Stage 再次调用 · '
              +'已确认复用原 Thread。未记录正式 Judge Rework 裁决。</div>';
          return "";
        }
        const origin='<div>此前执行：'+esc(previous||"未记录")+
          ' · '+esc(stage.previous_stage||"未记录")+'</div>';
        const mode=stage.context_mode==="same_thread"?
          '原 Thread 延续（已有线程标识证明）':
          stage.context_mode==="new_thread"?'新 Thread · 单独传递上下文须查看实际 Prompt':
          'Thread 继承关系未确认';
        const confirmed=stage.rework.confirmed_rework;
        return '<div class="tm-stage-context"><strong>'+ (confirmed?
          'Judge Rework · 待解决的科研问题':'Judge requested_resolution · Rework 未确认')+'</strong>'+
          '<p>'+esc(mode)+'</p>'+origin+
          '<div class="tm-stage-judge-request">'+esc(stage.rework.requested_resolution||'Judge 未保存详细 resolution')+'</div>'+
          '<small>可查看的历史消息不等于模型实际保留的上下文 Token。</small></div>';
      }
      // Keep the full retained event in the DOM; shorten only the display projection.
      function jsonViewer(content){
        let parsed;
        try{parsed=JSON.parse(content);}catch(_err){return "";}
        if(!parsed||typeof parsed!=="object")return "";
        const preview=JSON.stringify(parsed,(key,value)=>{
          if(typeof value==="string"&&value.length>180)
            return value.slice(0,140)+"…";
          if(Array.isArray(value)&&value.length>16)
            return [...value.slice(0,16),"…（另有 "+(value.length-16)+" 项）"];
          return value;
        },2);
        const full=JSON.stringify(parsed,null,2);
        return '<div class="tm-stage-json-viewer">'+
          '<div class="tm-stage-json-toolbar"><span>JSON · 格式化预览</span>'+
          '<button type="button" data-stage-json-toggle aria-expanded="false">全部</button></div>'+
          '<pre data-stage-json-preview>'+esc(preview)+'</pre>'+
          '<pre data-stage-json-full hidden>'+esc(full)+'</pre></div>';
      }
      function canvasUpdates(content){
        let value;
        try{value=JSON.parse(content);}catch(_err){return "";}
        if(!value||!Array.isArray(value.updates)||!value.updates.length||
           !value.updates.every(item=>item&&typeof item==="object"&&
               typeof item.node_id==="string"))return "";
        const cards=value.updates.map(item=>
          '<div class="tm-stage-canvas-change"><div><strong>'+esc(item.node_id)+'</strong>'+
          (item.frontier_status?'<span class="tm-stage-canvas-status">'+esc(item.frontier_status)+'</span>':'')+
          '</div>'+(item.reason?'<p>'+esc(item.reason)+'</p>':'')+
          '</div>').join('');
        const rest=Object.keys(value).filter(k=>k!=='updates').map(esc).join('、');
        return '<div class="tm-stage-canvas-changes"><div class="tm-stage-canvas-title">Canvas 节点维护 · '
          +value.updates.length+' 项更新</div>'+cards+
          (rest?'<small>还包含其他结构化字段：'+rest+'（见原始输出）</small>':'')+
          '<details class="tm-stage-raw"><summary>查看完整原始 JSON</summary>'+jsonViewer(content)+'</details></div>';
      }
      // Token accounting is retained in the source ledger and usage panels,
      // but its high-frequency per-Turn events are not conversation messages.
      function showConversationEvent(item){return item?.kind!=="TOKEN_USAGE";}
      function eventCard(item,density,stage){
        const kind=String(item.kind||"");
        if(kind==="TOKEN_USAGE")return "";
        const isPrompt=item.side==="prompt";
        const isSkill=kind==="SKILL_LOADED";
        const isTool=kind==="TOOL_ACTIVITY"||isSkill;
        const when=time(item.time);
        const meta=item.meta||{};
        // Message rendering must not crash the entire Stage reader when
        // a malformed/legacy event lacks stage context or a caller omits it.
        const stageMeta=(stage&&typeof stage==='object')?stage:{};
        const resolvedRole=stageMeta.role||meta.role||'';
        const context=[roles[resolvedRole]||resolvedRole||'未知 Agent',
          stageMeta.stage||meta.stage||'未知 Stage'].filter(Boolean).join(' · ');
        const turn=String(item.turn_id||'');
        const eventTitles={
          AGENT_STARTED:'Agent 调用开始',
          AGENT_COMPLETED:'Agent 调用结束（以 Invocation 最终状态为准）',
          LLM_TURN_STARTED:'模型 Turn 开始',
          LLM_TURN_COMPLETED:'模型 Turn 结束',
          LLM_RECONNECTING:'模型连接恢复中',
          LLM_FAILED:'模型调用失败'
        };
        const raw='<details class="tm-stage-raw"'+(density==="raw"?' open':'')+
          '><summary>原始事件字段 · Thread / Turn / 来源</summary>'+
          jsonViewer(JSON.stringify({event:kind,seq:item.seq,thread_id:item.thread_id,
            turn_id:item.turn_id,time:item.time,meta:meta}))+'</details>';
        if(Object.prototype.hasOwnProperty.call(eventTitles,kind) && kind!=="AGENT_COMPLETED" && !isSkill){
          let summary=eventTitles[kind];
          return '<div class="tm-stage-milestone"><strong>'+summary+'</strong>'+
            '<span>'+esc(context||meta.role||'当前 Stage')+'</span>'+
            (turn?'<span>Turn '+esc(turn.slice(0,12))+'…</span>':'')+
            '<time>'+when+'</time>'+raw+'</div>';
        }
        const agentName=roles[resolvedRole]||resolvedRole||'未知 Agent';
        const label=isPrompt?agentName+' · Prompt 输入':
          isSkill?String(meta.skill||meta.skill_name||meta.path||'未记录名称'):
          kind==="TOOL_ACTIVITY"?String(meta.tool||'tool'):
          kind==="AGENT_COMPLETED"?agentName+' · 结构化交付':
          agentName+' · '+kind;
        const cls=isPrompt?'tm-stage-prompt':
          isTool?'tm-stage-tool-message':'tm-stage-agent-message';
        const content=String(item.content||"");
        const structured=(kind==="LLM_MESSAGE"||kind==="AGENT_COMPLETED")?canvasUpdates(content):"";
        const jsonBody=jsonViewer(content);
        const isLong=content.length>1800;
        const body=structured || jsonBody || (isLong?
          '<div class="tm-stage-preview">'+esc(content.slice(0,700))+
          '…</div><details class="tm-stage-long"><summary>展开完整留存内容 · '+content.length+
          ' 字符</summary><pre>'+esc(content)+'</pre></details>':
          '<pre>'+esc(content||(kind==="LLM_MESSAGE"?
             "模型消息未记录文本正文": "此事件未提供文本正文；可查看原始字段"))+'</pre>');
        const coverage=item.content_truncated?
          '<div class="tm-stage-warning">本条正文超过接口上限，已裁剪；原始记录不在本气泡中完整提供。</div>':"";
        const detail=density==="normal"?"":raw;
        if(kind==="LLM_MESSAGE"){
          return '<article class="tm-stage-bubble tm-stage-agent-message">'+
            '<time class="tm-stage-reply-time">'+when+'</time>'+
            body+coverage+detail+'</article>';
        }
        if(isTool){
          const skillNote=isSkill?[meta.skill_path, meta.load_index!=null&&meta.load_index!==''?'load #'+meta.load_index:'',
            meta.first_load_in_turn?'first in turn':'',
            meta.detection?String(meta.detection).replace(/_/g,' '):''].filter(Boolean).join(' · '):'';
          const flat=(skillNote||content).replace(/\\s+/g,' ').trim();
          const brief=flat.length>160?flat.slice(0,159)+'…':flat;
          const extra=flat.length>160?
            '<details class="tm-stage-tool-note"><summary>展开完整调用</summary><pre>'+
            esc(skillNote||content)+'</pre></details>':'';
          return '<article class="tm-stage-bubble tm-stage-tool-message">'+
            '<div class="tm-stage-bubble-header"><strong>'+esc(label)+'</strong><small>'+when+
            '</small></div><pre>'+esc(brief||'未记录调用')+'</pre>'+extra+detail+'</article>';
        }
        return '<article class="tm-stage-bubble '+cls+'"><div class="tm-stage-bubble-header">'+
          '<strong>'+esc(label)+'</strong><small>'+when+'</small></div>'+
          body+coverage+detail+'</article>';
      }
      function toolDuration(items){
        const start=Date.parse(String(items[0]?.time||''));
        const end=Date.parse(String(items[items.length-1]?.time||''));
        if(!Number.isFinite(start)||!Number.isFinite(end)||end<start) return '用时未记录';
        const sec=Math.round((end-start)/1000);
        if(sec<1) return '用时 <1秒';
        if(sec<60) return '用时 '+sec+'秒';
        const min=Math.floor(sec/60), rem=sec%60;
        if(sec<3600) return '用时 '+min+'分'+(rem?rem+'秒':'');
        return '用时 '+Math.floor(sec/3600)+'小时'+Math.floor((sec%3600)/60)+'分';
      }
      function dialogueBlocks(items,density,stage){
        let html='', batch=[], batchKind='';
        const flush=()=>{
          if(!batch.length) return;
          const title=batchKind==='SKILL_LOADED'?'Skill 调用':'工具调用';
          const cards=batch.map(item=>'<div class="tm-stage-event-item" data-stage-event-seq="'+
            esc(item.seq)+'">'+eventCard(item,density,stage)+'</div>').join('');
          html+='<div class="tm-stage-tool-group"><details class="tm-stage-tool-fold"><summary>'+title+' · '+
            batch.length+' 次 · '+esc(toolDuration(batch))+'</summary>'+cards+'</details></div>';
          batch=[];
          batchKind='';
        };
        for(const item of items){
          const kind=String(item.kind||'');
          if(kind==='TOOL_ACTIVITY'||kind==='SKILL_LOADED'){
            if(batchKind&&batchKind!==kind) flush();
            batchKind=kind;
            batch.push(item);
          } else {
            flush();
            html+='<div class="tm-stage-event-item" data-stage-event-seq="'+esc(item.seq)+'">'+
              eventCard(item,density,stage)+'</div>';
          }
        }
        flush();
        return html;
      }
      function mount(root){
        if(!root||root.dataset.mounted==="1")return;
        root.dataset.mounted="1";
        const own=++token;
        const box=root.querySelector(".tm-stage-reader");
        if(!box)return;
        const timeline=box.querySelector("[data-stage-timeline]");
        box.addEventListener('click',event=>{
          const button=event.target.closest('[data-stage-json-toggle]');
          if(!button||!box.contains(button))return;
          const viewer=button.closest('.tm-stage-json-viewer');
          const preview=viewer?.querySelector('[data-stage-json-preview]');
          const full=viewer?.querySelector('[data-stage-json-full]');
          if(!preview||!full)return;
          const expand=button.getAttribute('aria-expanded')!=='true';
          preview.hidden=expand;
          full.hidden=!expand;
          button.setAttribute('aria-expanded',String(expand));
          button.textContent=expand?'收起':'全部';
        });
        const cycleSelect=box.querySelector("[data-stage-cycle]");
        const density=box.querySelector("[data-stage-density]");
        const roleFilter=box.querySelector("[data-stage-role]");
        const search=box.querySelector("[data-stage-search]");
        const status=box.querySelector(".tm-stage-status");
        const notice=box.querySelector("[data-stage-current-notice]");
        const noticeText=box.querySelector("[data-stage-current-text]");
        const currentButton=box.querySelector("[data-stage-switch-current]");
        let overview=null;
        let currentCycle="";
        let requestedStage=params().get('stage')||'';
        // Source identity travels through a new-tab URL, never a guessed
        // Cycle, guessed Invocation, or neighboring event in the timeline.
        let pendingSource=params().has('at')?{
          at:params().get('at')||'',
          kind:params().get('kind')||'',
          role:params().get('role')||'',
          hint:params().get('hint')||''
        }:null;
        let attemptedSource=false;
        function updateCurrentCycle(cycle){
          if(!cycle||!overview)return;
          currentCycle=cycle;
          const different=String(overview.cycle)!==String(currentCycle);
          notice.hidden=!different;
          if(different)
            noticeText.textContent='当前任务已进入 Cycle '+currentCycle+
              '，正在阅读历史 Cycle '+overview.cycle+'。';
        }
        window.tmStageConversationNotifyCycle=updateCurrentCycle;
        currentButton.addEventListener('click',()=>{
          if(!currentCycle)return;
          requestedStage='';
          pendingSource=null;
          const url=new URL(location.href);
          for(const key of ['at','kind','role','hint'])url.searchParams.delete(key);
          url.searchParams.set('tab','events');
          url.searchParams.set('cycle',currentCycle);
          url.searchParams.delete('stage');
          history.replaceState(null,'',url);
          fetchOverview(currentCycle);
        });
        roleFilter.innerHTML='<option value="all">全部 Agent</option>'+
          Object.entries(roles).filter(([k])=>k!=="system")
          .map(([key,label])=>'<option value="'+key+'">'+label+'</option>').join("");
        let jumper=null;
        function activeStageSection(){
          const sections=[...timeline.querySelectorAll('.tm-stage-section')].filter(section=>{
            const body=section.querySelector('.tm-stage-messages');
            return body&&!body.hidden&&section.dataset.stageLoaded==='1';
          });
          const mid=window.innerHeight/2;
          let best=null,bestDist=Infinity;
          for(const section of sections){
            if(typeof section.getBoundingClientRect!=='function') continue;
            const rect=section.getBoundingClientRect();
            if(rect.bottom<0||rect.top>window.innerHeight) continue;
            const dist=Math.abs((rect.top+rect.bottom)/2-mid);
            if(dist<bestDist){best=section;bestDist=dist;}
          }
          return best||sections[sections.length-1]||null;
        }
        function syncJumper(){
          if(!jumper) return;
          const section=activeStageSection();
          jumper.hidden=!section;
        }
        async function fillToEnd(section){
          for(let guard=0;guard<200 && section.querySelector('[data-stage-more]');guard++){
            const load=section.__stageLoad;
            if(typeof load!=='function') break;
            await load();
          }
        }
        function lastDialogueItem(section){
          const nodes=section.querySelectorAll('.tm-stage-event-item');
          return nodes.length?nodes[nodes.length-1]:section;
        }
        if(typeof document.createElement==='function' && typeof box.append==='function'){
          jumper=document.createElement('div');
          jumper.className='tm-stage-jump';
          jumper.hidden=true;
          jumper.innerHTML='<button type="button" data-stage-jump="start" aria-label="跳到本对话最前">↑</button>'+
            '<button type="button" data-stage-jump="end" aria-label="跳到本对话最后">↓</button>';
          box.append(jumper);
          jumper.addEventListener('click',async event=>{
            const button=event.target.closest('[data-stage-jump]');
            const section=activeStageSection();
            if(!button||!section) return;
            if(button.dataset.stageJump==='start'){
              const older=section.__stageLoadPrev;
              for(let guard=0;guard<200 && section.querySelector('[data-stage-prev]');guard++){
                if(typeof older!=='function') break;
                await older();
              }
              section.scrollIntoView({block:'start',behavior:'smooth'});
              status.textContent='已跳到本对话最早消息';
              return;
            }
            await fillToEnd(section);
            const last=lastDialogueItem(section);
            const fold=last.closest&&last.closest('details');
            if(fold) fold.open=true;
            last.scrollIntoView({block:'end',behavior:'instant'});
            status.textContent='已跳到本对话最新消息';
          });
          if(typeof window.addEventListener==='function')
            window.addEventListener('scroll',syncJumper,{passive:true});
        }
        async function showStage(stage,section,sourceTarget=null){
          const body=section.querySelector(".tm-stage-messages");
          if(section.dataset.stageLoaded==="1"){body.hidden=!body.hidden;syncJumper();return;}
          section.dataset.stageLoaded="1";
          body.hidden=false;
          body.textContent="加载对话中…";
          const firstCursor=(sourceTarget&&Number.isInteger(sourceTarget.stage_offset))?
            Math.floor(sourceTarget.stage_offset/45)*45:0;
          let items=[],cursor=firstCursor,start=firstCursor,pageObserver=null,prevObserver=null;
          function bindPager(){
            const more=body.querySelector("[data-stage-more]");
            const prev=body.querySelector("[data-stage-prev]");
            if(pageObserver){pageObserver.disconnect();pageObserver=null;}
            if(prevObserver){prevObserver.disconnect();prevObserver=null;}
            if(more){
              more.addEventListener("click",()=>load());
              if(typeof IntersectionObserver==='function'){
                pageObserver=new IntersectionObserver(entries=>{
                  if(entries.some(entry=>entry.isIntersecting)) load();
                },{root:null,rootMargin:'320px 0px'});
                pageObserver.observe(more);
              }
            }
            if(prev){
              prev.addEventListener("click",()=>loadPrev());
              if(typeof IntersectionObserver==='function'){
                prevObserver=new IntersectionObserver(entries=>{
                  if(entries.some(entry=>entry.isIntersecting)) loadPrev();
                },{root:null,rootMargin:'320px 0px'});
                prevObserver.observe(prev);
              }
            }
          }
          function paint(data,appendFrom){
            const mode=density.value;
            // The event renderer needs the enclosing Invocation/Stage for
            // Agent name, stage name and role-specific event semantics.
            const visible=items.filter(showConversationEvent);
            const omitted=items.length-visible.length;
            const ahead=start>0;
            const behind=cursor!==null&&cursor!==undefined;
            const statusLine='<div class="tm-stage-coverage">已加载 '+items.length+' / '+data.total+
              ' 条原始事件 · 显示 '+visible.length+' 条'+
              (omitted?' · 已省略 '+omitted+' 条 Token 用量记录':'')+
              (ahead?' · 前面还有 '+start+' 条':'')+
              (behind?' · 仍有后续记录':(ahead?' · 后面已到头':' · 当前 Stage 已加载完毕'))+
              (data.warnings?.length?' · 源文件存在读取限制':' · 源读取无截断告警')+'</div>';
            const dialogue=Number.isInteger(appendFrom)?body.querySelector('.tm-stage-dialogue'):null;
            if(dialogue&&typeof dialogue.insertAdjacentHTML==='function'){
              const added=items.slice(appendFrom).filter(showConversationEvent);
              const chunk=dialogueBlocks(added,mode,stage);
              if(chunk)dialogue.insertAdjacentHTML('beforeend',chunk);
              const coverage=body.querySelector('.tm-stage-coverage');
              if(coverage)coverage.outerHTML=statusLine;
              const oldMore=body.querySelector('[data-stage-more]');
              if(oldMore)oldMore.remove();
              if(behind)dialogue.insertAdjacentHTML('afterend',
                '<button type="button" data-stage-more>滚动到底自动加载下一批（每次 45 条）</button>');
              bindPager();
              return;
            }
            const content=dialogueBlocks(visible,mode,stage);
            body.innerHTML=statusLine+
              (ahead?'<button type="button" data-stage-prev>滚动到顶自动加载更早一批（每次 45 条）</button>':'')+
              '<div class="tm-stage-dialogue">'+(content||"<p>该 Stage 没有留存可读取消息。</p>")+'</div>'+
              (behind?'<button type="button" data-stage-more>滚动到底自动加载下一批（每次 45 条）</button>':'')+
              (data.warnings?.length?'<div class="tm-stage-warning">'+data.warnings.map(esc).join(" · ")+'</div>':'');
            bindPager();
          }
          function rememberWindow(){
            section.__atEnd=cursor===null||cursor===undefined;
            section.__nextOffset=start+items.length;
          }
          const load=()=>{
            if(section.__stagePending) return section.__stagePending;
            section.__stagePending=(async()=>{
            const resume=section.__tailResume===true;
            section.__tailResume=false;
            const requestCursor=resume?section.__nextOffset:cursor;
            const data=await request("stage",overview.cycle,stage.id,requestCursor);
            if(own!==token)return;
            const previous=items.length;
            items=items.concat(data.messages||[]);
            cursor=data.next_cursor;
            rememberWindow();
            paint(data, resume&&previous>0?previous:null);
            syncJumper();
            if(sourceTarget&&!resume){
              const original=[...body.querySelectorAll('.tm-stage-event-item')].find(
                el=>el.dataset.stageEventSeq===String(sourceTarget.target));
              if(original){
                const fold=original.closest('details');
                if(fold) fold.open=true;
                original.classList.add('tm-stage-jump-target');
                section.scrollIntoView({block:'start',behavior:'instant'});
                original.scrollIntoView({block:'center',behavior:'instant'});
                status.textContent='已精确定位 Cycle '+overview.cycle+
                  ' · '+(roles[stage.role]||stage.role)+' · '+stage.stage+
                  ' · Event #'+sourceTarget.target;
              }else if(items.some(item=>String(item.seq)===String(sourceTarget.target)&&
                                   !showConversationEvent(item))){
                status.textContent='已定位原始 Event #'+sourceTarget.target+
                  '；该 Token 用量更新已从对话展示中隐藏，账本记录仍保留。';
              }else{
                status.textContent='未能在当前 Stage 的这一批消息中找到指定 Event，未自动选择其他记录。';
              }
            }
            })().finally(()=>{section.__stagePending=null;});
            return section.__stagePending;
          };
          const loadPrev=()=>{
            if(start<=0) return Promise.resolve();
            if(section.__stagePrevPending) return section.__stagePrevPending;
            section.__stagePrevPending=(async()=>{
              const prev=Math.max(0,start-45);
              const data=await request("stage",overview.cycle,stage.id,prev);
              if(own!==token)return;
              const older=(data.messages||[]).slice(0,start-prev);
              items=older.concat(items);
              start=prev;
              rememberWindow();
              paint(data);
            })().finally(()=>{section.__stagePrevPending=null;});
            return section.__stagePrevPending;
          };
          section.__stageLoad=load;
          section.__stageLoadPrev=loadPrev;
          try{await load();}
          catch(error){body.textContent="无法读取 Stage 对话："+error.message;section.dataset.stageLoaded="0";}
        }
        function syncStageLabels(section,stage){
          const count=section.querySelector('[data-stage-count]');
          if(count)count.textContent=stage.count+' events';
          const state=section.querySelector('[data-stage-state]');
          if(state)state.textContent=(stage.rework?'Rework · ':
            (Number(stage.round)>1?'第 '+stage.round+' 次 Agent 调用 · ':''))+(stage.state||'');
        }
        function draw(options){
          if(!overview)return;
          const reuse=options&&options.reuse;
          const savedSdk=options&&options.savedSdk;
          const live=!!(options&&options.live);
          const expanded=new Set([...timeline.querySelectorAll('.tm-stage-heading[aria-expanded="true"]')]
            .map(button=>button.closest('.tm-stage-section')?.dataset.stageId));
          timeline.replaceChildren();
          let count=0;
          const needle=search.value.toLowerCase().trim();
          const role=roleFilter.value;
          const items=[...overview.stages.map(stage=>({type:"stage",time:stage.start,stage})),
            ...overview.transitions.map((sdk,i)=>({type:"sdk",time:sdk.time,sdk,i}))];
          items.sort((a,b)=>{
            const t=String(a.time||"").localeCompare(String(b.time||""));
            return t||((a.type==="sdk"?0:1)-(b.type==="sdk"?0:1));
          });
          items.forEach(item=>{
            if(item.type==="sdk"){
              if(role!=="all"||needle)return;
              const keptSdk=savedSdk&&savedSdk.get(String(item.sdk.seq));
              if(keptSdk){timeline.append(keptSdk);return;}
              const node=document.createElement("section");
              node.className="tm-stage-sdk-node";
              node.dataset.sdkSeq=String(item.sdk.seq);
              node.innerHTML='<span>SDK · '+esc(item.sdk.kind)+'</span><time>'+time(item.sdk.time)+'</time>'+
                '<details><summary>查看 SDK 决策与原始证据</summary><pre>按需读取完整事件…</pre></details>';
              const detail=node.querySelector('details');
              detail.addEventListener('toggle',async()=>{
                if(!detail.open||detail.dataset.loaded)return;
                detail.dataset.loaded='1';
                const pre=detail.querySelector('pre');
                try{
                  const data=await request('sdk',overview.cycle,'',item.sdk.seq);
                  const event=String(data.event||'No data');
                  const formatted=jsonViewer(event);
                  if(formatted)pre.outerHTML=formatted;
                  else pre.textContent=event;
                }catch(error){pre.textContent='无法读取 SDK 原始记录：'+error.message;}
              });
              timeline.append(node);return;
            }
            const stage=item.stage;
            const searchable=[stage.stage,stage.role,stage.state,stage.id].join(" ").toLowerCase();
            if(role!=="all"&&role!==stage.role)return;
            if(needle&&!searchable.includes(needle))return;
            count++;
            const kept=reuse&&reuse.get(stage.id);
            if(kept){
              syncStageLabels(kept,stage);
              const round=kept.querySelector('.tm-stage-round');
              if(round)round.textContent='Stage '+count;
              timeline.append(kept);
              return;
            }
            const section=document.createElement("section");
            section.className="tm-stage-section tm-role-"+stage.role;
            section.dataset.stageId=stage.id;
            section.innerHTML='<button class="tm-stage-heading" type="button" aria-expanded="false">'+
              '<span class="tm-stage-round">Stage '+count+'</span><span class="tm-role-chip tm-role-'+stage.role+'">'+esc(roles[stage.role]||stage.role)+'</span>'+
              '<strong>'+esc(stage.stage)+'</strong><span class="tm-stage-iteration" data-stage-state>'+
              (stage.rework?'Rework · ':(stage.round>1?'第 '+stage.round+' 次 Agent 调用 · ':''))+esc(stage.state)+'</span>'+
              '<span data-stage-count>'+stage.count+' events</span><span aria-hidden="true">⌄</span></button>'+
              meta(stage)+continuation(stage)+
              '<div class="tm-stage-messages" hidden></div>';
            section.querySelector(".tm-stage-heading").addEventListener("click",async()=>{
              const button=section.querySelector(".tm-stage-heading");
              requestedStage=stage.id;
              const url=new URL(location.href);
              url.searchParams.set('tab','events');
              url.searchParams.set('cycle',overview.cycle);
              url.searchParams.set('stage',stage.id);
              history.replaceState(null,'',url);
              await showStage(stage,section);
              button.setAttribute("aria-expanded",String(!section.querySelector(".tm-stage-messages").hidden));
            });
            timeline.append(section);
            if(!live&&expanded.has(stage.id))section.querySelector('.tm-stage-heading').click();
          });
          if(!live && count && !pendingSource && params().get('jump')!=='latest' && expanded.size===0 && !search.value.trim() && roleFilter.value==='all'){
            const exact=[...timeline.querySelectorAll('.tm-stage-section')].find(
              section=>section.dataset.stageId===requestedStage);
            (exact||timeline.querySelector('.tm-stage-section'))?.querySelector('.tm-stage-heading')?.click();
          }
          if(!count)timeline.innerHTML='<p class="tm-stage-empty">没有匹配的 Stage。</p>';
          status.textContent=overview.cycle+" · "+count+" 个 Stage · "+overview.transitions.length+" 个 SDK 过渡 · "+
            (overview.warnings.length?overview.warnings.join("；"):"源记录已读取")+" · 按需展开对话";
        }
        async function followEventLink(){
          if(!pendingSource||attemptedSource||!overview)return;
          attemptedSource=true;
          const url=new URL("/api/run/"+observerId+"/conversation",location.origin);
          url.searchParams.set("mode","recent");
          for(const [name,value] of Object.entries(pendingSource))
            url.searchParams.set(name,value);
          try{
            status.textContent="正在从保存的事件标识定位原 Stage…";
            const response=await fetch(url,{cache:"no-store"});
            if(!response.ok)throw new Error("事件查询失败 (HTTP "+response.status+")");
            const result=await response.json();
            if(!result.found){status.textContent=result.reason||"无法确定原始事件";return;}
            if(result.cycle!==overview.cycle)await fetchOverview(result.cycle);
            requestedStage=result.stage_id||"";
            if(result.stage_id){
              const section=[...timeline.querySelectorAll(".tm-stage-section")].find(
                el=>el.dataset.stageId===result.stage_id);
              const stage=overview.stages.find(item=>item.id===result.stage_id);
              if(!section||!stage){
                status.textContent="已找到原 Event，但该 Invocation 没有可展开的 Stage。";
                return;
              }
              const button=section.querySelector(".tm-stage-heading");
              button.setAttribute("aria-expanded","true");
              await showStage(stage,section,result);
            }else{
              // Some SDK/runtime transitions have no Agent Invocation. They
              // still belong on the original Events reader, never a false Stage.
              const event=result.events.find(v=>v.index===result.target);
              if(!event){status.textContent="无法找到该事件的原始记录。";return;}
              const section=document.createElement("section");
              section.className="tm-stage-sdk-node tm-stage-jump-target";
              const heading=document.createElement("strong");
              heading.textContent="已定位独立运行事件 · "+event.kind;
              const detail=document.createElement("pre");
              detail.textContent=event.content;
              section.append(heading,detail);
              timeline.prepend(section);
              section.scrollIntoView({block:"center"});
              status.textContent="已定位 Cycle "+result.cycle+" · Event #"+result.target+
                " · 非 Agent Invocation 事件";
            }
          }catch(error){status.textContent="无法定位原始 Event："+String(error.message||error);}
        }
        async function fetchOverview(cycle){
          timeline.textContent="正在构建 Stage 时间轴…";
          try{
            const data=await request("index",cycle);
            if(own!==token)return;
            overview=data;
            currentCycle=box.closest('.tm-tui-screen')?.dataset.tuiCycle||currentCycle||data.cycle;
            updateCurrentCycle(currentCycle);
            cycleSelect.innerHTML=data.cycles.map(v=>'<option value="'+esc(v)+'"'+(v===data.cycle?' selected':'')+'>'+esc(v)+'</option>').join("");
            draw();
            await followEventLink();
            if(params().get('jump')==='latest'){
              const sections=[...timeline.querySelectorAll('.tm-stage-section')];
              const section=sections[sections.length-1];
              const stage=section&&overview.stages.find(item=>item.id===section.dataset.stageId);
              if(section&&stage){
                section.querySelector('.tm-stage-heading')?.setAttribute('aria-expanded','true');
                await showStage(stage,section);
                await fillToEnd(section);
                const last=lastDialogueItem(section);
                const fold=last.closest&&last.closest('details');
                if(fold) fold.open=true;
                last.scrollIntoView({block:'end',behavior:'instant'});
                status.textContent='已跳到本对话最新消息';
              }
            }
          }catch(error){timeline.textContent="Stage Conversation 无法读取："+error.message;}
        }
        let liveBusy=false;
        function viewingCurrentCycle(){
          return !!overview && String(overview.cycle)===String(currentCycle||overview.cycle);
        }
        function followingTail(){
          const last=timeline.lastElementChild;
          if(!last||typeof last.getBoundingClientRect!=='function')return false;
          const rect=last.getBoundingClientRect();
          if(!rect||typeof rect.bottom!=='number')return false;
          return rect.bottom<=(window.innerHeight||0)+180;
        }
        async function refreshLive(){
          // The outer Run poll keeps this reader mounted. Pull new Invocations
          // and, once a Stage is already read through its previous end, append
          // only the new events instead of rebuilding the open transcript.
          if(liveBusy||!overview)return;
          if(typeof document!=='undefined'&&document.hidden)return;
          if(pendingSource&&!attemptedSource)return;
          if(!viewingCurrentCycle())return;
          liveBusy=true;
          try{
            const data=await request('index',overview.cycle);
            if(own!==token||!data||String(data.cycle)!==String(overview.cycle))return;
            const sameStages=(overview.stages||[]).map(stage=>stage.id).join('\\n')===
              (data.stages||[]).map(stage=>stage.id).join('\\n');
            const sameSdk=(overview.transitions||[]).map(item=>String(item.seq)).join('\\n')===
              (data.transitions||[]).map(item=>String(item.seq)).join('\\n');
            const follow=followingTail();
            if(sameStages&&sameSdk){
              overview=data;
              for(const stage of data.stages){
                const section=[...timeline.querySelectorAll('.tm-stage-section')]
                  .find(el=>el.dataset.stageId===stage.id);
                if(section)syncStageLabels(section,stage);
              }
            }else{
              const reuse=new Map([...timeline.querySelectorAll('.tm-stage-section')]
                .map(el=>[el.dataset.stageId,el]));
              const savedSdk=new Map([...timeline.querySelectorAll('[data-sdk-seq]')]
                .map(el=>[el.dataset.sdkSeq,el]));
              overview=data;
              draw({reuse,savedSdk,live:true});
            }
            const loaded=[...timeline.querySelectorAll('.tm-stage-section')].filter(section=>{
              const messages=section.querySelector('.tm-stage-messages');
              return messages&&!messages.hidden&&section.dataset.stageLoaded==='1'&&
                typeof section.__stageLoad==='function';
            });
            const latest=loaded[loaded.length-1];
            // A deep link opens the matching page, not the live tail. Catch up
            // forward so newer events appear without a manual refresh.
            if(latest&&latest.__atEnd!==true)await fillToEnd(latest);
            const open=loaded.filter(section=>section.__atEnd===true);
            for(const section of open){
              const stage=overview.stages.find(item=>item.id===section.dataset.stageId);
              const next=Number(section.__nextOffset);
              if(!stage||!Number.isFinite(next)||stage.count<=next)continue;
              section.__tailResume=true;
              await section.__stageLoad();
              if(follow){
                await fillToEnd(section);
                const last=lastDialogueItem(section);
                if(last&&typeof last.scrollIntoView==='function')
                  last.scrollIntoView({block:'end',behavior:'instant'});
              }
            }
          }catch(_err){/* Keep the open transcript when a poll fails. */}
          finally{liveBusy=false;}
        }
        window.tmStageConversationRefresh=refreshLive;
        roleFilter.addEventListener("change",draw);
        search.addEventListener("input",draw);
        density.addEventListener("change",()=>{
          if(overview)draw(); // Reset loaded cards to apply the requested audit density.
        });
        box.querySelector('[data-stage-refresh]').addEventListener('click',()=>{
          if(overview)refreshLive();
          else fetchOverview('');
        });
        cycleSelect.addEventListener("change",()=>{
          requestedStage='';
          pendingSource=null;
          const url=new URL(location.href);
          for(const key of ['at','kind','role','hint'])url.searchParams.delete(key);
          url.searchParams.set('tab','events');
          url.searchParams.set('cycle',cycleSelect.value);
          url.searchParams.delete('stage');
          history.replaceState(null,'',url);
          fetchOverview(cycleSelect.value);
        });
        if(typeof setInterval==='function'){
          if(window.__tmStageLive)clearInterval(window.__tmStageLive);
          window.__tmStageLive=setInterval(()=>{if(own===token)refreshLive();},8000);
        }
        fetchOverview(params().get('cycle')||'');
      }
      window.tmStageConversationMount=mount;
      mount(document.querySelector('#tm-run-live .tm-tui-screen[data-tui-screen="events"]'));
    })();</script>'''.replace("TM_STAGE_OBSERVER",observer_id)
