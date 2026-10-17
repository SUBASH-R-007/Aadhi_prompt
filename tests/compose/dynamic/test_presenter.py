"""``aadhi/compose/dynamic/presenter.py`` (DYNAMIC_VIDEO_DESIGN §3.2-§3.4, §4.3 aadhi_intro / aadhi_recap; B2 plan §1.2,
§1.12, §2 "Aadhi"; owner decision 2 of 2026-10-16): the clips measured from the real files, the placements and their
JS mirror (``web/js/dynamic/presenter.js``), the studio wall re-measured over all 192 frames, the timing rule, the
seeded layout and card choice, the synthesised appearances and their place in a timeline, the resolver's finishing
step, the slot tables against both templates' ``slotRows``, the text budgets against the HarfBuzz text fit, and the
frame cache on the real clip (extraction, reuse, the race, the sweep).

The clips are the real ones in ``video_template/`` (``Settings.branding_dir``); the ffmpeg tests are skipped where
ffmpeg is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import presenter as pr
from aadhi.compose.timeline import build_chapters, build_timeline
from aadhi.config import ROOT_DIR, Settings
from aadhi.schemas.screenplay import BoardScene, Presentation, Screenplay
from aadhi.schemas.timeline import Timeline
from tests.compose.factories import beat, make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

BRANDING = ROOT_DIR / "video_template"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg / ffprobe not installed")
JS = ROOT_DIR / "web" / "js" / "dynamic"


def presentation(**kw: Any) -> Presentation:
    data = {**PRESENTATION, "format": "landscape", "intro_layout": None, "reel_card_x": None, "hook": None,
            "theme_family": "midnight_indigo", "personality": "soft",
            "asset_style": {"family": "midnight_indigo", "personality": "soft"}, "aadhi_recap": True}
    data.update(kw)
    return Presentation.model_validate(data)


def screenplay(fmt: str = "landscape", *, recap: bool = True, **meta: Any) -> Screenplay:
    data = screenplay_dict()
    data.update(meta)
    data["learning_objectives"] = []
    data["presentation"] = presentation(format=fmt).model_dump(mode="json")
    scenes: list[dict[str, Any]] = [
        {"id": "s-facts", "type": "content", "title": "Facts", "concept_id": "ohm",
         "board": [{"id": "b1", "kind": "bullet", "text": "Current flows"},
                   {"id": "b2", "kind": "bullet", "text": "Voltage pushes"}],
         "beats": [beat("s-facts-b1", "Current flows.", board_item_id="b1"),
                   beat("s-facts-b2", "Voltage pushes.", board_item_id="b2")]},
    ]
    if recap:
        scenes.append({"id": "s-recap", "type": "recap", "title": "Recap", "concept_id": "ohm",
                       "board": [{"id": "q1", "kind": "question", "text": "What flows?", "blank": True},
                                 {"id": "t1", "kind": "takeaway", "text": "Current"}],
                       "beats": [beat("s-recap-b1", "What flows? Think.", board_item_id="q1"),
                                 beat("s-recap-b2", "Current.", board_item_id="t1")]})
    data["scenes"] = scenes
    return Screenplay.model_validate(data)


def node_json(script: str) -> Any:
    """Run an ES-module snippet with node (cwd = the repository) and parse what it prints as JSON."""
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT_DIR, capture_output=True, text=True,
                          encoding="utf-8", timeout=120, check=False)
    if proc.returncode != 0:
        pytest.fail(f"node failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


# --- the clips (§3.2) ---------------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("role", ["left", "center"])
def test_each_clip_is_1280x720_24fps_exactly_8s(role: str) -> None:
    path = BRANDING / pr.CLIPS[role]
    out = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_read_frames,duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, timeout=120).stdout
    s = json.loads(out)["streams"][0]
    assert (s["width"], s["height"]) == pr.CLIP_SIZE
    assert s["r_frame_rate"] == f"{pr.CLIP_FPS}/1"
    assert int(s["nb_read_frames"]) == pr.CLIP_FRAMES
    assert abs(float(s["duration"]) - pr.CLIP_SECONDS) < 1e-3


def test_only_the_left_and_centre_clips_are_used() -> None:
    from aadhi.compose.base import MASCOT_CLIPS, RETIRED_MASCOT_CLIPS

    assert pr.CLIPS == {"left": MASCOT_CLIPS["left"], "center": MASCOT_CLIPS["center"]}
    assert not set(pr.CLIPS.values()) & pr.NEVER_CLIPS
    assert not set(pr.CLIPS.values()) & set(RETIRED_MASCOT_CLIPS), "a retired clip is never named"
    with pytest.raises(ValueError, match="R-14"):
        pr.Placement(MASCOT_CLIPS["right"], (0, 0, 100, 100), (0, 0, 100, 100), 0)
    for clip in pr.CLIPS.values():
        assert (BRANDING / clip).is_file()


# --- placements and the JS mirror ----------------------------------------------------------------------------------------


def test_placements_keep_the_crops_aspect_and_lie_inside_the_clip_and_the_stage() -> None:
    for layout in pr.INTRO_LAYOUTS:
        p = pr.placement("landscape", layout)
        x, y, w, h = p.rect
        assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080
        cx, cy, cw, ch = p.crop
        assert w * ch == h * cw, layout  # exactly one aspect: drawn 1:1 at the box's size
        assert p.clip == pr.CLIPS["left"]
    for cx in pr.REEL_CARD_XS:
        p = pr.placement("portrait", "card", cx)
        assert p.rect == (cx, 800, 744, 744) and p.crop == (280, 0, 720, 720) and p.clip == pr.CLIPS["center"]
        assert cx + 744 <= 1080 and 800 + 744 <= 1920
    with pytest.raises(ValueError):
        pr.placement("portrait", "card", 500)
    with pytest.raises(ValueError):
        pr.placement("landscape", "card")
    # §3.3's numbers
    assert pr.placement("landscape", "card_left").rect == (96, 180, 720, 720)
    assert pr.placement("landscape", "pip").rect == (96, 432, 480, 480)
    assert pr.placement("landscape", "split").crop == (0, 0, 576, 720)
    assert pr.CARD_RADIUS == 40


def test_presenter_js_mirrors_the_python_placements_and_motion_constants() -> None:
    js = node_json("""
const m = await import('./web/js/dynamic/presenter.js');
const layouts = {};
for (const l of m.INTRO_LAYOUTS) layouts[l] = m.placement('landscape', l);
const reel = m.REEL_CARD_XS.map((x) => m.placement('portrait', 'card', x));
process.stdout.write(JSON.stringify({layouts, reel, xs: m.REEL_CARD_XS, fps: m.CLIP_FPS, frames: m.CLIP_FRAMES,
  wipe: m.WIPE_SECONDS, slide: m.SLIDE_SECONDS, fade: m.FADE_SECONDS, settle: [m.SETTLE_SECONDS, m.SETTLE_RISE],
  wall: m.WALL_TITLE_BOX, radius: m.CARD_RADIUS, recap: m.RECAP_LAYOUT, size: m.CLIP_SIZE}));
