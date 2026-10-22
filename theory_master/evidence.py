from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_provenance import normalize_provenance
from .specification import cycle_number


REGISTRY_SCHEMA = "theory-master/evidence-registry/v5"
LEGACY_REGISTRY_SCHEMA = "theory-master/evidence-registry/v1"
LIFECYCLE_SCHEMA = "theory-master/evidence-lifecycle/v1"
MANIFEST_SCHEMA = "theory-master/evidence-manifest/v4"
COVERAGE_SCHEMA = "theory-master/evidence-coverage/v1"
LEGACY_ASSESSMENT_SCHEMA = "theory-master/evidence-assessment/v1"
ASSESSMENT_SCHEMA = "theory-master/evidence-assessment/v2"
ASSESSMENT_BATCH_COMMIT_SCHEMA = "theory-master/evidence-assessment-batch-commit/v1"
EVIDENCE_BATCH_COMMIT_SCHEMA = "theory-master/evidence-registration-batch-commit/v1"
# Read-only fallback for historical Registry records with no producer role.
LEGACY_SCIENTIFIC_KINDS = frozenset({"theoretical", "numerical", "local_source", "literature"})
LIFECYCLE_ACTIONS = frozenset({"supersede", "retract", "restore", "integrity_drift"})
CONTRADICTORY_RELATIONS = frozenset({"contradicts", "invalidates"})
SCOPE_LIMITING_RELATIONS = frozenset({"limits_scope", "boundary_condition"})
EXTERNAL_POINTER = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
BARE_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _relation(item: dict[str, Any]) -> str:
    relation = item.get("relation") or item.get("relation_to_hypothesis") or "supports"
    return str(relation).strip().lower() or "supports"


def scientific_evidence_producer(record: dict[str, Any]) -> str | None:
    """Scientific provenance is SDK-owned producer identity, not a model-assigned type.

    Historical Registry rows without an invocation role retain a read-only
    fallback; current registrations always include SDK producer_invocation.role.
    """
    invocation = record.get("producer_invocation")
    invocation = invocation if isinstance(invocation, dict) else {}
    role = str(invocation.get("role") or record.get("producer_role") or "").strip()
    if role:
        return role if role in {"investigator", "theorist", "numerical", "literature"} else None
    # Old immutable records are allowed to keep their former classifications;
    # this path NEVER emits an evidence_kind in a new Evidence row.
    legacy_kind = str(record.get("evidence_kind") or "").strip()
    if legacy_kind == "literature":
        return "literature"
    return "historical_science" if legacy_kind in LEGACY_SCIENTIFIC_KINDS else None


def _is_external_pointer(value: str) -> bool:
    text = value.strip()
    return bool(EXTERNAL_POINTER.match(text) or BARE_DOI.match(text))


