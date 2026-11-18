"""Trajectory-derived procedural memory; no model calls or task execution.

The store is independent of Workflow and scientific verdicts. The Sciver
adapter accepts optional sidecar notes from ordinary research invocations.
"""

from .store import SkillLibrary, SkillError

__all__ = ["SkillLibrary", "SkillError"]
