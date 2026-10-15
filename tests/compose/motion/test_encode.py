"""ffmpeg commands of the motion engine: the chunk encoder exactly as §7.5, media extraction, the classic audio mix
and the final mux flags (§2.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.compose.ffmpeg import SCRIPT_FLAG_MODERN, concat_list
from aadhi.compose.motion.encode import (
    AUDIO_BITRATE,
    CHUNK_VF,
    EncoderSpec,
    MasterAudioSpec,
    MotionFinalSpec,
    SceneAudioSpec,
    extract_args,
    ffmpeg_pipe_base,
    final_mux_command,
    frame_runs,
    master_audio_command,
    scene_audio_command,
)
from aadhi.compose.motion.engine import scene_audio_spec
from aadhi.compose.motion.media import MediaRequest, file_rate, frame_files, native_frames
from aadhi.compose.video import SceneInputs, scene_segment_spec, segment_frames
from aadhi.config import Settings

from .fixtures import dynamic_timeline

# The design's encoder (§7.5) with the defaults MOTION_X264_PRESET=veryfast and MOTION_X264_CRF=17, as B0's
# feature checks run it on ffmpeg 5.1 (tests/compose/test_ffmpeg_features.py CHUNK_VF / CHUNK_ENCODE).
DESIGN_VF = ("scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
             "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709")
DESIGN_ENCODE = ["-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "17", "-maxrate", "12M",
                 "-bufsize", "24M", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", "-bf", "2",
                 "-x264-params", "open-gop=0"]


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_chunk_encoder_is_the_design_command() -> None:
    enc = EncoderSpec.from_settings(_settings(), 30, (1920, 1080))
    args = enc.chunk_args(frames=450, output="chunk_0003.ts", capture=(1920, 1080))
    assert CHUNK_VF == DESIGN_VF
    assert args == ["-protocol_whitelist", "file,pipe", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", "30",
                    "-i", "pipe:0", "-vf", DESIGN_VF, *DESIGN_ENCODE, "-threads", "4", "-frames:v", "450", "-an",
                    "-f", "mpegts", "chunk_0003.ts"]
    assert EncoderSpec(threads=None).codec_args() == DESIGN_ENCODE  # the design's parameters, plus a fixed thread count
    base = ffmpeg_pipe_base(_settings())
    assert "-nostdin" not in base and base[0] == "ffmpeg" and "-y" in base  # frames arrive on stdin


def test_chunk_encoder_settings_and_scaling() -> None:
    enc = EncoderSpec.from_settings(_settings(motion_x264_preset="faster", motion_x264_crf=20), 30, (1280, 720))
    args = enc.chunk_args(frames=10, output="chunk_0000.ts", capture=(1920, 1080))
    assert args[args.index("-preset") + 1] == "faster" and args[args.index("-crf") + 1] == "20"
    assert args[args.index("-vf") + 1] == "scale=1280:720:flags=lanczos," + DESIGN_VF  # a 720p draft
    assert enc.fingerprint((1920, 1080)) != EncoderSpec.from_settings(_settings(), 30, (1280, 720)).fingerprint(
        (1920, 1080))
    for bad in ("../x.ts", "a/b.ts", "", "x y.ts"):
        with pytest.raises(ValueError):
            enc.chunk_args(frames=1, output=bad, capture=(1920, 1080))
    with pytest.raises(Exception):  # noqa: B017 - pydantic validation error
        _settings(motion_x264_preset="placebo-ish")


def test_frame_runs() -> None:
    assert frame_runs([5, 3, 4, 4, 10, 11, 30]) == [(3, 3), (10, 2), (30, 1)]
    assert frame_runs([5, 3, 4, 10, 11, 30], max_gap=6) == [(3, 9), (30, 1)]
    assert frame_runs([]) == [] and frame_runs([0]) == [(0, 1)]
    assert frame_runs([239, 0, 1, 238]) == [(0, 2), (238, 2)]  # a loop wrapping around


def test_extract_args_seek_half_a_frame_early() -> None:
    args = extract_args("C:/media/clip.mp4", first=48, count=30, src_fps=24, size=(960, 540), out_dir="w/m")
    assert args[:2] == ["-ss", f"{47.5 / 24:.6f}"]
    assert args[args.index("-i") + 1] == "C:/media/clip.mp4" and args.index("-ss") < args.index("-i")
    assert args[args.index("-vf") + 1] == "scale=w=960:h=540:force_original_aspect_ratio=increase:flags=bicubic"
    assert args[args.index("-frames:v") + 1] == "30" and args[args.index("-q:v") + 1] == "3"
    assert args[args.index("-start_number") + 1] == "48" and args[-1] == "w/m/f_%06d.jpg"
    first = extract_args("c.mp4", first=0, count=1, src_fps=30, size=(2, 2), out_dir="o")
    assert first[:2] == ["-ss", "0.000000"]
    resampled = extract_args("c.mp4", first=30, count=5, src_fps=30, size=(64, 36), out_dir="o", resample=True)
    assert resampled[:2] == ["-ss", "1.000000"] and resampled[resampled.index("-vf") + 1].startswith("fps=30,")
    with pytest.raises(ValueError):
        extract_args("c.mp4", first=0, count=0, src_fps=30, size=(2, 2), out_dir="o")


def test_frame_files_hold_the_last_frame_past_the_end(tmp_path: Path) -> None:
    req = MediaRequest("video-x", "video", 30.0, (8, 9, 10, 11), (4, 4))
    for i in (8, 9):
        (tmp_path / f"f_{i:06d}.jpg").write_bytes(b"jpg")
    files, missing = frame_files(req, tmp_path)
    assert missing == [10, 11]
    assert files[("video-x", 10)] == files[("video-x", 11)] == tmp_path / "f_000009.jpg"
    req2 = MediaRequest("video-x", "video", 30.0, (5, 8), (4, 4))
    files2, missing2 = frame_files(req2, tmp_path)
    assert missing2 == [5] and files2[("video-x", 5)] == tmp_path / "f_000008.jpg"  # before the first: the first
    with pytest.raises(ValueError):
        frame_files(MediaRequest("video-x", "video", 30.0, (100,), (4, 4)), tmp_path)


def test_native_frames_are_a_pure_function_of_the_page_index() -> None:
    """A 24 fps file on the 30 fps page grid: index n shows file frame floor(n * 24 / 30), whatever else is asked."""
    assert file_rate(30.0, 24.0) == 24.0 and file_rate(30.0, 30.004) == 30.0 and file_rate(30.0, None) == 30.0
    assert file_rate(30.0, float("nan")) == 30.0 and file_rate(24.0, 0.0) == 24.0
    whole = native_frames(range(0, 120), src_fps=30.0, rate=24.0)
    assert [whole[n] for n in range(13, 19)] == [10, 11, 12, 12, 13, 14]
    assert all(whole[n] == (n * 24) // 30 for n in whole)
    for start in range(0, 60):  # a run starting anywhere maps every index the same way
        part = native_frames(range(start, start + 30), src_fps=30.0, rate=24.0)
        assert all(part[n] == whole[n] for n in part)
    assert native_frames([5, 7], src_fps=30.0, rate=30.0) == {5: 5, 7: 7}
    sixty = native_frames([0, 1, 2, 3], src_fps=30.0, rate=60.0)
    assert sixty == {0: 0, 1: 2, 2: 4, 3: 6}


def test_frame_files_maps_page_indices_to_native_frames(tmp_path: Path) -> None:
    req = MediaRequest("video-x", "video", 30.0, (13, 14, 15, 16, 40), (4, 4))
    native = native_frames(req.frames, src_fps=30.0, rate=24.0)
    for i in (10, 11, 12, 13):
        (tmp_path / f"f_{i:06d}.jpg").write_bytes(b"jpg")
    files, missing = frame_files(req, tmp_path, native=native)
    assert files[("video-x", 15)] == files[("video-x", 16)] == tmp_path / "f_000012.jpg"  # one source frame, two slots
    assert files[("video-x", 13)] == tmp_path / "f_000010.jpg"
    assert missing == [40] and files[("video-x", 40)] == tmp_path / "f_000012.jpg"  # past the end: the last frame held


def test_scene_audio_matches_the_classic_mix() -> None:
    """The motion engine places narration, ticks and the ding exactly where the classic segment does (§7.11)."""
    settings = _settings()
    tl = dynamic_timeline([3.0, 4.0, 5.0], quiz_scene=2)
    bounds = segment_frames(tl, 30)
    for i, scene in enumerate(tl.scenes):
        classic = scene_segment_spec(scene, [], timeline=tl, settings=settings, frames=bounds[i],
                                     inputs=SceneInputs(narration=Path("n.wav"), tick=Path("t.wav"),
                                                        ding=Path("d.wav")))
        mine = scene_audio_spec(scene, bounds[i], fps=30, name=f"aud_{i:04d}", narration="n.wav", tick="t.wav",
                                ding="d.wav")
        assert [(Path(p).name, c.delay, c.volume) for p, c in mine.clips] == [
            (Path(inp.path).name, c.delay, c.volume) for inp, c in classic.audio]
        assert mine.samples == classic.total_samples
    assert len(scene_audio_spec(tl.scenes[2], bounds[2], fps=30, name="a", narration="n", tick="t",
                                ding="d").clips) == 4  # narration + 2 ticks + ding


def test_scene_audio_command() -> None:
    spec = SceneAudioSpec(name="aud_0001", samples=144000, clips=(
        ("C:/x/n.wav", scene_audio_spec(dynamic_timeline([3.0]).scenes[0], (0, 90), fps=30, name="a",
                                        narration="C:/x/n.wav", tick=None, ding=None).clips[0][1]),))
    cmd = scene_audio_command(spec, script_flag=SCRIPT_FLAG_MODERN)
    assert cmd.args[-1] == "aud_0001.wav" and "-/filter_complex" in cmd.args
    assert "apad=whole_len=144000" in cmd.files["aud_0001.filtergraph"]
    silent = scene_audio_command(SceneAudioSpec(name="aud_0002", samples=4800), script_flag=SCRIPT_FLAG_MODERN)
    assert "-i" not in silent.args and "anullsrc" in silent.files["aud_0002.filtergraph"]
    with pytest.raises(ValueError):
        scene_audio_command(SceneAudioSpec(name="../x", samples=1), script_flag=SCRIPT_FLAG_MODERN)


def test_master_audio_is_encoded_once_aac_48k_stereo_384k() -> None:
    """The master track (§2.8 audio, §7.11) is encoded while the frames are captured; the final mux copies it."""
    cmd = master_audio_command(MasterAudioSpec(audio_list="audio.ffconcat", total_seconds=12.5),
                               script_flag=SCRIPT_FLAG_MODERN)
    a = cmd.args
    assert a[a.index("-c:a") + 1] == "aac" and a[a.index("-b:a") + 1] == AUDIO_BITRATE == "384k"
    assert a[a.index("-ar") + 1] == "48000" and a[a.index("-ac") + 1] == "2" and a[a.index("-t") + 1] == "12.5"
    assert a.count("concat") == 1 and "-vn" in a and a[-3:] == ["-f", "mp4", "master.m4a"]
    assert "[0:a]aresample=48000" in cmd.files["master.filtergraph"] and "-stream_loop" not in a
    bgm = master_audio_command(MasterAudioSpec(audio_list="a", total_seconds=5, bgm="C:/b/bgm.mp3", bgm_volume=0.05),
                               script_flag=SCRIPT_FLAG_MODERN)
    graph = bgm.files["master.filtergraph"]
    assert "sidechaincompress" in graph and "[0:a]" in graph and "[1:a]" in graph and "volume=0.0500" in graph
    assert "-stream_loop" in bgm.args


def test_final_mux_starts_at_zero_with_chapters_and_the_caption_track() -> None:
    spec = MotionFinalSpec(video_list="chunks.ffconcat", audio="master.m4a", metadata="chapters.ffmeta",
                           total_seconds=12.5, output="motion.mp4", soft_subtitles="captions.soft.srt",
                           subtitle_language="tam")
    cmd = final_mux_command(spec)
    a = cmd.args
    assert a[a.index("-movflags") + 1] == "+faststart+negative_cts_offsets"
    assert ["-map", "0:v", "-c:v", "copy"] == a[a.index("-map"):a.index("-map") + 4]  # chunks are never re-encoded
    assert ["-map", "1:a", "-c:a", "copy"] == a[a.index("-c:v") + 2:a.index("-c:v") + 6]  # nor is the master audio
    assert "-b:a" not in a and "-filter_complex" not in a and cmd.files == {}
    assert a[a.index("-c:s") + 1] == "mov_text" and a[a.index("-disposition:s:0") + 1] == "default"
    assert "language=tam" in a and a[a.index("-map_chapters") + 1] == "2" and a[a.index("-t") + 1] == "12.5"
    assert a.count("concat") == 1 and "ffmetadata" in a and a[-1] == "motion.mp4"
    assert a[a.index("master.m4a") - 3:a.index("master.m4a")] == ["-f", "mp4", "-i"]
    clean = final_mux_command(MotionFinalSpec(video_list="v", audio="m.m4a", metadata="m", total_seconds=5,
                                              output="o.mp4"))
    assert "-c:s" not in clean.args
    assert concat_list(["chunk_0000.ts", "chunk_0001.ts"]).splitlines()[1:] == [
        "file 'chunk_0000.ts'", "file 'chunk_0001.ts'"]
