"""Offline parallel runtime acceptance: no paid model calls or real processes."""
from __future__ import annotations

import json
import os
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.multi_worker import BranchClaims, CycleClaims, reserve_cycle_number, reserve_hypothesis_id, frontier_snapshot
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.worker_runtime import (schedule_once, list_workers, worker_action,
    save_worker, run_once, available_worker_name)


def canvas(root: Path, count=5):
    root.mkdir(exist_ok=True)
    path = root / 'exploration.canvas'  # visualization projection path only
    path.write_text('{"nodes": [], "edges": []}', encoding='utf-8')
    database = root / 'science_graph.sqlite3'
    with ScienceGraphStore(database) as store:
        for i in range(1, count + 1):
            store.add_hypothesis(f'H-{i:04d}', title=f'Branch {i}')
            store.set_frontiers_batch([{"hypothesis_id": f"H-{i:04d}", "status": "active", "reason": "test fixture"}])
    return path


def reserve_process(root: str, canvas_path: str, output):
    output.put((reserve_cycle_number(Path(root), 'g'),
                reserve_hypothesis_id(Path(root), Path(canvas_path).with_name("science_graph.sqlite3"))))


def test_parallel_id_reservations_no_collisions(tmp_path):
    cv = canvas(tmp_path, 1)
    ctx = multiprocessing.get_context('fork')
    queue = ctx.Queue()
    processes = [ctx.Process(target=reserve_process,args=(str(tmp_path),str(cv),queue)) for _ in range(9)]
    for proc in processes: proc.start()
    for proc in processes:
        proc.join(8)
        assert proc.exitcode == 0
    rows = [queue.get(timeout=4) for _ in processes]
    assert len({row[0] for row in rows}) == 9
    assert len({row[1] for row in rows}) == 9
    assert all((tmp_path / name / '.theory-master-cycle').is_file() for name, _ in rows)


def test_frontier_claim_uses_hypothesis_identity_not_projection_nodes(tmp_path):
    cv = canvas(tmp_path, 0)
    db = cv.with_name("science_graph.sqlite3")
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="Parent")
        store.add_hypothesis("H-0002", title="Second")
        store.add_evolution(edge_id="e1", source="H-0001", target="H-0002", operator="derive")
        store.set_frontiers_batch([
            {"hypothesis_id":"H-0001","status":"active","reason":"parent remains a valid origin"},
            {"hypothesis_id":"H-0002","status":"active","reason":"child is also eligible"},
        ])
    found = frontier_snapshot(db)[1]
    assert {r['hypothesis_id'] for r in found} == {'H-0001','H-0002'}
    claim = BranchClaims(tmp_path, db).claim('worker-1', hypothesis_id='H-0001')
    assert claim['entry_node_id'] == 'H-0001'


def test_scheduler_scales_to_five_with_mocked_child(tmp_path, monkeypatch):
    cv = canvas(tmp_path, 7)
    from theory_master import worker_runtime as wr
    monkeypatch.setattr(wr, '_route_capacity', lambda spec, n: (n, [None] * n))
    calls=[]
    monkeypatch.setattr(wr, '_launch_detached_service',
                        lambda args, **kw: calls.append(args))
    spec = SimpleNamespace(research_root=tmp_path, branch_canvas=cv)
    result = schedule_once(spec, resource_slots=5)
    assert len(result['started']) == 5
    assert len({r['worker_id'] for r in result['started']}) == 5
    claims = CycleClaims(tmp_path).list_claims()
    assert len(claims) == 5 and all(c['cycle_id'] is None and c['hypothesis_id'] is None for c in claims)
    assert all('--claim-id' in args and '--epoch' in args for args in calls)
    assert [r['worker_id'] for r in list_workers(tmp_path)] == [f'worker-{i}' for i in range(1, 6)]


def test_scheduler_refuses_existing_legacy_task(tmp_path, monkeypatch):
    from theory_master import worker_runtime as wr
    cv=canvas(tmp_path, 7)
    monkeypatch.setattr(wr, 'runtime_health', lambda root: {'active': True})
    result = schedule_once(SimpleNamespace(research_root=tmp_path, branch_canvas=cv), resource_slots=8)
    assert result['reason'] == 'legacy_runtime_active'
    assert list_workers(tmp_path) == []


