"""The flag-off gate (B3 plan §A B3-0 task 3, §3.1 item 8, critique C1): a presentation while the Dynamic mode is off
is a CLASSIC lecture, for the audio too.

A screenplay WITH a presentation, built while ``dynamic_enabled`` is False, must give the same ``clip_key``s, the same
``scene_audio`` keys, the same ``SceneAudio`` and the same timeline (every audio field included: ``music`` absent, the
branding BGM / tick / ding, the narration offsets) as the same screenplay without a presentation. "Off" is checked both
ways the mode can be off: the environment's ``DYNAMIC_ENABLED=false``, and the environment on with the administrator's
runtime switch (``app_settings``) off. The presentation used is a B3 one (a calibrated cast, procedural music), the
shape B3's voicing and music branches act on, so the gate proves those branches stay behind ``is_dynamic``; a B2-era
presentation is checked too. A positive control shows the mode really is on without the switch (the gate is live).
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from aadhi import runtime_flags
from aadhi.compose.presentation import b3_music, b3_voiced, dynamic_enabled
from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.screenplay import Presentation, Screenplay
from tests.pipeline.fakes import FakeTTS, install_fast_audio, install_providers

LECTURE: dict[str, Any] = {
    "language": "en-IN",
    "subject_name": "Physics", "unit_name": "Waves", "session_number": "1", "session_title": "Wave speed",
    "lexicon": [{"written": "SI", "spoken": "S I"}],
    "scenes": [
        {"id": "intro", "type": "content", "title": "Waves",
         "board": [{"id": "intro-h", "kind": "heading", "text": "Wave speed"},
                   {"id": "intro-f", "kind": "formula", "latex": "v = f \\lambda"}],
         "beats": [{"id": "intro-1", "narration": "A wave carries energy, not matter.", "board_item_id": "intro-h"},
                   {"id": "intro-2", "narration": "Its speed in SI units is metres per second.",
                    "board_item_id": "intro-f", "pause_after": 0.4}]},
        {"id": "card", "type": "chapter_card", "title": "Part two", "beats": [{"id": "card-1",
                                                                              "narration": "Part two."}]},
        {"id": "quiz", "type": "quiz_checkpoint", "title": "Check", "question": "What does a wave carry?",
         "options": ["Matter", "Energy"], "correct_index": 1, "countdown_seconds": 5,
         "beats": [{"id": "quiz-1", "narration": "What does a wave carry?"}],
         "reveal_beats": [{"id": "quiz-2", "narration": "Energy, not matter."}]},
    ],
}


def b3_presentation() -> dict[str, Any]:
    """A B3-shaped presentation: a calibrated persona cast and a procedural bed (both markers say B3)."""
    from aadhi.compose.dynamic.authoring import default_presentation

    p = default_presentation(subject_name="Physics", unit_name="Waves", session_number="1",
                             session_title="Wave speed", tts_provider="openai", language="en-IN")
    data = p.model_dump(mode="json")
    data["voices"] = {"narrator": "openai:marin", "student": "openai:ash",
                      "calibration": {"openai:marin": {"natural_wpm": 162.0, "control": "wpm=155"},
                                      "openai:ash": {"natural_wpm": 158.0, "control": "wpm=155"}}}
    data["music"] = {"source": "procedural", "provider": "procedural", "mood": "calm_focus",
                     "asset_key": "music-0123456789abcdef0123456789abcdef01234567", "mode": "bed"}
    out = Presentation.model_validate(data)
    assert b3_voiced(out) and b3_music(out)
    return out.model_dump(mode="json")


def b2_presentation() -> dict[str, Any]:
    from aadhi.compose.dynamic.authoring import default_presentation

    p = default_presentation(subject_name="Physics", unit_name="Waves", session_number="1",
                             session_title="Wave speed", tts_provider="edge", language="en-IN")
    assert not b3_voiced(p) and not b3_music(p)
    return p.model_dump(mode="json")


PRESENTATIONS = {"b3": b3_presentation, "b2_era": b2_presentation}


def _normalised(audio: dict[str, Any]) -> dict[str, Any]:
    """SceneAudio dumps with the store's random storage names blanked (the asset keys stay)."""
    out = copy.deepcopy(audio)
    for scene in out.values():
        if scene.get("storage_key"):
            scene["storage_key"] = "<storage>"
    return out


