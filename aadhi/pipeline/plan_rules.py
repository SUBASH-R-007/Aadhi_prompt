"""Pure plan checks and deterministic plan fixes (no I/O).

``plan_problems`` returns (hard, soft) problems for a ``GenPlan``; both are fed back to the model
on the first validation pass, later passes only see hard ones, and ``normalize_plan`` then fixes
everything it can deterministically (drop dangling refs and refs to the chunks the concept brief set
aside as non-teaching material, insert chapter cards / recap / hook /
quiz checkpoints, convert disallowed scene types, keep one opening, give every scene a narrative
role and drop unknown visual-note ids). The result always converts to a valid ``LecturePlan``. ``fit_plan`` / ``plan_limit_problems`` apply the stricter ``Screenplay`` limits
(<= 200 scenes, chapter titles <= 160 characters, metadata lengths) to any ``LecturePlan`` —
including teacher-edited ones — before a single scene is written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schemas.screenplay import slugify
from .base import GenerationOptions, LecturePlan
from .gen_models import GenChapter, GenPlan, GenPlannedScene
from .validate import content_patterns, packaging_phrases, subject_nouns_in

TEACHING_TYPES = ("content", "example", "simulation", "ai_video", "interactive", "title")
HOOK_TYPES = ("title", "content", "ai_video", "simulation", "interactive")
BASE_SCENE_TYPES = ("title", "content", "example", "summary", "key_takeaway", "recap", "chapter_card")

# Size limits a plan must meet to become a Screenplay (LecturePlan alone allows 40 x 60 scenes).
MAX_CHAPTERS = 40  # LecturePlan.chapters / Screenplay.chapters
MAX_CHAPTER_SCENES = 60  # PlannedChapter.scenes
MAX_SCENES = 200  # Screenplay.scenes
CHAPTER_TITLE_MAX = 160  # ChapterRef.title (PlannedChapter allows 240)
META_MAX = {"subject_name": 240, "unit_name": 240, "session_number": 60, "session_title": 300}  # Screenplay fields
BRIDGE_MAX = 300  # PlannedScene.bridge_in
MAX_VISUAL_NOTES = 10  # PlannedScene.visual_note_ids
DEFAULT_ROLE = {  # narrative role of a scene type when the model gave none
    "title": "context", "recap": "context", "chapter_card": "transition", "quiz_checkpoint": "check",
    "example": "example", "summary": "synthesis", "key_takeaway": "synthesis", "interactive": "practice",
    "content": "concept", "simulation": "concept", "ai_video": "application",
}
CHECK_ROLES = ("check", "practice")
_LABEL_PREFIX = re.compile(r"^\s*(?:clip|segment|scene|part|session|video)\s*\d+\s*(?:script)?\s*[-–—:.]\s*", re.I)
_BRACKET_TIMECODE = re.compile(r"\s*[\[(]\s*\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\s*[\])]")


@dataclass
class PlanContext:
    """What the plan may reference."""

    options: GenerationOptions
    chunk_ids: set[str] = field(default_factory=set)
    skipped_chunk_ids: set[str] = field(default_factory=set)  # set aside by the concept brief: never cited
    templates: set[str] = field(default_factory=set)
    has_figures: bool = False
    freeform_manim: bool = True
    manim_enabled: bool = True
    images_enabled: bool = True
    visual_note_ids: set[str] = field(default_factory=set)  # IngestResult.visual_notes the plan may cite
    source_text: str = ""  # the scoped source (to tell its subject matter from packaging talk)

    @property
    def target_seconds(self) -> int:
        return self.options.target_minutes * 60

    def allowed_scene_types(self) -> list[str]:
        o = self.options
        types = list(BASE_SCENE_TYPES)
        if o.include_quizzes:
            types.append("quiz_checkpoint")
        if o.allow_manim and self.manim_enabled and (self.templates or (o.allow_freeform_manim and self.freeform_manim)):
            types.append("simulation")
        if o.allow_ai_video:
            types.append("ai_video")
        if o.allow_interactive:
            types.append("interactive")
        return types

    def allowed_panels(self) -> list[str]:
        o = self.options
        kinds = ["skill_tree", "chart", "graph", "model_3d", "terminal", "quiz"]
        if self.has_figures:
            kinds.append("figure")
        if o.allow_generated_images and self.images_enabled:
            kinds.append("image")
        if o.allow_manim and self.manim_enabled and (self.templates or (o.allow_freeform_manim and self.freeform_manim)):
            kinds.append("manim")
        if o.allow_gifs:
            kinds.append("gif")
        return kinds


def _scenes(gen: GenPlan) -> list[GenPlannedScene]:
    return [s for ch in gen.chapters for s in ch.scenes]


def _dupes(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for k in keys:
        s = slugify(k)
        if s in seen and k not in out:
            out.append(k)
        seen.add(s)
    return out


def _core_concepts(gen: GenPlan) -> list[str]:
    return [slugify(c.key) for c in gen.concept_map if c.kind == "core"]


def quiz_gaps(gen: GenPlan, every: int) -> list[tuple[int, list[str]]]:
    """Positions (scene index in plan order) needing a quiz *before* them, with the concepts to cover.

    ``len(scenes)`` as position means "at the end of the lecture".
    """
    core = set(_core_concepts(gen))
    scenes = _scenes(gen)
    gaps: list[tuple[int, list[str]]] = []
    since: list[str] = []
    for i, s in enumerate(scenes):
        if s.type == "quiz_checkpoint":
            since = []
            continue
        c = slugify(s.concept_key) if s.concept_key else None
        if s.type in TEACHING_TYPES and c in core and c not in since:
            if len(since) >= every:
                gaps.append((i, list(since)))
                since = []
            since.append(c)
    if len(since) >= every:
        gaps.append((len(scenes), list(since)))
    return gaps


def plan_problems(gen: GenPlan, pc: PlanContext) -> tuple[list[str], list[str]]:
    """(hard, soft) problems with the plan."""
    hard: list[str] = []
    soft: list[str] = []
    scenes = _scenes(gen)
    if not gen.chapters or not scenes:
        hard.append("the plan has no chapters/scenes; organise the lecture into chapters of scenes")
        return hard, soft
    if not gen.concept_map:
        hard.append("concept_map is empty; list the concepts the lecture teaches")
    o = pc.options
    soft.extend(size_problems([len(ch.scenes) for ch in gen.chapters]))
    concepts = {slugify(c.key) for c in gen.concept_map}
    objectives = {slugify(x.key) for x in gen.learning_objectives}
    miscs = {slugify(m.key) for m in gen.misconceptions}
    for label, keys in (
        ("concept", [c.key for c in gen.concept_map]),
        ("objective", [x.key for x in gen.learning_objectives]),
        ("misconception", [m.key for m in gen.misconceptions]),
        ("scene", [s.key for s in scenes]),
        ("chapter", [c.key for c in gen.chapters]),
    ):
        for d in _dupes(keys):
            soft.append(f"duplicate {label} key {d!r}; keys must be unique")
    for c in gen.concept_map:
        bad = [d for d in c.depends_on if slugify(d) not in concepts]
        if bad:
            soft.append(f"concept {c.key!r} depends on unknown concepts {bad}")
    for x in gen.learning_objectives:
        bad = [k for k in x.concept_keys if slugify(k) not in concepts]
        if bad:
            soft.append(f"objective {x.key!r} references unknown concepts {bad}")
    allowed_types = set(pc.allowed_scene_types())
    allowed_panels = set(pc.allowed_panels())
    for s in scenes:
        where = f"scene {s.key!r}"
        if s.type not in allowed_types:
            soft.append(f"{where}: type {s.type!r} is not allowed here (allowed: {', '.join(sorted(allowed_types))})")
        if s.concept_key and slugify(s.concept_key) not in concepts:
            soft.append(f"{where}: unknown concept {s.concept_key!r}")
        bad_o = [k for k in s.objective_keys if slugify(k) not in objectives]
        if bad_o:
            soft.append(f"{where}: unknown objectives {bad_o}")
        bad_m = [k for k in s.misconception_keys if slugify(k) not in miscs]
        if bad_m:
            soft.append(f"{where}: unknown misconceptions {bad_m}")
        bad_r = [r for r in s.source_refs if r not in pc.chunk_ids]
        if bad_r and pc.chunk_ids:
            soft.append(f"{where}: unknown source chunk ids {bad_r[:5]}")
        aside = [r for r in s.source_refs if r in pc.skipped_chunk_ids]
        if aside:
            soft.append(f"{where}: cites {aside[:5]}, which hold no teaching content; cite chunks from the Source chunks "
                        "section")
        if s.side_panel_kind and s.side_panel_kind not in allowed_panels:
            soft.append(f"{where}: side panel {s.side_panel_kind!r} is not available (allowed: {', '.join(sorted(allowed_panels))})")
        if s.side_panel_kind and s.side_panel_kind != "skill_tree" and not s.visual_rationale.strip():
            soft.append(f"{where}: explain in visual_rationale why the {s.side_panel_kind} panel helps learning")
        if s.manim_template:
            if s.type != "simulation" and s.side_panel_kind != "manim":
                soft.append(f"{where}: manim_template is only for simulation scenes or manim side panels")
            elif s.manim_template not in pc.templates:
                soft.append(f"{where}: unknown manim template {s.manim_template!r}")
        elif s.type == "simulation" and not (o.allow_freeform_manim and pc.freeform_manim):
            soft.append(f"{where}: choose a manim_template for this simulation")
    first = gen.chapters[0].scenes[0] if gen.chapters[0].scenes else None
    if first is None or first.type not in HOOK_TYPES:
        soft.append("the first scene must be a cold-open hook (type title, content, ai_video, simulation or interactive)")
    if o.previous_session_summary.strip():
        first_scenes = gen.chapters[0].scenes
        if len(first_scenes) < 2 or first_scenes[1].type != "recap":
            soft.append("a previous-session summary was given: the second scene must be a recap scene")
    for i, ch in enumerate(gen.chapters[1:], 2):
        if not ch.scenes or ch.scenes[0].type != "chapter_card":
            soft.append(f"chapter {i} ({ch.key!r}) must start with a chapter_card scene")
    if o.include_quizzes:
        for pos, cs in quiz_gaps(gen, o.quiz_every_n_concepts):
            where = "at the end" if pos >= len(scenes) else f"before scene {scenes[pos].key!r}"
            soft.append(f"add a quiz_checkpoint {where} covering concepts {cs} "
                        f"(at most {o.quiz_every_n_concepts} core concepts between quizzes)")
    confronted = {slugify(k) for s in scenes for k in s.misconception_keys}
    for m in gen.misconceptions:
        if slugify(m.key) not in confronted:
            soft.append(f"misconception {m.key!r} is not confronted by any scene (list it in a teaching scene's or "
                        "quiz's misconception_keys)")
    taught = {slugify(k) for s in scenes if s.type != "quiz_checkpoint" for k in s.objective_keys}
    assessed = {slugify(k) for s in scenes if s.type == "quiz_checkpoint" for k in s.objective_keys}
    for x in gen.learning_objectives:
        k = slugify(x.key)
        if k not in taught:
            soft.append(f"objective {x.key!r} is not taught by any scene (list it in a teaching scene's objective_keys)")
        if o.include_quizzes and k not in assessed:
            soft.append(f"objective {x.key!r} is not assessed by any quiz_checkpoint")
    soft.extend(flow_problems(gen, pc))
    total = sum(max(5, s.est_seconds) for s in scenes)
    target = pc.target_seconds
    if not 0.75 * target <= total <= 1.25 * target:
        soft.append(f"estimated duration is {total} s but the target is {target} s (±25%); adjust scenes or est_seconds")
    return hard, soft


def flow_problems(gen: GenPlan, pc: PlanContext) -> list[str]:
    """Soft problems with the lecture's flow: one opening, narrative roles, bridges, the author's visual
    notes and plan text that talks about the source's packaging instead of its content."""
    out: list[str] = []
    scenes = _scenes(gen)
    if not scenes:
        return out
    titles = [s.key for s in scenes if s.type == "title"]
    if len(titles) > 1:
        out.append(f"the plan has {len(titles)} title scenes {titles[:5]}; a lecture has one opening, so turn the others "
                   "into teaching scenes or merge them (no title card per part of the source)")
    first = scenes[0]
    if first.narrative_role and first.narrative_role != "hook":
        out.append(f"scene {first.key!r} opens the lecture, so its narrative_role is 'hook'")
    hooks = [s.key for s in scenes[1:] if s.narrative_role == "hook"]
    if hooks:
        out.append(f"only the first scene is the hook; give scenes {hooks[:5]} the role they play later in the lecture")
    for s in scenes:
        if s.type == "quiz_checkpoint" and s.narrative_role and s.narrative_role not in CHECK_ROLES:
            out.append(f"scene {s.key!r} is a quiz, so its narrative_role is 'check' (or 'practice')")
        bad = [v for v in s.visual_note_ids if v not in pc.visual_note_ids]
        if bad:
            out.append(f"scene {s.key!r}: unknown visual_note_ids {bad[:5]} (use ids from \"Author's visual suggestions\")")
    if any(s.narrative_role or s.bridge_in.strip() for s in scenes):  # the model used the flow fields: complete them
        missing = [s.key for s in scenes[1:] if not s.bridge_in.strip()]
        if missing:
            more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
            out.append(f"scenes {missing[:6]}{more} have no bridge_in; give every scene after the first one sentence "
                       "linking it to the scene before")
    topic = " ".join([gen.session_title, *(f"{c.title} {c.summary}" for c in gen.concept_map)])
    nouns, content = subject_nouns_in(topic, pc.source_text), content_patterns(pc.source_text)
    texts = [("session_title", gen.session_title), *((f"chapter {ch.key!r} title", ch.title) for ch in gen.chapters)]
    texts += [(f"scene {s.key!r}", " \n ".join([s.goal, s.bridge_in, *s.key_points])) for s in scenes]
    leaks = [(where, found) for where, text in texts if (found := packaging_phrases(text, nouns, pc.source_text, content))]
    for where, found in leaks[:4]:
        out.append(f"{where} mentions the source's packaging ({', '.join(found[:3])}); describe the teaching content "
                   "only (clips, segments, timecodes and durations never appear in the lecture)")
    return out


