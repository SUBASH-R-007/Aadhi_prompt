"""Phase 18 — quality checks for style, typography, colour and backgrounds (and the styling side of code, formulas and
captions).

Phase 17 (styles.py) is the authority on how a scene looks: these checks never re-implement a token, they compare what a
scene's saved plan carries with what the planner gives now (cinematic.scene_look = styles.plan_look, the same call
build_plan makes) and run each scene's saved colours through Phase 17's own readability guard (styles._accessibility).
Text fit reuses the composer's estimate (composer.text_need, MIN_TEXT_SCALE); plan validity is cinematic.validate_plan.
Deterministic and read-only: nothing here writes a scene, a plan or a review, generates media or calls a model. Lesson
text is truncated before it is scanned and every pattern is linear.

  scene_checks(lesson, i)  plan validity, board text fit / tiny text / dense boards, a picture behind a light style
                           (depend only on what quality.scene_fingerprint covers + settings)
  lesson_checks(lesson)    the saved look against the resolved one (stale, changed, missing, mixed styles, original look
                           mixed with a chosen style), scene style choices, readability adjustments, contrast, backgrounds
                           that fell back, background changes from scene to scene, title casing, heading levels (they read
                           the Visual Review choices, the screenplay's composition and the saved CSS, which the scene
                           fingerprint does not cover, so they run every time)
  registry(lesson)         {"style": {...}, "captions": {...}, "background": {...}}
"""
import re
from collections import Counter

import cinematic as C
import composer as K
import styles as S
from quality import _text, issue, repair
# Phase 17's own readability guard, bound once (a scene's saved colours are measured exactly as the style measures them)
from styles import _accessibility as _phase17_readable

DENSE_WORDS = 40          # a board with at least this many words ...
DENSE_PER_AREA = 240      # ... and this many words per unit of frame area is a wall of text (a full 0.62 x 0.62 board
                          # holds about 165 words at the page's body size, ~430 per unit area)
ROOM_VISUAL_WIDTH = 0.30  # a visual wider than this can give the board room (the side panel share is 0.26-0.36)
LOOK_IDENTITY = ("id", "family", "version", "variant", "legacy", "fingerprint")
BACKGROUND_WORDS = {"gradient": "the gradient", "solid": "a plain colour", "studio": "the studio", "image": "a picture",
                    "video": "a clip"}
CODE_TOKENS = frozenset(("code-accent", "output-text"))  # Phase 17 tokens that only show in a scene with code
TEXT_LIMIT = 200          # titles and other short texts are scanned only this far (every pattern below is linear too)
MINOR_WORDS = frozenset("a an the and but or nor for so yet as at by in of on to up vs via per with from into over onto than off "
                        "out".split())
CASING_ORDER = ("sentence", "title", "upper")  # ties: the first wins
CASING_WORDS = {"sentence": ("capitalises only its first word", "capitalise only the first word"),
                "title": ("capitalises every main word", "capitalise every main word"),
                "upper": ("is written in capital letters", "are written in capital letters")}
PLAN_WORDS = {
    "camera": "a camera move would cut off or cover part of the content.",
    "typography": "parts of the layout overlap or reach into the caption area.",
    "presenter": "the presenter is placed outside the frame or over other content.",
    "diagrams": "the picture is placed outside the frame or over other content.",
    "motion": "the layout asks for an animation or a transition the player does not know.",
    "timing": "an element of the layout starts or ends at an impossible moment.",
    "preview_export": "the layout is damaged and would not play as planned.",
}
PLAN_DIMENSIONS = ("camera", "typography", "presenter", "diagrams", "motion", "timing", "preview_export")


# ---- small helpers -------------------------------------------------------------------------------------------------------

def _safe(make):
    """A check that cannot decide stays silent (it never breaks the report)."""
    try:
        return list(make() or [])
    except Exception:  # noqa: BLE001
        return []


def _replan(scenes, label="Re-plan the scene"):
    return repair("auto", "presentation", {"type": "replan", "scenes": sorted(set(scenes))}, label)


def _scenes_words(indices):
    nums = [str(i + 1) for i in sorted(set(indices))]
    if len(nums) == 1:
        return f"Scene {nums[0]}"
    shown, more = nums[:6], len(nums) - 6
    if more > 0:
        return "Scenes " + ", ".join(shown) + f" and {more} more"
    return "Scenes " + ", ".join(shown[:-1]) + " and " + shown[-1]


