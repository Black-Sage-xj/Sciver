"""Presentation-only Cycle guides, independent of full reports and Judge state."""
from __future__ import annotations

from typing import Any, Mapping


GRAPH_SUMMARY_FIELDS = ("conclusion", "basis", "scope", "open_question")
GRAPH_SUMMARY_LIMIT = 900


def validate_graph_summary(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != set(GRAPH_SUMMARY_FIELDS):
        raise ValueError("graph_summary must contain exactly conclusion, basis, scope, open_question")
    for key in GRAPH_SUMMARY_FIELDS:
        text = value[key]
        if not isinstance(text, str) or (key != "open_question" and not text.strip()):
            requirement = "a string" if key == "open_question" else "a nonempty string"
            raise ValueError(f"graph_summary.{key} must be {requirement}")
    if sum(len(value[key]) for key in GRAPH_SUMMARY_FIELDS) > GRAPH_SUMMARY_LIMIT:
        raise ValueError(f"graph_summary exceeds {GRAPH_SUMMARY_LIMIT} characters in total")


def graph_summary_text(outcome: Mapping[str, Any]) -> str:
    """Render the complete bounded guide; never manufacture one from a report."""
    guide = outcome.get("graph_summary")
    if guide is None:
        return ""
    validate_graph_summary(guide)
    return "\n\n".join(
        prefix + guide[key].strip()
        for key, prefix in (("conclusion", ""), ("basis", "依据："),
                            ("scope", "范围："), ("open_question", "未决："))
        if guide[key].strip()
    )


def hypothesis_reading_summary(node: Mapping[str, Any]) -> str:
    """Disambiguate an unassessed whole H from its recorded Cycle decisions."""
    summary = str(node.get("summary") or "")
    if node.get("cycle_outcomes") and node.get("scientific_status") == "none":
        suffix = "；状态：待检验。"
        if summary.endswith(suffix):
            return summary.removesuffix(suffix) + "；整体 H 尚未登记裁决，各轮结果见 Outcome。"
        return summary + "\n\n整体 H 尚未登记裁决，各轮结果见 Outcome。"
    return summary
