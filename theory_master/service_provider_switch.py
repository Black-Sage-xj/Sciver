"""Transactional Services provider/account choice preserving the logical tier.

Codex 1 and 2 share the selected model profile.
Selection changes the global defaults and publishes a manual provider preference
for the next new Worker Cycle, without rewriting any Worker-pinned tier or
in-flight frozen routes. No provider token is persisted here.
"""
from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path

from .backend_config import (
    MODEL_PROFILES, model_profile_path, model_provider_path,
    read_model_profile, read_model_provider,
)
from .codex_accounts import selected_account as current_codex_account

TIER_BY_PROFILE = {"test": 1, "codex": 2, "advanced": 3}
CODEX_PROFILES = {1: "test", 2: "codex", 3: "advanced"}
VALID_ACCOUNTS = {"account-1", "account-2"}


def preference_path() -> Path:
    """Explicit Services operator preference, separate from legacy CLI provider."""
    return model_profile_path().parent / "service_provider_preference"


def selected_service_preference() -> str | None:
    """Only manual Services choices override Worker-pinned provider at new Cycle.

    A later CLI profile/provider operation can invalidate the marker simply by
    changing the provider; this prevents stale Services choices shadowing CLI.
    """
    path = preference_path()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32:
            return None
        chosen = path.read_text(encoding="utf-8").strip()
        if chosen not in {"codex"}:
            return None
        if chosen != read_model_provider():
            return None
        return chosen
    except (OSError, UnicodeError, ValueError):
        # A damaged monitoring preference must not stop scientific Worker runs.
        return None



class SelectionConflict(RuntimeError):
    """Selection rejected before commit, or restored after a failure."""


def service_selection() -> dict[str, object]:
    profile = read_model_profile()
    provider = read_model_provider()
    tier = TIER_BY_PROFILE.get(profile)
    if tier is None:
        raise SelectionConflict("当前模型档位未知，禁止猜测切换目标")
    if provider != "codex":
        raise SelectionConflict("Services supports Codex account selection only")
    codex = current_codex_account()
    selected = (
        "account-" + codex if codex in {"1", "2"} else None
    )
    return {"active_account": selected, "provider": provider,
            "codex_account": ("account-" + codex) if codex in {"1", "2"} else None,
            "tier": tier, "profile": profile,
            "effective_at": "next_worker_cycle"}


def _write_private(path: Path, value: bytes | None) -> None:
    if path.is_symlink():
        raise SelectionConflict("模型档位配置是符号链接，拒绝修改")
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tm-provider-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as dest:
            dest.write(value)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def switch_service_provider(account: str) -> dict[str, object]:
    if account not in VALID_ACCOUNTS:
        raise ValueError("请选择 Codex 1 或 Codex 2")
    if os.environ.get("THEORYMASTER_PROFILE") or os.environ.get("THEORYMASTER_MODEL_PROVIDER"):
        raise SelectionConflict("模型路由受环境变量覆盖，网页不能保证切换生效")
    profile_path = model_profile_path()
    provider_path = model_provider_path()
    profile_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = profile_path.parent / ".services-provider.lock"
    if lock_path.is_symlink():
        raise SelectionConflict("切换锁路径不安全")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before = service_selection()
        tier = before["tier"]
        profile = CODEX_PROFILES[tier]
        provider = "codex"
        choice_path = preference_path()
        old_paths = {p: p.read_bytes() if p.is_file() and not p.is_symlink()
                     else None for p in (profile_path, provider_path, choice_path)}
        if any(p.is_symlink() for p in old_paths):
            raise SelectionConflict("模型档位配置路径不安全")

        account_switched = False
        try:
            # Reuse the same daemon/session/rollback protections as the
            # original Codex 1/2 Services switch; if blocked, no provider
            # files are changed.
            from tm_observer.codex_usage import switch_active_account
            switch_active_account(account)
            account_switched = account != before["codex_account"]
            _write_private(profile_path, (profile + "\n").encode())
            _write_private(provider_path, (provider + "\n").encode())
            # Publish the operator intent LAST: Worker routes change only after
            # both global configuration files have passed their update.
            _write_private(choice_path, (provider + "\n").encode())
            updated = service_selection()
            if updated["active_account"] != account or updated["tier"] != tier:
                raise SelectionConflict("切换后核验失败")
            return updated
        except Exception as exc:
            restored = True
            for path, original in old_paths.items():
                try:
                    _write_private(path, original)
                except OSError:
                    restored = False
            if account_switched and before["codex_account"]:
                try:
                    from tm_observer.codex_usage import switch_active_account
                    switch_active_account(before["codex_account"], restart_app=False)
                except Exception:
                    restored = False
            if not restored:
                raise SelectionConflict("切换失败且回滚不完整，请人工核对当前档位和账号") from None
            if isinstance(exc, SelectionConflict):
                raise
            raise SelectionConflict("切换失败，已恢复原配置：" + type(exc).__name__) from None
