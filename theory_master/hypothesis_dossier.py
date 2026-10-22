"""Stable scientific Hypothesis dossier: SDK copies frozen text; Lead supplies display copy."""
from __future__ import annotations

import re
from pathlib import Path

from .hypothesis_display import normalize_hypothesis_display


DOSSIER_MARKER = "<!-- theory-master/hypothesis-dossier/v1 -->"


def write_hypothesis_dossier(
    *, canvas_path: Path, notes_root: Path, hypothesis_id: str, cycle_id: str,
    hypothesis: str, scope: str, why_now: str, goal_advance: str, decisive_test: str,
    display_title: str = "", display_summary: str = "", candidate_id: str = "",
    evolution_operator: str = "", lineage_anchor_node_id: str = "",
    lineage_anchor_node_ids: list[str] | None = None,
) -> str:
    """Create one vault-relative H dossier and append non-duplicated frozen Cycle snapshots."""
    if not re.fullmatch(r"H-\d{4,}", hypothesis_id):
        raise ValueError("dossier needs canonical H ID")
    if not re.fullmatch(r"(?:cycle-)?\d+", cycle_id):
        raise ValueError("dossier needs safe Cycle ID")
    canvas_parent = canvas_path.resolve().parent
    note_root = notes_root.resolve()
    try:
        note_root.relative_to(canvas_parent)
    except ValueError as exc:
        raise ValueError("Hypothesis dossier must remain under the Canvas vault") from exc
    target = note_root / "hypotheses" / (hypothesis_id + ".md")
    rel = target.relative_to(canvas_parent).with_suffix("").as_posix()
    source_ids = list(dict.fromkeys(
        str(value).strip() for value in (lineage_anchor_node_ids or []) if str(value).strip()
    ))
    if lineage_anchor_node_id and lineage_anchor_node_id not in source_ids:
        source_ids.insert(0, lineage_anchor_node_id)
    title, summary = normalize_hypothesis_display(
        hypothesis_id, title=display_title, summary=display_summary,
        fallback_title=display_title or hypothesis_id,
    )
    parent_text = "、".join(source_ids) if source_ids else "（根节点；无上游 H）"
    snapshot_marker = "<!-- frozen-cycle:" + cycle_id + " -->"
    block = "\n".join([
        snapshot_marker, "## Cycle " + cycle_id + " · 冻结科学定义", "",
        "> 以下科学文本由 SDK 原样复制，不经摘要模型重述；SQLite 是科学图谱权威，Canvas 仅为只读投影。", "",
        "### Hypothesis（原文）", "", hypothesis.strip(), "",
        "### Scope（原文）", "", scope.strip() or "（未单独指定）", "",
        "### Why now（原文）", "", why_now.strip() or "（未单独指定）", "",
        "### Goal advance（原文）", "", goal_advance.strip() or "（未单独指定）", "",
        "### Decisive test（原文）", "", decisive_test.strip() or "（未单独指定）", "",
        "### SDK 来源索引", "",
        "- H ID：" + hypothesis_id,
        "- Cycle ID：" + cycle_id,
        "- Candidate ID：" + (candidate_id or "（未知）"),
        "- 上游锚点：" + parent_text,
        "- 上游谱系：" + ("、".join(
            f"{source_id}（{evolution_operator or 'unknown'}）" for source_id in source_ids
        ) if source_ids else "（根节点；无上游 H）"),
        "- 派生类型：" + (evolution_operator or "（根 H / 未指定）"), "",
    ])
    target.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join([
        DOSSIER_MARKER, "# " + hypothesis_id + " · " + title, "",
        "> 长期假说档案。标题与摘要是节点导读；完整科学定义在冻结快照中，当前裁决状态以 SQLite 为准。", "",
        "### 节点摘要（锁定时生成）", "", summary, "",
    ])
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if not existing.startswith(DOSSIER_MARKER):
            raise ValueError("refusing to overwrite a non-SDK Hypothesis dossier: " + str(target))
        migrated = existing.replace(
            "> 以下科学文本由 SDK 原样复制，不经摘要模型重述；科学裁决与活动状态以 Canvas/运行记录为准。",
            "> 以下科学文本由 SDK 原样复制，不经摘要模型重述；SQLite 是科学图谱权威，Canvas 仅为只读投影。",
        )
        # Update only the SDK-generated header. If a person has inserted extra
        # material there, leave it intact rather than guessing which text is theirs.
        first_snapshot = re.search(r"(?m)^<!-- frozen-cycle:", migrated)
        if first_snapshot:
            prefix = migrated[:first_snapshot.start()].rstrip("\n")
            lines = prefix.splitlines()
            heading_index = next((i for i, line in enumerate(lines) if line.startswith("### ")), -1)
            header_is_sdk_owned = (
                len(lines) >= 6 and lines[0] == DOSSIER_MARKER
                and lines[1].startswith("# " + hypothesis_id + " · ")
                and any(line.startswith("> 长期假说档案。") for line in lines[:5])
                and heading_index >= 0
                and lines[heading_index] in {"### Canvas 展示摘要（Lead 文案）", "### 节点摘要（锁定时生成）"}
                and all(not line.strip() for line in lines[heading_index + 1:-1])
            )
            if header_is_sdk_owned:
                migrated = header + "\n" + migrated[first_snapshot.start():]
        # The current Cycle's SDK-owned source index is safe to refresh on retry.
        if snapshot_marker in migrated:
            start = migrated.index(snapshot_marker)
            next_snapshot = re.search(r"(?m)^<!-- (?:frozen-cycle|evidence-cycle|report-cycle):", migrated[start + len(snapshot_marker):])
            end = start + len(snapshot_marker) + next_snapshot.start() if next_snapshot else len(migrated)
            prefix, cycle_block, suffix = migrated[:start], migrated[start:end], migrated[end:]
            trailing_newlines = len(cycle_block) - len(cycle_block.rstrip("\n"))
            cycle_lines = cycle_block.splitlines()
            typed_text = "、".join(
                f"{source_id}（{evolution_operator or 'unknown'}）" for source_id in source_ids
            ) if source_ids else "（根节点；无上游 H）"
            cycle_lines = [
                ("- 上游锚点：" + parent_text) if line.startswith("- 上游锚点：") else
                ("- 上游谱系：" + typed_text) if line.startswith("- 上游谱系：") else
                ("- 派生类型：" + (evolution_operator or "（根 H / 未指定）")) if line.startswith("- 派生类型：") else line
                for line in cycle_lines
            ]
            if source_ids and not any(line.startswith("- 上游谱系：") for line in cycle_lines):
                anchor_index = next((i for i, line in enumerate(cycle_lines) if line.startswith("- 上游锚点：")), -1)
                if anchor_index >= 0:
                    cycle_lines.insert(anchor_index + 1, "- 上游谱系：" + typed_text)
            # splitlines() preserves one trailing empty element; subtract it when
            # restoring the original newline count to keep retries byte-idempotent.
            migrated = prefix + "\n".join(cycle_lines) + ("\n" * max(0, trailing_newlines - 1)) + suffix
        if snapshot_marker not in migrated:
            migrated = migrated.rstrip() + "\n\n" + block + "\n"
        if migrated != existing:
            target.write_text(migrated, encoding="utf-8")
    else:
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(header + "\n" + block + "\n")
        except FileExistsError as exc:
            raise RuntimeError("concurrent Hypothesis dossier creation") from exc
    return rel


