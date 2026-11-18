"""One-way JSON Canvas views over existing, user-editable vault documents.

Canvas refresh never publishes Markdown and never reads Canvas changes back into
SQLite. All writers share the per-database projection lock and read a fresh
SQLite snapshot after acquiring it, so a delayed writer cannot install old data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _control_root(database: Path) -> Path:
    return database.resolve().parent / ".theory-master"


@contextmanager
def _projection_lock(database: Path) -> Iterator[None]:
    root = _control_root(database)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "canvas.lock").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _atomic_json(path: Path, payload: dict[str, Any]) -> bool:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.is_symlink():
        raise ValueError(f"Canvas output cannot be a symlink: {path}")
    if path.is_file() and path.read_bytes() == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tm-canvas-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _binding(database: Path, *, vault_root: Path | None = None,
             canvas_path: Path | None = None, branch_root: str | None = None) -> dict[str, Any]:
    root = (Path(vault_root) if vault_root is not None else database.parent).expanduser().resolve()
    branch = str(branch_root or "").strip() or None
    name = "exploration.canvas" if not branch else f"branch-{branch}.canvas"
    target = Path(canvas_path).expanduser() if canvas_path is not None else root / name
    if not target.is_absolute():
        target = root / target
    # Validate the lexical destination before resolving so output symlinks are rejected.
    if target.is_symlink():
        raise ValueError("Canvas output cannot be a symlink")
    target = target.resolve()
    if target.parent != root or target.suffix.lower() != ".canvas":
        raise ValueError("Canvas must be a .canvas file in the vault root")
    return {"vault_root": str(root), "canvas_path": str(target), "branch_root": branch}


def canvas_bindings(database: str | Path) -> list[dict[str, Any]]:
    """Return persistent automatic views; an unconfigured task has one full view."""
    database = Path(database).expanduser().resolve()
    path = _control_root(database) / "canvas-bindings.json"
    if not path.exists():
        pointer = _control_root(database) / "task.json"
        if pointer.is_file():
            config_path = Path(json.loads(pointer.read_text(encoding="utf-8"))["config_path"])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            canvas = config.get("branch_canvas")
            if canvas:
                root = Path(config.get("vault_root") or Path(canvas).parent)
                return [_binding(database, vault_root=root, canvas_path=Path(canvas))]
        return [_binding(database)]
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [_binding(database, vault_root=Path(row["vault_root"]),
                     canvas_path=Path(row["canvas_path"]), branch_root=row.get("branch_root"))
            for row in raw["bindings"]]


def replace_canvas_bindings(database: str | Path, bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomically replace view destinations, for a complete vault relocation."""
    database = Path(database).expanduser().resolve()
    normalized = [_binding(database, vault_root=Path(row["vault_root"]),
                           canvas_path=Path(row["canvas_path"]) if row.get("canvas_path") else None,
                           branch_root=row.get("branch_root")) for row in bindings]
    if len({row["canvas_path"] for row in normalized}) != len(normalized):
        raise ValueError("Canvas bindings must have unique destinations")
    with _projection_lock(database):
        _atomic_json(_control_root(database) / "canvas-bindings.json", {
            "schema": "theory-master/canvas-bindings/v1", "bindings": normalized,
        })
    return normalized


def register_canvas_binding(database: str | Path, *, vault_root: str | Path | None = None,
                            canvas_path: str | Path | None = None, branch_root: str | None = None,
                            replace: bool = False) -> dict[str, Any]:
    """Register one automatic full/branch view without touching scientific records."""
    database = Path(database).expanduser().resolve()
    if vault_root is None and canvas_path is None:
        full = next((row for row in canvas_bindings(database) if row["branch_root"] is None), None)
        if full:
            vault_root = full["vault_root"]
    binding = _binding(database, vault_root=Path(vault_root) if vault_root is not None else None,
                       canvas_path=Path(canvas_path) if canvas_path is not None else None,
                       branch_root=branch_root)
    with _projection_lock(database):
        bindings = [] if replace else canvas_bindings(database)
        bindings = [row for row in bindings if row["canvas_path"] != binding["canvas_path"]
                    and not (binding["branch_root"] is None and row["branch_root"] is None)]
        bindings.append(binding)
        _atomic_json(_control_root(database) / "canvas-bindings.json", {
            "schema": "theory-master/canvas-bindings/v1", "bindings": bindings,
        })
    return binding


def _state_path(database: Path, binding: dict[str, Any]) -> Path:
    key = hashlib.sha256(binding["canvas_path"].encode()).hexdigest()[:20]
    return _control_root(database) / f"canvas-status-{key}.json"


def canvas_status(database: str | Path) -> list[dict[str, Any]]:
    database = Path(database).expanduser().resolve()
    results = []
    for binding in canvas_bindings(database):
        path = _state_path(database, binding)
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        results.append({**binding, "status": "pending", "updated_at": None, **state})
    return results


def _safe_document(root: Path, ref: Any) -> str | None:
    """Use only existing Markdown inside this vault; never create or read its body."""
    text = str(ref or "").split("#", 1)[0].strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")
    if candidate.suffix.lower() != ".md":
        return None
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return relative.as_posix()


