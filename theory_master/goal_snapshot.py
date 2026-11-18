"""Frozen Goal Judge input, with content binding for a later short-lock commit."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from os.path import commonpath, relpath
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .delivery_repair import _canonical
from .goal_materials import (
    MATERIAL_PATH_KEYS,
    goal_material_sources,
    material_roots,
    resolve_material,
    rewrite_markdown_navigation,
)
from .runtime import write_json


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class GoalSnapshotSeed:
    """Captured while writers are excluded; large file copies happen afterwards."""

    plan: bytes
    graph: bytes
    agent_graph: bytes
    delivery: bytes
    access: bytes
    model: bytes


@dataclass(frozen=True)
class GoalAuthoritySnapshot:
    root: Path
    plan_path: Path
    graph_path: Path
    agent_graph_path: Path
    manifest_path: Path
    manifest_sha256: str
    access_json: bytes

    @classmethod
    def restore(
        cls, manifest_path: Path, expected_sha256: str
    ) -> GoalAuthoritySnapshot:
        root = manifest_path.parent
        if _sha(manifest_path) != expected_sha256:
            raise ValueError("Goal snapshot manifest changed during assessment")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_path = root / manifest.get("plan_path", "research_plan.md")
        if not plan_path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Goal snapshot plan leaves its frozen directory")
        snapshot = cls(
            root,
            plan_path,
            root / "science_graph.json",
            root / "agent_graph.json",
            manifest_path,
            expected_sha256,
            (root / "access.json").read_bytes(),
        )
        snapshot.validate()
        return snapshot

    @property
    def access(self) -> dict[str, Any]:
        return json.loads(self.access_json)

    def agent_view(self) -> dict[str, Any]:
        return json.loads(self.agent_graph_path.read_text(encoding="utf-8"))

    def validate(self, *, compare_sources: bool = True) -> None:
        if _sha(self.manifest_path) != self.manifest_sha256:
            raise ValueError("Goal snapshot manifest changed during assessment")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        for row in manifest["files"]:
            path = self.root / row["path"]
            if path.is_symlink() or not path.is_file() or _sha(path) != row["sha256"]:
                raise ValueError("Goal snapshot contents changed during assessment")
        if compare_sources:
            for row in manifest["sources"]:
                path = Path(row["path"])
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or _sha(path) != row["sha256"]
                ):
                    raise ValueError("Goal delivery source changed during assessment")


def materialize_goal_snapshot(
    spec: Any, cycle_dir: Path, seed: GoalSnapshotSeed
) -> GoalAuthoritySnapshot:
    """Make independent copies and rewrite every navigable local material reference.

    The manifest retains original paths only for SDK CAS. The Judge receives
    frozen paths; paths embedded in source prose remain provenance, not access.
    """
    root = cycle_dir / "goal_snapshots" / uuid.uuid4().hex
    root.mkdir(parents=True)
    allowed = material_roots(spec)
    layout_root = Path(commonpath(allowed))
    payload = json.loads(seed.delivery)
    catalog = payload.get("goal_material_sources")
    if catalog is None:  # Older persisted callers still use the same boundary.
        catalog = goal_material_sources(spec, [json.loads(seed.agent_graph), payload])
    registered = {
        row["path"]: row
        for row in catalog
        if row.get("status") in {"available", "directory"}
    }
    by_reference = {row["reference"]: row for row in catalog}
    sources: dict[str, str] = {}
    copied: dict[str, str] = {}
    unavailable: dict[tuple[str, str], dict[str, str]] = {}

    def target_for(path: Path) -> Path:
        # One mirrored tree preserves ../ and sibling navigation between all
        # registered files, including the captured Research Plan. Never flatten
        # documents and scripts to unrelated random filenames.
        return root / "files" / path.resolve().relative_to(layout_root)

    def rewrite_navigation(target: Path) -> None:
        if target.suffix.lower() not in {".md", ".markdown", ".mdown"}:
            return

        def destination(original: str) -> str:
            decoded = re.sub(r"\\([\\`*{}\[\]()#+.!_> -])", r"\1", original)
            parsed = urlsplit(decoded)
            if (parsed.scheme and parsed.scheme != "file") or (
                parsed.netloc and parsed.scheme != "file"
            ):
                return original
            path = Path(unquote(parsed.path))
            if not path.is_absolute():
                return original
            resolved = path.resolve()
            if str(resolved) not in registered and not path.suffix:
                resolved = path.with_suffix(".md").resolve()
            safe = not any(part.is_symlink() for part in (path, *path.parents))
            if (
                str(resolved) in registered
                and safe
                and parsed.netloc in {"", "localhost"}
            ):
                frozen = target_for(resolved)
            else:
                frozen = Path(
                    inaccessible(original, "unregistered_absolute_markdown_target")
                )
            relative = quote(Path(relpath(frozen, target.parent)).as_posix(), safe="/")
            return urlunsplit(("", "", relative, parsed.query, parsed.fragment))

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return
        rewritten = rewrite_markdown_navigation(text, destination)
        if rewritten != text:
            target.write_text(rewritten, encoding="utf-8")

    def freeze(path: Path) -> str:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(base) for base in allowed):
            raise ValueError(
                f"Goal snapshot material leaves registered task roots: {path}"
            )
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Goal snapshot material traverses a symlink: {path}")
        if str(resolved) in copied:
            return copied[str(resolved)]
        target = target_for(resolved)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with path.open("rb") as stream, target.open("xb") as output:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                output.write(chunk)
        sources[str(resolved)] = digest.hexdigest()
        copied[str(resolved)] = str(target)
        rewrite_navigation(target)
        target.chmod(0o444)
        return str(target)

    def inaccessible(value: str, reason: str) -> str:
        target = root / "unavailable" / hashlib.sha256(value.encode()).hexdigest()
        unavailable[(value, reason)] = {
            "reference": value,
            "reason": reason,
            "frozen_path": str(target),
        }
        return str(target)

    def rewrite(value: Any, key: str = "", provenance: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                name: rewrite(
                    item, name, provenance or name in {"goal_gate", "goal_gate_history"}
                )
                for name, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item, key, provenance) for item in value]
        if not isinstance(value, str) or key not in MATERIAL_PATH_KEYS or not value:
            return value
        if value.startswith(("https://", "http://")):
            return value  # Source identity only; the snapshot access rule forbids live retrieval.
        if provenance:
            return inaccessible(value, "prior_goal_assessment_provenance")
        row = by_reference.get(value) or resolve_material(spec, value)
        if row.get("path") not in registered:
            return inaccessible(value, row.get("status", "not_registered"))
        path = Path(row["path"])
        if row["status"] == "available":
            return freeze(path)
        target = target_for(path)
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

    plan = target_for(Path(spec.document))
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_bytes(seed.plan)
    plan_source = str(Path(spec.document).resolve())
    registered[plan_source] = {"path": plan_source, "status": "available"}
    copied[plan_source] = str(plan)
    sources[plan_source] = hashlib.sha256(seed.plan).hexdigest()
    rewrite_navigation(plan)
    model = root / "scientific_model.json"
    model.write_bytes(seed.model)
    graph = root / "science_graph.json"
    write_json(graph, rewrite(json.loads(seed.graph)))
    view = rewrite(json.loads(seed.agent_graph))
    rule = (
        "Assess only this immutable snapshot. Read its frozen Research Plan, graph and delivery files. "
        "Original paths and URLs in captured prose are provenance; do not follow live authorities or retrieve "
        "new sources. Apply the existing Final Gate unchanged."
    )
    view["snapshot_boundary"] = rule
    agent_graph = root / "agent_graph.json"
    write_json(agent_graph, view)
    payload = rewrite(payload)
    revision_path = root / "delivery_revision.json"
    write_json(revision_path, payload)
    index_path = root / "delivery_material_index.json"
    write_json(index_path, payload["material_index"])
    access = {
        **json.loads(seed.access),
        "revision_path": str(revision_path),
        "index_path": str(index_path),
        "snapshot_boundary": rule,
        "scientific_model_snapshot": str(model),
    }
    (root / "access.json").write_bytes(_canonical(access))
    files = []
    for path in root.rglob("*"):
        if path.is_file():
            files.append(
                {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}
            )
            path.chmod(0o444)
    manifest = root / "manifest.json"
    write_json(
        manifest,
        {
            "schema": "theory-master/goal-authority-snapshot/v1",
            "plan_path": plan.relative_to(root).as_posix(),
            "files": files,
            "sources": [{"path": p, "sha256": h} for p, h in sorted(sources.items())],
            "unavailable_references": [unavailable[key] for key in sorted(unavailable)],
        },
    )
    manifest.chmod(0o444)
    return GoalAuthoritySnapshot(
        root, plan, graph, agent_graph, manifest, _sha(manifest), _canonical(access)
    )
