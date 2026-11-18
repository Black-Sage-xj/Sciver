"""Notebook actions shared by the live graph and its Markdown reader."""

VAULT_CSS = r"""
<style>
.ex-vault{box-sizing:border-box;width:min(720px,calc(100vw - 28px));max-height:88dvh;overflow:auto;padding:22px;border:1px solid var(--tm-border,#475569);border-radius:12px;background:var(--tm-panel,#191f2c);color:var(--tm-text,#e2e8f0);box-shadow:0 18px 60px #0007;font-size:13px;line-height:1.65}
.ex-vault::backdrop{background:#0008}
.ex-vault header{display:flex;align-items:center;justify-content:space-between;gap:12px}
.ex-vault h2{margin:0;font-size:20px}.ex-vault h3{margin:0 0 9px;font-size:14px}
.ex-vault section{padding-top:17px;margin-top:17px;border-top:1px solid var(--tm-border,#475569)}
.ex-vault p{margin:8px 0}.ex-vault .ex-vault-note{color:var(--tm-muted,#a1a1aa);font-size:12px}
.ex-vault-path{display:block;padding:9px 11px;border:1px solid var(--tm-border,#475569);border-radius:6px;overflow-wrap:anywhere;white-space:pre-wrap;user-select:text;font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace}
.ex-vault-actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:9px 0}
.ex-vault button,.ex-vault select,.ex-vault input{max-width:100%;box-sizing:border-box;min-height:32px;padding:5px 9px;border:1px solid var(--tm-border,#475569);border-radius:6px;background:var(--tm-panel,#191f2c);color:inherit;font:inherit}
.ex-vault button{cursor:pointer}.ex-vault button:disabled{cursor:default;opacity:.5}
.ex-vault input{width:100%;margin:5px 0}.ex-vault select{min-width:160px}
.ex-vault [hidden]{display:none!important}.ex-vault-status{min-height:22px;margin-top:12px;overflow-wrap:anywhere}
.ex-vault-status[data-error="true"]{color:#fb7185}.ex-vault-directories{max-height:180px;overflow:auto;display:flex;flex-direction:column;gap:5px;margin:8px 0}
.ex-vault-directories button{text-align:left;overflow-wrap:anywhere}
.ex-document-tools{margin:12px 0 18px;padding:10px 12px;border:1px solid var(--read-border,var(--tm-border,#475569));border-radius:7px;font-size:12px;color:var(--read-muted,var(--tm-muted,#a1a1aa))}
.ex-document-tools .ex-document-path{display:block;margin-bottom:6px;overflow-wrap:anywhere;user-select:text;font:11px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace}
.ex-document-tools .ex-ref{margin:3px 5px 0 0;font-size:12px}
.ex-document-tools .ex-document-action-status{display:block;margin-top:6px;overflow-wrap:anywhere}
.ex-document-tools [hidden]{display:none!important}
</style>
"""

