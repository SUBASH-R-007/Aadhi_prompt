"""Phase 18 — the Quality & Consistency Engine.

Evaluates a whole lesson (its scenes with the plans Phases 12–17 produced) and each scene against the project's own
contracts: presenter, style, typography, colour, terminology and educational consistency, formulas, code, diagrams,
camera, motion, background, density, timing, media and preview/export consistency. It is deterministic (no score from a
model), read-only (it never writes the lesson, a Visual Review record, a plan or a media file, never generates and never
hooks into recovery) and versioned: a report names the rules that produced it and carries fingerprints, so a report made
from older inputs or older rules is known to be stale.

Every finding says what was checked, what failed, why, where (scene, element), how serious it is, whether it can be
repaired, how (a repair is classified by what it would change: presentation, composition, visual, presenter, timing or
content) and whether it was. Only repairs that are deterministic, local and leave approvals and media untouched are
offered as automatic ("re-plan": the existing planning recomputes a derived plan); everything else is a suggestion the
user applies through the existing Visual Review actions, or a note for the author.

The checks live in four families (quality_style, quality_media, quality_education, quality_timing); this module owns the
contract, the shared lesson context, the registry, the fingerprints, the report and the API.
"""
import hashlib
import json
import math
import re
import threading
import time
from collections import OrderedDict

RULES_VERSION = 1
RULES = f"quality_rules@{RULES_VERSION}"
MAX_SCENES = 200

# ---- severity (deterministic meaning; each rule declares one) ----------------------------------------------------------
# info     an intentional or harmless variation, recorded for completeness (never counted as a problem)
# notice   a possible inconsistency worth a look
# warning  a readability or consistency problem that lowers the lesson's quality
# error    a concrete contract violation (a plan that breaks its own rules, a missing file, a stale plan)
# blocking the lesson cannot be exported as reviewed (e.g. a visual the lesson shows is gone)
SEVERITIES = ("info", "notice", "warning", "error", "blocking")
RANK = {s: i for i, s in enumerate(SEVERITIES)}

DIMENSIONS = OrderedDict([
    ("presenter", "Presenter"), ("style", "Style"), ("typography", "Text"), ("colour", "Colours"),
    ("terminology", "Terminology"), ("education", "Concepts"), ("formulas", "Formulas"), ("code", "Code"),
    ("diagrams", "Diagrams"), ("camera", "Camera"), ("motion", "Motion"), ("background", "Backgrounds"),
    ("density", "Busy scenes"), ("timing", "Timing"), ("media", "Pictures and clips"), ("preview_export", "Preview and export"),
])
REPAIR_KINDS = ("none", "suggest", "auto")
# what a repair would change (Visual Review's approvals depend on this): presentation and timing are derived plans
# (approval-safe), composition / visual / presenter go through Visual Review, content is the author's
REPAIR_CLASSES = ("presentation", "timing", "composition", "visual", "presenter", "content")
ACTION_TYPES = ("replan", "scene_style", "composition", "none")


class Invalid(ValueError):
    """A malformed finding (a check that broke the contract)."""


def _norm(value, depth=0):
    """Numbers as the page keeps them: a plan that went through the browser comes back with 1.0 written as 1 (JSON in
    JavaScript), so whole floats become ints and the rest are rounded (6 places) before anything is hashed; an infinite
    or undefined number is not a number; nesting deeper than 40 levels is cut."""
    if depth > 40:
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        r = round(value, 6)
        return int(r) if r.is_integer() else r
    if isinstance(value, dict):
        return {str(k): _norm(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v, depth + 1) for v in value]
    return value


