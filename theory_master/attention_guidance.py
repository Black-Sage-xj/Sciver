"""Task-local operator guidance for resuming NEEDS_ATTENTION Cycles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import atomic, now, write_json


def guidance_path(cycle_dir: Path) -> Path:
    return Path(cycle_dir) / "attention" / "operator_guidance.md"


def guidance_meta_path(cycle_dir: Path) -> Path:
    return Path(cycle_dir) / "attention" / "operator_guidance.json"


def write_attention_guidance(cycle_dir: Path, text: str, *, source: str = "human", attention_code: str | None = None) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        raise ValueError("attention guidance must not be empty")
    if len(value) > 12000:
        raise ValueError("attention guidance is too long (max 12000 characters)")
    cycle_dir = Path(cycle_dir).resolve()
    path = guidance_path(cycle_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic(path, value + "\n")
    payload = {
        "status": "pending",
        "source": str(source or "human"),
        "attention_code": str(attention_code or "") or None,
        "created_at": now(),
        "text": value,
        "path": str(path),
    }
    write_json(guidance_meta_path(cycle_dir), payload)
    return payload


def load_attention_guidance(cycle_dir: Path) -> dict[str, Any] | None:
    cycle_dir = Path(cycle_dir).resolve()
    path = guidance_path(cycle_dir)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    meta_path = guidance_meta_path(cycle_dir)
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            meta = loaded
    return {
        **meta,
        "status": str(meta.get("status") or "pending"),
        "source": str(meta.get("source") or "external"),
        "text": text,
        "path": str(path),
    }


def mark_guidance_consumed(cycle_dir: Path, payload: dict[str, Any], *, role: str, stage: str) -> dict[str, Any]:
    updated = {
        **payload,
        "status": "consumed",
        "consumed_at": now(),
        "delivered_to_role": role,
        "delivered_to_stage": stage,
    }
    write_json(guidance_meta_path(Path(cycle_dir)), updated)
    return updated
