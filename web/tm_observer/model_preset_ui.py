"""One clickable six-stage preset editor in Task Console's MODEL heading.

UI rows and model catalog are projected from the authoritative profile TOMLs.
The SDK action writes Worker-local choices for the next safe role invocation.
"""
from __future__ import annotations

import html
import json


PRESET_LABELS = {
    "test": "Codex 一档",
    "codex": "Codex 二档",
    "advanced": "Codex 三档",
}
ROLE_LABELS = {
    "research_lead": "Research Lead",
    "challenger": "Challenger",
    "investigator": "Investigator",
    "literature": "Literature",
    "judge": "Judge",
    "reporter": "Reporter",
}


def dialog_html() -> str:
    from theory_master.backend_config import build_model_profile

    esc = lambda text: html.escape(str(text), quote=True)
    labels = PRESET_LABELS
    options = "".join('<option value="' + esc(key) + '">' + esc(label) + '</option>'
                      for key, label in labels.items())
    panels = []
    for key in labels:
        routes = build_model_profile(key)
        rows = "".join('<tr data-role="' + esc(role) + '"><th scope="row">' + esc(label) + '</th><td data-preset-model>'
                       + esc(routes[role].model) + '</td><td data-preset-effort>'
                       + esc(routes[role].reasoning_effort or "medium") + '</td></tr>'
                       for role, label in ROLE_LABELS.items())
        panels.append('<section data-model-preset-panel="' + esc(key) + '" hidden>'
                      '<table><thead><tr><th>AGENT</th><th>MODEL</th><th>EFFORT</th>'
                      '</tr></thead><tbody>' + rows + '</tbody></table>'
                      '<p data-model-compat aria-live="polite">模型可用性尚未验证</p></section>')
    editor_rows = []
    for role, label in ROLE_LABELS.items():
        editor_rows.append(
            '<tr data-template-role="' + esc(role) + '"><th>' + esc(label) + '</th>'
            '<td><input type="text" data-template-model maxlength="90" aria-label="' + esc(label) + ' 模型"></td>'
            '<td><select data-template-effort aria-label="' + esc(label) + ' Effort">'
            + ''.join('<option value="' + effort + '">' + effort + '</option>'
                      for effort in ("low", "medium", "high", "xhigh"))
            + '</select></td></tr>')
    return ('''<dialog id="tm-model-preset-dialog" class="tm-route-save-dialog tm-model-preset-dialog"
                aria-label="修改整套六阶段模型档位">
      <form method="dialog">
        <strong>六阶段模型档位</strong>
        <p>每档包含六个 Agent 的模型和推理强度。保存后立即更新未在执行的 Agent 的模型配置；
           正在执行的 Agent 保持本次调用使用的模型，结束后再切换。不必等待下一个 Cycle。
           保存整档会替换该 Worker 原有的逐阶段自定义覆盖。点击“⚙ 设置”可永久修改 Codex 三档的默认模型，不写回开源配置。</p>
        <div class="tm-preset-select-row"><select data-model-preset-select aria-label="选择六阶段模型档位">''' + options + '''</select>
        <button type="button" data-model-preset-settings title="永久修改当前 Codex 档位">⚙ 设置</button></div>
        ''' + "".join(panels) + '''
        <section data-model-template-editor hidden>
          <p>永久保存当前 Codex 档位的六阶段模型和 Effort 到用户私有配置。已运行中的 Agent 保留当前调用使用的模型。</p>
          <table><thead><tr><th>AGENT</th><th>MODEL</th><th>EFFORT</th></tr></thead><tbody>''' + "".join(editor_rows) + '''</tbody></table>
          <button type="button" data-template-save>永久保存此档</button>
          <button type="button" data-template-cancel>返回预览</button>
        </section>
        <div class="tm-model-catalog-row"><button type="button" data-model-catalog-check>检查 Codex 模型</button>
          <small data-model-catalog-result aria-live="polite">读取当前账号可见的模型目录（不发起付费模型调用；不保证实际请求成功）。</small>
        </div>
        <p data-model-preset-hint aria-live="polite"></p>
        <p data-model-preset-result role="status" aria-live="polite"></p>
        <footer><button type="submit" value="cancel">取消</button>
                <button type="button" data-model-preset-save>保存</button></footer>
      </form>
    </dialog>''')


