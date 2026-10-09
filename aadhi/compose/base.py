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
