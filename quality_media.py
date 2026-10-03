"""Phase 18 — the presenter and media checks of the Quality & Consistency Engine (quality.py owns the contract).

Presenter (dimension "presenter"; deterministic, from the plans the lesson plays)
  Who presents each scene is read as the page plays it: the composition's presenter (cinematic_plan.presenter and its
  presenter layer), else the Phase 12 plan the page uses (cinematic.presenter_context: the presenter plan when the lesson
  is not "Aadhi as the lesson says" and the plan is for the lesson's presenter; otherwise Aadhi placed by the screenplay).
  It is compared with the lesson's presenter (settings.presenter_id; Aadhi in the legacy mode). Identity is the user's
  choice, so a different presenter is reported with where to look, never repaired; a presenter clip chosen in Visual
  Review is the user's choice (info). Sides and sizes are compared between consecutive scenes that show the presenter;
  a difference that a Visual Review decision, the screenplay (scene.composition, aadhi_position) or the presenter
  plan's own source explains is not a problem. Aadhi (the mascot) is filmed in his studio; an AI presenter needs its
  clip; a composition that shows a presenter the presenter plan took out is out of date (re-plan).

Media (dimension "media"; only when the server looked the lesson's files up, lesson.media = load_media(...))
  Every visual a scene shows (visual_plan main/side, not removed in Visual Review) is checked against its library asset
  (missing, deleted, not the user's, file gone: the export would show an empty panel), its kind for the slot (the main
  visual is a video, a side picture an image, a side clip a video, as visuals.py routes them), an empty file, a shape
  clearly wrong for a 16:9 place, an AI asset without provenance, a visual still waiting for generation (or whose last
  generation failed). The presenter clip and the composition's background picture are checked the same way. AI
  visuals made by several generators are recorded (info): no lesson contract asks for one. Never a regeneration.

load_media(db, user_id, scenes, project_id): what the media checks read, in two read-only queries (the referenced
assets, the lesson's AI runs). Nothing is marked, generated, resolved to a URL or committed.
"""
import json
import os
import re
import sys
from collections import Counter

import cinematic as C
import presenters as P
import quality as Q

ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_IDS = 900                 # one IN (...) query, below SQLite's oldest bound on parameters
VISUAL_SLOTS = ("main", "side")
AI_ASSET_SOURCES = ("ai-image", "ai-video", "ai-presenter")
AI_PLAN_SOURCES = ("AI_IMAGE", "AI_VIDEO")
AI_PRESENTERS = ("ai_avatar", "custom")
RUN_KINDS = (None, "ai_media", "manim")  # the media executors (document analyses are not lesson media)
RUN_PROBLEMS = ("failed", "needs_attention")
RUN_ACTIVE = ("queued", "running", "recovering", "cancel_requested")
WIDE = 16 / 9                 # the main visual's place and the background fill the 16:9 frame
SHAPE_TOLERANCE = 0.25        # a shape more than 25 % off the place's is "clearly wrong"
SCALE_TOLERANCE = 0.25        # the presenter's box height changing more than 25 % between scenes with the same role
SIDES = ("left", "right")
WHERE = {"left": "on the left", "right": "on the right", "center": "in the centre", "pip": "small, in the corner",
         "hidden": "out of the scene"}
SIZES = {"dominant": "large", "small": "small", "hidden": "out of the scene"}
KIND_WORD = {"image": "a picture", "video": "a video", "audio": "a sound file"}


# ---- small helpers -----------------------------------------------------------------------------------------------------

def _guard(make, *args):
    """A check that cannot decide stays silent (never raises)."""
    try:
        return make(*args) or []
    except Exception:  # noqa: BLE001
        return []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _str(value, limit=64):
    return value[:limit] if isinstance(value, str) and value else None


def _asset_id(value):
    return value if isinstance(value, str) and ASSET_ID.match(value) else None


def _replan(scenes, label="Re-plan the scene"):
    return Q.repair("auto", "presentation", {"type": "replan", "scenes": sorted(set(scenes))}, label)


def _name(presenter_id):
    """A presenter's name for an educator (the built-in profiles' own names)."""
    profile = P.BUILTIN.get(presenter_id) if isinstance(presenter_id, str) else None
    if profile:
        return profile["name"]
    return "your own presenter" if isinstance(presenter_id, str) and ASSET_ID.match(presenter_id) else "another presenter"


def _type_of(presenter_id):
    profile = P.BUILTIN.get(presenter_id) if isinstance(presenter_id, str) else None
    if profile:
        return profile["type"]
    return "custom" if isinstance(presenter_id, str) and ASSET_ID.match(presenter_id) else None