def size_problems(scenes_per_chapter: list[int]) -> list[str]:
    """Chapter / scene count limits (soft: ``to_lecture_plan`` / ``fit_plan`` truncate what is left)."""
    out: list[str] = []
    if len(scenes_per_chapter) > MAX_CHAPTERS:
        out.append(f"the plan has {len(scenes_per_chapter)} chapters; use at most {MAX_CHAPTERS}")
    for i, n in enumerate(scenes_per_chapter, 1):
        if n > MAX_CHAPTER_SCENES:
            out.append(f"chapter {i} has {n} scenes; use at most {MAX_CHAPTER_SCENES} per chapter")
    total = sum(scenes_per_chapter)
    if total > MAX_SCENES:
        out.append(f"the plan has {total} scenes; a lecture has at most {MAX_SCENES}")
    return out


def plan_limit_problems(plan: LecturePlan) -> list[str]:
    """Problems that would stop ``plan`` from becoming a Screenplay ([] = it fits).

    For callers that accept edited plans (the plan-review API) so they can reject them up front;
    the pipeline itself applies ``fit_plan``.
    """
    out = size_problems([len(ch.scenes) for ch in plan.chapters])
    for i, ch in enumerate(plan.chapters, 1):
        if len(ch.title) > CHAPTER_TITLE_MAX:
            out.append(f"chapter {i} title is {len(ch.title)} characters; at most {CHAPTER_TITLE_MAX}")
    for name, limit in META_MAX.items():
        value = getattr(plan, name) or ""
        if len(value) > limit:
            out.append(f"{name} is {len(value)} characters; at most {limit}")
    return out