""")
    for layout in pr.INTRO_LAYOUTS:
        p = pr.placement("landscape", layout)
        assert js["layouts"][layout] == {"rect": list(p.rect), "crop": list(p.crop), "radius": p.radius}, layout
    assert [r["rect"][0] for r in js["reel"]] == list(pr.REEL_CARD_XS) == js["xs"]
    assert all(r["crop"] == list(pr.REEL_CROP) for r in js["reel"])
    assert (js["fps"], js["frames"], tuple(js["size"])) == (pr.CLIP_FPS, pr.CLIP_FRAMES, pr.CLIP_SIZE)
    assert (js["wipe"], js["slide"], js["fade"]) == (pr.WIPE_SECONDS, pr.SLIDE_SECONDS, pr.FADE_SECONDS)
    assert js["settle"] == [pr.SETTLE_SECONDS, pr.SETTLE_RISE]
    assert (js["wall"]["x0"], js["wall"]["y0"], js["wall"]["x1"], js["wall"]["y1"]) == pr.WALL_TITLE_BOX
    assert js["radius"] == pr.CARD_RADIUS
    assert js["recap"] == dict(pr.RECAP_LAYOUT)


def _norm(rows: Any) -> Any:
    return json.loads(json.dumps(rows))


def test_the_slot_tables_are_the_templates_own_slot_rows() -> None:
    js = node_json("""
