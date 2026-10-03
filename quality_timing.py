"""Phase 18 — the timing, camera, motion and busy-scene checks of the Quality & Consistency Engine (quality.py).

This family builds no timing of its own: it reads the stored plans and checks them against the rules of the phases that
made them.

  narration length  sync_director.narration_model (Phase 16): ONE source for how long the narration takes; the scene
                    stays on screen for that time with Phase 13's floor (at least 4 s) and the page's 0.8 s before the
                    next scene
  synchronization   plan["sync"] (Phase 16): valid (sync_director.validate), every moment inside the narration and
                    pointing at something the plan really shows, not busier than one moment every 0.8 s
  transitions       one kind per lesson unless a scene asks for another (Phase 13); moving kinds become a fade without
                    motion; each kind lasts cinematic.TRANSITION_SECONDS
  camera / motion   still when the lesson's (or the scene's) motion is "none" (Phase 13); variety from purpose, not
                    restless switching or repetition (Phase 14); code and formulas read under a still camera or a focus
  busy scenes       scene_intent density (Phase 14) and what the plan shows at once; caption length; reading time
  editor (Phase 19) a scene's minimum duration (scene.edit.min_seconds) lengthens its play time (never its narration); a
                    minimum shorter than the narration is noted (the scene plays until the narration ends); a scene whose
                    narration is muted plays like a silent scene; hidden scenes (scene.edit.hidden) are not played, so the
                    checks across scenes skip them

Camera framing bounds are the style family's (cinematic.validate_plan), not repeated here. Read-only and deterministic:
nothing here writes a scene, a plan or a review, generates media or asks a model.
"""
import cinematic as C
import composer as K
import scene_intent as SI
import sync_director as SD
from quality import issue, repair

MIN_SCENE = 4.0           # Phase 13: a scene lasts at least 4 s (cinematic.estimate_seconds)
TAIL = 0.8                # the page moves on 0.8 s after the narration ends (index.html)
DURATION_SLACK = (3.0, 0.35)  # a planned length further than max(3 s, 35 %) from the narration's is out of date
TIME_TOLERANCE = 0.05     # seconds: rounding in the stored plans
EVENT_TOLERANCE = 0.1
CAPTION_CHARS = 110       # about two subtitle lines; a longer sentence runs into the bottom band
READING_FACTOR = 1.5      # reading needs more than 1.5x the scene's time: too much to read
BUSY_SECONDS = 0.8        # Phase 16: one primary emphasis per 0.8 s
MOMENT_GAP = 0.1          # moments closer than this are one moment (a highlight and the presenter pointing at it)
MIN_BUSY_MOMENTS = 4
STILL_RUN = 5             # five moving cameras in a row and not one still scene
REPEAT_RUN = 3            # Phase 14: three identical layouts in a row are avoided
KINDS_NOTICE = 3
LABELS_BUSY = 4
SPARSE_SECONDS = 20.0
SPARSE_WORDS = 12
MOVING_ANIMATIONS = ("slide_in", "slide_out", "scale_in", "scale_out")
ENTRANCE_LAYERS_SKIPPED = ("background", "subtitles", "labels", "board")  # the page does not animate these from layer.start
READING_PURPOSES = {"code": "code", "formula": "a formula"}
READING_TEMPLATES = {"code_focus": "code", "formula_focus": "a formula"}

TRANSITION_WORDS = {"fade": ("a fade", "fades"), "crossfade": ("a crossfade", "crossfades"), "slide": ("a slide", "slides"),
                    "cut": ("a cut", "cuts"), "soft_fade": ("a soft fade", "soft fades"), "zoom": ("a zoom", "zooms"),
                    "wipe": ("a wipe", "wipes")}
MOVE_WORDS = {"static": "a still camera", "slow_zoom_in": "a slow zoom in", "slow_zoom_out": "a slow zoom out",
              "pan_left": "a pan to the left", "pan_right": "a pan to the right", "pan_up": "a pan upwards",
              "pan_down": "a pan downwards", "focus": "a focus"}
TARGET_WORDS = {"labels": "a label", "visual": "the picture", "presenter": "the presenter", "board": "a part of the board",
                "camera": "a camera move"}
MOTION_WORDS = {"none": "no", "subtle": "subtle", "moderate": "moderate"}


# ---- small helpers -------------------------------------------------------------------------------------------------------

def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _secs(x):
    x = float(x)
    return f"{x:.0f} s" if x >= 10 else f"{x:.1f} s"


def _plural(n, one, many):
    return one if n == 1 else many


def _layer(plan, layer_id):
    for layer in plan.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    return None


def _camera(plan):
    cam = plan.get("camera")
    return cam if isinstance(cam, dict) else {}


def _movement(plan):
    move = _camera(plan).get("movement")
    return move if move in C.CAMERA_MOVES else None


def _moving(plan):
    move = _movement(plan)
    return move is not None and move != "static"


def _transition(plan):
    t = plan.get("transition")
    return t if isinstance(t, dict) else {}


