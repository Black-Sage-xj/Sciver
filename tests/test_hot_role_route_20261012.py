"""Safety cases for operator model routes at the next SDK Agent boundary."""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from theory_master.abc_pipeline import AgentSemanticBackend
from theory_master.backend_config import (
    RoleRoute, build_model_profile, save_task_role_override,
)
from theory_master.codex_backend import CodexBackend
from theory_master.workflow import Workflow
from theory_master.workflow_invocation import InvocationMixin


class HotRoleRouteTests(unittest.TestCase):
    def setup_workflow(self, root: Path):
        (root / '.theory-master').mkdir()
        (root / '71').mkdir()
        routes = build_model_profile('advanced')
        backend = CodexBackend.__new__(CodexBackend)
        backend.routes = dict(routes)
        backend.threads = {}
        backend._thread_config = lambda role,provider: {
            'model_reasoning_effort': backend.routes[role].reasoning_effort}
        backend._ensure_client = Mock()
        backend.collector = None
        wf = Workflow.__new__(Workflow)
        wf.spec = types.SimpleNamespace(research_root=root)
        wf.routes = dict(routes)
        wf.backend = backend
        wf.semantic_backend = AgentSemanticBackend(backend, model=routes['research_lead'].model,
                                                   provider=routes['research_lead'].provider)
        wf.collector = Mock()
        wf.route_resolver = lambda: dict(routes)
        wf.resume_route_overrides = {}
        wf.explicit_cli_route_roles = frozenset()
        wf.cycle_dir = root / '71'
        wf._emit = Mock()
        wf._persist = Mock()
        return wf

    def test_approved_task_edit_applies_next_invocation_without_interrupting_existing_call(self):
        with tempfile.TemporaryDirectory() as temp:
            wf = self.setup_workflow(Path(temp))
            current = wf.routes['investigator']
            self.assertEqual(current.model, 'gpt-6-sol')
            wf.backend.threads['investigator-thread'] = types.SimpleNamespace(id='investigator-thread')
            resumed = types.SimpleNamespace(id='investigator-thread')
            wf.backend._ensure_client.return_value.thread_resume.return_value = resumed
            save_task_role_override(wf.spec.research_root, role='investigator',
                                    model='gpt-6.1-sol',provider='CodexHTTPS',effort='low')
            state = {'runtime_role_routes': {'investigator': Workflow._route_snapshot(current)},
                     'status': 'active'}
            fake_sdk = types.SimpleNamespace(ApprovalMode=types.SimpleNamespace(deny_all='deny'))
            with patch.dict(sys.modules, {'openai_codex':fake_sdk}):
                wf._apply_pending_task_route(state,'investigator',thread_id='investigator-thread')
            after = wf.routes['investigator']
            self.assertEqual((after.model, after.reasoning_effort),('gpt-6.1-sol','low'))
            self.assertIs(wf.backend.threads['investigator-thread'],resumed)
            kwargs = wf.backend._ensure_client.return_value.thread_resume.call_args.kwargs
            self.assertEqual(kwargs['model'],'gpt-6.1-sol')
            self.assertEqual(kwargs['config']['model_reasoning_effort'],'low')
            self.assertEqual(kwargs['approval_mode'],'deny')
            self.assertEqual(state['runtime_role_routes']['investigator']['model'],'gpt-6-sol',
                             'frozen Cycle-start route must never be rewritten')
            self.assertEqual(state['runtime_hot_role_routes']['investigator']['model'],'gpt-6.1-sol')
            self.assertEqual(state['runtime_hot_route_history'][0]['source'],'web_task_role_override')
            self.assertEqual(wf._emit.call_args.args[1],'operator.route.applied')
            wf._apply_pending_task_route(state,'investigator',thread_id='investigator-thread')
            self.assertEqual(wf.backend._ensure_client.return_value.thread_resume.call_count,1,
                             'already-applied preference must not reconfigure thread twice')

    def test_worker_profile_cannot_be_overridden_by_legacy_task_hot_route(self):
        with tempfile.TemporaryDirectory() as temp:
            wf = self.setup_workflow(Path(temp))
            wf.worker_id = 'worker-1'
            wf.routes = build_model_profile('test')
            wf.route_resolver = lambda: dict(wf.routes)
            # Reproduce the observed task-1 conflict: the Worker's new Cycle
            # froze Luna/medium, then the older task-level Sol/high hot edit
            # would otherwise replace the Research Lead route at _call().
            save_task_role_override(wf.spec.research_root, role='research_lead',
                                    model='gpt-6-sol', provider='CodexHTTPS',
                                    effort='high', activation='next_invocation')
            state = {'runtime_role_routes': {
                'research_lead': Workflow._route_snapshot(wf.routes['research_lead'])}}
            wf._apply_pending_task_route(state, 'research_lead', thread_id=None)
            self.assertEqual((wf.routes['research_lead'].model,
                              wf.routes['research_lead'].reasoning_effort),
                             ('gpt-6-luna', 'medium'))
            self.assertNotIn('runtime_hot_role_routes', state)
            wf._emit.assert_not_called()
            wf._persist.assert_not_called()

    def test_paused_worker_resumes_same_cycle_and_switches_only_new_invocations(self):
        from theory_master.worker_runtime import save_worker_profile, save_worker_route
        with tempfile.TemporaryDirectory() as temp:
            wf = self.setup_workflow(Path(temp))
            root = wf.spec.research_root
            worker_dir = root / '.theory-master/workers/worker-1'
            worker_dir.mkdir(parents=True)
            (worker_dir / 'runtime.json').write_text('{}')
            wf.worker_id = 'worker-1'
            save_worker_profile(root, 'worker-1', 'test')
            from theory_master.worker_runtime import resolve_worker_routes
            wf.route_resolver = lambda: resolve_worker_routes(root, 'worker-1')
            state = {'status':'paused', 'runtime_role_routes':{
                role: Workflow._route_snapshot(route) for role,route in wf.routes.items()}}
            wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf.routes['judge'].model, 'gpt-6-sol',
                             'resume must reconstruct immutable Cycle-start route')
            wf._apply_pending_task_route(state, 'judge')
            self.assertEqual(wf.routes['judge'].model, 'gpt-6-luna')
            self.assertEqual(wf.routes['research_lead'].model, 'gpt-6-sol',
                             'other roles are not changed until their own safe invocation')
            self.assertEqual(state['runtime_hot_route_history'][-1]['source'],
                             'worker_role_preference')
            self.assertEqual(state['runtime_role_routes']['judge']['model'], 'gpt-6-sol')
            self.assertEqual(wf._emit.call_args.args[1], 'operator.route.applied')
            # Existing task-level hot edits must NOT regain priority.
            save_task_role_override(root, role='judge', model='gpt-6-sol',
                                    provider='CodexHTTPS', effort='xhigh')
            wf._apply_pending_task_route(state, 'judge')
            self.assertEqual(wf.routes['judge'].model, 'gpt-6-luna')
            # A pending/unfinished native invocation cannot be rebased.
            save_worker_route(root, 'worker-1', role='judge', model='gpt-6-sol',
                              provider='CodexHTTPS', effort='high', activation='next_invocation')
            state['active_invocation_id'] = 'unfinished-native-goal'
            wf._apply_pending_task_route(state, 'judge')
            self.assertEqual(wf.routes['judge'].model, 'gpt-6-luna')
            state['active_invocation_id'] = None
            wf._apply_pending_task_route(state, 'judge')
            self.assertEqual((wf.routes['judge'].model, wf.routes['judge'].reasoning_effort),
                             ('gpt-6-sol', 'high'))
            # Explicit next_cycle edits must keep the frozen route until new Cycle.
            save_worker_route(root, 'worker-1', role='judge', model='gpt-6-luna',
                              provider='CodexHTTPS', effort='medium', activation='next_cycle')
            wf._apply_pending_task_route(state, 'judge')
            self.assertEqual(wf.routes['judge'].model, 'gpt-6-sol')

    def test_unknown_provider_change_on_used_thread_stays_deferred(self):
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            from theory_master.backend_config import task_route_catalog
            catalog = task_route_catalog() + [{'model': 'fixture-model', 'provider': 'FixtureProvider'}]
            with patch('theory_master.backend_config.task_route_catalog', return_value=catalog):
                save_task_role_override(wf.spec.research_root,role='investigator',
                                        model='fixture-model',provider='FixtureProvider',effort='low')
            with patch('theory_master.backend_config.task_route_catalog', return_value=catalog):
                state={}
                wf._apply_pending_task_route(state,'investigator',thread_id='existing-science-thread')
                self.assertEqual(wf.routes['investigator'].provider,'CodexHTTPS')
                self.assertEqual(state['runtime_hot_route_deferred']['investigator']['reason'],
                                 'provider_change_requires_new_thread')
                self.assertEqual(wf._emit.call_args.args[1],'operator.route.deferred')
                wf._apply_pending_task_route(state,'investigator',thread_id='existing-science-thread')
                self.assertEqual(wf._emit.call_count,1,'duplicate unchanged deferred state must not spam events')
                # An unused role may create its FIRST thread on another Provider.
                wf._apply_pending_task_route(state,'investigator',thread_id=None)
                self.assertEqual(wf.routes['investigator'].provider,'FixtureProvider')
                self.assertNotIn('investigator',state['runtime_hot_route_deferred'])

    def test_operator_can_keep_original_next_cycle_mode_without_hot_application(self):
        from theory_master.backend_config import read_task_role_activations,read_task_role_overrides
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            save_task_role_override(wf.spec.research_root,role='investigator',
                                    model='gpt-6.1-sol',provider='CodexHTTPS',effort='low',
                                    activation='next_cycle')
            self.assertEqual(read_task_role_activations(wf.spec.research_root)['investigator'],'next_cycle')
            state={}
            wf._apply_pending_task_route(state,'investigator',thread_id='existing')
            self.assertEqual(wf.routes['investigator'].model,'gpt-6-sol')
            wf._emit.assert_not_called()
            # The next Cycle's route resolver still uses the saved model.
            from theory_master.backend_config import resolve_routes
            after=resolve_routes(profile='advanced',env={},task_route_override_root=wf.spec.research_root)
            self.assertEqual(after['investigator'].model,'gpt-6.1-sol')
            self.assertEqual(after['investigator'].reasoning_effort,'low')
            # Saving another role must retain this role's timing.
            save_task_role_override(wf.spec.research_root,role='judge',
                                    model='gpt-6-sol',provider='CodexHTTPS',effort='medium')
            self.assertEqual(read_task_role_activations(wf.spec.research_root)['investigator'],'next_cycle')

    def test_legacy_task_override_without_activation_still_reads(self):
        from theory_master.backend_config import read_task_role_activations,read_task_role_overrides
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            old={'schema':'theory-master/web-role-routes/v1','roles':{'investigator':{
                 'model':'gpt-6.1-sol','provider':'CodexHTTPS','reasoning_effort':'low'}}}
            (wf.spec.research_root/'.theory-master'/'web-role-routes.json').write_text(json.dumps(old))
            self.assertEqual(read_task_role_overrides(wf.spec.research_root)['investigator'].model,'gpt-6.1-sol')
            self.assertEqual(read_task_role_activations(wf.spec.research_root)['investigator'],'next_invocation')
            old['roles']['investigator']['activation']='bogus'
            (wf.spec.research_root/'.theory-master'/'web-role-routes.json').write_text(json.dumps(old))
            with self.assertRaises(ValueError):read_task_role_overrides(wf.spec.research_root)

    def test_explicit_cli_route_has_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            save_task_role_override(wf.spec.research_root,role='investigator',
                                    model='gpt-6.1-sol',provider='CodexHTTPS',effort='low')
            wf.explicit_cli_route_roles=frozenset({'investigator'})
            wf._apply_pending_task_route({},'investigator')
            self.assertEqual(wf.routes['investigator'].model,'gpt-6-sol')
            wf._emit.assert_not_called()

    def test_rebind_failure_fails_closed_with_no_downgrade_or_ledger_misstatement(self):
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            wf.backend.threads['t1']=types.SimpleNamespace(id='t1')
            wf.backend._ensure_client.return_value.thread_resume.side_effect=RuntimeError('SDK refused requested model')
            save_task_role_override(wf.spec.research_root,role='investigator',
                                    model='gpt-6.1-sol',provider='CodexHTTPS',effort='low')
            state={}
            fake_sdk=types.SimpleNamespace(ApprovalMode=types.SimpleNamespace(deny_all='deny'))
            with patch.dict(sys.modules, {'openai_codex':fake_sdk}):
                with self.assertRaisesRegex(RuntimeError,'refused'):
                    wf._apply_pending_task_route(state,'investigator',thread_id='t1')
            self.assertEqual(wf.routes['investigator'].model,'gpt-6-sol')
            self.assertNotIn('runtime_hot_role_routes',state)
            self.assertEqual(wf._emit.call_args.args[1],'operator.route.failed')
            self.assertEqual(wf.backend.threads['t1'].id,'t1')

    def test_recovered_cycle_uses_applied_hot_snapshot_without_rewriting_frozen_start(self):
        with tempfile.TemporaryDirectory() as temp:
            wf=self.setup_workflow(Path(temp))
            initial={role:Workflow._route_snapshot(route) for role,route in wf.routes.items()}
            state={'runtime_role_routes':initial,
                   'runtime_hot_role_routes':{'investigator':{
                      'model':'gpt-6.1-sol','provider':'CodexHTTPS','model_vendor':'openai',
                      'reasoning_effort':'low'}}}
            wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf.routes['investigator'].model,'gpt-6.1-sol')
            self.assertEqual(state['runtime_role_routes']['investigator']['model'],'gpt-6-sol')
            wf.resume_route_overrides={'investigator':RoleRoute('gpt-6-sol','CodexHTTPS',
                                                             model_vendor='openai',reasoning_effort='medium')}
            wf._restore_resumed_cycle_routes(state)
            self.assertEqual(wf.routes['investigator'].model,'gpt-6-sol',
                             'explicit CLI recovery must override stored hot route')

    def test_invocation_checks_saved_route_only_after_reuse_and_before_model_capture(self):
        source=inspect.getsource(InvocationMixin._call)
        self.assertLess(source.index('if reused is not None'),source.index('self._apply_pending_task_route('))
        self.assertLess(source.index('self._apply_pending_task_route('),source.index('route = self.routes[actual]'))
        self.assertIn('self.backend.invoke',source)

    def test_presets_are_scientific_preferences_not_account_fallbacks(self):
        routes=build_model_profile('codex')
        self.assertEqual(routes['investigator'].model,'gpt-6-sol')
        self.assertEqual(routes['investigator'].reasoning_effort,'low')
        for role in ('research_lead','challenger','judge'):
            self.assertEqual(routes[role].model,'gpt-6-sol')
        self.assertEqual(routes['literature'].model,'gpt-6-luna')

if __name__=='__main__':unittest.main()
