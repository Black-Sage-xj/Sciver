"""Bounded, versioned skill storage with optimistic updates and event outbox."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROLES = frozenset({"investigator", "literature"})
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
MAX_BODY = 6000
MAX_ACTIVE = 200


class SkillError(ValueError):
    """Invalid, stale or unsupported procedural-memory operation."""


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def library_home() -> Path:
    configured = os.environ.get("SCIVER_SKILL_LIBRARY")
    if configured:
        return Path(configured).expanduser().absolute()
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "sciver/skill-evolution"


def text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise SkillError(f"invalid {label}")
    return value.strip()


def safe_directory(path: Path) -> Path:
    """Do not let an Agent-controlled symlink redirect SDK writes."""
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise SkillError("skill storage cannot contain symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


class SkillLibrary:
    def __init__(self, home: Path | None = None):
        self.home = (home or library_home()).expanduser().absolute()
        self.path = self.home / "library.sqlite3"

    @contextmanager
    def _db(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        safe_directory(self.home)
        if self.path.is_symlink():
            raise SkillError("linked skill database")
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL,
                    scope TEXT NOT NULL, role TEXT NOT NULL, version TEXT NOT NULL,
                    description TEXT NOT NULL, tags TEXT NOT NULL, status TEXT NOT NULL,
                    updated TEXT NOT NULL, UNIQUE(owner, role, name));
                CREATE TABLE IF NOT EXISTS releases (
                    skill TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(skill, version));
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY, skill TEXT, source TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, task TEXT NOT NULL, payload TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS usages (
                    id TEXT PRIMARY KEY, skill TEXT NOT NULL, version TEXT NOT NULL,
                    kind TEXT NOT NULL, source TEXT NOT NULL);
            """)
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _release(self, db: sqlite3.Connection, skill: str, version: str) -> dict[str, Any]:
        row = db.execute("SELECT payload FROM releases WHERE skill=? AND version=?",
                         (skill, version)).fetchone()
        if row is None:
            raise SkillError("unknown skill version")
        release = json.loads(row[0])
        expected = release["files"]
        root = self.home / "releases" / skill / version
        if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
            raise SkillError("skill release unavailable")
        for name, expected_hash in expected.items():
            path = root / name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise SkillError("skill release integrity changed")
        return {**release, "path": str(root)}

    def release(self, skill: str, version: str) -> dict[str, Any]:
        with self._db() as db:
            return self._release(db, skill, version)

    def owned(self, *, owner: str, role: str, name: str) -> dict[str, Any] | None:
        """Resolve an operator-owned entry, including recoverable archived skills."""
        if not self.path.exists():
            return None
        with self._db() as db:
            row = db.execute("SELECT id,version,status FROM skills WHERE owner=? AND role=? AND name=?",
                             (owner, role, name)).fetchone()
            return {**self._release(db, row["id"], row["version"]), "status": row["status"]} if row else None

    def catalogue(self, *, owner: str, role: str, query: str = "", limit: int = 5,
                  shared: bool = True) -> list[dict[str, Any]]:
        if not self.path.exists() or role not in ROLES:
            return []
        with self._db() as db:
            rows = db.execute("SELECT * FROM skills WHERE role=? AND status='active' "
                              "AND (owner=? OR (? AND scope='general')) ORDER BY updated DESC LIMIT 200",
                              (role, owner, int(shared))).fetchall()
            terms = set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2}", query.lower()))
            ranked = []
            for row in rows:
                haystack = (row["name"] + " " + row["description"] + " " + row["tags"]).lower()
                score = sum(word in haystack for word in terms)
                if row["owner"] != owner and not score:
                    continue
                ranked.append((score, row["owner"] == owner, row))
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            result = []
            for _, _, row in ranked[:max(0, min(limit, MAX_ACTIVE))]:
                try:
                    release = self._release(db, row["id"], row["version"])
                    counts = {r[0]: r[1] for r in db.execute(
                        "SELECT kind,count(*) FROM usages WHERE skill=? AND version=? GROUP BY kind",
                        (row["id"], row["version"]))}
                    last = db.execute("SELECT source FROM usages WHERE skill=? AND version=? "
                                      "AND kind IN ('reported_failure','corrected') ORDER BY rowid DESC LIMIT 1",
                                      (row["id"], row["version"])).fetchone()
                    release["usage_counts"] = counts
                    release["revision_hint"] = json.loads(last[0]).get("detail", "")[:400] if last else ""
                    result.append(release)
                except SkillError:
                    continue  # A broken optional skill must not stop research.
            return result

    def record_use(self, *, skill: str, version: str, kind: str, source: dict[str, Any]) -> None:
        if kind not in {"loaded", "reported_success", "reported_failure", "corrected", "unknown"}:
            raise SkillError("invalid skill observation")
        key = digest([skill, version, kind, source])
        with self._db(write=True) as db:
            self._release(db, skill, version)
            db.execute("INSERT OR IGNORE INTO usages VALUES (?,?,?,?,?)",
                       (key, skill, version, kind, json.dumps(source, ensure_ascii=False)))

    def maintain(self, note: dict[str, Any], *, owner: str, role: str,
                 source: dict[str, Any], files: dict[str, bytes] | None = None) -> dict[str, Any]:
        """Publish one explicit, source-bound change, never infer scientific success."""
        if role not in ROLES or not isinstance(note, dict):
            raise SkillError("invalid skill author")
        operation = note.get("operation", "create")
        if operation not in {"create", "update", "merge", "archive", "rollback"}:
            raise SkillError("invalid skill operation")
        name = text(note.get("name"), "name", 56)
        if not NAME.fullmatch(name):
            raise SkillError("skill name must be lowercase words separated by hyphens")
        skill = digest([owner, role, name])[:24]
        observation = digest([source, note])
        with self._db(write=True) as db:
            prior = db.execute("SELECT * FROM skills WHERE id=?", (skill,)).fetchone()
            saved = db.execute("SELECT source FROM observations WHERE id=?", (observation,)).fetchone()
            if saved:
                return {"status": "duplicate", "skill_id": skill}
            if operation == "create" and prior and prior["status"] == "archived":
                raise SkillError("archived skill needs an explicit versioned update or rollback")
            if operation != "create" and (not prior or note.get("base_version") != prior["version"]):
                raise SkillError("stale skill base_version; keep the current release")
            before = prior["version"] if prior else None
            absorbed = []
            if (operation != "archive" and (prior is None or prior["status"] != "active")
                    and db.execute("SELECT count(*) FROM skills WHERE status='active'").fetchone()[0] >= MAX_ACTIVE):
                raise SkillError("skill library capacity reached; merge or archive existing entries")
            if operation in {"archive", "rollback"}:
                version = text(note.get("target_version"), "target_version", 64) if operation == "rollback" else before
                release = self._release(db, skill, version)
                if operation == "rollback":
                    # Rollback restores the selected version's index metadata too.
                    db.execute("UPDATE skills SET version=?,description=?,tags=?,scope=?,status='active',updated=? WHERE id=?",
                               (version, release["description"], json.dumps(release["tags"]),
                                release["scope"], timestamp(), skill))
                else:
                    db.execute("UPDATE skills SET status='archived',updated=? WHERE id=?", (timestamp(), skill))
            else:
                description = text(note.get("description"), "description", 400)
                body = text(note.get("body"), "body", MAX_BODY)
                conditions = text(note.get("when_to_use"), "when_to_use", 800)
                scope = note.get("scope", "task")
                if scope not in {"task", "general"}:
                    raise SkillError("invalid scope")
                tags = note.get("tags", [])
                if not isinstance(tags, list) or len(tags) > 8 or any(not isinstance(t, str) or not 1 <= len(t) <= 40 for t in tags):
                    raise SkillError("invalid skill tags")
                if scope == "general" and not tags:
                    raise SkillError("general skills need relevance tags")
                supplied = dict(files or {})
                if prior:
                    previous = self._release(db, skill, prior["version"])
                    for filename in previous["files"]:
                        if filename != "SKILL.md" and filename not in supplied:
                            supplied[filename] = (Path(previous["path"]) / filename).read_bytes()
                if len(supplied) > 3 or sum(len(b) for b in supplied.values()) > 96_000:
                    raise SkillError("skill supporting files exceed limit")
                for filename in supplied:
                    if not re.fullmatch(r"scripts/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}\.(py|sh|js|R)", filename):
                        raise SkillError("invalid skill supporting file")
                content = ("---\nname: learned-" + name + "\ndescription: " + json.dumps(description, ensure_ascii=False)
                           + "\n---\n\n# Applicability\n" + conditions + "\n\n# Procedure\n" + body
                           + "\n\n# Evidence level\nTrajectory-derived procedural guidance. "
                             "No independent effectiveness evaluation. Scientific and role contracts take precedence.\n")
                payload_files = {"SKILL.md": content.encode(), **supplied}
                hashes = {p: hashlib.sha256(b).hexdigest() for p, b in payload_files.items()}
                version = digest([hashes, scope, tags])
                if operation == "create" and prior and prior["version"] != version:
                    raise SkillError("skill already exists; submit an explicit versioned update")
                release = {"id": skill, "name": name, "role": role, "owner": owner, "scope": scope,
                           "version": version, "description": description, "tags": tags,
                           "when_to_use": conditions, "files": hashes, "evidence_level": "trajectory_derived"}
                root = safe_directory(self.home / "releases" / skill / version)
                for filename, data in payload_files.items():
                    target = root / filename
                    safe_directory(target.parent)
                    if target.exists():
                        if target.is_symlink() or target.read_bytes() != data:
                            raise SkillError("immutable skill release conflict")
                    else:
                        # Exclusive creation prevents concurrent replacement of an immutable release.
                        with target.open("xb") as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                db.execute("INSERT OR IGNORE INTO releases VALUES (?,?,?)",
                           (skill, version, json.dumps(release, ensure_ascii=False)))
                db.execute("INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                           "scope=excluded.scope,version=excluded.version,description=excluded.description,"
                           "tags=excluded.tags,status='active',updated=excluded.updated",
                           (skill, name, owner, scope, role, version, description, json.dumps(tags), "active", timestamp()))
                if operation == "merge":
                    absorbs = note.get("absorbs")
                    if not isinstance(absorbs, list) or not 1 <= len(absorbs) <= 4:
                        raise SkillError("merge needs versioned source skills")
                    for item in absorbs:
                        if not isinstance(item, dict) or item.get("name") == name:
                            raise SkillError("invalid merge source")
                        absorbed.append({"name": item.get("name"), "version": item.get("version"),
                                         "id": digest([owner, role, item.get("name")])[:24]})
                        updated = db.execute("UPDATE skills SET status='archived' WHERE owner=? AND role=? AND name=? "
                                             "AND version=? AND status='active'",
                                             (owner, role, item.get("name"), item.get("version"))).rowcount
                        if updated != 1:
                            raise SkillError("stale merge source")
            outcome = note.get("outcome", "unknown")
            if outcome not in {"succeeded", "failed", "corrected", "unknown"}:
                raise SkillError("invalid operation outcome")
            db.execute("INSERT INTO observations VALUES (?,?,?,?)",
                       (observation, skill, json.dumps(source, ensure_ascii=False), outcome))
            if before == version and prior["status"] == "active" and operation not in {"archive", "rollback", "merge"}:
                return {"status": "unchanged", "skill_id": skill, "version": version}
            proposal = {
                "id": "SKP-" + observation[:24], "skill_id": skill, "name": name,
                "operation": operation, "from_version": before, "to_version": version,
                "role": role, "source": source, "at": timestamp(),
                "state": "applied_by_enabled_policy", "evidence_level": "trajectory_derived",
                "reason": text(note.get("reason"), "reason", 800),
                "before": self._release(db, skill, before) if before else None,
                "after": self._release(db, skill, version),
                "absorbed_skills": absorbed,
                "effective_at": "next_safe_invocation_catalogue; in-flight bindings unchanged",
            }
            db.execute("INSERT INTO proposals(id,task,payload) VALUES (?,?,?)",
                       (proposal["id"], owner, json.dumps(proposal, ensure_ascii=False)))
            return {"status": "applied", **proposal}

    def pending_events(self, owner: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM proposals WHERE task=? AND delivered=0 ORDER BY rowid LIMIT 50", (owner,))]

    def event_delivered(self, proposal: str) -> None:
        with self._db(write=True) as db:
            db.execute("UPDATE proposals SET delivered=1 WHERE id=?", (proposal,))

    def proposals(self, owner: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM proposals WHERE task=? ORDER BY rowid DESC LIMIT 100", (owner,))]
