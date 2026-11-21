"""Read-only presentation assets from the admitted Evidence manifest.

This module resolves references and formats links; it does not extract new
scientific results, rank evidence, or change its admissibility.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote, urlsplit

from .evidence import adjudicable_manifest_records
from .delivery_materials import material_rows

REPORT_PROMPT_MATERIAL_LIMIT = 64 * 1024
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


def report_materials(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose admitted references and SDK batch materials through one index.

    Relative historical pointers must resolve uniquely against SDK anchors.
    File contents, raw result arrays and runtime records are never injected.
    """
    return material_rows(
        adjudicable_manifest_records(context.get("evidence_manifest")), roots=_roots(context),
        cycle_dir=Path(str(context["cycle_dir"])) if context.get("cycle_dir") else None,
    )


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _representative_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Offer navigable figures, summaries, and scripts before bulk results."""
    groups: dict[str, list[tuple[Path, dict[str, Any]]]] = {
        "figure": [], "summary": [], "script": [], "other": [],
    }
    for asset in assets:
        if asset.get("status") not in {"available", "external"}:
            continue
        path = Path(asset.get("relative_path") or asset["reference"])
        stem = path.stem.lower()
        kind = asset.get("kind")
        if kind in {"figure", "script"}:
            group = kind
        elif path.suffix.lower() in {".md", ".rst"} or re.search(
            r"readme|summary|conclusion|protocol|metrics|overview|index", stem
        ):
            group = "summary"
        else:
            group = "other"
        if _json_size(asset) <= 2048:
            groups[group].append((path, asset))
    result = []
    for kind, limit in (("figure", 6), ("summary", 6), ("script", 4), ("other", 2)):
        ranked = sorted(groups[kind], key=lambda item: (len(item[0].parts), item[0].as_posix()))
        result.extend(asset for _, asset in ranked[:limit])
    return result


def compact_report_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound only Reporter prompt navigation, retaining admitted source identities."""
    if _json_size(materials) <= REPORT_PROMPT_MATERIAL_LIMIT:
        return materials
    projected = []
    # Reserve room for the enclosing list and a summary of any omitted rows.
    available = REPORT_PROMPT_MATERIAL_LIMIT - 512
    omitted_rows = 0
    omitted_assets = 0
    # An exact manifest offers a way to inspect everything in an abbreviated
    # batch. Keep those sources before individual ungrouped references.
    for row in sorted(materials, key=lambda item: not (item.get("manifest_path") and item.get("manifest_sha256"))):
        assets = row["assets"]
        representative = _representative_assets(assets)
        compact = {key: row[key] for key in ("evidence_id", "evidence_batch_id", "relation",
                   "manifest_path", "manifest_sha256") if key in row}
        evidence_ids = row.get("evidence_ids") or []
        if evidence_ids:
            compact.update(evidence_ids=evidence_ids[:12], evidence_count=len(evidence_ids))
        producer = row.get("producer_invocation") or {}
        if producer:
            compact["producer_invocation"] = {key: producer[key] for key in ("invocation_id", "role", "stage")
                                                   if key in producer}
        integrity = row.get("integrity")
        if integrity is not None:
            compact["integrity"] = {"status": integrity.get("status"),
                                    **{f"{key}_count": len(integrity.get(key) or [])
                                       for key in ("checked_hashes", "missing_paths", "changed_paths", "errors")}}
        compact.update(assets=representative, asset_count=len(assets),
                       omitted_asset_count=len(assets) - len(representative))
        size = _json_size(compact) + 2
        if size > available:
            omitted_rows += 1
            omitted_assets += len(assets)
            continue
        projected.append(compact)
        available -= size
    if omitted_rows:
        projected.append({"omitted_material_row_count": omitted_rows,
                          "omitted_asset_count": omitted_assets, "assets": []})
    return projected


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
        label = row.get("evidence_id") or "同次执行素材 " + str(row.get("evidence_batch_id") or "")
        if row.get("relation") == "same_invocation":
            lines.append("关联范围：同次执行产物；不表示每个文件支持每条 Finding，也不证明输入独立。")
            integrity = row.get("integrity") or {}
            if integrity.get("status") == "drifted":
                lines.append("素材身份核验发现缺失、变化或记录错误，需核对原始登记。")
            elif not row["assets"]:
                lines.append("该次执行未登记本地素材；科学结论仍由其自包含 Findings 与正式裁决说明。")
        for asset in row["assets"]:
            path = asset.get("path") or asset["reference"]
            safe_path = path.replace("`", "&#96;").replace("\n", " ")
            status = {"missing": "文件缺失", "ambiguous": "路径有歧义", "drifted": "文件内容已变化",
                      "external": "外部来源，未检查可访问性", "unsupported": "不支持的引用类型"}.get(asset["status"])
            lines.append(f"- {label} · {asset['kind']}：`{safe_path}`" + (f"（{status}）" if status else ""))
    if len(lines) == 2:
        lines.append("本轮可用于报告的 Evidence 未登记独立素材文件。")
    if warnings:
        lines.extend(["", "图片引用未能匹配唯一可用的已登记素材："])
        lines.extend("- `" + path.replace("`", "&#96;").replace("\n", " ") + "`" for path in warnings)
    return lines
