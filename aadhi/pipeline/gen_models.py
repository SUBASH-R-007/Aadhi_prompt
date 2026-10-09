"""LLM-facing response models (structured generation).

These models are what the language model fills in; ``canonicalize.py`` turns them into the
canonical screenplay classes. Rules (checked by ``aadhi.providers.llm.schema.assert_llm_compatible``):

* no ids: the model writes ASCII ``key`` fields in plans and nothing id-like in scenes;
* no unions except ``Optional``; no tuples; no open dicts (``dict[str, Any]``);
* flat, nullable optional fields instead of nested variants (e.g. ``GenSidePanel``);
* no numeric/length constraints (they are enforced during canonicalisation and fed back to the
  model as validation problems instead).

Simulation models are built per template with ``pydantic.create_model`` so the template's own
``params_model`` becomes part of the response schema.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from . import integrations
from .base import NarrativeRole

GenSceneType = Literal[
    "title", "content", "example", "summary", "key_takeaway", "recap",
    "chapter_card", "simulation", "ai_video", "interactive", "quiz_checkpoint",
]
GenPanelKind = Literal["skill_tree", "figure", "image", "chart", "graph", "model_3d", "manim", "terminal", "quiz", "gif"]
GenBoardKind = Literal[
    "heading", "bullet", "paragraph", "definition", "formula", "callout_info", "callout_tip",
    "callout_warning", "misconception", "code", "table", "figure", "example_step", "takeaway",
]
GenBloom = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
GenMascotPosition = Literal["left", "right", "center", "popup_bottom_left", "popup_bottom_right", "hidden"]

BOARD_SCENE_TYPES = ("title", "content", "example", "summary", "key_takeaway", "recap")


class GenModel(BaseModel):
    """Base for generation models: tolerant of extra keys, strips strings."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class GenConcept(GenModel):
    key: str = Field(description="short ASCII snake_case key, unique in the plan, e.g. 'ohms_law'")
    title: str = Field(description="concept title in the board language")
    summary: str = ""
    depends_on: list[str] = Field(default_factory=list, description="keys of concepts this one builds on")
    kind: Literal["prerequisite", "core"] = "core"


class GenObjective(GenModel):
    key: str = Field(description="short ASCII key, e.g. 'obj_apply_ohm'")
    text: str = Field(description="measurable objective starting with a verb")
    bloom: GenBloom = "understand"
    concept_keys: list[str] = Field(default_factory=list)


class GenMisconception(GenModel):
    key: str
    concept_key: str | None = None
    statement: str = Field(description="what students wrongly believe, in their words")
    correction: str = Field(description="what is actually true, and why")


class GenGlossaryTerm(GenModel):
    written: str = Field(description="term as written on the board, e.g. 'BJT'")
    spoken: str = Field(description="how the narrator should pronounce it, e.g. 'B J T'")
    keep_in_english: bool = Field(default=False, description="true for technical terms never translated")


class GenPlannedScene(GenModel):
    key: str = Field(description="short ASCII key, unique in the plan, e.g. 'ohm_intro'")
    type: GenSceneType
    narrative_role: NarrativeRole | None = Field(
        default=None,
        description="the scene's job in the lecture's arc: hook (scene 1 only), context, prerequisite, concept, "
                    "example, practice, check, application, synthesis or transition",
    )
    concept_key: str | None = None
    objective_keys: list[str] = Field(default_factory=list)
    bridge_in: str = Field(
        default="",
        description="one sentence linking this scene to the previous one (what the learner now knows -> what this "
                    "scene adds); empty only for the first scene",
    )
    goal: str = Field(description="what the learner should get from this scene, one or two sentences")
    key_points: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list, description="source chunk ids, e.g. 'c0004'")
    side_panel_kind: GenPanelKind | None = None
    visual_note_ids: list[str] = Field(
        default_factory=list, description="ids of the author's visual suggestions this scene uses, e.g. 'v0003'"
    )
    visual_rationale: str = Field(default="", description="why the visual helps learning; empty if none")
    manim_template: str | None = Field(default=None, description="template name for simulation scenes / manim panels")
    misconception_keys: list[str] = Field(default_factory=list)
    est_seconds: int = 45


