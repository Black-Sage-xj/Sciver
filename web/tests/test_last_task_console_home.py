"""Browser-scoped last Task Console landing and safe home redirect."""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from bs4 import BeautifulSoup

from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler, _remembered_console, _console_landing


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LastTaskConsoleHomeTests(unittest.TestCase):
    def test_cookie_parser_only_accepts_current_discovered_task(self):
        first="0123456789abcdef"
        second="abcdef0123456789"
        state={"runs":[{"observer_id":first},{"observer_id":second}]}
        self.assertEqual(_remembered_console("tm_last_task_console="+first,state),
                         "/run/"+first+"/")
        self.assertEqual(_remembered_console(
            "session_cookie=some-value; tm_last_task_console="+second,state),
            "/run/"+second+"/")
        for cookie in (
            None, "", "tm_last_task_console=ffffffffffffffff",
            "tm_last_task_console=../../evil",
            "tm_last_task_console=https://evil.test/",
            "tm_last_task_console=0123456789ABCDEF",
            "tm_last_task_console=0123456789abcdef%0D",
            "tm_last_task_console="+first+"-other",
            "tm_last_task_console="+("z"*20000),
        ):
            with self.subTest(cookie=str(cookie)[:48]):
                self.assertIsNone(_remembered_console(cookie,state))

    def test_console_landing_favors_verified_live_over_historical_cookie(self):
        paused='0123456789abcdef'
        live='abcdef0123456789'
        other='1111111111111111'
        state={'runs':[
            {'observer_id':paused, 'state':'NEEDS_ATTENTION'},
            {'observer_id':live, 'state':'LIVE'},
            {'observer_id':other, 'state':'OVER'},
        ]}
        stale_cookie='tm_last_task_console='+paused
        self.assertEqual(_console_landing(stale_cookie,state),'/run/'+live+'/')
        self.assertEqual(_console_landing(None,state),'/run/'+live+'/')
        self.assertEqual(_console_landing('tm_last_task_console=../../evil',state),'/run/'+live+'/')
        # Multiple verified live tasks require an explicit choice unless the
        # remembered task is itself one of the live tasks.
        state['runs'][2]['state']='LIVE'
        self.assertEqual(_console_landing(stale_cookie,state),'/run/?status=LIVE')
        self.assertEqual(_console_landing(None,state),'/run/?status=LIVE')
        self.assertEqual(_console_landing('tm_last_task_console='+live,state),'/run/'+live+'/')
        state['runs'][1]['state']='OVER'
        state['runs'][2]['state']='PAUSED'
        self.assertEqual(_console_landing(stale_cookie,state),'/run/'+paused+'/')
        self.assertEqual(_console_landing(None,state),'/run/')
        # Invalid or unverified task IDs can never be used as a redirect.
        state['runs'][1]={'observer_id':'../../evil','state':'LIVE'}
        self.assertEqual(_console_landing(None,state),'/run/')

    def test_recent_orphaned_task_beats_ten_day_old_cookie(self):
        paused='0123456789abcdef'
        current='abcdef0123456789'
        now=datetime.now(timezone.utc)
        state={'runs':[
            {'observer_id':paused, 'state':'NEEDS_ATTENTION',
             'last_activity_at':(now-timedelta(days=10)).isoformat()},
            {'observer_id':current, 'state':'ORPHANED',
             'last_activity_at':now.isoformat()},
        ]}
        cookie='tm_last_task_console='+paused
        self.assertEqual(_console_landing(cookie,state),'/run/'+current+'/')
        self.assertEqual(_console_landing(None,state),'/run/'+current+'/')
        # When two tasks have recent activity, choose explicitly instead of
        # silently guessing which ORPHANED worker is the desired task.
        state['runs'][0]['last_activity_at']=now.isoformat()
        self.assertEqual(_console_landing(None,state),'/run/')
        self.assertEqual(_console_landing(cookie,state),'/run/'+paused+'/')
        state['runs'][0]['last_activity_at']=(now-timedelta(days=10)).isoformat()
        state['runs'][1]['last_activity_at']=(now-timedelta(hours=2)).isoformat()
        self.assertEqual(_console_landing(cookie,state),'/run/'+paused+'/')
        self.assertEqual(_console_landing(None,state),'/run/')

    def test_home_returns_last_browser_task_else_task_list_without_redirect_loop(self):
        from test_observer import make_task
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            make_task(root,"home-task-alpha",status="RUNNING",master_id=1)
            make_task(root,"home-task-beta",status="PAUSED",master_id=2)
            cache=StateCache(root,ttl_s=.2)
            state=cache.get()
            ids=[row["observer_id"] for row in state["runs"]]
            self.assertEqual(len(ids),2)
            server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(cache))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base=f"http://127.0.0.1:{server.server_address[1]}"
            no_redirect=build_opener(_NoRedirect())
            def landing(cookie=None):
                headers={"Cookie":cookie} if cookie else {}
                with self.assertRaises(HTTPError) as caught:
                    no_redirect.open(Request(base+"/",headers=headers),timeout=7)
                err=caught.exception
                self.assertEqual(err.code,303)
                self.assertEqual(err.headers["Cache-Control"],"no-store")
                return err.headers.get("Location")
            try:
                self.assertEqual(landing(),"/run/")
                for index,ident in enumerate(ids):
                    href="/run/"+ident+"/"
                    with urlopen(base+href,timeout=7) as response:
                        self.assertEqual(response.status,200)
                        body=response.read().decode()
                        cookie=response.headers.get("Set-Cookie")
                    self.assertIsNotNone(cookie)
                    self.assertIn("tm_last_task_console="+ident,cookie)
                    for attr in ("Path=/","HttpOnly","Secure","SameSite=Lax",
                                 "Max-Age=7776000"):
                        self.assertIn(attr,cookie)
                    page=BeautifulSoup(body,"html.parser")
                    self.assertEqual(page.select_one(".tm-tui-eyebrow").get_text(),
                                     "TASK CONSOLE")
                    self.assertEqual([node.get_text(strip=True) for node in page.select(
                        ".tm-breadcrumb .tm-breadcrumb-workspace, "
                        ".tm-breadcrumb .tm-breadcrumb-link, "
                        ".tm-breadcrumb strong[aria-current]")],
                                     ["Workspace","Task List","Task Console"])
                    self.assertIsNone(page.select_one(".tm-breadcrumb a[href='/']"))
                    self.assertEqual(landing(cookie.split(";",1)[0]),href)
                    if index == 0:
                        first_cookie=cookie.split(";",1)[0]
                # Different browser still remembers its own last task.
                self.assertEqual(landing(first_cookie),"/run/"+ids[0]+"/")
                self.assertEqual(landing("tm_last_task_console=ffffffffffffffff"),"/run/")
                self.assertEqual(landing("tm_last_task_console=javascript:alert(1)"),"/run/")
                # The root route must override the paused task's cookie if a
                # verified LIVE task exists, without changing other routes.
                current_id=ids[0]
                paused_id=ids[1]
                live_state={'runs':[{**row, 'state':('LIVE' if row['observer_id']==current_id
                                                      else 'NEEDS_ATTENTION')}
                                    for row in state['runs']]}
                with patch.object(cache,'get',return_value=live_state):
                    self.assertEqual(landing('tm_last_task_console='+paused_id),
                                     '/run/'+current_id+'/')
                    self.assertEqual(landing(),'/run/'+current_id+'/')
                with urlopen(base+"/run/",timeout=7) as response:
                    list_body=response.read().decode()
                    self.assertIsNone(response.headers.get("Set-Cookie"))
                self.assertIn("Task List",list_body)
                self.assertEqual(landing(first_cookie),"/run/"+ids[0]+"/")
                with urlopen(base+"/workflow-stage-preview/",timeout=7) as response:
                    self.assertIsNone(response.headers.get("Set-Cookie"))
                    self.assertIn("Workflow",response.read().decode())
                self.assertEqual(landing(first_cookie),"/run/"+ids[0]+"/")
                with self.assertRaises(HTTPError) as missing:
                    urlopen(base+"/run/ffffffffffffffff/",timeout=7)
                self.assertEqual(missing.exception.code,404)
                self.assertIsNone(missing.exception.headers.get("Set-Cookie"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__=="__main__":
    unittest.main()
