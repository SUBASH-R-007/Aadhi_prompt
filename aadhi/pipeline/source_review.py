"""Source report: how Aadhi read the teacher's document, and the teacher's corrections before planning.

Pure functions over the stored ``IngestResult`` (no I/O, no settings, no model calls):

* ``analyze(ingest)``: the deterministic reading of the *scoped* source (``ingest.markdown`` /
  ``ingest.chunks``: metadata, administrative data and visual directions were already removed by
  ``source_scope``, so SME scaffolding never comes back as a section):

  - an **outline**: the title (the document's own title line, else a unique top heading, else none),
    sections from the top heading level and subtopics from the deeper ones, each with the chunks under
    it and a **role** from its heading words (objectives, prerequisites, summary, questions, references;
    English, Tamil and Hindi headings, other languages fall back to the English words);
  - a **content inventory**: formulas (lines, LaTeX ``$...$`` / ``$$...$$`` / ``\\(..\\)`` / ``\\[..\\]`` and
    conservative inline formulas in sentences, ``inline_formulas``: "... across it: V = I x R. Doubling ...")
    with the symbols the document never explains ("where F is ...", "force (F)", "m: mass"; superscripts
    are exponents, chemical components count only in reactions), fenced code blocks (and whether prose
    explains them), figures (``[Figure id: caption]`` markers), tables and questions;
  - **findings** in issue-code style (``source.long_paragraph`` ...), each with why it matters, a
    suggestion and where (chunk ids, heading), and an advisory **readiness** verdict. Findings never
    block generation; "no objectives / examples / summary" are not findings, because the planner writes
    objectives, quizzes and a recap anyway.

  Everything is bounded (formula symbols via one definition index per document, near-duplicates via an
  inverted shingle index with capped postings), so a 400,000-character source stays fast.

* ``build_report(ingest, ...)``: the teacher-facing ``SourceReport``: the analysis plus what the concept
  brief made of each chunk (cited / set aside with a reason / used as context), the concepts with their
  chunks, the counts of what source scoping removed (by category, never the values), the author's visual
  notes, ingest warnings and which chunks each scene cites (``source_refs``). Every text it shows that
  contains a value source scoping or the brief removed (``plan.excluded_matcher``) is masked.

* The source-review gate (``generate_lecture`` with ``review_source``): the teacher's corrections are
  ``SourceOverrides`` (chunks to set aside, chunks the brief set aside to restore, concept names), stored
  in ``generation_meta["source_overrides"]`` with the extract key they were made on. ``effective_brief``
  applies them to the concept brief (set-aside chunks become skipped chunks and stop being cited, concepts
  and key facts taught only from them are dropped, and so are the examples, ``must_explain`` points and
  ``why_it_matters`` of the remaining concepts drawn only from them; renamed concepts keep their key) and
  ``without_chunks`` takes them out of the ingest, so no stage after the review (plan, scenes, critic,
  repair, companion sheet, scene regeneration) sees them. Both are idempotent.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from ..schemas.screenplay import BoardScene, QuizScene, Screenplay, StrictModel
from .base import ConceptBrief, IngestResult, Severity, SkippedChunk, SourceChunk
from .chunking import _blocks
from .codeblocks import fence_open, fenced_segments, looks_like_code
from .ingest import INSTRUCTION_LIKE, readable_chars, source_kind

REVIEW_VERSION = "3"  # bump when the analysis changes (cached reports are keyed by extract + this)
# 2: headings read as the chunker reads them, chunks matched on their full heading path; whole-heading role words
OVERRIDES_KEY = "source_overrides"  # generation_meta: the teacher's corrections (SourceOverrides)
REVIEW_STAGE_KEY = "review_stage"  # generation_meta: "source" while paused for the source review
REVIEW_STAGE_SOURCE = "source"
RESUME_STAGE = "source_review"  # AwaitingReview state["stage"] of the source-review pause
TEACHER_SKIP_REASON = "off_topic"  # SkippedChunk reason recorded for chunks the teacher set aside

MAX_FINDINGS = 100
MAX_FORMULAS = 200
MAX_EXCLUDED = 500
MAX_CONCEPT_NAMES = 40
MESSAGE_CHARS = 300
EXCERPT_CHARS = 220
LONG_PARAGRAPH = 1000
OVERLOADED_CHARS = 8000
OVERLOADED_PARAGRAPHS = 14
DUPLICATE_MIN_WORDS = 12
DUPLICATE_JACCARD = 0.8
DUPLICATE_MAX_PARAGRAPHS = 4000
DUPLICATE_MAX_POSTINGS = 40
MAX_DEFINED_TERMS = 100
PER_CODE_CAP = {"source.long_paragraph": 10, "source.formula_undefined_symbols": 20, "source.code_unexplained": 10,
                "source.missing_figure": 10, "source.near_duplicate": 10, "source.term_before_definition": 10,
                "source.empty_section": 10, "source.overloaded_section": 10, "source.instruction_like_text": 10}

Role = Literal["teaching", "objectives", "prerequisites", "summary", "questions", "references"]
Verdict = Literal["well_structured", "partially_structured", "needs_reorganization", "missing_context", "ambiguous",
                  "incomplete"]
ChunkStatus = Literal["teaching", "context", "set_aside", "set_aside_by_you", "restored"]

# Heading words that give a section its role: English, with the common Tamil and Hindi headings.
ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "objectives": ("learning objectives", "objectives", "learning outcomes", "outcomes", "aims", "goals", "by the end of",
                   "what you will learn", "கற்றல் நோக்கங்கள்", "நோக்கங்கள்", "கற்றல் விளைவுகள்", "अधिगम उद्देश्य",
                   "उद्देश्य", "सीखने के उद्देश्य"),
    "prerequisites": ("prerequisites", "prerequisite", "prior knowledge", "before you begin", "before you start",
                      "requirements", "முன்நிபந்தனைகள்", "पूर्वापेक्षाएँ"),
    "summary": ("summary", "conclusion", "conclusions", "recap", "key takeaways", "takeaways", "in summary", "wrap up",
                "wrap-up", "சுருக்கம்", "முடிவுரை", "सारांश", "निष्कर्ष"),
    "questions": ("questions", "exercises", "review questions", "quiz", "practice", "practice questions",
                  "practice problems", "practice exercises", "practice set", "self-assessment", "self assessment",
                  "check your understanding", "assessment", "test yourself", "கேள்விகள்", "பயிற்சி", "प्रश्न",
                  "अभ्यास"),
    "references": ("references", "bibliography", "further reading", "sources", "citations"),
}
# Single words that also open ordinary topic headings ("Sources of Energy", "Goals of monetary policy", "Practice of
# medicine"): they give a role only as the whole heading ("Sources") or before a colon ("Goals: ..."). Deliberately
# stricter than the fork's ``role_of``, which read "Sources of Energy" as references.
WHOLE_HEADING_WORDS = frozenset({"outcomes", "aims", "goals", "requirements", "practice", "assessment", "sources",
                                 "conclusion"})

# Findings -> quality class; the worst open warning's class is the verdict (``readiness``).
FINDING_CLASS: dict[str, Verdict] = {
    "source.no_headings": "needs_reorganization", "source.long_paragraph": "needs_reorganization",
    "source.overloaded_section": "needs_reorganization", "source.term_before_definition": "needs_reorganization",
    "source.empty_section": "incomplete", "source.missing_figure": "incomplete", "source.truncated": "incomplete",
    "source.formula_undefined_symbols": "missing_context", "source.code_unexplained": "missing_context",
    "source.instruction_like_text": "ambiguous",
    "source.near_duplicate": "partially_structured", "source.no_title": "partially_structured",
}
PRECEDENCE: tuple[Verdict, ...] = ("needs_reorganization", "incomplete", "missing_context", "ambiguous",
                                   "partially_structured")

# What source scoping (rules) and the concept brief removed, in the teacher's words (counts only).
SCOPE_LABELS = {
    "person": "Names, designations and contact details of the authors, reviewers or faculty",
    "duration": "Video, clip and segment durations, run times, word counts",
    "timecode": "Start and end times of clips and segments",
    "admin": "Course and subject codes, dates, versions, page numbers, institution details",
    "production_note": "Recording and editing instructions",
    "structure": "Video packaging: per-clip title cards, bridges to the next part, repeated introductions",
    "duplicate": "Text repeated word for word",
}
SKIP_LABELS = {
    "administrative": "Document details: subject, unit or session lines, names, codes, dates",
    "production": "Recording, editing, camera or animation directions",
    "scaffolding": "Video packaging: title cards, bridges between clips, sign-offs",
    "duplicate": "A word-for-word repeat of another section",
    "off_topic": "Material unrelated to the lecture's subject",
}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

_CHUNK_ID = re.compile(r"^c\d{4,5}$")
_CONCEPT_KEY = re.compile(r"^[a-z0-9_]{1,64}$")


def _chunk_ids(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        v = str(v).strip()
        if not _CHUNK_ID.match(v):
            raise ValueError(f"{v[:20]!r} is not a source chunk id")
        if v not in out:
            out.append(v)
    return out


class SourceOverrides(StrictModel):
    """The teacher's corrections to how the source was read (made while the generation waits for them)."""

    ingest_key: str = Field(default="", max_length=300)  # the extract the chunk ids belong to
    excluded_chunk_ids: list[str] = Field(default_factory=list, max_length=MAX_EXCLUDED)  # set aside by the teacher
    restored_chunk_ids: list[str] = Field(default_factory=list, max_length=MAX_EXCLUDED)  # brief skips taken back
    concept_names: dict[str, str] = Field(default_factory=dict, max_length=MAX_CONCEPT_NAMES)  # key -> new name
    updated_at: str = Field(default="", max_length=40)

    _ids = field_validator("excluded_chunk_ids", "restored_chunk_ids")(_chunk_ids)

    @field_validator("concept_names")
    @classmethod
    def _names(cls, v: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, name in v.items():
            if not _CONCEPT_KEY.match(str(key)):
                raise ValueError(f"{str(key)[:20]!r} is not a concept key")
            text = " ".join(str(name).split())
            if not text or len(text) > 160:
                raise ValueError("a concept name must have 1 to 160 characters")
            out[str(key)] = text
        return out

    def is_empty(self) -> bool:
        return not (self.excluded_chunk_ids or self.restored_chunk_ids or self.concept_names)


class OutlineTopic(StrictModel):
    title: str = Field(default="", max_length=300)
    level: int = 0
    role: Role = "teaching"
    chunk_ids: list[str] = Field(default_factory=list)
    chars: int = 0
    empty: bool = False
    teaching_chunks: int = 0
    set_aside_chunks: int = 0
    visual_notes: int = 0


class OutlineSection(OutlineTopic):
    subtopics: list[OutlineTopic] = Field(default_factory=list)


class Outline(StrictModel):
    title: str = Field(default="", max_length=300)
    title_source: Literal["document", "heading", "not_provided"] = "not_provided"
    sections: list[OutlineSection] = Field(default_factory=list)


class FormulaCheck(StrictModel):
    chunk_id: str
    expression: str = Field(max_length=200)
    symbols: list[str] = Field(default_factory=list, max_length=40)
    undefined_symbols: list[str] = Field(default_factory=list, max_length=40)


class Inventory(StrictModel):
    formulas: list[FormulaCheck] = Field(default_factory=list, max_length=MAX_FORMULAS)
    formula_count: int = 0
    formulas_with_undefined_symbols: int = 0
    code_blocks: int = 0
    code_unexplained: int = 0
    figures: int = 0  # figures stored from the source
    figure_markers: int = 0  # [Figure ...] markers in the text
    tables: int = 0
    questions: int = 0  # lines that end with a question mark
    source_questions: int = 0  # questions the concept brief found (report only)


class Finding(StrictModel):
    id: str
    code: str
    severity: Severity = "warning"
    category: Verdict = "partially_structured"
    message: str = Field(max_length=MESSAGE_CHARS)
    why: str = Field(default="", max_length=MESSAGE_CHARS)
    suggestion: str = Field(default="", max_length=MESSAGE_CHARS)
    chunk_ids: list[str] = Field(default_factory=list, max_length=10)
    heading: str = Field(default="", max_length=MESSAGE_CHARS)


class Readiness(StrictModel):
    verdict: Verdict = "well_structured"
    ready: bool = True  # no open warning (advisory: never blocks generation)
    warnings: int = 0
    infos: int = 0


class ReportChunk(StrictModel):
    id: str
    heading: str = Field(default="", max_length=400)
    page: int | None = None
    chars: int = 0
    excerpt: str = Field(default="", max_length=EXCERPT_CHARS + 1)
    status: ChunkStatus = "teaching"  # with the saved corrections applied
    aadhi_status: ChunkStatus = "teaching"  # what the concept brief made of it (before any correction)
    reason: str = Field(default="", max_length=40)  # the brief's skip reason
    concepts: list[str] = Field(default_factory=list)
    visual_notes: int = 0
    formulas: int = 0
    code_blocks: int = 0
    figures: int = 0
    tables: int = 0
    questions: int = 0


class ScopeCount(StrictModel):
    category: str
    count: int
    label: str = ""
    source: Literal["rules", "brief"] = "rules"


class ConceptRow(StrictModel):
    key: str
    name: str = Field(max_length=160)
    original_name: str = Field(default="", max_length=160)
    chunk_ids: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    key_facts: int = 0
    dropped: bool = False  # every chunk it was taught from is set aside: it is not planned


class Accounting(StrictModel):
    chunks: int = 0
    cited: int = 0
    context: int = 0
    set_aside: int = 0
    set_aside_by_you: int = 0
    restored: int = 0
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)