def _planned(lesson):
    """The scenes with a saved cinematic plan (the style system styles composed scenes only)."""
    if not lesson.cinematic:
        return []
    return [i for i in range(lesson.count) if lesson.plan(i)]


def _layer(plan, layer_id):
    layers = plan.get("layers") if isinstance(plan.get("layers"), list) else []
    return next((l for l in layers if isinstance(l, dict) and l.get("id") == layer_id), None)


def _expected_look(lesson, i):
    """The look the planner gives scene i now (styles.plan_look, with the planner's own fallback)."""
    return lesson._get(("quality_style.look", i), lambda: C.scene_look(lesson.settings, lesson.scene(i))) or {}


def _lesson_look(lesson):
    """The lesson's look without any scene choice (text scale, tone, captions)."""
    return lesson._get(("quality_style.lesson_look",), lambda: C.scene_look(lesson.settings, None)) or {}


def _css_tokens(look):
    css = look.get("css") if isinstance(look.get("css"), dict) else {}
    return {k[len("--st-"):]: v for k, v in css.items() if isinstance(k, str) and k.startswith("--st-")}


def _text_scale(lesson):
    try:
        return float(_css_tokens(_lesson_look(lesson)).get("text-scale") or 1)
    except (TypeError, ValueError):
        return 1.0


def _grouped(lesson, planned, indices, rule, dimension, severity, one, every, key, *, element, extra=None, fix=None):
    """One finding for the whole lesson when every planned scene (two or more) has the same problem, else one per scene."""
    if not indices:
        return []
    extra = extra or {}
    if len(planned) >= 2 and set(indices) == set(planned):
        return [issue(rule, dimension, severity, every, scene=None, element="lesson",
                      evidence={"key": key, "scenes": len(indices), **extra}, repair=fix(indices) if fix else None)]
    return [issue(rule, dimension, severity, one(i), scene=i, element=element, evidence={"key": key, **extra},
                  repair=fix([i]) if fix else None) for i in sorted(indices)]


# ---- scene checks (memoized per scene by quality.py) ----------------------------------------------------------------------

def scene_checks(lesson, i):
    if not lesson.cinematic:
        return []
    plan = lesson.plan(i)
    if not plan:
        return []
    return (_safe(lambda: _plan_validity(lesson, i, plan)) + _safe(lambda: _text_fit(lesson, i, plan))
            + _safe(lambda: _scene_background(lesson, i, plan)))


def _problem_dimension(text):
    """Where a cinematic.validate_plan problem belongs."""
    t = _text(text, TEXT_LIMIT).lower()
    if "camera" in t or "shot" in t:
        return "camera"
    if "animation" in t or "transition" in t or "opacity" in t:
        return "motion"
    if "timing" in t or "duration" in t:
        return "timing"
    if "subtitle band" in t or "overlap" in t or "not on the frame" in t:
        ids = set(re.findall(r"\b(board|title|labels|subtitles|visual|presenter)\b", t))
        if ids & {"board", "title", "labels", "subtitles"} or not ids:
            return "typography"
        return "presenter" if "presenter" in ids else "diagrams"
    return "preview_export"


def _plan_validity(lesson, i, plan):
    try:
        problems = C.validate_plan(plan)
    except Exception:  # noqa: BLE001 - a plan the validator cannot even read is damaged
        problems = ["the plan could not be read"]
    groups = {}
    for p in problems:
        groups.setdefault(_problem_dimension(p), []).append(str(p)[:120])
    return [issue("style.invalid_plan", dim, "error", f"{lesson.label(i)}: {PLAN_WORDS[dim]} Re-plan the scene.",
                  scene=i, element=dim, evidence={"key": dim, "found": groups[dim]}, repair=_replan([i]))
            for dim in PLAN_DIMENSIONS if dim in groups]