def fit_plan(plan: LecturePlan) -> tuple[LecturePlan, list[str]]:
    """``plan`` clamped to the Screenplay limits (+ notes on what was cut); unchanged when it fits.

    Scenes beyond ``MAX_SCENES`` are dropped from the end (chapters left empty are dropped), chapter
    titles and metadata are truncated.
    """
    if not plan_limit_problems(plan):
        return plan, []
    notes: list[str] = []
    data = plan.model_dump(mode="json")
    budget = MAX_SCENES
    chapters = []
    for ch in data["chapters"]:
        kept = ch["scenes"][:budget]
        budget -= len(kept)
        if len(kept) < len(ch["scenes"]):
            notes.append(f"chapter {ch['id']}: dropped {len(ch['scenes']) - len(kept)} scene(s) beyond the "
                         f"{MAX_SCENES}-scene limit")
        if not kept:
            continue
        if len(ch["title"]) > CHAPTER_TITLE_MAX:
            notes.append(f"chapter {ch['id']}: title shortened to {CHAPTER_TITLE_MAX} characters")
        chapters.append({**ch, "title": (ch["title"] or ch["id"])[:CHAPTER_TITLE_MAX].rstrip() or ch["id"],
                         "scenes": kept})
    data["chapters"] = chapters[:MAX_CHAPTERS]
    for name, limit in META_MAX.items():
        if len(data.get(name) or "") > limit:
            notes.append(f"{name} shortened to {limit} characters")
            data[name] = data[name][:limit].rstrip()
    data["session_number"] = data.get("session_number") or "Session 1"
    return LecturePlan.model_validate(data), notes


