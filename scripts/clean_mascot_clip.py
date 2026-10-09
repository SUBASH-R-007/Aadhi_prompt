"""Clean a mascot clip (the baked-in black bars, or with --watermark the "Veo" mark) and write its poster frame.

    python scripts/clean_mascot_clip.py video_template/aadhi_left.mp4 video_template/aadhi_left_clean.mp4
    python scripts/clean_mascot_clip.py SRC DST --crf 16 --ffmpeg /usr/bin/ffmpeg --poster video_template/posters/aadhi_left_clean.jpg
    python scripts/clean_mascot_clip.py --poster-only video_template/aadhi_center.mp4 video_template/posters/aadhi_center.jpg

How ``video_template/aadhi_left_clean.mp4`` was made from ``aadhi_left.mp4`` (1280x720, 24 fps, 192 frames,
yuv420p, static camera; cropdetect reported crop=1184:720:48:0): black pillars at luma columns 0..48 and
1230..1279 with edge ringing in 49..51 and 1226..1229, and a dark line plus an overshoot row in rows 0..3.
The studio wall and floor behind them are static, so the bars are filled by mirroring the adjacent clean
picture across the seam (keeps the floor grain, continuous at the seam). A plain edge smear (ffmpeg
``fillborders=smear``) was rejected: Aadhi's fingertips touch column 52 in frames 143-162 and the smear
stretches them into a brown streak once per loop.

* top rows 0..3     <- mirror of rows 4..7 of the same frame (the horns start at row ~25);
* right 1226..1279  <- mirror of columns 1172..1225 of the same frame (never occluded);
* left 0..51        <- mirror of columns 52..103 of a CLEAN PLATE (per-pixel temporal median of all
                       frames): the hand enters that band in some frames, the median never contains it.

All three planes are processed at their own resolution (no 4:2:0 -> 4:4:4 round trip), frame count, fps,
duration and the loop seam are preserved, and the audio track is copied unchanged. The source is never
modified: keep the original clip and point ``aadhi.compose.base.MASCOT_CLIPS`` at the new file.

``--watermark`` removes the faint "Veo" mark instead (``aadhi_center``, ``aadhi_right``, ``aadhi_popup`` and
``no_aadhi`` -> ``<name>_clean.mp4``; same 1280x720 / 24 fps / 192 frames): semi-transparent white text at
luma x 1243..1263, y 695..703, on the static floor (the whiteboard tray in the popup clip). The box
x 1238..1279, y 690..707 (to the frame edge, so there is no right seam) becomes the mirror, across its left
edge and on the same rows, of a CLEAN PLATE (per-pixel temporal median of all frames) of the 42 columns left
of it, plus each frame's mean offset from the plate measured around the box (keeps the codec's slight
brightness pumping), blended into the original over 3 luma rows/columns at the inner edges. Horizontal
structures (the floor's streaks, the tray's bands) therefore continue through the box. Aadhi never enters
that corner: the script refuses a clip in which any pixel of the work box (target + source + ring) moves more
than ``STATIC_TOLERANCE`` from the plate in any frame (the four clips peak at 8..11, codec noise).

    python scripts/clean_mascot_clip.py --watermark video_template/aadhi_center.mp4 video_template/aadhi_center_clean.mp4 --poster video_template/posters/aadhi_center_clean.jpg

``--box X0,Y0,X1,Y1`` (even luma coordinates, default the "Veo" corner above) cleans another static mark the same
way. A box that stops short of the right frame edge also gets a ring and a feather on its right side. The intro
logo (``logo_animation.mp4``: 1280x720, 24 fps, 240 frames) carries a generator's four-pointed sparkle at luma
x 1136..1183, y 576..623 plus a soft glow, over the static wall/floor seam at y ~602; the box 1128..1191 x
568..631 is mirrored from columns 1064..1127 on the same rows, so the seam carries through (work-box deviation
peaks at ~11). The intro has no poster:

    python scripts/clean_mascot_clip.py --watermark --box 1128,568,1192,632 video_template/logo_animation.mp4 video_template/logo_animation_clean.mp4

Posters (``video_template/posters/<clip>.jpg``) are frame 0 of the clip at JPEG quality 3; the live player
shows them while a clip loads or stalls (web/js/player/mascot.js posterUrl). Every new clip needs one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

W, H, FPS = 1280, 720, 24
LEFT = 52  # luma columns 0..51 replaced
RIGHT_X = 1226  # luma columns 1226..1279 replaced
TOP = 4  # luma rows 0..3 replaced
WATERMARK = (1238, 690, 1280, 708)  # luma box x0, y0, x1 (frame edge), y1 replaced by --watermark (4:2:0 aligned)
FEATHER = 3  # luma rows/columns at the box's inner edges blended into the original picture
STATIC_TOLERANCE = 16  # max |frame - plate| in the work box; more means something moves in that corner


def read_frames(ffmpeg: str, src: Path) -> np.ndarray:
    """Every frame of ``src`` as raw yuv420p rows (frames x bytes)."""
    raw = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True, capture_output=True,
    ).stdout
    size = W * H * 3 // 2
    n = len(raw) // size
    if not n:
        raise SystemExit(f"{src}: no {W}x{H} frames decoded")
    return np.frombuffer(raw[: n * size], dtype=np.uint8).reshape(n, size).copy()


def planes(frames: np.ndarray) -> list[tuple[np.ndarray, int]]:
    """(plane view, chroma shift) for Y, U and V; the views write through to ``frames``."""
    n = frames.shape[0]
    y = frames[:, : W * H].reshape(n, H, W)
    u = frames[:, W * H : W * H * 5 // 4].reshape(n, H // 2, W // 2)
    v = frames[:, W * H * 5 // 4 :].reshape(n, H // 2, W // 2)
    return [(y, 0), (u, 1), (v, 1)]


def fill_plane(p: np.ndarray, shift: int) -> None:
    """In-place fill of one plane (``shift`` = chroma subsampling shift, 4:2:0 both ways)."""
    left, right_x, top = LEFT >> shift, RIGHT_X >> shift, TOP >> shift
    width = p.shape[2]
    p[:, :top, left:right_x] = p[:, 2 * top - 1 : top - 1 : -1, left:right_x]  # top: rows just below
    r = width - right_x
    p[:, :, right_x:] = p[:, :, right_x - 1 : right_x - 1 - r : -1]  # right: band left of the seam
    plate = np.median(p[:, :, left : 2 * left], axis=0)  # left: from the temporal-median clean plate
    p[:, :, :left] = np.rint(plate[:, ::-1]).astype(np.uint8)[None, :, :]


def unwatermark_plane(p: np.ndarray, shift: int, box_luma: tuple[int, int, int, int] = WATERMARK) -> float:
    """In-place removal of the watermark box (luma coordinates) of one plane; returns the work box's largest
    frame-to-plate deviation (raises SystemExit above STATIC_TOLERANCE: something moves in that corner)."""
    x0, y0, x1, y1 = (v >> shift for v in box_luma)
    w, h = x1 - x0, y1 - y0
    ring, feather = max(2, 4 >> shift), max(1, FEATHER >> shift)
    right = ring if x1 < p.shape[2] else 0  # a box short of the frame edge also has a ring on its right
    box = p[:, y0 - ring : y1 + ring, x0 - w - ring : x1 + right].astype(np.float32)  # ring + source + target
    plate = np.median(box, axis=0)
    deviation = float(np.abs(box - plate[None]).max())
    if deviation > STATIC_TOLERANCE:
        raise SystemExit(f"the watermark corner is not static (max deviation {deviation:.0f}); not cleaned")
    target = (slice(ring, ring + h), slice(ring + w, ring + 2 * w))
    outside = np.ones(plate.shape, dtype=bool)
    outside[target] = False
    offset = (box - plate[None])[:, outside].mean(axis=1)  # per-frame brightness offset around the box
    mirror = plate[ring : ring + h, ring : ring + w][:, ::-1]
    if right:
        # The mirror reverses a horizontal gradient (the wall's vignette): ramp each row towards the picture
        # right of the box, so its right edge meets it (median over 5 rows keeps the floor grain out of it).
        step = plate[ring : ring + h, ring + 2 * w :].mean(axis=1) - mirror[:, -right:].mean(axis=1)
        padded = np.pad(step, 2, mode="edge")
        step = np.median(np.stack([padded[k : k + h] for k in range(5)]), axis=0)
        mirror = mirror + step[:, None] * (np.arange(1, w + 1, dtype=np.float32) / w)[None, :]
    fill = mirror[None] + offset[:, None, None]
    alpha = np.ones((h, w), dtype=np.float32)
    for i in range(feather):
        a = (i + 1) / (feather + 1)
        alpha[i, :] *= a
        alpha[h - 1 - i, :] *= a
        alpha[:, i] *= a
        if right:
            alpha[:, w - 1 - i] *= a
    out = alpha * fill + (1 - alpha) * box[(slice(None), *target)]
    p[:, y0:y1, x0:x1] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    return deviation


def clean(ffmpeg: str, src: Path, dst: Path, crf: int, *, watermark: bool = False,
          box: tuple[int, int, int, int] = WATERMARK) -> int:
    frames = read_frames(ffmpeg, src)
    for plane, shift in planes(frames):
        if watermark:
            unwatermark_plane(plane, shift, box)
        else:
            fill_plane(plane, shift)
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(src), "-map", "0:v:0", "-map", "1:a:0?",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", "-map_metadata", "-1", str(dst)],
        input=frames.tobytes(), check=True,
    )
    return frames.shape[0]


def poster(ffmpeg: str, clip: Path, out: Path) -> None:
    """Frame 0 of ``clip`` as a quality-3 JPEG."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip), "-frames:v", "1", "-q:v", "3", str(out)],
        check=True,
    )