def _selected_graph(view: dict[str, Any], branch_root: str | None) -> dict[str, Any]:
    if not branch_root:
        return view
    hypotheses = {row["id"] for row in view["nodes"] if row["type"] == "Hypothesis"}
    if branch_root not in hypotheses:
        raise ValueError(f"Unknown branch Hypothesis: {branch_root}")
    selected = {branch_root}
    while True:
        children = {edge["target_hypothesis"] for edge in view["edges"]
                    if edge["source_hypothesis"] in selected
                    and json.loads(edge.get("provenance_json") or "{}").get("lineage_role") != "other"}
        if children <= selected:
            break
        selected |= children
    external = {edge["external_id"] for edge in view["external_edges"]
                if edge.get("hypothesis_id") in selected}
    while True:
        related = {edge["target_external"] for edge in view["external_relations"]
                   if edge["source_external"] in external}
        if related <= external:
            break
        external |= related
    return {
        **view,
        "nodes": [row for row in view["nodes"] if row["id"] in selected | external],
        "edges": [row for row in view["edges"]
                  if row["source_hypothesis"] in selected and row["target_hypothesis"] in selected],
        "external_edges": [row for row in view["external_edges"]
                           if row.get("hypothesis_id") in selected and row["external_id"] in external],
        "external_relations": [row for row in view["external_relations"]
                               if row["source_external"] in external and row["target_external"] in external],
    }


def vault_canvas_projection(store: Any, *, vault_root: Path | None = None,
                            branch_root: str | None = None) -> dict[str, Any]:
    """Build standard JSON Canvas file cards with stable IDs and labeled edges."""
    from .science_graph_store import external_detail_ref
    from .science_canvas_export import _external_text, _h_text
    if vault_root is None:
        full = next((row for row in canvas_bindings(store.path) if row["branch_root"] is None), None)
        vault_root = Path(full["vault_root"]) if full else store.path.parent
    root = vault_root.resolve()
    full_view = store.graph_view()
    view = _selected_graph(full_view, branch_root)
    layout = {row["id"]: dict(row) for row in store.db.execute("SELECT * FROM node_layout")}
    all_h = [row["id"] for row in full_view["nodes"] if row["type"] == "Hypothesis"]
    all_e = [row["id"] for row in full_view["nodes"] if row["type"] == "External"]
    positions = {node_id: (0, index * 1000) for index, node_id in enumerate(all_h)}
    positions.update({node_id: (1600, index * 500) for index, node_id in enumerate(all_e)})
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    missing: set[str] = set()
    doc_ids: dict[str, str] = {}

    def link(edge_id: str, source: str, target: str, label: str) -> None:
        if edge_id in edge_ids:
            return
        edge_ids.add(edge_id)
        edges.append({"id": edge_id, "fromNode": source, "fromSide": "right",
                      "toNode": target, "toSide": "left", "toEnd": "arrow", "label": label})

    for row in view["nodes"]:
        node_id = row["id"]
        is_h = row["type"] == "Hypothesis"
        x, y = positions[node_id]
        saved = layout.get(node_id) or {}
        geometry = {"x": saved.get("x") if saved.get("x") is not None else x,
                    "y": saved.get("y") if saved.get("y") is not None else y,
                    "width": max(400, saved.get("width") or 0),
                    "height": max(300, saved.get("height") or 0)}
        ref = (row.get("dossier_ref") or f"notes/hypotheses/{node_id}.md") if is_h else external_detail_ref(node_id)
        document = _safe_document(root, ref)
        base = {"id": node_id, **geometry, "color": "5" if is_h else "6"}
        if document:
            nodes.append({**base, "type": "file", "file": document})
            doc_ids.setdefault(document, node_id)
        else:
            if ref:
                missing.add(str(ref))
            # A graph record can be committed before its first document is published.
            # A text placeholder is replaced on the next refresh once the file exists.
            nodes.append({**base, "type": "text", "text":
                          (_h_text(row) if is_h else _external_text(row))
                          + "\n\nMarkdown: " + str(ref or "未关联文档")})
        if not is_h:
            continue
        for index, outcome in enumerate(row.get("cycle_outcomes") or []):
            refs = list(dict.fromkeys(str(outcome.get(key) or "")
                                     for key in ("document_ref", "report_ref") if outcome.get(key)))
            for ref in refs:
                document = _safe_document(root, ref)
                if not document:
                    missing.add(ref)
                    continue
                if document not in doc_ids:
                    doc_id = "document-" + hashlib.sha256(document.encode()).hexdigest()[:20]
                    doc_ids[document] = doc_id
                    nodes.append({"id": doc_id, "type": "file", "file": document,
                                  "x": geometry["x"] + 650, "y": geometry["y"] + index * 330,
                                  "width": 500, "height": 300})
                link("document-edge-" + hashlib.sha256((node_id + "\0" + document).encode()).hexdigest()[:20],
                     node_id, doc_ids[document], f"Cycle {outcome['cycle_id']} · 研究报告")
    for edge in view["edges"]:
        role = json.loads(edge.get("provenance_json") or "{}").get("lineage_role")
        if role == "other":
            continue
        label = str(edge.get("evolution_operator") or "hypothesis evolution")
        if role:
            label += " · " + {"main": "主要来源", "other": "其它来源"}.get(role, role)
        link("lineage-" + edge["id"], edge["source_hypothesis"], edge["target_hypothesis"], label)
    for edge in view["external_edges"]:
        if edge.get("hypothesis_id"):
            link("external-" + edge["id"], edge["hypothesis_id"], edge["external_id"], "参考资料")
    for edge in view["external_relations"]:
        link("relation-" + edge["id"], edge["source_external"], edge["target_external"], "资料关联")
    revision_row = store.db.execute("SELECT value FROM graph_meta WHERE key='graph_revision'").fetchone()
    return {"nodes": nodes, "edges": edges, "tm_science_graph_projection": {
        "version": 3, "schema": "theory-master/vault-canvas/v1", "canonical": "sqlite",
        "read_only": True, "branch_root": branch_root,
        "revision": int(revision_row["value"]) if revision_row else 0,
        "missing_documents": sorted(missing), "markdown_policy": "existing_files_only",
    }}


