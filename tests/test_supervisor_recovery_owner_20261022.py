"""Deterministic ownership/acceptance regression; all model turns are fake."""
import copy
import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor_recovery as jobs
from theory_master import supervisor_candidate_repair as candidates
from theory_master.runtime import write_json
from test_supervisor_repair_receipts_20261022 import stopped, method_receipt
from theory_master import supervisor_repair_receipts as receipts
from theory_master.runtime_supervisor_review import review_runtime_failure


def test_owner_survives_retry_windows_and_config_change_wakes(stopped):
    _, cycle, state, _ = stopped
    original = copy.deepcopy(state)
    for turn in range(8):
        job = jobs.begin_action(cycle, state)
        result = jobs.finish_action(cycle, state, {'action': 'hold', 'reason': f'Rejected plan {turn}'})
        assert result['status'] == 'waiting'
        assert result['plan_revision'] == turn + 1
        assert result['recovery_owner']
        assert not jobs.due(cycle, state)
        assert jobs.due(cycle, state, at=result['next_wakeup_epoch'] + 1)
    (cycle.parent / 'config.toml').write_text('model="corrected"')
    assert jobs.due(cycle, state)
    assert original == state


def test_working_owner_has_process_identity_and_dead_owner_is_reclaimed(stopped):
    _, cycle, state, _ = stopped
    jobs.begin_action(cycle, state)
    assert not jobs.due(cycle, state, at=10**12)
    with patch('theory_master.runtime_manager.process_identity_alive', return_value=False):
        assert jobs.due(cycle, state)
        assert not jobs.projection(cycle)['executing']


def test_activity_bound_to_authorized_session_and_new_failure_wakes(stopped):
    _, cycle, state, _ = stopped
    jobs.begin_action(cycle, state)
    jobs.finish_action(cycle, state, {'action': 'resume', 'session_id': 'current'})
    jobs.record_activity(cycle, verified=True, detail='old activity', session_id='old')
    assert jobs.read_job(cycle)['status'] == 'resume_authorized'
    jobs.record_activity(cycle, verified=True, detail='new tool call', session_id='current')
    assert jobs.read_job(cycle)['status'] == 'activity_verified'
    assert not jobs.due(cycle, state)
    state['recovery']['at'] = 'new-failure'
    assert jobs.due(cycle, state)


def test_operational_attention_is_distinct_from_science_and_operator_pause(stopped):
    _, cycle, state, _ = stopped
    state.update(status='needs_attention', attention={'code': 'runtime_failure', 'reason': 'tool broke'})
    assert jobs.operational_incident(state)
    assert jobs.due(cycle, state)
    state['attention']['code'] = 'investigator_native_goal_blocked'
    assert not jobs.due(cycle, state)
    state['attention']['code'] = 'runtime_failure'
    state['recovery']['action'] = 'pause'
    assert not jobs.due(cycle, state)


def test_fresh_repair_after_cap_grants_only_one_resume_and_reissued_receipt_cannot_refresh(stopped):
    engine, cycle, state, _ = stopped
    state['supervisor_runtime_resume_count'] = 3
    method_receipt(stopped)
    with patch.object(receipts, '__file__', str(engine / 'supervisor_repair_receipts.py')):
        args = dict(backend=None, research_root=cycle.parent, cycle_dir=cycle,
                    state=state, state_path=cycle / 'research_state.json', incident_id='test')
        assert review_runtime_failure(**args)['action'] == 'resume'
        assert state['supervisor_runtime_resume_count'] == 4
        identity = receipts.repair_credit_key(state)
        method_receipt(stopped)
        assert receipts.repair_credit_key(state) == identity
        assert review_runtime_failure(**args)['action'] == 'hold'
        state['supervisor_repair_receipt']['repair_identity'] = 'fabricated-fresh-repair'
        with pytest.raises(ValueError, match='identity mismatch'):
            receipts.inspect_repair_receipt(cycle, state, engine=engine)


