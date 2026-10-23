"""Optional local login; credentials are private and scoped to a workspace."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .auth import BasicAuthCredentials, match_account


class AccessSettingsError(ValueError):
    pass


def is_local_host(host: str) -> bool:
    try:
        name = urlsplit('http://' + host).hostname or ''
        return name == 'localhost' or ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def settings_path(state_dir: Path, workspace: Path) -> Path:
    ident = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return state_dir / f'web-access-{ident}.json'


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000).hex()
    return f'$sciver-pbkdf2-sha256$310000${salt}${digest}'


class AccessSettings:
    def __init__(self, state_dir: Path, workspace: Path, explicit_auth=None):
        self.path = settings_path(state_dir, workspace)
        self.lock = threading.RLock()
        self.externally_managed = explicit_auth is not None
        self._config = {'enabled': False, 'username': ''}
        if not self.externally_managed and self.path.exists():
            if self.path.is_symlink():
                raise AccessSettingsError('登录配置不能是符号链接')
            self._config = json.loads(self.path.read_text())
            if not isinstance(self._config, dict) or not isinstance(self._config.get('enabled'), bool):
                raise AccessSettingsError('登录配置格式无效，请使用 sciver web --reset-login 恢复')
            if self._config['enabled'] and not (isinstance(self._config.get('username'), str)
                and self._config['username'] and isinstance(self._config.get('password_hash'), str)
                and self._config['password_hash'].startswith('$sciver-pbkdf2-sha256$')):
                raise AccessSettingsError('已启用的登录配置缺少凭据，请使用 sciver web --reset-login 恢复')
        self.current = explicit_auth if self.externally_managed else self._credentials()

    def _credentials(self):
        return (BasicAuthCredentials(self._config['username'], self._config['password_hash'])
                if self._config['enabled'] else None)

    def public(self) -> dict:
        return {'enabled': self.current is not None, 'username': self._config.get('username', ''),
                'externally_managed': self.externally_managed}

    def save(self, payload: dict) -> dict:
        with self.lock:
            if self.externally_managed:
                raise AccessSettingsError('此服务由启动时的认证文件管理；请修改该文件并重启服务')
            if not isinstance(payload.get('enabled'), bool) or set(payload) - {'enabled', 'username', 'password', 'current_password'}:
                raise AccessSettingsError('登录设置格式无效')
            if self.current and not match_account(self.current, self._config['username'], str(payload.get('current_password') or '')):
                raise AccessSettingsError('当前密码不正确，设置未修改')
            config = dict(self._config)
            username = payload.get('username', config.get('username', ''))
            password = payload.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str):
                raise AccessSettingsError('用户名和密码必须是文本')
            username = username.strip()
            if payload['enabled']:
                if not username or len(username) > 80 or ':' in username or any(ord(c) < 32 for c in username):
                    raise AccessSettingsError('用户名须为 1–80 个字符，不能包含冒号或控制字符')
                if password:
                    if not 8 <= len(password) <= 1024:
                        raise AccessSettingsError('密码须为 8–1024 个字符')
                    config['password_hash'] = hash_password(password)
                elif not config.get('password_hash') or username != config.get('username'):
                    raise AccessSettingsError('请设置新密码')
            config.update(enabled=payload['enabled'], username=username)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.parent / ('.' + self.path.name + '.' + secrets.token_hex(8))
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as handle:
                    json.dump(config, handle, ensure_ascii=False)
                    handle.write('\n')
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self._config = config
            self.current = self._credentials()
            return self.public()


def reset_local_login(state_dir: Path, workspace: Path) -> None:
    path = settings_path(state_dir, workspace)
    if path.is_symlink():
        raise AccessSettingsError('登录配置不能是符号链接')
    path.unlink(missing_ok=True)
