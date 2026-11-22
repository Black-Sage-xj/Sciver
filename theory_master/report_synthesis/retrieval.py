"""Deterministic publication retrieval over an independently frozen snapshot.

Scientific records and negative results remain present.  Raw arrays, executable
source and row-level data are indexed explicitly, never silently truncated or
summarized with invented statistics.  The full snapshot remains the authority.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any

BUNDLE_SCHEMA = "graph-research/final-report-retrieval/v1"
_SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".r", ".jl", ".ipynb", ".js", ".ts"}
_INDEX_ROLES = {"report_material_catalog", "invocation_material_manifest", "evidence_registry", "evidence_manifest", "evidence_lifecycle"}
_RAW_FIELDS = re.compile(r"(?:bootstrap(?:_samples?|_replicates?|_draws?)?|resamples?|predictions?|posterior_draws?|residuals?|raw_data|observations?)(?:$|_)", re.I)
_SCIENTIFIC_FIELDS = re.compile(r"counterexample|contradiction|negative|finding|limitation|boundar|scope|failed|failure|unresolved|open_question|parameter|coefficient|estimate|interval|quantile", re.I)
_AVAILABLE = {"available", "frozen", "ok"}
_DUPLICATE_MIN_CHARS = 1200
_CORE_DOCUMENT_ROLES = {"research_plan", "round_report", "goal_receipt", "scientific_model_state"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _file_name(document: dict[str, Any]) -> str:
    return str(document.get("source_path") or document.get("reference") or document.get("source_ref") or "")


def _numeric_table(value: list[Any]) -> bool:
    if not value or len(value) > 128 or len(_json(value)) > 16_000:
        return False
    return all(isinstance(row, (dict, list)) and 0 < len(row) <= 16
               and all(isinstance(item, (str, int, float, bool, type(None)))
                       and (not isinstance(item, str) or len(item) <= 600)
                       for item in (row.values() if isinstance(row, dict) else row)) for row in value)


def _projection(value: Any, *, path: str, source_ref: str,
                transformations: list[dict[str, Any]]) -> Any:
    """Retain recorded scalars and small tables; index raw, long arrays exactly."""
    semantic = bool(_SCIENTIFIC_FIELDS.search(path))
    if isinstance(value, dict):
        field = path.rsplit(".", 1)[-1]
        raw_mapping = (bool(_RAW_FIELDS.search(field)) or "detail" in field.casefold()
                       or all(re.fullmatch(r"(?:unit|sample|replicate|row|draw)?[-_]?\d+", str(key), re.I) for key in value))
        if not semantic and len(value) > 64 and raw_mapping and all(isinstance(item, (dict, list)) for item in value.values()):
            marker = {"_retrieval": "index_only_record_mapping", "record_count": len(value),
                      "sha256": _sha(value), "source_ref": source_ref, "source_json_path": path,
                      "reason": "large homogeneous per-record detail mapping; original remains frozen"}
            transformations.append({"source_ref": source_ref, "path": path, "action": "record_mapping_to_index",
                                    "record_count": len(value), "sha256": marker["sha256"]})
            return marker
        return {key: _projection(item, path=f"{path}.{key}", source_ref=source_ref,
                                 transformations=transformations) for key, item in value.items()}
    if isinstance(value, list):
        # Short confidence intervals, coefficient lists and small existing
        # result tables stay intact.  No aggregate statistic is calculated.
        short_scalars = len(value) <= 32 and all(not isinstance(item, (dict, list)) for item in value)
        short_labels = len(value) <= 128 and all(isinstance(item, str) and len(item) <= 64 for item in value)
        if short_scalars or short_labels or _numeric_table(value):
            return copy.deepcopy(value)
        numeric_array = all(isinstance(item, (int, float, bool)) for item in value)
        def numeric_row(row: Any) -> bool:
            if isinstance(row, list):
                return bool(row) and all(isinstance(item, (int, float, bool, type(None))) for item in row)
            if not isinstance(row, dict) or not row:
                return False
            numeric = False
            for key, item in row.items():
                if isinstance(item, (int, float, bool)):
                    numeric = True
                elif item is None:
                    continue
                elif key.casefold() in {"id", "row", "index", "sample", "replicate", "draw"} and isinstance(item, str):
                    continue
                else:
                    return False
            return numeric
        numeric_rows = bool(value) and all(numeric_row(item) for item in value)
        raw = any(_RAW_FIELDS.search(re.sub(r"\[\d+\]", "", part)) for part in path.split("."))
        raw_identity_keys = {"replicate", "draw", "sample", "observation", "row", "index"}
        indexed_records = (bool(value) and all(isinstance(row, dict)
                           and any(key in raw_identity_keys and isinstance(item, (int, float)) for key, item in row.items())
                           for row in value))
        if semantic or not (raw or numeric_array or numeric_rows or indexed_records):
            return [_projection(item, path=f"{path}[{index}]", source_ref=source_ref,
                                transformations=transformations) for index, item in enumerate(value)]
        marker: dict[str, Any] = {"_retrieval": "index_only_array", "length": len(value),
                                   "sha256": _sha(value), "source_ref": source_ref,
                                   "source_json_path": path,
                                   "reason": "raw_or_long_array; consult the frozen original for values"}
        if value and all(isinstance(item, dict) for item in value):
            marker["record_fields"] = sorted({str(key) for item in value for key in item})
            exceptions = [item for item in value if any(
                (str(key).casefold() in {"error", "failure", "failed_check", "counterexample"} and bool(content))
                or (str(key).casefold() == "status" and re.search(r"fail|error|reject|invalid|not.converg|nonconverg", str(content), re.I))
                for key, content in item.items())]
            if exceptions:
                marker["explicit_exception_records"] = copy.deepcopy(exceptions)
        transformations.append({"source_ref": source_ref, "path": path, "action": "array_to_index",
                                "length": len(value), "sha256": marker["sha256"]})
        return marker
    return value


def _table(document: dict[str, Any], transformations: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any]]:
    content = document["content"]
    suffix = Path(_file_name(document)).suffix.casefold()
    try:
        rows = list(csv.reader(io.StringIO(content), delimiter="\t" if suffix == ".tsv" else ","))
    except csv.Error:
        return None, {"reason": "unparseable_table", "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
    metadata: dict[str, Any] = {"rows_including_header": len(rows), "columns": rows[0] if rows else [],
                                "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
    if len(rows) <= 128 and len(content) <= 16000 and all(len(row) <= 16 for row in rows):
        return content, metadata
    header = metadata["columns"]
    roles = set(document.get("roles") or ())
    outputs = {"label", "prediction", "predicted_label"}
    if (1 < len(rows) <= 128 and roles & {"registered_evidence_artifact", "recorded_evidence_artifact", "registered_material"}
            and all(len(row) == len(header) for row in rows)):
        columns = [i for i, name in enumerate(header) if name.strip().casefold() in outputs]
        if columns and len(columns) <= 8:
            if 0 not in columns and header[0].strip().casefold() in {"", "id", "row", "index", "row_id", "row_index"}:
                columns.insert(0, 0)
            text = io.StringIO(newline="")
            writer = csv.writer(text, delimiter="\t" if suffix == ".tsv" else ",", lineterminator="\n")
            writer.writerows([row[i] for i in columns] for row in rows)
            if len(text.getvalue()) <= 16_000:
                metadata = {**metadata, "reason": "exact_reported_output_columns",
                            "projected_column_indices": columns}
                transformations.append({"source_ref": document["source_ref"], "action": "recorded_table_output_columns",
                                        "column_indices": columns, "sha256": metadata["content_sha256"]})
                return text.getvalue(), metadata
    transformations.append({"source_ref": document["source_ref"], "action": "row_level_table_to_index",
                            "rows_including_header": len(rows), "sha256": metadata["content_sha256"]})
    metadata["reason"] = "row_level_or_large_table; recorded result summaries are supplied separately"
    return None, metadata


def build_report_bundle(snapshot: dict[str, Any], *, max_chars: int = 360000) -> dict[str, Any]:
    """Return a bounded, traceable model view without changing source identity.

    All H/Outcome/Evidence objects remain.  Every omitted document body retains
    its file identity, frozen locator and explicit visibility.  Failure to fit
    scientific content raises ``ValueError`` rather than discarding outcomes.
    ``retrieval.serialized_chars`` measures compact UTF-8 JSON characters.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1000:
        raise ValueError("max_chars must be an integer of at least 1000")
    if not isinstance(snapshot, dict) or not snapshot.get("fingerprint") or not isinstance(snapshot.get("source_index"), dict):
        raise ValueError("A frozen source snapshot with fingerprint and source_index is required")
    bundle = _build_bundle(snapshot, max_chars)
    if bundle["retrieval"]["serialized_chars"] <= max_chars:
        return bundle
    return _fit_supplemental_documents(snapshot, bundle, max_chars)


