"""Cinematic scene composition (Phase 13): how a scene's elements share the frame, move and change.

  screenplay scene ─┬─ Visual Router (visuals.py)          visual_plan      the educational visual   (unchanged)
                    ├─ Presenter Director (presenters.py)   presenter_plan   who presents, what they do (unchanged)
                    └─ Cinematic composer (compose_scene)   cinematic_plan   where everything goes, when, and how
                         a template (explicit, a Visual Review choice, or a fixed table by the scene's role)
                         → layers on the normalized 16:9 frame (background, visual, board, presenter, labels,
                           title, subtitles), checked against the safe areas
                         → a camera move that keeps every important layer whole and clear of the title and
                           subtitles (or no move at all), a timeline, and the lesson's transition

The plan is data: the page (cinematic.js) renders it for the preview and the lesson, and the export records that
same page, so the preview and the video show the same composition. The composer never selects visuals (the
router does), never decides who presents or how they speak (the Director does), and never changes the lesson's
text: layers point to the scene's own fields, formulas stay MathJax and code stays Prism.

Rule-based by design (Phase 13): templates are chosen from an explicit choice or a fixed role table, not
reasoned about. Scenes without a cinematic plan (the default "Classic" setting) render exactly as before.
"""
import copy
import datetime
import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import models
import editor_api
import styles
import visual_director
from ai_providers import IMAGE, MediaRequest
from database import get_db
from presenters import _lesson_position, scene_role

VERSION = 1
COMPOSER_VERSION = 1  # the Intelligent Scene Composer (composer.py): a new version re-opens earlier approvals
MODES = ("classic", "cinematic")
TYPOGRAPHY = ("academic", "modern")
MOTIONS = ("subtle", "none")
TRANSITIONS = ("fade", "crossfade", "slide", "cut", "soft_fade", "zoom", "wipe")  # soft_fade, zoom, wipe: Phase 17
MOVING_TRANSITIONS = ("slide", "zoom", "wipe")  # become a fade when the lesson has no motion
BACKGROUNDS = ("auto", "studio", "gradient", "solid", "image", "video", "ai")
EMPHASIS = ("clear", "subtle")
CAMERA_MOVES = ("static", "slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "focus")
SHOTS = ("wide", "medium", "close", "presenter", "visual", "split", "full_canvas")
ANIMATIONS = ("fade_in", "fade_out", "slide_in", "slide_out", "scale_in", "scale_out", "highlight", "appear", "disappear")
TEXT_ROLES = ("title", "subtitle", "body", "label", "formula", "code", "callout", "quiz", "answer", "caption")
LAYER_TYPES = ("background", "visual", "board", "presenter", "label", "title", "subtitles")
FOCUS_TARGETS = ("board", "visual", "formula", "presenter", "title")
Z = {"background": 0, "visual": 20, "board": 30, "presenter": 40, "label": 50, "title": 60, "subtitles": 90}
TRANSITION_SECONDS = {"cut": 0.0, "fade": 0.5, "crossfade": 0.6, "slide": 0.55, "soft_fade": 0.8, "zoom": 0.6, "wipe": 0.6}
DEFAULT_STYLE = {"typography": "academic", "motion": "subtle", "transitions": "fade", "background": "auto", "emphasis": "clear"}

# ---- safe areas (normalized to the 16:9 frame) -----------------------------------------------------------------
# The subtitle band is the page's own (#subtitle-track, 60 px above the bottom): nothing important goes there.
# The title band holds the scene title as an overlay that the camera does not move (like a broadcast graphic).
FRAME = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
SUBTITLES = {"x": 0.0, "y": 0.85, "w": 1.0, "h": 0.15}
EDGE = 0.02              # nothing important closer than this to the frame's edge (after the camera moved)
CONTENT_Y = (0.19, 0.81)  # where boards and visuals live (leaves the camera room to move)
TITLE_Y = (0.05, 0.10)
SAFE_AREAS = {
    "top": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.17},
    "bottom": SUBTITLES,
    "left": {"x": 0.0, "y": 0.0, "w": 0.03, "h": 1.0},
    "right": {"x": 0.97, "y": 0.0, "w": 0.03, "h": 1.0},
    "subtitles": SUBTITLES,
    "content": {"x": 0.05, "y": CONTENT_Y[0], "w": 0.90, "h": CONTENT_Y[1] - CONTENT_Y[0]},
    "presenter": {"x": 0.70, "y": 0.12, "w": 0.27, "h": 0.73},
}

# Where a drawn or AI presenter stands (presenter on the right; mirrored for the left)
PRESENTER_BOXES = {
    "side": {"x": 0.71, "y": 0.14, "w": 0.25, "h": 0.71},
    "large": {"x": 0.64, "y": 0.11, "w": 0.30, "h": 0.74},
    "small": {"x": 0.74, "y": 0.30, "w": 0.21, "h": 0.55},
    "pip": {"x": 0.80, "y": 0.52, "w": 0.15, "h": 0.28},
}
# Aadhi (the mascot) is filmed in full-frame studio clips: where he stands in them, and his face
MASCOT_BOXES = {"right": {"x": 0.66, "y": 0.03, "w": 0.32, "h": 0.94}, "left": {"x": 0.03, "y": 0.03, "w": 0.33, "h": 0.95}}
MASCOT_REGION = (0.05, 0.63)  # the content beside him (mirrored for the left clip, where he stands a little further in)
FACE_SHARE = 0.42  # the top of a presenter box that holds the face (the teacher's head, an AI clip's head)
# Where the content may go next to the presenter (x range; the presenter column on the right)
REGION = {"side": (0.05, 0.67), "large": (0.05, 0.61), "small": (0.05, 0.70), "pip": (0.05, 0.95), "none": (0.05, 0.95)}
GAP = 0.02

# ---- templates (data) ------------------------------------------------------------------------------------------
# presenter: how the presenter appears (side | large | small | pip | hidden); split: the visual's share of the
# content width when the scene has a visual; board: how the board presents its text; the default shot and camera
# move and what the camera looks at. Boxes are computed from these (layout_boxes), then mirrored for a left presenter.
TEMPLATES = {
    "presenter_intro": {"label": "Presenter intro", "presenter": "large", "small": "side", "title": "hero", "board": "body", "split": None,
                        "shot": "medium", "movement": "slow_zoom_in", "target": "title"},
    "presenter_explanation": {"label": "Presenter + explanation", "presenter": "side", "small": "small", "title": "band", "board": "body",
                              "split": None, "shot": "medium", "movement": "slow_zoom_in", "target": "board"},
    "presenter_plus_visual": {"label": "Presenter + visual", "presenter": "side", "small": "pip", "title": "band", "board": "body", "split": 0.47,
                              "shot": "medium", "movement": "slow_zoom_in", "target": "visual"},
    "visual_focus": {"label": "Visual focus", "presenter": "hidden", "small": "pip", "title": "band", "board": "visual", "split": None,
                     "shot": "visual", "movement": "static", "target": "visual"},
    "formula_focus": {"label": "Formula focus", "presenter": "small", "small": "small", "title": "band", "board": "formula", "split": 0.36,
                      "shot": "close", "movement": "focus", "target": "formula"},
    "code_focus": {"label": "Code focus", "presenter": "hidden", "small": "pip", "title": "band", "board": "code", "split": 0.30,
                   "shot": "wide", "movement": "static", "target": "board"},
    "diagram_focus": {"label": "Diagram focus", "presenter": "pip", "small": "pip", "title": "band", "board": "body", "split": 0.60,
                      "shot": "visual", "movement": "focus", "target": "visual"},
    "comparison": {"label": "Comparison", "presenter": "small", "small": "small", "title": "band", "board": "comparison", "split": 0.34,
                   "shot": "wide", "movement": "static", "target": "board"},
    "quiz": {"label": "Quiz", "presenter": "side", "small": "small", "title": "band", "board": "quiz", "split": None,
             "shot": "wide", "movement": "static", "target": "board"},
    "summary": {"label": "Summary", "presenter": "side", "small": "small", "title": "band", "board": "summary", "split": 0.40,
                "shot": "medium", "movement": "slow_zoom_out", "target": "board"},
}
# The fixed role table (presenters.scene_role): used only when neither the screenplay nor Visual Review names one
ROLE_TEMPLATE = {"intro": "presenter_intro", "explanation": "presenter_explanation", "visual": "presenter_plus_visual",
                 "math": "formula_focus", "code": "code_focus", "text_heavy": "presenter_explanation", "quiz": "quiz",
                 "summary": "summary", "technical": "visual_focus", "video": "visual_focus"}
SHOT_SCALE = {"wide": 1.03, "medium": 1.06, "close": 1.10, "presenter": 1.08, "visual": 1.10, "split": 1.0, "full_canvas": 1.0}
MAX_SCALE = 1.12
FULL_CANVAS_TYPES = ("simulation", "visual", "p5_simulation")
PALETTES = {  # the page's own colours (deep purple / indigo, gold accents)
    "academic": {"gradient": ["#140a2e", "#2a1352", "#0d1b3d"], "solid": "#1a1036", "accent": "#FFD700"},
    "modern": {"gradient": ["#0b1224", "#1b2a4a", "#10233d"], "solid": "#111a2e", "accent": "#7fd4ff"},
}


def _box(x, y, w, h):
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def mirror(box):
    return None if box is None else _box(1 - box["x"] - box["w"], box["y"], box["w"], box["h"])


def overlap(a, b):
    return a["x"] < b["x"] + b["w"] - 1e-9 and b["x"] < a["x"] + a["w"] - 1e-9 and a["y"] < b["y"] + b["h"] - 1e-9 and b["y"] < a["y"] + a["h"] - 1e-9


def inside(box, area, tol=1e-6):
    return (box["x"] >= area["x"] - tol and box["y"] >= area["y"] - tol and box["x"] + box["w"] <= area["x"] + area["w"] + tol
            and box["y"] + box["h"] <= area["y"] + area["h"] + tol)


def face_box(box):
    return _box(box["x"], box["y"], box["w"], box["h"] * FACE_SHARE)


def valid_box(box):
    try:
        return all(isinstance(box[k], (int, float)) for k in "xywh") and box["w"] > 0 and box["h"] > 0 and inside(box, FRAME)
    except (KeyError, TypeError):
        return False


# ---- layout ----------------------------------------------------------------------------------------------------

