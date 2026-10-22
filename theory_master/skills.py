from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from importlib.resources import files

from .identity import identity_block
from .role_registry import ROLE_DISPLAY_NAMES, WRITE_ROLES, role_display_name

# System-level occasional capabilities only. Always-on role text lives in identities/.
# Task-local capabilities are declared by each task's theory-master.config.json and
# live under <task-config-dir>/.theory-master/skills/.
ROLE_SKILLS: dict[str, tuple[str, ...]] = {
    "semantic_annotator": (),
    "budget_reviewer": (),
    "attention_reviewer": (),
    "runtime_supervisor": (),
    "incident_supervisor": (),
    "research_chat": (),
    "research_lead": (),
    "challenger": (),
    "investigator": (
        "theory-investigation",
        "numerical-investigation",
        "symbolic",
        "proof-writer",
        "analyze-results",
        "experiment-plan",
        "compute-dispatch",
        "plotting",
    ),
    "literature": ("tm-scholar", "pdf-reading", "research-lit", "novelty-check"),
    "judge": (),
    "reporter": ("report-writing",),
}

DESIGN_NAMES: dict[str, str] = {
    "report-writing": "report-format",
    "network-graph": "network/graph",
    "pdf-reading": "PDF-reading",
    "tm-scholar": "literature-research",
}


def thread_cycle_prefix(cycle_id: str, role: str) -> str:
    raw = str(cycle_id or "").strip()
    match = re.fullmatch(r"cycle-(\d+)", raw)
    if raw.isdigit():
        short_cycle = f"C{raw}"
    elif match:
        short_cycle = f"C{match.group(1)}"
    else:
        short_cycle = raw or "cycle"
    return f"[{short_cycle} · {role_display_name(role)}]"


def skills_root() -> Path:
    override = os.environ.get("THEORYMASTER_SKILLS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(str(files("theory_master").joinpath("resources", "skills")))


@dataclass(frozen=True)
class SkillRef:
    directory: str
    name: str
    description: str
    path: Path
    design_name: str
    scope: str = "system"

    @property
    def skill_md(self) -> Path:
        return self.path / "SKILL.md"


def _frontmatter_fields(text: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter")
    fields: dict[str, str] = {}
    key: str | None = None
    chunks: list[str] = []
    for raw in match.group(1).splitlines():
        if not raw.strip():
            continue
        if raw[:1] in {" ", "\t"} and key:
            chunks.append(raw.strip())
            continue
        if key:
            fields[key] = " ".join(chunks).strip().strip("'\"")
        name, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"invalid SKILL.md frontmatter line: {raw}")
        key = name.strip()
        chunks = [value.strip()]
    if key:
        fields[key] = " ".join(chunks).strip().strip("'\"")
    return fields


def parse_skill(path: Path, *, scope: str = "system") -> SkillRef:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"missing skill file: {skill_md}")
    fields = _frontmatter_fields(skill_md.read_text(encoding="utf-8"))
    name = fields.get("name") or path.name
    description = fields.get("description") or ""
    if not description:
        raise ValueError(f"skill missing description: {skill_md}")
    return SkillRef(path.name, name, description, path.resolve(), DESIGN_NAMES.get(path.name, path.name), scope)


def resolve_skill(directory: str, root: Path | None = None, *, scope: str = "system") -> SkillRef:
    path = (root or skills_root()) / directory
    if path.is_symlink():
        raise ValueError(f"linked skill directory is not allowed; copy the skill instead: {path}")
    if not path.exists():
        raise FileNotFoundError(f"missing skill directory: {path}")
    return parse_skill(path, scope=scope)


def task_skills_root(task_config_path: Path) -> Path:
    return task_config_path.expanduser().resolve().parent / ".theory-master" / "skills"


def _task_skill_names(task_config_path: Path | None, role: str) -> tuple[str, ...]:
    if task_config_path is None:
        return ()
    config_path = task_config_path.expanduser().resolve()
    try:
        import json
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read task skill config: {config_path}") from exc
    raw = config.get("task_skills") or {}
    if not isinstance(raw, dict):
        raise ValueError("task_skills must be an object mapping role -> skill directories")
    unknown_roles = sorted(str(key) for key in raw if str(key) not in ROLE_SKILLS)
    if unknown_roles:
        raise ValueError(f"task_skills contains unknown roles: {', '.join(unknown_roles)}")
    values = raw.get(role) or []
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"task_skills.{role} must be a list of skill directory names")
    names: list[str] = []
    for item in values:
        name = item.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError(f"invalid task-local skill directory: {item!r}")
        if name in names:
            raise ValueError(f"duplicate task-local skill for {role}: {name}")
        names.append(name)
    return tuple(names)


