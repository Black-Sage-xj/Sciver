"""Task preset saves use temporary fixtures and never call a model provider."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from theory_master.backend_config import (
    TASK_ROLE_OVERRIDES_NAME, build_model_profile, read_task_model_profile,
    read_task_role_overrides, resolve_routes, save_task_model_profile,
    save_task_role_override,
)
from theory_master.operator_control import dispatch
from theory_master.tui.web_bridge import project


def test_atomic_task_preset_replaces_roles_and_preserves_other_tasks(tmp_path):
    root = tmp_path / 'task'
    guard = root / '.theory-master'
    guard.mkdir(parents=True)
    save_task_role_override(root, role='judge', model='gpt-6-luna',
                            provider='CodexHTTPS', effort='xhigh')
    configured = save_task_model_profile(root, 'codex')
    assert configured['profile'] == 'codex'
    assert read_task_model_profile(root) == 'codex'
    expected = build_model_profile('codex')
    assert read_task_role_overrides(root) == expected
    assert resolve_routes(profile='test', env={}, task_route_override_root=root) == expected
    saved = guard / TASK_ROLE_OVERRIDES_NAME
    before = saved.read_bytes()
    with pytest.raises(ValueError):
        save_task_model_profile(root, 'unknown')
    assert saved.read_bytes() == before
    assert read_task_role_overrides(tmp_path / 'other') == {}
    assert not list(root.glob('[0-9]*'))
    save_task_role_override(root, role='judge', model='gpt-6-luna',
                            provider='CodexHTTPS', effort='high')
    assert read_task_model_profile(root) == 'codex'
    assert read_task_role_overrides(root)['judge'].model == 'gpt-6-luna'


def test_task_preset_accepts_private_template_models(tmp_path, monkeypatch):
    from theory_master.user_model_presets import save
    monkeypatch.setenv('THEORYMASTER_MODEL_PRESETS_FILE', str(tmp_path / 'template.json'))
    roles = {role: {'model': route.model, 'reasoning_effort': route.reasoning_effort}
             for role, route in build_model_profile('codex').items()}
    roles['judge']['model'] = 'gpt-private-choice'
    save('codex', roles)
    (tmp_path / '.theory-master').mkdir()
    save_task_model_profile(tmp_path, 'codex')
    assert read_task_role_overrides(tmp_path)['judge'].model == 'gpt-private-choice'


def test_sdk_action_saves_without_launch_or_changing_scientific_state(tmp_path):
    (tmp_path / '.theory-master').mkdir()
    config = tmp_path / 'config.json'
    request = {'task_root': str(tmp_path), 'research_root': str(tmp_path), 'run_id': 31,
               'action': 'set_task_profile', 'value': {'profile': 'codex'}}
    with patch('theory_master.operator_control.TaskBinding.load',
               return_value=SimpleNamespace(config_path=config)), \
         patch('theory_master.operator_control.TaskSpec.from_config',
               return_value=SimpleNamespace(research_root=tmp_path, master_id=31)):
        outcome = dispatch(request)
        assert outcome['applies_to_current_turn'] is False
        assert outcome['effective_at'] == 'next_safe_role_invocation'
        assert read_task_model_profile(tmp_path) == 'codex'
        with patch('theory_master.worker_runtime.list_workers',
                   return_value=[{'worker_id': 'worker-1'}]):
            with pytest.raises(ValueError, match='Worker'):
                dispatch(request)
    assert not list(tmp_path.glob('[0-9]*'))


def test_task_projection_shows_saved_tier_and_keeps_running_call_frozen(tmp_path):
    (tmp_path / '.theory-master').mkdir()
    save_task_model_profile(tmp_path, 'codex')
    old = build_model_profile('test')
    state = {'cycle_id': '2', 'status': 'RUNNING', 'execution_active': True,
             'active_invocation_id': 'inv-2-investigator-abcdef',
             'runtime_role_routes': {role: {'model': route.model, 'provider': route.provider,
                                           'reasoning_effort': route.reasoning_effort}
                                     for role, route in old.items()}}
    with patch('theory_master.tui.web_bridge.FileSource') as source:
        source.return_value.snapshot.return_value = state
        source.return_value.run_dir = None
        visible = project(tmp_path)['role_settings']
    assert visible['judge']['preset_profile'] == 'codex'
    assert visible['judge']['model'] == build_model_profile('codex')['judge'].model
    assert visible['judge']['pending'] is None
    current = visible['investigator']
    assert current['model'] == old['investigator'].model
    assert current['pending_reason'] == 'active_invocation'
    assert current['pending']['model'] == build_model_profile('codex')['investigator'].model
    assert state['runtime_role_routes']['judge']['model'] == old['judge'].model
