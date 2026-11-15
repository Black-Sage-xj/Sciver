"""Resolve the optional, operator-supplied Cycle audit integration.

Sciver does not bundle the external tmux launcher or trajectory-audit skill.
Resolution is lazy so an ordinary offline installation needs neither resource.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


ENV_NAMES = (
    "THEORY_MASTER_AUDIT_SKILL",
    "THEORY_MASTER_AUDIT_RELAY",
    "THEORY_MASTER_AUDIT_WORKDIR",
)


class AuditAdapterError(RuntimeError):
    """The optional audit integration is absent or incompletely configured."""


@dataclass(frozen=True)
class AuditAdapter:
    skill: Path
    relay: Path
    workdir: Path


def resolve_audit_adapter(
    *, environ: Mapping[str, str] | None = None, package_root: Path | None = None,
) -> AuditAdapter:
    """Require a complete explicit adapter, or an existing legacy integration.

    Partial explicit configuration never silently borrows a path from the local
    monorepo. All paths are validated before callers create audit artifacts.
    """
    env = os.environ if environ is None else environ
    values = [str(env.get(name) or "").strip() for name in ENV_NAMES]
    if any(name in env for name in ENV_NAMES):
        missing = [name for name, value in zip(ENV_NAMES, values) if not value]
        if missing:
            raise AuditAdapterError(
                "Optional Cycle audit adapter is incomplete; set absolute paths for: "
                + ", ".join(missing)
            )
        for name, value in zip(ENV_NAMES, values):
            if not Path(value).is_absolute():
                raise AuditAdapterError(f"{name} must be an absolute path: {value}")
        adapter = AuditAdapter(*(Path(value).resolve() for value in values))
    else:
        package = (package_root or Path(__file__).resolve().parents[1]).resolve()
        tool = package.parent
        adapter = AuditAdapter(
            tool / ".codex/skills/tm-audit-review/SKILL.md",
            tool / "python_script/start_origin_tmux_relay.py",
            tool.parent,
        )
        if tool.name != "tool" or not adapter.skill.is_file() or not adapter.relay.is_file():
            raise AuditAdapterError(
                "Optional Cycle audit adapter is not configured. Set absolute paths in "
                + ", ".join(ENV_NAMES)
                + "; Cycle audit is disabled by default."
            )
    if not adapter.skill.is_file():
        raise FileNotFoundError(f"Cycle audit skill file not found: {adapter.skill}")
    if not adapter.relay.is_file():
        raise FileNotFoundError(f"Cycle audit relay file not found: {adapter.relay}")
    if not adapter.workdir.is_dir():
        raise NotADirectoryError(f"Cycle audit working directory not found: {adapter.workdir}")
    return adapter
