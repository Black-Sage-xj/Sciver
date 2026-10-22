from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tm_observer import codex_usage


class CodexUsageAccountSwitchTests(unittest.TestCase):
    def test_switch_changes_only_auth_identity_and_preserves_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / ".codex-accounts"
            account1 = root / "account-1"
            account2 = root / "account-2"
            account1.mkdir(parents=True)
            account2.mkdir(parents=True)
            (account1 / "auth.json").write_text("one-refreshed", encoding="utf-8")
            (account2 / "auth.json").write_text("two", encoding="utf-8")
            selector = root / "current"
            selector.symlink_to("account-1", target_is_directory=True)

            daily = base / ".codex"
            tm = base / ".codex-theory-master"
            for runtime in (daily, tm):
                (runtime / "skills").mkdir(parents=True)
                (runtime / "sessions").mkdir()
                (runtime / "config.toml").write_text("shared\n", encoding="utf-8")
                (runtime / "sessions" / "old-thread.jsonl").write_text("rollout\n", encoding="utf-8")
            # Simulate Codex atomically replacing the daily auth symlink during refresh.
            (daily / "auth.json").write_text("one-refreshed", encoding="utf-8")
            (tm / "auth.json").symlink_to(selector / "auth.json")

            accounts = (("Codex 1", account1), ("Codex 2", account2))
            with mock.patch.object(codex_usage, "DEFAULT_ACCOUNTS", accounts):
                selected = codex_usage.switch_active_account(
                    "account-2", selector=selector, runtime_homes=(daily, tm), restart_app=False
                )

            self.assertEqual(selected, "account-2")
            self.assertEqual(selector.readlink(), Path("account-2"))
            self.assertTrue((daily / "auth.json").is_symlink())
            self.assertTrue((tm / "auth.json").is_symlink())
            self.assertEqual((daily / "auth.json").resolve(), (account2 / "auth.json").resolve())
            self.assertEqual((tm / "auth.json").resolve(), (account2 / "auth.json").resolve())
            self.assertEqual((account1 / "auth.json").read_text(encoding="utf-8"), "one-refreshed")
            for runtime in (daily, tm):
                self.assertEqual((runtime / "config.toml").read_text(encoding="utf-8"), "shared\n")
                self.assertEqual(
                    (runtime / "sessions" / "old-thread.jsonl").read_text(encoding="utf-8"),
                    "rollout\n",
                )
                self.assertTrue((runtime / "skills").is_dir())