def _legacy(settings):
    return settings.get("presenter_legacy", True) is not False


def _expected(settings):
    """The lesson's presenter: Aadhi as the lesson says (legacy), else the presenter the lesson chose."""
    if _legacy(settings):
        return P.DEFAULT_PRESENTER
    pid = settings.get("presenter_id")
    return pid if isinstance(pid, str) and pid else P.DEFAULT_PRESENTER


def _usual_default(settings):
    side = settings.get("presenter_position")
    return side if side in SIDES else "right"


# ---- who presents a scene, as the lesson plays it -------------------------------------------------------------------------

def _ctx(lesson, i):
    """The Phase 12 presenter as the composer and the page read it (cinematic.presenter_context)."""
    return lesson._get(("quality_media.ctx", i), lambda: C.presenter_context(lesson.scene(i), lesson.settings)) or {}


def _effective(lesson, i):
    """Whether the page uses scene i's presenter plan (presenter.js effective / cinematic.presenter_context)."""
    plan = lesson.presenter_plan(i)
    s = lesson.settings
    return bool(plan) and not _legacy(s) and plan.get("presenter_id") == s.get("presenter_id")


def _presence(lesson, i):
    """{who, type, shown, side, placement, box, planned} of scene i's presenter: the composition's when the scene has one,
    else the presenter plan the page uses (Aadhi placed by the screenplay otherwise)."""
    def make():
        plan = lesson.plan(i)
        shown_plan = plan.get("presenter") if isinstance(plan.get("presenter"), dict) else None
        if shown_plan is not None:
            layer = next((l for l in plan.get("layers") or [] if isinstance(l, dict) and l.get("type") == "presenter"), None)
            box = _dict(layer).get("box") if layer else None
            box = box if C.valid_box(box or {}) else None
            return {"who": _str(shown_plan.get("presenter_id")), "type": _str(shown_plan.get("type")),
                    "shown": shown_plan.get("shown") is True, "side": shown_plan.get("side"),
                    "placement": _dict(layer).get("placement"), "box": box, "planned": True}
        ctx = _ctx(lesson, i)
        return {"who": _str(ctx.get("presenter_id")), "type": _str(ctx.get("type")), "shown": bool(ctx.get("enabled")),
                "side": ctx.get("side"), "placement": ctx.get("placement"), "box": None, "planned": False}
    return lesson._get(("quality_media.presence", i), make) or {"who": None, "type": None, "shown": False, "side": None,
                                                                  "placement": None, "box": None, "planned": False}


def _presenter_review(lesson, i):
    review = lesson.reviews(i).get("presenter")
    return review if isinstance(review, dict) else {}


def _side_choice(lesson, i):
    """Who chose scene i's presenter side: 'review', 'screenplay', or None (the planning's own choice)."""
    scene = lesson.scene(i)
    review = _presenter_review(lesson, i)
    if review.get("status") in ("approved", "changed") and review.get("position") in ("left", "right", "center"):
        return "review"
    if C.review_overrides(scene).get("presenter_position") in SIDES:
        return "review"
    explicit, _warnings = C.clean_composition(scene.get("composition"))
    if explicit.get("presenter_position") in SIDES:
        return "screenplay"
    if not _effective(lesson, i):
        return "screenplay"  # Aadhi where the screenplay places him (presenter_context's lesson branch)
    source = lesson.presenter_plan(i).get("source")
    if source == "review":
        return "review"
    if source == "lesson":
        return "screenplay"  # "As the lesson says": the screenplay's placement
    return None


def _latest_runs(lesson):
    """{(scene_index, slot): run} — the last run of each scene slot (load_media keeps only the latest)."""
    media = lesson.media if isinstance(lesson.media, dict) else {}
    out = {}
    for run in media.get("runs") or []:
        if isinstance(run, dict):
            out[(run.get("scene_index"), run.get("slot"))] = run
    return out


# ---- presenter: one scene ---------------------------------------------------------------------------------------------------