def _sha(value, n=16):
    return hashlib.sha256(json.dumps(_norm(value), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:n]


def _text(value, limit=300):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def issue(rule, dimension, severity, message, *, scene=None, element=None, evidence=None, repair=None):
    """One finding. rule: 'family.what' (stable id); message: plain words for an educator (Scene numbers 1-based);
    evidence: a small structured dict (what was compared); repair: repair(...) or None (nothing to repair)."""
    if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", str(rule or "")):
        raise Invalid(f"rule id: {rule!r}")
    if dimension not in DIMENSIONS:
        raise Invalid(f"dimension: {dimension!r}")
    if severity not in RANK:
        raise Invalid(f"severity: {severity!r}")
    if scene is not None and not (isinstance(scene, int) and not isinstance(scene, bool) and 0 <= scene < MAX_SCENES):
        raise Invalid(f"scene: {scene!r}")
    rep = repair or {"kind": "none", "class": None, "action": {"type": "none"}, "label": None}
    out = {"rule": rule, "dimension": dimension, "severity": severity, "scene": scene,
           "element": _text(element, 60) or None, "message": _text(message, 400),
           "evidence": _clean_evidence(evidence or {}), "repair": rep,
           "repair_status": "available" if rep["kind"] in ("auto", "suggest") else "not_repairable"}
    out["id"] = _sha([rule, scene, out["element"], _evidence_key(out["evidence"])], 12)
    return out


def repair(kind, klass, action, label):
    """How a finding can be repaired. kind: suggest | auto (auto only for approval-safe derived plans: class
    presentation or timing with a 'replan' action); action: {'type': 'replan', 'scenes': [i]} | {'type':
    'scene_style', 'scene': i, 'overrides': {'style_accent': ...}} | {'type': 'composition', 'scene': i, 'overrides':
    {...}} (applied by the user through Visual Review)."""
    if kind not in ("suggest", "auto"):
        raise Invalid(f"repair kind: {kind!r}")
    if klass not in REPAIR_CLASSES:
        raise Invalid(f"repair class: {klass!r}")
    if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
        raise Invalid(f"repair action: {action!r}")
    if kind == "auto" and not (klass in ("presentation", "timing") and action["type"] == "replan"):
        raise Invalid("only a re-plan of a derived plan (presentation / timing) may be automatic")
    return {"kind": kind, "class": klass, "action": json.loads(json.dumps(action, default=str)), "label": _text(label, 80)}


def _clean_evidence(evidence):
    """Evidence is data (shown in the debug view): plain JSON values only, bounded."""
    def clean(v, depth=0):
        if depth > 3:
            return None
        if isinstance(v, float) and not math.isfinite(v):
            return None  # an infinite / undefined number from crafted data: never echoed (not valid JSON)
        if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            return v[:200]
        if isinstance(v, (list, tuple)):
            return [clean(x, depth + 1) for x in list(v)[:12]]
        if isinstance(v, dict):
            return {str(k)[:40]: clean(x, depth + 1) for k, x in list(v.items())[:16]}
        return str(v)[:200]
    return clean(evidence) if isinstance(evidence, dict) else {}


def _evidence_key(evidence):
    return {k: evidence[k] for k in sorted(evidence) if k in ("key", "term", "value", "expected", "found", "slot", "target")}


# ---- the lesson context (shared by every check; each derivation computed once) --------------------------------------------

class Lesson:
    """What the checks read: the scenes and their plans, the lesson settings, the concept map, and (on the server, when
    the lesson's media could be looked up) the media metadata. Derivations reuse the existing modules."""

    def __init__(self, scenes, settings=None, concept_map=None, media=None, recomposed=None, assist=False):
        self.scenes = [_bounded(s) for s in (scenes if isinstance(scenes, list) else [])][:MAX_SCENES]
        self.assist = bool(assist)  # the optional AI assistance runs only when the request asks for it
        self.count = len(self.scenes)
        self.settings = settings if isinstance(settings, dict) else {}
        cmap = concept_map.get("concept_map") if isinstance(concept_map, dict) else concept_map
        self.concept_map = [c for c in (cmap if isinstance(cmap, list) else []) if isinstance(c, dict)][:200]
        self.media = media  # None: not checked (no server lookup); else quality_media's metadata
        self.recomposed = recomposed  # None: not recomputed; else the plans the current planning gives (preview = export)
        self._memo = {}

    def _get(self, key, make):
        if key not in self._memo:
            try:
                self._memo[key] = make()
            except Exception:  # noqa: BLE001 - a derivation that fails on odd data reads as "nothing known"
                self._memo[key] = None
        return self._memo[key]

    @property
    def cinematic(self):
        return self.settings.get("mode") == "cinematic"

    def scene(self, i):
        return self.scenes[i] if 0 <= i < self.count else {}

    def plan(self, i):
        p = self.scene(i).get("cinematic_plan")
        return p if isinstance(p, dict) else {}

    def look(self, i):
        style = self.plan(i).get("style")
        look = style.get("look") if isinstance(style, dict) else None
        return look if isinstance(look, dict) else {}

    def tokens(self, i):
        """The effective style tokens of scene i, resolved now by Phase 17 (the authority) for the lesson's settings."""
        import styles
        return self._get(("tokens", i), lambda: styles.resolve(self.settings, self.scene(i))["tokens"]) or {}

    def facts(self, i):
        import scene_intent
        return self._get(("facts", i), lambda: scene_intent.board_facts(self.scene(i).get("html") or "")) or {}

    def understanding(self, i):
        import visual_director
        titles = {str(c.get("id")): _text(c.get("title"), 120) for c in self.concept_map if c.get("id")}
        return self._get(("understand", i), lambda: visual_director.understand(self.scene(i), i, self.count, self.settings, titles)) or {}

    def narration(self, i):
        import sync_director
        return self._get(("narration", i), lambda: sync_director.narration_model(self.scene(i).get("narration"))) or {}

    def direction(self, i):
        d = self.scene(i).get("visual_direction")
        return d if isinstance(d, dict) else {}

    def visual_plan(self, i):
        v = self.scene(i).get("visual_plan")
        return v if isinstance(v, dict) else {}

    def presenter_plan(self, i):
        p = self.scene(i).get("presenter_plan")
        return p if isinstance(p, dict) else {}

    def reviews(self, i):
        r = self.scene(i).get("visual_review")
        return r if isinstance(r, dict) else {}

    def kind(self, i):
        return _text(self.scene(i).get("type") or "content", 40)

    def label(self, i):
        """'Scene 4 (“Inside the leaf”)' for messages."""
        title = _text(self.scene(i).get("title"), 60)
        return f"Scene {i + 1}" + (f" (“{title}”)" if title else "")


# ---- fingerprints -------------------------------------------------------------------------------------------------------

def _slots(visual_plan):
    return {slot: {k: p.get(k) for k in ("source", "selection", "asset_id", "media", "requires_generation", "would_require", "error",
                                         "review_status")}
            for slot, p in sorted(visual_plan.items()) if isinstance(p, dict)}


# parts of a scene that change without changing what it shows or means: review timestamps, AI records, notes, signed
# links (they expire), the page's own response bookkeeping
# (and a quality report kept with a scene: it never makes its own report stale)
VOLATILE = frozenset(("reviewed_at", "url", "ai", "notes", "httpStatus", "created_at", "updated_at", "quality"))
TEXT_LIMITS = {"title": 300, "subtitle": 300, "narration": 12000, "html": 30000}
SCENE_CHECK_BUDGET = 20.0   # seconds for the scenes' own checks in one report (a crafted lesson cannot hold a worker)
MAX_LESSON_TEXT = 3_000_000  # characters of HTML + narration a report accepts (413 above)


def _bounded(scene):
    """A shallow copy of a scene with its long texts bounded (the input is never changed)."""
    if not isinstance(scene, dict):
        return {}
    out = dict(scene)
    for key, limit in TEXT_LIMITS.items():
        if isinstance(out.get(key), str) and len(out[key]) > limit:
            out[key] = out[key][:limit]
    return out


def _stable(value, depth=0):
    """value without its volatile parts (at any depth)."""
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {k: _stable(v, depth + 1) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_stable(v, depth + 1) for v in value]
    return value


def scene_fingerprint(lesson, i):
    """What scene i's own checks depend on: the whole scene as planned (its texts, plans, direction, visuals, presenter,
    Visual Review records), its place in the lesson and every lesson setting — never review timestamps, AI records, notes or
    signed links. A change re-evaluates that scene; its findings are reused while it is unchanged."""
    return _sha({"rules": RULES, "i": i, "n": lesson.count, "scene": _stable(lesson.scene(i)), "settings": _settings_key(lesson.settings)})


def _settings_key(settings):
    return _stable(settings if isinstance(settings, dict) else {})


def media_digest(media):
    if not isinstance(media, dict):
        return None
    return _sha({k: media[k] for k in sorted(media) if k in ("assets", "runs")})


def _asset_ids(scene):
    """The asset ids a scene refers to (its visuals, presenter clip, background, review choices)."""
    ids = set()

    def walk(v, depth=0):
        if depth > 8:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "asset_id" and isinstance(x, str):
                    ids.add(x)
                else:
                    walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)
    walk({k: scene.get(k) for k in ("visual_plan", "presenter_plan", "cinematic_plan", "visual_review")})
    return ids