def _sync(plan):
    """The scene's synchronization plan: a dict, None (none stored) or False (something stored that is not a plan)."""
    if "sync" not in plan or plan.get("sync") is None:
        return None
    return plan["sync"] if isinstance(plan["sync"], dict) else False


def _valid_sync(plan):
    sync = _sync(plan)
    return sync if sync and not SD.validate(sync) else None


def _quiz(lesson, i):
    return lesson.kind(i) == "quiz_checkpoint"


def _lesson_motion(lesson):
    m = lesson.settings.get("motion")
    return m if m in C.MOTIONS else C.DEFAULT_STYLE["motion"]


def _lesson_still(lesson):
    return _lesson_motion(lesson) == "none"


def _scene_still(lesson, plan):
    """Phase 13: the lesson style's "none" overrides everything; a scene's own motion level can also be "none"."""
    return _lesson_still(lesson) or plan.get("motion") == "none"


def _lesson_transition(lesson):
    """The lesson's transition setting, as the plans should show it (a moving kind is a fade without motion)."""
    t = lesson.settings.get("transitions")
    t = t if t in C.TRANSITIONS else C.DEFAULT_STYLE["transitions"]
    return _effective(t, _lesson_still(lesson))


def _effective(kind, still):
    return "fade" if still and kind in C.MOVING_TRANSITIONS else kind


def _choice(lesson, i, key):
    """The scene's own explicit choice for a composition key: (Visual Review value, screenplay value), "auto" ignored."""
    scene = lesson.scene(i)
    try:
        review = C.review_overrides(scene)
    except Exception:  # noqa: BLE001 - a malformed review record is no choice
        review = {}
    try:
        screenplay, _warnings = C.clean_composition(scene.get("composition"))
    except Exception:  # noqa: BLE001
        screenplay = {}
    pick = [review.get(key) if isinstance(review, dict) else None, screenplay.get(key)]
    return [v if v not in (None, "", "auto") else None for v in pick]


def _chosen(lesson, i, key):
    return any(_choice(lesson, i, key))


def _model(lesson, i):
    m = lesson.narration(i)
    return m if isinstance(m, dict) and isinstance(m.get("segments"), list) else {"segments": [], "sentences": [], "estimate_total": 0.0}


def _has_narration(model):
    return bool(model["segments"]) and any(s.get("chars") for s in model["segments"])


def narration_seconds(lesson, i):
    """How long the narration takes (Phase 16's estimate: the one source for the narration's length)."""
    return float(_model(lesson, i).get("estimate_total") or 0.0)


def _edit(lesson, i):
    """Phase 19: the scene's editor data (scene.edit), or {}."""
    edit = lesson.scene(i).get("edit")
    return edit if isinstance(edit, dict) else {}


def hold_seconds(lesson, i):
    """Phase 19: the scene's minimum duration set in the editor (0 < x ≤ 600 s), or None."""
    return C.edit_min_seconds(lesson.scene(i))


def hidden(lesson, i):
    """Phase 19: a scene hidden in the editor stays in the lesson but is never played (preview and export)."""
    return _edit(lesson, i).get("hidden") is True


def muted(lesson, i):
    """Phase 19: a scene whose narration is muted in the editor plays without speaking (like a silent scene)."""
    return _edit(lesson, i).get("narration_muted") is True


def played(lesson):
    """The scenes that play, in order (hidden scenes left out)."""
    return [i for i in range(lesson.count) if not hidden(lesson, i)]


def narration_scene_seconds(lesson, i):
    """How long the narration keeps the scene on screen: the narration's length with Phase 13's floor and the page's pause
    before the next scene (a scene without narration: Phase 13's own length for it). What the plan's duration follows."""
    model = _model(lesson, i)
    if not _has_narration(model):
        return float(C.estimate_seconds(""))
    return round(max(MIN_SCENE, float(model["estimate_total"]) + TAIL), 2)


def scene_seconds(lesson, i):
    """How long the scene stays on screen: what its narration needs (narration_scene_seconds; a muted narration: a silent
    scene's length), and at least the minimum duration set in the editor (Phase 19; the narration is never cut)."""
    base = float(C.estimate_seconds("")) if muted(lesson, i) else narration_scene_seconds(lesson, i)
    hold = hold_seconds(lesson, i)
    return round(max(hold, base), 2) if hold is not None else base


def _intent(lesson, i):
    return lesson._get(("quality_timing.intent", i),
                       lambda: SI.scene_intent(lesson.scene(i), i, lesson.count, lesson.settings)) or {}


def _planned(lesson, i):
    """The scene's stored plan when the lesson is cinematic (else {})."""
    return lesson.plan(i) if lesson.cinematic else {}


def _replan(klass, i, label="Re-plan the scene"):
    return repair("auto", klass, {"type": "replan", "scenes": [i]}, label)


def _suggest(i, overrides, label):
    return repair("suggest", "composition", {"type": "composition", "scene": i, "overrides": overrides}, label)


