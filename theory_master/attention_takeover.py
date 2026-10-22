"""Compatibility names for the merged Supervisor intervention switch."""
from __future__ import annotations

from pathlib import Path

from .supervisor_intervention import supervisor_enabled, write_supervisor_setting


def attention_takeover_enabled(research_root: Path) -> bool:
    return supervisor_enabled(research_root)


def write_attention_takeover_setting(research_root: Path, enabled: bool) -> Path:
    return write_supervisor_setting(research_root, enabled)
