"""Offline search planning boundary, without provider calls."""
import json
import pytest
from theory_master.literature_planning import validate_plan, plan_search
from theory_master.backend_config import RoleRoute


def test_plan_validation_deduplicates_queries_and_rejects_prose_or_missing_anchors():
    plan = {"queries": [{"query": "ring polymer mechanics", "purpose": "architecture"},
                        {"query": "ring polymer mechanics", "purpose": "duplicate"}],
            "relevance_terms": ["polymer"], "scope": "材料"}
    assert len(validate_plan(plan)["queries"]) == 1
    for bad in ({**plan, "queries": []}, {**plan, "relevance_terms": []},
                {**plan, "queries": [{"query": "a" * 181, "purpose": "bad"}]}):
        with pytest.raises(ValueError): validate_plan(bad)


def test_planner_has_own_persisted_query_only_delivery_and_no_replay(tmp_path):
    class Backend:
        calls = 0
        def ensure_thread(self, *args): return "planning-thread"
        def invoke(self, role, prompt, thread, model, provider, *, cancel_event=None):
            self.calls += 1
            assert "not a review" in prompt and "Do not invent papers" in prompt
            return thread, json.dumps({"queries": [{"query": "ring polymer elasticity", "purpose": "architecture"}],
                                      "relevance_terms": ["polymer"], "scope": "研究兴趣"})
    backend = Backend()
    route = RoleRoute("fixture", "fixture")
    value = plan_search(tmp_path, {"query": "研究兴趣"}, route, backend=backend)
    assert plan_search(tmp_path, {}, route, backend=backend) == value
    assert backend.calls == 1
    assert json.loads((tmp_path / "planning/execution.json").read_text())["thread_id"] == "planning-thread"


PLAN = {'queries': [{'query': 'soft matter elasticity', 'purpose': 'mechanisms'}],
        'relevance_terms': ['soft matter'], 'scope': '研究兴趣'}


class RecordingBackend:
    def __init__(self, failures=0):
        self.calls = 0
        self.threads = []
        self.failures = failures
    def ensure_thread(self, role, previous, model, provider):
        self.threads.append(previous)
        return previous or 'own-planning-thread'
    def invoke(self, role, prompt, thread, model, provider, *, on_event=None, cancel_event=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError('temporary connection failure')
        if on_event:
            on_event({'last_request_usage': {'input_tokens': 100, 'output_tokens': 20,
                                            'cached_input_tokens': 80}})
        return thread, json.dumps(PLAN)


def execution(tmp_path):
    return json.loads((tmp_path / 'planning/execution.json').read_text())


def test_saved_response_recovers_without_constructing_or_invoking_backend(tmp_path):
    home = tmp_path/'planning'
    home.mkdir()
    (home/'response.txt').write_text(json.dumps(PLAN))
    (home/'execution.json').write_text(json.dumps({'status': 'running', 'phase': 'invoking',
        'started_at': 'original-start', 'thread_id': 'original-thread', 'attempts': 1,
        'reasoning_effort': 'high'}))
    backend = RecordingBackend()
    assert plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend) == PLAN
    saved = execution(tmp_path)
    assert saved['response_recovered'] and saved['status'] == 'completed'
    assert saved['started_at'] == 'original-start' and saved['reasoning_effort'] == 'high'
    assert saved['thread_id'] == 'original-thread'
    assert backend.calls == 0 and backend.threads == []


def test_completed_receipt_retains_started_effort_attempt_and_usage(tmp_path):
    backend = RecordingBackend()
    route = RoleRoute('fixture', 'fixture', reasoning_effort='medium')
    plan_search(tmp_path, {}, route, backend=backend)
    saved = execution(tmp_path)
    assert saved['started_at'] and saved['completed_at']
    assert saved['reasoning_effort'] == 'medium' and saved['attempts'] == 1
    assert saved['attempt_history'][0]['status'] == 'completed'
    assert saved['usage_available'] and saved['usage']['input_tokens'] == 100
    assert saved['attempt_history'][0]['usage']['output_tokens'] == 20
    assert saved['error'] == '' and saved['phase'] == 'completed'


def test_unacknowledged_transient_request_requires_original_thread_recovery(tmp_path):
    backend = RecordingBackend(failures=1)
    route = RoleRoute('fixture', 'fixture')
    with pytest.raises(ConnectionError):
        plan_search(tmp_path, {}, route, backend=backend)
    first = execution(tmp_path)
    assert first['status'] == 'failed' and not first['retryable']
    for _ in range(3):
        with pytest.raises(RuntimeError, match='recover its thread'):
            plan_search(tmp_path, {}, route, backend=backend)
    saved = execution(tmp_path)
    assert backend.threads == [None] and backend.calls == 1
    assert saved['attempts'] == 1 and saved['thread_id'] == 'own-planning-thread'
    assert [item['status'] for item in saved['attempt_history']] == ['failed']