# ---------------------------------------------------------------------------
# Deterministic fixes
# ---------------------------------------------------------------------------


def _uniq_key(key: str, used: set[str], max_len: int) -> str:
    base = slugify(key or "item")[:max_len].strip("-_") or "item"
    k, n = base, 2
    while k in used:
        suffix = f"-{n}"
        k = f"{base[: max_len - len(suffix)]}{suffix}"
        n += 1
    used.add(k)
    return k


def _sentences(text: str, limit: int = 6) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text or "") if p.strip()]
    return [p[:200] for p in parts[:limit]]


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:")


def clean_title(title: str) -> str:
    """``title`` without source packaging labels ("Clip 2 Script - ", "[0:10 - 1:15]"); unchanged when
    nothing would be left."""
    out = _BRACKET_TIMECODE.sub("", _LABEL_PREFIX.sub("", title or "")).strip(" -–—:")
    return out or (title or "").strip()


@dataclass
class NormalizeReport:
    notes: list[str] = field(default_factory=list)


def normalize_plan(gen: GenPlan, pc: PlanContext) -> tuple[GenPlan, NormalizeReport]:
    """Return a fixed copy of ``gen`` (keys slugified + unique, refs valid, required scenes present)."""
    g = gen.model_copy(deep=True)
    rep = NormalizeReport()
    o = pc.options
    cleaned = clean_title(g.session_title)
    if cleaned != g.session_title.strip():
        rep.notes.append("removed a source packaging label from the session title")
    g.session_title = cleaned
    # --- keys ---------------------------------------------------------------
    used: set[str] = set()
    cmap: dict[str, str] = {}
    for c in g.concept_map:
        new = _uniq_key(c.key, used, 48)
        cmap.setdefault(slugify(c.key), new)
        c.key = new
    for c in g.concept_map:
        c.depends_on = list(dict.fromkeys(cmap[slugify(d)] for d in c.depends_on if slugify(d) in cmap and cmap[slugify(d)] != c.key))
    _break_cycles(g)
    used = set()
    omap: dict[str, str] = {}
    for x in g.learning_objectives:
        new = _uniq_key(x.key, used, 48)
        omap.setdefault(slugify(x.key), new)
        x.key = new
        x.concept_keys = [cmap[slugify(k)] for k in x.concept_keys if slugify(k) in cmap]
    used = set()
    mmap: dict[str, str] = {}
    for m in g.misconceptions:
        new = _uniq_key(m.key, used, 48)
        mmap.setdefault(slugify(m.key), new)
        m.key = new
        m.concept_key = cmap.get(slugify(m.concept_key)) if m.concept_key else None
    used_ch: set[str] = set()
    used_sc: set[str] = set()
    allowed_types = set(pc.allowed_scene_types())
    allowed_panels = set(pc.allowed_panels())
    for ch in g.chapters:
        ch.key = _uniq_key(ch.key, used_ch, 48)
        ch.title = clean_title(ch.title)
        ch.concept_keys = [cmap[slugify(k)] for k in ch.concept_keys if slugify(k) in cmap]
        kept: list[GenPlannedScene] = []
        for s in ch.scenes:
            s.key = _uniq_key(s.key, used_sc, 40)
            s.concept_key = cmap.get(slugify(s.concept_key)) if s.concept_key else None
            s.objective_keys = list(dict.fromkeys(omap[slugify(k)] for k in s.objective_keys if slugify(k) in omap))
            s.misconception_keys = list(dict.fromkeys(mmap[slugify(k)] for k in s.misconception_keys if slugify(k) in mmap))
            if pc.chunk_ids:
                s.source_refs = [r for r in dict.fromkeys(s.source_refs) if r in pc.chunk_ids][:20]
            aside = [r for r in s.source_refs if r in pc.skipped_chunk_ids]
            if aside:
                s.source_refs = [r for r in s.source_refs if r not in pc.skipped_chunk_ids]
                rep.notes.append(f"scene {s.key}: dropped {len(aside)} source ref(s) to sections the concept brief set "
                                 "aside as non-teaching material")
            s.key_points = [k for k in s.key_points if k.strip()][:12]
            s.est_seconds = int(min(600, max(5, s.est_seconds or 45)))
            s.visual_note_ids = [v for v in dict.fromkeys(x.strip() for x in s.visual_note_ids)
                                 if v in pc.visual_note_ids][:MAX_VISUAL_NOTES]
            s.bridge_in = _trim(s.bridge_in, BRIDGE_MAX)
            if s.manim_template and (
                s.manim_template not in pc.templates or (s.type != "simulation" and s.side_panel_kind != "manim")
            ):
                s.manim_template = None
            if not _fix_scene_type(s, pc, allowed_types, rep):
                continue
            if s.side_panel_kind and s.side_panel_kind not in allowed_panels:
                rep.notes.append(f"scene {s.key}: dropped unavailable {s.side_panel_kind} panel")
                s.side_panel_kind = None
            if s.side_panel_kind == "manim" and not s.manim_template and not (o.allow_freeform_manim and pc.freeform_manim):
                s.side_panel_kind = None
            if s.type != "simulation" and s.side_panel_kind != "manim":
                s.manim_template = None
            kept.append(s)
        ch.scenes = kept
    g.chapters = [ch for ch in g.chapters if ch.scenes]
    if not g.chapters:
        return g, rep
    _ensure_hook(g, rep, used_sc)
    _ensure_recap(g, pc, rep, used_sc)
    _ensure_chapter_cards(g, rep, used_sc)
    if o.include_quizzes:
        _ensure_quizzes(g, pc, rep, used_sc)
    _ensure_objectives(g, pc, rep)
    _ensure_misconceptions(g, rep)
    _ensure_flow(g, rep)
    return g, rep


