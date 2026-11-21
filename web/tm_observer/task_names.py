"""Short operator-facing task identities, independent of scientific Goal text."""
from __future__ import annotations

import re
from typing import Any


MAX_TASK_NAME_LENGTH = 48


def _normalized(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def validate_task_name(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("名称不能包含换行或控制字符")
    name = _normalized(value)
    if len(name) > MAX_TASK_NAME_LENGTH or (not name and not allow_empty):
        raise ValueError(f"名称须为 1–{MAX_TASK_NAME_LENGTH} 个字符；完整研究问题请放在研究计划中")
    return name


def _short_goal(goal: str) -> str:
    first = next((line.strip(" #\t") for line in goal.splitlines() if line.strip()), "")
    first = _normalized(first)
    if len(first) <= MAX_TASK_NAME_LENGTH:
        return first
    # Only take a complete short sentence. A prefix cut from a long research
    # question is not a task title, even if it happens to fit on one line.
    sentence = re.split(r"(?<=[。！？!?])|(?<=\.)\s+", first, maxsplit=1)[0].strip()
    return sentence if len(sentence) <= MAX_TASK_NAME_LENGTH else ""


def task_name(value: Any = "", *, task_id: Any = None, goal: str = "") -> str:
    """Keep an explicit short name; safely migrate legacy paragraph names."""
    name = _normalized(value)
    if name and len(name) <= MAX_TASK_NAME_LENGTH:
        return name
    short_goal = _short_goal(goal)
    if short_goal:
        return short_goal
    ident = _normalized(task_id)
    return "研究任务" + (" " + ident if ident and len(ident) <= 16 else "")
