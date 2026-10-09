"""Branding mascot assets: every clip has a poster, no clip has baked-in black bars, and the cleaned
left clip (aadhi_left_clean.mp4) matches the original except where the bars were."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aadhi.compose.base import MASCOT_CLIPS
from aadhi.config import ROOT_DIR, Settings

BRANDING = ROOT_DIR / "video_template"
W, H = 1280, 720


def poster_for(clip: str) -> Path:
    """Mirror of web/js/player/mascot.js posterUrl(): <name>.mp4 -> posters/<name>.jpg."""
    return BRANDING / "posters" / f"{Path(clip).stem}.jpg"


def test_every_mascot_clip_has_a_frame_size_poster() -> None:
    for clip in sorted(set(MASCOT_CLIPS.values())):
        assert (BRANDING / clip).is_file(), clip
        poster = poster_for(clip)
        assert poster.is_file(), poster
        with Image.open(poster) as im:
            assert im.format == "JPEG" and im.size == (W, H), poster


def test_posters_and_clean_clip_are_served_from_branding() -> None:
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from aadhi.api.static import BRANDING_CACHE, BrandingStaticFiles

    app = Starlette(routes=[Mount("/branding", BrandingStaticFiles(directory=BRANDING), name="branding")])
    with TestClient(app) as client:
        r = client.get("/branding/posters/aadhi_left_clean.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert r.headers["cache-control"] == BRANDING_CACHE
        r = client.get("/branding/aadhi_left_clean.mp4", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"


def test_original_left_clip_and_poster_are_kept() -> None:
    # Timelines stored before the switch (and v1 HTML exports) still reference the original clip.
    assert MASCOT_CLIPS["left"] == "aadhi_left_clean.mp4"
    assert (BRANDING / "aadhi_left.mp4").is_file()
    assert poster_for("aadhi_left.mp4").is_file()


VEO_CLIPS = ("aadhi_center", "aadhi_right", "aadhi_popup", "no_aadhi")  # had the "Veo" watermark


def test_watermarked_clips_are_retired_for_their_clean_copies() -> None:
    from aadhi.compose.base import RETIRED_MASCOT_CLIPS

    for name in VEO_CLIPS:
        clean = f"{name}_clean.mp4"
        assert clean in MASCOT_CLIPS.values(), clean
        assert RETIRED_MASCOT_CLIPS[f"{name}.mp4"] == clean
        # the originals stay for v1 exports and as the source of the re-encode
        assert (BRANDING / f"{name}.mp4").is_file() and poster_for(f"{name}.mp4").is_file()
    assert not set(RETIRED_MASCOT_CLIPS) & set(MASCOT_CLIPS.values()), "a retired clip is still in use"
    assert set(RETIRED_MASCOT_CLIPS.values()) <= set(MASCOT_CLIPS.values())


def test_the_image_copies_every_mascot_clip() -> None:
    """The Dockerfile copies branding files by an allow-list: a renamed clip must still be in the image."""
    from fnmatch import fnmatch

    from aadhi.compose.base import LOGO_VIDEO, RETIRED_BRANDING_FILES, RETIRED_MASCOT_CLIPS

    text = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    patterns = [p[len("video_template/"):] for line in text.splitlines() if line.startswith("COPY ")
                for p in line.split() if p.startswith("video_template/") and not p.endswith("/")]
    assert patterns, "no video_template COPY line found"
    for clip in sorted(set(MASCOT_CLIPS.values()) | set(RETIRED_MASCOT_CLIPS)):
        assert any(fnmatch(clip, p) for p in patterns), f"{clip} is not copied into the image"
    for name in sorted({LOGO_VIDEO, *RETIRED_BRANDING_FILES, *RETIRED_BRANDING_FILES.values()}):
        assert any(fnmatch(name, p) for p in patterns), f"{name} is not copied into the image"


def test_the_intro_logo_is_the_clean_copy_and_the_original_stays() -> None:
    from aadhi.compose.base import LOGO_VIDEO, RETIRED_BRANDING_FILES

    assert LOGO_VIDEO == "logo_animation_clean.mp4"
    assert RETIRED_BRANDING_FILES == {"logo_animation.mp4": LOGO_VIDEO}
    assert (BRANDING / LOGO_VIDEO).is_file()
    assert (BRANDING / "logo_animation.mp4").is_file()  # the source of the re-encode stays


# --- real ffmpeg ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tools() -> tuple[str, str]:
    settings = Settings(_env_file=None)
    ffmpeg, ffprobe = shutil.which(settings.ffmpeg_path), shutil.which(settings.ffprobe_path)
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe not installed")
    return ffmpeg, ffprobe


def video_info(ffprobe: str, path: Path) -> dict:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=width,height,r_frame_rate,nb_read_frames,pix_fmt:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, timeout=120,
    ).stdout
    data = json.loads(out)
    s = data["streams"][0]
    return {"size": (s["width"], s["height"]), "fps": s["r_frame_rate"], "frames": int(s["nb_read_frames"]),
            "pix_fmt": s["pix_fmt"], "duration": round(float(data["format"]["duration"]), 2)}


def luma_frame(ffmpeg: str, path: Path, frame: int) -> np.ndarray:
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True, timeout=120,
    ).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(H, W).astype(float)


@pytest.mark.slow
def test_no_mascot_clip_has_black_bars(tools: tuple[str, str]) -> None:
    ffmpeg, _ = tools
    for clip in sorted(set(MASCOT_CLIPS.values())):
        log = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(BRANDING / clip), "-vf", "cropdetect=limit=24:round=2:reset=0",
             "-f", "null", "-"],
            check=True, capture_output=True, text=True, timeout=300,
        ).stderr
        crops = re.findall(r"crop=(\d+:\d+:\d+:\d+)", log)
        assert crops and crops[-1] == f"{W}:{H}:0:0", f"{clip}: {crops[-1] if crops else 'no cropdetect output'}"


@pytest.mark.slow
def test_clean_left_clip_matches_the_original_outside_the_bars(tools: tuple[str, str]) -> None:
    ffmpeg, ffprobe = tools
    original, clean = BRANDING / "aadhi_left.mp4", BRANDING / MASCOT_CLIPS["left"]
    assert video_info(ffprobe, clean) == video_info(ffprobe, original)  # same size, fps, frames (loop), duration
    for n in (0, 148):  # 148: Aadhi's hand touches the old bar edge
        a, b = luma_frame(ffmpeg, original, n), luma_frame(ffmpeg, clean, n)
        assert a[:, :40].mean() < 30 and b[:, :52].min() > 60, "left pillar filled"
        assert b[:, 1226:].min() > 60 and b[:4, 52:1226].min() > 60, "right pillar and top line filled"
        # the fill continues the wall/floor: no visible step at the seams (median over rows/columns,
        # so the few rows where the hand touches the seam do not count)
        assert np.median(np.abs(b[4:, 51] - b[4:, 52])) <= 2
        assert np.median(np.abs(b[4:, 1225] - b[4:, 1226])) <= 2
        assert np.median(np.abs(b[3, 52:1226] - b[4, 52:1226])) <= 2
        # no streak where the hand touches the seam (an edge smear would stretch the fingers across)
        assert b[355:390, :48].min() > 150
        # the picture itself is only re-encoded (visually lossless)
        mse = ((a[4:, 52:1226] - b[4:, 52:1226]) ** 2).mean()
        assert 10 * np.log10(255**2 / max(mse, 1e-9)) > 40


@pytest.mark.slow
@pytest.mark.parametrize("name", VEO_CLIPS)
def test_clean_veo_clip_has_no_watermark_and_matches_the_original_elsewhere(tools: tuple[str, str], name: str) -> None:
    ffmpeg, ffprobe = tools
    original, clean = BRANDING / f"{name}.mp4", BRANDING / f"{name}_clean.mp4"
    assert video_info(ffprobe, clean) == video_info(ffprobe, original)  # same size, fps, frames (loop), duration

    def audio_md5(path: Path) -> str:
        return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "md5", "-"],
                              check=True, capture_output=True, text=True, timeout=120).stdout.strip()

    assert audio_md5(clean) == audio_md5(original)  # the audio track is copied, not re-encoded
    box = (slice(690, 708), slice(1238, 1280))  # the replaced corner (scripts/clean_mascot_clip.py WATERMARK)
    for n in (0, 100):
        a, b = luma_frame(ffmpeg, original, n), luma_frame(ffmpeg, clean, n)

        def mark(f: np.ndarray) -> np.ndarray:
            # "Veo" text area minus the mirror of the background just left of it
            return f[695:704, 1243:1264] - f[695:704, 1222:1243][:, ::-1]

        text = mark(a) > 8
        assert text.sum() > 40 and mark(a)[text].mean() > 8, "the original carries the mark"
        assert abs(mark(b)[text].mean()) < 2.5 and np.abs(mark(b)[text]).max() < 12, "mark removed"
        # no seam at the box's left/top/bottom edges (median step no larger than inside the floor/board)
        assert np.median(np.abs(b[690:708, 1237] - b[690:708, 1238])) <= 3
        assert np.median(np.abs(b[707, 1238:] - b[708, 1238:])) <= 5
        outside = np.ones(a.shape, dtype=bool)
        outside[box] = False
        mse = ((a - b) ** 2)[outside].mean()  # the rest of the picture is only re-encoded
        assert 10 * np.log10(255**2 / max(mse, 1e-9)) > 40


@pytest.mark.slow
def test_clean_logo_has_no_sparkle_and_matches_the_original_elsewhere(tools: tuple[str, str]) -> None:
    """logo_animation_clean.mp4: the generator's sparkle (luma x 1136..1183, y 576..623 plus glow) is filled
    from the wall/floor beside it (scripts/clean_mascot_clip.py --watermark --box 1128,568,1192,632)."""
    from aadhi.compose.base import LOGO_VIDEO

    ffmpeg, ffprobe = tools
    original, clean = BRANDING / "logo_animation.mp4", BRANDING / LOGO_VIDEO
    assert video_info(ffprobe, clean) == video_info(ffprobe, original)  # same size, fps, 240 frames, duration

    def audio_md5(path: Path) -> str:
        return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", "-f", "md5", "-"],
                              check=True, capture_output=True, text=True, timeout=120).stdout.strip()

    assert audio_md5(clean) == audio_md5(original)  # the audio track is copied, not re-encoded
    y0, y1, x0, x1 = 568, 632, 1128, 1192  # the replaced box
    box = (slice(y0, y1), slice(x0, x1))
    t = (np.arange(x0, x1) - (x0 - 3.5)) / ((x1 + 2.5) - (x0 - 3.5))
    for n in (0, 120, 239):
        a, b = luma_frame(ffmpeg, original, n), luma_frame(ffmpeg, clean, n)

        def mark(f: np.ndarray) -> np.ndarray:
            # the sparkle's area minus the background interpolated per row between the columns either side
            left, right = f[y0:y1, x0 - 6 : x0].mean(axis=1), f[y0:y1, x1 : x1 + 6].mean(axis=1)
            background = left[:, None] * (1 - t)[None] + right[:, None] * t[None]
            return (f[box] - background)[8:56, 8:56]

        sparkle = mark(a) > 8
        assert sparkle.sum() > 400 and mark(a)[sparkle].mean() > 20, "the original carries the sparkle"
        assert abs(mark(b)[sparkle].mean()) < 2.5 and np.abs(mark(b)[sparkle]).max() < 14, "sparkle removed"
        # no seam at any edge of the box (median step no larger than in the wall/floor around it)
        assert np.median(np.abs(b[y0:y1, x0 - 1] - b[y0:y1, x0])) <= 3
        assert np.median(np.abs(b[y0:y1, x1 - 1] - b[y0:y1, x1])) <= 3
        assert np.median(np.abs(b[y0 - 1, x0:x1] - b[y0, x0:x1])) <= 3
        assert np.median(np.abs(b[y1 - 1, x0:x1] - b[y1, x0:x1])) <= 3
        outside = np.ones(a.shape, dtype=bool)
        outside[box] = False
        mse = ((a - b) ** 2)[outside].mean()  # the rest of the picture is only re-encoded
        assert 10 * np.log10(255**2 / max(mse, 1e-9)) > 40


def _cue_geometry() -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    """CUE_ANCHORS and CUE_SIZE as web/js/player/mascot-state.js defines them (stage px)."""
    source = (ROOT_DIR / "web" / "js" / "player" / "mascot-state.js").read_text(encoding="utf-8")
    anchors = {pos: (int(x), int(y)) for pos, x, y in
               re.findall(r"(left|right|center): Object\.freeze\(\{ x: (\d+), y: (\d+) \}\)", source)}
    size = re.search(r"CUE_SIZE = Object\.freeze\(\{ w: (\d+), h: (\d+) \}\)", source)
    assert set(anchors) == {"left", "right", "center"} and size, "CUE_ANCHORS / CUE_SIZE not found"
    return anchors, (int(size.group(1)), int(size.group(2)))


@pytest.mark.slow
def test_cue_bubble_never_covers_aadhi(tools: tuple[str, str]) -> None:
    """The cue bubble (CUE_SIZE pill at CUE_ANCHORS, plus a margin) stays on the plain wall in EVERY frame of
    the left/right/center clips: it is screenshot into the MP4 exactly as the player draws it."""
    ffmpeg, _ = tools
    anchors, (w, h) = _cue_geometry()
    margin, rows = 8, 128  # stage px around the bubble; clip rows decoded (the bubble sits near the top)
    for position, (cx, cy) in sorted(anchors.items()):
        raw = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(BRANDING / MASCOT_CLIPS[position]), "-vf", f"crop={W}:{rows}:0:0",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True, capture_output=True, timeout=300,
        ).stdout
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, rows, W, 3).astype(np.int16)
        assert frames.shape[0] >= 190, f"{position}: {frames.shape[0]} frames decoded"
        wall = np.median(frames[:, :30].reshape(-1, 3), axis=0)
        bw, bh, radius = w + 2 * margin, h + 2 * margin, h // 2 + margin
        x0, y0 = cx - bw / 2, cy - bh / 2
        points = set()
        for y in range(int(y0), int(y0 + bh) + 1):
            for x in range(int(x0), int(x0 + bw) + 1):
                # inside the rounded rectangle (stage px): distance to the inner rectangle <= radius
                dx = max(x0 + radius - x, 0, x - (x0 + bw - radius))
                dy = max(y0 + radius - y, 0, y - (y0 + bh - radius))
                if x0 <= x <= x0 + bw and y0 <= y <= y0 + bh and dx * dx + dy * dy <= radius * radius:
                    points.add((int((y + 0.5) / 1.5), int((x + 0.5) / 1.5)))
        ys, xs = (np.array(v) for v in zip(*sorted(points)))
        assert ys.max() < rows, f"{position}: bubble below the decoded rows"
        covered = (np.abs(frames[:, ys, xs] - wall).max(axis=2) > 40).any(axis=1)
        hit = np.flatnonzero(covered).tolist()
        assert not hit, f"{position}: the bubble at {(cx, cy)} covers Aadhi in frames {hit}"
