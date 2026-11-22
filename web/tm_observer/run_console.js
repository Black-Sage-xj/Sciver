"use strict";
(function () {
  const element = document.getElementById("tm-run-config");
  if (!element) return;
  const config = JSON.parse(element.textContent);

      const host=document.getElementById('tm-run-live');
      let lastRunSync=new Date().toLocaleTimeString();
      function runFreshness(text){
        const target=host?.querySelector('[data-run-freshness]');
        if(target)target.textContent=text;
      }
      runFreshness('页面快照 · '+lastRunSync+' · 每 8 秒检查');
      const selectedWorker=config.selectedWorker;
      if(/(?:[?&])worker_actions(?:=|&|$)/.test(window.location?.search||'')){
        const workerDialogUrl=new URL(window.location.href);
        workerDialogUrl.searchParams.delete('worker_actions');
        history.replaceState(history.state,'',workerDialogUrl);
        const dialog=host?.querySelector('#tm-worker-state-dialog');
        if(dialog&&!dialog.open)dialog.showModal();
      }
      const selectedHistoryCycle=config.selectedHistoryCycle;
      const cycleHistoryLoads=new Set();
      if(selectedWorker) document.addEventListener('tm-cycle-max-save',async event=>{
        // The Cycle limit belongs to the Master. Only the selected Worker's
        // scientific controls are Worker-scoped; use the Master guard here.
        const ctx=document.querySelector('[data-worker-request-context]');
        if(!ctx){event.detail.complete(false,'任务状态不可用，请刷新页面');return;}
        try{
          const response=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/action`,{method:'POST',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
            body:JSON.stringify({action:'max_cycles',value:event.detail.value,
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:ctx.dataset.cycle,expected_status:ctx.dataset.status,
              expected_launch_id:ctx.dataset.launch})});
          const result=await response.json();
          if(!response.ok||!result.ok)throw new Error(result.error||'任务上限更新失败');
          event.detail.complete(true,'已更新任务 Cycle 上限');
        }catch(error){event.detail.complete(false,String(error.message||error));}
      });
      document.addEventListener('click',async event=>{
        const refreshButton=event.target.closest?.('[data-worker-refresh]');
        if(refreshButton){location.reload();return;}
        const button=event.target.closest?.('[data-worker-action]');
        if(!button)return;
        const controls=button.closest('[data-worker-id]');
        if(!controls||!selectedWorker)return;
        const next=button.dataset.workerAction==='run_next';
        const context=next?document.querySelector('[data-worker-request-context]'):controls;
        if(!context){alert('任务状态不可用，请刷新页面');return;}
        const label=next?'启动调度器，分配新的科研 Cycle（不恢复已结束的 Cycle）':
          button.dataset.workerAction==='kill'?'不可恢复地终止 '+selectedWorker+' 当前 Cycle（将在安全检查点执行）':
          button.dataset.workerAction==='preserve_release'?'保全 '+selectedWorker+' 当前 Cycle 的阶段性成果并结束本轮。将使用一次独立只读 Investigator preserve_research 模型调用（可能计费）；成果报告和 Canvas 核验成功后才释放当前前沿。原 Cycle 不可恢复，不伪造 Judge 裁决。':
          button.dataset.workerAction==='release'?'只解除 '+selectedWorker+' 的占用，不会整理科研成果，也不能恢复原 Cycle':
          '确认操作 '+button.dataset.workerAction+' · '+selectedWorker;
        if(!confirm(label+'？'))return;
        const action=button.dataset.workerAction;
        const starting=next||action==='resume';
        const progress=starting?window.tmStartProgress?.start({
          action:next?'start_worker_scheduler':'worker_control',
          label:next?'启动调度器':'恢复 '+selectedWorker,
          worker:next?null:selectedWorker,
          scope:next?'scheduler':'worker'
        }):null;
        if(starting&&!progress){alert('已有启动请求正在处理，请勿重复提交；请先检查当前状态。');return;}
        button.disabled=true;
        try{
          const reply=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/action`,{method:'POST',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
            body:JSON.stringify({action:next?'start_worker_scheduler':'worker_control',
              value:next?null:{worker_id:selectedWorker,operation:action},
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:next?context.dataset.cycle:controls.dataset.cycle,
              expected_status:next?context.dataset.status:controls.dataset.status,
              expected_launch_id:next?context.dataset.launch:''})});
          const response=await reply.json();
          if(!reply.ok||!response.ok)throw new Error(response.error||'Worker 操作被拒绝');
          if(starting)progress.accepted();
          else location.reload();
        }catch(error){
          if(starting)progress.failed(String(error.message||error));
          else{alert(String(error.message||error));button.disabled=false;}
        }
      });
      const views={};
      let view=config.secondary;
      // Preserve exact Worker identity when navigating secondary readers,
      // including compatibility redirects from retired tab names.
      const currentParams=new URLSearchParams(location.search);
      const oldTab=currentParams.get('tab');
      const requestedWorker=currentParams.get('worker');
      if(typeof history.replaceState==='function' &&
          (['thread','artifacts','logs'].includes(oldTab) || requestedWorker!==selectedWorker && !!requestedWorker)) {
        const fixed=new URLSearchParams();
        if(selectedWorker)fixed.set('worker',selectedWorker);
        const tabName=['thread','artifacts'].includes(oldTab)?'research':
          oldTab==='logs'?'':oldTab;
        if(tabName)fixed.set('tab',tabName);
        const query=fixed.toString();
        history.replaceState(null,'',location.pathname+(query?'?'+query:''));
      }
      // First paint uses the last persisted, authentic provider percentages,
      // injected by the observer server. Cached endpoint is lightweight;
      // only the server initiates low-frequency (>=5m) provider RPCs.
      const workerQuotaAccount=config.pinnedAccount;
      let codexQuotaSnapshot=config.quotaSnapshot;
      const initialQuotaAccount=Array.isArray(codexQuotaSnapshot?.accounts)
        ?codexQuotaSnapshot.accounts.find(row=>row.account_home===(workerQuotaAccount||codexQuotaSnapshot.active_account)):null;
      let lastQuotaOkAt=Number.isFinite(initialQuotaAccount?.queried_at)
        ?initialQuotaAccount.queried_at*1000:null;
      let quotaStale=lastQuotaOkAt===null||Date.now()-lastQuotaOkAt>=300000;
      let quotaError=false,quotaPromise=null,quotaFollowupPending=false;
      function validQuotaPercent(value){
        return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=100?value:null;
      }
      function paintCodexQuota(){
        const card=host.querySelector('[data-codex-quota]');
        if(!card)return;
        const account=card.querySelector('[data-quota-account]');
        const value=card.querySelector('[data-quota-value]');
        const week=card.querySelector('[data-quota-week]');
        const progress=card.querySelector('[role="progressbar"]');
        const fill=card.querySelector('[data-quota-fill]');
        const status=card.querySelector('[data-quota-status]');
        const active=workerQuotaAccount||codexQuotaSnapshot?.active_account;
        const label=active==='account-1'?'Codex 1':active==='account-2'?'Codex 2':'当前账号未知';
        const current=Array.isArray(codexQuotaSnapshot?.accounts)
          ?codexQuotaSnapshot.accounts.find(row=>row.account_home===active):null;
        // Match Services: the server computes remaining_percent from the
        // provider window. Do not invert or re-round a separate percentage.
        const windows=current?.ok===true?[current.primary,current.secondary].filter(w=>
          w&&validQuotaPercent(w.remaining_percent)!==null):[];
        const windowLabel=w=>w.window_duration_mins===300?'5 小时':
          w.window_duration_mins===10080?'7 天':
          Number.isInteger(w.window_duration_mins)&&w.window_duration_mins>0
            ?w.window_duration_mins+' 分钟':'时长未知窗口';
        const remaining=windows.length?windows[0].remaining_percent:null;
        const firstLabel=windows.length?windowLabel(windows[0]):'额度窗口';
        const fiveText=firstLabel+'剩余：'+(remaining===null?'暂不可用':remaining+'%');
        const weekText=windows.length>1?windowLabel(windows[1])+'剩余：'+windows[1].remaining_percent+'%':'';
        // Even painting identical textContent replaces Text nodes, which
        // causes unnecessary visual/MutationObserver churn every 8 seconds.
        if(account.textContent!==label)account.textContent=label;
        if(value.textContent!==fiveText)value.textContent=fiveText;
        if(week.textContent!==weekText)week.textContent=weekText;
        week.hidden=windows.length<2;
        if(progress.getAttribute?.('aria-label')!==firstLabel+'剩余额度')
          progress.setAttribute('aria-label',firstLabel+'剩余额度');
        const width=(remaining===null?0:remaining)+'%';
        if(fill.style.width!==width)fill.style.width=width;
        if(remaining===null)progress.removeAttribute('aria-valuenow');
        else if(progress.getAttribute?.('aria-valuenow')!==String(remaining))
          progress.setAttribute('aria-valuenow',String(remaining));
        // An old but accurate provider reading is explicitly a cached reading,
        // not a new 0% or 'querying' state after a transient API failure.
        const time=lastQuotaOkAt ? new Date(lastQuotaOkAt).toLocaleTimeString('zh-CN',
          {hour:'2-digit',minute:'2-digit'}) : '';
        const freshness=lastQuotaOkAt
          ?(quotaError?'刷新失败 · 显示上次成功 '+time:
            (quotaStale?'历史记录 · 更新于 ':'更新于 ')+time+
            (codexQuotaSnapshot?.refreshing?' · 后台更新中':''))
          :('暂无历史记录'+(codexQuotaSnapshot?.refreshing?' · 后台更新中':' · 等待低频更新'));
        if(status&&status.textContent!==freshness)status.textContent=freshness;
      }
      function loadCodexQuota(){
        if(document.hidden)return Promise.resolve();
        // A tab load and a periodic timer may collide. Share the in-flight
        // request rather than issuing another slow provider RPC.
        if(quotaPromise)return quotaPromise;
        quotaPromise=(async()=>{
        try{
          const response=await fetch('/codex-usage-cached.json',{cache:'no-store'});
          if(!response.ok)throw new Error('historical quota endpoint unavailable');
          const candidate=await response.json();
          if(!Array.isArray(candidate?.accounts)||
              !([null,'account-1','account-2'].includes(candidate.active_account)))
            throw new Error('invalid historical quota snapshot');
          const current=candidate.accounts.find(item=>item.account_home===(workerQuotaAccount||candidate.active_account));
          // Valid historical records may be old, but never fabricate fresh data.
          // If the selected account changes, discard the old account display.
          if(current&&(current.ok!==true||
              ![current.primary,current.secondary].some(w=>
                w&&validQuotaPercent(w.remaining_percent)!==null)))
            throw new Error('malformed historical rate windows');
          codexQuotaSnapshot=candidate;
          lastQuotaOkAt=current&&Number.isFinite(current.queried_at)
            ?current.queried_at*1000:null;
          quotaStale=lastQuotaOkAt===null||Date.now()-lastQuotaOkAt>=300000;
          quotaError=false;
          if(candidate.refreshing&&!quotaFollowupPending){
            // One cheap follow-up to display a completed background update.
            // It never starts extra provider RPCs within the server's 5m TTL.
            quotaFollowupPending=true;
            setTimeout(()=>{quotaFollowupPending=false;loadCodexQuota();},25000);
          }
        }catch(_error){quotaError=true;}
        paintCodexQuota();
        })().finally(()=>{quotaPromise=null;});
        return quotaPromise;
      }
      let busy=false;
      const eventFilter={role:'all',category:'all',search:''};
      // Token details float outside the table and never change row height.
      let usagePopover=null,usageAnchor=null;
      // This native dialog lives OUTSIDE the 8-second refreshed Run panel.
      const budgetDialog=document.getElementById('tm-investigator-budget-dialog');
      let budgetOpener=null;
      function showBudgetReview(button){
        if(!budgetDialog)return;
        const source=button.closest('[data-tui-pane="investigation"]')
          ?.querySelector('template[data-budget-review-template]');
        if(!source)return;
        const content=budgetDialog.querySelector('[data-budget-modal-content]');
        if(!content)return;
        content.replaceChildren(source.content.cloneNode(true));
        budgetOpener=button;
        if(!budgetDialog.open)budgetDialog.showModal();
        content.querySelector('[data-budget-modal-close]')?.focus();
      }
      budgetDialog?.addEventListener('close',()=>{
        if(budgetOpener?.isConnected)budgetOpener.focus();
        budgetOpener=null;
      });
      budgetDialog?.addEventListener('click',event=>{
        if(event.target===budgetDialog){budgetDialog.close();return;}
        if(event.target.closest('[data-budget-modal-close]')){
          budgetDialog.close();return;
        }
        const select=event.target.closest('[data-budget-review-select]');
        if(!select)return;
        const next=select.dataset.budgetReviewSelect;
        budgetDialog.querySelectorAll('[data-budget-review-select]').forEach(button=>{
          button.setAttribute('aria-pressed',String(button===select));
        });
        budgetDialog.querySelectorAll('[data-budget-review-panel]').forEach(panel=>{
          panel.hidden=panel.dataset.budgetReviewPanel!==next;
        });
      });
      const judgeDialog=document.getElementById('tm-judge-review-dialog');
      let judgeOpener=null;
      function openJudgeDetails(button){
        const source=button.closest('[data-tui-pane="judge"]')
          ?.querySelector('template[data-judge-review-template]');
        const content=judgeDialog?.querySelector('[data-judge-modal-content]');
        if(!source||!content)return;
        content.replaceChildren(source.content.cloneNode(true));
        judgeOpener=button;
        if(!judgeDialog.open)judgeDialog.showModal();
        content.querySelector('[data-judge-modal-close]')?.focus();
      }
      judgeDialog?.addEventListener('close',()=>{
        if(judgeOpener?.isConnected)judgeOpener.focus();
        judgeOpener=null;
      });
      judgeDialog?.addEventListener('click',event=>{
        if(event.target===judgeDialog||event.target.closest('[data-judge-modal-close]')){
          judgeDialog.close();return;
        }
        const selected=event.target.closest('[data-judge-review-select]');
        if(!selected)return;
        judgeDialog.querySelectorAll('[data-judge-review-select]').forEach(item=>{
          item.setAttribute('aria-pressed',String(item===selected));
        });
        judgeDialog.querySelectorAll('[data-judge-review-panel]').forEach(panel=>{
          panel.hidden=panel.dataset.judgeReviewPanel!==selected.dataset.judgeReviewSelect;
        });
      });
      function closeUsagePopover(restoreFocus=false){
        const target=usageAnchor;
        if(target?.isConnected)target.setAttribute('aria-expanded','false');
        usagePopover?.remove();
        usagePopover=null;usageAnchor=null;
        if(restoreFocus&&target?.isConnected)target.focus();
      }
      function positionUsagePopover(){
        if(!usagePopover||!usageAnchor?.isConnected){closeUsagePopover();return;}
        const a=usageAnchor.getBoundingClientRect();
        const width=usagePopover.getBoundingClientRect().width;
        const height=usagePopover.getBoundingClientRect().height;
        const gutter=12;
        let left=a.right+gutter;
        if(left+width+gutter>window.innerWidth)left=a.left-width-gutter;
        if(left<gutter)left=Math.max(gutter,Math.min(a.left,window.innerWidth-width-gutter));
        const top=Math.min(Math.max(gutter,a.top),Math.max(gutter,window.innerHeight-height-gutter));
        usagePopover.style.left=left+'px';
        usagePopover.style.top=top+'px';
      }
      function toggleUsagePopover(button){
        if(usageAnchor===button){closeUsagePopover(true);return;}
        closeUsagePopover();
        let values;
        try{values=JSON.parse(button.dataset.tokenDetail);}catch(_err){return;}
        if(!values||typeof values!=='object'||Array.isArray(values))return;
        const panel=document.createElement('section');
        panel.className='tm-usage-popover';
        panel.setAttribute('role','dialog');
        panel.setAttribute('aria-label','Token 用量明细');
        const head=document.createElement('div');
        head.className='tm-usage-popover-head';
        const title=document.createElement('strong');title.textContent='Token 用量明细';
        const close=document.createElement('button');
        close.type='button';close.textContent='关闭';close.setAttribute('aria-label','关闭 Token 明细');
        head.append(title,close);
        const list=document.createElement('dl');
        for(const [name,value] of Object.entries(values)){
          const dt=document.createElement('dt'),dd=document.createElement('dd');
          dt.textContent=name;dd.textContent=String(value);
          list.append(dt,dd);
        }
        const note=document.createElement('p');
        note.className='tm-usage-popover-note';
        note.textContent='缓存命中率 = 缓存输入 ÷ 输入 Token；缓存已包含在输入中，推理已包含在输出中，不能重复相加。';
        panel.append(head,list,note);
        document.body.append(panel);
        usagePopover=panel;usageAnchor=button;
        button.setAttribute('aria-expanded','true');
        positionUsagePopover();
        close.addEventListener('click',()=>closeUsagePopover(true));
        close.focus();
      }
      // Native select choices only show (默认) when their menu is being opened.
      function routeChoiceLabels(field,expanded){
        const selectedDefault=field.dataset.routeDefaultValue||'';
        for(const option of field.options){
          if(option.dataset.routePlainLabel===undefined)
            option.dataset.routePlainLabel=option.textContent;
          option.textContent=option.dataset.routePlainLabel+
            (expanded&&selectedDefault&&option.value===selectedDefault?'（默认）':'');
        }
      }
      host.addEventListener('pointerdown',event=>{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field)routeChoiceLabels(field,true);
      });
      host.addEventListener('keydown',event=>{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field&&['Enter',' ','ArrowDown','ArrowUp'].includes(event.key))routeChoiceLabels(field,true);
      });
      // Begin at the most recent event; leave manual scroll positions alone.
      const firstTimeline=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
      if(firstTimeline){
        const selected=firstTimeline.querySelector('[data-history-selected]');
        if(selected)firstTimeline.scrollTop+=selected.getBoundingClientRect().top-firstTimeline.getBoundingClientRect().top-8;
        else firstTimeline.scrollTop=firstTimeline.scrollHeight;
      }
      // Historical headers are inserted above the first page; the request is
      // scoped to the task's persisted Cycle catalog and never opens raw paths.
      let olderCycleLoading=false;
      function makeOlderCycleRow(item) {
        const detail=document.createElement('details');
        detail.className='tm-cycle-group';
        detail.dataset.cycleGroupKey='cycle-'+item.id;
        detail.dataset.cycleHistoryLazy=String(item.id);
        const summary=document.createElement('summary');
        summary.className='tm-cycle-summary';
        summary.title='点击展开本 Cycle 记录';
        const title=document.createElement('span');
        title.className='tm-cycle-label';
        title.textContent='Cycle '+item.id;
        summary.append(title);
        if(item.time) {
          const clock=document.createElement('time');
          clock.textContent=String(item.time);
          summary.append(clock);
        }
        if(['supported','rejected','open','unadjudicated'].includes(item.verdict)) {
          const verdict=document.createElement('span');
          verdict.className='tm-cycle-verdict';
          verdict.title=item.verdict==='unadjudicated'?'本 Cycle 未形成最终科学裁决':'本 Cycle 的最终 Judge 裁决';
          verdict.textContent=item.verdict==='unadjudicated'?'未裁决':'Judge · '+item.verdict;
          summary.append(verdict);
        }
        const count=document.createElement('span');
        count.className='tm-cycle-count';
        count.textContent='展开';
        const chevron=document.createElement('span');
        chevron.className='tm-cycle-chevron';
        chevron.setAttribute('aria-hidden','true');
        chevron.textContent='⌄';
        summary.append(count,chevron);
        const placeholder=document.createElement('div');
        placeholder.className='tm-cycle-history-placeholder';
        placeholder.dataset.cycleHistoryPlaceholder='';
        placeholder.setAttribute('aria-live','polite');
        placeholder.textContent='展开后自动加载记录';
        detail.append(summary,placeholder);
        return detail;
      }
      async function loadOlderCycles(timeline) {
        const cursor=timeline?.dataset.cycleEarliest;
        if(olderCycleLoading||!timeline||!cursor||timeline.dataset.cycleHasMore!=='1')return;
        olderCycleLoading=true;
        const heading=timeline.querySelector('.tm-cycle-history-total');
        try {
          const url=`/api/run/${encodeURIComponent(config.observerId)}/cycle-page?before=`+encodeURIComponent(cursor);
          const reply=await fetch(url,{cache:'no-store'});
          if(!reply.ok)throw new Error('HTTP '+reply.status);
          const page=await reply.json();
          if(!Array.isArray(page.cycles)||typeof page.has_more!=='boolean')
            throw new Error('Invalid Cycle page');
          // Use the current DOM after await: the 8-second poll may have
          // replaced the old history panel while this request was in flight.
          const current=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
          const list=current?.querySelector('.tm-run-cycle-groups');
          if(!current||!list||current.dataset.cycleEarliest!==cursor)return;
          const top=current.scrollTop, height=current.scrollHeight;
          const existing=new Set([...list.querySelectorAll('[data-cycle-group-key]')]
            .map(d=>d.dataset.cycleGroupKey));
          for(const item of page.cycles) {
            const id=Number(item.id);
            if(!Number.isSafeInteger(id)||id<0||existing.has('cycle-'+id))continue;
            const row=makeOlderCycleRow(item);
            const sibling=[...list.children].find(element=>{
              const detail=element.matches?.('[data-cycle-group-key]')?element:
                element.querySelector?.('[data-cycle-group-key]');
              return detail&&Number(detail.dataset.cycleGroupKey?.replace('cycle-',''))>id;
            });
            list.insertBefore(row,sibling||null);
            existing.add('cycle-'+id);
          }
          if(page.cycles.length)current.dataset.cycleEarliest=String(page.cycles[0].id);
          current.dataset.cycleHasMore=page.has_more?'1':'0';
          const count=existing.size, total=Number(page.total)||count;
          if(heading)heading.textContent='已显示 '+count+' / '+total+' 个 Cycle'+
            (page.has_more?' · 向上滚动到顶部继续加载更早 Cycle':' · 已加载全部历史 Cycle')+
            ' · 点击 Cycle 行展开记录';
          // Insert above, but do not shift the Cycle the reader is viewing.
          current.scrollTop=top+current.scrollHeight-height;
        }catch(error) {
          const current=host.querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
          const note=current?.querySelector('.tm-cycle-history-total');
          if(note)note.textContent='加载更早 Cycle 失败；再次向上滚动可重试';
        }finally{olderCycleLoading=false;}
      }
      host.addEventListener('scroll',event=>{
        const timeline=event.target;
        if(historyView==='recent'&&timeline?.matches?.(
          '[data-history-panel="recent"] .tm-run-events-scroll[data-cycle-has-more="1"]'
        )&&timeline.scrollTop<=24)loadOlderCycles(timeline);
      },true);
      let historyView='recent';
      const visitedHistory=new Set(['recent']);
      function applyHistoryView(){
        host.querySelectorAll('[data-history-view]').forEach(button=>{
          const selected=button.dataset.historyView===historyView;
          button.setAttribute('aria-selected',String(selected));
          button.tabIndex=selected?0:-1;
        });
        host.querySelectorAll('[data-history-panel]').forEach(panel=>{
          panel.hidden=panel.dataset.historyPanel!==historyView;
        });
      }
      function chooseHistoryView(next){
        if(!['recent','important','incidents'].includes(next))return;
        historyView=next;
        applyHistoryView();
        if(!visitedHistory.has(next)){
          const timeline=host.querySelector('[data-history-panel="'+next+'"] .tm-run-events-scroll');
          if(timeline)timeline.scrollTop=timeline.scrollHeight;
          visitedHistory.add(next);
        }
      }
      // Load a historical Cycle into its own disclosure instead of navigating
      // away from the live Task Console. The API renders one trusted snapshot
      // through the same server-side projection used for direct history links.
      host.addEventListener('click',async event=>{
        const summary=event.target.closest('summary');
        const detail=summary?.parentElement;
        if(!detail?.matches('details[data-cycle-history-lazy]')||
           detail.dataset.cycleHistoryLoaded==='1'||cycleHistoryLoads.has(detail.dataset.cycleHistoryLazy))return;
        if(detail.open)return; // Closing an already-open row must stay local.
        const id=detail.dataset.cycleHistoryLazy;
        const placeholder=detail.querySelector('[data-cycle-history-placeholder]');
        if(!id||!placeholder)return;
        cycleHistoryLoads.add(id);
        detail.dataset.cycleHistoryLoading='1';
        placeholder.textContent='正在加载本 Cycle 记录…';
        try{
          const query=new URLSearchParams({tab:view||'activity',history_cycle:id});
          if(selectedWorker)query.set('worker',selectedWorker);
          const reply=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/view?`+query.toString(),{cache:'no-store'});
          if(!reply.ok)throw new Error('Cycle history request failed: '+reply.status);
          const parsed=new DOMParser().parseFromString(await reply.text(),'text/html');
          const wrapper=[...parsed.querySelectorAll('[data-history-selected]')]
            .find(item=>item.dataset.historySelected===id);
          const source=wrapper?.querySelector('details.tm-cycle-group');
          if(!source)throw new Error('Cycle history was not present in the response');
          const current=[...host.querySelectorAll('details[data-cycle-history-lazy]')]
            .find(item=>item.dataset.cycleHistoryLazy===id);
          if(!current||current.dataset.cycleHistoryLoaded==='1')return;
          const loaded=source.cloneNode(true);
          loaded.dataset.cycleHistoryLoaded='1';
          loaded.open=current.open;
          current.replaceWith(loaded);
        }catch(error){
          const current=[...host.querySelectorAll('details[data-cycle-history-lazy]')]
            .find(item=>item.dataset.cycleHistoryLazy===id);
          const currentPlaceholder=current?.querySelector('[data-cycle-history-placeholder]');
          if(currentPlaceholder)currentPlaceholder.textContent='加载失败；收起后再次展开可重试';
          if(current)delete current.dataset.cycleHistoryLoading;
        }finally{
          cycleHistoryLoads.delete(id);
        }
      });
      host.addEventListener('click',event=>{
        const tab=event.target.closest('[data-history-view]');
        if(tab)chooseHistoryView(tab.dataset.historyView);
      });
      host.addEventListener('keydown',event=>{
        const tab=event.target.closest('[data-history-view]');
        if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();
        const order=['recent','important','incidents'];
        const next=event.key==='Home'?'recent':event.key==='End'?'incidents':
          order[(order.indexOf(tab.dataset.historyView)+(event.key==='ArrowRight'?1:order.length-1))%order.length];
        chooseHistoryView(next);
        host.querySelector('[data-history-view="'+next+'"]')?.focus();
      });
      host.addEventListener('focusout',event=>{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field)routeChoiceLabels(field,false);
      });
      document.addEventListener('pointerdown',event=>{
        if(usagePopover&&!usagePopover.contains(event.target)&&
            !usageAnchor?.contains(event.target))closeUsagePopover();
      });
      window.addEventListener('resize',positionUsagePopover);
      window.addEventListener('scroll',positionUsagePopover,true);
      function applyEventFilters(){
        if(view!=='events')return;
        const pane=host.querySelector('.tm-events-browser');
        if(!pane)return;
        for(const name of ['role','category','search']){
          const field=pane.querySelector('[data-event-filter="'+name+'"]');
          if(field&&field.value!==eventFilter[name])field.value=eventFilter[name];
        }
        let count=0;
        pane.querySelectorAll('.tm-event').forEach(card=>{
          const match=(eventFilter.role==='all'||card.dataset.eventRole===eventFilter.role)
             &&(eventFilter.category==='all'||card.dataset.eventCategory===eventFilter.category)
             &&(!eventFilter.search||card.textContent.toLowerCase().includes(eventFilter.search));
          card.hidden=!match;if(match)count++;
        });
        const label=pane.querySelector('[data-event-count]');
        if(label)label.textContent='匹配 '+count+' 条 · 仅最近事件；过滤只改变显示，不修改日志';
      }
      host.addEventListener('input',event=>{
        if(event.target.matches('[data-event-filter="search"]')){
          eventFilter.search=event.target.value.toLowerCase().trim();applyEventFilters();
        }
      });
      host.addEventListener('change',event=>{
        const name=event.target.dataset.eventFilter;
        if(name&&name!=='search'){eventFilter[name]=event.target.value;applyEventFilters();}
      });
      function reconcileHeaderPreservingQuota(current,fresh) {
        // Elapsed changes almost every 8-second Run poll. Updating the whole
        // header via innerHTML would delete the live Codex quota card, create
        // placeholders, restart its progress-bar transition and then repaint.
        // Patch only changed sibling metrics: keep quota DOM connected.
        const oldGrid=current.querySelector(':scope > .tm-tui-kv-grid');
        const nextGrid=fresh.querySelector(':scope > .tm-tui-kv-grid');
        if(!oldGrid||!nextGrid||oldGrid.children.length!==nextGrid.children.length)
          return false; // Unrecognized header layout: use general safe path.
        const oldCards=[...oldGrid.children],newCards=[...nextGrid.children];
        const oldQuota=oldGrid.querySelector('[data-codex-quota]');
        const newQuota=nextGrid.querySelector('[data-codex-quota]');
        if(!oldQuota||!newQuota)return false;
        for(let i=0;i<oldCards.length;i++){
          const oldIsQuota=oldCards[i]===oldQuota;
          const newIsQuota=newCards[i]===newQuota;
          if(oldIsQuota!==newIsQuota)return false;
        }
        for(let i=0;i<oldCards.length;i++){
          if(oldCards[i]===oldQuota)continue;
          if(oldCards[i].outerHTML!==newCards[i].outerHTML)
            oldCards[i].replaceWith(newCards[i]);
        }
        // Attention/warning content may change independently of the metrics.
        const oldExtras=[...current.children].filter(item=>item!==oldGrid);
        const freshExtras=[...fresh.children].filter(item=>item!==nextGrid);
        for(let i=0;i<Math.max(oldExtras.length,freshExtras.length);i++){
          if(!oldExtras[i])current.append(freshExtras[i]);
          else if(!freshExtras[i])oldExtras[i].remove();
          else {
            const oldDetails=oldExtras[i].querySelector('[data-recovery-details]');
            const newDetails=freshExtras[i].querySelector('[data-recovery-details]');
            if(oldDetails&&newDetails&&oldDetails.textContent===newDetails.textContent)
              newDetails.open=oldDetails.open;
            if(oldExtras[i].outerHTML!==freshExtras[i].outerHTML)
              oldExtras[i].replaceWith(freshExtras[i]);
          }
        }
        return true;
      }
      async function refresh() {
        // Continue polling identity and process state while reading a long
        // Stage Conversation. Its inner conversation DOM remains untouched.
        if(!host||document.hidden||busy)return;
        busy=true;
        try {
          const reply=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/view?tab=`+encodeURIComponent(view||'activity')+(selectedWorker?'&worker='+encodeURIComponent(selectedWorker):'')+(selectedHistoryCycle?'&history_cycle='+encodeURIComponent(selectedHistoryCycle):''),{cache:'no-store'});
          if(!reply.ok){runFreshness('读取失败 · 保留 '+lastRunSync+' 的快照');return;}
          lastRunSync=new Date().toLocaleTimeString();
          const snapshotStale=reply.headers.get('X-Sciver-State-Freshness')==='stale';
          const refreshFailed=reply.headers.get('X-Sciver-State-Refresh-Failed')==='1';
          runFreshness(snapshotStale
            ? (refreshFailed?'状态刷新失败 · ':'后台更新中 · ')+'当前为 '+reply.headers.get('X-Sciver-State-Age')+' 秒前的快照'
            : '最近获取 '+lastRunSync+' · 8 秒自动检查');
          const parsed=new DOMParser().parseFromString(await reply.text(),'text/html');
          const incoming=parsed.querySelector('.tm-tui-screen');
          const present=host.querySelector('.tm-tui-screen');
          if(!incoming)return;
          // A selected Worker may be dismissed in another tab. Do not reuse
          // its stale control surface for a newly selected Worker.
          if(present && present.dataset.tuiWorker!==incoming.dataset.tuiWorker) {
            window.location.assign(`/run/${encodeURIComponent(config.observerId)}/?worker=`+encodeURIComponent(incoming.dataset.tuiWorker||'worker-1')); return;
          }
          // Master/Worker controls outside the live pane must follow the
          // freshest Cycle and State. Never leave a stale Pause after OVER.
          if(present&&(document.getElementById('tm-operator')||selectedWorker) &&
             !window.tmStartProgress?.shouldHoldRefresh() &&
             (present.dataset.tuiStatus!==incoming.dataset.tuiStatus ||
              present.dataset.tuiCycle!==incoming.dataset.tuiCycle)) {
            window.location.reload();
            return;
          }
          if(present) {
            const oldReport=present.querySelector('[data-final-report-entry="console"]');
            const newReport=incoming.querySelector('[data-final-report-entry="console"]');
            const reportStarting=oldReport?.dataset.reportStarting==='1'&&
              newReport?.querySelector('[data-report-state="queued"],[data-report-state="missing"]');
            if(oldReport&&newReport&&!oldReport.dataset.reportSubmitting&&!reportStarting&&oldReport.outerHTML!==newReport.outerHTML){
              const hadFocus=oldReport.contains(document.activeElement);
              oldReport.replaceWith(newReport);
              if(hadFocus)newReport.querySelector('a,button')?.focus({preventScroll:true});
            }
            const oldTabs=present.querySelector('.tm-worker-tabs');
            const newTabs=incoming.querySelector('.tm-worker-tabs');
            // Keep the status of *other* Agents current, even when the selected
            // Worker's own State/Cycle is unchanged. Preserve pending click feedback.
            if(oldTabs&&newTabs&&oldTabs.dataset.workerMutating!=='1'
               &&oldTabs.innerHTML!==newTabs.innerHTML)oldTabs.replaceWith(newTabs);
          }
          if(!present||present.dataset.tuiScreen!==incoming.dataset.tuiScreen) {
            closeUsagePopover();
            host.replaceChildren(incoming);
            paintCodexQuota();
            if(view==='events')window.tmStageConversationMount?.(incoming);
            return;
          }
          const oldPanes=present.querySelectorAll('[data-tui-pane]');
          const newPanes=incoming.querySelectorAll('[data-tui-pane]');
          if(oldPanes.length!==newPanes.length && view!=='events'){
            closeUsagePopover();
            host.replaceChildren(incoming);
            paintCodexQuota();
            return;
          }
          // Never replace an active Stage reader, even if its server layout
          // differs; continue updating Task/Cycle identity and status only.
          for(let n=0;n<Math.min(oldPanes.length,newPanes.length);n++){
            if(oldPanes[n].dataset.tuiPane==='header' &&
                reconcileHeaderPreservingQuota(oldPanes[n],newPanes[n]))continue;
            if(oldPanes[n].dataset.tuiPane==='workflow'&&oldPanes[n].querySelector('[data-route-dirty="1"]'))continue;
            // Keep a switch or model draft mounted until the API request settles.
            if(oldPanes[n].dataset.tuiPane==='quota-settings' &&
              (oldPanes[n].querySelector('[data-setting-saving="1"]') ||
               (document.activeElement && oldPanes[n].contains(document.activeElement) &&
                document.activeElement.matches('input,select,[data-setting-save-model]'))))continue;
            if(view==='events'&&oldPanes[n].dataset.tuiPane==='secondary')continue;
            // Never destroy a partially typed Cycle limit during live polling.
            if(oldPanes[n].dataset.tuiPane==='header'
               &&oldPanes[n].querySelector('[data-cycle-max-form]:not([hidden])'))continue;
            // Preserve prepended history when the 8-second live poll sends
            // only its default ten Cycle headers. Merge before DOM comparison
            // so polling cannot silently discard the user's older pages.
            const oldRecent=oldPanes[n].querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
            const newRecent=newPanes[n].querySelector('[data-history-panel="recent"] .tm-run-events-scroll');
            if(oldRecent&&newRecent&&oldRecent.dataset.cycleEarliest) {
              const newList=newRecent.querySelector('.tm-run-cycle-groups');
              const oldList=oldRecent.querySelector('.tm-run-cycle-groups');
              if(oldList&&newList) {
                const keys=new Set([...newList.querySelectorAll('[data-cycle-group-key]')]
                  .map(row=>row.dataset.cycleGroupKey));
                for(const original of oldList.children) {
                  const detail=original.matches?.('[data-cycle-group-key]')?original:
                    original.querySelector?.('[data-cycle-group-key]');
                  const key=detail?.dataset.cycleGroupKey;
                  if(!key||keys.has(key))continue;
                  const id=Number(key.replace('cycle-',''));
                  const next=[...newList.children].find(item=>{
                    const d=item.matches?.('[data-cycle-group-key]')?item:
                      item.querySelector?.('[data-cycle-group-key]');
                    return d&&Number(d.dataset.cycleGroupKey?.replace('cycle-',''))>id;
                  });
                  newList.insertBefore(original.cloneNode(true),next||null);
                  keys.add(key);
                }
                newRecent.dataset.cycleEarliest=oldRecent.dataset.cycleEarliest;
                newRecent.dataset.cycleHasMore=oldRecent.dataset.cycleHasMore;
                const total=Number(newRecent.dataset.cycleTotal)||keys.size;
                const heading=newRecent.querySelector('.tm-cycle-history-total');
                if(heading)heading.textContent='已显示 '+keys.size+' / '+total+' 个 Cycle'+
                  (newRecent.dataset.cycleHasMore==='1'?' · 向上滚动到顶部继续加载更早 Cycle':
                  ' · 已加载全部历史 Cycle')+' · 点击 Cycle 行展开记录';
              }
            }
            if(oldPanes[n].innerHTML!==newPanes[n].innerHTML){
              const previousCycleOpen=new Map([...oldPanes[n].querySelectorAll('details[data-cycle-group-key]')]
                .map(detail=>[detail.dataset.cycleGroupKey,detail.open]));
              const loadedCycleHtml=new Map([...oldPanes[n].querySelectorAll('details[data-cycle-group-key][data-cycle-history-loaded="1"]')]
                .map(detail=>[detail.dataset.cycleGroupKey,detail.innerHTML]));
              const openDetails=new Set([...oldPanes[n].querySelectorAll('details:not([data-cycle-group-key])')]
                .flatMap((detail,index)=>detail.open?[index]:[]));
              const historyScroll=new Map([...oldPanes[n].querySelectorAll('[data-history-panel]')]
                .map(panel=>{
                  const timeline=panel.querySelector('.tm-run-events-scroll');
                  return [panel.dataset.historyPanel, timeline?{
                    top:timeline.scrollTop,
                    nearLatest:!panel.hidden &&
                      timeline.scrollHeight-timeline.clientHeight-timeline.scrollTop<28
                  }:null];
                }));
              const expandedImportant=new Set([...oldPanes[n].querySelectorAll('details[data-important-key][open]')]
                .map(detail=>detail.dataset.importantKey));
              closeUsagePopover();
              oldPanes[n].innerHTML=newPanes[n].innerHTML;
              if(oldPanes[n].dataset.tuiPane==='quota-settings'){
                showSettingDetail(oldPanes[n],oldPanes[n].dataset.activeSetting||'');
              }
              applyHistoryView();
              [...oldPanes[n].querySelectorAll('details:not([data-cycle-group-key]):not([data-important-key])')].forEach((detail,index)=>{
                if(openDetails.has(index))detail.open=true;
              });
              oldPanes[n].querySelectorAll('details[data-important-key]').forEach(detail=>{
                if(expandedImportant.has(detail.dataset.importantKey))detail.open=true;
              });
              oldPanes[n].querySelectorAll('details[data-cycle-group-key]').forEach(detail=>{
                const key=detail.dataset.cycleGroupKey;
                if(loadedCycleHtml.has(key)){
                  detail.innerHTML=loadedCycleHtml.get(key);
                  detail.dataset.cycleHistoryLoaded='1';
                }
                if(previousCycleOpen.has(key))detail.open=previousCycleOpen.get(key);
              });
              oldPanes[n].querySelectorAll('[data-history-panel]').forEach(panel=>{
                const timeline=panel.querySelector('.tm-run-events-scroll');
                const previous=historyScroll.get(panel.dataset.historyPanel);
                if(timeline&&previous)timeline.scrollTop=previous.nearLatest
                  ?timeline.scrollHeight:previous.top;
              });
            }
          }
          // The Task/Cycle identity sits in the title bar, outside all
          // data-tui-pane nodes. Refresh it explicitly on every successful
          // state poll; do not rely on replacing the science transcript.
          const oldIdentity=present.querySelector('.tm-tui-identity');
          const newIdentity=incoming.querySelector('.tm-tui-identity');
          if(oldIdentity&&newIdentity&&oldIdentity.textContent!==newIdentity.textContent)
            oldIdentity.textContent=newIdentity.textContent;
          applyEventFilters();
          paintCodexQuota();
          present.dataset.tuiCycle=incoming.dataset.tuiCycle;
          present.dataset.tuiStatus=incoming.dataset.tuiStatus;
          if(view==='events')
            window.tmStageConversationNotifyCycle?.(incoming.dataset.tuiCycle);
        } catch (_err) { runFreshness('网络异常 · 保留 '+lastRunSync+' 的快照'); }
        finally {busy=false;}
      }
      function select(viewName){
        // Every secondary page is a separate focused reader. Full
        // navigation both removes home-only controls on entry and restores
        // them on return, without retaining stale Stage JS or layout.
        const params=new URLSearchParams();
        if(selectedWorker)params.set('worker',selectedWorker);
        if(viewName)params.set('tab',viewName);
        const query=params.toString();
        window.location.assign(location.pathname+(query?'?'+query:''));
      }
      function openCycleEditor(button){
        const tile=button.closest('.tm-tui-kv');
        const form=tile?.querySelector('[data-cycle-max-form]');
        if(!form)return;
        form.hidden=false;
        button.setAttribute('aria-expanded','true');
        const input=form.querySelector('[data-cycle-max-input]');
        if(input){input.value=tile.querySelector('[data-cycle-max-current]')?.textContent||input.value;input.focus();input.select();}
      }
      function closeCycleEditor(tile){
        const form=tile?.querySelector('[data-cycle-max-form]');
        if(!form)return;
        form.hidden=true;
        tile.querySelector('[data-tui-op="max_cycles"]')?.setAttribute('aria-expanded','false');
        tile.querySelector('[data-tui-op="max_cycles"]')?.focus();
      }
      const supportsHotModelRoute=config.hotRoleRoute;
      const routeSaveDialog=document.getElementById('tm-route-save-dialog');
      function chooseRouteActivation(role,model,effort,prior){
        if(!routeSaveDialog)return Promise.resolve(null);
        routeSaveDialog.returnValue='';
        const description=routeSaveDialog.querySelector('[data-route-save-description]');
        description.textContent=role+' · '+model+' · '+effort+'；请选择何时应用新的模型配置。';
        for(const input of routeSaveDialog.querySelectorAll('input[name="tm-route-activation"]'))
          input.checked=input.value===(prior==='next_cycle'?'next_cycle':'next_invocation');
        return new Promise(resolve=>{
          routeSaveDialog.addEventListener('close',()=>{
            const selected=routeSaveDialog.querySelector('input[name="tm-route-activation"]:checked');
            resolve(routeSaveDialog.returnValue==='save'?selected?.value||null:null);
          },{once:true});
          routeSaveDialog.showModal();
        });
      }
      async function submitAgentModel(field,editTimingOnly=false){
        const row=field.closest('tr[data-agent-role]');
        if(!row)return;
        const modelSelect=row.querySelector('[data-route-model]');
        const effortSelect=row.querySelector('[data-route-effort]');
        const modelChoice=modelSelect.value,effort=effortSelect.value;
        const restore=()=>{modelSelect.value=row.dataset.savedModel;effortSelect.value=row.dataset.savedEffort;};
        const at=modelChoice.lastIndexOf('@');
        if(at<1||!['low','medium','high','xhigh'].includes(effort)){restore();return;}
        if(!editTimingOnly&&modelChoice===row.dataset.savedModel&&effort===row.dataset.savedEffort)return;
        const role=row.dataset.agentRole;
        const model=modelChoice.slice(0,at),provider=modelChoice.slice(at+1);
        const activation=await chooseRouteActivation(role,model,effort,
          selectedWorker&&!editTimingOnly?'next_invocation':row.dataset.savedActivation);
        if(!activation){restore();return;}
        if(editTimingOnly&&activation===row.dataset.savedActivation)return;
        const box=document.getElementById('tm-operator')||document.querySelector?.('.tm-worker-controls');
        if(!box){restore();return;}
        row.dataset.routeDirty='1';
        modelSelect.disabled=true;
        effortSelect.disabled=true;
        try{
          const response=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/action`,{method:'POST',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
            body:JSON.stringify({action:selectedWorker?'set_worker_route':'set_role_route',
              value:selectedWorker?{worker_id:selectedWorker,role,model,provider,reasoning_effort:effort,activation}:
                  {role,model,provider,reasoning_effort:effort,activation},
              request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:box.dataset.cycle,expected_status:box.dataset.status,
              expected_launch_id:selectedWorker?'':box.dataset.launch})});
          const reply=await response.json();
          if(!response.ok||!reply.ok)throw new Error(reply.error||'模型配置保存失败');
          row.dataset.savedModel=modelChoice;
          row.dataset.savedEffort=effort;
          row.dataset.savedActivation=activation;
          row.dataset.routeDirty='0';
          await refresh();
        }catch(error){
          restore();
          row.dataset.routeDirty='0';
          window.alert('无法保存模型配置：'+String(error.message||error)+'。请刷新后重试。');
        }finally{
          modelSelect.disabled=false;
          effortSelect.disabled=false;
        }
      }
      function showSettingDetail(pane,key){
        const details=pane?.querySelector('#tm-setting-detail-box');
        if(!details)return;
        const item=key?[...details.querySelectorAll('[data-setting-detail]')]
          .find(el=>el.dataset.settingDetail===key):null;
        const selected=item?key:'';
        pane.dataset.activeSetting=selected;
        details.hidden=!selected;
        details.querySelectorAll('[data-setting-detail]').forEach(el=>{
          el.hidden=el.dataset.settingDetail!==selected;
        });
        pane.querySelectorAll('[data-setting-open]').forEach(button=>{
          button.setAttribute('aria-expanded',
            String(button.dataset.settingOpen===selected));
        });
      }
      async function commitSetting(action,value,input,prior){
        const box=document.getElementById('tm-operator');
        const settings=document.getElementById('tm-web-settings');
        const info=settings?.querySelector('.tm-setting-feedback');
        if(!box||!settings)return;
        settings.dataset.settingSaving='1';
        if(input)input.disabled=true;
        if(info)info.textContent='正在保存设置…';
        try{
          const response=await fetch(`/api/run/${encodeURIComponent(config.observerId)}/action`,{method:'POST',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
            body:JSON.stringify({action,value,request_id:crypto.randomUUID().replace(/-/g,''),
              expected_cycle:box.dataset.cycle,expected_status:box.dataset.status,
              expected_launch_id:box.dataset.launch})});
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error(data.error||'写入失败');
          const key=input?.dataset?.settingKey;
          const caption=input?.closest('.tm-setting-compact')?.querySelector('small');
          if(caption){
            caption.textContent=input.dataset.settingMode==='flag'
              ? '已配置'+(input.checked?'开启':'关闭')+' · 下次 Run/Resume'
              : (input.checked?'开启':'关闭')+' · 当前任务';
          }
          if(key==='cycle_audit'){
            const menuStatus=settings.querySelector('.tm-setting-gear-status');
            if(menuStatus)menuStatus.textContent='已配置'+(input.checked?'开启':'关闭')+' · 下次 Run/Resume';
          }
          if(info)info.textContent='已保存至当前任务。部分配置在下次运行或审查时生效。';
        }catch(error){
          if(input&&prior!==undefined)input.checked=prior;
          if(info)info.textContent='保存失败：'+String(error.message||error)+'。请刷新后再试。';
        }finally{
          if(input)input.disabled=false;
          delete settings.dataset.settingSaving;
        }
      }
      // The quota-settings pane is re-rendered every eight seconds; listener
      // ownership belongs to the stable document, never a disposable input.
      document.addEventListener('change',event=>{
        const input=event.target.closest?.('[data-tui-pane="quota-settings"] [data-setting-key]');
        if(!input)return;
        const key=input.dataset.settingKey,selected=input.checked;
        const action=input.dataset.settingMode==='hot'?key:'set_operator_preference';
        const value=input.dataset.settingMode==='hot'?selected:{kind:'flag',key,value:selected};
        commitSetting(action,value,input,!selected);
      });
      document.addEventListener('click',event=>{
        const card=event.target.closest?.('[data-tui-pane="quota-settings"] [data-setting-card]');
        if(!card)return;
        // Only the physical switch label may change settings; all other
        // card surfaces merely select or collapse this card's detail.
        if(event.target.closest('.tm-setting-switch-control'))return;
        const pane=card.closest('[data-tui-pane="quota-settings"]');
        const key=card.dataset.settingCard;
        showSettingDetail(pane,pane.dataset.activeSetting===key?'':key);
      });
      document.addEventListener('keydown',event=>{
        const help=event.target.closest?.('[data-tui-pane="quota-settings"] .tm-setting-help');
        if(help && (event.key==='Enter'||event.key===' ')){
          event.preventDefault();
          help.click();
        }
      });
      document.addEventListener('click',event=>{
        const button=event.target.closest?.('#tm-web-settings [data-setting-save-model]');
        if(!button)return;
        const settings=document.getElementById('tm-web-settings');
        const key=button.dataset.settingSaveModel;
        const value=settings?.querySelector('[data-setting-model="'+key+'"]')?.value;
        if(value===undefined)return;
        commitSetting('set_operator_preference',{kind:'model',key,value},button);
      });
      host.addEventListener('click',event=>{
        const button=event.target.closest('[data-route-activation-edit]');
        if(button){
          const field=button.closest('tr[data-agent-role]')?.querySelector('[data-route-model]');
          if(field)submitAgentModel(field,true);
        }
      });
      host.addEventListener('change',event=>{
        const field=event.target.closest('[data-route-model],[data-route-effort]');
        if(field){routeChoiceLabels(field,false);submitAgentModel(field);}
      });
      document.addEventListener('click', event=>{
        const workerTab=event.target.closest('.tm-worker-tab[href]');
        if(workerTab && !event.defaultPrevented && event.button===0 &&
           !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey){
          if(workerTab.classList.contains('is-selected')){
            const dialog=host.querySelector('#tm-worker-state-dialog');
            if(dialog){event.preventDefault();if(!dialog.open)dialog.showModal();}
          }else{
            event.preventDefault();
            const url=new URL(workerTab.href);
            url.searchParams.set('worker_actions','1');
            window.location.assign(url);
          }
          return;
        }
        const workerOpener=event.target.closest('[data-worker-state-open]');
        if(workerOpener){
          const dialog=host.querySelector('#tm-worker-state-dialog');
          if(dialog&&!dialog.open)dialog.showModal();
          return;
        }
        const opener=event.target.closest('[data-tui-process-actions]');
        const budgetButton=event.target.closest('[data-budget-review-open]');
        if(budgetButton){showBudgetReview(budgetButton);return;}
        const judgeButton=event.target.closest('[data-judge-review-open]');
        if(judgeButton){openJudgeDetails(judgeButton);return;}
        if(opener){
          if(selectedWorker){
            const state=host.querySelector('[data-worker-id]')?.dataset.status?.toUpperCase();
            const operation=['OVER','RELEASED','CANCELLED','STOPPED','FAILED_START'].includes(state)?'run_next':
              ['PAUSED','FAILED','INTERRUPTED','NEEDS_ATTENTION'].includes(state)?'resume':
              state==='RUNNING'?'pause':null;
            const button=operation?document.querySelector('[data-worker-action="'+operation+'"]'):null;
            if(button)button.click();
            else alert('当前 Worker 没有可执行的操作，请检查状态。');
            return;
          }
          const dialog=document.getElementById('tm-operator-quick-dialog');
          if(dialog&&!dialog.open)dialog.showModal();
          return;
        }
        const quick=event.target.closest('[data-tui-quick-action]');
        if(quick){
          const menu=document.getElementById('tm-operator-quick-dialog');
          if(menu?.open)menu.close();
          const action=quick.dataset.tuiQuickAction;
          const existing=document.getElementById('tm-operator')?.querySelector(
            '[data-operator-action="'+action+'"]');
          // The original control owns confirmation, validation, and request
          // submission. Do not create a parallel operator API entrypoint.
          if(existing&&!existing.disabled)existing.click();
          else window.alert('当前 Cycle 不接受此控制请求；请刷新运行状态。');
          return;
        }
        const link=event.target.closest('[data-tui-view]');
        const tokenButton=event.target.closest('[data-token-detail]');
        if(tokenButton){toggleUsagePopover(tokenButton);return;}
        if(link){select(link.dataset.tuiView);return;}
        if(event.target.closest('[data-tui-copy]')){
          const value=host.querySelector('[data-tui-pane="secondary"]')?.textContent;
          if(value&&navigator.clipboard?.writeText)navigator.clipboard.writeText(value);
          return;
        }
        const cancel=event.target.closest('[data-cycle-max-cancel]');
        if(cancel){closeCycleEditor(cancel.closest('.tm-tui-kv'));return;}
        const op=event.target.closest('[data-tui-op="max_cycles"]');
        if(op){openCycleEditor(op);return;}
      });
      document.addEventListener('submit',event=>{
        const form=event.target.closest('[data-cycle-max-form]');
        if(!form)return;
        event.preventDefault();
        const input=form.querySelector('[data-cycle-max-input]');
        const value=Number(input?.value);
        const info=form.querySelector('[data-cycle-max-message]');
        if(!Number.isSafeInteger(value)||value<1||value>1000000){info.textContent='请输入 1–1000000 的整数';return;}
        const tile=form.closest('.tm-tui-kv');
        const save=form.querySelector('[data-cycle-max-save]');
        save.disabled=true;
        info.textContent='正在保存…';
        document.dispatchEvent(new CustomEvent('tm-cycle-max-save',{detail:{
          value,complete(okay,message){
            save.disabled=false;
            if(okay){
              tile.querySelector('[data-cycle-max-current]').textContent=String(value);
              closeCycleEditor(tile);
            }else info.textContent=message||'保存失败，请刷新后重试';
          }
        }}));
      });
      document.addEventListener('keydown', event=>{
        if(event.key==='Escape'&&usagePopover){
          event.preventDefault();closeUsagePopover(true);return;
        }
        if(event.altKey||event.ctrlKey||event.metaKey||event.repeat)return;
        const el=event.target;
        if(el.closest('input, textarea, select, [contenteditable="true"]'))return;
        const key=event.key.toLowerCase();
        if(key==='escape'&&view){event.preventDefault();select('');return;}

        if(key==='q'){event.preventDefault();location.assign('/run/');return;}
        if(key==='o'&&view&&view!=='events'){event.preventDefault();const value=host.querySelector('[data-tui-pane="secondary"]')?.textContent;
          if(value&&navigator.clipboard?.writeText)navigator.clipboard.writeText(value);return;}
      });
      paintCodexQuota(); // do not clear server-rendered last-good history
      loadCodexQuota();
      setInterval(loadCodexQuota,300000);
      setInterval(refresh,8000);

})();
