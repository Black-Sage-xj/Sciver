"""Independent immutable Plan and Scientific Model histories; legacy snapshots retained."""
from __future__ import annotations
import difflib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _home(plan: Path) -> Path:
    base = Path(plan).parent / ".theory-master"
    if base.is_symlink() or not base.is_dir():
        raise ValueError("unsafe task state directory")
    home = base / "research-plan-versions"
    if home.is_symlink():
        raise ValueError("unsafe history directory")
    home.mkdir(mode=0o700, exist_ok=True)
    return home

@contextmanager
def plan_lock(plan: Path):
    base = Path(plan).parent / ".theory-master"
    if base.is_symlink() or not base.is_dir():
        raise ValueError("unsafe plan lock directory")
    lock = base / "web-research-plan.lock"
    if lock.is_symlink():
        raise ValueError("unsafe plan lock")
    with lock.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield

SCOPES = ("plan", "model")
MODEL_HEADER = re.compile(r"(?m)^# Scientific Model[ \t]*\r?$")


def project(raw: str, scope: str = "plan") -> str:
    if scope not in SCOPES:
        raise ValueError("invalid history scope")
    matches = list(MODEL_HEADER.finditer(raw))
    if len(matches) > 1:
        raise ValueError("duplicate Scientific Model sections")
    if not matches:
        return raw if scope == "plan" else ""
    start = matches[0].start()
    return raw[:start] if scope == "plan" else raw[start:]


def merge(current: str, snapshot: str, scope: str) -> str:
    """Restore one scope while preserving the latest other scope verbatim."""
    if scope == "plan":
        if project(snapshot, "model"):
            raise ValueError("Plan snapshot contains Scientific Model")
        return snapshot + project(current, "model")
    if scope != "model" or project(snapshot, "plan") or not MODEL_HEADER.match(snapshot):
        raise ValueError("invalid Scientific Model snapshot")
    return project(current, "plan") + snapshot


def _rows(index: Path) -> list[dict]:
    if index.is_symlink() or (index.is_file() and index.stat().st_size > 12_000_000):
        raise ValueError("unsafe plan history")
    if not index.exists():
        return []
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row.get("version"), int) or not isinstance(row.get("sha256"), str)
           or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) for row in rows):
        raise ValueError("corrupt plan history")
    return rows


def versions(plan: Path, scope: str = "plan") -> list[dict]:
    if scope not in SCOPES:
        raise ValueError("invalid history scope")
    return _rows(_home(plan) / f"{scope}-history.jsonl")


def _append(plan: Path, raw: str, *, scope: str, reason: str, source: str,
            section: str = "", recorded_at: str | None = None,
            legacy_version: int | None = None) -> dict:
    rows = versions(plan, scope)
    sha = digest(raw)
    if rows and rows[-1]["sha256"] == sha:
        return rows[-1]
    home = _home(plan)
    data = raw.encode("utf-8")
    snapshot = home / (sha + ".md")
    if snapshot.is_symlink():
        raise ValueError("unsafe plan snapshot")
    if snapshot.exists():
        if snapshot.read_bytes() != data:
            raise ValueError("snapshot checksum mismatch")
    else:
        fd, tmp = tempfile.mkstemp(prefix=".snapshot-", dir=home)
        try:
            with os.fdopen(fd, "wb") as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, snapshot)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    row = {"version": rows[-1]["version"] + 1 if rows else 1,
           "scope": scope, "sha256": sha,
           "previous_sha256": rows[-1]["sha256"] if rows else None,
           "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": source, "section": section, "reason": reason.strip()}
    if legacy_version is not None:
        row["legacy_version"] = legacy_version
    index = home / f"{scope}-history.jsonl"
    if index.is_symlink():
        raise ValueError("unsafe plan history index")
    with index.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())
    return row


def _snapshot(plan: Path, row: dict) -> str:
    file = _home(plan) / (row["sha256"] + ".md")
    if file.is_symlink() or not file.is_file():
        raise ValueError("历史快照文件无效")
    raw = file.read_text(encoding="utf-8")
    if digest(raw) != row["sha256"]:
        raise ValueError("历史快照校验失败")
    return raw


def _migrate(plan: Path) -> None:
    """Under the shared lock, project old snapshots without changing their ledger."""
    home = _home(plan)
    marker = home / "split-history-v1.json"
    if marker.is_symlink():
        raise ValueError("unsafe migration marker")
    if marker.exists():
        return
    legacy = _rows(home / "history.jsonl")
    # Each stream can resume a partially completed migration independently.
    last = {scope: max((r.get("legacy_version", 0) for r in versions(plan, scope)), default=0)
            for scope in SCOPES}
    for row in legacy:
        raw = _snapshot(plan, row)
        for scope in SCOPES:
            if row["version"] > last[scope]:
                _append(plan, project(raw, scope), scope=scope,
                        reason=row.get("reason") or "历史版本迁移",
                        source=row.get("source") or "legacy", section=row.get("section") or "",
                        recorded_at=row.get("recorded_at"), legacy_version=row["version"])
    # The marker is advisory: migration can safely resume if interrupted before it.
    marker.write_text(json.dumps({"schema": "sciver/split-plan-history/v1",
                                  "legacy_count": len(legacy)}) + "\n", encoding="utf-8")


def record(plan: Path, raw: str, *, reason: str, source: str, section: str = "") -> dict:
    """Under plan_lock, record only the independently changed scope(s)."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1200:
        raise ValueError("必须提供 1-1200 字的更正问题说明")
    if len(raw.encode("utf-8")) > 1_048_576:
        raise ValueError("plan snapshot too large")
    _migrate(plan)
    rows = {scope: _append(plan, project(raw, scope), scope=scope, reason=reason,
                           source=source, section=section) for scope in SCOPES}
    return {**rows["plan"], "model_version": rows["model"]["version"]}


def capture_current(plan: Path) -> dict:
    """Under plan_lock, detect changes independently for each content scope."""
    raw = Path(plan).read_text(encoding="utf-8")
    _migrate(plan)
    external = any((rows := versions(plan, scope))
                   and rows[-1]["sha256"] != digest(project(raw, scope)) for scope in SCOPES)
    return record(plan, raw,
                  reason=("检测到外部保存；原始更正说明未提供" if external
                          else "启用独立版本历史，现存内容为基线版本"),
                  source="external-observed" if external else "baseline")


def content(plan: Path, version: int, scope: str = "plan") -> str:
    row = next((x for x in versions(plan, scope) if x["version"] == version), None)
    if row is None:
        raise ValueError("历史版本不存在")
    return _snapshot(plan, row)


def compare(plan: Path, version: int, current: str, scope: str = "plan") -> str:
    return "".join(difflib.unified_diff(
        content(plan, version, scope).splitlines(keepends=True),
        project(current, scope).splitlines(keepends=True),
        fromfile=f"{scope}-v{version}", tofile=f"{scope}-current"))[:200000]
