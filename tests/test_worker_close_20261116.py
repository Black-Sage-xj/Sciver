"""Offline close controls: exact owner, no model/backend/process launch."""
import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from theory_master import worker_runtime as wr
from theory_master.multi_worker import CycleClaims, reserve_cycle_number
from theory_master.worker_close import close_worker, watch_close
from theory_master.worker_mode import configure_mode, read_mode, request_one_worker
from tm_observer.worker_console import registered_workers, tab_bar
from tm_observer.run_control import _validated_value, OperatorError


def setup(root, status='PAUSED', claim=True):
    configure_mode(root, enabled=True, desired_agents=3)
    for number in (1, 2, 3):
        wr.save_worker(root, f'worker-{number}', status=status)
    spec = SimpleNamespace(research_root=root, branch_canvas=root/'exploration.canvas')
    if claim:
        claims = CycleClaims(root)
        owned = claims.claim_cycle('worker-2')
        cycle = reserve_cycle_number(root, 'g')
        claims.bind_cycle('worker-2', owned['claim_id'], owned['fencing_epoch'], cycle)
        (root/cycle/'research_state.json').write_text(json.dumps({
            'worker_id':'worker-2', 'status':status.lower(),
            'judgment':{'verdict':'unchanged'}, 'hypothesis_id':'H-1'}))
        wr.save_worker(root, 'worker-2', current_cycle=cycle,
                       claim_id=owned['claim_id'], epoch=owned['fencing_epoch'])
    return spec


@pytest.mark.parametrize('status', ['PAUSED', 'FAILED', 'NEEDS_ATTENTION', 'INTERRUPTED', 'OVER', 'COMPLETE', 'CANCELLED'])
def test_close_stopped_keeps_science_and_siblings(tmp_path, status):
    spec = setup(tmp_path, status)
    before = [wr.read_worker(tmp_path, wid) for wid in ('worker-1', 'worker-3')]
    cycle = wr.read_worker(tmp_path, 'worker-2')['current_cycle']
    result = close_worker(spec, 'worker-2')
    assert result['status'] == 'closed' and result['history_preserved']
    assert read_mode(tmp_path)['desired_agents'] == 2
    assert CycleClaims(tmp_path).list_claims() == []
    state = json.loads((tmp_path/cycle/'research_state.json').read_text())
    assert state['judgment'] == {'verdict':'unchanged'}
    assert state['status'] == status.lower()
    assert state['resumable'] is False
    assert [wr.read_worker(tmp_path, wid) for wid in ('worker-1','worker-3')] == before
    assert close_worker(spec, 'worker-2')['status'] == 'closed'
    assert read_mode(tmp_path)['desired_agents'] == 2
    assert wr.available_worker_name(tmp_path, exclude=frozenset({'worker-1','worker-3'})) == 'worker-4'


def test_live_close_waits_for_exit_and_fences_resume(tmp_path, monkeypatch):
    spec = setup(tmp_path, 'RUNNING')
    launches = []
    live = [True]
    monkeypatch.setattr(wr, 'process_identity_alive', lambda *a: live[0])
    monkeypatch.setattr(wr, '_launch_detached_service', lambda *a, **kw: launches.append((a,kw)))
    assert close_worker(spec, 'worker-2')['status'] == 'closing'
    assert len(launches) == 1
    assert 'theory_master.worker_close' in launches[0][0][0]
    cycle = wr.read_worker(tmp_path, 'worker-2')['current_cycle']
    assert json.loads((tmp_path/cycle/'recovery_control.json').read_text())['action'] == 'pause'
    assert not wr.read_worker(tmp_path, 'worker-2').get('dismissed')
    assert len(CycleClaims(tmp_path).list_claims()) == 1
    assert read_mode(tmp_path)['desired_agents'] == 3
    with pytest.raises(ValueError, match='closing or closed'):
        wr.worker_action(spec, 'worker-2', 'resume')
    live[0] = False
    assert watch_close(spec, 'worker-2') == 0
    assert wr.read_worker(tmp_path, 'worker-2')['dismissed']
    assert not CycleClaims(tmp_path).list_claims()


