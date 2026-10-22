"""Clickable MODEL header, six profile previews, Worker-only guarded save."""
import json
from pathlib import Path
from unittest import mock

import pytest
from bs4 import BeautifulSoup

from tm_observer.model_preset_ui import dialog_html, script
from tm_observer.run_control import OperatorError, _validated_value, execute


def test_dialog_has_six_distinct_six_agent_model_effort_configs(monkeypatch):
    soup=BeautifulSoup(dialog_html(),"html.parser")
    assert soup.select_one("#tm-model-preset-dialog form")
    assert [o["value"] for o in soup.select("[data-model-preset-select] option")] == [
        "test","codex","advanced"]
    assert len(soup.select("[data-model-preset-panel]"))==3
    for panel in soup.select("[data-model-preset-panel]"):
        assert len(panel.select("tbody tr"))==6
        assert len(panel.select("tbody td"))==12


def test_preset_save_is_immediate_intent_but_never_interrupts_active_turn():
    soup = BeautifulSoup(dialog_html(), 'html.parser')
    button = soup.select_one('#tm-model-preset-dialog [data-model-preset-save]')
    assert button is not None and button.text.strip() == '保存'
    text = soup.select_one('#tm-model-preset-dialog p').get_text(' ', strip=True)
    assert '立即更新未在执行的 Agent' in text
    assert '正在执行的 Agent' in text
    assert '结束后再切换' in text
    assert '不必等待下一个 Cycle' in text
    source = script('a' * 16, 'worker-1')
    assert "action:'set_worker_profile'" in source
    assert '未运行的角色下一次调用使用新档位' in source
    assert '运行中的角色本次调用结束后使用新档位' in source
    assert '下一 Cycle 生效' not in source


def test_script_targets_one_worker_only_and_does_not_hot_swap_turns():
    source=script("8dd38a9977eef7e5","worker-2")
    assert "const selectedWorker=\"worker-2\"" in source
    assert "set_worker_profile" in source
    assert "expected_cycle" in source
    assert "expected_status" in source
    assert "/api/run/8dd38a9977eef7e5/action" in source
    assert "location.reload()" in source
    assert "当前 Worker" not in source  # no invented backend role editing path


@pytest.mark.parametrize("selected", ["test","codex","advanced"])
def test_guarded_whole_worker_profile_payload_allowed(selected):
    assert _validated_value("set_worker_profile", {
        "worker_id":"worker-2","profile":selected})["profile"]==selected


@pytest.mark.parametrize("payload",[
    {"worker_id":"worker-1","profile":"other"},
    {"worker_id":"../worker-1","profile":"test"},
    {"worker_id":"worker-1","profile":"codex","untrusted":"x"},
    {"worker_id":"worker-1"},
])
def test_invalid_profile_and_worker_rejected(payload):
    with pytest.raises(OperatorError):
        _validated_value("set_worker_profile",payload)


def test_exact_worker_identity_guard_and_sdk_save_route(tmp_path):
    root=tmp_path/"research"
    (root/".theory-master").mkdir(parents=True)
    run={"research_root":str(root), "run_id":1, "cycle":"999"}
    worker={"worker_id":"worker-2","status":"RUNNING","current_cycle":"137"}
    payload={"action":"set_worker_profile",
             "value":{"worker_id":"worker-2","profile":"codex"},
             "request_id":"e"*32,"expected_cycle":"137","expected_status":"RUNNING",
             "expected_launch_id":""}
    with mock.patch("tm_observer.worker_console.registered_workers", return_value=[worker]),\
         mock.patch("tm_observer.run_control._sdk_apply",return_value={
             "configured":{"profile":"codex","effective_at":"next_worker_cycle"}}) as applied:
        result=execute(run,payload)
        assert result["ok"] is True
        assert result["outcome"]["configured"]["profile"]=="codex"
        applied.assert_called_once()
        assert execute(run,payload).get("duplicate") is True
        wrong={**payload,"request_id":"d"*32,"expected_cycle":"136"}
        with pytest.raises(OperatorError,match="Cycle"):
            execute(run,wrong)


def test_full_console_contains_preset_modal_and_stable_delegated_handler(tmp_path):
    from tm_observer.run_views import run_detail_body
    root=tmp_path/'research'
    worker=root/'.theory-master/workers/worker-1'
    worker.mkdir(parents=True)
    (root/'.theory-master/worker_mode.json').write_text(json.dumps({
        'schema':'theory-master/worker-mode/v1','enabled':True,'desired_agents':1}))
    (worker/'runtime.json').write_text(json.dumps({
        'schema':'theory-master/worker-runtime/v1',
        'worker_id':'worker-1','current_cycle':'134',
        'status':'PAUSED'}))
    cycle=root/'134'
    cycle.mkdir()
    (cycle/'.theory-master-cycle').write_text('v2')
    (cycle/'run_state.json').write_text(json.dumps({'status':'PAUSED','phase':'provider_quota_exhausted'}))
    run={'observer_id':'a'*16,'research_root':str(root),'cycle_dir':str(cycle),
         'cycle':'134','task_name':'fixture','state':'PAUSED'}
    html=run_detail_body(run,worker_id='worker-1')
    soup=BeautifulSoup(html,'html.parser')
    assert len(soup.select('#tm-model-preset-dialog'))==1
    assert len(soup.select('[data-model-preset-panel]'))==3
    assert 'set_worker_profile' in html
    assert 'const selectedWorker="worker-1"' in html
    # Trigger lives inside 8-second refreshed host; modal lives outside it.
    assert soup.select_one('#tm-run-live #tm-model-preset-dialog') is None
    assert soup.select_one('#tm-model-preset-dialog [data-model-preset-save]')
