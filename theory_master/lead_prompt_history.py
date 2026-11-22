"""Deliver changes against the last confirmed stage input on a Lead thread."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .invocations import stable_hash
from .runtime import write_json


def context_changes(old: Any, new: Any, path: tuple = ()) -> list[dict]:
    """Ordered edits to a JSON tree. Array indices are zero based."""
    if type(old) is type(new) and old == new:
        return []
    edits = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() - new.keys()):
            edits.append({"remove": [*path, key]})
        for key, value in new.items():
            if key in old:
                edits.extend(context_changes(old[key], value, (*path, key)))
            else:
                edits.append({"set": [*path, key], "value": value})
        return edits
    if isinstance(old, list) and isinstance(new, list):
        # Histories and the SQLite H index mostly append. Avoid resending
        # previously observed rows, including when an existing row changes.
        for index, (a, b) in enumerate(zip(old, new)):
            edits.extend(context_changes(a, b, (*path, index)))
        if len(new) > len(old):
            edits.append({"append": list(path), "values": new[len(old):]})
        elif len(new) < len(old):
            edits.append({"truncate": list(path), "length": len(new)})
        return edits
    return [{"set": list(path), "value": new}]


def prepare_lead_prompt(session, full_prompt: str) -> str:
    """Snapshot the allowed stage view and invalidate its receipt before dispatch."""
    cycle = session.owner.cycle_dir
    stage = session.stage
    thread = session.identifier
    history = session.owner.spec.research_root / ".theory-master" / "lead-context"
    receipt = history / stable_hash(thread) / (stage + ".json")
    previous = {}
    if session.continuation and receipt.exists():
        try:
            previous = json.loads(receipt.read_text())
        except (OSError, ValueError):
            pass
    if session.continuation:
        full_prompt = f"[Cycle {cycle.name}] " + full_prompt
    instructions, marker, _ = full_prompt.rpartition("\nContext:\n")
    if not marker:
        raise ValueError("Lead input has no stage context")
    # Initial and continuation wrappers differ. Hash the stage definition and
    # projected schema directly, including per-invocation closed-choice rules.
    from .stage_registry import stage_definition_text
    context = session.prompt_context
    rules = stable_hash([stage_definition_text("research_lead", stage, context),
                         session.contract.ref, session.contract.prompt_projection(context)])
    full_context = dict(context)
    if "agent_graph_reuse" in full_context and session.agent_graph is not None:
        full_context.pop("agent_graph_reuse")
        full_context["agent_graph"] = session.agent_graph
    # Use the same JSON types (e.g. array rather than tuple) as the receiver.
    full_context = json.loads(json.dumps(full_context, ensure_ascii=False))
    record = {"cycle": cycle.name, "stage": stage, "thread": thread, "rules": rules,
              "context": full_context, "invocation": session.invocation_id}
    snapshot = cycle / "lead_context" / (session.invocation_id + ".json")
    write_json(snapshot, {"instructions": instructions, **record})
    session.lead_prompt_receipt = receipt, record
    write_json(receipt, {"pending": session.invocation_id})
    session.lead_prompt_metrics = {"lead_context_mode": "full",
                                   "context_chars": len(json.dumps(full_context, ensure_ascii=False))}
    if (not isinstance(previous, dict) or previous.get("rules") != rules
            or previous.get("thread") != thread or previous.get("stage") != stage
            or not isinstance(previous.get("context"), dict)):
        # Replaced/recovered threads may have no safe baseline for graph_reuse.
        if full_context != context:
            return instructions + marker + json.dumps(full_context, ensure_ascii=False)
        return full_prompt
    additions = {key: value for key, value in full_context.items() if key not in previous["context"]}
    existing = {key: value for key, value in full_context.items() if key not in additions}
    updates = {"since_cycle": previous["cycle"], "since_invocation": previous["invocation"],
               "current_cycle": cycle.name, "stage_snapshot": str(snapshot),
               "changes": context_changes(previous["context"], existing)}
    if session.guidance_view is not None:
        updates["new_operator_guidance"] = session.guidance_view
    payload = json.dumps({"lead_stage_update": updates, **additions}, ensure_ascii=False)
    session.lead_prompt_metrics = {"lead_context_mode": "delta", "since_invocation": previous["invocation"],
                                   "context_chars": len(payload), "contract_chars": 0}
    return (f"[Cycle {cycle.name}] Continue as TheoryMaster research_lead.\n"
            f"Current task: {stage} — use its previously delivered procedure and output contract "
            f"{session.contract.ref}. Return only the required JSON.\n"
            "Apply the ordered changes below to the last confirmed input for this same stage. "
            "Paths contain object keys or zero-based array indices. Other top-level fields in Context are newly added. Omitted fields retain their value. "
            "Use the current stage's allowed sources and workspace. If compaction removed needed details, "
            "read stage_snapshot for the current authoritative input and instructions.\n"
            "Context:\n" + payload)


def confirm_lead_prompt(session) -> None:
    receipt, record = session.lead_prompt_receipt
    if session.identifier == record["thread"]:
        write_json(receipt, record)
