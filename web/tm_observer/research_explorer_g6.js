/* Read-only G6 visualization: never read/write Obsidian node geometry. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const reader=$('ex-reader'), alertBox=$('ex-error'), tooltip=$('ex-tooltip');
const workspace=$('ex-workspace'), layout=$('ex-layout'), splitter=$('ex-splitter');
const palette={
  Hypothesis:{fill:'#3175b8',line:'#93c5fd',short:'H'},
  // Read-only compatibility for historical Canvas; V2 has no standalone Outcome node.
  Decision:{fill:'#936023',line:'#fcd34d',short:'D'},
  Finding:{fill:'#178474',line:'#5eead4',short:'F'},
  StageSummary:{fill:'#7850b1',line:'#ddd6fe',short:'S'},
  External:{fill:'#596d87',line:'#e2e8f0',short:'E'},
  Unknown:{fill:'#2c3037',line:'#64748b',short:'?'}
};
const stable=(a,b)=>String(a).localeCompare(String(b),'en',{numeric:true});
// Presentation-only card geometry; never consult the source Canvas coordinates.
const CARD_W=352,CARD_H=154,SOURCE_STRIP_W=42,STRIP_PAD_Y=12,STRIP_ROW_H=24;
// Semantic zoom: show prose only once node size is readable on screen.
const DETAIL_MIN_ZOOM=0.60;
// The overview's coloring dimension is a viewer preference, not a scientific fact.
let overviewColorMode='frontier';
const frontierFill={active:'#186F98',parked:'#996723',closed:'#4B586A',unknown:'#384152'};
const outcomeFill={supported:'#207A62',rejected:'#A34550',open:'#665198',unadjudicated:'#495568',mixed:'#656C7A',legacy:'#656C7A',none:'#374151'};
const statusLabels={active:'Active',parked:'Parked',closed:'Closed',unknown:'未标记',
  supported:'Supported',rejected:'Rejected',open:'Open',unadjudicated:'未裁决（研究中止）',mixed:'Mixed',legacy:'历史归一',none:'尚未裁决'};
function hypothesisFrontier(sem={}){
  const branches=Array.isArray(sem.frontiers)?sem.frontiers:[];
  const active=branches.map(b=>b?.status).filter(s=>['active','parked','closed'].includes(s));
  const states=active.length?active:
    (['active','parked','closed'].includes(sem.frontier_status)?[sem.frontier_status]:[]);
  return ['active','parked','closed'].find(s=>states.includes(s))||'unknown';
}
function frontierSummary(sem={}){
  const branches=Array.isArray(sem.frontiers)?sem.frontiers:[];
  const counts={active:0,parked:0,closed:0};
  for(const b of branches)if(Object.hasOwn(counts,b?.status))counts[b.status]++;
  return branches.length>1?['active','parked','closed'].filter(s=>counts[s])
    .map(s=>counts[s]+' '+statusLabels[s]).join(' · '):statusLabels[hypothesisFrontier(sem)];
}
// Normalized legacy milestones are *not* original Judge Open verdicts.
function normalizedOutcomeKind(o){
  const originals=Array.isArray(o?.original_verdicts)?o.original_verdicts:[];
  return o?.verdict==='open'&&originals.some(v=>v&&v!=='open')?'legacy':o?.verdict;
}
// Never interpret the default compatibility outcomes.verdict='open' as a Judge decision.
function hypothesisVerdict(sem={}){
  const records=Array.isArray(sem.cycle_outcomes)?sem.cycle_outcomes.filter(
    o=>o&&['supported','rejected','open'].includes(o.verdict)&&hasRecordedOutcome(o)):[];
  if(records.length){
    const verdicts=new Set(records.map(normalizedOutcomeKind));
    return verdicts.size===1?[...verdicts][0]:'mixed';
  }
  const whole=sem.whole_h_assessment;
  if(whole&&['supported','rejected','open'].includes(whole.verdict)&&hasRecordedOutcome(whole))
    return normalizedOutcomeKind(whole);
  if(sem.outcome&&['supported','rejected','open'].includes(sem.outcome.verdict)&&
     hasRecordedOutcome(sem.outcome))return normalizedOutcomeKind(sem.outcome);
  // Legacy Canvas with explicit terminal state but no Cycle record.
  if(['supported','rejected'].includes(sem.scientific_status))return sem.scientific_status;
  if(Array.isArray(sem.cycle_outcomes)&&sem.cycle_outcomes.some(
       o=>o?.verdict==='unadjudicated'&&o?.review_state==='unadjudicated'))
    return 'unadjudicated';
  return 'none';
}
function nodeFill(n,compact=compactView()){
  if(knownType(n||{})!=='Hypothesis')return (palette[knownType(n||{})]||palette.Unknown).fill;
  const sem=n.semantic||{};
  return compact&&overviewColorMode==='outcome'?outcomeFill[hypothesisVerdict(sem)]:
    frontierFill[hypothesisFrontier(sem)];
}
function ensureOverviewColorToggle(){
  const graphHost=$('ex-graph');
  if(!graphHost||graphHost.querySelector?.('#ex-color-mode'))return;
  const button=document.createElement('button');
  button.id='ex-color-mode';button.type='button';button.className='ex-color-mode';
  const toolbar=$('ex-zoom-percent')?.parentElement;
  button.style.cssText=(toolbar?'':'position:absolute;top:55px;left:12px;z-index:8;')+
    'padding:6px 10px;border:1px solid #596579;border-radius:7px;'+
    'background:#1c2432;color:#eef2fa;font:600 12px system-ui;'+
    'cursor:pointer;box-shadow:0 3px 12px #0008';
  const refresh=()=>{
    button.textContent='远景颜色：'+(overviewColorMode==='frontier'?'前沿':'结论')+' ⇄';
    button.title='仅切换 ≤60% 数字视图的节点填充色；详情始终上 H 前沿、下 Outcome';
    button.setAttribute('aria-label',button.title);
    button.setAttribute('aria-pressed',String(overviewColorMode==='outcome'));
  };
  button.onclick=()=>{overviewColorMode=overviewColorMode==='frontier'?'outcome':'frontier';
    refresh();updateZoomLabels();renderColorLegend()};
  refresh();
  if(toolbar?.insertBefore)toolbar.insertBefore(button,$('ex-zoom-percent'));
  else graphHost.append(button);
}

// G6's context.viewport does not exist until graph.render() completes.
// A callable getZoom method alone does NOT mean the viewport is ready.
function safeZoom(){
  if(!graphReady||!graph)return 1;
  try{
    const zoom=graph.getZoom();
    return Number.isFinite(zoom)&&zoom>0?zoom:1;
  }catch{return 1}
}
const compactView=()=>graphReady&&safeZoom()<=DETAIL_MIN_ZOOM;
// External source Cycles are provenance labels, never H freeze Cycles.
// An explicit source wins; otherwise time and predecessor edges locate it.
function sourceCycleForExternal(n){
  if(knownType(n)!=='External')return null;
  const field=String(n.text||'').match(/^(?:source_cycle|cycle_id):\s*(?:cycle-)?(\d+)\s*$/m);
  if(field){
    const cycle=Number(field[1]);
    return Number.isSafeInteger(cycle)&&cycle>0?cycle:null;
  }
  const cycles=[...String(n.id).matchAll(/(?:^|[-_])c(?:ycle)?-?0*([1-9]\d*)(?=$|[-_])/gi)]
    .map(match=>Number(match[1]));
  const unique=[...new Set(cycles.filter(c=>Number.isSafeInteger(c)&&c>0))];
  return unique.length===1?unique[0]:null;
}
let externalTimelineCache=null;
function originalNodeTime(n){
  const header=String(n.text||'').match(/^timestamp:\s*([^\n\r]+)/m);
  return String(n.semantic?.sortable_time||header?.[1]||'').trim();
}
function timelineMinute(raw,defaultYear){
  const full=/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(raw);
  const short=/^(\d{2})-(\d{2})-(\d{2}):(\d{2})$/.exec(raw);
  if(!full&&!short)return null;
  const year=full?Number(full[1]):defaultYear;
  const month=Number(full?full[2]:short[1]),day=Number(full?full[3]:short[2]);
  const hour=Number(full?(full[4]??12):short[3]),minute=Number(full?(full[5]??0):short[4]);
  const date=new Date(Date.UTC(year,month-1,day,hour,minute));
  if(date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||
    date.getUTCDate()!==day||date.getUTCHours()!==hour||date.getUTCMinutes()!==minute)return null;
  return date.getTime()/60000;
}
function externalTimeline(){
  if(externalTimelineCache?.source===index&&
      externalTimelineCache?.count===index?.nodes?.length)return externalTimelineCache;
  const all=index?.nodes||[];
  const year=Number(all.map(originalNodeTime).map(t=>/^(\d{4})-\d{2}-\d{2}/.exec(t)?.[1])
    .find(Boolean)||new Date().getFullYear());
  const anchors=all.flatMap(n=>{
    const cycle=knownType(n)==='External'?sourceCycleForExternal(n):n.semantic?.cycle;
    const time=timelineMinute(originalNodeTime(n),year);
    return Number.isSafeInteger(cycle)&&cycle>0&&time!==null?[{time,cycle}]:[];
  }).sort((a,b)=>a.time-b.time||a.cycle-b.cycle);
  const lookup=new Map(all.map(n=>[n.id,n]));
  const inbound=new Map(all.map(n=>[n.id,[]]));
  for(const edge of index?.edges||[])if(inbound.has(edge.toNode)&&lookup.has(edge.fromNode))
    inbound.get(edge.toNode).push(edge.fromNode);
  externalTimelineCache={source:index,count:all.length,year,anchors,lookup,inbound};
  return externalTimelineCache;
}
function predecessorCycle(n,timeline){
  const seen=new Set([n.id]);let frontier=[n.id];
  while(frontier.length){
    const next=[],cycles=[];
    for(const id of frontier)for(const parentId of timeline.inbound.get(id)||[]){
      if(seen.has(parentId))continue;
      seen.add(parentId);
      const parent=timeline.lookup.get(parentId);
      const cycle=knownType(parent)==='External'?sourceCycleForExternal(parent):displayCycle(parent);
      if(Number.isSafeInteger(cycle)&&cycle>0)cycles.push(cycle);
      else next.push(parentId);
    }
    if(cycles.length)return Math.max(...cycles);
    frontier=next;
  }
  return null;
}
function externalCycleEstimate(n){
  if(knownType(n)!=='External')return null;
  const source=sourceCycleForExternal(n);
  if(source!==null){
    return {display:'E'+source,heading:'来源 C'+source,
      note:'记录明确标示来源 Cycle '+source+'；不代表 H 的冻结 Cycle'};
  }
  const timeline=externalTimeline();
  const stamp=originalNodeTime(n),time=timelineMinute(stamp,timeline.year);
  if(time!==null&&timeline.anchors.length){
    const nearest=timeline.anchors.reduce((best,point)=>
      Math.abs(point.time-time)<Math.abs(best.time-time)?point:best);
    return {display:'E'+nearest.cycle,heading:'靠近 C'+nearest.cycle,
      note:'原始时间 '+stamp+'；按最近的明确 Cycle 时间锚点估算，仅用于图谱定位'};
  }
  if(time===null){
    const preceding=predecessorCycle(n,timeline);
    if(preceding!==null)return {display:'E'+preceding,heading:'前序 C'+preceding,
      note:'无可解析时间；沿前序连线找到最近层级的 Cycle，多个前序取最大值，仅用于图谱定位'};
  }
  return {display:'E?',heading:'来源 Cycle 未知',
    note:time===null?'无可解析时间，前序节点也没有可信 Cycle':'没有可信 Cycle 时间锚点'};
}
let hypothesisCycleCache=null;
function displayCycle(n){
  const explicit=n?.semantic?.cycle;
  if(Number.isSafeInteger(explicit)&&explicit>0)return explicit;
  if(knownType(n||{})!=='Hypothesis'||!index)return null;
  if(hypothesisCycleCache?.source!==index||hypothesisCycleCache?.count!==index.nodes.length||
      hypothesisCycleCache?.edgeCount!==index.edges.length){
    const parents=new Map(index.nodes.map(node=>[node.id,[]]));
    for(const edge of index.edges)if(isScientificEdge(edge)&&parents.has(edge.toNode))
      parents.get(edge.toNode).push(edge.fromNode);
    hypothesisCycleCache={source:index,count:index.nodes.length,edgeCount:index.edges.length,
      lookup:new Map(index.nodes.map(node=>[node.id,node])),parents,cycles:new Map()};
  }
  const {lookup,parents,cycles}=hypothesisCycleCache;
  function infer(id,path){
    const node=lookup.get(id),own=node?.semantic?.cycle;
    if(Number.isSafeInteger(own)&&own>0)return own;
    if(knownType(node||{})!=='Hypothesis'||path.has(id))return null;
    if(cycles.has(id))return cycles.get(id);
    path.add(id);
    const previous=(parents.get(id)||[]).map(parentId=>infer(parentId,path))
      .filter(c=>Number.isSafeInteger(c)&&c>0);
    path.delete(id);
    const value=previous.length?Math.max(...previous)+1:null;
    cycles.set(id,value);
    return value;
  }
  return infer(n.id,new Set());
}
function compactCycleLabel(n){
  const type=knownType(n);
  if(type==='External')return externalCycleEstimate(n)?.display||'E?';
  const cycle=displayCycle(n);
  if(Number.isSafeInteger(cycle)&&cycle>=0)return String(cycle);
  return type==='Hypothesis'?'C?':(palette[type]||palette.Unknown).short;
}
// G6's huge inverse-zoom font metrics displaced overview labels ABOVE cards.
// Keep G6 labels small and hidden in compact mode; center short labels in a
// screen-space flex overlay whose rectangle exactly matches each G6 node.
const COMPACT_FONT_PIXEL_CAP=72;
function compactFontPixels(label,width,height){
  const glyphs=Math.max(1,Array.from(label).length);
  return Math.max(0,Math.min(COMPACT_FONT_PIXEL_CAP,
    height*.72,width*.86/(glyphs*.66)));
}
function graphLabelStyle(data,style,compact){
  const incoming=(style.ports||[]).some(p=>p.key.startsWith('in:'));
  if(!compact)return {
    labelText:data.detailLabel||'',labelFontSize:12.5,labelFontWeight:600,
    labelOffsetX:incoming?SOURCE_STRIP_W/2:0,
    labelMaxWidth:incoming?CARD_W-SOURCE_STRIP_W-20:CARD_W-30,
    labelMaxLines:data.hasOutcome?7:6,labelWordWrap:true
  };
  return {
    labelText:'',labelFontSize:12.5,labelFontWeight:800,labelOffsetX:0,
    labelMaxWidth:CARD_W-26,labelMaxLines:1,labelWordWrap:false
  };
}

const ID=s=>String(s??'');
let index=null, selected=null, graph=null, graphReady=false, taskId=$('ex-task').value;
let mode='all', collapsed=new Set(), searchTimer=null, renderSerial=0;
let searchRequestSerial=0;
const base=()=>'/api/canvas-md/tasks/'+encodeURIComponent(taskId);
const syncLayoutDemoLink=()=>{
  const link=$('ex-layout-demo-link');if(link)link.href='/research-explorer-tmp/?observer_id='+encodeURIComponent(taskId);
  const c=$('ex-layout-c-link');if(c)c.href='/research-explorer-tmp-c/?observer_id='+encodeURIComponent(taskId);
};
syncLayoutDemoLink();
const uri=(kind,id)=>kind+':'+id;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
const api=async path=>{const r=await fetch(path,{cache:'no-store',credentials:'same-origin'}),v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v};
function showError(message){alertBox.textContent='图谱无法渲染：'+message;alertBox.hidden=false}
function knownType(n){return n.semantic?.type||'Unknown'}
function rank(n){const m=n.semantic||{},cycle=displayCycle(n);return [cycle??999999,m.sortable_time||'',ID(n.id)]}
function compareNodes(a,b){const x=rank(a),y=rank(b);return x[0]-y[0]||stable(x[1],y[1])||stable(x[2],y[2])}
function isScientificEdge(e){
  // Preserve all real edges, but reference/provenance edges do not advance a scientific branch.
  return !['external_reference','research_source'].includes(e.edge_type)&&
    !['external_reference','research_source'].includes(e.type);
}
function structure(nodes,edges){
  const lookup=new Map(nodes.map(n=>[n.id,n]));
  const allEdges=edges.filter(e=>lookup.has(e.fromNode)&&lookup.has(e.toNode));
  const lineage=allEdges.filter(isScientificEdge);
  const children=new Map(nodes.map(n=>[n.id,[]])),parents=new Map(nodes.map(n=>[n.id,[]]));
  for(const e of lineage){children.get(e.fromNode).push(e.toNode);parents.get(e.toNode).push(e.fromNode)}
  for(const v of children.values())v.sort(stable);
  for(const v of parents.values())v.sort(stable);
  return {lookup,allEdges,lineage,children,parents};
}
function traverse(start,adj){
  const found=new Set(start),todo=[...start];
  for(let i=0;i<todo.length;i++)for(const id of adj.get(todo[i])||[])if(!found.has(id)){found.add(id);todo.push(id)}
  return found;
}
function baseScope(s){
  if(mode==='all')return new Set(s.lookup.keys());
  if(mode==='local'&&selected?.kind==='node'){
    const ids=new Set([selected.id]);let frontier=[selected.id];
    for(let step=0;step<2;step++){
      const next=[];
      for(const id of frontier)for(const n of [...(s.parents.get(id)||[]),...(s.children.get(id)||[])])
        if(!ids.has(n)){ids.add(n);next.push(n)}
      frontier=next;
    }
    return ids;
  }
  const tips=[...s.lookup.values()].filter(n=>n.semantic?.frontier_status==='active').map(n=>n.id);
  if(mode==='frontier'&&tips.length)return traverse(tips,s.parents);
  return new Set(s.lookup.keys());
}
function visibility(s,ignoreCollapsed=false){
  const scope=baseScope(s), indegree=new Map([...scope].map(id=>[id,0]));
  for(const e of s.lineage)if(scope.has(e.fromNode)&&scope.has(e.toNode))
    indegree.set(e.toNode,indegree.get(e.toNode)+1);
  let roots=[...scope].filter(id=>indegree.get(id)===0).sort(stable);
  // A cyclic legacy component has no root. Keep it visible rather than dropping research.
  if(!roots.length)roots=[...scope].sort(stable);
  const visible=new Set(roots),todo=[...roots];
  for(let i=0;i<todo.length;i++){
    const id=todo[i];
    if(!ignoreCollapsed&&collapsed.has(id))continue;
    for(const nxt of s.children.get(id)||[]){
      if(scope.has(nxt)&&!visible.has(nxt)){visible.add(nxt);todo.push(nxt)}
    }
  }
  // Independently preserve rootless cyclic components even in a mixed DAG.
  for(const id of scope)if(!visible.has(id)&&indegree.get(id)>0){
    const backwards=traverse([id],s.parents);
    if(![...backwards].some(x=>visible.has(x)||indegree.get(x)===0)){
      const group=traverse([id],s.children);
      for(const x of group)if(scope.has(x))visible.add(x);
    }
  }
  const hidden=new Map();
  for(const id of collapsed)if(!ignoreCollapsed&&scope.has(id)){
    const children=traverse([id],s.children);
    hidden.set(id,[...children].filter(n=>n!==id&&scope.has(n)&&!visible.has(n)).length);
  }
  return {scope,visible,hidden};
}
function abbreviate(s,maxUnits=32){
  const chars=Array.from(ID(s).replace(/\s+/g,' ').trim());let used=0,out='';
  for(const ch of chars){
    const units=/[\u2e80-\u9fff\uf900-\ufaff]/u.test(ch)?2:1;
    if(used+units>maxUnits-1)return out+'…';
    out+=ch;used+=units;
  }
  return out;
}
function sourceTypeColor(edge){
  const node=index?.nodes.find(n=>n.id===edge.fromNode);
  return (palette[knownType(node||{})]||palette.Unknown).line;
}
function inputColorMap(target){
  const input=(index?.edges||[]).filter(e=>e.toNode===target)
    .sort((a,b)=>stable(a.fromNode,b.fromNode)||stable(a.id,b.id));
  return new Map(input.map(e=>[ID(e.id),sourceTypeColor(e)]));
}
function cardHeight(count){return Math.max(CARD_H,STRIP_PAD_Y*2+count*STRIP_ROW_H)}
function portFraction(i,count,height){
  return (STRIP_PAD_Y+(i+.5)*(height-2*STRIP_PAD_Y)/count)/height;
}


const typeNames={Hypothesis:'假设',Decision:'历史 Outcome',Finding:'历史 Finding',StageSummary:'历史总结',External:'外部资料',Unknown:'未分类'};
const frontierNames={active:'活跃',parked:'搁置',closed:'已终止',unknown:'未标记'};
const frontierColors={active:'#34d399',parked:'#fbbf24',closed:'#94a3b8',unknown:'#64748b'};
function markedTips(){
  if(!index)return [];
  if(index.canvas==='science_graph.sqlite3'){
    // Exactly one current status per H. Old Decision-level observations live
    // in the audit archive, never appear as extra active research branches.
    return index.nodes.filter(n=>knownType(n)==='Hypothesis').map(n=>({
      node:n,status:hypothesisFrontier(n.semantic||{}),terminal:true
    }));
  }
  const scientificOut=new Set(index.edges.filter(isScientificEdge).map(e=>e.fromNode));
  return index.nodes.filter(n=>['active','parked','closed'].includes(n.semantic?.frontier_status))
    .map(n=>({node:n,status:n.semantic.frontier_status,terminal:!scientificOut.has(n.id)}));
}
function overviewRender(){
  if(!index)return;
  const records=markedTips(),counts={active:0,parked:0,closed:0,unknown:0};
  for(const record of records)counts[record.status]++;
  const summary=$('ex-tip-summary');
  summary.replaceChildren();
  const caption=document.createElement('span');caption.className='ex-overview-title';
  caption.textContent=index.canvas==='science_graph.sqlite3'?'猜想前沿（按 H 去重）':'研究末端';summary.append(caption);
  const statusTabs=[[null,'全部'],['active','活跃'],['parked','搁置'],['closed','已终止']];
  if(index.canvas==='science_graph.sqlite3')statusTabs.push(['unknown','未标记']);
  for(const [status,title] of statusTabs){
    const button=document.createElement('button');
    button.className='ex-tip-trigger';button.type='button';
    if(status)button.dataset.frontier=status;
    const count=status?counts[status]:records.length;
    button.textContent=title+' '+count;
    button.title=status?'查看'+title+'的全部末端节点':'打开全部研究末端，含当前视图未显示的分支';
    button.onclick=()=>openTipPanel(status);
    summary.append(button);
  }
  const mismatch=index.canvas==='science_graph.sqlite3'?0:records.filter(r=>!r.terminal).length;
  if(mismatch){
    const warning=document.createElement('span');warning.className='ex-overview-title';
    warning.title='部分历史节点仍有后续科研连线；仅提示，不修改 Canvas 的标记';
    warning.textContent='⚠ '+mismatch+' 个标记需核验';summary.append(warning);
  }
  renderColorLegend();
}
function renderColorLegend(){
  if(!index)return;
  const legend=$('ex-type-legend');legend.replaceChildren();
  const hNodes=index.nodes.filter(n=>knownType(n)==='Hypothesis');
  const compact=compactView();
  const f=['active','parked','closed','unknown'];
  const o=['supported','rejected','open','mixed','legacy','none'];
  const dimensions=compact?
    (overviewColorMode==='frontier'?[['前沿颜色',f,frontierFill,hypothesisFrontier]]:
      [['结论颜色',o,outcomeFill,hypothesisVerdict]]):
    [['H · 前沿',f,frontierFill,hypothesisFrontier],
      ['Outcome · 结论',o,outcomeFill,hypothesisVerdict]];
  const key=(label,color,count)=>{
    const item=document.createElement('span');item.className='ex-type-key';
    item.title=label+' · '+count+' 个节点';
    const dot=document.createElement('span');dot.className='ex-type-dot';
    dot.style.background=color;dot.style.borderColor='#cbd5e1';
    const text=document.createElement('span');text.textContent=label+' '+count;
    item.append(dot);item.append(text);legend.append(item);
  };
  for(const [title,states,colors,project] of dimensions){
    const heading=document.createElement('span');heading.className='ex-overview-title';
    heading.textContent=title;legend.append(heading);
    for(const state of states){
      const count=hNodes.filter(n=>project(n.semantic||{})===state).length;
      if(count)key(statusLabels[state],colors[state],count);
    }
  }
  for(const type of ['Decision','Finding','StageSummary','External','Unknown']){
    const count=index.nodes.filter(n=>knownType(n)===type).length;
    if(count)key(typeNames[type]||type,(palette[type]||palette.Unknown).fill,count);
  }
}
let tipWorkerRequest=0;
async function openTipPanel(filter=null){
  const panel=$('ex-tip-panel');
  if(!index)return;
  // Repeating the same filter closes the overlay, just like a document outline.
  if(!panel.hidden&&panel.dataset.filter===(filter||'all')){
    ++tipWorkerRequest;panel.hidden=true;return;
  }
  const request=++tipWorkerRequest,currentTask=taskId;
  panel.replaceChildren();panel.dataset.filter=filter||'all';
  const records=markedTips().filter(r=>!filter||r.status===filter)
    .sort((a,b)=>{
      const order={active:0,parked:1,closed:2,unknown:3};
      return order[a.status]-order[b.status]||compareNodes(a.node,b.node);
    });
  const head=document.createElement('div');head.className='ex-tip-panel-head';
  const title=document.createElement('strong');
  title.textContent=(index.canvas==='science_graph.sqlite3'?'研究分支 · ':'科研末端 · ')+(filter?frontierNames[filter]:'全部')+' '+records.length;
  head.append(title);
  const close=document.createElement('button');close.textContent='关闭 ×';
  close.onclick=()=>{++tipWorkerRequest;panel.hidden=true};head.append(close);panel.append(head);
  const note=document.createElement('p');note.className='ex-tip-panel-note';
  note.textContent=index.canvas==='science_graph.sqlite3'?'每个 H 只有一个当前前沿状态；缺失状态显示未标记。历史 Decision 前沿仅保留于归档。':'列表使用 Canvas 原始 frontier_status；点击条目定位，若不在当前研究前沿中则切换至完整图谱。';
  panel.append(note);
  const activeEntries=new Map();
  for(const {node,status,terminal} of records){
    const entry=document.createElement('div');entry.className='ex-tip-entry';
    entry.style.borderLeftColor=frontierColors[status];
    const button=document.createElement('button');button.className='ex-tip-item';
    button.type='button';
    const label=document.createElement('span');
    label.textContent=(terminal?'':'⚠ ') + abbreviate(node.semantic?.title||node.label||node.id,54);
    button.append(label);
    const sub=document.createElement('span');sub.className='ex-tip-sub';
    sub.textContent=cardCycleText(node,[],new Map())+' · '+(typeNames[knownType(node)]||knownType(node))+
      ' · '+frontierNames[status]+(terminal?'':' · 存在后续科研连线');
    button.append(sub);
    button.onclick=()=>{++tipWorkerRequest;panel.hidden=true;navigate('node',node.id,'',{focus:true})};
    entry.append(button);
    if(status==='active')activeEntries.set(node.id,entry);
    panel.append(entry);
  }
  panel.hidden=false;
  if(!activeEntries.size)return;
  try{
    // Query verified live process/Cycle ownership at each opening. The graph's
    // scientific index is not a live Worker monitor; never use cached RUNNING.
    const response=await api(base()+'/workers');
    if(request!==tipWorkerRequest||currentTask!==taskId||panel.hidden)return;
    for(const worker of response.workers||[]){
      const entry=activeEntries.get(worker.node_id);
      if(!entry||!/^worker-[1-9]\d*$/.test(worker.worker_id)||
         !/^[1-9]\d*$/.test(worker.cycle_id))continue;
      let badges=Array.from(entry.children).find(item=>item.className==='ex-tip-workers');
      if(!badges){badges=document.createElement('div');badges.className='ex-tip-workers';entry.append(badges)}
      const link=document.createElement('a');link.className='ex-tip-worker';
      link.href='/run/'+encodeURIComponent(taskId)+'/?worker='+encodeURIComponent(worker.worker_id);
      link.textContent='● Worker '+worker.worker_id.slice(7)+' · C'+worker.cycle_id+' ↗';
      link.title=(worker.association==='frozen'?'当前 Cycle 冻结 H':'尚未冻结新 H，认领研究前沿')+
        ' · 点击打开 '+worker.worker_id+' 的 Task Console';
      link.setAttribute('aria-label',link.title);
      badges.append(link);
    }
  }catch(error){
    if(request!==tipWorkerRequest||currentTask!==taskId||panel.hidden)return;
    note.textContent+=' · Worker 在线状态暂不可读取';
  }
}

function shortCardTitle(s){
  // Show much more of the existing Canvas heading; never summarize with an LLM.
  return abbreviate(s,84);
}
function cardScientificTitle(node){
  const title=String(node.semantic?.title||node.label||'').trim();
  // Legacy nodes without a scientific title often use the technical ID as a
  // fallback label. That identifier belongs in the detail metadata, not here.
  return !title||title===node.id||title==='# '+node.id?'未命名科研节点':title;
}
// Only adjudicated Cycles have scientific Outcomes.
function hasRecordedOutcome(outcome){
  if(!outcome||typeof outcome!=='object'||Array.isArray(outcome))return false;
  if(['supported','rejected'].includes(outcome.verdict))return true;
  if(String(outcome.cycle_id??'').trim())return true;
  if(['summary','decisive_test','judge_rationale','report_ref']
    .some(key=>String(outcome[key]??'').trim()))return true;
  return ['findings','open_questions','boundary','evidence_refs']
    .some(key=>Array.isArray(outcome[key])&&outcome[key].length>0);
}
function cardCycleText(n,edges,lookup){
  // An External timeline label is read-only provenance, not a scientific Cycle.
  if(knownType(n)==='External')return externalCycleEstimate(n)?.heading||'C?';
  // The creation Cycle wins over any later Outcome Cycle.
  const own=displayCycle(n);
  if(Number.isSafeInteger(own)&&own>0)return 'C'+own+
    (n.semantic?.cycle==null&&knownType(n)==='Hypothesis'?'（前序+1）':'');
  // Unknown first freeze remains unknown, even with a later recorded Outcome.
  if(knownType(n)==='Hypothesis')return 'C?';
  const previous=new Set();
  for(const e of edges){
    if(e.toNode!==n.id||!isScientificEdge(e))continue;
    const parent=lookup.get(e.fromNode);
    if(knownType(parent||{})!=='Decision')continue;
    const c=parent.semantic?.cycle;
    if(Number.isSafeInteger(c)&&c>=0)previous.add(c);
  }
  if(!previous.size)return 'C?';
  const ordered=[...previous].sort((a,b)=>a-b);
  const cycles=ordered.slice(0,3).map(c=>'C'+c).join('/');
  return cycles+(ordered.length>3?'…':'')+'（前序）';
}
function cardStatusLine(sem){
  if(hasRecordedOutcome(sem.outcome)){
    const result=outcomeVerdictLabel(sem.outcome);
    return 'Outcome '+result+(sem.frontier_status?' · 前沿 '+sem.frontier_status:'');
  }
  const scientific=sem.verdict||sem.scientific_status||sem.legacy_status;
  return [scientific&&scientific!=='none'?'科学 '+scientific:'',sem.frontier_status?'前沿 '+sem.frontier_status:''].filter(Boolean).join(' · ');
}
function outcomeVerdictLabel(outcome){
  if(outcome?.verdict==='unadjudicated')
    return '未裁决（'+(outcome.termination_reason==='investigator_budget_review_rejected'
      ?'预算拒绝续批':'研究中止')+'）';
  const originals=Array.isArray(outcome?.original_verdicts)?outcome.original_verdicts:[];
  return originals.length?originals.join('/')+'（兼容 '+(outcome.verdict||'open')+'）':
    (outcome?.verdict||'open');
}
function compactTime(value){
  const raw=String(value||'').trim();
  return raw.replace(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$/,
    (_all,day,clock,zone='')=>day+' '+clock+(zone||''));
}
function hypothesisTime(sem={}){
  const source=sem.source_timestamp||sem.timestamp;
  if(source)return 'H 时间 · '+compactTime(source);
  if(sem.registered_at)return '登记时间 · '+compactTime(sem.registered_at);
  return 'H 时间未记录';
}
function outcomeTime(outcome){
  if(!outcome)return '暂无裁决时间';
  if(outcome.source_timestamps?.length)return '多源裁决时间见详情';
  if(outcome.source_timestamp)return '裁决时间 · '+compactTime(outcome.source_timestamp);
  if(outcome.recorded_at)return '记录时间 · '+compactTime(outcome.recorded_at);
  return '裁决时间未记录';
}
function outcomeDetailsHtml(outcomes){
  const records=Array.isArray(outcomes)?outcomes.filter(hasRecordedOutcome):[];
  if(!records.length)return '';
  const facts=(heading,items)=>Array.isArray(items)&&items.length?
    '<h4>'+esc(heading)+'</h4><ul>'+items.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ul>':'';
  const detail=outcome=>'<details'+(outcome===records[records.length-1]?' open':'')+
    '><summary>Cycle '+esc(outcome.cycle_id)+' · '+esc(outcomeVerdictLabel(outcome))+'</summary>'+
    '<p class="muted">'+esc(outcomeTime(outcome))+'</p>'+
    (outcome.source_timestamps?.length?
      '<p class="muted">原始来源时间：'+outcome.source_timestamps.map(esc).join(' · ')+'</p>':'')+
    (outcome.summary?'<div class="ex-md">'+mdHtml(outcome.summary,null)+'</div>':'')+
    (outcome.decisive_test?'<p><strong>冻结检验：</strong>'+esc(outcome.decisive_test)+'</p>':'')+
    (outcome.review_state==='unadjudicated'?
      '<p class="muted">此记录属于本猜想的阶段性科研保全，不是 Judge 科学裁决；研究执行未完成。</p>':'')+
    (outcome.prior_judge_objection?'<p><strong>此前 Judge 返工要求（未完成）：</strong>'+
      esc(outcome.prior_judge_objection)+'</p>':'')+
    (outcome.judge_rationale?'<p><strong>裁决理由：</strong>'+esc(outcome.judge_rationale)+'</p>':'')+
    facts('确立的科学认识',outcome.findings)+
    facts('尚未解决的问题',outcome.open_questions)+
    facts('适用范围与限制',outcome.boundary)+
    (Array.isArray(outcome.evidence_refs)&&outcome.evidence_refs.length?
      '<p class="muted">Evidence ID（仅编号，具体证据请查阅科研证据登记记录）：'+
      outcome.evidence_refs.map(esc).join(' · ')+'</p>':'')+
    (outcome.document_ref?'<p><button class="ex-ref" data-ref="'+esc(uri('doc',outcome.document_ref))+
      '">阅读本 Cycle Outcome 文档 →</button></p>':
      outcome.unavailable_document_ref?'<p class="muted">Cycle Outcome 文档尚不可读取：'+
        esc(outcome.unavailable_document_ref)+'</p>':'')+
    (outcome.report_ref?'<p><button class="ex-ref" data-ref="'+esc(uri('doc',outcome.report_ref))+
      '">阅读原始科研报告 →</button></p>':
      outcome.unavailable_report_ref?'<p class="muted">原始报告引用不可读取：'+
        esc(outcome.unavailable_report_ref)+'</p>':'')+'</details>';
  return '<section class="ex-section ex-outcome-details" id="ex-outcome-details"><h3>Cycle Outcomes · '+
    esc(records.length)+'</h3><p class="muted">包含 Judge 科学裁决与未裁决的阶段性保全；两者不得混同。</p>'+
    records.map(detail).join('')+'</section>';
}
// Highlight exactly two outgoing scientific hops within the visible graph.
// Provenance/reference links never invent successors or extend the hop count.
function downstreamHighlight(start,visibleEdges,maxHops=2){
  const ids=new Set(),lineageIds=new Set();
  if(!start)return {ids,lineageIds};
  const outgoing=new Map();
  for(const e of visibleEdges){
    if(!isScientificEdge(e))continue;
    if(!outgoing.has(e.fromNode))outgoing.set(e.fromNode,[]);
    outgoing.get(e.fromNode).push(e);
  }
  let frontier=[start];ids.add(start);
  for(let depth=0;depth<maxHops&&frontier.length;depth++){
    const next=[];
    for(const source of frontier)for(const e of outgoing.get(source)||[]){
      lineageIds.add(ID(e.id||e.fromNode+'->'+e.toNode));
      if(!ids.has(e.toNode)){ids.add(e.toNode);next.push(e.toNode)}
    }
    frontier=next;
  }
  return {ids,lineageIds};
}
// Horizontal = recorded Cycle or a display-only predecessor + 1 placement;
// vertical = inherited branches. Remaining unknown H nodes use a separate area.
// Coordinates are deterministically computed from the entire active view scope,
// independent of selection and branch folding, and never read from Canvas x/y.
const TIMELINE_STEP=492, TIMELINE_ROW=232;
function timelineCoordinates(nodes,edges){
  const lookup=new Map(nodes.map(n=>[n.id,n]));
  const historical=nodes.filter(n=>knownType({semantic:n.data.semantic})!=='External');
  const scientific=n=>n.data?.semantic?.type!=='External';
  const freeze=n=>{
    const c=n.data?.semantic?.cycle;
    return scientific(n)&&Number.isSafeInteger(c)&&c>=0?c:null;
  };
  const placement=n=>freeze(n)??n.data?.placementCycle??null;
  const dates=[...new Set(nodes.map(placement).filter(c=>c!==null))].sort((a,b)=>a-b);
  const ranks=new Map(dates.map((c,i)=>[c,i]));
  const inbound=new Map(nodes.map(n=>[n.id,[]]));
  const outbound=new Map(nodes.map(n=>[n.id,[]]));
  for(const e of edges){
    if(!lookup.has(e.source)||!lookup.has(e.target))continue;
    inbound.get(e.target).push(e.source);outbound.get(e.source).push(e.target);
  }
  for(const map of [inbound,outbound])for(const list of map.values())list.sort(stable);
  const xById=new Map(),yById=new Map();
  const undated=historical.filter(n=>placement(n)===null);
  // Unknown origins get a separate area after a gap. They do not occupy or
  // precede any numbered Cycle column, and their H IDs never become Cycles.
  const undatedSet=new Set(undated.map(n=>n.id)),levels=new Map();
  function legacyDepth(id,path=new Set()){
    if(levels.has(id))return levels.get(id);
    if(path.has(id))return 0;
    path.add(id);
    const parents=(inbound.get(id)||[]).filter(p=>undatedSet.has(p));
    const d=parents.length?Math.min(8,1+Math.max(...parents.map(p=>legacyDepth(p,path)))):0;
    path.delete(id);levels.set(id,d);return d;
  }
  for(const n of undated)legacyDepth(n.id);
  for(const n of nodes){
    const c=placement(n);
    if(c!==null)xById.set(n.id,ranks.get(c)*TIMELINE_STEP);
  }
  for(const n of undated){
    // A parent Cycle is not this Hypothesis's Cycle. Preserve only relative
    // depth within the undated area, separated from the numbered chronology.
    xById.set(n.id,(dates.length+2+legacyDepth(n.id))*TIMELINE_STEP);
  }
  // Only External nodes without an explicit or estimated Cycle use an
  // adjacent scientific node as their placement fallback.
  for(const n of nodes)if(!scientific(n)&&!xById.has(n.id)){
    const neighbors=[...(inbound.get(n.id)||[]),...(outbound.get(n.id)||[])];
    const anchors=neighbors.filter(id=>xById.has(id));
    if(anchors.length)xById.set(n.id,Math.max(...anchors.map(id=>xById.get(id)))+TIMELINE_STEP*.22);
    else xById.set(n.id,-TIMELINE_STEP*10);
  }
  const positioned=[...nodes].sort((a,b)=>xById.get(a.id)-xById.get(b.id)||stable(a.id,b.id));
  let nextRoot=0;
  const occupied=[];
  function available(x,y){
    return occupied.every(p=>Math.abs(x-p.x)>=CARD_W+24||
      Math.abs(y-p.y)>=(p.height+CARD_H)/2+36);
  }
  for(const n of positioned){
    const x=xById.get(n.id);
    const parents=(inbound.get(n.id)||[]).filter(p=>yById.has(p));
    const neighbours=parents.length?parents:(scientific(n)?[]:
      [...(outbound.get(n.id)||[])].filter(p=>yById.has(p)));
    const desired=neighbours.length?
      neighbours.reduce((sum,id)=>sum+yById.get(id),0)/neighbours.length:
      nextRoot++*TIMELINE_ROW;
    let y=desired;
    for(let offset=0;offset<occupied.length+2;offset++){
      const probe=offset===0?desired:desired+(offset%2?1:-1)*Math.ceil(offset/2)*TIMELINE_ROW;
      if(available(x,probe)){y=probe;break}
    }
    yById.set(n.id,y);
    occupied.push({x,y,height:n.style?.size?.[1]||CARD_H});
  }
  for(const n of nodes){n.style.x=xById.get(n.id);n.style.y=yById.get(n.id)}
  return {xById,yById};
}

function g6Data(layoutBase=false){
  const s=structure(index.nodes,index.edges),vis=visibility(s,layoutBase);
  // Retain the whole base-mode layout. Only element visibility changes on fold.
  const rawEdges=s.allEdges.filter(e=>vis.visible.has(e.fromNode)&&vis.visible.has(e.toNode)&&
    (layoutBase||!(collapsed.has(e.fromNode)&&isScientificEdge(e))));
  const folded=visibility(s);
  // Stable ports are derived exclusively from the real visible edges, never from
  // Canvas x/y or a guessed parent reference. Distinct targets prevent coincident arrows.
  const edgeKey=e=>ID(e.id||e.fromNode+'->'+e.toNode);
  const incoming=new Map(),outgoing=new Map();
  for(const e of rawEdges){
    if(!incoming.has(e.toNode))incoming.set(e.toNode,[]);
    if(!outgoing.has(e.fromNode))outgoing.set(e.fromNode,[]);
    incoming.get(e.toNode).push(e);outgoing.get(e.fromNode).push(e);
  }
  for(const group of incoming.values())group.sort((a,b)=>stable(a.fromNode,b.fromNode)||stable(edgeKey(a),edgeKey(b)));
  for(const group of outgoing.values())group.sort((a,b)=>stable(a.toNode,b.toNode)||stable(edgeKey(a),edgeKey(b)));
  const portFor=(group,side,height)=>{
    const list=group||[];
    return list.map((e,i)=>({
      key:(side==='in'?'in:':'out:')+edgeKey(e),
      // The target port is EXACTLY centered on the corresponding in-card index row.
      placement:[side==='in'?0:1,portFraction(i,list.length,height)],
      r:side==='in'?3:undefined,
      fill:side==='in'?sourceTypeColor(e):undefined,
      stroke:'#111827',lineWidth:1,linkToCenter:true
    }));
  };
  const target=selected?.kind==='node'?selected.id:null;
  const activeEdgeId=selected?.kind==='edge'?selected.id:null;
  const selectedEdge=activeEdgeId?rawEdges.find(e=>edgeKey(e)===activeEdgeId):null;
  const descendants=downstreamHighlight(target,rawEdges);
  const sourceIds=new Set(target?(incoming.get(target)||[]).map(e=>e.fromNode):[]);
  const traceColor=inputColorMap(target);
  const nodes=[...vis.visible].map(id=>s.lookup.get(id)).filter(Boolean).sort(compareNodes).map(n=>{
    const semantic=n.semantic||{},type=knownType(n),style=palette[type]||palette.Unknown;
    const hasOutcome=type==='Hypothesis'&&hasRecordedOutcome(semantic.outcome);
    const stateLine=type==='Hypothesis'&&!hasOutcome?
      (semantic.frontier_status?'前沿 '+semantic.frontier_status:''):cardStatusLine(semantic);
    const cycleLabel=cardCycleText(n,rawEdges,s.lookup);
    const fold=collapsed.has(n.id)?' ▸ '+(folded.hidden.get(n.id)||0):'';
    const isTarget=n.id===target,isSource=sourceIds.has(n.id);
    const isDownstream=!!target&&descendants.ids.has(n.id);
    const isEdgeEndpoint=!!selectedEdge&&(n.id===selectedEdge.fromNode||n.id===selectedEdge.toNode);
    const faint=!!(target||selectedEdge)&&!isTarget&&!isSource&&!isDownstream&&!isEdgeEndpoint;
    const inCount=(incoming.get(n.id)||[]).length;
    const height=Math.max(cardHeight(inCount),hasOutcome?198:CARD_H);
    // Technical IDs stay in graph data and deep links, not card labels.
    const cardLabel=type==='Hypothesis'?'':
      cycleLabel+' · '+style.short+fold+'\n'+
      shortCardTitle(cardScientificTitle(n))+
      (stateLine?'\n'+stateLine:'');
    const externalLabel=type==='External'?externalCycleEstimate(n)?.display:null;
    const externalCycle=/^E([1-9]\d*)$/.exec(externalLabel||'');
    const nodeData={semantic,nodeId:n.id,detailLabel:cardLabel,
      placementCycle:type==='Hypothesis'?displayCycle(n):externalCycle?Number(externalCycle[1]):null,
      compactLabel:compactCycleLabel(n),hasOutcome};
    const nodeStyle={size:[CARD_W,height],ports:[...portFor(incoming.get(n.id),'in',height),
      ...portFor(outgoing.get(n.id),'out',height)]};
    return {id:n.id,type:'rect',data:nodeData,style:{
      size:nodeStyle.size,radius:10,zIndex:100,fill:nodeFill(n),fillOpacity:1,
      stroke:isTarget?'#ffffff':isEdgeEndpoint?'#fde68a':isDownstream?'#a5f3fc':
        isSource?'#67e8f9':semantic.frontier_status==='active'?'#bfdbfe':style.line,
      lineWidth:isTarget?4:isEdgeEndpoint?3.8:isDownstream?3.6:isSource?3.1:
        semantic.frontier_status==='active'?2.8:1.8,
      ports:nodeStyle.ports,portR:0,portLineWidth:0,
      labelFill:faint?'#c8d4e4':'#ffffff',
      labelPlacement:'center',labelTextAlign:'center',labelTextBaseline:'middle',
      ...graphLabelStyle(nodeData,nodeStyle,compactView(),safeZoom()),
      labelTextOverflow:'ellipsis',cursor:'pointer'
    }};
  });
  const edges=rawEdges.sort((a,b)=>stable(edgeKey(a),edgeKey(b))).map(e=>{
    const key=edgeKey(e),isIncoming=!!target&&e.toNode===target;
    const isDownstream=!!target&&descendants.lineageIds.has(key);
    const active=activeEdgeId===key;
    const highlighted=active||isIncoming||isDownstream;
    const dim=!!(target||selectedEdge)&&!highlighted;
    const type=isScientificEdge(e);
    const bright=active?'#fef08a':isIncoming?(traceColor.get(key)||'#67e8f9'):
      isDownstream?'#67e8f9':type?'#e2e8f0':'#cbd5e1';
    return {
      id:key,source:e.fromNode,target:e.toNode,
      data:{sourceEdge:e},
      style:{sourcePort:'out:'+key,targetPort:'in:'+key,
        // All relations are solid. Directional selection brightens successors,
        // while an explicitly clicked edge gets a separate gold accent.
        stroke:bright,
        lineWidth:active?5:isDownstream?4.4:isIncoming?4.1:dim?2.4:type?3.3:3,
        lineDash:[],endArrow:true,
        opacity:highlighted?1:dim?.28:1,
        zIndex:active?20:isDownstream?12:isIncoming?11:1,
        router:{type:'orth',padding:18},
        shadowBlur:highlighted?active?16:10:0,
        shadowColor:highlighted?bright:'transparent',
        cursor:'pointer',increasedLineWidthForHitTesting:12}
    };
  });
  timelineCoordinates(nodes,edges);
  return {nodes,edges};
}

// PDF-reader style "previous view" history: store only completed navigation actions.
// Free panning, pinching, hovering, scrolling are captured with the current view but
// NEVER create new history entries. State is private to this browser tab/task.
const trail=[];let trailIndex=-1,historySerial=-1,restoring=false,navSerial=0;
function captureView(){
  let camera=null;
  if(graphReady&&graph){
    try{
      const zoom=safeZoom(),position=graph.getPosition();
      if(Array.isArray(position)&&position.every(Number.isFinite))
        camera={zoom,position:[...position]};
    }catch{/* A disposed/reinitializing viewport has no camera snapshot. */}
  }
  return {taskId,selected:selected?{...selected}:null,mode,collapsed:[...collapsed].sort(stable),
    camera,readerScroll:reader.scrollTop||0};
}
// Canonical reader location: browser Back/Forward and view history use one state.
function syncBrowserLocation(push=false){
  const hash=selected?'#'+encodeURIComponent(uri(selected.kind,selected.id)+
    (selected.anchor?'#'+selected.anchor:'')):'';
  const url=location.pathname+(location.search||'')+hash;
  const state={tmExplorerView:captureView(),tmExplorerIndex:trailIndex};
  if(push&&typeof history.pushState==='function')history.pushState(state,'',url);
  else if(typeof history.replaceState==='function')history.replaceState(state,'',url);
}

