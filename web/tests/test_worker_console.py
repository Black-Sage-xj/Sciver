"""Worker tabs must select their own Cycle and never leak Master controls."""
from __future__ import annotations

import json
from pathlib import Path

from tm_observer.worker_console import registered_workers, select_worker, tab_bar
from tm_observer.run_views import run_detail_body


def test_worker_tabs_and_exact_cycle_projection(tmp_path):
    root = tmp_path / 'research'
    folder = root / '.theory-master' / 'workers'
    for index in range(1, 6):
        worker = folder / f'worker-{index}'
        worker.mkdir(parents=True)
        (worker / 'runtime.json').write_text(json.dumps({
            'schema':'theory-master/worker-runtime/v1', 'worker_id': f'worker-{index}',
            'status': 'RUNNING' if index == 1 else 'PAUSED',
            'current_cycle': str(index), 'target_h_id': f'H-{index:04d}'
        }))
        cycle = root / str(index)
        cycle.mkdir()
        (cycle / '.theory-master-cycle').write_text('v2')
    (root / '.theory-master' / 'worker_mode.json').write_text(json.dumps({
        'schema':'theory-master/worker-mode/v1', 'enabled':True, 'desired_agents':5
    }))
    run = {'research_root':str(root), 'observer_id':'a'*16,
           'cycle_dir':str(root/'1'), 'cycle':'1', 'task_name':'Mock'}
    rows = registered_workers(run)
    assert len(rows) == 5
    selected, workers, worker_id = select_worker(run, 'worker-4')
    assert worker_id == 'worker-4'
    assert Path(selected['cycle_dir']).name == '4'
    assert selected['state'] == 'PAUSED'
    header = tab_bar(run, workers, worker_id)
    assert header.count('tm-worker-tab ') == 5
    assert 'Agent 4' in header and 'worker=worker-4' in header
    assert selected['worker_id'] == 'worker-4'
    assert run['cycle'] == '1'  # Task's previous projection was not changed
    (root / '.theory-master' / 'worker_mode.json').write_text(json.dumps({
        'schema':'theory-master/worker-mode/v1', 'enabled':False, 'desired_agents':5
    }))
    hidden, tabs, identity = select_worker(run, 'worker-4')
    assert hidden is run and tabs == [] and identity is None
    assert 'Agent 1' in tab_bar(run, tabs, identity)
    assert 'data-worker-add' in tab_bar(run, tabs, identity)
    assert 'tm-multi-bar' not in tab_bar(run, tabs, identity)


def test_orphaned_worker_is_never_marked_as_verified_running(tmp_path):
    from bs4 import BeautifulSoup

    root = tmp_path / 'research'
    control = root / '.theory-master'
    worker = control / 'workers' / 'worker-1'
    worker.mkdir(parents=True)
    (control / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True, 'desired_agents': 2}))
    # A stale RUNNING flag without a verifiable PID/start-ticks must stay ORPHANED.
    (worker / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'RUNNING', 'current_cycle': '183', 'target_h_id': 'H-0001'}))
    cycle = root / '183'
    cycle.mkdir()
    (cycle / '.theory-master-cycle').write_text('v2')
    (cycle / 'run_state.json').write_text(json.dumps({'status': 'RUNNING', 'cycle_index': 1}))
    run = {'observer_id': 'a'*16, 'research_root': str(root), 'cycle_dir': str(cycle),
           'cycle': '183', 'task_name': 'Mock', 'state': 'ORPHANED'}

    page = run_detail_body(run, worker_id='worker-1')
    soup = BeautifulSoup(page, 'html.parser')
    screen = soup.select_one('.tm-tui-screen')
    assert screen is not None and screen.get('data-tui-status') == 'ORPHANED'
    assert screen.get('data-runtime-verified') == 'false'
    assert '登记进程未通过身份校验' in page
    assert soup.select_one('[data-worker-id="worker-1"] [data-worker-action="release"]')
    assert not soup.select_one('[data-worker-id="worker-1"] [data-worker-action="resume"]')


