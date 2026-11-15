"""Read-only research graph regression tests: metadata, deterministic view, DAG collapse."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tm_observer.graph_semantics import project_node
from tm_observer.research_explorer_g6 import graph_content

SCRIPT = Path(__file__).resolve().parents[1] / "tm_observer" / "research_explorer_g6.js"

HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const nodes = [
  {id:'A',label:'root A',x:9999,y:-444,width:1200,height:500,
    semantic:{type:'Hypothesis',frontier_status:null,title:'A hypothesis'},text:'# A'},
  {id:'B',label:'root B',x:-9999,y:120,width:1,height:1,
    semantic:{type:'Decision',cycle:150,frontier_status:'parked',title:'B decision'},text:'# B'},
  {id:'C',label:'shared finding',semantic:{type:'Finding',title:'Shared finding'},
    text:'type: Finding\nid: C\ntimestamp: 09-08-19:21\n\n# Shared finding\n\nThis body discusses type: Finding, and must not be hidden.'},
  {id:'D',label:'decision',semantic:{type:'Decision',frontier_status:'active',verdict:'open',cycle:3,title:'D'},text:'# D'},
  {id:'X',label:'private child',semantic:{type:'Finding',frontier_status:'closed',title:'Only A'},text:'# X'}
];
const edges = [
 {id:'AC',fromNode:'A',toNode:'C'}, {id:'BC',fromNode:'B',toNode:'C'},
 {id:'CD',fromNode:'C',toNode:'D'}, {id:'AX',fromNode:'A',toNode:'X'}
];
const index={nodes,edges,aliases:{},references:{},backlinks:{},stats:{node_count:5,edge_count:4,doc_count:0}};
const elements={};
const elem=id=>(elements[id]??=( {
  id,value:'0000000000000001',textContent:'',innerHTML:'',hidden:false,style:{},dataset:{},listeners:{},children:[],scrollTop:0,offsetHeight:125,
  addEventListener(name,fn){this.listeners[name]=fn},
  setAttribute(name,v){this[name]=String(v)},
  getBoundingClientRect(){return {left:0,top:0,width:800,height:500}},
  querySelectorAll(){return []},
  querySelector(){return null},
  replaceChildren(){this.children=[]},
  append(child){this.children.push(child)},
}));
let onReady,latestGraph,onPopState;
const docListeners={};
const storage=new Map();
class FakeGraph {
  constructor(options){this.options=options;this.events={};this.data={nodes:[],edges:[]};this.camera={zoom:1,position:[0,0]};this.draws=0;this.renders=0;latestGraph=this}
  on(name,cb){this.events[name]=cb}
  setData(data){this.data=data}
  setLayout(layout){this.layout=layout}
  getNodeData(id){return id?this.data.nodes.find(n=>n.id===id):this.data.nodes}
  getEdgeData(){return this.data.edges}
  updateNodeData(data){for(const n of data){const old=this.getNodeData(n.id);if(old)old.style={...old.style,...n.style}}}
  updateEdgeData(data){for(const e of data){const old=this.data.edges.find(x=>x.id===e.id);if(old)old.style={...old.style,...e.style}}}
  async setElementVisibility(changes,animate){
    this.visibilityCalls=(this.visibilityCalls||0)+1;
    assert.equal(animate,false,'fold visibility must be instantaneous');
    for(const [id,value] of Object.entries(changes)){
      const target=this.getNodeData(id)||this.data.edges.find(e=>e.id===id);
      assert(target,'cannot hide an unregistered graph element '+id);
      target.style.visibility=value;
    }
  }
  async draw(){this.draws++}
  async render(){
    // Match G6 5.1.1: viewport is not yet created when render begins.
    this.events.aftertransform?.();
    this.renders++;
  }
  async fitView(){
    this.fitCalls=(this.fitCalls||0)+1;
    if(Number.isFinite(this.nextFitZoom)){
      this.camera.zoom=this.nextFitZoom;this.nextFitZoom=null;
      this.events.aftertransform?.();
    }
  }
  async focusElement(id,animation){this.focused=id;this.focusAnimation=animation;this.camera.position=[50,75]}
  getZoom(){
    if(!this.renders)throw new TypeError("undefined is not an object (evaluating 'this.context.viewport.getZoom')");
    return this.camera.zoom;
  }
  getPosition(){
    if(!this.renders)throw new TypeError('viewport not initialized');
    return this.camera.position;
  }
  async zoomTo(z,animation){this.camera.zoom=z;this.zoomAnimation=animation}
  async translateTo(pos){this.camera.position=[...pos]}
  async translateBy(offset){this.camera.position=this.camera.position.map((v,i)=>v+offset[i]);this.events.aftertransform?.()}
  resize(width,height){this.lastResize=[width,height];this.resizeCalls=(this.resizeCalls||0)+1}
  getElementPosition(id){const map={A:[-300,0],B:[-300,140],C:[0,0],D:[280,0],X:[0,140]};return map[id]||[0,0]}
  getViewportByCanvas(p){return [p[0]+350,p[1]+250]}
  zoomBy(factor,animation,origin){this.zoomOrigin=origin;this.camera.zoom*=factor;this.events.aftertransform?.()}
  destroy(){}
}
const location={hash:'',pathname:'/research-explorer/',search:'?observer_id=0000000000000001'};
let pageIndex=0;
const pageHistory=[{state:null,url:location.pathname+location.search}];
function setAddress(url){
  const next=new URL(url,'http://localhost');
  location.pathname=next.pathname;location.search=next.search;location.hash=next.hash;
}
const history={
  get state(){return pageHistory[pageIndex].state},
  pushState(state,_title,url){
    pageHistory.splice(pageIndex+1);
    pageHistory.push({state,url});
    pageIndex=pageHistory.length-1;setAddress(url);
  },
  replaceState(state,_title,url){
    pageHistory[pageIndex]={state,url};setAddress(url);
  },
  go(delta){
    const next=pageIndex+delta;
    if(next<0||next>=pageHistory.length)return;
    pageIndex=next;setAddress(pageHistory[pageIndex].url);
    if(onPopState)onPopState({state:this.state});
  }
};
const ctx={
  document:{getElementById:elem,fullscreenElement:null,
    addEventListener(name,fn){docListeners[name]=fn},
    async exitFullscreen(){this.fullscreenElement=null;docListeners.fullscreenchange?.()},
    createElement:tag=>({tagName:tag.toUpperCase(),style:{},dataset:{},children:[],append(child){this.children.push(child)},setAttribute(name,value){this[name]=value}})},
  window:{G6:{Graph:FakeGraph},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    addEventListener(name,fn){if(name==='DOMContentLoaded')onReady=fn;if(name==='popstate')onPopState=fn}},
  location, history,
  URLSearchParams, URL,
  fetch:async url=>({ok:true,json:async()=>
    url.endsWith('/workers')?{workers:[
      {worker_id:'worker-2',node_id:'D',cycle_id:'3',association:'frozen'},
      {worker_id:'worker-5',node_id:'D',cycle_id:'4',association:'claimed'},
      {worker_id:'worker-3',node_id:'X',cycle_id:'9',association:'frozen'}
    ]}:index}),
  setTimeout,clearTimeout,console
};
vm.runInNewContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]),'research_explorer_layout_c.js'),'utf8'),ctx);
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
  elements['ex-workspace']=elem('ex-workspace');
  elements['ex-workspace'].requestFullscreen=async()=>{
    ctx.document.fullscreenElement=elements['ex-workspace'];docListeners.fullscreenchange?.();
  };
  // Old saved "stack" choices must not override the new default side-by-side layout.
  storage.set('tm-explorer-pane-layout-v1',JSON.stringify({direction:'stack',side:63,stack:50}));
  onReady();await tick();
  // Pane sizing changes CSS geometry only: G6's layout, document selection and
  // navigation history must survive resizes and fullscreen toggles.
  assert.equal(elements['ex-layout'].dataset.layout,'side');
  assert.equal(elements['ex-splitter']['aria-orientation'],'vertical');
  const initialRenders=latestGraph.renders;
  assert.equal(latestGraph.fitCalls,1,'fresh entry fits the full tree');
  assert.equal(latestGraph.data.nodes.length,5,
    'full graph includes the otherwise filtered historical branch');
  assert.equal(latestGraph.focused,undefined,
    'legacy-only fixture centers the newest trusted Cycle, not the rightmost arbitrary node');
  assert.equal(history.state?.tmExplorerView?.selected,null,
    'landing center never silently selects a node or forges a deep link');
  assert.equal(elements['ex-zoom-percent'].textContent,'100%',
    '100% is the native fixed-size node scale, not relative to graph bounds');
  elements['ex-plus'].onclick();
  assert.equal(elements['ex-zoom-percent'].textContent,'125%',
    'plus refreshes the absolute zoom label');
  elements['ex-minus'].onclick();
  assert.equal(elements['ex-zoom-percent'].textContent,'100%',
    'inverse minus returns to the same physical scale');
  latestGraph.nextFitZoom=.375;
  await elements['ex-fit'].onclick();
  assert.equal(elements['ex-zoom-percent'].textContent,'38%',
    'fit-to-view reports its true scale rather than redefining it to 100%');
  latestGraph.camera.zoom=1;
  latestGraph.events.aftertransform();
  assert.equal(elements['ex-zoom-percent'].textContent,'100%',
    'native G6 zoom 1 remains 100% after a fit');
  assert.equal(elements['ex-error'].hidden,true,
    'G6 first render must not read viewport.getZoom before viewport exists');
  assert(latestGraph.getNodeData().length>0,'nodes render even if viewport is absent during startup');
  const initialSelected=history.state?.tmExplorerView?.selected?.id;
  const split=elements['ex-splitter'];
  split.listeners.pointerdown({button:0,pointerId:1,clientX:350,clientY:300,preventDefault(){}});
  split.listeners.pointermove({pointerId:1,clientX:380,clientY:325});
  split.listeners.pointerup({pointerId:1});
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.deepEqual([...latestGraph.lastResize],[800,500],
    'splitter resize explicitly sizes G6 to its current grid cell');
  assert.equal(elements['ex-layout'].style['--ex-side-size'],'47.5%');
  assert(storage.get('tm-explorer-pane-layout-v1').includes('"side":47.5'));
  split.listeners.keydown({key:'ArrowLeft',shiftKey:false,preventDefault(){}});
  assert.equal(elements['ex-layout'].style['--ex-side-size'],'44.5%');
  split.listeners.dblclick();
  assert.equal(elements['ex-layout'].style['--ex-side-size'],'63%');
  split.listeners.keydown({key:'ArrowLeft',shiftKey:false,preventDefault(){}});
  assert.equal(elements['ex-layout'].style['--ex-side-size'],'60%');
  assert.equal(elements['ex-splitter']['aria-orientation'],'vertical');
  const callsBefore=latestGraph.resizeCalls;
  elements['ex-g6'].getBoundingClientRect=()=>({left:0,top:0,width:600,height:500});
  split.listeners.pointerdown({button:0,pointerId:2,clientX:420,clientY:320,preventDefault(){}});
  split.listeners.pointermove({pointerId:2,clientX:430,clientY:320});
  split.listeners.pointerup({pointerId:2});
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.deepEqual([...latestGraph.lastResize],[600,500],
    'G6 canvas must shed its stale previous size when only divider moves');
  assert.equal(latestGraph.resizeCalls,callsBefore+1,
    'a coalesced layout change triggers exactly one canvas resize');
  await elements['ex-fullscreen'].onclick();
  assert.equal(elements['ex-fullscreen']['aria-pressed'],'true');
  await elements['ex-fullscreen'].onclick();
  assert.equal(elements['ex-fullscreen']['aria-pressed'],'false');
  assert.equal(latestGraph.renders,initialRenders,'pane resize must not re-layout the research graph');
  assert.equal(history.state?.tmExplorerView?.selected?.id,initialSelected,'resize preserves selected node');

  assert.equal(latestGraph.data.edges.length,3,'one natural predecessor per node');
  assert(!latestGraph.data.edges.some(e=>e.id==='BC'));
  const initialPositions=JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y]));
  const initialCamera=JSON.stringify(latestGraph.camera),readerHtml=elements['ex-reader'].innerHTML;
  const line=id=>latestGraph.data.edges.find(e=>e.id===id);
  const baseColor=line('AC').style.stroke;
  const hover=id=>latestGraph.events['edge:pointerover']({target:{id},originalEvent:{clientX:400,clientY:200}});
  const menu=elements['ex-edge-menu'],action=elements['ex-edge-menu-action'];
  hover('AC');await tick();
  assert.equal(menu.hidden,false);
  assert.equal(menu.dataset.targetId,'C','edge chooses its child subtree rather than source A');
  assert.equal(line('AC').style.stroke,'#7dd3fc');
  assert.equal(line('AC').style.lineWidth,6);
  assert.equal(line('AX').style.stroke,baseColor,'sibling line is not hover-highlighted');
  assert.equal(action.textContent,'−');
  const fixedPosition=JSON.stringify([menu.style.left,menu.style.top]);
  latestGraph.events['edge:pointermove']({target:{id:'AC'},originalEvent:{clientX:700,clientY:450}});
  assert.equal(JSON.stringify([menu.style.left,menu.style.top]),fixedPosition,'circle is anchored to the child edge, not the mouse');
  await action.onclick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'hidden');
  assert.equal(latestGraph.getNodeData('C').style.visibility,'visible','branch root remains to reopen the edge menu');
  assert.equal(latestGraph.getNodeData('X').style.visibility,'visible','sibling branch remains open');
  assert.equal(line('AC').style.visibility,'visible','collapsed branch incoming edge is retained');
  assert.equal(action.textContent,'+');
  assert.equal(JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y])),initialPositions);
  assert.equal(JSON.stringify(latestGraph.camera),initialCamera);
  assert.equal(elements['ex-reader'].innerHTML,readerHtml);
  assert.equal(history.state.tmExplorerView.selected,null,'folding never selects or navigates');
  latestGraph.events['edge:pointerleave']();
  menu.listeners.pointerenter();await new Promise(r=>setTimeout(r,230));
  assert.equal(menu.hidden,false,'moving from the line into its menu keeps it open');
  await action.onclick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'visible');
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'hidden');
  elements['ex-forward'].onclick();await tick();await tick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'visible');
  hover('AX');assert.equal(action.disabled,true,'leaf has no descendants to fold');
  assert.equal(line('AC').style.stroke,baseColor,'previous edge highlight is restored');
  latestGraph.events['edge:pointerleave']();await new Promise(r=>setTimeout(r,230));
  assert.equal(menu.hidden,true);
  assert.equal(line('AX').style.stroke,baseColor);
  hover('AC');await action.onclick();
  const fits=latestGraph.fitCalls;
  await elements['ex-all'].onclick();
  assert(latestGraph.data.nodes.every(n=>n.style.visibility==='visible'));
  assert.equal(latestGraph.fitCalls,fits+1,'complete graph expands and fits the entire tree');
  assert.equal(menu.hidden,true);
  assert(latestGraph.renders>=initialRenders,'history may restore a complete graph render');
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('Full')||elements['ex-reader'].innerHTML.includes('Shared finding'));
  assert(elements['ex-reader'].innerHTML.includes('ex-node-tags'));
  const strip=elements['ex-source-rail'].children.find(n=>n.dataset.nodeId==='C');
  assert.equal(strip.children.length,1);
  assert.equal(strip.children[0].dataset.sourceId,'A');
  assert.equal(history.state.tmExplorerView.selected.id,'C');
  assert(location.hash.includes('node%3AC'));
  const oldRender=latestGraph.renders;
  latestGraph.events['node:dblclick']({target:{id:'C'}});await tick();await tick();
  assert.equal(latestGraph.focused,'C');assert.equal(latestGraph.renders,oldRender);
  // H/Outcome cards, verdict classifications and semantic zoom stay shared.
  nodes[0].semantic={type:'Hypothesis',cycle:1,frontier_status:'active',
    cycle_outcomes:[{verdict:'supported',cycle_id:'1',summary:'Supported result'}],
    outcome:{verdict:'supported',cycle_id:'1',summary:'Supported result'}};
  await elements['ex-refresh'].onclick();await tick();await tick();
  let initialCard=elements['ex-outcome-cards'].children.find(n=>n.dataset.nodeId==='A');
  assert.equal(initialCard.children.length,2);
  assert(initialCard.children[0].children[0].textContent.includes('Active'));
  assert(initialCard.children[1].children[0].textContent.includes('Supported'));
  initialCard.children[1].onclick({preventDefault(){},stopPropagation(){}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.anchor,'outcome');
  latestGraph.camera.zoom=.4;latestGraph.events.aftertransform();
  assert.equal(elements['ex-compact-labels'].hidden,false);
  assert.equal(elements['ex-source-rail'].hidden,true);
  const initialToggle=elements['ex-graph'].children.find(x=>x.id==='ex-color-mode');
  assert(initialToggle);initialToggle.onclick();
  assert.equal(latestGraph.getNodeData('A').style.fill,'#207A62');
  latestGraph.camera.zoom=1;latestGraph.events.aftertransform();
  assert.equal(elements['ex-compact-labels'].hidden,true);
  assert.equal(elements['ex-source-rail'].hidden,false);
  initialToggle.onclick();
  nodes[0].semantic={type:'Hypothesis',frontier_status:null,title:'A hypothesis'};
  nodes.push({id:'E',label:'E',semantic:{type:'External',title:'E'},text:'# E'});
  // One H with an Outcome remains ONE graph vertex, with two separate
  // clickable sections. These clicks preserve the deep link and history.
  nodes.push({id:'H-0008',label:'人工猜想',
    text:'type: Hypothesis\nid: H-0008\nscientific_status: open\ncreated_cycle: 87\nfrontier_status: active\n\n# 人工猜想',
    semantic:{type:'Hypothesis',cycle:87,title:'物理条件下的定量规律',
      source_timestamp:'10-07-22:43',registered_at:'2026-10-18T10:00:00+00:00',
      outcome:{cycle_id:87,verdict:'supported',summary:'该数学关系已经通过检验。',
        source_timestamp:'2026-10-08T00:45:12+08:00'}}});
  // The database inserts this empty open Outcome row when a new H is created.
  // It must NOT cause a lower clickable region or an Outcome heading.
  nodes.push({id:'H-0009',label:'尚未裁决的猜想',text:'# 尚未裁决的猜想',
    semantic:{type:'Hypothesis',cycle:88,title:'尚未裁决的猜想',scientific_status:'open',
      outcome:{id:'H-0009-outcome',hypothesis_id:'H-0009',cycle_id:'',
        verdict:'open',summary:'',decisive_test:'',judge_rationale:'',
        report_ref:null,findings:[],open_questions:[],boundary:[],evidence_refs:[]}}});
  nodes.push({id:'H-0010',label:'已有 open 裁决的猜想',text:'# 已有 open 裁决的猜想',
    semantic:{type:'Hypothesis',cycle:90,title:'已有 open 裁决的猜想',
      outcome:{cycle_id:'90',verdict:'open',summary:''}}});
  edges.push({id:'DH8',fromNode:'D',toNode:'H-0008'},
    {id:'H8H9',fromNode:'H-0008',toNode:'H-0009'},
    {id:'H9H10',fromNode:'H-0009',toNode:'H-0010'});
  await elements['ex-refresh'].onclick();await tick();
  const h=latestGraph.getNodeData('H-0008');
  assert(h,'Outcome does not replace H');
  assert.equal(latestGraph.getNodeData().filter(x=>x.id==='H-0008').length,1);
  assert.equal(h.style.labelText,'','G6 label must not overlap split interactive regions');
  assert.equal(h.style.size[1],198,'H with Outcome has two readable halves');
  const overlays=elements['ex-outcome-cards'].children;
  const unjudged=latestGraph.getNodeData('H-0009');
  assert.equal(unjudged.style.size[1],154,'unjudged H retains its geometry');
  assert.equal(unjudged.style.labelText,'','G6 text does not duplicate HTML sections');
  const unjudgedCard=overlays.find(x=>x.dataset.nodeId==='H-0009');
  assert(unjudgedCard,'unjudged H still has both visual regions');
  assert(unjudgedCard.children[0].children[0].textContent.includes('C88 · H'));
  assert(unjudgedCard.children[1].children[0].textContent.includes('尚未裁决'),
    'default open compatibility row never becomes a Judge judgment');
  assert.equal(unjudgedCard.children[1].style.background,'#374151');
  assert(overlays.some(x=>x.dataset.nodeId==='A'),
    'unadjudicated H also has an Outcome placeholder region');
  assert(overlays.some(x=>x.dataset.nodeId==='H-0010'),
    'recorded open judgment with a Cycle still counts as Outcome');
  let card=overlays.find(x=>x.dataset.nodeId==='H-0008');
  assert(card,'H with Outcome must have HTML overlay');
  assert.equal(card.children.length,2,'two distinct clickable buttons');
  assert.equal(card.children[0].children[0].textContent,'C87 · H · 未标记',
    'upper H region displays frontier status');
  assert(card.children[1].children[0].textContent.includes('Outcome · Supported'));
  assert.equal(card.children[1].style.background,'#207A62',
    'lower Outcome section independently conveys the Judge verdict');
  assert.equal(card.children[0].children.length,2,'upper H card has title and summary only');
  assert.equal(card.children[1].children.length,2,'lower Outcome card has title and summary only');
  assert(!card.children.some(button=>button.children.some(part=>part.className==='ex-outcome-time')),
    'no timestamps remain in graph nodes');
  assert(!card.children[0].children[0].textContent.includes('H-0008'));
  assert.equal(card.dataset.stripWidth,'42','predecessor index stays separate');
  assert.equal(card.style.width,'302px','overlay does not cover 42px incoming rail');
  const splitRenderCount=latestGraph.renders;
  let scrolled=0;
  elements['ex-reader'].querySelector=selector=>selector==='.ex-outcome-details'?
    {scrollIntoView(){scrolled++}}:null;
  const persistentOutcomeButton=card.children[1];
  persistentOutcomeButton.onclick({preventDefault(){},stopPropagation(){}});
  await tick();await tick();
  const selectedOverlay=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0008');
  assert.equal(selectedOverlay.children[1],persistentOutcomeButton,
    'single-click must keep the same DOM target so a second click can become dblclick');
  assert.equal(history.state.tmExplorerView.selected.id,'H-0008');
  assert.equal(history.state.tmExplorerView.selected.anchor,'outcome');
  assert(location.hash.includes('outcome'),'Outcome has a directly linkable target');
  assert(scrolled>0,'Outcome click scrolls to final H judgment');
  assert(elements['ex-reader'].innerHTML.includes('ex-outcome-details'));
  const hReader=elements['ex-reader'].innerHTML;
  assert(hReader.includes('<h2>'),'selected H remains readable by title');
  assert(hReader.includes('<div class="ex-node-time">H 时间 · 10-07-22:43</div>'),
    'true original H time is in the RIGHT reader, not in graph card');
  assert(hReader.includes('裁决时间 · 2026-10-08 00:45+08:00'),
    'Outcome source time is inside right-side Cycle Outcome details');
  assert(!hReader.includes('Canvas 节点 · H-0008'),
    'duplicate top metadata line with id/status/type is removed');
  assert(hReader.includes('<details class="ex-node-tags"><summary>节点标签 '),
    'H metadata is grouped under collapsed disclosure');
  assert(hReader.includes('节点标签 <span class="ex-node-tags-count">(5)</span>'),
    'all five source fields remain accessible inside collapsed H tags');
  assert(!hReader.includes('<details class="ex-node-tags" open'),
    'node labels are collapsed by default for Hypothesis too');
  assert.equal(latestGraph.renders,splitRenderCount,'Outcome selection never reruns DAG layout');
  card=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0008');
  assert(card.children[1].className.includes('is-selected'),'clicked region is highlighted');
  card.children[0].onclick({preventDefault(){},stopPropagation(){}});
  await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.anchor,'');
  assert.equal(elements['ex-reader'].scrollTop,0,'H click selects top section');
  card=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0008');
  assert(card.children[0].className.includes('is-selected'));
  latestGraph.camera.zoom=1.3;
  latestGraph.events.aftertransform();
  assert.equal(card.style.transform,'scale(1.3)','HTML regions track G6 pan/zoom');
  card.children[0].ondblclick({preventDefault(){},stopPropagation(){}});
  await tick();await tick();
  assert.equal(latestGraph.camera.zoom,2.08,'double-click on upper H panel also enlarges node');
  assert.equal(latestGraph.focused,'H-0008','upper panel double click centers the same H');
  assert.equal(latestGraph.renders,splitRenderCount,'overlay zoom does not rerun Dagre');
  persistentOutcomeButton.ondblclick({preventDefault(){},stopPropagation(){}});
  await tick();await tick();
  assert.equal(latestGraph.camera.zoom,2.4,'Outcome lower half double-click zooms further');
  assert.equal(history.state.tmExplorerView.selected.anchor,'outcome',
    'Outcome double-click preserves its separate detail target');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='DH8').target,'H-0008');
  // Overview labels use real Cycle metadata; unknown and external vertices
  // retain explicit type letters instead of blank cards or guessed Cycles.
  const compactRenders=latestGraph.renders;
  latestGraph.camera.zoom=.34;
  latestGraph.events.aftertransform();
  const compactLabels=elements['ex-compact-labels'];
  const labelFor=id=>compactLabels.children.find(label=>label.dataset.nodeId===id);
  assert.equal(compactLabels.hidden,false,'overview overlay must be visible');
  assert.equal(latestGraph.getNodeData('H-0008').style.labelText,'',
    'G6 font must be disabled in compact mode to prevent displaced text');
  assert.equal(labelFor('H-0008').textContent,'87',
    'H-0008 corresponds to scientific Cycle 87, not ID 0008');
  assert.equal(labelFor('H-0009').textContent,'88');
  assert.equal(labelFor('B').textContent,'150');
  assert.equal(labelFor('D').textContent,'3');
  assert.equal(labelFor('A').textContent,'C?',
    'Hypothesis without a verified Cycle never displays an H ID as chronology');
  assert.equal(labelFor('C').textContent,'F',
    'uncycled finding remains distinguishable');
  assert.equal(labelFor('E').textContent,'E?',
    'external references must be readable and never become fake Cycles');
  latestGraph.events['node:pointerover']({target:{id:'E'}});
  assert(elements['ex-tooltip'].innerHTML.includes('外部参考资料 · 非科研 Cycle'),
    'external node tooltip must explain the E marker without inventing a verdict');
  assert(elements['ex-tooltip'].innerHTML.includes('E'),
    'external node identity is preserved in its tooltip');
  const overviewStyle=latestGraph.getNodeData('H-0008').style;
  assert.equal(overviewStyle.labelOffsetX,0,
    'native label still has no predecessor offset in compact mode');
  // Overview colors select a semantic dimension without changing layout or camera.
  const colorToggle=elements['ex-graph'].children.find(x=>x.id==='ex-color-mode');
  assert(colorToggle,'overview color mode control exists inside graph toolbar');
  assert(colorToggle.textContent.includes('前沿'),'frontier coloring is the default');
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#384152',
    'unspecified frontier is neutral even when scientific verdict is supported');
  const colorRenders=latestGraph.renders;
  colorToggle.onclick();
  assert(colorToggle.textContent.includes('结论'));
  assert(elements['ex-type-legend'].children.some(x=>x.textContent==='Outcome'),
    'Outcome legend remains visible when switching the overview color dimension');
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#207A62',
    'Outcome overview paints supported H green');
  assert.equal(latestGraph.getNodeData('H-0009').style.fill,'#374151',
    'unjudged placeholder open does NOT become purple in Outcome overview');
  assert.equal(latestGraph.getNodeData('E').style.fill,'#596d87',
    'External always remains provenance gray');
  assert.equal(latestGraph.renders,colorRenders,'overview color toggle never relayouts');
  colorToggle.onclick();
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#384152',
    'toggling back restores frontier color without moving nodes');
  const cycleLabel=labelFor('H-0008');
  assert.equal(cycleLabel.style.width,352*.34+'px');
  assert.equal(cycleLabel.style.height,198*.34+'px');
  assert.equal(cycleLabel.style.left,350-352*.34/2+'px');
  assert.equal(cycleLabel.style.top,250-198*.34/2+'px');
  // Pan changes only screen-space projection, not the original graph layout.
  const originalViewportProjection=latestGraph.getViewportByCanvas.bind(latestGraph);
  latestGraph.getViewportByCanvas=p=>{const point=originalViewportProjection(p);return [point[0]+23,point[1]-17]};
  latestGraph.events.aftertransform();
  assert.equal(cycleLabel.style.left,350+23-352*.34/2+'px');
  assert.equal(cycleLabel.style.top,250-17-198*.34/2+'px');
  latestGraph.getViewportByCanvas=originalViewportProjection;
  latestGraph.events.aftertransform();
  assert.equal(cycleLabel.style.left,350-352*.34/2+'px');
  assert.equal(cycleLabel.style.top,250-198*.34/2+'px');
  assert(Number.parseFloat(cycleLabel.style.fontSize)<=72,
    'font is bounded to 72 screen px');
  assert(Number.parseFloat(cycleLabel.style.fontSize)>=40,
    'numeric label occupies the card in overview');
  const largeScreenFont=Number.parseFloat(cycleLabel.style.fontSize);
  latestGraph.camera.zoom=.2;
  latestGraph.events.aftertransform();
  const smallScreenFont=Number.parseFloat(cycleLabel.style.fontSize);
  assert(smallScreenFont<largeScreenFont,'font grows visibly as the node becomes larger');
  latestGraph.camera.zoom=.35;
  latestGraph.events.aftertransform();
  assert(Number.parseFloat(cycleLabel.style.fontSize)<=72,
    'overview font reaches a pixel cap instead of growing indefinitely');
  assert.equal(labelFor('E').textContent,'E?');
  latestGraph.camera.zoom=.34;
  latestGraph.events.aftertransform();
  card=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0008');
  assert.equal(card.style.visibility,'hidden','two-section detail hides in compact zoom');
  assert.equal(latestGraph.renders,compactRenders,'semantic zoom must not invoke Dagre');
  assert.equal(elements['ex-source-rail'].hidden,true,
    'compact overview hides embedded predecessor index H118/H121');
  assert.equal(elements['ex-source-rail'].children.length,0,
    'compact overview contains no left-side predecessor buttons');
  latestGraph.camera.zoom=.60;
  latestGraph.events.aftertransform();
  assert.equal(compactLabels.hidden,false,'exactly 60% remains numeric overview');
  assert.equal(card.style.visibility,'hidden','exactly 60% still hides full Outcome details');
  assert.equal(elements['ex-source-rail'].hidden,true);
  latestGraph.camera.zoom=.601;
  latestGraph.events.aftertransform();
  assert.equal(compactLabels.hidden,true,'above 60% switches to detailed content');
  assert.equal(card.style.visibility,'visible','60.1% shows Outcome detail');
  assert.equal(elements['ex-source-rail'].hidden,false,
    'detail mode restores predecessor index');
  assert(elements['ex-source-rail'].children.length>0);
  latestGraph.camera.zoom=.34;
  latestGraph.events.aftertransform();
  latestGraph.camera.zoom=.95;
  latestGraph.events.aftertransform();
  assert.equal(latestGraph.getNodeData('H-0008').style.labelText,'');
  assert.equal(latestGraph.getNodeData('H-0008').style.labelOffsetX,21,
    'detailed view restores reserved left predecessor rail');
  assert.equal(latestGraph.getNodeData('H-0009').style.labelText,'');
  assert.equal(latestGraph.getNodeData('H-0009').style.labelFontSize,12.5);
  assert.equal(card.style.visibility,'visible','detail view returns beyond threshold');
  assert.equal(compactLabels.hidden,true,'overview labels hide in detail mode');
  assert.equal(latestGraph.renders,compactRenders,'details never cause relayout');
  latestGraph.camera.zoom=.35;
  latestGraph.events.aftertransform();
  latestGraph.events['node:dblclick']({target:{id:'H-0009'}});await tick();await tick();
  assert.equal(latestGraph.camera.zoom,1.15,
    'double-click a very tiny numbered node jumps to readable detail zoom');
  assert.equal(latestGraph.getNodeData('H-0009').style.labelText,'',
    'native G6 labels remain hidden beneath interactive detail overlay');
  assert.equal(elements['ex-compact-labels'].hidden,true,
    'double click crosses 60% and reveals H/Outcome regions');
  latestGraph.camera.zoom=3.2;
  latestGraph.events['node:dblclick']({target:{id:'H-0009'}});await tick();await tick();
  assert.equal(latestGraph.camera.zoom,3.2,
    'double-click never unexpectedly zooms out when user already exceeded max');
  // Changing node count never recalibrates what 100% means.
  const nodesBeforeGrowth=latestGraph.data.nodes.length;
  latestGraph.data.nodes.push({id:'new-visual-node',style:{labelText:'',labelFontSize:12.5},data:{compactLabel:'',detailLabel:''}});
  assert.equal(latestGraph.data.nodes.length,nodesBeforeGrowth+1);
  latestGraph.camera.zoom=1;
  latestGraph.events.aftertransform();
  assert.equal(elements['ex-zoom-percent'].textContent,'100%',
    '100% does not depend on added nodes or the current visible graph scope');
  latestGraph.events['node:click']({target:{id:'H-0009'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'H-0009');
  nodes.push({id:'H-9999',label:'Unassigned cycle',semantic:{type:'Hypothesis',title:'Unassigned cycle'}});
  await elements['ex-refresh'].onclick();await tick();
  latestGraph.camera.zoom=.30;
  latestGraph.events.aftertransform();
  assert.equal(labelFor('H-9999').textContent,'C?',
    'H numeric suffix is never displayed as a Cycle');
  nodes.push({id:'H-0034',label:'Selected source',semantic:{type:'Hypothesis',title:'Selected source'}});
  nodes.push({id:'H-0035',label:'Other source',semantic:{type:'Hypothesis',title:'Other source'}});
  nodes.push({id:'2-hypothesis',label:'Historical source',semantic:{type:'Hypothesis',title:'Historical source'}});
  await elements['ex-refresh'].onclick();await tick();
  latestGraph.camera.zoom=.07;
  latestGraph.events.aftertransform();
  assert.equal(labelFor('H-0034').textContent,'C?',
    'at 7% an uncycled H shows unknown Cycle');
  assert.equal(labelFor('H-0035').textContent,'C?',
    'another uncycled H also avoids ID-based chronology');
  assert.equal(labelFor('2-hypothesis').textContent,'C?',
    'legacy H IDs are not used as fallback Cycle labels');
  assert.equal(labelFor('H-0008').textContent,'87',
    'a trustworthy Cycle still takes priority over its H ID');
  assert(Number.parseFloat(labelFor('H-0034').style.fontSize)>0,
    '7% label remains visibly sized');
  assert(Number.parseFloat(labelFor('H-0034').style.fontSize)<=72,
    'fallback labels obey the normal pixel cap');
  assert(!elements['ex-reader'].innerHTML.includes('ex-outcome-details'),
    'unjudged Hypothesis reader must not contain an Outcome section');
  // Historical multi-cycle verdicts and multiple frontier branches are independent.
  const h8=nodes.find(n=>n.id==='H-0008');
  h8.semantic.frontiers=[{status:'closed'},{status:'active'},{status:'parked'}];
  h8.semantic.cycle_outcomes=[
    {cycle_id:'87',verdict:'supported',summary:'scope A'},
    {cycle_id:'89',verdict:'rejected',summary:'scope B'}];
  await elements['ex-refresh'].onclick();await tick();
  latestGraph.camera.zoom=.30;latestGraph.events.aftertransform();
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#186F98',
    'active frontier takes precedence over parked and closed branches');
  const multi=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0008');
  assert.equal(multi.children[0].style.background,'#186F98',
    'H upper half is colored by the active research frontier');
  assert.equal(multi.children[1].style.background,'#656C7A',
    'mixed historical outcomes are not colored by an arbitrary latest verdict');
  assert(multi.children[0].title.includes('1 Active · 1 Parked · 1 Closed'));
  colorToggle.onclick();
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#656C7A',
    'Outcome color mode reports mixed history in overview');
  latestGraph.camera.zoom=.601;latestGraph.events.aftertransform();
  assert.equal(latestGraph.getNodeData('H-0008').style.fill,'#186F98',
    'at >60%, G6 underlying H surface always uses frontier status');
  assert.equal(multi.children[1].style.background,'#656C7A',
    'detail lower Outcome retains its own verdict color across mode changes');
  // Imported milestone_advanced is a compatibility projection, not Judge Open.
  nodes.find(n=>n.id==='H-0010').semantic.cycle_outcomes=[
    {cycle_id:'90',verdict:'open',original_verdicts:['milestone_advanced']}];
  await elements['ex-refresh'].onclick();await tick();
  const legacy=elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='H-0010');
  assert(legacy.children[1].children[0].textContent.includes('历史归一'));
  assert.equal(legacy.children[1].style.background,'#656C7A',
    'legacy-normalized Open remains neutral instead of asserting a Judge Open');

  console.log('G6 view model test: full tree fits by default; hover branch controls and reader/zoom OK');
})().catch(e=>{console.error(e);process.exitCode=1});

"""


