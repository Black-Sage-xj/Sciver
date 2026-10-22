"""Regression: optional compact-number overlay must not abort G6 render."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from tm_observer.research_explorer_g6 import graph_content
from test_research_explorer_g6 import HARNESS, SCRIPT


def test_server_render_contains_compact_overlay_and_inline_script():
    page = BeautifulSoup(graph_content(), 'html.parser')
    self_contained = page.select_one('#ex-g6')
    assert self_contained is not None
    assert page.select_one('#ex-compact-labels[hidden]') is not None
    assert page.select_one('#ex-error[hidden]') is not None
    assert 'function compactLabelHost()' in str(page)


@pytest.mark.skipif(shutil.which('node') is None, reason='requires Node.js')
def test_loading_historical_markup_without_overlay_recovers():
    """Model an old open browser tab receiving newer graph JS without overlay HTML."""
    old = 'getElementById:elem,fullscreenElement:null'
    replacement = "getElementById:id=>id==='ex-compact-labels'?(elements[id]||null):elem(id),fullscreenElement:null"
    assert old in HARNESS
    setup_point = "vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
    assert setup_point in HARNESS
    setup = r"""
// Legacy markup has no #ex-compact-labels element. Keep normal DOM emulation
// but register on-demand children by id once the graph root mounts them.
const oldAppend=elem('ex-graph').append;
elem('ex-graph').append=function(child){
  oldAppend.call(this,child);
  if(child.id)elements[child.id]=child;
};
const oldCreate=ctx.document.createElement;
ctx.document.createElement=function(tag){
  const child=oldCreate(tag);
  child.replaceChildren=function(){this.children=[];};
  return child;
};
"""
    script = HARNESS.replace(old, replacement, 1).replace(setup_point, setup+'\n'+setup_point, 1)
    test_point = "  const initialRenders=latestGraph.renders;"
    assert test_point in script
    script = script.replace(test_point,
                            "  assert(elements['ex-compact-labels'],'missing overlay is remounted');\n"
                            "  assert.equal(elements['ex-error'].hidden,true,'graph renders without error');\n"
                            "  assert(latestGraph.renders>0,'timeline graph still renders');\n"
                            +test_point,1)
    result = subprocess.run(['node','-e',script,str(SCRIPT)],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