def _build_bundle(snapshot: dict[str, Any], max_chars: int, *, compact_file_index: bool = False) -> dict[str, Any]:
    bundle = copy.deepcopy(snapshot)
    transformations: list[dict[str, Any]] = []
    warnings: list[str] = []
    selected_refs: list[str] = []
    index_only_refs: list[str] = []
    visible: dict[str, dict[str, Any]] = {}
    text_sources: dict[str, str] = {}

    def remember_text(content: str, path: str) -> str | None:
        if len(content) < _DUPLICATE_MIN_CHARS:
            return None
        digest = hashlib.sha256(content.encode()).hexdigest()
        previous = text_sources.get(digest)
        text_sources.setdefault(digest, path)
        return previous

    def visibility(document: dict[str, Any], level: str) -> None:
        document["visibility"] = level
        reference = str(document["source_ref"])
        for ref in (reference, document.get("reference"), document.get("id")):
            if ref:
                visible[str(ref)] = {"visibility": level, "document_ref": reference,
                                     "status": document.get("status", "available")}
        (index_only_refs if level == "index_only" else selected_refs).append(reference)

    original = bundle.get("original_question") or {}
    if isinstance(original.get("content"), str):
        remember_text(original["content"], "original_question.content")
    # Preserve scoped scientific sentences in every positive, negative and
    # inconclusive round.  Large data accidentally embedded as structured
    # arrays is represented as a clearly marked projection.
    for key in ("hypotheses", "outcomes", "preservations", "evidence_records", "external_sources"):
        rows = bundle.get(key) or []
        for number, record in enumerate(rows):
            ref = str(record.get("source_ref") or record.get("id") or record.get("evidence_id") or f"{key}[{number}]")
            for field in ("summary", "body", "judge_rationale", "decisive_test"):
                if isinstance(record.get(field), str):
                    remember_text(record[field], f"{key}[{number}].{field}")
            # These collections contain the scientific statements, not raw
            # result arrays: preserve all their entries even for long runs.
            protected = {"findings", "open_questions", "boundary", "evidence_refs", "source_decisions"}
            for field, value in list(record.items()):
                if field in protected or field == "report_context":
                    continue
                if isinstance(value, (dict, list)) and field not in {"research_transition", "frozen_hypothesis", "whole_h_assessment", "frontiers"}:
                    record[field] = _projection(value, path=f"{key}[{number}].{field}", source_ref=ref,
                                                transformations=transformations)
            context = record.get("report_context")
            if isinstance(context, dict):
                reproduction = context.pop("reproduction_index", None)
                if isinstance(reproduction, str):
                    context["reproduction_index_retrieval"] = {"visibility": "index_only", "chars": len(reproduction),
                                                               "sha256": hashlib.sha256(reproduction.encode()).hexdigest()}
                    transformations.append({"source_ref": ref, "action": "machine_reproduction_index_to_index"})
                body = context.get("body")
                if isinstance(body, str):
                    duplicate = remember_text(body, f"{key}[{number}].report_context.body")
                    if duplicate:
                        context.pop("body")
                        context["body_visible_source"] = duplicate
                        transformations.append({"source_ref": ref, "action": "deduplicate_exact_text",
                                                "path": f"{key}[{number}].report_context.body", "visible_source": duplicate})
                for supplement_index, supplement in enumerate(context.get("documents") or []):
                    if isinstance(supplement, dict) and isinstance(supplement.get("content"), str):
                        duplicate = remember_text(supplement["content"], f"{key}[{number}].report_context.documents[{supplement_index}].content")
                        if duplicate:
                            supplement.pop("content")
                            supplement["content_visible_source"] = duplicate
                            transformations.append({"source_ref": ref, "action": "deduplicate_exact_text", "visible_source": duplicate})
            visible[ref] = {"visibility": "full", "scientific_record": key, "status": "recorded"}

    source_cycle = str((bundle.get("goal_terminal") or {}).get("source_cycle") or "")
    lineage_nodes = {str(edge.get(field)) for edge in bundle.get("lineage", [])
                     for field in ("source_hypothesis", "target_hypothesis") if edge.get(field)}
    evidence_closure = {str(ref) for record in bundle.get("outcomes", []) for ref in record.get("evidence_refs", [])}
    question_terms = {term.casefold() for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", str(original.get("content") or ""))}
    material_ids = {}
    for document_number, document in enumerate(bundle.get("documents", [])):
        ref = str(document["source_ref"])
        name = _file_name(document)
        suffix = Path(name).suffix.casefold()
        roles = set(document.get("roles") or [])
        document["material_id"] = f"D-{document_number + 1:04d}"
        for alias in (document.get("source_ref"), document.get("reference"), document.get("id")):
            if alias:
                material_ids[str(alias)] = document["material_id"]
        document["retrieval_priority"] = (
            100 if roles & {"research_plan", "round_report", "goal_receipt", "scientific_model_state"} else
            80 if "registered_evidence_artifact" in roles or document.get("reference") in evidence_closure else
            70 if suffix in {".md", ".markdown"} else 50)
        if source_cycle and f"/{source_cycle}/" in name:
            document["retrieval_priority"] += 5
        document["retrieval_relevance"] = {"question_filename_matches": sorted(term for term in question_terms if term in name.casefold()),
                                            "linked_from_recorded_source": bool(roles & {"referenced_material", "registered_evidence_artifact", "registered_material", "round_report"})}
        content = document.get("content")
        if document.get("status", "available") not in _AVAILABLE:
            document.pop("content", None)
            document["retrieval_index"] = {"reason": "source_unavailable_or_changed", "status": document.get("status")}
            transformations.append({"source_ref": ref, "action": "unavailable_document_to_index",
                                    "status": document.get("status")})
            warnings.append(f"source_not_available_for_support:{ref}")
            visibility(document, "index_only")
            continue
        if not isinstance(content, str):
            visibility(document, "index_only")
            continue
        outcome_archive = suffix == ".json" and "/notes/outcomes/" in name.replace("\\", "/")
        execution_metadata = suffix == ".json" and bool(re.search(r"(?:manifest|catalog|run_metadata)", Path(name).name, re.I))
        if suffix == ".json" and not execution_metadata:
            try:
                metadata_payload = json.loads(content)
            except ValueError:
                metadata_payload = None
            if isinstance(metadata_payload, dict):
                units = metadata_payload.get("units")
                unit_rows = list(units.values()) if isinstance(units, dict) else units
                execution_metadata = (("argv" in metadata_payload and ("cwd" in metadata_payload or "inputs_sha256" in metadata_payload))
                                      or (isinstance(unit_rows, list) and bool(unit_rows)
                                          and all(isinstance(unit, dict) and ("argv" in unit or "inputs_sha256" in unit)
                                                  for unit in unit_rows)))
        duplicate_reading_projection = bool(roles & {"hypothesis_dossier", "scientific_model_out.json"})
        if suffix in _SCRIPT_SUFFIXES or roles & _INDEX_ROLES or outcome_archive or execution_metadata or duplicate_reading_projection:
            document.pop("content")
            reason = "structured_hypotheses_and_outcomes_supplied_separately" if duplicate_reading_projection else "executable_or_execution_manifest"
            document["retrieval_index"] = {"reason": reason, "content_chars": len(content)}
            transformations.append({"source_ref": ref, "action": "document_to_index", "reason": reason})
            visibility(document, "index_only")
            continue
        if suffix in {".csv", ".tsv"}:
            retained, metadata = _table(document, transformations)
            document["retrieval_index"] = metadata
            if retained is None:
                document.pop("content")
                visibility(document, "index_only")
                continue
            if "projected_column_indices" in metadata:
                document["content"] = retained
                visibility(document, "structured_projection")
                continue
        elif suffix == ".json":
            try:
                parsed = json.loads(content)
            except ValueError:
                # A malformed document stays available exactly as recorded.
                warnings.append(f"invalid_json_preserved:{ref}")
            else:
                previous = len(transformations)
                projection = _projection(parsed, path="$", source_ref=ref, transformations=transformations)
                document["content"] = _json(projection)
                if len(transformations) != previous:
                    document["retrieval_index"] = {"original_content_chars": len(content),
                                                    "original_content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                                                    "reason": "recorded_scalars_and_small_tables; long_arrays_indexed"}
                    visibility(document, "structured_projection")
                    continue
                if document["content"] != content:
                    transformations.append({"source_ref": ref, "action": "canonical_json_without_value_changes"})
        elif suffix == ".jsonl":
            document.pop("content")
            document["retrieval_index"] = {"reason": "row_level_jsonl", "nonempty_lines": sum(bool(line.strip()) for line in content.splitlines())}
            transformations.append({"source_ref": ref, "action": "document_to_index", "reason": "row_level_jsonl"})
            visibility(document, "index_only")
            continue
        if "round_report" in roles:
            match = re.search(r"(?m)^## 复现索引[ \t]*$", document["content"])
            if match:
                ending = re.search(r"(?m)^#{1,2}\s", document["content"][match.end():])
                end = match.end() + ending.start() if ending else len(document["content"])
                removed = document["content"][match.start():end]
                document["content"] = (document["content"][:match.start()]
                                       + "## 复现索引\n机器路径和执行记录已在完整冻结来源及source_index保留。\n"
                                       + document["content"][end:])
                transformations.append({"source_ref": ref, "action": "machine_reproduction_index_to_index",
                                        "chars": len(removed), "sha256": hashlib.sha256(removed.encode()).hexdigest()})
        duplicate = remember_text(document["content"], f"documents[{document_number}].content")
        if duplicate:
            document.pop("content")
            document["content_visible_source"] = duplicate
            transformations.append({"source_ref": ref, "action": "deduplicate_exact_text", "visible_source": duplicate})
        visibility(document, "structured_projection" if "budget_projection" in document else "full")

    structured_sources: dict[str, str] = {}
    def share_structure(value: Any, path: str, material_id: str) -> Any:
        if not isinstance(value, (dict, list)):
            return value
        serialized = _json(value)
        if len(serialized) >= 700:
            digest = hashlib.sha256(serialized.encode()).hexdigest()
            if digest in structured_sources:
                transformations.append({"material_id": material_id, "action": "deduplicate_exact_structure",
                                        "path": path, "visible_source": structured_sources[digest], "sha256": digest})
                return {"_retrieval": "duplicate_visible_structure", "visible_source": structured_sources[digest], "sha256": digest}
            structured_sources[digest] = path
        if isinstance(value, dict):
            return {key: share_structure(item, path + "." + key, material_id) for key, item in value.items()}
        return [share_structure(item, path + f"[{number}]", material_id) for number, item in enumerate(value)]
    for document in bundle.get("documents", []):
        if not document.get("content") or not _file_name(document).casefold().endswith(".json"):
            continue
        try:
            value = json.loads(document["content"])
        except ValueError:
            continue
        previous = len(transformations)
        projected = share_structure(value, document["material_id"] + ".content:$", document["material_id"])
        if len(transformations) != previous:
            document["content"] = _json(projected)
            document["visibility"] = "structured_projection"
            for alias in (document.get("source_ref"), document.get("reference"), document.get("id")):
                if alias in visible:
                    visible[alias]["visibility"] = "structured_projection"

    canonical_materials = {document["source_ref"] for document in bundle.get("documents", [])}
    removed_aliases = []
    for ref, item in list(bundle["source_index"].items()):
        match = visible.get(ref) or visible.get(str(item.get("id") or ""))
        item.update(match or {"visibility": "full"})
        material_id = material_ids.get(ref) or material_ids.get(str(item.get("id") or ""))
        if material_id:
            if ref not in canonical_materials:
                removed_aliases.append(ref)
                bundle["source_index"].pop(ref)
                continue
            item["material_id"] = material_id
            for key in ("id", "sha256", "document_ref"):
                item.pop(key, None)
    for document in bundle.get("documents", []):
        # Repeated machine-local absolute paths and aliases are already in the
        # complete source_index.  Keep a short join key for the model view;
        # the archival snapshot retains every original path and hash.
        for key in ("source_path", "reference", "id", "frozen_path", "frozen_sha256"):
            document.pop(key, None)
        for key in ("retrieval_priority", "retrieval_relevance"):
            document.pop(key, None)
        if document["visibility"] == "index_only":
            document.pop("roles", None)
            document.pop("kind", None)
            if compact_file_index:
                # The immutable snapshot retains the duplicate file metadata.
                for key in ("sha256", "size", "status", "material_id"):
                    document.pop(key, None)
    if removed_aliases:
        transformations.append({"action": "canonicalize_redundant_file_aliases", "alias_count": len(removed_aliases),
                                "alias_set_sha256": _sha(sorted(removed_aliases)),
                                "reason": "all canonical file source_refs and all scientific H/Outcome/Evidence refs retained; original aliases remain in the full snapshot"})
    for transformation in transformations:
        material_id = material_ids.get(str(transformation.get("source_ref") or ""))
        if material_id:
            transformation["material_id"] = material_id
            transformation.pop("source_ref")
    for figure in bundle.get("figures", []):
        figure["visibility"] = "full" if figure.get("status") in {"available", "frozen", "ok"} else "index_only"
        for ref in (figure.get("source_ref"), figure.get("reference"), figure.get("id")):
            if ref in bundle["source_index"]:
                bundle["source_index"][ref]["visibility"] = figure["visibility"]
    bundle["derived"] = True
    bundle["retrieval"] = {
        "schema": BUNDLE_SCHEMA, "source_fingerprint": snapshot["fingerprint"],
        "full_snapshot_required_for_archival": True,
        "selected_refs": sorted(set(selected_refs)), "index_only_refs": sorted(set(index_only_refs)),
        "transformations": transformations, "warnings": warnings,
        "coverage": {"hypotheses": len(bundle.get("hypotheses", [])), "outcomes": len(bundle.get("outcomes", [])),
                     "preservations": len(bundle.get("preservations", [])), "evidence_records": len(bundle.get("evidence_records", [])),
                     "negative_outcomes": sum(row.get("verdict") == "rejected" for row in bundle.get("outcomes", [])),
                     "lineage_edges": len(bundle.get("lineage", [])), "lineage_hypotheses": len(lineage_nodes),
                     "all_scientific_records_retained": True, "all_round_reports_present_or_explicitly_unavailable": True},
        "max_chars": max_chars, "serialized_chars": 0,
        "serialization": "json.dumps(ensure_ascii=False,sort_keys=True,separators=(',',':'))",
        "boundary": "Index-only material is not read evidence. Projections contain only recorded values; array indices report counts and hashes, not calculated scientific statistics. Full sources remain frozen separately.",
    }
    if compact_file_index:
        retrieval = bundle["retrieval"]
        retrieval["selected_ref_count"] = len(retrieval.pop("selected_refs"))
        retrieval["index_only_ref_count"] = len(retrieval.pop("index_only_refs"))
        retrieval["supplemental_document_budget"] = "All file references and their visibility remain in source_index. Supplemental bodies indexed for space remain unmodified in the frozen snapshot."
    # Keep the measurement stable despite its own decimal digit count.
    for _ in range(4):
        size = len(_json(bundle))
        if size == bundle["retrieval"]["serialized_chars"]:
            break
        bundle["retrieval"]["serialized_chars"] = size
    return bundle


def _supplemental_excerpt(content: str) -> tuple[str, list[str]] | None:
    """Preserve exact, named top-level JSON fields that fit in a short excerpt."""
    try:
        value = json.loads(content)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    summary_key = re.compile(r"summary|result|metric|estimate|finding|conclu|diagnostic|check|limitation", re.I)
    fields = sorted(value, key=lambda key: (0 if summary_key.search(key) else 1, key))
    excerpt = {}
    remaining = 10_000
    for key in fields:
        item = value[key]
        if not summary_key.search(key) and isinstance(item, (dict, list)):
            continue
        cost = len(_json({key: item}))
        if cost <= remaining:
            excerpt[key] = item
            remaining -= cost
    if not excerpt or not any(summary_key.search(key) for key in excerpt):
        return None
    return _json(excerpt), sorted(set(value) - set(excerpt))


def _fit_supplemental_documents(snapshot: dict[str, Any], full_bundle: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Fit supplemental bodies before deduplication so visible pointers stay valid."""
    projected = {doc["source_ref"]: doc for doc in full_bundle.get("documents", [])}
    root = (snapshot.get("sources") or {}).get("research_root")
    closing_cycle = str((snapshot.get("goal_terminal") or {}).get("source_cycle") or "")
    closing_root = Path(root) / closing_cycle if root and closing_cycle else None
    core = []
    candidates = []
    for number, doc in enumerate(snapshot.get("documents", [])):
        view = projected[doc["source_ref"]]
        roles = set(doc.get("roles", ()))
        content = doc.get("content")
        if (roles & _CORE_DOCUMENT_ROLES or not isinstance(content, str)
                or view["visibility"] == "index_only"):
            core.append(doc)
            continue
        index = {key: value for key, value in doc.items() if key != "content"}
        index["retrieval_index"] = {"reason": "supplemental_body_over_budget", "content_chars": len(content)}
        core.append(index)
        selected = doc
        if len(content) > 16_000 and Path(_file_name(doc)).suffix.casefold() == ".json":
            excerpt = _supplemental_excerpt(content)
            if excerpt is not None:
                body, omitted = excerpt
                selected = {**doc, "content": body,
                            "budget_projection": {"reason": "selected_exact_top_level_fields",
                                                  "omitted_keys": omitted}}
        cost = len(_json(selected["content"])) + len(_json(selected.get("budget_projection", {})))
        if closing_root is not None and Path(_file_name(doc)).is_relative_to(closing_root):
            priority = 0
        elif roles & {"registered_evidence_artifact", "recorded_evidence_artifact", "registered_material"}:
            priority = 1
        else:
            priority = 2
        candidates.append((priority, cost, number, selected))

    minimal = _build_bundle({**snapshot, "documents": core}, max_chars, compact_file_index=True)
    base_size = minimal["retrieval"]["serialized_chars"]
    if base_size > max_chars:
        raise ValueError(f"Report retrieval bundle exceeds max_chars: {base_size}>{max_chars}; scientific records were retained, no silent truncation performed")
    if not candidates:
        return minimal

    remaining = max_chars - base_size
    for _ in range(2):
        documents = list(core)
        available = remaining
        for _, cost, number, doc in sorted(candidates, key=lambda row: row[:3]):
            if cost <= available:
                documents[number] = doc
                available -= cost
        fitted = _build_bundle({**snapshot, "documents": documents}, max_chars, compact_file_index=True)
        size = fitted["retrieval"]["serialized_chars"]
        if size <= max_chars:
            return fitted
        remaining = max(0, remaining - (size - max_chars) - 1000)
    return minimal