def _safely(checks, *args):
    """Each rule on its own: one that cannot decide on odd data stays silent and never costs the others theirs."""
    out = []
    for check in checks:
        try:
            out += check(*args) or []
        except Exception:  # noqa: BLE001
            continue
    return out


# ---- one scene (memoized by quality.scene_fingerprint: only the scene's text, its plan's hashed fields, the settings) ----

def _caption_long(lesson, i):
    model = _model(lesson, i)
    long = [len(s.get("text") or "") for s in model.get("sentences") or [] if len(s.get("text") or "") > CAPTION_CHARS]
    if not long:
        return []
    n = len(long)
    return [issue("timing.caption_long", "timing", "notice",
                  f"{lesson.label(i)}: {n} {_plural(n, 'sentence', 'sentences')} of the narration {_plural(n, 'is', 'are')} longer "
                  f"than two subtitle lines (the longest has {max(long)} characters), so the captions would crowd the bottom of "
                  f"the picture. Shorter sentences read better.",
                  scene=i, element="captions", evidence={"key": "caption_length", "count": n, "longest": max(long),
                                                         "limit": CAPTION_CHARS})]


def _reading_time(lesson, i):
    if _quiz(lesson, i):
        return []  # a quiz runs its own question, countdown and reveal
    reading = _intent(lesson, i).get("reading_seconds")
    if not _num(reading):
        return []
    seconds = scene_seconds(lesson, i)
    if reading <= READING_FACTOR * seconds:
        return []
    return [issue("timing.reading_time", "timing", "warning",
                  f"{lesson.label(i)} has too much to read in the time the narration gives: about {_secs(reading)} of reading "
                  f"in a scene of about {_secs(seconds)}. Consider splitting the scene or giving it more narration.",
                  scene=i, element="board", evidence={"key": "reading_time", "reading_seconds": reading,
                                                      "scene_seconds": seconds, "factor": READING_FACTOR})]


def _duration_mismatch(lesson, i):
    plan = _planned(lesson, i)
    planned = plan.get("duration")
    if not plan or not _num(planned) or planned <= 0:
        return []  # a missing duration is the plan's own validation (cinematic.validate_plan)
    expected = narration_scene_seconds(lesson, i)  # the plan's duration follows the narration (never the editor's minimum)
    if abs(planned - expected) <= max(DURATION_SLACK[0], DURATION_SLACK[1] * expected):
        return []
    if abs(planned - C.estimate_seconds(lesson.scene(i).get("narration"))) <= TIME_TOLERANCE:
        return []  # what re-planning gives today: nothing a re-plan could change
    return [issue("timing.duration_mismatch", "timing", "notice",
                  f"{lesson.label(i)}: its planned length ({_secs(planned)}) no longer matches its narration (about "
                  f"{_secs(expected)}), so its moments are timed for another narration. Re-planning retimes it.",
                  scene=i, element="duration", evidence={"key": "duration", "planned": planned, "narration": expected},
                  repair=_replan("timing", i, "Retime the scene"))]


def _transition_seconds(lesson, i):
    plan = _planned(lesson, i)
    t = _transition(plan)
    kind = t.get("in")
    if not plan or kind not in C.TRANSITION_SECONDS:
        return []  # an unknown kind is the plan's own validation
    expected = C.TRANSITION_SECONDS[kind]
    found = t.get("duration")
    if _num(found) and abs(found - expected) <= 0.01:
        return []
    shown = _secs(found) if _num(found) else "an unknown time"
    return [issue("timing.transition_seconds", "timing", "error",
                  f"{lesson.label(i)}: the change into this scene lasts {shown}, but {TRANSITION_WORDS[kind][0]} lasts "
                  f"{_secs(expected)}. Re-planning fixes it.",
                  scene=i, element="transition", evidence={"key": "transition_seconds", "kind": kind,
                                                           "seconds": found if _num(found) else None, "expected": expected},
                  repair=_replan("timing", i))]


def _shown(plan):
    """What the plan shows at once: board, visual, presenter (and how large), label count."""
    board, visual, presenter, labels = _layer(plan, "board"), _layer(plan, "visual"), _layer(plan, "presenter"), _layer(plan, "labels")
    decision = ((plan.get("composition") or {}).get("decision") or {}) if isinstance(plan.get("composition"), dict) else {}
    size = decision.get("presenter_role") if isinstance(decision, dict) else None
    if size not in ("dominant", "secondary", "small"):
        size = {"large": "dominant", "side": "secondary", "small": "small", "pip": "small"}.get((presenter or {}).get("placement"), "secondary")
    full_canvas = bool(visual and visual.get("role") == "full_canvas")
    return {"board": bool(board), "visual": bool(visual) and not full_canvas, "full_canvas": full_canvas,
            "presenter": size if presenter else None,
            "labels": len([x for x in (labels or {}).get("items") or [] if isinstance(x, dict)]) if labels else 0}


