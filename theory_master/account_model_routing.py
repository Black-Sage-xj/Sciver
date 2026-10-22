"""Codex account-specific verified model ceiling for the tier-2 role profile.

The app-server model/list advertises 6.1 for both accounts, but account-2
returned HTTP 400 for gpt-6.1-sol. Treat account-specific observed rejection
as authoritative until the user explicitly updates this policy.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .backend_config import ACCOUNT_PROVIDERS, RoleRoute


# account 1: 6.1 Sol confirmed by a fresh running Research Lead turn.
# account 2: 6.1 Sol rejected (Cycle 88), 6 Sol is the conservative fallback.
ACCOUNT_SOL_CEILINGS = {"1": "gpt-6.1-sol", "2": "gpt-6-sol"}


def routes_for_account(
    preferred: Mapping[str, RoleRoute], account: str | None
) -> dict[str, RoleRoute]:
    """Preserve role, provider and effort; lower only an unsupported Sol model."""
    routes = dict(preferred)
    if account != "2":
        return routes
    for role, route in routes.items():
        if route.provider in ACCOUNT_PROVIDERS and route.model == "gpt-6.1-sol":
            routes[role] = replace(route, model=ACCOUNT_SOL_CEILINGS["2"])
    return routes
