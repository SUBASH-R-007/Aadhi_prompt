"""Pure ffmpeg filter-graph/argument builders, version gating and the process runner."""

from __future__ import annotations

import asyncio
import sys
import time

import psutil
import pytest

from aadhi.compose import ffmpeg as ff
from aadhi.compose.ffmpeg import (
    SCRIPT_FLAG_LEGACY,
    SCRIPT_FLAG_MODERN,
    AudioClip,
    FFmpegError,
    FinalSpec,
    Input,
    MediaLayer,
    Rect,
    SegmentSpec,
    StateFrame,
    audio_mix_filter,
    background_filter,
    bgm_filter,
    build_final,
    build_segment,
    clock_input,
    concat_document,
    concat_list,
    concat_safe,
    even,
    filter_escape,
    filter_script_flag,
    fit_filter,
    ft,
    image_input,
    ken_burns_filter,
    media_filter,
    overlay_filter,
    parse_ffmpeg_version,
    parse_probe,
    plan_states,
    run_process,
    state_mixes,
    state_stream_filter,
    subprocess_env,
    subtitles_filter,
    video_input,
)
from aadhi.config import Settings
from aadhi.jobs.base import JobCancelled
from aadhi.schemas.timeline import KenBurns, KenBurnsPoint


def inputs_of(args: list[str]) -> list[int]:
    return [i for i, a in enumerate(args) if a == "-i"]


def assert_whitelisted(args: list[str]) -> None:
    """Every -i is preceded (within its option group) by -protocol_whitelist file,pipe."""
    starts = [i for i, a in enumerate(args) if a == "-protocol_whitelist"]
    idx = inputs_of(args)
    assert len(starts) == len(idx)
    for s, i in zip(starts, idx, strict=True):
        assert s < i and args[s + 1] == "file,pipe"
        assert "-i" not in args[s + 1:i]


# --- small helpers ------------------------------------------------------------------------------


def test_ft_and_even() -> None:
    assert ft(1.0) == "1" and ft(0.1234) == "0.123" and ft(-3) == "0" and ft(2.5) == "2.5"
    assert ft(1 / 30, 6) == "0.033333"
    assert even(3) == 4 or even(3) == 2
    assert even(101) % 2 == 0 and even(0.4) == 2


def test_inputs_have_whitelist_and_explicit_formats() -> None:
    img = image_input("a.png", 30).args()
    assert img[:2] == ["-protocol_whitelist", "file,pipe"]
    assert img[img.index("-f") + 1] == "image2" and "-pattern_type" in img and img[-2:] == ["-i", "a.png"]
    vid = video_input("clip.mp4", loop=True, audio=False).args()
    assert "-stream_loop" in vid and "-an" in vid and "-f" not in vid
    gif = video_input("x.gif", loop=True, gif=True).args()
    assert gif[gif.index("-f") + 1] == "gif" and "-ignore_loop" in gif
    assert Input("list.txt", "concat", ("-safe", "1")).args()[2:4] == ["-f", "concat"]


def test_rect_from_stage_scales_and_clamps() -> None:
    r = Rect.from_stage({"x": 960, "y": 540, "width": 960, "height": 540}, stage=(1920, 1080), out=(1280, 720))
    assert r == Rect(640, 360, 640, 360)
    r2 = Rect.from_stage({"left": -10, "top": 10, "w": 2000, "h": 101}, stage=(1920, 1080), out=(1920, 1080))
    assert r2.x == 0 and r2.w == 1920 and r2.h % 2 == 0
    assert Rect.from_stage({"x": 0, "y": 0, "width": 1, "height": 1}, stage=(1920, 1080), out=(1920, 1080)) is None
    assert Rect.from_stage(None, stage=(1920, 1080), out=(1920, 1080)) is None
    assert Rect.from_stage({"x": "bad"}, stage=(1920, 1080), out=(1920, 1080)) is None


