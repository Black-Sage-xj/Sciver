"""Supervisor refresh must never destroy a human draft or stop polling."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from tm_observer.supervisor_view import _refresh_script


class SupervisorSafePollingTests(unittest.TestCase):
    def test_refresh_is_recurring_and_defers_while_details_or_draft_is_open(self):
        script = _refresh_script(active=True, waiting=False)
        self.assertIn('const delay=8000;', script)
        self.assertIn('document.querySelector("details[open]")', script)
        self.assertIn('const hasDraft=', script)
        self.assertIn('const editing=', script)
        self.assertIn('location.reload();poll()', script)
        self.assertNotIn('if(!document.querySelector("details[open]"))location.reload();', script)
        self.assertEqual(_refresh_script(active=False, waiting=False), '')
        self.assertIn('const delay=3000;', _refresh_script(active=True, waiting=True))

    @unittest.skipUnless(shutil.which('node'), 'requires Node.js')
    def test_suppressed_refresh_reschedules_until_safe(self):
        script = re.sub(r'^<script>|</script>$', '', _refresh_script(active=True, waiting=False))
        harness = r"""
const assert = require('node:assert/strict');
let timer = null, reloads = 0, detailsOpen = true, editing = false, draft = '';
const composer = {shadowRoot:{activeElement:null,querySelector:()=>({get value(){return draft}})}};
const document = {
  visibilityState:'visible',
  querySelector:selector => selector==='details[open]' && detailsOpen ? {} : null,
  querySelectorAll:selector => selector==='loquix-chat-composer' ? [composer] : [],
  activeElement:{matches:()=>editing}
};
const location = {reload:()=>{reloads++}};
const setTimeout = (fn,delay)=>{timer={fn,delay};return 1};
eval(process.argv[1]);
assert.equal(timer.delay,8000);
timer.fn(); assert.equal(reloads,0); assert.equal(timer.delay,8000,'open details must reschedule polling');
detailsOpen=false; editing=true; timer.fn(); assert.equal(reloads,0,'focused composer must defer reload');
editing=false; draft='unsent human instruction'; timer.fn(); assert.equal(reloads,0,'unsent draft must defer reload');
draft=''; timer.fn(); assert.equal(reloads,1,'safe page may refresh');
"""
        result = subprocess.run(['node', '-e', harness, script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