def _presenter_scene(lesson, i):
    out, infos = [], []
    label, settings = lesson.label(i), lesson.settings
    expected = _expected(settings)
    pres = _presence(lesson, i)
    ctx = _ctx(lesson, i)
    pplan = lesson.presenter_plan(i)
    effective = _effective(lesson, i)
    review = _presenter_review(lesson, i)
    plan = lesson.plan(i)

    # A composition that shows a presenter the presenter plan does not (taken out, or another presenter): out of date
    if pres["planned"] and pres["shown"]:
        if not ctx.get("enabled"):
            out.append(Q.issue("presenter.plan_mismatch", "presenter", "error",
                               f"{label} shows the presenter, but the presenter was taken out of this scene"
                               + (" in Visual Review" if review.get("status") == "removed" else "")
                               + ": its composition is out of date.",
                               scene=i, element="presenter", evidence={"key": "disabled", "found": pres["who"]},
                               repair=_replan([i])))
        elif ctx.get("presenter_id") != pres["who"] or (ctx.get("type") and ctx.get("type") != pres["type"]):
            out.append(Q.issue("presenter.plan_mismatch", "presenter", "error",
                               f"{label} was composed for {_name(pres['who'])}, but its presenter is now {_name(ctx.get('presenter_id'))}: "
                               "its composition is out of date.",
                               scene=i, element="presenter",
                               evidence={"key": "other_presenter", "expected": ctx.get("presenter_id"), "found": pres["who"]},
                               repair=_replan([i])))
    if effective and review.get("status") == "removed" and pplan.get("enabled") and pres["shown"]:
        out.append(Q.issue("presenter.plan_mismatch", "presenter", "error",
                           f"The presenter was removed from {label} in Visual Review, but the scene still shows them. "
                           "Open the scene's presenter in Visual Review to apply the decision again.",
                           scene=i, element="presenter", evidence={"key": "removed_in_review", "found": pres["who"]}))

    # Who presents, against the lesson's presenter (identity is the user's choice: no repair, the message tells where).
    # A composition out of date with its presenter plan is re-planned (above); who presents is then the plan's presenter.
    if pres["planned"] and pres["shown"]:
        who = _str(ctx.get("presenter_id")) if ctx.get("enabled") else None
    else:
        who = pres["who"] if pres["shown"] else None
    if who and who != expected:
        if not _legacy(settings) and not pplan:
            why, key = "the scene has no presenter plan yet, so Aadhi stands in", "no_plan"
        elif not _legacy(settings) and pplan.get("presenter_id") != settings.get("presenter_id"):
            why, key = f"its presenter plan was made for {_name(pplan.get('presenter_id'))}", "plan_for_other"
        else:
            why, key = "its composition was made with other presenter settings", "plan"
        out.append(Q.issue("presenter.unexpected_switch", "presenter", "error",
                           f"{label} is presented by {_name(who)} instead of the lesson's presenter, {_name(expected)}: "
                           f"{why}. Check the presenter settings, then the scene's presenter in Visual Review.",
                           scene=i, element="presenter", evidence={"key": key, "expected": expected, "found": who}))

    # The clip the scene plays shows another presenter: a choice in Visual Review, or an unexpected switch
    media = _dict(pplan.get("media")) if effective else {}
    clip_presenter = _str(media.get("presenter_id"))
    if pres["shown"] and clip_presenter and clip_presenter != pplan.get("presenter_id"):
        if review.get("status") in ("approved", "changed") and review.get("asset_id") and review.get("asset_id") == media.get("asset_id"):
            infos.append(Q.issue("presenter.intentional", "presenter", "info",
                                 f"The presenter clip chosen for {label} in Visual Review shows {_name(clip_presenter)} (your choice).",
                                 scene=i, element="presenter", evidence={"key": "chosen_clip", "found": clip_presenter}))
        else:
            out.append(Q.issue("presenter.unexpected_switch", "presenter", "error",
                               f"The presenter clip in {label} shows {_name(clip_presenter)}, not the lesson's presenter, "
                               f"{_name(pplan.get('presenter_id'))}. Check the scene's presenter clip in Visual Review.",
                               scene=i, element="presenter clip",
                               evidence={"key": "clip", "expected": pplan.get("presenter_id"), "found": clip_presenter}))

    # Aadhi (the mascot) is filmed in his studio: another background behind him breaks the composition's own rule
    if pres["planned"] and pres["shown"] and pres["type"] == "mascot":
        background = _dict(plan.get("background")).get("type")
        if background != "studio":
            out.append(Q.issue("presenter.mascot_background", "presenter", "error",
                               f"Aadhi presents {label} in front of a background other than his studio (he is filmed there).",
                               scene=i, element="background", evidence={"key": "background", "expected": "studio", "found": background},
                               repair=_replan([i])))

    # An AI presenter without its clip plays nothing in its place
    if effective and pplan.get("enabled") and pplan.get("type") in AI_PRESENTERS and not _asset_id(media.get("asset_id")):
        run = _latest_runs(lesson).get((i, "presenter")) if isinstance(lesson.media, dict) else None
        status = _dict(run).get("status")
        failed = status in RUN_PROBLEMS
        out.append(Q.issue("presenter.no_clip", "presenter", "warning",
                           f"{label} has no presenter clip yet"
                           + (" (the last attempt to make it did not finish)" if failed else "")
                           + ", so it plays without its presenter. Visual Review shows the presenter's options.",
                           scene=i, element="presenter clip", evidence={"key": "no_clip", "found": status or "none"}))

    # Intentional variations (at most one note per scene)
    infos += _intentional(lesson, i, effective, review, pres)
    return out + infos[:1]


