"""Classify runtime exceptions without inventing an Agent protocol violation."""
from __future__ import annotations

from .investigator_budget import BudgetLedgerError
from .output_normalization import OutputNormalizationError
from .runtime import now


def record_failure(state: dict, exc: Exception) -> dict:
    if isinstance(exc, BudgetLedgerError):
        category = "budget_validation"
    elif isinstance(exc, OutputNormalizationError):
        category = "protocol"
        state["protocol_health"] = "violation"
    else:
        category = "runtime_validation" if isinstance(exc, ValueError) else "runtime"
    # Contract and artifact validators set violation at their source. Preserve
    # those explicit decisions, including when a later generic exception occurs.
    details = {"kind": type(exc).__name__, "category": category, "message": str(exc)}
    state["recovery"] = {"action": "error", **details, "at": now()}
    state["last_runtime_failure"] = dict(state["recovery"])
    return {"error": str(exc), "error_kind": details["kind"],
            "failure_category": category, "resumable": True}
