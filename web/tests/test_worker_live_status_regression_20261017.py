"""Regression for V2 Worker live while legacy Supervisor is stopped."""
from __future__ import annotations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from tm_observer.collector import TaskLocation, collect_run
from tm_observer.worker_console import registered_workers, select_worker
from tm_observer.run_views import run_detail_body


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data),encoding='utf-8')


def _fixture(root: Path, *, cycle_status: str = 'active', worker_pid: int | None = None,
             current_cycle: str = '130', claimed: str = 'worker-1') -> TaskLocation:
    worker_pid = worker_pid or os.getpid()
    task = root/'task'
    research = task/'research'
    config = {'research_root':str(research),'task_id':1}
    _write(task/'.theory-master/task.json',{'schema':'theory-master/task/v2'})
    _write(task/'config.json',config)
    _write(research/'.theory-master/runtime.json',{'schema':'theory-master/runtime/v1',
               'state':'RUNNING','runner_pid':99999999})
    _write(research/'130/run_state.json',{'status':cycle_status.upper(),
             'execution_active':True,'owner_pid':99999999,'phase':'investigator:running'})
    _write(research/'130/research_state.json',{'status':cycle_status,'worker_id':claimed,
             'cycle_id':'130','phase':'investigator:running', 'execution_active':True,'owner_pid':99999999})
    from tm_observer.collector import _proc_start_ticks
    _write(research/'.theory-master/workers/worker-1/runtime.json',{
       'schema':'theory-master/worker-runtime/v1','worker_id':'worker-1',
       'status':'RUNNING','current_cycle':current_cycle,'pid':worker_pid,
       'pid_start_ticks':_proc_start_ticks(worker_pid)})
    return TaskLocation(task,research,task/'.theory-master/task.json',task/'config.json',config)


def test_latest_live_worker_overrides_obsolete_legacy_runner_orphaned():
    with TemporaryDirectory() as d:
        run=collect_run(_fixture(Path(d)))
        assert run['state']=='LIVE'
        assert run['pid']==os.getpid()
        assert run['pid_alive'] is True
        assert run['provenance']['state']['field']=='verified_worker_identity'
        assert not any(w['code']=='ORPHANED' for w in run['warnings'])


def test_wrong_cycle_or_terminal_state_must_not_appear_live():
    with TemporaryDirectory() as d:
        run=collect_run(_fixture(Path(d),current_cycle='129'))
        assert run['state']=='ORPHANED'
    with TemporaryDirectory() as d:
        run=collect_run(_fixture(Path(d),cycle_status='over'))
        assert run['state']=='OVER'


def test_dead_worker_does_not_mask_orphaned_runtime():
    with TemporaryDirectory() as d:
        run=collect_run(_fixture(Path(d),worker_pid=99999999))
        assert run['state']=='ORPHANED'


def test_old_cycle_live_worker_not_masked_by_newer_over_worker():
    with TemporaryDirectory() as d:
        location=_fixture(Path(d))
        research=location.research_root
        _write(research/'131/run_state.json', {'status':'OVER', 'execution_active':False,
                                             'phase':'reporter:done'})
        _write(research/'131/research_state.json', {'status':'over', 'worker_id':'worker-2'})
        _write(research/'.theory-master/workers/worker-2/runtime.json', {
            'schema':'theory-master/worker-runtime/v1', 'worker_id':'worker-2',
            'status':'OVER', 'current_cycle':'131'})
        run=collect_run(location)
        assert run['cycle']=='131'  # newest scientific Cycle stays visible
        assert run['state']=='LIVE'  # Worker-1 remains in its own active Cycle 130
        assert run['pid']==os.getpid()
        assert run['provenance']['state']['field']=='verified_worker_identity'
        workers=registered_workers(run)
        assert [w['status'] for w in workers]==['RUNNING','OVER']


def test_dead_worker_console_and_list_both_show_orphaned():
    from bs4 import BeautifulSoup
    with TemporaryDirectory() as d:
        location=_fixture(Path(d),worker_pid=99999999)
        research=location.research_root
        _write(research/'.theory-master/worker_mode.json', {
            'schema':'theory-master/worker-mode/v1','enabled':True,'desired_agents':1})
        (research/'130/.theory-master-cycle').write_text('v2')
        run=collect_run(location)
        assert run['state']=='ORPHANED'
        assert run['active_agent'] is None
        workers=registered_workers(run)
        assert len(workers)==1
        assert workers[0]['raw_status']=='RUNNING'
        assert workers[0]['status']=='ORPHANED'
        projected, _, chosen=select_worker(run,'worker-1')
        assert chosen=='worker-1' and projected['state']=='ORPHANED'
        document=BeautifulSoup(run_detail_body(run,worker_id='worker-1'),'html.parser')
        assert document.select_one('.tm-tui-title-actions .tm-tui-state-badge') is None
        assert document.select_one('.tm-tui-process').get_text(strip=True).startswith('ORPHANED')
        assert document.select_one('.tm-worker-tab.is-selected small').get_text(strip=True)=='ORPHANED'
        assert '登记进程未通过身份校验' in document.get_text()


def test_reused_pid_does_not_count_as_running_worker():
    with TemporaryDirectory() as d:
        location=_fixture(Path(d))
        path=location.research_root/'.theory-master/workers/worker-1/runtime.json'
        record=json.loads(path.read_text())
        record['pid_start_ticks']+=1
        _write(path,record)
        run=collect_run(location)
        assert run['state']=='ORPHANED'
        assert registered_workers(run)[0]['status']=='ORPHANED'
