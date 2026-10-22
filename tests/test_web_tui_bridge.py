"""The Web TUI must call the same scientific render_* functions as Textual."""
from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from theory_master.observability.collector import FileSource
from theory_master.tui import render
from theory_master.tui.web_bridge import project, PRIMARY


class CanonicalTuiRendererTests(unittest.TestCase):
    def test_primary_views_match_cli_textual_renderers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = root / '2'
            cycle.mkdir()
            (cycle / 'run_state.json').write_text(json.dumps({
                'master_id': 1, 'cycle_id': '2', 'status': 'RUNNING',
                'phase': 'investigator:running', 'current_agent': 'investigator',
                'cycle_index': 2, 'max_cycles': 4,
                'agents': {'investigator': {'state': 'running', 'model': 'gpt-6-sol', 'elapsed_s': 23}},
                'project_tokens_total': 5211,
            }), encoding='utf-8')
            snapshot = FileSource(root).snapshot()
            visible = project(root)
            self.assertEqual(visible['cycle_id'], snapshot['cycle_id'])
            self.assertEqual(tuple(visible['parts']), tuple(PRIMARY))
            for key, callback in PRIMARY.items():
                self.assertEqual(visible['parts'][key], callback(snapshot), key)
            self.assertIn('AGENT/STAGE', visible['parts']['workflow'])
            self.assertIn('Investigator', visible['parts']['workflow'])

    def test_idle_role_without_agent_metrics_uses_frozen_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {
                "cycle_id": "70", "status": "RUNNING",
                "agents": {"literature": {"state": "idle", "turn_count": 0}},
                "runtime_role_routes": {
                    "literature": {
                        "model": "gpt-6-luna", "provider": "CodexHTTPS",
                        "reasoning_effort": "low",
                    },
                },
            }
            with patch("theory_master.backend_config.read_model_profile", return_value="test"), \
                 patch("theory_master.tui.web_bridge.FileSource") as source:
                source.return_value.snapshot.return_value = state
                source.return_value.run_dir = None
                projected = project(root)
            self.assertEqual(projected["role_settings"]["literature"], {
                "model": "gpt-6-luna", "provider": "CodexHTTPS",
                "reasoning_effort": "low", "pending": None,
                "preset_tier": "Codex 一档", "preset_profile": "test",
            })
            self.assertIsNone(projected["role_usage"]["literature"]["cycle"]["tokens_total"])
            self.assertIn("Literature", projected["parts"]["workflow"])

    def test_worker_tier_uses_worker_routes_not_stale_task_pending(self):
        from theory_master.backend_config import save_task_role_override
        from theory_master.worker_runtime import save_worker_profile, save_worker_route

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / '.theory-master' / 'workers' / 'worker-1'
            worker.mkdir(parents=True)
            (worker / 'runtime.json').write_text(json.dumps({
                'schema': 'theory-master/worker-runtime/v1',
                'worker_id': 'worker-1', 'current_cycle': '9', 'status': 'RUNNING',
            }), encoding='utf-8')
            cycle = root / '9'
            cycle.mkdir()
            state = {'cycle_id': '9', 'status': 'RUNNING', 'cycle_index': 1,
                     'agents': {'judge': {'model': 'gpt-6-sol', 'provider': 'CodexHTTPS',
                                          'reasoning_effort': 'medium', 'state': 'idle'}}}
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')
            save_worker_profile(root, 'worker-1', 'test')
            # An unrelated legacy single-runner edit must not make the Worker
            # appear to be waiting for the wrong Judge model forever.
            save_task_role_override(root, role='judge', model='gpt-6-sol',
                                    provider='CodexHTTPS', effort='medium')
            projected = project(root, cycle_dir=cycle, worker_id='worker-1')
            judge = projected['role_settings']['judge']
            self.assertEqual(judge['model'], 'gpt-6-luna')
            self.assertIsNone(judge['pending'])
            self.assertNotIn('待安全调用', judge['preset_tier'])

            # After the new Worker Cycle starts, the badge clears automatically.
            state['agents']['judge']['model'] = 'gpt-6-luna'
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')
            judge = project(root, cycle_dir=cycle, worker_id='worker-1')['role_settings']['judge']
            self.assertIsNone(judge['pending'])
            self.assertNotIn('待安全调用', judge['preset_tier'])

            # The authoritative SDK application receipt outranks a delayed
            # Collector agent snapshot after same-Cycle pause/resume.
            state['agents']['judge']['model'] = 'gpt-6-sol'
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')
            (cycle / 'research_state.json').write_text(json.dumps({
                'worker_id':'worker-1', 'runtime_hot_role_routes': {
                    'judge': {'model':'gpt-6-luna', 'provider':'CodexHTTPS',
                              'reasoning_effort':'medium'}},
                'runtime_role_routes': {
                    'judge': {'model':'gpt-6-sol', 'provider':'CodexHTTPS',
                              'reasoning_effort':'medium'}}}), encoding='utf-8')
            judge = project(root, cycle_dir=cycle, worker_id='worker-1')['role_settings']['judge']
            self.assertEqual(judge['model'], 'gpt-6-luna')
            self.assertIsNone(judge['pending'])
            (cycle / 'research_state.json').unlink()
            state['agents']['judge']['model'] = 'gpt-6-luna'
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')

            # Existing Cycle-bound per-role entries stay deferred by choice.
            save_worker_route(root, 'worker-1', role='judge', model='gpt-6-sol',
                              provider='CodexHTTPS', effort='high')
            judge = project(root, cycle_dir=cycle, worker_id='worker-1')['role_settings']['judge']
            self.assertEqual(judge['pending'], {
                'model': 'gpt-6-sol', 'provider': 'CodexHTTPS',
                'reasoning_effort': 'high', 'activation': 'next_cycle'})

            save_worker_route(root, 'worker-1', role='judge', model='gpt-6-sol',
                              provider='CodexHTTPS', effort='high', activation='next_invocation')
            judge = project(root, cycle_dir=cycle, worker_id='worker-1')['role_settings']['judge']
            self.assertIsNone(judge['pending'])
            self.assertEqual((judge['model'], judge['reasoning_effort']),
                             ('gpt-6-sol', 'high'))
            self.assertNotIn('待安全调用', judge['preset_tier'])

            # The legacy task scope still sees its own saved override.
            single = project(root)['role_settings']['judge']
            self.assertEqual(single['pending']['model'], 'gpt-6-sol')
            self.assertEqual(single['pending']['activation'], 'next_invocation')

    def test_worker_active_invocation_pending_only_for_its_role(self):
        from theory_master.worker_runtime import save_worker_profile
        from theory_master.backend_config import MODEL_PROFILES
        from tm_observer.run_views import _tui_workflow_table
        from bs4 import BeautifulSoup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / '.theory-master/workers/worker-1'
            worker.mkdir(parents=True)
            runtime = {'schema': 'theory-master/worker-runtime/v1',
                       'worker_id': 'worker-1', 'current_cycle': '19',
                       'status': 'RUNNING'}
            (worker / 'runtime.json').write_text(json.dumps(runtime), encoding='utf-8')
            cycle = root / '19'
            cycle.mkdir()
            state = {'cycle_id': '19', 'status': 'RUNNING', 'cycle_index': 1,
                     'current_agent': 'investigator', 'current_turn_role': 'investigator',
                     'agents': {role: {'state': 'running' if role == 'investigator' else 'idle',
                                      'model': 'gpt-6-luna', 'provider': 'CodexHTTPS',
                                      'reasoning_effort': 'medium'} for role in MODEL_PROFILES['codex']}}
            (cycle / 'run_state.json').write_text(json.dumps(state), encoding='utf-8')
            scientific = {'worker_id':'worker-1',
                          'active_invocation_id':'inv-19-investigator-abcd',
                          'runtime_role_routes': {role: {'model':'gpt-6-luna',
                          'provider':'CodexHTTPS', 'reasoning_effort':'medium'}
                          for role in MODEL_PROFILES['codex']}}
            (cycle / 'research_state.json').write_text(json.dumps(scientific), encoding='utf-8')
            save_worker_profile(root, 'worker-1', 'codex')
            with patch('theory_master.worker_runtime.process_identity_alive', return_value=True):
                visible = project(root, cycle_dir=cycle, worker_id='worker-1')
            routes = visible['role_settings']
            self.assertEqual(routes['investigator']['model'], 'gpt-6-luna')
            self.assertEqual(routes['investigator']['pending_reason'], 'active_invocation')
            self.assertEqual(routes['investigator']['pending']['model'], 'gpt-6-sol')
            for role in ('research_lead', 'challenger', 'literature', 'judge', 'reporter'):
                self.assertIsNone(routes[role]['pending'], role)
                self.assertEqual(routes[role]['model'], MODEL_PROFILES['codex'][role].model)
            names = ('Research Lead', 'Challenger', 'Investigator', 'Literature', 'Judge', 'Reporter')
            raw = 'AGENT/STAGE    MODEL                         COST      TIME     STATE\n'
            raw += '\n'.join(f'{name:<13}  {"GPT-6 Luna":<24}  {"—":>8}  {"00:00":>8}  {("running" if name == "Investigator" else "idle"):>8}' for name in names)
            page = BeautifulSoup(_tui_workflow_table(raw, hot_route_capable=True,
                                  settings=routes, catalog=visible['model_catalog']), 'html.parser')
            self.assertEqual(len(page.select('.tm-tui-pending')), 1)
            self.assertIn('当前调用结束后切换', page.select_one('.tm-tui-pending').get_text())
            self.assertNotIn('等待当前 Cycle', page.get_text())
            # Once the same invocation is gone, the saved target is the
            # immediately effective next-call selection, not a pending edit.
            scientific['active_invocation_id'] = None
            (cycle / 'research_state.json').write_text(json.dumps(scientific), encoding='utf-8')
            with patch('theory_master.worker_runtime.process_identity_alive', return_value=True):
                after = project(root, cycle_dir=cycle, worker_id='worker-1')
            self.assertIsNone(after['role_settings']['investigator']['pending'])
            self.assertEqual(after['role_settings']['investigator']['model'], 'gpt-6-sol')
            runtime['status'] = 'PAUSED'
            (worker / 'runtime.json').write_text(json.dumps(runtime), encoding='utf-8')
            scientific['active_invocation_id'] = 'inv-19-investigator-abcd'
            (cycle / 'research_state.json').write_text(json.dumps(scientific), encoding='utf-8')
            with patch('theory_master.worker_runtime.process_identity_alive', return_value=False):
                paused = project(root, cycle_dir=cycle, worker_id='worker-1')
            self.assertIsNone(paused['role_settings']['investigator']['pending'])
            # SDK-confirmed Provider-thread deferral is a real limitation,
            # unlike an idle role's stale Cycle route. Keep it visible.
            scientific['runtime_hot_route_deferred'] = {
                'judge': {'reason': 'provider_change_requires_new_thread'}}
            (cycle / 'research_state.json').write_text(json.dumps(scientific), encoding='utf-8')
            with patch('theory_master.worker_runtime.process_identity_alive', return_value=False):
                deferred = project(root, cycle_dir=cycle, worker_id='worker-1')
            self.assertEqual(deferred['role_settings']['judge']['pending_reason'],
                             'provider_deferred')
            self.assertEqual(deferred['role_settings']['judge']['pending']['model'],
                             'gpt-6-sol')

    def test_web_timeline_shows_full_stored_message_without_expanding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = root / "2"
            cycle.mkdir()
            message = "Workflow failed: " + "validation cannot claim holdout or gate identity " * 5
            stored = {"cycle_id": "2", "status": "FAILED",
                      "action_history": [{"at": "2026-10-11T22:02:27+08:00",
                                          "event": "WORKFLOW_FAILED", "text": message}]}
            self.assertNotIn(message, render.render_last(stored))  # TUI remains compact
            with patch("theory_master.tui.web_bridge.FileSource") as source:
                source.return_value.snapshot.return_value = stored
                source.return_value.run_dir = None
                self.assertIn(message, project(root)["parts"]["last"])  # Web displays all text

    def test_web_keeps_older_cycle_markers_without_changing_cli_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            starts = {0: "56", 25: "57", 48: "58"}
            actions = [{
                "at": f"2026-10-11T21:{index // 60:02d}:{index % 60:02d}+08:00",
                "event": "CYCLE_STARTED" if index in starts else "SKILL_LOADED",
                "text": ("Cycle started · " + starts[index]) if index in starts
                        else "Investigator · skill loaded · test",
                "role": "" if index in starts else "investigator",
            } for index in range(60)]
            stored = {"cycle_id": "58", "status": "RUNNING",
                      "action_history": actions}
            compact = render.render_last(stored)
            self.assertNotIn("Cycle started · 56", compact)
            with patch("theory_master.tui.web_bridge.FileSource") as source:
                source.return_value.snapshot.return_value = stored
                source.return_value.run_dir = None
                visible = project(root)
            timeline = visible["parts"]["last"]
            self.assertIn("Cycle started · 56", timeline)
            self.assertIn("Cycle started · 57", timeline)
            self.assertIn("Cycle started · 58", timeline)
            self.assertEqual(len(timeline.splitlines()), len(visible["recent_events"]))
            self.assertEqual(len(visible["recent_events"]), len(actions))
            self.assertEqual(visible["recent_events"][0]["at"], actions[0]["at"])

    def test_legacy_cycle_keeps_original_renderers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = root / 'cycle-0003'
            cycle.mkdir()
            (cycle / 'run_state.json').write_text(json.dumps({
                'master_id': 8, 'cycle_id': 'cycle-0003',
                'status': 'OVER', 'phase': 'complete', 'project_tokens_total': 1001,
            }), encoding='utf-8')
            projection = project(root, cycle_dir=cycle)
            self.assertIn('Task ID 8', projection['parts']['identity'])
            self.assertIn('1,001', projection['parts']['tokens'])
            self.assertEqual(projection['cycle_id'], 'cycle-0003')


