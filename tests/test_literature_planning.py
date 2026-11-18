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


def test_transient_retry_reuses_thread_and_has_finite_call_bound(tmp_path):
    backend = RecordingBackend(failures=1)
    route = RoleRoute('fixture', 'fixture')
    with pytest.raises(ConnectionError):
        plan_search(tmp_path, {}, route, backend=backend)
    first = execution(tmp_path)
    assert first['status'] == 'failed' and first['retryable']
    assert plan_search(tmp_path, {}, route, backend=backend) == PLAN
    saved = execution(tmp_path)
    assert backend.threads == [None, 'own-planning-thread']
    assert saved['attempts'] == 2
    assert [item['status'] for item in saved['attempt_history']] == ['failed', 'completed']


def test_transient_retry_limit_never_makes_unchecked_duplicate_calls(tmp_path):
    backend = RecordingBackend(failures=99)
    route = RoleRoute('fixture', 'fixture')
    for _ in range(2):
        with pytest.raises(ConnectionError):
            plan_search(tmp_path, {}, route, backend=backend)
    with pytest.raises(RuntimeError):
        plan_search(tmp_path, {}, route, backend=backend)
    assert backend.calls == 2 and not execution(tmp_path)['retryable']


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
