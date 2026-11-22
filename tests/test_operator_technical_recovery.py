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


@pytest.mark.parametrize('variant', ['valid', 'modified_history', 'replacement_has_goal', 'exact_missing_thread', 'changed_stage'])
@pytest.mark.parametrize('stage', ['investigate', 'judge_rework'])
def test_consumed_continuation_resumes_after_exact_setup_conflict(stopped, monkeypatch, variant, stage):
    from unittest.mock import patch
    from theory_master.invocations import InvocationLedger
    from theory_master.native_goal_recovery import find_native_goal_origin
    from theory_master.resume_preparation import consume_resume_repairs, prepare_resume_recovery

    if stage == 'judge_rework':
        set_judge_rework(stopped)
    logical_key = add_old_generations(stopped)
    cycle, state, goal, _, _ = stopped
    if stage == 'investigate':
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
    failure = {'invocation_id': invocation, 'role': 'investigator', 'stage': stage,
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
    elif variant == 'changed_stage':
        state['last_invocation_failure']['stage'] = 'judge_rework' if stage == 'investigate' else 'investigate'
        write_json(cycle / 'research_state.json', state)
    retried = recovery.validate_operator_technical_recovery(cycle, state, engine=engine)
    if variant in {'replacement_has_goal', 'modified_history', 'changed_stage'}:
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
        logical_key=logical_key, stage=stage,
    )) is None


def set_judge_rework(stopped):
    cycle, state, _, auth_path, _ = stopped
    judge = {'assessment_state': 'rework_requested', 'verdict': None,
             'objections': [{'objection_id': 'J-1', 'requested_resolution': 'Resolve the frozen comparison'}]}
    state['last_invocation_failure']['stage'] = 'judge_rework'
    state.update(next_executor='investigator', judge_rework_count=1, investigation_attempt=2,
                 role_outputs={'judge': copy.deepcopy(judge)}, evidence_refs=['E-1'],
                 rework_authorization={'authorized': True, 'target_executor': 'investigator',
                    'attempt': 1, 'judge_request': copy.deepcopy(judge)},
                 investigation_attempts={'attempt-0002': {'outputs': [],
                    'judge_feedback': copy.deepcopy(judge), 'objections': copy.deepcopy(judge['objections'])}})
    ledger = cycle / 'invocations.jsonl'
    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    for row in entries:
        row['stage'] = 'judge_rework'
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in entries))
    write_json(cycle / 'research_state.json', state)
    registry = cycle / 'evidence/registry.jsonl'
    registry.parent.mkdir(exist_ok=True)
    registry.write_text(json.dumps({'evidence_id': 'E-1', 'statement': 'original result'}) + '\n')
    auth = json.loads(auth_path.read_text())
    auth.update(stage='judge_rework', judge_rework_binding=recovery._judge_rework_binding(cycle, state, {}))
    write_json(auth_path, auth)


def test_judge_rework_rescan_keeps_original_judge_evidence_and_budget(stopped, monkeypatch):
    from theory_master.resume_preparation import consume_resume_repairs, prepare_resume_recovery
    from theory_master.runtime_supervisor_review import review_runtime_failure

    set_judge_rework(stopped)
    cycle, state, goal, _, _ = stopped
    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal', lambda *_: goal)
    before = copy.deepcopy(state)
    result = review_runtime_failure(backend=None, research_root=cycle.parent, cycle_dir=cycle,
        state=state, state_path=cycle / 'research_state.json', incident_id='test', scheduler_locked=True)
    assert result['action'] == 'resume', result
    receipt = inspect_repair_receipt(cycle, state)
    proof = json.loads(receipt['tests'])
    assert proof['stage'] == 'judge_rework'
    assert proof['mode'] == recovery.ARTIFACT_RESCAN
    assert proof['limits'] is None
    consume_resume_repairs(cycle, state)
    prepare_resume_recovery(state, cycle)
    for key in ('cycle_id', 'hypothesis_lock', 'role_outputs', 'evidence_refs', 'investigation_attempts',
                'rework_authorization', 'judge_rework_count', 'investigator_budget',
                'scientific_verdict', 'native_goal', 'last_invocation_failure'):
        assert state[key] == before[key]
    assert 'investigator' not in state['threads']
    assert state['operator_guidance_pending']
    with pytest.raises(ValueError):
        recovery.consume_technical_recovery(cycle, state)


def test_blocked_handoff_is_bound_material_not_completed_delivery(stopped):
    set_judge_rework(stopped)
    cycle, state, goal, auth_path, _ = stopped
    handoff = cycle / 'workspace/.tm-handoff' / (state['last_invocation_id'] + '.json')
    handoff.parent.mkdir()
    handoff.write_text('{"findings":[{"statement":"Executed gate failed"}]}')
    auth = json.loads(auth_path.read_text())
    auth['artifact_manifest'][str(handoff)] = sha(handoff)
    auth['prior_handoff_sha256'] = sha(handoff)
    write_json(auth_path, auth)

    before = copy.deepcopy(state)
    proof, inputs = recovery.check_authorization(cycle, state, goal_reader=lambda *_: goal)
    assert state == before
    assert proof['prior_handoff_sha256'] == sha(handoff)
    assert inputs[str(handoff)] == sha(handoff)
    assert proof['blocked_goal_identity']['status'] == 'blocked'
    assert proof['scientific_verdict'] is None


