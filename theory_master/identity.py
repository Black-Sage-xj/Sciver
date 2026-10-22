from __future__ import annotations

import os
from pathlib import Path
from importlib.resources import files

from .role_registry import BUDGET_ROLES, PATH_ANCHOR_ROLES, ROLES

AUXILIARY_ROLES = frozenset({
    "semantic_annotator", "budget_reviewer", "attention_reviewer",
    "runtime_supervisor", "incident_supervisor", "research_chat",
})


def identities_root() -> Path:
    override = os.environ.get("THEORYMASTER_IDENTITIES_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(str(files("theory_master").joinpath("resources", "identities")))


def identity_files(role: str) -> tuple[Path, ...]:
    if role not in ROLES and role not in AUXILIARY_ROLES:
        raise ValueError(f"unknown TheoryMaster role: {role}")
    root = identities_root()
    if role == "semantic_annotator":
        return (root / "semantic_annotator.md",)
    if role == "budget_reviewer":
        return (root / "budget_reviewer.md",)
    if role == "attention_reviewer":
        return (root / "supervisor.md", root / "attention_reviewer.md")
    if role == "runtime_supervisor":
        return (root / "supervisor.md", root / "runtime_supervisor.md")
    if role == "incident_supervisor":
        return (root / "supervisor.md", root / "incident_supervisor.md")
    if role == "research_chat":
        return (root / "research_chat.md",)
    files = [root / f"{role}.md"]
    # Investigator uses native Codex Goal; generic Stage Card handoff text
    # belongs only to roles that actually receive ordinary Stage Turns.
    files.append(root / ("_investigator_handoff.md" if role == "investigator"
                         else "_invocation_handoff.md"))
    if role in BUDGET_ROLES:
        files.append(root / "_round_budget.md")
    # Only roles whose stage projection actually carries path anchors need the
    # path-resolution attachment. Investigator deliberately discovers relevant
    # scientific material from its workspace instead of receiving a path catalog.
    if role in PATH_ANCHOR_ROLES:
        files.append(root / "_paths.md")
    return tuple(files)


def identity_block(role: str) -> str:
    parts: list[str] = []
    for path in identity_files(role):
        if not path.is_file():
            raise FileNotFoundError(f"missing identity file: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"empty identity file: {path}")
        parts.append(text)
    return "\n\n".join(parts)
