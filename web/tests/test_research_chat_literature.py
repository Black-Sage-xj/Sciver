"""Offline Chat-to-Literature integration, preserving scientific task state."""
from __future__ import annotations

import hashlib
import fcntl
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from tests.test_literature_service import LiteratureBackend, Retriever
from theory_master.task import bootstrap
from tm_observer import research_chat as chat, research_preparation as prep
from tm_observer import research_chat_literature as literature
from tm_observer.collector import collect_state
from tm_observer.research_preparation_literature import run_search
from tm_observer.research_chat_ui import page_body
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


QUERY = 'Compare the mechanism and its counterexample'
SCOPE = 'A bounded scientific question'
PLAN = ('# Goal\n\nExplain the observed scaling.\n\n'
        '# Research Contract\n\nCompare specified mechanisms using independent samples.\n\n'
        '# Final Gate\n\nResolve the signed prediction comparison with uncertainty.\n\n'
        '# Required Context\n\nUse measurement definitions and source notes.\n')


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    state, search = tmp_path / 'state', tmp_path / 'workspace'
    search.mkdir()
    monkeypatch.setenv('THEORY_MASTER_DATA_ROOT', str(tmp_path / 'data'))
    ident = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    return state, search, ident


def request(**overrides):
    return {'query': QUERY, 'scope': SCOPE, **overrides}


def test_preparation_uses_real_role_current_route_report_and_idempotent_job(prepared):
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(), Retriever()
    root = prep.workspace(state, ident)
    plan_before = (root / 'research_plan.md').read_bytes()
    session_before = (state / 'research-chat' / (ident + '.json')).read_bytes()
    result = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    assert result['status'] == 'completed', result
    assert result['job_id'] == result['service_job_id']
    assert result['literature_agent_invoked'] and result['model_invoked']
    assert result['thread_id'] == 'fixture-literature-thread'
    assert result['agent_turns'] == 4 and result['source_access_counts']['full_text'] == 2
    expected = root / 'literature' / 'studies' / result['job_id']
    assert Path(result['manifest']) == expected / 'manifest.json'
    manifest = json.loads(Path(result['manifest']).read_text())
    assert manifest['route']['model'] == 'gpt-6-sol'
    assert manifest['route']['reasoning_effort'] == 'medium'
    assert (root / 'research_plan.md').read_bytes() == plan_before
    assert (state / 'research-chat' / (ident + '.json')).read_bytes() == session_before
    assert not list(root.rglob('science_graph.sqlite3'))
    listing = literature.list_studies(state, ident)
    assert listing['jobs'][0]['job_id'] == result['job_id']
    assert listing['jobs'][0]['report_available']
    assert 'Source-grounded observations' in literature.read_report(state, ident, result['job_id'])
    # Only its independent index is locked; the existing Chat lock may be held.
    with (state / 'research-chat' / (ident + '.lock')).open('a+') as handle:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX)
        again = literature.run_study(state, search, ident, request(job_id=result['job_id']),
                                     backend=backend, retriever=retrieve)
    assert again['status'] == 'completed'
    assert len(backend.calls) == 4 and len(retrieve.calls) == 3
    with pytest.raises(literature.ChatLiteratureError, match='不能更换'):
        literature.run_study(state, search, ident, request(job_id=result['job_id'], query='Another question'),
                             backend=backend, retriever=retrieve)


def test_explicit_budget_increase_resumes_same_job_and_keeps_progress_readable(prepared):
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    first = literature.run_study(state, search, ident, request(max_rounds=1, max_sources=3),
                                 backend=backend, retriever=retrieve)
    assert first['status'] == 'paused' and first['stop_reason'] == 'round_budget_exhausted'
    original_calls = len(backend.calls)
    observed = []
    def progress(event):
        if event['event'] == 'literature.job_started':
            observed.append(literature.read_study(state, ident, first['job_id'])['job'])
    resumed = literature.run_study(state, search, ident,
        request(job_id=first['job_id'], max_rounds=2), backend=backend, retriever=retrieve, on_event=progress)
    assert resumed['job_id'] == first['job_id'] and resumed['round'] == 2
    assert len(backend.calls) > original_calls and observed
    manifest = json.loads(Path(resumed['manifest']).read_text())
    index = json.loads(literature._index_path(state, ident).read_text())
    assert manifest['request'] == index['jobs'][0]['request']
    assert manifest['request']['max_rounds'] == 2 and manifest['request']['max_sources'] == 3
    calls_before = len(backend.calls), len(retrieve.calls)
    for limits in ({}, {'max_rounds': 1, 'max_sources': 1}):
        literature.run_study(state, search, ident, request(job_id=first['job_id'], **limits),
                             backend=backend, retriever=retrieve)
        current = json.loads(Path(resumed['manifest']).read_text())['request']
        assert current['max_rounds'] == 2 and current['max_sources'] == 3
    assert (len(backend.calls), len(retrieve.calls)) == calls_before