const a = await import('./web/js/dynamic/templates/aadhi_intro.js');
const r = await import('./web/js/dynamic/templates/aadhi_recap.js');
process.stdout.write(JSON.stringify({intro: a.slotRows(), recap: r.slotRows()}));
""")
    for name, ours, theirs in (("aadhi_intro", pr.intro_slots(), js["intro"]), ("aadhi_recap", pr.recap_slots(), js["recap"])):
        ours = _norm(ours)
        assert set(ours) == set(theirs), name
        for slot, per_format in ours.items():
            assert set(per_format) == set(theirs[slot]), (name, slot)
            for fmt, per_variant in per_format.items():
                assert set(per_variant) == set(theirs[slot][fmt]), (name, slot, fmt)
                for variant, row in per_variant.items():
                    page = theirs[slot][fmt][variant]
                    for key in ("w", "max_lines", "size", "role", "face", "weight", "line_height", "tracking_em",
                                "prefer_lines", "critical"):
                        assert row[key] == page[key], (name, slot, fmt, variant, key, row[key], page[key])


def test_the_slot_rows_build_template_slot_specs() -> None:
    from aadhi.compose.dynamic.templates import SlotSpec

    for table in (pr.intro_slots(), pr.recap_slots()):
        for per_format in table.values():
            for per_variant in per_format.values():
                for row in per_variant.values():
                    SlotSpec(**row)  # the shape templates.py wraps (step 5's request)


# --- the studio wall (§3.3 "studio surface token") -----------------------------------------------------------------------


def _luminance_range(frames: Any) -> tuple[float, float]:
    """(min, max) WCAG relative luminance over uint8 RGB frames, one frame at a time through a 256-entry table (the
    whole clip at float64 would need 1.5 GB)."""
    import numpy as np

    c = np.arange(256, dtype=np.float64) / 255.0
    lut = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)
    lo, hi = 1.0, 0.0
    for frame in frames:
        lum = 0.2126 * lut[frame[..., 0]] + 0.7152 * lut[frame[..., 1]] + 0.0722 * lut[frame[..., 2]]
        lo, hi = min(lo, float(lum.min())), max(hi, float(lum.max()))
    return lo, hi


@needs_ffmpeg
def test_the_wall_measured_over_all_192_frames_is_the_one_pinned() -> None:
    np = pytest.importorskip("numpy")
    x0, y0, x1, y1 = pr.WALL.region
    w, h = x1 - x0, y1 - y0
    raw = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(BRANDING / pr.CLIPS["left"]), "-vf",
                          f"crop={w}:{h}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=300).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
    assert frames.shape[0] == pr.CLIP_FRAMES
    lo, hi = _luminance_range(frames)
    # the pinned darkest colour is the worst case the inks are checked against: no frame is darker (decoder rounding
    # of +-0.005 allowed), and the measurement still finds a pixel that dark (the pin is not stale on the light side)
    assert lo >= pr.WALL.darkest_luminance - 0.005
    assert lo <= pr.WALL.darkest_luminance + 0.01
    assert abs(hi - pr.WALL.lightest_luminance) < 0.01
    # the title box on the stage is the region at 1.5x (fullframe shows the whole clip at 1920x1080)
    bx0, by0, bx1, by1 = pr.WALL_TITLE_BOX
    assert by0 >= y0 * 1.5 and by1 <= y1 * 1.5 and bx0 >= x0 * 1.5 and bx1 <= x1 * 1.5, "the measured region holds the box"
    assert x0 % 2 == 0 and y0 % 2 == 0 and w % 2 == 0 and h % 2 == 0, "ffmpeg crops 4:2:0 on even px"


def test_the_wall_inks_hold_text_contrast_on_the_darkest_wall() -> None:
    from aadhi.compose.dynamic.colour_math import contrast_ratio, relative_luminance

    assert abs(relative_luminance(pr.WALL.darkest) - pr.WALL.darkest_luminance) < 0.002
    inks = pr.wall_inks()
    assert inks["wall"] == pr.WALL.darkest
    for key in ("wall_ink", "wall_ink2"):
        assert contrast_ratio(inks[key], pr.WALL.darkest) >= pr.WALL_MIN_CONTRAST, key
        assert contrast_ratio(inks[key], pr.WALL.lightest) >= pr.WALL_MIN_CONTRAST, key


# --- timing (§3.3) -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "fmt", "speech_end", "t_exit", "duration", "too_long"), [
    ("intro", "landscape", None, 4.4, 5.0, False),       # silent: the title holds until 5.0 s, the wipe ends it
    ("intro", "landscape", 3.0, 4.4, 5.0, False),        # ends early: held
    ("intro", "landscape", 5.5, 5.8, 6.4, False),        # t_exit = last word + 0.3
    ("intro", "landscape", 7.1, 7.4, 8.0, False),        # the longest allowed line
    ("intro", "landscape", 7.4, 7.4, 8.0, True),         # too long: the lint asks for a shorter one; held at 8.0
    ("intro", "portrait", None, 4.7, 5.0, False),        # 9:16: the slide is 300 ms
    ("intro", "portrait", 6.0, 6.3, 6.6, False),
    ("recap", "landscape", None, 4.7, 5.0, False),       # the card's fade is 300 ms
    ("recap", "portrait", 4.9, 5.2, 5.5, False),
])
def test_timing_follows_the_rule(kind: Any, fmt: Any, speech_end: float | None, t_exit: float, duration: float,
                                 too_long: bool) -> None:
    t = pr.timing(kind, fmt, speech_end)
    assert (t.t_exit, t.duration, t.too_long) == (t_exit, duration, too_long)
    assert pr.MIN_SECONDS <= t.duration <= pr.MAX_SECONDS
    assert t.audio_offset == {"landscape": 0.8, "portrait": 0.15}[fmt]
    assert t.duration <= pr.CLIP_SECONDS, "the clip never loops or freezes"


def test_exits_and_entrances() -> None:
    assert pr.exit_of("intro", "landscape") == ("wipe", 0.6)
    assert pr.exit_of("intro", "portrait") == ("slide_down", 0.3)
    assert pr.exit_of("recap", "landscape") == ("fade", 0.3)
    assert pr.enter_of("intro", "portrait") == "rise"
    assert pr.enter_of("intro", "landscape") == pr.enter_of("recap", "portrait") == "none"


# --- choices -------------------------------------------------------------------------------------------------------------


def test_the_intro_layout_is_seeded_stored_wins_pip_needs_a_hook_last_used_is_left_out() -> None:
    picks = {pr.intro_layout(presentation(look_seed=s), has_hook=True) for s in range(200)}
    assert picks == set(pr.INTRO_LAYOUTS), "every layout is reachable"
    no_hook = {pr.intro_layout(presentation(look_seed=s), has_hook=False) for s in range(200)}
    assert no_hook == set(pr.INTRO_LAYOUTS) - {"pip"}
    for s in range(50):
        p = presentation(look_seed=s)
        first = pr.intro_layout(p, has_hook=True)
        assert pr.intro_layout(p, has_hook=True) == first, "deterministic"
        assert pr.intro_layout(p, has_hook=True, last_used=first) != first
    assert pr.intro_layout(presentation(intro_layout="split"), has_hook=False) == "split"
    assert pr.intro_layout(presentation(intro_layout="pip"), has_hook=False) != "pip"
    assert pr.intro_layout(presentation(intro_layout="pip"), has_hook=True) == "pip"
    assert pr.intro_layout(presentation(format="portrait"), has_hook=True) == "card"
    epochs = {pr.intro_layout(presentation(look_seed=7, layout_epoch=e), has_hook=True) for e in range(40)}
    assert len(epochs) > 1, "New layouts (layout_epoch) re-rolls it"
    looks = {pr.intro_layout(presentation(look_seed=7, look_epoch=e), has_hook=True) for e in range(40)}
    assert len(looks) == 1, "Fresh look / New colours (look_epoch) does not move the layout"


def test_the_reel_card_x_is_seeded_and_the_stored_one_wins() -> None:
    xs = {pr.reel_card_x(presentation(format="portrait", look_seed=s)) for s in range(100)}
    assert xs == set(pr.REEL_CARD_XS)
    assert pr.reel_card_x(presentation(format="portrait", reel_card_x=240)) == 240
    assert pr.reel_card_x(presentation(format="portrait", reel_card_x=540)) in pr.REEL_CARD_XS


def test_the_recap_is_on_by_default_in_16_9_and_off_in_9_16() -> None:
    assert pr.recap_enabled(presentation()) is True
    assert pr.recap_enabled(presentation(aadhi_recap=False)) is False
    assert pr.recap_enabled(presentation(format="portrait")) is False


# --- the text the synthesised intro sets ----------------------------------------------------------------------------------


def test_the_chip_fits_its_budget() -> None:
    sp = screenplay()
    assert pr.chip_text(sp, "landscape") == "Unit 2: DC Circuits · Session 3"  # 59 chars with the subject: dropped
    assert pr.chip_text(sp, "portrait") == "Session 3"
    short = screenplay(subject_name="Physics", unit_name="Waves")
    assert pr.chip_text(short, "landscape") == "Physics · Waves · Session 3"
    assert pr.chip_text(screenplay(subject_name="", unit_name="", session_number=""), "landscape") == ""
    assert pr.plain_rich("a $b$ [[c]] *d*") == "a \\$b\\$ \\[\\[c\\]\\] \\*d\\*"


def test_the_intro_title_is_never_cut() -> None:
    """Step-5 review R5-5: the intro sets the session title whole in both formats; the page fits it in its 4-line slot
    and reports what does not fit at the minimum (preflight then asks for a shorter title). A 32-character cap cut
    the showcase's title to "Ohm's Law - The Rule That Runs…" in 9:16 although the headline holds it whole."""
    showcase = "Ohm's Law - The Rule That Runs Every Circuit"
    long = "Ohm's Law, Kirchhoff's Laws and the Rules That Quietly Run Every Circuit in Your Whole House"
    for fmt in ("landscape", "portrait"):
        sp = screenplay(fmt, session_title=showcase)
        assert pr.intro_title(sp, fmt) == showcase
        assert pr.intro_scene(sp, pr.plan_appearances(sp)).board[0].text == showcase  # type: ignore[arg-type]
        assert pr.intro_title(screenplay(fmt, session_title=long), fmt) == long, "too long: the page reports it"
    assert not hasattr(pr, "TITLE_MAX_CHARS")


def _headline_fit(text: str, fmt: str, language: str = "en-IN") -> Any:
    """The HarfBuzz text fit of ``text`` in the intro's title slot as the page fits it: 16:9 the narrowest title box
    (``pip`` sets the title smaller beside the hook) at 72-120 px in up to 4 lines; 9:16 the hook band's headline, the
    largest 4 px step of 88-104 px whose lines fit 4 AND whose height fits the 420 px band (hook_question.js
    ``fitInBox``), else the minimum's result."""
    from aadhi.compose.dynamic import faces, textfit

    fonts = faces.resolver_fonts("outfit")

    def fit(width: int, size: tuple[int, int], lines: int) -> Any:
        return textfit.fit_text(text, width=width, size=size, max_lines=lines, role="display", weight=700,
                                fonts=fonts, language=language, tracking_em=pr.TRACKING_EM)

    if fmt == "landscape":
        width = min(box[2] - box[0] for box, *_ in pr.INTRO_STACK.values() if box != pr.INTRO_STACK["pip"][0])
        return fit(width, (72, 120), 4)
    lo, hi, lines = pr.INTRO_HEADLINE
    r = None
    for size in range(hi, lo - 1, -4):
        r = fit(pr.HOOK_BAND_W, (size, size), lines)
        if r.fits and len(r.lines) * size * pr.DISPLAY_LINE_HEIGHT <= 420:
            return r
    return r