class SceneTrace(StrictModel):
    scene_id: str
    title: str = Field(default="", max_length=300)
    type: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    unknown_refs: int = 0


class SourceReport(StrictModel):
    review_version: str = REVIEW_VERSION
    file: str = Field(default="", max_length=255)
    source_kind: str = ""
    source_format: str = "unknown"
    pages: int | None = None
    language: str | None = None
    characters: int = 0
    truncated: bool = False
    attach_original: bool = False
    outline: Outline = Field(default_factory=Outline)
    chunks: list[ReportChunk] = Field(default_factory=list)
    inventory: Inventory = Field(default_factory=Inventory)
    findings: list[Finding] = Field(default_factory=list, max_length=MAX_FINDINGS)
    findings_total: int = 0
    strengths: list[str] = Field(default_factory=list)
    readiness: Readiness = Field(default_factory=Readiness)
    scope: list[ScopeCount] = Field(default_factory=list)
    visual_notes: int = 0
    brief_available: bool = False
    concepts: list[ConceptRow] = Field(default_factory=list)
    accounting: Accounting = Field(default_factory=Accounting)
    warnings: list[str] = Field(default_factory=list)
    scenes: list[SceneTrace] = Field(default_factory=list)
    overrides: SourceOverrides | None = None


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")  # as ``chunking`` reads headings
_NUMBERING = re.compile(r"^(?:\(?(?:\d+(?:\.\d+)*|[ivx]+)[.)]\s*|\d+(?:\.\d+)*\s+)+")  # "1.2 ", "(iv) ", "IV. "
_FIGURE_MARKER = re.compile(r"\[Figure ([A-Za-z0-9\-]+)(?::\s*([^\]]*))?\]")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_WORD = re.compile(r"\w+", re.U)


def role_of(title: str) -> Role:
    """The role a heading gives its section ("Learning objectives" -> objectives); "teaching" otherwise.

    Numbering is stripped ("1.2 ", "(iv) ", "IV. "; a Roman numeral only before "." or ")", so "In summary"
    keeps its "I"); a word of ``WHOLE_HEADING_WORDS`` counts only as the whole heading or before a colon."""
    t = _NUMBERING.sub("", (title or "").strip().lower()).strip(" :.-")
    for role, words in ROLE_WORDS.items():
        if any(t == w or t.startswith(w + ":") or (w not in WHOLE_HEADING_WORDS and t.startswith(w + " "))
               for w in words):
            return role  # type: ignore[return-value]
    return "teaching"


def _prose_lines(text: str) -> list[str]:
    """``text``'s lines outside fenced code."""
    return [ln for code, lines in fenced_segments(text.split("\n")) if not code for ln in lines]


def _paragraphs(text: str) -> list[str]:
    """Prose paragraphs of a chunk: outside code, without tables and figure markers, list items kept."""
    out: list[str] = []
    for code, lines in fenced_segments(text.split("\n")):
        if code:
            continue
        cur: list[str] = []
        for line in [*lines, ""]:
            s = line.strip()
            if not s or s.startswith("|") or _FIGURE_MARKER.fullmatch(s):
                if cur:
                    out.append(" ".join(cur))
                    cur = []
                continue
            cur.append(s)
    return out


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fid(code: str, *parts: Any) -> str:
    raw = "\x00".join([code, *(str(p) for p in parts)])
    return "f" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - an id, not security


# ---------------------------------------------------------------------------
# outline
# ---------------------------------------------------------------------------


def _lead(text: str) -> str:
    """Whitespace-normalised start of a text: how a chunk is matched to the first content under a heading."""
    return " ".join(text[:400].split())[:80]


@dataclass
class _Heading:
    index: int
    level: int
    text: str
    path: tuple[str, ...] = ()  # the full heading path, as ``chunk_markdown`` records it on chunks
    lead: str | None = None  # start of the first content under it before the next heading (None: nothing)


