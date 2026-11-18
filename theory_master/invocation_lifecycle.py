"""One failure boundary for every phase of an SDK-owned invocation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .invocations import InvocationLedger, TERMINAL_STATES
from .invocation_artifacts import InvocationArtifactTracker
from .native_goal import NativeGoalExecutionLimit
from .provider_errors import ProviderQuotaExhausted
from .runtime import write_json
from .workflow_support import RecoveryStop


@dataclass
class InvocationLifecycle:
    owner: Any
    state: dict[str, Any]
    ledger: InvocationLedger
    invocation_id: str
    role: str
    stage: str
    tracker: InvocationArtifactTracker | None = None
    phase: str = "setup"

    def __enter__(self) -> InvocationLifecycle:
        return self

    def _manifest(self, status: str) -> Path | None:
        if self.tracker is None:
            return None
        path = self.tracker.finalize(status=status)
        # Evidence registration needs the file inventory before semantic closure.
        # A later semantic failure must not leave that inventory labelled success.
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != status:
            write_json(path, {**payload, "status": status})
        return path

    def __exit__(self, kind, error, traceback) -> bool:
        current = self.ledger.snapshot(self.invocation_id)
        if current is None:
            return False
        recorded = str(current.get("state") or "")
        active = self.state.get("active_invocation_id")
        own_active = active == self.invocation_id or (
            active is None
            and (
                self.state.get("last_invocation_id") == self.invocation_id
                or (self.state.get("recovery") or {}).get("invocation_id")
                == self.invocation_id
            )
        )
        try:
            if error is None:
                return False
            if own_active:
                self.state["last_invocation_failure"] = {
                    "invocation_id": self.invocation_id,
                    "role": self.role,
                    "stage": self.stage,
                    "failure_phase": self.phase,
                    "error_type": type(error).__name__,
                }
            from .supervisor_incident import NativeGoalRuntimeFailure

            if isinstance(error, NativeGoalRuntimeFailure):
                self.state["native_goal"] = dict(
                    getattr(self.owner.backend, "last_native_goal", {}) or {}
                )
            interrupted = isinstance(
                error,
                (
                    ProviderQuotaExhausted,
                    RecoveryStop,
                    NativeGoalExecutionLimit,
                    KeyboardInterrupt,
                    SystemExit,
                ),
            )
            terminal = "interrupted" if interrupted else "failed"
            # A durable semantic closure can be safely reused after checkpoint
            # failure. Keep that completed/not_committed contract honest.
            reusable = recorded in {"completed", "committed"} and bool(
                current.get("closure_hash")
            )
            if reusable:
                terminal = recorded
            elif recorded in TERMINAL_STATES:
                terminal = recorded
            manifest_status = "completed" if reusable else terminal
            manifest = self._manifest(manifest_status)
            if recorded not in TERMINAL_STATES or reusable:
                self.ledger.transition(
                    self.invocation_id,
                    terminal,
                    role=self.role,
                    stage=self.stage,
                    failure_phase=self.phase,
                    error_type=type(error).__name__,
                    error=str(error),
                    commit_state=current.get("commit_state") or "not_committed",
                    invocation_artifact_manifest=str(manifest) if manifest else None,
                )
            elif manifest and not current.get("invocation_artifact_manifest"):
                self.ledger.transition(
                    self.invocation_id,
                    terminal,
                    invocation_artifact_manifest=str(manifest),
                    failure_phase=self.phase,
                )
        except Exception as cleanup_error:
            # Never turn a typed quota/control signal into a generic cleanup error.
            # Persist the secondary failure for the outer Cycle's recovery record.
            self.state.setdefault("invocation_cleanup_errors", []).append(
                {
                    "invocation_id": self.invocation_id,
                    "phase": self.phase,
                    "error_type": type(cleanup_error).__name__,
                    "error": str(cleanup_error),
                }
            )
        finally:
            if own_active:
                self.state["last_invocation_id"] = self.invocation_id
                self.state["active_invocation_id"] = None
                if error is not None:
                    self.state["execution_active"] = False
                    self.state["active_role"] = None
                    self.state["current_agent"] = None
                    try:
                        self.owner._persist(self.state)
                    except Exception as persist_error:
                        self.state.setdefault("invocation_cleanup_errors", []).append(
                            {
                                "invocation_id": self.invocation_id,
                                "phase": "failure_checkpoint",
                                "error_type": type(persist_error).__name__,
                                "error": str(persist_error),
                            }
                        )
        return False