def _intentional(lesson, i, effective, review, pres):
    """The user's or the screenplay's presenter choices that make this scene differ (info, worth recording)."""
    scene, label = lesson.scene(i), lesson.label(i)
    default = _usual_default(lesson.settings)
    found = []
    if effective and review.get("status") == "removed" and not pres["shown"]:
        found.append(("removed", None, f"The presenter was taken out of {label} in Visual Review (your choice)."))
    elif effective and review.get("status") in ("approved", "changed") and review.get("position") in ("center", "pip") + SIDES \
            and review.get("position") != default:
        found.append(("moved", review["position"], f"The presenter of {label} was placed {WHERE[review['position']]} in Visual Review (your choice)."))
    overrides = C.review_overrides(scene)
    if overrides.get("presenter_size") in SIZES:
        found.append(("review_size", overrides["presenter_size"],
                      f"{label} shows the presenter {SIZES[overrides['presenter_size']]}, as chosen in Visual Review."))
    elif overrides.get("presenter_position") == "hidden" or overrides.get("presenter_position") in SIDES and overrides["presenter_position"] != default:
        found.append(("review_position", overrides["presenter_position"],
                      f"{label} shows the presenter {WHERE[overrides['presenter_position']]}, as chosen in Visual Review."))
    explicit, _warnings = C.clean_composition(scene.get("composition"))
    position = explicit.get("presenter_position")
    if position == "hidden" or position in SIDES and position != default:
        found.append(("screenplay_position", position, f"The screenplay places the presenter of {label} {WHERE[position]}."))
    return [Q.issue("presenter.intentional", "presenter", "info", message, scene=i, element="presenter",
                    evidence={"key": key, "value": value}) for key, value, message in found[:1]]


# ---- presenter: across scenes ---------------------------------------------------------------------------------------------

def _shown(lesson):
    return [(i, p) for i in range(lesson.count) for p in (_presence(lesson, i),) if p["shown"]]


def _usual_side(lesson, shown):
    counts = Counter(p["side"] for _i, p in shown if p["side"] in SIDES)
    default = _usual_default(lesson.settings)
    return max(SIDES, key=lambda s: (counts[s], s == default))


def _side_flips(lesson):
    shown = [(i, p) for i, p in _shown(lesson) if p["side"] in ("left", "right", "center")]
    if len(shown) < 2:
        return []
    usual = _usual_side(lesson, shown)
    out, flagged = [], set()
    for (a, pa), (b, pb) in zip(shown, shown[1:]):
        if pa["side"] == pb["side"]:
            continue
        for j, pj in ((a, pa), (b, pb)):
            if pj["side"] == usual or j in flagged or _side_choice(lesson, j) is not None:
                continue
            flagged.add(j)
            out.append(Q.issue("presenter.side_flip", "presenter", "notice",
                               f"The presenter changes sides in {lesson.label(j)}: the lesson usually shows them {WHERE[usual]}, "
                               "and no choice in Visual Review or the screenplay asks for the change.",
                               scene=j, element="presenter", evidence={"key": "side", "expected": usual, "found": pj["side"]},
                               repair=Q.repair("suggest", "composition",
                                               {"type": "composition", "scene": j, "overrides": {"presenter_position": usual}},
                                               f"Keep the presenter {WHERE[usual]}")))
    return out


def _scale_jumps(lesson):
    out, previous = [], None
    for i, pres in _shown(lesson):
        if not pres["box"]:
            continue
        if previous is not None:
            j, prev = previous
            if prev["type"] == pres["type"] and prev["placement"] == pres["placement"]:
                h0, h1 = float(prev["box"]["h"]), float(pres["box"]["h"])
                if max(h0, h1) > 0 and abs(h1 - h0) / max(h0, h1) > SCALE_TOLERANCE:
                    out.append(Q.issue("presenter.scale_jump", "presenter", "notice",
                                       f"The presenter in {lesson.label(i)} is {'larger' if h1 > h0 else 'smaller'} than in the scene "
                                       "before, although both scenes show them the same way.",
                                       scene=i, element="presenter",
                                       evidence={"key": "box_height", "expected": round(h0, 3), "found": round(h1, 3),
                                                 "value": pres["placement"]}))  # no repair: a re-plan gives the same sizes
        previous = (i, pres)
    return out


# ---- media: one scene -------------------------------------------------------------------------------------------------------

