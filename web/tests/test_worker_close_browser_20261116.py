"""Chromium + real HTTP controls + deterministic temp Worker writes; no models."""
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master import worker_runtime as wr
from theory_master.multi_worker import CycleClaims, reserve_cycle_number
from theory_master.worker_mode import configure_mode
from theory_master.worker_close import watch_close
from tm_observer.web import ThreadingHTTPServer, make_handler


@pytest.mark.skipif(os.environ.get('TM_BROWSER_E2E') != '1', reason='requires opt-in Playwright Chromium')
def test_browser_close_cancel_error_retry_and_live_completion(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    root=tmp_path
    configure_mode(root, enabled=True, desired_agents=3)
    spec=SimpleNamespace(research_root=root, branch_canvas=root/'exploration.canvas')
    for index, status in ((1,'PAUSED'),(2,'PAUSED'),(3,'FAILED')):
        wid=f'worker-{index}'
        owned=CycleClaims(root).claim_cycle(wid)
        cycle=reserve_cycle_number(root,'g')
        CycleClaims(root).bind_cycle(wid, owned['claim_id'], owned['fencing_epoch'], cycle)
        state={'worker_id':wid,'status':status.lower(),'cycle_id':cycle}
        (root/cycle/'research_state.json').write_text(json.dumps(state))
        (root/cycle/'run_state.json').write_text(json.dumps({**state,'status':status}))
        wr.save_worker(root,wid,status=status,current_cycle=cycle,
                       claim_id=owned['claim_id'],epoch=owned['fencing_epoch'])
    run={'observer_id':'a'*16, 'task_name':'Close Agent browser fixture', 'run_id':1,
         'research_root':str(root),'task_root':str(root), 'cycle':'1',
         'cycle_dir':str(root/'1'),'state':'PAUSED'}
    cache=SimpleNamespace(search_root=root,get=lambda:{'runs':[run],'git':{}})
    calls=[]
    fail=[False]
    def sdk(target,action,value):
        from tm_observer.run_control import OperatorError
        assert target['research_root']==str(root) and action=='worker_control'
        calls.append(value)
        if fail[0]:
            raise OperatorError('fixture close rejected',409)
        return wr.worker_action(spec,value['worker_id'],value['operation'])
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(cache))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with patch('tm_observer.run_control._sdk_apply',side_effect=sdk), sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1100,'height':800})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_address[1]}/run/'+run['observer_id']+'/'
            page.goto(base+'?worker=worker-2')
            expect(page.locator('[data-worker-close="worker-1"]')).to_have_count(0)
            expect(page.locator('[data-worker-close="worker-2"]')).to_be_visible()
            expect(page.locator('[data-worker-close="worker-3"]')).to_be_visible()
            page.once('dialog',lambda dialog:dialog.dismiss())
            page.locator('[data-worker-close="worker-2"]').click()
            assert calls==[]
            fail[0]=True
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('[data-worker-close="worker-2"]').click()
            expect(page.locator('[data-worker-feedback]')).to_contain_text('fixture close rejected')
            expect(page.locator('[data-worker-close="worker-2"]')).to_be_enabled()
            fail[0]=False
            page.once('dialog',lambda dialog:dialog.accept())
            page.locator('[data-worker-close="worker-2"]').click()
            expect(page).to_have_url(base+'?worker=worker-1')
            expect(page.locator('[data-worker-close="worker-2"]')).to_have_count(0)
            assert (root/'2'/'research_state.json').exists()
            # Emulate the *test process* as a live Worker; the launcher is fake.
            # No signals, models, account selection or runtime daemon are used.
            from theory_master.runtime_manager import process_start_ticks
            wr.save_worker(root,'worker-3', status='RUNNING',pid=os.getpid(),
                           pid_start_ticks=process_start_ticks(os.getpid()))
            state_path=root/'3'/'research_state.json'
            state=json.loads(state_path.read_text());state['status']='active'
            state_path.write_text(json.dumps(state))
            page.reload()
            page.once('dialog',lambda dialog:dialog.accept())
            with patch.object(wr,'_launch_detached_service') as launch:
                page.locator('[data-worker-close="worker-3"]').click()
                expect(page.locator('[data-worker-feedback]')).to_contain_text('安全暂停并关闭')
                assert launch.call_count==1
            # Existing poll must replace disabled CLOSING cards on completion.
            page.reload()
            expect(page.locator('[data-worker-close="worker-3"]')).to_be_disabled()
            expect(page.locator('.tm-worker-list')).to_contain_text('关闭中')
            wr.save_worker(root,'worker-3',status='PAUSED',pid=None,pid_start_ticks=None)
            state['status']='paused';state_path.write_text(json.dumps(state))
            assert watch_close(spec,'worker-3')==0
            expect(page.locator('[data-worker-close="worker-3"]')).to_have_count(0,timeout=12000)
            assert (root/'3'/'research_state.json').exists()
            assert not errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
