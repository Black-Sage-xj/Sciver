"""Deterministic, scope-preserving display contract for scientific Hypotheses."""
from __future__ import annotations

import re

TITLE_LIMIT = 40
CLAIM_LIMIT = 80
CONDITIONS_LIMIT = 80
SUMMARY_LIMIT = 240

_PENDING_STATUS = "待检验"
_SUPPORTED_STATUS = "已支持（仅限冻结范围）"
_REJECTED_STATUS = "已反驳（仅限冻结范围）"
_UNRESOLVED_STATUS = "未定"
_UNADJUDICATED_STATUS = "未裁决"
_UNAVAILABLE_PREFIX = "摘要暂不可用；完整命题与适用条件见档案。状态："
_LONG_FORM_PREFIX = "完整命题（原文）："

_SUMMARY_RE = re.compile(
    r"^主张：(?P<claim>.+)；条件：(?P<conditions>.+)；状态：(?P<status>[^。]+)。$"
)
_UNAVAILABLE_RE = re.compile(
    r"^摘要暂不可用；完整命题与适用条件见档案。状态：(?P<status>[^。]+)。$"
)
_LONG_FORM_RE = re.compile(
    r"^完整命题（原文）：(?P<hypothesis>.+)；状态：(?P<status>[^。]+)。$",
    re.DOTALL,
)
_GENERIC_TITLES = {"hypothesis", "假说", "科学假说", "研究假说", "摘要暂不可用"}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def compose_hypothesis_summary(claim: object, conditions: object, *, status: str = _PENDING_STATUS) -> str:
    """Compose the only accepted new-H summary from separately checked semantics."""
    claim_text, condition_text = _clean(claim), _clean(conditions)
    if not claim_text or len(claim_text) > CLAIM_LIMIT:
        raise ValueError(f"canvas_claim must be nonempty and at most {CLAIM_LIMIT} characters")
    if not condition_text or len(condition_text) > CONDITIONS_LIMIT:
        raise ValueError(f"canvas_conditions must be nonempty and at most {CONDITIONS_LIMIT} characters")
    if status not in {
        _PENDING_STATUS, _SUPPORTED_STATUS, _REJECTED_STATUS,
        _UNRESOLVED_STATUS, _UNADJUDICATED_STATUS,
    }:
        raise ValueError("unsupported scientific display status")
    summary = f"主张：{claim_text}；条件：{condition_text}；状态：{status}。"
    if len(summary) > SUMMARY_LIMIT:
        raise ValueError(f"composed hypothesis summary exceeds {SUMMARY_LIMIT} characters")
    return summary


def is_compliant_hypothesis_summary(value: object) -> bool:
    """Recognize compact summaries and explicit long-form frozen-text fallbacks."""
    raw = str(value or "").strip()
    long_form = _LONG_FORM_RE.fullmatch(raw)
    if long_form:
        return bool(long_form.group("hypothesis").strip() and long_form.group("status") in {
            _PENDING_STATUS, _SUPPORTED_STATUS, _REJECTED_STATUS,
            _UNRESOLVED_STATUS, _UNADJUDICATED_STATUS,
        })
    summary = _clean(raw)
    if not summary or len(summary) > SUMMARY_LIMIT:
        return False
    match = _SUMMARY_RE.fullmatch(summary)
    if match:
        claim, conditions = match.group("claim"), match.group("conditions")
        return (
            0 < len(claim) <= CLAIM_LIMIT
            and 0 < len(conditions) <= CONDITIONS_LIMIT
            and match.group("status") in {
                _PENDING_STATUS, _SUPPORTED_STATUS, _REJECTED_STATUS,
                _UNRESOLVED_STATUS, _UNADJUDICATED_STATUS,
            }
        )
    return _UNAVAILABLE_RE.fullmatch(summary) is not None


def status_for_scientific_state(scientific_status: object, *, adjudicated: bool) -> str:
    if not adjudicated:
        return _PENDING_STATUS
    status = str(scientific_status or "").strip().lower()
    if status == "supported":
        return _SUPPORTED_STATUS
    if status == "rejected":
        return _REJECTED_STATUS
    if status == "open":
        return _UNRESOLVED_STATUS
    return _UNADJUDICATED_STATUS


def summary_with_current_status(
    value: object, scientific_status: object, *, adjudicated: bool,
) -> str:
    """Update only the SDK-owned epistemic label; never rewrite the scientific claim."""
    raw = str(value or "").strip()
    long_form = _LONG_FORM_RE.fullmatch(raw)
    if long_form and is_compliant_hypothesis_summary(raw):
        status = status_for_scientific_state(scientific_status, adjudicated=adjudicated)
        return f"{_LONG_FORM_PREFIX}{long_form.group('hypothesis')}；状态：{status}。"
    summary = _clean(raw)
    if not is_compliant_hypothesis_summary(summary):
        return summary
    status = status_for_scientific_state(scientific_status, adjudicated=adjudicated)
    if _UNAVAILABLE_RE.fullmatch(summary):
        return f"{_UNAVAILABLE_PREFIX}{status}。"
    long_form = _LONG_FORM_RE.fullmatch(summary)
    if long_form:
        return f"{_LONG_FORM_PREFIX}{long_form.group('hypothesis')}；状态：{status}。"
    match = _SUMMARY_RE.fullmatch(summary)
    if not match:
        return summary
    return f"主张：{match.group('claim')}；条件：{match.group('conditions')}；状态：{status}。"


def fallback_hypothesis_display(
    hypothesis_id: str, *, preferred_title: object = "", long_text: object = "",
    scientific_status: object = "open", adjudicated: bool = False,
) -> tuple[str, str]:
    """Use the frozen full hypothesis when compact display generation fails."""
    title = _clean(preferred_title)
    if not title or title.casefold() in _GENERIC_TITLES or title == hypothesis_id:
        title = hypothesis_id
    elif len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT].rstrip()
    status = status_for_scientific_state(scientific_status, adjudicated=adjudicated)
    full_text = str(long_text or "").strip()
    if full_text:
        summary = f"{_LONG_FORM_PREFIX}{full_text}；状态：{status}。"
    else:
        # Callers without access to a frozen proposition retain the legacy safe fallback.
        summary = f"{_UNAVAILABLE_PREFIX}{status}。"
    assert len(title) <= TITLE_LIMIT
    return title, summary


def normalize_hypothesis_display(
    hypothesis_id: str, *, title: object, summary: object,
    fallback_title: object = "", scientific_status: object = "open",
    adjudicated: bool = False,
) -> tuple[str, str]:
    """Accept only a bounded title plus the explicit three-part summary contract."""
    clean_title = _clean(title)
    raw_summary = str(summary or "").strip()
    clean_summary = raw_summary if _LONG_FORM_RE.fullmatch(raw_summary) else _clean(raw_summary)
    if (
        clean_title and len(clean_title) <= TITLE_LIMIT
        and clean_title.casefold() not in _GENERIC_TITLES
        and is_compliant_hypothesis_summary(clean_summary)
    ):
        return clean_title, summary_with_current_status(
            clean_summary, scientific_status, adjudicated=adjudicated,
        )
    return fallback_hypothesis_display(
        hypothesis_id, preferred_title=clean_title or fallback_title,
        scientific_status=scientific_status, adjudicated=adjudicated,
    )
