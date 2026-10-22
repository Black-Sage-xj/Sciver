"""Read-only Observatory adapter for a TheoryMaster Canvas and its Markdown vault.

This module never imports the TM runtime, writes to the task, or derives scientific
edges from wiki links. Each request resolves a real stable observer_id in the Run index.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from .graph_semantics import project_node

DOC_DIRS = {"nodes", "notes", "literature"}
OBSERVER_ID = re.compile(r"^[0-9a-f]{16}$")
MAX_CANVAS_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_DOCUMENT_COUNT = 3000
# Indexing/search reads are bounded across the whole task, not per file only.
MAX_INDEX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SEARCH_CACHE_TASKS = 8
MAX_GRAPH_CACHE_TASKS = 4
MAX_GRAPH_CACHE_BYTES = 32 * 1024 * 1024
_SEARCH_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_SEARCH_CACHE_BYTES = 0
_SEARCH_CACHE_LOCK = threading.RLock()
_SEARCH_BUILD_LOCKS: dict[str, threading.Lock] = {}
_INDEX_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_INDEX_CACHE_BYTES = 0
_INDEX_CACHE_LOCK = threading.RLock()
_INDEX_BUILD_LOCKS: dict[str, threading.Lock] = {}
WIKILINK = re.compile(r"(?<!!)\[\[([^\]\n]{1,512})\]\]")
MD_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\n]{1,1024})\)")
METADATA_ID = re.compile(r"(?im)^id:\s*([A-Za-z][A-Za-z0-9._-]{0,127})\s*$")
H_ID = re.compile(r"\bH-\d{1,6}\b", re.IGNORECASE)


class ExplorerError(ValueError):
    """Safe message and HTTP error category for public API responses."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _inside(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def vault_root(search_root: Path, observer_id: str, state: dict[str, Any]) -> Path:
    """Locate the vault through a Run's stable ID and canonical research root.

    The research root contains runtime/data, while source Canvas/MD live under
    the task's canonical notebook root. Never accept either path from HTTP.
    """
    if not OBSERVER_ID.fullmatch(observer_id):
        raise ExplorerError("无效任务标识", 404)
    run = next((r for r in state.get("runs", []) if r.get("observer_id") == observer_id), None)
    if run is None:
        raise ExplorerError("任务未在 Run 索引中", 404)
    repo = search_root.resolve()
    notebook = (repo / "笔记区").resolve()
    root_text, research_text = run.get("task_root"), run.get("research_root")
    if not isinstance(root_text, str) or not isinstance(research_text, str):
        raise ExplorerError("任务路径信息不完整", 404)
    root = Path(root_text).resolve()
    research = Path(research_text).resolve()
    if (
        root == notebook or not _inside(root, notebook) or not _inside(research, repo)
        or hashlib.sha256(str(research).encode("utf-8")).hexdigest()[:16] != observer_id
    ):
        raise ExplorerError("任务映射不安全", 403)
    canvas = root / "exploration.canvas"
    graph_database = root / "science_graph.sqlite3"
    if not root.is_dir() or not (
        (canvas.is_file() and _inside(canvas.resolve(), root))
        or (graph_database.is_file() and _inside(graph_database.resolve(), root))
    ):
        raise ExplorerError("此任务尚无可阅读的科研图谱", 404)
    # If present, ensure the task configuration really binds this notebook
    # to the research root reported by Run. Do not follow config-provided paths.
    config = root / "theory-master.config.json"
    if config.is_file():
        if not _inside(config.resolve(), root) or config.stat().st_size > 512 * 1024:
            raise ExplorerError("任务配置不可读取", 403)
        try:
            parsed = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExplorerError("任务配置无效", 422) from exc
        if not isinstance(parsed, dict) or (
            parsed.get("research_root") and Path(parsed["research_root"]).resolve() != research
        ) or (
            parsed.get("branch_canvas") and canvas.is_file()
            and Path(parsed["branch_canvas"]).resolve() != canvas.resolve()
        ) or (
            parsed.get("graph_database") and
            Path(parsed["graph_database"]).resolve() != graph_database.resolve()
        ):
            raise ExplorerError("任务源文件与 Run 索引不一致", 409)
    return root


def _read_text(path: Path, maximum: int) -> str:
    try:
        if path.stat().st_size > maximum:
            raise ExplorerError("文件超过只读预览大小限制", 413)
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExplorerError("无法读取科研文件", 404) from exc