@pytest.fixture()
def gate(job_ctx, monkeypatch):
    install_providers(monkeypatch)
    install_fast_audio(monkeypatch)
    tts = FakeTTS(name="openai")  # narrates as the server's OpenAI provider would (keys carry provider and model)
    from aadhi.pipeline import integrations

    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: tts)
    job_ctx.settings = job_ctx.settings.model_copy(update={
        "tts_provider": "openai", "openai_tts_model": "gpt-4o-mini-tts", "image_provider": "none",
        "video_provider": "none"})
    runtime_flags.invalidate()
    yield {"ctx": job_ctx, "tts": tts}
    runtime_flags.invalidate()


def _switch_off(mode: str, ctx: Any, db_session: Any) -> Any:
    """Settings with the Dynamic mode off the way ``mode`` names (and the switch stored for "runtime")."""
    if mode == "environment":
        return ctx.settings.model_copy(update={"dynamic_enabled": False})
    settings = ctx.settings.model_copy(update={"dynamic_enabled": True})
    runtime_flags.set_flag(db_session, "dynamic_enabled", False)
    db_session.commit()
    return settings


def _build(ctx: Any, sp: Screenplay, settings: Any) -> dict[str, Any]:
    ctx.settings = settings
    res = asyncio.run(build_assets_detailed(ctx, sp, GenerationOptions(tts_voice="nova")))
    assert not [i for i in res.issues if i.severity == "error"], res.issues
    m = res.manifest
    return {
        "clips": {sid: [b.clip_asset_key for b in a.beats] for sid, a in m.audio.items()},
        "scene_audio": {sid: a.asset_key for sid, a in m.audio.items()},
        "audio": _normalised(m.model_dump(mode="json")["audio"]),
        "manifest": m,
    }


@pytest.mark.parametrize("which", sorted(PRESENTATIONS))
@pytest.mark.parametrize("mode", ["environment", "runtime"])
def test_a_presentation_with_the_mode_off_is_voiced_and_timed_as_classic(gate, db_session, mode: str,
                                                                         which: str) -> None:
    ctx = gate["ctx"]
    classic = Screenplay.model_validate(LECTURE)
    with_presentation = Screenplay.model_validate({**LECTURE, "presentation": PRESENTATIONS[which]()})
    off = _switch_off(mode, ctx, db_session)
    assert dynamic_enabled(off) is False

    a = _build(ctx, classic, off)
    gate["tts"].texts.clear()
    b = _build(ctx, with_presentation, off)
    assert b["clips"] == a["clips"] and all(all(keys) for keys in a["clips"].values())
    assert b["scene_audio"] == a["scene_audio"]
    assert b["audio"] == a["audio"]
    # and the clip keys are the classic ones: provider, voice, model, rate - nothing of the presentation's cast
    assert {(x["provider"], x["voice"]) for x in b["audio"].values()} == {("openai", "nova")}

    for build in (build_timeline, preview_timeline):
        ta = build(classic, a["manifest"], settings=off).model_dump(mode="json")
        tb = build(with_presentation, b["manifest"], settings=off).model_dump(mode="json")
        assert "music" not in tb and "sfx" not in tb and "presentation" not in tb
        assert tb == ta


def test_the_gate_is_live_with_the_mode_on(gate) -> None:
    """Positive control: the same presentation with the mode on (no switch stored) builds a Dynamic timeline, so the
    equalities above are about the switch, not about a presentation that is ignored anyway."""
    ctx = gate["ctx"]
    on = ctx.settings.model_copy(update={"dynamic_enabled": True})
    assert dynamic_enabled(on) is True
    sp = Screenplay.model_validate({**LECTURE, "presentation": b3_presentation()})
    built = _build(ctx, sp, on)
    timeline = build_timeline(sp, built["manifest"], settings=on)
    assert timeline.presentation is not None
    classic = build_timeline(Screenplay.model_validate(LECTURE), built["manifest"], settings=on)
    assert timeline.model_dump(mode="json") != classic.model_dump(mode="json")
