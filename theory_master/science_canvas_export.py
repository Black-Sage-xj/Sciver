"""Project SQLite science graph back to the ORIGINAL exploration.canvas location.

This Canvas is a generated *view* of canonical SQLite records. Preserve the
complete historical Canvas in both a hashed backup and the SQLite legacy tables.
Never call this migration automatically from the running task scheduler.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .science_graph_store import (
    ScienceGraphStore, compact_external_summary, external_detail_ref, import_legacy_canvas,
)


def _short_status(frontiers: list[dict[str, Any]]) -> tuple[str, bool, str]:
    statuses = {row["status"] for row in frontiers}
    priority = any(row.get("priority") == "preferred" and row["status"] == "active"
                   for row in frontiers)
    status = next((value for value in ("active", "parked", "closed") if value in statuses), "")
    matching = [row for row in frontiers if row.get("status") == status]
    chosen = next((row for row in matching if row.get("priority") == "preferred"),
                  matching[0] if matching else {})
    return status, priority, str(chosen.get("reason") or "").strip()


def _archive_original_node(store: ScienceGraphStore, node_id: str) -> dict[str, Any]:
    rec = store.db.execute("SELECT raw_json FROM legacy_records WHERE id=?", (node_id,)).fetchone()
    return json.loads(rec["raw_json"]) if rec else {}


def _layout(store: ScienceGraphStore, node_id: str, legacy: dict[str, Any]) -> dict[str, Any]:
    saved = store.db.execute(
        "SELECT x,y,width,height FROM node_layout WHERE id=?", (node_id,)
    ).fetchone()
    return {
        "x": int(saved["x"]) if saved and saved["x"] is not None else int(legacy.get("x") or 0),
        "y": int(saved["y"]) if saved and saved["y"] is not None else int(legacy.get("y") or 0),
        "width": max(400, int(saved["width"] or 0) if saved else int(legacy.get("width") or 0)),
        "height": max(280, int(saved["height"] or 0) if saved else int(legacy.get("height") or 0)),
    }


def _h_text(node: dict[str, Any]) -> str:
    """Render a concise scientific guide; complete claims and evidence stay in linked files."""
    from .hypothesis_display import normalize_hypothesis_display

    status, preferred, frontier_reason = _short_status(node.get("frontiers", []))
    whole = node.get("whole_h_assessment") or {}
    adjudicated = bool(
        str(whole.get("cycle_id") or "").strip()
        or str(whole.get("judge_rationale") or "").strip()
        or str(whole.get("decisive_test") or "").strip()
        or str(whole.get("summary") or "").strip()
        or whole.get("verdict") in {"supported", "rejected"}
    )
    title, summary = normalize_hypothesis_display(
        str(node["id"]), title=node.get("title"), summary=node.get("summary"),
        fallback_title=node.get("title"),
        scientific_status=node.get("scientific_status"), adjudicated=adjudicated,
    )

    def preview(value: object, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        # Never cut a scientific clause mid-word and never imply that a preview is complete.
        boundary = max(text.rfind("。", 0, limit), text.rfind("；", 0, limit), text.rfind("，", 0, limit))
        if boundary >= int(limit * 0.55):
            return text[:boundary + 1].rstrip() + "…"
        return text[:limit].rstrip() + "…"

    header = [f"id: {node['id']}", "type: Hypothesis",
              f"scientific_status: {node['scientific_status']}"]
    if status:
        header.append(f"frontier_status: {status}")
    if preferred:
        header.append("priority: preferred")
    lines = [*header, "", f"# {title}", "", summary]
    dossier_ref = node.get("dossier_ref") or f"notes/hypotheses/{node['id']}"
    lines += ["", "[[" + str(dossier_ref) + "]]"]

    outcome = node.get("outcome")
    if outcome:
        verdict_label = ", ".join(outcome.get("original_verdicts") or []) or str(outcome["verdict"])
        if outcome.get("original_verdicts"):
            verdict_label += "（兼容记录为 " + str(outcome["verdict"]) + "）"
        lines += ["", "---", "", "## Cycle " + str(outcome["cycle_id"]) +
                  " · Outcome · " + verdict_label]
        if outcome.get("summary"):
            lines += ["", preview(outcome["summary"], 180)]
        questions = outcome.get("open_questions") or []
        if questions:
            lines += ["", "**未解决：** " + preview(questions[0], 100)]
        if outcome.get("document_ref"):
            lines += ["", "[[" + str(outcome["document_ref"]) + "]]"]
        if outcome.get("report_ref"):
            lines += ["", "[[" + str(outcome["report_ref"]) + "]]"]
    if status == "parked" and frontier_reason:
        lines += ["", "**暂停原因：** " + preview(frontier_reason, 90)]
    if node.get("cycle_count"):
        lines += ["", "Cycle Outcome 共 " + str(node["cycle_count"]) + " 条"]
        for prior in node.get("cycle_outcomes", [])[-4:-1]:
            lines += ["- Cycle " + str(prior["cycle_id"]) + " · " +
                      (", ".join(prior.get("original_verdicts") or []) or str(prior["verdict"]))]
    return "\n".join(x for x in lines if x is not None).rstrip()


def _external_text(node: dict[str, Any]) -> str:
    """Keep graph cards short; full source text lives in a linked detail document."""
    detail_ref = external_detail_ref(node.get("id"))
    lines = [
        f"id: {node['id']}", "type: External",
        f"content_type: {node['content_type']}",
        f"source: {str(node['source'] or '')[:100]}", "",
        f"# {node['title'] or node['id']}", "",
        compact_external_summary(node.get("content"), 220) or "No concise preview available.",
    ]
    if detail_ref:
        lines += ["", f"[[{detail_ref}|详细内容]]"]
    return "\n".join(lines).rstrip()


def write_external_detail_documents(
    store: ScienceGraphStore, *, notes_root: Path | None = None,
) -> int:
    """Atomically materialize full External bodies as generated, linked Markdown views.

    SQLite and the legacy archive remain authoritative. Existing hand-written
    files are never overwritten, and paths cannot escape the task's notebook root.
    """
    task_root = store.path.parent.resolve()
    root = Path(notes_root) if notes_root is not None else task_root / "notes"
    if root.is_symlink():
        raise ValueError("External detail notes root cannot be a symlink")
    resolved_root = root.resolve()
    if not resolved_root.is_relative_to(task_root):
        raise ValueError("External detail notes root escapes the task root")
    resolved_root.mkdir(parents=True, exist_ok=True)
    root = resolved_root
    detail_dir = root / "external"
    if detail_dir.is_symlink():
        raise ValueError("External detail directory cannot be a symlink")
    detail_dir.mkdir(parents=True, exist_ok=True)
    if not detail_dir.resolve().is_relative_to(task_root):
        raise ValueError("External detail directory escapes the task root")

    marker = "<!-- generated-by: theory-master-external-detail/v1 -->\n"
    written = 0
    for row in store.db.execute(
        "SELECT id,content FROM external_records ORDER BY id"
    ):
        ref = external_detail_ref(row["id"])
        if not ref:
            continue
        target = task_root / ref
        if target.is_symlink() or not target.resolve().is_relative_to(task_root):
            raise ValueError(f"Unsafe External detail target: {ref}")
        payload = marker + str(row["content"] or "").rstrip() + "\n"
        if target.exists():
            prior = target.read_text(encoding="utf-8")
            if not prior.startswith(marker):
                raise ValueError(f"Refusing to overwrite hand-written External detail: {ref}")
            if prior == payload:
                continue
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=target.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        os.replace(temporary, target)
        written += 1
    return written


def _resolve_display_overlaps(nodes: list[dict[str, Any]],
                              original_layout_ids: set[str]) -> int:
    """Minimal, deterministic layout repair in the read-only projection only.

    Keep original legacy coordinates in the database archive; don't change
    source graph identity or scientific lineage when Outcome enlarges H cards.
    """
    placed: list[tuple[float, float, float, float]] = []
    adjusted = 0
    # Trusted original geometry takes precedence over missing/default 0,0.
    order = sorted(nodes, key=lambda n: (0 if n["id"] in original_layout_ids else 1,
                                        float(n.get("x", 0)), float(n.get("y", 0)),
                                        str(n["id"])))
    for node in order:
        x, y = float(node.get("x", 0)), float(node.get("y", 0))
        width, height = float(node.get("width", 0)), float(node.get("height", 0))
        original_y = y
        for _ in range(len(nodes) + 1):
            colliders = [(left, top, w, h) for left, top, w, h in placed
                         if min(x + width, left + w) > max(x, left) + 8
                         and min(y + height, top + h) > max(y, top) + 8]
            if not colliders:
                break
            y = max(top + h + 60 for _, top, _, h in colliders)
        else:
            raise ValueError("Cannot resolve Canvas projection overlaps")
        if y != original_y:
            node["y"] = y
            adjusted += 1
        placed.append((x, y, width, height))
    return adjusted


def canvas_projection(store: ScienceGraphStore) -> dict[str, Any]:
    """Create a valid Obsidian JSON Canvas containing ONLY H and External."""
    view = store.graph_view()
    nodes = []
    original_layout_ids: set[str] = set()
    for n in view["nodes"]:
        legacy = _archive_original_node(store, n["id"])
        if "x" in legacy and "y" in legacy:
            original_layout_ids.add(n["id"])
        loc = _layout(store, n["id"], legacy)
        is_h = n["type"] == "Hypothesis"
        loc["height"] = max(loc["height"], 500 if is_h and n.get("outcome") else 280)
        nodes.append({
            "id": n["id"], "type": "text", **loc,
            "color": str(legacy.get("color") or ("5" if is_h else "6")),
            "text": _h_text(n) if is_h else _external_text(n),
            **({"outcome": n["outcome"], "cycle_outcomes": n["cycle_outcomes"],
                "whole_h_assessment": n["whole_h_assessment"], "cycle_count": n["cycle_count"],
                "frontiers": n["frontiers"]} if is_h else {}),
        })
    edges = []
    edge_ids: set[str] = set()
    def link(base: str, source: str, target: str, kind: str, **extra: Any) -> None:
        if not source or not target or source == target:
            return
        edge_id = base
        count = 1
        while edge_id in edge_ids:
            edge_id = f"{base}-projection-{count}"
            count += 1
        edge_ids.add(edge_id)
        edges.append({
            "id": edge_id, "fromNode": source, "fromSide": "right",
            "toNode": target, "toSide": "left", "edge_type": kind, **extra,
        })
    for e in view["edges"]:
        link(e["id"], e["source_hypothesis"], e["target_hypothesis"],
             "hypothesis_evolution",
             evolution_operator=e["evolution_operator"],
             originating_outcome=e["originating_outcome"])
    for e in view["external_edges"]:
        if e.get("hypothesis_id"):
            link(e["id"], e["hypothesis_id"], e["external_id"],
                 "external_reference",
                 originating_outcome=e.get("originating_outcome"))
    for e in view.get("external_relations", []):
        link(e["id"], e["source_external"], e["target_external"],
             "external_reference")
    allowed = {node["id"] for node in nodes}
    for edge in edges:
        if edge["fromNode"] not in allowed or edge["toNode"] not in allowed:
            raise ValueError("Projection contains a dangling scientific reference")
    adjusted = _resolve_display_overlaps(nodes, original_layout_ids)
    return {
        "nodes": nodes, "edges": edges,
        "tm_science_graph_projection": {
            "version": 2,
            "schema": "theorymaster/science-graph-canvas/v2",
            "database": "science_graph.sqlite3",
            "canonical": "sqlite", "read_only": True,
            "layout_policy": "legacy_preserving_minimal_nonoverlap",
            "layout_adjustments": adjusted,
            "node_types": ["Hypothesis", "External"],
            "outcome_relation": (
                "one_whole_h_assessment_per_hypothesis; "
                "zero_or_more_cycle_outcomes_per_hypothesis"
            ),
        },
    }


def migrate_original_canvas(canvas: Path, database: Path, *, preview: bool = False) -> dict[str, Any]:
    """Explicit operator migration: import, preserve, then atomically project.

    Recheck source hash immediately before replace; never overwrite a source
    changed by a live Worker in the meantime.
    """
    canvas = Path(canvas)
    database = Path(database)
    if not canvas.is_file():
        raise FileNotFoundError(canvas)
    old = canvas.read_bytes()
    digest = hashlib.sha256(old).hexdigest()
    decoded = json.loads(old)
    prior_projection = decoded.get("tm_science_graph_projection")
    if prior_projection and database.is_file():
        with ScienceGraphStore(database, readonly=True) as store:
            converted = canvas_projection(store)
        if preview:
            return {
                "mode": "preview_existing", "hypotheses": sum(
                    n["text"].find("type: Hypothesis") >= 0 for n in converted["nodes"]),
                "visible_nodes": len(converted["nodes"]), "visible_edges": len(converted["edges"]),
            }
    elif prior_projection:
        raise ValueError("Canvas is already a generated projection, but its canonical SQLite database is absent")
    elif preview:
        with tempfile.TemporaryDirectory() as tempdir:
            temporary_database = Path(tempdir) / "science_graph.sqlite3"
            report = import_legacy_canvas(canvas, temporary_database, create_backup=False)
            with ScienceGraphStore(temporary_database, readonly=True) as store:
                projected = canvas_projection(store)
            return {
                "mode": "preview_new", **report,
                "original_nodes": len(decoded["nodes"]),
                "visible_nodes": len(projected["nodes"]), "visible_edges": len(projected["edges"]),
                "unresolved_legacy_nodes": len(report.get("diagnostics") or []),
                "source_sha256": digest,
            }
    else:
        if database.is_file():
            raise ValueError("Refusing to overwrite a pre-existing canonical database on first migration")
        report = import_legacy_canvas(canvas, database, create_backup=True)
        with ScienceGraphStore(database, readonly=True) as store:
            converted = canvas_projection(store)

    # Only first conversion needs its source digest checked in the history archive.
    # Generated Canvas files are derived views, not fresh legacy inputs.
    source = None
    if not prior_projection:
        with ScienceGraphStore(database, readonly=True) as store:
            source = store.db.execute(
                "SELECT original_bytes,node_count,edge_count FROM legacy_canvas_archive WHERE digest=?",
                (digest,),
            ).fetchone()
            if source is None or source["original_bytes"] != old:
                raise ValueError("Original Canvas bytes are not preserved in SQLite")
            mapped = store.db.execute(
                "SELECT COUNT(*) FROM legacy_conversion_map WHERE source_digest=?", (digest,)
            ).fetchone()[0]
            if mapped != source["node_count"]:
                raise ValueError("Historical node mapping is incomplete")
    with ScienceGraphStore(database, readonly=True) as store:
        write_external_detail_documents(store, notes_root=canvas.parent / "notes")
    payload = (json.dumps(converted, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if prior_projection and payload == old:
        with ScienceGraphStore(database, readonly=True) as store:
            count = store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0]
            whole_h_count = store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            archived = store.db.execute(
                "SELECT SUM(node_count),SUM(edge_count) FROM legacy_canvas_archive"
            ).fetchone()
        return {
            "mode": "projected", "database": str(database), "canvas": str(canvas),
            "backup": None, "previous_sha256": digest,
            "visible_nodes": len(converted["nodes"]), "visible_edges": len(converted["edges"]),
            "historical_nodes_archived": archived[0] or 0,
            "historical_edges_archived": archived[1] or 0, "outcomes": count,
            "whole_h_assessments": whole_h_count,
        }
    if hashlib.sha256(canvas.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Canvas changed during migration; original preserved; projection not installed")
    backup = canvas.with_name(canvas.name + ".before-outcome-" + digest[:12] + ".bak")
    if not backup.exists():
        backup.write_bytes(old)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=canvas.parent, prefix=".tm-graph-", suffix=".canvas",
                                         delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        # Optimistic SHA guard plus atomic swap, done only with stopped writers.
        if hashlib.sha256(canvas.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Canvas changed immediately before replace; projection cancelled")
        os.replace(tmp_path, canvas)
        directory_fd = os.open(str(canvas.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
    with ScienceGraphStore(database, readonly=True) as store:
        whole_h_count = store.db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        cycle_count = store.db.execute("SELECT COUNT(*) FROM cycle_evaluations").fetchone()[0]
        if source is not None:
            legacy_count, legacy_edge_count = source["node_count"], source["edge_count"]
        else:
            archived = store.db.execute(
                "SELECT COALESCE(SUM(node_count),0),COALESCE(SUM(edge_count),0) "
                "FROM legacy_canvas_archive"
            ).fetchone()
            legacy_count, legacy_edge_count = archived[0], archived[1]
    return {
        "mode": "projected", "database": str(database), "canvas": str(canvas),
        "backup": str(backup), "previous_sha256": digest,
        "visible_nodes": len(converted["nodes"]), "visible_edges": len(converted["edges"]),
        "historical_nodes_archived": legacy_count,
        "historical_edges_archived": legacy_edge_count,
        "outcomes": cycle_count,
        "whole_h_assessments": whole_h_count,
        "cycle_evaluations_archived": cycle_count,
    }


def restore_original_canvas(database: Path, digest: str, destination: Path) -> None:
    """Restore the complete historical Canvas bytes to a new file."""
    with ScienceGraphStore(database, readonly=True) as store:
        row = store.db.execute(
            "SELECT original_bytes FROM legacy_canvas_archive WHERE digest=?", (digest,)
        ).fetchone()
        if row is None:
            raise ValueError("Historical Canvas is not archived")
        data = row["original_bytes"]
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("History export destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup original Canvas, migrate to SQLite, rewrite Canvas as H/E projection")
    parser.add_argument("canvas", type=Path)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--preview", action="store_true", help="Audit and preview without writing the original or database")
    parser.add_argument("--commit-cutover", action="store_true",
                        help="Explicitly acknowledge that all old Canvas writers are stopped "
                             "and SDK writes are already migrated to SQLite")
    args = parser.parse_args()
    if not args.preview and not args.commit_cutover:
        parser.error("Refusing live Canvas replacement without --commit-cutover; "
                     "first complete the SDK SQLite runtime integration and stop Canvas writers")
    database = args.database or args.canvas.with_name("science_graph.sqlite3")
    print(json.dumps(migrate_original_canvas(args.canvas, database, preview=args.preview),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