def test_explicit_source_budget_increase_reads_remaining_source(prepared):
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    first = literature.run_study(state, search, ident, request(max_sources=1),
                                 backend=backend, retriever=retrieve)
    assert first['status'] == 'paused' and first['stop_reason'] == 'source_budget_exhausted'
    backend.remaining = False
    resumed = literature.run_study(state, search, ident, request(job_id=first['job_id'], max_sources=2),
                                   backend=backend, retriever=retrieve)
    assert resumed['status'] == 'completed'
    assert len([call for call in retrieve.calls if call['task_type'] == 'targeted_source']) == 2
    calls_before = len(backend.calls), len(retrieve.calls)
    again = literature.run_study(state, search, ident,
        request(job_id=first['job_id'], max_sources=12, max_rounds=4), backend=backend, retriever=retrieve)
    assert again['status'] == 'completed'
    assert (len(backend.calls), len(retrieve.calls)) == calls_before
    assert json.loads(Path(again['manifest']).read_text())['request']['max_sources'] == 2


@pytest.mark.parametrize('changed', [
    {'query': 'Another question'}, {'scope': 'Another scope'},
    {'max_rounds': 5}, {'max_sources': 13},
])
def test_resume_keeps_assigned_question_and_hard_budget_limits(prepared, changed):
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(remaining=True), Retriever()
    first = literature.run_study(state, search, ident, request(max_rounds=1),
                                 backend=backend, retriever=retrieve)
    before = literature._index_path(state, ident).read_bytes()
    calls_before = len(backend.calls), len(retrieve.calls)
    with pytest.raises(ValueError):
        literature.run_study(state, search, ident, request(job_id=first['job_id'], **changed),
                             backend=backend, retriever=retrieve)
    assert literature._index_path(state, ident).read_bytes() == before
    assert (len(backend.calls), len(retrieve.calls)) == calls_before


def promote_preparation(state, search, ident):
    current = prep.snapshot(state, ident)
    current = prep.update(state, ident, {'expected_revision': current['revision'], 'draft': PLAN,
                                       'title': 'Fixture project', 'intent': 'Compare the mechanism.'})
    created = prep.create_project(state, search, ident, {'confirm': True, 'expected_revision': current['revision']})
    path = state / 'research-chat' / (ident + '.json')
    row = json.loads(path.read_text())
    row.update(mode='project', task_id=created['observer_id'], preparation_id=ident)
    path.write_text(json.dumps(row))
    return created


def test_promoted_chat_keeps_legacy_reports_and_places_new_jobs_in_project(prepared):
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(), Retriever()
    old = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    index_path = literature._index_path(state, ident)
    index = json.loads(index_path.read_text())
    index['jobs'][0].pop('binding')  # Existing installations stored one index binding.
    literature._save(index_path, index)
    old_report = literature.read_report(state, ident, old['job_id'])
    created = promote_preparation(state, search, ident)
    assert literature.read_report(state, ident, old['job_id']) == old_report
    assert literature.read_study(state, ident, old['job_id'])['job']['status'] == 'completed'
    with pytest.raises(literature.ChatLiteratureError, match='筹备文献已冻结'):
        literature.run_study(state, search, ident, request(job_id=old['job_id']),
                             backend=backend, retriever=retrieve)
    new = literature.run_study(state, search, ident, request(query='Follow up in this project'),
                               backend=backend, retriever=retrieve)
    assert Path(new['manifest']).is_relative_to(Path(created['research_root']) / 'chat-literature' / ident)
    assert {job['job_id'] for job in literature.list_studies(state, ident)['jobs']} == {old['job_id'], new['job_id']}
    assert literature.read_report(state, ident, old['job_id']) == old_report
    assert literature.read_report(state, ident, new['job_id'])
    saved = json.loads(index_path.read_text())
    assert saved['binding']['mode'] == 'preparation'
    assert saved['jobs'][1]['binding']['mode'] == 'project'


@pytest.mark.parametrize('tamper', ['preparation_id', 'created_task', 'workspace_root'])
def test_promoted_preparation_reports_require_verified_origin(prepared, tamper):
    state, search, ident = prepared
    old = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    promote_preparation(state, search, ident)
    if tamper == 'preparation_id':
        path = state / 'research-chat' / (ident + '.json')
        row = json.loads(path.read_text())
        row['preparation_id'] = 'f' * 32
        path.write_text(json.dumps(row))
    elif tamper == 'created_task':
        root = prep.workspace(state, ident)
        metadata = prep._metadata(root, ident)
        metadata['created_task']['observer_id'] = 'f' * 16
        prep._write_meta(root, metadata)
    else:
        path = literature._index_path(state, ident)
        index = json.loads(path.read_text())
        index['jobs'][0]['binding']['workspace_root'] = str(search)
        literature._save(path, index)
    with pytest.raises(literature.ChatLiteratureError, match='绑定不一致'):
        literature.read_report(state, ident, old['job_id'])