class CodexSwitchSafetyTests(unittest.TestCase):
    @staticmethod
    def fixture(base: Path):
        home = base / 'accounts'
        first, second = home / 'account-1', home / 'account-2'
        for folder in (first, second):
            folder.mkdir(parents=True)
            (folder / 'auth.json').write_text(folder.name, encoding='utf-8')
        selector = home / 'current'
        selector.symlink_to(first.name)
        runtime = base / 'runtime'
        runtime.mkdir()
        auth = runtime / 'auth.json'
        auth.symlink_to(selector / 'auth.json')
        return first, second, selector, runtime

    @staticmethod
    def fake_process(base: Path, managed_binary: Path, actual_binary: Path):
        import json
        root = base / 'proc'
        proc = root / '999'
        proc.mkdir(parents=True)
        (proc / 'cmdline').write_bytes(b'codex\0app-server\0--listen\0unix://\0')
        (proc / 'exe').symlink_to(actual_binary)
        version = json.dumps({'status': 'running', 'managedCodexPath': str(managed_binary)})
        result = mock.Mock(returncode=0, stdout=version)
        return root, result

    def test_unmanaged_app_server_refused_with_no_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first, second, selector, runtime = self.fixture(base)
            managed = base / 'managed'
            unmanaged = base / 'unmanaged'
            managed.write_text('binary', encoding='utf-8')
            unmanaged.write_text('binary', encoding='utf-8')
            proc, version = self.fake_process(base, managed, unmanaged)
            with mock.patch.object(codex_usage.subprocess, 'run', return_value=version):
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict,
                                            '不归 daemon 管理'):
                    codex_usage._assert_managed_app_server(process_root=proc)
            with mock.patch.object(codex_usage, 'DEFAULT_ACCOUNTS', (
                ('Codex 1', first), ('Codex 2', second)
            )), mock.patch.object(codex_usage, '_assert_no_active_codex_sessions'), \
                    mock.patch.object(codex_usage, '_assert_managed_app_server',
                                  side_effect=codex_usage.AccountSwitchConflict('blocked')) as guard, \
                    mock.patch.object(codex_usage, '_verify_selected_account') as verify, \
                    mock.patch.object(codex_usage, '_restart_direct_app_servers') as restart:
                result = codex_usage.switch_active_account('account-2', selector=selector,
                                                            runtime_homes=(runtime,))
            self.assertEqual(result, 'account-2')
            guard.assert_called_once()
            verify.assert_called_once()
            restart.assert_not_called()
            self.assertEqual(selector.readlink(), Path('account-2'))
            self.assertEqual((runtime / 'auth.json').resolve(), second / 'auth.json')
            self.assertEqual((first / 'auth.json').read_text(), 'account-1')

    def test_deleted_old_binary_is_never_mistaken_for_managed_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            managed = base / "managed"
            managed.write_text("binary", encoding="utf-8")
            proc, version = self.fake_process(base, managed, managed)
            (proc / "999" / "exe").unlink()
            (proc / "999" / "exe").symlink_to(str(managed) + " (deleted)")
            with mock.patch.object(codex_usage.subprocess, "run", return_value=version):
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict, "已被升级移除"):
                    codex_usage._assert_managed_app_server(process_root=proc)

    def test_preflight_accepts_only_daemon_managed_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            binary = base / 'managed'
            binary.write_text('binary', encoding='utf-8')
            proc, version = self.fake_process(base, binary, binary)
            with mock.patch.object(codex_usage.subprocess, 'run', return_value=version):
                codex_usage._assert_managed_app_server(process_root=proc)
                (proc / '999' / 'cmdline').write_bytes(b'codex\0app-server\0--stdio\0')
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict,
                                            '无法确认 daemon 管理的'):
                    codex_usage._assert_managed_app_server(process_root=proc)

    def test_restart_failure_rolls_back_account_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, selector, runtime = self.fixture(Path(tmp))
            with mock.patch.object(codex_usage, 'DEFAULT_ACCOUNTS', (
                ('Codex 1', first), ('Codex 2', second)
            )), mock.patch.object(codex_usage, '_assert_no_active_codex_sessions'), \
                    mock.patch.object(codex_usage, '_assert_managed_app_server') as guard, \
                    mock.patch.object(codex_usage, '_restart_direct_app_servers',
                                      side_effect=OSError('daemon rejected')) as restart:
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict,
                                            '回滚未通过验证'):
                    codex_usage.switch_active_account('account-2', selector=selector,
                                                       runtime_homes=(runtime,))
            guard.assert_called_once()
            self.assertEqual(restart.call_count, 2)  # restart old identity after failure
            self.assertEqual(selector.readlink(), Path('account-1'))
            self.assertEqual((runtime / 'auth.json').resolve(), first / 'auth.json')
            self.assertEqual((second / 'auth.json').read_text(), 'account-2')

    def test_successful_switch_requires_preflight_before_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, selector, runtime = self.fixture(Path(tmp))
            observations = []
            def inspect_preflight():
                observations.append(('preflight', selector.readlink()))
            def inspect_restart():
                observations.append(('restart', selector.readlink()))
            with mock.patch.object(codex_usage, 'DEFAULT_ACCOUNTS', (
                ('Codex 1', first), ('Codex 2', second)
            )), mock.patch.object(codex_usage, '_assert_no_active_codex_sessions'), \
                    mock.patch.object(codex_usage, '_assert_managed_app_server',
                                  side_effect=inspect_preflight), \
                    mock.patch.object(codex_usage, '_verify_selected_account'), \
                    mock.patch.object(codex_usage, '_restart_direct_app_servers',
                                      side_effect=inspect_restart):
                result = codex_usage.switch_active_account('account-2', selector=selector,
                                                           runtime_homes=(runtime,))
            self.assertEqual(result, 'account-2')
            self.assertEqual(observations, [('preflight', Path('account-1')),
                                            ('restart', Path('account-2')),
                                            ('preflight', Path('account-2'))])
            self.assertEqual(selector.readlink(), Path('account-2'))
            self.assertEqual((runtime / 'auth.json').resolve(), second / 'auth.json')

    def test_live_proxy_blocks_switch_before_auth_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first, second, selector, runtime = self.fixture(base)
            proc = base / 'proc'
            client = proc / '9876'
            client.mkdir(parents=True)
            (client / 'cmdline').write_bytes(b'/home/me/bin/codex.real\0app-server\0proxy\0')
            with self.assertRaisesRegex(codex_usage.AccountSwitchConflict, 'Desktop proxy'):
                codex_usage._assert_no_active_codex_sessions(proc)
            self.assertEqual(selector.resolve(), first.resolve())
            (client / 'cmdline').write_bytes(b'/home/me/bin/codex.real\0app-server\0--stdio\0')
            codex_usage._assert_no_active_codex_sessions(proc)

    def test_switch_is_interprocess_locked(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            _, _, selector, _ = self.fixture(Path(tmp))
            with codex_usage._account_switch_process_lock(selector):
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict, '正在执行'):
                    codex_usage._account_switch_process_lock(selector).__enter__()

    def test_failed_account_verification_rolls_back_and_restarts_original_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, selector, runtime = self.fixture(Path(tmp))
            checks=[]
            def check(target, selected, homes):
                checks.append(target.name)
                if target == second:
                    raise RuntimeError('wrong identity')
            with mock.patch.object(codex_usage, 'DEFAULT_ACCOUNTS', (
                ('Codex 1', first), ('Codex 2', second)
            )), mock.patch.object(codex_usage, '_assert_no_active_codex_sessions'), \
                 mock.patch.object(codex_usage, '_assert_managed_app_server'), \
                 mock.patch.object(codex_usage, '_restart_direct_app_servers') as restart, \
                 mock.patch.object(codex_usage, '_verify_selected_account', side_effect=check):
                with self.assertRaisesRegex(codex_usage.AccountSwitchConflict,
                                            '已回滚且验证通过'):
                    codex_usage.switch_active_account('account-2', selector=selector,
                                                       runtime_homes=(runtime,))
            self.assertEqual(selector.resolve(), first.resolve())
            self.assertEqual((runtime / 'auth.json').resolve(), first / 'auth.json')
            self.assertEqual(checks, ['account-2', 'account-1'])
            self.assertEqual(restart.call_count, 2)

    def test_account_verification_uses_non_generating_rpc_and_rejects_wrong_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, selector, runtime = self.fixture(Path(tmp))
            selector.unlink()
            selector.symlink_to(second.name)
            (runtime / 'auth.json').unlink()
            (runtime / 'auth.json').symlink_to(selector / 'auth.json')
            with mock.patch.object(codex_usage, '_rpc_request', side_effect=[
                {'account': {'type': 'chatgpt', 'email': 'intended@sample.invalid'}},
                {'account': {'type': 'chatgpt', 'email': 'stale@sample.invalid'}}
            ]) as rpc:
                with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                    codex_usage._verify_selected_account(second, selector, (runtime,))
            self.assertEqual(rpc.call_count, 2)
            self.assertTrue(all(call.args[1] == 'account/read' for call in rpc.call_args_list))

    def test_same_account_request_still_refreshes_and_verifies_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, selector, runtime = self.fixture(Path(tmp))
            with mock.patch.object(codex_usage, 'DEFAULT_ACCOUNTS', (
                ('Codex 1', first), ('Codex 2', second)
            )), mock.patch.object(codex_usage, '_assert_no_active_codex_sessions') as idle, \
                 mock.patch.object(codex_usage, '_assert_managed_app_server') as owned, \
                 mock.patch.object(codex_usage, '_restart_direct_app_servers') as restart, \
                 mock.patch.object(codex_usage, '_verify_selected_account') as verify:
                result = codex_usage.switch_active_account('account-1', selector=selector,
                                                           runtime_homes=(runtime,))
            self.assertEqual(result, 'account-1')
            idle.assert_called_once()
            self.assertEqual(owned.call_count, 2)
            restart.assert_called_once()
            verify.assert_called_once_with(first, selector, (runtime,))

    def test_http_conflict_preserves_reason_without_switching(self):
        import base64
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        from tm_observer.auth import BasicAuthCredentials
        from tm_observer.web import make_handler
        creds = BasicAuthCredentials("test-user", "test-password")
        handler = make_handler(mock.Mock(search_root=Path("/tmp")), auth=creds)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        authorization = "Basic " + base64.b64encode(b"test-user:test-password").decode()
        request = Request(
            url + "/codex-account/switch",
            data=urlencode({"account": "account-2"}).encode(),
            headers={"Authorization": authorization, "Origin": url},
            method="POST",
        )
        try:
            with mock.patch("tm_observer.web.switch_active_account",
                            side_effect=codex_usage.AccountSwitchConflict(
                                "检测到未受 daemon 管理的服务，账号没有更改。")) as switch:
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(request, timeout=3)
                self.assertEqual(ctx.exception.code, 409)
                reply = json.loads(ctx.exception.read())
                self.assertIn("账号没有更改", reply["error"])
                self.assertIn("daemon", reply["error"])
                switch.assert_called_once_with("account-2")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_failing_daemon_version_must_not_authorize_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            proc = base / 'proc'
            proc.mkdir()
            with mock.patch.object(codex_usage.subprocess, 'run',
                                   return_value=mock.Mock(returncode=1, stdout='')):
                with self.assertRaises(codex_usage.AccountSwitchConflict):
                    codex_usage._assert_managed_app_server(process_root=proc)


