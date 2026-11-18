"""Read-only delivery material identity shared by reports and Goal completion.

A batch binding says only that files were captured during the same completed
invocation. It does not assert that every file supports every Finding, or that
newly written outputs constitute new or independent scientific inputs.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit

from .invocation_artifacts import MANIFEST_SCHEMA, is_delivery_file

INDEX_SCHEMA = "theory-master/delivery-material-index/v1"
EXECUTION_RELATION = "same_invocation"
_FIGURES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_CYCLE = re.compile(r"(?:[1-9][0-9]*|cycle-[0-9]+)\Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("material metadata must be an object")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _manifest(path: Path, *, producer: Mapping[str, Any], workspace: Path | None = None,
              cycle_dir: Path | None = None) -> dict[str, Any]:
    invocation_id = str(producer.get("invocation_id") or "")
    if not invocation_id or Path(invocation_id).name != invocation_id:
        raise ValueError("material manifest requires a canonical invocation identity")
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("material manifest may not be a symlink")
    expected_parent = (cycle_dir / "invocation_artifacts").resolve() if cycle_dir else path.parent.resolve()
    if path.resolve() != expected_parent / f"{invocation_id}.json" or expected_parent.name != "invocation_artifacts":
        raise ValueError("material manifest is outside the invocation artifact directory")
    payload = _json(path)
    if (payload.get("schema") != MANIFEST_SCHEMA or payload.get("status") != "completed"
            or payload.get("invocation_id") != invocation_id
            or payload.get("role") != producer.get("role")):
        raise ValueError("material manifest is not the matching completed invocation")
    if producer.get("stage") and payload.get("stage") != producer.get("stage"):
        raise ValueError("material manifest stage differs from its producer")
    root = Path(str(payload.get("workspace") or ""))
    if not root.is_absolute() or (workspace is not None and root.resolve() != workspace.resolve()):
        raise ValueError("material manifest workspace differs from the registered Cycle")
    if root.is_symlink():
        raise ValueError("material workspace may not be a symlink")
    if cycle_dir is not None and root.resolve() != (cycle_dir / "workspace").resolve():
        # Callers with an explicitly bound workspace can support legacy layouts.
        if workspace is None or root.resolve() != workspace.resolve():
            raise ValueError("material workspace is outside the registered Cycle")
    return payload


def bind_invocation_materials(manifest_path: Path | str, *, cycle_dir: Path, workspace: Path,
                             producer_invocation: Mapping[str, Any]) -> dict[str, Any]:
    """Pin completed SDK metadata; never use a model-authored file reference."""
    path = Path(manifest_path)
    _manifest(path, producer=producer_invocation, workspace=workspace, cycle_dir=cycle_dir)
    return {"relation": EXECUTION_RELATION, "manifest_path": str(path.resolve()),
            "manifest_sha256": _sha256(path), "workspace": str(workspace.resolve())}


def _local_asset(row: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    relative = Path(str(row.get("relative_path") or ""))
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or not is_delivery_file(relative)):
        raise ValueError("invalid or SDK-owned material path")
    candidate = workspace / relative
    for path in (candidate, *candidate.parents):
        if path == workspace.parent:
            break
        if path.is_symlink():
            raise ValueError("material path may not traverse a symlink")
    if str(candidate.resolve()) != str(row.get("path") or ""):
        raise ValueError("material path and relative identity differ")
    expected = str(row.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("material has no captured content hash")
    asset = {"reference": str(candidate.resolve()), "path": str(candidate.resolve()),
             "relative_path": relative.as_posix(), "sha256": expected,
             "size": row.get("size"), "kind": material_kind(str(candidate), "")}
    if not candidate.is_file():
        asset["status"] = "missing"
    else:
        current = _sha256(candidate)
        asset["current_sha256"] = current
        asset["status"] = "available" if current == expected else "drifted"
        if asset["kind"] == "figure" and asset["status"] == "available":
            asset["image_ref"] = quote(str(candidate.resolve()), safe="/:")
    return asset


def execution_materials(binding: Mapping[str, Any], producer: Mapping[str, Any], *,
                        cycle_dir: Path | None = None) -> dict[str, Any]:
    """Verify a pinned material binding without writing a registry or projection."""
    path = Path(str(binding.get("manifest_path") or ""))
    result: dict[str, Any] = {"relation": EXECUTION_RELATION, "manifest_path": str(path),
                             "manifest_sha256": binding.get("manifest_sha256"), "assets": []}
    checked: dict[str, str] = {}
    missing: list[str] = []
    changed: list[str] = []
    errors: list[str] = []
    try:
        if binding.get("relation") != EXECUTION_RELATION:
            raise ValueError("unsupported material association")
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("material manifest may not be a symlink")
        if not path.is_file():
            missing.append(str(path))
        else:
            digest = _sha256(path)
            # The metadata hash protects file identities, but is not itself a
            # local scientific artifact and cannot make an empty set verified.
            if digest != binding.get("manifest_sha256"):
                changed.append(str(path))
            else:
                payload = _manifest(path, producer=producer,
                                    workspace=Path(str(binding.get("workspace"))) if binding.get("workspace") else None,
                                    cycle_dir=cycle_dir)
                workspace = Path(payload["workspace"]).resolve()
                seen = set()
                for row in [*(payload.get("created") or []), *(payload.get("modified") or [])]:
                    if not isinstance(row, dict):
                        raise ValueError("invalid captured material record")
                    relative = Path(str(row.get("relative_path") or ""))
                    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                        raise ValueError("captured material path leaves its workspace")
                    if not is_delivery_file(relative):
                        continue  # historical tracker manifests included SDK files
                    asset = _local_asset(row, workspace)
                    if asset["path"] in seen:
                        continue
                    seen.add(asset["path"])
                    result["assets"].append(asset)
                    if asset["status"] == "missing":
                        missing.append(asset["path"])
                    else:
                        checked[asset["path"]] = asset["current_sha256"]
                        if asset["status"] == "drifted":
                            changed.append(asset["path"])
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(str(exc))
    status = "drifted" if missing or changed or errors else "verified" if checked else "none_registered"
    result["integrity"] = {"status": status, "checked_hashes": checked,
                           "missing_paths": missing, "changed_paths": changed, "errors": errors}
    return result


def material_kind(reference: str, role: str) -> str:
    suffix = Path(urlsplit(reference).path).suffix.lower()
    if suffix in _FIGURES:
        return "figure"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if role == "implementation" or suffix in {".py", ".sh", ".jl", ".r", ".ipynb"}:
        return "script"
    return "data" if role == "input" or suffix in {".npz", ".npy", ".h5", ".parquet"} else "document"


def material_rows(records: Iterable[Mapping[str, Any]], *, roots: Iterable[Path] = (),
                  cycle_dir: Path | None = None) -> list[dict[str, Any]]:
    """Shared material view: explicit legacy pointers plus batch execution files."""
    rows: list[dict[str, Any]] = []
    batches: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        pointers = record.get("artifact_pointers") or []
        pointers = [pointers] if isinstance(pointers, str) else pointers
        roles = {str(item.get("path")): str(item.get("role") or "")
                 for item in record.get("artifact_roles") or [] if isinstance(item, dict)}
        assets = []
        for reference in dict.fromkeys(p for p in pointers if isinstance(p, str) and p.strip()):
            role = roles.get(reference, "")
            asset = {"reference": reference, "kind": material_kind(reference, role)}
            if role:
                asset["role"] = role
            if urlsplit(reference).scheme in {"https", "http"}:
                asset["status"] = "external"
                if asset["kind"] == "figure":
                    asset["image_ref"] = quote(reference, safe="/:?#=&%")
            elif re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", reference):
                asset["status"] = "unsupported"
            else:
                path = Path(reference)
                matches = set()
                for candidate in ([path] if path.is_absolute() else [root / path for root in roots]):
                    try:
                        resolved = candidate.resolve()
                        if resolved.is_file():
                            matches.add(resolved)
                    except (OSError, ValueError, RuntimeError):
                        continue
                asset["status"] = "available" if len(matches) == 1 else "ambiguous" if matches else "missing"
                if len(matches) == 1:
                    resolved = matches.pop()
                    asset["path"] = resolved.as_posix()
                    if asset["kind"] == "figure":
                        asset["image_ref"] = quote(resolved.as_posix(), safe="/:")
            assets.append(asset)
        if assets:
            rows.append({"evidence_id": record.get("evidence_id"), "assets": assets})
        binding = record.get("execution_materials")
        producer = record.get("producer_invocation") or {}
        if not isinstance(binding, dict) or not isinstance(producer, dict):
            continue
        key = (str(record.get("evidence_batch_id") or producer.get("invocation_id") or ""),
               str(binding.get("manifest_path") or ""), str(binding.get("manifest_sha256") or ""))
        if key not in batches:
            batches[key] = {"evidence_batch_id": record.get("evidence_batch_id"), "evidence_ids": [],
                            "producer_invocation": dict(producer),
                            **execution_materials(binding, producer, cycle_dir=cycle_dir)}
        evidence_id = str(record.get("evidence_id") or "")
        if evidence_id and evidence_id not in batches[key]["evidence_ids"]:
            batches[key]["evidence_ids"].append(evidence_id)
    rows.extend(batches.values())
    return rows


def task_delivery_material_index(research_root: Path) -> dict[str, Any]:
    """Read committed Cycle metadata and bound files; never scan scientific data."""
    root = Path(research_root).resolve()
    materials = []
    cycles = []
    for cycle in sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                         and _CYCLE.fullmatch(p.name)), key=lambda p: (int(p.name.split("-")[-1]), p.name)) if root.is_dir() else []:
        evidence_dir = cycle / "evidence"
        commits = {str(row.get("evidence_batch_id")) for row in _jsonl(evidence_dir / "registration_batches.jsonl")
                   if row.get("event") == "batch_committed"
                   and row.get("schema") == "theory-master/evidence-registration-batch-commit/v1"}
        records = [row for row in _jsonl(evidence_dir / "registry.jsonl") if row.get("evidence_id")
                   and (not row.get("evidence_batch_id") or str(row["evidence_batch_id"]) in commits)]
        # File visibility does not confer scientific authority. Preserve the
        # lifecycle/admission boundary even in the task-wide delivery view.
        from .evidence import EvidenceRegistry
        lifecycle = {str(row["evidence_id"]): "active" for row in records}
        for event in _jsonl(evidence_dir / "lifecycle.jsonl"):
            evidence_id = str(event.get("evidence_id") or "")
            if evidence_id in lifecycle:
                lifecycle[evidence_id] = {"supersede": "superseded", "retract": "retracted",
                                          "restore": "active", "integrity_drift": "integrity_drifted"}.get(
                                              str(event.get("action")), lifecycle[evidence_id])
        states = {}
        for record in records:
            integrity = EvidenceRegistry._current_integrity(record)
            status = lifecycle[str(record["evidence_id"])]
            if status == "active" and integrity["status"] == "drifted":
                status = "integrity_drifted"
            projected_record = {**record, "lifecycle_status": status, "integrity_current": integrity}
            states[str(record["evidence_id"])] = {"lifecycle_status": status,
                "adjudicable": EvidenceRegistry._is_adjudicable(projected_record)}
        projected = material_rows(records, roots=[cycle / "workspace", root], cycle_dir=cycle)
        for row in projected:
            row["cycle_id"] = cycle.name
            ids = row.get("evidence_ids") or [row.get("evidence_id")]
            row["evidence_states"] = {key: states[key] for key in ids if key in states}
        materials.extend(projected)
        cycles.append({"cycle_id": cycle.name, "evidence_ids": [row["evidence_id"] for row in records],
                       "registered_material_groups": len(projected)})
    return {"schema": INDEX_SCHEMA, "research_root": str(root), "cycles": cycles, "materials": materials,
            "boundary": "Execution materials record same-invocation provenance, not per-Finding support or scientific independence."}
