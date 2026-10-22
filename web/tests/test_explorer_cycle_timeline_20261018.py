"""Chronology uses first frozen H only, with branch-following fixed positions."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tm_observer.canvas_md import build_index
from test_research_explorer_g6 import SCRIPT


NODE_CHECK = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(process.argv[1],'utf8');
const start=src.indexOf('const TIMELINE_STEP=');
const end=src.indexOf('function g6Data(',start);
assert(start>=0&&end>start);
const helper=`const CARD_W=352,CARD_H=154;
  function knownType(n){return n.semantic?.type||'Unknown'}
  function stable(a,b){return String(a).localeCompare(String(b),'en',{numeric:true})}
  `+src.slice(start,end)+`\n;timelineCoordinates`;
const timeline=vm.runInNewContext(helper,{});
const raw=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const cycleStart=src.indexOf('let hypothesisCycleCache=null;');
const cycleEnd=src.indexOf('function compactCycleLabel(',cycleStart);
const infer=vm.runInNewContext(`const index=${JSON.stringify(raw)};
  function knownType(n){return n?.semantic?.type||'Unknown'}
  function isScientificEdge(e){return !['external_reference','research_source'].includes(e.edge_type)}
  `+src.slice(cycleStart,cycleEnd)+`;displayCycle`,{});
const nodes=raw.nodes.map(n=>({id:n.id,data:{semantic:n.semantic,
  placementCycle:n.placementCycle??(n.semantic.type==='Hypothesis'?infer(n):null)},style:{size:[352,154]}}));
const edges=raw.edges.map(e=>({source:e.fromNode,target:e.toNode,id:e.id}));
timeline(nodes,edges);
const byId=new Map(nodes.map(n=>[n.id,n]));
const x=id=>byId.get(id)?.style.x;
const y=id=>byId.get(id)?.style.y;
const known=nodes.filter(n=>n.data.semantic.type==='Hypothesis'&&Number.isInteger(n.data.semantic.cycle));
const unknown=nodes.filter(n=>n.data.semantic.type==='Hypothesis'&&
  !Number.isInteger(n.data.semantic.cycle)&&!Number.isInteger(n.data.placementCycle));
assert(known.length>0);
for(const n of unknown)assert(x(n.id)>Math.max(...known.map(h=>x(h.id))),
  `Unknown Cycle ${n.id} must stay in the separate undated area`);
for(const a of known)for(const b of known){
 if(a.data.semantic.cycle<b.data.semantic.cycle)
  assert(x(a.id)<x(b.id),`Cycle ${a.data.semantic.cycle} (${a.id}) must be left of Cycle ${b.data.semantic.cycle} (${b.id})`);
 if(a.data.semantic.cycle===b.data.semantic.cycle)
  assert.equal(x(a.id),x(b.id),'same frozen Cycle shares one vertical column');
}
for(const n of nodes)if(Number.isInteger(n.data.placementCycle))for(const h of known){
 if(n.data.placementCycle<h.data.semantic.cycle)assert(x(n.id)<x(h.id));
 if(n.data.placementCycle>h.data.semantic.cycle)assert(x(n.id)>x(h.id));
}
for(const n of nodes){
 assert(Number.isFinite(n.style.x)&&Number.isFinite(n.style.y),`finite position ${n.id}`);
}
for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
 const a=nodes[i],b=nodes[j];
 if(Math.abs(a.style.x-b.style.x)<352+24){
   assert(Math.abs(a.style.y-b.style.y)>=(a.style.size[1]+b.style.size[1])/2+36,
    `overlapping cards ${a.id}/${b.id}`);
 }
}
console.log('PASS',known.length,'dated',unknown.length,'unknown',nodes.length,'nodes',edges.length,'edges');
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='requires Node.js')
def test_cycle_column_and_external_satellite_geometry(tmp_path: Path):
    fixture = {
        'realFixture': False,
        'nodes': [
            {'id':'H-116','semantic':{'type':'Hypothesis','cycle':116}},
            {'id':'H-128','semantic':{'type':'Hypothesis','cycle':128}},
            {'id':'H-118','semantic':{'type':'Hypothesis','cycle':118}},
            {'id':'E','semantic':{'type':'External','cycle':None},'placementCycle':49},
            {'id':'H-undated','semantic':{'type':'Hypothesis','cycle':None,
                                         'outcome':{'cycle_id':'190'}}},
        ],
        'edges': [
            {'id':'116-118','fromNode':'H-116','toNode':'H-118'},
            {'id':'118-E','fromNode':'H-118','toNode':'E'},
            {'id':'128-E','fromNode':'H-128','toNode':'E'},
            {'id':'u128','fromNode':'H-undated','toNode':'H-128'},
        ],
    }
    path=tmp_path/'fixture.json';path.write_text(json.dumps(fixture))
    result=subprocess.run(['node','-e',NODE_CHECK,str(SCRIPT),str(path)],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_orphaned_dossier_reference_can_still_supply_first_frozen(tmp_path: Path):
    from theory_master.science_graph_store import ScienceGraphStore
    text='<!-- theory-master/hypothesis-dossier/v1 -->\n# H-0090\n\n<!-- frozen-cycle:128 -->\n<!-- frozen-cycle:131 -->\n'
    doc=tmp_path/'notes'/'hypotheses'/'H-0090.md';doc.parent.mkdir(parents=True);doc.write_text(text)
    with ScienceGraphStore(tmp_path/'science_graph.sqlite3') as db:
        db.add_hypothesis('H-0090', title='Orphaned ref', dossier_ref=None)
        db.append_outcome(outcome_id='140-outcome', hypothesis_id='H-0090',cycle_id='140',
                          verdict='rejected',summary='later test')
    n=next(n for n in build_index(tmp_path)['nodes'] if n['id']=='H-0090')
    assert n['semantic']['cycle']==128
    assert n['semantic']['outcome']['cycle_id']=='140'
    assert 'created_cycle: 128' in n['text']
