"""External identity and discussion edges stay distinct from scientific lineage."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from test_research_explorer_g6 import HARNESS, SCRIPT


ADDITIONAL = r"""
  const first={id:'H-0002',label:'Recorded research goal',semantic:{type:'Hypothesis',cycle:2}};
  const next={id:'H-0003',label:'Recorded next proposal',semantic:{type:'Hypothesis',cycle:3}};
  const human={id:'human-direction-ui',label:'Directional control design',semantic:{
    type:'External',source:'human',title:'Directional control design',external:{
      sequence:8,display_id:'E8',title:'Directional control design',
      created_at:'2026-11-19T08:41:28+00:00',delivery_status:'consumed',proposal_cycle_id:'3',
      hypothesis:'Compare directions. <script>untrusted()</script>',
      decisive_test:'Hold the model and disorder fixed.',reason:'Follow the recorded research goal.',
      event_history:[{status:'consumed',timestamp:'2026-11-19T08:42:00+00:00',detail:{cycle_id:'3'}}]
    }},text:'type: External\nid: human-direction-ui\nsource_cycle: 99\n\n# Directional control design'};
  nodes.push(first,next,human);
  const main={id:'ui-science',fromNode:first.id,toNode:next.id,edge_type:'hypothesis_evolution'};
  const input={id:'ui-input',fromNode:next.id,toNode:human.id,edge_type:'discussion_context',
    relation:'proposal_delivery',label:'本轮讨论输入',reason:'Recorded delivery to Cycle 3.',display_primary:true};
  const target={id:'ui-target',fromNode:first.id,toNode:human.id,edge_type:'discussion_context',
    relation:'body_mention',label:'讨论目标',reason:'The human direction names this goal.',display_primary:false};
  edges.push(main,input,target);
  await ctx.window.testReloadIndex();await tick();await tick();
  await elements['ex-all'].onclick();await tick();await tick();
  const view=ctx.window.testExternalInternals;
  assert.equal(view.isScientificEdge(input),false);
  assert.equal(view.isScientificEdge(target),false);
  assert.equal(view.isScientificEdge(main),true);
  const descendants=view.downstreamHighlight(first.id,[main,input,target]);
  assert(descendants.ids.has(next.id));
  assert(!descendants.ids.has(human.id),'discussion input cannot become a scientific successor');
  const tree=view.structure(nodes,edges);
  assert.equal(tree.parents.get(human.id)[0],next.id,'delivery anchors placement');
  assert(!tree.parents.get(human.id).includes(first.id),'secondary target does not reparent the input');
  for(const edge of [input,target]){
    const rendered=latestGraph.data.edges.find(e=>e.id===edge.id);
    assert(rendered,'both evidenced relations must be rendered');
    assert.deepEqual(Array.from(rendered.style.lineDash),[8,5]);
    assert.equal(rendered.style.endArrow,false,'discussion association is not a directed derivation');
    assert.equal(rendered.style.labelText,edge.label);
  }
  assert.equal(latestGraph.getNodeData(human.id).data.compactLabel,'E8',
    'stable external identity is independent of source Cycle 99');
  assert.equal(latestGraph.getNodeData(human.id).data.placementCycle,99);
  latestGraph.events['node:click']({target:{id:human.id}});await tick();await tick();
  const html=elements['ex-reader'].innerHTML;
  assert(html.includes('E8 · Directional control design'));
  assert(html.includes('已送入本轮讨论'));
  assert(html.includes('2026/11/19 16:41:28 北京时间'));
  assert(html.includes('判别检验'));
  assert(html.includes('&lt;script&gt;untrusted()&lt;/script&gt;'));
  assert(!html.includes('<script>untrusted()'));
  assert(html.indexOf('相关节点')<html.indexOf('<dt>研究方向'),
    'related graph nodes remain visible ahead of long scientific text');
  assert(html.includes('查看连线依据'));
  latestGraph.events['node:click']({target:{id:next.id}});await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('本轮讨论输入'),
    'the formal H also exposes the associated human direction');
"""


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required")
def test_external_identity_reader_and_non_scientific_associations():
    code = HARNESS.replace(
        "window.testReloadIndex=()=>{rememberBefore();return load()};",
        "window.testExternalInternals={isScientificEdge,downstreamHighlight,structure};"
        "window.testReloadIndex=()=>{rememberBefore();return load()};",
    )
    needle = "  console.log('G6 view model test:"
    assert needle in code
    code = code.replace(needle, ADDITIONAL + "\n" + needle)
    proc = subprocess.run(
        ["node", "-e", code, str(SCRIPT)], capture_output=True, text=True, timeout=25,
    )
    assert proc.returncode == 0, proc.stderr