def scene_media_digest(lesson, i):
    """The media metadata scene i's checks read (its own assets and runs): another scene's run changing status never
    re-checks this one."""
    media = lesson.media
    if not isinstance(media, dict):
        return None
    assets = media.get("assets") if isinstance(media.get("assets"), dict) else {}
    ids = _asset_ids(lesson.scene(i))
    runs = [r for r in (media.get("runs") or []) if isinstance(r, dict) and r.get("scene_index") == i]
    return _sha({"assets": {k: assets[k] for k in sorted(ids) if k in assets}, "runs": runs, "checked": media.get("checked")})


def lesson_fingerprint(lesson, scene_fps):
    """The report's identity: the rules, the lesson settings that matter, the concept map, every scene's fingerprint and
    the media metadata (when checked). A report whose fingerprint differs from the current one is stale."""
    cmap = [{"id": c.get("id"), "title": c.get("title")} for c in lesson.concept_map]
    return _sha({"rules": RULES, "settings": _settings_key(lesson.settings), "concepts": cmap, "scenes": scene_fps,
                 "media": media_digest(lesson.media), "recomposed": lesson.recomposed is not None})


# ---- the checks --------------------------------------------------------------------------------------------------------

def _families():
    """The check families (each: scene_checks(lesson, i) -> [issue], lesson_checks(lesson) -> [issue], registry(lesson)
    -> {section: value}); a family that is missing or fails is reported as a limitation, never as a crash."""
    found = []
    for name in FAMILY_MODULES:
        try:
            found.append((name, __import__(name)))
        except Exception:  # noqa: BLE001 - a family that cannot load (missing, broken) is left out, never a crash
            continue
    return found