def layout_boxes(template, *, placement, has_visual, has_board, has_labels, mascot_side=None, visual_first=True, split=None,
                 board_height=None, visual_height=None):
    """Boxes for a template with the presenter on the right: {title, visual, board, presenter, labels} (absent = None).
    placement: how the presenter appears here (side | large | small | pip | none); mascot_side: Aadhi's clip side
    when the presenter is the mascot (his box is fixed by the clip). split: the visual's share of the content width
    (the composer's choice; the template's by default); board_height: a board alone in its band sized to its text
    (normalized height, centred in the band) instead of filling the band."""
    t = TEMPLATES[template]
    x0, x1 = MASCOT_REGION if mascot_side else REGION[placement]
    presenter = (MASCOT_BOXES["right"] if mascot_side else PRESENTER_BOXES.get(placement)) if placement != "none" else None
    width = x1 - x0
    y0, y1 = CONTENT_Y
    boxes = {"title": None, "visual": None, "board": None, "presenter": presenter, "labels": None}
    if t["title"] == "hero":
        if placement == "none":
            boxes["title"] = _box(0.14, 0.27, 0.72, 0.17)
            if has_board:
                boxes["board"] = _box(0.18, 0.47, 0.64, 0.30)
        else:
            boxes["title"] = _box(x0 + 0.02, 0.26, width - 0.04, 0.18)
            if has_board:
                boxes["board"] = _box(x0 + 0.02, 0.47, width - 0.04, 0.30)
        if has_visual:  # an explicit intro with a visual: the visual below the title, the text beside it
            boxes["board"] = _box(x0 + 0.02 + (width - 0.04) * 0.52, 0.47, (width - 0.04) * 0.48, 0.30) if has_board else None
            boxes["visual"] = _box(x0 + 0.02, 0.47, (width - 0.04) * (0.5 if has_board else 1.0), 0.30)
        return boxes
    boxes["title"] = _box(x0, TITLE_Y[0], min(width, 0.90), TITLE_Y[1])
    if t["board"] == "visual":  # the main visual (an AI video) is the board, centred in the region
        h = y1 - y0 + 0.01
        w = min(width, h)  # on the 16:9 frame a 16:9 box is as wide (in frame units) as it is tall
        boxes["board"] = _box(x0 + (width - w) / 2, y0 - 0.005, w, h)
        return boxes
    split = (split or t["split"]) if has_visual and (split or t["split"]) else None
    if split:
        vw = width * split - GAP / 2
        visual = _box(x0, y0, vw, y1 - y0)
        board = _box(x0 + vw + GAP, y0, width - vw - GAP, y1 - y0) if has_board else None
        if board is None:  # only the visual: it takes the region (at most 0.62 wide, centred)
            vw = min(width, 0.62)
            visual = _box(x0 + (width - vw) / 2, y0, vw, y1 - y0)
        if not visual_first and board is not None:
            visual, board = _box(x0 + board["w"] + GAP, y0, visual["w"], visual["h"]), _box(x0, y0, board["w"], board["h"])
        if visual_height and visual_height < visual["h"] - 0.02 and placement != "pip":
            # a wide picture in a narrow column: a card of the picture's own shape, centred in the band
            vh = max(0.30, visual_height)
            visual = _box(visual["x"], y0 + ((y1 - y0) - vh - (0.10 if has_labels else 0.0)) / 2, visual["w"], vh)
        if placement == "pip" and board is not None and presenter and overlap(board, presenter):
            board = _box(board["x"], board["y"], board["w"], presenter["y"] - GAP - board["y"])  # above the small presenter
        elif board is not None and board_height and board_height < board["h"] - 0.02:
            # short text beside a visual: a card sized to it, centred on the visual (not a tall, mostly empty card)
            h = max(0.30, board_height)
            # centred on the visual as it will be seen (a full-height visual loses the label row to it)
            seen = visual["h"] - (0.10 if has_labels and visual["y"] + visual["h"] + 0.10 > y1 + 1e-6 else 0.0)
            board = _box(board["x"], visual["y"] + (seen - h) / 2, board["w"], h)
        boxes["visual"], boxes["board"] = visual, board
    elif has_board:
        bw = min(width, (0.90 if t["board"] == "code" else 0.80) if placement == "none" else width)  # code uses the whole width
        if t["board"] in ("formula", "comparison") and placement != "none":
            bw = min(width, 0.62)
        h = min(y1 - y0, max(0.30, board_height)) if board_height else y1 - y0
        top = y0 + ((y1 - y0) - h) / 2
        boxes["board"] = _box(x0 + (width - bw) / 2 if placement == "none" else x0, top, bw, h)
        if placement == "pip" and presenter and overlap(boxes["board"], presenter):
            boxes["board"] = _box(x0, top, presenter["x"] - GAP - x0, h)
    elif has_visual:
        vw = min(width, 0.70)
        boxes["visual"] = _box(x0 + (width - vw) / 2, y0, vw, y1 - y0)
    if has_labels:  # a row of label chips under what they label (the visual, else the board)
        target = "visual" if boxes["visual"] else "board"
        b = boxes[target]
        if b and b["y"] + b["h"] + 0.10 <= y1 + 1e-6:  # a compact card: the chips go below it, the card keeps its size
            boxes["labels"] = _box(b["x"], b["y"] + b["h"] + 0.015, b["w"], 0.085)
        elif b:
            boxes[target] = _box(b["x"], b["y"], b["w"], b["h"] - 0.10)
            boxes["labels"] = _box(b["x"], b["y"] + b["h"] - 0.085, b["w"], 0.085)
    content = [b for b in (boxes["visual"], boxes["board"]) if b]
    if content:  # the title lines up with the content column under it
        left = min(b["x"] for b in content)
        right = max(b["x"] + b["w"] for b in content)
        boxes["title"] = _box(left, TITLE_Y[0], right - left, TITLE_Y[1])
    return boxes


# ---- camera ----------------------------------------------------------------------------------------------------
# A framing is the part of the frame the camera shows: {x, y, w} with the frame's own aspect (normalized w = h).
# A layer at box b appears at (b - framing.xy) / framing.w. The camera never moves the title and the subtitles.

FULL = {"x": 0.0, "y": 0.0, "w": 1.0}


def through(framing, box):
    s = 1.0 / framing["w"]
    return {"x": (box["x"] - framing["x"]) * s, "y": (box["y"] - framing["y"]) * s, "w": box["w"] * s, "h": box["h"] * s}


def framing_ok(framing, important, forbidden):
    """Every important box, as the camera shows it, stays whole inside the frame (edge margin) and clear of the
    forbidden areas (the title overlay and the subtitle band)."""
    if framing["x"] < -1e-9 or framing["y"] < -1e-9 or framing["x"] + framing["w"] > 1 + 1e-9 or framing["y"] + framing["w"] > 1 + 1e-9:
        return False
    for box in important:
        b = through(framing, box)
        if b["x"] < EDGE - 1e-9 or b["y"] < EDGE - 1e-9 or b["x"] + b["w"] > 1 - EDGE + 1e-9 or b["y"] + b["h"] > 1 - EDGE + 1e-9:
            return False
        if any(overlap(b, f) for f in forbidden):
            return False
    return True


def best_framing(important, forbidden, target, max_scale, steps=24):
    """The closest framing (largest zoom up to max_scale) that keeps every important box whole and clear,
    as near the target's centre as allowed. Returns FULL when no zoom is possible."""
    cx = target["x"] + target["w"] / 2 if target else 0.5
    cy = target["y"] + target["h"] / 2 if target else 0.5
    scale = max_scale
    while scale > 1.0049:
        w = 1.0 / scale
        best = None
        for i in range(steps + 1):
            for j in range(steps + 1):
                f = {"x": (1 - w) * i / steps, "y": (1 - w) * j / steps, "w": w}
                if framing_ok(f, important, forbidden):
                    d = (f["x"] + w / 2 - cx) ** 2 + (f["y"] + w / 2 - cy) ** 2
                    if best is None or d < best[0]:
                        best = (d, f)
        if best:
            return {k: round(v, 4) for k, v in best[1].items()}
        scale -= 0.005
    return dict(FULL)


def pan_framings(important, forbidden, axis, scale, steps=24):
    """(start, end) of a pan at this zoom along an axis: the two furthest feasible framings, or None."""
    w = 1.0 / scale
    other = (1 - w) / 2
    feasible = []
    for i in range(steps + 1):
        v = (1 - w) * i / steps
        f = {"x": v, "y": other, "w": w} if axis == "x" else {"x": other, "y": v, "w": w}
        if framing_ok(f, important, forbidden):
            feasible.append(f)
    if len(feasible) < 2:
        return None
    return ({k: round(v, 4) for k, v in feasible[0].items()}, {k: round(v, 4) for k, v in feasible[-1].items()})


def camera_plan(movement, shot, target_box, important, forbidden, *, duration, start=0.6, motion="subtle", locked=None):
    """The camera for a scene: a move only as far as it keeps every important layer whole and readable."""
    cam = {"shot": shot, "movement": movement, "from": dict(FULL), "to": dict(FULL), "start": start, "duration": 0.0,
           "easing": "ease-in-out", "safe": True, "note": None}
    if locked:
        cam.update(movement="static", note=locked)
        return cam
    if motion == "none" or movement == "static" or shot in ("full_canvas", "split"):
        cam["movement"] = "static"
        if movement != "static" and motion == "none":
            cam["note"] = "motion switched off in the lesson style"
        return cam
    cap = min(MAX_SCALE, SHOT_SCALE.get(shot, 1.06), {"subtle": 1.07, "moderate": 1.10}.get(motion, 1.07))
    if movement in ("pan_left", "pan_right", "pan_up", "pan_down"):
        ends = pan_framings(important, forbidden, "x" if movement in ("pan_left", "pan_right") else "y", min(cap, 1.06))
        if ends is None:
            cam.update(movement="static", note="no room to pan without cropping content")
            return cam
        a, b = ends
        # pan_right: the view travels right (the content drifts left)
        cam["from"], cam["to"] = (a, b) if movement in ("pan_right", "pan_down") else (b, a)
    else:
        framing = best_framing(important, forbidden, target_box, cap)
        if framing["w"] > 0.992:
            cam.update(movement="static", note="no room to move without cropping content")
            return cam
        cam["from"], cam["to"] = (framing, dict(FULL)) if movement == "slow_zoom_out" else (dict(FULL), framing)
    cam["duration"] = round(max(3.0, min(duration - start, 3.6 if movement == "focus" else 40.0)), 2)
    if movement == "focus":
        cam["easing"] = "ease-out"
    cam["scale"] = round(1.0 / min(cam["from"]["w"], cam["to"]["w"]), 3)
    return cam


# ---- timeline --------------------------------------------------------------------------------------------------

PAUSE = re.compile(r"\[PAUSE(?::(\d+(?:\.\d+)?))?\]", re.I)
WORDS_PER_SECOND = 2.6
PAUSE_DEFAULT = 1.5  # the page's own default (index.html parseNarrationSegments)


def _spoken(text):
    return len(re.findall(r"[\w']+", PAUSE.sub(" ", str(text or "")).replace("[SYNC]", " ")))