def _density_overloaded(lesson, i):
    plan = _planned(lesson, i)
    if not plan or _quiz(lesson, i):
        return []
    s = _shown(plan)
    dense = _intent(lesson, i).get("density") == "high"
    everything = s["board"] and s["visual"] and s["presenter"] and s["labels"] >= LABELS_BUSY
    competing = s["visual"] or s["presenter"] in ("dominant", "secondary") or s["labels"] >= LABELS_BUSY - 1
    # a dense board alone (the presenter small or away, no picture) is the composer's own answer to density: whether there
    # is time to read it is the reading-time rule's
    if not (everything or (dense and s["board"] and competing)):
        return []
    parts = (["a dense board"] if dense else (["the board"] if s["board"] else [])) + (["a picture"] if s["visual"] else []) \
        + (["the presenter"] if s["presenter"] else []) + ([f"{s['labels']} labels"] if s["labels"] else [])
    if s["presenter"] in ("dominant", "secondary"):
        fix, label = {"presenter_size": "small"}, "Make the presenter smaller"
    elif s["visual"]:
        fix, label = {"visual_size": "side_panel"}, "Make the picture narrower"
    elif s["presenter"] == "small":
        fix, label = {"presenter_size": "hidden"}, "Let the presenter step away"
    else:
        fix, label = None, None
    return [issue("timing.density_overloaded", "density", "warning",
                  f"{lesson.label(i)} shows a lot at once ({', '.join(parts)}). Making one element smaller would give the "
                  f"content more room; nothing is removed.",
                  scene=i, element="scene", evidence={"key": "overloaded", "density": _intent(lesson, i).get("density"),
                                                      "shown": s},
                  repair=_suggest(i, fix, label) if fix else None)]


def _density_sparse(lesson, i):
    plan = _planned(lesson, i)
    if not plan or _quiz(lesson, i) or not _has_narration(_model(lesson, i)):
        return []
    seconds = narration_seconds(lesson, i)
    if seconds <= SPARSE_SECONDS or _layer(plan, "visual"):
        return []
    f = lesson.facts(i)
    if not isinstance(f, dict) or f.get("words", 0) > SPARSE_WORDS or f.get("items", 0) > 1 or f.get("code_lines") \
            or f.get("formulas") or f.get("tables") or f.get("images"):
        return []
    return [issue("timing.density_sparse", "density", "notice",
                  f"{lesson.label(i)} shows only one short line for about {_secs(seconds)} of narration, with no picture. "
                  f"Consider adding a visual in Visual Review or splitting the narration.",
                  scene=i, element="board", evidence={"key": "sparse", "narration_seconds": seconds, "words": f.get("words", 0)})]


def _hold_shorter(lesson, i):
    """Phase 19: a minimum duration shorter than the narration changes nothing (the narration is never cut or sped up)."""
    hold = hold_seconds(lesson, i)
    if hold is None or muted(lesson, i) or not _has_narration(_model(lesson, i)):
        return []
    needs = narration_seconds(lesson, i)
    if hold + TIME_TOLERANCE >= needs:
        return []
    return [issue("timing.hold_shorter", "timing", "notice",
                  f"{lesson.label(i)}: the scene is set to {_secs(hold)} but its narration needs about {_secs(needs)}: it plays "
                  f"until the narration ends.",
                  scene=i, element="duration", evidence={"key": "hold_shorter", "min_seconds": hold,
                                                         "narration_seconds": round(needs, 2)})]


SCENE_CHECKS = (_caption_long, _reading_time, _duration_mismatch, _transition_seconds, _density_overloaded, _density_sparse,
                _hold_shorter)


def scene_checks(lesson, i):
    """One scene's own checks (they read only its text, its plan's hashed fields and the lesson settings)."""
    if not 0 <= i < lesson.count:
        return []
    return _safely(SCENE_CHECKS, lesson, i)


# ---- across the lesson (run every time: they read the synchronization plans and the scenes' explicit choices) -----------

def _sync_problems(lesson, i, plan, _sync_plan):
    """A stored synchronization plan that is not valid (Phase 16's own validation)."""
    sync = _sync(plan)
    if sync is None:
        return []
    problems = ["not a synchronization plan"] if sync is False else SD.validate(sync)
    if not problems:
        return []
    return [issue("timing.sync_invalid", "timing", "error",
                  f"{lesson.label(i)}: the timing of its highlights and labels is damaged or comes from an older version, so "
                  f"the scene cannot play them. Re-planning rebuilds it.",
                  scene=i, element="timing", evidence={"key": "sync_invalid", "problems": sorted(set(problems))[:6]},
                  repair=_replan("timing", i))]


def _late_events(lesson, i, plan, sync):
    model = _model(lesson, i)
    has = _has_narration(model)
    limit = float(model["estimate_total"]) if has else scene_seconds(lesson, i)
    late = []
    for e in sync.get("events") or []:
        at = e.get("at") if isinstance(e.get("at"), dict) else None
        beyond = has and at is not None and at.get("segment", 0) >= len(model["segments"])
        if beyond or (_num(e.get("estimate")) and e["estimate"] > limit + EVENT_TOLERANCE):
            late.append(e)
    if not late:
        return []
    n = len(late)
    return [issue("timing.event_after_end", "timing", "error",
                  f"{lesson.label(i)}: {n} {_plural(n, 'highlight or label is', 'highlights or labels are')} timed after the "
                  f"narration ends, so the viewer would never see {_plural(n, 'it', 'them')}. Re-planning retimes the scene.",
                  scene=i, element="timing", evidence={"key": "event_after_end", "count": n, "narration_seconds": limit,
                                                       "latest": max((e.get("estimate") or 0) for e in late),
                                                       "types": sorted({str(e.get("type")) for e in late})},
                  repair=_replan("timing", i, "Retime the scene"))]


