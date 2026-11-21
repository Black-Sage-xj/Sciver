"""Lossless missing-array syntax repair and evidence-bound untouched reentry."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master import supervisor_json_array_recovery as recovery
from theory_master.output_normalization import _close_array_before_root_field, normalize_agent_output
from theory_master.contracts import role_contract
from theory_master.runtime import write_json
from theory_master.supervisor_repair_receipts import inspect_repair_receipt, failure_binding
from theory_master.runtime_supervisor_review import resume_blocker
from theory_master.workflow_support import prequalification_restart_allowed


@pytest.mark.parametrize('items',[[{'claim':'unchanged'}],[{'claim':'one'},{'claim':'two'}]])
def test_exactly_one_closer_is_added_without_changing_any_scalar(items):
    expected={'items':items,'ready':False};valid=json.dumps(expected)
    boundary=valid.index('], "ready"');raw=valid[:boundary]+valid[boundary+1:]
    repaired,edit=_close_array_before_root_field(raw,{'items':[],'ready':'boolean'})
    assert repaired==raw[:edit['offset']]+']'+raw[edit['offset']:]
    result=normalize_agent_output(raw,{'items':[],'ready':'boolean'})
    assert result.value==expected and list(result.repairs)==[edit]


@pytest.mark.parametrize('raw,schema',[
    ('{"items":[{"claim":"same"},"unknown":false}',{'items':[]}),
    ('{"items":[{"claim":"same"},"ready":false}',None),
    ('{"ready":true,"items":[{"claim":"same"},"ready":false}',{'items':[],'ready':'boolean'}),
    ('{"items":[[{"claim":"same"},"ready":false]}',{'items':[],'ready':'boolean'}),
    ('{"items":[{"claim":"unterminated},"ready":false}',{'items':[],'ready':'boolean'}),
])
def test_ambiguous_unknown_or_content_loss_inputs_are_not_rewritten(raw,schema):
    assert _close_array_before_root_field(raw,schema)==(raw,None)


def test_valid_json_with_bracket_text_is_untouched():
    raw=json.dumps({'items':[{'claim':'Literal }, "ready":false] remains text'}],'ready':False})
    result=normalize_agent_output(raw,{'items':[],'ready':'boolean'})
    assert not result.repairs and result.value==json.loads(raw)


@pytest.fixture
def stopped(tmp_path,monkeypatch):
    from tests.test_core import TheoryMasterTest
    from theory_master.workflow import Workflow
    from theory_master.backend_fakes import FakeBackend
    spec,_=TheoryMasterTest().make(tmp_path,reworks=0)
    workflow=Workflow(spec,FakeBackend(reworks=0));cycle=spec.research_root/'1'
    workflow._bind_cycle(cycle,create=True);state=workflow._new_state()
    invocation,thread='inv-1-research_lead-json','original-lead'
    candidate={'hypothesis':'A bounded claim','scope':'Fixed scope','why_now':'An unresolved distinction',
               'goal_advance':'Resolve the distinction','decisive_test':'A bounded discriminating test',
               'transition':{'prior_gap':'The distinction is unresolved.','current_change':'Test it.'}}
    complete=json.dumps({'candidates':[candidate],'needs_literature':False})
    point=complete.index('], "needs_literature"');raw=complete[:point]+complete[point+1:]
    artifact=cycle/'contract_repairs'/(invocation+'.json')
    fault={'action':'contract_repair_failed','invocation_id':invocation,'role':'research_lead',
           'stage':'frontier_proposal','thread_id':thread,'artifact':str(artifact),
           'repair_prompt':'Correct only syntax','at':'original-failure'}
    state.update(status='needs_attention',phase=recovery.PHASE,protocol_health='violation',
                 execution_active=False,resumable=True,recovery=fault,
                 attention={'code':'contract_repair_failed'},threads={'research_lead':thread},
                 last_invocation_id=invocation,active_invocation_id=None,role_outputs={},
                 hypothesis_lock={'status':'unlocked'},hypothesis_locked=False,qualification_status='pending')
    write_json(artifact,{**fault,'raw_outputs':[raw,raw],
        'validation_errors':[{'path':'/','code':'json_syntax_error','actual':'syntax_damage'}]})
    (cycle/'invocations.jsonl').write_text(json.dumps({'invocation_id':invocation,'state':'failed',
        'commit_state':'not_committed','role':'research_lead','stage':'frontier_proposal',
        'thread_id':thread,'error_type':'ContractRepairFailed'})+'\n')
    (cycle/'runtime_events.jsonl').write_text(''.join(json.dumps({'event':'LLM_MESSAGE',
        'message_role':'assistant','thread_id':thread,'content':raw})+'\n' for _ in range(2)))
    write_json(cycle/'research_state.json',state)
    monkeypatch.setattr(recovery,'_original_source',lambda *_:'0'*64)
    return spec,cycle,state


def validate(stopped):
    _,cycle,state=stopped
    return recovery.validate_json_array_reentry(cycle,state,engine=Path(recovery.__file__).resolve().parent)


def test_untouched_failure_has_a_fingerprint_but_no_plain_resume_authority(stopped):
    _,cycle,state=stopped
    assert failure_binding(cycle,state)
    assert 'verified repair' in resume_blocker(state)
    assert not prequalification_restart_allowed(state)
    before=copy.deepcopy(state);receipt=validate(stopped)
    assert receipt['validator']==recovery.VALIDATOR
    assert state['recovery']==before['recovery'] and state['scientific_verdict'] is None
    assert prequalification_restart_allowed(state)
    assert resume_blocker(state,contract_projection_repair=receipt) is None
    assert recovery.consume_json_array_reentry(cycle,state)
    assert state['protocol_health']=='valid'
    from theory_master.supervisor_frontier_recovery import consume_frontier_reentry
    assert consume_frontier_reentry(cycle,state) is False
    assert recovery.consume_json_array_reentry(cycle,state) is False
    assert state['hypothesis_lock']=={'status':'unlocked'} and state['scientific_verdict'] is None
    assert state['supervisor_json_array_audit'][0]['original_failure']==before['recovery']


@pytest.mark.parametrize('patch',[
    {'execution_active':True},{'resumable':False},{'hypothesis_locked':True},
    {'scientific_verdict':'supported'},{'active_invocation_id':'live'},
    {'investigator_budget':{'exhausted':True}}, {'recovery':{'action':'pause'}},
    {'native_goal':{'status':'blocked'}},
])
def test_ineligible_states_cannot_receive_a_json_reentry(stopped,patch):
    _,_,state=stopped;state.update(patch);assert validate(stopped) is None


def test_changed_raw_evidence_invalidates_receipt(stopped):
    _,cycle,state=stopped;validate(stopped)
    with (cycle/'runtime_events.jsonl').open('a') as stream:stream.write('{}\n')
    with pytest.raises(ValueError):inspect_repair_receipt(cycle,state)


def test_operator_control_after_validation_remains_binding(stopped):
    _,cycle,state=stopped;validate(stopped);write_json(cycle/'recovery_control.json',{'action':'pause'})
    with pytest.raises(ValueError,match='control'):recovery.consume_json_array_reentry(cycle,state)


def test_normal_workflow_resumes_the_same_uncommitted_cycle(stopped):
    from theory_master.workflow import Workflow
    from theory_master.backend_fakes import FakeBackend
    spec,cycle,state=stopped;validate(stopped);write_json(cycle/'research_state.json',state)
    workflow=Workflow(spec,FakeBackend(reworks=0));workflow.set_max_cycles(1)
    result=workflow.resume(cycle.name)
    assert result['protocol_health']=='valid'
    assert result['cycle_id']==cycle.name
    assert len(result['supervisor_json_array_reentries'])==1
    assert result.get('scientific_verdict') in {'supported','rejected','open','milestone_advanced'}


@pytest.mark.parametrize('captured',[False,True])
def test_original_dependency_uses_recorded_hash_or_pinned_importer(tmp_path,monkeypatch,captured):
    import hashlib
    root=tmp_path/'old-runtime';package=root/'theory_master';package.mkdir(parents=True)
    old=b'def normalize_agent_output(raw,schema):\n    raise ValueError("old parser")\n'
    loader=b'from .output_normalization import normalize_agent_output\n'
    (package/'output_normalization.py').write_bytes(old);(package/'invocation_output.py').write_bytes(loader)
    sha=lambda value:hashlib.sha256(value).hexdigest()
    hashes={'invocation_output.py':sha(loader)}
    if captured:hashes['output_normalization.py']=sha(old)
    state={'runtime_source_provenance':{'source_coherent':True,'sdk_git_sha':'1'*40,
        'source_root':str(root),'sdk_git_root':str(root),'module_sha256':hashes,
        'module_paths':{'invocation_output.py':str(package/'invocation_output.py')}}}
    def command(args,**kwargs):
        if 'rev-parse' in args:return SimpleNamespace(stdout='1'*40+'\n')
        return SimpleNamespace(stdout=loader if args[-1].endswith('invocation_output.py') else old)
    monkeypatch.setattr(recovery.subprocess,'run',command)
    assert recovery._original_source(Path(recovery.__file__).parent,state)==sha(old)
    (package/'output_normalization.py').write_bytes(b'changed dependency')
    with pytest.raises(ValueError,match='pinned normalizer changed'):
        recovery._original_source(Path(recovery.__file__).parent,state)
