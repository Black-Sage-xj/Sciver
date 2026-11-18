"""Investigator native Codex Goal, separate from Judge/Findings Contract.

Installed openai-codex 0.155.1 exposes goal operations through CodexClient.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .scientific_objective import (
    serialize_frozen_science as _compact_frozen_science, frozen_science_digest,
)


class NativeGoalIncomplete(RuntimeError):
    """A native Goal stopped without its complete status; never submit Findings."""

    def __init__(self, status: str, thread_id: str):
        self.status = status
        self.thread_id = thread_id
        super().__init__(f"investigator native Goal is {status}, not scientifically complete")


class NativeGoalBudgetExceeded(RuntimeError):
    """A live Codex usage event reached TM allowance BEFORE native Goal completed."""

    def __init__(self, thread_id: str, observed_tokens: int, allowance_tokens: int):
        self.thread_id = thread_id
        self.observed_tokens = int(observed_tokens)
        self.allowance_tokens = int(allowance_tokens)
        super().__init__(
            f"Investigator Native Goal live budget exceeded: "
            f"{self.observed_tokens} / {self.allowance_tokens} tokens"
        )


class NativeGoalDeliveryError(NativeGoalIncomplete):
    """A completed Goal did not provide a usable administrative findings handoff."""

    def __init__(self, thread_id: str, detail: str):
        super().__init__("deliveryInvalid", thread_id)
        self.detail = detail
        self.args = (f"Investigator Goal completed, but findings handoff is invalid: {detail}",)


class NativeGoalExecutionLimit(RuntimeError):
    """An invocation-bound input limitation, never Findings or a Judge verdict."""

    def __init__(self, thread_id: str, execution_limit: dict[str, Any], handoff_path: Path):
        self.thread_id = thread_id
        self.execution_limit = execution_limit
        self.handoff_path = str(handoff_path)
        super().__init__(execution_limit["reason"])


@dataclass(frozen=True)
class NativeGoalResult:
    status: str
    thread_id: str
    physical_turns: int
    tools_started: int
    final_response: str | None = None


# External Codex Goal accepts at most 4000 characters. Leave a safety margin
# and NEVER truncate the immutable scientific objective to fit the budget.
CODEX_GOAL_MAX_CHARS = 4000
NATIVE_GOAL_SAFE_CHARS = 3800
FROZEN_CONTEXT_SHA_MARKER = "FROZEN_OBJECTIVE_CONTEXT_SHA256="
JUDGE_REQUESTS_SHA_MARKER = "JUDGE_REQUESTS_CONTEXT_SHA256="
EXECUTION_LIMIT_PROTOCOL_MARKER = "SDK_EXECUTION_LIMIT_HANDOFF_V1"
EXECUTION_LIMIT_SCHEMA = "theory-master/execution-limit-handoff/v1"
EXECUTION_LIMIT_MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
EXECUTION_LIMIT_INSTRUCTION = (
    f"{EXECUTION_LIMIT_PROTOCOL_MARKER}: Only an indispensable input/support gap "
    "remaining after a bounded source-recovery audit permits Setup's execution_limit "
    "handoff. Method failure or cost alone does not. This closes execution without "
    "a completed test, Findings or scientific verdict; keep frozen science unchanged. "
)


def verify_native_goal_context_reference(goal: str, setup_context: str) -> None:
    """Fail closed before any model call if Goal references missing/changed science."""
    if FROZEN_CONTEXT_SHA_MARKER not in goal:
        return
    first = goal.split(FROZEN_CONTEXT_SHA_MARKER, 1)[1].splitlines()[0].strip()
    if len(first) != 64 or any(ch not in "0123456789abcdef" for ch in first):
        raise ValueError("invalid native Goal frozen-science context digest")
    try:
        source = setup_context.lstrip()
        if source.startswith("Load SDK context for the upcoming Native Goal "):
            source = source.partition("\n")[2].lstrip()
        parsed, _ = json.JSONDecoder().raw_decode(source)
        objective = parsed["frozen_objective"]
        if not isinstance(objective, dict):
            raise TypeError("missing objective")
        actual = frozen_science_digest(objective)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("native Goal refers to absent or malformed frozen scientific setup context") from exc
    if actual != first:
        raise ValueError("native Goal frozen-science context hash mismatch; do not start the Goal")
    if JUDGE_REQUESTS_SHA_MARKER in goal:
        declared = goal.split(JUDGE_REQUESTS_SHA_MARKER, 1)[1].splitlines()[0].strip()
        objections = parsed.get("judge_objections")
        if not isinstance(objections, list) or not objections:
            raise ValueError("native Goal Judge rework reference has no requested_resolution context")
        requests = [str(o.get("requested_resolution") or "").strip() for o in objections if isinstance(o, dict)]
        if not requests or not all(requests):
            raise ValueError("native Goal Judge rework reference has malformed requests")
        actual_requests = hashlib.sha256(
            json.dumps(requests, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if declared != actual_requests:
            raise ValueError("native Goal Judge rework requested_resolution hash mismatch")


def _bounded_goal(
    rendered: str, *, stage: str, scientific_objective: dict[str, Any],
    allow_setup_reference: bool, short_target: str, short_instruction: str,
    judge_requests: list[str] | None = None,
) -> str:
    """Prefer verbatim H/scope/test; reference the full setup only if too long."""
    if len(rendered) <= NATIVE_GOAL_SAFE_CHARS:
        return rendered
    if not allow_setup_reference:
        raise ValueError(
            f"native Goal objective exceeds Codex's {CODEX_GOAL_MAX_CHARS}-character limit "
            f"or TM's {NATIVE_GOAL_SAFE_CHARS}-character safety budget: {len(rendered)}"
        )
    digest = frozen_science_digest(scientific_objective)
    # Judge rework may itself contain >4000 characters of objections. The
    # objections are already in full in setup; preserve by their own hash.
    if stage == "judge_rework":
        if not judge_requests:
            raise ValueError("long Judge rework requires original requested_resolution")
        short_target = "Resolve ALL exact Judge requested_resolution items in setup Context."
        requests_hash = hashlib.sha256(
            json.dumps(judge_requests, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        requests_ref = f"{JUDGE_REQUESTS_SHA_MARKER}{requests_hash}\n"
    else:
        requests_ref = ""
    bounded = (
        f"{short_target}\n\n"
        "FROZEN SCIENCE (exact Setup reference):\n"
        "Read the FULL, exact frozen_objective (hypothesis, scope, decisive_test) "
        "from the immediately preceding SDK Setup Context. Its original values "
        "are authoritative; do not summarize, weaken, or reconstruct them.\n"
        f"{FROZEN_CONTEXT_SHA_MARKER}{digest}\n"
        f"{requests_ref}"
        f"\n{short_instruction}"
    )
    if len(bounded) > NATIVE_GOAL_SAFE_CHARS:
        raise ValueError("SDK native Goal reference still exceeds character safety budget")
    return bounded



def investigator_goal(stage: str, context: dict[str, Any], *, layout: str = "v1",
                      allow_setup_reference: bool = False) -> str:
    """Frozen science belongs in the persisted Goal; fixed role/tools do not."""
    if stage not in {"investigate", "judge_rework", "integrate_literature"}:
        raise ValueError(f"unsupported Investigator Goal stage: {stage}")
    if layout not in {"v1", "v2"}:
        raise ValueError("Goal layout must be v1 (baseline) or v2 (internal decisive chain)")
    objective = context.get("frozen_objective")
    if not isinstance(objective, dict) or not all(str(objective.get(k) or "").strip()
                                                      for k in ("hypothesis", "scope", "decisive_test")):
        raise ValueError("Investigator Goal requires complete frozen hypothesis/scope/decisive_test")
    # Full scientific values remain verbatim. Only deterministic JSON punctuation
    # and whitespace are compacted, keeping the Goal readable without lossy edits.
    locked = _compact_frozen_science(objective)
    if stage == "judge_rework":
        objections = context.get("judge_objections")
        if not isinstance(objections, list) or not objections:
            raise ValueError("Judge rework Goal requires actual requested_resolution")
        requests = [str(o.get("requested_resolution") or "").strip() for o in objections if isinstance(o, dict)]
        if not requests or not all(requests):
            raise ValueError("Judge rework has missing requested_resolution")
        target = ("Resolve the Judge's exact outstanding scientific objections in the RETAINED Investigator thread:\n"
                  + "\n".join(f"{i}. {v}" for i, v in enumerate(requests, 1)))
    else:
        target = ("Investigate the frozen Hypothesis until sufficient evidence is obtained "
                  "for independent adjudication under its decisive_test, rather than "
                  "stopping at a local result.")
    if layout == "v2":
        # Native Goal owns completion science; Developer Instructions own stable
        # role/tool policy; Setup owns full inputs, paths and delivery mechanics.
        # Avoid injecting the old Stage Card, schema, or workflow bookkeeping here.
        # Historical-result reuse stays in the Investigator identity. Keep one
        # pre-compute reminder here and do not repeat that rule.
        # Verification follows the frozen object: a proof is not failed for
        # lacking a blind dataset; a test that freezes prediction, isolation,
        # or error bounds still requires them.
        work_order = (
            "JUDGE REWORK: Finish the missing prerequisites and exact requested "
            "scientific comparisons, using valid in-scope alternatives if old "
            "data/methods are ineligible. Neither a weaker surrogate test nor "
            "a partial Finding resolves the Judge request. Do not treat "
            "post-hoc generated data as independent holdout/gate Evidence, "
            "or previously exposed results as new independent evidence."
            if stage == "judge_rework" else
            "LITERATURE INTEGRATION: Verify supplied Literature Evidence and "
            "its applicability; complete any newly feasible deductions or "
            "numerical comparisons under the same frozen test. A source "
            "summary is not research completion. Use the verification that "
            "frozen test requires; do not invent a blind dataset for a proof, "
            "and do not replace a required isolated or error-bounded comparison "
            "with exposed history. "
            if stage == "integrate_literature" else
            "SCIENTIFIC COMPLETION CHAIN: qualify inputs. "
            "A proof needs no blind dataset. Statistical or numerical tests keep "
            "prespecified predictions, data isolation and error checks. "
            "execute the complete decisive test; perform independent checks "
            "appropriate to it and preserve reproducible evidence. "
            "Support, falsification or true inconclusive require a completed "
            "frozen test; partial work is not completion. Exposed or post-hoc "
            "results cannot impersonate new independent evidence."
        )
        operational = (
            "EXECUTION AND EXIT: Estimate "
            "feasibility and scientific value before expensive methods; cite an "
            "applicable prior result when it already answers this frozen step, "
            "and do not skip a required independent check; abandon "
            "unaffordable METHODS, never the Goal. Worst-case sample bounds "
            "must not force endless searches; use bounded exploration. "
            "Never self-terminate or request native blocked for failed methods. "
            "Record gaps and continue in-scope. Native runtime blocked is an "
            "execution interruption for SDK diagnosis, never scientific completion. Only SDK review "
            "at an actual Investigator token budget boundary can decide "
            "further spending. "
            "Resource limits need no proof of mathematical impossibility. "
            "Never invent results or "
            "silently weaken the frozen "
            "estimand/scope. Unperformed tests are not inconclusive. Resource "
            "guards are token/usage/operator, NOT elapsed time. Native Goal "
            "completion does not replace independent Judge adjudication. "
            + EXECUTION_LIMIT_INSTRUCTION
        )
        guidance = context.get("operator_guidance")
        directed = bool(isinstance(guidance, dict) and str(guidance.get("text") or "").strip())
        if directed:
            operational += (
                " HUMAN EXECUTION DIRECTION: follow the one-shot strategy in "
                "Setup Context within frozen science; advice is not Evidence."
            )
        rendered = (
            f"{target}\n\n"
            f"FROZEN SCIENTIFIC OBJECTIVE (immutable):\n{locked}\n\n"
            f"{work_order}\n{operational}"
        )
        return _bounded_goal(
            rendered, stage=stage, scientific_objective=objective,
            allow_setup_reference=allow_setup_reference, short_target=target,
            short_instruction=f"{work_order}\n{operational}",
            judge_requests=requests if stage == "judge_rework" else None,
        )
    objective = (f"{target}\n\nFROZEN SCIENTIFIC OBJECTIVE (DO NOT CHANGE):\n{locked}\n\n"
            "Completion: establish evidence sufficient for independent Judge adjudication of the whole "
            "frozen test. A valid result may support, contradict or be genuinely inconclusive AFTER "
            "the required discriminating test has actually been executed. Obtain or construct permitted "
            "missing inputs, debug failed numerical/theoretical steps and continue the same scientific "
            "research until all necessary prerequisites and comparisons are completed. "
            "A partial Finding, unperformed experiment, or difficulty alone is NOT completion. "
            "Only rigorous impossibility under the exact frozen scope may replace execution. "
            "Never fabricate science or silently weaken scope. "
            "Use the existing fixed Developer Instructions for tools, permissions and skill policy; "
            "those are NOT replaced by this Goal. "
            "You must not voluntarily set this Native Goal to blocked or request "
            "an early stop for scientific difficulty. State unsolved gaps and "
            "continue legitimate in-scope exploration; SDK/operator alone "
            "control usage, budget, interruption and recovery. Never suppress "
            "a genuine SDK/provider status. Native runtime blocked is an execution "
            "interruption for SDK diagnosis, never scientific completion. Do not claim Goal complete or "
            "submit findings[] for unfinished science. "
            "On real Goal completion, preserve reproducible science and hand off the authored "
            "claims according to the separate setup delivery instruction; then an INDEPENDENT Judge adjudicates. "
            + EXECUTION_LIMIT_INSTRUCTION)
    return _bounded_goal(
        objective, stage=stage, scientific_objective=context["frozen_objective"],
        allow_setup_reference=allow_setup_reference, short_target=target,
        short_instruction=(
            "Complete the frozen decisive test and prerequisites before submission; "
            "preserve evidence for independent Judge review. Do not voluntarily "
            "block/stop this Goal for scientific difficulty; report precise "
            "gaps, try valid alternatives, and obey SDK/operator resource guards. "
            + EXECUTION_LIMIT_INSTRUCTION
        ),
        judge_requests=requests if stage == "judge_rework" else None,
    )


def native_goal_setup_turn(context_prompt: str, goal_objective: str = "") -> str:
    """Build the exact setup Turn, without duplicating science already in Goal.

    Long Goals that reference Setup must retain the full frozen_objective.
    For fully inline Goals the same exact science is already in the native
    objective, so Setup only needs navigation, prior evidence, Judge metadata,
    and the SDK's explicit handoff interface. No scientific text is compressed.
    """
    setup = context_prompt
    if goal_objective and FROZEN_CONTEXT_SHA_MARKER not in goal_objective:
        try:
            ctx, end = json.JSONDecoder().raw_decode(context_prompt.lstrip())
        except (ValueError, TypeError):
            ctx = None
            end = 0
        if isinstance(ctx, dict) and isinstance(ctx.get("frozen_objective"), dict):
            frozen = ctx["frozen_objective"]
            # Remove only if that exact canonical science is in this Goal.
            if _compact_frozen_science(frozen) in goal_objective:
                ctx = {k: v for k, v in ctx.items() if k != "frozen_objective"}
                setup = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
                setup += context_prompt.lstrip()[end:]
    return (
        "Load SDK context for the upcoming Native Goal (not a second research target). "
        "Do not execute science, submit Findings or change the Goal in this setup turn. "
        "Confirm readiness briefly.\n" + setup
    )


def run_native_goal(thread: Any, objective: str, *,
                    on_event: Callable[[Any], None] | None = None,
                    on_turn_boundary: Callable[[Any], None] | None = None) -> NativeGoalResult:
    """Use the SDK's genuine thread-scoped Goal logical stream, never a normal Turn.

    Scientific Findings require native `complete`. The separate administrative
    input-limit hook may interrupt only at a verified physical turn boundary.
    Scientific duration has NO elapsed-time limit. Native token/usage budgets,
    explicit operator pause/cancel and genuine errors retain their own statuses.
    """
    if len(objective) > CODEX_GOAL_MAX_CHARS:
        raise ValueError(
            f"native Goal objective exceeds Codex's {CODEX_GOAL_MAX_CHARS}-character limit: {len(objective)}"
        )
    from openai_codex._goal import _GoalNotificationStream  # installed pinned SDK 0.155.1
    from openai_codex.generated.v2_all import (
        TurnStartedNotification, TurnCompletedNotification, TurnStatus, ThreadGoalStatus,
    )
    from .observability.codex_events import (
        _get, extract_agent_text, extract_final_from_turn, turn_id_of,
    )

    client = thread._client
    state, logical_turn_id = client.start_goal_operation(thread.id, objective)
    active_physical_turn = None
    boundary_cancel_requested = False

    def next_notification():
        # The official cursor intentionally hides intermediate physical turn
        # completions. Observe only their safe boundary, preserving its logical
        # stream and all ordinary event/Collector semantics unchanged.
        nonlocal active_physical_turn, boundary_cancel_requested
        event = client.next_goal_notification(state)
        payload = _get(event, "payload")
        method = str(getattr(event, "method", ""))
        if (method == "turn/started" and isinstance(payload, TurnStartedNotification)
                and payload.thread_id == thread.id):
            active_physical_turn = payload.turn.id
        elif (method == "turn/completed" and isinstance(payload, TurnCompletedNotification)
                and payload.thread_id == thread.id
                and payload.turn.id == active_physical_turn):
            if (on_turn_boundary is not None and payload.turn.status == TurnStatus.completed
                    and payload.turn.error is None and state.status == ThreadGoalStatus.active
                    and not state.interrupt_requested and not state.cleared):
                try:
                    on_turn_boundary(event)
                except KeyboardInterrupt:
                    # The official stream has its own KeyboardInterrupt cancel
                    # branch. Let it own that request and skip our outer retry.
                    boundary_cancel_requested = True
                    raise
                except BaseException:
                    # Cancel before the official cursor unregisters and clears
                    # its current-turn routing state while unwinding the hook.
                    client.cancel_goal_operation(state)
                    boundary_cancel_requested = True
                    raise
            active_physical_turn = None
        return event

    stream = _GoalNotificationStream(
        state=state,
        next_notification=next_notification if on_turn_boundary is not None
                          else lambda: client.next_goal_notification(state),
        unregister=lambda: client.unregister_goal_operation(state),
        cancel_goal=lambda: client.cancel_goal_operation(state),
    )
    turns = 0
    tools = 0
    final_response = None
    try:
        for event in stream:
            method = str(getattr(event, "method", ""))
            payload = _get(event, "payload")
            scoped_payload = _get(payload, "params") or payload
            event_thread = _get(scoped_payload, "thread_id", "threadId")
            event_turn = turn_id_of(scoped_payload)
            # The SDK coalesces physical turns under this operation's logical
            # ID. Never recover text from another Goal or from thread history.
            if ((not event_thread or event_thread == thread.id)
                    and (not event_turn or event_turn == logical_turn_id)):
                text = None
                if method == "item/completed":
                    text = extract_agent_text(payload)
                elif method == "turn/completed":
                    text = extract_final_from_turn(scoped_payload)
                if text:
                    # Completed assistant items are public reports; tool output
                    # and private reasoning are excluded by these extractors.
                    final_response = text
            if method == "turn/started":
                turns += 1
            if method == "item/started":
                item = getattr(getattr(event, "payload", None), "item", None)
                payload_item = getattr(item, "root", item)
                name = type(payload_item).__name__.lower()
                if any(token in name for token in ("command", "tool", "function", "mcp")):
                    tools += 1
            if on_event is not None:
                on_event(event)
    except BaseException:
        # A failed Python/SKD stream must not leave an orphaned active Goal.
        if not boundary_cancel_requested:
            client.cancel_goal_operation(state)
        raise
    finally:
        stream.close()
    status = state.status.value if state.status is not None else "unknown"
    return NativeGoalResult(status=status, thread_id=thread.id,
                            physical_turns=turns, tools_started=tools,
                            final_response=final_response)


def _safe_workspace_bytes(workspace_path: Path, relative: Path, *, max_bytes: int) -> bytes:
    """Read a bounded regular file through directory FDs, without following links."""
    root = Path(workspace_path)
    if (not root.is_absolute() or ".." in root.parts or relative.is_absolute()
            or not relative.parts or ".." in relative.parts):
        raise ValueError("execution-limit files require safe workspace-relative paths")
    parts = (*root.parts[1:], *relative.parts)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
                raise ValueError("execution-limit source must be a nonempty bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(max_bytes + 1)
            if not 0 < len(data) <= max_bytes:
                raise ValueError("execution-limit source changed size or exceeds its limit")
            return data
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def execution_limit_setup_binding(context_prompt: str, handoff_path: Path) -> dict[str, str] | None:
    """Read trusted SDK Setup metadata; historical/findings-only callers have none."""
    try:
        context, _ = json.JSONDecoder().raw_decode(context_prompt.lstrip())
    except (ValueError, TypeError):
        return None
    binding = context.get("sdk_execution_limit_handoff") if isinstance(context, dict) else None
    if binding is None:
        return None
    expected = {"schema", "invocation_id", "frozen_objective_sha256", "workspace_path", "handoff_path"}
    if not isinstance(binding, dict) or set(binding) != expected or binding.get("schema") != EXECUTION_LIMIT_SCHEMA:
        raise ValueError("invalid SDK execution-limit binding")
    if any(not isinstance(binding[key], str) or not binding[key].strip() for key in expected):
        raise ValueError("empty SDK execution-limit binding")
    path = Path(handoff_path)
    if (binding["handoff_path"] != str(path)
            or path != Path(binding["workspace_path"]) / ".tm-handoff" / (binding["invocation_id"] + ".json")):
        raise ValueError("SDK execution-limit binding does not match this invocation handoff")
    digest = binding["frozen_objective_sha256"]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("invalid SDK frozen-objective digest")
    if (isinstance(context.get("frozen_objective"), dict)
            and frozen_science_digest(context["frozen_objective"]) != digest):
        raise ValueError("SDK execution-limit frozen-objective binding changed")
    return binding


def read_execution_limit_handoff(
    result: NativeGoalResult, handoff_path: Path, *, invocation_id: str,
    frozen_objective_sha256: str, workspace_path: Path,
) -> dict[str, Any] | None:
    """Validate authored input limits, never infer a limitation from public prose.

    This attests the invocation, frozen text and local source bytes only. The
    scientific sufficiency of the author's recovery audit is not machine certified.
    """
    if result.status not in {"complete", "blocked", "execution_limited"}:
        raise NativeGoalIncomplete(result.status, result.thread_id)
    path = Path(handoff_path)
    root = Path(workspace_path)
    try:
        if (not invocation_id or path != root / ".tm-handoff" / (invocation_id + ".json")
                or len(frozen_objective_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in frozen_objective_sha256)):
            raise ValueError("execution-limit handoff has no exact invocation/frozen-objective binding")
        try:
            raw = _safe_workspace_bytes(root, path.relative_to(root), max_bytes=1024 * 1024)
        except FileNotFoundError:
            return None
        authored = json.loads(raw)
        if not isinstance(authored, dict) or "execution_limit" not in authored:
            return None
        if set(authored) != {"execution_limit"}:
            raise ValueError("execution_limit cannot be mixed with Findings or other handoffs")
        limit = authored["execution_limit"]
        fields = {"kind", "invocation_id", "frozen_objective_sha256", "reason", "checked_alternatives",
                  "unexecuted_test", "required_dependency", "evidence"}
        if not isinstance(limit, dict) or set(limit) != fields:
            raise ValueError("execution_limit requires the exact SDK input-limit schema")
        if (limit["kind"] != "input_support_unavailable" or limit["invocation_id"] != invocation_id
                or limit["frozen_objective_sha256"] != frozen_objective_sha256):
            raise ValueError("execution_limit kind, invocation or frozen objective does not match")
        for key in ("reason", "unexecuted_test", "required_dependency"):
            if not isinstance(limit[key], str) or not 0 < len(limit[key].strip()) <= 12_000:
                raise ValueError(f"execution_limit.{key} requires bounded nonempty text")
        alternatives = limit["checked_alternatives"]
        if (not isinstance(alternatives, list) or not 0 < len(alternatives) <= 32
                or any(not isinstance(row, str) or not 0 < len(row.strip()) <= 4000 for row in alternatives)):
            raise ValueError("execution_limit requires a bounded, nonempty source-recovery audit")
        evidence = limit["evidence"]
        if not isinstance(evidence, list) or not 0 < len(evidence) <= 32:
            raise ValueError("execution_limit requires existing local evidence")
        seen = set()
        total = 0
        for row in evidence:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                raise ValueError("execution-limit evidence requires exactly path and sha256")
            if not isinstance(row["path"], str) or not row["path"].strip():
                raise ValueError("execution-limit evidence path must be nonempty")
            relative = Path(row["path"])
            if (relative.as_posix() != row["path"] or row["path"] in seen
                    or relative == path.relative_to(root)):
                raise ValueError("execution-limit evidence path must be unique, canonical and independent of the handoff")
            data = _safe_workspace_bytes(root, relative, max_bytes=EXECUTION_LIMIT_MAX_EVIDENCE_BYTES)
            total += len(data)
            if total > 32 * 1024 * 1024:
                raise ValueError("execution-limit evidence exceeds total size limit")
            if not isinstance(row["sha256"], str) or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("execution-limit evidence SHA-256 does not match existing source bytes")
            seen.add(row["path"])
        return limit
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise NativeGoalDeliveryError(result.thread_id, "execution_limit: " + str(exc)) from exc


def read_goal_findings(result: NativeGoalResult, handoff_path: Path) -> str:
    """Map authored claims to the contract without a new model turn or science inference.

    An invocation-specific path is supplied by SDK. Research notes, proofs and
    numerical outputs are unrestricted; only this tiny handoff is machine-owned.
    Scientific method, result, and validated provenance declarations survive verbatim
    into Evidence and Judge. Provenance remains Investigator-authored and is not
    independently attested by this handoff adapter; other research fields remain in
    freely organized workspace files.
    """
    if result.status != "complete":
        raise NativeGoalIncomplete(result.status, result.thread_id)
    path = Path(handoff_path)
    try:
        if not path.is_file() or path.is_symlink():
            raise ValueError("explicit handoff file does not exist or is a symlink")
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("handoff exceeds 1 MiB; put detailed calculations in research files")
        authored = json.loads(path.read_text(encoding="utf-8"))
        items = authored.get("findings") if isinstance(authored, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("expected a nonempty findings array")
        findings = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                claim = item
            elif isinstance(item, dict):
                claim = item.get("claim", item.get("statement"))
                if "claim" in item and "statement" in item and item["claim"] != item["statement"]:
                    raise ValueError(f"finding {index}: conflicting claim and statement")
            else:
                raise ValueError(f"finding {index}: expected a claim string or object")
            if not isinstance(claim, str) or not claim.strip():
                raise ValueError(f"finding {index}: missing nonempty claim/statement")
            finding = {"statement": claim}
            if isinstance(item, dict):
                if "method" in item:
                    if not isinstance(item["method"], str) or not item["method"].strip():
                        raise ValueError(f"finding {index}: method must be nonempty text when present")
                    finding["method"] = item["method"]
                if "result" in item:
                    if item["result"] is None:
                        raise ValueError(f"finding {index}: result must not be null when present")
                    finding["result"] = item["result"]
                if "provenance" in item:
                    from .contract_stages import validate_investigator_provenance
                    validate_investigator_provenance(item["provenance"], path=f"finding {index}.provenance")
                    finding["provenance"] = item["provenance"]
            findings.append(finding)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise NativeGoalDeliveryError(result.thread_id, str(exc)) from exc
    return json.dumps({"findings": findings}, ensure_ascii=False)
