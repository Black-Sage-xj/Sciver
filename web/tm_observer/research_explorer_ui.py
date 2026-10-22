"""Research Explorer client: read-only Canvas viewport and Markdown navigation."""
from __future__ import annotations

import html
from pathlib import Path

from .canvas_md import ExplorerError, vault_root
from .dashboard_ui import dashboard_shell
from .research_explorer_g6 import graph_content

EXPLORER_CSS = r"""
<style>
.tm-explorer-view .tm-content{max-width:none;padding:15px 20px}
.ex-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.ex-toolbar input{min-width:240px;flex:1;max-width:420px;background:#18181b;border:1px solid #3f3f46;border-radius:7px;padding:10px;color:white}
.ex-toolbar select{background:#18181b;color:white;padding:9px}
.ex-layout{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(340px,1fr);gap:12px;height:calc(100vh - 225px);min-height:520px}
.ex-graph,.ex-reader{position:relative;background:#111115;border:1px solid #313139;border-radius:12px;overflow:hidden;min-width:0}
.ex-graph{cursor:grab;touch-action:none}
.ex-graph:active{cursor:grabbing}
#ex-svg{width:100%;height:100%}
.ex-node{cursor:pointer}
.ex-node rect{fill:#1b1e26;stroke:#737987;stroke-width:2}
.ex-node text{font-size:19px;fill:#f1f5f9;pointer-events:none}
.ex-node.selected rect{stroke:#60a5fa;stroke-width:5}
.ex-edge{stroke:#77839b;stroke-width:2.5;fill:none;opacity:.8;pointer-events:none}
.ex-actions{position:absolute;top:12px;left:12px;display:flex;gap:5px}
.ex-hint{position:absolute;bottom:12px;left:12px;padding:5px 9px;border-radius:7px;background:#0c0c0ed9;color:#a1a1aa;font-size:12px;pointer-events:none}
.ex-reader{padding:22px;overflow-y:auto;overflow-wrap:anywhere}
.ex-reader h2{margin:0 0 7px;font-size:20px}
.ex-reader .ex-meta{font-size:12px;color:#a1a1aa;margin-bottom:15px}
.ex-reader .ex-md{line-height:1.75}
.ex-md pre{overflow:auto;background:#08080a;padding:12px;border-radius:8px}
.ex-md img{max-width:100%}
.ex-md a{color:#93c5fd}
.ex-md blockquote{border-left:3px solid #64748b;padding-left:13px;color:#cbd5e1}
.ex-md table{display:block;overflow:auto}
.ex-section{border-top:1px solid #333;padding-top:10px;margin-top:18px}
.ex-ref{display:inline-block;margin:4px;padding:5px 9px;border:1px solid #414557;border-radius:7px;cursor:pointer;background:#222633;color:#dbeafe}
.ex-search{position:absolute;z-index:5;top:55px;left:12px;right:12px;max-height:60%;overflow:auto;background:#19191f;border:1px solid #454550;border-radius:8px;padding:6px;box-shadow:0 12px 32px #0009}
.ex-search button{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid #333;border-radius:0}
.ex-search small{display:block;color:#a1a1aa}
@media(max-width:1100px){.ex-layout{grid-template-columns:1fr;height:auto}.ex-graph{height:53vh;min-height:340px}.ex-reader{height:60vh}}
</style>
"""
EXPLORER_HTML = r"""
<div class="ex-toolbar">
<label>科研任务 <select id="ex-task">TM_CANVAS_TASK_OPTIONS</select></label>
<input id="ex-query" type="search" placeholder="搜索 Canvas 节点、Markdown 正文…" aria-label="Search scientific documents"/>
<button id="ex-refresh" title="重新读取当前 Canvas 与 Markdown">刷新文件</button><span class="small" id="ex-stats">读取中…</span>
</div>
<div class="ex-layout">
  <div class="ex-graph" id="ex-graph" aria-label="Canvas scientific graph">
    <svg id="ex-svg" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="科研节点与继承连线"></svg>
    <div class="ex-actions"><button id="ex-fit">适应画布</button><button id="ex-plus">＋</button><button id="ex-minus">－</button></div>
    <div class="ex-hint">双指滑动平移 · 捏合缩放 · 也可拖拽</div>
    <div class="ex-search" id="ex-results" hidden></div>
  </div>
  <section class="ex-reader" id="ex-reader" aria-live="polite"><p class="muted">点击左侧节点以阅读科研内容。</p></section>
</div>
<script defer src="https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
<script>
(()=>{
'use strict';
const $=id=>document.getElementById(id), svg=$('ex-svg'), graph=$('ex-graph'), reader=$('ex-reader');
const NS='http://www.w3.org/2000/svg';
let index=null,selected=null,viewport={x:0,y:0,w:100,h:100},drag=null;let taskId=$('ex-task').value;const base=()=>'/api/canvas-md/tasks/'+encodeURIComponent(taskId);
function elt(name,attrs={},text=''){const el=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));if(text)el.textContent=text;return el}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function uri(kind,id){return kind+':'+id}
function showError(message){reader.textContent='无法加载：'+message}
async function api(path){const r=await fetch(path,{credentials:'same-origin',cache:'no-store'});const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v}
function svgView(){svg.setAttribute('viewBox',[viewport.x,viewport.y,viewport.w,viewport.h].join(' '))}
function bounds(n){return {x:Number(n.x)||0,y:Number(n.y)||0,w:Math.max(100,Number(n.width)||420),h:Math.max(70,Number(n.height)||260)}}
function fit(){if(!index||!index.nodes.length)return;let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;for(const n of index.nodes){const b=bounds(n);minX=Math.min(minX,b.x);minY=Math.min(minY,b.y);maxX=Math.max(maxX,b.x+b.w);maxY=Math.max(maxY,b.y+b.h)}const pad=250;viewport={x:minX-pad,y:minY-pad,w:maxX-minX+2*pad,h:maxY-minY+2*pad};svgView()}
function focus(node){const b=bounds(node),ratio=svg.clientWidth/Math.max(1,svg.clientHeight),w=Math.max(b.w*3,900);viewport={x:b.x+b.w/2-w/2,y:b.y+b.h/2-w/ratio/2,w,h:w/ratio};svgView()}
function zoom(scale){const cx=viewport.x+viewport.w/2,cy=viewport.y+viewport.h/2;viewport.w*=scale;viewport.h*=scale;viewport.x=cx-viewport.w/2;viewport.y=cy-viewport.h/2;svgView()}
function graphRender(){svg.replaceChildren();if(!index)return;const lookup=new Map(index.nodes.map(n=>[n.id,n]));const edgeLayer=elt('g');for(const e of index.edges){const a=lookup.get(e.fromNode),b=lookup.get(e.toNode);if(!a||!b)continue;const ab=bounds(a),bb=bounds(b);edgeLayer.append(elt('path',{class:'ex-edge',d:'M '+(ab.x+ab.w)+' '+(ab.y+ab.h/2)+' C '+(ab.x+ab.w+bb.x)/2+' '+(ab.y+ab.h/2)+', '+(ab.x+ab.w+bb.x)/2+' '+(bb.y+bb.h/2)+', '+bb.x+' '+(bb.y+bb.h/2)}))}svg.append(edgeLayer);
const layer=elt('g');for(const n of index.nodes){const b=bounds(n),g=elt('g',{class:'ex-node'+(selected?.kind==='node'&&selected.id===n.id?' selected':''),tabindex:'0',role:'button','aria-label':n.label});g.append(elt('rect',{x:b.x,y:b.y,width:b.w,height:b.h,rx:15}));let lines=(n.label||n.id).match(/.{1,27}/gu)||[n.id];lines=lines.slice(0,4);lines.forEach((line,i)=>g.append(elt('text',{x:b.x+20,y:b.y+42+i*32},line)));g.addEventListener('click',()=>navigate('node',n.id));g.addEventListener('keydown',e=>{if(e.key==='Enter')navigate('node',n.id)});layer.append(g)}svg.append(layer);svgView()}
function resolve(target,source){let key;try{key=decodeURIComponent(target.split('|')[0].split('#')[0]).trim()}catch{return null}if(!key)return null;const aliases=index.aliases||{};let candidates=[key,key.replace(/\.md$/i,'')];if(source&&key.startsWith('.')){const parts=source.split('/');parts.pop();for(const part of key.split('/')){if(part==='..')parts.pop();else if(part!=='.')parts.push(part)}candidates.unshift(parts.join('/'))}for(const c of candidates){const hit=aliases[c.toLowerCase()];if(hit){const sep=hit.indexOf(':');return {kind:hit.slice(0,sep),id:hit.slice(sep+1),anchor:target.split('#')[1]?.split('|')[0]||''}}}return null}
function mdHtml(text,source){if(!window.marked||!window.DOMPurify)return '<pre>'+esc(text)+'</pre>';
const math=[];let protectedText=text.replace(/(\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g,raw=>{const block=raw.startsWith('$$');math.push({value:raw.slice(block?2:1,block?-2:-1),block});return 'TMKATEXPLACEHOLDER'+(math.length-1)+'END'});
protectedText=protectedText.replace(/(?<!!)\[\[([^\]\n]+)\]\]/g,(full,link)=>{const label=link.split('|')[1]||link.split('|')[0];const ref=resolve(link,source);return ref?'['+label.replace(/[\[\]]/g,'')+'](#tmref='+encodeURIComponent(ref.kind+':'+ref.id)+(ref.anchor?'%23'+encodeURIComponent(ref.anchor):'')+')':esc(label)});
const result=window.DOMPurify.sanitize(window.marked.parse(protectedText));
const doc=new DOMParser().parseFromString('<div id="parsed">'+result+'</div>','text/html');const wrap=doc.getElementById('parsed');wrap.querySelectorAll('a').forEach(a=>{const href=a.getAttribute('href')||'';if(href.startsWith('#tmref=')){a.dataset.ref=decodeURIComponent(href.slice(7));a.removeAttribute('href')}else if(href.startsWith('#')){}else if(/^https?:\/\//i.test(href)){a.target='_blank';a.rel='noopener noreferrer'}else{const ref=resolve(href,source);if(ref){a.dataset.ref=uri(ref.kind,ref.id)+(ref.anchor?'#'+ref.anchor:'');a.removeAttribute('href')}else a.removeAttribute('href')}});
wrap.querySelectorAll('*').forEach(el=>{for(const child of Array.from(el.childNodes)){if(child.nodeType!==3||!child.textContent.includes('TMKATEXPLACEHOLDER'))continue;const fragment=doc.createDocumentFragment(),re=/TMKATEXPLACEHOLDER(\d+)END/g;let pos=0,m;while((m=re.exec(child.textContent))){fragment.append(doc.createTextNode(child.textContent.slice(pos,m.index)));const spec=math[Number(m[1])],box=doc.createElement(spec.block?'div':'span');try{if(window.katex)box.innerHTML=window.katex.renderToString(spec.value,{displayMode:spec.block,throwOnError:false,trust:false,output:'html'});else box.textContent=spec.value}catch{box.textContent=spec.value}fragment.append(box);pos=re.lastIndex}fragment.append(doc.createTextNode(child.textContent.slice(pos)));child.replaceWith(fragment)}});
return wrap.innerHTML}
function refsBlock(key){const refs=index.backlinks[key]||[];return '<div class="ex-section"><strong>反向引用 ('+refs.length+')</strong><div>'+(refs.length?refs.map(r=>'<button class="ex-ref" data-ref="'+esc(uri(r.kind,r.id))+'">'+esc(r.label)+'</button>').join(''):'<p class="muted">暂无引用</p>')+'</div></div>'}
function outbound(key){const refs=index.references[key]||[];return '<div class="ex-section"><strong>文档引用 ('+refs.length+')</strong><div>'+refs.map(r=>'<button class="ex-ref" data-ref="'+esc(uri(r.kind,r.id)+(r.anchor?'#'+r.anchor:''))+'">'+esc(r.id)+(r.anchor?'#'+r.anchor:'')+'</button>').join('')+'</div></div>'}
let navigationSerial=0;
async function navigate(kind,id,anchor='',record=true){
  if(!index)return;
  const serial=++navigationSerial;
  const key=uri(kind,id);
  if(kind==='node'){
    const node=index.nodes.find(n=>n.id===id);if(!node)return;
    selected={kind,id,anchor};
    reader.innerHTML='<h2>'+esc(node.label)+'</h2><div class="ex-meta">Canvas 节点 · '+esc(id)+
      '</div><div class="ex-md">'+mdHtml(node.text||'',null)+'</div>'+outbound(key)+refsBlock(key);
    focus(node);graphRender();
    const linked=(index.references[key]||[]).find(r=>r.kind==='doc');
    if(linked){
      const section=document.createElement('section');section.className='ex-section';
      section.innerHTML='<h3>关联 Markdown</h3><p class="muted">关联文档单独阅读，不合并到当前节点。</p>'+
        '<button class="ex-ref" data-ref="'+esc(uri('doc',linked.id))+'">打开关联文档：'+esc(linked.id)+' →</button>';
      reader.append(section);
    }
  }else if(kind==='doc'){
    try{
      const doc=await api(base()+'/document?path='+encodeURIComponent(id));
      if(serial!==navigationSerial)return;
      selected={kind,id,anchor};
      reader.innerHTML='<h2>'+esc(doc.title)+'</h2><div class="ex-meta">Markdown · '+esc(doc.id)+
        '</div><div class="ex-md">'+mdHtml(doc.content,doc.id)+'</div>'+outbound(key)+refsBlock(key);
    }catch(e){showError(e.message);return}
  }else return;
  if(serial!==navigationSerial)return;
  reader.scrollTop=0;
  if(anchor){
    const headings=reader.querySelectorAll('.ex-md h1,.ex-md h2,.ex-md h3,.ex-md h4,.ex-md h5,.ex-md h6');
    const target=Array.from(headings).find(h=>h.id===anchor||h.textContent.trim()===anchor);
    if(target)target.scrollIntoView({block:'start'});
  }
  const url=location.pathname+(location.search||'')+
    '#'+encodeURIComponent(kind+':'+id+(anchor?'#'+anchor:''));
  const state={tmExplorerTask:taskId,tmExplorerSelected:{kind,id,anchor}};
  if(record&&typeof history.pushState==='function')history.pushState(state,'',url);
  else history.replaceState(state,'',url);
}
reader.addEventListener('click',e=>{const el=e.target.closest('[data-ref]');if(!el)return;const v=el.dataset.ref,split=v.indexOf(':'),hash=v.indexOf('#');if(split>0)navigate(v.slice(0,split),v.slice(split+1,hash<0?undefined:hash),hash<0?'':v.slice(hash+1))});
async function load(){try{index=await api(base()+'/index');$('ex-stats').textContent=index.stats.node_count+' 节点 · '+index.stats.edge_count+' 科研连线 · '+index.stats.doc_count+' 文档';graphRender();fit();const hash=decodeURIComponent(location.hash.slice(1)),i=hash.indexOf(':');if(i>0){const h=hash.indexOf('#');navigate(hash.slice(0,i),hash.slice(i+1,h<0?undefined:h),h<0?'':hash.slice(h+1),false)}else if(index.nodes.length)navigate('node',index.nodes[0].id,'',false)}catch(e){showError(e.message);$('ex-stats').textContent='加载失败'}}
$('ex-task').onchange=()=>{taskId=$('ex-task').value;selected=null;history.pushState({tmExplorerTask:taskId},'',location.pathname+'?observer_id='+encodeURIComponent(taskId));load()};window.addEventListener('popstate',()=>{
  const params=new URLSearchParams(location.search),fromUrl=params.get('observer_id');
  if(fromUrl&&fromUrl!==taskId&&Array.from($('ex-task').options).some(o=>o.value===fromUrl)){
    taskId=fromUrl;$('ex-task').value=taskId;selected=null;load();return;
  }
  const hash=decodeURIComponent(location.hash.slice(1)),i=hash.indexOf(':');
  if(i>0){
    const h=hash.indexOf('#');
    navigate(hash.slice(0,i),hash.slice(i+1,h<0?undefined:h),h<0?'':hash.slice(h+1),false);
  }else{selected=null;reader.innerHTML='<p class="muted">点击左侧节点以阅读科研内容。</p>';}
});
$('ex-fit').onclick=fit;$('ex-plus').onclick=()=>zoom(.75);$('ex-minus').onclick=()=>zoom(1.35);$('ex-refresh').onclick=load;
svg.addEventListener('pointerdown',e=>{if(e.target.closest('.ex-node'))return;drag={x:e.clientX,y:e.clientY,ox:viewport.x,oy:viewport.y};svg.setPointerCapture(e.pointerId)});
svg.addEventListener('pointermove',e=>{if(!drag)return;viewport.x=drag.ox-(e.clientX-drag.x)*viewport.w/svg.clientWidth;viewport.y=drag.oy-(e.clientY-drag.y)*viewport.h/svg.clientHeight;svgView()});
svg.addEventListener('pointerup',()=>drag=null);svg.addEventListener('pointercancel',()=>drag=null);
// Trackpad two-finger scroll pans as soon as the pointer is over Canvas.
function canvasWheel(e){
  if(e.target.closest('.ex-actions, .ex-search'))return;
  e.preventDefault();
  const w=Math.max(1,svg.clientWidth),h=Math.max(1,svg.clientHeight);
  const unit=e.deltaMode===1?16:(e.deltaMode===2?h:1);
  if(e.ctrlKey||e.metaKey){
    // Trackpad pinch emits a modified wheel event; zoom around the cursor.
    const rect=svg.getBoundingClientRect();
    const ax=Math.max(0,Math.min(1,(e.clientX-rect.left)/Math.max(1,rect.width)));
    const ay=Math.max(0,Math.min(1,(e.clientY-rect.top)/Math.max(1,rect.height)));
    const scale=Math.exp(Math.max(-1,Math.min(1,e.deltaY*unit*.008)));
    const nextW=Math.max(80,Math.min(200000,viewport.w*scale));
    const applied=nextW/viewport.w,nextH=viewport.h*applied;
    viewport.x+=ax*(viewport.w-nextW);viewport.y+=ay*(viewport.h-nextH);
    viewport.w=nextW;viewport.h=nextH;
  }else{
    const dx=e.shiftKey&&e.deltaX===0?e.deltaY:e.deltaX;
    const dy=e.shiftKey&&e.deltaX===0?0:e.deltaY;
    viewport.x+=dx*unit*viewport.w/w;
    viewport.y+=dy*unit*viewport.h/h;
  }
  svgView();
}
graph.addEventListener('wheel',canvasWheel,{passive:false});
let searchTimer; $('ex-query').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(async()=>{const q=$('ex-query').value.trim(),box=$('ex-results');if(!q){box.hidden=true;return}try{const hits=(await api(base()+'/search?q='+encodeURIComponent(q))).results;box.replaceChildren();for(const h of hits){const b=document.createElement('button');b.textContent=h.label+' ('+h.kind+')';const small=document.createElement('small');small.textContent=h.excerpt;b.append(small);b.onclick=()=>{navigate(h.kind,h.id);box.hidden=true};box.append(b)}box.hidden=false}catch(e){box.textContent=e.message;box.hidden=false}},250)});
window.addEventListener('DOMContentLoaded',load);
})();
</script>
"""

