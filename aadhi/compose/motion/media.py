"""Media frames for the stage (DYNAMIC_VIDEO_DESIGN §7.7): what a chunk needs, extracted before it is captured.

The stage never plays ``<video>`` in render mode (seeking one is asynchronous and frame-inexact). For each chunk the
worker asks ``aadhiStage.mediaPlan(f0, f1)`` which source frames are visible (the page applies the frame selection
``floor((t - media_start + trim_in) * srcFps + 1e-6)`` and the loop / freeze / clamp ``end_behavior``), extracts
exactly those frames from each mezzanine with ffmpeg at the largest size they are shown in the chunk (never above
the stage size) and serves them to the page from the worker's own workspace (``StageSession.frames``,
``route.fulfill``). The range asked for runs :data:`LOOKAHEAD_FRAMES` past the chunk, so the page's prefetch of the
next frames is served too. The frames are deleted after the chunk; JPEGs are never written for the captured
frames themselves (they are piped).

A plan entry's ``key`` names the extracted frames (the page asks ``/__frames/<key>/<n>.jpg``; the stage makes it
path-safe and lets it carry the size, e.g. ``v-<hash>-960x540``) and its ``asset_key`` names the source: an asset
key of the timeline (``MediaRef.asset_key``; content addressed) or the file name of a branding clip
(``ResolvedLook.presenter.clip_key``: Aadhi). Without ``asset_key`` the ``key`` is the source. The engine extracts
only sources the timeline names, and neither name is ever used as a path.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PLAN_ENTRIES = 32
MAX_FRAMES_PER_ENTRY = 20_000
LOOKAHEAD_FRAMES = 10
RUN_GAP_FRAMES = 12  # merge runs this close: decoding a few unused frames is cheaper than another seek
_KINDS = frozenset({"video", "aadhi"})
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class MediaPlanError(ValueError):
    """``mediaPlan()`` broke the contract."""


@dataclass(frozen=True)
class MediaRequest:
    """One entry of ``aadhiStage.mediaPlan(f0, f1)``, validated."""

    key: str  # the frames' name in /__frames/<key>/<n>.jpg
    kind: str
    src_fps: float
    frames: tuple[int, ...]
    size: tuple[int, int]
    source: str = ""  # the asset key (or branding clip) the frames come from; "" = ``key``

    @property
    def asset(self) -> str:
        """The source to extract from."""
        return self.source or self.key

    @property
    def slug(self) -> str:
        """A file-system-safe directory name for the key."""
        return hashlib.sha1(self.key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def parse_media_plan(raw: Any, *, stage: tuple[int, int]) -> list[MediaRequest]:
    """Validate the page's media plan; sizes are clamped to the stage, entries of one key are merged."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MediaPlanError("aadhiStage.mediaPlan() did not return a list")
    if len(raw) > MAX_PLAN_ENTRIES:
        raise MediaPlanError(f"aadhiStage.mediaPlan() listed {len(raw)} media (limit {MAX_PLAN_ENTRIES})")
    merged: dict[str, MediaRequest] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise MediaPlanError("a mediaPlan entry is not an object")
        key = entry.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise MediaPlanError(f"a mediaPlan entry has an invalid key {str(key)[:60]!r}")
        source = entry.get("asset_key")
        if source is None:
            source = key
        if not isinstance(source, str) or not _KEY_RE.match(source):
            raise MediaPlanError(f"mediaPlan entry {key[:40]} has an invalid asset_key {str(source)[:60]!r}")
        kind = entry.get("kind")
        if kind not in _KINDS:
            raise MediaPlanError(f"mediaPlan entry {key[:40]}: unknown kind {str(kind)[:20]!r}")
        fps = entry.get("srcFps")
        if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or not 0 < fps <= 240:
            raise MediaPlanError(f"mediaPlan entry {key[:40]}: srcFps must be in (0, 240]")
        frames = entry.get("frames")
        if not isinstance(frames, list) or len(frames) > MAX_FRAMES_PER_ENTRY:
            raise MediaPlanError(f"mediaPlan entry {key[:40]}: frames must be a list of at most "
                                 f"{MAX_FRAMES_PER_ENTRY}")
        indices: list[int] = []
        for f in frames:
            if not isinstance(f, (int, float)) or isinstance(f, bool) or not math.isfinite(f) or f < 0 or f != int(f):
                raise MediaPlanError(f"mediaPlan entry {key[:40]}: frame indices must be whole numbers >= 0")
            indices.append(int(f))
        size = entry.get("size")
        if (not isinstance(size, (list, tuple)) or len(size) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                           for v in size)):
            raise MediaPlanError(f"mediaPlan entry {key[:40]}: size must be [w, h] > 0")
        w = max(2, min(int(math.ceil(size[0])), int(stage[0])))
        h = max(2, min(int(math.ceil(size[1])), int(stage[1])))
        if not indices:
            continue
        prev = merged.get(key)
        if prev is not None:
            if abs(prev.src_fps - float(fps)) > 1e-6 or prev.asset != source:
                raise MediaPlanError(f"mediaPlan lists {key[:40]} twice with another source or frame rate")
            merged[key] = MediaRequest(key, prev.kind, prev.src_fps, tuple(sorted({*prev.frames, *indices})),
                                       (max(prev.size[0], w), max(prev.size[1], h)), prev.source)
        else:
            merged[key] = MediaRequest(key, str(kind), float(fps), tuple(sorted(set(indices))), (w, h),
                                       "" if source == key else source)
    return [merged[k] for k in sorted(merged)]


