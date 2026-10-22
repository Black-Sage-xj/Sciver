"""Transactional science graph storage for the Hypothesis + External model.

A Cycle Outcome is a record owned by exactly one Hypothesis, not a graph node.
Legacy Canvas import is an explicit, conservative, idempotent operation; this
module never modifies or auto-imports the original Canvas.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .goal_terminal import GoalSealedError

SCHEMA_VERSION = 2
GOAL_CLOSE_REASON = "Final Gate passed; no remaining active research frontier"
GOAL_COMPLETED_STOP = "goal_completed_stop"
_STATUSES = {"active", "parked", "closed"}
_H_STATES = {"none", "open", "supported", "rejected"}
_NODE_TYPES = {"Hypothesis", "Decision", "Finding", "StageSummary", "External"}
_HEADER = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):\s*(.*?)\s*$")
_LINK = re.compile(r"\[\[([^]|]+)")
_SECTION = re.compile(r"^## (Established after this Cycle|Unresolved|Boundary)\s*$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_header(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines()[:35]:
        if not line.strip():
            if result:
                break
            continue
        match = _HEADER.fullmatch(line.strip())
        if not match:
            break
        result.setdefault(match.group(1), match.group(2))
    return result


def _title_and_summary(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    if not title:
        return "", ""
    begin = next(i for i, line in enumerate(lines) if line.startswith("# ")) + 1
    summary: list[str] = []
    for line in lines[begin:]:
        if line.startswith(("## ", "**Decisive test**:", "**Judge rationale**:",
                            "**Evidence refs**:", "[[", "Goal Completion:")):
            break
        summary.append(line)
    return title, "\n".join(summary).strip()


_SEARCH_TERM = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_SEARCH_ID = re.compile(r"H-\d{4,}|external-[A-Za-z0-9._-]{1,160}", re.IGNORECASE)


def search_terms(query: str) -> list[str]:
    """Deterministic query terms. No stemming and no second model."""
    seen: list[str] = []
    for match in _SEARCH_TERM.findall(str(query or "").casefold()):
        if match not in seen:
            seen.append(match)
        if len(seen) >= 12:
            break
    return seen


def compact_external_summary(content: Any, limit: int = 180) -> str:
    """Return a short human/Agent preview without changing the stored source."""
    raw = str(content or "").strip()
    if not raw or limit <= 0:
        return ""
    _, summary = _title_and_summary(raw)
    if not summary:
        heading = re.search(r"(?m)^# .+$", raw)
        summary = raw[heading.end():] if heading else raw
    text = " ".join(summary.split())
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "…"


def external_detail_ref(external_id: Any) -> str | None:
    """Safe, stable Obsidian/Explorer path for an External's full detail."""
    ident = str(external_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", ident):
        return None
    return f"notes/external/{ident}.md"


def _cycle_sections(text: str) -> dict[str, list[str]]:
    names = {
        "Established after this Cycle": "findings",
        "Unresolved": "open_questions",
        "Boundary": "boundary",
    }
    result: dict[str, list[str]] = {x: [] for x in names.values()}
    active: str | None = None
    for raw in text.splitlines():
        match = _SECTION.match(raw)
        if match:
            active = names[match.group(1)]
            continue
        if raw.startswith(("## ", "**", "[[")):
            active = None
        if active and raw.lstrip().startswith("- "):
            result[active].append(raw.lstrip()[2:].strip())
    return result


def _bold_field(text: str, field: str) -> str:
    prefix = f"**{field}**:"
    return next((line[len(prefix):].strip() for line in text.splitlines()
                 if line.startswith(prefix)), "")


class ScienceGraphStore:
    """Single-host SQLite canonical graph store; all writes use IMMEDIATE txn."""

    def __init__(self, database: str | Path, *, readonly: bool = False,
                 allow_after_goal_seal: bool = False):
        self.path = Path(database)
        self.readonly = readonly
        # Only the seal transaction and non-scientific derived indexes
        # (e.g. cycle_memory_index) may write after the Goal is sealed.
        self.allow_after_goal_seal = allow_after_goal_seal
        self._sealing = False
        if readonly:
            if not self.path.is_file() or self.path.is_symlink():
                raise ValueError("Read-only science graph is missing or unsafe")
            self.db = sqlite3.connect(
                f"file:{self.path.resolve().as_posix()}?mode=ro",
                timeout=30, isolation_level=None, uri=True,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self._savepoint_seq = 0
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 30000")
        if readonly:
            self.db.execute("PRAGMA query_only = ON")
            try:
                row = self.db.execute(
                    "SELECT value FROM graph_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None or row["value"] != str(SCHEMA_VERSION):
                    raise ValueError("Unsupported science graph schema")
            except sqlite3.DatabaseError as exc:
                raise ValueError("Invalid science graph schema") from exc
        else:
            # Verify schema compatibility before modifying journal mode.
            self._schema()
            self.db.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ScienceGraphStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _schema(self) -> None:
        # Never even add V2 tables to an existing V1 database. Conversion must
        # be explicit and backed up; an accidental open must fail without DDL.
        prior = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_meta'"
        ).fetchone()
        if prior:
            row = self.db.execute(
                "SELECT value FROM graph_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None or row["value"] != str(SCHEMA_VERSION):
                raise RuntimeError("Existing science graph schema is not V2; explicit backup/migration required")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS graph_meta(
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hypotheses(
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '', scientific_status TEXT NOT NULL DEFAULT 'none',
                dossier_ref TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcomes(
                id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL UNIQUE REFERENCES hypotheses(id),
                cycle_id TEXT NOT NULL DEFAULT '',
                verdict TEXT NOT NULL DEFAULT 'open'
                    CHECK(verdict IN ('open','supported','rejected')),
                summary TEXT NOT NULL DEFAULT '',
                decisive_test TEXT NOT NULL DEFAULT '', judge_rationale TEXT NOT NULL DEFAULT '',
                findings_json TEXT NOT NULL DEFAULT '[]', open_questions_json TEXT NOT NULL DEFAULT '[]',
                boundary_json TEXT NOT NULL DEFAULT '[]', evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                report_ref TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cycle_evaluations(
                id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
                cycle_id TEXT NOT NULL, verdict TEXT NOT NULL,
                source_json TEXT NOT NULL, recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cycle_preservations(
                cycle_id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
                source_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS cycle_preservations_by_h
                ON cycle_preservations(hypothesis_id, cycle_id);
            CREATE TABLE IF NOT EXISTS hypothesis_cycle_owners(
                cycle_id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL UNIQUE REFERENCES hypotheses(id),
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scientific_edges(
                id TEXT PRIMARY KEY, source_hypothesis TEXT NOT NULL REFERENCES hypotheses(id),
                target_hypothesis TEXT NOT NULL REFERENCES hypotheses(id),
                evolution_operator TEXT, originating_outcome TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                CHECK(source_hypothesis != target_hypothesis)
            );
            CREATE TABLE IF NOT EXISTS external_records(
                id TEXT PRIMARY KEY, content_type TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_edges(
                id TEXT PRIMARY KEY, hypothesis_id TEXT REFERENCES hypotheses(id),
                external_id TEXT NOT NULL REFERENCES external_records(id),
                edge_type TEXT NOT NULL DEFAULT 'external_reference',
                originating_outcome TEXT
            );
            CREATE TABLE IF NOT EXISTS external_relations(
                id TEXT PRIMARY KEY,
                source_external TEXT NOT NULL REFERENCES external_records(id),
                target_external TEXT NOT NULL REFERENCES external_records(id),
                edge_type TEXT NOT NULL DEFAULT 'external_reference'
            );
            CREATE TABLE IF NOT EXISTS legacy_edges(
                id TEXT PRIMARY KEY,
                raw_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS frontier_branches(
                id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
                anchor_outcome TEXT,
                status TEXT NOT NULL CHECK(status IN ('active','parked','closed')),
                priority TEXT CHECK(priority IS NULL OR priority = 'preferred'),
                reason TEXT NOT NULL DEFAULT '', changed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS legacy_frontier_archive(
                id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL,
                anchor_outcome TEXT, status TEXT NOT NULL,
                priority TEXT, reason TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL, archived_at TEXT NOT NULL,
                provenance TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS graph_events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                action TEXT NOT NULL, subject TEXT NOT NULL, detail_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS node_layout(
                id TEXT PRIMARY KEY, x REAL, y REAL, width REAL, height REAL
            );
            CREATE TABLE IF NOT EXISTS legacy_records(
                id TEXT PRIMARY KEY, node_type TEXT NOT NULL, raw_json TEXT NOT NULL,
                diagnostic TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS legacy_canvas_archive(
                digest TEXT PRIMARY KEY, original_name TEXT NOT NULL,
                original_bytes BLOB NOT NULL, node_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS legacy_conversion_map(
                source_digest TEXT NOT NULL REFERENCES legacy_canvas_archive(digest),
                legacy_position INTEGER NOT NULL,
                legacy_id TEXT, old_type TEXT NOT NULL,
                target_kind TEXT NOT NULL, target_id TEXT,
                note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(source_digest, legacy_position)
            );
            CREATE TABLE IF NOT EXISTS cycle_memory_index(
                cycle_id TEXT PRIMARY KEY,
                hypothesis_id TEXT,
                worker_id TEXT,
                execution_mode TEXT NOT NULL,
                concurrent_workers_at_start INTEGER,
                status TEXT NOT NULL,
                cycle_dir TEXT NOT NULL,
                memory_components_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_goal_terminal(
                goal_id TEXT PRIMARY KEY,
                verdict TEXT NOT NULL CHECK(verdict='passed'),
                source_cycle TEXT NOT NULL,
                sealed_at TEXT NOT NULL,
                research_plan_sha256 TEXT NOT NULL,
                judge_receipt_ref TEXT,
                detail_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memory_index_by_h ON cycle_memory_index(hypothesis_id, cycle_id);
            CREATE INDEX IF NOT EXISTS cycle_evaluations_by_h ON cycle_evaluations(hypothesis_id, cycle_id);
            CREATE UNIQUE INDEX IF NOT EXISTS one_scientific_outcome_per_cycle
                ON cycle_evaluations(cycle_id);
            CREATE INDEX IF NOT EXISTS frontier_by_h ON frontier_branches(hypothesis_id, status);
            CREATE INDEX IF NOT EXISTS edges_by_source ON scientific_edges(source_hypothesis);
        """)
        old = self.db.execute("SELECT value FROM graph_meta WHERE key='schema_version'").fetchone()
        if old is None:
            self.db.execute("INSERT INTO graph_meta(key,value) VALUES ('schema_version',?)",
                            (str(SCHEMA_VERSION),))
        elif int(old["value"]) != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported graph schema {old['value']}; expected {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.readonly:
            raise ValueError("Read-only science graph cannot be modified")
        # Nested writes use SAVEPOINT so an entire migration remains atomic.
        if self.db.in_transaction:
            self._savepoint_seq += 1
            name = f"science_graph_sp_{self._savepoint_seq}"
            self.db.execute(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.db.execute(f"RELEASE SAVEPOINT {name}")
                raise
            else:
                self.db.execute(f"RELEASE SAVEPOINT {name}")
            return
        self.db.execute("BEGIN IMMEDIATE")
        if not (self.allow_after_goal_seal or self._sealing):
            try:
                terminal = self._goal_terminal_row()
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            if terminal is not None:
                self.db.execute("ROLLBACK")
                raise GoalSealedError(
                    f"Goal {terminal['goal_id']} sealed by Cycle {terminal['source_cycle']}; "
                    "late scientific write rejected"
                )
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _goal_terminal_row(self) -> sqlite3.Row | None:
        # Read-only connections to older V2 databases may lack the table.
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_goal_terminal'"
        ).fetchone():
            return None
        return self.db.execute(
            "SELECT * FROM task_goal_terminal ORDER BY sealed_at, goal_id LIMIT 1"
        ).fetchone()

    def goal_terminal(self) -> dict[str, Any] | None:
        """Task-wide sealed Goal verdict, or None when the Goal is still open."""
        row = self._goal_terminal_row()
        if row is None:
            return None
        record = {key: row[key] for key in row.keys() if key != "detail_json"}
        record["detail"] = json.loads(row["detail_json"])
        return record

    def seal_goal(self, *, goal_id: str, source_cycle: str, research_plan_sha256: str,
                  judge_receipt_ref: str | None, detail: dict[str, Any]) -> dict[str, Any]:
        """Persist Goal PASS once and close every open frontier in the same transaction.

        Idempotent: an existing seal is returned unchanged (``newly_sealed`` False).
        A frontier whose H is owned by a Cycle without its own Outcome (still
        running) is closed as ``goal_completed_stop`` without any verdict (G8).
        """
        goal_id = str(goal_id or "").strip()
        source_cycle = str(source_cycle or "").strip()
        if not goal_id or not source_cycle:
            raise ValueError("Goal seal requires goal_id and source_cycle")
        if not re.fullmatch(r"[a-f0-9]{64}", str(research_plan_sha256 or "")):
            raise ValueError("Goal seal requires the research_plan.md sha256")
        if not isinstance(detail, dict):
            raise ValueError("Goal seal detail must be an object")
        self._sealing = True
        try:
            with self.transaction():
                if self._goal_terminal_row() is not None:
                    return {**self.goal_terminal(), "newly_sealed": False,
                            "closed_frontier_ids": [], "goal_completed_stop_ids": []}
                records = list(self.db.execute(
                    "SELECT f.*, o.cycle_id AS owner_cycle, "
                    "EXISTS(SELECT 1 FROM cycle_evaluations e WHERE e.cycle_id=o.cycle_id) "
                    "OR EXISTS(SELECT 1 FROM cycle_preservations p WHERE p.cycle_id=o.cycle_id) "
                    "AS owner_has_outcome "
                    "FROM frontier_branches f "
                    "LEFT JOIN hypothesis_cycle_owners o ON o.hypothesis_id=f.hypothesis_id "
                    "WHERE f.status IN ('active','parked') ORDER BY f.id"
                ))
                stopped: list[str] = []
                for entry in records:
                    running = entry["owner_cycle"] is not None and not entry["owner_has_outcome"]
                    if running:
                        stopped.append(str(entry["hypothesis_id"]))
                    self.set_frontier(
                        branch_id=entry["id"], hypothesis_id=entry["hypothesis_id"],
                        anchor_outcome=entry["anchor_outcome"], status="closed",
                        reason=GOAL_COMPLETED_STOP if running else GOAL_CLOSE_REASON,
                        preferred=False,
                    )
                closed = [str(entry["hypothesis_id"]) for entry in records]
                self.db.execute(
                    "INSERT INTO task_goal_terminal(goal_id,verdict,source_cycle,sealed_at,"
                    "research_plan_sha256,judge_receipt_ref,detail_json) VALUES (?,?,?,?,?,?,?)",
                    (goal_id, "passed", source_cycle, _timestamp(), research_plan_sha256,
                     judge_receipt_ref, _json(detail)),
                )
                self._event("goal.sealed", goal_id, {
                    "source_cycle": source_cycle, "closed_frontier_ids": closed,
                    "goal_completed_stop_ids": stopped,
                })
                return {**self.goal_terminal(), "newly_sealed": True,
                        "closed_frontier_ids": closed, "goal_completed_stop_ids": stopped}
        finally:
            self._sealing = False

    def _event(self, action: str, subject: str, detail: Any) -> None:
        self.db.execute(
            "INSERT INTO graph_events(timestamp,action,subject,detail_json) VALUES (?,?,?,?)",
            (_timestamp(), action, subject, _json(detail)),
        )

    def add_hypothesis(self, h_id: str, *, title: str = "", summary: str = "",
                       scientific_status: str = "none", dossier_ref: str | None = None) -> None:
        if not re.fullmatch(r"H-\d{4,}", h_id):
            raise ValueError(f"Invalid stable Hypothesis ID: {h_id}")
        if scientific_status not in _H_STATES:
            raise ValueError(f"Invalid whole-H scientific status: {scientific_status}")
        with self.transaction():
            found = self.db.execute("SELECT id FROM hypotheses WHERE id=?", (h_id,)).fetchone()
            if found:
                return  # Existing science is never silently overwritten.
            self.db.execute(
                "INSERT INTO hypotheses(id,title,summary,scientific_status,dossier_ref,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (h_id, title, summary, scientific_status, dossier_ref, _timestamp()),
            )
            self.db.execute(
                "INSERT INTO outcomes(id,hypothesis_id,cycle_id,verdict,created_at,updated_at)"
                " VALUES (?,?,'','open',?,?)",
                (h_id+"-outcome",h_id,_timestamp(),_timestamp()),
            )
            if scientific_status in {"supported","rejected"}:
                self.db.execute("UPDATE outcomes SET verdict=? WHERE hypothesis_id=?",
                                (scientific_status,h_id))
            self.set_frontier(
                branch_id="frontier:" + h_id, hypothesis_id=h_id,
                status="active", reason="new Hypothesis created",
            )
            self._event("hypothesis.created", h_id, {"scientific_status": scientific_status})

    def register_cycle_hypothesis(self, *, cycle_id: str, hypothesis_id: str) -> None:
        """Bind a newly locked scientific Cycle to its own H, including retries."""
        cycle_id = str(cycle_id).strip()
        if not cycle_id:
            raise ValueError("Cycle identity is required for a new H")
        with self.transaction():
            prior = self.db.execute(
                "SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                (cycle_id,),
            ).fetchone()
            if prior:
                if prior["hypothesis_id"] != hypothesis_id:
                    raise ValueError(f"Cycle {cycle_id} is already bound to another H")
                return
            owner = self.db.execute(
                "SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            if owner:
                raise ValueError(f"H {hypothesis_id} is already owned by Cycle {owner['cycle_id']}")
            if not self.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (hypothesis_id,)).fetchone():
                raise ValueError(f"Unknown H for Cycle lock: {hypothesis_id}")
            self.db.execute(
                "INSERT INTO hypothesis_cycle_owners(cycle_id,hypothesis_id,recorded_at) VALUES (?,?,?)",
                (cycle_id,hypothesis_id,_timestamp()),
            )
            self._event("cycle.hypothesis_locked",cycle_id,{"hypothesis_id":hypothesis_id})

    def append_outcome(self, *, outcome_id: str, hypothesis_id: str, cycle_id: str, verdict: str,
                       title: str = "", summary: str = "", decisive_test: str = "", judge_rationale: str = "",
                       findings: list[str] | None = None, open_questions: list[str] | None = None,
                       boundary: list[str] | None = None, evidence_refs: list[str] | None = None,
                       report_ref: str | None = None,
                       source_decisions: list[dict[str, Any]] | None = None,
                       authorized_revision_reason: str | None = None,
                       record_only: bool = False) -> None:
        """Record a Cycle evaluation, updating the single H Outcome in place.

        The outcome_id is a canonical Cycle Outcome identity. Immutable Cycle
        records are stored in cycle_evaluations. A replay is idempotent; conflicts fail closed.
        """
        if not outcome_id or not cycle_id or verdict not in {"open", "supported", "rejected"}:
            raise ValueError("Cycle evaluation requires ID, Cycle ID and valid verdict")
        evaluation = dict(
            id=outcome_id,hypothesis_id=hypothesis_id,cycle_id=str(cycle_id),
            verdict=verdict,title=title,summary=summary,decisive_test=decisive_test,
            judge_rationale=judge_rationale,findings=findings or [],
            open_questions=open_questions or [],boundary=boundary or [],
            evidence_refs=evidence_refs or [],report_ref=report_ref,
        )
        if source_decisions:
            evaluation["source_decisions"] = source_decisions
        with self.transaction():
            old = self.db.execute(
                "SELECT source_json FROM cycle_evaluations WHERE id=?",(outcome_id,),
            ).fetchone()
            if old:
                if json.loads(old["source_json"]) != evaluation:
                    raise ValueError(f"Cycle evaluation replay conflicts: {outcome_id}")
                return
            if self.db.execute(
                    "SELECT 1 FROM cycle_preservations WHERE cycle_id=?", (str(cycle_id),)
            ).fetchone():
                raise ValueError("unadjudicated preserved Cycle cannot gain fabricated Judge Outcome")
            cycle_prior = self.db.execute(
                "SELECT id FROM cycle_evaluations WHERE cycle_id=?", (str(cycle_id),),
            ).fetchone()
            if cycle_prior:
                raise ValueError(
                    f"Cycle {cycle_id} already has scientific Outcome {cycle_prior['id']}"
                )
            owner = self.db.execute(
                "SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                (str(cycle_id),),
            ).fetchone()
            if owner and owner["hypothesis_id"] != hypothesis_id:
                raise ValueError(f"Cycle {cycle_id} Outcome belongs to {owner['hypothesis_id']}")
            # A new H is the exact frozen Cycle proposition. Legacy broad H
            # records have no owner and retain their separate whole-H status.
            current = self.db.execute(
                "SELECT id,cycle_id,verdict FROM outcomes WHERE hypothesis_id=?",
                (hypothesis_id,),
            ).fetchone()
            if not current:
                raise ValueError(f"Missing unique Outcome for H: {hypothesis_id}")
            if not record_only and current["verdict"] in {"supported", "rejected"}:
                if not authorized_revision_reason or not authorized_revision_reason.strip():
                    raise ValueError(
                        "Terminal H Outcome cannot be silently revised; "
                        "an explicit scientific revision reason is required"
                    )
            self.db.execute(
                "INSERT INTO cycle_evaluations(id,hypothesis_id,cycle_id,verdict,source_json,recorded_at)"
                " VALUES (?,?,?,?,?,?)",
                (outcome_id,hypothesis_id,str(cycle_id),verdict,_json(evaluation),_timestamp()),
            )
            def order(v: str):
                match = re.search(r"\d+",str(v))
                return (int(match.group()) if match else -1,str(v))
            # Historical Cycle verdicts may only adjudicate one frozen local
            # prediction, not the complete parent H. Import their evidence but
            # do not silently promote a local reject/support to whole-H fate.
            if not record_only and (not current["cycle_id"] or order(str(cycle_id)) >= order(current["cycle_id"])):
                self.db.execute(
                    "UPDATE outcomes SET cycle_id=?,verdict=?,summary=?,decisive_test=?,"
                    "judge_rationale=?,findings_json=?,open_questions_json=?,boundary_json=?,"
                    "evidence_refs_json=?,report_ref=?,updated_at=? WHERE hypothesis_id=?",
                    (str(cycle_id),verdict,summary,decisive_test,judge_rationale,
                     _json(findings or []),_json(open_questions or []),
                     _json(boundary or []),_json(evidence_refs or []),
                     report_ref,_timestamp(),hypothesis_id),
                )
                if owner:
                    self.db.execute(
                        "UPDATE hypotheses SET scientific_status=? WHERE id=?",
                        (verdict,hypothesis_id),
                    )
                self._event("outcome.revised",current["id"],{
                    "hypothesis_id":hypothesis_id,"cycle_id":str(cycle_id),
                    "previous_verdict":current["verdict"],"verdict":verdict,
                    "source_evaluation":outcome_id,
                    "authorized_revision_reason":authorized_revision_reason,
                })
            elif report_ref and (not current["cycle_id"] or order(str(cycle_id)) >= order(current["cycle_id"])):
                # A record-only Cycle evaluation must not promote a local verdict
                # to whole-H status, but the canonical H page still needs a link
                # to the latest human-readable Cycle report.
                self.db.execute(
                    "UPDATE outcomes SET report_ref=?,updated_at=? WHERE hypothesis_id=?",
                    (report_ref,_timestamp(),hypothesis_id),
                )
            self._event("cycle.evaluation_registered",outcome_id,{
                "hypothesis_id":hypothesis_id,"cycle_id":str(cycle_id),
                "record_only": record_only,
            })

    def record_preserved_cycle(self, *, hypothesis_id: str, cycle_id: str,
                               summary: str, report_ref: str,
                               source_attention_code: str,
                               prior_judge_objection: str,
                               stage_output_sha256: str) -> dict[str, Any]:
        """One non-Judge, evidence-backed Cycle closure embedded in its H Outcome.

        Unadjudicated is NOT an accepted append_outcome scientific verdict.
        It must never mutate the Judge-owned whole-H outcomes row.
        """
        if not all(str(x or "").strip() for x in (
                hypothesis_id, cycle_id, summary, report_ref, source_attention_code,
                stage_output_sha256)):
            raise ValueError("preserved Cycle requires complete verified provenance")
        if not re.fullmatch(r"[a-f0-9]{64}", stage_output_sha256):
            raise ValueError("preservation Stage SHA is malformed")
        outcome = {
            "id": f"preservation-C{cycle_id}",
            "hypothesis_id": hypothesis_id, "cycle_id": str(cycle_id),
            "verdict": "unadjudicated", "review_state": "unadjudicated",
            "execution_state": "unfinished", "termination_reason": source_attention_code,
            "summary": summary, "prior_judge_objection": prior_judge_objection,
            "judge_rationale": "",
            "decisive_test": "not completed",
            "findings": [], "open_questions": [],
            "boundary": ["阶段性研究结果，不代表 Judge 裁决"],
            "evidence_refs": [], "report_ref": report_ref,
            "document_ref": report_ref, "stage_output_sha256": stage_output_sha256,
        }
        with self.transaction():
            if not self.db.execute(
                    "SELECT 1 FROM hypotheses WHERE id=?", (hypothesis_id,)
            ).fetchone():
                raise ValueError("unknown preserved Hypothesis")
            owner = self.db.execute(
                "SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                (str(cycle_id),)
            ).fetchone()
            if owner and owner["hypothesis_id"] != hypothesis_id:
                raise ValueError("preservation Cycle/Hypothesis ownership mismatch")
            if self.db.execute(
                    "SELECT 1 FROM cycle_evaluations WHERE cycle_id=?", (str(cycle_id),)
            ).fetchone():
                raise ValueError("already Judge-adjudicated Cycle cannot be marked unadjudicated")
            existing = self.db.execute(
                "SELECT source_json FROM cycle_preservations WHERE cycle_id=?", (str(cycle_id),)
            ).fetchone()
            if existing:
                if json.loads(existing["source_json"]) != outcome:
                    raise ValueError("Cycle preservation replay conflicts")
                return outcome
            self.db.execute(
                "INSERT INTO cycle_preservations(cycle_id,hypothesis_id,source_json,recorded_at)"
                " VALUES (?,?,?,?)",
                (str(cycle_id), hypothesis_id, _json(outcome), _timestamp()),
            )
            self._event("cycle.preserved_unadjudicated", outcome["id"], {
                "hypothesis_id": hypothesis_id, "cycle_id": str(cycle_id),
                "scientific_verdict": None,
            })
        return outcome

    def discard_auto_preservation_external(self, *, hypothesis_id: str,
                                           cycle_id: str, node_id: str) -> bool:
        """Migrate only an SDK-owned orphan-free preservation External."""
        edge_id = f"tm-preservation-{hypothesis_id}-{cycle_id}"
        with self.transaction():
            row = self.db.execute(
                "SELECT content_type,source,content FROM external_records WHERE id=?",
                (node_id,),
            ).fetchone()
            if not row:
                return False
            if (row["content_type"] != "StageSummary"
                    or row["source"] != "preserve_research"
                    or f"source_cycle: {cycle_id}" not in row["content"]
                    or f"hypothesis: {hypothesis_id}" not in row["content"]):
                raise ValueError("External is not the expected automated preservation")
            edges = list(self.db.execute(
                "SELECT id,hypothesis_id FROM external_edges WHERE external_id=?", (node_id,)
            ))
            if [(e["id"],e["hypothesis_id"]) for e in edges] != [(edge_id,hypothesis_id)]:
                raise ValueError("External has unexpected linked references")
            if self.db.execute(
                "SELECT 1 FROM external_relations WHERE source_external=? OR target_external=?",
                (node_id,node_id)
            ).fetchone():
                raise ValueError("External is referenced by another External")
            if not self.db.execute(
                "SELECT 1 FROM cycle_preservations WHERE cycle_id=? AND hypothesis_id=?",
                (str(cycle_id),hypothesis_id)
            ).fetchone():
                raise ValueError("preservation Outcome must be stored before removing External")
            self.db.execute("DELETE FROM external_edges WHERE id=?", (edge_id,))
            self.db.execute("DELETE FROM external_records WHERE id=?", (node_id,))
            self._event("preservation.external_merged_into_h", node_id, {
                "hypothesis_id":hypothesis_id, "cycle_id":str(cycle_id),
            })
            return True


    def cycle_evaluation(self, cycle_id: str) -> dict[str, Any] | None:
        """The committed Cycle Outcome of one Cycle (at most one exists), or None."""
        row = self.db.execute(
            "SELECT source_json FROM cycle_evaluations WHERE cycle_id=?", (str(cycle_id),),
        ).fetchone()
        return json.loads(row["source_json"]) if row else None

    def cycle_history(self, hypothesis_id: str) -> list[dict[str, Any]]:
        return [json.loads(row["source_json"]) for row in self.db.execute(
            "SELECT source_json FROM cycle_evaluations WHERE hypothesis_id=?"
            " ORDER BY recorded_at,id",(hypothesis_id,),
        )]

    def cycle_outcomes(self, hypothesis_id: str) -> list[dict[str, Any]]:
        """Return complete final Cycle results without promoting them to whole-H fate."""
        outcomes = self.cycle_history(hypothesis_id)
        preserved = [
            json.loads(row["source_json"]) for row in self.db.execute(
                "SELECT source_json FROM cycle_preservations WHERE hypothesis_id=?",
                (hypothesis_id,),
            )
        ] if self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_preservations'"
        ).fetchone() else []
        if set(str(v.get("cycle_id")) for v in preserved) & set(
                str(v.get("cycle_id")) for v in outcomes):
            raise ValueError("Cycle cannot be both adjudicated and preserved")
        outcomes.extend(preserved)
        for outcome in outcomes:
            originals = [str(item.get("raw_verdict") or "")
                         for item in outcome.get("source_decisions") or []]
            if not originals:
                legacy = self.db.execute(
                    "SELECT raw_json FROM legacy_records WHERE id=?", (outcome["id"],)
                ).fetchone()
                if legacy:
                    original_node = json.loads(legacy["raw_json"])
                    original_header = _read_header(str(original_node.get("text", "")))
                    originals = [original_header.get("verdict") or original_header.get("status") or ""]
            original_verdicts = sorted({item for item in originals if item and item != outcome["verdict"]})
            if original_verdicts:
                outcome["original_verdicts"] = original_verdicts
        return sorted(
            outcomes,
            key=lambda row: (
                int(match.group()) if (match := re.search(r"\d+", str(row["cycle_id"]))) else -1,
                str(row["cycle_id"]),
            ),
        )

    def preserve_unadjudicated_summary(self, *, hypothesis_id: str, cycle_id: str,
                                     node_id: str, title: str, content: str) -> dict[str, str]:
        """Persist a provenance-backed StageSummary external, never a Judge Outcome.

        The source has already been verified and linked to the frozen H by
        preserve_research. Replays must match exactly or fail closed.
        """
        edge_id = f"tm-preservation-{hypothesis_id}-{cycle_id}"
        with self.transaction():
            if not self.db.execute(
                "SELECT id FROM hypotheses WHERE id=?", (hypothesis_id,)
            ).fetchone():
                raise ValueError("Preservation refers to an unknown scientific Hypothesis")
            owner = self.db.execute(
                "SELECT hypothesis_id FROM hypothesis_cycle_owners WHERE cycle_id=?",
                (str(cycle_id),),
            ).fetchone()
            if owner and owner["hypothesis_id"] != hypothesis_id:
                raise ValueError("Preservation Cycle/Hypothesis identity mismatch")
            external = self.db.execute(
                "SELECT content_type,source,title,content FROM external_records WHERE id=?",
                (node_id,),
            ).fetchone()
            identity = ("StageSummary", "preserve_research", title, content)
            if external:
                if tuple(external) != identity:
                    raise ValueError("Existing StageSummary has a conflicting source")
            else:
                self.db.execute(
                    "INSERT INTO external_records"
                    "(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
                    (node_id, *identity, _timestamp()),
                )
            edge = self.db.execute(
                "SELECT hypothesis_id,external_id,edge_type FROM external_edges WHERE id=?",
                (edge_id,),
            ).fetchone()
            relation = (hypothesis_id, node_id, "research_source")
            if edge:
                if tuple(edge) != relation:
                    raise ValueError("Existing preservation edge conflicts")
            else:
                self.db.execute(
                    "INSERT INTO external_edges"
                    "(id,hypothesis_id,external_id,edge_type,originating_outcome)"
                    " VALUES (?,?,?,?,NULL)",
                    (edge_id, *relation),
                )
                self._event("preservation.stage_summary_registered", node_id, {
                    "hypothesis_id": hypothesis_id, "cycle_id": cycle_id,
                    "scientific_verdict": None,
                })
        return {"node_id": node_id, "edge_id": edge_id}


    def add_evolution(self, *, edge_id: str, source: str, target: str,
                      operator: str | None = None, outcome_id: str | None = None) -> None:
        with self.transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO scientific_edges"
                "(id,source_hypothesis,target_hypothesis,evolution_operator,originating_outcome)"
                " VALUES (?,?,?,?,?)", (edge_id, source, target, operator, outcome_id),
            )
            self._event("edge.observed", edge_id, {"source": source, "target": target})

    def set_frontier(self, *, branch_id: str, hypothesis_id: str, status: str,
                     anchor_outcome: str | None = None, reason: str = "",
                     preferred: bool = False) -> None:
        """One authoritative frontier row per H; multirow legacy DB requires repair."""
        if status not in _STATUSES:
            raise ValueError(f"Unknown frontier state: {status}")
        with self.transaction():
            prior_id = self.db.execute("SELECT * FROM frontier_branches WHERE id=?", (branch_id,)).fetchone()
            if prior_id and prior_id["hypothesis_id"] != hypothesis_id:
                raise ValueError("Cannot reassign an existing branch to a different H")
            existing = list(self.db.execute(
                "SELECT * FROM frontier_branches WHERE hypothesis_id=?", (hypothesis_id,)))
            if len(existing) > 1:
                raise ValueError(f"H {hypothesis_id} has duplicate frontier rows: explicit reconciliation required")
            prior = existing[0] if existing else None
            row_id = str(prior["id"]) if prior else branch_id
            self.db.execute(
                "INSERT INTO frontier_branches(id,hypothesis_id,anchor_outcome,status,priority,reason,changed_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "anchor_outcome=excluded.anchor_outcome,status=excluded.status,priority=excluded.priority,"
                "reason=excluded.reason,changed_at=excluded.changed_at",
                (row_id, hypothesis_id, anchor_outcome, status,
                 "preferred" if preferred and status == "active" else None, reason, _timestamp()),
            )
            self._event("frontier.changed", row_id,
                        {"hypothesis_id": hypothesis_id, "from": prior["status"] if prior else None,
                         "to": status, "anchor_outcome": anchor_outcome, "reason": reason})

    def frontier_view(self) -> dict[str, Any]:
        """DB-authoritative H states; absence is unknown, not closed."""
        hypotheses = {
            str(row["id"]): {"outcome": str(row["verdict"]), "branches": []}
            for row in self.db.execute(
                "SELECT hypotheses.id, outcomes.verdict FROM hypotheses "
                "JOIN outcomes ON outcomes.hypothesis_id=hypotheses.id"
            )
        }
        for row in self.db.execute("SELECT * FROM frontier_branches ORDER BY id"):
            hypotheses[row["hypothesis_id"]]["branches"].append(dict(row))
        for h_id, value in hypotheses.items():
            branches = value["branches"]
            if len(branches) > 1:
                raise ValueError(f"H {h_id} has duplicate frontier rows: explicit reconciliation required")
            value["frontier_status"] = branches[0]["status"] if branches else None
            value["preferred"] = bool(branches and branches[0]["status"] == "active"
                                      and branches[0]["priority"] == "preferred")
        return hypotheses

    def assert_eligible_hypothesis(
        self, hypothesis_id: str, *, same_h: bool,
        reconsideration_trigger: str = "", revision_reason: str = "",
    ) -> None:
        """Policy for Lead's selected anchor; H descendants do not enter the check.

        New H derivations can cite closed/rejected historical H as scientific
        provenance without re-opening that H or treating its verdict as open.
        """
        state = self.frontier_view().get(hypothesis_id)
        if state is None:
            raise ValueError(f"Unknown research Hypothesis anchor: {hypothesis_id}")
        if not same_h:
            return
        if state["frontier_status"] is None:
            raise ValueError(f"H {hypothesis_id} has no verified frontier state; explicit curation required")
        if state["frontier_status"] == "closed":
            raise ValueError(f"closed H cannot be silently continued as same H: {hypothesis_id}")
        if state["frontier_status"] == "parked" and not reconsideration_trigger.strip():
            raise ValueError(f"parked H requires reconsideration_trigger: {hypothesis_id}")
        if state["outcome"] in {"supported", "rejected"} and not revision_reason.strip():
            raise ValueError(
                f"terminal Outcome {state['outcome']} requires scientific revision reason "
                f"before same-H retesting: {hypothesis_id}"
            )

    def set_frontiers_batch(self, updates: list[dict[str, Any]]) -> None:
        """Research Lead-only frontier transition; atomic for several H records."""
        with self.transaction():
            seen: set[str] = set()
            for update in updates:
                h = str(update.get("hypothesis_id") or "").strip()
                if not h or h in seen:
                    raise ValueError(f"frontier maintenance needs unique H: {h}")
                seen.add(h)
                reason = str(update.get("reason") or "").strip()
                if not reason:
                    raise ValueError(f"frontier maintenance requires reason: {h}")
                status = str(update.get("status") or "")
                if status not in _STATUSES:
                    raise ValueError(f"Invalid frontier status: {status}")
                if self.db.execute("SELECT 1 FROM hypotheses WHERE id=?", (h,)).fetchone() is None:
                    raise ValueError(f"Unknown frontier H: {h}")
                self.set_frontier(
                    branch_id="frontier:"+h, hypothesis_id=h, status=status,
                    reason=reason, preferred=bool(update.get("preferred")),
                    anchor_outcome=update.get("anchor_outcome"),
                )

    def close_all_frontiers_for_goal(self, reason: str = "Final Gate passed") -> list[str]:
        """Close every open research branch on verified Goal Completion."""
        with self.transaction():
            records = list(self.db.execute(
                "SELECT * FROM frontier_branches WHERE status IN ('active','parked')"
            ))
            for entry in records:
                self.set_frontier(
                    branch_id=entry["id"], hypothesis_id=entry["hypothesis_id"],
                    anchor_outcome=entry["anchor_outcome"],
                    status="closed", reason=reason, preferred=False,
                )
            return [str(entry["hypothesis_id"]) for entry in records]

    def graph_view(self) -> dict[str, Any]:
        """Only Hypothesis/External appear as GRAPH nodes; Outcomes stay embedded."""
        hypotheses = []
        for h in self.db.execute("SELECT * FROM hypotheses ORDER BY id"):
            item = dict(h)
            row = self.db.execute(
                "SELECT * FROM outcomes WHERE hypothesis_id=?",(h["id"],)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Missing unique Outcome for H: {h['id']}")
            outcome = dict(row)
            for name in ("findings","open_questions","boundary","evidence_refs"):
                outcome[name] = json.loads(outcome.pop(name+"_json"))
            item["whole_h_assessment"] = outcome
            item["legacy_scientific_status"] = item["scientific_status"]
            item["scientific_status"] = outcome["verdict"]
            cycle_records = [dict(r) for r in self.db.execute(
                "SELECT id,cycle_id,verdict,source_json FROM cycle_evaluations WHERE hypothesis_id=?",
                (h["id"],),
            )]
            item["cycle_outcomes"] = self.cycle_outcomes(h["id"])
            item["cycle_count"] = len(item["cycle_outcomes"])
            for cycle_outcome in item["cycle_outcomes"]:
                cycle_id = str(cycle_outcome["cycle_id"])
                if cycle_outcome.get("verdict") != "unadjudicated":
                    cycle_outcome["document_ref"] = (
                        f"notes/outcomes/{cycle_id}.md"
                        if re.fullmatch(r"[A-Za-z0-9_-]+", cycle_id) else None
                    )
            item["outcome"] = item["cycle_outcomes"][-1] if item["cycle_outcomes"] else None
            # A legacy open whole-H placeholder is not an actual Judge verdict.
            placeholder = (outcome["verdict"] == "open" and not outcome["cycle_id"]
                           and not outcome["judge_rationale"]
                           and not outcome["decisive_test"] and not outcome["summary"])
            # The epistemic label is SDK-owned and follows the current whole-H
            # assessment. The frozen claim/scope words themselves are untouched.
            from .hypothesis_display import summary_with_current_status
            item["summary"] = summary_with_current_status(
                item.get("summary"), outcome["verdict"], adjudicated=not placeholder,
            )
            needs_review = placeholder and (len(cycle_records) > 1 or any(
                r["verdict"] in ("supported", "rejected") for r in cycle_records))
            item["legacy_identity_review"] = ({
                "status": "unadjudicated_whole_h",
                "cycle_ids": [str(r["cycle_id"]) for r in cycle_records],
                "reason": "A historical Cycle verdict does not adjudicate the whole H.",
            } if needs_review else None)

            if cycle_records:
                def cycle_order(row: dict[str, Any]) -> tuple[int, str]:
                    match = re.search(r"\d+", str(row["cycle_id"]))
                    return (int(match.group()) if match else -1, str(row["id"]))
                latest = max(cycle_records, key=cycle_order)
                payload = json.loads(latest["source_json"])
                item["latest_cycle_evaluation"] = {
                    "id": latest["id"], "cycle_id": latest["cycle_id"],
                    "verdict": latest["verdict"], "report_ref": payload.get("report_ref"),
                }
            item["frontiers"] = [
                dict(r) for r in self.db.execute(
                    "SELECT * FROM frontier_branches WHERE hypothesis_id=? ORDER BY id", (h["id"],))
            ]
            item["type"] = "Hypothesis"
            hypotheses.append(item)
        external = [{**dict(row), "type": "External"} for row in self.db.execute(
            "SELECT * FROM external_records ORDER BY id")]
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": hypotheses + external,
            "edges": [dict(row) for row in self.db.execute("SELECT * FROM scientific_edges ORDER BY id")],
            "external_edges": [dict(row) for row in self.db.execute("SELECT * FROM external_edges ORDER BY id")],
            "external_relations": [dict(row) for row in self.db.execute("SELECT * FROM external_relations ORDER BY id")],
        }


    def agent_view(
        self,
        entry_hypothesis_id: str | None = None,
        *,
        focused_summary_chars: int = 230,
        focused_outcome_summary_chars: int = 220,
        external_summary_chars: int = 120,
        focused_recent_outcomes: int = 3,
    ) -> dict[str, Any]:
        """Return a compact, deterministic, read-only graph view for Agent context.

        This is deliberately not a Canvas serialization: no geometry, colors,
        timestamps, row metadata, raw External bodies, or duplicated graph-view
        fields enter the result. Stable H/External IDs remain as machine keys.
        """
        graph = self.graph_view()
        hypotheses = [node for node in graph["nodes"] if node.get("type") == "Hypothesis"]
        external = [node for node in graph["nodes"] if node.get("type") == "External"]
        edges = graph["edges"]
        entry = str(entry_hypothesis_id or "").strip()
        known_h = {str(node["id"]) for node in hypotheses}
        focus_ids = {entry} if entry in known_h else set()
        if focus_ids:
            # Prioritize the entry and its one-hop H neighborhood; detailed outcomes
            # expand only for the entry H, while the full index and typed lineage stay available.
            for edge in edges:
                if edge["source_hypothesis"] == entry:
                    focus_ids.add(str(edge["target_hypothesis"]))
                elif edge["target_hypothesis"] == entry:
                    focus_ids.add(str(edge["source_hypothesis"]))

        def clipped(value: Any, limit: int) -> str:
            text = " ".join(str(value or "").split())
            return text if len(text) <= limit else text[:max(1, limit - 1)].rstrip() + "…"

        def whole_h_status(node: dict[str, Any]) -> str:
            outcome = node.get("whole_h_assessment") or {}
            placeholder = (
                outcome.get("verdict") == "open"
                and not str(outcome.get("cycle_id") or "").strip()
                and not any(str(outcome.get(key) or "").strip()
                            for key in ("judge_rationale", "decisive_test", "summary"))
            )
            if placeholder:
                return "none"
            status = str(outcome.get("verdict") or "none")
            return status if status in {"none", "open", "supported", "rejected"} else "none"

        def cycle_pattern(records: list[dict[str, Any]]) -> str:
            verdicts: set[str] = set()
            for record in records:
                raw = record.get("original_verdicts") or [record.get("verdict")]
                verdicts.update(str(item) for item in raw if item)
            if not verdicts:
                return "none"
            return sorted(verdicts)[0] if len(verdicts) == 1 else "mixed"

        def h_order(node: dict[str, Any]) -> tuple[int, int, str]:
            statuses = {str(row.get("status")) for row in node.get("frontiers") or []}
            frontier_rank = 0 if "active" in statuses else 1 if "parked" in statuses else 2 if "closed" in statuses else 3
            return (0 if str(node["id"]) in focus_ids else 1, frontier_rank, str(node["id"]))

        status_order = {"active": 0, "parked": 1, "closed": 2, "unmarked": 3}

        def frontier_rows_for(node: dict[str, Any], reason_chars: int = 90) -> list[dict[str, Any]]:
            rows = []
            for frontier in node.get("frontiers") or []:
                row: dict[str, Any] = {"status": str(frontier.get("status") or "unmarked")}
                if frontier.get("priority") == "preferred":
                    row["preferred"] = True
                reason = clipped(frontier.get("reason"), reason_chars)
                if reason:
                    row["reason"] = reason
                if frontier.get("anchor_outcome"):
                    row["anchor_outcome"] = str(frontier["anchor_outcome"])
                rows.append(row)
            return rows

        def frontier_states_for(node: dict[str, Any]) -> list[str]:
            statuses = {str(row.get("status") or "unmarked") for row in node.get("frontiers") or []}
            result = sorted(statuses or {"unmarked"})
            result.sort(key=lambda value: (status_order.get(value, 4), value))
            return result

        def dossier_ref_for(node: dict[str, Any]) -> str:
            if node.get("dossier_ref"):
                return str(node["dossier_ref"])
            node_id = str(node["id"])
            candidate = self.path.parent / "notes" / "hypotheses" / f"{node_id}.md"
            try:
                candidate.resolve().relative_to(self.path.parent.resolve())
            except ValueError:
                return ""
            return f"notes/hypotheses/{node_id}.md" if candidate.is_file() and not candidate.is_symlink() else ""

        compact_hypotheses: list[list[Any]] = []
        focus_details: list[dict[str, Any]] = []
        current_frontiers: list[dict[str, Any]] = []
        hypothesis_columns = [
            "id", "title", "whole_h_status", "frontier_states", "cycle_count",
            "cycle_result_pattern",
        ]
        frontier_h_ids: set[str] = set()
        from .hypothesis_display import normalize_hypothesis_display
        for node in sorted(hypotheses, key=h_order):
            node_id = str(node["id"])
            records = list(node.get("cycle_outcomes") or [])
            statuses = frontier_states_for(node)
            # Non-focus H remain discoverable by ID and hypothesis-detail.
            dossier_ref = dossier_ref_for(node) if node_id in focus_ids else ""
            whole_status = whole_h_status(node)
            display_title, display_summary = normalize_hypothesis_display(
                node_id, title=node.get("title"), summary=node.get("summary"),
                fallback_title=node.get("title"), scientific_status=node.get("scientific_status"),
                adjudicated=whole_status != "none",
            )
            compact_hypotheses.append([
                node_id, clipped(display_title or node_id, 40),
                whole_status, statuses, int(node.get("cycle_count") or 0),
                cycle_pattern(records),
            ])

            if node_id == entry:
                compact_outcomes = []
                keep_outcomes = max(0, focused_recent_outcomes)
                for outcome in records[-keep_outcomes:] if keep_outcomes else []:
                    row: dict[str, Any] = {
                        "cycle": str(outcome.get("cycle_id") or ""),
                        "verdict": str(outcome.get("verdict") or "open"),
                    }
                    if outcome.get("original_verdicts"):
                        row["source_verdicts"] = list(outcome["original_verdicts"][:3])
                    summary = clipped(outcome.get("summary"), focused_outcome_summary_chars)
                    if summary:
                        row["result"] = summary
                    refs = [str(ref) for ref in (outcome.get("evidence_refs") or []) if ref]
                    if refs:
                        row["evidence_refs"] = refs[:3]
                    report_ref = outcome.get("report_ref") or outcome.get("document_ref")
                    if report_ref:
                        row["detail_ref"] = str(report_ref)
                    compact_outcomes.append(row)
                details: dict[str, Any] = {
                    "id": node_id,
                    "claim": clipped(display_summary, min(focused_summary_chars, 160)),
                    "recent_outcomes": compact_outcomes,
                }
                frontier_rows = frontier_rows_for(node)
                if frontier_rows:
                    details["frontiers"] = frontier_rows
                if dossier_ref:
                    details["dossier_ref"] = dossier_ref
                if node.get("legacy_identity_review"):
                    details["whole_h_review_required"] = True
                focus_details.append(details)

            # Parked H remain in the complete index; their rationale is on demand.
            if "active" in statuses:
                frontier_h_ids.add(node_id)
                latest = records[-1] if records else None
                latest_view = None
                if latest:
                    latest_view = {
                        "cycle": str(latest.get("cycle_id") or ""),
                        "verdict": str(latest.get("verdict") or "open"),
                    }
                    summary = clipped(latest.get("summary"), 48)
                    if summary:
                        latest_view["result"] = summary
                    detail = latest.get("report_ref") or latest.get("document_ref")
                    if detail:
                        latest_view["detail_ref"] = str(detail)
                candidate: dict[str, Any] = {
                    "id": node_id,
                    "states": statuses,
                    # This collection is active-only; repeating status on each
                    # nested row adds no information (other states stay in H index).
                    "frontiers": [
                        {key: value for key, value in row.items() if key != "status"}
                        for row in frontier_rows_for(node, reason_chars=40)
                        if row["status"] == "active"
                    ],
                }
                if latest_view:
                    candidate["latest_outcome"] = latest_view
                # The focused dossier is already in focus_details; other
                # dossiers remain resolvable with hypothesis-detail.
                current_frontiers.append(candidate)

        # Include External cards linked to current frontiers or the focus neighborhood.
        # Full source bodies and unrelated External cards expand on demand by stable ID.
        relevant_h_ids = frontier_h_ids | focus_ids
        relevant_external_ids = {
            str(edge["external_id"])
            for edge in graph["external_edges"]
            if str(edge.get("hypothesis_id") or "") in relevant_h_ids
        }
        focus_external_ids = {
            str(edge["external_id"])
            for edge in graph["external_edges"]
            if str(edge.get("hypothesis_id") or "") in focus_ids
        }
        compact_external: list[list[str]] = []
        external_columns = ["id", "title", "content_type", "source", "detail_ref", "summary"]
        for node in external:
            external_id = str(node["id"])
            if external_id not in relevant_external_ids:
                continue
            detail_ref = external_detail_ref(external_id) or ""
            summary = (
                compact_external_summary(node.get("content"), external_summary_chars)
                if external_id in focus_external_ids else ""
            )
            compact_external.append([
                external_id, clipped(node.get("title") or external_id, 80),
                str(node.get("content_type") or ""),
                clipped(node.get("source"), 30) if external_id in focus_external_ids else "",
                detail_ref, summary,
            ])

        # SQLite retains all typed edges. Show incoming provenance for every
        # active H, and both incoming/outgoing edges for the explicit focus.
        # Outgoing edges from other active H (often long parked histories) can
        # be explored via hypothesis-detail; no canonical lineage is removed.
        lineage = [[
            str(edge["source_hypothesis"]), str(edge["target_hypothesis"]),
            str(edge.get("evolution_operator") or "unknown"),
            str(edge.get("originating_outcome") or ""),
        ] for edge in sorted(edges, key=lambda row: (
            str(row["source_hypothesis"]), str(row["target_hypothesis"]),
            str(row.get("evolution_operator") or "unknown"),
        )) if str(edge["target_hypothesis"]) in frontier_h_ids
            or str(edge["source_hypothesis"]) in focus_ids
            or str(edge["target_hypothesis"]) in focus_ids]
        external_refs = [[
            str(edge["hypothesis_id"]), str(edge["external_id"]),
            str(edge.get("originating_outcome") or ""),
        ] for edge in sorted(graph["external_edges"], key=lambda row: (
            str(row.get("hypothesis_id") or ""), str(row["external_id"]),
        )) if str(edge.get("hypothesis_id") or "") in relevant_h_ids]
        source_relations = [[
            str(edge["source_external"]), str(edge["target_external"]),
            str(edge.get("edge_type") or "external_reference"),
        ] for edge in sorted(graph.get("external_relations", []), key=lambda row: (
            str(row["source_external"]), str(row["target_external"]),
        )) if str(edge["source_external"]) in relevant_external_ids
          or str(edge["target_external"]) in relevant_external_ids]
        return {
            "schema": "theorymaster/agent-science-graph/v1",
            "projection_version": "compact-frontier-v3",
            "entry_hypothesis_id": entry if entry in known_h else None,
            "semantics": {
                "frontier_states": "unmarked=no stored label; never infer closed from topology",
                "whole_h_status": "none=no whole-H Judge verdict; open=explicit whole-H verdict; Cycle outcomes are distinct",
                "current_frontiers": "Active-only; nested frontier status is implicit; other states in H index; full detail via hypothesis-detail",
                "lineage": "Incoming typed edges to active H and edges incident to focus; omitted edges via hypothesis-detail",
                "external": "Only relevant IDs/summaries; full source via external-detail",
            },
            "hypothesis_columns": hypothesis_columns,
            "hypotheses": compact_hypotheses,
            "current_frontiers": current_frontiers,
            "focus_details": focus_details,
            "lineage_columns": ["from", "to", "operator", "outcome_ref"],
            "lineage": lineage,
            "lineage_omitted_count": len(edges) - len(lineage),
            "external_source_columns": external_columns,
            "external_sources": compact_external,
            "external_reference_columns": ["from", "to", "outcome_ref"],
            "external_references": external_refs,
            "source_relation_columns": ["from", "to", "type"],
            "source_relations": source_relations,
        }

    def agent_candidate_review_view(
        self, candidates: list[dict[str, Any]], *, recent_outcomes: int = 2,
    ) -> dict[str, Any]:
        """Return SQLite-derived source-H context for Challenger, never a Canvas path."""
        graph = self.graph_view()
        known_h = {
            str(node.get("id")): node for node in graph["nodes"]
            if node.get("type") == "Hypothesis" and node.get("id")
        }
        candidate_sources: list[dict[str, Any]] = []
        source_ids: list[str] = []
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            raw_ids = candidate.get("lineage_anchor_node_ids")
            if not isinstance(raw_ids, list):
                raw_ids = [candidate.get("lineage_anchor_node_id")]
            ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value or "").strip()))
            ids = [value for value in ids if value in known_h][:3]
            source_ids.extend(ids)
            candidate_sources.append({
                "candidate_id": str(candidate.get("id") or ""),
                "source_hypothesis_ids": ids,
            })
        source_ids = list(dict.fromkeys(source_ids))
        details = [
            self.agent_hypothesis_detail(hypothesis_id, recent_outcomes=recent_outcomes)
            for hypothesis_id in source_ids
        ]
        relevant_ids = set(source_ids)
        lineage = [[
            str(edge["source_hypothesis"]), str(edge["target_hypothesis"]),
            str(edge.get("evolution_operator") or "unknown"),
            str(edge.get("originating_outcome") or ""),
        ] for edge in sorted(graph["edges"], key=lambda row: (
            str(row["source_hypothesis"]), str(row["target_hypothesis"]),
        )) if str(edge["source_hypothesis"]) in relevant_ids
          or str(edge["target_hypothesis"]) in relevant_ids]
        return {
            "schema": "theorymaster/agent-candidate-review-graph/v1",
            "semantics": "SQLite-derived source-H dossiers and typed adjacent lineage for the current candidate shortlist; no Canvas geometry or file path is authoritative.",
            "candidate_sources": candidate_sources,
            "source_hypotheses": details,
            "lineage_columns": ["from", "to", "operator", "outcome_ref"],
            "lineage": lineage,
        }

    def agent_goal_completion_view(
        self, *, recent_outcomes_per_h: int = 1,
    ) -> dict[str, Any]:
        """Return a compact SQLite-derived research trajectory for the task Final Gate."""
        graph = self.graph_view()

        def clipped(value: Any, limit: int) -> str:
            text = " ".join(str(value or "").split())
            return text if len(text) <= limit else text[:max(1, limit - 1)].rstrip() + "…"

        def real_whole_assessment(node: dict[str, Any]) -> dict[str, Any]:
            whole = node.get("whole_h_assessment") or {}
            placeholder = (
                whole.get("verdict") == "open"
                and not str(whole.get("cycle_id") or "").strip()
                and not any(str(whole.get(key) or "").strip()
                            for key in ("judge_rationale", "decisive_test", "summary"))
            )
            if placeholder:
                return {}
            result = {}
            if whole.get("verdict") not in (None, ""):
                result["verdict"] = str(whole["verdict"])
            if whole.get("cycle_id") not in (None, ""):
                result["cycle"] = str(whole["cycle_id"])
            summary = clipped(whole.get("summary"), 160)
            if summary:
                result["summary"] = summary
            return result

        h_rows: list[dict[str, Any]] = []
        for node in sorted((row for row in graph["nodes"] if row.get("type") == "Hypothesis"),
                           key=lambda row: str(row.get("id") or "")):
            outcomes = list(node.get("cycle_outcomes") or [])
            outcome_limit = max(0, min(5, int(recent_outcomes_per_h)))
            recent = outcomes[-outcome_limit:] if outcome_limit else []
            compact_outcomes = []
            for outcome in recent:
                row: dict[str, Any] = {
                    "cycle": str(outcome.get("cycle_id") or ""),
                    "verdict": str(outcome.get("verdict") or "open"),
                }
                summary = clipped(outcome.get("summary"), 140)
                if summary:
                    row["summary"] = summary
                ref = outcome.get("report_ref") or outcome.get("document_ref")
                if ref:
                    row["detail_ref"] = str(ref)
                compact_outcomes.append(row)
            h_rows.append({
                "id": str(node.get("id") or ""),
                "title": clipped(node.get("title") or node.get("id"), 100),
                "claim": clipped(node.get("summary"), 180),
                "whole_h_assessment": real_whole_assessment(node),
                "frontiers": [{
                    "status": str(frontier.get("status") or "unmarked"),
                    **({"reason": clipped(frontier.get("reason"), 70)} if frontier.get("reason") else {}),
                } for frontier in node.get("frontiers") or []] or [{"status": "unmarked"}],
                "cycle_count": int(node.get("cycle_count") or 0),
                "recent_cycle_outcomes": compact_outcomes,
                "dossier_ref": str(node.get("dossier_ref") or ""),
            })
        lineage = [[
            str(edge["source_hypothesis"]), str(edge["target_hypothesis"]),
            str(edge.get("evolution_operator") or "unknown"),
            str(edge.get("originating_outcome") or ""),
        ] for edge in sorted(graph["edges"], key=lambda row: (
            str(row["source_hypothesis"]), str(row["target_hypothesis"]),
        ))]
        external = [{
            "id": str(node.get("id") or ""),
            "title": clipped(node.get("title") or node.get("id"), 70),
            "content_type": str(node.get("content_type") or ""),
            "detail_ref": external_detail_ref(str(node.get("id") or "")) or "",
        } for node in graph["nodes"] if node.get("type") == "External"]
        return {
            "schema": "theorymaster/agent-goal-completion-graph/v1",
            "semantics": "SQLite-authoritative H trajectory, whole-H assessments, recent Cycle-local outcomes, explicit frontier labels, typed lineage, and External references. Read detail_ref only when needed to decide the existing Final Gate; no Canvas parsing.",
            "hypotheses": h_rows,
            "lineage_columns": ["from", "to", "operator", "outcome_ref"],
            "lineage": lineage,
            "external_sources": external,
        }

    def agent_hypothesis_detail(
        self, hypothesis_id: str, *, recent_outcomes: int = 5,
    ) -> dict[str, Any]:
        """Expand one H on demand without serializing the whole graph or Canvas.

        The compact agent_view is the index; this method is the bounded detail
        endpoint for the selected scientific claim and its immediate references.
        """
        node_id = str(hypothesis_id or "").strip()
        graph = self.graph_view()
        node = next((row for row in graph["nodes"]
                     if row.get("type") == "Hypothesis" and str(row.get("id")) == node_id), None)
        if node is None:
            raise KeyError(f"Unknown hypothesis: {node_id}")
        try:
            limit = max(0, min(20, int(recent_outcomes)))
        except (TypeError, ValueError):
            limit = 5
        whole = node.get("whole_h_assessment") or {}
        placeholder_whole = (
            whole.get("verdict") == "open"
            and not str(whole.get("cycle_id") or "").strip()
            and not any(str(whole.get(key) or "").strip()
                        for key in ("judge_rationale", "decisive_test", "summary"))
        )
        if placeholder_whole:
            whole = {}
        whole_fields = (
            "verdict", "cycle_id", "summary", "decisive_test", "judge_rationale",
            "findings", "open_questions", "boundary", "evidence_refs",
        )
        cycle_rows = list(node.get("cycle_outcomes") or [])
        if limit:
            cycle_rows = cycle_rows[-limit:]
        else:
            cycle_rows = []
        outcome_fields = (
            "cycle_id", "verdict", "review_state", "execution_state",
            "termination_reason", "prior_judge_objection", "original_verdicts",
            "summary", "decisive_test", "judge_rationale", "findings",
            "open_questions", "boundary", "evidence_refs", "report_ref",
            "document_ref",
        )
        lineage = [[
            str(edge["source_hypothesis"]), str(edge["target_hypothesis"]),
            str(edge.get("evolution_operator") or "unknown"),
            str(edge.get("originating_outcome") or ""),
        ] for edge in graph["edges"] if node_id in {
            str(edge["source_hypothesis"]), str(edge["target_hypothesis"]),
        }]
        linked_external = []
        for edge in graph["external_edges"]:
            if str(edge.get("hypothesis_id") or "") != node_id:
                continue
            external_id = str(edge["external_id"])
            source = next((item for item in graph["nodes"]
                           if item.get("type") == "External" and str(item.get("id")) == external_id), None)
            if source is None:
                continue
            linked_external.append([
                external_id, str(source.get("title") or external_id),
                str(source.get("content_type") or ""),
                external_detail_ref(external_id) or "",
                str(edge.get("originating_outcome") or ""),
            ])
        # This is the detail surface, not a graph-card preview. Preserve the
        # stored text here; compact normalization belongs only in index/card views.
        return {
            "schema": "theorymaster/agent-hypothesis-detail/v1",
            "id": node_id,
            "title": str(node.get("title") or node_id),
            "claim": str(node.get("summary") or ""),
            "whole_h_assessment": {key: whole.get(key) for key in whole_fields if key in whole},
            "cycle_outcomes": [
                {key: row.get(key) for key in outcome_fields if key in row}
                for row in cycle_rows
            ],
            "frontiers": [{
                "status": str(row.get("status") or "unmarked"),
                "reason": str(row.get("reason") or ""),
                "priority": str(row.get("priority") or ""),
                "anchor_outcome": str(row.get("anchor_outcome") or ""),
            } for row in node.get("frontiers") or []],
            "lineage_columns": ["from", "to", "operator", "outcome_ref"],
            "lineage": sorted(lineage),
            "external_reference_columns": ["id", "title", "content_type", "detail_ref", "outcome_ref"],
            "external_references": sorted(linked_external),
            "dossier_ref": str(node.get("dossier_ref") or ""),
            "whole_h_review_required": bool(node.get("legacy_identity_review")),
        }

    def agent_cycle_detail(self, cycle_id: str) -> dict[str, Any]:
        """Locate one Cycle's authoritative verdict or preserved result and source pointers.

        Never infer a formal verdict from a whole-H placeholder. Returned paths
        are *relative locators*, not permission to read private Workspace files.
        """
        ident = str(cycle_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", ident):
            raise ValueError("invalid Cycle ID")
        tables = {row["name"] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('cycle_evaluations','cycle_preservations','cycle_memory_index')"
        )}
        judge = (self.db.execute(
            "SELECT hypothesis_id,verdict,source_json FROM cycle_evaluations WHERE cycle_id=?",
            (ident,),
        ).fetchone() if "cycle_evaluations" in tables else None)
        preserved = (self.db.execute(
            "SELECT hypothesis_id,source_json FROM cycle_preservations WHERE cycle_id=?",
            (ident,),
        ).fetchone() if "cycle_preservations" in tables else None)
        memory = (self.db.execute(
            "SELECT hypothesis_id,status,cycle_dir,memory_components_json "
            "FROM cycle_memory_index WHERE cycle_id=?", (ident,),
        ).fetchone() if "cycle_memory_index" in tables else None)
        if judge is not None and preserved is not None:
            raise ValueError("Cycle cannot be both adjudicated and preserved")
        if judge is None and preserved is None and memory is None:
            raise KeyError(f"Unknown Cycle: {ident}")

        source = judge or preserved
        raw = str(source["source_json"]) if source is not None else ""
        record = json.loads(raw) if raw else {}
        if not isinstance(record, dict):
            raise ValueError("Cycle source must be a JSON object")
        record_kind = "judge" if judge is not None else "preserved" if preserved is not None else "unadjudicated"
        verdict = str(judge["verdict"]) if judge is not None else "unadjudicated"
        hypothesis_id = str(source["hypothesis_id"] if source is not None else memory["hypothesis_id"] or "")
        if source is not None and memory is not None and memory["hypothesis_id"]:
            if hypothesis_id != str(memory["hypothesis_id"]):
                raise ValueError("Cycle evidence and memory index disagree on hypothesis identity")

        def preview(value: Any, limit: int) -> str:
            body = " ".join(str(value or "").split())
            return body if len(body) <= limit else body[:limit - 1].rstrip() + "…"

        def safe_ref(value: Any) -> str:
            ref = str(value or "").strip()
            if not ref or len(ref) > 240 or "\\" in ref or "\x00" in ref:
                return ""
            path = Path(ref)
            if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
                return ""
            return ref if path.parts and path.parts[0] in {"notes", "evidence", "workspace"} else ""

        raw_refs = record.get("evidence_refs") or []
        refs = [safe_ref(item) for item in raw_refs if isinstance(item, str)] if isinstance(raw_refs, list) else []
        refs = list(dict.fromkeys(item for item in refs if item))
        report_ref = safe_ref(record.get("report_ref") or record.get("document_ref"))
        research_refs: dict[str, str] = {}
        memory_status = None
        if memory is not None:
            memory_status = str(memory["status"] or "")
            # Check only the relative suffix; do not surface the stored private
            # cycle_dir and do not claim the files exist in an open-source clone.
            index_dir = Path(str(memory["cycle_dir"] or ""))
            if len(index_dir.parts) >= 2 and index_dir.parts[-2:] == ("research", ident):
                try:
                    components = json.loads(str(memory["memory_components_json"] or "[]"))
                except (ValueError, TypeError):
                    components = []
                allowed = {"workspace", "notes", "evidence", "run_report.md",
                           "invocations.jsonl", "scientific_model_out.json"}
                if isinstance(components, list):
                    for item in components:
                        if isinstance(item, str) and item in allowed:
                            research_refs[item] = f"{ident}/{item}"

        return {
            "schema": "theorymaster/agent-cycle-detail/v1",
            "cycle_id": ident,
            "hypothesis_id": hypothesis_id,
            "record_kind": record_kind,
            "adjudication_status": "adjudicated" if judge is not None else "unadjudicated",
            "judge_verdict": verdict if judge is not None else None,
            "preserved": preserved is not None,
            "summary": preview(record.get("summary"), 520),
            "decisive_test": preview(record.get("decisive_test"), 300),
            "judge_rationale": preview(record.get("judge_rationale"), 280) if judge is not None else "",
            "evidence_refs": refs[:8],
            "evidence_refs_omitted": (len(raw_refs) - len(refs[:8])) if isinstance(raw_refs, list) else 0,
            "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None,
            "report_ref": report_ref or None,
            "memory_status": memory_status,
            "research_root_relative_refs": research_refs,
            "research_root_source": "existing research_root Context; never a graph authority",
            "file_availability": "not_checked; missing private Workspace is allowed",
            "whole_h_status_inferred": False,
        }

    def agent_external_detail(
        self, external_id: str, *, max_content_chars: int = 12_000,
    ) -> dict[str, Any]:
        """Expand one External record with bounded original text and provenance.

        The record metadata and relationships are authoritative SQLite values;
        content is capped for Agent context and advertises truncation explicitly.
        """
        node_id = str(external_id or "").strip()
        row = self.db.execute(
            "SELECT id,content_type,source,title,content FROM external_records WHERE id=?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown External record: {node_id}")
        try:
            content_limit = max(0, min(24_000, int(max_content_chars)))
        except (TypeError, ValueError):
            content_limit = 12_000
        full_content = str(row["content"] or "")
        content = full_content[:content_limit]
        linked_hypotheses = [[
            str(edge["hypothesis_id"]), str(edge["originating_outcome"] or ""),
        ] for edge in self.db.execute(
            "SELECT hypothesis_id,originating_outcome FROM external_edges "
            "WHERE external_id=? ORDER BY hypothesis_id,originating_outcome", (node_id,),
        )]
        relations = [[
            str(edge["source_external"]), str(edge["target_external"]),
            str(edge["edge_type"] or "external_reference"),
        ] for edge in self.db.execute(
            "SELECT source_external,target_external,edge_type FROM external_relations "
            "WHERE source_external=? OR target_external=? "
            "ORDER BY source_external,target_external", (node_id, node_id),
        )]
        return {
            "schema": "theorymaster/agent-external-detail/v1",
            "id": str(row["id"]),
            "title": str(row["title"] or row["id"]),
            "content_type": str(row["content_type"] or ""),
            "source": str(row["source"] or ""),
            "content": content,
            "content_truncated": len(full_content) > len(content),
            "content_chars_total": len(full_content),
            "detail_ref": external_detail_ref(node_id) or "",
            "hypothesis_reference_columns": ["hypothesis_id", "outcome_ref"],
            "hypothesis_references": linked_hypotheses,
            "source_relation_columns": ["from", "to", "type"],
            "source_relations": relations,
        }

    def agent_search(self, query: str, *, limit: int = 8, offset: int = 0) -> dict[str, Any]:
        """Find all historical H/Cycle evidence and External across lineage branches.

        Matching is global. Declared lineage is not a filter: a relevant External
        may hang off an H that is not the candidate's direct parent.
        """
        text = " ".join(str(query or "").split())
        if not text or len(text) > 200:
            return {
                "schema": "theorymaster/agent-science-graph-search/v1",
                "status": "invalid_arguments",
                "error": "search query must be 1..200 characters",
            }
        terms = search_terms(text)
        # Common scientific identifiers like P(d) or F^2 contain only
        # one-letter tokens; retain their exact notation for global lookup.
        if not terms and re.fullmatch(r"[A-Za-z]\([A-Za-z0-9]\)|[A-Za-z]\^[0-9]+", text):
            terms = [text.casefold()]
        if not terms and not _SEARCH_ID.fullmatch(text):
            return {
                "schema": "theorymaster/agent-science-graph-search/v1",
                "status": "invalid_arguments",
                "error": "search query has no usable term",
            }
        try:
            cap = max(1, min(8, int(limit)))
            start = int(offset)
            if start < 0 or start > 100_000:
                raise ValueError("offset out of range")
        except (TypeError, ValueError):
            return {"schema": "theorymaster/agent-science-graph-search/v1",
                    "status": "invalid_arguments", "error": "limit/offset must be bounded integers"}
        graph = self.graph_view()
        hypotheses = [node for node in graph["nodes"] if node.get("type") == "Hypothesis"]
        external = [node for node in graph["nodes"] if node.get("type") == "External"]
        by_id = {str(node.get("id")): node for node in hypotheses + external}

        def clip(value: Any, size: int) -> str:
            body = " ".join(str(value or "").split())
            return body if len(body) <= size else body[:max(1, size - 1)].rstrip() + "…"

        def links_for(node_id: str) -> list[str]:
            kind = by_id.get(node_id, {}).get("type")
            found: list[str] = []
            if kind == "Hypothesis":
                for edge in graph["edges"]:
                    ends = (str(edge["source_hypothesis"]), str(edge["target_hypothesis"]))
                    if node_id in ends:
                        other = ends[0] if ends[1] == node_id else ends[1]
                        if other not in found:
                            found.append(other)
                for edge in graph["external_edges"]:
                    if str(edge.get("hypothesis_id") or "") == node_id:
                        other = str(edge["external_id"])
                        if other not in found:
                            found.append(other)
            elif kind == "External":
                for edge in graph["external_edges"]:
                    if str(edge["external_id"]) == node_id and edge.get("hypothesis_id"):
                        other = str(edge["hypothesis_id"])
                        if other not in found:
                            found.append(other)
                for edge in graph.get("external_relations", []):
                    ends = (str(edge["source_external"]), str(edge["target_external"]))
                    if node_id in ends:
                        other = ends[0] if ends[1] == node_id else ends[1]
                        if other not in found:
                            found.append(other)
            return found[:8]

        def row_for(node_id: str) -> dict[str, Any]:
            node = by_id[node_id]
            is_external = node.get("type") == "External"
            summary = (compact_external_summary(node.get("content"), 180)
                       if is_external else clip(node.get("summary"), 180))
            result = {
                "id": node_id,
                "type": str(node.get("type") or ""),
                "title": clip(node.get("title") or node_id, 80),
                "summary": summary,
                "linked_ids": links_for(node_id),
            }
            if not is_external:
                matched = []
                for outcome in node.get("cycle_outcomes") or []:
                    if not isinstance(outcome, dict):
                        continue
                    searchable = " ".join(str(outcome.get(key) or "") for key in
                        ("summary", "decisive_test", "judge_rationale", "findings", "boundary"))
                    if text.casefold() in searchable.casefold() or any(
                        term in searchable.casefold() for term in terms
                    ):
                        matched.append({
                            "cycle_id": str(outcome.get("cycle_id") or ""),
                            "record_kind": ("preserved" if outcome.get("verdict") == "unadjudicated"
                                            else "judge"),
                            "verdict": str(outcome.get("verdict") or ""),
                        })
                if matched:
                    result["matched_cycles"] = matched if len(matched) <= 2 else [matched[0], matched[-1]]
                    result["matched_cycles_truncated"] = len(matched) > 2
                    result["matched_cycle_count"] = len(matched)
            return result

        exact = next((node_id for node_id in by_id if node_id.casefold() == text.casefold()), None)
        if exact:
            ordered = [exact]
            for linked in links_for(exact):
                if linked in by_id and linked not in ordered:
                    ordered.append(linked)
            return {
                "schema": "theorymaster/agent-science-graph-search/v1",
                "status": "ok",
                "query": text,
                "match": "id",
                "results": [row_for(node_id) for node_id in ordered[start:start + cap]],
                "truncated": len(ordered) > start + cap,
                "total_matches": len(ordered), "offset": start,
                "next_offset": start + cap if len(ordered) > start + cap else None,
            }

        def haystack(node: dict[str, Any]) -> tuple[str, str, str]:
            node_id = str(node.get("id") or "")
            title = str(node.get("title") or "")
            parts = [str(node.get("summary") or "")]
            if node.get("type") == "External":
                parts.append(str(node.get("content") or "")[:16000])
            else:
                for outcome in node.get("cycle_outcomes") or []:
                    if isinstance(outcome, dict):
                        for field in ("summary", "decisive_test", "judge_rationale", "findings", "boundary"):
                            parts.append(str(outcome.get(field) or "")[:1000])
            return node_id.casefold(), title.casefold(), " ".join(parts).casefold()

        ranked: list[tuple[int, str, str]] = []
        folded_query = text.casefold()
        for node in hypotheses + external:
            node_id, title, body = haystack(node)
            score = 50 if folded_query and folded_query in title else 0
            for term in terms:
                if term in node_id:
                    score += 20
                elif term in title:
                    score += 10
                elif term in body:
                    score += 2
            if score:
                ranked.append((score, str(node.get("type") or ""), str(node.get("id"))))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        chosen = [item[2] for item in ranked[start:start + cap]]
        return {
            "schema": "theorymaster/agent-science-graph-search/v1",
            "status": "ok",
            "query": text,
            "match": "text",
            "results": [row_for(node_id) for node_id in chosen],
            "truncated": len(ranked) > start + cap,
            "total_matches": len(ranked), "offset": start,
            "next_offset": start + cap if len(ranked) > start + cap else None,
        }


def import_legacy_canvas(canvas_file: str | Path, database: str | Path, *,
                         create_backup: bool = True) -> dict[str, Any]:
    """Operator-invoked import. Fail closed for ambiguous lineage; retain raw nodes."""
    src = Path(canvas_file)
    raw_bytes = src.read_bytes()
    data = json.loads(raw_bytes)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("Canvas must contain nodes[] and edges[]")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    backup = None
    if create_backup:
        backup = src.with_name(src.name + ".before-sqlite-" + digest[:12] + ".bak")
        if not backup.exists():
            shutil.copy2(src, backup)
    diagnostics: list[str] = []
    with ScienceGraphStore(database) as store:
        import_key = "canvas_import:" + digest
        if store.db.execute("SELECT 1 FROM graph_meta WHERE key=?", (import_key,)).fetchone():
            return {"status": "already_imported", "backup": str(backup) if backup else None,
                    "diagnostics": []}

        with store.transaction():
            # Preserve every source node, edge and unknown legacy field verbatim.
            store.db.execute(
                "INSERT OR IGNORE INTO legacy_canvas_archive"
                "(digest,original_name,original_bytes,node_count,edge_count,imported_at)"
                " VALUES (?,?,?,?,?,?)",
                (digest, src.name, raw_bytes, len(data["nodes"]),
                 len(data.get("edges", [])), _timestamp()),
            )
            nodes = {str(n.get("id")): n for n in data["nodes"] if isinstance(n, dict) and n.get("id")}
            headers = {node_id: _read_header(str(node.get("text", ""))) for node_id, node in nodes.items()}
            types = {node_id: headers[node_id].get("type", "Unknown") for node_id in nodes}
            edges = [e for e in data.get("edges", []) if isinstance(e, dict)]
            incoming: dict[str, list[str]] = {}
            outgoing: dict[str, list[str]] = {}
            for e in edges:
                a, b = str(e.get("fromNode", "")), str(e.get("toNode", ""))
                incoming.setdefault(b, []).append(a)
                outgoing.setdefault(a, []).append(b)

            # Historical Hypothesis nodes used non-canonical IDs before H-000n.
            # Preserve their exact identity instead of dropping or fabricating one.
            h_alias: dict[str, str] = {}
            for node_id, node in nodes.items():
                h = headers[node_id]
                if types[node_id] != "Hypothesis":
                    continue
                h_id = h.get("id") or node_id
                if not re.fullmatch(r"H-\d{4,}", h_id):
                    h_id = node_id
                    diagnostics.append(f"Legacy Hypothesis identity {node_id} retained unchanged")
                title, summary = _title_and_summary(str(node.get("text", "")))
                status = h.get("scientific_status", "none")
                if status not in _H_STATES:
                    status = "none"
                note_link = _LINK.search(str(node.get("text", "")))
                dossier_ref = note_link.group(1) if note_link else None
                store.db.execute(
                    "INSERT OR IGNORE INTO hypotheses"
                    "(id,title,summary,scientific_status,dossier_ref,created_at) VALUES (?,?,?,?,?,?)",
                    (h_id, title, summary, status, dossier_ref, _timestamp()),
                )
                store.db.execute(
                    "INSERT OR IGNORE INTO outcomes"
                    "(id,hypothesis_id,cycle_id,verdict,created_at,updated_at)"
                    " VALUES (?,?,'','open',?,?)",
                    (h_id+"-outcome",h_id,_timestamp(),_timestamp()),
                )
                if status in {"supported","rejected"}:
                    store.db.execute(
                        "UPDATE outcomes SET verdict=? WHERE hypothesis_id=? AND cycle_id=''",
                        (status,h_id),
                    )
                h_alias[node_id] = h_id
            valid_h = {row["id"] for row in store.db.execute("SELECT id FROM hypotheses")}
            decision_owners: dict[str, str] = {}
            decision_groups: dict[str, list[dict[str, Any]]] = {}
            for node_id, node in nodes.items():
                if types[node_id] != "Decision":
                    continue
                parents = {h_alias[src_id] for src_id in incoming.get(node_id, []) if src_id in h_alias}
                if len(parents) != 1:
                    diagnostics.append(f"Outcome {node_id} has {len(parents)} unambiguous H parents; preserved as External")
                    continue
                parent_id = next(iter(parents))
                h = headers[node_id]
                text = str(node.get("text", ""))
                _, summary = _title_and_summary(text)
                sections = _cycle_sections(text)
                raw_verdict = h.get("verdict") or h.get("status") or ""
                if raw_verdict not in {"open", "supported", "rejected", "milestone_advanced"}:
                    diagnostics.append(
                        f"Decision {node_id} has no recognized Judge verdict; preserved as External"
                    )
                    continue
                # A milestone_advanced is a *local* Cycle disposition, not a
                # terminal fate of H. Preserve it in the raw archive and treat
                # the one final Outcome as open until a formal verdict exists.
                verdict = raw_verdict if raw_verdict in {"open", "supported", "rejected"} else "open"
                if raw_verdict != verdict:
                    diagnostics.append(
                        f"Cycle {node_id} legacy verdict {raw_verdict} retained in raw archive; compatibility verdict is open"
                    )
                refs = _bold_field(text, "Evidence refs")
                match = _LINK.search(text)
                cycle_id = h.get("cycle_id") or re.sub(r"-decision.*$", "", node_id)
                decision_groups.setdefault(str(cycle_id), []).append({
                    "id": node_id, "hypothesis_id": parent_id, "cycle_id": str(cycle_id),
                    "raw_verdict": raw_verdict, "verdict": verdict,
                    "summary": summary,
                    "decisive_test": _bold_field(text, "Decisive test"),
                    "judge_rationale": _bold_field(text, "Judge rationale"),
                    "findings": sections["findings"],
                    "open_questions": sections["open_questions"],
                    "boundary": sections["boundary"],
                    "evidence_refs": [s.strip() for s in refs.split(",") if s.strip()],
                    "report_ref": match.group(1) if match else None,
                })

            for cycle_id, records in decision_groups.items():
                parents = {record["hypothesis_id"] for record in records}
                verdicts = {record["verdict"] for record in records}
                if len(parents) != 1 or len(verdicts) != 1:
                    diagnostics.append(
                        f"Cycle {cycle_id} has conflicting historical Decision owners/verdicts; "
                        "all originals preserved as External pending scientific review"
                    )
                    continue
                parent_id = next(iter(parents))
                def combined(key: str) -> str:
                    values = [(row["id"], str(row[key]).strip()) for row in records if row[key]]
                    if len(records) == 1:
                        return values[0][1] if values else ""
                    return "\n\n".join(f"{source_id}: {value}" for source_id, value in values)
                def merged(key: str) -> list[str]:
                    return list(dict.fromkeys(
                        str(item) for row in records for item in row[key]
                    ))
                outcome_id = (records[0]["id"] if len(records) == 1
                              else f"cycle-{cycle_id}-outcome")
                try:
                    store.append_outcome(
                        outcome_id=outcome_id, hypothesis_id=parent_id, cycle_id=cycle_id,
                        verdict=records[0]["verdict"], summary=combined("summary"),
                        decisive_test=combined("decisive_test"),
                        judge_rationale=combined("judge_rationale"),
                        findings=merged("findings"), open_questions=merged("open_questions"),
                        boundary=merged("boundary"), evidence_refs=merged("evidence_refs"),
                        report_ref=records[0]["report_ref"] if len(records) == 1 else None,
                        source_decisions=(records if len(records) > 1 or
                                          records[0]["raw_verdict"] != records[0]["verdict"] else None),
                        authorized_revision_reason="historical import: retain all past Cycle evaluations",
                        record_only=True,
                    )
                    decision_owners.update({row["id"]: parent_id for row in records})
                except (sqlite3.IntegrityError, ValueError) as exc:
                    diagnostics.append(f"Cycle {cycle_id} Outcome not imported: {exc}")

            for node_id, node in nodes.items():
                kind = types[node_id]
                h = headers[node_id]
                text = str(node.get("text", ""))
                store.db.execute("INSERT OR IGNORE INTO legacy_records VALUES (?,?,?,?)",
                                 (node_id, kind, _json(node), "retained for source/audit"))
                if (kind == "Hypothesis" and node_id in h_alias) or (
                    kind == "Decision" and node_id in decision_owners
                ):
                    historical_status = h.get("frontier_status")
                    if historical_status in _STATUSES:
                        owner = h_alias[node_id] if kind == "Hypothesis" else decision_owners[node_id]
                        store.db.execute(
                            "INSERT OR IGNORE INTO legacy_frontier_archive"
                            "(id,hypothesis_id,anchor_outcome,status,priority,reason,changed_at,archived_at,provenance)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            ("legacy-"+("h:" if kind == "Hypothesis" else "outcome:")+node_id,
                             owner, node_id if kind == "Decision" else None, historical_status,
                             "preferred" if h.get("priority") == "preferred" else None,
                             "", _timestamp(), _timestamp(), "legacy-canvas-import"),
                        )
                if kind == "Hypothesis" and node_id in h_alias:
                    status = h.get("frontier_status")
                    if status in _STATUSES:
                        store.set_frontier(branch_id="legacy-h:" + node_id, hypothesis_id=h_alias[node_id],
                                           status=status, preferred=h.get("priority") == "preferred")
                elif kind == "Decision" and node_id in decision_owners:
                    status = h.get("frontier_status")
                    if status in _STATUSES:
                        store.set_frontier(branch_id="legacy-outcome:" + node_id,
                                           hypothesis_id=decision_owners[node_id],
                                           anchor_outcome=node_id, status=status,
                                           preferred=h.get("priority") == "preferred")
                elif kind in {"External", "Finding", "StageSummary"} or (kind == "Decision" and node_id not in decision_owners):
                    content_type = h.get("content_type") or ("Legacy" + kind if kind != "External" else "Note")
                    if kind == "Decision":
                        content_type = "OrphanOutcome"
                    title, _ = _title_and_summary(text)
                    # Historical Findings cannot silently become adjudicated science.
                    store.db.execute(
                        "INSERT OR IGNORE INTO external_records"
                        "(id,content_type,source,title,content,created_at) VALUES (?,?,?,?,?,?)",
                        (node_id, content_type, h.get("source", "legacy-canvas"),
                         title, text, _timestamp()),
                    )
                    if kind == "Finding":
                        diagnostics.append(f"Finding {node_id} preserved as External; adjudication unverified")

            # Conflicting legacy Decision statuses are historical evidence,
            # never an arbitrary present-day H verdict. Keep them in archive.
            for owner in set(h_alias.values()):
                observations = list(store.db.execute(
                    "SELECT status FROM legacy_frontier_archive WHERE hypothesis_id=?",
                    (owner,),
                ))
                if len({r["status"] for r in observations}) > 1:
                    store.db.execute("DELETE FROM frontier_branches WHERE hypothesis_id=?", (owner,))
                    diagnostics.append(
                        f"Conflicting historical frontiers for {owner}; explicit curation required"
                    )

            for index, edge in enumerate(edges):
                source, target = str(edge.get("fromNode", "")), str(edge.get("toNode", ""))
                kind = str(edge.get("edge_type") or "")
                edge_id = str(edge.get("id") or f"legacy-edge-{index}")
                store.db.execute("INSERT OR IGNORE INTO legacy_edges(id,raw_json) VALUES (?,?)",
                                 (edge_id, _json(edge)))
                if target in h_alias and (source in h_alias or source in decision_owners):
                    if source in h_alias:
                        parent_h, source_outcome = h_alias[source], None
                    else:
                        parent_h, source_outcome = decision_owners[source], source
                    target_h = h_alias[target]
                    if parent_h != target_h:
                        store.add_evolution(edge_id=edge_id, source=parent_h, target=target_h,
                                            operator=edge.get("evolution_operator"), outcome_id=source_outcome)
                external_ids = {
                    n for n, t in types.items()
                    if t in {"External", "Finding", "StageSummary"}
                    or (t == "Decision" and n not in decision_owners)
                }
                if source in external_ids and target in external_ids:
                    store.db.execute(
                        "INSERT OR IGNORE INTO external_relations"
                        "(id,source_external,target_external,edge_type) VALUES (?,?,?,?)",
                        (edge_id, source, target, kind or "external_reference"),
                    )
                for s, t in ((source, target), (target, source)):
                    if t in h_alias and s in external_ids:
                        store.db.execute(
                            "INSERT OR IGNORE INTO external_edges"
                            "(id,hypothesis_id,external_id,edge_type) VALUES (?,?,?,?)",
                            (edge_id, h_alias[t], s, kind or "external_reference"),
                        )
                    elif s in decision_owners and t in external_ids:
                        store.db.execute(
                            "INSERT OR IGNORE INTO external_edges"
                            "(id,hypothesis_id,external_id,edge_type,originating_outcome)"
                            " VALUES (?,?,?,?,?)",
                            (edge_id, decision_owners[s], t, kind or "external_reference", s),
                        )

            for node_id, node in nodes.items():
                if node_id not in h_alias and not (
                    types[node_id] in {"External", "Finding", "StageSummary"}
                    or (types[node_id] == "Decision" and node_id not in decision_owners)
                ):
                    continue
                if all(key in node for key in ("x", "y")):
                    store.db.execute(
                        "INSERT OR IGNORE INTO node_layout(id,x,y,width,height) VALUES (?,?,?,?,?)",
                        (h_alias.get(node_id, node_id), node.get("x"), node.get("y"), node.get("width"), node.get("height")),
                    )
            for ordinal, raw_node in enumerate(data["nodes"]):
                raw_id = str(raw_node.get("id") or "") if isinstance(raw_node, dict) else ""
                raw_type = types.get(raw_id, "Unknown")
                if raw_type == "Hypothesis" and raw_id in h_alias:
                    role, target_id = "Hypothesis", h_alias[raw_id]
                elif raw_type == "Decision" and raw_id in decision_owners:
                    role, target_id = "CycleEvaluation_for_H", decision_owners[raw_id]
                elif raw_type in {"External", "Finding", "StageSummary", "Decision"}:
                    role, target_id = "External_archive", raw_id
                else:
                    role, target_id = "raw_history_only", raw_id or None
                    diagnostics.append(f"Legacy node position {ordinal} needs manual mapping")
                store.db.execute(
                    "INSERT OR IGNORE INTO legacy_conversion_map"
                    "(source_digest,legacy_position,legacy_id,old_type,target_kind,target_id,note)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (digest, ordinal, raw_id, raw_type, role, target_id,
                     "Original raw Canvas record preserved; no scientific authority inferred"),
                )
            store.db.execute("INSERT INTO graph_meta(key,value) VALUES (?,?)",
                             (import_key, _json({"source": str(src), "date": _timestamp(),
                                                 "diagnostics": diagnostics})))
            store._event("canvas.imported", str(src), {"digest": digest, "warnings": len(diagnostics)})
            return {"status": "imported", "backup": str(backup) if backup else None,
                    "source_digest": digest, "legacy_nodes": len(data["nodes"]),
                    "legacy_edges": len(data.get("edges", [])),
                    "diagnostics": diagnostics, "hypotheses": len(valid_h),
                    "whole_h_assessments": len(valid_h),
                    "outcomes": store.db.execute(
                        "SELECT COUNT(*) FROM cycle_evaluations"
                    ).fetchone()[0],
                    "cycle_evaluations": store.db.execute(
                        "SELECT COUNT(*) FROM cycle_evaluations"
                    ).fetchone()[0],
                    "legacy_decisions_linked": len(decision_owners)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit legacy Canvas → SQLite science graph import")
    parser.add_argument("canvas", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_legacy_canvas(args.canvas, args.database,
                                          create_backup=not args.no_backup),
                     indent=2, ensure_ascii=False))