class GraphSemanticsTest(unittest.TestCase):
    def test_scientific_header_is_separate_from_canvas_type_and_geometry(self):
        source = {"id": "result-hookean", "type": "text", "x": 400, "y": 700,
                  "text": "type: Finding\nid: result-hookean\ntimestamp: 09-08-19:21\n\n# Hookean theorem\n\nFull math unchanged."}
        parsed = project_node(source)
        self.assertEqual(parsed["type"], "Finding")
        self.assertEqual(parsed["title"], "Hookean theorem")
        self.assertIsNone(parsed.get("legacy_status"))
        self.assertIsNone(parsed["sortable_time"])
        self.assertNotIn("x", parsed)
        self.assertNotIn("y", parsed)

    def test_external_never_inherits_scientific_authority(self):
        parsed = project_node({"id": "manual", "text": "type: External\n"
                               "content_type: Finding\nfrontier_status: active\n"
                               "scientific_status: supported\n\n# Note"})
        self.assertEqual(parsed["type"], "External")
        self.assertIsNone(parsed["frontier_status"])
        self.assertIsNone(parsed["scientific_status"])

    def test_legacy_decision_and_timestamp(self):
        parsed = project_node({"id": "historical-c15-decision-4",
                               "text": "type: Decision\nverdict: open\n"
                                       "timestamp: 2026-10-04T22:01:26+08:00\n\n# Judge"})
        self.assertEqual(parsed["cycle"], 15)
        self.assertEqual(project_node({"id": "150-decision", "text": "type: Decision\n\n# Test"})["cycle"], 150)
        self.assertEqual(parsed["verdict"], "open")
        self.assertEqual(project_node({"id": "9-decision", "text":
            "type: Decision\nid: 9-decision\nstatus: rejected\ncycle_id: 9\n\n# Legacy"})["legacy_status"], "rejected")
        self.assertEqual(parsed["sortable_time"], "2026-10-04T22:01:26+08:00")

    def test_explicit_cycle_metadata_does_not_infer_from_arbitrary_ids(self):
        external = project_node({"id": "audit-finding-c0087", "text":
                                 "type: External\ncycle_id: 87\n\n# Audit"})
        self.assertEqual(external["cycle"], 87)
        older = project_node({"id": "H-0011", "text":
                              "type: Hypothesis\ncreated_cycle: cycle-126\n"
                              "cycle_id: 130\n\n# Older H"})
        self.assertEqual(older["cycle"], 126)
        unknown = project_node({"id": "audit-finding-c0087", "text":
                                "type: External\n\n# Audit"})
        self.assertIsNone(unknown["cycle"])
        self.assertIsNone(project_node({"id": "H-0087", "text":
                                        "type: Hypothesis\n\n# H"})["cycle"])

    def test_legacy_hypothesis_cycle_identifiers(self):
        for identifier, expected in (("1-hypothesis", 1), ("3-hypothesis", 3),
                                     ("audit-hyp-c0088-scanlan-independence-semantics-20260928", 88)):
            self.assertEqual(project_node({"id": identifier, "text":
                                           f"type: Hypothesis\nid: {identifier}\n\n# H"})["cycle"], expected)
        self.assertEqual(project_node({"id": "audit-hyp-c0088-example", "text":
                                       "type: Hypothesis\ncreated_cycle: 89\n\n# H"})["cycle"], 89)

    def test_document_and_scripts_embed_without_canvas_write(self):
        page = graph_content()
        self.assertIn("contain:paint;isolation:isolate", page,
                      "graph/reader must clip retired canvas compositing layers")
        self.assertIn("#ex-g6{height:100%;width:100%;overflow:hidden", page)
        self.assertIn("antv/g6@5.1.1", page)
        self.assertNotIn('id="ex-layout-side"', page)
        self.assertNotIn('id="ex-layout-stack"', page)
        self.assertNotIn("左右分屏", page)
        self.assertNotIn("上下分屏", page)
        self.assertIn('id="ex-fullscreen"', page)
        # The removed jump-to-latest research-frontier control must not have
        # a rendered button, JS listener, or leftover navigation helper.
        self.assertNotIn('id="ex-frontier"', page)
        self.assertNotIn('jumpToResearchFrontier', page)
        self.assertNotIn('latestFullGraphNode', page)
        self.assertIn('id="ex-all"', page)
        self.assertNotIn('id="ex-local"', page)
        self.assertIn('id="ex-zoom-percent"', page)
        self.assertLess(page.index('id="ex-plus"'), page.index('id="ex-zoom-percent"'))
        self.assertLess(page.index('id="ex-zoom-percent"'), page.index('id="ex-minus"'))
        self.assertIn('100% 为固定节点基础尺寸', page)
        self.assertIn('role="separator"', page)
        self.assertIn("window.G6.Graph", page)
        self.assertNotIn("TM_GRAPH_JS", page)
        self.assertNotIn("n.x", SCRIPT.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "requires Node.js")
    def test_fresh_entry_deep_link_and_stale_camera_priority(self):
        # Reuse the same fake browser/G6 setup for independent first visits.
        prefix = HARNESS.split("  onReady();await tick();", 1)[0]
        cases = (
            ("stale-unanchored", "location.hash='';", "undefined", "null"),
            ("explicit-deep-link", "location.hash='#node%3AD';", "D", "'D'"),
        )
        for title, location_setup, expected_focus, expected_selection in cases:
            with self.subTest(title=title):
                script = prefix + f'''
  pageHistory[pageIndex].state={{
    tmExplorerView:{{taskId:'0000000000000001',mode:'frontier',
      selected:{{kind:'node',id:'A',anchor:''}},collapsed:['X'],
      camera:{{zoom:.18,position:[999,999]}},readerScroll:0}},
    tmExplorerIndex:0
  }};
  {location_setup}
  onReady();await tick();await tick();
  assert(latestGraph.fitCalls||latestGraph.focused,'landing fits the tree or honors a deep link');
  assert.equal(latestGraph.data.nodes.length,5,'complete graph is available');
  assert.equal(latestGraph.focused,{('undefined' if expected_focus=='undefined' else repr(expected_focus))},
    'landing focuses latest Cycle or explicit deep-linked node');
  assert.equal(history.state.tmExplorerView.selected?.id??null,{expected_selection},
    'fresh entry is never hijacked by a stale selected node');
  assert.deepEqual([...history.state.tmExplorerView.collapsed],[],
    'previously folded nodes do not hide new landing content');
  console.log('fresh-entry smoke success');
}})().catch(e=>{{console.error(e);process.exitCode=1}});
'''
                proc = subprocess.run(
                    ["node", "-e", script, str(SCRIPT)],
                    capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("fresh-entry smoke success", proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "requires Node.js")
    def test_graph_view_model_and_multi_parent_collapse(self):
        process = subprocess.run(["node", "-e", HARNESS, str(SCRIPT)],
                                 capture_output=True, text=True, timeout=12, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("full tree fits by default", process.stdout)


if __name__ == "__main__":
    unittest.main()
