"""Offline account switching preserves tiers and rolls back failed writes."""
from unittest.mock import patch
import pytest
from theory_master import service_provider_switch as switching

@pytest.fixture
def selection(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    provider = tmp_path / "provider"
    profile.write_text("codex\n")
    provider.write_text("codex\n")
    monkeypatch.setenv("THEORYMASTER_PROFILE_FILE", str(profile))
    monkeypatch.setenv("THEORYMASTER_MODEL_PROVIDER_FILE", str(provider))
    monkeypatch.delenv("THEORYMASTER_PROFILE", raising=False)
    monkeypatch.delenv("THEORYMASTER_MODEL_PROVIDER", raising=False)
    active = ["1"]
    monkeypatch.setattr(switching, "current_codex_account", lambda: active[0])
    def switch(account, **kwargs):
        active[0] = account[-1]
    monkeypatch.setattr("tm_observer.codex_usage.switch_active_account", switch)
    return profile, provider, active

def test_switch_preserves_tier(selection):
    profile, provider, active = selection
    result = switching.switch_service_provider("account-2")
    assert result["active_account"] == "account-2"
    assert result["tier"] == 2
    assert result["effective_at"] == "next_worker_cycle"
    assert profile.read_text() == "codex\n"
    assert provider.read_text() == "codex\n"
    assert active == ["2"]

def test_write_failure_restores_account_and_configuration(selection):
    profile, provider, active = selection
    original = switching._write_private
    failed = False
    def fail_once(path, value):
        nonlocal failed
        if path == provider and not failed:
            failed = True
            raise OSError("fixture disk failure")
        original(path, value)
    with patch.object(switching, "_write_private", side_effect=fail_once):
        with pytest.raises(switching.SelectionConflict, match="已恢复"):
            switching.switch_service_provider("account-2")
    assert active == ["1"]
    assert profile.read_text() == provider.read_text() == "codex\n"

def test_unknown_selection_rejected_before_writes(selection):
    profile, provider, active = selection
    with pytest.raises(ValueError):
        switching.switch_service_provider("unregistered-account")
    assert active == ["1"]
    assert profile.read_text() == provider.read_text() == "codex\n"
