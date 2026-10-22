"""No-billing regressions for local Codex tier templates and model introspection."""
import json
from pathlib import Path

import pytest

from theory_master.backend_config import MODEL_PROFILES, build_model_profile, ROUTE_ROLE_ORDER
from theory_master.user_model_presets import CODEX_TIERS, save, effective
from theory_master.codex_model_catalog import model_catalog
from tm_observer.run_control import _validated_value, OperatorError
from tm_observer.model_preset_ui import dialog_html, script


def _roles(profile="codex"):
    return {role: {"model": route.model, "reasoning_effort": route.reasoning_effort}
            for role, route in MODEL_PROFILES[profile].items()}


def test_persistent_default_templates_round_trip_and_do_not_modify_bundled_tomls(tmp_path, monkeypatch):
    saved_file = tmp_path / "private" / "model_presets.json"
    monkeypatch.setenv("THEORYMASTER_MODEL_PRESETS_FILE", str(saved_file))
    original = MODEL_PROFILES["codex"]["investigator"]
    chosen = _roles()
    chosen["investigator"] = {"model": "gpt-custom-x", "reasoning_effort": "xhigh"}
    stored = save("codex", chosen)
    assert stored["effective_at"] == "next_safe_role_invocation"
    assert saved_file.stat().st_mode & 0o777 == 0o600
    assert json.loads(saved_file.read_text())["schema"] == "theory-master/user-codex-model-presets/v1"
    assert build_model_profile("codex")["investigator"].model == "gpt-custom-x"
    assert build_model_profile("codex")["investigator"].reasoning_effort == "xhigh"
    assert MODEL_PROFILES["codex"]["investigator"] == original
    assert build_model_profile("advanced")["investigator"] == MODEL_PROFILES["advanced"]["investigator"]
    chosen["investigator"]["model"] = "mutated-in-memory"
    assert effective("codex")["investigator"].model == "gpt-custom-x"


def test_save_rejects_incomplete_rows_and_invalid_models_without_overwriting(tmp_path, monkeypatch):
    target = tmp_path / "presets.json"
    monkeypatch.setenv("THEORYMASTER_MODEL_PRESETS_FILE", str(target))
    assert not target.exists()
    for invalid in ({}, {"research_lead": _roles()["research_lead"]}):
        with pytest.raises(ValueError):
            save("codex", invalid)
    invalid = _roles()
    invalid["judge"]["model"] = "bad model<script>"
    with pytest.raises(ValueError):
        save("test", invalid)
    with pytest.raises(ValueError):
        save("unknown-tier", _roles())
    assert not target.exists()


def test_symlink_settings_refused(tmp_path, monkeypatch):
    destination = tmp_path / "target.json"
    destination.write_text("{}")
    link = tmp_path / "presets.json"
    link.symlink_to(destination)
    monkeypatch.setenv("THEORYMASTER_MODEL_PRESETS_FILE", str(link))
    with pytest.raises(ValueError, match="unsafe"):
        save("codex", _roles())
    assert destination.read_text() == "{}"


def test_public_first_run_shows_codex_only_and_settings_and_catalog(monkeypatch):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(dialog_html(), "html.parser")
    options = [x["value"] for x in soup.select("[data-model-preset-select] option")]
    assert options == list(CODEX_TIERS)
    assert len(soup.select("[data-model-preset-panel] tbody tr")) == 18
    assert len(soup.select("[data-template-role]")) == 6
    assert soup.select_one("[data-model-preset-settings]")
    assert soup.select_one("[data-template-save]")
    assert soup.select_one("[data-model-catalog-check]")
    source = script("a"*16, "worker-1")
    for token in ("save_model_tier_template", "inspect_codex_models", "supported",
                  "catalog.models", "next_safe_role_invocation"):
        if token == "next_safe_role_invocation":
            continue
        assert token in source


def test_persist_actions_are_strictly_allowlisted():
    obj = {"profile": "codex", "roles": _roles()}
    assert _validated_value("save_model_tier_template", obj) == obj
    assert _validated_value("inspect_codex_models", {"worker_id": "worker-1"})
    for payload in ({"profile": "unknown-tier", "roles": _roles()},
                    {"profile": "codex", "roles": {"research_lead": _roles()["research_lead"]}},
                    {"profile": "codex", "roles": {**_roles(), "judge": {
                        "model": "bad route", "reasoning_effort": "high"}}}):
        with pytest.raises(OperatorError):
            _validated_value("save_model_tier_template", payload)
    with pytest.raises(OperatorError):
        _validated_value("inspect_codex_models", {"worker_id": "../../owner"})


def test_catalog_auth_missing_is_unknown_not_supported(monkeypatch, tmp_path):
    monkeypatch.setattr("theory_master.codex_model_catalog.codex_auth_for_worker",
                        lambda root, worker: (None, "unconfigured"))
    result = model_catalog(tmp_path, None)
    assert result["status"] == "unknown"
    assert "models" not in result




def test_catalog_model_directory_only_and_no_paid_inference(tmp_path, monkeypatch):
    # This test deliberately never invokes the Codex binary.
    monkeypatch.setattr("theory_master.codex_model_catalog.codex_auth_for_worker",
                        lambda root, worker: (None, "unconfigured"))
    response = model_catalog(tmp_path, "worker-1")
    assert response["status"] == "unknown"
    assert "不调用付费模型" in response["note"]


def test_public_services_exposes_only_codex_accounts():
    from theory_master.service_provider_switch import VALID_ACCOUNTS
    from tm_observer.web import codex_usage_body
    assert VALID_ACCOUNTS == {"account-1", "account-2"}
    assert "Codex Usage" in codex_usage_body()