def _orphans(lesson, i, plan, sync):
    """Moments whose target the plan does not show (what Phase 16's resolution removes when it builds the plan)."""
    labels = _layer(plan, "labels")
    count = len((labels or {}).get("items") or []) if labels else 0
    gone = []
    understanding = None
    for e in sync.get("events") or []:
        t = e.get("target") if isinstance(e.get("target"), dict) else {}
        where = t.get("layer")
        if where == "labels":
            ok = isinstance(t.get("item"), int) and not isinstance(t.get("item"), bool) and 0 <= t["item"] < count
        elif where in ("visual", "presenter"):
            ok = bool(_layer(plan, where))
        elif where == "board":
            ok = bool(_layer(plan, "board")) and t.get("kind") in SD.BOARD_KINDS
            if ok:
                if understanding is None:
                    understanding = lesson.understanding(i) or {}
                try:
                    ok = bool(SD._board_target_exists(t, understanding))
                except Exception:  # noqa: BLE001 - the board could not be read: nothing to decide on
                    ok = True
        elif where == "camera":
            # a camera moment with no camera move planned; with motion switched off it is the camera rule's
            ok = _moving(plan) or _scene_still(lesson, plan)
        else:
            ok = True  # an unknown target is the synchronization's own validation
        if not ok:
            gone.append(where)
    if not gone:
        return []
    n = len(gone)
    what = sorted({TARGET_WORDS[g] for g in gone})
    return [issue("timing.orphan_target", "timing", "error",
                  f"{lesson.label(i)}: {n} {_plural(n, 'moment points', 'moments point')} at something the scene does not show "
                  f"({', '.join(what)}), so nothing happens then. Re-planning times the scene again for what it shows.",
                  scene=i, element="timing", evidence={"key": "orphan_target", "count": n, "targets": sorted(set(gone))},
                  repair=_replan("timing", i))]


def _moments(sync):
    times = sorted(e["estimate"] for e in sync.get("events") or [] if _num(e.get("estimate")))
    moments, start = 0, None
    for t in times:
        if start is None or t - start > MOMENT_GAP:
            moments += 1
            start = t
    return moments


def _busy(lesson, i, plan, sync):
    moments = _moments(sync)
    seconds = narration_scene_seconds(lesson, i)  # the moments follow the narration (an editor hold adds no room between them)
    if moments < MIN_BUSY_MOMENTS or seconds / moments >= BUSY_SECONDS:
        return []
    return [issue("timing.motion_busy", "motion", "notice",
                  f"{lesson.label(i)} has many moments close together ({moments} in about {_secs(seconds)}): the viewer may not "
                  f"be able to follow them all.",
                  scene=i, element="timing", evidence={"key": "busy", "moments": moments, "scene_seconds": seconds,
                                                       "every": BUSY_SECONDS})]


def _late_items(lesson, i, plan, sync):
    """Things on screen timed after the scene ends: element entrances, and (when no synchronization drives the scene) the
    timeline's labels, highlights and camera move."""
    duration = plan.get("duration") if _num(plan.get("duration")) and plan["duration"] > 0 else scene_seconds(lesson, i)
    hold = hold_seconds(lesson, i)
    if hold is not None:
        duration = max(duration, hold)  # Phase 19: the scene stays on screen at least its minimum duration
    late = {}
    for layer in plan.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") not in ENTRANCE_LAYERS_SKIPPED and _num(layer.get("start")) \
                and layer["start"] > duration + TIME_TOLERANCE:
            late[(str(layer.get("id")), None)] = layer["start"]
    if sync is None:
        for entry in plan.get("timeline") or []:
            if isinstance(entry, dict) and _num(entry.get("at")) and entry["at"] > duration + TIME_TOLERANCE:
                late[(str(entry.get("layer")), entry.get("item") if isinstance(entry.get("item"), int) else None)] = entry["at"]
        labels = _layer(plan, "labels")
        for n, item in enumerate((labels or {}).get("items") or [] if labels else []):
            if isinstance(item, dict) and _num(item.get("at")) and item["at"] > duration + TIME_TOLERANCE:
                late[("labels", n)] = item["at"]
    elif _moving(plan) and not any(isinstance(e, dict) and str(e.get("type", "")).startswith("camera") for e in sync.get("events") or []):
        start = _camera(plan).get("start")
        if _num(start) and start > duration + TIME_TOLERANCE:
            late[("camera", None)] = start  # no camera moment: the page plays the planned move at its start
    if not late:
        return []
    n = len(late)
    return [issue("timing.item_after_end", "timing", "error",
                  f"{lesson.label(i)}: {n} {_plural(n, 'thing on screen is', 'things on screen are')} timed after the scene ends "
                  f"(the latest at {_secs(max(late.values()))} in a scene of {_secs(duration)}), so "
                  f"{_plural(n, 'it', 'they')} would never be seen. Re-planning retimes the scene.",
                  scene=i, element="entrances", evidence={"key": "item_after_end", "count": n, "latest": max(late.values()),
                                                          "scene_seconds": duration, "layers": sorted({k[0] for k in late})},
                  repair=_replan("timing", i, "Retime the scene"))]


