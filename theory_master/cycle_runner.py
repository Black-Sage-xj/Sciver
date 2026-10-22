"""Cycle execution boundary for the long-lived Workflow facade."""

from __future__ import annotations

from typing import Any, Protocol


class CycleOwner(Protocol):
    def _execute_cycle(
        self, resume_state: dict[str, Any] | None = None, *,
        same_cycle_continuation: bool = False,
    ) -> dict[str, Any]: ...


class CycleRunner:
    """Small coordinator that keeps the public Workflow lifecycle stable."""

    def __init__(self, owner: CycleOwner):
        self.owner = owner

    def run(self, resume_state: dict[str, Any] | None = None, *,
            same_cycle_continuation: bool = False) -> dict[str, Any]:
        if same_cycle_continuation:
            return self.owner._execute_cycle(
                resume_state, same_cycle_continuation=True
            )
        return self.owner._execute_cycle(resume_state)
