"""Explicit, backed-up legacy frontier reconciliation. Never modify Judge evidence.

Usage: python scripts/reconcile_science_frontiers.py --vault PATH --research PATH [--apply]
Without --apply this is a read-only report. Historical source records survive in
legacy_frontier_archive and the original SQLite backup.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


def _cycle_number(value: str) -> int:
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else -1


def plan(database: Path, research: Path) -> dict:
    con = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if con.execute("SELECT value FROM graph_meta WHERE key='schema_version'").fetchone()[0] != "2":
            raise ValueError("Refusing to reconcile an unexpected graph schema")
        histories = defaultdict(list)
        for r in con.execute("SELECT * FROM frontier_branches ORDER BY id"):
            histories[r["hypothesis_id"]].append(dict(r))
        updates = defaultdict(list)
        for folder in sorted((x for x in research.iterdir() if x.is_dir() and x.name.isdecimal()),
                             key=lambda p: int(p.name)):
            source = folder / "frontier_maintenance_out.json"
            if not source.is_file():
                continue
            try:
                items = json.loads(source.read_text(encoding="utf-8")).get("updates", [])
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("frontier_status") in {"active", "parked", "closed"}:
                    updates[str(item.get("node_id") or "")].append({
                        "cycle": int(folder.name), "status": item["frontier_status"],
                        "reason": str(item.get("reason") or ""), "source": str(source)
                    })
        choices = {}
        unresolved = {}
        for h, records in histories.items():
            # Only an explicit H maintenance decision is authoritative; an old
            # Decision's frontier field is a historical observation, not a new H.
            direct = updates.get(h, [])
            latest_h = max(direct, key=lambda u: u["cycle"]) if direct else None
            mapped = [(r, updates.get(r["id"].split(":", 1)[-1], [])) for r in records]
            latest_dec = max((u for _, history in mapped for u in history),
                             key=lambda u: u["cycle"], default=None)
            observed = {r["status"] for r in records}
            if latest_h and (not latest_dec or latest_h["cycle"] >= latest_dec["cycle"]):
                choice = latest_h
                provenance = "lead-H-maintenance"
            elif len(observed) == 1:
                choice = dict(status=next(iter(observed)), reason=records[0]["reason"],
                              cycle=latest_dec["cycle"] if latest_dec else None)
                provenance = "legacy-consensus"
            else:
                unresolved[h] = {
                    "reason": "conflicting historical Decision frontier observations; H-level status unverified",
                    "records": [(r["id"], r["status"]) for r in records],
                }
                continue
            choices[h] = {
                "status": choice["status"], "reason": choice.get("reason") or
                          ("Historical frontier; confirm scientific eligibility before use"),
                "priority": "preferred" if choice["status"] == "active" and any(
                    r["priority"] == "preferred" for r in records) else None,
                "provenance": provenance, "cycle": choice.get("cycle"),
                "prior": [(r["id"], r["status"]) for r in records],
            }

        # Historical Judge verdicts are preserved verbatim, including narrow
        # results attached to broader legacy H. Never fabricate whole-H Judge verdicts.
        ambiguous = []
        for row in con.execute("""
            SELECT h.id, o.verdict as whole_h, e.cycle_id, e.verdict AS cycle_verdict
            FROM hypotheses h JOIN outcomes o ON o.hypothesis_id=h.id
            JOIN cycle_evaluations e ON e.hypothesis_id=h.id
            WHERE o.cycle_id='' AND o.verdict='open'
            ORDER BY h.id, CAST(e.cycle_id AS INTEGER)
        """):
            if row["cycle_verdict"] in ("supported", "rejected"):
                ambiguous.append(dict(row))
        total_h = con.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
        return {
            "database": str(database), "historical_records": sum(map(len,histories.values())),
            "historical_h": len(histories), "total_h": total_h,
            "choices": choices, "unresolved": unresolved,
            "no_explicit_frontier": total_h-len(choices),
            "historical_outcome_identity_review": ambiguous,
            "verdicts_preserved": True,
            "active_h": sorted(h for h,v in choices.items() if v["status"]=="active"),
            "statuses": dict(Counter(v["status"] for v in choices.values())),
        }
    finally:
        con.close()


def apply(database: Path, research: Path, report: dict, vault: Path) -> dict:
    runtime = research / ".theory-master/runtime.json"
    if runtime.is_file():
        live = json.loads(runtime.read_text(encoding="utf-8"))
        if live.get("state") not in ("STOPPED", "OVER", "NEEDS_ATTENTION", "COMPLETE"):
            raise RuntimeError("Refusing migration: TheoryMaster Master task is not stopped")
    workers = research / ".theory-master/workers"
    for path in workers.glob("*/runtime.json"):
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("status") in ("RUNNING","STARTING","RESUMING"):
            raise RuntimeError("Refusing migration: active worker " + str(path))
    # Idempotency check before allocating a second backup directory.
    guard = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        if guard.execute("SELECT value FROM graph_meta WHERE key='frontier_reconciliation:v1'").fetchone():
            raise RuntimeError("Already reconciled; refusing duplicate migration")
    finally:
        guard.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = vault / ".backups" / ("frontier-reconciliation-" + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    backup = folder / "science_graph.sqlite3"
    src = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(backup)
    try:
        src.backup(target)
    finally:
        target.close(); src.close()
    db = sqlite3.connect(str(database), isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT value FROM graph_meta WHERE key='frontier_reconciliation:v1'").fetchone():
                raise RuntimeError("Already reconciled; refusing duplicate migration")
            existing = [dict(r) for r in db.execute("SELECT * FROM frontier_branches")]
            assert len(existing) == report["historical_records"], "Concurrent frontier changed"
            db.execute("""CREATE TABLE IF NOT EXISTS legacy_frontier_archive(
                id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL,
                anchor_outcome TEXT, status TEXT NOT NULL, priority TEXT,
                reason TEXT NOT NULL DEFAULT '', changed_at TEXT NOT NULL,
                archived_at TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT ''
            )""")
            when = datetime.now(timezone.utc).isoformat()
            for row in existing:
                db.execute("""INSERT OR IGNORE INTO legacy_frontier_archive
                    (id,hypothesis_id,anchor_outcome,status,priority,reason,changed_at,archived_at,provenance)
                    VALUES (?,?,?,?,?,?,?,?,?)""",(
                    row["id"],row["hypothesis_id"],row["anchor_outcome"],row["status"],
                    row["priority"],row["reason"],row["changed_at"],when,"pre-reconciliation-sqlite"))
            db.execute("DELETE FROM frontier_branches")
            for h, info in sorted(report["choices"].items()):
                db.execute("""INSERT INTO frontier_branches
                    (id,hypothesis_id,anchor_outcome,status,priority,reason,changed_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    ("frontier:"+h,h,None,info["status"],info["priority"],info["reason"],when))
                db.execute("""INSERT INTO graph_events(timestamp,action,subject,detail_json)
                    VALUES (?,?,?,?)""",
                    (when,"frontier.reconciled",h,json.dumps(info,ensure_ascii=False)))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS frontier_one_row_per_h ON frontier_branches(hypothesis_id)")
            db.execute("INSERT INTO graph_meta(key,value) VALUES (?,?)",
                       ("frontier_reconciliation:v1",json.dumps({
                           "date":when,"archive":str(backup),"count":len(existing),
                           "canonical":len(report["choices"])},ensure_ascii=False)))
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Foreign key check failed")
            assert db.execute("SELECT COUNT(*) FROM frontier_branches").fetchone()[0] == len(report["choices"])
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()
    out = {**report,"backup":str(backup),"applied_at":stamp}
    (folder/"reconciliation-report.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    return out


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument("--vault",type=Path,required=True)
    cli.add_argument("--research",type=Path,required=True)
    cli.add_argument("--apply",action="store_true")
    args=cli.parse_args()
    db=args.vault/"science_graph.sqlite3"
    report=plan(db,args.research)
    if args.apply:
        report=apply(db,args.research,report,args.vault)
    print(json.dumps({k:v for k,v in report.items()
        if k not in ("choices","historical_outcome_identity_review")},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
