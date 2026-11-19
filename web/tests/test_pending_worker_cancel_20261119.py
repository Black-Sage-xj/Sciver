"""Pending cancellation reconciles stale cards without touching live Workers."""
import json
import os
import threading
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from theory_master import operator_control
from theory_master.worker_mode import configure_mode, mode_path, request_one_worker
from tm_observer.worker_console import registered_workers, tab_bar


def setup_task(root, monkeypatch, *, workers=1):
    root.mkdir(exist_ok=True)
    configure_mode(root, enabled=True, desired_agents=1)
    for index in range(1, workers + 1):
        folder = root / f'.theory-master/workers/worker-{index}'
        folder.mkdir(parents=True)
        (folder / 'runtime.json').write_text(json.dumps({
            'schema': 'theory-master/worker-runtime/v1',
            'worker_id': f'worker-{index}', 'status': 'PAUSED', 'current_cycle': '1'}))
    monkeypatch.setattr(operator_control.TaskBinding, 'load', lambda _: SimpleNamespace(config_path=root / 'config.json'))
    monkeypatch.setattr(operator_control.TaskSpec, 'from_config', lambda _: SimpleNamespace(research_root=root, master_id=1))
    return {'observer_id': 'a' * 16, 'task_root': str(root), 'research_root': str(root),
            'run_id': 1, 'task_name': 'Pending cancellation', 'state': 'PAUSED'}


def cancel(run):
    return operator_control.dispatch({**run, 'action': 'cancel_pending_worker', 'value': None})


@pytest.mark.parametrize('workers', [0, 1, 2])
def test_cancel_real_request_then_stale_request_preserves_registry(tmp_path, monkeypatch, workers):
    run = setup_task(tmp_path, monkeypatch, workers=workers)
    request_one_worker(tmp_path, registered_count=workers)
    runtime_before = {p: p.read_bytes() for p in tmp_path.glob('.theory-master/workers/*/runtime.json')}
    assert cancel(run)['requested_agents'] == max(1, workers)
    mode_before = mode_path(tmp_path).read_bytes()
    assert cancel(run)['already_satisfied'] is True
    assert mode_path(tmp_path).read_bytes() == mode_before
    assert all(p.read_bytes() == contents for p, contents in runtime_before.items())
    page = BeautifulSoup(tab_bar(run, registered_workers(run), 'worker-1'), 'html.parser')
    assert not page.select('[data-worker-cancel]')
    assert len(page.select('.tm-worker-card')) == max(1, workers)


def test_slot_claimed_before_cancel_keeps_assigned_worker_and_capacity(tmp_path, monkeypatch):
    run = setup_task(tmp_path, monkeypatch, workers=2)
    path = mode_path(tmp_path)
    mode = json.loads(path.read_text())
    mode.update(desired_agents=2, requested_agents=2)
    path.write_text(json.dumps(mode))
    before = path.read_bytes()
    assert cancel(run)['already_satisfied'] is True
    assert path.read_bytes() == before
    assert len(registered_workers(run)) == 2


def test_provenance_history_does_not_hide_real_worker_as_pending(tmp_path, monkeypatch):
    run = setup_task(tmp_path, monkeypatch)
    runtime = tmp_path / '.theory-master/workers/worker-1/runtime.json'
    worker = json.loads(runtime.read_text())
    worker['runtime_source_history'] = [{'module_sha256': 'x' * 80000}]
    runtime.write_text(json.dumps(worker))
    request_one_worker(tmp_path, registered_count=1)
    assert cancel(run)['requested_agents'] == 1
    workers = registered_workers(run)
    assert [w['worker_id'] for w in workers] == ['worker-1']
    page = BeautifulSoup(tab_bar(run, workers, 'worker-1'), 'html.parser')
    assert not page.select('[data-worker-cancel]')
    assert page.select_one('.tm-worker-tab small').get_text() == 'PAUSED'


@pytest.mark.skipif(os.environ.get('TM_BROWSER_E2E') != '1', reason='requires opt-in Playwright Chromium')
def test_browser_stale_pending_card_cancel_reloads_successfully(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from tm_observer import run_control
    from tm_observer.web import ThreadingHTTPServer, make_handler

    run = setup_task(tmp_path, monkeypatch)
    request_one_worker(tmp_path, registered_count=1)
    cache = SimpleNamespace(search_root=tmp_path, get=lambda: {'runs': [run], 'git': {}})
    calls = []

    def sdk(target, action, value):
        calls.append(action)
        return operator_control.dispatch({**target, 'action': action, 'value': value})

    monkeypatch.setattr(run_control, '_sdk_apply', sdk)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cache))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            url = f'http://127.0.0.1:{server.server_address[1]}/run/{run["observer_id"]}/'
            page.goto(url)
            expect(page.locator('[data-worker-cancel]')).to_have_count(1)
            # Another tab already cancelled; this tab still has the old card.
            assert cancel(run)['requested_agents'] == 1
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/action')) as response:
                page.locator('[data-worker-cancel]').click()
            assert response.value.status == 200
            expect(page.locator('[data-worker-cancel]')).to_have_count(0)
            expect(page.locator('.tm-worker-card')).to_have_count(1)
            assert calls == ['cancel_pending_worker']
            receipts = list(tmp_path.glob('.theory-master/web-operator-receipts/*.json'))
            assert len(receipts) == 1
            receipt = json.loads(receipts[0].read_text())
            assert receipt['phase'] == 'applied'
            assert receipt['outcome']['already_satisfied'] is True
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
