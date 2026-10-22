from __future__ import annotations

"""Model/provider routing and profile configuration."""

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

class Backend(Protocol):
    def preflight_runtime(self) -> None: ...
    def ensure_thread(self, role: str, thread_id: str | None, model: str, provider: str) -> str: ...
    def invoke(self, role: str, prompt: str, thread_id: str | None, model: str, provider: str) -> tuple[str, str]: ...
    def drop_thread(self, thread_id: str | None) -> None: ...


@dataclass(frozen=True)
class RoleRoute:
    model: str
    provider: str
    model_vendor: str | None = None
    reasoning_effort: str | None = None


ACCOUNT_PROVIDERS = frozenset({"Codex", "ChatGPT", "CodexHTTPS"})
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


DEFAULT_ROUTE = RoleRoute("gpt-6-luna", "CodexHTTPS", model_vendor="openai", reasoning_effort="medium")
KNOWN_ROUTES = {
    "deepseek-flash": RoleRoute("deepseek-flash", "DeepSeek"),
    "gpt-6-luna": RoleRoute("gpt-6-luna", "Codex", model_vendor="openai"),
}
DEFAULT_ROUTES = {
    "research_lead": DEFAULT_ROUTE,
    "judge": DEFAULT_ROUTE,
    "investigator": DEFAULT_ROUTE,
    "challenger": DEFAULT_ROUTE,
    "literature": DEFAULT_ROUTE,
    "reporter": DEFAULT_ROUTE,
}
ROUTE_ROLES = frozenset(DEFAULT_ROUTES)
ROUTE_ROLE_ORDER = tuple(DEFAULT_ROUTES)

MODEL_PROFILE_DEFAULT = "test"
MODEL_PROVIDER_DEFAULT = "codex"
MODEL_PROFILE_DIR = Path(__file__).resolve().parent / "model_profiles"
# Private operator presets live outside the distributed Python package.
LOCAL_MODEL_PROFILE_DIR = Path(__file__).resolve().parents[1] / "config" / "local" / "model_profiles"
MODEL_PROFILE_ALIASES = {
    "basic": "test",
    "1": "test",
    "2": "codex",
    "3": "advanced",
}
MODEL_PROFILE_LEVELS = {
    "test": 1,
    "codex": 2,
    "advanced": 3,
}
MODEL_PROVIDER_MAP: dict[str, dict[str, RoleRoute]] = {
    "codex": {
        "luna": RoleRoute("gpt-6-luna", "CodexHTTPS", model_vendor="openai"),
        "sol": RoleRoute("gpt-6-sol", "CodexHTTPS", model_vendor="openai"),
        "sol61": RoleRoute("gpt-6.1-sol", "CodexHTTPS", model_vendor="openai"),
    },
}