def _room_suggestion(plan, i):
    """A composition change that gives the board more room (applied by the user in Visual Review), or None."""
    presenter = _layer(plan, "presenter")
    if presenter and (plan.get("presenter") or {}).get("shown") and presenter.get("role") != "mascot" \
            and presenter.get("placement") not in ("small", "pip"):
        return repair("suggest", "composition", {"type": "composition", "scene": i, "overrides": {"presenter_size": "small"}},
                      "Make the presenter smaller")
    visual = _layer(plan, "visual")
    box = (visual or {}).get("box") or {}
    if visual and visual.get("role") != "full_canvas" and C.valid_box(box) and box["w"] > ROOM_VISUAL_WIDTH:
        return repair("suggest", "composition", {"type": "composition", "scene": i, "overrides": {"visual_size": "side_panel"}},
                      "Make the picture narrower")
    return None


def _text_fit(lesson, i, plan):
    """The board's text in its box (the composer's own estimate), at the page's size and at the style's text size."""
    board = _layer(plan, "board")
    box = (board or {}).get("box") or {}
    if not board or not C.valid_box(box):
        return []
    role = board.get("role")
    style = role if role in C.BOARD_STYLES else (C.TEMPLATES.get(plan.get("template")) or {}).get("board")
    if not style or style == "visual":
        return []
    facts = lesson.facts(i)
    if not facts:
        return []
    intent = {"type": lesson.kind(i), "board": facts}
    needed, available, width_scale = K.text_need(intent, box, style)
    fit = round(min(1.0, available / needed, width_scale), 3) if available else 0.0
    out = []
    if fit < K.MIN_TEXT_SCALE:
        fix = _room_suggestion(plan, i)
        out.append(issue("style.text_overflow", "typography", "warning",
                         f"{lesson.label(i)} has too much text for the space it gets: it would have to shrink to {int(fit * 100)} % "
                         f"(the page never goes below {int(K.MIN_TEXT_SCALE * 100)} %), so part of it may be cut off. "
                         + ("Giving the board more room would help." if fix else "Shorten the text or split the scene in two."),
                         scene=i, element="board", evidence={"key": "text_fit", "fit": fit, "minimum": K.MIN_TEXT_SCALE,
                                                             "words": facts.get("words"), "board": style},
                         repair=fix))
        return out
    scale = _text_scale(lesson)
    if scale > 1.0 + 1e-9:
        # the style's text size (family x the lesson's text size choice) makes the same text take more room; the page's
        # fit then shrinks it, never below 80 %
        wider = {"type": intent["type"], "board": dict(facts, code_width=int(round((facts.get("code_width") or 0) * scale)))}
        _n, _a, width_at_scale = K.text_need(wider, box, style)
        fit_at_scale = round(min(1.0, available / (needed * scale), width_at_scale), 3) if available else 0.0
        if fit_at_scale < K.MIN_TEXT_SCALE:
            out.append(issue("style.tiny_text", "typography", "warning",
                             f"{lesson.label(i)}: at the lesson's text size the board's text no longer fits its space: it would be "
                             f"squeezed to the smallest size the page allows and could still be cut off.",
                             scene=i, element="board", evidence={"key": "text_fit_at_style_size", "fit": fit_at_scale,
                                                                 "text_scale": scale, "minimum": K.MIN_TEXT_SCALE},
                             repair=_room_suggestion(plan, i)))
            return out
    area = box["w"] * box["h"]
    words = int(facts.get("words") or 0)
    if words >= DENSE_WORDS and area > 0 and words / area >= DENSE_PER_AREA:
        out.append(issue("style.dense_board", "density", "notice",
                         f"{lesson.label(i)} packs a lot of words into a small board (about {words}): the text is crowded in the "
                         f"space it has.",
                         scene=i, element="board", evidence={"key": "words_per_area", "words": words, "area": round(area, 3),
                                                             "per_area": round(words / area)}))
    return out


def _scene_background(lesson, i, plan):
    bg = plan.get("background") if isinstance(plan.get("background"), dict) else {}
    out = []
    if bg.get("type") in ("image", "video") and _lesson_look(lesson).get("tone") == "light":
        out.append(issue("style.picture_background_light", "background", "info",
                         f"{lesson.label(i)} shows {BACKGROUND_WORDS[bg['type']]} behind a light style: the title and labels sit "
                         f"on the style's own card so they stay readable.",
                         scene=i, element="background", evidence={"key": "picture_background_light", "kind": bg["type"]}))
    return out


# ---- lesson checks (run every time; linear) ----------------------------------------------------------------------------------