def _camera_motion_off(lesson, i, plan, sync):
    """Phase 13: the camera stays still when the lesson's (or this scene's) motion is switched off; a camera moment in the
    synchronization would move it all the same."""
    if not _scene_still(lesson, plan):
        return []
    moments = [e for e in (sync or {}).get("events") or [] if isinstance(e, dict) and str(e.get("type", "")).startswith("camera")]
    if not _moving(plan) and not moments:
        return []
    why = "the lesson's camera motion is switched off" if _lesson_still(lesson) else "this scene has no motion"
    move = _movement(plan) if _moving(plan) else "focus"
    return [issue("timing.camera_motion_off", "camera", "error",
                  f"{lesson.label(i)}: the camera moves ({MOVE_WORDS.get(move, 'a camera move')}) although {why}. "
                  f"Re-planning keeps it still.",
                  scene=i, element="camera", evidence={"key": "camera_motion_off", "movement": _movement(plan),
                                                       "moments": len(moments), "lesson_motion": _lesson_motion(lesson)},
                  repair=_replan("presentation", i, "Keep the camera still"))]


def _motion_not_still(lesson, i, plan, _sync_plan):
    lesson_still = _lesson_still(lesson)
    moving_layers = sorted({str(layer.get("id")) for layer in plan.get("layers") or [] if isinstance(layer, dict)
                            and (layer.get("enter") in MOVING_ANIMATIONS or layer.get("exit") in MOVING_ANIMATIONS)})
    wrong_level = lesson_still and plan.get("motion") not in (None, "none")
    if not (wrong_level or (_scene_still(lesson, plan) and moving_layers)):
        return []
    why = "the lesson's motion is switched off" if lesson_still else "this scene has no motion"
    return [issue("timing.motion_not_still", "motion", "error",
                  f"{lesson.label(i)} still has moving entrances although {why}. Re-planning makes them plain fades.",
                  scene=i, element="motion", evidence={"key": "motion_not_still", "motion": plan.get("motion"),
                                                       "lesson_motion": _lesson_motion(lesson), "layers": moving_layers},
                  repair=_replan("presentation", i, "Re-plan without motion"))]


def _motion_choice(lesson, i, plan, _sync_plan):
    chosen = _choice(lesson, i, "motion")[0]  # only Visual Review sets a scene's motion level
    if chosen not in MOTION_WORDS or plan.get("motion") != chosen or chosen == _lesson_motion(lesson):
        return []
    return [issue("timing.motion_choice", "motion", "info",
                  f"{lesson.label(i)} uses {MOTION_WORDS[chosen]} motion, chosen for this scene in Visual Review (the lesson "
                  f"uses {MOTION_WORDS[_lesson_motion(lesson)]} motion).",
                  scene=i, element="motion", evidence={"key": "motion_choice", "value": chosen})]


def _transition_kind(lesson, i, plan, _sync_plan):
    t = _transition(plan)
    found, out = t.get("in"), t.get("out")
    if found not in C.TRANSITIONS:
        return []  # an unknown kind is the plan's own validation
    still = _scene_still(lesson, plan)
    if still and (found in C.MOVING_TRANSITIONS or out in C.MOVING_TRANSITIONS):
        moving = found if found in C.MOVING_TRANSITIONS else out
        why = "the lesson's motion is switched off" if _lesson_still(lesson) else "this scene has no motion"
        return [issue("timing.moving_transition", "motion", "error",
                      f"{lesson.label(i)} changes with {TRANSITION_WORDS[moving][0]} although {why}: it should be a plain fade. "
                      f"Re-planning fixes it.",
                      scene=i, element="transition", evidence={"key": "moving_transition", "found": moving},
                      repair=_replan("presentation", i, "Use a plain fade"))]
    raw = lesson.settings.get("transitions") if lesson.settings.get("transitions") in C.TRANSITIONS else C.DEFAULT_STYLE["transitions"]
    expected = _effective(raw, still)
    if found == expected:
        return []
    lesson_kind = _lesson_transition(lesson)
    chosen = [_effective(v, still) for v in _choice(lesson, i, "transition") if v in C.TRANSITIONS]
    if found in chosen:
        return [issue("timing.transition_choice", "motion", "info",
                      f"{lesson.label(i)} changes with {TRANSITION_WORDS[found][0]}, chosen for this scene (the lesson uses "
                      f"{TRANSITION_WORDS[lesson_kind][1]}).",
                      scene=i, element="transition", evidence={"key": "transition_choice", "value": found})]
    return [issue("timing.transition_mismatch", "motion", "notice",
                  f"{lesson.label(i)} changes with {TRANSITION_WORDS[found][0]} while the lesson uses "
                  f"{TRANSITION_WORDS[expected][1]}, and nothing in the scene asks for it. Re-planning gives it the lesson's "
                  f"transition.",
                  scene=i, element="transition", evidence={"key": "transition_mismatch", "found": found, "expected": expected},
                  repair=_replan("presentation", i, "Use the lesson's transition"))]


