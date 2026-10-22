"""Independent read-only B prototype: group explicit frontier anchors with their real ancestor paths."""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from .canvas_md import ExplorerError, vault_root
from .dashboard_ui import dashboard_shell


B_CSS = r"""
<style>
.tm-layout-b{color:#e7edf7;max-width:none}
.tm-layout-b .b-hero{padding:16px 18px;background:#171b25;border:1px solid #30394a;border-radius:12px;margin-bottom:12px}
.tm-layout-b .b-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.tm-layout-b .b-eyebrow{font-size:10px;letter-spacing:.15em;font-weight:800;color:#c4b5fd;margin-bottom:5px}
.tm-layout-b h1{font-size:22px;line-height:1.3;margin:0 0 7px}
.tm-layout-b .b-subtitle{font-size:13px;color:#b6c2d6;line-height:1.55;margin:0;max-width:950px}
.tm-layout-b .b-links{display:flex;gap:7px;flex-wrap:wrap}
.tm-layout-b .b-link{display:inline-flex;align-items:center;white-space:nowrap;border:1px solid #4b5a73;background:#202838;color:#dbeafe;border-radius:8px;padding:8px 11px;text-decoration:none;font-size:12px}
.tm-layout-b .b-link.current{border-color:#8b7ad4;background:#2b2543;color:#ddd6fe}
.tm-layout-b .b-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:14px}
.tm-layout-b button,.tm-layout-b input{font:inherit}
.tm-layout-b button{cursor:pointer;border:1px solid #475569;background:#242a36;color:#e5edf8;border-radius:7px;padding:7px 11px;font-size:12px}
.tm-layout-b button:hover,.tm-layout-b button:focus-visible{border-color:#c4b5fd;background:#302b45}
.tm-layout-b button[aria-pressed="true"]{border-color:#a78bfa;background:#3b2f63;color:#f5f3ff}
.tm-layout-b .b-search{min-width:190px;flex:1;max-width:360px;background:#11151e;color:#f1f5f9;border:1px solid #424b5e;border-radius:7px;padding:8px 10px}
.tm-layout-b .b-stats{font-size:11px;color:#bac5d8;margin-left:auto}
.tm-layout-b .b-note{border-left:3px solid #a78bfa;padding:9px 11px;background:#201d2b;color:#d8d2ec;font-size:12px;line-height:1.6;border-radius:0 7px 7px 0;margin-top:12px}
.tm-layout-b .b-main{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px;align-items:start}
.tm-layout-b .b-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,410px),1fr));gap:12px;align-items:start}
.tm-layout-b .b-card,.tm-layout-b .b-detail-panel{min-width:0;background:#11151d;border:1px solid #303847;border-radius:11px;overflow:hidden}
.tm-layout-b .b-card-head{padding:12px 13px 10px;border-bottom:1px solid #2a3342;background:#171c27}
.tm-layout-b .b-card-title{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.tm-layout-b .b-card-title strong{font-size:13px;line-height:1.5;overflow-wrap:anywhere}
.tm-layout-b .b-anchor{font-size:12px;color:#c4b5fd;font-weight:800;margin-top:5px}
.tm-layout-b .b-reason{font-size:11px;color:#b6c2d6;line-height:1.5;margin:7px 0 0}
.tm-layout-b .b-status{display:inline-block;padding:3px 7px;border-radius:5px;font-size:10px;font-weight:750;background:#293241;color:#cbd5e1;white-space:nowrap}
.tm-layout-b .b-status.active{background:#153a32;color:#a7f3d0}.tm-layout-b .b-status.parked{background:#3b2e16;color:#fde68a}.tm-layout-b .b-status.closed{background:#293241;color:#cbd5e1}.tm-layout-b .b-status.unknown{background:#382b48;color:#e9d5ff}
.tm-layout-b .b-graph-scroll{overflow:auto;max-width:100%;background:#10141c}
.tm-layout-b .b-mini-graph{display:block;min-width:330px}
.tm-layout-b .b-node{cursor:pointer;outline:none}
.tm-layout-b .b-node rect{fill:#1b2230;stroke:#526076;stroke-width:1.3}
.tm-layout-b .b-node.status-active rect{stroke:#258d71}.tm-layout-b .b-node.status-parked rect{stroke:#a77b22}.tm-layout-b .b-node.status-closed rect{stroke:#586477}
.tm-layout-b .b-node.selected rect{stroke:#a78bfa;stroke-width:3;fill:#2b2543}
.tm-layout-b .b-node.shared rect{stroke-dasharray:4 3}
.tm-layout-b .b-node:focus-visible rect{stroke:#f8fafc;stroke-width:3}
.tm-layout-b .b-node-id{font-size:11px;font-weight:800;fill:#f1f5f9}
.tm-layout-b .b-node-label{font-size:10px;fill:#dbe4f2}
.tm-layout-b .b-edge{fill:none;stroke:#8592a7;stroke-width:1.6;opacity:.78}
.tm-layout-b .b-shared-note{padding:7px 12px;font-size:10px;color:#9eabc0;border-top:1px solid #252d3a}
.tm-layout-b .b-detail{padding:14px;max-height:calc(100vh - 300px);min-height:300px;overflow:auto;overflow-wrap:anywhere}
.tm-layout-b .b-detail h2{font-size:16px;line-height:1.4;margin:0 0 6px}
.tm-layout-b .b-detail h3{font-size:12px;color:#dce6f6;margin:15px 0 7px;padding-top:10px;border-top:1px solid #303847}
.tm-layout-b .b-meta{font-size:11px;color:#9eabc0;line-height:1.6}
.tm-layout-b .b-relations{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px}
.tm-layout-b .b-relations li{font-size:11px;line-height:1.45;background:#191f2a;border:1px solid #2d3748;border-radius:6px;padding:7px 8px}
.tm-layout-b .b-relations button{font-size:11px;padding:3px 6px;margin-right:5px}
.tm-layout-b .b-rel-id{font-weight:800;color:#ddd6fe}
.tm-layout-b .b-empty{padding:24px;color:#a8b5c9;font-size:13px;line-height:1.6}
@media(max-width:1100px){.tm-layout-b .b-main{grid-template-columns:minmax(0,1fr)}.tm-layout-b .b-detail{max-height:none;min-height:0}.tm-layout-b .b-groups{grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))}}
@media(max-width:700px){.tm-layout-b .b-head{flex-direction:column}.tm-layout-b .b-stats{width:100%;margin-left:0}.tm-layout-b .b-search{max-width:none;flex-basis:100%}.tm-layout-b h1{font-size:19px}}
</style>
"""