def test_the_showcase_title_fits_the_9_16_headline_whole() -> None:
    """The measured evidence for R5-5: the showcase's 44-character title fits the 9:16 headline (88-104 px, <= 4 lines,
    the 420 px band) whole, so the old cut lost "Every Circuit" for nothing; the 16:9 title box holds it too."""
    for fmt in ("landscape", "portrait"):
        r = _headline_fit("Ohm's Law - The Rule That Runs Every Circuit", fmt)
        assert r.fits, (fmt, r.lines)
        if fmt == "portrait":
            assert len(r.lines) * r.size * pr.DISPLAY_LINE_HEIGHT <= 420, "the hook band's height"
            assert r.size >= pr.INTRO_HEADLINE[0]
    # a title past the slot at its minimum does not fit: the page reports it (aadhi_intro.test.js), never a cut
    assert not _headline_fit("Ohm's Law, Kirchhoff's Laws and the Rules That Quietly Run Every Circuit", "portrait").fits


def _opening(heading: str | None, title: str = "Welcome", **meta: Any) -> Screenplay:
    """The lecture of :func:`screenplay` opening with a classic title scene (narrated by the narrator)."""
    data = screenplay(**meta).model_dump(mode="json")
    board = [{"id": "h", "kind": "heading", "text": heading}] if heading else []
    data["scenes"].insert(0, {"id": "s-title", "type": "title", "title": title, "board": board,
                              "beats": [beat("s-title-b1", "Welcome to this session on Ohm's law.")]})
    return Screenplay.model_validate(data)


def test_the_intro_does_not_repeat_the_lectures_own_opening_title() -> None:
    """Step-5 review R5-4: when the first shown scene already shows the session title (the classic title scene, the
    generator's cold open, whose heading may be shortened with an ellipsis), the intro names the unit, else the
    subject, and the chip leaves that part out; only a lecture with neither keeps the repeat."""
    long = "Ohm's Law and the Rules That Run Every Circuit"
    repeats = [_opening("Ohm's Law"), _opening(None, title="Ohm's Law"), _opening("ohm's  LAW"),
               _opening("Ohm's Law and the Rules That…", session_title=long),
               _opening("Ohm's Law and the Rules That...", session_title=long)]
    for fmt in ("landscape", "portrait"):
        for sp in repeats:
            assert pr.intro_title(sp, fmt) == "Unit 2: DC Circuits", (fmt, sp.scenes[0].board)
            scene = pr.intro_scene(sp, pr.plan_appearances(sp))  # type: ignore[arg-type]
            assert scene.board[0].text == scene.title == "Unit 2: DC Circuits"
            assert "Unit 2" not in (scene.subtitle or ""), "the chip leaves the part the title names out"
        assert pr.intro_title(_opening("Something else"), fmt) == "Ohm's Law", "no repeat: the session title"
        assert pr.intro_title(_opening("Ohm's", session_title=long), fmt) == long, "a short heading is no repeat"
        assert pr.intro_title(_opening("Ohm's Law", unit_name=""), fmt) == "Basic Electrical Engineering", \
            "no unit: the subject"
        bare = _opening("Ohm's Law", unit_name="", subject_name="")
        assert pr.intro_title(bare, fmt) == "Ohm's Law", "nothing else to name: the repeat is kept"
    assert pr.chip_text(screenplay(), "landscape", title="Unit 2: DC Circuits") == \
        "Basic Electrical Engineering · Session 3"
    # with no session title the first scene's own title is a repeat by definition: the unit names the session
    assert pr.intro_title(screenplay(session_title=""), "landscape") == "Unit 2: DC Circuits"


def _kitchen_sink(fmt: str) -> Screenplay:
    data = json.loads((ROOT_DIR / "tests" / "invariance" / "goldens" / "classic_kitchen_sink" / "screenplay.json")
                      .read_text("utf-8"))
    data["presentation"] = presentation(format=fmt).model_dump(mode="json")
    return Screenplay.model_validate(data)


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
def test_the_kitchen_sink_built_dynamic_does_not_show_its_title_twice(fmt: str) -> None:
    """R5-4's evidence: the classic kitchen sink opens with a title scene whose only text is the session title; built
    as a Dynamic video, Aadhi's intro was followed by the same title set again by kinetic_statement."""
    sp = _kitchen_sink(fmt)
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True))
    intro, first = tl.scenes[0], tl.scenes[1]
    assert (intro.scene_id, intro.look.template) == ("aadhi-intro", "aadhi_intro")  # type: ignore[union-attr]
    assert first.scene_id == "s-title" and [b.text for b in first.board] == ["Ohm's Law"]
    assert [b.text for b in intro.board] == ["Unit 2: DC Circuits"] and intro.title == "Unit 2: DC Circuits"


# --- the synthesised appearances in a timeline --------------------------------------------------------------------------


def test_plan_appearances() -> None:
    sp = screenplay()
    a = pr.plan_appearances(sp)
    assert a is not None and a.intro and a.recap_before == "s-recap"
    assert (a.intro_id, a.recap_id) == ("aadhi-intro", "aadhi-recap")
    assert pr.plan_appearances(sp, enabled=False) is None, "DYNAMIC_AADHI_INTRO=false"
    assert pr.plan_appearances(screenplay("portrait")).recap_before is None  # type: ignore[union-attr]
    assert pr.plan_appearances(screenplay(recap=False)).recap_before is None  # type: ignore[union-attr]
    classic = Screenplay.model_validate({**screenplay_dict(), "learning_objectives": []})
    assert pr.plan_appearances(classic) is None
    # a screenplay that already opens with its own presenter scene keeps it: nothing synthesised
    data = sp.model_dump(mode="json")
    data["scenes"].insert(0, {"id": "own-intro", "type": "title", "title": "Hi",
                              "board": [{"id": "t", "kind": "heading", "text": "Ohm"}],
                              "beats": [beat("own-b1", "Hi, I'm Aadhi!", speaker="aadhi")],
                              "look": {"presenter": {"clip": "left", "seconds": 5.0, "layout": "card_left"}}})
    own = Screenplay.model_validate(data)
    assert pr.plan_appearances(own).intro is False  # type: ignore[union-attr]
    # ids stay unique against the screenplay's
    data["scenes"][0]["id"] = "aadhi-intro"
    clash = pr.plan_appearances(Screenplay.model_validate(data))
    assert clash is not None and clash.intro_id == "aadhi-intro-2"
    assert pr.appearance_of("aadhi-intro", ["aadhi-intro"]) is None, "a screenplay scene is the screenplay's"
    assert pr.appearance_of("aadhi-intro-2", ["aadhi-intro"]) == "intro"
    assert pr.appearance_of("aadhi-recap", []) == "recap"
    assert pr.appearance_of("intro", []) is None


