"""Compose subsystem contract.

    timeline.build_timeline(screenplay, manifest, *, settings, version_id, revision,
                            include_intro=True, branding=None) -> Timeline     (pure, stores asset keys)
    timeline.preview_timeline(screenplay, manifest, *, settings, ...) -> Timeline
                            (editor preview: uses manifest clips where beat hashes still match,
                             estimated durations otherwise; Timeline.estimated=True)
    timeline.resolve_urls(timeline, store) -> Timeline   (serve time: fill every url from asset keys)
    timeline.default_branding(settings) -> Branding
    captions.to_srt(cues) / captions.to_vtt(cues) -> str
    chapters.youtube_chapters(timeline) -> str
    video.render_video  (job handler "render_video")         (timeline -> MP4 + SRT/VTT + chapters)
    base.stage_size(format) -> (w, h)                         (logical stage per format; None = landscape)
    base.output_size(timeline, settings) -> (w, h)            (MP4 size; classic = RENDER_WIDTH x RENDER_HEIGHT)

(Narration audio concatenation lives in ``aadhi.pipeline.audio`` because it is part of the
asset stage; compose only consumes finished per-scene audio files.)

MP4 layer order (per scene, back to front):
  1. mascot clip for ``layout.mascot_position`` (or static background), audio stripped, looped;
  2. media (Manim / AI video / Ken-Burns still / side-panel video) scaled into the rect returned by
     render mode with ``MediaRef.fit``;
  3. the transparent render-mode PNG for the current visual state (it has transparent "holes" where
     media is shown: render mode hides media elements with ``visibility:hidden``).
Hotlinked GIFs (``render_in_mp4=False``) are skipped in MP4s; 3D panels are a fixed angle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

ResolveUrl = Callable[[str], str]

# Mascot clip per layout position (files in Settings.branding_dir, served at /branding/).
# popup_bottom_left / popup_bottom_right use the same close-up clip (mirroring would mirror the
# whiteboard text), so they render identically.
# "left" points at aadhi_left_clean.mp4: aadhi_left.mp4 with its baked-in black pillars and dark top
# line filled in (same 1280x720 / 24 fps / 192 frames, picture otherwise unchanged). The original
# file stays for timelines stored before the switch; the new name keeps week-long branding caches
# from serving the old picture. The live player derives each clip's poster as
# posters/<clip name>.jpg (web/js/player/mascot.js).
# right/center/popup/hidden point at <name>_clean.mp4: the same clips with the faint "Veo" watermark in
# the bottom-right corner filled from a clean plate of the static floor/whiteboard beside it (same
# 1280x720 / 24 fps / 192 frames / audio; scripts/clean_mascot_clip.py --watermark).
MASCOT_CLIPS: dict[str, str] = {
    "left": "aadhi_left_clean.mp4",
    "right": "aadhi_right_clean.mp4",
    "center": "aadhi_center_clean.mp4",
    "popup_bottom_left": "aadhi_popup_clean.mp4",
    "popup_bottom_right": "aadhi_popup_clean.mp4",
    "hidden": "no_aadhi_clean.mp4",
}
# Clip files replaced by a cleaned re-encode -> their replacement. Timelines stored (and lectures
# published) before the switch still name the old file; ``timeline.resolve_urls`` serves the current
# one, so they lose the black bars / watermark without a rebuild. The old files stay in the branding dir.
RETIRED_MASCOT_CLIPS: dict[str, str] = {
    "aadhi_left.mp4": "aadhi_left_clean.mp4",
    "aadhi_right.mp4": "aadhi_right_clean.mp4",
    "aadhi_center.mp4": "aadhi_center_clean.mp4",
    "aadhi_popup.mp4": "aadhi_popup_clean.mp4",
    "no_aadhi.mp4": "no_aadhi_clean.mp4",
}
STATIC_BACKGROUND = "static_background.png"
# The intro logo with the generator's sparkle mark (bottom right, every frame) filled from the wall/floor
# beside it (same 1280x720 / 24 fps / 240 frames / audio; scripts/clean_mascot_clip.py --watermark --box).
LOGO_VIDEO = "logo_animation_clean.mp4"
# Other branding files replaced by a cleaned re-encode -> their replacement (served by ``timeline.resolve_urls``
# like RETIRED_MASCOT_CLIPS, which holds mascot clips only). The old files stay in the branding dir.
RETIRED_BRANDING_FILES: dict[str, str] = {
    "logo_animation.mp4": "logo_animation_clean.mp4",
}
BGM = "bgm.mp3"

INTRO_LOGO_SECONDS = 5.5
INTRO_CARD_SECONDS = 3.0
SCENE_TRANSITION_SECONDS = 0.4  # fade-in at the start of every scene
SCENE_LEAD_SECONDS = 0.5  # narration starts this long after the scene starts (audio_offset)
SCENE_TAIL_SECONDS = 0.8  # breathing room after the last beat of a scene
SILENT_SCENE_SECONDS = 4.0  # chapter cards etc. without narration
QUIZ_REVEAL_HOLD_SECONDS = 1.0  # extra hold after the reveal before the next scene

# Stage geometry: the player renders a fixed 1920x1080 logical stage scaled with a CSS transform.
STAGE_WIDTH = 1920
STAGE_HEIGHT = 1080

# Logical stage per video format (DYNAMIC_VIDEO_DESIGN §2.1): the canvas the page lays out at DPR 1. The classic
# Aadhi presenter is always landscape; a Dynamic reel is portrait. The constants above stay the landscape stage.
STAGE_SIZES: dict[str, tuple[int, int]] = {
    "landscape": (STAGE_WIDTH, STAGE_HEIGHT),
    "portrait": (STAGE_HEIGHT, STAGE_WIDTH),
}


class _Sized(Protocol):
    width: int
    height: int


class _RenderSize(Protocol):
    render_width: int
    render_height: int


def stage_size(format: str | None = None) -> tuple[int, int]:
    """Logical stage ``(width, height)`` of a video format (``"landscape"`` / ``"portrait"``); None = landscape
    (the classic presenter). Raises ValueError for an unknown format."""
    try:
        return STAGE_SIZES[format or "landscape"]
    except KeyError:
        raise ValueError(f"unknown video format {format!r}") from None


def timeline_stage(tl: _Sized) -> tuple[int, int]:
    """The stage a timeline was laid out on (``Timeline.width`` x ``Timeline.height``): the render page's
    viewport and the size of its screenshots. 1920x1080 for every classic timeline."""
    return int(tl.width), int(tl.height)


def _even(value: float) -> int:
    return max(2, 2 * round(value / 2))


def output_size(tl: _Sized, settings: _RenderSize) -> tuple[int, int]:
    """Encoded MP4 size of a timeline.

    The classic landscape stage keeps ``RENDER_WIDTH`` x ``RENDER_HEIGHT`` exactly, as before. Any other stage
    keeps the configured quality: it is scaled by the configured short side over the landscape stage's short side
    (1080), rounded to even pixels for 4:2:0 video, so 1920x1080 settings give a 1080x1920 reel and 1280x720
    settings a 720x1280 one.
    """
    stage = timeline_stage(tl)
    if stage == (STAGE_WIDTH, STAGE_HEIGHT):
        return int(settings.render_width), int(settings.render_height)
    scale = min(int(settings.render_width), int(settings.render_height)) / min(STAGE_WIDTH, STAGE_HEIGHT)
    return _even(stage[0] * scale), _even(stage[1] * scale)
