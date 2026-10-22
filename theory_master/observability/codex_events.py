"""Duck-typed Codex Notification → TheoryMasterEvent. No generated SDK types."""

from __future__ import annotations

from typing import Any

from theory_master.observability.costs import USAGE_KEYS

NOISY_METHODS = {
    "item/agentMessage/delta",
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    "item/reasoning/textDelta",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/summaryPartAdded",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/plan/delta",
    "item/mcpToolCall/progress",
}


def is_transient_reconnect_error(message: Any) -> bool:
    """Return whether a Codex error is an in-turn reconnect notification.

    The Codex SDK can emit ``error``/``thread/error`` notifications while it is
    still retrying the active turn.  These are progress events, not terminal
    turn failures.  Keep this classifier deliberately narrow so real provider
    errors still fail the workflow.
    """
    text = str(message or "").strip().lower()
    return any(marker in text for marker in (
        "reconnecting",
        "reconnect attempt",
        "connection reset; retrying",
        "temporarily unavailable; retrying",
    ))


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        if obj is None:
            return None
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
            continue
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return None


def _norm_type(value: Any) -> str:
    return str(value or "").replace("_", "").replace("-", "").lower()


def item_root(payload: Any) -> Any:
    item = _get(payload, "item")
    if item is None:
        # Unknown SDK notifications retain the decoded wire payload under
        # ``params``.  Some Codex HTTPS/app-server versions send the completed
        # Responses item shape on ``item/completed`` even though the installed
        # SDK schema expects a ThreadItem, so validation falls back to this
        # wrapper instead of exposing ``payload.item`` directly.
        item = _get(_get(payload, "params"), "item")
    if item is None:
        return None
    root = _get(item, "root")
    if root is None:
        root = item
    # Some app-server/SDK versions expose a timeline entry here rather than
    # the thread item itself: ``root.item.root``.  Unwrap that transport
    # envelope so the same extractor handles both notification shapes.
    nested = _get(root, "item")
    if nested is not None:
        root = _get(nested, "root") or nested
    return root


def item_type(payload: Any) -> str:
    root = item_root(payload)
    typ = _get(root, "type")
    typ = getattr(typ, "value", typ)
    return str(typ or "")


def turn_id_of(payload: Any) -> str | None:
    value = _get(payload, "turn_id", "turnId")
    if value:
        return str(value)
    turn = _get(payload, "turn")
    ident = _get(turn, "id")
    return str(ident) if ident else None


def extract_command(payload: Any) -> str | None:
    root = item_root(payload)
    command = _get(root, "command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if command:
        return str(command)
    return None


def extract_path(payload: Any) -> str | None:
    root = item_root(payload)
    path = _get(root, "path", "file_path", "filePath")
    if path:
        return str(path)
    changes = _get(root, "changes")
    if isinstance(changes, list) and changes:
        first = changes[0]
        nested = _get(first, "path", "file_path", "filePath")
        if nested:
            return str(nested)
    return None


def extract_usage(payload: Any) -> dict[str, int] | None:
    usage = _get(payload, "usage")
    if usage is None:
        usage = _get(_get(payload, "turn"), "usage")
    if usage is None:
        token_usage = _get(payload, "token_usage", "tokenUsage")
        usage = _get(token_usage, "total") or _get(token_usage, "last")
    if usage is None:
        return None
    tokens_in = _get(usage, "input_tokens", "inputTokens")
    tokens_out = _get(usage, "output_tokens", "outputTokens")
    tokens_total = _get(usage, "total_tokens", "totalTokens")
    if tokens_total is None and tokens_in is not None and tokens_out is not None:
        tokens_total = int(tokens_in) + int(tokens_out)
    if tokens_in is None and tokens_out is None and tokens_total is None:
        return None
    result: dict[str, int] = {}
    if tokens_in is not None:
        result["tokens_in"] = int(tokens_in)
    if tokens_out is not None:
        result["tokens_out"] = int(tokens_out)
    if tokens_total is not None:
        result["tokens_total"] = int(tokens_total)
    return result or None


def extract_raw_usage(payload: Any) -> dict[str, int] | None:
    """Keep provider usage structure needed for later billing/adaptation."""
    usage = _get(payload, "usage") or _get(_get(payload, "turn"), "usage")
    if usage is None:
        token_usage = _get(payload, "token_usage", "tokenUsage")
        # thread/tokenUsage is cumulative for the thread. Preserve that total;
        # the Collector converts it to a per-turn delta against the previous
        # completed cumulative snapshot.
        usage = _get(token_usage, "total") or _get(token_usage, "last")
    if usage is None:
        return None
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"), "output_tokens": ("output_tokens", "outputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens", "cache_read_input_tokens"),
        "cache_write_tokens": ("cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens", "cacheWriteInputTokens"),
        "reasoning_tokens": ("reasoning_tokens", "reasoningTokens", "reasoning_output_tokens", "reasoningOutputTokens"),
    }
    result = {key: int(value) for key, names in aliases.items() if (value := _get(usage, *names)) is not None}
    result["provider_specific_usage"] = {}
    return result or None