@dataclass
class _Node:
    title: str
    level: int
    role: str
    heading: int | None  # index into the headings (None: text before the first section)
    chunk_ids: list[str] = field(default_factory=list)
    chars: int = 0
    paragraphs: int = 0
    subtopics: list[_Node] = field(default_factory=list)


def _headings(markdown: str) -> list[_Heading]:
    """Headings as the chunker reads them (``chunking._blocks``: same line splitting, fences and heading rule),
    each with its full path and the start of the first content under it."""
    out: list[_Heading] = []
    stack: list[tuple[int, str]] = []
    for block in _blocks(markdown or ""):
        if block.kind == "heading":
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.text[:200]))
            out.append(_Heading(len(out), block.level, block.text[:200], tuple(t for _, t in stack)))
        elif block.kind != "page" and out and out[-1].lead is None:
            out[-1].lead = _lead(block.text)
    return out


def _owners(headings: Sequence[_Heading], chunks: Sequence[SourceChunk]) -> dict[str, int]:
    """chunk id -> index of the heading it sits under (-1: before any heading). Chunks and headings are both in
    document order, a chunk never spans a heading and its heading path is the full path of the heading before it;
    of two headings with the same path, a chunk that starts with the first text under the later one belongs to it
    (an "Introduction" under every unit). A chunk matching no heading stays with the previous one."""
    by_path: dict[tuple[str, ...], list[int]] = {}
    for h in headings:
        if h.lead is not None:
            by_path.setdefault(h.path, []).append(h.index)
    pos = -1
    out: dict[str, int] = {}
    for c in chunks:
        path = tuple(c.heading_path)
        if path:
            same = by_path.get(path, [])
            k = bisect.bisect_right(same, pos)
            if k < len(same) and (pos < 0 or headings[pos].path != path
                                  or _lead(c.text).startswith(headings[same[k]].lead or "")):
                pos = same[k]
        out[c.id] = pos
    return out


_TITLE_LINE = re.compile(r"^\s*title\s*[:\-–—]\s*(\S.{0,298}?)\s*$", re.I)


def title_line(ingest: IngestResult) -> tuple[str, str | None]:
    """(title, id of the chunk that holds nothing but it) for a "Title: ..." line opening the document."""
    first = ingest.chunks[0] if ingest.chunks else None
    if first is None or first.heading_path:
        return "", None
    lines = [ln for ln in first.text.split("\n") if ln.strip()]
    m = _TITLE_LINE.match(lines[0]) if lines else None
    if m is None:
        return "", None
    return m.group(1), first.id if len(lines) == 1 else None


def _outline_nodes(ingest: IngestResult) -> tuple[str, str, list[_Node], dict[str, _Node]]:
    """(title, title source, sections, chunk id -> node); a chunk holding only a "Title:" line belongs to none."""
    headings = _headings(ingest.markdown)
    owners = _owners(headings, ingest.chunks)
    line_title, title_chunk = title_line(ingest)
    title_idx: int | None = None
    if headings and not any(o == -1 for cid, o in owners.items() if cid != title_chunk):
        first = headings[0]
        same = [h for h in headings if h.level == first.level]
        deeper = [h for h in headings if h.level > first.level]
        if len(same) == 1 and (deeper or len(headings) == 1):
            title_idx = 0
    meta_title = (ingest.document_meta.session_title or "").strip() or line_title
    if meta_title:
        title, source = meta_title, "document"
    elif title_idx is not None:
        title, source = headings[title_idx].text, "heading"
    else:
        title, source = "", "not_provided"
    rest = [h for h in headings if h.index != title_idx]
    section_level = min((h.level for h in rest), default=1)
    sections: list[_Node] = []
    by_heading: dict[int, _Node] = {}
    preamble: _Node | None = None
    current: _Node | None = None
    sub: _Node | None = None
    for h in rest:
        if h.level == section_level:
            current = _Node(h.text, h.level, role_of(h.text), h.index)
            sections.append(current)
            sub = None
            by_heading[h.index] = current
            continue
        if current is None:  # a deeper heading before any section heading
            current = _Node("", section_level, "teaching", None)
            sections.append(current)
        if sub is None or h.level <= sub.level:
            role = role_of(h.text)
            sub = _Node(h.text, h.level, current.role if role == "teaching" else role, h.index)
            current.subtopics.append(sub)
        by_heading[h.index] = sub  # a deeper heading stays inside its subtopic
    by_chunk: dict[str, _Node] = {}
    for c in ingest.chunks:
        if c.id == title_chunk:
            continue
        owner = owners.get(c.id, -1)
        node = by_heading.get(owner) if owner >= 0 and owner != title_idx else None
        if node is None:
            if preamble is None:
                preamble = _Node("", section_level, "teaching", None)
            node = preamble
        node.chunk_ids.append(c.id)
        node.chars += len(c.text)
        node.paragraphs += len(_paragraphs(c.text))
        by_chunk[c.id] = node
    if preamble is not None:
        sections.insert(0, preamble)
    return title, source, sections, by_chunk


# ---------------------------------------------------------------------------
# formulas
# ---------------------------------------------------------------------------

FORMULA_OPS = ("=", "→", "⇌", "->", "<->", "≈", "≤", "≥", "∝", "⟶", "≠")
REACTION = ("→", "⇌", "->", "<->", "⟶")
FUNCTIONS = {"sin", "cos", "tan", "log", "ln", "exp", "sqrt", "lim", "max", "min", "d", "dx", "dt", "e", "pi", "π",
             "mod", "and", "or"}
# Ordinary short English words a sentence-like formula carries ("divide by m to get the acceleration: a = F / m"):
# never symbols when written in lower case (a single letter can always be a symbol; 4+ letters never are).
FORMULA_WORDS = {"the", "to", "of", "in", "on", "at", "by", "is", "be", "as", "an", "for", "so", "if", "we", "it", "its",
                 "get", "put", "use", "let", "all", "are", "was", "has", "can", "per", "not", "no", "do", "you", "our",
                 "one", "two", "set", "add", "via", "out", "up", "how", "why", "new", "now", "see", "say", "any", "may",
                 "too", "but", "he", "she", "his", "her", "my", "us"}
SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ"
_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
          "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
          "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
          "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω"}
_LATEX_OPS = ("=", "\\approx", "\\leq", "\\geq", "\\le", "\\ge", "\\propto", "\\to", "\\rightarrow", "\\neq", "<", ">")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_INLINE_MATH = re.compile(r"(?<![\\$])\$([^$\n]{1,300})\$(?!\$)|\\\((.{1,300}?)\\\)")
_TOKEN = re.compile(r"[A-Za-zΑ-Ωα-ω][A-Za-z0-9_]*")
_SYM = r"[A-Za-zΑ-Ωα-ω][A-Za-z0-9_']{0,11}"  # long enough for chemical formulas (C6H12O6)
_SYMBOL_DEFINITIONS = (  # where the document says what a symbol stands for
    re.compile(rf"(?<![\w])\(\s*({_SYM})\s*\)"),  # "force (F)", "carbon dioxide (CO2)"
    re.compile(rf"\bwhere\s+({_SYM})(?![\w])"),  # "where F is ..."
    re.compile(rf"(?<![\w])({_SYM})(?![\w])\s*(?:is|are|denotes|represents|stands\s+for|means)\b"),
    re.compile(rf"(?<![\w])({_SYM})(?![\w])\s*[:=]\s*(?:the\s+)?[a-z]{{3,}}"),  # "m: mass", "v = velocity"
    re.compile(rf"(?<![\w])({_SYM})(?![\w])\s*[–—-]\s*[a-z]{{3,}}"),  # "R - resistance"
    re.compile(rf"(?:\band|,)\s+({_SYM})(?![\w])\s+(?:is|are|the)\b"),  # "..., m the mass and c the speed of light"
)


def looks_like_formula(text: str) -> bool:
    """A short single line with an equation or reaction operator that is not a sentence."""
    t = _LIST_MARK.sub("", text.strip())
    if not t or len(t) > 200 or "\n" in t or t.startswith("|") or _FIGURE_MARKER.search(t):
        return False
    if not any(op in t for op in FORMULA_OPS):
        return False
    lower_words = [w for w in re.findall(r"[A-Za-z]{4,}", t) if w.islower()]
    if len(lower_words) > 3 or len(t.split()) > 16:
        return False
    return bool(re.search(r"[A-Za-z0-9₀-₉]", t))


def _plain(text: str) -> str:
    """Subscripts as plain characters (CO₂ = CO2) for matching; superscripts are exponents and dropped."""
    return unicodedata.normalize("NFKC", re.sub(f"[{SUPERSCRIPTS}]", " ", text))