def explorer_html(search_root: Path, state: dict, selected: str = "", *, legacy: bool = False) -> str:
    requested_task_id = selected
    available = []
    for run in state.get("runs", []):
        observer_id = str(run.get("observer_id", ""))
        try:
            vault_root(search_root, observer_id, state)
        except ExplorerError:
            continue
        available.append(run)
    # Prefer the original Master 1 note if the visitor has not selected a Run.
    if not selected and available:
        primary = next((r for r in available if str(r.get("run_id")) == "1" and "矩阵腔体Green" in str(r.get("task_root"))), None)
        selected = str((primary or available[0])["observer_id"])
    options = "".join(
        f'<option value="{html.escape(str(r["observer_id"]), quote=True)}"'
        + (" selected" if r["observer_id"] == selected else "")
        + f'>Master {html.escape(str(r.get("run_id", "?")))} · {html.escape(str(r.get("task_name", "Research Task")))}</option>'
        for r in available
    )
    if not available:
        options = '<option value="">没有可访问的科研画布</option>'
    content = (EXPLORER_CSS + EXPLORER_HTML if legacy else graph_content()).replace("TM_CANVAS_TASK_OPTIONS", options)
    # When reached from a specific Run's Canvas/MD link, the observer_id in
    # the URL is the selected task. Keep the DOM select for the existing reader
    # but do not repeat an unrelated "科研任务 Master …" chooser above it.
    if requested_task_id and selected == requested_task_id and any(
        str(row.get("observer_id")) == selected for row in available
    ):
        content = content.replace('<label>科研任务 <select id="ex-task">', '<label hidden style="display:none">科研任务 <select id="ex-task">')
    has_source_task = bool(requested_task_id and any(
        str(row.get("observer_id")) == requested_task_id
        for row in available
    ) and len(requested_task_id) == 16 and all(ch in "0123456789abcdef" for ch in requested_task_id))
    return dashboard_shell("Sciver Observatory — Explore map", content,
                           active="explorer",
                           back_href=f"/run/{requested_task_id}/" if has_source_task else None)