VAULT_HTML = r"""
<dialog id="ex-vault" class="ex-vault" aria-labelledby="ex-vault-title">
 <header><h2 id="ex-vault-title">Markdown 笔记库</h2><form method="dialog"><button type="submit" autofocus>关闭</button></form></header>
 <p class="ex-vault-note">图谱关联原有 Markdown。可在 Obsidian 阅读和编辑，Canvas 随图谱自动更新。</p>
 <span id="ex-vault-path" class="ex-vault-path">读取中…</span>
 <div class="ex-vault-actions">
  <button id="ex-vault-copy" type="button" disabled>复制库路径</button>
  <button id="ex-vault-folder" type="button" hidden>打开文件夹</button>
  <button id="ex-vault-obsidian" type="button" hidden>用 Obsidian 打开</button>
  <button id="ex-vault-download" type="button">下载整个笔记库…</button>
 </div>
 <p id="ex-vault-host-note" class="ex-vault-note"></p>
 <details><summary>本机 Obsidian 库名称</summary>
  <label for="ex-vault-obsidian-name">已在本机 Obsidian 打开的库名称</label>
  <input id="ex-vault-obsidian-name" type="text" autocomplete="off" spellcheck="false"/>
  <p class="ex-vault-note">名称只保存在当前浏览器。远程使用时，先下载并解压笔记库，再将文件夹作为库打开。</p>
 </details>
 <section>
  <h3>Canvas</h3>
  <span id="ex-vault-canvas" class="ex-vault-path">读取中…</span>
  <p id="ex-vault-updated" class="ex-vault-note"></p>
  <div class="ex-vault-actions"><button id="ex-vault-refresh" type="button">立即刷新</button><button id="ex-vault-canvas-download" type="button">另存全图 Canvas…</button></div>
  <label for="ex-vault-branch">指定猜想及其后续分支</label>
  <div class="ex-vault-actions"><select id="ex-vault-branch" aria-label="选择导出的猜想分支"></select><button id="ex-vault-create-branch" type="button">在库根目录生成分支 Canvas</button><button id="ex-vault-download-branch" type="button">另存分支 Canvas…</button></div>
  <p class="ex-vault-note">Canvas 单向生成；本地调整布局后可另存副本。Markdown 始终引用库内同一份文件。</p>
 </section>
 <section>
  <h3>输出位置</h3>
  <p class="ex-vault-note">下载和另存使用本机保存位置。下方可整体移动服务端笔记库，后续输出继续保存在新位置。</p>
  <details id="ex-vault-location"><summary>选择笔记库目录</summary>
   <p id="ex-vault-move-note" class="ex-vault-note">任务暂停后可移动笔记库。</p>
   <label for="ex-vault-destination">目标笔记库目录（完整路径，也可填写新目录）</label>
   <input id="ex-vault-destination" type="text" autocomplete="off" spellcheck="false"/>
   <div class="ex-vault-actions"><button id="ex-vault-browse" type="button">浏览服务端文件夹</button><button id="ex-vault-move" type="button" disabled>移动笔记库到此目录</button></div>
   <div id="ex-vault-directory-picker" hidden>
    <div class="ex-vault-actions"><button id="ex-vault-up" type="button">上一级</button><button id="ex-vault-use-directory" type="button">选择当前目录</button></div>
    <span id="ex-vault-directory-path" class="ex-vault-path"></span>
    <div id="ex-vault-directories" class="ex-vault-directories" aria-label="服务端子文件夹"></div>
   </div>
  </details>
 </section>
 <p id="ex-vault-status" class="ex-vault-status" role="status" aria-live="polite"></p>
</dialog>
"""

