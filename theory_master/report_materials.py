"""Read-only presentation assets from the admitted Evidence manifest.

This module resolves references and formats links; it does not extract new
scientific results, rank evidence, or change its admissibility.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlsplit

from .evidence import adjudicable_manifest_records

FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_ANCHORS = ("cycle_workspace", "cycle_dir", "task_home", "research_root",
            "legacy_research_root", "benchmark_root", "notes_vault_root", "repo_root")
_CODE = re.compile(r"(^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=fence)[ \t]*$|\Z)|(?P<tick>`+)[^\n]*?(?P=tick))", re.M | re.S)
_IMAGE = re.compile(
    r'!\[(?P<alt>(?:\\.|[^\]\\])*)\]\(\s*'
    r'(?:<(?P<angle>[^>\n]+)>|(?P<url>(?:[^\s()]|\([^\s()]*\))+))'
    r'(?:[ \t]+(?P<title>"[^"\n]*"|\x27[^\x27\n]*\x27))?[ \t]*\)'
    r'|!\[\[(?P<wiki>[^\]\n]+)\]\]'
)


def _roots(context: Mapping[str, Any]) -> list[Path]:
    return list(dict.fromkeys(Path(str(context[key])).resolve()
                             for key in _ANCHORS if context.get(key)))


def _kind(reference: str, role: str) -> str:
    suffix = Path(urlsplit(reference).path).suffix.lower()
    if suffix in FIGURE_SUFFIXES:
        return "figure"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if role == "implementation" or suffix in {".py", ".sh", ".jl", ".r", ".ipynb"}:
        return "script"
    return "data" if role == "input" or suffix in {".npz", ".npy", ".h5", ".parquet"} else "document"


def report_materials(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose registered pointers only, with explicit local availability.

    Relative historical pointers must resolve uniquely against SDK anchors.
    File contents, raw result arrays and runtime records are never injected.
    """
    roots = _roots(context)
    rows = []
    for record in adjudicable_manifest_records(context.get("evidence_manifest")):
        pointers = record.get("artifact_pointers") or []
        if isinstance(pointers, str):
            pointers = [pointers]
        roles = {str(item.get("path")): str(item.get("role") or "")
                 for item in record.get("artifact_roles") or [] if isinstance(item, dict)}
        assets = []
        for reference in dict.fromkeys(p for p in pointers if isinstance(p, str) and p.strip()):
            role = roles.get(reference, "")
            asset = {"reference": reference, "kind": _kind(reference, role)}
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
                candidates = [path] if path.is_absolute() else [root / path for root in roots]
                matches = set()
                for candidate in candidates:
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
    return rows


def normalize_report_images(body: str, materials: list[dict[str, Any]],
                            context: Mapping[str, Any], *, note_dir: Path | None = None
                            ) -> tuple[str, list[str]]:
    """Bind Markdown/Obsidian images to registered figures; preserve code text.

    Notes use document-relative paths; graph summaries use absolute paths so
    that they remain readable without a document origin.
    """
    figures = [asset for row in materials for asset in row["assets"] if asset["kind"] == "figure"]
    roots = _roots(context)
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        wiki = match["wiki"]
        target = wiki.split("|", 1)[0] if wiki else match["angle"] or match["url"]
        if wiki and target.split("#", 1)[0].lower().endswith(".md"):
            return match.group()  # SDK freezes document/section embeds separately.
        decoded = unquote(target)
        hits = [a for a in figures if target in (a["reference"], a.get("image_ref"), a.get("path"))
                or decoded in (a["reference"], a.get("path"))]
        if not hits and not urlsplit(decoded).scheme:
            paths = set()
            for root in roots:
                try:
                    paths.add((root / decoded).resolve().as_posix())
                except (OSError, ValueError, RuntimeError):
                    continue
            hits = [a for a in figures if a.get("path") in paths]
        available = {a.get("path") or a["reference"]: a for a in hits
                     if a["status"] in {"available", "external"}}
        if len(available) != 1:
            warnings.append(target)
            alt = (wiki.split("|", 1)[-1] if "|" in (wiki or "") else wiki) if wiki else match["alt"]
            if wiki and alt and re.fullmatch(r"\d+(?:x\d+)?", alt):
                alt = "图片"
            return (alt or "图片") + "（图片引用不可用，见复现索引）"
        asset = next(iter(available.values()))
        path = asset.get("path")
        final = os.path.relpath(path, note_dir) if path and note_dir else path or asset["reference"]
        if wiki:
            label = "|" + wiki.split("|", 1)[1] if "|" in wiki else ""
            return "![[" + final + label + "]]"
        title = " " + match["title"] if match["title"] else ""
        encoded = quote(final, safe="/:") if path else asset["image_ref"]
        return "![" + match["alt"] + "](" + encoded + title + ")"

    parts, end = [], 0
    for code in _CODE.finditer(body):
        parts.extend((_IMAGE.sub(replace, body[end:code.start()]), code.group()))
        end = code.end()
    parts.append(_IMAGE.sub(replace, body[end:]))
    return "".join(parts), list(dict.fromkeys(warnings))


def material_index(materials: list[dict[str, Any]], warnings: list[str]) -> list[str]:
    lines = ["### 数据、完整表格与分析脚本", ""]
    for row in materials:
        for asset in row["assets"]:
            path = asset.get("path") or asset["reference"]
            safe_path = path.replace("`", "&#96;").replace("\n", " ")
            status = {"missing": "文件缺失", "ambiguous": "路径有歧义",
                      "external": "外部来源，未检查可访问性", "unsupported": "不支持的引用类型"}.get(asset["status"])
            lines.append(f"- {row['evidence_id']} · {asset['kind']}：`{safe_path}`" + (f"（{status}）" if status else ""))
    if len(lines) == 2:
        lines.append("本轮可用于报告的 Evidence 未登记独立素材文件。")
    if warnings:
        lines.extend(["", "图片引用未能匹配唯一可用的已登记素材："])
        lines.extend("- `" + path.replace("`", "&#96;").replace("\n", " ") + "`" for path in warnings)
    return lines