def _problem(entry):
    """What keeps a looked-up asset from being shown (None: it can be), or 'unknown' when it was not looked up."""
    if not isinstance(entry, dict):
        return "unknown"
    status = entry.get("status")
    if status == "deleted":
        return "deleted"
    if not entry.get("accessible"):
        return "unavailable"
    if status not in (None, "ready"):
        return status if status in ("failed", "pending") else "unavailable"
    if entry.get("exists") is False:
        return "file_gone"
    return None


WHY_MISSING = {"deleted": "it was deleted", "unavailable": "it is no longer in your library", "failed": "its file could not be used",
               "file_gone": "its file is gone", "asset_unavailable": "it is no longer available", "media_missing": "its file is gone"}


def _noun(slot, plan, kind):
    if plan.get("source") == "MANIM":
        return "animation"
    if slot == "main":
        return "video" if kind == "ai_video" else "animation"
    return "clip" if plan.get("media") in ("VIDEO", "ANIMATION") else "picture"


def _expected_kinds(slot, plan):
    if slot == "main":
        return ("video",)
    media = plan.get("media")
    if media == "STATIC_IMAGE":
        return ("image",)
    if media in ("VIDEO", "ANIMATION"):
        return ("video",)
    return ("image", "video")


def _shape_off(entry, expected):
    w, h = entry.get("width"), entry.get("height")
    if not (isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0):
        return None
    found = w / h
    off = max(found / expected, expected / found) - 1
    return round(found, 3) if off > SHAPE_TOLERANCE else None


def _is_ai(plan_source, entry):
    return plan_source in AI_PLAN_SOURCES or _dict(entry).get("source") in AI_ASSET_SOURCES


def _asset_quality(i, label, slot, noun, entry, expected_kinds, expected_shape, ai):
    """Kind, empty file, shape and provenance of an asset that can be shown."""
    out = []
    kind = entry.get("kind")
    if kind and kind not in expected_kinds:
        out.append(Q.issue("media.kind_mismatch", "media", "error",
                           f"The {noun} for {label} is {KIND_WORD.get(kind, 'a file of another kind')}, but this place needs "
                           f"{' or '.join(KIND_WORD.get(k, k) for k in expected_kinds)}. Choose another one in Visual Review.",
                           scene=i, element=slot, evidence={"slot": slot, "expected": list(expected_kinds), "found": kind}))
    if entry.get("file_size") == 0:
        out.append(Q.issue("media.empty_file", "media", "error",
                           f"The {noun} for {label} is an empty file: nothing would be shown. Choose another one in Visual Review.",
                           scene=i, element=slot, evidence={"slot": slot, "found": 0}))
    if expected_shape:
        shape = _shape_off(entry, expected_shape)
        if shape is not None:
            out.append(Q.issue("media.aspect", "media", "notice",
                               f"The {noun} for {label} has a shape that does not fit its wide place: it will show with bands or be cropped.",
                               scene=i, element=slot, evidence={"slot": slot, "expected": round(expected_shape, 3), "found": shape}))
    if ai and not entry.get("has_provenance"):
        out.append(Q.issue("media.no_provenance", "media", "notice",
                           f"The AI-made {noun} for {label} has no record of how it was made.",
                           scene=i, element=slot, evidence={"slot": slot, "key": "provenance"}))
    return out


