"""Last-good Codex quota history for low-frequency Run UI displays.

Only non-secret account labels/remaining percentages/timestamps are persisted.
Live Codex RPCs are limited to one background refresh per 5 minutes across
all Run browser tabs; failed reads preserve the last successful history.
Services' explicit live refresh can also contribute authentic observations.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .codex_usage import active_account, collect_codex_usage

PERIOD_S = 300
MAX_STORED_BYTES = 5000
ACCOUNTS = ('account-1', 'account-2')


def _percent(value: Any) -> int | float | None:
    if type(value) not in (int, float) or not 0 <= value <= 100:
        return None
    return value


def _valid_window(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    remaining = _percent(data.get('remaining_percent'))
    duration = data.get('window_duration_mins')
    duration = duration if type(duration) is int and 0 < duration <= 525600 else None
    return {'remaining_percent': remaining, 'window_duration_mins': duration} if remaining is not None else None


def _clean_account(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict) or data.get('account_home') not in ACCOUNTS:
        return None
    primary = _valid_window(data.get('primary'))
    secondary = _valid_window(data.get('secondary'))
    if ((data.get('primary') is not None and primary is None) or
            (data.get('secondary') is not None and secondary is None)):
        return None
    stamp = data.get('queried_at')
    if (data.get('ok') is not True or (primary is None and secondary is None)
            or type(stamp) is not int or stamp <= 0 or stamp > time.time()+300):
        return None
    return {'account_home': data['account_home'], 'ok': True,
            'primary': primary, 'secondary': secondary, 'queried_at': stamp}


def _clean_display_account(data: Any) -> dict[str, Any] | None:
    """Whitelist non-secret fields useful to the live Services card.

    These details remain memory-only. The persisted last-good record continues
    to contain only account identity, remaining percentages, and timestamps.
    """
    base = _clean_account(data)
    if base is None:
        return None

    def safe_reset(value: Any) -> int | float | None:
        if type(value) not in (int, float) or not 0 < value < 10**12:
            return None
        return value

    def safe_window(name: str) -> dict[str, Any] | None:
        raw = data.get(name)
        raw = raw if isinstance(raw, dict) else {}
        if base[name] is None:
            return None
        result = dict(base[name])
        result["resets_at"] = safe_reset(raw.get("resets_at"))
        return result

    label = data.get("label")
    plan_type = data.get("plan_type")
    allowed = data.get("ordinary_usage_allowed")
    reset_credits = data.get("reset_credits")
    count = reset_credits.get("available_count") if isinstance(reset_credits, dict) else None
    if type(count) is not int or count < 0:
        count = None
    return {
        **base,
        "label": label[:80] if isinstance(label, str) else (
            "Codex 1" if base["account_home"] == "account-1" else "Codex 2"),
        "plan_type": plan_type[:80] if isinstance(plan_type, str) else None,
        "ordinary_usage_allowed": allowed if type(allowed) is bool else None,
        "primary": safe_window("primary"),
        "secondary": safe_window("secondary"),
        "reset_credits": {"available_count": count},
    }


class QuotaHistory:
    def __init__(self, path: Path, *, provider: Callable[[], dict[str, Any]] = collect_codex_usage,
                 active: Callable[[], str | None] = active_account, period_s: float = PERIOD_S):
        self.path = Path(path)
        self.provider = provider
        self.active = active
        self.period_s = max(60.0, float(period_s))
        self._lock = threading.RLock()
        self._accounts: dict[str, dict[str, Any]] = {}
        # Rich, sanitized card fields are process-memory only; disk stays quota-only.
        self._display_details: dict[str, dict[str, Any]] = {}
        self._attempt_at = 0.0
        self._refreshing = False
        self._last_attempt_at = 0
        self._last_refresh_error: str | None = None
        try:
            if self.path.is_file() and not self.path.is_symlink() and self.path.stat().st_size <= MAX_STORED_BYTES:
                record = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(record, dict) and isinstance(record.get('accounts'), list):
                    for raw in record['accounts']:
                        entry = _clean_account(raw)
                        if entry is not None:
                            self._accounts[entry['account_home']] = entry
        except (OSError, ValueError, UnicodeError):
            pass
        if self._accounts:
            # Recent persisted sample should not force a new provider RPC on
            # every tm-observer restart. Older history triggers one background
            # refresh on the first Run page, while still being displayed.
            age = max(0, time.time() - max(a['queried_at'] for a in self._accounts.values()))
            if age < self.period_s:
                self._attempt_at = time.monotonic() - age

    def _persist(self) -> None:
        """Atomic, mode-0600 historical snapshot; no auth, credit or RPC data."""
        if self.path.is_symlink():
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.quota-history-', dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as writer:
                    json.dump({'schema': 'tm-quota-history/1',
                               'accounts': list(self._accounts.values())}, writer)
                    writer.write('\n')
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass  # Historical persistence is best-effort; memory stays valid.

    def observe(self, raw: dict[str, Any]) -> None:
        """Save each account when at least one provider window was really read."""
        if not isinstance(raw, dict) or not isinstance(raw.get('accounts'), list):
            return
        with self._lock:
            changed = False
            for source in raw['accounts']:
                value = _clean_account(source)
                if value is None:
                    continue
                key = value['account_home']
                previous = self._accounts.get(key)
                if previous is None or value['queried_at'] >= previous['queried_at']:
                    self._accounts[key] = value
                    display = _clean_display_account(source)
                    if display is not None:
                        self._display_details[key] = display
                    changed = True
            if changed:
                # A manual Services refresh counts as a recent real read too:
                # do not immediately launch a duplicate Run-provider refresh.
                self._attempt_at = time.monotonic()
                self._last_attempt_at = int(time.time())
                self._last_refresh_error = None
                self._persist()

    def _refresh(self) -> None:
        try:
            raw = self.provider()
            accounts = raw.get('accounts') if isinstance(raw, dict) else None
            has_valid_account = isinstance(accounts, list) and any(
                _clean_account(account) is not None for account in accounts
            )
            if has_valid_account:
                self.observe(raw)
                with self._lock:
                    self._last_refresh_error = None
            else:
                # Provider failures may arrive as {ok:false}, not exceptions.
                # Expose only a safe code, never raw RPC errors or credentials.
                with self._lock:
                    self._last_refresh_error = 'provider_query_failed'
        except Exception:
            with self._lock:
                self._last_refresh_error = 'provider_refresh_exception'
        finally:
            with self._lock:
                self._last_attempt_at = int(time.time())
                self._refreshing = False

    def snapshot(self, *, request_update: bool = False) -> dict[str, Any]:
        # Active account is read from the *current selector*, never persisted.
        # This protects against a cross-tab / Services account change.
        active = self.active()
        with self._lock:
            if (request_update and not self._refreshing
                    and time.monotonic() - self._attempt_at >= self.period_s):
                self._refreshing = True
                self._attempt_at = time.monotonic()
                threading.Thread(target=self._refresh, daemon=True,
                                 name='tm-observer-quota-history-refresh').start()
            values = [dict(self._display_details.get(k) or self._accounts[k])
                      for k in ACCOUNTS if k in self._accounts]
            account = self._accounts.get(active or '')
            age = max(0, int(time.time() - account['queried_at'])) if account else None
            return {'active_account': active, 'accounts': values,
                    'refreshing': self._refreshing, 'cache_age_s': age,
                    'last_attempt_at': self._last_attempt_at,
                    'last_refresh_error': self._last_refresh_error,
                    'historical': True, 'minimum_refresh_seconds': int(self.period_s)}
