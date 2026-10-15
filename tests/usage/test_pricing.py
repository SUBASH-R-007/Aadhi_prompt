"""Claude list prices and prompt-cache pricing in ``aadhi.usage.pricing``."""

from __future__ import annotations

import datetime as dt
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


# --- current Gemini / OpenAI / Veo models (list prices of October 2026) ------------------------------

OCT_2026 = dt.date(2026, 10, 10)
JAN_2027 = dt.date(2027, 1, 1)


@pytest.mark.parametrize(
    ("provider", "model", "price_in", "price_out"),
    [
        ("gemini", "gemini-3.8-flash", 0.75, 3.75),
        ("gemini", "gemini-3.7-flash", 0.75, 3.75),
        ("gemini", "gemini-3.5-flash-lite", 0.30, 2.50),  # longest prefix: not gemini-3.5-flash
        ("gemini", "gemini-3.5-flash", 1.50, 9.00),
        ("gemini", "gemini-3.1-pro-preview", 2.00, 12.00),
        ("gemini", "gemini-3.1-pro-preview-customtools", 2.00, 12.00),
        ("gemini", "gemini-3.1-flash-lite", 0.25, 1.50),
        ("gemini", "gemini-2.5-pro", 1.25, 10.00),  # older models keep their prices
        ("gemini", "gemini-3.9-flash-future", 0.75, 3.75),  # unlisted Gemini: the default model's price
        ("openai", "gpt-5.6-sol", 4.00, 20.00),
        ("openai", "gpt-5.6-terra", 2.00, 12.00),
        ("openai", "gpt-5.6-luna", 0.20, 1.20),
        ("openai", "gpt-5.6", 4.00, 20.00),
        ("openai", "gpt-6-luna", 0.10, 0.50),
        ("openai", "gpt-6.1-sol", 2.00, 10.00),
        ("openai", "gpt-6-astra", 10.00, 50.00),
        ("openai", "gpt-5.5", 5.00, 30.00),
        ("openai", "gpt-5-mini", 0.25, 2.00),  # gpt-5.x keys never shadow the gpt-5 family
        ("openai", "gpt-4.1", 2.00, 8.00),
        ("openai", "gpt-4.1-mini", 0.40, 1.60),
    ],
)
def test_current_llm_list_prices(settings: Settings, provider: str, model: str, price_in: float,
                                  price_out: float) -> None:
    usage = Usage(provider, model, "llm", input_tokens=M, output_tokens=M)
    assert estimate_cost(usage, settings, on=OCT_2026) == pytest.approx(price_in + price_out)


def test_current_media_list_prices(settings: Settings) -> None:
    def cost(provider: str, model: str, op: str, **kw) -> float:
        return estimate_cost(Usage(provider, model, op, **kw), settings, on=OCT_2026)

    # TTS per 1M characters (audio tokens at 25/s of speech, ~940 characters per minute)
    assert cost("gemini", "gemini-3.8-flash-tts", "tts", characters=M) == pytest.approx(14.5)
    assert cost("gemini", "gemini-3.8-flash-lite-tts", "tts", characters=M) == pytest.approx(9.7)
    assert cost("gemini", "gemini-2.5-flash-preview-tts", "tts", characters=M) == pytest.approx(16.0)
    assert cost("gemini", "gemini-2.5-pro-preview-tts", "tts", characters=M) == pytest.approx(32.0)
    assert cost("openai", "gpt-4o-mini-tts", "tts", characters=M) == pytest.approx(16.0)
    assert cost("openai", "gpt-4o-mini-tts-2025-12-15", "tts", characters=M) == pytest.approx(16.0)
    assert cost("openai", "tts-1-hd", "tts", characters=M) == pytest.approx(30.0)
    # images (1K)
    assert cost("gemini", "gemini-nano-banana-2.1", "image", units=10) == pytest.approx(0.336)
    assert cost("gemini", "gemini-3.1-flash-image", "image") == pytest.approx(0.067)
    assert cost("gemini", "gemini-3.1-flash-lite-image", "image") == pytest.approx(0.0336)
    assert cost("gemini", "gemini-3-pro-image", "image") == pytest.approx(0.134)
    assert cost("gemini", "gemini-2.5-flash-image", "image") == pytest.approx(0.039)
    # video per second (720p)
    assert cost("veo", "veo-3.1-fast-generate-preview", "video", seconds=8) == pytest.approx(0.80)
    assert cost("veo", "veo-3.1-generate-preview", "video", seconds=8) == pytest.approx(3.20)
    assert cost("veo", "veo-3.1-lite-generate-preview", "video", seconds=8) == pytest.approx(0.40)
    assert cost("veo", "veo-3.0-fast-generate-001", "video", seconds=8) == pytest.approx(3.20)
    assert cost("veo", "veo-2.0-generate-001", "video", seconds=8) == pytest.approx(4.0)


