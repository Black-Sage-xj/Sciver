"""H-scoped opportunity descriptions; verdicts and task completion are separate."""
from __future__ import annotations

from typing import Any, Mapping

OPPORTUNITY_FIELDS = {"remaining_question", "dependency", "reopen_basis"}


def prior_status(context: Mapping[str, Any], hypothesis_id: str) -> str | None:
    graph = context.get("agent_graph") or {}
    columns = graph.get("hypothesis_columns") or []
    for row in graph.get("hypotheses") or []:
        if isinstance(row, list):
            row = dict(zip(columns, row))
        if not isinstance(row, Mapping) or row.get("id") != hypothesis_id:
            continue
        states = row.get("frontier_states") or [item.get("status") for item in row.get("frontiers") or []]
        return states[0] if len(states) == 1 else None
    return None


def validate_opportunity(update: Mapping[str, Any], previous_status: str | None = None) -> dict[str, str]:
    result = {}
    for field in OPPORTUNITY_FIELDS:
        value = update.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"frontier {field} must be text")
        result[field] = value.strip()
    status = update.get("status")
    if status in {"active", "parked"} and not result["remaining_question"]:
        raise ValueError("active/parked frontier requires an H-scoped remaining_question")
    if status == "parked" and not result["dependency"]:
        raise ValueError("parked frontier requires a concrete dependency")
    if status == "closed" and any(result.values()):
        raise ValueError("closed frontier cannot carry an open opportunity")
    if previous_status == "closed" and status in {"active", "parked"} and not result["reopen_basis"]:
        raise ValueError("reopening a closed frontier requires reopen_basis")
    return result