def test_add_agent_request_renders_one_pending_card_per_click(tmp_path):
    from theory_master.worker_mode import request_one_worker, read_mode
    root = tmp_path / 'research'
    folder = root / '.theory-master' / 'workers' / 'worker-1'
    folder.mkdir(parents=True)
    (folder / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'RUNNING', 'current_cycle': '1'}))
    # An old max-count=2 setting is not a user click on +.
    (root / '.theory-master' / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True, 'desired_agents': 2}))
    run = {'research_root': str(root), 'observer_id': 'a'*16}
    rows = registered_workers(run)
    assert '待分配' not in tab_bar(run, rows, 'worker-1')
    updated = request_one_worker(root, registered_count=len(rows))
    assert updated['requested_agents'] == 2
    page = tab_bar(run, rows, 'worker-1')
    assert 'Agent 2<small>待分配</small>' in page
    assert page.count('data-worker-add') == 1
    assert request_one_worker(root, registered_count=len(rows))['requested_agents'] == 3
    assert 'Agent 3<small>待分配</small>' in tab_bar(run, rows, 'worker-1')
    assert read_mode(root)['desired_agents'] == 3


def test_over_worker_has_next_cycle_control_not_resume(tmp_path):
    from bs4 import BeautifulSoup
    import subprocess
    root = tmp_path / 'research'
    control = root / '.theory-master'
    worker = control / 'workers' / 'worker-1'
    worker.mkdir(parents=True)
    (control / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True, 'desired_agents': 2}))
    (worker / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'OVER', 'current_cycle': '126', 'target_h_id': 'H-0089'}))
    cycle = root / '126'
    cycle.mkdir()
    (cycle / '.theory-master-cycle').write_text('v2')
    (cycle / 'run_state.json').write_text(json.dumps({
        'status': 'OVER', 'cycle_index': 3, 'max_cycles': 100,
        'phase': 'complete', 'project_elapsed_s': 10,
        'started_at': '2026-10-18T00:00:00+00:00',
        'cycle_started_at': '2026-10-18T00:00:00+00:00'}))
    run = {'observer_id': 'a'*16, 'research_root': str(root),
           'cycle_dir': str(cycle), 'cycle': '126', 'task_name': 'Mock', 'state': 'OVER'}
    page = run_detail_body(run, worker_id='worker-1')
    soup = BeautifulSoup(page, 'html.parser')
    assert soup.select_one('[data-worker-id="worker-1"] [data-worker-action="run_next"]')
    assert not soup.select_one('[data-worker-id="worker-1"] [data-worker-action="resume"]')
    assert soup.select_one('#tm-start-progress-dialog') is not None
    screen = soup.select_one('.tm-tui-screen')
    assert screen is not None and screen.get('data-runtime-verified') == 'false'
    assert 'start_worker_scheduler' in page and 'data-worker-request-context' in page
    assert "['OVER','RELEASED','CANCELLED','STOPPED','FAILED_START'].includes(state)" in page
    assert "document.getElementById('tm-operator')||selectedWorker" in page
    for script in soup.select('script'):
        code = script.get_text()
        if not code.strip():
            continue
        result = subprocess.run(['node', '--check', '-'], input=code, text=True,
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr


def test_released_worker_state_exposes_next_cycle_scheduler_action(tmp_path):
    from bs4 import BeautifulSoup

    root = tmp_path / 'research'
    control = root / '.theory-master'
    worker = control / 'workers' / 'worker-1'
    worker.mkdir(parents=True)
    (control / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True, 'desired_agents': 2}))
    (worker / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'RELEASED', 'current_cycle': '126', 'target_h_id': 'H-0089'}))
    cycle = root / '126'
    cycle.mkdir()
    (cycle / '.theory-master-cycle').write_text('v2')
    (cycle / 'run_state.json').write_text(json.dumps({
        'status': 'OVER', 'cycle_index': 3, 'max_cycles': 100,
        'phase': 'complete', 'project_elapsed_s': 10,
        'started_at': '2026-10-18T00:00:00+00:00',
        'cycle_started_at': '2026-10-18T00:00:00+00:00'}))
    run = {'observer_id': 'a'*16, 'research_root': str(root),
           'cycle_dir': str(cycle), 'cycle': '126', 'task_name': 'Mock', 'state': 'OVER'}

    page = run_detail_body(run, worker_id='worker-1')
    soup = BeautifulSoup(page, 'html.parser')
    state_button = soup.select_one('.tm-tui-process[data-worker-state-open]')
    assert state_button is not None
    assert state_button.get_text(' ', strip=True).startswith('RELEASED')
    assert soup.select_one('[data-worker-id="worker-1"] [data-worker-action="run_next"]')
    assert soup.select_one('#tm-worker-state-dialog [data-worker-action="run_next"]')
    assert '当前 Worker 已释放前沿占用' in page
    assert 'start_worker_scheduler' in page


