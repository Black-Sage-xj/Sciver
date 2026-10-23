"""Task Settings: safe deep-link, scoped Audit editor, zero science side effects."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

from bs4 import BeautifulSoup

from tm_observer.run_views import run_detail_body
from tm_observer.task_settings_ui import task_settings_body, workspace_settings_body
from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class TaskSettingsTests(unittest.TestCase):
    def test_task_settings_is_single_audit_editor_not_run_cockpit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/'research'/'70'
            cycle.mkdir(parents=True)
            (cycle/'run_state.json').write_text(json.dumps({
                "cycle_id":"70","status":"RUNNING","cycle_audit_actions_enabled":False,
            }),encoding='utf-8')
            run={"observer_id":"a"*16,"run_id":1,"research_root":str(root/'research'),
                 "cycle_dir":str(cycle),"cycle":"70","task_name":"<task & title>",
                 "state":"RUNNING"}
            html=task_settings_body(run)
            dom=BeautifulSoup(html,"html.parser")
            self.assertIsNotNone(dom.select_one('.tm-task-settings-page'))
            self.assertEqual(dom.select_one('.tm-task-settings-back')['href'],
                             '/run/'+'a'*16+'/')
            self.assertIn('&lt;task &amp; title&gt;',html)
            self.assertEqual(len(dom.select('#tm-web-settings')),1)
            self.assertEqual(len(dom.select('input[data-setting-key]')),1)
            self.assertEqual(dom.select_one('input[data-setting-key]')['data-setting-key'],
                             'cycle_audit')
            self.assertTrue(dom.select_one('.tm-setting-gear-submenu').has_attr('open'))
            self.assertFalse(dom.select_one('[data-setting-detail="cycle_audit"]').has_attr('hidden'))
            self.assertEqual(len(dom.select('[data-setting-model="cycle_audit"]')),1)
            self.assertEqual(len(dom.select('[data-setting-save-model="cycle_audit"]')),1)
            self.assertEqual(dom.select_one('[data-settings-task]')['data-status'],'RUNNING')
            self.assertNotIn('data-setting-key="quota_auto_adjust"',html)
            self.assertIn("settings.addEventListener('change'",html)
            self.assertIn("settings.addEventListener('click'",html)
            self.assertIn('expected_launch_id:page.dataset.launch',html)
            self.assertIn("已配置",html)
            self.assertNotIn("window.location.reload()",html)

    def test_real_web_task_route_and_top_right_navigation(self):
        from test_observer import make_task
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            make_task(root,'settings-task',status='RUNNING',master_id=1)
            cache=StateCache(root,ttl_s=.2)
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(cache))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base=f'http://127.0.0.1:{server.server_address[1]}'
            try:
                runs=cache.get()['runs']
                self.assertEqual(len(runs),1)
                ident=runs[0]['observer_id']
                path=f'/run/{ident}/'
                current=BeautifulSoup(urlopen(base+path,timeout=7).read(),'html.parser')
                gear=current.select_one('.tm-tui-titlebar .tm-tui-title-actions > a.tm-run-settings-link')
                self.assertIsNotNone(gear)
                self.assertEqual(gear['href'],'/settings/?observer_id='+ident)
                self.assertEqual(gear['aria-label'],'运行设置')
                self.assertEqual(len(current.select('.tm-tui-title-actions .tm-tui-state-badge')),0)
                self.assertEqual(len(current.select('.tm-tui-title-actions .tm-run-settings-link')),1)
                self.assertFalse(current.select('.tm-quota-auto .tm-setting-gear'))
                self.assertEqual(len(current.select('.tm-quota-auto [data-setting-key]')),2)
                landing=BeautifulSoup(urlopen(base+'/settings/',timeout=7).read(),'html.parser')
                self.assertEqual([x.get_text(strip=True) for x in landing.select(
                    '.tm-breadcrumb .tm-breadcrumb-workspace, '
                    '.tm-breadcrumb strong[aria-current]')], ['Workspace','Settings'])
                self.assertEqual(len(landing.select('nav#tm-main-nav a.tm-nav-link')),8)
                self.assertEqual(
                    {a['href'] for a in landing.select('nav#tm-main-nav a.tm-nav-link')},
                    {'/research-chat/', '/run/', '/task-console/', '/research-plan/',
                     '/workflow-stage-preview/', '/architecture/', '/services/', '/settings/'},
                )
                self.assertEqual(landing.select_one('nav#tm-main-nav a[href="/task-console/"]').get_text(strip=True),
                                 'Task Console')
                self.assertEqual(landing.select_one('a.tm-nav-link[aria-current="page"]')['href'],
                                 '/settings/')
                self.assertIsNone(landing.select_one('[data-setting-key]'))
                self.assertIsNotNone(landing.select_one('.tm-settings-task-picker'))
                self.assertIn(ident, [x.get('value') for x in landing.select('select[name=observer_id] option')])
                response=urlopen(base+'/settings/?observer_id='+ident,timeout=7)
                self.assertEqual(response.status,200)
                settings=BeautifulSoup(response.read(),'html.parser')
                self.assertEqual(settings.select_one('a.tm-task-settings-back')['href'],path)
                self.assertEqual([x.get_text(strip=True) for x in settings.select(
                    '.tm-breadcrumb .tm-breadcrumb-workspace, '
                    '.tm-breadcrumb strong[aria-current]')], ['Workspace','Settings'])
                self.assertNotIn('Task Console',settings.select_one('.tm-breadcrumb').get_text())
                self.assertIn('自动审计',settings.get_text())
                self.assertIsNotNone(settings.select_one('[data-setting-save-model="cycle_audit"]'))
                self.assertEqual(len(settings.select('[data-setting-key]')),1)
                self.assertEqual(settings.select_one('select[name=observer_id] option[selected]')['value'],ident)
                self.assertIsNone(settings.select_one('.tm-tui-screen'))
                self.assertEqual(settings.select_one('a.tm-nav-link[aria-current="page"]')['href'],
                                 '/settings/')
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self,req,fp,code,msg,headers,newurl):
                        return None
                with self.assertRaises(HTTPError) as legacy:
                    build_opener(NoRedirect()).open(base+path+'settings/',timeout=7)
                self.assertEqual(legacy.exception.code,303)
                self.assertEqual(legacy.exception.headers['Location'],'/settings/?observer_id='+ident)
                for malformed in ('/run/'+ident+'/settings/extra/',
                                  '/settings/?observer_id='+'f'*16,
                                  '/settings/?observer_id=../escape'):
                    with self.subTest(malformed=malformed):
                        with self.assertRaises(HTTPError) as caught:
                            urlopen(base+malformed,timeout=7)
                        self.assertEqual(caught.exception.code,404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__=='__main__':
    unittest.main()
