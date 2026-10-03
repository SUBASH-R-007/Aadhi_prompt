"""AI Presenter / AI Teacher (Phase 12): who presents a lesson, when and where they appear, what they do,
and how their speech follows the lesson's narration.

  screenplay scene ─┬─ Visual Router (visuals.py): the educational visual            (unchanged)
                    └─ Presenter Director (plan_scene): the presenter plan
                         whether a presenter appears (by what the scene teaches), which one, where (a layout
                         the page's board already makes room for, checked against the content-safe area), the
                         behaviour, expression and gesture, and why (refined by the scene's Phase 15 visual
                         direction, its presenter interaction, where the Director itself chooses)
  narration ──► speech timeline (speech_timeline): the same [PAUSE]-split TTS segments the page plays, their
                timing, and a loudness envelope (what makes a presenter's mouth follow the voice)
  presenter clip (AI avatar profiles only) ──► presenter_request ──► the AI media layer (ai_media.py): the
                provider registry (capabilities), cache, durable runs and recovery, library assets  (reused)

Presenter types
  mascot       Aadhi, the existing animated mascot: MascotController plays it as before (talking animation)
  illustrated  Aadhi Teacher, drawn by the page (presenter.js): expressions, gestures, mouth driven by the
               speech envelope (follows the voice's loudness; not phoneme lip sync); no provider, no cost
  ai_avatar    a generated presenter clip: needs a provider that can speak the narration (capability check)
  custom       a user's presenter with a reference picture: needs a provider that accepts one

Scenes without a presenter_plan keep working: the plan is derived from their aadhi_position ("As the lesson
says"), so existing lessons look exactly as before.

Acting at a moment (Phase 16 synchronization): sync_capabilities says what a presenter can change mid-scene (the drawn
teacher: gesture and expression, in place; Aadhi: only his narration state; an AI clip: nothing, it is pre-rendered),
and acting_for turns a semantic action (point, explain, ...) into only those values, or None when it cannot.
"""
import asyncio
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import editor_api
import models
from ai_providers import PRESENTER, MediaRequest
from database import get_db
from visuals import run_request, scene_for_run

VERSION = 1
TYPES = ("mascot", "illustrated", "ai_avatar", "custom")
BEHAVIORS = ("idle", "talking", "explaining", "thinking", "listening", "welcoming", "concluding")
EXPRESSIONS = ("neutral", "friendly", "engaged", "thinking", "surprised", "happy", "encouraging", "serious")
GESTURES = ("none", "open_hand", "point", "counting", "explaining", "emphasis", "thinking", "welcome")
POSITIONS = ("left", "center", "right", "hidden")
PLACEMENTS = ("side", "pip", "foreground", "background")
MODES = ("lesson", "auto", "always", "off")  # lesson: follow the screenplay's aadhi_position (the default, as before)
STYLES = ("friendly", "energetic", "calm", "formal")
BACKGROUNDS = ("scene", "dark", "light", "transparent")

# When a presenter cannot show something, the closest thing it can (recorded as a fallback, never a failure)
EXPRESSION_FALLBACK = {"surprised": "engaged", "happy": "friendly", "encouraging": "friendly", "serious": "neutral",
                       "thinking": "neutral", "engaged": "friendly", "friendly": "neutral"}
GESTURE_FALLBACK = {"counting": "explaining", "emphasis": "explaining", "point": "open_hand", "welcome": "open_hand",
                    "thinking": "none", "explaining": "open_hand", "open_hand": "none"}


def map_vocab(wanted, supported, fallback, default):
    """(value shown, note or None): the wanted value if supported, else the nearest supported one."""
    if not supported:
        return None, None
    value, seen = wanted or default, set()
    while value and value not in supported and value not in seen:
        seen.add(value)
        value = fallback.get(value)
    if value not in supported:
        value = default if default in supported else supported[0]
    note = None if value == wanted or wanted is None else f"{wanted} shown as {value}"
    return value, note


# ---- profiles -------------------------------------------------------------------------------------------------

BUILTIN = {
    "aadhi": {
        "id": "aadhi", "name": "Aadhi", "type": "mascot", "role": "guide", "version": 1,
        "appearance": {"description": "Aadhi, the EduEngine mascot (the lesson's existing animated clips)", "palette": ["#B026FF", "#FFD700"]},
        "voice": "lesson narration", "defaults": {"position": "left", "behavior": "explaining", "expression": "friendly", "gesture": "none"},
        "capabilities": {"speech": "talking_animation", "lip_sync": False, "expressions": ["neutral", "friendly", "thinking", "happy"],
                         "gestures": [], "transparent_background": True, "consistent_identity": True, "reference_image": False,
                         "placements": ["side", "pip"], "provider": None},
    },
    "aadhi-teacher": {
        "id": "aadhi-teacher", "name": "Aadhi Teacher", "type": "illustrated", "role": "teacher", "version": 1,
        "appearance": {"description": "An illustrated teacher in a purple blazer with a gold collar, short dark hair, warm skin tone",
                       "palette": ["#5B2A86", "#FFD700", "#C98B62", "#2B1B14"], "clothing": "purple blazer, gold collar",
                       "hair": "short, dark"},
        "voice": "lesson narration", "defaults": {"position": "right", "behavior": "explaining", "expression": "engaged", "gesture": "explaining"},
        "capabilities": {"speech": "audio_envelope", "lip_sync": False, "expressions": list(EXPRESSIONS), "gestures": list(GESTURES),
                         "transparent_background": True, "consistent_identity": True, "reference_image": False,
                         "placements": ["side", "pip", "foreground"], "provider": None},
    },
    "ai-teacher": {
        "id": "ai-teacher", "name": "AI Teacher", "type": "ai_avatar", "role": "teacher", "version": 1,
        "appearance": {"description": "A friendly human teacher in a purple blazer, plain classroom background, chest-up framing",
                       "palette": ["#5B2A86", "#FFD700", "#E0B48C"], "clothing": "purple blazer", "hair": "shoulder-length, dark"},
        "voice": "lesson narration", "defaults": {"position": "right", "behavior": "explaining", "expression": "engaged", "gesture": "open_hand"},
        "capabilities": None,  # from the presenter provider actually configured
    },
}
DEFAULT_PRESENTER = "aadhi"


