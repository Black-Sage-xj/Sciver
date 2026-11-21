"""Offline pinning and quota recovery of an original single-task Native Goal."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from theory_master import supervisor
from theory_master.backend_config import DEFAULT_ROUTES
from theory_master.codex_accounts import account_rate_limits
from theory_master.codex_backend import CodexBackend
from theory_master.quota_auto_adjust import read_controller_state, write_quota_auto_adjust_setting
from theory_master.retained_goal_quota import (
    boot_goal_account_binding,
    capture_goal_account_origin,
    goal_rollout_identity,
    prepare_boot_goal_home,
    prepare_retained_goal_home,
    retained_goal_binding,
    verify_retained_quota_launch,
)
from theory_master.runtime_manager import load_registry, update_registry


def auth(number, refresh):
    return json.dumps({"tokens": {"account_id": f"fixture-{number}", "refresh_token": refresh}})


@pytest.fixture
def retained_task(tmp_path, monkeypatch):
    root = tmp_path / "research"
    runtime = root / ".theory-master"
    runtime.mkdir(parents=True)
    (runtime / "env").write_text("THEORY_MASTER_QUOTA_AUTO_ADJUST=1\n")
    cycle = root / "1"
    cycle.mkdir()
    (cycle / ".theory-master-cycle").write_text("theory-master/v2\n")
    state = {
        "cycle_id": "1", "status": "paused", "phase": "provider_quota_exhausted",
        "execution_active": False, "resumable": True, "runtime_codex_account": "2",
        "hypothesis_lock": {"status": "locked", "id": "H-0001"},
        "native_goal": {"status": "usageLimited", "thread_id": "original-thread"},
        "recovery": {"action": "resume", "code": "provider_quota_exhausted",
                     "provider": "CodexHTTPS", "codex_account": "2"},
    }
    layout = {name: tmp_path / name for name in ("1", "2", "runtime", "root")}
    for directory in layout.values():
        directory.mkdir(mode=0o700)
    for number in ("1", "2"):
        file = layout[number] / "auth.json"
        file.write_text(auth(number, "initial-credential"))
        file.chmod(0o600)
    (layout["runtime"] / "config.toml").write_text("")
    (layout["runtime"] / "auth.json").symlink_to(layout["2"] / "auth.json")
    rollout = layout["runtime"] / "rollout.jsonl"
    rollout.write_text("fixture history")
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    monkeypatch.delenv("THEORYMASTER_WORKER_PINNED_ACCOUNT", raising=False)
    monkeypatch.delenv("THEORYMASTER_WORKER_CODEX_HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(layout["runtime"]))
    monkeypatch.setattr("theory_master.retained_goal_quota.read_layout", lambda: layout)
    state["native_goal_quota_origin"] = {
        **capture_goal_account_origin("2"), **goal_rollout_identity(str(rollout)), "thread_id": "original-thread",
        "provider": "CodexHTTPS", "objective_sha256": "a" * 64, "created_at": 1,
    }
    (cycle / "research_state.json").write_text(json.dumps(state))
    return root, cycle, state, layout


@pytest.mark.parametrize("finishes", [True, False])
def test_quota_probe_and_managed_launch_keep_same_private_identity(retained_task, finishes):
    root, cycle, state, layout = retained_task
    update_registry(root, state="LAUNCHING", launch_id="quota-fixture")
    calls = []
    child = MagicMock(pid=99_999_999)

    def probe(account, *, auth_path):
        assert account == "2"
        assert auth_path.read_text() == auth("2", "initial-credential")
        auth_path.write_text(auth("2", "private-refresh"))
        return {"ok": True, "ordinary_usage_allowed": True}

    def launch(argv, **kwargs):
        approval = json.loads(argv[argv.index("--retained-quota") + 1])
        selected = verify_retained_quota_launch(root, approval, managed_child=True)
        assert selected.account == "2"
        assert selected.auth_path.read_text() == auth("2", "private-refresh")
        assert selected.home.is_relative_to(layout["root"]) and not selected.home.is_relative_to(root)
        assert selected.sqlite_home == layout["runtime"]
        assert kwargs["env"].get("THEORYMASTER_QUOTA_PROVIDER_FALLBACK") is None
        calls.append(approval)
        return child

    def finish(**kwargs):
        if finishes:
            (cycle / "research_state.json").write_text(json.dumps({**state, "status": "over"}))
        return 0

    child.wait.side_effect = finish
    with (
        patch("theory_master.codex_accounts.account_rate_limits", side_effect=probe) as rate_limits,
        patch("theory_master.codex_accounts.selected_account", return_value="1"),
        patch("theory_master.codex_accounts.use_account") as switch,
        patch.object(supervisor, "_quota_snapshots") as shared_probe,
        patch.object(supervisor, "_notify_supervisor_terminal"),
        patch.object(supervisor.subprocess, "Popen", side_effect=launch),
    ):
        assert supervisor.main([
            "--task-root", str(root), "--operation", "resume", "--launch-id", "quota-fixture",
        ]) == 0
    assert len(calls) == rate_limits.call_count == 1
    switch.assert_not_called()
    shared_probe.assert_not_called()
    assert (layout["2"] / "auth.json").read_text() == auth("2", "initial-credential")
    assert load_registry(root)["state"] == "STOPPED"
    if not finishes:
        assert read_controller_state(root)["provider_fallback_blocked"] == "retained_goal_repaused_without_activity"


def test_quota_rpc_uses_the_same_private_credential_home(retained_task):
    root, cycle, state, _ = retained_task
    home = prepare_retained_goal_home(root, retained_goal_binding(cycle, state), create=True)
    process = MagicMock()
    process.poll.return_value = 0
    process.stdout.readline.return_value = json.dumps({"id": 2, "result": {"ordinaryUsageAllowed": True}})
    with (
        patch("theory_master.codex_accounts._codex_binary", return_value=Path("/fixture/codex")),
        patch("theory_master.codex_accounts.subprocess.Popen", return_value=process) as launch,
        patch("theory_master.codex_accounts.select.select", return_value=([process.stdout], [], [])),
    ):
        assert account_rate_limits("2", auth_path=home.auth_path)["ordinary_usage_allowed"] is True
    env = launch.call_args.kwargs["env"]
    assert env["CODEX_HOME"] == str(home.home)
    assert env["CODEX_SQLITE_HOME"] != str(home.sqlite_home)
    assert "sqlite_home=" + json.dumps(env["CODEX_SQLITE_HOME"]) in launch.call_args.args[0]


@pytest.mark.parametrize("foreign_account", [None, "1"])
def test_unknown_quota_or_mismatched_account_stays_paused(retained_task, foreign_account):
    root, cycle, state, _ = retained_task
    if foreign_account:
        state["recovery"]["codex_account"] = foreign_account
        (cycle / "research_state.json").write_text(json.dumps(state))
    with (
        patch("theory_master.codex_accounts.account_rate_limits", return_value={"ok": False}) as rate_limits,
        patch("theory_master.codex_accounts.selected_account", return_value="1"),
        patch.object(supervisor, "_activate_quota_account") as activate,
        patch.object(supervisor, "_notify_verified_quota_wait"),
        patch.object(supervisor.time, "sleep", side_effect=lambda _: write_quota_auto_adjust_setting(root, False)),
    ):
        assert supervisor._wait_for_quota(root) is None
    assert rate_limits.call_count == (0 if foreign_account else 1)
    activate.assert_not_called()


def test_stale_or_unmanaged_launch_is_rejected_before_startup(retained_task):
    root, cycle, state, _ = retained_task
    binding = retained_goal_binding(cycle, state)
    home = prepare_retained_goal_home(root, binding, create=True)
    approval = {"cycle": cycle.name, "binding": binding,
                "sqlite_home": str(home.sqlite_home), "auth_sha256": home.verify_auth()}
    with pytest.raises(ValueError, match="managed Supervisor authorization"):
        verify_retained_quota_launch(root, approval, managed_child=True)
    home.auth_path.write_text(auth("2", "newer-private-credential"))
    with pytest.raises(ValueError, match="credential or history changed"):
        verify_retained_quota_launch(root, approval)
    home.auth_path.write_text(auth("1", "wrong-identity"))
    with pytest.raises(ValueError, match="original Goal identity"):
        prepare_retained_goal_home(root, binding, create=True)


def test_legacy_slot_recredentialing_and_replaced_history_cannot_grant_resume(retained_task):
    root, cycle, state, layout = retained_task
    binding = retained_goal_binding(cycle, state)
    historical = {key: value for key, value in state.items() if key != "native_goal_quota_origin"}
    assert retained_goal_binding(cycle, historical) is None
    (layout["2"] / "auth.json").write_text(auth("1", "replacement-in-slot-two"))
    with pytest.raises(ValueError, match="Stored account changed"):
        prepare_retained_goal_home(root, binding, create=True)
    runtime = layout["runtime"]
    runtime.rename(runtime.with_name("old-runtime"))
    runtime.mkdir()
    (runtime / "config.toml").write_text("")
    with pytest.raises(ValueError, match="SQLite history has changed"):
        prepare_retained_goal_home(root, binding, create=True)


def test_original_transcript_may_append_but_not_be_rewritten(retained_task):
    root, cycle, state, layout = retained_task
    binding = retained_goal_binding(cycle, state)
    rollout = layout["runtime"] / "rollout.jsonl"
    with rollout.open("a") as stream:
        stream.write("\nretained attachment activity")
    home = prepare_retained_goal_home(root, binding, create=True)
    assert home.verify_auth()
    rollout.write_text("rewritten native history")
    with pytest.raises(ValueError, match="transcript has changed"):
        home.verify_auth()


def test_codex_client_uses_pinned_auth_and_original_sqlite_without_global_selection(retained_task):
    root, cycle, state, _ = retained_task
    home = prepare_retained_goal_home(root, retained_goal_binding(cycle, state), create=True)
    backend = CodexBackend(root, routes=DEFAULT_ROUTES, retained_goal_home=home)
    with (
        patch.object(backend, "_verify_provider_runtime"),
        patch.object(backend, "_run_network_guard"),
        patch.object(backend, "_runtime_config", return_value=((), {})),
        patch.object(backend, "_sdk_environment", return_value={}),
        patch("openai_codex.Codex") as codex,
        patch("theory_master.codex_accounts.account_switch_lock") as global_lock,
        patch.object(backend, "_ensure_openai_https_provider_registered") as shared_config,
    ):
        backend._ensure_client()
    config = codex.call_args.args[0]
    assert config.env["CODEX_HOME"] == str(home.home)
    assert config.env["CODEX_SQLITE_HOME"] == str(home.sqlite_home)
    assert 'cli_auth_credentials_store="file"' in config.config_overrides
    assert backend.client_account == "2"
    global_lock.assert_not_called()
    shared_config.assert_not_called()


def as_boot_goal(state):
    # The durable ledger can record newer usage than the last state projection.
    # Boot uses their identical immutable Goal identity and keeps the counters.
    state.update(status="active", execution_active=True, hypothesis_locked=True, recovery={})
    state["runtime_role_routes"] = {"investigator": {"provider": "CodexHTTPS", "model": "frozen-model"}}
    identity = {
        "thread_id": "original-thread", "objective_sha256": "a" * 64, "created_at": 1,
        "token_budget": 500, "tokens_used": 2, "time_used_seconds": 5, "status": "active",
    }
    state["native_goal"] = {"status": "active", "thread_id": "original-thread", "origin_identity": identity}
    return {"thread_id": identity["thread_id"], "objective_sha256": identity["objective_sha256"],
            "persistent_goal": {**identity, "tokens_used": 42, "time_used_seconds": 7}}


def test_boot_binding_uses_original_private_account_without_switch_or_quota_probe(retained_task, monkeypatch):
    root, cycle, state, layout = retained_task
    origin = as_boot_goal(state)
    current_auth = layout["runtime"] / "auth.json"
    current_auth.unlink()
    current_auth.symlink_to(layout["1"] / "auth.json")
    account2 = layout["2"] / "auth.json"
    account2.write_text(auth("2", "legitimate-refresh-same-identity"))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(layout["1"]))

    with (
        patch("theory_master.codex_accounts.account_switch_lock") as lock,
        patch("theory_master.codex_accounts.selected_account") as selector,
        patch("theory_master.codex_accounts.account_rate_limits") as quota_probe,
    ):
        binding = boot_goal_account_binding(cycle, state, origin)
        assert binding is not None
        assert binding["provider"] == "CodexHTTPS"
        assert binding["model"] == "frozen-model"
        assert binding["codex_home"] == str(layout["runtime"])
        assert not (layout["root"] / "retained-goal-auth").exists()
        home = prepare_boot_goal_home(root, binding)
        assert home.auth_path.read_text() == auth("2", "legitimate-refresh-same-identity")
        assert home.home.is_relative_to(layout["root"])
        assert home.verify_auth()
        assert home.sqlite_home == layout["runtime"]
    assert current_auth.readlink() == layout["1"] / "auth.json"
    lock.assert_not_called()
    selector.assert_not_called()
    quota_probe.assert_not_called()


@pytest.mark.parametrize("change", ["credential", "config", "rollout", "legacy_origin", "model_provider",
                                    "different_goal", "quota_state"])
def test_boot_binding_rejects_changed_source_or_protected_goal(retained_task, change):
    _, cycle, state, layout = retained_task
    origin = as_boot_goal(state)
    if change == "credential":
        (layout["2"] / "auth.json").write_text(auth("1", "foreign-account-in-slot-2"))
    elif change == "config":
        (layout["runtime"] / "config.toml").write_text("# changed source after the original start")
    elif change == "rollout":
        (layout["runtime"] / "rollout.jsonl").write_text("different original rollout prefix")
    elif change == "legacy_origin":
        state["native_goal_quota_origin"].pop("codex_config_sha256")
    elif change == "model_provider":
        state["runtime_hot_role_routes"] = {"investigator": {"provider": "Codex", "model": "frozen-model"}}
    elif change == "different_goal":
        origin["persistent_goal"]["created_at"] = 2
    elif change == "quota_state":
        state["native_goal"]["status"] = "usageLimited"

    assert boot_goal_account_binding(cycle, state, origin) is None


def test_boot_rechecks_private_config_and_source_before_using_credential(retained_task):
    root, cycle, state, layout = retained_task
    binding = boot_goal_account_binding(cycle, state, as_boot_goal(state))
    assert binding is not None
    home = prepare_boot_goal_home(root, binding)
    (home.home / "config.toml").write_text("# changed private provider configuration")
    with pytest.raises(ValueError, match="Retained Codex configuration changed"):
        home.verify_auth()
    (layout["runtime"] / "config.toml").write_text("# changed canonical provider configuration")
    with pytest.raises(ValueError, match="Original Codex home or configuration changed"):
        prepare_boot_goal_home(root, binding)


def test_original_effective_sdk_home_grants_boot_source_capture(retained_task, monkeypatch):
    _, _, _, layout = retained_task
    inherited_home = capture_goal_account_origin("2", {})
    boot_capable = capture_goal_account_origin("2", {"CODEX_HOME": str(layout["runtime"])})
    assert boot_capable and boot_capable["codex_home"] == str(layout["runtime"])
    assert boot_capable["codex_config_sha256"]
    assert inherited_home == boot_capable
    assert capture_goal_account_origin("2", {"CODEX_HOME": str(layout["1"])}) is None

    monkeypatch.delenv("CODEX_HOME")
    quota_only = capture_goal_account_origin("2", {})
    assert quota_only and set(quota_only) == {
        "account", "account_identity_sha256", "sqlite_home", "sqlite_identity",
    }
