"""Frozen Lead reasoning shared by graph readers and research reports."""
from __future__ import annotations

from typing import Any, Mapping


TRANSITION_SCHEMA = "theory-master/research-transition/v1"
TRANSITION_LIMIT = 400


def validate_transition(value: Any) -> None:
    required = {"prior_gap", "current_change"}
    if (not isinstance(value, dict) or not required <= value.keys()
            or set(value) - required - {"source_contributions"}):
        raise ValueError("transition requires prior_gap/current_change and optional source_contributions")
    for field in ("prior_gap", "current_change"):
        text = value[field]
        if not isinstance(text, str) or not text.strip() or len(text) > TRANSITION_LIMIT:
            raise ValueError(f"transition.{field} must be nonempty and at most {TRANSITION_LIMIT} characters")
    if "source_contributions" in value:
        contributions = value["source_contributions"]
        if (not isinstance(contributions, dict)
                or any(not isinstance(key, str) or not key or key.strip() != key
                       or not isinstance(text, str) or not text.strip() or len(text) > TRANSITION_LIMIT
                       for key, text in contributions.items())):
            raise ValueError("transition.source_contributions requires canonical H keys and nonempty bounded explanations")


def validate_source_contributions(candidate: Mapping[str, Any], source_ids: list[str], *, required: bool = False) -> None:
    """The map explains the existing source set; it cannot declare another set."""
    transition = candidate.get("transition")
    validate_transition(transition)
    contributions = transition.get("source_contributions")
    if contributions is None:
        if required:
            raise ValueError("new proposal requires transition.source_contributions ({} for a root H)")
        return  # Historical checkpoints retain their recorded, possibly absent notes.
    if set(contributions) != set(source_ids):
        raise ValueError("transition.source_contributions keys must exactly match main and other source H IDs")


def transition_record(candidate: Mapping[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Copy proposal reasoning; absence is a historical record, never an inference."""
    if "transition" not in candidate:
        return None
    transition = candidate["transition"]
    validate_source_contributions(candidate, [source["hypothesis_id"] for source in sources])
    contributions = transition.get("source_contributions", {})
    for field in ("why_now", "goal_advance"):
        if not isinstance(candidate.get(field), str) or not candidate[field].strip():
            raise ValueError(f"research transition requires {field}")
    return {
        "schema": TRANSITION_SCHEMA,
        **{key: transition[key] for key in ("prior_gap", "current_change")},
        "why_now": candidate["why_now"],
        "goal_advance": candidate["goal_advance"],
        "sources": [{**{key: val for key, val in source.items() if key != "contribution"},
                     **({"contribution": contributions[source["hypothesis_id"]]}
                        if source["hypothesis_id"] in contributions else {})} for source in sources],
    }


def research_transition_text(record: Mapping[str, Any] | None) -> str:
    """Render the same frozen sentences without generating a second explanation."""
    if not record:
        return ""
    validate_transition({key: record.get(key) for key in ("prior_gap", "current_change")})
    sources = record.get("sources") or []
    lines = ["## 为什么研究到这里", ""]
    if sources:
        lines += ["**承接研究：** " + "；".join(
            ("主要来源：" if source.get("role") == "main" else "其他来源：")
            + "[[" + source["hypothesis_id"] + "]]"
            + (" · " + source["title"] if source.get("title") else "")
            for source in sources
        ), ""]
        statuses = {"supported": "支持", "rejected": "反驳", "open": "尚无法判定", "unadjudicated": "未裁决"}
        for source in sources:
            if source.get("contribution"):
                lines += [source["hypothesis_id"] + " 的贡献：" + source["contribution"], ""]
            refs = ["[[" + ref["report_ref"] + "|Cycle " + str(ref["cycle_id"]) + " · "
                    + ("历史裁决：" + "、".join(ref["original_verdicts"]) if ref.get("original_verdicts")
                       else statuses.get(ref.get("verdict"), str(ref.get("verdict") or "未标记"))) + " 报告]]"
                    for ref in source.get("outcome_refs") or [] if ref.get("report_ref")]
            if refs:
                lines += [source["hypothesis_id"] + " 的来源记录：" + "、".join(refs), ""]
    label = "前序留下的问题" if sources else "从什么问题出发"
    lines += [f"**{label}：** {record['prior_gap']}", "",
              f"**这一步改变什么：** {record['current_change']}", "",
              f"**为什么现在研究：** {record['why_now']}", "",
              f"**推进目标：** {record['goal_advance']}"]
    return "\n".join(lines)
