"""Frames, chunks and chunk cache keys of a motion render (DYNAMIC_VIDEO_DESIGN §7.6). Pure, except
:func:`bundle_digest`, which stats the stage's files.

* **Frames.** A scene covers ``[round(start * fps), round(end * fps))`` of the absolute timeline: one rounding per
  shared boundary, so per-scene rounding never accumulates drift (the classic ``video.segment_frames``, without an
  intro: a Dynamic timeline has none).
* **Chunks.** Scene-aligned runs of 240-600 frames (8-20 s at 30 fps): consecutive scenes are grouped until a chunk
  reaches 240 frames without passing 600; a scene longer than 600 frames is split mid-scene into equal parts (with
  a short run of scenes before it, which would otherwise be a chunk below 240 frames); a short tail joins the
  chunk before it when it fits. Splitting anywhere is safe because every frame is a pure function of the frame
  number (§7.4). Workers take chunks longest first.
* **Cache key** (frozen in B1). sha256 of: the stage bundle (every file the render page can load: ``web/js``,
  ``web/css``, ``web/vendor`` with its fonts and manifest, ``web/dynamic``, ``render-dynamic.html``), the
  engine version (``TimelinePresentation.engine_version``) and :data:`CHUNK_CACHE_VERSION`, the absolute frame
  range, the timeline slices that can affect those frames (the scenes the chunk shows, the outgoing scene before
  them, whose final state a transition paints, the title of the chapter each of those scenes is in, which
  ``board_generic`` draws as the kicker, the global presentation and stage fields, and the captions shown in the
  range), the media files they name, the encoder parameters, the capture settings and the Chromium version. Asset
  keys are content addresses, so a scene slice that names the same keys names the same pixels.
  :data:`STAGE_TIMELINE_FIELDS` lists every top-level timeline field the stage reads; each one is in the key.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...schemas.timeline import Timeline

CHUNK_MIN_FRAMES = 240
CHUNK_MAX_FRAMES = 600
# 2: chapter titles (the kicker) are hashed; clips at another rate than the page's grid are extracted natively
CHUNK_CACHE_VERSION = 2
# Files under web_dir the stage page can load; everything else (Studio tests, examples, the p5 sandbox) is ignored.
BUNDLE_PATHS = ("render-dynamic.html", "js", "css", "vendor", "dynamic")
_BUNDLE_SKIP_DIRS = frozenset({"node_modules", "tests", "_e2e", "__pycache__"})
_GLOBAL_FIELDS = ("width", "height", "fps", "language", "board_language", "meta", "transition_seconds",
                  "total_duration")
# Top-level timeline fields the stage (web/js/dynamic) reads, and how the key covers each: the global fields as they
# are, the rest sliced per chunk (tests/compose/motion/test_chunks.py checks the stage's sources against this).
STAGE_TIMELINE_FIELDS = (*_GLOBAL_FIELDS, "presentation", "scenes", "captions", "chapters")


@dataclass(frozen=True)
class Chunk:
    """Frames ``[f0, f1)`` of the video (``index`` = position in frame order) and the scenes they show."""

    index: int
    f0: int
    f1: int
    scenes: tuple[int, ...]

    @property
    def frames(self) -> int:
        """Number of frames."""
        return self.f1 - self.f0

    @property
    def name(self) -> str:
        """File stem of the encoded chunk (``chunk_0000``)."""
        return f"chunk_{self.index:04d}"


def scene_frame_bounds(timeline: Timeline, fps: int | None = None) -> list[tuple[int, int]]:
    """Frame range ``[f0, f1)`` of every scene (contiguous, from absolute times; at least one frame each)."""
    rate = int(fps or timeline.fps)
    edges = [s.start for s in timeline.scenes] + [timeline.total_duration]
    if timeline.intro is not None and timeline.intro.duration > 0:  # never for a Dynamic timeline (validator)
        edges.insert(0, 0.0)
    frames = [math.floor(e * rate + 0.5) for e in edges]
    out: list[tuple[int, int]] = []
    for a, b in zip(frames, frames[1:], strict=False):
        start = out[-1][1] if out else a
        out.append((start, max(start + 1, b)))
    if timeline.intro is not None and timeline.intro.duration > 0:
        out = out[1:]
    return out


def total_frames(bounds: Sequence[tuple[int, int]]) -> int:
    """Frames of the whole video (the end of the last scene)."""
    return bounds[-1][1] if bounds else 0


def plan_chunks(bounds: Sequence[tuple[int, int]], *, min_frames: int = CHUNK_MIN_FRAMES,
                max_frames: int = CHUNK_MAX_FRAMES) -> list[Chunk]:
    """Scene-aligned chunks covering every frame exactly once (see the module docstring)."""
    if min_frames < 1 or max_frames < min_frames:
        raise ValueError("chunk sizes need 1 <= min_frames <= max_frames")
    pieces: list[tuple[int, int]] = []
    start: int | None = None  # pending run of whole scenes [start, end)
    end = 0
    for a, b in bounds:
        n = b - a
        pending = end - start if start is not None else 0
        if pending + n <= max_frames:
            start = a if start is None else start
            end = b
            if end - start >= min_frames:
                pieces.append((start, end))
                start = None
            continue
        if n <= max_frames:  # the scene fits on its own: close the pending run (it is non-empty here)
            assert start is not None
            pieces.append((start, end))
            start, end = a, b
            if n >= min_frames:
                pieces.append((a, b))
                start = None
            continue
        # a long scene: split it (with a short pending run before it) into equal parts of at most max_frames
        first = a
        if start is not None and pending < min_frames:
            first = start
        elif start is not None:
            pieces.append((start, end))
        start = None
        span = b - first
        parts = math.ceil(span / max_frames)
        edges = [first + span * j // parts for j in range(parts + 1)]
        pieces.extend(zip(edges, edges[1:], strict=False))
    if start is not None:
        tail = (start, end)
        if (pieces and end - start < min_frames and pieces[-1][1] == start
                and end - pieces[-1][0] <= max_frames):
            pieces[-1] = (pieces[-1][0], end)
        else:
            pieces.append(tail)
    chunks: list[Chunk] = []
    for i, (f0, f1) in enumerate(pieces):
        scenes = tuple(k for k, (a, b) in enumerate(bounds) if a < f1 and b > f0)
        chunks.append(Chunk(i, f0, f1, scenes))
    return chunks


def assignment_order(chunks: Iterable[Chunk]) -> list[Chunk]:
    """Chunks longest first (ties in frame order): the pool finishes with the short ones."""
    return sorted(chunks, key=lambda c: (-c.frames, c.index))


# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------


def bundle_digest(web_dir: Path) -> str:
    """Identity of every file the stage page can load (:data:`BUNDLE_PATHS` under ``web_dir``): relative path,
    size and modification time (a content change always moves the mtime). Blocking; a few hundred stats."""
    root = Path(web_dir)
    h = hashlib.sha256(b"aadhi-stage-bundle|")
    entries: list[tuple[str, int, int]] = []
    for name in BUNDLE_PATHS:
        top = root / name
        if top.is_file():
            st = top.stat()
            entries.append((name, st.st_size, st.st_mtime_ns))
            continue
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d not in _BUNDLE_SKIP_DIRS)
            for fname in sorted(filenames):
                path = Path(dirpath) / fname
                try:
                    st = path.stat()
                except OSError:
                    continue
                entries.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    for rel, size, mtime in sorted(entries):
        h.update(f"{rel}|{size}|{mtime}\n".encode())
    return h.hexdigest()


def _kicker_chapters(data: Mapping[str, Any], indices: Iterable[int]) -> list[str]:
    """The title of the chapter each scene is in, as ``board_generic.kickerOf`` finds it (the last chapter starting
    at or before the scene)."""
    chapters = data.get("chapters") or []
    scenes = data["scenes"]
    out: list[str] = []
    for i in sorted(indices):
        if i >= len(scenes):
            continue
        start = float(scenes[i]["start"])
        title = ""
        for c in chapters:
            if float(c["start"]) <= start + 1e-3:
                title = str(c.get("title") or "")
        out.append(title)
    return out


def chunk_slices(timeline: Timeline, chunk: Chunk, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The timeline data that can change the pixels of ``chunk`` (see the module docstring). ``data`` is the
    timeline's ``model_dump(mode="json")`` when the caller already has it (one dump for every chunk)."""
    indices = set(chunk.scenes)
    if chunk.scenes and chunk.scenes[0] > 0:
        indices.add(chunk.scenes[0] - 1)  # the outgoing scene of a transition into the first one
    fps = timeline.fps
    t0, t1 = chunk.f0 / fps, chunk.f1 / fps
    data = data if data is not None else timeline.model_dump(mode="json")
    return {
        "scenes": [data["scenes"][i] for i in sorted(indices) if i < len(data["scenes"])],
        "chapters": _kicker_chapters(data, indices),
        "global": {k: data.get(k) for k in _GLOBAL_FIELDS} | {"presentation": data.get("presentation")},
        "captions": [c for c in data.get("captions", []) if c["start"] < t1 and c["end"] > t0],
    }


