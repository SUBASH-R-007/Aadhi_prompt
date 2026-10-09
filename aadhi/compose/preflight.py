"""Render preflight: what the MP4 would show differently from the editor, before rendering.

The timeline builder never fails on missing assets (see ``aadhi.compose.timeline``): a scene
without narration plays silent on estimated timings, a missing animation / clip becomes a text
board, and a side panel without its media shows only its title in the MP4. This module turns
those degradations into a list the teacher sees before (and after) a render, instead of a
surprise in the video.

``blocking`` items (silent scenes) make ``POST /api/versions/{vid}/render`` answer
``409 render_preflight`` when the request sets ``allow_degraded: false``; the default request
renders anyway (existing API clients keep working). Pure: no I/O.

Quality check before export (``quality_items``): the version's open errors and warnings (lint, the reviewer
and generation/translation failures; at most ``QUALITY_MAX_ITEMS``) as items with ``reason: "quality"``. They are never blocking: a
render never waits for them, the dialog only lists them. ``render_preflight(timeline, issues=...)`` appends
them after the degradations; without ``issues`` the result is exactly the degradations, as before. Issues of
hidden scenes (skipped in the video) are never listed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..schemas.timeline import MediaRef, TimedScene, Timeline
from .timeline import FALLBACKS_META

BLOCKING_REASONS = frozenset({"no_audio", "audio_mismatch"})
MEDIA_PANEL_KINDS = frozenset({"figure", "image", "manim"})

MESSAGES = {
    "no_audio": "No narration audio: the scene would be silent in the video (timed from the text).",
    "audio_mismatch": "The narration no longer matches the script: the scene would be silent in the video. "
                      "Build the lecture again to record it.",
    "simulation_without_media": "The animation is not available: the video shows a text board of its steps instead.",
    "ai_video_without_media": "The video clip is not available: the video shows a text board with its description.",
    "panel_media_missing": "The side panel's picture or animation is not available: the video shows only its title.",
    "panel_not_in_video": "This side panel (an online GIF) is not included in the video: only its title is shown.",
    "media_not_in_video": "This scene's online media is not included in the video.",
}


def _item(scene: TimedScene | None, scene_id: str, reason: str) -> dict[str, Any]:
    return {
        "scene_index": scene.index if scene is not None else None,
        "scene_id": scene_id,
        "title": (scene.title if scene is not None else "") or scene_id,
        "reason": reason,
        "blocking": reason in BLOCKING_REASONS,
        "message": MESSAGES.get(reason, reason.replace("_", " ")),
    }


def _not_in_video(ref: MediaRef | None) -> bool:
    return ref is not None and not ref.render_in_mp4


def render_preflight(timeline: Timeline, issues: Iterable[Any] | None = None,
                     hidden: Iterable[str] = frozenset()) -> list[dict[str, Any]]:
    """Degradations of ``timeline`` in scene order: ``{scene_index, scene_id, title, reason, blocking, message}``.

    Sources: ``Timeline.meta["fallbacks"]`` (silent scenes, boards replacing missing media), side
    panels of a media kind whose media is missing, and hotlinked media that the MP4 leaves out.
    With ``issues`` (the version's stored issues), the quality items follow (``quality_items``; ``hidden``: the
    ids of the scenes skipped in the video, whose issues are left out).
    """
    by_id = {s.scene_id: s for s in timeline.scenes}
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(scene: TimedScene | None, scene_id: str, reason: str) -> None:
        if (scene_id, reason) in seen:
            return
        seen.add((scene_id, reason))
        items.append(_item(scene, scene_id, reason))

    for fb in timeline.meta.get(FALLBACKS_META) or []:
        if not isinstance(fb, dict):
            continue
        sid, reason = str(fb.get("scene_id") or ""), str(fb.get("reason") or "")
        if sid and reason:
            add(by_id.get(sid), sid, reason)
    for scene in timeline.scenes:
        panel = scene.side_panel
        if panel is not None:
            if panel.media is None and panel.panel.kind in MEDIA_PANEL_KINDS:
                add(scene, scene.scene_id, "panel_media_missing")
            elif _not_in_video(panel.media):
                add(scene, scene.scene_id, "panel_not_in_video")
        main = scene.media if scene.media is not None else scene.poster
        if _not_in_video(main):
            add(scene, scene.scene_id, "media_not_in_video")
    order = {s.scene_id: i for i, s in enumerate(timeline.scenes)}
    items.sort(key=lambda it: order.get(it["scene_id"], len(order)))
    if issues is not None:
        items.extend(quality_items(issues, timeline, hidden))
    return items


# --- quality check before export --------------------------------------------------------------

QUALITY_REASON = "quality"
QUALITY_MAX_ITEMS = 12
QUALITY_SEVERITIES = {"error": 0, "warning": 1}  # notes (info) are not listed before a render
# Lint, the reviewer and system issues (a scene that failed to generate or translate, a failed timeline): the
# editor's render dialog lists them too. Missing media (assets, manim) is listed from the timeline above.
QUALITY_SOURCES = frozenset({"lint", "critic", "system"})


def _field(issue: Any, name: str) -> Any:
    return issue.get(name) if isinstance(issue, dict) else getattr(issue, name, None)


def _alternate(first: list[Any], second: list[Any]) -> list[Any]:
    """``first[0], second[0], first[1], second[1], ...``, then the rest of the longer list."""
    out: list[Any] = []
    for n in range(max(len(first), len(second))):
        out += first[n:n + 1] + second[n:n + 1]
    return out


def quality_items(issues: Iterable[Any], timeline: Timeline | None = None,
                  hidden: Iterable[str] = frozenset()) -> list[dict[str, Any]]:
    """Open errors and warnings as non-blocking preflight items: errors first; within a severity, lecture-wide
    issues (no scene) alternate with scene issues (in scene order), so neither kind crowds the other out of the
    first ``QUALITY_MAX_ITEMS``.

    ``issues``: stored issue dicts or ``Issue`` objects. Each item is ``{scene_index, scene_id, title, reason:
    "quality", blocking: False, message, severity, code}``; when more than ``QUALITY_MAX_ITEMS`` are open, a
    last item without a scene says how many more the editor lists.

    ``hidden``: ids of the scenes skipped in the video (``SceneBase.hidden``). Their issues are notes for the teacher,
    never listed before a render. (Not "scenes missing from the timeline": a stale timeline lacks newly added scenes,
    whose errors must still be listed.)
    """
    hidden = frozenset(hidden)
    scenes = {s.scene_id: s for s in timeline.scenes} if timeline is not None else {}
    order = {s.scene_id: i for i, s in enumerate(timeline.scenes)} if timeline is not None else {}
    found = []
    for issue in issues or []:
        severity = _field(issue, "severity")
        if severity not in QUALITY_SEVERITIES or (_field(issue, "source") or "lint") not in QUALITY_SOURCES:
            continue
        if _field(issue, "scene_id") in hidden:
            continue
        message = str(_field(issue, "message") or "").strip()
        if message:
            found.append((issue, severity, message))
    ranked: list[tuple[Any, str, str]] = []
    for severity in sorted(QUALITY_SEVERITIES, key=QUALITY_SEVERITIES.__getitem__):
        same = [f for f in found if f[1] == severity]
        lecture = [f for f in same if not _field(f[0], "scene_id")]
        in_scenes = sorted((f for f in same if _field(f[0], "scene_id")),
                           key=lambda f: order.get(_field(f[0], "scene_id"), len(order)))
        ranked += _alternate(lecture, in_scenes)
    found = ranked
    items: list[dict[str, Any]] = []
    for issue, severity, message in found[:QUALITY_MAX_ITEMS]:
        sid = _field(issue, "scene_id")
        scene = scenes.get(sid) if sid else None
        items.append({
            "scene_index": scene.index if scene is not None else None,
            "scene_id": sid,
            "title": (scene.title if scene is not None else "") or (sid or ""),
            "reason": QUALITY_REASON,
            "blocking": False,
            "message": message[:400],
            "severity": severity,
            "code": str(_field(issue, "code") or ""),
        })
    more = len(found) - QUALITY_MAX_ITEMS
    if more > 0:
        items.append({"scene_index": None, "scene_id": None, "title": "", "reason": QUALITY_REASON, "blocking": False,
                      "message": f"{more} more error(s) or warning(s): see Issues in the editor.",
                      "severity": "warning", "code": ""})
    return items


def has_blocking(items: list[dict[str, Any]]) -> bool:
    """True when any item would make the video silent somewhere."""
    return any(it.get("blocking") for it in items)
