"""Cost estimates for billable provider calls.

APPROXIMATE PUBLIC LIST PRICES (Gemini/OpenAI as of 2025, Anthropic Claude as of September 2026) —
VERIFY AND OVERRIDE VIA PRICING_FILE. They exist to enforce budgets and show teachers what a lecture
costs; they are not invoices.

``PRICING_FILE`` (JSON) is deep-merged over ``DEFAULT_PRICES`` and may override any entry, e.g.::

    {"llm": {"gemini-2.5-flash": {"input": 0.30, "output": 2.50}},
     "tts": {"elevenlabs": {"default": 200.0}},
     "image": {"gemini": {"default": 0.039}}}

Lookup rules:

* ``llm`` / ``vision``: by model family = the longest table key that prefixes the model name
  (``gemini-2.5-flash-001`` -> ``gemini-2.5-flash``), regardless of provider (the fake provider
  reports real model names, so dev/e2e budgets behave realistically); unknown models fall back to
  the provider default, then to the global default, with a warning logged once per model. The
  Claude default is the highest Claude list price, so an unlisted model is over-, not under-costed.
* Prompt caching (Anthropic): when ``Usage.meta`` carries ``cache_read_tokens`` /
  ``cache_write_tokens`` (both included in ``input_tokens``), cache writes cost 1.25x and cache
  reads 0.10x the input price, unless the model entry sets its own ``cache_write`` /
  ``cache_read`` price (USD per 1M tokens). OpenAI's ``cached_tokens`` meta is not discounted.
* ``tts``: per provider, then model family within it; ``image``: per image; ``video``: per second.
* Provider ``fake`` with an unknown model costs 0; ``edge`` TTS and ``pollinations`` images are free.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..providers.base import Usage

__all__ = ["DEFAULT_PRICES", "estimate_cost", "load_prices", "reset_price_cache"]

log = logging.getLogger(__name__)

PER_MILLION = 1_000_000.0
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute prompt-cache write, relative to the input price
CACHE_READ_MULTIPLIER = 0.10  # prompt-cache hit, relative to the input price

# APPROXIMATE PUBLIC LIST PRICES (Gemini/OpenAI 2025, Claude September 2026) — VERIFY AND OVERRIDE VIA PRICING_FILE.
DEFAULT_PRICES: dict[str, Any] = {
    # USD per 1M tokens, keyed by model family (longest prefix match).
    "llm": {
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
        "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
        "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "gpt-5-nano": {"input": 0.05, "output": 0.40},
        "gpt-5-mini": {"input": 0.25, "output": 2.00},
        "gpt-5": {"input": 1.25, "output": 10.00},
        "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
        "gpt-4.1": {"input": 2.00, "output": 8.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "o4-mini": {"input": 1.10, "output": 4.40},
        "o3": {"input": 2.00, "output": 8.00},
        # Anthropic Claude ("cache_read": documented cache-hit price where it is not 0.10x input).
        "claude-fable-5-1": {"input": 10.00, "output": 50.00, "cache_read": 0.25},
        "claude-fable-5": {"input": 10.00, "output": 50.00},
        "claude-mythos-5": {"input": 10.00, "output": 50.00},
        "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
        "claude-opus-5": {"input": 5.00, "output": 25.00},
        "claude-opus-4-8": {"input": 5.00, "output": 25.00},
        "claude-opus-4-7": {"input": 5.00, "output": 25.00},
        "claude-opus-4-6": {"input": 5.00, "output": 25.00},
        "claude-opus-4-5": {"input": 5.00, "output": 25.00},
        "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
        "claude-sonnet-5": {"input": 2.00, "output": 10.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        # Older Claude families still served (dated ids and aliases match by prefix; the 4-5+ keys
        # above are longer, so they win for current models).
        "claude-opus-4": {"input": 15.00, "output": 75.00},  # Opus 4 / 4.1 (claude-opus-4-0, -4-1, dated ids)
        "claude-sonnet-4": {"input": 3.00, "output": 15.00},  # Sonnet 4 (claude-sonnet-4-0, -4-20250514)
        "claude-3-opus": {"input": 15.00, "output": 75.00},
        "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
        "claude-3-haiku": {"input": 0.25, "output": 1.25},
    },
    # Provider fallbacks for unknown LLM models (USD per 1M tokens). The Claude fallback is the most
    # expensive Claude price, so budgets over-estimate (never under-estimate) an unlisted model.
    "llm_provider_default": {
        "gemini": {"input": 0.30, "output": 2.50},
        "openai": {"input": 2.50, "output": 10.00},
        "anthropic": {"input": 15.00, "output": 75.00},
        "fake": {"input": 0.0, "output": 0.0},
        "default": {"input": 1.25, "output": 10.00},
    },
    # USD per 1M input characters: provider -> {model family | "default": price}.
    "tts": {
        "edge": {"default": 0.0},
        "fake": {"default": 0.0},
        "openai": {"tts-1-hd": 30.0, "tts-1": 15.0, "gpt-4o-mini-tts": 12.0, "default": 15.0},
        "elevenlabs": {"eleven_flash": 150.0, "eleven_turbo": 150.0, "default": 300.0},
        "gemini": {"gemini-2.5-pro": 32.0, "default": 16.0},  # audio-token priced; ~25 tokens/s of speech
        "default": {"default": 30.0},
    },
    # USD per generated image.
    "image": {
        "gemini": {"default": 0.039},
        "openai": {"default": 0.04},
        "pollinations": {"default": 0.0},
        "fake": {"default": 0.0},
        "none": {"default": 0.0},
        "default": {"default": 0.04},
    },
    # USD per generated second of video.
    "video": {
        "veo": {"veo-3.0-fast": 0.40, "veo-3": 0.75, "veo-2": 0.50, "default": 0.50},
        "fake": {"default": 0.0},
        "none": {"default": 0.0},
        "default": {"default": 0.75},
    },
    # USD per call (GIPHY search is free).
    "gif": {"default": {"default": 0.0}},
}

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "prices": None}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_prices(settings: Settings | None = None) -> dict[str, Any]:
    """Effective price table (defaults merged with ``PRICING_FILE``; cached by path + mtime)."""
    settings = settings or get_settings()
    path: Path | None = settings.pricing_file
    key: tuple[str, float] | None = None
    if path is not None:
        try:
            key = (str(Path(path).resolve()), Path(path).stat().st_mtime)
        except OSError:
            log.warning("PRICING_FILE %s is not readable; using default prices", path)
            key = None
    with _cache_lock:
        if _cache["prices"] is not None and _cache["key"] == key:
            return _cache["prices"]
    prices = DEFAULT_PRICES
    if key is not None and path is not None:
        try:
            override = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(override, dict):
                raise TypeError("pricing file must contain a JSON object")
            prices = _deep_merge(DEFAULT_PRICES, override)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("ignoring invalid PRICING_FILE %s: %s", path, exc)
            prices = DEFAULT_PRICES
    with _cache_lock:
        _cache["key"], _cache["prices"] = key, prices
    return prices


def reset_price_cache() -> None:
    """Forget the cached price table (tests)."""
    with _cache_lock:
        _cache["key"], _cache["prices"] = None, None


def _family(table: dict[str, Any], model: str) -> Any | None:
    """Value of the longest key in ``table`` that prefixes ``model`` (case-insensitive)."""
    m = (model or "").strip().lower()
    m = m.removeprefix("models/")
    best: str | None = None
    for k in table:
        if k != "default" and m.startswith(k.lower()) and (best is None or len(k) > len(best)):
            best = k
    return table[best] if best is not None else None


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f > 0 else 0.0


def _per_provider(prices: dict[str, Any], section: str, provider: str, model: str) -> float:
    table = prices.get(section) or {}
    entry = table.get((provider or "").lower())
    if not isinstance(entry, dict):
        entry = table.get("default") or {}
    found = _family(entry, model)
    return _num(found if found is not None else entry.get("default", 0.0))


_unpriced_models: set[tuple[str, str]] = set()


def _llm_rate(prices: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    rate = _family(prices.get("llm") or {}, model)
    if not isinstance(rate, dict):
        defaults = prices.get("llm_provider_default") or {}
        rate = defaults.get((provider or "").lower()) or defaults.get("default") or {}
        key = ((provider or "").lower(), model or "")
        if key[0] != "fake" and key not in _unpriced_models:  # warn once per model and process
            _unpriced_models.add(key)
            log.warning("no list price for %s model %r; using the provider default %s (add it to PRICING_FILE)",
                        key[0] or "unknown", model, rate)
    return rate


def _llm_cost(usage: Usage, rate: dict[str, Any]) -> float:
    """Token cost; prompt-cache reads/writes (Anthropic meta) are priced separately from uncached input."""
    p_in, p_out = _num(rate.get("input")), _num(rate.get("output"))
    input_tokens = max(usage.input_tokens, 0)
    meta = usage.meta or {}
    read = min(int(_num(meta.get("cache_read_tokens"))), input_tokens)
    write = min(int(_num(meta.get("cache_write_tokens"))), input_tokens - read)
    p_read = _num(rate["cache_read"]) if "cache_read" in rate else p_in * CACHE_READ_MULTIPLIER
    p_write = _num(rate["cache_write"]) if "cache_write" in rate else p_in * CACHE_WRITE_MULTIPLIER
    cost = (input_tokens - read - write) * p_in / PER_MILLION + max(usage.output_tokens, 0) * p_out / PER_MILLION
    if read or write:
        cost += (write * p_write + read * p_read) / PER_MILLION
    return cost


def estimate_cost(usage: Usage, settings: Settings | None = None) -> float:
    """Estimated USD cost of one provider call (never negative)."""
    prices = load_prices(settings)
    op = (usage.operation or "").lower()
    provider = (usage.provider or "").lower()
    model = usage.model or ""
    if op in ("llm", "vision"):
        cost = _llm_cost(usage, _llm_rate(prices, provider, model))
    elif op == "tts":
        cost = max(usage.characters, 0) * _per_provider(prices, "tts", provider, model) / PER_MILLION
    elif op == "image":
        cost = max(usage.units, 1) * _per_provider(prices, "image", provider, model)
    elif op == "video":
        cost = max(usage.seconds, 0.0) * _per_provider(prices, "video", provider, model)
    elif op == "gif":
        cost = max(usage.units, 1) * _per_provider(prices, "gif", provider, model)
    else:
        log.debug("no pricing for operation %r", op)
        cost = 0.0
    return round(max(cost, 0.0), 8)