def role_skills(
    role: str,
    root: Path | None = None,
    *,
    task_config_path: Path | None = None,
) -> tuple[SkillRef, ...]:
    names = ROLE_SKILLS.get(role)
    if names is None:
        raise ValueError(f"unknown TheoryMaster role: {role}")
    system_refs = tuple(resolve_skill(name, root, scope="system") for name in names)
    task_names = _task_skill_names(task_config_path, role)
    overlap = sorted(set(names) & set(task_names))
    if overlap:
        raise ValueError(f"task-local skills may not shadow system skills for {role}: {', '.join(overlap)}")
    if not task_names:
        return system_refs
    local_root = task_skills_root(task_config_path)  # type: ignore[arg-type]
    task_refs = tuple(resolve_skill(name, local_root, scope="task") for name in task_names)
    return system_refs + task_refs


def loaded_skill_for_path(
    role: str,
    value: str | Path,
    root: Path | None = None,
    *,
    task_config_path: Path | None = None,
) -> SkillRef | None:
    """Return the whitelisted skill whose entry ``SKILL.md`` was read.

    Codex exposes completed file/command activity rather than a native skill-use
    notification.  Match both resolved paths (important for shared symlinks) and
    the stable ``<skill>/SKILL.md`` suffix used by relative shell commands.
    """
    candidate = Path(str(value)).expanduser()
    candidate_parts = candidate.parts
    for ref in role_skills(role, root, task_config_path=task_config_path):
        entry = ref.skill_md
        try:
            if candidate.resolve(strict=False) == entry.resolve(strict=False):
                return ref
        except OSError:
            pass
        suffix = (ref.directory, "SKILL.md")
        if len(candidate_parts) >= 2 and tuple(candidate_parts[-2:]) == suffix:
            return ref
    return None


def skill_prompt_block(
    role: str,
    root: Path | None = None,
    *,
    task_config_path: Path | None = None,
) -> str:
    refs = role_skills(role, root, task_config_path=task_config_path)
    if not refs:
        return (
            f"Optional skills for role {role}: none. "
            "Follow the identity block. Do not load other skills."
        )
    lines = [
        f"Optional skills for role {role} (SDK whitelist). "
        "Load a skill's SKILL.md only when this turn actually needs that capability. "
        "Do not use skills outside these two levels.",
    ]
    for scope, title in (("system", "System-level TheoryMaster skills"), ("task", "Task-local Master skills")):
        scoped = [ref for ref in refs if ref.scope == scope]
        if not scoped:
            continue
        lines.append(f"{title}:")
        for ref in scoped:
            alias = f" (design: {ref.design_name})" if ref.design_name != ref.directory else ""
            lines.append(f"- {ref.name}{alias}: {ref.skill_md}")
    return "\n".join(lines)


def developer_instructions(
    role: str,
    extra: str = "",
    root: Path | None = None,
    *,
    cycle_id: str | None = None,
    task_config_path: Path | None = None,
) -> str:
    parts: list[str] = []
    if cycle_id:
        parts.append(
            f"TheoryMaster Cycle: {cycle_id}\n"
            f"You are TheoryMaster {role_display_name(role)}."
        )
    parts.extend([identity_block(role), skill_prompt_block(role, root, task_config_path=task_config_path)])
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def turn_input(
    role: str,
    prompt: str,
    root: Path | None = None,
    *,
    include_skills: bool = True,
    task_config_path: Path | None = None,
) -> Any:
    """Build a turn payload without eagerly loading optional skills.

    The thread-init developer instructions expose the role's skill whitelist and
    concrete SKILL.md paths.  The agent reads a skill only when the current Stage
    actually needs it.  This keeps progressive disclosure intact; repeated turns
    carry only the incremental task.  ``include_skills`` remains for API
    compatibility but no longer preloads SkillInput objects.
    """
    role_skills(role, root, task_config_path=task_config_path)  # validate both whitelist levels eagerly; do not attach contents
    try:
        from openai_codex import TextInput  # type: ignore
    except ImportError:
        return prompt
    return [TextInput(text=prompt)]


def write_rule(role: str) -> str:
    if role == "reporter":
        return (
            "Return only the JSON required by the current Reporter stage. The SDK writes the canonical branch note and applies Canvas patches. "
            "Do not write calculation scripts, data, plots, notes_dir, Canvas, or the Scientific Model projection yourself. "
            "Keep note/Canvas narratives compact: report scientific conclusions, scope, limitations and evidence references only; "
            "never embed raw experimental tables, trajectory events, numerical arrays or full dataset payloads."
        )
    if role == "literature":
        return (
            "Write only your own reproducible reading artifacts (notes, extracted quotes, citation traces) under your cwd, "
            "this Cycle's Literature workspace cycle_workspace/literature/. scholarly_retrieval_root is SDK-materialized retrieval output: "
            "read it, do not edit or replace it. Do not write Canvas, notes, the Scientific Model, or anywhere outside your cwd. "
            "The authoritative TheoryMaster handoff is still the required JSON output."
        )
    if role in WRITE_ROLES:
        return (
            "Write calculation files and new artifacts only under the current Cycle write root (cycle_workspace; your cwd); "
            "treat the Task scientific root as read/navigation only. "
            "Never copy raw datasets (CSV/NPZ/data/pickle/event dumps) into task_home, notes_dir or Canvas; "
            "the scientific data belong in the non-sync benchmark_root or current cycle_workspace."
        )
    return "Do not write notes_dir, Canvas, or calculation scripts. Return JSON only."
