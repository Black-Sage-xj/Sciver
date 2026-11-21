"""Offline Worker claim progression after a local research route ends."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from theory_master import worker_runtime as wr
from theory_master.multi_worker import CycleClaims, reserve_cycle_number
from theory_master.scientific_objective import frozen_science_digest
from theory_master.science_graph_store import ScienceGraphStore
from theory_master.task_progression import local_replan_reason
from theory_master.worker_mode import configure_mode


def local_state(reason, cycle='1'):
    state = {'cycle_id': cycle, 'worker_id': 'worker-1', 'execution_active': False,
             'protocol_health': 'valid', 'scientific_verdict': None, 'resumable': False,
             'next_start_node_id': None}
    if reason == 'no_qualified_candidate':
        state.update(status=reason, phase='qualification', qualification_status=reason,
                     hypothesis_lock={'status': 'unlocked'}, qualification_attempts=[{'attempt': 1}],
                     frontier_review={'status': 'unqualified'}, attention={'code': reason})
    else:
        lock = {'id': f'H-{int(cycle):04d}', 'status': 'locked', 'hypothesis': 'Frozen hypothesis.',
                'scope': 'Frozen scope.', 'decisive_test': 'Frozen test.'}
        report = {'kind': reason, 'invocation_id': f'inv-{cycle}-investigator-original',
                  'frozen_objective_sha256': frozen_science_digest(lock), 'reason': 'Required input is absent.'}
        state.update(status='over', phase='complete', assessment_state='not_adjudicated',
                     hypothesis_lock=lock,
                     operational_only_closure={'kind': reason, 'execution_limit': report},
                     execution_limit_handoff={'invocation_id': report['invocation_id'],
                         'execution_limit': copy.deepcopy(report), 'handoff_path': '/bound/handoff.json'})
    return state


@pytest.fixture
def task(tmp_path, monkeypatch):
    root = tmp_path / 'research'
    root.mkdir()
    canvas = tmp_path / 'exploration.canvas'
    canvas.write_text('{"nodes":[],"edges":[]}')
    with ScienceGraphStore(tmp_path / 'science_graph.sqlite3') as store:
        for n in (1, 2, 3):
            store.add_hypothesis(f'H-{n:04d}', title=f'Existing H {n}')
    plan = tmp_path / 'research_plan.md'
    plan.write_text('# Goal\nAn unfinished research Goal.')
    spec = SimpleNamespace(research_root=root, branch_canvas=canvas, document=plan, max_cycles=2,
                           task_mode=True, task_home=tmp_path)
    monkeypatch.setattr('theory_master.worker_routes.resolve_worker_routes', lambda *_: {})
    monkeypatch.setattr('theory_master.worker_routes._route_capacity', lambda spec, n: (n, [None] * n))
    monkeypatch.setattr('theory_master.worker_launch._task_scoped_process_env', lambda *_: {})
    monkeypatch.setattr('theory_master.worker_execution._update_cycle_memory_index', lambda *a, **k: None)
    return spec, root


def install_workflow(monkeypatch, spec, outcome):
    """Keep real Worker/Claim/Scheduler code; substitute only scientific execution."""
    from theory_master import backends, workflow
    from theory_master.observability import collector
    root, visits, snapshots = spec.research_root, [], []
    class Backend:
        def __init__(self, *a, **kw):
            pass
        def preflight_runtime(self):
            pass
    class Workflow:
        def __init__(self, *a, **kw):
            self.cycle_dir = None
        def _run_cycle(self, resume_state=None):
            assert resume_state is None  # Local failure always allocates a new Cycle.
            current = self.worker_claims.assert_active(self.worker_id, self.worker_claim_id,
                                                       self.worker_claim_epoch)
            assert current['cycle_id'] is None and current['hypothesis_id'] is None
            assert not hasattr(self, 'worker_focus_id')
            cycle = reserve_cycle_number(root, 'g')
            self.worker_claims.bind_cycle(self.worker_id, self.worker_claim_id,
                                          self.worker_claim_epoch, cycle)
            self.cycle_dir = root / cycle
            state = outcome(cycle) if callable(outcome) else copy.deepcopy(outcome)
            state.update(cycle_id=cycle, worker_id=self.worker_id)
            if state.get('status') == 'no_qualified_candidate':
                state['wake_fingerprint'] = wr.research_direction_fingerprint(spec)
            lock = state.get('hypothesis_lock') or {}
            if lock.get('id'):
                self.worker_claims.bind_hypothesis(self.worker_id, self.worker_claim_id,
                                                  self.worker_claim_epoch, lock['id'])
            path = self.cycle_dir / 'research_state.json'
            if state.get("operational_only_closure"):
                from theory_master.supervisor_input_support import capture_input_support_baseline

                capture_input_support_baseline(spec, state)
                (self.cycle_dir / "operational_closure.json").write_text(json.dumps(state["operational_only_closure"]))
            path.write_text(json.dumps(state))
            model = self.cycle_dir / 'scientific_model_out.json'
            model.write_text('{"version":0,"summary":"unchanged"}')
            snapshots.extend([(path, path.read_bytes()), (model, model.read_bytes())])
            visits.append({'cycle': cycle, 'claim_id': self.worker_claim_id,
                           'resume': resume_state, 'bound_h': lock.get('id')})
            return state
    monkeypatch.setattr(backends, 'CodexBackend', Backend)
    monkeypatch.setattr(workflow, 'Workflow', Workflow)
    monkeypatch.setattr(collector, 'Collector', MagicMock())
    return visits, snapshots


def run_claim(spec, root, worker='worker-1'):
    claims = CycleClaims(root, Path(spec.branch_canvas).with_name('science_graph.sqlite3'))
    claim = claims.claim_cycle(worker)
    code = wr.run_once(spec, worker, claim['claim_id'], claim['fencing_epoch'])
    # Stand in for the detached process exiting; no actual process is spawned.
    wr.save_worker(root, worker, pid=None, pid_start_ticks=None)
    return code, claims, claim


@pytest.mark.parametrize('reason', ['no_qualified_candidate', 'input_support_unavailable'])
def test_local_failure_finishes_old_claim_and_next_allocation_is_unbound(task, monkeypatch, reason):
    spec, root = task
    visits, snapshots = install_workflow(monkeypatch, spec, lambda c: local_state(reason, c))
    code, claims, old = run_claim(spec, root)
    assert code == 0 and claims.list_claims() == []
    assert claims.history()[-1]['status'] == 'finished'
    worker = wr.read_worker(root, 'worker-1')
    assert worker['status'] == 'OVER' and worker['claim_id'] is None and worker['epoch'] is None
    assert worker['last_claim_id'] == old['claim_id']
    assert worker['target_h_id'] is None and worker['entry_node_id'] is None
    assert worker['last_local_replan']['reason'] == reason
    launches = []
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda args, **kw: launches.append(args))
    outcome = wr.schedule_once(spec, resource_slots=1, preferred_hypothesis='H-0001',
                               eligible_worker_ids=frozenset({'worker-1'}))
    assert len(outcome['started']) == 1 and len(launches) == 1
    [new] = claims.list_claims()
    assert new['claim_id'] != old['claim_id'] and new['cycle_id'] is None and new['hypothesis_id'] is None
    assert '--resume-cycle' not in launches[0] and 'H-0001' not in launches[0]
    assert len(visits) == 1
    assert all(path.read_bytes() == data for path, data in snapshots)
    assert wr.schedule_once(spec, resource_slots=1)['started'] == []




def register_observations(spec):
    path = spec.task_home / "new_observations.csv"
    data = b"t,x\n0,1\n"
    path.write_bytes(data)
    (spec.task_home / "input_manifest.json").write_text(json.dumps({
        "schema": "theorymaster/public-research-input-manifest/v1",
        "data_files": [{"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }))

@pytest.mark.parametrize("withdraw_before_worker", [False, True])
def test_worker_waits_after_two_input_limited_cycles_then_starts_one_fresh_claim(task, monkeypatch, withdraw_before_worker):
    from theory_master.specification import TaskSpec
    from theory_master.supervisor_activation import request_stopped_takeover

    spec, root = task
    spec.max_cycles = 4
    monkeypatch.setattr(TaskSpec, "from_task_root", lambda root: spec)
    waits = []
    monkeypatch.setattr("theory_master.supervisor_activation._launch_worker_recovery",
                        lambda path: waits.append(path) or {"status": "worker_recovery_started"})
    visits, snapshots = install_workflow(monkeypatch, spec, lambda c: local_state("input_support_unavailable", c))
    assert run_claim(spec, root)[0] == 0
    assert wr.read_worker(root, "worker-1")["status"] == "OVER"
    code, claims, old = run_claim(spec, root)
    assert code == 0 and claims.list_claims() == []
    worker = wr.read_worker(root, "worker-1")
    assert worker["status"] == wr.WAITING_RESEARCH_DIRECTION
    assert worker["input_support_wait_cycle"] == "2" and worker["last_claim_id"] == old["claim_id"]
    assert request_stopped_takeover(spec)["status"] == "worker_recovery_started"
    assert waits == [root]

    launches = []

    def launch(argv, **_):
        launches.append(argv)
        if not withdraw_before_worker:
            return
        (spec.task_home / "new_observations.csv").unlink()
        claim = argv[argv.index("--claim-id") + 1]
        epoch = int(argv[argv.index("--epoch") + 1])
        assert wr.run_once(spec, "worker-1", claim, epoch) == 0
        assert len(visits) == 2

    monkeypatch.setattr("theory_master.worker_launch._launch_detached_service", launch)
    spec.document.write_text(spec.document.read_text() + "\nA new plan annotation is not input.")
    assert wr.schedule_once(spec, resource_slots=1)["started"] == []

    register_observations(spec)
    result = wr.schedule_once(spec, resource_slots=1, supervisor_recovery_only=True)
    assert len(result["started"]) == len(launches) == 1
    assert result["started"][0]["worker_id"] == "worker-1"
    assert "--resume-cycle" not in launches[0]
    if withdraw_before_worker:
        state = wr.read_worker(root, "worker-1")
        assert state["status"] == wr.WAITING_RESEARCH_DIRECTION and state["current_cycle"] == "2"
        assert not claims.list_claims()
    assert wr.schedule_once(spec, resource_slots=1)["started"] == []
    assert all(path.read_bytes() == data for path, data in snapshots)

@pytest.mark.parametrize('reason', ['no_qualified_candidate', 'input_support_unavailable'])
@pytest.mark.parametrize('guard', ['pause', 'interrupt', 'budget', 'protocol', 'runtime', 'abandoned'])
def test_independent_guards_keep_a_local_failure_out_of_new_allocations(task, monkeypatch, reason, guard):
    spec, root = task
    state = local_state(reason)
    if guard in {'pause', 'interrupt'}:
        state['recovery'] = {'action': guard}
    elif guard == 'budget':
        state['investigator_budget'] = {'exhausted': True}
    elif guard == 'protocol':
        state['protocol_health'] = 'violation'
    elif guard == 'runtime':
        state['recovery'] = {'action': 'error', 'kind': 'UnknownRuntimeFailure'}
    else:
        state['abandoned_cycle'] = True
    assert local_replan_reason(state) is None
    _, snapshots = install_workflow(monkeypatch, spec, state)
    code, claims, _ = run_claim(spec, root)
    worker = wr.read_worker(root, 'worker-1')
    if reason == 'input_support_unavailable':
        assert code == 2 and worker['status'] == 'NEEDS_ATTENTION'
        assert claims.list_claims()[0]['status'] == 'paused'
    else:
        assert code == 0 and worker['status'] == wr.WAITING_RESEARCH_DIRECTION
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda *a, **kw: pytest.fail('guarded allocation'))
    assert wr.schedule_once(spec, resource_slots=1,
                            eligible_worker_ids=frozenset({'worker-1'}))['started'] == []
    assert all(path.read_bytes() == data for path, data in snapshots)


def test_unknown_operational_closure_retains_claim_for_review(task, monkeypatch):
    spec, root = task
    state = local_state('input_support_unavailable')
    state['operational_only_closure']['kind'] = 'unknown_operational_failure'
    install_workflow(monkeypatch, spec, state)
    code, claims, _ = run_claim(spec, root)
    assert code == 2 and claims.list_claims()[0]['status'] == 'paused'
    assert wr.read_worker(root, 'worker-1')['status'] == 'NEEDS_ATTENTION'


@pytest.mark.parametrize('reason', ['no_qualified_candidate', 'input_support_unavailable'])
@pytest.mark.parametrize('previous_count', [0, 1])
def test_daemon_replans_with_new_claims_only_within_remaining_worker_quota(task, monkeypatch, reason, previous_count):
    from theory_master import specification, worker_supervisor_dispatch, budget_auto_preservation
    spec, root = task
    configure_mode(root, enabled=True, desired_agents=1)
    visits, snapshots = install_workflow(monkeypatch, spec, lambda c: local_state(reason, c))
    monkeypatch.setattr(specification.TaskSpec, 'from_task_root', classmethod(lambda cls, path: spec))
    monkeypatch.setattr(worker_supervisor_dispatch, 'dispatch_pending', lambda *_: False)
    monkeypatch.setattr(budget_auto_preservation, 'dispatch_auto_budget_preservation', lambda *_: False)
    monkeypatch.setattr('theory_master.worker_quota._repair_missing_quota_watchers', lambda *_: [])
    monkeypatch.setattr('theory_master.worker_quota._dispatch_exhausted_budget_reviewer_transport', lambda *_: False)
    # Only shorten the daemon poll. Real run_once heartbeat still blocks until
    # its stop event is set, preventing a synthetic busy heartbeat thread.
    original_wait = wr.threading.Event.wait
    monkeypatch.setattr(wr.threading.Event, 'wait', lambda self, n=None: False if n == 8 else original_wait(self, n))
    path = root / '.theory-master/worker_scheduler.json'
    original_waiting_state = None
    if previous_count:
        waiting_worker(root, local_state(reason), spec=spec)
        original_waiting_state = (root / '1/research_state.json').read_bytes()
        if reason == "input_support_unavailable":
            register_observations(spec)
        path.write_text(json.dumps({'status': 'STOPPED', 'quota_mode': 'per_worker', 'resume_batch': True,
            'max_cycles_per_worker': 2, 'cycles_started_by_worker': {'worker-1': previous_count}}))
    launches = []
    def launch(args, **kwargs):
        claim_id, epoch = args[args.index('--claim-id') + 1], int(args[args.index('--epoch') + 1])
        launches.append(args)
        assert '--resume-cycle' not in args
        assert wr.run_once(spec, 'worker-1', claim_id, epoch) == 0
        wr.save_worker(root, 'worker-1', pid=None, pid_start_ticks=None)
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', launch)
    assert wr.scheduler_daemon(spec, 1) == 0
    metadata = json.loads(path.read_text())
    assert metadata['stop_reason'] == 'cycle_budget_exhausted'
    assert metadata['cycles_started_by_worker'] == {'worker-1': 2}
    assert len(launches) == len(visits) == 2 - previous_count
    assert len({v['claim_id'] for v in visits}) == len(visits)
    assert len({v['cycle'] for v in visits}) == len(visits)
    assert CycleClaims(root).list_claims() == []
    assert len(CycleClaims(root).history()) == len(visits)
    assert all(path.read_bytes() == data for path, data in snapshots)
    if original_waiting_state is not None:
        assert (root / '1/research_state.json').read_bytes() == original_waiting_state
        assert visits[0]['cycle'] == '2'


def waiting_worker(root, state, *, spec=None):
    cycle = root / '1'
    cycle.mkdir()
    if spec is not None:
        (cycle / '.theory-master-cycle').write_text('theory-master/v2\ng\n')
        state['wake_fingerprint'] = wr.research_direction_fingerprint(spec)
        if state.get("operational_only_closure"):
            from theory_master.supervisor_input_support import capture_input_support_baseline

            capture_input_support_baseline(spec, state)
            (cycle / "operational_closure.json").write_text(json.dumps(state["operational_only_closure"]))
    (cycle / 'research_state.json').write_text(json.dumps(state))
    wr.save_worker(root, 'worker-1', status=wr.WAITING_RESEARCH_DIRECTION, current_cycle='1',
                   claim_id=None, epoch=None, pid=None)


def test_historical_canonical_waiting_attempt_wakes_for_one_fresh_claim(task, monkeypatch):
    spec, root = task
    waiting_worker(root, local_state('no_qualified_candidate'), spec=spec)
    before = (root / '1/research_state.json').read_bytes()
    launches = []
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda args, **kw: launches.append(args))
    result = wr.schedule_once(spec, resource_slots=1, eligible_worker_ids=frozenset({'worker-1'}))
    assert len(result['started']) == 1 and len(launches) == 1
    assert result['started'][0]['wake_reason'] == 'local_replan:no_qualified_candidate'
    assert '--resume-cycle' not in launches[0]
    assert wr.schedule_once(spec, resource_slots=1)['started'] == []
    assert (root / '1/research_state.json').read_bytes() == before


@pytest.mark.parametrize('case', ['pause', 'foreign_worker', 'foreign_cycle', 'symlink', 'invalid_json', 'oversize'])
def test_historical_waiting_replan_requires_safe_matching_unguarded_state(task, monkeypatch, case):
    spec, root = task
    state = local_state('no_qualified_candidate')
    if case == 'pause':
        state['recovery'] = {'action': 'pause'}
    elif case == 'foreign_worker':
        state['worker_id'] = 'worker-2'
    elif case == 'foreign_cycle':
        state['cycle_id'] = '2'
    waiting_worker(root, state)
    path = root / '1/research_state.json'
    if case == 'invalid_json':
        path.write_text('broken')
    elif case == 'oversize':
        path.write_bytes(b' ' * 1_048_577)
    elif case == 'symlink':
        target = root / 'original-state.json'
        path.rename(target)
        path.symlink_to(target)
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda *a, **kw: pytest.fail('invalid state replan'))
    assert wr.schedule_once(spec, resource_slots=1)['started'] == []


def test_goal_seal_stops_the_next_allocation_after_local_failure(task, monkeypatch):
    spec, root = task
    install_workflow(monkeypatch, spec, local_state('input_support_unavailable'))
    run_claim(spec, root)
    with ScienceGraphStore(Path(spec.branch_canvas).with_name('science_graph.sqlite3')) as store:
        store.seal_goal(goal_id='g', source_cycle='9', research_plan_sha256='0' * 64,
                        judge_receipt_ref=None, detail={'verdict': 'passed'})
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda *a, **kw: pytest.fail('sealed allocation'))
    assert wr.schedule_once(spec, resource_slots=1)['reason'] == 'master_complete'