class BillingProjectionTests(unittest.TestCase):
    """Nonbilling model usage is still meaningful observational evidence."""

    def test_unpriced_agents_keep_measured_tokens_and_cache_without_fake_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = root / "5"
            cycle.mkdir()
            (cycle / ".theory-master-cycle").write_text("fixture", encoding="utf-8")
            snapshot = {
                "cycle_id":"5", "status":"RUNNING",
                "tokens_in":1000, "tokens_out":150, "tokens_total":1150,
                "cached_input_tokens":750, "reasoning_tokens":50,
                "cycle_cost":{"amount":None,"currency":None,"source":"unavailable",
                              "model_calls":2,"priced_model_calls":0},
                "project_tokens_in":4300,"project_tokens_out":300,
                "project_tokens_total":4600, "project_cached_input_tokens":2700,
                "project_reasoning_tokens":85,
                "project_cost":{"amount":0.01066,"currency":"USD","source":"estimated",
                                "confidence":"partial","model_calls":4,"priced_model_calls":1},
                "agents":{"research_lead":{"tokens_in":1000,"tokens_out":150,"tokens_total":1150,
                                           "cached_input_tokens":750,"reasoning_tokens":50,
                                           "cost":{"amount":None,"currency":None,"source":"unavailable",
                                                   "model_calls":2,"priced_model_calls":0}}},
                "project_agents":{"research_lead":{"tokens_in":4300,"tokens_out":300,"tokens_total":4600,
                                                   "cached_input_tokens":2700,"reasoning_tokens":85,
                                                   "cost":{"amount":None,"currency":None,"source":"unavailable",
                                                           "model_calls":3,"priced_model_calls":0}},
                                  "reporter":{"tokens_in":None,"tokens_out":None,"tokens_total":None,
                                              "cost":{"amount":0.01066,"currency":"USD","source":"estimated",
                                                      "model_calls":1,"priced_model_calls":1}}}
            }
            (cycle/"run_state.json").write_text(json.dumps(snapshot),encoding="utf-8")
            web=project(root)
            lead=web["role_usage"]["research_lead"]
            self.assertEqual(lead["cycle"]["tokens_total"],1150)
            self.assertEqual(lead["project"]["tokens_total"],4600)
            self.assertEqual(lead["project"]["cached_input_tokens"],2700)
            self.assertEqual(lead["project"]["reasoning_tokens"],85)
            self.assertIsNone(lead["project"]["cost"]["amount"])
            self.assertEqual(lead["project"]["cost"]["model_calls"],3)
            self.assertEqual(web["role_usage"]["reporter"]["project"]["cost"]["amount"],0.01066)
            self.assertIsNone(web["role_usage"]["literature"]["cycle"]["tokens_total"])
            self.assertEqual(web["usage_totals"]["project"]["tokens_total"],4600)
            self.assertEqual(web["usage_totals"]["project"]["cost"]["confidence"],"partial")
            self.assertEqual(web["usage_totals"]["project"]["cost"]["priced_model_calls"],1)
            self.assertEqual(web["usage_totals"]["project"]["cost"]["model_calls"],4)
            self.assertEqual(web["parts"]["workflow"],render.render_workflow(FileSource(root).snapshot()))