def _visual_slot(lesson, i, slot, plan, assets, runs):
    label = lesson.label(i)
    noun = _noun(slot, plan, lesson.kind(i))
    asset_id = _asset_id(plan.get("asset_id"))
    error = plan.get("error")
    run = runs.get((i, slot))
    if asset_id:
        entry = assets.get(asset_id)
        problem = _problem(entry)
        if problem == "unknown":
            return []  # not looked up: nothing can be said
        if problem == "pending":
            return [Q.issue("media.not_generated", "media", "warning",
                            f"The {noun} for {label} is not ready yet: the scene will show the fallback until it is.",
                            scene=i, element=slot, evidence={"slot": slot, "found": "pending"})]
        if problem:
            return [Q.issue("media.missing", "media", "blocking",
                            f"The {noun} for {label} is missing ({WHY_MISSING.get(problem, problem)}): the export would show "
                            f"{'an empty panel' if slot == 'side' else 'nothing in its place'}. Choose another one in Visual Review.",
                            scene=i, element=slot, evidence={"slot": slot, "found": problem})]
        out = []
        if error:
            out.append(Q.issue("media.plan_outdated", "media", "notice",
                               f"The {noun} for {label} was unavailable when the scene's visuals were planned and is available "
                               "again: open the scene in Visual Review to show it.",
                               scene=i, element=slot, evidence={"slot": slot, "found": _str(error, 40)}))
            return out
        return _asset_quality(i, label, slot, noun, entry, _expected_kinds(slot, plan), WIDE if slot == "main" else None,
                              _is_ai(plan.get("source"), entry))
    if error in ("asset_unavailable", "media_missing"):
        return [Q.issue("media.missing", "media", "blocking",
                        f"The {noun} for {label} is missing ({WHY_MISSING[error]}): the export would show "
                        f"{'an empty panel' if slot == 'side' else 'nothing in its place'}. Choose another one in Visual Review.",
                        scene=i, element=slot, evidence={"slot": slot, "found": error})]
    waiting = plan.get("requires_generation") is True or bool(plan.get("would_require")) or plan.get("source") == "MANIM"
    if not waiting:
        return []
    status = _dict(run).get("status")
    if status in RUN_PROBLEMS:
        return [Q.issue("media.run_failed", "media", "warning",
                        f"The {noun} for {label} could not be made (the last attempt did not finish): the scene will show the "
                        "fallback. Visual Review shows what can be done.",
                        scene=i, element=slot, evidence={"slot": slot, "found": status})]
    making = status in RUN_ACTIVE
    return [Q.issue("media.not_generated", "media", "warning",
                    f"The {noun} for {label} {'is still being made' if making else 'has not been made yet'}: "
                    "the scene will show the fallback.",
                    scene=i, element=slot,
                    evidence={"slot": slot, "found": "making" if making else ("manim" if plan.get("source") == "MANIM" else "not_generated")})]


def _slot_shown(lesson, i, slot, plan):
    review = lesson.reviews(i).get(slot)
    if plan.get("selection") == "removed" or (isinstance(review, dict) and review.get("status") == "removed"):
        return False
    return plan.get("source") != "NONE" or plan.get("error") in ("asset_unavailable", "media_missing") \
        or plan.get("requires_generation") is True or bool(plan.get("would_require"))


def _background(lesson, i, assets):
    plan = lesson.plan(i)
    background = _dict(plan.get("background"))
    asset_id = _asset_id(background.get("asset_id"))
    if not asset_id or background.get("type") not in ("image", "video"):
        return []
    entry = assets.get(asset_id)
    problem = _problem(entry)
    label = lesson.label(i)
    if problem == "unknown":
        return []
    if problem:
        return [Q.issue("media.background_missing", "media", "error",
                        f"The background of {label} is missing ({WHY_MISSING.get(problem, problem)}): the scene would show an empty "
                        "backdrop. Re-planning shows the plain background instead.",
                        scene=i, element="background", evidence={"slot": "background", "found": problem},
                        repair=_replan([i]))]
    noun = "background picture" if background["type"] == "image" else "background video"
    return _asset_quality(i, label, "background", noun, entry, (background["type"],), WIDE,
                          bool(background.get("generated")) or _is_ai(None, entry))


def _presenter_clip(lesson, i, assets):
    if not _effective(lesson, i):
        return []
    pplan = lesson.presenter_plan(i)
    media = _dict(pplan.get("media"))
    asset_id = _asset_id(media.get("asset_id"))
    if not asset_id or not pplan.get("enabled") or not _presence(lesson, i)["shown"]:
        return []
    entry = assets.get(asset_id)
    problem = _problem(entry)
    label = lesson.label(i)
    if problem == "unknown":
        return []
    if problem:
        return [Q.issue("media.presenter_clip_missing", "media", "error",
                        f"The presenter clip of {label} is missing ({WHY_MISSING.get(problem, problem)}): the scene would play "
                        "without its presenter. Choose another clip in Visual Review.",
                        scene=i, element="presenter clip", evidence={"slot": "presenter", "found": problem})]
    return _asset_quality(i, label, "presenter", "presenter clip", entry, ("video", "image"), None, _is_ai(None, entry))


def _media_scene(lesson, i):
    media = lesson.media
    if not isinstance(media, dict) or media.get("checked") is False:
        return []
    assets = _dict(media.get("assets"))
    runs = _latest_runs(lesson)
    out = []
    plans = lesson.visual_plan(i)
    for slot in VISUAL_SLOTS:
        plan = plans.get(slot)
        if isinstance(plan, dict) and _slot_shown(lesson, i, slot, plan):
            out += _guard(_visual_slot, lesson, i, slot, plan, assets, runs)
    out += _guard(_background, lesson, i, assets)
    out += _guard(_presenter_clip, lesson, i, assets)
    return out


# ---- media: across scenes ---------------------------------------------------------------------------------------------------

