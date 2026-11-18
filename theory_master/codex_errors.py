"""Typed failures shared by ordinary Turns and Native Goal transport."""
from __future__ import annotations

import json


class ToolApprovalUnavailable(RuntimeError):
    """An automatic review service failed before an authorized tool could run."""


class CodexProviderRequestRejected(RuntimeError):
    """Provider rejected the model/request; reconnect cannot repair it."""


class CodexTurnCancelled(RuntimeError):
    """The caller explicitly stopped an interactive Turn."""


class CodexTurnFailed(RuntimeError):
    """A failed/incomplete Turn cannot deliver its partial assistant text."""


def provider_request_rejection(raw_error: str | None) -> CodexProviderRequestRejected | None:
    if not raw_error:
        return None
    try:
        payload = json.loads(raw_error)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None
    detail = payload["error"]
    code = payload.get("status") or detail.get("status") or detail.get("code")
    if str(code) != "400" or str(detail.get("type") or "") != "invalid_request_error":
        return None
    message = str(detail.get("message") or "Provider rejected the request")
    return CodexProviderRequestRejected(f"Codex provider HTTP 400 invalid_request_error: {message[:700]}")
