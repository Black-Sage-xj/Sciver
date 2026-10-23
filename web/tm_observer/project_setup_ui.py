"""First-use explanation and explicit source selection for project setup."""
from __future__ import annotations

import hashlib


def setup_panel(workspace: str) -> str:
    key = hashlib.sha256(workspace.encode()).hexdigest()[:16]
    return '''
<style>
.tm-setup-panel{border:1px solid #5a5670;border-radius:14px;padding:18px;background:#24212f;display:grid;gap:12px}
.tm-setup-panel h3,.tm-setup-panel p{margin:0}.tm-setup-panel p{color:#bdb7cb;font-size:13px;line-height:1.6}
.tm-setup-panel label{display:grid;gap:6px;font-size:13px}.tm-setup-panel input[type=text]{background:#18181c;border:1px solid #56515f;color:#fafafa;border-radius:8px;padding:10px;width:100%;box-sizing:border-box}
.tm-setup-panel button,.tm-setup-welcome button{border:1px solid #8b72e8;background:#513897;color:white;padding:10px 14px;border-radius:9px;cursor:pointer}.tm-setup-panel button:disabled{opacity:.5}
.tm-setup-check{display:flex!important;align-items:flex-start;gap:8px!important;font-size:12px!important;line-height:1.5}.tm-setup-files{max-height:200px;overflow:auto;display:grid;gap:5px}
.tm-setup-welcome{background:#24212f;color:#fafafa;max-width:540px;width:calc(100% - 48px);border:1px solid #777080;border-radius:16px;padding:24px}.tm-setup-welcome::backdrop{background:#0009}.tm-setup-welcome p,.tm-setup-welcome li{line-height:1.8;color:#d0c9dd}.tm-setup-welcome button+button{margin-left:10px;background:#34303d}
.tm-setup-status{font-size:12px;color:#c9c0e1}.tm-setup-error{color:#fecdd3!important;white-space:pre-wrap}
</style>
<dialog class="tm-setup-welcome" data-setup-welcome aria-labelledby="setup-welcome-title">
<h2 id="setup-welcome-title">欢迎来到 Sciver Workspace</h2>
<p>启动科研任务只需要一份 Research Plan；不必预先写好，可在这里通过对话建立。</p>
<ol><li>从空白开始，或选择已有目录中的计划、笔记、数据。</li><li>逐步说明目标、研究边界、完成标准和必要背景；系统会整理成草稿。</li><li>检查包含初始 Scientific Model 的完整计划，确认后项目才会加入 Task List。</li></ol>
<p>建项过程不会启动研究。基础引导无需模型账号；模型辅助完善草稿可自行开启。</p>
<button type="button" data-setup-welcome-close>开始建立项目</button><button type="button" data-setup-welcome-skip>先看看</button>
</dialog>
<form class="tm-setup-panel" data-project-setup aria-label="建立科研项目">
<h3>建立科研项目</h3><p>只需一份 Research Plan：目标、研究边界、完成标准和必要背景都可在后续对话中整理。已有计划或资料也可导入；原项目保持不变。</p>
<label>项目名称<input name="name" type="text" required maxlength="60" placeholder="例如：布朗运动扩散系数研究" autocomplete="off"></label>
<details><summary>我已有研究计划或项目资料</summary>
<p>填写运行 Sciver 的电脑上的目录；浏览器所在电脑的路径无法直接读取。</p>
<label>已有项目目录<input name="source_directory" type="text" placeholder="/absolute/path/to/my-project" autocomplete="off"></label>
<button type="button" data-setup-scan>检查目录并选择资料</button>
<p data-setup-inventory-status></p><div class="tm-setup-files" data-setup-files></div>
<label class="tm-setup-check"><input name="read_source" type="checkbox">允许读取选中资料以整理草稿；最终确认后复制到新任务。不会修改原文件。</label>
</details>
<label class="tm-setup-check"><input name="use_model" type="checkbox">使用已配置的模型完善草稿（会发送选中资料的文本摘录并消耗额度；不开启也能完成建项）</label>
<p>下一步从科研目标开始，逐项形成计划草稿。项目只会在最后确认后创建。</p>
<button type="submit">进入目标引导</button><p class="tm-setup-error" data-setup-error role="alert"></p>
</form>
<script>(function(){
const form=document.querySelector('[data-project-setup]');if(!form)return;
const modal=document.querySelector('[data-setup-welcome]');
const key='sciver-project-welcome-''' + key + '''';
function closeWelcome(){modal?.close();try{localStorage.setItem(key,'seen');}catch(_){} }
modal?.querySelector('[data-setup-welcome-close]')?.addEventListener('click',()=>{closeWelcome();form.elements.name.focus();});
modal?.querySelector('[data-setup-welcome-skip]')?.addEventListener('click',closeWelcome);
modal?.addEventListener('cancel',()=>{try{localStorage.setItem(key,'seen');}catch(_){}});
try{if(!localStorage.getItem(key))modal?.showModal();}catch(_){}
const feedback=form.querySelector('[data-setup-error]');const files=form.querySelector('[data-setup-files]');
let checkedDirectory='';
async function post(path,data){const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TM-Operator-Intent':'1'},body:JSON.stringify(data)});const body=await r.json();if(!r.ok)throw new Error(body.error||'请求失败');return body;}
form.elements.source_directory.addEventListener('input',()=>{checkedDirectory='';files.replaceChildren();form.elements.read_source.checked=false;});
form.querySelector('[data-setup-scan]').addEventListener('click',async event=>{
const button=event.currentTarget;button.disabled=true;feedback.textContent='';
try{const directory=form.elements.source_directory.value.trim();const data=await post('/api/research-chat/import-preview',{source_directory:directory});files.replaceChildren();
for(const file of data.files){const label=document.createElement('label');label.className='tm-setup-check';const input=document.createElement('input');input.type='checkbox';input.value=file.path;input.dataset.setupFile='1';input.checked=file.path==='research_plan.md';label.append(input,document.createTextNode(file.path+' · '+Math.ceil(file.size/1024)+' KB'));files.append(label);}
checkedDirectory=directory;form.querySelector('[data-setup-inventory-status]').textContent='找到 '+data.files.length+' 个可选文件。最多选择 100 个、共 250 MB。'+(data.truncated?'目录扫描已达上限，可先整理到较小目录。':'');
}catch(error){feedback.textContent=error.message;checkedDirectory='';}finally{button.disabled=false;}
});
form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;feedback.textContent='';
try{const source=form.elements.source_directory.value.trim();if(source&&source!==checkedDirectory)throw new Error('请先检查目录，并选择要导入的资料。');
const data=await post('/api/research-chat/session',{mode:'setup',name:form.elements.name.value,source_directory:source,source_files:[...form.querySelectorAll('[data-setup-file]:checked')].map(x=>x.value),read_source:form.elements.read_source.checked,use_model:form.elements.use_model.checked});
location.assign('/research-chat/?session='+encodeURIComponent(data.session_id));
}catch(error){feedback.textContent=error.message;button.disabled=false;}
});
})();</script>
'''
