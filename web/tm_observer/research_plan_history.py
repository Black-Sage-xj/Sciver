"""Immutable full-text Research Plan snapshots with append-only change rationale."""
from __future__ import annotations
import difflib
import fcntl
import hashlib
import json
import os
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

def versions(plan: Path) -> list[dict]:
    index = _home(plan) / "history.jsonl"
    if index.is_symlink() or (index.is_file() and index.stat().st_size > 12_000_000):
        raise ValueError("unsafe plan history")
    if not index.exists():
        return []
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row.get("version"), int) or not isinstance(row.get("sha256"), str) for row in rows):
        raise ValueError("corrupt plan history")
    return rows

def record(plan: Path, raw: str, *, reason: str, source: str, section: str = "") -> dict:
    """Call while holding plan_lock. Store before next plan write for durability."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1200:
        raise ValueError("必须提供 1-1200 字的更正问题说明")
    data = raw.encode("utf-8")
    if len(data) > 1_048_576:
        raise ValueError("plan snapshot too large")
    rows = versions(plan)
    sha = digest(raw)
    if rows and rows[-1]["sha256"] == sha:
        return rows[-1]
    home = _home(plan)
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
           "sha256": sha, "previous_sha256": rows[-1]["sha256"] if rows else None,
           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": source, "section": section, "reason": reason.strip()}
    index = home / "history.jsonl"
    if index.is_symlink():
        raise ValueError("unsafe plan history index")
    with index.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())
    return row

def capture_current(plan: Path) -> dict:
    """Under plan_lock, record external changes on discovery, without fabricated reasons."""
    raw = Path(plan).read_text(encoding="utf-8")
    rows = versions(plan)
    external = bool(rows) and rows[-1]["sha256"] != digest(raw)
    return record(plan, raw,
                  reason=("检测到外部保存；原始更正说明未提供" if external
                          else "启用版本历史，现存 Plan 为基线版本"),
                  source="external-observed" if external else "baseline")

def content(plan: Path, version: int) -> str:
    row = next((x for x in versions(plan) if x["version"] == version), None)
    if row is None:
        raise ValueError("历史版本不存在")
    file = _home(plan) / (row["sha256"] + ".md")
    if file.is_symlink() or not file.is_file():
        raise ValueError("历史快照文件无效")
    raw = file.read_text(encoding="utf-8")
    if digest(raw) != row["sha256"]:
        raise ValueError("历史快照校验失败")
    return raw

def compare(plan: Path, version: int, current: str) -> str:
    return "".join(difflib.unified_diff(
        content(plan, version).splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"v{version}", tofile="current"))[:200000]
