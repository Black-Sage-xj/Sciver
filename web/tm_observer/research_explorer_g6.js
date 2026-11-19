/* Read-only G6 visualization: never read/write Obsidian node geometry. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const reader=$('ex-reader'), alertBox=$('ex-error'), tooltip=$('ex-tooltip');
const workspace=$('ex-workspace'), layout=$('ex-layout'), splitter=$('ex-splitter');
let mobileReadingRequested=false;
function showMobileGraph(){mobileReadingRequested=false;delete layout.dataset.mobileReading}
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
  if(!graphHost||document.querySelector?.('#ex-color-mode')||graphHost.querySelector?.('#ex-color-mode'))return;
  const button=document.createElement('button');
  button.id='ex-color-mode';button.type='button';button.className='ex-color-mode';
  const menu=document.querySelector?.('#ex-view-color');
  const toolbar=$('ex-zoom-percent')?.parentElement;
  if(!menu)button.style.cssText=(toolbar?'':'position:absolute;top:55px;left:12px;z-index:8;')+
    'padding:6px 10px;border:1px solid #596579;border-radius:7px;'+
    'font:600 12px system-ui;'+
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
  if(menu)menu.append(button);
  else if(toolbar?.insertBefore)toolbar.insertBefore(button,$('ex-zoom-percent'));
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
const vaultUi=globalThis.SciverVaultUI?.create({getTask:()=>taskId,
  getBranch:()=>selected?.kind==='node'&&/^H-\d+$/.test(selected.id)?selected.id:
    docOriginView?.taskId===taskId&&docOriginView.selected?.kind==='node'&&/^H-\d+$/.test(docOriginView.selected.id)?docOriginView.selected.id:null,
  getHypotheses:()=>index?.nodes.filter(node=>/^H-\d+$/.test(node.id))||[]});
let collapsed=new Set(), searchTimer=null, renderSerial=0;
let hoveredEdge=null,edgeMenuTimer=null;
let searchRequestSerial=0;
const base=()=>'/api/canvas-md/tasks/'+encodeURIComponent(taskId);
workspace.dataset.graphLayout='tree';
const uri=(kind,id)=>kind+':'+id;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
const api=async path=>{const r=await fetch(path,{cache:'no-store',credentials:'same-origin'}),v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v};
function showError(message){alertBox.textContent='图谱无法渲染：'+message;alertBox.hidden=false}
function knownType(n){return n.semantic?.type||'Unknown'}
function rank(n){const m=n.semantic||{},cycle=displayCycle(n);return [cycle??999999,m.sortable_time||'',ID(n.id)]}
function compareNodes(a,b){const x=rank(a),y=rank(b);return x[0]-y[0]||stable(x[1],y[1])||stable(x[2],y[2])}
function isResearchReference(e){
  return e.edge_type==='research_source'||e.type==='research_source'||e.lineage_role==='other';
}
function isScientificEdge(e){
  // Preserve all real edges, but reference/provenance edges do not advance a scientific branch.
  return !isResearchReference(e)&&!['external_reference','research_source'].includes(e.edge_type)&&
    !['external_reference','research_source'].includes(e.type);
}
function structure(nodes,edges){
  const lookup=new Map(nodes.map(n=>[n.id,n]));
  const realEdges=edges.filter(e=>lookup.has(e.fromNode)&&lookup.has(e.toNode));
  // Choose from the complete index, before local scope or folding. A view
  // change must not silently choose a different predecessor for the same node.
  const lineage=globalThis.SciverTreeLayout.projectForest(nodes,
    realEdges.filter(e=>!isResearchReference(e)).map(e=>({id:ID(e.id||e.fromNode+'->'+e.toNode),source:e.fromNode,
      target:e.toNode,data:{sourceEdge:e}}))).edges.map(e=>e.data.sourceEdge);
  // Only the chosen predecessor belongs on the tree. Other recorded sources
  // remain available in the reader through the unmodified index.
  const allEdges=lineage;
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
function visibility(s,ignoreCollapsed=false){
  const scope=new Set(s.lookup.keys()), indegree=new Map([...scope].map(id=>[id,0]));
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
  const style=palette[knownType(node||{})]||palette.Unknown;
  return lightGraph()?style.fill:style.line;
}
const lightGraph=()=>document.documentElement?.getAttribute('data-sciver-theme')==='white';
function edgeWidth(base,highlighted){
  // Keep branches readable in the full-tree overview (often 10–15% zoom).
  return Math.max(base,(highlighted?2.2:1.15)/safeZoom());
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
  caption.textContent=index.canvas==='science_graph.sqlite3'?'前沿':'研究末端';
  caption.title='按猜想去重；色块对应猜想上半部分，点击状态可查看节点';summary.append(caption);
  const statusTabs=[[null,'全部'],['active','活跃'],['parked','搁置'],['closed','已终止']];
  if(index.canvas==='science_graph.sqlite3')statusTabs.push(['unknown','未标记']);
  for(const [status,title] of statusTabs){
    const button=document.createElement('button');
    button.className='ex-tip-trigger';button.type='button';
    if(status)button.dataset.frontier=status;
    const count=status?counts[status]:records.length;
    button.textContent=title+' '+count;
    button.title=status?'查看'+title+'的全部末端节点；色块对应猜想上半部分':'打开全部研究末端，含当前视图未显示的分支';
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
  const legend=$('ex-type-legend');
  const expanded=!!legend.querySelector?.('details')?.open;
  legend.replaceChildren();
  const hNodes=index.nodes.filter(n=>knownType(n)==='Hypothesis');
  const counts={supported:0,rejected:0,open:0,mixed:0,legacy:0,unadjudicated:0,none:0};
  for(const node of hNodes)counts[hypothesisVerdict(node.semantic||{})]++;
  const labels={supported:'支持',rejected:'否决',open:'Open',none:'待裁决',
    mixed:'多轮结论不一',legacy:'历史归一',unadjudicated:'研究中止 · 未裁决'};
  const key=(host,label,color,count,title=label)=>{
    const item=document.createElement('span');item.className='ex-type-key';
    item.title=title+' · '+count+' 个节点';
    const dot=document.createElement('span');dot.className='ex-type-dot';
    dot.style.background=color;dot.style.borderColor='#cbd5e1';
    const text=document.createElement('span');text.textContent=label+' '+count;
    item.append(dot);item.append(text);host.append(item);
  };
  if(hNodes.length){
    const heading=document.createElement('span');heading.className='ex-overview-title';
    heading.textContent='Outcome';heading.title='猜想下半部分的裁决状态；按猜想去重';legend.append(heading);
    for(const state of ['supported','rejected','open','none'])if(counts[state])
      key(legend,labels[state],outcomeFill[state],counts[state],statusLabels[state]);
    const extra=['mixed','legacy','unadjudicated'].filter(state=>counts[state]);
    if(extra.length){
      const more=document.createElement('details');more.className='ex-legend-more';more.id='ex-outcome-more';
      more.open=expanded;
      const summary=document.createElement('summary');
      const count=extra.reduce((sum,state)=>sum+counts[state],0);
      summary.textContent='更多 '+count;
      summary.title=extra.map(state=>labels[state]+' '+counts[state]).join('；');
      summary.setAttribute('aria-label','其他 Outcome 状态，共 '+count+' 个猜想');
      const panel=document.createElement('div');panel.className='ex-legend-more-panel';
      for(const state of extra)key(panel,labels[state],outcomeFill[state],counts[state],statusLabels[state]);
      more.append(summary);more.append(panel);legend.append(more);
    }
  }
  const types=document.createElement('div');types.className='ex-overview-types';
  for(const type of ['Decision','Finding','StageSummary','External','Unknown']){
    const count=index.nodes.filter(n=>knownType(n)===type).length;
    if(count)key(types,typeNames[type]||type,(palette[type]||palette.Unknown).fill,count);
  }
  if(types.children.length)legend.append(types);
}
let tipWorkerRequest=0;
async function openTipPanel(filter=null){
  const panel=$('ex-tip-panel');
  if(!index)return;
  // Repeating the same filter closes the overlay, just like a document outline.
  if(!panel.hidden&&panel.dataset.filter===(filter||'all')&&!$('ex-branches')?.open){
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
  close.onclick=()=>{++tipWorkerRequest;panel.hidden=true;closeToolbarMenus(true)};head.append(close);panel.append(head);
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
    button.onclick=()=>{++tipWorkerRequest;panel.hidden=true;closeToolbarMenus();navigate('node',node.id,'',{focus:true})};
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
function readerVerdict(outcome){
  const kind=normalizedOutcomeKind(outcome);
  const labels={supported:'支持',rejected:'反驳',open:'尚无法判定',
    unadjudicated:'未裁决',legacy:'历史记录'};
  return '<span class="ex-verdict" data-verdict="'+esc(kind||'none')+'">'+
    esc(labels[kind]||'未标记')+'<small>'+esc(outcomeVerdictLabel(outcome))+'</small></span>';
}
function readerDocumentButton(ref,label){
  return ref?'<button type="button" class="ex-ref ex-document-link" data-ref="'+esc(uri('doc',ref))+'">'+
    esc(label)+' <span aria-hidden="true">↗</span></button>':'';
}
function outcomeDetailsHtml(outcomes){
  const records=Array.isArray(outcomes)?outcomes.filter(hasRecordedOutcome):[];
  const guideHtml=guide=>guide?'<section class="ex-outcome-guide">'+
    '<div class="ex-conclusion ex-md">'+mdHtml(guide.conclusion||'',null)+'</div>'+
    '<dl class="ex-guide-facts">'+[['basis','关键依据'],['scope','适用范围'],['open_question','未决问题']]
      .filter(([key])=>guide[key]).map(([key,label])=>'<div><dt>'+label+'</dt><dd class="ex-md">'+
        mdHtml(guide[key],null)+'</dd></div>').join('')+'</dl></section>':'';
  const detail=outcome=>'<details class="ex-cycle-result"'+(outcome===records[records.length-1]?' open':'')+
    '><summary><span class="ex-cycle-label">Cycle '+esc(outcome.cycle_id)+'</span>'+readerVerdict(outcome)+'</summary>'+
    '<div class="ex-result-body"><p class="ex-result-time">'+esc(outcomeTime(outcome))+'</p>'+
    (outcome.review_state==='unadjudicated'||outcome.verdict==='unadjudicated'?
      '<p class="ex-reader-notice">阶段性研究记录 · 执行未完成，尚无 Judge 科学裁决。</p>':'')+
    guideHtml(outcome.graph_summary)+
    (!outcome.graph_summary?'<p class="ex-legacy-preview">'+esc(String(outcome.summary||outcome.judge_rationale||'概览尚未记录。').slice(0,360))+
      (String(outcome.summary||'').length>360?'…':'')+'</p>':'')+
    '<div class="ex-reader-documents">'+
    '<button type="button" class="ex-ref ex-document-link" data-ref="'+esc(uri('report',outcome.cycle_id))+'">'+
    '阅读研究报告 <span aria-hidden="true">↗</span></button>'+
    '<button type="button" class="ex-ref" data-discuss-cycle="'+esc(outcome.cycle_id)+'">讨论这一轮</button></div></div></details>';
  return '<section class="ex-outcome-details" id="ex-outcome-details" aria-labelledby="ex-results-title">'+
    '<header class="ex-reader-section-head"><h3 id="ex-results-title">研究结论 <span class="ex-result-count">'+records.length+' 轮</span></h3></header>'+
    (records.length?records.map(detail).join(''):'<p class="ex-reader-empty">尚无本轮结论，等待研究与裁决。</p>')+'</section>';
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
function linkedNodeId(kind=selected?.kind,id=selected?.id){
  if(!index)return null;
  if(kind==='node')return index.nodes.some(n=>n.id===id)?id:null;
  if(kind==='report'){
    const node=index.nodes.find(n=>[...(n.semantic?.cycle_outcomes||[]),n.semantic?.outcome]
      .some(outcome=>outcome&&String(outcome.cycle_id)===String(id)))||
      index.nodes.find(n=>String(n.semantic?.cycle)===String(id));
    return node?.id||null;
  }
  if(kind==='doc')return index.nodes.find(n=>n.semantic?.dossier_ref===id||
    (index.references['node:'+n.id]||[]).some(ref=>ref.kind==='doc'&&ref.id===id))?.id||null;
  return null;
}
function g6Data(layoutBase=false){
  const s=structure(index.nodes,index.edges),vis=visibility(s,layoutBase);
  // Retain the whole base-mode layout. Only element visibility changes on fold.
  const rawEdges=s.allEdges.filter(e=>vis.visible.has(e.fromNode)&&vis.visible.has(e.toNode)&&
    (layoutBase||!(collapsed.has(e.fromNode)&&isScientificEdge(e))));
  const folded=visibility(s);
  // Stable ports are derived exclusively from the displayed lineage edges.
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
  const target=linkedNodeId();
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
      stroke:lightGraph()?(isTarget?'#1d4ed8':isEdgeEndpoint?'#92400e':
        isDownstream||isSource?'#0e7490':style.fill):isTarget?'#ffffff':isEdgeEndpoint?'#fde68a':isDownstream?'#a5f3fc':
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
    const isHovered=hoveredEdge===key;
    const highlighted=active||isIncoming||isDownstream||isHovered;
    const dim=!!(target||selectedEdge)&&!highlighted;
    const type=isScientificEdge(e);
    const bright=lightGraph()?(isHovered?'#1d4ed8':active?'#92400e':
      isIncoming?(traceColor.get(key)||'#0e7490'):isDownstream?'#0e7490':type?'#64748b':'#7c8594'):
      isHovered?'#7dd3fc':active?'#fef08a':isIncoming?(traceColor.get(key)||'#67e8f9'):
      isDownstream?'#67e8f9':type?'#e2e8f0':'#cbd5e1';
    const baseLineWidth=isHovered?6:active?5:isDownstream?4.4:isIncoming?4.1:dim?2.4:type?3.3:3;
    return {
      id:key,source:e.fromNode,target:e.toNode,
      data:{sourceEdge:e,baseLineWidth,highlighted},
      style:{sourcePort:'out:'+key,targetPort:'in:'+key,
        stroke:bright,
        lineWidth:edgeWidth(baseLineWidth,highlighted),
        lineDash:isResearchReference(e)?[6,4]:[],endArrow:true,
        opacity:highlighted?1:dim?(lightGraph()?.55:.28):1,
        zIndex:isHovered?24:active?20:isDownstream?12:isIncoming?11:1,
        router:{type:'orth',padding:18},
        shadowBlur:!lightGraph()&&highlighted?active?16:10:0,
        shadowColor:highlighted?bright:'transparent',
        cursor:'pointer',increasedLineWidthForHitTesting:12}
    };
  });
  globalThis.SciverTreeLayout.treeCoordinates(nodes,edges);
  return {nodes,edges};
}

// PDF-reader style "previous view" history: store only completed navigation actions.
// Free panning, pinching, hovering, scrolling are captured with the current view but
// NEVER create new history entries. State is private to this browser tab/task.
const trail=[];let trailIndex=-1,historySerial=-1,restoring=false,navSerial=0,docOriginView=null;
function captureView(){
  let camera=null;
  if(graphReady&&graph){
    try{
      const zoom=safeZoom(),position=graph.getPosition();
      if(Array.isArray(position)&&position.every(Number.isFinite))
        camera={zoom,position:[...position]};
    }catch{/* A disposed/reinitializing viewport has no camera snapshot. */}
  }
  return {taskId,selected:selected?{...selected}:null,collapsed:[...collapsed].sort(stable),
    camera,readerScroll:reader.scrollTop||0};
}
// Canonical reader location: browser Back/Forward and view history use one state.
function syncBrowserLocation(push=false){
  const hash=selected?'#'+encodeURIComponent(uri(selected.kind,selected.id)+
    (selected.anchor?'#'+selected.anchor:'')):'';
  const params=new URLSearchParams(location.search);
  params.delete('layout');params.delete('view');
  const query=params.toString();
  const url=location.pathname+(query?'?'+query:'')+hash;
  const state={tmExplorerView:captureView(),tmExplorerIndex:trailIndex};
  if(push&&typeof history.pushState==='function')history.pushState(state,'',url);
  else if(typeof history.replaceState==='function')history.replaceState(state,'',url);
}