def _ai_visuals(lesson):
    """[(scene, kind, provider, model)] of the AI visuals the lesson shows (from the assets when looked up)."""
    media = lesson.media if isinstance(lesson.media, dict) else {}
    assets = _dict(media.get("assets"))
    found = []
    for i in range(lesson.count):
        plans = lesson.visual_plan(i)
        for slot in VISUAL_SLOTS:
            plan = plans.get(slot)
            if not isinstance(plan, dict) or not _slot_shown(lesson, i, slot, plan):
                continue
            asset_id = _asset_id(plan.get("asset_id"))
            entry = assets.get(asset_id) if asset_id else None
            if not asset_id or not _is_ai(plan.get("source"), entry):
                continue  # only visuals that were made (a planned one has no maker yet)
            entry = _dict(entry)
            if entry and _problem(entry):
                continue
            kind = entry.get("kind") or ("video" if plan.get("source") == "AI_VIDEO" else "image")
            provider = _str(entry.get("provider")) or _str(plan.get("provider"))
            model = _str(entry.get("model"), 120) or _str(plan.get("model"), 120)
            if provider:
                found.append((i, kind, provider, model))
    return found


def _mixed_providers(lesson):
    by_kind = {}
    for _i, kind, provider, model in _ai_visuals(lesson):
        by_kind.setdefault(kind, set()).add(f"{provider}/{model}" if model else provider)
    out = []
    for kind, makers in sorted(by_kind.items()):
        if len(makers) > 1:
            out.append(Q.issue("media.mixed_providers", "media", "info",
                               f"The lesson's AI-made {'videos' if kind == 'video' else 'pictures'} come from {len(makers)} different "
                               "generators (the lesson does not ask for one).",
                               element=kind, evidence={"key": kind, "found": sorted(makers)}))
    return out


# ---- the family's interface (quality.py) -----------------------------------------------------------------------------------

def scene_checks(lesson, i):
    """Scene i's presenter and media findings (depends only on the scene, the lesson settings and lesson.media)."""
    return _guard(_presenter_scene, lesson, i) + _guard(_media_scene, lesson, i)


def lesson_checks(lesson):
    """Across scenes: presenter side flips and size jumps; AI visuals from several generators. (A background that fell
    back to the gradient, e.g. after a failed AI background, is the style family's finding: not reported twice.)"""
    out = _guard(_side_flips, lesson) + _guard(_scale_jumps, lesson)
    if isinstance(lesson.media, dict) and lesson.media.get("checked") is not False:
        out += _guard(_mixed_providers, lesson)
    return out


def registry(lesson):
    """The presenter and media sections of the lesson's consistency registry (small, structured)."""
    expected = _expected(lesson.settings)
    positions, roles, switches = [], [], []
    for i in range(lesson.count):
        pres = _presence(lesson, i)
        if not pres["shown"]:
            positions.append("hidden")
            roles.append("hidden")
            continue
        positions.append(pres["side"] if pres["side"] in ("left", "right", "center") else None)
        roles.append(pres["placement"] if isinstance(pres["placement"], str) else ("side" if pres["type"] == "mascot" else None))
        if pres["who"] and pres["who"] != expected and len(switches) < 50:
            switches.append({"scene": i, "presenter_id": pres["who"]})
    sources = Counter()
    for i in range(lesson.count):
        plans = lesson.visual_plan(i)
        for slot in VISUAL_SLOTS:
            plan = plans.get(slot)
            if isinstance(plan, dict) and _slot_shown(lesson, i, slot, plan) and isinstance(plan.get("source"), str):
                sources[plan["source"][:20]] += 1
    providers = sorted({provider for _i, _k, provider, _m in _ai_visuals(lesson)})
    return {"presenter": {"profile": expected, "type": _type_of(expected), "positions": positions, "roles": roles,
                          "switches": switches},
            "media": {"sources": dict(sorted(sources.items())), "providers": providers,
                      "checked": isinstance(lesson.media, dict) and lesson.media.get("checked") is not False}}


# ---- what the media checks read (server side; read-only) ----------------------------------------------------------------------

def referenced_asset_ids(scenes):
    """Every library asset a lesson's scenes refer to for its visuals, presenter clips and backgrounds, and the assets
    chosen in Visual Review (sorted, at most MAX_IDS)."""
    found = set()
    for scene in scenes[:Q.MAX_SCENES] if isinstance(scenes, list) else []:
        if not isinstance(scene, dict):
            continue
        for plan in _dict(scene.get("visual_plan")).values():
            if isinstance(plan, dict) and _asset_id(plan.get("asset_id")):
                found.add(plan["asset_id"])
        media = _dict(_dict(scene.get("presenter_plan")).get("media"))
        if _asset_id(media.get("asset_id")):
            found.add(media["asset_id"])
        background = _dict(_dict(scene.get("cinematic_plan")).get("background"))
        if _asset_id(background.get("asset_id")):
            found.add(background["asset_id"])
        for review in _dict(scene.get("visual_review")).values():
            if isinstance(review, dict) and _asset_id(review.get("asset_id")):
                found.add(review["asset_id"])
    return sorted(found)[:MAX_IDS]


