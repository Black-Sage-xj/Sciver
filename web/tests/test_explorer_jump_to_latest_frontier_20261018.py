"""The complete graph expands branches, fits records and preserves navigation."""
from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / 'tm_observer' / 'research_explorer_g6.js'
BASE_TEST = TEST_DIR / 'test_research_explorer_g6.py'


def _harness_with(extra: str) -> str:
    tree = ast.parse(BASE_TEST.read_text(encoding='utf-8'))
    harness = next(ast.literal_eval(node.value) for node in tree.body if
                   isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == 'HARNESS'
                       for target in node.targets))
    marker = '\n})().catch(e=>{console.error(e);process.exitCode=1});'
    assert marker in harness
    return harness.replace(marker, '\n' + extra + marker)


@unittest.skipUnless(shutil.which('node'), 'requires Node.js')
class ExplorerFrontierJumpTests(unittest.TestCase):
    def _run(self, extra: str) -> None:
        result = subprocess.run(['node', '-e', _harness_with(extra), str(SCRIPT)],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_complete_graph_expands_closed_and_parked_branches_without_changing_selection(self):
        self._run(r"""
  const assertFullGraph=()=>{
    assert.deepEqual(Array.from(latestGraph.getNodeData(),n=>n.id).sort(),nodes.map(n=>n.id).sort());
    for(const node of latestGraph.getNodeData())
      assert.equal(node.style.visibility,'visible','full graph preserves '+node.id);
  };
  nodes.push(
    {id:'H-0060',label:'historical parked',text:'# Historical',
      semantic:{type:'Hypothesis',frontier_status:'parked',cycle:60}},
    {id:'H-0070',label:'older active',text:'# Older active',
      semantic:{type:'Hypothesis',frontier_status:'active',cycle:70}},
    {id:'H-0089',label:'later active',text:'# Later active',
      semantic:{type:'Hypothesis',frontier_status:'active',cycle:89}},
    {id:'H-0090',label:'newer H but closed',text:'# Closed',
      semantic:{type:'Hypothesis',frontier_status:'closed',cycle:160}}
  );
  edges.push({id:'DH70',fromNode:'D',toNode:'H-0070'},
             {id:'DH89',fromNode:'D',toNode:'H-0089'});
  await elements['ex-refresh'].onclick();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'H-0060'}});await tick();await tick();
  assertFullGraph();
  latestGraph.events['edge:pointerover']({target:{id:'AC'},originalEvent:{clientX:400,clientY:200}});
  await elements['ex-edge-menu-action'].onclick();await tick();
  assert.equal(latestGraph.getNodeData('H-0070').style.visibility,'hidden');
  assert.equal(latestGraph.getNodeData('H-0089').style.visibility,'hidden');
  assert.equal(latestGraph.getNodeData('H-0090').style.visibility,'visible',
    'closed sibling is still visible when an active branch is folded');
  const beforeCompleteIndex=pageIndex,beforeFits=latestGraph.fitCalls;
  await elements['ex-all'].onclick();await tick();await tick();
  assertFullGraph();
  assert.equal(latestGraph.fitCalls,beforeFits+1,'complete graph fits all records');
  assert.equal(history.state.tmExplorerView.selected.id,'H-0060',
    'complete graph retains historical selection rather than jumping to the newest node');
  assert(location.hash.includes('node%3AH-0060'));
  assert.equal(pageIndex,beforeCompleteIndex+1,'expanding branches preserves browser back history');
  history.go(-1);await tick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'H-0060');
  assert.equal(latestGraph.getNodeData('H-0070').style.visibility,'hidden',
    'back restores the folded branch snapshot');
  await elements['ex-all'].onclick();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'H-0070'}});await tick();await tick();
  assertFullGraph();
  const olderFits=latestGraph.fitCalls;
  await elements['ex-all'].onclick();await tick();await tick();
  assertFullGraph();
  assert.equal(history.state.tmExplorerView.selected.id,'H-0070');
  assert.equal(latestGraph.fitCalls,olderFits+1,'repeat complete-graph clicks still fit');
""")

    def test_without_active_frontier_complete_graph_still_expands_and_fits(self):
        self._run(r"""
  nodes.forEach(n=>{if(n.semantic?.frontier_status==='active')n.semantic.frontier_status='parked'});
  await elements['ex-refresh'].onclick();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'B'}});await tick();await tick();
  latestGraph.events['edge:pointerover']({target:{id:'AC'},originalEvent:{clientX:400,clientY:200}});
  await elements['ex-edge-menu-action'].onclick();await tick();
  assert.equal(latestGraph.getNodeData('D').style.visibility,'hidden');
  const beforeFits=latestGraph.fitCalls;
  await elements['ex-all'].onclick();await tick();await tick();
  assert.deepEqual(Array.from(latestGraph.getNodeData(),n=>n.id).sort(),nodes.map(n=>n.id).sort());
  for(const node of latestGraph.getNodeData())
    assert.equal(node.style.visibility,'visible','parked graphs still show '+node.id);
  assert.equal(latestGraph.fitCalls,beforeFits+1);
  assert.equal(history.state.tmExplorerView.selected.id,'B');
  assert.equal(elements['ex-error'].hidden,true);
""")


if __name__ == '__main__':
    unittest.main()