def _pauses(text):
    return sum(float(m.group(1)) if m.group(1) else PAUSE_DEFAULT for m in PAUSE.finditer(str(text or "")))


def estimate_seconds(narration):
    """How long the scene's narration takes (words at a speaking pace, plus its pauses); at least 4 s."""
    text = str(narration or "")
    if not text.strip():
        return 5.0
    return round(max(4.0, min(600.0, _spoken(text) / WORDS_PER_SECOND + _pauses(text) + 0.8)), 2)


MIN_SECONDS_MAX = 600.0  # Phase 19: the editor's minimum duration for a scene is at most 10 minutes


def edit_min_seconds(scene):
    """Phase 19: the editor's minimum duration for the scene (scene.edit.min_seconds, 0 < x ≤ 600 s), or None. Presentation
    timing only: the page holds the scene at least this long (its narration is never sped up or cut)."""
    edit = scene.get("edit") if isinstance(scene, dict) else None
    value = edit.get("min_seconds") if isinstance(edit, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not 0 < value <= MIN_SECONDS_MAX:
        return None
    value = round(float(value), 2)
    return value if value > 0 else None


def sync_seconds(narration, n):
    """When the n-th [SYNC] reveal happens (estimated), or None if the narration has fewer."""
    text = str(narration or "")
    parts = text.split("[SYNC]")
    if n < 1 or n >= len(parts):
        return None
    before = "[SYNC]".join(parts[:n])
    return round(_spoken(before) / WORDS_PER_SECOND + _pauses(before) + 0.4, 2)


def anchor_time(at, narration, default):
    """(seconds, anchor): a number of seconds, or {"sync": n} (the n-th reveal), else the default."""
    if isinstance(at, (int, float)) and 0 <= at <= 600:
        return round(float(at), 2), None
    if isinstance(at, dict) and isinstance(at.get("sync"), int):
        t = sync_seconds(narration, at["sync"])
        return (t if t is not None else default), {"sync": at["sync"]}
    return default, None


# ---- explicit composition (screenplay field "composition", and Visual Review overrides) ----------------------------

def clean_composition(raw):
    """(composition, warnings): the screenplay's composition with unknown values dropped (and said so)."""
    out, warnings = {}, []
    if not isinstance(raw, dict):
        return out, warnings
    checks = (("template", TEMPLATES), ("camera", CAMERA_MOVES), ("shot", SHOTS), ("transition", TRANSITIONS),
              ("focus", FOCUS_TARGETS))
    for key, vocab in checks:
        if raw.get(key) is not None:
            if raw[key] in vocab:
                out[key] = raw[key]
            else:
                warnings.append(f"unknown {key} “{str(raw[key])[:40]}” ignored")
    for key, vocab in (("presenter_position", ("left", "right", "hidden")), ("visual_position", ("left", "right"))):
        if raw.get(key) is not None:
            if raw[key] in vocab:
                out[key] = raw[key]
            else:
                warnings.append(f"unknown {key} “{str(raw[key])[:40]}” ignored")
    bg = raw.get("background")
    if isinstance(bg, str):
        bg = {"type": bg}
    if isinstance(bg, dict) and bg.get("type") is not None:
        if bg["type"] in BACKGROUNDS:
            out["background"] = {"type": bg["type"]}
            if isinstance(bg.get("asset_id"), str) and re.fullmatch(r"[0-9a-f]{32}", bg["asset_id"]):
                out["background"]["asset_id"] = bg["asset_id"]
        else:
            warnings.append(f"unknown background “{str(bg['type'])[:40]}” ignored")
    labels = []
    for i, label in enumerate(raw.get("labels") or []):
        text = label.get("text") if isinstance(label, dict) else label
        if isinstance(text, str) and text.strip() and len(labels) < 6:
            labels.append({"index": i, "text": text.strip()[:80], "at": label.get("at") if isinstance(label, dict) else None})
    if len(raw.get("labels") or []) > 6:
        warnings.append("only the first 6 labels are shown")
    if labels:
        out["labels"] = labels
    emphasis = []
    for e in raw.get("emphasis") or []:
        if isinstance(e, dict) and (e.get("target") in ("board", "visual", "formula", "presenter")
                                    or (isinstance(e.get("target"), str) and re.fullmatch(r"label:\d", e["target"]))):
            emphasis.append({"target": e["target"], "at": e.get("at")})
    if emphasis:
        out["emphasis"] = emphasis[:6]
    return out, warnings


def check_overrides(overrides):
    """Visual Review changes for a scene's composition: only known values (422 otherwise)."""
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=422, detail="overrides must be an object.")
    allowed = {"template": TEMPLATES, "camera": CAMERA_MOVES, "transition": TRANSITIONS,
               "presenter_position": ("left", "right", "hidden"), "visual_position": ("left", "right"),
               "background": BACKGROUNDS, "presenter_size": ("dominant", "secondary", "small", "hidden"),
               "visual_size": ("dominant", "secondary", "side_panel", "hidden"), "motion": ("none", "subtle", "moderate"),
               "style_accent": styles.OVERRIDE_OPTIONS["accent"], "style_background": styles.OVERRIDE_OPTIONS["background"]}
    problems = [f"{k} is not something a composition can change" for k in overrides if k not in allowed]
    # "auto" gives a choice back to the composer
    problems += [f"unknown {k} “{str(v)[:40]}”" for k, v in overrides.items() if k in allowed and v != "auto" and v not in allowed[k]]
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems) + ".")
    return dict(overrides)


# ---- what the scene contains ---------------------------------------------------------------------------------

def found_nothing(side, panel):
    """The router planned no visual for an open request (nothing matched and no generation is planned): the page would show
    the concept map or a 'no image' notice in its place, so a composition gives it no room."""
    return isinstance(side, dict) and side.get("source") == "NONE" and (not panel or panel.get("type") == "image")


def _visual_slot(scene):
    """The educational visual the router chose, as a layer source: ("side" | "main", identity) or (None, None)."""
    plans = scene.get("visual_plan") if isinstance(scene.get("visual_plan"), dict) else {}
    kind = str(scene.get("type") or "")
    if kind in ("ai_video",) + FULL_CANVAS_TYPES:
        main = plans.get("main") if isinstance(plans.get("main"), dict) else {}
        if main.get("selection") == "removed":
            return None, None
        return "main", {"source": main.get("source"), "asset_id": main.get("asset_id"), "selection": main.get("selection"),
                        "renderer": main.get("renderer")}
    side = plans.get("side") if isinstance(plans.get("side"), dict) else None
    panel = scene.get("side_panel") if isinstance(scene.get("side_panel"), dict) else None
    if isinstance(side, dict):
        if side.get("selection") == "removed" or side.get("media") in (None, "NONE") and not panel:
            return None, None
        if found_nothing(side, panel):
            return None, None  # the router found nothing to show: the content gets the room (Visual Review says "No visual")
        return "side", {"source": side.get("source"), "asset_id": side.get("asset_id"), "selection": side.get("selection"),
                        "renderer": side.get("renderer"), "panel": (panel or {}).get("type")}
    if panel and panel.get("type"):
        return "side", {"panel": panel.get("type")}
    return None, None


def _board_role(scene, template):
    html = scene.get("html") if isinstance(scene.get("html"), str) else ""
    style = TEMPLATES[template]["board"]
    if style == "formula" or (style == "body" and re.search(r"\\\(|\\\[|\$\$|formula-block|math-block", html)):
        return "formula" if style == "formula" else "body"
    return {"code": "code", "quiz": "quiz", "summary": "body", "visual": "body", "body": "body", "comparison": "comparison"}.get(style, "body")


def presenter_context(scene, style_settings):
    """Who presents this scene and whether they are shown, from the Phase 12 plan (or Aadhi's lesson placement)."""
    legacy = style_settings.get("presenter_legacy", True)
    plan = scene.get("presenter_plan") if isinstance(scene.get("presenter_plan"), dict) else None
    default_side = style_settings.get("presenter_position") if style_settings.get("presenter_position") in ("left", "right") else "right"
    if not legacy and plan and plan.get("presenter_id") == style_settings.get("presenter_id"):
        side = plan.get("position") if plan.get("position") in ("left", "right") else default_side
        return {"type": plan.get("type") or "mascot", "presenter_id": plan.get("presenter_id"), "enabled": bool(plan.get("enabled")),
                "side": side, "review_removed": plan.get("review_status") == "removed", "media": bool(plan.get("media")),
                "placement": plan.get("placement")}
    position, _placement, _layout = _lesson_position(scene)
    side = position if position in ("left", "right") else default_side
    return {"type": "mascot", "presenter_id": "aadhi", "enabled": position != "hidden", "side": side, "review_removed": False,
            "media": False, "placement": _placement}


# ---- fingerprints ------------------------------------------------------------------------------------------------

def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def input_fingerprint(scene, style, presenter, composer=None, direction=None):
    """What the composition is made from: the scene's content, its visual and presenter, its explicit composition,
    the lesson style, and the composer (its version; the AI model when a model's suggestion decided). A Visual Review
    approval no longer applies when this changes (Phase 6 staleness)."""
    _slot, visual = _visual_slot(scene)
    return _sha({"v": VERSION, "type": scene.get("type"), "title": scene.get("title"), "subtitle": scene.get("subtitle"),
                 "html": _sha(scene.get("html") or ""), "composition": scene.get("composition"), "visual": visual,
                 "presenter": {k: presenter.get(k) for k in ("type", "presenter_id", "enabled", "side", "review_removed", "media")},
                 "style": {k: style.get(k) for k in ("typography", "motion", "transitions", "background", "background_asset_id")},
                 "composer": COMPOSER_VERSION if not composer else [COMPOSER_VERSION, composer],
                 **({"direction": direction} if direction else {})})


def plan_hash(plan):
    """The composition plan's own version fingerprint (what is rendered)."""
    keep = {k: plan.get(k) for k in ("version", "template", "layers", "camera", "transition", "background", "timeline")}
    return _sha(keep)


# ---- the composer --------------------------------------------------------------------------------------------------

def _style(settings):
    s = {**DEFAULT_STYLE, **{k: v for k, v in (settings or {}).items() if k in DEFAULT_STYLE and v is not None}}
    for key, vocab in (("typography", TYPOGRAPHY), ("motion", MOTIONS), ("transitions", TRANSITIONS), ("background", BACKGROUNDS),
                       ("emphasis", EMPHASIS)):
        if s[key] not in vocab:
            s[key] = DEFAULT_STYLE[key]
    s["background_asset_id"] = (settings or {}).get("background_asset_id")
    s["palette"] = _palette(settings or {}, s["typography"])
    return s


