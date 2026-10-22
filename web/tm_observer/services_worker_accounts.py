"""Sanitized per-task Worker account assignments for the Services page.

Read-only inventory: all mutations use the existing authenticated task operator.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .worker_console import registered_workers

_SCHEMA = "theory-master/worker-account-preferences/v1"


def snapshot(state: dict) -> dict:
    tasks = []
    for run in state.get("runs", []):
        ident = run.get("observer_id")
        if not isinstance(ident, str) or not re.fullmatch("[a-f0-9]{16}", ident):
            continue
        workers = registered_workers(run)
        if not workers:
            continue
        root = Path(str(run.get("research_root") or "")).resolve()
        path = root / ".theory-master" / "worker_account_preferences.json"
        preferences = {}
        if not path.is_symlink() and (not path.exists() or path.stat().st_size <= 16384):
            try:
                values = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                if values.get("schema") == _SCHEMA and isinstance(values.get("accounts"), dict):
                    preferences = values["accounts"]
            except (OSError, ValueError, UnicodeError):
                pass
        tasks.append({
            "observer_id": ident,
            "task_name": str(run.get("task_name") or run.get("run_id") or "科研任务")[:100],
            "workers": [
                {"worker_id": w["worker_id"], "status": str(w.get("status") or "UNKNOWN"),
                 "current_cycle": str(w.get("current_cycle") or ""),
                 "current_account": w.get("account") if w.get("account") in {"1", "2"} else None,
                 "planned_account": preferences.get(w["worker_id"])
                 if preferences.get(w["worker_id"]) in {"1", "2"} else None}
                for w in workers],
        })
    return {"tasks": tasks}


def section_html() -> str:
    return r"""
