"""Deterministic budget checks: no Codex calls or science state mutations."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from theory_master import worker_runtime
from theory_master.worker_mode import configure_mode


@pytest.mark.parametrize('prior,expected', [({}, 4), ({'worker-1': 1, 'worker-2': 1}, 2)])
def test_each_worker_owns_its_two_cycle_allowance(tmp_path, monkeypatch, prior, expected):
    root = tmp_path / 'research'
    root.mkdir()
    configure_mode(root, enabled=True, desired_agents=2)
    meta = root / '.theory-master' / 'worker_scheduler.json'
    if prior:
        meta.write_text(json.dumps({'quota_mode': 'per_worker', 'status': 'STOPPED',
                                    'max_cycles_per_worker': 2,
                                    'worker_ids': ['worker-1', 'worker-2'],
                                    'cycles_started_by_worker': prior,
                                    'resume_batch': True}))
    spec = SimpleNamespace(research_root=root, max_cycles=2)
    from theory_master import specification, worker_supervisor_dispatch
    monkeypatch.setattr(specification.TaskSpec, 'from_task_root', classmethod(lambda cls, path: spec))
    monkeypatch.setattr(worker_runtime, '_master_complete', lambda root, **kw: False)
    monkeypatch.setattr(worker_supervisor_dispatch, 'dispatch_pending', lambda root: False)
    monkeypatch.setattr(worker_runtime.threading.Event, 'wait', lambda self, n: False)
    launched = []
    def fake_dispatch(spec, *, resource_slots, preferred_hypothesis=None, eligible_worker_ids=None):
        chosen = sorted(eligible_worker_ids)
        launched.extend(chosen)
        return {'started': [{'worker_id': name} for name in chosen]}
    monkeypatch.setattr(worker_runtime, 'schedule_once', fake_dispatch)
    assert worker_runtime.scheduler_daemon(spec, 2) == 0
    assert len(launched) == expected
    data = json.loads(meta.read_text())
    assert data['status'] == 'STOPPED'
    assert data['quota_mode'] == 'per_worker'
    assert data['cycles_started_by_worker'] == {'worker-1': 2, 'worker-2': 2}
    assert not data['resume_batch']


def test_worker_profile_is_independent_and_retains_preset_semantics(tmp_path, monkeypatch):
    root = tmp_path / 'research'
    (root / '.theory-master' / 'workers' / 'worker-1').mkdir(parents=True)
    (root / '.theory-master' / 'workers' / 'worker-2').mkdir(parents=True)
    (root / '.theory-master' / 'workers' / 'worker-1' / 'runtime.json').write_text('{}')
    assert worker_runtime.save_worker_profile(root, 'worker-1', '2')['profile'] == 'codex'
    assert worker_runtime.read_worker_profile(root, 'worker-1') == 'codex'
    assert worker_runtime.read_worker_profile(root, 'worker-2') is None
    from theory_master.backend_config import MODEL_PROFILES
    routes = worker_runtime.resolve_worker_routes(root, 'worker-1')
    assert routes['research_lead'] == MODEL_PROFILES['codex']['research_lead']
    assert routes['investigator'] == MODEL_PROFILES['codex']['investigator']


def test_reallocated_worker_keeps_its_pinned_account(tmp_path, monkeypatch):
    from theory_master import worker_runtime
    root = tmp_path / 'research'
    root.mkdir()
    spec = SimpleNamespace(research_root=root, branch_canvas=root / 'canvas.json')
    existing = [dict(worker_id='worker-1', account='1', effective_status='OVER', process_alive=False),
                dict(worker_id='worker-2', account='2', effective_status='OVER', process_alive=False)]
    class FakeClaims:
        def __init__(self, *args): pass
        def claim_cycle(self, worker, **kwargs):
            return {'cycle_id':None,'hypothesis_id':None,'claim_id':f'claim-{worker}','fencing_epoch':1}
    monkeypatch.setattr(worker_runtime,'CycleClaims',FakeClaims)
    monkeypatch.setattr(worker_runtime,'runtime_health',lambda root:{'active':False})
    monkeypatch.setattr(worker_runtime,'_master_complete',lambda root, **kw:False)
    monkeypatch.setattr(worker_runtime,'_living_workers',lambda root:[])
    monkeypatch.setattr(worker_runtime,'_route_capacity',lambda spec,slots:(2,['1','2']))
    monkeypatch.setattr(worker_runtime,'_share_enabled',lambda root:True)
    monkeypatch.setattr(worker_runtime,'list_workers',lambda root:existing)
    monkeypatch.setattr(worker_runtime,'read_worker',lambda root,worker:next(w for w in existing if w['worker_id']==worker))
    monkeypatch.setattr(worker_runtime,'save_worker',lambda *a,**kw:None)
    monkeypatch.setattr(worker_runtime,'_task_scoped_process_env',lambda root:{})
    monkeypatch.setattr(worker_runtime,'_pinned_codex_home',lambda root,worker,acct:tmp_path)
    monkeypatch.setattr(worker_runtime,'process_start_ticks',lambda pid:1)
    monkeypatch.setattr(worker_runtime,'_shared_account_candidate',lambda accounts,active,started:sorted(accounts)[-1] if accounts else None)
    spawned=[]
    def fake_launch(args, **kwargs):
        spawned.append(args)
    monkeypatch.setattr(worker_runtime, '_launch_detached_service', fake_launch)
    outcome=worker_runtime.schedule_once(spec,resource_slots=2,
                    eligible_worker_ids=frozenset({'worker-1','worker-2'}))
    assert [(row['worker_id'],row['account']) for row in outcome['started']] == [
        ('worker-1','1'),('worker-2','2')]


def test_increasing_per_worker_cap_preserves_existing_counts(tmp_path, monkeypatch):
    root = tmp_path / 'research'
    root.mkdir()
    configure_mode(root, enabled=True, desired_agents=2)
    meta = root / '.theory-master' / 'worker_scheduler.json'
    meta.write_text(json.dumps({
        'quota_mode': 'per_worker', 'status': 'STALLED', 'max_cycles_per_worker': 10,
        'worker_ids': ['worker-1', 'worker-2'],
        'cycles_started_by_worker': {'worker-1': 10, 'worker-2': 2},
        'resume_batch': True,
    }))
    spec = SimpleNamespace(research_root=root, max_cycles=20)
    from theory_master import specification, worker_supervisor_dispatch
    monkeypatch.setattr(specification.TaskSpec, 'from_task_root', classmethod(lambda cls, path: spec))
    monkeypatch.setattr(worker_runtime, '_master_complete', lambda root, **kw: False)
    monkeypatch.setattr(worker_supervisor_dispatch, 'dispatch_pending', lambda root: False)
    monkeypatch.setattr(worker_runtime.threading.Event, 'wait', lambda self, n: False)
    monkeypatch.setattr(worker_runtime, 'SCHEDULER_NO_PROGRESS_GRACE_SECONDS', 0)
    monkeypatch.setattr(worker_runtime, 'schedule_once', lambda *a, **kw: {'started': [], 'reason': 'no eligible idle Worker'})
    assert worker_runtime.scheduler_daemon(spec, 2) == 2
    data = json.loads(meta.read_text())
    assert data['max_cycles_per_worker'] == 20
    assert data['cycles_started_by_worker'] == {'worker-1': 10, 'worker-2': 2}
    assert data['remaining_cycles_by_worker'] == {'worker-1': 10, 'worker-2': 18}