def _recap_dict(sid: str, *, retrieval: bool) -> dict[str, Any]:
    """A recap scene: the retrieval recap (§4.3 recap_retrieval's data: a blank question and takeaways), or a
    "Previously in ..." recap of prior knowledge (bullets: list_build draws it, as the showcase's s3)."""
    if retrieval:
        return {"id": sid, "type": "recap", "title": "Recap", "concept_id": "ohm",
                "board": [{"id": f"{sid}-q", "kind": "question", "text": "What flows?", "blank": True},
                          {"id": f"{sid}-t", "kind": "takeaway", "text": "Current"}],
                "beats": [beat(f"{sid}-b1", "What flows? Think.", board_item_id=f"{sid}-q"),
                          beat(f"{sid}-b2", "Current.", board_item_id=f"{sid}-t")]}
    return {"id": sid, "type": "recap", "title": "PREVIOUSLY IN ELECTRIC CIRCUITS", "concept_id": "ohm",
            "board": [{"id": f"{sid}-1", "kind": "bullet", "text": "Charge flows in a loop"},
                      {"id": f"{sid}-2", "kind": "bullet", "text": "A cell pushes it"}],
            "beats": [beat(f"{sid}-b1", "Last time we saw charge flow.", board_item_id=f"{sid}-1"),
                      beat(f"{sid}-b2", "And a cell pushes it.", board_item_id=f"{sid}-2")]}


def _lecture(*scenes: Any, fmt: str = "landscape") -> Screenplay:
    """The lecture of :func:`screenplay` with these scenes ("facts": its content scene)."""
    data = screenplay(fmt).model_dump(mode="json")
    facts = data["scenes"][0]
    data["scenes"] = [facts if s == "facts" else s for s in scenes]
    return Screenplay.model_validate(data)


CARD = {"id": "s-card", "type": "chapter_card", "title": "Part two", "chapter_label": "Part 2", "beats": []}


def test_the_recap_card_opens_the_retrieval_recap_never_a_previously_recap() -> None:
    """Step-5 review R5-1: the card went before the FIRST recap scene, which in a real lecture is often a "Previously
    in ..." recap near the start (the showcase's s3, drawn by list_build): Aadhi asked "Quick check: what are the 3 big
    ideas?" before anything was taught. It goes before the retrieval recap that recap_retrieval draws."""
    prev, retr = _recap_dict("s-prev", retrieval=False), _recap_dict("s-retr", retrieval=True)
    a = pr.plan_appearances(_lecture(prev, "facts", retr))
    assert a is not None and a.recap_before == "s-retr", "after the lesson, before the retrieval recap"
    a = pr.plan_appearances(_lecture(prev, "facts"))
    assert a is not None and a.recap_before is None, "no retrieval recap: no recap card at all"
    # two retrieval recaps (one closing a chapter, one closing the lecture): the lecture's last one
    mid = _recap_dict("s-mid", retrieval=True)
    a = pr.plan_appearances(_lecture("facts", CARD, mid, _recap_dict("s-end", retrieval=True)))
    assert a is not None and a.recap_before == "s-end"
    # a retrieval-shaped recap of prior knowledge ahead of the first chapter card is not the lecture's retrieval recap
    early = _recap_dict("s-early", retrieval=True)
    a = pr.plan_appearances(_lecture(early, "facts", CARD))
    assert a is not None and a.recap_before is None
    # step-5 audit A5-1: a single-chapter lecture has NO chapter card to guard with (the generator adds cards for
    # chapters 2+ only), so the card must still never come before anything is taught
    a = pr.plan_appearances(_lecture(early, "facts"))
    assert a is not None and a.recap_before is None, "a 'previously' recap opening a card-less lecture"
    a = pr.plan_appearances(_lecture(early, "facts", _recap_dict("s-end", retrieval=False)))
    assert a is not None and a.recap_before is None, "...also when the lecture ends with a list-shaped recap"
    a = pr.plan_appearances(_lecture(early, "facts", _recap_dict("s-end", retrieval=True)))
    assert a is not None and a.recap_before == "s-end", "the closing retrieval recap still gets it"
    assert pr.teaches(_lecture("facts").scenes[0]) and not pr.teaches(_lecture(early).scenes[0])
    # the screenplay's own presenter recap right before it wins (nothing synthesised)
    own = {"id": "s-own", "type": "recap", "title": "Recap", "concept_id": "ohm",
           "board": [{"id": "p", "kind": "question", "text": "Ready for a quick check?"}],
           "beats": [beat("s-own-b1", "Ready for a quick check?", board_item_id="p", speaker="aadhi")],
           "look": {"presenter": {"clip": "left", "seconds": 5.0, "layout": "card_left"}}}
    a = pr.plan_appearances(_lecture("facts", own, retr))
    assert a is not None and a.recap_before is None
    a = pr.plan_appearances(_lecture(own, "facts", CARD, retr))
    assert a is not None and a.recap_before is None, "wherever it stands: one recap appearance (R-12)"
    # one his template cannot draw shows no Aadhi, so it does not count (a second question: board_generic draws it)
    undrawn = {**own, "board": [*own["board"], {"id": "p2", "kind": "question", "text": "And another?"}]}
    a = pr.plan_appearances(_lecture("facts", undrawn, retr))
    assert a is not None and a.recap_before == "s-retr"


def test_the_showcase_built_in_16_9_has_no_recap_card_before_its_previously_recap() -> None:
    """R5-1's evidence, on the showcase itself: in 16:9 the card ran 50.65-55.65 s right before s3 "PREVIOUSLY IN
    ELECTRIC CIRCUITS" (list_build). The showcase has no recap that recap_retrieval draws, so it gets no card."""
    from tests.invariance import classic_fixture as fx

    data = fx.load_screenplay("showcase_ohms_law").model_dump(mode="json")
    data["presentation"] = presentation(format="landscape").model_dump(mode="json")
    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True))
    ids = [s.scene_id for s in tl.scenes]
    assert ids[0] == "aadhi-intro" and "aadhi-recap" not in ids, ids
    s3 = next(s for s in tl.scenes if s.scene_id == "s3")
    assert s3.type == "recap" and s3.look.template == "list_build"  # type: ignore[union-attr]


def _transform_survival(fmt: str) -> Screenplay:
    data = json.loads((ROOT_DIR / "tests" / "core" / "fixtures" / "transform_survival_dynamic.json").read_text("utf-8"))
    data["presentation"] = {**data["presentation"], "format": fmt}
    return Screenplay.model_validate(data)


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
def test_a_screenplays_own_presenter_scene_with_an_empty_board_opens_the_video_with_aadhi(fmt: str) -> None:
    """Step-5 review R5-2 (owner decision 2): transform_survival_dynamic's s1 is a presenter scene (look.presenter,
    Aadhi's beat) with its title and subtitle but an empty board. aadhi_intro refused it (no heading item), nothing was
    synthesised (it has a presenter), and board_generic opened the video without Aadhi while "Hi, I am Aadhi" played.
    aadhi_intro now takes the scene's own title as the title."""
    sp = _transform_survival(fmt)
    s1 = sp.scenes[0]
    assert isinstance(s1, BoardScene) and s1.board == [] and s1.look is not None and s1.look.presenter is not None
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True))
    first = tl.scenes[0]
    assert first.scene_id == "s1", "nothing synthesised before the screenplay's own presenter scene"
    assert first.look is not None and first.look.template == "aadhi_intro"
    assert first.look.presenter is not None and first.look.presenter["layout"] == first.look.variant
    assert "aadhi-intro" not in [s.scene_id for s in tl.scenes]


