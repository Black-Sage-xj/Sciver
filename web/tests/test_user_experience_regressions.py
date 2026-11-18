"""User-facing navigation and empty-state regressions; no model calls."""
import hashlib
import json
import threading
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import expect, sync_playwright

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_chat_widget import WIDGET_STYLE, widget_html
from tm_observer.stage_flow import stage_workflow_body
from tm_observer.task_settings_ui import workspace_settings_body


def test_settings_navigation_targets_real_sections():
    for task in (None, {"observer_id": "a" * 16, "task_name": "Example"}):
        page = BeautifulSoup(workspace_settings_body([task] if task else [], task), "html.parser")
        links = page.select('.tm-settings-nav a[href^="#"]')
        assert len(links) == 4
        for link in links:
            assert len(page.select(link['href'])) == 1


def test_mobile_workflow_index_uses_current_stage_catalog():
    page = BeautifulSoup(stage_workflow_body(), "html.parser")
    index = page.select_one('.stage-mobile-index')
    assert index is not None
    assert index.select_one('a[href="#workflow-diagram"]')
    lead = next(item for item in index.select(':scope > details') if 'Research Lead' in item.summary.get_text())
    assert any('frontier_proposal' in item.summary.get_text() for item in lead.select(':scope > details'))
    assert index.select_one('details details p').get_text(strip=True)


def test_mobile_chat_launcher_is_in_toolbar_and_desktop_position_returns():
    script = Path(__file__).parents[1] / 'tm_observer' / 'research_chat_widget.js'
    page_html = dashboard_shell('Settings', '<main style="height:2400px">Page</main>', active='settings')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            def respond(route):
                if route.request.url.endswith('/widget.js'):
                    route.fulfill(body=script.read_text(), content_type='application/javascript')
                elif route.request.url.endswith('/fixture'):
                    route.fulfill(body=page_html, content_type='text/html')
                else:
                    route.fulfill(body='<body></body>', content_type='text/html')
            page.route('http://widget.test/**', respond)
            page.goto('http://widget.test/fixture')
            launcher = page.locator('#sc-chat-launcher')
            assert launcher.evaluate("e => !!e.closest('.tm-topbar-actions')")
            page.evaluate('window.scrollTo(0, 500)')
            assert launcher.bounding_box()['y'] < 0, 'docked launcher must not cover scrolling content'
            page.evaluate('window.scrollTo(0, 0)')
            launcher.click()
            expect(page.locator('#sc-chat-panel')).to_be_visible()
            page.locator('#sc-chat-minimize').click()
            page.set_viewport_size({'width': 1280, 'height': 900})
            assert launcher.evaluate("e => e.parentElement.id === 'sciver-chat-widget'")
            assert launcher.evaluate("e => getComputedStyle(e).position") == 'fixed'
        finally:
            browser.close()


def test_stage_empty_states_and_sdk_only_records():
    from tm_observer.run_stage_ui import shell, script
    body = ('<!doctype html><meta charset="utf-8"><div id="tm-run-live"><div class="tm-tui-screen" '
            'data-tui-screen="events" data-tui-cycle="1">' + shell() + '</div></div>' + script('a' * 16))
    state = {'cycle': '1', 'cycles': ['1'], 'stages': [], 'transitions': [],
             'warnings': ['runtime_events.jsonl: unavailable']}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            def respond(route):
                if '/conversation' in route.request.url:
                    route.fulfill(json=state)
                else:
                    route.fulfill(body=body, content_type='text/html')
            page.route('http://stage.test/**', respond)
            page.goto('http://stage.test/fixture?tab=events')
            expect(page.locator('[data-stage-timeline]')).to_contain_text('本轮尚无可读取的 Stage 记录')
            state['warnings'] = ['events.jsonl: unreadable']
            page.reload()
            expect(page.locator('[data-stage-timeline]')).to_contain_text('部分记录无法读取')
            state['warnings'] = []
            state['transitions'] = [{'kind': 'CHECKPOINT', 'seq': 1, 'time': '2026-01-01'}]
            page.reload()
            expect(page.locator('[data-stage-timeline]')).to_contain_text('SDK · CHECKPOINT')
            assert page.locator('.tm-stage-empty').count() == 0
            state['transitions'] = []
            state['stages'] = [{'id': 'stage-1', 'role': 'investigator', 'stage': 'investigate',
                                'state': 'complete', 'start': '2026-01-01', 'count': 0}]
            page.reload()
            expect(page.locator('.tm-stage-heading')).to_be_visible()
            page.locator('[data-stage-search]').fill('no matching stage')
            expect(page.locator('[data-stage-timeline]')).to_contain_text('没有符合当前筛选条件的 Stage')
        finally:
            browser.close()