def _profile_route(role: str, payload: dict[str, Any], *, path: Path) -> RoleRoute:
    model = str(payload.get("model") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    effort = str(payload.get("reasoning_effort") or "").strip().lower() or None
    if not model or not provider:
        raise ValueError(f"invalid model profile {path}: role {role!r} requires model and provider")
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ValueError(f"invalid reasoning_effort {effort!r} for {role!r} in {path}")
    vendor = str(payload.get("model_vendor") or "").strip() or None
    if vendor is None and provider in ACCOUNT_PROVIDERS:
        vendor = "openai"
    return RoleRoute(model, provider, model_vendor=vendor, reasoning_effort=effort)


def load_model_profile_file(path: Path) -> dict[str, RoleRoute]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise ValueError(f"invalid model profile {path}: missing [roles]")
    missing = sorted(ROUTE_ROLES - set(roles))
    extra = sorted(set(roles) - ROUTE_ROLES)
    if missing or extra:
        raise ValueError(f"invalid model profile {path}: missing={missing} extra={extra}")
    return {role: _profile_route(role, roles[role], path=path) for role in ROUTE_ROLE_ORDER}


def _load_model_profiles() -> dict[str, dict[str, RoleRoute]]:
    profiles: dict[str, dict[str, RoleRoute]] = {}
    for directory in (MODEL_PROFILE_DIR, LOCAL_MODEL_PROFILE_DIR):
        for path in sorted(directory.glob("*.toml")):
            profiles[path.stem] = load_model_profile_file(path)
    if not profiles:
        raise RuntimeError(f"no TheoryMaster model profiles found under {MODEL_PROFILE_DIR}")
    return profiles


def _route_tier(route: RoleRoute) -> str | None:
    model = route.model.lower()
    if "6.1" in model and "sol" in model:
        return "sol61"
    if "luna" in model:
        return "luna"
    if "terra" in model:
        return "terra"
    if "sol" in model:
        return "sol"
    if "astra" in model:
        return "astra"
    return None


def build_model_profile(profile: str, provider_profile: str | None = None) -> dict[str, RoleRoute]:
    profile = normalize_model_profile(profile)
    from .user_model_presets import effective
    routes = effective(profile)
    if provider_profile is None:
        return routes
    provider_profile = normalize_model_provider(provider_profile)
    provider_map = MODEL_PROVIDER_MAP[provider_profile]
    remapped: dict[str, RoleRoute] = {}
    for role, route in routes.items():
        # A profile already targeting the requested provider is authoritative:
        # re-mapping by model family would silently downgrade its exact model.
        if provider_profile == "codex" and route.provider in ACCOUNT_PROVIDERS:
            remapped[role] = route
            continue
        tier = _route_tier(route)
        if tier is None:
            raise ValueError(f"cannot apply provider override {provider_profile!r} to model {route.model!r}")
        mapped = provider_map.get(tier)
        remapped[role] = route if mapped is None else replace(mapped, reasoning_effort=route.reasoning_effort)
    return remapped




def model_profile_path() -> Path:
    override = (os.environ.get("THEORYMASTER_PROFILE_FILE") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "theory-master" / "model_profile"


def model_provider_path() -> Path:
    override = (os.environ.get("THEORYMASTER_MODEL_PROVIDER_FILE") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".config" / "theory-master" / "model_provider"


def normalize_model_profile(name: str) -> str:
    profile = str(name or "").strip().lower()
    profile = MODEL_PROFILE_ALIASES.get(profile, profile)
    if profile not in MODEL_PROFILES:
        allowed = ", ".join(sorted(MODEL_PROFILES))
        raise ValueError(f"unknown model profile {name!r}; choose one of: {allowed}")
    return profile


def normalize_model_provider(name: str) -> str:
    provider = str(name or "").strip().lower()
    if provider not in MODEL_PROVIDER_MAP:
        allowed = ", ".join(sorted(MODEL_PROVIDER_MAP))
        raise ValueError(f"unknown model provider {name!r}; choose one of: {allowed}")
    return provider


def read_model_profile(*, env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    from_env = str(source.get("THEORYMASTER_PROFILE") or "").strip()
    if from_env:
        return normalize_model_profile(from_env)
    path = model_profile_path()
    if path.is_file():
        return normalize_model_profile(path.read_text(encoding="utf-8").strip())
    return MODEL_PROFILE_DEFAULT


def read_model_provider(*, env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    from_env = str(source.get("THEORYMASTER_MODEL_PROVIDER") or "").strip()
    if from_env:
        return normalize_model_provider(from_env)
    path = model_provider_path()
    if path.is_file():
        return normalize_model_provider(path.read_text(encoding="utf-8").strip())
    return MODEL_PROVIDER_DEFAULT


def write_model_profile(name: str) -> Path:
    profile = normalize_model_profile(name)
    path = model_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile + "\n", encoding="utf-8")
    return path


def write_model_provider(name: str) -> Path:
    provider = normalize_model_provider(name)
    path = model_provider_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(provider + "\n", encoding="utf-8")
    return path


MODEL_PROFILES: dict[str, dict[str, RoleRoute]] = _load_model_profiles()


def parse_model_spec(spec: str, provider: str | None = None) -> RoleRoute:
    """Parse `model`, `model@Provider`, or a known alias such as `gpt-5.6-luna`."""
    text = str(spec or "").strip()
    if not text:
        raise ValueError("empty model spec")
    chosen_provider = str(provider or "").strip() or None
    if "@" in text:
        model, _, named = text.partition("@")
        model, named = model.strip(), named.strip()
        if not model or not named:
            raise ValueError(f"invalid model@provider: {spec}")
        if chosen_provider and chosen_provider != named:
            raise ValueError(f"provider conflict: {named} vs {chosen_provider}")
        return _route_for(model, named)
    if chosen_provider:
        return _route_for(text, chosen_provider)
    known = KNOWN_ROUTES.get(text)
    if known is not None:
        return known
    raise ValueError(f"unknown model {text!r}; pass MODEL@PROVIDER or --provider")


def _route_for(model: str, provider: str) -> RoleRoute:
    known = KNOWN_ROUTES.get(model)
    if known is not None and known.provider == provider:
        return known
    vendor = "openai" if provider in ACCOUNT_PROVIDERS else None
    return RoleRoute(model, provider, model_vendor=vendor)


def parse_role_spec(spec: str) -> tuple[str, RoleRoute]:
    text = str(spec or "").strip()
    role, separator, model_spec = text.partition("=")
    role, model_spec = role.strip().lower(), model_spec.strip()
    if not separator or not role or not model_spec:
        raise ValueError(f"invalid route {spec!r}; expected ROLE=MODEL or ROLE=MODEL@PROVIDER")
    if role not in ROUTE_ROLES:
        raise ValueError(f"unknown role {role!r}")
    return role, parse_model_spec(model_spec)


def _apply_role_specs(routes: dict[str, RoleRoute], specs: list[str]) -> dict[str, RoleRoute]:
    """Apply explicit route overrides for current TheoryMaster roles."""
    for role, route in (parse_role_spec(item) for item in specs):
        routes[role] = route
    return routes


TASK_ROLE_OVERRIDES_NAME = "web-role-routes.json"


def task_route_catalog() -> list[dict[str, str]]:
    """List only model/provider pairs explicitly supported by a provider map.

    A scientific model preset is not proof that a provider can serve that model;
    keep unsupported pairs out of task-local route controls and fail closed.
    """
    variants = {
        (route.model, route.provider)
        for group in MODEL_PROVIDER_MAP.values()
        for route in group.values()
    }
    return [{"model": model, "provider": provider} for model, provider in sorted(variants)]


def read_task_role_overrides(research_root: Path) -> dict[str, RoleRoute]:
    """Load operator-selected routes for future launches, never live threads."""
    path = Path(research_root) / ".theory-master" / TASK_ROLE_OVERRIDES_NAME
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16000:
        raise ValueError("invalid task-local route overrides file")
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "theory-master/web-role-routes/v1":
        raise ValueError("invalid task-local route override schema")
    selected = payload.get("roles", {})
    if not isinstance(selected, dict) or not set(selected).issubset(ROUTE_ROLES):
        raise ValueError("invalid task-local role overrides")
    allowed = {(item["model"], item["provider"]) for item in task_route_catalog()}
    result: dict[str, RoleRoute] = {}
    for role, data in selected.items():
        if not isinstance(data, dict) or set(data) not in (
                {"model", "provider", "reasoning_effort"},
                {"model", "provider", "reasoning_effort", "activation"}):
            raise ValueError("invalid task role route entry")
        model, provider, effort = data["model"], data["provider"], data["reasoning_effort"]
        if not isinstance(model, str) or not isinstance(provider, str) or (model, provider) not in allowed:
            raise ValueError("unknown task role model/provider")
        if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
            raise ValueError("invalid task role reasoning effort")
        result[role] = replace(_route_for(model, provider), reasoning_effort=effort)
        if data.get("activation", "next_invocation") not in {"next_invocation", "next_cycle"}:
            raise ValueError("invalid task role activation mode")
    return result


def read_task_role_activations(research_root: Path) -> dict[str, str]:
    """Activation timing for validated Web route overrides (v1 rows remain compatible)."""
    path = Path(research_root) / ".theory-master" / TASK_ROLE_OVERRIDES_NAME
    if not path.exists():
        return {}
    # Validation is shared with read_task_role_overrides, never trust timing
    # from a file whose role or model would be rejected by routing.
    read_task_role_overrides(research_root)
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {role: row.get("activation", "next_invocation")
            for role, row in payload.get("roles", {}).items()}


def save_task_role_override(research_root: Path, *, role: str, model: str,
                            provider: str, effort: str,
                            activation: str = "next_invocation") -> dict[str, str]:
    """Atomically save one role's next-launch model, no hot thread changes."""
    if role not in ROUTE_ROLES or not isinstance(model, str) or not isinstance(provider, str) or not isinstance(effort, str):
        raise ValueError("invalid role route selection")
    if (model, provider) not in {(entry["model"], entry["provider"]) for entry in task_route_catalog()}:
        raise ValueError("unsupported model/provider combination")
    if effort not in REASONING_EFFORTS:
        raise ValueError("unsupported reasoning effort")
    if activation not in {"next_invocation", "next_cycle"}:
        raise ValueError("unsupported model activation time")
    from .runtime import atomic
    directory = Path(research_root) / ".theory-master"
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("missing task runtime registry")
    previous = read_task_role_overrides(research_root)
    previous_modes = read_task_role_activations(research_root)
    previous[role] = replace(_route_for(model, provider), reasoning_effort=effort)
    previous_modes[role] = activation
    payload = {"schema": "theory-master/web-role-routes/v1", "roles": {
        key: {"model": route.model, "provider": route.provider, "reasoning_effort": route.reasoning_effort,
              "activation": previous_modes.get(key, "next_invocation")}
        for key, route in sorted(previous.items())
    }}
    import json
    atomic(directory / TASK_ROLE_OVERRIDES_NAME, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"role": role, "model": model, "provider": provider,
            "reasoning_effort": effort, "activation": activation}


def resolve_routes(
    *,
    model: str | None = None,
    provider: str | None = None,
    role_specs: list[str] | None = None,
    profile: str | None = None,
    env: dict[str, str] | None = None,
    task_env_file: Path | None = None,
    task_route_override_root: Path | None = None,
) -> dict[str, RoleRoute]:
    """Resolve a model profile, then overlay explicit route overrides.

    CLI beats env beats profile. `env=None` reads `~/.config/theory-master/env`, optional task-local
    `<task_root>/.theory-master/env`, then the process environment.
    Tests pass `env={}` to isolate.
    """
    if env is None:
        from .codex_backend import CodexBackend
        merged = dict(CodexBackend._env_file_values())
        if task_env_file is not None:
            merged.update(CodexBackend._read_env_file(task_env_file.expanduser()))
        merged.update(os.environ)
    else:
        merged = dict(env)
    if profile:
        selected_profile = normalize_model_profile(profile)
    elif merged.get("THEORYMASTER_PROFILE"):
        selected_profile = normalize_model_profile(str(merged["THEORYMASTER_PROFILE"]))
    elif env is None:
        selected_profile = read_model_profile()
    else:
        selected_profile = MODEL_PROFILE_DEFAULT
    selected_provider: str | None = None
    if merged.get("THEORYMASTER_MODEL_PROVIDER"):
        selected_provider = normalize_model_provider(str(merged["THEORYMASTER_MODEL_PROVIDER"]))
    elif env is None:
        provider_path = model_provider_path()
        if provider_path.is_file():
            selected_provider = read_model_provider()
    routes = build_model_profile(selected_profile, selected_provider)
    env_model = merged.get("THEORYMASTER_MODEL")
    env_provider = merged.get("THEORYMASTER_PROVIDER")
    if env_model:
        route = parse_model_spec(env_model, env_provider)
        routes = {role: route for role in routes}
    elif env_provider:
        raise ValueError("THEORYMASTER_PROVIDER requires THEORYMASTER_MODEL")
    env_role_specs = [
        part.strip() for part in str(merged.get("THEORYMASTER_ROUTE") or "").split(",") if part.strip()
    ]
    routes = _apply_role_specs(routes, env_role_specs)
    # Persisted per-task Web route choices beat base/env profiles, but explicit
    # CLI --model/--route values remain higher precedence. Existing threads
    # keep their frozen runtime route until the next Run/Resume.
    if task_route_override_root is not None:
        routes.update(read_task_role_overrides(task_route_override_root))
    if model:
        route = parse_model_spec(model, provider)
        routes = {role: route for role in routes}
    elif provider:
        raise ValueError("--provider requires --model")
    routes = _apply_role_specs(routes, list(role_specs or []))
    return routes
