"""Investigator native Codex Goal, separate from Judge/Findings Contract.

Installed openai-codex 0.155.1 exposes goal operations through CodexClient.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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


def _compact_frozen_science(objective: dict[str, Any]) -> str:
    """One canonical serialization for Goal display and Setup integrity.

    Only JSON syntax whitespace is removed; scientific field values are never
    shortened, normalized, summarized, or rewritten.
    """
    canonical = {key: objective[key] for key in ("hypothesis", "scope", "decisive_test")}
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))


def frozen_science_digest(objective: dict[str, Any]) -> str:
    """Digest the exact frozen science, using the same compact Goal serialization."""
    return hashlib.sha256(_compact_frozen_science(objective).encode("utf-8")).hexdigest()


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
            "Record gaps and continue in-scope. Only SDK review "
            "at an actual Investigator token budget boundary can decide "
            "further spending. "
            "Resource limits need no proof of mathematical impossibility. "
            "Never invent results or "
            "silently weaken the frozen "
            "estimand/scope. Unperformed tests are not inconclusive. Resource "
            "guards are token/usage/operator, NOT elapsed time. Native Goal "
            "completion does not replace independent Judge adjudication."
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
            "a genuine SDK/provider status. Do not claim Goal complete or "
            "submit findings[] for unfinished science. "
            "On real Goal completion, preserve reproducible science and hand off the authored "
            "claims according to the separate setup delivery instruction; then an INDEPENDENT Judge adjudicates.")
    return _bounded_goal(
        objective, stage=stage, scientific_objective=context["frozen_objective"],
        allow_setup_reference=allow_setup_reference, short_target=target,
        short_instruction=(
            "Complete the frozen decisive test and prerequisites before submission; "
            "preserve evidence for independent Judge review. Do not voluntarily "
            "block/stop this Goal for scientific difficulty; report precise "
            "gaps, try valid alternatives, and obey SDK/operator resource guards. "
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
                    on_event: Callable[[Any], None] | None = None) -> NativeGoalResult:
    """Use the SDK's genuine thread-scoped Goal logical stream, never a normal Turn.

    Only the native `complete` status permits the SDK to consume the authored handoff.
    Scientific duration has NO elapsed-time limit. Native token/usage budgets,
    explicit operator pause/cancel and genuine errors retain their own statuses.
    """
    if len(objective) > CODEX_GOAL_MAX_CHARS:
        raise ValueError(
            f"native Goal objective exceeds Codex's {CODEX_GOAL_MAX_CHARS}-character limit: {len(objective)}"
        )
    from openai_codex._goal import _GoalNotificationStream  # installed pinned SDK 0.155.1

    client = thread._client
    state, _logical_turn_id = client.start_goal_operation(thread.id, objective)
    stream = _GoalNotificationStream(
        state=state,
        next_notification=lambda: client.next_goal_notification(state),
        unregister=lambda: client.unregister_goal_operation(state),
        cancel_goal=lambda: client.cancel_goal_operation(state),
    )
    turns = 0
    tools = 0
    try:
        for event in stream:
            method = str(getattr(event, "method", ""))
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
        client.cancel_goal_operation(state)
        raise
    finally:
        stream.close()
    status = state.status.value if state.status is not None else "unknown"
    return NativeGoalResult(status=status, thread_id=thread.id,
                            physical_turns=turns, tools_started=tools)


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
