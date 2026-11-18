"""Embedded Research Chat shares routes and records without creating a model turn."""
from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import urlopen

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_chat import create, snapshot
from tm_observer.research_chat_ui import embedded_page
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


def test_launcher_drag_and_nearby_open_in_browser():
    from playwright.sync_api import sync_playwright
    from tm_observer.research_chat_widget import WIDGET_STYLE, widget_html

    script = Path(__file__).parents[1] / 'tm_observer' / 'research_chat_widget.js'
    page_html = '<!doctype html>' + WIDGET_STYLE + '<body>' + widget_html() + '</body>'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
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
        panel = page.locator('#sc-chat-panel')

        def drag_to(x, y):
            box = launcher.bounding_box()
            page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
            page.mouse.down()
            page.mouse.move(x, y, steps=8)
            page.mouse.up()

        def assert_nearby():
            a, b = launcher.bounding_box(), panel.bounding_box()
            assert 0 <= b['x'] <= 1280 - b['width']
            assert 0 <= b['y'] <= 900 - b['height']
            # Either horizontal or vertical edges are separated by the anchor gap.
            gaps = [abs(b['x'] + b['width'] + 12 - a['x']),
                    abs(a['x'] + a['width'] + 12 - b['x']),
                    abs(b['y'] + b['height'] + 12 - a['y']),
                    abs(a['y'] + a['height'] + 12 - b['y'])]
            assert min(gaps) < 1

        drag_to(100, 120)
        assert panel.is_hidden(), 'Dragging must not open chat'
        launcher.click()
        assert panel.is_visible()
        assert_nearby()
        page.locator('#sc-chat-minimize').click()
        drag_to(1180, 760)
        assert panel.is_hidden()
        launcher.click()
        assert_nearby()
        page.locator('#sc-chat-minimize').click()
        position = launcher.bounding_box()
        page.reload()
        assert launcher.bounding_box() == position
        launcher.focus()
        page.keyboard.press('Enter')
        assert panel.is_visible(), 'Keyboard activation must still open chat'
        drag_to(-100, -100)
        assert launcher.bounding_box()['x'] == 12
        assert launcher.bounding_box()['y'] == 12
        assert_nearby()
        page.set_viewport_size({'width': 390, 'height': 640})
        drag_to(1000, 1000)
        box = launcher.bounding_box()
        assert box['x'] + box['width'] <= 390
        assert box['y'] + box['height'] <= 640
        page.locator('#sc-chat-minimize').click()
        launcher.click()
        assert panel.bounding_box()['width'] <= 390
        browser.close()


def test_launcher_is_lazy_and_task_aware_without_recursing_into_chat():
    page = dashboard_shell('图谱', '<p>图谱</p>', active='explorer', back_href='/run/' + 'a' * 16 + '/')
    assert 'id="sciver-chat-widget" data-task-id="aaaaaaaaaaaaaaaa"' in page
    assert 'id="sc-chat-launcher"' in page
    assert 'aria-modal="false"' in page
    assert 'data-sc-chat-resize="nw"' in page
    assert '<iframe class="sc-chat-frame" id="sc-chat-frame" title="Sciver Research Chat" hidden>' in page
    assert 'src="/research-chat/?' not in page
    full_chat = dashboard_shell('Research Chat', '<p>chat</p>', active='research_chat')
    assert 'id="sciver-chat-widget"' not in full_chat


def test_embedded_chat_preserves_pending_actions_and_escaped_task_context():
    task_id = 'a' * 16
    page = embedded_page({
        'tasks': [{'id': task_id, 'label': '<script>task</script>'}],
        'sessions': [{'id': 'b' * 32, 'title': '<unsafe title>'}],
        'session': {'id': 'b' * 32, 'task_id': task_id, 'title': '<unsafe title>',
                    'pending_action': {'id': 'action-1', 'title': '方向干预预览', 'summary': '保留人工确认'}},
    }, task_id='c' * 16)
    from html.parser import HTMLParser
    class Sections(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'section':
                values = dict(attrs)
                if 'tm-research-chat' in values.get('class', '').split():
                    self.chat = values
    parsed = Sections()
    parsed.feed(page)
    assert parsed.chat['data-embedded'] == 'true'
    assert parsed.chat['data-task-id'] == task_id
    assert 'class="tm-chat-embedded"' in page
    assert 'class="tm-sidebar"' not in page
    assert 'id="sciver-chat-widget"' not in page
    assert 'data-task-id="' + task_id + '"' in page
    assert '&lt;unsafe title&gt;' in page
    assert '&lt;script&gt;task&lt;/script&gt;' in page
    assert '<script>task</script>' not in page
    assert 'data-research-action="action-1" data-decision="confirm"' in page
    assert 'data-research-action="action-1" data-decision="reject"' in page
    assert 'data-research-embedded-history' in page
    assert 'id="tm-research-chat-app" data-session-id="' + 'b' * 32 in page


def test_embed_routes_share_sessions_and_gets_do_not_create_conversations(tmp_path, monkeypatch):
    state = {'runs': [{'observer_id': 'a' * 16, 'task_name': 'Research fixture'}]}
    notes = tmp_path / 'notes'
    cache = StateCache(tmp_path)
    monkeypatch.setattr(cache, 'get', lambda: state)
    # If a read-only request accidentally starts a turn, fail rather than calling a model.
    def forbidden(*args, **kwargs):
        raise AssertionError('GET must not start a chat turn')
    monkeypatch.setattr('tm_observer.research_chat.begin_send', forbidden)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache, field_notes_state_dir=notes))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    def get(path):
        with urlopen(base + path, timeout=5) as response:
            assert response.status == 200
            return response.read().decode()
    try:
        script = get('/research-chat/widget.js')
        assert 'event.source!==frame.contentWindow' in script
        assert 'event.origin!==location.origin' in script
        assert 'setPointerCapture' in script
        new = get('/research-chat/?embed=1&new=1&task_id=' + 'a' * 16)
        assert 'class="tm-chat-embedded"' in new and 'data-embedded="true"' in new
        assert 'data-task-id="' + 'a' * 16 + '"' in new
        full_new = get('/research-chat/?new=1&task_id=' + 'a' * 16)
        assert 'data-task-id="' + 'a' * 16 + '"' in full_new
        assert not list((notes / 'research-chat').glob('*.json'))
        invalid = get('/research-chat/?embed=1&new=1&task_id=unknown')
        assert 'data-task-id=""' in invalid
        ident = create(notes, state, {'mode': 'project', 'task_id': 'a' * 16})['session_id']
        initial = snapshot(notes, state, ident)['session']
        embedded = get('/research-chat/?embed=1&session=' + ident)
        full = get('/research-chat/?session=' + ident)
        assert 'data-session="' + ident + '"' in embedded
        assert 'data-session="' + ident + '"' in full
        assert 'class="tm-sidebar"' not in embedded
        assert 'class="tm-sidebar"' in full
        assert snapshot(notes, state, ident)['session']['messages'] == initial['messages']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
