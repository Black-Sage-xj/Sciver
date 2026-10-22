"""Task-specific UI mode never launches actual scientific research in tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from theory_master.worker_mode import configure_mode, read_mode
from theory_master import worker_runtime


def test_mode_defaults_hidden_and_persists_by_task(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    assert read_mode(a)['enabled'] is False
    a_on = configure_mode(a, enabled=True, desired_agents=8)
    assert a_on['enabled'] and a_on['desired_agents'] == 8
    assert read_mode(b)['enabled'] is False
    assert configure_mode(a, enabled=False, desired_agents=8)['enabled'] is False
    assert read_mode(a)['desired_agents'] == 8


@pytest.mark.parametrize('enabled,n', [(True, 0), (True, 1025), (1, 3), (True, True), (False, 2.2)])
def test_invalid_modes_rejected(tmp_path, enabled, n):
    with pytest.raises(ValueError):
        configure_mode(tmp_path, enabled=enabled, desired_agents=n)


def test_scheduler_rejects_wrong_python_before_claiming_science(monkeypatch, tmp_path):
    import importlib.util
    from theory_master.backend_config import ACCOUNT_PROVIDERS
    from theory_master import backends
    root = tmp_path / 'task'
    configure_mode(root, enabled=True, desired_agents=2)
    config_path = root / 'theory-master.config.json'
    config_path.write_text('{}', encoding='utf-8')
    spec = SimpleNamespace(research_root=root, config_path=config_path)
    monkeypatch.setattr(worker_runtime, 'runtime_health', lambda _: {'active': False})
    monkeypatch.setattr(worker_runtime, 'scheduler_status', lambda _: {'process_alive': False})
    provider = next(iter(ACCOUNT_PROVIDERS))
    monkeypatch.setattr(backends, 'resolve_routes', lambda **kwargs: {'research_lead': SimpleNamespace(provider=provider)})
    actual_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec',
                        lambda name: None if name == 'openai_codex' else actual_find_spec(name))
    with pytest.raises(RuntimeError, match='launcher Python'):
        worker_runtime.start_scheduler(spec, slots=2)
    persisted = worker_runtime._read_json(root / '.theory-master' / 'worker_scheduler.json')
    assert persisted['status'] == 'FAILED'
    assert persisted['pid'] is None
    assert 'launcher Python' in persisted['error_detail']


def test_scheduler_configuration_blocks_launch_when_off(monkeypatch, tmp_path):
    root = tmp_path / 'task'
    spec = SimpleNamespace(research_root=root)
    configure_mode(root, enabled=False, desired_agents=6)
    with pytest.raises(ValueError, match='OFF'):
        worker_runtime.start_scheduler(spec, slots=2)
    configure_mode(root, enabled=True, desired_agents=6)
    monkeypatch.setattr(worker_runtime, 'runtime_health', lambda _: {'active': True})
    with pytest.raises(ValueError, match='legacy Master'):
        worker_runtime.start_scheduler(spec, slots=6)
