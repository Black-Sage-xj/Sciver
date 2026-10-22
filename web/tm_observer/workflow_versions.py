from __future__ import annotations

import json
from pathlib import Path
from typing import Any


HISTORY_DIR = Path(__file__).resolve().parent / "workflow_versions"
HISTORY_SCHEMA = "tm-observer/workflow-history/v1"


def load_workflow_snapshots() -> tuple[dict[str, Any], ...]:
    snapshots: list[dict[str, Any]] = []
    if not HISTORY_DIR.is_dir():
        return ()
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != HISTORY_SCHEMA:
            continue
        version = str(payload.get("version") or "").strip()
        mermaid = str(payload.get("mermaid") or "").strip()
        if not version or not mermaid:
            continue
        item = dict(payload)
        item["_path"] = str(path)
        snapshots.append(item)
    snapshots.sort(
        key=lambda item: (str(item.get("captured_at") or ""), str(item.get("version") or "")),
        reverse=True,
    )
    return tuple(snapshots)


def workflow_snapshot(version: str | None) -> dict[str, Any] | None:
    target = str(version or "").strip()
    if not target or target == "current":
        return None
    for item in load_workflow_snapshots():
        if item.get("version") == target:
            return item
    raise ValueError(f"unknown workflow history version: {target}")