def _palette(settings, typography):
    """(name, gradient colours, solid colour) behind a cinematic scene: the Phase 17 style family's, or (a lesson that
    chose no style) the legacy typography's palette, exactly as before."""
    if settings.get("style") in styles.FAMILY_DEFS:
        try:
            t = styles.resolve(settings)["tokens"]
            return settings["style"], [t["bg-1"], t["bg-2"], t["bg-3"]], t["bg-solid"]
        except styles.Invalid:
            pass
    palette = PALETTES[typography]
    return typography, palette["gradient"], palette["solid"]


def scene_look(settings, scene):
    """The scene's effective style (Phase 17) for its plan: the lesson's style family with its overrides and the
    scene's own Visual Review style choices, resolved once (the page applies it; preview and export read the same)."""
    try:
        return styles.plan_look(settings, scene)
    except styles.Invalid:
        return styles.plan_look({"typography": settings.get("typography") if settings.get("typography") in TYPOGRAPHY else "academic"}, scene)


def choose_template(scene, index, count, explicit, review_template, has_visual):
    """(template, why): Visual Review first, then the screenplay's choice, then the fixed role table."""
    if review_template in TEMPLATES:
        return review_template, "chosen in Visual Review"
    if explicit in TEMPLATES:
        return explicit, "the screenplay asks for it"
    role = scene_role(scene, index, count)
    template = ROLE_TEMPLATE.get(role, "presenter_explanation")
    if template == "presenter_intro" and has_visual:
        template = "presenter_plus_visual"
    if template == "presenter_plus_visual" and not has_visual:
        template = "presenter_explanation"
    return template, f"a {role.replace('_', ' ')} scene"


def resolve_background(scene_bg, style, presenter, previous, resolve_asset):
    """(background, note): what fills the frame behind everything. Aadhi (the mascot) is filmed in his studio, so
    a lesson he presents keeps the studio; other backgrounds need the Aadhi Teacher, an AI presenter or none."""
    name, gradient, solid = style.get("palette") or (style["typography"], PALETTES[style["typography"]]["gradient"],
                                                    PALETTES[style["typography"]]["solid"])
    wanted = (scene_bg or {}).get("type") or style["background"]
    asset_id = (scene_bg or {}).get("asset_id") or style.get("background_asset_id")
    if presenter["type"] == "mascot" and wanted not in ("studio",):
        note = None if wanted == "auto" else "Aadhi is filmed in his studio; other backgrounds need the Aadhi Teacher, an AI presenter or no presenter"
        return {"type": "studio"}, note
    if wanted in ("auto", "gradient"):
        return {"type": "gradient", "palette": name, "colors": list(gradient)}, None
    if wanted == "studio":
        return {"type": "studio"}, None
    if wanted == "solid":
        return {"type": "solid", "palette": name, "color": solid}, None
    if wanted == "ai" and not asset_id and isinstance(previous, dict) and previous.get("type") == "image" and previous.get("generated"):
        asset_id = previous.get("asset_id")  # the lesson's AI background made earlier (kept with the lesson)
    if wanted in ("image", "video", "ai"):
        found = resolve_asset(asset_id) if asset_id else None
        kind = "image" if wanted == "ai" else wanted
        if found and found.get("kind") == kind:
            return {"type": kind, "asset_id": asset_id, "url": found.get("url"), "generated": wanted == "ai",
                    "scrim": 0.55}, None
        fallback = {"type": "gradient", "palette": name, "colors": list(gradient), "fallback": True}
        if wanted == "ai" and not asset_id:
            return fallback, "no AI background has been generated yet; the clean gradient is used"
        return fallback, f"the chosen background {kind} is unavailable; the clean gradient is used"
    return {"type": "gradient", "palette": name, "colors": list(gradient)}, None


def review_overrides(scene):
    """The scene's Visual Review choices for its composition (approved or changed), or {}."""
    review = (scene.get("visual_review") or {}).get("composition") if isinstance(scene.get("visual_review"), dict) else None
    overrides = (review or {}).get("overrides") if isinstance(review, dict) and review.get("status") in ("approved", "changed") else {}
    return overrides if isinstance(overrides, dict) else {}


def phase13_decision(scene, index, count, settings=None):
    """The Phase 13 composition choice (Visual Review, then the screenplay, then the fixed role table), as a decision.
    The intelligent composer (composer.py) replaces it as the default; it stays the last-resort fallback."""
    settings = settings or {}
    style = _style(settings)
    presenter = presenter_context(scene, settings)
    explicit, _warnings = clean_composition(scene.get("composition"))
    overrides = review_overrides(scene)
    slot, _identity = _visual_slot(scene)
    template, why = choose_template(scene, index, count, explicit.get("template"), overrides.get("template"), slot in ("side", "main"))
    t = TEMPLATES[template]
    side = overrides.get("presenter_position") or explicit.get("presenter_position") or presenter["side"]
    return {"template": template, "reason": why, "side": presenter["side"] if side == "hidden" else side,
            "presenter_role": "hidden" if side == "hidden" else None,
            "visual_first": (overrides.get("visual_position") or explicit.get("visual_position") or "left") == "left",
            "shot": explicit.get("shot"), "camera": overrides.get("camera") or explicit.get("camera") or t["movement"],
            "focus": explicit.get("focus") or t["target"], "motion": style["motion"],
            "transition": explicit.get("transition") or overrides.get("transition"),
            "background": {"type": overrides["background"]} if overrides.get("background") else explicit.get("background"),
            "emphasis": explicit.get("emphasis") or [], "source": "phase13"}


# Board representations chosen by the visual direction (Phase 15): data for the page's styles, on the board they restyle
BOARD_STYLES = {"steps": "body", "timeline": "body", "key_points": "body", "code_output": "code"}
PLACEMENT_FOR_ROLE = {"secondary": "side", "hidden": "hidden"}
MOTION_CAP = {"none": 1.0, "subtle": 1.07, "moderate": 1.10}