def test_a_presenter_scene_aadhi_intro_cannot_draw_still_gets_the_synthesised_intro() -> None:
    """The other half of R5-2: a first presenter scene the template does not draw (here a teacher's lock on another
    template) would open the video with no Aadhi; the synthesised intro goes before it."""
    data = _transform_survival("landscape").model_dump(mode="json")
    data["scenes"][0]["look"] = {**data["scenes"][0]["look"], "template": "hook_question", "lock": "pinned"}
    data["scenes"][0]["board"] = [{"id": "q", "kind": "question", "text": "Why does current flow at all?"}]
    locked = Screenplay.model_validate(data)
    a = pr.plan_appearances(locked)
    assert a is not None and a.intro
    tl = build_timeline(locked, make_manifest(locked), settings=Settings(_env_file=None, dynamic_enabled=True))
    assert [s.scene_id for s in tl.scenes[:2]] == ["aadhi-intro", "s1"]
    assert tl.scenes[0].look.template == "aadhi_intro"  # type: ignore[union-attr]
    assert tl.scenes[1].look.template == "hook_question"  # type: ignore[union-attr]


def test_the_studio_names_the_appearances_as_this_module_does() -> None:
    """web/js/studio/lib/sceneName.js (step-5 review R5-3) recognises the ids this module gives and names them with
    :data:`APPEARANCE_NAMES`, the names the render job's PreflightBlocked message uses."""
    js = node_json("""
const m = await import('./web/js/studio/lib/sceneName.js');
const ids = ['aadhi-intro', 'aadhi-recap', 'aadhi-intro-2', 'aadhi-recap-12', 'aadhi-intro-x', 'intro', 's1'];
process.stdout.write(JSON.stringify({names: m.APPEARANCE_NAMES,
  named: Object.fromEntries(ids.map((id) => [id, m.appearanceName({scene_id: id, scene_number: null})]))}));
""")
    assert js["names"] == dict(pr.APPEARANCE_NAMES)
    for sid, name in js["named"].items():
        kind = pr.appearance_of(sid, [])
        assert name == (pr.APPEARANCE_NAMES[kind] if kind else None), sid


def _inserted(sp: Screenplay) -> tuple[Timeline, Any]:
    """The timeline as step 5's timeline.py / direct.py / presentation.py requests build it (by hand here, the way the
    fixture generator does)."""
    from aadhi.compose.captions import normalize_cues, shift_cues
    from aadhi.compose.dynamic.direct import ScenePlan
    from aadhi.compose.dynamic.direct import plan as direct_plan
    from aadhi.compose.presentation import resolve_looks, timeline_presentation

    base = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True,
                                                                     dynamic_aadhi_intro=False))
    ids = [s.id for s in sp.scenes]
    plan = pr.plan_appearances(sp)
    assert plan is not None
    scenes = []
    t = 0.0
    if plan.intro:
        scenes.append(pr.intro_scene(sp, plan, index=0, start=0.0))
        t = scenes[-1].duration
    for s in base.scenes:
        if pr.appearance_of(s.scene_id, ids) is not None:
            continue
        if s.scene_id == plan.recap_before:
            scenes.append(pr.recap_scene(sp, plan, index=len(scenes), start=t))
            t = round(t + scenes[-1].duration, 3)
        scenes.append(s.model_copy(update={"index": len(scenes), "start": t}))
        t = round(t + s.duration, 3)
    chapters = pr.fold_opening_chapter(build_chapters(sp, scenes), scenes, plan)
    plans = direct_plan(sp, scenes, sp.presentation.format, chapters)  # type: ignore[union-attr]
    for i, s in enumerate(scenes):
        synth = pr.plan_for(s, sp, sp.presentation.format)  # type: ignore[union-attr]
        if synth is not None:
            plans[i] = ScenePlan(s.scene_id, synth[0], synth[1], (), 1.0, "structure", forced=True)
    looks = pr.apply_to_looks(sp, scenes, resolve_looks(sp, scenes, chapters, plans))
    for s, look in zip(scenes, looks, strict=True):
        s.look = look
    caps = [c for s in scenes for b in s.beats for c in shift_cues(b.captions, s.start)]
    data = base.model_dump(mode="json")
    data.update({"scenes": [s.model_dump(mode="json") for s in scenes], "total_duration": t,
                 "chapters": [c.model_dump(mode="json") for c in chapters],
                 "captions": [c.model_dump(mode="json") for c in normalize_cues(caps)],
                 "presentation": timeline_presentation(sp.presentation, scenes, t).model_dump(mode="json")})  # type: ignore[arg-type]
    return Timeline.model_validate(data), plan


def test_the_appearances_make_a_valid_dynamic_timeline() -> None:
    tl, plan = _inserted(screenplay())
    ids = [s.scene_id for s in tl.scenes]
    assert ids == ["aadhi-intro", "s-facts", "aadhi-recap", "s-recap"]
    intro, facts, recap, retrieval = tl.scenes
    assert (intro.look.template, recap.look.template) == ("aadhi_intro", "aadhi_recap")  # type: ignore[union-attr]
    assert intro.start == 0.0 and intro.duration == 5.0 and intro.audio_offset == 0.8
    assert intro.board[0].text == "Ohm's Law" and intro.subtitle == "Unit 2: DC Circuits · Session 3"
    assert intro.beats == [] and intro.audio_asset_key is None, "silent until B3/B4 give Aadhi his voice and lines"
    look = intro.look
    assert look is not None and look.presenter is not None
    assert look.presenter["clip_key"] == "aadhi_left_clean.mp4"
    assert look.presenter["layout"] == look.variant
    assert look.presenter["t_exit"] == 4.4 and look.presenter["exit_kind"] == "wipe"
    assert look.refs is not None and look.refs.accent_next == facts.look.tokens["block.base"]  # type: ignore[union-attr]
    assert facts.look.transition_in["kind"] == "crossfade" and facts.look.transition_in["seconds"] >= 0.3  # type: ignore[union-attr]
    assert recap.look.presenter["layout"] == "card_left" and recap.look.presenter["exit_kind"] == "fade"  # type: ignore[index,union-attr]
    assert recap.board[0].text == pr.RECAP_PROMPT
    assert retrieval.look.template == "recap_retrieval"  # type: ignore[union-attr]
    # the intro opens the first chapter: no 5 s "Introduction" chapter (YouTube would drop the first real one for it)
    assert [(c.start, c.title) for c in tl.chapters] == [(0.0, "Ohm's Law")]
    assert intro.look.tokens["struct.accent"] == facts.look.tokens["struct.accent"], "the chapter-1 accent (§3.3)"  # type: ignore[union-attr]
    assert tl.intro is None, "no classic intro in a Dynamic timeline"
    assert all(c.start >= facts.start for c in tl.captions), "the appearances are silent: no caption before s-facts"
    # the stored presenter is JSON-plain and survives a round trip
    again = Timeline.model_validate(json.loads(tl.model_dump_json()))
    assert again.scenes[0].look.presenter == look.presenter  # type: ignore[union-attr]


