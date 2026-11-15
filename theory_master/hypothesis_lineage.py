"""Explicit branch parent and supplementary scientific provenance."""
from __future__ import annotations

from typing import Any, Mapping


def explicit_lineage(candidate: Mapping[str, Any]) -> tuple[str, list[str]] | None:
    """Validate the new contract; absence preserves historical unranked sources."""
    if not {"lineage_main_node_id", "lineage_other_node_ids"} & candidate.keys():
        return None
    main = candidate.get("lineage_main_node_id")
    others = candidate.get("lineage_other_node_ids", [])
    if not isinstance(main, str) or not main or main.strip() != main:
        raise ValueError("lineage_main_node_id requires one canonical nonempty H ID")
    if (not isinstance(others, list) or len(others) > 2
            or any(not isinstance(x, str) or not x or x.strip() != x for x in others)
            or len(set(others)) != len(others) or main in others):
        raise ValueError("lineage_other_node_ids requires 0..2 unique H IDs distinct from main")
    return main, others


def lineage_sources(candidate: Mapping[str, Any]) -> list[str]:
    explicit = explicit_lineage(candidate)
    if explicit is not None:
        return [explicit[0], *explicit[1]]
    legacy = candidate.get("lineage_anchor_node_ids")
    if isinstance(legacy, list) and legacy:
        return list(legacy)
    return [candidate["lineage_anchor_node_id"]] if candidate.get("lineage_anchor_node_id") else []


def source_role_text(node: Mapping[str, Any]) -> str:
    """Shared detail copy for the Canvas and live SQLite observer projection."""
    main = node.get("lineage_main_node_id")
    if not main:
        return ""
    return ("\n\n### 前序来源\n\n- Main（分支连线）：" + str(main)
            + "\n- 其它（仅追溯）：" + ("、".join(node.get("lineage_other_node_ids") or []) or "（无）"))