CSS = """
button.tm-model-preset-tier[data-model-preset-open] {
  font:inherit; font-size:11px; color:#e2e8f0; background:transparent;
  cursor:pointer; text-align:left; }
button.tm-model-preset-tier[data-model-preset-open]:hover {
  background:#334155; border-color:#93c5fd; }
button.tm-model-preset-tier[data-model-preset-open]:focus-visible {
  outline:2px solid #93c5fd; outline-offset:3px; }
.tm-model-preset-dialog { width:min(630px,calc(100vw - 26px)); }
.tm-model-preset-dialog select {
  background:#111c2b; color:#fff; border:1px solid #64748b;
  border-radius:8px; padding:9px; width:100%; max-width:290px; }
.tm-model-preset-dialog table {
  width:100%; font-size:12px; margin-top:14px; border-collapse:collapse; }
.tm-model-preset-dialog table th, .tm-model-preset-dialog table td {
  border-bottom:1px solid #42516b; text-align:left; padding:7px 6px; }
.tm-model-preset-dialog .tm-preset-select-row,.tm-model-preset-dialog .tm-model-catalog-row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:8px; }
.tm-model-preset-dialog [data-model-template-editor][hidden] { display:none; }
.tm-model-preset-dialog [data-template-model] { width:100%; max-width:250px; padding:7px; color:white; background:#111c2b; border:1px solid #64748b; border-radius:6px; }
.tm-model-preset-dialog [data-model-compat] { color:#fcd34d; }
.tm-model-preset-dialog [data-model-preset-panel][hidden] { display:none; }
.tm-model-preset-dialog [data-model-preset-result] { color:#fcd34d; }
"""


