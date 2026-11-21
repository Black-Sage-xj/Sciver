"""Bounded, read-only discovery of SDK-registered Reporter materials."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
import sys
from typing import Any, Mapping

from .runtime import write_json

SCHEMA = "theory-master/report-material-catalog/v1"
MAX_CHARS = 24_000
BOUNDARY = ("Materials record availability and same-invocation provenance, not per-Finding "
            "support or independent scientific inputs. Select relevant established results; "
            "do not reanalyse data. Empty files and logs are auxiliary, not scientific judgments.")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path: Path) -> bool:
    return path.is_file() and not any(p.is_symlink() for p in (path, *path.parents))


def prepare_catalog(path: Path, materials: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist complete metadata outside the prompt; pin legacy local references too."""
    rows = json.loads(json.dumps(materials))
    for row in rows:
        for asset in row.get("assets", []):
            local = Path(asset["path"]) if asset.get("path") else None
            if asset.get("status") == "available" and local is not None:
                if not _regular(local):
                    asset["status"] = "missing"
                else:
                    if not asset.get("sha256"):
                        asset["sha256"] = _hash(local)
                    asset["size"] = local.stat().st_size
    write_json(path, {"schema": SCHEMA, "materials": rows})
    return {"path": str(path.resolve()), "sha256": _hash(path), "overview": overview(rows)}


