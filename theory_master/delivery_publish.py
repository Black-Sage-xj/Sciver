"""Explicit, deterministic publication of task-local delivery files.

Publication records file identity and provenance, never scientific support. It
does not run Agents, change Evidence, or create a Goal assessment/revision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .delivery_materials import task_delivery_material_index

MANIFEST_SCHEMA = "theory-master/delivery-publication/v1"
RECEIPT_SCHEMA = "theory-master/delivery-publication-receipt/v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CYCLE = re.compile(r"(?:[1-9][0-9]*|cycle-[0-9]+)\Z")
_DOCUMENT_SUFFIXES = {".md", ".txt", ".rst", ".pdf", ".html"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("publication requires a lowercase SHA-256 identity")
    return value


def _absolute(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("publication paths must be explicit absolute paths")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("publication paths must be absolute without traversal")
    return path


def _relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("publication requires an explicit relative file path")
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != value:
        raise ValueError("invalid publication relative path")
    return path


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Walk from / with directory FDs; no component may be a symlink."""
    path = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _check_path(path: Path) -> None:
    """Check every existing ancestor, including parents of a missing target."""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"publication path traverses a symlink: {part}")
    # Opening an existing parent catches non-directory ancestors as well.
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    with _directory(parent):
        pass


@contextmanager
def _file(path: Path) -> Iterator[int]:
    with _directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"publication source/target is not a regular file: {path}")
        yield fd
    finally:
        os.close(fd)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with _file(path) as fd:
        for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    with _file(path) as fd:
        chunks = []
        for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
            chunks.append(chunk)
    value = json.loads(b"".join(chunks))
    if not isinstance(value, dict):
        raise ValueError("publication metadata must be an object")
    return value


def _pinned(path: Path, digest: str) -> None:
    _check_path(path)
    if _file_hash(path) != digest:
        raise ValueError(f"publication content hash drift: {path}")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _roots(spec: Any) -> tuple[Path, Path, list[Path], list[Path]]:
    root = _absolute(spec.research_root)
    plan = _absolute(spec.document)
    task_home = getattr(spec, "task_home", None)
    if task_home is None:
        raise ValueError("publication requires an explicit TaskHome")
    home = _absolute(task_home)
    for path in (root, plan, home):
        _check_path(path)
    note = plan.parent
    sources = [root, note, home]
    forbidden = [root, note, home / "inputs", root / "inputs"]
    canvas = getattr(spec, "branch_canvas", None)
    if canvas:
        canvas_root = _absolute(canvas).parent
        sources.append(canvas_root)
        forbidden.append(canvas_root)
    notes = getattr(spec, "notes_mirror_dir", None)
    if notes:
        sources.append(_absolute(notes))
        forbidden.append(_absolute(notes))
    benchmark = getattr(spec, "benchmark_root", None)
    if benchmark:
        sources.append(_absolute(benchmark))
        forbidden.append(_absolute(benchmark))
    from .task import note_vault_root
    for path in (note, home):
        vault = note_vault_root(path)
        if vault is not None:
            forbidden.append(vault)
    # Sources are explicit existing local documents/materials of this task.
    return root, home, list(dict.fromkeys(sources)), forbidden