function samePlace(a,b){
  return a.taskId===b.taskId&&a.mode===b.mode&&
    (a.selected?.kind||'')===(b.selected?.kind||'')&&
    (a.selected?.id||'')===(b.selected?.id||'')&&
    (a.selected?.anchor||'')===(b.selected?.anchor||'')&&
    a.collapsed.join('|')===b.collapsed.join('|');
}
function trailPosition(seq=trailIndex){return trail.findIndex(entry=>entry.historyIndex===seq)}
function trimTrail(){
  while(trail.length>70){
    const position=trailPosition();
    if(position<0){trail.shift();continue}
    if(position===0){trail.pop();continue}
    if(position===trail.length-1){trail.shift();continue}
    const leftDistance=trailIndex-trail[0].historyIndex;
    const rightDistance=trail[trail.length-1].historyIndex-trailIndex;
    if(leftDistance>rightDistance)trail.shift();else trail.pop();
  }
}
function rememberBefore(){
  if(restoring||trailIndex<0)return;
  const position=trailPosition();
  if(position>=0)trail[position]={...captureView(),historyIndex:trailIndex};
  // The browser entry stores its own complete snapshot even after this bounded
  // in-memory trail has evicted the corresponding older entry.
  syncBrowserLocation(false);
}
function rememberAfter(trackCamera=false){
  if(restoring)return;
  const next=captureView(),position=trailPosition(),current=position>=0?trail[position]:null;
  const sameCamera=!trackCamera||(!current?.camera&&!next.camera)||
    (current?.camera&&next.camera&&current.camera.zoom===next.camera.zoom&&
     current.camera.position.every((v,i)=>v===next.camera.position[i]));
  if(current&&samePlace(current,next)&&sameCamera){
    trail[position]={...next,historyIndex:trailIndex};syncBrowserLocation(false);return;
  }
  const hadPrevious=trailIndex>=0;
  if(position>=0)trail.splice(position+1);else trail.splice(0,trail.length);
  trailIndex=++historySerial;
  trail.push({...next,historyIndex:trailIndex});
  trimTrail();
  historyButtons();
  syncBrowserLocation(hadPrevious);
}
function historyButtons(){
  $('ex-back').disabled=trailIndex<=0;
  $('ex-forward').disabled=trailIndex<0||trailIndex>=historySerial;
}
function syncModeButtons(){
  for(const [name,button] of [['all','ex-all'],['local','ex-local']])
    $(button).setAttribute('aria-pressed',String(name===mode));
  $('ex-collapse').textContent=selected?.kind==='node'&&collapsed.has(selected.id)?
    '展开分支':'折叠分支';
}
async function restoreSnapshot(snapshot,indexNext=null){
  if(!snapshot||snapshot.taskId!==taskId||restoring)return;
  restoring=true;navSerial++;
  try{
    mode=['frontier','all','local'].includes(snapshot.mode)?snapshot.mode:'frontier';
    collapsed=new Set(Array.isArray(snapshot.collapsed)?snapshot.collapsed:[]);
    selected=snapshot.selected?{...snapshot.selected}:null;
    syncModeButtons();
    if(selected?.kind){
      await navigate(selected.kind,selected.id,selected.anchor||'',{record:false,focus:false,reveal:false});
    }else{
      reader.innerHTML='<p class="muted">选择左侧科研节点以阅读完整内容。</p>';
      await renderGraph();
    }
    if(snapshot.camera&&graph&&typeof graph.zoomTo==='function'&&typeof graph.translateTo==='function'){
      await graph.zoomTo(snapshot.camera.zoom,false);
      await graph.translateTo(snapshot.camera.position,false);
    }
    reader.scrollTop=Number(snapshot.readerScroll)||0;
    updateZoomLabels();
    updateSourceRail();
    if(Number.isInteger(indexNext)&&indexNext>=0){
      trailIndex=indexNext;historySerial=Math.max(historySerial,indexNext);
      const position=trailPosition(indexNext),entry={...captureView(),historyIndex:indexNext};
      if(position>=0)trail[position]=entry;
      else{trail.push(entry);trail.sort((a,b)=>a.historyIndex-b.historyIndex);trimTrail()}
    }
    historyButtons();
    syncBrowserLocation(false);
  }catch(e){showError('无法恢复历史视图：'+e.message)}
  finally{restoring=false}
}
async function restoreView(delta){
  if(restoring)return;
  const next=trailIndex+delta;
  if(next<0||next>historySerial)return;
  rememberBefore();
  if(typeof history.go==='function'&&history.state?.tmExplorerView){history.go(delta);return}
  const position=trailPosition(next);
  if(position<0)return;
  await restoreSnapshot(trail[position],next);
}
function compactSourceLabel(source){
  const type=knownType(source||{});
  if(type==='External')return externalCycleEstimate(source)?.display||'E?';
  const cycle=displayCycle(source);
  return Number.isSafeInteger(cycle)&&cycle>0?'C'+cycle:'C?';
}

