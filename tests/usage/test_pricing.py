"""Claude list prices and prompt-cache pricing in ``aadhi.usage.pricing``."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest

from aadhi.config import Settings
from aadhi.providers.base import Usage
from aadhi.usage import pricing
from aadhi.usage.pricing import estimate_cost

M = 1_000_000


@pytest.fixture(autouse=True)
def _fresh_prices() -> Iterator[None]:
    pricing.reset_price_cache()
    yield
    pricing.reset_price_cache()


@pytest.fixture()
def settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("model", "price_in", "price_out"),
    [
        ("claude-opus-5-5", 4.0, 20.0),  # longest prefix: not claude-opus-5
        ("claude-opus-5", 5.0, 25.0),
        ("claude-fable-5-1", 10.0, 50.0),
        ("claude-fable-5", 10.0, 50.0),
        ("claude-mythos-5-1", 10.0, 50.0),
        ("claude-opus-4-8", 5.0, 25.0),
        ("claude-opus-4-5-20251101", 5.0, 25.0),  # dated snapshot -> family
        ("claude-sonnet-5-5", 2.0, 10.0),
        ("claude-sonnet-5", 2.0, 10.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-sonnet-4-5-20250929", 3.0, 15.0),
        ("claude-haiku-4-5", 1.0, 5.0),
        # Older families still served: never the cheaper current-Opus price.
        ("claude-opus-4-0", 15.0, 75.0),
        ("claude-opus-4-20250514", 15.0, 75.0),
        ("claude-opus-4-1", 15.0, 75.0),
        ("claude-opus-4-1-20250805", 15.0, 75.0),
        ("claude-sonnet-4-0", 3.0, 15.0),
        ("claude-sonnet-4-20250514", 3.0, 15.0),
        ("claude-3-7-sonnet-20250219", 3.0, 15.0),
        ("claude-3-5-haiku-20241022", 0.8, 4.0),
        ("claude-3-haiku-20240307", 0.25, 1.25),
        ("claude-3-opus-20240229", 15.0, 75.0),
        ("claude-future-9", 15.0, 75.0),  # unknown Claude model: the conservative anthropic provider default
    ],
)
def test_claude_list_prices(settings: Settings, model: str, price_in: float, price_out: float) -> None:
    usage = Usage("anthropic", model, "llm", input_tokens=M, output_tokens=M)
    assert estimate_cost(usage, settings) == pytest.approx(price_in + price_out)


def test_unknown_claude_model_is_over_estimated_and_logged_once(settings: Settings, caplog) -> None:
    listed = max(r["input"] + r["output"] for k, r in pricing.DEFAULT_PRICES["llm"].items() if k.startswith("claude"))
    pricing._unpriced_models.discard(("anthropic", "claude-unlisted-1"))
    with caplog.at_level("WARNING", logger="aadhi.usage.pricing"):
        for _ in range(3):
            cost = estimate_cost(Usage("anthropic", "claude-unlisted-1", "llm", input_tokens=M, output_tokens=M),
                                 settings)
    assert cost >= listed  # budgets fail closed for models without a list price
    assert sum("claude-unlisted-1" in r.getMessage() for r in caplog.records) == 1
    caplog.clear()
    estimate_cost(Usage("fake", "fake-model", "llm", input_tokens=M), settings)  # the free test engine: silent
    assert not caplog.records


def test_cache_reads_and_writes_are_priced_separately(settings: Settings) -> None:
    # input_tokens includes the cache traffic: 100k uncached + 300k written + 600k read.
    meta = {"cache_read_tokens": 600_000, "cache_write_tokens": 300_000}
    sonnet = Usage("anthropic", "claude-sonnet-5-5", "llm", input_tokens=M, meta=meta)
    assert estimate_cost(sonnet, settings) == pytest.approx(0.1 * 2.0 + 0.3 * 2.0 * 1.25 + 0.6 * 2.0 * 0.10)
    opus = Usage("anthropic", "claude-opus-5-5", "vision", input_tokens=M, output_tokens=M, meta=meta)
    # Claude Opus 5.5 lists its own cache-hit price ($0.20/M rather than 0.10x of $4).
    assert estimate_cost(opus, settings) == pytest.approx(0.1 * 4.0 + 0.3 * 5.0 + 0.6 * 0.20 + 20.0)
    plain = Usage("anthropic", "claude-sonnet-5-5", "llm", input_tokens=M)
    assert estimate_cost(plain, settings) == pytest.approx(2.0)


def test_cache_tokens_are_clamped_and_openai_cached_tokens_unchanged(settings: Settings) -> None:
    odd = Usage("anthropic", "claude-haiku-4-5", "llm", input_tokens=1000,
                meta={"cache_read_tokens": 5000, "cache_write_tokens": -3})
    assert estimate_cost(odd, settings) == pytest.approx(1000 * 0.10 / M)  # never more than input_tokens
    openai = Usage("openai", "gpt-4.1", "llm", input_tokens=M, meta={"cached_tokens": 500_000})
    assert estimate_cost(openai, settings) == pytest.approx(2.0)


def test_pricing_file_can_set_cache_prices(settings: Settings, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"llm": {"claude-sonnet-5-5": {"cache_write": 4.0, "cache_read": 0.5}}}),
                    encoding="utf-8")
    custom = settings.model_copy(update={"pricing_file": path})
    usage = Usage("anthropic", "claude-sonnet-5-5", "llm", input_tokens=2 * M,
                  meta={"cache_read_tokens": M, "cache_write_tokens": M})
    assert estimate_cost(usage, custom) == pytest.approx(4.0 + 0.5)
