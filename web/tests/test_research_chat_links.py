"""Chat citations resolve to bounded readers without mutating stored conversations."""
import hashlib
import json
import threading
from urllib.parse import quote, urlparse
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

import pytest

from tm_observer.canvas_md import ExplorerError
from tm_observer.research_chat_links import document_destination
from tm_observer.web import StateCache, make_handler
from theory_master.science_graph_store import ScienceGraphStore


@pytest.fixture
def task(tmp_path):
    root = tmp_path / '笔记区' / 'tasks' / 'synthetic'
    (root / 'notes/outcomes').mkdir(parents=True)
    (root / 'research_plan.md').write_text('# Goal\nSynthetic public fixture')
    (root / 'notes/method.md').write_text('# Method\n\n## Conditions\nA fixed condition.')
    (root / 'notes/outcomes/1.md').write_text('# Legacy report\nSynthetic observation.')
    research = tmp_path / 'runtime/research'
    research.mkdir(parents=True)
    ident = hashlib.sha256(str(research).encode()).hexdigest()[:16]
    state = {'runs': [{'observer_id': ident, 'run_id': '1', 'task_root': str(root),
                       'research_root': str(research), 'task_name': 'Synthetic'}]}
    (root / 'theory-master.config.json').write_text(json.dumps({
        'research_root': str(research), 'branch_canvas': str(root / 'exploration.canvas')}))
    with ScienceGraphStore(root / 'science_graph.sqlite3') as store:
        store.add_hypothesis('H-0001', title='Synthetic question')
        store.append_outcome(outcome_id='synthetic-1', hypothesis_id='H-0001', cycle_id='1',
            verdict='supported', title='Synthetic result', summary='A bounded result',
            report_ref='notes/old-report.md', record_only=True)
    return tmp_path, root, ident, state


@pytest.mark.parametrize('target', ['absolute', 'file', 'relative', 'vault', 'encoded', 'line', 'old'])
def test_report_citations_share_one_reader(task, target):
    search, root, ident, state = task
    path = root / 'notes/outcomes/1.md'
    refs = {'absolute': str(path), 'file': path.as_uri(), 'relative': 'notes/outcomes/1.md',
            'vault': '/notes/outcomes/1.md', 'encoded': quote(str(path), safe=''),
            'line': str(path) + ':12:3#L12', 'old': 'notes/old-report.md'}
    original = (root / 'science_graph.sqlite3').read_bytes()
    url = document_destination(search, state, ident, refs[target])
    assert url == '/research-explorer/?observer_id=' + ident + '#report%3A1'
    assert (root / 'science_graph.sqlite3').read_bytes() == original


def test_document_anchor_and_leaf_line_link(task):
    search, _, ident, state = task
    url = document_destination(search, state, ident, 'notes/method.md#Conditions')
    assert urlparse(url).fragment == 'doc%3Anotes%2Fmethod.md%23Conditions'
    assert document_destination(search, state, ident, 'research_plan.md:3').endswith('#doc%3Aresearch_plan.md')


@pytest.mark.parametrize('target', ['../outside.md', '../../other/notes/outcomes/1.md',
    'https://example.org/notes/outcomes/1.md', 'file://remote/notes/outcomes/1.md',
    '//remote/notes/outcomes/1.md', '.theory-master/task.json', 'notes/missing.md',
    'notes/%00.md', 'notes/outcomes/1.md\nHeader: injected'])
def test_unavailable_or_escaped_targets_are_not_web_redirects(task, target):
    search, _, ident, state = task
    with pytest.raises(ExplorerError):
        document_destination(search, state, ident, target)


def test_other_task_absolute_path_and_symlink_are_not_relabelled(task):
    search, root, ident, state = task
    outside = search / 'other/notes/outcomes/1.md'
    outside.parent.mkdir(parents=True)
    outside.write_text('Other task')
    (root / 'notes/escape.md').symlink_to(outside)
    for target in [str(outside), 'notes/escape.md']:
        with pytest.raises(ExplorerError) as error:
            document_destination(search, state, ident, target)
        assert error.value.status == 403


def test_open_route_redirects_and_gives_a_readable_missing_document_response(task, monkeypatch):
    search, root, ident, state = task
    cache = StateCache(search)
    monkeypatch.setattr(cache, 'get', lambda: state)
    monkeypatch.setattr('tm_observer.research_chat.begin_send', lambda *a, **k: pytest.fail('No model calls'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache, field_notes_state_dir=search/'observer'))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with urlopen(base+'/research-chat/open?task_id='+ident+'&target='+quote(str(root/'notes/outcomes/1.md'))) as response:
            assert response.status == 200
            assert urlparse(response.url).path == '/research-explorer/'
            assert urlparse(response.url).fragment == 'report%3A1'
        with pytest.raises(HTTPError) as error:
            urlopen(base+'/research-chat/open?task_id='+ident+'&target=notes/missing.md')
        assert error.value.code == 404
        assert '暂时无法打开文档' in error.value.read().decode()
        assert not list((search/'observer/research-chat').glob('*.json'))
    finally:
        server.shutdown()
        server.server_close()
