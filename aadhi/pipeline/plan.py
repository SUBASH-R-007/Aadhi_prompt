"""Stage 2 — plan: one structured call -> validated ``LecturePlan`` (+ lexicon seed).

The planner sees the request (audience, depth, duration, languages, allowed media), teacher
metadata (document metadata fills what the teacher left empty), the previous-session summary, a note
about the source, the concept brief ("Concepts to teach": what the lecture must teach, in teaching
order), the Manim template catalogue, the source figures, the author's visual suggestions and the
source chunks (or the original PDF natively when ingest flagged ``attach_original`` and the
provider is Gemini). Administrative and production data that source scoping removed
(``IngestResult.excluded``, ``ConceptBrief.excluded``) never reaches the model, not even as things
to avoid; lint checks the lecture for leaks deterministically. With a brief, the chunks it set aside
as non-teaching material (``ConceptBrief.skipped_chunks``) are left out of "Source chunks" (only their
number is mentioned) and plan refs to them are dropped (``plan_rules``). Semantic problems go back to the
model inside the ``generate_json`` re-ask loop; whatever is still wrong afterwards is fixed
deterministically by ``plan_rules.normalize_plan``.

A source the model could not see (a scanned PDF with almost no extractable text, while the original
file cannot be attached) is refused with an ``IngestError`` before any paid call
(``require_grounding``): a lecture with no grounding must never be marked ready.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import FileInput
from ..schemas.screenplay import ConceptNode, LearningObjective, LexiconEntry, Misconception
from . import integrations
from .base import (
    ConceptBrief,
    GenerationOptions,
    IngestResult,
    LecturePlan,
    PlannedChapter,
    PlannedScene,
    SourceMeta,
)
from .gen_models import GenPlan
from .ingest import IngestError, readable_chars
from .plan_rules import (
    BRIDGE_MAX,
    CHAPTER_TITLE_MAX,
    MAX_CHAPTER_SCENES,
    MAX_CHAPTERS,
    MAX_SCENES,
    MAX_VISUAL_NOTES,
    PlanContext,
    normalize_plan,
    plan_problems,
)
from .prompting import extract_json, join_sections, json_section, language_label, section, system_prompt
from .scene_context import shorten
from .validate import header_values

log = logging.getLogger(__name__)

PLAN_PROMPTS = ("style", "plan")
PLAN_CONTEXT_CHARS = 250_000
MAX_ATTACH_BYTES = 20 * 1024 * 1024
MIN_GROUNDING_CHARS = 200  # extracted text below max(this, 40/page) cannot ground a lecture on its own
MIN_GROUNDING_CHARS_PER_PAGE = 40
PLAN_VISUAL_NOTES_MAX = 80  # author's visual suggestions shown to the planner
PLAN_VISUAL_NOTE_CHARS = 400  # each shortened at a sentence / word boundary
PLAN_VISUAL_CHARS = 24_000  # total


@dataclass
class PlanResult:
    plan: LecturePlan
    lexicon: list[LexiconEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class TeacherMeta:
    """Project-level metadata (options win over these, these win over the model)."""

    subject_name: str = ""
    unit_name: str = ""
    session_number: str = ""
    session_title: str = ""


def plan_model(options: GenerationOptions, settings: Any) -> str:
    """The lecture engine's planning model (an admin's ``llm_model_plan`` override for that engine wins)."""
    return integrations.llm_model(settings, "plan", options, override=options.llm_model_plan)


def skipped_ids(brief: ConceptBrief | None) -> frozenset[str]:
    """Chunks the concept brief set aside as non-teaching material (none without a brief)."""
    return frozenset(brief.skipped_chunk_ids()) if brief is not None else frozenset()


def plan_context(ingest: IngestResult, options: GenerationOptions, settings: Any,
                 brief: ConceptBrief | None = None) -> PlanContext:
    templates = {t.name for t in integrations.list_templates()}
    skipped = skipped_ids(brief)
    return PlanContext(
        options=options,
        chunk_ids={c.id for c in ingest.chunks},
        skipped_chunk_ids=set(skipped),
        templates=templates,
        has_figures=bool(ingest.figures),
        freeform_manim=bool(settings.manim_allow_freeform),
        manim_enabled=settings.manim_sandbox != "disabled",
        images_enabled=settings.image_provider != "none",
        visual_note_ids={v.id for v in ingest.visual_notes},
        source_text="\n".join(c.text for c in ingest.chunks if c.id not in skipped),
    )


def _chunk_payload(ingest: IngestResult, budget: int, skip: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The source chunks for the planner, without those the concept brief set aside (``skip``)."""
    chunks = [c for c in ingest.chunks if c.id not in skip]
    total = sum(len(c.text) for c in chunks) or 1
    scale = min(1.0, budget / total)
    out = []
    for c in chunks:
        text = c.text if scale >= 1.0 else c.text[: max(300, int(len(c.text) * scale))]
        out.append({"id": c.id, "page": c.page, "heading": " > ".join(c.heading_path), "text": text})
    return out