def extract_last_raw_usage(payload: Any) -> dict[str, int] | None:
    """Return only the latest request usage from a thread/tokenUsage payload."""
    token_usage = _get(payload, "token_usage", "tokenUsage")
    usage = _get(token_usage, "last") if token_usage is not None else None
    if usage is None:
        return None
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"), "output_tokens": ("output_tokens", "outputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens", "cache_read_input_tokens"),
        "cache_write_tokens": ("cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens", "cacheWriteInputTokens"),
        "reasoning_tokens": ("reasoning_tokens", "reasoningTokens", "reasoning_output_tokens", "reasoningOutputTokens"),
    }
    result = {key: int(value) for key, names in aliases.items() if (value := _get(usage, *names)) is not None}
    result["provider_specific_usage"] = {}
    return result or None

def extract_context_stats(payload: Any) -> dict[str, int]:
    token_usage = _get(payload, "token_usage", "tokenUsage")
    if token_usage is None:
        return {}
    last = _get(token_usage, "last")
    result: dict[str, int] = {}
    context_tokens = _get(last, "input_tokens", "inputTokens")
    context_window = _get(token_usage, "model_context_window", "modelContextWindow")
    if context_tokens is not None:
        result["context_tokens"] = int(context_tokens)
    if context_window is not None:
        result["context_window"] = int(context_window)
    return result


def extract_native_cost(payload: Any) -> dict[str, Any] | None:
    """Duck-type common request-level provider billing payloads without assuming one SDK schema."""
    turn = _get(payload, "turn")
    candidate = _get(payload, "cost", "billing", "charge") or _get(turn, "cost", "billing", "charge")
    if candidate is None:
        return None
    if isinstance(candidate, (int, float)):
        return {"amount": float(candidate)}
    amount = _get(candidate, "amount", "total", "total_cost", "totalCost")
    if amount is None:
        return None
    currency = _get(candidate, "currency", "currency_code", "currencyCode")
    return {"amount": float(amount), "currency": str(currency) if currency else None}


def extract_agent_text(payload: Any) -> str | None:
    root = item_root(payload)
    # ``message`` is role-neutral in current Responses/app-server rollouts: the
    # same wire type is used for developer, user, and assistant messages. Never
    # treat input messages as an agent response. Dedicated ``agentMessage`` /
    # ``agent_message`` items remain safe even when they omit an explicit role.
    kind = _norm_type(_get(root, "type"))
    role = _norm_type(_get(root, "role"))
    if kind == "message" and role not in {"assistant", "agent"}:
        return None
    if role in {"user", "developer", "system"}:
        return None
    if kind not in {"agentmessage", "message", "assistant"}:
        # Some transports omit the item type. Only accept that fallback when
        # the role itself explicitly identifies an assistant/agent message.
        if role not in {"assistant", "agent"}:
            return None
        if _get(root, "text", "content") is None:
            return None
    text = _get(root, "text")
    if text:
        return str(text)
    # Response-shaped SDK items use ``agent_message`` with a content array,
    # while rollout/thread items use ``agentMessage`` with a direct ``text``
    # field.  Both shapes are emitted by current Codex transports.
    content = _get(root, "content")
    parts: list[str] = []
    for item in content if isinstance(content, list) else [content]:
        candidate = _get(item, "text")
        if candidate is None:
            # The official SDK models response content as a RootModel
            # (AgentMessageInputContent.root -> InputText...).  Accept that
            # shape as well as the plain dict/object form used by older
            # transports and test doubles.
            candidate = _get(_get(item, "root"), "text")
        if candidate:
            parts.append(str(candidate))
    return "".join(parts) or None


