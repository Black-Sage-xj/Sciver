"""Offline, invocation-bound migration to the explicit execution-limit protocol."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor_repair_receipts as receipts
from theory_master.runtime import write_json
from theory_master.runtime_supervisor_review import review_runtime_failure, resume_blocker
from theory_master.supervisor_intervention import MAX_RUNTIME_RESUMES


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


@pytest.fixture
def stopped(tmp_path):
    cycle = tmp_path / 'research/14'
    (cycle / 'checkpoints').mkdir(parents=True)
    (cycle / 'workspace').mkdir()
    invocation, thread = 'inv-14-investigator-original', 'original-investigator-thread'
    start, finish = '2026-11-18T22:51:34+08:00', '2026-11-18T22:56:32+08:00'
    objective = 'Investigate the unchanged frozen objective using the historical delivery contract.'
    lock = {'id': 'H-14', 'hypothesis': 'Frozen hypothesis.', 'scope': 'Frozen scope.',
            'decisive_test': 'Frozen decisive test.'}
    checkpoint = cycle / 'checkpoints/000011-attempt-started.json'
    write_json(checkpoint, {'cycle_id': '14', 'hypothesis_lock': lock})
    state = {'status': 'failed', 'cycle_id': '14', 'phase': 'failed', 'execution_active': False,
             'resumable': True, 'protocol_health': 'valid', 'scientific_verdict': None,
             'hypothesis_lock': lock, 'last_safe_checkpoint': str(checkpoint),
             'active_invocation_id': invocation, 'threads': {'investigator': thread},
             'native_goal': {'status': 'blocked', 'thread_id': thread,
                'runtime_failure_kind': 'native_goal_dependency_blocked',
                'objective_sha256': hashlib.sha256(objective.encode()).hexdigest()},
             'recovery': {'action': 'error', 'kind': 'NativeGoalRuntimeFailure', 'at': finish}}
    write_rows(cycle / 'events.jsonl', [
        {'type': 'role.started', 'at': start, 'payload': {'role': 'investigator',
            'stage': 'investigate', 'invocation_id': invocation, 'thread_id': thread}},
        {'type': 'run.failed', 'at': finish, 'payload': {'error_kind': 'NativeGoalRuntimeFailure'}},
    ])
    write_rows(cycle / 'invocations.jsonl', [
        {'event': 'invocation.prepared', 'invocation_id': invocation, 'state': 'prepared',
            'role': 'investigator', 'stage': 'investigate', 'commit_state': 'not_committed'},
        {'event': 'invocation.running', 'invocation_id': invocation, 'state': 'running', 'thread_id': thread},
        {'event': 'invocation.failed', 'invocation_id': invocation, 'state': 'failed',
            'error_type': 'NativeGoalRuntimeFailure'},
    ])
    # Historical input telemetry has no thread/invocation ID; the single
    # role.started window and the persisted objective hash bind it exactly.
    write_rows(cycle / 'runtime_events.jsonl', [
        {'ts': start, 'event': 'AGENT_INPUT', 'role': 'investigator', 'stage': 'investigate',
            'input_kind': 'native_goal_objective', 'content': objective},
        {'ts': finish, 'event': 'LLM_MESSAGE', 'role': 'investigator', 'message_role': 'assistant',
            'thread_id': thread, 'turn_id': 'native-goal-turn',
            'content': 'This invocation reports a blocked Goal. Its scientific reason is not judged by this validator.'},
        {'ts': finish, 'event': 'LLM_TURN_COMPLETED', 'role': 'investigator',
            'thread_id': thread, 'turn_id': 'native-goal-turn'},
    ])
    write_json(cycle / 'scientific_model_out.json', {'frozen': 'original model'})
    write_json(cycle / 'workspace/input_gate.json', {'frozen': 'original evidence'})
    write_json(cycle / 'research_state.json', state)
    return cycle, state


def validate(stopped):
    cycle, state = stopped
    return receipts.validate_existing_execution_limit_protocol(
        cycle, state, engine=Path(receipts.__file__).resolve().parent)


def edit_rows(path, edit):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    edit(rows)
    write_rows(path, rows)


def scientific_files(cycle):
    return {str(path.relative_to(cycle)): path.read_bytes() for path in cycle.rglob('*')
            if path.is_file() and 'supervisor' not in path.parts and path.name != 'research_state.json'}


def test_protocol_receipt_only_certifies_a_new_delivery_opportunity(stopped):
    cycle, state = stopped
    original, files = copy.deepcopy(state), scientific_files(cycle)
    record = validate(stopped)
    assert record['validator'] == receipts.EXECUTION_LIMIT_PROTOCOL_VALIDATOR
    assert record['disposition'] == 'already_fixed' and record['runtime_verification'] == 'pending'
    assert record['resume_performed'] is False
    assert receipts.inspect_repair_receipt(cycle, state) == record
    evidence = json.loads(record['tests'])
    assert evidence['resume_preparation'] == 'retained_thread_delivery_only'
    assert set(evidence['evidence_paths']) == set(record['extra_inputs'])
    assert evidence['historical_objective_sha256'] == state['native_goal']['objective_sha256']
    assert resume_blocker(state).startswith('Native Goal requires')
    assert resume_blocker(state, runtime_revalidated=True) is None
    assert {k: v for k, v in state.items() if not k.startswith('supervisor_')} == original
    assert scientific_files(cycle) == files
    assert not (cycle / 'workspace/.tm-handoff').exists()


@pytest.mark.parametrize('mutation', [
    'new_objective', 'stale_objective', 'duplicate_objective', 'foreign_objective_thread',
    'foreign_objective_invocation', 'foreign_report_thread', 'foreign_report_invocation',
    'foreign_report_role', 'foreign_report_turn', 'private_report', 'missing_report',
    'report_after_failure', 'missing_completion', 'provider_error', 'provider_error_without_role',
    'persisted_provider_error', 'later_invocation',
    'foreign_start_thread', 'foreign_start_invocation', 'foreign_ledger_thread', 'foreign_active_invocation',
    'committed', 'other_error', 'other_stage', 'other_native_kind', 'other_native_status',
    'pause', 'interrupt', 'budget', 'budget_validation', 'protocol', 'active', 'not_resumable',
    'abandoned', 'verdict', 'checkpoint', 'resume_budget', 'missing_events', 'oversize', 'symlink',
])
def test_protocol_migration_rejects_incomplete_binding_and_independent_guards(stopped, mutation):
    cycle, state = stopped
    runtime, events, ledger = (cycle / name for name in
                               ('runtime_events.jsonl', 'events.jsonl', 'invocations.jsonl'))
    if mutation in {'new_objective', 'stale_objective', 'duplicate_objective'}:
        def mutate(rows):
            if mutation == 'new_objective':
                rows[0]['content'] += ' SDK_EXECUTION_LIMIT_HANDOFF_V1'
                state['native_goal']['objective_sha256'] = hashlib.sha256(rows[0]['content'].encode()).hexdigest()
            elif mutation == 'stale_objective':
                rows[0]['content'] += ' tampered'
            else:
                rows.append(copy.deepcopy(rows[0]))
        edit_rows(runtime, mutate)
    elif mutation.startswith('foreign_objective_'):
        field = 'thread_id' if mutation.endswith('thread') else 'invocation_id'
        edit_rows(runtime, lambda rows: rows[0].update({field: 'foreign'}))
    elif mutation.startswith('foreign_report_'):
        field = {'thread': 'thread_id', 'invocation': 'invocation_id', 'role': 'role', 'turn': 'turn_id'}[mutation.rsplit('_', 1)[1]]
        edit_rows(runtime, lambda rows: rows[1].update({field: 'foreign'}))
    elif mutation in {'private_report', 'missing_report', 'report_after_failure', 'missing_completion',
                      'provider_error', 'provider_error_without_role'}:
        def mutate(rows):
            if mutation == 'private_report':
                rows[1]['message_role'] = 'user'
            elif mutation == 'missing_report':
                rows[1]['content'] = ''
            elif mutation == 'report_after_failure':
                rows[1]['ts'] = '2026-11-18T22:56:33+08:00'
            elif mutation == 'missing_completion':
                rows.pop()
            else:
                rows.append({**rows[-1], 'event': 'LLM_TURN_FAILED', 'error': 'terminal provider failure'})
                if mutation == 'provider_error_without_role':
                    rows[-1].pop('role')
        edit_rows(runtime, mutate)
    elif mutation == 'persisted_provider_error':
        state['native_goal']['provider_failure'] = 'terminal provider failure'
    elif mutation == 'later_invocation':
        edit_rows(events, lambda rows: rows.append({'type': 'role.started',
            'at': '2026-11-18T22:56:33+08:00', 'payload': {'invocation_id': 'later'}}))
    elif mutation.startswith('foreign_start_'):
        field = 'thread_id' if mutation.endswith('thread') else 'invocation_id'
        edit_rows(events, lambda rows: rows[0]['payload'].update({field: 'foreign'}))
    elif mutation == 'foreign_ledger_thread':
        edit_rows(ledger, lambda rows: rows[1].update(thread_id='foreign'))
    elif mutation == 'foreign_active_invocation':
        state['active_invocation_id'] = 'foreign'
    elif mutation == 'committed':
        edit_rows(ledger, lambda rows: rows[-1].update(commit_state='committed'))
    elif mutation == 'other_error':
        edit_rows(ledger, lambda rows: rows[-1].update(error_type='RuntimeError'))
    elif mutation == 'other_stage':
        edit_rows(ledger, lambda rows: rows[0].update(stage='unexpected'))
    elif mutation in {'other_native_kind', 'other_native_status'}:
        state['native_goal']['runtime_failure_kind' if mutation.endswith('kind') else 'status'] = 'other'
    elif mutation in {'pause', 'interrupt'}:
        state['recovery']['action'] = mutation
    elif mutation == 'budget':
        state['investigator_budget'] = {'exhausted': True}
    elif mutation == 'budget_validation':
        state['recovery']['category'] = 'budget_validation'
    elif mutation == 'protocol':
        state['protocol_health'] = 'violation'
    elif mutation == 'active':
        state['execution_active'] = True
    elif mutation == 'not_resumable':
        state['resumable'] = False
    elif mutation == 'abandoned':
        state['abandoned_cycle'] = True
    elif mutation == 'verdict':
        state['scientific_verdict'] = 'supported'
    elif mutation == 'checkpoint':
        Path(state['last_safe_checkpoint']).unlink()
    elif mutation == 'resume_budget':
        state['supervisor_runtime_resume_count'] = MAX_RUNTIME_RESUMES
    elif mutation == 'missing_events':
        events.unlink()
    elif mutation == 'oversize':
        runtime.write_bytes(b' ' * 16_000_001)
    else:
        outside = cycle.parent / 'foreign-events.jsonl'
        runtime.rename(outside)
        runtime.symlink_to(outside)
    write_json(cycle / 'research_state.json', state)
    assert validate(stopped) is None
    assert receipts.RECEIPT_KEY not in state


@pytest.mark.parametrize('mutation', ['events', 'runtime_events', 'invocations', 'state', 'checkpoint'])
def test_receipt_rechecks_every_historical_record_and_frozen_binding(stopped, mutation):
    cycle, state = stopped
    validate(stopped)
    if mutation in {'events', 'runtime_events', 'invocations'}:
        path = cycle / (mutation + '.jsonl')
        path.write_text(path.read_text() + '\n')
    elif mutation == 'state':
        state['native_goal']['thread_id'] = 'foreign'
    else:
        path = Path(state['last_safe_checkpoint'])
        path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError):
        receipts.inspect_repair_receipt(cycle, state)


def test_runtime_review_uses_the_receipt_once_without_a_model_or_scientific_change(stopped):
    cycle, state = stopped
    original, files = copy.deepcopy(state), scientific_files(cycle)
    backend = MagicMock()
    with patch('theory_master.supervisor_runtime_repair.attempt_runtime_repair') as engineering:
        result = review_runtime_failure(backend=backend, research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / 'research_state.json', incident_id='INC-offline-protocol')
        assert result['action'] == 'resume'
        assert state['supervisor_runtime_resume_count'] == 1
        assert len(state['supervisor_execution_limit_protocol_reentries']) == 1
        assert resume_blocker(state, runtime_revalidated=True).startswith('The retained-thread')
        second = review_runtime_failure(backend=backend, research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / 'research_state.json', incident_id='INC-offline-repeat')
    assert second['action'] == 'hold' and 'already used' in second['reason']
    assert state['supervisor_runtime_resume_count'] == 1
    assert len(state['supervisor_consumed_repairs']) == 1
    assert {k: v for k, v in state.items() if not k.startswith('supervisor_')} == original
    assert scientific_files(cycle) == files
    assert validate(stopped) is None
    backend.invoke_runtime_review.assert_not_called()
    backend.invoke.assert_not_called()
    engineering.assert_not_called()


def test_consumed_opportunity_cannot_be_reissued_after_the_receipt_is_removed(stopped):
    cycle, state = stopped
    state['supervisor_execution_limit_protocol_reentries'] = [receipts.execution_limit_protocol_reentry_key(state)]
    write_json(cycle / 'research_state.json', state)
    assert validate(stopped) is None
    assert 'already used' in resume_blocker(state, runtime_revalidated=True)


def test_a_saved_protocol_receipt_never_adds_to_the_ordinary_resume_budget(stopped):
    cycle, state = stopped
    validate(stopped)
    state['supervisor_runtime_resume_count'] = MAX_RUNTIME_RESUMES
    write_json(cycle / 'research_state.json', state)
    result = review_runtime_failure(backend=MagicMock(), research_root=cycle.parent, cycle_dir=cycle,
        state=state, state_path=cycle / 'research_state.json', incident_id='INC-offline-budget')
    assert result['action'] == 'hold'
    assert 'authorized Supervisor runtime resumes' in result['reason']
    assert state['supervisor_runtime_resume_count'] == MAX_RUNTIME_RESUMES
    assert not state.get('supervisor_execution_limit_protocol_reentries')


def test_readonly_peer_review_does_not_create_a_protocol_receipt(stopped):
    cycle, state = stopped
    with patch.object(receipts, 'validate_existing_execution_limit_protocol') as validator:
        result = review_runtime_failure(backend=MagicMock(), research_root=cycle.parent, cycle_dir=cycle,
            state=state, state_path=cycle / 'research_state.json', incident_id='INC-offline-peer',
            readonly_provider_probe_only=True)
    assert result['action'] == 'hold'
    validator.assert_not_called()
    assert receipts.RECEIPT_KEY not in state
