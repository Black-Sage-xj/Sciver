"""Short-lived Supervisor incident session for human takeover.

Created only after an actionable notification or exception reaches the operator-facing flow
(runtime failure, NEEDS_ATTENTION, hold, or protocol violation). Normal Cycles do not
allocate this store. Decision functions stay in the existing review modules; this file
only records the dialogue and accepts a later follow-up turn. That turn may apply an
operator-requested config edit on the allowlist below.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
import fcntl
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .runtime import now, write_json

SCHEMA = "theory-master/supervisor-incident-session/v1"
MESSAGE_SCHEMA = "theory-master/supervisor-incident-message/v1"
LINK_SCHEMA = "theory-master/supervisor-incident-link/v1"
SESSION_ID = re.compile(r"SIS-[0-9a-f]{16}\Z")
ROLES = frozenset({"supervisor", "human", "tool", "system"})
STATUSES = frozenset({"active", "resolved", "closed"})
TRIGGER_TYPES = frozenset({
    "needs_attention", "hold", "protocol_violation", "runtime_failure",
})
MAX_HUMAN = 4000
MAX_CONTENT = 8000
_EDIT_PATHS = frozenset({
    "theory-master.config.json",
    "config.toml",
    ".theory-master/web-role-routes.json",
})
_EDIT_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 1_500_000
IMAGE_ID = re.compile(r"[0-9a-f]{16}\Z")
_IMAGE_EXT = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_SECRET = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|"
    r"((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[\"']?[^\s\"']+"
)


class IncidentSessionError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def _has_symlink_below(base: Path, path: Path) -> bool:
    """Reject symlinks in an allowlisted target's path below its trusted root."""
    base = Path(base).resolve()
    path = Path(path)
    try:
        relative = path.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _task_config_path(research_root: Path) -> Path | None:
    """Read the canonical task config binding without bootstrapping or writing state."""
    from .task import SYSTEM_CONFIG_NAME, TASK_CONFIG_NAME, TASK_DIR_NAME, effective_path_model

    root = Path(research_root).resolve()
    pointer = root / TASK_DIR_NAME / TASK_CONFIG_NAME
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if _has_symlink_below(root, pointer) or not pointer.is_file():
        raise IncidentSessionError("task binding is not a safe regular file")
    try:
        binding = json.loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(binding, dict):
            raise ValueError("invalid task binding")
        raw_config = binding.get("config_path")
        candidate = Path(str(raw_config or "")).expanduser()
        if binding.get("schema") != "theory-master/task/v2" or not candidate.is_absolute():
            raise ValueError("invalid task binding")
        if candidate.name != SYSTEM_CONFIG_NAME or candidate.is_symlink():
            raise ValueError("invalid task config target")
        config_path = candidate.resolve()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        task_home_raw = config.get("task_home") or str(Path(config["plan_path"]).expanduser().parent)
        task_home = Path(task_home_raw).expanduser().resolve()
        if config_path.parent != task_home or _has_symlink_below(task_home, config_path):
            raise ValueError("task config escapes its canonical Task Home")
        _task_home, expected_root, _legacy_root = effective_path_model(config)
        if expected_root.resolve() != root:
            raise ValueError("task binding points to another research root")
        return config_path
    except IncidentSessionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IncidentSessionError("canonical task config binding is invalid") from exc


