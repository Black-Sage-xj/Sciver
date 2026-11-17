"""Rendered contrast and graph overview regressions; no model or account calls."""
import os
import re
import subprocess

import pytest

from test_research_explorer_g6 import HARNESS, SCRIPT
from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_explorer_g6 import GRAPH_CSS


def contrast(foreground, background):
    def luminance(color):
        channels = [int(v) / 255 for v in re.findall(r'\d+', color)[:3]]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


def test_graph_light_selection_and_low_zoom_visibility():
    bootstrap, marker, _ = HARNESS.partition('  onReady();await tick();')
    assert marker
    scenario = bootstrap + r"""
  ctx.document.documentElement={getAttribute:()=>theme};
  let theme='white';
  onReady();await tick();await tick();
  const initial=latestGraph.data.edges.find(e=>e.id==='AC').style.stroke;
  assert.equal(initial,'#64748b');
  const renders=latestGraph.renders;
  latestGraph.camera.zoom=.12;latestGraph.events.aftertransform();await tick();
  for(const edge of latestGraph.data.edges)
    assert(edge.style.lineWidth*.12>=1.15,'overview branches stay at least 1.15 CSS pixels');
  latestGraph.events['edge:click']({target:{id:'AX'}});await tick();await tick();
  let selected=latestGraph.data.edges.find(e=>e.id==='AX');
  assert.equal(selected.style.stroke,'#92400e');
  assert(selected.style.lineWidth*.12>=2.2,'selected edges remain distinct in overview');
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AC').style.opacity,.55);
  assert.equal(latestGraph.renders,renders,'selection and zoom do not relayout');
  latestGraph.camera.zoom=1;latestGraph.events.aftertransform();await tick();
  selected=latestGraph.data.edges.find(e=>e.id==='AX');
  assert.equal(selected.style.lineWidth,5,'zoom compensation reverses on zoom in');
  theme='blue';await elements['ex-refresh'].onclick();await tick();await tick();
  assert.equal(latestGraph.data.edges.find(e=>e.id==='AX').style.stroke,'#fef08a');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', scenario, str(SCRIPT)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.environ.get('TM_BROWSER_E2E') != '1', reason='opt-in Chromium check')
def test_rendered_light_controls_hover_focus_and_semantic_cards():
    from playwright.sync_api import sync_playwright
    body = '''<section class="tm-quota-section"><p class="tm-account-meta">prolite · Available</p>
    <button class="tm-account-switch">切换到此账号</button></section>
    <div class="tm-tui-kv-grid"><a href="#graph" class="tm-explore-map">
    <span class="tm-explore-map-body"><strong>图谱</strong><span>探索科研图谱</span></span></a></div>
    <a class="tm-run-settings-link" href="#settings">⚙</a>
    <div class="ex-actions"><button class="ex-color-mode">远景颜色</button>
    <output class="ex-zoom-percent">12%</output></div>
    <div class="ex-outcome-card" style="position:relative;width:300px">
    <button class="ex-outcome-part" style="background:#384152">Hypothesis</button></div>'''
    html = dashboard_shell('Contrast', GRAPH_CSS + body, active='console')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1280, 'height':850})
        page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        page.add_init_script("localStorage.setItem('sciver-theme-v1','white')")
        page.goto('http://theme.test/')

        def colors(selector):
            return page.locator(selector).evaluate('''e=>{
              const fg=getComputedStyle(e).color;let p=e,bg;
              do {bg=getComputedStyle(p).backgroundColor;p=p.parentElement}
              while(bg==='rgba(0, 0, 0, 0)'&&p);
              return [fg,bg];}''')

        for selector in ('.tm-account-meta', '.tm-account-switch', '.tm-explore-map-body strong',
                         '.tm-explore-map-body > span', '.tm-run-settings-link',
                         '.ex-color-mode', '.ex-zoom-percent', '.ex-outcome-part'):
            assert contrast(*colors(selector)) >= 4.5, (selector, colors(selector))
        for selector in ('.tm-account-switch', '.tm-explore-map', '.tm-run-settings-link'):
            page.locator(selector).hover()
            assert contrast(*colors(selector)) >= 4.5, selector
            page.mouse.down()
            assert contrast(*colors(selector)) >= 4.5, selector
            page.mouse.up()
        page.keyboard.press('Tab')
        page.locator('.tm-explore-map').focus()
        outline = page.locator('.tm-explore-map').evaluate('e=>getComputedStyle(e).outlineStyle')
        assert outline != 'none'
        # A live theme switch restores dark graph controls without a reload.
        for theme in ('black', 'blue', 'white'):
            page.evaluate('(t)=>document.documentElement.setAttribute("data-sciver-theme",t)', theme)
            for selector in ('.ex-color-mode', '.ex-zoom-percent', '.ex-outcome-part'):
                assert contrast(*colors(selector)) >= 4.5, (theme, selector)
        browser.close()