@pytest.mark.parametrize('change', ['missing_authorization', 'missing_manifest',
    'changed_handoff', 'missing_handoff', 'compute_mode'])
def test_blocked_handoff_requires_exact_rescan_binding(stopped, change):
    set_judge_rework(stopped)
    cycle, state, goal, auth_path, _ = stopped
    handoff = cycle / 'workspace/.tm-handoff' / (state['last_invocation_id'] + '.json')
    handoff.parent.mkdir()
    handoff.write_text('{"findings":[{"statement":"Executed gate failed"}]}')
    auth = json.loads(auth_path.read_text())
    auth['artifact_manifest'][str(handoff)] = sha(handoff)
    auth['prior_handoff_sha256'] = sha(handoff)
    if change == 'missing_authorization': auth.pop('prior_handoff_sha256')
    elif change == 'missing_manifest': auth['artifact_manifest'].pop(str(handoff))
    elif change == 'changed_handoff': handoff.write_text('{"findings":[]}')
    elif change == 'missing_handoff': handoff.unlink()
    elif change == 'compute_mode': auth['mode'] = recovery.BOUNDED_COMPUTATION
    write_json(auth_path, auth)
    with pytest.raises((ValueError, OSError)):
        recovery.check_authorization(cycle, state, goal_reader=lambda *_: goal)


@pytest.mark.parametrize('change', ['bounded_computation', 'wrong_stage', 'missing_binding',
    'changed_judge', 'changed_objection', 'changed_rework', 'changed_evidence',
    'new_lifecycle', 'submitted_attempt', 'changed_frozen', 'changed_refs', 'unpersisted_rework'])
def test_judge_rework_rescan_rejects_changed_science_and_compute(stopped, change):
    set_judge_rework(stopped)
    cycle, state, goal, auth_path, _ = stopped
    auth = json.loads(auth_path.read_text())
    if change == 'bounded_computation':
        set_bounded_continuation(stopped)
        auth = json.loads(auth_path.read_text())
    elif change == 'wrong_stage': auth['stage'] = 'investigate'
    elif change == 'missing_binding': auth.pop('judge_rework_binding')
    elif change == 'changed_judge': state['role_outputs']['judge']['verdict'] = 'open'
    elif change == 'changed_objection':
        state['investigation_attempts']['attempt-0002']['objections'][0]['requested_resolution'] = 'other'
    elif change == 'changed_rework': state['rework_authorization']['attempt'] += 1
    elif change == 'changed_evidence': (cycle / 'evidence/registry.jsonl').write_text('{"evidence_id":"E-1","statement":"changed"}\n')
    elif change == 'new_lifecycle': (cycle / 'evidence/lifecycle.jsonl').write_text('{}\n')
    elif change == 'submitted_attempt': state['investigation_attempts']['attempt-0002']['outputs'] = [{}]
    elif change == 'changed_frozen': state['hypothesis_lock']['scope'] = 'new scope'
    elif change == 'changed_refs': state['evidence_refs'] = ['E-other']
    elif change == 'unpersisted_rework':
        state['investigation_attempts']['attempt-0002']['objections'][0]['requested_resolution'] = 'new authorized request'
        auth['judge_rework_binding'] = recovery._judge_rework_binding(cycle, state, {})
    write_json(auth_path, auth)
    with pytest.raises((ValueError, OSError)):
        recovery.check_authorization(cycle, state, goal_reader=lambda *_: goal)


@pytest.mark.parametrize('change', ['state', 'evidence', 'absence'])
def test_judge_rework_receipt_rechecks_live_binding_before_consumption(stopped, monkeypatch, change):
    set_judge_rework(stopped)
    cycle, state, goal, _, _ = stopped
    monkeypatch.setattr('theory_master.supervisor_native_goal_recovery.read_existing_goal', lambda *_: goal)
    receipt = recovery.validate_operator_technical_recovery(cycle, state, engine=Path(recovery.__file__).parent)
    assert receipt is not None
    if change == 'state': state['investigation_attempts']['attempt-0002']['objections'][0]['reason'] = 'changed'
    elif change == 'evidence': (cycle / 'evidence/registry.jsonl').write_text('{"evidence_id":"E-1","statement":"changed"}\n')
    else: (cycle / 'evidence/lifecycle.jsonl').write_text('{}\n')
    with pytest.raises(ValueError): recovery.consume_technical_recovery(cycle, state)