B_HTML = r"""
<div class="tm-layout-b" id="tm-layout-b" data-observer-id="TM_OBSERVER_ID">
  <section class="b-hero">
    <div class="b-head">
      <div>
        <div class="b-eyebrow">TEMPORARY LAYOUT LAB · OPTION B · READ ONLY</div>
        <h1>研究图谱 · 前沿分组视图</h1>
        <p class="b-subtitle">每个明确记录的 frontier 是一个锚点；卡片展示它沿真实 hypothesis_evolution 边回溯的祖先路径。共享祖先会在不同卡片重复显示，便于比较多个前沿如何分叉或汇合。</p>
      </div>
      <div class="b-links">
        <a class="b-link" href="TM_ORIGINAL_HREF">原图谱</a>
        <a class="b-link" href="TM_LAYOUT_A_HREF">A+C 版</a>
      </div>
    </div>
    <div class="b-toolbar">
      <button id="b-toggle-closed" type="button" aria-pressed="false">显示已关闭前沿</button>
      <input id="b-search" class="b-search" type="search" placeholder="搜索前沿、H 编号或猜想标题…" aria-label="搜索前沿分组"/>
      <button id="b-reset" type="button">清除搜索与选择</button>
      <span id="b-stats" class="b-stats">读取只读图谱中…</span>
    </div>
    <div id="b-note" class="b-note">临时页面不写入 Canvas、SQLite、研究任务或原页面配置。组内路径是从前沿锚点沿真实边反向遍历得到的，不代表系统已声明这些祖先属于该前沿。</div>
  </section>
  <div class="b-main">
    <section>
      <div id="b-groups" class="b-groups"><div class="b-empty">正在读取真实前沿记录…</div></div>
      <div id="b-error" class="b-empty" hidden></div>
    </section>
    <aside class="b-detail-panel">
      <div class="b-card-head"><strong>节点与关系详情</strong><div class="b-meta">只读 · 真实边</div></div>
      <div id="b-detail" class="b-detail"><p class="b-meta">点击任一卡片中的节点，查看其前驱、后继和明确登记的前沿状态。</p></div>
    </aside>
  </div>
</div>
<script>
(function(){
'use strict';
var root=document.getElementById('tm-layout-b');
if(!root)return;
var observerId=root.getAttribute('data-observer-id');
var $=function(id){return document.getElementById(id);};
var groupHost=$('b-groups'),detail=$('b-detail'),note=$('b-note'),stats=$('b-stats'),errorBox=$('b-error');
var NS='http://www.w3.org/2000/svg';
var allNodes=[],allEdges=[],nodes=[],edges=[],lookup=new Map(),incoming=new Map(),outgoing=new Map();
var groups=[],sharedCounts=new Map(),selectedId=null,showClosed=false,query='';
function svgEl(name,attrs,text){
  var item=document.createElementNS(NS,name);
  Object.keys(attrs||{}).forEach(function(k){item.setAttribute(k,String(attrs[k]));});
  if(text!==undefined&&text!==null)item.textContent=String(text);
  return item;
}
function esc(value){
  return String(value===undefined||value===null?'':value).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function hNumber(id){var m=/^H-(\d{1,6})$/.exec(String(id||''));return m?Number(m[1]):null;}
function cycleValue(node){
  var value=node&&node.semantic?node.semantic.cycle:null;
  if(value===null||value===undefined||String(value).trim()==='')return null;
  value=Number(value);return Number.isFinite(value)?value:null;
}
function statusOf(frontier,node){
  var status=frontier&&frontier.status;
  if(['active','parked','closed'].indexOf(status)>=0)return status;
  status=node&&node.semantic?node.semantic.frontier_status:null;
  return ['active','parked','closed'].indexOf(status)>=0?status:'unknown';
}
function statusLabel(status){return ({active:'进行中',parked:'暂缓',closed:'已关闭',unknown:'未标记'})[status]||'未标记';}
function labelOf(node){return String(node&&node.label||node&&node.semantic&&node.semantic.title||node&&node.id||'未命名节点');}
function compareNodes(a,b){
  var ca=cycleValue(a),cb=cycleValue(b);
  if(ca!==null&&cb!==null&&ca!==cb)return ca-cb;
  if(ca!==null&&cb===null)return -1;
  if(ca===null&&cb!==null)return 1;
  return String(a.id).localeCompare(String(b.id));
}
function ancestorsFor(anchorId){
  var seen=new Set([anchorId]),queue=[anchorId];
  while(queue.length){
    var id=queue.shift();
    (incoming.get(id)||[]).forEach(function(edge){
      var parent=edge.fromNode;
      if(!lookup.has(parent)||seen.has(parent))return;
      seen.add(parent);queue.push(parent);
    });
  }
  return seen;
}
function buildGroups(){
  var result=[];
  nodes.forEach(function(node){
    var sem=node.semantic||{};
    var frontiers=Array.isArray(sem.frontiers)?sem.frontiers:[];
    if(!frontiers.length){
      result.push({id:'unrecorded:'+node.id,anchorId:node.id,anchor:node,status:'unknown',
        reason:'该猜想没有显式 frontiers 记录；此卡仅表示未登记分组，不推断前沿。',
        explicit:false,nodeIds:new Set([node.id])});
      return;
    }
    frontiers.forEach(function(frontier,i){
      var anchorId=String(frontier&&frontier.hypothesis_id||node.id);
      var anchor=lookup.get(anchorId);
      if(!anchor||!nodes.some(function(n){return n.id===anchorId;}))return;
      var nodeIds=ancestorsFor(anchorId);
      result.push({id:String(frontier&&frontier.id||('frontier:'+anchorId+':'+i)),
        anchorId:anchorId,anchor:anchor,status:statusOf(frontier,node),
        reason:String(frontier&&frontier.reason||''),explicit:true,nodeIds:nodeIds,frontier:frontier});
    });
  });
  result.sort(function(a,b){
    var rank={active:0,parked:1,unknown:2,closed:3};
    return (rank[a.status]??2)-(rank[b.status]??2)||compareNodes(a.anchor,b.anchor)||a.id.localeCompare(b.id);
  });
  sharedCounts=new Map();
  result.forEach(function(group){group.nodeIds.forEach(function(id){sharedCounts.set(id,(sharedCounts.get(id)||0)+1);});});
  result.forEach(function(group){
    group.nodes=Array.from(group.nodeIds).map(function(id){return lookup.get(id);}).filter(Boolean).sort(compareNodes);
    group.edges=edges.filter(function(edge){return group.nodeIds.has(edge.fromNode)&&group.nodeIds.has(edge.toNode);});
    group.externalParents=allEdges.filter(function(edge){return edge.toNode===group.anchorId&&!group.nodeIds.has(edge.fromNode);});
  });
  return result;
}
function matchesGroup(group){
  if(!showClosed&&group.status==='closed')return false;
  if(!query)return true;
  var haystack=[group.id,group.anchorId,labelOf(group.anchor),group.reason];
  group.nodes.forEach(function(node){haystack.push(node.id,labelOf(node));});
  return haystack.join(' ').toLowerCase().indexOf(query.toLowerCase())>=0;
}
function miniGraph(group,groupIndex){
  var svg=svgEl('svg',{class:'b-mini-graph',role:'img','aria-label':group.id+' 的真实祖先路径'});
  var rowByCycle=new Map(),row=0;
  group.nodes.forEach(function(node){
    var c=cycleValue(node),key=c===null?'unknown:'+node.id:'cycle:'+c;
    if(!rowByCycle.has(key))rowByCycle.set(key,row++);
  });
  var sameCycleIndex=new Map(),positions=new Map(),maxX=0;
  group.nodes.forEach(function(node){
    var c=cycleValue(node),key=c===null?'unknown:'+node.id:'cycle:'+c;
    var lane=sameCycleIndex.get(key)||0;sameCycleIndex.set(key,lane+1);
    var pos={x:18+lane*158,y:25+(rowByCycle.get(key)||0)*72};
    positions.set(node.id,pos);maxX=Math.max(maxX,pos.x+142);
  });
  var edgeLaneBase=maxX+7;
  var width=edgeLaneBase+Math.max(1,group.edges.length)*13+28;
  var height=Math.max(100,24+row*72);
  svg.setAttribute('width',width);svg.setAttribute('height',height);
  svg.setAttribute('viewBox','0 0 '+width+' '+height);svg.style.width=width+'px';svg.style.height=height+'px';
  var defs=svgEl('defs');
  var marker=svgEl('marker',{id:'b-arrow-'+groupIndex,viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:5,markerHeight:5,orient:'auto'});
  marker.appendChild(svgEl('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#8592a7'}));defs.appendChild(marker);svg.appendChild(defs);
  group.edges.forEach(function(edge,i){
    var from=positions.get(edge.fromNode),to=positions.get(edge.toNode);if(!from||!to)return;
    var lane=edgeLaneBase+i*13;
    var path=svgEl('path',{class:'b-edge',
      d:'M '+(from.x+140)+' '+(from.y+22)+' C '+lane+' '+(from.y+22)+', '+lane+' '+(to.y+22)+', '+(to.x+140)+' '+(to.y+22),
      'marker-end':'url(#b-arrow-'+groupIndex+')'});
    path.appendChild(svgEl('title',{},edge.fromNode+' → '+edge.toNode+' · '+(edge.evolution_operator||edge.edge_type||'真实关系')));
    svg.appendChild(path);
  });
  group.nodes.forEach(function(node){
    var pos=positions.get(node.id),status=node.semantic&&node.semantic.frontier_status||'unknown';
    var shared=(sharedCounts.get(node.id)||0)>1;
    var nodeGroup=svgEl('g',{class:'b-node status-'+status+(node.id===selectedId?' selected':'')+(shared?' shared':''),
      tabindex:'0',role:'button','data-node-id':node.id,'aria-label':node.id+' '+labelOf(node)});
    nodeGroup.appendChild(svgEl('rect',{x:pos.x,y:pos.y,width:140,height:45,rx:7}));
    nodeGroup.appendChild(svgEl('text',{class:'b-node-id',x:pos.x+8,y:pos.y+14},node.id+(shared?' · 共享':'')+(node.id===group.anchorId?' · 锚点':'')));
    var title=labelOf(node);if(Array.from(title).length>19)title=Array.from(title).slice(0,18).join('')+'…';
    nodeGroup.appendChild(svgEl('text',{class:'b-node-label',x:pos.x+8,y:pos.y+31},title));
    nodeGroup.appendChild(svgEl('title',{},node.id+' · '+labelOf(node)+(shared?' · 出现在多个前沿回溯路径中':'')));
    nodeGroup.addEventListener('click',function(){selectedId=node.id;renderDetails();renderGroups();});
    nodeGroup.addEventListener('keydown',function(event){if(event.key==='Enter'||event.key===' '){event.preventDefault();selectedId=node.id;renderDetails();renderGroups();}});
    svg.appendChild(nodeGroup);
  });
  return svg;
}
function renderGroups(){
  $('b-toggle-closed').setAttribute('aria-pressed',String(showClosed));
  $('b-toggle-closed').textContent=showClosed?'隐藏已关闭前沿':'显示已关闭前沿';
  groupHost.replaceChildren();
  var visible=groups.filter(matchesGroup);
  stats.textContent=visible.length+' / '+groups.length+' 个前沿组 · '+nodes.length+' 个 H · '+edges.length+' 条真实图内边';
  if(!visible.length){
    groupHost.appendChild(document.createTextNode('没有符合当前筛选条件的前沿分组。'));
    return;
  }
  visible.forEach(function(group,index){
    var card=document.createElement('article');card.className='b-card';
    var head=document.createElement('div');head.className='b-card-head';
    var title=document.createElement('div');title.className='b-card-title';
    var left=document.createElement('div');
    var status=document.createElement('span');status.className='b-status '+group.status;status.textContent=statusLabel(group.status);left.appendChild(status);
    var id=document.createElement('div');id.className='b-anchor';id.textContent=group.id;left.appendChild(id);
    var anchor=document.createElement('strong');anchor.textContent=group.anchorId+' · '+labelOf(group.anchor);left.appendChild(anchor);
    title.appendChild(left);
    var count=document.createElement('span');count.className='b-meta';count.textContent=group.nodes.length+' 个节点';title.appendChild(count);
    head.appendChild(title);
    if(group.reason){var reason=document.createElement('p');reason.className='b-reason';reason.textContent=group.reason;head.appendChild(reason);}
    var sub=document.createElement('p');sub.className='b-reason';
    sub.textContent='祖先回溯路径 · '+group.edges.length+' 条真实边'+(group.externalParents.length?' · '+group.externalParents.length+' 条前序关系未展开':'');
    head.appendChild(sub);card.appendChild(head);
    var graphWrap=document.createElement('div');graphWrap.className='b-graph-scroll';graphWrap.appendChild(miniGraph(group,index));card.appendChild(graphWrap);
    var shared=group.nodes.filter(function(n){return (sharedCounts.get(n.id)||0)>1;});
    if(shared.length){var sharedNote=document.createElement('div');sharedNote.className='b-shared-note';sharedNote.textContent='虚线节点为共享祖先：'+shared.map(function(n){return n.id;}).join('、')+'。共享表示多条回溯路径相交，不是前沿成员声明。';card.appendChild(sharedNote);}
    groupHost.appendChild(card);
  });
}
function detailRelation(edge,direction){
  var otherId=direction==='in'?edge.fromNode:edge.toNode,other=lookup.get(otherId);
  return '<li><button type="button" data-jump="'+esc(otherId)+'">'+esc(otherId)+'</button>'+
    '<span class="b-rel-id">'+esc(labelOf(other||{id:otherId}))+'</span>'+
    '<div class="b-meta">'+esc(edge.edge_type||'关系类型未标注')+
    (edge.evolution_operator?' · '+esc(edge.evolution_operator):'')+
    (edge.originating_outcome?' · 来源 '+esc(edge.originating_outcome):'')+'</div></li>';
}
function renderDetails(){
  if(!selectedId){detail.innerHTML='<p class="b-meta">点击任一节点查看详情。</p>';return;}
  var node=lookup.get(selectedId);if(!node){detail.textContent='节点不在当前图谱索引中。';return;}
  var sem=node.semantic||{},frontiers=Array.isArray(sem.frontiers)?sem.frontiers:[];
  var inbound=allEdges.filter(function(e){return e.toNode===node.id;});
  var outbound=allEdges.filter(function(e){return e.fromNode===node.id;});
  var appearances=groups.filter(function(g){return g.nodeIds.has(node.id);});
  detail.innerHTML='<h2>'+esc(labelOf(node))+'</h2>'+
    '<div class="b-meta">'+esc(node.id)+' · '+esc(cycleValue(node)!==null?'Cycle '+cycleValue(node):'Cycle 未知')+
    '<br>科学状态：'+esc(sem.scientific_status||'未标记')+
    '<br>当前前沿状态：'+esc(sem.frontier_status||'未标记')+
    '<br>在 '+appearances.length+' 个前沿回溯组中出现</div>'+
    '<h3>明确登记的 frontier 记录 · '+frontiers.length+'</h3>'+
    (frontiers.length?'<ul class="b-relations">'+frontiers.map(function(f){return '<li><span class="b-rel-id">'+esc(f.id||'未命名前沿')+'</span><div class="b-meta">'+esc(f.status||'状态未标记')+'</div>'+(f.reason?'<div class="b-meta">'+esc(f.reason)+'</div>':'')+'</li>';}).join('')+'</ul>':'<p class="b-meta">没有显式 frontiers 记录。</p>')+
    '<h3>真实前驱 · '+inbound.length+'</h3>'+
    (inbound.length?'<ul class="b-relations">'+inbound.map(function(e){return detailRelation(e,'in');}).join('')+'</ul>':'<p class="b-meta">没有显式前驱边。</p>')+
    '<h3>真实后继 · '+outbound.length+'</h3>'+
    (outbound.length?'<ul class="b-relations">'+outbound.map(function(e){return detailRelation(e,'out');}).join('')+'</ul>':'<p class="b-meta">没有显式后继边。</p>')+
    '<p class="b-meta">组内祖先仅由图谱真实关系回溯得到，不应解读为原始数据已声明的前沿成员关系。</p>';
  detail.querySelectorAll('[data-jump]').forEach(function(button){
    button.addEventListener('click',function(){selectedId=this.getAttribute('data-jump');renderDetails();renderGroups();});
  });
}
function loadError(message){
  errorBox.hidden=false;errorBox.textContent='图谱读取失败：'+message;
  groupHost.replaceChildren();stats.textContent='读取失败';
}
async function load(){
  try{
    var response=await fetch('/api/canvas-md/tasks/'+encodeURIComponent(observerId)+'/index',{cache:'no-store',credentials:'same-origin'});
    var payload=await response.json();
    if(!response.ok)throw new Error(payload.error||('HTTP '+response.status));
    if(!Array.isArray(payload.nodes)||!Array.isArray(payload.edges))throw new Error('只读图谱索引格式不符合预期');
    allNodes=payload.nodes;allEdges=payload.edges;lookup=new Map(allNodes.map(function(n){return [n.id,n];}));
    nodes=allNodes.filter(function(n){var num=hNumber(n.id);return num!==null&&num>=128&&(n.semantic||{}).type==='Hypothesis';}).sort(compareNodes);
    if(!nodes.length)throw new Error('当前索引没有 H-0128 及后续的 Hypothesis 节点');
    var nodeIds=new Set(nodes.map(function(n){return n.id;}));
    edges=allEdges.filter(function(e){return e.edge_type==='hypothesis_evolution'&&nodeIds.has(e.fromNode)&&nodeIds.has(e.toNode);});
    incoming=new Map(nodes.map(function(n){return [n.id,[]];}));
    outgoing=new Map(nodes.map(function(n){return [n.id,[]];}));
    edges.forEach(function(e){incoming.get(e.toNode).push(e);outgoing.get(e.fromNode).push(e);});
    groups=buildGroups();
    selectedId=(groups.find(function(g){return g.status==='active';})||groups.find(function(g){return g.status==='parked';})||groups[0]).anchorId;
    var multi=nodes.filter(function(n){return Array.isArray((n.semantic||{}).frontiers)&&(n.semantic||{}).frontiers.length>1;}).length;
    note.textContent='当前实际命中 '+nodes.length+' 个 H（只使用索引中真实存在的 H-0128+ 节点），建立 '+groups.length+' 个显式前沿锚点组；其中 '+multi+' 个 H 有多个显式 frontier 记录。纵向顺序按 Cycle 排序（没有可用 sortable_time），不是精确创建时间。共享节点表示真实祖先路径重叠，不代表数据声明的前沿成员关系。';
    errorBox.hidden=true;renderGroups();renderDetails();
  }catch(error){loadError(error&&error.message||String(error));}
}
$('b-toggle-closed').addEventListener('click',function(){showClosed=!showClosed;renderGroups();});
$('b-search').addEventListener('input',function(){query=this.value||'';renderGroups();});
$('b-reset').addEventListener('click',function(){query='';$('b-search').value='';selectedId=null;renderGroups();renderDetails();});
load();
})();
</script>
"""


