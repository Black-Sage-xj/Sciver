"""Worker profile is per-role pending until a safe invocation boundary."""
import json
from pathlib import Path
from unittest import mock

import pytest
from theory_master.worker_runtime import (
    save_worker_profile, save_worker_route, resolve_worker_routes, read_worker_profile,
    worker_route_overrides,
)
from theory_master.backend_config import MODEL_PROFILES




@pytest.mark.parametrize('profile', ['test','codex','advanced'])
def test_whole_tier_clears_worker_overrides_and_sets_six_exact_routes(tmp_path, profile):
    root=tmp_path/'research'
    worker=root/'.theory-master/workers/worker-1'
    worker.mkdir(parents=True)
    (worker/'runtime.json').write_text('{}')
    save_worker_route(root,'worker-1',role='research_lead',model='gpt-6-sol',
                      provider='CodexHTTPS',effort='high')
    save_worker_route(root,'worker-1',role='investigator',model='gpt-6-sol',
                      provider='CodexHTTPS',effort='medium')
    result=save_worker_profile(root,'worker-1',profile)
    assert result['effective_at']=='next_safe_role_invocation'
    assert result['cleared_worker_role_overrides']==2
    assert read_worker_profile(root,'worker-1')==profile
    assert worker_route_overrides(root,'worker-1')=={}
    # This test covers the saved Worker profile contract, not the separate
    # global Services provider-switch contract. Isolate that preference so a
    # machine-level account selection cannot rewrite the fixture's routes.
    with mock.patch('theory_master.service_provider_switch.selected_service_preference', return_value=None):
        resolved=resolve_worker_routes(root,'worker-1')
    assert set(resolved)==set(MODEL_PROFILES[profile])
    for role, route in MODEL_PROFILES[profile].items():
        assert resolved[role].model==route.model
        assert resolved[role].reasoning_effort==route.reasoning_effort
        assert resolved[role].provider==route.provider
    assert not (root/'1').exists()
    # Per-role customization remains possible AFTER selecting the preset.
    save_worker_route(root,'worker-1',role='investigator',model='gpt-6-sol',
                      provider='CodexHTTPS',effort='high')
    assert len(worker_route_overrides(root,'worker-1'))==1
    assert len(resolve_worker_routes(root,'worker-1'))==6


def test_worker_role_activation_modes_are_independent_and_legacy_is_cycle_bound(tmp_path):
    from theory_master.worker_runtime import worker_route_activations
    root = tmp_path / 'research'
    worker = root / '.theory-master/workers/worker-1'
    worker.mkdir(parents=True)
    (worker/'runtime.json').write_text('{}')
    saved = save_worker_route(root, 'worker-1', role='judge', model='gpt-6-sol',
                              provider='CodexHTTPS', effort='high', activation='next_invocation')
    assert saved['effective_at'] == 'next_safe_role_invocation'
    assert worker_route_activations(root, 'worker-1') == {'judge': 'next_invocation'}
    save_worker_route(root, 'worker-1', role='research_lead', model='gpt-6-sol',
                      provider='CodexHTTPS', effort='medium', activation='next_cycle')
    assert worker_route_activations(root, 'worker-1') == {
        'judge': 'next_invocation', 'research_lead': 'next_cycle'}
    data = json.loads((worker / 'role_routes.json').read_text())
    data['roles']['judge'].pop('activation')
    (worker / 'role_routes.json').write_text(json.dumps(data))
    assert worker_route_activations(root, 'worker-1')['judge'] == 'next_cycle'
    data['roles']['judge']['activation'] = 'invalid'
    (worker / 'role_routes.json').write_text(json.dumps(data))
    with pytest.raises(ValueError, match='activation'):
        worker_route_overrides(root, 'worker-1')


def test_selected_worker_tier_beats_parent_task_role_overrides(tmp_path):
    from theory_master.backend_config import save_task_role_override
    root=tmp_path/'research'
    worker=root/'.theory-master/workers/worker-2'
    worker.mkdir(parents=True)
    (worker/'runtime.json').write_text('{}')
    save_task_role_override(root,role='investigator',model='gpt-6-sol',
                            provider='CodexHTTPS',effort='high',activation='next_cycle')
    save_worker_profile(root,'worker-2','test')
    # A machine-wide manual Services preference is independent of this test's
    # task-role vs pinned-tier precedence contract.
    with mock.patch('theory_master.service_provider_switch.selected_service_preference', return_value=None):
        resolved=resolve_worker_routes(root,'worker-2')
    assert {role:(route.model,route.reasoning_effort) for role,route in resolved.items()} == {
        role:(route.model,route.reasoning_effort) for role,route in MODEL_PROFILES['test'].items()
    }
    assert all(route.provider=='CodexHTTPS' for route in resolved.values())


def test_invalid_preset_does_not_destroy_old_worker_overrides(tmp_path):
    root=tmp_path/'research'
    worker=root/'.theory-master/workers/worker-1'
    worker.mkdir(parents=True)
    (worker/'runtime.json').write_text('{}')
    save_worker_route(root,'worker-1',role='judge',model='gpt-6-sol',
                      provider='CodexHTTPS',effort='high')
    before=worker_route_overrides(root,'worker-1')
    with pytest.raises(ValueError):
        save_worker_profile(root,'worker-1','not-a-profile')
    assert worker_route_overrides(root,'worker-1')==before
    assert read_worker_profile(root,'worker-1') is None


def test_operator_profile_action_changes_only_selected_worker_at_safe_boundary(tmp_path):
    from theory_master.operator_control import dispatch, ControlRejected
    from types import SimpleNamespace
    root=tmp_path/'research'
    root.mkdir()
    config=root/'theory-master.config.json'
    config.write_text('{}')
    worker1=root/'.theory-master/workers/worker-1'
    worker2=root/'.theory-master/workers/worker-2'
    worker1.mkdir(parents=True)
    worker2.mkdir(parents=True)
    (worker1/'runtime.json').write_text('{}')
    (worker2/'runtime.json').write_text('{}')
    save_worker_profile(root,'worker-1','codex')
    operation={'action':'set_worker_profile','task_root':str(root),
               'research_root':str(root),'run_id':1,
               'value':{'worker_id':'worker-2','profile':'advanced'}}
    with mock.patch('theory_master.operator_control.TaskBinding.load',
                    return_value=SimpleNamespace(config_path=config)),\
         mock.patch('theory_master.operator_control.TaskSpec.from_config',
                    return_value=SimpleNamespace(research_root=root,master_id=1)):
        outcome=dispatch(operation)
        assert outcome['configured']['effective_at']=='next_safe_role_invocation'
        assert outcome['applies_to_current_turn'] is False
        assert read_worker_profile(root,'worker-2')=='advanced'
        assert read_worker_profile(root,'worker-1')=='codex'
        with pytest.raises(ControlRejected):
            dispatch({**operation,'value':{'worker_id':'worker-2','profile':'unknown'}})