def _library():
    """The server's asset library (its storage volumes), else one built the same way (no side effects)."""
    for name in ("server", "__main__"):
        lib = getattr(sys.modules.get(name), "asset_library", None)
        if lib is not None and hasattr(lib, "file_exists"):
            return lib
    try:
        import assets
        return assets.build_library(os.getenv("STATIC_DIR") or "static_videos", "video_template")
    except Exception:  # noqa: BLE001
        return None


def _generation(details):
    """details.generation of an asset (provider and model only are kept; never the prompt)."""
    try:
        data = json.loads(details) if details else {}
    except (TypeError, ValueError):
        return {}
    generation = data.get("generation") if isinstance(data, dict) else None
    return generation if isinstance(generation, dict) else {}


def _entry(row, user_id, library):
    """One asset as the checks see it. Someone else's asset (or an unknown id) says only that it is not accessible."""
    blank = {"kind": None, "status": None, "exists": False, "width": None, "height": None, "file_size": None, "mime": None,
             "source": None, "provider": None, "model": None, "has_provenance": False, "accessible": False}
    if row is None or not (row.owner_id is None or row.owner_id == user_id):
        return blank
    from assets import AssetLibrary
    accessible = AssetLibrary.usable_by(row, user_id)
    exists = False
    if accessible:
        try:
            exists = bool(library.file_exists(row)) if library is not None else None
        except Exception:  # noqa: BLE001 - an unknown storage volume: the file cannot be confirmed
            exists = False
    generation = _generation(row.details)
    provider = generation.get("provider")
    return {"kind": _str(row.kind, 10), "status": _str(row.status, 10), "exists": exists,
            "width": row.width if isinstance(row.width, int) else None, "height": row.height if isinstance(row.height, int) else None,
            "file_size": row.file_size if isinstance(row.file_size, int) else None, "mime": _str(row.mime_type, 100),
            "source": _str(row.source, 20), "provider": _str(provider, 40), "model": _str(generation.get("model"), 120),
            "has_provenance": isinstance(provider, str) and bool(provider), "accessible": bool(accessible)}


def load_media(db, user_id, scenes, project_id=None, library=None):
    """The lesson's media metadata for the checks: {"checked": True, "assets": {id: {kind, status, exists, width, height,
    file_size, mime, source, provider, model, has_provenance, accessible}}, "runs": [{scene_index, slot, kind, status,
    provider, model}]}. Read-only: one query for the assets (the user's own and shared ones; another user's asset reads as
    not accessible), a plain file check for each (nothing is marked missing), one query for the lesson's AI runs (the
    latest of each scene slot; none without a saved lesson). No URL, no prompt, no secret."""
    import models
    library = library if library is not None else _library()
    ids = referenced_asset_ids(scenes)
    columns = (models.Asset.id, models.Asset.owner_id, models.Asset.kind, models.Asset.status, models.Asset.source,
               models.Asset.mime_type, models.Asset.file_size, models.Asset.width, models.Asset.height, models.Asset.details,
               models.Asset.storage_volume, models.Asset.storage_key)
    rows = {row.id: row for row in db.query(*columns).filter(models.Asset.id.in_(ids)).all()} if ids else {}
    assets = {asset_id: _entry(rows.get(asset_id), user_id, library) for asset_id in ids}
    runs = []
    if isinstance(project_id, int) and not isinstance(project_id, bool):
        r = models.AIGenerationRun
        found = db.query(r.scene_index, r.slot, r.kind, r.media_type, r.status, r.provider, r.model).filter(
            r.project_id == project_id, r.user_id == user_id).order_by(r.created_at, r.id).all()
        latest = {}
        for run in found:
            if run.kind not in RUN_KINDS:
                continue
            latest[(run.scene_index, run.slot)] = {
                "scene_index": run.scene_index if isinstance(run.scene_index, int) else None, "slot": _str(run.slot, 10),
                "kind": "manim" if run.kind == "manim" else (_str(run.media_type, 10) or "media"), "status": _str(run.status, 20),
                "provider": _str(run.provider, 40), "model": _str(run.model, 120)}
        runs = sorted(latest.values(), key=lambda x: (x["scene_index"] if x["scene_index"] is not None else -1, x["slot"] or ""))
    return {"checked": True, "assets": assets, "runs": runs}