def _assets(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate file identities without changing the canonical Evidence records."""
    assets: dict[str, dict[str, Any]] = {}
    for row in materials:
        for asset in row.get("assets", []):
            key = str(asset.get("path") or asset.get("reference") or "")
            if not key:
                continue
            item = dict(asset)
            item["reference"] = key
            item["label"] = str(item.get("relative_path") or key)
            item["auxiliary"] = item.get("size") == 0 or Path(key).suffix.lower() == ".log"
            previous = assets.get(key)
            # Conflicting identities must not turn an unavailable asset into an
            # inspected result merely because another registration was available.
            if previous and (previous.get("sha256") != item.get("sha256")
                             or previous.get("status") != item.get("status")):
                item["status"] = "ambiguous"
            assets[key] = item
    return sorted(assets.values(), key=lambda a: a["label"])


def _directory(asset: Mapping[str, Any]) -> str:
    label = str(asset["label"])
    if not asset.get("relative_path"):
        return str(Path(label).parent) if asset.get("path") else "external"
    parts = Path(label).parts
    return parts[0] + "/" if len(parts) > 1 else "./"


def overview(materials: list[dict[str, Any]], *, offset: int = 0) -> dict[str, Any]:
    assets = _assets(materials)
    directories = sorted(Counter(_directory(a) for a in assets).items())
    start = max(0, offset)
    return {
        "total_files": len(assets),
        "nonempty_available_files": sum(a.get("status") == "available" and a.get("size") != 0
                                        for a in assets),
        "empty_files": sum(a.get("size") == 0 for a in assets),
        "log_files": sum(Path(a["reference"]).suffix.lower() == ".log" for a in assets),
        "kinds": dict(Counter(str(a.get("kind", "document")) for a in assets)),
        "availability": dict(Counter(str(a.get("status", "unknown")) for a in assets)),
        "directories": [{"directory": name, "files": count} for name, count in directories[start:start + 12]],
        "directory_count": len(directories),
        "next_offset": start + 12 if start + 12 < len(directories) else None,
    }


def material_access(context: Mapping[str, Any]) -> dict[str, Any]:
    """Only a bounded overview and navigation enter the initial Reporter prompt."""
    catalog = context.get("report_material_catalog")
    summary = catalog.get("overview") if isinstance(catalog, dict) else None
    if summary is None:
        materials = context.get("report_material_rows")
        if materials is None:
            from .report_materials import report_materials
            materials = report_materials(context)
        summary = overview(materials)
    result = {"overview": summary, "boundary": BOUNDARY}
    if isinstance(catalog, dict) and catalog.get("path") and catalog.get("sha256"):
        source = Path(__file__).resolve().parents[1]
        result["command_prefix"] = (
            f"PYTHONPATH={shlex.quote(str(source))}:${{PYTHONPATH:-}} "
            f"{shlex.quote(sys.executable)} -m theory_master.report_material_tools "
            f"--catalog {shlex.quote(str(catalog['path']))} --sha256 {shlex.quote(str(catalog['sha256']))}"
        )
        result["operations"] = {
            "overview": "overview [--offset N]",
            "list": "list [--directory DIR] [--query TEXT] [--kind KIND] [--offset N] [--limit N] [--include-auxiliary]",
            "read": "read REFERENCE [--offset N] [--max-chars N]",
        }
    return result


def _public_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {key: asset[key] for key in ("reference", "relative_path", "kind", "size", "status", "image_ref", "auxiliary")
            if key in asset}


def execute(catalog: Path, digest: str, operation: str, reference: str = "", *,
            directory: str = "", query: str = "", kind: str = "", offset: int = 0,
            limit: int = 20, max_chars: int = 12_000, include_auxiliary: bool = False) -> dict[str, Any]:
    """Read a pinned catalog and, on demand, one unchanged registered file."""
    try:
        if not _regular(catalog) or _hash(catalog) != digest:
            return {"status": "catalog_unavailable_or_changed"}
        payload = json.loads(catalog.read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA:
            return {"status": "invalid_catalog"}
        materials = payload["materials"]
        start = max(0, offset)
        if operation == "overview":
            return {"status": "ok", **overview(materials, offset=start), "boundary": BOUNDARY}
        assets = _assets(materials)
        if operation == "list":
            matches = [a for a in assets
                       if (include_auxiliary or not a["auxiliary"])
                       and (not directory or a["label"].startswith(directory))
                       and (not query or query.casefold() in a["label"].casefold())
                       and (not kind or a.get("kind") == kind)]
            page = [_public_asset(a) for a in matches[start:start + max(1, min(40, limit))]]
            while len(page) > 1 and len(json.dumps(page, ensure_ascii=False)) > MAX_CHARS:
                page.pop()
            return {"status": "ok", "total_matches": len(matches), "offset": start, "assets": page,
                    "next_offset": start + len(page) if start + len(page) < len(matches) else None,
                    "auxiliary_hidden": not include_auxiliary}
        if operation != "read":
            return {"status": "invalid_operation"}
        matches = [a for a in assets if reference in (a["reference"], a.get("relative_path"))]
        if len(matches) != 1:
            return {"status": "unregistered_or_ambiguous"}
        asset = matches[0]
        metadata = _public_asset(asset)
        if asset.get("status") != "available":
            return {"status": asset.get("status", "unavailable"), "asset": metadata}
        path = Path(asset["path"])
        if not _regular(path):
            return {"status": "missing_or_symlink", "asset": metadata}
        expected = asset.get("sha256")
        if not expected or _hash(path) != expected:
            return {"status": "drifted", "asset": metadata}
        if asset.get("kind") == "figure":
            return {"status": "figure", "asset": metadata}
        with path.open(encoding="utf-8") as stream:
            remaining = start
            while remaining:
                skipped = stream.read(min(65_536, remaining))
                if not skipped:
                    break
                remaining -= len(skipped)
            count = max(1, min(MAX_CHARS, max_chars))
            content = stream.read(count)
            more = bool(stream.read(1))
        # Recheck after reading as well: a concurrent rewrite must not be
        # returned as the frozen scientific material.
        if not _regular(path) or _hash(path) != expected:
            return {"status": "drifted", "asset": metadata}
        return {"status": "ok", "asset": metadata, "offset": start, "content": content,
                "next_offset": start + len(content) if more else None}
    except UnicodeError:
        return {"status": "binary_material", "message": "Use the registered file's native viewer; no text was decoded."}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "unavailable", "error": str(exc)[:1000]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("operation", choices=("overview", "list", "read"))
    parser.add_argument("reference", nargs="?", default="")
    parser.add_argument("--directory", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=12_000)
    parser.add_argument("--include-auxiliary", action="store_true")
    args = vars(parser.parse_args(argv))
    args["digest"] = args.pop("sha256")
    result = execute(**args)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("status") in {"ok", "figure", "external", "binary_material"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