def test_mobile_report_prioritizes_reader_and_can_return_to_graph(tmp_path):
    from theory_master.science_graph_store import ScienceGraphStore
    from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler

    vault = tmp_path / '笔记区' / '探索台账' / 'sample'
    vault.mkdir(parents=True)
    research = tmp_path / 'research'
    research.mkdir()
    (vault / 'theory-master.config.json').write_text(json.dumps({
        'research_root': str(research), 'branch_canvas': str(vault / 'exploration.canvas'),
    }))
    (vault / 'notes').mkdir()
    (vault / 'notes/report.md').write_text('# Report\n\n## Results\nSample result.\n')
    with ScienceGraphStore(vault / 'science_graph.sqlite3') as graph:
        graph.add_hypothesis('H-0001', title='A conjecture', summary='Sample')
        graph.append_outcome(outcome_id='out-1', hypothesis_id='H-0001', cycle_id='1',
            verdict='supported', title='Report', summary='Sample', judge_rationale='Criterion',
            report_ref='notes/report.md', record_only=True)
    ident = hashlib.sha256(str(research).encode()).hexdigest()[:16]
    task = {'observer_id': ident, 'task_root': str(vault), 'research_root': str(research), 'task_name': 'sample'}
    cache = StateCache(tmp_path)
    cache.get = lambda: {'runs': [task]}
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache, field_notes_state_dir=tmp_path / 'state'))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    fake_graph = """window.G6={Graph:class {
      constructor(){this.data={nodes:[],edges:[]};this.zoom=1;this.position=[0,0]}
      on(){} setData(data){this.data=data} async render(){} async draw(){await new Promise(r=>setTimeout(r,250))} resize(){}
      getNodeData(id){return id?this.data.nodes.find(n=>n.id===id):this.data.nodes}
      getEdgeData(){return this.data.edges} updateNodeData(){} updateEdgeData(){}
      async setElementVisibility(){} async fitView(){} async focusElement(){}
      getZoom(){return this.zoom} getPosition(){return this.position}
      async zoomTo(z){this.zoom=z} async translateTo(p){this.position=p}
      getElementPosition(){return [0,0]} getViewportByCanvas(p){return [p[0]+200,p[1]+200]}
    }};"""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 390, 'height': 844})
                def respond(route):
                    url = route.request.url
                    if route.request.method != 'GET':
                        route.abort()
                    elif url.startswith(origin):
                        route.continue_()
                    else:
                        route.fulfill(body=fake_graph if '/g6@' in url else '', content_type='application/javascript')
                page.route('**/*', respond)
                url = origin + '/research-explorer/?observer_id=' + ident + '#report%3A1'
                page.goto(url)
                expect(page.locator('#ex-reader .ex-document-title')).to_be_visible()
                expect(page.locator('#ex-graph')).to_be_hidden()
                page.locator('#ex-all').click()
                expect(page.locator('#ex-graph')).to_be_visible()
                page.wait_for_timeout(600)
                expect(page.locator('#ex-graph')).to_be_visible()
                page.goto(url)
                expect(page.locator('#ex-graph')).to_be_hidden()
                page.locator('[data-doc-back]').click()
                expect(page.locator('#ex-graph')).to_be_visible()
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.goto(url)
                expect(page.locator('#ex-reader .ex-document-title')).to_be_visible()
                expect(page.locator('#ex-graph')).to_be_visible()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