def lesson_checks(lesson):
    return (_safe(lambda: _look_consistency(lesson)) + _safe(lambda: _scene_styles(lesson))
            + _safe(lambda: _adjustments(lesson)) + _safe(lambda: _contrast(lesson))
            + _safe(lambda: _background_fallbacks(lesson)) + _safe(lambda: _background_changes(lesson))
            + _safe(lambda: _title_casing(lesson))
            + _safe(lambda: _heading_levels(lesson)))


def _style_key(look):
    return f"{look.get('id') or str(look.get('family')) + '@' + str(look.get('version'))}" + (f":{look['variant']}" if look.get("variant") else "")


def _style_name(look, versions_differ=False):
    if look.get("legacy"):
        return "the lesson's original look"
    family, version = look.get("family"), look.get("version")
    label = ((S.FAMILY_DEFS.get(family) or {}).get(version) or {}).get("label") if isinstance(family, str) else None
    label = label or str(look.get("label") or family or "another style")[:40]
    return f"“{label}”" + (f" (version {version})" if versions_differ else "")


def _look_consistency(lesson):
    """Every saved look is the one the lesson's settings (and the scene's own choices) give now."""
    planned = _planned(lesson)
    if not planned:
        return []
    stored = {i: lesson.look(i) for i in planned}
    with_look = [i for i in planned if stored[i]]
    out = []
    now = _lesson_look(lesson)
    now_name = _style_name(now)

    missing = [i for i in planned if not stored[i]]
    if missing and (with_look or lesson.settings.get("style")):
        out += _grouped(lesson, planned, missing, "style.missing_look", "style", "error",
                        lambda i: (f"{lesson.label(i)} carries no style " + ("while the other scenes do" if with_look else f"(the lesson's is {now_name})")
                                   + ": it would play in the default look. Re-plan it."),
                        f"No scene carries the lesson's style ({now_name}) yet: re-plan the lesson so the preview and the export show it.",
                        "missing_look", element="look", fix=lambda s: _replan(s, "Re-plan the scenes" if len(s) > 1 else "Re-plan the scene"))

    covered = set()
    keys = {i: _style_key(stored[i]) for i in with_look}
    distinct = sorted(set(keys.values()))
    if len(distinct) > 1:
        targets = [i for i in with_look if keys[i] != _style_key(_expected_look(lesson, i))]
        if not targets:  # cannot happen with one lesson style; the minority then
            counts = Counter(keys.values())
            rare = min(distinct, key=lambda k: (counts[k], k))
            targets = [i for i in with_look if keys[i] == rare]
        by_key, versions = {}, {}
        for i in with_look:
            by_key.setdefault(keys[i], stored[i])
            versions.setdefault(str(stored[i].get("family")), set()).add(str(stored[i].get("version")))
        names = [_style_name(by_key[k], len(versions.get(str(by_key[k].get("family")), ())) > 1) for k in distinct]
        n = len(targets)
        out.append(issue("style.mixed_styles", "style", "error",
                         f"The lesson mixes styles ({', '.join(dict.fromkeys(names))}): {_scenes_words(targets)} "
                         f"{'does' if n == 1 else 'do'} not use the lesson's style, {now_name}. Re-plan {'it' if n == 1 else 'them'} "
                         f"so every scene looks the same.",
                         scene=None, element="lesson", evidence={"key": "mixed_styles", "styles": distinct, "lesson_style": _style_key(now),
                                                                 "scenes": [t + 1 for t in targets]},
                         repair=_replan(targets, "Re-plan these scenes" if n > 1 else "Re-plan the scene")))
        covered |= set(targets)
    elif len({bool(stored[i].get("legacy")) for i in with_look}) > 1:
        targets = [i for i in with_look if bool(stored[i].get("legacy")) != bool(_expected_look(lesson, i).get("legacy"))]
        n = len(targets)
        if targets:
            out.append(issue("style.legacy_mix", "style", "error",
                             f"Some scenes keep the lesson's original look while others use the chosen style: re-plan "
                             f"{_scenes_words(targets)} so {'it matches' if n == 1 else 'they all match'} {now_name}.",
                             scene=None, element="lesson", evidence={"key": "legacy_mix", "scenes": [t + 1 for t in targets],
                                                                     "lesson_legacy": bool(now.get("legacy"))},
                             repair=_replan(targets, "Re-plan these scenes" if n > 1 else "Re-plan the scene")))
            covered |= set(targets)

    stale, changed = [], []
    for i in with_look:
        if i in covered:
            continue
        expected = _expected_look(lesson, i)
        if not expected:
            continue
        look = stored[i]
        if any(look.get(k) != expected.get(k) for k in LOOK_IDENTITY):
            stale.append(i)
        elif look.get("css") != expected.get("css") or look.get("tone") != expected.get("tone") or look.get("prefs") != expected.get("prefs"):
            changed.append(i)
    out += _grouped(lesson, planned, stale, "style.stale_look", "style", "error",
                    lambda i: f"{lesson.label(i)} was styled with older style choices: re-plan it so it shows the lesson's current style.",
                    "Every scene was styled with older style choices (the lesson's style changed since): re-plan the lesson so the "
                    "preview and the export show the current style.",
                    "stale_look", element="look", fix=lambda s: _replan(s, "Re-plan the scenes" if len(s) > 1 else "Re-plan the scene"))
    out += _grouped(lesson, planned, changed, "style.look_css", "style", "error",
                    lambda i: f"In {lesson.label(i)} the saved colours and lettering do not match the scene's style (they were changed "
                              f"outside the planner): re-plan it.",
                    "The saved colours and lettering of every scene do not match the lesson's style (they were changed outside the "
                    "planner): re-plan the lesson.",
                    "look_css", element="look", fix=lambda s: _replan(s, "Re-plan the scenes" if len(s) > 1 else "Re-plan the scene"))
    return out


