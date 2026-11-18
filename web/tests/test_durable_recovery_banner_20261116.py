"""Render real failure/recovery combinations without a browser or model."""
import copy
import json
import time
from unittest.mock import patch

import pytest

from tm_observer.supervisor_view import banner_html, index_chip


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
    assert index_chip({**run, 'state': 'RECOVERING'}) == ''


def test_index_recovery_wait_uses_current_dependency_instead_of_historical_hold(stopped_run):
    _, _, _, run = stopped_run
    chip = index_chip({**run, 'state': 'RECOVERY_WAIT', 'raw_status': 'FAILED'})
    assert 'data-recovery-owner="true"' in chip
    assert 'data-recovery-status="waiting"' in chip
    assert 'Supervisor 等待依赖变化' in chip
    assert 'Supervisor 已停下' not in chip


@pytest.mark.parametrize(('status', 'label'), [
    ('pending', 'Supervisor 等待诊断'),
    ('working', 'Supervisor 等待调度核查'),
    ('waiting', 'Supervisor 等待下一次检查'),
    ('resume_authorized', 'Supervisor 等待受控恢复'),
    ('activity_verified', 'Supervisor 已验证恢复活动'),
    ('stopped', 'Supervisor 已停止'),
])
def test_index_and_banner_share_recovery_status_labels(stopped_run, status, label):
    cycle, state, job, run = stopped_run
    job.update(status=status, dependency_wait=False, owner_pid=999999999, owner_start_ticks=1)
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    assert label in index_chip(run)
    assert f'<strong>{label}</strong>' in banner_html(run, state)


def test_index_working_requires_verified_live_owner(stopped_run):
    cycle, state, job, run = stopped_run
    job.update(status='working', owner_pid=123, owner_start_ticks=456)
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    with patch('tm_observer.collector._pid_alive', return_value=True) as alive:
        assert 'Supervisor 诊断与修复中' in index_chip(run)
        assert 'Supervisor 诊断与修复中' in banner_html(run, state)
    alive.assert_called_with(123, 456)


def test_index_stale_recovery_keeps_legacy_hold(stopped_run):
    cycle, state, _, run = stopped_run
    state['recovery']['at'] = '2026-11-16T18:00:00+08:00'
    (cycle / 'research_state.json').write_text(json.dumps(state))
    chip = index_chip(run)
    assert 'data-recovery-owner="true"' not in chip
    assert 'Supervisor 已停下' in chip


def test_contract_delivery_attention_matches_sdk_failure_details(stopped_run):
    cycle, state, job, run = stopped_run
    state.update(status='needs_attention', phase='judge:terminal_assessment:contract_repair_failed',
                 recovery={'action': 'contract_repair_failed', 'at': '2026-11-16T16:01:48+08:00',
                           'role': 'judge', 'stage': 'terminal_assessment'},
                 attention={'code': 'contract_repair_failed',
                            'reason': 'The judge output still violated its contract.'})
    job['failure'] = {**state['recovery'], 'kind': 'RuntimeError', 'category': 'runtime',
                      'message': state['attention']['reason']}
    (cycle / 'research_state.json').write_text(json.dumps(state))
    (cycle / 'supervisor/recovery.json').write_text(json.dumps(job))
    run.update(state='RECOVERY_WAIT', raw_status='NEEDS_ATTENTION')
    assert 'Supervisor 等待依赖变化' in index_chip(run)
    assert 'data-recovery-owner="true"' in banner_html(run, state)
    state['attention']['reason'] = 'A different contract failure.'
    (cycle / 'research_state.json').write_text(json.dumps(state))
    assert 'data-recovery-owner="true"' not in index_chip(run)
    state['attention'].update(code='qualification_failed', reason=job['failure']['message'])
    (cycle / 'research_state.json').write_text(json.dumps(state))
    assert 'data-recovery-owner="true"' not in index_chip(run)
