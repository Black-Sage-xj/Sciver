"""Check an explicitly required method grid against numeric Plan anchors.

This is intentionally narrow. It checks a stated interval against numbers the
operator put in the same Plan; it does not infer a transition or judge science.
"""

from __future__ import annotations

import re
from typing import Mapping, Any


_ANCHOR = re.compile(r"W[_ ]?c\s*\(\s*([xyz])\s*\)\s*[=≈~]\s*(\d+(?:\.\d+)?)", re.I)
_GRID = re.compile(
    r"\bW\s*=\s*(\d+(?:\.\d+)?)\s*"
    r"(?:,\s*\d+(?:\.\d+)?\s*)?"
    r",?\s*(?:…|\.{2,}|—|–|-|to|through|至|到)\s*,?\s*"
    r"(\d+(?:\.\d+)?)",
    re.I,
)


def require_explicit_plan_grid(candidate: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Guard a Plan that explicitly requires a W grid spanning both reference boundaries.

    The first x/z pair in Required Context is the selected model's reference;
    later pairs may describe other models solely to warn against mixing them.
    The reviewer checks the requirement when the interval is not machine-readable.
    """
    contract = str(context.get("research_contract") or "")
    if not (("拟议 W 网格须" in contract and "覆盖两条边界" in contract)
            or re.search(r"proposed\s+W\s+grid\s+must\s+cover\s+both", contract, re.I)):
        return
    anchors: dict[str, float] = {}
    for match in _ANCHOR.finditer(str(context.get("required_context") or "")):
        anchors.setdefault(match.group(1).lower(), float(match.group(2)))
        if "x" in anchors and "z" in anchors:
            break
    if "x" not in anchors or "z" not in anchors:
        return  # No machine-readable reference pair; leave source assessment to reviewers.
    test = str(candidate.get("decisive_test") or "")
    grid = _GRID.search(test)
    if grid is None:
        # The task prescribes coverage, not the notation used to express a grid.
        return
    lower, upper = (float(grid.group(1)), float(grid.group(2)))
    if lower > upper or any(not lower <= value <= upper for value in anchors.values()):
        raise ValueError(
            f"Proposed W={lower:g}…{upper:g} misses the selected-model Plan anchors "
            f"Wc(x)={anchors['x']:g}, Wc(z)={anchors['z']:g}. "
            "Check the proposed interval against the references specified in this Research Contract."
        )