class CodexResetCreditTests(unittest.TestCase):
    def test_count_is_reported_without_leaking_credit_details(self) -> None:
        result = {
            "rateLimits": {},
            "rateLimitResetCredits": {
                "availableCount": 3,
                "credits": [{"id": "opaque-secret", "title": "reset"}],
            },
        }
        snapshot = codex_usage._sanitize_snapshot("Codex 1", Path("/tmp/account-1"), result)
        self.assertEqual(snapshot["reset_credits"], {"available_count": 3})
        self.assertNotIn("opaque-secret", str(snapshot))
        self.assertIsNone(
            codex_usage._sanitize_snapshot("Codex 1", Path("/tmp/account-1"), {})[
                "reset_credits"
            ]["available_count"]
        )

    def test_consume_targets_exact_account_with_idempotency_key(self) -> None:
        from uuid import uuid4

        key = str(uuid4())
        homes = (("Codex 1", Path("/tmp/account-1")), ("Codex 2", Path("/tmp/account-2")))
        with mock.patch.object(codex_usage, "DEFAULT_ACCOUNTS", homes), mock.patch.object(
            codex_usage, "_rpc_request", return_value={"outcome": "reset"}
        ) as rpc:
            self.assertEqual(codex_usage.consume_reset_credit("account-2", key), "reset")
            rpc.assert_called_once()
            args, kwargs = rpc.call_args
            self.assertEqual(args, (Path("/tmp/account-2"), "account/rateLimitResetCredit/consume",
                                    {"idempotencyKey": key}))
            self.assertNotIn("creditId", args[2])

    def test_invalid_account_or_attempt_never_redeems(self) -> None:
        from uuid import uuid4

        with mock.patch.object(codex_usage, "_rpc_request") as rpc:
            with self.assertRaises(ValueError):
                codex_usage.consume_reset_credit("account-3", str(uuid4()))
            with self.assertRaises(ValueError):
                codex_usage.consume_reset_credit("account-1", "bad-key")
            rpc.assert_not_called()

    def test_all_documented_outcomes_are_preserved(self) -> None:
        from uuid import uuid4

        for outcome in ("reset", "nothingToReset", "noCredit", "alreadyRedeemed"):
            with self.subTest(outcome=outcome):
                with mock.patch.object(codex_usage, "_rpc_request", return_value={"outcome": outcome}):
                    self.assertEqual(codex_usage.consume_reset_credit("account-1", str(uuid4())), outcome)
        with mock.patch.object(codex_usage, "_rpc_request", return_value={"outcome": "unexpected"}):
            with self.assertRaises(RuntimeError):
                codex_usage.consume_reset_credit("account-1", str(uuid4()))