// One Hypothesis owns at most one Outcome, never a separate graph vertex.
// Split HTML controls overlay the G6 rectangle without duplicating its ports.
let outcomeCardsIndex=null;
function updateOutcomeCards(){
  const host=$('ex-outcome-cards');
  if(!index||!graph){host.replaceChildren();outcomeCardsIndex=null;return}
  const lookup=new Map(index.nodes.map(n=>[n.id,n]));
  const shown=visibleNodes();
  const visible=graph.getNodeData().filter(data=>{
    const node=lookup.get(data.id);
    return shown.has(data.id)&&knownType(node||{})==='Hypothesis';
  });
  // Preserve button identity on selection changes: replacing the DOM after
  // the first click prevents browsers from dispatching a real dblclick.
  const existing=Array.from(host.children);
  const statusSignature=node=>{const sem=node?.semantic||{};
    return JSON.stringify([hypothesisFrontier(sem),frontierSummary(sem),
      hypothesisVerdict(sem),(sem.cycle_outcomes||[]).length,
      sem.title,sem.outcome?.summary]);};
  if(outcomeCardsIndex===index&&existing.length===visible.length&&
      visible.every((data,i)=>existing[i].dataset.nodeId===data.id&&
        existing[i].dataset.statusSignature===statusSignature(lookup.get(data.id)))){
    for(const [i,data] of visible.entries()){
      const card=existing[i];
      const [width,height]=data.style?.size||[CARD_W,CARD_H];
      const strip=(data.style?.ports||[]).some(p=>p.key.startsWith('in:'))?SOURCE_STRIP_W:0;
      card.dataset.stripWidth=String(strip);
      card.style.width=(width-strip-8)+'px';card.style.height=(height-8)+'px';
      card.style.opacity='1';
      const chosen=selected?.kind==='node'&&selected.id===data.id?
        (selected.anchor==='outcome'?'outcome':'hypothesis'):'';
      for(const button of Array.from(card.children)){
        const part=button.className.includes('ex-outcome-outcome')?'outcome':'hypothesis';
        button.className=button.className.replace(/\s+is-selected/g,'')+
          (chosen===part?' is-selected':'');
      }
    }
    positionOutcomeCards();return;
  }
  outcomeCardsIndex=index;
  host.replaceChildren();
  for(const data of visible){
    const node=lookup.get(data.id),sem=node?.semantic||{};
    const card=document.createElement('div');
    card.className='ex-outcome-card';card.dataset.nodeId=data.id;
    card.dataset.statusSignature=statusSignature(node);
    const width=data.style?.size?.[0]||CARD_W,height=data.style?.size?.[1]||CARD_H;
    const strip=(data.style?.ports||[]).some(p=>p.key.startsWith('in:'))?SOURCE_STRIP_W:0;
    card.dataset.stripWidth=String(strip);
    card.style.opacity='1';
    card.style.width=(width-strip-8)+'px';card.style.height=(height-8)+'px';
    const cycle=cardCycleText(node,[],lookup);
    const status=hypothesisFrontier(sem),verdict=hypothesisVerdict(sem);
    const records=Array.isArray(sem.cycle_outcomes)?sem.cycle_outcomes.filter(hasRecordedOutcome):[];
    const outcome=records.length===1?records[0]:
      (!records.length&&hasRecordedOutcome(sem.outcome)?sem.outcome:null);
    const outcomeDescription=verdict==='mixed'?'多轮裁决结论不同，请查看各轮 Outcome':
      verdict==='legacy'?'历史兼容结论，不代表 Judge 明确判定 Open':
      verdict==='none'?'本猜想尚无可信 Judge 科学裁决':
      verdict==='unadjudicated'?'阶段性成果；预算拒绝续批，尚未完成 Judge 裁决。'+
        (outcome?.summary||''):
      (outcome?.summary||outcome?.judge_rationale||'查看科学裁决详情');
    const sections=[
      ['hypothesis',cycle+' · H · '+statusLabels[status],
        shortCardTitle(cardScientificTitle(node)),''],
      ['outcome','Outcome · '+statusLabels[verdict]+
        (records.length>1?' · '+records.length+'轮':''),
        abbreviate(outcomeDescription,98),'outcome']
    ];
    for(const [part,title,description,anchor] of sections){
      const button=document.createElement('button');button.type='button';
      button.className='ex-outcome-part ex-outcome-'+part+
        (part==='outcome'?' ex-outcome-verdict-'+verdict:'')+
        (selected?.kind==='node'&&selected.id===data.id&&
          (selected.anchor==='outcome'?'outcome':'hypothesis')===part?' is-selected':'');
      button.style.background=part==='hypothesis'?frontierFill[status]:outcomeFill[verdict];
      button.title=(part==='hypothesis'?'前沿 '+frontierSummary(sem)+'；查看猜想：':
        (verdict==='unadjudicated'?'科研保全状态 ':'科学裁决状态 ')+
        statusLabels[verdict]+'；查看 Outcome：')+description;
      button.setAttribute('aria-label',button.title);
      const heading=document.createElement('span');heading.className='ex-outcome-kicker';
      heading.textContent=title;
      const copy=document.createElement('span');copy.className='ex-outcome-copy';
      copy.textContent=description;
      button.append(heading);button.append(copy);
      button.onclick=event=>{
        event?.preventDefault?.();event?.stopPropagation?.();
        navigate('node',data.id,anchor,{focus:false});
      };
      button.ondblclick=event=>{
        event?.preventDefault?.();event?.stopPropagation?.();
        navigate('node',data.id,anchor,{zoomNode:true,recordCamera:true});
      };
      card.append(button);
    }
    host.append(card);
  }
  positionOutcomeCards();
}
function positionOutcomeCards(){
  if(!graphReady||!graph)return;
  const zoom=safeZoom(),bounds=$('ex-graph').getBoundingClientRect();
  for(const card of Array.from($('ex-outcome-cards').children)){
    try{
      const n=graph.getNodeData(card.dataset.nodeId);
      if(!n){card.style.visibility='hidden';continue}
      const vp=graph.getViewportByCanvas(graph.getElementPosition(card.dataset.nodeId));
      if(!Number.isFinite(vp[0])||!Number.isFinite(vp[1])){
        card.style.visibility='hidden';continue
      }
      const [width,height]=n.style.size,strip=Number(card.dataset.stripWidth)||0;
      const left=vp[0]-width*zoom/2+(strip+4)*zoom;
      const top=vp[1]-height*zoom/2+4*zoom;
      card.style.left=left+'px';card.style.top=top+'px';
      card.style.transform='scale('+zoom+')';
      card.style.visibility=(zoom<=DETAIL_MIN_ZOOM||left>bounds.width||top>bounds.height||
        left+(width-strip-8)*zoom<0||top+(height-8)*zoom<0)?'hidden':'visible';
    }catch{card.style.visibility='hidden'}
  }
}
// Each predecessor index is EMBEDDED in its node's left 42px. The rows
// share the exact ordering and normalized Y position used by target G6 ports.
// No source Canvas geometry, floating source rail, or inferred relationship.
function updateSourceRail(){
  const host=$('ex-source-rail');
  host.replaceChildren();
  host.hidden=compactView();
  if(!index||!graph)return;
  if(compactView()){
    rebuildCompactLabels();positionSourceRail();updateOutcomeCards();return;
  }
  const shown=visibleNodes();
  const graphNodes=graph.getNodeData().filter(n=>shown.has(n.id));
  const visibleEdges=graph.getEdgeData();
  const incoming=new Map();
  for(const e of visibleEdges){
    const raw=e.data?.sourceEdge||index.edges.find(x=>x.id===e.id);
    if(!raw)continue;
    if(!incoming.has(e.target))incoming.set(e.target,[]);
    incoming.get(e.target).push(raw);
  }
  for(const items of incoming.values())
    items.sort((a,b)=>stable(a.fromNode,b.fromNode)||stable(a.id,b.id));
  const lookup=new Map(index.nodes.map(n=>[n.id,n]));
  for(const n of graphNodes){
    const links=incoming.get(n.id)||[];
    if(!links.length)continue;
    const strip=document.createElement('div');
    strip.className='ex-node-source-strip';
    strip.dataset.nodeId=n.id;
    strip.style.width=SOURCE_STRIP_W+'px';
    strip.style.height=(n.style?.size?.[1]||cardHeight(links.length))+'px';
    strip.setAttribute('aria-label','前序节点索引：'+n.id);
    for(const [i,edge] of links.entries()){
      const source=lookup.get(edge.fromNode);
      const scienceType=knownType(source||{});
      const color=sourceTypeColor(edge);
      const button=document.createElement('button');
      button.className='ex-source-link';button.type='button';
      button.dataset.sourceId=edge.fromNode;button.dataset.edgeId=edge.id;
      button.style.visibility=shownElements.get(ID(edge.id||edge.fromNode+'->'+edge.toNode))==='hidden'?'hidden':'visible';
      button.style.color=color;
      button.style.borderLeftColor=color;
      button.title='来源：'+edge.fromNode+' · '+(source?.semantic?.title||source?.label||'')+
        '（'+(typeNames[scienceType]||scienceType)+'）';
      button.setAttribute('aria-label','跳转前序节点 '+edge.fromNode);
      button.textContent=compactSourceLabel(source);
      button.onclick=event=>{
        event?.preventDefault?.();event?.stopPropagation?.();
        navigate('node',edge.fromNode,'',{focus:true});
      };
      strip.append(button);
    }
    host.append(strip);
  }
  rebuildCompactLabels();
  positionSourceRail();
  updateOutcomeCards();
}
// The compact label layer is an optional presentation overlay. Earlier page
// HTML (e.g. an open tab during a Web update) can lack it even while the newest
// JS is executing. Recreate it on demand; never abort the scientific graph.
function compactLabelHost(){
  const present=$('ex-compact-labels');
  if(present)return present;
  const parent=$('ex-graph');
  if(!parent)return null;
  const host=document.createElement('div');
  host.id='ex-compact-labels';host.className='ex-compact-labels';
  host.setAttribute('aria-hidden','true');host.hidden=true;
  parent.append(host);
  return host;
}
// Read-only labels never affect G6 geometry or intercept graph interactions.
function rebuildCompactLabels(){
  const host=compactLabelHost();
  if(!host)return;
  host.replaceChildren();
  if(!graphReady||!graph||!index)return;
  const visible=visibleNodes();
  for(const node of graph.getNodeData()){
    if(!visible.has(node.id))continue;
    const label=document.createElement('span');
    label.className='ex-compact-label';
    label.dataset.nodeId=node.id;
    label.textContent=node.data?.compactLabel||'?';
    label.style.opacity='1';
    host.append(label);
  }
}
function positionCompactLabels(){
  const host=compactLabelHost();
  if(!host)return;
  const compact=compactView();
  host.hidden=!compact;
  if(!compact||!graphReady||!graph)return;
  const zoom=safeZoom(),bounds=$('ex-graph').getBoundingClientRect();
  for(const label of Array.from(host.children)){
    try{
      const node=graph.getNodeData(label.dataset.nodeId);
      const [x,y]=graph.getViewportByCanvas(graph.getElementPosition(label.dataset.nodeId));
      if(!node||!Number.isFinite(x)||!Number.isFinite(y)){
        label.style.visibility='hidden';continue;
      }
      const width=node.style.size[0]*zoom,height=node.style.size[1]*zoom;
      const left=x-width/2,top=y-height/2;
      label.style.left=left+'px';label.style.top=top+'px';
      label.style.width=width+'px';label.style.height=height+'px';
      label.style.fontSize=compactFontPixels(label.textContent,width,height)+'px';
      label.style.visibility=(left>bounds.width||top>bounds.height||left+width<0||top+height<0)?'hidden':'visible';
    }catch{label.style.visibility='hidden'}
  }
}
function positionSourceRail(){
  const host=$('ex-source-rail');
  if(!graphReady||!graph)return;
  const zoom=safeZoom();
  host.hidden=compactView();
  const bounds=$('ex-graph').getBoundingClientRect();
  for(const strip of Array.from(host.children)){
    try{
      const n=graph.getNodeData(strip.dataset.nodeId);
      const point=graph.getElementPosition(strip.dataset.nodeId);
      const vp=graph.getViewportByCanvas(point);
      if(!n||!Number.isFinite(vp[0])||!Number.isFinite(vp[1])){
        strip.style.visibility='hidden';continue;
      }
      const width=n.style.size[0],height=n.style.size[1];
      const left=vp[0]-width*zoom/2,top=vp[1]-height*zoom/2;
      // Keep this overlay strictly inside the actual node card's left edge.
      strip.style.left=left+'px';strip.style.top=top+'px';
      strip.style.transform='scale('+zoom+')';
      strip.style.visibility=(zoom<=DETAIL_MIN_ZOOM||left>bounds.width||top>bounds.height||
        left+SOURCE_STRIP_W*zoom<0||top+height*zoom<0)?'hidden':'visible';
    }catch{strip.style.visibility='hidden'}
  }
  positionOutcomeCards();
  positionCompactLabels();
}
// Only switch between compact and detailed labels; never rerun Dagre.
let lastCompactMode=null;
function updateZoomLabels(){
  // This is the native G6 camera scale, not a percentage of fit-to-view.
  // 100% always maps to the fixed CARD_W/CARD_H graph-coordinate size.
  $('ex-zoom-percent').textContent=Math.round(safeZoom()*100)+'%';
  if(!graphReady||!graph)return;
  const compact=compactView(),changes=[];
  if(lastCompactMode!==compact){lastCompactMode=compact;updateSourceRail();renderColorLegend()}
  for(const n of graph.getNodeData()){
    const source=index?.nodes.find(source=>source.id===n.id);
    const next={...graphLabelStyle(n.data||{},n.style||{},compact),
      ...(source?{fill:nodeFill(source,compact)}:{})};
    if(Object.entries(next).some(([key,value])=>n.style?.[key]!==value))
      changes.push({id:n.id,style:next});
  }
  if(changes.length){
    graph.updateNodeData(changes);
    Promise.resolve(graph.draw()).catch(err=>showError('缩放标签更新失败：'+err.message));
  }
}
// Double-click enlarges a numbered node into its readable detail level.
// Further double-clicks progressively zoom, up to a bounded maximum.
async function zoomIntoNode(id){
  if(!graphReady||!graph?.getNodeData(id))return;
  const currentZoom=safeZoom();
  const targetZoom=Math.max(currentZoom,Math.min(2.4,Math.max(1.15,currentZoom*1.6)));
  await graph.zoomTo(targetZoom,{duration:300,easing:'ease-in-out'});
  await graph.focusElement(id,{duration:360,easing:'ease-in-out'});
  updateZoomLabels();
  positionSourceRail();
}
// G6 5 element visibility changes only paint the current nodes and lines.
// Collapse is not an input to Dagre layout, so coordinates and camera stay put.
let shownElements=new Map();
function visibleNodes(){
  if(!index)return new Set();
  return visibility(structure(index.nodes,index.edges)).visible;
}
function desiredVisibility(){
  const s=structure(index.nodes,index.edges),vis=visibility(s);
  const displayedEdges=new Set(s.allEdges.filter(e=>vis.visible.has(e.fromNode)&&
    vis.visible.has(e.toNode)&&!(collapsed.has(e.fromNode)&&isScientificEdge(e)))
    .map(e=>ID(e.id||e.fromNode+'->'+e.toNode)));
  const result=new Map();
  for(const n of graph.getNodeData())result.set(n.id,vis.visible.has(n.id)?'visible':'hidden');
  for(const e of graph.getEdgeData())result.set(e.id,displayedEdges.has(e.id)?'visible':'hidden');
  return result;
}
async function syncElementVisibility(){
  if(!graphReady||!graph)return;
  const wanted=desiredVisibility(),changes={};
  for(const [id,value] of wanted)if(shownElements.get(id)!==value)changes[id]=value;
  shownElements=wanted;
  if(Object.keys(changes).length)await graph.setElementVisibility(changes,false);
}
async function updateSelectionWithoutLayout(opts={}){
  if(!index)return;
  if(!graphReady||!graph){await renderGraph({focus:opts.focus&&selected?.kind==='node'?selected.id:null});return}
  const next=g6Data(true);
  const oldNodes=graph.getNodeData(),oldEdges=graph.getEdgeData();
  const signature=arr=>arr.map(x=>x.id).sort(stable).join('\u0000');
  if(signature(next.nodes)!==signature(oldNodes)||signature(next.edges)!==signature(oldEdges)){
    // A visibility/mode change requires relayout. renderGraph expects a node
    // ID, whereas selection-paint opts.focus is a boolean; preserve centering.
    await renderGraph({...opts,focus:opts.focus&&selected?.kind==='node'?selected.id:null});return;
  }
  graph.updateNodeData(next.nodes.map(({id,style})=>({id,style})));
  graph.updateEdgeData(next.edges.map(({id,style})=>({id,style})));
  await graph.draw(); // Only paint styles: NEVER re-run Dagre on a node click.
  await syncElementVisibility();
  if(opts.focus&&selected?.kind==='node')await graph.focusElement(selected.id,
    opts.animateFocus?{duration:360,easing:'ease-in-out'}:undefined);
  updateSourceRail();
}
async function ensureG6(){
  if(window.G6?.Graph)return;
  // Alternate official CDN when jsDelivr is blocked; never substitute another library.
  await new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    script.src='https://unpkg.com/@antv/g6@5.1.1/dist/g6.min.js';
    script.onload=resolve;
    script.onerror=()=>reject(Error('G6 的两个 CDN 均无法访问'));
    document.head.appendChild(script);
  });
  if(!window.G6?.Graph)throw Error('G6 引擎未能初始化');
}
// G6 listens for wheel events on its own canvas, not on HTML node controls.
// Bridge only overlay-origin gestures; native canvas gestures stay with G6.
$('ex-graph').addEventListener('wheel',event=>{
  if(!graphReady||!graph||!event.target?.closest?.('.ex-outcome-card, .ex-node-source-strip'))return;
  event.preventDefault();
  event.stopPropagation();
  if(event.ctrlKey){
    const rect=$('ex-g6').getBoundingClientRect();
    const origin=[event.clientX-rect.left,event.clientY-rect.top];
    Promise.resolve(graph.zoomBy(Math.exp(-event.deltaY*0.008),false,origin))
      .catch(err=>showError('节点缩放失败：'+err.message));
  }else{
    Promise.resolve(graph.translateBy([-event.deltaX,-event.deltaY],false))
      .catch(err=>showError('节点平移失败：'+err.message));
  }
},{passive:false});
function graphInit(){
  ensureOverviewColorToggle();
  if(graph)return;
  if(!window.G6?.Graph)throw Error('G6 依赖未加载（CDN 不可达）');
  graph=new window.G6.Graph({
    container:'ex-g6',autoResize:true,autoFit:false,animation:false,
    data:{nodes:[],edges:[]},
    // Positions come from authoritative Cycle columns; no automatic graph
    // layout may override the fixed X axis or move branches on selection.
    node:{type:'rect'},
    edge:{type:'polyline',style:{router:{type:'orth'}}},
    // On trackpads, swipe emits wheel without ctrlKey; pinch emits wheel with ctrlKey.
    // Use mutually exclusive G6 behaviors so one gesture cannot both pan and zoom.
    behaviors:[
      'drag-canvas',
      {type:'scroll-canvas',key:'trackpad-pan',enable:event=>!event.ctrlKey,
        sensitivity:1,range:Infinity},
      {type:'zoom-canvas',key:'trackpad-pinch',enable:event=>!!event.ctrlKey,
        sensitivity:0.8}
    ]
  });
  graph.on('node:click',e=>{const id=e.target?.id;if(id)navigate('node',id,'',{focus:false})});
  // Double-click enlarges and centers the node; never collapse its branch.
  graph.on('node:dblclick',e=>{const id=e.target?.id;
    if(id)navigate('node',id,'',{zoomNode:true,recordCamera:true});
  });
  graph.on('node:pointerover',e=>{
    const n=index?.nodes.find(n=>n.id===e.target?.id);
    if(!n)return;
    const sem=n.semantic||{},isExternal=knownType(n)==='External';
    const status=isExternal?'外部参考资料 · 非科研 Cycle':
      knownType(n)==='Hypothesis'?
        ('前沿 '+frontierSummary(sem)+' / 科学 '+statusLabels[hypothesisVerdict(sem)]):
        ([sem.scientific_status||sem.verdict,sem.frontier_status].filter(Boolean).join(' / ')||'未标注状态');
    const source=isExternal&&sem.source?' · 来源 '+sem.source:'';
    const timeNote=isExternal?' · '+(externalCycleEstimate(n)?.note||'未估算时间'):'';
    tooltip.innerHTML='<strong>'+esc(sem.title||n.label)+'</strong>'+
      esc(isExternal?'外部资料':knownType(n))+' · '+esc(status+source+timeNote)+
      '<small>单击查看详情 · 双击放大并居中 · 顶部按钮折叠分支</small>';
    tooltip.hidden=false;
  });
  graph.on('node:pointermove',e=>{
    const rect=$('ex-graph').getBoundingClientRect(),ev=e.originalEvent;
    if(!ev)return;
    const x=Math.max(8,Math.min(rect.width-310,ev.clientX-rect.left+14));
    const y=Math.max(60,Math.min(rect.height-110,ev.clientY-rect.top+15));
    tooltip.style.left=x+'px';tooltip.style.top=y+'px';
  });
  graph.on('node:pointerleave',()=>tooltip.hidden=true);
  graph.on('aftertransform',()=>{updateZoomLabels();positionSourceRail()});
  graph.on('edge:click',e=>{
    const id=ID(e.target?.id),relation=index?.edges.find(x=>ID(x.id||x.fromNode+'->'+x.toNode)===id);
    if(!relation)return;
    const selectedNode=selected?.kind==='node'?selected.id:null;
    if(selectedNode){
      const visibleIds=visibleNodes();
      const visibleEdges=index.edges.filter(x=>visibleIds.has(x.fromNode)&&visibleIds.has(x.toNode)&&
        !(collapsed.has(x.fromNode)&&isScientificEdge(x)));
      const downstream=downstreamHighlight(selectedNode,visibleEdges);
      const next=downstream.lineageIds.has(id)?relation.toNode:
        relation.toNode===selectedNode?relation.fromNode:null;
      if(next&&visibleIds.has(next)){
        navigate('node',next,'',{focus:true,animateFocus:true,recordCamera:true});return;
      }
    }
    // Unrelated edges still open the provenance/relation detail view.
    navigate('edge',id,'',{focus:false});
  });
}
async function renderGraph(opts={}){
  if(!index)return;
  const ticket=++renderSerial;
  try{
    await ensureG6();
    graphInit();
    // The graph object may exist before its viewport does.
    graphReady=false;
    const data=g6Data(true);
    shownElements=new Map();
    graph.setData(data);
    await graph.render();
    if(ticket!==renderSerial)return;
    graphReady=true;
    await syncElementVisibility();
    if(opts.fit)await graph.fitView({when:'always',direction:'both'});
    else if(opts.focus&&graph.getNodeData(opts.focus))await graph.focusElement(opts.focus,
      opts.animateFocus?{duration:360,easing:'ease-in-out'}:undefined);
    updateZoomLabels();
    updateSourceRail();
    alertBox.hidden=true;
  }catch(e){showError(e?.message||String(e))}
}
function fit(){if(graph)graph.fitView()}
function zoom(factor){if(graph)graph.zoomBy(factor)}
async function setMode(next){
  if(next===mode)return;
  rememberBefore();mode=next;syncModeButtons();
  await renderGraph({fit:true});rememberAfter();
}
async function toggleCollapse(){
  if(selected?.kind!=='node')return;
  rememberBefore();
  const id=selected.id;
  if(collapsed.has(id))collapsed.delete(id);else collapsed.add(id);
  syncModeButtons();
  await updateSelectionWithoutLayout({focus:false});rememberAfter();
}
function revealNode(id){
  const s=structure(index.nodes,index.edges);
  if(!baseScope(s).has(id))mode='all';
  // Search/navigation never remains trapped behind a collapsed ancestor.
  const related=traverse([id],s.parents);
  for(const x of related)collapsed.delete(x);
  syncModeButtons();
}
function resolve(target,source){
  let key;try{key=decodeURIComponent(target.split('|')[0].split('#')[0]).trim()}catch{return null}
  if(!key)return null;
  const aliases=index.aliases||{};
  let candidates=[key,key.replace(/\.md$/i,'')];
  if(source&&key.startsWith('.')){
    const parts=source.split('/');parts.pop();
    for(const part of key.split('/'))if(part==='..')parts.pop();else if(part!=='.')parts.push(part);
    candidates.unshift(parts.join('/'));
  }
  for(const c of candidates){const hit=aliases[c.toLowerCase()];if(hit){
    const sep=hit.indexOf(':');return {kind:hit.slice(0,sep),id:hit.slice(sep+1),
      anchor:target.split('#')[1]?.split('|')[0]||''};
  }}return null;
}
function mdHtml(text,source){
  if(!window.marked||!window.DOMPurify)return '<pre>'+esc(text)+'</pre>';
  const math=[];
  let protectedText=text.replace(/(\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g,raw=>{
    const block=raw.startsWith('$$');math.push({value:raw.slice(block?2:1,block?-2:-1),block});
    return 'TMKATEXPLACEHOLDER'+(math.length-1)+'END';
  });
  protectedText=protectedText.replace(/(?<!!)\[\[([^\]\n]+)\]\]/g,(full,link)=>{
    const label=link.split('|')[1]||link.split('|')[0],ref=resolve(link,source);
    return ref?'['+label.replace(/[\[\]]/g,'')+'](#tmref='+encodeURIComponent(ref.kind+':'+ref.id)+
      (ref.anchor?'%23'+encodeURIComponent(ref.anchor):'')+')':esc(label);
  });
  const result=window.DOMPurify.sanitize(window.marked.parse(protectedText));
  const doc=new DOMParser().parseFromString('<div id="parsed">'+result+'</div>','text/html');
  const wrap=doc.getElementById('parsed');
  wrap.querySelectorAll('a').forEach(a=>{
    const href=a.getAttribute('href')||'';
    if(href.startsWith('#tmref=')){a.dataset.ref=decodeURIComponent(href.slice(7));a.removeAttribute('href')}
    else if(href.startsWith('#')){}
    else if(/^https?:\/\//i.test(href)){a.target='_blank';a.rel='noopener noreferrer'}
    else{const ref=resolve(href,source);if(ref){
      a.dataset.ref=uri(ref.kind,ref.id)+(ref.anchor?'#'+ref.anchor:'');a.removeAttribute('href');
    }else a.removeAttribute('href')}
  });
  wrap.querySelectorAll('*').forEach(el=>{for(const child of Array.from(el.childNodes)){
    if(child.nodeType!==3||!child.textContent.includes('TMKATEXPLACEHOLDER'))continue;
    const fragment=doc.createDocumentFragment(),re=/TMKATEXPLACEHOLDER(\d+)END/g;
    let pos=0,m;
    while((m=re.exec(child.textContent))){
      fragment.append(doc.createTextNode(child.textContent.slice(pos,m.index)));
      const spec=math[Number(m[1])],box=doc.createElement(spec.block?'div':'span');
      try{if(window.katex)box.innerHTML=window.katex.renderToString(spec.value,{
        displayMode:spec.block,throwOnError:false,trust:false,output:'html'
      });else box.textContent=spec.value}catch{box.textContent=spec.value}
      fragment.append(box);pos=re.lastIndex;
    }
    fragment.append(doc.createTextNode(child.textContent.slice(pos)));child.replaceWith(fragment);
  }});
  return wrap.innerHTML;
}
function refsBlock(key){
  const refs=index.backlinks[key]||[];
  return '<div class="ex-section"><strong>反向引用 ('+refs.length+')</strong><div>'+
    (refs.length?refs.map(r=>'<button class="ex-ref" data-ref="'+esc(uri(r.kind,r.id))+'">'+
      esc(r.label)+'</button>').join(''):'<p class="muted">暂无引用</p>')+'</div></div>';
}
function outbound(key){
  const refs=index.references[key]||[];
  return '<div class="ex-section"><strong>文档引用 ('+refs.length+')</strong><div>'+
    refs.map(r=>'<button class="ex-ref" data-ref="'+esc(uri(r.kind,r.id)+(r.anchor?'#'+r.anchor:''))+
      '">'+esc(r.id)+(r.anchor?'#'+r.anchor:'')+'</button>').join('')+'</div></div>';
}
// Canvas node headers are presentation metadata, not the scientific Markdown body.
// This is a read-only rendering split: original node.text is never altered.
function splitNodeHeader(text){
  const original=ID(text);
  const lines=original.replace(/^\uFEFF/,'').split(/\r?\n/);
  const fields=[];
  let end=0;
  for(let i=0;i<Math.min(lines.length,40);i++){
    const line=lines[i];
    if(!line.trim()){end=i+1;break}
    const match=/^([A-Za-z][A-Za-z0-9_]*):[ \t]*(.*)$/.exec(line);
    if(!match){end=i;break}
    fields.push({key:match[1],value:match[2]});
    end=i+1;
  }
  // Don't hide ordinary prose that just happens to begin with "Key: value".
  if(!fields.some(f=>f.key==='type')||!fields.some(f=>f.key==='id'))
    return {fields:[],body:original};
  return {fields,body:lines.slice(end).join('\n')};
}
function nodeHeaderDisclosure(fields){
  if(!fields.length)return '';
  return '<details class="ex-node-tags">'+
    '<summary>节点标签 <span class="ex-node-tags-count">('+fields.length+')</span></summary>'+
    '<div class="ex-node-tags-list">'+fields.map(field=>
      '<div class="ex-node-tag-line"><span class="ex-node-tag-key">'+esc(field.key)+
      ':</span> <span class="ex-node-tag-value">'+esc(field.value)+'</span></div>'
    ).join('')+'</div></details>';
}
async function navigate(kind,id,anchor='',options={}){
  if(!index)return;
  if(kind==='node'&&!index.nodes.some(n=>n.id===id))return;
  if(kind==='edge'&&!index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===id))return;
  const request=++navSerial;
  if(options.record!==false)rememberBefore();
  // Apply an explicitly requested view mode *after* saving the old location,
  // so browser Back returns to the correct previous mode, camera and node.
  if(options.mode==='frontier'||options.mode==='all')mode=options.mode;
  selected={kind,id,anchor};
  const key=uri(kind,id);
  if(kind==='node'){
    const node=index.nodes.find(n=>n.id===id);if(!node)return;
    const m=node.semantic||{};
    const header=splitNodeHeader(node.text||'');
    const timeEstimate=knownType(node)==='External'?externalCycleEstimate(node):null;
    // ID, type, scientific/frontier status live only in collapsed 节点标签;
    // do not duplicate them in a flat metadata header above the disclosure.
    const sourceHeaderTime=header.fields.find(field=>field.key==='timestamp')?.value;
    const when=knownType(node)==='Hypothesis'?hypothesisTime(
      {...m,timestamp:m.timestamp||sourceHeaderTime}):
      ((m.timestamp||sourceHeaderTime)?'原始时间 · '+
        compactTime(m.timestamp||sourceHeaderTime):'');
    const timeLine=when?'<div class="ex-node-time">'+esc(when)+'</div>':'';
    const estimatedNote=timeEstimate?'<p class="muted">'+esc(timeEstimate.note)+'</p>':'';
    const identityNote=m.legacy_identity_review?.status==='unadjudicated_whole_h'
      ?'<p class="muted">历史身份待核验：当前整体 Open 仅为迁移占位，并非 Judge 对整个 H 的科学裁决；保留下方各 Cycle 原始结论。</p>':'';
    const dossierButton=knownType(node)==='Hypothesis'?
      (m.dossier_ref?
        '<div class="ex-section"><button class="ex-ref" data-ref="'+esc(uri('doc',m.dossier_ref))+'">查看完整猜想文档 →</button></div>':
        '<p class="muted">当前无法关联到可读取的猜想档案'+
        (m.unavailable_dossier_ref?'（数据库登记的文件路径不可读取）':'（请检查后台服务版本、文档索引及文件同步）')+'；这不代表猜想原文不存在。</p>') : '';

    reader.innerHTML='<h2>'+esc(node.label)+'</h2>'+timeLine+
      nodeHeaderDisclosure(header.fields)+estimatedNote+identityNote+dossierButton+
      '<div class="ex-md">'+mdHtml(header.body,null)+'</div>'+
      outcomeDetailsHtml(m.cycle_outcomes?.length?m.cycle_outcomes:(m.outcome?[m.outcome]:[]))+outbound(key)+refsBlock(key);
    if(options.reveal!==false)revealNode(id);
    syncModeButtons();
    await updateSelectionWithoutLayout({focus:options.zoomNode?false:options.focus!==false,
      animateFocus:options.animateFocus===true});
    if(request!==navSerial)return;
    if(options.zoomNode){
      await zoomIntoNode(id);
      if(request!==navSerial)return;
    }
    const linked=(index.references[key]||[]).filter(r=>r.kind==='doc'&&r.id!==m.dossier_ref)
      .sort((a,b)=>Number(b.id.startsWith('nodes/'))-Number(a.id.startsWith('nodes/')))[0];
    if(linked){
      const section=document.createElement('section');section.className='ex-section';
      section.innerHTML='<h3>关联 Markdown</h3><p class="muted">关联文档单独阅读，不合并到当前 Canvas 节点。</p>'+
        '<button class="ex-ref" data-ref="'+esc(uri('doc',linked.id))+'">打开关联文档：'+esc(linked.id)+' →</button>';
      reader.append(section);
    }
  }else if(kind==='edge'){
    const edge=index.edges.find(e=>ID(e.id||e.fromNode+'->'+e.toNode)===id);
    if(options.reveal!==false){
      revealNode(edge.fromNode);revealNode(edge.toNode);
    }
    reader.innerHTML='<h2>科研连线</h2><div class="ex-meta">'+esc(edge.id||id)+'</div>'+
      '<p><strong>来源：</strong><button class="ex-ref" data-ref="'+esc(uri('node',edge.fromNode))+'">'+esc(edge.fromNode)+'</button></p>'+
      '<p><strong>目标：</strong><button class="ex-ref" data-ref="'+esc(uri('node',edge.toNode))+'">'+esc(edge.toNode)+'</button></p>'+
      '<p><strong>关系：</strong>'+esc(edge.edge_type||edge.label||'Canvas directed edge')+'</p>'+
      (edge.evolution_operator?'<p><strong>科学派生：</strong>'+esc(edge.evolution_operator)+'</p>':'')+
      (edge.originating_outcome?'<p><strong>来源 Outcome：</strong>'+esc(edge.originating_outcome)+'</p>':'')+
      (edge.provenance_json&&edge.provenance_json!=='{}'?
        '<details><summary>科研连线来源记录</summary><pre>'+esc(edge.provenance_json)+'</pre></details>':'');
    syncModeButtons();
    await updateSelectionWithoutLayout();
  }else if(kind==='doc'){
    try{
      const doc=await api(base()+'/document?path='+encodeURIComponent(id));
      if(request!==navSerial||selected?.kind!==kind||selected.id!==id)return;
      reader.innerHTML='<h2>'+esc(doc.title)+'</h2><div class="ex-meta">Markdown · '+
        esc(doc.id)+'</div><div class="ex-md" data-doc-content="1">'+mdHtml(doc.content,doc.id)+'</div>'+
        (doc.next_offset!==null&&doc.next_offset!==undefined?
          '<button class="ex-ref" data-doc-more="'+doc.next_offset+'">加载后续 Markdown 内容 ↓</button>':'')+
        outbound(key)+refsBlock(key);
      await updateSelectionWithoutLayout();
    }catch(e){reader.textContent='无法加载：'+e.message}
  }
  if(request!==navSerial)return;
  reader.scrollTop=0;
  if(anchor){
    const outcomeTarget=anchor==='outcome'&&kind==='node'?
      reader.querySelector('.ex-outcome-details'):null;
    if(outcomeTarget)outcomeTarget.scrollIntoView({block:'start'});
    else if(anchor!=='outcome'){
      const candidates=reader.querySelectorAll('.ex-md h1,.ex-md h2,.ex-md h3,.ex-md h4,.ex-md h5,.ex-md h6');
      const target=Array.from(candidates).find(h=>h.id===anchor||h.textContent.trim()===anchor||
        h.textContent.trim().startsWith(anchor));
      if(target)target.scrollIntoView({block:'start'});
    }
  }
  if(options.record!==false)rememberAfter(Boolean(options.recordCamera));
  else if(!restoring)syncBrowserLocation(false);
}
reader.addEventListener('click',async e=>{
  const more=e.target.closest('[data-doc-more]');
  if(more){
    if(selected?.kind!=='doc')return;
    const docId=selected.id,offset=Number(more.dataset.docMore);
    if(!Number.isSafeInteger(offset)||offset<0)return;
    more.disabled=true;
    try{
      const page=await api(base()+'/document?path='+encodeURIComponent(docId)+
        '&offset='+encodeURIComponent(offset));
      if(selected?.kind!=='doc'||selected.id!==docId)return;
      const target=reader.querySelector('[data-doc-content]');
      if(target)target.insertAdjacentHTML('beforeend',mdHtml(page.content,docId));
      if(page.next_offset===null||page.next_offset===undefined)more.remove();
      else{more.dataset.docMore=String(page.next_offset);more.disabled=false}
      await updateSelectionWithoutLayout();
    }catch(err){more.disabled=false;more.textContent='读取失败，点击重试：'+err.message}
    return;
  }
  const el=e.target.closest('[data-ref]');if(!el)return;
  const v=el.dataset.ref,split=v.indexOf(':'),hash=v.indexOf('#');
  if(split>0)navigate(v.slice(0,split),v.slice(split+1,hash<0?undefined:hash),hash<0?'':v.slice(hash+1));
});
function decodeHash(){
  let hash;try{hash=decodeURIComponent(location.hash.slice(1))}catch{return null}
  const i=hash.indexOf(':');if(i<1)return null;
  const h=hash.indexOf('#');return {kind:hash.slice(0,i),id:hash.slice(i+1,h<0?undefined:h),
    anchor:h<0?'':hash.slice(h+1)};
}
// An ordinary entry always begins with the complete chronology, anchored to
// the newest frozen H. External time estimates must not supersede scientific
// Cycle ownership; older archived Decisions are only a fallback for V1 data.
function latestScientificNodeId(){
  if(!index?.nodes?.length)return null;
  const scientific=index.nodes.filter(n=>knownType(n)==='Hypothesis'||
    knownType(n)==='Decision');
  const real=scientific.filter(n=>Number.isSafeInteger(n.semantic?.cycle)&&n.semantic.cycle>0);
  const hypotheses=real.filter(n=>knownType(n)==='Hypothesis');
  const fallback=scientific.filter(n=>knownType(n)==='Hypothesis')
    .map(n=>({node:n,cycle:displayCycle(n)}))
    .filter(entry=>Number.isSafeInteger(entry.cycle)&&entry.cycle>0);
  const candidates=(hypotheses.length?hypotheses:real)
    .map(n=>({node:n,cycle:n.semantic.cycle}));
  // Prefer authoritative Cycle IDs; inferred H coordinates are not a substitute
  // for an existing frozen Cycle identifier.
  const scored=candidates.length?candidates:fallback;
  scored.sort((a,b)=>b.cycle-a.cycle||stable(b.node.id,a.node.id));
  return scored[0]?.node.id||null;
}
async function load({fresh=false}={}){
  try{
    const newIndex=await api(base()+'/index'),previous=index;
    index=newIndex;
    overviewRender();
    ++tipWorkerRequest;$('ex-tip-panel').hidden=true;
    const indexWarning=index.stats.partial?' · 文档索引达到总读取预算，部分链接可能未索引':'';
    $('ex-stats').textContent=index.stats.node_count+' 节点 · '+
      index.stats.edge_count+' 科研连线 · '+index.stats.doc_count+' 文档'+indexWarning;
    const hash=decodeHash();
    const hashValid=hash&&((hash.kind==='node'&&index.nodes.some(n=>n.id===hash.id))||
      (hash.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===hash.id))||
      hash.kind==='doc');
    const saved=history.state?.tmExplorerView;
    if(fresh&&!hashValid){
      // A new direct visit or a task switch has no explicit document target.
      // Old hashless camera snapshots must not override the default landing.
      mode='all';selected=null;collapsed.clear();syncModeButtons();
      const newest=latestScientificNodeId();
      await renderGraph(newest?{focus:newest}:{fit:true});
      if(trailIndex<0)rememberAfter();
    }else if(fresh&&hashValid){
      mode='all';selected=null;collapsed.clear();syncModeButtons();
      await navigate(hash.kind,hash.id,hash.anchor,{record:false,focus:true});
    }else if(saved?.taskId===taskId&&(!saved.selected||
      (saved.selected.kind==='node'&&index.nodes.some(n=>n.id===saved.selected.id))||
      (saved.selected.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===saved.selected.id))||
      saved.selected.kind==='doc')){
      await restoreSnapshot(saved,history.state?.tmExplorerIndex);
    }else if(hashValid){
      await navigate(hash.kind,hash.id,hash.anchor,{record:false,focus:false});
    }else if(selected?.kind==='node'&&index.nodes.some(n=>n.id===selected.id)){
      await navigate('node',selected.id,selected.anchor||'',{record:false,focus:false});
    }else if(selected?.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===selected.id)){
      await navigate('edge',selected.id,'',{record:false,focus:false});
    }else{
      selected=null;
      await renderGraph({fit:!previous});
      if(trailIndex<0)rememberAfter();
    }
    if(trailIndex<0)rememberAfter();
    else syncBrowserLocation(false);
  }catch(e){showError(e.message);$('ex-stats').textContent='加载失败 · 保留当前文档'}
}
// Pane sizing is a reading preference, not scientific or browser-navigation state.
// The desktop layout always stays side-by-side; narrow screens adapt responsively.
// Never rerender Dagre or mutate the Canvas when only a pane is resized.
const LAYOUT_KEY='tm-explorer-pane-layout-v1';
const pane={side:63,stack:50};
const bounded=(value,min,max)=>Math.max(min,Math.min(max,value));
const isNarrow=()=>Boolean(window.matchMedia?.('(max-width:860px)').matches);
const isStacked=isNarrow;
function persistPane(){
  try{window.localStorage?.setItem(LAYOUT_KEY,JSON.stringify(pane))}catch{}
}
let railResizePending=false, lastSizedGraph=null, lastCanvasSize=null;
function refreshRailAfterResize(){
  if(railResizePending)return;
  railResizePending=true;
  const schedule=window.requestAnimationFrame?.bind(window)||((fn)=>setTimeout(fn,16));
  schedule(()=>{
    railResizePending=false;
    if(!graphReady||!graph)return;
    const rect=$('ex-g6').getBoundingClientRect();
    const size=[Math.round(rect.width),Math.round(rect.height)];
    if(size.every(n=>n>0)&&(graph!==lastSizedGraph||
        !lastCanvasSize||size.some((n,i)=>n!==lastCanvasSize[i]))){
      // G6 autoResize only tracks window resizing, not CSS grid splitter drags.
      graph.resize(...size);
      lastSizedGraph=graph;lastCanvasSize=size;
    }
    positionSourceRail();
  });
}
function syncPane(){
  layout.dataset.layout='side';
  const style=layout.style;
  if(style.setProperty){style.setProperty('--ex-side-size',pane.side+'%');style.setProperty('--ex-stack-size',pane.stack+'%')}
  else{style['--ex-side-size']=pane.side+'%';style['--ex-stack-size']=pane.stack+'%'}
  const stacked=isStacked(),value=stacked?pane.stack:pane.side;
  const {lo,hi}=paneRange(stacked?'stack':'side');
  splitter.setAttribute('aria-orientation',stacked?'horizontal':'vertical');
  splitter.setAttribute('aria-valuemin',String(Math.round(lo)));
  splitter.setAttribute('aria-valuemax',String(Math.round(hi)));
  splitter.setAttribute('aria-valuenow',String(Math.round(value)));
  splitter.setAttribute('aria-valuetext',Math.round(value)+'% 图谱');
  refreshRailAfterResize();
}
function paneRange(axis){
  const rect=layout.getBoundingClientRect();
  const available=Math.max(1,(axis==='stack'?rect.height:rect.width)-12);
  const minimum=axis==='stack'?170:280,otherMinimum=axis==='stack'?160:280;
  const lo=Math.min(48,Math.max(20,minimum/available*100));
  const hi=Math.max(52,Math.min(80,100-otherMinimum/available*100));
  return {lo,hi};
}
function changePaneSize(value){
  const axis=isStacked()?'stack':'side';
  const {lo,hi}=paneRange(axis);
  pane[axis]=Math.round(bounded(value,lo,hi)*10)/10;
  syncPane();
}
function paneFromPointer(e){
  const rect=layout.getBoundingClientRect(),stacked=isStacked();
  const extent=stacked?rect.height:rect.width;
  const offset=stacked?e.clientY-rect.top:e.clientX-rect.left;
  changePaneSize(100*offset/Math.max(1,extent));
}
function initPaneControls(){
  try{
    const stored=JSON.parse(window.localStorage?.getItem(LAYOUT_KEY)||'null');
    if(Number.isFinite(stored?.side))pane.side=bounded(stored.side,20,80);
    if(Number.isFinite(stored?.stack))pane.stack=bounded(stored.stack,20,80);
  }catch{}
  syncPane();
  if(window.ResizeObserver){
    const observer=new window.ResizeObserver(refreshRailAfterResize);
    observer.observe($('ex-graph'));
  }
}
let resizingPointer=null;
splitter.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;
  resizingPointer=e.pointerId;
  layout.dataset.resizing='true';splitter.dataset.dragging='true';
  splitter.setPointerCapture?.(e.pointerId);paneFromPointer(e);e.preventDefault();
});
splitter.addEventListener('pointermove',e=>{
  if(resizingPointer===e.pointerId)paneFromPointer(e);
});
function finishPaneResize(e){
  if(resizingPointer!==e.pointerId)return;
  resizingPointer=null;delete layout.dataset.resizing;delete splitter.dataset.dragging;
  if(splitter.hasPointerCapture?.(e.pointerId))splitter.releasePointerCapture(e.pointerId);
  persistPane();refreshRailAfterResize();
}
splitter.addEventListener('pointerup',finishPaneResize);
splitter.addEventListener('pointercancel',finishPaneResize);
splitter.addEventListener('dblclick',()=>{
  if(isStacked())pane.stack=50;else pane.side=63;
  syncPane();persistPane();
});
splitter.addEventListener('keydown',e=>{
  if(e.altKey||e.ctrlKey||e.metaKey)return;
  const stacked=isStacked(),current=stacked?pane.stack:pane.side;
  let next=null;
  if(e.key==='Home')next=stacked?50:63;
  else if(e.key==='ArrowLeft'&&!stacked||e.key==='ArrowUp'&&stacked)next=current-(e.shiftKey?10:3);
  else if(e.key==='ArrowRight'&&!stacked||e.key==='ArrowDown'&&stacked)next=current+(e.shiftKey?10:3);
  if(next===null)return;
  e.preventDefault();changePaneSize(next);persistPane();
});
$('ex-fullscreen').onclick=async()=>{
  try{
    if(document.fullscreenElement===workspace)await document.exitFullscreen();
    else if(workspace.requestFullscreen)await workspace.requestFullscreen();
  }catch(e){console.warn('工作区全屏切换失败',e)}
};
function syncFullscreen(){
  const isFull=document.fullscreenElement===workspace;
  const button=$('ex-fullscreen');button.textContent=isFull?'⤢ 退出全屏':'⛶ 全屏';
  button.setAttribute('aria-pressed',String(isFull));refreshRailAfterResize();
}
document.addEventListener?.('fullscreenchange',syncFullscreen);
window.addEventListener('resize',()=>{syncPane();refreshRailAfterResize()});
$('ex-task').addEventListener('change',()=>{
  taskId=$('ex-task').value;syncLayoutDemoLink();selected=null;mode='all';collapsed.clear();
  searchRequestSerial++;clearTimeout(searchTimer);$('ex-results').replaceChildren();$('ex-results').hidden=true;
  $('ex-tip-panel').hidden=true;
  trail.length=0;trailIndex=-1;historySerial=-1;navSerial++;historyButtons();
  if(graph){graph.destroy();graph=null}
  const switched=location.pathname+'?observer_id='+encodeURIComponent(taskId);
  if(typeof history.pushState==='function')history.pushState({tmExplorerTask:taskId},'',switched);
  else history.replaceState(null,'',switched);
  load({fresh:true});
});
$('ex-back').onclick=()=>restoreView(-1);
$('ex-forward').onclick=()=>restoreView(1);
window.addEventListener('keydown',e=>{
  const tag=e.target?.tagName||'';
  if(e.altKey&&!e.ctrlKey&&!e.metaKey&&!['INPUT','TEXTAREA','SELECT'].includes(tag)){
    if(e.key==='ArrowLeft'){e.preventDefault();restoreView(-1)}
    else if(e.key==='ArrowRight'){e.preventDefault();restoreView(1)}
  }
});
$('ex-all').onclick=()=>setMode('all');
$('ex-local').onclick=()=>setMode('local');
$('ex-fit').onclick=fit;
$('ex-plus').onclick=()=>zoom(1.25);
$('ex-minus').onclick=()=>zoom(.8);
$('ex-collapse').onclick=toggleCollapse;
$('ex-expand-all').onclick=async()=>{if(!collapsed.size)return;rememberBefore();collapsed.clear();syncModeButtons();await updateSelectionWithoutLayout({focus:false});rememberAfter()};
$('ex-refresh').onclick=()=>{searchRequestSerial++;clearTimeout(searchTimer);$('ex-results').hidden=true;rememberBefore();load()};
window.addEventListener('popstate',event=>{
  const fromUrl=new URLSearchParams(location.search).get('observer_id');
  if(fromUrl&&fromUrl!==taskId&&Array.from($('ex-task').options||[]).some(o=>o.value===fromUrl)){
    taskId=fromUrl;$('ex-task').value=taskId;syncLayoutDemoLink();selected=null;collapsed.clear();
    trail.length=0;trailIndex=-1;historySerial=-1;navSerial++;historyButtons();
    if(graph){graph.destroy();graph=null}
    load();return;
  }
  const snapshot=event.state?.tmExplorerView;
  if(snapshot?.taskId===taskId){
    const i=event.state?.tmExplorerIndex;
    restoreSnapshot(snapshot,Number.isInteger(i)?i:null);
  }else{
    const target=decodeHash();
    if(target)navigate(target.kind,target.id,target.anchor,{record:false,focus:false});
  }
});
$('ex-query').addEventListener('input',()=>{
  const requestSerial=++searchRequestSerial;
  clearTimeout(searchTimer);
  const box=$('ex-results');
  if(!$('ex-query').value.trim()){box.replaceChildren();box.hidden=true;return}
  searchTimer=setTimeout(async()=>{
    const q=$('ex-query').value.trim();
    if(!q){box.replaceChildren();box.hidden=true;return}
    try{
      const payload=await api(base()+'/search?q='+encodeURIComponent(q));
      if(requestSerial!==searchRequestSerial||q!==$('ex-query').value.trim())return;
      box.replaceChildren();
      if(payload.partial){
        const notice=document.createElement('p');notice.className='ex-search-warning';
        notice.textContent='搜索索引受总读取预算限制，结果可能不完整；可缩小文档范围或刷新文件后重试。';
        box.append(notice);
      }
      const rows=payload.results||[];
      if(!rows.length){
        const empty=document.createElement('p');empty.className='ex-search-empty';
        empty.textContent=payload.partial?'已扫描内容中没有匹配项，但索引不完整，不能据此判断整个任务均无匹配。':'没有找到匹配的科研节点或 Markdown 文档。';
        box.append(empty);
      }
      for(const h of rows){
        const b=document.createElement('button');b.textContent=h.label+' ('+h.kind+')';
        const small=document.createElement('small');small.textContent=h.excerpt;
        b.append(small);b.onclick=()=>{navigate(h.kind,h.id,'',{focus:true});box.hidden=true};box.append(b);
      }
      box.hidden=false;
    }catch(e){
      if(requestSerial!==searchRequestSerial)return;
      box.textContent=e.message;box.hidden=false;
    }
  },250);
});
window.addEventListener('DOMContentLoaded',()=>{initPaneControls();load({fresh:true})});
})();
