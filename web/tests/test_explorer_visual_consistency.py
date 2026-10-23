"""Regression for uniform Research Explorer link strokes and uncluttered toolbar."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tm_observer.research_explorer_g6 import graph_content
from tm_observer.research_explorer_ui import EXPLORER_HTML
from test_research_explorer_g6 import HARNESS, SCRIPT


class ExplorerVisualConsistencyTest(unittest.TestCase):
    def test_default_toolbar_has_no_timestamp_or_legacy_link(self):
        page = graph_content()
        self.assertIn('id="ex-stats"', page)
        self.assertIn("index.stats.doc_count+' 文档'", page)
        self.assertNotIn("当前文件（非历史快照）", page)
        self.assertNotIn("旧版坐标视图", page)
        self.assertNotIn("ex-legacy", page)
        self.assertNotIn("toLocaleTimeString", page)
        self.assertNotIn("当前文件（非历史快照）", EXPLORER_HTML)
        self.assertNotIn("toLocaleTimeString", EXPLORER_HTML)
        self.assertNotIn("不提供历史版本", EXPLORER_HTML)

    def test_search_actions_and_branch_counts_share_one_toolbar(self):
        from bs4 import BeautifulSoup
        page = graph_content()
        soup = BeautifulSoup(page, 'html.parser')
        toolbar = soup.select_one('#ex-workspace > .ex-toolbar')
        self.assertIsNotNone(toolbar)
        for control in ('ex-query', 'ex-refresh', 'ex-fullscreen', 'ex-stats',
                        'ex-tip-summary', 'ex-type-legend'):
            self.assertIsNotNone(toolbar.select_one('#' + control), control)
        self.assertEqual(len(soup.select('#ex-workspace > .ex-toolbar')), 1)
        self.assertFalse(soup.select('#ex-workspace > .ex-overview'),
                         'there must not be a second statistics toolbar')
        self.assertEqual(len(soup.select('#ex-workspace > .ex-layout')), 1)
        self.assertIn('flex-wrap:wrap',page)

    @unittest.skipUnless(shutil.which("node"), "requires Node.js")
    def test_reference_and_scientific_edges_have_identical_solid_dash_pattern(self):
        # Reuse the real G6 data-builder test harness and introduce a separate
        # reference edge after existing graph/navigation assertions complete.
        addition = r"""
  const sourceEdge = edges.find(e=>e.id==='AX');
  sourceEdge.edge_type = 'external_reference';
  await elements['ex-refresh'].onclick();await tick();
  const reference = latestGraph.data.edges.find(e=>e.id==='AX');
  const scientific = latestGraph.data.edges.find(e=>e.id==='BC');
  assert(reference&&scientific,'both edge semantics remain visible');
  assert.deepEqual(reference.style.lineDash,scientific.style.lineDash);
  assert.equal(reference.style.lineDash.length,0,'solid pattern is stable for overlapping routes');
  // A reference link can be clicked, but cannot extend scientific lineage.
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.opacity,.28,
    'reference link cannot masquerade as a downstream scientific relationship');
  assert.equal(latestGraph.data.nodes.find(n=>n.id==='X').style.fillOpacity,1);
  latestGraph.events['edge:click']({target:{id:'AX'}});await tick();await tick();
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.stroke,'#fef08a',
    'reference link is still individually clickable and highlighted');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.lineWidth,5);
  assert.equal(history.state.tmExplorerView.selected.kind,'edge');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AC').style.opacity,.28);
  // Direct link opened in a new tab also restores selected-edge highlighting.
  const savedEdge=history.state.tmExplorerView;
  await elements['ex-refresh'].onclick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'edge');
  assert.equal(history.state.tmExplorerView.selected.id,savedEdge.selected.id);
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.stroke,'#fef08a');
  assert.equal(reference.style.endArrow,true);
  assert.equal(scientific.style.endArrow,true);
  """
        needle = "  console.log('G6 view model test:"
        self.assertIn(needle, HARNESS)
        script = HARNESS.replace(needle, addition + "\n" + needle)
        result = subprocess.run(
            ["node", "-e", script, str(SCRIPT)],
            capture_output=True, text=True, timeout=12, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