def _source_key(item: dict[str, Any]) -> str:
    """Stable source identity, independent of a model-provided display title."""
    for key in ("source_id", "doi_or_url", "doi", "url", "paper_title", "artifact_pointers", "artifacts"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def adjudicable_manifest_records(manifest: Any) -> list[dict[str, Any]]:
    """Return only Evidence explicitly authorized for scientific adjudication.

    The Manifest is an audit projection and may contain active records that are
    intentionally non-adjudicable.  Scientific consumers must use this boundary
    instead of treating lifecycle_status=active as sufficient authority.
    """
    if not isinstance(manifest, dict):
        return []
    payload = manifest.get("payload") if isinstance(manifest.get("payload"), dict) else manifest
    records = payload.get("records") if isinstance(payload, dict) else []
    return [
        record for record in (records or [])
        if isinstance(record, dict) and record.get("adjudicable") is True
    ]


class EvidenceRegistry:
    """Append-only evidence store and deterministic projections for one Cycle."""

    def __init__(self, cycle_dir: Path, workspace: Path, *, task_local_root: Path | None = None,
                 repo_root: Path | None = None, research_root: Path | None = None,
                 legacy_research_root: Path | None = None):
        self.cycle_dir = cycle_dir
        self.workspace = workspace
        self.evidence_dir = cycle_dir / "evidence"
        self.records_path = self.evidence_dir / "registry.jsonl"
        self.registration_batches_path = self.evidence_dir / "registration_batches.jsonl"
        self.lifecycle_path = self.evidence_dir / "lifecycle.jsonl"
        self.assessments_path = self.evidence_dir / "assessments.jsonl"
        self.criteria_path = self.evidence_dir / "criteria.json"
        self.manifest_path = workspace / "evidence_manifest.json"
        self.coverage_path = workspace / "coverage_matrix.json"
        self.last_registration_audit: list[dict[str, Any]] = []
        self.lineage_roots: list[Path] = []
        for root in (research_root, legacy_research_root):
            if root is None:
                continue
            resolved = Path(root).expanduser().resolve()
            if resolved not in self.lineage_roots:
                self.lineage_roots.append(resolved)
        self.artifact_anchors: list[tuple[str, Path]] = []
        for namespace, root in (
            ("cycle_workspace", workspace),
            ("task_local", task_local_root),
            ("research_root", research_root),
            ("legacy_research_root", legacy_research_root),
            ("repo", repo_root),
        ):
            if root is None:
                continue
            resolved = Path(root).expanduser().resolve()
            if all(resolved != existing for _, existing in self.artifact_anchors):
                self.artifact_anchors.append((namespace, resolved))

    @staticmethod
    def _cycle_identity(name: str) -> int | str:
        number = cycle_number(str(name or ""))
        return number if number is not None else str(name or "")

    @staticmethod
    def _declared_lineage_refs(item: dict[str, Any], *, legacy_read: bool = False) -> list[str]:
        refs: list[str] = []
        for key in ("artifact_pointers", "artifacts", "local_original_path"):
            refs.extend(_strings(item.get(key)))
        # Only interpret the obsolete source_refs when reading a pre-v8
        # immutable Registry row. Do not ask for or emit it in new science.
        if legacy_read and item.get("source_refs"):
            refs.extend(_strings(item["source_refs"]))
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        if provenance.get("declaration_ref"):
            refs.extend(_strings(provenance.get("declaration_ref")))
        return list(dict.fromkeys(refs))

    def _cycle_for_local_ref(self, ref: str) -> int | str | None:
        text = str(ref or "").strip()
        if not text or _is_external_pointer(text):
            return None
        source = Path(text).expanduser()
        candidates = [source.resolve()] if source.is_absolute() else [
            (root / source).resolve() for root in self.lineage_roots
        ]
        for candidate in candidates:
            for root in self.lineage_roots:
                try:
                    relative = candidate.relative_to(root)
                except ValueError:
                    continue
                if not relative.parts:
                    continue
                number = cycle_number(relative.parts[0])
                if number is not None:
                    return number
                first = str(relative.parts[0]).strip()
                if first:
                    return first
        return None

    def _lineage_metadata(self, items: list[dict[str, Any]], *, legacy_read: bool = False) -> dict[str, Any]:
        """Infer asset origin from artifact paths, not agent-authored source refs."""
        analysis_cycle = self._cycle_identity(self.cycle_dir.name)
        historical: set[int | str] = set()
        has_current_asset_ref = False
        for item in items:
            for ref in self._declared_lineage_refs(item, legacy_read=legacy_read):
                origin = self._cycle_for_local_ref(ref)
                if origin == analysis_cycle:
                    has_current_asset_ref = True
                elif origin is not None:
                    historical.add(origin)
        if not historical:
            # Absence of an old-cycle path is NOT evidence of a fresh, independent
            # source. Without an inspectable SDK asset reference, report unknown
            # origin; Judge must determine scientific independence from the claim.
            return ({"origin_cycle": analysis_cycle,
                     "analysis_cycle": analysis_cycle, "freshness": "current_cycle"}
                    if has_current_asset_ref else
                    {"analysis_cycle": analysis_cycle, "freshness": "origin_unverified"})
        origins = sorted(historical, key=lambda value: (not isinstance(value, int), str(value)))
        lineage: dict[str, Any] = {
            "origin_cycle": origins[0],
            "analysis_cycle": analysis_cycle,
            "freshness": "historical_reanalysis",
        }
        if len(origins) > 1:
            lineage["origin_cycles"] = origins
        return lineage

    def _committed_evidence_records(self) -> list[dict[str, Any]]:
        """Return only logically committed Evidence rows.

        Historical rows predate registration batches and remain implicitly
        committed. New rows carrying evidence_batch_id are visible only after
        their batch_committed marker is durable.
        """
        committed_batches = {
            str(item.get("evidence_batch_id"))
            for item in _jsonl(self.registration_batches_path)
            if item.get("schema") == EVIDENCE_BATCH_COMMIT_SCHEMA
            and item.get("event") == "batch_committed"
            and item.get("evidence_batch_id")
        }
        return [
            record for record in _jsonl(self.records_path)
            if record.get("evidence_id")
            and (
                not str(record.get("evidence_batch_id") or "")
                or str(record.get("evidence_batch_id")) in committed_batches
            )
        ]

    @staticmethod
    def _canonical_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
        """Project the legacy H key as a foreign-key reference without rewriting history."""
        normalized = dict(record)
        if not normalized.get("hypothesis_ref") and normalized.get("hypothesis_id"):
            normalized["hypothesis_ref"] = normalized.get("hypothesis_id")
        normalized.pop("hypothesis_id", None)
        return normalized

    def _provenance_declaration_matches(self, ref: str) -> list[str]:
        source = Path(str(ref or "")).expanduser()
        if not str(ref or "").strip():
            return []
        if source.is_absolute():
            try:
                resolved = source.resolve()
            except OSError:
                return []
            return [str(resolved)] if resolved.is_file() else []

        roots: list[Path] = []
        for _namespace, root in getattr(self, "artifact_anchors", []):
            roots.append(Path(root))
        roots.extend([self.workspace, self.cycle_dir.parent])
        matches: list[str] = []
        for root in roots:
            try:
                candidate = (Path(root) / source).resolve()
            except OSError:
                continue
            if candidate.is_file() and str(candidate) not in matches:
                matches.append(str(candidate))
        return matches

    def _canonical_artifact_pointers(
        self, value: Any,
    ) -> tuple[list[str], list[dict[str, str]], dict[str, str]]:
        canonical: list[str] = []
        rejected: list[dict[str, str]] = []
        resolved_by_original: dict[str, str] = {}
        for pointer in _strings(value):
            if _is_external_pointer(pointer):
                canonical.append(pointer)
                resolved_by_original[pointer] = pointer
                continue
            try:
                source = Path(pointer).expanduser()
                matches: list[Path] = []
                attempted: list[str] = []
                if source.is_absolute():
                    resolved = source.resolve()
                    attempted.append(str(resolved))
                    if resolved.exists():
                        matches.append(resolved)
                else:
                    for _namespace, anchor in self.artifact_anchors:
                        candidate = (anchor / source).resolve()
                        attempted.append(str(candidate))
                        if candidate.exists() and candidate not in matches:
                            matches.append(candidate)
                if len(matches) == 1:
                    canonical.append(str(matches[0]))
                    resolved_by_original[pointer] = str(matches[0])
                    continue
                if len(matches) > 1:
                    reason = "ambiguous local artifact pointer; matches: " + ", ".join(map(str, matches))
                else:
                    # Recover only a malformed relative path whose basename names
                    # exactly one file inside this Cycle's workspace.
                    recovered: list[Path] = []
                    if not source.is_absolute() and source.name:
                        try:
                            recovered = [path.resolve() for path in self.workspace.rglob(source.name)
                                         if path.is_file()]
                        except OSError:
                            recovered = []
                    if len(recovered) == 1:
                        path = recovered[0]
                        try:
                            path.relative_to(self.workspace.resolve())
                        except ValueError:
                            pass
                        else:
                            canonical.append(str(path))
                            resolved_by_original[pointer] = str(path)
                            self.last_registration_audit.append({
                                "pointer": pointer, "status": "corrected_unique_workspace_basename",
                                "resolved_path": str(path),
                            })
                            continue
                    tried = ", ".join(attempted) or "no artifact anchors configured"
                    reason = f"local artifact pointer does not exist; tried: {tried}"
            except (OSError, RuntimeError, ValueError) as exc:
                reason = f"local artifact pointer cannot be resolved: {exc}"
            rejected_item = {"pointer": pointer, "reason": reason}
            rejected.append(rejected_item)
            self.last_registration_audit.append({"status": "rejected", **rejected_item})
        return canonical, rejected, resolved_by_original

    @staticmethod
    def _verified_source_access(declared: str, source_id: str, transport: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        """Bind Literature access level to SDK retrieval/reading artifacts.

        The agent may describe what it used, but the persisted access level is
        determined from the retrieval corpus and (for full text) its reading
        manifest.  Old cycles without a transport remain explicitly unverified.
        """
        evidence = {"declared": declared, "verified": "unavailable", "verification_status": "unverified",
                    "source_id": source_id}
        papers_path = Path(str(transport.get("papers_json") or ""))
        papers: list[dict[str, Any]] = []
        if papers_path.is_file():
            try:
                loaded = json.loads(papers_path.read_text(encoding="utf-8"))
                papers = [row for row in loaded if isinstance(row, dict)] if isinstance(loaded, list) else []
            except (OSError, ValueError):
                pass
        wanted = source_id.casefold()
        paper = next((row for row in papers if wanted in {
            str(row.get(key) or "").strip().casefold() for key in ("id", "doi", "url", "title")
        }), None)
        if paper is None:
            evidence["reason"] = "source_id is absent from SDK retrieval record"
            return evidence
        evidence["retrieval_ref"] = {"papers_json": str(papers_path), "source_id": source_id}
        if declared == "unavailable":
            evidence.update({"verified": "unavailable", "verification_status": "verified"})
            return evidence
        if declared == "metadata":
            evidence.update({"verified": "metadata", "verification_status": "verified"})
            return evidence
        if declared == "abstract":
            if str(paper.get("abstract") or "").strip():
                evidence.update({"verified": "abstract", "verification_status": "verified"})
            else:
                evidence["reason"] = "SDK retrieval record has no abstract"
            return evidence
        reading = transport.get("reading") if isinstance(transport.get("reading"), dict) else {}
        manifest_path = Path(str(reading.get("pdf_manifest") or ""))
        if manifest_path.is_file():
            try:
                with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                target_values = {str(paper.get(key) or "").strip().casefold() for key in ("doi", "title", "arxiv_id")}
                match = next((row for row in rows if target_values & {
                    str(row.get(key) or "").strip().casefold() for key in ("doi", "title", "arxiv_id", "key")
                } and str(row.get("status") or "") == "read"), None)
                if match:
                    evidence.update({
                        "verified": "full_text", "verification_status": "verified",
                        "reading_ref": {"pdf_manifest": str(manifest_path), "key": str(match.get("key") or source_id),
                                        "markdown_path": str(match.get("markdown_path") or "")},
                    })
                    return evidence
            except (OSError, csv.Error):
                pass
        evidence["reason"] = "no SDK reading record establishes full-text access"
        return evidence

    def register_role_output(self, role: str, output: dict[str, Any], *, epoch: str,
                             producer_invocation: dict[str, Any],
                             hypothesis_ref: str = "",
                             default_criterion_ids: list[str] | None = None,
                             known_criterion_ids: list[str] | None = None,
                             default_issue_ids: list[str] | None = None,
                             default_scope: str = "",
                             source_transport: dict[str, Any] | None = None) -> list[str]:
        self.last_registration_audit = []
        invocation_role = str(producer_invocation.get("role") or "").strip()
        if invocation_role and invocation_role != role:
            raise ValueError("producer_invocation.role must match the registering role")
        producer_invocation = {**producer_invocation, "role": role}
        existing = {str(record.get("evidence_id")) for record in self._committed_evidence_records()}
        if known_criterion_ids:
            catalog: dict[str, Any] = {"criterion_ids": []}
            if self.criteria_path.is_file():
                try:
                    loaded = json.loads(self.criteria_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        catalog = loaded
                except (OSError, ValueError):
                    pass
            catalog["criterion_ids"] = _strings([*(catalog.get("criterion_ids") or []), *known_criterion_ids])
            _atomic_json(self.criteria_path, catalog)
        created: list[str] = []
        pending_records: list[dict[str, Any]] = []
        criterion_refs: dict[str, list[str]] = {}
        updates = output.get("criterion_updates") if isinstance(output.get("criterion_updates"), dict) else {}
        for criterion_id, update in updates.items():
            if not isinstance(update, dict):
                continue
            for evidence_id in _strings(update.get("evidence_refs")):
                criterion_refs.setdefault(evidence_id, []).append(str(criterion_id))
        sources = (("evidence", output.get("evidence")), ("claims", output.get("claims")),
                   ("evidence_records", output.get("evidence_records")))
        batch_items: list[dict[str, Any]] = []
        for _source, raw_values in sources:
            if raw_values is None:
                continue
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            batch_items.extend(dict(raw) if isinstance(raw, dict) else {"summary": str(raw)} for raw in values)
        # Scientific-turn lineage is conservative: if one finding explicitly
        # reuses a historical asset, sibling findings from the same turn inherit
        # that origin unless they carry an independent lineage in a later turn.
        batch_lineage = self._lineage_metadata(batch_items)
        for source, raw_values in sources:
            if raw_values is None:
                continue
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            for index, raw in enumerate(values):
                item = dict(raw) if isinstance(raw, dict) else {"summary": str(raw)}
                body = item.get("body") or item.get("scientific_insight") or item.get("claim") or item.get("summary") or raw
                canonical = {
                    "cycle_id": self.cycle_dir.name,
                    "hypothesis_ref": str(hypothesis_ref or "").strip(),
                    "source": source, "source_index": index, "body": body,
                    "producer_invocation": producer_invocation,
                    "lineage": batch_lineage,
                }
                digest = hashlib.sha256(json.dumps(
                    canonical, ensure_ascii=False, sort_keys=True, default=str
                ).encode()).hexdigest()[:16]
                evidence_id = str(item.get("evidence_id") or f"E-{self.cycle_dir.name}-{digest}")
                if not item.get("evidence_id"):
                    # Preserve idempotency for an interrupted legacy Cycle whose
                    # deterministic Evidence ID used the old serialized key name.
                    # This object is a digest probe only; it is never persisted as a new record.
                    legacy_canonical = {
                        **canonical,
                        "hypothesis_id": canonical["hypothesis_ref"],
                    }
                    legacy_digest = hashlib.sha256(json.dumps(
                        legacy_canonical, ensure_ascii=False, sort_keys=True, default=str
                    ).encode()).hexdigest()[:16]
                    legacy_evidence_id = f"E-{self.cycle_dir.name}-{legacy_digest}"
                    if legacy_evidence_id in existing:
                        evidence_id = legacy_evidence_id
                    if evidence_id not in existing and item.get("evidence_kind"):
                        # Read-only digest probes for v7/v6 invocation replay.
                        # Old Evidence IDs included a model-authored kind and
                        # assumed current-cycle asset origin when no path was
                        # declared. These probes never create new metadata.
                        old_lineage = self._lineage_metadata(batch_items, legacy_read=True)
                        if old_lineage.get("freshness") == "origin_unverified":
                            old_lineage = {
                                "origin_cycle": self._cycle_identity(self.cycle_dir.name),
                                "analysis_cycle": self._cycle_identity(self.cycle_dir.name),
                                "freshness": "current_cycle",
                            }
                        old_canonical = {
                            **canonical,
                            "evidence_kind": str(item["evidence_kind"]),
                            "lineage": old_lineage,
                        }
                        for candidate in (old_canonical, {
                            **old_canonical, "hypothesis_id": canonical["hypothesis_ref"],
                        }):
                            old_digest = hashlib.sha256(json.dumps(
                                candidate, ensure_ascii=False, sort_keys=True, default=str
                            ).encode()).hexdigest()[:16]
                            old_id = f"E-{self.cycle_dir.name}-{old_digest}"
                            if old_id in existing:
                                evidence_id = old_id
                                break
                if evidence_id in existing:
                    created.append(evidence_id)
                    continue
                artifact_pointers = item.get("artifact_pointers") or item.get("artifacts") or []
                if source == "evidence_records" and (item.get("doi_or_url") or item.get("local_original_path")):
                    artifact_pointers = [pointer for pointer in (item.get("doi_or_url"), item.get("local_original_path")) if pointer]
                submitted_artifact_pointers = _strings(artifact_pointers)
                artifact_pointers, rejected_artifact_pointers, resolved_by_original = self._canonical_artifact_pointers(artifact_pointers)
                provenance = None
                if role == "investigator":
                    # Validate every Investigator claim, including theoretical:
                    # removing a model-authored type cannot bypass protected identity.
                    provenance = normalize_provenance(
                        item,
                        workspace=self.workspace,
                        resolve_local_ref=self._provenance_declaration_matches,
                    )
                if submitted_artifact_pointers and not artifact_pointers:
                    self.last_registration_audit.append({
                        "status": "all_artifacts_rejected",
                        "evidence_id": evidence_id,
                        "rejected_count": len(rejected_artifact_pointers),
                    })
                    # Keep the output-to-ref positional binding stable. The
                    # resumable protocol violation prevents this placeholder
                    # ref from reaching a Judge or a committed scientific state.
                    created.append(evidence_id)
                    continue
                local_artifacts = [Path(pointer) for pointer in artifact_pointers if not _is_external_pointer(pointer)]
                artifact_hashes = {
                    str(path): _file_sha256(path)
                    for path in local_artifacts if path.is_file()
                }
                integrity = {
                    "artifact_paths_exist": not rejected_artifact_pointers,
                    "artifact_hashes_recorded": len(artifact_hashes) == len(local_artifacts),
                    "artifact_roles_valid": True,
                }
                record = {
                    "schema": REGISTRY_SCHEMA,
                    "evidence_id": evidence_id,
                    "created_at": _now(),
                    # Stable scientific ownership is SDK-injected. The Agent never
                    # authors either identity, and criterion IDs remain Cycle-local.
                    "cycle_id": self.cycle_dir.name,
                    "hypothesis_ref": str(hypothesis_ref or "").strip(),
                    "epoch": str(item.get("epoch") or epoch),
                    # Judge owns science; Investigator evidence has no inferred relation.
                    "relation": None if role == "investigator" else _relation(item),
                    "criterion_ids": _strings([
                        *_strings(item.get("criterion_ids") or item.get("criteria")),
                        *criterion_refs.get(evidence_id, []),
                        *_strings(default_criterion_ids),
                    ]),
                    "objection_ids": _strings(item.get("objection_ids")),
                    "issue_ids": _strings(item.get("issue_ids") or item.get("critical_issue_ids") or default_issue_ids),
                    "scope": (item.get("scope") if role == "investigator"
                              else item.get("scope") or default_scope),
                    "artifact_pointers": artifact_pointers if isinstance(artifact_pointers, list) else [artifact_pointers],
                    "producer_invocation": producer_invocation,
                    **batch_lineage,
                    # source is an output coordinate, not a second producer identity.
                    "source": {"field": source, "index": index},
                    "summary": str(item.get("summary") or item.get("scientific_insight") or item.get("claim") or body),
                    "body": body,
                    "integrity": integrity,
                }
                if artifact_hashes:
                    record["integrity"]["artifact_hashes"] = artifact_hashes
                if role == "literature":
                    source_access = str(item.get("source_access") or "unavailable").strip()
                    if source_access not in {"metadata", "abstract", "full_text", "unavailable"}:
                        raise ValueError(f"invalid literature source_access: {source_access}")
                    # Preserve malformed/legacy Literature output for audit,
                    # but make the absence explicit and keep it non-adjudicable
                    # until a real SDK source identity is supplied.
                    source_id = _source_key(item) or "unresolved"
                    record["literature_source"] = {
                        key: item.get(key) for key in ("paper_title", "authors", "year", "doi_or_url", "local_original_path")
                        if item.get(key) not in (None, "", [], {})
                    }
                    record["literature_source"]["source_id"] = source_id
                    for key in ("exact_quote", "locator", "interpretation"):
                        if item.get(key) not in (None, "", [], {}):
                            record[key] = item[key]
                    record["source_access"] = self._verified_source_access(
                        source_access, source_id, source_transport or {}, item,
                    )
                if item.get("limitations") not in (None, "", [], {}):
                    record["limitations"] = str(item.get("limitations"))
                if role == "investigator":
                    for key in ("method", "result"):
                        if key in item:
                            record[key] = item[key]
                if provenance is not None:
                    record["provenance"] = provenance
                if rejected_artifact_pointers:
                    record["rejected_artifact_pointers"] = rejected_artifact_pointers
                artifact_roles = item.get("artifact_roles")
                if isinstance(artifact_roles, list):
                    canonical_roles = []
                    for artifact_role in artifact_roles:
                        if not isinstance(artifact_role, dict):
                            continue
                        original_path = str(artifact_role.get("path") or "").strip()
                        artifact_purpose = str(artifact_role.get("role") or "").strip()
                        canonical_path = resolved_by_original.get(original_path)
                        if canonical_path and artifact_purpose in {"implementation", "result", "input", "documentation", "other"}:
                            canonical_roles.append({"path": canonical_path, "role": artifact_purpose})
                        else:
                            integrity["artifact_roles_valid"] = False
                    if canonical_roles:
                        record["artifact_roles"] = canonical_roles
                pending_records.append(record)
                existing.add(evidence_id)
                created.append(evidence_id)
        # A protocol-invalid scientific turn must not publish a subset of its
        # otherwise-valid findings. Keep positional refs for the caller audit,
        # but leave the Registry unchanged so the invocation can be quarantined.
        if any(item.get("status") == "all_artifacts_rejected" for item in self.last_registration_audit):
            self.rebuild()
            return created

        if pending_records:
            batch_payload = [
                {key: value for key, value in record.items() if key != "created_at"}
                for record in pending_records
            ]
            batch_hash = hashlib.sha256(
                json.dumps(batch_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            invocation_id = str(producer_invocation.get("invocation_id") or "").strip()
            batch_id = "EB-" + hashlib.sha256(f"{invocation_id}:{batch_hash}".encode()).hexdigest()[:16]
            evidence_ids = [str(record["evidence_id"]) for record in pending_records]
            expected_records = [
                {**record, "evidence_batch_id": batch_id, "evidence_batch_hash": batch_hash}
                for record in pending_records
            ]

            batch_ledger = _jsonl(self.registration_batches_path)
            committed = next((
                item for item in batch_ledger
                if item.get("schema") == EVIDENCE_BATCH_COMMIT_SCHEMA
                and item.get("event") == "batch_committed"
                and str(item.get("evidence_batch_id") or "") == batch_id
            ), None)
            if committed is not None:
                if list(committed.get("evidence_ids") or []) != evidence_ids or str(committed.get("evidence_batch_hash") or "") != batch_hash:
                    raise ValueError(f"conflicting committed Evidence registration retry: {batch_id}")
                self.rebuild()
                return created

            persisted_batch_rows = [
                item for item in _jsonl(self.records_path)
                if str(item.get("evidence_batch_id") or "") == batch_id and item.get("evidence_id")
            ]
            expected_by_id = {str(item["evidence_id"]): item for item in expected_records}
            for persisted in persisted_batch_rows:
                evidence_id = str(persisted.get("evidence_id") or "")
                expected = expected_by_id.get(evidence_id)
                if expected is None:
                    raise ValueError(f"conflicting persisted Evidence registration retry: {evidence_id}")
                persisted_cmp = {key: value for key, value in persisted.items() if key != "created_at"}
                expected_cmp = {key: value for key, value in expected.items() if key != "created_at"}
                if persisted_cmp != expected_cmp:
                    raise ValueError(f"conflicting persisted Evidence registration retry: {evidence_id}")

            persisted_ids = {str(item.get("evidence_id")) for item in persisted_batch_rows}
            for record in expected_records:
                if str(record["evidence_id"]) not in persisted_ids:
                    _append(self.records_path, record)
            _append(self.registration_batches_path, {
                "schema": EVIDENCE_BATCH_COMMIT_SCHEMA,
                "event": "batch_committed",
                "evidence_batch_id": batch_id,
                "evidence_batch_hash": batch_hash,
                "evidence_ids": evidence_ids,
                "producer_invocation": dict(producer_invocation),
                "committed_at": _now(),
            })
        self.rebuild()
        return created

    def append_judge_assessments(self, assessments: list[dict[str, Any]], *, judge_invocation: dict[str, Any]) -> list[str]:
        """Historical API retained only to fail closed.

        Current Judge output is a branch verdict plus at most one scientific
        objection.  The legacy per-Evidence assessment ledger is read-only and
        is never extended by current runtime code.
        """
        raise RuntimeError("legacy Judge assessment ledger is read-only")

    def append_lifecycle(self, action: str, evidence_id: str, *, reason: str,
                         superseding_evidence_id: str | None = None,
                         producer_invocation: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in LIFECYCLE_ACTIONS:
            raise ValueError(f"invalid evidence lifecycle action: {action}")
        known = {str(record.get("evidence_id")) for record in self._committed_evidence_records()}
        if evidence_id not in known:
            raise ValueError(f"unknown evidence_id: {evidence_id}")
        if action == "supersede" and (not superseding_evidence_id or superseding_evidence_id not in known):
            raise ValueError("supersede requires a registered superseding_evidence_id")
        event = {
            "schema": LIFECYCLE_SCHEMA,
            "event_id": f"EV-{hashlib.sha256(f'{action}:{evidence_id}:{superseding_evidence_id}:{reason}'.encode()).hexdigest()[:16]}",
            "at": _now(), "action": action, "evidence_id": evidence_id, "reason": reason,
            "superseding_evidence_id": superseding_evidence_id,
            "producer_invocation": producer_invocation or {},
        }
        prior_ids = {str(item.get("event_id")) for item in _jsonl(self.lifecycle_path)}
        if event["event_id"] not in prior_ids:
            _append(self.lifecycle_path, event)
        self.rebuild()
        return event

    @staticmethod
    def _project_provenance(value: Any) -> dict[str, Any] | None:
        """Normalize legacy path-only 'frozen' claims without editing history."""
        if not isinstance(value, dict):
            return None
        projected = dict(value)
        if projected.get("source") in {"benchmark_provided", "pre_specified_synthetic"}:
            projected.pop("frozen_before_current_cycle", None)
            projected["freeze_status"] = (
                "legacy_unverified" if "freeze_status" not in value else "unverified"
            )
            projected["protected_identity_verified"] = False
        return projected

    @staticmethod
    def _current_integrity(record: dict[str, Any]) -> dict[str, Any]:
        """Verify local artifacts without changing the append-only Evidence row."""
        registered = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
        expected = registered.get("artifact_hashes") if isinstance(registered.get("artifact_hashes"), dict) else {}
        checked: dict[str, str] = {}
        missing: list[str] = []
        changed: list[str] = []
        for pointer in record.get("artifact_pointers") or []:
            text = str(pointer)
            if _is_external_pointer(text):
                continue
            path = Path(text)
            if not path.is_file():
                missing.append(text)
                continue
            digest = _file_sha256(path)
            checked[text] = digest
            if expected.get(text) != digest:
                changed.append(text)
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        declaration_ref = str(provenance.get("declaration_ref") or "")
        declaration_sha = str(provenance.get("declaration_sha256") or "")
        # Legacy records have no pinned declaration hash: retain the original
        # Evidence as science, but their claimed protected identity is unverified.
        if declaration_ref and declaration_sha:
            declaration = Path(declaration_ref)
            if not declaration.is_file():
                missing.append(declaration_ref)
            else:
                try:
                    digest = _file_sha256(declaration)
                except OSError:
                    missing.append(declaration_ref)
                else:
                    checked[declaration_ref] = digest
                    if digest != declaration_sha:
                        changed.append(declaration_ref)
        status = "verified" if not missing and not changed else "drifted"
        return {"status": status, "checked_hashes": checked, "missing_paths": missing, "changed_paths": changed}

    @staticmethod
    def _is_adjudicable(record: dict[str, Any]) -> bool:
        if record.get("lifecycle_status") != "active":
            return False
        if (record.get("integrity_current") or {}).get("status") != "verified":
            return False
        producer = scientific_evidence_producer(record)
        if producer is None:
            return False
        if producer != "literature":
            return True
        access = record.get("source_access")
        return (
            isinstance(access, dict)
            and access.get("verification_status") == "verified"
            and access.get("verified") in {"metadata", "abstract", "full_text"}
        )

    def rebuild(self) -> dict[str, Any]:
        records = [
            self._canonical_evidence_record(record)
            for record in self._committed_evidence_records()
        ]
        events = _jsonl(self.lifecycle_path)
        assessment_ledger = _jsonl(self.assessments_path)
        committed_batches = {
            str(item.get("assessment_batch_id")) for item in assessment_ledger
            if item.get("schema") == ASSESSMENT_BATCH_COMMIT_SCHEMA
            and item.get("event") == "batch_committed"
        }
        assessments = [
            item for item in assessment_ledger
            if item.get("assessment_id")
            and (
                # Historical v1 rows predate batch commits and remain readable
                # as implicitly committed compatibility records.
                item.get("schema") == LEGACY_ASSESSMENT_SCHEMA
                or (
                    item.get("schema") == ASSESSMENT_SCHEMA
                    and str(item.get("assessment_batch_id") or "") in committed_batches
                )
            )
        ]
        latest_assessments: dict[str, dict[str, Any]] = {}
        for assessment in assessments:
            evidence_id = str(assessment.get("evidence_id") or "")
            if evidence_id:
                latest_assessments[evidence_id] = assessment
        status = {str(record.get("evidence_id")): "active" for record in records}
        lineage_groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            invocation = record.get("producer_invocation") if isinstance(record.get("producer_invocation"), dict) else {}
            group_key = (
                str(record.get("evidence_batch_id") or "").strip()
                or str(invocation.get("invocation_id") or "").strip()
                or str(record.get("evidence_id") or "")
            )
            lineage_groups.setdefault(group_key, []).append(record)
        lineage_projection = {
            key: self._lineage_metadata(group, legacy_read=True)
            for key, group in lineage_groups.items()
        }
        for event in events:
            target = str(event.get("evidence_id") or "")
            if target not in status:
                continue
            action = event.get("action")
            status[target] = {"supersede": "superseded", "retract": "retracted", "restore": "active",
                              "integrity_drift": "integrity_drifted"}.get(str(action), status[target])
        # Drift is a lifecycle fact, not a silent change to an immutable
        # registration snapshot.  Emit it once when an otherwise-active local
        # artifact no longer hashes to the recorded value.
        prior_event_ids = {str(event.get("event_id")) for event in events}
        for record in records:
            evidence_id = str(record.get("evidence_id") or "")
            if not evidence_id or status.get(evidence_id) != "active":
                continue
            integrity = self._current_integrity(record)
            if integrity["status"] == "verified":
                continue
            reason = "local artifact integrity drift: " + json.dumps(
                {"missing": integrity["missing_paths"], "changed": integrity["changed_paths"]}, sort_keys=True,
            )
            event_id = "EV-" + hashlib.sha256(f"integrity_drift:{evidence_id}:{reason}".encode()).hexdigest()[:16]
            if event_id not in prior_event_ids:
                event = {"schema": LIFECYCLE_SCHEMA, "event_id": event_id, "at": _now(),
                         "action": "integrity_drift", "evidence_id": evidence_id, "reason": reason,
                         "superseding_evidence_id": None, "producer_invocation": {"kind": "sdk", "role": "registry"}}
                _append(self.lifecycle_path, event)
                events.append(event)
                prior_event_ids.add(event_id)
            status[evidence_id] = "integrity_drifted"
        projected = []
        for record in records:
            evidence_id = str(record.get("evidence_id"))
            current_integrity = self._current_integrity(record)
            row = {**record, "lifecycle_status": status.get(evidence_id, "active"),
                   "integrity_current": current_integrity}
            if "provenance" in row:
                row["provenance"] = self._project_provenance(row["provenance"])
            invocation = record.get("producer_invocation") if isinstance(record.get("producer_invocation"), dict) else {}
            lineage_key = (
                str(record.get("evidence_batch_id") or "").strip()
                or str(invocation.get("invocation_id") or "").strip()
                or evidence_id
            )
            for key, value in lineage_projection.get(lineage_key, {}).items():
                row.setdefault(key, value)
            # v2 records carried a redundant producer_role.  New records use
            # producer_invocation.role exclusively; this projection preserves a
            # safe read fallback for older append-only files without rewriting
            # their historical bytes.
            invocation = row.get("producer_invocation") if isinstance(row.get("producer_invocation"), dict) else {}
            if not str(invocation.get("role") or "").strip() and row.get("producer_role"):
                row["producer_invocation"] = {**invocation, "role": str(row["producer_role"])}
            if evidence_id in latest_assessments:
                row["judge_assessment"] = latest_assessments[evidence_id]
                if current_integrity["status"] != "verified":
                    row["judge_assessment_stale"] = True
            row["adjudicable"] = self._is_adjudicable(row)
            projected.append(row)
        manifest = {
            "schema": MANIFEST_SCHEMA, "cycle_id": self.cycle_dir.name,
            "records_path": str(self.records_path), "registration_batches_path": str(self.registration_batches_path),
            "lifecycle_path": str(self.lifecycle_path), "assessments_path": str(self.assessments_path), "records": projected,
            "lifecycle_events": events, "judge_assessments": assessments,
        }
        known_criteria: list[str] = []
        if self.criteria_path.is_file():
            try:
                catalog = json.loads(self.criteria_path.read_text(encoding="utf-8"))
                if isinstance(catalog, dict):
                    known_criteria = _strings(catalog.get("criterion_ids"))
            except (OSError, ValueError):
                pass
        criteria: dict[str, list[str]] = {criterion_id: [] for criterion_id in known_criteria}
        issues: dict[str, list[str]] = {}
        contradictions: list[str] = []
        scope_limitations: list[str] = []
        adjudicable_records = [record for record in projected if record["adjudicable"]]
        for record in adjudicable_records:
            evidence_id = str(record["evidence_id"])
            for criterion in record.get("criterion_ids") or []:
                criteria.setdefault(str(criterion), []).append(evidence_id)
            for issue in record.get("issue_ids") or []:
                issues.setdefault(str(issue), []).append(evidence_id)
            if record.get("relation") in CONTRADICTORY_RELATIONS:
                contradictions.append(evidence_id)
            if record.get("relation") in SCOPE_LIMITING_RELATIONS:
                scope_limitations.append(evidence_id)
        coverage = {
            "schema": COVERAGE_SCHEMA, "cycle_id": self.cycle_dir.name,
            "criteria": criteria, "issues": issues,
            "contradictions": contradictions,
            "scope_limitations": scope_limitations,
            "active_evidence_ids": [str(record["evidence_id"]) for record in adjudicable_records],
        }
        _atomic_json(self.manifest_path, manifest)
        _atomic_json(self.coverage_path, coverage)
        return {"manifest": manifest, "coverage": coverage}

    def context(self) -> dict[str, Any]:
        projections = self.rebuild()
        return {
            "evidence_manifest": {"path": str(self.manifest_path), "payload": projections["manifest"]},
            "coverage_matrix": {"path": str(self.coverage_path), "payload": projections["coverage"]},
        }

    def evidence_ids_by_producer(self, role: str, *, active_only: bool = False) -> list[str]:
        """Return Evidence IDs from the sole current producer identity field."""
        records = self.rebuild()["manifest"]["records"]
        return [str(record["evidence_id"]) for record in records
                if isinstance(record.get("producer_invocation"), dict)
                and record["producer_invocation"].get("role") == role
                and (not active_only or record.get("lifecycle_status") == "active")]