# --- ffmpeg version gating (Debian bookworm ships 5.1) ------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("ffmpeg version 5.1.6-0+deb12u1 Copyright (c) 2000-2024", (5, 1)),
    ("ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright", (8, 1)),
    ("ffmpeg version n7.0.2 Copyright", (7, 0)),
    ("ffmpeg version N-118000-gabcdef\n  libavfilter    10.  4.100 / 10.  4.100", (7, 0)),
    ("ffmpeg version 2024-05-13-git-abc\nlibavfilter     9. 12.100", (6, 0)),
    ("garbage", None),
    ("", None),
])
def test_parse_ffmpeg_version(text: str, expected: tuple[int, int] | None) -> None:
    assert parse_ffmpeg_version(text) == expected


def test_filter_script_flag_version_gate() -> None:
    assert filter_script_flag((5, 1)) == "-filter_complex_script"
    assert filter_script_flag((6, 1)) == SCRIPT_FLAG_LEGACY
    assert filter_script_flag((7, 0)) == "-/filter_complex" == SCRIPT_FLAG_MODERN
    assert filter_script_flag((8, 1)) == SCRIPT_FLAG_MODERN
    assert filter_script_flag(None) == SCRIPT_FLAG_LEGACY  # unknown: the flag every version accepts


def test_ffmpeg_version_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return b"ffmpeg version 5.1.6-0+deb12u1 Copyright", ""

    monkeypatch.setattr(ff, "run_process", fake_run)
    monkeypatch.setattr(ff, "_VERSION_CACHE", {})
    settings = Settings(_env_file=None, ffmpeg_path="ffmpeg-test-binary")
    assert asyncio.run(ff.ffmpeg_version(settings)) == (5, 1)
    assert asyncio.run(ff.ffmpeg_version(settings)) == (5, 1)
    assert len(calls) == 1 and calls[0][-1] == "-version"


def test_ffmpeg_version_unknown_when_binary_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(cmd, **kw):
        raise FFmpegError("x not found")

    monkeypatch.setattr(ff, "run_process", failing)
    monkeypatch.setattr(ff, "_VERSION_CACHE", {})
    assert asyncio.run(ff.ffmpeg_version(Settings(_env_file=None))) is None


def test_installed_ffmpeg_version_detected() -> None:
    version = asyncio.run(ff.ffmpeg_version(Settings(_env_file=None)))
    assert version is not None and version[0] >= 5


# --- video filters ------------------------------------------------------------------------------


def test_fit_filters() -> None:
    contain = fit_filter(640, 360, "contain")
    assert "force_original_aspect_ratio=decrease" in contain and "pad=640:360" in contain
    assert "color=0x00000000" in contain  # transparent letterbox
    cover = fit_filter(640, 360, "cover")
    assert "force_original_aspect_ratio=increase" in cover and "crop=640:360" in cover


def test_background_filters() -> None:
    v = background_filter("video", width=1280, height=720, fps=30, frames=90)
    assert v.startswith("[0:v]") and "trim=end_frame=90" in v and v.endswith("[bg]") and "fps=30" in v
    i = background_filter("image", width=1280, height=720, fps=30, frames=90, clock_label="0:v", in_label="1:v")
    # the still is converted once and looped as references over the paced clock frames
    assert "[1:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1,format=yuv420p,loop=loop=89" in i
    assert "[0:v]trim=end_frame=90" in i and "[bg_clk][bg_img]overlay" in i and i.endswith("[bg]")
    c = background_filter("color", width=1280, height=720, fps=30, frames=90)
    assert c == "[0:v]trim=end_frame=90,setpts=PTS-STARTPTS,format=yuv420p[bg]"
    clock = clock_input(width=1280, height=720, fps=30, frames=90, color="#1A0B2E").args()
    assert clock[clock.index("-f") + 1] == "lavfi" and clock[-1] == "color=c=0x1A0B2E:s=1280x720:r=30:d=4"
    assert clock[:2] == ["-protocol_whitelist", "file,pipe"]


def test_ken_burns_filter_expressions() -> None:
    kb = KenBurns(start=KenBurnsPoint(cx=0.4, cy=0.5, scale=1.0), end=KenBurnsPoint(cx=0.6, cy=0.45, scale=1.2))
    f = ken_burns_filter(kb, 640, 360, fps=30, frames=91)
    assert "zoompan=" in f and "s=640x360" in f and "fps=30" in f and "d=1" in f
    assert "(1.0000+(0.2000)*on/90)" in f
    assert "(0.4000+(0.2000)*on/90)*iw-iw/zoom/2" in f
    assert "(0.5000+(-0.0500)*on/90)*ih-ih/zoom/2" in f
    assert "scale=1280:720" in f  # supersampled before zoompan (no jitter)
    assert "loop=loop=90:size=1" in f


