"""Provider-specific, auditable cost accounting for TheoryMaster runtime events."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Protocol

from .pricing import load_pricing_cache, model_price
from ..specification import list_cycle_dirs

COST_SOURCES = frozenset({"provider_reported", "provider_billing_api", "calculated", "estimated", "unavailable"})
USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "reasoning_tokens")


def unavailable_cost(*, provider: str, model_vendor: str | None, model: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"amount": None, "currency": None, "source": "unavailable", "confidence": "none", "pricing_rule_id": None,
            "pricing_effective_at": None, "provider_reported_amount": None, "provider": provider,
            "model_vendor": model_vendor, "model": model, "raw_usage": usage or {}}


class CostAdapter(Protocol):
    def assess(self, *, provider: str, model_vendor: str | None, model: str, usage: dict[str, Any], native_cost: dict[str, Any] | None) -> dict[str, Any]: ...


class NativeCostAdapter:
    """Use a request's provider-supplied charge; never guess a missing price."""
    def assess(self, *, provider: str, model_vendor: str | None, model: str, usage: dict[str, Any], native_cost: dict[str, Any] | None) -> dict[str, Any]:
        if not native_cost or native_cost.get("amount") is None:
            return unavailable_cost(provider=provider, model_vendor=model_vendor, model=model, usage=usage)
        return {"amount": float(native_cost["amount"]), "currency": str(native_cost.get("currency") or "USD"),
                "source": "provider_reported", "confidence": "exact", "pricing_rule_id": None,
                "pricing_effective_at": None, "provider_reported_amount": float(native_cost["amount"]),
                "provider": provider, "model_vendor": model_vendor, "model": model, "raw_usage": usage}


@dataclass(frozen=True)
class PricingRule:
    rule_id: str
    effective_at: str
    currency: str
    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cached_input_per_million: float = 0.0
    cache_write_per_million: float = 0.0
    reasoning_per_million: float = 0.0


class PricingRuleAdapter(NativeCostAdapter):
    """Fallback calculation for one provider/model pricing snapshot, after native billing."""
    def __init__(self, rule: PricingRule):
        self.rule = rule

    def assess(self, *, provider: str, model_vendor: str | None, model: str, usage: dict[str, Any], native_cost: dict[str, Any] | None) -> dict[str, Any]:
        native = super().assess(provider=provider, model_vendor=model_vendor, model=model, usage=usage, native_cost=native_cost)
        if native["source"] == "provider_reported":
            return native
        weights = {
            "input_tokens": self.rule.input_per_million, "output_tokens": self.rule.output_per_million,
            "cached_input_tokens": self.rule.cached_input_per_million, "cache_write_tokens": self.rule.cache_write_per_million,
        }
        if not any(key in usage for key in USAGE_KEYS):
            return native
        # Reasoning tokens are a subset of output tokens. Charging them again
        # would double count the same generated tokens.
        amount = sum(float(usage.get(key) or 0) * price / 1_000_000 for key, price in weights.items())
        return {"amount": amount, "currency": self.rule.currency, "source": "calculated", "confidence": "high",
                "pricing_rule_id": self.rule.rule_id, "pricing_effective_at": self.rule.effective_at,
                "provider_reported_amount": None, "provider": provider, "model_vendor": model_vendor,
                "model": model, "raw_usage": usage}