def test_primary_cannot_close_or_dismiss(tmp_path):
    spec = setup(tmp_path, 'OVER', claim=False)
    for action in ('close', 'dismiss'):
        with pytest.raises(ValueError, match='permanent primary'):
            wr.worker_action(spec, 'worker-1', action)


def test_reconciliation_failure_keeps_visible_claim_and_retry(tmp_path):
    spec = setup(tmp_path, 'PAUSED')
    cycle = wr.read_worker(tmp_path, 'worker-2')['current_cycle']
    state_path = tmp_path/cycle/'research_state.json'
    good = state_path.read_text()
    state_path.write_text(json.dumps({'worker_id':'worker-3', 'status':'paused'}))
    with pytest.raises(ValueError, match='identity mismatch'):
        close_worker(spec, 'worker-2')
    assert len(CycleClaims(tmp_path).list_claims()) == 1
    assert read_mode(tmp_path)['desired_agents'] == 3
    run = {'research_root': str(tmp_path), 'observer_id': 'a'*16}
    rows = registered_workers(run)
    assert rows[1]['status'] == 'CLOSE_FAILED'
    doc = BeautifulSoup(tab_bar(run, rows, 'worker-1'), 'html.parser')
    assert not doc.select_one('[data-worker-close="worker-2"]').has_attr('disabled')
    state_path.write_text(good)
    assert close_worker(spec, 'worker-2')['status'] == 'closed'


def test_roster_preserves_remaining_ids_and_add_uses_new_id(tmp_path):
    spec = setup(tmp_path, 'FAILED')
    assert close_worker(spec,'worker-2')['status'] == 'closed'
    assert wr.scheduler_worker_ids(tmp_path, 2) == ('worker-1', 'worker-3')
    request_one_worker(tmp_path, registered_count=2)
    assert wr.scheduler_worker_ids(tmp_path, 3) == ('worker-1', 'worker-3', 'worker-4')


@pytest.mark.parametrize('status', ['PAUSED','FAILED','RUNNING','ORPHANED','OVER','WAITING_RESEARCH_DIRECTION','CLOSING'])
def test_auxiliary_tabs_always_have_close_primary_never_does(tmp_path, status):
    setup(tmp_path, claim=False)
    wr.save_worker(tmp_path, 'worker-2', status=status,
                   close_requested=status=='CLOSING')
    run={'research_root':str(tmp_path), 'observer_id':'a'*16}
    doc=BeautifulSoup(tab_bar(run, registered_workers(run), 'worker-1'), 'html.parser')
    assert not doc.select_one('[data-worker-close="worker-1"]')
    button=doc.select_one('[data-worker-close="worker-2"]')
    assert button and button.has_attr('disabled') == (status=='CLOSING')
    assert doc.select_one('[data-worker-close="worker-3"]')
    value={'worker_id':'worker-2', 'operation':'close'}
    assert _validated_value('worker_control',value)==value
    with pytest.raises(OperatorError):
        _validated_value('worker_control',{**value,'account':'1'})


def test_capacity_reduction_replay_after_partial_close_is_idempotent(tmp_path, monkeypatch):
    spec=setup(tmp_path, 'FAILED')
    original=wr.save_worker
    def fail_hide(root, wid, **changes):
        if changes.get('dismissed'):
            raise OSError('simulated crash before hiding')
        return original(root,wid,**changes)
    monkeypatch.setattr(wr,'save_worker',fail_hide)
    with pytest.raises(OSError):
        close_worker(spec,'worker-2')
    assert read_mode(tmp_path)['desired_agents']==2
    assert not wr.read_worker(tmp_path,'worker-2').get('dismissed')
    monkeypatch.setattr(wr,'save_worker',original)
    assert close_worker(spec,'worker-2')['status']=='closed'
    assert read_mode(tmp_path)['desired_agents']==2