@pytest.mark.parametrize('field', ['creation', 'created_task'])
def test_frozen_preparation_refuses_new_literature_without_model(prepared, field):
    state, search, ident = prepared
    root = prep.workspace(state, ident)
    value = prep._metadata(root, ident)
    value[field] = {'status': 'fixture frozen'}
    prep._write_meta(root, value)
    backend, retrieve = LiteratureBackend(), Retriever()
    with pytest.raises(literature.ChatLiteratureError, match='确认立项'):
        literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    assert not backend.calls and not retrieve.calls
    assert not (root / 'literature').exists()


def test_reuses_only_current_preparation_recorded_retrieval_manifest(prepared):
    state, search, ident = prepared
    old_retrieve = Retriever()
    previous = run_search(prep.workspace(state, ident), QUERY, scope=SCOPE,
                          retrieve=lambda task, directory, **kwargs: old_retrieve(task, directory))
    assert previous['status'] == 'completed', previous
    backend, retrieve = LiteratureBackend(), Retriever()
    result = literature.run_study(state, search, ident, request(retrieval_manifests=[previous['manifest']]),
                                  backend=backend, retriever=retrieve)
    assert result['status'] == 'completed', result
    assert all(item['task_type'] == 'targeted_source' for item in retrieve.calls)
    other = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    with pytest.raises(literature.ChatLiteratureError, match='当前筹备'):
        literature.run_study(state, search, other, request(retrieval_manifests=[previous['manifest']]),
                             backend=backend, retriever=retrieve)


def test_task_binding_uses_canonical_research_root_and_leaves_science_unchanged(prepared):
    state, search, _ = prepared
    project = search / 'project'
    project.mkdir()
    plan = project / 'research_plan.md'
    plan.write_text(PLAN)
    binding = bootstrap(plan, allocation_root=search)
    current = collect_state(search, include_git_info=False)
    task = next(row for row in current['runs'] if row['research_root'] == str(binding.research_root))
    ident = chat.create(state, current, {'mode': 'project', 'task_id': task['observer_id']})['session_id']
    database = Path(binding.config['graph_database'])
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (plan, database)}
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    assert result['status'] == 'completed'
    assert Path(result['manifest']).parent == binding.research_root / 'chat-literature' / ident / result['job_id']
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
    assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'completed'
    record_path = state / 'research-chat' / (ident + '.json')
    record = json.loads(record_path.read_text())
    record['task_id'] = 'f' * 16
    record_path.write_text(json.dumps(record))
    with pytest.raises(literature.ChatLiteratureError):
        literature.list_studies(state, ident)
    with pytest.raises(literature.ChatLiteratureError):
        literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())


def test_progress_projection_never_calls_retrieval_completion_review_completion(prepared):
    state, search, ident = prepared
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    path = Path(result['manifest'])
    manifest = json.loads(path.read_text())
    manifest['literature_agent_invoked'] = False
    path.write_text(json.dumps(manifest))
    assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'incomplete'
    path.unlink()
    assert literature.list_studies(state, ident)['jobs'][0]['status'] == 'registered'


def test_report_cross_session_and_symlink_are_rejected(prepared, tmp_path):
    state, search, ident = prepared
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    other = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    with pytest.raises(literature.ChatLiteratureError):
        literature.read_report(state, other, result['job_id'])
    report = Path(result['report'])
    report.unlink()
    private = tmp_path / 'private.md'
    private.write_text('private material')
    report.symlink_to(private)
    with pytest.raises(literature.ChatLiteratureError):
        literature.read_report(state, ident, result['job_id'])
    assert not literature.list_studies(state, ident)['jobs'][0]['report_available']


def test_paused_job_and_retry_retain_original_chat_route(prepared):
    state, search, ident = prepared
    class PreInvokeFailure(LiteratureBackend):
        prepared = False

        def ensure_thread(self, *args):
            if not self.prepared:
                self.prepared = True
                raise ConnectionError('fixture pre-invocation connection failure')
            return super().ensure_thread(*args)

    backend, retrieve = PreInvokeFailure(), Retriever()
    failed = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    assert failed['status'] == 'failed'
    path = state / 'research-chat' / (ident + '.json')
    row = json.loads(path.read_text())
    row['model'], row['reasoning_effort'] = 'different-model', 'high'
    path.write_text(json.dumps(row))
    resumed = literature.run_study(state, search, ident, request(job_id=failed['job_id']),
                                   backend=backend, retriever=retrieve)
    assert resumed['status'] == 'completed'
    assert all(call['model'] == 'gpt-6-sol' for call in backend.calls)
    assert literature.list_studies(state, ident)['jobs'][0]['agent_turns'] == 4


