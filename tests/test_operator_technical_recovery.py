"""Bounded operator artifact recovery; all Goal reads are fake GET-only probes."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import operator_technical_recovery as recovery
from theory_master.native_goal_recovery import goal_identity
from theory_master.runtime import write_json
from theory_master.supervisor_repair_receipts import (
    execution_limit_protocol_reentry_key, inspect_repair_receipt,
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def stopped(tmp_path):
    cycle = tmp_path / 'research/7'
    workspace = cycle / 'workspace'
    (workspace / 'analysis').mkdir(parents=True)
    goal = SimpleNamespace(thread_id='old-thread', objective='immutable objective',
        created_at=100, status='blocked', tokens_used=120, time_used_seconds=20, token_budget=None)
    lock = {'id':'H-7','status':'locked','hypothesis':'claim','scope':'scope','decisive_test':'test'}
    checkpoint = cycle / 'checkpoints/lock.json'
    write_json(checkpoint, {'cycle_id':'7','hypothesis_lock':lock})
    invocation = 'inv-7-investigator-failed'
    state = {'cycle_id':'7','status':'failed','phase':'failed','protocol_health':'valid',
        'scientific_verdict':None,'execution_active':False,'active_invocation_id':None,'resumable':True,
        'last_invocation_id':invocation,'last_invocation_failure':{'invocation_id':invocation,
            'role':'investigator','stage':'investigate','failure_phase':'invoke',
            'error_type':'NativeGoalRuntimeFailure'},
        'native_goal':{'status':'blocked','runtime_failure_kind':'native_goal_dependency_blocked',
            'thread_id':'old-thread','objective_sha256':hashlib.sha256(b'immutable objective').hexdigest()},
        'threads':{'investigator':'old-thread'},'hypothesis_lock':lock,
        'last_safe_checkpoint':str(checkpoint),
        'investigator_budget': {'remaining_tokens': 5000, 'exhausted': False, 'rejected': False},
        'recovery':{'action':'error','kind':'NativeGoalRuntimeFailure','message':'native blocked'}}
    write_json(cycle / 'research_state.json', state)
    ledger = {'invocation_id':invocation,'event':'invocation.failed','state':'failed',
        'commit_state':'not_committed','role':'investigator','stage':'investigate',
        'thread_id':'old-thread','error_type':'NativeGoalRuntimeFailure',
        'native_goal_identity':goal_identity(goal)}
    (cycle / 'invocations.jsonl').write_text(json.dumps(ledger)+'\n')
    for name in ('events.jsonl','runtime_events.jsonl'):(cycle / name).write_text('{}\n')
    original,repaired,result = [workspace / 'analysis' / name for name in ('source.py','repair.py','result.npz')]
    for path in (original,repaired,result):path.write_bytes(b'preserved bytes')
    auth_path = cycle / 'attention/technical-recovery-authorization.json'
    auth = {'schema':recovery.SCHEMA,'mode':'verified_artifact_rescan',
        'authorization_source':'delegated_operator','user_instruction':'Repair and continue',
        'scope':'Read the already repaired artifacts; no new computation',
        'cycle':str(cycle),'failed_invocation_id':invocation,'thread_id':'old-thread',
        'frozen_objective':lock,'source_script':str(original),'source_script_sha256':sha(original),
        'recovery_script':str(repaired),'recovery_script_sha256':sha(repaired),
        'artifact_manifest':{str(result):sha(result)}}
    write_json(auth_path,auth)
    write_json(cycle / 'attention/operator_guidance.json', {'status':'pending',
        'attention_code':'operator_technical_recovery','text':'Read '+str(auth_path)})
    (cycle / 'attention/operator_guidance.md').write_text('Read '+str(auth_path))
    return cycle,state,goal,auth_path,result


def test_verified_artifact_recovery_is_read_only(stopped):
    cycle,state,goal,_,_ = stopped
    before = copy.deepcopy(state)
    proof,inputs = recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)
    assert state == before
    assert proof['scientific_verdict'] is None and proof['new_scientific_cycle'] is False
    assert proof['blocked_goal_identity']['status']=='blocked'
    assert str(cycle/'attention/technical-recovery-authorization.json') in inputs


@pytest.mark.parametrize('change', ['missing_artifact','tampered_artifact','foreign_science',
    'agent_authorization','wrong_failure','missing_guidance','running_goal','exhausted_budget',
    'already_used','live_owner','provider_failure','escaping_path','missing_scope','malformed_authorization',
    'zero_budget','no_checkpoint','unlocked','mismatched_guidance'])
def test_invalid_recovery_never_grants_authority(stopped,change):
    cycle,state,goal,auth_path,result = stopped
    auth=json.loads(auth_path.read_text())
    if change=='malformed_authorization':auth=[]
    elif change=='missing_scope':auth['scope']=''
    elif change=='missing_artifact':auth['artifact_manifest']={}
    elif change=='tampered_artifact':result.write_bytes(b'changed')
    elif change=='foreign_science':auth['frozen_objective']['scope']='other'
    elif change=='agent_authorization':auth['authorization_source']='investigator'
    elif change=='wrong_failure':auth['failed_invocation_id']='inv-other'
    elif change=='missing_guidance':(cycle/'attention/operator_guidance.json').unlink()
    elif change=='running_goal':goal.status='active'
    elif change=='exhausted_budget':state['investigator_budget']['exhausted']=True
    elif change=='zero_budget':state['investigator_budget']['remaining_tokens']=0
    elif change=='no_checkpoint':state['last_safe_checkpoint']=None
    elif change=='unlocked':state['hypothesis_lock']['status']='unlocked'
    elif change=='mismatched_guidance':(cycle / 'attention/operator_guidance.md').write_text('Unbound guidance')
    elif change=='already_used':state['supervisor_execution_limit_protocol_reentries']=[execution_limit_protocol_reentry_key(state)]
    elif change=='live_owner':state['execution_active']=True
    elif change=='provider_failure':state['native_goal']['provider_failure']={'code':'auth'}
    elif change=='escaping_path':
        outside=cycle/'outside';outside.write_bytes(b'x')
        auth['artifact_manifest']={str(cycle/'workspace/../outside'):sha(outside)}
    write_json(auth_path,auth)
    with pytest.raises((ValueError,OSError,RuntimeError)):
        recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


def test_absent_authorization_is_inert(tmp_path):
    assert recovery.validate_operator_technical_recovery(tmp_path,{},engine=tmp_path) is None


def test_receipt_is_bound_to_artifact_and_original_failure(stopped,monkeypatch):
    cycle,state,goal,_,result=stopped
    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal',lambda *_:goal)
    engine=Path(recovery.__file__).parent
    receipt=recovery.validate_operator_technical_recovery(cycle,state,engine=engine)
    assert receipt is not None
    assert inspect_repair_receipt(cycle,state,engine=engine)==receipt
    result.write_bytes(b'changed after verification')
    with pytest.raises(ValueError):inspect_repair_receipt(cycle,state,engine=engine)



def set_bounded_continuation(stopped):
    cycle, _, _, auth_path, _ = stopped
    lock = cycle / 'workspace/analysis/.lock'
    lock.touch()
    auth = json.loads(auth_path.read_text())
    auth.update(mode=recovery.BOUNDED_COMPUTATION, dispatcher_locks=[str(lock)],
                limits={'max_workers': 2, 'threads_per_worker': 1, 'cpu_cores': 2, 'max_unit_seconds': 60})
    write_json(auth_path, auth)
    return lock


def test_bounded_continuation_requires_a_free_dispatcher_and_wakes_on_release(stopped):
    cycle, state, goal, _, _ = stopped
    lock = set_bounded_continuation(stopped)
    with lock.open('r') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        waiting = recovery.dependency_fingerprint(cycle, state)
        with pytest.raises(ValueError, match='compute is still owned'):
            recovery.check_authorization(cycle, state, goal_reader=lambda *_:goal)
    assert recovery.dependency_fingerprint(cycle, state) != waiting
    proof, _ = recovery.check_authorization(cycle, state, goal_reader=lambda *_:goal)
    assert proof['mode'] == recovery.BOUNDED_COMPUTATION
    assert proof['limits']['max_unit_seconds'] == 60


def test_bounded_continuation_requires_declared_worker_threads_within_cpu_cap(stopped):
    _, _, goal, auth_path, _ = stopped
    set_bounded_continuation(stopped)
    auth = json.loads(auth_path.read_text())
    auth['limits']['threads_per_worker'] = 2
    write_json(auth_path, auth)
    cycle, state, *_ = stopped
    with pytest.raises(ValueError, match='execution envelope'):
        recovery.check_authorization(cycle, state, goal_reader=lambda *_:goal)


def test_runtime_supervisor_dispatches_bounded_reentry_on_same_cycle(stopped, monkeypatch):
    from theory_master.resume_preparation import consume_resume_repairs, prepare_resume_recovery
    from theory_master.runtime_supervisor_review import resume_blocker, review_runtime_failure

    cycle, state, goal, _, _ = stopped
    lock = set_bounded_continuation(stopped)
    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal', lambda *_: goal)
    old = copy.deepcopy(state)
    result = review_runtime_failure(
        backend=None, research_root=cycle.parent, cycle_dir=cycle,
        state=state, state_path=cycle / 'research_state.json', incident_id='test', scheduler_locked=True,
    )
    assert result['action'] == 'resume', result
    receipt = inspect_repair_receipt(cycle, state)
    assert receipt['validator'] == recovery.VALIDATOR
    assert not state.get('supervisor_execution_limit_protocol_reentries')
    assert resume_blocker(state, runtime_revalidated=True, contract_projection_repair=receipt) is None
    with lock.open('r') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='compute is still owned'):
            consume_resume_repairs(cycle, state)
    assert recovery.technical_recovery_origins(state) == set()
    consume_resume_repairs(cycle, state)
    prepare_resume_recovery(state, cycle)
    assert state['hypothesis_lock'] == old['hypothesis_lock']
    assert state['investigator_budget'] == old['investigator_budget']
    assert state['scientific_verdict'] is None
    assert state['native_goal'] == old['native_goal']
    assert 'investigator' not in state['threads']
    assert state['operator_guidance_pending']
    assert recovery.technical_recovery_origins(state) == {old['last_invocation_id']}
    assert 'already used' in resume_blocker(state, runtime_revalidated=True)


def test_runtime_supervisor_keeps_waiting_and_wakes_when_operator_authorizes(stopped, monkeypatch):
    from theory_master.supervisor_continuity import review_and_repair_runtime_failure
    from theory_master.supervisor_recovery import due

    cycle, state, goal, auth_path, _ = stopped
    auth = auth_path.read_bytes()
    auth_path.unlink()
    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal', lambda *_: goal)
    result = review_and_repair_runtime_failure(
        backend=None, research_root=cycle.parent, cycle_dir=cycle,
        state=state, state_path=cycle / 'research_state.json', incident_id='test', scheduler_locked=True,
    )
    assert result['action'] == 'hold'
    assert result['recovery_job']['dependency_wait'] is True
    assert not due(cycle, state)
    auth_path.write_bytes(auth)
    assert due(cycle, state)


def add_old_generations(stopped):
    cycle, _, goal, _, _ = stopped
    ledger = cycle / 'invocations.jsonl'
    failed = json.loads(ledger.read_text())
    failed['logical_key'] = 'same-locked-investigator-slot'
    earlier = {**failed, 'state': 'interrupted', 'error_type': None}
    previous = [
        {**earlier, 'invocation_id': f'inv-old-{stamp}',
         'native_goal_identity': {**goal_identity(goal), 'created_at': stamp}}
        for stamp in (60, 80)
    ]
    different_slot = {**previous[-1], 'invocation_id': 'inv-unrelated', 'logical_key': 'other-slot'}
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in [*previous, different_slot, failed]))
    return failed['logical_key']


def test_bounded_recovery_retires_only_proven_older_generations(stopped):
    add_old_generations(stopped)
    cycle, state, goal, _, _ = stopped
    proof, _ = recovery.check_authorization(cycle, state, goal_reader=lambda *_: goal)
    assert set(proof['origin_exclusion_ids']) == {state['last_invocation_id'], 'inv-old-60', 'inv-old-80'}


@pytest.mark.parametrize('variant', ['valid', 'modified_history', 'replacement_has_goal', 'exact_missing_thread'])
def test_consumed_continuation_resumes_after_exact_setup_conflict(stopped, monkeypatch, variant):
    from unittest.mock import patch
    from theory_master.invocations import InvocationLedger
    from theory_master.native_goal_recovery import find_native_goal_origin
    from theory_master.resume_preparation import consume_resume_repairs, prepare_resume_recovery

    logical_key = add_old_generations(stopped)
    cycle, state, goal, _, _ = stopped
    set_bounded_continuation(stopped)
    origin = state['last_invocation_id']
    replacement = 'replacement-setup-only'
    sdk_errors = pytest.importorskip('openai_codex.errors') if variant == 'exact_missing_thread' else None

    def goal_reader(_cycle, request):
        if request['thread_id'] == goal.thread_id:
            return goal
        assert request['thread_id'] == replacement
        if variant == 'replacement_has_goal':
            return object()
        if variant == 'exact_missing_thread':
            raise ValueError('original native Goal GET is unavailable') from sdk_errors.InvalidRequestError(
                -32600, f'thread not found: {replacement}',
            )
        return None

    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal', goal_reader)
    engine = Path(recovery.__file__).parent
    with patch.object(recovery, '_retired_origins', return_value=[origin]):
        original = recovery.validate_operator_technical_recovery(cycle, state, engine=engine)
    assert original is not None
    consume_resume_repairs(cycle, state)
    original_audit = copy.deepcopy(state['supervisor_technical_reentry_audit'][0])
    invocation = 'inv-replacement-failed-setup'
    failure = {'invocation_id': invocation, 'role': 'investigator', 'stage': 'investigate',
               'failure_phase': 'setup', 'error_type': 'NativeGoalRecoveryConflict'}
    state['threads']['investigator'] = replacement
    state.update(last_invocation_id=invocation, last_invocation_failure=failure,
                 recovery={'action': 'error', 'kind': 'NativeGoalRecoveryConflict',
                           'message': 'unfinished native Goal changed thread or contract'})
    state['investigator_budget']['remaining_tokens'] -= 1
    write_json(cycle / 'research_state.json', state)
    with (cycle / 'invocations.jsonl').open('a') as stream:
        stream.write(json.dumps({**failure, 'thread_id': replacement, 'logical_key': logical_key,
                                 'state': 'failed', 'commit_state': 'not_committed'}) + '\n')
    with (cycle / 'runtime_events.jsonl').open('a') as stream:
        stream.write('{"event":"RUN_FAILED"}\n')
    if variant == 'modified_history':
        (cycle / 'events.jsonl').write_text('{"event":"changed"}\n')
    retried = recovery.validate_operator_technical_recovery(cycle, state, engine=engine)
    if variant in {'replacement_has_goal', 'modified_history'}:
        assert retried is None
        assert state['supervisor_technical_reentry_audit'] == [original_audit]
        return

    assert retried is not None and retried['repair_identity'] != original['repair_identity']
    assert recovery.consume_technical_recovery(cycle, state)
    assert state['supervisor_technical_reentry_audit'][0] == original_audit
    assert state['investigator_budget']['remaining_tokens'] == 4999
    assert 'investigator' not in state['threads']
    assert state['scientific_verdict'] is None
    assert recovery.technical_recovery_origins(state) == {origin, 'inv-old-60', 'inv-old-80'}
    prepare_resume_recovery(state, cycle)
    assert find_native_goal_origin(SimpleNamespace(
        owner=SimpleNamespace(cycle_dir=cycle), state=state,
        invocation_ledger=InvocationLedger(cycle / 'invocations.jsonl'),
        identifier='next-investigator-thread', invocation_id='inv-next',
        logical_key=logical_key, stage='investigate',
    )) is None
