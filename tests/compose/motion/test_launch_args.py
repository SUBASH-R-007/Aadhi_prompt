"""A capture worker's Chromium is launched with exactly the flags its chunk cache key records (§7.4, §7.5).

``StageOptions.chromium_args()`` feeds ``capture_fingerprint`` (part of every chunk's cache key), while
``stage_page.stage_browser`` builds the launch list itself. A flag added to one list and not the other (as
``DETERMINISM_ARGS`` had to be added to both) would reuse a cached chunk captured under other flags, or miss the cache
for nothing. Stand-in Playwright driver: no Chromium (fast).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.compose.dynamic.stage_page import DETERMINISM_ARGS
from aadhi.compose.motion.stage import StageOptions, StageSession
from aadhi.config import Settings

BENCH_FLAG = "--disable-frame-rate-limit"  # the benchmark's experiment flag (StageOptions.extra_args)


class _Launched(Exception):
    """The stand-in driver was asked for a browser: the test has the flags it needs."""


class _Chromium:
    """``playwright.chromium``: records each ``launch`` and stops there."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def launch(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        raise _Launched


@pytest.mark.parametrize("options", [StageOptions(), StageOptions(webgl=True), StageOptions(extra_args=(BENCH_FLAG,)),
                                     StageOptions(webgl=True, extra_args=(BENCH_FLAG,))],
                         ids=["default", "webgl", "extra", "webgl+extra"])
async def test_a_worker_launches_with_the_flags_its_cache_key_records(options: StageOptions) -> None:
    chromium = _Chromium()
    session = StageSession(settings=Settings(_env_file=None), size=(320, 180), token="t", served=None,
                           base_url="http://127.0.0.1:8000", options=options,
                           playwright=SimpleNamespace(chromium=chromium))
    with pytest.raises(_Launched):
        await session.start()
    assert len(chromium.calls) == 1
    launched = chromium.calls[0]["args"]
    assert launched == options.chromium_args() == options.capture_fingerprint()["args"]
    assert set(DETERMINISM_ARGS) <= set(launched)  # order-independent frames, Dynamic stage only (§7.4)