function samePlace(a,b){
  return a.taskId===b.taskId&&
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
async function restoreSnapshot(snapshot,indexNext=null){
  if(!snapshot||snapshot.taskId!==taskId||restoring)return;
  restoring=true;navSerial++;
  try{
    collapsed=new Set(Array.isArray(snapshot.collapsed)?snapshot.collapsed:[]);
    selected=snapshot.selected?{...snapshot.selected}:null;
    if(selected?.kind){
      await navigate(selected.kind,selected.id,selected.anchor||'',{record:false,focus:false,reveal:false});
    }else{
      showMobileGraph();
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
      sem.title,sem.outcome?.summary,sem.outcome?.graph_summary,sem.research_transition]);};
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
    const transition=researchTransition(sem);
    const transitionCopy=transition&&(transition.current_change||transition.why_now);
    const status=hypothesisFrontier(sem),verdict=hypothesisVerdict(sem);
    const records=Array.isArray(sem.cycle_outcomes)?sem.cycle_outcomes.filter(hasRecordedOutcome):[];
    const outcome=records.length===1?records[0]:
      (!records.length&&hasRecordedOutcome(sem.outcome)?sem.outcome:null);
    const outcomeDescription=verdict==='mixed'?'多轮裁决结论不同，请查看各轮 Outcome':
      verdict==='legacy'?'历史兼容结论，不代表 Judge 明确判定 Open':
      verdict==='none'?'本猜想尚无可信 Judge 科学裁决':
      verdict==='unadjudicated'?'阶段性成果；预算拒绝续批，尚未完成 Judge 裁决。'+
        (outcome?.summary||''):
      (outcome?.graph_summary?.conclusion||outcome?.summary||outcome?.judge_rationale||'查看科学裁决详情');
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
        (part==='hypothesis'&&transitionCopy?' has-transition':'')+
        (part==='outcome'?' ex-outcome-verdict-'+verdict:'')+
        (selected?.kind==='node'&&selected.id===data.id&&
          (selected.anchor==='outcome'?'outcome':'hypothesis')===part?' is-selected':'');
      button.style.background=part==='hypothesis'?frontierFill[status]:outcomeFill[verdict];
      button.title=(part==='hypothesis'?'前沿 '+frontierSummary(sem)+'；查看猜想：':
        (verdict==='unadjudicated'?'科研保全状态 ':'科学裁决状态 ')+
        statusLabels[verdict]+'；查看 Outcome：')+description;
      if(part==='hypothesis'&&transitionCopy)button.title+='\n这一步改变什么：'+transitionCopy;
      if(part==='outcome'&&outcome?.graph_summary)
        button.title=Object.values(outcome.graph_summary).filter(Boolean).join('\n');
      button.setAttribute('aria-label',button.title);
      const heading=document.createElement('span');heading.className='ex-outcome-kicker';
      heading.textContent=title;
      const copy=document.createElement('span');copy.className='ex-outcome-copy';
      copy.textContent=description;
      button.append(heading);button.append(copy);
      if(part==='hypothesis'&&transitionCopy){
        const reason=document.createElement('span');reason.className='ex-outcome-transition';
        reason.textContent=abbreviate(transitionCopy,54);button.append(reason);
      }
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
const edgeMenu=$('ex-edge-menu'),edgeMenuAction=$('ex-edge-menu-action');
function repaintHoveredEdges(previous){
  if(!graphReady||!graph)return;
  const ids=new Set([previous,hoveredEdge]);
  const edges=g6Data(true).edges.filter(e=>ids.has(e.id));
  if(edges.length){graph.updateEdgeData(edges.map(({id,data,style})=>({id,data,style})));
    Promise.resolve(graph.draw()).catch(e=>showError(e.message));}
}
function closeEdgeMenu(){
  clearTimeout(edgeMenuTimer);
  const previous=hoveredEdge;hoveredEdge=null;edgeMenu.hidden=true;
  if(previous)repaintHoveredEdges(previous);
}
function deferEdgeMenuClose(){
  clearTimeout(edgeMenuTimer);edgeMenuTimer=setTimeout(closeEdgeMenu,200);
}
function refreshEdgeMenu(){
  if(!hoveredEdge||!graphReady||!graph)return;
  const edge=graph.getEdgeData().find(e=>e.id===hoveredEdge);
  if(!edge||edge.style.visibility==='hidden'){closeEdgeMenu();return;}
  const s=structure(index.nodes,index.edges),target=edge.target;
  const count=traverse([target],s.children).size-1,folded=collapsed.has(target);
  edgeMenu.dataset.edgeId=edge.id;edgeMenu.dataset.targetId=target;
  edgeMenuAction.textContent=folded?'+':'−';
  const actionLabel=(folded?'展开':'折叠')+'子分支（'+count+' 个后代节点）';
  edgeMenuAction.setAttribute('aria-label',actionLabel);
  const transition=researchTransition(s.lookup.get(target)?.semantic);
  edgeMenuAction.title=transition?'这一步改变什么：'+transition.current_change+
    '\n为什么现在研究：'+transition.why_now:actionLabel;
  edgeMenuAction.disabled=!count;
  edgeMenuAction.setAttribute('aria-expanded',String(!folded));
  edgeMenu.hidden=!count;
  positionEdgeMenu();
}
function positionEdgeMenu(){
  if(!hoveredEdge||edgeMenu.hidden||!graphReady||!graph)return;
  const edge=graph.getEdgeData().find(e=>e.id===hoveredEdge),node=edge&&graph.getNodeData(edge.target);
  if(!node)return;
  const [width,height]=node.style.size,center=graph.getElementPosition(node.id);
  const port=node.style.ports?.find(p=>p.key===edge.style.targetPort);
  const y=center[1]+height*((port?.placement?.[1]??.5)-.5);
  const point=graph.getViewportByCanvas([center[0]-width/2,y]);
  const box=$('ex-graph').getBoundingClientRect();
  // One fixed anchor on this child's incoming line; mouse motion never moves it.
  edgeMenu.style.left=Math.max(4,Math.min(box.width-28,point[0]-30))+'px';
  edgeMenu.style.top=Math.max(54,Math.min(box.height-28,point[1]-12))+'px';
}
function openEdgeMenu(event){
  const id=ID(event.target?.id),edge=graph?.getEdgeData().find(e=>e.id===id);
  if(!edge||edge.style.visibility==='hidden')return;
  if(isResearchReference(edge.data?.sourceEdge||{})){closeEdgeMenu();return;}
  clearTimeout(edgeMenuTimer);tooltip.hidden=true;
  const previous=hoveredEdge;hoveredEdge=id;
  refreshEdgeMenu();
  repaintHoveredEdges(previous);
}
edgeMenu.addEventListener('pointerenter',()=>clearTimeout(edgeMenuTimer));
edgeMenu.addEventListener('pointerleave',deferEdgeMenuClose);
edgeMenuAction.onclick=async event=>{
  event?.preventDefault?.();event?.stopPropagation?.();
  clearTimeout(edgeMenuTimer);
  if(edgeMenuAction.disabled||!hoveredEdge)return;
  await toggleNodeCollapse(edgeMenu.dataset.targetId);refreshEdgeMenu();
};
$('ex-graph').addEventListener('pointerdown',event=>{
  if(!event.target?.closest?.('#ex-edge-menu'))closeEdgeMenu();
});
window.addEventListener('keydown',event=>{if(event.key==='Escape')closeEdgeMenu()});
// Each predecessor index is EMBEDDED in its node's left 42px. The rows
// share the exact ordering and normalized Y position used by target G6 ports.
// No source Canvas geometry, floating source rail, or inferred relationship.
function updateSourceRail(){
  refreshEdgeMenu();
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
      const transition=researchTransition(lookup.get(n.id)?.semantic);
      if(transition)button.title+='\n这一步改变什么：'+transition.current_change+
        '\n为什么现在研究：'+transition.why_now;
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
  positionNodePulse();
}
let pulsedNode=null,pulseAnimation=null,pulseTimer;
let pulseRing=null;
function nodePulseRing(){
  if(pulseRing)return pulseRing;
  pulseRing=$('ex-node-pulse')||document.createElement('div');
  pulseRing.id='ex-node-pulse';pulseRing.hidden=true;pulseRing.setAttribute('aria-hidden','true');
  Object.assign(pulseRing.style,{position:'absolute',pointerEvents:'none',zIndex:5,
    boxSizing:'border-box',border:'3px solid #93c5fd',borderRadius:'14px',boxShadow:'0 0 22px #60a5fa'});
  $('ex-graph').append(pulseRing);
  return pulseRing;
}
function positionNodePulse(){
  if(!pulsedNode||!graphReady||!graph)return;
  const ring=nodePulseRing();
  const node=graph.getNodeData(pulsedNode);if(!node)return;
  const [x,y]=graph.getViewportByCanvas(graph.getElementPosition(pulsedNode));
  const [width,height]=node.style.size,zoom=safeZoom(),gap=5;
  Object.assign(ring.style,{left:(x-width*zoom/2-gap)+'px',top:(y-height*zoom/2-gap)+'px',
    width:(width*zoom+gap*2)+'px',height:(height*zoom+gap*2)+'px'});
}
function pulseNode(id){
  const ring=nodePulseRing();
  clearTimeout(pulseTimer);pulseAnimation?.cancel();
  pulsedNode=id;positionNodePulse();ring.hidden=false;
  const finish=()=>{ring.hidden=true;pulsedNode=null;pulseAnimation=null};
  const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if(ring.animate&&!reduced){
    pulseAnimation=ring.animate([{opacity:0},{opacity:1,offset:.15},
      {opacity:.3,offset:.45},{opacity:1,offset:.65},{opacity:0}],{duration:1400,easing:'ease-out'});
    pulseAnimation.onfinish=finish;
  }else pulseTimer=setTimeout(finish,1400);
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
  const edgeChanges=[];
  for(const e of graph.getEdgeData()){
    const lineWidth=edgeWidth(e.data.baseLineWidth,e.data.highlighted);
    if(e.style.lineWidth!==lineWidth)edgeChanges.push({id:e.id,style:{lineWidth}});
  }
  if(changes.length||edgeChanges.length){
    if(changes.length)graph.updateNodeData(changes);
    if(edgeChanges.length)graph.updateEdgeData(edgeChanges);
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
  graph.updateEdgeData(next.edges.map(({id,data,style})=>({id,data,style})));
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
  if(!graphReady||!graph||!event.target?.closest?.('.ex-outcome-card, .ex-node-source-strip, .ex-edge-menu'))return;
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
    // The selected viewer layout supplies coordinates; G6 must not override
    // them or move branches on selection.
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
      '<small>单击查看详情 · 双击放大并居中 · 悬停连线可操作子分支</small>';
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
  graph.on('aftertransform',()=>{updateZoomLabels();positionSourceRail();positionEdgeMenu()});
  graph.on('edge:pointerover',openEdgeMenu);
  graph.on('edge:pointermove',event=>{
    if(hoveredEdge!==ID(event.target?.id))openEdgeMenu(event);
    else{clearTimeout(edgeMenuTimer);positionEdgeMenu()}
  });
  graph.on('edge:pointerleave',deferEdgeMenuClose);
  graph.on('edge:click',e=>{
    const id=ID(e.target?.id),relation=index?.edges.find(x=>ID(x.id||x.fromNode+'->'+x.toNode)===id);
    if(!relation)return;
    const selectedNode=selected?.kind==='node'?selected.id:null;
    if(selectedNode){
      const visibleIds=visibleNodes();
      const visibleEdges=structure(index.nodes,index.edges).allEdges.filter(x=>visibleIds.has(x.fromNode)&&visibleIds.has(x.toNode)&&
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
  closeEdgeMenu();
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
async function showCompleteGraph(){
  showMobileGraph();
  rememberBefore();collapsed.clear();closeEdgeMenu();
  await updateSelectionWithoutLayout({focus:false});
  if(graph)await graph.fitView({when:'always',direction:'both'});rememberAfter(true);
}
async function toggleNodeCollapse(id){
  if(!index||!graphReady||!index.nodes.some(node=>node.id===id))return;
  rememberBefore();
  if(collapsed.has(id))collapsed.delete(id);else collapsed.add(id);
  await updateSelectionWithoutLayout({focus:false});rememberAfter();
}
function revealNode(id){
  const s=structure(index.nodes,index.edges);
  // Search/navigation never remains trapped behind a collapsed ancestor.
  const related=traverse([id],s.parents);
  for(const x of related)collapsed.delete(x);
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
  let protectedText=text.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|(\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g,(raw,code)=>{
    if(code)return code;
    const block=raw.startsWith('$$');math.push({value:raw.slice(block?2:1,block?-2:-1),block});
    return 'TMKATEXPLACEHOLDER'+(math.length-1)+'END';
  });
  protectedText=protectedText.replace(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|!\[\[([^\]\n]+)\]\]/g,(full,code,link)=>{
    if(code)return code;
    const [target,label]=link.split('|'),width=/^\d{1,4}$/.test(label||'')?Number(label):null;
    if(target.split('#')[0].toLowerCase().endsWith('.md'))return '[['+link+']]';
    return '<img src="'+esc(target)+'" alt="'+esc(width?target:(label||target))+
      '" data-vault-image="1"'+(width?' width="'+width+'"':'')+'>';
  });
  protectedText=protectedText.replace(/(?<!!)\[\[([^\]\n]+)\]\]/g,(full,link)=>{
    const label=link.split('|')[1]||link.split('|')[0],ref=resolve(link,source);
    return ref?'['+label.replace(/[\[\]]/g,'')+'](#tmref='+encodeURIComponent(ref.kind+':'+ref.id)+
      (ref.anchor?'%23'+encodeURIComponent(ref.anchor):'')+')':esc(label);
  });
  const result=window.DOMPurify.sanitize(window.marked.parse(protectedText));
  const doc=new DOMParser().parseFromString('<div id="parsed">'+result+'</div>','text/html');
  const wrap=doc.getElementById('parsed');
  if(window.Prism)wrap.querySelectorAll('pre code[class*="language-"]').forEach(code=>{
    window.Prism.highlightElement(code);
  });
  wrap.querySelectorAll('img').forEach(img=>{
    const raw=img.getAttribute('src')||'';
    if(!/^https?:\/\//i.test(raw)){
      if(!raw||/^[a-z][a-z\d+.-]*:/i.test(raw)||raw.startsWith('//')){img.remove();return}
      let target;try{target=decodeURIComponent(raw.split('#')[0])}catch{img.remove();return}
      img.setAttribute('src',base()+'/image?path='+encodeURIComponent(target)+
        '&source='+encodeURIComponent(source||'')+(img.dataset.vaultImage?'&vault=1':''));
    }
    img.removeAttribute('srcset');img.removeAttribute('sizes');
    img.setAttribute('loading','lazy');img.setAttribute('referrerpolicy','no-referrer');
  });
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
function withoutRepeatedTitle(body,title){
  const heading=/^(\s*(?:<!--[\s\S]*?-->\s*)*)# ([^\n]+)\n?/.exec(body);
  return heading&&heading[2].trim()===String(title).trim()?
    heading[1]+body.slice(heading[0].length):body;
}
function researchTransition(sem={}){
  const value=sem.research_transition;
  return ['theory-master/research-transition/v1','theory-master/research-transition/v2'].includes(value?.schema)&&
    ['prior_gap','current_change','why_now','goal_advance'].every(key=>typeof value[key]==='string')&&
    Array.isArray(value.sources)?value:null;
}
function transitionStatus(value){
  return ({supported:'支持',rejected:'反驳',open:'尚无法判定',none:'尚无整体裁决',
    unadjudicated:'未裁决'})[value]||'未标记';
}
function transitionOutcomeStatus(outcome){
  const originals=Array.isArray(outcome.original_verdicts)?outcome.original_verdicts.filter(Boolean):[];
  return originals.length?'历史裁决：'+originals.join('/') : transitionStatus(outcome.verdict);
}
function transitionSourcesHtml(sources){
  const ordered=[...sources].sort((a,b)=>(a.role==='main'?0:1)-(b.role==='main'?0:1));
  const link=source=>{
    const id=String(source.hypothesis_id||''),linked=index.nodes.find(n=>n.id===id);
    const title=source.title||linked?.semantic?.title||linked?.label||id;
    return linked?'<button type="button" class="ex-ref ex-transition-source-link" title="'+esc(title)+'" aria-label="'+esc(id+' · '+title)+'" data-ref="'+
      esc(uri('node',id))+'">'+esc(id)+'</button>':'<span title="'+esc(title)+'">'+esc(id)+'</span>';
  };
  const group=(items,role,label)=>items.length?'<span class="ex-source-group" data-source-role="'+role+'"><span>'+label+'</span>'+items.map(link).join('')+'</span>':'';
  const main=ordered.filter(s=>s.role==='main'),others=ordered.filter(s=>s.role!=='main');
  return ordered.length?'<nav class="ex-transition-sources" aria-label="前序研究来源">'+
    group(main,'main','主来源')+group(others,'other',main.length?'参考来源':'前序来源')+'</nav>':'';
}
function transitionSourceNotesHtml(sources){
  const ordered=[...sources].sort((a,b)=>(a.role==='main'?0:1)-(b.role==='main'?0:1));
  const notes=ordered.map(source=>{
    const id=String(source.hypothesis_id||''),linked=index.nodes.find(n=>n.id===id);
    const summary=linked?.semantic?.hypothesis_summary;
    const claim=typeof summary==='string'?(/^主张：([\s\S]+)；条件：[\s\S]+；状态：[^\n]+。$/.exec(summary)?.[1]||summary):'';
    const records=(source.outcome_refs||[]).filter(outcome=>String(outcome.cycle_id||'').trim());
    return '<div class="ex-source-note" data-source-role="'+(source.role==='main'?'main':'other')+'">'+
      '<h5>'+esc(id)+' <span>'+esc(source.title||linked?.label||'')+'</span></h5>'+
      '<dl><div><dt>前序研究问题 <small>当前来源记录</small></dt><dd class="ex-md"'+(claim?' data-discussion-node="'+esc(id)+'"':'')+'>'+
      (claim?mdHtml(claim,null):'<span class="ex-reader-empty">未记录问题概述，可点击来源查看原文。</span>')+'</dd></div>'+
      (source.contribution?'<div><dt>本步承接</dt><dd class="ex-transition-contribution ex-md">'+mdHtml(source.contribution,null)+'</dd></div>':'')+'</dl>'+
      (records.length?'<div class="ex-transition-results" aria-label="来源的检验记录">'+
        records.map(outcome=>'<button type="button" class="ex-ref" data-ref="'+esc(uri('report',outcome.cycle_id))+'">'+
          'Cycle '+esc(outcome.cycle_id)+' · '+esc(transitionOutcomeStatus(outcome))+'</button>').join('')+'</div>':'')+'</div>';
  }).join('');
  const missing=ordered.filter(s=>!s.contribution).map(s=>s.hypothesis_id);
  return notes+(missing.length?'<p class="ex-source-notes-missing">'+esc(missing.join('、'))+' 未记录逐来源的承接说明；本步的总体变化见下方。</p>':'');
}
function researchTransitionHtml(node){
  const transition=researchTransition(node.semantic);
  const sources=transition?.sources.length?transition.sources:
    index.edges.filter(edge=>edge.toNode===node.id&&
      (isScientificEdge(edge)||isResearchReference(edge)))
      .map(edge=>({edge,source:index.nodes.find(source=>source.id===edge.fromNode)}))
      .filter(({source})=>source&&['Hypothesis','Decision'].includes(knownType(source)))
      .filter(({source},position,items)=>items.findIndex(item=>item.source.id===source.id)===position)
      .map(({edge,source})=>({hypothesis_id:source.id,title:source.semantic?.title||source.label,
        role:edge.lineage_role==='main'?'main':isResearchReference(edge)?'other':undefined}));
  // A source-free initial hypothesis has no predecessor rationale to display.
  if(!sources.length)return '';
  let content;
  if(transition){
    const fields=[['prior_gap','前序留下的问题'],
      ['current_change','这一步改变什么'],['why_now','为什么现在研究']];
    content=transitionSourcesHtml(sources)+transitionSourceNotesHtml(sources)+
      '<dl class="ex-transition-facts">'+fields.map(([key,label])=>'<div><dt>'+label+
        '</dt><dd class="ex-md">'+mdHtml(transition[key],null)+'</dd></div>').join('')+'</dl>'+
      (transition.goal_advance?'<div class="ex-transition-goal"><h5>对研究目标的推进</h5><div class="ex-md">'+
        mdHtml(transition.goal_advance,null)+'</div></div>':'');
  }else{
    content='<p class="ex-reader-empty">承接理由未记录。</p>'+transitionSourcesHtml(sources);
  }
  return '<section class="ex-research-transition" aria-label="为什么研究到这里">'+
    '<h4>为什么研究到这里</h4>'+content+'</section>';
}
function hypothesisReaderHtml(node,header,timeLine,key){
  const sem=node.semantic||{};
  const summary=sem.hypothesis_summary;
  // Only the SDK's explicit claim/conditions format is split; legacy prose is
  // kept intact. The raw Markdown and scientific records remain unchanged.
  const parts=typeof summary==='string'&&/^主张：([\s\S]+)；条件：([\s\S]+)；状态：[^\n]+。$/.exec(summary);
  const body=parts?'<dl class="ex-hypothesis-facts"><div><dt>主张</dt><dd class="ex-md">'+
    mdHtml(parts[1],null)+'</dd></div><div><dt>适用条件</dt><dd class="ex-md">'+
    mdHtml(parts[2],null)+'</dd></div></dl>':
    '<div class="ex-md">'+mdHtml(typeof summary==='string'?summary:
      withoutRepeatedTitle(header.body,node.label),null)+'</div>';
  const frontier=hypothesisFrontier(sem);
  const branchLabels={active:'继续研究',parked:'暂缓研究',closed:'研究已收束',unknown:'未标记'};
  const branchReasons=(sem.frontiers||[]).filter(f=>f.reason);
  const whole=sem.whole_h_assessment;
  const assessment=['supported','rejected','open'].includes(sem.scientific_status)&&
    sem.legacy_identity_review?.status!=='unadjudicated_whole_h'&&whole&&hasRecordedOutcome(whole)?
    '<div class="ex-whole-assessment">整体猜想裁决 '+readerVerdict(whole)+'</div>':
    '';
  const dossier=sem.dossier_ref?readerDocumentButton(sem.dossier_ref,'查看完整猜想文档'):
    '<p class="ex-reader-empty">暂未关联可读取的猜想文档。</p>';
  const outcomes=sem.cycle_outcomes?.length?sem.cycle_outcomes:
    (sem.outcome&&String(sem.outcome.cycle_id??'').trim()?[sem.outcome]:[]);
  return '<section class="ex-hypothesis-details" id="ex-hypothesis-details" aria-labelledby="ex-hypothesis-title">'+
    '<header class="ex-reader-section-head">'+(Number.isInteger(sem.cycle)&&sem.cycle>=0?'<p class="ex-reader-cycle">Cycle '+esc(sem.cycle)+'</p>':'')+'<h3 id="ex-hypothesis-title">猜想</h3></header>'+
    '<div class="ex-hypothesis-heading"><h2 class="ex-hypothesis-title">'+esc(node.label)+'</h2>'+timeLine+'</div>'+researchTransitionHtml(node)+body+
    '<div class="ex-reader-documents">'+dossier+'</div></section>'+
    outcomeDetailsHtml(outcomes)+
    '<details class="ex-reader-context"><summary>研究分支 · '+esc(branchLabels[frontier])+'</summary>'+
    '<p class="muted">记录后续研究安排与收束原因。</p>'+
    (branchReasons.length?branchReasons.map(f=>'<p><strong>'+esc(branchLabels[f.status]||f.status)+
      '</strong> · '+esc(f.reason)+'</p>').join(''):'<p class="muted">暂无分支原因记录。</p>')+'</details>'+
    '<details class="ex-reader-context ex-reader-sources"><summary>节点信息与文档引用</summary>'+assessment+nodeHeaderDisclosure(header.fields)+
    (index.references[key]?.length?outbound(key):'')+(index.backlinks[key]?.length?refsBlock(key):'')+'</details>';
}
let readingSourceVersion='',discussionRequest='',discussionStatusTarget=null;
// Keep interactive reader styling with the script served on each page load.
const readingStyles=`
.ex-hypothesis-details,.ex-outcome-details{min-width:0;margin:0 0 22px}
.ex-outcome-details{border-top:1px solid var(--read-border);padding-top:24px;margin-top:30px}
.ex-reader .ex-reader-section-head{display:block;margin:0 0 16px}
.ex-reader .ex-reader-section-head h3{font-size:22px;line-height:1.4;font-weight:700;color:var(--read-text)}
.ex-reader .ex-reader-cycle{margin:0 0 5px;font-size:12px;font-weight:650;color:var(--read-blue);font-variant-numeric:tabular-nums}
.ex-hypothesis-heading{border-left:3px solid var(--read-blue);padding:2px 0 2px 14px;margin-bottom:24px}
.ex-reader .ex-hypothesis-heading .ex-hypothesis-title{font-size:24px;line-height:1.45;margin:0 0 10px;color:var(--read-text);font-weight:700}
html[data-sciver-theme] .ex-reader .ex-hypothesis-heading .ex-node-time{margin:0}
.ex-outcome-details .ex-cycle-result:first-of-type{border-top:0;margin-top:0;padding-top:0}
.ex-reader .ex-research-transition{padding:0;border:0;border-radius:0;background:transparent;margin-bottom:24px}
.ex-transition-sources{display:flex;gap:18px;align-items:baseline;overflow-x:auto;white-space:nowrap;margin:0 0 20px;padding:0 0 4px;font-size:12px;scrollbar-width:thin}
.ex-source-group{display:inline-flex;gap:9px;align-items:baseline}
[data-source-role="main"]{--source-color:var(--read-blue)}
[data-source-role="other"]{--source-color:var(--read-teal)}
.ex-source-group{color:var(--source-color)}
.ex-source-group>span:first-child{font-size:11px;color:var(--read-muted)}
html[data-sciver-theme] .ex-reader .ex-source-group .ex-transition-source-link{margin:0;padding:0;border:0;border-radius:0;background:transparent;color:inherit;font:inherit;text-decoration:underline;text-underline-offset:3px;white-space:nowrap}
.ex-source-note{margin:0 0 20px}
.ex-reader .ex-source-note h5{margin:0 0 9px;font-size:12px;color:var(--source-color);line-height:1.6}
.ex-source-note h5 span{font-weight:500;color:var(--read-muted)}
.ex-source-note dl,.ex-source-note dd{margin:0}
.ex-source-note dl>div+div{margin-top:10px}
.ex-source-note dt,.ex-reader .ex-transition-goal h5{margin:0 0 5px;font-size:12px;font-weight:650;color:var(--read-muted)}
.ex-source-note dt small{margin-left:6px;font-size:10px;font-weight:400}
html[data-sciver-theme] .ex-reader .ex-source-note .ex-md{font-size:13px;line-height:1.75}
.ex-source-note .ex-transition-results{margin-top:7px}
.ex-source-notes-missing{font-size:11px;line-height:1.7;color:var(--read-muted);margin:0 0 20px}
.ex-reader .ex-transition-goal{margin-top:16px;color:var(--read-text)}
.ex-selection-popover{position:absolute;z-index:1000;max-width:min(280px,calc(100% - 16px));padding:5px;border:1px solid var(--read-border);border-radius:9px;background:var(--read-card);color:var(--read-text);box-shadow:0 4px 18px #0005}
.ex-selection-popover[hidden]{display:none}
.ex-selection-popover button{border:0;border-radius:5px;padding:6px 9px;background:transparent;color:inherit;cursor:pointer;font:inherit}
.ex-selection-popover button:hover,.ex-selection-popover button:focus-visible{background:var(--read-soft)}
.ex-selection-popover p{margin:2px 9px 5px;font-size:12px;line-height:1.4}
.ex-selection-popover p:empty{display:none}
@media(max-width:600px){.ex-reader .ex-hypothesis-heading .ex-hypothesis-title{font-size:21px}.ex-transition-sources{gap:14px}}
`;
function discussionTools(){
  return '<style data-reader-interactions>'+readingStyles+'</style>'+
    '<div class="ex-selection-popover" data-selection-popover hidden><button type="button" data-discuss-selection>引用选中文字讨论</button>'+
    '<p data-selection-status role="status"></p></div>';
}
function discussReading(source,statusTarget){
  discussionRequest=String(Date.now())+Math.random();
  const status=statusTarget||reader.querySelector('[data-discussion-status]');
  discussionStatusTarget=status;
  if(status)status.textContent='正在核验讨论来源…';
  window.dispatchEvent(new CustomEvent('sciver-chat-reference',{detail:{task_id:taskId,request_id:discussionRequest,source}}));
}
window.addEventListener('sciver-chat-reference-result',event=>{
  if(event.detail?.request_id!==discussionRequest)return;
  const status=discussionStatusTarget;
  if(status)status.textContent=event.detail.error||'来源已加入聊天草稿；可以继续输入问题。';
});
async function navigate(kind,id,anchor='',options={}){
  if(!index)return;
  discussionStatusTarget=null;
  if(kind==='node'&&!index.nodes.some(n=>n.id===id))return;
  if(kind==='edge'&&!index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===id))return;
  if(['doc','report'].includes(kind))mobileReadingRequested=true;
  else showMobileGraph();
  const request=++navSerial;
  if(['doc','report'].includes(kind)&&!['doc','report'].includes(selected?.kind))docOriginView=captureView();
  if(options.record!==false)rememberBefore();
  selected={kind,id,anchor};
  readingSourceVersion='';
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
    reader.innerHTML=knownType(node)==='Hypothesis'?hypothesisReaderHtml(node,header,timeLine,key):
      '<h2>'+esc(node.label)+'</h2>'+timeLine+
      nodeHeaderDisclosure(header.fields)+estimatedNote+
      '<div class="ex-md">'+mdHtml(withoutRepeatedTitle(header.body,node.label),null)+'</div>'+
      outbound(key)+refsBlock(key);
    if(options.reveal!==false)revealNode(id);
    await updateSelectionWithoutLayout({focus:options.zoomNode?false:options.focus!==false,
      animateFocus:options.animateFocus===true});
    if(request!==navSerial)return;
    if(options.zoomNode){
      await zoomIntoNode(id);
      if(request!==navSerial)return;
    }
    const linked=(index.references[key]||[]).filter(r=>r.kind==='doc'&&r.id!==m.dossier_ref)
      .sort((a,b)=>Number(b.id.startsWith('nodes/'))-Number(a.id.startsWith('nodes/')))[0];
    if(linked&&knownType(node)!=='Hypothesis'){
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
    await updateSelectionWithoutLayout();
  }else if(kind==='doc'){
    try{
      const doc=await api(base()+'/document?path='+encodeURIComponent(id));
      if(request!==navSerial||selected?.kind!==kind||selected.id!==id)return;
      if(doc.report_cycle)return navigate('report',doc.report_cycle,anchor,{...options,record:false});
      readingSourceVersion=doc.source_sha256||'';
      reader.innerHTML='<button class="ex-ref" type="button" data-doc-back="1">← 返回图谱位置</button><h2 class="ex-document-title">'+esc(doc.title)+'</h2><div class="ex-meta">Markdown · '+
        esc(doc.id)+'</div>'+(vaultUi?.documentTools(doc.id)||'')+'<div class="ex-md" data-doc-content="1">'+mdHtml(withoutRepeatedTitle(doc.content,doc.title),doc.id)+'</div>'+
        (doc.next_offset!==null&&doc.next_offset!==undefined?
          '<button class="ex-ref" data-doc-more="'+doc.next_offset+'">加载后续 Markdown 内容 ↓</button>':'')+
        outbound(key)+refsBlock(key);
      await updateSelectionWithoutLayout();
    }catch(e){reader.textContent='无法加载：'+e.message}
  }else if(kind==='report'){
    try{
      const doc=await api(base()+'/report?cycle='+encodeURIComponent(id)+'&section='+encodeURIComponent(anchor));
      if(request!==navSerial||selected?.kind!==kind||selected.id!==id)return;
      readingSourceVersion=doc.source_sha256||'';
      reader.innerHTML='<button class="ex-ref" type="button" data-doc-back="1">← 返回图谱位置</button>'+
        '<p class="ex-report-kicker">Cycle '+esc(id)+' · '+(doc.adjudication_status==='unadjudicated'?'阶段性研究报告':'研究报告')+'</p>'+
        '<h2 class="ex-document-title">'+esc(doc.title)+'</h2>'+
        (vaultUi?.documentTools(doc.id)||'')+
        (anchor?'<button class="ex-ref" data-ref="'+esc(uri('report',id))+'">查看完整报告</button>':'')+
        '<details class="ex-report-toc"><summary>报告目录 · 按章节阅读</summary><nav aria-label="报告章节">'+
        (doc.sections||[]).filter(s=>s.level===2).map(s=>'<button class="ex-ref" data-ref="'+
          esc(uri('report',id)+'#'+s.id)+'">'+esc(s.title)+'</button>').join('')+'</nav></details>'+
        '<div class="ex-md" data-doc-content="1">'+mdHtml(withoutRepeatedTitle(doc.content,doc.title),doc.id)+'</div>'+
        (doc.next_offset!==null&&doc.next_offset!==undefined?'<button class="ex-ref" data-report-more="'+doc.next_offset+'">加载后续报告内容 ↓</button>':'');
      await updateSelectionWithoutLayout();
    }catch(e){reader.textContent='无法加载研究报告：'+e.message}
  }
  if(request!==navSerial)return;
  if(['node','doc','report'].includes(kind))reader.insertAdjacentHTML('afterbegin',discussionTools());
  if(options.highlight){
    const nodeId=linkedNodeId(kind,id);
    if(nodeId){
      if(kind!=='node'){
        revealNode(nodeId);
        await updateSelectionWithoutLayout();
        if(request!==navSerial)return;
        await graph.focusElement(nodeId,{duration:360,easing:'ease-in-out'});
        if(request!==navSerial)return;
      }
      positionSourceRail();pulseNode(nodeId);
    }
  }
  if(['doc','report'].includes(kind)&&mobileReadingRequested)layout.dataset.mobileReading='true';
  reader.scrollTop=0;
  if(anchor){
    const outcomeTarget=anchor==='outcome'&&kind==='node'?
      reader.querySelector('.ex-outcome-details'):null;
    if(outcomeTarget)outcomeTarget.scrollIntoView({block:'start'});
    else if(anchor!=='outcome'){
      const candidates=reader.querySelectorAll('.ex-md h1,.ex-md h2,.ex-md h3,.ex-md h4,.ex-md h5,.ex-md h6,.ex-hypothesis-title,.ex-document-title');
      const target=Array.from(candidates).find(h=>h.id===anchor||h.textContent.trim()===anchor||
        h.textContent.trim().startsWith(anchor));
      if(target)target.scrollIntoView({block:'start'});
    }
  }
  if(options.record!==false)rememberAfter(Boolean(options.recordCamera));
  else if(!restoring)syncBrowserLocation(false);
}
function showReadingSelection(){
  const popover=reader.querySelector('[data-selection-popover]');
  if(!popover)return;
  const selection=window.getSelection();
  if(!selection||selection.isCollapsed||!selection.rangeCount||!selection.toString().trim()||
      !reader.contains(selection.anchorNode)||!reader.contains(selection.focusNode)){
    popover.hidden=true;return;
  }
  const range=selection.getRangeAt(0),bounds=reader.getBoundingClientRect();
  const rects=Array.from(range.getClientRects()).filter(r=>r.width&&r.height&&
    r.bottom>bounds.top&&r.top<bounds.bottom&&r.right>bounds.left&&r.left<bounds.right);
  if(!rects.length){popover.hidden=true;return;}
  const rect=rects[0];
  popover.hidden=false;
  const status=popover.querySelector('[data-selection-status]');
  if(status)status.textContent='';
  const size=popover.getBoundingClientRect();
  const left=Math.max(bounds.left+8,Math.min(rect.right-size.width,bounds.right-size.width-8,window.innerWidth-size.width-8));
  const above=rect.top-size.height-8;
  const top=Math.max(bounds.top+8,above>=bounds.top?above:Math.min(rect.bottom+8,bounds.bottom-size.height-8));
  popover.style.left=(left-bounds.left-reader.clientLeft+reader.scrollLeft)+'px';
  popover.style.top=(top-bounds.top-reader.clientTop+reader.scrollTop)+'px';
}
function readingSelectionSource(selection){
  const scope=node=>(node?.nodeType===1?node:node?.parentElement)?.closest('[data-discussion-node]');
  const start=scope(selection?.anchorNode),end=scope(selection?.focusNode);
  return start===end?{id:start?.dataset.discussionNode||''}:null;
}
reader.addEventListener('pointerup',e=>{if(!e.target.closest('[data-selection-popover]'))requestAnimationFrame(showReadingSelection);});
reader.addEventListener('keyup',e=>{if(!e.target.closest('[data-selection-popover]'))requestAnimationFrame(showReadingSelection);});
reader.addEventListener('scroll',()=>{const popover=reader.querySelector('[data-selection-popover]');if(popover)popover.hidden=true;});
document.addEventListener('selectionchange',()=>{
  const selection=window.getSelection(),popover=reader.querySelector('[data-selection-popover]');
  if(popover&&(!selection||selection.isCollapsed||!reader.contains(selection.anchorNode)||!reader.contains(selection.focusNode)))popover.hidden=true;
});
reader.addEventListener('pointerdown',e=>{
  if(e.target.closest('[data-selection-popover] button'))e.preventDefault();
});
reader.addEventListener('click',async e=>{
  const cycle=e.target.closest('[data-discuss-cycle]');
  if(cycle){discussReading({kind:'report',id:cycle.dataset.discussCycle,section:'',quote:''});return;}
  if(e.target.closest('[data-discuss-selection]')){
    if(!selected||!['node','doc','report'].includes(selected.kind))return;
    const source={kind:selected.kind,id:selected.id,section:selected.kind==='node'?'':selected.anchor||'',quote:''};
    if(readingSourceVersion)source.source_version=readingSourceVersion;
    {
      const selection=window.getSelection();
      const quote=selection?.toString().trim()||'';
      const status=reader.querySelector('[data-selection-status]');
      if(!quote||!reader.contains(selection.anchorNode)||!reader.contains(selection.focusNode)){
        if(status)status.textContent='请先选中当前正文中的文字。';return;
      }
      const scope=readingSelectionSource(selection);
      if(!scope){if(status)status.textContent='请在同一来源的正文内选择文字。';return;}
      if(scope.id){source.kind='node';source.id=scope.id;source.section='';delete source.source_version;}
      if(quote.length>4000){if(status)status.textContent='每次最多引用 4000 字，请缩短选段。';return;}
      source.quote=quote;
    }
    discussReading(source,reader.querySelector('[data-selection-status]'));return;
  }
  if(e.target.closest('[data-doc-back]')){
    showMobileGraph();
    const origin=docOriginView;
    if(origin?.taskId===taskId&&origin.selected?.kind){
      await navigate(origin.selected.kind,origin.selected.id,origin.selected.anchor||'',{focus:false,reveal:false});
      if(origin.camera&&graph){
        await graph.zoomTo(origin.camera.zoom,false);
        await graph.translateTo(origin.camera.position,false);
        updateZoomLabels();updateSourceRail();
      }
      reader.scrollTop=origin.readerScroll||0;
    }else if(trailIndex>0)await restoreView(-1);
    else{
      const related=(index.backlinks[uri('doc',selected?.id)]||[]).find(ref=>ref.kind==='node');
      const nodeId=related?.id||index.nodes[0]?.id;
      if(nodeId)await navigate('node',nodeId,'',{focus:false});
    }
    return;
  }
  const reportMore=e.target.closest('[data-report-more]');
  if(reportMore&&selected?.kind==='report'){
    const target={...selected},offset=Number(reportMore.dataset.reportMore);
    reportMore.disabled=true;
    try{
      const page=await api(base()+'/report?cycle='+encodeURIComponent(target.id)+'&section='+encodeURIComponent(target.anchor||'')+'&offset='+offset);
      if(selected?.kind!=='report'||selected.id!==target.id||selected.anchor!==target.anchor)return;
      reader.querySelector('[data-doc-content]')?.insertAdjacentHTML('beforeend',mdHtml(page.content,page.id));
      if(page.next_offset===null||page.next_offset===undefined)reportMore.remove();
      else{reportMore.dataset.reportMore=String(page.next_offset);reportMore.disabled=false}
    }catch(err){reportMore.disabled=false;reportMore.textContent='读取失败，点击重试：'+err.message}
    return;
  }
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
function decodeHash(value=location.hash){
  let hash;try{hash=decodeURIComponent(value.slice(1))}catch{return null}
  const i=hash.indexOf(':');if(i<1)return null;
  const h=hash.indexOf('#');return {kind:hash.slice(0,i),id:hash.slice(i+1,h<0?undefined:h),
    anchor:h<0?'':hash.slice(h+1)};
}
let pendingChatTarget=null;
window.addEventListener('sciver-explorer-navigate',event=>{
  let url;try{url=new URL(event.detail.href)}catch{return}
  const target=decodeHash(url.hash);
  if(!target||!['node','edge','doc','report'].includes(target.kind))return;
  event.preventDefault();
  if(!index){pendingChatTarget=target;return}
  navigate(target.kind,target.id,target.anchor,{focus:true,animateFocus:true,highlight:true})
    .catch(error=>showError(error.message));
});
window.addEventListener('hashchange',()=>{
  const target=decodeHash();
  if(!index||!target||selected&&target.kind===selected.kind&&target.id===selected.id&&target.anchor===(selected.anchor||''))return;
  navigate(target.kind,target.id,target.anchor,{record:false,focus:false});
});
async function load({fresh=false}={}){
  vaultUi?.warm();
  try{
    const newIndex=await api(base()+'/index'),previous=index;
    index=newIndex;
    overviewRender();
    ++tipWorkerRequest;$('ex-tip-panel').hidden=true;
    $('ex-stats').textContent=index.stats.node_count+' 节点 · '+
      index.stats.edge_count+' 科研连线 · '+index.stats.doc_count+' 文档';
    $('ex-index-warning').hidden=!index.stats.partial;
    const hash=pendingChatTarget||decodeHash();pendingChatTarget=null;
    const hashValid=hash&&((hash.kind==='node'&&index.nodes.some(n=>n.id===hash.id))||
      (hash.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===hash.id))||
      ['doc','report'].includes(hash.kind));
    const saved=history.state?.tmExplorerView;
    if(fresh&&!hashValid){
      // A new direct visit or a task switch has no explicit document target.
      // Old hashless camera snapshots must not override the default landing.
      selected=null;collapsed.clear();showMobileGraph();
      await renderGraph({fit:true});
      if(trailIndex<0)rememberAfter();
    }else if(fresh&&hashValid){
      selected=null;collapsed.clear();
      await navigate(hash.kind,hash.id,hash.anchor,{record:false,focus:true,highlight:true});
    }else if(saved?.taskId===taskId&&(!saved.selected||
      (saved.selected.kind==='node'&&index.nodes.some(n=>n.id===saved.selected.id))||
      (saved.selected.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===saved.selected.id))||
      ['doc','report'].includes(saved.selected.kind))){
      await restoreSnapshot(saved,history.state?.tmExplorerIndex);
    }else if(hashValid){
      await navigate(hash.kind,hash.id,hash.anchor,{record:false,focus:false});
    }else if(selected?.kind==='node'&&index.nodes.some(n=>n.id===selected.id)){
      await navigate('node',selected.id,selected.anchor||'',{record:false,focus:false});
    }else if(selected?.kind==='edge'&&index.edges.some(e=>ID(e.id||e.fromNode+'->'+e.toNode)===selected.id)){
      await navigate('edge',selected.id,'',{record:false,focus:false});
    }else{
      selected=null;showMobileGraph();
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
  const button=$('ex-fullscreen');button.textContent=isFull?'⤢':'⛶';
  button.setAttribute('aria-label',isFull?'退出全屏':'全屏');
  button.title=isFull?'退出全屏（Esc）':'全屏（Esc 退出）';
  button.setAttribute('aria-pressed',String(isFull));refreshRailAfterResize();
}
document.addEventListener?.('fullscreenchange',syncFullscreen);
window.addEventListener('resize',()=>{syncPane();refreshRailAfterResize()});
$('ex-task').addEventListener('change',()=>{
  taskId=$('ex-task').value;selected=null;collapsed.clear();showMobileGraph();
  vaultUi?.reset();reader.innerHTML='<p class="muted">选择左侧科研节点以阅读完整内容。</p>';
  searchRequestSerial++;clearTimeout(searchTimer);$('ex-results').replaceChildren();$('ex-results').hidden=true;
  $('ex-tip-panel').hidden=true;closeToolbarMenus();
  trail.length=0;trailIndex=-1;historySerial=-1;navSerial++;historyButtons();
  if(graph){graph.destroy();graph=null}
  const switched=location.pathname+'?observer_id='+encodeURIComponent(taskId);
  if(typeof history.pushState==='function')history.pushState({tmExplorerTask:taskId},'',switched);
  else history.replaceState(null,'',switched);
  load({fresh:true});
});
// Tool panels overlay the workspace without shrinking or moving the graph.
const toolbarMenus=Array.from(document.querySelectorAll?.('.ex-toolbar-menu')||[]);
function closeToolbarMenus(restoreFocus=false,except=null){
  for(const menu of toolbarMenus){
    if(menu===except||!menu.open)continue;
    menu.open=false;
    if(menu.id==='ex-branches'){++tipWorkerRequest;$('ex-tip-panel').hidden=true}
    if(restoreFocus)menu.querySelector('summary')?.focus();
  }
}
for(const menu of toolbarMenus){
  menu.addEventListener('toggle',()=>{
    if(!menu.open){
      if(menu.id==='ex-branches'){++tipWorkerRequest;$('ex-tip-panel').hidden=true}
      return;
    }
    closeToolbarMenus(false,menu);
    if(menu.id==='ex-branches'){
      $('ex-tip-panel').hidden=true;openTipPanel(null);
    }
  });
}
document.addEventListener?.('pointerdown',event=>{
  if(!event.target.closest?.('.ex-toolbar-menu'))closeToolbarMenus();
  if(!event.target.closest?.('.ex-search-field,#ex-results'))$('ex-results').hidden=true;
});
$('ex-back').onclick=()=>restoreView(-1);
$('ex-forward').onclick=()=>restoreView(1);
window.addEventListener('keydown',e=>{
  const tag=e.target?.tagName||'';
  const editing=['INPUT','TEXTAREA','SELECT'].includes(tag)||e.target?.isContentEditable;
  if(e.key==='/'&&!editing&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!document.querySelector?.('dialog[open]')){
    e.preventDefault();closeToolbarMenus();$('ex-query').focus();return;
  }
  if(e.key==='Escape'){
    closeToolbarMenus(true);$('ex-results').hidden=true;
  }
  if(e.altKey&&!e.ctrlKey&&!e.metaKey&&!['INPUT','TEXTAREA','SELECT'].includes(tag)){
    if(e.key==='ArrowLeft'){e.preventDefault();restoreView(-1)}
    else if(e.key==='ArrowRight'){e.preventDefault();restoreView(1)}
  }
});
$('ex-all').onclick=showCompleteGraph;
$('ex-fit').onclick=fit;
$('ex-plus').onclick=()=>zoom(1.25);
$('ex-minus').onclick=()=>zoom(.8);
$('ex-expand-all').onclick=async()=>{if(!collapsed.size)return;rememberBefore();collapsed.clear();await updateSelectionWithoutLayout({focus:false});rememberAfter()};
window.addEventListener('popstate',event=>{
  const fromUrl=new URLSearchParams(location.search).get('observer_id');
  if(fromUrl&&fromUrl!==taskId&&Array.from($('ex-task').options||[]).some(o=>o.value===fromUrl)){
    taskId=fromUrl;$('ex-task').value=taskId;
    vaultUi?.reset();reader.innerHTML='<p class="muted">选择左侧科研节点以阅读完整内容。</p>';
    selected=null;collapsed.clear();showMobileGraph();
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
      const number=/^\d+$/.test(q)?q.replace(/^0+/,'')||'0':null;
      const direct=number===null?[]:(index?.nodes||[]).filter(n=>{
        const match=String(n.id).match(/(?:^|[-_])0*(\d+)$/);
        return match&&(match[1].replace(/^0+/,'')||'0')===number;
      }).sort((a,b)=>stable(a.id,b.id));
      const directIds=new Set(direct.map(n=>n.id));
      function section(title,rows,emptyMessage){
        const group=document.createElement('div');group.className='ex-search-section';
        const heading=document.createElement('div');heading.className='ex-search-section-title';
        heading.textContent=title;group.append(heading);
        if(!rows.length){
          const empty=document.createElement('p');empty.className='ex-search-empty';
          empty.textContent=emptyMessage;group.append(empty);
        }
        for(const h of rows){
          const b=document.createElement('button');b.textContent=h.label+' ('+h.kind+')';
        const small=document.createElement('small');small.textContent=h.excerpt;
          b.append(small);b.onclick=()=>{navigate(h.kind,h.id,'',{focus:true});box.hidden=true};group.append(b);
        }
        box.append(group);
      }
      if(number!==null)section('节点编号',direct.map(n=>({kind:'node',id:n.id,
        label:n.id+' · '+(n.label||n.id),excerpt:''})),
        '当前图谱没有该编号的节点。');
      const rows=(payload.results||[]).filter(h=>h.kind!=='node'||!directIds.has(h.id));
      section('内容匹配',rows,payload.partial?
        '已扫描内容中没有其他匹配项；搜索索引尚未覆盖全部文档。':
        '没有找到其他匹配的科研节点或 Markdown 文档。');
      if(payload.partial){
        const notice=document.createElement('p');notice.className='ex-search-warning';
        notice.textContent='搜索索引受总读取预算限制，结果可能不完整；可缩小文档范围后重试。';
        box.append(notice);
      }
      box.hidden=false;
    }catch(e){
      if(requestSerial!==searchRequestSerial)return;
      box.textContent=e.message;box.hidden=false;
    }
  },250);
});
window.addEventListener('DOMContentLoaded',()=>{initPaneControls();load({fresh:true})});
if(typeof MutationObserver!=='undefined'&&document.documentElement){
  new MutationObserver(()=>{
    if(graphReady)updateSelectionWithoutLayout().catch(err=>showError(err.message));
  }).observe(document.documentElement,{attributes:true,attributeFilter:['data-sciver-theme']});
}
})();
