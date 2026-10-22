/* Sciver layout C. Display-only forest; scientific edges and statuses remain read-only. */
(function(root){
'use strict';
const NUM_W=188,NUM_H=65,COL=238,ROW=94,TOP=85,LEFT=48;
const statusNames={active:'活跃',parked:'搁置',closed:'已终止',unknown:'未标记'};
const outcomes={supported:'Supported',rejected:'Rejected',open:'Open',mixed:'Mixed',none:'未裁决',unadjudicated:'未裁决'};
const compare=(a,b)=>String(a).localeCompare(String(b),'en',{numeric:true});
const validStatus=s=>Object.hasOwn(statusNames,s)?s:'unknown';
function freezeCycle(n){
  const s=n.semantic||{},c=s.cycle;
  if(c===null||c===undefined||String(c).trim()==='')return null;
  const v=Number(c);return Number.isSafeInteger(v)&&v>=0?v:null;
}
function nodeOrder(a,b){
  const ac=freezeCycle(a),bc=freezeCycle(b);
  return (ac===null?Infinity:ac)-(bc===null?Infinity:bc)||compare(a.id,b.id);
}
function nodeStatus(n){
  const s=n.semantic||{},f=Array.isArray(s.frontiers)?s.frontiers:[];
  const statuses=f.map(x=>validStatus(x.status));
  return statuses.includes('active')?'active':statuses.includes('parked')?'parked':
    statuses.includes('closed')?'closed':validStatus(s.frontier_status);
}
function verdict(n){
  const s=n.semantic||{};
  const source=Array.isArray(s.cycle_outcomes)?s.cycle_outcomes.filter(x=>
    ['supported','rejected','open'].includes(x&&x.verdict) &&
    x.review_state!=='unadjudicated'):[];
  if(source.length){
    const v=[...new Set(source.map(x=>x.verdict))];return v.length===1?v[0]:'mixed';
  }
  const h=s.whole_h_assessment||s.outcome;
  if(h&&['supported','rejected'].includes(h.verdict)&&h.review_state!=='unadjudicated')return h.verdict;
  if(['supported','rejected'].includes(s.scientific_status))return s.scientific_status;
  return 'none';
}
function buildTree(rawNodes,rawEdges){
  // Nodes are unique; secondary edges are never discarded or rewritten.
  const nodes=rawNodes.filter(n=>(n.semantic||{}).type==='Hypothesis').sort(nodeOrder);
  const lookup=new Map(nodes.map(n=>[n.id,n]));
  const allEdges=rawEdges.filter(e=>e.edge_type==='hypothesis_evolution'&&
    lookup.has(e.fromNode)&&lookup.has(e.toNode)&&e.fromNode!==e.toNode);
  const incoming=new Map(nodes.map(n=>[n.id,[]])),outgoing=new Map(nodes.map(n=>[n.id,[]]));
  for(const e of allEdges){incoming.get(e.toNode).push(e);outgoing.get(e.fromNode).push(e);}
  const chronological=new Map(nodes.map((n,i)=>[n.id,i]));
  const parent=new Map(),primaryEdges=[],secondary=[];
  for(const n of nodes){
    const candidates=incoming.get(n.id).filter(e=>chronological.get(e.fromNode)<chronological.get(n.id))
      .sort((a,b)=>chronological.get(b.fromNode)-chronological.get(a.fromNode)||compare(a.fromNode,b.fromNode)||compare(a.id,b.id));
    if(candidates.length){const p=candidates[0];parent.set(n.id,p.fromNode);primaryEdges.push(p);}
    for(const e of incoming.get(n.id))if(!candidates.length||e!==candidates[0])secondary.push(e);
  }
  const children=new Map(nodes.map(n=>[n.id,[]]));
  for(const [child,p] of parent)children.get(p).push(child);
  for(const v of children.values())v.sort((a,b)=>nodeOrder(lookup.get(a),lookup.get(b)));
  const roots=nodes.filter(n=>!parent.has(n.id)).map(n=>n.id);
  const cycles=[...new Set(nodes.map(freezeCycle).filter(c=>c!==null))].sort((a,b)=>a-b);
  const cycleRank=new Map(cycles.map((c,i)=>[c,i]));
  const pos=new Map(),rootBounds=new Map(),subtree=new Map();
  let slot=0;
  function place(id,rootId,depth){
    const kids=children.get(id),start=slot;
    let y;
    if(!kids.length){y=TOP+slot++*ROW;}
    else{
      for(const kid of kids)place(kid,rootId,depth+1);
      const first=pos.get(kids[0]).y,last=pos.get(kids[kids.length-1]).y;
      y=(first+last)/2;
    }
    const n=lookup.get(id),c=freezeCycle(n);
    // Never invent a missing freeze Cycle; place unknowns in a separately-labelled band.
    const x=LEFT+(c===null?cycles.length+1+Math.min(depth,15):cycleRank.get(c))*COL;
    pos.set(id,{x,y,root:rootId,cycle:c,depth});
    const count={total:1,active:nodeStatus(n)==='active'?1:0,parked:nodeStatus(n)==='parked'?1:0,
      closed:nodeStatus(n)==='closed'?1:0,unknown:nodeStatus(n)==='unknown'?1:0,
      supported:verdict(n)==='supported'?1:0,rejected:verdict(n)==='rejected'?1:0};
    for(const kid of kids){const k=subtree.get(kid);for(const field of Object.keys(count))count[field]+=k[field];}
    subtree.set(id,count);
    return {start,end:slot};
  }
  for(const id of roots){
    const start=slot;place(id,id,0);rootBounds.set(id,{y1:TOP+start*ROW-36,y2:TOP+(slot-1)*ROW+NUM_H+36});
    slot+=0.8;
  }
  const width=Math.max(1200,...[...pos.values()].map(p=>p.x+NUM_W+55));
  const height=Math.max(620,TOP+(slot+0.7)*ROW);
  return {nodes,lookup,allEdges,incoming,outgoing,parent,children,roots,primaryEdges,secondary,
    cycles,pos,rootBounds,subtree,width,height};
}
if(typeof module!=='undefined'&&module.exports)module.exports={buildTree,freezeCycle,nodeStatus,verdict};
if(typeof document==='undefined')return;
const host=document.getElementById('tm-tree-c');
if(!host)return;
const observerId=host.getAttribute('data-observer-id');
const $=id=>document.getElementById(id);
const svg=$('c-svg'),view=$('c-viewport'),detail=$('c-detail');
const ns='http://www.w3.org/2000/svg';
const el=(name,attrs={},txt)=>{const n=document.createElementNS(ns,name);
  for(const k of Object.keys(attrs))n.setAttribute(k,String(attrs[k]));
  if(txt!==undefined)n.textContent=String(txt);return n;};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const brief=(s,n=27)=>{const a=Array.from(String(s||'').replace(/\s+/g,' ').trim());return a.length>n?a.slice(0,n-1).join('')+'…':a.join('');};
const title=n=>String((n.semantic||{}).title||n.label||n.id);
let tree=null,selected=null,query='',collapsed=new Set(),showAux=false;
let camera={x:0,y:0,s:1},drag=null,shown=new Set();
function shownIds(){
  const result=new Set();
  function walk(id){result.add(id);if(!collapsed.has(id))for(const kid of tree.children.get(id))walk(kid);}
  for(const id of tree.roots)walk(id);
  return result;
}
function ancestorPath(id){const a=[];let curr=id;
  while(curr&&tree.lookup.has(curr)){a.push(curr);curr=tree.parent.get(curr);}
  return a.reverse();
}
function reveal(id){
  for(const a of ancestorPath(id))collapsed.delete(a);
}
function updateCamera(){
  const w=Math.max(1,view.clientWidth),h=Math.max(1,view.clientHeight);
  svg.setAttribute('viewBox',[camera.x,camera.y,w/camera.s,h/camera.s].join(' '));
}
function fitAll(){
  if(!tree)return;const w=Math.max(1,view.clientWidth),h=Math.max(1,view.clientHeight);
  camera.s=Math.max(.018,Math.min(1.2,Math.min(w/(tree.width+140),h/(tree.height+140))));
  camera.x=-48;camera.y=-48;updateCamera();
}
function focus(id,keepScale=false){
  if(!tree||!tree.pos.has(id))return;
  const p=tree.pos.get(id),w=Math.max(1,view.clientWidth),h=Math.max(1,view.clientHeight);
  if(!keepScale)camera.s=Math.min(1.35,Math.max(.78,camera.s));
  camera.x=p.x+NUM_W/2-w/(camera.s*2);camera.y=p.y+NUM_H/2-h/(camera.s*2);updateCamera();
}
function zoom(factor,px,py){
  const box=view.getBoundingClientRect(),sx=px===undefined?box.width/2:px,sy=py===undefined?box.height/2:py;
  const old=camera.s,wx=camera.x+sx/old,wy=camera.y+sy/old;
  camera.s=Math.max(.018,Math.min(2.4,old*factor));
  camera.x=wx-sx/camera.s;camera.y=wy-sy/camera.s;updateCamera();
}
function wire(e,kind,highlight=false){
  const a=tree.pos.get(e.fromNode),b=tree.pos.get(e.toNode);
  if(!a||!b)return null;
  const y1=a.y+NUM_H/2,y2=b.y+NUM_H/2,mid=a.x+NUM_W+24;
  const d=kind==='c-aux'?
    'M '+(a.x+NUM_W)+' '+y1+' Q '+((a.x+b.x)/2)+' '+(Math.min(y1,y2)-42)+' '+b.x+' '+y2:
    'M '+(a.x+NUM_W)+' '+y1+' H '+mid+' V '+y2+' H '+b.x;
  const p=el('path',{d,class:'c-line'+(kind?' '+kind:'')+(highlight?' is-path':'')});
  const tooltip=el('title',{},e.fromNode+' → '+e.toNode+' · '+(e.evolution_operator||'科学演化关系')+
    (kind==='c-aux'?'（辅助来源，未参与树状布局）':''));
  p.append(tooltip);return p;
}
function render(){
  if(!tree)return;
  shown=shownIds();svg.replaceChildren();
  const pathIds=new Set(selected?ancestorPath(selected):[]);
  const segs=new Set();
  for(const id of pathIds)if(tree.parent.has(id))segs.add(tree.parent.get(id)+'|'+id);
  const rootsBg=el('g');
  for(const [id,b] of tree.rootBounds){
    rootsBg.append(el('rect',{x:8,y:b.y1,width:tree.width+25,height:b.y2-b.y1,
      rx:9,fill:'#2b3548','fill-opacity':'.13'}));
    rootsBg.append(el('text',{x:16,y:b.y1+13,class:'c-root-label'},
      '起始方向 · '+id+(tree.subtree.get(id).active?' · 含活跃后代':'')));
  }
  svg.append(rootsBg);
  const lines=el('g');
  for(const e of tree.primaryEdges){
    if(!shown.has(e.fromNode)||!shown.has(e.toNode))continue;
    const p=wire(e,'',segs.has(e.fromNode+'|'+e.toNode));if(p)lines.append(p);
  }
  if(showAux)for(const e of tree.secondary){
    if(!shown.has(e.fromNode)||!shown.has(e.toNode))continue;
    const p=wire(e,'c-aux');if(p)lines.append(p);
  }
  svg.append(lines);
  const group=el('g');
  for(const node of tree.nodes){
    if(!shown.has(node.id))continue;
    const p=tree.pos.get(node.id),status=nodeStatus(node),childCount=tree.children.get(node.id).length;
    const g=el('g',{class:'c-node'+(selected===node.id?' is-selected':'')+
      (query&&!((node.id+' '+title(node)).toLowerCase().includes(query.toLowerCase()))?' is-muted':''),
      transform:'translate('+p.x+' '+p.y+')','data-status':status,tabindex:0,role:'button',
      'aria-label':node.id+' '+title(node)});
    g.append(el('rect',{class:'c-node-box',width:NUM_W,height:NUM_H,rx:8}));
    g.append(el('rect',{x:0,y:0,width:5,height:NUM_H,rx:3,fill:status==='active'?'#38bdf8':status==='parked'?'#f5b845':status==='closed'?'#738091':'#536177'}));
    g.append(el('text',{x:13,y:19,'font-weight':'700'},node.id+
      (p.cycle!==null?' · C'+p.cycle:' · Cycle 未知')));
    g.append(el('text',{x:13,y:37,class:'c-subtitle'},brief(title(node),25)));
    const sub=tree.subtree.get(node.id);
    const label=outcomes[verdict(node)]+' · '+statusNames[status]+
      (sub.active&&status!=='active'?' · ↗'+sub.active+' 活跃':'');
    g.append(el('text',{x:13,y:54,class:'c-subtitle'},brief(label,33)));
    g.append(el('title',{},title(node)+' · '+statusNames[status]+' · '+outcomes[verdict(node)]));
    g.addEventListener('click',event=>{event.stopPropagation();select(node.id,false);});
    g.addEventListener('dblclick',event=>{event.stopPropagation();select(node.id,true);});
    g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(node.id,true);}});
    group.append(g);
    if(childCount){
      const fold=el('g',{class:'c-fold',transform:'translate('+(p.x+NUM_W+13)+' '+(p.y+NUM_H/2)+')',role:'button',
        tabindex:0,'aria-label':(collapsed.has(node.id)?'展开':'折叠')+node.id+' 后代'});
      fold.append(el('circle',{r:12}));
      fold.append(el('text',{x:0,y:4},collapsed.has(node.id)?'+':'−'));
      fold.append(el('title',{},'后代 '+(sub.total-1)+' 个 H · 活跃 '+sub.active+' · 搁置 '+sub.parked+
        ' · 已关闭 '+sub.closed));
      const fn=event=>{event.preventDefault();event.stopPropagation();toggle(node.id);};
      fold.addEventListener('click',fn);
      fold.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' ')fn(event);});
      group.append(fold);
    }
  }
  svg.append(group);updateStats();
}
function updateStats(){
  if(!tree)return;
  $('c-stats').textContent=tree.nodes.length+' 个 H · '+tree.allEdges.length+' 条真实演化边 · '+
    tree.secondary.length+' 条辅助来源 · 当前显示 '+shown.size+' 个 H';
  const agg={active:0,parked:0,closed:0,unknown:0};
  for(const n of tree.nodes)agg[nodeStatus(n)]++;
  $('c-summary').innerHTML='<div class="c-caption">节点 '+tree.nodes.length+' · 分支起点 '+tree.roots.length+
    '<br>Active '+agg.active+' / Parked '+agg.parked+'<br>Closed '+agg.closed+' / 未标记 '+agg.unknown+
    '<br>辅助科学关系 '+tree.secondary.length+'</div>';
}
function buttons(ids){
  return ids.map(id=>'<button type="button" data-jump="'+esc(id)+'">'+esc(id)+' · '+
    esc(brief(title(tree.lookup.get(id)),18))+'</button>').join('');
}
function relations(edges,inbound){
  if(!edges.length)return '<p class="c-meta">未记录</p>';
  return edges.map(e=>{const other=inbound?e.fromNode:e.toNode;
    return '<div><button type="button" data-jump="'+esc(other)+'">'+esc(other)+'</button>'+
      '<span class="c-meta">'+esc(e.evolution_operator||'hypothesis_evolution')+
      (tree.parent.get(inbound?selected:other)===(inbound?other:selected)?' · 显示主干':' · 其他真实来源')+
      '</span></div>';
  }).join('');
}
function renderDetail(){
  if(!selected||!tree.lookup.has(selected))return;
  const node=tree.lookup.get(selected),s=node.semantic||{},path=ancestorPath(selected),sum=tree.subtree.get(selected);
  const records=Array.isArray(s.cycle_outcomes)?s.cycle_outcomes:[];
  detail.innerHTML='<h2>'+esc(selected)+' · '+esc(title(node))+'</h2>'+
    '<div class="c-meta">首次冻结：'+esc(freezeCycle(node)===null?'Cycle 未知':'Cycle '+freezeCycle(node))+'</div>'+
    '<span class="c-chip '+nodeStatus(node)+'">'+statusNames[nodeStatus(node)]+'</span>'+
    '<span class="c-chip">'+outcomes[verdict(node)]+'</span>'+
    '<h3>科学命题</h3><p class="c-summary">'+esc(s.summary||node.text||'请通过原图谱查看完整科学文档。')+'</p>'+
    '<h3>分支后代</h3><p class="c-meta">共 '+sum.total+' 个 H（含自身） · '+sum.active+
    ' Active · '+sum.parked+' Parked · '+sum.closed+' Closed · '+sum.unknown+' 未标记</p>'+
    '<button id="c-fold-selected" type="button">'+(collapsed.has(selected)?'展开':'折叠')+'此分支</button>'+
    '<h3>显示主路径</h3><div>'+buttons(path)+'</div>'+
    '<h3>全部真实父节点 · '+tree.incoming.get(selected).length+'</h3>'+
    relations(tree.incoming.get(selected),true)+
    '<h3>全部真实子节点 · '+tree.outgoing.get(selected).length+'</h3>'+
    relations(tree.outgoing.get(selected),false)+
    '<h3>Cycle Outcome · '+records.length+'</h3>'+
    (records.length?records.map(o=>'<details><summary class="c-meta">Cycle '+esc(o.cycle_id||'未标记')+
      ' · '+esc(o.review_state==='unadjudicated'?'未裁决':o.verdict||'未知')+'</summary>'+
      '<p>'+esc(o.summary||o.judge_rationale||'未提供摘要')+'</p></details>').join(''):'<p class="c-meta">无可读取的历史 Cycle Outcome</p>')+
    '<h3>原始研究文档</h3><p><a href="/research-explorer/?observer_id='+
    encodeURIComponent(observerId)+'#'+encodeURIComponent('node:'+selected)+
    '">在正式 Research Explorer 中查看完整节点与 Markdown →</a></p>'+
    '<p class="c-warning">本图的主要父节点仅供视觉布局使用，不能替代数据库中的全部科学来源。</p>';
  detail.querySelectorAll('[data-jump]').forEach(button=>
    button.addEventListener('click',()=>select(button.getAttribute('data-jump'),true)));
  const fold=$('c-fold-selected');if(fold)fold.addEventListener('click',()=>toggle(selected));
}
function select(id,center){
  if(!tree||!tree.lookup.has(id))return;
  reveal(id);selected=id;render();renderDetail();
  if(center)focus(id);
}
function toggle(id){
  if(collapsed.has(id))collapsed.delete(id);else collapsed.add(id);
  render();if(selected)renderDetail();
}
function renderNav(){
  if(!tree)return;
  const active=tree.nodes.filter(n=>nodeStatus(n)==='active');
  $('c-frontiers').innerHTML=active.length?buttons(active.map(n=>n.id)):'<p class="c-caption">没有 Active 记录。</p>';
  const hits=query?tree.nodes.filter(n=>(n.id+' '+title(n)+' '+(n.semantic||{}).summary).toLowerCase().includes(query.toLowerCase())):[];
  $('c-results').innerHTML=query?(hits.length?buttons(hits.slice(0,40).map(n=>n.id))+'<small>共 '+hits.length+' 条命中</small>':'无匹配猜想'):
    '输入关键词可定位并自动展开被折叠的研究路径。';
  host.querySelectorAll('.c-side [data-jump]').forEach(b=>
    b.addEventListener('click',()=>select(b.getAttribute('data-jump'),true)));
}
function loadError(msg){const b=$('c-error');b.hidden=false;b.textContent='布局 C 只读图谱加载失败：'+msg;}
async function load(){
  try{
    const r=await fetch('/api/canvas-md/tasks/'+encodeURIComponent(observerId)+'/index',
      {cache:'no-store',credentials:'same-origin'});
    const data=await r.json();
    if(!r.ok)throw Error(data.error||'HTTP '+r.status);
    if(!Array.isArray(data.nodes)||!Array.isArray(data.edges))throw Error('科学图索引格式无效');
    tree=buildTree(data.nodes,data.edges);
    if(!tree.nodes.length)throw Error('没有可用的 Hypothesis 节点');
    $('c-error').hidden=true;renderNav();render();
    const first=tree.nodes.find(n=>nodeStatus(n)==='active')||tree.nodes[0];
    select(first.id,false);
    // Prefer a legible active branch over a fully compressed, unreadable overview.
    focus(first.id);
  }catch(e){loadError(e&&e.message||String(e));}
}
$('c-fit').onclick=fitAll;
$('c-plus').onclick=()=>zoom(1.25);
$('c-minus').onclick=()=>zoom(1/1.25);
$('c-expand').onclick=()=>{collapsed.clear();render();if(selected)renderDetail();};
$('c-aux').onclick=()=>{showAux=!showAux;$('c-aux').setAttribute('aria-pressed',String(showAux));render();};
$('c-active').onclick=()=>{if(!tree)return;const n=tree.nodes.find(n=>nodeStatus(n)==='active');if(n)select(n.id,true);};
$('c-query').oninput=e=>{query=e.target.value.trim();renderNav();render();};
$('c-reset').onclick=()=>{query='';$('c-query').value='';renderNav();render();};
view.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.c-node,.c-fold'))return;
  drag={x:e.clientX,y:e.clientY,cx:camera.x,cy:camera.y};view.classList.add('is-panning');view.setPointerCapture(e.pointerId);});
