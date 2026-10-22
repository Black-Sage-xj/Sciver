"""Safe, repeatable repair of missing historical H dossiers and display metadata."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .hypothesis_display import normalize_hypothesis_display
from .hypothesis_dossier import DOSSIER_MARKER
from .science_graph_store import ScienceGraphStore

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_OLD_AUTHORITY_LINE = (
    "> 以下科学文本由 SDK 原样复制，不经摘要模型重述；科学裁决与活动状态以 Canvas/运行记录为准。"
)
_NEW_AUTHORITY_LINE = (
    "> 以下科学文本由 SDK 原样复制，不经摘要模型重述；SQLite 是科学图谱权威，Canvas 仅为只读投影。"
)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_dossier_path(task_root: Path, ref: str) -> Path | None:
    value = str(ref or "").strip()
    if not value or value.startswith("/") or ".." in Path(value).parts:
        return None
    candidate = (task_root / (value if value.endswith(".md") else value + ".md"))
    if candidate.is_symlink():
        return None
    try:
        candidate.resolve().relative_to(task_root.resolve())
    except ValueError:
        return None
    return candidate


def _incoming_lineage(store: ScienceGraphStore, hypothesis_id: str) -> list[tuple[str, str]]:
    return [
        (str(row["source_hypothesis"]), str(row["evolution_operator"] or "unknown"))
        for row in store.db.execute(
            "SELECT source_hypothesis,evolution_operator FROM scientific_edges "
            "WHERE target_hypothesis=? ORDER BY id", (hypothesis_id,),
        )
    ]


def _current_status(node: dict[str, Any]) -> tuple[str, bool]:
    whole = node.get("whole_h_assessment") or {}
    adjudicated = bool(
        str(whole.get("cycle_id") or "").strip()
        or str(whole.get("judge_rationale") or "").strip()
        or str(whole.get("decisive_test") or "").strip()
        or str(whole.get("summary") or "").strip()
        or whole.get("verdict") in {"supported", "rejected"}
    )
    return str(node.get("scientific_status") or "none"), adjudicated


def _render_historical_dossier(
    node: dict[str, Any], *, original_text: str, lineage: list[tuple[str, str]],
    display_title: str, display_summary: str,
) -> str:
    ident = str(node["id"])
    lines = [
        DOSSIER_MARKER,
        "<!-- generated-by: theory-master-historical-hypothesis-dossier/v1 -->",
        f"# {ident} · 历史科学档案",
        "",
        "> 本档案由 SQLite 与保留的 legacy_records 回填。原始字段逐字保留；未保存的冻结范围或决定性检验不作推断补写。",
        "> SQLite 是科学图谱权威，Canvas 仅为只读投影。当前裁决状态以 SQLite 为准。",
        "",
        "### 节点摘要（历史摘要未通过新契约时明确降级）",
        "",
        display_summary,
        "",
        "## SQLite 历史字段",
        "",
        "### 原始标题（逐字保留）",
        "",
        str(node.get("title") or "（历史字段为空）"),
        "",
        "### 原始摘要（逐字保留）",
        "",
        str(node.get("summary") or "（历史字段为空）"),
        "",
        "### 原始节点文本（legacy_records 原文）",
        "",
    ]
    if original_text:
        lines.extend("    " + line for line in original_text.splitlines())
    else:
        lines.append("    （legacy_records 中没有该节点的原始文本；以上 SQLite 字段是可用的历史来源。）")
    lines.extend(["", "## SQLite 谱系索引", ""])
    if lineage:
        lines.extend(f"- {parent}（{operator}）" for parent, operator in lineage)
    else:
        lines.append("- 未记录上游 H→H 谱系；不据此推断其科学来源。")
    lines.extend([
        "",
        "## 迁移边界",
        "",
        "- 本文是历史资料档案，不是新生成的冻结命题，也不替代 Judge 的任何裁决。",
        "- 完整原文保留在本档案及 SQLite/legacy_records；展示摘要不可用时，不以截断原文冒充科学压缩。",
        "",
    ])
    return "\n".join(lines)


def _refresh_sdk_dossier(
    path: Path, *, hypothesis_id: str, display_title: str, display_summary: str,
    owner_cycle: str | None, lineage: list[tuple[str, str]],
) -> tuple[bool, str | None]:
    """Refresh only recognizable SDK-generated header/index fields; preserve annotations."""
    if not path.is_file() or path.is_symlink():
        return False, "dossier path is missing or unsafe"
    original = path.read_text(encoding="utf-8")
    if not original.startswith(DOSSIER_MARKER):
        return False, "dossier is not SDK-managed; preserved without overwrite"
    text = original.replace(_OLD_AUTHORITY_LINE, _NEW_AUTHORITY_LINE)
    if owner_cycle:
        marker = f"<!-- frozen-cycle:{owner_cycle} -->"
        start = text.find(marker)
        if start >= 0:
            next_marker = re.search(
                r"(?m)^<!-- (?:frozen-cycle|evidence-cycle|report-cycle):",
                text[start + len(marker):],
            )
            end = start + len(marker) + next_marker.start() if next_marker else len(text)
            prefix, block, suffix = text[:start], text[start:end], text[end:]
            parent_text = "、".join(parent for parent, _ in lineage) if lineage else "（根节点；无上游 H）"
            typed_text = "、".join(f"{parent}（{operator}）" for parent, operator in lineage) if lineage else "（根节点；无上游 H）"
            block_lines = block.splitlines()
            out: list[str] = []
            found_lineage = False
            for line in block_lines:
                if line.startswith("- 上游锚点："):
                    out.append("- 上游锚点：" + parent_text)
                elif line.startswith("- 上游谱系："):
                    if lineage:
                        out.append("- 上游谱系：" + typed_text)
                        found_lineage = True
                else:
                    out.append(line)
                    if line.startswith("- 上游锚点：") and not found_lineage and lineage:
                        # The following loop handles the existing line separately; this
                        # flag is only used when an explicit typed-lineage field exists.
                        pass
            if lineage and not any(line.startswith("- 上游谱系：") for line in out):
                index = next((i for i, line in enumerate(out) if line.startswith("- 上游锚点：")), -1)
                if index >= 0:
                    out.insert(index + 1, "- 上游谱系：" + typed_text)
            text = prefix + "\n".join(out) + suffix
    # The header is rewritten only when its shape is still the known SDK template.
    first_snapshot = re.search(r"(?m)^<!-- frozen-cycle:", text)
    if first_snapshot:
        prefix = text[:first_snapshot.start()].rstrip("\n")
        lines = prefix.splitlines()
        heading_index = next((i for i, line in enumerate(lines) if line.startswith("### ")), -1)
        known_header = (
            len(lines) >= 6 and lines[0] == DOSSIER_MARKER
            and lines[1].startswith("# " + hypothesis_id + " · ")
            and any(line.startswith("> 长期假说档案。") for line in lines[:5])
            and heading_index >= 0
            and lines[heading_index] in {
                "### Canvas 展示摘要（Lead 文案）", "### 节点摘要（锁定时生成）",
            }
            and all(not line.strip() for line in lines[heading_index + 1:-1])
        )
        if known_header:
            header = "\n".join([
                DOSSIER_MARKER,
                f"# {hypothesis_id} · {display_title}", "",
                "> 长期假说档案。标题与摘要是节点导读；完整科学定义在冻结快照中，当前裁决状态以 SQLite 为准。", "",
                "### 节点摘要（锁定时生成）", "", display_summary, "",
            ])
            text = header + "\n" + text[first_snapshot.start():]
    if text != original:
        _atomic_write(path, text if text.endswith("\n") else text + "\n")
    return text != original, None


def repair_hypothesis_dossiers(
    database: Path, notes_root: Path, *, preview: bool = True,
) -> dict[str, Any]:
    """Repair missing dossier links and normalize only display metadata, never frozen claims."""
    task_root = notes_root.parent.resolve()
    plan: list[dict[str, Any]] = []
    conflicts: list[str] = []
    with ScienceGraphStore(database, readonly=preview) as store:
        graph = store.graph_view()
        nodes = [node for node in graph["nodes"] if node.get("type") == "Hypothesis"]
        for node in nodes:
            ident = str(node["id"])
            if not _SAFE_ID.fullmatch(ident):
                conflicts.append(f"{ident}: unsafe ID cannot be used as a dossier filename")
                continue
            canonical_ref = f"notes/hypotheses/{ident}"
            canonical_path = notes_root / "hypotheses" / f"{ident}.md"
            existing_ref = str(node.get("dossier_ref") or "")
            existing_path = _safe_dossier_path(task_root, existing_ref) if existing_ref else None
            path = existing_path if existing_path and existing_path.is_file() else canonical_path
            ref_is_broken = bool(existing_ref and (existing_path is None or not existing_path.is_file()))
            missing = not path.is_file()
            lineage = _incoming_lineage(store, ident)
            owner_row = store.db.execute(
                "SELECT cycle_id FROM hypothesis_cycle_owners WHERE hypothesis_id=?", (ident,),
            ).fetchone()
            owner_cycle = str(owner_row["cycle_id"]) if owner_row else None
            status, adjudicated = _current_status(node)
            title, summary = normalize_hypothesis_display(
                ident, title=node.get("title"), summary=node.get("summary"),
                fallback_title=node.get("title"), scientific_status=status,
                adjudicated=adjudicated,
            )
            legacy_row = store.db.execute(
                "SELECT raw_json FROM legacy_records WHERE id=?", (ident,),
            ).fetchone()
            raw_text = ""
            if legacy_row:
                try:
                    raw_text = str(json.loads(legacy_row["raw_json"]).get("text") or "")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    raw_text = str(legacy_row["raw_json"] or "")
            action = {
                "id": ident, "old_ref": existing_ref, "new_ref": canonical_ref,
                "missing_dossier": missing, "broken_ref": ref_is_broken,
                "display_changed": title != str(node.get("title") or "") or summary != str(node.get("summary") or ""),
                "multi_parent": len(lineage) > 1, "path": str(path),
            }
            plan.append({
                "node": node, "ident": ident, "path": path, "canonical_path": canonical_path,
                "existing_path": existing_path, "canonical_ref": canonical_ref, "existing_ref": existing_ref,
                "missing": missing, "ref_is_broken": ref_is_broken,
                "lineage": lineage, "owner_cycle": owner_cycle,
                "title": title, "summary": summary, "raw_text": raw_text, "action": action,
            })

    written = 0
    refreshed = 0
    display_updates = 0
    refs_updated = 0
    if not preview:
        updates: list[tuple[str, str, str]] = []
        for item in plan:
            ident = item["ident"]
            path: Path = item["path"]
            existing_path = item["existing_path"]
            ref = item["existing_ref"] if (
                item["existing_ref"] and existing_path and existing_path.is_file()
            ) else item["canonical_ref"]
            node = item["node"]
            if item["missing"]:
                if path.exists():
                    conflicts.append(f"{ident}: destination already exists but was not a readable SDK dossier")
                    continue
                content = _render_historical_dossier(
                    node, original_text=item["raw_text"], lineage=item["lineage"],
                    display_title=item["title"], display_summary=item["summary"],
                )
                _atomic_write(item["canonical_path"], content)
                written += 1
                ref = item["canonical_ref"]
            elif path.is_file() and path.read_text(encoding="utf-8").startswith(DOSSIER_MARKER):
                changed, error = _refresh_sdk_dossier(
                    path, hypothesis_id=ident, display_title=item["title"],
                    display_summary=item["summary"], owner_cycle=item["owner_cycle"],
                    lineage=item["lineage"],
                )
                refreshed += int(changed)
                if error:
                    conflicts.append(f"{ident}: {error}")
            elif item["existing_ref"] and existing_path and existing_path.is_file() and item["path"] == existing_path:
                # A human-owned dossier is linked but never rewritten.
                ref = item["existing_ref"]
            else:
                conflicts.append(f"{ident}: no safe dossier target")
                continue
            if item["action"]["display_changed"]:
                display_updates += 1
            if item["existing_ref"] != ref:
                refs_updated += 1
            updates.append((ident, item["title"], item["summary"]))
            # Canonical ref repair happens after the file is safely present.
            item["final_ref"] = ref

        with ScienceGraphStore(database) as store:
            with store.transaction():
                for item in plan:
                    ident = item["ident"]
                    if not _SAFE_ID.fullmatch(ident) or not item.get("final_ref"):
                        continue
                    store.db.execute(
                        "UPDATE hypotheses SET title=?,summary=?,dossier_ref=? WHERE id=?",
                        (item["title"], item["summary"], item["final_ref"], ident),
                    )
    else:
        written = sum(1 for item in plan if item["missing"])
        refreshed = sum(1 for item in plan if not item["missing"] and item["owner_cycle"] and len(item["lineage"]) > 1)
        display_updates = sum(1 for item in plan if item["action"]["display_changed"])
        refs_updated = sum(1 for item in plan if item["existing_ref"] != item["canonical_ref"] and (item["missing"] or item["ref_is_broken"]))

    return {
        "preview": preview,
        "hypotheses": len(plan),
        "missing_dossiers": sum(1 for item in plan if item["missing"]),
        "broken_refs": sum(1 for item in plan if item["ref_is_broken"]),
        "display_updates": display_updates,
        "multi_parent_dossiers": sum(1 for item in plan if len(item["lineage"]) > 1),
        "dossiers_written": written,
        "dossiers_refreshed": refreshed,
        "refs_updated": refs_updated,
        "conflicts": conflicts,
        "actions": [item["action"] for item in plan],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or safely repair historical H dossier/display metadata")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair_hypothesis_dossiers(
        args.database, args.notes_root, preview=not args.write,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
