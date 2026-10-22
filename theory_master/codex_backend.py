from __future__ import annotations

"""Official Codex SDK transport adapter."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

from .backend_config import ACCOUNT_PROVIDERS, DEFAULT_ROUTES, RoleRoute
from .execution_policy import PROCESS_CONTROL_GUARD, REPO_GUARD, make_approval_decision
from .role_registry import WRITE_ROLES
from .skills import developer_instructions, turn_input
from .provider_errors import ProviderQuotaExhausted, is_provider_quota_exhausted


def _codex_home() -> Path:
    """Return TheoryMaster's stable Codex runtime home.

    Account selection changes only the auth identity behind this runtime; ambient
    daily-Codex ``CODEX_HOME`` values are intentionally ignored.
    """
    isolated = (os.environ.get("THEORYMASTER_WORKER_CODEX_HOME") or "").strip()
    if isolated:
        path = Path(isolated).expanduser().resolve()
        if not (path / "config.toml").is_file():
            raise RuntimeError("pinned Worker Codex home not initialized")
        return path
    from .codex_accounts import ensure_layout

    return ensure_layout()["runtime"]


class ToolApprovalUnavailable(RuntimeError):
    """An automatic review service failed before an authorized tool could run."""


class CodexProviderRequestRejected(RuntimeError):
    """Provider explicitly rejected the model/request; reconnect cannot fix it."""


class CodexTurnCancelled(RuntimeError):
    """The caller explicitly stopped this interactive turn."""


def provider_request_rejection(raw_error: str | None) -> CodexProviderRequestRejected | None:
    """Preserve the original provider 400 instead of treating it as absent output.

    Only an explicit invalid_request_error / HTTP 400 is classified here.
    Stream EOFs and genuinely missing assistant replies remain recoverable.
    """
    if not raw_error:
        return None
    try:
        payload = json.loads(raw_error)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("error")
    if not isinstance(detail, dict):
        return None
    code = payload.get("status") or detail.get("status") or detail.get("code")
    category = str(detail.get("type") or "")
    if str(code) != "400" or category != "invalid_request_error":
        return None
    message = str(detail.get("message") or "Provider rejected the request")
    # Preserve the actionable model name and reason, without exposing arbitrary
    # provider metadata/headers or dumping potentially sensitive payloads.
    return CodexProviderRequestRejected(
        f"Codex provider HTTP 400 invalid_request_error: {message[:700]}"
    )


class CodexBackend:
    """Thin official-SDK adapter; imports lazily so offline use has no dependency."""
    def __init__(self, cwd: Path, routes: dict[str, RoleRoute] | None = None, *, task_config_path: Path | None = None):
        self.cwd = cwd
        # TheoryMaster owns one stable Codex runtime. Account switching changes
        # only runtime_home/auth.json via the account selector, never config/skills.
        active_codex_home = _codex_home()
        os.environ["CODEX_HOME"] = str(active_codex_home)
        # Stop native Codex project discovery at the cycle workspace so parent
        # AGENTS.md or repo-scoped skills cannot leak into TheoryMaster agents.
        marker = Path(self.cwd).expanduser().resolve() / ".theory-master-codex-root"
        marker.touch(exist_ok=True)
        self.routes = routes
        self.task_config_path = task_config_path.expanduser().resolve() if task_config_path is not None else None
        self.cycle_id: str | None = None
        self.client: Any | None = None
        self.client_account: str | None = None
        self.threads: dict[str, Any] = {}
        self.thread_accounts: dict[str, str] = {}
        # Track first-turn vs continuation for prompt/input compatibility. Role
        # Core and the skill whitelist live in thread developer instructions;
        # optional SKILL.md bodies are no longer eagerly attached on first turn.
        self._new_thread_ids: set[str] = set()
        self.collector: Any | None = None
        self._logger = logging.getLogger(__name__)
        self._turn_reads: list[str] | None = None
        self._network_checked = False
        # Bound reads to this Task, even though self.cwd later moves to a Cycle.
        self._quota_task_root = Path(cwd).expanduser().resolve()
        self._last_quota_probe_at: float | None = None

    def consume_turn_reads(self) -> list[str] | None:
        reads = self._turn_reads
        self._turn_reads = None
        return reads

    def _uses_account_network(self) -> bool:
        routes = self.routes or DEFAULT_ROUTES
        return any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())

    @staticmethod
    def _apply_network_result(result: dict[str, Any]) -> None:
        proxy = result.get("proxy")
        keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        if proxy:
            for key in keys:
                os.environ[key] = str(proxy)
            os.environ["CODEX_PROXY"] = str(proxy)
        else:
            for key in keys:
                os.environ.pop(key, None)
            os.environ.pop("CODEX_PROXY", None)

    def _run_network_guard(self, reason: str, *, required: bool) -> dict[str, Any] | None:
        if not self._uses_account_network() or os.environ.get("THEORYMASTER_NETWORK_CHECK", "1").strip().lower() in {"0", "false", "off", "no"}:
            return None
        cli = os.environ.get("CODEX_NET_CLI") or shutil.which("codex-net") or str(Path.home() / "bin" / "codex-net")
        try:
            proc = subprocess.run(
                [cli, "ensure", "--json", "--reason", reason],
                text=True, capture_output=True, timeout=float(os.environ.get("CODEX_NET_GUARD_TIMEOUT", "45")),
            )
            if proc.returncode != 0:
                message = (proc.stderr or proc.stdout or "codex-net failed").strip()
                if required:
                    raise RuntimeError(f"Codex network check failed before startup: {message}")
                self._logger.warning("codex-net could not repair route during %s: %s", reason, message)
                return None
            result = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
        except Exception as exc:
            if required:
                raise RuntimeError(f"Codex network check failed before startup: {exc}") from exc
            self._logger.warning("codex-net check failed during %s: %s", reason, exc)
            return None
        self._apply_network_result(result)
        self._network_checked = True
        self._logger.info(
            "codex-net %s: proxy=%s selector=%s selection=%s changed=%s",
            reason, result.get("proxy") or "DIRECT", result.get("selector"), result.get("selection"), result.get("changed"),
        )
        return result

    # The SDK already retries ordinary stream reconnects.  These are an outer
    # guard for a dead app-server/socket after the SDK gives control back.
    OUTER_TURN_RETRIES = 5
    OUTER_RETRY_DELAYS = (5, 10, 20, 40, 60)
    TURN_INACTIVITY_WATCHDOG_SECONDS = 300.0
    COMPLETED_RESPONSE_RECOVERY_ATTEMPTS = 5
    COMPLETED_RESPONSE_RECOVERY_DELAY_SECONDS = 0.2
    # Native Codex compaction is enabled for account-backed continuity threads.
    # 220k is ~85% of the currently advertised 258.4k GPT-6 context window; Codex
    # itself also clamps configured limits against the model's usable context.
    NATIVE_COMPACT_THRESHOLD_TOKENS = 220_000
    NATIVE_COMPACT_SCOPE = "total"
    PROXY_ENV_KEYS = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )

    @classmethod
    def _native_compaction_enabled(cls) -> bool:
        return os.environ.get("THEORYMASTER_NATIVE_COMPACTION", "1").strip().lower() not in {"0", "false", "off", "no"}

    @classmethod
    def _native_compact_threshold(cls) -> int:
        raw = os.environ.get("THEORYMASTER_COMPACT_THRESHOLD_TOKENS")
        if raw is None:
            return cls.NATIVE_COMPACT_THRESHOLD_TOKENS
        try:
            value = int(raw)
        except ValueError:
            return cls.NATIVE_COMPACT_THRESHOLD_TOKENS
        return max(16_000, value)

    @classmethod
    def _native_compact_scope(cls) -> str:
        scope = os.environ.get("THEORYMASTER_COMPACT_SCOPE", cls.NATIVE_COMPACT_SCOPE).strip().lower()
        return scope if scope in {"total", "body_after_prefix"} else cls.NATIVE_COMPACT_SCOPE

    def _thread_config(self, role: str, provider: str) -> dict[str, Any]:
        route = (self.routes or {}).get(role)
        if route is not None:
            effort = route.reasoning_effort
        else:
            # Supervisor profiles are intentionally outside the six scientific-role routes.
            # Pin their effort explicitly instead of relying on provider-specific fallback.
            from .supervisor_intervention import DEFAULT_REASONING_EFFORT, SUPERVISOR_ROLES
            effort = DEFAULT_REASONING_EFFORT if role in SUPERVISOR_ROLES else None
        config: dict[str, Any] = {}
        if provider in ACCOUNT_PROVIDERS:
            # Preserve the historical account-backed default when a route does
            # not explicitly pin reasoning effort.
            config["model_reasoning_effort"] = effort or "medium"
        elif effort:
            config["model_reasoning_effort"] = effort
        if provider in ACCOUNT_PROVIDERS and self._native_compaction_enabled():
            config["model_auto_compact_token_limit"] = self._native_compact_threshold()
            config["model_auto_compact_token_limit_scope"] = self._native_compact_scope()
        return config

    @classmethod
    def _watchdog_seconds(cls) -> float:
        raw = os.environ.get("THEORYMASTER_TURN_WATCHDOG_SECONDS")
        if raw is None:
            return cls.TURN_INACTIVITY_WATCHDOG_SECONDS
        try:
            value = float(raw)
        except ValueError:
            return cls.TURN_INACTIVITY_WATCHDOG_SECONDS
        return max(5.0, value)
    @staticmethod
    def _deepseek_catalog() -> Path:
        bundled = Path(__file__).resolve().parent / "deepseek_models.json"
        home = _codex_home() / "deepseek.models.json"
        return home if home.is_file() else bundled

    @staticmethod
    def _read_env_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        if not path.is_file():
            return values
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip("'\"")
        return values

    @staticmethod
    def _env_file_values() -> dict[str, str]:
        """Read credentials from an env file outside this repository.

        Default `~/.config/theory-master/env`, same shape as the project's `hpc-ssh/env`:
        `KEY=value` lines, `#` comments.  Real environment variables win over the file.
        """
        override = os.environ.get("THEORYMASTER_ENV_FILE")
        path = Path(override).expanduser() if override else Path.home() / ".config" / "theory-master" / "env"
        return CodexBackend._read_env_file(path)

    @staticmethod
    def _tls12_config_path() -> Path:
        path = Path.home() / ".config" / "theory-master" / "openssl-tls12.cnf"
        content = (
            "openssl_conf = default_conf\n"
            "[default_conf]\n"
            "ssl_conf = ssl_sect\n"
            "[ssl_sect]\n"
            "system_default = system_default_sect\n"
            "[system_default_sect]\n"
            "MinProtocol = TLSv1.2\n"
            "MaxProtocol = TLSv1.2\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _ensure_openai_https_provider_registered() -> Path:
        """Persist the HTTPS-only account provider so saved Codex threads can reopen.

        SDK config_overrides exist only for the creating process, while a persisted
        thread stores ``model_provider=openai_https``.  Native Codex/ChatGPT later
        reloads that thread from TheoryMaster's runtime ``config.toml``; without this durable,
        credential-free provider definition the conversation cannot be opened.
        """
        import fcntl

        path = _codex_home() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        header = "[model_providers.openai_https]"
        block = (
            "\n[model_providers.openai_https]\n"
            'name = "OpenAI HTTPS only"\n'
            'base_url = "https://chatgpt.com/backend-api/codex"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
            "supports_websockets = false\n"
        )
        with path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            text = handle.read()
            if header not in text:
                handle.seek(0, os.SEEK_END)
                if text and not text.endswith("\n"):
                    handle.write("\n")
                handle.write(block.lstrip("\n"))
                handle.flush()
                os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return path

    @staticmethod
    def _runtime_config(routes: dict[str, RoleRoute] | None = None) -> tuple[tuple[str, ...], dict[str, str]]:
        """Load provider metadata without persisting a credential in this project."""
        stored = CodexBackend._env_file_values()
        # Worker CODEX_HOME contains only its pinned Codex identity. Provider
        # metadata/credentials stay in the shared TM runtime home; never copy
        # bearer tokens into Worker homes or launcher argv.
        from .codex_accounts import runtime_home
        from .provider_extensions import optional_provider_overrides
        deepseek = (os.environ.get("THEORYMASTER_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
                    or stored.get("THEORYMASTER_DEEPSEEK_API_KEY") or stored.get("DEEPSEEK_API_KEY"))
        used = {route.provider for route in (routes or DEFAULT_ROUTES).values()}
        optional = used - ACCOUNT_PROVIDERS - {"DeepSeek"}
        overrides, environment = optional_provider_overrides(
            optional, stored, runtime_home=runtime_home()
        )
        if "DeepSeek" in used and deepseek:
            environment["DEEPSEEK_API_KEY"] = deepseek
        overrides.extend([
            'model_providers.DeepSeek.name="DeepSeek"',
            'model_providers.DeepSeek.base_url="https://api.deepseek.com/"',
            'model_providers.DeepSeek.wire_api="responses"',
            'model_providers.DeepSeek.env_key="DEEPSEEK_API_KEY"',
            "model_providers.DeepSeek.requires_openai_auth=false",
        ])
        if "CodexHTTPS" in used:
            overrides.extend([
                'model_providers.openai_https.name="OpenAI HTTPS only"',
                'model_providers.openai_https.base_url="https://chatgpt.com/backend-api/codex"',
                'model_providers.openai_https.wire_api="responses"',
                'model_providers.openai_https.requires_openai_auth=true',
                'model_providers.openai_https.supports_websockets=false',
            ])
            environment["OPENSSL_CONF"] = str(CodexBackend._tls12_config_path())
        if not (used & ACCOUNT_PROVIDERS):
            overrides[0:0] = ['preferred_auth_method="apikey"', 'forced_login_method="api"']
        # DeepSeek catalog hides GPT models. Inject it only for DeepSeek-only runs.
        if used <= {"DeepSeek"}:
            catalog = CodexBackend._deepseek_catalog()
            overrides.insert(2, f'model_catalog_json="{catalog}"')
        return tuple(overrides), environment

    @staticmethod
    def _compute_python() -> Path:
        """Return the interpreter exposed to numerical Agent shells.

        TheoryMaster calculations must not depend on whichever lightweight
        interpreter happened to start the desktop CLI. Operators may override
        the shared scientific environment explicitly; the default is the
        established Miniconda environment.
        """
        selected = os.environ.get("THEORYMASTER_COMPUTE_PYTHON", "").strip()
        path = Path(selected).expanduser() if selected else Path.home() / "miniconda3" / "bin" / "python"
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(
                "TheoryMaster compute interpreter is unavailable: "
                f"{path}. Set THEORYMASTER_COMPUTE_PYTHON to an executable scientific Python."
            )
        return path.resolve()

    @classmethod
    def _sdk_environment(cls, routes: dict[str, RoleRoute] | None = None) -> dict[str, str]:
        """Build the environment passed to Codex SDK/app-server.

        Preserve the caller's proxy settings for optional providers too.
        Direct access can be unavailable even when a configured proxy works.
        """
        _overrides, provider_env = cls._runtime_config(routes)
        compute_python = cls._compute_python()
        provider_env["THEORYMASTER_COMPUTE_PYTHON"] = str(compute_python)
        provider_env["PYTHON"] = str(compute_python)
        provider_env["PATH"] = f"{compute_python.parent}:{os.environ.get('PATH', '')}"
        return provider_env
    def reset_client_for_provider_change(self) -> None:
        """Drop provider-owned thread handles before a Cycle resumes on another provider."""
        old_client = getattr(self, "client", None)
        self.client = None
        self.client_account = None
        self.threads = getattr(self, "threads", {})
        self.thread_accounts = getattr(self, "thread_accounts", {})
        self._new_thread_ids = getattr(self, "_new_thread_ids", set())
        self.threads.clear()
        self.thread_accounts.clear()
        self._new_thread_ids.clear()
        if old_client is not None:
            try:
                old_client.close()
            except Exception:
                self._logger.warning("Old provider client close failed at safe route boundary", exc_info=True)

    def prepare_cycle_account(self, preferred_routes: dict[str, RoleRoute]) -> dict[str, RoleRoute]:
        """Choose an available Codex identity at a safe Cycle boundary."""
        from .account_model_routing import routes_for_account
        from .quota_auto_adjust import (
            quota_auto_adjust_enabled, proactive_switch_target, write_controller_state,
        )
        from .codex_accounts import (
            account_rate_limits, account_switch_lock, selected_account, use_account,
        )
        from .quota_provider_fallback import codex_quota_states

        self._cycle_provider_fallback = None
        if not any(route.provider in ACCOUNT_PROVIDERS for route in preferred_routes.values()):
            self._cycle_selected_account = None
            return dict(preferred_routes)

        pinned = (os.environ.get("THEORYMASTER_WORKER_PINNED_ACCOUNT") or "").strip()
        if pinned and (pinned not in {"1", "2"} or not os.environ.get("THEORYMASTER_WORKER_CODEX_HOME")):
            raise RuntimeError("invalid pinned Worker Codex identity")
        task_root = getattr(self, "_quota_task_root", None)
        auto_adjust = isinstance(task_root, Path) and quota_auto_adjust_enabled(task_root)
        selected = pinned or selected_account()
        target = None
        switch_reason = None
        snapshots: dict[str, dict] = {}

        if auto_adjust:
            try:
                # One fresh dual-account read per Cycle; no synthetic model turns.
                snapshots = {number: account_rate_limits(number) for number in ("1", "2")}
                if not pinned:
                    states = codex_quota_states(snapshots)
                    available = [number for number in ("1", "2") if states[number] is True]
                    if available and selected not in available:
                        with account_switch_lock():
                            actual = selected_account()
                            if actual in available:
                                selected = actual
                            else:
                                selected = available[0]
                                use_account(selected, restart_app=False, _lock_held=True)
                                target = selected
                                switch_reason = "codex_quota_available"
                    else:
                        candidate = proactive_switch_target(snapshots, selected)
                        if candidate:
                            with account_switch_lock():
                                actual = selected_account()
                                candidate = proactive_switch_target(snapshots, actual)
                                if candidate:
                                    use_account(candidate, restart_app=False, _lock_held=True)
                                    selected = candidate
                                    target = candidate
                                    switch_reason = "limiting_window_below_30_and_less_than_other"
                                else:
                                    selected = actual
                    write_controller_state(
                        task_root,
                        status="cycle_boundary_switched" if target else "cycle_boundary_checked",
                        selected_account=selected, selected_provider="Codex",
                        switch_reason=switch_reason,
                        accounts=snapshots, quota_fallback_for_resume=None,
                    )
                else:
                    write_controller_state(
                        task_root, status="cycle_boundary_checked", selected_account=pinned,
                        selected_provider="Codex", accounts=snapshots,
                        quota_fallback_for_resume=None,
                    )
            except Exception:
                self._logger.warning("Cycle-boundary Codex quota check failed; retaining configured route", exc_info=True)
                selected = pinned or selected_account()
                target = None
                try:
                    write_controller_state(
                        task_root, status="cycle_boundary_checked",
                        selected_account=selected, selected_provider="Codex",
                        accounts=snapshots, quota_fallback_for_resume=None,
                    )
                except Exception:
                    self._logger.warning("Failed to record Codex quota check status", exc_info=True)

        # A Worker remains pinned to its private identity; the Worker quota
        # watcher performs any identity migration after a paused safe checkpoint.
        effective_routes = routes_for_account(preferred_routes, selected)
        if (getattr(self, "client", None) is not None
                and getattr(self, "client_account", None) in {"1", "2"}
                and selected in {"1", "2"} and self.client_account != selected):
            self.reset_client_for_provider_change()
        self._cycle_selected_account = selected
        return effective_routes

    def _ensure_client(self) -> Any:
        try:
            from openai_codex import Codex, CodexConfig  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Codex SDK unavailable; install `.[sdk]`") from exc
        if self.client is None:
            self._run_network_guard("theorymaster-startup" if not self._network_checked else "theorymaster-client-rebuild", required=True)
            routes = self.routes or DEFAULT_ROUTES
            account_backed = any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())
            if any(route.provider == "CodexHTTPS" for route in routes.values()):
                self._ensure_openai_https_provider_registered()
            overrides, _provider_env = self._runtime_config(self.routes)
            environment = self._sdk_environment(self.routes)
            pinned = (os.environ.get("THEORYMASTER_WORKER_PINNED_ACCOUNT") or "").strip()
            if account_backed and pinned:
                # Private Worker CODEX_HOME permanently resolves to this identity;
                # global account selection is unrelated and must not be changed.
                self.client_account = pinned
                self.client = Codex(CodexConfig(config_overrides=overrides, env=environment, cwd=str(self.cwd)))
            elif account_backed:
                from .codex_accounts import account_switch_lock, selected_account
                # Freeze selector provenance and app-server creation under the
                # same host-wide lock used by account switching. This prevents
                # a second Task from rotating auth between those two operations.
                with account_switch_lock():
                    self.client_account = selected_account()
                    self.client = Codex(CodexConfig(config_overrides=overrides, env=environment, cwd=str(self.cwd)))
            else:
                self.client_account = None
                self.client = Codex(CodexConfig(config_overrides=overrides, env=environment, cwd=str(self.cwd)))
            inner = getattr(self.client, "_client", None)
            if inner is not None:
                # The cycle workspace plus the OS temp dir are the only writable roots; anything
                # else (repository history, gs_study task roots, 笔记区) needs a human.
                inner._approval_handler = make_approval_decision(
                    (Path(self.cwd).resolve(), Path(tempfile.gettempdir()).resolve())
                )
        return self.client

    @staticmethod
    def _verify_provider_runtime() -> None:
        """Fail before a managed Cycle when the lazy Codex dependency is unusable.

        Keeping these imports lazy remains useful for offline commands.  Managed
        execution, however, must discover a missing or incompatible SDK before
        it creates a Cycle and persists an active research state.
        """
        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox  # type: ignore
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "Codex provider runtime unavailable; install a compatible `.[sdk]` before starting TheoryMaster"
            ) from exc
        required = {
            "Codex": Codex,
            "CodexConfig": CodexConfig,
            "ApprovalMode.deny_all": getattr(ApprovalMode, "deny_all", None),
            "Sandbox.workspace_write": getattr(Sandbox, "workspace_write", None),
            "Sandbox.read_only": getattr(Sandbox, "read_only", None),
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise RuntimeError(
                "Codex provider runtime is incompatible; missing SDK symbols: " + ", ".join(missing)
            )

    def preflight_runtime(self) -> None:
        """Validate all configured Codex routes without making a model turn.

        A disposable workspace and threads let the app-server validate provider
        routes and connect each role's MCP Gateway.  This deliberately runs
        before Workflow allocates a real Cycle, so a lazy-import failure cannot
        leave an apparently started Cycle behind.
        """
        self._verify_provider_runtime()
        routes = self.routes or DEFAULT_ROUTES
        original_cwd, original_cycle_id, original_collector = self.cwd, self.cycle_id, self.collector
        prior_client = self.client
        if prior_client is not None:
            raise RuntimeError("Codex runtime preflight requires a fresh backend client")
        try:
            with tempfile.TemporaryDirectory(prefix="tm-runtime-preflight-") as root:
                workspace = Path(root) / "workspace"
                workspace.mkdir()
                self.cwd = workspace
                self.cycle_id = "runtime-preflight"
                # Do not emit synthetic thread records into the real Cycle's
                # collector; these threads exist only to validate startup.
                self.collector = None
                for role, route in routes.items():
                    try:
                        self.ensure_thread(role, None, route.model, route.provider)
                    except Exception as exc:
                        raise RuntimeError(
                            f"TheoryMaster runtime preflight failed for {role} route "
                            f"{route.provider}/{route.model}: {exc}"
                        ) from exc
        finally:
            client = self.client
            if client is not None:
                try:
                    client.close()
                except Exception:
                    self._logger.debug("failed to close disposable preflight Codex client", exc_info=True)
            self.client = None
            self.threads = {}
            self.client_account = None
            self._new_thread_ids.clear()
            self.thread_accounts.clear()
            self.cwd, self.cycle_id, self.collector = original_cwd, original_cycle_id, original_collector

    def _investigator_navigation_rule(self) -> str:
        """Expose a read anchor without widening the Cycle write sandbox."""
        write_root = Path(self.cwd).expanduser().resolve()
        read_root = (
            self.task_config_path.parent.expanduser().resolve()
            if self.task_config_path is not None
            else write_root
        )
        return (
            f"TheoryMaster Investigator navigation: Task scientific read root = {read_root}. "
            f"Current Cycle write root = {write_root}. "
            "How to use these roots is defined in the Investigator Role Core (本地导航)."
        )

    @staticmethod
    def _thread_sandbox(role: str):
        from openai_codex import Sandbox  # type: ignore
        return Sandbox.workspace_write if role in WRITE_ROLES else Sandbox.read_only

    def ensure_thread(self, role: str, thread_id: str | None, model: str, provider: str) -> str:
        # Keep the thread lifecycle testable with a minimal SDK facade.  Actual
        # managed runs call preflight_runtime(), which still requires the full
        # ApprovalMode API before any Cycle is created.
        from openai_codex import Sandbox  # type: ignore
        try:
            from openai_codex import ApprovalMode  # type: ignore
            deny_all = ApprovalMode.deny_all
        except (ImportError, AttributeError):
            deny_all = None
        client = self._ensure_client()
        thread = self.threads.get(thread_id or "")
        newly_attached = thread is None
        if thread is None:
            sandbox = (Sandbox.read_only if role == "investigator" and
                       bool(getattr(self, "_preservation_read_only", False))
                       else self._thread_sandbox(role))
            thread_cwd = Path(self.cwd)
            if role == "literature":
                thread_cwd = thread_cwd / "literature"
                thread_cwd.mkdir(parents=True, exist_ok=True)
            # A resumed SDK thread already owns its developer identity and skill
            # contract.  Re-sending them here appends a second copy to the thread.
            effective_config = self._thread_config(role, provider)
            extra: dict[str, Any] = {"config": effective_config}
            if not thread_id:
                compute_python = self._compute_python()
                compute_rule = (
                    f"Scientific Python is fixed for this TheoryMaster task: {compute_python}. "
                    "Use this interpreter for every numerical script and package check; it is also first on PATH. "
                    "Do not fall back to another Python interpreter or install packages unless this interpreter itself reports a missing dependency."
                )
                writer_guard = f"{PROCESS_CONTROL_GUARD} {REPO_GUARD}" if role in WRITE_ROLES else ""
                navigation_rule = self._investigator_navigation_rule() if role == "investigator" else ""
                extra["developer_instructions"] = developer_instructions(
                    role,
                    " ".join(part for part in (writer_guard, compute_rule, navigation_rule) if part),
                    cycle_id=self.cycle_id,
                    task_config_path=self.task_config_path,
                )
            # Gateway enforcement is independent of Codex's model-backed reviewer.
            # Never request an approval; sandbox boundaries remain in force.
            if deny_all is not None:
                extra["approval_mode"] = deny_all
            if thread_id:
                # Resumed Codex threads otherwise silently retain their old
                # persisted model, even when the operator intentionally uses
                # a lower-cost Investigator route for this recovery episode.
                # SDK thread_resume must pin BOTH model and provider; omitting
                # model_provider can silently use api.openai.com on a resumed
                # API-backed thread, bypassing the configured auth.
                resumed_provider = ("openai_https" if provider == "CodexHTTPS"
                                    else None if provider in ACCOUNT_PROVIDERS else provider)
                thread = client.thread_resume(
                    thread_id, model=model, model_provider=resumed_provider, **extra
                )
            else:
                start_args: dict[str, Any] = {
                    "model": model,
                    "sandbox": sandbox,
                    "cwd": str(thread_cwd),
                    **extra,
                }
                if provider in ACCOUNT_PROVIDERS:
                    # Account-backed Codex uses the user's existing ChatGPT login.
                    # CodexHTTPS is an explicit HTTPS-only transport for proxy
                    # environments where long WebSocket streams are unstable.
                    start_args["model_provider"] = "openai_https" if provider == "CodexHTTPS" else None
                else:
                    start_args["model_provider"] = provider
                thread = client.thread_start(**start_args)
                if self.collector is not None:
                    record_input = getattr(self.collector, "on_agent_input", None)
                    if callable(record_input):
                        record_input(
                            role=role, stage="thread_initialization",
                            content=extra["developer_instructions"],
                            input_kind="developer_instructions",
                        )
        identifier = getattr(thread, "id", thread_id or f"codex-{role}")
        if newly_attached and self.collector is not None:
            record = getattr(self.collector, "on_codex", None)
            if callable(record):
                try:
                    record({"event": "THREAD_CONFIG", "provider": provider,
                            "thread_action": "resume" if thread_id else "start",
                            "native_compaction_enabled": "model_auto_compact_token_limit" in effective_config,
                            "compact_threshold_tokens": effective_config.get("model_auto_compact_token_limit"),
                            "compact_scope": effective_config.get("model_auto_compact_token_limit_scope"),
                            "native_functions_excluded": False},
                           role=role, thread_id=identifier)
                except Exception:
                    pass
        self.threads[identifier] = thread
        if provider in ACCOUNT_PROVIDERS:
            # The app-server/client owns the authentication snapshot. A different
            # Task may change the host-wide selector while this turn is running,
            # so never infer a live turn's identity from the later selector.
            if self.client_account in {"1", "2"}:
                self.thread_accounts[identifier] = self.client_account
            else:
                from .codex_accounts import selected_account
                self.thread_accounts[identifier] = selected_account()
        if not thread_id:
            self._new_thread_ids.add(identifier)
        return identifier
    def reconfigure_cached_thread(self, role: str, thread_id: str,
                                  route: RoleRoute) -> None:
        """Change a cached thread's NEXT invocation model, never an active Turn.

        Called only from Workflow's serial Agent boundary. A thread absent
        from the live cache will be resumed normally by ensure_thread later.
        """
        if thread_id not in self.threads:
            return
        from openai_codex import ApprovalMode  # type: ignore
        resumed = self._ensure_client().thread_resume(
            thread_id, model=route.model,
            config=self._thread_config(role, route.provider),
            approval_mode=ApprovalMode.deny_all)
        if getattr(resumed, "id", thread_id) != thread_id:
            raise RuntimeError("provider changed the thread identity during model reconfiguration")
        self.threads[thread_id] = resumed

    def drop_thread(self, thread_id: str | None) -> None:
        """Discard a cached Codex thread so the next ensure_thread starts fresh."""
        if thread_id:
            self.threads.pop(thread_id, None)
            self.thread_accounts.pop(thread_id, None)
            self._new_thread_ids.discard(thread_id)

    def close(self) -> None:
        """Close this adapter's local app-server while leaving remote thread IDs resumable."""
        client = self.client
        self.client = None
        self.threads.clear()
        self.thread_accounts.clear()
        self._new_thread_ids.clear()
        if client is not None:
            try:
                client.close()
            except Exception:
                self._logger.debug("failed to close disposable Codex client", exc_info=True)
    def snapshot_budget_review_thread(self, thread_id: str, destination: Path) -> dict[str, Any]:
        """Read the EXISTING Investigator thread; do not resume or invoke science.

        SDK thread/read can expose original visible tool outputs omitted from the
        lightweight runtime events. Private reasoning and compaction are excluded
        by the archive writer. Errors are handled by the resource-review layer,
        which still supplies the independently persisted dialogue timeline.
        """
        from .budget_review_transcript import write_sdk_thread_snapshot

        client = self._ensure_client()
        inner = getattr(client, "_client", None)
        if inner is None or not callable(getattr(inner, "thread_read", None)):
            raise RuntimeError("Codex SDK does not expose thread/read")
        result = inner.thread_read(thread_id, include_turns=True)
        return write_sdk_thread_snapshot(
            result, thread_id=thread_id, destination=Path(destination),
        )

    def invoke_budget_review(self, prompt: str, model: str, provider: str) -> tuple[str, str]:
        """Fresh *tool-enabled but read-only* resource review, never a Judge.

        The auxiliary role has Sandbox.read_only + ApprovalMode.deny_all.
        It may inspect original recorded dialogue, tool outputs, and workspace
        artifacts without any permission to change the scientific state.
        """
        previous_collector = self.collector
        self.collector = None
        self._turn_reads = None
        self.last_budget_review_reads: list[str] = []
        try:
            result = self.invoke("budget_reviewer", prompt, None, model, provider)
            self.last_budget_review_reads = list(self._turn_reads or [])
            return result
        finally:
            self._turn_reads = None
            self.collector = previous_collector

    def invoke_attention_review(self, prompt: str, model: str, provider: str) -> tuple[str, str]:
        """SDK-owned fresh, read-only auxiliary call; do not replace active Agent."""
        previous_collector = self.collector
        self.collector = None
        try:
            return self.invoke("attention_reviewer", prompt, None, model, provider)
        finally:
            self.collector = previous_collector

    def invoke_runtime_review(self, prompt: str, model: str, provider: str) -> tuple[str, str]:
        """Fresh read-only Runtime Supervisor turn after a FAILED Cycle."""
        previous_collector = self.collector
        self.collector = None
        try:
            return self.invoke("runtime_supervisor", prompt, None, model, provider)
        finally:
            self.collector = previous_collector

    def invoke_goal(self, role: str, *, goal_objective: str, context_prompt: str,
                    handoff_path: str, thread_id: str | None,
                    model: str, provider: str,
                    token_allowance: int | None = None,
                    tokens_before_goal: int = 0) -> tuple[str, str]:
        """Production Investigator native Goal; Findings exist only on Goal complete.

        Other roles still use ordinary stage-bound Codex turns.
        """
        if role != "investigator":
            raise ValueError("Native Goal execution is reserved for Investigator")
        from .native_goal import (
            run_native_goal, read_goal_findings, NativeGoalIncomplete,
            NativeGoalBudgetExceeded, CODEX_GOAL_MAX_CHARS,
            verify_native_goal_context_reference, native_goal_setup_turn,
        )
        # Fail before the paid/setup model Turn if a Native Goal reference is
        # inconsistent with the complete immutable objective supplied in Context.
        if len(goal_objective) > CODEX_GOAL_MAX_CHARS:
            raise ValueError(
                f"native Goal objective exceeds Codex's {CODEX_GOAL_MAX_CHARS}-character limit: {len(goal_objective)}"
            )
        verify_native_goal_context_reference(goal_objective, context_prompt)
        identifier = self.ensure_thread(role, thread_id, model, provider)
        thread = self.threads[identifier]
        from .provider_capacity import (
            CapacityReconnectWindow, CAPACITY_WAIT_LIMIT_SECONDS, is_model_at_capacity,
            is_native_goal_thread_busy, CapacityRecoveryTimeout,
            settle_failed_capacity_turn,
        )
        from .native_goal import NativeGoalResult
        from .backend_config import ACCOUNT_PROVIDERS
        capacity_window = CapacityReconnectWindow(
            Path(handoff_path), thread_id=identifier, model=model, provider=provider
        )
        def capacity_event(event_name: str, *, wait: float = 0.0, retry: int = 0,
                           elapsed: float = 0.0, error: str = "") -> None:
            if self.collector is not None:
                try:
                    self.collector.on_codex({
                        "event": event_name, "agent": role, "provider": provider,
                        "duration_s": wait,
                        "detail": f"attempt={retry}; elapsed={elapsed:.1f}s; max=1800s",
                        "error": error[:400],
                    }, role=role, thread_id=identifier)
                except Exception:
                    self._logger.debug("capacity wait observability failed", exc_info=True)

        prior_window = capacity_window.snapshot() if provider in ACCOUNT_PROVIDERS else None

        def capacity_timeout(receipt: dict, error: str) -> None:
            """After a bounded provider retry window, escalate OPERATIONS, not science."""
            self.last_native_goal = {
                "status": "failed", "thread_id": identifier,
                "capacity_reconnect": {
                    "status": "timeout", "retry_count": int(receipt["retries"]),
                    "busy_retries": int(receipt.get("busy_retries") or 0),
                    "limit_seconds": CAPACITY_WAIT_LIMIT_SECONDS, "error": error[:400],
                },
            }
            capacity_event("CAPACITY_RETRY_EXHAUSTED",
                           retry=int(receipt["retries"]),
                           elapsed=CAPACITY_WAIT_LIMIT_SECONDS, error=error)
            capacity_window.clear()
            raise CapacityRecoveryTimeout(
                thread_id=identifier,
                detail=f"Provider capacity recovery timed out after "
                      f"{CAPACITY_WAIT_LIMIT_SECONDS}s "
                      f"(capacity failures={receipt['retries']}, "
                      f"thread busy={receipt.get('busy_retries', 0)}): {error}",
            )

        if prior_window is not None and capacity_window.remaining(prior_window) <= 0:
            capacity_timeout(prior_window, str(prior_window.get("last_error") or "capacity wait expired"))
        if identifier in self._new_thread_ids or context_prompt:
            # Context/qualifier/paths are supplied in a separate ordinary turn,
            # not confused with the Goal's immutable scientific finish condition.
            thread.run(native_goal_setup_turn(context_prompt, goal_objective))
            self._new_thread_ids.discard(identifier)
        provider_usage_baseline: int | None = None
        capacity_error = ""
        terminal_provider_error = ""
        failed_capacity_turn_id = ""
        recovered_progress = False

        def on_event(event: Any) -> None:
            nonlocal provider_usage_baseline, capacity_error, recovered_progress, terminal_provider_error, failed_capacity_turn_id
            from .observability.codex_events import map_notification
            mapped = map_notification(event, role=role, thread_id=identifier)
            if not mapped:
                return
            if mapped.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED"}:
                terminal_provider_error = str(mapped.get("error") or "provider turn failed")
            if mapped.get("event") in {"LLM_FAILED", "LLM_TURN_FAILED", "LLM_TURN_COMPLETED"}:
                if is_model_at_capacity(mapped.get("error")):
                    capacity_error = str(mapped["error"])
                    failed_capacity_turn_id = str(mapped.get("turn_id") or "")
            if mapped.get("event") in {"LLM_MESSAGE", "TOOL_ACTIVITY"}:
                # Fresh model output AFTER an old transport failure is evidence
                # that the earlier failure is not this Goal's terminal cause.
                terminal_provider_error = ""
                recovered_progress = True
                # An earlier capacity warning is no longer terminal if the
                # provider has since delivered fresh scientific work.
                capacity_error = ""
                failed_capacity_turn_id = ""
            if self.collector is not None:
                try:
                    self.collector.on_codex(mapped, role=role, thread_id=identifier)
                except Exception:
                    self._logger.debug("native Goal observability event failed", exc_info=True)
            if token_allowance is None or mapped.get("event") != "TOKEN_USAGE":
                return
            raw = mapped.get("raw_usage")
            if not isinstance(raw, dict):
                return
            # thread/tokenUsage.total is lifetime cumulative (cached inputs included).
            # First event's last-request amount counts usage NEW to this Goal.
            provider_total = int(raw.get("input_tokens") or 0) + int(raw.get("output_tokens") or 0)
            if provider_usage_baseline is None:
                last = mapped.get("last_request_usage")
                request_total = (
                    int(last.get("input_tokens") or 0) + int(last.get("output_tokens") or 0)
                    if isinstance(last, dict) else 0
                )
                provider_usage_baseline = max(0, provider_total - request_total)
            observed = int(tokens_before_goal) + max(0, provider_total - provider_usage_baseline)
            if self.collector is not None:
                self.collector.record_native_goal_usage(
                    role, observed_tokens=observed, allowance_tokens=int(token_allowance)
                )
            if observed >= int(token_allowance):
                raise NativeGoalBudgetExceeded(identifier, observed, int(token_allowance))

        # Native blocked is never Investigator's permission to end research.
        # A bounded automatic re-entry handles accidental self-block while
        # preserving the same frozen objective and scientific thread; repeating
        # the fault beyond this guard is an operational failure, not science.
        premature_block_retries = 0
        max_premature_block_retries = 2
        while True:
            capacity_error = ""
            terminal_provider_error = ""
            failed_capacity_turn_id = ""
            recovered_progress = False
            try:
                goal = run_native_goal(thread, goal_objective, on_event=on_event)
            except NativeGoalBudgetExceeded as exc:
                self.last_native_goal = {
                    "status": "budgetLimited", "source": "theory_master_live_token_guard",
                    "thread_id": identifier, "observed_tokens": exc.observed_tokens,
                    "allowance_tokens": exc.allowance_tokens,
                }
                raise
            except Exception as exc:
                # A direct capacity exception may occur before a turn event.
                # Do not retry unrelated transport, quota, contract or SDK errors.
                if is_native_goal_thread_busy(exc, identifier) and prior_window is not None:
                    # A rejected Goal start does not create another Goal. Never
                    # call thread_goal_clear() on a potentially ACTIVE Goal:
                    # preserve the original thread and wait for server idle.
                    receipt = capacity_window.record_busy(str(exc))
                    prior_window = receipt
                    if capacity_window.remaining(receipt) <= 0:
                        capacity_timeout(receipt, str(exc))
                    delay = capacity_window.busy_delay(receipt)
                    capacity_event("CAPACITY_THREAD_BUSY_WAIT", wait=delay,
                                   retry=int(receipt["busy_retries"]),
                                   elapsed=CAPACITY_WAIT_LIMIT_SECONDS - capacity_window.remaining(receipt),
                                   error=str(exc))
                    import time
                    time.sleep(delay)
                    if capacity_window.remaining(receipt) <= 0:
                        capacity_timeout(receipt, str(exc))
                    continue
                if is_provider_quota_exhausted(exc):
                    # Native Goal can raise an explicit account quota error before
                    # returning a terminal status. Route it through Workflow's
                    # durable PAUSED/quota-watcher protocol; never retry it as
                    # model capacity or collapse it into a generic FAILED Worker.
                    capacity_window.clear()
                    accounts = getattr(self, "thread_accounts", {})
                    account = accounts.get(identifier or "") if isinstance(accounts, dict) else None
                    self.last_native_goal = {
                        "status": "usageLimited",
                        "thread_id": identifier,
                        "runtime_failure_kind": "provider_quota_exhausted",
                        "provider": provider,
                        "account": account,
                    }
                    raise ProviderQuotaExhausted(
                        str(exc), provider=provider, account=account
                    ) from exc
                if not is_model_at_capacity(exc):
                    raise
                goal = NativeGoalResult("blocked", identifier, 0, 0)
                capacity_error = str(exc)
            if recovered_progress and prior_window is not None:
                # Real scientific work after reconnect ends that outage episode;
                # a later capacity failure gets a fresh 30-minute window.
                capacity_window.clear()
                prior_window = None
            self.last_native_goal = {
                "status": goal.status, "thread_id": identifier,
                "physical_turns": goal.physical_turns, "tools_started": goal.tools_started,
            }
            if goal.status == "complete":
                capacity_window.clear()
                thread._client.thread_goal_clear(thread.id)
                return identifier, read_goal_findings(goal, Path(handoff_path))
            if goal.status == "blocked" and not capacity_error and not terminal_provider_error:
                capacity_window.clear()
                if premature_block_retries < max_premature_block_retries:
                    premature_block_retries += 1
                    # A finished logical Goal must be cleared before starting
                    # another, but the scientific conversation stays intact.
                    thread._client.thread_goal_clear(thread.id)
                    capacity_event("NATIVE_GOAL_UNEXPECTED_BLOCK_RETRY",
                                   retry=premature_block_retries)
                    continue
                from .supervisor_incident import NativeGoalRuntimeFailure
                self.last_native_goal["unexpected_block_retries"] = premature_block_retries
                self.last_native_goal["runtime_failure_kind"] = "repeated_unexpected_block"
                raise NativeGoalRuntimeFailure(
                    thread_id=identifier,
                    error="Native Goal repeatedly returned unauthorized blocked "
                          "without a provider error or Investigator budget limit; "
                          "operational recovery required, not scientific preservation."
                )
            if goal.status != "blocked" or not capacity_error or provider not in ACCOUNT_PROVIDERS:
                capacity_window.clear()
                if goal.status == "blocked" and terminal_provider_error:
                    # Do not turn provider LLM_FAILED into an Investigator
                    # scientific verdict. Runtime Supervisor receives the exact
                    # error and may authorize a *bounded* managed resume.
                    from .supervisor_incident import NativeGoalRuntimeFailure
                    self.last_native_goal["provider_failure"] = terminal_provider_error[:1200]
                    raise NativeGoalRuntimeFailure(
                        thread_id=identifier, error=terminal_provider_error,
                        provider_event="LLM_FAILED",
                    )
                raise NativeGoalIncomplete(goal.status, identifier)
            receipt = capacity_window.record_failure(capacity_error)
            prior_window = receipt
            remaining = capacity_window.remaining(receipt)
            elapsed = min(CAPACITY_WAIT_LIMIT_SECONDS,
                          CAPACITY_WAIT_LIMIT_SECONDS - remaining)
            if remaining <= 0:
                capacity_timeout(receipt, capacity_error)
            wait = capacity_window.delay(receipt)
            # Some providers emit a terminal capacity failure while the exact
            # physical turn remains active. Only when that failed turn is still
            # the last active turn, settle it before a new native Goal. Never
            # interrupt another turn or clear an unconfirmed active Goal.
            if failed_capacity_turn_id and settle_failed_capacity_turn(thread, failed_capacity_turn_id):
                capacity_event("CAPACITY_FAILED_TURN_SETTLED",
                               retry=int(receipt["retries"]), error=capacity_error)
            thread._client.thread_goal_clear(thread.id)
            capacity_event("CAPACITY_RETRY_WAIT", wait=wait, retry=receipt["retries"],
                           elapsed=elapsed, error=capacity_error)
            import time
            time.sleep(wait)
            # Re-read the durable deadline before retry to avoid crossing the
            # window after a long scheduling pause or Runner interruption.
            if capacity_window.remaining(receipt) <= 0:
                capacity_timeout(receipt, capacity_error)

    def invoke(self, role: str, prompt: str, thread_id: str | None, model: str, provider: str,
               *, on_delta: Any | None = None,
               cancel_event: threading.Event | None = None) -> tuple[str, str]:
        from theory_master.observability.codex_events import (
            extract_agent_delta,
            extract_agent_text,
            extract_final_from_turn,
            extract_final_from_thread,
            is_transient_reconnect_error,
            map_notification,
        )
        from theory_master.read_trace import paths_from_event
        # `thread_id` is the continuation signal. New threads receive Role Core
        # plus the optional-skill whitelist in developer instructions; SKILL.md
        # bodies are not eagerly attached. Resumed turns carry only the stage delta.
        # Direct callers may pass no thread ID; ensure_thread will create one
        # during this invocation, so that path is also a first turn.
        include_skills = thread_id is None or thread_id in self._new_thread_ids
        payload = turn_input(role, prompt, include_skills=include_skills, task_config_path=self.task_config_path)
        if thread_id:
            self._new_thread_ids.discard(thread_id)
        identifier = thread_id
        last_error: Exception | None = None
        retry_payload: str | None = None
        budget_review_finalization_attempted = False
        for attempt in range(self.OUTER_TURN_RETRIES + 1):
            try:
                identifier = self.ensure_thread(role, identifier, model, provider)
                self._new_thread_ids.discard(identifier)
                thread = self.threads[identifier]
                turn = getattr(thread, "turn", None)
                if not callable(turn):
                    result = thread.run(payload)
                    return identifier, getattr(result, "final_response", "") or ""
                turn_payload = retry_payload if retry_payload is not None else payload
                retry_payload = None
                handle = thread.turn(turn_payload)
                texts: list[str] = []
                delta_text: list[str] = []
                streamed_events: list[Any] = []
                turn_reads: list[str] = []
                terminal_error: str | None = None
                approval_error: str | None = None
                turn_completed = False
                completed_turn_id: str | None = None
                watchdog_fired = threading.Event()
                watchdog_lock = threading.Lock()
                watchdog: list[threading.Timer | None] = [None]
                cancel_watch_done = threading.Event()

                def _watch_cancel() -> None:
                    if cancel_event is None:
                        return
                    while not cancel_watch_done.wait(0.1):
                        if cancel_event.is_set():
                            try:
                                handle.interrupt()
                            except Exception:
                                pass
                            return

                cancel_watch = threading.Thread(target=_watch_cancel, daemon=True)
                cancel_watch.start()

                def _watchdog_interrupt() -> None:
                    # A terminal notification can be queued at the same time
                    # as the timer callback.  Do not turn a completed turn
                    # into a reconnect merely because the callback won the
                    # scheduling race.
                    with watchdog_lock:
                        if turn_completed:
                            return
                        watchdog_fired.set()
                    try:
                        handle.interrupt()
                    except Exception:
                        pass

                def _arm_watchdog() -> None:
                    timeout_s = self._watchdog_seconds()
                    with watchdog_lock:
                        old = watchdog[0]
                        if old is not None:
                            old.cancel()
                        timer = threading.Timer(timeout_s, _watchdog_interrupt)
                        timer.daemon = True
                        watchdog[0] = timer
                        timer.start()

                _arm_watchdog()
                try:
                    for event in handle.stream():
                        # Keep the original SDK notifications as well as the
                        # normalized observability events.  The official SDK
                        # collector has stricter knowledge of the installed
                        # notification models than our duck-typed mapper and
                        # is the final-response fallback for real streams.
                        streamed_events.append(event)
                        # The SDK may emit turn/completed before the final
                        # item/completed notification.  Keep draining the
                        # stream so the agent message is not lost, but do not
                        # let the inactivity watchdog interrupt an already
                        # terminal turn while the transport closes.
                        with watchdog_lock:
                            completed = turn_completed
                        if not completed:
                            _arm_watchdog()
                        try:
                            mapped = map_notification(event, role=role, thread_id=identifier)
                        except Exception:
                            mapped = None
                        if mapped:
                            turn_reads.extend(paths_from_event(mapped))
                        if mapped and mapped.get("event", "").startswith("COMPACTION_"):
                            mapped.setdefault("episode_id", identifier)
                            mapped.setdefault("compact_threshold_tokens", self._native_compact_threshold())
                            mapped.setdefault("compact_scope", self._native_compact_scope())
                        if mapped and self.collector is not None:
                            try:
                                self.collector.on_codex(mapped, role=role, thread_id=identifier)
                            except Exception:
                                pass
                        if mapped and is_provider_quota_exhausted(mapped.get("error")):
                            raise ProviderQuotaExhausted(
                                str(mapped.get("error") or "provider quota exhausted"),
                                provider=provider,
                                account=self.thread_accounts.get(identifier or ""),
                            )
                        if mapped and mapped.get("event") == "TOOL_APPROVAL_UNAVAILABLE":
                            approval_error = str(mapped.get("error") or "automatic approval review failed")
                        if mapped and mapped.get("event") == "LLM_RECONNECTING":
                            network_result = self._run_network_guard("llm-reconnecting", required=False)
                            if network_result and network_result.get("changed"):
                                raise ConnectionError(
                                    "Codex connection route changed by codex-net after LLM_RECONNECTING; rebuild client and retry turn"
                                )
                        method = str(getattr(event, "method", "") or "")
                        event_payload = getattr(event, "payload", None)
                        if mapped and mapped.get("event") in {"LLM_TURN_FAILED", "LLM_FAILED"}:
                            terminal_error = str(mapped.get("error") or "Codex turn failed")
                        if mapped and mapped.get("event") == "LLM_TURN_COMPLETED" and mapped.get("error"):
                            terminal_error = str(mapped["error"])
                        if method == "item/completed" or method.endswith("/agentMessage/completed"):
                            text = extract_agent_text(event_payload)
                            if text:
                                texts.append(text)
                        elif method == "item/agentMessage/delta" or method.endswith("/agentMessage/delta"):
                            delta = extract_agent_delta(event_payload)
                            if delta:
                                delta_text.append(delta)
                                if callable(on_delta):
                                    try:
                                        on_delta(delta)
                                    except Exception:
                                        self._logger.debug("Codex stream callback failed", exc_info=True)
                        elif method == "turn/completed":
                            text = extract_final_from_turn(event_payload)
                            if text:
                                texts.append(text)
                            completed_turn_id = getattr(
                                getattr(event_payload, "turn", None), "id", None
                            )
                            # The SDK has delivered the terminal event.  Some
                            # transports keep the underlying stream open for a
                            # short time after this notification; waiting for
                            # that close lets the inactivity watchdog interrupt
                            # an already-finished turn and incorrectly retry it
                            # on the same server-side writer.
                            with watchdog_lock:
                                turn_completed = True
                finally:
                    cancel_watch_done.set()
                    cancel_watch.join(timeout=0.2)
                    with watchdog_lock:
                        timer = watchdog[0]
                        if timer is not None:
                            timer.cancel()
                self._turn_reads = list(dict.fromkeys(turn_reads))
                if cancel_event is not None and cancel_event.is_set():
                    raise CodexTurnCancelled("Codex turn stopped by operator")
                # Codex can emit error(400) followed by turn/completed with no
                # assistant text. This is a *rejected request*, not an empty
                # success; do not retry the unchanged unsupported model 5 times.
                rejected = provider_request_rejection(terminal_error)
                if rejected is not None:
                    raise rejected
                if approval_error:
                    raise ToolApprovalUnavailable(approval_error)
                # Once the inactivity watchdog has fired, this turn is a timeout
                # even if handle.interrupt() causes the transport to emit a later
                # turn/completed notification. Any assistant text/deltas collected
                # before that interrupt are progress/partial output, never a final
                # Agent result.
                if watchdog_fired.is_set():
                    raise TimeoutError(f"Codex turn inactivity watchdog timed out after {self._watchdog_seconds():g}s")
                if turn_completed:
                    if texts:
                        return identifier, texts[-1]
                    if delta_text:
                        return identifier, "".join(delta_text)
                    # `TurnHandle.stream()` yields the same notifications that
                    # the SDK's `handle.run()` collector consumes.  Reuse that
                    # collector after the stream has been drained so current
                    # SDK ItemCompletedNotification instances are recognized
                    # even when their wire shape is not covered by the
                    # duck-typed extractor above.
                    try:
                        from openai_codex._run import _collect_turn_result  # type: ignore

                        collected = _collect_turn_result(
                            iter(streamed_events), turn_id=str(completed_turn_id or handle.id)
                        )
                        if collected.final_response:
                            return identifier, collected.final_response
                    except Exception:
                        self._logger.debug(
                            "official Codex turn-result recovery failed",
                            exc_info=True,
                        )
                    # A few app-server transports emit the terminal turn
                    # notification before the completed agent item reaches
                    # the turn subscription.  The official SDK persists the
                    # item in thread history, so read that history once before
                    # declaring a successful turn response missing.
                    read = getattr(thread, "read", None)
                    if callable(read):
                        # Some app-server transports publish turn/completed
                        # before the completed item is visible to thread/read.
                        # Give that persistence boundary a short bounded grace
                        # period; otherwise a valid turn is misreported as the
                        # TUI error "completed without a final response".
                        recovered = None
                        for recovery_attempt in range(self.COMPLETED_RESPONSE_RECOVERY_ATTEMPTS):
                            try:
                                recovered = extract_final_from_thread(
                                    read(include_turns=True),
                                    turn_id=completed_turn_id,
                                )
                            except Exception:
                                recovered = None
                            if recovered:
                                return identifier, recovered
                            if recovery_attempt + 1 < self.COMPLETED_RESPONSE_RECOVERY_ATTEMPTS:
                                time.sleep(self.COMPLETED_RESPONSE_RECOVERY_DELAY_SECONDS)
                    raise RuntimeError("Codex backend delivery error: turn completed without assistant final response")
                if terminal_error:
                    raise RuntimeError(terminal_error)
                if texts:
                    return identifier, texts[-1]
                raise RuntimeError("Codex backend delivery error: turn ended without assistant final response")
            except Exception as exc:
                if isinstance(exc, CodexTurnCancelled):
                    raise
                last_error = exc
                message = str(exc)
                if isinstance(exc, CodexProviderRequestRejected):
                    raise
                if isinstance(exc, ProviderQuotaExhausted):
                    raise
                if is_provider_quota_exhausted(message):
                    raise ProviderQuotaExhausted(
                        message,
                        provider=provider,
                        account=self.thread_accounts.get(identifier or ""),
                    ) from exc
                watchdog_timeout = isinstance(exc, TimeoutError) and "inactivity watchdog" in message.lower()
                retryable = is_transient_reconnect_error(message) or any(
                    marker in message.lower()
                    for marker in (
                        "connection", "broken pipe", "eof", "timed out", "timeout",
                        "app-server", "disconnected", "backend delivery error",
                        "process is not running", "process closed stdout",
                    )
                )
                if role == "budget_reviewer" and "turn completed without assistant final response" in message.lower():
                    # A completed read-only review may have inspected substantial
                    # evidence without producing its final JSON. Replaying the
                    # entire archive on fresh turns wastes quota; ask the SAME
                    # persisted thread to finalize once. Never infer approval.
                    if budget_review_finalization_attempted:
                        raise
                    budget_review_finalization_attempted = True
                    retry_payload = turn_input(
                        role,
                        "The prior read-only Budget Reviewer turn is complete but had no final "
                        "JSON. Do not repeat any research or tools. From the material already "
                        "inspected, return one decision JSON now with decision, reason, "
                        "next_test, evidence_ids, progress_refs. If a defensible review "
                        "cannot be completed from inspected evidence, say why instead of "
                        "inventing a scientific finding or resource approval.",
                        include_skills=False,
                        task_config_path=self.task_config_path,
                    )
                if watchdog_timeout:
                    # The interrupted server-side thread already retains the task,
                    # tool calls and any workspace effects from the timed-out turn.
                    # Resume that same thread with a minimal continuation signal
                    # instead of replaying the full original task prompt.
                    retry_payload = turn_input(
                        role,
                        "The previous turn was interrupted by the TheoryMaster inactivity watchdog. "
                        "Continue the same assigned task from the current thread and workspace state. "
                        "Do not restart or repeat work that is already complete. Inspect existing artifacts/results "
                        "only as needed, then return the required stage output.",
                        include_skills=False,
                        task_config_path=self.task_config_path,
                    )
                if not retryable or attempt >= self.OUTER_TURN_RETRIES:
                    raise
                delay = self.OUTER_RETRY_DELAYS[min(attempt, len(self.OUTER_RETRY_DELAYS) - 1)]
                if self.collector is not None:
                    try:
                        self.collector.on_codex(
                            {"event": "LLM_RECONNECTING", "error": f"{message}; retry {attempt + 1}/{self.OUTER_TURN_RETRIES}",
                             "retry_index": attempt + 1},
                            role=role, thread_id=identifier or "",
                        )
                    except Exception:
                        pass
                self._logger.warning(
                    "Codex turn temporarily unavailable for role=%s; retry %d/%d in %ss: %s",
                    role, attempt + 1, self.OUTER_TURN_RETRIES, delay, message,
                )
                # Close the old local app-server before resuming the same
                # server-side thread. Every cached Thread is bound to this client,
                # so invalidate all of them before closing it; keeping another
                # role's wrapper would later send through the closed app-server.
                old_client = self.client
                self.client = None
                self.client_account = None
                self.threads.clear()
                self._new_thread_ids.clear()
                if old_client is not None:
                    try:
                        old_client.close()
                    except Exception:
                        self._logger.debug("failed to close Codex client during retry", exc_info=True)
                time.sleep(delay)
        assert last_error is not None
        raise last_error
