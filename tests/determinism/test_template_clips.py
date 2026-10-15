"""Clips in the B2c pilot templates on the real render page (B2 plan §A step 3: media_split "clip, mediaBox,
look_here"; §P (d): a picture is only ever scaled down from at least its final size; DYNAMIC_VIDEO_DESIGN §7.4 test
(a), §7.7). The template fixtures carry ``data:`` pictures only (the fixture gate ``test_template_fixtures.py`` serves
no ``/__frames/``), so this file gates what a clip adds: each frame of a ``contain_blur`` clip (its pre-extracted
JPEGs, a new picture every frame, served through the worker's ``/__frames/`` route as ``test_kenburns_determinism.py``
serves them) drawn by

* media_split, in both formats, with the clip's blurred backdrop shown (its size unknown, so its box keeps the
  template's shape) and with the box fitted to the clip: the medium's entrance and a look_here on the panel. A clip is
  never scaled: it enters by a fade only (media_split ``clipShown``) and a scene that shows one never pushes the camera
  (cues.js ``showsClip``), so the look_here is drawn in its outline form. Measured here (B2c integration): with the
  push, 80 of 120 frames differed by render order in both formats (a page started inside the push drew the clip
  differently from then on); with the 0.96 -> 1 entrance, the first visible frame differed in reverse order (9:16,
  backdrop shown); stills under the same push and entrance did not;
* list_build's 16:9 split layout, whose right 45% shows the side panel's clip (``mediaBox`` slot ``panel``),

gives identical SHA-256 per captured frame (CDP JPEG q92) rendered 1xN in order, as 3 chunks on three pages at once
and in reverse; the page asks only for the one clip key ``mediaPlan`` plans (its ``mediaBox`` size), and for frame n of
the clip on frame n of the scene. The look_here is moved early (1.0 s) so it lies inside the frames gated.

Slow and container-marked like the other real-page determinism tests: hashes are compared within one machine only.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import Any

import pytest

from aadhi.compose.dynamic.determinism import run_plans, standard_plans
from aadhi.compose.dynamic.stage_page import open_stage
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, load_fixture
from tests.determinism.test_kenburns_determinism import CLIP_FRAME, CLIP_SIZE, _clip_frame

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "determinism.template.clip"
FRAMES = 120  # 4 s at 30 fps: the medium's entrance (0.15-0.65 s x the personality's scale) and the look_here
LOOK_AT = 1.0  # the panel look_here's start (s), held to t + dur
LOOK_DUR = 1.6

_CUE_JS = """() => {
  const cam = document.querySelector('.dyn-camera');
  const outline = document.querySelector('.dyn-cue-outline');
  const frame = document.querySelector('.dyn-ms-frame');
  const box = (r) => [r.left, r.top, r.right, r.bottom];
  const bb = outline && outline.getAttribute('d') ? outline.getBBox() : null;
  return { camera: cam ? getComputedStyle(cam).transform : null, clip: !!document.querySelector('.dyn-media--clip img'),
           outline: outline ? [outline.getAttribute('d') || '', getComputedStyle(outline).visibility] : null,
           around: bb ? [bb.x, bb.y, bb.x + bb.width, bb.y + bb.height] : null,
           frame: frame ? box(frame.getBoundingClientRect()) : null };
}"""


def _with_clip(name: str, template: str, sized: bool) -> dict[str, Any]:
    """The fixture with its template's first scene showing a contain_blur clip on its side panel (a figure panel, as
    the pipeline gives a clip), and its panel look_here (if any) moved to LOOK_AT."""
    tl = copy.deepcopy(load_fixture(FIXTURE_DIR / name))
    scene = next(s for s in tl["scenes"] if s["look"]["template"] == template)
    media: dict[str, Any] = {"kind": "video", "asset_key": f"clip-{template}", "url": f"/media/{template}.mp4",
                             "duration": float(scene["duration"]), "src_fps": tl["fps"], "end_behavior": "freeze",
                             "fit": "contain_blur", "render_in_mp4": True}
    if sized:
        media.update(width=CLIP_SIZE[0], height=CLIP_SIZE[1])
    scene["side_panel"] = {"panel": {"kind": "figure", "title": "A river delta", "rationale": "", "figure_id": "fig"},
                           "media": media, "show_at": 0.0}
    for e in scene.get("events") or []:
        if e["kind"] == "look_here" and e["target"] == "panel":
            e.update(t=LOOK_AT, dur=LOOK_DUR)
    return tl


@pytest.mark.parametrize(("name", "template", "sized", "look"), [
    ("media_split.landscape.json", "media_split", False, True),
    ("media_split.landscape.json", "media_split", True, True),
    ("media_split.portrait.json", "media_split", False, True),
    ("media_split.portrait.json", "media_split", True, True),
    ("list_build.landscape.json", "list_build", False, False),
], ids=["media_split-16x9-backdrop", "media_split-16x9-sized", "media_split-9x16-backdrop", "media_split-9x16-sized",
        "list_build-16x9-backdrop"])
async def test_pilot_clip_frames_are_identical_in_any_order(real_app, name: str, template: str, sized: bool,
                                                           look: bool) -> None:
    served = _with_clip(name, template, sized)
    served_frames: dict[str, bytes] = {}
    unknown: set[str] = set()

    def serve(path: str) -> bytes | None:
        m = CLIP_FRAME.match(path)
        if m is None:
            unknown.add(path)
            return None
        if path not in served_frames:
            # the worker's extraction: the clip scaled to cover the planned size, aspect kept (encode.extract_args)
            k = max(int(m[2]) / CLIP_SIZE[0], int(m[3]) / CLIP_SIZE[1])
            served_frames[path] = _clip_frame(int(m[4]), 2 * round(CLIP_SIZE[0] * k / 2),
                                              2 * round(CLIP_SIZE[1] * k / 2))
        return served_frames[path]

    @asynccontextmanager
    async def open_page():
        async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                              base_url=real_app.base, served_timeline=served, frames=serve) as page:
            yield page

    report = await run_plans(open_page, standard_plans(0, FRAMES, 3))
    assert not unknown, f"the page asked for media the test does not serve: {sorted(unknown)[:5]}"
    mismatches = report.mismatches()
    assert mismatches == [], f"{len(mismatches)} of {FRAMES} frames differ by render order, first: {mismatches[:3]}"
    assert report.guard_trips == [] and report.errors == [], (report.guard_trips, report.errors)
    assert report.distinct_frames() > FRAMES // 2, "the clip should change every frame"
    keys = {m[1] for m in map(CLIP_FRAME.match, served_frames) if m}
    assert len(keys) == 1, f"one clip at one size in every chunk (mediaPlan): {keys}"
    shown = {int(m[4]) for m in map(CLIP_FRAME.match, served_frames) if m}
    # frame n of the clip for frame n of the scene (src_fps = fps, shown from 0); the forward order also prefetches the
    # next few (media.js LOOKAHEAD)
    assert set(range(FRAMES)) <= shown and min(shown) == 0, sorted(set(range(FRAMES)) - shown)[:5]
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served, frames=serve) as page:
        await page.seek(round((LOOK_AT + 1.0) * served["fps"]))  # the look_here drawn and held
        got = await page.page.evaluate(_CUE_JS)
        assert page.errors == []
    assert got["clip"], "the clip is on the page"
    assert got["camera"] is None, "a scene that shows a clip has no camera to push (cues.js showsClip)"
    if look:
        assert got["outline"] is not None and got["outline"][0] and got["outline"][1] == "visible", (
            f"the look_here on the clip's panel is drawn as an outline: {got}")
        # round the medium, never across it (B2c review CG-1: the medium is a shape, its outline clamped only to the
        # shapes area; the 9:16 stage spans x 60-1020, past the critical text column): the stroke's centre line
        # FOCUS_OUTSET + FOCUS_PX / 2 = 10 px outside the frame on every side (+-1 for rounding)
        fx0, fy0, fx1, fy1 = got["frame"]
        ox0, oy0, ox1, oy1 = got["around"]
        assert all(abs(d - 10) <= 1 for d in (fx0 - ox0, fy0 - oy0, ox1 - fx1, oy1 - fy1)), got