def test_exact_worker_pause_and_safe_release(tmp_path, monkeypatch):
    cv=canvas(tmp_path,2)
    from theory_master import worker_runtime as wr
    manager=CycleClaims(tmp_path)
    claim=manager.claim_cycle('worker-2')
    c=reserve_cycle_number(tmp_path,'g')
    manager.bind_cycle('worker-2', claim['claim_id'], claim['fencing_epoch'], c)
    (tmp_path/c/'research_state.json').write_text(json.dumps({'worker_id':'worker-2',
       'status':'paused'}))
    save_worker(tmp_path, 'worker-2',status='RUNNING',current_cycle=c,
       claim_id=claim['claim_id'],epoch=claim['fencing_epoch'],
       target_h_id='H-0002',entry_node_id='H-0002',pid=os.getpid())
    assert worker_action(SimpleNamespace(research_root=tmp_path,branch_canvas=cv),'worker-2','pause')['status']=='PAUSE_REQUESTED'
    assert json.loads((tmp_path/c/'recovery_control.json').read_text())['action']=='pause'
    with pytest.raises(ValueError, match='still live'):
        worker_action(SimpleNamespace(research_root=tmp_path,branch_canvas=cv),'worker-2','release')
    # Stop old process ownership; don't simulate a real kill.
    save_worker(tmp_path,'worker-2',pid=900000,status='PAUSED')
    (tmp_path/c/'recovery_control.json').unlink()
    assert worker_action(SimpleNamespace(research_root=tmp_path,branch_canvas=cv),'worker-2','release')['status']=='RELEASED'
    assert manager.list_claims() == []
    assert manager.history()[-1]['status'] == 'released'


def test_worker_process_full_cycle_boundary_mock(tmp_path, monkeypatch):
    cv=canvas(tmp_path,1)
    manager=CycleClaims(tmp_path)
    claim=manager.claim_cycle('worker-1')
    from theory_master import workflow as workflow_mod, backends as backends_mod
    calls=[]
    class Backend:
        def __init__(self, root, routes): self.root=root
        def preflight_runtime(self): calls.append('preflight')
    class Workflow:
        def __init__(self, spec, backend, routes, route_resolver, **kwargs): self.cycle_dir=None
        def _run_cycle(self):
            calls.append(('cycle', self.worker_id, self.worker_claim_id,
                          type(self.worker_claims).__name__, type(self.worker_proposal_gate).__name__,
                          hasattr(self, 'worker_focus_id')))
            number=reserve_cycle_number(tmp_path,'g')
            self.worker_claims.bind_cycle(self.worker_id, self.worker_claim_id, self.worker_claim_epoch, number)
            self.cycle_dir=tmp_path/number
            return {'status':'over'}
    monkeypatch.setattr(backends_mod,'CodexBackend',Backend)
    monkeypatch.setattr(workflow_mod,'Workflow',Workflow)
    plan = tmp_path / 'research_plan.md'
    plan.write_text('# test plan')
    code=run_once(SimpleNamespace(research_root=tmp_path,branch_canvas=cv,document=plan),'worker-1',claim['claim_id'],claim['fencing_epoch'])
    assert code==0 and calls[0]=='preflight'
    assert calls[1]==('cycle','worker-1',claim['claim_id'],'CycleClaims','ProposalGate',False)
    assert manager.list_claims()==[] and manager.history()[-1]['status']=='finished'
    assert list_workers(tmp_path)[0]['status']=='OVER'


def test_failed_child_start_releases_claim(tmp_path, monkeypatch):
    from theory_master import worker_runtime as wr
    cv = canvas(tmp_path, 2)
    monkeypatch.setattr(wr, '_route_capacity', lambda spec, slots: (slots, [None] * slots))
    def fail_spawn(*args, **kwargs):
        raise OSError('simulated launcher fault')
    monkeypatch.setattr(wr, '_launch_detached_service', fail_spawn)
    with pytest.raises(OSError, match='simulated'):
        schedule_once(SimpleNamespace(research_root=tmp_path, branch_canvas=cv), resource_slots=2)
    assert CycleClaims(tmp_path).list_claims() == []
    assert list_workers(tmp_path)[0]['status'] == 'FAILED_START'