def test_cancel_event_and_progress_pass_through_actual_service(prepared):
    state, search, ident = prepared
    cancel = threading.Event()
    cancel.set()
    backend, retrieve, events = LiteratureBackend(), Retriever(), []
    result = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve,
                                  cancel_event=cancel, on_event=events.append)
    assert result['status'] == 'paused'
    assert result['stop_reason'] == 'cancelled'
    assert not backend.calls and not retrieve.calls
    assert events and events[-1]['event'] == 'literature.job_finished'
    index = json.loads((state / 'research-chat-literature' / (ident + '.json')).read_text())
    assert index['jobs'][0]['active_owner'] is None


def test_dead_process_running_manifest_projects_as_interrupted(prepared):
    state, search, ident = prepared
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    path = Path(result['manifest'])
    value = json.loads(path.read_text())
    value['status'] = 'running'
    path.write_text(json.dumps(value))
    listing = literature.list_studies(state, ident)
    assert listing['jobs'][0]['status'] == 'interrupted'
    assert listing['jobs'][0]['report_available']


def test_cli_uses_receipt_and_sanitizes_exception(prepared, monkeypatch, capsys):
    state, search, ident = prepared
    payloads = []
    def fake(state_dir, search_root, session, payload, **kwargs):
        assert isinstance(kwargs['cancel_event'], threading.Event)
        payloads.append(payload)
        return {'status': 'paused', 'job_id': 'b' * 32, 'thread_id': 'thread', 'report': '/safe/report.md'}
    monkeypatch.setattr(literature, 'run_study', fake)
    args = ['--state-dir', str(state), '--search-root', str(search), '--session', ident, '--query', QUERY]
    assert literature.main(args) == 0
    assert json.loads(capsys.readouterr().out)['thread_id'] == 'thread'
    assert 'max_rounds' not in payloads[0] and 'max_sources' not in payloads[0]
    _, fresh_request = literature._request(payloads[0], {})
    assert fresh_request['max_rounds'] == 2 and fresh_request['max_sources'] == 6
    assert literature.main([*args, '--job-id', 'b' * 32, '--max-rounds', '2']) == 0
    capsys.readouterr()
    assert payloads[1]['max_rounds'] == 2 and 'max_sources' not in payloads[1]
    assert literature.main([*args, '--max-rounds', '3', '--max-sources', '8']) == 0
    capsys.readouterr()
    assert payloads[2]['max_rounds'] == 3 and payloads[2]['max_sources'] == 8
    def fail(*args, **kwargs):
        raise ValueError('fixture private API key must not echo')
    monkeypatch.setattr(literature, 'run_study', fail)
    assert literature.main(args) == 1
    assert 'private API key' not in capsys.readouterr().out


@pytest.mark.parametrize('reason,message', [
    ('binding_mismatch', '文献索引与当前会话任务绑定不一致'),
    ('literature_record_invalid', '文献记录无法读取'),
])
def test_cli_preserves_safe_service_error_details(prepared, monkeypatch, capsys, reason, message):
    state, search, ident = prepared
    def fail(*args, **kwargs):
        raise literature.ChatLiteratureError(message, 409, reason)
    monkeypatch.setattr(literature, 'run_study', fail)
    assert literature.main(['--state-dir', str(state), '--search-root', str(search),
                            '--session', ident, '--query', QUERY]) == 1
    assert json.loads(capsys.readouterr().out) == {
        'ok': False, 'status': 'blocked', 'error': message, 'http_status': 409, 'stop_reason': reason}


def test_cli_invalid_budget_is_actionable_without_invoking_a_model(prepared, capsys):
    state, search, ident = prepared
    assert literature.main(['--state-dir', str(state), '--search-root', str(search),
                            '--session', ident, '--query', QUERY, '--max-rounds', '5']) == 1
    output = json.loads(capsys.readouterr().out)
    assert output['http_status'] == 400 and output['stop_reason'] == 'invalid_budget'
    assert 'max_rounds' in output['error'] and '1 to 4' in output['error']
    assert not (prep.workspace(state, ident) / 'literature').exists()