def _ensure_flow(g: GenPlan, rep: NormalizeReport) -> None:
    """One opening (later title scenes become content scenes) and a narrative role for every scene;
    the first scene is the hook and has no bridge."""
    scenes = _scenes(g)
    for s in scenes[1:]:
        if s.type == "title":
            s.type = "content"
            if s.narrative_role in (None, "hook"):
                s.narrative_role = "context"
            rep.notes.append(f"scene {s.key}: a second title scene became a content scene (one opening per lecture)")
    for i, s in enumerate(scenes):
        if i == 0:
            s.narrative_role, s.bridge_in = "hook", ""
        elif s.narrative_role in (None, "hook") or (s.type == "quiz_checkpoint" and s.narrative_role not in CHECK_ROLES):
            s.narrative_role = DEFAULT_ROLE.get(s.type, "concept")


def _fix_scene_type(s: GenPlannedScene, pc: PlanContext, allowed: set[str], rep: NormalizeReport) -> bool:
    """Convert or drop scenes whose type is not available. Returns False to drop the scene."""
    o = pc.options
    if s.type == "simulation" and s.type in allowed and not s.manim_template and not (o.allow_freeform_manim and pc.freeform_manim):
        if pc.templates:
            s.type = "content"
            rep.notes.append(f"scene {s.key}: simulation without a template became a content scene")
    if s.type in allowed:
        return True
    if s.type == "quiz_checkpoint":
        rep.notes.append(f"scene {s.key}: quizzes are disabled; scene removed")
        return False
    rep.notes.append(f"scene {s.key}: {s.type} is unavailable; converted to a content scene")
    if s.type == "ai_video" and "image" in pc.allowed_panels() and not s.side_panel_kind:
        s.side_panel_kind = "image"
        s.visual_rationale = s.visual_rationale or "A realistic picture grounds the idea in the real world."
    s.type = "content"
    s.manim_template = None if s.side_panel_kind != "manim" else s.manim_template
    return True


