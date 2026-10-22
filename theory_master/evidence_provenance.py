"""Lightweight, fail-closed provenance rules for strong test-set identities.

This is intentionally not a general data-lineage system. It only prevents
post-hoc/agent-generated validation from being upgraded by prose into identities
such as benchmark-provided holdout or gate evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

SOURCES = frozenset({
    "benchmark_provided",
    "pre_specified_synthetic",
    "agent_generated",
    "external_source",
})
SPLITS = frozenset({"none", "fit", "validation", "holdout", "gate"})
PROTECTED_SPLITS = frozenset({"holdout", "gate"})

def normalize_provenance(
    item: dict[str, Any],
    *,
    workspace: Path,
    resolve_local_ref: Callable[[str], list[str]],
) -> dict[str, Any] | None:
    """Validate optional structured provenance.

    Agent declarations cannot prove pre-Cycle freeze, even if the referenced
    file is outside the current workspace. Preserve claims for audit without
    upgrading their scientific identity to verified holdout/gate authority.
    """
    raw = item.get("provenance")
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("Evidence provenance must be an object")

    source = str(raw.get("source") or "").strip()
    split = str(raw.get("split") or "none").strip()
    declaration_ref = str(raw.get("declaration_ref") or "").strip()
    if source not in SOURCES:
        raise ValueError("Evidence provenance.source is invalid")
    if split not in SPLITS:
        raise ValueError("Evidence provenance.split is invalid")
    if source == "agent_generated" and split in PROTECTED_SPLITS:
        raise ValueError("agent-generated validation cannot claim holdout or gate identity")
    if split in PROTECTED_SPLITS and source not in {"benchmark_provided", "pre_specified_synthetic"}:
        raise ValueError("holdout/gate split requires frozen benchmark or pre-specified provenance")

    protected_frozen = source in {"benchmark_provided", "pre_specified_synthetic"}
    canonical_ref = declaration_ref
    if protected_frozen:
        if not declaration_ref:
            raise ValueError(
                "frozen benchmark/pre-specified holdout or gate identity requires declaration_ref"
            )
        matches = resolve_local_ref(declaration_ref)
        if len(matches) != 1:
            raise ValueError("provenance declaration_ref must resolve to exactly one local artifact")
        declaration = Path(matches[0]).resolve()
        try:
            declaration.relative_to(workspace.resolve())
        except ValueError:
            pass  # Location is a basic guard, NOT proof of prior freeze.
        else:
            raise ValueError("protected provenance declaration_ref must be outside the current Cycle workspace")
        canonical_ref = str(declaration)
        digest = hashlib.sha256()
        try:
            with declaration.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ValueError("provenance declaration_ref cannot be hashed") from exc

    normalized = {"source": source, "split": split}
    if canonical_ref:
        normalized["declaration_ref"] = canonical_ref
    if protected_frozen:
        normalized["declaration_sha256"] = digest.hexdigest()
        # Hashing at registration protects against later modification; it does
        # not establish that the declaration existed before any scientific test.
        normalized["freeze_status"] = "unverified"
        normalized["protected_identity_verified"] = False
    return normalized