def test_read_only_http_status_report_and_unknown_job(prepared):
    state, search, ident = prepared
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(search), field_notes_state_dir=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with urlopen(base + f'/api/research-chat/{ident}/literature', timeout=5) as response:
            listing = json.loads(response.read())
        assert listing['jobs'][0]['status'] == 'completed'
        assert listing['jobs'][0]['job_id'] == result['job_id']
        with urlopen(base + f'/api/research-chat/{ident}/literature/' + result['job_id'], timeout=5) as response:
            detail = json.loads(response.read())
            assert response.headers['Cache-Control'] == 'no-store'
        assert detail['job']['query'] == QUERY and detail['job']['scope'] == SCOPE
        assert len(detail['job']['sources']) == 2 and len(detail['job']['activity']) == 4
        assert detail['job']['landscape_routes'][0]['name'] == 'Mechanism'
        assert detail['job']['landscape_routes'][0]['citations'][0]['doi'] == '10.1234/foundation'
        assert 'landscape_routes' not in listing['jobs'][0]
        assert all(source['agent_reviewed'] for source in detail['job']['sources'])
        assert str(search) not in json.dumps(detail) and str(state) not in json.dumps(detail)
        with pytest.raises(HTTPError) as caught:
            urlopen(base + f'/api/research-chat/{ident}/literature/' + 'f' * 32, timeout=5)
        assert caught.value.code == 404
        with urlopen(base + listing['jobs'][0]['report_href'], timeout=5) as response:
            assert 'Source-grounded observations' in response.read().decode()
            assert response.headers['Cache-Control'] == 'no-store'
        with pytest.raises(HTTPError) as caught:
            urlopen(base + f'/api/research-chat/{ident}/literature/' + 'f' * 32 + '/report', timeout=5)
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_status_ui_is_a_read_only_hook_for_real_research_sessions():
    from bs4 import BeautifulSoup
    from tm_observer.research_chat_literature_ui import literature_panel
    for mode in ('preparation', 'project', 'frontier'):
        panel = BeautifulSoup(literature_panel({'id': 'a' * 32, 'mode': mode}), 'html.parser')
        assert panel.select_one('[data-chat-literature-session]')
        assert panel.select_one('button[data-chat-literature-open]')['aria-haspopup'] == 'dialog'
        assert panel.select_one('dialog[aria-labelledby]')
        assert panel.select_one('[data-chat-literature-close]')
        assert panel.select_one('[data-chat-literature-report]')['target'] == '_blank'
    assert not literature_panel({'id': 'bad" onclick="x', 'mode': 'project'})
    assert not literature_panel({'id': 'a' * 32, 'mode': 'migration'})
    assert 'data-chat-literature-session' in page_body({'session': {'id': 'a' * 32, 'mode': 'project'}, 'tasks': []})


