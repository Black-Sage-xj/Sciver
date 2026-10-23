from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BasicAuthCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class ObservatoryAuth:
    """One Observatory. Every account has the same interface and operator controls."""

    accounts: tuple[BasicAuthCredentials, ...]

    def __post_init__(self) -> None:
        if not self.accounts:
            raise ValueError("Observatory auth requires at least one account")
        names = [item.username for item in self.accounts]
        if len(names) != len(set(names)):
            raise ValueError("Observatory auth usernames must be unique")


AuthRealm = BasicAuthCredentials | ObservatoryAuth


SESSION_COOKIE = "tm_observer_session"
SESSION_TTL_S = 7 * 24 * 60 * 60


def default_auth_file() -> Path:
    return Path("~/.secrets.d/tm_observer_public.json").expanduser()


def _account_from_mapping(payload: dict, *, path: Path) -> BasicAuthCredentials:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        raise ValueError(f"invalid TM Observatory auth file: {path}")
    return BasicAuthCredentials(username, password)


def load_auth_file(path: Path) -> AuthRealm:
    path = path.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    primary = _account_from_mapping(payload, path=path)
    raw_viewers = payload.get("viewers") or []
    if raw_viewers is None:
        raw_viewers = []
    if not isinstance(raw_viewers, list):
        raise ValueError(f"invalid TM Observatory viewers: {path}")
    viewers = tuple(_account_from_mapping(item, path=path) for item in raw_viewers)
    if not viewers:
        return primary
    return ObservatoryAuth((primary, *viewers))


def auth_accounts(credentials: AuthRealm) -> tuple[BasicAuthCredentials, ...]:
    if isinstance(credentials, ObservatoryAuth):
        return credentials.accounts
    return (credentials,)


def _password_matches(stored: str, supplied: str) -> bool:
    if stored.startswith("$sciver-pbkdf2-sha256$"):
        try:
            _, _, rounds, salt, expected = stored.split("$")
            count = int(rounds)
            if not 100000 <= count <= 1000000 or len(salt) != 32 or len(expected) != 64:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", supplied.encode(), bytes.fromhex(salt), count).hex()
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(supplied.encode(), stored.encode())


def match_account(credentials: AuthRealm, username: str, password: str) -> BasicAuthCredentials | None:
    for account in auth_accounts(credentials):
        if hmac.compare_digest(username.encode(), account.username.encode()) and _password_matches(account.password, password):
            return account
    return None


def ensure_auth_file(path: Path | None = None, *, username: str = "tmobserver") -> Path:
    path = (path or default_auth_file()).expanduser().resolve()
    if path.is_file():
        load_auth_file(path)
        os.chmod(path, 0o600)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    password = secrets.token_urlsafe(32)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"username": username, "password": password}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def authorize_header(header: str | None, credentials: AuthRealm) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    encoded = header[6:].strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    username, password = decoded.split(":", 1)
    return match_account(credentials, username, password) is not None


def _session_key(credentials: BasicAuthCredentials) -> bytes:
    return hashlib.sha256(credentials.password.encode("utf-8")).digest()


def issue_session_token(
    credentials: BasicAuthCredentials,
    *,
    now: float | None = None,
    ttl_s: int = SESSION_TTL_S,
) -> str:
    expires = int((time.time() if now is None else now) + int(ttl_s))
    message = f"{credentials.username}:{expires}".encode("utf-8")
    signature = hmac.new(_session_key(credentials), message, hashlib.sha256).hexdigest()
    raw = f"{expires}:{signature}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_session_token(
    token: str | None,
    credentials: AuthRealm,
    *,
    now: float | None = None,
) -> bool:
    return any(
        _verify_session_token_for_account(token, account, now=now)
        for account in auth_accounts(credentials)
    )


def _verify_session_token_for_account(
    token: str | None,
    credentials: BasicAuthCredentials,
    *,
    now: float | None = None,
) -> bool:
    if not token:
        return False
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        expires_text, signature = raw.split(":", 1)
        expires = int(expires_text)
    except (ValueError, UnicodeDecodeError):
        return False
    current = int(time.time() if now is None else now)
    if expires < current:
        return False
    message = f"{credentials.username}:{expires}".encode("utf-8")
    expected = hmac.new(_session_key(credentials), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def session_from_cookie(header: str | None, credentials: AuthRealm) -> bool:
    if not header:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(header)
    except Exception:
        return False
    morsel = cookie.get(SESSION_COOKIE)
    return morsel is not None and verify_session_token(morsel.value, credentials)
