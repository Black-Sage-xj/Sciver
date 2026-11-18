from __future__ import annotations
from typing import TYPE_CHECKING

"Explicit invocation phases; the Workflow owns policy and persistence services."
import uuid
from pathlib import Path
from typing import Any
from .invocation_artifacts import InvocationArtifactTracker
from .invocations import InvocationLedger
from dataclasses import dataclass, field
from .invocation_lifecycle import InvocationLifecycle

NATIVE_INVESTIGATOR_STAGES = frozenset(
    {"investigate", "judge_rework", "integrate_literature"}
)
if TYPE_CHECKING:
    from .invocation_preparation import InvocationPreparation
    from .invocation_context import InvocationContextPreparation
    from .invocation_transport import InvocationTransport
    from .invocation_output import InvocationOutputValidation
    from .invocation_commit import InvocationCommit


@dataclass
class InvocationSession:
    commit: InvocationCommit = field(init=False)
    output_validation: InvocationOutputValidation = field(init=False)
    transport: InvocationTransport = field(init=False)
    context_preparation: InvocationContextPreparation = field(init=False)
    preparation: InvocationPreparation = field(init=False)
    owner: Any
    state: dict[str, Any]
    role: str
    context: dict[str, Any]
    resume_role: str | None
    fresh_thread: bool
    contract_role: str | None
    output_key: str | None
    actual: str
    stage: str
    contract: Any
    invocation_ledger: InvocationLedger
    logical_key: str
    threads: dict[str, str] | None = None
    thread_providers: dict[str, str] | None = None
    thread_key: str | None = None
    thread_id: str | None = None
    route: Any = None
    continuation: bool | None = None
    validation_context: dict[str, Any] | None = None
    agent_graph: dict[str, Any] | None = None
    graph_stamp: str | None = None
    initial_context: dict[str, Any] | None = None
    continuation_context: dict[str, Any] | None = None
    pending_guidance: dict[str, Any] | None = None
    prompt_context: dict[str, Any] | None = None
    native_preflight_goal: str | None = None
    invocation_id: str | None = None
    skill_binding: Any = None
    skill_versions: Any = None
    skill_protocols: Any = None
    skill_flags: Any = None
    skill_addendum: Any = None
    predecessor_id: Any = None
    artifact_tracker: InvocationArtifactTracker | None = None
    prompt: str | None = None
    use_native_goal: bool | None = None
    native_objective: str | None = None
    handoff_path: Path | None = None
    limit_binding: dict[str, Any] | None = None
    setup_context: str | None = None
    raw: str | None = None
    output: dict[str, Any] | None = None
    artifact_manifest: Path | None = None
    agent_output: dict[str, Any] | None = None
    semantic_audit: Any = None
    evidence_registration_audit: Any = None
    closure_artifact: Path | None = None
    closure_payload: dict[str, Any] | None = None
    closure_hash: str | None = None
    identifier: str | None = None
    guidance_view: dict[str, Any] | None = None
    lifecycle: InvocationLifecycle | None = None

    def __post_init__(self):
        from .invocation_preparation import InvocationPreparation
        from .invocation_context import InvocationContextPreparation
        from .invocation_transport import InvocationTransport
        from .invocation_output import InvocationOutputValidation
        from .invocation_commit import InvocationCommit

        self.preparation = InvocationPreparation(self)
        self.context_preparation = InvocationContextPreparation(self)
        self.transport = InvocationTransport(self)
        self.output_validation = InvocationOutputValidation(self)
        self.commit = InvocationCommit(self)

    def run(self):
        self.preparation._prepare_thread()
        self.context_preparation._prepare_context()
        self.invocation_id = (
            f"inv-{self.owner.cycle_dir.name}-{self.role}-{uuid.uuid4().hex[:12]}"
        )
        self.preparation._prepare_skills()
        self.lifecycle = InvocationLifecycle(
            self.owner,
            self.state,
            self.invocation_ledger,
            self.invocation_id,
            self.role,
            self.stage,
        )
        with self.lifecycle:
            self.lifecycle.phase = "setup"
            self.preparation._begin()
            self.transport._prepare_native_delivery()
            self.lifecycle.phase = "invoke"
            resumed_output = self.transport._invoke_initial()
            if resumed_output is not None:
                return resumed_output
            self.lifecycle.phase = "validation"
            self.output_validation._validate_and_repair()
            self.lifecycle.phase = "semantic_closure"
            self.commit._close_output()
            self.lifecycle.phase = "commit"
            return self.commit._commit_output()