def _indexed_hypothesis_cycles(store: Any) -> dict[str, str]:
    """Recover first recorded H cycles only from exact, gap-free provenance rows."""
    try:
        rows = store.db.execute(
            "SELECT cycle_id,hypothesis_id,provenance_json FROM cycle_memory_index"
        ).fetchall()
    except sqlite3.Error:
        return {}

    cycle_ids = {
        int(str(row["cycle_id"]).strip()) for row in rows
        if re.fullmatch(r"[1-9][0-9]*", str(row["cycle_id"] or "").strip())
    }
    if not cycle_ids:
        return {}

    candidates: dict[str, int] = {}
    invalid: set[str] = set()
    for row in rows:
        hypothesis_id = str(row["hypothesis_id"] or "").strip()
        if not hypothesis_id:
            continue
        cycle_text = str(row["cycle_id"] or "").strip()
        if not re.fullmatch(r"[1-9][0-9]*", cycle_text):
            invalid.add(hypothesis_id)
            continue
        try:
            provenance = json.loads(str(row["provenance_json"] or "{}"))
        except (TypeError, ValueError):
            invalid.add(hypothesis_id)
            continue
        if (not isinstance(provenance, dict)
                or provenance.get("source") != "research_state.json"
                or provenance.get("raw_hypothesis_id") != hypothesis_id):
            invalid.add(hypothesis_id)
            continue
        cycle = int(cycle_text)
        # If any earlier Cycle is absent from the index, its H lock could be
        # missing too; do not mislabel a later first-seen Cycle as creation.
        if any(previous not in cycle_ids for previous in range(1, cycle + 1)):
            continue
        if hypothesis_id not in candidates or cycle < candidates[hypothesis_id]:
            candidates[hypothesis_id] = cycle
    return {
        hypothesis_id: str(cycle) for hypothesis_id, cycle in candidates.items()
        if hypothesis_id not in invalid
    }


def _hypothesis_creation_cycle(root: Path, dossier_ref: str | None,
                               hypothesis_id: str | None = None,
                               indexed_cycle: str | None = None) -> str | None:
    """Prefer the frozen-cycle dossier marker, then an exact Cycle-memory record.

    Some older migrated H records lack ``dossier_ref`` although the canonical
    notes/hypotheses/H-xxxx.md file exists. The memory-index fallback is derived
    from research_state.json and requires exact H identity plus gap-free Cycle
    coverage. An Outcome, H numeric suffix or file mtime never substitutes.
    """
    candidates: list[str] = []
    if isinstance(dossier_ref, str) and dossier_ref.startswith("notes/"):
        candidates.append(dossier_ref)
    if isinstance(hypothesis_id, str) and re.fullmatch(r"H-[0-9]{4,6}", hypothesis_id):
        candidates.append(f"notes/hypotheses/{hypothesis_id}")
    for candidate in dict.fromkeys(candidates):
        rel = candidate if candidate.endswith(".md") else candidate + ".md"
        path = (root / rel).resolve()
        if not _inside(path, root) or not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                content = handle.read(65536).decode("utf-8", errors="replace")
        except OSError:
            continue
        marker = re.search(r"<!-- frozen-cycle:(?:cycle-)?([0-9]+) -->", content)
        if marker:
            return marker.group(1)
    if isinstance(indexed_cycle, str) and re.fullmatch(r"[1-9][0-9]*", indexed_cycle):
        return indexed_cycle
    return None


def _readable_markdown_ref(root: Path, ref: str | None) -> str | None:
    """Turn a stored vault-relative Markdown stem into a public read_document ID.

    Legacy SDK references omit .md. Only actual allowlisted files may be linked;
    malformed/out-of-vault paths must never gain an Explorer reading endpoint.
    """
    if not isinstance(ref, str) or not ref.strip():
        return None
    path, fragment = _target_parts(ref)
    normalized = _safe_relative(path)
    if fragment or not normalized or normalized.split("/", 1)[0] not in DOC_DIRS:
        return None
    candidate = normalized if normalized.endswith(".md") else normalized + ".md"
    resolved = (root / candidate).resolve()
    return candidate if _inside(resolved, root) and resolved.is_file() else None


