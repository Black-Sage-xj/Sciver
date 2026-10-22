from __future__ import annotations

"""Authoritative TheoryMaster role metadata.

Keep role identity, capability membership, display names, and role feature flags
in one dependency-light module so workflow, skills, observability, and contracts
cannot silently drift.
"""

ROLES = (
    "research_lead",
    "challenger",
    "investigator",
    "literature",
    "judge",
    "reporter",
)

ALL_KNOWN_ROLES = frozenset(ROLES)

INVESTIGATION_EXECUTORS = frozenset({"investigator", "literature"})
# Compatibility alias for older internal imports. New code should prefer the
# executor name so "capability" is not confused with an Investigator skill.
CAPABILITIES = INVESTIGATION_EXECUTORS
# Operator-facing Incident Supervisor may repair engineering code; automatic reviewers remain read-only.
WRITE_ROLES = frozenset((*INVESTIGATION_EXECUTORS, "incident_supervisor"))
BUDGET_ROLES = frozenset()
PATH_ANCHOR_ROLES = frozenset({"research_lead", "challenger", "judge"})

ROLE_DISPLAY_NAMES: dict[str, str] = {
    "research_lead": "Research Lead",
    "challenger": "Challenger",
    "investigator": "Investigator",
    "literature": "Literature",
    "judge": "Judge",
    "reporter": "Reporter",
}


def role_display_name(role: str) -> str:
    return ROLE_DISPLAY_NAMES.get(role, role.replace("_", " ").title())