def script(ident: str, selected_worker: str | None) -> str:
    # Script belongs to the stable parent of the 8-second-refreshed Console;
    # the trigger is delegated through document, not attached to discarded rows.
    source = r"""<script>(function() {
      const dialog=document.getElementById('tm-model-preset-dialog');
      const live=document.getElementById('tm-run-live');
      const choice=dialog?.querySelector('[data-model-preset-select]');
      const selectedWorker=__WORKER__;
      if(!dialog||!live||!choice)return;
      let openedBy=null;
      function preview(){
        dialog.querySelectorAll('[data-model-preset-panel]').forEach(panel=>{
          panel.hidden=panel.dataset.modelPresetPanel!==choice.value;
        });
        const editor=dialog.querySelector('[data-model-template-editor]');
        editor.hidden=true;
        dialog.querySelector('[data-model-preset-save]').hidden=false;
        dialog.querySelector('[data-model-preset-settings]').disabled=
          !['test','codex','advanced'].includes(choice.value);
        renderModelChecks();
      }

      const editor=dialog.querySelector('[data-model-template-editor]');
      const status=dialog.querySelector('[data-model-preset-result]');
      const catalogLabel=dialog.querySelector('[data-model-catalog-result]');
      let catalog=null;
      function postControl(action,value){
        const state=live.querySelector('.tm-worker-controls[data-worker-id="'+selectedWorker+'"]')
          || live.querySelector('[data-tui-screen]');
        if(!state)return Promise.reject(new Error('运行状态缺失，请刷新页面'));
        return fetch('/api/run/__IDENT__/action',{
          method:'POST', headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
          body:JSON.stringify({
            action, value,
            request_id:crypto.randomUUID().replace(/-/g,''),
            expected_cycle:state.dataset.cycle||state.dataset.tuiCycle||'',
            expected_status:state.dataset.status||state.dataset.tuiStatus||'',
            expected_launch_id:''
          })
        }).then(async response=>{
          const data=await response.json();
          if(!response.ok||!data.ok)throw new Error(data.error||'操作失败');
          return data.outcome;
        });
      }
      function renderModelChecks(){
        dialog.querySelectorAll('[data-model-preset-panel]').forEach(panel=>{
          const tip=panel.querySelector('[data-model-compat]');
          if(panel.dataset.modelPresetPanel.startsWith('y-')){
            tip.textContent='外部 Provider 需要自行验证模型可用性';return;
          }
          if(catalog?.status!=='catalog_read'){
            tip.textContent='未验证：该 Codex 账号的模型目录尚未确认';return;
          }
          const warnings=[];
          panel.querySelectorAll('tbody tr').forEach(row=>{
            const model=row.querySelector('[data-preset-model]').textContent.trim();
            const effort=row.querySelector('[data-preset-effort]').textContent.trim();
            if(!catalog.models.includes(model))warnings.push(model+' 未在账号目录列出');
            else {
              const supported=(catalog.efforts||{})[model]||[];
              if(supported.length&&!supported.includes(effort))warnings.push(model+' 不支持 '+effort);
            }
          });
          tip.textContent=warnings.length?'警告：'+[...new Set(warnings)].join('；')+
            '；请修改档位后再使用':'目录列出六阶段模型及 Effort；但实际账号调用权限仍未经验证';
        });
      }
      async function checkCodexModels(){
        const btn=dialog.querySelector('[data-model-catalog-check]');
        btn.disabled=true;
        catalogLabel.textContent='读取账号模型目录中…';
        try{
          catalog=await postControl('inspect_codex_models',{worker_id:selectedWorker});
          catalogLabel.textContent=catalog.account+'：'+(catalog.reason||'未知')+
            '。目录信息不能保证实际调用成功。';
        }catch(error){
          catalog=null;
          catalogLabel.textContent='无法验证账号模型：'+String(error.message||error);
        }finally{btn.disabled=false;renderModelChecks();}
      }
      dialog.querySelector('[data-model-catalog-check]').addEventListener('click',checkCodexModels);
      dialog.querySelector('[data-model-preset-settings]').addEventListener('click',()=>{
        if(!['test','codex','advanced'].includes(choice.value))return;
        const panel=dialog.querySelector('[data-model-preset-panel="'+choice.value+'"]');
        panel.querySelectorAll('tbody tr').forEach(row=>{
          const dst=editor.querySelector('[data-template-role="'+row.dataset.role+'"]');
          dst.querySelector('[data-template-model]').value=row.querySelector('[data-preset-model]').textContent.trim();
          dst.querySelector('[data-template-effort]').value=row.querySelector('[data-preset-effort]').textContent.trim();
        });
        dialog.querySelectorAll('[data-model-preset-panel]').forEach(panel=>panel.hidden=true);
        editor.hidden=false;
        dialog.querySelector('[data-model-preset-save]').hidden=true;
      });
      dialog.querySelector('[data-template-cancel]').addEventListener('click',preview);
      dialog.querySelector('[data-template-save]').addEventListener('click',async event=>{
        const btn=event.currentTarget;
        const roles={};
        editor.querySelectorAll('[data-template-role]').forEach(row=>{
          roles[row.dataset.templateRole]={
            model:row.querySelector('[data-template-model]').value.trim(),
            reasoning_effort:row.querySelector('[data-template-effort]').value
          };
        });
        if(Object.values(roles).some(row=>!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,89}$/.test(row.model))){
          status.textContent='模型 ID 不能为空，且只能包含字母、数字、点、斜杠、下划线或短横线';
          return;
        }
        btn.disabled=true;
        try{
          await postControl('save_model_tier_template',{profile:choice.value,roles});
          status.textContent='档位模板已永久保存，下一次安全调用开始使用';
          dialog.close();location.reload();
        }catch(error){
          status.textContent='保存失败：'+String(error.message||error);
        }finally{btn.disabled=false;}
      });
      live.addEventListener('click',event=>{
        const trigger=event.target.closest('[data-model-preset-open]');
        if(!trigger||dialog.open)return;
        openedBy=trigger;
        choice.value=trigger.dataset.modelPresetProfile||'test';
        preview();
        const button=dialog.querySelector('[data-model-preset-save]');
        button.disabled=!selectedWorker;
        const label=dialog.querySelector('[data-model-preset-hint]');
        label.textContent=selectedWorker
          ?'当前：'+trigger.textContent.trim()+'。仅修改 Agent '+selectedWorker.replace('worker-','')+'。未运行的角色立即采用新配置，运行中的角色结束本次调用后切换。'
          :'请先在上方选择 Agent 1 或 Agent 2，然后再修改整套档位。';
        dialog.querySelector('[data-model-preset-result]').textContent='';
        dialog.showModal();
        if(!catalog)checkCodexModels();
      });
      choice.addEventListener('change',preview);
      dialog.addEventListener('close',()=>{
        if(openedBy?.isConnected)openedBy.focus();
        openedBy=null;
      });
      dialog.querySelector('[data-model-preset-save]')?.addEventListener('click',async event=>{
        if(!selectedWorker)return;
        const submit=event.currentTarget;
        const message=dialog.querySelector('[data-model-preset-result]');
        const state=live.querySelector('.tm-worker-controls[data-worker-id="'+selectedWorker+'"]')
          || live.querySelector('[data-tui-screen]');
        if(!state){message.textContent='无法读取当前 Agent 运行状态，请刷新页面';return;}
        const old=submit.textContent;
        submit.disabled=true;
        submit.textContent='正在保存…';
        try{
          const request={
            action:'set_worker_profile',
            value:{worker_id:selectedWorker, profile:choice.value},
            request_id:crypto.randomUUID().replace(/-/g,''),
            expected_cycle:state.dataset.cycle||state.dataset.tuiCycle||'',
            expected_status:state.dataset.status||state.dataset.tuiStatus||'',
            expected_launch_id:''
          };
          const response=await fetch('/api/run/__IDENT__/action',{
            method:'POST',
            headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
            body:JSON.stringify(request)
          });
          const result=await response.json();
          if(!response.ok||!result.ok)throw new Error(result.error||'保存档位失败');
          message.textContent='已保存。未运行的角色下一次调用使用新档位；运行中的角色本次调用结束后使用新档位';
          dialog.close();
          location.reload();
        }catch(error){
          message.textContent='保存失败：'+String(error.message||error);
        }finally{
          submit.disabled=false;
          submit.textContent=old;
        }
      });
    })();</script>"""
    return source.replace("__IDENT__", json.dumps(ident)[1:-1]).replace(
        "__WORKER__", json.dumps(selected_worker, ensure_ascii=False))