class OfficialOpenAIPricingAdapter(NativeCostAdapter):
    """Estimate token-equivalent USD cost from the cached official OpenAI Standard rate card."""
    ACCOUNT_PROVIDER_IDS = frozenset({"codex", "codexhttps", "chatgpt"})

    def assess(self, *, provider: str, model_vendor: str | None, model: str, usage: dict[str, Any], native_cost: dict[str, Any] | None) -> dict[str, Any]:
        native = super().assess(provider=provider, model_vendor=model_vendor, model=model, usage=usage, native_cost=native_cost)
        if native["source"] == "provider_reported":
            return native
        snapshot = load_pricing_cache()
        input_tokens = int(usage.get("input_tokens") or 0)
        priced = model_price(snapshot, model, input_tokens=input_tokens)
        if priced is None or not any(key in usage for key in USAGE_KEYS):
            return native
        rates, band = priced
        cached = min(input_tokens, max(0, int(usage.get("cached_input_tokens") or 0)))
        cache_write = min(max(0, input_tokens - cached), max(0, int(usage.get("cache_write_tokens") or 0)))
        uncached = max(0, input_tokens - cached - cache_write)
        output = max(0, int(usage.get("output_tokens") or 0))
        amount = (uncached * rates["input"] + cached * rates["cached_input"] + cache_write * rates["cache_write"] + output * rates["output"]) / 1_000_000
        fetched = str((snapshot or {}).get("fetched_at") or "")
        source = "estimated" if provider.lower() in self.ACCOUNT_PROVIDER_IDS else "calculated"
        return {
            "amount": amount, "currency": str((snapshot or {}).get("currency") or "USD"), "source": source,
            "confidence": "high" if cached or cache_write else "medium", "pricing_rule_id": f"openai-standard-{band}",
            "pricing_effective_at": fetched or None, "provider_reported_amount": None, "provider": provider,
            "model_vendor": model_vendor, "model": model, "pricing_band": band,
            "pricing_source_url": (snapshot or {}).get("source_url"), "raw_usage": usage,
        }


# Billing onboarding is explicit. Do not add a price here without a dated provider snapshot.
ADAPTERS: dict[str, CostAdapter] = {
    "deepseek": NativeCostAdapter(),
    "codex": OfficialOpenAIPricingAdapter(),
    "codexhttps": OfficialOpenAIPricingAdapter(),
    "chatgpt": OfficialOpenAIPricingAdapter(),
    "openai": OfficialOpenAIPricingAdapter(),
}


def assess_cost(*, provider: str | None, model_vendor: str | None, model: str | None, usage: dict[str, Any] | None, native_cost: dict[str, Any] | None) -> dict[str, Any]:
    provider_id = str(provider or "unknown").strip().lower()
    model_id = str(model or "unknown")
    normalized = {key: int(value) for key, value in (usage or {}).items() if key in USAGE_KEYS and value is not None}
    if isinstance((usage or {}).get("provider_specific_usage"), dict):
        normalized["provider_specific_usage"] = dict((usage or {})["provider_specific_usage"])
    adapter = ADAPTERS.get(provider_id)
    if adapter is None:
        return unavailable_cost(provider=provider_id, model_vendor=model_vendor, model=model_id, usage=normalized)
    return adapter.assess(provider=provider_id, model_vendor=model_vendor, model=model_id, usage=normalized, native_cost=native_cost)


def codex_thread_turn_usage_records(
    thread_ids: list[str], *, sessions_root: Path | None = None,
) -> list[tuple[str, str | None, dict[str, int]]]:
    """Recover final usage keyed by native thread and completed turn IDs."""
    if sessions_root is None:
        from theory_master.codex_accounts import ensure_layout
        codex_home = ensure_layout()["runtime"]
        root = codex_home / "sessions"
    else:
        root = sessions_root
    if not root.is_dir():
        return []
    recovered: list[tuple[str, str | None, dict[str, int]]] = []
    for thread_id in dict.fromkeys(str(item) for item in thread_ids if item):
        matches = sorted(root.rglob(f"*{thread_id}.jsonl"))
        if not matches:
            continue
        latest: dict[str, int] | None = None
        for line in matches[-1].read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") if isinstance(event, dict) else None
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "token_count":
                info = payload.get("info") or {}
                raw = info.get("last_token_usage") or {}
                if isinstance(raw, dict):
                    latest = {
                        "input_tokens": int(raw.get("input_tokens") or 0),
                        "output_tokens": int(raw.get("output_tokens") or 0),
                        "cached_input_tokens": int(raw.get("cached_input_tokens") or 0),
                        "cache_write_tokens": int(raw.get("cache_write_input_tokens") or 0),
                        "reasoning_tokens": int(raw.get("reasoning_output_tokens") or 0),
                    }
            elif payload.get("type") == "task_complete" and latest is not None:
                recovered.append((thread_id, str(payload.get("turn_id") or "") or None, dict(latest)))
                latest = None
            elif payload.get("type") == "turn_aborted":
                latest = None
    return recovered


