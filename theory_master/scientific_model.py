"""Atomic current-version Scientific Model store.

Human-facing scientific content lives only in the Markdown Scientific Model
section of research_plan.md. Machine metadata lives separately in the runtime
root; it is never duplicated into the Research Plan.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .runtime import atomic

SCIENTIFIC_MODEL_SCHEMA = "theory-master/scientific-model/v4"
SCIENTIFIC_MODEL_STATE_FILENAME = "scientific_model_state.json"
AUTHOR_FIELDS = ("summary",)
MAX_SUMMARY_CHARS = 2400
_MODEL_FIELDS = {
    "schema", "version", "updated_from_cycle", "goal_id", "summary", "provenance",
}
_META_FIELDS = _MODEL_FIELDS - {"summary"}


def _normalize_summary(value: Any) -> str:
    return " ".join(str(value or "").split())


def empty_model(goal_id: str = "") -> dict[str, Any]:
    return {
        "schema": SCIENTIFIC_MODEL_SCHEMA,
        "version": 0,
        "updated_from_cycle": None,
        "goal_id": goal_id,
        "summary": "",
        "provenance": {"created_by": "theory-master", "source_cycles": []},
    }


def validate_authored_content(content: dict[str, Any]) -> None:
    if not isinstance(content, dict):
        raise ValueError("Research Lead Scientific Model content must be an object")
    if set(content) != {"summary"}:
        raise ValueError("Research Lead Scientific Model must contain exactly one field: summary")
    if not isinstance(content["summary"], str):
        raise ValueError("Scientific Model summary must be a string")
    normalized = _normalize_summary(content["summary"])
    if not normalized:
        raise ValueError("Scientific Model replacement summary must be non-empty")
    if len(normalized) > MAX_SUMMARY_CHARS:
        raise ValueError(f"Scientific Model summary exceeds {MAX_SUMMARY_CHARS} characters")


def validate_model(model: dict[str, Any]) -> None:
    if not isinstance(model, dict):
        raise ValueError("Scientific Model must be an object")
    if set(model) != _MODEL_FIELDS:
        unexpected = sorted(set(model) - _MODEL_FIELDS)
        missing = sorted(_MODEL_FIELDS - set(model))
        raise ValueError(
            "Scientific Model must use only the current schema fields"
            + (f"; unexpected={unexpected}" if unexpected else "")
            + (f"; missing={missing}" if missing else "")
        )
    if model.get("schema") != SCIENTIFIC_MODEL_SCHEMA:
        raise ValueError("invalid Scientific Model schema; backward compatibility is disabled")
    if not isinstance(model.get("version"), int) or model["version"] < 0:
        raise ValueError("Scientific Model version must be a non-negative integer")
    if model.get("updated_from_cycle") is not None and not isinstance(model.get("updated_from_cycle"), str):
        raise ValueError("Scientific Model updated_from_cycle must be a string or null")
    if not isinstance(model.get("goal_id"), str):
        raise ValueError("Scientific Model goal_id must be a string")
    if not isinstance(model.get("summary"), str):
        raise ValueError("Scientific Model summary must be a string")
    summary = _normalize_summary(model.get("summary"))
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ValueError(f"Scientific Model summary exceeds {MAX_SUMMARY_CHARS} characters")
    if model["summary"] != summary:
        raise ValueError("Scientific Model summary must be one normalized paragraph")
    if not isinstance(model.get("provenance"), dict):
        raise ValueError("Scientific Model provenance is required")
    source_cycles = model["provenance"].get("source_cycles", [])
    if not isinstance(source_cycles, list) or any(not isinstance(item, str) or not item for item in source_cycles):
        raise ValueError("Scientific Model provenance.source_cycles must be a list of non-empty strings")


def authored_content(model: dict[str, Any]) -> dict[str, Any]:
    validate_model(model)
    return {"summary": str(model["summary"])}


def state_path(root: Path) -> Path:
    return Path(root).expanduser().resolve() / SCIENTIFIC_MODEL_STATE_FILENAME


def _metadata(model: dict[str, Any]) -> dict[str, Any]:
    validate_model(model)
    return {key: deepcopy(model[key]) for key in _META_FIELDS}


def _load_metadata(root: Path, *, goal_id: str) -> dict[str, Any]:
    path = state_path(root)
    if not path.is_file():
        raise ValueError(f"current Scientific Model state is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != _META_FIELDS:
        raise ValueError("Scientific Model runtime metadata must use only the current schema fields")
    if value.get("schema") != SCIENTIFIC_MODEL_SCHEMA:
        raise ValueError("invalid Scientific Model runtime schema; backward compatibility is disabled")
    if value.get("goal_id") != goal_id:
        raise ValueError("Scientific Model goal_id does not match task config")
    return value


def initialize_state(root: Path, *, goal_id: str) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    path = state_path(root)
    if path.exists():
        return _load_metadata(root, goal_id=goal_id)
    model = empty_model(goal_id)
    root.mkdir(parents=True, exist_ok=True)
    atomic(path, json.dumps(_metadata(model), ensure_ascii=False, indent=2) + "\n")
    return _metadata(model)


def load_from_paths(root: Path, plan: Path, *, goal_id: str) -> dict[str, Any]:
    from .embedded_state import load_summary

    metadata = _load_metadata(root, goal_id=goal_id)
    model = {**metadata, "summary": _normalize_summary(load_summary(plan))}
    validate_model(model)
    return model


def _authoritative_plan(root: Path) -> tuple[Path, str, Path]:
    from .task import SYSTEM_SCHEMA, TASK_CONFIG_NAME, TASK_DIR_NAME

    candidate = Path(root).expanduser().resolve()
    if candidate.is_file():
        plan = candidate
        config_path = plan.parent / "theory-master.config.json"
        research_root = plan.parent / TASK_DIR_NAME / "research"
    else:
        research_root = candidate
        pointer = candidate / TASK_DIR_NAME / TASK_CONFIG_NAME
        if not pointer.is_file():
            raise RuntimeError(f"TheoryMaster task binding is required to load Scientific Model: {candidate}")
        pointer_data = json.loads(pointer.read_text(encoding="utf-8"))
        config_path = Path(pointer_data["config_path"]).expanduser().resolve()
        plan = Path(json.loads(config_path.read_text(encoding="utf-8"))["plan_path"]).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from .embedded_state import CONFIG_MARKER, FORMAT_VERSION

    if config.get("schema") != SYSTEM_SCHEMA or config.get(CONFIG_MARKER) != FORMAT_VERSION:
        raise RuntimeError("TheoryMaster task does not use the current research_plan format")
    return plan, str(config["goal_id"]), research_root


def load(root: Path, *, goal_id: str = "") -> dict[str, Any]:
    plan, configured_goal_id, research_root = _authoritative_plan(root)
    if goal_id and goal_id != configured_goal_id:
        raise ValueError("Scientific Model requested goal_id does not match task config")
    model = load_from_paths(research_root, plan, goal_id=configured_goal_id)
    _repair_history(research_root, model)
    return model


def _history_payload(model: dict[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2) + "\n"


def _safe_history_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "initial")).strip("-.")
    return token or "initial"


def _ensure_update_history(root: Path, model: dict[str, Any]) -> Path:
    history = root / "scientific_model_history"
    updates = history / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    payload = _history_payload(model)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    cycle = _safe_history_token(model.get("updated_from_cycle"))
    path = updates / f"v{int(model['version'])}--{cycle}--{digest[:12]}.json"
    if path.is_file():
        return path
    atomic(path, payload)
    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "version": int(model["version"]),
        "updated_from_cycle": model.get("updated_from_cycle"),
        "sha256": digest,
        "file": str(path.relative_to(history)),
    }
    index = history / "index.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _repair_history(root: Path, model: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    history = root / "scientific_model_history"
    history.mkdir(exist_ok=True)
    history_path = history / f"v{model['version']}.json"
    expected = _history_payload(model)
    if not history_path.is_file():
        atomic(history_path, expected)
    _ensure_update_history(root, model)


def save(root: Path, model: dict[str, Any]) -> None:
    from .embedded_state import replace_summary

    validate_model(model)
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan, goal_id, research_root = _authoritative_plan(root)
    if model.get("goal_id") != goal_id:
        raise ValueError("Scientific Model goal_id does not match task config")
    replace_summary(plan, model["summary"], reason=f"科研 Cycle {model.get('updated_from_cycle') or 'unknown'} 更新 Scientific Model：持久科学认识变化")
    atomic(state_path(research_root), json.dumps(_metadata(model), ensure_ascii=False, indent=2) + "\n")
    _repair_history(research_root, model)


def preview_authored_model(
    current: dict[str, Any],
    content: dict[str, Any] | None,
    *,
    cycle_id: str,
    action: str,
) -> dict[str, Any]:
    validate_model(current)
    if not cycle_id:
        raise ValueError("Scientific Model commit requires cycle_id")
    if action not in {"keep", "replace"}:
        raise ValueError("Scientific Model action must be keep or replace")
    if action == "keep":
        if content not in (None, {}):
            raise ValueError("Scientific Model keep action must not carry replacement content")
        return deepcopy(current)
    if content is None:
        raise ValueError("Scientific Model replace action requires complete content")
    validate_authored_content(content)
    normalized = {"summary": _normalize_summary(content["summary"])}
    if normalized == authored_content(current):
        return deepcopy(current)
    source_cycles = list(current["provenance"].get("source_cycles", []))
    updated = {
        "schema": SCIENTIFIC_MODEL_SCHEMA,
        "version": int(current["version"]) + 1,
        "updated_from_cycle": cycle_id,
        "goal_id": current["goal_id"],
        **normalized,
        "provenance": {
            **current["provenance"],
            "created_by": "research_lead",
            "source_cycles": [*source_cycles, cycle_id],
        },
    }
    validate_model(updated)
    return updated


class ScientificModelVersionConflict(ValueError):
    """CAS failure: another Cycle committed the Scientific Model after this one read it."""

    def __init__(self, *, base_version: int, current_version: int):
        super().__init__(
            "Scientific Model changed during parallel Cycle; retain evidence for reconciliation "
            f"(base v{base_version}, current v{current_version})"
        )
        self.base_version = base_version
        self.current_version = current_version


def _already_committed_by(current: dict[str, Any], content: dict[str, Any] | None,
                          *, cycle_id: str, action: str) -> bool:
    """A crash after save but before the Cycle recorded it must not self-conflict."""
    if action != "replace" or not isinstance(content, dict):
        return False
    return (str(current.get("updated_from_cycle") or "") == cycle_id
            and current.get("summary") == _normalize_summary(content.get("summary")))


def commit_authored_model(
    root: Path,
    content: dict[str, Any] | None,
    *,
    cycle_id: str,
    action: str,
    expected_version: int | None = None,
    graph_path: Path | None = None,
) -> dict[str, Any]:
    """Commit authored scientific synthesis; parallel Workers require version CAS.

    Existing single-Worker calls preserve historical behavior. Workers set the
    version they actually read at Cycle synthesis, avoiding a silent lost update
    (a conflict raises ScientificModelVersionConflict; the caller records it as
    a durable pending update). With ``graph_path`` the save runs inside the
    shared science commit barrier and refuses a sealed Goal (GoalSealedError).
    """
    from .goal_terminal import assert_goal_open, science_commit_barrier

    barrier: Any = nullcontext()
    if graph_path is not None:
        research_root = _authoritative_plan(root)[2]
        barrier = science_commit_barrier(research_root, "shared")
    with barrier:
        if graph_path is not None:
            assert_goal_open(research_root, Path(graph_path))
        if expected_version is None:
            current = load(root)
            updated = preview_authored_model(current, content, cycle_id=cycle_id, action=action)
            if updated != current:
                save(root, updated)
            return updated
        lock_path = Path(root).resolve() / ".theory-master" / "scientific_model_commit.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as fp:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            current = load(root)
            if int(current["version"]) != int(expected_version) and action != "keep":
                if _already_committed_by(current, content, cycle_id=cycle_id, action=action):
                    return current
                raise ScientificModelVersionConflict(
                    base_version=int(expected_version), current_version=int(current["version"]),
                )
            updated = preview_authored_model(current, content, cycle_id=cycle_id, action=action)
            if updated != current:
                save(root, updated)
            return updated


# ---------------------------------------------------------------------------
# Durable pending Scientific Model updates (I-08): a CAS-conflicted synthesis
# is never dropped; a later cycle_synthesis must explicitly dispose of it.
# ---------------------------------------------------------------------------

PENDING_SCHEMA = "theory-master/scientific-model-pending/v1"
PENDING_FILENAME = "scientific_model_pending.json"
PENDING_STATUSES = frozenset({"pending", "integrated", "superseded", "rejected"})
DISPOSITION_STATUSES = frozenset({"integrated", "superseded", "rejected"})


def pending_path(research_root: Path) -> Path:
    return Path(research_root).expanduser().resolve() / ".theory-master" / PENDING_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def _pending_locked(research_root: Path) -> Iterator[dict[str, Any]]:
    path = pending_path(research_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield _read_pending(path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_pending(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": PENDING_SCHEMA, "items": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema") != PENDING_SCHEMA
            or not isinstance(value.get("items"), list)):
        raise ValueError(f"incompatible Scientific Model pending registry: {path}")
    return value


def _save_pending(research_root: Path, value: dict[str, Any]) -> None:
    atomic(pending_path(research_root), json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def record_pending_update(
    research_root: Path, *, cycle_id: str, worker_id: str | None,
    base_version: int, current_version: int, update: dict[str, Any],
) -> dict[str, Any]:
    """Persist one conflicted synthesis; idempotent per Cycle (crash-safe retry)."""
    cycle_id = str(cycle_id)
    if not cycle_id:
        raise ValueError("pending Scientific Model update requires cycle_id")
    item_id = f"smp-cycle-{_safe_history_token(cycle_id)}"
    with _pending_locked(research_root) as registry:
        for item in registry["items"]:
            if item.get("id") == item_id:
                if item.get("update") != update:
                    raise ValueError(f"pending Scientific Model update {item_id} replay conflicts")
                return dict(item)
        item = {
            "id": item_id, "cycle": cycle_id, "worker": worker_id,
            "base_version": int(base_version),
            "current_version_at_conflict": int(current_version),
            "update": deepcopy(update), "status": "pending", "disposition": None,
            "resolved_by_cycle": None, "created_at": _now_iso(), "resolved_at": None,
        }
        registry["items"].append(item)
        _save_pending(research_root, registry)
        return dict(item)


def list_pending(research_root: Path, *, status: str | None = "pending") -> list[dict[str, Any]]:
    path = pending_path(research_root)
    if not path.is_file():
        return []
    with _pending_locked(research_root) as registry:
        return [deepcopy(item) for item in registry["items"]
                if status is None or item.get("status") == status]


def validate_pending_dispositions(dispositions: Any, injected_ids: list[str]) -> list[str]:
    """Every injected pending id needs exactly one explicit disposition (no silent loss)."""
    errors: list[str] = []
    injected = [str(item) for item in injected_ids]
    if dispositions in (None, []):
        dispositions = []
    if not isinstance(dispositions, list):
        return ["pending_dispositions must be a list"]
    seen: list[str] = []
    for index, row in enumerate(dispositions):
        if not isinstance(row, dict) or set(row) != {"pending_id", "status", "rationale"}:
            errors.append(f"pending_dispositions[{index}] must contain exactly pending_id, status, rationale")
            continue
        pending_id = str(row.get("pending_id") or "")
        if pending_id not in injected:
            errors.append(f"pending_dispositions[{index}] references unknown pending_id: {pending_id}")
        if pending_id in seen:
            errors.append(f"duplicate pending disposition: {pending_id}")
        seen.append(pending_id)
        if row.get("status") not in DISPOSITION_STATUSES:
            errors.append(f"pending_dispositions[{index}].status must be integrated|superseded|rejected")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            errors.append(f"pending_dispositions[{index}].rationale is required")
    missing = [item for item in injected if item not in seen]
    if missing:
        errors.append("pending Scientific Model updates lack a disposition: " + ", ".join(missing))
    return errors


def apply_pending_dispositions(
    research_root: Path, dispositions: list[dict[str, Any]], *, resolved_by_cycle: str,
) -> list[dict[str, Any]]:
    """Durably resolve pending items; idempotent for the same resolving Cycle.

    An item already resolved by another Cycle keeps that first resolution and
    is reported with ``applied=False``; nothing is overwritten.
    """
    results: list[dict[str, Any]] = []
    if not dispositions:
        return results
    with _pending_locked(research_root) as registry:
        by_id = {str(item.get("id")): item for item in registry["items"]}
        changed = False
        for row in dispositions:
            item = by_id.get(str(row["pending_id"]))
            if item is None:
                raise ValueError(f"unknown pending Scientific Model update: {row['pending_id']}")
            if item.get("status") == "pending":
                item.update({
                    "status": row["status"],
                    "disposition": {"status": row["status"], "rationale": row["rationale"].strip()},
                    "resolved_by_cycle": str(resolved_by_cycle),
                    "resolved_at": _now_iso(),
                })
                changed = True
                results.append({"pending_id": item["id"], "status": item["status"], "applied": True})
            else:
                results.append({"pending_id": item["id"], "status": item["status"],
                                "applied": item.get("resolved_by_cycle") == str(resolved_by_cycle),
                                "resolved_by_cycle": item.get("resolved_by_cycle")})
        if changed:
            _save_pending(research_root, registry)
    return results
