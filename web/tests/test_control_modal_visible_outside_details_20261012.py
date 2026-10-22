"""Modal must leave hidden action adapter before display."""
import subprocess
import unittest
from bs4 import BeautifulSoup
from tm_observer.run_views import run_detail_body


class ModalVisibleOutsideDetailsTests(unittest.TestCase):
    def test_two_dialogs_are_relocated_by_controller_before_show_modal(self):
        html=run_detail_body({"observer_id":"a"*16,"state":"RUNNING",
                              "task_name":"Fixture","cycle":"1"})
        soup=BeautifulSoup(html,'html.parser')
        folded=soup.select_one('#tm-operator[hidden]')
        self.assertIsNotNone(folded)
        self.assertIsNone(soup.select_one('details#tm-tui-controls'))
        self.assertIsNotNone(folded.select_one('dialog#tm-operator-quick-dialog'))
        self.assertIsNotNone(folded.select_one('dialog#tm-operator-confirm-dialog'))
        # These are relocated to SIBLINGS at runtime, prior to any top-layer
        # showModal. A test that only checks dialog.open misses invisible UI.
        operator=soup.select_one("#tm-operator")
        script=operator.find_next_sibling('script').text
        self.assertNotIn("box.closest('details')",script)
        self.assertIn("box.after(quickDialog,confirmDialog)",script)
        self.assertIn("const dialog=confirmDialog",script)
        self.assertNotIn("box.querySelector('#tm-operator-confirm-dialog')",script)
        self.assertNotIn("box.querySelector('#tm-operator-quick-dialog')",script)
        result=subprocess.run(['node','--check'],input=script,text=True,capture_output=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_no_changes_to_operator_request_boundary(self):
        source=run_detail_body({"observer_id":"a"*16,"state":"RUNNING",
                                "task_name":"Fixture","cycle":"1"})
        self.assertIn("existing.click();",source)
        self.assertIn("'X-TM-Operator-Intent':'1'",source)
        self.assertIn("expected_cycle:box.dataset.cycle",source)
        self.assertIn("expected_status:box.dataset.status",source)
        self.assertIn("expected_launch_id:box.dataset.launch",source)
