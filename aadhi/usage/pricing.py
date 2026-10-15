"""Cost estimates for billable provider calls.

APPROXIMATE PUBLIC LIST PRICES (Gemini, OpenAI and Veo as of October 2026 for the current models,
older entries 2025; Anthropic Claude as of September 2026) — VERIFY AND OVERRIDE VIA PRICING_FILE.
They exist to enforce budgets and show teachers what a lecture costs; they are not invoices.

``PRICING_FILE`` (JSON) is deep-merged over ``DEFAULT_PRICES`` and may override any entry, e.g.::

    {"llm": {"gemini-3.8-flash": {"input": 0.75, "output": 3.75}},
     "tts": {"elevenlabs": {"default": 200.0}},
     "image": {"gemini": {"default": 0.039}},
     "image_tokens": {"gemini-nano-banana-2.1": {"input": 1.50, "output": 7.50}}}

Announced list-price changes are kept in ``DEFAULT_PRICES["scheduled"]``: from its ``from`` date (UTC)
each step's ``prices`` are deep-merged over the table (Google doubles the Gemini 3.6-3.8 Flash and 3.8
TTS prices on 2027-01-01). Values in ``PRICING_FILE`` always win over the schedule; a ``"scheduled"``
list there replaces the built-in one (``[]`` turns the steps off).

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
* ``tts``: per provider, then model family within it, in USD per 1M characters (audio-token priced
  models are converted at 25 audio tokens per second of speech, about 940 characters per minute);
  ``image``: per image (1K output), plus prompt and thinking tokens (``Usage.meta["thoughts_tokens"]``) at
  the ``image_tokens`` rates (USD per 1M tokens, by model family) for Gemini image models: Gemini 3 image
  models always think, and Google bills that on top of the image;
  ``video``: per second (720p, the resolution the Veo and Gemini Omni adapters request; Omni's per-token price is
  converted at its 5,792 video tokens per second);
  ``music``: per provider and model family, ``{"unit": USD per generated song or clip, "second": USD per second
  of music}`` (Lyria bills per song, ElevenLabs Music per minute); a plain number is a per-unit price.
* Provider ``fake`` with an unknown model costs 0; ``edge`` TTS, ``pollinations`` images and ``procedural``
  music are free.
* Unknown operations fail closed: every quantity the usage reports (tokens, characters, units — at least one —
  and seconds) is charged at the global default rate of its kind (the ``"default"`` entries of ``llm_provider_default``,
  ``tts``, ``image`` and ``video``) and a warning is logged once per operation, so a new paid call that nobody
  priced never passes the budget as free. Only the ``fake`` provider stays free.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..providers.base import Usage

__all__ = ["DEFAULT_PRICES", "estimate_cost", "load_prices", "reset_price_cache"]

log = logging.getLogger(__name__)

PER_MILLION = 1_000_000.0
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute prompt-cache write, relative to the input price
CACHE_READ_MULTIPLIER = 0.10  # prompt-cache hit, relative to the input price

# Gemini Omni video (aadhi/providers/video/omni.py; provider "omni"): gemini-omni-1.1-flash, the replacement of the
# Veo 3.1 previews, is billed per token on the paid tier only (pricing page, updated 2026-10-09): USD 1.50 per 1M
# input tokens, 9.00 per 1M text and 17.50 per 1M video output tokens, at 5,792 video tokens per second of 720p
# video. The adapter reports Usage.seconds = video output tokens / 5,792, so this per-second price (17.50 x 5,792 /
# 1M) matches Google's charge for any clip length; the prompt's input tokens are not priced (well under 1% of a
# clip). gemini-omni-flash-preview has the same prices.
OMNI_VIDEO_USD_PER_MILLION_TOKENS = 17.50
OMNI_VIDEO_TOKENS_PER_SECOND = 5792
_OMNI_PER_SECOND = round(OMNI_VIDEO_USD_PER_MILLION_TOKENS * OMNI_VIDEO_TOKENS_PER_SECOND / PER_MILLION, 5)

# APPROXIMATE PUBLIC LIST PRICES (Gemini/OpenAI October 2026, older entries 2025, Claude September 2026)
# — VERIFY AND OVERRIDE VIA PRICING_FILE. Sources: ai.google.dev/gemini-api/docs/pricing (updated
# 2026-10-09) and developers.openai.com/api/docs/pricing (read 2026-10-10).
DEFAULT_PRICES: dict[str, Any] = {
    # USD per 1M tokens, keyed by model family (longest prefix match).
    "llm": {
        # Gemini 3 (standard tier; the 3.6-3.8 Flash prices double on 2027-01-01, see "scheduled").
        "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
        "gemini-3.7-flash": {"input": 0.75, "output": 3.75},
        "gemini-3.6-flash": {"input": 0.75, "output": 3.75},
        "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
        "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
        "gemini-3.1-pro": {"input": 2.00, "output": 12.00},  # prompts up to 200k tokens ($4 / $18 above)
        "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
        "gemini-3-flash": {"input": 0.50, "output": 3.00},
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
        # Current OpenAI reasoning models (prompts up to 272k tokens; long prompts cost more).
        "gpt-6-astra": {"input": 10.00, "output": 50.00},
        "gpt-6.1-sol": {"input": 2.00, "output": 10.00},
        "gpt-6-sol": {"input": 2.00, "output": 10.00},
        "gpt-6-luna": {"input": 0.10, "output": 0.50},
        "gpt-5.6-cyber": {"input": 12.50, "output": 75.00},
        "gpt-5.6-sol": {"input": 4.00, "output": 20.00},
        "gpt-5.6-terra": {"input": 2.00, "output": 12.00},
        "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
        "gpt-5.6": {"input": 4.00, "output": 20.00},  # the gpt-5.6 alias routes to gpt-5.6-sol
        "gpt-5.5": {"input": 5.00, "output": 30.00},
        "gpt-5.4": {"input": 2.50, "output": 15.00},
        "gpt-5.2": {"input": 1.75, "output": 14.00},
        "gpt-5.1": {"input": 1.25, "output": 10.00},
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
        "gemini": {"input": 0.75, "output": 3.75},  # the default Gemini model's price (gemini-3.8-flash)
        "openai": {"input": 2.50, "output": 10.00},
        "anthropic": {"input": 15.00, "output": 75.00},
        "fake": {"input": 0.0, "output": 0.0},
        "default": {"input": 1.25, "output": 10.00},
    },
    # USD per 1M input characters: provider -> {model family | "default": price}.
    "tts": {
        "edge": {"default": 0.0},
        "fake": {"default": 0.0},
        # gpt-4o-mini-tts: $0.60 / 1M text tokens + $12 / 1M audio tokens, about $0.015 per minute of speech.
        "openai": {"tts-1-hd": 30.0, "tts-1": 15.0, "gpt-4o-mini-tts": 16.0, "default": 15.0},
        # ElevenLabs API list prices (elevenlabs.io/pricing/api, read 2026-10-10; the same per-character rate on
        # every plan): v4 / v3 / Multilingual v2 $0.08 per 1K characters; v4 Turbo, v3 Conversational and
        # Flash / Turbo $0.04. The v4 and v4 Turbo promotion ($0.022 / $0.011 until 12 Oct 2026) is not used, so
        # budgets never under-estimate. Unlisted models: the higher price.
        "elevenlabs": {"eleven_v4_turbo": 40.0, "eleven_v4": 80.0, "eleven_v3_conversational": 40.0,
                       "eleven_v3": 80.0, "eleven_multilingual": 80.0, "eleven_flash": 40.0, "eleven_turbo": 40.0,
                       "default": 80.0},
        # Audio-token priced (25 tokens/s of speech): 3.8 Flash TTS $0.50 text + $9 audio per 1M tokens,
        # 3.8 Flash-Lite TTS $0.50 + $6 (both double on 2027-01-01); 2.5 Flash TTS $10, 2.5 Pro and 3.1 TTS $20.
        "gemini": {"gemini-3.8-flash-lite-tts": 9.7, "gemini-3.8-flash-tts": 14.5, "gemini-3.1-flash-tts": 32.0,
                   "gemini-2.5-pro": 32.0, "default": 16.0},
        "default": {"default": 30.0},
    },
    # USD per generated image (1K output).
    "image": {
        "gemini": {"gemini-nano-banana-2.1": 0.0336, "gemini-3.1-flash-lite-image": 0.0336,
                   "gemini-3.1-flash-image": 0.067, "gemini-3-pro-image": 0.134, "gemini-2.5-flash-image": 0.039,
                   "default": 0.039},
        "openai": {"default": 0.04},
        "pollinations": {"default": 0.0},
        "fake": {"default": 0.0},
        "none": {"default": 0.0},
        "default": {"default": 0.04},
    },
    # USD per 1M tokens of Gemini image models' prompt ("input") and thinking ("output"), billed on top of the
    # per-image price, keyed by model family (longest prefix match).
    "image_tokens": {
        "gemini-nano-banana-2.1": {"input": 1.50, "output": 7.50},
        "gemini-3.1-flash-lite-image": {"input": 0.25, "output": 1.50},
        "gemini-3.1-flash-image": {"input": 0.50, "output": 3.00},
        "gemini-3-pro-image": {"input": 2.00, "output": 12.00},
        "gemini-2.5-flash-image": {"input": 0.30, "output": 2.50},
    },
    # USD per generated second of video (Veo 3.1 at 720p, with its audio track; Gemini Omni from its token price).
    "video": {
        "veo": {"veo-3.1-lite": 0.05, "veo-3.1-fast": 0.10, "veo-3.1": 0.40, "veo-3.0-fast": 0.40, "veo-3": 0.75,
                "veo-2": 0.50, "default": 0.50},
        "omni": {"gemini-omni": _OMNI_PER_SECOND, "default": _OMNI_PER_SECOND},
        "fake": {"default": 0.0},
        "none": {"default": 0.0},
        "default": {"default": 0.75},
    },
    # USD per call (GIPHY search is free).
    "gif": {"default": {"default": 0.0}},
    # Generated music: {"unit": USD per song or clip, "second": USD per second of music} by provider, then model
    # family. Lyria (ai.google.dev/gemini-api/docs/pricing, updated 2026-10-09): lyria-3.5 and lyria-3-pro $0.08 per
    # song, lyria-3-clip $0.04 per 30 s clip; it runs on the Gemini key, so a Lyria adapter reporting the "gemini"
    # provider is priced the same. ElevenLabs Music (elevenlabs.io/pricing/api, read 2026-10-10): $0.15 per minute.
    # Unknown music providers are charged both rates (fail closed).
    "music": {
        "lyria": {"lyria-3-clip": {"unit": 0.04}, "lyria-3-pro": {"unit": 0.08}, "lyria-3.5": {"unit": 0.08},
                  "default": {"unit": 0.08}},
        "gemini": {"lyria-3-clip": {"unit": 0.04}, "lyria-3-pro": {"unit": 0.08}, "lyria-3.5": {"unit": 0.08},
                   "default": {"unit": 0.08}},
        "elevenlabs": {"default": {"second": 0.0025}},
        "procedural": {"default": 0.0},
        "fake": {"default": 0.0},
        "default": {"default": {"unit": 0.08, "second": 0.0025}},
    },
    # Announced list-price changes, applied from "from" (UTC date) on: deep-merged over the sections above.
    "scheduled": [
        {
            "from": "2027-01-01",
            "prices": {
                "llm": {
                    "gemini-3.8-flash": {"input": 1.50, "output": 7.50},
                    "gemini-3.7-flash": {"input": 1.50, "output": 7.50},
                    "gemini-3.6-flash": {"input": 1.50, "output": 7.50},
                },
                "llm_provider_default": {"gemini": {"input": 1.50, "output": 7.50}},
                "tts": {"gemini": {"gemini-3.8-flash-lite-tts": 19.5, "gemini-3.8-flash-tts": 29.1}},
            },
        },
    ],
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


def _due_steps(schedule: Any, on: date) -> list[dict[str, Any]]:
    """``prices`` of the scheduled changes in effect on ``on``, oldest first (malformed steps are skipped)."""
    due: list[tuple[date, dict[str, Any]]] = []
    for step in schedule if isinstance(schedule, list) else []:
        try:
            start, prices = date.fromisoformat(str(step["from"])), step["prices"]
        except (KeyError, TypeError, ValueError):
            log.warning("ignoring malformed scheduled price change %r", step)
            continue
        if isinstance(prices, dict) and start <= on:
            due.append((start, prices))
    return [prices for _, prices in sorted(due, key=lambda item: item[0])]


def load_prices(settings: Settings | None = None, *, on: date | None = None) -> dict[str, Any]:
    """Effective price table on ``on`` (default: today, UTC): the defaults, then the scheduled changes due
    by then, then ``PRICING_FILE``. Cached by path + mtime + day."""
    settings = settings or get_settings()
    day = on or datetime.now(UTC).date()
    path: Path | None = settings.pricing_file
    file_key: tuple[str, float] | None = None
    if path is not None:
        try:
            file_key = (str(Path(path).resolve()), Path(path).stat().st_mtime)
        except OSError:
            log.warning("PRICING_FILE %s is not readable; using default prices", path)
            file_key = None
    key = (file_key, day.isoformat())
    with _cache_lock:
        if _cache["prices"] is not None and _cache["key"] == key:
            return _cache["prices"]
    override: dict[str, Any] = {}
    if file_key is not None and path is not None:
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("pricing file must contain a JSON object")
            override = loaded
        except (OSError, ValueError, TypeError) as exc:
            log.warning("ignoring invalid PRICING_FILE %s: %s", path, exc)
    prices = DEFAULT_PRICES
    for step in _due_steps(override.get("scheduled", DEFAULT_PRICES["scheduled"]), day):
        prices = _deep_merge(prices, step)
    if override:
        prices = _deep_merge(prices, override)
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


def estimate_cost(usage: Usage, settings: Settings | None = None, *, on: date | None = None) -> float:
    """Estimated USD cost of one provider call (never negative) at the list prices of ``on`` (default today)."""
    prices = load_prices(settings, on=on)
    op = (usage.operation or "").lower()
    provider = (usage.provider or "").lower()
    model = usage.model or ""
    if op in ("llm", "vision"):
        cost = _llm_cost(usage, _llm_rate(prices, provider, model))
    elif op == "tts":
        cost = max(usage.characters, 0) * _per_provider(prices, "tts", provider, model) / PER_MILLION
    elif op == "image":
        cost = max(usage.units, 1) * _per_provider(prices, "image", provider, model)
        rate = _family(prices.get("image_tokens") or {}, model)
        if isinstance(rate, dict):  # Gemini image models also bill the prompt and the thinking tokens
            # not ``output_tokens``: those include the image's own tokens, which the per-image price covers
            thoughts = int(_num((usage.meta or {}).get("thoughts_tokens")))
            cost += (max(usage.input_tokens, 0) * _num(rate.get("input"))
                     + thoughts * _num(rate.get("output"))) / PER_MILLION
    elif op == "video":
        cost = max(usage.seconds, 0.0) * _per_provider(prices, "video", provider, model)
    elif op == "gif":
        cost = max(usage.units, 1) * _per_provider(prices, "gif", provider, model)
    elif op == "music":
        cost = _music_cost(usage, _music_rate(prices, provider, model))
    else:
        cost = _unknown_operation_cost(usage, prices, op, provider)
    return round(max(cost, 0.0), 8)


def _music_rate(prices: dict[str, Any], provider: str, model: str) -> dict[str, float]:
    """``{"unit", "second"}`` music prices of ``provider`` / ``model`` (a plain number is a per-unit price)."""
    table = prices.get("music") or {}
    entry = table.get(provider)
    if not isinstance(entry, dict):
        entry = table.get("default") or {}
    found = _family(entry, model)
    rate = found if found is not None else entry.get("default", 0.0)
    if isinstance(rate, dict):
        return {"unit": _num(rate.get("unit")), "second": _num(rate.get("second"))}
    return {"unit": _num(rate), "second": 0.0}


def _music_cost(usage: Usage, rate: dict[str, float]) -> float:
    """At least one song or clip at the unit price, plus the generated seconds at the per-second price."""
    return max(usage.units, 1) * rate["unit"] + max(usage.seconds, 0.0) * rate["second"]


_unpriced_operations: set[str] = set()


def _global_default(prices: dict[str, Any], section: str) -> float:
    """``prices[section]["default"]["default"]``: the price of an unknown provider and model in ``section``."""
    entry = (prices.get(section) or {}).get("default")
    return _num(entry.get("default")) if isinstance(entry, dict) else 0.0


def _unknown_operation_cost(usage: Usage, prices: dict[str, Any], op: str, provider: str) -> float:
    """Fail closed (see the module docstring): every reported quantity at the global default rate of its kind."""
    if provider == "fake":
        return 0.0
    if op not in _unpriced_operations:  # warn once per operation and process
        _unpriced_operations.add(op)
        log.warning("no pricing for operation %r; charging the global default rates (add it to aadhi.usage.pricing "
                    "or PRICING_FILE)", op)
    tokens = (prices.get("llm_provider_default") or {}).get("default")
    tokens = tokens if isinstance(tokens, dict) else {}
    return (max(usage.input_tokens, 0) * _num(tokens.get("input")) / PER_MILLION
            + max(usage.output_tokens, 0) * _num(tokens.get("output")) / PER_MILLION
            + max(usage.characters, 0) * _global_default(prices, "tts") / PER_MILLION
            + max(usage.units, 1) * _global_default(prices, "image")
            + max(usage.seconds, 0.0) * _global_default(prices, "video"))