def file_rate(src_fps: float, real_fps: float | None) -> float:
    """The rate to extract a clip at: the file's own (probed) rate when it differs from the page's ``srcFps``."""
    if real_fps is not None and math.isfinite(real_fps) and real_fps > 0 and abs(real_fps - src_fps) > 0.01:
        return float(real_fps)
    return float(src_fps)


def native_frames(frames: Sequence[int], *, src_fps: float, rate: float) -> dict[int, int]:
    """``page index -> file frame``: the source frame on screen at ``n / src_fps``, i.e. ``floor(n * rate / src_fps)``.

    The page indexes a clip on its ``srcFps`` grid (the timeline's rate when ``MediaRef.src_fps`` is unset); a file
    at another rate (a 24 fps Veo clip) is extracted at its own rate, which is frame-exact. An ``fps`` filter would
    anchor its output grid to wherever an extraction run starts, so the same page index would show different source
    frames depending on the chunk plan (§7.4). Identity when the rates agree.
    """
    if abs(rate - src_fps) <= 1e-9:
        return {int(n): int(n) for n in frames}
    return {int(n): math.floor(int(n) * rate / src_fps + 1e-6) for n in frames}


def frame_files(request: MediaRequest, directory: Path, exists: Callable[[Path], bool] = Path.is_file, *,
                native: Mapping[int, int] | None = None) -> tuple[dict[tuple[str, int], Path], list[int]]:
    """``(key, index) -> file`` for every requested frame after extraction into ``directory``.

    ``native`` maps a requested index to the file frame extracted for it (:func:`native_frames`; indices that share a
    source frame share its file); the registry stays keyed by the page's index. A frame ffmpeg could not produce (the
    page asked past the end of the file, e.g. by rounding) shows the closest earlier extracted frame (a freeze); the
    second value lists those indices. Raises :class:`MediaPlanError` when nothing was extracted at all.
    """
    out: dict[tuple[str, int], Path] = {}
    missing: list[int] = []
    for i in request.frames:
        path = directory / f"f_{(native or {}).get(i, i):06d}.jpg"
        if exists(path):
            out[(request.key, i)] = path
        else:
            missing.append(i)
    if not out:
        raise MediaPlanError(f"no frame of {request.key[:40]} could be extracted")
    if missing:
        absent = set(missing)
        held = out[(request.key, min(i for i in request.frames if i not in absent))]
        for i in request.frames:  # sorted: hold the latest extracted frame
            if i in absent:
                out[(request.key, i)] = held
            else:
                held = out[(request.key, i)]
    return out, missing


def resolve_media_file(key: str, *, local: Mapping[str, Path], branding_dir: Path) -> Path | None:
    """The local file of a plan key: an asset already copied for the render, else a branding clip by file name."""
    if key in local:
        return local[key]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}", key) and ".." not in key:
        path = Path(branding_dir).resolve() / key
        if path.is_file():
            return path
    return None
