"""Completed Goal redelivery requires SDK evidence and an explicit operator grant."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import operator_completed_delivery as recovery
from theory_master.native_goal_recovery import goal_identity, find_native_goal_origin, NativeGoalRecoveryConflict
from theory_master.runtime import write_json
from theory_master.supervisor_repair_receipts import inspect_repair_receipt


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    cycle = tmp_path / 'research/7'
    workspace = cycle / 'workspace'
    (workspace / 'analysis').mkdir(parents=True)
    manuscript = workspace / 'analysis/proof.md'
    manuscript.write_text('Investigator-authored argument and its stated uncertainty.\n')
    goal = SimpleNamespace(thread_id='old-thread', objective='immutable original objective',
        created_at=100, status='complete', tokens_used=120, time_used_seconds=20, token_budget=None)
    lock = {'id':'H-7', 'status':'locked', 'hypothesis':'claim', 'scope':'scope', 'decisive_test':'test'}
    checkpoint = cycle / 'checkpoints/lock.json'
    write_json(checkpoint, {'cycle_id':'7', 'hypothesis_lock':lock})
    invocation = 'inv-7-investigator-original'
    manifest = cycle / 'invocation_artifacts' / (invocation + '.json')
    state = {'cycle_id':'7', 'status':'needs_attention', 'phase':recovery.PHASE,
        'protocol_health':'valid', 'scientific_verdict':None, 'execution_active':False,
        'active_invocation_id':None, 'resumable':True, 'last_invocation_id':invocation,
        'last_invocation_failure':{'invocation_id':invocation, 'role':'investigator',
            'stage':'investigate', 'failure_phase':'invoke', 'error_type':'RecoveryStop'},
        'native_goal':{'status':'complete', 'thread_id':'old-thread'},
        'threads':{'investigator':'old-thread'}, 'hypothesis_lock':lock,
        'last_safe_checkpoint':str(checkpoint),
        'attention':{'code':'investigator_handoff_invalid', 'reason':recovery.REASON,
                     'thread_id':'old-thread'}, 'recovery':{},
        'investigator_budget':{'allowance_tokens':1000, 'observed_tokens':600,
                               'remaining_tokens':400, 'exhausted':False, 'rejected':False}}
    write_json(cycle / 'research_state.json',state)
    ledger = {'invocation_id':invocation, 'event':'invocation.interrupted', 'state':'interrupted',
        'commit_state':'not_committed', 'role':'investigator', 'stage':'investigate',
        'thread_id':'old-thread', 'failure_phase':'invoke',
        'reason':'SDK handoff invalid: '+recovery.REASON,
        'invocation_artifact_manifest':str(manifest), 'native_goal_identity':goal_identity(goal),
        'logical_key':'same-slot', 'contract_ref':{}}
    (cycle / 'invocations.jsonl').write_text(json.dumps(ledger)+'\n')
    write_json(manifest, {'invocation_id':invocation, 'status':'interrupted',
        'role':'investigator', 'stage':'investigate', 'workspace':str(workspace),
        'created':[], 'modified':[{'path':str(manuscript),'sha256':sha(manuscript)}]})
    handoff = workspace / '.tm-handoff' / (invocation+'.json')
    (cycle / 'events.jsonl').write_text('{}\n')
    (cycle / 'runtime_events.jsonl').write_text(json.dumps({'event':'AGENT_INPUT',
        'role':'investigator','input_kind':'native_goal_setup_turn','content':'Write '+str(handoff)})+'\n')
    path = cycle / 'attention/completed-delivery-authorization.json'
    auth = {'schema':recovery.SCHEMA,'authorization_source':'delegated_operator',
        'user_instruction':'Repair and continue the monitored task',
        'scope':'Investigator must inspect its own manuscript and author its own new handoff',
        'cycle':str(cycle),'invocation_id':invocation,'thread_id':'old-thread',
        'frozen_objective':lock,'artifact_manifest':{str(manuscript):sha(manuscript)}}
    write_json(path,auth)
    write_json(cycle / 'attention/operator_guidance.json', {'status':'pending',
        'attention_code':'operator_completed_delivery','text':'Read '+str(path)})
    (cycle / 'attention/operator_guidance.md').write_text('Read '+str(path))
    from theory_master import supervisor_native_goal_recovery
    monkeypatch.setattr(supervisor_native_goal_recovery,'read_existing_goal',lambda *_:goal)
    return cycle,state,goal,path,manuscript


def validate(stopped):
    cycle,state,*_ = stopped
    return recovery.validate_completed_delivery(cycle,state,engine=Path(recovery.__file__).resolve().parent)


def test_verification_reads_only_original_evidence_and_goal(stopped):
    cycle,state,goal,_,manuscript = stopped
    original=copy.deepcopy(state); data=manuscript.read_bytes(); reads=[]
    proof,inputs=recovery.check_authorization(cycle,state,goal_reader=lambda *_:(reads.append('GET'),goal)[1])
    assert reads==['GET'] and state==original and manuscript.read_bytes()==data
    assert proof['completed_goal_identity']['status']=='complete'
    assert proof['scientific_verdict'] is None
    assert str(manuscript) in inputs
    assert not (cycle/'workspace/.tm-handoff'/f'{state["last_invocation_id"]}.json').exists()


def test_receipt_consumption_preserves_science_budget_and_terminal_goal(stopped):
    cycle,state,goal,_,manuscript=stopped
    original=copy.deepcopy(state); data=manuscript.read_bytes()
    receipt=validate(stopped)
    assert inspect_repair_receipt(cycle,state)==receipt
    assert recovery.completed_delivery_origins(state,'old-thread')==set()
    assert recovery.consume_completed_delivery(cycle,state)
    assert state['native_goal']==original['native_goal']
    assert state['last_invocation_failure']==original['last_invocation_failure']
    assert state['investigator_budget']==original['investigator_budget']
    assert state['hypothesis_lock']==original['hypothesis_lock']
    assert manuscript.read_bytes()==data and state['scientific_verdict'] is None
    assert recovery.completed_delivery_origins(state,'old-thread')=={original['last_invocation_id']}
    assert recovery.completed_delivery_origins(state,'other-thread')==set()
    session=SimpleNamespace(state={**state,'recovery':{'action':'resume'}},
        owner=SimpleNamespace(cycle_dir=cycle),identifier='old-thread',invocation_id='inv-new',
        stage='investigate',logical_key='same-slot', contract=SimpleNamespace(descriptor=lambda:{}),
        invocation_ledger=SimpleNamespace(snapshots=lambda:[json.loads((cycle/'invocations.jsonl').read_text())]))
    assert find_native_goal_origin(session) is None
    new_record={**json.loads((cycle/'invocations.jsonl').read_text()),
                'invocation_id':'inv-new-interrupted', 'state':'interrupted'}
    session.invocation_ledger=SimpleNamespace(snapshots=lambda:[new_record])
    with pytest.raises(NativeGoalRecoveryConflict, match='unique thread-bound start'):
        find_native_goal_origin(session)
    state['attention']=original['attention']
    with pytest.raises(ValueError,match='already consumed'):recovery.consume_completed_delivery(cycle,state)


@pytest.mark.parametrize('patch',[
    {'execution_active':True},{'active_invocation_id':'live'},{'resumable':False},
    {'protocol_health':'violation'},{'scientific_verdict':'supported'},
    {'investigator_budget':{'exhausted':True}},{'investigator_budget':{'rejected':True}},
    {'recovery':{'action':'pause'}},{'recovery':{'action':'interrupt'}},
    {'native_goal':{'status':'active','thread_id':'old-thread'}},
    {'worker_id':'claimed-worker'},
])
def test_operational_guards_remain_in_force(stopped,patch):
    cycle,state,goal,*_=stopped;state.update(patch)
    with pytest.raises(ValueError):recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


@pytest.mark.parametrize('field,value',[
    ('authorization_source','agent'),('scope',''),('thread_id','different'),
    ('frozen_objective',{'hypothesis':'other','scope':'other','decisive_test':'other'}),
])
def test_operator_grant_is_bound_to_original_failure(stopped,field,value):
    cycle,state,goal,path,_=stopped;a=json.loads(path.read_text());a[field]=value;write_json(path,a)
    with pytest.raises(ValueError):recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


def test_altered_manuscript_does_not_certify_a_new_finding(stopped):
    cycle,state,goal,_,manuscript=stopped;manuscript.write_text('Altered claim')
    with pytest.raises(ValueError,match='SDK capture'):recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


def test_completed_identity_cannot_be_replaced(stopped):
    cycle,state,goal,*_=stopped;goal.created_at+=1
    with pytest.raises(NativeGoalRecoveryConflict):recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


def test_pending_control_after_validation_blocks_consumption(stopped):
    cycle,state,*_=stopped;validate(stopped);write_json(cycle/'recovery_control.json',{'action':'pause'})
    with pytest.raises(ValueError,match='control'):recovery.consume_completed_delivery(cycle,state)


def test_changed_evidence_invalidates_receipt(stopped):
    cycle,state,_,_,manuscript=stopped;validate(stopped);manuscript.write_text('Changed after validation')
    with pytest.raises(ValueError):inspect_repair_receipt(cycle,state)


def test_uncertified_audit_cannot_exclude_an_old_goal(stopped):
    cycle,state,*_=stopped;validate(stopped);recovery.consume_completed_delivery(cycle,state)
    state['supervisor_consumed_repairs']=[]
    assert recovery.completed_delivery_origins(state,'old-thread')==set()


def test_budget_generations_require_recorded_sdk_approval(stopped):
    cycle,state,goal,*_=stopped
    path=cycle/'invocations.jsonl';original=json.loads(path.read_text())
    previous={**original,'invocation_id':'inv-7-budget-interrupted',
              'reason':'live Native Goal token guard exceeded',
              'recorded_at':'1970-01-01T00:00:10+00:00',
              'native_goal_identity':{**original['native_goal_identity'],'created_at':1}}
    previous['event']='invocation.interrupted'
    shutdown={'invocation_id':previous['invocation_id'],'event':'invocation.interrupted',
              'state':'interrupted','recorded_at':'1970-01-01T00:20:00+00:00'}
    path.write_text(json.dumps(previous)+'\n'+json.dumps(original)+'\n'+json.dumps(shutdown)+'\n')
    with pytest.raises(ValueError,match='budget approval'):
        recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)
    (cycle/'events.jsonl').write_text(json.dumps({'at':'1970-01-01T00:00:20+00:00',
        'type':'investigation.budget_review_decided','payload':{'decision':'approve'}})+'\n')
    proof,_=recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)
    assert set(proof['origin_exclusion_ids'])=={previous['invocation_id'],original['invocation_id']}
    validate(stopped);recovery.consume_completed_delivery(cycle,state)
    assert recovery.completed_delivery_origins(state,'old-thread')==set(proof['origin_exclusion_ids'])


def preparation_failure(stopped):
    cycle,state,*_=stopped
    validate(stopped);recovery.consume_completed_delivery(cycle,state)
    invocation='inv-7-preparation-failed'
    state.update(status='failed',phase='failed',last_invocation_id=invocation,
        last_invocation_failure={'invocation_id':invocation,'role':'investigator','stage':'investigate',
            'failure_phase':'invoke','error_type':'NativeGoalRecoveryConflict'},
        recovery={'action':'error','kind':'NativeGoalRecoveryConflict',
            'message':'the original native Goal no longer exists'})
    with (cycle/'invocations.jsonl').open('a') as f:f.write(json.dumps({
        'invocation_id':invocation,'state':'failed','commit_state':'not_committed',
        'role':'investigator','stage':'investigate','thread_id':'old-thread',
        'error_type':'NativeGoalRecoveryConflict'})+'\n')
    with (cycle/'events.jsonl').open('a') as f:f.write(json.dumps({'at':'1970-01-01T00:03:20+00:00',
        'type':'role.started','payload':{'invocation_id':invocation}})+'\n')
    write_json(cycle/'research_state.json',state)
    return cycle,state


def test_unstarted_preparation_failure_keeps_the_original_one_goal_authority(stopped):
    cycle,state=preparation_failure(stopped)
    goal=stopped[2]
    proof,_=recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)
    assert proof['origin_invocation_id']=='inv-7-investigator-original'
    assert proof['preparation_retry_from']=='inv-7-preparation-failed'
    assert proof['scientific_verdict'] is None
    validate(stopped);recovery.consume_completed_delivery(cycle,state)
    assert len(state['supervisor_completed_delivery_audit'])==2
    assert state['investigator_budget']['remaining_tokens']==400


@pytest.mark.parametrize('event',['LLM_TURN_STARTED','LLM_MESSAGE','TOOL_ACTIVITY'])
def test_preparation_retry_cannot_hide_an_executed_model_or_tool(stopped,event):
    cycle,state=preparation_failure(stopped)
    with (cycle/'runtime_events.jsonl').open('a') as f:f.write(json.dumps({
        'ts':'1970-01-01T00:03:21+00:00','role':'investigator','event':event})+'\n')
    with pytest.raises(ValueError,match='executed model turn'):
        recovery.check_authorization(cycle,state,goal_reader=lambda *_:stopped[2])