def _reading_kind(lesson, i, plan):
    purpose = _intent(lesson, i).get("purpose")
    return READING_PURPOSES.get(purpose) or READING_TEMPLATES.get(plan.get("template"))


def _camera_reading(lesson, i, plan, _sync_plan):
    move = _movement(plan)
    if move in (None, "static", "focus"):
        return []
    what = _reading_kind(lesson, i, plan)
    if not what:
        return []
    if _chosen(lesson, i, "camera"):
        return [issue("timing.camera_reading", "camera", "info",
                      f"{lesson.label(i)}: the camera moves ({MOVE_WORDS[move]}) over {what}, as chosen for this scene.",
                      scene=i, element="camera", evidence={"key": "camera_reading", "value": move})]
    fix = "static" if what == "code" else "focus"
    return [issue("timing.camera_reading", "camera", "notice",
                  f"{lesson.label(i)}: the camera moves ({MOVE_WORDS[move]}) while the viewer reads {what}. "
                  f"{'A still camera' if fix == 'static' else 'A focus or a still camera'} keeps it easier to read.",
                  scene=i, element="camera", evidence={"key": "camera_reading", "value": move},
                  repair=_suggest(i, {"camera": fix}, "Keep the camera still" if fix == "static" else "Focus on the formula"))]


# per scene, with the scene's valid synchronization (or None): every one reads (lesson, i, plan, sync)
PLAN_CHECKS = (_sync_problems, _late_items, _camera_motion_off, _motion_not_still, _motion_choice, _transition_kind, _camera_reading)
SYNC_CHECKS = (_late_events, _orphans, _busy)


def _per_scene(lesson):
    out = []
    for i in range(lesson.count):
        plan = _planned(lesson, i)
        if not plan:
            continue
        valid = _valid_sync(plan)
        out += _safely(PLAN_CHECKS + (SYNC_CHECKS if valid else ()), lesson, i, plan, valid)
    return out


def _runs(order, same):
    """The longest runs of scenes that play one after another (order: the played scenes' indices; Phase 19: a hidden scene
    is not between its neighbours) where same(previous, current) holds: lists of scene indices."""
    runs, run = [], []
    for i in order:
        if run and same(run[-1], i):
            run.append(i)
            continue
        if run:
            runs.append(run)
        run = [i]
    if run:
        runs.append(run)
    return runs