def append_hypothesis_evidence(*, canvas_path: Path, wikilink_stem: str, cycle_id: str, evidence_refs: list[str]) -> None:
    """Keep growing Evidence indexes in H dossier, never in the Canvas box."""
    refs = list(dict.fromkeys(str(ref).strip() for ref in evidence_refs if str(ref).strip()))
    if not refs:
        return
    canvas_parent = canvas_path.resolve().parent
    target = (canvas_parent / (wikilink_stem + ".md")).resolve()
    target.relative_to(canvas_parent)
    if not target.is_file():
        raise ValueError("linked Hypothesis dossier is missing: " + str(target))
    original = target.read_text(encoding="utf-8")
    if not original.startswith(DOSSIER_MARKER):
        raise ValueError("cannot modify a non-SDK dossier")
    marker = "<!-- evidence-cycle:" + cycle_id + " -->"
    if marker in original:
        return
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n" + marker + "\n### Cycle " + cycle_id + " · Evidence 引用索引\n\n")
        handle.write("\n".join("- " + ref for ref in refs) + "\n")


def append_hypothesis_cycle_report(*, canvas_path: Path, notes_root: Path, hypothesis_id: str, cycle_id: str, note_path: Path) -> None:
    """Index an already materialized human-readable Cycle report by wikilink."""
    if not re.fullmatch(r"H-\d{4,}", hypothesis_id):
        return
    parent = canvas_path.resolve().parent
    try:
        report_stem = note_path.resolve().relative_to(parent).with_suffix("").as_posix()
    except ValueError:
        return  # Do not fabricate a broken link for non-vault runtime notes.
    if not note_path.is_file():
        return
    dossier = notes_root.resolve() / "hypotheses" / (hypothesis_id + ".md")
    if not dossier.is_file():
        return
    existing = dossier.read_text(encoding="utf-8")
    if not existing.startswith(DOSSIER_MARKER):
        return
    marker = "<!-- report-cycle:" + cycle_id + " -->"
    if marker not in existing:
        with dossier.open("a", encoding="utf-8") as handle:
            handle.write("\n" + marker + "\n### Cycle " + cycle_id + " · 人类可读报告\n\n[[" + report_stem + "]]\n")