def _scene_styles(lesson):
    """A scene's own accent / background (Visual Review) is intentional; many different scene accents are a notice."""
    if not lesson.cinematic:
        return []
    out, accents = [], {}
    lesson_accent = S.clean_overrides(lesson.settings.get("style_overrides")).get("accent") if lesson.settings.get("style") else None
    for i in range(lesson.count):
        own = S.scene_overrides(lesson.scene(i))
        if not own:
            continue
        parts = ([f"accent colour ({own['accent']})"] if own.get("accent") else []) + \
                ([f"background ({own['background']})"] if own.get("background") else [])
        out.append(issue("style.scene_style", "style", "info",
                         f"{lesson.label(i)} uses its own {' and '.join(parts)}, chosen in Visual Review.",
                         scene=i, element="style", evidence={"key": "scene_style", "accent": own.get("accent"),
                                                             "background": own.get("background")}))
        if own.get("accent") and own["accent"] != lesson_accent:
            accents[i] = own["accent"]
    if len(set(accents.values())) >= 3:
        for i in sorted(accents):
            out.append(issue("style.many_accents", "colour", "notice",
                             f"Many scenes use their own accent colour ({_scenes_words(accents)}): {lesson.label(i)} uses "
                             f"{accents[i]}. Using the lesson's accent would make the lesson look more consistent.",
                             scene=i, element="accent", evidence={"key": "many_accents", "accent": accents[i],
                                                                  "accents": sorted(set(accents.values()))},
                             repair=repair("suggest", "presentation",
                                           {"type": "scene_style", "scene": i, "overrides": {"style_accent": "auto"}},
                                           "Use the lesson's accent colour")))
    return out


def _adjustments(lesson):
    """Colours Phase 17 had to make darker or lighter to stay readable (it did: a notice, in its own plain words)."""
    planned = _planned(lesson)
    found = {}
    for i in planned:
        adjusted = lesson.look(i).get("adjustments")
        if isinstance(adjusted, list) and adjusted:
            found.setdefault(tuple(str(a)[:120] for a in adjusted[:8]), []).append(i)
    def fix(scenes):  # a scene's own accent made it need an adjustment: the lesson's accent is the suggestion
        if len(scenes) == 1 and S.scene_overrides(lesson.scene(scenes[0])).get("accent"):
            return repair("suggest", "presentation", {"type": "scene_style", "scene": scenes[0], "overrides": {"style_accent": "auto"}},
                          "Use the lesson's accent colour")
        return None

    out = []
    for words, indices in sorted(found.items(), key=lambda kv: kv[1][0]):
        said = "; ".join(words)
        out += _grouped(lesson, planned, indices, "style.readability_adjusted", "colour", "notice",
                        lambda i, said=said: f"{lesson.label(i)}: kept readable — {said}.",
                        f"Kept readable in every scene — {said}.", "adjusted", element="colours",
                        extra={"adjustments": list(words)}, fix=fix)
    return out


