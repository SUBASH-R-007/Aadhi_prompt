"""O17, the stored-voice freeze (B3 plan §A B3-0 task 3, §5 O17; owner answer 2026-10-17: FREEZE).

A built lecture keeps the voice it was made with, including one built keyless on Edge; moving it to OpenAI is an
explicit teacher re-voice. Concretely: a stored classic lecture whose options name no provider
(``GenerationOptions.tts_provider is None``: the server's ``TTS_PROVIDER`` narrated it), rebuilt after
``TTS_PROVIDER`` flips, must make ZERO TTS calls and keep its stored ``SceneAudio.provider/voice``.

Today a rebuild re-voices it: ``assets._Builder._reusable`` compares the stored ``(provider, voice)`` with the build's
voice, which follows the new ``TTS_PROVIDER``, so every scene is synthesised again on the new provider (paid, on
OpenAI). The freeze belongs to the ``tts-openai`` branch, which B3 cannot change; until it lands this test is a strict
``xfail``: it must fail, and only on its O17 assertions (``raises=AssertionError``). An unexpected pass is an error
too, saying that the freeze arrived and the mark must go. The setup (a stored lecture built keyless on Edge, with no
provider named in its options) is checked by :func:`stored_lecture`, which raises :class:`SetupError`, not an
``AssertionError``, so a setup that breaks (for example after ``tts-openai`` changes how a keyless build is narrated)
is reported as a real failure and is never swallowed as the expected xfail. B3a does not start until it passes or the
owner answers O17 otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.pipeline import integrations
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.manifest import AssetManifest
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.fakes import FakeTTS, install_fast_audio, install_providers

LECTURE: dict[str, Any] = {
    "language": "en-IN",
    "scenes": [
        {"id": "a", "type": "content", "title": "A", "beats": [{"id": "a1", "narration": "Current flows round a loop."},
                                                                {"id": "a2", "narration": "Resistance slows it."}]},
        {"id": "q", "type": "quiz_checkpoint", "title": "Q", "question": "Which?", "options": ["x", "y"],
         "correct_index": 0, "countdown_seconds": 4, "beats": [{"id": "q1", "narration": "Which one is right?"}],
         "reveal_beats": [{"id": "q2", "narration": "The first one."}]},
    ],
}


class SetupError(RuntimeError):
    """The O17 scenario could not be set up: a real error, never the expected xfail (which is an AssertionError)."""


def _require(ok: object, what: str) -> None:
    if not ok:
        raise SetupError(f"O17 setup broken: {what}")


def stored_lecture(job_ctx: Any, monkeypatch: Any, options: GenerationOptions) -> tuple[dict[str, FakeTTS], Screenplay,
                                                                                         AssetManifest, dict[str, Any]]:
    """A classic lecture built while the server's ``TTS_PROVIDER`` was Edge (keyless), its options naming no provider:
    the fakes, the screenplay, the stored manifest and the settings the rebuild starts from."""
    install_providers(monkeypatch)
    install_fast_audio(monkeypatch)
    fakes = {"edge": FakeTTS(name="edge"), "openai": FakeTTS(name="openai")}
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: fakes[name or settings.tts_provider])
    sp = Screenplay.model_validate(LECTURE)
    _require(options.tts_provider is None, "the stored options name a provider (O17 is about those that name none)")
    base = {"tts_voice": "", "image_provider": "none", "video_provider": "none", "openai_tts_model": "gpt-4o-mini-tts"}
    job_ctx.settings = job_ctx.settings.model_copy(update={**base, "tts_provider": "edge"})
    stored = asyncio.run(build_assets_detailed(job_ctx, sp, options)).manifest
    _require({a.provider for a in stored.audio.values()} == {"edge"} and fakes["edge"].texts,
             "the keyless build was not narrated on Edge")
    return fakes, sp, stored, base


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "O17 FREEZE is not implemented yet: it lands with the tts-openai branch (a rebuild must reuse the stored "
    "SceneAudio.provider/voice when the stored options name no provider); today a TTS_PROVIDER flip re-voices "
    "stored lectures"))
def test_a_stored_lecture_keeps_its_voice_after_tts_provider_flips(job_ctx, monkeypatch) -> None:
    options = GenerationOptions()  # no provider named: the server's TTS_PROVIDER narrated it (Edge, keyless)
    fakes, sp, stored, base = stored_lecture(job_ctx, monkeypatch, options)

    for tts in fakes.values():
        tts.texts.clear()
    job_ctx.settings = job_ctx.settings.model_copy(update={**base, "tts_provider": "openai"})  # the default flips
    rebuilt = asyncio.run(build_assets_detailed(job_ctx, sp, options, previous=stored)).manifest

    assert fakes["openai"].texts == [] and fakes["edge"].texts == [], "the stored lecture was re-voiced"
    assert {(a.provider, a.voice) for a in rebuilt.audio.values()} == {
        (a.provider, a.voice) for a in stored.audio.values()}
    assert rebuilt.model_dump(mode="json")["audio"] == stored.model_dump(mode="json")["audio"]


def test_a_broken_setup_is_an_error_not_the_expected_xfail(job_ctx, monkeypatch) -> None:
    """The setup's checks raise ``SetupError`` (not an ``AssertionError``, which the xfail mark would accept), so a
    broken setup fails the O17 test instead of passing as its expected failure."""
    with pytest.raises(SetupError, match="name a provider"):
        stored_lecture(job_ctx, monkeypatch, GenerationOptions(tts_provider="edge"))
    assert not issubclass(SetupError, AssertionError)
