"""Deterministic human-readable and structured documents for scientific Cycle Outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .science_graph_store import ScienceGraphStore

_MARKDOWN_GENERATOR = "theory-master/cycle-outcome"
_MARKDOWN_VERSION = 2
_JSON_GENERATOR = "theory-master/cycle-outcome-json"
_JSON_VERSION = 2
_V1_MARKER_RE = re.compile(r"^<!-- theory-master/cycle-outcome/source:([0-9a-f]{64}) -->$")
_V2_MARKER_RE = re.compile(
    r"^<!-- theory-master/cycle-outcome/v2 source:([0-9a-f]{64}) render:([0-9a-f]{64}) -->$"
)


def outcome_document_ref(cycle_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cycle_id):
        raise ValueError(f"Unsafe Cycle ID: {cycle_id!r}")
    return f"notes/outcomes/{cycle_id}.md"


def outcome_document_json_ref(cycle_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cycle_id):
        raise ValueError(f"Unsafe Cycle ID: {cycle_id!r}")
    return f"notes/outcomes/{cycle_id}.json"


def _source_digest(outcome: dict[str, Any], lock: dict[str, Any], original_decision: str) -> str:
    payload = {
        "version": _MARKDOWN_VERSION,
        "outcome": outcome,
        "lock": lock,
        "original_decision": original_decision,
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _render_outcome_body(
    outcome: dict[str, Any], *, lock: dict[str, Any], original_decision: str,
) -> str:
    cycle_id = str(outcome["cycle_id"])
    raw_verdicts = {
        str(item.get("raw_verdict") or "") for item in outcome.get("source_decisions") or []
    }
    if not raw_verdicts and original_decision:
        raw_verdicts = set(re.findall(r"(?m)^verdict:\s*(\S+)", original_decision))
    compatibility_only = bool(raw_verdicts - {str(outcome["verdict"])})
    lines = [
        f"# Cycle {cycle_id} · Outcome",
        "",
        f"- Cycle ID: `{cycle_id}`",
        f"- Hypothesis ID: `{outcome['hypothesis_id']}`",
        "- Original Decision ID(s): " + ", ".join(
            f"`{item['id']}`" for item in (outcome.get("source_decisions") or [outcome])
        ),
        f"- {'Stored compatibility verdict' if compatibility_only else 'Judge verdict'}: `{outcome['verdict']}`",
        "",
        "本页按 SQLite 中保存的原始记录整理该 Cycle 的裁决，不改变 Judge 的结论或扩大其适用范围。",
    ]
    if compatibility_only:
        lines.extend(["", "原始 Decision 的裁决类型为：" + ", ".join(
            f"`{value}`" for value in sorted(raw_verdicts) if value
        ) + "。数据库兼容字段不可替代原始类型。"])
    for label, value in (
        ("冻结命题", lock.get("hypothesis")),
        ("冻结范围", lock.get("scope")),
        ("决定性检验", outcome.get("decisive_test") or lock.get("decisive_test")),
        ("本轮结论", outcome.get("summary")),
        ("Judge 裁决依据", outcome.get("judge_rationale")),
    ):
        if value:
            lines.extend(["", f"## {label}", "", str(value).strip()])
    for label, key in (
        ("成立的发现", "findings"),
        ("未解决问题", "open_questions"),
        ("边界", "boundary"),
        ("Evidence 引用", "evidence_refs"),
    ):
        values = outcome.get(key) or []
        if values:
            lines.extend(["", f"## {label}", ""])
            lines.extend(f"- {str(value).strip()}" for value in values)
    if outcome.get("report_ref"):
        lines.extend(["", "## 原始报告", "", f"[[{outcome['report_ref']}]]"])
    if original_decision.strip():
        lines.extend(["", "## 原始 Decision 正文", "", original_decision.strip()])
    lines.extend([
        "", "## 结构化来源记录", "",
        f"[[{outcome_document_json_ref(cycle_id)}|打开独立 JSON 审计记录]]",
        "", "完整的结构化记录单独保存，避免在人类可读正文中重复整份 JSON。", "",
    ])
    return "\n".join(lines)


def render_outcome_document(
    outcome: dict[str, Any], *, lock: dict[str, Any] | None = None,
    original_decision: str = "",
) -> str:
    """Render a concise reading document with verifiable source and render digests."""
    normalized_lock = lock or {}
    body = _render_outcome_body(
        outcome, lock=normalized_lock, original_decision=original_decision,
    )
    source_digest = _source_digest(outcome, normalized_lock, original_decision)
    render_digest = hashlib.sha256((body + "\n").encode("utf-8")).hexdigest()
    marker = f"<!-- {_MARKDOWN_GENERATOR}/v2 source:{source_digest} render:{render_digest} -->"
    return marker + "\n" + body + "\n"


def render_outcome_json(
    outcome: dict[str, Any], *, lock: dict[str, Any] | None = None,
    original_decision: str = "",
) -> str:
    """Render the machine-readable archive as a separately verifiable generated file."""
    normalized_lock = lock or {}
    payload = {
        "_generated_by": _JSON_GENERATOR,
        "_version": _JSON_VERSION,
        "source_digest": _source_digest(outcome, normalized_lock, original_decision),
        "outcome": outcome,
        "lock": normalized_lock,
        "original_decision": original_decision,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    document = {**payload, "_render_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _render_outcome_document_v1(
    outcome: dict[str, Any], *, lock: dict[str, Any] | None = None,
    original_decision: str = "",
) -> str:
    """Exact legacy renderer, used only to verify old SDK-owned documents before migration."""
    lock = lock or {}
    cycle_id = str(outcome["cycle_id"])
    source_digest = hashlib.sha256(json.dumps(
        outcome, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    raw_verdicts = {
        str(item.get("raw_verdict") or "") for item in outcome.get("source_decisions") or []
    }
    if not raw_verdicts and original_decision:
        raw_verdicts = set(re.findall(r"(?m)^verdict:\s*(\S+)", original_decision))
    compatibility_only = bool(raw_verdicts - {str(outcome["verdict"])})
    lines = [
        f"<!-- theory-master/cycle-outcome/source:{source_digest} -->",
        f"# Cycle {cycle_id} · Outcome", "",
        f"- Cycle ID: `{cycle_id}`",
        f"- Hypothesis ID: `{outcome['hypothesis_id']}`",
        "- Original Decision ID(s): " + ", ".join(
            f"`{item['id']}`" for item in (outcome.get("source_decisions") or [outcome])
        ),
        f"- {'Stored compatibility verdict' if compatibility_only else 'Judge verdict'}: `{outcome['verdict']}`",
        "", "本页按原始记录整理该 Cycle 的裁决，不改变 Judge 的结论或扩大其适用范围。",
    ]
    if compatibility_only:
        lines.extend(["", "原始 Decision 的裁决类型为：" + ", ".join(
            f"`{value}`" for value in sorted(raw_verdicts) if value
        ) + "。数据库兼容字段不可替代原始类型。"])
    for label, value in (
        ("冻结命题", lock.get("hypothesis")),
        ("冻结范围", lock.get("scope")),
        ("决定性检验", outcome.get("decisive_test") or lock.get("decisive_test")),
        ("本轮结论", outcome.get("summary")),
        ("Judge 裁决依据", outcome.get("judge_rationale")),
    ):
        if value:
            lines.extend(["", f"## {label}", "", str(value).strip()])
    for label, key in (
        ("成立的发现", "findings"), ("未解决问题", "open_questions"),
        ("边界", "boundary"), ("Evidence 引用", "evidence_refs"),
    ):
        values = outcome.get(key) or []
        if values:
            lines.extend(["", f"## {label}", ""])
            lines.extend(f"- {str(value).strip()}" for value in values)
    if outcome.get("report_ref"):
        lines.extend(["", "## 原始报告", "", f"[[{outcome['report_ref']}]]"])
    if original_decision.strip():
        lines.extend(["", "## 原始 Decision 正文", "", original_decision.strip()])
    lines.extend(["", "## 结构化来源记录", "", "```json",
                  json.dumps(outcome, ensure_ascii=False, sort_keys=True, indent=2), "```", ""])
    return "\n".join(lines)


def _valid_v2_markdown(existing: str) -> bool:
    first, separator, body = existing.partition("\n")
    match = _V2_MARKER_RE.fullmatch(first)
    if not separator or not match:
        return False
    actual = hashlib.sha256((body).encode("utf-8")).hexdigest()
    return actual == match.group(2)


def _valid_v2_json(existing: str) -> bool:
    try:
        document = json.loads(existing)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(document, dict):
        return False
    if document.get("_generated_by") != _JSON_GENERATOR or document.get("_version") != _JSON_VERSION:
        return False
    outcome = document.get("outcome")
    lock = document.get("lock")
    original_decision = document.get("original_decision")
    if not isinstance(outcome, dict) or not isinstance(lock, dict) or not isinstance(original_decision, str):
        return False
    if document.get("source_digest") != _source_digest(outcome, lock, original_decision):
        return False
    expected = document.get("_render_digest")
    payload = {key: value for key, value in document.items() if key != "_render_digest"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return isinstance(expected, str) and hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected


def _can_refresh_markdown(
    existing: str, content: str, outcome: dict[str, Any], *,
    lock: dict[str, Any] | None, original_decision: str,
) -> bool:
    if existing == content or _valid_v2_markdown(existing):
        return True
    first = existing.splitlines()[0] if existing.splitlines() else ""
    if _V1_MARKER_RE.fullmatch(first):
        return existing == _render_outcome_document_v1(
            outcome, lock=lock, original_decision=original_decision,
        )
    return False


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_outcome_document(
    notes_root: Path, outcome: dict[str, Any], *,
    lock: dict[str, Any] | None = None, original_decision: str = "",
) -> Path:
    ref = outcome_document_ref(str(outcome["cycle_id"]))
    target = notes_root.parent / ref
    json_target = notes_root.parent / outcome_document_json_ref(str(outcome["cycle_id"]))
    content = render_outcome_document(outcome, lock=lock, original_decision=original_decision)
    json_content = render_outcome_json(outcome, lock=lock, original_decision=original_decision)

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if not _can_refresh_markdown(
            existing, content, outcome, lock=lock, original_decision=original_decision,
        ):
            raise ValueError(f"Cycle Outcome document conflicts with existing file: {target}")
    if json_target.exists():
        existing_json = json_target.read_text(encoding="utf-8")
        if existing_json != json_content and not _valid_v2_json(existing_json):
            raise ValueError(f"Cycle Outcome JSON conflicts with existing file: {json_target}")

    # Validate both destinations before replacing either one. A manual annotation
    # is never overwritten merely because a source digest happens to match.
    if not json_target.exists() or json_target.read_text(encoding="utf-8") != json_content:
        _atomic_write(json_target, json_content)
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        _atomic_write(target, content)
    return target


def backfill_outcome_documents(
    database: Path, notes_root: Path, *, research_root: Path | None = None,
    preview: bool = True,
) -> dict[str, Any]:
    """Preview or materialize source-verifiable documents for stored Cycle outcomes."""
    documents: list[str] = []
    conflicts: list[str] = []
    with ScienceGraphStore(database, readonly=True) as store:
        rows = list(store.db.execute(
            "SELECT id,cycle_id,source_json FROM cycle_evaluations ORDER BY cycle_id,id"
        ))
        for row in rows:
            outcome = json.loads(row["source_json"])
            cycle_id = str(row["cycle_id"])
            ref = outcome_document_ref(cycle_id)
            json_ref = outcome_document_json_ref(cycle_id)
            lock: dict[str, Any] = {}
            lock_conflict = False
            if research_root and (research_root / cycle_id / "research_state.json").is_file():
                state = json.loads((research_root / cycle_id / "research_state.json").read_text(encoding="utf-8"))
                lock = state.get("hypothesis_lock") or {}
                if str(lock.get("id") or "") != str(outcome["hypothesis_id"]):
                    migration = outcome.get("identity_migration") or {}
                    if str(lock.get("id") or "") != str(migration.get("original_hypothesis_id") or ""):
                        conflicts.append(f"{cycle_id}: lock H differs from imported Outcome H")
                        lock = {}
                        lock_conflict = True
            originals = []
            for source in outcome.get("source_decisions") or [outcome]:
                raw = store.db.execute(
                    "SELECT raw_json FROM legacy_records WHERE id=?", (source["id"],)
                ).fetchone()
                if raw:
                    originals.append(json.loads(raw["raw_json"]).get("text", ""))
            original = "\n\n---\n\n".join(originals)
            content = render_outcome_document(outcome, lock=lock, original_decision=original)
            json_content = render_outcome_json(outcome, lock=lock, original_decision=original)
            target = notes_root.parent / ref
            json_target = notes_root.parent / json_ref
            conflict = lock_conflict
            if target.exists() and not _can_refresh_markdown(
                target.read_text(encoding="utf-8"), content, outcome,
                lock=lock, original_decision=original,
            ):
                conflicts.append(f"{cycle_id}: human-edited or unrecognized Markdown at {target}")
                conflict = True
            if json_target.exists():
                existing_json = json_target.read_text(encoding="utf-8")
                if existing_json != json_content and not _valid_v2_json(existing_json):
                    conflicts.append(f"{cycle_id}: human-owned JSON at {json_target}")
                    conflict = True
            documents.extend([ref, json_ref])
            if not preview and not conflict:
                write_outcome_document(notes_root, outcome, lock=lock, original_decision=original)
    return {
        "preview": preview,
        "outcomes": len(rows),
        "documents": documents,
        "conflicts": conflicts,
        "source_digest": hashlib.sha256("\n".join(
            row["source_json"] for row in rows
        ).encode("utf-8")).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or backfill Cycle Outcome documents")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--notes-root", type=Path, required=True)
    parser.add_argument("--research-root", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = backfill_outcome_documents(
        args.database, args.notes_root, research_root=args.research_root,
        preview=not args.write,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
