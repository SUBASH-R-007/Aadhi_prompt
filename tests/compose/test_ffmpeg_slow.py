"""Real ffmpeg encodes: segments (state streams, cross-fades, delayed panel clips), Ken-Burns still,
memory bound with many states, concat + BGM + chapters, caption burn-in with libass escaping."""

from __future__ import annotations

import asyncio
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import psutil
import pytest
from PIL import Image, ImageDraw

from aadhi.compose.captions import to_srt
from aadhi.compose.chapters import to_ffmetadata
from aadhi.compose.ffmpeg import (
    AudioClip,
    Command,
    FinalSpec,
    Input,
    MediaLayer,
    Rect,
    SegmentSpec,
    StateFrame,
    build_final,
    build_segment,
    concat_list,
    ffmpeg_base,
    image_input,
    probe,
    run_ffmpeg,
    state_mixes,
    subprocess_env,
    video_input,
)
from aadhi.compose.frames import transparent_png, write_state_mixes
from aadhi.config import ROOT_DIR, Settings
from aadhi.schemas.timeline import CaptionCue, Chapter, KenBurns, KenBurnsPoint

pytestmark = pytest.mark.slow

FPS = 30
W, H = 640, 360


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_preset="ultrafast", render_crf=28)


def write_png(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int], size=(1920, 1080)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=(*color, 255))
    im.save(path)
    return path


