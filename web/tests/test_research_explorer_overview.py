"""Compact grouped status overview; deterministic DOM/data checks only."""
import shutil
import subprocess

from bs4 import BeautifulSoup
import pytest

from tm_observer.research_explorer_g6 import graph_content
from test_research_explorer_g6 import HARNESS, SCRIPT


def test_overview_separates_actions_and_groups_and_keeps_warning_accessible():
    soup = BeautifulSoup(graph_content(), 'html.parser')
    toolbar = soup.select_one('#ex-workspace > .ex-toolbar')
    assert toolbar.select_one('.ex-toolbar-main #ex-query')
    assert toolbar.select_one('.ex-toolbar-main #ex-fullscreen')
    assert not toolbar.select_one('#ex-layout-c, #ex-layout-original')
    assert toolbar.select_one('.ex-toolbar-main #ex-stats')
    assert toolbar.select_one('.ex-overview #ex-tip-summary')
    assert toolbar.select_one('.ex-overview #ex-type-legend')
    warning = toolbar.select_one('#ex-index-warning')
    assert warning.has_attr('hidden')
    assert warning['aria-label']
    assert '读取预算' in warning['title']
    # Header controls open overlays; history/zoom stay at the canvas edges.
    assert not soup.select('.ex-page-head')
    for menu_id in ('ex-branches', 'ex-legend-menu'):
        menu = toolbar.select_one('#' + menu_id)
        assert menu.name == 'details' and not menu.has_attr('open')
        assert menu.select_one('summary')
    assert toolbar.select_one('#ex-branches #ex-tip-panel')
    assert not soup.select('#ex-refresh, #ex-view-menu')
    for control in ('ex-fit', 'ex-expand-all', 'ex-view-color'):
        element = toolbar.select_one('#' + control)
        assert element and not element.find_parent('details'), control
    assert toolbar.select_one('#ex-legend-menu > summary #ex-stats')
    assert toolbar.select_one('#ex-legend-menu #ex-type-legend')
    assert not soup.select('#ex-graph .ex-toolbar-menu')
    assert soup.select_one('#ex-graph .ex-zoom-controls #ex-zoom-percent')
    assert soup.select_one('#ex-workspace > #ex-results')
    assert graph_content().index('id="ex-help-open"') < graph_content().index('id="ex-help-script"')



def test_frontier_keys_are_not_duplicated_and_every_outcome_is_counted_once():
    if not shutil.which('node'):
        pytest.skip('requires Node.js')
    prefix = HARNESS.split('  onReady();await tick();', 1)[0]
    script = prefix + r"""
  const N=(id,status,extra={})=>({id,label:id,text:'# '+id,
    semantic:{type:'Hypothesis',cycle:Number(id.slice(2)),frontier_status:status,...extra}});
  nodes.splice(0,nodes.length,
    N('H-1','active',{outcome:{verdict:'supported'}}),
    N('H-2','parked',{outcome:{verdict:'rejected'}}),
    N('H-3','closed',{outcome:{verdict:'open',cycle_id:'3'}}),
    N('H-4',null,{outcome:{verdict:'open',cycle_id:'4',original_verdicts:['partial']}}),
    N('H-5',null,{cycle_outcomes:[{verdict:'supported',cycle_id:'5'},{verdict:'rejected',cycle_id:'6'}]}),
    N('H-6',null,{cycle_outcomes:[{verdict:'unadjudicated',review_state:'unadjudicated',cycle_id:'7'}]}),
    N('H-7',null),
    {id:'E1',label:'external',semantic:{type:'External'}});
  edges.splice(0,edges.length);
  index.canvas='science_graph.sqlite3';
  index.stats={node_count:8,edge_count:0,doc_count:10,partial:true};
  onReady();await tick();await tick();
  const summary=elements['ex-tip-summary'],legend=elements['ex-type-legend'];
  assert.equal(summary.children[0].textContent,'前沿');
  assert.equal(summary.children.filter(x=>x.tagName==='BUTTON').length,5);
  assert(summary.children.some(x=>x.textContent==='全部 7'));
  assert(summary.children.some(x=>x.textContent==='活跃 1'));
  assert(summary.children.some(x=>x.textContent==='未标记 4'));
  const primary=legend.children.filter(x=>x.className==='ex-type-key');
  assert.deepEqual(primary.map(x=>x.children[1].textContent),['支持 1','否决 1','Open 1','待裁决 1']);
  assert(!legend.children.some(x=>['H · 前沿','前沿颜色'].includes(x.textContent)),
    'frontier color keys are already in clickable status tabs');
  const more=legend.children.find(x=>x.id==='ex-outcome-more');
  assert(more&&!more.open,'rare verdicts are collapsed by default');
  assert.equal(more.children[0].textContent,'更多 3');
  assert(more.children[0].title.includes('研究中止 · 未裁决 1'));
  const extra=more.children[1].children.map(x=>x.children[1].textContent);
  assert.deepEqual(extra,['多轮结论不一 1','历史归一 1','研究中止 · 未裁决 1']);
  assert.equal(primary.length+extra.length,7,'all seven outcome classes count each H once');
  const types=legend.children.find(x=>x.className==='ex-overview-types');
  assert.equal(types.children[0].children[1].textContent,'外部资料 1');
  assert(!elements['ex-stats'].textContent.includes('读取预算'));
  assert.equal(elements['ex-index-warning'].hidden,false,
    'compact statistics still expose partial-index uncertainty');
  legend.querySelector=selector=>selector==='details'?more:null;
  more.open=true;
  latestGraph.events.aftertransform();
  assert(elements['ex-type-legend'].children.find(x=>x.id==='ex-outcome-more').open,
    'an open more panel survives zoom updates');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_toolbar_color_control_is_unique_after_index_reload():
    if not shutil.which('node'):
        pytest.skip('requires Node.js')
    prefix = HARNESS.split('  onReady();await tick();', 1)[0]
    script = prefix + r"""
  const menu=elem('ex-view-color');
  ctx.document.querySelector=selector=>selector==='#ex-view-color'?menu:
    selector==='#ex-color-mode'?menu.children.find(child=>child.id==='ex-color-mode'):null;
  onReady();await tick();await tick();
  assert.equal(menu.children.length,1,'color control belongs in the visible toolbar');
  const toggle=menu.children[0];
  toggle.onclick();
  assert.equal(toggle['aria-pressed'],'true');
  await ctx.window.testReloadIndex();await tick();await tick();
  assert.equal(menu.children.length,1,'refresh cannot duplicate controls outside the graph');
  assert.equal(menu.children[0],toggle,'color choice survives the graph reload');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