def _refresh_locked(database: Path, binding: dict[str, Any]) -> dict[str, Any]:
    from .science_graph_store import ScienceGraphStore
    target = Path(binding["canvas_path"])
    state_path = _state_path(database, binding)
    prior = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    try:
        with ScienceGraphStore(database, readonly=True) as store:
            store.db.execute("BEGIN")
            try:
                projection = vault_canvas_projection(store, vault_root=Path(binding["vault_root"]),
                                                     branch_root=binding["branch_root"])
            finally:
                store.db.execute("ROLLBACK")
        changed = _atomic_json(target, projection)
        metadata = {**binding, "status": "ready", "error": None,
                    "revision": projection["tm_science_graph_projection"]["revision"],
                    "updated_at": _now() if changed else prior.get("updated_at") or _now(),
                    "missing_documents": projection["tm_science_graph_projection"]["missing_documents"],
                    "node_count": len(projection["nodes"]), "edge_count": len(projection["edges"])}
        _atomic_json(state_path, metadata)
        return {**metadata, "changed": changed}
    except Exception as exc:
        # Keep the last complete Canvas and expose a retryable error independently
        # of the already committed scientific transaction.
        metadata = {**prior, **binding, "status": "pending", "error": str(exc),
                    "last_attempt_at": _now(), "updated_at": prior.get("updated_at")}
        try:
            _atomic_json(state_path, metadata)
        except OSError:
            log.exception("Could not record Canvas refresh failure for %s", target)
        raise


def refresh_canvas(database: str | Path, *, vault_root: str | Path | None = None,
                   canvas_path: str | Path | None = None, branch_root: str | None = None) -> dict[str, Any]:
    """Refresh one root-level Canvas atomically; never write a Markdown document."""
    database = Path(database).expanduser().resolve()
    if vault_root is None and canvas_path is None:
        bindings = canvas_bindings(database)
        existing = next((row for row in bindings if row["branch_root"] == branch_root), None)
        full = next((row for row in bindings if row["branch_root"] is None), None)
        if existing:
            vault_root, canvas_path = existing["vault_root"], existing["canvas_path"]
        elif full:
            vault_root = full["vault_root"]
    binding = _binding(database, vault_root=Path(vault_root) if vault_root is not None else None,
                       canvas_path=Path(canvas_path) if canvas_path is not None else None,
                       branch_root=branch_root)
    with _projection_lock(database):
        return _refresh_locked(database, binding)


def refresh_registered_canvases(database: str | Path) -> list[dict[str, Any]]:
    """Refresh every registered view, retrying prior failures on each invocation."""
    database = Path(database).expanduser().resolve()
    results = []
    try:
        with _projection_lock(database):
            for binding in canvas_bindings(database):
                try:
                    results.append(_refresh_locked(database, binding))
                except Exception:
                    log.exception("Canvas refresh pending for %s", binding["canvas_path"])
                    path = _state_path(database, binding)
                    results.append(json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                                   else {**binding, "status": "pending"})
    except Exception as exc:
        # Even lock/metadata-directory failures must not block the scientific
        # caller after its database commit. A later invocation retries all views.
        log.exception("Canvas refresh unavailable for %s", database)
        results.append({"status": "pending", "error": str(exc), "database": str(database)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh a one-way Obsidian Canvas from the current science graph")
    parser.add_argument("database", type=Path)
    parser.add_argument("--vault-root", type=Path)
    parser.add_argument("--canvas-path", type=Path)
    parser.add_argument("--branch-root", help="Hypothesis ID whose descendants form the branch")
    parser.add_argument("--register", action="store_true", help="Keep this view updated on future graph commits")
    args = parser.parse_args()
    options = {"vault_root": args.vault_root, "canvas_path": args.canvas_path, "branch_root": args.branch_root}
    if args.register:
        register_canvas_binding(args.database, **options)
    print(json.dumps(refresh_canvas(args.database, **options), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
