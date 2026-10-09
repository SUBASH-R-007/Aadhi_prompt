"""Pipeline contract: options, intermediate artifacts and stage signatures.

Stages (sibling modules, orchestrated by ``orchestrator.py``):

    ingest.ingest_source(ctx, source_doc)                       -> IngestResult
    brief.build_brief(ctx, ingest, options)                     -> ConceptBrief      (may raise BriefUnavailable)
    plan.make_plan(ctx, ingest, options, *, brief=None)         -> LecturePlan       (validated)
    script.write_scenes(ctx, plan, ingest, options, *, brief=None) -> Screenplay     (per-scene, parallel)
    validate.lint(screenplay, options)                          -> list[Issue]       (pure)
    critic.critique(ctx, screenplay, ingest, options, *, brief=None)        -> list[Issue]    (LLM judge)
    repair.repair(ctx, screenplay, issues, ingest, options, *, brief=None)  -> Screenplay     (only failing scenes)
    companion.build_sheet(ctx, screenplay, ingest, options, *, brief=None)  -> CompanionSheet (practice problems)

With a concept brief, the chunks it set aside (``ConceptBrief.skipped_chunks``) reach no stage after it:
planning, scene writing, the critic, repair and the companion sheet take ``brief=`` for that.
    assets.build_assets(ctx, screenplay, options, previous=None, scene_ids=None) -> AssetManifest
    aadhi.compose.timeline.build_timeline(...)                  -> Timeline

LLM-facing response models live in ``gen_models.py`` (no ids, no unions, no tuples, no open dicts);
``canonicalize.py`` turns them into canonical ``Screenplay`` objects and assigns every id.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..schemas.screenplay import (
    ConceptNode,
    LearningObjective,
    Misconception,
    SceneType,
    SidePanelKind,
    SourceFigure,
    StrictModel,
    _opt_slug,
    _slug_list,
    _slug_validator,
    assert_acyclic,
)

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en-IN": "English (India)",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "ta-IN": "Tamil",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
}

PIPELINE_PROGRESS = {  # overall progress budget per stage (generate_lecture)
    "ingest": (0.00, 0.06),
    "plan": (0.06, 0.18),  # concept brief (0.06-0.10) + plan; reported under the "plan" stage
    "script": (0.18, 0.45),
    "validate": (0.45, 0.52),
    "companion": (0.52, 0.55),
    "assets": (0.55, 0.97),
    "timeline": (0.97, 1.00),
}


def _lang(v: str | None) -> str | None:
    if v is not None and v not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language {v!r}")
    return v


class GenerationOptions(StrictModel):
    language: str = "en-IN"  # narration language
    board_language: str | None = None  # None = same as narration
    audience: str = Field(default="first-year engineering undergraduates", max_length=200)
    target_minutes: int = Field(default=15, ge=3, le=90)
    depth: Literal["overview", "standard", "deep"] = "standard"
    # Teacher-supplied metadata (wins over what the model extracts).
    subject_name: str | None = Field(default=None, max_length=240)
    unit_name: str | None = Field(default=None, max_length=240)
    session_number: str | None = Field(default=None, max_length=60)
    session_title: str | None = Field(default=None, max_length=300)
    previous_session_summary: str = Field(default="", max_length=4000)  # enables a recap scene
    extra_instructions: str = Field(default="", max_length=4000)
    # Pedagogy / media switches. The planner decides *whether* to use each; these are caps.
    include_quizzes: bool = True
    quiz_every_n_concepts: int = Field(default=2, ge=1, le=6)
    allow_manim: bool = True
    allow_freeform_manim: bool = True
    allow_generated_images: bool = True
    allow_ai_video: bool = False
    max_ai_videos: int = Field(default=2, ge=0, le=8)
    allow_interactive: bool = False  # p5 sketches (web player only)
    allow_gifs: bool = False
    # Generated images for this lecture (None = the server's IMAGE_PROVIDER, then its backups); the API
    # refuses a provider that is not configured for the requester (422 at options.image_provider).
    image_provider: Literal["gemini", "pollinations"] | None = None
    # Use a picture from the job owner's library (aadhi.library.match, score >= aadhi.library.AUTO_USE_THRESHOLD;
    # aadhi.pipeline.assets) for a side panel's generated image instead of generating one. Left out of the
    # serialised options when off, so stored options and checkpoint digests are unchanged.
    prefer_library_visuals: bool = Field(default=False, exclude_if=lambda v: v is False)
    review_plan: bool = False  # pause after planning for teacher approval
    # Voice
    tts_provider: Literal["edge", "gemini", "openai", "elevenlabs", "fake"] | None = None
    tts_voice: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")
    tts_rate: str | None = Field(default=None, pattern=r"^[+-]\d{1,2}%$")
    # AI engine for this lecture (None = the server default LLM_PROVIDER); must be configured.
    llm_provider: Literal["gemini", "openai", "anthropic", "fake"] | None = None
    # Model overrides: honoured only for admins and only if in LLM_MODEL_ALLOWLIST (API strips otherwise).
    llm_model_plan: str | None = Field(default=None, max_length=80)
    llm_model_script: str | None = Field(default=None, max_length=80)

    _chk_lang = field_validator("language", "board_language")(_lang)


class SourceChunk(StrictModel):
    """Addressable slice of the source for grounding (``source_refs`` hold chunk ids)."""

    id: str = Field(pattern=r"^c\d{4,5}$")  # c0001 ...
    page: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str = Field(max_length=2000)


ExcludedCategory = Literal[
    "person",  # SME / author / reviewer / faculty names, designations, contact details
    "duration",  # video/clip/segment durations, estimated run times, word counts
    "timecode",  # "[0:10 - 1:15]" style timings
    "admin",  # course/subject codes, dates, versions, departments, institution boilerplate, page x of y
    "production_note",  # editing/recording instructions that are not teaching content
    "structure",  # clip/segment scaffolding: per-clip title cards, "bridge to next part", "what this video covers"
    "duplicate",  # content repeated verbatim elsewhere in the source
]


class ExcludedItem(StrictModel):
    """Something deliberately kept OUT of the teaching content (shown to the teacher for audit)."""

    category: ExcludedCategory
    text: str = Field(max_length=300)  # the removed text (shortened)
    reason: str = Field(default="", max_length=200)
    source: Literal["rules", "brief"] = "rules"  # deterministic filter or the concept-brief model


class SourceMeta(StrictModel):
    """Document-level metadata found in the source. Used ONLY to fill title cards when the teacher
    did not type them; never part of the teaching content."""

    subject_name: str = Field(default="", max_length=240)
    unit_name: str = Field(default="", max_length=240)
    session_number: str = Field(default="", max_length=60)
    session_title: str = Field(default="", max_length=300)


class VisualNote(StrictModel):
    """The source author's visual/animation suggestion (e.g. an SME script's ANIMATION line).
    Planners and scene writers may use it as a visual idea; it is never narrated or shown as text."""

    id: str = Field(pattern=r"^v\d{4,5}$")  # v0001 ...
    near_chunk_id: str | None = None  # the content chunk it belongs to
    text: str = Field(max_length=1500)


class IngestResult(StrictModel):
    """Normalised view of the uploaded source.

    ``markdown``/``chunks`` contain teachable content only: the source-scoping step
    (``aadhi.pipeline.source_scope``) moves document metadata to ``document_meta``, the author's
    visual directions to ``visual_notes`` and administrative/production data to ``excluded``.
    """

    markdown: str  # text with "<!-- page N -->" markers, headings, tables as markdown, LaTeX math
    chunks: list[SourceChunk] = Field(default_factory=list)
    document_meta: SourceMeta = Field(default_factory=SourceMeta)
    visual_notes: list[VisualNote] = Field(default_factory=list, max_length=500)
    excluded: list[ExcludedItem] = Field(default_factory=list, max_length=500)
    source_format: Literal["sme_script", "notes", "textbook", "unknown"] = "unknown"
    pages: int | None = None
    figures: list[SourceFigure] = Field(default_factory=list)
    # When True the original file is also attached natively to planning/script LLM calls
    # (multimodal models read layout, figures, equations and scanned pages directly).
    attach_original: bool = False
    source_mime: str = ""
    source_storage_key: str | None = None
    detected_language: str | None = None
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


NarrativeRole = Literal[
    "hook", "context", "prerequisite", "concept", "example", "practice", "check", "application", "synthesis", "transition"
]


class BriefFact(StrictModel):
    text: str = Field(max_length=600)  # a definition, law, formula (LaTeX allowed), property or result
    source_refs: list[str] = Field(default_factory=list, max_length=10)


class BriefConcept(StrictModel):
    """One concept the learner must understand, distilled from the source."""

    key: str = Field(max_length=64)  # ASCII snake_case
    name: str = Field(max_length=160)
    why_it_matters: str = Field(default="", max_length=400)
    must_explain: list[str] = Field(default_factory=list, max_length=12)  # what the explanation must cover
    key_facts: list[BriefFact] = Field(default_factory=list, max_length=20)
    examples: list[str] = Field(default_factory=list, max_length=10)  # worked examples / analogies from the source
    prerequisites: list[str] = Field(default_factory=list, max_length=10)  # concept keys
    source_refs: list[str] = Field(default_factory=list, max_length=20)


SkipReason = Literal[
    "administrative",  # a subject / unit / session line, names, codes, dates, institution boilerplate
    "production",  # recording, editing, camera or animation directions
    "scaffolding",  # clip packaging: title cards, "coming up next" bridges, sign-offs, repeated video intros
    "duplicate",  # a verbatim repeat of another chunk
    "off_topic",  # material unrelated to the lecture's subject
]


class SkippedChunk(StrictModel):
    """A source chunk the concept brief judged not to be teaching content."""

    chunk_id: str = Field(pattern=r"^c\d{4,5}$")
    reason: SkipReason


class ConceptBrief(StrictModel):
    """What the lecture must teach: the source's concepts, in teaching order, with nothing else.

    Produced by ``aadhi.pipeline.brief`` from the scoped source; the planner builds the lecture
    from it (and cites the source chunks)."""

    topic: str = Field(default="", max_length=300)
    concepts: list[BriefConcept] = Field(default_factory=list, max_length=40)
    teaching_order: list[str] = Field(default_factory=list, max_length=40)  # concept keys
    source_questions: list[str] = Field(default_factory=list, max_length=20)  # quiz/practice items in the source
    source_question_refs: list[str] = Field(default_factory=list, max_length=100)  # chunks holding those questions
    excluded: list[ExcludedItem] = Field(default_factory=list, max_length=100)  # semantic exclusions
    # Every source chunk is either cited by a concept / source question or listed here with a reason.
    # When a brief exists, planners and scene writers only receive the chunks that are not skipped.
    # The brief's own skips (<= 500, brief.skipped_from_gen) plus the teacher's set-asides (<= source_review.MAX_EXCLUDED).
    skipped_chunks: list[SkippedChunk] = Field(default_factory=list, max_length=1000)
    notes: str = Field(default="", max_length=1000)

    def cited_chunk_ids(self) -> set[str]:
        """Chunks cited by a concept, one of its key facts or a source question."""
        out = set(self.source_question_refs)
        for c in self.concepts:
            out.update(c.source_refs)
            out.update(r for f in c.key_facts for r in f.source_refs)
        return out

    def skipped_chunk_ids(self) -> set[str]:
        """Chunks the brief set aside as non-teaching material."""
        return {s.chunk_id for s in self.skipped_chunks}


class PlannedScene(StrictModel):
    id: str
    type: SceneType
    concept_id: str | None = None
    objective_ids: list[str] = Field(default_factory=list)
    goal: str = Field(max_length=600)  # what the learner should get from this scene
    key_points: list[str] = Field(default_factory=list, max_length=12)
    source_refs: list[str] = Field(default_factory=list, max_length=20)  # chunk ids
    side_panel_kind: SidePanelKind | None = None
    visual_rationale: str = Field(default="", max_length=400)
    manim_template: str | None = None  # simulation scenes: suggested template (validated against registry)
    misconception_ids: list[str] = Field(default_factory=list)  # to target (warning callouts / quiz distractors)
    est_seconds: int = Field(default=45, ge=5, le=600)
    # Flow: where this scene sits in the lecture's arc and how it follows from the previous scene.
    narrative_role: NarrativeRole | None = None
    bridge_in: str = Field(default="", max_length=300)  # the link from the previous scene, in one sentence
    visual_note_ids: list[str] = Field(default_factory=list, max_length=10)  # IngestResult.visual_notes used

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_id", mode="before")(_opt_slug)
    _norm_lists = field_validator("objective_ids", "misconception_ids", mode="before")(_slug_list)

    @model_validator(mode="after")
    def _template_only_for_sim(self) -> "PlannedScene":
        if self.manim_template and self.type != "simulation" and self.side_panel_kind != "manim":
            raise ValueError(f"scene {self.id}: manim_template only for simulation scenes / manim panels")
        return self


class PlannedChapter(StrictModel):
    id: str
    title: str = Field(max_length=240)
    concept_ids: list[str] = Field(default_factory=list)
    scenes: list[PlannedScene] = Field(default_factory=list, max_length=60)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_ids", mode="before")(_slug_list)


class LecturePlan(StrictModel):
    subject_name: str = ""
    unit_name: str = ""
    session_number: str = "Session 1"
    session_title: str = ""
    learning_objectives: list[LearningObjective] = Field(default_factory=list, max_length=12)
    concept_map: list[ConceptNode] = Field(default_factory=list, max_length=150)
    misconceptions: list[Misconception] = Field(default_factory=list, max_length=60)
    glossary_terms: list[str] = Field(default_factory=list, max_length=200)  # seeds the TTS lexicon
    chapters: list[PlannedChapter] = Field(default_factory=list, max_length=40)
    notes: str = ""

    @model_validator(mode="after")
    def _refs(self) -> "LecturePlan":
        concepts = {c.id for c in self.concept_map}
        if len(concepts) != len(self.concept_map):
            raise ValueError("concept ids must be unique")
        assert_acyclic({c.id: list(c.depends_on) for c in self.concept_map})
        objectives = {o.id for o in self.learning_objectives}
        miscs = {m.id for m in self.misconceptions}
        seen: set[str] = set()
        for ch in self.chapters:
            for s in ch.scenes:
                if s.id in seen:
                    raise ValueError(f"duplicate planned scene id {s.id}")
                seen.add(s.id)
                if s.concept_id and concepts and s.concept_id not in concepts:
                    raise ValueError(f"scene {s.id}: unknown concept {s.concept_id}")
                if objectives and any(o not in objectives for o in s.objective_ids):
                    raise ValueError(f"scene {s.id}: unknown objective ids")
                if miscs and any(m not in miscs for m in s.misconception_ids):
                    raise ValueError(f"scene {s.id}: unknown misconception ids")
        return self

    def all_scenes(self) -> list[PlannedScene]:
        return [s for ch in self.chapters for s in ch.scenes]


Severity = Literal["error", "warning", "info"]
_ISSUE_CODE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


class IssueData(StrictModel):
    """Structured detail of an issue for the Studio (declared fields only: issues stay LLM-schema compatible)."""

    # Machine-readable reason, e.g. on manim.render_failed: timeout | memory_limit | latex | frame_limit ...
    # (aadhi.manim.base.FAILURE_CATEGORIES).
    category: str | None = Field(default=None, max_length=64)


class Issue(StrictModel):
    code: str  # e.g. "board.too_many_items", "quiz.missing", "grounding.unsupported_claim"
    severity: Severity = "warning"
    message: str
    scene_id: str | None = None
    beat_id: str | None = None
    source: Literal["lint", "critic", "manim", "assets", "system"] = "lint"
    fixable: bool = True  # repair stage may rewrite the scene to fix it
    # Optional structured detail, e.g. {"category": "timeout"} on manim.render_failed. Left out of the
    # JSON when absent, so stored issues (and every issue without detail) keep their shape.
    # Field(exclude_if=...) needs pydantic>=2.12 (the floor in requirements.txt).
    data: IssueData | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _ISSUE_CODE.match(v):
            raise ValueError(f"issue code must look like 'area.problem', got {v!r}")
        return v