def test_media_filter_freeze_loop_still() -> None:
    rect = Rect(10, 20, 640, 360)
    freeze = media_filter(MediaLayer(3, "video", rect, end_behavior="freeze"), fps=30, frames=60, out_label="m0")
    assert freeze.startswith("[3:v]") and "tpad=stop_mode=clone" in freeze and "trim=end_frame=60" in freeze
    assert "start=" not in freeze  # no delay for media visible from the segment start
    loop = media_filter(MediaLayer(3, "video", rect, fit="cover", end_behavior="loop"), fps=30, frames=60,
                        out_label="m0")
    assert "tpad" not in loop and "crop=640:360" in loop
    still = media_filter(MediaLayer(4, "image", rect), fps=30, frames=60, out_label="m1")
    assert "format=yuva420p,loop=loop=59" in still and still.endswith("[m1]")  # converted once, then looped
    kb = media_filter(MediaLayer(4, "image", rect, ken_burns=KenBurns()), fps=30, frames=60, out_label="m1")
    assert "zoompan" in kb


def test_media_filter_delays_panel_video_to_show_at() -> None:
    """A panel clip starts at visible_from (frame 0 there), like videoTimeAt(t - show_at)."""
    rect = Rect(1450, 200, 360, 360)
    f = media_filter(MediaLayer(1, "video", rect, end_behavior="freeze", visible_from=4.0), fps=30, frames=360,
                     out_label="m1")
    assert "tpad=start=120:start_mode=add:color=0x00000000:stop_mode=clone:stop_duration=13" in f
    assert f.index("format=rgba,tpad") < f.index("trim=end_frame=360")  # transparent padding (alpha kept)
    loop = media_filter(MediaLayer(1, "gif", rect, end_behavior="loop", visible_from=2.5), fps=30, frames=360,
                        out_label="m1")
    assert "tpad=start=75:start_mode=add:color=0x00000000" in loop and "stop_mode" not in loop
    still = media_filter(MediaLayer(1, "image", rect, visible_from=4.0), fps=30, frames=360, out_label="m1")
    assert "tpad" not in still  # stills are simply gated by the overlay's enable window


# --- state PNG streams ----------------------------------------------------------------------------


def test_concat_safe_names() -> None:
    assert concat_safe("frames/s000_0001.png") and concat_safe("s001.mp4") and concat_safe("a-b/c_d.e.f")
    for bad in ["", "/abs/x.png", "C:/x.png", "../x.png", "frames/.hidden.png", "a//b.png", "a b.png", "it's.png",
                "x\ny.png", "frames/"]:
        assert not concat_safe(bad), bad


def test_concat_document_uses_virtual_clock_and_repeats_last() -> None:
    text = concat_document([("frames/a.png", 7), ("frames/b.png", 1), ("frames/c.png", 22)])
    assert text.splitlines() == [
        "ffconcat version 1.0",
        "file 'frames/a.png'", "duration 0.280000",  # 7 frames * 1/25 s (exact on the demuxer's time base)
        "file 'frames/b.png'", "duration 0.040000",
        "file 'frames/c.png'", "duration 0.880000",
        "file 'frames/c.png'",
    ]
    assert concat_document([]) == "ffconcat version 1.0\n"
    with pytest.raises(ValueError):
        concat_document([("../etc/passwd", 1)])
    with pytest.raises(ValueError):
        concat_list(["it's.mp4"])
    assert concat_list(["seg_000.mp4", "s001.mp4"]).splitlines() == [
        "ffconcat version 1.0", "file 'seg_000.mp4'", "file 's001.mp4'"]


def _mixes(plan, old: str, new: str) -> list:
    return [m for m in plan.mixes if (m.old, m.new) == (old, new)]


