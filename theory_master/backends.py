"""Backward-compatible backend facade.

Routing, deterministic fixtures, Codex transport, and approval policy live in focused modules.
"""
from __future__ import annotations

from typing import Any

from .backend_config import *  # noqa: F401,F403
from .backend_fakes import CanonicalFakeBackend, FakeBackend  # noqa: F401
from .codex_backend import CodexBackend  # noqa: F401
from .execution_policy import (  # noqa: F401
    PROCESS_CONTROL_GUARD, REPO_GUARD, approval_decision,
    is_history_rewriting_command, is_process_control_command, make_approval_decision,
)

def json_object(text: str) -> dict[str, Any]:
    """Backward-compatible JSON-object facade over canonical output normalization."""
    from .output_normalization import normalize_agent_output

    return normalize_agent_output(text).value