def latex_plain(expr: str) -> str:
    """A LaTeX expression as plain symbols: text and units dropped, Greek letters as letters, exponents dropped."""
    s = re.sub(r"\\(?:text|mathrm|operatorname|mbox|textrm|unit)\s*\{[^{}]*\}", " ", expr)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: f" {_GREEK[m.group(1)]}" if m.group(1) in _GREEK else " ", s)
    s = re.sub(r"\^\s*(?:\{[^{}]*\}|\S)", " ", s)
    s = re.sub(r"_\s*\{\s*(\w+)\s*\}", r"_\1", s)
    return re.sub(r"[{}]", " ", s)


def formula_parts(formula: str) -> tuple[list[str], list[str]]:
    """(variables, chemical components) of a formula, from its symbols. Superscripts are exponents (v² is v);
    only a reaction (→) has chemical components."""
    plain = _plain(formula)
    tokens = _TOKEN.findall(plain)
    reaction = any(op in formula for op in REACTION)
    chemical = [t for t in tokens
                if reaction and re.fullmatch(r"(?:[A-Z][a-z]?\d*){2,}|[A-Z][a-z]?\d+|[A-Z][a-z]?", t)]
    variables: list[str] = []
    for t in tokens:
        if t in chemical or t.lower() in FUNCTIONS or len(t) > 3 or t in FORMULA_WORDS:
            continue
        if t not in variables:
            variables.append(t)
    return variables, list(dict.fromkeys(chemical))


def symbol_index(texts: Iterable[str]) -> set[str]:
    """Every symbol the document explains somewhere ("where F is", "(F)", "m: mass" ...): one pass per text."""
    out: set[str] = set()
    for text in texts:
        plain = _plain(text.replace("$", " ").replace("\\(", " ").replace("\\)", " "))
        for pattern in _SYMBOL_DEFINITIONS:
            out.update(m.group(1) for m in pattern.finditer(plain))
    return out