def _break_cycles(g: GenPlan) -> None:
    deps = {c.key: list(c.depends_on) for c in g.concept_map}
    state: dict[str, int] = {}

    def visit(n: str) -> None:
        stack = [(n, iter(deps.get(n, [])))]
        state[n] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:  # back edge -> drop it
                deps[node].remove(nxt)
                stack[-1] = (node, iter([d for d in deps[node] if state.get(d) != 2]))
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(deps.get(nxt, []))))

    for k in list(deps):
        if k not in state:
            visit(k)
    for c in g.concept_map:
        c.depends_on = deps[c.key]


def _new_scene(key: str, used: set[str], **kw) -> GenPlannedScene:
    return GenPlannedScene(key=_uniq_key(key, used, 40), **kw)


def _ensure_hook(g: GenPlan, rep: NormalizeReport, used: set[str]) -> None:
    first = g.chapters[0].scenes[0]
    if first.type in HOOK_TYPES:
        return
    topic = g.session_title or (g.concept_map[0].title if g.concept_map else "today's topic")
    hook = _new_scene(
        "cold_open", used, type="title", narrative_role="hook",
        concept_key=g.concept_map[0].key if g.concept_map else None,
        goal=f"Hook the learner with a real-world question about {topic} and introduce the session.",
        key_points=[f"Why {topic} matters"], est_seconds=30,
    )
    g.chapters[0].scenes.insert(0, hook)
    rep.notes.append("inserted a cold-open hook scene")


