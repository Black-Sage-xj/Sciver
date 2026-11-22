"""Read-only, bounded source snapshots for task-level research publications.

Publication does not adjudicate science.  This module reads schema-v2 SQLite
directly so that collecting an already sealed task never migrates its store.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from .markdown import CODE

SCHEMA = "graph-research/final-report-snapshot/v1"
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_FILES = 512
FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".py", ".r", ".jl", ".yaml", ".yml"}
_PRIVATE_PARTS = {".git", ".ssh", ".aws", ".codex", ".agents", ".claude", "codex-secrets"}
_LINK = re.compile(r"!?\[\[([^\]\n|]+)(?:\|[^\]\n]*)?\]\]|!?\[[^\]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)\n]*)?\)")
_LOCAL_FILE = re.compile(r"(?<![\w])((?:[\w./-]+/)*[\w.-]+\.(?:markdown|jsonl|json|csv|tsv|png|jpe?g|gif|webp|svg|txt|md|py|r|jl|yaml|yml))(?![\w])", re.I)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _regular(path: Path) -> bool:
    return path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))


def _private(path: Path) -> bool:
    return (any(p.casefold() in _PRIVATE_PARTS for p in path.parts)
            or path.name.casefold().startswith((".env", "credentials", "sudo-password")))


def _safe_figure(raw: bytes, suffix: str) -> bool:
    if suffix == ".png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".webp":
        return raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    if suffix != ".svg" or b"<!ENTITY" in raw.upper() or b"<!DOCTYPE" in raw.upper():
        return False
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        return False
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].casefold()
        if tag in {"script", "foreignobject", "iframe", "object", "embed", "style", "animate", "set"}:
            return False
        for key, value in node.attrib.items():
            key = key.rsplit("}", 1)[-1].casefold()
            if key.startswith("on") or "url(" in value.casefold():
                return False
            if key in {"href", "src"} and not value.startswith("#"):
                return False
    return True


def _parse(raw: Any, fallback: Any, warnings: list[str], label: str) -> Any:
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        warnings.append(f"invalid_json:{label}")
        return fallback
    if not isinstance(result, type(fallback)):
        warnings.append(f"invalid_json_type:{label}")
        return fallback
    return result


def _snapshot_rows(database: Path, require_seal: bool) -> tuple[dict[str, Any], list[str]]:
    if not _regular(database):
        raise ValueError("Graph database is missing or traverses a symlink")
    warnings: list[str] = []
    result: dict[str, Any] = {}
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"hypotheses", "cycle_evaluations"} <= tables:
            raise ValueError("A schema-v2 research graph is required")
        for table in ("graph_meta", "hypotheses", "hypothesis_reading_context", "outcomes", "cycle_evaluations",
                      "cycle_preservations", "scientific_edges", "external_records", "external_edges",
                      "external_relations", "frontier_branches", "hypothesis_cycle_owners", "task_goal_terminal",
                      "goal_gate_assessments", "goal_gate_assessment_revisions", "cycle_memory_index"):
            result[table] = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')] if table in tables else []
        result["graph_events_max_seq"] = (connection.execute("SELECT COALESCE(MAX(seq),0) FROM graph_events").fetchone()[0]
                                          if "graph_events" in tables else 0)
        result["frozen_hypotheses"] = [dict(row) for row in connection.execute(
            "SELECT subject,detail_json FROM graph_events WHERE action='hypothesis.frozen' ORDER BY seq")
        ] if "graph_events" in tables else []
        for key, rows in result.items():
            if isinstance(rows, list):
                rows.sort(key=lambda row: _canonical(row))
        seals = result["task_goal_terminal"]
        if require_seal and not any(row.get("verdict") == "passed" for row in seals):
            raise ValueError("Final report requires a committed passed Goal seal")
        if not seals:
            warnings.append("goal_seal_missing:report_is_not_a_completion_receipt")
        return result, warnings
    finally:
        connection.close()


def build_snapshot(database: Path, *, research_root: Path, plan_path: Path,
                   task_metadata: dict | None = None, require_seal: bool = True,
                   allowed_roots: Iterable[Path] | None = None) -> dict[str, Any]:
    """Collect all recorded rounds and explicitly referenced, bounded materials.

    ``allowed_roots`` is a host-supplied binding for separated vault/runtime
    locations.  Referenced paths never expand it.  The explicitly supplied Plan
    is readable as one file even when it lies outside those roots.
    """
    database = Path(database).absolute()
    plan_path = Path(plan_path).absolute()
    root = Path(research_root).absolute()
    roots = sorted({root, *(Path(p).absolute() for p in (allowed_roots or ()))}, key=str)
    if any(p.is_symlink() for base in roots for p in (base, *base.parents)):
        raise ValueError("Source roots must not traverse symlinks")
    rows, warnings = _snapshot_rows(database, require_seal)
    metadata = json.loads(json.dumps(task_metadata or {}, ensure_ascii=False))
    transitions = {r["hypothesis_id"]: _parse(r["source_json"], {}, warnings, r["hypothesis_id"])
                   for r in rows["hypothesis_reading_context"]}
    frozen = {r["subject"]: _parse(r["detail_json"], {}, warnings, r["subject"])
              for r in rows["frozen_hypotheses"]}
    whole = {}
    for row in rows["outcomes"]:
        item = dict(row)
        for key in list(item):
            if key.endswith("_json"):
                item[key[:-5]] = _parse(item.pop(key), [], warnings, f"{row['id']}:{key}")
        whole[row["hypothesis_id"]] = item
    hypotheses = []
    for row in rows["hypotheses"]:
        item = {**row, "source_ref": row["id"], "research_transition": transitions.get(row["id"]),
                "frozen_hypothesis": frozen.get(row["id"]), "whole_h_assessment": whole.get(row["id"]),
                "frontiers": [r for r in rows["frontier_branches"] if r["hypothesis_id"] == row["id"]]}
        if row["id"] in frozen:
            item.update({key: frozen[row["id"]][key] for key in ("claim", "scope", "decisive_test") if key in frozen[row["id"]]})
        hypotheses.append(item)
    def evaluations(table: str) -> list[dict[str, Any]]:
        return [{**_parse(r["source_json"], {}, warnings, str(r.get("id", r.get("cycle_id")))),
                 **{k: v for k, v in r.items() if k != "source_json"},
                 "source_sha256": hashlib.sha256(r["source_json"].encode()).hexdigest()}
                for r in rows[table]]
    outcomes = evaluations("cycle_evaluations")
    preservations = evaluations("cycle_preservations")
    lineage = [{**r, "provenance": _parse(r["provenance_json"], {}, warnings, r["id"])} for r in rows["scientific_edges"]]
    for edge in lineage:
        edge["role"] = edge["provenance"].get("lineage_role", "unknown")
        edge["source_ref"] = edge["id"]
    seals = [{**r, "detail": _parse(r["detail_json"], {}, warnings, r["goal_id"])} for r in rows["task_goal_terminal"]]
    if len(seals) > 1:
        warnings.append("multiple_goal_seals")
    seal = seals[0] if seals else None
    documents: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    source_index: dict[str, dict[str, Any]] = {}
    ambiguous_aliases: set[str] = set()
    total_bytes = 0
    linked_documents: deque[tuple[Path, str]] = deque()
    supplemental_materials: list[tuple[str, str, Path | None, str | None]] = []

    def index(reference: str, item: dict[str, Any]) -> None:
        if reference in ambiguous_aliases:
            return
        old = source_index.get(reference)
        if old is not None and old != item:
            source_index.pop(reference)
            ambiguous_aliases.add(reference)
        else:
            source_index[reference] = item

    def resolve(reference: str, origin: Path | None = None, *, explicit_plan: bool = False) -> tuple[Path | None, str]:
        if not isinstance(reference, str) or not reference or "\x00" in reference:
            return None, "invalid_reference"
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            return None, "external_reference"
        candidate = Path(unquote(parsed.path))
        if not candidate.parts:
            return None, "anchor_reference"
        bases = [origin, origin.parent] if origin else []
        bases += [database.parent, plan_path.parent, root, *roots]
        candidates = [candidate] if candidate.is_absolute() else [base / candidate for base in bases if base]
        hits: set[Path] = set()
        rejected = "missing"
        for path in candidates:
            path = path.absolute()
            if not path.suffix and not path.exists() and path.with_suffix(".md").is_file():
                path = path.with_suffix(".md")
            resolved = path.resolve()
            if not (any(resolved.is_relative_to(base.resolve()) for base in roots)
                    or (explicit_plan and resolved == plan_path.resolve())):
                rejected = "outside_allowed_roots"
            elif _private(path):
                rejected = "private_path"
            elif not _regular(path):
                rejected = "symlink_or_missing" if path.exists() else "missing"
            else:
                hits.add(resolved)
        if len(hits) != 1:
            return None, "ambiguous_reference" if hits else rejected
        return hits.pop(), "available"

    def collect(reference: str, *, role: str = "evidence", origin: Path | None = None,
                expected_sha256: str | None = None, explicit_plan: bool = False,
                follow_links: bool = False) -> dict[str, Any] | None:
        nonlocal total_bytes
        # Graph/Evidence identities are citations, not extensionless filenames.
        if isinstance(reference, str) and re.fullmatch(r"(?:H-\d{4,}|E-[A-Za-z0-9_.-]+)", reference):
            return None
        path, status = resolve(reference, origin, explicit_plan=explicit_plan)
        if path is None:
            if status not in {"anchor_reference", "external_reference"}:
                warnings.append(f"{status}:{reference}")
            return None
        if str(path) in by_path:
            previous = by_path[str(path)]
            if role not in previous["roles"]:
                previous["roles"].append(role)
            if expected_sha256 and previous.get("sha256") and previous["sha256"] != expected_sha256:
                previous.update(status="source_changed", expected_sha256=expected_sha256)
                warnings.append(f"source_changed:{reference}")
            index(reference, {"kind": previous["kind"], "id": previous["source_ref"], "sha256": previous.get("sha256")})
            return previous
        if len(by_path) >= MAX_FILES:
            warnings.append("material_count_limit_reached")
            return None
        size = path.stat().st_size
        suffix = path.suffix.casefold()
        kind = "figure" if suffix in FIGURE_SUFFIXES else "document"
        root_index = next((i for i, base in enumerate(roots) if path.is_relative_to(base)), None)
        label = f"root-{root_index}/{path.relative_to(roots[root_index]).as_posix()}" if root_index is not None else "research_plan.md"
        item: dict[str, Any] = {"reference": reference, "source_ref": "file:" + label, "source_path": str(path),
                                "kind": kind, "roles": [role], "size": size, "status": "available"}
        if kind == "figure":
            item["path"] = str(path)
        by_path[str(path)] = item
        (figures if kind == "figure" else documents).append(item)
        if size > MAX_FILE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
            item.update(status="not_frozen_size_limit", expected_sha256=expected_sha256)
            warnings.append(f"not_frozen_size_limit:{reference}")
            return item
        raw = path.read_bytes()
        total_bytes += len(raw)
        digest = hashlib.sha256(raw).hexdigest()
        item["sha256"] = digest
        item["id"] = ("figure:" + digest[:16] + ":" + hashlib.sha256(item["source_ref"].encode()).hexdigest()[:8]
                      if kind == "figure" else item["source_ref"])
        if expected_sha256 and expected_sha256 != digest:
            item.update(status="source_changed", expected_sha256=expected_sha256)
            warnings.append(f"source_changed:{reference}")
        if kind == "figure" and not _safe_figure(raw, suffix):
            item["status"] = "unsafe_or_invalid_figure"
            warnings.append(f"unsafe_or_invalid_figure:{reference}")
        if kind == "document" and suffix in TEXT_SUFFIXES and len(raw) <= MAX_TEXT_BYTES:
            try:
                item["content"] = raw.decode("utf-8")
            except UnicodeDecodeError:
                warnings.append(f"non_utf8_document:{reference}")
        for alias in (reference, item["source_ref"], item["id"]):
            index(alias, {"kind": kind, "id": item["id"], "sha256": digest})
        if "content" in item and (follow_links or role in {
                "evidence", "registered_evidence_artifact", "registered_material", "same_invocation_material", "referenced_material"}):
            linked_documents.append((path, item["content"]))
        return item

    plan = collect(str(plan_path), role="research_plan", explicit_plan=True, follow_links=True)
    if not plan or "content" not in plan:
        raise ValueError("Research Plan must be an available bounded UTF-8 document")
    original_question = {"source_ref": "research_plan", "path": str(plan_path), "sha256": plan["sha256"], "content": plan["content"]}
    index("research_plan", {"kind": "research_plan", "id": "research_plan", "sha256": plan["sha256"]})
    if seal and seal.get("research_plan_sha256") != plan["sha256"]:
        warnings.append("research_plan_changed_since_goal_seal")
    goal_receipts = []
    if seal:
        seal["source_ref"] = "goal:terminal"
        index(seal["source_ref"], {"kind": "goal_terminal", "id": seal["source_ref"], "sha256": _digest(seal)})
        references = [metadata.get("goal_receipt_path"), seal.get("judge_receipt_ref"), seal["detail"].get("receipt_path")]
        for reference in dict.fromkeys(r for r in references if isinstance(r, str) and r):
            receipt = collect(reference, role="goal_receipt")
            if receipt and "content" in receipt:
                goal_receipts.append({"source_ref": receipt["source_ref"], "sha256": receipt["sha256"],
                                      "content": receipt["content"]})
    gate_rows = rows["goal_gate_assessment_revisions"] or rows["goal_gate_assessments"]
    goal_history = []
    for number, row in enumerate(gate_rows, start=1):
        ref = f"goal:assessment:{number}"
        record = {**row, "source_ref": ref}
        goal_history.append(record)
        index(ref, {"kind": "goal_assessment", "id": ref, "sha256": _digest(record)})
    for hypothesis in hypotheses:
        index(hypothesis["id"], {"kind": "hypothesis", "id": hypothesis["id"]})
        if hypothesis.get("dossier_ref"):
            collect(hypothesis["dossier_ref"], role="hypothesis_dossier", follow_links=True)
    cycle_dirs: dict[str, Path] = {}
    for row in rows["cycle_memory_index"]:
        cycle_dirs[str(row["cycle_id"])] = Path(row["cycle_dir"])
    evidence_records: dict[tuple[str, str], dict[str, Any]] = {}

    def collect_workspace_references(text: str, workspace: Path) -> None:
        # Older outcomes sometimes record concrete paths in prose, with an
        # empty artifact-pointers array. Admit only explicit existing files.
        for reference in dict.fromkeys(match[1] for match in _LOCAL_FILE.finditer(text)):
            path, _ = resolve(reference, workspace)
            if path is not None and path.is_relative_to(workspace.resolve()):
                collect(str(path), role="recorded_evidence_artifact", origin=workspace)

    def collect_evidence(cycle: str, candidates: list[Path]) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for cycle_dir in candidates:
            # The registry is append-only; a projection adds lifecycle and
            # adjudicability information without replacing its scientific text.
            for relative in ("evidence/registry.jsonl", "workspace/evidence_manifest.json"):
                path, _ = resolve(str(cycle_dir / relative))
                if path is None:
                    continue
                document = collect(str(path), role="evidence_registry" if relative.endswith("jsonl") else "evidence_manifest")
                if not document or "content" not in document:
                    continue
                if relative.endswith("jsonl"):
                    found = [_parse(line, {}, warnings, relative) for line in document["content"].splitlines() if line.strip()]
                else:
                    payload = _parse(document["content"], {}, warnings, relative)
                    if isinstance(payload.get("payload"), dict):
                        payload = payload["payload"]
                    found = payload.get("records") or []
                for record in found:
                    if isinstance(record, dict) and record.get("evidence_id"):
                        records[str(record["evidence_id"])] = {**record, "manifest_source_ref": document["source_ref"]}
            lifecycle = cycle_dir / "evidence/lifecycle.jsonl"
            if lifecycle.is_file():
                collect(str(lifecycle), role="evidence_lifecycle")
        for evidence_id, record in records.items():
            record.setdefault("cycle_id", cycle)
            record["source_ref"] = evidence_id
            evidence_records[(cycle, evidence_id)] = record
            index(evidence_id, {"kind": "evidence", "id": evidence_id,
                                "sha256": _digest({k: v for k, v in record.items() if k != "source_ref"})})
            pointers = record.get("artifact_pointers") or []
            if isinstance(pointers, str):
                pointers = [pointers]
            hashes = (record.get("integrity") or {}).get("artifact_hashes") or {}
            for pointer in pointers:
                if isinstance(pointer, str):
                    collect(pointer, role="registered_evidence_artifact", origin=candidates[0] / "workspace",
                            expected_sha256=hashes.get(pointer))
            for field in ("method", "result", "body", "summary"):
                value = record.get(field)
                if isinstance(value, (dict, list)):
                    value = _canonical(value).decode("utf-8")
                if isinstance(value, str):
                    collect_workspace_references(value, candidates[0] / "workspace")
            binding = record.get("execution_materials") or {}
            if isinstance(binding, dict) and binding.get("manifest_path"):
                manifest = collect(binding["manifest_path"], role="invocation_material_manifest",
                                   expected_sha256=binding.get("manifest_sha256"))
                if manifest and manifest.get("status") == "available" and "content" in manifest:
                    payload = _parse(manifest["content"], {}, warnings, "invocation_material_manifest")
                    for asset in [*(payload.get("created") or []), *(payload.get("modified") or [])]:
                        if isinstance(asset, dict) and asset.get("path"):
                            supplemental_materials.append((asset["path"], "same_invocation_material", None, asset.get("sha256")))
        return records

    closing_cycle = str((seal or {}).get("source_cycle") or "")
    round_records = sorted(outcomes + preservations, key=lambda record: str(record["cycle_id"]) != closing_cycle)
    for item in round_records:
        ident = item.get("id") or "preservation:" + str(item["cycle_id"])
        item.setdefault("id", ident)
        item["source_ref"] = ident
        index(ident, {"kind": "outcome" if item in outcomes else "preservation", "id": ident,
                      "sha256": item["source_sha256"]})
        report = collect(item["report_ref"], role="round_report", follow_links=True) if item.get("report_ref") else None
        origin = Path(report["source_path"]).parent if report else None
        cycle = str(item["cycle_id"])
        candidates = [cycle_dirs[cycle]] if cycle in cycle_dirs else [root / cycle, root / "research" / cycle]
        registered = collect_evidence(cycle, candidates)
        context = item.get("report_context") or {}
        manifests = context.get("evidence_manifest") or []
        known_hashes = {m.get("reference"): m.get("sha256") for m in manifests if isinstance(m, dict)}
        for reference in item.get("evidence_refs") or []:
            if isinstance(reference, str):
                if reference in registered:
                    continue
                if re.fullmatch(r"E-[A-Za-z0-9_.-]+", reference):
                    warnings.append(f"missing_evidence_record:{reference}")
                else:
                    collect(reference, role="evidence", origin=origin, expected_sha256=known_hashes.get(reference))
        for entry in manifests:
            if isinstance(entry, dict) and entry.get("reference"):
                collect(entry["reference"], role="evidence", origin=origin, expected_sha256=entry.get("sha256"))
        for document in context.get("documents") or []:
            if isinstance(document, dict) and isinstance(document.get("content"), str):
                index(str(document.get("reference") or document.get("source_ref") or ident + ":document"),
                      {"kind": "frozen_supplement", "id": ident, "sha256": document.get("sha256")})
        for match in _LINK.finditer(CODE.sub("", str(context.get("body") or ""))):
            collect(match[1] or match[2], role="recorded_report_material", origin=origin)
        for body in (context.get("body"), report.get("content") if report else None, item.get("summary")):
            if isinstance(body, str):
                collect_workspace_references(body, candidates[0] / "workspace")
        for cycle_dir in candidates:
            path, _ = resolve(str(cycle_dir / "report_material_catalog.json"))
            if path:
                catalog = collect(str(path), role="report_material_catalog")
                if catalog and "content" in catalog:
                    payload = _parse(catalog["content"], {}, warnings, str(path))
                    for material in payload.get("materials") or []:
                        for asset in material.get("assets") or []:
                            if asset.get("status") == "available" and (asset.get("path") or asset.get("reference")):
                                supplemental_materials.append((asset.get("path") or asset["reference"], "registered_material",
                                                               cycle_dir, asset.get("sha256")))
            for relative in ("workspace/evidence_manifest.json", "scientific_model_out.json"):
                if (cycle_dir / relative).is_file():
                    collect(str(cycle_dir / relative), role=relative.split("/")[-1])
    scientific_model = {"source_ref": "research_plan", "section": "Scientific Model", "content": ""}
    model_match = re.search(r"(?mi)^(#{1,6})\s+Scientific Model[^\n]*\n", plan["content"])
    if model_match:
        tail = plan["content"][model_match.end():]
        scientific_model["content"] = re.split(r"(?m)^#{1," + str(len(model_match[1])) + r"}\s+", tail, maxsplit=1)[0].strip()
    model_path = metadata.get("scientific_model_state_path") or str(root / "scientific_model_state.json")
    model_file = collect(model_path, role="scientific_model_state")
    if model_file and "content" in model_file:
        scientific_model["metadata"] = _parse(model_file["content"], {}, warnings, "scientific_model_state")
    # Register every round's primary records before expanding supplemental
    # outputs or recursive links. A single invocation can contain hundreds of files.
    for reference, role, origin, expected_sha256 in supplemental_materials:
        collect(reference, role=role, origin=origin, expected_sha256=expected_sha256)
    while linked_documents:
        path, content = linked_documents.popleft()
        for match in _LINK.finditer(CODE.sub("", content)):
            collect(match[1] or match[2], role="referenced_material", origin=path.parent, follow_links=True)
        for reference in dict.fromkeys(match[1] for match in _LOCAL_FILE.finditer(content)):
            resolved, _ = resolve(reference, path.parent)
            if resolved is not None:
                collect(str(resolved), role="referenced_material", origin=path.parent, follow_links=True)
    for edge in lineage:
        index(edge["id"], {"kind": "lineage", "id": edge["id"]})
    external = [{**r, "source_ref": r["id"]} for r in rows["external_records"]]
    for item in external:
        index(item["id"], {"kind": "external", "id": item["id"]})
    snapshot = {"schema": SCHEMA, "task_metadata": metadata,
                "sources": {"database": str(database), "research_root": str(root), "allowed_roots": list(map(str, roots)),
                            "graph_events_max_seq": rows["graph_events_max_seq"], "graph_meta": rows["graph_meta"]},
                "goal_terminal": seal, "original_question": original_question, "scientific_model": scientific_model,
                "goal_receipts": goal_receipts, "hypotheses": hypotheses, "lineage": lineage, "outcomes": outcomes,
                "preservations": preservations, "external_sources": external, "external_edges": rows["external_edges"],
                "evidence_records": sorted(evidence_records.values(), key=lambda record: (str(record["cycle_id"]), record["evidence_id"])),
                "external_relations": rows["external_relations"], "goal_gate_history": goal_history,
                "documents": documents, "figures": figures, "source_index": source_index,
                "warnings": sorted(set(warnings)), "ambiguous_source_refs": sorted(ambiguous_aliases)}
    snapshot["fingerprint"] = _digest(snapshot)
    return snapshot


def materialize_snapshot(snapshot: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Freeze unchanged registered files and snapshot metadata in a fresh directory.

    The source fingerprint remains the collection identity.  Publication errors
    are explicit warnings; unsafe, changed or oversized sources are never copied.
    """
    payload = copy.deepcopy(snapshot)
    if payload.get("schema") != SCHEMA or payload.get("fingerprint") != _digest({k: v for k, v in payload.items() if k != "fingerprint"}):
        raise ValueError("Snapshot fingerprint is invalid")
    destination = Path(destination).absolute()
    if destination.exists() or any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError("Snapshot destination must be a new directory without symlinks")
    roots = [Path(p).resolve() for p in payload["sources"]["allowed_roots"]]
    plan = Path(payload["original_question"]["path"]).resolve()
    destination.mkdir(parents=True)
    for row in payload["documents"] + payload["figures"]:
        if row.get("status") != "available" or not row.get("sha256"):
            continue
        source = Path(row["source_path"])
        if not (_regular(source) and not _private(source)
                and (source.resolve() == plan or any(source.resolve().is_relative_to(root) for root in roots))):
            row["status"] = "source_unavailable_at_freeze"
        elif source.stat().st_size > MAX_FILE_BYTES:
            row["status"] = "not_frozen_size_limit"
        else:
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                row["status"] = "source_changed_at_freeze"
            else:
                relative = "files/" + row["sha256"] + source.suffix.casefold()
                path = destination / relative
                path.parent.mkdir(exist_ok=True)
                if not path.exists():
                    with path.open("xb") as stream:
                        stream.write(raw)
                row["frozen_path"] = relative
                row["frozen_sha256"] = row["sha256"]
                if row["kind"] == "figure":
                    row["path"] = relative
                continue
        payload["warnings"].append(f"{row['status']}:{row['reference']}")
    payload["warnings"] = sorted(set(payload["warnings"]))
    payload["materialized_manifest_sha256"] = _digest(payload)
    manifest = destination / "snapshot.json"
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"snapshot": payload, "snapshot_path": str(manifest), "fingerprint": snapshot["fingerprint"],
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "warnings": payload["warnings"]}
