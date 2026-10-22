"""No-model recovery evidence and shared launcher regression."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from theory_master import supervisor_repair_receipts as receipts
from theory_master import supervisor_continuity as continuity
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import review_runtime_failure, resume_blocker
from theory_master.supervisor_runtime_repair import _TESTS


@pytest.fixture
def stopped(tmp_path):
    engine = tmp_path / 'sdk/theory_master'
    engine.mkdir(parents=True)
    (engine / '__init__.py').write_text('')
    (engine / 'workflow.py').write_text('class Workflow:\n    def repaired(self):\n        return 42\n')
    (engine / 'canvas_projection.py').write_text('VALUE = 1\n')
    (engine.parent / 'tests').mkdir()
    for name in _TESTS:
        (engine.parent / 'tests' / name).write_text('def test_offline():\n    assert True\n')
    cycle = tmp_path / 'research/1'
    (cycle / 'checkpoints').mkdir(parents=True)
    (cycle / '.theory-master-cycle').write_text('theory-master/v2\ntest\n')
    checkpoint = cycle / 'checkpoints/000001-locked.json'
    checkpoint.write_text('{"cycle_id":"1","hypothesis_lock":{"id":"H-1"}}')
    state = {'status': 'failed', 'cycle_id': '1', 'phase': 'investigator', 'resumable': True,
             'protocol_health': 'valid', 'hypothesis_lock': {'id': 'H-1'},
             'last_safe_checkpoint': str(checkpoint),
             'recovery': {'kind': 'AttributeError', 'category': 'runtime',
                          'at': 'original-occurrence',
                          'message': "'Workflow' object has no attribute 'repaired'"}}
    write_json(cycle / 'research_state.json', state)
    log = ('Traceback (most recent call last):\n'
           f'  File "{engine / "workflow.py"}", line 1, in run\n'
           f'AttributeError: {state["recovery"]["message"]}\n')
    return engine, cycle, state, log


def method_receipt(stopped):
    engine, cycle, state, log = stopped
    return receipts.validate_existing_method(cycle, state, engine=engine, log=log)


def test_already_fixed_method_checked_in_fresh_interpreter_without_model(stopped):
    engine, cycle, state, log = stopped
    record = method_receipt(stopped)
    assert record['disposition'] == 'already_fixed'
    assert record['validator'] == receipts.ATTRIBUTE_VALIDATOR
    assert json.loads(record['tests'])['available'] is True
    assert receipts.inspect_repair_receipt(cycle, state, engine=engine) == record
    assert record['runtime_verification'] == 'pending'
    assert record['failure'] == state['recovery']
    assert record['checkpoint'] == state['last_safe_checkpoint']
    assert not record['resume_performed']


@pytest.mark.parametrize('mutation', ['new_failure', 'checkpoint', 'source', 'tests', 'receipt', 'identity'])
def test_stale_or_tampered_receipts_rejected(stopped, mutation):
    engine, cycle, state, _ = stopped
    method_receipt(stopped)
    if mutation == 'new_failure':
        state['recovery']['at'] = 'new-occurrence-same-message'
    elif mutation == 'checkpoint':
        Path(state['last_safe_checkpoint']).write_text('{}')
    elif mutation == 'source':
        (engine / 'workflow.py').write_text('class Workflow: pass\n')
    elif mutation == 'tests':
        (engine.parent / 'tests' / _TESTS[0]).write_text('')
    elif mutation == 'receipt':
        Path(state[receipts.RECEIPT_KEY]['path']).write_text('{}')
    else:
        state['hypothesis_lock']['id'] = 'H-2'
    with pytest.raises(ValueError):
        receipts.inspect_repair_receipt(cycle, state, engine=engine)


@pytest.mark.parametrize('source', [
    'class Workflow: pass\n',
    'class Workflow:\n    repaired = property(lambda s: 42)\n',
    'class Workflow:\n    def repaired(self): pass\n    def __getattribute__(self, key): raise AttributeError(key)\n',
])
def test_missing_method_descriptor_or_dynamic_lookup_does_not_validate(stopped, source):
    engine, cycle, state, _ = stopped
    (engine / 'workflow.py').write_text(source)
    assert method_receipt(stopped) is None
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('guard', [
    {'protocol_health': 'violation'}, {'resumable': False},
    {'investigator_budget': {'exhausted': True}}, {'abandoned_cycle': True},
])
def test_method_probe_does_not_override_guards(stopped, guard):
    _, _, state, _ = stopped
    state.update(guard)
    assert method_receipt(stopped) is None


def test_method_probe_requires_exact_failure_trace(stopped):
    engine, cycle, state, log = stopped
    assert receipts.validate_existing_method(cycle, state, engine=engine, log='') is None
    state['recovery']['message'] = "'Other' object has no attribute 'repaired'"
    assert receipts.validate_existing_method(cycle, state, engine=engine, log=log) is None


def test_runtime_receipt_reused_and_resume_count_is_not_reset(stopped):
    engine, cycle, state, _ = stopped
    method_receipt(stopped)
    with patch.object(receipts, '__file__', str(engine / 'supervisor_repair_receipts.py')), \
         patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair') as repair, \
         patch.object(receipts, 'validate_existing_method') as probe:
        for i in range(3):
            result = review_runtime_failure(backend=None, research_root=cycle.parent, cycle_dir=cycle,
                state=state, state_path=cycle / 'research_state.json', incident_id='INC-test')
            assert result['action'] == 'resume'
            assert state['supervisor_runtime_resume_count'] == i + 1
        result = review_runtime_failure(backend=None, research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / 'research_state.json', incident_id='INC-test')
    assert result['action'] == 'hold'
    repair.assert_not_called()
    probe.assert_not_called()


def test_actual_workflow_mixin_method_can_be_probed_without_instantiation(stopped):
    _, cycle, state, _ = stopped
    engine = Path(receipts.__file__).resolve().parent
    state['recovery']['message'] = "'Workflow' object has no attribute '_ensure_locked_hypothesis_projection'"
    write_json(cycle / 'research_state.json', state)
    log = ('Traceback (most recent call last):\n'
           f'  File "{engine / "workflow.py"}", line 1, in run\n'
           f'AttributeError: {state["recovery"]["message"]}')
    record = receipts.validate_existing_method(cycle, state, engine=engine, log=log)
    assert record['disposition'] == 'already_fixed'


def test_canvas_observed_patch_uses_actual_offline_subprocess(stopped):
    engine, cycle, state, _ = stopped
    before = receipts.source_manifest(engine)
    (engine / 'canvas_projection.py').write_text('VALUE = 2\n')
    with patch('theory_master.supervisor_runtime_repair._register_auto_change', return_value='TMC-test'), \
         patch('theory_master.supervisor_runtime_repair._verify_auto_change', return_value=True):
        record = receipts.validate_engineering_changes(cycle, state, engine=engine,
            target=engine / 'canvas_projection.py', before=before)
    assert record['phase'] == 'repair_validated'
    assert record['disposition'] == 'validated_observed_patch'
    assert receipts.inspect_repair_receipt(cycle, state, engine=engine) == record


def test_changes_to_validator_inputs_fail_closed(stopped):
    engine, cycle, state, _ = stopped
    before = receipts.source_manifest(engine)
    (engine / 'canvas_projection.py').write_text('VALUE = 2\n')
    (engine.parent / 'tests' / _TESTS[0]).write_text('def test_always_pass(): pass\n')
    result = receipts.validate_engineering_changes(cycle, state, engine=engine,
        target=engine / 'canvas_projection.py', before=before)
    assert result['phase'] == 'blocked'
    assert receipts.RECEIPT_KEY not in state


def test_single_runner_consumes_engineering_handoff_and_deduplicates(stopped):
    from theory_master.supervisor import _handle_failed_state
    _, cycle, state, _ = stopped
    state['recovery'] = {'kind': 'RuntimeError', 'message': 'Unknown frontier H'}
    write_json(cycle / 'research_state.json', state)
    review = {'action': 'hold', 'reason': 'Needs engineering repair',
              'repair_suggestion': 'Fix the frontier resolver', 'session_id': 'SUP-test'}
    with patch('theory_master.supervisor._supervisor_backend', return_value=None), \
         patch('theory_master.runtime_supervisor_review.review_runtime_failure', return_value=review), \
         patch.object(continuity, 'attempt_engineering_skill',
                      return_value={'status': 'engineering_attempted'}) as engineering:
        assert not _handle_failed_state(cycle.parent, repair_enabled=True)
        assert not _handle_failed_state(cycle.parent, repair_enabled=True)
    engineering.assert_called_once()
    handoff = json.loads((cycle / 'run_state.json').read_text())['repair_handoff']
    assert handoff['engineering_skill']['repair_phase'] == 'blocked'
    assert 'No SDK validator' in handoff['engineering_skill']['reason']


def test_paused_cycle_never_enters_paid_engineering(stopped):
    _, cycle, state, _ = stopped
    state['recovery']['action'] = 'pause'
    review = {'action': 'hold', 'repair_suggestion': 'Fix code'}
    assert not continuity.engineering_skill_eligible(state, review)
    assert 'Operator pause' in resume_blocker(state, runtime_revalidated=True)


def test_operator_state_changed_during_engineering_is_not_overwritten(stopped):
    _, cycle, state, _ = stopped
    state['recovery'] = {'kind': 'RuntimeError', 'message': 'Unknown frontier H'}
    write_json(cycle / 'research_state.json', state)
    review = {'action': 'hold', 'repair_suggestion': 'Repair resolver'}

    def change(*args):
        current = json.loads((cycle / 'research_state.json').read_text())
        current['recovery']['action'] = 'pause'
        write_json(cycle / 'research_state.json', current)
        return {'status': 'engineering_attempted'}

    with patch('theory_master.runtime_supervisor_review.review_runtime_failure', return_value=review), \
         patch.object(continuity, 'attempt_engineering_skill', side_effect=change):
        result = continuity.review_and_repair_runtime_failure(
            backend=None, research_root=cycle.parent, cycle_dir=cycle, state=state,
            state_path=cycle / 'research_state.json', incident_id='INC-test', scheduler_locked=True)
    assert result['action'] == 'hold'
    assert result['reason'] == 'operator_pause'
    assert json.loads((cycle / 'research_state.json').read_text())['recovery']['action'] == 'pause'


def test_single_runner_peer_guard_prevents_engineering(stopped):
    from theory_master.supervisor import _handle_failed_state
    _, cycle, _, _ = stopped
    with patch('theory_master.worker_runtime.list_workers', return_value=[{'process_alive': True}]), \
         patch('theory_master.supervisor._supervisor_backend') as factory:
        assert not _handle_failed_state(cycle.parent, repair_enabled=True)
    factory.assert_not_called()


def test_missing_validator_or_gate_does_not_repeat_paid_turns(stopped):
    _, cycle, state, _ = stopped
    state['recovery'] = {'kind': 'RuntimeError', 'message': 'Unknown frontier H'}
    review = {'action': 'hold', 'repair_suggestion': 'Repair resolver'}
    with patch('theory_master.runtime_supervisor_review.review_runtime_failure', return_value=review), \
         patch.object(continuity, 'attempt_engineering_skill') as engineering:
        result = continuity.review_and_repair_runtime_failure(
            backend=None, research_root=cycle.parent, cycle_dir=cycle, state=state,
            state_path=cycle / 'research_state.json', incident_id='INC-test')
    assert 'exclusive scheduler gate' in result['reason']
    engineering.assert_not_called()


def test_no_edit_reply_does_not_own_concurrent_changes(stopped):
    from theory_master import supervisor_incident_session as sessions
    from theory_master.identity import identity_block
    from theory_master.execution_policy import REPO_GUARD, is_history_rewriting_command
    _, cycle, _, _ = stopped
    record = sessions.open_notification_incident(
        research_root=cycle.parent, cycle_dir=cycle, task_id='1',
        notification_id='SUP-0123456789abcdef', code='http_400', summary='Unsupported model route')

    class Backend:
        def invoke(self, role, prompt, *args):
            assert 'git commit' in prompt and 'pending review' in prompt
            assert 'already_fixed' in prompt
            return 'fake', 'I made no edits.'
        def drop_thread(self, thread):
            pass

    with patch.object(sessions, '_engineering_snapshot', side_effect=[{'a.py': 'old'}, {'a.py': 'new'}]):
        result = sessions._run_model_follow_up(cycle.parent, record['id'], Backend())
    assert result['repair']['phase'] == 'changes_observed'
    assert result['repair']['engine_change_attribution'] == 'observed_unattributed'
    assert 'git commit' in identity_block('incident_supervisor')
    assert 'commit' in REPO_GUARD
    assert is_history_rewriting_command('git commit -m repair')
    sessions.record_engineering_validation(cycle.parent, record['id'],
        {'phase': 'blocked', 'reason': 'No supported validator'}, None)
    saved, messages = sessions.load_incident(cycle.parent, record['id'])
    assert saved['status'] == 'active'
    assert saved['repair_status'] == 'blocked'
    assert messages[-1]['metadata']['source'] == 'sdk_repair_validation'


def test_runtime_activity_verification_binds_original_receipt_not_latest_state(stopped):
    from theory_master.supervisor_intervention import SupervisorSession, append_supervisor_verification
    _, cycle, state, _ = stopped
    original = {'path': 'original.json', 'sha256': 'original-digest'}
    session = SupervisorSession(cycle, profile='runtime', incident_id='INC-test', code='run_failed', summary='test')
    session.append('repair_binding', 'authorized', repair_receipt=original)
    next_session = SupervisorSession(cycle, profile='runtime', incident_id='INC-next', code='run_failed', summary='next')
    next_session.append('repair_binding', 'new failure', repair_receipt={'path': 'another.json'})
    append_supervisor_verification(cycle, session_id=session.session_id, verified=True,
                                   detail='LLM_MESSAGE after original managed launch')
    rows = [json.loads(line) for line in (cycle / 'supervisor/session.jsonl').read_text().splitlines()]
    assert rows[-1]['repair_receipt'] == original
    assert rows[-1]['session_id'] == session.session_id
    assert rows[-1]['status'] == 'verified'
    assert rows[-1]['root_cause_resolved'] is False
