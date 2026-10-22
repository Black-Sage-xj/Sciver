"""Run Process RUNNING action menu is UI-only until explicit confirmed click."""
import subprocess
import unittest

from bs4 import BeautifulSoup

from tm_observer.run_views import _tui_summary, run_detail_body


_RUNNING = ("Process RUNNING   State RUNNING\n"
            "Cycle 4 / 30    Phase investigator:running  Elapsed 30:50\n"
            "Started 10-11T16:26:08  Cycle start 10-11T16:26:08  Cost —")


class ProcessQuickControlsTests(unittest.TestCase):
    def test_running_badge_opens_standalone_dialog_with_pause_and_kill(self):
        soup = BeautifulSoup(_tui_summary(_RUNNING), "html.parser")
        trigger = soup.select_one('button.tm-tui-process-green[data-tui-process-actions]')
        self.assertIsNotNone(trigger)
        self.assertIn("RUNNING", trigger.get_text(" ", strip=True))
        self.assertIsNone(soup.select_one(".tm-process-action-menu"))
        from tm_observer.run_controls_ui import control_panel
        dialog = BeautifulSoup(control_panel({"observer_id":"a"*16,"cycle":"4",
                                              "state":"RUNNING"}),"html.parser")
        quick=dialog.select_one('#tm-operator-quick-dialog')
        self.assertIsNotNone(quick)
        self.assertNotIn("open",quick.attrs)
        self.assertEqual([b["data-tui-quick-action"] for b in quick.select("button[data-tui-quick-action]")],
                         ["pause","kill"])
        self.assertFalse(quick.select('[data-tui-quick-action="abandon_cycle"]'))
        confirm=dialog.select_one('#tm-operator-confirm-dialog')
        self.assertIsNotNone(confirm)
        self.assertIsNotNone(confirm.select_one("#tm-operator-confirm-ack"))

    def test_stopped_and_failed_process_both_remain_readonly(self):
        for process in ("STOPPED", "FAILED", "IDLE", "WAITING", "RECOVERING"):
            with self.subTest(process=process):
                soup = BeautifulSoup(_tui_summary(_RUNNING.replace(
                    "Process RUNNING", f"Process {process}")), "html.parser")
                self.assertIsNone(soup.select_one("[data-tui-process-actions]"))
                self.assertIsNotNone(soup.select_one(".tm-tui-process"))

    def test_click_delegates_to_existing_confirmed_operator_not_new_api(self):
        page = run_detail_body({"observer_id": "a" * 16,
                                "task_name": "Test", "cycle": "4",
                                "state": "RUNNING"})
        self.assertIn("existing.click();", page)
        self.assertIn("if(existing&&!existing.disabled)", page)
        self.assertIn("if(menu?.open)menu.close();", page)
        self.assertIn("dialog.showModal();", page)
        self.assertIn('data-tui-quick-action="kill"',page)
        self.assertNotIn('data-tui-quick-action="abandon_cycle"',page)
        script = page.split("<script>")[-1].split("</script>",1)[0]
        result = subprocess.run(["node", "--check", "--input-type=commonjs"],
                                input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