def excluded_matcher(ingest: IngestResult, brief: ConceptBrief | None = None) -> Callable[[str], bool]:
    """Predicate: does a text contain something source scoping removed? (keeps it out of prompts)"""
    needles: set[str] = set()
    for item in [*ingest.excluded, *(brief.excluded if brief is not None else [])]:
        for value in [item.text, *header_values(item.text)]:
            value = " ".join(value.split()).lower()
            if len(value) >= 4:
                needles.add(value)

    def matches(text: str) -> bool:
        low = " ".join((text or "").split()).lower()
        return any(n in low for n in needles)

    return matches


def brief_payload(brief: ConceptBrief, ingest: IngestResult) -> dict[str, Any]:
    """The concept brief for the planner: concepts in teaching order (never its exclusions)."""
    order = {k: i for i, k in enumerate(brief.teaching_order)}
    concepts = sorted(brief.concepts, key=lambda c: order.get(c.key, len(order)))
    out: dict[str, Any] = {
        "topic": brief.topic,
        "teaching_order": list(brief.teaching_order),
        "concepts": [
            {
                "key": c.key, "name": c.name, "why_it_matters": c.why_it_matters, "must_explain": list(c.must_explain),
                "key_facts": [{"text": f.text, "source_refs": list(f.source_refs)} for f in c.key_facts],
                "examples": list(c.examples), "prerequisites": list(c.prerequisites), "source_refs": list(c.source_refs),
            }
            for c in concepts
        ],
        "source_questions": list(brief.source_questions),
    }
    if brief.notes.strip() and not excluded_matcher(ingest, brief)(brief.notes):
        out["notes"] = brief.notes.strip()
    return out