FAMILY_MODULES = ("quality_style", "quality_media", "quality_education", "quality_timing")


# per-scene findings reused while a scene (and the lesson settings / rules) are unchanged: a small in-process memo,
# never persisted (the report is derived on demand)
_SCENE_MEMO = OrderedDict()
_SCENE_MEMO_MAX = 4000


_MEMO_LOCK = threading.Lock()


def _memo_get(key):
    with _MEMO_LOCK:
        value = _SCENE_MEMO.get(key)
        if value is not None:
            _SCENE_MEMO.move_to_end(key)
        return value


def _memo_put(key, value):
    with _MEMO_LOCK:
        _SCENE_MEMO[key] = value
        _SCENE_MEMO.move_to_end(key)
        while len(_SCENE_MEMO) > _SCENE_MEMO_MAX:
            _SCENE_MEMO.popitem(last=False)


def _core_checks(lesson):
    """Preview / export consistency (the core's own family): the plan the page plays and records is the stored one, and
    the current planning gives the same plan (otherwise what was previewed and reviewed is not what an export records)."""
    out = []
    if not lesson.cinematic:
        return out
    re_plans = lesson.recomposed if isinstance(lesson.recomposed, list) else None
    stale = []
    for i in range(lesson.count):
        plan = lesson.plan(i)
        if lesson.kind(i) == "quiz_checkpoint" and not plan:
            continue
        if not plan:
            out.append(issue("core.no_plan", "preview_export", "error",
                             f"{lesson.label(i)} has no composition yet: it would play in the plain layout.",
                             scene=i, element="scene", evidence={"key": "no_plan"},
                             repair=repair("auto", "presentation", {"type": "replan", "scenes": [i]}, "Plan the scene")))
            continue
        if re_plans is None or i >= len(re_plans) or not isinstance(re_plans[i], dict):
            continue
        now = re_plans[i]
        # the layout and the timing themselves (not plan_hash / the sync fingerprint: since Phase 17 they also carry the
        # style's background colours, and the style family owns a stale look, compared with Phase 17 directly)
        changed = [k for k, a, b in (("layout", _layout_key(plan), _layout_key(now)), ("timing", _timing_key(plan), _timing_key(now)))
                   if a != b]
        if changed:
            stale.append(i)
            klass = "timing" if changed == ["timing"] else "presentation"
            out.append(issue("core.stale_plan", "preview_export", "error",
                             f"{lesson.label(i)} was planned with older settings: the preview and an export would not show "
                             f"what the lesson's current {', '.join(changed)} give.",
                             scene=i, element="plan", evidence={"key": "stale", "found": changed},
                             repair=repair("auto", klass, {"type": "replan", "scenes": [i]}, "Re-plan the scene")))
    return out


