"""B0 pricing (DYNAMIC_VIDEO_DESIGN §5.7): the ElevenLabs list prices, the ``music`` operation and unknown
operations failing closed."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from collections.abc import Iterator

import pytest

from aadhi.config import Settings
from aadhi.providers.base import Usage
from aadhi.usage import pricing
from aadhi.usage.pricing import DEFAULT_PRICES, estimate_cost

M = 1_000_000
OCT_2026 = dt.date(2026, 10, 10)


@pytest.fixture(autouse=True)
def _fresh_prices() -> Iterator[None]:
    pricing.reset_price_cache()
    yield
    pricing.reset_price_cache()


@pytest.fixture()
def settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]


def cost(settings: Settings, provider: str, model: str, op: str, **kw) -> float:
    return estimate_cost(Usage(provider, model, op, **kw), settings, on=OCT_2026)


# --- ElevenLabs text to speech (list prices read 2026-10-10) -----------------------------------------


@pytest.mark.parametrize(
    ("model", "per_million"),
    [
        ("eleven_v4", 80.0),  # list price; the 72% promotion until 12 Oct 2026 is deliberately not used
        ("eleven_v4_turbo", 40.0),  # longest prefix: not eleven_v4
        ("eleven_v3", 80.0),
        ("eleven_v3_conversational", 40.0),
        ("eleven_multilingual_v2", 80.0),
        ("eleven_multilingual_v1", 80.0),
        ("eleven_flash_v2_5", 40.0),
        ("eleven_flash_v2", 40.0),
        ("eleven_turbo_v2_5", 40.0),  # deprecated, billed like Flash
        ("eleven_v9_future", 80.0),  # unlisted: the higher price
    ],
)
def test_elevenlabs_tts_list_prices(settings: Settings, model: str, per_million: float) -> None:
    assert cost(settings, "elevenlabs", model, "tts", characters=M) == pytest.approx(per_million)
    assert cost(settings, "elevenlabs", model, "tts", characters=1000) == pytest.approx(per_million / 1000)


def test_elevenlabs_default_is_the_highest_listed_tts_price() -> None:
    table = DEFAULT_PRICES["tts"]["elevenlabs"]
    assert table["default"] == max(v for k, v in table.items() if k != "default")


# --- music -------------------------------------------------------------------------------------------


def test_music_prices(settings: Settings) -> None:
    # Lyria: per song or clip (at least one per call), on the Gemini key
    assert cost(settings, "lyria", "lyria-3.5", "music") == pytest.approx(0.08)
    assert cost(settings, "lyria", "lyria-3.5", "music", units=3, seconds=360) == pytest.approx(0.24)
    assert cost(settings, "lyria", "lyria-3-clip-preview", "music", units=2) == pytest.approx(0.08)
    assert cost(settings, "lyria", "lyria-3-pro-preview", "music") == pytest.approx(0.08)
    assert cost(settings, "gemini", "lyria-3.5", "music") == pytest.approx(0.08)  # adapter reporting the key's provider
    assert cost(settings, "lyria", "lyria-4-future", "music") == pytest.approx(0.08)
    # ElevenLabs Music: $0.15 per minute of generated music
    assert cost(settings, "elevenlabs", "music_v2_5", "music", seconds=60) == pytest.approx(0.15)
    assert cost(settings, "elevenlabs", "music_v2_5", "music", seconds=312, units=1) == pytest.approx(0.78)
    # free sources
    assert cost(settings, "procedural", "bed-v1", "music", seconds=300) == 0.0
    assert cost(settings, "fake", "fake-music", "music", seconds=300, units=3) == 0.0
    # an unknown music provider pays both rates (fail closed)
    assert cost(settings, "stableaudio", "x", "music", seconds=60) == pytest.approx(0.08 + 0.15)
    assert cost(settings, "", "", "music", units=0) == pytest.approx(0.08)
    # negative quantities never reduce the cost
    assert cost(settings, "elevenlabs", "music_v2_5", "music", seconds=-50) == 0.0


def test_pricing_file_can_price_music(settings: Settings, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"music": {"lyria": {"lyria-3.5": 0.10},  # a plain number is per unit
                                          "elevenlabs": {"default": {"second": 0.004}}}}), encoding="utf-8")
    custom = settings.model_copy(update={"pricing_file": path})
    assert cost(custom, "lyria", "lyria-3.5", "music", units=2) == pytest.approx(0.20)
    assert cost(custom, "lyria", "lyria-3-clip", "music") == pytest.approx(0.04)  # deep merge keeps the rest
    assert cost(custom, "elevenlabs", "music_v2_5", "music", seconds=100) == pytest.approx(0.4)


# --- unknown operations fail closed -----------------------------------------------------------------


def test_unknown_operations_are_charged_at_the_global_defaults(settings: Settings, caplog) -> None:
    pricing._unpriced_operations.discard("telepathy")
    tokens = DEFAULT_PRICES["llm_provider_default"]["default"]
    per_char = DEFAULT_PRICES["tts"]["default"]["default"]
    per_unit = DEFAULT_PRICES["image"]["default"]["default"]
    per_second = DEFAULT_PRICES["video"]["default"]["default"]
    with caplog.at_level("WARNING", logger="aadhi.usage.pricing"):
        everything = cost(settings, "acme", "x", "telepathy", input_tokens=M, output_tokens=M, characters=M,
                          units=2, seconds=10)
        nothing = cost(settings, "acme", "x", "telepathy")
    assert everything == pytest.approx(tokens["input"] + tokens["output"] + per_char + 2 * per_unit
                                       + 10 * per_second)
    assert nothing == pytest.approx(per_unit) and nothing > 0  # never free: at least one unit
    assert sum("telepathy" in r.getMessage() for r in caplog.records) == 1  # warned once per operation
    assert cost(settings, "acme", "x", "", units=1) > 0  # no operation at all: also unknown


def test_unknown_operations_stay_free_for_the_fake_provider(settings: Settings, caplog) -> None:
    pricing._unpriced_operations.discard("fake-op")
    with caplog.at_level("WARNING", logger="aadhi.usage.pricing"):
        assert cost(settings, "fake", "fake-model", "fake-op", input_tokens=M, units=5, seconds=9) == 0.0
    assert not caplog.records


def test_known_operations_are_unchanged_by_the_fail_closed_rule(settings: Settings) -> None:
    assert cost(settings, "giphy", "search", "gif", units=1) == 0.0
    assert cost(settings, "edge", "en-IN-NeerjaNeural", "tts", characters=M) == 0.0
    assert cost(settings, "pollinations", "flux", "image", units=4) == 0.0
    assert cost(settings, "veo", "veo-3.1-fast-generate-preview", "video", seconds=8) == pytest.approx(0.8)


def test_pricing_file_moves_the_fail_closed_rates(settings: Settings, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"image": {"default": {"default": 0.5}}, "video": {"default": {"default": 2.0}}}),
                    encoding="utf-8")
    custom = settings.model_copy(update={"pricing_file": path})
    assert cost(custom, "acme", "x", "hologram", units=1, seconds=3) == pytest.approx(0.5 + 6.0)