def test_thread_start_failure_can_retry_before_any_model_request(tmp_path):
    class Backend(RecordingBackend):
        def ensure_thread(self, role, previous, model, provider):
            thread = super().ensure_thread(role, previous, model, provider)
            if len(self.threads) == 1:
                raise ConnectionError('temporary connection before invoking')
            return thread

    backend = Backend()
    route = RoleRoute('fixture', 'fixture')
    with pytest.raises(ConnectionError):
        plan_search(tmp_path, {}, route, backend=backend)
    assert execution(tmp_path)['retryable'] and backend.calls == 0
    assert plan_search(tmp_path, {}, route, backend=backend) == PLAN
    assert backend.calls == 1 and execution(tmp_path)['thread_start_attempts'] == 2


def test_thread_start_retries_are_bounded_without_resetting_attempts(tmp_path):
    class Backend(RecordingBackend):
        def ensure_thread(self, *args):
            self.threads.append(args[1])
            raise ConnectionError('temporary connection before invoking')

    backend = Backend()
    route = RoleRoute('fixture', 'fixture')
    for _ in range(2):
        with pytest.raises(ConnectionError):
            plan_search(tmp_path, {}, route, backend=backend)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            plan_search(tmp_path, {}, route, backend=backend)
    assert len(backend.threads) == 2 and backend.calls == 0
    assert execution(tmp_path)['thread_start_attempts'] == 2 and not execution(tmp_path)['retryable']


def test_crashed_inflight_invocation_without_response_needs_recovery(tmp_path):
    home = tmp_path/'planning'
    home.mkdir()
    (home/'execution.json').write_text(json.dumps({'status': 'running', 'phase': 'invoking',
        'thread_id': 'existing-thread', 'attempts': 1}))
    backend = RecordingBackend()
    for _ in range(2):
        with pytest.raises(RuntimeError, match='recover its thread'):
            plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend)
    assert backend.calls == 0 and backend.threads == []
    assert execution(tmp_path)['status'] == 'blocked'


@pytest.mark.parametrize('raw', ['not JSON', 'x' * (64 * 1024 + 1)])
def test_invalid_saved_response_is_persisted_failure_without_model_retry(tmp_path, raw):
    home = tmp_path/'planning'
    home.mkdir()
    (home/'response.txt').write_text(raw)
    backend = RecordingBackend()
    for _ in range(2):
        with pytest.raises(ValueError):
            plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend)
    saved = execution(tmp_path)
    assert saved['status'] == 'failed' and saved['phase'] == 'validation'
    assert saved['error'] and not saved['retryable'] and backend.calls == 0


def test_publication_failure_replays_actual_model_response(tmp_path, monkeypatch):
    from theory_master import literature_planning as planning
    real_write = planning.write_json
    def interrupt(path, value):
        if path.name == 'plan.json':
            raise OSError('simulated interruption during publication')
        real_write(path, value)
    backend = RecordingBackend()
    route = RoleRoute('fixture', 'fixture')
    monkeypatch.setattr(planning, 'write_json', interrupt)
    with pytest.raises(OSError):
        plan_search(tmp_path, {}, route, backend=backend)
    monkeypatch.setattr(planning, 'write_json', real_write)
    assert plan_search(tmp_path, {}, route, backend=backend) == PLAN
    assert backend.calls == 1 and execution(tmp_path)['response_recovered']


@pytest.mark.parametrize('field,value', [
    ('queries', [{'query': {'not': 'text'}, 'purpose': 'mechanisms'}]),
    ('relevance_terms', [{'not': 'text'}]), ('scope', 'x' * 2001),
])
def test_planner_does_not_coerce_or_silently_truncate_invalid_fields(field, value):
    with pytest.raises(ValueError):
        validate_plan({**PLAN, field: value})


def test_legacy_running_receipt_without_phase_never_implicitly_repeats_request(tmp_path):
    home = tmp_path/'planning'
    home.mkdir()
    (home/'execution.json').write_text(json.dumps({'status': 'running', 'thread_id': 'legacy-thread'}))
    backend = RecordingBackend()
    with pytest.raises(RuntimeError, match='recover its thread'):
        plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend)
    assert backend.calls == 0


def test_active_planner_lock_prevents_concurrent_duplicate_model_call(tmp_path):
    class Backend(RecordingBackend):
        def invoke(self, role, prompt, thread, model, provider, **kwargs):
            with pytest.raises(RuntimeError, match='already active'):
                plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=self)
            return super().invoke(role, prompt, thread, model, provider, **kwargs)
    backend = Backend()
    assert plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend) == PLAN
    assert backend.calls == 1


def test_legacy_failed_invoking_retry_flag_cannot_repeat_unacknowledged_request(tmp_path):
    home = tmp_path / 'planning'
    home.mkdir()
    (home / 'execution.json').write_text(json.dumps({'status': 'failed', 'phase': 'invoking',
        'retryable': True, 'thread_id': 'original-thread', 'attempts': 1}))
    backend = RecordingBackend()
    with pytest.raises(RuntimeError, match='recover its thread'):
        plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend)
    assert backend.calls == 0 and backend.threads == []
    assert execution(tmp_path)['thread_id'] == 'original-thread'