def _layout_key(plan):
    """What a scene shows and where (the composition), without the style's colours."""
    background = plan.get("background") if isinstance(plan.get("background"), dict) else {}
    return _sha(_stable({"plan": {k: plan.get(k) for k in ("version", "template", "shot", "layers", "camera", "transition", "timeline", "motion")},
                         "background": {k: v for k, v in background.items() if k not in ("colors", "color", "palette")}}))


def _timing_key(plan):
    """When things happen (Phase 16's moments, the hold, the narration model), without fingerprints that hash the style."""
    sync = plan.get("sync") if isinstance(plan.get("sync"), dict) else {}
    raw = sync.get("events") if isinstance(sync.get("events"), list) else []
    events = [{k: e.get(k) for k in ("id", "type", "target", "at", "estimate", "duration", "tolerance")} for e in raw[:200] if isinstance(e, dict)]
    return _sha(_stable({"events": events, "end_hold": sync.get("end_hold"), "narration": sync.get("narration"),
                         "duration": plan.get("duration")}))


def _check_safely(make, family, limitations):
    try:
        found = make() or []
        return [f for f in found if isinstance(f, dict) and f.get("rule")]
    except Exception as e:  # noqa: BLE001 - a broken check never breaks the report or the lesson
        limitations.append(f"{family}: some checks could not run ({type(e).__name__})")
        return []


def evaluate_lesson(scenes, settings=None, concept_map=None, media=None, recomposed=None, assist=False):
    """The quality report of a lesson (deterministic; read-only). media: quality_media's metadata from the server (None:
    the lesson's files were not looked up); recomposed: the plans the current planning gives (None: not recomputed);
    assist: run the optional AI assistance (AI-assisted mode only; suggestions, never authoritative)."""
    lesson = Lesson(scenes, settings, concept_map, media, recomposed, assist)
    families = _families()
    limitations = []
    loaded = {name for name, _m in families}
    labels = {"quality_style": "style and text", "quality_media": "presenter and media", "quality_education": "terminology and concepts",
              "quality_timing": "timing and motion"}
    for name in FAMILY_MODULES:
        if name not in loaded:
            limitations.append(f"The {labels[name]} checks are not available.")
    if media is None:
        limitations.append("Pictures and clips were not looked up (save the lesson to check its files).")
    if recomposed is None and lesson.cinematic:
        limitations.append("The plans were not recomputed (the preview / export comparison used the stored plans only).")
    scene_fps = [scene_fingerprint(lesson, i) for i in range(lesson.count)]
    digest = media_digest(media)
    issues = []
    started = time.monotonic()
    skipped = 0
    for i in range(lesson.count):
        key = (scene_fps[i], scene_media_digest(lesson, i))
        cached = _memo_get(key)
        if cached is None:
            if time.monotonic() - started > SCENE_CHECK_BUDGET:
                skipped += 1
                continue
            cached = []
            before = len(limitations)
            for name, module in families:
                if hasattr(module, "scene_checks"):
                    cached += _check_safely(lambda m=module: m.scene_checks(lesson, i), name, limitations)
            if len(limitations) == before:
                _memo_put(key, cached)  # only complete results are reused
        issues += [dict(f, fingerprint=scene_fps[i]) for f in cached]
    if skipped:
        limitations.append(f"The lesson is very large: {skipped} scene(s) could not be checked in time (check again to continue).")
    for name, module in families:
        if hasattr(module, "lesson_checks"):
            issues += [dict(f, fingerprint=scene_fps[f["scene"]] if isinstance(f.get("scene"), int) and f["scene"] < lesson.count else None)
                       for f in _check_safely(lambda m=module: m.lesson_checks(lesson), name, limitations)]
    issues += [dict(f, fingerprint=scene_fps[f["scene"]] if isinstance(f.get("scene"), int) and f["scene"] < lesson.count else None)
               for f in _check_safely(lambda: _core_checks(lesson), "preview and export", limitations)]
    issues = _approval_aware(lesson, _fold(issues))
    issues = _hidden_scenes(lesson, issues)
    # one finding per id (two families never report the same thing twice), most serious first, then by scene
    seen, unique = set(), []
    for f in sorted(issues, key=lambda f: (-RANK[f["severity"]], f["scene"] if f["scene"] is not None else -1, f["rule"], f["id"])):
        if f["id"] not in seen:
            seen.add(f["id"])
            unique.append(f)
    registry = {}
    for name, module in families:
        if hasattr(module, "registry"):
            try:
                part = module.registry(lesson)
                if isinstance(part, dict):
                    registry.update({str(k)[:40]: v for k, v in part.items()})
            except Exception as e:  # noqa: BLE001
                limitations.append(f"{name}: its part of the registry could not be built ({type(e).__name__})")
    return _report(lesson, unique, registry, scene_fps, limitations, families)