def _canonical_task_id(research_root: Path, fallback: str) -> str:
    """Prefer the task ID from the canonical config, never an Observer URL ID."""
    root = Path(research_root).resolve()
    try:
        config_path = _task_config_path(root)
        if config_path is None:
            # Legacy v1 roots have no task binding pointer; only inspect the
            # explicitly allowlisted task config directly under that root.
            legacy_config = root / "theory-master.config.json"
            if not _has_symlink_below(root, legacy_config) and legacy_config.is_file():
                config_path = legacy_config
        if config_path is not None:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            value = config.get("task_id")
            if value is None or not str(value).strip():
                value = config.get("master_id")
            if value is not None and str(value).strip():
                return str(value)
    except (IncidentSessionError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return str(fallback or root.name)


def _notification_trigger_type(code: str, source: dict[str, Any] | None) -> str:
    """Classify a notification by its actual nature, not as a runtime failure by default."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(code or "").lower()).strip("_")
    status = str((source or {}).get("run_status") or "").lower()
    if "protocol" in normalized:
        return "protocol_violation"
    if normalized in {"hold", "supervisor_hold", "runtime_supervisor_hold"}:
        return "hold"
    if normalized in {"run_failed", "runtime_failure", "system_failure"} or status in {"failed", "error"}:
        return "runtime_failure"
    return "needs_attention"


def sessions_dir(research_root: Path) -> Path:
    return Path(research_root) / ".theory-master" / "supervisor_incidents" / "sessions"


def _redact(value: Any, limit: int = MAX_CONTENT) -> str:
    raw = str(value or "").replace("\x00", "")
    raw = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", raw)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n…[truncated]"


def _check_id(session_id: str) -> str:
    if not SESSION_ID.fullmatch(str(session_id or "")):
        raise IncidentSessionError("unknown supervisor incident session", status=404)
    return str(session_id)


def _session_file(research_root: Path, session_id: str) -> Path:
    path = sessions_dir(research_root) / f"{_check_id(session_id)}.json"
    if path.is_symlink():
        raise IncidentSessionError("supervisor incident session rejected", status=404)
    return path


def _messages_file(research_root: Path, session_id: str) -> Path:
    path = sessions_dir(research_root) / f"{_check_id(session_id)}.messages.jsonl"
    if path.is_symlink():
        raise IncidentSessionError("supervisor incident session rejected", status=404)
    return path


@contextmanager
def _lock(research_root: Path) -> Iterator[None]:
    directory = sessions_dir(research_root)
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / ".lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _read_session(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise IncidentSessionError("unknown supervisor incident session", status=404)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IncidentSessionError("supervisor incident session unreadable", status=404) from exc
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise IncidentSessionError("supervisor incident session rejected", status=404)
    return record


def _read_messages(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise IncidentSessionError("supervisor incident messages rejected", status=404)
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("schema") == MESSAGE_SCHEMA:
                rows.append(row)
    except (OSError, ValueError) as exc:
        raise IncidentSessionError("supervisor incident messages unreadable", status=404) from exc
    return rows


def _append_message(research_root: Path, session_id: str, role: str, content: str,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if role not in ROLES:
        raise IncidentSessionError(f"unknown incident message role: {role}")
    record = {
        "schema": MESSAGE_SCHEMA,
        "id": f"SIM-{uuid.uuid4().hex[:16]}",
        "session_id": session_id,
        "role": role,
        "content": _redact(content),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "created_at": now(),
    }
    path = _messages_file(research_root, session_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _write_link(cycle_dir: Path, record: dict[str, Any]) -> None:
    folder = Path(cycle_dir) / "supervisor"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "incident_link.json", {
        "schema": LINK_SCHEMA,
        "session_id": record["id"],
        "status": record["status"],
        "cycle_id": record["cycle_id"],
        "trigger_type": record["trigger_type"],
        "at": now(),
    })


def _opening(cycle_id: str, trigger_type: str, trigger_event: str, state: dict[str, Any]) -> str:
    health = state.get("protocol_health") or "unknown"
    lines = [
        f"检测到 {cycle_id}:",
        "",
        str(trigger_event or trigger_type),
        f"protocol_health={health}",
        "",
        "当前状态:",
    ]
    for key in ("status", "phase", "runtime_state", "router_ready", "resumable", "next_executor"):
        if key in state and state.get(key) not in (None, ""):
            lines.append(f"{key}={state.get(key)}")
    attention = state.get("attention") if isinstance(state.get("attention"), dict) else {}
    if attention.get("code"):
        lines.append(f"attention={attention.get('code')}")
    lines.extend(["", "开始分析原因。"])
    return "\n".join(lines)


def _find_active(research_root: Path, dedupe_key: str) -> dict[str, Any] | None:
    directory = sessions_dir(research_root)
    if not directory.is_dir():
        return None
    for path in directory.glob("SIS-*.json"):
        if path.is_symlink():
            continue
        try:
            record = _read_session(path)
        except IncidentSessionError:
            continue
        if record.get("status") == "active" and record.get("dedupe_key") == dedupe_key:
            return record
    return None


def publish_review_incident(
    *,
    research_root: Path,
    cycle_dir: Path,
    state: dict[str, Any],
    trigger_type: str,
    trigger_event: str,
    analysis: str | None,
    model_invoked: bool,
    system_note: str | None,
    tool_reads: list[str] | None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Open or reuse the incident dialogue. Does not change review decisions."""
    if trigger_type not in TRIGGER_TYPES:
        raise IncidentSessionError(f"unknown incident trigger: {trigger_type}")
    root = Path(research_root)
    cycle_id = str(state.get("cycle_id") or Path(cycle_dir).name)
    event = _redact(trigger_event, 240)
    dedupe_key = f"{cycle_id}:{trigger_type}:{event}"
    with _lock(root):
        current = _find_active(root, dedupe_key)
        fresh = current is None
        if fresh:
            session_id = f"SIS-{uuid.uuid4().hex[:16]}"
            record = {
                "schema": SCHEMA,
                "id": session_id,
                "task_id": str(state.get("task_id") or root.name),
                "cycle_id": cycle_id,
                "cycle_dirname": Path(cycle_dir).name,
                "trigger_type": trigger_type,
                "trigger_event": event,
                "status": "active",
                "created_at": now(),
                "closed_at": None,
                "dedupe_key": dedupe_key,
                "audit": audit if isinstance(audit, dict) else {},
            }
            write_json(_session_file(root, session_id), record)
            _append_message(root, session_id, "supervisor",
                            _opening(cycle_id, trigger_type, event, state),
                            {"source": "incident_open"})
        else:
            record = current
            session_id = str(record["id"])
        existing = {row.get("content") for row in _read_messages(_messages_file(root, session_id))}
        for raw_path in tool_reads or []:
            content = "read " + _redact(raw_path, 500).replace("\n", " ")
            if content not in existing:
                _append_message(root, session_id, "tool", content, {"source": "turn_reads"})
                existing.add(content)
        prose = _redact(analysis, 6000).strip() if model_invoked and analysis else ""
        if prose and prose not in existing:
            _append_message(root, session_id, "supervisor", prose,
                            {"source": "supervisor_review"})
            existing.add(prose)
        note = _redact(system_note, 6000).strip() if system_note else ""
        if note and note not in existing:
            _append_message(root, session_id, "system", note, {"source": "supervisor_review"})
        _write_link(Path(cycle_dir), record)
        return record


def open_notification_incident(*, research_root: Path, cycle_dir: Path, task_id: str,
                                 notification_id: str, code: str, summary: str,
                                 source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open/reuse a chat session from a notification without fabricating Supervisor output.

    The notification context is an explicitly sourced system message; a Supervisor
    reply is added only if the real Supervisor later writes one.
    """
    root = Path(research_root)
    cycle_path = Path(cycle_dir)
    cycle_id = cycle_path.name
    key = _redact(notification_id, 180).strip()
    if not key or not re.fullmatch(r"(?:current-stop:[0-9a-f]{18}|SUP-[0-9a-f]{16}|SIS-[0-9a-f]{16}|[A-Za-z0-9_.:-]{1,180})", key):
        raise IncidentSessionError("invalid notification reference")
    event = _redact(f"{code}: {summary}", 240)
    dedupe_key = f"notification:{cycle_id}:{key}"
    with _lock(root):
        current = _find_active(root, dedupe_key)
        if current is not None:
            return current
        session_id = f"SIS-{uuid.uuid4().hex[:16]}"
        canonical_task_id = _canonical_task_id(root, task_id)
        trigger_type = _notification_trigger_type(code, source)
        record = {
            "schema": SCHEMA, "id": session_id, "task_id": canonical_task_id,
            "cycle_id": cycle_id, "cycle_dirname": cycle_path.name,
            "trigger_type": trigger_type, "trigger_event": event,
            "status": "active", "created_at": now(), "closed_at": None,
            "dedupe_key": dedupe_key,
            "audit": {"source": "notification", "notification_id": key,
                      **(source if isinstance(source, dict) else {})},
        }
        write_json(_session_file(root, session_id), record)
        context = (
            "通知上下文（只读来源记录；不是 Supervisor 发言）\n\n"
            f"任务：{_redact(canonical_task_id, 200)}\nCycle：{_redact(cycle_id, 100)}\n"
            f"通知类型：{_redact(code, 160)}\n通知摘要：{_redact(summary, 1800)}\n"
            f"通知引用：{key}\n来源：TM 待处理通知列表"
        )
        _append_message(root, session_id, "tool", context,
                        {"source": "notification_context", "notification_id": key, "untrusted_source": True})
        return record


def load_incident(research_root: Path, session_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(research_root)
    with _lock(root):
        record = _read_session(_session_file(root, session_id))
        messages = _read_messages(_messages_file(root, session_id))
    return record, messages


def locate_incident(roots: list[Path], session_id: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    _check_id(session_id)
    seen: set[Path] = set()
    for raw in roots:
        try:
            root = Path(raw).resolve()
        except OSError:
            continue
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        path = sessions_dir(root) / f"{session_id}.json"
        if path.is_symlink() or not path.is_file():
            continue
        record, messages = load_incident(root, session_id)
        return root, record, messages
    raise IncidentSessionError("unknown supervisor incident session", status=404)


def list_notification_incidents(research_root: Path) -> list[dict[str, Any]]:
    """List persisted notification-originated conversations for the Supervisor history rail."""
    root = Path(research_root)
    directory = sessions_dir(root)
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in directory.glob("SIS-*.json"):
        if path.is_symlink() or not SESSION_ID.fullmatch(path.stem):
            continue
        try:
            record, messages = load_incident(root, path.stem)
        except (IncidentSessionError, OSError, ValueError):
            continue
        audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
        if audit.get("source") != "notification":
            continue
        latest = next((message for message in reversed(messages)
                       if message.get("role") in {"human", "supervisor", "system"}
                       and not (isinstance(message.get("metadata"), dict)
                                and message["metadata"].get("source") == "notification_context")), None)
        row = dict(record)
        row["last_message_at"] = str((latest or {}).get("at") or record.get("created_at") or "")
        row["last_message"] = str((latest or {}).get("content") or record.get("trigger_event") or "")[:350]
        rows.append(row)
    rows.sort(key=lambda item: (str(item.get("last_message_at") or item.get("created_at") or ""),
                                str(item.get("id") or "")), reverse=True)
    return rows


_FOLLOW_UPS: set[str] = set()
_FOLLOW_LOCK = threading.Lock()


def follow_up_pending(research_root: Path, session_id: str) -> bool:
    """Return whether this incident's asynchronous Supervisor turn is still active."""
    try:
        key = f"{Path(research_root).resolve()}:{_check_id(session_id)}"
    except (OSError, IncidentSessionError):
        return False
    with _FOLLOW_LOCK:
        return key in _FOLLOW_UPS
_PROSE_KEYS = ("answer", "explanation", "diagnosis", "reason", "guidance", "repair_suggestion")


def supervisor_prose(raw: str) -> str:
    """Show a model JSON object as the operator-facing sentences."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    if not text or text[:1] not in "{[":
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(value, dict):
        return text
    parts = [str(value.get(key) or "").strip() for key in _PROSE_KEYS]
    prose = "\n\n".join(part for part in parts if part)
    return prose or text


def split_operational_edits(text: str) -> tuple[str, list[dict[str, str]]]:
    """Separate the visible reply from one SDK edit request."""
    match = None
    for found in _EDIT_FENCE.finditer(text):
        match = found
    if match is None:
        return text.strip(), []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text.strip(), []
    raw_edits = payload.get("edits") if isinstance(payload, dict) else None
    if not isinstance(raw_edits, list):
        return text.strip(), []
    edits: list[dict[str, str]] = []
    for item in raw_edits:
        if not isinstance(item, dict):
            raise IncidentSessionError("invalid operational edit")
        edits.append({
            "path": str(item.get("path") or ""),
            "old": str(item.get("old") or ""),
            "new": str(item.get("new") or ""),
        })
    prose = (text[:match.start()] + text[match.end():]).strip()
    return prose, edits


def _resolve_operational_edit_path(root: Path, rel: str) -> Path:
    """Resolve one logical allowlisted path under its canonical task-owned root."""
    if rel not in _EDIT_PATHS:
        raise IncidentSessionError(f"path not allowed: {rel}")
    root = Path(root).resolve()
    if rel == "theory-master.config.json":
        canonical = _task_config_path(root)
        if canonical is not None:
            return canonical
    target = root / rel
    if _has_symlink_below(root, target):
        raise IncidentSessionError("symlink paths are not allowed for operational edits")
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise IncidentSessionError(f"config file missing: {rel}") from exc
    if not resolved.is_file() or root not in resolved.parents:
        raise IncidentSessionError("config file is outside the allowlisted root")
    return resolved


def _stage_bytes(path: Path, payload: bytes, mode: int) -> Path:
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.tm-edit-", dir=str(path.parent))
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, stat.S_IMODE(mode))
        return temp_path
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def apply_operational_edits(research_root: Path, edits: list[dict[str, str]]) -> list[str]:
    """Validate every replacement first, then atomically replace staged files with rollback."""
    if not edits:
        return []
    if len(edits) > 4:
        raise IncidentSessionError("too many operational edits")
    root = Path(research_root).resolve()
    planned: dict[Path, dict[str, Any]] = {}
    seen_anchors: set[tuple[Path, str]] = set()
    for item in edits:
        if not isinstance(item, dict):
            raise IncidentSessionError("operational edit record is invalid")
        rel = str(item.get("path") or "").replace("\\", "/").strip()
        path = _resolve_operational_edit_path(root, rel)
        old, new = str(item.get("old") or ""), str(item.get("new") or "")
        if not old or len(old) > 500 or len(new) > 500 or old == new:
            raise IncidentSessionError("operational edit text is invalid")
        anchor = (path, old)
        if anchor in seen_anchors:
            raise IncidentSessionError(f"duplicate edit target: {rel}")
        seen_anchors.add(anchor)
        if path not in planned:
            original_bytes = path.read_bytes()
            try:
                current = original_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise IncidentSessionError(f"config file is not UTF-8 text: {rel}") from exc
            planned[path] = {
                "original_bytes": original_bytes,
                "updated_text": current,
                "mode": path.stat().st_mode,
                "labels": [],
            }
        entry = planned[path]
        current = entry["updated_text"]
        if len(current) > 200_000 or current.count(old) != 1:
            raise IncidentSessionError(f"edit target is not unique: {rel}")
        entry["updated_text"] = current.replace(old, new, 1)
        if rel not in entry["labels"]:
            entry["labels"].append(rel)

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    replaced: list[Path] = []
    rollback_failed = False
    try:
        for path, entry in planned.items():
            staged[path] = _stage_bytes(path, entry["updated_text"].encode("utf-8"), entry["mode"])
            backups[path] = _stage_bytes(path, entry["original_bytes"], entry["mode"])
        for path, entry in planned.items():
            if path.read_bytes() != entry["original_bytes"]:
                raise IncidentSessionError(f"config changed during edit validation: {path.name}")
        try:
            for path in planned:
                os.replace(staged[path], path)
                replaced.append(path)
            for parent in {path.parent for path in planned}:
                fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            rollback_errors: list[str] = []
            for path in reversed(replaced):
                try:
                    os.replace(backups[path], path)
                    backups.pop(path, None)
                except OSError as rollback_exc:
                    rollback_failed = True
                    rollback_errors.append(f"{path}: {rollback_exc}")
            if rollback_errors:
                raise IncidentSessionError(
                    "operational edit failed and rollback was incomplete; backups preserved: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise IncidentSessionError("operational edit failed; prior files were restored") from exc
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)
        if not rollback_failed:
            for temp_path in backups.values():
                temp_path.unlink(missing_ok=True)
    return [f"已修改 {rel}" for entry in planned.values() for rel in entry["labels"]]


def _history_prompt(record: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    lines = [
        "You are the TheoryMaster Incident Supervisor, not a read-only commentator.",
        "You are the single TheoryMaster Supervisor executing its operator-delegated engineering repair skill. Inspect, directly fix engineering code in your writable engine workspace, run offline checks, and report actual results. This engineering skill is not read-only.",
        "A runtime triage hold with repair_suggestion is an internal engineering handoff. Diagnose, apply authorized repairs and run offline validation. Return evidence to the SDK executor for guarded revalidation and resume; do not launch a Worker from this engineering turn, which may hold scheduler.lock. Never clear an active Goal, reset scientific state or bypass budget guards. Keep the research continuity incident open until activity is verified.",
        "For task-local configurations only, append one fenced json edits block for SDK application; for engine code repairs use your workspace tools directly.",
        "After a repair report root cause, touched files, validation, any unresolved uncertainty and whether a reload/restart is needed. Never claim an unapplied request succeeded.",
        "The enforced repository guard declines git commit, push, reset, rebase and stash in this runtime role. Register TMC before engine edits, report exact paths and source hashes, and leave Git finalization pending review; do not try alternate commands to bypass the guard. Repository development commit rules have an explicit runtime-role exception.",
        "SDK validators currently cover the evidence-bound legacy budget bridge, scoped Canvas patches and already-available ordinary Workflow methods from attributable AttributeErrors. Unsupported faults require a new reviewed validator; a test command in your reply is not executable SDK authority. Return already_fixed when appropriate, or blocked with the missing validator/precondition. Do not make cosmetic edits to manufacture a patch.",
        "State the actual phase: diagnosed, repair_applied, repair_validated, or blocked with the exact failed precondition. A successful model reply, file change, active incident, or suggestion does not prove validation or recovery. If the original data error is already fixed, request the SDK evidence-bound revalidator instead of inventing another code patch. ValueError alone does not prove a protocol violation.",
        "SDK task-config edit paths only: canonical Task Home theory-master.config.json, research-root config.toml, research-root .theory-master/web-role-routes.json. Do not write Task/Science state or home Codex credentials.",
        "Do not change scientific conclusions, research_state.json, or emit a resume/hold/preserve decision.",
        "Attached images are shown to the operator. You only receive their filenames, not the pixels.",
        f"Cycle: {record.get('cycle_id')}",
        f"Trigger: {record.get('trigger_type')} / {record.get('trigger_event')}",
        "History:",
    ]
    for row in messages[-30:]:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        names = [str(item.get("name") or "")[:80] for item in (meta.get("images") or [])
                 if isinstance(item, dict) and item.get("name")]
        suffix = f" [attached images: {', '.join(names)}]" if names else ""
        lines.append(f"{row.get('role')}: {str(row.get('content') or '')[:1500]}{suffix}")
    return "\n".join(lines)


def _incident_chat_config_path(research_root: Path) -> Path | None:
    """Resolve the task config for chat without enforcing scientific-plan format."""
    from .task import (
        SYSTEM_CONFIG_NAME, SYSTEM_SCHEMA, TASK_CONFIG_NAME, TASK_DIR_NAME,
        TASK_SCHEMA, effective_path_model,
    )

    root = Path(research_root).expanduser().resolve()
    pointer = root / TASK_DIR_NAME / TASK_CONFIG_NAME
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if pointer.is_symlink() or not pointer.is_file():
        raise ValueError("task binding is not a safe regular file")
    binding = json.loads(pointer.read_text(encoding="utf-8"))
    if not isinstance(binding, dict) or binding.get("schema") != TASK_SCHEMA:
        raise ValueError("invalid task binding")
    candidate = Path(str(binding.get("config_path") or "")).expanduser()
    if not candidate.is_absolute() or candidate.name != SYSTEM_CONFIG_NAME or candidate.is_symlink():
        raise ValueError("invalid task config target")
    config_path = candidate.resolve()
    if not config_path.is_file():
        raise ValueError("task config target is missing")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema") != SYSTEM_SCHEMA:
        raise ValueError("invalid TheoryMaster system config")
    task_home_raw = config.get("task_home") or str(Path(config["plan_path"]).expanduser().parent)
    task_home = Path(task_home_raw).expanduser().resolve()
    if config_path.parent != task_home:
        raise ValueError("task config escapes its canonical Task Home")
    _task_home, expected_root, _legacy_root = effective_path_model(config)
    if expected_root.resolve() != root:
        raise ValueError("task binding points to another research root")
    return config_path


def _incident_supervisor_backend(root: Path):
    """Build the human incident-chat backend without migrating scientific task state.

    Resolve the canonical task config and persisted routes, but do not run
    TaskSpec's strict scientific-plan format gate. Chat must remain available to
    explain a legacy task even when starting a new Cycle requires a format upgrade.
    Runtime/Worker recovery uses a separate read-only diagnostic route so broken scientific configuration cannot disable diagnosis.
    """
    from .backends import CodexBackend, resolve_routes

    research_root = Path(root).expanduser().resolve()
    config_path = _incident_chat_config_path(research_root)
    env = research_root / ".theory-master" / "env"
    routes = resolve_routes(
        model=None,
        provider=None,
        role_specs=[],
        task_env_file=env if env.is_file() else None,
        task_route_override_root=research_root,
    )
    backend = CodexBackend(research_root, routes=routes, task_config_path=config_path)
    # Operator-facing Incident Supervisor edits ENGINE files in its own writable
    # workspace. Research records remain outside that workspace; task config
    # changes use the independently validated SDK edit path.
    # Real CodexBackend instances expose a writable instance dictionary. Test
    # sentinels and alternative backend adapters may be immutable objects; they
    # still preserve the factory contract and need no local-engine cwd override.
    if hasattr(backend, "__dict__"):
        backend.cwd = Path(__file__).resolve().parents[1]
    return backend


def _engineering_snapshot() -> dict[str, str]:
    """Small content-hash snapshot of tracked/untracked engine source for audit."""
    engine = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
             "theory_master", "web", "tests", "design", "changes", "pyproject.toml", "README.md"],
            cwd=engine, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True, timeout=12,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    result: dict[str, str] = {}
    names = [n for n in proc.stdout.split(b"\x00") if n]
    for raw in names[:5000]:
        rel = raw.decode("utf-8", "surrogateescape")
        path = engine / rel
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 3_000_000:
                continue
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return result


def _operator_repair_mail(record: dict[str, Any], report: str) -> str:
    """Send a post-change notice, never a pre-approval request."""
    from .observability.email_notify import _notification_setting
    import smtplib
    import ssl
    from email.message import EmailMessage
    user = _notification_setting("AUTO_SUBMIT_SMTP_USER")
    password = _notification_setting("AUTO_SUBMIT_SMTP_PASSWORD")
    recipient = _notification_setting("THEORYMASTER_NOTIFY_TO")
    if not user or not password or not recipient:
        return "邮件未发送：SMTP 或接收地址未配置（站内记录已保存）"
    mail = EmailMessage()
    mail["Subject"] = f"[TheoryMaster Supervisor] 修复操作通知 · Cycle {record.get('cycle_id')}"
    mail["From"] = user
    mail["To"] = recipient
    mail.set_content(report)
    host = _notification_setting("AUTO_SUBMIT_SMTP_HOST") or "smtp.163.com"
    port = int(_notification_setting("AUTO_SUBMIT_SMTP_PORT") or "465")
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                smtp.login(user, password)
                smtp.send_message(mail)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(mail)
    except Exception as exc:
        return f"邮件发送失败：{type(exc).__name__}（站内记录已保存）"
    return "修复通知已发送到配置的邮箱"


def _run_model_follow_up(root: Path, session_id: str, backend: Any | None) -> dict[str, Any]:
    """One incident turn. May apply an allowlisted config edit. Does not decide resume/hold/preserve."""
    with _lock(root):
        record = _read_session(_session_file(root, session_id))
        if record.get("status") != "active":
            messages = _read_messages(_messages_file(root, session_id))
            return {"ok": True, "session": record, "messages": messages}
        messages = _read_messages(_messages_file(root, session_id))
    actual_sdk_backend = backend is None
    if backend is None:
        try:
            backend = _incident_supervisor_backend(root)
        except Exception as exc:
            with _lock(root):
                _append_message(
                    root, session_id, "system",
                    "Supervisor could not start a reply to your message "
                    f"({type(exc).__name__}: {_redact(exc, 400)}). "
                    "Your message is saved; automatic monitoring is controlled separately.",
                    {"source": "follow_up"},
                )
                record = _read_session(_session_file(root, session_id))
                messages = _read_messages(_messages_file(root, session_id))
            return {"ok": False, "session": record, "messages": messages}
    from .supervisor_intervention import DEFAULT_PROVIDER, supervisor_model
    before_engine = _engineering_snapshot()
    prompt = _history_prompt(record, messages)
    thread = None
    try:
        model = supervisor_model(root)
        invoke = getattr(backend, "invoke", None)
        if callable(invoke):
            thread, raw = invoke("incident_supervisor", prompt, None, model, DEFAULT_PROVIDER)
        else:
            thread, raw = backend.invoke_runtime_review(prompt, model, DEFAULT_PROVIDER)
        reads = capture_turn_reads(backend)
        prose = ""
        notes: list[str] = []
        try:
            prose, edits = split_operational_edits(supervisor_prose(_redact(raw, 6000)))
            notes = apply_operational_edits(root, edits)
        except IncidentSessionError as exc:
            notes = [f"未修改：{exc}"]
        reply = prose or (notes[0] if notes else "") or "Supervisor 没有返回文字。"
    except Exception as exc:
        # Tool-backed edits can succeed even when the subsequent model turn
        # fails. Never omit the incident notice for such a partial repair.
        after_failure = _engineering_snapshot()
        changed_after_failure = sorted(
            path for path in set(before_engine) | set(after_failure)
            if before_engine.get(path) != after_failure.get(path)
        )
        failure_report = ""
        failure_mail = ""
        if changed_after_failure:
            failure_report = (
                f"Supervisor 部分修复警报 · {now()} · Cycle {record.get('cycle_id')}\n"
                f"本轮检测到工程变动：{', '.join(changed_after_failure[:35])}\n"
                f"随后调用失败：{type(exc).__name__}。变更未通过最终验证，需复查。"
            )
            failure_mail = (_operator_repair_mail(record, failure_report) if actual_sdk_backend
                            else "测试/注入后端：未发送邮件")
        with _lock(root):
            if failure_report:
                _append_message(root, session_id, "system",
                                failure_report + "\n" + failure_mail,
                                {"source": "repair_report", "status": "unverified_partial",
                                 "changed_engine": changed_after_failure[:35]})
            _append_message(
                root, session_id, "system",
                f"Supervisor reply failed ({type(exc).__name__}: {_redact(exc, 400)}). "
                "Your message remains saved; automatic monitoring is controlled separately.",
                {"source": "follow_up"},
            )
            record = _read_session(_session_file(root, session_id))
            messages = _read_messages(_messages_file(root, session_id))
        return {"ok": False, "session": record, "messages": messages}
    finally:
        drop = getattr(backend, "drop_thread", None)
        if thread and callable(drop):
            try:
                drop(thread)
            except Exception:
                pass
    after_engine = _engineering_snapshot()
    changed_engine = sorted(set(before_engine) | set(after_engine))
    changed_engine = [path for path in changed_engine if before_engine.get(path) != after_engine.get(path)]
    changed_settings = [note for note in notes if note.startswith("已修改 ")]
    report = ""
    mail_outcome = ""
    if changed_engine or changed_settings:
        report = (
            f"Supervisor 操作报告 · {now()} · Cycle {record.get('cycle_id')}\n"
            f"工程文件变化（本轮期间观测）：{', '.join(changed_engine[:35]) or '无'}\n"
            f"SDK 设置修改：{'; '.join(changed_settings) or '无'}\n"
            f"Supervisor 说明：{_redact(reply, 2500)}\n"
            "注意：仅证明文件发生变化；模型说明中的测试、部署和修复效果须按实际证据复核。"
        )
        mail_outcome = (_operator_repair_mail(record, report) if actual_sdk_backend
                        else "测试/注入后端：未发送邮件")
    with _lock(root):
        for item in reads:
            _append_message(root, session_id, "tool", "read " + _redact(item, 500).replace("\n", " "),
                            {"source": "turn_reads"})
        for note in notes:
            _append_message(root, session_id, "system", note, {"source": "operational_edit"})
        if report:
            _append_message(root, session_id, "system", report + "\n" + mail_outcome,
                            {"source": "repair_report", "changed_engine": changed_engine[:35],
                             "changed_settings": changed_settings})
        _append_message(root, session_id, "supervisor", reply, {"source": "human_follow_up"})
        record = _read_session(_session_file(root, session_id))
        messages = _read_messages(_messages_file(root, session_id))
    return {"ok": True, "session": record, "messages": messages,
            "repair": {"phase": ("repair_applied" if changed_settings else
                                 "changes_observed" if changed_engine else "diagnosed"),
                       "changed_engine": changed_engine, "changed_settings": changed_settings,
                       "engine_change_attribution": "observed_unattributed",
                       "git_finalization": "pending_review" if changed_engine else "not_applicable",
                       "verification": "requires_sdk_revalidation"}}


def record_engineering_validation(root: Path, session_id: str, validation: dict,
                                  receipt: dict | None) -> None:
    """Append SDK evidence without resolving an incident before runtime activity."""
    with _lock(root):
        record = _read_session(_session_file(root, session_id))
        phase = str(validation.get("phase") or "blocked")
        record["repair_status"] = phase
        record["repair_receipt"] = receipt
        write_json(_session_file(root, session_id), record)
        _append_message(root, session_id, "system",
                        "SDK engineering validation: " + phase + ". "
                        + str(validation.get("reason") or validation.get("tests") or "")[:1600]
                        + " Runtime activity remains unverified.",
                        {"source": "sdk_repair_validation", "phase": phase,
                         "validator": validation.get("validator"), "receipt": receipt})


def _follow_up_thread(root: Path, session_id: str, key: str) -> None:
    try:
        _run_model_follow_up(root, session_id, None)
        _record, messages = load_incident(root, session_id)
        if messages and messages[-1].get("role") == "human":
            _run_model_follow_up(root, session_id, None)
    finally:
        with _FOLLOW_LOCK:
            _FOLLOW_UPS.discard(key)


def _schedule_follow_up(root: Path, session_id: str) -> None:
    key = f"{root.resolve()}:{session_id}"
    with _FOLLOW_LOCK:
        if key in _FOLLOW_UPS:
            return
        _FOLLOW_UPS.add(key)
    threading.Thread(
        target=_follow_up_thread, args=(root, session_id, key),
        name="supervisor-incident-follow-up", daemon=True,
    ).start()


def _image_kind(blob: bytes) -> tuple[str, str] | None:
    if blob.startswith(b"RIFF") and len(blob) >= 12 and blob[8:12] == b"WEBP":
        return "image/webp", "webp"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    return None


def _images_dir(research_root: Path, session_id: str) -> Path:
    path = sessions_dir(research_root) / f"{_check_id(session_id)}.images"
    if path.is_symlink():
        raise IncidentSessionError("supervisor incident session rejected", status=404)
    return path


def _prepare_images(images: list[Any]) -> list[tuple[bytes, str, str, str]]:
    if len(images) > MAX_IMAGES:
        raise IncidentSessionError("too many images")
    prepared: list[tuple[bytes, str, str, str]] = []
    for item in images:
        if not isinstance(item, dict):
            raise IncidentSessionError("invalid image")
        raw = item.get("data")
        if isinstance(raw, str):
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[-1]
            try:
                blob = base64.b64decode(raw, validate=True)
            except ValueError as exc:
                raise IncidentSessionError("invalid image") from exc
        elif isinstance(raw, (bytes, bytearray)):
            blob = bytes(raw)
        else:
            raise IncidentSessionError("invalid image")
        if not blob or len(blob) > MAX_IMAGE_BYTES:
            raise IncidentSessionError("image is too large")
        kind = _image_kind(blob)
        if kind is None:
            raise IncidentSessionError("unsupported image type")
        media, ext = kind
        name = Path(str(item.get("name") or "image")).name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "image"
        prepared.append((blob, media, ext, name))
    return prepared


def _store_images(research_root: Path, session_id: str,
                  prepared: list[tuple[bytes, str, str, str]]) -> list[dict[str, str]]:
    if not prepared:
        return []
    folder = _images_dir(research_root, session_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for blob, media, ext, name in prepared:
        image_id = uuid.uuid4().hex[:16]
        dest = folder / f"{image_id}.{ext}"
        if dest.exists() or dest.is_symlink():
            raise IncidentSessionError("image store rejected")
        dest.write_bytes(blob)
        saved.append({"id": image_id, "name": name, "media_type": media})
    return saved


def image_bytes(research_root: Path, session_id: str, image_id: str) -> tuple[bytes, str]:
    if not IMAGE_ID.fullmatch(str(image_id or "")):
        raise IncidentSessionError("image not found", status=404)
    folder = _images_dir(research_root, session_id)
    found: list[tuple[Path, str]] = []
    for ext, media in _IMAGE_EXT.items():
        path = folder / f"{image_id}.{ext}"
        if path.is_symlink():
            raise IncidentSessionError("image not found", status=404)
        if path.is_file():
            found.append((path, media))
    if len(found) != 1:
        raise IncidentSessionError("image not found", status=404)
    path, media = found[0]
    blob = path.read_bytes()
    if len(blob) > MAX_IMAGE_BYTES or _image_kind(blob) is None:
        raise IncidentSessionError("image not found", status=404)
    return blob, media


def post_human_message(research_root: Path, session_id: str, content: str,
                       backend: Any | None = None,
                       images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Append one human message and continue the same Supervisor session.

    A web send returns as soon as the message is stored. The model turn runs
    afterwards, so the button is not stuck on the provider call.
    """
    text = _redact(content, MAX_HUMAN).strip()
    prepared = _prepare_images(list(images or []))
    if not text and not prepared:
        raise IncidentSessionError("message content is empty")
    root = Path(research_root)
    with _lock(root):
        record = _read_session(_session_file(root, session_id))
        if record.get("status") != "active":
            raise IncidentSessionError("supervisor incident session is closed", status=409)
        messages = _read_messages(_messages_file(root, session_id))
        last = messages[-1] if messages else None
        same_unanswered = (
            isinstance(last, dict) and last.get("role") == "human"
            and last.get("content") == text and not prepared
        )
        if not same_unanswered:
            saved = _store_images(root, session_id, prepared)
            metadata: dict[str, Any] = {"source": "operator"}
            if saved:
                metadata["images"] = saved
            _append_message(root, session_id, "human", text, metadata)
            messages = _read_messages(_messages_file(root, session_id))
    if backend is None:
        _schedule_follow_up(root, session_id)
        record, messages = load_incident(root, session_id)
        return {"ok": True, "pending": True, "session": record, "messages": messages}
    return _run_model_follow_up(root, session_id, backend)


def _finish(research_root: Path, session_id: str, status: str, note: str) -> dict[str, Any]:
    if status not in {"resolved", "closed"}:
        raise IncidentSessionError("unknown incident close status")
    root = Path(research_root)
    with _lock(root):
        path = _session_file(root, session_id)
        record = _read_session(path)
        if record.get("status") != "active":
            raise IncidentSessionError("supervisor incident session is closed", status=409)
        record["status"] = status
        record["closed_at"] = now()
        write_json(path, record)
        if note.strip():
            _append_message(root, session_id, "system", note.strip(), {"source": status})
        else:
            _append_message(root, session_id, "system", f"session {status}", {"source": status})
        messages = _read_messages(_messages_file(root, session_id))
    cycle_id = str(record.get("cycle_dirname") or record.get("cycle_id") or "")
    if cycle_id and not cycle_id.startswith(".") and "/" not in cycle_id:
        cycle_dir = root / cycle_id
        if cycle_dir.is_dir() and not cycle_dir.is_symlink():
            _write_link(cycle_dir, record)
    return {"ok": True, "session": record, "messages": messages}


def resolve_incident(research_root: Path, session_id: str, note: str = "") -> dict[str, Any]:
    return _finish(research_root, session_id, "resolved", note or "incident resolved")


def close_incident(research_root: Path, session_id: str, note: str = "") -> dict[str, Any]:
    return _finish(research_root, session_id, "closed", note or "incident closed")


def capture_turn_reads(backend: Any) -> list[str]:
    consume = getattr(backend, "consume_turn_reads", None)
    if not callable(consume):
        return []
    try:
        found = consume()
    except Exception:
        return []
    if not isinstance(found, list):
        return []
    reads: list[str] = []
    for item in found[:40]:
        text = " ".join(str(item).split())
        if text:
            reads.append(text[:500])
    return reads
