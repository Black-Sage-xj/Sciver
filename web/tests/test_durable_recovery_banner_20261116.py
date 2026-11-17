"""Render real failure/recovery combinations without a browser or model."""
import copy
import json
import time

import pytest

from tm_observer.supervisor_view import banner_html


@pytest.fixture
def stopped_run(tmp_path):
    cycle = tmp_path / '1'
    (cycle / 'supervisor').mkdir(parents=True)
    failure = {'action': 'error', 'kind': 'NativeGoalRuntimeFailure', 'category': 'runtime',
               'at': '2026-11-16T16:01:48+08:00', 'message': 'Native Goal dependency blocked'}
    state = {'status': 'failed', 'cycle_id': '1', 'recovery': failure}
    job = {'schema': 'theory-master/supervisor-recovery/v1', 'cycle': '1', 'failure': failure,
           'status': 'waiting', 'blocking_dependency': 'Need reviewed rule <not HTML>',
           'dependency_wait': True, 'next_wakeup_epoch': time.time()+300,
           'last_verified_progress': None}
    (cycle / 'research_state.json').write_text(json.dumps(state))
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    (cycle / 'supervisor/status.json').write_text(json.dumps({
        'schema': 'theory-master/supervisor-status/v1', 'status': 'hold', 'code': 'run_failed'}))
    return cycle, state, job, {'research_root': str(tmp_path), 'cycle': '1', 'state': 'FAILED'}


@pytest.mark.parametrize('worker_id', [None, 'worker-1'])
def test_single_task_and_worker_show_dependency_wait_instead_of_stopped(stopped_run, worker_id):
    cycle, state, job, run = stopped_run
    state['worker_id'] = worker_id
    (cycle / 'research_state.json').write_text(json.dumps(state))
    html = banner_html(run, state)
    assert 'data-recovery-owner="true"' in html
    assert 'Supervisor 等待依赖变化' in html and '条件未变时不重复模型修复' in html
    assert '下次检查' in html and 'UTC+8' in html
    assert '尚无已验证恢复活动' in html and '&lt;not HTML&gt;' in html
    assert 'Supervisor 已停下' not in html


def test_stale_failure_receipt_does_not_mask_current_failure(stopped_run):
    cycle, state, job, run = stopped_run
    newer = copy.deepcopy(state)
    newer['recovery']['at'] = '2026-11-16T18:00:00+08:00'
    (cycle / 'research_state.json').write_text(json.dumps(newer))
    assert 'data-recovery-owner="true"' not in banner_html(run, newer)


def test_working_job_without_live_owner_is_not_shown_as_executing(stopped_run):
    cycle, state, job, run = stopped_run
    job.update(status='working', owner_pid=999999999, owner_start_ticks=1)
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    html = banner_html(run, state)
    assert '等待调度核查' in html and '诊断与修复中' not in html


def test_operator_pause_cannot_be_masked_by_old_recovery_wait(stopped_run):
    cycle, state, job, run = stopped_run
    state.update(status='paused', phase='paused', recovery={'action': 'pause'})
    (cycle / 'research_state.json').write_text(json.dumps(state))
    assert 'data-recovery-owner="true"' not in banner_html(run, state)


def test_expired_wait_time_does_not_promise_a_check_in_the_past(stopped_run):
    cycle, state, job, run = stopped_run
    job['next_wakeup_epoch'] = time.time()-300
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    html = banner_html(run, state)
    assert '已进入定期条件检查' in html
    assert '下次检查：' not in html


def test_live_recovering_worker_takes_precedence_over_frozen_failure(stopped_run):
    cycle, state, job, run = stopped_run
    state['worker_id'] = 'worker-1'
    (cycle / 'research_state.json').write_text(json.dumps(state))
    workers = cycle.parent / '.theory-master/workers/worker-1'
    workers.mkdir(parents=True)
    (workers / 'runtime.json').write_text(json.dumps({'current_cycle': '1', 'status': 'RECOVERING'}))
    assert 'data-recovery-owner="true"' not in banner_html(run, state)
