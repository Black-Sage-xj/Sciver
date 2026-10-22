"""Historical Preferred ResearchFocus inspection.

Preferred remains a display label. It does not emit notices or change scheduling.
"""
from __future__ import annotations

import re
from typing import Any


def inspect_preferred_focus(graph: dict[str, Any], started_ids: set[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Detect preferred ResearchFocus lifecycle from a SQLite graph_view payload."""
    nodes = {str(node.get("id") or ""): node for node in graph.get("nodes", [])
             if isinstance(node, dict) and node.get("id")}
    hypotheses = {node_id: node for node_id, node in nodes.items()
                  if node.get("type") == "Hypothesis"}
    frontier: dict[str, dict[str, Any]] = {}
    for h_id, node in hypotheses.items():
        rows = node.get("frontiers") or []
        frontier[h_id] = rows[0] if rows else {"status": "unmarked", "priority": ""}

    children: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source_hypothesis") or "")
        target = str(edge.get("target_hypothesis") or "")
        if source in hypotheses and target in hypotheses:
            children.setdefault(source, []).append(target)

    def focus_descendants(root_id: str) -> tuple[set[str], list[str]]:
        visited: set[str] = set()
        tips: list[str] = []
        stack = [root_id]
        while stack:
            current = stack.pop()
            if current in visited or current not in hypotheses:
                continue
            visited.add(current)
            descendants = children.get(current, [])
            if descendants:
                stack.extend(descendants)
            else:
                tips.append(current)
        return visited, tips

    focus_records: list[tuple[str, str]] = []
    for node_id, node in nodes.items():
        if node.get("type") != "External" or node.get("content_type") != "ResearchFocus":
            continue
        match = re.search(r"(?m)^target_node_id:\s*([^\s]+)\s*$", str(node.get("content") or ""))
        target = match.group(1) if match else ""
        if target in hypotheses:
            focus_records.append((node_id, target))

    started: list[str] = []
    exhausted: list[dict[str, Any]] = []
    for focus_id, target in focus_records:
        descendants, tips = focus_descendants(target)
        target_frontier = frontier.get(target, {})
        is_new = (target_frontier.get("status") == "active"
                  and target_frontier.get("priority") == "preferred")
        registered = focus_id in started_ids or is_new
        if is_new and focus_id not in started_ids:
            started.append(focus_id)
        if registered and tips and all(frontier.get(h, {}).get("status") == "closed" for h in tips):
            exhausted.append({
                "focus_id": focus_id, "target_node_id": target,
                "tips": tips, "descendant_count": len(descendants),
            })
    return started, exhausted

def check_preferred_research_focus(workflow: Any, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Historical Preferred markers are display-only and do not notify or schedule (I-12)."""
    del workflow, state
    return None