class GenChapter(GenModel):
    key: str
    title: str
    concept_keys: list[str] = Field(default_factory=list)
    scenes: list[GenPlannedScene] = Field(default_factory=list)


class GenPlan(GenModel):
    subject_name: str = ""
    unit_name: str = ""
    session_number: str = ""
    session_title: str = ""
    learning_objectives: list[GenObjective] = Field(default_factory=list)
    concept_map: list[GenConcept] = Field(default_factory=list)
    misconceptions: list[GenMisconception] = Field(default_factory=list)
    glossary_terms: list[GenGlossaryTerm] = Field(default_factory=list)
    chapters: list[GenChapter] = Field(default_factory=list)
    notes: str = Field(default="", description="short note to the teacher about the plan")


# ---------------------------------------------------------------------------
# Board items, beats, side panels
# ---------------------------------------------------------------------------


class GenVariable(GenModel):
    symbol_latex: str
    meaning: str
    unit: str = ""


class GenBoardItem(GenModel):
    kind: GenBoardKind
    text: str = ""
    term: str | None = None
    latex: str | None = None
    variables: list[GenVariable] = Field(default_factory=list)
    code_language: str | None = None
    code: str | None = None
    table_headers: list[str] | None = None
    table_rows: list[list[str]] | None = None
    figure_id: str | None = None
    caption: str | None = None
    justification: str | None = None
    blank: bool = Field(default=False, description="example_step only: show as a blank to be filled later")
    misconception_key: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class GenBeat(GenModel):
    narration: str = Field(description="what Aadhi says: plain spoken text, 1-3 sentences, no markup")
    spoken: str | None = Field(default=None, description="optional pronunciation override for TTS only")
    board: GenBoardItem | None = Field(default=None, description="board item revealed as this beat starts")
    fill_previous_blank: bool = Field(default=False, description="fills the latest blank step not yet filled")
    highlight_steps: list[int] = Field(
        default_factory=list, description="1-based numbers of earlier board items to re-emphasise (max 2)"
    )
    pause_after: float = Field(default=0.0, description="seconds of silence after the beat (0-8)")
    visual_cue: str | None = Field(default=None, description="simulations: what the animation shows")
    source_refs: list[str] = Field(default_factory=list)


class GenChartDataset(GenModel):
    label: str = ""
    data: list[float] = Field(default_factory=list)


class GenGraphFunction(GenModel):
    expr: str = Field(description="math.js expression in x, e.g. 'x^2 - 2*x'")
    label: str | None = None


class GenGraphPoint(GenModel):
    x: float
    y: float
    label: str | None = None


class GenPrimitive3D(GenModel):
    shape: Literal["sphere", "box", "cylinder", "cone", "torus", "arrow"]
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    size: list[float] = Field(default_factory=lambda: [1.0])
    color: str = "#b026ff"
    label: str | None = None


class GenSidePanel(GenModel):
    """Flattened side panel: fill only the fields of the chosen ``kind``."""

    kind: GenPanelKind
    title: str | None = None
    rationale: str = ""
    show_from_beat: int | None = Field(default=None, description="1-based beat number; null = from the start")
    figure_id: str | None = None
    image_prompt: str | None = None
    chart_type: Literal["bar", "line", "pie", "doughnut", "radar"] | None = None
    chart_labels: list[str] = Field(default_factory=list)
    chart_datasets: list[GenChartDataset] = Field(default_factory=list)
    chart_x_label: str | None = None
    chart_y_label: str | None = None
    graph_functions: list[GenGraphFunction] = Field(default_factory=list)
    graph_points: list[GenGraphPoint] = Field(default_factory=list)
    graph_x_min: float | None = None
    graph_x_max: float | None = None
    graph_y_min: float | None = None
    graph_y_max: float | None = None
    graph_x_label: str | None = None
    graph_y_label: str | None = None
    model_primitives: list[GenPrimitive3D] = Field(default_factory=list)
    model_auto_rotate: bool = True
    manim_code: str | None = None
    terminal_command: str | None = None
    terminal_output: str | None = None
    quiz_question: str | None = None
    quiz_options: list[str] = Field(default_factory=list)
    quiz_correct_index: int | None = None
    gif_query: str | None = None


# ---------------------------------------------------------------------------
# Scene families
# ---------------------------------------------------------------------------


