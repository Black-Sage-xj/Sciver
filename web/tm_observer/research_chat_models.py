"""Account-scoped, bounded model-directory cache for operator chat.

Only the explicit catalog endpoint/unknown-choice validation probes Codex.
Rendering chat snapshots and settings reads this cache without subprocesses.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FALLBACK_MODELS = ("gpt-6-sol", "gpt-6-luna", "gpt-6.1-sol")
FALLBACK_EFFORTS = ("low", "medium", "high", "xhigh")
# Explicit members of the installed Codex protocol ReasoningEffort enum. The
# extra values become selectable only when this account advertises them.
SDK_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
TTL_SECONDS = 30.0
MIN_REFRESH_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 6.0
_monotonic = time.monotonic
_LOCK = threading.RLock()
_ENTRIES: dict[str, "_Entry"] = {}


@dataclass
class _Entry:
    scope: str = field(default_factory=lambda: uuid.uuid4().hex)
    models: list[str] | None = None
    efforts: dict[str, list[str]] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    updated_mono: float = 0.0
    attempted_mono: float | None = None
    failed: bool = False
    reason: str | None = None
    event: threading.Event | None = None


_BUSY_ENTRY = _Entry(failed=True, reason="模型目录查询繁忙，请稍后重试")


def _identity() -> tuple[str, Path, str]:
    from theory_master.codex_accounts import runtime_home

    auth = runtime_home().expanduser().absolute() / "auth.json"
    try:
        if auth.stat().st_size > 1_000_000:
            digest = "oversize"
        else:
            digest = hashlib.sha256(auth.read_bytes()).hexdigest()
    except OSError as exc:
        digest = "unavailable:" + type(exc).__name__
    # This key never leaves the process. Auth/account changes cannot reuse
    # another identity's last-good directory, including on transient failure.
    return hashlib.sha256((str(auth) + "\0" + digest).encode()).hexdigest(), auth, digest


def _entry(key: str) -> _Entry:
    value = _ENTRIES.get(key)
    if value is None:
        # Bound old identities without evicting a probe another request awaits.
        for old_key in list(_ENTRIES):
            if len(_ENTRIES) < 8:
                break
            if _ENTRIES[old_key].event is None:
                del _ENTRIES[old_key]
        if len(_ENTRIES) >= 8:
            return _BUSY_ENTRY
        value = _ENTRIES[key] = _Entry()
    return value


def _view(entry: _Entry, current_model: str | None = None) -> dict[str, Any]:
    models = list(entry.models) if entry.models is not None else list(FALLBACK_MODELS)
    if isinstance(current_model, str) and current_model and current_model not in models:
        models.append(current_model)
    extra_efforts = {effort for values in entry.efforts.values() for effort in values}
    efforts = [effort for effort in SDK_EFFORTS if effort in FALLBACK_EFFORTS or effort in extra_efforts]
    stale = entry.failed or entry.models is None or _monotonic() - entry.updated_mono >= TTL_SECONDS
    status = ("refreshing" if entry.event else "unavailable" if entry.failed else
              "catalog_read" if entry.models is not None else "unqueried")
    metadata: dict[str, Any] = {"status": status, "stale": stale, "updated_at": entry.updated_at, "scope": entry.scope}
    if entry.reason:
        metadata["reason"] = entry.reason
    return {"available_models": models, "available_reasoning_efforts": efforts,
            "model_reasoning_efforts": {key: list(values) for key, values in entry.efforts.items()},
            "model_default_reasoning_efforts": dict(entry.defaults), "model_catalog": metadata}


def cached_catalog(*, current_model: str | None = None) -> dict[str, Any]:
    key, _, _ = _identity()
    with _LOCK:
        return _view(_entry(key), current_model)


def refresh_catalog(*, force: bool = False, current_model: str | None = None) -> dict[str, Any]:
    key, auth, auth_fingerprint = _identity()
    with _LOCK:
        entry = _entry(key)
        if entry is _BUSY_ENTRY:
            return _view(entry, current_model)
        event = entry.event
        if event is None:
            now = _monotonic()
            if (entry.attempted_mono is not None and now - entry.attempted_mono < MIN_REFRESH_SECONDS
                    or not force and entry.models is not None and not entry.failed
                    and now - entry.updated_mono < TTL_SECONDS):
                return _view(entry, current_model)
            event = entry.event = threading.Event()
            entry.attempted_mono = now
            owns_probe = True
        else:
            owns_probe = False
    if not owns_probe:
        event.wait(timeout=PROBE_TIMEOUT_SECONDS + 2.0)
        return cached_catalog(current_model=current_model)
    try:
        from theory_master.codex_model_catalog import model_catalog

        result = model_catalog(auth.parent, None, timeout=PROBE_TIMEOUT_SECONDS,
                               auth_path=auth, account_label="Research Chat Codex",
                               expected_auth_fingerprint=auth_fingerprint)
        # The selector may have changed while the child read its frozen auth.
        # Discard that response instead of publishing it to the new account.
        active_key, _, _ = _identity()
        with _LOCK:
            if active_key == key and result.get("status") == "catalog_read" and isinstance(result.get("models"), list):
                entry.models = sorted({model for model in result["models"] if isinstance(model, str)
                                       and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model)})
                raw_efforts = result.get("efforts") or {}
                entry.efforts = {model: [effort for effort in SDK_EFFORTS if effort in raw_efforts[model]]
                                 for model in entry.models if isinstance(raw_efforts.get(model), list)}
                raw_defaults = result.get("default_efforts") or {}
                entry.defaults = {model: raw_defaults[model] for model in entry.models
                                  if raw_defaults.get(model) in entry.efforts.get(model, [])}
                entry.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                entry.updated_mono = _monotonic()
                entry.failed = False
                entry.reason = "已读取当前聊天账号的模型目录"
            elif active_key == key:
                entry.failed = True
                entry.reason = str(result.get("reason") or "暂时无法读取模型目录")[:200]
    except Exception as exc:
        with _LOCK:
            entry.failed = True
            entry.reason = f"模型目录读取失败（{type(exc).__name__}）"
    finally:
        with _LOCK:
            entry.event = None
            event.set()
    return cached_catalog(current_model=current_model)


def model_allowed(model: Any, *, current_model: str | None = None) -> bool:
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", model):
        return False
    view = cached_catalog(current_model=current_model)
    if model in view["available_models"]:
        return True
    return model in refresh_catalog(force=True)["available_models"]


def effort_allowed(effort: Any, model: str, *, current_effort: str | None = None) -> bool:
    if not isinstance(effort, str) or effort not in SDK_EFFORTS:
        return False
    if effort == current_effort:
        return True
    view = cached_catalog(current_model=model)
    mapping = view["model_reasoning_efforts"]
    if model in mapping:
        return effort in mapping[model]
    if effort in FALLBACK_EFFORTS:
        return True
    return effort in refresh_catalog(force=True)["model_reasoning_efforts"].get(model, [])
