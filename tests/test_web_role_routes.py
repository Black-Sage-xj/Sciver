"""Persistent per-task Web model/effort controls, next-run only.

All files in these tests are disposable temporary fixtures; no real task is
reconfigured or resumed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from theory_master.backend_config import (
    REASONING_EFFORTS, task_route_catalog, read_task_role_overrides,
    save_task_role_override, resolve_routes,
)


class TaskRoleOverrideTests(unittest.TestCase):
    def test_next_run_reads_role_specific_model_and_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            before=resolve_routes(profile='test', env={}, task_route_override_root=root)
            original=before['investigator']
            out=save_task_role_override(root,role='investigator',model='gpt-6.1-sol',provider='CodexHTTPS',effort='high')
            self.assertEqual(out['role'],'investigator')
            selected=resolve_routes(profile='test', env={}, task_route_override_root=root)
            self.assertEqual(selected['investigator'].model,'gpt-6.1-sol')
            self.assertEqual(selected['investigator'].provider,'CodexHTTPS')
            self.assertEqual(selected['investigator'].reasoning_effort,'high')
            self.assertEqual(selected['judge'],before['judge'])
            # The original immutable RoleRoute object is not hot-updated.
            self.assertEqual(before['investigator'],original)
            # An explicit CLI route must outrank a previously saved Web edit.
            explicit=resolve_routes(profile='test',env={},task_route_override_root=root,
                                    role_specs=['investigator=gpt-6-luna'])
            self.assertEqual(explicit['investigator'].model,'gpt-6-luna')
            self.assertNotEqual(explicit['investigator'],selected['investigator'])
            saved=read_task_role_overrides(root)
            self.assertEqual(saved['investigator'].reasoning_effort,'high')
            # Separate tasks cannot read each other's configuration.
            other=root/'other'
            (other/'.theory-master').mkdir(parents=True)
            self.assertEqual(read_task_role_overrides(other),{})

    def test_allowlist_and_existing_selections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            first=save_task_role_override(root,role='research_lead',model='gpt-6-sol',provider='CodexHTTPS',effort='xhigh')
            second=save_task_role_override(root,role='judge',model='gpt-6-luna',provider='CodexHTTPS',effort='low')
            self.assertEqual(len(read_task_role_overrides(root)),2)
            self.assertEqual(set(REASONING_EFFORTS),{'low','medium','high','xhigh'})
            for entry in (
                {'role':'root', 'model':'gpt-6-sol','provider':'CodexHTTPS','effort':'medium'},
                {'role':'judge','model':'unknown','provider':'CodexHTTPS','effort':'medium'},
                {'role':'judge','model':'gpt-6-sol','provider':'UnknownProvider','effort':'medium'},
                {'role':'judge','model':'gpt-6-sol','provider':'CodexHTTPS','effort':'ultra'},
            ):
                with self.assertRaises(ValueError):save_task_role_override(root,**entry)
            self.assertEqual(len(read_task_role_overrides(root)),2)
            self.assertGreaterEqual(len(task_route_catalog()),3)

    def test_malformed_override_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'.theory-master'
            folder.mkdir()
            (folder/'web-role-routes.json').write_text('{"schema":"bad","roles":{}}')
            with self.assertRaises(ValueError):read_task_role_overrides(root)
            with self.assertRaises(ValueError):resolve_routes(profile='test',env={},task_route_override_root=root)


class OperatorRoleRouteTests(unittest.TestCase):
    def test_sdk_worker_persists_only_intended_role_without_launching(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from theory_master.operator_control import dispatch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.theory-master').mkdir()
            config = root / 'theory-master.config.json'
            config.write_text('{}', encoding='utf-8')
            fake = SimpleNamespace(master_id=1, research_root=root)
            with patch('theory_master.operator_control.TaskBinding.load', return_value=SimpleNamespace(config_path=config)):
                with patch('theory_master.operator_control.TaskSpec.from_config', return_value=fake):
                    result = dispatch({'task_root':str(root),'research_root':str(root),'run_id':1,
                                       'action':'set_role_route','value':{
                                           'role':'reporter','model':'gpt-6-luna',
                                           'provider':'CodexHTTPS','reasoning_effort':'xhigh'}})
            self.assertEqual(result['effective_at'], 'next_safe_agent_invocation_if_supported_else_next_run_or_resume')
            self.assertFalse(result['applies_to_current_turn'])
            saved = read_task_role_overrides(root)
            self.assertEqual(saved['reporter'].reasoning_effort,'xhigh')
            self.assertEqual(list(saved), ['reporter'])
            # Both timing choices remain user-selectable. Next Cycle means no
            # hot route change even on a Runner supporting safe-boundary edits.
            with patch('theory_master.operator_control.TaskBinding.load', return_value=SimpleNamespace(config_path=config)):
                with patch('theory_master.operator_control.TaskSpec.from_config', return_value=fake):
                    delayed=dispatch({'task_root':str(root),'research_root':str(root),'run_id':1,
                                      'action':'set_role_route','value':{
                                          'role':'reporter','model':'gpt-6-luna',
                                          'provider':'CodexHTTPS','reasoning_effort':'xhigh',
                                          'activation':'next_cycle'}})
            self.assertEqual(delayed['effective_at'],'next_cycle_boundary')
            from theory_master.backend_config import read_task_role_activations
            self.assertEqual(read_task_role_activations(root)['reporter'],'next_cycle')


class NextCycleRouteReloadTests(unittest.TestCase):
    def test_current_cycle_frozen_then_next_boundary_reloads_entire_sdk_route(self):
        from unittest.mock import Mock
        from theory_master.abc_pipeline import AgentSemanticBackend, FallbackSemanticBackend
        from theory_master.codex_backend import CodexBackend
        from theory_master.workflow import Workflow

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".theory-master").mkdir()
            def resolver():
                return resolve_routes(profile="test", env={}, task_route_override_root=root)

            initial = resolver()
            backend = CodexBackend.__new__(CodexBackend)
            # Route reload is independent of the operator-selected account ceiling.
            backend.prepare_cycle_account = Mock(side_effect=lambda routes: dict(routes))
            backend.routes = dict(initial)
            original_semantic = AgentSemanticBackend(backend, model=initial["research_lead"].model,
                                                     provider=initial["research_lead"].provider)
            collector = Mock()
            workflow = Workflow.__new__(Workflow)
            workflow.spec = type("Spec", (), {"research_root": root})()
            workflow.routes = dict(initial)
            workflow.backend = backend
            workflow.collector = collector
            workflow.route_resolver = resolver
            workflow.semantic_backend = FallbackSemanticBackend(Mock(), original_semantic)

            save_task_role_override(root, role="research_lead", model="gpt-6.1-sol",
                                    provider="CodexHTTPS", effort="high")
            save_task_role_override(root, role="investigator", model="gpt-6-sol",
                                    provider="CodexHTTPS", effort="low")
            # During the active Cycle, existing Workflow and Backend keep their old model.
            self.assertEqual(workflow.routes, initial)
            self.assertEqual(backend.routes, initial)
            self.assertEqual(original_semantic.model, initial["research_lead"].model)

            workflow._refresh_next_cycle_routes()
            self.assertEqual(workflow.routes["research_lead"].model, "gpt-6.1-sol")
            self.assertEqual(workflow.routes["investigator"].reasoning_effort, "low")
            self.assertEqual(backend.routes, workflow.routes)
            self.assertEqual(original_semantic.model, "gpt-6.1-sol")
            self.assertEqual(original_semantic.provider, "CodexHTTPS")
            collector.set_routes.assert_called_once_with(workflow.routes)

            # Identical preferences never trigger redundant collector/model changes.
            workflow._refresh_next_cycle_routes()
            collector.set_routes.assert_called_once()

    def test_frozen_resume_does_not_reload_and_new_cycle_does(self):
        from types import SimpleNamespace
        from unittest.mock import patch, Mock
        from theory_master.workflow import Workflow

        class BoundaryReached(Exception):
            pass

        wf = Workflow.__new__(Workflow)
        wf.invocation_cycle_index = 0
        wf.spec = SimpleNamespace(research_root=Path("/tmp/mock"), goal_id="mock-goal", task_mode=True)

        # Resume an existing Cycle: no route re-resolution before binding threads.
        with patch.object(Workflow, "_refresh_next_cycle_routes") as refresh:
            with patch.object(Workflow, "_bind_cycle", side_effect=BoundaryReached):
                with self.assertRaises(BoundaryReached):
                    wf._execute_cycle({"cycle_id": "5"})
            refresh.assert_not_called()

        # First action of a new Cycle is route resolution, before allocating an ID.
        wf.invocation_cycle_index = 0
        with patch.object(Workflow, "_refresh_next_cycle_routes") as refresh:
            with patch("theory_master.workflow.make_run_id", side_effect=BoundaryReached):
                with self.assertRaises(BoundaryReached):
                    wf._execute_cycle(None)
            refresh.assert_called_once_with()

    def test_explicit_cli_role_route_keeps_priority_on_each_boundary(self):
        from types import SimpleNamespace
        from theory_master.workflow import Workflow
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".theory-master").mkdir()
            def resolver():
                return resolve_routes(profile="test", env={}, task_route_override_root=root,
                                      role_specs=["investigator=gpt-6-luna"])
            wf = Workflow.__new__(Workflow)
            wf.routes = resolver()
            wf.route_resolver = resolver
            wf.backend = SimpleNamespace()
            wf.semantic_backend = SimpleNamespace()
            wf.collector = None
            save_task_role_override(root, role="investigator", model="gpt-6.1-sol",
                                    provider="CodexHTTPS", effort="high")
            wf._refresh_next_cycle_routes()
            self.assertEqual(wf.routes["investigator"].model, "gpt-6-luna")

    def test_resume_restores_prior_cycle_routes_before_applying_future_changes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from theory_master.workflow import Workflow
        from theory_master.codex_backend import CodexBackend
        from theory_master.abc_pipeline import AgentSemanticBackend

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".theory-master").mkdir()
            cycle = root / "55"
            cycle.mkdir()
            old = resolve_routes(profile="test", env={}, task_route_override_root=root)
            # Legacy monitoring snapshots have the six Agent routes but no
            # explicit runtime_role_routes in research_state.json.
            legacy = {"agents": {
                role: {"model": route.model, "provider": route.provider,
                       "reasoning_effort": route.reasoning_effort,
                       "model_vendor": route.model_vendor}
                for role, route in old.items()
            }}
            import json
            (cycle / "run_state.json").write_text(json.dumps(legacy), encoding="utf-8")
            save_task_role_override(root, role="research_lead",
                                    model="gpt-6.1-sol", provider="CodexHTTPS", effort="high")
            def resolver():
                return resolve_routes(profile="test", env={}, task_route_override_root=root)
            future = resolver()
            backend = CodexBackend.__new__(CodexBackend)
            # Route reload is independent of the operator-selected account ceiling.
            backend.prepare_cycle_account = Mock(side_effect=lambda routes: dict(routes))
            backend.routes = dict(future)
            collector = Mock()
            wf = Workflow.__new__(Workflow)
            wf.routes = dict(future)
            wf.backend = backend
            wf.collector = collector
            wf.route_resolver = resolver
            wf.cycle_dir = cycle
            wf.semantic_backend = AgentSemanticBackend(backend, model=future["research_lead"].model,
                                                       provider=future["research_lead"].provider)
            # Existing Cycle Resume keeps its original scientific Agent route.
            wf._restore_resumed_cycle_routes({})
            self.assertEqual(wf.routes["research_lead"].model, old["research_lead"].model)
            self.assertEqual(backend.routes["research_lead"].model, old["research_lead"].model)
            self.assertEqual(wf.semantic_backend.model, old["research_lead"].model)
            # The next new Cycle rereads the latest saved task preference.
            wf._refresh_next_cycle_routes()
            self.assertEqual(wf.routes["research_lead"].model, "gpt-6.1-sol")

            # New Cycle always has the authoritative per-Cycle snapshot;
            # it must outrank any later-mutated legacy run_state projection.
            wf._restore_resumed_cycle_routes({"runtime_role_routes": {
                role: {"model": route.model, "provider": route.provider,
                       "reasoning_effort": route.reasoning_effort,
                       "model_vendor": route.model_vendor}
                for role, route in old.items()
            }})
            self.assertEqual(wf.routes["research_lead"].model, old["research_lead"].model)
