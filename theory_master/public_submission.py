"""Explicit public JSON delivery contracts, independent of scientific grading."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any

from referencing.exceptions import Unresolvable

SCHEMA = "theory-master/public-submission-contract/v1"
MAX_CONTRACT_BYTES = 128 * 1024
MAX_SUBMISSION_BYTES = 16 * 1024 * 1024


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
    value = strict_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or set(value) - {"schema", "json_schema", "selection"}
        or value.get("selection", "unique_registered_json") != "unique_registered_json"
        or not isinstance(value.get("json_schema"), dict)
    ):
        raise ValueError("Invalid public submission contract declaration")
    _validator(value["json_schema"])
    return {
        "schema": SCHEMA,
        "reference": reference,
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "json_schema": value["json_schema"],
        "selection": "unique_registered_json",
    }


def _registered_json_assets(payload: dict) -> list[dict]:
    assets = {}
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
            if (
                isinstance(name, str)
                and Path(name).suffix.lower() == ".json"
                and asset.get("status") == "available"
                and asset.get("role") != "input"
            ):
                previous = assets.get(name) or {}
                if (
                    previous.get("sha256")
                    and asset.get("sha256")
                    and previous["sha256"] != asset["sha256"]
                ):
                    raise ValueError("Conflicting registered submission content hashes")
                assets[name] = asset if asset.get("sha256") else previous or asset
    return [assets[name] for name in sorted(assets)]


def check_frozen_submission(payload: dict, snapshot: Any) -> dict | None:
    """Check only registered immutable artifacts; do not discover or author data."""
    declaration = payload.get("submission_contract")
    if declaration is None:
        return None
    contract_path = Path(declaration["path"])
    if not contract_path.is_relative_to(snapshot.root):
        raise ValueError("Public submission contract is outside its frozen snapshot")
    contract_raw = read_bounded_regular_file(contract_path, MAX_CONTRACT_BYTES)
    contract_value = strict_json(contract_raw)
    if (
        hashlib.sha256(contract_raw).hexdigest() != declaration["sha256"]
        or not isinstance(contract_value, dict)
        or contract_value.get("schema") != SCHEMA
        or contract_value.get("json_schema") != declaration["json_schema"]
    ):
        raise ValueError(
            "Public submission contract bytes differ from frozen authority"
        )
    validator = _validator(declaration["json_schema"])
    manifest = strict_json(
        read_bounded_regular_file(snapshot.manifest_path, MAX_SUBMISSION_BYTES)
    )
    hashes = {
        str(snapshot.root / row["path"]): row["sha256"] for row in manifest["files"]
    }
    matches, failures = [], []
    for asset in _registered_json_assets(payload):
        path = Path(asset["path"])
        try:
            if not path.is_relative_to(snapshot.root) or str(path) not in hashes:
                raise ValueError("Submission is outside the registered frozen snapshot")
            raw = read_bounded_regular_file(path, MAX_SUBMISSION_BYTES)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != hashes[str(path)] or (
                asset.get("sha256") and digest != asset["sha256"]
            ):
                raise ValueError("Registered submission content hash changed")
            document = strict_json(raw)
            errors = []
            for error in validator.iter_errors(document):
                errors.append(
                    {
                        "path": "/" + "/".join(map(str, error.absolute_path)),
                        "message": error.message[:1000],
                    }
                )
                if len(errors) >= 20:
                    break
            if errors:
                failures.append({"path": str(path), "errors": errors})
            else:
                matches.append({"path": str(path), "sha256": digest})
        except (OSError, ValueError, RecursionError, Unresolvable) as exc:
            failures.append(
                {"path": str(path), "errors": [{"message": str(exc)[:1000]}]}
            )
    if len(matches) == 1:
        return {
            "status": "valid",
            "contract_sha256": declaration["sha256"],
            "submission": matches[0],
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
        "reason": "Exactly one formally registered, immutable JSON artifact must satisfy the explicit public submission schema.",
    }
