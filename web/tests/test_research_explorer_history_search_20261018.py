"""Deterministic regressions for bounded Explorer history and search races."""
from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / 'tm_observer' / 'research_explorer_g6.js'
BASE_TEST = TEST_DIR / 'test_research_explorer_g6.py'


def _base_harness() -> str:
    tree = ast.parse(BASE_TEST.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'HARNESS' for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('G6 test harness not found')


def _with_extra_js(extra: str) -> str:
    harness = _base_harness()
    marker = "\n})().catch(e=>{console.error(e);process.exitCode=1});"
    if marker not in harness:
        raise AssertionError('G6 harness completion marker changed; update this regression harness')
    head, tail = harness.rsplit(marker, 1)
    return head + '\n' + extra + marker + tail


@unittest.skipUnless(shutil.which('node'), 'requires Node.js')
class ExplorerHistorySearchRegressionTests(unittest.TestCase):
    def _run(self, extra: str) -> None:
        process = subprocess.run(['node', '-e', _with_extra_js(extra), str(SCRIPT)],
                                 capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_history_indices_remain_stable_after_more_than_seventy_views(self):
        self._run(r"""
  const oldBrowserIndex=3;
  assert(pageHistory[oldBrowserIndex]?.state?.tmExplorerView,'fixture needs an earlier Explorer view');
  for(let i=0;i<85;i++){
    latestGraph.events['node:click']({target:{id:i%2?'A':'C'}});
    await tick();await tick();
  }
  assert(pageIndex-oldBrowserIndex>70,'stress run must evict the old in-memory trail entry');
  const expectedOldSeq=pageHistory[oldBrowserIndex].state.tmExplorerIndex;
  const nextSelected=pageHistory[oldBrowserIndex+1].state.tmExplorerView.selected.id;
  history.go(oldBrowserIndex-pageIndex);
  await tick();await tick();await tick();
  assert.equal(pageIndex,oldBrowserIndex);
  assert.equal(history.state.tmExplorerIndex,expectedOldSeq);
  assert.equal(elements['ex-back'].disabled,false,'old browser history entry retains a valid back direction');
  assert.equal(elements['ex-forward'].disabled,false,'old browser history entry retains a valid forward direction');
  elements['ex-forward'].onclick();await tick();await tick();await tick();
  assert.equal(pageIndex,oldBrowserIndex+1);
  assert.equal(history.state.tmExplorerView.selected.id,nextSelected,'forward restores the adjacent browser snapshot');
""")

    def test_late_search_response_cannot_replace_newer_query(self):
        self._run(r"""
  const query=elements['ex-query'],box=elements['ex-results'],pending=[];
  const nativeSetTimeout=ctx.setTimeout;
  ctx.setTimeout=(fn,delay)=>nativeSetTimeout(fn,delay===250?0:delay);
  ctx.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
  query.value='old-search';query.listeners.input();await tick();await tick();
  assert.equal(pending.length,1,'first debounced request started');
  query.value='new-search';query.listeners.input();await tick();await tick();
  assert.equal(pending.length,2,'second debounced request started');
  pending[1].resolve({ok:true,json:async()=>({results:[{kind:'doc',id:'new',label:'NEW_RESULT',excerpt:'new excerpt'}],partial:true})});
  await tick();await tick();
  assert(box.children.some(child=>String(child.textContent||'').includes('NEW_RESULT')),'new query is displayed');
  assert(box.children.some(child=>child.className==='ex-search-warning'),'partial-index warning is visible');
  pending[0].resolve({ok:true,json:async()=>({results:[{kind:'doc',id:'old',label:'OLD_RESULT',excerpt:'old excerpt'}],partial:false})});
  await tick();await tick();
  assert(box.children.some(child=>String(child.textContent||'').includes('NEW_RESULT')),'late old response cannot replace new results');
  assert(!box.children.some(child=>String(child.textContent||'').includes('OLD_RESULT')),'stale result is discarded');
""")


if __name__ == '__main__':
    unittest.main()
