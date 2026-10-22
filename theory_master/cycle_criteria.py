from __future__ import annotations

"""Current single-Cycle criterion helpers with legacy read compatibility."""

from typing import Any

CURRENT_CYCLE_CRITERION_ID = "C-1"


def criterion_rows(value: Any) -> list[dict[str, Any]]:
    """Read current list-form criteria and legacy success/failure containers."""
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    if value.get("id") or value.get("criterion_id"):
        return [dict(value)]
    rows: list[dict[str, Any]] = []
    for key in ("criteria", "success", "failure"):
        items = value.get(key)
        if isinstance(items, list):
            rows.extend(dict(item) for item in items if isinstance(item, dict))
    return rows


def make_cycle_criteria(decisive_test: Any) -> list[dict[str, str]]:
    """Author the only criterion for a new Cycle."""
    criterion = str(decisive_test or "").strip()
    if not criterion:
        raise ValueError("cycle criterion requires a non-empty decisive_test")
    return [{"id": CURRENT_CYCLE_CRITERION_ID, "criterion": criterion}]