def _source_reference(value: Any, roots: list[Path], spec: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError("document provenance requires explicit path and SHA-256")
    path = _absolute(value["path"])
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("publication source/reference is outside this task")
    benchmark = getattr(spec, "benchmark_root", None)
    if benchmark and path.is_relative_to(_absolute(benchmark)):
        # This pure parser does not call the publication reader. Keep the same
        # explicit Required Context boundary used by Goal; TaskHome containment
        # must not admit hidden benchmark answers or unrelated attachments.
        from .goal_materials import _benchmark_relative, required_benchmark_references
        allowed = {_absolute(benchmark) / relative for reference in required_benchmark_references(spec)
                   if (relative := _benchmark_relative(reference)) is not None}
        if path not in allowed:
            raise ValueError("publication benchmark source/reference is not registered in Required Context")
    digest = _hash(value["sha256"])
    _pinned(path, digest)
    return {"path": str(path), "sha256": digest}


def _registered(source: dict[str, Any], root: Path, index: dict[str, Any]) -> dict[str, Any]:
    if set(source) != {"kind", "cycle_id", "invocation_id", "path", "sha256"}:
        raise ValueError("registered material requires Cycle, invocation, relative path and SHA-256")
    cycle = source["cycle_id"]
    invocation = source["invocation_id"]
    if not isinstance(cycle, str) or not _CYCLE.fullmatch(cycle):
        raise ValueError("invalid registered Cycle identity")
    if not isinstance(invocation, str) or not invocation or Path(invocation).name != invocation:
        raise ValueError("invalid registered invocation identity")
    relative = _relative(source["path"])
    digest = _hash(source["sha256"])
    candidate = root / cycle / "workspace" / relative
    matches = []
    for group in index["materials"]:
        producer = group.get("producer_invocation") or {}
        if (group.get("cycle_id") != cycle or producer.get("invocation_id") != invocation
                or group.get("integrity", {}).get("status") != "verified"
                or not group.get("evidence_ids")):
            continue
        for asset in group.get("assets") or []:
            if (asset.get("relative_path") == relative.as_posix() and asset.get("path") == str(candidate)
                    and asset.get("sha256") == digest and asset.get("status") == "available"):
                matches.append(group)
    if len(matches) != 1:
        raise ValueError("source is not a unique verified, committed invocation material of this task")
    group = matches[0]
    manifest = _absolute(group["manifest_path"])
    if manifest != root / cycle / "invocation_artifacts" / f"{invocation}.json":
        raise ValueError("foreign invocation manifest")
    _pinned(manifest, _hash(group["manifest_sha256"]))
    _pinned(candidate, digest)
    return {"kind": "registered_material", "path": str(candidate), "sha256": digest,
            "relative_path": relative.as_posix(), "cycle_id": cycle, "invocation_id": invocation,
            "manifest_path": str(manifest), "manifest_sha256": group["manifest_sha256"],
            "evidence_ids": group["evidence_ids"]}


def _preflight(spec: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "research_root", "plan_sha256", "destinations", "files"}:
        raise ValueError("invalid delivery publication manifest fields")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ValueError("unsupported delivery publication manifest schema")
    root, home, sources, forbidden = _roots(spec)
    if _absolute(manifest["research_root"]) != root:
        raise ValueError("publication manifest belongs to another task")
    plan_digest = _hash(manifest["plan_sha256"])
    _pinned(_absolute(spec.document), plan_digest)
    destinations = manifest["destinations"]
    if not isinstance(destinations, dict) or set(destinations) != {"analysis", "result"}:
        raise ValueError("publication must explicitly map TaskHome analysis and result directories")
    for name, value in destinations.items():
        path = _absolute(value)
        if path != home / name or any(_overlap(path, other) for other in forbidden):
            raise ValueError("destination must be TaskHome analysis/result outside notes, research and inputs")
        _check_path(path)
        if path.exists() and not path.is_dir():
            raise ValueError(f"publication destination is not a directory: {path}")
    files = manifest["files"]
    if not isinstance(files, list) or not files or len(files) > 1000:
        raise ValueError("publication requires 1..1000 explicit file mappings")
    registered = any(
        isinstance(row, dict) and isinstance(row.get("source"), dict)
        and row["source"].get("kind") == "registered_material" for row in files)
    if registered:
        for row in files:
            source = row.get("source") if isinstance(row, dict) else None
            cycle = source.get("cycle_id") if isinstance(source, dict) else None
            if isinstance(cycle, str) and _CYCLE.fullmatch(cycle):
                for name in ("registry.jsonl", "registration_batches.jsonl"):
                    _check_path(root / cycle / "evidence" / name)
    index = task_delivery_material_index(root) if registered else {}
    rows = []
    targets: set[Path] = set()
    for mapping in files:
        if not isinstance(mapping, dict) or set(mapping) != {"source", "target"}:
            raise ValueError("each publication mapping requires source and target")
        target = _relative(mapping["target"])
        if len(target.parts) < 2 or target.parts[0] not in destinations:
            raise ValueError("target must be a file below analysis/ or result/")
        path = home / target
        if path in targets or any(_overlap(path, other) for other in targets):
            raise ValueError("duplicate or overlapping publication targets")
        targets.add(path)
        _check_path(path)
        source = mapping["source"]
        if not isinstance(source, dict):
            raise ValueError("publication source must be an object")
        references = []
        if source.get("kind") == "registered_material":
            identity = _registered(source, root, index)
        elif source.get("kind") == "supplemental_document":
            if set(source) != {"kind", "path", "sha256", "references"}:
                raise ValueError("supplemental document requires pinned content and provenance")
            identity = {"kind": "supplemental_document", **_source_reference(
                {"path": source["path"], "sha256": source["sha256"]}, sources, spec)}
            if Path(identity["path"]).suffix.lower() not in _DOCUMENT_SUFFIXES:
                raise ValueError("supplemental delivery source must be a document")
            if not isinstance(source["references"], list) or not 1 <= len(source["references"]) <= 1000:
                raise ValueError("supplemental delivery document requires explicit source references")
            references = [_source_reference(ref, sources, spec) for ref in source["references"]]
            if identity["path"] in {ref["path"] for ref in references}:
                raise ValueError("supplemental document cannot cite itself as provenance")
        else:
            raise ValueError("unsupported publication source kind")
        if path == Path(identity["path"]):
            raise ValueError("publication target must differ from its source")
        digest = identity["sha256"]
        if path.exists() and _file_hash(path) != digest:
            raise ValueError(f"publication target conflict; existing content will not be overwritten: {path}")
        rows.append({"path": str(path), "target": target.as_posix(), "sha256": digest,
                     "kind": identity["kind"], "source": identity, "references": references})
    ident = _digest(manifest)
    receipt_path = root / "delivery_publications" / f"{ident}.json"
    _check_path(receipt_path)
    _check_path(root / ".theory-master" / "science_commit.lock")
    payload = {"schema": RECEIPT_SCHEMA, "publication_id": ident,
               "research_root": str(root), "plan_sha256": plan_digest,
               "receipt_path": str(receipt_path), "authority": "delivery_only",
               "scientific_evidence": False, "manifest": manifest, "files": rows}
    receipt = {**payload, "receipt_sha256": _digest(payload)}
    if receipt_path.exists() and _json(receipt_path) != receipt:
        raise ValueError("publication receipt conflicts with this immutable manifest")
    return receipt


def _install_file(source: Path, target: Path, digest: str) -> None:
    """Stage, hash, fsync, then atomically install without overwriting a name."""
    with _file(source) as src, _directory(target.parent, create=True) as parent:
        temp = f".delivery-publish-{secrets.token_hex(12)}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600 | (os.fstat(src).st_mode & 0o111), dir_fd=parent)
        try:
            checked = hashlib.sha256()
            for chunk in iter(lambda: os.read(src, 1024 * 1024), b""):
                checked.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view):]
            if checked.hexdigest() != digest:
                raise ValueError(f"publication source changed during copy: {source}")
            os.fsync(fd)
            try:
                os.link(temp, target.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                if _file_hash(target) != digest:
                    raise ValueError(f"publication target conflict: {target}") from None
            os.fsync(parent)
        finally:
            os.close(fd)
            os.unlink(temp, dir_fd=parent)


def _install_receipt(path: Path, receipt: dict[str, Any]) -> None:
    with _directory(path.parent, create=True) as parent:
        temp = f".delivery-publish-{secrets.token_hex(12)}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            data = _canonical(receipt) + b"\n"
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            try:
                os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                if _json(path) != receipt:
                    raise ValueError("publication receipt identity conflict") from None
            os.fsync(parent)
        finally:
            os.close(fd)
            os.unlink(temp, dir_fd=parent)


def _assert_open(spec: Any) -> None:
    from .goal_terminal import GoalSealedError, goal_sealed
    root = _absolute(spec.research_root)
    graph = getattr(spec, "graph_database", None)
    if graph is None:
        from .notebook import graph_database_path
        canvas = getattr(spec, "branch_canvas", None)
        graph = graph_database_path(Path(canvas).parent if canvas else root)
    if goal_sealed(root, Path(graph), spec=spec) is not None:
        raise GoalSealedError("cannot publish delivery for a sealed Goal")


def publication_spec(manifest: dict[str, Any], master_id: str | int) -> Any:
    """Load an existing manifest-bound task without discovery or pointer repair.

    Normal task discovery can create a missing runtime pointer. Publication's
    preview must remain read-only, so require that binding to exist already and
    check its exact Task ID/root before invoking the existing spec validator.
    """
    from .specification import TaskSpec
    from .task import SYSTEM_SCHEMA, TASK_SCHEMA, effective_master_id, effective_path_model
    if not isinstance(manifest, dict):
        raise ValueError("publication manifest must be an object")
    root = _absolute(manifest.get("research_root"))
    _check_path(root)
    with _directory(root):
        pass
    pointer = _json(root / ".theory-master" / "task.json")
    if pointer.get("schema") != TASK_SCHEMA:
        raise ValueError("publication requires an existing current task pointer")
    config_path = _absolute(pointer.get("config_path"))
    config = _json(config_path)
    if config.get("schema") != SYSTEM_SCHEMA:
        raise ValueError("publication requires a current task configuration")
    _, configured_root, _ = effective_path_model(config)
    if configured_root != root or str(effective_master_id(config)) != str(master_id).strip():
        raise ValueError("publication Task ID/root differs from the registered task")
    return TaskSpec.from_config(config_path)


def publish_delivery(spec: Any, manifest: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Validate an explicit mapping; only ``dry_run=False`` publishes files.

    Known conflicts are rejected before any write. Receipt is committed last;
    a crash may leave a subset of identical files, which the same manifest can
    safely finish. No scientific state, old delivery revision or Goal is changed.
    """
    # Detach from a mutable caller, while preserving canonical JSON identity.
    manifest = json.loads(_canonical(manifest))
    receipt = _preflight(spec, manifest)
    if not dry_run:
        from .goal_terminal import science_commit_barrier
        _assert_open(spec)
        with science_commit_barrier(Path(spec.research_root), "exclusive"):
            _assert_open(spec)
            if _preflight(spec, manifest) != receipt:
                raise ValueError("publication provenance changed during preflight")
            for row in receipt["files"]:
                _install_file(Path(row["source"]["path"]), Path(row["path"]), row["sha256"])
            if _preflight(spec, manifest) != receipt:
                raise ValueError("publication provenance changed before receipt commit")
            for row in receipt["files"]:
                _pinned(Path(row["path"]), row["sha256"])
            _install_receipt(Path(receipt["receipt_path"]), receipt)
    return {**receipt, "dry_run": dry_run, "status": "preview" if dry_run else "published",
            "files": [{**row, "status": "available" if Path(row["path"]).is_file() else "pending"}
                      for row in receipt["files"]]}


def read_publications(spec: Any) -> list[dict[str, Any]]:
    """Pure read. Only ``status=available`` files grant exact-file read access.

    Revalidate the explicit task mapping, committed source identity, source and
    reference hashes, receipt digest, and every destination. Invalid receipts
    expose diagnostics with no authorized file paths.
    """
    root = _absolute(spec.research_root)
    directory = root / "delivery_publications"
    if not directory.exists() and not directory.is_symlink():
        return []
    try:
        _check_path(directory)
        with _directory(directory):
            paths = sorted(directory.glob("*.json"))
    except (OSError, ValueError, RuntimeError) as exc:
        return [{"publication_id": "", "status": "drifted", "authority": "delivery_only",
                 "scientific_evidence": False, "files": [], "errors": [str(exc)]}]
    rows = []
    for path in paths:
        if not _HASH.fullmatch(path.stem):
            continue
        row: dict[str, Any] = {"publication_id": path.stem, "receipt_path": str(path),
                               "authority": "delivery_only", "scientific_evidence": False,
                               "status": "drifted", "files": []}
        try:
            receipt = _json(path)
            manifest = receipt.get("manifest")
            if not isinstance(manifest, dict) or _digest(manifest) != path.stem:
                raise ValueError("publication receipt manifest identity mismatch")
            if _preflight(spec, manifest) != receipt:
                raise ValueError("publication receipt verification failed")
            for item in receipt["files"]:
                _pinned(Path(item["path"]), item["sha256"])
            row.update(receipt)
            row["status"] = "available"
            row["files"] = [{**item, "status": "available"} for item in receipt["files"]]
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            row["errors"] = [str(exc)]
        rows.append(row)
    return rows