def build_plan(scene, index, count, settings=None, resolve_asset=lambda _id: None, decision=None):
    """The rendered composition (Phase 13 plan) for a composition decision: the template's layout with the decision's
    presenter role and side, the visual's share, the board's size, the camera, the motion level, the emphasis and the
    transition, checked against the safe areas. Without a decision: the Phase 13 choice (phase13_decision)."""
    settings = settings or {}
    if settings.get("mode", "classic") != "cinematic" or not isinstance(scene, dict):
        return None
    d = decision or phase13_decision(scene, index, count, settings)
    style = _style(settings)
    presenter = presenter_context(scene, settings)
    explicit, warnings = clean_composition(scene.get("composition"))
    review = (scene.get("visual_review") or {}).get("composition") if isinstance(scene.get("visual_review"), dict) else None
    slot, visual_identity = _visual_slot(scene)
    kind = str(scene.get("type") or "")
    has_visual = slot == "side" and d.get("visual_role") != "hidden"
    template = d["template"]
    t = TEMPLATES[template]
    notes = list(d.get("notes") or [])

    # Presenter: shown when the Director shows them and the decision gives them room; placed as decided
    side = d.get("side") if d.get("side") in ("left", "right") else presenter["side"]
    role = d.get("presenter_role")
    shown = presenter["enabled"] and role != "hidden"
    if role == "hidden" and presenter["enabled"] and (d.get("source") == "phase13" or {"presenter_position", "presenter_size"} & set(d.get("locked") or [])):
        notes.append("presenter hidden in this scene (explicit choice)")
    if role == "dominant":
        placement = "large" if template == "presenter_intro" else "side"
    elif role == "small":
        placement = t["small"]
    elif role in PLACEMENT_FOR_ROLE:
        placement = PLACEMENT_FOR_ROLE[role]
    else:
        placement = t["presenter"]
    if kind in FULL_CANVAS_TYPES or slot == "main" and template == "visual_focus":
        placement = "hidden"
    if placement == "hidden" and shown:
        notes.append("the visual needs the stage: the presenter steps away")
        shown = False
    mascot = presenter["type"] == "mascot"
    if shown and mascot and placement in ("pip", "small"):
        notes.append("Aadhi cannot be made small in his studio clip: the content gets the stage")
        shown = False
    if presenter["type"] in ("ai_avatar", "custom") and shown and not presenter["media"]:
        notes.append("no presenter clip yet (generate it in Visual Review); nothing is shown in its place")
    if not shown:
        placement = "none"

    # Layout (presenter on the right, mirrored for the left)
    has_board = kind not in FULL_CANVAS_TYPES and (bool(scene.get("html")) or kind in ("quiz_checkpoint", "ai_video", "content",
                                                                                      "example", "key-takeaway", "recap"))
    if t["title"] == "hero" and kind in ("title", "chapter_card"):
        has_board = False  # the title is the content
    labels = explicit.get("labels") or [dict(l, direction=True) for l in (d.get("direction_labels") or [])][:6]
    boxes = layout_boxes(template, placement=placement, has_visual=has_visual, has_board=has_board or t["board"] == "visual",
                         has_labels=bool(labels), mascot_side=presenter["side"] if mascot and shown else None,
                         visual_first=d.get("visual_first", True), split=d.get("split"), board_height=d.get("board_height"),
                         visual_height=d.get("visual_height"))
    if has_visual and not boxes["visual"]:
        warnings.append("this template has no place for the scene's visual, so it is not shown")
    if shown and side == "left":
        boxes = {k: mirror(v) for k, v in boxes.items()}
        if mascot:
            boxes["presenter"] = dict(MASCOT_BOXES["left"])

    duration = estimate_seconds(scene.get("narration"))
    narration = scene.get("narration")
    full_canvas = kind in FULL_CANVAS_TYPES
    shot = d.get("shot") or ("full_canvas" if full_canvas else t["shot"])

    # Background
    background, bg_note = resolve_background(d.get("background"), style, presenter, (scene.get("cinematic_plan") or {}).get("background")
                                             if isinstance(scene.get("cinematic_plan"), dict) else None, resolve_asset)
    if bg_note:
        notes.append(bg_note)
    if full_canvas:
        background = {"type": "canvas"}  # the visual fills the frame

    # Layers
    motion = d.get("motion") if d.get("motion") in MOTION_CAP else style["motion"]
    if style["motion"] == "none":
        motion = "none"  # the lesson style switched motion off: nothing overrides that
    enter = (lambda preferred: preferred if motion != "none" else "fade_in")
    layers = [{"id": "background", "type": "background", "box": dict(FRAME), "z": Z["background"], "visible": True, "opacity": 1,
               "scale": 1, "start": 0.0, "end": None, "enter": "appear", "exit": None, "important": False, "camera": background["type"] != "studio"}]
    if full_canvas:
        layers.append({"id": "visual", "type": "visual", "role": "full_canvas", "source": {"kind": "visual_plan", "slot": "main"},
                       "box": dict(FRAME), "z": Z["visual"], "visible": True, "opacity": 1, "scale": 1, "start": 0.0, "end": None,
                       "enter": "fade_in", "exit": None, "important": True, "camera": False})
    else:
        if boxes["visual"]:
            layers.append({"id": "visual", "type": "visual", "role": (visual_identity or {}).get("panel") or "visual",
                           "source": {"kind": "visual_plan", "slot": "side"}, "box": boxes["visual"], "z": Z["visual"], "visible": True,
                           "opacity": 1, "scale": 1, "start": d.get("visual_start", 0.3), "end": None, "enter": enter("scale_in"), "exit": None,
                           "important": True, "camera": True})
        if boxes["board"]:
            role_ = "body" if t["board"] == "visual" else _board_role(scene, template)
            if d.get("board_style") in BOARD_STYLES and BOARD_STYLES[d["board_style"]] == role_:
                role_ = d["board_style"]
            layers.append({"id": "board", "type": "board", "role": role_,
                           "source": {"kind": "visual_plan", "slot": "main"} if t["board"] == "visual" else {"kind": "scene", "field": "html"},
                           "box": boxes["board"], "z": Z["board"], "visible": True, "opacity": 1, "scale": 1, "start": 0.1, "end": None,
                           "enter": enter("fade_in"), "exit": None, "important": True, "camera": True, "style": t["board"]})
        if boxes["title"] and (scene.get("title") or kind == "quiz_checkpoint"):
            layers.append({"id": "title", "type": "title", "role": "title", "source": {"kind": "scene", "field": "title"},
                           "subtitle": bool(scene.get("subtitle")),
                           "box": boxes["title"], "z": Z["title"], "visible": True, "opacity": 1, "scale": 1, "start": 0.0, "end": None,
                           "enter": enter("slide_in") if t["title"] == "hero" else "fade_in", "exit": None, "important": True,
                           "camera": t["title"] == "hero", "variant": t["title"]})
        if boxes["labels"] and labels:
            items = []
            label_times = d.get("label_times") or []
            for n, label in enumerate(labels):
                default = label_times[n] if n < len(label_times) else round(1.2 + n * 0.8, 2)
                at, anchor = anchor_time(label.get("at"), narration, default)
                if label.get("direction"):
                    items.append({"source": {"kind": "direction", "field": "annotations", "index": label["index"]}, "at": at, "anchor": anchor})
                else:
                    items.append({"source": {"kind": "composition", "field": "labels", "index": label["index"]}, "at": at, "anchor": anchor})
            layers.append({"id": "labels", "type": "label", "role": "label", "items": items, "box": boxes["labels"], "z": Z["label"],
                           "visible": True, "opacity": 1, "scale": 1, "start": items[0]["at"], "end": None, "enter": enter("slide_in"),
                           "exit": None, "important": True, "camera": True})
    if shown and boxes["presenter"]:
        layers.append({"id": "presenter", "type": "presenter", "role": presenter["type"], "source": {"kind": "presenter_plan"},
                       "presenter_id": presenter["presenter_id"], "side": side, "placement": "side" if mascot else placement,
                       "box": boxes["presenter"], "face": face_box(boxes["presenter"]), "z": Z["presenter"], "visible": True,
                       "opacity": 1, "scale": round(boxes["presenter"]["h"] / PRESENTER_BOXES["side"]["h"], 3), "start": d.get("presenter_start", 0.2),
                       "end": None, "enter": enter("slide_in") if not mascot else "appear", "exit": None, "important": True,
                       "camera": not mascot})
    layers.append({"id": "subtitles", "type": "subtitles", "role": "caption", "source": {"kind": "narration"}, "box": dict(SUBTITLES),
                   "z": Z["subtitles"], "visible": True, "opacity": 1, "scale": 1, "start": 0.0, "end": None, "enter": None,
                   "exit": None, "important": False, "camera": False, "reserved": True})

    # Camera: what it may move and what must stay whole (the motion level caps the zoom)
    by_id = {layer["id"]: layer for layer in layers}
    important = [layer.get("face") or layer["box"] for layer in layers if layer["important"] and layer["camera"]]
    forbidden = [dict(SUBTITLES)] + ([by_id["title"]["box"]] if "title" in by_id and not by_id["title"]["camera"] else [])
    focus = d.get("focus") or t["target"]
    target_layer = by_id.get({"formula": "board"}.get(focus, focus))
    target_box = (target_layer.get("face") or target_layer["box"]) if target_layer else None
    movement = d.get("camera") or t["movement"]
    locked = "Aadhi's studio clip cannot move with the camera" if mascot and shown else None
    camera = camera_plan(movement, shot, target_box, important, forbidden, duration=duration, motion=motion, locked=locked)
    camera["target"] = focus if camera["movement"] != "static" else None
    if camera["movement"] == "focus" and not full_canvas:
        camera["start"] = d.get("camera_start") if isinstance(d.get("camera_start"), (int, float)) else anchor_time(
            (d.get("emphasis") or [{}])[0].get("at") if d.get("emphasis") else None, narration, 1.2)[0]
    if camera["note"]:
        notes.append(camera["note"])

    # Transition: the lesson's, unless this scene explicitly asks for another
    t_in = d.get("transition") or style["transitions"]
    if motion == "none" and t_in in MOVING_TRANSITIONS:
        t_in = "fade"
    transition = {"in": t_in, "out": style["transitions"] if not (motion == "none" and style["transitions"] in MOVING_TRANSITIONS) else "fade",
                  "duration": TRANSITION_SECONDS[t_in]}

    # Timeline (explicit seconds; [SYNC] anchors are followed by the page when the narration reaches them)
    timeline = [{"layer": layer["id"], "at": layer["start"], "event": layer["enter"]} for layer in layers
                if layer["enter"] and layer["type"] not in ("subtitles",)]
    for layer in layers:
        for n, item in enumerate(layer.get("items") or []):
            timeline.append({"layer": layer["id"], "item": n, "at": item["at"], "anchor": item["anchor"], "event": layer["enter"]})
    for e in d.get("emphasis") or []:
        target = e["target"]
        if target.split(":")[0] not in by_id and not (target.startswith("label") and "labels" in by_id) and not (target == "formula" and "board" in by_id):
            continue  # an emphasis on something this composition does not show is dropped
        at, anchor = anchor_time(e.get("at"), narration, 2.0)
        timeline.append({"layer": target.split(":")[0] if not target.startswith("label") else "labels",
                         "item": int(target.split(":")[1]) if target.startswith("label:") else None,
                         "at": at, "anchor": anchor, "event": "highlight", "duration": 1.6})
    if camera["movement"] != "static":
        timeline.append({"layer": "camera", "at": camera["start"], "event": camera["movement"], "duration": camera["duration"]})
    timeline.sort(key=lambda e: e["at"])

    plan = {"version": VERSION, "template": template, "template_label": t["label"], "reason": d.get("reason") or "", "shot": shot,
            "style": {**{k: style[k] for k in DEFAULT_STYLE}, "look": scene_look(settings, scene)}, "motion": motion,
            "background": background, "layers": layers, "camera": camera,
            "transition": transition, "duration": duration, "timeline": timeline,
            "safe_areas": {k: SAFE_AREAS[k] for k in ("top", "bottom", "subtitles", "content")},
            "presenter": {"type": presenter["type"], "presenter_id": presenter["presenter_id"], "shown": shown, "side": side},
            "notes": notes, "warnings": warnings,
            "review_status": (review or {}).get("status") if isinstance(review, dict) and review.get("status") in ("approved", "changed") else "pending"}
    if d.get("reveal") in ("progressive", "together") and kind != "quiz_checkpoint":
        plan["reveal"] = d["reveal"]  # the board's items: one by one with the narration, or together (a comparison)
    if isinstance(d.get("direction_summary"), dict):
        plan["direction"] = d["direction_summary"]
    hold = edit_min_seconds(scene)
    if hold is not None:
        # Phase 19: the editor's minimum duration (the page holds the scene at least this long; "duration" stays the
        # narration's estimate). Presentation timing: never part of the input fingerprint, so approvals are kept
        plan["min_seconds"] = hold
    plan["fingerprint"] = input_fingerprint(scene, style, presenter, d.get("ai_identity") if d.get("source") == "ai" else None,
                                            d.get("direction_fp"))
    if isinstance(review, dict) and review.get("fingerprint") and review["fingerprint"] != plan["fingerprint"]:
        plan["review_stale"] = True
        if review.get("status") == "approved":
            plan["review_status"] = "pending"  # an approval of an older composition never carries over
    problems = validate_plan(plan)
    if problems:  # a safety net: never show a composition that breaks the safe areas
        plan["warnings"] = plan["warnings"] + problems
    plan["plan_hash"] = plan_hash(plan)
    return plan


def compose_scene(scene, index, count, settings=None, resolve_asset=lambda _id: None, context=None):
    """The cinematic plan for one scene, decided by the Intelligent Scene Composer (composer.py), or None in Classic."""
    import composer
    return composer.compose_scene(scene, index, count, settings or {}, resolve_asset, context)


def compose_lesson(scenes, settings=None, resolve_asset=lambda _id: None, ai=None, concept_map=None, directions=None):
    import composer
    return composer.compose_lesson(scenes, settings or {}, resolve_asset, ai=ai, concept_map=concept_map, directions=directions)


