"""Black and navy Sciver workspace appearance remains a browser-only preference."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from tm_observer.dashboard_ui import STYLE, dashboard_shell
from tm_observer import task_settings_ui


class WorkspaceThemeTests(unittest.TestCase):
    def test_both_themes_cover_shared_workspace_surface(self):
        for palette, expected in (("black", "#09090b"), ("blue", "#07162f")):
            with self.subTest(palette=palette):
                self.assertIn('html[data-sciver-theme="' + palette + '"]', STYLE)
                self.assertIn("--tm-bg:" + expected, STYLE)
                self.assertIn('html[data-sciver-theme="' + palette + '"] .tm-nav-link', STYLE)
        self.assertIn("background:var(--tm-bg)", STYLE)
        self.assertIn("background:var(--tm-panel)", STYLE)

    def test_settings_includes_theme_for_workspace_and_selected_task(self):
        plain = task_settings_ui.workspace_settings_body([], None)
        self.assertIn('data-sciver-theme-picker', plain)
        self.assertIn('<option value="black">', plain)
        self.assertIn('<option value="blue">', plain)
        self.assertIn('仅保存在当前浏览器', plain)
        run = {"observer_id": "a" * 16, "task_name": "My Task"}
        with (
            patch.object(task_settings_ui.run_monitor, "data", return_value={"status": "PAUSED"}),
            patch.object(task_settings_ui, "_launch_id", return_value="launch-1"),
            patch.object(task_settings_ui, "settings_panel", return_value="<p>audit</p>"),
        ):
            selected = task_settings_ui.workspace_settings_body([run], run)
        self.assertEqual(selected.count('data-sciver-theme-picker'), 1)
        self.assertIn('data-settings-task="aaaaaaaaaaaaaaaa"', selected)
        self.assertIn("<p>audit</p>", selected)

    def test_bootstrap_precedes_css_in_every_shell(self):
        for page in ("settings", "services", "run", "workflow", "research_chat"):
            with self.subTest(page=page):
                html = dashboard_shell("Sciver", "<p>works</p>", active=page)
                dom = BeautifulSoup(html, "html.parser")
                self.assertIsNotNone(dom.select_one("script[data-sciver-theme-bootstrap]"))
                self.assertIsNotNone(dom.select_one("script[data-sciver-theme-controls]"))
                self.assertLess(html.index('data-sciver-theme-bootstrap'), html.index("<style>"))
                self.assertIn("sciver-theme-v1", html)

    def test_login_uses_same_first_paint_theme_bootstrap(self):
        from tm_observer.web import login_html
        login = login_html()
        normal = dashboard_shell("Sciver", "<p>works</p>", active="settings")
        marker = '<script data-sciver-theme-bootstrap>'
        for page in (login, normal):
            self.assertEqual(page.count(marker), 1)
            self.assertLess(page.index(marker), page.index("<style>"))
        script = lambda page: BeautifulSoup(page, "html.parser").select_one("script[data-sciver-theme-bootstrap]").text
        self.assertEqual(script(login), script(normal))

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for client-side preference test")
    def test_persisted_theme_switch_storage_sync_and_private_mode(self):
        dom = BeautifulSoup(dashboard_shell("Test", "<p>works</p>", active="settings"), "html.parser")
        bootstrap = dom.select_one("script[data-sciver-theme-bootstrap]").text
        controller = dom.select_one("script[data-sciver-theme-controls]").text
        js = r"""
const assert=require('assert'),vm=require('vm');
const bootstrap=process.argv[1], controller=process.argv[2];
function setup(saved,blocked=false) {
  const storage=new Map(saved===null?[]:[['sciver-theme-v1',saved]]);
  const attrs={},handlers={},picker={value:'',addEventListener:(type,fn)=>handlers['picker:'+type]=fn};
  const root={setAttribute:(k,v)=>attrs[k]=v,getAttribute:k=>attrs[k]};
  const document={documentElement:root,querySelector:q=>q.includes('data-sciver-theme-picker')?picker:null};
  const window={localStorage:{
      getItem(k){if(blocked)throw new Error('blocked');return storage.get(k)||null},
      setItem(k,v){if(blocked)throw new Error('blocked');storage.set(k,v)}
    },addEventListener:(type,fn)=>handlers['window:'+type]=fn};
  vm.runInNewContext(bootstrap,{document,window});
  vm.runInNewContext(controller,{document,window});
  return {attrs,picker,storage,handlers};
}
const blue=setup('blue');
assert.equal(blue.attrs['data-sciver-theme'],'blue');
assert.equal(blue.picker.value,'blue');
blue.picker.value='black';blue.handlers['picker:change']();
assert.equal(blue.attrs['data-sciver-theme'],'black');
assert.equal(blue.storage.get('sciver-theme-v1'),'black');
blue.handlers['window:storage']({key:'sciver-theme-v1',newValue:'blue'});
assert.equal(blue.attrs['data-sciver-theme'],'blue');
assert.equal(blue.picker.value,'blue');
blue.handlers['window:storage']({key:'sciver-theme-v1',newValue:'invalid'});
assert.equal(blue.attrs['data-sciver-theme'],'black');
for(const saved of [null,'invalid'])assert.equal(setup(saved).attrs['data-sciver-theme'],'black');
const privateMode=setup('blue',true);
assert.equal(privateMode.attrs['data-sciver-theme'],'black');
privateMode.picker.value='blue';privateMode.handlers['picker:change']();
assert.equal(privateMode.attrs['data-sciver-theme'],'blue');
"""
        result = subprocess.run(
            ["node", "-e", js, bootstrap, controller],
            capture_output=True, text=True, timeout=12,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
