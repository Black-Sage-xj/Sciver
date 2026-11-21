"""Verify explicitly registered research inputs without scanning task directories."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .goal_materials import required_benchmark_references
from .native_goal import _safe_workspace_bytes

_FIXTURE_SCHEMA = "sciver/private-scientific-fixture-manifest/v1"
_SUPPLEMENTAL_SCHEMA = "theorymaster/public-research-input-manifest/v1"
_FIXTURE = "benchmark/fixture_manifest.json"
_SOURCE_MANIFEST = "benchmark/source_manifest.json"
_SOURCE_MANIFEST_SCHEMA = "sciver-local-public-input-manifest/v1"
_CONTROL_FILES = {"fixture_manifest.json", "source_manifest.json", "input_manifest.json"}
_FIXTURE_CONTEXT = {"public_task.md", "source_notes.md"}
_PRIVATE_PART = re.compile(r"grader|expected|evaluator|answer", re.I)
_MAX_MANIFEST = 1_000_000
_MAX_INPUT = 64 * 1024 * 1024
_MAX_FILES = 40
_TOTAL_INLINE_BYTES = 24_000
_DEFAULT_INLINE_BYTES = 8_000
_PRIORITY_INLINE_BYTES = 12_000
_TEXT_SUFFIXES = {".py", ".md", ".json"}
_INPUT_KINDS = {"fixture_input", "supplemental_input"}


def _read(path: Path, limit: int) -> bytes:
    return _safe_workspace_bytes(Path("/"), path.relative_to("/"), max_bytes=limit)


def _manifest(path: Path, schema: str) -> dict:
    try:
        value = json.loads(_read(path, _MAX_MANIFEST))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("schema") == schema else {}


def _artifact_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or not root.is_absolute():
        return None
    path = Path(value)
    try:
        relative = path.relative_to(root) if path.is_absolute() else path
    except ValueError:
        return None
    if not relative.parts or any(part.startswith(".") or _PRIVATE_PART.search(part) for part in relative.parts):
        return None
    return root / relative


def _fixture_rows(spec: Any) -> list[tuple]:
    root = getattr(spec, "benchmark_root", None)
    if root is None:
        return []
    root = Path(root)
    references = set(required_benchmark_references(spec))
    if _FIXTURE not in references:
        return []
    files = _manifest(root / "fixture_manifest.json", _FIXTURE_SCHEMA).get("files")
    if not isinstance(files, list):
        return []
    rows = []
    for row in files:
        if not isinstance(row, dict):
            continue
        reference = row.get("path")
        if not isinstance(reference, str) or reference not in references:
            continue
        url = urlsplit(reference)
        parts = Path(unquote(url.path)).parts
        if len(parts) < 2 or parts[0] != "benchmark" or url.scheme or url.netloc:
            continue
        path = _artifact_path(root, str(Path(*parts[1:])))
        if path is None:
            continue
        provenance = (parts[1] in {"provenance", "source"} or path.name in _CONTROL_FILES | _FIXTURE_CONTEXT)
        rows.append((reference, path, row.get("size"), row.get("sha256"), _FIXTURE,
                     "provenance" if provenance else "fixture_input"))
    return rows


def _supplemental_rows(spec: Any) -> list[tuple]:
    root = Path(getattr(spec, "task_home", None) or Path(spec.document).parent)
    path = root / "input_manifest.json"
    manifest = _manifest(path, _SUPPLEMENTAL_SCHEMA)
    rows = []
    for field, kind in (("data_files", "supplemental_input"), ("derived_context_files", "provenance")):
        files = manifest.get(field, [])
        if not isinstance(files, list):
            return []
        for row in files:
            if not isinstance(row, dict):
                continue
            artifact = _artifact_path(root, row.get("path"))
            if artifact is None or artifact.name in _CONTROL_FILES:
                continue
            rows.append((str(artifact), artifact, row.get("bytes"), row.get("sha256"), str(path), kind))
    return rows


def _prioritize_input_context(verified: list[dict], digests: frozenset[str]) -> None:
    """Identify related context within the same dedicated directory and manifest."""
    bundles: dict[tuple[str, Path], set[str]] = {}
    for item in verified:
        if item["kind"] != "supplemental_input" or item["sha256"] not in digests:
            continue
        manifest = Path(item["manifest"])
        parent = Path(item["path"]).parent
        # A task's root or a top-level notes/source directory may contain
        # unrelated history. A direct sibling in a nested bundle is scoped.
        if len(parent.relative_to(manifest.parent).parts) >= 2:
            bundles.setdefault((item["manifest"], parent), set()).add(item["sha256"])
    for item in verified:
        path = Path(item["path"])
        if item["kind"] == "provenance" and path.suffix in _TEXT_SUFFIXES:
            related = bundles.get((item["manifest"], path.parent))
            if related:
                item["priority_context_for"] = sorted(related)


def _inline_rank(item: dict, digests: frozenset[str]) -> int:
    if item["kind"] in _INPUT_KINDS and item["sha256"] in digests:
        return 0
    if item.get("priority_context_for"):
        return 2 if Path(item["path"]).suffix == ".py" else 1
    return 3


def _valid_pin(size: object, sha256: object) -> bool:
    return (type(size) is int and 0 < size <= _MAX_INPUT and isinstance(sha256, str)
            and re.fullmatch(r"[a-f0-9]{64}", sha256) is not None)


def _pinned_bytes(path: Path, size: int, sha256: str) -> bytes | None:
    try:
        data = _read(path, size)
    except (OSError, ValueError):
        return None
    return data if len(data) == size and hashlib.sha256(data).hexdigest() == sha256 else None


def registered_fixture_inputs(spec: Any, *, excluded_sha256: frozenset[str] = frozenset()) -> list[dict]:
    """Inventory canonical public data pins, including unresolved file availability."""
    rows = _fixture_rows(spec)
    if not rows or len({row[1] for row in rows}) != len(rows):
        return []
    root = Path(spec.benchmark_root)
    inventory = []
    for reference, path, size, sha256, _, kind in sorted(rows, key=lambda row: row[0])[:_MAX_FILES]:
        if (kind != "fixture_input" or reference != f"benchmark/{path.relative_to(root).as_posix()}"
                or not _valid_pin(size, sha256) or sha256 in excluded_sha256):
            continue
        available = _pinned_bytes(path, size, sha256) is not None
        inventory.append({"reference": reference, "path": str(path), "size": size, "sha256": sha256,
                          "status": "available" if available else "unavailable"})
    return inventory


def verified_research_inputs(spec: Any, *, include_text: bool = False,
                             supplemental_only: bool = False,
                             excluded_sha256: frozenset[str] = frozenset(),
                             prioritize_sha256: frozenset[str] = frozenset()) -> list[dict]:
    """Return scoped, size- and hash-verified artifacts from canonical declarations.

    ``kind`` separates evidence-bearing inputs from contextual provenance. A new
    data identity can be recognized by its SHA-256 without treating a second
    path to the same bytes as a new observation.

    ``prioritize_sha256`` reserves space for the context of verified inputs
    before historical material, within the same inventory and text limits.
    Directly related derived context receives ``priority_context_for`` with
    the matched digests. Input order and membership stay stable.
    """
    fixture = [] if supplemental_only else _fixture_rows(spec)
    supplements = _supplemental_rows(spec)
    # Ambiguous declarations in either manifest grant no access through it.
    rows = []
    for manifest_rows in (fixture, supplements):
        if len({row[1] for row in manifest_rows}) == len(manifest_rows):
            rows.extend(manifest_rows)
    rows.sort(key=lambda row: (row[1].name not in _FIXTURE_CONTEXT, row[0]))
    if len(rows) > _MAX_FILES:
        priority_bundles = {
            (manifest, path.parent) for _, path, _, digest, manifest, kind in rows
            if kind == "supplemental_input" and isinstance(digest, str) and digest in prioritize_sha256
            and len(path.parent.relative_to(Path(manifest).parent).parts) >= 2
        }
        # Reserve the bounded inventory for formal inputs and public task
        # context before filling it with a new input's supporting notes.
        def inventory_rank(index: int) -> int:
            _, path, _, _, manifest, kind = rows[index]
            if kind == "fixture_input":
                return 0
            if kind in _INPUT_KINDS or path.name in _FIXTURE_CONTEXT:
                return 1
            return 2 if (manifest, path.parent) in priority_bundles else 3

        selected = sorted(range(len(rows)), key=inventory_rank)[:_MAX_FILES]
        rows = [rows[index] for index in sorted(selected)]
    verified = []
    text_candidates = []
    for reference, path, size, sha256, manifest, kind in rows:
        if not _valid_pin(size, sha256) or sha256 in excluded_sha256:
            continue
        data = _pinned_bytes(path, size, sha256)
        if data is None:
            continue
        item = {"reference": reference, "path": str(path), "sha256": sha256,
                "status": "available", "size": size, "manifest": manifest, "kind": kind}
        if include_text and path.suffix in _TEXT_SUFFIXES and size <= _PRIORITY_INLINE_BYTES:
            text_candidates.append((item, data))
        verified.append(item)
    if prioritize_sha256:
        _prioritize_input_context(verified, prioritize_sha256)

    remaining_text_bytes = _TOTAL_INLINE_BYTES
    for item, data in sorted(text_candidates, key=lambda row: _inline_rank(row[0], prioritize_sha256)):
        limit = _PRIORITY_INLINE_BYTES if _inline_rank(item, prioritize_sha256) < 3 else _DEFAULT_INLINE_BYTES
        budget = min(limit, remaining_text_bytes)
        if len(data) > budget:
            continue
        text = data.decode("utf-8", errors="replace")
        text_bytes = len(text.encode("utf-8")) if prioritize_sha256 else len(data)
        if text_bytes <= budget:
            item["source_text"] = text
            remaining_text_bytes -= text_bytes
    return verified


def _interface_template_pins(spec: Any) -> set[tuple[str, int, str]]:
    """Read interface roles from the task's hash-pinned source manifest."""
    from .public_submission import strict_json

    declarations = [row for row in _fixture_rows(spec) if row[0] == _SOURCE_MANIFEST]
    if len(declarations) != 1:
        return set()
    _, path, size, digest, _, _ = declarations[0]
    if not _valid_pin(size, digest) or size > _MAX_MANIFEST:
        return set()
    data = _pinned_bytes(path, size, digest)
    if data is None:
        return set()
    try:
        manifest = strict_json(data)
    except (ValueError, RecursionError):
        return set()
    if (not isinstance(manifest, dict) or manifest.get("schema") != _SOURCE_MANIFEST_SCHEMA
            or not isinstance(manifest.get("files"), list)):
        return set()
    by_reference: dict[str, list[dict]] = {}
    for row in manifest["files"]:
        if isinstance(row, dict) and isinstance(row.get("relative_path"), str):
            by_reference.setdefault(row["relative_path"], []).append(row)
    return {
        (reference, row["bytes"], row["sha256"])
        for reference, matches in by_reference.items()
        if len(matches) == 1 and (row := matches[0]).get("role") == "public_interface_template"
        and row.get("verified") is True and _valid_pin(row.get("bytes"), row.get("sha256"))
    }


