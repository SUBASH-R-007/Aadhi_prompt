"""The legacy gate (B3 plan §A B3-0 task 3, §3.1b, owner decision O-B9): a stored B2-era Dynamic lecture is voiced and
mixed by today's code, at every B3 step, until the teacher acts.

``tests/fixtures/b3/b2_era/`` holds such a lecture in two parts:

* **Frozen inputs** (``screenplay.json``, ``options.json``, ``manifest.json``), written once on 2026-10-17 at
  ``dd8c2d4`` and never regenerated, because they stand for a lecture that is already stored. The screenplay has B2's
  default presentation (a bare narrator id, no calibration, the placeholder ``MusicChoice(source="off")``), and the
  manifest is the one the fake TTS built for it. Nothing writes them: no update mode and no builder (restore them from
  git if they are ever lost).
* **Derived goldens** (``timeline.json``, ``master_audio.json``): what today's code makes from the FROZEN inputs (the
  timeline, and the B1 master audio that the Dynamic MP4 still carries). They are regenerated from those inputs, never
  by rebuilding the inputs, with ``python -m tests.fixtures.b3.build b2_era`` after B2 changes what a Dynamic timeline
  holds (with the change named in the commit).

After every B3 step:

* a fresh build of the frozen screenplay gives the frozen AUDIO: identical clip and ``scene_audio`` keys and identical
  ``SceneAudio`` (the store's random storage names aside). Media keys are B2's and are not part of this gate;
* a rebuild on the frozen manifest, with a store that holds exactly the frozen assets (not today's build of them),
  makes ZERO TTS calls and returns the frozen audio unchanged. If B3 re-keyed a B2-era clip, this would re-synthesise
  every stored lecture, and the gate fails (``test_the_rebuild_gate_sees_a_re_key``);
* the timeline of the frozen inputs has no ``MusicPlan`` (``music`` absent, ``sfx`` empty: the B1 mix plays), and its
  per-scene audio is the frozen manifest's. The whole timeline equals its derived golden;
* the B1 master (per-scene narration in timeline order, the quiz tick and ding, and the ducked branding BGM, with no
  other music) is the master of that timeline, and it equals its derived golden.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import shutil
from typing import Any

import pytest

from aadhi import runtime_flags
from aadhi.compose.presentation import b3_music, b3_voiced
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.manifest import AssetManifest
from aadhi.schemas.screenplay import MusicChoice, Screenplay
from aadhi.schemas.timeline import Timeline
from tests.fixtures.b3 import build as b3
from tests.pipeline.fakes import install_fast_audio, install_providers

REGENERATE = f"a derived golden: regenerate it from the frozen inputs with python -m tests.fixtures.b3.build {b3.LEGACY}"


@pytest.fixture()
def legacy(job_ctx, monkeypatch):
    providers = install_providers(monkeypatch)
    install_fast_audio(monkeypatch)
    job_ctx.settings = job_ctx.settings.model_copy(update={
        "tts_provider": "fake", "tts_voice": "", "tts_rate": "+0%", "image_provider": "fake", "video_provider": "none",
        "dynamic_enabled": True})
    runtime_flags.invalidate()  # no administrator's switch cached by an earlier test
    yield {"ctx": job_ctx, "providers": providers}
    runtime_flags.invalidate()


@pytest.fixture()
def frozen() -> dict[str, Any]:
    sp, options, manifest = b3.legacy_inputs()
    return {"sp": sp, "options": options, "manifest": manifest}


def _build(env: dict[str, Any], sp: Screenplay, options: GenerationOptions,
           previous: AssetManifest | None = None) -> AssetManifest:
    res = asyncio.run(build_assets_detailed(env["ctx"], sp, options, previous=previous))
    assert not [i for i in res.issues if i.severity == "error"], res.issues
    return res.manifest


def _audio(manifest: AssetManifest, *, storage: bool = True) -> dict[str, Any]:
    """The manifest's audio as JSON (``storage=False``: the store's random storage names blanked)."""
    out = copy.deepcopy(manifest.model_dump(mode="json")["audio"])
    if not storage:
        for audio in out.values():
            if audio.get("storage_key"):
                audio["storage_key"] = "<storage>"
    return out


def _seed_store(ctx: Any, manifest: AssetManifest) -> set[str]:
    """Put into the (empty) store exactly the assets ``manifest`` names, under their frozen keys and storage keys: the
    state of a server that holds the stored lecture. Narration files get the bytes the fake build stored (dummy MP3s);
    each scene's ``Asset.meta`` carries its beats and countdown as the asset build writes them."""
    from aadhi.db import session_scope
    from aadhi.models import Asset
    from aadhi.storage.assets import storage_key_for

    rows: dict[str, dict[str, Any]] = {}

    def add(key: str, kind: str, storage_key: str, mime: str, **values: Any) -> None:
        if key not in rows:
            rows[key] = {"kind": kind, "storage_key": storage_key, "mime": mime, **values}

    for audio in manifest.audio.values():
        if audio.asset_key and audio.storage_key:
            meta: dict[str, Any] = {"beats": [b.model_dump(mode="json") for b in audio.beats],
                                    "countdown_start": audio.countdown_start}
            if audio.envelope:
                meta.update(envelope=audio.envelope, envelope_fps=audio.envelope_fps)
            add(audio.asset_key, "scene_audio", audio.storage_key, audio.mime, duration_s=audio.duration, meta=meta)
        for beat in audio.beats:
            if beat.clip_asset_key:
                add(beat.clip_asset_key, "tts", storage_key_for("tts", beat.clip_asset_key, "audio/wav"), "audio/wav",
                    meta={"provider": audio.provider, "voice": audio.voice})
    for media in manifest.media.values():
        for info in (media.main, media.side_panel, media.poster, *media.figures.values()):
            if info is not None and info.asset_key and info.storage_key:
                add(info.asset_key, info.storage_key.split("/")[1], info.storage_key, info.mime,
                    width=info.width, height=info.height, duration_s=info.duration, meta={})
    with session_scope() as db:
        for key, row in rows.items():
            data = b"ID3\x03\x00fake" + hashlib.sha256(key.encode()).digest()
            ctx.assets.storage.put_bytes(row["storage_key"], data, row["mime"])
            db.add(Asset(key=key, size_bytes=len(data), **row))
    return set(rows)


