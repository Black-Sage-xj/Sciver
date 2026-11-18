"""Read-only Literature progress and inspectable work in the existing Chat shell."""
from __future__ import annotations

import html
import re


def literature_panel(session: dict) -> str:
    ident = str(session.get('id') or '')
    if session.get('mode') not in {'preparation', 'project', 'frontier'} or not re.fullmatch(r'[a-f0-9]{32}', ident):
        return ''
    return r'''<aside class="tm-chat-literature" data-chat-literature-session="''' + html.escape(ident, quote=True) + r'''"
      aria-label="Literature 文献调研" hidden>
      <button type="button" class="tm-chat-literature-open" data-chat-literature-open aria-label="文献调研"
        aria-haspopup="dialog" aria-expanded="false" aria-controls="tm-literature-detail" aria-describedby="tm-literature-summary">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 7v14m0-14C9 4 5 4 2 5v14c3-1 7-1 10 2m0-14c3-3 7-3 10-2v14c-3-1-7-1-10 2" /></svg>
        <span class="tm-chat-literature-text" aria-hidden="true">文献</span>
        <span class="tm-chat-literature-dot" aria-hidden="true"></span>
      </button>
      <strong class="tm-chat-literature-status" data-chat-literature-label role="status" aria-live="polite">Literature</strong>
      <span id="tm-literature-summary" hidden><span data-chat-literature-query></span> · <span data-chat-literature-counts></span></span>
      <dialog id="tm-literature-detail" class="tm-literature-detail" data-chat-literature-dialog aria-labelledby="tm-literature-title">
        <header class="tm-literature-detail-head"><h2 id="tm-literature-title">文献调研详情</h2>
          <button type="button" data-chat-literature-close aria-label="关闭文献调研详情">×</button></header>
        <div class="tm-literature-detail-body">
          <label class="tm-literature-jobs" data-chat-literature-jobs-label hidden>调研记录 <select data-chat-literature-jobs aria-label="选择调研记录"></select></label>
          <p class="tm-literature-notice" data-chat-literature-notice role="status" aria-live="polite"></p>
          <div data-chat-literature-detail-content hidden>
            <section><h3>调研问题</h3><p data-chat-literature-detail-query></p><ol class="tm-literature-activity" data-chat-literature-query-plan></ol>
              <div data-chat-literature-scope-wrap hidden><h3>关注范围</h3><p data-chat-literature-scope></p></div></section>
            <section><h3 data-chat-literature-detail-status>调研进展</h3><p data-chat-literature-progress></p>
              <p class="tm-literature-muted" data-chat-literature-updated></p>
              <ol class="tm-literature-activity" data-chat-literature-activity></ol>
              <p data-chat-literature-remaining hidden></p></section>
            <section data-chat-literature-landscape-wrap hidden><h3 data-chat-literature-landscape-title>阶段梳理</h3>
              <p class="tm-literature-muted" data-chat-literature-landscape-note></p>
              <ol class="tm-literature-activity" data-chat-literature-landscape-routes></ol></section>
            <section><h3 data-chat-literature-sources-title>候选文献</h3>
              <p class="tm-literature-muted">优先显示已选择阅读和领域代表文献；正文可用与 Agent 已完成阅读分别记录。</p>
              <ul class="tm-literature-sources" data-chat-literature-sources></ul>
              <details data-chat-literature-raw-wrap hidden><summary data-chat-literature-raw-summary>其他原始检索候选 · 待筛选</summary>
                <p class="tm-literature-muted">这些记录尚未入选本轮阅读，可能包含不相关结果。</p>
                <ul class="tm-literature-sources" data-chat-literature-raw-sources></ul></details></section>
            <section><h3>调研报告</h3><a data-chat-literature-report target="_blank" rel="noopener noreferrer" hidden>查看报告</a>
              <p class="tm-literature-muted" data-chat-literature-no-report>报告尚未生成，可先查看上面的文献与阅读进展。</p></section>
          </div>
        </div>
      </dialog>
    </aside>
    <style>
    .tm-chat-literature { display:flex; flex:0 0 auto; margin:0; min-width:0; }
    .tm-chat-literature[hidden] { display:none; }
    #tm-research-chat-app .tm-chat-literature-open { display:inline-flex; align-items:center; justify-content:center; gap:6px; box-sizing:border-box;
      height:29px; padding:5px 7px; border:0; border-radius:7px; cursor:pointer; white-space:nowrap;
      background:transparent; color:var(--chat-muted,var(--tm-muted,#a1a1aa)); font:inherit; font-size:11px; }
    #tm-research-chat-app .tm-chat-literature-open:hover { background:var(--sc-chat-hover,#ffffff0d); color:var(--chat-text,var(--tm-text,#e4e4e7)); }
    .tm-chat-literature-dot { width:5px; height:5px; border-radius:50%; background:currentColor; opacity:.55; }
    .tm-chat-literature[data-state="running"] .tm-chat-literature-dot { background:var(--chat-effort,#b6a1f5); opacity:1; }
    .tm-chat-literature-status { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
    .tm-literature-detail { box-sizing:border-box; width:min(740px,calc(100vw - 28px)); max-width:none; max-height:84dvh; padding:0;
      border:1px solid var(--chat-line,var(--tm-border,#41414a)); border-radius:16px;
      background:var(--chat-bg,var(--tm-panel,#18181c)); color:var(--chat-text,var(--tm-text,#e4e4e7)); box-shadow:0 24px 80px #0005; }
    .tm-literature-detail::backdrop { background:#0007; }
    .tm-literature-detail[open] { display:flex; flex-direction:column; }
    .tm-literature-detail-head { display:flex; flex:0 0 auto; align-items:center; justify-content:space-between; gap:16px;
      padding:16px 20px; border-bottom:1px solid var(--chat-line,var(--tm-border,#41414a)); }
    .tm-literature-detail h2 { margin:0; color:inherit; font-size:16px; }
    .tm-literature-detail-head button { width:32px; height:32px; padding:0; border:0; border-radius:8px; background:transparent; color:inherit; font:24px/1 system-ui; cursor:pointer; }
    .tm-literature-detail-body { padding:4px 20px 20px; min-height:0; overflow:auto; overscroll-behavior:contain; font-size:13px; line-height:1.7; }
    .tm-literature-detail section { padding-top:16px; }
    .tm-literature-detail h3 { margin:0 0 6px; color:inherit; font-size:13px; font-weight:650; }
    .tm-literature-detail p { margin:6px 0; white-space:pre-wrap; overflow-wrap:anywhere; }
    .tm-literature-detail a { color:var(--chat-effort,var(--tm-accent,#b6a1f5)); overflow-wrap:anywhere; }
    .tm-literature-muted,.tm-literature-notice { color:var(--chat-muted,var(--tm-muted,#a1a1aa)); font-size:12px; }
    .tm-literature-jobs { display:flex; gap:12px; align-items:center; padding-top:14px; }
    .tm-literature-jobs select { width:100%; min-width:0; border:1px solid var(--chat-line,#41414a); border-radius:7px;
      padding:6px 8px; background:var(--chat-panel,#18181c); color:inherit; font:inherit; }
    .tm-literature-activity { margin:10px 0 0; padding-left:20px; }
    .tm-literature-activity li { margin:4px 0; }
    .tm-literature-sources { list-style:none; margin:8px 0 0; padding:0; }
    .tm-literature-sources li { padding:10px 0; border-top:1px solid var(--chat-line,var(--tm-border,#41414a)); overflow-wrap:anywhere; }
    .tm-literature-sources li>:first-child { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; overflow:hidden; }
    .tm-literature-source-meta { display:block; margin-top:3px; color:var(--chat-muted,var(--tm-muted,#a1a1aa)); font-size:12px; }
    .tm-literature-detail details summary { cursor:pointer; margin-top:12px; color:var(--chat-muted,var(--tm-muted,#a1a1aa)); }
    .tm-chat-literature :is(button,select,a):focus-visible { outline:2px solid var(--chat-effort,#b6a1f5); outline-offset:3px; }
    @media(max-width:540px) { .tm-chat-literature-text { display:none; } .tm-literature-detail-head { padding:13px 16px; }
      .tm-literature-detail-body { padding:4px 16px 16px; } }
    </style><script>(function(){
      const panel=document.querySelector('[data-chat-literature-session]');
      if(!panel)return;
      const root=panel.closest('.tm-research-chat');
      const session=panel.dataset.chatLiteratureSession;
      const get=name=>panel.querySelector('[data-chat-literature-'+name+']');
      const label=get('label'),query=get('query'),counts=get('counts'),dialog=get('dialog'),opener=get('open');
      const selector=get('jobs'),notice=get('notice'),content=get('detail-content');
      const labels={registered:'等待启动',running:'正在调研',completed:'已交付报告',paused:'已暂停',failed:'执行失败',incomplete:'交付记录不完整',interrupted:'已中断，可继续原调研'};
      const workLabels={running:'进行中',completed:'已完成',failed:'未完成',pending:'等待中',interrupted:'已中断'};
      const accessLabels={metadata:'题录可用',abstract:'摘要可读',full_text:'正文可读'};
      const stopLabels={cancelled:'调研已停止，已有进展保留。',round_budget_exhausted:'本轮调研已结束，仍有问题待继续调研。',source_budget_exhausted:'本轮阅读已结束，仍有文献待继续调研。',source_access_unverified:'部分来源仍待核实。'};
      let pending=false,controller=null,jobs=[],selected='',detailController=null,detailVersion=0,detailLoading=false,optionsSignature='';
      function present(){
        const slot=root?.querySelector('[data-chat-literature-slot]');
        if(slot&&panel.parentElement!==slot)slot.append(panel);
        panel.hidden=!slot||!jobs.length;
      }
      const layout=new MutationObserver(present);
      if(root)layout.observe(root,{childList:true,subtree:true});
      const valid=id=>/^[a-f0-9]{32}$/.test(String(id||''));
      const summary=job=>{const access=job.source_access_counts||{};return '候选 '+Number(job.source_count||0)+' 篇 · 可读正文 '+Number(access.full_text||0)
        +' 篇 · Agent 已完成 '+Number(job.agent_turns||0)+' 次阅读与综合';};
      function reportLink(job){
        const report=get('report'),allowed=valid(job.job_id)&&job.report_available;
        report.hidden=!allowed;get('no-report').hidden=!!allowed;
        if(allowed)report.href='/api/research-chat/'+session+'/literature/'+job.job_id+'/report';else report.removeAttribute('href');
        report.textContent=job.status==='completed'?'查看完整报告':'查看阶段报告';
      }
      function render(job){
        get('detail-query').textContent=String(job.query||'');
        const plan=get('query-plan');plan.replaceChildren();
        (Array.isArray(job.search_queries)?job.search_queries:[]).forEach(item=>{const li=document.createElement('li');li.textContent=String(item.query||'')+(item.purpose?' · '+item.purpose:'');plan.append(li);});
        get('scope').textContent=String(job.scope||'');get('scope-wrap').hidden=!job.scope;
        get('detail-status').textContent='调研进展 · '+(labels[job.status]||'状态待核实');
        const activity=Array.isArray(job.activity)?job.activity:[];
        const active=activity.find(item=>item.status==='running');
        let progress=summary(job);
        if(job.status==='running')progress+='。'+(active?'正在'+active.label+'。':job.literature_agent_invoked?'正在获取与整理下一步文献材料。':Number(job.source_count||0)?'已找到候选文献，正在准备 Agent 阅读与综合。':'正在检索候选文献。');
        else if(stopLabels[job.stop_reason])progress+='。'+stopLabels[job.stop_reason];
        if(job.access_revalidated)progress+='。来源核验记录已根据保留材料更新，请以当前报告为准；历史回复保留当时状态。';
        get('progress').textContent=progress;
        const updated=new Date(job.updated_at);get('updated').textContent=Number.isNaN(updated.getTime())?'':'更新于 '+updated.toLocaleString('zh-CN');
        const activityNode=get('activity');activityNode.replaceChildren();
        activity.forEach(item=>{const li=document.createElement('li');li.textContent=String(item.label||'文献分析')+' · '+(workLabels[item.status]||'等待中');activityNode.append(li);});
        if(!activity.length){const li=document.createElement('li');li.textContent=job.literature_agent_invoked?'Agent 暂无已记录的阅读结果。':'Agent 尚未开始阅读与综合。';activityNode.append(li);}
        const remaining=get('remaining');remaining.hidden=!job.remaining_scientific_question;
        remaining.textContent=job.remaining_scientific_question?'待继续核实：'+job.remaining_scientific_question:'';
        const overview=Array.isArray(job.landscape_routes)?job.landscape_routes:[];
        get('landscape-wrap').hidden=!overview.length;
        get('landscape-title').textContent=job.status==='completed'?'研究脉络':'阶段梳理';
        get('landscape-note').textContent=job.status==='running'?'正在核对原文，以下为阶段结果。':job.status==='completed'?'具体材料与适用边界见调研报告。':'以下为已保存的阶段结果，原文核对尚未完成。';
        const overviewNode=get('landscape-routes');overviewNode.replaceChildren();
        overview.forEach(route=>{const li=document.createElement('li'),name=document.createElement('strong'),focus=document.createElement('p');
          name.textContent=String(route.name||'研究方向');focus.textContent=String(route.focus||'');li.append(name,focus);
          (Array.isArray(route.citations)?route.citations:[]).forEach(citation=>{try{const url=new URL(citation.url);if(url.protocol!=='https:'||url.hostname!=='doi.org'||url.username||url.password)return;
            const link=document.createElement('a');link.href=url.href;link.textContent=String(citation.title||citation.doi||'代表来源');link.target='_blank';link.rel='noopener noreferrer';li.append(link,document.createTextNode(' '));}catch(error){}});
          overviewNode.append(li);});
        const sources=Array.isArray(job.sources)?job.sources:[];
        const focused=sources.filter(source=>source.selection_status&&source.selection_status!=='retrieval_candidate');
        const raw=sources.filter(source=>!source.selection_status||source.selection_status==='retrieval_candidate');
        const primary=focused.length?focused:raw.slice(0,10),otherCandidates=focused.length?raw:raw.slice(10);
        get('sources-title').textContent=(focused.length?'已选择与领域代表文献 · '+focused.length+' 篇':'原始检索候选 · 待筛选')+'（检索候选共 '+Number(job.source_count||0)+' 篇）';
        const sourcesNode=get('sources'),rawNode=get('raw-sources');sourcesNode.replaceChildren();rawNode.replaceChildren();
        function sourceItem(source){const li=document.createElement('li');let title=document.createElement('span');
          try{const url=new URL(source.url);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password){title=document.createElement('a');title.href=url.href;title.target='_blank';title.rel='noopener noreferrer';}}catch(error){}
          title.textContent=String(source.title||'未命名文献');title.title=title.textContent;li.append(title);
          const category={reviewed:'已选读',selected:'已选择阅读',representative:'领域代表文献',retrieval_candidate:'原始检索候选 · 待筛选'}[source.selection_status]||'原始检索候选 · 待筛选';
          const meta=document.createElement('span');meta.className='tm-literature-source-meta';meta.textContent=category+' · '+(accessLabels[source.reading_level]||'题录可用')+' · '+(source.agent_reviewed?'Agent 已完成阅读与核对（'+(accessLabels[source.reading_level]||'题录')+'）':'Agent 尚未完成单篇阅读')+(source.doi?' · DOI '+source.doi:'');li.append(meta);return li;}
        primary.forEach(source=>sourcesNode.append(sourceItem(source)));otherCandidates.forEach(source=>rawNode.append(sourceItem(source)));
        get('raw-wrap').hidden=!otherCandidates.length;
        get('raw-summary').textContent='其他原始检索候选 · 待筛选（本页 '+otherCandidates.length+' 篇）';
        if(!sources.length){const li=document.createElement('li');li.textContent='候选文献尚未返回。';sourcesNode.append(li);}
        reportLink(job);content.hidden=false;
      }
      async function loadDetail(){
        if(!dialog.open||!valid(selected)||detailLoading)return;
        const id=selected,version=++detailVersion;
        detailLoading=true;detailController=new AbortController();const current=detailController;
        const timeout=setTimeout(()=>current.abort(),12000);
        try{
          const response=await fetch('/api/research-chat/'+session+'/literature/'+id,{credentials:'same-origin',cache:'no-store',signal:current.signal});
          const data=await response.json();
          if(!response.ok||!data.ok||!data.job||data.job.job_id!==id)throw new Error('unavailable');
          if(version!==detailVersion||id!==selected||!dialog.open)return;
          render(data.job);notice.textContent='';
        }catch(error){if(version===detailVersion&&dialog.open)notice.textContent=content.hidden?'进展暂时无法读取，请稍后重新打开。':'暂时无法更新，当前显示上次读取的进展。';}
        finally{clearTimeout(timeout);if(version===detailVersion){detailLoading=false;detailController=null;}}
      }
      function selectJob(id){
        detailVersion++;detailController?.abort();detailLoading=false;selected=id;content.hidden=true;notice.textContent='正在读取调研进展…';loadDetail();
      }
      opener.addEventListener('click',()=>{if(dialog.open)return;dialog.showModal();opener.setAttribute('aria-expanded','true');selected=jobs[0]?.job_id||'';selector.value=selected;selectJob(selected);});
      get('close').addEventListener('click',()=>dialog.close());
      dialog.addEventListener('close',()=>{detailVersion++;detailController?.abort();detailLoading=false;opener.setAttribute('aria-expanded','false');opener.focus();});
      dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();});
      selector.addEventListener('change',()=>selectJob(selector.value));
      async function refresh(){
        if(pending||document.hidden)return;
        pending=true;controller=new AbortController();const current=controller;const timeout=setTimeout(()=>current.abort(),12000);
        try{
          const response=await fetch('/api/research-chat/'+session+'/literature',{credentials:'same-origin',cache:'no-store',signal:current.signal});
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error('unavailable');
          jobs=(Array.isArray(data.jobs)?data.jobs:[]).filter(job=>valid(job.job_id));
          const job=jobs[0];
          if(!job){present();if(dialog.open)dialog.close();return;}
          present();panel.dataset.state=job.status;label.textContent='Literature · '+(labels[job.status]||'状态待核实');
          query.textContent=String(job.query||'');query.title=query.textContent;counts.textContent=summary(job)+(job.remaining_scientific_question?' · 仍有问题待核实':'');
          opener.setAttribute('aria-label','文献调研 · '+(labels[job.status]||'状态待核实'));
          opener.title=label.textContent+'\n'+query.textContent+'\n'+counts.textContent;
          const signature=JSON.stringify(jobs.map(item=>[item.job_id,item.status,item.query]));
          if(signature!==optionsSignature){optionsSignature=signature;selector.replaceChildren();jobs.forEach(item=>{const option=document.createElement('option');option.value=item.job_id;option.textContent=(labels[item.status]||'状态待核实')+' · '+String(item.query||'').slice(0,80);selector.append(option);});selector.value=selected||job.job_id;}
          get('jobs-label').hidden=jobs.length<2;
          if(dialog.open){if(!jobs.some(item=>item.job_id===selected))selectJob(job.job_id);else loadDetail();}
        }catch(error){if(!panel.hidden){label.textContent='Literature · 状态暂时无法读取';opener.setAttribute('aria-label','文献调研 · 状态暂时无法读取');opener.title=label.textContent;}}
        finally{clearTimeout(timeout);pending=false;controller=null;}
      }
      const timer=setInterval(refresh,4000);
      document.addEventListener('visibilitychange',refresh);
      window.addEventListener('pagehide',()=>{clearInterval(timer);controller?.abort();detailController?.abort();layout.disconnect();});
      refresh();
    })();</script>'''