def _hidden_scenes(lesson, issues):
    """Phase 19: a scene hidden in the editor is not played or exported: its findings are recorded as info (shown in the
    debug view, never counted as something to review)."""
    out = []
    for f in issues:
        i = f.get("scene")
        edit = lesson.scene(i).get("edit") if isinstance(i, int) else None
        if isinstance(edit, dict) and edit.get("hidden") is True and f["severity"] != "info":
            f = dict(f, severity="info", evidence={**f["evidence"], "hidden_scene": True, "was": f["severity"]})
        out.append(f)
    return out


def _fold(issues):
    """One cause, one finding: in a scene, the findings whose repair is the same (re-planning that scene, or the same
    suggestion) are folded into the most important of them (a stale plan first), the others named in its evidence."""
    def priority(f):
        return (f["rule"] != "core.stale_plan", not f["rule"].startswith("style."), -RANK[f["severity"]], f["rule"])

    groups = OrderedDict()
    rest = []
    for f in issues:
        rep = f.get("repair") or {}
        action = rep.get("action") or {}
        if f.get("scene") is None or rep.get("kind") not in ("auto", "suggest") or action.get("type") == "none":
            rest.append(f)
            continue
        if action.get("type") == "replan":
            key = (f["scene"], "replan", tuple(action.get("scenes") or ()))
        else:
            key = (f["scene"], json.dumps(action, sort_keys=True, default=str))
        groups.setdefault(key, []).append(f)
    out = list(rest)
    for members in groups.values():
        members.sort(key=priority)
        head = dict(members[0])
        if len(members) > 1:
            head["evidence"] = {**head["evidence"], "also": sorted({m["rule"] for m in members[1:]})}
            head["severity"] = SEVERITIES[max(RANK[m["severity"]] for m in members)]
        out.append(head)
    return out


def _approval_aware(lesson, issues):
    """An automatic repair never takes an approval away: a re-plan that would change an approved scene's composition
    inputs (e.g. after the lesson's motion or transitions changed) becomes a suggestion that says so."""
    re_plans = lesson.recomposed if isinstance(lesson.recomposed, list) else None

    def approval_lost(i):
        review = lesson.reviews(i).get("composition") if 0 <= i < lesson.count else None
        if not (isinstance(review, dict) and review.get("status") in ("approved", "changed")):
            return False
        if re_plans is None or i >= len(re_plans) or not isinstance(re_plans[i], dict):
            return True  # not recomputed: cannot promise the approval stays
        return re_plans[i].get("fingerprint") != review.get("fingerprint")

    out = []
    for f in issues:
        rep = f.get("repair") or {}
        action = rep.get("action") or {}
        if rep.get("kind") == "auto" and action.get("type") == "replan" and any(
                approval_lost(i) for i in action.get("scenes") or [] if isinstance(i, int)):
            f = dict(f, repair={"kind": "suggest", "class": "composition", "action": action,
                                "label": "Re-plan (the scene's approval will be asked again)"})
        out.append(f)
    return out


def _status(counts):
    if counts["blocking"]:
        return "blocked"
    if counts["error"]:
        return "attention"
    if counts["warning"] or counts["notice"]:
        return "review"
    return "good"