def appearance_hash(profile):
    data = {"id": profile["id"], "version": profile["version"], "appearance": profile.get("appearance"),
            "reference": profile.get("reference_asset_id")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def custom_profile(row):
    return {"id": row.id, "name": row.name, "type": "custom", "role": "teacher", "version": row.version,
            "appearance": json.loads(row.appearance or "{}"), "reference_asset_id": row.reference_asset_id,
            "voice": "lesson narration", "defaults": {"position": "right", "behavior": "explaining", "expression": "engaged",
                                                      "gesture": "open_hand", **json.loads(row.defaults or "{}")},
            "capabilities": None, "owner_id": row.owner_id}


def get_profile(db, user_id, presenter_id):
    if presenter_id in BUILTIN:
        return BUILTIN[presenter_id]
    if isinstance(presenter_id, str) and re.fullmatch(r"[0-9a-f]{32}", presenter_id):
        row = db.get(models.PresenterProfile, presenter_id)
        if row is not None and row.owner_id == user_id:
            return custom_profile(row)
    return None


def provider_view(registry):
    """The presenter providers of this server with what they can really do, and why the others cannot
    present (e.g. Veo makes video from text or pictures but cannot speak a supplied narration)."""
    order = registry.order(PRESENTER)
    providers, other = [], []
    for name in registry.names():
        provider = registry.get(name)
        caps = provider.capabilities()
        state, reason = registry.state(name)
        if PRESENTER in caps.media_types:
            providers.append({"name": name, "label": provider.label, "state": state, "reason": reason,
                              "paid": caps.paid, "capabilities": caps.to_dict().get("presenter", {}), "in_order": name in order})
        elif "video" in caps.media_types and name != "manual":
            other.append({"name": name, "label": provider.label,
                          "why": "it makes video from text or a picture but cannot speak a supplied narration, so it cannot present"})
    usable = [p for p in providers if p["state"] == "available" and p["in_order"]]
    return {"providers": providers, "not_presenter_capable": other, "available": bool(usable),
            "message": None if usable else "AI presenter generation isn't configured yet. You can continue with the Aadhi "
                                           "mascot, the illustrated Aadhi Teacher, or no presenter."}


def profile_view(profile, registry):
    """A profile with what it can do on this server (from its own renderer, or from the presenter provider)."""
    data = {k: v for k, v in profile.items() if k != "owner_id"}
    if profile["type"] in ("ai_avatar", "custom"):
        view = provider_view(registry)
        usable = [p for p in view["providers"] if p["state"] == "available" and p["in_order"]]
        if profile["type"] == "custom" and profile.get("reference_asset_id"):
            usable = [p for p in usable if p["capabilities"].get("reference_image")]
        best = usable[0] if usable else None
        data["capabilities"] = ({"speech": "lip_sync" if best["capabilities"].get("lip_sync") else "talking_animation", "provider": best["name"],
                                 **best["capabilities"], "placements": ["side", "pip"]} if best else None)
        data["available"] = bool(best)
        data["unavailable_reason"] = None if best else (
            "No presenter provider that accepts a reference picture is configured." if profile["type"] == "custom" and profile.get("reference_asset_id")
            else view["message"])
    else:
        data["available"] = True
        data["unavailable_reason"] = None
    data["appearance_hash"] = appearance_hash(profile)
    return data


# ---- layout: where the presenter stands, and what it must never cover -----------------------------------------------
# Normalized to the 16:9 stage. The layouts are the page's own (index.html applyAadhiLayout): each makes room
# for the presenter zone; the board and side zone are where the educational content is.

BOARD = {"left": (0.30, 0.75), "right": (0.25, 0.70), "center": (0.55, 1.00), "popup_bottom_left": (0.25, 1.00),
         "popup_bottom_right": (0.25, 1.00), "hidden": (0.25, 1.00)}
BOARD_Y = (0.08, 0.86)
# The side zone always holds content (a side visual, or the lesson's skill tree): the right zone when the presenter
# stands left, otherwise the left 25 %. The popup layouts leave no free corner, so drawn / AI presenters never use them.
SIDE_ZONE = {"left": (0.76, 1.00)}  # otherwise (0, 0.25)
SUBTITLES = {"x": 0.0, "y": 0.87, "w": 1.0, "h": 0.13}
BOXES = {"left": {"x": 0.02, "y": 0.14, "w": 0.26, "h": 0.72}, "right": {"x": 0.72, "y": 0.14, "w": 0.26, "h": 0.72},
         "center": {"x": 0.28, "y": 0.14, "w": 0.25, "h": 0.70},
         # picture-in-picture: small, low in the presenter's own zone, above the subtitles
         "pip_left": {"x": 0.05, "y": 0.52, "w": 0.18, "h": 0.34}, "pip_right": {"x": 0.77, "y": 0.52, "w": 0.18, "h": 0.34}}


def layout_for(position, placement):
    """The board layout (index.html applyAadhiLayout) that makes room for the presenter."""
    if position == "hidden" or placement is None:
        return "hidden"
    if placement == "pip":
        return "left" if position == "left" else "right"
    return position if position in ("left", "right", "center") else "right"


def box_key(layout, placement):
    return f"pip_{layout}" if placement == "pip" and layout in ("left", "right") else layout


def _overlap(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def content_zones(layout, has_side_visual=True):
    """The content-safe area of a layout: the board, the side zone (a side visual or the skill tree), the subtitles."""
    x0, x1 = BOARD.get(layout, BOARD["hidden"])
    s0, s1 = SIDE_ZONE.get(layout, (0.0, 0.25))
    return [{"name": "board", "x": x0, "y": BOARD_Y[0], "w": x1 - x0, "h": BOARD_Y[1] - BOARD_Y[0]},
            {"name": "side visual" if has_side_visual else "side panel", "x": s0, "y": 0.0, "w": s1 - s0, "h": 1.0},
            {"name": "subtitles", **SUBTITLES}]


def safe_box(layout, placement, has_side_visual=True):
    """(box, conflicts): the presenter's box for a layout and the content it would cover (empty: safe)."""
    box = BOXES.get(box_key(layout, placement))
    if box is None:
        return None, ["no free zone in this layout"]
    return dict(box), [z["name"] for z in content_zones(layout, has_side_visual) if _overlap(box, z)]


# ---- the Presenter Director ---------------------------------------------------------------------------------------

ROLE_RULES = {
    "intro": {"show": True, "placement": "side", "behavior": "welcoming", "expression": "happy", "gesture": "welcome",
              "why": "an introduction: the teacher welcomes the learner"},
    "summary": {"show": True, "placement": "side", "behavior": "concluding", "expression": "encouraging", "gesture": "open_hand",
                "why": "a summary: the teacher wraps up"},
    "quiz": {"show": True, "placement": "side", "behavior": "listening", "expression": "encouraging", "gesture": "point",
             "why": "a quiz: the teacher asks and waits"},
    "visual": {"show": True, "placement": "side", "behavior": "explaining", "expression": "engaged", "gesture": "open_hand",
               "why": "an explanation with a supporting visual: the teacher stands beside it"},
    "explanation": {"show": True, "placement": "side", "behavior": "explaining", "expression": "engaged", "gesture": "explaining",
                    "why": "an explanation: the teacher presents it"},
    "math": {"show": True, "placement": "pip", "behavior": "explaining", "expression": "engaged", "gesture": "point",
             "why": "mathematics: the equation gets the canvas, the teacher stays small"},
    "text_heavy": {"show": True, "placement": "pip", "behavior": "explaining", "expression": "neutral", "gesture": "none",
                   "why": "a lot of text on the board: the teacher stays small"},
    "code": {"show": False, "why": "code needs the whole canvas: the presenter steps away"},
    "technical": {"show": False, "why": "an animation or simulation needs the learner's full attention: the presenter steps away"},
    "video": {"show": False, "why": "the video is the visual: the presenter steps away"},
}
LESSON_BEHAVIOR = {"talking": "talking", "explaining": "explaining", "thinking": "thinking", "question": "listening", "success": "concluding"}

# The Visual Director's semantic presenter intent (Phase 15, visual_director.py): scene.visual_direction.presenter,
# version 1. Untrusted client data, used only when it matches that contract. Its role (size, placement) belongs to the
# scene composer (Phase 14) and is never read here; its interaction refines what the presenter does, in this module's own
# vocabulary, only where the Director itself chooses (the screenplay, the user's settings and Visual Review keep winning)
# and only with what the presenter can show. A semantic intent per scene: no timing, no speech synchronization.
DIRECTION_VERSION = 1
DIRECTION_ROLES = ("dominant", "secondary", "guide", "demonstrator", "hidden")
DIRECTION_INTERACTIONS = ("introduces", "explains", "points_to_visual", "points_to_board", "pauses_for_visual", "summarizes", "asks", "none")
INTERACTION_ACTING = {  # interaction: (behavior, expression, gesture, why); None keeps the Director's own value
    "introduces": ("welcoming", "happy", "welcome", "introduces the scene"),         # as an introduction (ROLE_RULES)
    "points_to_visual": (None, None, "point", "points to the visual"),               # the drawn teacher points inwards
    "points_to_board": (None, None, "point", "points to the board"),
    "pauses_for_visual": ("listening", None, "none", "pauses for the visual"),
    "summarizes": ("concluding", "encouraging", "open_hand", "sums up"),              # as a summary
    "asks": ("listening", "encouraging", None, "asks the learner"),                   # as a quiz: asks and waits
}  # "explains" (the Director's default) and "none": nothing changes


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def scene_role(scene, index, count):
    kind = str(scene.get("type") or "").lower()
    title = str(scene.get("title") or "").lower()
    html = scene.get("html") if isinstance(scene.get("html"), str) else ""
    panel = scene.get("side_panel") if isinstance(scene.get("side_panel"), dict) else {}
    if kind in ("quiz_checkpoint", "quiz"):
        return "quiz"
    if kind == "ai_video":
        return "video"
    if kind in ("simulation", "visual", "p5_simulation") or scene.get("manim_code") or panel.get("type") == "manim":
        return "technical"
    if re.search(r"\b(summary|recap|conclusion|key takeaways|wrap[- ]up)\b", title):
        return "summary"
    if index == 0 or kind == "title":
        return "intro"
    if re.search(r"<pre|<code", html) or panel.get("type") == "code" or scene.get("code"):
        return "code"
    if re.search(r"\\\(|\\\[|\$\$|class=\"?math|katex|mathjax", html, re.I) or panel.get("type") in ("equation", "math"):
        return "math"
    if panel or (isinstance(scene.get("visual_plan"), dict) and (scene["visual_plan"].get("side") if isinstance(scene["visual_plan"].get("side"), dict) else {}).get("media") not in (None, "NONE")):
        return "visual"
    if len(_plain(html)) > 600:
        return "text_heavy"
    return "explanation"


def _has_side_visual(scene):
    plan = (scene.get("visual_plan") or {}).get("side") if isinstance(scene.get("visual_plan"), dict) else None
    if isinstance(plan, dict):
        return plan.get("media") not in (None, "NONE") and plan.get("selection") != "removed"
    return isinstance(scene.get("side_panel"), dict)


def direction_interaction(scene):
    """The interaction the scene's visual direction asks of the presenter, or None: no direction, another version,
    anything outside the contract, or a hidden presenter role."""
    direction = scene.get("visual_direction")
    version = direction.get("version") if isinstance(direction, dict) else None
    presenter = direction.get("presenter") if version == DIRECTION_VERSION and not isinstance(version, bool) else None
    if not isinstance(presenter, dict) or not isinstance(presenter.get("role"), str) or presenter["role"] not in DIRECTION_ROLES \
            or presenter["role"] == "hidden":
        return None
    interaction = presenter.get("interaction")
    return interaction if isinstance(interaction, str) and interaction in DIRECTION_INTERACTIONS else None


def directed_acting(interaction, behavior, expression, gesture, caps):
    """(behavior, expression, gesture, why or None): the Director's choice refined by the visual direction's interaction.
    An expression or gesture the presenter cannot show is not chosen: it keeps the Director's own (unknown capabilities,
    as for an AI presenter without a provider, limit nothing, like everywhere in the plan)."""
    acting = INTERACTION_ACTING.get(interaction)
    if acting is None:
        return behavior, expression, gesture, None
    want_behavior, want_expression, want_gesture, why = acting
    can = lambda value, vocab: value is not None and (vocab is None or value in vocab)  # noqa: E731
    chosen = (want_behavior or behavior,
              want_expression if can(want_expression, caps.get("expressions")) else expression,
              want_gesture if can(want_gesture, caps.get("gestures")) else gesture)
    return (*chosen, why if chosen != (behavior, expression, gesture) else None)


def _lesson_position(scene):
    """The screenplay's own placement (the mascot's aadhi_position), as the plan fields."""
    pos = str(scene.get("aadhi_position") or "").lower()
    pos = {"popup": "popup_bottom_left", "none": "hidden"}.get(pos, pos)
    if scene.get("type") == "ai_video" and pos in ("", "hidden"):
        return "left", "background", "left"  # the mascot stays in the background around AI videos (as before)
    if not pos:
        needs = scene.get("type") in ("title", "content") and len(scene.get("html") or "") <= 300
        return ("left", "side", "left") if needs else ("hidden", None, "hidden")
    if pos.startswith("popup"):
        return ("left" if pos.endswith("left") else "right"), "pip", pos
    if pos in ("left", "right", "center"):
        return pos, "side", pos
    return "hidden", None, "hidden"


def plan_scene(scene, index, count, profile, settings=None):
    """The presenter plan for one scene (see the module docstring). Visual Review decisions come first."""
    settings = settings or {}
    mode = settings.get("mode") if settings.get("mode") in MODES else "lesson"
    style = settings.get("style") if settings.get("style") in STYLES else "friendly"
    default_side = settings.get("position") if settings.get("position") in ("left", "right") else profile["defaults"].get("position", "right")
    caps = profile.get("capabilities") or {}
    role = scene_role(scene, index, count)
    narration = re.sub(r"\[(?:PAUSE[^\]]*|SYNC)\]", " ", str(scene.get("narration") or ""))
    plan = {"presenter_id": profile["id"], "profile_version": profile["version"], "type": profile["type"], "role": role,
            "speech": True, "source": "director", "version": VERSION}

    if mode == "off":
        plan.update(enabled=False, position="hidden", placement=None, reason="presenter switched off")
    elif mode == "lesson":
        position, placement, layout = _lesson_position(scene)
        state = str(scene.get("mascot_state") or "").lower()
        if profile["type"] != "mascot" and layout.startswith("popup"):
            layout = position  # drawn / AI presenters: small in their own zone (the popup layouts have no free corner)
        plan.update(enabled=position != "hidden", position=position, placement=placement, source="lesson",
                    behavior=LESSON_BEHAVIOR.get(state, "explaining"), expression="friendly", gesture="none",
                    reason="as the lesson's screenplay places the presenter")
        plan["layout"] = layout
    else:
        rule = ROLE_RULES[role]
        show = rule["show"] or mode == "always"
        placement = rule.get("placement", "pip") if rule["show"] else "pip"
        behavior, expression, gesture = rule.get("behavior", "explaining"), rule.get("expression", "engaged"), rule.get("gesture", "none")
        # The scene's visual direction (Phase 15): what the presenter does, never whether or where it appears
        behavior, expression, gesture, directed = directed_acting(direction_interaction(scene) if show else None,
                                                                  behavior, expression, gesture, caps)
        if gesture in ("explaining", "open_hand") and re.search(r"\b(first|second|third|two|three|four|five|steps?|stages?|phases?)\b", narration, re.I):
            gesture = "counting"
        elif gesture in ("explaining", "open_hand") and _has_side_visual(scene) and re.search(r"\b(look at|here|this (diagram|picture|chart|figure)|shown)\b", narration, re.I):
            gesture = "point"
        expression = {"formal": {"happy": "friendly", "engaged": "serious", "encouraging": "friendly"}.get(expression, expression),
                      "energetic": {"engaged": "happy", "friendly": "happy"}.get(expression, expression),
                      "calm": {"happy": "friendly", "engaged": "friendly"}.get(expression, expression)}.get(style, expression)
        plan.update(enabled=show, position=default_side if show else "hidden", placement=placement if show else None,
                    behavior=behavior, expression=expression, gesture=gesture,
                    reason=rule["why"] if rule["show"] or mode != "always" else rule["why"].replace("steps away", "stays small (always shown)"))
        if directed:
            plan["reason"] += f"; visual direction: {directed}"
    # User choices for expression/gesture (Advanced settings) override the automatic ones
    if settings.get("expression") in EXPRESSIONS:
        plan["expression"] = settings["expression"]
    if settings.get("gesture") in GESTURES:
        plan["gesture"] = settings["gesture"]

    # Visual Review decisions for the presenter are authoritative
    review = (scene.get("visual_review") or {}).get("presenter") if isinstance(scene.get("visual_review"), dict) else None
    if isinstance(review, dict) and review.get("status") in ("approved", "changed", "removed"):
        plan["review_status"] = review["status"]
        if review["status"] == "removed":
            plan.update(enabled=False, position="hidden", placement=None, source="review", reason="removed in Visual Review")
        else:
            if review.get("position") in ("left", "right", "center"):
                plan.update(enabled=True, position=review["position"], placement="side", source="review")
            elif review.get("position") == "pip":
                plan.update(enabled=True, placement="pip", source="review")
            if review.get("asset_id"):
                plan["media_asset_id"] = review["asset_id"]
    else:
        plan["review_status"] = "pending"

    # Safe composition: never over the board, the side visual or the subtitles
    if plan.get("enabled"):
        layout = plan.get("layout") if plan.get("source") == "lesson" and plan.get("layout") else layout_for(plan["position"], plan.get("placement"))
        side = _has_side_visual(scene)
        if profile["type"] == "mascot" and layout.startswith("popup"):
            box, conflicts = None, []  # Aadhi's own popup clip (MascotController), as before
        else:
            box, conflicts = safe_box(layout, plan.get("placement"), side)
        if conflicts and plan.get("placement") == "pip":
            layout = layout_for(default_side, "side")
            box, conflicts = safe_box(layout, "side", side)
            plan.update(placement="side", position=default_side, composition_note="no room for the small presenter; moved to the side")
        if conflicts and plan.get("source") != "lesson":
            plan.update(enabled=False, position="hidden", placement=None, composition_note=f"would cover the {', '.join(conflicts)}; hidden")
            layout, box = "hidden", None
        plan["layout"] = layout if plan.get("enabled") else "hidden"
        plan["box"] = box if plan.get("enabled") else None
    else:
        plan["layout"], plan["box"] = "hidden", None
    # What the presenter can actually do here (expressions / gestures it lacks are shown as the nearest it has)
    for key, vocab, fallback, default in (("expression", caps.get("expressions"), EXPRESSION_FALLBACK, "neutral"),
                                          ("gesture", caps.get("gestures"), GESTURE_FALLBACK, "none")):
        if plan.get(key) and vocab is not None:
            shown, note = map_vocab(plan[key], vocab, fallback, default)
            if note:
                plan[f"{key}_shown"] = shown
                plan.setdefault("fallbacks", []).append(note)
            elif not vocab:
                plan[f"{key}_shown"] = None
    return plan


def plan_lesson(scenes, profile, settings=None):
    count = len(scenes)
    return [plan_scene(s if isinstance(s, dict) else {}, i, count, profile, settings) for i, s in enumerate(scenes)]


# ---- acting at a moment (Phase 16 synchronization) -------------------------------------------------------------------
# The plan above is per scene. The Synchronization Director (sync_director.py) can also ask the presenter to act at a
# moment of the narration; these say what each presenter really can do then, and nothing else.

SYNC_ACTIONS = ("introduce", "point", "explain", "pause", "summarize", "emphasis")
# Aadhi's narration states (mascot.js STATES; "question" and "success" are the quiz's own moments, never synchronized)
SYNC_MASCOT_STATES = ("talking", "explaining", "thinking", "idle")


def _interaction_acting(name):
    _behavior, expression, gesture, _why = INTERACTION_ACTING[name]
    return expression, gesture


ACTION_ACTING = {  # action: (expression, gesture, Aadhi's narration state); None: that part does not change
    "introduce": (*_interaction_acting("introduces"), "talking"),        # happy, welcome
    "point": (*_interaction_acting("points_to_visual"), None),           # point; Aadhi cannot point
    "explain": (ROLE_RULES["explanation"]["expression"], ROLE_RULES["explanation"]["gesture"], "explaining"),
    "pause": (*_interaction_acting("pauses_for_visual"), "thinking"),     # arms down; Aadhi thinks (as at a [PAUSE])
    "summarize": (*_interaction_acting("summarizes"), "explaining"),      # encouraging, open hand
    "emphasis": ("engaged", "emphasis", None),                            # a raised hand; Aadhi has no emphasis of his own
}


def sync_capabilities(presenter_type, profile=None):
    """What a presenter can do AT A MOMENT of the narration, for synchronization:
    {"acts": bool, "gestures": [...], "expressions": [...], "states": [...]} (vocabulary order, fresh lists).
      illustrated         acts: its gestures and expressions change in place mid-scene (presenter.js PresenterStage.act),
                          limited to the profile's capabilities (without a profile: the built-in Aadhi Teacher's)
      mascot              Aadhi acts only through his narration states (talking / explaining / thinking / idle, played
                          by MascotController): no gestures, and no expression of his own at a moment
      ai_avatar, custom   a pre-rendered clip cannot change mid-scene: acts False
      anything else       acts False
    presenter_type: the plan's / profile's type (None: the profile's own type). Whether the presenter appears in the
    scene at all (plan["enabled"], the composition) is the caller's to check."""
    kind = presenter_type if isinstance(presenter_type, str) else (profile.get("type") if isinstance(profile, dict) else None)
    if kind == "illustrated":
        caps = profile.get("capabilities") if isinstance(profile, dict) else None
        caps = caps if isinstance(caps, dict) else BUILTIN["aadhi-teacher"]["capabilities"]
        have = lambda key: caps.get(key) if isinstance(caps.get(key), (list, tuple)) else ()  # noqa: E731
        gestures = [g for g in GESTURES if g in have("gestures")]
        expressions = [x for x in EXPRESSIONS if x in have("expressions")]
        return {"acts": bool(gestures or expressions), "gestures": gestures, "expressions": expressions, "states": []}
    if kind == "mascot":
        return {"acts": True, "gestures": [], "expressions": [], "states": list(SYNC_MASCOT_STATES)}
    return {"acts": False, "gestures": [], "expressions": [], "states": []}


def acting_for(action, capabilities):
    """{"gesture": g, "expression": x, "state": s} for a semantic action (SYNC_ACTIONS) at a moment, with ONLY values in
    the capabilities (sync_capabilities); a part the presenter cannot show is None (it does not change). None when the
    presenter does not act, the action is unknown, or the presenter cannot do what defines it (its gesture, or for Aadhi
    his state): the caller then gives the visual the emphasis instead. Never a nearest stand-in: nothing is faked."""
    entry = ACTION_ACTING.get(action) if isinstance(action, str) else None
    caps = capabilities if isinstance(capabilities, dict) else {}
    if entry is None or caps.get("acts") is not True:
        return None
    expression, gesture, state = entry
    pick = lambda value, key: value if value is not None and isinstance(caps.get(key), (list, tuple)) and value in caps[key] else None  # noqa: E731
    acting = {"gesture": pick(gesture, "gestures"), "expression": pick(expression, "expressions"), "state": pick(state, "states")}
    return acting if acting["gesture"] is not None or acting["state"] is not None else None


# ---- speech timeline ---------------------------------------------------------------------------------------------

PAUSE = re.compile(r"\[PAUSE(?::(\d+(?:\.\d+)?))?\]", re.I)
FPS = 25


def parse_segments(text):
    """The narration split at [PAUSE] / [PAUSE:n] markers, exactly as the page does (parseNarrationSegments)."""
    tokens = PAUSE.split(str(text or ""))
    segments = []
    for i in range(0, len(tokens), 2):
        seg = (tokens[i] or "").strip()
        has_marker = i + 1 < len(tokens)
        raw = tokens[i + 1] if has_marker else None
        pause = (float(raw) if raw not in (None, "") else 1.5) if has_marker else 0.0
        if seg:
            segments.append({"text": seg, "pause_after": pause})
        elif segments:
            segments[-1]["pause_after"] += pause
    return segments


def loudness(path, fps=FPS):
    """The voice's loudness, fps values a second from 0 (silence) to 1 (loud speech)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, timeout=120).stdout
    window = 8000 // fps
    count = len(raw) // 2
    samples = memoryview(raw).cast("h") if count else []
    levels = []
    for start in range(0, count, window):
        chunk = samples[start:start + window]
        if not len(chunk):
            break
        rms = math.sqrt(sum(v * v for v in chunk) / len(chunk))
        db = 20 * math.log10(rms / 32768) if rms > 0 else -100
        levels.append(round(min(1.0, max(0.0, (db + 50) / 35)), 2))
    return levels


def media_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


async def speech_timeline(text, voice, engine, tts, path_of):
    """The scene's speech as the page will play it: each segment's audio (the same /generate-audio files,
    so preview, export and the presenter share one audio pipeline), when it starts, how long it lasts, the
    pauses, and the loudness envelope. audio_sha256 identifies the speech for the presenter cache."""
    segments = parse_segments(text)
    if not segments:
        raise ValueError("the scene has no narration")
    out, envelope, digests, t = [], [], [], 0.0
    for seg in segments:
        clean = seg["text"].replace("[SYNC]", "").strip()
        result = await tts(clean, voice, engine)
        path = path_of(result["audio_url"])
        duration, levels = await asyncio.to_thread(lambda: (media_seconds(path), loudness(path)))
        out.append({"text": clean, "audio_url": result["audio_url"].split("?", 1)[0], "start": round(t, 3), "duration": round(duration, 3),
                    "pause_after": seg["pause_after"], "envelope_start": len(envelope)})
        envelope.extend(levels[:max(1, int(round(duration * FPS)))])
        envelope.extend([0.0] * int(round(seg["pause_after"] * FPS)))
        t += duration + seg["pause_after"]
        digests.append(f"{_sha(path)}:{duration:.3f}:{seg['pause_after']}")
    return {"version": 1, "fps": FPS, "voice": voice, "engine": engine, "segments": out, "duration": round(t, 3),
            "envelope": envelope, "audio_sha256": hashlib.sha256("|".join(digests).encode()).hexdigest()}


# ---- presenter clip requests ---------------------------------------------------------------------------------------

def presenter_request(profile, plan, timeline, scene_text, *, provider=None, allow_fallback=None, model=None, force=False,
                      background="scene", project_id=None, scene_index=None):
    """The normalized presenter request for the AI media layer (no provider-specific field)."""
    return MediaRequest(media_type=PRESENTER, prompt=re.sub(r"\[(?:PAUSE[^\]]*|SYNC)\]", " ", scene_text or "").strip() or profile["name"],
                        provider=provider, allow_fallback=allow_fallback, model=model, duration_seconds=max(0.5, timeline["duration"]),
                        force_regenerate=force, purpose="presenter", project_id=project_id, scene_index=scene_index, slot="presenter",
                        presenter={"presenter_id": profile["id"], "profile_version": profile["version"], "name": profile["name"],
                                   "appearance": profile.get("appearance"), "appearance_hash": appearance_hash(profile),
                                   "reference_asset_ids": [profile["reference_asset_id"]] if profile.get("reference_asset_id") else [],
                                   "behavior": plan.get("behavior") or "explaining", "expression": plan.get("expression") or "engaged",
                                   "gesture": plan.get("gesture") or "none", "background": background if background in BACKGROUNDS else "scene",
                                   "speech": {"audio_sha256": timeline["audio_sha256"], "duration": timeline["duration"],
                                              "fps": timeline["fps"], "envelope": timeline["envelope"],
                                              "segments": [{k: s[k] for k in ("start", "duration", "pause_after")} for s in timeline["segments"]]}})


def presenter_still_wanted(db, run):
    """A presenter clip continued without the user (recovery) is not made for a scene whose presenter was
    removed in Visual Review, or a lesson / scene that no longer exists. Phase 20: the scene is found by its id when
    the run recorded one (visuals.scene_for_run)."""
    request = run_request(run)
    if run.project_id is None or (run.scene_index is None and not request.get("scene_id")):
        return True, ""
    project = db.query(models.Project).filter(models.Project.id == run.project_id, models.Project.user_id == run.user_id).first()
    if project is None:
        return False, "The lesson this was for no longer exists, so it was not generated."
    scenes = json.loads(project.json_data or "{}").get("scenes") or []
    index = scene_for_run(scenes, request)
    if index is None:
        return False, "The scene this was for no longer exists, so it was not generated."
    review = (scenes[index].get("visual_review") or {}).get("presenter") or {}
    if review.get("status") == "removed":
        return False, "The presenter was removed from this scene in Visual Review, so it was not generated."
    return True, ""


def store_generated_clip(db, run, library, link):
    """A finished presenter clip for a scene of a saved lesson is kept in that scene's presenter plan (unless the
    user removed the presenter there, chose another clip, or it is a New Version the user applies themself).
    Phase 20: written with editor_api.update_lesson (a save committed meanwhile is kept; the checks above are made on
    the lesson as it is then) on the scene found by its id when the run recorded one (visuals.scene_for_run)."""
    request = run_request(run)
    if run.project_id is None or (run.scene_index is None and not request.get("scene_id")) or run.forced:
        return
    project = db.query(models.Project).filter(models.Project.id == run.project_id, models.Project.user_id == run.user_id).first()
    asset = db.get(models.Asset, run.asset_id) if run.asset_id else None
    if project is None or asset is None:
        return
    media = clip_media(asset, run, link)

    def apply(payload):
        scenes = payload.get("scenes") or []
        index = scene_for_run(scenes, request)
        if index is None:
            return False
        scene = scenes[index]
        if not request.get("scene_id") and isinstance(scene.get("scene_id"), str) and scene["scene_id"]:
            request["scene_id"] = scene["scene_id"]  # a retry finds this same scene, even if it was moved meanwhile
        review = (scene.get("visual_review") or {}).get("presenter") or {}
        plan = scene.get("presenter_plan") if isinstance(scene.get("presenter_plan"), dict) else {}
        if review.get("status") in ("removed", "changed") or (plan.get("media") or {}).get("asset_id"):
            return False
        plan["media"] = dict(media)
        scene["presenter_plan"] = plan
        return True

    if editor_api.update_lesson(db, project, apply) is not None:
        library.record_project_references(db, project, run.user_id)


def clip_media(asset, run=None, link=None):
    details = json.loads(asset.details or "{}")
    presenter = details.get("presenter") or {}
    gen = details.get("generation") or {}
    return {"asset_id": asset.id, "url": link(asset) if link else None, "kind": asset.kind, "provider": gen.get("provider"),
            "model": gen.get("model"), "lip_sync": bool(presenter.get("lip_sync")), "duration": asset.duration_seconds,
            "presenter_id": presenter.get("presenter_id"), "run_id": run.id if run else gen.get("run_id")}


# ---- API -----------------------------------------------------------------------------------------------------------

class PresenterSettings(BaseModel):
    presenter_id: str = DEFAULT_PRESENTER
    mode: str = "lesson"
    position: str = "right"
    style: str = "friendly"
    expression: str | None = None
    gesture: str | None = None
    provider: str | None = None
    background: str = "scene"


class PlanIn(BaseModel):
    scenes: list
    settings: PresenterSettings = PresenterSettings()


class SpeechIn(BaseModel):
    text: str
    voice: str = "en-US-GuyNeural"
    tts_engine: str = "default"


class GenerateIn(BaseModel):
    scene: dict
    scene_index: int | None = None
    project_id: int | None = None
    settings: PresenterSettings = PresenterSettings()
    voice: str = "en-US-GuyNeural"
    tts_engine: str = "default"
    provider: str | None = None
    allow_fallback: bool | None = None
    model: str | None = None
    force_regenerate: bool = False
    wait: bool = True


class ReviewIn(BaseModel):
    project_id: int
    scene_index: int
    action: str  # keep | choose | remove | reset | move
    asset_id: str | None = None
    position: str | None = None  # left | right | center | pip
    scene: dict | None = None
    settings: PresenterSettings = PresenterSettings()


class ProfileIn(BaseModel):
    name: str
    description: str = ""
    palette: list = []
    reference_asset_id: str | None = None


def check_settings(settings):
    """Presenter settings from the page, refused when they name something that does not exist."""
    problems = []
    if settings.mode not in MODES:
        problems.append(f"mode must be one of {', '.join(MODES)}")
    if settings.style not in STYLES:
        problems.append(f"style must be one of {', '.join(STYLES)}")
    if settings.position not in ("left", "right"):
        problems.append("position must be left or right")
    if settings.expression is not None and settings.expression not in EXPRESSIONS:
        problems.append("unknown expression")
    if settings.gesture is not None and settings.gesture not in GESTURES:
        problems.append("unknown gesture")
    if settings.background not in BACKGROUNDS:
        problems.append("unknown background")
    if settings.provider is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", settings.provider):
        problems.append("provider must be a provider name")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems) + ".")
    return settings


def create_presenters_router(*, get_current_user, registry, ai_media, library, tts, path_of, link_for, answer_for):
    router = APIRouter(prefix="/api/presenters", tags=["presenters"])

    def profile_or_404(db, user, presenter_id):
        profile = get_profile(db, user.id, presenter_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Presenter not found.")
        return profile

    def resolved(db, user, profile):
        """A profile with its capabilities on this server (AI profiles: from the presenter provider)."""
        return {**profile, "capabilities": profile_view(profile, registry)["capabilities"]}

    @router.get("")
    def presenters(current_user=Depends(get_current_user), db=Depends(get_db)):
        """The presenters a user can choose (built-in and their own), what each can do here, the presenter
        providers (and why other providers cannot present), and the vocabularies."""
        own = db.query(models.PresenterProfile).filter(models.PresenterProfile.owner_id == current_user.id).order_by(models.PresenterProfile.created_at).all()
        profiles = [profile_view(p, registry) for p in list(BUILTIN.values()) + [custom_profile(r) for r in own]]
        return {"profiles": profiles, "default": DEFAULT_PRESENTER, "providers": provider_view(registry),
                "vocabulary": {"behaviors": BEHAVIORS, "expressions": EXPRESSIONS, "gestures": GESTURES, "positions": POSITIONS,
                               "placements": PLACEMENTS, "modes": MODES, "styles": STYLES, "backgrounds": BACKGROUNDS}}

    @router.post("/profiles")
    def create_profile(body: ProfileIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """A custom presenter: a name, how they look, and optionally a reference picture from the user's library
        (used only by presenter providers that accept one; no avatar is trained here)."""
        name = re.sub(r"\s+", " ", body.name or "").strip()[:80]
        if not name:
            raise HTTPException(status_code=422, detail="A presenter needs a name.")
        if body.reference_asset_id is not None:
            asset = library.accessible(db, body.reference_asset_id, current_user.id)
            if asset is None or asset.kind != "image" or asset.owner_id != current_user.id:
                raise HTTPException(status_code=422, detail="The reference must be one of your own pictures.")
        palette = [c for c in body.palette if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)][:6]
        row = models.PresenterProfile(id=uuid.uuid4().hex, owner_id=current_user.id, name=name, type="custom",
                                      appearance=json.dumps({"description": (body.description or "")[:500], "palette": palette}),
                                      reference_asset_id=body.reference_asset_id, version=1)
        db.add(row)
        db.commit()
        return profile_view(custom_profile(row), registry)

    @router.delete("/profiles/{profile_id}")
    def delete_profile(profile_id: str, current_user=Depends(get_current_user), db=Depends(get_db)):
        row = db.get(models.PresenterProfile, profile_id) if re.fullmatch(r"[0-9a-f]{32}", profile_id or "") else None
        if row is None or row.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Presenter not found.")
        db.delete(row)
        db.commit()
        return {"deleted": profile_id}

    @router.post("/plan")
    def plan(body: PlanIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The Presenter Director's plan for every scene (never generates anything)."""
        if len(body.scenes) > 200:
            raise HTTPException(status_code=413, detail="A lesson can have at most 200 scenes.")
        check_settings(body.settings)
        profile = resolved(db, current_user, profile_or_404(db, current_user, body.settings.presenter_id))
        plans = plan_lesson(body.scenes, profile, body.settings.model_dump())
        for p in plans:  # a clip or picture chosen in Visual Review: where the page loads it from
            asset = library.accessible(db, p["media_asset_id"], current_user.id) if p.get("media_asset_id") else None
            if asset is not None and asset.status == "ready":
                p["media"] = clip_media(asset, link=link_for(current_user))
        return {"presenter": profile_view(profile_or_404(db, current_user, body.settings.presenter_id), registry), "plans": plans}

    @router.post("/speech")
    async def speech(body: SpeechIn, current_user=Depends(get_current_user)):
        """The scene's speech timeline (segments, timing, loudness envelope) from the lesson's own TTS."""
        if not body.text.strip():
            raise HTTPException(status_code=422, detail="The scene has no narration.")
        if len(body.text) > 20000:
            raise HTTPException(status_code=413, detail="The narration is too long.")
        try:
            return await speech_timeline(body.text, body.voice, body.tts_engine, lambda t, v, e: tts(t, v, e, current_user), path_of)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @router.post("/generate")
    async def generate(body: GenerateIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """A presenter clip for one scene (AI presenter profiles only). Cached, durable and recoverable like any
        AI media; explicit only (opening the review or playing never generates). Without a presenter-capable
        provider the answer says so: nothing is faked."""
        check_settings(body.settings)
        profile = profile_or_404(db, current_user, body.settings.presenter_id)
        if profile["type"] not in ("ai_avatar", "custom"):
            raise HTTPException(status_code=422, detail=f"{profile['name']} is drawn by the page; there is nothing to generate.")
        if body.provider is not None and not re.fullmatch(r"[a-z0-9-]{1,40}", body.provider):
            raise HTTPException(status_code=422, detail="provider must be a provider name.")
        narration = str(body.scene.get("narration") or "").strip()
        if not narration:
            raise HTTPException(status_code=422, detail="The scene has no narration for the presenter to speak.")
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == current_user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        view = provider_view(registry)
        if not view["available"] and body.provider is None:
            raise HTTPException(status_code=503, detail=view["message"])
        plan_ = plan_scene(body.scene, body.scene_index or 0, (body.scene_index or 0) + 1, resolved(db, current_user, profile),
                           {**body.settings.model_dump(), "mode": "always" if body.settings.mode in ("lesson", "off") else body.settings.mode})
        try:
            timeline = await speech_timeline(narration, body.voice, body.tts_engine, lambda t, v, e: tts(t, v, e, current_user), path_of)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        request = presenter_request(profile, plan_, timeline, narration, provider=body.provider or body.settings.provider,
                                    allow_fallback=body.allow_fallback, model=body.model, force=body.force_regenerate,
                                    background=body.settings.background, project_id=body.project_id, scene_index=body.scene_index)
        return await answer_for(db, current_user, request, body.wait)

    @router.post("/review")
    def review(body: ReviewIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """A Visual Review decision for a scene's presenter (approve, choose another clip or picture, remove,
        move, reset), kept in the saved lesson (scene.visual_review.presenter) and applied first by the Director.
        Never generates anything."""
        check_settings(body.settings)
        if body.action not in ("keep", "choose", "remove", "reset", "move"):
            raise HTTPException(status_code=422, detail="action must be keep, choose, remove, move or reset.")
        if body.position is not None and body.position not in ("left", "right", "center", "pip"):
            raise HTTPException(status_code=422, detail="position must be left, right, center or pip.")
        project = db.query(models.Project).filter(models.Project.id == body.project_id, models.Project.user_id == current_user.id).first()
        if project is None:
            raise HTTPException(status_code=404, detail="Lesson not found.")
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
            decision = {"reviewed_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}
            current = reviews.get("presenter") if isinstance(reviews.get("presenter"), dict) else {}
            if body.action == "remove":
                decision["status"] = "removed"
            elif body.action in ("keep", "choose", "move"):
                decision["status"] = "changed" if body.action in ("choose", "move") else "approved"
                if body.position:
                    decision["position"] = body.position
                elif current.get("position") and body.action != "choose":
                    decision["position"] = current["position"]
                asset_id = body.asset_id or (current.get("asset_id") if body.action == "move" else None)
                if body.action == "choose" and not asset_id:
                    raise HTTPException(status_code=422, detail="asset_id is required to choose a presenter clip or picture.")
                if body.action == "keep" and not asset_id:
                    asset_id = ((scene.get("presenter_plan") or {}).get("media") or {}).get("asset_id")
                if asset_id:
                    asset = library.accessible(db, asset_id, current_user.id)
                    if asset is None:
                        raise HTTPException(status_code=404, detail="Asset not found.")
                    if asset.status != "ready" or not library.file_exists(asset) or asset.kind not in ("video", "image"):
                        raise HTTPException(status_code=422, detail="Choose a presenter clip (video) or a presenter picture.")
                    decision["asset_id"] = asset.id
            if body.action == "reset":
                reviews.pop("presenter", None)
            else:
                reviews["presenter"] = decision
            if reviews:
                scene["visual_review"] = reviews
            else:
                scene.pop("visual_review", None)
            profile = resolved(db, current_user, profile_or_404(db, current_user, body.settings.presenter_id))
            new_plan = plan_scene(scene, body.scene_index, len(scenes), profile, body.settings.model_dump())
            old_media = (scene.get("presenter_plan") or {}).get("media") if isinstance(scene.get("presenter_plan"), dict) else None
            chosen = new_plan.get("media_asset_id")
            if chosen:
                new_plan["media"] = clip_media(library.accessible(db, chosen, current_user.id), link=link_for(current_user))
            elif old_media and body.action != "remove":
                new_plan["media"] = old_media
            scene["presenter_plan"] = new_plan
            out.update(review=reviews.get("presenter"), plan=new_plan)
            return True

        revision = editor_api.update_lesson(db, project, apply)
        library.record_project_references(db, project, current_user.id)
        return {"review": out["review"], "plan": out["plan"], "revision": revision}

    return router
