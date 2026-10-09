"""Deterministic tick/ding sound effects."""

from __future__ import annotations

import asyncio
import io
import wave

import numpy as np

from aadhi.compose.sounds import (
    DING_KEY,
    SAMPLE_RATE,
    TICK_KEY,
    ding_wav,
    ensure_sounds,
    ensure_sounds_async,
    sound_key,
    tick_wav,
)


def read_wav(data: bytes) -> tuple[int, int, np.ndarray]:
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
        return w.getframerate(), w.getnchannels(), np.frombuffer(frames, dtype="<i2")


def test_wavs_are_deterministic_and_valid() -> None:
    assert tick_wav() == tick_wav()
    assert ding_wav() == ding_wav()
    sr, ch, tick = read_wav(tick_wav())
    assert (sr, ch) == (SAMPLE_RATE, 1)
    assert 0.05 < len(tick) / sr < 0.2
    _, _, ding = read_wav(ding_wav())
    assert 0.8 < len(ding) / sr < 1.5
    for sig in (tick, ding):
        assert np.abs(sig).max() > 3000  # audible
        assert np.abs(sig).max() < 32767  # no clipping
        assert abs(int(sig[-1])) < 200  # faded out: no click


def test_keys_are_fixed_and_distinct() -> None:
    assert TICK_KEY == sound_key("tick") and DING_KEY == sound_key("ding")
    assert TICK_KEY != DING_KEY and TICK_KEY.startswith("scene_audio-")


def test_ensure_sounds_is_idempotent(asset_store) -> None:
    first = ensure_sounds(asset_store)
    second = ensure_sounds(asset_store)
    assert first == second
    assert first["tick"].storage_key.startswith("assets/scene_audio/") and first["tick"].storage_key.endswith(".wav")
    assert asset_store.storage.get_bytes(first["ding"].storage_key) == ding_wav()
    row = asset_store.get(TICK_KEY)
    assert row.mime == "audio/wav" and row.kind == "scene_audio" and 0.05 < row.duration_s < 0.2


def test_ensure_sounds_recreates_missing_blob(asset_store) -> None:
    first = ensure_sounds(asset_store)
    asset_store.storage.delete(first["tick"].storage_key)
    again = ensure_sounds(asset_store)
    assert asset_store.storage.exists(again["tick"].storage_key)


def test_ensure_sounds_async(asset_store) -> None:
    out = asyncio.run(ensure_sounds_async(asset_store))
    assert set(out) == {"tick", "ding"}
    assert out == ensure_sounds(asset_store)
