"""Guard task preset requests, including the browser's actual launch token."""
import json
import shutil
import subprocess
from uuid import uuid4
from unittest.mock import patch

import pytest

from tm_observer.model_preset_ui import script
from tm_observer.run_control import execute, OperatorError


def test_task_profile_requires_current_launch_and_checks_replay_value(tmp_path):
    guard = tmp_path / '.theory-master'
    guard.mkdir()
    (guard / 'runtime.json').write_text(json.dumps({'launch_id': 'live-launch'}))
    run = {'research_root': str(tmp_path), 'run_id': 31}
    request = {'action': 'set_task_profile', 'value': {'profile': 'codex'},
               'request_id': uuid4().hex, 'expected_cycle': '2',
               'expected_status': 'PAUSED', 'expected_launch_id': 'live-launch'}
    with patch('tm_observer.run_monitor.data', return_value={'cycle_id': '2', 'status': 'PAUSED'}), \
         patch('tm_observer.worker_console.registered_workers', return_value=[]), \
         patch('tm_observer.run_control._sdk_apply', return_value={'configured': {'profile': 'codex'}}) as apply:
        with pytest.raises(OperatorError, match='Runner'):
            execute(run, {**request, 'expected_launch_id': ''})
        apply.assert_not_called()
        assert execute(run, request)['ok']
        assert execute(run, request)['duplicate']
        apply.assert_called_once()
        with pytest.raises(OperatorError, match='request_id'):
            execute(run, {**request, 'value': {'profile': 'test'}})
        with pytest.raises(OperatorError, match='Cycle'):
            execute(run, {**request, 'request_id': uuid4().hex, 'expected_cycle': '1'})
    with patch('tm_observer.worker_console.registered_workers', return_value=[{'worker_id': 'worker-1'}]):
        with pytest.raises(OperatorError, match='Agent'):
            execute(run, {**request, 'request_id': uuid4().hex})


@pytest.mark.parametrize('worker', [None, 'worker-2'])
@pytest.mark.parametrize('operation', ['save', 'catalog'])
def test_browser_save_targets_task_or_worker_with_correct_generation(worker, operation):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the browser handler regression')
    source = script('a' * 16, worker).removeprefix('<script>').removesuffix('</script>')
    harness = r'''
const listeners = {};
const button = {disabled:false, textContent:'保存', addEventListener:(e,f)=>listeners.save=f};
const catalogButton = {disabled:false, addEventListener:(e,f)=>listeners.catalog=f};
const empty = {hidden:true, addEventListener:()=>{}, querySelectorAll:()=>[]};
const choice = {value:'codex', addEventListener:()=>{}};
const message = {textContent:''};
const context = {dataset:{cycle:'2',status:'PAUSED',launch:'live-launch'}};
const dialog = {querySelector:s=>s==='[data-model-preset-select]'?choice:
  s==='[data-model-preset-save]'?button:s==='[data-model-preset-result]'?message:
  s==='[data-model-catalog-check]'?catalogButton:empty,
  querySelectorAll:()=>[], addEventListener:()=>{}, close:()=>{}};
const live = {querySelector:()=>context, addEventListener:()=>{}};
global.document = {getElementById:id=>id==='tm-model-preset-dialog'?dialog:live};
global.location = {reload:()=>{}};
let request;
global.fetch = async (url, opts)=>{request=JSON.parse(opts.body);return {ok:true,json:async()=>({ok:true,outcome:{account:"fixture",reason:"mock"}})};};
'''
    harness += source
    harness += ('\nlisteners.save({currentTarget:button})' if operation == 'save' else '\nlisteners.catalog()')
    harness += '.then(()=>console.log(JSON.stringify(request)));'
    done = subprocess.run([node, '-e', harness], text=True, capture_output=True, check=True)
    request = json.loads(done.stdout)
    if operation == 'save':
        assert request['action'] == ('set_worker_profile' if worker else 'set_task_profile')
        assert request['value'] == ({'worker_id': worker, 'profile': 'codex'} if worker else {'profile': 'codex'})
    else:
        assert request['action'] == 'inspect_codex_models'
        assert request['value'] == {'worker_id': worker}
    assert request['expected_launch_id'] == ('' if worker else 'live-launch')
    assert request['expected_cycle'] == '2'
    assert request['expected_status'] == 'PAUSED'
