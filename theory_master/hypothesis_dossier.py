"""Stable scientific Hypothesis dossier: SDK copies frozen text; Lead supplies display copy."""
from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
from typing import Any

from .hypothesis_display import normalize_hypothesis_display


DOSSIER_MARKER = "<!-- theory-master/hypothesis-dossier/v1 -->"


def _append_publication(*, canvas_path: Path, target: Path, token: str,
                        block: str, header: str | None = None) -> None:
    """Append each published block once, even after its visible marker is edited."""
    root = canvas_path.resolve().parent
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Hypothesis dossier must remain inside its vault")
    relative = target.resolve().relative_to(root).as_posix()
    internal = root / ".theory-master"
    if not internal.resolve().is_relative_to(root):
        raise ValueError("Markdown publication metadata must remain inside its vault")
    internal.mkdir(parents=True, exist_ok=True)
    metadata = internal / "markdown-publications.json"
    locking = internal / "markdown-publications.lock"
    if metadata.is_symlink() or locking.is_symlink():
        raise ValueError("Markdown publication metadata must not be a symbolic link")
    with locking.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            saved = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {"version": 1, "documents": {}}
            documents = saved["documents"]
            published = set(documents.get(relative, []))
            if not target.is_file() and (published or header is None):
                raise FileNotFoundError("Published Hypothesis dossier is missing: " + str(target))
            if target.is_file():
                existing = target.read_text(encoding="utf-8")
                # A crash between appending a block and recording its token can
                # be recovered without changing or repeating the visible body.
                published.update(re.findall(r"(?m)^<!-- ((?:frozen|evidence|report)-cycle:[A-Za-z0-9_-]+) -->$", existing))
            if token not in published:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    with target.open("a", encoding="utf-8") as note:
                        note.write("\n\n" + block + "\n")
                else:
                    with target.open("x", encoding="utf-8") as note:
                        note.write((header or "") + "\n" + block + "\n")
                published.add(token)
            if documents.get(relative) != sorted(published):
                from .cycle_outcome_documents import _atomic_write
                documents[relative] = sorted(published)
                _atomic_write(metadata, json.dumps(saved, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_hypothesis_dossier(
    *, canvas_path: Path, notes_root: Path, hypothesis_id: str, cycle_id: str,
    hypothesis: str, scope: str, why_now: str, goal_advance: str, decisive_test: str,
    display_title: str = "", display_summary: str = "", candidate_id: str = "",
    evolution_operator: str = "", lineage_anchor_node_id: str = "",
    lineage_anchor_node_ids: list[str] | None = None,
    lineage_main_node_id: str = "", lineage_other_node_ids: list[str] | None = None,
    research_transition: dict[str, Any] | None = None,
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
    role_lines = (["- Main 来源（分支连线）：" + lineage_main_node_id,
                   "- 其它来源（仅追溯）：" + ("、".join(lineage_other_node_ids or []) or "（无）")]
                  if lineage_main_node_id else [])
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
        *role_lines,
        "- 上游锚点：" + parent_text,
        "- 上游谱系：" + ("、".join(
            f"{source_id}（{evolution_operator or 'unknown'}）" for source_id in source_ids
        ) if source_ids else "（根节点；无上游 H）"),
        "- 派生类型：" + (evolution_operator or "（根 H / 未指定）"), "",
    ])
    target.parent.mkdir(parents=True, exist_ok=True)
    from .research_transition import research_transition_text
    header = "\n".join([
        DOSSIER_MARKER, "# " + hypothesis_id + " · " + title, "",
        "> 长期假说档案。标题与摘要是节点导读；完整科学定义在冻结快照中，当前裁决状态以 SQLite 为准。", "",
        "### 节点摘要（锁定时生成）", "", summary, "",
        *([research_transition_text(research_transition), ""] if research_transition else []),
    ])
    _append_publication(canvas_path=canvas_path, target=target,
                        token="frozen-cycle:" + cycle_id, block=block, header=header)
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
    if not re.fullmatch(r"(?:cycle-)?\d+", cycle_id):
        raise ValueError("dossier needs safe Cycle ID")
    marker = "<!-- evidence-cycle:" + cycle_id + " -->"
    _append_publication(canvas_path=canvas_path, target=target,
        token="evidence-cycle:" + cycle_id,
        block=marker + "\n### Cycle " + cycle_id + " · Evidence 引用索引\n\n" + "\n".join("- " + ref for ref in refs))


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
    if not re.fullmatch(r"(?:cycle-)?\d+", cycle_id):
        raise ValueError("dossier needs safe Cycle ID")
    marker = "<!-- report-cycle:" + cycle_id + " -->"
    _append_publication(canvas_path=canvas_path, target=dossier,
        token="report-cycle:" + cycle_id,
        block=marker + "\n### Cycle " + cycle_id + " · 人类可读报告\n\n[[" + report_stem + "]]")
