"""State PNG hygiene for the compositor.

Render-mode screenshots are fed to ffmpeg as ONE concat stream per layer, so every PNG of a
stream must decode to the same size and pixel format (a mid-stream change would make ffmpeg
re-initialise the whole filtergraph). Chromium writes 8-bit RGBA PNGs at the stage size; these
helpers guarantee it, create the fully transparent "gap" frame and write the cross-fade frames
(two states mixed in premultiplied alpha, so a translucent panel shared by both keeps its opacity).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from .ffmpeg import StateMix


def _is_rgba8_png(im: Image.Image, size: tuple[int, int]) -> bool:
    tile = getattr(im, "tile", None) or []
    rawmode = tile[0][3] if tile and len(tile[0]) > 3 else None
    return im.format == "PNG" and im.mode == "RGBA" and tuple(im.size) == tuple(size) and rawmode == "RGBA"


def ensure_rgba_png(path: Path, size: tuple[int, int]) -> bool:
    """Make ``path`` an 8-bit RGBA PNG of exactly ``size`` (rewritten atomically if needed).

    Returns True when the file had to be rewritten. Only the header is read for compliant files.
    """
    with Image.open(path) as im:
        if _is_rgba8_png(im, size):
            return False
        out = im.convert("RGBA")
    if tuple(out.size) != tuple(size):
        out = out.resize(size, Image.Resampling.LANCZOS)
    tmp = path.with_name(path.name + ".tmp")
    out.save(tmp, format="PNG")
    os.replace(tmp, path)
    return True


def normalize_pngs(paths: Iterable[Path], size: tuple[int, int]) -> int:
    """:func:`ensure_rgba_png` for each distinct path; returns how many were rewritten (blocking)."""
    return sum(ensure_rgba_png(p, size) for p in sorted(set(paths)))


def write_state_mixes(mixes: Iterable[StateMix], work: Path) -> int:
    """Write every cross-fade PNG (``ffmpeg.StateMix``) under ``work``: ``(1 - weight) * old + weight * new``
    in premultiplied alpha, at the old state's size. Existing files are rewritten (their states may have been
    captured again). Each state pair is decoded once. Returns how many were written (blocking)."""
    pairs: dict[tuple[str, str], list[StateMix]] = {}
    for mix in mixes:
        pairs.setdefault((mix.old, mix.new), []).append(mix)
    written = 0
    for (old_path, new_path), group in pairs.items():
        with Image.open(work / old_path) as a, Image.open(work / new_path) as b:
            old = a.convert("RGBA")
            new = b.convert("RGBA")
        if new.size != old.size:
            new = new.resize(old.size, Image.Resampling.LANCZOS)
        old_p, new_p = old.convert("RGBa"), new.convert("RGBa")
        for mix in {m.out: m for m in group}.values():
            out = work / mix.out
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            Image.blend(old_p, new_p, float(mix.weight)).convert("RGBA").save(tmp, format="PNG", compress_level=1)
            os.replace(tmp, out)
            written += 1
    return written


def transparent_png(path: Path, size: tuple[int, int]) -> Path:
    """Write a fully transparent 8-bit RGBA PNG (shown before a late first state, e.g. under the intro logo)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (0, 0, 0, 0)).save(path, format="PNG")
    return path
