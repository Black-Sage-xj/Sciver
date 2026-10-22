"""Isolated Native Goal reader/editor, mounted outside the 8s Run pane poll."""
from __future__ import annotations

import json


def trigger() -> str:
    return ('<button type="button" class="tm-native-goal-trigger" data-goal-open '
            'aria-haspopup="dialog" aria-controls="tm-native-goal-dialog" '
            'title="查看 Investigator 本次科研目标和完成判据">查看 Goal ↗</button>')


def modal(observer_id: str) -> str:
    quoted = json.dumps(observer_id)
    return '''<style>
.tm-native-goal-trigger { margin-left:auto; font:inherit; font-size:11px; padding:4px 9px; color:#bfdbfe; background:#1f2937; border:1px solid #43516a; border-radius:7px; cursor:pointer; }
.tm-native-goal-trigger:hover { background:#303c50; }
.tm-native-goal-trigger:focus-visible { outline:2px solid #93c5fd; outline-offset:2px; }
.tm-investigator-goal-field { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;
 gap:12px; border:1px solid #4b648d; border-radius:9px; background:rgba(44,75,117,.20);
 padding:12px 13px; margin:10px 0 12px; }
.tm-investigator-goal-intro { flex:1 1 190px; }
.tm-investigator-goal-intro strong { color:#bfdbfe; font-size:14px; }
.tm-investigator-goal-intro p { color:#abbdd6; font-size:11px; line-height:1.6; margin:4px 0 0; }
.tm-native-goal-head { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
.tm-native-goal-head .tm-tui-section-title { margin:0; }
.tm-native-goal-dialog { position:fixed; width:min(850px,calc(100vw - 30px)); max-height:calc(100dvh - 32px); overflow:auto; background:#191b21; color:#e4e4e7; border:1px solid #596375; border-radius:12px; box-shadow:0 20px 60px #0009; padding:19px; }
.tm-native-goal-dialog::backdrop { background:rgba(0,0,0,.76); }
.tm-native-goal-dialog header { display:flex; align-items:center; justify-content:space-between; gap:18px; }
.tm-native-goal-dialog header strong { font-size:17px; }
.tm-native-goal-dialog button { cursor:pointer; }
.tm-native-goal-close { border:1px solid #505361; color:#eee; border-radius:7px; background:#30323c; padding:7px 11px; }
.tm-native-goal-dialog p { font-size:12px; line-height:1.65; color:#cbd5e1; }
.tm-native-goal-warning { border-left:3px solid #eab308; padding:8px 12px; background:#282415; color:#fcd34d !important; }
.tm-native-goal-source { font-size:11px !important; color:#94a3b8 !important; }
.tm-native-goal-retry { padding:6px 10px; color:#bfdbfe; background:#22314c; border:1px solid #6685b9; border-radius:7px; }
.tm-native-goal-retry[hidden] { display:none !important; }
.tm-native-goal-dialog label { display:block; font-size:12px; font-weight:650; margin:13px 0 6px; }
/* Editing never occupies dialog space until the reader explicitly opens it. */
.tm-native-goal-dialog [hidden] { display:none !important; }
.tm-native-goal-read-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:13px 0 6px; }
.tm-native-goal-read-head strong { font-size:12px; }
.tm-native-goal-begin-edit { padding:6px 12px; border:1px solid #6685b9; border-radius:7px; color:#bfdbfe; background:#22314c; }
.tm-native-goal-begin-edit:hover { background:#33496a; }
.tm-native-goal-begin-edit:disabled { opacity:.45; cursor:not-allowed; }
.tm-native-goal-cancel { padding:9px 14px; color:#e4e4e7; background:#2d3039; border:1px solid #596375; border-radius:7px; }
.tm-native-goal-original { max-height:31dvh; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; padding:12px; font:12px/1.65 ui-monospace,Menlo,monospace; background:#121419; border:1px solid #3f4554; border-radius:8px; }
.tm-native-goal-edit { width:100%; min-height:175px; resize:vertical; box-sizing:border-box; font:12px/1.6 ui-monospace,Menlo,monospace; color:#e4e4e7; background:#11141b; border:1px solid #596375; border-radius:8px; padding:11px; }
.tm-native-goal-ack { display:flex !important; align-items:flex-start; gap:8px; font-weight:400 !important; line-height:1.6; }
.tm-native-goal-ack input { margin-top:3px; }
.tm-native-goal-actions { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }
.tm-native-goal-save { color:#fff; background:#38588b; border:1px solid #6685b9; border-radius:7px; padding:9px 14px; }
.tm-native-goal-save:disabled { opacity:.45; cursor:not-allowed; }
.tm-native-goal-status { min-height:18px; font-size:12px; color:#fcd34d; }
</style>
<dialog class="tm-native-goal-dialog" id="tm-native-goal-dialog" aria-label="Investigator Native Goal 原文及修订草稿">
<header><strong>Investigator · Native Goal</strong><button type="button" class="tm-native-goal-close" data-goal-close>关闭</button></header>
<p data-goal-source class="tm-native-goal-source">正在查询 SDK 保存的 Goal 输入记录…</p>
<button type="button" class="tm-native-goal-retry" data-goal-retry hidden>重新读取</button>
<div class="tm-native-goal-read-head"><strong>已提交的原始 Goal（只读）</strong><button type="button" class="tm-native-goal-begin-edit" data-goal-begin-edit hidden>编辑</button></div>
<pre data-goal-original class="tm-native-goal-original">正在读取…</pre>
<p data-goal-revision>没有已保存的草稿</p>
<div data-goal-editor hidden>
  <p class="tm-native-goal-warning">当前 Native Goal 与冻结科学目标不可在执行中直接覆盖。这里只保存人工修订草稿并登记历史：<strong>不会修改当前 Goal，也不会自动注入下一 Attempt</strong>。</p>
  <label for="tm-native-goal-edit">修订草稿（最多 4000 字符）</label>
  <textarea id="tm-native-goal-edit" data-goal-edit class="tm-native-goal-edit" maxlength="4000" disabled></textarea>
  <label class="tm-native-goal-ack"><input type="checkbox" data-goal-ack disabled>我确认：本次仅保存草稿，不会影响当前科研执行，亦不自动用于后续 Attempt。</label>
  <div class="tm-native-goal-actions"><button type="button" class="tm-native-goal-cancel" data-goal-cancel>取消编辑</button><button type="button" class="tm-native-goal-save" data-goal-save disabled>保存未生效草稿</button></div>
</div>
<p class="tm-native-goal-status" data-goal-status role="status" aria-live="polite"></p>
</dialog>
<script>(function(){
'use strict';
const ident=TM_NATIVE_GOAL_ID;
const dialog=document.getElementById('tm-native-goal-dialog');
if(!dialog)return;
const get=name=>dialog.querySelector('[data-goal-'+name+']');
const textarea=get('edit'),ack=get('ack'),save=get('save'),retryButton=get('retry');
const editButton=get('begin-edit'),editor=get('editor'),cancel=get('cancel');
const MAX_GOAL_READ_RETRIES=8,GOAL_READ_RETRY_MS=1500;
let latest=null,loading=false,editing=false,opener=null,loadGeneration=0;
const endpoint='/api/run/'+ident+'/goal';
const showStatus=(text)=>{get('status').textContent=text;};
function updateSave(){
 save.disabled=loading||!editing||!latest?.editable_as_draft_only||!ack.checked||!textarea.value.trim();
 editButton.disabled=loading||!latest?.editable_as_draft_only;
 cancel.disabled=loading;
 retryButton.disabled=loading;
}
function setEditing(active){
 editing=active;
 editor.hidden=!active;
 editButton.hidden=active||!latest?.editable_as_draft_only;
 ack.checked=false;
 if(active){
  textarea.value=(latest.draft?.base_matches_goal&&typeof latest.draft?.text==='string')
   ?latest.draft.text:latest.objective;
  textarea.focus();
 }
 updateSave();
}
ack.addEventListener('change',updateSave);
textarea.addEventListener('input',updateSave);
editButton.addEventListener('click',()=>{
 if(loading||!latest?.editable_as_draft_only)return;
 showStatus('');setEditing(true);
});
cancel.addEventListener('click',()=>{
 if(loading)return;
 setEditing(false);showStatus('已取消编辑，未保存本次更改。');
});
retryButton.addEventListener('click',()=>{
 if(loading)return;
 showStatus('');loadGoal();
});
function showPendingGoal(attempt,generation){
 get('original').textContent='当前 Cycle 尚未记录 Investigator 的实际 Goal/Prompt。';
 get('source').textContent='等待当前 Cycle 的真实输入记录…';
 if(dialog.open&&attempt<MAX_GOAL_READ_RETRIES){
  showStatus('正在等待 Investigator 输入记录（'+(attempt+1)+'/'+MAX_GOAL_READ_RETRIES+'）；不会借用其他 Cycle 的内容。');
  window.setTimeout(()=>{if(dialog.open&&generation===loadGeneration)loadGoal(attempt+1,generation);},GOAL_READ_RETRY_MS);
 }else{
  retryButton.hidden=false;
  showStatus('当前 Cycle 尚未记录原始 Goal；仍禁止保存草稿。请稍后点击“重新读取”。');
 }
}
async function loadGoal(attempt=0,generation=null){
 if(generation===null)generation=++loadGeneration;
 loading=true; latest=null;setEditing(false);textarea.value='';textarea.disabled=true;ack.disabled=true;retryButton.hidden=true;
 get('original').textContent='正在读取…';get('source').textContent='正在查询真实记录…';
 get('revision').textContent='';showStatus('');updateSave();
 try{
  const resp=await fetch(endpoint,{cache:'no-store'});
  const payload=await resp.json();
  if(generation!==loadGeneration)return;
  if(!resp.ok){
   if(payload.code==='goal_input_pending'){showPendingGoal(attempt,generation);return;}
   throw new Error(payload.error||('HTTP '+resp.status));
  }
  latest=payload;retryButton.hidden=true;
  get('original').textContent=payload.objective;
  const isNative=payload.mode==='native';
  get('source').textContent='Cycle '+payload.cycle+' · SDK 输入登记时间 '+(payload.recorded_at||'未记录')+' · '+payload.source;
  textarea.value=(payload.draft?.base_matches_goal&&typeof payload.draft?.text==='string')?payload.draft.text:payload.objective;
  textarea.disabled=!isNative;ack.disabled=!isNative;
  get('revision').textContent=!isNative?'历史运行使用普通 Investigator Prompt，而非 Native Goal；仅能查看当时的实际指令，不提供在线编辑。':payload.draft
   ? ('草稿版本 '+payload.draft.revision+' · 保存于 '+payload.draft.updated_at+
      (payload.draft.base_matches_goal?' · 未生效':' · 原始 Goal 已改变，旧稿不自动沿用；编辑框已改为新 Goal'))
   :'尚未保存修订草稿（草稿与实际运行的 Goal 相互独立）';
 }catch(error){
  if(generation!==loadGeneration)return;
  get('original').textContent='暂无法显示完整的原始 Goal';
  get('source').textContent='读取失败：'+String(error.message||error);
  retryButton.hidden=false;
  showStatus('未获取到原始记录，禁止保存草稿。');
 }finally{if(generation===loadGeneration){loading=false;setEditing(false);}}
}
document.addEventListener('click',event=>{
 const trigger=event.target.closest('[data-goal-open]');
 if(trigger){opener=trigger;dialog.showModal();loadGoal();return;}
 if(event.target.closest('[data-goal-close]')&&dialog.open)dialog.close();
});
dialog.addEventListener('close',()=>{loadGeneration++;setEditing(false);if(opener?.isConnected)opener.focus();opener=null;});
save.addEventListener('click',async()=>{
 if(save.disabled||!latest)return;
 if(!window.confirm('只保存未生效 Goal 草稿，不更改当前 Native Goal，亦不自动用于下一 Attempt。确定保存吗？'))return;
 loading=true;updateSave();showStatus('正在保存草稿…');
 try{
  const resp=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},
   body:JSON.stringify({cycle:latest.cycle,text:textarea.value,
    expected_goal_sha256:latest.sha256,expected_revision:latest.draft?.revision||0,
    acknowledge_draft_only:true})});
  const payload=await resp.json();
  if(!resp.ok)throw new Error(payload.error||('HTTP '+resp.status));
  latest.draft={text:textarea.value,revision:payload.revision,updated_at:payload.saved_at,base_matches_goal:true};
  get('revision').textContent='未生效草稿 · 版本 '+payload.revision+' · '+payload.saved_at;
  setEditing(false);showStatus('草稿已保存（未生效），不影响当前或下一 Attempt。');
 }catch(error){showStatus('保存失败：'+String(error.message||error)+'。请核查后重试。');}
 finally{loading=false;updateSave();}
});
})();</script>'''.replace('TM_NATIVE_GOAL_ID', quoted)
