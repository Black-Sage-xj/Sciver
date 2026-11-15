"""Embedded Research Chat shares routes and records without creating a model turn."""
from __future__ import annotations

import threading
from urllib.request import urlopen

from tm_observer.dashboard_ui import dashboard_shell
from tm_observer.research_chat import create, snapshot
from tm_observer.research_chat_ui import embedded_page
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


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
                if values.get('class') == 'tm-research-chat':
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