def test_fast_child_does_not_rewind_runtime(tmp_path, monkeypatch):
    from theory_master import worker_runtime as wr
    cv = canvas(tmp_path, 1)
    monkeypatch.setattr(wr, '_route_capacity', lambda spec, slots: (slots, [None] * slots))
    def fake_launch(args, **kwargs):
        worker = args[args.index('--worker') + 1]
        save_worker(tmp_path, worker, status='RUNNING', current_cycle='42')
    monkeypatch.setattr(wr, '_launch_detached_service', fake_launch)
    response = schedule_once(SimpleNamespace(research_root=tmp_path, branch_canvas=cv), resource_slots=1)
    assert len(response['started']) == 1
    status = list_workers(tmp_path)[0]
    assert status['status'] == 'RUNNING' and status['current_cycle'] == '42'


def test_worker_role_routes_are_independent(tmp_path):
    from theory_master.worker_runtime import save_worker_route, worker_route_overrides
    from theory_master.backend_config import task_route_catalog
    variants = task_route_catalog()
    assert variants
    selection = variants[0]
    save_worker(tmp_path, 'worker-1', status='PAUSED')
    save_worker(tmp_path, 'worker-2', status='PAUSED')
    configured = save_worker_route(tmp_path, 'worker-1', role='investigator',
                                   model=selection['model'], provider=selection['provider'],
                                   effort='high')
    assert configured['effective_at'] == 'next_worker_cycle'
    assert worker_route_overrides(tmp_path, 'worker-1')['investigator'].reasoning_effort == 'high'
    assert worker_route_overrides(tmp_path, 'worker-2') == {}
    with pytest.raises(ValueError, match='unsupported'):
        save_worker_route(tmp_path, 'worker-2', role='investigator', model='invalid',
                          provider='Invalid', effort='high')


def test_critical_scientific_dependency_signal_is_distinct_from_new_finding(tmp_path):
    from theory_master.worker_runtime import detect_critical_knowledge_change
    import hashlib
    cv = canvas(tmp_path, 2)
    plan = tmp_path / 'research_plan.md'
    plan.write_text('Science plan v1')
    spec = SimpleNamespace(document=plan, branch_canvas=cv)
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    assert detect_critical_knowledge_change(spec, hypothesis_id='H-0001', plan_digest=digest) is None
    assert detect_critical_knowledge_change(spec, plan_digest=digest) is None
    with ScienceGraphStore(cv.with_name("science_graph.sqlite3")) as store:
        store.set_frontiers_batch([{"hypothesis_id":"H-0002","status":"closed","reason":"other frontier closed"}])
    assert detect_critical_knowledge_change(spec, hypothesis_id='H-0001', plan_digest=digest) is None
    with ScienceGraphStore(cv.with_name("science_graph.sqlite3")) as store:
        store.set_frontiers_batch([{"hypothesis_id":"H-0001","status":"closed","reason":"dependency closed"}])
    assert detect_critical_knowledge_change(spec, hypothesis_id='H-0001', plan_digest=digest)['reason'] == 'own_hypothesis_closed'
    plan.write_text('Science plan v2')
    assert detect_critical_knowledge_change(spec, hypothesis_id='H-0001', plan_digest=digest)['reason'] == 'research_plan_changed'