def validate_plan(plan):
    """Problems with a composition plan (empty: valid): boxes on the frame, important layers clear of each other and
    of the subtitle band, known vocabulary, sane timing, and a camera that keeps important layers whole."""
    problems = []
    if not isinstance(plan, dict):
        return ["not a plan"]
    if plan.get("template") not in TEMPLATES:
        problems.append("unknown template")
    layers = plan.get("layers") or []
    ids = [layer.get("id") for layer in layers]
    if len(ids) != len(set(ids)):
        problems.append("duplicate layer ids")
    for layer in layers:
        if layer.get("type") not in LAYER_TYPES:
            problems.append(f"layer {layer.get('id')}: unknown type")
        if not valid_box(layer.get("box") or {}):
            problems.append(f"layer {layer.get('id')}: its box is not on the frame")
        if not 0 <= (layer.get("opacity") if isinstance(layer.get("opacity"), (int, float)) else -1) <= 1:
            problems.append(f"layer {layer.get('id')}: opacity must be 0..1")
        if layer.get("enter") not in (None,) + ANIMATIONS or layer.get("exit") not in (None,) + ANIMATIONS:
            problems.append(f"layer {layer.get('id')}: unknown animation")
        start, end = layer.get("start"), layer.get("end")
        if not isinstance(start, (int, float)) or start < 0 or (end is not None and (not isinstance(end, (int, float)) or end <= start)):
            problems.append(f"layer {layer.get('id')}: bad timing")
    z = [layer.get("z") for layer in layers]
    if any(not isinstance(v, int) for v in z):
        problems.append("every layer needs an order (z)")
    # a full-canvas visual is the canvas itself (the subtitles overlay it, as in the classic view)
    important = [layer for layer in layers if layer.get("important") and layer.get("type") not in ("subtitles", "background")
                 and layer.get("role") != "full_canvas"]
    for i, a in enumerate(important):
        box_a = a.get("face") if a.get("type") == "presenter" else a.get("box")
        if a.get("type") != "presenter" and valid_box(box_a or {}) and overlap(box_a, SUBTITLES):
            problems.append(f"layer {a['id']} reaches into the subtitle band")
        for b in important[i + 1:]:
            box_b = b.get("box")
            box_a_full = a.get("box")
            if valid_box(box_a_full or {}) and valid_box(box_b or {}) and overlap(box_a_full, box_b):
                problems.append(f"layers {a['id']} and {b['id']} overlap")
    camera = plan.get("camera") or {}
    if camera.get("movement") not in CAMERA_MOVES:
        problems.append("unknown camera movement")
    if camera.get("shot") not in SHOTS:
        problems.append("unknown shot")
    boxes = [(layer.get("face") or layer.get("box")) for layer in important if layer.get("camera")]
    title = next((layer for layer in layers if layer.get("type") == "title" and not layer.get("camera")), None)
    forbidden = [dict(SUBTITLES)] + ([title["box"]] if title else [])
    for key in ("from", "to"):
        f = camera.get(key)
        if not isinstance(f, dict) or not all(isinstance(f.get(k), (int, float)) for k in "xyw"):
            problems.append(f"camera {key} framing is missing")
        elif camera.get("movement") != "static" and not framing_ok(f, [b for b in boxes if valid_box(b)], forbidden):
            problems.append(f"the camera's {key} framing crops or covers important content")
        elif camera.get("movement") != "static" and 1.0 / f["w"] > MAX_SCALE + 1e-6:
            problems.append("the camera zooms further than allowed")
    transition = plan.get("transition") or {}
    if transition.get("in") not in TRANSITIONS or transition.get("out") not in TRANSITIONS:
        problems.append("unknown transition")
    if not isinstance(plan.get("duration"), (int, float)) or plan["duration"] <= 0:
        problems.append("the scene needs a duration")
    if "min_seconds" in plan:  # Phase 19: the editor's minimum duration, when the scene has one
        hold = plan["min_seconds"]
        if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not 0 < hold <= MIN_SECONDS_MAX:
            problems.append("the minimum duration must be more than 0 s and at most 600 s")
    return problems


def inspector_facts(plan):
    """The scene inspector's summary in words (Visual Review), advanced details left out."""
    if not plan:
        return []
    cam = plan.get("camera") or {}
    shots = {"wide": "Wide", "medium": "Medium", "close": "Close", "presenter": "On the presenter", "visual": "On the visual",
             "split": "Split", "full_canvas": "Full canvas"}
    moves = {"static": "still", "slow_zoom_in": "slow zoom in", "slow_zoom_out": "slow zoom out", "pan_left": "pan left",
             "pan_right": "pan right", "pan_up": "pan up", "pan_down": "pan down", "focus": "focus"}
    return [["Template", plan.get("template_label") or plan.get("template")],
            ["Presenter", f"{plan['presenter']['presenter_id']} · {plan['presenter']['side']}" if plan["presenter"]["shown"] else "Hidden"],
            ["Camera", f"{shots.get(cam.get('shot'), cam.get('shot'))} · {moves.get(cam.get('movement'), cam.get('movement'))}"
                       + (f" → {cam.get('target')}" if cam.get("target") and cam.get("movement") != "static" else "")],
            ["Transition", (plan.get("transition") or {}).get("in")], ["Background", (plan.get("background") or {}).get("type")],
            ["Duration", f"{plan.get('duration')} s"]]


# ---- AI background (optional, explicit): the existing AI image layer, cache, runs and recovery ----------------------

BACKGROUND_STYLE_WORDS = {
    "academic": "deep indigo and violet, soft gradient light, subtle paper texture",
    "modern": "dark navy and teal, soft geometric light, clean",
}


def background_prompt(style, prompt="", words=None):
    """The request for an educational background: the style's words plus the user's wish, always calm and empty
    (no text, no people), so it never competes with the lesson. The prompt is part of the cache identity. `words`: a
    Phase 17 style family's background words (Cinematic Education's are the "academic" ones, so its backgrounds are the
    ones already generated)."""
    style = style if style in BACKGROUND_STYLE_WORDS else "academic"
    wish = re.sub(r"\s+", " ", str(prompt or "")).strip()[:300]
    return (f"Minimal educational video background, {words or BACKGROUND_STYLE_WORDS[style]}"
            + (f", {wish}" if wish else "") + ". Calm, low contrast, soft focus, no text, no people, no logos, 16:9.")


def background_request(typography="academic", prompt="", *, provider=None, allow_fallback=None, model=None, force=False,
                       project_id=None, words=None):
    """The provider-independent request for a lesson background (an AI image, 16:9)."""
    return MediaRequest(media_type=IMAGE, prompt=background_prompt(typography, prompt, words), provider=provider,
                        allow_fallback=allow_fallback, model=model, aspect_ratio="16:9", force_regenerate=force,
                        purpose="cinematic-background", project_id=project_id, slot="background")


def store_generated_background(db, run, link):
    """A background generated in the background (or recovered after a restart) is kept for the lesson that asked:
    scenes whose plan waits for an AI background get it (a background chosen since is never replaced)."""
    if run.project_id is None or not run.asset_id:
        return False
    project = db.query(models.Project).filter(models.Project.id == run.project_id, models.Project.user_id == run.user_id).first()
    if project is None:
        return False
    out = {"changed": False}

    def apply(payload):  # Phase 20: on the lesson as it is now (a save committed meanwhile is kept)
        out["changed"] = False
        for scene in payload.get("scenes") or []:
            plan = scene.get("cinematic_plan") if isinstance(scene, dict) else None
            if isinstance(plan, dict) and isinstance(plan.get("background"), dict) and plan["background"].get("fallback") \
                    and (plan.get("style") or {}).get("background") == "ai":
                plan["background"] = {"type": "image", "asset_id": run.asset_id, "generated": True, "scrim": 0.55}
                out["changed"] = True
        lesson = payload.setdefault("cinematic", {})
        known = lesson.get("background_asset_id") == run.asset_id
        lesson["background_asset_id"] = run.asset_id
        return out["changed"] or not known

    editor_api.update_lesson(db, project, apply)
    return out["changed"]


# ---- API -----------------------------------------------------------------------------------------------------------

class CinematicSettings(BaseModel):
    mode: str = "classic"
    typography: str = "academic"
    motion: str = "subtle"
    transitions: str = "fade"
    background: str = "auto"
    background_asset_id: str | None = None
    emphasis: str = "clear"
    presenter_id: str = "aadhi"
    presenter_legacy: bool = True
    presenter_position: str = "right"
    composer: str = "rules"            # rules (deterministic, no model) | ai (a model helps with ambiguous scenes)
    composer_provider: str = "gemini"  # the text model's provider (Phase 11's providers; "fake" on test servers)
    composer_model: str | None = None
    director: str = "rules"            # the visual director (Phase 15): rules (deterministic) | ai (a model helps with ambiguous scenes)
    learner_level: str | None = None   # beginner | intermediate | advanced (None: general)
    style: str | None = None           # Phase 17: the lesson's style family (None: the legacy typography's look)
    style_version: int | None = None   # the family version the lesson was made with (None: the latest)
    style_overrides: dict | None = None  # a few bounded choices (styles.OVERRIDE_OPTIONS)


class PlanIn(BaseModel):
    scenes: list
    settings: CinematicSettings = CinematicSettings()
    project_id: int | None = None
    concept_map: list | None = None


class ReviewIn(BaseModel):
    project_id: int
    scene_index: int
    action: str  # keep | change | reset
    overrides: dict | None = None
    scene: dict | None = None
    scenes: list | None = None   # the page's lesson (its scenes' visual and presenter plans), the context the scene is composed in
    settings: CinematicSettings = CinematicSettings()
    concept_map: list | None = None


class StyleIn(BaseModel):
    """A lesson's video style (Phase 17): a family and a few bounded choices; style None = the lesson's original look."""
    project_id: int
    style: str | None = None
    style_version: int | None = None
    style_overrides: dict | None = None


class BackgroundIn(BaseModel):
    prompt: str = ""
    typography: str = "academic"
    style: str | None = None           # Phase 17: the style family's background words (None: the typography's, as before)
    provider: str | None = None
    allow_fallback: bool | None = None
    model: str | None = None
    force_regenerate: bool = False
    wait: bool = True
    project_id: int | None = None


def check_settings(settings):
    problems = []
    for key, vocab in (("mode", MODES), ("typography", TYPOGRAPHY), ("motion", MOTIONS), ("transitions", TRANSITIONS),
                       ("background", BACKGROUNDS), ("emphasis", EMPHASIS)):
        if getattr(settings, key) not in vocab:
            problems.append(f"{key} must be one of {', '.join(vocab)}")
    if settings.presenter_position not in ("left", "right"):
        problems.append("presenter_position must be left or right")
    if settings.style is not None and settings.style not in styles.FAMILY_DEFS:
        problems.append(f"style must be one of {', '.join(styles.FAMILIES)}")
    if settings.style_version is not None and not (1 <= settings.style_version <= 1000):
        problems.append("style_version must be a version number")
    try:
        styles.check_overrides(settings.style_overrides)
    except styles.Invalid as e:
        problems.append(str(e))
    if settings.background_asset_id is not None and not re.fullmatch(r"[0-9a-f]{32}", settings.background_asset_id):
        problems.append("background_asset_id must be an asset id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", settings.presenter_id or ""):
        problems.append("presenter_id must be a presenter id")
    if settings.composer not in ("rules", "ai"):
        problems.append("composer must be rules or ai")
    if settings.composer_provider not in ("gemini", "openai", "fake"):
        problems.append("composer_provider must be gemini, openai or fake")
    if settings.composer_model is not None and not re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", settings.composer_model):
        problems.append("composer_model must be a model code")
    if settings.director not in ("rules", "ai"):
        problems.append("director must be rules or ai")
    if settings.learner_level is not None and settings.learner_level not in ("beginner", "intermediate", "advanced", "general"):
        problems.append("learner_level must be beginner, intermediate, advanced or general")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems) + ".")
    return settings