def extract_agent_delta(payload: Any) -> str | None:
    """Extract text from an ``item/agentMessage/delta`` notification.

    A few Codex transports can deliver the terminal turn notification without
    a corresponding ``item/completed`` event.  The deltas are still the
    complete assistant message in order, so the backend can use their joined
    text as a bounded final-response fallback.
    """
    delta = _get(payload, "delta")
    return str(delta) if delta else None


def extract_final_from_turn(payload: Any) -> str | None:
    turn = _get(payload, "turn")
    text = _get(turn, "final_response", "finalResponse")
    if text:
        return str(text)
    direct = extract_agent_text(payload)
    if direct:
        return direct
    # Recent SDK versions expose the completed turn's items on the terminal
    # notification instead of a synthetic `final_response` field.  The stream
    # normally delivers item/completed first, but transports can legally omit
    # those notifications while retaining the full terminal turn payload.
    items = _get(turn, "items", "output", "messages") or []
    # ``Turn.items`` is normally a list, but proxy/app-server versions have
    # exposed a view object (or a mapping containing the list) at this point.
    # Keep this normalization local so the final-response path remains
    # independent of a particular generated SDK model.
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or items.get("messages") or items
    elif not isinstance(items, (list, tuple)):
        for attr in ("items", "data", "messages"):
            candidate = getattr(items, attr, None)
            if candidate is not None:
                items = candidate
                break
    sequence = items if isinstance(items, (list, tuple)) else [items]
    for item in reversed(sequence):
        candidate = extract_agent_text({"item": item})
        if candidate:
            return candidate
    # Some HTTPS transports wrap the completed assistant message one level
    # below the turn instead of exposing it as a thread item.  These names are
    # deliberately narrow: do not mistake tool/reasoning output for the final
    # assistant response.
    for container_name in ("response", "result", "message"):
        container = _get(turn, container_name)
        candidate = extract_agent_text({"item": container})
        if candidate:
            return candidate
    return None


def extract_final_from_thread(payload: Any, turn_id: str | None = None) -> str | None:
    """Extract the latest assistant response from a thread-read payload.

    Some Codex transports publish ``turn/completed`` before exposing the
    completed agent message on the turn subscription.  The SDK persists that
    message in ``thread/read(includeTurns=true)``; this helper accepts both
    the SDK response object and simple test doubles.
    """
    thread = _get(payload, "thread") or payload
    turns = _get(thread, "turns") or []
    candidates = turns if isinstance(turns, list) else [turns]
    for turn in reversed(candidates):
        if turn_id is not None and _get(turn, "id") not in (None, turn_id):
            continue
        text = extract_final_from_turn({"turn": turn})
        if text:
            return text
    return None


def extract_error(payload: Any) -> str | None:
    error = _get(payload, "error", "message")
    if error is None:
        error = _get(_get(payload, "turn"), "error")
    if error is None:
        return None
    message = _get(error, "message") if not isinstance(error, str) else error
    if message is None and error is not None:
        message = str(error)
    return str(message) if message else None