@pytest.fixture
def candidate_pair(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    (old / 'theory_master').mkdir(parents=True)
    (old / 'theory_master/__init__.py').write_text('')
    (old / 'theory_master/helper.py').write_text('def first(values):\n    return values[0]\n')
    (old / 'tests').mkdir()
    for name in candidates._TESTS:
        (old / 'tests' / name).write_text('from theory_master.helper import first\ndef test_nonempty():\n    assert first([42]) == 42\n')
    shutil.copytree(old, new)
    (new / 'theory_master/helper.py').write_text('def first(values):\n    return values[0] if values else None\n')
    (new / 'reproduction').mkdir()
    (new / 'reproduction/test_incident.py').write_text('from theory_master.helper import first\ndef test_empty():\n    assert first([]) is None\n')
    (new / 'repair_plan.json').write_text(json.dumps({'regression_tests': [candidates._TESTS[0]], 'reason': 'Empty sequence was indexed'}))
    state = {'recovery': {'kind': 'IndexError', 'message': 'list index out of range'}}
    backend = MagicMock()
    backend.invoke_runtime_review.return_value = ('independent-review', '{"approved":true,"reason":"Realistic empty-input defect"}')
    return old, new, state, backend


def accept(pair):
    old, new, state, backend = pair
    return candidates.accept_candidate(old, new, state, backend=backend, model='Fake', provider='Fake')


def test_real_differential_acceptance_and_frozen_regressions(candidate_pair):
    result = accept(candidate_pair)
    assert result['phase'] == 'repair_validated', result
    assert result['baseline']['tests'] == 1 and not result['baseline']['passed']
    assert result['candidate']['passed']
    assert len(result['regressions']) == 2 * len(candidates._TESTS)
    assert candidates.promote_candidate(candidate_pair[0] / 'theory_master', candidate_pair[0], candidate_pair[1], result)


@pytest.mark.parametrize('fault', ['protected', 'test_change', 'no_original_failure', 'reject_review', 'no_fix'])
def test_candidate_cannot_self_accept_or_bypass_original_failure(candidate_pair, fault):
    old, new, state, backend = candidate_pair
    if fault == 'protected':
        (new / 'theory_master/supervisor.py').write_text('pass\n')
    elif fault == 'test_change':
        (new / 'tests' / candidates._TESTS[0]).write_text('def test_fake(): pass\n')
    elif fault == 'no_original_failure':
        state['recovery']['message'] = 'unrelated exception'
    elif fault == 'reject_review':
        backend.invoke_runtime_review.return_value = ('review', '{"approved":false,"reason":"Not attributable"}')
    elif fault == 'no_fix':
        (new / 'theory_master/helper.py').write_text('def first(values):\n    return values[0]  # still broken\n')
    assert accept(candidate_pair)['phase'] == 'blocked'


def test_offline_validator_rejects_skips_no_tests_and_network(tmp_path):
    test = tmp_path / 'test_probe.py'
    for body in ['import pytest\ndef test_skip(): pytest.skip("fake")\n', '',
                 'import socket\ndef test_network(): socket.create_connection(("127.0.0.1", 1))\n']:
        test.write_text(body)
        assert not candidates.run_tests(tmp_path, [test])['passed']


def test_rejected_candidate_evidence_survives_workspace(candidate_pair, tmp_path):
    old, new, _, _ = candidate_pair
    cycle = tmp_path / 'research/1'
    cycle.mkdir(parents=True)
    evidence = candidates.retain_evidence(cycle, old, new, {'phase': 'blocked', 'reason': 'test failure'})
    shutil.rmtree(new)
    data = json.loads(evidence.read_text())
    assert 'helper.py' in data['diff']
    assert 'reproduction/test_incident.py' in data['files']
    assert data['result']['phase'] == 'blocked'


def test_ui_projection_is_not_an_execution_claim(stopped):
    from theory_master.tui.render import render_identity
    _, cycle, state, _ = stopped
    jobs.begin_action(cycle, state)
    jobs.finish_action(cycle, state, {'action': 'hold', 'reason': 'provider_probe_required'})
    output = render_identity({'supervisor_enabled': True, 'supervisor_recovery': jobs.projection(cycle)})
    assert 'waiting' in output and 'provider_probe_required' in output
    assert '额度自调: 关闭' in output and '执行中' not in output


def test_local_configuration_receipt_revalidates_file_and_preserves_remote_blockers(stopped):
    from theory_master import supervisor_configuration_repair as config
    from theory_master.backend_config import task_route_catalog
    engine, cycle, state, _ = stopped
    root = cycle.parent
    (root / '.theory-master').mkdir()
    path = root / '.theory-master/web-role-routes.json'
    pair = task_route_catalog()[0]
    route = {**pair, 'reasoning_effort': 'medium', 'activation': 'next_invocation'}
    path.write_text(json.dumps({'schema': 'theory-master/web-role-routes/v1', 'roles': {'investigator': route}}))
    state['recovery'] = {'kind': 'ValueError', 'category': 'runtime_validation', 'message': 'unknown task role model/provider'}
    state['runtime_role_routes'] = {'investigator': pair}
    write_json(cycle / 'research_state.json', state)
    log = 'Traceback (most recent call last):\n  File "/sdk/theory_master/backend_config.py", line 345\nValueError: unknown task role model/provider'
    with patch.object(config, '__file__', str(engine / 'supervisor_configuration_repair.py')):
        result = config.validate_configuration(root, cycle, state, log=log)
        assert result['phase'] == 'repair_validated'
        assert receipts.inspect_repair_receipt(cycle, state, engine=engine)['validator'] == config.CONFIG_VALIDATOR
        path.write_text(path.read_text() + '\n')
        with pytest.raises(ValueError, match='inputs changed'):
            receipts.inspect_repair_receipt(cycle, state, engine=engine)
        state['runtime_role_routes']['investigator']['model'] = 'unsupported-frozen-model'
        assert 'frozen_route_requires_repair' in config.validate_configuration(root, cycle, state, log=log)['reason']
        state['recovery']['message'] = 'authentication failed'
        assert 'provider_probe_required' in config.validate_configuration(root, cycle, state, log=log)['reason']


def test_single_runner_waits_then_replans_without_holding_scheduler_lock(stopped):
    from theory_master.supervisor import _recover_failed_state
    _, cycle, state, _ = stopped
    stop = MagicMock()
    stop.is_set.return_value = False
    def handle(*args, **kwargs):
        job = jobs.begin_action(cycle, state)
        jobs.finish_action(cycle, state, {'action': 'hold', 'reason': 'validator failed'})
        return False
    def waiting(seconds):
        import fcntl
        gate = cycle.parent / '.theory-master/scheduler.lock'
        gate.parent.mkdir(exist_ok=True)
        with gate.open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state['recovery']['action'] = 'pause'
    stop.wait.side_effect = waiting
    with patch('theory_master.supervisor._latest_scientific_state', side_effect=lambda root: ('1', state, None)), \
         patch('theory_master.supervisor._error_repair_enabled', return_value=True), \
         patch('theory_master.supervisor._handle_failed_state', side_effect=handle) as review, \
         patch('theory_master.supervisor.update_registry'):
        assert not _recover_failed_state(cycle.parent, repair_enabled=True, stop=stop)
    assert review.call_count == 1 and stop.wait.call_count == 1


def test_worker_replans_after_old_manual_cap_and_config_wakes_backoff(tmp_path):
    from test_unified_supervisor_continuity_20261019 import UnifiedSupervisorTests
    from theory_master.worker_runtime import worker_dir
    from theory_master.worker_supervisor_dispatch import dispatch_pending, _fingerprint
    cycle, state = UnifiedSupervisorTests().fixture(tmp_path)
    path = worker_dir(tmp_path, 'worker-1') / 'supervisor_dispatch.json'
    jobs.begin_action(cycle, state)
    job = jobs.finish_action(cycle, state, {'action': 'hold', 'reason': 'no validator'})
    path.write_text(json.dumps({'cycle': cycle.name, 'worker_id': 'worker-1', 'fingerprint': _fingerprint(state),
        'failure_signature': _fingerprint(state), 'status': 'retry_wait', 'recovery_attempts': 7,
        'retry_at_epoch': job['next_wakeup_epoch'] + 5000}))
    with patch('theory_master.worker_supervisor_dispatch.subprocess.Popen') as spawn:
        assert dispatch_pending(tmp_path)
        spawn.assert_not_called()
        (tmp_path / 'config.toml').write_text('changed="wake"')
        spawn.return_value.pid = 99999999
        assert dispatch_pending(tmp_path)
        spawn.assert_called_once()
    assert json.loads(path.read_text())['recovery_attempts'] == 8


def test_operational_attention_keeps_worker_claim_eligibility(tmp_path):
    from test_unified_supervisor_continuity_20261019 import UnifiedSupervisorTests
    from theory_master.worker_runtime import save_worker, list_workers
    from theory_master.worker_supervisor_dispatch import _eligible
    cycle, state = UnifiedSupervisorTests().fixture(tmp_path)
    state.update(status='needs_attention', attention={'code': 'runtime_failure', 'reason': 'Unknown frontier H'})
    write_json(cycle / 'research_state.json', state)
    save_worker(tmp_path, 'worker-1', status='NEEDS_ATTENTION')
    worker = list_workers(tmp_path)[0]
    assert _eligible(tmp_path, worker) is not None
    state['attention']['code'] = 'investigator_native_goal_blocked'
    write_json(cycle / 'research_state.json', state)
    assert _eligible(tmp_path, worker) is None


@pytest.mark.parametrize('pause_during_review', [False, True])
def test_full_candidate_pipeline_promotes_only_unchanged_failure(stopped, pause_during_review):
    engine, cycle, state, _ = stopped
    (engine / 'helper.py').write_text('def first(values):\n    return values[0]\n')
    state['recovery'] = {'kind': 'IndexError', 'message': 'list index out of range'}
    write_json(cycle / 'research_state.json', state)
    def edit(package, model, prompt):
        (package / 'theory_master/helper.py').write_text('def first(values):\n    return values[0] if values else None\n')
        (package / 'reproduction').mkdir()
        (package / 'reproduction/test_incident.py').write_text('from theory_master.helper import first\ndef test_empty():\n    assert first([]) is None\n')
        (package / 'repair_plan.json').write_text(json.dumps({'regression_tests': [candidates._TESTS[0]]}))
        return True, 'fake isolated editor'
    def review(*args):
        if pause_during_review:
            paused = copy.deepcopy(state)
            paused['recovery']['action'] = 'pause'
            write_json(cycle / 'research_state.json', paused)
        return 'independent', '{"approved":true,"reason":"Empty list defect"}'
    backend = MagicMock()
    backend.invoke_runtime_review.side_effect = review
    with patch.object(candidates, '__file__', str(engine / 'supervisor_candidate_repair.py')), \
         patch.object(candidates, '_invoke_codex', side_effect=edit), \
         patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-fake'), \
         patch('theory_master.supervisor_runtime_repair._verify_auto_change', return_value=True):
        result = candidates.attempt_candidate_repair(root=cycle.parent, cycle=cycle, state=state,
            review={'repair_suggestion': 'Fix empty input'}, backend=backend, model='Fake', provider='Fake')
    if pause_during_review:
        assert result['phase'] == 'blocked' and 'Cycle changed' in result['reason']
        assert (engine / 'helper.py').read_text() == 'def first(values):\n    return values[0]\n'
    else:
        assert result['phase'] == 'repair_validated', result
        assert receipts.inspect_repair_receipt(cycle, state, engine=engine)['validator'] == candidates.VALIDATOR
        assert 'if values else None' in (engine / 'helper.py').read_text()
    assert list((cycle / 'supervisor/candidates').glob('*.json'))


def test_multifile_install_rolls_back_on_concurrent_second_file_change(candidate_pair, tmp_path):
    from theory_master import supervisor_runtime_repair as narrow
    old, new, _, _ = candidate_pair
    (old / 'theory_master/second.py').write_text('VALUE=1\n')
    (new / 'theory_master/second.py').write_text('VALUE=2\n')
    live = tmp_path / 'live'
    shutil.copytree(old, live)
    actual = narrow._promote_patch
    def fail_second(target, before, after):
        return False if target.name == 'second.py' else actual(target, before, after)
    with patch.object(narrow, '_promote_patch', side_effect=fail_second):
        assert not candidates.promote_candidate(live / 'theory_master', old, new,
            {'sources': ['theory_master/helper.py', 'theory_master/second.py']})
    assert (live / 'theory_master/helper.py').read_bytes() == (old / 'theory_master/helper.py').read_bytes()


def test_diagnostic_backend_does_not_require_valid_scientific_configuration(tmp_path):
    from theory_master.supervisor import _supervisor_backend
    (tmp_path / '.theory-master').mkdir()
    (tmp_path / '.theory-master/web-role-routes.json').write_text('{invalid')
    with patch('theory_master.backends.CodexBackend') as backend:
        _supervisor_backend(tmp_path)
    assert set(backend.call_args.kwargs['routes']) == {'runtime_supervisor'}


def test_web_shows_waiting_owner_without_claiming_active_dialogue_is_work(stopped):
    from tm_observer import supervisor_view
    _, cycle, state, _ = stopped
    jobs.begin_action(cycle, state)
    jobs.finish_action(cycle, state, {'action': 'hold', 'reason': '需要有效验收凭证'})
    data = {'cycle': '1', 'current_cycle': '1', 'cycles': ['1'], 'sessions': [], 'warnings': []}
    with patch.object(supervisor_view, 'sessions', return_value=data), \
         patch.object(supervisor_view, '_cycle_dir', return_value=cycle), \
         patch.object(supervisor_view, 'supervisor_enabled_for', return_value=True):
        rendered = supervisor_view.page_body({'id': 'test'}, state=state)
    assert 'sdk:runtime_supervisor' in rendered and 'waiting' in rendered
    assert '需要有效验收凭证' in rendered and '恢复任务 · 执行中' not in rendered


def test_operational_incident_routing_has_recovery_owner():
    from theory_master.observability.incident_routing import classify_incident
    for code in jobs.OPERATIONAL_ATTENTION:
        route = classify_incident(severity='attention', kind='needs_attention', code=code)
        assert route['owner'] == 'supervisor_runtime'
    route = classify_incident(severity='attention', kind='needs_attention', code='investigator_native_goal_blocked')
    assert route['owner'] == 'supervisor_attention'


def test_rejected_candidate_replans_without_repeating_same_independent_review(stopped):
    engine, cycle, state, _ = stopped
    (engine / 'helper.py').write_text('VALUE=1\n')
    def edit(package, model, prompt):
        (package / 'theory_master/helper.py').write_text('VALUE=2\n')
        (package / 'reproduction').mkdir()
        (package / 'reproduction/test_incident.py').write_text('def test_repro():\n    assert True\n')
        (package / 'repair_plan.json').write_text(json.dumps({'regression_tests': [candidates._TESTS[0]]}))
        return True, 'fake'
    backend = MagicMock()
    backend.invoke_runtime_review.return_value = ('review', '{"approved":false,"reason":"Contrived reproducer"}')
    with patch.object(candidates, '__file__', str(engine / 'supervisor_candidate_repair.py')), \
         patch.object(candidates, '_invoke_codex', side_effect=edit) as editor, \
         patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-fake'):
        args = dict(root=cycle.parent, cycle=cycle, state=state, review={}, backend=backend, model='Fake', provider='Fake')
        jobs.begin_action(cycle, state)
        first = candidates.attempt_candidate_repair(**args)
        jobs.finish_action(cycle, state, {'action': 'hold', 'reason': first['reason'], 'engineering_skill': {'validation': first}})
        second = candidates.attempt_candidate_repair(**args)
    assert first['phase'] == second['phase'] == 'blocked'
    assert 'Unchanged rejected' in second['reason']
    assert backend.invoke_runtime_review.call_count == 1
    assert 'Contrived reproducer' in editor.call_args.args[2]


@pytest.mark.parametrize('pause_during_plan', [False, True])
def test_typed_configuration_proposal_is_validated_before_install(stopped, pause_during_plan):
    from theory_master import supervisor_configuration_repair as config
    from theory_master.backend_config import task_route_catalog
    engine, cycle, state, _ = stopped
    root = cycle.parent
    (root / '.theory-master').mkdir()
    path = root / '.theory-master/web-role-routes.json'
    pair = task_route_catalog()[0]
    route = {**pair, 'reasoning_effort': 'medium', 'activation': 'next_invocation'}
    payload = {'schema': 'theory-master/web-role-routes/v1', 'roles': {'investigator': {**route, 'model': 'invalid-model'}}}
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    state['recovery'] = {'kind': 'ValueError', 'message': 'unknown task role model/provider'}
    state['runtime_role_routes'] = {'investigator': pair}
    write_json(cycle / 'research_state.json', state)
    (root / '.theory-master/runner.log').write_text('Traceback (most recent call last):\n  File "/sdk/theory_master/backend_config.py", line 3\nValueError: unknown task role model/provider')
    backend = MagicMock()
    def plan(*args):
        if pause_during_plan:
            changed = copy.deepcopy(state)
            changed['recovery']['action'] = 'pause'
            write_json(cycle / 'research_state.json', changed)
        return 'planner', json.dumps({'roles': {'investigator': route}, 'reason': 'Correct invalid local model field'})
    backend.invoke_runtime_review.side_effect = plan
    with patch.object(config, '__file__', str(engine / 'supervisor_configuration_repair.py')), \
         patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-fake'), \
         patch('theory_master.supervisor_runtime_repair._verify_auto_change', return_value=True):
        args = dict(root=root, cycle=cycle, state=state, review={}, backend=backend, model='Fake', provider='Fake')
        if pause_during_plan:
            with pytest.raises(ValueError, match='Cycle changed'):
                config.attempt_configuration_repair(**args)
            assert path.read_bytes() == original
        else:
            result = config.attempt_configuration_repair(**args)
            assert result['phase'] == 'repair_validated'
            assert receipts.inspect_repair_receipt(cycle, state, engine=engine)['validator'] == config.CONFIG_VALIDATOR
