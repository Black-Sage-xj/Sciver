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
  assert.equal(elements['ex-all']['aria-pressed'],'true',
    'direct unanchored entry defaults to 完整图谱');
  assert.equal(latestGraph.data.nodes.length,5,
    'full graph includes the otherwise filtered historical branch');
  assert.equal(latestGraph.focused,'B',
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
  const tipButtons=elements['ex-tip-summary'].children.filter(x=>x.tagName==='BUTTON');
  assert.equal(tipButtons.length,4,'all/active/parked/closed navigation controls');
  assert(tipButtons.some(x=>x.textContent.includes('全部 3')));
  assert(tipButtons.some(x=>x.textContent.includes('已终止 1')));
  const legend=elements['ex-type-legend'].children.filter(x=>x.className==='ex-type-key');
  assert.equal(legend.length,4,'detail legend shows H frontier, Outcome, and legacy node types');
  assert.notEqual(legend[0].children[0].style.background,legend[1].children[0].style.background);
  const closedControl=tipButtons.find(x=>x.textContent.includes('已终止'));
  closedControl.onclick();
  assert.equal(elements['ex-tip-panel'].hidden,false);
  assert.equal(elements['ex-tip-panel'].children.filter(x=>x.className==='ex-tip-entry').length,1);
  const tipCycle=elements['ex-tip-panel'].children.find(x=>x.className==='ex-tip-entry')
    .children[0].children.find(x=>x.className==='ex-tip-sub').textContent;
  assert(tipCycle.startsWith('C? · '),'frontier list uses Cycle rather than node ID');
  closedControl.onclick();
  assert.equal(elements['ex-tip-panel'].hidden,true);
  // Worker chips must appear exclusively on currently active H rows, with
  // exact native Task Console deep-links and no card click interception.
  const activeControl=tipButtons.find(x=>x.textContent.includes('活跃'));
  await activeControl.onclick();await tick();
  const activeRow=elements['ex-tip-panel'].children.find(x=>x.className==='ex-tip-entry');
  const chips=activeRow.children.find(x=>x.className==='ex-tip-workers');
  assert.equal(chips.children.length,2,'two active workers can share one research H');
  assert.equal(chips.children[0].href,
    '/run/0000000000000001/?worker=worker-2','worker badge points at selected Console');
  assert(chips.children[0].textContent.includes('Worker 2 · C3'));
  assert(chips.children[1].textContent.includes('Worker 5 · C4'));
  assert(!elements['ex-tip-panel'].children.some(x=>x.textContent?.includes('Worker 3')),
    'closed/nonactive H is not assigned a live badge');
  activeControl.onclick();
  assert.equal(elements['ex-tip-panel'].hidden,true,'active panel still closes normally');
  assert.equal(latestGraph.data.nodes.length,5,'initial mode is the full graph');
  assert(latestGraph.data.nodes.some(n=>n.id==='X'),
    'full landing retains closed and non-frontier historical branches');
  const graphOptions=latestGraph.options;
  assert.equal(graphOptions.layout,undefined,
    'G6 must respect fixed Cycle positions rather than recompute Dagre ranks');
  assert(latestGraph.data.nodes.every(n=>Number.isFinite(n.style.x)&&Number.isFinite(n.style.y)),
    'all graph nodes have explicit x/y coordinates');
  // G6 trackpad routing: swipe pans and pinch zooms, never both for one event.
  const pan=graphOptions.behaviors.find(b=>b.key==='trackpad-pan');
  const pinch=graphOptions.behaviors.find(b=>b.key==='trackpad-pinch');
  assert.equal(pan.type,'scroll-canvas');
  assert.equal(pinch.type,'zoom-canvas');
  for(const [event,expectedPan,expectedZoom] of [
    [{ctrlKey:false,deltaX:64,deltaY:28},true,false],
    [{ctrlKey:true,deltaX:0,deltaY:-12},false,true],
    [{ctrlKey:false,metaKey:true,deltaY:18},true,false],
  ]){
    assert.equal(pan.enable(event),expectedPan,'two-finger pan gate');
    assert.equal(pinch.enable(event),expectedZoom,'pinch zoom gate');
    assert.notEqual(pan.enable(event),pinch.enable(event),'gesture behaviors must be exclusive');
  }
  assert.equal(graphOptions.behaviors.filter(b=>b==='zoom-canvas').length,0,
    'unconditional wheel zoom must be disabled');
  // G6's wheel listener is attached to its canvas; HTML overlay buttons
  // must forward gestures without stealing wheel events from that canvas.
  const overlayWheel=elements['ex-graph'].listeners.wheel;
  assert.equal(typeof overlayWheel,'function');
  const originalCamera=[...latestGraph.camera.position];
  const gesture=(target,ctrlKey,dx,dy)=>{
    const flags={prevented:false,stopped:false};
    overlayWheel({target,ctrlKey,deltaX:dx,deltaY:dy,clientX:400,clientY:200,
      preventDefault(){flags.prevented=true},stopPropagation(){flags.stopped=true}});
    return flags;
  };
  const overlayTarget={closest:()=>({className:'ex-outcome-card'})};
  const panFlags=gesture(overlayTarget,false,24,-12);
  assert.deepEqual(panFlags,{prevented:true,stopped:true});
  assert.deepEqual([...latestGraph.camera.position],
    [originalCamera[0]-24,originalCamera[1]+12],
    'HTML H/Outcome overlay continues two-finger G6 pan');
  const unchanged=latestGraph.camera.zoom;
  const nativeFlags=gesture({closest:()=>null},false,10,10);
  assert.deepEqual(nativeFlags,{prevented:false,stopped:false},
    'native G6 canvas handles its own wheel events exactly once');
  assert.equal(latestGraph.camera.zoom,unchanged);
  const pinchFlags=gesture(overlayTarget,true,0,-20);
  assert.deepEqual(pinchFlags,{prevented:true,stopped:true});
  assert(latestGraph.camera.zoom>unchanged,'pinch zoom still works over HTML node controls');
  assert.deepEqual([...latestGraph.zoomOrigin],[400,200],
    'overlay pinch is anchored at the pointer, not viewport center');
  latestGraph.camera.zoom=1;latestGraph.camera.position=originalCamera;
  latestGraph.events.aftertransform();
  assert.equal(latestGraph.layout,undefined,'manual coordinate chart has no hidden layout');
  assert.deepEqual([...latestGraph.data.nodes[2].style.size],[352,154]);
  const cards=new Map(latestGraph.data.nodes.map(n=>[n.id,n]));
  assert(cards.get('D').style.labelText.startsWith('C3 · D\n'),
    'Decision card heading has compact type and verified own cycle');
  assert(!cards.get('D').style.labelText.includes('Decision'),
    'type long name is no longer repeated inside card');
  assert(cards.get('C').style.labelText.startsWith('C150（前序） · F\n'),
    'Finding can show the explicitly qualified incoming Decision cycle');
  assert.equal(cards.get('A').style.labelText,'',
    'H content is displayed by split overlay, never duplicated by native G6 label');
  assert(elements['ex-outcome-cards'].children.find(x=>x.dataset.nodeId==='A')
    .children[0].children[0].textContent.startsWith('C? · H'),
    'missing Cycle remains explicitly unknown inside upper H region');
  assert(cards.get('B').style.labelText.includes('前沿 parked'),
    'frontier status is not conflated with scientific status');
  assert(cards.get('D').style.labelText.includes('科学 open'),
    'verdict remains visible');
  assert.equal(cards.get('D').style.labelMaxLines,6);
  assert.equal(cards.get('A').style.size[0],352);
  assert.equal(cards.get('A').style.fill,'#384152','H with no frontier has a neutral color');
  elements['ex-all'].onclick();await tick();
  const before=JSON.stringify(latestGraph.data);
  assert.equal(latestGraph.data.nodes.length,5);
  const defaultLine=latestGraph.data.edges.find(e=>e.id==='AC');
  assert(Math.min(...latestGraph.data.nodes.map(n=>n.style.zIndex))>
    Math.max(...latestGraph.data.edges.map(e=>e.style.zIndex)),
    'every card must render above every connecting line');
  assert.equal(defaultLine.style.stroke,'#e2e8f0','default edge is visibly bright');
  assert.equal(defaultLine.style.lineWidth,3.3,'default edge is thick enough to trace');
  assert.equal(defaultLine.style.opacity,1,'default edge is fully opaque');
  assert.equal(defaultLine.style.cursor,'pointer','edge advertises clickability');
  assert(defaultLine.style.increasedLineWidthForHitTesting>=10,'thin segments have generous click targets');
  nodes[0].x=-123456;nodes[0].y=888888;nodes[1].width=888;
  elements['ex-all'].onclick();await tick();
  assert.equal(JSON.stringify(latestGraph.data),before,'geometry must never affect read-only layout input');
  // Click only repaints styles, no Dagre re-layout. All direct inbound edges
  // attach to separately clickable rows within the target node's left strip.
  const originalRenders=latestGraph.renders;
  latestGraph.events['node:click']({target:{id:'C'}});
  await tick();await tick();
  assert(latestGraph.data.nodes.every(n=>n.style.fillOpacity===1),
    'selection must not make unrelated cards translucent');
  assert([...elements['ex-outcome-cards'].children].every(card=>card.style.opacity==='1'),
    'Outcome overlay cards remain opaque too');
  assert(Math.min(...latestGraph.data.nodes.map(n=>n.style.zIndex))>
    Math.max(...latestGraph.data.edges.map(e=>e.style.zIndex)),
    'highlighted lines must remain beneath cards');
  assert(pageHistory.length>=2, 'reading a node creates a browser history entry');
  assert.equal(history.state.tmExplorerView.selected.id,'C',
    'browser history records the selected scientific document');
  assert(location.hash.includes('node%3AC'), 'URL remains deep-linkable');
  // Leading Canvas metadata is a small, initially closed disclosure rather
  // than a flattened Markdown paragraph. The actual scientific text is intact.
  const rightHtml=elements['ex-reader'].innerHTML;
  assert(rightHtml.includes('<div class="ex-node-time">原始时间 · 09-08-19:21</div>'),
    'legacy Finding timestamp is shown in right-side reader');
  assert(!rightHtml.includes('Canvas 节点 · '),'repeated metadata heading is removed');
  assert(rightHtml.includes('<details class="ex-node-tags"><summary>节点标签 '));
  assert(!rightHtml.includes('<details class="ex-node-tags" open'),
    'metadata must be collapsed by default');
  assert(rightHtml.includes('节点标签 <span class="ex-node-tags-count">(3)</span>'));
  assert(rightHtml.includes('<div class="ex-node-tag-line"><span class="ex-node-tag-key">type:</span> <span class="ex-node-tag-value">Finding</span></div>'));
  assert(rightHtml.includes('<div class="ex-node-tag-line"><span class="ex-node-tag-key">id:</span> <span class="ex-node-tag-value">C</span></div>'));
  assert(rightHtml.includes('<div class="ex-node-tag-line"><span class="ex-node-tag-key">timestamp:</span> <span class="ex-node-tag-value">09-08-19:21</span></div>'));
  const scientificBody=rightHtml.split('<div class="ex-md">')[1];
  assert(scientificBody.includes('# Shared finding'));
  assert(scientificBody.includes('type: Finding, and must not be hidden'),
    'content field-like prose after the header remains visible');
  assert(!scientificBody.includes('timestamp: 09-08-19:21'),
    'raw frontmatter is not repeated in the Markdown body');
  assert.equal(latestGraph.renders,originalRenders,'selection must not rerun global layout');
  assert(latestGraph.draws>0,'selection must repaint');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AC').style.opacity,1);
  assert.equal(latestGraph.data.edges.find(e=>e.id==='BC').style.opacity,1);
  assert.equal(latestGraph.data.edges.find(e=>e.id==='CD').style.opacity,1,
    'clicking a node highlights outgoing downstream scientific paths');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='CD').style.stroke,'#67e8f9');
  assert.equal(latestGraph.data.nodes.find(n=>n.id==='D').style.stroke,'#a5f3fc');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.opacity,.28,
    'an unrelated visible branch remains present but less prominent');
  assert.notEqual(latestGraph.data.edges.find(e=>e.id==='AC').style.targetPort,
                  latestGraph.data.edges.find(e=>e.id==='BC').style.targetPort);
  assert.notEqual(latestGraph.data.edges.find(e=>e.id==='AC').style.stroke,
                  latestGraph.data.edges.find(e=>e.id==='BC').style.stroke,
    'source identities need separate tracing colors when routes overlap');
  const cPorts=latestGraph.data.nodes.find(n=>n.id==='C').style.ports;
  const leftPorts=cPorts.filter(x=>x.key.startsWith('in:'));
  assert.equal(leftPorts.length,2);
  assert.notEqual(leftPorts[0].placement[1],leftPorts[1].placement[1]);
  const stripHost=elements['ex-source-rail'];
  const cStrip=stripHost.children.find(n=>n.dataset.nodeId==='C');
  assert(cStrip,'source index must live inside the C node card');
  assert.equal(cStrip.style.width,'42px');
  assert.equal(cStrip.style.height,'154px');
  const directChips=cStrip.children.filter(n=>n.tagName==='BUTTON');
  assert.equal(directChips.length,2);
  assert.equal(directChips[0].dataset.sourceId,'A');
  assert.equal(directChips[1].dataset.sourceId,'B');
  assert.equal(directChips[0].textContent,'C?','no fabricated Cycle for Hypothesis');
  assert.equal(directChips[1].textContent,'C150','predecessor rail uses Cycle rather than type ID');
  assert.equal(directChips[0].children.length,0,'compact entry contains no extra long label');
  assert.equal(directChips[1].children.length,0,'compact entry contains no extra long label');
  assert.notEqual(directChips[0].style.color,directChips[1].style.color,
    'predecessor type color distinguishes Hypothesis from Decision');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AC').style.stroke,
    latestGraph.data.nodes.find(n=>n.id==='A').style.ports.length?'#93c5fd':'NO-PORT');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='BC').style.stroke,'#fcd34d');
  for(let i=0;i<2;i++)assert.equal(leftPorts[i].placement[1],
    (12+(i+.5)*(154-24)/2)/154,'port endpoint matches the index row center');
  assert.equal(elements['ex-back'].disabled,false);
  latestGraph.camera={zoom:1.8,position:[122,-56]};
  latestGraph.events.aftertransform();
  assert.equal(cStrip.style.transform,'scale(1.8)','embedded strip follows pinch zoom');
  assert.equal(elements['ex-zoom-percent'].textContent,'180%',
    'pinch zoom synchronizes the visible camera percentage');
  directChips.find(n=>n.title.includes('B')).onclick();
  await tick();await tick();
  assert.equal(latestGraph.focused,'B','source chip should focus its original node');
  assert.equal(elements['ex-forward'].disabled,true);
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(latestGraph.camera.zoom,1.8,'PDF-style back restores zoom');
  assert.equal(elements['ex-zoom-percent'].textContent,'180%',
    'history restores the zoom label even without a transform event');
  assert.deepEqual(latestGraph.camera.position,[122,-56],'PDF-style back restores translation');
  assert(elements['ex-source-rail'].children.some(n=>n.dataset.nodeId==='C'),
    'embedded source strip restores after navigation history');
  elements['ex-forward'].onclick();await tick();await tick();
  assert.equal(latestGraph.focused,'B','forward restores selected source');
  // Double-click zooms into and centers the selected node, and the browser
  // history retains the exact pre-zoom camera. No graph re-layout occurs.
  latestGraph.camera={zoom:1.65,position:[220,-90]};
  const nodesBeforeCenter=latestGraph.data.nodes.map(n=>n.id).join(',');
  const rendersBeforeZoom=latestGraph.renders;
  latestGraph.events['node:dblclick']({target:{id:'B'}});await tick();await tick();
  assert.equal(latestGraph.focused,'B','double click explicitly centers the node');
  assert.equal(latestGraph.zoomAnimation.duration,300,'zoom uses smooth animation');
  assert.equal(latestGraph.focusAnimation.duration,360,'focus uses smooth animation');
  assert.equal(latestGraph.camera.zoom,2.4,'double click enlarges, capped at readable max');
  assert.equal(elements['ex-zoom-percent'].textContent,'240%',
    'double click updates the absolute percentage');
  assert.equal(latestGraph.data.nodes.map(n=>n.id).join(','),nodesBeforeCenter,
    'double click never collapses or alters the graph');
  assert.equal(latestGraph.renders,rendersBeforeZoom,'double-click is camera-only, no Dagre relayout');
  assert.equal(elements['ex-back'].disabled,false);
  elements['ex-back'].onclick();await tick();await tick();
  assert.deepEqual(latestGraph.camera.position,[220,-90],
    'Back restores position before zooming into the node');
  assert.equal(latestGraph.camera.zoom,1.65,'Back restores previous zoom');
  elements['ex-forward'].onclick();await tick();await tick();
  assert.equal(latestGraph.camera.position[0],50,'Forward restores centered camera');
  assert.equal(latestGraph.camera.zoom,2.4,'Forward restores enlarged node');
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  const beforeFoldRenders=latestGraph.renders;
  const beforeFoldLayout=latestGraph.layout;
  const beforeFoldCamera=JSON.stringify(latestGraph.camera);
  const beforeFoldPositions=new Map(latestGraph.getNodeData().map(n=>[n.id,latestGraph.getElementPosition(n.id)]));
  const isShown=id=>(latestGraph.getNodeData(id)||latestGraph.data.edges.find(e=>e.id===id))?.style.visibility!=='hidden';
  elements['ex-collapse'].onclick();await tick();await tick();
  assert.equal(latestGraph.renders,beforeFoldRenders,'collapse never re-runs Dagre');
  assert.equal(latestGraph.layout,beforeFoldLayout,'collapse leaves layout object untouched');
  assert.equal(JSON.stringify(latestGraph.camera),beforeFoldCamera,'collapse never moves the camera');
  for(const n of latestGraph.getNodeData())assert.deepEqual(latestGraph.getElementPosition(n.id),
    beforeFoldPositions.get(n.id),'collapse leaves every node at its original position');
  assert(!isShown('X'),'exclusive descendant of collapsed A is hidden in place');
  assert(isShown('A')&&isShown('C')&&isShown('D'),'other nodes stay visible in their slots');
  assert(!isShown('AC'),'collapsed outbound link is hidden in place');
  assert(isShown('BC'),'alternative parent edge stays visible');
  // Folding forms one stable history step; back/forward change visibility only.
  elements['ex-back'].onclick();await tick();await tick();
  assert(isShown('X'),'back reveals the original branch without reflow');
  elements['ex-forward'].onclick();await tick();await tick();
  assert(!isShown('X'),'forward hides the original branch without reflow');
  elements['ex-expand-all'].onclick();await tick();await tick();
  assert(isShown('X')&&isShown('AC'),'expand restores original nodes and edges');
  assert.equal(latestGraph.renders,beforeFoldRenders,'expand and history do not re-render Dagre');
  assert.equal(JSON.stringify(latestGraph.camera),beforeFoldCamera,'camera is unchanged on fold and unfold');
  assert.equal(latestGraph.data.nodes.length,5);
  assert.equal(latestGraph.data.edges.length,4);
  assert.equal(elements['ex-forward'].disabled,true,'new branch action clears forward history');
  // Navigating a filtered-out closed tip opens the real full graph and focuses it.
  closedControl.onclick();
  const closedTip=elements['ex-tip-panel'].children.find(x=>x.className==='ex-tip-entry').children[0];
  closedTip.onclick();await tick();await tick();
  assert.equal(latestGraph.focused,'X');
  assert.equal(elements['ex-all']['aria-pressed'],'true');
  assert.equal(elements['ex-tip-panel'].hidden,true);
  // More than four incoming edges automatically grows the card; every connector
  // still reaches its own embedded index row rather than sharing one endpoint.
  nodes.push({id:'E',label:'E',semantic:{type:'External',title:'E'},text:'# E'});
  nodes.push({id:'Z',label:'Z',semantic:{type:'Finding',title:'Z'},text:'# Z'});
  edges.push(
    {id:'AD',fromNode:'A',toNode:'D'},
    {id:'BD',fromNode:'B',toNode:'D'},
    {id:'XD',fromNode:'X',toNode:'D'},
    {id:'ED',fromNode:'E',toNode:'D'},
    {id:'ZD',fromNode:'Z',toNode:'D'}
  );
  await elements['ex-refresh'].onclick();await tick();
  const bigNode=latestGraph.data.nodes.find(n=>n.id==='D');
  assert.deepEqual([...bigNode.style.size],[352,168]);
  const dStrip=elements['ex-source-rail'].children.find(n=>n.dataset.nodeId==='D');
  assert.equal(dStrip.children.filter(n=>n.tagName==='BUTTON').length,6);
  assert.equal(dStrip.style.height,'168px');
  const dPorts=bigNode.style.ports.filter(port=>port.key.startsWith('in:'));
  assert.equal(dPorts.length,6);
  for(let i=0;i<dPorts.length;i++)assert.equal(dPorts[i].placement[1],
    (12+(i+.5)*(168-24)/6)/168);
  // No input click is routed through a floating element outside the node.
  assert.equal(elements['ex-source-rail'].style.left,undefined);
  // Test both sides of a two-hop cutoff. A->D is a shortcut, so D->Y is a
  // second hop; Y->T is the third and must remain dim. E->Y is provenance.
  nodes.push({id:'Y',label:'Y',semantic:{type:'Finding',title:'Y'},text:'# Y'});
  nodes.push({id:'T',label:'T',semantic:{type:'Finding',title:'T'},text:'# T'});
  edges.push({id:'DY',fromNode:'D',toNode:'Y'},
    {id:'YT',fromNode:'Y',toNode:'T'},
    {id:'EY',fromNode:'E',toNode:'Y',edge_type:'external_reference'});
  await elements['ex-refresh'].onclick();await tick();
  // Two-level directional trace stops precisely at scientific hop 2, while
  // unrelated/provenance paths remain visible but do not receive highlights.
  const noRelayout=latestGraph.renders;
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  for(const id of ['AC','AX','CD','AD','XD','DY']){
    const line=latestGraph.data.edges.find(e=>e.id===id);
    assert(line.style.lineWidth>=4,'reachable scientific edge '+id+' is highlighted');
    assert.equal(line.style.opacity,1,'reachable path '+id+' remains opaque');
  }
  for(const id of ['YT','EY','BC'])assert.equal(latestGraph.data.edges.find(e=>e.id===id).style.opacity,.28,
    'third-hop/provenance/alternate-parent path '+id+' must not be highlighted');
  assert.notEqual(latestGraph.data.nodes.find(n=>n.id==='T').style.stroke,'#a5f3fc',
    'third-generation successor node must not be highlighted');
  assert.equal(latestGraph.data.nodes.find(n=>n.id==='D').style.stroke,'#a5f3fc',
    'descendant nodes receive a visible cyan outline');
  assert.equal(latestGraph.renders,noRelayout,'node click must not restart Dagre');
  // Any lit successor edge directly opens its target node and centers it.
  latestGraph.events['edge:click']({target:{id:'DY'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'node');
  assert.equal(history.state.tmExplorerView.selected.id,'Y');
  assert.equal(latestGraph.focused,'Y');
  assert(location.hash.includes('node%3AY'));
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'A');
  latestGraph.events['edge:click']({target:{id:'AC'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'C');
  assert.equal(latestGraph.focused,'C');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='CD').style.opacity,1,
    'selecting newly reached C illuminates its next successor');
  // The corresponding lit incoming edge jumps backwards to its source.
  latestGraph.events['edge:click']({target:{id:'BC'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'B');
  assert.equal(latestGraph.focused,'B');
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'C');
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'A');
  assert.equal(latestGraph.renders,noRelayout,'edge jumping preserves Dagre layout');

  // Clicking an unrelated line still selects it (gold) and its endpoints, stores
  // a deep link, and can be reversed through the ordinary Back navigation.
  latestGraph.events['edge:click']({target:{id:'BC'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'edge');
  assert.equal(history.state.tmExplorerView.selected.id,'BC');
  assert(location.hash.includes('edge%3ABC'),'clicked relation gets a stable deep link');
  assert(elements['ex-reader'].innerHTML.includes('科研连线'));
  const selectedLine=latestGraph.data.edges.find(e=>e.id==='BC');
  assert.equal(selectedLine.style.stroke,'#fef08a');
  assert.equal(selectedLine.style.lineWidth,5);
  assert.equal(selectedLine.style.opacity,1);
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AC').style.opacity,.28);
  for(const id of ['B','C'])
    assert.equal(latestGraph.data.nodes.find(n=>n.id===id).style.stroke,'#fde68a');
  assert.equal(latestGraph.renders,noRelayout,'edge click should repaint only, no re-layout');
  elements['ex-back'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'A');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='CD').style.stroke,'#67e8f9');
  elements['ex-forward'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'edge');
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
  assert(elements['ex-type-legend'].children.some(x=>x.textContent==='结论颜色'),
    'overview legend switches along with the color dimension');
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
  // Task switching is a fresh unanchored entry, even if the previous task
  // carried a selected node/hash and the page was in a different scope.
  elements['ex-task'].listeners.change();await tick();await tick();
  assert.equal(elements['ex-all']['aria-pressed'],'true',
    'task entry also resets to complete graph');
  assert.equal(latestGraph.focused,'H-0010',
    'newest real Hypothesis Cycle 90 wins over historical Decision 150 and External');
  assert.equal(history.state.tmExplorerView.selected,null,
    'task landing leaves reader unselected and does not rewrite H identity');
  console.log('G6 view model test: full graph centers latest valid Hypothesis Cycle; hierarchy/zoom controls OK');

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
        self.assertIn('id="ex-local"', page)
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
            ("stale-unanchored", "location.hash='';", "B", "null"),
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
  assert.equal(elements['ex-all']['aria-pressed'],'true');
  assert.equal(latestGraph.data.nodes.length,5,'complete graph is available');
  assert.equal(latestGraph.focused,'{expected_focus}',
    'landing focuses latest Cycle or explicit deep-linked node');
  assert.equal(history.state.tmExplorerView.selected?.id??null,{expected_selection},
    'fresh entry is never hijacked by a stale selected node');
  assert.equal(history.state.tmExplorerView.mode,'all');
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
        self.assertIn("full graph centers latest valid Hypothesis Cycle", process.stdout)


if __name__ == "__main__":
    unittest.main()
