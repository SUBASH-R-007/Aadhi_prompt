"""The motion render engine: Dynamic videos captured frame by frame from the stage (DYNAMIC_VIDEO_DESIGN §7).

Modules (import them directly; this package import stays cheap):

* ``engine``     ``render_motion(ctx, timeline, out_path, settings, *, burn_captions=False, ...)`` -> ``MotionResult``;
                 ``is_dynamic``; the container remedies for ``aadhi.compose.dynamic.container``
* ``stage``      ``StageSession``: one capture worker (its own headless Chromium, one page, page recycling) over
                 ``aadhi.compose.dynamic.stage_page``, and the ``/__frames/...`` media frames it serves
* ``chunks``     scene frame ranges, the 240-600-frame chunk planner, the stage bundle digest, chunk cache keys
* ``cache``      ``ChunkCache``: encoded chunks in the render workspace (a retry reuses them)
* ``encode``     the chunk encoder (image2pipe -> x264 -> MPEG-TS), media frame extraction, the audio and the final mux
* ``media``      ``mediaPlan()`` validation and the extracted frame registry entries
* ``resources``  the cgroup-aware worker count and the host-wide CPU slots (``RENDER_SLOT_DIR`` lock files)
* ``job``        the ``render_video`` job's Dynamic branch (preflight, render, QA, store)

The classic Aadhi presenter renders (``aadhi.compose.video``) never use this package.
"""

from __future__ import annotations

from typing import Any

_ENGINE_NAMES = frozenset({"render_motion", "is_dynamic", "MotionConfig", "MotionError", "MotionResult"})


def __getattr__(name: str) -> Any:
    """``from aadhi.compose.motion import render_motion`` (the engine module is imported on first use)."""
    if name in _ENGINE_NAMES:
        from . import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