def test_the_fixture_is_a_b2_era_lecture(frozen) -> None:
    p = frozen["sp"].presentation
    assert p is not None
    assert ":" not in p.voices.narrator and p.voices.calibration == {} and p.voices.student is None
    assert p.music == MusicChoice(source="off")
    assert b3_voiced(p) is False and b3_music(p) is False
    assert frozen["options"] == GenerationOptions()
    quiz = frozen["manifest"].audio["check"]
    assert quiz.countdown_seconds == 5 and [b.phase for b in quiz.beats] == ["main", "reveal"]
    assert all(a.provider == "fake" for a in frozen["manifest"].audio.values())


def test_a_fresh_build_gives_the_frozen_keys_and_scene_audio(legacy, frozen) -> None:
    manifest = _build(legacy, frozen["sp"], frozen["options"])
    assert legacy["providers"].tts.texts, "a fresh store synthesises the narration"
    now, stored = _audio(manifest, storage=False), _audio(frozen["manifest"], storage=False)
    assert {s: [b["clip_asset_key"] for b in a["beats"]] for s, a in now.items()} == {
        s: [b["clip_asset_key"] for b in a["beats"]] for s, a in stored.items()}
    assert {s: a["asset_key"] for s, a in now.items()} == {s: a["asset_key"] for s, a in stored.items()}
    assert now == stored


def test_a_rebuild_on_the_stored_manifest_makes_no_tts_call(legacy, frozen) -> None:
    seeded = _seed_store(legacy["ctx"], frozen["manifest"])  # the server holds the stored lecture: only its assets
    assert {b.clip_asset_key for a in frozen["manifest"].audio.values() for b in a.beats} <= seeded
    rebuilt = _build(legacy, frozen["sp"], frozen["options"], previous=frozen["manifest"])
    assert legacy["providers"].tts.texts == [], "a B2-era lecture was re-synthesised"
    assert _audio(rebuilt) == _audio(frozen["manifest"])


def test_the_rebuild_gate_sees_a_re_key(legacy, frozen, monkeypatch) -> None:
    """The gate's own check: had B3 changed the clip key of a B2-era beat, the rebuild above would synthesise again
    (the store holds only the frozen keys' files). A store filled by today's build would hide that."""
    from aadhi.pipeline import assets
    from aadhi.storage.assets import compute_key

    original = assets._Builder.clip_key

    def re_keyed(self: Any, text: str, voice: Any = None) -> str:
        return compute_key("tts", {"re_keyed": original(self, text, voice)})

    _seed_store(legacy["ctx"], frozen["manifest"])
    monkeypatch.setattr(assets._Builder, "clip_key", re_keyed)
    _build(legacy, frozen["sp"], frozen["options"], previous=frozen["manifest"])
    beats = sum(len(a.beats) for a in frozen["manifest"].audio.values())
    assert len(legacy["providers"].tts.texts) == beats