def _camera_constant(lesson):
    if _lesson_still(lesson):
        return []  # every move is already an error of its own
    order = played(lesson)
    moving = {i: bool(_planned(lesson, i)) and _moving(_planned(lesson, i)) for i in order}
    out = []
    for run in _runs(order, lambda p, c: moving[p] and moving[c]):
        a, b = run[0], run[-1]
        if not moving[a] or len(run) < STILL_RUN:
            continue
        middle = run[(len(run) - 1) // 2]
        # info: the planner's own default (slow, subtle moves) does this on most lessons; recorded, not a problem
        out.append(issue("timing.camera_constant", "camera", "info",
                         f"Scenes {a + 1}–{b + 1} all move the camera, one after another: a still scene now and then lets the "
                         f"viewer rest.",
                         scene=a, element="camera", evidence={"key": "camera_constant", "from": a, "to": b, "count": len(run),
                                                              "middle": middle}))
    return out


def _move_explained(lesson, i, plan):
    """A camera move with a reason in the scene: its own explicit choice, the visual direction's camera intent (Phase 15),
    the layout's own move (Phase 13), or a focus (it leans in on what is taught)."""
    move = _movement(plan)
    if move == "focus" or _chosen(lesson, i, "camera"):
        return True
    intent = lesson.direction(i).get("camera_intent") or ((plan.get("direction") or {}).get("camera") if isinstance(plan.get("direction"), dict) else None)
    if K.DIRECTION_CAMERA.get(intent) == move:
        return True
    return (C.TEMPLATES.get(plan.get("template")) or {}).get("movement") == move


def _camera_switch(lesson):
    if _lesson_still(lesson):
        return []
    out = []
    order = played(lesson)
    for p, i in zip(order, order[1:]):  # the scene played before (Phase 19: a hidden scene between them is not played)
        prev, cur = _planned(lesson, p), _planned(lesson, i)
        if not (prev and cur and _moving(prev) and _moving(cur)) or _movement(prev) == _movement(cur):
            continue
        if _move_explained(lesson, i, cur):
            continue
        out.append(issue("timing.camera_switch", "camera", "notice",
                         f"{lesson.label(i)} switches from {MOVE_WORDS[_movement(prev)]} to {MOVE_WORDS[_movement(cur)]} with "
                         f"nothing in the scene calling for it: changing the camera move from scene to scene can feel restless.",
                         scene=i, element="camera", evidence={"key": "camera_switch", "found": _movement(cur),
                                                              "previous": _movement(prev)},
                         repair=_suggest(i, {"camera": "static"}, "Keep the camera still")))
    return out


def _repetition(lesson):
    def key(i):
        plan = _planned(lesson, i)
        return (plan.get("template"), _movement(plan)) if plan and _moving(plan) else None
    order = played(lesson)
    keys = {i: key(i) for i in order}
    out = []
    for run in _runs(order, lambda p, c: keys[p] is not None and keys[p] == keys[c]):
        a = run[0]
        if keys[a] is None or len(run) < REPEAT_RUN:
            continue
        third = run[REPEAT_RUN - 1]
        move = keys[a][1]
        if _chosen(lesson, third, "template") or _chosen(lesson, third, "camera"):
            out.append(issue("timing.motion_repetition", "motion", "info",
                             f"{lesson.label(third)} repeats the layout and camera move of the two scenes before it, as chosen "
                             f"for this scene.",
                             scene=third, element="layout", evidence={"key": "repetition", "count": len(run)}))
            continue
        out.append(issue("timing.motion_repetition", "motion", "notice",
                         f"{lesson.label(third)} is the third scene in a row with the same layout and {MOVE_WORDS[move]} "
                         f"({len(run)} in a row): the lesson may feel repetitive.",
                         scene=third, element="layout", evidence={"key": "repetition", "count": len(run),
                                                                  "template": keys[a][0], "movement": move},
                         repair=_suggest(third, {"camera": "static"}, "Keep the camera still here")))
    return out


def _transition_mix(lesson):
    used = sorted({_transition(_planned(lesson, i)).get("in") for i in played(lesson)} & set(C.TRANSITIONS))
    if len(used) < KINDS_NOTICE:
        return []
    return [issue("timing.transition_mix", "motion", "notice",
                  f"This lesson changes scenes in {len(used)} different ways ({', '.join(TRANSITION_WORDS[k][1] for k in used)}). "
                  f"One or two kinds feel calmer and more consistent.",
                  element="transitions", evidence={"key": "transition_mix", "kinds": used})]


def _density_consecutive(lesson):
    order = played(lesson)
    dense = {i: bool(_planned(lesson, i) or not lesson.cinematic) and not _quiz(lesson, i)
             and _intent(lesson, i).get("density") == "high" for i in order}
    out = []
    for run in _runs(order, lambda p, c: dense[p] and dense[c]):
        a, b = run[0], run[-1]
        if not dense[a] or len(run) == 1:
            continue
        out.append(issue("timing.density_consecutive", "density", "notice",
                         f"Scenes {a + 1}–{b + 1} are dense, one after another: a lighter scene between them would give the "
                         f"viewer time to catch up.",
                         scene=run[1], element="scene", evidence={"key": "density_consecutive", "from": a, "to": b,
                                                                  "count": len(run)}))
    return out


LESSON_CHECKS = (_camera_constant, _camera_switch, _repetition, _transition_mix, _density_consecutive)


def lesson_checks(lesson):
    """The checks that read the synchronization plans, the scenes' explicit choices or several scenes at once."""
    if not lesson.count:
        return []
    out = _safely([_per_scene], lesson)
    if lesson.cinematic:
        out += _safely(LESSON_CHECKS, lesson)
    else:
        out += _safely([_density_consecutive], lesson)
    return out


# ---- the registry ------------------------------------------------------------------------------------------------------

def registry(lesson):
    """{"camera": {"moves": {movement: scenes}, "still_scenes": n}, "transitions": {"lesson": kind, "used": {kind: scenes}},
    "pacing": {"total_seconds": x, "scene_seconds": [...]}}."""
    moves, used = {}, {}
    for i in range(lesson.count):
        plan = _planned(lesson, i)
        if not plan:
            continue
        move = _movement(plan)
        if move:
            moves[move] = moves.get(move, 0) + 1
        kind = _transition(plan).get("in")
        if kind in C.TRANSITIONS:
            used[kind] = used.get(kind, 0) + 1
    seconds = []
    for i in range(lesson.count):
        if hidden(lesson, i):
            seconds.append(0.0)  # Phase 19: a hidden scene is not played
            continue
        try:
            seconds.append(scene_seconds(lesson, i))
        except Exception:  # noqa: BLE001
            seconds.append(None)
    return {"camera": {"moves": dict(sorted(moves.items())), "still_scenes": moves.get("static", 0)},
            "transitions": {"lesson": _lesson_transition(lesson), "used": dict(sorted(used.items()))},
            "pacing": {"total_seconds": round(sum(s for s in seconds if s), 2), "scene_seconds": seconds}}