def test_worker_state_action_matrix_has_safe_fallback_for_lifecycle_states():
    from tm_observer.run_views import _worker_summary_controls

    expected = {
        'RUNNING': {'pause', 'kill'},
        'PAUSED': {'resume', 'release'},
        'FAILED': {'resume', 'release'},
        'NEEDS_ATTENTION': {'resume', 'release'},
        'INTERRUPTED': {'resume', 'release'},
        'ORPHANED': {'release'},
        'OVER': {'run_next'},
        'RELEASED': {'run_next'},
        'CANCELLED': {'run_next'},
        'STOPPED': {'run_next'},
        'FAILED_START': {'run_next'},
        'RESERVED': set(),
        'STARTING': set(),
        'PAUSE_REQUESTED': set(),
        'STOP_REQUESTED': set(),
        'COMPLETE': set(),
        'COMPLETED': set(),
        'FAILED_RECOVERY': set(),
        'UNKNOWN': set(),
        'UNRECOGNIZED_FUTURE_STATE': set(),
    }
    all_actions = {'pause', 'kill', 'resume', 'release', 'run_next'}
    for status, wanted in expected.items():
        rendered = _worker_summary_controls({'state': status, 'cycle': '126'}, 'worker-1')
        actual = {action for action in all_actions
                  if f'data-worker-action="{action}"' in rendered}
        assert actual == wanted, status
        if wanted:
            assert 'data-worker-refresh' not in rendered, status
        else:
            assert 'data-worker-refresh' in rendered, status


def test_independent_task_runner_does_not_appear_as_third_agent(tmp_path):
    """Live standalone Task and two attention Workers retain separate identities."""
    from bs4 import BeautifulSoup

    root = tmp_path / 'research'
    folder = root / '.theory-master' / 'workers'
    for index in (1, 2):
        directory = folder / f'worker-{index}'
        directory.mkdir(parents=True)
        (directory / 'runtime.json').write_text(json.dumps({
            'schema': 'theory-master/worker-runtime/v1',
            'worker_id': f'worker-{index}', 'status': 'NEEDS_ATTENTION',
            'current_cycle': str(184 + index),
        }))
    (root / '.theory-master' / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True,
        'desired_agents': 2, 'requested_agents': 2,
    }))
    run = {'research_root': str(root), 'observer_id': 'a'*16,
           'state': 'LIVE', 'pid_alive': True}
    selected, workers, identity = select_worker(run, None)
    assert selected is run and identity is None
    main = BeautifulSoup(tab_bar(run, workers, identity), 'html.parser')
    assert len(main.select('.tm-worker-card')) == 2
    assert [card.get_text(' ', strip=True).split(' NEEDS_ATTENTION')[0]
            for card in main.select('.tm-worker-card')] == ['Agent 1', 'Agent 2']
    assert main.select_one('.tm-task-runner-link') is not None
    assert not main.select_one('.tm-worker-current')
    assert not main.select_one('.tm-task-runner-link a')  # viewing standalone Task

    selected, workers, identity = select_worker(run, 'worker-1')
    assert identity == 'worker-1'
    historical = BeautifulSoup(tab_bar(run, workers, identity), 'html.parser')
    assert len(historical.select('.tm-worker-card')) == 2
    assert historical.select_one('.tm-task-runner-link a')['href'] == '/run/' + 'a'*16 + '/'
    assert historical.select_one('.tm-worker-card .is-selected') is not None


