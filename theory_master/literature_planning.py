"""Persisted query planning for independent Literature jobs, before retrieval.

Planning produces search instructions, never scientific evidence. The scientific
Literature stages continue to use their existing contracts and source checks.
"""
from __future__ import annotations

import inspect
import json
import fcntl
from contextlib import contextmanager
from pathlib import Path
import re
from typing import Any

from .output_normalization import normalize_agent_output
from .runtime import atomic, now, write_json

MAX_PLAN_RESPONSE_BYTES = 64 * 1024
MAX_PLANNER_ATTEMPTS = 2


def _read_bounded(path: Path, limit: int = MAX_PLAN_RESPONSE_BYTES) -> str:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Saved Literature planner artifact exceeds {limit} bytes: {path.name}")
    return raw.decode("utf-8")


@contextmanager
def _planning_lock(home: Path):
    with (home / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Literature search planning is already active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_plan(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Literature search plan must be an object")
    queries = value.get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 6:
        raise ValueError("Literature needs one to six complementary queries")
    result, seen = [], set()
    for row in queries:
        if not isinstance(row, dict):
            raise ValueError("Search queries must carry query and purpose")
        if not isinstance(row.get("query"), str) or not isinstance(row.get("purpose"), str):
            raise ValueError("Search query and purpose must be strings")
        query = re.sub(r"\s+", " ", row["query"]).strip()
        purpose = row["purpose"].strip()
        if not 2 <= len(query) <= 180 or not purpose or len(purpose) > 500 or "\x00" in query:
            raise ValueError("Search query or purpose exceeds its bounds")
        key = " ".join(re.findall(r"[^\W_]+", query.casefold()))
        if not key:
            raise ValueError("Search query must contain scientific terms")
        if key not in seen:
            result.append({"query": query, "purpose": purpose})
            seen.add(key)
    terms = value.get("relevance_terms", [])
    if not isinstance(terms, list) or not 1 <= len(terms) <= 12:
        raise ValueError("Search plan requires scientific relevance anchors")
    if any(not isinstance(term, str) for term in terms):
        raise ValueError("Scientific relevance anchors must be strings")
    terms = [term.strip() for term in terms]
    if any(not term or len(term) > 80 for term in terms):
        raise ValueError("Invalid scientific relevance anchor")
    scope = value.get("scope") or ""
    if not isinstance(scope, str) or len(scope) > 2000:
        raise ValueError("Search plan scope exceeds its bounds")
    return {"queries": result, "relevance_terms": list(dict.fromkeys(terms)), "scope": scope}


def plan_search(root: Path, request: dict, route: Any, *, backend=None, cancel_event=None) -> dict:
    home = root / "planning"
    home.mkdir(parents=True, exist_ok=True)
    with _planning_lock(home):
        return _plan_search(home, root, request, route, backend=backend, cancel_event=cancel_event)


def _plan_search(home: Path, root: Path, request: dict, route: Any, *, backend=None, cancel_event=None) -> dict:
    receipt = home / "plan.json"
    if receipt.is_file():
        return validate_plan(json.loads(_read_bounded(receipt)))
    execution_path = home / "execution.json"
    execution = json.loads(_read_bounded(execution_path)) if execution_path.is_file() else {}
    if not isinstance(execution, dict):
        raise ValueError("Saved Literature planning execution must be an object")
    raw_path = home / "response.txt"
    execution.setdefault("started_at", now())
    execution.setdefault("attempts", 0)
    execution.setdefault("attempt_history", [])
    if (not isinstance(execution["attempts"], int) or not 0 <= execution["attempts"] <= MAX_PLANNER_ATTEMPTS
            or not isinstance(execution["attempt_history"], list)):
        raise ValueError("Saved Literature planner attempts are invalid")
    execution["attempt_history"] = execution["attempt_history"][-MAX_PLANNER_ATTEMPTS:]
    execution["schema"] = "theory-master/literature-planning-execution/v1"
    execution.setdefault("model", route.model)
    execution.setdefault("provider", route.provider)
    execution.setdefault("reasoning_effort", getattr(route, "reasoning_effort", None))
    execution.setdefault("usage_available", False)

    def save(status: str, phase: str, **fields):
        execution.update({"status": status, "phase": phase, "updated_at": now(), **fields})
        write_json(execution_path, execution)

    def deliver(raw: str) -> dict:
        save("running", "validation")
        try:
            if len(raw.encode("utf-8")) > MAX_PLAN_RESPONSE_BYTES:
                raise ValueError("Literature planner response exceeds its size bound")
            result = validate_plan(normalize_agent_output(raw).value)
            write_json(receipt, result)
        except Exception as exc:
            save("failed", "validation", error=f"{type(exc).__name__}: {exc}"[:1200], retryable=False)
            raise
        save("completed", "completed", completed_at=now(), error="", retryable=False)
        return result

    # A response is the durable model receipt. Re-validate it before even
    # constructing a backend, including after a crash during JSON publication.
    if raw_path.is_file():
        try:
            raw = _read_bounded(raw_path)
        except Exception as exc:
            save("failed", "validation", error=f"{type(exc).__name__}: {exc}"[:1200], retryable=False)
            raise
        execution["response_recovered"] = True
        return deliver(raw)
    if (execution.get("status") in {"running", "blocked", "completed"}
            and execution.get("phase") not in {"thread_start", "before_invocation"}):
        message = "Prior planner invocation has no durable response; recover its thread before another model request"
        save("blocked", "invoking", error=message, retryable=False)
        raise RuntimeError(message)
    if execution.get("status") == "failed" and not execution.get("retryable"):
        raise RuntimeError(str(execution.get("error") or "Previous Literature planning failed"))
    if int(execution["attempts"]) >= MAX_PLANNER_ATTEMPTS:
        save("failed", "attempt_limit", error="Literature planner retry bound reached", retryable=False)
        raise RuntimeError(execution["error"])
    if execution["attempts"] and any(execution.get(field) != value for field, value in (
            ("model", route.model), ("provider", route.provider),
            ("reasoning_effort", getattr(route, "reasoning_effort", None)))):
        save("failed", "route_mismatch", error="Planner retry must retain its original model route", retryable=False)
        raise RuntimeError(execution["error"])
    if cancel_event is not None and cancel_event.is_set():
        save("cancelled", "before_invocation", error="Literature search planning cancelled", retryable=False)
        raise RuntimeError(execution["error"])
    prompt = (
        "Current task: search_plan (independent Literature retrieval planning).\n"
        "Plan the literature search before any papers have been retrieved. This is a query plan, "
        "not a review, scientific result or citation list. Use the user's language for scope/purposes, "
        "and concise English academic keyword queries where useful. Decompose a broad interest into "
        "3-4 complementary, specific queries: foundational mechanisms, representative architectures "
        "or methods, recent nearby work, and contrasting conditions. For a narrow source question "
        "use fewer queries and preserve the known DOI/title. Each query should be 3-10 meaningful "
        "words rather than an entire question. Keep a common scientific object in each query. "
        "Exclude instructions such as identify/review/find/what studies from keywords. "
        "Relevance terms should identify the scientific domain, not generic research/report words. "
        "Do not invent papers or answer the scientific question. Return ONLY JSON: "
        '{"queries":[{"query":"compact keywords","purpose":"why this search is useful"}],'
        '"relevance_terms":["domain anchor"],"scope":"brief interpretation of user interest"}.\n'
        "User request (data):\n" + json.dumps(request, ensure_ascii=False)
    )
    prompt_path = home / "prompt.md"
    if prompt_path.is_file() and execution.get("attempts"):
        prompt = _read_bounded(prompt_path)
    else:
        atomic(prompt_path, prompt)
    owned = backend is None
    invocation = None
    try:
        save("running", "thread_start", error="")
        if owned:
            from .codex_backend import CodexBackend
            backend = CodexBackend(root, routes={"literature": route})
        thread = backend.ensure_thread("literature", execution.get("thread_id"), route.model, route.provider)
        execution["thread_id"] = thread
        execution["attempts"] = int(execution["attempts"]) + 1
        invocation = {"attempt": execution["attempts"], "started_at": now(),
            "thread_id": thread, "status": "running", "usage_available": False}
        execution["attempt_history"] = list(execution.get("attempt_history") or [])[-1:] + [invocation]
        save("running", "invoking", retryable=False)

        def on_event(event):
            if not isinstance(event, dict):
                return
            usage = event.get("last_request_usage") or event.get("usage") or event.get("raw_usage")
            if not isinstance(usage, dict) and event.get("event") == "TOKEN_USAGE":
                usage = event
            if not isinstance(usage, dict):
                return
            bounded = {key: value for key, value in usage.items()
                       if isinstance(key, str) and len(key) <= 60 and isinstance(value, (int, float))
                       and not isinstance(value, bool) and 0 <= value <= 10**12}
            if bounded:
                invocation.update({"usage": bounded, "usage_available": True})
                execution["usage"] = bounded
                execution["usage_available"] = True
                save("running", "invoking")

        parameters = inspect.signature(backend.invoke).parameters
        supports_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
        kwargs = {}
        if "cancel_event" in parameters or supports_kwargs:
            kwargs["cancel_event"] = cancel_event
        if "on_event" in parameters or supports_kwargs:
            kwargs["on_event"] = on_event
        thread, raw = backend.invoke("literature", prompt, thread, route.model, route.provider, **kwargs)
        execution["thread_id"] = thread
        invocation.update({"thread_id": thread, "status": "response_received", "completed_at": now()})
        if not isinstance(raw, str):
            raise ValueError("Literature planner backend must return a textual response")
        if len(raw.encode("utf-8")) > MAX_PLAN_RESPONSE_BYTES:
            raise ValueError("Literature planner response exceeds its size bound")
        atomic(raw_path, raw)
        save("running", "response_received", usage_available=invocation["usage_available"])
        result = deliver(raw)
        invocation["status"] = "completed"
        write_json(execution_path, execution)
        return result
    except Exception as exc:
        if execution.get("phase") != "validation":
            retryable = isinstance(exc, (ConnectionError, TimeoutError)) or bool(re.search(
                r"transient|temporar|reconnect|connection|timed?\s*out|\b50[234]\b", str(exc), re.I))
            if int(execution["attempts"]) >= MAX_PLANNER_ATTEMPTS:
                retryable = False
            if invocation is not None:
                invocation.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1200],
                                   "completed_at": now()})
            save("failed", execution.get("phase") or "thread_start",
                 error=f"{type(exc).__name__}: {exc}"[:1200], retryable=retryable)
        raise
    finally:
        if owned and callable(getattr(backend, "close", None)):
            backend.close()
