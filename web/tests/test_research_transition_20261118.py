"""Frozen scientific transitions remain readable, navigable and read-only."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess

import pytest

from test_research_explorer_g6 import HARNESS, SCRIPT
from theory_master.science_graph_store import ScienceGraphStore
from tm_observer.canvas_md import build_index, load_canvas
from tm_observer.graph_semantics import project_node


def _candidate():
    return {
        "transition": {"prior_gap": "The supported model leaves the long-time limit open.",
                       "current_change": "Vary the observation interval instead of adding trajectories.",
                       "source_contributions": {"H-0001": "Carries the response and sampled population.",
                                                "H-0002": "Provides the unresolved alternative explanation."}},
        "why_now": "The previous comparison isolated interval dependence.",
        "goal_advance": "Determine which observation scales identify the mechanism.",
    }


def test_sqlite_transition_is_projected_without_changing_the_frozen_sources(tmp_path):
    database = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(database) as store:
        store.add_hypothesis("H-0001", title="Supported bounded model", scientific_status="supported")
        store.add_hypothesis("H-0002", title="Independent unresolved comparison")
        store.add_hypothesis("H-0003", title="Observation interval dependence")
        store.append_outcome(outcome_id="cycle-1", hypothesis_id="H-0001", cycle_id="1",
                             verdict="supported", summary="Supported on the sampled interval.",
                             report_ref="notes/outcomes/1.md", record_only=True)
        store.append_outcome(outcome_id="cycle-2", hypothesis_id="H-0002", cycle_id="2",
                             verdict="open", summary="Two mechanisms remain indistinguishable.")
        store.add_evolution(edge_id="main", source="H-0001", target="H-0003", lineage_role="main")
        store.add_evolution(edge_id="other", source="H-0002", target="H-0003", lineage_role="other")
        expected = store.record_research_transition("H-0003", _candidate())
        # Later source edits must not rewrite the scientific motivation snapshot.
        store.db.execute("UPDATE hypotheses SET title='Later edited title' WHERE id='H-0001'")
    before = database.read_bytes()
    canvas = {node["id"]: node for node in load_canvas(tmp_path)["nodes"]}
    indexed = {node["id"]: node["semantic"] for node in build_index(tmp_path)["nodes"]}
    assert canvas["H-0003"]["research_transition"] == expected
    assert indexed["H-0003"]["research_transition"] == expected
    assert indexed["H-0001"]["research_transition"] is None
    sources = {source["role"]: source for source in expected["sources"]}
    assert sources["main"]["contribution"] == "Carries the response and sampled population."
    assert sources["other"]["contribution"] == "Provides the unresolved alternative explanation."
    assert {edge["lineage_role"]: edge["edge_type"] for edge in load_canvas(tmp_path)["edges"]} == {"main": "hypothesis_evolution", "other": "research_source"}
    assert sources["main"]["title"] == "Supported bounded model"
    assert sources["main"]["scientific_status"] == "supported"
    assert sources["main"]["outcome_refs"][0]["cycle_id"] == "1"
    assert sources["other"]["outcome_refs"][0]["verdict"] == "open"
    assert database.read_bytes() == before


def test_canvas_projection_only_accepts_an_explicit_hypothesis_object(tmp_path):
    record = {"schema": "theory-master/research-transition/v1", **_candidate()["transition"],
              "why_now": _candidate()["why_now"], "goal_advance": _candidate()["goal_advance"], "sources": []}
    raw = {"id": "H-0001", "text": "type: Hypothesis\nid: H-0001\n\n# Claim",
           "research_transition": record}
    before = copy.deepcopy(raw)
    assert project_node(raw)["research_transition"] == record
    assert raw == before
    assert project_node({**raw, "research_transition": "unstructured reason"})["research_transition"] is None
    assert project_node({**raw, "text": "type: External\n\n# Reference"})["research_transition"] is None
    assert project_node({"id": "H-0002", "text": raw["text"] + "\nwhy_now: body mention"})["research_transition"] is None
    canvas = tmp_path / "exploration.canvas"
    canvas.write_text(json.dumps({"nodes": [raw], "edges": []}), encoding="utf-8")
    canvas_before = canvas.read_bytes()
    assert build_index(tmp_path)["nodes"][0]["semantic"]["research_transition"] == record
    assert canvas.read_bytes() == canvas_before


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_reader_shows_frozen_transition_before_claim_and_source_clicks_navigate():
    bootstrap, marker, _ = HARNESS.partition("  onReady();await tick();")
    assert marker
    script = bootstrap + r"""
  const transition=(sources,change='Vary observation interval < 2 seconds')=>({
    schema:'theory-master/research-transition/v1',sources,
    prior_gap:'Supported in the tested regime; long-time identifiability remains open.',
    current_change:change,why_now:'The last bounded comparison isolated this ambiguity.',
    goal_advance:'Distinguish the two physical mechanisms.'});
  nodes[0].semantic={type:'Hypothesis',title:'Edited source A',scientific_status:'rejected',
    research_transition:transition([],'Start from interval-dependent covariance')};
  nodes[1].semantic={type:'Hypothesis',title:'Edited source B',scientific_status:'none'};
  nodes[2].label='Interval-dependent covariance';
  nodes[2].semantic={type:'Hypothesis',title:nodes[2].label,
    hypothesis_summary:'主张：Interval identifies the mechanism；条件：Independent trajectories；状态：待检验。',
    research_transition:transition([
      {hypothesis_id:'B',role:'other',title:'Frozen unresolved comparison',scientific_status:'open',
        contribution:'This alternative explains the same observation < differently',outcome_refs:[]},
      {hypothesis_id:'A',role:'main',title:'Frozen supported model',scientific_status:'supported',
        outcome_refs:[{id:'cycle-1',cycle_id:'1',verdict:'supported',report_ref:'notes/outcomes/1.md'}]}
    ])};
  const graphFetch=ctx.fetch;
  ctx.fetch=async url=>url.includes('/report?')?{ok:true,json:async()=>({
    id:'notes/outcomes/1.md',title:'Source comparison report',content:'Frozen source report body.',
    sections:[],next_offset:null,adjudication_status:'adjudicated'
  })}:graphFetch(url);
  const before=JSON.stringify(nodes);
  onReady();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  const html=elements['ex-reader'].innerHTML;
  const transitionSection=html.slice(html.indexOf('class="ex-research-transition"'),html.indexOf('class="ex-hypothesis-facts"'));
  assert(transitionSection.includes('为什么研究到这里'),'reason is above the scientific claim');
  for(const label of ['前序留下的问题','这一步改变什么','为什么现在研究','推进目标'])assert(transitionSection.includes(label));
  assert(transitionSection.includes('Vary observation interval &lt; 2 seconds'),'original text is safely rendered');
  assert(transitionSection.includes('主要承接')&&transitionSection.includes('补充来源'));
  assert(transitionSection.includes('This alternative explains the same observation &lt; differently'));
  assert(transitionSection.indexOf('Frozen supported model')<transitionSection.indexOf('Frozen unresolved comparison'));
  assert(transitionSection.includes('整体猜想：支持')&&transitionSection.includes('整体猜想：尚无法判定'));
  assert(!transitionSection.includes('Edited source'),'snapshot titles and states do not follow later source edits');
  assert(!transitionSection.includes('失败')&&!transitionSection.includes('rejected'));
  assert(transitionSection.includes('data-ref="node:A"')&&transitionSection.includes('data-ref="node:B"'));
  assert(transitionSection.includes('data-ref="report:1"')&&transitionSection.includes('Cycle 1 · 支持'));
  assert(!transitionSection.includes('theory-master/research-transition')&&!transitionSection.includes('"sources"'));
  const card=elements['ex-outcome-cards'].children.find(c=>c.dataset.nodeId==='C');
  assert(card.children[0].children.some(part=>part.textContent==='Interval-dependent covariance'));
  assert(card.children[0].children.some(part=>part.className==='ex-outcome-transition'&&part.textContent.includes('Vary observation interval')));
  const sourceStrip=elements['ex-source-rail'].children.find(strip=>strip.dataset.nodeId==='C');
  assert(sourceStrip.children[0].title.includes('为什么现在研究：The last bounded comparison'));
  latestGraph.events['edge:pointerover']({target:{id:'AC'}});await tick();
  assert(elements['ex-edge-menu-action'].title.includes('Vary observation interval < 2 seconds'));
  assert.equal(JSON.stringify(nodes),before,'rendering does not alter the frozen object or science');
  const reportLink={dataset:{ref:'report:1'}};
  elements['ex-reader'].listeners.click({target:{closest:selector=>selector==='[data-ref]'?reportLink:null}});
  await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'report');
  assert.equal(history.state.tmExplorerView.selected.id,'1');
  assert(elements['ex-reader'].innerHTML.includes('Frozen source report body.'));
  // The same delegated node navigation handles the source links in the reader.
  const sourceLink={dataset:{ref:'node:A'}};
  elements['ex-reader'].listeners.click({target:{closest:selector=>selector==='[data-ref]'?sourceLink:null}});
  await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'A');
  assert(!elements['ex-reader'].innerHTML.includes('ex-research-transition'),
    'an explicit source-free record does not give the initial hypothesis a predecessor rationale');
  assert(!elements['ex-reader'].innerHTML.includes('Start from interval-dependent covariance'));
  assert.equal(JSON.stringify(nodes),before,'hidden root motivation remains in the scientific record');
  // Another source-free root has no transition record and is not first in node order.
  latestGraph.events['node:click']({target:{id:'B'}});await tick();await tick();
  assert(!elements['ex-reader'].innerHTML.includes('ex-research-transition'));
  assert(!elements['ex-reader'].innerHTML.includes('承接理由未记录。'));
  // A recorded supplementary H source remains visible in the reader, while
  // external references and edges from external nodes are not H predecessors.
  nodes[4].semantic={type:'External',title:'Background reference'};
  edges.push({id:'A-ref-B',fromNode:'A',toNode:'B',edge_type:'external_reference'},
    {id:'C-source-B',fromNode:'C',toNode:'B',type:'research_source'},
    {id:'X-context-B',fromNode:'X',toNode:'B'});
  latestGraph.events['node:click']({target:{id:'B'}});await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('data-ref="node:C"'));
  assert(!elements['ex-reader'].innerHTML.includes('data-ref="node:A"'));
  assert(!elements['ex-reader'].innerHTML.includes('data-ref="node:X"'));
  edges.splice(-3);
  // A legacy H keeps real predecessor links but receives no invented cause.
  delete nodes[2].semantic.research_transition;
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  const legacy=elements['ex-reader'].innerHTML;
  assert(legacy.includes('承接理由未记录。')&&legacy.includes('data-ref="node:A"'));
  assert(!legacy.includes('前序留下的问题')&&!legacy.includes('The last bounded comparison'));
  // Unadjudicated is distinct from Open and from failure; why_now is a card fallback.
  nodes[2].semantic.research_transition=transition([
    {hypothesis_id:'A',role:'main',title:'Unadjudicated source',scientific_status:'none',
      outcome_refs:[{id:'partial',cycle_id:'7',verdict:'unadjudicated',report_ref:'notes/outcomes/7.md'}]}
  ],'');
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  const unfinished=elements['ex-reader'].innerHTML;
  assert(unfinished.includes('整体猜想：尚无整体裁决')&&unfinished.includes('Cycle 7 · 未裁决'));
  assert(!unfinished.includes('整体猜想：尚无法判定')&&!unfinished.includes('失败'));
  const fallbackCard=elements['ex-outcome-cards'].children.find(c=>c.dataset.nodeId==='C');
  assert(fallbackCard.children[0].children.some(part=>part.className==='ex-outcome-transition'&&part.textContent.includes('last bounded comparison')));
  // A historical normalization to Open is not an original Judge Open verdict.
  nodes[2].semantic.research_transition.sources[0].outcome_refs=[
    {id:'legacy-milestone',cycle_id:'8',verdict:'open',original_verdicts:['milestone_advanced'],report_ref:'notes/outcomes/8.md'}
  ];
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  const historical=elements['ex-reader'].innerHTML;
  assert(historical.includes('Cycle 8 · 历史裁决：milestone_advanced'));
  assert(!historical.includes('Cycle 8 · 尚无法判定'),'historical milestone must not become Judge Open');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_supplementary_sources_stay_in_reader_without_graph_ports_or_edges():
    bootstrap, marker, _ = HARNESS.partition("  onReady();await tick();")
    assert marker
    script = bootstrap + r"""
  edges[0].lineage_role='main';
  edges[1].edge_type='research_source';edges[1].lineage_role='other';
  nodes[2].semantic={type:'Hypothesis',title:'Combined hypothesis'};
  onReady();await tick();await tick();
  const line=id=>latestGraph.data.edges.find(e=>e.id===id);
  assert(line('AC')&&!line('BC'),'only the main predecessor is drawn');
  assert.deepEqual([...line('AC').style.lineDash],[]);
  const card=latestGraph.data.nodes.find(n=>n.id==='C');
  assert.deepEqual(Array.from(card.style.ports.filter(p=>p.key.startsWith('in:')),p=>p.key),['in:AC']);
  const strip=elements['ex-source-rail'].children.find(s=>s.dataset.nodeId==='C');
  assert.equal(strip.children.length,1,'only the drawn predecessor gets a rail entry');
  const positions=JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y]));
  const renders=latestGraph.renders;
  latestGraph.events['node:click']({target:{id:'C'}});await tick();await tick();
  assert(!line('BC'),'focusing the target does not draw a supplementary edge');
  const html=elements['ex-reader'].innerHTML;
  assert(html.includes('data-ref="node:A"')&&html.includes('data-ref="node:B"'),
    'legacy records retain both navigable sources in the reader');
  assert(html.includes('主要承接')&&html.includes('补充来源'));
  assert.equal(line('AC').style.visibility,'visible');
  assert.equal(latestGraph.renders,renders,'focus does not rebuild the tree');
  assert.equal(JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y])),positions);
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  assert(!line('BC'));
  assert.equal(JSON.stringify(latestGraph.data.nodes.map(n=>[n.id,n.style.x,n.style.y])),positions);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