def visual_note_payload(ingest: IngestResult, *, limit: int = PLAN_VISUAL_NOTES_MAX, chars: int = PLAN_VISUAL_NOTE_CHARS,
                        budget: int = PLAN_VISUAL_CHARS, skip: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The author's visual suggestions (id, nearby chunk, shortened text) within ``budget`` characters; those next to
    a chunk the concept brief set aside (``skip``) are left out with it."""
    out: list[dict[str, Any]] = []
    used = 0
    for v in [v for v in ingest.visual_notes if not (v.near_chunk_id and v.near_chunk_id in skip)][:limit]:
        text = shorten(v.text, chars)
        if not text:
            continue
        if used + len(text) > budget:
            break
        out.append({"id": v.id, "near_chunk": v.near_chunk_id, "text": text})
        used += len(text)
    return out


# SME scripts repeat narration as board lines; scoping drops exact repeats, reworded ones remain
TWICE_NOTE = "A point may appear twice in a chunk, once as narration and once as a board line: it is one point, taught once."
SOURCE_FORMAT_NOTES = {
    "sme_script": (
        "The source is an author's script for a video lesson, originally written as several clips and segments with "
        "on-screen directions and narration. Its administrative and production details (names, dates, codes, clip and "
        "segment labels, timecodes, durations, recording directions) were removed before you see it, and the author's "
        "visual directions are listed separately as suggestions. Ignore any such detail that remains: plan one "
        "continuous session that teaches the subject matter. " + TWICE_NOTE
    ),
    "notes": ("The source is the teacher's notes. Administrative details (headers, names, dates, codes) were removed; "
              "ignore any that remain and teach the subject matter."),
    "textbook": ("The source is textbook material. Page furniture and administrative details were removed; ignore any "
                 "that remain and teach the subject matter."),
    "unknown": ("Teach the subject matter of the source only; ignore administrative or production details (author names, "
                "dates, codes, durations, recording directions) if any remain."),
}
LENGTH_NOTE = ("The session length comes from the Request (target_minutes); ignore any video, clip or segment "
               "duration the source mentions.")


def set_aside_note(n: int) -> str:
    """How many sections the concept brief set aside (a count only: never their text or reasons)."""
    if not n:
        return ""
    what = "1 section of the source" if n == 1 else f"{n} sections of the source"
    return (f"The concept brief set aside {what} as non-teaching material (document details, production notes or "
            "video packaging); the source chunks below hold the teaching content only.")


def about_source(ingest: IngestResult, attached: bool, brief: ConceptBrief | None = None) -> str:
    """The "About the source" note: scoping, length, the sections the brief set aside (a count), extraction caveats
    (never anything that was excluded)."""
    leaks = excluded_matcher(ingest, brief)
    lines = [SOURCE_FORMAT_NOTES.get(ingest.source_format, SOURCE_FORMAT_NOTES["unknown"]), LENGTH_NOTE]
    known = {c.id for c in ingest.chunks}
    lines.append(set_aside_note(sum(1 for cid in skipped_ids(brief) if cid in known)))
    lines += [w for w in ingest.warnings if w.strip() and not leaks(w)]
    lines.append(source_note_for(ingest, attached))
    return "\n".join(x for x in lines if x).strip()


def with_document_meta(meta: TeacherMeta, doc: SourceMeta) -> TeacherMeta:
    """Teacher metadata with its empty fields filled from the source's own header (title cards only)."""
    return TeacherMeta(
        subject_name=meta.subject_name or doc.subject_name, unit_name=meta.unit_name or doc.unit_name,
        session_number=meta.session_number or doc.session_number, session_title=meta.session_title or doc.session_title,
    )


def build_plan_prompt(ingest: IngestResult, options: GenerationOptions, pc: PlanContext, meta: TeacherMeta,
                      *, attached: bool = False, brief: ConceptBrief | None = None) -> str:
    """User prompt for the planner (sections + JSON blocks).

    ``attached``: the original file goes along; ``brief``: the concept brief ("Concepts to teach").
    Nothing from ``ingest.excluded`` / ``brief.excluded`` is included, and with a brief the "Source chunks"
    leave out the chunks it set aside (``brief.skipped_chunks``): the cited ones stay, and so does any chunk
    the brief neither cited nor skipped.
    """
    o = options
    request = {
        "audience": o.audience,
        "depth": o.depth,
        "target_minutes": o.target_minutes,
        "target_seconds": o.target_minutes * 60,
        "narration_language": language_label(o.language),
        "board_language": language_label(o.board_language or o.language),
        "include_quizzes": o.include_quizzes,
        "quiz_every_n_concepts": o.quiz_every_n_concepts,
        "allowed_scene_types": pc.allowed_scene_types(),
        "allowed_side_panels": pc.allowed_panels(),
        "has_previous_session_summary": bool(o.previous_session_summary.strip()),
        "subject_name": o.subject_name or meta.subject_name or None,
        "unit_name": o.unit_name or meta.unit_name or None,
        "session_number": o.session_number or meta.session_number or None,
        "session_title": o.session_title or meta.session_title or None,
    }
    templates = [
        {"name": t.name, "title": t.title, "when_to_use": t.description, "steps": t.steps_hint}
        for t in integrations.list_templates()
    ]
    figures = [{"id": f.id, "page": f.page, "caption": f.caption} for f in ingest.figures]
    notes = visual_note_payload(ingest, skip=skipped_ids(brief))
    has_brief = brief is not None and bool(brief.concepts)
    what = 'the concepts in "Concepts to teach", in dependency order' if has_brief else "the source's subject matter"
    return join_sections(
        json_section("Request", request),
        section("Previous session summary", o.previous_session_summary),
        section("Teacher's extra instructions", o.extra_instructions),
        section("About the source", about_source(ingest, attached, brief)),
        json_section("Concepts to teach", brief_payload(brief, ingest)) if has_brief else "",
        json_section("Animation templates", templates) if templates else "",
        json_section("Source figures", figures) if figures else "",
        json_section("Author's visual suggestions", notes) if notes else "",
        json_section("Source chunks", _chunk_payload(ingest, PLAN_CONTEXT_CHARS, skipped_ids(brief))),
        section("Task", f"Plan this lecture as one continuous session that teaches {what}, grounded in the source "
                        "chunks. Respond with JSON that matches the response schema."),
    )


def request_from_prompt(prompt: str) -> dict[str, Any]:
    """Parse the Request block back (used by fake responders)."""
    return extract_json(prompt, "Request") or {}


def make_validator(pc: PlanContext):
    """Semantic validator for ``generate_json``: everything on the first pass, hard problems after."""
    calls = {"n": 0}

    def validate(gen: GenPlan) -> list[str]:
        calls["n"] += 1
        hard, soft = plan_problems(gen, pc)
        if hard:
            return hard
        if calls["n"] == 1:
            return soft
        try:  # later passes: accept anything that normalises into a valid LecturePlan
            to_lecture_plan(normalize_plan(gen, pc)[0], pc.options, TeacherMeta())
        except ValueError as exc:
            return [str(exc)[:500]]
        return []

    return validate


def to_lecture_plan(gen: GenPlan, options: GenerationOptions, meta: TeacherMeta) -> LecturePlan:
    """Canonical LecturePlan from a normalised GenPlan (keys already unique slugs)."""
    concepts = {c.key for c in gen.concept_map[:150]}
    objectives = {x.key for x in gen.learning_objectives[:12]}
    miscs = {m.key for m in gen.misconceptions[:60]}
    chapters = []
    budget = MAX_SCENES  # the plan must also fit a Screenplay (<= 200 scenes, chapter titles <= 160)
    for ch in gen.chapters[:MAX_CHAPTERS]:
        kept = ch.scenes[: min(MAX_CHAPTER_SCENES, budget)]
        if ch.scenes and not kept:
            continue
        budget -= len(kept)
        scenes = [
            PlannedScene(
                id=s.key, type=s.type, concept_id=s.concept_key if s.concept_key in concepts else None,
                objective_ids=[k for k in s.objective_keys if k in objectives][:10],
                goal=(s.goal or s.key)[:600], key_points=[k[:300] for k in s.key_points][:12],
                source_refs=s.source_refs[:20], side_panel_kind=s.side_panel_kind,
                visual_rationale=s.visual_rationale[:400], manim_template=s.manim_template,
                misconception_ids=[k for k in s.misconception_keys if k in miscs], est_seconds=s.est_seconds,
                narrative_role=s.narrative_role, bridge_in=s.bridge_in[:BRIDGE_MAX],
                visual_note_ids=s.visual_note_ids[:MAX_VISUAL_NOTES],
            )
            for s in kept
        ]
        chapters.append(PlannedChapter(
            id=ch.key, title=ch.title[:CHAPTER_TITLE_MAX].strip() or ch.key,
            concept_ids=[k for k in ch.concept_keys if k in concepts], scenes=scenes,
        ))
    return LecturePlan(
        subject_name=(options.subject_name or meta.subject_name or gen.subject_name)[:240],
        unit_name=(options.unit_name or meta.unit_name or gen.unit_name)[:240],
        session_number=(options.session_number or meta.session_number or gen.session_number or "Session 1")[:60],
        session_title=(options.session_title or meta.session_title or gen.session_title)[:300],
        learning_objectives=[
            LearningObjective(id=x.key, text=x.text[:400] or x.key, bloom=x.bloom,
                              concept_ids=[k for k in x.concept_keys if k in concepts][:10])
            for x in gen.learning_objectives[:12]
        ],
        concept_map=[
            ConceptNode(id=c.key, title=c.title[:160] or c.key, summary=c.summary[:600],
                        depends_on=[d for d in c.depends_on if d in concepts][:20], kind=c.kind)
            for c in gen.concept_map[:150]
        ],
        misconceptions=[
            Misconception(id=m.key, concept_id=m.concept_key if m.concept_key in concepts else None, statement=m.statement[:600] or m.key,
                          correction=m.correction[:800] or "See the lecture.")
            for m in gen.misconceptions[:60]
        ],
        glossary_terms=[t.written[:60] for t in gen.glossary_terms if t.written.strip()][:200],
        chapters=chapters,
        notes=gen.notes,
    )


def lexicon_seed(gen: GenPlan, language: str) -> list[LexiconEntry]:
    """Lexicon entries from the plan's glossary (spoken forms; keep_in_english flags)."""
    out: list[LexiconEntry] = []
    seen: set[str] = set()
    for t in gen.glossary_terms:
        written, spoken = t.written.strip()[:60], (t.spoken or "").strip()[:120]
        if not written or written.lower() in seen:
            continue
        if spoken.lower() == written.lower() and not t.keep_in_english:
            continue
        seen.add(written.lower())
        out.append(LexiconEntry(written=written, spoken=spoken or written, language=None, keep_in_english=t.keep_in_english))
    return out[:200]


ATTACHMENT_NOTE = ("The original document is attached. Read it directly for equations, figures and scanned "
                   "pages; cite the chunk ids below for grounding. It may still show administrative or production "
                   "details (names, dates, codes, durations, recording directions): ignore them.")
NOT_ATTACHED_NOTE = ("Parts of the source (scanned pages, equations or figures) could not be extracted as text and the "
                     "original file is not available to you. Use only what the source chunks say; never invent "
                     "content for the missing parts.")


def source_note_for(ingest: IngestResult, attached: bool) -> str:
    """What the model is told about the original file (nothing when ingest did not ask for it)."""
    if attached:
        return ATTACHMENT_NOTE
    return NOT_ATTACHED_NOTE if ingest.attach_original else ""


SCENE_SCRIPT_NOTE = ("The source is an author's script for a video lesson. Treat its narration as raw material: keep the "
                     "technical content and good analogies, rewrite it for this scene and its place in the lecture, and "
                     "leave out the script's own structure (clips, segments, timings, introductions and sign-offs). "
                     + TWICE_NOTE)


def scene_source_note(ingest: IngestResult, attached: bool) -> str:
    """The scene writers' "About the source" note: the source's kind (video scripts) + the attachment note."""
    parts = [SCENE_SCRIPT_NOTE if ingest.source_format == "sme_script" else "", source_note_for(ingest, attached)]
    return "\n".join(x for x in parts if x)


def require_grounding(ingest: IngestResult, files: Sequence[FileInput]) -> None:
    """Refuse a source the model would barely see (``IngestError``, raised before any paid call).

    Applies when ingest asked for the original file (scanned / garbled PDF) but it could not be
    attached (provider without native PDF reading, or the file is too large) and the extracted text
    is below ``max(MIN_GROUNDING_CHARS, MIN_GROUNDING_CHARS_PER_PAGE * pages)``. ``[Figure ...]``
    markers and page comments do not count as text.
    """
    if files or not ingest.attach_original:
        return
    need = max(MIN_GROUNDING_CHARS, MIN_GROUNDING_CHARS_PER_PAGE * (ingest.pages or 0))
    if readable_chars(ingest.markdown) >= need:
        return
    raise IngestError(
        "the PDF looks scanned: almost no text could be extracted, and the original file cannot be given to the "
        "configured AI model (it reads extracted text only, or the file is over 20 MB). Run OCR on the PDF or "
        "upload a text-based PDF/DOCX, or use an AI provider that reads PDFs directly (Gemini)."
    )


async def original_attachment(ctx: Any, ingest: IngestResult, llm: Any, *, purpose: str = "planner") -> list[FileInput]:
    """The uploaded file as a native attachment when ingest asked for it (scanned / maths-heavy PDFs).

    Only multimodal providers that read documents natively get it (Gemini); others work from the
    extracted text. Oversized files are skipped with a warning.
    """
    if not ingest.attach_original or getattr(llm, "name", "") != "gemini" or not ingest.source_storage_key:
        return []
    data = await asyncio.to_thread(ctx.storage.get_bytes, ingest.source_storage_key)
    if len(data) > MAX_ATTACH_BYTES:
        ctx.log(f"The original file is too large to attach for the {purpose}; using extracted text only.", "warning")
        return []
    return [FileInput(data=data, mime=ingest.source_mime or "application/pdf", name="source")]


async def generate_plan(
    ctx: Any, ingest: IngestResult, options: GenerationOptions, meta: TeacherMeta | None = None,
    *, brief: ConceptBrief | None = None,
) -> PlanResult:
    """Plan the lecture (LLM + validation + deterministic fixes), from the concept ``brief`` when given."""
    meta = with_document_meta(meta or TeacherMeta(), ingest.document_meta)
    settings = ctx.settings
    pc = plan_context(ingest, options, settings, brief)
    llm = integrations.get_llm(settings, integrations.llm_engine(options, settings))
    model = plan_model(options, settings)
    files = await original_attachment(ctx, ingest, llm)
    require_grounding(ingest, files)
    if files:
        ctx.log("The original file is given to the AI model so scanned pages and equations are read directly.")
    elif ingest.attach_original:
        ctx.log("Parts of the source (scanned pages or equations) could not be extracted as text, and the AI model "
                "cannot read the original file: the lecture uses the extracted text only.", "warning")
    async with integrations.limit("llm"):
        gen: GenPlan = await llm.generate_json(
            model=model,
            system=system_prompt(*PLAN_PROMPTS),
            prompt=build_plan_prompt(ingest, options, pc, meta, attached=bool(files), brief=brief),
            schema=GenPlan,
            files=files,
            temperature=0.5,
            on_usage=ctx.record_usage,
            validate=make_validator(pc),
            validation_retries=2,
        )
    fixed, report = normalize_plan(gen, pc)
    for note in report.notes:
        log.info("plan fix: %s", note)
    plan = to_lecture_plan(fixed, options, meta)
    return PlanResult(plan=plan, lexicon=lexicon_seed(fixed, options.language), notes=report.notes, model=model)


async def make_plan(ctx: Any, ingest: IngestResult, options: GenerationOptions, *,
                    brief: ConceptBrief | None = None) -> LecturePlan:
    """Contract entry point (see ``aadhi.pipeline.base``); ``brief`` as in ``generate_plan``."""
    return (await generate_plan(ctx, ingest, options, brief=brief)).plan
