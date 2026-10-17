"""Classic audio keys, frozen (B3 plan §3.1 item 1-3, §A B3-0 task 3: "must pass now and forever").

The invariance goldens use a synthetic manifest and ``TTS_PROVIDER=fake``, so they cannot see a change to how the
asset stage keys its audio. B3 adds style, persona, per-sentence units and speaker gains to the Dynamic voicing branch
only (``b3_voicing``); this file is the gate that the CLASSIC keys gain nothing:

* the ``clip_key`` payload and value of fixed beats narrated on ``edge``, ``openai`` and ``fake`` (a real
  ``build_assets`` run with a recording ``compute_key``, so the payload is the one the build really hashes);
* the ``scene_audio`` key payload and value for the resulting clip lists (a plain scene and a quiz with its countdown
  and reveal beat);
* ``AUDIO_VERSION``, ``KEEP_PAD_SECONDS``, ``INTER_BEAT_GAP_SECONDS``;
* the built-in sounds: ``TICK_KEY`` / ``DING_KEY``, ``SOUND_VERSION`` / ``SOUND_KIND`` and the sha256 of
  ``tick_wav()`` / ``ding_wav()``;
* the OpenAI speech request without a style (extends ``test_openai_tts_legacy_models_keep_the_same_request`` to
  ``gpt-4o-mini-tts``): no ``instructions`` key, ``speed`` only off 1.0.

A failure here means classic audio would be re-synthesised (paid, on OpenAI) or would sound different. Change a value
only for a classic change the owner approved.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from typing import Any

import pytest

from aadhi.pipeline import assets as assets_mod
from aadhi.pipeline import integrations
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.fakes import FakeTTS, install_fast_audio, install_providers

# The fixed lecture: a two-beat scene (one beat with a lexicon respelling, one with a pause) and a quiz with its
# countdown and a reveal beat.
SCREENPLAY: dict[str, Any] = {
    "language": "en-IN",
    "lexicon": [{"written": "BJT", "spoken": "B J T"}],
    "scenes": [
        {"id": "k", "type": "content", "title": "Keys",
         "beats": [{"id": "k1", "narration": "A BJT has three terminals.", "pause_after": 0.5},
                   {"id": "k2", "narration": "Current flows from collector to emitter."}]},
        {"id": "q", "type": "quiz_checkpoint", "title": "Check", "question": "Which?", "options": ["a", "b"],
         "correct_index": 1, "countdown_seconds": 5,
         "beats": [{"id": "q1", "narration": "Which terminal is the base?"}],
         "reveal_beats": [{"id": "q2", "narration": "It is the middle one."}]},
    ],
}
SPOKEN = ("A B J T has three terminals.", "Current flows from collector to emitter.", "Which terminal is the base?",
          "It is the middle one.")
VOICES = {"edge": ("en-IN-NeerjaNeural", ""), "openai": ("nova", "gpt-4o-mini-tts"), "fake": ("fake-en-IN", "")}

# --- the frozen values -----------------------------------------------------------------------------------------------

# per provider, the clips of k1, k2, q1, q2 in order
CLIP_KEYS: dict[str, tuple[str, ...]] = {
    "edge": ("tts-163a1bbffa6d5db1875ddd531cbb701adc7b4d34", "tts-9986ff73585ad94eb4e11d66950eac00b47781a0",
             "tts-bee76dfe07c809d5ad381383a1c6be40efaa5815", "tts-973778faf2d7d0b5d7942d8d9392abf868af6115"),
    "openai": ("tts-71b0a4f8a95f766d3adaadcaa986d8f84ddf6e55", "tts-02047cc709919e7442f754c26e3973799bb4f254",
               "tts-d8bf8dbd5c33a1446f8e3e50ba49be1a5903194b", "tts-b3a489a7bfa845cf309ad0e57b3ac88d1c41f7bb"),
    "fake": ("tts-021838702be5d2582d3136807ab04e5e0044164c", "tts-f7664e5815360db3c669cd836a46562bfb253fb3",
             "tts-ea609e40d8bb856af710d54a709360d3077399c9", "tts-80db285f9b796148ea629b15a6a15064139d90cb"),
}
SCENE_AUDIO_KEYS: dict[str, dict[str, str]] = {
    "edge": {"k": "scene_audio-ab1a3cc0fb2faf3da6a69afde7cc9954d63877ba",
             "q": "scene_audio-f9ce3af6e0e852688268e5af8151bc33c6045542"},
    "openai": {"k": "scene_audio-35452782072e80508a83112f1a0effa4fbcd5a47",
               "q": "scene_audio-41e44787d67f51e3004e9518b951ec686d74e9d3"},
    "fake": {"k": "scene_audio-3d52051a4b9c00f6c1716f0e2be8b934d3077e9f",
             "q": "scene_audio-783945b893fc4f8ad2f4d3304297f5368290f446"},
}
AUDIO_VERSION = "2"
KEEP_PAD_SECONDS = 0.03
INTER_BEAT_GAP_SECONDS = 0.15
SOUNDS = {
    "version": "1", "kind": "scene_audio",
    "tick_key": "scene_audio-2caa45a8bdd340744d7cbab083adb537618be899",
    "ding_key": "scene_audio-db018b1b2381b4646a3cb71b47f4dbbbee854dbf",
    "tick_sha256": "6953561e04cdfa1c2f52cd5d0411ca1cb022396bc001d7bb3d75285072bdb4b2",
    "ding_sha256": "4b28a1a7e91b8dde8d66dae3b875011f779e8f640b7fb98f6b900a93d916f4bf",
}


def _build(job_ctx: Any, monkeypatch: pytest.MonkeyPatch, provider: str) -> tuple[Any, list[tuple[str, Any, str]]]:
    """One classic asset build of SCREENPLAY on ``provider`` (a fake TTS under that name), recording every key."""
    voice, model = VOICES[provider]
    tts = FakeTTS(name=provider)
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: tts)
    recorded: list[tuple[str, Any, str]] = []
    real = assets_mod.compute_key

    def recording(kind: str, inputs: dict[str, Any], version: str = "1") -> str:
        recorded.append((kind, inputs, version))
        return real(kind, inputs, version)

    monkeypatch.setattr(assets_mod, "compute_key", recording)
    job_ctx.settings = job_ctx.settings.model_copy(update={
        "tts_provider": provider, "tts_voice": "", "tts_rate": "+0%", "openai_tts_model": "gpt-4o-mini-tts",
        "default_language": "en-IN", "image_provider": "none", "video_provider": "none", "dynamic_enabled": False})
    opts = GenerationOptions(tts_provider=provider, tts_voice=voice)
    res = asyncio.run(build_assets_detailed(job_ctx, Screenplay.model_validate(SCREENPLAY), opts))
    assert not [i for i in res.issues if i.severity == "error"], res.issues
    assert sorted(tts.texts) == sorted(SPOKEN)
    return res.manifest, recorded


@pytest.fixture()
def keyed(job_ctx, monkeypatch):
    install_providers(monkeypatch)  # every provider seam offline (the TTS is replaced per provider in _build)
    install_fast_audio(monkeypatch)  # the real assembly logic without ffmpeg
    return lambda provider: _build(job_ctx, monkeypatch, provider)


@pytest.mark.parametrize("provider", sorted(VOICES))
def test_the_classic_clip_key_payload_and_value(keyed, provider: str) -> None:
    voice, model = VOICES[provider]
    manifest, recorded = keyed(provider)
    payloads = sorted((inputs for kind, inputs, _ in recorded if kind == "tts"), key=lambda p: p["text"])
    # exactly these six fields: style, persona, sentence units and gains never enter a classic clip key
    assert payloads == sorted(({"text": t, "voice": voice, "provider": provider, "rate": "+0%", "model": model,
                                "language": "en-IN"} for t in SPOKEN), key=lambda p: p["text"])
    assert {version for kind, _, version in recorded if kind == "tts"} == {"1"}
    clips = tuple(b.clip_asset_key for sid in ("k", "q") for b in manifest.audio[sid].beats)
    assert clips == CLIP_KEYS[provider]
    assert {(a.provider, a.voice, a.language) for a in manifest.audio.values()} == {(provider, voice, "en-IN")}


@pytest.mark.parametrize("provider", sorted(VOICES))
def test_the_classic_scene_audio_key_payload_and_value(keyed, provider: str) -> None:
    manifest, recorded = keyed(provider)
    scene = {inputs["narration"][0]: inputs for kind, inputs, _ in recorded if kind == "scene_audio"}
    k, q = manifest.audio["k"], manifest.audio["q"]
    assert scene["A BJT has three terminals."] == {
        "clips": [b.clip_asset_key for b in k.beats], "pauses": [0.5, 0.0], "phases": ["main", "main"],
        "narration": ["A BJT has three terminals.", "Current flows from collector to emitter."], "countdown": None,
        "audio": AUDIO_VERSION}
    assert scene["Which terminal is the base?"] == {
        "clips": [b.clip_asset_key for b in q.beats], "pauses": [0.0, 0.0], "phases": ["main", "reveal"],
        "narration": ["Which terminal is the base?", "It is the middle one."], "countdown": 5, "audio": AUDIO_VERSION}
    assert {sid: a.asset_key for sid, a in manifest.audio.items()} == SCENE_AUDIO_KEYS[provider]
    # no gains in a classic SceneAudio (R-41's gains are B3-voiced only)
    assert all(not a.speaker_gains for a in manifest.audio.values())


def test_the_audio_constants() -> None:
    from aadhi.pipeline import audio
    from aadhi.schemas import manifest

    assert audio.AUDIO_VERSION == AUDIO_VERSION
    assert audio.KEEP_PAD_SECONDS == KEEP_PAD_SECONDS
    assert manifest.INTER_BEAT_GAP_SECONDS == INTER_BEAT_GAP_SECONDS


def test_the_built_in_sounds() -> None:
    from aadhi.compose import sounds

    assert sounds.SOUND_VERSION == SOUNDS["version"] and sounds.SOUND_KIND == SOUNDS["kind"]
    assert sounds.SOUND_NAMES == ("tick", "ding")
    assert sounds.TICK_KEY == SOUNDS["tick_key"] and sounds.DING_KEY == SOUNDS["ding_key"]
    assert hashlib.sha256(sounds.tick_wav()).hexdigest() == SOUNDS["tick_sha256"]
    assert hashlib.sha256(sounds.ding_wav()).hexdigest() == SOUNDS["ding_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("rate", "speed"), [("+0%", None), ("+10%", 1.1), ("-8%", 0.92)])
async def test_the_openai_request_without_a_style(rate: str, speed: float | None) -> None:
    """``gpt-4o-mini-tts`` gets exactly ``model, voice, input, response_format`` (+ ``speed`` off 1.0): no
    ``instructions`` while no style is given (B3a sends one only with a non-empty style, B3 plan §3.1 item 2)."""
    from aadhi.config import Settings
    from aadhi.providers.tts.audio_utils import pcm_to_wav
    from aadhi.providers.tts.openai import OpenAITTS
    from tests.providers.fakes import FakeOpenAIClient

    async def no_sleep(_: float) -> None:
        return None

    wav = pcm_to_wav(struct.pack("<24000h", *([0] * 24000)), sample_rate=24000)
    settings = Settings(_env_file=None, openai_api_key="sk-test-123456789", openai_tts_model="gpt-4o-mini-tts")
    client = FakeOpenAIClient(audio=wav)
    await OpenAITTS(settings, client_factory=lambda: client, sleep=no_sleep).synthesize(
        "Hello", voice="nova", language="en-IN", rate=rate)
    expected: dict[str, Any] = {"model": "gpt-4o-mini-tts", "voice": "nova", "input": "Hello", "response_format": "mp3"}
    if speed is not None:
        expected["speed"] = speed
    assert client.audio.speech.calls == [expected]