VAULT_SCRIPT = r"""
globalThis.SciverVaultUI={create({getTask,getBranch,getHypotheses}){
 const byId=id=>document.getElementById(id),dialog=byId('ex-vault');
 if(!dialog)return null;
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const endpoint=(task,action)=>'/api/canvas-md/tasks/'+encodeURIComponent(task)+'/'+action;
 let metadata=null,metadataTask='',epoch=0,directory=null,metadataRequest=null,metadataSerial=0,directorySerial=0;
 const controls=new Set();
 const current=(task,revision)=>getTask()===task&&epoch===revision;
 function status(message,error=false){const out=byId('ex-vault-status');out.textContent=message;out.dataset.error=String(error)}
 async function request(task,action,options){
  const response=await fetch(endpoint(task,action),{cache:'no-store',credentials:'same-origin',...options});
  const value=await response.json();if(!response.ok||value.ok===false)throw Error(value.error||value.canvases?.find(row=>row.error)?.error||'Canvas 尚未刷新成功，请稍后重试');return value;
 }
 function absolute(path){return metadata&&metadataTask===getTask()?metadata.vault_root.replace(/\/$/,'')+'/'+path:path}
 function updateDocumentPaths(){
  for(const node of document.querySelectorAll('[data-vault-document-path]'))node.textContent=absolute(node.dataset.vaultDocumentPath);
  for(const node of document.querySelectorAll('[data-vault-document-action="folder"]'))node.hidden=metadata?.local_actions_available!==true;
 }
 function renderMetadata(){
  if(!metadata)return;
  byId('ex-vault-path').textContent=metadata.vault_root;
  byId('ex-vault-copy').disabled=false;
  byId('ex-vault-canvas').textContent=metadata.canvas_path||metadata.canvas_relative_path||'尚未生成';
  const canvasState=(metadata.canvases||[]).find(row=>row.canvas_path===metadata.canvas_path);
  const pending=metadata.status&&metadata.status!=='ready';
  byId('ex-vault-updated').textContent=(pending?'等待刷新'+(canvasState?.error?'：'+canvasState.error:'')+'。':'')+(metadata.updated_at?'最近成功刷新：'+metadata.updated_at:'');
  const local=metadata.local_actions_available===true;
  byId('ex-vault-folder').hidden=!local;
  byId('ex-vault-obsidian').hidden=false;
  if(!byId('ex-vault-obsidian-name').value){
   let saved='';try{saved=window.localStorage?.getItem('sciver-obsidian-vault:'+getTask())||''}catch{}
   byId('ex-vault-obsidian-name').value=saved||metadata.vault_root.replace(/\/$/,'').split('/').pop();
  }
  byId('ex-vault-host-note').textContent=local?'文件夹和 Obsidian 在服务所在的本机打开。':'笔记库位于服务端。下载到本机后，可用 Obsidian 打开整个文件夹。';
  byId('ex-vault-move').disabled=metadata.move_available===false;
  byId('ex-vault-move-note').textContent=metadata.move_disabled_reason||'任务暂停或停止时可移动整个笔记库。';
  if(!byId('ex-vault-destination').value)byId('ex-vault-destination').value=metadata.vault_root;
  updateDocumentPaths();
 }
 function refreshMetadata(force=false){
  const task=getTask(),revision=epoch;
  if(!force&&metadataTask===task&&metadata)return Promise.resolve(metadata);
  if(!force&&metadataRequest)return metadataRequest;
  const serial=++metadataSerial;
  const pending=request(task,'vault').then(value=>{
   if(current(task,revision)&&serial===metadataSerial){metadata=value;metadataTask=task;renderMetadata()}
   return value;
  }).finally(()=>{if(metadataRequest===pending)metadataRequest=null});
  metadataRequest=pending;return pending;
 }
 function branches(){
  const select=byId('ex-vault-branch'),active=getBranch();select.replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='选择猜想 H';select.append(placeholder);
  for(const node of getHypotheses()){
   const option=document.createElement('option');option.value=node.id;option.textContent=node.id+' · '+(node.label||node.id);select.append(option);
  }
  if(active)select.value=active;
  select.onchange=()=>{const disabled=!/^H-\d+$/.test(select.value);byId('ex-vault-create-branch').disabled=disabled;byId('ex-vault-download-branch').disabled=disabled};select.onchange();
 }
 async function copy(value){
  if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(value);return}catch{}}
  const input=document.createElement('textarea');input.value=value;input.style.cssText='position:fixed;opacity:0;';document.body.append(input);input.select();
  try{if(!document.execCommand('copy'))throw Error('请选中文件路径后复制')}finally{input.remove()}
 }
 async function save(task,action,suggestedName,mime,extension){
  const revision=epoch;
  // Open the picker during the click's user activation, before any network wait.
  const handle=typeof window.showSaveFilePicker==='function'?await window.showSaveFilePicker({suggestedName,types:[{description:extension==='md'?'Markdown':extension==='canvas'?'Canvas':'笔记库 ZIP',accept:{[mime]:['.'+extension]}}]}):null;
  if(!current(task,revision))return false;
  const response=await fetch(endpoint(task,action),{cache:'no-store',credentials:'same-origin'});
  if(!response.ok){let value;try{value=await response.json()}catch{}throw Error(value?.error||'下载失败')}
  if(!current(task,revision))return false;
  if(handle){
   const output=await handle.createWritable();
   try{if(response.body?.pipeTo)await response.body.pipeTo(output);else{await output.write(await response.blob());await output.close()}}
   catch(error){try{await output.abort()}catch{}throw error}
  }else{
   const blob=await response.blob();if(!current(task,revision))return false;
   const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=suggestedName;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  return true;
 }
 async function action(name,payload={}){
  const task=getTask(),revision=epoch;
  let value;
  try{value=await request(task,'vault-action',{method:'POST',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify({action:name,...payload})})}
  catch(error){if(current(task,revision))await refreshMetadata(true).catch(()=>{});throw error}
  if(!current(task,revision))return null;
  await refreshMetadata(true);return current(task,revision)?value:null;
 }
 function bind(id,operation){controls.add(id);byId(id).addEventListener('click',async()=>{
  const button=byId(id),task=getTask(),revision=epoch;button.disabled=true;status('');
  try{const message=await operation();if(current(task,revision)&&message)status(message)}catch(error){if(current(task,revision)&&error.name!=='AbortError')status(error.message,true)}
  finally{if(current(task,revision)){button.disabled=false;if(id==='ex-vault-move'&&metadata?.move_available===false)button.disabled=true}}
 })}
 async function browse(path){
  const task=getTask(),revision=epoch,serial=++directorySerial;
  const value=await request(task,'vault-directories'+(path?'?path='+encodeURIComponent(path):''));
  if(!current(task,revision)||serial!==directorySerial)return;
  directory=value;byId('ex-vault-directory-picker').hidden=false;byId('ex-vault-directory-path').textContent=value.path;
  byId('ex-vault-up').disabled=!value.parent||value.parent===value.path;
  const list=byId('ex-vault-directories');list.replaceChildren();
  for(const item of value.directories||[]){
   const button=document.createElement('button');button.type='button';button.textContent=item.name;button.addEventListener('click',()=>browse(item.path).catch(error=>{if(current(task,revision))status(error.message,true)}));list.append(button);
  }
  if(!list.children.length){const empty=document.createElement('span');empty.textContent='此目录没有子文件夹';list.append(empty)}
 }
 bind('ex-vault-open',async()=>{
  dialog.showModal();branches();status('读取笔记库…');await refreshMetadata(true);return '';
 });
 bind('ex-vault-copy',async()=>{if(!metadata)return;await copy(metadata.vault_root);return '已复制笔记库路径'});
 bind('ex-vault-folder',async()=>{if(await action('open_folder'))return '已请求打开本机文件夹'});
 byId('ex-vault-obsidian-name').addEventListener('input',()=>{try{window.localStorage?.setItem('sciver-obsidian-vault:'+getTask(),byId('ex-vault-obsidian-name').value.trim())}catch{}});
 bind('ex-vault-obsidian',async()=>{
  const name=byId('ex-vault-obsidian-name').value.trim();if(!name)throw Error('请填写本机 Obsidian 库名称');
  window.location.href='obsidian://open?vault='+encodeURIComponent(name)+'&file='+encodeURIComponent(metadata?.canvas_relative_path||'exploration.canvas');
 });
 bind('ex-vault-download',async()=>await save(getTask(),'vault-download','research-notebook.zip','application/zip','zip')?'笔记库已保存，可用 Obsidian 打开解压后的文件夹':'');
 bind('ex-vault-canvas-download',async()=>await save(getTask(),'canvas-download',metadata?.canvas_relative_path?.split('/').pop()||'exploration.canvas','application/json','canvas')?'全图 Canvas 已保存':'');
 bind('ex-vault-refresh',async()=>await action('refresh_canvas')?'Canvas 已刷新':'');
 bind('ex-vault-create-branch',async()=>{const branch=byId('ex-vault-branch').value;if(!/^H-\d+$/.test(branch))throw Error('请先选择猜想分支');const result=await action('create_branch',{branch});return result?'已生成分支 Canvas：'+(result.canvas_relative_path||result.path||branch):''});
 bind('ex-vault-download-branch',async()=>{const branch=byId('ex-vault-branch').value;if(!/^H-\d+$/.test(branch))throw Error('请先选择猜想分支');return await save(getTask(),'canvas-download?branch='+encodeURIComponent(branch),branch+'.canvas','application/json','canvas')?'分支 Canvas 已保存':''});
 bind('ex-vault-browse',async()=>{const path=byId('ex-vault-destination').value||metadata?.vault_root;await browse(path);return ''});
 bind('ex-vault-up',async()=>{if(directory?.parent)await browse(directory.parent);return ''});
 bind('ex-vault-use-directory',async()=>{if(directory)byId('ex-vault-destination').value=directory.path;return '已选目录；也可在目标路径末尾填写新文件夹名'});
 bind('ex-vault-move',async()=>{const destination=byId('ex-vault-destination').value.trim();if(!destination)throw Error('请选择目标目录');const result=await action('move',{destination});return result?'笔记库已移动，后续输出将保存到新位置':''});
 document.getElementById('ex-reader')?.addEventListener('click',async event=>{
  const button=event.target.closest('[data-vault-document-action]');if(!button)return;
  const task=getTask(),revision=epoch,path=button.dataset.path,toolbar=button.closest('.ex-document-tools');
  const feedback=toolbar.querySelector('.ex-document-action-status');button.disabled=true;
  try{
   if(button.dataset.vaultDocumentAction==='copy'){await refreshMetadata();if(!current(task,revision))return;await copy(absolute(path));feedback.textContent='已复制文档路径'}
   else if(button.dataset.vaultDocumentAction==='obsidian'){
    await refreshMetadata();if(!current(task,revision))return;
    const name=byId('ex-vault-obsidian-name').value.trim();if(!name)throw Error('请先在笔记库中填写本机 Obsidian 库名称');
    window.location.href='obsidian://open?vault='+encodeURIComponent(name)+'&file='+encodeURIComponent(path);
   }else if(button.dataset.vaultDocumentAction==='folder'){
    await refreshMetadata();if(!current(task,revision))return;
    if(!metadata?.local_actions_available)throw Error('远程使用时，请在本机打开同步或下载的笔记库');
    if(await action('open_folder',{path}))feedback.textContent='已请求显示所在文件夹';
   }else if(button.dataset.vaultDocumentAction==='save'&&await save(task,'document-download?path='+encodeURIComponent(path),path.split('/').pop()||'document.md','text/markdown','md'))feedback.textContent='已保存 Markdown 副本';
  }catch(error){if(current(task,revision)&&error.name!=='AbortError')feedback.textContent=error.message}
  finally{if(current(task,revision))button.disabled=false}
 });
 return {
  reset(){epoch++;metadataSerial++;directorySerial++;metadata=null;metadataTask='';metadataRequest=null;directory=null;if(dialog.open)dialog.close();for(const id of controls)byId(id).disabled=false;byId('ex-vault-path').textContent='读取中…';byId('ex-vault-canvas').textContent='读取中…';byId('ex-vault-updated').textContent='';byId('ex-vault-host-note').textContent='';byId('ex-vault-destination').value='';byId('ex-vault-obsidian-name').value='';byId('ex-vault-copy').disabled=true;byId('ex-vault-move').disabled=true;byId('ex-vault-folder').hidden=true;byId('ex-vault-obsidian').hidden=true;byId('ex-vault-directory-picker').hidden=true;status('')},
  warm(){refreshMetadata().catch(()=>{})},
  documentTools(path){return '<div class="ex-document-tools"><span class="ex-document-path" data-vault-document-path="'+escape(path)+'">'+escape(absolute(path))+'</span><button type="button" class="ex-ref" data-vault-document-action="copy" data-path="'+escape(path)+'">复制文件路径</button><button type="button" class="ex-ref" data-vault-document-action="obsidian" data-path="'+escape(path)+'">在 Obsidian 打开</button><button type="button" class="ex-ref" data-vault-document-action="folder" data-path="'+escape(path)+'"'+(metadata?.local_actions_available===true?'':' hidden')+'>显示所在文件夹</button><button type="button" class="ex-ref" data-vault-document-action="save" data-path="'+escape(path)+'">另存 Markdown 副本…</button><span class="ex-document-action-status" role="status"></span></div>'},
  updateDocumentPaths
 };
}};
"""