def codex_thread_turn_usages(thread_id: str, *, sessions_root: Path | None = None) -> list[dict[str, int]]:
    """Recover final per-turn token usage from a persisted native Codex thread."""
    return [usage for _thread, _turn, usage in codex_thread_turn_usage_records([thread_id], sessions_root=sessions_root)]


def codex_turn_usage(thread_id: str, turn_id: str, *, sessions_root: Path | None = None) -> dict[str, int] | None:
    """Find one completed turn's exact usage in its native Codex session."""
    for _thread, recovered_turn_id, usage in codex_thread_turn_usage_records([thread_id], sessions_root=sessions_root):
        if recovered_turn_id == turn_id:
            return usage
    return None


def backfill_run_state_costs(run_state_path: Path, *, sessions_root: Path | None = None) -> dict[str, Any]:
    """Fill missing request usage/prices from native Codex turn history."""
    state = json.loads(run_state_path.read_text(encoding="utf-8"))
    original_state = json.loads(json.dumps(state))
    changed = 0
    completed_turns: dict[str, list[tuple[str, str]]] = {}
    events_path = run_state_path.with_name("runtime_events.jsonl")
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "LLM_TURN_COMPLETED":
                continue
            role = str(event.get("role") or event.get("agent") or "")
            thread_id, turn_id = event.get("thread_id"), event.get("turn_id")
            if role and thread_id and turn_id:
                completed_turns.setdefault(role, []).append((str(thread_id), str(turn_id)))

    cost_agents = {**(state.get("agents") or {}), **(state.get("auxiliary_agents") or {})}
    for role, agent in cost_agents.items():
        if not isinstance(agent, dict):
            continue
        existing = list(agent.get("cost_records") or [])
        turns = completed_turns.get(role, [])
        recovered = codex_thread_turn_usage_records([thread for thread, _turn in turns], sessions_root=sessions_root)
        usage_by_turn = {(thread, turn): usage for thread, turn, usage in recovered if turn}

        # runtime_events are the authoritative request order; a per-role record
        # is only patched when the exact (thread_id, turn_id) match is present.
        # Legacy states without those events may still use the old one-thread,
        # exact-length alignment, but never a partial or latest-N guess.
        aligned_usages: list[dict[str, int] | None]
        if turns and len(turns) == len(existing):
            aligned_usages = [usage_by_turn.get(key) for key in turns]
        elif not turns and existing and agent.get("thread_id"):
            legacy = codex_thread_turn_usages(str(agent["thread_id"]), sessions_root=sessions_root)
            aligned_usages = legacy if len(legacy) == len(existing) else [None] * len(existing)
        else:
            aligned_usages = [None] * len(existing)

        records = []
        for record, usage in zip(existing, aligned_usages):
            if usage and (record.get("amount") is None or not record.get("raw_usage")):
                if record.get("amount") is None:
                    record = assess_cost(
                        provider=agent.get("provider"), model_vendor=agent.get("model_vendor"),
                        model=agent.get("model"), usage=usage, native_cost=None,
                    )
                else:
                    record = {**record, "raw_usage": usage}
                changed += 1
            records.append(record)
        agent["cost_records"] = records
        agent["cost"] = aggregate_cost(records)
        agent_usage = aggregate_usage(records)
        if agent_usage:
            agent.update({
                "tokens_in": agent_usage["input_tokens"], "tokens_out": agent_usage["output_tokens"],
                "tokens_total": agent_usage["total_tokens"], "cached_input_tokens": agent_usage["cached_input_tokens"],
                "uncached_input_tokens": agent_usage["uncached_input_tokens"], "reasoning_tokens": agent_usage["reasoning_tokens"],
            })

        project = (state.get("project_agents") or {}).get(role)
        if isinstance(project, dict):
            project_records = list(project.get("cost_records") or [])
            if len(project_records) >= len(records):
                start = len(project_records) - len(records)
                for offset, record in enumerate(records):
                    if project_records[start + offset].get("amount") is None and record.get("amount") is not None:
                        project_records[start + offset] = record
                project["cost_records"] = project_records
                project["cost"] = aggregate_cost(project_records)
            elif not project_records:
                project["cost_records"] = list(records)
                project["cost"] = aggregate_cost(records)
                project_records = list(records)
            project_usage = aggregate_usage(project_records)
            if project_usage:
                project.update({
                    "tokens_in": project_usage["input_tokens"], "tokens_out": project_usage["output_tokens"],
                    "tokens_total": project_usage["total_tokens"], "cached_input_tokens": project_usage["cached_input_tokens"],
                    "uncached_input_tokens": project_usage["uncached_input_tokens"], "reasoning_tokens": project_usage["reasoning_tokens"],
                })

    all_cycle_records = [record for agent in cost_agents.values() for record in (agent.get("cost_records") or [])]
    state["cost"] = aggregate_cost(all_cycle_records)
    state["cycle_cost"] = state["cost"]
    project_records = [record for agent in (state.get("project_agents") or {}).values() for record in (agent.get("cost_records") or [])]
    if project_records:
        state["project_cost"] = aggregate_cost(project_records)
    project_usage = aggregate_usage(project_records or all_cycle_records)
    if project_usage:
        state.update({
            "project_tokens_in": project_usage["input_tokens"], "project_tokens_out": project_usage["output_tokens"],
            "project_tokens_total": project_usage["total_tokens"],
            "project_cached_input_tokens": project_usage["cached_input_tokens"],
            "project_uncached_input_tokens": project_usage["uncached_input_tokens"],
            "project_reasoning_tokens": project_usage["reasoning_tokens"],
        })
    cycle_usage = aggregate_usage(all_cycle_records)
    if cycle_usage:
        state.update({
            "tokens_in": cycle_usage["input_tokens"], "tokens_out": cycle_usage["output_tokens"],
            "tokens_total": cycle_usage["total_tokens"], "cached_input_tokens": cycle_usage["cached_input_tokens"],
            "uncached_input_tokens": cycle_usage["uncached_input_tokens"], "reasoning_tokens": cycle_usage["reasoning_tokens"],
            "cycle_tokens_in": cycle_usage["input_tokens"], "cycle_tokens_out": cycle_usage["output_tokens"],
            "cycle_tokens_total": cycle_usage["total_tokens"],
        })
    if state != original_state:
        temp = run_state_path.with_suffix(run_state_path.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(run_state_path)
    return {"path": str(run_state_path), "changed_records": changed, "cost": state.get("cost"), "project_cost": state.get("project_cost")}


def backfill_research_costs(research_root: Path, *, sessions_root: Path | None = None) -> dict[str, Any]:
    results = []
    for cycle_dir in reversed(list_cycle_dirs(Path(research_root), task_mode=True)):
        path = cycle_dir / "run_state.json"
        if path.is_file():
            results.append(backfill_run_state_costs(path, sessions_root=sessions_root))
    return {"research_root": str(research_root), "cycles": results, "changed_records": sum(int(item["changed_records"]) for item in results)}


def aggregate_cost(records: list[dict[str, Any]]) -> dict[str, Any]:
    known = [item for item in records if item.get("amount") is not None and item.get("currency")]
    currencies = {str(item["currency"]) for item in known}
    if not known or len(currencies) != 1:
        return {"amount": None, "currency": None, "source": "unavailable", "confidence": "none", "priced_model_calls": len(known), "model_calls": len(records)}
    sources = {str(item.get("source")) for item in known}
    source = sources.pop() if len(sources) == 1 else "estimated"
    return {"amount": sum(float(item["amount"]) for item in known), "currency": currencies.pop(), "source": source,
            "confidence": "partial" if len(known) != len(records) else "aggregated", "priced_model_calls": len(known), "model_calls": len(records)}


def aggregate_usage(records: list[dict[str, Any]]) -> dict[str, int] | None:
    """Sum completed request usage; reasoning is reported but remains inside output."""
    totals = {key: 0 for key in USAGE_KEYS}
    found = False
    for record in records:
        usage = record.get("raw_usage") if isinstance(record, dict) else None
        if not isinstance(usage, dict):
            continue
        present = [key for key in USAGE_KEYS if usage.get(key) is not None]
        if not present:
            continue
        found = True
        for key in present:
            try:
                totals[key] += max(0, int(usage[key]))
            except (TypeError, ValueError):
                continue
    if not found:
        return None
    result = dict(totals)
    result["uncached_input_tokens"] = max(0, result["input_tokens"] - result["cached_input_tokens"])
    # Output already includes reasoning_tokens; do not add reasoning again.
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result
