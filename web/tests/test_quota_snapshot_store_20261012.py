"""Quota history: no live provider calls from repeated Run page reads."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from tm_observer.quota_snapshot_store import QuotaHistory
from tm_observer.run_views import _codex_quota_metric_card, run_detail_body


def provider_result(*, account='account-2', five=73, week=66, timestamp=None):
    stamp = int(time.time()) if timestamp is None else timestamp
    return {'active_account': account, 'accounts': [
        {'account_home':account, 'ok':True,
         'primary':{'used_percent':100-five, 'remaining_percent':five, 'window_duration_mins':300},
         'secondary':{'used_percent':100-week, 'remaining_percent':week, 'window_duration_mins':10080},
         'queried_at':stamp,
         'reset_credits':{'available_count':4},'credits':{'balance':9999},
         'authorization':'must never persist this'}]}


class QuotaHistoryTests(unittest.TestCase):
    def test_week_only_primary_survives_restart_without_invented_five_hour_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'history.json'
            result = provider_result()
            result['accounts'][0]['primary'] = {
                'remaining_percent': 81, 'window_duration_mins': 10080}
            result['accounts'][0]['secondary'] = None
            cache = QuotaHistory(path, provider=lambda: result, active=lambda: 'account-2')
            cache.observe(result)
            restored = QuotaHistory(path, provider=lambda: result, active=lambda: 'account-2')
            snapshot = restored.snapshot()
            self.assertIsNone(snapshot['accounts'][0]['secondary'])
            self.assertEqual(snapshot['accounts'][0]['primary']['window_duration_mins'], 10080)
            card = _codex_quota_metric_card(snapshot)
            self.assertIn('7 天剩余：81%', card)
            self.assertNotIn('5 小时剩余', card)
            self.assertIn('data-quota-week hidden', card)

    def test_first_read_nonblocking_one_refresh_across_many_pages_and_tabs(self):
        with tempfile.TemporaryDirectory() as temp:
            started=threading.Event()
            release=threading.Event()
            calls=[]
            def provider():
                calls.append('rpc')
                started.set()
                if not release.wait(timeout=4):
                    raise RuntimeError('test timeout')
                return provider_result()
            path=Path(temp)/'history.json'
            source=QuotaHistory(path, provider=provider,active=lambda:'account-2')
            try:
                initial=source.snapshot(request_update=True)
                self.assertTrue(initial['refreshing'])
                self.assertEqual(initial['accounts'],[])
                self.assertTrue(started.wait(timeout=2))
                for _ in range(60):
                    self.assertTrue(source.snapshot(request_update=True)['refreshing'])
                self.assertEqual(calls,['rpc'])
            finally:
                release.set()
            for _ in range(100):
                if not source.snapshot()['refreshing']: break
                time.sleep(.01)
            ready=source.snapshot(request_update=True)
            self.assertEqual(ready['accounts'][0]['primary']['remaining_percent'],73)
            self.assertEqual(calls,['rpc'])
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            raw=path.read_text()
            self.assertNotIn('authorization',raw)
            self.assertNotIn('reset_credits',raw)
            self.assertNotIn('credits',raw)
            self.assertLess(len(raw),700)

    def test_warm_restart_restores_saved_history_with_no_immediate_rpc(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.json'
            previous=QuotaHistory(path,provider=lambda:None,active=lambda:'account-2')
            previous.observe(provider_result(five=65,week=40))
            called=[]
            after=QuotaHistory(path,provider=lambda:called.append(1) or provider_result(),
                               active=lambda:'account-2')
            historical=after.snapshot(request_update=True)
            self.assertEqual(called,[])
            self.assertFalse(historical['refreshing'])
            self.assertEqual(historical['accounts'][0]['primary']['remaining_percent'],65)
            self.assertEqual(historical['cache_age_s'],0)
            card=_codex_quota_metric_card(historical)
            self.assertIn('Codex 2',card)
            self.assertIn('5 小时剩余：65%',card)
            self.assertIn('7 天剩余：40%',card)
            self.assertIn('历史记录 · 更新于',card)
            self.assertNotIn('Codex 额度 · 查询中',card)
            page=run_detail_body({'observer_id':'a'*16,'run_id':1,'task_name':'Mock',
                                  'cycle':'1','state':'OVER'},quota_snapshot=historical)
            self.assertIn('5 小时剩余：65%',page)
            self.assertIn('7 天剩余：40%',page)
            self.assertIn('setInterval(loadCodexQuota,300000)',page)
            self.assertIn('/codex-usage-cached.json',page)

    def test_stale_history_remains_visible_before_update_and_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.json'
            stale=provider_result(five=23,week=76,timestamp=int(time.time())-3600)
            reader=QuotaHistory(path,provider=lambda:None,active=lambda:'account-2')
            reader.observe(stale)
            started=threading.Event();release=threading.Event();calls=[]
            def failing_provider():
                calls.append(1)
                started.set()
                release.wait(timeout=4)
                raise OSError('codex is temporarily offline')
            reader=QuotaHistory(path,provider=failing_provider,active=lambda:'account-2')
            try:
                initial=reader.snapshot(request_update=True)
                self.assertTrue(initial['refreshing'])
                self.assertEqual(initial['accounts'][0]['primary']['remaining_percent'],23)
                self.assertIn('5 小时剩余：23%',_codex_quota_metric_card(initial))
                self.assertTrue(started.wait(timeout=2))
                self.assertEqual(len(calls),1)
            finally:
                release.set()
            for _ in range(100):
                if not reader.snapshot()['refreshing']:break
                time.sleep(.01)
            after=reader.snapshot(request_update=True)
            self.assertEqual(len(calls),1,'failed queries are throttled too')
            self.assertEqual(after['accounts'][0]['primary']['remaining_percent'],23)
            self.assertGreater(after['cache_age_s'],300)
            self.assertEqual(after['last_refresh_error'], 'provider_refresh_exception')
            self.assertGreater(after['last_attempt_at'], 0)
            self.assertEqual(json.loads(path.read_text())['accounts'][0]['primary']['remaining_percent'],23)

    def test_provider_error_payload_is_reported_without_discarding_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'history.json'
            stale = provider_result(five=31, week=68, timestamp=int(time.time()) - 3600)
            seed = QuotaHistory(path, provider=lambda: None, active=lambda: 'account-2')
            seed.observe(stale)
            failure = {'accounts': [
                {'account_home': 'account-1', 'ok': False, 'error': 'RuntimeError: provider unavailable'},
                {'account_home': 'account-2', 'ok': False, 'error': 'RuntimeError: provider unavailable'},
            ]}
            reader = QuotaHistory(path, provider=lambda: failure, active=lambda: 'account-2')
            initial = reader.snapshot(request_update=True)
            self.assertTrue(initial['refreshing'])
            for _ in range(100):
                after = reader.snapshot()
                if not after['refreshing']:
                    break
                time.sleep(.01)
            self.assertEqual(after['last_refresh_error'], 'provider_query_failed')
            self.assertEqual(after['accounts'][0]['primary']['remaining_percent'], 31)
            self.assertGreater(after['last_attempt_at'], 0)

    def test_account_switch_never_reuses_previous_accounts_percent(self):
        with tempfile.TemporaryDirectory() as temp:
            selected=['account-1']
            cache=QuotaHistory(Path(temp)/'history.json',provider=lambda:None,active=lambda:selected[0])
            cache.observe(provider_result(account='account-1',five=9,week=12))
            cache.observe(provider_result(account='account-2',five=85,week=60))
            self.assertEqual(cache.snapshot()['active_account'],'account-1')
            selected[0]='account-2'
            switched=cache.snapshot()
            self.assertEqual(switched['active_account'],'account-2')
            self.assertIn('5 小时剩余：85%',_codex_quota_metric_card(switched))
            self.assertNotIn('5 小时剩余：9%',_codex_quota_metric_card(switched))
            selected[0]=None
            self.assertNotIn('5 小时剩余：9%',_codex_quota_metric_card(cache.snapshot()))
            self.assertNotIn('5 小时剩余：85%',_codex_quota_metric_card(cache.snapshot()))

    def test_bad_provider_read_and_invalid_disk_never_replace_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.json'
            cache=QuotaHistory(path,provider=lambda:None,active=lambda:'account-2')
            cache.observe(provider_result(five=55,week=25))
            damaged=provider_result(five=0,week=0)
            damaged['accounts'][0]['primary']['remaining_percent']=-999
            cache.observe(damaged)
            cache.observe({'accounts':[{'account_home':'account-2','ok':False}]})
            self.assertEqual(cache.snapshot()['accounts'][0]['primary']['remaining_percent'],55)
            path.write_text('{bad json',encoding='utf-8')
            cold=QuotaHistory(path,provider=lambda:None,active=lambda:'account-2')
            self.assertEqual(cold.snapshot()['accounts'],[])
            path.unlink()
            path.symlink_to(Path(temp)/'outside.json')
            unsafe=QuotaHistory(path,provider=lambda:None,active=lambda:'account-2')
            unsafe.observe(provider_result())
            self.assertFalse((Path(temp)/'outside.json').exists())

    def test_authenticated_cached_endpoint_and_services_feed_shared_history(self):
        import base64
        from tm_observer.auth import BasicAuthCredentials
        from tm_observer.web import make_handler
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            cache=mock.Mock(search_root=base)
            cache.get.return_value={'runs': []}
            with (mock.patch('tm_observer.web.collect_codex_usage',return_value=provider_result()) as live,
                  mock.patch('tm_observer.web.QuotaHistory') as factory):
                history=factory.return_value
                history.snapshot.return_value={'active_account':'account-2','accounts':[],
                                               'refreshing':True,'historical':True,'cache_age_s':None}
                handler=make_handler(cache,auth=BasicAuthCredentials('viewer','pass'),
                                     field_notes_state_dir=base)
                server=ThreadingHTTPServer(('127.0.0.1',0),handler)
                worker=threading.Thread(target=server.serve_forever,daemon=True)
                worker.start()
                try:
                    root=f'http://127.0.0.1:{server.server_address[1]}'
                    auth='Basic '+base64.b64encode(b'viewer:pass').decode()
                    def get(path):
                        with urlopen(Request(root+path,headers={'Authorization':auth}),timeout=3) as response:
                            return json.loads(response.read())
                    result=get('/codex-usage-cached.json')
                    self.assertEqual(result['accounts'],[])
                    history.snapshot.assert_called_with(request_update=True)
                    self.assertEqual(live.call_count,0,'Run cached route cannot call Codex')
                    manual=get('/codex-usage.json')
                    self.assertEqual(manual['accounts'][0]['primary']['remaining_percent'],73)
                    self.assertEqual(live.call_count,1)
                    history.observe.assert_called_once()
                finally:
                    server.shutdown();server.server_close();worker.join(timeout=3)


    def test_services_display_details_are_memory_only_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'history.json'
            raw = provider_result()
            account = raw['accounts'][0]
            account.update({
                'label': 'Codex 2', 'plan_type': 'pro', 'ordinary_usage_allowed': True,
                'reset_credits': {'available_count': 3}, 'authorization': 'must never escape',
            })
            account['primary']['resets_at'] = int(time.time()) + 3600
            account['secondary']['resets_at'] = int(time.time()) + 7200
            cache = QuotaHistory(path, provider=lambda: None, active=lambda: 'account-2')
            cache.observe(raw)
            display = cache.snapshot()['accounts'][0]
            self.assertEqual(display['plan_type'], 'pro')
            self.assertTrue(display['ordinary_usage_allowed'])
            self.assertEqual(display['reset_credits']['available_count'], 3)
            self.assertIsNotNone(display['primary']['resets_at'])
            self.assertNotIn('authorization', display)
            persisted = json.loads(path.read_text(encoding='utf-8'))['accounts'][0]
            self.assertNotIn('plan_type', persisted)
            self.assertNotIn('reset_credits', persisted)
            self.assertNotIn('authorization', persisted)

if __name__=='__main__':unittest.main()
