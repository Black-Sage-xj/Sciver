"""Startup-only visibility for effective model routes, without changing routing.

This reports public model identifiers and reasoning levels only. It never
prints other task environment entries (which can contain credentials).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .backend_config import (
    ROUTE_ROLE_ORDER, RoleRoute, build_model_profile,
    normalize_model_profile, read_model_profile, read_model_provider,
)
from .codex_backend import CodexBackend


def model_route_preflight_lines(
    effective: Mapping[str, RoleRoute], *, task_env_file: Path | None,
) -> list[str]:
    """Compare configured global tier, task tier and resolved routes.

    Model resolution and per-task overrides remain entirely owned by
    resolve_routes(); this diagnostic is read-only and advisory.
    """
    global_profile = read_model_profile(env={})
    global_provider = read_model_provider(env={})
    expected = build_model_profile(global_profile, global_provider)
    local = (CodexBackend._read_env_file(Path(task_env_file))
             if task_env_file is not None and Path(task_env_file).is_file() else {})
    def safe_tier(value: str | None) -> str:
        if not value:
            return "(none)"
        try:
            return normalize_model_profile(value)
        except ValueError:
            return "(invalid, overridden)"

    task_tier = safe_tier(local.get("THEORYMASTER_PROFILE"))
    process_tier = safe_tier(os.environ.get("THEORYMASTER_PROFILE"))
    lines = [
        "MODEL PREFLIGHT global_tier=" + global_profile
        + " global_provider=" + global_provider
        + " task_env_tier=" + task_tier
        + " process_env_tier=" + process_tier
        + " (task/role/CLI overrides remain allowed)"
    ]
    differences = []
    for role in ROUTE_ROLE_ORDER:
        planned, actual = expected[role], effective[role]
        if planned != actual:
            def label(route: RoleRoute) -> str:
                return f"{route.model}@{route.provider}/{route.reasoning_effort or 'default'}"
            differences.append(f"{role}:global={label(planned)} effective={label(actual)}")
    if differences:
        lines.append("MODEL ROUTE OVERRIDE WARNING: " + "; ".join(differences))
    elif any(tier not in {"(none)", global_profile} for tier in (task_tier, process_tier)):
        lines.append("MODEL ROUTE OVERRIDE WARNING: environment tier differs from global "
                     "but is masked by another higher-priority route setting")
    lines.append("MODEL EFFECTIVE ROUTES " + "; ".join(
        f"{role}={effective[role].model}/{effective[role].reasoning_effort or 'default'}"
        for role in ROUTE_ROLE_ORDER
    ))
    return lines
