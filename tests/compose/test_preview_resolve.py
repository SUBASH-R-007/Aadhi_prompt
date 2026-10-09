"""preview_timeline (reuse vs estimate) and resolve_urls (serve-time URL filling)."""

from __future__ import annotations

import sys
import types

import pytest

from aadhi.compose import timeline as timeline_mod
from aadhi.compose.sounds import DING_KEY, TICK_KEY, ensure_sounds
from aadhi.compose.timeline import STORAGE_KEYS_META, build_timeline, preview_timeline, resolve_urls
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline
from aadhi.storage.assets import Produced

from .factories import SPEECH, make_manifest, make_screenplay, screenplay_dict


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def scene(tl: Timeline, sid: str):
    return next(s for s in tl.scenes if s.scene_id == sid)


def edited(mutate) -> Screenplay:
    data = screenplay_dict()
    mutate(data)
    return Screenplay.model_validate(data)


def board_scene(data: dict) -> dict:
    return next(s for s in data["scenes"] if s["id"] == "s-board")


# --- preview ------------------------------------------------------------------------------------


def test_preview_unchanged_reuses_audio(settings: Settings) -> None:
    sp = make_screenplay()
    manifest = make_manifest(sp)
    prev = preview_timeline(sp, manifest, settings=settings, version_id=5)
    built = build_timeline(sp, manifest, settings=settings, version_id=5)
    assert prev.estimated
    assert prev.version_id == 5 and prev.screenplay_revision is None
    s = scene(prev, "s-board")
    assert s.audio_asset_key == "scene_audio-s-board"
    assert not any(b.estimated for b in s.beats)
    assert [b.start for b in s.beats] == [b.start for b in scene(built, "s-board").beats]
    assert scene(prev, "s-noaudio").beats[0].estimated
    Timeline.model_validate(prev.model_dump())


def test_preview_edited_narration_estimates_only_that_beat(settings: Settings) -> None:
    sp0 = make_screenplay()
    manifest = make_manifest(sp0)

    def mutate(d: dict) -> None:
        board_scene(d)["beats"][1]["narration"] = "We write this relation as V equals I times R, always."

    prev = preview_timeline(edited(mutate), manifest, settings=settings)
    s = scene(prev, "s-board")
    assert s.audio_asset_key is None  # the narration file no longer matches
    assert [b.estimated for b in s.beats] == [False, True, False, False, False]
    assert s.beats[0].speech_end - s.beats[0].start == pytest.approx(SPEECH)
    changed = s.beats[1]
    assert changed.speech_end - changed.start == pytest.approx(len(changed.narration) * 0.065, abs=1e-3)
    # reused beat words are shifted to the new layout
    assert s.beats[2].words[0].start == pytest.approx(s.beats[2].start, abs=1e-3)


def test_preview_pause_change_drops_audio(settings: Settings) -> None:
    manifest = make_manifest(make_screenplay())

    def mutate(d: dict) -> None:
        board_scene(d)["beats"][0]["pause_after"] = 2.0

    s = scene(preview_timeline(edited(mutate), manifest, settings=settings), "s-board")
    assert s.audio_asset_key is None
    assert not any(b.estimated for b in s.beats)  # durations all reused
    assert s.beats[0].end - s.beats[0].speech_end == pytest.approx(2.0)


def test_preview_beat_reorder_or_new_beat(settings: Settings) -> None:
    manifest = make_manifest(make_screenplay())

    def mutate(d: dict) -> None:
        board_scene(d)["beats"].append({"id": "s-board-b9", "narration": "One more thing."})

    s = scene(preview_timeline(edited(mutate), manifest, settings=settings), "s-board")
    assert s.audio_asset_key is None and s.beats[-1].estimated and not s.beats[0].estimated


def test_preview_uses_scene_hash_when_available(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = make_screenplay()
    manifest = make_manifest(sp)
    # wipe spoken text: only the scene hash can prove the audio still matches
    audio = manifest.audio["s-board"]
    manifest.audio["s-board"] = audio.model_copy(update={"beats": [b.model_copy(update={"spoken_text": ""})
                                                                    for b in audio.beats]})
    assert scene(preview_timeline(sp, manifest, settings=settings), "s-board").audio_asset_key is None
    fake = types.ModuleType("aadhi.pipeline.assets")
    fake.scene_hash = lambda sc: f"hash-{sc.id}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aadhi.pipeline.assets", fake)
    timeline_mod._scene_hasher.cache_clear()
    manifest.scene_hashes["s-board"] = "hash-s-board"
    try:
        assert scene(preview_timeline(sp, manifest, settings=settings), "s-board").audio_asset_key ==             "scene_audio-s-board"
    finally:
        timeline_mod._scene_hasher.cache_clear()


