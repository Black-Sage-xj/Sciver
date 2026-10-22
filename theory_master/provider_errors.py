from __future__ import annotations

"""Provider error classification shared by transport and workflow recovery."""

import re
from typing import Any


class ProviderQuotaExhausted(RuntimeError):
    """The active provider/account has no usable quota left for another model call."""

    def __init__(self, message: str, *, provider: str | None = None, account: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.account = account


_EXPLICIT_QUOTA_MARKERS = (
    "you've hit your usage limit",
    "you have hit your usage limit",
    "usage limit reached",
    "usage limit exceeded",
    "quota exceeded",
    "quota exhausted",
    "credits exhausted",
    "credit exhausted",
    "credits depleted",
    "credit balance is too low",
    "insufficient credits",
    "insufficient credit",
    "account budget exhausted",
    "budget exhausted",
    "billing hard limit has been reached",
    "billing limit has been reached",
    "insufficient_quota",
)


def is_provider_quota_exhausted(message: Any) -> bool:
    """Return True only for explicit account/quota exhaustion.

    A bare HTTP 429 or ordinary rate-limit message is intentionally *not*
    terminal here: those can be transient and remain eligible for the normal
    provider/transport recovery path.
    """
    text = str(message or "").strip().lower().replace("’", "'")
    if not text:
        return False
    # API providers may return HTTP 403 for insufficient prepaid balance rather
    # than an English quota code. Require BOTH the precharge failure and a
    # balance/required-amount detail; unrelated 401/403 are not quota errors.
    if "预扣费额度失败" in text and "剩余额度" in text and "需要预扣费额度" in text:
        return True

    if any(marker in text for marker in _EXPLICIT_QUOTA_MARKERS):
        return True
    if re.search(r"\b(?:usage|account)\s+(?:quota|budget|limit)\b.{0,48}\b(?:exhausted|depleted|exceeded|reached)\b", text):
        return True
    if re.search(r"\b(?:quota|credits?)\b.{0,48}\b(?:exhausted|depleted)\b", text):
        return True
    return False
