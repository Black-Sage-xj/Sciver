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


from .codex_errors import (
    ToolApprovalUnavailable, CodexProviderRequestRejected, CodexTurnCancelled,
    CodexTurnFailed, provider_request_rejection,
)


class CodexBackend:
    """Thin official-SDK adapter; imports lazily so offline use has no dependency."""
    def __init__(self, cwd: Path, routes: dict[str, RoleRoute] | None = None, *,
                 task_config_path: Path | None = None,
                 writable_roots: tuple[Path, ...] | None = None):
        self.cwd = cwd
        # Only Research Chat consumes these account-scoped capabilities. Normal
        # scientific roles retain their existing Cycle-local sandbox policy.
        self.writable_roots = tuple(dict.fromkeys(
            Path(root).expanduser().resolve() for root in (writable_roots or ())
        ))
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
        if role == "research_chat":
            config["model_reasoning_summary"] = "auto"
        if provider in ACCOUNT_PROVIDERS:
            # Preserve the historical account-backed default when a route does
            # not explicitly pin reasoning effort.
            config["model_reasoning_effort"] = effort or "medium"
        elif effort:
            config["model_reasoning_effort"] = effort
        if provider in ACCOUNT_PROVIDERS and self._native_compaction_enabled():
            config["model_auto_compact_token_limit"] = self._native_compact_threshold()
            config["model_auto_compact_token_limit_scope"] = self._native_compact_scope()
        if role == "research_chat":
            # Preparation commands need to persist plans, literature indexes and
            # project registration in this account's state/workspace. Do not
            # inherit a globally writable /tmp or ambient TMPDIR alongside them.
            roots = (Path(self.cwd).expanduser().resolve(), *getattr(self, "writable_roots", ()))
            config["sandbox_workspace_write"] = {
                "writable_roots": [str(root) for root in dict.fromkeys(roots)],
                "network_access": True,
                "exclude_tmpdir_env_var": True,
                "exclude_slash_tmp": True,
            }
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
        # Resolving a venv bin/python symlink selects its base interpreter and
        # loses that environment's installed scientific dependencies.
        return path.absolute()

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
            self._verify_provider_runtime()
            self._run_network_guard("theorymaster-startup" if not self._network_checked else "theorymaster-client-rebuild", required=True)
            routes = self.routes or DEFAULT_ROUTES
            account_backed = any(route.provider in ACCOUNT_PROVIDERS for route in routes.values())
            if any(route.provider == "CodexHTTPS" for route in routes.values()):
                self._ensure_openai_https_provider_registered()
            overrides, _provider_env = self._runtime_config(self.routes)
            environment = self._sdk_environment(self.routes)
            if (set(routes) == {"research_chat"}
                    or os.environ.get("THEORYMASTER_CHAT_SANDBOX") == "1"):
                # Nested literature SDK/CLI processes need temporary IPC/files,
                # while the chat sandbox deliberately excludes global /tmp.
                # Each backend uses its own cwd: reusing the parent's TMPDIR
                # hits its read-only synthetic bubblewrap mount-registry lock.
                # Pass this workspace directory to the client, not the host env.
                temp_root = Path(self.cwd).expanduser().resolve() / ".theory-master-tmp"
                temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                environment["TMPDIR"] = str(temp_root)
                environment["THEORYMASTER_CHAT_SANDBOX"] = "1"
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
            from .codex_sdk_compat import install_approval_handler
            install_approval_handler(self.client, make_approval_decision(
                (Path(self.cwd).resolve(), Path(tempfile.gettempdir()).resolve())
            ))
        return self.client

    @staticmethod
    def _verify_provider_runtime() -> None:
        from .codex_sdk_compat import verify_sdk
        verify_sdk()

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
        return Sandbox.workspace_write if role in WRITE_ROLES or role == "research_chat" else Sandbox.read_only

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
        from .codex_sdk_compat import read_thread
        result = read_thread(client, thread_id, include_turns=True)
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
                    tokens_before_goal: int = 0,
                    control_check: Any = None,
                    goal_identity_observer: Any = None) -> tuple[str, str]:
        """Production Investigator native Goal; Findings require native completion."""
        from .codex_goal_transport import NativeGoalRunner
        return NativeGoalRunner(
            self, role, goal_objective=goal_objective, context_prompt=context_prompt,
            handoff_path=handoff_path, thread_id=thread_id, model=model, provider=provider,
            token_allowance=token_allowance, tokens_before_goal=tokens_before_goal,
            control_check=control_check,
            goal_identity_observer=goal_identity_observer,
        ).run()

    def invoke(self, role: str, prompt: str, thread_id: str | None, model: str, provider: str,
               *, on_delta: Any | None = None,
               cancel_event: threading.Event | None = None,
               on_event: Any | None = None, on_handle: Any | None = None,
               input_images: list[str] | None = None,
               request_user_input: Any | None = None,
               usage_guard: Any | None = None, control_check: Any | None = None) -> tuple[str, str]:
        from .codex_turn_transport import CodexTurnTransport
        include_skills = thread_id is None or thread_id in self._new_thread_ids
        payload = turn_input(role, prompt, include_skills=include_skills, task_config_path=self.task_config_path)
        if input_images:
            from openai_codex import LocalImageInput, TextInput
            payload = ([TextInput(text=payload)] if isinstance(payload, str) else list(payload)) + [
                LocalImageInput(path=path) for path in input_images]
        if thread_id:
            self._new_thread_ids.discard(thread_id)
        return CodexTurnTransport(self).run(
            role, payload, thread_id, model, provider, on_delta=on_delta,
            cancel_event=cancel_event, on_event=on_event, on_handle=on_handle,
            request_user_input=request_user_input,
            usage_guard=usage_guard, control_check=control_check,
        )