ADJUSTED = " made darker or lighter to stay readable"  # the ending of Phase 17's plain words


def unreadable(css, tone):
    """(token names, Phase 17's plain words) of a look's saved CSS that fails Phase 17's own readability guard
    (styles._accessibility run on a copy of its tokens); ((), []) when every pair reads."""
    tokens = {k[len("--st-"):]: v for k, v in css.items() if isinstance(k, str) and k.startswith("--st-")}
    fixed = dict(tokens)
    words = _phase17_readable(fixed, tone if tone in S.TONES else "dark")
    changed = tuple(sorted(k for k in fixed if fixed[k] != tokens.get(k) and not k.endswith("-rgb")))
    return changed, [str(w)[:120] for w in (words or [])]


def _contrast(lesson):
    """Each scene's saved colours through Phase 17's readability guard (it never fires on the built-in styles). A re-plan
    is offered only when the saved colours are not what the style gives now (a stale look); when the style itself gives
    them, re-planning cannot help: the error stays, with no repair."""
    planned = _planned(lesson)
    groups = {}  # (what shows, stale) -> {"scenes": [...], "words": [...], "dimension": ...}
    for i in planned:
        try:
            look = lesson.look(i)
            css = look.get("css")
            if not isinstance(css, dict) or not css:
                continue  # no saved look: style.missing_look
            changed, words = unreadable(css, look.get("tone") if look.get("tone") in S.TONES else _expected_look(lesson, i).get("tone"))
            if not words:
                continue
            shows = set(changed)
            if not lesson.facts(i).get("code_blocks"):
                shows -= CODE_TOKENS
            if not str(lesson.scene(i).get("narration") or "").strip():
                shows.discard("caption-text")
            if not lesson.scene(i).get("title"):
                shows.discard("title-text")
            if changed and not shows:
                continue  # only colours this scene does not show
            stale = css != _expected_look(lesson, i).get("css")
            dimension = "code" if shows and shows <= CODE_TOKENS else ("typography" if shows == {"caption-text"} else "colour")
            group = groups.setdefault((",".join(sorted(shows)), stale), {"scenes": [], "words": words, "dimension": dimension})
            group["scenes"].append(i)
        except Exception:  # noqa: BLE001 - a look that cannot be measured stays silent
            continue
    out = []
    for (target, stale), g in sorted(groups.items(), key=lambda kv: (kv[1]["scenes"][0], kv[0])):
        said = "; ".join(w[:-len(ADJUSTED)] if w.endswith(ADJUSTED) else w for w in g["words"])
        after = (" Re-plan the scene." if stale else
                 " The style itself gives these colours, so re-planning cannot help: choose another accent colour or style.")
        out += _grouped(lesson, planned, g["scenes"], "style.low_contrast", g["dimension"], "error",
                        lambda i, said=said, after=after: f"{lesson.label(i)}: some colours would be hard to read ({said})." + after,
                        f"In every scene some colours would be hard to read ({said})." + after.replace("the scene", "the lesson"),
                        "contrast", element=target or "colours",
                        extra={"target": target, "adjustments": g["words"], "stale": stale},
                        fix=(lambda sc: _replan(sc, "Re-plan the scenes" if len(sc) > 1 else "Re-plan the scene")) if stale else None)
    return out


def _background_wish(lesson, i):
    """The background the scene itself asks for (a Visual Review choice, else the screenplay's composition), or None."""
    scene = lesson.scene(i)
    chosen = C.review_overrides(scene).get("background")
    if isinstance(chosen, str) and chosen not in ("", "auto"):
        return chosen[:20]
    composition, _warnings = C.clean_composition(scene.get("composition"))
    wanted = (composition.get("background") or {}).get("type")
    return wanted if wanted not in (None, "auto") else None


def _explicit_background(lesson, i):
    return _background_wish(lesson, i) is not None