def test_browser_reads_actual_literature_status_and_report_without_model(prepared):
    from playwright.sync_api import expect, sync_playwright
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(), Retriever()
    result = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    manifest_path = Path(result['manifest'])
    manifest = json.loads(manifest_path.read_text())
    manifest.pop('delivery_integrity')  # This test projects a legacy unsealed fixture.
    manifest['sources'].insert(0, {'source_id': 'raw-unselected', 'title': 'Unscreened retrieval result',
                                  'reading_level': 'metadata', 'url': 'https://example.org/raw'})
    manifest_path.write_text(json.dumps(manifest))
    service_calls = len(backend.calls), len(retrieve.calls)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(search), field_notes_state_dir=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1100, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/research-chat/?session=' + ident)
            expect(page.locator('[data-chat-literature-label]')).to_have_text('Literature · 已交付报告')
            expect(page.locator('[data-chat-literature-counts]')).to_contain_text('可读正文 2 篇')
            card = page.locator('[data-chat-literature-open]')
            card.focus()
            page.keyboard.press('Enter')
            detail = page.locator('[data-chat-literature-dialog]')
            expect(detail).to_be_visible()
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text(QUERY)
            expect(page.locator('[data-chat-literature-scope]')).to_have_text(SCOPE)
            expect(page.locator('[data-chat-literature-activity] li')).to_have_count(4)
            expect(page.locator('[data-chat-literature-landscape-title]')).to_have_text('研究脉络')
            expect(page.locator('[data-chat-literature-landscape-routes]')).to_contain_text('Mechanism')
            expect(page.locator('[data-chat-literature-landscape-routes] a')).to_have_attribute('href', 'https://doi.org/10.1234/foundation')
            expect(page.locator('[data-chat-literature-sources] li')).to_have_count(2)
            expect(page.locator('[data-chat-literature-sources]')).to_contain_text('Agent 已完成阅读')
            expect(page.locator('[data-chat-literature-sources]')).not_to_contain_text('Unscreened retrieval result')
            raw = page.locator('[data-chat-literature-raw-wrap]')
            expect(raw.locator('summary')).to_contain_text('原始检索候选 · 待筛选')
            expect(page.locator('[data-chat-literature-raw-sources]')).not_to_be_visible()
            raw.locator('summary').click()
            expect(page.locator('[data-chat-literature-raw-sources]')).to_contain_text('Unscreened retrieval result')
            link = page.locator('[data-chat-literature-report]')
            expect(link).to_be_visible()
            with page.expect_popup() as opened:
                link.click()
            report = opened.value
            report.wait_for_load_state()
            expect(report.locator('[data-literature-report]')).to_contain_text('Source-grounded observations')
            report.close()
            page.set_viewport_size({'width': 390, 'height': 844})
            for theme in ('white', 'black', 'blue'):
                page.evaluate('(theme)=>document.documentElement.setAttribute("data-sciver-theme",theme)', theme)
                bounds = page.locator('[data-chat-literature-session]').bounding_box()
                assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 391
                detail_bounds = detail.bounding_box()
                assert detail_bounds['x'] >= 0 and detail_bounds['x'] + detail_bounds['width'] <= 391
                assert detail_bounds['height'] <= 844 * .85
            page.keyboard.press('Escape')
            expect(detail).not_to_be_visible()
            expect(card).to_be_focused()
            page.screenshot(path='/tmp/sciver-chat-literature-status.png', full_page=True)
            assert not errors
            browser.close()
        assert service_calls == (len(backend.calls), len(retrieve.calls))
        assert literature.read_report(state, ident, result['job_id'])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_detail_projects_only_bounded_public_source_and_agent_fields(prepared):
    state, search, ident = prepared
    result = literature.run_study(state, search, ident, request(), backend=LiteratureBackend(), retriever=Retriever())
    path = Path(result['manifest'])
    manifest = json.loads(path.read_text())
    manifest['sources'] = [
        {'title': 'Unsafe link', 'url': 'javascript:alert(1)', 'reading_level': 'metadata',
         'text_path': '/private/token', 'materialization': {'secret': 'hidden'}},
        {'title': 'DOI fallback', 'url': 'https://account:secret@example.com/private',
         'doi': '10.1234/public', 'reading_level': 'abstract'},
        {'title': 'Broken URL', 'url': 'https://[invalid', 'reading_level': 'unknown'},
        {'title': 'Public source', 'url': 'https://example.org/paper', 'reading_level': 'full_text'},
    ] * 60
    manifest['invocations'] = [{'key': 'round-01-source-01', 'stage': 'targeted_source',
        'status': 'running', 'prompt_path': '/private/prompt', 'error': 'secret API token'}] * 110
    manifest['status'] = 'running'
    path.write_text(json.dumps(manifest))
    detail = literature.read_study(state, ident, result['job_id'])['job']
    assert len(detail['sources']) == 200 and len(detail['activity']) == 100
    assert detail['sources'][0]['url'] == ''
    assert detail['sources'][1]['url'] == 'https://doi.org/10.1234/public'
    assert detail['sources'][2]['url'] == '' and detail['sources'][2]['reading_level'] == 'metadata'
    assert detail['sources'][3]['url'] == 'https://example.org/paper'
    serialized = json.dumps(detail)
    assert '/private/' not in serialized and 'secret' not in serialized
    assert detail['activity'][0]['label'] == '核对文献材料'
    assert detail['activity'][0]['status'] == 'interrupted'
    other = chat.create(state, {'runs': []}, {'mode': 'preparation', 'entry': 'clear'})['session_id']
    with pytest.raises(literature.ChatLiteratureError, match='不存在'):
        literature.read_study(state, other, result['job_id'])
    with pytest.raises(literature.ChatLiteratureError):
        literature.read_study(state, ident, '../unsafe')


