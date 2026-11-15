"""Deterministic layout, inline navigation and retired URL regressions; no LLM calls."""
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

import pytest

from tm_observer.research_explorer_g6 import graph_content
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler
from test_research_explorer_g6 import HARNESS, SCRIPT

BASE = Path(__file__).resolve().parents[1] / "tm_observer"
TREE = BASE / "research_explorer_layout_c.js"


def run_js(source, *args):
    if not shutil.which("node"):
        pytest.skip("requires Node.js")
    result = subprocess.run(["node", "-e", source, *map(str, args)], check=False,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result


def test_one_tree_graph_has_no_layout_switch_local_scope_or_per_node_controls():
    page = graph_content()
    for control in ('ex-reader', 'ex-outcome-cards', 'ex-source-rail', 'ex-edge-menu'):
        assert f'id="{control}"' in page
    for retired in ('ex-layout-original', 'ex-layout-c', 'ex-local', 'ex-collapse',
                    'ex-branch-controls', 'ex-branch-toggle', 'tm-tree-c', 'c-help'):
        assert f'id="{retired}"' not in page
    assert 'id="ex-edge-menu-title"' not in page
    assert 'id="ex-edge-menu-summary"' not in page
    assert 'border-radius:50%' in page
    assert 'treeCoordinates(nodes,edges)' in page
    for retired in ('timelineCoordinates', 'setGraphLayout', 'setMode', 'latestScientificNodeId'):
        assert retired not in page


def test_compact_tree_keeps_all_nodes_edges_and_styles_and_ignores_cycle_gaps():
    run_js(r"""
const assert=require('node:assert/strict');
const {treeCoordinates}=require(process.argv[1]);
const N=(id,cycle,height=198,type='Hypothesis')=>({id,data:{semantic:{cycle,type}},
 style:{size:[352,height],fill:'#123456',ports:[{key:'original'}]}});
const E=(source,target,id)=>({id,source,target,data:{sourceEdge:{edge_type:'hypothesis_evolution'}}});
const nodes=[N('H-001',1,350),N('H-002',180),N('H-003',2),N('H-004',181,500),
 N('H-005',null),N('E-1',null,154,'External'),N('F-1',182,154,'Finding')];
const edges=[E('H-001','H-002','a'),E('H-001','H-003','b'),E('H-002','H-004','c'),
 E('H-003','H-004','d'),E('H-004','H-005','unknown'),
 E('H-005','E-1','ref'),E('H-004','F-1','finding')];
const before=JSON.stringify({nodes,edges});
const t=treeCoordinates(nodes,edges);
assert.equal(t.positions.size,nodes.length);
assert.equal(t.parent.get('H-004'),'H-002');
assert.equal(t.positions.get('H-002').x-t.positions.get('H-001').x,416,
 'a parent and child remain one card width + 64 apart regardless of Cycle gap');
assert.equal(t.positions.get('H-003').x,t.positions.get('H-002').x);
for(const n of nodes){
 assert(Number.isFinite(n.style.x)&&Number.isFinite(n.style.y));
 for(const m of nodes){if(n.id===m.id)continue;
  assert(Math.abs(n.style.x-m.style.x)>=352+63 ||
    Math.abs(n.style.y-m.style.y)>=(n.style.size[1]+m.style.size[1])/2+35,
    'variable-height cards must not overlap: '+n.id+' '+m.id);
 }
 const old=JSON.parse(before).nodes.find(x=>x.id===n.id);
 const {x,y,...style}=n.style;
 assert.deepEqual(style,old.style,'layout changes no colors, ports or content');
 assert.deepEqual(n.data,old.data);
}
assert.deepEqual(edges,JSON.parse(before).edges,'secondary, backward and reference edges remain intact');
const rerun=JSON.parse(before);
rerun.nodes.reverse();rerun.edges.reverse();
const again=treeCoordinates(rerun.nodes,rerun.edges);
assert.deepEqual([...t.positions].sort(),[...again.positions].sort());
const long=Array.from({length:4000},(_,i)=>N('chain-'+i,i));
const links=long.slice(1).map((n,i)=>E(long[i].id,n.id,'e'+i));
const chain=treeCoordinates(long,links);
assert.equal(chain.positions.size,4000);
assert.equal(chain.positions.get('chain-3999').x,3999*416);
assert.equal(new Set([...chain.positions.values()].map(p=>p.y)).size,1);
assert.equal(treeCoordinates([],[]).positions.size,0);
""", TREE)


def test_old_browser_scope_and_layout_snapshot_cannot_restore_removed_views():
    prefix = HARNESS.split('  onReady();await tick();', 1)[0]
    run_js(prefix + r"""
  onReady();await tick();await tick();
  const snapshot={taskId:'0000000000000001',selected:{kind:'node',id:'C',anchor:''},
    mode:'local',graphLayout:'original',collapsed:[],camera:{zoom:1,position:[0,0]}};
  onPopState({state:{tmExplorerView:snapshot,tmExplorerIndex:1}});await tick();await tick();
  assert.equal(latestGraph.data.nodes.length,5);
  assert.equal(latestGraph.data.edges.length,3);
  assert.equal(elements['ex-workspace'].dataset.graphLayout,'tree');
  assert.equal(history.state.tmExplorerView.mode,undefined);
  assert.equal(history.state.tmExplorerView.graphLayout,undefined);
})().catch(e=>{console.error(e);process.exitCode=1});
""", SCRIPT)


def test_old_layout_query_opens_full_tree_and_is_removed_from_canonical_url():
    prefix = HARNESS.split('  onReady();await tick();', 1)[0]
    prefix = prefix.replace("const location={hash:'',pathname:", "const location={hash:'',pathname:")
    run_js(prefix + r"""
  location.search+='&layout=original&view=legacy';
  onReady();await tick();await tick();
  assert.equal(latestGraph.data.nodes.length,5);
  assert.equal(latestGraph.data.edges.length,3);
  assert.equal(latestGraph.fitCalls,1);
  assert.equal(new URLSearchParams(location.search).get('layout'),null);
  assert.equal(new URLSearchParams(location.search).get('view'),null);
})().catch(e=>{console.error(e);process.exitCode=1});
""", SCRIPT)


def test_old_c_and_b_urls_redirect_to_one_explorer(tmp_path):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(tmp_path, ttl_s=1)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = build_opener(NoRedirect())
        for path in ('research-explorer-tmp-c', 'research-explorer-tmp-b', 'research-explorer-tmp',
                     'research-explorer-tmp-v0', 'research-explorer-tmp-a0'):
            for slash in ('', '/'):
                with pytest.raises(HTTPError) as error:
                    opener.open(f'http://127.0.0.1:{server.server_port}/{path}{slash}?observer_id=8dd38a9977eef7e5', timeout=10)
                assert error.value.code == 303
                target = urlsplit(error.value.headers['Location'])
                assert target.path == '/research-explorer/'
                params = parse_qs(target.query)
                assert params['observer_id'] == ['8dd38a9977eef7e5']
                assert params.get('layout') is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_single_predecessor_uses_natural_order_ignores_primary_fields_and_breaks_cycles():
    run_js(r"""
const assert=require('node:assert/strict');
const {projectForest}=require(process.argv[1]);
const nodes=[{id:'H-30',data:{semantic:{cycle:1}}},
 {id:'H-10',data:{semantic:{cycle:0}}},{id:'H-2',data:{semantic:{cycle:99}}},
 {id:'isolated'}];
const edges=[{id:'e10',source:'H-10',target:'H-30',primary_source:true,
 data:{sourceEdge:{edge_type:'hypothesis_evolution',is_primary:true}}},
 {id:'e2',source:'H-2',target:'H-30',primary_source:false,
 data:{sourceEdge:{edge_type:'research_source',is_primary:false}}},
 {id:'e11',source:'H-2',target:'H-30'},
 {id:'self',source:'isolated',target:'isolated'},
 {id:'missing',source:'unknown',target:'H-30'}];
const before=JSON.stringify({nodes,edges});
const forest=projectForest(nodes,edges);
assert.equal(forest.parent.get('H-30'),'H-2');
assert.deepEqual(forest.edges.map(e=>e.id),['e2']);
assert.equal(forest.traversal.length,nodes.length);
assert.equal(JSON.stringify({nodes,edges}),before,'projection never edits source records');
const reversed=projectForest([...nodes].reverse(),[...edges].reverse());
assert.deepEqual([...reversed.parent],[...forest.parent]);
assert.deepEqual(reversed.edges,forest.edges);
const cyclic=projectForest(['A','B','C','D'].map(id=>({id})),[
 {id:'ab',source:'A',target:'B'},{id:'ba',source:'B',target:'A'},
 {id:'cb',source:'C',target:'B'},{id:'dd',source:'D',target:'D'}]);
assert.equal(cyclic.parent.get('A'),'B');
assert.equal(cyclic.parent.get('B'),'C','skip cyclic first candidate and try next source');
assert.equal(cyclic.traversal.length,4);
assert.equal(cyclic.parent.size,2);
for(const id of cyclic.traversal){
 const seen=new Set();let current=id;
 while(cyclic.parent.has(current)){
   assert(!seen.has(current),'display projection is acyclic');
   seen.add(current);current=cyclic.parent.get(current);
 }
}
""", TREE)


def test_early_c_centers_parent_between_child_branches_without_card_overlap():
    run_js(r"""
const assert=require('node:assert/strict');
const {treeCoordinates}=require(process.argv[1]);
const ns=['A','B','C','D','E','F'].map(id=>({id,style:{size:[352,198]}}));
const es=[['A','B'],['A','C'],['B','D'],['B','E'],['B','F']]
 .map(([source,target])=>({id:source+target,source,target}));
const t=treeCoordinates(ns,es);
assert.equal(t.positions.get('A').y,(t.positions.get('B').y+t.positions.get('C').y)/2,
 'restore early C parent centering between first/last child, not subtree band center');
assert.equal(t.positions.get('B').y,(t.positions.get('D').y+t.positions.get('F').y)/2);
assert.equal(t.positions.get('B').x,t.positions.get('C').x);
assert.equal(t.positions.get('B').x-t.positions.get('A').x,416);
// Oversized cards stay inside their own subtree band even with asymmetric children.
ns.find(n=>n.id==='A').style.size[1]=1600;
ns.find(n=>n.id==='D').style.size[1]=1900;
ns.push({id:'other-root',style:{size:[352,2500]}});
treeCoordinates(ns,es);
for(const a of ns)for(const b of ns)if(a.id!==b.id)
 assert(Math.abs(a.style.x-b.style.x)>=416 ||
   Math.abs(a.style.y-b.style.y)>=(a.style.size[1]+b.style.size[1])/2+35,
   'restored branch centering keeps large cards separate');
""", TREE)


def test_hovered_child_branch_folds_independently_preserving_nested_and_sibling_state():
    prefix = HARNESS.split('  onReady();await tick();', 1)[0]
    run_js(prefix + r"""
  nodes.push({id:'Y',label:'Y',semantic:{type:'Finding'}},{id:'Z',label:'Z',semantic:{type:'Finding'}});
  edges.push({id:'DY',fromNode:'D',toNode:'Y'},{id:'XZ',fromNode:'X',toNode:'Z'});
  onReady();await tick();await tick();
  const hover=id=>latestGraph.events['edge:pointerover']({target:{id},originalEvent:{clientX:400,clientY:200}});
  const action=elements['ex-edge-menu-action'];
  const positions=JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y]));
  const camera=JSON.stringify(latestGraph.camera),reader=elements['ex-reader'].innerHTML,renders=latestGraph.renders;
  hover('CD');await action.onclick();
  assert.equal(latestGraph.getNodeData('Y').style.visibility,'hidden');
  hover('AC');await action.onclick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'hidden');
  assert.equal(latestGraph.getNodeData('X').style.visibility,'visible');
  hover('AX');await action.onclick();
  assert.equal(latestGraph.getNodeData('Z').style.visibility,'hidden');
  hover('AC');await action.onclick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'visible');
  assert.equal(latestGraph.getNodeData('Y').style.visibility,'hidden','nested child fold retained');
  assert.equal(latestGraph.getNodeData('Z').style.visibility,'hidden','sibling fold retained');
  assert.equal(JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y])),positions);
  assert.equal(JSON.stringify(latestGraph.camera),camera);
  assert.equal(elements['ex-reader'].innerHTML,reader);
  assert.equal(history.state.tmExplorerView.selected,null);
  assert.equal(latestGraph.renders,renders);
  await elements['ex-expand-all'].onclick();
  assert(latestGraph.data.nodes.every(n=>n.style.visibility==='visible'));
})().catch(e=>{console.error(e);process.exitCode=1});
""", SCRIPT)