class GenSceneCommon(GenModel):
    title: str = ""
    subtitle: str | None = None
    mascot_position: GenMascotPosition | None = None
    teacher_notes: str = ""


class GenBoardScene(GenSceneCommon):
    """title / content / example / summary / key_takeaway / recap scenes."""

    beats: list[GenBeat] = Field(default_factory=list)
    side_panel: GenSidePanel | None = None


class GenChapterCard(GenSceneCommon):
    chapter_label: str | None = Field(default=None, description="short label in the board language, e.g. 'Part 2'")
    beats: list[GenBeat] = Field(default_factory=list, description="0-2 short beats")


class GenQuizDistractor(GenModel):
    text: str
    why_wrong: str = Field(description="feedback shown when a learner picks this option")
    misconception_key: str | None = None


class GenQuiz(GenSceneCommon):
    question: str
    correct: str = Field(description="the correct option text")
    distractors: list[GenQuizDistractor] = Field(default_factory=list, description="2-3 plausible wrong options")
    explanation: str = ""
    bloom: GenBloom = "understand"
    countdown_seconds: int = 8
    question_beats: list[GenBeat] = Field(default_factory=list)
    reveal_beats: list[GenBeat] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class GenSimulationBase(GenSceneCommon):
    beats: list[GenBeat] = Field(default_factory=list, description="one beat per animation step")


class GenSimulationCode(GenSimulationBase):
    """Free-form Manim (only when no template fits)."""

    code: str = Field(description="python source defining one class <Name>(AadhiScene)")


class GenAIVideo(GenSceneCommon):
    beats: list[GenBeat] = Field(default_factory=list)
    video_prompt: str
    rationale: str = ""
    fallback_image_prompt: str | None = None
    fallback_figure_id: str | None = None


class GenInteractive(GenSceneCommon):
    beats: list[GenBeat] = Field(default_factory=list)
    p5_code: str


# ---------------------------------------------------------------------------
# Critic, translation, practice
# ---------------------------------------------------------------------------


class GenFinding(GenModel):
    scene_id: str
    beat_number: int | None = None
    category: Literal["grounding", "factual", "formula", "pedagogy", "flow", "clarity", "misconception"]
    severity: Literal["error", "warning", "info"] = "warning"
    claim: str = Field(default="", description="verbatim excerpt from the scene the finding is about")
    evidence: str = Field(default="", description="verbatim quote from a source chunk, if any")
    evidence_chunk: str | None = None
    message: str
    suggestion: str = ""


class GenCritique(GenModel):
    findings: list[GenFinding] = Field(default_factory=list)
    overall: str = ""


class GenTranslatedField(GenModel):
    path: str
    text: str


class GenTranslation(GenModel):
    items: list[GenTranslatedField] = Field(default_factory=list)