# Inline formulas inside a sentence ("the voltage across it: V = I x R. Doubling ..."). Deliberately conservative:
# operands are short symbols (at most 3 characters, or with a subscript) and numbers, the expression must contain an
# arithmetic operator or an exponent, and must name at least one symbol, so "a = b", "x = 5", "1 + 1 = 2" and
# "Total = 5 + 3" are never formulas. Inline code, URLs, headings, tables and code-like lines are skipped.
# Implicit products (F = ma, E = mc², A = πr²) and quantities with units (1 km/h = 5/18 m/s) are not counted.
_IN_NUM = r"\d+(?:[.,]\d+)?"
# a number written on its own; the lookbehind accepts exactly the spans the old "NUM NUM" pair did ("1,234,567")
# without its ambiguous splits, which backtracked exponentially when no relation followed
_IN_NUMBER = r"\d+(?:[.,]\d+(?:(?<=\d\d)[.,]\d+)?)?"
_IN_SYMBOL = r"[A-Za-zΑ-Ωα-ω](?:_\{?\w{1,4}\}?|[A-Za-z0-9₀-₉]{0,2})"
_IN_POWER = r"(?:[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁻]+|\^\s*(?:\d+|[A-Za-z]|\{[^{}\n]{1,10}\}))?"
_IN_OPERAND = rf"(?:(?:{_IN_NUM})?{_IN_SYMBOL}|{_IN_NUMBER}){_IN_POWER}"  # a coefficient only when written on: "2πr"
_IN_ARITH = r"(?:\s*[+*/×÷·−]\s*|\s+[x\-]\s+)"
_IN_REL = r"\s*(?:(?<![=!<>])=(?!=)|≈|∝|≠|≤|≥)\s*"
_IN_EXPR = rf"{_IN_OPERAND}(?:{_IN_ARITH}{_IN_OPERAND}){{0,10}}"
_INLINE_FORMULA = re.compile(rf"(?<![\w.$\\])({_IN_EXPR}(?:{_IN_REL}{_IN_EXPR}){{1,4}})(?![\w(])")
_IN_SIGN = re.compile(r"[+*/×÷·−^⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]|\s[x\-]\s")
_IN_RELATION = re.compile(r"[=≈∝≠≤≥]")
_IN_TIMES = re.compile(r"(?<=\S)\s+x\s+(?=\S)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_IN_IMPLICIT = re.compile(r"[a-zα-ω][A-Za-zΑ-Ωα-ω]")  # "mc", "πr", "km", "kWh": an implicit product or a unit
_IN_AFTER_NUMBER = re.compile(r"\d\s+$")  # "1 km/h = 5/18 m/s": the unit of a quantity, not a formula
INLINE_SCAN_CHARS = 5000  # per line: longer lines are scanned up to here
MAX_INLINE_PER_LINE = 20


def inline_formulas(line: str) -> list[tuple[str, str]]:
    """(expression as written, plain symbols text) of the formulas written inside a sentence (see above). The
    plain text reads a spaced ``x`` between operands as a multiplication sign, not as a symbol."""
    s = line.strip()
    if not s or s.startswith(("#", "|")) or not _IN_RELATION.search(s) or looks_like_code(s):
        return []
    s = _URL.sub(" ", _INLINE_CODE.sub(" ", s[:INLINE_SCAN_CHARS]))
    out: list[tuple[str, str]] = []
    for m in _INLINE_FORMULA.finditer(s):
        expr = " ".join(m.group(1).split())
        if not _IN_SIGN.search(expr) or _IN_AFTER_NUMBER.search(s, max(0, m.start() - 8), m.start()):
            continue
        plain = _IN_TIMES.sub(" × ", expr)
        tokens = _TOKEN.findall(_plain(plain))
        if not tokens or any(t.islower() and len(t) > 1 and (t in FORMULA_WORDS or t in ("and", "or")) for t in tokens):
            continue
        if any(_IN_IMPLICIT.match(t) and t.lower() not in FUNCTIONS for t in tokens):
            continue  # implicit products are not counted, with or without an exponent
        out.append((expr, plain))
        if len(out) >= MAX_INLINE_PER_LINE:
            break
    return out


def _formulas_in(text: str) -> list[tuple[str, str]]:
    """(expression as written, plain symbols text) of the formulas in a chunk's prose."""
    out: list[tuple[str, str]] = []
    prose = "\n".join(_prose_lines(text))

    def display(m: re.Match[str]) -> str:
        expr = (m.group(1) or m.group(2) or "").strip()
        if any(op in expr for op in _LATEX_OPS):
            out.append((" ".join(expr.split()), latex_plain(expr)))
        return "\n"

    prose = _DISPLAY_MATH.sub(display, prose)
    for line in prose.split("\n"):
        inline = list(_INLINE_MATH.finditer(line))
        for m in inline:
            expr = (m.group(1) or m.group(2) or "").strip()
            if any(op in expr for op in _LATEX_OPS):
                out.append((expr, latex_plain(expr)))
        if inline:
            continue
        s = line.strip()
        if s and not s.startswith("#") and looks_like_formula(s):
            expr = _LIST_MARK.sub("", s)
            out.append((expr, expr))
        else:
            out.extend(inline_formulas(s))
    return out


# ---------------------------------------------------------------------------
# definitions (English wording only)
# ---------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?।])\s+")
_PRONOUNS = {"it", "this", "that", "these", "those", "they", "there", "he", "she", "we", "you", "i", "here", "which",
             "what", "each", "one", "where", "when", "if", "so", "then", "thus", "hence", "but", "and", "or", "because",
             "while", "such", "its", "their", "our"}
_DEF_PATTERNS = (
    re.compile(r"^(?:definition\s*[:\-–—]\s*)?(?P<term>[\w][\w\s\-’'()/]{0,60}?)\s+(?:is|are)\s+"
               r"(?:defined\s+as|called|known\s+as|termed)\s+\S", re.I),
    re.compile(r"^(?P<term>[\w][\w\s\-’'()/]{0,60}?)\s+(?:refers\s+to|means|denotes)\s+\S", re.I),
    re.compile(r"^(?P<term>[A-Z][\w\-’']*(?:\s+[\w\-’']+){0,2})\s+(?:is|are)\s+(?:a|an)\s+\S"),
    re.compile(r"^(?P<term>[\w][\w\s\-’'()/]{0,50}?)\s+(?:is|are)\s+(?:a|an|the)\s+(?:\w+\s+){0,2}(?:process|method|"
               r"technique|type|kind|form|measure|unit|property|quantity|set|collection|device|component|structure|"
               r"algorithm|function|study|branch|state|ability|rate|ratio|amount|force|law|principle|theory|concept|"
               r"part|way|substance|organelle|molecule|pigment)\b", re.I),
)


def defined_term(sentence: str) -> str | None:
    """The term a sentence defines ("Chlorophyll is a green pigment ..." -> "Chlorophyll"), else None."""
    for pattern in _DEF_PATTERNS:
        m = pattern.match(sentence.strip())
        if m:
            term = re.sub(r"^(?:an?|the)\s+", "", m.group("term").strip(" -–—:"), flags=re.I)
            words = term.split()
            if 1 <= len(words) <= 6 and words[0].lower() not in _PRONOUNS:
                return term
    return None


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


@dataclass
class _ChunkStats:
    formulas: int = 0
    code_blocks: int = 0
    figures: int = 0
    tables: int = 0
    questions: int = 0


@dataclass
class Analysis:
    """The deterministic reading of one extract (cacheable per extract key + ``REVIEW_VERSION``)."""

    title: str
    title_source: str
    sections: list[_Node]
    node_of: dict[str, _Node]
    stats: dict[str, _ChunkStats]
    inventory: Inventory
    findings: list[Finding]
    strengths: list[str]


_FIG_REF = re.compile(r"\b(fig(?:ure)?\.?|table)\s*(\d+(?:\.\d+)?)\b", re.I)
_CAPTION = re.compile(r"^(?P<kind>fig(?:ure)?\.?|table|chart|diagram|graph|image|illustration)\s*(?P<num>\d+(?:\.\d+)?)?"
                      r"\s*[:.\-–—]\s*", re.I)


def _explains(paragraph: str | None) -> bool:
    if not paragraph:
        return False
    s = paragraph.strip()
    return len(s.split()) >= 8 and not s.startswith(("#", "|")) and not looks_like_code(s)


def _edge_paragraph(lines: Sequence[str], last: bool) -> str | None:
    paras = [p for p in "\n".join(lines).split("\n\n") if p.strip()]
    if not paras:
        return None
    return paras[-1] if last else paras[0]


def _code_blocks(chunks: Sequence[SourceChunk]) -> list[tuple[str, bool]]:
    """(chunk id, explained) per fenced code block; a block the chunker split over several chunks counts once
    (in the chunk where it starts)."""
    segs = [fenced_segments(c.text.split("\n")) for c in chunks]
    out: list[tuple[str, bool]] = []
    for i, c in enumerate(chunks):
        parts = segs[i]
        for k, (code, lines) in enumerate(parts):
            if not code:
                continue
            if k == 0 and i > 0 and segs[i - 1] and segs[i - 1][-1][0] \
                    and chunks[i - 1].heading_path == c.heading_path \
                    and fence_open(segs[i - 1][-1][1][0]) == fence_open(lines[0]):
                continue  # the continuation of a block split by the chunker
            before = parts[k - 1][1] if k > 0 else (
                segs[i - 1][-1][1] if i > 0 and segs[i - 1] and not segs[i - 1][-1][0]
                and chunks[i - 1].heading_path == c.heading_path else [])
            after = parts[k + 1][1] if k + 1 < len(parts) else (
                segs[i + 1][0][1] if i + 1 < len(chunks) and segs[i + 1] and not segs[i + 1][0][0]
                and chunks[i + 1].heading_path == c.heading_path else [])
            explained = _explains(_edge_paragraph(before, last=True)) or _explains(_edge_paragraph(after, last=False))
            out.append((c.id, explained))
    return out


def _near_duplicates(paragraphs: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """(earlier chunk, later chunk) pairs of paragraphs that say nearly the same thing (3-word shingles, Jaccard
    >= 0.8). Bounded: at most ``DUPLICATE_MAX_PARAGRAPHS`` paragraphs and ``DUPLICATE_MAX_POSTINGS`` paragraphs per
    shingle (a shingle in more paragraphs than that is too common to tell anything)."""
    shingles: list[tuple[str, set[str]]] = []
    for cid, text in paragraphs:
        words = _words(text)
        if len(words) < DUPLICATE_MIN_WORDS:
            continue
        shingles.append((cid, {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}))
        if len(shingles) >= DUPLICATE_MAX_PARAGRAPHS:
            break
    postings: dict[str, list[int]] = {}
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, (cid, sh) in enumerate(shingles):
        shared: Counter[int] = Counter()
        for s in sh:
            for j in postings.get(s, ()):
                shared[j] += 1
        for j, n in shared.most_common(5):
            other = shingles[j][1]
            if n / max(1, len(sh | other)) >= DUPLICATE_JACCARD:
                key = (shingles[j][0], cid)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
                break
        for s in sh:
            lst = postings.setdefault(s, [])
            if len(lst) < DUPLICATE_MAX_POSTINGS:
                lst.append(i)
    return pairs


class _Findings:
    def __init__(self) -> None:
        self.items: list[Finding] = []
        self.counts: Counter[str] = Counter()

    def add(self, code: str, severity: Severity, message: str, why: str, suggestion: str, *,
            chunk_ids: Sequence[str] = (), heading: str = "", key: Any = "") -> None:
        self.counts[code] += 1
        if self.counts[code] > PER_CODE_CAP.get(code, 10):
            return
        self.items.append(Finding(
            id=_fid(code, ",".join(chunk_ids), key), code=code, severity=severity,
            category=FINDING_CLASS.get(code, "partially_structured"), message=_short(message, MESSAGE_CHARS),
            why=_short(why, MESSAGE_CHARS), suggestion=_short(suggestion, MESSAGE_CHARS),
            chunk_ids=list(chunk_ids)[:10], heading=_short(heading, MESSAGE_CHARS),
        ))

    def overflow(self) -> None:
        """One summary line per finding type that was capped."""
        for code, n in self.counts.items():
            cap = PER_CODE_CAP.get(code, 10)
            if n > cap:
                self.items.append(Finding(
                    id=_fid(code, "more"), code=code, severity="info",
                    category=FINDING_CLASS.get(code, "partially_structured"),
                    message=f"{n - cap} more like this in the document.", why="", suggestion=""))


def _node_title(node: _Node | None) -> str:
    return node.title if node is not None else ""


def analyze(ingest: IngestResult) -> Analysis:
    """The deterministic reading of a scoped source (see the module docstring)."""
    chunks = list(ingest.chunks)
    title, title_source, sections, node_of = _outline_nodes(ingest)
    stats = {c.id: _ChunkStats() for c in chunks}
    found = _Findings()
    strengths: list[str] = []
    english = (ingest.detected_language or "").startswith("en")
    roles = {c.id: (node_of[c.id].role if c.id in node_of else "title") for c in chunks}

    # formulas: one symbol index for the whole document
    defined = symbol_index(p for c in chunks for p in _paragraphs(c.text))
    formulas: list[FormulaCheck] = []
    formula_total = with_undefined = 0
    for c in chunks:
        seen_expr: set[str] = set()
        for expr, plain in _formulas_in(c.text):
            if expr in seen_expr:
                continue
            seen_expr.add(expr)
            variables, comps = formula_parts(plain)
            undefined = [s for s in [*variables, *comps] if s not in defined]
            stats[c.id].formulas += 1
            formula_total += 1
            if undefined:
                with_undefined += 1
                missing = ", ".join(undefined[:8])
                found.add("source.formula_undefined_symbols", "warning", f"Formula symbols are not explained: {missing}",
                          "The lecture can only explain a formula correctly when the source says what every symbol "
                          "stands for.", f"Say what {missing} stand{'s' if len(undefined) == 1 else ''} for (with units "
                          "where relevant) next to the formula.", chunk_ids=[c.id], heading=_node_title(node_of.get(c.id)),
                          key=expr)
            if len(formulas) < MAX_FORMULAS:
                formulas.append(FormulaCheck(chunk_id=c.id, expression=_short(expr, 200), symbols=[*variables, *comps][:40],
                                             undefined_symbols=undefined[:40]))
    if formula_total and not with_undefined:
        strengths.append("Every formula's symbols are explained.")

    # code blocks
    code = _code_blocks(chunks)
    for n, (cid, explained) in enumerate(code):
        stats[cid].code_blocks += 1
        if not explained:
            found.add("source.code_unexplained", "warning", "Code without an explanation",
                      "Learners need to know what the code does and why before or after reading it.",
                      "Add a sentence before the code saying what it does, and one after it saying what it outputs.",
                      chunk_ids=[cid], heading=_node_title(node_of.get(cid)), key=n)

    # figures, tables, questions, figure references
    known_figures: set[tuple[bool, str]] = set()
    for f in ingest.figures:
        m = _CAPTION.match(f.caption or "")
        if m and m.group("num"):
            known_figures.add((m.group("kind").lower().startswith("table"), m.group("num")))
    known_figures |= {(False, str(i)) for i in range(1, len(ingest.figures) + 1)}
    mentions: list[tuple[str, bool, str]] = []
    n_tables = 0
    for c in chunks:
        in_table = False
        for line in _prose_lines(c.text):
            s = line.strip()
            for m in _FIGURE_MARKER.finditer(s):
                stats[c.id].figures += 1
                cap = _CAPTION.match(m.group(2) or "")
                if cap and cap.group("num"):
                    known_figures.add((cap.group("kind").lower().startswith("table"), cap.group("num")))
            if s.startswith("|"):
                if not in_table:
                    stats[c.id].tables += 1
                    n_tables += 1
                in_table = True
                continue
            in_table = False
            body = _FIGURE_MARKER.sub(" ", s)
            cap = _CAPTION.match(body)
            if cap and cap.group("num") and len(body) <= 300:  # a caption line names its own figure
                known_figures.add((cap.group("kind").lower().startswith("table"), cap.group("num")))
                continue
            if _LIST_MARK.sub("", body).rstrip().endswith("?"):
                stats[c.id].questions += 1
            for m in _FIG_REF.finditer(body):
                mentions.append((c.id, m.group(1).lower().startswith("table"), m.group(2)))
    reported: set[tuple[bool, str]] = set()
    for cid, is_table, num in mentions:
        ref = (is_table, num)
        if ref in known_figures or ref in reported:
            continue
        if is_table and n_tables >= float(num):
            continue
        reported.add(ref)
        what = "table" if is_table else "figure"
        found.add("source.missing_figure", "warning", f"The text refers to {what} {num}, which is not in the document",
                  "The text points at something the learner (and the lecture) cannot see.",
                  f"Include {what} {num} with its caption, or describe it in the text.", chunk_ids=[cid],
                  heading=_node_title(node_of.get(cid)), key=f"{what}{num}")

    # paragraphs: long ones, near-duplicates, terms used before their definition
    paragraphs = [(c.id, p) for c in chunks for p in _paragraphs(c.text)]
    for cid, p in paragraphs:
        if len(p) > LONG_PARAGRAPH and not looks_like_code(p):
            found.add("source.long_paragraph", "warning" if len(p) > LONG_PARAGRAPH * 1.4 else "info",
                      f"Very long paragraph ({len(p):,} characters)",
                      "A long unbroken paragraph usually mixes several ideas; a lecture scene works best with one idea "
                      "at a time.", "Split it into shorter paragraphs, one idea each, with sub-headings if the ideas "
                      "differ.", chunk_ids=[cid], heading=_node_title(node_of.get(cid)), key=p[:80])
    for first, second in _near_duplicates([(cid, p) for cid, p in paragraphs if roles.get(cid) == "teaching"]):
        found.add("source.near_duplicate", "info", "Repeated content",
                  "Nearly the same explanation appears twice; the lecture would repeat itself.",
                  "Keep one version, or refer back to the first one.", chunk_ids=list(dict.fromkeys([first, second])),
                  heading=_node_title(node_of.get(second)), key=f"{first}-{second}")
    if english:
        _terms_before_definition(found, paragraphs, roles, node_of)

    # structure
    headings = [s for s in sections if s.heading is not None]
    if title_source == "not_provided":
        found.add("source.no_title", "info", "No title found",
                  "Aadhi names the lecture from its content unless a session title is given.",
                  "Add a title line at the top, or type the session title when you create the lecture.")
    else:
        strengths.append("Clear title.")
    paragraph_count = len(paragraphs)
    if not headings and not any(s.subtopics for s in sections) and (
            readable_chars(ingest.markdown) > 1200 or paragraph_count >= 5):
        found.add("source.no_headings", "warning", "No headings",
                  "Without headings the document's topics cannot be told apart and turned into separate scenes.",
                  "Add a heading for each main topic and a sub-heading for each concept.",
                  chunk_ids=[c.id for c in chunks[:1]])
    elif headings:
        teaching = sum(1 for s in headings if s.role == "teaching")
        strengths.append(f"{teaching} teaching section{'s' if teaching != 1 else ''} with headings.")
    sme = ingest.source_format == "sme_script"
    section_ids = {id(s) for s in sections}
    for node in [n for s in sections for n in (s, *s.subtopics)]:
        if node.heading is None:
            continue
        has_sub = id(node) in section_ids and any(t.chunk_ids for t in node.subtopics)
        if not node.chunk_ids and not has_sub and node.role != "references":
            found.add("source.empty_section", "info" if sme or ingest.excluded else "warning",
                      f"Nothing to teach under “{node.title}”",
                      "A heading with nothing under it (or only details that were set aside as administrative) leaves "
                      "a gap in the lecture.", "Add the missing content or remove the heading.", heading=node.title,
                      key=node.heading)
        if node.role == "teaching" and not node.subtopics and (
                node.chars > OVERLOADED_CHARS or node.paragraphs > OVERLOADED_PARAGRAPHS):
            found.add("source.overloaded_section", "warning", f"“{node.title}” covers a lot without sub-headings",
                      "Without sub-headings the concepts cannot be told apart and turned into separate scenes.",
                      "Divide it into sub-sections, one concept each.", chunk_ids=node.chunk_ids[:3], heading=node.title,
                      key=node.heading)

    # text addressed to an AI (never quoted)
    for c in chunks:
        n = sum(1 for line in _prose_lines(c.text) if INSTRUCTION_LIKE.search(line))
        if n:
            found.add("source.instruction_like_text", "warning", "Text that reads like an instruction to an AI",
                      "It is treated as ordinary source text and never followed, but it could end up in the lecture.",
                      "Remove it unless it really belongs to the lesson.", chunk_ids=[c.id],
                      heading=_node_title(node_of.get(c.id)), key=n)
    if ingest.truncated:
        found.add("source.truncated", "warning", "Only the first part of the document was used",
                  "The document is longer than the limit, so the end of it is not in the lecture.",
                  "Split the document into several lectures, or remove what does not need teaching.")

    found.overflow()
    order = {"error": 0, "warning": 1, "info": 2}
    chunk_pos = {c.id: i for i, c in enumerate(chunks)}
    found.items.sort(key=lambda f: (order.get(f.severity, 3),
                                    min((chunk_pos.get(x, 10 ** 6) for x in f.chunk_ids), default=-1)))
    inventory = Inventory(
        formulas=formulas, formula_count=formula_total, formulas_with_undefined_symbols=with_undefined,
        code_blocks=len(code), code_unexplained=sum(1 for _, ok in code if not ok), figures=len(ingest.figures),
        figure_markers=sum(s.figures for s in stats.values()), tables=n_tables,
        questions=sum(s.questions for s in stats.values()),
    )
    if code and all(ok for _, ok in code):
        strengths.append("Every code example is explained.")
    return Analysis(title, title_source, sections, node_of, stats, inventory, found.items, strengths)


def _terms_before_definition(found: _Findings, paragraphs: Sequence[tuple[str, str]], roles: Mapping[str, str],
                             node_of: Mapping[str, _Node]) -> None:
    """A term used in an earlier teaching paragraph than the one that defines it (English wording only)."""
    defs: list[tuple[int, str, str]] = []  # (paragraph index, term, chunk id)
    seen: set[str] = set()
    for i, (cid, p) in enumerate(paragraphs):
        if roles.get(cid) != "teaching":
            continue
        for s in _SENTENCE.split(p):
            term = defined_term(s)
            if term and term.lower() not in seen:
                seen.add(term.lower())
                defs.append((i, term, cid))
        if len(defs) >= MAX_DEFINED_TERMS:
            break
    if not defs:
        return
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for _, t, _ in sorted(defs, key=lambda d: -len(d[1]))) + r")\b",
                         re.I)
    first_use: dict[str, tuple[int, str]] = {}
    for i, (cid, p) in enumerate(paragraphs):
        if roles.get(cid) != "teaching":
            continue
        for m in pattern.finditer(p):
            first_use.setdefault(m.group(1).lower(), (i, cid))
    for i, term, cid in defs:
        use = first_use.get(term.lower())
        if use is not None and use[0] < i:
            found.add("source.term_before_definition", "warning", f"“{term}” is used before it is defined",
                      "A beginner meets the term before knowing what it means.",
                      f"Move the definition of “{term}” before its first use, or add a short definition there.",
                      chunk_ids=list(dict.fromkeys([use[1], cid])), heading=_node_title(node_of.get(use[1])),
                      key=term.lower())