def test_unregistered_worker_cannot_introduce_arbitrary_paths(tmp_path):
    root=tmp_path / 'research'
    root.mkdir()
    run={'research_root':str(root),'observer_id':'b'*16}
    selected, workers, identity=select_worker(run,'../../other')
    assert workers == [] and identity is None and selected is run


def test_live_managed_task_is_not_masked_by_historical_released_worker(tmp_path):
    """Cycle 133: an independently running Task must not inherit RELEASED UI/actions."""
    from bs4 import BeautifulSoup

    root = tmp_path / 'research'
    meta = root / '.theory-master'
    worker = meta / 'workers' / 'worker-1'
    worker.mkdir(parents=True)
    (meta / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True,
        'desired_agents': 1, 'requested_agents': 1}))
    (worker / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'RELEASED', 'current_cycle': '133'}))
    cycle = root / '133'
    cycle.mkdir()
    (cycle / '.theory-master-cycle').write_text('v2')
    (cycle / 'run_state.json').write_text(json.dumps({
        'status': 'RUNNING', 'phase': 'investigator:running',
        'cycle_index': 1, 'max_cycles': 1, 'execution_active': True,
        'started_at': '2026-10-18T01:42:14+08:00',
        'cycle_started_at': '2026-10-18T01:42:14+08:00'}))
    (cycle / 'research_state.json').write_text(json.dumps({
        'status': 'active', 'phase': 'investigator:running',
        'execution_active': True}))
    run = {'observer_id': 'a'*16, 'research_root': str(root),
           'cycle_dir': str(cycle), 'cycle': '133', 'task_name': 'Mock',
           'state': 'LIVE', 'raw_status': 'RUNNING', 'pid_alive': True}

    selected, workers, identity = select_worker(run, None)
    assert identity is None and selected is run
    tabs = tab_bar(run, workers, identity)
    assert '当前运行<small>RUNNING</small>' not in tabs
    assert '独立 Task Runner · RUNNING（非 Agent）' in tabs
    assert 'Agent 1<small>RELEASED</small>' in tabs
    assert tabs.count('tm-worker-card') == 1

    page = run_detail_body(run)
    soup = BeautifulSoup(page, 'html.parser')
    state = soup.select_one('.tm-tui-process')
    assert state is not None and state.get_text(' ', strip=True).startswith('RUNNING')
    screen = soup.select_one('.tm-tui-screen')
    assert screen is not None and screen.get('data-runtime-verified') == 'true'
    assert soup.select_one('[data-tui-process-actions]')
    assert soup.select_one('#tm-operator')
    assert not soup.select_one('#tm-worker-state-dialog')

    historical = run_detail_body(run, worker_id='worker-1')
    old = BeautifulSoup(historical, 'html.parser')
    assert '旧 Worker 的历史状态' in historical
    assert '当前运行' in historical
    assert not old.select_one('[data-worker-action="run_next"]')
    assert not old.select_one('[data-tui-process-actions]')

    # A genuine active Worker continues to be selected, rather than being
    # concealed by a separately live-looking Task snapshot.
    (worker / 'runtime.json').write_text(json.dumps({
        'schema': 'theory-master/worker-runtime/v1', 'worker_id': 'worker-1',
        'status': 'RUNNING', 'current_cycle': '133'}))
    from tm_observer import worker_console
    from unittest.mock import patch
    with patch.object(worker_console, '_verified_active_worker', return_value=True):
        selected, workers, identity = select_worker(run, None)
    assert identity == 'worker-1' and selected['state'] == 'RUNNING'