<style>
.tm-service-workers{margin:20px 0}
.tm-service-worker-task{border:1px solid var(--tm-border,#525866);border-radius:12px;padding:14px 16px;margin:10px 0}
.tm-service-worker-task h3{font-size:14px;margin:0 0 8px}
.tm-service-worker-row{display:grid;grid-template-columns:90px minmax(130px,1fr) minmax(155px,1fr) minmax(235px,1.3fr);gap:10px;align-items:center;border-top:1px solid var(--tm-border,#525866);padding:10px 0}
.tm-service-worker-row select{padding:7px;color:inherit;background:#242d39;border:1px solid #667085;border-radius:7px;max-width:130px}
.tm-service-worker-row button{padding:8px 11px;border-radius:7px;background:#26466e;border:1px solid #6886b2;color:white;cursor:pointer}
.tm-service-worker-row button:disabled{opacity:.5}
.tm-service-worker-edit{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
@media(max-width:800px){.tm-service-worker-row{grid-template-columns:1fr 1fr}.tm-service-worker-edit{grid-column:1/-1}}
</style>
<section class="tm-service-workers" aria-labelledby="tm-worker-account-title">
<h2 id="tm-worker-account-title">Worker 账号分配</h2>
<p class="small">这里分配 Codex 账号身份，不等于上方的全局默认 Provider。“保存分配”只影响<b>下一次新 Cycle</b>，不改冻结 Cycle。旧调度器需要在安全重启、加载新版代码后才会执行新分配。<b>PAUSED / INTERRUPTED</b> 的 Worker 可显式选择新账号后点击“切换并恢复当前 Cycle”；这将实际继续科研执行，SDK 仍会检查身份、额度与检查点。</p>
<div id="tm-service-worker-list" aria-live="polite">正在读取 Worker…</div>
<p id="tm-service-worker-feedback" role="status" aria-live="polite" class="small"></p>
</section>
<script>
(()=>{
 const area=document.getElementById('tm-service-worker-list');
 const note=document.getElementById('tm-service-worker-feedback');
 if(!area)return;
 let busy=false, snapshot=null;
 const drafts=new Map();
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const label=a=>a==='1'?'Codex 1':a==='2'?'Codex 2':'尚未绑定';
 function render(data){
  if(busy)return;
  const tasks=Array.isArray(data.tasks)?data.tasks:[];
  if(!tasks.length){area.textContent='当前没有已注册的科研 Worker。';return;}
  area.innerHTML=tasks.map(t=>'<div class="tm-service-worker-task"><h3>'+esc(t.task_name)+
   ' <a class="small" href="/runs/'+encodeURIComponent(t.observer_id)+'/">Task Console ↗</a></h3>'+
   t.workers.map(w=>{const chosen=drafts.get(t.observer_id+':'+w.worker_id)??w.planned_account??'keep';
    return '<div class="tm-service-worker-row" data-task="'+esc(t.observer_id)+'" data-worker="'+esc(w.worker_id)+'" data-current="'+esc(w.current_account)+'">'+
    '<strong>'+esc(w.worker_id.replace('worker-','Agent '))+'</strong>'+
    '<span class="small">'+esc(w.status)+' · 当前 '+esc(label(w.current_account))+'</span>'+
    '<span class="small">下个 Cycle：'+esc(w.planned_account?label(w.planned_account):(w.current_account?'保持 '+label(w.current_account):'自动分配'))+'</span>'+
    '<div class="tm-service-worker-edit"><select aria-label="'+esc(w.worker_id)+' 下轮账号">'+
    '<option value="keep" '+(chosen==='keep'?'selected':'')+'>保持当前 / 自动</option>'+
    '<option value="1" '+(chosen==='1'?'selected':'')+'>Codex 1</option>'+
    '<option value="2" '+(chosen==='2'?'selected':'')+'>Codex 2</option></select>'+
    '<button type="button" data-assign>保存分配</button>'+
    (['PAUSED','INTERRUPTED'].includes(w.status) && w.current_account
     ? '<button type="button" data-migrate '+(!['1','2'].includes(chosen)||chosen===w.current_account?'disabled':'')+'>切换并恢复当前 Cycle</button>' : '')+
    '</div></div>';
   }).join('')+'</div>').join('');
 }
 async function refresh(){
  if(busy)return;
  try{const res=await fetch('/services-workers.json',{cache:'no-store'}), data=await res.json();
   if(!res.ok)throw Error(data.error||'读取失败');snapshot=data;render(data);
  }catch(err){if(!snapshot)area.textContent='Worker 信息暂不可用：'+String(err.message||err);}
 }
 area.addEventListener('change',evt=>{
  const select=evt.target.closest?.('.tm-service-worker-row select');
  const row=select?.closest('.tm-service-worker-row');
  if(row)drafts.set(row.dataset.task+':'+row.dataset.worker, select.value);
  const migrate=row?.querySelector('[data-migrate]');
  if(migrate)migrate.disabled=!['1','2'].includes(select.value)||select.value===row.dataset.current;
 });
 area.addEventListener('click',async evt=>{
  const btn=evt.target.closest?.('[data-assign],[data-migrate]');if(!btn||busy||btn.disabled)return;
  const migration=btn.hasAttribute('data-migrate');
  const row=btn.closest('[data-task][data-worker]'), account=row?.querySelector('select')?.value;
  const task=snapshot?.tasks?.find(t=>t.observer_id===row?.dataset.task);
  const worker=task?.workers?.find(w=>w.worker_id===row?.dataset.worker);
  if(!worker||!['keep','1','2'].includes(account))return;
  if(migration){
   if(!['PAUSED','INTERRUPTED'].includes(worker.status)||!['1','2'].includes(account)||
      account===worker.current_account)return;
   if(!confirm('这会把当前冻结 Cycle 的 Codex 身份迁移到 '+label(account)+
    '，并立即 Resume 继续科研（可能产生模型调用）。不会改变全局 Provider。确定继续吗？'))return;
  }else if((worker.planned_account||'keep')===account){
   note.textContent='当前分配未变化。';return;
  }
  busy=true;btn.disabled=true;
  note.textContent=migration?'正在核验安全迁移并恢复当前 Cycle…':'正在保存下一新 Cycle 的账号…';
  try{
   const res=await fetch('/api/run/'+encodeURIComponent(task.observer_id)+'/action',{
    method:'POST',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
    body:JSON.stringify({action:migration?'worker_control':'set_worker_account',
     value:migration?{worker_id:worker.worker_id,operation:'resume',account}:{worker_id:worker.worker_id,account},
     request_id:crypto.randomUUID().replace(/-/g,''),
     expected_cycle:worker.current_cycle,expected_status:worker.status,expected_launch_id:''})
   }),result=await res.json();
   if(!res.ok||!result.ok)throw Error(result.error||'保存未确认');
   drafts.delete(task.observer_id+':'+worker.worker_id);
   note.textContent=migration?
    worker.worker_id+' 已提交迁移到 '+label(account)+' 并 Resume；请检查 Task Console 的实际启动状态。':
    worker.worker_id+' 已保存：'+(account==='keep'?'保持现有选择':label(account))+'，下一新 Cycle 生效。';
  }catch(err){note.textContent='操作未确认：'+String(err.message||err)+'。请刷新状态后核对。';}
  finally{busy=false;btn.disabled=false;await refresh();}
 });
 refresh();setInterval(refresh,12000);
})();
</script>
"""