def test_before_invocation_resume_retains_original_thread_and_rejects_replacement(tmp_path):
    home = tmp_path / 'planning'
    home.mkdir()
    seed = {'status': 'running', 'phase': 'before_invocation', 'thread_id': 'original-thread', 'attempts': 0}
    (home / 'execution.json').write_text(json.dumps(seed))

    class WrongThread(RecordingBackend):
        def ensure_thread(self, *args):
            self.threads.append(args[1])
            return 'replacement-thread'

    backend = WrongThread()
    with pytest.raises(ValueError, match='retain the original thread'):
        plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend)
    assert backend.calls == 0 and backend.threads == ['original-thread']
    assert execution(tmp_path)['thread_id'] == 'original-thread'
    assert not execution(tmp_path)['retryable']


def test_before_invocation_resume_uses_existing_thread_without_replacement(tmp_path):
    home = tmp_path / 'planning'
    home.mkdir()
    (home / 'execution.json').write_text(json.dumps({'status': 'running', 'phase': 'before_invocation',
        'thread_id': 'original-thread', 'attempts': 0}))
    backend = RecordingBackend()
    assert plan_search(tmp_path, {}, RoleRoute('fixture', 'fixture'), backend=backend) == PLAN
    assert backend.threads == ['original-thread'] and backend.calls == 1
    assert execution(tmp_path)['thread_id'] == 'original-thread'


def test_wrong_returned_thread_retains_original_raw_and_never_reinvokes(tmp_path):
    class WrongThread(RecordingBackend):
        def invoke(self, *args, **kwargs):
            _, raw = super().invoke(*args, **kwargs)
            return 'foreign-thread', raw

    backend = WrongThread()
    route = RoleRoute('fixture', 'fixture')
    with pytest.raises(ValueError, match='different thread'):
        plan_search(tmp_path, {}, route, backend=backend)
    raw_path = tmp_path / 'planning/response.txt'
    raw = raw_path.read_bytes()
    for _ in range(3):
        with pytest.raises(ValueError, match='different thread'):
            plan_search(tmp_path, {}, route, backend=backend)
    saved = execution(tmp_path)
    assert raw_path.read_bytes() == raw and backend.calls == 1
    assert saved['thread_id'] == 'own-planning-thread' and saved['returned_thread_id'] == 'foreign-thread'
    assert saved['attempt_history'][0]['thread_id'] == 'own-planning-thread'
    assert not (tmp_path / 'planning/plan.json').exists()


@pytest.mark.parametrize('stage', ['thread_start', 'invoking'])
def test_control_and_provider_failures_with_network_words_cannot_retry(tmp_path, stage):
    from theory_master.codex_errors import CodexProviderRequestRejected, CodexTurnCancelled
    from theory_master.provider_errors import ProviderQuotaExhausted
    from theory_master.workflow_support import RecoveryStop

    for index, kind in enumerate((RecoveryStop, ProviderQuotaExhausted,
                                  CodexProviderRequestRejected, CodexTurnCancelled)):
        root = tmp_path / str(index)
        error = kind('operator/provider connection timeout stop')

        class Failed(RecordingBackend):
            def ensure_thread(self, *args):
                thread = super().ensure_thread(*args)
                if stage == 'thread_start':
                    raise error
                return thread

            def invoke(self, *args, **kwargs):
                self.calls += 1
                raise error

        backend = Failed()
        route = RoleRoute('fixture', 'fixture')
        with pytest.raises(kind) as caught:
            plan_search(root, {}, route, backend=backend)
        assert caught.value is error and not execution(root)['retryable']
        with pytest.raises(RuntimeError):
            plan_search(root, {}, route, backend=backend)
        assert backend.calls == int(stage == 'invoking') and len(backend.threads) == 1


@pytest.mark.parametrize('failure', [None, ConnectionError('original invocation connection failure'),
                                    KeyboardInterrupt('original interrupt')])
def test_owned_cleanup_error_does_not_hide_delivery_or_original_failure(tmp_path, monkeypatch, failure):
    from theory_master import codex_backend
    created = []

    class Owned(RecordingBackend):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.closes = 0
            created.append(self)

        def invoke(self, *args, **kwargs):
            if failure:
                self.calls += 1
                raise failure
            return super().invoke(*args, **kwargs)

        def close(self):
            self.closes += 1
            raise RuntimeError('cleanup failed')

    monkeypatch.setattr(codex_backend, 'CodexBackend', Owned)
    route = RoleRoute('fixture', 'fixture')
    if failure:
        with pytest.raises(type(failure)) as caught:
            plan_search(tmp_path, {}, route)
        assert caught.value is failure
    else:
        assert plan_search(tmp_path, {}, route) == PLAN
        assert execution(tmp_path)['status'] == 'completed'
    assert len(created) == 1 and created[0].closes == 1
    assert 'cleanup failed' in execution(tmp_path)['cleanup_error']