def _is_interface_stub(data: bytes) -> bool:
    try:
        module = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError):
        return False
    body = module.body[1:] if ast.get_docstring(module, clean=False) is not None else module.body
    annotation_nodes = (ast.arguments, ast.arg, ast.Name, ast.Load, ast.Attribute, ast.Subscript,
                        ast.Tuple, ast.List, ast.Constant, ast.BinOp, ast.BitOr)
    has_stub = False
    for statement in body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if (not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) or statement.decorator_list
                or statement.type_params or statement.args.defaults or any(statement.args.kw_defaults)):
            return False
        for signature in (statement.args, statement.returns):
            if signature is not None and any(not isinstance(node, annotation_nodes) for node in ast.walk(signature)):
                return False
        implementation = (statement.body[1:] if ast.get_docstring(statement, clean=False) is not None
                          else statement.body)
        if len(implementation) != 1:
            return False
        match implementation[0]:
            case ast.Raise(exc=ast.Name(id="NotImplementedError"), cause=None) | ast.Pass():
                pass
            case ast.Raise(exc=ast.Call(func=ast.Name(id="NotImplementedError"), args=args, keywords=[]),
                           cause=None) if len(args) <= 1 and all(
                               isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in args):
                pass
            case ast.Expr(value=ast.Constant(value=value)) if value is Ellipsis:
                pass
            case _:
                return False
        has_stub = True
    return has_stub


def qualification_input_ids(spec: Any) -> list[str]:
    """Identify available evidence for direction changes, keeping fixture access intact."""
    inputs = [row for row in verified_research_inputs(spec) if row["kind"] in _INPUT_KINDS]
    small_python_fixtures = [row for row in inputs if row["kind"] == "fixture_input"
                             and row["size"] <= _PRIORITY_INLINE_BYTES and Path(row["path"]).suffix == ".py"]
    declarations = _interface_template_pins(spec) if small_python_fixtures else set()
    templates = set()
    for row in small_python_fixtures:
        pin = (row["reference"], row["size"], row["sha256"])
        if pin in declarations:
            data = _pinned_bytes(Path(row["path"]), row["size"], row["sha256"])
            if data is not None and _is_interface_stub(data):
                templates.add(pin)
    return sorted({row["sha256"] for row in inputs if row["kind"] != "fixture_input"
                   or (row["reference"], row["size"], row["sha256"]) not in templates})
