"""Pure-Markdown Scientific Model section in research_plan.md.

The Research Plan is human-facing Markdown. Machine metadata belongs in the
runtime store and must never be duplicated into the plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import atomic
from .research_plan_history import capture_current, plan_lock, record

FORMAT_VERSION = 4
CONFIG_MARKER = "embedded_state_format"
MODEL_HEADING = "Scientific Model"
_EMPTY_VISIBLE = "尚未形成稳定的科学总结。"


@dataclass(frozen=True)
class MarkdownSection:
    heading: str
    start: int
    end: int
    summary: str


def _normalize_summary(value: Any) -> str:
    return " ".join(str(value or "").split())


def _render_section(summary: str) -> str:
    visible = _normalize_summary(summary) or _EMPTY_VISIBLE
    return f"# {MODEL_HEADING}\n\n{visible}\n"


def _h1_matches(text: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"(?m)^# {re.escape(MODEL_HEADING)}[ \t]*\r?$", text))


def _section(text: str) -> MarkdownSection:
    matches = _h1_matches(text)
    if len(matches) != 1:
        raise ValueError(
            f"research_plan.md requires exactly one H1 '# {MODEL_HEADING}' (found {len(matches)})"
        )
    hit = matches[0]
    body_start = hit.end()
    if text[body_start:body_start + 2] == "\r\n":
        body_start += 2
    elif text[body_start:body_start + 1] == "\n":
        body_start += 1
    next_h1 = re.search(r"(?m)^# (?!#).*(?:\r?\n|\Z)", text[body_start:])
    end = body_start + next_h1.start() if next_h1 else len(text)
    body = text[body_start:end].strip()
    if "theory-master-state:" in body or body.startswith("{") or '"schema"' in body:
        raise ValueError(
            "Scientific Model must be pure Markdown; embedded machine-state payloads are unsupported"
        )
    if body == _EMPTY_VISIBLE:
        summary = ""
    else:
        summary = _normalize_summary(body)
    return MarkdownSection(MODEL_HEADING, hit.start(), end, summary)


def parse(text: str) -> MarkdownSection:
    return _section(text)


def load_summary(plan: Path) -> str:
    return parse(Path(plan).read_text(encoding="utf-8")).summary


def replace_summary(plan: Path, summary: str, *, reason: str = "科研流程更新 Scientific Model") -> None:
    path = Path(plan)
    with plan_lock(path):
        text = path.read_text(encoding="utf-8")
        capture_current(path)
        section = parse(text)
        candidate = text[:section.start] + _render_section(summary) + text[section.end:]
        parse(candidate)
        if candidate != text:
            atomic(path, candidate)
            record(path, candidate, reason=reason, source="sdk-scientific-model", section="Scientific Model")


def append_model(text: str, model: dict[str, Any]) -> str:
    suffix = "" if not text or text.endswith("\n") else "\n"
    return text + suffix + "\n" + _render_section(str(model.get("summary") or ""))


def initialize_model(plan: Path, model: dict[str, Any]) -> None:
    """Initialize the section and version ledger for a new Research Plan."""
    path = Path(plan)
    state = path.parent / ".theory-master"
    if state.is_symlink():
        raise ValueError("unsafe task directory")
    state.mkdir(mode=0o700, exist_ok=True)
    with plan_lock(path):
        text = path.read_text(encoding="utf-8")
        capture_current(path)
        matches = _h1_matches(text)
        if not matches:
            candidate = append_model(text, model)
        else:
            if len(matches) != 1:
                raise ValueError("new research_plan.md must contain at most one '# Scientific Model'")
            section = parse(text)
            summary = section.summary or str(model.get("summary") or "")
            candidate = text[:section.start] + _render_section(summary) + text[section.end:]
        if candidate != text:
            atomic(path, candidate)
            record(path, candidate, reason="任务初始化并标准化 Scientific Model", source="sdk-bootstrap", section="Scientific Model")


def validate_current(plan: Path, config: dict[str, Any]) -> MarkdownSection:
    if config.get(CONFIG_MARKER) != FORMAT_VERSION:
        raise ValueError(
            f"research_plan format must be current version {FORMAT_VERSION}; backward compatibility is disabled"
        )
    return parse(Path(plan).read_text(encoding="utf-8"))
