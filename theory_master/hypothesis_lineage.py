"""Explicit branch parent and supplementary scientific provenance."""
from __future__ import annotations

from typing import Any, Mapping

from .research_transition import TRANSITION_LIMIT


def normalize_singleton_references(
    output: dict[str, Any], known_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Wrap one unambiguous existing H ID; never split, strip or guess IDs."""
    candidates = output.get("candidates")
    if not isinstance(candidates, list):
        return output, []
    replacements = list(candidates)
    repairs = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        source = candidate.get("lineage_other_node_ids")
        main = candidate.get("lineage_main_node_id")
        if (isinstance(source, str) and source in known_ids and source.strip() == source
                and isinstance(main, str) and main in known_ids and main != source):
            replacements[index] = {**candidate, "lineage_other_node_ids": [source]}
            repairs.append({"kind": "wrap_singleton_lineage_reference",
                            "path": f"candidates[{index}].lineage_other_node_ids", "source_id": source})
    return ({**output, "candidates": replacements} if repairs else output), repairs


def lineage_repair_snapshot(output: Mapping[str, Any], known_ids: set[str]) -> list[dict[str, Any]]:
    """Protect valid declared references even if another candidate field is invalid."""
    protected = []
    candidates = output.get("candidates")
    for index, candidate in enumerate(candidates if isinstance(candidates, list) else []):
        if not isinstance(candidate, Mapping):
            continue
        roles = {}
        main = candidate.get("lineage_main_node_id")
        if isinstance(main, str) and main in known_ids:
            roles[main] = "main"
        others = candidate.get("lineage_other_node_ids", [])
        if isinstance(others, list):
            for source in others:
                if isinstance(source, str) and source in known_ids and source != main:
                    roles[source] = "other"
        if not roles:
            continue
        try:
            explicit = explicit_lineage(candidate)
            exact = explicit is not None and all(source in known_ids for source in [explicit[0], *explicit[1]])
        except ValueError:
            exact = False
        # An over-capacity declaration is not a valid decision to freeze in
        # full: preserving every other source makes every legal repair fail.
        # Preserve the main and the declared choice pool, leaving the Agent
        # (never the SDK) to choose a legal subset and remove its unused notes.
        choices = []
        if (isinstance(main, str) and main in known_ids and main.strip() == main
                and isinstance(others, list) and len(others) > 2
                and all(isinstance(source, str) and source in known_ids
                        and source.strip() == source and source != main for source in others)
                and len(set(others)) == len(others)):
            choices = list(others)
        contribution_sources = set(roles)
        if choices:
            roles = {main: "main"}
        transition = candidate.get("transition")
        notes = transition.get("source_contributions") if isinstance(transition, Mapping) else None
        protected.append({"index": index, "hypothesis": candidate.get("hypothesis"),
                          "roles": roles, "exact": exact,
                          **({"repairable_other_sources": choices} if choices else {}),
                          "contributions": {key: val for key, val in (notes.items() if isinstance(notes, Mapping) else [])
                                            if key in contribution_sources and isinstance(val, str) and val.strip()
                                            and len(val) <= TRANSITION_LIMIT}})
    return protected


def assert_repair_lineage_preserved(protected: list[dict[str, Any]], repaired: Mapping[str, Any]) -> None:
    """Formatting repair cannot silently delete, reorder or reassign source decisions."""
    candidates = repaired.get("candidates")
    for original in protected:
        index = original["index"]
        if (not isinstance(candidates, list) or index >= len(candidates)
                or not isinstance(candidates[index], Mapping)):
            raise ValueError(f"candidates[{index}] repair removed a candidate with protected lineage")
        candidate = candidates[index]
        if (isinstance(original["hypothesis"], str) and original["hypothesis"].strip()
                and candidate.get("hypothesis") != original["hypothesis"]):
            raise ValueError(f"candidates[{index}] repair changed/reordered the protected scientific source subject")
        choices = original.get("repairable_other_sources") or []
        known = set(original["roles"]) | set(choices)
        current = lineage_repair_snapshot({"candidates": [candidate]}, known)
        roles = current[0]["roles"] if current else {}
        if any(roles.get(source) != role for source, role in original["roles"].items()):
            raise ValueError(f"candidates[{index}] repair must preserve valid lineage IDs and main/other roles: {original['roles']}")
        if original["exact"]:
            explicit = explicit_lineage(candidate)
            if explicit is None or set([explicit[0], *explicit[1]]) != set(original["roles"]):
                raise ValueError(f"candidates[{index}] contract repair cannot change the declared lineage source set")
        if choices:
            explicit = explicit_lineage(candidate)
            if explicit is None or not set(explicit[1]).issubset(choices):
                raise ValueError(f"candidates[{index}] repair must select other sources from the original declared pool")
            roles = {explicit[0]: "main", **{source: "other" for source in explicit[1]}}
        transition = candidate.get("transition")
        notes = transition.get("source_contributions") if isinstance(transition, Mapping) else {}
        retained = {key: val for key, val in original["contributions"].items()
                    if not choices or key in roles}
        if (retained and (not isinstance(notes, Mapping)
                or any(notes.get(key) != val for key, val in retained.items()))) or (
                    choices and isinstance(notes, Mapping)
                    and any(source in notes for source in set(choices) - set(roles))):
            raise ValueError(f"candidates[{index}] repair must preserve valid source contribution explanations")


def explicit_lineage(candidate: Mapping[str, Any]) -> tuple[str, list[str]] | None:
    """Validate the new contract; absence preserves historical unranked sources."""
    if not {"lineage_main_node_id", "lineage_other_node_ids"} & candidate.keys():
        return None
    main = candidate.get("lineage_main_node_id")
    others = candidate.get("lineage_other_node_ids", [])
    if not isinstance(main, str) or not main or main.strip() != main:
        raise ValueError("lineage_main_node_id requires one canonical nonempty H ID")
    if (not isinstance(others, list) or len(others) > 2
            or any(not isinstance(x, str) or not x or x.strip() != x for x in others)
            or len(set(others)) != len(others) or main in others):
        raise ValueError("lineage_other_node_ids requires 0..2 unique H IDs distinct from main")
    return main, others


def lineage_sources(candidate: Mapping[str, Any]) -> list[str]:
    explicit = explicit_lineage(candidate)
    if explicit is not None:
        return [explicit[0], *explicit[1]]
    legacy = candidate.get("lineage_anchor_node_ids")
    if isinstance(legacy, list) and legacy:
        return list(legacy)
    return [candidate["lineage_anchor_node_id"]] if candidate.get("lineage_anchor_node_id") else []


def source_role_text(node: Mapping[str, Any]) -> str:
    """Shared detail copy for the Canvas and live SQLite observer projection."""
    main = node.get("lineage_main_node_id")
    if not main:
        return ""
    text = ("\n\n### 前序来源\n\n- Main（分支连线）：" + str(main)
            + "\n- 其它（仅追溯）：" + ("、".join(node.get("lineage_other_node_ids") or []) or "（无）"))
    record = node.get("research_transition") or {}
    for source in record.get("sources") or []:
        if source.get("contribution"):
            text += "\n- " + source["hypothesis_id"] + " 的贡献：" + source["contribution"]
    return text