view.addEventListener('pointermove',e=>{if(!drag)return;
  camera.x=drag.cx-(e.clientX-drag.x)/camera.s;camera.y=drag.cy-(e.clientY-drag.y)/camera.s;updateCamera();});
const endDrag=()=>{drag=null;view.classList.remove('is-panning');};
view.addEventListener('pointerup',endDrag);view.addEventListener('pointercancel',endDrag);
view.addEventListener('wheel',e=>{e.preventDefault();const box=view.getBoundingClientRect();
  if(e.ctrlKey||e.metaKey)zoom(Math.exp(-e.deltaY*.004),e.clientX-box.left,e.clientY-box.top);
  else{camera.x+=e.deltaX/camera.s;camera.y+=e.deltaY/camera.s;updateCamera();}
},{passive:false});
view.addEventListener('keydown',e=>{if(e.key==='+'||e.key==='=')zoom(1.18);
  else if(e.key==='-')zoom(1/1.18);
  else if(e.key==='ArrowRight')camera.x+=80/camera.s;
  else if(e.key==='ArrowLeft')camera.x-=80/camera.s;
  else if(e.key==='ArrowDown')camera.y+=80/camera.s;
  else if(e.key==='ArrowUp')camera.y-=80/camera.s;
  else return;
  e.preventDefault();updateCamera();});
if(typeof ResizeObserver!=='undefined')new ResizeObserver(updateCamera).observe(view);
load();
})(typeof globalThis!=='undefined'?globalThis:this);
