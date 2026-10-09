"""Narration loudness envelope (aadhi.pipeline.envelope) and how the asset stage stores it."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from typing import Any

import numpy as np
import pytest

from aadhi.pipeline import assets as assets_mod
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.envelope import (
    CEIL_DBFS,
    ENVELOPE_FPS,
    FLOOR_DBFS,
    decode_envelope,
    encode_envelope,
    loudness_envelope,
    narration_envelope,
)
from aadhi.schemas.screenplay import Screenplay

SR = 24_000


def tone(seconds: float, dbfs: float, sr: int = SR) -> np.ndarray:
    """A 440 Hz sine whose RMS is ``dbfs``."""
    t = np.arange(int(round(seconds * sr))) / sr
    amp = np.sqrt(2) * 10 ** (dbfs / 20)
    return np.rint(np.sin(2 * np.pi * 440 * t) * amp * 32767).astype(np.int16)


def silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(round(seconds * sr)), dtype=np.int16)


# --- pure ----------------------------------------------------------------------------------------


def test_one_byte_per_frame_silence_zero_loud_speech_full() -> None:
    pcm = np.concatenate([silence(1.0), tone(1.0, -9.0), silence(0.5)])
    env = loudness_envelope(pcm, SR)
    assert len(env) == int(2.5 * ENVELOPE_FPS)
    assert set(env[:30]) == {0} and set(env[60:]) == {0}, "pauses are zero"
    assert min(env[30:60]) == 255, "loud speech saturates"


def test_levels_map_linearly_between_the_floor_and_the_ceiling() -> None:
    mid = (FLOOR_DBFS + CEIL_DBFS) / 2
    env = loudness_envelope(tone(1.0, mid), SR)
    assert all(abs(v - 128) <= 2 for v in env[1:-1]), sorted(set(env))
    assert set(loudness_envelope(tone(0.5, FLOOR_DBFS - 6), SR)) == {0}


def test_partial_last_window_and_other_rates() -> None:
    pcm = tone(1.01, -9.0)  # 30 full windows + 240 samples
    env = loudness_envelope(pcm, SR)
    assert len(env) == 31 and env[-1] == 255, "the short last window is measured on its own samples"
    assert len(loudness_envelope(pcm, SR, fps=25)) == 26
    assert loudness_envelope(np.zeros(0, dtype=np.int16), SR) == b""


def test_base64_round_trip_and_malformed_text() -> None:
    raw = bytes([0, 1, 127, 255])
    text = encode_envelope(raw)
    assert text == "AAF//w==" and decode_envelope(text) == raw
    assert encode_envelope(b"") is None
    assert decode_envelope(None) == b"" and decode_envelope("") == b"" and decode_envelope("not base64!") == b""


def test_a_long_lecture_stays_small() -> None:
    text = encode_envelope(bytes(30 * 60 * ENVELOPE_FPS))  # 30 minutes
    assert text is not None and len(text) <= 75_000


# --- real ffmpeg: what the player hears ----------------------------------------------------------


def test_envelope_of_the_encoded_mp3_lines_up_with_the_pcm() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    from aadhi.pipeline.audio import encode_mp3

    pcm = np.concatenate([silence(1.0), tone(1.0, -12.0), silence(1.0)])
    mp3 = asyncio.run(encode_mp3(pcm))
    env = decode_envelope(asyncio.run(narration_envelope(mp3, "audio/mpeg")))
    assert abs(len(env) - 90) <= 1
    loud = [i for i, v in enumerate(env) if v > 128]
    assert abs(loud[0] - 30) <= 1 and abs(loud[-1] - 59) <= 1, "the LAME header keeps the decode sample-aligned"
    assert max(env[:28]) < 10 and max(env[62:]) < 10


# --- the asset stage -----------------------------------------------------------------------------


def _beat(i: str, text: str, **kw: Any) -> dict[str, Any]:
    return {"id": i, "narration": text, **kw}


SCREENPLAY = {"language": "en-IN", "scenes": [
    {"id": "a", "type": "content", "title": "A", "beats": [_beat("a-b1", "Resistance opposes current.", pause_after=1.0),
                                                           _beat("a-b2", "Copper resists very little.")]},
    {"id": "card", "type": "chapter_card", "title": "Part two", "beats": []},
    {"id": "b", "type": "content", "title": "B", "beats": [_beat("b-b1", "Silver conducts even better.")]},
]}


def fake_envelope(calls: list[bytes]) -> Any:
    async def measure(data: bytes, mime: str, *, ffmpeg: str = "ffmpeg", fps: int = ENVELOPE_FPS) -> str | None:
        calls.append(data)
        return encode_envelope(hashlib.sha256(data).digest()[:8])

    return measure


def failing_envelope(calls: list[bytes]) -> Any:
    async def measure(data: bytes, mime: str, *, ffmpeg: str = "ffmpeg", fps: int = ENVELOPE_FPS) -> str | None:
        calls.append(data)
        raise RuntimeError("no decoder")

    return measure


@pytest.fixture()
def stage(job_ctx, providers, fast_audio):
    sp = Screenplay.model_validate(SCREENPLAY)

    def build(**kw: Any):
        return asyncio.run(build_assets_detailed(job_ctx, sp, GenerationOptions(), **kw))

    return job_ctx, build


def test_new_narration_stores_its_envelope_in_the_asset_and_the_manifest(stage, monkeypatch) -> None:
    ctx, build = stage
    calls: list[bytes] = []
    monkeypatch.setattr(assets_mod, "narration_envelope", fake_envelope(calls))
    res = build()
    a = res.manifest.audio["a"]
    stored = ctx.storage.get_bytes(a.storage_key)
    assert a.envelope == encode_envelope(hashlib.sha256(stored).digest()[:8]), "measured from the file the page plays"
    assert a.envelope_fps == ENVELOPE_FPS
    assert ctx.assets.get(a.asset_key).meta["envelope"] == a.envelope  # cached with the narration
    assert "card" not in res.manifest.audio or res.manifest.audio["card"].envelope is None
    assert len(calls) == 2, "one measurement per narration file"
    # the timeline carries it to the player
    from aadhi.compose.timeline import build_timeline

    tl = build_timeline(Screenplay.model_validate(SCREENPLAY), res.manifest, settings=ctx.settings)
    scene = next(s for s in tl.scenes if s.scene_id == "a")
    assert scene.audio_envelope == a.envelope and scene.audio_envelope_fps == ENVELOPE_FPS


def test_a_failed_measurement_never_fails_the_narration(stage, monkeypatch) -> None:
    _, build = stage
    monkeypatch.setattr(assets_mod, "narration_envelope", failing_envelope([]))
    res = build()
    assert not [i for i in res.issues if i.severity == "error"]
    assert res.manifest.audio["a"].asset_key and res.manifest.audio["a"].envelope is None


def test_narration_built_before_envelopes_gets_one_without_new_tts(stage, providers, monkeypatch) -> None:
    ctx, build = stage
    monkeypatch.setattr(assets_mod, "narration_envelope", failing_envelope([]))
    first = build().manifest  # "old" manifest and cached files: no envelope anywhere
    assert first.audio["a"].envelope is None and "envelope" not in (ctx.assets.get(first.audio["a"].asset_key).meta or {})
    n_tts = len(providers.tts.texts)
    calls: list[bytes] = []
    monkeypatch.setattr(assets_mod, "narration_envelope", fake_envelope(calls))
    # 1) reused scenes (nothing changed): measured once from the stored narration
    again = build(previous=first)
    assert sorted(again.reused) == sorted(first.media) and not again.built
    assert again.manifest.audio["a"].envelope and again.manifest.audio["b"].envelope
    assert len(providers.tts.texts) == n_tts, "no TTS re-run"
    assert len(calls) == 2
    # the next build reuses the envelope from the manifest
    third = build(previous=again.manifest)
    assert third.manifest.audio == again.manifest.audio and len(calls) == 2
    # 2) a rebuilt scene whose cached narration file has no envelope: measured from the stored file
    rebuilt = build(previous=first, scene_ids=["a"])
    assert rebuilt.built == ["a"] and rebuilt.manifest.audio["a"].envelope == again.manifest.audio["a"].envelope
    assert len(providers.tts.texts) == n_tts
