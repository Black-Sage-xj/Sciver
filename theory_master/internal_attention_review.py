"""Legacy module alias for Supervisor-owned in-process Attention review.

The real authority and implementation live in supervisor_attention_review.py.
Old saved receipts under attention/sdk_internal_review_* are preserved.
"""
from __future__ import annotations

from .supervisor_attention_review import (
    REVIEW_MODEL, REVIEW_PROVIDER, POLICY_VERSION, MAX_SCIENTIFIC_CONTINUES,
    HARD_CODES, MAX_QUALIFICATION_RETRIES, _brief, _parse_response,
    review_supervisor_attention,
)

review_attention_in_sdk = review_supervisor_attention
