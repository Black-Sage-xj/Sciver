"""Official archived completion is evidence only after verified live GET absence."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from theory_master import completed_goal_archive as archive
from theory_master import operator_completed_delivery as recovery
from theory_master import codex_goal_transport as transport
from theory_master.native_goal import NativeGoalResult
from tests.test_operator_completed_delivery import stopped


@pytest.fixture
def archived(stopped, tmp_path, monkeypatch):
    cycle,state,goal,auth_path,_=stopped
    runtime=tmp_path/'runtime';path=runtime/'sessions/record.jsonl';path.parent.mkdir(parents=True)
    camel={'threadId':goal.thread_id,'objective':goal.objective,'createdAt':goal.created_at,
           'status':'complete','tokensUsed':goal.tokens_used,'timeUsedSeconds':goal.time_used_seconds}
    rows=[{'type':'session_meta','payload':{'id':goal.thread_id}},
          {'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','call_id':'complete-call',
           'input':'const r = await tools.update_goal({status:"complete"});\\ntext(r);\\n'}},
          {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'complete-call',
           'output':[{'type':'input_text','text':json.dumps({'goal':camel})}]}}]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    auth=json.loads(auth_path.read_text());auth['archived_goal_completion']={
        'rollout_path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'call_id':'complete-call'}
    auth_path.write_text(json.dumps(auth))
    from theory_master import codex_accounts
    monkeypatch.setattr(codex_accounts,'runtime_home',lambda:runtime)
    monkeypatch.setattr(archive,'_original_clear_source',lambda _s:None)
    return stopped,path,rows,auth


def test_completed_sdk_tool_response_can_prove_a_cleared_terminal_identity(archived):
    (cycle,state,goal,*_),_,_,_=archived
    proof,_=recovery.check_authorization(cycle,state,goal_reader=lambda *_:None)
    assert proof['completed_goal_identity']['created_at']==goal.created_at
    assert proof['completed_goal_identity']['tokens_used']==goal.tokens_used
    assert proof['goal_observation']=='official_archived_completion_after_verified_get_absence'


@pytest.mark.parametrize('change', ['unmatched_call','forged_script','wrong_thread','wrong_identity','two_completions'])
def test_unbound_or_ambiguous_archives_are_rejected(archived,change):
    (cycle,state,_,auth_path,_),path,rows,auth=archived
    if change=='unmatched_call':rows[2]['payload']['call_id']='other-call'
    if change=='forged_script':rows[1]['payload']['input']='text({goal:{status:"complete"}});'
    if change=='wrong_thread':rows[0]['payload']['id']='other-thread'
    if change=='wrong_identity':
        d=json.loads(rows[2]['payload']['output'][0]['text']);d['goal']['createdAt']+=1
        rows[2]['payload']['output'][0]['text']=json.dumps(d)
    if change=='two_completions':rows.append(rows[2])
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows));auth['archived_goal_completion']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();auth_path.write_text(json.dumps(auth))
    with pytest.raises((ValueError,RuntimeError)):recovery.check_authorization(cycle,state,goal_reader=lambda *_:None)


def test_manuscript_or_assistant_completion_claim_cannot_replace_sdk_response(archived):
    (cycle,state,_,auth_path,_),path,rows,auth=archived
    rows=[rows[0],{'type':'response_item','payload':{'type':'message','role':'assistant','content':'Goal complete'}}]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows));auth['archived_goal_completion']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();auth_path.write_text(json.dumps(auth))
    with pytest.raises(ValueError):recovery.check_authorization(cycle,state,goal_reader=lambda *_:None)


def test_foreign_live_goal_cannot_be_masked_by_an_archive(archived):
    (cycle,state,goal,*_),_,_,_=archived;goal.created_at+=1
    with pytest.raises(RuntimeError):recovery.check_authorization(cycle,state,goal_reader=lambda *_:goal)


@pytest.mark.parametrize('unsafe', [False,True])
def test_original_transport_must_have_the_exact_recorded_clear_order(monkeypatch,unsafe):
    source=b'''def _deliver(self, goal):
    if goal.status == "complete":
        clear_goal(self.thread)
        return native.read_goal_findings(goal, self.path)
'''
    if unsafe:source=source.replace(b'        clear_goal(self.thread)\n',b'')
    state={'runtime_source_provenance':{'source_coherent':True,'sdk_git_sha':'1'*40,
        'module_sha256':{'codex_goal_transport.py':hashlib.sha256(source).hexdigest()}}}
    monkeypatch.setattr(archive.subprocess,'run',lambda *_a,**_k:SimpleNamespace(stdout=source))
    if unsafe:
        with pytest.raises(ValueError):archive._original_clear_source(state)
    else:archive._original_clear_source(state)


@pytest.mark.parametrize('fails', [False,True])
def test_goal_is_cleared_only_after_a_valid_findings_delivery(monkeypatch,fails):
    session=SimpleNamespace(capacity_window=Mock(),origin=None,limit_binding=None,
        thread=object(),identifier='thread',handoff_path='/unused/handoff.json')
    sequence=[]
    monkeypatch.setattr(transport,'clear_goal',lambda _:sequence.append('clear'))
    def findings(*_):
        sequence.append('read')
        if fails:raise ValueError('invalid handoff')
        return 'authored findings'
    monkeypatch.setattr(transport.native,'read_goal_findings',findings)
    result=SimpleNamespace(status='complete')
    cls=next(value for value in vars(transport).values() if isinstance(value,type) and hasattr(value,'_deliver'))
    if fails:
        with pytest.raises(ValueError):cls._deliver(session,result)
        assert sequence==['read']
    else:
        assert cls._deliver(session,result)==('thread','authored findings')
        assert sequence==['read','clear']


def test_exact_prefix_allows_only_appended_thread_settings(archived):
    (cycle,state,_,auth_path,_),path,rows,auth=archived
    auth['archived_goal_completion']['byte_length']=path.stat().st_size
    auth_path.write_text(json.dumps(auth))
    with path.open('a') as f:f.write(json.dumps({'type':'event_msg','payload':{
        'type':'thread_settings_applied','thread_id':'old-thread'}})+'\n')
    proof,_=recovery.check_authorization(cycle,state,goal_reader=lambda *_:None)
    assert proof['completed_goal_identity']['status']=='complete'
    with path.open('a') as f:f.write(json.dumps({'type':'event_msg','payload':{
        'type':'thread_goal_updated','goal':{'status':'active'}}})+'\n')
    with pytest.raises(ValueError,match='later operation'):
        recovery.check_authorization(cycle,state,goal_reader=lambda *_:None)