def test_preview_without_manifest(settings: Settings) -> None:
    prev = preview_timeline(make_screenplay(), None, settings=settings, include_intro=False)
    assert prev.estimated and prev.intro is None
    assert all(s.audio_asset_key is None for s in prev.scenes)


# --- resolve_urls -------------------------------------------------------------------------------


def test_resolve_urls_creates_sounds_on_first_use(app_env, asset_store) -> None:
    sp = make_screenplay()
    out = resolve_urls(build_timeline(sp, make_manifest(sp), settings=app_env), asset_store)
    assert out.branding.tick_url.startswith("/media/assets/scene_audio/") and out.branding.tick_url.endswith(".wav")
    assert asset_store.get(TICK_KEY) is not None and asset_store.get(DING_KEY) is not None


def test_resolve_urls_fills_everything_with_one_query(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    ensure_sounds(asset_store)  # steady state: the sound effects exist
    calls: list[list[str]] = []
    real = asset_store.get_many

    def counting(keys):
        keys = list(keys)
        calls.append(keys)
        return real(keys)

    monkeypatch.setattr(asset_store, "get_many", counting)
    out = resolve_urls(tl, asset_store)
    assert len(calls) == 1  # one lookup (only the sound effects are not in the storage-key map)
    assert set(calls[0]) == {TICK_KEY, DING_KEY}
    s = scene(out, "s-board")
    assert s.audio_url == "/media/assets/scene_audio/scene_audio-s-board/x.mp3"
    assert s.figures["fig"].url == "/media/assets/figure/figure-aaa/f.png"
    assert s.side_panel.media.url == "/media/assets/figure/figure-aaa/f.png"
    assert scene(out, "s-sim").media.url == "/media/assets/manim/manim-sim/m.mp4"
    assert scene(out, "s-interactive").poster.url.endswith("/p.png")
    assert scene(out, "s-noaudio").side_panel.media.url.startswith("https://media.giphy.com/")
    assert STORAGE_KEYS_META not in out.meta and STORAGE_KEYS_META in tl.meta  # input untouched
    assert scene(tl, "s-board").audio_url is None
    assert out.branding.tick_url.startswith("/media/assets/scene_audio/") and out.branding.tick_url.endswith(".wav")
    assert out.branding.ding_url.startswith("/media/assets/scene_audio/")
    assert out.branding.mascot_clips["left"] == "/branding/aadhi_left_clean.mp4"
    Timeline.model_validate(out.model_dump())


def test_resolve_urls_looks_up_unlisted_keys_and_fills_metadata(app_env, asset_store) -> None:
    data = screenplay_dict()
    sim = next(s for s in data["scenes"] if s["id"] == "s-sim-fallback")
    sim["override_asset_key"] = "upload-video-1"
    sp = Screenplay.model_validate(data)
    asset_store.put("upload-video-1", "upload", Produced(data=b"\x00" * 16, mime="video/mp4", duration_s=3.5,
                                                         width=640, height=360))
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    assert "upload-video-1" not in tl.meta[STORAGE_KEYS_META]
    out = resolve_urls(tl, asset_store)
    m = scene(out, "s-sim-fallback").media
    assert m.url.startswith("/media/assets/upload/upload-video-1/") and m.url.endswith(".mp4")
    assert m.mime == "video/mp4" and m.width == 640 and m.duration == 3.5


def test_resolve_urls_unknown_assets_get_none(app_env, asset_store) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    tl.meta[STORAGE_KEYS_META] = {}
    out = resolve_urls(tl, asset_store)
    assert scene(out, "s-board").audio_url is None
    assert scene(out, "s-sim").media.url is None


def test_resolve_urls_signed(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    seen: list[tuple[str, int]] = []

    def signed(key: str, ttl: int, *, download_name=None) -> str:
        seen.append((key, ttl))
        return f"https://signed.example/{key}?sig=1"

    monkeypatch.setattr(asset_store.storage, "signed_url", signed)
    out = resolve_urls(tl, asset_store, signed=True)
    assert scene(out, "s-sim").media.url == "https://signed.example/assets/manim/manim-sim/m.mp4?sig=1"
    assert all(ttl == app_env.s3_presign_ttl_seconds for _, ttl in seen)


def test_resolve_urls_keeps_existing_branding_sounds(app_env, asset_store, monkeypatch) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    tl.branding.tick_url = "/branding/tick.wav"
    tl.branding.ding_url = "/branding/ding.wav"
    calls: list[list[str]] = []
    monkeypatch.setattr(asset_store, "get_many", lambda keys: calls.append(list(keys)) or {})
    out = resolve_urls(tl, asset_store)
    assert calls == []  # every key was in the storage map: no query at all
    assert out.branding.tick_url == "/branding/tick.wav"


def test_resolve_urls_serves_the_replacement_of_a_retired_mascot_clip(app_env, asset_store) -> None:
    """Timelines stored (lectures published) before aadhi_left.mp4 was re-encoded without its black bars
    still name the old file: serving maps it to the clean clip, without a rebuild."""
    from aadhi.compose.base import RETIRED_MASCOT_CLIPS
    from aadhi.compose.timeline import current_clip_url

    # batch 2: the four Veo clips were re-encoded without their watermark (<name>_clean.mp4)
    assert RETIRED_MASCOT_CLIPS == {
        "aadhi_left.mp4": "aadhi_left_clean.mp4", "aadhi_right.mp4": "aadhi_right_clean.mp4",
        "aadhi_center.mp4": "aadhi_center_clean.mp4", "aadhi_popup.mp4": "aadhi_popup_clean.mp4",
        "no_aadhi.mp4": "no_aadhi_clean.mp4",
    }
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    clips = {**tl.branding.mascot_clips, "left": "/branding/aadhi_left.mp4", "custom": "/media/x/aadhi_left.mp4",
             "hidden": "/branding/no_aadhi.mp4"}
    tl.branding.mascot_clips = clips
    out = resolve_urls(tl, asset_store)
    assert out.branding.mascot_clips["left"] == "/branding/aadhi_left_clean.mp4"
    assert out.branding.mascot_clips["hidden"] == "/branding/no_aadhi_clean.mp4"
    assert out.branding.mascot_clips["custom"] == "/media/x/aadhi_left.mp4"  # only branding paths are mapped
    assert out.branding.mascot_clips["right"] == "/branding/aadhi_right_clean.mp4"  # current clip: unchanged
    assert tl.branding.mascot_clips["left"] == "/branding/aadhi_left.mp4"  # the stored copy is untouched
    assert current_clip_url("/branding/aadhi_center.mp4") == "/branding/aadhi_center_clean.mp4"
    assert current_clip_url("/branding/aadhi_center_clean.mp4") == "/branding/aadhi_center_clean.mp4"
    assert current_clip_url("/branding/logo_animation.mp4") == "/branding/logo_animation.mp4"


def test_resolve_urls_serves_the_clean_intro_logo_to_a_stored_timeline(app_env, asset_store) -> None:
    """Timelines stored before the intro logo lost its sparkle mark name logo_animation.mp4: served as the
    clean file without a rebuild (RETIRED_BRANDING_FILES); the stored copy is untouched."""
    from aadhi.compose.base import LOGO_VIDEO, RETIRED_BRANDING_FILES
    from aadhi.compose.timeline import current_branding_url

    assert RETIRED_BRANDING_FILES == {"logo_animation.mp4": "logo_animation_clean.mp4"}
    assert LOGO_VIDEO == "logo_animation_clean.mp4" and LOGO_VIDEO not in RETIRED_BRANDING_FILES
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=app_env)
    assert tl.intro is not None
    tl.intro.logo_video_url = "/branding/logo_animation.mp4"
    out = resolve_urls(tl, asset_store)
    assert out.intro is not None and out.intro.logo_video_url == "/branding/logo_animation_clean.mp4"
    assert tl.intro.logo_video_url == "/branding/logo_animation.mp4"
    assert current_branding_url("/branding/logo_animation_clean.mp4") == "/branding/logo_animation_clean.mp4"
    assert current_branding_url("/media/x/logo_animation.mp4") == "/media/x/logo_animation.mp4"
    assert current_branding_url("/branding/aadhi_left.mp4") == "/branding/aadhi_left.mp4"  # mascot map is separate
    tl.intro.logo_video_url = None
    assert resolve_urls(tl, asset_store).intro.logo_video_url is None