def _background_fallbacks(lesson):
    """A chosen background that could not be used (the gradient shows instead): one finding when the lesson's own setting
    fell back in every scene, else one per scene."""
    with_backdrop, fell = [], {}
    for i in _planned(lesson):
        bg = lesson.plan(i).get("background")
        if not isinstance(bg, dict) or not isinstance(bg.get("type"), str) or bg["type"] == "canvas":
            continue  # a full-canvas visual is the frame itself: it has no backdrop to fall back
        with_backdrop.append(i)
        if bg.get("fallback"):
            style = lesson.plan(i).get("style")
            wish = _background_wish(lesson, i)
            fell[i] = ("scene", wish) if wish else ("lesson", style.get("background") if isinstance(style, dict) else None)
    if not fell:
        return []
    advice = "Pick another background or add the missing picture."
    if len(with_backdrop) >= 2 and set(fell) == set(with_backdrop) and len(set(fell.values())) == 1:
        indices = sorted(fell)
        return [issue("style.background_fallback", "background", "warning",
                      f"The lesson's chosen background could not be used in any scene ({_scenes_words(indices)}), so the plain "
                      f"gradient is shown instead. {advice}",
                      scene=None, element="lesson", evidence={"key": "background_fallback", "wanted": fell[indices[0]][1],
                                                              "count": len(indices), "scenes": [i + 1 for i in indices]})]
    return [issue("style.background_fallback", "background", "warning",
                  f"{lesson.label(i)}: the chosen background could not be used, so the plain gradient is shown instead. {advice}",
                  scene=i, element="background", evidence={"key": "background_fallback", "wanted": fell[i][1], "from": fell[i][0]})
            for i in sorted(fell)]


def _background_changes(lesson):
    """The backdrop changing from one scene to the next with nothing asking for it."""
    out = []
    prev = None          # (index, kind, background, presenter type) of the last planned scene with a backdrop
    before_prev = None   # the kind before the last reported change (a lone odd scene is reported once, not on the way back)
    reported_prev = False
    for i in _planned(lesson):
        plan = lesson.plan(i)
        bg = plan.get("background") if isinstance(plan.get("background"), dict) else {}
        kind = bg.get("type")
        if not isinstance(kind, str) or kind == "canvas":
            continue  # a full-canvas visual is the frame itself
        presenter = (plan.get("presenter") or {}).get("type") if isinstance(plan.get("presenter"), dict) else None
        reported = False
        if prev is not None and kind != prev[1]:
            p_index, p_kind, p_bg, p_presenter = prev
            if bg.get("fallback") or p_bg.get("fallback"):
                pass  # the background that fell back is its own warning
            elif _explicit_background(lesson, i):
                out.append(issue("style.background_choice", "background", "info",
                                 f"{lesson.label(i)} uses its own background ({BACKGROUND_WORDS.get(kind, kind)}), chosen for this scene.",
                                 scene=i, element="background", evidence={"key": "background_choice", "kind": kind, "previous": p_kind}))
            elif _explicit_background(lesson, p_index):
                pass  # back to the lesson's background after a scene that chose its own
            elif "studio" in (kind, p_kind) and presenter != p_presenter:
                out.append(issue("style.background_presenter", "background", "info",
                                 f"The background of {lesson.label(i)} differs from the scene before because another presenter appears "
                                 f"(Aadhi is filmed in his studio).",
                                 scene=i, element="background", evidence={"key": "background_presenter", "kind": kind, "previous": p_kind}))
            elif reported_prev and kind == before_prev:
                pass  # the way back from a lone odd scene (already reported)
            else:
                out.append(issue("style.background_change", "background", "notice",
                                 f"The background of {lesson.label(i)} ({BACKGROUND_WORDS.get(kind, kind)}) differs from the one of Scene {p_index + 1} "
                                 f"({BACKGROUND_WORDS.get(p_kind, p_kind)}) though neither scene asks for it. Re-plan the lesson or "
                                 f"choose the background in Visual Review.",
                                 scene=i, element="background", evidence={"key": "background_change", "kind": kind, "previous": p_kind}))
                reported = True
                before_prev = p_kind
        reported_prev = reported
        prev = (i, kind, bg, presenter)
    return out


_SEGMENTS = re.compile(r"[:;!?.—–]|\s-\s")  # fixed-width alternatives only: linear
_WORD = re.compile(r"[^\W\d_][\w'’-]*")