def readiness(findings: Sequence[Finding], done: Iterable[str] = ()) -> Readiness:
    """The advisory verdict from the findings still open (``done``: ids the teacher marked as done)."""
    closed = set(done)
    open_ = [f for f in findings if f.id not in closed]
    warnings = [f for f in open_ if f.severity in ("warning", "error")]
    verdict: Verdict = "well_structured"
    if warnings:
        verdict = min((f.category for f in warnings),
                      key=lambda c: PRECEDENCE.index(c) if c in PRECEDENCE else len(PRECEDENCE))
    elif open_:
        verdict = "partially_structured"
    return Readiness(verdict=verdict, ready=not warnings, warnings=len(warnings), infos=len(open_) - len(warnings))


# ---------------------------------------------------------------------------
# the teacher's corrections
# ---------------------------------------------------------------------------


def load_overrides(meta: Mapping[str, Any] | None) -> SourceOverrides | None:
    """The stored corrections (None when absent or invalid)."""
    raw = (meta or {}).get(OVERRIDES_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return SourceOverrides.model_validate(raw)
    except ValidationError:
        return None


def overrides_for(meta: Mapping[str, Any] | None, ingest_key: Any) -> SourceOverrides | None:
    """The stored corrections when they were made on the extract ``ingest_key`` (chunk ids are only meaningful
    for it); None otherwise."""
    ov = load_overrides(meta)
    if ov is None or ov.is_empty() or not ingest_key or ov.ingest_key != ingest_key:
        return None
    return ov


def override_problems(ov: SourceOverrides, ingest: IngestResult, brief: ConceptBrief | None) -> list[dict[str, Any]]:
    """Pydantic-style error entries (``loc`` under ``body``) for corrections that do not fit this source."""
    known = {c.id for c in ingest.chunks}
    problems: list[dict[str, Any]] = []
    unknown = [c for c in [*ov.excluded_chunk_ids, *ov.restored_chunk_ids] if c not in known]
    if unknown:
        problems.append({"loc": ["body", "excluded_chunk_ids"], "type": "value_error",
                         "msg": f"Unknown source chunk ids: {', '.join(unknown[:8])}."})
    skipped = brief.skipped_chunk_ids() if brief is not None else set()
    aside = set(ov.excluded_chunk_ids) | (skipped - set(ov.restored_chunk_ids))
    if known and known <= aside:
        problems.append({"loc": ["body", "excluded_chunk_ids"], "type": "value_error",
                         "msg": "Keep at least one part of the document to teach."})
    not_skipped = [c for c in ov.restored_chunk_ids if c in known and c not in skipped]
    if not_skipped:
        problems.append({"loc": ["body", "restored_chunk_ids"], "type": "value_error",
                         "msg": f"Only parts Aadhi set aside can be restored: {', '.join(not_skipped[:8])}."})
    keys = {c.key for c in brief.concepts} if brief is not None else set()
    bad = [k for k in ov.concept_names if k not in keys]
    if bad:
        problems.append({"loc": ["body", "concept_names"], "type": "value_error",
                         "msg": f"Unknown concepts: {', '.join(bad[:8])}."})
    return problems


def _plain_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").casefold()).split())


_CONTENT_WORD = re.compile(r"\w{3,}")


def _content_words(text: str) -> set[str]:
    return set(_CONTENT_WORD.findall((text or "").casefold()))


def _only_in(gone: str, kept: str) -> Callable[[str], bool]:
    """Predicate: does a brief text come only from the set-aside chunks (``gone``, plain text) and not from the
    kept ones? Its plain text is in them and not in the kept ones, or most of the words it shares with the source
    occur only in them (at least two)."""
    gone_words, kept_words = _content_words(gone), _content_words(kept)

    def test(item: str) -> bool:
        plain = _plain_text(item)
        if plain and plain in gone and plain not in kept:
            return True
        grounded = [w for w in _content_words(item) if w in gone_words or w in kept_words]
        aside = [w for w in grounded if w not in kept_words]
        return len(aside) >= 2 and 2 * len(aside) > len(grounded)

    return test


def effective_brief(brief: ConceptBrief, ingest: IngestResult, ov: SourceOverrides | None) -> ConceptBrief:
    """``brief`` with the teacher's corrections (idempotent): chunks set aside become skipped chunks and are no
    longer cited; key facts and concepts taught only from them are dropped (and so are prerequisites on dropped
    concepts); a remaining concept that cited a set-aside chunk loses the examples, must_explain points and
    why_it_matters drawn only from set-aside chunks; source questions found only in them are dropped; restored
    chunks are no longer skipped; renamed concepts keep their key.

    ``skipped_chunks`` holds the brief's own skips (at most 500, ``brief.skipped_from_gen``) plus the teacher's
    set-asides (at most ``MAX_EXCLUDED``), never cut: a cut would send some of either back to the models."""
    if ov is None or ov.is_empty():
        return brief
    order = {c.id: i for i, c in enumerate(ingest.chunks)}
    excluded = {c for c in ov.excluded_chunk_ids if c in order}
    restored = {c for c in ov.restored_chunk_ids if c in order} - excluded

    def keep(refs: Sequence[str]) -> list[str]:
        return [r for r in refs if r not in excluded]

    texts: tuple[str, str] | None = None  # (set-aside, kept) plain texts, built when first needed

    def plain_texts() -> tuple[str, str]:
        nonlocal texts
        if texts is None:
            texts = ("\n".join(_plain_text(c.text) for c in ingest.chunks if c.id in excluded),
                     "\n".join(_plain_text(c.text) for c in ingest.chunks if c.id not in excluded))
        return texts

    aside: Callable[[str], bool] | None = None
    concepts = []
    dropped: set[str] = set()
    for c in brief.concepts:
        facts = [f.model_copy(update={"source_refs": keep(f.source_refs)}) for f in c.key_facts
                 if not f.source_refs or keep(f.source_refs)]
        own = keep(c.source_refs)
        had_refs = bool(c.source_refs) or any(f.source_refs for f in c.key_facts)
        if had_refs and not own and not any(f.source_refs for f in facts):
            dropped.add(c.key)
            continue
        update: dict[str, Any] = {"source_refs": own, "key_facts": facts, "name": ov.concept_names.get(c.key, c.name)}
        if not excluded.isdisjoint([*c.source_refs, *(r for f in c.key_facts for r in f.source_refs)]):
            # taught from a kept part too: its prose drawn only from the set-aside parts must not reach the planner
            if aside is None:
                aside = _only_in(*plain_texts())
            update |= {"examples": [x for x in c.examples if not aside(x)],
                       "must_explain": [x for x in c.must_explain if not aside(x)],
                       "why_it_matters": "" if aside(c.why_it_matters) else c.why_it_matters}
        concepts.append(c.model_copy(update=update))
    concepts = [c.model_copy(update={"prerequisites": [p for p in c.prerequisites if p not in dropped]}) for c in concepts]
    questions = list(brief.source_questions)
    q_refs = keep(brief.source_question_refs)
    if excluded & set(brief.source_question_refs):
        if not q_refs:
            questions = []
        else:
            gone, kept = plain_texts()  # one chunk per line: a question (one line) never matches across two
            questions = [q for q in questions if not (_plain_text(q) in gone and _plain_text(q) not in kept)]
    skipped = [s for s in brief.skipped_chunks if s.chunk_id not in restored and s.chunk_id not in excluded]
    skipped += [SkippedChunk(chunk_id=cid, reason=TEACHER_SKIP_REASON) for cid in sorted(excluded, key=order.__getitem__)]
    skipped.sort(key=lambda s: order.get(s.chunk_id, len(order)))
    return brief.model_copy(update={
        "concepts": concepts,
        "teaching_order": [k for k in brief.teaching_order if k not in dropped],
        "source_questions": questions,
        "source_question_refs": q_refs,
        "skipped_chunks": skipped,  # <= 500 of the brief's own + <= MAX_EXCLUDED (ConceptBrief allows 1000)
    })


def without_chunks(ingest: IngestResult, chunk_ids: Iterable[str]) -> IngestResult:
    """``ingest`` without the given chunks (and the visual notes and figures that only they hold); idempotent."""
    drop = set(chunk_ids)
    if not drop or not any(c.id in drop for c in ingest.chunks):
        return ingest
    kept = [c for c in ingest.chunks if c.id not in drop]
    gone_figures = {m.group(1) for c in ingest.chunks if c.id in drop for m in _FIGURE_MARKER.finditer(c.text)}
    kept_figures = {m.group(1) for c in kept for m in _FIGURE_MARKER.finditer(c.text)}
    figures = [f for f in ingest.figures if f.id not in gone_figures or f.id in kept_figures]
    notes = [v for v in ingest.visual_notes if not (v.near_chunk_id and v.near_chunk_id in drop)]
    return ingest.model_copy(update={
        "chunks": kept, "figures": figures, "visual_notes": notes,
        "markdown": "\n\n".join(c.text for c in kept).strip() + "\n",
    })


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _heading(chunk: SourceChunk) -> str:
    return " > ".join(chunk.heading_path)


def _scene_refs(scene: Any) -> list[str]:
    refs: list[str] = [r for b in scene.all_beats() for r in b.source_refs]
    if isinstance(scene, BoardScene):
        refs += [r for item in scene.board for r in item.source_refs]
    if isinstance(scene, QuizScene):
        refs += list(scene.source_refs)
    if scene.intent is not None:
        refs += list(scene.intent.source_refs)
    return list(dict.fromkeys(refs))


def scene_trace(screenplay: Screenplay, ingest: IngestResult, safe: Callable[[str, str], str]) -> list[SceneTrace]:
    """Which source chunks each scene cites (beats, board items, quiz, scene intent), with their headings."""
    by_id = {c.id: c for c in ingest.chunks}
    out: list[SceneTrace] = []
    for scene in screenplay.scenes:
        refs = _scene_refs(scene)
        known = [r for r in refs if r in by_id]
        out.append(SceneTrace(
            scene_id=scene.id, title=_short(safe(scene.title or "", ""), 300), type=scene.type, chunk_ids=known[:20],
            headings=list(dict.fromkeys(safe(_heading(by_id[r]), "") for r in known if by_id[r].heading_path))[:10],
            unknown_refs=len(refs) - len(known),
        ))
    return out


def _excerpt(text: str) -> str:
    """The start of a chunk's prose (code shows as "[code]", figure markers as "[figure]")."""
    prose = " ".join(_paragraphs(text))
    if not prose and any(code for code, _ in fenced_segments(text.split("\n"))):
        prose = "[code]"
    return _short(_FIGURE_MARKER.sub("[figure]", prose), EXCERPT_CHARS)


def build_report(ingest: IngestResult, *, analysis: Analysis | None = None, brief: ConceptBrief | None = None,
                 overrides: SourceOverrides | None = None, screenplay: Screenplay | None = None,
                 filename: str = "") -> SourceReport:
    """The teacher-facing report (see the module docstring). ``brief``: the stored concept brief (during the
    source review the raw one, afterwards the corrected one: ``effective_brief`` is idempotent); ``overrides``:
    the teacher's corrections made on this extract."""
    from .plan import excluded_matcher  # the planner's own rule for what must never be shown or sent

    analysis = analysis if analysis is not None else analyze(ingest)
    leaks = excluded_matcher(ingest, brief)

    def safe(text: str, fallback: str = "(hidden: it contains details set aside from the document)") -> str:
        return fallback if text and leaks(text) else text

    ov = overrides if overrides is not None and not overrides.is_empty() else None
    raw_skips = {s.chunk_id: s.reason for s in brief.skipped_chunks} if brief is not None else {}
    eff = effective_brief(brief, ingest, ov) if brief is not None else None
    excluded = set(ov.excluded_chunk_ids) if ov else set()
    restored = set(ov.restored_chunk_ids) if ov else set()
    cited = eff.cited_chunk_ids() if eff is not None else set()
    concept_of: dict[str, list[str]] = {}
    for c in eff.concepts if eff is not None else []:
        for r in [*c.source_refs, *(x for f in c.key_facts for x in f.source_refs)]:
            concept_of.setdefault(r, [])
            if c.key not in concept_of[r]:
                concept_of[r].append(c.key)
    near = Counter(v.near_chunk_id for v in ingest.visual_notes if v.near_chunk_id)

    raw_cited = brief.cited_chunk_ids() if brief is not None else set()  # built once, not once per chunk
    eff_skipped = eff.skipped_chunk_ids() if eff is not None else set()

    def aadhi_status(cid: str) -> ChunkStatus:
        if brief is None:
            return "teaching"
        reason = raw_skips.get(cid)
        if reason is not None and not (cid in excluded and reason == TEACHER_SKIP_REASON):
            return "set_aside"
        return "teaching" if cid in raw_cited else "context"

    def status(cid: str) -> ChunkStatus:
        if cid in excluded:
            return "set_aside_by_you"
        if cid in restored:
            return "restored"
        if eff is None:
            return "teaching"
        if cid in eff_skipped:
            return "set_aside"
        return "teaching" if cid in cited else "context"

    rows: list[ReportChunk] = []
    statuses: dict[str, ChunkStatus] = {}
    for c in ingest.chunks:
        st = status(c.id)
        statuses[c.id] = st
        cs = analysis.stats.get(c.id) or _ChunkStats()
        rows.append(ReportChunk(
            id=c.id, heading=_short(safe(_heading(c)), 400), page=c.page, chars=len(c.text),
            excerpt=safe(_excerpt(c.text), ""), status=st, aadhi_status=aadhi_status(c.id),
            reason=raw_skips.get(c.id, "") if c.id not in excluded else "", concepts=concept_of.get(c.id, [])[:10],
            visual_notes=near.get(c.id, 0), formulas=cs.formulas, code_blocks=cs.code_blocks, figures=cs.figures,
            tables=cs.tables, questions=cs.questions,
        ))

    def topic(node: _Node) -> dict[str, Any]:
        aside = sum(1 for cid in node.chunk_ids if statuses.get(cid) in ("set_aside", "set_aside_by_you"))
        return {"title": _short(safe(node.title), 300), "level": node.level, "role": node.role,
                "chunk_ids": list(node.chunk_ids), "chars": node.chars,
                "empty": not node.chunk_ids and not any(t.chunk_ids for t in node.subtopics),
                "teaching_chunks": len(node.chunk_ids) - aside, "set_aside_chunks": aside,
                "visual_notes": sum(near.get(cid, 0) for cid in node.chunk_ids)}

    outline = Outline(
        title=_short(safe(analysis.title, ""), 300), title_source=analysis.title_source,  # type: ignore[arg-type]
        sections=[OutlineSection(**topic(s), subtopics=[OutlineTopic(**topic(t)) for t in s.subtopics])
                  for s in analysis.sections],
    )
    findings = [f.model_copy(update={"message": safe(f.message, "A problem in a part set aside from the document."),
                                     "heading": safe(f.heading, ""), "suggestion": safe(f.suggestion, "")})
                for f in analysis.findings][:MAX_FINDINGS]
    inventory = analysis.inventory.model_copy(update={
        "formulas": [f for f in analysis.inventory.formulas if not leaks(f.expression)],
        "source_questions": len(eff.source_questions) if eff is not None else 0,
    })
    scope = [ScopeCount(category=cat, count=n, label=SCOPE_LABELS.get(cat, ""), source="rules")
             for cat, n in sorted(Counter(e.category for e in ingest.excluded).items(), key=lambda kv: (-kv[1], kv[0]))]
    if brief is not None:
        scope += [ScopeCount(category=cat, count=n, label=SCOPE_LABELS.get(cat, ""), source="brief")
                  for cat, n in sorted(Counter(e.category for e in brief.excluded).items(),
                                       key=lambda kv: (-kv[1], kv[0]))]
    concepts: list[ConceptRow] = []
    if brief is not None and eff is not None:
        kept = {c.key: c for c in eff.concepts}
        by_id = {c.id: c for c in ingest.chunks}
        for c in brief.concepts:  # refs as the brief cited them (the Studio previews unsaved set-asides with them)
            e = kept.get(c.key)
            src = e if e is not None else c
            refs = list(dict.fromkeys([*c.source_refs, *(x for f in c.key_facts for x in f.source_refs)]))
            name = src.name
            concepts.append(ConceptRow(
                key=c.key, name=_short(safe(name, "(hidden)"), 160),
                original_name=_short(safe(c.name, "(hidden)"), 160) if name != c.name else "",
                chunk_ids=refs[:20], key_facts=len(src.key_facts), dropped=e is None,
                headings=list(dict.fromkeys(safe(_heading(by_id[r]), "") for r in refs
                                            if r in by_id and by_id[r].heading_path))[:8],
            ))
    accounting = Accounting(chunks=len(rows))
    for row in rows:
        if row.status == "teaching":
            accounting.cited += 1
        elif row.status == "context":
            accounting.context += 1
        elif row.status == "set_aside":
            accounting.set_aside += 1
            if row.reason:
                accounting.skipped_by_reason[row.reason] = accounting.skipped_by_reason.get(row.reason, 0) + 1
        elif row.status == "set_aside_by_you":
            accounting.set_aside_by_you += 1
        else:
            accounting.restored += 1
    return SourceReport(
        file=_short(filename, 255), source_kind=source_kind(ingest.source_mime, filename),
        source_format=ingest.source_format, pages=ingest.pages, language=ingest.detected_language,
        characters=sum(len(c.text) for c in ingest.chunks), truncated=ingest.truncated,
        attach_original=ingest.attach_original, outline=outline, chunks=rows, inventory=inventory, findings=findings,
        findings_total=len(analysis.findings), strengths=analysis.strengths, readiness=readiness(findings),
        scope=scope, visual_notes=len(ingest.visual_notes), brief_available=brief is not None, concepts=concepts,
        accounting=accounting, warnings=[w for w in ingest.warnings if w.strip() and not leaks(w)][:20],
        scenes=scene_trace(screenplay, ingest, safe) if screenplay is not None else [],
        overrides=ov,
    )
