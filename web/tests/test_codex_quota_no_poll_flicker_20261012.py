"""Keep Codex quota card DOM stable across 8-second scientific status polls."""
import subprocess
import unittest

from tm_observer.run_views import run_detail_body


class QuotaStableDuringRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=run_detail_body({"observer_id":"a"*16,"state":"RUNNING",
                                  "cycle":"70","task_name":"Synthetic"})
        cls.js=cls.html.split("<script>")[-1].split("</script>")[0]

    def test_header_refresh_never_replaces_quota_card_on_elapsed_change(self):
        js=self.js
        self.assertIn("function reconcileHeaderPreservingQuota(current,fresh)",js)
        self.assertIn("const oldQuota=oldGrid.querySelector('[data-codex-quota]')",js)
        self.assertIn("const newQuota=nextGrid.querySelector('[data-codex-quota]')",js)
        self.assertIn("if(oldCards[i]===oldQuota)continue;",js)
        self.assertIn("oldCards[i].replaceWith(newCards[i]);",js)
        self.assertIn("reconcileHeaderPreservingQuota(oldPanes[n],newPanes[n]))continue;",js)
        self.assertIn("if(!oldGrid||!nextGrid||oldGrid.children.length!==nextGrid.children.length)",js)
        self.assertIn("for(let i=0;i<Math.max(oldExtras.length,freshExtras.length);i++)",js)

    def test_same_quota_snapshot_does_not_mutate_dom_every_poll(self):
        js=self.js
        self.assertIn("if(account.textContent!==label)account.textContent=label",js)
        self.assertIn("if(value.textContent!==fiveText)value.textContent=fiveText",js)
        self.assertIn("if(week.textContent!==weekText)week.textContent=weekText",js)
        self.assertIn("if(fill.style.width!==width)fill.style.width=width",js)
        self.assertIn("progress.getAttribute?.('aria-valuenow')!==String(remaining)",js)

    def test_js_syntax_and_existing_eight_second_cycle_poll(self):
        self.assertIn("setInterval(loadCodexQuota,300000)",self.js)
        self.assertIn("setInterval(refresh,8000)",self.js)
        checked=subprocess.run(["node","--check","--input-type=commonjs"],input=self.js,
                               text=True,capture_output=True,timeout=15)
        self.assertEqual(checked.returncode,0,checked.stderr)