class CodexResetCreditHTTPTests(unittest.TestCase):
    def test_post_requires_login_and_same_origin_and_returns_real_outcome(self) -> None:
        import base64
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from uuid import uuid4

        from tm_observer.auth import BasicAuthCredentials
        from tm_observer.web import codex_usage_html, make_handler

        page = codex_usage_html()
        self.assertIn("Reset cards available:", page)
        self.assertIn("redeemResetCard", page)
        creds = BasicAuthCredentials("test-user", "test-password")
        handler = make_handler(mock.Mock(search_root=Path('/tmp')), auth=creds)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        data = urlencode({"account": "account-2", "idempotency_key": str(uuid4())}).encode()
        authorization = "Basic " + base64.b64encode(b"test-user:test-password").decode()
        try:
            with mock.patch("tm_observer.web.consume_reset_credit", return_value="reset") as consume:
                no_auth = Request(url + "/codex-account/reset-credit", data=data,
                                  headers={"Origin": url}, method="POST")
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(no_auth, timeout=3)
                self.assertEqual(ctx.exception.code, 401)

                no_origin = Request(url + "/codex-account/reset-credit", data=data,
                                    headers={"Authorization": authorization}, method="POST")
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(no_origin, timeout=3)
                self.assertEqual(ctx.exception.code, 403)

                request = Request(url + "/codex-account/reset-credit", data=data,
                                  headers={"Authorization": authorization, "Origin": url}, method="POST")
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["outcome"], "reset")
                consume.assert_called_once()
                self.assertEqual(consume.call_args.args[0], "account-2")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