def write_wav(path: Path, seconds: float, freq: float = 440.0, rate: int = 24000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    data = (0.4 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def make_clip(path: Path, settings: Settings, seconds: float = 1.0) -> Path:
    asyncio.run(run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=s=320x180:r=24:d={seconds}", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(path)], settings=settings))
    return path


def make_numbered_clip(path: Path, settings: Settings, frames: int = 30) -> Path:
    """Clip whose frame i is uniformly grey 8*i (lossless), so the shown frame can be identified."""
    src = path.parent / "numbered"
    src.mkdir(exist_ok=True)
    for i in range(frames):
        Image.new("RGB", (160, 160), (8 * i, 8 * i, 8 * i)).save(src / f"f{i:03d}.png")
    asyncio.run(run_ffmpeg(["-framerate", str(FPS), "-i", str(src / "f%03d.png"), "-c:v", "libx264", "-qp", "0",
                            "-preset", "ultrafast", "-pix_fmt", "yuv444p", str(path)], settings=settings))
    return path


def decode_frames(video: Path, settings: Settings, size: tuple[int, int] = (W, H)) -> np.ndarray:
    """All frames of ``video`` as an (n, h, w, 3) uint8 array (exact frame indexing, no seeking)."""
    raw = video.with_suffix(".rgb")
    asyncio.run(run_ffmpeg(["-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw)], settings=settings))
    return np.fromfile(raw, dtype=np.uint8).reshape(-1, size[1], size[0], 3)


def frame_at(video: Path, t: float, settings: Settings, out: Path) -> np.ndarray:
    asyncio.run(run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)], settings=settings))
    with Image.open(out) as im:
        return np.asarray(im.convert("RGB")).astype(int)


def wav_samples(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path)) as w:
        return w.getnframes(), w.getframerate(), w.getnchannels()


def write_command(cmd: Command, work: Path, spec: SegmentSpec | None = None) -> None:
    for name, text in cmd.files.items():
        (work / name).write_text(text, encoding="utf-8")
    if spec is not None:  # the cross-fade frames the state list names (the renderer writes them too)
        write_state_mixes(state_mixes(spec), work)


def encode(spec: SegmentSpec, work: Path, name: str, settings: Settings) -> tuple[Path, Path]:
    cmd = build_segment(spec, name=name)
    write_command(cmd, work, spec)
    asyncio.run(run_ffmpeg(cmd.args, settings=settings, cwd=work, timeout=600))
    return work / f"{name}.mp4", work / f"{name}.wav"


def test_segment_from_pngs_clip_and_audio(tmp_path: Path, settings: Settings) -> None:
    frames = 100  # 3.333 s
    write_png(tmp_path / "frames" / "s0.png", (255, 0, 0), (60, 600, 460, 1000))
    write_png(tmp_path / "frames" / "s1.png", (0, 255, 0), (60, 600, 460, 1000))
    transparent_png(tmp_path / "frames" / "blank.png", (1920, 1080))
    clip = make_clip(tmp_path / "clip.mp4", settings)
    narration = write_wav(tmp_path / "narr.wav", 1.5)
    tick = write_wav(tmp_path / "tick.wav", 0.08, freq=1800)
    mascot = ROOT_DIR / "video_template" / "aadhi_left.mp4"
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=frames, fade_in=0.4, crf=28, preset="ultrafast",
                       blank_state="frames/blank.png")
    if mascot.exists():
        spec.background, spec.background_kind = video_input(mascot, loop=True, audio=False), "video"
    spec.media.append((video_input(clip, audio=False),
                       MediaLayer(0, "video", Rect(320, 20, 300, 170), fit="contain", end_behavior="freeze")))
    spec.states = [StateFrame("frames/s0.png", 0.0), StateFrame("frames/s1.png", 1.5)]
    tick_in = Input(str(tick))
    spec.audio = [(Input(str(narration)), AudioClip(0, delay=0.5)), (tick_in, AudioClip(0, delay=2.0)),
                  (tick_in, AudioClip(0, delay=3.0))]
    video, audio = encode(spec, tmp_path, "seg", settings)

    info = asyncio.run(probe(video, settings=settings))
    assert info.has_video and not info.has_audio and (info.width, info.height) == (W, H)
    assert info.duration == pytest.approx(frames / FPS, abs=1 / FPS + 1e-3)
    assert wav_samples(audio) == (round(frames * 48000 / FPS), 48000, 2)  # exact length, 48 kHz stereo

    px = decode_frames(video, settings)
    assert len(px) == frames
    region = (slice(int(800 / 3), int(980 / 3)), slice(int(80 / 3), int(440 / 3)))  # inside the PNG boxes

    def rg(i: int) -> tuple[float, float]:
        return float(px[i][region][..., 0].mean()), float(px[i][region][..., 1].mean())

    r, g = rg(30)
    assert r > 180 and g < 80  # state 0 (red)
    r, g = rg(84)
    assert g > 180 and r < 80  # state 1 (green), after its cross-fade
    # cross-fade: state 1 starts at frame 45 and reaches full opacity at frame 49
    r45, g45 = rg(45)
    r47, g47 = rg(47)
    assert r45 > r47 > rg(49)[0] and g45 < g47 < rg(49)[1]
    assert 20 < g45 < 120 and 60 < r45 < 230  # alpha 1/5 of green over red
    assert rg(44)[1] < 30  # no green before the state starts
    media = px[90][30 // 3 * 3:180, 330:610]
    assert media.std() > 20  # frozen last frame of the test clip, not flat background
    assert px[0].mean() < px[30].mean()  # fade-in from black

    # narration audible in its window, silence before it
    with wave.open(str(audio)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
    assert np.abs(pcm[: int(0.4 * 48000)]).max() < 50
    assert np.abs(pcm[int(0.7 * 48000): int(1.8 * 48000)]).max() > 5000


@pytest.mark.parametrize("end_behavior", ["freeze", "loop"])
def test_panel_clip_starts_at_show_at(tmp_path: Path, settings: Settings, end_behavior: str) -> None:
    """The panel's frame at show_at is clip frame 0; loops wrap relative to show_at (videoTimeAt)."""
    clip = make_numbered_clip(tmp_path / "numbered.mp4", settings, frames=30)  # 1 s clip
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=150, crf=1, preset="ultrafast")
    rect = Rect(400, 100, 160, 160)
    spec.media.append((video_input(clip, audio=False, loop=end_behavior == "loop"),
                       MediaLayer(0, "video", rect, fit="contain", end_behavior=end_behavior,  # type: ignore[arg-type]
                                  visible_from=2.0)))
    video, _ = encode(spec, tmp_path, "panel", settings)
    px = decode_frames(video, settings)

    def clip_frame(i: int) -> int:
        return round(float(px[i][130:230, 430:530].mean()) / 8)

    background = px[30][130:230, 430:530].mean()
    assert abs(background - px[30][10:50, 10:50].mean()) < 3  # panel hidden before show_at
    assert clip_frame(60) == 0  # t = show_at: first clip frame
    assert abs(clip_frame(75) - 15) <= 1  # t = show_at + 0.5 s
    assert abs(clip_frame(89) - 29) <= 1
    if end_behavior == "freeze":
        assert abs(clip_frame(120) - 29) <= 1  # frozen on the last frame
    else:
        assert abs(clip_frame(105) - 15) <= 1  # wrapped: (105 - 60) % 30
        assert clip_frame(120) == 0


def test_intro_like_late_first_state(tmp_path: Path, settings: Settings) -> None:
    """A first state after frame 0 shows the transparent blank (logo underneath) before it."""
    write_png(tmp_path / "frames" / "card.png", (255, 255, 0), (0, 0, 1919, 1079))
    transparent_png(tmp_path / "frames" / "blank.png", (1920, 1080))
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=60, crf=20, preset="ultrafast",
                       blank_state="frames/blank.png", background_color="#000000")
    spec.states = [StateFrame("frames/card.png", 1.0)]
    video, _ = encode(spec, tmp_path, "intro", settings)
    px = decode_frames(video, settings)
    assert px[20].mean() < 10  # blank (black colour background)
    assert px[40][..., 0].mean() > 200 and px[40][..., 2].mean() < 60  # the yellow card


def test_many_states_keep_memory_and_argv_constant(tmp_path: Path, settings: Settings) -> None:
    """120 distinct 1080p state PNGs in a 1080p segment: bounded RSS, short command line."""
    n = 120
    frames_dir = tmp_path / "frames"
    for i in range(n):
        write_png(frames_dir / f"s{i:04d}.png", ((i * 37) % 256, (i * 91) % 256, 200), (100 + i, 100, 900 + i, 900))
    transparent_png(frames_dir / "blank.png", (1920, 1080))
    spec = SegmentSpec(width=1920, height=1080, fps=FPS, frames=600, crf=35, preset="ultrafast",
                       blank_state="frames/blank.png")
    spec.states = [StateFrame(f"frames/s{i:04d}.png", i * 20.0 / n) for i in range(n)]
    cmd = build_segment(spec, name="many")
    write_command(cmd, tmp_path, spec)
    argv = [*ffmpeg_base(settings), *cmd.args]
    assert len(" ".join(argv)) < 2500
    proc = subprocess.Popen(argv, cwd=tmp_path, env=subprocess_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    peak = 0
    ps = psutil.Process(proc.pid)
    t0 = time.monotonic()
    while proc.poll() is None:
        try:
            peak = max(peak, ps.memory_info().rss)
        except psutil.Error:
            break
        if time.monotonic() - t0 > 600:
            proc.kill()
            pytest.fail("encode took too long")
        time.sleep(0.05)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    assert proc.returncode == 0, err[-500:]
    assert peak < 800 * 1024 * 1024, f"peak RSS {peak / 2**20:.0f} MB"  # was ~1.5 GB with 40 PNG inputs
    info = asyncio.run(probe(tmp_path / "many.mp4", settings=settings))
    assert info.duration == pytest.approx(20.0, abs=0.05)


def test_ken_burns_still_segment(tmp_path: Path, settings: Settings) -> None:
    im = Image.new("RGB", (800, 450), (20, 20, 60))
    d = ImageDraw.Draw(im)
    for x in range(0, 800, 40):
        d.line([x, 0, x, 450], fill=(255, 255, 0), width=3)
    im.save(tmp_path / "still.jpg")
    kb = KenBurns(start=KenBurnsPoint(cx=0.5, cy=0.5, scale=1.0), end=KenBurnsPoint(cx=0.6, cy=0.5, scale=1.6))
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=60, crf=28, preset="ultrafast")
    spec.media.append((image_input(tmp_path / "still.jpg", FPS),
                       MediaLayer(0, "image", Rect(0, 0, W, H), fit="cover", ken_burns=kb)))
    video, _ = encode(spec, tmp_path, "kb", settings)
    info = asyncio.run(probe(video, settings=settings))
    assert info.duration == pytest.approx(2.0, abs=0.05)
    a = frame_at(video, 0.0, settings, tmp_path / "a.png")
    b = frame_at(video, 1.9, settings, tmp_path / "b.png")

    def stripes(frame: np.ndarray) -> int:  # yellow vertical lines crossing the middle row
        row = frame[H // 2, :, :]
        yellow = (row[:, 0] > 150) & (row[:, 1] > 150) & (row[:, 2] < 120)
        return int(np.count_nonzero(np.diff(yellow.astype(int)) == 1))

    assert stripes(a) > stripes(b) + 3  # zoomed in: fewer stripes visible at the end


def test_concat_bgm_and_chapters(tmp_path: Path, settings: Settings) -> None:
    names = []
    for n, color in enumerate([(255, 0, 0), (0, 0, 255)]):
        write_png(tmp_path / f"p{n}.png", color, (0, 0, 1919, 1079))
        narr = write_wav(tmp_path / f"n{n}.wav", 1.0, freq=300 + 200 * n)
        spec = SegmentSpec(width=W, height=H, fps=FPS, frames=75 + n * 15, crf=28, preset="ultrafast")
        spec.states.append(StateFrame(f"p{n}.png", 0.0))
        spec.audio.append((Input(str(narr)), AudioClip(0, delay=0.3)))
        encode(spec, tmp_path, f"seg{n}", settings)
        names.append(f"seg{n}")
    total = (75 + 90) / FPS
    (tmp_path / "v.ffconcat").write_text(concat_list([f"{n}.mp4" for n in names]), encoding="utf-8")
    (tmp_path / "a.ffconcat").write_text(concat_list([f"{n}.wav" for n in names]), encoding="utf-8")
    (tmp_path / "ch.ffmeta").write_text(
        to_ffmetadata([Chapter(start=0, title="Intro"), Chapter(start=2.5, title="Part = two")], total,
                      title="Test"), encoding="utf-8")
    bgm = ROOT_DIR / "video_template" / "bgm.mp3"
    spec = FinalSpec(video_list="v.ffconcat", audio_list="a.ffconcat", metadata="ch.ffmeta", total_seconds=total,
                     output="final.mp4", bgm=str(bgm) if bgm.exists() else None, bgm_volume=0.2, bgm_delay=0.5,
                     width=W, height=H, fps=FPS)
    cmd = build_final(spec, filter_script="final.fg")
    write_command(cmd, tmp_path)
    asyncio.run(run_ffmpeg(cmd.args, settings=settings, cwd=tmp_path, timeout=300))
    info = asyncio.run(probe(tmp_path / "final.mp4", settings=settings))
    assert info.duration == pytest.approx(total, abs=0.1)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    assert [c["title"] for c in info.chapters] == ["Intro", "Part = two"]
    assert info.chapters[1]["start"] == pytest.approx(2.5)
    red = frame_at(tmp_path / "final.mp4", 1.0, settings, tmp_path / "r.png")
    blue = frame_at(tmp_path / "final.mp4", 4.0, settings, tmp_path / "b.png")
    assert red[..., 0].mean() > 180 and blue[..., 2].mean() > 180


def _burn(tmp_path: Path, settings: Settings, srt: str, name: str) -> np.ndarray:
    work = tmp_path / name
    work.mkdir()
    spec = SegmentSpec(width=W, height=H, fps=FPS, frames=45, crf=28, preset="ultrafast")
    encode(spec, work, "seg", settings)
    (work / "v.ffconcat").write_text(concat_list(["seg.mp4"]), encoding="utf-8")
    (work / "a.ffconcat").write_text(concat_list(["seg.wav"]), encoding="utf-8")
    (work / "m.ffmeta").write_text(to_ffmetadata([Chapter(start=0, title="A")], 1.5), encoding="utf-8")
    (work / "c.srt").write_text(srt, encoding="utf-8")
    (work / "fonts").mkdir()
    spec2 = FinalSpec(video_list="v.ffconcat", audio_list="a.ffconcat", metadata="m.ffmeta", total_seconds=1.5,
                      output="out.mp4", burn_subtitles="c.srt", fonts_dir="fonts", width=W, height=H, fps=FPS,
                      crf=28, preset="ultrafast")
    cmd = build_final(spec2, filter_script="f.fg")
    write_command(cmd, work)
    asyncio.run(run_ffmpeg(cmd.args, settings=settings, cwd=work, timeout=300))
    return frame_at(work / "out.mp4", 0.7, settings, work / "s.png")


def test_burn_in_subtitles(tmp_path: Path, settings: Settings) -> None:
    frame = _burn(tmp_path, settings, "1\n00:00:00,000 --> 00:00:01,500\nHELLO WORLD\n", "plain")
    bottom = frame[H - 80:H - 10, :, :]
    assert (bottom > 200).all(axis=2).sum() > 50  # white caption glyphs near the bottom


def test_burn_in_keeps_backslashes_and_braces_literal(tmp_path: Path, settings: Settings) -> None:
    """'C:\\New' must not become a line break and '{x}' must not vanish (libass overrides)."""
    cue = [CaptionCue(start=0.0, end=1.5, text="OPEN C:\\NEW {SET} NOW")]

    def text_lines(frame: np.ndarray) -> int:  # bands of rows containing caption pixels
        rows = (frame > 200).all(axis=2).any(axis=1)
        return int(np.count_nonzero(rows[1:] & ~rows[:-1]) + int(rows[0]))

    def text_cols(frame: np.ndarray) -> int:
        lit = (frame > 200).all(axis=2)
        return int(np.count_nonzero(lit.any(axis=0)))

    escaped = _burn(tmp_path, settings, to_srt(cue, burn_in=True), "escaped")
    naive = _burn(tmp_path, settings, to_srt(cue), "naive")
    assert text_lines(escaped) == 1 and text_lines(naive) == 2  # the naive SRT breaks the line at "\N"
    assert text_cols(escaped) > text_cols(naive)  # and hides "{SET}"; the escaped one shows all of it
    ass = tmp_path / "escaped.ass"
    (tmp_path / "escaped.srt").write_text(to_srt(cue, burn_in=True), encoding="utf-8")
    asyncio.run(run_ffmpeg(["-i", str(tmp_path / "escaped.srt"), str(ass)], settings=settings))
    dialogue = [ln for ln in ass.read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue:")][0]
    assert "\\N" not in dialogue and "\\{SET\\}" in dialogue