def _ensure_recap(g: GenPlan, pc: PlanContext, rep: NormalizeReport, used: set[str]) -> None:
    summary = pc.options.previous_session_summary.strip()
    if not summary:
        return
    scenes = g.chapters[0].scenes
    if len(scenes) > 1 and scenes[1].type == "recap":
        return
    for ch in g.chapters:
        for i, s in enumerate(ch.scenes):
            if s.type == "recap":
                ch.scenes.pop(i)
                g.chapters[0].scenes.insert(1, s)
                rep.notes.append("moved the recap scene right after the hook")
                return
    recap = _new_scene(
        "recap_previous", used, type="recap", narrative_role="context",
        bridge_in="Before going further, recall what the previous session established.",
        goal="Briefly recap the previous session and connect it to today's topic.",
        key_points=_sentences(summary, 5), est_seconds=40,
    )
    g.chapters[0].scenes.insert(1, recap)
    rep.notes.append("inserted a recap of the previous session")


def _ensure_chapter_cards(g: GenPlan, rep: NormalizeReport, used: set[str]) -> None:
    for i, ch in enumerate(g.chapters[1:], 2):
        if ch.scenes[0].type == "chapter_card":
            continue
        card = _new_scene(
            f"{ch.key}_card", used, type="chapter_card", narrative_role="transition",
            bridge_in=_trim(f"With that in place, we move on to {ch.title}.", BRIDGE_MAX),
            concept_key=ch.concept_keys[0] if ch.concept_keys else None,
            goal=f"Open part {i}: {ch.title}.", key_points=[ch.title], est_seconds=6,
        )
        ch.scenes.insert(0, card)
        rep.notes.append(f"inserted a chapter card for chapter {i}")