class FrozenHypothesisReadProjectionTests(unittest.TestCase):
    def test_projection_uses_locked_scientific_object_not_transient_display(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            cycle=root/"61"
            cycle.mkdir()
            (cycle/".theory-master-cycle").write_text("fixture",encoding="utf-8")
            (cycle/"run_state.json").write_text(json.dumps({
                "cycle_id":"61","status":"FAILED",
                "hypothesis":"transient stale copy",
                "hypothesis_lock":{"id":"H-0051","status":"locked",
                   "hypothesis":"真实冻结命题","scope":"冻结适用范围",
                   "decisive_test":"真实冻结检验",
                   "qualification":{"review":{"reason":"Challenger 原始理由"},
                                    "selection_rationale":"候选选择说明"}},
                "qualification_status":"qualified",
            },ensure_ascii=False),encoding="utf-8")
            projected=project(root)
            frozen=projected["frozen_hypothesis"]
            self.assertEqual(frozen["id"],"H-0051")
            self.assertTrue(frozen["locked"])
            self.assertEqual(frozen["hypothesis"],"真实冻结命题")
            self.assertEqual(frozen["scope"],"冻结适用范围")
            self.assertEqual(frozen["decisive_test"],"真实冻结检验")
            self.assertEqual(frozen["challenger_reason"],"Challenger 原始理由")
            self.assertNotIn("transient stale copy",str(frozen))
