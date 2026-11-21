"""Discover the operator's live Codex skills without copying them into Sciver."""
from __future__ import annotations

import os
from pathlib import Path

from .skills import SkillRef, parse_skill


def shared_skills(*workspaces: Path) -> tuple[SkillRef, ...]:
    roots: list[Path] = []
    override = os.environ.get("THEORYMASTER_CODEX_SKILL_ROOTS")
    if override is not None:
        roots.extend(Path(value).expanduser() for value in override.split(os.pathsep) if value)
    else:
        for workspace in workspaces or (Path.cwd(),):
            for parent in (workspace.resolve(), *workspace.resolve().parents):
                roots.extend((parent / ".codex" / "skills", parent / ".agents" / "skills"))
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        roots.extend((codex_home / "skills", Path.home() / ".agents" / "skills"))
    found: dict[str, SkillRef] = {}
    seen: set[Path] = set()
    for root in roots:
        if root.resolve() in seen:
            continue
        seen.add(root.resolve())
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            # Codex's bundled system skills live one level below .system.
            try:
                candidates = sorted(entry.iterdir()) if entry.name == ".system" and entry.is_dir() else (entry,)
            except OSError:
                continue
            for candidate in candidates:
                try:
                    ref = parse_skill(candidate, scope="codex")
                except (OSError, ValueError):
                    continue
                found.setdefault(ref.name, ref)
    return tuple(found.values())


def shared_skill_prompt(*workspaces: Path) -> str:
    refs = shared_skills(*workspaces)
    lines = ["Live shared Codex skill catalog (original files; no copied skill bodies).",
             "When a skill applies or the operator names it, read its SKILL.md and follow its instructions. "
             "Resolve referenced resources relative to that skill directory. Operator instructions take precedence."]
    lines.extend(f"- {ref.name}: {ref.description}\n  Source: {ref.skill_md}" for ref in refs)
    if not refs:
        lines.append("No local Codex skills were found. Report this when a requested skill is unavailable.")
    return "\n".join(lines)
