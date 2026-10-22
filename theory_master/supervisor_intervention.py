"""Supervisor intervention: one task switch, two bounded review profiles.

The switch replaces the separate "error repair" and "Attention takeover"
settings. Runtime failures and NEEDS_ATTENTION keep different prompts and
authority; they only share the switch, model choice and the visible session.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .runtime import atomic, now, write_json

KEY = "THEORY_MASTER_SUPERVISOR"
LEGACY_ATTENTION_KEY = "THEORY_MASTER_ATTENTION_TAKEOVER"
LEGACY_ERROR_KEY = "THEORY_MASTER_ERROR_REPAIR"
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_PROVIDER = "CodexHTTPS"
DEFAULT_REASONING_EFFORT = "medium"
SUPERVISOR_ROLES = frozenset({"runtime_supervisor", "attention_reviewer", "incident_supervisor"})
SESSION_SCHEMA = "theory-master/supervisor-session/v1"
STATUS_SCHEMA = "theory-master/supervisor-status/v1"
PROFILES = frozenset({"runtime", "attention"})
STATUSES = frozenset({"active", "hold", "returned"})
MAX_RUNTIME_RESUMES = 3

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def _env_path(research_root: Path) -> Path:
    return Path(research_root) / ".theory-master" / "env"


def _task_env_value(research_root: Path, key: str) -> str | None:
    path = _env_path(research_root)
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("'\"")
    return None


def _parse(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return False


def _legacy_error_repair_flag(research_root: Path) -> bool | None:
    try:
        from .operator_preferences import read
        value = read(Path(research_root))["flags"].get("error_repair")
    except (OSError, ValueError):
        return None
    return value if type(value) is bool else None


def supervisor_enabled(research_root: Path) -> bool:
    """Hot-read the switch; a legacy explicit OFF keeps old tasks closed."""
    root = Path(research_root)
    for raw in (os.environ.get(KEY), _task_env_value(root, KEY)):
        parsed = _parse(raw)
        if parsed is not None:
            return parsed
    legacy = (
        _parse(os.environ.get(LEGACY_ATTENTION_KEY)),
        _parse(_task_env_value(root, LEGACY_ATTENTION_KEY)),
        _parse(os.environ.get(LEGACY_ERROR_KEY)),
        _legacy_error_repair_flag(root),
    )
    return not any(value is False for value in legacy)


def write_supervisor_setting(research_root: Path, enabled: bool) -> Path:
    path = _env_path(Path(research_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    output: list[str] = []
    for raw in lines:
        name, sep, _value = raw.partition("=")
        if sep and name.strip() in {KEY, LEGACY_ATTENTION_KEY}:
            continue
        output.append(raw)
    if output and output[-1].strip():
        output.append("")
    output.append(f"{KEY}={'1' if enabled else '0'}")
    atomic(path, "\n".join(output).rstrip() + "\n")
    return path


def supervisor_model(research_root: Path) -> str:
    try:
        from .operator_preferences import read
        models = read(Path(research_root))["models"]
    except (OSError, ValueError):
        return DEFAULT_MODEL
    return models.get("supervisor") or models.get("attention_review") or DEFAULT_MODEL


class SupervisorSession:
    """Append-only visible record under ``<cycle>/supervisor/``.

    ``session.jsonl`` keeps every message; ``status.json`` is the latest
    session summary the Run home screen reads.
    """

    def __init__(self, cycle_dir: Path, *, profile: str, incident_id: str,
                 code: str, summary: str, model: str | None = None):
        if profile not in PROFILES:
            raise ValueError(f"unknown Supervisor profile: {profile}")
        self.cycle_dir = Path(cycle_dir)
        self.folder = self.cycle_dir / "supervisor"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.session_id = f"SUP-{uuid.uuid4().hex[:16]}"
        self.profile = profile
        self.incident_id = incident_id
        self.code = code
        self.summary = summary[:1200]
        self.model = model
        self._seq = self._last_seq()
        self.append("opened", f"{code}: {self.summary}")
        self.set_status("active")

    def _last_seq(self) -> int:
        path = self.folder / "session.jsonl"
        if not path.is_file():
            return 0
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        try:
            return int(json.loads(lines[-1]).get("seq") or 0) if lines else 0
        except (ValueError, AttributeError):
            return len(lines)

    def append(self, kind: str, content: str, **fields: Any) -> None:
        self._seq += 1
        record = {
            "schema": SESSION_SCHEMA, "seq": self._seq, "at": now(),
            "session_id": self.session_id, "profile": self.profile,
            "incident_id": self.incident_id, "code": self.code,
            "kind": kind, "content": str(content), **fields,
        }
        with (self.folder / "session.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def set_status(self, status: str, *, action: str | None = None, reason: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown Supervisor status: {status}")
        write_json(self.folder / "status.json", {
            "schema": STATUS_SCHEMA, "at": now(), "session_id": self.session_id,
            "profile": self.profile, "status": status, "incident_id": self.incident_id,
            "code": self.code, "summary": self.summary, "model": self.model,
            "action": action, "reason": (reason or "")[:1200],
        })

    def close(self, status: str, *, action: str, reason: str) -> None:
        self.append("decision", reason, action=action, status=status)
        self.set_status(status, action=action, reason=reason)


def append_supervisor_verification(cycle_dir: Path, *, session_id: str,
                                   verified: bool, detail: str) -> None:
    """Persist a real recovery check against the original Supervisor session.

    This is a message in the append-only session history, not an alteration of
    frozen science or of the existing authorization decision.
    """
    folder = Path(cycle_dir) / "supervisor"
    path = folder / "session.jsonl"
    if path.is_symlink() or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        seq = int(json.loads(lines[-1]).get("seq") or 0) + 1 if lines else 1
        bindings = [json.loads(line) for line in lines]
        binding = next((row for row in reversed(bindings)
                        if row.get("session_id") == session_id
                        and row.get("kind") == "repair_binding"), {})
    except (OSError, ValueError, TypeError, AttributeError):
        return
    record = {
        "schema": SESSION_SCHEMA, "seq": seq, "at": now(),
        "session_id": session_id, "profile": "runtime", "code": "run_failed",
        "kind": "verification", "status": "verified" if verified else "unverified",
        "repair_receipt": binding.get("repair_receipt"),
        "budget_reconciliation": binding.get("budget_reconciliation"),
        "verification_scope": "model_or_tool_activity_only", "root_cause_resolved": False,
        "content": ("Managed resume verified: " if verified else
                    "Managed resume not verified: ") + str(detail)[:550],
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        from .supervisor_recovery import record_activity
        record_activity(Path(cycle_dir), verified=verified, detail=detail, session_id=session_id)
    except (OSError, ValueError):
        pass


def mark_returned(cycle_dir: Path) -> None:
    """Flip an active/hold Attention status to returned after the SDK resumes science."""
    path = Path(cycle_dir) / "supervisor" / "status.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(current, dict) and current.get("status") != "returned":
        current.update(status="returned", at=now())
        write_json(path, current)