class GenPracticeProblem(GenModel):
    question: str
    steps: list[str] = Field(default_factory=list)
    final_answer: str
    hints: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    objective_keys: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class GenPractice(GenModel):
    problems: list[GenPracticeProblem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dynamic models (template params)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _simulation_model(template: str, params_model: type[BaseModel]) -> type[GenModel]:
    return create_model(
        f"GenSimulation_{template}",
        __base__=GenSimulationBase,
        __doc__=f"Simulation scene using the '{template}' animation template.",
        params=(params_model, Field(description=f"parameters of the '{template}' template")),
    )


def simulation_model(template: str | None) -> type[GenModel]:
    """Response model for a simulation scene: template params when the template exists, else code."""
    tpl = integrations.get_template(template)
    if tpl is None or template is None:
        return GenSimulationCode
    return _simulation_model(template, tpl.params_model)


@lru_cache(maxsize=128)
def _panel_manim_model(template: str, params_model: type[BaseModel]) -> type[GenModel]:
    panel = create_model(
        f"GenSidePanel_{template}",
        __base__=GenSidePanel,
        manim_params=(params_model | None, Field(default=None, description=f"'{template}' template parameters")),
    )
    return create_model(
        f"GenBoardScene_{template}",
        __base__=GenBoardScene,
        side_panel=(panel | None, None),
    )


def board_scene_model(panel_template: str | None = None) -> type[GenModel]:
    """Board scene model; with a manim side-panel template its params become part of the schema."""
    tpl = integrations.get_template(panel_template)
    if tpl is None or panel_template is None:
        return GenBoardScene
    return _panel_manim_model(panel_template, tpl.params_model)


def scene_family(scene_type: str) -> str:
    """Prompt/model family for a scene type: board | chapter | quiz | simulation | ai_video | interactive."""
    if scene_type in BOARD_SCENE_TYPES:
        return "board"
    return {
        "chapter_card": "chapter",
        "quiz_checkpoint": "quiz",
        "simulation": "simulation",
        "ai_video": "ai_video",
        "interactive": "interactive",
    }[scene_type]


def model_for_scene(scene_type: str, manim_template: str | None = None) -> type[GenModel]:
    """Response model for a planned scene."""
    family = scene_family(scene_type)
    if family == "board":
        return board_scene_model(manim_template)
    if family == "simulation":
        return simulation_model(manim_template)
    return {"chapter": GenChapterCard, "quiz": GenQuiz, "ai_video": GenAIVideo, "interactive": GenInteractive}[family]


ALL_STATIC_MODELS: tuple[type[GenModel], ...] = (
    GenPlan, GenBoardScene, GenChapterCard, GenQuiz, GenSimulationCode, GenAIVideo, GenInteractive,
    GenCritique, GenTranslation, GenPractice,
)


# ---------------------------------------------------------------------------
# Concept brief (aadhi.pipeline.brief): what the lecture must teach, before planning
# ---------------------------------------------------------------------------

GenExcludedCategory = Literal["person", "duration", "timecode", "admin", "production_note", "structure", "duplicate"]


class GenBriefFact(GenModel):
    text: str = Field(description="a definition, law, formula (LaTeX), property or result, copied faithfully")
    source_refs: list[str] = Field(default_factory=list, description="source chunk ids, e.g. 'c0004'")


class GenBriefConcept(GenModel):
    key: str = Field(description="short ASCII snake_case key, unique in the brief, e.g. 'de_morgan_theorems'")
    name: str = Field(description="the concept's name as a learner would see it")
    why_it_matters: str = Field(default="", description="one sentence: why the learner needs this concept")
    must_explain: list[str] = Field(default_factory=list, description="points an explanation must cover")
    key_facts: list[GenBriefFact] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list, description="worked examples, analogies, applications from the source")
    prerequisites: list[str] = Field(default_factory=list, description="keys of concepts in this brief to learn first")
    source_refs: list[str] = Field(default_factory=list, description="source chunk ids that teach this concept")


class GenBriefExclusion(GenModel):
    category: GenExcludedCategory
    text: str = Field(description="generic description of what was ignored; never a person's name or contact detail")
    reason: str = ""


GenSkipReason = Literal["administrative", "production", "scaffolding", "duplicate", "off_topic"]


class GenBriefQuestion(GenModel):
    text: str = Field(description="the question word for word, with its answer when the source gives one")
    source_refs: list[str] = Field(default_factory=list, description="source chunk ids the question comes from")


class GenSkippedChunk(GenModel):
    chunk_id: str = Field(description="id of a source chunk that holds no teaching content, e.g. 'c0001'")
    reason: GenSkipReason


class GenBrief(GenModel):
    topic: str = Field(default="", description="the lecture's topic in a few words")
    concepts: list[GenBriefConcept] = Field(default_factory=list)
    teaching_order: list[str] = Field(default_factory=list, description="every concept key, prerequisites first")
    source_questions: list[GenBriefQuestion] = Field(default_factory=list,
                                                     description="quiz/practice questions found in the source")
    skipped_chunks: list[GenSkippedChunk] = Field(
        default_factory=list,
        description="chunks that hold no teaching content, each with a reason; every other chunk is cited")
    excluded: list[GenBriefExclusion] = Field(default_factory=list)
    notes: str = Field(default="", description="one or two sentences for the planner about the source as a whole")

    @field_validator("source_questions", mode="before")
    @classmethod
    def _plain_questions(cls, value: object) -> object:
        """A plain string is a question without chunk refs (older answers and hand-written fixtures)."""
        if isinstance(value, list):
            return [{"text": v} if isinstance(v, str) else v for v in value]
        return value
