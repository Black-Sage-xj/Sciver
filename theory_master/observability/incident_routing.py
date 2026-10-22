"""Deterministic anomaly nature and ownership, not an implicit model invocation.

Severity is a display dimension.  This routing classifies the *cause* so that
formatting, quota, scientific stalls and engineering failures cannot all fall
through to an expensive / unsafe generic repair Agent.
"""
from __future__ import annotations

from typing import Any

ROUTING_VERSION = 2


def classify_incident(*, severity: str, kind: str, code: str | None = None,
                      summary: str = "", fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the initial intended owner; no claim of dispatch or recovery."""
    from ..supervisor_recovery import OPERATIONAL_ATTENTION
    fields = fields or {}
    signal = " ".join((str(code or ""), str(summary), str(fields.get("error_type") or ""))).lower()
    if severity == "important":
        nature, owner, strategy = "milestone", "observability", "record_only"
    elif kind in {"json_normalization", "contract_repair_retry", "json_repair_retry"} or severity == "minor":
        nature, owner, strategy = "format_or_contract", "sdk", "bounded_deterministic_or_contract_repair"
    elif (kind == "quota_pause" or "run_paused" in signal or "usage limit" in signal
          or "usagelimited" in signal or "provider_quota" in signal):
        nature, owner, strategy = "provider_quota", "quota_controller", "wait_or_switch_account"
    elif any(marker in signal for marker in (
        "cycle_cost_budget", "investigator_budget", "budget_review_required",
        "budget_exhausted", "budget_guard",
    )):
        nature, owner, strategy = "budget_guard", "budget_controller", "bounded_budget_review"
    elif kind == "runtime_model_tier_mismatch" or any(marker in signal for marker in (
        "model is not supported", "invalid_api_key", "authentication failed", "permission denied",
    )):
        nature, owner, strategy = "provider_configuration", "supervisor_runtime", "validate_configuration_or_wait_for_provider"
    elif kind == "needs_attention" and code in OPERATIONAL_ATTENTION:
        nature, owner, strategy = "operational_failure", "supervisor_runtime", "persistent_recovery_with_guarded_resume"
    elif kind == "needs_attention":
        nature, owner, strategy = "scientific_or_operational_attention", "supervisor_attention", "bounded_review_no_science_edit"
    elif kind == "system_failure" or severity == "error":
        nature, owner, strategy = "engineering_failure", "supervisor_runtime", "isolated_code_repair_or_review"
    else:
        nature, owner, strategy = "unclassified", "operator", "hold_and_diagnose"
    return {
        "version": ROUTING_VERSION,
        "nature": nature,
        "owner": owner,
        "strategy": strategy,
        "dispatch_status": "classified_only",  # a record is never proof of a real handoff
    }
