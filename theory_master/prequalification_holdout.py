"""Exact, opt-in fixture holdout for qualification and its auxiliary roles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .role_registry import WRITE_ROLES

GUARDED_ROLES = frozenset({
    "research_lead", "challenger", "attention_reviewer", "literature",
    "runtime_supervisor", "incident_supervisor",
})
_SHA256 = re.compile(r"[a-f0-9]{64}")
_REFERENCE = re.compile(r"benchmark/(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+")


@dataclass(frozen=True)
class HoldoutFile:
    reference: str
    path: Path
    sha256: str
    size: int


def holdout_files(config_path: Path | None) -> tuple[HoldoutFile, ...]:
    """Verify exact registered files without accepting links or patterns."""
    if config_path is None:
        return ()
    config = json.loads(config_path.read_bytes())
    if not isinstance(config, dict):
        raise ValueError("Invalid TheoryMaster task configuration object")
    if "prequalification_holdout" not in config:
        return ()

    from .research_inputs import _FIXTURE_SCHEMA, _MAX_FILES, _MAX_INPUT, _manifest, _read
    from .task import SYSTEM_SCHEMA

    policy = config["prequalification_holdout"]
    benchmark = config.get("benchmark_root")
    if (config.get("schema") != SYSTEM_SCHEMA or not isinstance(benchmark, str)
            or not Path(benchmark).is_absolute() or re.search(r"[\x00-\x1f?*\[\]{}\\]", benchmark)
            or not isinstance(policy, dict)
            or set(policy) != {"files"} or not isinstance(policy["files"], list)
            or not 0 < len(policy["files"]) <= _MAX_FILES):
        raise ValueError("Invalid prequalification_holdout task declaration")
    root = Path(benchmark)
    files = _manifest(root / "fixture_manifest.json", _FIXTURE_SCHEMA).get("files")
    if not isinstance(files, list):
        raise ValueError("Prequalification holdout requires the canonical fixture manifest")

    result = {}
    for entry in policy["files"]:
        if not isinstance(entry, dict) or set(entry) != {"reference", "sha256"}:
            raise ValueError("Holdout entries require a fixture reference and SHA-256")
        reference, digest = entry["reference"], entry["sha256"]
        if (not isinstance(reference, str) or not _REFERENCE.fullmatch(reference)
                or any(part.startswith(".") for part in reference.split("/"))
                or reference == "benchmark/fixture_manifest.json"
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError("Invalid prequalification holdout fixture reference or SHA-256")
        path = root / reference.removeprefix("benchmark/")
        matches = [row for row in files if isinstance(row, dict) and row.get("path") == reference]
        if path in result or len(matches) != 1 or matches[0].get("sha256") != digest:
            raise ValueError(f"Holdout requires one matching fixture pin for {reference}")
        size = matches[0].get("size")
        if type(size) is not int or not 0 < size <= _MAX_INPUT:
            raise ValueError(f"Invalid fixture size pin for holdout {reference}")
        try:
            data = _read(path, size)
            info = path.stat(follow_symlinks=False)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Cannot read registered holdout fixture {reference}") from exc
        if info.st_nlink != 1 or len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Holdout fixture no longer matches its single-file pin: {reference}")
        result[path] = HoldoutFile(reference, path, digest, size)
    return tuple(result[path] for path in sorted(result))


def holdout_context(files: tuple[HoldoutFile, ...]) -> dict[str, Any] | None:
    if not files:
        return None
    return {
        "files": [{"reference": file.reference, "sha256": file.sha256, "size": file.size,
                   "status": "verified_reserved"} for file in files],
        "access": (
            "These registered files were verified and are reserved for the Investigator after Hypothesis Lock. "
            "Build or review the test protocol without inspecting their contents. "
            "A read denial during qualification confirms the reservation."
        ),
    }


def thread_profile(config_path: Path | None, role: str, *, workspace: Path | None = None
                   ) -> tuple[str, dict[str, Any]] | None:
    if role not in GUARDED_ROLES:
        return None
    files = holdout_files(config_path)
    if not files:
        return None
    filesystem = {":root": "read"}
    if role in WRITE_ROLES:
        if workspace is not None and any(file.path.is_relative_to(workspace.resolve()) for file in files):
            raise ValueError("Prequalification holdout must be outside the role's writable workspace")
        filesystem[":workspace_roots"] = "write"
    filesystem.update({str(file.path): "deny" for file in files})
    profile = {"filesystem": filesystem, "network": {"enabled": False}}
    identity = json.dumps([profile, "web_search=disabled", [file.sha256 for file in files]], sort_keys=True)
    name = "tm-prequalification-" + hashlib.sha256(identity.encode()).hexdigest()
    return name, {"default_permissions": name, "permissions": {name: profile}, "web_search": "disabled"}