def media_keys(slices: Mapping[str, Any]) -> list[str]:
    """Every asset key (``asset_key``, ``clip_key``) named in the slices, sorted."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("asset_key", "clip_key") and isinstance(v, str) and v:
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(slices.get("scenes"))
    return sorted(found)


def chunk_key(timeline: Timeline, chunk: Chunk, *, bundle: str, encoder: Mapping[str, Any],
              capture: Mapping[str, Any], chromium: str, media: Mapping[str, str] | None = None,
              data: Mapping[str, Any] | None = None) -> str:
    """Content address of an encoded chunk (64 hex). ``media`` maps the media keys the slices name to the identity
    of their files when they are not content addresses (branding files: name, size, mtime); ``data`` as for
    :func:`chunk_slices`."""
    slices = chunk_slices(timeline, chunk, data)
    engine = timeline.presentation.engine_version if timeline.presentation is not None else 0
    doc = {
        "v": CHUNK_CACHE_VERSION,
        "engine": engine,
        "frames": [chunk.f0, chunk.f1],
        "slices": slices,
        "media": {k: (media or {}).get(k, k) for k in media_keys(slices)},
        "bundle": bundle,
        "encoder": dict(encoder),
        "capture": dict(capture),
        "chromium": chromium,
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str, ensure_ascii=True).encode()).hexdigest()