def test_close_fences_checkpoint_of_another_live_worker(tmp_path, monkeypatch):
    spec=setup(tmp_path,'RUNNING')
    cycle=wr.read_worker(tmp_path,'worker-2')['current_cycle']
    state=tmp_path/cycle/'research_state.json'
    state.write_text(json.dumps({'worker_id':'worker-3','status':'active'}))
    monkeypatch.setattr(wr,'process_identity_alive',lambda *args:True)
    with pytest.raises(ValueError,match='claim mismatch'):
        close_worker(spec,'worker-2')
    assert not (tmp_path/cycle/'recovery_control.json').exists()
    assert not wr.read_worker(tmp_path,'worker-2').get('close_requested')


def test_paused_close_must_match_claim_cycle(tmp_path):
    spec=setup(tmp_path,'PAUSED')
    wr.save_worker(tmp_path,'worker-2',current_cycle='999')
    with pytest.raises(ValueError,match='claim changed'):
        close_worker(spec,'worker-2')
    assert len(CycleClaims(tmp_path).list_claims())==1


def test_all_pending_requests_have_cancel_and_no_science(tmp_path):
    setup(tmp_path,claim=False)
    request_one_worker(tmp_path,registered_count=3)
    request_one_worker(tmp_path,registered_count=3)
    run={'research_root':str(tmp_path), 'observer_id':'a'*16}
    doc=BeautifulSoup(tab_bar(run,registered_workers(run),'worker-1'),'html.parser')
    assert len(doc.select('[data-worker-cancel]'))==2


def test_restart_with_changed_roster_keeps_remaining_cycle_budgets(tmp_path, monkeypatch):
    spec=setup(tmp_path,'OVER',claim=False)
    close_worker(spec,'worker-2')
    meta=tmp_path/'.theory-master/worker_scheduler.json'
    meta.write_text(json.dumps({'quota_mode':'per_worker','status':'STOPPED',
        'resume_batch':True,'max_cycles_per_worker':8,
        'worker_ids':['worker-1','worker-2','worker-3'],
        'cycles_started_by_worker':{'worker-1':7,'worker-2':2,'worker-3':5}}))
    spec.max_cycles=8
    from theory_master import specification, worker_supervisor_dispatch
    monkeypatch.setattr(specification.TaskSpec,'from_task_root',classmethod(lambda cls,path:spec))
    monkeypatch.setattr(wr,'_master_complete',lambda *args,**kwargs:False)
    monkeypatch.setattr(worker_supervisor_dispatch,'dispatch_pending',lambda *args:False)
    monkeypatch.setattr(wr.threading.Event,'wait',lambda *args:False)
    launched=[]
    def dispatch(spec,**kwargs):
        names=sorted(kwargs['eligible_worker_ids'])
        launched.extend(names)
        return {'started':[{'worker_id':wid} for wid in names]}
    monkeypatch.setattr(wr,'schedule_once',dispatch)
    assert wr.scheduler_daemon(spec,2)==0
    assert launched.count('worker-1')==1
    assert launched.count('worker-3')==3
    assert 'worker-2' not in launched


def test_close_launch_failure_remains_visible_and_retryable(tmp_path, monkeypatch):
    spec=setup(tmp_path,'RUNNING')
    monkeypatch.setattr(wr,'process_identity_alive',lambda *args:True)
    def fail_launch(*args,**kwargs):
        raise RuntimeError('fixture service unavailable')
    monkeypatch.setattr(wr,'_launch_detached_service',fail_launch)
    with pytest.raises(RuntimeError,match='service unavailable'):
        close_worker(spec,'worker-2')
    run={'research_root':str(tmp_path),'observer_id':'a'*16}
    assert registered_workers(run)[1]['status']=='CLOSE_FAILED'
    assert len(CycleClaims(tmp_path).list_claims())==1
    monkeypatch.setattr(wr,'process_identity_alive',lambda *args:False)
    assert close_worker(spec,'worker-2')['status']=='closed'