def test_a_portrait_lecture_opens_with_the_card_and_has_no_recap_appearance() -> None:
    tl, _ = _inserted(screenplay("portrait"))
    assert [s.scene_id for s in tl.scenes] == ["aadhi-intro", "s-facts", "s-recap"]
    p = tl.scenes[0].look.presenter  # type: ignore[union-attr]
    assert p["layout"] == "card" and p["clip_key"] == "aadhi_center_clean.mp4"
    assert p["rect"][1:] == [800, 744, 744] and p["rect"][0] in pr.REEL_CARD_XS
    assert p["exit_kind"] == "slide_down" and p["enter_kind"] == "rise"
    assert tl.scenes[0].audio_offset == 0.15


def test_presenter_of_and_plan_for_leave_ordinary_scenes_alone() -> None:
    tl, _ = _inserted(screenplay())
    sp = screenplay()
    assert pr.plan_for(tl.scenes[1], sp, "landscape") is None
    assert pr.presenter_of(tl.scenes[1], sp, "landscape") is None
    assert pr.plan_for(tl.scenes[0], sp, "landscape") == ("aadhi_intro", tl.scenes[0].look.variant)  # type: ignore[union-attr]


def test_apply_to_looks_gives_no_presenter_to_a_scene_another_template_draws() -> None:
    tl, _ = _inserted(screenplay())
    sp = screenplay()
    looks = [s.look for s in tl.scenes]
    looks[0] = looks[0].model_copy(update={"template": "hook_question", "presenter": None})  # type: ignore[union-attr]
    out = pr.apply_to_looks(sp, tl.scenes, looks)
    assert out[0].presenter is None, "DYNAMIC_AADHI_INTRO=false: hook_question draws the scene, no Aadhi"


def test_a_screenplay_presenter_scene_gets_its_own_layout_and_its_speech_timing() -> None:
    sp = screenplay()
    data = sp.model_dump(mode="json")
    data["scenes"].insert(0, {"id": "own-intro", "type": "title", "title": "Hi",
                              "board": [{"id": "t", "kind": "heading", "text": "Ohm"},
                                        {"id": "q", "kind": "question", "text": "Why?"}],
                              "beats": [beat("own-b1", "Hi, I'm Aadhi! Why does a fuse not melt?", speaker="aadhi")],
                              "look": {"presenter": {"clip": "left", "seconds": 6.0, "layout": "pip"}}})
    own = Screenplay.model_validate(data)
    tl = build_timeline(own, make_manifest(own), settings=Settings(_env_file=None, dynamic_enabled=True,
                                                                   dynamic_aadhi_intro=False))
    found = pr.presenter_of(tl.scenes[0], own, "landscape")
    assert found is not None
    kind, layout, spec = found
    assert (kind, layout) == ("intro", "pip")
    end = pr.speech_end(tl.scenes[0])
    assert end is not None
    assert spec["t_exit"] <= tl.scenes[0].duration - 0.6 + 1e-9, "the exit stays inside the scene the builder timed"


def test_the_fixtures_are_current() -> None:
    from tests.compose.dynamic import make_aadhi_fixtures

    assert make_aadhi_fixtures.main(["--check"]) == 0


# --- the frame cache (plan §2 "Aadhi", §1.12) -----------------------------------------------------------------------------


def test_extract_args_crop_before_scaling_frame_exact() -> None:
    args = pr.extract_args("in.mp4", crop=(0, 0, 720, 720), size=(480, 480), out_dir="out")
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("crop=720:720:0:0,scale=w=480:h=480")
    assert "fps=" not in vf
    assert args[args.index("-frames:v") + 1] == str(pr.CLIP_FRAMES)
    assert args[args.index("-start_number") + 1] == "0"
    assert "-ss" not in args
    assert args[-1].endswith("f_%06d.jpg")
    with pytest.raises(ValueError):
        pr.extract_args("in.mp4", crop=(0, 0, 1, 720), size=(480, 480), out_dir="out")
    with pytest.raises(ValueError):
        pr.cache_dir("root", "NOT-HEX", (0, 0, 720, 720), (480, 480))


@needs_ffmpeg
def test_ensure_frames_extracts_every_frame_once_and_reuses_them(tmp_path: Path) -> None:
    from PIL import Image

    clip = BRANDING / pr.CLIPS["left"]
    directory, count = pr.ensure_frames(tmp_path, clip, crop=(0, 0, 720, 720), size=(96, 96), ffmpeg=str(FFMPEG))
    assert count == pr.CLIP_FRAMES
    assert directory == pr.cache_dir(tmp_path, pr.clip_digest(clip), (0, 0, 720, 720), (96, 96))
    assert directory.relative_to(tmp_path).parts[:2] == ("derivatives", "aadhi")
    with Image.open(directory / pr.frame_name(0)) as im:
        assert im.size == (96, 96)
    with Image.open(directory / pr.frame_name(pr.CLIP_FRAMES - 1)) as im:
        assert im.size == (96, 96)
    assert not (directory / pr.frame_name(pr.CLIP_FRAMES)).exists()

    def boom(args: list[str]) -> None:
        raise AssertionError("a complete set is never extracted again")

    again, n = pr.ensure_frames(tmp_path, clip, crop=(0, 0, 720, 720), size=(96, 96), runner=boom)
    assert (again, n) == (directory, count)
    assert not list(directory.parent.glob(".tmp-*")), "no temporary directory is left behind"