def test_the_timeline_of_the_stored_lecture_plays_its_audio_with_the_b1_mix(frozen) -> None:
    dump = b3.legacy_timeline()
    built = Timeline.model_validate(dump)
    assert built.presentation is not None and built.music is None and built.sfx == []
    assert "music" not in dump and "sfx" not in dump
    audio = frozen["manifest"].audio
    narrated = [s for s in built.scenes if s.scene_id in audio]
    assert {s.scene_id for s in narrated} == {s.id for s in frozen["sp"].scenes if s.all_beats()}
    for scene in narrated:
        stored = audio[scene.scene_id]
        assert (scene.audio_asset_key, scene.audio_duration) == (stored.asset_key, stored.duration), scene.scene_id
        assert [(b.beat_id, [w.text for w in b.words]) for b in scene.beats] == [
            (b.beat_id, [w.text for w in b.words]) for b in stored.beats], scene.scene_id
    assert dump == b3.load(b3.LEGACY, "timeline"), REGENERATE


def test_the_b1_master_audio_is_unchanged(frozen) -> None:
    timeline = Timeline.model_validate(b3.legacy_timeline())
    doc = b3.master_audio(timeline)
    commands = doc["commands"]
    narration = [a for c in commands for a in c["args"] if a.startswith("<work>/inputs/")]
    assert narration == [f"<work>/inputs/{s.audio_asset_key}.mp3" for s in timeline.scenes if s.audio_asset_key]
    assert {a.removeprefix("<work>/inputs/").removesuffix(".mp3") for a in narration} == {
        a.asset_key for a in frozen["manifest"].audio.values()}
    quiz_key = frozen["manifest"].audio["check"].asset_key
    with_sounds = [c["args"] for c in commands if "<work>/sfx_tick.wav" in c["args"]]
    assert len(with_sounds) == 1 and f"<work>/inputs/{quiz_key}.mp3" in with_sounds[0]
    assert "<work>/sfx_ding.wav" in with_sounds[0]
    master = commands[-1]
    assert [a for a in master["args"] if a.startswith("<")] == ["<branding>/bgm.mp3"]  # the one music: branding BGM
    assert "sidechaincompress" in master["files"]["master.filtergraph"]  # ducked under the narration
    assert doc == b3.load(b3.LEGACY, "master_audio"), REGENERATE


def test_the_builder_writes_only_the_derived_goldens(tmp_path, monkeypatch) -> None:
    """``python -m tests.fixtures.b3.build b2_era`` rewrites the timeline and master audio from the frozen inputs and
    never touches (or rebuilds) the inputs: a B2 change to ``default_presentation`` cannot turn the stored lecture
    into a new one."""
    from aadhi.compose.dynamic import authoring

    goldens = {d: b3.load(b3.LEGACY, d) for d in b3.LEGACY_DERIVED}
    copy_dir = tmp_path / "fixtures"
    shutil.copytree(b3.FIXTURE_DIR / b3.LEGACY, copy_dir / b3.LEGACY)
    monkeypatch.setattr(b3, "FIXTURE_DIR", copy_dir)

    def changed(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the builder rebuilt the frozen screenplay")

    monkeypatch.setattr(authoring, "default_presentation", changed)
    inputs = {d: b3.path(b3.LEGACY, d).read_bytes() for d in b3.LEGACY_INPUTS}
    for d in b3.LEGACY_DERIVED:
        b3.path(b3.LEGACY, d).write_text("{}\n", encoding="utf-8")
    assert b3.stale(b3.LEGACY) == [f"{b3.LEGACY}/{d}.json" for d in b3.LEGACY_DERIVED]
    assert b3.main([b3.LEGACY]) == 0
    assert {d: b3.path(b3.LEGACY, d).read_bytes() for d in b3.LEGACY_INPUTS} == inputs
    assert {d: b3.load(b3.LEGACY, d) for d in b3.LEGACY_DERIVED} == goldens
    assert b3.stale(b3.LEGACY) == []
