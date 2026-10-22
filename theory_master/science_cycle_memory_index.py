"""Read-only scan of historical Cycle artifacts into V2's traceability index.

This never runs any Agent and does not alter Cycle directories. Incomplete
historical parallelism stays UNKNOWN rather than inferred from today's slots.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .science_graph_store import ScienceGraphStore

ARTIFACTS = (
    "research_state.json", "run_state.json", "invocations.jsonl", "events.jsonl",
    "runtime_events.jsonl", "important_events.jsonl", "evidence", "notes",
    "checkpoints", "invocation_artifacts", "workspace", "run_report.md",
    "scientific_model_out.json", "cycle_synthesis_out.json", "worker_concurrency_events.jsonl",
)


def index_task_cycle_memories(database: str | Path, research_root: str | Path) -> dict[str, Any]:
    root = Path(research_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("research_root must be a directory")
    found = 0
    multi = 0
    unknown = 0
    linked = 0
    # Derived traceability index, not science: it must keep indexing the
    # final Cycles after the Goal seal.
    with ScienceGraphStore(database, allow_after_goal_seal=True) as store:
        known_h = {str(row[0]) for row in store.db.execute("SELECT id FROM hypotheses")}
        with store.transaction():
            for cycle in sorted(root.iterdir(),key=lambda p:int(p.name) if p.name.isdigit() else -1):
                if not cycle.is_dir() or not re.fullmatch(r"[1-9][0-9]*",cycle.name):
                    continue
                if not (cycle/".theory-master-cycle").is_file() or not (cycle/"research_state.json").is_file():
                    continue
                try:
                    state = json.loads((cycle/"research_state.json").read_text(encoding="utf-8"))
                    if not isinstance(state,dict):
                        continue
                except (OSError,ValueError):
                    continue
                context = state.get("cycle_execution_context") or {}
                if not isinstance(context,dict):
                    context={}
                worker = str(state.get("worker_id") or context.get("worker_id") or "")
                mode = str(context.get("mode") or ("multi_worker" if worker else "unknown_legacy"))
                if mode not in {"single_worker", "multi_worker", "unknown_legacy"}:
                    mode = "unknown_legacy"
                count = context.get("concurrent_workers_at_start")
                count = count if type(count) is int and count >= 1 else None
                raw_h = str((state.get("hypothesis_lock") or {}).get("id") or
                            state.get("claimed_hypothesis_id") or "")
                h_id = raw_h if raw_h in known_h else None
                manifest = [name for name in ARTIFACTS if (cycle/name).exists()]
                metadata = {"source":"research_state.json", "raw_hypothesis_id":raw_h or None,
                            "claimed_frontier_id":state.get("claimed_frontier_id"),
                            "execution_context":context}
                store.db.execute(
                    "INSERT INTO cycle_memory_index(cycle_id,hypothesis_id,worker_id,execution_mode,"
                    "concurrent_workers_at_start,status,cycle_dir,memory_components_json,provenance_json,indexed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cycle_id) DO UPDATE SET "
                    "hypothesis_id=excluded.hypothesis_id,worker_id=excluded.worker_id,"
                    "execution_mode=excluded.execution_mode,"
                    "concurrent_workers_at_start=COALESCE(cycle_memory_index.concurrent_workers_at_start,excluded.concurrent_workers_at_start),"
                    "status=excluded.status,cycle_dir=excluded.cycle_dir,"
                    "memory_components_json=excluded.memory_components_json,"
                    "provenance_json=excluded.provenance_json,indexed_at=excluded.indexed_at",
                    (cycle.name,h_id,worker or None,mode,count,str(state.get("status") or "unknown"),
                     str(cycle.resolve()),json.dumps(manifest,ensure_ascii=False),
                     json.dumps(metadata,ensure_ascii=False),datetime.now(timezone.utc).isoformat()),
                )
                found+=1
                multi+=mode=="multi_worker"
                unknown+=mode=="unknown_legacy"
                linked+=h_id is not None
    return {"indexed_cycles":found,"known_h_linked_cycles":linked,
            "multi_worker_cycles":multi,"legacy_parallelism_unknown":unknown,
            "note":"Historical concurrency is not inferred when no start-time snapshot exists"}


def hypothesis_cycle_memories(database: str | Path, hypothesis_id: str) -> dict[str, Any]:
    """H → all Cycle folders and immutable adjudications; no guessed evidence."""
    with ScienceGraphStore(database, readonly=True) as store:
        if store.db.execute("SELECT 1 FROM hypotheses WHERE id=?",(hypothesis_id,)).fetchone() is None:
            raise ValueError("Unknown Hypothesis in science graph")
        rows=[dict(r) for r in store.db.execute(
            "SELECT cycle_id,worker_id,execution_mode,concurrent_workers_at_start,"
            "status,cycle_dir,memory_components_json,provenance_json "
            "FROM cycle_memory_index WHERE hypothesis_id=?",
            (hypothesis_id,),
        )]
        for row in rows:
            row["memory_components"] = json.loads(row.pop("memory_components_json"))
            row["provenance"] = json.loads(row.pop("provenance_json"))
        rows.sort(key=lambda row:int(row["cycle_id"]) if row["cycle_id"].isdigit() else -1)
        evaluations=store.cycle_history(hypothesis_id)
        outcome=store.db.execute("SELECT id,verdict,summary,report_ref FROM outcomes WHERE hypothesis_id=?",
                                 (hypothesis_id,)).fetchone()
    return {"hypothesis_id": hypothesis_id, "outcome": dict(outcome) if outcome else None,
            "cycle_memories": rows, "cycle_evaluations": evaluations}


def main() -> None:
    parser=argparse.ArgumentParser(description="Index existing Cycle memories in V2 graph without running science")
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--research-root",type=Path)
    parser.add_argument("--hypothesis",type=str,help="Read all indexed memories for exactly one H")
    args=parser.parse_args()
    if args.hypothesis:
        result=hypothesis_cycle_memories(args.database,args.hypothesis)
    elif args.research_root:
        result=index_task_cycle_memories(args.database,args.research_root)
    else:
        parser.error("--research-root (index) or --hypothesis (read) is required")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