def test_paused_claim_owner_reserves_account_and_parallel_agent_slot(tmp_path):
    """A paused science Cycle cannot be evicted from the 2-Agent capacity."""
    from theory_master.worker_runtime import _living_workers, save_worker
    canvas = tmp_path / 'canvas.json'
    import json
    canvas.write_text('{"nodes": [], "edges": []}', encoding='utf-8')
    research = tmp_path / 'research'; research.mkdir()
    database = tmp_path / 'science_graph.sqlite3'
    with ScienceGraphStore(database) as store:
        for h in ("H-0001", "H-0002"):
            store.add_hypothesis(h, title=h)
            store.set_frontiers_batch([{"hypothesis_id":h,"status":"active","reason":"fixture"}])
    claims = CycleClaims(research)
    claim = claims.claim_cycle('worker-1')
    claims.update('worker-1', claim['claim_id'], claim['fencing_epoch'], action='pause')
    save_worker(research, 'worker-1', status='PAUSED', current_cycle='127',
                account='1', claim_id=claim['claim_id'], epoch=claim['fencing_epoch'])
    save_worker(research, 'worker-2', status='OVER', account='2', claim_id=None, epoch=None)
    occupied = _living_workers(research)
    assert len(occupied) == 1 and occupied[0]['account'] == '1'
    # A failed Worker with an unreleased Cycle Claim still reserves the slot;
    # it must be resumed or released explicitly before new work can use it.
    save_worker(research, 'worker-1', status='FAILED', current_cycle='127',
                account='1', claim_id=claim['claim_id'], epoch=claim['fencing_epoch'])
    occupied = _living_workers(research)
    assert len(occupied) == 1 and occupied[0]['account'] == '1'


def test_resume_rejects_checkpoint_whose_hypothesis_differs_from_cycle_claim(tmp_path, monkeypatch):
    """Cycle Claims bind identity to Worker + Cycle + H; a mismatched H lock fails closed."""
    from types import SimpleNamespace
    from theory_master.science_graph_store import ScienceGraphStore
    from theory_master.science_canvas_export import canvas_projection
    from theory_master.runtime import write_json
    cv = tmp_path / "exploration.canvas"
    db = tmp_path / "science_graph.sqlite3"
    with ScienceGraphStore(db) as store:
        store.add_hypothesis("H-0001", title="Bound")
        store.add_hypothesis("H-0002", title="Other")
        write_json(cv, canvas_projection(store))
    manager = CycleClaims(tmp_path, db)
    claim = manager.claim_cycle("worker-1")
    cycle = reserve_cycle_number(tmp_path, "goal")
    manager.bind_cycle("worker-1", claim["claim_id"], claim["fencing_epoch"], cycle)
    manager.bind_hypothesis("worker-1", claim["claim_id"], claim["fencing_epoch"], "H-0001")
    manager.update("worker-1", claim["claim_id"], claim["fencing_epoch"], action="pause")
    write_json(tmp_path / cycle / "research_state.json", {
        "worker_id": "worker-1", "hypothesis_lock": {"id": "H-0002"}, "status": "paused",
    })
    save_worker(tmp_path, "worker-1", status="PAUSED", current_cycle=cycle,
                claim_id=claim["claim_id"], epoch=claim["fencing_epoch"], pid=99999999)
    launched = []
    monkeypatch.setattr("theory_master.worker_runtime._launch_detached_service", lambda *a, **k: launched.append(a))
    with pytest.raises(ValueError, match="identity mismatch"):
        worker_action(SimpleNamespace(research_root=tmp_path, branch_canvas=cv), "worker-1", "resume")
    assert launched == []
    assert manager.list_claims()[0]["claim_id"] == claim["claim_id"]


def test_task_scoped_supervisor_and_mail_off_are_inherited_by_worker(tmp_path):
    from theory_master import worker_runtime
    envdir=tmp_path/'.theory-master'
    envdir.mkdir()
    (envdir/'env').write_text('THEORY_MASTER_SUPERVISOR=0\nTHEORY_MASTER_EMAIL_TERMINAL=0\n')
    result=worker_runtime._task_scoped_process_env(tmp_path)
    assert result['THEORY_MASTER_SUPERVISOR']=='0'
    assert result['THEORY_MASTER_EMAIL_TERMINAL']=='0'


def test_worker_resume_restores_pending_human_guidance(tmp_path):
    from theory_master.worker_runtime import _restore_cycle_operator_guidance
    from theory_master.attention_guidance import write_attention_guidance
    cycle = tmp_path / "139"
    cycle.mkdir()
    text = "Human approved the frozen 16-network evaluation plan."
    write_attention_guidance(cycle, text, source="human", attention_code="run_failed")
    state = {"recovery": {"kind": "NativeGoalRuntimeFailure"}}
    restored = _restore_cycle_operator_guidance(state, cycle)
    assert restored["operator_guidance_pending"] is True
    assert restored["operator_guidance"]["text"] == text
    assert restored["recovery"]["attention_guidance"]["source"] == "human"