class CodexResetConfirmationBrowserTests(unittest.TestCase):
    def test_progressive_confirmation_never_redeems_on_cancellation(self) -> None:
        import json
        import shutil
        import subprocess

        from tm_observer.web import codex_usage_html

        if not shutil.which("node"):
            self.skipTest("node is not installed")
        # Everything runs in a mocked JS VM: fake DOM, fake fetch and fake account.
        # The real Codex CLI, credit consumption RPC and network are never called.
        html = codex_usage_html()
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const script = __INLINE_SCRIPT__;
async function runScenario(primary, secondary, confirmations, cancelAt = 0, readFails = false) {
  const dialogs = [];
  const writes = [];
  const button = {disabled: false};
  const elements = {
    usage: {innerHTML: ''},
    'usage-updated': {textContent: ''},
    'switch-message': {textContent: ''},
    'reset-message': {textContent: ''}
  };
  const context = {
    window: {confirm: text => {
      dialogs.push(text);
      return dialogs.length !== cancelAt;
    }},
    document: {
      getElementById: id => elements[id],
      querySelector: () => null,
      querySelectorAll: () => [button]
    },
    fetch: async (url, options) => {
      if (url === '/codex-usage.json') {
        if (readFails) throw new Error('offline');
        return {
          ok: true,
          json: async () => ({
            active_account: 'account-1',
            accounts: [{
              ok: true, account_home: 'account-1', label: 'Codex 1',
              reset_credits: {available_count: 2},
              primary: primary === null ? null : {remaining_percent: primary},
              secondary: secondary === null ? null : {remaining_percent: secondary},
              queried_at: 100000
            }]
          })
        };
      }
      if (url === '/codex-account/reset-credit') {
        writes.push({url, options});
        return {ok: true, json: async () => ({outcome:'reset'})};
      }
      throw new Error('Unexpected URL: ' + url);
    },
    crypto: {randomUUID: () => '13d4fdf3-845d-4bc2-ac95-f2ae453e1d0b'},
    URLSearchParams, Date, console, setInterval: () => {}
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  await context.loadUsage();
  await context.redeemResetCard('account-1');
  assert.equal(dialogs.length, cancelAt || confirmations);
  assert.equal(writes.length, cancelAt ? 0 : 1);
  dialogs.forEach((dialog, index) => {
    assert.match(dialog, /Codex 1/);
    assert.ok(dialog.includes('第 ' + (index + 1) + '/' + confirmations + ' 次确认'));
    assert.ok(dialog.includes('不可撤销'));
  });
  if (cancelAt) assert.match(elements['reset-message'].textContent, /已取消，未消耗/);
  if (writes.length) {
    const body = new URLSearchParams(writes[0].options.body);
    assert.equal(body.get('account'), 'account-1');
    assert.ok(body.has('idempotency_key'));
  }
}
(async () => {
  await runScenario(30, 30, 2);            // exactly 30% is not above 30%
  await runScenario(0, 0, 2);
  await runScenario(31, 0, 3);              // either quota window is > 30%
  await runScenario(0, 31, 3);
  await runScenario(null, 0, 3);            // unknown is high risk
  await runScenario(0, null, 3);
  await runScenario(25, 10, 2, 1);          // cancelling any step does not redeem
  await runScenario(25, 10, 2, 2);
  await runScenario(35, 10, 3, 1);
  await runScenario(35, 10, 3, 2);
  await runScenario(35, 10, 3, 3);
  await runScenario(0, 0, 3, 0, true);       // failed quota fetch -> three dialogs
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
        harness = harness.replace("__INLINE_SCRIPT__", json.dumps(script, ensure_ascii=False))
        result = subprocess.run(
            ["node", "-e", harness],
            text=True,
            capture_output=True,
            check=False,
            timeout=12,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



class CodexServicesDashboardTests(unittest.TestCase):
    def test_services_dashboard_renders_both_quota_accounts_without_writes(self) -> None:
        import json
        import shutil
        import subprocess

        from tm_observer.web import services_html

        if not shutil.which("node"):
            self.skipTest("node is not installed")
        page = services_html({"system": {"task_count": 4}}, None)
        from tm_observer.dashboard_ui import _VIEWS
        self.assertEqual(page.split('<nav class="tm-nav"', 1)[1].split('</nav>', 1)[0].count('class="tm-nav-link"'), len(_VIEWS))
        self.assertNotIn("Active account", page)
        self.assertNotIn("Use Codex 1", page)
        self.assertNotIn("Use Codex 2", page)
        self.assertIn("切换到此账号", page)
        self.assertIn("tm-active-indicator", page)
        self.assertIn("resetConfirmationCount(snapshot)", page)
        self.assertIn('class="tm-refresh-button"', page)
        self.assertEqual(page.count('id="usage-updated"'), 1)
        self.assertLess(page.index('id="usage-updated"'), page.index("id='usage'"))
        self.assertEqual(page.count("id='switch-message'"), 1)
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const code = __SCRIPT__;
const el = {
  usage: {innerHTML: ''},
  'switch-message': {textContent: ''},
  'reset-message': {textContent: ''},
  'usage-updated': {textContent: ''},
  'usage-sync': {textContent: ''}
};
let gets = 0, writes = 0;
const context = {
  document: {getElementById: id => el[id], querySelectorAll: () => [], querySelector: () => null},
  window: {confirm: () => {throw new Error('Unexpected confirmation')}},
  fetch: async (path, opts={}) => {
    if (opts.method === 'POST') {writes++; throw new Error('Unexpected POST');}
    assert.ok(['/codex-usage.json', '/codex-usage-cached.json'].includes(path));
    gets++;
    return {ok: true, json: async () => ({
      active_account: 'account-2',
      generated_at: Math.floor(Date.now() / 1000) - 5,
      refreshing: false,
      last_refresh_error: 'provider_query_failed',
      minimum_refresh_seconds: 300,
      accounts: [
        {label: 'Codex 1', account_home: 'account-1', ok: true, queried_at: 123,
         plan_type: 'pro', ordinary_usage_allowed: true,
         reset_credits: {available_count: 3},
         primary: {remaining_percent: 74, used_percent: 26, window_duration_mins: 300, resets_at: 4567},
         secondary: {remaining_percent: 61, used_percent: 39, window_duration_mins: 10080, resets_at: 5678}},
        {label: 'Codex 2', account_home: 'account-2', ok: true, queried_at: 123,
         plan_type: 'prolite', ordinary_usage_allowed: true,
         reset_credits: {available_count: 1},
         primary: {remaining_percent: 28, used_percent: 72, window_duration_mins: 10080, resets_at: 4567},
         secondary: null}
      ]
    })};
  },
  Date, console, setInterval: () => {}, URLSearchParams
};
vm.createContext(context);
vm.runInContext(code, context);
(async () => {
  await context.loadUsage();
  await new Promise(resolve => setImmediate(resolve)); // let the page's initial selection/bootstrap poll settle
  assert.match(el.usage.innerHTML, /class="card tm-account-card is-active"/);  assert.match(el.usage.innerHTML, /● 全局默认/);
  assert.equal((el.usage.innerHTML.match(/tm-active-indicator/g) || []).length, 1);
  assert.equal((el.usage.innerHTML.match(/class="tm-account-switch"/g) || []).length, 1);
  assert.match(el.usage.innerHTML, /data-account="account-1" onclick="switchAccount/);
  assert.match(el.usage.innerHTML, /Codex 1/);
  assert.match(el.usage.innerHTML, /Codex 2/);
  assert.match(el.usage.innerHTML, /74%/);
  assert.match(el.usage.innerHTML, /28%/);
  const codex2 = el.usage.innerHTML.split('Codex 2')[1].split('Reset cards available')[0];
  assert.match(codex2, /7-day window/);
  assert.doesNotMatch(codex2, /5-hour window|unavailable/);
  assert.match(el.usage.innerHTML, /Reset cards available: 3/);
  assert.match(el.usage.innerHTML, /Reset cards available: 1/);
  assert.doesNotMatch(el.usage.innerHTML, /Used:|Last checked:|连续确认/);
  // Provider queried_at=123 is genuinely ancient despite fresh API generated_at.
  // Never display a cached quota as if the provider had just been queried.
  assert.match(el['usage-updated'].textContent, /历史快照 · .* · [0-9]+天前更新$/);
  assert.equal(el['usage-sync'].textContent, '· 最近一次刷新失败，保留历史额度');
  vm.runInContext('lastUsageAt = Date.now()/1000 - 75; updateUsageAge()', context);
  assert.match(el['usage-updated'].textContent, /· 1分钟前更新$/);
  vm.runInContext('lastUsageAt = Date.now()/1000 - 4000; updateUsageAge()', context);
  assert.match(el['usage-updated'].textContent, /· 1小时前更新$/);
  vm.runInContext('lastUsageAt = null; updateUsageAge()', context);
  assert.equal(el['usage-updated'].textContent, '更新状态未知');
  assert.equal((el.usage.innerHTML.match(/reset-credit-button/g) || []).length, 2);
  assert.ok(gets >= 1);
  assert.equal(writes, 0);
})().catch(error => { console.error(error); process.exitCode = 1 });
"""
        harness = harness.replace("__SCRIPT__", json.dumps(script, ensure_ascii=False))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_services_switch_and_errors_use_mocked_requests_only(self) -> None:
        import json
        import shutil
        import subprocess

        from tm_observer.web import services_html

        if not shutil.which("node"):
            self.skipTest("node is not installed")
        page = services_html({}, None)
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const code = __SCRIPT__;
let active = 'account-2';
let writes = 0;
let failGet = false;
const els = {
  usage: {innerHTML: '', querySelector: () => els.usage.innerHTML.includes('tm-account-card') ? {} : null},
  'switch-message': {textContent: ''},
  'reset-message': {textContent: ''},
  'usage-updated': {textContent: ''}
};
const context = {
  document: {getElementById: id => els[id],
    querySelector: () => null,
    querySelectorAll: () => []},
  fetch: async (path, opts={}) => {
    if (opts.method === 'POST') {
      assert.equal(path, '/services-selection/switch');
      const params = new URLSearchParams(opts.body);
      assert.equal(params.get('account'), 'account-1');
      writes++;
      active = 'account-1';
      return {ok: true, json: async () => ({ok: true, active_account: 'account-1', tier: 2})};
    }
    assert.ok(['/codex-usage.json', '/codex-usage-cached.json'].includes(path));
    if (failGet) throw new Error('<img src=x onerror=alert(1)>');
    return {ok: true, json: async () => ({
      active_account: active,
      generated_at: Math.floor(Date.now() / 1000),
      accounts: [
        {account_home:'account-1', label:'Codex <1>', plan_type:'<svg onload=evil()>',
         ok:true, reset_credits:{available_count:0}, ordinary_usage_allowed:true},
        {account_home:'account-2', label:'Codex 2', ok:false,
         error:'<img src=x onerror=alert(1)>'}
      ]
    })};
  },
  Date, console, setInterval: () => {}, URLSearchParams
};
vm.createContext(context);
vm.runInContext(code, context);
(async () => {
  await context.loadUsage();
  await new Promise(resolve => setImmediate(resolve)); // let the page's initial selection/bootstrap poll settle
  assert.equal(writes, 0, 'initial page load must not switch accounts');
  assert.match(els.usage.innerHTML, /Codex &lt;1&gt;/);
  assert.match(els.usage.innerHTML, /&lt;svg onload=evil\(\)&gt;/);
  assert.match(els.usage.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(els.usage.innerHTML, /<img|<svg/);
  assert.ok(els.usage.innerHTML.indexOf('data-account="account-1" onclick="switchAccount') >= 0);
  assert.equal((els.usage.innerHTML.match(/tm-active-indicator/g) || []).length, 1);
  await context.switchAccount('not-an-account');
  assert.equal(writes, 0);
  await context.switchAccount('account-1');
  assert.equal(writes, 1);
  assert.match(els['switch-message'].textContent, /已切换到 Codex 1/);
  assert.match(els.usage.innerHTML, /class="card tm-account-card is-active"><div class="tm-account-header"><h2>Codex &lt;1&gt;/);
  failGet = true;
  await context.loadUsage();
  assert.match(els.usage.innerHTML, /Codex &lt;1&gt;/, 'failed refresh retains last verified quota card');
  assert.doesNotMatch(els.usage.innerHTML, /<img/);
  assert.match(els['usage-updated'].textContent, /更新/, 'keeps last verified update timestamp');
  assert.equal(writes, 1);
})().catch(error => { console.error(error); process.exitCode = 1 });
"""
        harness = harness.replace("__SCRIPT__", json.dumps(script, ensure_ascii=False))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
