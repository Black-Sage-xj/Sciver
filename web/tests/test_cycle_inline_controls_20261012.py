"""Cycle card owns limit edit; State owns run/pause/resume/kill, no duplicate panel."""
import subprocess
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from tm_observer.run_views import _tui_summary, run_detail_body
from tm_observer.run_controls_ui import control_panel

_RUN = ("Process RUNNING   State RUNNING\n"
        "Cycle 1 / 40    Phase investigator:running  Elapsed 00:17\n"
        "Started 10-12T02:46:39  Cycle start 10-12T18:45:30  Cost —")


class CycleInlineEditorTests(unittest.TestCase):
    def test_clickable_edit_form_lives_inside_cycle_metric(self):
        panel = BeautifulSoup(_tui_summary(_RUN), "html.parser")
        cycle = next(x for x in panel.select(".tm-tui-kv") if x.dt.get_text(strip=True) == "Cycle")
        edit = cycle.select_one('[data-tui-op="max_cycles"]')
        self.assertIsNotNone(edit)
        self.assertEqual(edit.get_text(strip=True), "修改上限")
        self.assertEqual(edit["aria-expanded"], "false")
        form = cycle.select_one("form[data-cycle-max-form]")
        self.assertIsNotNone(form)
        self.assertIn("hidden", form.attrs)
        self.assertEqual(form.select_one('[data-cycle-max-input]')["value"], "40")
        self.assertEqual(form.select_one('[data-cycle-max-input]')["min"], "1")
        self.assertEqual(form.select_one('[data-cycle-max-input]')["max"], "1000000")
        self.assertIsNotNone(form.select_one("button[type=submit]"))
        self.assertIsNotNone(form.select_one("button[data-cycle-max-cancel]"))

    def test_no_duplicate_visible_control_panel_and_intact_confirm_controller(self):
        for status in ("RUNNING", "PAUSED", "CANCELLED"):
            with self.subTest(status=status):
                run={"observer_id":"a"*16,"cycle":"71","task_name":"Fixture","state":status}
                html=run_detail_body(run)
                soup=BeautifulSoup(html, "html.parser")
                self.assertIsNone(soup.select_one("#tm-tui-controls"))
                control=soup.select_one("#tm-operator[hidden]")
                self.assertIsNotNone(control)
                self.assertIsNone(control.select_one("button[data-operator-action=max_cycles]"))
                self.assertIsNone(control.select_one("#tm-operator-max"))
                for phrase in ("任务控制 · Run / Resume / Pause / Kill",
                               "操作会确认目标 Cycle 和 Runner；页面关闭不会停止计算。",
                               "运行开关在下方独立设置框管理"):
                    self.assertNotIn(phrase,html)
                self.assertIsNotNone(soup.select_one("#tm-operator-confirm-dialog"))
                self.assertIn("box.after(quickDialog,confirmDialog)",html)
                self.assertIn("'X-TM-Operator-Intent':'1'",html)
                self.assertIn("expected_cycle:box.dataset.cycle",html)
                self.assertIn("expected_status:box.dataset.status",html)
                self.assertIn("expected_launch_id:box.dataset.launch",html)
                self.assertIn("const confirmations=action==='kill'?2:1",html)
                self.assertIn("document.addEventListener('tm-cycle-max-save'",html)
                self.assertIn("Number.isSafeInteger(value)",html)
                self.assertIn("if(action!=='max_cycles')",html)
                self.assertIn("data-cycle-max-form",html)
                self.assertIn("data-cycle-max-current",html)

    def test_run_still_accessible_from_cancelled_state_card(self):
        summary=BeautifulSoup(_tui_summary(_RUN.replace("Process RUNNING   State RUNNING",
                                                         "Process STOPPED   State CANCELLED"),
                                            run_available=True),"html.parser")
        self.assertIsNotNone(summary.select_one('[data-tui-process-actions]'))
        with patch("tm_observer.run_controls_ui.run_monitor.data",
                   return_value={"status":"CANCELLED","cycle_id":"71"}):
            page=BeautifulSoup(control_panel({"observer_id":"a"*16,"cycle":"71",
                                              "state":"CANCELLED"}),"html.parser")
        self.assertEqual([x["data-tui-quick-action"] for x in
                          page.select("#tm-operator-quick-dialog [data-tui-quick-action]")],
                         ["run"])
        self.assertFalse(page.select_one('[data-operator-action="run"]').has_attr("disabled"))

    def test_js_syntax_all_page_scripts(self):
        html=run_detail_body({"observer_id":"a"*16,"cycle":"71",
                              "task_name":"Fixture","state":"RUNNING"})
        doc=BeautifulSoup(html,"html.parser")
        for index,tag in enumerate(doc.select("script")):
            if tag.get("src"): continue
            code=tag.string or ""
            if not code.strip():continue
            result=subprocess.run(["node","--check","--input-type=commonjs"],
                                  input=code,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,f"script {index}: {result.stderr}")


if __name__=="__main__":
    unittest.main()
