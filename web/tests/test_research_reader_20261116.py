"""Research reading hierarchy, scientific authority and themed Markdown regressions."""
import os
import shutil
import subprocess

import pytest

from test_research_explorer_g6 import HARNESS, SCRIPT
from test_theme_contrast_20261116 import contrast
from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_explorer_g6 import GRAPH_CSS
from tm_observer.research_reader_ui import READER_CSS


@pytest.mark.skipif(not shutil.which("node"), reason="requires Node.js")
def test_hypothesis_and_result_are_separate_and_placeholder_is_not_a_verdict():
    bootstrap, marker, _ = HARNESS.partition("  onReady();await tick();")
    assert marker
    script = bootstrap + r"""
  const sem=nodes[0].semantic;
  nodes[0].label='Adjacent-increment covariance correction';
  sem.hypothesis_summary='主张：D removes noise bias；条件：dt=0.10; iid errors；状态：待检验。';
  sem.scientific_status='none';
  sem.legacy_identity_review={status:'unadjudicated_whole_h'};
  // A report pointer on the migration placeholder still does not adjudicate H.
  sem.whole_h_assessment={verdict:'open',report_ref:'notes/report.md'};
  sem.dossier_ref='notes/hypotheses/H-0001.md';
  sem.cycle_outcomes=[
    {cycle_id:'1',verdict:'open',summary:'First bounded test is inconclusive.'},
    {cycle_id:'2',verdict:'unadjudicated',review_state:'unadjudicated',summary:'Preserved unfinished work'},
    {cycle_id:'3',verdict:'supported',graph_summary:{conclusion:'D=0.3683',
      basis:'Independent trajectory intervals',scope:'150 train / 50 validation',
      open_question:'Parameter equality remains unresolved'},summary:'Full scientific record',
      document_ref:'notes/outcomes/3.md',report_ref:'notes/report.md'}
  ];
  sem.frontiers=[{status:'closed',reason:'No further test under this hypothesis.'}];
  index.references['node:A']=[{kind:'doc',id:sem.dossier_ref,anchor:''},
    {kind:'doc',id:'notes/outcomes/3.md',anchor:''}];
  const before=JSON.stringify({sem,text:nodes[0].text});
  onReady();await tick();await tick();
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  const html=elements['ex-reader'].innerHTML;
  assert.equal(JSON.stringify({sem,text:nodes[0].text}),before,'reading never mutates science');
  assert(html.indexOf('id="ex-hypothesis-details"')<html.indexOf('id="ex-outcome-details"'));
  assert.equal(html.split('Adjacent-increment covariance correction').length-1,1,'title appears once');
  assert(html.includes('<dt>主张</dt>')&&html.includes('<dt>适用条件</dt>'));
  assert(html.includes('D removes noise bias')&&html.includes('dt=0.10; iid errors'));
  assert(html.includes('整体猜想尚无单独裁决'));
  assert(!html.includes('整体猜想裁决 <span'),'migration placeholder is not Judge Open');
  assert(!html.includes('关联 Markdown'),'no third duplicate document section');
  assert(html.includes('<details class="ex-reader-context ex-reader-sources"><summary>'));
  assert(!html.includes('ex-reader-sources" open'),'technical metadata starts collapsed');
  assert(html.includes('data-ref="doc:notes/hypotheses/H-0001.md"'));
  assert(html.includes('data-ref="doc:notes/outcomes/3.md"'));
  assert(html.includes('data-ref="report:3"'));
  assert(!html.includes('data-ref="doc:notes/report.md"'));
  assert(html.includes('data-verdict="unadjudicated"'));
  assert(html.includes('尚无 Judge 科学裁决'));
  assert.equal((html.match(/class="ex-cycle-result" open/g)||[]).length,1,'only latest round starts open');
  assert(!html.includes('Full scientific record'),'full report is read through one report entry');
  assert.equal((html.match(/阅读研究报告/g)||[]).length,3,'one report entry per recorded round');
  assert(html.includes('Parameter equality remains unresolved'));
  // Nonstandard legacy prose is kept, without trying to infer claim/conditions.
  delete sem.hypothesis_summary;
  nodes[0].text='# Distinct historical title\n\nUnstructured frozen science: x < y.';
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('Distinct historical title'));
  assert(elements['ex-reader'].innerHTML.includes('Unstructured frozen science: x &lt; y.'));
  sem.cycle_outcomes=[];sem.outcome=sem.whole_h_assessment;
  latestGraph.events['node:click']({target:{id:'A'}});await tick();await tick();
  assert(!elements['ex-reader'].innerHTML.includes('class="ex-cycle-result"'),
    'whole-H placeholder with a report pointer is not a Cycle result');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(SCRIPT)], capture_output=True,
                            text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.environ.get("TM_BROWSER_E2E") != "1", reason="opt-in Chromium check")
def test_markdown_color_contrast_and_overflow_in_all_themes():
    from playwright.sync_api import sync_playwright

    body = '''<section class="ex-reader" style="width:420px;height:800px">
    <div class="ex-reader-card"><header class="ex-reader-section-head"><h3>猜想</h3></header>
    <span class="ex-verdict" data-verdict="supported">支持</span>
    <span class="ex-verdict" data-verdict="rejected">反驳</span>
    <span class="ex-verdict" data-verdict="open">尚无法判定</span>
    <span class="ex-verdict" data-verdict="unadjudicated">未裁决</span>
    <div class="ex-md"><h2>检验结果</h2><p>指定范围内的研究正文。</p><h3>适用范围</h3>
    <p><a href="#test">原始文档</a> <code>sigma^2</code></p><blockquote>独立轨迹级区间。</blockquote>
    <pre><code><span class="token keyword">def</span> <span class="token function">estimate</span>():
    <span class="token comment"># trajectory estimator</span>
    <span class="token keyword">return</span> <span class="token string">"''' + 'x' * 100 + '''"</span></code></pre>
    <table><thead><tr><th>Dataset</th><th>Diffusion</th></tr></thead>
    <tbody><tr><td>Training</td><td>0.3683</td></tr><tr><td>Validation</td><td>0.3626</td></tr></tbody></table>
    </div><button class="ex-ref ex-document-link">完整猜想文档 ↗</button></div></section>'''
    html = dashboard_shell("Research reader", GRAPH_CSS + READER_CSS + body, active="explorer")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1200, "height":900})
        page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=html))
        page.goto("http://reader.test/")
        for theme in ("black", "blue", "white"):
            page.evaluate("t=>document.documentElement.setAttribute('data-sciver-theme',t)", theme)
            for selector in (".ex-reader-section-head h3", ".ex-md h2", ".ex-md h3", ".ex-md p",
                             ".ex-md a", ".ex-md p code", ".ex-md blockquote", ".token.keyword",
                             ".token.function", ".token.string", ".token.comment", ".ex-md th",
                             ".ex-document-link", '[data-verdict="supported"]', '[data-verdict="rejected"]',
                             '[data-verdict="open"]', '[data-verdict="unadjudicated"]'):
                colors = page.locator(selector).first.evaluate('''e=>{
                  const fg=getComputedStyle(e).color;let p=e,bg;
                  do{bg=getComputedStyle(p).backgroundColor;p=p.parentElement}
                  while(bg==='rgba(0, 0, 0, 0)'&&p);return [fg,bg];}''')
                assert contrast(*colors) >= 4.5, (theme, selector, colors)
            assert page.locator(".ex-md h2").evaluate("e=>getComputedStyle(e).color") != \
                page.locator(".ex-md p").first.evaluate("e=>getComputedStyle(e).color")
            assert page.locator("pre").evaluate("e=>e.scrollWidth>e.clientWidth")
            assert page.locator(".ex-reader").evaluate("e=>e.scrollWidth<=e.clientWidth")
        page.locator(".ex-document-link").focus()
        assert page.locator(".ex-document-link").evaluate("e=>getComputedStyle(e).outlineStyle") != "none"
        browser.close()
