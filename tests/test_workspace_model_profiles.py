"""Independent workspaces use bundled profiles without host development overrides."""
from __future__ import annotations

from theory_master import backend_config


def _profile(directory, name, model):
    directory.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        f'[roles.{role}]\nmodel = "{model}"\nprovider = "CodexHTTPS"\nreasoning_effort = "medium"\n'
        for role in backend_config.ROUTE_ROLE_ORDER
    )
    (directory / (name + ".toml")).write_text(text)


def _directories(tmp_path, monkeypatch):
    bundled, local = tmp_path / "bundled", tmp_path / "operator-private"
    _profile(bundled, "test", "bundled-model")
    _profile(local, "test", "operator-override")
    _profile(local, "operator-only", "operator-model")
    monkeypatch.setattr(backend_config, "MODEL_PROFILE_DIR", bundled)
    monkeypatch.setattr(backend_config, "LOCAL_MODEL_PROFILE_DIR", local)
    return bundled, local


def test_isolated_workspace_loads_only_bundled_profiles(tmp_path, monkeypatch):
    _directories(tmp_path, monkeypatch)
    monkeypatch.setenv("SCIVER_ISOLATED_WORKSPACE", "1")
    profiles = backend_config._load_model_profiles()
    assert set(profiles) == {"test"}
    assert all(route.model == "bundled-model" for route in profiles["test"].values())


def test_primary_preserves_local_profile_overrides(tmp_path, monkeypatch):
    _directories(tmp_path, monkeypatch)
    monkeypatch.delenv("SCIVER_ISOLATED_WORKSPACE", raising=False)
    profiles = backend_config._load_model_profiles()
    assert set(profiles) == {"test", "operator-only"}
    assert all(route.model == "operator-override" for route in profiles["test"].values())


def test_isolated_workspace_does_not_open_invalid_private_toml(tmp_path, monkeypatch):
    _bundled, local = _directories(tmp_path, monkeypatch)
    (local / "invalid.toml").write_text("this is not valid TOML = [")
    monkeypatch.setenv("SCIVER_ISOLATED_WORKSPACE", "1")
    assert set(backend_config._load_model_profiles()) == {"test"}
