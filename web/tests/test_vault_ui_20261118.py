"""Live graph notebook actions, local/remote boundaries and task isolation."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tm_observer.research_explorer_g6 import graph_content
from tm_observer.vault_ui import VAULT_SCRIPT


def test_notebook_is_available_on_the_live_graph_and_reader():
    html = graph_content()
    assert 'id="ex-vault-open"' in html
    assert 'id="ex-vault"' in html
    assert html.index("globalThis.SciverVaultUI=") < html.index("const vaultUi=")
    assert "vaultUi?.documentTools(doc.id)" in html
    assert "X-TM-Operator-Intent" in html


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_notebook_actions_use_current_task_and_preserve_native_save_activation():
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(id=''){this.id=id;this.value='';this.textContent='';this.dataset={};this.style={};this.children=[];this.listeners={};this.hidden=false;this.disabled=false;this.open=false}
 addEventListener(name,callback){this.listeners[name]=callback}
 async click(){if(this.download)calls.push({download:this.download,href:this.href});return this.listeners.click?.({target:this})}
 append(child){this.children.push(child)}
 replaceChildren(){this.children=[];this.value=''}
 showModal(){this.open=true}
 close(){this.open=false}
 remove(){}
 select(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id)};
const pathElement=new Element();pathElement.dataset.vaultDocumentPath='notes/task-b/report.md';
const calls=[],storage=new Map();let task='task-a',resolveA,failRefresh=false;
const metadataB={vault_root:'/srv/notebooks/vault-b',canvas_path:'/srv/notebooks/vault-b/task-b.canvas',canvas_relative_path:'task-b.canvas',local_actions_available:false,move_available:false,move_disabled_reason:'请先暂停任务',updated_at:'2026-11-18'};
const response=value=>({ok:true,json:async()=>value,body:{pipeTo:async output=>{calls.push('write');await output.close()}},blob:async()=>({})});
const ctx={document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:selector=>selector==='[data-vault-document-path]'?[pathElement]:[],body:new Element(),execCommand:()=>true},
 navigator:{clipboard:{writeText:async value=>{calls.push({copy:value})}}},
 window:{location:{href:''},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},showSaveFilePicker:async options=>{calls.push('picker');calls.push({picker:options});return {createWritable:async()=>({close:async()=>calls.push('close'),abort:async()=>{}})}}},
 URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},setTimeout:()=>0,
 fetch:async(url,options={})=>{
  calls.push({url,options});
  if(url==='/api/canvas-md/tasks/task-a/vault')return new Promise(resolve=>resolveA=()=>resolve(response({vault_root:'/old/wrong-task',canvas_relative_path:'old.canvas',local_actions_available:true})));
  if(url==='/api/canvas-md/tasks/task-b/vault')return response(metadataB);
  if(url.includes('/vault-action'))return response(failRefresh?{ok:false,canvases:[{status:'pending',error:'disk unavailable'}]}:{canvas_relative_path:'task-b--H-0002.canvas'});
  if(url.includes('/vault-directories'))return response({path:'/srv/notebooks',parent:'/srv',directories:[{name:'<script> folder',path:'/srv/notebooks/new'}]});
  return response({});
 },console};
vm.createContext(ctx);
"""
    scenario = r"""
(async()=>{
 const ui=ctx.SciverVaultUI.create({getTask:()=>task,getBranch:()=> 'H-0002',getHypotheses:()=>[{id:'H-0002',label:'Safe <title>'}]});
 const oldOpen=get('ex-vault-open').click();await Promise.resolve();
 assert.equal(get('ex-vault-open').disabled,true);
 task='task-b';ui.reset();assert.equal(get('ex-vault-open').disabled,false,'task switch releases old in-flight controls');ui.warm();
 await new Promise(resolve=>setImmediate(resolve));
 resolveA();await oldOpen;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(get('ex-vault-path').textContent,metadataB.vault_root,'late old-task metadata cannot replace current notebook');
 assert.equal(get('ex-vault-folder').hidden,true,'remote service cannot pretend to open local Finder');
 assert.equal(get('ex-vault-obsidian').hidden,false,'remote use opens a locally registered Obsidian vault by name');
 assert.equal(get('ex-vault-move').disabled,true);
 assert.equal(pathElement.textContent,'/srv/notebooks/vault-b/notes/task-b/report.md');
 const html=ui.documentTools('notes/a"<&.md');
 assert(html.includes('a&quot;&lt;&amp;.md')&&!html.includes('a"<&.md'),'document paths must be escaped before rendering');
 await get('ex-vault-open').click();
 assert.equal(get('ex-vault').open,true);
 assert.equal(get('ex-vault-branch').value,'H-0002');
 assert.equal(get('ex-vault-branch').children[1].textContent,'H-0002 · Safe <title>','labels use DOM text, not HTML');
 get('ex-vault-obsidian-name').value='My research vault';get('ex-vault-obsidian-name').listeners.input();
 await get('ex-vault-obsidian').click();
 assert.equal(ctx.window.location.href,'obsidian://open?vault=My%20research%20vault&file=task-b.canvas');
 assert(!ctx.window.location.href.includes('/srv/'),'Obsidian never receives a remote absolute path');
 assert.equal(storage.get('sciver-obsidian-vault:task-b'),'My research vault');
 const feedback=new Element(),documentButton=new Element();
 documentButton.dataset={path:'notes/task-b/report.md',vaultDocumentAction:'copy'};
 documentButton.closest=()=>({querySelector:()=>feedback});
 await get('ex-reader').listeners.click({target:{closest:()=>documentButton}});
 assert(calls.some(call=>call.copy==='/srv/notebooks/vault-b/notes/task-b/report.md'));
 documentButton.dataset.vaultDocumentAction='obsidian';
 await get('ex-reader').listeners.click({target:{closest:()=>documentButton}});
 assert.equal(ctx.window.location.href,'obsidian://open?vault=My%20research%20vault&file=notes%2Ftask-b%2Freport.md');
 documentButton.dataset.vaultDocumentAction='folder';calls.length=0;
 await get('ex-reader').listeners.click({target:{closest:()=>documentButton}});
 assert(!calls.some(call=>call.url?.endsWith('/vault-action')),'remote document actions cannot open server folders');
 calls.length=0;await get('ex-vault-download-branch').click();
 assert.equal(calls[0],'picker','native save picker opens before the first fetch');
 assert(calls.some(call=>call.url==='/api/canvas-md/tasks/task-b/canvas-download?branch=H-0002'));
 assert(calls.includes('write')&&calls.includes('close'));
 calls.length=0;await get('ex-vault-create-branch').click();
 const mutation=calls.find(call=>call.url?.endsWith('/vault-action'));
 assert.equal(mutation.options.headers['X-TM-Operator-Intent'],'1');
 assert.deepEqual(JSON.parse(mutation.options.body),{action:'create_branch',branch:'H-0002'});
 await get('ex-vault-browse').click();
 assert.equal(get('ex-vault-directories').children[0].textContent,'<script> folder');
 await get('ex-vault-use-directory').click();
 assert.equal(get('ex-vault-destination').value,'/srv/notebooks');
 ctx.window.showSaveFilePicker=undefined;calls.length=0;
 await get('ex-vault-canvas-download').click();
 assert(calls.some(call=>call.download==='task-b.canvas'&&call.href==='blob:test'),'browsers without a native picker use a normal file download');
 // Cancellation does not fetch the file and does not become an error message.
 ctx.window.showSaveFilePicker=async()=>{const e=Error('cancel');e.name='AbortError';throw e};
 calls.length=0;await get('ex-vault-download').click();assert.equal(calls.length,0);
 assert.equal(get('ex-vault-status').textContent,'');
 failRefresh=true;metadataB.status='pending';metadataB.canvases=[{canvas_path:metadataB.canvas_path,status:'pending',error:'disk unavailable'}];
 await get('ex-vault-refresh').click();
 assert.equal(get('ex-vault-status').textContent,'disk unavailable','HTTP 200 with ok:false is not a successful refresh');
 assert.equal(get('ex-vault-status').dataset.error,'true');
 assert(get('ex-vault-updated').textContent.includes('等待刷新：disk unavailable'),'pending state remains visible independently of the action result');
 task='task-c';ui.reset();assert.equal(get('ex-vault').open,false);
 assert.equal(get('ex-vault-obsidian-name').value,'');assert.equal(get('ex-vault-copy').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    script = harness + "\nvm.runInContext(" + repr(VAULT_SCRIPT) + ",ctx);\n" + scenario
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
