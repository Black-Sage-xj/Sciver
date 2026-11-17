"""Executable report navigation, old links, pagination and return path checks."""
import shutil
import subprocess

import pytest

from test_research_explorer_g6 import HARNESS, SCRIPT


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_report_sections_and_old_document_link_share_one_reading_entity():
    prefix, marker, _ = HARNESS.partition("  onReady();await tick();")
    assert marker
    script = prefix + r"""
  const calls=[];
  ctx.fetch=async url=>{calls.push(url);return {ok:true,json:async()=>{
    if(url.includes('/report?')){
      const section=new URL(url,'http://localhost').searchParams.get('section')||'';
      return {id:'notes/outcomes/1.md',title:'One research report',cycle_id:'1',
        adjudication_status:'adjudicated',sections:[{id:'main-results',title:'Main results',level:2}],
        content:section?'## Main results\nOriginal detailed result.':'# One research report\n\n## Main results\nOriginal detailed result.',
        next_offset:null};
    }
    if(url.includes('/document?'))return {report_cycle:'1'};
    if(url.endsWith('/workers'))return {workers:[]};
    return index;
  }}};
  location.hash='#node%3AA';
  onReady();await tick();await tick();
  const clickRef=async ref=>{
    await elements['ex-reader'].listeners.click({target:{closest:s=>s==='[data-ref]'?{dataset:{ref}}:null}});
    await tick();await tick();
  };
  await clickRef('report:1');
  assert(elements['ex-reader'].innerHTML.includes('报告目录 · 按章节阅读'));
  assert(elements['ex-reader'].innerHTML.includes('Original detailed result.'));
  assert.equal(history.state.tmExplorerView.selected.kind,'report');
  await clickRef('report:1#main-results');
  assert(calls.some(url=>url.includes('/report?cycle=1&section=main-results')));
  assert(elements['ex-reader'].innerHTML.includes('查看完整报告'));
  await clickRef('doc:notes/old-report.md');
  assert.equal(history.state.tmExplorerView.selected.kind,'report','old narrative links resolve to the same report');
  assert.equal(history.state.tmExplorerView.selected.id,'1');
  await elements['ex-reader'].listeners.click({target:{closest:s=>s==='[data-doc-back]'?{}:null}});
  await tick();await tick();
  assert.equal(history.state.tmExplorerView.selected.kind,'node');
  assert.equal(history.state.tmExplorerView.selected.id,'A');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