def test_plan_states_cross_fades() -> None:
    states = [StateFrame("a.png", 0.0), StateFrame("b.png", 1.0), StateFrame("c.png", 2.5)]
    plan = plan_states(states, fps=30, frames=120, fade=0.15, blank="blank.png")
    assert plan.fade_frames == 4  # round(0.15 * 30)
    ab, bc = _mixes(plan, "a.png", "b.png"), _mixes(plan, "b.png", "c.png")
    assert [m.weight for m in ab] == [m.weight for m in bc] == [0.2, 0.4, 0.6, 0.8]  # (j + 1) / (fade + 1)
    # one stream: each state in full once its window is over, the window as one mixed PNG per frame
    assert plan.base == (("a.png", 30), *[(m.out, 1) for m in ab], ("b.png", 41), *[(m.out, 1) for m in bc],
                         ("c.png", 41))
    assert len(plan.mixes) == 8 and all(m.out.startswith("mix-") and concat_safe(m.out) for m in plan.mixes)
    assert sum(n for _, n in plan.base) == 120


def test_plan_states_dense_same_frame_and_late_states() -> None:
    states = [StateFrame("a.png", 0.0), StateFrame("b.png", 1.0), StateFrame("c.png", 1.05),
              StateFrame("d.png", 1.06), StateFrame("e.png", 3.9), StateFrame("late.png", 4.0)]
    plan = plan_states(states, fps=30, frames=120, fade=0.15, blank="z.png")
    # c and d land on frame 32: the later one wins; b's fade (from frame 30) is cut short by d;
    # e (frame 117) never completes its fade before the end; "late" starts at the end (dropped)
    ab, bd, de = _mixes(plan, "a.png", "b.png"), _mixes(plan, "b.png", "d.png"), _mixes(plan, "d.png", "e.png")
    assert [len(ab), len(bd), len(de)] == [2, 4, 3] and not _mixes(plan, "c.png", "d.png")
    assert plan.base == (("a.png", 30), *[(m.out, 1) for m in ab], *[(m.out, 1) for m in bd], ("d.png", 81),
                         *[(m.out, 1) for m in de])
    assert sum(n for _, n in plan.base) == 120


def test_plan_states_last_state_inside_fade_window_at_end() -> None:
    plan = plan_states([StateFrame("a.png", 0), StateFrame("b.png", 3.95)], fps=30, frames=120, blank="z.png")
    (mix,) = plan.mixes  # b never completes its fade: only its first mixed frame is shown
    assert plan.base == (("a.png", 119), (mix.out, 1)) and mix.weight == 0.2


def test_plan_states_without_fade_and_errors() -> None:
    hard = plan_states([StateFrame("a.png", 0), StateFrame("b.png", 1.01)], fps=30, frames=60, fade=0)
    assert hard.base == (("a.png", 31), ("b.png", 29)) and hard.mixes == () and hard.fade_frames == 0
    assert plan_states([], fps=30, frames=60) == plan_states([StateFrame("a.png", 0)], fps=30, frames=0)
    single = plan_states([StateFrame("a.png", 0)], fps=30, frames=60)  # one state: no blank needed
    assert single.base == (("a.png", 60),) and single.mixes == ()
    with pytest.raises(ValueError):
        plan_states([StateFrame("a.png", 0), StateFrame("b.png", 1)], fps=30, frames=60)  # fades need a blank
    late_first = plan_states([StateFrame("card.png", 5.5)], fps=30, frames=300, blank="z.png")
    assert late_first.base[0] == ("z.png", 165) and len(late_first.mixes) == 4  # intro: blank first
    assert {(m.old, m.new) for m in late_first.mixes} == {("z.png", "card.png")}
    with pytest.raises(ValueError):
        plan_states([StateFrame("card.png", 5.5)], fps=30, frames=300, fade=0)


def test_state_stream_filter() -> None:
    base = state_stream_filter(5, fps=30, frames=95, size=(1280, 720), out_label="sb")
    assert base == ("[5:v]settb=1/750,setpts=PTS*25/30,format=rgba,scale=1280:720:flags=lanczos,"
                    "format=yuva420p,fps=30,trim=end_frame=95[sb]")
    same = state_stream_filter(6, fps=30, frames=95, size=None, out_label="sb")
    assert "scale" not in same and "fade" not in same  # cross-fades are mixed PNGs in the stream itself


