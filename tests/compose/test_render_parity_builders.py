"""Render parity builders (pure): BT.709 colour, scene-layer fades, mascot loop phase and crossfade,
soft caption track, and the render job's per-scene wiring (no ffmpeg runs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.compose import video as video_mod
from aadhi.compose.ffmpeg import (
    BT709_CODEC_TAGS,
    BT709_CONVERT,
    BT709_TAG,
    FinalSpec,
    Input,
    MediaLayer,
    Rect,
    SegmentSpec,
    StateFrame,
    alpha_fade_in,
    background_filter,
    build_final,
    build_segment,
    crossfade_clip_filter,
    image_input,
    loop_phase_frames,
    media_filter,
    relabel_bt709,
    state_stream_filter,
    subtitle_language_code,
    to_yuv,
    video_codec_args,
    video_input,
)
from aadhi.compose.screenshot import CaptureResult, StateShot
from aadhi.compose.timeline import build_timeline
from aadhi.compose.video import SceneInputs, render_warnings, scene_segment_spec, segment_frames
from aadhi.config import Settings

from .factories import make_manifest, make_screenplay


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_fps=30, render_width=1280, render_height=720)


# --- colour -------------------------------------------------------------------------------------


def test_colour_helpers_are_identity_when_off() -> None:
    assert to_yuv("yuv420p", bt709=False) == "format=yuv420p"
    assert relabel_bt709(False) == ""
    assert to_yuv("yuva420p", bt709=True) == f"format=rgba,{BT709_CONVERT},format=yuva420p,{BT709_TAG}"
    assert relabel_bt709(True) == f",{BT709_TAG}"
    assert "out_color_matrix=bt709" in BT709_CONVERT and "out_range=tv" in BT709_CONVERT
    assert video_codec_args(fps=30, crf=20, preset="fast")[-2:] == ["-video_track_timescale", "15360"]
    tagged = video_codec_args(fps=30, crf=20, preset="fast", bt709=True)
    assert tagged[-len(BT709_CODEC_TAGS):] == list(BT709_CODEC_TAGS)
    assert "bt709" in tagged and "-bf" not in tagged  # B-frames stay (the frame grid holds: slow test)


def test_background_filters_bt709_and_phase() -> None:
    plain = background_filter("video", width=640, height=360, fps=30, frames=90)
    assert background_filter("video", width=640, height=360, fps=30, frames=90, offset_frames=0, bt709=False) == plain
    assert "trim=start_frame" not in plain and "setparams" not in plain
    phased = background_filter("video", width=640, height=360, fps=30, frames=90, offset_frames=37, bt709=True)
    assert "fps=30,trim=start_frame=37,setpts=PTS-STARTPTS,scale=640:360" in phased  # skipped before scaling
    assert phased.endswith(f"format=yuv420p,{BT709_TAG}[bg]") and "out_color_matrix" not in phased  # relabel only
    image = background_filter("image", width=640, height=360, fps=30, frames=90, in_label="1:v", bt709=True)
    assert f"{BT709_CONVERT},format=yuv420p,{BT709_TAG},loop=" in image  # still converted once
    color = background_filter("color", width=640, height=360, fps=30, frames=90, bt709=True)
    assert BT709_CONVERT in color  # the visible colour clock is converted per frame
    assert background_filter("color", width=640, height=360, fps=30, frames=90) == \
        "[0:v]trim=end_frame=90,setpts=PTS-STARTPTS,format=yuv420p[bg]"


def test_media_and_state_filters_bt709_and_layer_fade() -> None:
    still = MediaLayer(0, "image", Rect(0, 0, 200, 100), fit="contain")
    assert media_filter(still, fps=30, frames=60, out_label="m0") == media_filter(
        still, fps=30, frames=60, out_label="m0", fade_in=0, bt709=False)
    faded = media_filter(still, fps=30, frames=60, out_label="m0", fade_in=0.4, bt709=True)
    assert BT709_CONVERT in faded and faded.endswith(f"{alpha_fade_in(0.4, 30)}[m0]")
    clip = MediaLayer(0, "video", Rect(0, 0, 200, 100), end_behavior="freeze", visible_from=1.0)
    legacy_clip = media_filter(clip, fps=30, frames=60, out_label="m1")
    assert legacy_clip.endswith("format=rgba[m1]")
    assert media_filter(clip, fps=30, frames=60, out_label="m1", bt709=True).endswith(f"{BT709_TAG}[m1]")
    base = state_stream_filter(3, fps=30, frames=60, size=None, out_label="sb")
    assert "format=rgba,format=yuva420p" in base and "setparams" not in base
    conv = state_stream_filter(3, fps=30, frames=60, size=None, out_label="sb", fade_in=0.4, bt709=True)
    assert f"format=rgba,{BT709_CONVERT},format=yuva420p,{BT709_TAG}" in conv
    assert conv.endswith("trim=end_frame=60" + alpha_fade_in(0.4, 30) + "[sb]")


def test_alpha_fade_in_is_enabled_on_its_ramp_only() -> None:
    assert alpha_fade_in(0, 30) == ""
    f = alpha_fade_in(0.4, 30)
    assert "fade=t=in:st=0:d=0.4:alpha=1" in f and "enable='lte(t,0.416667)'" in f


# --- mascot loop phase / crossfade ---------------------------------------------------------------


@pytest.mark.parametrize(("start", "clip", "expected"), [
    (0, 8.0, 0), (240, 8.0, 0), (241, 8.0, 1), (345, 8.0, 105), (1000, 8.0, 40), (100, None, 0), (100, 0.0, 0),
    (25, 1.0 / 3, 5),  # 10 frames per loop at 30 fps
])
def test_loop_phase_frames(start: int, clip: float | None, expected: int) -> None:
    assert loop_phase_frames(start, fps=30, clip_seconds=clip) == expected


def test_consecutive_segments_continue_the_loop() -> None:
    starts = [0, 345, 700, 1201]
    phases = [loop_phase_frames(s, fps=30, clip_seconds=8.0) for s in starts]
    for (a, pa), (b, pb) in zip(zip(starts, phases, strict=True), list(zip(starts, phases, strict=True))[1:],
                                strict=False):
        assert (pa + (b - a)) % 240 == pb  # the next segment shows the next clip frame


def test_crossfade_clip_filter() -> None:
    f = crossfade_clip_filter(width=640, height=360, fps=30, frames=18, in_label="2:v", out_label="xf",
                              offset_frames=12)
    assert f.startswith("[2:v]setpts=PTS-STARTPTS,fps=30,trim=start_frame=12,setpts=PTS-STARTPTS,scale=640:360")
    assert "trim=end_frame=18" in f and f.endswith("fade=t=out:st=0:d=0.6:alpha=1[xf]")
    assert "format=yuva420p" in f and "out_color_matrix" not in f  # YUV clip: no matrix conversion


def test_build_segment_with_parity_options() -> None:
    clip = video_input("C:/b/left.mp4", loop=True, audio=False)
    prev = video_input("C:/b/right.mp4", loop=True, audio=False)
    spec = SegmentSpec(width=640, height=360, fps=30, frames=90, background=clip, background_kind="video",
                       layer_fade_in=0.4, background_offset_frames=40, previous_background=prev,
                       previous_background_offset_frames=40, bt709=True, blank_state="frames/blank.png")
    spec.media.append((image_input("C:/a/fig.png", 30), MediaLayer(0, "image", Rect(10, 10, 100, 100))))
    spec.states = [StateFrame("frames/a.png", 0.0), StateFrame("frames/b.png", 1.0)]
    cmd = build_segment(spec, name="s001")
    graph = cmd.files["s001.filtergraph"]
    i_args = [cmd.args[i + 1] for i, a in enumerate(cmd.args) if a == "-i"]
    assert i_args[:3] == [clip.path, prev.path, "C:/a/fig.png"]
    assert "trim=start_frame=40" in graph and "[bg][xf]overlay" in graph and "[vxf][m0]overlay" in graph
    assert graph.count("fade=t=in:st=0:d=0.4:alpha=1") == 2  # media + the state stream (cross-fades mixed in)
    assert ",fade=t=in:st=0:d=0.4[" not in graph and "fade=t=in:st=0:d=0.4," not in graph  # no fade from black
    assert all(tag in cmd.args for tag in BT709_CODEC_TAGS)


def test_build_segment_defaults_are_unchanged() -> None:
    clip = video_input("C:/b/left.mp4", loop=True, audio=False)
    spec = SegmentSpec(width=640, height=360, fps=30, frames=90, background=clip, background_kind="video",
                       fade_in=0.4, blank_state="frames/blank.png")
    spec.states = [StateFrame("frames/a.png", 0.0)]
    cmd = build_segment(spec, name="s000")
    graph = cmd.files["s000.filtergraph"]
    for marker in ("setparams", "out_color_matrix", "alpha=1:enable='lte", "trim=start_frame", "[xf]"):
        assert marker not in graph
    assert "-colorspace" not in cmd.args
    assert "fade=t=in:st=0:d=0.4,format=yuv420p[vout]" in graph  # the whole-picture fade as before


# --- soft caption track -------------------------------------------------------------------------


@pytest.mark.parametrize(("lang", "code"), [("en-IN", "eng"), ("ta-IN", "tam"), ("hi", "hin"), ("te-IN", "tel"),
                                            ("kn-IN", "kan"), ("ml-IN", "mal"), ("xx-YY", "und"), ("", "und")])
def test_subtitle_language_code(lang: str, code: str) -> None:
    assert subtitle_language_code(lang) == code


def test_build_final_soft_subtitles() -> None:
    base = FinalSpec(video_list="v.ffconcat", audio_list="a.ffconcat", metadata="m.ffmeta", total_seconds=12.5,
                     output="out.mp4")
    plain = build_final(base)
    assert "-c:s" not in plain.args and "mov_text" not in plain.args
    spec = FinalSpec(video_list="v.ffconcat", audio_list="a.ffconcat", metadata="m.ffmeta", total_seconds=12.5,
                     output="out.mp4", soft_subtitles="captions.soft.srt", subtitle_language="tam")
    args = build_final(spec).args
    i_args = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    assert i_args == ["v.ffconcat", "a.ffconcat", "m.ffmeta", "captions.soft.srt"]
    sub_at = args.index("captions.soft.srt")
    assert args[sub_at - 5: sub_at] == ["-protocol_whitelist", "file,pipe", "-f", "srt", "-i"]
    j = args.index("-c:s")
    assert args[j - 2: j] == ["-map", "3:s:0"] and args[j + 1] == "mov_text"
    assert args[j + 2: j + 6] == ["-metadata:s:s:0", "language=tam", "-disposition:s:0", "0"]
    assert args.index("-map_metadata") > j and args[args.index("-map_metadata") + 1] == "2"
    assert args[args.index("-t") + 1] == "12.5"  # the cut applies to the caption track too
    burned = build_final(FinalSpec(video_list="v", audio_list="a", metadata="m", total_seconds=3, output="o.mp4",
                                   burn_subtitles="c.srt", bt709=True)).args
    assert all(tag in burned for tag in BT709_CODEC_TAGS)


def test_srt_input_is_whitelisted() -> None:
    args = Input("captions.soft.srt", "srt").args()
    assert args == ["-protocol_whitelist", "file,pipe", "-f", "srt", "-i", "captions.soft.srt"]


# --- render job wiring --------------------------------------------------------------------------


def test_scene_spec_mascot_phase_and_crossfade(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    scene = tl.scenes[3]
    frames = segment_frames(tl, 30)[4]
    inputs = SceneInputs(background=Path("C:/b/aadhi_right.mp4"), background_offset_frames=17,
                         previous_background=Path("C:/b/aadhi_left.mp4"), previous_background_offset_frames=17)
    spec = scene_segment_spec(scene, [], timeline=tl, inputs=inputs, frames=frames, settings=settings)
    assert spec.background_offset_frames == 17 and spec.previous_background is not None
    assert "-stream_loop" in spec.previous_background.options and spec.previous_background_offset_frames == 17
    assert spec.bt709 is True  # RENDER_COLOR_BT709 default
    off = settings.model_copy(update={"render_mascot_continuity": False, "render_color_bt709": False})
    legacy = scene_segment_spec(scene, [], timeline=tl, inputs=inputs, frames=frames, settings=off)
    assert legacy.background_offset_frames == 0 and legacy.previous_background is None and legacy.bt709 is False
    still = SceneInputs(background=Path("C:/b/bg.png"), background_is_image=True, background_offset_frames=17)
    assert scene_segment_spec(scene, [], timeline=tl, inputs=still, frames=frames,
                              settings=settings).background_offset_frames == 0
    same = SceneInputs(background=Path("C:/b/a.mp4"), previous_background=Path("C:/b/a.mp4"))
    assert scene_segment_spec(scene, [], timeline=tl, inputs=same, frames=frames,
                              settings=settings).previous_background is None


def test_clip_phases_follow_absolute_time_and_mark_position_changes(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    bounds = segment_frames(tl, 30)
    left, right = Path("C:/b/left.mp4"), Path("C:/b/right.mp4")

    def clip_for(scene):
        return right if scene.layout.mascot_position == "right" else left

    phases = video_mod._clip_phases(tl, bounds, 1, 30, clip_for, {left: 8.0, right: 8.0})
    assert len(phases) == len(tl.scenes)
    for i, (phase, prev, _) in enumerate(phases):
        assert phase == loop_phase_frames(bounds[i + 1][0], fps=30, clip_seconds=8.0)
        expected_prev = clip_for(tl.scenes[i - 1]) if i and clip_for(tl.scenes[i - 1]) != clip_for(tl.scenes[i]) else None
        assert prev == expected_prev
    assert phases[0][1] is None  # the first scene follows the intro: no crossfade
    assert any(p[1] == left for p in phases) and any(p[1] == right for p in phases)


def test_render_warnings_include_title_only_panels(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    board = next(s for s in tl.scenes if s.scene_id == "s-board")
    capture = CaptureResult(scenes={board.index: [
        StateShot(0.0, "k0", Path("a.png")),
        StateShot(1.0, "k1", Path("b.png"), notices=("This image could not be loaded.",))]})
    items = render_warnings(tl, capture)
    reasons = {(it["scene_id"], it["reason"]) for it in items}
    assert ("s-board", "panel_notice") in reasons and ("s-noaudio", "no_audio") in reasons
    notice = next(it for it in items if it["reason"] == "panel_notice")
    assert notice["blocking"] is False and "title only" in notice["message"]
    assert render_warnings(tl) == [it for it in items if it["reason"] != "panel_notice"]


def test_render_warnings_carry_the_screenplay_number_when_given(settings: Settings) -> None:
    sp = make_screenplay()
    hidden = sp.model_copy(update={"scenes": [s.model_copy(update={"hidden": s.id == "s-title"}) for s in sp.scenes]})
    tl = build_timeline(hidden, make_manifest(hidden), settings=settings)
    board = next(s for s in tl.scenes if s.scene_id == "s-board")
    capture = CaptureResult(scenes={board.index: [
        StateShot(1.0, "k1", Path("b.png"), notices=("This image could not be loaded.",))]})
    numbers = {s.id: i + 1 for i, s in enumerate(hidden.scenes)}
    plain = render_warnings(tl, capture)
    numbered = render_warnings(tl, capture, numbers=numbers)
    assert all("scene_number" not in it for it in plain)  # without numbers: unchanged
    assert [{k: v for k, v in it.items() if k != "scene_number"} for it in numbered] == plain
    for it in numbered:
        assert it["scene_number"] == numbers[it["scene_id"]]
        assert it["scene_index"] == next(s.index for s in tl.scenes if s.scene_id == it["scene_id"])
    notice = next(it for it in numbered if it["reason"] == "panel_notice")
    assert notice["scene_number"] == numbers["s-board"] == board.index + 2  # the hidden title scene is counted


# --- untagged media videos are relabelled BT.709, not re-matrixed ------------------------------------


def test_untagged_hd_media_video_is_relabelled_before_its_rgba_conversion() -> None:
    from aadhi.compose.ffmpeg import BT709_TAG_KEEP_RANGE

    layer = MediaLayer(0, "video", Rect(0, 0, 640, 360), untagged_yuv=True)
    out = media_filter(layer, fps=30, frames=60, out_label="m0", bt709=True)
    assert out.startswith(f"[0:v]{BT709_TAG},setpts=PTS-STARTPTS")
    full = MediaLayer(0, "video", Rect(0, 0, 640, 360), untagged_yuv=True, range_tagged=True)
    assert media_filter(full, fps=30, frames=60, out_label="m0", bt709=True).startswith(
        f"[0:v]{BT709_TAG_KEEP_RANGE},setpts")  # its own range tag is kept
    tagged = MediaLayer(0, "video", Rect(0, 0, 640, 360))
    assert media_filter(tagged, fps=30, frames=60, out_label="m0", bt709=True).startswith("[0:v]setpts")
    assert media_filter(layer, fps=30, frames=60, out_label="m0", bt709=False) == media_filter(
        tagged, fps=30, frames=60, out_label="m0", bt709=False)  # bt709 off: the original output, bit for bit
    gif = MediaLayer(0, "gif", Rect(0, 0, 640, 360), untagged_yuv=True)
    assert media_filter(gif, fps=30, frames=60, out_label="m0", bt709=True).startswith("[0:v]setpts")


def test_probe_reads_colour_tags_and_untagged_hd() -> None:
    from aadhi.compose.ffmpeg import parse_probe, untagged_hd

    def probe(**stream):
        return parse_probe({"format": {"duration": "4"}, "streams": [
            {"codec_type": "video", "width": 1280, "height": 720, "pix_fmt": "yuv420p", **stream}]})

    plain = probe()
    assert (plain.pix_fmt, plain.color_space, plain.color_range) == ("yuv420p", None, None) and untagged_hd(plain)
    unknown = probe(color_space="unknown", color_range="unknown")
    assert unknown.color_space is None and unknown.color_range is None and untagged_hd(unknown)
    tagged = probe(color_space="bt709", color_range="tv")
    assert (tagged.color_space, tagged.color_range) == ("bt709", "tv") and not untagged_hd(tagged)
    assert not untagged_hd(probe(height=480))  # SD keeps ffmpeg's BT.601 default
    assert not untagged_hd(probe(pix_fmt="rgb24"))
    assert not untagged_hd(parse_probe({"streams": [{"codec_type": "audio"}]}))


def test_scene_and_intro_video_layers_follow_the_probe(settings: Settings, tmp_path: Path) -> None:
    from aadhi.compose.ffmpeg import parse_probe
    from aadhi.schemas.timeline import MediaRef

    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    scene = tl.scenes[0].model_copy(update={"media": MediaRef(kind="video", asset_key="clip-key")})
    frames = segment_frames(tl, 30)[1 if tl.intro is not None else 0]
    shots = [StateShot(0.0, "k0", tmp_path / "a.png", media_rect={"x": 0, "y": 0, "width": 960, "height": 540})]
    seen = []
    for untagged in ({"clip-key": False}, {"clip-key": True}, {}):
        inputs = SceneInputs(media={"clip-key": tmp_path / "clip.mp4"}, untagged_media=untagged)
        spec = scene_segment_spec(scene, shots, timeline=tl, inputs=inputs, frames=frames, settings=settings)
        (layer,) = [layer for _, layer in spec.media if layer.kind == "video"]
        seen.append((layer.untagged_yuv, layer.range_tagged))
    assert seen == [(True, False), (True, True), (False, False)]
    assert tl.intro is not None
    logo = parse_probe({"streams": [{"codec_type": "video", "width": 1280, "height": 720, "pix_fmt": "yuv420p"}]})
    intro = video_mod.intro_segment_spec(tl, [], frames=90, settings=settings, logo=tmp_path / "logo.mp4",
                                         logo_has_audio=False, background=None, logo_probe=logo)
    assert intro.media[0][1].untagged_yuv and not intro.media[0][1].range_tagged
    bare = video_mod.intro_segment_spec(tl, [], frames=90, settings=settings, logo=tmp_path / "logo.mp4",
                                        logo_has_audio=False, background=None)
    assert not bare.media[0][1].untagged_yuv


# --- cross-fades mixed in premultiplied alpha --------------------------------------------------------


def test_state_mixes_keep_a_shared_translucent_panel_and_ramp_new_elements(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    from aadhi.compose.ffmpeg import plan_states, state_mixes
    from aadhi.compose.frames import write_state_mixes

    (tmp_path / "frames").mkdir()
    old = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    ImageDraw.Draw(old).rectangle((20, 20, 300, 160), fill=(55, 15, 100, 219))  # the glass board, 0.86
    new = old.copy()
    ImageDraw.Draw(new).rectangle((40, 40, 80, 60), fill=(250, 250, 250, 255))  # a bullet appears on it
    ImageDraw.Draw(new).rectangle((302, 165, 318, 178), fill=(10, 200, 30, 219))  # a glass chip on empty space
    old.save(tmp_path / "frames" / "a.png")
    new.save(tmp_path / "frames" / "b.png")
    plan = plan_states([StateFrame("frames/a.png", 0.0), StateFrame("frames/b.png", 1.0)], fps=30, frames=60,
                       blank="frames/blank.png")
    spec = SegmentSpec(width=320, height=180, fps=30, frames=60, blank_state="frames/blank.png",
                       states=[StateFrame("frames/a.png", 0.0), StateFrame("frames/b.png", 1.0)])
    assert state_mixes(spec) == plan.mixes
    assert write_state_mixes(plan.mixes, tmp_path) == 4
    previous = 0
    for mix in plan.mixes:
        im = Image.open(tmp_path / mix.out)
        assert im.mode == "RGBA" and im.size == (320, 180)
        board = im.getpixel((200, 120))
        assert all(abs(a - b) <= 2 for a, b in zip(board, (55, 15, 100, 219), strict=True)), board  # no darkening
        chip = im.getpixel((310, 170))
        assert chip[3] > previous and abs(chip[3] - round(219 * mix.weight)) <= 2  # fades in evenly
        assert abs(chip[1] - 200) <= 3  # its colour, not a darker one
        previous = chip[3]
    rewritten = write_state_mixes(plan.mixes, tmp_path)  # a retried attempt rewrites them (states may differ)
    assert rewritten == 4
