"""Fresh Explorer entries focus the latest scientific Cycle without filtering."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from test_research_explorer_g6 import HARNESS, SCRIPT

@unittest.skipUnless(shutil.which("node"), "requires Node.js")
class ExplorerFrontierJumpTests(unittest.TestCase):
    def _run(self, setup: str, expected_focus: str, expected_selected: str) -> None:
        prefix = HARNESS.split("  onReady();await tick();", 1)[0]
        script = prefix + setup + f"""
  onReady();await tick();await tick();
  assert.equal(elements['ex-all']['aria-pressed'],'true');
  assert.equal(history.state.tmExplorerView.mode,'all');
  assert.equal(latestGraph.focused,'{expected_focus}');
  assert.equal(history.state.tmExplorerView.selected?.id??null,{expected_selected});
  assert.equal(elements['ex-error'].hidden,true);
}})().catch(e=>{{console.error(e);process.exitCode=1}});
"""
        result = subprocess.run(["node", "-e", script, str(SCRIPT)],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_entry_prefers_latest_frozen_hypothesis_over_newer_external(self):
        self._run(r"""
  nodes.push({id:'H-current',semantic:{type:'Hypothesis',cycle:90,frontier_status:'closed'},text:'# Current H'},
             {id:'external-newer',semantic:{type:'External'},text:'source_cycle: 999\n# Reference'});
  location.hash='';
""", "H-current", "null")

    def test_explicit_historical_link_wins_over_latest_cycle(self):
        self._run(r"""
  nodes.push({id:'H-current',semantic:{type:'Hypothesis',cycle:90,frontier_status:'active'},text:'# Current H'});
  location.hash='#node%3AA';
""", "A", "'A'")

if __name__ == "__main__":
    unittest.main()
