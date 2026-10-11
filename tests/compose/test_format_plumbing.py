"""Format plumbing (B0, DYNAMIC_VIDEO_DESIGN §2.1): stage and output sizes per format, segment specs, state
PNG sizes, the screenshot viewport, caption line width and aspect-aware QA edge sampling. The classic
(landscape) path must stay exactly as before: 1920x1080 stage, RENDER_WIDTH x RENDER_HEIGHT output."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from aadhi.compose import qa as qa_mod
from aadhi.compose.base import (
    STAGE_HEIGHT,
    STAGE_SIZES,
    STAGE_WIDTH,
    output_size,
    stage_size,
    timeline_stage,
)
from aadhi.compose.captions import MAX_LINE_CHARS, MAX_LINES, split_caption
from aadhi.compose.ffmpeg import run_ffmpeg
from aadhi.compose.qa import EDGE_SAMPLE, edge_sample_size, inspect_render
from aadhi.compose.screenshot import CaptureResult, IntroShot, StateShot, capture_render_frames
from aadhi.compose.timeline import build_timeline
from aadhi.compose.video import SceneInputs, _prepare_frames, intro_segment_spec, scene_segment_spec, segment_frames
from aadhi.config import Settings
from aadhi.schemas.timeline import TimedWord

from .factories import make_manifest, make_screenplay
from .stub_server import serve_stub


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_fps=30)


def _classic(settings: Settings):
    sp = make_screenplay()
    return build_timeline(sp, make_manifest(sp), settings=settings)


def _portrait(tl):
    """The classic test timeline on the portrait stage (the size ``build_timeline`` gives a portrait lecture)."""
    return tl.model_copy(update={"width": STAGE_HEIGHT, "height": STAGE_WIDTH})


# --- stage_size / output_size ----------------------------------------------------------------------


def test_stage_size_per_format() -> None:
    assert stage_size() == stage_size(None) == stage_size("landscape") == (1920, 1080) == (STAGE_WIDTH, STAGE_HEIGHT)
    assert stage_size("portrait") == (1080, 1920)
    assert set(STAGE_SIZES) == {"landscape", "portrait"}
    with pytest.raises(ValueError, match="unknown video format"):
        stage_size("square")


def test_classic_timeline_keeps_the_landscape_stage_and_render_size(settings: Settings) -> None:
    tl = _classic(settings)
    assert timeline_stage(tl) == (1920, 1080)
    assert output_size(tl, settings) == (settings.render_width, settings.render_height) == (1920, 1080)
    # every configured size is kept exactly for the classic stage, even an unusual one
    for w, h in [(1280, 720), (640, 360), (854, 480), (1919, 1081), (1080, 1920)]:
        custom = settings.model_copy(update={"render_width": w, "render_height": h})
        assert output_size(tl, custom) == (w, h)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [((1920, 1080), (1080, 1920)), ((1280, 720), (720, 1280)), ((640, 360), (360, 640)), ((854, 480), (480, 854)),
     ((3840, 2160), (2160, 3840))],
)
def test_portrait_output_keeps_the_configured_quality(settings: Settings, configured, expected) -> None:
    tl = _portrait(_classic(settings))
    custom = settings.model_copy(update={"render_width": configured[0], "render_height": configured[1]})
    assert timeline_stage(tl) == (1080, 1920)
    out = output_size(tl, custom)
    assert out == expected and out[0] % 2 == 0 and out[1] % 2 == 0  # 4:2:0 needs even sides


@pytest.mark.parametrize(("fmt", "stage"), [("portrait", (1080, 1920)), ("landscape", (1920, 1080))])
def test_the_timeline_stage_follows_the_lecture_format(settings: Settings, fmt: str, stage: tuple[int, int]) -> None:
    from tests.schemas.v3_fixtures import PRESENTATION

    presentation = {**PRESENTATION, "format": fmt, "hook": None,
                    "music": {"source": "procedural", "provider": "procedural"}}
    sp = make_screenplay(presentation=presentation)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    assert timeline_stage(tl) == stage == stage_size(fmt)
    assert timeline_stage(_classic(settings)) == (1920, 1080)  # no presentation: the classic stage


# --- segment specs and state PNGs ------------------------------------------------------------------


def _shots(scene, rect=None) -> list[StateShot]:
    times = [0.0] + [b.start for b in scene.beats]
    return [StateShot(t, f"k{i}", Path(f"frames/s_{i}.png"), rect, None, "contain") for i, t in enumerate(times)]


def test_classic_segment_specs_are_unchanged(settings: Settings) -> None:
    small = settings.model_copy(update={"render_width": 1280, "render_height": 720})
    tl = _classic(small)
    sim = next(s for s in tl.scenes if s.scene_id == "s-sim")
    frames = segment_frames(tl, 30)[sim.index + 1]
    rect = {"x": 576, "y": 160, "width": 1024, "height": 576}
    spec = scene_segment_spec(sim, _shots(sim, rect), timeline=tl, frames=frames, settings=small,
                              inputs=SceneInputs(media={"manim-sim": Path("C:/a/manim.mp4")}))
    assert (spec.width, spec.height, tuple(spec.state_size)) == (1280, 720, (1920, 1080))
    (_, layer), = spec.media
    assert (layer.rect.x, layer.rect.y, layer.rect.w, layer.rect.h) == (384, 107, 682, 384)
    intro = intro_segment_spec(tl, [], frames=90, settings=small, logo=None, logo_has_audio=False, background=None)
    assert (intro.width, intro.height, tuple(intro.state_size)) == (1280, 720, (1920, 1080))


def test_portrait_segment_specs_use_the_timeline_stage(settings: Settings) -> None:
    small = settings.model_copy(update={"render_width": 1280, "render_height": 720})
    tl = _portrait(_classic(small))
    sim = next(s for s in tl.scenes if s.scene_id == "s-sim")
    frames = segment_frames(tl, 30)[sim.index + 1]
    rect = {"x": 0, "y": 480, "width": 1080, "height": 608}  # stage px of the 1080x1920 page
    spec = scene_segment_spec(sim, _shots(sim, rect), timeline=tl, frames=frames, settings=small,
                              inputs=SceneInputs(media={"manim-sim": Path("C:/a/manim.mp4")}))
    assert (spec.width, spec.height, tuple(spec.state_size)) == (720, 1280, (1080, 1920))
    (_, layer), = spec.media
    assert (layer.rect.x, layer.rect.y, layer.rect.w, layer.rect.h) == (0, 320, 720, 406)  # scaled by 2/3
    intro = intro_segment_spec(tl, [], frames=90, settings=small, logo=None, logo_has_audio=False, background=None)
    assert (intro.width, intro.height, tuple(intro.state_size)) == (720, 1280, (1080, 1920))


@pytest.mark.parametrize("stage", [(1920, 1080), (1080, 1920)])
def test_state_pngs_are_normalised_to_the_stage(tmp_path: Path, stage: tuple[int, int]) -> None:
    shot = tmp_path / "frames" / "s.png"
    shot.parent.mkdir(parents=True)
    Image.new("RGB", (stage[0] - 7, stage[1] + 3), (10, 20, 30)).save(shot)  # off by a few px, no alpha
    capture = CaptureResult(scenes={0: [StateShot(0.0, "k", shot)]}, intro=[IntroShot(0.0, shot)])
    assert _prepare_frames(capture, tmp_path, stage) == 1
    with Image.open(shot) as im, Image.open(tmp_path / "frames" / "blank.png") as blank:
        assert im.size == stage and im.mode == "RGBA"
        assert blank.size == stage and blank.getpixel((0, 0))[3] == 0


@pytest.mark.slow
@pytest.mark.parametrize("portrait", [False, True])
def test_screenshot_viewport_follows_the_timeline(app_env, tmp_path: Path, portrait: bool) -> None:
    tl = _classic(app_env)
    if portrait:
        tl = _portrait(tl)
    with serve_stub("t", tl.model_dump(mode="json")) as (base, _):
        settings = app_env.model_copy(update={"base_url": base})
        result = asyncio.run(capture_render_frames(settings=settings, token="t", timeline=tl, out_dir=tmp_path,
                                                   scene_indices=[0], pages=1))
    with Image.open(result.scenes[0][0].png_path) as im:
        assert im.size == ((1080, 1920) if portrait else (1920, 1080))


def test_prepare_frames_defaults_to_the_classic_stage(tmp_path: Path) -> None:
    capture = CaptureResult()
    _prepare_frames(capture, tmp_path)
    with Image.open(tmp_path / "frames" / "blank.png") as blank:
        assert blank.size == (1920, 1080)


# --- captions: line width --------------------------------------------------------------------------

TEXT = ("Ohm's law says that the current through a conductor between two points is directly proportional "
        "to the voltage across the two points, and inversely proportional to the resistance between them.")


def _words(text: str, start: float = 0.0) -> list[TimedWord]:
    out, t = [], start
    for tok in text.split():
        out.append(TimedWord(text=tok, start=round(t, 3), end=round(t + 0.3, 3)))
        t += 0.35
    return out


def test_split_caption_default_width_is_unchanged() -> None:
    words = _words(TEXT)
    assert split_caption(TEXT, words) == split_caption(TEXT, words, width=MAX_LINE_CHARS)
    for cue in split_caption(TEXT, words):
        lines = cue.text.split("\n")
        assert len(lines) <= MAX_LINES and all(len(line) <= 42 for line in lines)


@pytest.mark.parametrize("width", [24, 32, 60])
def test_split_caption_wraps_to_the_given_width(width: int) -> None:
    words = _words(TEXT)
    cues = split_caption(TEXT, words, width=width)
    assert cues
    for cue in cues:
        lines = cue.text.split("\n")
        assert len(lines) <= MAX_LINES and all(len(line) <= width for line in lines), cue.text
    # the words survive in order, and the cues stay ordered and non-overlapping
    assert " ".join(c.text.replace("\n", " ") for c in cues).split() == TEXT.split()
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:], strict=False))
    if width < MAX_LINE_CHARS:
        assert len(cues) > len(split_caption(TEXT, words))


def test_split_caption_hard_splits_tokens_longer_than_the_width() -> None:
    cues = split_caption("Pneumonoultramicroscopicsilicovolcanoconiosis is long", None, start=0.0, end=4.0, width=10)
    assert all(len(line) <= 10 for c in cues for line in c.text.split("\n"))
    assert split_caption("abc", None, width=0)  # clamped to at least one character per line


# --- QA: aspect-aware edge sampling ----------------------------------------------------------------


def test_edge_sample_size_follows_the_aspect() -> None:
    assert edge_sample_size(1920, 1080) == EDGE_SAMPLE == (320, 180)
    assert edge_sample_size(1280, 720) == (320, 180)
    assert edge_sample_size(1080, 1920) == (180, 320)
    assert edge_sample_size(720, 1280) == (180, 320)
    assert edge_sample_size(1080, 1080) == (320, 180)  # square: as landscape
    assert edge_sample_size(None, None) == edge_sample_size(0, 1920) == (320, 180)  # unknown: as before


def test_inspect_render_samples_portrait_frames_transposed(monkeypatch, settings: Settings, tmp_path: Path) -> None:
    sizes: list[tuple[int, int]] = []

    async def fake_probe(path, entries, settings, **kw):
        assert "width,height" in entries
        return {"format": {"duration": "1.0"}, "streams": [
            {"codec_type": "video", "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "time_base": "1/15360",
             "width": 1080, "height": 1920}]}

    async def fake_pts(path, settings, **kw):
        return [i * 512 for i in range(30)]

    async def fake_gray(path, t, settings, *, size=EDGE_SAMPLE, timeout=60):
        sizes.append(size)
        return bytes([128]) * (size[0] * size[1])

    monkeypatch.setattr(qa_mod, "_ffprobe_json", fake_probe)
    monkeypatch.setattr(qa_mod, "packet_pts", fake_pts)
    monkeypatch.setattr(qa_mod, "gray_frame", fake_gray)
    probed = asyncio.run(inspect_render(tmp_path / "x.mp4", settings=settings, fps=30, expected_frames=30,
                                        expect_audio=False))
    assert sizes == [(180, 320)] * 3 and probed.black_edges == []
    sizes.clear()
    told = asyncio.run(inspect_render(tmp_path / "x.mp4", settings=settings, fps=30, expected_frames=30,
                                      expect_audio=False, size=(1920, 1080)))
    assert sizes == [(320, 180)] * 3 and told.ok  # the caller's output size wins over the probe


@pytest.mark.slow
def test_inspect_a_portrait_render_with_pillarbox_bars(tmp_path: Path, settings: Settings) -> None:
    mp4 = tmp_path / "portrait.mp4"
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=144x320:r=30:d=1", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=48000:duration=1", "-vf",
                            "pad=180:320:18:0:black,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                            "-video_track_timescale", "15360", "-c:a", "aac", "-shortest", str(mp4)],
                           settings=settings))
    qa = asyncio.run(inspect_render(mp4, settings=settings, fps=30, expected_frames=30, expect_audio=True,
                                    size=(180, 320)))
    assert qa.ok, qa.problems
    assert qa.black_edges == ["left", "right"]