def test_browser_opens_running_study_before_report_and_keeps_job_selection(prepared):
    from playwright.sync_api import expect, sync_playwright
    state, search, ident = prepared
    backend, retrieve = LiteratureBackend(), Retriever()
    first = literature.run_study(state, search, ident, request(), backend=backend, retriever=retrieve)
    second = literature.run_study(state, search, ident, request(query='A newer running study'),
                                  backend=backend, retriever=retrieve)
    second_path = Path(second['manifest'])
    manifest = json.loads(second_path.read_text())
    manifest.pop('delivery_integrity')  # An in-progress fixture has no completed-job seal.
    manifest.update(status='running', literature_agent_invoked=True, model_invoked=True,
                    output={'landscape': {'major_routes': [{'name': '<script>Preliminary mechanism</script>',
                                                           'focus': 'A preliminary scientific map is already available.'}]}})
    manifest['invocations'] = [{'key': 'round-01-landscape', 'stage': 'landscape', 'status': 'running'}]
    manifest['sources'] = [{'title': '<script>Unsafe source title</script>', 'url': 'javascript:alert(1)',
                            'reading_level': 'abstract', 'source_id': 'new-source'}]
    second_path.write_text(json.dumps(manifest))
    Path(second['report']).unlink()
    index_path = state / 'research-chat-literature' / (ident + '.json')
    index = json.loads(index_path.read_text())
    index['jobs'][-1]['active_owner'] = {'pid': __import__('os').getpid()}
    index_path.write_text(json.dumps(index))
    owner_handle = (second_path.parent / '.literature-job.lock').open('a+')
    fcntl.flock(owner_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    service_calls = len(backend.calls), len(retrieve.calls)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(search), field_notes_state_dir=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/research-chat/?session=' + ident)
            expect(page.locator('[data-chat-literature-label]')).to_have_text('Literature · 正在调研')
            page.locator('[data-chat-literature-open]').click()
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text('A newer running study')
            expect(page.locator('[data-chat-literature-progress]')).to_contain_text('正在梳理领域与选择文献')
            expect(page.locator('[data-chat-literature-landscape-title]')).to_have_text('阶段梳理')
            expect(page.locator('[data-chat-literature-landscape-note]')).to_contain_text('正在核对原文，以下为阶段结果')
            expect(page.locator('[data-chat-literature-landscape-routes]')).to_contain_text('<script>Preliminary mechanism</script>')
            assert page.locator('[data-chat-literature-landscape-routes] script').count() == 0
            expect(page.locator('[data-chat-literature-report]')).not_to_be_visible()
            expect(page.locator('[data-chat-literature-no-report]')).to_be_visible()
            expect(page.locator('[data-chat-literature-sources]')).to_contain_text('<script>Unsafe source title</script>')
            assert page.locator('[data-chat-literature-sources] a').count() == 0
            selector = page.locator('[data-chat-literature-jobs]')
            selector.select_option(first['job_id'])
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text(QUERY)
            expect(page.locator('[data-chat-literature-report]')).to_be_visible()
            page.wait_for_timeout(4200)
            expect(selector).to_have_value(first['job_id'])
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text(QUERY)
            # Delay an already-read response beyond a job change; stale details must not win.
            page.evaluate('''(firstId)=>{
                const original=window.fetch;
                window.fetch=async(...args)=>{
                    const response=await original(...args);
                    if(String(args[0]).endsWith('/literature/'+firstId)){
                        window.detailResponseWaiting=true;
                        await new Promise(resolve=>setTimeout(resolve,700));
                    }
                    return response;
                };
            }''', first['job_id'])
            selector.select_option(second['job_id'])
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text('A newer running study')
            selector.select_option(first['job_id'])
            page.wait_for_function('window.detailResponseWaiting===true')
            selector.select_option(second['job_id'])
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text('A newer running study')
            page.wait_for_timeout(850)
            expect(page.locator('[data-chat-literature-detail-query]')).to_have_text('A newer running study')
            page.locator('[data-chat-literature-close]').click()
            expect(page.locator('[data-chat-literature-dialog]')).not_to_be_visible()
            assert not errors
            browser.close()
        assert service_calls == (len(backend.calls), len(retrieve.calls))
    finally:
        owner_handle.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_report_browser_renders_clickable_markdown_without_executing_raw_html(prepared, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    state, search, ident = prepared
    report = ('# Polymer topology\n\n## Representative mechanisms\n\n'
              '[Original paper](https://doi.org/10.1234/fixture)\n\n'
              '</script><script>window.literatureUnsafe = true</script>\n\n'
              '<img src="invalid" onerror="window.literatureUnsafe = true">')
    monkeypatch.setattr(literature, 'read_report', lambda *args: report)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(StateCache(search), field_notes_state_dir=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    report_url = base + f'/api/research-chat/{ident}/literature/' + 'e' * 32 + '/report'
    try:
        with urlopen(report_url, timeout=5) as response:
            body = response.read().decode()
        assert '&lt;/script&gt;' in body and '\\u003c/script>' in body
        assert 'research-chat.js?v=' in body and 'research-chat.css?v=' in body
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1100, 'height': 900})
            context.route('https://doi.org/**', lambda route: route.fulfill(body='Original paper fixture'))
            page = context.new_page()
            requests, errors = [], []
            page.on('request', lambda request: requests.append(request.url))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(report_url)
            content = page.locator('[data-literature-report]')
            expect(content.locator('h1')).to_have_text('Polymer topology')
            expect(content.locator('h2')).to_have_text('Representative mechanisms')
            link = content.get_by_role('link', name='Original paper')
            expect(link).to_have_attribute('href', 'https://doi.org/10.1234/fixture')
            with page.expect_popup() as opened:
                link.click()
            expect(opened.value).to_have_url('https://doi.org/10.1234/fixture')
            opened.value.close()
            assert page.evaluate('window.literatureUnsafe') is None
            assert content.locator('script, img[onerror]').count() == 0
            for theme in ('white', 'black'):
                page.evaluate('(theme)=>document.documentElement.setAttribute("data-sciver-theme",theme)', theme)
                expect(content.locator('h1')).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not any('/api/research-chat/models' in url or '/settings' in url for url in requests)
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_public_sources_prioritize_selected_and_stable_doi_representatives_without_scientific_filtering():
    raw = {'source_id': 'raw', 'title': 'Unscreened retrieval', 'reading_level': 'abstract'}
    reviewed = {'source_id': 'doi:10.1234/reviewed', 'doi': '10.1234/reviewed', 'title': 'Reviewed source'}
    selected = {'source_id': 'doi:10.1234/selected', 'doi': '10.1234/selected', 'title': 'Selected for reading'}
    representative = {'source_id': 'doi:10.1234/representative', 'doi': '10.1234/representative',
                      'title': 'Representative source'}
    manifest = {'sources': [raw, representative, selected, reviewed],
                'source_reviews': {'doi:10.1234/reviewed': {}},
                'selected_sources': [{'source_id': 'old-provider-key', 'paper': {'doi': '10.1234/selected'}}],
                'output': {'landscape': {'representative_papers': [
                    {'source_ref': 'https://doi.org/10.1234/REPRESENTATIVE'}]}}}
    sources = literature._public_sources(manifest)
    assert [source['title'] for source in sources] == [
        'Reviewed source', 'Selected for reading', 'Representative source', 'Unscreened retrieval']
    assert [source['selection_status'] for source in sources] == [
        'reviewed', 'selected', 'representative', 'retrieval_candidate']
    assert sources[0]['agent_reviewed'] and not any(source['agent_reviewed'] for source in sources[1:])
    assert sources[-1]['reading_level'] == 'abstract'
    # The public cap must not hide a selected source located after raw hits.
    manifest['sources'] = [dict(raw, title=f'Raw {index}') for index in range(205)] + [selected]
    capped = literature._public_sources(manifest)
    assert len(capped) == 200 and capped[0]['title'] == 'Selected for reading'


@pytest.mark.parametrize('extra', [
    {'source_aliases': {'arxiv:cond-mat/0007270': 'doi:10.1063/1.1290134'}},
    {'source_arxiv_id': 'cond-mat/0007270'},
    {'review_records': [{'source_ref': 'https://doi.org/10.1063/1.1290134'}]},
])
def test_public_review_projection_resolves_persisted_arxiv_doi_identity_aliases(extra):
    source = {'source_id': 'doi:10.1063/1.1290134', 'doi': '10.1063/1.1290134',
              'title': 'Reviewed original source', 'reading_level': 'full_text'}
    if extra.get('source_arxiv_id'):
        source['arxiv_id'] = extra['source_arxiv_id']
    manifest = {'sources': [source], 'source_aliases': extra.get('source_aliases', {}),
                'source_reviews': {'arxiv:cond-mat/0007270': {
                    'evidence_records': extra.get('review_records', [])}}}
    before = json.dumps(manifest, sort_keys=True)
    projected = literature._public_sources(manifest)
    assert projected[0]['agent_reviewed'] and projected[0]['selection_status'] == 'reviewed'
    assert json.dumps(manifest, sort_keys=True) == before
    manifest['source_reviews'] = {}
    manifest['selected_sources'] = [{'source_id': 'doi:10.1063/1.1290134'}]
    projected = literature._public_sources(manifest)
    assert projected[0]['selection_status'] == 'selected' and not projected[0]['agent_reviewed']


def test_preliminary_landscape_projection_is_bounded_and_cites_only_catalogued_dois():
    manifest = {'sources': [{'doi': '10.1234/known', 'title': 'Known representative'}],
                'output': {'landscape': {'major_routes': [
                    {'name': 'N' * 500, 'focus': 'F' * 2000, 'representative_papers': [
                        {'source_ref': 'https://doi.org/10.1234/KNOWN'},
                        {'source_ref': '10.1234/uncatalogued'},
                        {'source_ref': '/private/source.md'}]}] * 8}}}
    before = json.dumps(manifest, sort_keys=True)
    routes = literature._public_landscape_routes(manifest)
    assert len(routes) == 6
    assert len(routes[0]['name']) == 160 and len(routes[0]['focus']) == 1200
    assert routes[0]['citations'] == [{'doi': '10.1234/known', 'title': 'Known representative',
                                      'url': 'https://doi.org/10.1234/known'}]
    assert '/private' not in json.dumps(routes)
    assert json.dumps(manifest, sort_keys=True) == before
