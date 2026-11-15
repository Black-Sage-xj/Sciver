"""Search requests must be latest-query-wins in the G6 browser UI."""
from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
SCRIPT = TEST_DIR.parent / 'tm_observer' / 'research_explorer_g6.js'
BASE_TEST = TEST_DIR / 'test_research_explorer_g6.py'


def _harness_with_search_race() -> str:
    tree = ast.parse(BASE_TEST.read_text(encoding='utf-8'))
    base = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'HARNESS' for target in node.targets))
    marker = "\n})().catch(e=>{console.error(e);process.exitCode=1});"
    if marker not in base:
        raise AssertionError('G6 harness completion marker changed')
    head, tail = base.rsplit(marker, 1)
    extra = r"""
  const query=elements['ex-query'],box=elements['ex-results'],pending=[];
  const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
  const nativeSetTimeout=ctx.setTimeout;
  ctx.setTimeout=(fn,delay)=>nativeSetTimeout(fn,delay===250?0:delay);
  ctx.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
  query.value='old-search';query.listeners.input();await tick();await tick();
  assert.equal(pending.length,1,'first debounced request started');
  query.value='new-search';query.listeners.input();await tick();await tick();
  assert.equal(pending.length,2,'second debounced request started');
  pending[1].resolve({ok:true,json:async()=>({results:[{kind:'doc',id:'new',label:'NEW_RESULT',excerpt:'new excerpt'}],partial:true})});
  await tick();await tick();
  assert(descendants(box).some(child=>String(child.textContent||'').includes('NEW_RESULT')),'new query is displayed');
  assert(descendants(box).some(child=>child.className==='ex-search-warning'),'partial-index warning is visible');
  pending[0].resolve({ok:true,json:async()=>({results:[{kind:'doc',id:'old',label:'OLD_RESULT',excerpt:'old excerpt'}],partial:false})});
  await tick();await tick();
  assert(descendants(box).some(child=>String(child.textContent||'').includes('NEW_RESULT')),'late old response cannot replace new results');
  assert(!descendants(box).some(child=>String(child.textContent||'').includes('OLD_RESULT')),'stale result is discarded');
  index.nodes.push({id:'H-0012',label:'Exact node',semantic:{type:'Hypothesis'},text:'# Exact node'});
  query.value='12';query.listeners.input();await tick();await tick();
  assert.equal(pending.length,3);
  pending[2].resolve({ok:true,json:async()=>({results:[
    {kind:'node',id:'H-0012',label:'Exact node',excerpt:'12 in content'},
    {kind:'doc',id:'notes/other.md',label:'Other 12',excerpt:'content 12'}
  ],partial:false})});
  await tick();await tick();
  assert.equal(box.children[0].children[0].textContent,'节点编号');
  assert(box.children[0].children.some(child=>String(child.textContent||'').includes('H-0012')));
  assert.equal(box.children[1].children[0].textContent,'内容匹配');
  assert(box.children[1].children.some(child=>String(child.textContent||'').includes('Other 12')));
  assert(!box.children[1].children.some(child=>String(child.textContent||'').includes('Exact node')),
    'an exact node appears only in the node-number section');
"""
    return head + '\n' + extra + marker + tail


@unittest.skipUnless(shutil.which('node'), 'requires Node.js')
class ExplorerSearchRaceTests(unittest.TestCase):
    def test_late_search_response_cannot_replace_newer_query(self):
        process = subprocess.run(['node', '-e', _harness_with_search_race(), str(SCRIPT)],
                                 capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == '__main__':
    unittest.main()
