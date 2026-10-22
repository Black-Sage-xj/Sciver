"""Operator-owned, durable Codex model tier templates.

Public TOMLs are defaults. Overrides live outside the repository, are read
at each profile resolution, and cannot alter an in-flight invocation.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from .backend_config import MODEL_PROFILES, REASONING_EFFORTS, ROUTE_ROLE_ORDER, RoleRoute

CODEX_TIERS = ("test", "codex", "advanced")
SCHEMA = "theory-master/user-codex-model-presets/v1"
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,89}\Z")


def path() -> Path:
    override = os.environ.get("THEORYMASTER_MODEL_PRESETS_FILE", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "theory-master" / "codex_model_presets.json"


def _read() -> dict:
    target = path()
    if not target.exists():
        return {}
    if target.is_symlink() or not target.is_file() or target.stat().st_size > 30000:
        raise ValueError("unsafe custom model preset file")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("tiers"), dict):
        raise ValueError("invalid saved model presets")
    if set(data["tiers"]) - set(CODEX_TIERS):
        raise ValueError("unknown custom model tier")
    for name, roles in data["tiers"].items():
        _validate(roles)
    return data["tiers"]


def _validate(roles: dict) -> None:
    if not isinstance(roles, dict) or set(roles) != set(ROUTE_ROLE_ORDER):
        raise ValueError("model tier must define all six research roles")
    for row in roles.values():
        if not isinstance(row, dict) or set(row) != {"model", "reasoning_effort"}:
            raise ValueError("model tier rows must have only model and reasoning_effort")
        model, effort = row["model"], row["reasoning_effort"]
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
            raise ValueError("invalid Codex model identifier")
        if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
            raise ValueError("invalid Codex reasoning effort")


def effective(profile: str) -> dict[str, RoleRoute]:
    if profile not in MODEL_PROFILES:
        raise ValueError("unknown model profile")
    original = dict(MODEL_PROFILES[profile])
    if profile not in CODEX_TIERS:
        return original
    rows = _read().get(profile)
    if rows is None:
        return original
    return {role: RoleRoute(
        model=rows[role]["model"], provider="CodexHTTPS", model_vendor="openai",
        reasoning_effort=rows[role]["reasoning_effort"]
    ) for role in ROUTE_ROLE_ORDER}


def save(profile: str, roles: dict) -> dict[str, object]:
    if profile not in CODEX_TIERS:
        raise ValueError("only Codex presets can be customized")
    _validate(roles)
    target = path()
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("unsafe preset destination")
    previous = dict(_read())
    previous[profile] = {r: dict(roles[r]) for r in ROUTE_ROLE_ORDER}
    payload = json.dumps({"schema": SCHEMA, "tiers": previous}, ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".model-presets-", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return {"profile": profile, "saved_roles": len(roles), "effective_at": "next_safe_role_invocation"}


def is_custom(profile: str) -> bool:
    return profile in CODEX_TIERS and profile in _read()