def _sqlite_graph_projection(root: Path) -> dict[str, Any]:
    """Read-only compatibility view: exactly H and External graph nodes.

    Each final Cycle Outcome is embedded under its H; historical H may have many.
    """
    from theory_master.science_graph_store import (
        ScienceGraphStore, compact_external_summary, external_detail_ref,
    )

    graph_file = root / "science_graph.sqlite3"
    if not _inside(graph_file.resolve(), root):
        raise ExplorerError("科研数据库路径不安全", 403)
    try:
        with ScienceGraphStore(graph_file, readonly=True) as store:
            source = store.graph_view()
            indexed_cycles = _indexed_hypothesis_cycles(store)
            # Original timestamps live in retained Canvas raw headers. Imported
            # SQLite created_at/recorded_at are migration timestamps, not proof
            # of when a scientific H or Decision originally occurred.
            original_times: dict[str, str] = {}
            for record in store.db.execute("SELECT id,raw_json FROM legacy_records"):
                try:
                    raw = json.loads(record["raw_json"])
                    header = str(raw.get("text") or "")
                    matched = re.search(r"^timestamp:\s*(\S[^\r\n]*)$", header, re.M)
                    if matched:
                        original_times[str(record["id"])] = matched.group(1).strip()
                except (ValueError, TypeError, AttributeError):
                    continue
            evaluation_times = {
                str(row["id"]): str(row["recorded_at"])
                for row in store.db.execute("SELECT id,recorded_at FROM cycle_evaluations")
            }
    except (OSError, ValueError) as exc:
        raise ExplorerError("科研图谱数据库不可读取", 422) from exc
    nodes: list[dict[str, Any]] = []
    for h in source["nodes"]:
        kind = h["type"]
        if kind == "Hypothesis":
            # When an imported historical H has no source timestamp, label its
            # database timestamp as registration rather than original creation.
            h["source_timestamp"] = original_times.get(h["id"])
            h["registered_at"] = h.get("created_at")
            for outcome in h.get("cycle_outcomes") or []:
                original_outcome_ref = outcome.get("document_ref")
                if original_outcome_ref:
                    outcome["document_ref"] = _readable_markdown_ref(root, original_outcome_ref)
                    if not outcome["document_ref"]:
                        outcome["unavailable_document_ref"] = original_outcome_ref
                original_report_ref = outcome.get("report_ref")
                if original_report_ref:
                    # Keep the historical source ref, but expose a document ID
                    # that the read-only Markdown API can actually resolve.
                    resolved_report = _readable_markdown_ref(root, original_report_ref)
                    outcome["source_report_ref"] = original_report_ref
                    outcome["report_ref"] = resolved_report
                    if not resolved_report:
                        outcome["unavailable_report_ref"] = original_report_ref
                source_ids = [str(outcome.get("id") or "")]
                source_ids.extend(str(row.get("id") or "")
                                  for row in outcome.get("source_decisions") or [])
                times = sorted({original_times[id_] for id_ in source_ids
                                if id_ in original_times})
                if len(times) == 1:
                    outcome["source_timestamp"] = times[0]
                elif len(times) > 1:
                    outcome["source_timestamps"] = times
                if evaluation_times.get(str(outcome.get("id") or "")):
                    outcome["recorded_at"] = evaluation_times[str(outcome["id"])]
            frontiers = h.get("frontiers") or []
            active = next((f["status"] for f in frontiers if f["status"] == "active"), None)
            status = active or (frontiers[-1]["status"] if frontiers else None)
            created_cycle = _hypothesis_creation_cycle(
                root, h.get("dossier_ref"), h["id"], indexed_cycles.get(h["id"])
            )
            # Older H records may have a canonical SDK dossier on disk without
            # a migrated dossier_ref (e.g. H-0090); recovery is read-only.
            original_dossier_ref = h.get("dossier_ref")
            dossier_ref = _readable_markdown_ref(
                root, original_dossier_ref or f"notes/hypotheses/{h['id']}"
            )
            h["resolved_dossier_ref"] = dossier_ref
            if original_dossier_ref and not dossier_ref:
                h["unavailable_dossier_ref"] = original_dossier_ref
            text = (
                f"type: Hypothesis\nid: {h['id']}\nscientific_status: {h['scientific_status']}\n"
                + (f"created_cycle: {created_cycle}\n" if created_cycle is not None else "")
                + (f"frontier_status: {status}\n" if status else "")
                + f"\n# {h['title'] or h['id']}\n\n{h['summary']}\n"
                + (f"\n[[{dossier_ref}|查看完整猜想文档]]\n" if dossier_ref else "")
                + (f"[[{h['outcome']['document_ref']}]]"
                   if h.get("outcome") and h["outcome"].get("document_ref") else "")
            )
        else:
            detail_ref = _readable_markdown_ref(root, external_detail_ref(h.get("id")))
            text = (
                f"type: External\nid: {h['id']}\ncontent_type: {h['content_type']}\n"
                f"source: {str(h['source'] or '')[:100]}\n\n# {h['title'] or h['id']}\n\n"
                f"{compact_external_summary(h.get('content'), 220) or 'No concise preview available.'}"
            )
            if detail_ref:
                text += f"\n\n[[{detail_ref}|详细内容]]"
        nodes.append({
            "id": h["id"], "type": "text", "text": text,
            "outcome": h.get("outcome") if kind == "Hypothesis" else None,
            "dossier_ref": h.get("resolved_dossier_ref") if kind == "Hypothesis" else None,
            "unavailable_dossier_ref": h.get("unavailable_dossier_ref") if kind == "Hypothesis" else None,
            "source_timestamp": h.get("source_timestamp") if kind == "Hypothesis" else None,
            "registered_at": h.get("registered_at") if kind == "Hypothesis" else None,
            "cycle_outcomes": h.get("cycle_outcomes", []) if kind == "Hypothesis" else [],
            "whole_h_assessment": h.get("whole_h_assessment") if kind == "Hypothesis" else None,
            "cycle_count": h.get("cycle_count",0) if kind == "Hypothesis" else 0,
            "frontiers": h.get("frontiers", []) if kind == "Hypothesis" else [],
            "latest_cycle_evaluation": (h.get("latest_cycle_evaluation")
                                        if kind == "Hypothesis" else None),
            "legacy_identity_review": (h.get("legacy_identity_review")
                                       if kind == "Hypothesis" else None),
        })
    edges = [
        {"id": e["id"], "fromNode": e["source_hypothesis"],
         "toNode": e["target_hypothesis"], "edge_type": "hypothesis_evolution",
         "evolution_operator": e["evolution_operator"],
         "originating_outcome": e["originating_outcome"],
         "provenance_json": e["provenance_json"]}
        for e in source["edges"]
    ]
    edges.extend({
        "id": e["id"], "fromNode": e["hypothesis_id"], "toNode": e["external_id"],
        "edge_type": e["edge_type"], "originating_outcome": e["originating_outcome"]
    } for e in source["external_edges"])
    # External→External references preserve provenance and connectivity,
    # but never acquire scientific lineage or adjudication authority.
    edges.extend({
        "id": e["id"], "fromNode": e["source_external"], "toNode": e["target_external"],
        "edge_type": e["edge_type"]
    } for e in source.get("external_relations", []))
    return {"nodes": nodes, "edges": edges}