def lesson_context(page_scenes, saved_scenes, index, scene):
    """The lesson a reviewed scene is composed in: the page's scenes (which carry their visual and presenter plans) when
    they line up with the saved lesson, else the saved ones; the reviewed scene itself is the saved one being changed."""
    if isinstance(page_scenes, list) and len(page_scenes) == len(saved_scenes) and len(page_scenes) <= 200:
        context = copy.deepcopy(page_scenes)
    else:
        context = list(saved_scenes)
    context[index] = scene
    return context


def check_concept_map(concept_map):
    """The lesson's concept map, bounded (ids and titles only)."""
    if not isinstance(concept_map, list):
        return None
    return [{"id": str(c.get("id"))[:40], "title": str(c.get("title"))[:80]} for c in concept_map[:100]
            if isinstance(c, dict) and c.get("id") is not None and isinstance(c.get("title"), str)]


def director_status():
    """The visual director's modes, whether AI-assisted direction can run here (per provider), and its vocabulary."""
    providers = {}
    for provider in ("gemini", "openai", "fake"):
        ok, reason = visual_director.ai_available({"composer_provider": provider})
        providers[provider] = {"available": ok, "reason": reason or None}
    return {"modes": list(visual_director.MODES), "ai_available": any(v["available"] for v in providers.values()), "providers": providers,
            "strategies": visual_director.STRATEGY_LABELS, "visuals": visual_director.VISUAL_KIND_LABELS,
            "presenter_roles": list(visual_director.PRESENTER_ROLES), "camera": list(visual_director.CAMERA_INTENTS),
            "motion": list(visual_director.MOTION_INTENTS), "prefer": list(visual_director.PREFER)}


def composer_status():
    """The composer's modes and whether AI-assisted composition can run here (and with which providers)."""
    import composer
    providers = {}
    for provider in ("gemini", "openai", "fake"):
        ok, reason = composer.ai_available({"composer_provider": provider})
        if provider != "fake" or ok:
            providers[provider] = {"available": ok, "reason": None if ok else reason}
    return {"version": COMPOSER_VERSION, "modes": ["rules", "ai"], "default": "rules",
            "ai_available": any(p["available"] for p in providers.values()), "providers": providers}


class RegenerateIn(BaseModel):
    scenes: list
    scene_index: int
    project_id: int | None = None
    settings: CinematicSettings = CinematicSettings()
    concept_map: list | None = None


class DirectionIn(BaseModel):
    scenes: list
    settings: CinematicSettings = CinematicSettings()
    project_id: int | None = None
    concept_map: list | None = None


class DirectionReviewIn(BaseModel):
    project_id: int
    scene_index: int
    action: str  # change | reset
    overrides: dict | None = None
    scene: dict | None = None
    scenes: list | None = None
    settings: CinematicSettings = CinematicSettings()
    concept_map: list | None = None