def test_overlay_filter() -> None:
    ov = overlay_filter("bg", "s1", "v1", start=1.0, end=2.15)
    assert ov == "[bg][s1]overlay=x=0:y=0:eof_action=pass:repeatlast=0:enable='between(t,1,2.15)'[v1]"
    assert "enable" not in overlay_filter("a", "b", "c", x=3, y=4)


# --- audio --------------------------------------------------------------------------------------


def test_audio_mix_exact_length_and_shared_inputs() -> None:
    f = audio_mix_filter([AudioClip(1, delay=0.5), AudioClip(2, delay=3.0, volume=0.8),
                          AudioClip(2, delay=4.0, volume=0.8), AudioClip(3, delay=8.0)], total_samples=480000)
    assert "[2:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=2[s2_0][s2_1]" in f
    assert "adelay=delays=500:all=1" in f and "adelay=delays=3000:all=1" in f and "adelay=delays=8000:all=1" in f
    assert "amix=inputs=4:normalize=0" in f
    assert f.rstrip().endswith("apad=whole_len=480000,atrim=end_sample=480000,asetpts=PTS-STARTPTS[aout]")
    silent = audio_mix_filter([], total_samples=1600)
    assert silent.startswith("anullsrc=r=48000:cl=stereo") and "end_sample=1600" in silent
    single = audio_mix_filter([AudioClip(1, delay=0, duration=5.5, fade_out=0.5)], total_samples=100)
    assert "atrim=duration=5.5,afade=t=out:st=5:d=0.5" in single and "amix" not in single


def test_bgm_filter_ducks_under_narration() -> None:
    f = bgm_filter(narration="1:a", bgm="2:a", volume=0.06, delay=5.5, total=60)
    assert "asplit=2[narr][key]" in f and "sidechaincompress" in f and "[music][key]" in f
    assert "volume=0.0600" in f and "adelay=delays=5500:all=1" in f
    assert "amix=inputs=2:normalize=0:duration=first" in f


# --- full command builders ----------------------------------------------------------------------


def make_spec(n_states: int = 2) -> SegmentSpec:
    spec = SegmentSpec(width=1280, height=720, fps=30, frames=95, fade_in=0.4, crf=23, preset="veryfast",
                       background=video_input("C:/branding/aadhi_left.mp4", loop=True, audio=False),
                       background_kind="video", blank_state="frames/blank.png")
    spec.media.append((video_input("manim.mp4", audio=False), MediaLayer(0, "video", Rect(100, 50, 640, 360),
                                                                         end_behavior="freeze")))
    spec.media.append((image_input("panel.png", 30), MediaLayer(0, "image", Rect(900, 50, 300, 300),
                                                                visible_from=1.2)))
    spec.states = [StateFrame(f"frames/s_{i:04d}.png", i * 3.0 / n_states) for i in range(n_states)]
    tick = Input("tick.wav")
    spec.audio = [(Input("narr.mp3"), AudioClip(0, delay=0.5)), (tick, AudioClip(0, delay=1.0)),
                  (tick, AudioClip(0, delay=2.0))]
    return spec


def test_build_segment_command() -> None:
    cmd = build_segment(make_spec(), name="s001")
    args, graph = cmd.args, cmd.files["s001.filtergraph"]
    assert_whitelisted(args)
    # bg, 2 media, 1 state stream (cross-fades mixed in), narration + tick (opened once)
    assert len(inputs_of(args)) == 1 + 2 + 1 + 2
    i = args.index("s001.states.ffconcat")  # single-threaded PNG decoding: flat memory
    assert args[i - 6:i] == ["concat", "-safe", "1", "-threads", "1", "-i"]
    assert "s001.fades.ffconcat" not in cmd.files
    assert args[args.index("-filter_complex_script") + 1] == "s001.filtergraph"  # ffmpeg 5.1 compatible default
    assert "-/filter_complex" not in args
    assert args[args.index("-frames:v") + 1] == "95"
    assert args[args.index("-crf") + 1] == "23" and args[args.index("-preset") + 1] == "veryfast"
    assert "-video_track_timescale" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[-1] == "s001.wav" and "pcm_s16le" in args and "s001.mp4" in args
    assert "file 'frames/s_0000.png'" in cmd.files["s001.states.ffconcat"]
    mixes = state_mixes(make_spec())
    assert mixes and all(f"file '{m.out}'" in cmd.files["s001.states.ffconcat"] for m in mixes)
    assert all(m.out.startswith("frames/mix-") for m in mixes)  # next to the states
    assert "[1:v]setpts=PTS-STARTPTS" in graph  # media input indices follow the background
    assert "overlay=x=100:y=50" in graph and "enable='between(t,1.2," in graph
    assert "[3:v]settb=1/750" in graph and "scale=1280:720" in graph  # stage-size PNGs scaled to the output
    assert "fade=t=in:st=0:d=0.4" in graph and "trim=end_frame=95" in graph
    assert "asplit=2" in graph and "end_sample=152000" in graph  # 95 frames at 30 fps = 152000 samples
    assert graph.count("[vout]") == 1 and graph.count("[aout]") == 1


