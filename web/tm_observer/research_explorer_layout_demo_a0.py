"""Independent A+C Research Explorer: full graph with vertical chronology and branch lanes."""
from __future__ import annotations
import html
from pathlib import Path
from urllib.parse import quote
from .canvas_md import ExplorerError, vault_root
from .dashboard_ui import dashboard_shell

AC_CSS = r"""
<style>
.tm-layout-ac{color:#e7edf7;max-width:none}.tm-layout-ac .ac-hero{padding:15px 18px;background:#171b25;border:1px solid #30394a;border-radius:12px;margin-bottom:12px}
.tm-layout-ac .ac-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.tm-layout-ac .ac-eyebrow{font-size:10px;letter-spacing:.15em;font-weight:800;color:#c4b5fd;margin-bottom:5px}
.tm-layout-ac h1{font-size:22px;line-height:1.3;margin:0 0 6px}.tm-layout-ac .ac-subtitle{font-size:12px;color:#b6c2d6;line-height:1.55;margin:0;max-width:1000px}
.tm-layout-ac .ac-links,.tm-layout-ac .ac-toolbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.tm-layout-ac .ac-link{display:inline-flex;align-items:center;white-space:nowrap;border:1px solid #4b5a73;background:#202838;color:#dbeafe;border-radius:8px;padding:7px 10px;text-decoration:none;font-size:12px}
.tm-layout-ac .ac-toolbar{margin-top:13px}.tm-layout-ac button,.tm-layout-ac input{font:inherit}.tm-layout-ac button{cursor:pointer;border:1px solid #475569;background:#242a36;color:#e5edf8;border-radius:7px;padding:7px 10px;font-size:12px}
.tm-layout-ac button:hover,.tm-layout-ac button:focus-visible{border-color:#c4b5fd;background:#302b45}.tm-layout-ac .ac-search{min-width:200px;flex:1;max-width:470px;background:#11151e;color:#f1f5f9;border:1px solid #424b5e;border-radius:7px;padding:8px 10px}
.tm-layout-ac .ac-stats{font-size:11px;color:#bac5d8;margin-left:auto}.tm-layout-ac .ac-notice{font-size:11px;color:#cbd5e1;margin-top:9px;line-height:1.5}
.tm-layout-ac .ac-main{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:12px;align-items:stretch;min-width:0}.tm-layout-ac .ac-graph-panel,.tm-layout-ac .ac-detail-panel{min-width:0;background:#11151d;border:1px solid #303847;border-radius:11px;overflow:hidden}
.tm-layout-ac .ac-panel-head{padding:10px 13px;background:#171c27;border-bottom:1px solid #2a3342;display:flex;align-items:center;justify-content:space-between;gap:8px}.tm-layout-ac .ac-panel-head strong{font-size:12px}.tm-layout-ac .ac-axis-note{font-size:10px;color:#9eabc0}
.tm-layout-ac .ac-graph-viewport{height:calc(100vh - 255px);min-height:480px;max-height:1100px;overflow:auto;position:relative;background-color:#0d1119;background-image:radial-gradient(#344055 .7px,transparent .7px);background-size:18px 18px;cursor:grab;overscroll-behavior:contain}
.tm-layout-ac .ac-graph-viewport.is-panning{cursor:grabbing;user-select:none}.tm-layout-ac .ac-svg{display:block;max-width:none;touch-action:pan-x pan-y}
.tm-layout-ac .ac-edge{fill:none;stroke:#64748b;stroke-width:1.5;opacity:.65;cursor:pointer;pointer-events:stroke}.tm-layout-ac .ac-edge.evolution{stroke:#a78bfa;stroke-width:2;opacity:.83}.tm-layout-ac .ac-edge.selected{stroke:#fbbf24;stroke-width:3.5;opacity:1}.tm-layout-ac .ac-edge:hover{stroke:#f8fafc;stroke-width:3;opacity:1}
.tm-layout-ac .ac-node{cursor:pointer;outline:none}.tm-layout-ac .ac-node rect{fill:#1b2230;stroke:#526076;stroke-width:1.35;rx:8}.tm-layout-ac .ac-node.type-hypothesis rect{stroke:#8b7ad4}.tm-layout-ac .ac-node.type-outcome rect{stroke:#258d71}.tm-layout-ac .ac-node.type-evidence rect{stroke:#a77b22}
.tm-layout-ac .ac-node.selected rect{stroke:#fbbf24;stroke-width:3;fill:#302b20}.tm-layout-ac .ac-node.search-hit rect{stroke:#38bdf8;stroke-width:2.5}.tm-layout-ac .ac-node.search-dim{opacity:.34}.tm-layout-ac .ac-node:focus-visible rect{stroke:#f8fafc;stroke-width:3}
.tm-layout-ac .ac-node-id{font-size:11px;font-weight:800;fill:#f1f5f9}.tm-layout-ac .ac-node-label{font-size:10px;fill:#dbe4f2}.tm-layout-ac .ac-node-type{font-size:9px;fill:#9eabc0}.tm-layout-ac .ac-cycle-band{fill:#161d2a;opacity:.55}.tm-layout-ac .ac-cycle-label{font-size:11px;font-weight:800;fill:#a5b4fc}
.tm-layout-ac .ac-detail{padding:13px;max-height:calc(100vh - 255px);min-height:480px;overflow:auto;overflow-wrap:anywhere}.tm-layout-ac .ac-detail h2{font-size:16px;line-height:1.4;margin:0 0 7px;overflow-wrap:anywhere}.tm-layout-ac .ac-detail h3{font-size:12px;color:#dce6f6;margin:15px 0 7px;padding-top:10px;border-top:1px solid #303847}
.tm-layout-ac .ac-meta{font-size:11px;color:#9eabc0;line-height:1.65}.tm-layout-ac .ac-md{font-size:12px;line-height:1.65;color:#dbe4f2;white-space:pre-wrap;overflow-wrap:anywhere;background:#0e131c;border:1px solid #283244;border-radius:7px;padding:10px;margin-top:10px}
.tm-layout-ac .ac-md h3,.tm-layout-ac .ac-md h4{margin:7px 0;font-size:12px}.tm-layout-ac .ac-relations{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px}.tm-layout-ac .ac-relations li{font-size:11px;line-height:1.45;background:#191f2a;border:1px solid #2d3748;border-radius:6px;padding:7px 8px;overflow-wrap:anywhere}
.tm-layout-ac .ac-relations button{font-size:10px;padding:3px 6px;margin:2px 4px 2px 0}.tm-layout-ac .ac-ref-list{display:flex;gap:5px;flex-wrap:wrap}.tm-layout-ac .ac-ref-list button{max-width:100%;overflow-wrap:anywhere;text-align:left}
.tm-layout-ac .ac-empty{padding:22px;color:#a8b5c9;font-size:12px;line-height:1.6}.tm-layout-ac .ac-search-results{max-height:180px;overflow:auto;margin-top:8px;display:flex;gap:5px;flex-wrap:wrap}.tm-layout-ac .ac-search-results:empty{display:none}.tm-layout-ac .ac-search-results button{font-size:10px}.tm-layout-ac .ac-warning{color:#fcd34d}
@media(max-width:1200px){.tm-layout-ac .ac-main{grid-template-columns:minmax(0,1fr)}.tm-layout-ac .ac-detail{max-height:none;min-height:0}.tm-layout-ac .ac-graph-viewport{height:65vh}}
@media(max-width:700px){.tm-layout-ac .ac-head{flex-direction:column}.tm-layout-ac .ac-stats{width:100%;margin-left:0}.tm-layout-ac .ac-search{max-width:none;flex-basis:100%}.tm-layout-ac h1{font-size:19px}.tm-layout-ac .ac-graph-viewport{min-height:420px}}
</style>
"""