def _ensure_quizzes(g: GenPlan, pc: PlanContext, rep: NormalizeReport, used: set[str]) -> None:
    titles = {c.key: c.title for c in g.concept_map}
    for _ in range(50):  # each pass fixes the first gap; bounded for safety
        gaps = quiz_gaps(g, pc.options.quiz_every_n_concepts)
        if not gaps:
            return
        pos, concepts = gaps[0]
        flat = [(ci, si) for ci, ch in enumerate(g.chapters) for si in range(len(ch.scenes))]
        scenes = _scenes(g)
        if pos >= len(flat):  # end of lecture: before trailing summary/takeaway scenes
            ci, si = flat[-1]
            si += 1
            while si > 0 and g.chapters[ci].scenes[si - 1].type in ("summary", "key_takeaway"):
                si -= 1
        else:
            ci, si = flat[pos]
        teaching = [s for s in scenes if s.concept_key in concepts and s.type != "quiz_checkpoint"]
        objectives = [x.key for x in g.learning_objectives if set(x.concept_keys) & set(concepts)]
        miscs = [m.key for m in g.misconceptions if m.concept_key in concepts]
        refs = list(dict.fromkeys(r for s in teaching for r in s.source_refs))[:8]
        names = ", ".join(titles.get(c, c) for c in concepts)
        quiz = _new_scene(
            "quiz_" + "_".join(concepts)[:30], used, type="quiz_checkpoint", concept_key=concepts[-1],
            narrative_role="check", bridge_in=_trim(f"Before moving on, check what you now know about {names}.", BRIDGE_MAX),
            objective_keys=objectives[:6], misconception_keys=miscs[:4], source_refs=refs,
            goal=f"Retrieval check: {names}.",
            key_points=[titles.get(c, c) for c in concepts], est_seconds=40,
        )
        g.chapters[ci].scenes.insert(si, quiz)
        rep.notes.append(f"inserted a quiz checkpoint covering {', '.join(concepts)}")


def _ensure_objectives(g: GenPlan, pc: PlanContext, rep: NormalizeReport) -> None:
    scenes = _scenes(g)
    for x in g.learning_objectives:
        teaching = [s for s in scenes if s.type != "quiz_checkpoint"]
        if not any(x.key in s.objective_keys for s in teaching):
            target = next((s for s in teaching if s.concept_key in x.concept_keys and s.type != "chapter_card"), None)
            target = target or next((s for s in teaching if s.type in ("content", "example")), None)
            if target is not None:
                target.objective_keys.append(x.key)
                rep.notes.append(f"objective {x.key} attached to scene {target.key}")
        if pc.options.include_quizzes:
            quizzes = [s for s in scenes if s.type == "quiz_checkpoint"]
            if quizzes and not any(x.key in q.objective_keys for q in quizzes):
                target = next((q for q in quizzes if q.concept_key in x.concept_keys), quizzes[-1])
                target.objective_keys.append(x.key)
                rep.notes.append(f"objective {x.key} assessed by quiz {target.key}")


def _ensure_misconceptions(g: GenPlan, rep: NormalizeReport) -> None:
    """Attach every misconception no scene confronts to a scene of its concept (quiz first, then teaching)."""
    scenes = _scenes(g)
    for m in g.misconceptions:
        if any(m.key in s.misconception_keys for s in scenes):
            continue
        same = [s for s in scenes if m.concept_key and s.concept_key == m.concept_key]
        target = next((s for s in same if s.type == "quiz_checkpoint"), None)
        target = target or next((s for s in same if s.type in ("content", "example")), None)
        if target is not None:
            target.misconception_keys.append(m.key)
            rep.notes.append(f"misconception {m.key} confronted in scene {target.key}")


def chapter_of(g: GenPlan, scene_key: str) -> GenChapter | None:
    for ch in g.chapters:
        if any(s.key == scene_key for s in ch.scenes):
            return ch
    return None