@needs_ffmpeg
def test_a_cached_frame_is_the_clips_frame_n_cropped(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    from PIL import Image

    clip = BRANDING / pr.CLIPS["center"]
    crop, size = pr.REEL_CROP, (72, 72)
    directory, _ = pr.ensure_frames(tmp_path, clip, crop=crop, size=size, ffmpeg=str(FFMPEG))
    for n in (0, 100, 191):
        direct = tmp_path / f"direct_{n}.png"
        subprocess.run([str(FFMPEG), "-v", "error", "-y", "-i", str(clip), "-vf",
                        f"select=eq(n\\,{n}),crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},scale={size[0]}:{size[1]}:flags=bicubic",
                        "-frames:v", "1", str(direct)], check=True, timeout=120)
        with Image.open(directory / pr.frame_name(n)) as a, Image.open(direct) as b:
            diff = np.abs(np.asarray(a.convert("RGB"), float) - np.asarray(b.convert("RGB"), float)).mean()
        assert diff < 6, f"frame {n}: mean difference {diff:.2f} (JPEG at q2 only)"


def test_ensure_frames_survives_a_lost_race_and_an_incomplete_leftover(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not really a clip")
    crop, size = (0, 0, 720, 720), (8, 8)

    def fake(args: list[str]) -> None:
        out = Path(args[-1]).parent
        for n in range(3):
            (out / pr.frame_name(n)).write_bytes(b"jpg")

    # an incomplete leftover at the final place (no done.json) is replaced
    final = pr.cache_dir(tmp_path, pr.clip_digest(clip), crop, size)
    final.mkdir(parents=True)
    (final / pr.frame_name(0)).write_bytes(b"half")
    directory, count = pr.ensure_frames(tmp_path, clip, crop=crop, size=size, runner=fake)
    assert (directory, count) == (final, 3)
    assert json.loads((final / pr.DONE).read_text("utf-8"))["frames"] == 3

    # another worker finishes first, while this one extracts: its complete set stays, this one's is discarded
    other = (16, 16)
    winner = pr.cache_dir(tmp_path, pr.clip_digest(clip), crop, other)

    def racing(args: list[str]) -> None:
        tmp = Path(args[-1]).parent
        assert tmp.name.startswith(".tmp-") and tmp.parent == winner.parent
        fake(args)
        winner.mkdir()
        for n in range(5):
            (winner / pr.frame_name(n)).write_bytes(b"won")
        (winner / pr.DONE).write_text(json.dumps({"v": pr.CACHE_VERSION, "digest": pr.clip_digest(clip),
                                                  "crop": list(crop), "size": list(other), "frames": 5}), "utf-8")

    directory2, count2 = pr.ensure_frames(tmp_path, clip, crop=crop, size=other, runner=racing)
    assert (directory2, count2) == (winner, 5), "the winner's complete set is used"
    assert (winner / pr.frame_name(0)).read_bytes() == b"won"
    assert not list(winner.parent.glob(".tmp-*")), "the loser's temporary set is removed"


def test_sweep_cache_removes_old_clips_and_stale_temporaries(tmp_path: Path) -> None:
    keep, old = "a" * 64, "b" * 64
    k = pr.cache_dir(tmp_path, keep, (0, 0, 720, 720), (8, 8))
    o = pr.cache_dir(tmp_path, old, (0, 0, 720, 720), (8, 8))
    for d in (k, o):
        d.mkdir(parents=True)
    fresh = k.parent / ".tmp-8x8-1-aaaa"
    stale = k.parent / ".tmp-8x8-2-bbbb"
    fresh.mkdir()
    stale.mkdir()
    os.utime(stale, (1000.0, 1000.0))
    removed = pr.sweep_cache(tmp_path, [keep], now=1000.0 + pr.STALE_TMP_SECONDS + 1)
    assert k.is_dir() and not o.parent.parent.exists()
    assert not stale.exists()
    assert fresh.exists(), "a temporary set younger than the limit is another worker's, still extracting"
    assert any(p == stale for p in removed)
    assert pr.sweep_cache(tmp_path / "nothing", [keep]) == []


def _serves_presenter_urls() -> bool:
    import inspect

    from aadhi.compose.timeline import resolve_urls

    return "presenter" in inspect.getsource(resolve_urls)


@pytest.mark.skipif(not _serves_presenter_urls(), reason="step 5's timeline.py request (resolve_urls) is not applied yet")
def test_resolve_urls_gives_the_live_player_the_presenters_clip(app_env, asset_store) -> None:
    from aadhi.compose.timeline import resolve_urls

    tl, _ = _inserted(screenplay())
    out = resolve_urls(tl, asset_store)
    intro, recap = out.scenes[0], out.scenes[2]
    assert intro.look.presenter["url"] == "/branding/aadhi_left_clean.mp4"  # type: ignore[union-attr,index]
    assert recap.look.presenter["url"] == "/branding/aadhi_left_clean.mp4"  # type: ignore[union-attr,index]
    assert "url" not in tl.scenes[0].look.presenter, "the stored timeline keeps no URL"  # type: ignore[union-attr,operator]
    retired = tl.model_copy(deep=True)
    retired.scenes[0].look.presenter = {**retired.scenes[0].look.presenter, "clip_key": "aadhi_left.mp4"}  # type: ignore[union-attr,dict-item]
    assert resolve_urls(retired, asset_store).scenes[0].look.presenter["url"] == "/branding/aadhi_left_clean.mp4"  # type: ignore[union-attr,index]
    Timeline.model_validate(out.model_dump())


def test_fold_opening_chapter_keeps_any_other_chapter_list() -> None:
    from aadhi.schemas.timeline import Chapter

    tl, plan = _inserted(screenplay())
    scenes = tl.scenes
    built = build_chapters(screenplay(), scenes)
    assert built[0].title == "Introduction" and built[1].start == scenes[1].start
    assert pr.fold_opening_chapter(built, scenes, plan) == [Chapter(start=0.0, title=built[1].title), *built[2:]]
    assert pr.fold_opening_chapter(built, scenes, None) == built
    other = [Chapter(start=0.0, title="Intro"), Chapter(start=9.0, title="Later")]
    assert pr.fold_opening_chapter(other, scenes, plan) == other, "a list of another shape is left alone"


def _workspace_sweeps_the_cache() -> bool:
    from aadhi.compose import workspace

    return hasattr(workspace, "_sweep_presenter_cache")


@pytest.mark.skipif(not _workspace_sweeps_the_cache(), reason="step 5's workspace.py request is not applied yet")
def test_the_render_sweep_removes_the_cache_of_a_retired_clip(tmp_path: Path) -> None:
    from aadhi.compose import workspace

    settings = Settings(_env_file=None, render_work_dir=str(tmp_path / "work"), data_dir=str(tmp_path / "data"))
    root = workspace.work_root(settings)
    current = pr.cache_dir(root, pr.clip_digest(BRANDING / pr.CLIPS["left"]), (0, 0, 720, 720), (8, 8))
    retired = pr.cache_dir(root, "c" * 64, (0, 0, 720, 720), (8, 8))
    for d in (current, retired):
        d.mkdir(parents=True)
    assert workspace.sweep_stale(settings, lambda ids: {}) >= 1
    assert current.is_dir() and not retired.exists()
    assert workspace._sweep_presenter_cache(settings, tmp_path / "nothing") == []