AC_HTML = r"""
<div class="tm-layout-ac" id="tm-layout-ac" data-observer-id="TM_OBSERVER_ID">
<section class="ac-hero"><div class="ac-head"><div><div class="ac-eyebrow">A0 SNAPSHOT · ORIGINAL LAYOUT · READ ONLY</div>
<h1>研究图谱 · A0 原始布局演示</h1><p class="ac-subtitle">保留纵向时间轴与横向分支的布局。所有 Canvas 节点和原始连线均加载；点击节点查看详情，点击连线或关系列表可跳转到关联节点。</p></div>
<div class="ac-links"><a class="ac-link" href="TM_ORIGINAL_HREF">原图谱</a><a class="ac-link" href="TM_LAYOUT_B_HREF">B 前沿分组版</a><a class="ac-link" href="TM_CURRENT_A_C_HREF">当前 A+C 版</a></div></div>
<div class="ac-toolbar"><input id="ac-search" class="ac-search" type="search" placeholder="搜索全部节点 ID、标题或内容…" aria-label="搜索全部图谱节点"/>
<button id="ac-search-clear" type="button">清除搜索</button><button id="ac-back" type="button">← 后退</button><button id="ac-forward" type="button">前进 →</button><button id="ac-zoom-out" type="button">−</button><button id="ac-zoom-in" type="button">＋</button>
<button id="ac-fit-width" type="button">适配宽度</button><button id="ac-fit-all" type="button">适配全图</button><button id="ac-refresh" type="button">重新加载</button><span id="ac-stats" class="ac-stats">正在读取完整图谱…</span></div>
<div id="ac-search-results" class="ac-search-results"></div><div id="ac-notice" class="ac-notice">只读视图：不会写入 Canvas 或科研任务。Cycle 越早越靠上；没有可用 Cycle 的节点放入未标记时间区域，不从 H 编号猜测 Cycle。</div></section>
<div class="ac-main"><section class="ac-graph-panel"><div class="ac-panel-head"><strong>完整节点图谱</strong><span class="ac-axis-note">纵向：Cycle 时间顺序 · 横向：分支与关联</span></div>
<div id="ac-viewport" class="ac-graph-viewport"><div class="ac-empty">正在加载全部节点与连线…</div></div></section>
<aside class="ac-detail-panel"><div class="ac-panel-head"><strong>节点 / 连线详情</strong><span class="ac-axis-note">点击查看 · 只读</span></div><div id="ac-detail" class="ac-detail"><p class="ac-meta">点击图谱中的任意节点或连线，查看详情、前驱、后继和文档引用。</p></div></aside></div></div>
<script>
(function(){
'use strict';
const root=document.getElementById('tm-layout-ac');if(!root)return;
const observerId=root.getAttribute('data-observer-id'),$=id=>document.getElementById(id);
const viewport=$('ac-viewport'),detail=$('ac-detail'),stats=$('ac-stats'),notice=$('ac-notice'),results=$('ac-search-results');
const NS='http://www.w3.org/2000/svg',NW=242,NH=108,COL=282,ROW=220,TOP=64,LEFT=36;
let allNodes=[],allEdges=[],index=null,nodeById=new Map(),positions=new Map(),selected=null,query='',scale=1,size={width:1200,height:800},svg=null,docPath=null,docText='';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key=(kind,id)=>kind+':'+id;
const api=async path=>{const r=await fetch(path,{cache:'no-store',credentials:'same-origin'}),v=await r.json();if(!r.ok)throw Error(v.error||('HTTP '+r.status));return v;};
const cycleOf=n=>{const s=n&&n.semantic||{},v=s.cycle??s.placementCycle;if(v===null||v===undefined||String(v).trim()==='')return null;const x=Number(v);return Number.isSafeInteger(x)&&x>=0?x:null;};
const typeOf=n=>String(n&&n.semantic&&n.semantic.type||n&&n.type||'Unknown');
const labelOf=n=>String(n&&n.label||n&&n.semantic&&n.semantic.title||n&&n.id||'未命名节点');
const edgeId=e=>String(e&&e.id||((e&&e.fromNode)||'?')+'->'+((e&&e.toNode)||'?'));
function el(name,attrs={},text){const n=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));if(text!==undefined)n.textContent=String(text);return n;}
function md(raw,refs=[]){const safeRefs=Array.isArray(refs)?refs:[],tokens=[];const findRef=(target,label)=>safeRefs.find(r=>r.id===target||r.label===target||r.label===label||String(r.id||'').endsWith('/'+target)||String(r.id||'').split('/').pop()===target);let source=String(raw??'');const token=html=>{const t='ACREF'+tokens.length+'TOKEN';tokens.push({t,html});return t;};source=source.replace(/\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g,(whole,target,anchor,label)=>{const r=findRef(target,label);return r?token('<button class="ac-md-link" type="button" data-ref-kind="'+esc(r.kind)+'" data-ref-id="'+esc(r.id)+'">'+esc(label||r.label||target)+' →</button>'):(label||target);});source=source.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(whole,label,target)=>{const r=findRef(target,label);if(r)return token('<button class="ac-md-link" type="button" data-ref-kind="'+esc(r.kind)+'" data-ref-id="'+esc(r.id)+'">'+esc(label)+' →</button>');if(/^https?:\/\//i.test(target))return token('<a href="'+esc(target)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+'</a>');return label;});let s=esc(source).replace(/^#### (.*)$/gm,'<h4>$1</h4>').replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h3>$1</h3>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');tokens.forEach(x=>{s=s.replace(x.t,x.html);});return s;}
function layout(){
 const parent=new Map(allNodes.map(n=>[n.id,n.id]));
 const find=id=>{let p=parent.get(id);while(p!==parent.get(p))p=parent.get(p);let c=id;while(parent.get(c)!==p){const q=parent.get(c);parent.set(c,p);c=q;}return p;};
 const union=(a,b)=>{if(!parent.has(a)||!parent.has(b))return;const x=find(a),y=find(b);if(x!==y){if(x.localeCompare(y,'en',{numeric:true})<0)parent.set(y,x);else parent.set(x,y);}};
 allEdges.forEach(e=>{if(e.edge_type==='hypothesis_evolution')union(e.fromNode,e.toNode);});
 const adjacent=new Map(allNodes.map(n=>[n.id,[]]));allEdges.forEach(e=>{if(adjacent.has(e.fromNode)&&adjacent.has(e.toNode)){adjacent.get(e.fromNode).push(e.toNode);adjacent.get(e.toNode).push(e.fromNode);}});
 const component=new Map();allNodes.forEach(n=>{if(typeOf(n)==='Hypothesis'){component.set(n.id,find(n.id));return;}const related=(adjacent.get(n.id)||[]).filter(id=>typeOf(nodeById.get(id)||{})==='Hypothesis').sort((a,b)=>a.localeCompare(b,'en',{numeric:true}));component.set(n.id,related.length?find(related[0]):find(n.id));});
 const cycles=[...new Set(allNodes.map(cycleOf).filter(x=>x!==null))].sort((a,b)=>a-b),rank=new Map(cycles.map((c,i)=>[c,i])),groups=new Map();
 allNodes.forEach(n=>{const c=component.get(n.id)||n.id,row=cycleOf(n)===null?'undated':'cycle:'+cycleOf(n),k=c+'|'+row;if(!groups.has(k))groups.set(k,[]);groups.get(k).push(n);});
 for(const list of groups.values())list.sort((a,b)=>typeOf(a).localeCompare(typeOf(b))||String(a.id).localeCompare(String(b.id),'en',{numeric:true}));
 const comps=[...new Set(allNodes.map(n=>component.get(n.id)||n.id))],minCycle=new Map();
 comps.forEach(c=>{const cs=allNodes.filter(n=>(component.get(n.id)||n.id)===c).map(cycleOf).filter(x=>x!==null);minCycle.set(c,cs.length?Math.min(...cs):Number.MAX_SAFE_INTEGER);});
 comps.sort((a,b)=>minCycle.get(a)-minCycle.get(b)||String(a).localeCompare(String(b),'en',{numeric:true}));
 const widths=new Map();comps.forEach(c=>{let max=1;for(const r of cycles.map(x=>'cycle:'+x))max=Math.max(max,(groups.get(c+'|'+r)||[]).length);max=Math.max(max,Math.min(3,(groups.get(c+'|undated')||[]).length));widths.set(c,max*COL+40);});
 const offset=new Map();let x=LEFT;comps.forEach(c=>{offset.set(c,x);x+=widths.get(c)+48;});
 positions=new Map();let maxY=TOP;
 cycles.forEach(cycle=>{const y=TOP+rank.get(cycle)*ROW;comps.forEach(c=>{(groups.get(c+'|cycle:'+cycle)||[]).forEach((n,i)=>positions.set(n.id,{x:offset.get(c)+i*COL,y,cycle}));});maxY=Math.max(maxY,y+NH+ROW);});
 const unknownY=TOP+cycles.length*ROW+48;let unknownRows=0;
 comps.forEach(c=>{(groups.get(c+'|undated')||[]).forEach((n,i)=>{positions.set(n.id,{x:offset.get(c)+(i%3)*COL,y:unknownY+Math.floor(i/3)*ROW,cycle:null});unknownRows=Math.max(unknownRows,Math.floor(i/3)+1);});});
 size={width:Math.max(1200,x+40),height:Math.max(700,unknownY+unknownRows*ROW+60)};
}
function pathFor(e){const a=positions.get(e.fromNode),b=positions.get(e.toNode);if(!a||!b)return '';const ax=a.x+NW/2,ay=a.y+NH/2,bx=b.x+NW/2,by=b.y+NH/2;if(Math.abs(by-ay)<22){const d=Math.max(45,Math.abs(bx-ax)*.35);return 'M '+ax+' '+ay+' C '+(ax+(bx>=ax?d:-d))+' '+(ay-40)+', '+(bx>=ax?bx-d:bx+d)+' '+(by-40)+', '+bx+' '+by;}const mid=(ay+by)/2;return 'M '+ax+' '+ay+' C '+ax+' '+mid+', '+bx+' '+mid+', '+bx+' '+by;}
function draw(){
 svg=document.createElementNS(NS,'svg');svg.setAttribute('class','ac-svg');svg.setAttribute('role','img');svg.setAttribute('aria-label','完整科研图谱');svg.setAttribute('viewBox','0 0 '+size.width+' '+size.height);svg.setAttribute('width',Math.round(size.width*scale));svg.setAttribute('height',Math.round(size.height*scale));
 const defs=el('defs'),marker=el('marker',{id:'ac-arrow',viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});marker.appendChild(el('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#8b98ad'}));defs.appendChild(marker);svg.appendChild(defs);
 const world=el('g',{id:'ac-world'});world.appendChild(el('rect',{x:0,y:0,width:size.width,height:size.height,fill:'transparent','pointer-events':'all'}));
 const cycles=[...new Set(allNodes.map(cycleOf).filter(x=>x!==null))].sort((a,b)=>a-b),bands=el('g');
 cycles.forEach((c,i)=>{const y=TOP+i*ROW-28;bands.appendChild(el('rect',{class:'ac-cycle-band',x:8,y,width:size.width-16,height:ROW-6,rx:8}));bands.appendChild(el('text',{class:'ac-cycle-label',x:18,y:y+17},'CYCLE '+c));});
 if(allNodes.some(n=>cycleOf(n)===null))bands.appendChild(el('text',{class:'ac-cycle-label',x:18,y:TOP+cycles.length*ROW+22},'未标记时间 · 不推断 Cycle'));world.appendChild(bands);
 const eg=el('g');allEdges.forEach(e=>{const d=pathFor(e);if(!d)return;const p=el('path',{class:'ac-edge '+(e.edge_type==='hypothesis_evolution'?'evolution':'other')+(selected&&selected.kind==='edge'&&selected.id===edgeId(e)?' selected':''),d,'marker-end':'url(#ac-arrow)'});p.appendChild(el('title',{},edgeId(e)+' · '+(e.edge_type||e.label||'关系')));p.addEventListener('click',event=>{event.stopPropagation();selectEdge(e);});eg.appendChild(p);});world.appendChild(eg);
 const ng=el('g');allNodes.forEach(n=>{const p=positions.get(n.id);if(!p)return;const t=typeOf(n).toLowerCase(),match=!query||[n.id,labelOf(n),n.text,typeOf(n)].join(' ').toLowerCase().includes(query.toLowerCase()),sel=selected&&selected.kind==='node'&&selected.id===n.id;const g=el('g',{class:'ac-node type-'+(t==='hypothesis'?'hypothesis':t==='outcome'?'outcome':t==='evidence'?'evidence':'other')+(sel?' selected':'')+(query&&match?' search-hit':'')+(query&&!match?' search-dim':''),transform:'translate('+p.x+' '+p.y+')',tabindex:0,role:'button','aria-label':n.id+' '+labelOf(n)});g.appendChild(el('rect',{width:NW,height:NH,rx:8}));g.appendChild(el('text',{class:'ac-node-id',x:11,y:19},n.id));const a=Array.from(labelOf(n)),title=a.length>31?a.slice(0,30).join('')+'…':a.join('');g.appendChild(el('text',{class:'ac-node-label',x:11,y:41},title));g.appendChild(el('text',{class:'ac-node-type',x:11,y:61},typeOf(n)+(p.cycle===null?' · Cycle 未标记':' · Cycle '+p.cycle)));const s=n.semantic||{};g.appendChild(el('text',{class:'ac-node-type',x:11,y:80},Array.from(String(s.scientific_status||s.frontier_status||'')).slice(0,30).join('')));g.appendChild(el('title',{},n.id+' · '+labelOf(n)+' · '+typeOf(n)));g.addEventListener('click',ev=>{ev.stopPropagation();selectNode(n.id,true);});g.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();selectNode(n.id,true);}});ng.appendChild(g);});world.appendChild(ng);svg.appendChild(world);viewport.replaceChildren(svg);applyScale();
}
function applyScale(){if(svg){svg.setAttribute('width',Math.round(size.width*scale));svg.setAttribute('height',Math.round(size.height*scale));}}
function setHash(kind,id){try{const hash='#'+encodeURIComponent(key(kind,id));if(location.hash!==hash)history.pushState({...((history.state&&typeof history.state==='object')?history.state:{}),tmExplorerLayoutAC:{kind,id}},'',hash);}catch(_){}}
function focusNode(id){const p=positions.get(id);if(p)viewport.scrollTo({left:Math.max(0,p.x*scale-viewport.clientWidth*.35),top:Math.max(0,p.y*scale-viewport.clientHeight*.32),behavior:'smooth'});}
function refButtons(refs){if(!Array.isArray(refs)||!refs.length)return '<p class="ac-meta">暂无记录</p>';return '<div class="ac-ref-list">'+refs.map(r=>'<button type="button" data-ref-kind="'+esc(r.kind)+'" data-ref-id="'+esc(r.id)+'">'+esc(r.label||r.id)+' →</button>').join('')+'</div>';}
function relList(items,dir){if(!items.length)return '<p class="ac-meta">没有显式关系。</p>';return '<ul class="ac-relations">'+items.map(e=>{const id=dir==='in'?e.fromNode:e.toNode;return '<li><button type="button" data-jump-node="'+esc(id)+'">'+esc(id)+'</button><strong>'+esc(labelOf(nodeById.get(id)||{id}))+'</strong><div class="ac-meta">'+esc(e.edge_type||e.label||'关系类型未标注')+(e.evolution_operator?' · '+esc(e.evolution_operator):'')+'</div></li>';}).join('')+'</ul>';}
function selectNode(id,focus,historyPush=true){const n=nodeById.get(id);if(!n)return;selected={kind:'node',id};if(historyPush)setHash('node',id);draw();nodeDetail(n);if(focus)focusNode(id);}
function nodeDetail(n){const s=n.semantic||{},incoming=allEdges.filter(e=>e.toNode===n.id),outgoing=allEdges.filter(e=>e.fromNode===n.id),k=key('node',n.id),refs=index.references&&index.references[k]||[],back=index.backlinks&&index.backlinks[k]||[],outcomes=Array.isArray(s.cycle_outcomes)?s.cycle_outcomes:(s.outcome?[s.outcome]:[]);detail.innerHTML='<h2>'+esc(labelOf(n))+'</h2><div class="ac-meta">'+esc(n.id)+' · '+esc(typeOf(n))+' · '+esc(cycleOf(n)===null?'Cycle 未标记':'Cycle '+cycleOf(n))+'<br>科学状态：'+esc(s.scientific_status||'未标记')+'<br>前沿状态：'+esc(s.frontier_status||'未标记')+'<br>'+incoming.length+' 条前驱 · '+outgoing.length+' 条后继</div><div class="ac-md">'+md(n.text||'（该节点没有文本内容）',refs)+'</div><h3>Cycle Outcomes · '+outcomes.length+'</h3>'+(outcomes.length?'<ul class="ac-relations">'+outcomes.map(o=>'<li><strong>Cycle '+esc(o.cycle_id||'未标记')+' · '+esc(o.verdict||'未裁决')+'</strong>'+(o.summary?'<div class="ac-meta">'+esc(o.summary)+'</div>':'')+(o.judge_rationale?'<div class="ac-meta">'+esc(o.judge_rationale)+'</div>':'')+'</li>').join('')+'</ul>':'<p class="ac-meta">暂无 Cycle Outcome 记录。</p>')+'<h3>真实前驱 · '+incoming.length+'</h3>'+relList(incoming,'in')+'<h3>真实后继 · '+outgoing.length+'</h3>'+relList(outgoing,'out')+'<h3>文档引用 · '+refs.length+'</h3>'+refButtons(refs)+'<h3>反向引用 · '+back.length+'</h3>'+refButtons(back);}
function selectEdge(e,historyPush=true){selected={kind:'edge',id:edgeId(e)};if(historyPush)setHash('edge',edgeId(e));draw();detail.innerHTML='<h2>科研连线</h2><div class="ac-meta">'+esc(edgeId(e))+'<br>关系类型：'+esc(e.edge_type||e.label||'未标记')+'</div><h3>来源节点</h3><p><button data-jump-node="'+esc(e.fromNode)+'">'+esc(e.fromNode)+' · '+esc(labelOf(nodeById.get(e.fromNode)||{id:e.fromNode}))+' →</button></p><h3>目标节点</h3><p><button data-jump-node="'+esc(e.toNode)+'">'+esc(e.toNode)+' · '+esc(labelOf(nodeById.get(e.toNode)||{id:e.toNode}))+' →</button></p><h3>原始关系记录</h3><div class="ac-md">'+esc(JSON.stringify(e,null,2))+'</div>';}
async function loadDoc(path,offset,historyPush=true){try{const newDoc=!offset||docPath!==path;if(newDoc){docPath=path;docText='';}const d=await api('/api/canvas-md/tasks/'+encodeURIComponent(observerId)+'/document?path='+encodeURIComponent(path)+'&offset='+encodeURIComponent(offset||0));docText+=d.content||'';selected={kind:'doc',id:path};if(historyPush&&newDoc)setHash('doc',path);detail.innerHTML='<h2>'+esc(d.title||path)+'</h2><div class="ac-meta">Markdown 文档 · '+esc(path)+'</div><div class="ac-md">'+md(docText,index.references&&index.references[key('doc',path)]||[])+'</div>'+(d.next_offset!==null&&d.next_offset!==undefined?'<p><button id="ac-doc-more" data-path="'+esc(path)+'" data-offset="'+d.next_offset+'">加载后续 Markdown 内容 ↓</button></p>':'<p class="ac-meta">文档已读完。</p>');}catch(e){detail.textContent='无法读取 Markdown：'+(e&&e.message||String(e));}}
function searchResults(){results.replaceChildren();if(!query)return;const found=allNodes.filter(n=>[n.id,labelOf(n),n.text,typeOf(n)].join(' ').toLowerCase().includes(query.toLowerCase()));const label=document.createElement('span');label.className='ac-meta';label.textContent='匹配 '+found.length+' 个节点（图谱中的节点不会被移除）';results.appendChild(label);found.forEach(n=>{const b=document.createElement('button');b.type='button';b.textContent=n.id+' · '+labelOf(n);b.addEventListener('click',()=>selectNode(n.id,true));results.appendChild(b);});}
function zoom(v){scale=Math.max(.05,Math.min(1.8,v));applyScale();}
async function load(){stats.textContent='正在读取完整图谱…';try{index=await api('/api/canvas-md/tasks/'+encodeURIComponent(observerId)+'/index');if(!Array.isArray(index.nodes)||!Array.isArray(index.edges))throw Error('索引缺少 nodes 或 edges 数组');allNodes=index.nodes;allEdges=index.edges;nodeById=new Map(allNodes.map(n=>[n.id,n]));if(nodeById.size!==allNodes.length)throw Error('发现重复节点 ID，拒绝静默丢弃节点');layout();scale=1;selected=null;draw();const en=(index.stats&&index.stats.node_count)??allNodes.length,ee=(index.stats&&index.stats.edge_count)??allEdges.length;stats.textContent=allNodes.length+' / '+en+' 个节点 · '+allEdges.length+' / '+ee+' 条连线';notice.textContent='已载入完整 Canvas 节点与连线。'+(index.stats&&index.stats.partial?'文档索引分段读取；打开 Markdown 后可继续分页加载。':'Markdown 可从节点引用或反向引用打开。')+' 未记录时间的节点单独排列。';let hash='';try{hash=decodeURIComponent(location.hash.slice(1)||'');}catch(_){}const m=/^(node|edge|doc):(.*)$/.exec(hash);if(m){if(m[1]==='node'&&nodeById.has(m[2]))selectNode(m[2],false,false);else if(m[1]==='edge'){const e=allEdges.find(x=>edgeId(x)===m[2]);if(e)selectEdge(e,false);}else if(m[1]==='doc')await loadDoc(m[2],0,false);}if(!selected&&allNodes.length)selectNode(allNodes[0].id,false,false);searchResults();}catch(e){viewport.innerHTML='<div class="ac-empty ac-warning">图谱读取失败：'+esc(e&&e.message||String(e))+'</div>';stats.textContent='读取失败';}}
$('ac-search').addEventListener('input',function(){query=this.value||'';if(svg)svg.querySelectorAll('.ac-node').forEach(g=>{const n=nodeById.get(g.getAttribute('data-node-id')),hit=!query||[n.id,labelOf(n),n.text,typeOf(n)].join(' ').toLowerCase().includes(query.toLowerCase());g.classList.toggle('search-hit',Boolean(query&&hit));g.classList.toggle('search-dim',Boolean(query&&!hit));});searchResults();});
$('ac-search-clear').addEventListener('click',()=>{$('ac-search').value='';query='';if(svg)svg.querySelectorAll('.ac-node').forEach(g=>g.classList.remove('search-hit','search-dim'));searchResults();});
$('ac-zoom-in').addEventListener('click',()=>zoom(scale*1.2));$('ac-zoom-out').addEventListener('click',()=>zoom(scale/1.2));
$('ac-fit-width').addEventListener('click',()=>zoom(Math.min(1,Math.max(.05,(viewport.clientWidth-20)/size.width))));
$('ac-fit-all').addEventListener('click',()=>zoom(Math.max(.05,Math.min(1,(viewport.clientWidth-20)/size.width,(viewport.clientHeight-20)/size.height))));
$('ac-refresh').addEventListener('click',load);$('ac-back').addEventListener('click',()=>history.back());$('ac-forward').addEventListener('click',()=>history.forward());
viewport.addEventListener('pointerdown',e=>{if(e.target.closest&&e.target.closest('.ac-node,.ac-edge'))return;viewport.classList.add('is-panning');viewport.dataset.px=String(e.clientX);viewport.dataset.py=String(e.clientY);viewport.dataset.sx=String(viewport.scrollLeft);viewport.dataset.sy=String(viewport.scrollTop);try{viewport.setPointerCapture(e.pointerId);}catch(_){}});
viewport.addEventListener('pointermove',e=>{if(!viewport.classList.contains('is-panning'))return;viewport.scrollLeft=Number(viewport.dataset.sx||0)-(e.clientX-Number(viewport.dataset.px||e.clientX));viewport.scrollTop=Number(viewport.dataset.sy||0)-(e.clientY-Number(viewport.dataset.py||e.clientY));});
for(const n of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(n,()=>viewport.classList.remove('is-panning'));
detail.addEventListener('click',e=>{const n=e.target.closest('[data-jump-node]');if(n){selectNode(n.getAttribute('data-jump-node'),true);return;}const r=e.target.closest('[data-ref-kind]');if(r){const kind=r.getAttribute('data-ref-kind'),id=r.getAttribute('data-ref-id');if(kind==='node')selectNode(id,true);else if(kind==='doc')loadDoc(id,0);return;}const more=e.target.closest('#ac-doc-more');if(more)loadDoc(more.getAttribute('data-path'),Number(more.getAttribute('data-offset')));});
window.addEventListener('resize',()=>{if(scale<.5)applyScale();});window.addEventListener('popstate',()=>{let h='';try{h=decodeURIComponent(location.hash.slice(1)||'');}catch(_){}const m=/^(node|edge|doc):(.*)$/.exec(h);if(!m)return;if(m[1]==='node'&&nodeById.has(m[2]))selectNode(m[2],false,false);else if(m[1]==='edge'){const e=allEdges.find(x=>edgeId(x)===m[2]);if(e)selectEdge(e,false);}else if(m[1]==='doc')loadDoc(m[2],0,false);});load();
})();
</script>
"""


