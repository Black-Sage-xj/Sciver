"""Bound automatic qualification retries through real offline Workflow/Worker paths."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from theory_master import worker_runtime as wr
from theory_master.multi_worker import reserve_cycle_number
from theory_master.supervisor_activation import input_support_target
from theory_master.task_progression import qualification_replan_allowed
from theory_master.worker_direction import direction_changed, research_direction_fingerprint
from theory_master.workflow import Workflow
from test_task_progression_continuation_20261119 import (
    ProgressionBackend, _task, _cycles, _rows,
)
from test_worker_task_progression_20261119 import (
    task, install_workflow, local_state, run_claim,
)


def test_serial_repeated_qualification_stops_after_one_fresh_lead(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=6)
    backend = ProgressionBackend('qualification', fail_every_cycle=True)
    workflow = Workflow(spec, backend)
    stopped = workflow.run()
    assert [p.name for p in _cycles(spec)] == ['1', '2']
    assert workflow.invocation_cycle_index == 2 and stopped['status'] == 'no_qualified_candidate'
    before = {p: (p / 'research_state.json').read_bytes() for p in _cycles(spec)}
    # An explicit task Resume grants an attempt, not another unbounded batch.
    resumed = Workflow(spec, backend)
    resumed.resume(spec.master_id)
    assert [p.name for p in _cycles(spec)] == ['1', '2', '3']
    assert resumed.invocation_cycle_index == 1
    assert all((p / 'research_state.json').read_bytes() == raw for p, raw in before.items())
    assert not _rows(backend, cycle=4, role='research_lead', stage='frontier_proposal')


def test_repeated_formal_input_limit_waits_after_one_fresh_attempt(tmp_path, monkeypatch):
    spec = _task(tmp_path, monkeypatch, max_cycles=4)
    workflow = Workflow(spec, ProgressionBackend('input_limit', fail_every_cycle=True))
    stopped = workflow.run()
    assert [cycle.name for cycle in _cycles(spec)] == ['1', '2']
    assert workflow.invocation_cycle_index == 2
    assert stopped['scientific_verdict'] is None
    assert input_support_target(_cycles(spec)[-1], stopped)['route'] == 'input_support_wait'


def _stop(spec, *, fingerprint=None, worker='worker-1'):
    cycle = reserve_cycle_number(spec.research_root, 'fixture')
    state = local_state('no_qualified_candidate', cycle)
    state['worker_id'] = worker
    state['wake_fingerprint'] = (research_direction_fingerprint(spec) if fingerprint is None
                                 else fingerprint)
    path = spec.research_root / cycle / 'research_state.json'
    path.write_text(json.dumps(state))
    return state


def _declare_input(spec, *, name="measurements.csv", data=b"t,x\n0,1\n", restore=True):
    home = Path(getattr(spec, "task_home", None) or spec.document.parent)
    artifact = home / name
    manifest = home / "input_manifest.json"
    current = json.loads(manifest.read_text()) if manifest.is_file() else {
        "schema": "theorymaster/public-research-input-manifest/v1", "data_files": []}
    current["data_files"].append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest.write_text(json.dumps(current))
    if restore:
        artifact.write_bytes(data)
    return manifest, artifact


@pytest.mark.parametrize('change', ['plan', 'model', 'outcome', 'inputs'])
def test_real_direction_change_restores_one_bounded_opportunity(task, change):
    spec, root = task
    _stop(spec)
    second = _stop(spec)
    assert not qualification_replan_allowed(spec, second)
    if change == 'plan':
        spec.document.write_text('# Goal\nA new authorized research direction.')
    elif change == 'model':
        (root / 'scientific_model_state.json').write_text('{"version":1}')
    elif change == 'inputs':
        _declare_input(spec)
    else:
        from theory_master.science_graph_store import ScienceGraphStore
        with ScienceGraphStore(Path(spec.branch_canvas).with_name('science_graph.sqlite3')) as store:
            store.db.execute('INSERT INTO cycle_evaluations VALUES (?,?,?,?,?,?)',
                             ('outcome-1', 'H-0001', '9', 'open', '{}', '2026-11-20'))
    assert qualification_replan_allowed(spec, second)
    third = _stop(spec)
    assert qualification_replan_allowed(spec, third)
    fourth = _stop(spec)
    assert not qualification_replan_allowed(spec, fourth)


@pytest.mark.parametrize('bad', [None, {}, {'committed_outcome_count': 0},
                               {'committed_outcome_count': True, 'committed_outcome_ids_sha256': 'x',
                                'scientific_model_version': 0, 'research_plan_sha256': 'y'}])
def test_unknown_direction_cannot_grant_an_automatic_attempt(task, bad):
    spec, _ = task
    state = _stop(spec)
    state['wake_fingerprint'] = bad
    assert not qualification_replan_allowed(spec, state)
    assert not direction_changed(bad, research_direction_fingerprint(spec))


def test_new_input_field_does_not_fake_change_in_legacy_history(task):
    spec, _ = task
    old = research_direction_fingerprint(spec)
    old.pop('verified_research_input_ids')
    _stop(spec, fingerprint=old)
    current = _stop(spec)
    assert not direction_changed(old, current['wake_fingerprint'])
    assert not qualification_replan_allowed(spec, current)


@pytest.mark.parametrize('fault', ['symlink', 'oversized', 'malformed'])
def test_unsafe_history_never_grants_retries(task, fault):
    spec, root = task
    first = _stop(spec)
    second = _stop(spec)
    path = root / first['cycle_id'] / 'research_state.json'
    if fault == 'malformed':
        path.write_text('{broken')
    elif fault == 'oversized':
        path.write_bytes(b' ' * 2_000_001)
    else:
        original = path.with_suffix('.original')
        path.rename(original)
        path.symlink_to(original)
    assert not qualification_replan_allowed(spec, second)


def test_worker_wait_and_repeated_scheduler_ticks_do_not_reset_retry(task, monkeypatch):
    spec, root = task
    install_workflow(monkeypatch, spec, lambda cycle: local_state('no_qualified_candidate', cycle))
    assert run_claim(spec, root)[0] == 0
    assert wr.read_worker(root, 'worker-1')['status'] == 'OVER'
    assert run_claim(spec, root)[0] == 0
    waiting = wr.read_worker(root, 'worker-1')
    assert waiting['status'] == wr.WAITING_RESEARCH_DIRECTION
    assert waiting['wake_fingerprint']['research_inputs_sha256']
    launches = []
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service',
                        lambda args, **kw: launches.append(args))
    for _ in range(3):
        result = wr.schedule_once(spec, resource_slots=1,
                                  eligible_worker_ids=frozenset({'worker-1'}))
        assert result['started'] == []
    assert launches == []
    wr.worker_action(spec, 'worker-1', 'retry_direction')
    assert len(wr.schedule_once(spec, resource_slots=1)['started']) == 1
    retry = json.loads(wr._retry_direction_path(root, 'worker-1').read_text())
    assert retry['status'] == 'consumed'


@pytest.mark.parametrize("legacy", [False, True])
def test_restored_input_wakes_waiting_worker_with_unchanged_manifest(task, monkeypatch, legacy):
    spec, root = task
    data = b"x\n1\n"
    manifest, artifact = _declare_input(spec, data=data, restore=False)
    original = manifest.read_bytes()
    install_workflow(monkeypatch, spec, lambda cycle: local_state('no_qualified_candidate', cycle))
    run_claim(spec, root)
    run_claim(spec, root)
    if legacy:
        from theory_master.worker_store import save_worker
        previous = wr.read_worker(root, "worker-1")["wake_fingerprint"].copy()
        previous.pop("verified_research_input_ids")
        save_worker(root, "worker-1", wake_fingerprint=previous)
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda *a, **kw: None)
    assert wr.schedule_once(spec, resource_slots=1)['started'] == []
    artifact.write_bytes(data)
    assert manifest.read_bytes() == original
    result = wr.schedule_once(spec, resource_slots=1)
    assert len(result['started']) == 1
    assert result['started'][0]['wake_reason'] == 'direction_fingerprint_changed'


def test_only_small_canonical_manifests_supply_input_wake_signal(task):
    spec, _ = task
    before = research_direction_fingerprint(spec)
    (spec.document.parent / 'unregistered-output.json').write_text('{"result":1}')
    assert not direction_changed(before, research_direction_fingerprint(spec))
    manifest = Path(getattr(spec, "task_home", None) or spec.document.parent) / 'input_manifest.json'
    manifest.write_text('{"source":"new text, no registered evidence"}')
    assert not direction_changed(before, research_direction_fingerprint(spec))
    manifest.write_bytes(b' ' * 2_000_001)
    assert not direction_changed(before, research_direction_fingerprint(spec))


def test_manifest_mtime_and_unrelated_logs_are_not_new_inputs(task):
    spec, _ = task
    manifest, artifact = _declare_input(spec)
    before = research_direction_fingerprint(spec)
    os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "note": "just formatting"}, indent=2))
    (spec.document.parent / 'runtime.log').write_text('new heartbeat')
    assert not direction_changed(before, research_direction_fingerprint(spec))
    _declare_input(spec, name="mirror.csv")
    assert not direction_changed(before, research_direction_fingerprint(spec))
    artifact.unlink()
    (artifact.parent / "mirror.csv").write_text("not the declared bytes")
    assert not direction_changed(before, research_direction_fingerprint(spec))


def test_different_worker_archives_do_not_consume_own_fresh_lead(task):
    spec, _ = task
    _stop(spec, worker='worker-1')
    other = _stop(spec, worker='worker-2')
    assert qualification_replan_allowed(spec, other)
    repeated_other = _stop(spec, worker='worker-2')
    assert not qualification_replan_allowed(spec, repeated_other)


@pytest.mark.parametrize('change', ['active', 'locked_hypothesis', 'scientific_result'])
def test_nonempty_work_breaks_the_old_empty_qualification_episode(task, change):
    spec, root = task
    _stop(spec)
    work = _stop(spec)
    if change == 'active':
        work.update(status='active', phase='investigator:running', execution_active=True,
                    hypothesis_lock={'id':'H-0002', 'status':'locked'})
    elif change == 'locked_hypothesis':
        work.update(status='over', hypothesis_lock={'id':'H-0002', 'status':'locked'})
    else:
        work.update(status='over', scientific_verdict='open')
    (root / work['cycle_id'] / 'research_state.json').write_text(json.dumps(work))
    current = _stop(spec)
    assert qualification_replan_allowed(spec, current)


def test_legacy_unknown_worker_stays_dormant_until_explicit_retry(task, monkeypatch):
    from test_worker_task_progression_20261119 import waiting_worker
    spec, root = task
    waiting_worker(root, local_state('no_qualified_candidate'))
    monkeypatch.setattr('theory_master.worker_launch._launch_detached_service', lambda *a, **kw: None)
    assert wr.schedule_once(spec, resource_slots=1)['started'] == []
    wr.worker_action(spec, 'worker-1', 'retry_direction')
    assert len(wr.schedule_once(spec, resource_slots=1)['started']) == 1


@pytest.mark.parametrize('guard', ['active', 'locked_hypothesis', 'scientific_result', 'resource', 'pause'])
def test_current_nonqualification_or_guard_is_not_reclassified_as_an_empty_stop(task, guard):
    spec, _ = task
    current = _stop(spec)
    if guard == 'active':
        current['execution_active'] = True
    elif guard == 'locked_hypothesis':
        current['hypothesis_lock'] = {'status':'locked', 'id':'H-0001'}
    elif guard == 'scientific_result':
        current['scientific_verdict'] = 'open'
    elif guard == 'resource':
        current['cycle_budget'] = {'exhausted':True}
    else:
        current['recovery'] = {'action':'pause'}
    assert not qualification_replan_allowed(spec, current)
    assert not Workflow._can_continue_cycle_budget(current, spec=spec)


@pytest.mark.parametrize('guard', [None, 'active', 'locked_hypothesis', 'resource', 'protocol'])
def test_supervisor_recognizes_only_canonical_closed_qualification_as_terminal(task, guard):
    from theory_master.supervisor import _exit_disposition
    spec, root = task
    state = _stop(spec)
    if guard == 'active':
        state['execution_active'] = True
    elif guard == 'locked_hypothesis':
        state['hypothesis_lock'] = {'status':'locked', 'id':'H-0001'}
    elif guard == 'resource':
        state['cycle_budget'] = {'exhausted':True}
    elif guard == 'protocol':
        state['protocol_health'] = 'violation'
    (root / state['cycle_id'] / 'research_state.json').write_text(json.dumps(state))
    assert (_exit_disposition(root) == 'terminal') == (guard is None)
