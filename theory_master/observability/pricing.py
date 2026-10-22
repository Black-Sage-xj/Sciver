"""Daily official pricing snapshots for TheoryMaster cost estimation."""
from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

OPENAI_PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_SCHEMA = "theory-master/pricing/v1"
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
LONG_CONTEXT_THRESHOLD = 272_000
OPENAI_MODELS = frozenset({"gpt-6-sol", "gpt-6-luna"})


def pricing_cache_path() -> Path:
    override = (os.environ.get("THEORYMASTER_PRICING_CACHE") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "theory-master" / "pricing" / "openai-standard.json"


def _plain_row(row_html: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row_html))).strip()


def parse_openai_standard_pricing(page: str) -> dict[str, Any]:
    """Parse the rendered Standard token-price table from OpenAI's official page."""
    marker = 'data-content-switcher-pane="true" data-value="standard"'
    start = page.find(marker)
    if start < 0:
        raise ValueError("OpenAI pricing page no longer exposes the Standard pricing pane")
    # Other pricing tiers follow as sibling panes; limiting the slice prevents
    # Batch/Flex/Fast rows from being mistaken for Standard prices.
    next_pane = page.find('data-content-switcher-pane="true" data-value=', start + len(marker))
    standard = page[start: next_pane if next_pane >= 0 else len(page)]
    models: dict[str, Any] = {}
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>", standard, flags=re.I | re.S):
        text = _plain_row(row)
        model = next((name for name in OPENAI_MODELS if text.startswith(name + " ") or text == name), None)
        if model is None:
            continue
        prices = [float(value) for value in re.findall(r"\$([0-9]+(?:\.[0-9]+)?)", text)]
        if len(prices) < 4:
            continue
        short = prices[:4]
        long = prices[4:8] if len(prices) >= 8 else None
        entry: dict[str, Any] = {
            "short_context": {
                "input": short[0], "cached_input": short[1], "cache_write": short[2], "output": short[3],
            }
        }
        if long is not None:
            entry["long_context"] = {
                "input": long[0], "cached_input": long[1], "cache_write": long[2], "output": long[3],
            }
        models[model] = entry
    missing = sorted(OPENAI_MODELS - set(models))
    if missing:
        raise ValueError(f"OpenAI pricing page missing expected models: {', '.join(missing)}")
    return models


def fetch_openai_pricing(*, timeout: float = 20.0) -> dict[str, Any]:
    req = Request(OPENAI_PRICING_URL, headers={"User-Agent": "TheoryMaster-Pricing/1.0"})
    with urlopen(req, timeout=timeout) as response:
        page = response.read().decode("utf-8", "ignore")
    models = parse_openai_standard_pricing(page)
    fetched = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "schema": PRICING_SCHEMA,
        "source": "openai_official",
        "source_url": OPENAI_PRICING_URL,
        "pricing_tier": "standard",
        "currency": "USD",
        "unit": "per_1m_tokens",
        "long_context_threshold_tokens": LONG_CONTEXT_THRESHOLD,
        "fetched_at": fetched,
        "fetched_epoch": time.time(),
        "models": models,
    }


def write_pricing_cache(snapshot: dict[str, Any], path: Path | None = None) -> Path:
    target = path or pricing_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def load_pricing_cache(path: Path | None = None) -> dict[str, Any] | None:
    target = path or pricing_cache_path()
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("schema") == PRICING_SCHEMA else None


def pricing_cache_is_fresh(snapshot: dict[str, Any] | None, *, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> bool:
    if not snapshot:
        return False
    fetched = snapshot.get("fetched_epoch")
    try:
        return (time.time() - float(fetched)) < max_age_seconds
    except (TypeError, ValueError):
        return False


def refresh_pricing(*, force: bool = False, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, timeout: float = 20.0) -> tuple[dict[str, Any], bool]:
    """Refresh at most once per max_age; stale cache is retained on network failure."""
    cached = load_pricing_cache()
    if not force and pricing_cache_is_fresh(cached, max_age_seconds=max_age_seconds):
        return cached, False
    try:
        snapshot = fetch_openai_pricing(timeout=timeout)
        write_pricing_cache(snapshot)
        return snapshot, True
    except Exception:
        if cached is not None:
            return cached, False
        raise


def model_price(snapshot: dict[str, Any] | None, model: str, *, input_tokens: int = 0) -> tuple[dict[str, float], str] | None:
    if not snapshot:
        return None
    entry = (snapshot.get("models") or {}).get(str(model).lower())
    if not isinstance(entry, dict):
        return None
    threshold = int(snapshot.get("long_context_threshold_tokens") or LONG_CONTEXT_THRESHOLD)
    band = "long_context" if input_tokens > threshold and entry.get("long_context") else "short_context"
    rates = entry.get(band)
    if not isinstance(rates, dict):
        return None
    return {key: float(rates.get(key) or 0.0) for key in ("input", "cached_input", "cache_write", "output")}, band