def title_casing(title):
    """'sentence' | 'title' | 'upper' | None (cannot tell: one word, proper nouns only, or an ambiguous mixture)."""
    text = _text(str(title)[:4 * TEXT_LIMIT] if isinstance(title, str) else "", TEXT_LIMIT)
    tokens = _WORD.findall(text)
    if len([t for t in tokens if len(t) >= 2]) >= 2 and all(t.isupper() for t in tokens) and sum(len(t) for t in tokens) >= 6:
        return "upper"
    caps = lower = 0
    for segment in _SEGMENTS.split(text):
        words = _WORD.findall(segment)
        for word in words[1:]:  # the first word of a title (and after a colon) is capitalised in every style
            if len(word) < 2 or (word.isupper() and len(word) >= 2) or (word[1:] != word[1:].lower()):
                continue  # 'I', acronyms (DNA), internal capitals (iPhone): no signal
            if word[0].isupper():
                caps += 1
            elif word.lower() not in MINOR_WORDS:
                lower += 1
    if lower >= 1 and caps <= 1:
        return "sentence"
    if lower == 0 and caps >= 2:
        return "title"
    return None


def _title_casing(lesson):
    found = {i: title_casing(lesson.scene(i).get("title")) for i in range(lesson.count)}
    found = {i: c for i, c in found.items() if c}
    if len(set(found.values())) < 2:
        return []
    counts = Counter(found.values())
    usual = max(CASING_ORDER, key=lambda c: (counts.get(c, 0), -CASING_ORDER.index(c)))
    return [issue("style.title_casing", "typography", "notice",
                  f"{lesson.label(i)} {CASING_WORDS[c][0]}, unlike most titles in the lesson, which {CASING_WORDS[usual][1]}.",
                  scene=i, element="title", evidence={"key": "title_casing", "expected": usual, "found": c})
            for i, c in sorted(found.items()) if c != usual]


_HEADING = re.compile(r"<h([1-6])\b", re.I)


def _heading_levels(lesson):
    """Scenes of the same kind whose boards start with a different heading level."""
    levels = {}
    for i in range(lesson.count):
        html = lesson.scene(i).get("html")
        found = _HEADING.findall(html) if isinstance(html, str) else []
        if found:
            levels[i] = min(int(h) for h in found)
    by_kind = {}
    for i in levels:
        by_kind.setdefault(lesson.kind(i), []).append(i)
    out = []
    for kind in sorted(by_kind):
        indices = by_kind[kind]
        counts = Counter(levels[i] for i in indices)
        if len(counts) < 2:
            continue
        usual = max(counts, key=lambda lv: (counts[lv], -lv))
        for i in indices:
            if levels[i] != usual:
                out.append(issue("style.heading_levels", "typography", "notice",
                                 f"The board of {lesson.label(i)} starts with a {'larger' if levels[i] < usual else 'smaller'} heading than "
                                 f"similar scenes in the lesson: headings change size from scene to scene.",
                                 scene=i, element="board", evidence={"key": "heading_level", "expected": f"h{usual}", "found": f"h{levels[i]}"}))
    return sorted(out, key=lambda f: f["scene"])


# ---- registry ------------------------------------------------------------------------------------------------------------

def registry(lesson):
    look = _lesson_look(lesson)
    overrides = look.get("overrides") if isinstance(look.get("overrides"), dict) else {}
    accents = {}
    for i in range(lesson.count):
        accent = S.scene_overrides(lesson.scene(i)).get("accent")
        if accent:
            accents[str(i)] = accent
    kinds = []
    for i in _planned(lesson):
        bg = lesson.plan(i).get("background")
        if isinstance(bg, dict) and isinstance(bg.get("type"), str):
            kinds.append(bg["type"][:20])
    return {"style": {"id": look.get("id"), "family": look.get("family"), "version": look.get("version"),
                      "legacy": look.get("legacy"), "tone": look.get("tone"), "accent": overrides.get("accent") or "default",
                      "scene_accents": accents},
            "captions": {"style": (look.get("prefs") or {}).get("caption"), "size": overrides.get("caption_size") or "standard"},
            "background": {"kinds": sorted(set(kinds)), "counts": dict(sorted(Counter(kinds).items()))}}