def explorer_layout_b_html(search_root: Path, state: dict, selected: str = "") -> str:
    """Render an independent, read-only frontier-grouping prototype."""
    observer_id = str(selected or "").strip()
    try:
        vault_root(search_root, observer_id, state)
    except ExplorerError as exc:
        body = (
            '<section class="b-hero"><h1>临时布局实验 · B 前沿分组</h1>'
            '<p>无法读取指定科研任务：' + html.escape(str(exc)) + '</p>'
            '<a href="/research-explorer/">返回原图谱</a></section>'
        )
        return dashboard_shell("临时研究图谱布局实验 · B", B_CSS + body,
                               active="run", breadcrumb_leaf="B 方案 · 前沿分组")
    original_href = "/research-explorer/?observer_id=" + quote(observer_id, safe="") + "#node%3AH-0090"
    layout_a_href = "/research-explorer-tmp/?observer_id=" + quote(observer_id, safe="")
    body = (
        B_CSS
        + B_HTML.replace("TM_OBSERVER_ID", html.escape(observer_id, quote=True))
        .replace("TM_ORIGINAL_HREF", html.escape(original_href, quote=True))
        .replace("TM_LAYOUT_A_HREF", html.escape(layout_a_href, quote=True))
    )
    return dashboard_shell(
        "临时研究图谱布局实验 · B 前沿分组", body, active="run",
        back_href=original_href, breadcrumb_leaf="B 方案 · 前沿分组",
    )