def test_build_segment_modern_flag() -> None:
    cmd = build_segment(make_spec(), name="s002", script_flag=SCRIPT_FLAG_MODERN)
    assert cmd.args[cmd.args.index("-/filter_complex") + 1] == "s002.filtergraph"
    assert "-filter_complex_script" not in cmd.args


def test_build_segment_size_is_constant_in_the_number_of_states() -> None:
    small = build_segment(make_spec(3), name="s003")
    big = build_segment(make_spec(1500), name="s003")
    assert len(inputs_of(small.args)) == len(inputs_of(big.args))
    assert small.args == big.args  # argv does not grow with the states
    assert len(" ".join(big.args)) < 2000
    assert big.files["s003.states.ffconcat"].count("file '") >= 90  # the states live in the list files


def test_build_segment_rejects_unsafe_names() -> None:
    with pytest.raises(ValueError):
        build_segment(make_spec(), name="../x")
    spec = make_spec()
    spec.states = [StateFrame("C:/abs/x.png", 0.0)]
    with pytest.raises(ValueError):
        build_segment(spec, name="s1")


def test_build_segment_color_background_without_audio() -> None:
    spec = SegmentSpec(width=640, height=360, fps=25, frames=50, state_size=(640, 360))
    spec.states = [StateFrame("a.png", 0.0)]
    cmd = build_segment(spec, name="v")
    graph = cmd.files["v.filtergraph"]
    assert len(inputs_of(cmd.args)) == 2 and "v.fades.ffconcat" not in cmd.files  # clock + states
    assert cmd.args[inputs_of(cmd.args)[0] + 1] == "color=c=0x1A0B2E:s=640x360:r=25:d=3"
    assert graph.startswith("[0:v]trim=end_frame=50")
    assert "scale=" not in graph.split("[1:v]")[1].split("[sb]")[0]  # same size: no scaling
    assert "anullsrc" in graph and "fade=t=in" not in graph
    bare = build_segment(SegmentSpec(width=640, height=360, fps=25, frames=50), name="w")
    assert set(bare.files) == {"w.filtergraph"} and len(inputs_of(bare.args)) == 1
    still = SegmentSpec(width=640, height=360, fps=25, frames=50, background=image_input("bg.png", 25),
                        background_kind="image")
    still_cmd = build_segment(still, name="x")
    assert [still_cmd.args[i + 1] for i in inputs_of(still_cmd.args)] == ["color=c=0x1A0B2E:s=640x360:r=25:d=3",
                                                                          "bg.png"]
    assert "[1:v]scale=640:360" in still_cmd.files["x.filtergraph"]


