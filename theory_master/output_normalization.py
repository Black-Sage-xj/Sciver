"""Deterministic normalization for schema-bound Agent JSON outputs.

The model owns scientific scalar content. The SDK owns JSON structure. This
module repairs only syntax that can be recovered without inventing or changing
scientific values, then returns a canonical object plus an auditable repair
trace. Semantic/contract violations remain the responsibility of the normal
RoleContract validator and its bounded repair path.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


FENCE = chr(96) * 3


@dataclass(frozen=True)
class NormalizationResult:
    value: dict[str, Any]
    canonical_json: str
    classification: str
    repairs: tuple[dict[str, Any], ...]
    original_error: str | None = None


class OutputNormalizationError(ValueError):
    def __init__(self, message: str, *, classification: str, original_error: str | None = None):
        super().__init__(message)
        self.classification = classification
        self.original_error = original_error or message


def _repair_json_string_escapes(text: str) -> str:
    valid = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if escaped:
            output.append(char)
            escaped = False
            index += 1
            continue
        if char == '"':
            output.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if next_char in valid:
                output.append(char)
                escaped = True
            else:
                output.append("\\\\")
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _scan_state(text: str) -> tuple[bool, list[str]]:
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "}]":
            if stack and char == stack[-1]:
                stack.pop()
    return in_string, stack


def _remove_trailing_commas(text: str) -> tuple[str, int]:
    output: list[str] = []
    in_string = False
    escaped = False
    removed = 0
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            look = index + 1
            while look < len(text) and text[look].isspace():
                look += 1
            if look < len(text) and text[look] in "}]":
                removed += 1
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output), removed


def _repair_delimiters(text: str) -> tuple[str, list[dict[str, Any]], bool]:
    output: list[str] = []
    stack: list[str] = []
    repairs: list[dict[str, Any]] = []
    in_string = False
    escaped = False
    pairs = {"{": "}", "[": "]"}
    for offset, char in enumerate(text):
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            output.append(char)
            continue
        if char in pairs:
            stack.append(pairs[char])
            output.append(char)
            continue
        if char in "}]":
            if stack and char == stack[-1]:
                stack.pop()
                output.append(char)
            else:
                repairs.append({"kind": "remove_impossible_closing_delimiter", "offset": offset, "token": char})
            continue
        output.append(char)
    if in_string:
        return text, [], True
    if stack:
        missing = "".join(reversed(stack))
        output.extend(missing)
        repairs.append({"kind": "append_missing_closing_delimiters", "tokens": missing})
    return "".join(output), repairs, False


def _schema_top_keys(schema: Mapping[str, Any] | None) -> set[str]:
    return {str(key) for key in schema} if isinstance(schema, Mapping) else set()


def _decode_object_with_tail(payload: str, schema: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(payload)
    if not isinstance(value, dict):
        raise ValueError("role JSON must be object")
    tail = payload[end:].strip()
    if not tail:
        return value, []
    if tail.startswith(FENCE):
        return value, [{"kind": "strip_suffix_code_fence", "chars": len(tail)}]
    if tail.startswith(","):
        extension_payload = "{" + tail[1:].lstrip()
        extension, extension_end = decoder.raw_decode(extension_payload)
        if not isinstance(extension, dict) or extension_payload[extension_end:].strip():
            raise json.JSONDecodeError("invalid top-level continuation", payload, end)
        overlap = set(value) & set(extension)
        if overlap:
            raise ValueError("role JSON continuation repeats top-level fields: " + ", ".join(sorted(overlap)))
        expected = _schema_top_keys(schema)
        if expected and not set(extension).issubset(expected):
            unknown = sorted(set(extension) - expected)
            raise ValueError("role JSON continuation contains fields outside current contract: " + ", ".join(unknown))
        return {**value, **extension}, [{
            "kind": "merge_premature_top_level_close",
            "offset": end,
            "fields": sorted(extension),
        }]
    if "{" not in tail and "[" not in tail:
        return value, [{"kind": "strip_suffix_prose", "chars": len(tail)}]
    raise json.JSONDecodeError("extra data after JSON object", payload, end)


def _parser_directed_edit(text: str, error: json.JSONDecodeError) -> tuple[str, dict[str, Any] | None]:
    pos = max(0, min(len(text), int(error.pos)))
    message = str(error.msg or "")
    if "Expecting ',' delimiter" in message:
        return text[:pos] + "," + text[pos:], {"kind": "insert_missing_comma", "offset": pos}
    if "Expecting ':' delimiter" in message:
        return text[:pos] + ":" + text[pos:], {"kind": "insert_missing_colon", "offset": pos}
    if pos < len(text) and text[pos] in ",}]":
        token = text[pos]
        return text[:pos] + text[pos + 1:], {
            "kind": "remove_unexpected_structural_token",
            "offset": pos,
            "token": token,
        }
    return text, None


def _looks_like_content_loss(text: str, error: Exception) -> bool:
    in_string, _stack = _scan_state(text)
    if in_string:
        return True
    if isinstance(error, json.JSONDecodeError):
        message = str(error.msg or "")
        if "Unterminated string" in message:
            return True
        tail = text.rstrip()
        if int(error.pos) >= max(0, len(text) - 1):
            if tail.endswith(":") or tail.endswith(",") or re.search(r"\b(?:tru|fals|nul|[-+]?\d+[eE][+-]?)$", tail):
                return True
    return False


def normalize_agent_output(raw_text: str, schema: Mapping[str, Any] | None = None) -> NormalizationResult:
    """Normalize one schema-bound Agent response without inventing field values."""
    raw = str(raw_text or "")
    start = raw.find("{")
    if start < 0:
        raise OutputNormalizationError(
            "role did not return a JSON object",
            classification="content_loss",
        )
    repairs: list[dict[str, Any]] = []
    if raw[:start].strip():
        repairs.append({"kind": "strip_prefix_prose", "chars": start})
    working = raw[start:].strip()
    if working.endswith(FENCE):
        working = working[:-len(FENCE)].rstrip()
        repairs.append({"kind": "strip_suffix_code_fence", "chars": len(FENCE)})

    original_error: str | None = None
    last_error: Exception | None = None
    attempted_escape_repair = False
    attempted_trailing_commas = False
    attempted_delimiters = False
    parser_edit_count = 0

    for _ in range(8):
        try:
            value, tail_repairs = _decode_object_with_tail(working, schema)
            repairs.extend(tail_repairs)
            canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
            return NormalizationResult(
                value=value,
                canonical_json=canonical,
                classification="syntax_damage" if repairs else "valid",
                repairs=tuple(repairs),
                original_error=original_error,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if original_error is None:
                original_error = str(exc)
            if isinstance(exc, ValueError) and "repeats top-level fields" in str(exc):
                raise OutputNormalizationError(
                    str(exc),
                    classification="syntax_damage",
                    original_error=original_error,
                ) from exc

        if not attempted_escape_repair:
            attempted_escape_repair = True
            escaped = _repair_json_string_escapes(working)
            if escaped != working:
                working = escaped
                repairs.append({"kind": "escape_invalid_backslash_in_string"})
                continue

        if not attempted_trailing_commas:
            attempted_trailing_commas = True
            trimmed, count = _remove_trailing_commas(working)
            if count:
                working = trimmed
                repairs.append({"kind": "remove_trailing_commas", "count": count})
                continue

        if not attempted_delimiters:
            attempted_delimiters = True
            balanced, delimiter_repairs, content_loss = _repair_delimiters(working)
            if content_loss:
                raise OutputNormalizationError(
                    str(last_error or "unterminated JSON string"),
                    classification="content_loss",
                    original_error=original_error,
                )
            if delimiter_repairs:
                working = balanced
                repairs.extend(delimiter_repairs)
                continue

        if isinstance(last_error, json.JSONDecodeError) and parser_edit_count < 3:
            edited, repair = _parser_directed_edit(working, last_error)
            if repair is not None and edited != working:
                working = edited
                repairs.append(repair)
                parser_edit_count += 1
                continue

        classification = "content_loss" if _looks_like_content_loss(working, last_error or ValueError()) else "syntax_damage"
        raise OutputNormalizationError(
            str(last_error or "role JSON could not be normalized"),
            classification=classification,
            original_error=original_error,
        )

    raise OutputNormalizationError(
        str(last_error or "role JSON normalization budget exhausted"),
        classification="syntax_damage",
        original_error=original_error,
    )