def parse_box(text: str) -> tuple[int, int, int, int]:
    """``X0,Y0,X1,Y1`` -> an even (4:2:0 aligned) luma box inside the frame with room for its mirror source."""
    try:
        x0, y0, x1, y1 = (int(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("expected X0,Y0,X1,Y1") from None
    if any(v % 2 for v in (x0, y0, x1, y1)):
        raise argparse.ArgumentTypeError("box coordinates must be even (4:2:0 chroma)")
    if not (0 < x1 - x0 <= x0 - 4 and 4 <= y0 < y1 <= H - 4 and x1 <= W):
        raise argparse.ArgumentTypeError("box must fit in the frame with its mirror source and ring")
    return x0, y0, x1, y1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path, help="source clip (never modified)")
    parser.add_argument("dst", type=Path, help="cleaned clip to write (or the poster with --poster-only)")
    parser.add_argument("--crf", type=int, default=16, help="libx264 CRF of the re-encode (default 16)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary (default: ffmpeg on PATH)")
    parser.add_argument("--poster", type=Path, help="also write the cleaned clip's poster frame here")
    parser.add_argument("--poster-only", action="store_true", help="only write the poster of SRC to DST")
    parser.add_argument("--watermark", action="store_true", help='remove the "Veo" mark instead of the black bars')
    parser.add_argument("--box", type=parse_box, default=WATERMARK,
                        help="with --watermark: the luma box X0,Y0,X1,Y1 to clean (even; default the Veo corner)")
    args = parser.parse_args(argv)
    if args.src.resolve() == args.dst.resolve():
        parser.error("DST must differ from SRC: keep the original clip")
    if args.poster_only:
        poster(args.ffmpeg, args.src, args.dst)
        print(f"poster -> {args.dst}")
        return 0
    n = clean(args.ffmpeg, args.src, args.dst, args.crf, watermark=args.watermark, box=args.box)
    print(f"frames={n} -> {args.dst}")
    if args.poster:
        poster(args.ffmpeg, args.dst, args.poster)
        print(f"poster -> {args.poster}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
