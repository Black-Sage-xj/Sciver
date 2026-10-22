"""Task-local Web operator preferences, separate from scientific state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import atomic

FILE = "web-operator-preferences.json"
BOOL_KEYS = {"error_repair", "cycle_audit"}
MODEL_KEYS = {"supervisor", "attention_review", "cycle_audit"}
MODEL_ALLOWLIST = {"gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"}

def read(root: Path) -> dict[str, Any]:
    path = Path(root) / ".theory-master" / FILE
    if not path.exists():
        return {"flags": {}, "models": {}}
    if path.is_symlink() or path.stat().st_size > 16000:
        raise ValueError("invalid task preference file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "theory-master/web-operator-preferences/v1":
        raise ValueError("invalid task preference schema")
    flags, models = data.get("flags"), data.get("models")
    if not isinstance(flags, dict) or not set(flags) <= BOOL_KEYS or any(type(v) is not bool for v in flags.values()):
        raise ValueError("invalid task preference flags")
    if not isinstance(models, dict) or not set(models) <= MODEL_KEYS or any(
        not isinstance(v, str) or v not in MODEL_ALLOWLIST for v in models.values()
    ):
        raise ValueError("invalid task preference models")
    return {"flags": flags, "models": models}

def write(root: Path, *, kind: str, key: str, value: Any) -> dict[str, Any]:
    if kind == "flag":
        if key not in BOOL_KEYS or type(value) is not bool:
            raise ValueError("invalid launch preference flag")
    elif kind == "model":
        if key not in MODEL_KEYS or not isinstance(value, str) or (value and value not in MODEL_ALLOWLIST):
            raise ValueError("invalid auxiliary model")
    else:
        raise ValueError("invalid task preference kind")
    root = Path(root)
    parent = root / ".theory-master"
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("missing task registry")
    data = read(root)
    if kind == "model" and value == "":
        data["models"].pop(key, None)  # Restore SDK / Codex CLI default.
    else:
        data["flags" if kind == "flag" else "models"][key] = value
    payload = {"schema": "theory-master/web-operator-preferences/v1", **data}
    atomic(parent / FILE, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return data