def load_canvas(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if (root / "science_graph.sqlite3").is_file():
        return _sqlite_graph_projection(root)
    path = root / "exploration.canvas"
    # Defend against replacing the public Canvas with a symlink outside the vault.
    if not _inside(path.resolve(), root):
        raise ExplorerError("画布路径不安全", 403)
    try:
        data = json.loads(_read_text(path, MAX_CANVAS_BYTES))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExplorerError("Canvas 格式无效", 422) from exc
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges", []), list):
        raise ExplorerError("Canvas 缺少节点或连线", 422)
    return data


def document_paths(root: Path) -> dict[str, Path]:
    """Closed allowlist: top-level research_plan.md plus selected Markdown subtrees."""
    items: dict[str, Path] = {}
    candidates = [root / "research_plan.md"]
    for dirname in sorted(DOC_DIRS):
        directory = root / dirname
        if directory.is_dir() and _inside(directory.resolve(), root):
            candidates.extend(sorted(directory.rglob("*.md")))
    for candidate in candidates:
        rel = candidate.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or len(items) >= MAX_DOCUMENT_COUNT:
            continue
        resolved = candidate.resolve()
        if not _inside(resolved, root) or not resolved.is_file():
            continue
        items[rel.as_posix()] = resolved
    return items


def _target_parts(target: str) -> tuple[str, str]:
    """Strip alias and fragment while retaining anchors separately."""
    head = target.split("|", 1)[0].strip()
    if head.startswith("#"):
        return "", head[1:]
    path, sep, fragment = head.partition("#")
    return unquote(path.strip()), unquote(fragment) if sep else ""


def _safe_relative(value: str, source_doc: str | None = None) -> str | None:
    if not value or "\\" in value or value.startswith(("/", "~")) or ":" in value or "\x00" in value:
        return None
    # Wiki links with slash are vault-root relative; ./ and ../ are document relative.
    if value.startswith(("./", "../")) and source_doc:
        prefix = PurePosixPath(source_doc).parent.parts
        segments = list(prefix) + value.split("/")
    else:
        segments = value.split("/")
    clean: list[str] = []
    for segment in segments:
        if segment in {"", "."}:
            continue
        if segment == "..":
            if not clean:
                return None
            clean.pop()
            continue
        if segment.startswith("."):
            return None
        clean.append(segment)
    return "/".join(clean) or None


def _first_heading(text: str) -> str:
    for line in text.splitlines()[:30]:
        if line.startswith("# "):
            return line.lstrip("# ").strip()[:150]
    return ""


def _display_name(node: dict[str, Any]) -> str:
    title = _first_heading(str(node.get("text", "")))
    return title or str(node.get("label") or node.get("file") or node.get("id") or "Untitled")[:150]


def _aliases(nodes: list[dict[str, Any]], documents: dict[str, Path]) -> dict[str, str]:
    """Only index exact node IDs/explicit metadata and *unambiguous* document stems."""
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()

    def add(name: str, target: str) -> None:
        key = name.casefold()
        if key in aliases and aliases[key] != target:
            ambiguous.add(key)
        else:
            aliases[key] = target

    for node in nodes:
        ident = str(node.get("id", "")).strip()
        if not ident:
            continue
        uri = "node:" + ident
        add(ident, uri)
        text = str(node.get("text") or "")
        match = METADATA_ID.search(text)
        if match:
            add(match.group(1), uri)
        title = _first_heading(text)
        hids = H_ID.findall(title)
        if len(set(hids)) == 1:
            add(hids[0], uri)
    stem_counts: dict[str, int] = {}
    for rel in documents:
        stem_counts[PurePosixPath(rel).stem.casefold()] = stem_counts.get(PurePosixPath(rel).stem.casefold(), 0) + 1
    for rel in documents:
        uri = "doc:" + rel
        add(rel, uri)
        add(rel[:-3], uri)
        stem = PurePosixPath(rel).stem
        if stem_counts[stem.casefold()] == 1:
            add(stem, uri)
    for key in ambiguous:
        aliases.pop(key, None)
    return aliases


def resolve_link(target: str, aliases: dict[str, str], source_doc: str | None = None) -> dict[str, str] | None:
    path, fragment = _target_parts(target)
    if not path:
        return {"kind": "anchor", "id": "", "anchor": fragment} if fragment and source_doc else None
    if urlsplit(path).scheme or path.startswith("//"):
        return None
    normalized = _safe_relative(path, source_doc)
    if normalized is None:
        return None
    values = [normalized, normalized.removesuffix(".md")]
    # Obsidian global wiki lookups are allowed only when the basename is unique.
    if "/" not in normalized:
        values.append(PurePosixPath(normalized).stem)
    for value in values:
        found = aliases.get(value.casefold())
        if found:
            kind, ident = found.split(":", 1)
            return {"kind": kind, "id": ident, "anchor": fragment}
    return None


def iter_references(text: str):
    for m in WIKILINK.finditer(text):
        yield m.group(1)
    for m in MD_LINK.finditer(text):
        yield m.group(1)


def _document_preview(path: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> str:
    """Read a bounded UTF-8 prefix; the full document API remains paginated."""
    try:
        with path.open("rb") as stream:
            return stream.read(max(0, min(MAX_DOCUMENT_BYTES, int(max_bytes)))).decode("utf-8", errors="ignore")
    except OSError as exc:
        raise ExplorerError("无法读取科研文件", 404) from exc


def read_document(root: Path, path: str, offset: int = 0) -> dict[str, Any]:
    """Paginate arbitrarily long UTF-8 Markdown without a total length cap."""
    if not isinstance(path, str) or len(path) > 512 or not isinstance(offset, int) or offset < 0:
        raise ExplorerError("无效文档路径或偏移量", 400)
    files = document_paths(root)
    if path not in files:
        raise ExplorerError("未找到可公开阅读的 Markdown 文档", 404)
    file = files[path]
    size = file.stat().st_size
    if offset > size:
        raise ExplorerError("文档读取偏移量超出范围", 400)
    try:
        with file.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(MAX_DOCUMENT_BYTES)
            if offset + len(chunk) < size:
                # Prefer a complete Markdown line to avoid partial block syntax.
                last_line = chunk.rfind(b"\n")
                if last_line >= 0:
                    chunk = chunk[:last_line + 1]
                else:
                    # A long line may exceed the chunk; maintain UTF-8 boundary.
                    while chunk:
                        try:
                            chunk.decode("utf-8")
                            break
                        except UnicodeDecodeError as exc:
                            if exc.start >= len(chunk) - 4:
                                chunk = chunk[:exc.start]
                            else:
                                raise
            text = chunk.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExplorerError("无法读取科研文档", 422) from exc
    end = offset + len(chunk)
    return {
        "id": path, "title": _first_heading(text) or PurePosixPath(path).stem,
        "content": text, "next_offset": end if end < size else None,
    }


def _build_index_uncached(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    canvas = load_canvas(root)
    docs = document_paths(root)
    # Preserve original geometry/edge semantics; never derive edge records from references.
    nodes: list[dict[str, Any]] = []
    for raw in canvas["nodes"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        nodes.append({
            "id": raw["id"], "type": raw.get("type", "text"),
            "text": str(raw.get("text") or ""),
            "file": str(raw.get("file") or ""),
            "label": _display_name(raw),
            "x": raw.get("x", 0), "y": raw.get("y", 0),
            "width": raw.get("width", 420), "height": raw.get("height", 260),
            "color": raw.get("color", ""),
            "semantic": {
                **project_node(raw),
                "outcome": raw.get("outcome"),
                "dossier_ref": raw.get("dossier_ref"),
                "unavailable_dossier_ref": raw.get("unavailable_dossier_ref"),
                "source_timestamp": raw.get("source_timestamp"),
                "registered_at": raw.get("registered_at"),
                "cycle_outcomes": list(raw.get("cycle_outcomes") or []),
                "whole_h_assessment": raw.get("whole_h_assessment"),
                "cycle_count": raw.get("cycle_count",0),
                "frontiers": list(raw.get("frontiers") or []),
                "latest_cycle_evaluation": raw.get("latest_cycle_evaluation"),
                "legacy_identity_review": raw.get("legacy_identity_review"),
            },
        })
    aliases = _aliases(nodes, docs)
    backlinks: dict[str, list[dict[str, str]]] = {}
    references: dict[str, list[dict[str, str]]] = {}

    def scan(ref_kind: str, ref_id: str, label: str, content: str) -> None:
        source_key = f"{ref_kind}:{ref_id}"
        targets_seen: set[str] = set()
        references[source_key] = []
        for link in iter_references(content):
            resolved = resolve_link(link, aliases, ref_id if ref_kind == "doc" else None)
            if resolved is None or resolved["kind"] == "anchor":
                continue
            target_key = resolved["kind"] + ":" + resolved["id"]
            if target_key in targets_seen:
                continue
            targets_seen.add(target_key)
            references[source_key].append(resolved)
            backlinks.setdefault(target_key, []).append({
                "kind": ref_kind, "id": ref_id, "label": label,
            })

    for node in nodes:
        scan("node", node["id"], node["label"], node["text"])
    document_index: list[dict[str, str]] = []
    # Keep the whole request bounded even when a task contains thousands of MD files.
    per_document_limit = min(MAX_DOCUMENT_BYTES, max(8192, MAX_INDEX_TOTAL_BYTES // max(1, len(docs))))
    indexed_document_bytes = 0
    partial_index = len(docs) >= MAX_DOCUMENT_COUNT
    for rel, real in docs.items():
        try:
            file_size = real.stat().st_size
            text = _document_preview(real, max_bytes=per_document_limit)
        except (OSError, ExplorerError):
            partial_index = True
            continue
        indexed_document_bytes += len(text.encode("utf-8"))
        partial_index = partial_index or file_size > per_document_limit
        title = _first_heading(text) or PurePosixPath(rel).stem
        document_index.append({"id": rel, "title": title, "excerpt": re.sub(r"\s+", " ", text)[:280]})
        scan("doc", rel, title, text)
    valid = {n["id"] for n in nodes}
    edges = [
        e for e in canvas.get("edges", [])
        if isinstance(e, dict) and e.get("fromNode") in valid and e.get("toNode") in valid
    ]
    return {
        "canvas": ("science_graph.sqlite3" if (root / "science_graph.sqlite3").is_file()
                   else "exploration.canvas"), "nodes": nodes, "edges": edges,
        "documents": document_index, "backlinks": backlinks, "references": references,
        "aliases": aliases,
        "stats": {"node_count": len(nodes), "edge_count": len(edges), "doc_count": len(docs),
                  "partial": partial_index, "indexed_document_bytes": indexed_document_bytes},
    }


def _node_search_content(node: dict[str, Any]) -> str:
    content = str(node.get("text", ""))
    for outcome in node.get("cycle_outcomes") or ([node.get("outcome")] if node.get("outcome") else []):
        if not isinstance(outcome, dict):
            continue
        content += "\n" + "\n".join(
            str(outcome.get(k) or "") for k in ("summary", "judge_rationale", "decisive_test", "verdict")
        )
        content += "\n" + "\n".join(
            str(item) for k in ("findings", "open_questions", "boundary")
            for item in (outcome.get(k) or [])
        )
    return content


def _search_signature(root: Path, docs: dict[str, Path]) -> tuple[Any, ...]:
    paths = [root / "science_graph.sqlite3", root / "science_graph.sqlite3-wal", root / "exploration.canvas"]
    signature: list[Any] = []
    for path in paths:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None, None))
    for rel, path in docs.items():
        try:
            stat = path.stat()
            signature.append((rel, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        except OSError:
            signature.append((rel, None, None))
    return tuple(signature)


def build_index(root: Path) -> dict[str, Any]:
    """Build a graph index once per stable task-file signature.

    Cache entries are bounded by both task count and serialized size. A second
    signature check prevents publishing a snapshot while the Canvas/SQLite/MD
    files are changing underneath the indexer.
    """
    global _INDEX_CACHE_BYTES
    root = Path(root).resolve()
    key = str(root)
    docs = document_paths(root)
    signature = _search_signature(root, docs)
    with _INDEX_CACHE_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached is not None and cached["signature"] == signature:
            _INDEX_CACHE.move_to_end(key)
            return cached["index"]
        build_lock = _INDEX_BUILD_LOCKS.setdefault(key, threading.Lock())
    with build_lock:
        docs = document_paths(root)
        signature = _search_signature(root, docs)
        with _INDEX_CACHE_LOCK:
            cached = _INDEX_CACHE.get(key)
            if cached is not None and cached["signature"] == signature:
                _INDEX_CACHE.move_to_end(key)
                return cached["index"]
        index = _build_index_uncached(root)
        try:
            after_signature = _search_signature(root, document_paths(root))
        except (OSError, ExplorerError):
            after_signature = ()
        if after_signature != signature:
            # Return the coherent result to this caller, but never cache a mixed snapshot.
            return index
        payload = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        size = len(payload)
        with _INDEX_CACHE_LOCK:
            previous = _INDEX_CACHE.pop(key, None)
            if previous is not None:
                _INDEX_CACHE_BYTES -= previous["bytes"]
            if size <= MAX_GRAPH_CACHE_BYTES:
                _INDEX_CACHE[key] = {"signature": signature, "index": index, "payload": payload, "bytes": size}
                _INDEX_CACHE_BYTES += size
            while _INDEX_CACHE and (len(_INDEX_CACHE) > MAX_GRAPH_CACHE_TASKS
                                    or _INDEX_CACHE_BYTES > MAX_GRAPH_CACHE_BYTES):
                _, evicted = _INDEX_CACHE.popitem(last=False)
                _INDEX_CACHE_BYTES -= evicted["bytes"]
        return index


def build_index_payload(root: Path, observer_id: str) -> bytes:
    """Return compact cached index JSON with the stable observer ID attached.

    Avoid re-serializing a multi-megabyte graph on every browser revalidation.
    The observer ID is JSON-encoded separately and never mutates the shared index.
    """
    root = Path(root).resolve()
    index = build_index(root)
    with _INDEX_CACHE_LOCK:
        cached = _INDEX_CACHE.get(str(root))
        base = cached.get("payload") if cached is not None and cached.get("index") is index else None
    if isinstance(base, bytes) and base.endswith(b"}"):
        return base[:-1] + b',"observer_id":' + json.dumps(observer_id, ensure_ascii=True).encode("ascii") + b"}\n"
    result = dict(index)
    result["observer_id"] = observer_id
    return (json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _search_index(root: Path) -> dict[str, Any]:
    global _SEARCH_CACHE_BYTES
    root = Path(root).resolve()
    key = str(root)
    docs = document_paths(root)
    signature = _search_signature(root, docs)
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(key)
        if cached is not None and cached["signature"] == signature:
            _SEARCH_CACHE.move_to_end(key)
            return cached
        build_lock = _SEARCH_BUILD_LOCKS.setdefault(key, threading.Lock())
    with build_lock:
        docs = document_paths(root)
        signature = _search_signature(root, docs)
        with _SEARCH_CACHE_LOCK:
            cached = _SEARCH_CACHE.get(key)
            if cached is not None and cached["signature"] == signature:
                _SEARCH_CACHE.move_to_end(key)
                return cached

        canvas = load_canvas(root)
        nodes: list[dict[str, str]] = []
        node_bytes = 0
        partial = len(docs) >= MAX_DOCUMENT_COUNT
        node_budget = MAX_INDEX_TOTAL_BYTES // 2
        for node in canvas["nodes"]:
            if not isinstance(node, dict):
                continue
            label = _display_name(node)
            content = _node_search_content(node)
            encoded = content.encode("utf-8")
            remaining = max(0, node_budget - node_bytes)
            if len(encoded) > remaining:
                content = encoded[:remaining].decode("utf-8", errors="ignore")
                partial = True
            node_bytes += len(content.encode("utf-8"))
            nodes.append({"id": str(node.get("id", "")), "label": label, "content": content})

        document_budget = max(0, MAX_INDEX_TOTAL_BYTES - node_bytes)
        per_document_limit = min(MAX_DOCUMENT_BYTES, max(8192, document_budget // max(1, len(docs))))
        documents: list[dict[str, str]] = []
        document_bytes = 0
        for rel, path in docs.items():
            try:
                file_size = path.stat().st_size
                content = _document_preview(path, max_bytes=per_document_limit)
            except (OSError, ExplorerError):
                partial = True
                continue
            document_bytes += len(content.encode("utf-8"))
            if file_size > per_document_limit:
                partial = True
            documents.append({"id": rel, "label": _first_heading(content) or PurePosixPath(rel).stem,
                              "content": content})

        entry = {"signature": signature, "nodes": nodes, "documents": documents,
                 "partial": partial, "scanned_documents": len(documents),
                 "scanned_bytes": node_bytes + document_bytes, "bytes": node_bytes + document_bytes}
        with _SEARCH_CACHE_LOCK:
            previous = _SEARCH_CACHE.pop(key, None)
            if previous is not None:
                _SEARCH_CACHE_BYTES -= previous["bytes"]
            _SEARCH_CACHE[key] = entry
            _SEARCH_CACHE_BYTES += entry["bytes"]
            while _SEARCH_CACHE and (len(_SEARCH_CACHE) > MAX_SEARCH_CACHE_TASKS
                                     or _SEARCH_CACHE_BYTES > MAX_INDEX_TOTAL_BYTES):
                _, evicted = _SEARCH_CACHE.popitem(last=False)
                _SEARCH_CACHE_BYTES -= evicted["bytes"]
            return entry


def search_payload(root: Path, query: str) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > 80:
        raise ExplorerError("搜索字符串超出限制", 400)
    needle = query.strip().casefold()
    if not needle:
        return {"results": [], "partial": False, "scanned_documents": 0, "scanned_bytes": 0}
    index = _search_index(root)
    results: list[dict[str, str]] = []
    for node in index["nodes"]:
        if needle in (node["label"] + "\n" + node["content"]).casefold():
            results.append({"kind": "node", "id": node["id"], "label": node["label"],
                            "excerpt": node["content"][:200]})
        if len(results) >= 60:
            break
    if len(results) < 60:
        for doc in index["documents"]:
            content = doc["content"]
            idx = content.casefold().find(needle)
            if idx != -1:
                results.append({"kind": "doc", "id": doc["id"], "label": doc["label"],
                                "excerpt": content[max(0, idx - 100):idx + 180].replace("\n", " ")})
            if len(results) >= 60:
                break
    return {"results": results, "partial": index["partial"],
            "scanned_documents": index["scanned_documents"], "scanned_bytes": index["scanned_bytes"]}


def search(root: Path, query: str) -> list[dict[str, str]]:
    """Compatibility wrapper for callers that need only result rows."""
    return search_payload(root, query)["results"]
