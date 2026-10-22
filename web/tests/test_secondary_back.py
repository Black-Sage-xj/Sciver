"""One non-sticky breadcrumb Back control across all Observatory subpages."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from bs4 import BeautifulSoup

from tm_observer.dashboard_ui import STYLE, dashboard_shell
from tm_observer.research_explorer_ui import explorer_html
from tm_observer.web import (
    StateCache, ThreadingHTTPServer, make_handler,
    stage_workflow_guide_html, stage_workflow_html,
)


IDENT = "8dd38a9977eef7e5"


class SecondaryBackNavigationTests(unittest.TestCase):
    def test_shared_shell_only_shows_back_on_nested_pages(self):
        for active in ("run", "explorer", "architecture", "services"):
            page = BeautifulSoup(dashboard_shell("Test", "<p>Body</p>", active=active), "html.parser")
            self.assertEqual(page.select_one(".tm-breadcrumb a.tm-secondary-back")["href"],
                             "/workflow-stage-preview/")
        workflow = BeautifulSoup(dashboard_shell("Test", "Body", active="workflow"), "html.parser")
        self.assertEqual(workflow.select("a.tm-secondary-back"), [])
        tasks = BeautifulSoup(dashboard_shell("Test", "Hi", active="run",
                                             back_href="/run/"), "html.parser")
        back = tasks.select_one(".tm-topbar .tm-breadcrumb > a.tm-secondary-back")
        self.assertIsNotNone(back)
        self.assertEqual(back["href"], "/run/")
        self.assertEqual(back["aria-label"], "返回Task List")
        self.assertIn("返回", back.get_text())
        self.assertEqual(len(back.select("svg path")), 1)
        self.assertEqual(len(tasks.select(".tm-secondary-back")), 1)
        self.assertIsNotNone(tasks.select_one("button[data-sidebar-toggle]"))
        self.assertEqual(back.find_parent("nav")["aria-label"], "页面导航")
        self.assertIn(".tm-topbar { position:relative;", STYLE)
        self.assertNotIn(".tm-secondary-backbar { position:sticky;", STYLE)
        self.assertIn(".tm-secondary-back:focus-visible", STYLE)

    def test_tui_subview_back_to_current_task_not_run_list(self):
        href=f"/run/{IDENT}/"
        dom = BeautifulSoup(dashboard_shell("Detail", "Task", active="run",
                                             back_href=href), "html.parser")
        link=dom.select_one("a.tm-secondary-back")
        self.assertEqual(link["href"], href)
        self.assertEqual(link["aria-label"], "返回任务概览")
        self.assertEqual(len(dom.select(".tm-secondary-back")), 1)

    def test_full_clickable_workspace_run_console_explore_hierarchy(self):
        def labels_and_links(html):
            dom = BeautifulSoup(html, "html.parser")
            crumb = dom.select_one("nav.tm-breadcrumb")
            nodes = crumb.select("button.tm-breadcrumb-workspace, a.tm-breadcrumb-link, strong[aria-current='page']")
            labels = [node.get_text(strip=True) for node in nodes]
            hrefs = [node.get("href") for node in nodes]
            self.assertEqual(len(crumb.select("strong[aria-current='page']")), 1)
            self.assertNotIn("Research Explorer", crumb.get_text())
            self.assertIsNotNone(crumb.select_one("button.tm-breadcrumb-workspace[data-workspace-nav]"))
            self.assertIsNone(crumb.select_one("a[href='/']"))
            return labels, hrefs
        overview = dashboard_shell("Run", "Body", active="run")
        self.assertEqual(labels_and_links(overview),
                         (["Workspace", "Task List"], [None, None]))
        self.assertEqual(labels_and_links(dashboard_shell(
            "Task", "Body", active="run", back_href="/run/")),
            (["Workspace", "Task List", "Task Console"], [None, "/run/", None]))
        task_href = f"/run/{IDENT}/"
        self.assertEqual(labels_and_links(dashboard_shell(
            "Task events", "Body", active="run", back_href=task_href,
            breadcrumb_leaf="Events")),
            (["Workspace", "Task List", "Task Console", "Events"],
             [None, "/run/", task_href, None]))
        explorer = dashboard_shell(
            "Task Explore map", "Body", active="explorer", back_href=task_href)
        self.assertEqual(labels_and_links(explorer),
                         (["Workspace", "Task List", "Task Console", "Explore map"],
                          [None, "/run/", task_href, None]))
        nav = BeautifulSoup(explorer, "html.parser").select_one("nav#tm-main-nav")
        self.assertGreaterEqual(len(nav.select("a")), 6)  # navigation categories can expand
        self.assertIsNone(nav.select_one('a[href="/research-explorer/"]'))
        self.assertEqual(nav.select_one('a[aria-current="page"]')["href"], task_href)
        self.assertEqual(nav.select_one('a[aria-current="page"]').get_text(strip=True), "Task Console")
        console = BeautifulSoup(dashboard_shell(
            "Task", "Body", active="run", back_href="/run/"), "html.parser")
        side = console.select_one("nav#tm-main-nav")
        self.assertEqual(side.select_one('a[aria-current="page"]')["href"], "/")
        self.assertEqual(side.select_one('a[aria-current="page"]').get_text(strip=True), "Task Console")
        self.assertIsNone(side.select_one('a[href="/run/"][aria-current]'))
        self.assertEqual(labels_and_links(dashboard_shell(
            "Guide", "Body", active="workflow",
            back_href="/workflow-stage-preview/", breadcrumb_leaf="Guide")),
            (["Workspace", "Workflow", "Guide"],
             [None, "/workflow-stage-preview/", None]))
        self.assertEqual(labels_and_links(dashboard_shell(
            "Codex Usage", "Body", active="services", back_href="/services/")),
            (["Workspace", "Services", "Codex Usage"],
             [None, "/services/", None]))

    def test_unknown_untrusted_parent_is_never_rendered(self):
        for bad in ("javascript:alert(1)", "https://example.com",
                    "//evil.test", "/run/not-id/", "/run/"+IDENT+"/?evil=1",
                    '/run/'+IDENT+'/" onclick="alert(1)'):
            page=BeautifulSoup(dashboard_shell("Test", "Body", active="run", back_href=bad),
                               "html.parser")
            self.assertEqual(page.select("a.tm-secondary-back"), [])

    def test_explorer_uses_task_parent_only_if_user_supplies_real_source(self):
        state={"runs":[{"observer_id":IDENT, "run_id":1, "task_name":"Research A"}]}
        with patch("tm_observer.research_explorer_ui.vault_root", return_value=Path("/tmp")):
            from_run=BeautifulSoup(explorer_html(Path("/tmp"),state,selected=IDENT),"html.parser")
            self.assertEqual(from_run.select_one(".tm-topbar a.tm-secondary-back")["href"],
                             f"/run/{IDENT}/")
            self.assertEqual(len(from_run.select(".tm-secondary-back")),1)
            general=BeautifulSoup(explorer_html(Path("/tmp"),state),"html.parser")
            self.assertEqual(general.select_one(".tm-secondary-back")["href"],
                             "/workflow-stage-preview/")
            unknown=BeautifulSoup(explorer_html(Path("/tmp"),state,selected="evil"),"html.parser")
            self.assertEqual(unknown.select_one(".tm-secondary-back")["href"],
                             "/workflow-stage-preview/")

    def test_services_back_uses_last_task_console_cookie(self):
        from urllib.request import Request
        from tool.tm_observer.tests.test_observer import make_task
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_task(root, "nav-task", status="RUNNING", master_id=1)
            cache = StateCache(root, ttl_s=.2)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cache))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                bare = BeautifulSoup(urlopen(base + "/services/", timeout=7).read(), "html.parser")
                self.assertEqual(bare.select_one(".tm-secondary-back")["href"],
                                 "/workflow-stage-preview/")
                index = BeautifulSoup(urlopen(base + "/run/", timeout=7).read(), "html.parser")
                task = index.select_one(".tm-run-task-link")
                self.assertIsNotNone(task)
                ident = task["href"].strip("/").split("/")[1]
                request = Request(base + "/services/",
                                  headers={"Cookie": "tm_last_task_console=" + ident})
                page = BeautifulSoup(urlopen(request, timeout=7).read(), "html.parser")
                link = page.select_one(".tm-breadcrumb a.tm-secondary-back")
                self.assertEqual(link["href"], task["href"])
                self.assertEqual(link["aria-label"], "返回任务概览")
                stale = Request(base + "/services/",
                                headers={"Cookie": "tm_last_task_console=0123456789abcdef"})
                gone = BeautifulSoup(urlopen(stale, timeout=7).read(), "html.parser")
                self.assertEqual(gone.select_one(".tm-secondary-back")["href"],
                                 "/workflow-stage-preview/")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_workflow_guide_and_version_have_parent_but_overview_not(self):
        overview=BeautifulSoup(stage_workflow_html(),"html.parser")
        self.assertEqual(overview.select(".tm-secondary-back"),[])
        full=BeautifulSoup(stage_workflow_html(display_mode="full"),"html.parser")
        link=full.select_one(".tm-breadcrumb a.tm-secondary-back")
        self.assertIsNotNone(link)
        self.assertEqual(link["href"],"/workflow-stage-preview/")
        guide=BeautifulSoup(stage_workflow_guide_html(),"html.parser")
        link=guide.select_one(".tm-breadcrumb a.tm-secondary-back")
        self.assertIsNotNone(link)
        self.assertEqual(link["href"],"/workflow-stage-preview/")

    def test_http_run_route_parent_is_correct_for_nested_research_events(self):
        # Integration: test routing rather than only independently calling shell.
        from tool.tm_observer.tests.test_observer import make_task
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            make_task(root, "nav-task", status="RUNNING", master_id=1)
            cache=StateCache(root,ttl_s=.2)
            server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(cache))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base=f"http://127.0.0.1:{server.server_address[1]}"
            try:
                index=BeautifulSoup(urlopen(base+"/run/",timeout=7).read(),"html.parser")
                self.assertEqual(index.select_one(".tm-breadcrumb a.tm-secondary-back")["href"],
                                 "/workflow-stage-preview/")
                task=index.select_one(".tm-run-task-link")
                self.assertIsNotNone(task)
                path=task["href"]
                html=urlopen(base+path,timeout=7).read().decode()
                dom=BeautifulSoup(html,"html.parser")
                self.assertEqual(dom.select_one(".tm-secondary-back")["href"],"/run/")
                self.assertIsNone(dom.select_one(".tm-tui-detach"))
                self.assertIsNotNone(dom.select_one(".tm-tui-screen"))
                for tab in ("research","events"):
                    page=BeautifulSoup(urlopen(base+path+"?tab="+tab,timeout=7).read(),"html.parser")
                    self.assertEqual(page.select_one(".tm-secondary-back")["href"],path)
                    self.assertEqual(len(page.select(".tm-secondary-back")),1)
                view=urlopen(base+f"/api/run/{path.strip('/').split('/')[1]}/view?tab=research",
                             timeout=7).read().decode()
                self.assertNotIn('class="tm-secondary-back"',view)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__=="__main__":
    unittest.main()
