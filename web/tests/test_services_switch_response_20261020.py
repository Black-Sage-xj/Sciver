"""Regression: Services switch never assumes an empty POST response means failure or success."""
from __future__ import annotations

import json
import subprocess
import unittest

from tm_observer.web import codex_usage_body


NODE_TEST = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = JSON.parse(process.argv[1]);
let actual = {active_account:'account-1', tier:2, provider:'codex'};
let mode = 'normal';
let posts = 0, gets = 0;
const message = {textContent:''};
const sandbox = {
  switchBusy:false, resetBusy:false, usageRequestId:0,
  selectedService:{...actual},
  Number, String, encodeURIComponent, console,
  document:{
    getElementById(id){ assert.equal(id,'switch-message'); return message; },
    querySelectorAll(){ return []; }
  },
  loadServiceSelection:async()=>{},
  loadUsage:async()=>{},
  fetch:async (url, options={})=>{
    if(url === '/services-selection.json') {
      gets++;
      if(mode === 'unverified') throw new Error('status unavailable');
      return {ok:true,json:async()=>({...actual})};
    }
    assert.equal(url,'/services-selection/switch');
    assert.equal(options.method,'POST');
    assert.equal(options.headers['X-TM-Operator-Intent'],'1');
    posts++;
    const target = new URLSearchParams(options.body).get('account');
    if(mode !== 'lost_no_switch' && mode !== 'unverified') {
      actual = {...actual,active_account:target,
        provider:'codex'};
    }
    if(mode !== 'normal') {
      return {ok:true,json:async()=>{ throw new SyntaxError('Unexpected end of JSON input'); }};
    }
    return {ok:true,json:async()=>({...actual})};
  }
};
vm.createContext(sandbox);
vm.runInContext(source,sandbox);
(async()=>{
  await sandbox.switchAccount('account-2');
  assert.equal(sandbox.selectedService.active_account,'account-2');
  assert.match(message.textContent,/已切换到 Codex 2/);

  mode='lost_after_switch';
  await sandbox.switchAccount('account-1');
  assert.equal(sandbox.selectedService.active_account,'account-1');
  assert.equal(sandbox.selectedService.tier,2);
  assert.match(message.textContent,/响应异常，但已核验当前账号为 Codex 1/);

  mode='lost_no_switch';
  await sandbox.switchAccount('account-2');
  assert.equal(sandbox.selectedService.active_account,'account-1');
  assert.match(message.textContent,/未确认切换到 Codex 2/);

  mode='unverified';
  await sandbox.switchAccount('account-1');
  assert.match(message.textContent,/切换结果不明/);
  assert.equal(posts,4,'must never retry a POST with unknown outcome');
  assert.equal(gets,3,'only error paths should perform reconciliation GET');
  console.log('PASS: success, lost reply after commit, failed switch, unreachable verification');
})().catch(err=>{console.error(err.stack||err);process.exitCode=1});
"""


class ServicesSwitchResponseTests(unittest.TestCase):
    def test_response_loss_does_not_replay_or_false_report_success(self):
        script = codex_usage_body().split('<script>', 1)[1].split('</script>', 1)[0]
        start = script.index('async function switchAccount(account) {')
        end = script.index('\nloadServiceSelection().finally', start)
        switch_function = script[start:end]
        result = subprocess.run(
            ['node', '-e', NODE_TEST, json.dumps(switch_function)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS: success, lost reply', result.stdout)


if __name__ == '__main__':
    unittest.main()