def map_notification(event: Any, *, role: str | None = None, thread_id: str | None = None) -> dict[str, Any] | None:
    """Map visible model conversation plus compact tool activity into runtime events.

    Tool stdout/deltas and private reasoning remain excluded. Completed tool
    actions are retained only as one-line audit summaries so the conversation
    timeline stays readable.
    """
    method = str(getattr(event, "method", "") or "")
    if not method or method in NOISY_METHODS:
        return None
    payload = getattr(event, "payload", None)
    if payload is None and isinstance(event, dict):
        method = str(event.get("method") or "")
        payload = event.get("payload")
        if method in NOISY_METHODS:
            return None
    mapped: dict[str, Any] = {"method": method}
    if role:
        mapped["agent"] = role
        mapped["role"] = role
    if thread_id:
        mapped["thread_id"] = thread_id
    ident = turn_id_of(payload)
    if ident:
        mapped["turn_id"] = ident
    kind = _norm_type(item_type(payload))
    if method == "item/autoApprovalReview/completed":
        review = _get(payload, "review")
        rationale = str(_get(review, "rationale") or "")
        if rationale.lower().startswith("automatic approval review failed:"):
            mapped["event"] = "TOOL_APPROVAL_UNAVAILABLE"
            mapped["error"] = rationale[:2000]
            action = _get(_get(payload, "action"), "root") or _get(payload, "action")
            mapped["mcp_server"] = str(_get(action, "server") or "")
            mapped["mcp_tool"] = str(_get(action, "tool_name", "toolName") or "")
            return mapped
        return None
    if method == "turn/started":
        mapped["event"] = "LLM_TURN_STARTED"
        return mapped
    if method in {"turn/completed", "turn/failed"}:
        error = extract_error(payload)
        if method == "turn/failed" and is_transient_reconnect_error(error):
            mapped["event"] = "LLM_RECONNECTING"
        else:
            mapped["event"] = "LLM_TURN_COMPLETED" if method == "turn/completed" else "LLM_TURN_FAILED"
        usage = extract_usage(payload)
        if usage:
            mapped.update(usage)
        raw_usage = extract_raw_usage(payload)
        if raw_usage:
            mapped["raw_usage"] = raw_usage
        native_cost = extract_native_cost(payload)
        if native_cost:
            mapped["native_cost"] = native_cost
        if error:
            mapped["error"] = error
        status = _get(_get(payload, "turn"), "status")
        if status is not None:
            mapped["turn_status"] = getattr(status, "value", status)
        return mapped
    if method in {"error", "thread/error"}:
        error = extract_error(payload) or method
        lower = error.lower()
        if "compact" in lower:
            mapped["event"] = "COMPACTION_FAILED"
            mapped["episode_id"] = thread_id
        else:
            mapped["event"] = "LLM_RECONNECTING" if is_transient_reconnect_error(error) else "LLM_FAILED"
        mapped["error"] = error
        return mapped
    if method == "thread/tokenUsage/updated":
        mapped["event"] = "TOKEN_USAGE"
        usage = extract_usage(payload)
        if usage:
            mapped.update(usage)
        raw_usage = extract_raw_usage(payload)
        if raw_usage:
            mapped["raw_usage"] = raw_usage
        last_request_usage = extract_last_raw_usage(payload)
        if last_request_usage:
            mapped["last_request_usage"] = last_request_usage
        mapped.update(extract_context_stats(payload))
        return mapped
    if method == "thread/compacted":
        mapped["event"] = "COMPACTION_COMPLETED"
        mapped["episode_id"] = thread_id
        mapped["context_after"] = None
        mapped["compaction_usage"] = None
        mapped["compaction_cost"] = None
        return mapped
    if method == "item/started" and kind in {"contextcompaction", "compaction", "compactiontrigger"}:
        mapped["event"] = "COMPACTION_STARTED"
        mapped["episode_id"] = thread_id
        return mapped
    if method == "item/completed" and kind == "agentmessage":
        text = extract_agent_text(payload)
        if not text:
            return None
        mapped["event"] = "LLM_MESSAGE"
        mapped["message_role"] = "assistant"
        mapped["content"] = text
        return mapped
    if method == "item/completed" and kind == "commandexecution":
        mapped["event"] = "TOOL_ACTIVITY"
        mapped["tool"] = "command"
        mapped["detail"] = extract_command(payload) or "command completed"
        return mapped
    if method == "item/completed" and kind == "filechange":
        mapped["event"] = "TOOL_ACTIVITY"
        mapped["tool"] = "file"
        mapped["detail"] = extract_path(payload) or "file change completed"
        return mapped
    if method == "item/completed" and kind in {"mcptoolcall", "websearch", "toolcall"}:
        root = item_root(payload)
        name = _get(root, "tool", "name", "tool_name", "toolName", "query")
        error = str(_get(_get(root, "error"), "message") or "")
        if error.lower().startswith("automatic approval review failed:"):
            mapped["event"] = "TOOL_APPROVAL_UNAVAILABLE"
            mapped["error"] = error[:2000]
            mapped["mcp_server"] = str(_get(root, "server") or "")
            mapped["mcp_tool"] = str(name or "")
            return mapped
        mapped["event"] = "TOOL_ACTIVITY"
        mapped["tool"] = "web" if kind == "websearch" else "mcp" if kind == "mcptoolcall" else "tool"
        mapped["detail"] = str(name or f"{mapped['tool']} call completed")
        if kind == "mcptoolcall":
            server = _get(root, "server")
            arguments = _get(root, "arguments")
            mapped["mcp_server"] = str(server or "")
            mapped["mcp_tool"] = str(name or "")
            if isinstance(arguments, dict):
                mapped["arguments"] = arguments
        return mapped
    if method in {"item/started", "item/completed"}:
        # Reasoning and unrecognized item types stay out of the human transcript.
        return None
    return None
