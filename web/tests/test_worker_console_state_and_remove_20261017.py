"""Dual Worker status isolation and safe, reversible UI queue management."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from theory_master.tui.web_bridge import project
from theory_master.worker_mode import request_one_worker, reduce_one_worker_request, read_mode
from theory_master.worker_runtime import worker_action
from tm_observer.worker_console import registered_workers, tab_bar
from tm_observer.run_control import _validated_value, OperatorError
from tm_observer.run_views import run_detail_body


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf8')


def setup_workers(root: Path):
    write_json(root / '.theory-master/worker_mode.json',
               {'schema':'theory-master/worker-mode/v1', 'enabled': True,
                'desired_agents': 2, 'requested_agents': 2})
    for index, status in ((1, 'RUNNING'), (2, 'OVER')):
        cycle = root / str(index)
        cycle.mkdir(parents=True)
        (cycle / '.theory-master-cycle').write_text('v2')
        write_json(cycle / 'run_state.json',
                   {'status':status, 'cycle_id':str(index), 'cycle_index':index,
                    'max_cycles':40, 'project_tokens_total':111*index,
                    'tokens_total':111*index, 'phase':'investigator:running',
                    'research_root':str(root), 'execution_active':index==1})
        write_json(cycle / 'research_state.json',
                   {'status':'active' if index == 1 else 'over',
                    'worker_id':f'worker-{index}', 'cycle_id':str(index)})
        write_json(root / f'.theory-master/workers/worker-{index}/runtime.json',
                   {'schema':'theory-master/worker-runtime/v1',
                    'worker_id':f'worker-{index}', 'current_cycle':str(index),
                    'status':status})


def test_two_worker_cycle_projection_not_global_latest(tmp_path):
    root = tmp_path/'research'
    setup_workers(root)
    first = project(root, cycle_dir=root/'1', worker_id='worker-1')
    second = project(root, cycle_dir=root/'2', worker_id='worker-2')
    assert first['status'] == 'RUNNING' and first['cycle_id'] == '1'
    assert second['status'] == 'OVER' and second['cycle_id'] == '2'
    assert 'State OVER' in second['parts']['header']
    assert 'Cycle 2 / 40' in second['parts']['header']
    assert first['usage_totals']['cycle']['tokens_total'] == 111
    assert second['usage_totals']['cycle']['tokens_total'] == 222
    run = {'observer_id':'a'*16, 'research_root':str(root),
           'cycle_dir':str(root/'1'), 'cycle':'1', 'state':'RUNNING',
           'task_name':'Mock dual Workers'}
    doc = BeautifulSoup(run_detail_body(run, worker_id='worker-2'), 'html.parser')
    assert doc.select_one('.tm-worker-tab.is-selected small').get_text() == 'OVER'
    assert doc.select_one('.tm-tui-header-stats .tm-tui-kv dd').get_text(strip=True).startswith('OVER')
    assert doc.select_one('.tm-tui-screen')['data-tui-worker'] == 'worker-2'


def test_reject_mismatched_cycle_provenance(tmp_path):
    root = tmp_path/'research'
    setup_workers(root)
    state = root/'2'/'research_state.json'
    write_json(state, {'worker_id':'worker-1', 'status':'active'})
    with pytest.raises(ValueError, match='provenance mismatch'):
        project(root, cycle_dir=root/'2', worker_id='worker-2')


def test_cancel_only_unassigned_request_and_preserve_running_workers(tmp_path):
    root = tmp_path/'research'
    setup_workers(root)
    assert request_one_worker(root,registered_count=2)['requested_agents']==3
    run={'research_root':str(root),'observer_id':'a'*16}
    nav = BeautifulSoup(tab_bar(run,registered_workers(run),'worker-1'),'html.parser')
    assert nav.select_one('[data-worker-cancel]')
    assert reduce_one_worker_request(root, registered_count=2)['desired_agents']==2
    assert read_mode(root)['desired_agents']==2
    with pytest.raises(ValueError,match='already assigned'):
        reduce_one_worker_request(root,registered_count=2)
    assert not BeautifulSoup(tab_bar(run,registered_workers(run),'worker-1'),'html.parser').select('[data-worker-cancel]')
    assert (root/'.theory-master/workers/worker-1/runtime.json').is_file()


def test_terminal_worker_hide_keeps_cycle_data(tmp_path):
    root = tmp_path/'research'
    setup_workers(root)
    spec = SimpleNamespace(research_root=root, branch_canvas=root/'branch_canvas.json')
    run={'research_root':str(root),'observer_id':'a'*16}
    page=BeautifulSoup(tab_bar(run,registered_workers(run),'worker-2'), 'html.parser')
    assert page.select_one('[data-worker-dismiss="worker-2"]')
    assert not page.select_one('[data-worker-dismiss="worker-1"]')
    assert worker_action(spec, 'worker-2', 'dismiss')['history_preserved'] is True
    assert read_mode(root)['desired_agents']==1
    assert [w['worker_id'] for w in registered_workers(run)]==['worker-1']
    assert (root/'2'/'run_state.json').is_file()
    assert (root/'2'/'research_state.json').is_file()
    assert json.loads((root/'.theory-master/workers/worker-2/runtime.json').read_text())['dismissed'] is True
    with pytest.raises(ValueError):
        worker_action(spec, 'worker-1', 'dismiss')


def test_remove_actions_validate_exact_shape():
    assert _validated_value('cancel_pending_worker',None) is None
    good={'worker_id':'worker-2','operation':'dismiss'}
    assert _validated_value('worker_control',good)==good
    for action,value in [('cancel_pending_worker',{'worker_id':'worker-1'}),
                         ('worker_control',{'worker_id':'worker-2','operation':'rm'}),
                         ('worker_control',{'worker_id':'../../secrets','operation':'dismiss'})]:
        with pytest.raises(OperatorError):
            _validated_value(action,value)


def test_selected_worker_survives_secondary_navigation_and_poll(tmp_path):
    root=tmp_path/'research'
    setup_workers(root)
    run={'observer_id':'a'*16,'research_root':str(root), 'cycle_dir':str(root/'1'),
         'cycle':'1','state':'RUNNING','task_name':'Mock'}
    page=run_detail_body(run,worker_id='worker-2')
    assert "if(selectedWorker)params.set('worker',selectedWorker)" in page
    assert "requestedWorker!==selectedWorker" in page
    assert "oldTabs.replaceWith(newTabs)" in page
    assert 'data-tui-worker="worker-2"' in page
    assert 'data-worker-action="run_next"' in page
    # A Worker State card must not route its click to Master-wide controls.
    doc=BeautifulSoup(page,'html.parser')
    assert not doc.select_one('.tm-tui-header-stats [data-tui-process-actions]')


def test_cannot_hide_worker_that_still_owns_a_branch_claim(tmp_path):
    root=tmp_path/'research'
    setup_workers(root)
    write_json(root/'.theory-master/cycle_claims.json', {
        'schema':'theory-master/cycle-claims/v1','epoch':1,'history':[],
        'claims':{'owned':{'worker_id':'worker-2','claim_id':'owned','fencing_epoch':1,
                           'cycle_id':'2','hypothesis_id':None,'status':'paused'}}})
    spec=SimpleNamespace(research_root=root,branch_canvas=root/'branch_canvas.json')
    with pytest.raises(ValueError, match='still owns a research claim'):
        worker_action(spec,'worker-2','dismiss')
    assert [w['worker_id'] for w in registered_workers({'research_root':str(root)})]==['worker-1','worker-2']