def test_gemini_image_prompt_and_thinking_tokens_are_priced_on_top_of_the_image(settings: Settings) -> None:
    def cost(provider: str, model: str, **kw) -> float:
        return estimate_cost(Usage(provider, model, "image", **kw), settings, on=OCT_2026)

    # 1 image (~1,120 image tokens, covered by the per-image price) + 200 prompt + 1,000 thinking tokens
    thinking = {"units": 1, "input_tokens": 200, "output_tokens": 2120, "meta": {"thoughts_tokens": 1000}}
    assert cost("gemini", "gemini-nano-banana-2.1", **thinking) == pytest.approx(0.0336 + 200 * 1.5e-6 + 1000 * 7.5e-6)
    assert cost("gemini", "gemini-3-pro-image-preview", **thinking) == pytest.approx(0.134 + 200 * 2e-6 + 1000 * 12e-6)
    assert cost("gemini", "gemini-3.1-flash-lite-image", **thinking) == pytest.approx(0.0336 + 200 * 0.25e-6
                                                                                     + 1000 * 1.5e-6)
    # without thinking (Gemini 2.5 image) only the prompt is added; no usage metadata = the plain image price
    assert cost("gemini", "gemini-2.5-flash-image", units=1, input_tokens=200, output_tokens=1290,
                meta={"thoughts_tokens": 0}) == pytest.approx(0.039 + 200 * 0.3e-6)
    assert cost("gemini", "gemini-nano-banana-2.1", units=1) == pytest.approx(0.0336)
    assert cost("gemini", "gemini-nano-banana-2.1", units=1, meta={"thoughts_tokens": -5}) == pytest.approx(0.0336)
    # other providers and models keep the plain per-image price
    assert cost("openai", "gpt-image-1", **thinking) == pytest.approx(0.04)
    assert cost("fake", "fake-image", **thinking) == 0.0
    assert cost("pollinations", "default", **thinking) == 0.0


def test_pricing_file_can_set_image_token_prices(settings: Settings, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"image_tokens": {"gemini-nano-banana-2.1": {"input": 0.0, "output": 10.0}}}),
                    encoding="utf-8")
    custom = settings.model_copy(update={"pricing_file": path})
    usage = Usage("gemini", "gemini-nano-banana-2.1", "image", units=1, input_tokens=M, meta={"thoughts_tokens": M})
    assert estimate_cost(usage, custom, on=OCT_2026) == pytest.approx(0.0336 + 10.0)


def test_announced_gemini_price_step_applies_from_2027(settings: Settings) -> None:
    flash = Usage("gemini", "gemini-3.8-flash", "llm", input_tokens=M, output_tokens=M)
    tts = Usage("gemini", "gemini-3.8-flash-tts", "tts", characters=M)
    unlisted = Usage("gemini", "gemini-3.9-flash-future", "llm", input_tokens=M)
    pro = Usage("gemini", "gemini-3.1-pro-preview", "llm", input_tokens=M)
    assert estimate_cost(flash, settings, on=dt.date(2026, 12, 31)) == pytest.approx(0.75 + 3.75)
    assert estimate_cost(flash, settings, on=JAN_2027) == pytest.approx(1.50 + 7.50)
    assert estimate_cost(tts, settings, on=JAN_2027) == pytest.approx(29.1)
    assert estimate_cost(Usage("gemini", "gemini-3.8-flash-lite-tts", "tts", characters=M), settings,
                         on=JAN_2027) == pytest.approx(19.5)
    assert estimate_cost(unlisted, settings, on=JAN_2027) == pytest.approx(1.50)
    assert estimate_cost(pro, settings, on=JAN_2027) == pytest.approx(2.00)  # no step announced
    assert pricing.load_prices(settings, on=OCT_2026) is pricing.DEFAULT_PRICES
    assert pricing.DEFAULT_PRICES["llm"]["gemini-3.8-flash"] == {"input": 0.75, "output": 3.75}  # never mutated
    assert estimate_cost(flash, settings) > 0  # today's table


def test_pricing_file_wins_over_the_schedule(settings: Settings, tmp_path: pathlib.Path) -> None:
    flash = Usage("gemini", "gemini-3.8-flash", "llm", input_tokens=M, output_tokens=M)
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"llm": {"gemini-3.8-flash": {"input": 1.0, "output": 2.0}}}), encoding="utf-8")
    custom = settings.model_copy(update={"pricing_file": path})
    assert estimate_cost(flash, custom, on=OCT_2026) == pytest.approx(3.0)
    assert estimate_cost(flash, custom, on=JAN_2027) == pytest.approx(3.0)  # the operator's value is kept
    tts = Usage("gemini", "gemini-3.8-flash-tts", "tts", characters=M)
    assert estimate_cost(tts, custom, on=JAN_2027) == pytest.approx(29.1)  # other entries still step

    path.write_text(json.dumps({"scheduled": []}), encoding="utf-8")  # an empty schedule turns the steps off
    off = settings.model_copy(update={"pricing_file": tmp_path / "prices.json"})
    pricing.reset_price_cache()
    assert estimate_cost(flash, off, on=JAN_2027) == pytest.approx(0.75 + 3.75)

    path.write_text(json.dumps({"scheduled": [{"from": "someday", "prices": {}}, {"prices": {}},
                                              {"from": "2027-02-01", "prices": {"llm": {
                                                  "gemini-3.8-flash": {"input": 9.0, "output": 9.0}}}}]}),
                    encoding="utf-8")
    pricing.reset_price_cache()
    assert estimate_cost(flash, off, on=JAN_2027) == pytest.approx(0.75 + 3.75)  # malformed steps are skipped
    assert estimate_cost(flash, off, on=dt.date(2027, 2, 1)) == pytest.approx(18.0)
