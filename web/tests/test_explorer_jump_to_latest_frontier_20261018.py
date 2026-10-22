"""The Explorer's Research Frontier button navigates, not only filters."""
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

    def test_button_finds_rightmost_full_graph_node_even_if_closed_or_filtered(self):
        self._run(r"""
  nodes.push(
    {id:'H-0060',label:'historical parked',text:'# Historical',
      semantic:{type:'Hypothesis',frontier_status:'parked',cycle:60}},
    {id:'H-0070',label:'older active',text:'# Older active',
      semantic:{type:'Hypothesis',frontier_status:'active',cycle:70,
        latest_cycle_evaluation:{cycle_id:'120'}}},
    {id:'H-0089',label:'most recently studied active',text:'# Latest active',
      semantic:{type:'Hypothesis',frontier_status:'active',cycle:89,
        latest_cycle_evaluation:{cycle_id:'133'}}},
    {id:'H-0090',label:'newer H but closed',text:'# Closed',
      semantic:{type:'Hypothesis',frontier_status:'closed',cycle:160}}
  );
  edges.push({id:'DH70',fromNode:'D',toNode:'H-0070'},
             {id:'DH89',fromNode:'D',toNode:'H-0089'});
  await elements['ex-refresh'].onclick();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'H-0060'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.mode,'all','opening parked deep-link reveals the full graph');
  const beforeFrontierIndex=pageIndex;
  await elements['ex-frontier'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.mode,'all');
  assert.equal(history.state.tmExplorerView.selected.id,'H-0090');
  assert.equal(latestGraph.focused,'H-0090','viewport must center the rightmost node');
  assert(location.hash.includes('node%3AH-0090'));
  assert.equal(pageIndex,beforeFrontierIndex+1,'new selection must preserve browser back history');
  history.go(-1);await tick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'H-0060','back goes to historical node');
  assert.equal(history.state.tmExplorerView.mode,'all','back restores original visibility mode');
  await elements['ex-frontier'].onclick();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'H-0070'}});await tick();await tick();
  assert.equal(history.state.tmExplorerView.mode,'all','older active H remains in the full graph');
  await elements['ex-frontier'].onclick();await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.id,'H-0090',
    'clicking again must still locate the rightmost node');
  assert.equal(latestGraph.focused,'H-0090');
""")

    def test_without_active_frontier_button_still_jumps_in_full_graph(self):
        self._run(r"""
  nodes.forEach(n=>{if(n.semantic?.frontier_status==='active')n.semantic.frontier_status='parked'});
  await elements['ex-refresh'].onclick();await tick();await tick();
  await elements['ex-all'].onclick();await tick();
  assert.equal(history.state.tmExplorerView.mode,'all');
  await elements['ex-frontier'].onclick();await tick();
  assert.equal(history.state.tmExplorerView.mode,'all');
  assert.equal(history.state.tmExplorerView.selected.id,'B');
  assert.equal(elements['ex-error'].hidden,true);
""")


if __name__ == '__main__':
    unittest.main()