def _report(lesson, issues, registry, scene_fps, limitations, families):
    counts = {s: sum(1 for f in issues if f["severity"] == s) for s in SEVERITIES}
    dims = OrderedDict()
    for dim, label in DIMENSIONS.items():
        mine = [f for f in issues if f["dimension"] == dim and f["severity"] != "info"]
        worst = max((RANK[f["severity"]] for f in mine), default=-1)
        dims[dim] = {"label": label, "status": "pass" if worst < RANK["notice"] else SEVERITIES[worst], "issues": len(mine)}
    scenes = []
    for i in range(lesson.count):
        mine = [f for f in issues if f["scene"] == i]
        c = {s: sum(1 for f in mine if f["severity"] == s) for s in SEVERITIES}
        scenes.append({"index": i, "fingerprint": scene_fps[i], "status": _status(c), "counts": c})
    attention = sum(1 for f in issues if f["severity"] != "info")
    return {"rules": RULES, "version": RULES_VERSION, "fingerprint": lesson_fingerprint(lesson, scene_fps),
            "status": _status(counts), "summary": {"scenes": lesson.count, "counts": counts, "attention": attention,
                                                   "auto_repairable": sum(1 for f in issues if f["repair"]["kind"] == "auto")},
            "dimensions": dims, "scenes": scenes, "issues": issues, "registry": registry,
            "limitations": sorted(set(limitations)), "families": [name for name, _m in families]}


def is_stale(report, scenes, settings=None, concept_map=None, media=None, recomposed=None):  # noqa: D401
    """True when a stored report no longer describes the lesson (other inputs, or other rules)."""
    if not isinstance(report, dict) or report.get("rules") != RULES:
        return True
    lesson = Lesson(scenes, settings, concept_map, media, recomposed)
    return report.get("fingerprint") != lesson_fingerprint(lesson, [scene_fingerprint(lesson, i) for i in range(lesson.count)])


# ---- API ---------------------------------------------------------------------------------------------------------------

def create_quality_router(*, get_current_user, library, link_for):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel
    import cinematic as C
    import models
    from database import get_db

    router = APIRouter(prefix="/api/quality", tags=["quality"])

    class QualityIn(BaseModel):
        scenes: list
        settings: C.CinematicSettings = C.CinematicSettings()
        project_id: int | None = None
        concept_map: list | None = None
        recompose: bool = True
        assist: bool = False   # the optional AI assistance (AI-assisted mode only; slower: asked for explicitly)

    @router.get("")
    def vocabulary(current_user=Depends(get_current_user)):
        """The quality contract: rules version, severities, dimensions, repair classes."""
        return {"rules": RULES, "severities": SEVERITIES, "dimensions": DIMENSIONS, "repair_classes": REPAIR_CLASSES}

    @router.post("/lesson")
    def lesson_quality(body: QualityIn, current_user=Depends(get_current_user), db=Depends(get_db)):
        """The lesson's quality report (read-only: nothing is saved, generated or approved; the lesson's files are
        looked up without being marked, the plans recomputed without a model; the optional AI assistance only when the
        request asks for it with `assist`)."""
        if len(body.scenes) > MAX_SCENES:
            raise HTTPException(status_code=413, detail=f"A lesson can have at most {MAX_SCENES} scenes.")
        size = sum(len(s.get(k)) for s in body.scenes if isinstance(s, dict) for k in ("html", "narration") if isinstance(s.get(k), str))
        if size > MAX_LESSON_TEXT:
            raise HTTPException(status_code=413, detail="This lesson is too large to check.")
        C.check_settings(body.settings)
        if body.project_id is not None and not db.query(models.Project.id).filter(
                models.Project.id == body.project_id, models.Project.user_id == current_user.id).first():
            raise HTTPException(status_code=404, detail="Lesson not found.")
        settings = body.settings.model_dump()
        concept_map = C.check_concept_map(body.concept_map)
        media = None
        try:
            import quality_media
            media = quality_media.load_media(db, current_user.id, body.scenes, body.project_id, library=library)
        except ImportError:
            media = None
        except Exception:  # noqa: BLE001 - the files could not be looked up: reported as a limitation
            media = None
        recomposed = None
        if body.recompose and settings.get("mode") == "cinematic":
            try:
                def resolve(asset_id):
                    asset = library.accessible(db, asset_id, current_user.id) if asset_id else None
                    if asset is None or asset.status != "ready" or asset.kind not in ("image", "video"):
                        return None
                    return {"kind": asset.kind, "url": link_for(current_user)(asset), "width": asset.width, "height": asset.height}
                recomposed = C.compose_lesson([_bounded(s) for s in json.loads(json.dumps(body.scenes))], settings, resolve, ai={"cached_only": True},
                                              concept_map=concept_map)
            except Exception:  # noqa: BLE001
                recomposed = None
        return evaluate_lesson(body.scenes, settings, concept_map, media, recomposed, assist=body.assist)

    return router
