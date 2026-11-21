"""Task-bound publication and read APIs for the common Markdown document reader.

Only publication artifacts and explicitly frozen sources are exposed. Model
invocation inputs, authentication, and live research paths are never web roots.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from .notebook import atomic_json
from .research_report import sections, section_text

SCHEMA = "sciver/final-report-publication/v1"
VERSION = re.compile(r"[a-f0-9]{64}")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 24 * 1024 * 1024
MAX_PUBLICATION_BYTES = 128 * 1024 * 1024
MAX_PUBLICATION_FILES = 1024


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("invalid report asset path")
    value = unquote(value)
    if "\x00" in value or "\\" in value:
        raise ValueError("invalid report asset path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
        raise ValueError("report asset path is outside this publication")
    if urlsplit(value).scheme or "?" in value or "#" in value:
        raise ValueError("invalid report asset path")
    return path.as_posix()


def _regular(path: Path) -> bool:
    return path.is_file() and not any(part.is_symlink() for part in (path, *path.parents))


def _read(path: Path, maximum: int) -> bytes:
    if not _regular(path):
        raise FileNotFoundError("report artifact is missing or unsafe")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("report artifact exceeds the preview size limit")
    return raw


def _json(path: Path, maximum: int = MAX_TEXT_BYTES) -> dict[str, Any]:
    value = json.loads(_read(path, maximum))
    if not isinstance(value, dict):
        raise ValueError("invalid report metadata")
    return value


def _registry(research_root: Path) -> Path:
    research_root = Path(research_root).absolute()
    if research_root.resolve() != research_root:
        raise ValueError("research root must be an exact task binding")
    root = research_root / ".theory-master" / "final-reports"
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("unsafe final report registry")
    return root


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {"version": manifest["version"], "title": manifest["title"],
            "state": manifest["state"], "available": True,
            "created_at": manifest.get("created_at", "")}


def _manifest(directory: Path, research_root: Path, *, verify_sources: bool) -> dict[str, Any]:
    result = _json(directory / "result.json")
    if result.get("state") not in {"ready", "needs_revision"}:
        raise ValueError("report has no readable completed draft")
    snapshot = _json(directory / "source-snapshot.json", MAX_SNAPSHOT_BYTES)
    if (snapshot.get("sources", {}).get("research_root") != str(Path(research_root).resolve())
            or result.get("snapshot_fingerprint") != snapshot.get("fingerprint")):
        raise ValueError("report sources belong to a different task")
    body = _read(directory / "report.md", MAX_TEXT_BYTES)
    if result.get("report_sha256") and result["report_sha256"] != _hash(body):
        raise ValueError("report body changed after publication validation")
    report = _json(directory / "report.json", 4 * MAX_TEXT_BYTES)
    if isinstance(report.get("markdown"), str) and report["markdown"] != body.decode("utf-8"):
        raise ValueError("structured report and canonical Markdown do not match")
    review_raw = _read(directory / "review.json", MAX_TEXT_BYTES)
    artifacts: dict[str, dict[str, Any]] = {}
    for filename in ("report.md", "report.json", "result.json", "review.json", "source-snapshot.json"):
        maximum = MAX_SNAPSHOT_BYTES if filename == "source-snapshot.json" else 4 * MAX_TEXT_BYTES
        raw = _read(directory / filename, maximum)
        artifacts[filename] = {"sha256": _hash(raw), "size": len(raw)}
    for filename in ("amendment.json", "validation.json"):
        if (directory / filename).exists():
            raw = _read(directory / filename, MAX_TEXT_BYTES)
            artifacts[filename] = {"sha256": _hash(raw), "size": len(raw)}
    if (directory / "trajectory.svg").exists():
        raw = _read(directory / "trajectory.svg", MAX_IMAGE_BYTES)
        artifacts["trajectory.svg"] = {"sha256": _hash(raw), "size": len(raw)}
    by_source: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for row in snapshot.get("documents", []) + snapshot.get("figures", []):
        if not isinstance(row, dict) or not row.get("frozen_path"):
            continue
        path = _relative("sources/" + str(row["frozen_path"]))
        expected = row.get("frozen_sha256") or row.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("frozen report source has no content identity")
        if verify_sources:
            raw = _read(directory / path, MAX_IMAGE_BYTES)
            if _hash(raw) != expected:
                raise ValueError("frozen report source changed before publication")
            size = len(raw)
        else:
            size = int(row.get("size") or 0)
        artifacts[path] = {"sha256": expected, "size": size}
        record = {"kind": row.get("kind", "document"), "id": row.get("id") or row.get("source_ref"),
                  "path": path, "title": Path(str(row.get("source_path") or row.get("reference") or path)).name,
                  "original_path": row.get("source_path", "")}
        for key in (row.get("source_ref"), row.get("reference"), row.get("id"), row.get("source_path")):
            if isinstance(key, str) and key:
                by_source[key] = record
                if key in aliases and aliases[key] != path:
                    aliases.pop(key)
                    ambiguous_aliases.add(key)
                elif key not in ambiguous_aliases:
                    aliases[key] = path
    if len(artifacts) > MAX_PUBLICATION_FILES or sum(row["size"] for row in artifacts.values()) > MAX_PUBLICATION_BYTES:
        raise ValueError("report publication exceeds the file or byte limit")
    source_index = {}
    recorded_index = snapshot.get("source_index", {})
    if not isinstance(recorded_index, dict) or any(not isinstance(row, dict) for row in recorded_index.values()):
        raise ValueError("report source index is invalid")
    for ref, row in recorded_index.items():
        source_index[ref] = {"kind": row.get("kind", "record"), "id": row.get("id") or ref,
                             "title": row.get("title") or ref}
        if ref in by_source:
            source_index[ref].update({k: v for k, v in by_source[ref].items() if k != "original_path"})
    identity = {"task": str(Path(research_root).resolve()), "snapshot": snapshot["fingerprint"],
                "body": _hash(body), "review": _hash(review_raw),
                "prompts": result.get("prompt_metadata"),
                "amendment": artifacts.get("amendment.json", {}).get("sha256")}
    version = _hash(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
    return {"schema": SCHEMA, "version": version, "research_root": str(Path(research_root).resolve()),
            "state": result["state"], "title": str(report.get("title") or "科研总报告"),
            "snapshot_fingerprint": snapshot["fingerprint"], "body_source_sha256": _hash(body),
            "created_at": datetime.fromtimestamp((directory / "result.json").stat().st_mtime, timezone.utc).isoformat(),
            "artifacts": artifacts, "source_index": source_index,
            "source_files": by_source, "original_path_aliases": aliases,
            "original_vault_root": str(Path(snapshot["original_question"]["path"]).parent)
                if isinstance(snapshot.get("original_question"), dict) and snapshot["original_question"].get("path") else None}


def publish_report(research_root: Path, source_dir: Path) -> dict[str, Any]:
    """Import one validated report into a task-owned immutable publication.

    Called by a trusted local operator or generator, never with HTTP paths.
    The snapshot's task binding must match before any source can be copied.
    """
    registry = _registry(research_root)
    source = Path(source_dir).absolute()
    if source.resolve() != source:
        raise ValueError("unsafe report import directory")
    manifest = _manifest(source, research_root, verify_sources=True)
    publications = registry / "publications"
    if publications.is_symlink():
        raise ValueError("unsafe report publications directory")
    publications.mkdir(parents=True, exist_ok=True)
    destination = publications / manifest["version"]
    if destination.exists():
        if destination.is_symlink():
            raise ValueError("unsafe existing report publication")
        previous = _json(destination / "publication.json", MAX_SNAPSHOT_BYTES)
        if (previous.get("schema") != SCHEMA or previous.get("version") != manifest["version"]
                or previous.get("research_root") != str(Path(research_root).resolve())):
            raise ValueError("conflicting report publication")
        for relative, expected in previous.get("artifacts", {}).items():
            raw = _read(destination / _relative(relative), MAX_SNAPSHOT_BYTES)
            if _hash(raw) != expected.get("sha256"):
                raise ValueError("existing immutable report publication changed")
        return {**_summary(previous), "directory": str(destination)}
    temporary = Path(tempfile.mkdtemp(prefix=".publication-", dir=publications))
    try:
        for relative, expected in manifest["artifacts"].items():
            raw = _read(source / relative, MAX_SNAPSHOT_BYTES)
            if _hash(raw) != expected["sha256"]:
                raise ValueError("report artifact changed during publication")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        manifest["created_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(temporary / "publication.json", manifest)
        try:
            temporary.rename(destination)
        except FileExistsError:
            previous = _json(destination / "publication.json", MAX_SNAPSHOT_BYTES)
            if previous.get("version") != manifest["version"]:
                raise ValueError("conflicting concurrent publication")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {**_summary(manifest), "directory": str(destination)}


def _publications(research_root: Path) -> tuple[list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    registry = _registry(research_root)
    found: dict[str, tuple[dict[str, Any], Path]] = {}
    jobs = []
    publications = registry / "publications"
    if publications.is_dir() and not publications.is_symlink():
        for directory in sorted(publications.iterdir())[:256]:
            if not VERSION.fullmatch(directory.name) or directory.is_symlink():
                continue
            try:
                manifest = _json(directory / "publication.json", MAX_SNAPSHOT_BYTES)
                if (manifest.get("schema") != SCHEMA or manifest.get("version") != directory.name
                        or manifest.get("research_root") != str(Path(research_root).resolve())
                        or manifest.get("state") not in {"ready", "needs_revision"}
                        or not isinstance(manifest.get("title"), str)
                        or not isinstance(manifest.get("artifacts"), dict)
                        or not isinstance(manifest.get("source_index"), dict)):
                    continue
                found[manifest["version"]] = (manifest, directory)
            except (OSError, ValueError, TypeError):
                continue
    jobs_dir = registry / "jobs"
    versions_dir = registry / "versions"
    remaining_attempts = 256
    if jobs_dir.is_dir() and not jobs_dir.is_symlink():
        for path in sorted(jobs_dir.glob("*.json"))[:256]:
            try:
                if not VERSION.fullmatch(path.stem):
                    continue
                job = _json(path)
                if (job.get("schema") != "sciver/final-report-job/v1" or job.get("job_id") != path.stem
                        or job.get("research_root") != str(Path(research_root).resolve())):
                    continue
                jobs.append(job)
                # A regeneration resets output_dir while its new attempt runs.
                # Discover completed attempts through the validated job binding
                # so earlier versions remain readable throughout that window.
                directory = versions_dir / path.stem
                if (versions_dir.is_symlink() or directory.is_symlink() or not directory.is_dir()
                        or directory.resolve() != directory or remaining_attempts <= 0):
                    continue
                attempts = sorted((candidate for candidate in directory.iterdir()
                    if re.fullmatch(r"attempt-[0-9]{4,8}", candidate.name)),
                    key=lambda candidate: int(candidate.name.partition("-")[2]), reverse=True)
                for candidate in attempts[:remaining_attempts]:
                    remaining_attempts -= 1
                    if candidate.is_symlink() or not candidate.is_dir() or candidate.resolve() != candidate:
                        continue
                    try:
                        manifest = _manifest(candidate, research_root, verify_sources=False)
                        found.setdefault(manifest["version"], (manifest, candidate))
                    except (OSError, ValueError, TypeError, KeyError):
                        continue
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return sorted(found.values(), key=lambda row: row[0].get("created_at", ""), reverse=True), jobs


def final_report_descriptor(research_root: Path) -> dict[str, Any]:
    publications, jobs = _publications(research_root)
    versions = [_summary(manifest) for manifest, _ in publications]
    latest = max(jobs, key=lambda row: str(row.get("updated_at") or ""), default={})
    if versions:
        return {**versions[0], "versions": versions, "latest_job_state": latest.get("state")}
    state = latest.get("state") or "missing"
    return {"available": False, "state": state, "version": None, "title": "科研总报告", "versions": [],
            "message": {"queued": "总报告已排队", "running": "总报告正在生成", "failed": "总报告生成失败，已有科研结果仍可阅读",
                        "ready": "总报告已生成，尚未发布到任务阅读区", "needs_revision": "总报告已有草稿，尚未发布到任务阅读区"}.get(state, "此任务尚无总报告")}


def _publication(research_root: Path, version: str = "") -> tuple[dict[str, Any], Path]:
    if version and not VERSION.fullmatch(version):
        raise ValueError("invalid final report version")
    publications, _ = _publications(research_root)
    for manifest, directory in publications:
        if not version or manifest["version"] == version:
            return manifest, directory
    raise FileNotFoundError("final report publication is not available")


def _artifact(manifest: dict[str, Any], directory: Path, relative: str, maximum: int) -> bytes:
    relative = _relative(relative)
    expected = manifest.get("artifacts", {}).get(relative)
    if not isinstance(expected, dict):
        raise FileNotFoundError("asset is not registered in this report")
    raw = _read(directory / relative, maximum)
    if _hash(raw) != expected.get("sha256"):
        raise ValueError("published report artifact changed")
    return raw


def _page(content: str, *, ident: str, title: str, manifest: dict[str, Any], section: str, offset: int,
          max_chars: int = 1024 * 1024) -> dict[str, Any]:
    selected = section_text(content, section)
    if offset < 0 or offset > len(selected):
        raise ValueError("invalid report offset")
    end = min(len(selected), offset + max_chars)
    return {"id": ident, "title": title, "content": selected[offset:end], "section": section,
            "source_sha256": _hash(content.encode()), "body_source_sha256": manifest["body_source_sha256"],
            "sections": [{k: row[k] for k in ("id", "title", "level")} for row in sections(content)],
            "next_offset": end if end < len(selected) else None,
            "available": True, "state": manifest["state"], "version": manifest["version"],
            "source_index": manifest.get("source_index", {})}


def _lineage_width(raw: bytes) -> int:
    """Use the fixed SVG drawing width without executing or expanding XML."""
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return 800
    try:
        root = ET.fromstring(raw)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            return 800
        viewbox = [float(value) for value in re.split(r"[\s,]+", root.get("viewBox", "").strip())]
        if len(viewbox) != 4 or not all(math.isfinite(value) for value in viewbox) or viewbox[2] <= 0:
            return 800
        return max(1, min(1600, int(math.ceil(viewbox[2]))))
    except (ET.ParseError, ValueError, OverflowError):
        return 800


def _research_story_svg(manifest: dict[str, Any], directory: Path) -> bytes:
    from graph_research.final_report_story import story_svg
    report = json.loads(_artifact(manifest, directory, "report.json", 4 * MAX_TEXT_BYTES))
    snapshot = json.loads(_artifact(manifest, directory, "source-snapshot.json", MAX_SNAPSHOT_BYTES))
    return story_svg(report, snapshot).encode("utf-8")


def _report_reading(manifest: dict[str, Any], directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from graph_research.final_report_reading import cycle_reading
    report = json.loads(_artifact(manifest, directory, "report.json", 4 * MAX_TEXT_BYTES))
    report["markdown"] = _artifact(manifest, directory, "report.md", MAX_TEXT_BYTES).decode("utf-8")
    snapshot = json.loads(_artifact(manifest, directory, "source-snapshot.json", MAX_SNAPSHOT_BYTES))
    return cycle_reading(report, snapshot), snapshot


def read_final_report(research_root: Path, *, version: str = "", section: str = "", offset: int = 0) -> dict[str, Any]:
    descriptor = final_report_descriptor(research_root)
    if not descriptor["available"] and not version:
        return {**descriptor, "id": "", "content": "", "sections": [], "next_offset": None}
    manifest, directory = _publication(research_root, version)
    reading, _ = _report_reading(manifest, directory)
    content = reading["markdown"]
    story = _research_story_svg(manifest, directory)
    if story:
        width = _lineage_width(story)
        content += ("\n\n## 研究主线概览\n\n"
                    '<div class="research-lineage-image research-story-image" role="region" tabindex="0" aria-label="研究主线图">'
                    f'<img src="research-story.svg" width="{width}" alt="研究主线：关键阶段、发现与转向"></div>\n\n'
                    "沿关键问题、检验与转向梳理研究进程；箭头表示下一关键阶段。"
                    "各阶段对应的记录可按 Cycle 查看。\n")
    result = _page(content, ident=f"final-reports/{manifest['version']}/report.md", title=manifest["title"],
                   manifest=manifest, section=section, offset=offset)
    return {**result, "source_index": reading["cycles"],
            "versions": descriptor["versions"], "kind": "final-report"}


def read_final_report_source(research_root: Path, ref: str, *, version: str = "", section: str = "", offset: int = 0) -> dict[str, Any]:
    manifest, directory = _publication(research_root, version)
    index = manifest.get("source_index", {})
    cycle = None
    if ref.startswith("cycle:"):
        reading, snapshot = _report_reading(manifest, directory)
        cycle = reading["cycles"].get(ref)
        if cycle is None:
            raise FileNotFoundError("this Cycle is not recorded in this report")
        lookup = cycle.get("source_ref")
        row = index.get(lookup, {})
        if not row.get("path"):
            records = [record for field in ("outcomes", "preservations") for record in snapshot.get(field, [])
                       if isinstance(record, dict) and str(record.get("cycle_id")) == str(cycle["cycle_id"])]
            content = f'# {cycle["title"]}\n\n```json\n{json.dumps(records, ensure_ascii=False, indent=2)}\n```\n'
            return {**_page(content, ident=f"final-reports/{manifest['version']}/report.md", title=cycle["title"],
                           manifest=manifest, section=section, offset=offset),
                    "source_index": {**index, **reading["cycles"]},
                    "kind": "final-report-source", "source_ref": ref}
    elif ref not in index:
        raise FileNotFoundError("source reference is not registered in this report")
    else:
        row = index[ref]
    path = row.get("path")
    ident = f"final-reports/{manifest['version']}/report.md"
    title = str(cycle["title"] if cycle else row.get("title") or ref)
    if path:
        relative = _relative(path)
        suffix = Path(relative).suffix.lower()
        if suffix in IMAGE_TYPES:
            content = f"# {title}\n\n![来源图片]({relative})\n"
        else:
            content = _artifact(manifest, directory, relative, MAX_TEXT_BYTES).decode("utf-8")
            if suffix not in {".md", ".markdown"}:
                language = {".json": "json", ".py": "python", ".sh": "bash"}.get(suffix, "")
                content = f"# {title}\n\n```{language}\n{content}\n```\n"
            ident = f"final-reports/{manifest['version']}/{relative}"
    else:
        snapshot = json.loads(_artifact(manifest, directory, "source-snapshot.json", MAX_SNAPSHOT_BYTES))
        record = next((record for field in ("hypotheses", "outcomes", "preservations", "lineage", "external_sources", "evidence_records", "goal_gate_history")
                       for record in snapshot.get(field, []) if isinstance(record, dict)
                       and ref in {record.get("id"), record.get("source_ref"), record.get("evidence_id")}), None)
        terminal = snapshot.get("goal_terminal")
        if ref == "goal:terminal" and isinstance(terminal, dict) and terminal.get("source_ref") == ref:
            record = terminal
        if record is None:
            if ref == "research_plan":
                content = str(snapshot.get("original_question", {}).get("content") or "")
            else:
                raise FileNotFoundError("this source has no frozen readable content")
        else:
            title = str(record.get("title") or ref)
            content = f"# {title}\n\n```json\n{json.dumps(record, ensure_ascii=False, indent=2)}\n```\n"
    result = _page(content, ident=ident, title=title, manifest=manifest, section=section, offset=offset)
    if cycle:
        result["source_index"] = {**index, **reading["cycles"]}
    return {**result, "kind": "final-report-source", "source_ref": ref}


def final_report_image(research_root: Path, source: str, target: str, *, vault: bool = False) -> tuple[bytes, str]:
    pieces = source.split("/")
    if len(pieces) < 3 or pieces[0] != "final-reports" or not VERSION.fullmatch(pieces[1]):
        raise ValueError("invalid final report image source")
    manifest, directory = _publication(research_root, pieces[1])
    source_path = PurePosixPath("/".join(pieces[2:]))
    if source_path.as_posix() not in manifest["artifacts"]:
        raise ValueError("image source is not registered in this report")
    # Historical frozen Markdown can still contain its original relative image
    # names. Resolve only through captured aliases; never inspect live paths.
    target = unquote(target)
    if "\x00" in target or "\\" in target or urlsplit(target).scheme or "?" in target or "#" in target:
        raise ValueError("invalid final report image target")
    if target == "research-story.svg" and source_path.as_posix() == "report.md":
        story = _research_story_svg(manifest, directory)
        if not story:
            raise FileNotFoundError("this report has no recorded research story")
        return story, "image/svg+xml"
    aliases = manifest.get("original_path_aliases", {})
    mapped = None
    if vault and manifest.get("original_vault_root"):
        mapped = aliases.get(os.path.normpath(os.path.join(manifest["original_vault_root"], target)))
    mapped = mapped or aliases.get(target)
    original = next((row.get("original_path") for row in manifest.get("source_files", {}).values()
                     if row.get("path") == source_path.as_posix() and row.get("original_path")), None)
    if original and mapped is None:
        mapped = aliases.get(os.path.normpath(os.path.join(os.path.dirname(original), target)))
    if mapped and Path(mapped).suffix.lower() in IMAGE_TYPES:
        return _artifact(manifest, directory, mapped, MAX_IMAGE_BYTES), IMAGE_TYPES[Path(mapped).suffix.lower()]
    target = _relative(target)
    candidates = [target]
    if source_path.as_posix() != "report.md":
        candidates.insert(0, _relative((source_path.parent / target).as_posix()))
    for relative in candidates:
        mime = IMAGE_TYPES.get(Path(relative).suffix.lower())
        if not mime or relative not in manifest["artifacts"]:
            continue
        return _artifact(manifest, directory, relative, MAX_IMAGE_BYTES), mime
    raise FileNotFoundError("image is not registered in this report")
