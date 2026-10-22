"""One-click add-Agent belongs to Agent cards, never a top settings bar."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup

from tm_observer.worker_settings_ui import mode_snapshot, top_bar, script
from tm_observer.worker_console import select_worker
from tm_observer.task_settings_ui import task_settings_body
from tm_observer.run_views import run_detail_body
from tm_observer.run_control import _validated_value, OperatorError


def test_default_off_minimal_console_bar_and_no_settings_controls(tmp_path):
    root = tmp_path / 'task'
    root.mkdir()
    run = {'observer_id': 'a'*16, 'research_root': str(root), 'task_name':'Experiment'}
    assert mode_snapshot(run) == {'enabled':False, 'desired_agents':2}
    settings = task_settings_body(run)
    assert 'data-multi-enabled' not in settings
    assert 'set_worker_mode' not in settings
    html = run_detail_body(run)
    dom = BeautifulSoup(html, 'html.parser')
    assert not dom.select('.tm-multi-bar')
    assert dom.select_one('[data-worker-request-context]').has_attr('hidden')
    assert len(dom.select('[data-worker-add]')) == 1
    assert dom.select_one('.tm-worker-tabs') is not None
    assert html.index('class="tm-tui-titlebar"') < html.index('class="tm-worker-tabs"')
    assert select_worker(run, 'worker-1') == (run, [], None)
    (root/'.theory-master').mkdir()
    (root/'.theory-master'/'worker_mode.json').write_text(json.dumps({
        'schema':'theory-master/worker-mode/v1', 'enabled':True,'desired_agents':7}))
    assert 'data-multi-enabled' not in task_settings_body(run)
    dom = BeautifulSoup(run_detail_body(run), 'html.parser')
    assert not dom.select('[data-multi-enabled]')
    assert not dom.select('[data-multi-desired]')
    assert len(dom.select('[data-worker-add]')) == 1


def test_action_allowlist_checks_task_count_and_no_forged_extra_data():
    good = {'enabled':True,'desired_agents':12}
    assert _validated_value('set_worker_mode', good) == good
    for invalid in ({'enabled':1,'desired_agents':2}, {'enabled':True,'desired_agents':0},
                    {'enabled':True,'desired_agents':1025}, {'enabled':True,'desired_agents':2,'path':'/tmp/x'}):
        try:
            _validated_value('set_worker_mode', invalid)
        except OperatorError:
            pass
        else:
            raise AssertionError('invalid Worker mode bypassed API validation')
    assert _validated_value('start_worker_scheduler', None) is None
    assert _validated_value('stop_worker_scheduler', None) is None
    assert _validated_value('add_worker', None) is None
    try:
        _validated_value('add_worker', {'desired_agents': 999})
    except OperatorError:
        pass
    else:
        raise AssertionError('add_worker must not accept forged parameters')


def test_console_script_syntax_and_scoped_endpoint():
    rendered = script('a'*16)
    dom = BeautifulSoup(rendered,'html.parser')
    body = dom.select_one('script').get_text()
    assert "'/api/run/'" in body and "'add_worker'" in body
    assert "'cancel_pending_worker'" in body and "'worker_control'" in body
    assert 'data-worker-add' in body and 'expected_launch_id' in body
    parsed = subprocess.run(['node','--check','-'],input=body,text=True,capture_output=True,timeout=5)
    assert parsed.returncode == 0, parsed.stderr


def test_top_bar_separates_enabled_mode_from_active_scheduler(tmp_path):
    root = tmp_path / 'task'
    control = root / '.theory-master'
    control.mkdir(parents=True)
    (control / 'worker_mode.json').write_text(json.dumps({
        'schema': 'theory-master/worker-mode/v1', 'enabled': True,
        'desired_agents': 2
    }))
    run = {'observer_id': 'a'*16, 'research_root': str(root), 'task_name': 'Mock'}
    assert 'data-worker-request-context' in top_bar(run)
    assert 'tm-multi-bar' not in top_bar(run)
    assert mode_snapshot(run)['desired_agents'] == 2
