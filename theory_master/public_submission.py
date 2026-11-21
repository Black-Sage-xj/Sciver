"""Explicit public delivery structure, independent of scientific grading."""

from __future__ import annotations

import ast
import hashlib
import json
import keyword
import math
import os
import re
import stat
from pathlib import Path
from typing import Any

from referencing.exceptions import Unresolvable

SCHEMA = "theory-master/public-submission-contract/v1"
PYTHON_SCHEMA = "theory-master/public-python-submission-contract/v1"
MAX_CONTRACT_BYTES = 128 * 1024
MAX_SUBMISSION_BYTES = 16 * 1024 * 1024
MAX_PYTHON_BYTES = 1024 * 1024
_PYTHON_FILENAME = re.compile(r"[^/\\\x00-\x1f]+\.py")
_SHA256 = re.compile(r"[a-f0-9]{64}")


def _reject_constant(value: str) -> None:
    raise ValueError("Non-standard JSON numeric constant: " + value)


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def strict_json(data: bytes | str) -> Any:
    value = json.loads(data, parse_constant=_reject_constant, object_pairs_hook=_object)
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 200_000 or depth > 64:
            raise ValueError("JSON document exceeds structural bounds")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON number is not finite")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def read_bounded_regular_file(path: Path, limit: int) -> bytes:
    """Keep every directory descriptor pinned; reject symlink races and FIFOs."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe public submission input: " + str(path))
    directory = descriptor = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError(
                "Unsafe or oversized public submission input: " + str(path)
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Public submission input exceeded its byte bound")
        return data
    except OSError as exc:
        raise ValueError(
            "Unsafe or unreadable public submission input: " + str(path)
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _validator(schema: dict):
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource
    from referencing.jsonschema import DRAFT202012

    def offline(reference):
        raise NoSuchResource(ref=reference)

    # Traverse schema positions only: const/enum/default are opaque authored data.
    stack, references = [schema], []
    singles = {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "items",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    }
    maps = {
        "properties",
        "patternProperties",
        "$defs",
        "dependentSchemas",
        "definitions",
    }
    arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = item.get(key)
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#"):
                    raise ValueError(
                        "Public submission schemas permit only in-document references"
                    )
                references.append(reference)
        stack.extend(item[key] for key in singles if key in item)
        for key in maps:
            if isinstance(item.get(key), dict):
                stack.extend(item[key].values())
        for key in arrays:
            if isinstance(item.get(key), list):
                stack.extend(item[key])
    if (
        schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
        != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise ValueError("Public submission schema requires JSON Schema Draft 2020-12")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid public JSON Schema: " + exc.message) from exc
    registry = Registry(retrieve=offline)
    resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    registry = registry.with_resource(
        "urn:theory-master:public-submission", resource
    ).crawl()
    resolver = registry.resolver("urn:theory-master:public-submission")
    for reference in references:
        try:
            resolver.lookup(reference)
        except Unresolvable as exc:
            raise ValueError(
                "Unresolved in-document public submission schema reference: "
                + reference
            ) from exc
    return Draft202012Validator(schema, registry=registry)


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and value.isidentifier() and not keyword.iskeyword(value)


def _contract_structure(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Invalid public submission contract declaration")
    schema = value.get("schema")
    if schema == SCHEMA:
        if (set(value) - {"schema", "json_schema", "selection"}
                or value.get("selection", "unique_registered_json") != "unique_registered_json"
                or not isinstance(value.get("json_schema"), dict)):
            raise ValueError("Invalid public submission contract declaration")
        return {"schema": SCHEMA, "json_schema": value["json_schema"], "selection": "unique_registered_json"}
    if schema != PYTHON_SCHEMA or set(value) != {"schema", "filename", "callable", "allowed_imports"}:
        raise ValueError("Invalid public submission contract declaration")
    filename, callable_spec, imports = value["filename"], value["callable"], value["allowed_imports"]
    if (not isinstance(filename, str) or not _PYTHON_FILENAME.fullmatch(filename)
            or not isinstance(callable_spec, dict) or set(callable_spec) != {"name", "parameters"}
            or not _identifier(callable_spec["name"])):
        raise ValueError("Python submission requires a literal .py basename and one callable name")
    parameters = callable_spec["parameters"]
    if (not isinstance(parameters, list) or not all(_identifier(name) for name in parameters)
            or len(set(parameters)) != len(parameters)):
        raise ValueError("Python submission requires distinct positional parameter names")
    if (not isinstance(imports, list) or not all(isinstance(name, str) and
            all(_identifier(part) for part in name.split(".")) for name in imports)
            or len(set(imports)) != len(imports)):
        raise ValueError("Python submission requires distinct absolute import names")
    return {**value, "format": "python", "selection": "unique_registered_python"}


def load_submission_contract(spec: Any) -> dict | None:
    """Only an explicit benchmark namespace grants access; never search parents."""
    reference = getattr(spec, "submission_contract_ref", None)
    if reference is None:
        return None
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise ValueError(
            "submission_contract must name an explicit benchmark/ JSON file"
        )
    relative = Path(reference)
    benchmark = getattr(spec, "benchmark_root", None)
    if hasattr(spec, "benchmark_dir") and spec.benchmark_dir is None:
        benchmark = None  # TaskSpec legacy convention is not explicit input authority.
    if (
        relative.is_absolute()
        or relative.as_posix() != reference
        or len(relative.parts) < 2
        or relative.parts[0] != "benchmark"
        or ".." in relative.parts
        or benchmark is None
        or relative.suffix != ".json"
    ):
        raise ValueError(
            "submission_contract requires an explicit safe benchmark/ JSON reference"
        )
    root = Path(benchmark)
    if not root.is_absolute():
        raise ValueError(
            "Public submission benchmark_root must be explicit and absolute"
        )
    path = root.joinpath(*relative.parts[1:])
    raw = read_bounded_regular_file(path, MAX_CONTRACT_BYTES)
    contract = _contract_structure(strict_json(raw))
    if contract["schema"] == SCHEMA:
        _validator(contract["json_schema"])
    return {
        "reference": reference,
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        **contract,
    }


def _registered_assets(payload: dict, declaration: dict, hashes: dict) -> list[dict]:
    from .delivery_materials import EXECUTION_RELATION

    python_filename = declaration["filename"] if declaration["schema"] == PYTHON_SCHEMA else None
    if python_filename is not None and "operator_public_submission" in payload:
        from .operator_public_submission import checked_operator_asset

        selected = checked_operator_asset(payload["operator_public_submission"], declaration, hashes)
        return [selected] if selected is not None else []
    assets = {}
    inputs = set()
    for group in (payload.get("material_index") or {}).get("materials", []):
        states = group.get("evidence_states") or {}
        if not any(
            row.get("lifecycle_status") == "active"
            for row in states.values()
            if isinstance(row, dict)
        ):
            continue
        if group.get("integrity", {}).get("status", "verified") != "verified":
            continue
        for asset in group.get("assets") or []:
            name = asset.get("path")
            if not isinstance(name, str) or asset.get("status") != "available":
                continue
            if python_filename is None:
                if Path(name).suffix.lower() != ".json" or asset.get("role") == "input":
                    continue
            else:
                if Path(name).name != python_filename:
                    continue
                if asset.get("role") == "input":
                    inputs.add(name)
                    continue
                producer = group.get("producer_invocation") or {}
                if (group.get("relation") != EXECUTION_RELATION
                        or group.get("integrity", {}).get("status") != "verified"
                        or not isinstance(producer, dict) or producer.get("role") != "investigator"
                        or not isinstance(producer.get("invocation_id"), str) or not producer["invocation_id"]
                        or not _SHA256.fullmatch(str(group.get("manifest_sha256") or ""))
                        or not _SHA256.fullmatch(str(asset.get("sha256") or ""))):
                    continue
            previous = assets.get(name) or {}
            if (previous.get("sha256") and asset.get("sha256")
                    and previous["sha256"] != asset["sha256"]):
                raise ValueError("Conflicting registered submission content hashes")
            assets[name] = asset if asset.get("sha256") else previous or asset
    return [assets[name] for name in sorted(assets.keys() - inputs)]


def check_frozen_submission(payload: dict, snapshot: Any) -> dict | None:
    """Check only registered immutable artifacts; do not discover or author data."""
    declaration = payload.get("submission_contract")
    if declaration is None:
        return None
    contract_path = Path(declaration["path"])
    if not contract_path.is_relative_to(snapshot.root):
        raise ValueError("Public submission contract is outside its frozen snapshot")
    contract_raw = read_bounded_regular_file(contract_path, MAX_CONTRACT_BYTES)
    contract = {key: value for key, value in declaration.items() if key not in {"reference", "path", "sha256"}}
    if (
        hashlib.sha256(contract_raw).hexdigest() != declaration["sha256"]
        or _contract_structure(strict_json(contract_raw)) != contract
    ):
        raise ValueError(
            "Public submission contract bytes differ from frozen authority"
        )
    manifest = strict_json(
        read_bounded_regular_file(snapshot.manifest_path, MAX_SUBMISSION_BYTES)
    )
    hashes = {
        str(snapshot.root / row["path"]): row["sha256"] for row in manifest["files"]
    }
    return _check_registered_submission(payload, declaration, hashes, snapshot_root=snapshot.root)


def check_current_submission(payload: dict) -> dict | None:
    """Preview the same schema and registration rule on verified live sources."""
    declaration = payload.get("submission_contract")
    if declaration is None:
        return None
    hashes = {
        row["path"]: row["sha256"] for row in payload.get("goal_material_sources", [])
        if row.get("status") == "available" and row.get("path") and row.get("sha256")
    }
    return _check_registered_submission(payload, declaration, hashes)


def _python_errors(raw: bytes, declaration: dict) -> list[dict]:
    try:
        tree = ast.parse(raw, filename=declaration["filename"], mode="exec")
    except SyntaxError as error:
        return [{"line": error.lineno, "message": f"Invalid Python syntax: {error.msg}"[:1000]}]
    signature = declaration["callable"]
    name = signature["name"]
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   and node.name == name]
    errors = []
    if len(definitions) != 1 or not isinstance(definitions[0], ast.FunctionDef):
        errors.append({"message": f"Expected one top-level synchronous function definition named {name}"[:1000]})
    else:
        args = definitions[0].args
        if ([arg.arg for arg in [*args.posonlyargs, *args.args]] != signature["parameters"]
                or args.defaults or args.vararg or args.kwonlyargs or args.kwarg):
            expected = ", ".join(signature["parameters"])
            errors.append({"line": definitions[0].lineno,
                           "message": f"Expected exact positional signature {name}({expected})"[:1000]})
    allowed = set(declaration["allowed_imports"])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = ["." * node.level + (node.module or "")]
        else:
            continue
        for module in modules:
            parent = module
            while parent and parent not in allowed:
                parent = parent.rpartition(".")[0]
            if not parent or module.startswith("."):
                errors.append({"line": node.lineno, "message": f"Disallowed explicit Python import: {module}"[:1000]})
                if len(errors) == 20:
                    return errors
    return errors


def _check_registered_submission(
    payload: dict, declaration: dict, hashes: dict, *, snapshot_root: Path | None = None
) -> dict:
    python_filename = declaration["filename"] if declaration["schema"] == PYTHON_SCHEMA else None
    validator = _validator(declaration["json_schema"]) if python_filename is None else None
    verification = {"verification": "python_ast_structure"} if python_filename is not None else {}
    matches, failures = [], []
    for asset in _registered_assets(payload, declaration, hashes):
        path = Path(asset["path"])
        try:
            if snapshot_root is not None and not path.is_relative_to(snapshot_root):
                raise ValueError("Submission is outside the registered frozen snapshot")
            if str(path) not in hashes:
                raise ValueError("Submission is outside the registered frozen snapshot"
                                 if snapshot_root is not None else "Submission has no verified registered source")
            raw = read_bounded_regular_file(path, MAX_PYTHON_BYTES if python_filename else MAX_SUBMISSION_BYTES)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != hashes[str(path)] or (
                asset.get("sha256") and digest != asset["sha256"]
            ):
                raise ValueError("Registered submission content hash changed")
            if validator is None:
                errors = _python_errors(raw, declaration)
            else:
                document = strict_json(raw)
                errors = []
                for error in validator.iter_errors(document):
                    errors.append({"path": "/" + "/".join(map(str, error.absolute_path)),
                                   "message": error.message[:1000]})
                    if len(errors) >= 20:
                        break
            if errors:
                failures.append({"path": str(path), "errors": errors})
            else:
                matches.append({"path": str(path), "sha256": digest,
                                **({"origin": asset["origin"]} if asset.get("origin") else {})})
        except (OSError, ValueError, RecursionError, Unresolvable) as exc:
            failures.append(
                {"path": str(path), "errors": [{"message": str(exc)[:1000]}]}
            )
    if len(matches) == 1:
        return {
            "status": "valid",
            "contract_sha256": declaration["sha256"],
            "submission": matches[0],
            **verification,
        }
    return {
        "status": "invalid",
        "code": (
            "registered_submission_schema_absent"
            if not matches
            else "registered_submission_ambiguous"
        ),
        "contract_sha256": declaration["sha256"],
        "contract": declaration,
        "matching_artifacts": matches,
        "artifact_errors": failures[:50],
        "reason": (f"Exactly one formally registered, immutable {python_filename} artifact must satisfy the declared Python structure."
                   if python_filename else "Exactly one formally registered, immutable JSON artifact must satisfy the explicit public submission schema."),
        **verification,
    }