def explorer_layout_a0_demo_html(search_root: Path, state: dict, selected: str = "") -> str:
    """Render the independent full-data A+C timeline/branch graph."""
    observer_id = str(selected or "").strip()
    try:
        vault_root(search_root, observer_id, state)
    except ExplorerError as exc:
        body = ('<section class="ac-hero"><h1>临时图谱布局实验</h1><p>无法读取指定科研任务：'
                + html.escape(str(exc)) + '</p><a href="/research-explorer/">返回原图谱</a></section>')
        return dashboard_shell("临时研究图谱布局实验 · A0 原始版", AC_CSS + body,
                               active="explorer", breadcrumb_leaf="布局实验 A0 原始版")
    original = "/research-explorer/?observer_id=" + quote(observer_id, safe="")
    layout_b = "/research-explorer-tmp-b/?observer_id=" + quote(observer_id, safe="")
    current_a_c = "/research-explorer-tmp/?observer_id=" + quote(observer_id, safe="")
    body = (AC_CSS + AC_HTML.replace("TM_OBSERVER_ID", html.escape(observer_id, quote=True))
            .replace("TM_ORIGINAL_HREF", html.escape(original, quote=True))
            .replace("TM_LAYOUT_B_HREF", html.escape(layout_b, quote=True))
            .replace("TM_CURRENT_A_C_HREF", html.escape(current_a_c, quote=True)))
    return dashboard_shell("临时研究图谱布局实验 · A0 原始版", body, active="explorer",
                           back_href=original, breadcrumb_leaf="A0 原始布局演示")