def create_cinematic_router(*, get_current_user, library, link_for, answer_for):
    router = APIRouter(prefix="/api/cinematic", tags=["cinematic"])

    def asset_resolver(db, user):
        link = link_for(user)

        def resolve(asset_id):
            asset = library.accessible(db, asset_id, user.id) if asset_id else None
            if asset is None or asset.status != "ready" or asset.kind not in ("image", "video"):
                return None
            # the size lets the composer give a wide picture a wide area and never stretch it
            return {"kind": asset.kind, "url": link(asset), "width": asset.width, "height": asset.height}
        return resolve

    @router.get("")
    def vocabulary(current_user=Depends(get_current_user)):
        """What a composition can use: templates, camera moves and shots, transitions, backgrounds, styles, safe areas."""
        return {"version": VERSION, "templates": {k: v["label"] for k, v in TEMPLATES.items()}, "camera": CAMERA_MOVES,
                "shots": SHOTS, "transitions": TRANSITIONS, "backgrounds": BACKGROUNDS, "animations": ANIMATIONS,
                "text_roles": TEXT_ROLES, "styles": {"typography": TYPOGRAPHY, "motion": MOTIONS, "emphasis": EMPHASIS},
                "looks": {"default": styles.DEFAULT_FAMILY, "families": styles.catalog(), "options": styles.options()},
                "safe_areas": SAFE_AREAS, "composer": composer_status(), "director": director_status()}

    @router.post("/plan")
    def plan(body: PlanIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The composition of every scene (never generates anything). Classic mode: no plans (scenes render as before)."""
        if len(body.scenes) > 200:
            raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
        check_settings(body.settings)
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == current_user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        settings = body.settings.model_dump()
        directions = None
        if body.settings.mode == "cinematic":  # the visual direction each composition follows (never asks a model here)
            directions = visual_director.directions_for(body.scenes, settings, check_concept_map(body.concept_map))
        plans = compose_lesson(body.scenes, settings, asset_resolver(db, current_user), concept_map=check_concept_map(body.concept_map),
                               directions=directions)
        return {"version": VERSION, "mode": body.settings.mode, "style": _style(settings) if body.settings.mode == "cinematic" else None,
                "plans": plans, "directions": directions}

    @router.post("/review")
    def review(body: ReviewIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """A Visual Review decision for a scene's composition (keep it, change template / presenter side / visual
        side / camera / transition / background, or go back to the automatic one), kept in the saved lesson
        (scene.visual_review.composition, with the fingerprint it was made for) and applied first by the composer."""
        check_settings(body.settings)
        if body.action not in ("keep", "change", "reset"):
            raise HTTPException(status_code=422, detail="action must be keep, change or reset.")
        overrides = check_overrides(body.overrides or {}) if body.action == "change" else None
        if body.action == "change" and not overrides:
            raise HTTPException(status_code=422, detail="Say what to change.")
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        settings = {**body.settings.model_dump(), "mode": "cinematic"}
        out = {}

        def apply(payload):
            """The decision applied to the lesson as it is now (Phase 20: again, if another save landed first)."""
            scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
            if not 0 <= body.scene_index < len(scenes):
                raise HTTPException(status_code=404, detail="Scene not found.")
            if body.scene is not None:
                editor_api.same_scene(scenes, body.scene_index, body.scene)  # Phase 19: the same scene, by id
                scenes[body.scene_index] = editor_api.reviewed_scene(scenes[body.scene_index], body.scene)  # Phase 20: never an older copy
            scene = scenes[body.scene_index]
            if not isinstance(scene, dict):
                raise HTTPException(status_code=422, detail="This scene cannot be reviewed.")
            reviews = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
            current = reviews.get("composition") if isinstance(reviews.get("composition"), dict) else {}
            stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
            fingerprint = None  # the saved plan's own (set below, once the scene is composed with the review)
            if body.action == "reset":
                reviews.pop("composition", None)
            elif body.action == "keep":
                kept = current.get("overrides") if current.get("status") in ("changed", "approved") and isinstance(current.get("overrides"), dict) else None
                layout = {k: v for k, v in (kept or {}).items() if not str(k).startswith("style_")}  # a scene's style is not its layout
                reviews["composition"] = {"status": "changed" if layout else "approved", **({"overrides": kept} if kept else {}),
                                          "fingerprint": fingerprint, "reviewed_at": stamp}
            else:
                saved = current.get("overrides") if isinstance(current.get("overrides"), dict) else {}  # a malformed record is ignored
                merged = {**saved, **overrides}
                merged = {k: v for k, v in merged.items() if v != "auto"}  # "Automatic": the composer decides again
                # Phase 17: a scene's style (style_accent / style_background) is not its layout: changing or undoing only
                # the style keeps an approval of the layout (approved, with the scene's style choices kept beside it)
                layout_approved = current.get("status") == "approved" and not any(not str(k).startswith("style_") for k in saved)
                style_only = all(str(k).startswith("style_") for k in {**overrides, **merged})
                if layout_approved and style_only:
                    reviews["composition"] = {"status": "approved", **({"overrides": merged} if merged else {}),
                                              "fingerprint": fingerprint, "reviewed_at": stamp}
                elif merged:
                    reviews["composition"] = {"status": "changed", "overrides": merged, "fingerprint": fingerprint, "reviewed_at": stamp}
                else:
                    reviews.pop("composition", None)
            if reviews:
                scene["visual_review"] = reviews
            else:
                scene.pop("visual_review", None)
            # Composed as in the lesson (its context and the AI suggestion the lesson kept; a review never asks the model)
            context = lesson_context(body.scenes, scenes, body.scene_index, scene)
            concept_map = check_concept_map(body.concept_map)
            directions = visual_director.directions_for(context, settings, concept_map)
            new_plan = compose_lesson(context, settings, asset_resolver(db, current_user), ai={"cached_only": True},
                                      concept_map=concept_map, directions=directions)[body.scene_index]
            if isinstance(reviews.get("composition"), dict):
                reviews["composition"]["fingerprint"] = new_plan["fingerprint"]
            scene["cinematic_plan"] = new_plan
            if directions[body.scene_index]:
                scene["visual_direction"] = directions[body.scene_index]
            out.update(review=reviews.get("composition"), plan=new_plan, direction=directions[body.scene_index])
            return True

        revision = editor_api.update_lesson(db, project, apply)
        library.record_project_references(db, project, current_user.id)
        return {"review": out["review"], "plan": out["plan"], "direction": out["direction"], "revision": revision}

    @router.post("/regenerate")
    def regenerate(body: RegenerateIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The composition of one scene decided again (in AI-assisted mode with a fresh model suggestion). Only the
        composition: no picture, clip or presenter is generated. A regenerated composition needs a new look in
        Visual Review (an approval is withdrawn); the user's explicit choices stay."""
        check_settings(body.settings)
        if len(body.scenes) > 200:
            raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
        if not 0 <= body.scene_index < len(body.scenes) or not isinstance(body.scenes[body.scene_index], dict):
            raise HTTPException(status_code=404, detail="Scene not found.")
        settings = {**body.settings.model_dump(), "mode": "cinematic"}
        project = None
        if body.project_id is not None:
            project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Lesson not found.")
            saved_scenes = json.loads(project.json_data or "{}").get("scenes")
            if isinstance(saved_scenes, list):  # Phase 19: the same scene, by id (it is written back at this position)
                editor_api.same_scene(saved_scenes, body.scene_index, body.scenes[body.scene_index])
        scenes = copy.deepcopy(body.scenes)
        scene = scenes[body.scene_index]

        def withdraw_approval(target):
            reviews = target.get("visual_review") if isinstance(target.get("visual_review"), dict) else {}
            if isinstance(reviews.get("composition"), dict) and reviews["composition"].get("status") == "approved":
                reviews.pop("composition")
                if reviews:
                    target["visual_review"] = reviews
                else:
                    target.pop("visual_review", None)

        withdraw_approval(scene)
        directions = visual_director.directions_for(scenes, settings, check_concept_map(body.concept_map))
        plans = compose_lesson(scenes, settings, asset_resolver(db, current_user), ai={"force": [body.scene_index]},
                               concept_map=check_concept_map(body.concept_map), directions=directions)
        plan = plans[body.scene_index]
        if directions[body.scene_index]:
            scene["visual_direction"] = directions[body.scene_index]
        revision = None
        if project is not None:
            def apply(payload):  # Phase 20: on the lesson as it is now (the same scene, by id; a save committed meanwhile is kept)
                saved = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
                if not (0 <= body.scene_index < len(saved) and isinstance(saved[body.scene_index], dict)):
                    return False
                editor_api.same_scene(saved, body.scene_index, body.scenes[body.scene_index])
                merged = editor_api.reviewed_scene(saved[body.scene_index], scene)  # never an older copy of the scene's content
                withdraw_approval(merged)
                saved[body.scene_index] = {**merged, "cinematic_plan": plan}
                return True

            revision = editor_api.update_lesson(db, project, apply)
            if revision is not None:
                library.record_project_references(db, project, current_user.id)
        return {"plan": plan, "review": (scene.get("visual_review") or {}).get("composition"), "direction": directions[body.scene_index],
                "revision": revision or editor_api.revision_of(project)}

    @router.post("/background")
    async def background(body: BackgroundIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """An AI background for the lesson (optional, explicit): the existing AI image layer with its provider choice,
        cache (identical requests are reused), durable runs and recovery. Nothing here generates on its own."""
        if body.typography not in TYPOGRAPHY:
            raise HTTPException(status_code=422, detail=f"typography must be one of {', '.join(TYPOGRAPHY)}.")
        if body.style is not None and body.style not in styles.FAMILY_DEFS:
            raise HTTPException(status_code=422, detail=f"style must be one of {', '.join(styles.FAMILIES)}.")
        if body.provider is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", body.provider):
            raise HTTPException(status_code=422, detail="provider must be a provider name.")
        if body.model is not None and not re.fullmatch(r"[A-Za-z0-9._/:-]{1,120}", body.model):
            raise HTTPException(status_code=422, detail="model must be a model code.")
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == current_user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        request = background_request(body.typography, body.prompt, provider=body.provider, allow_fallback=body.allow_fallback,
                                     model=body.model, force=body.force_regenerate, project_id=body.project_id,
                                     words=styles.background_words({"style": body.style}) if body.style else None)
        return await answer_for(db, current_user, request, body.wait)

    @router.post("/style")
    def lesson_style(body: StyleIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The lesson's video style (Phase 17), kept with the saved lesson in place: a new look is not a new version of
        the lesson's content (no new history entry, nothing generated, no approval re-opened). None: the original look."""
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        if body.style is not None and body.style not in styles.FAMILY_DEFS:
            raise HTTPException(status_code=422, detail=f"style must be one of {', '.join(styles.FAMILIES)}.")
        if body.style_version is not None and not (1 <= body.style_version <= 1000):
            raise HTTPException(status_code=422, detail="style_version must be a version number.")
        try:
            styles.check_overrides(body.style_overrides)
        except styles.Invalid as e:
            raise HTTPException(status_code=422, detail=str(e) + ".")
        choice = styles.lesson_choice({"style": body.style, "style_version": body.style_version,
                                       "style_overrides": body.style_overrides}) if body.style else None
        def apply(payload):  # Phase 20: on the lesson as it is now (a save committed meanwhile is kept)
            if choice:
                payload["cinematic_style"] = choice
            else:
                payload.pop("cinematic_style", None)
            return True

        revision = editor_api.update_lesson(db, project, apply)
        return {"cinematic_style": choice, "revision": revision}

    def save_directions(db, user, project_id, directions, plans=None, scene_ids=None):
        """The directions (and plans) kept with the saved lesson, where its scenes line up with the ones planned.
        Phase 20: written with editor_api.update_lesson (a save committed meanwhile is kept); with `scene_ids` (the
        planned scenes' ids) a saved scene that is not the planned one at that position (moved since) is left alone."""
        if project_id is None:
            return
        project = db.query(models.Project).filter(models.Project.id == project_id, models.Project.user_id == user.id).first()
        if project is None:
            return

        def apply(payload):
            saved = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
            if len(saved) != len(directions):
                return False
            before = json.dumps(saved, sort_keys=True, default=str)
            for i, direction in enumerate(directions):
                if direction and isinstance(saved[i], dict):
                    planned = scene_ids[i] if scene_ids is not None and i < len(scene_ids) else None
                    if isinstance(planned, str) and isinstance(saved[i].get("scene_id"), str) and planned != saved[i]["scene_id"]:
                        continue  # another scene is at this position now
                    saved[i]["visual_direction"] = direction
                    if plans is not None and plans[i] is not None:
                        saved[i]["cinematic_plan"] = plans[i]
            return json.dumps(saved, sort_keys=True, default=str) != before

        editor_api.update_lesson(db, project, apply)

    def planned_ids(scenes):
        return [s.get("scene_id") if isinstance(s, dict) else None for s in scenes]

    @router.post("/direction")
    def direction(body: DirectionIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The visual direction of every scene (Phase 15): the educational strategy, what should teach, the presenter's
        role, the camera and motion intent. Data only: nothing is generated. AI-assisted mode asks a text model about
        ambiguous scenes (bounded, validated, kept with the lesson); otherwise, and on any failure, the rules decide."""
        if len(body.scenes) > 200:
            raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
        check_settings(body.settings)
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == current_user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        if body.settings.mode != "cinematic":
            return {"version": visual_director.VERSION, "directions": [None] * len(body.scenes)}
        settings = body.settings.model_dump()
        directions = visual_director.direct_lesson(body.scenes, settings, check_concept_map(body.concept_map))
        save_directions(db, current_user, body.project_id, directions, scene_ids=planned_ids(body.scenes))
        return {"version": visual_director.VERSION, "directions": directions}

    @router.post("/direction/regenerate")
    def direction_regenerate(body: RegenerateIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """One scene's visual direction decided again (in AI-assisted mode with a fresh model suggestion), then its
        composition. Never media: a picture, clip or presenter is regenerated only from Visual Review's own buttons."""
        check_settings(body.settings)
        if len(body.scenes) > 200:
            raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
        if not 0 <= body.scene_index < len(body.scenes) or not isinstance(body.scenes[body.scene_index], dict):
            raise HTTPException(status_code=404, detail="Scene not found.")
        project = None
        if body.project_id is not None:
            project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
            if project is None:
                raise HTTPException(status_code=404, detail="Lesson not found.")
            saved_scenes = json.loads(project.json_data or "{}").get("scenes")
            if isinstance(saved_scenes, list):  # Phase 19: the same scene, by id (its direction is saved at this position)
                editor_api.same_scene(saved_scenes, body.scene_index, body.scenes[body.scene_index])
        settings = {**body.settings.model_dump(), "mode": "cinematic"}
        concept_map = check_concept_map(body.concept_map)
        scenes = copy.deepcopy(body.scenes)
        fresh = visual_director.direct_lesson(scenes, settings, concept_map, ai={"force": [body.scene_index]})
        directions = [scene.get("visual_direction") if isinstance(scene, dict) and i != body.scene_index else fresh[i]
                      for i, scene in enumerate(scenes)]
        scenes[body.scene_index]["visual_direction"] = fresh[body.scene_index]
        stable = visual_director.directions_for(scenes, settings, concept_map)
        plans = compose_lesson(scenes, settings, asset_resolver(db, current_user), ai={"cached_only": True}, concept_map=concept_map,
                               directions=stable)
        save_directions(db, current_user, body.project_id, [d if i == body.scene_index else None for i, d in enumerate(directions)],
                        scene_ids=planned_ids(body.scenes))
        if project is not None:
            db.refresh(project)
        return {"direction": stable[body.scene_index], "plan": plans[body.scene_index], "revision": editor_api.revision_of(project)}

    @router.post("/direction/review")
    def direction_review(body: DirectionReviewIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The user's visual direction choices for a scene (strategy, what should teach, presenter role, camera, motion,
        prefer existing / static), kept in the saved lesson as scene.visual_review.direction and applied above the
        director's own; "auto" gives a choice back; reset removes them all. Nothing is generated."""
        check_settings(body.settings)
        if body.action not in ("change", "reset"):
            raise HTTPException(status_code=422, detail="action must be change or reset")
        try:
            overrides = visual_director.check_user_overrides(body.overrides or {}) if body.action == "change" else {}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if body.action == "change" and not overrides:
            raise HTTPException(status_code=422, detail="Say what to change.")
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        settings = {**body.settings.model_dump(), "mode": "cinematic"}
        concept_map = check_concept_map(body.concept_map)
        out = {}

        def apply(payload):
            """The choices applied to the lesson as it is now (Phase 20: again, if another save landed first)."""
            scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
            if len(scenes) > 200:
                raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
            if not 0 <= body.scene_index < len(scenes):
                raise HTTPException(status_code=404, detail="Scene not found.")
            if body.scene is not None:
                editor_api.same_scene(scenes, body.scene_index, body.scene)  # Phase 19: the same scene, by id
                scenes[body.scene_index] = editor_api.reviewed_scene(scenes[body.scene_index], body.scene)  # Phase 20: never an older copy
            scene = scenes[body.scene_index]
            if not isinstance(scene, dict):
                raise HTTPException(status_code=422, detail="This scene cannot be reviewed.")
            reviews = scene.get("visual_review") if isinstance(scene.get("visual_review"), dict) else {}
            current = reviews.get("direction") if isinstance(reviews.get("direction"), dict) else {}
            stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
            saved = current.get("overrides") if isinstance(current.get("overrides"), dict) else {}  # a malformed record is ignored
            merged = {**saved, **overrides} if body.action == "change" else {}
            merged = {k: v for k, v in merged.items() if v != "auto"}
            if merged:
                reviews["direction"] = {"status": "changed", "overrides": merged, "reviewed_at": stamp}
            else:
                reviews.pop("direction", None)
            if reviews:
                scene["visual_review"] = reviews
            else:
                scene.pop("visual_review", None)
            context = lesson_context(body.scenes, scenes, body.scene_index, scene)
            stable = visual_director.directions_for(context, settings, concept_map)
            scene["visual_direction"] = stable[body.scene_index]
            plan = compose_lesson(context, settings, asset_resolver(db, current_user), ai={"cached_only": True}, concept_map=concept_map,
                                  directions=stable)[body.scene_index]
            scene["cinematic_plan"] = plan
            out.update(review=reviews.get("direction"), direction=stable[body.scene_index], plan=plan)
            return True

        revision = editor_api.update_lesson(db, project, apply)
        library.record_project_references(db, project, current_user.id)
        return {"review": out["review"], "direction": out["direction"], "plan": out["plan"], "revision": revision}

    return router
