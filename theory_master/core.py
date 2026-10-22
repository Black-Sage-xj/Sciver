"""Backward-compatible public facade for TheoryMaster.

Historically the package implementation lived in this module. Keep all public
names importable here while the implementation is split into focused modules.
"""

from .workflow import *  # noqa: F401,F403
from .backends import json_object  # noqa: F401  # historical public facade export