def test_build_final_with_bgm_chapters_and_burn_in() -> None:
    spec = FinalSpec(video_list="video.ffconcat", audio_list="audio.ffconcat", metadata="chapters.ffmeta",
                     total_seconds=63.5, output="out.mp4", bgm="C:/b/bgm.mp3", bgm_delay=5.5,
                     burn_subtitles="captions.srt", fonts_dir="fonts", font_name="Noto Sans Tamil", height=720)
    cmd = build_final(spec, filter_script="final.fg")
    args, graph = cmd.args, cmd.files["final.fg"]
    assert_whitelisted(args)
    assert args[args.index("-filter_complex_script") + 1] == "final.fg"
    fmts = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
    assert fmts == ["concat", "concat", "ffmetadata"]
    assert args[args.index("-map_chapters") + 1] == "3" and args[args.index("-map_metadata") + 1] == "3"
    assert "+faststart" in args and args[args.index("-t") + 1] == "63.5"
    assert "subtitles=filename=captions.srt:fontsdir=fonts" in graph and "FontName=Noto Sans Tamil" in graph
    assert "libx264" in args and "sidechaincompress" in graph
    plain = build_final(FinalSpec(video_list="v", audio_list="a", metadata="m", total_seconds=10, output="o.mp4"),
                        filter_script="f", script_flag=SCRIPT_FLAG_MODERN)
    assert args.count("-stream_loop") == 1 and "-stream_loop" not in plain.args
    assert plain.args[plain.args.index("-c:v") + 1] == "copy" and "[1:a]aresample" in plain.files["f"]
    assert plain.args[plain.args.index("-map_chapters") + 1] == "2"
    assert plain.args[plain.args.index("-/filter_complex") + 1] == "f"


def test_escapes() -> None:
    assert filter_escape("C:\\a b:c,d'e[f]") == "C\\:/a b\\:c\\,d\\'e\\[f\\]"
    sub = subtitles_filter("c.srt", fonts_dir=None, font_name="Inter", height=1080)
    assert sub.startswith("subtitles=filename=c.srt:force_style='FontName=Inter,FontSize=22")


def test_parse_probe() -> None:
    data = {"format": {"duration": "12.5", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                         "avg_frame_rate": "30/1"},
                        {"codec_type": "audio", "codec_name": "aac"}],
            "chapters": [{"start_time": "0.0", "end_time": "5.0", "tags": {"title": "Intro"}}]}
    p = parse_probe(data)
    assert (p.duration, p.width, p.height, p.fps) == (12.5, 1280, 720, 30.0)
    assert p.has_video and p.has_audio and p.audio_codec == "aac"
    assert p.chapters == [{"start": 0.0, "end": 5.0, "title": "Intro"}]
    empty = parse_probe({})
    assert empty.duration is None and not empty.has_video


# --- process runner -----------------------------------------------------------------------------


def test_subprocess_env_is_allow_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-123")
    env = subprocess_env()
    assert "GEMINI_API_KEY" not in env and any(k.upper() == "PATH" for k in env)


def test_run_process_success_and_failure_tail() -> None:
    out, _ = asyncio.run(run_process([sys.executable, "-c", "print('hello')"], timeout=30, capture_stdout=True))
    assert out.strip() == b"hello"
    with pytest.raises(FFmpegError) as ei:
        asyncio.run(run_process([sys.executable, "-c", "import sys; sys.stderr.write('x'*20000 + 'BOOM'); sys.exit(3)"],
                                timeout=30))
    assert ei.value.returncode == 3 and ei.value.stderr_tail.endswith("BOOM")
    assert len(ei.value.stderr_tail) <= 8192
    with pytest.raises(FFmpegError, match="not found"):
        asyncio.run(run_process(["definitely-not-a-binary-xyz"], timeout=5))


def _leftovers(before: set[int], marker: str) -> list[psutil.Process]:
    time.sleep(0.5)
    return [p for p in psutil.process_iter(["pid", "cmdline"])
            if p.pid not in before and marker in " ".join(p.info.get("cmdline") or [])]


def test_run_process_timeout_kills_tree() -> None:
    code = ("import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
    before = {p.pid for p in psutil.process_iter()}
    t0 = time.monotonic()
    with pytest.raises(FFmpegError, match="timed out"):
        asyncio.run(run_process([sys.executable, "-c", code], timeout=2))
    assert time.monotonic() - t0 < 20
    assert _leftovers(before, "time.sleep(60)") == []


def test_run_process_honours_cancellation_while_running() -> None:
    """A user cancel kills the running process tree within about a second (no wait for the timeout)."""
    code = ("import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(61)']); time.sleep(61)")
    before = {p.pid for p in psutil.process_iter()}
    t0 = time.monotonic()

    def check() -> None:
        if time.monotonic() - t0 > 1.5:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        asyncio.run(run_process([sys.executable, "-c", code], timeout=600, check_cancelled=check))
    assert time.monotonic() - t0 < 15
    assert _leftovers(before, "time.sleep(61)") == []
