"""Deterministic, realistic FakeLLM responses derived from the prompt context.

With ``LLM_PROVIDER=fake`` the whole pipeline runs offline and still produces a coherent lecture
from the *scoped* source (the teachable content only, see ``source_scope``):

* **Concept brief** (``brief_responder``): concepts come from the source's teaching headings, never
  from structural or administrative ones ("Learning objectives", "Title card", "Bridge to next part",
  "Quick quiz", "Summary", subject / unit lines). Examples, analogies and applications join the
  concept they illustrate; a run of small sibling sections (Postulate 1, 2, 3 ...) becomes one concept
  when the list would otherwise be too long. Facts are the source's formula lines and definitions,
  and the source's quiz questions are kept with their answers and chunk refs. Every chunk is
  accounted for (``account_for_chunks``): a lone title-page line or a metadata section is skipped as
  administrative, a title card or a bridge to the next clip as scaffolding, and everything else
  (objectives, a hook, a summary) is cited by the nearest concept.
* **Plan** (``plan_responder``): it is built from "Concepts to teach" (or, without a brief, from the
  same reading of the headings). There is one cold open that uses the source's hook section and states
  the objectives once. Then come the concepts in teaching order, with worked examples, analogies and
  applications from the source. A step-by-step animation is added when the source has a formula or a
  derivation and the ``equation_steps`` template exists. There is a quiz every N concepts (built from
  the source's own questions when it has them), a narrative role and a one-sentence bridge for every
  scene, and the author's visual suggestions next to each scene's chunks.
* **Scenes**: narration and board items come from the source's real sentences, joined across the
  line breaks of a script, with sign-offs and recording talk ("in this video") filtered out. Each
  scene opens with its planned bridge.

Every responder reads only the prompt (the JSON sections written by the pipeline), so outputs are a
pure function of the input.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..schemas.screenplay import slugify
from . import integrations
from .prompting import extract_json
from .source_scope import parse_title_line
from .validate import packaging_phrases, subject_nouns_in

log = logging.getLogger(__name__)

Responder = Callable[[str, type[BaseModel]], dict[str, Any]]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_MARKERS = re.compile(r"\[Figure [^\]]*\]|<!--.*?-->")
_FORMULA = re.compile(r"\b([A-Z][a-z]?)\s*=\s*([A-Z][a-z]?)\s*(?:([×x*·/])\s*)?([A-Z][a-z]?)\b")
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVX]+\.|chapter\s+\d+[:.]?|unit\s+\d+[:.]?)\s*", re.IGNORECASE)
_STOP_ACRONYMS = {
    "THE", "AND", "FOR", "NOT", "ALL", "ARE", "YOU", "PDF", "DOCX", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO",
    "HE", "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "OFF", "HIGH", "LOW", "YES", "NOR", "BIT", "SET", "GET", "RUN", "ADD", "END", "OUT", "NEW", "OLD", "TWO",
}
_TIMESTAMP = re.compile(r"[\[(]\s*\d{1,2}:\d{2}[^\])]*[\])]")
_LABEL = re.compile(
    r"^(?:(?:segment|scene|clip|part|section|lesson|session|topic|module)\s*\d+\s*(?:script)?\s*[-–:.]\s*"
    r"|(?:board\s+title|title\s+card|subject\s+name|unit\s+name|on\s+screen)\s*[-–:]\s*)+",
    re.IGNORECASE,
)
_METADATA = re.compile(r"^\s*(?:subject\s+name|unit\s+name|session\s+\d+\s*$|title\s+card|scene\s+\d+\s*[-–:]\s*title\s+card)",
                       re.IGNORECASE)
_SPEAKER = re.compile(r"^[A-Z][A-Za-z]{1,20}\s+(?:speaks|says|narrates|displays(?:\s*\([^)]*\))?)\s*:\s*", re.IGNORECASE)
_DIRECTION = re.compile(r"^[A-Z][A-Z /&-]{2,40}(?:\s+[A-Za-z]+)?\s*:")  # "ANIMATION:", "BOARD Title:", "DETAILED VISUAL:"
_VOWELS = set("AEIOU")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "vs"}
_LEGEND = re.compile(r"\b([A-Z][a-z]?) (?:is|denotes|stands for) the ([a-z]+(?: (?!and\b|or\b|is\b)[a-z]+)?)")

WEIGHTS = {"title": 30, "recap": 35, "chapter_card": 6, "content": 60, "example": 70, "simulation": 50,
           "quiz_checkpoint": 40, "summary": 40, "key_takeaway": 35, "ai_video": 30, "interactive": 40}
NATIVE = {"ta": "தமிழ்", "hi": "हिंदी", "te": "తెలుగు", "kn": "ಕನ್ನಡ", "ml": "മലയാളം", "en": "English"}
MAX_BRIEF_CONCEPTS = 8  # a demo brief groups small sibling sections beyond this
MAX_EXAMPLE_SCENES = 3


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Source text without figure/page markers, table rows, Markdown syntax and script stage directions.

    Teachers sometimes upload video scripts: lines such as "ANIMATION: …" or "BOARD Title: …" are
    dropped and speaker labels ("Aadhi speaks:") are removed, so only teachable prose remains.
    """
    return re.sub(r"\s+", " ", " ".join(source_lines(text))).strip()


_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def source_lines(text: str) -> list[str]:
    """Plain lines of source text: markers, table rows, Markdown, speaker labels and directions removed."""
    out: list[str] = []
    for line in _MARKERS.sub(" ", text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("|"):
            continue
        s = _LIST_MARK.sub("", re.sub(r"^#+\s*", "", s)).replace("**", "").replace("__", "")
        s = _SPEAKER.sub("", s).strip()
        if s and not _DIRECTION.match(s):
            out.append(re.sub(r"\s+", " ", s))
    return out


_CONNECTIVES = {"and", "or", "not", "but", "then", "if", "so", "nor", "else"}
_SIGN_OFF = re.compile(
    r"^(?:thank\s+you|thanks)\b|^(?:see\s+you|bye|goodbye)\b|^that'?s\s+(?:all|it)\s+for\b"
    r"|^let'?s\s+(?:summari[sz]e|recap|begin|get\s+started)\b|^(?:welcome|hello|hi)\b",
    re.I,
)
_LEAD_IN_MAX_WORDS = 32
# "We will learn KCL and how to apply it." states an objective of the video, it teaches nothing
_OBJECTIVE_SENTENCE = re.compile(r"^(?:(?:so|now|today)\s*,?\s*)?(?:we|you)\s+(?:will|are\s+going\s+to|shall|'ll)\s+"
                                 r"(?:learn|see|cover|study|discuss|look\s+at|explore|find\s+out)\b", re.I)


def _unwrap(lines: list[str]) -> list[str]:
    """Join lines broken in the middle of a sentence (PDF layout): the next line starts lower-case."""
    out: list[str] = []
    for s in lines:
        if out and out[-1][-1:] not in ".!?:" and s[:1].islower() and s.split()[0].lower() not in _CONNECTIVES:
            out[-1] = f"{out[-1]} {s}"
        else:
            out.append(s)
    return out


def prose_sentences(text: str, limit: int = 12, nouns: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Teachable sentences of a source text, in order and de-duplicated.

    Scripts break sentences over lines ("Where:" / "0 means FALSE" / "1 means TRUE"); a lead-in line
    ending with a colon is joined with the fragments that follow it. Board fragments without a
    sentence around them, sign-offs ("Thank you for watching.") and sentences about the recording
    ("In this video, we will learn") are dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    pending: list[str] = []

    def add(sentence: str) -> None:
        s = re.sub(r"\s+", " ", sentence).strip().strip('"“”').strip()
        if not s:
            return
        s = s if s[-1] in ".!?" else s.rstrip(" ,;:") + "."
        key = re.sub(r"\W+", " ", s.lower()).strip()
        if key in seen or not 3 <= len(s.split()) <= 60 or _SIGN_OFF.search(s) or _OBJECTIVE_SENTENCE.search(s) or (
                packaging_phrases(s, set(nouns))):
            return
        seen.add(key)
        out.append(s)

    def flush() -> None:
        if len(pending) == 1 and len(pending[0].split()) >= 6:  # a statement that ends in a colon
            add(pending[0])
        elif len(pending) > 1:
            joined = pending[0]
            for frag in pending[1:]:
                last = joined.split()[-1].lower() if joined.split() else ""
                joiner = " " if joined.endswith(":") or frag.lower() in _CONNECTIVES or last in _CONNECTIVES else ", "
                joined = f"{joined}{joiner}{frag.rstrip('.')}"
            add(joined)
        pending.clear()

    fragment = ""  # a short label line just before ("AND Operation")
    for s in _unwrap(source_lines(text)):
        words = s.split()
        if pending:
            joining = pending[-1].endswith(":") or pending[-1].lower() in _CONNECTIVES
            full = s[-1] in ".!?" and len(words) >= (8 if joining else 4) and s[:1].isupper()
            if s.endswith(":") or full or len(" ".join(pending).split()) > _LEAD_IN_MAX_WORDS:
                flush()
            else:
                pending.append(s)
                if s[-1] in ".!?":
                    flush()
                continue
        if s.endswith(":"):
            pending.append(s)
        elif s[-1] in ".!?":
            if fragment and re.match(r"^[A-Z][a-z]+ed\b", s):  # "AND Operation" + "Represented by a dot."
                s = f"{fragment} is {s[0].lower()}{s[1:]}"
            for part in _SENT_SPLIT.split(s):
                add(part)
        letters = sum(ch.isalpha() for ch in s)
        shouted = letters and sum(ch.isupper() for ch in s) >= 0.8 * letters
        fragment = s if len(words) <= 4 and s[-1:] not in ".!?:" and latex_of(s) is None and not shouted else ""
    flush()
    return out[:limit]


def table_sentences(text: str, limit: int = 6) -> list[str]:
    """One sentence per row of the Markdown tables in ``text`` ('AND: boolean expression Y = A · B; output is 1
    when all inputs are 1.'), so a section that teaches with a table can still be narrated."""
    out: list[str] = []
    headers: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            headers = []
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        if not headers:
            headers = cells
            continue
        pairs = [f"{focus_case(h, text)} {c}" if h else c for h, c in zip(headers[1:], cells[1:], strict=False) if c]
        if cells and cells[0] and pairs:
            out.append(f"{cells[0]}: {'; '.join(pairs)}.")
    return out[:limit]


def sentences(text: str, limit: int = 12) -> list[str]:
    """Teachable sentences of ``text`` (see ``prose_sentences``)."""
    return prose_sentences(text, limit)


def speakable(text: str) -> str:
    """Remove characters that narration must not contain (symbols are said in words)."""
    t = text.replace("^", " to the power ").replace("_", " ").replace("%", " percent")
    t = t.replace("·", " dot ").replace("̅", " bar").replace("×", " times ")
    t = re.sub(r"[*`$\\{}<>#\[\]|]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def board_safe(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`$\\\[\]|]", "", text)).strip()


def shorten(text: str, n: int = 90) -> str:
    t = board_safe(text).rstrip(".")
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def key_phrase(sentence: str, max_words: int = 6) -> str:
    """A short board phrase for a sentence (the narration says the full sentence: redundancy principle)."""
    words = board_safe(sentence).rstrip(".!?").split()
    while words and words[0].lower() in ("a", "an", "the", "so", "and", "but", "also", "in", "this"):
        words = words[1:]
    phrase = " ".join(words[:max_words]).rstrip(",;:")
    return (phrase + " …") if len(words) > max_words else phrase


def limit_words(text: str, n: int = 45) -> str:
    words = text.split()
    if len(words) <= n:
        return text
    return " ".join(words[:n]).rstrip(",;:") + "."


def title_of(heading: str) -> str:
    """Readable concept title from a heading path: numbering, script labels and timestamps removed."""
    t = _NUMBERING.sub("", (heading or "").split(">")[-1]).strip()
    t = _TIMESTAMP.sub(" ", t)
    t = _LABEL.sub("", t).strip(" -–:")
    t = re.sub(r"\s+", " ", t).strip()
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:  # SHOUTED HEADING -> Title Case
        words = t.lower().split()
        t = " ".join(w if i and w in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return t[:80]


def is_metadata_heading(heading: str) -> bool:
    """Headings that describe the document rather than teach (subject/unit names, title cards)."""
    return bool(_METADATA.search((heading or "").split(">")[-1]))


def find_formula(text: str) -> tuple[str, str, str, str] | None:
    """(lhs, a, op, b) for simple formulas such as 'V = I × R', 'V = IR', 'I = V/R'.

    'A = ID Card' (a letter naming a thing) is not a formula: an implicit product must not run into a
    capitalised word.
    """
    plain = clean_text(text)
    for m in _FORMULA.finditer(plain):
        lhs, a, op, b = m.group(1), m.group(2), m.group(3) or "*", m.group(4)
        if len({lhs, a, b}) < 3:
            continue
        rest = plain[m.end():]
        if (not m.group(3) and re.match(r"\s+[A-Z][a-z]", rest)) or re.match(r"\s*[-+−·×*/(̅]", rest):
            continue  # 'A = ID Card' names a thing; 'Y = AB + AB̅' goes on: neither is a simple formula
        return lhs, a, "/" if op == "/" else "*", b
    return None


def formula_latex(f: tuple[str, str, str, str]) -> str:
    lhs, a, op, b = f
    return f"{lhs} = \\frac{{{a}}}{{{b}}}" if op == "/" else f"{lhs} = {a} \\times {b}"


def formula_variables(f: tuple[str, str, str, str], text: str) -> list[dict[str, str]]:
    """Legend entries parsed from text such as 'where V is the voltage, I is the current'."""
    found: dict[str, str] = {}
    for m in _LEGEND.finditer(clean_text(text)):
        found.setdefault(m.group(1), m.group(2))
    return [{"symbol_latex": sym, "meaning": found[sym]} for sym in (f[0], f[1], f[3]) if sym in found]


def formula_spoken(f: tuple[str, str, str, str]) -> str:
    lhs, a, op, b = f
    return f"{lhs} equals {a} divided by {b}" if op == "/" else f"{lhs} equals {a} times {b}"


_LATEX_CHARS = re.compile(r"^[A-Za-z0-9\s+\-=()\\{}.,'/^_<>|\[\]]*$")
_TEXT_WORD = re.compile(r"(?<![\\A-Za-z])[A-Za-z]*[a-z][A-Za-z]*")
_ENGLISH = re.compile(r"\b(?:or|and|if|then|of|to|is|in|for|as|at|by|on|so|it|be|we|not)\b", re.I)


def latex_of(expr: str) -> str | None:
    """LaTeX for a source formula line ('A · A̅ = 0' -> 'A \\cdot \\overline{A} = 0'), or None when the line is
    not a clean formula (it contains words, or symbols without a LaTeX form)."""
    s = re.sub(r"(\([^()]*\)|[A-Za-z0-9])̅", lambda m: "\\overline{" + m.group(1) + "}", expr or "")
    s = s.replace("·", " \\cdot ").replace("×", " \\times ").replace("÷", " \\div ").replace("−", "-").replace("–", "-")
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    if "=" not in s or not _LATEX_CHARS.match(s):
        return None
    bare = re.sub(r"\\[A-Za-z]+", " ", s)
    if any(len(w) >= 3 for w in _TEXT_WORD.findall(bare)) or _ENGLISH.search(bare):
        return None
    return s


def formula_steps(text: str) -> list[tuple[str, str]]:
    """Clean formula lines of a text, in order, each with the lead-in that introduced it
    ('Factor A:' -> 'Factor A'); duplicates (a line said aloud and shown on the board) dropped."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    lead = ""
    for s in source_lines(text):
        tex = latex_of(s)
        if tex is None:
            words = s.rstrip(":").split()
            if s.endswith(":") and len(words) <= 8:
                lead = " ".join(words)
            elif s[-1:] in ".!?":
                lead = ""
            continue
        key = re.sub(r"\s+", "", tex)
        if key not in seen:
            seen.add(key)
            out.append((s.rstrip("."), lead))
        lead = ""
    return out


def derivation(text: str) -> list[tuple[str, str]]:
    """The longest run of formula lines with the same left-hand side ('Y = AB + AB̅' ... 'Y = A'), each
    with what happened in between (lead-ins and side results), or [] when shorter than three lines."""
    steps = formula_steps(text)
    best: list[tuple[str, str]] = []
    for i, (expr, _) in enumerate(steps):
        lhs = expr.split("=", 1)[0].strip()
        chain: list[tuple[str, str]] = [(expr, "")]
        between: list[str] = []
        for later, lead in steps[i + 1:]:
            if later.split("=", 1)[0].strip() == lhs:
                note = " ".join(x for x in [*between, lead] if x).strip()
                chain.append((later, note))
                between = []
            else:
                between += [x for x in (lead, later) if x]
        if len(chain) > len(best):
            best = chain
    return best if len(best) >= 3 else []


def misconception_for(concept: dict[str, Any], other: dict[str, Any] | None) -> tuple[str, str]:
    """A typical student error for a concept: proportional reasoning for formulas, confusion otherwise."""
    title, summary, f = concept["title"], concept["summary"], concept.get("_formula")
    if f:
        lhs, a, op, b = f
        if op == "/":
            return (f"If {b} doubles, {lhs} doubles too.",
                    f"{lhs} = {a}/{b}, so {lhs} is inversely proportional to {b}: doubling {b} halves {lhs} when {a} is fixed.")
        return (f"If {b} doubles, {lhs} is halved.",
                f"{lhs} = {a} x {b}, so {lhs} is proportional to {b}: doubling {b} doubles {lhs} when {a} is fixed.")
    focus = concept.get("_focus") or title
    if other is not None:
        return (f"{focus[:1].upper() + focus[1:]} and {mid_sentence(other.get('_focus') or other['title'])} mean the "
                "same thing.", f"They are different ideas. {summary}")
    return (f"You only need {mid_sentence(focus)} for exams, not for real devices.",
            f"Engineers rely on it in real devices every day. {summary}")


def key_of(text: str, fallback: str) -> str:
    return (slugify(text, fallback).replace("-", "_")[:40].strip("_")) or fallback


# ---------------------------------------------------------------------------
# reading the source: section roles, concept names, questions
# ---------------------------------------------------------------------------

_STRUCTURAL = re.compile(
    r"^(?:(?:learning|lesson|session|course|clip|video|module)\s+)?(?:objectives?|outcomes?|goals?|agenda)$"
    r"|^what\s+(?:this|the|today'?s)\s+\w+\s+(?:will\s+)?covers?$|^what\s+(?:you|we)(?:'ll|\s+will)\s+learn$"
    r"|^in\s+this\s+(?:video|clip|session|lesson)$"
    r"|\btitle\s+(?:card|screen)\b|^(?:opening\s+)?titles?$|\bbridge\s+to\b|^(?:coming\s+up|up\s+next)\b"
    r"|^next\s+(?:part|clip|video|episode)\b|^(?:subject|unit|course|module)\s+name\b|^session\s+\d+$"
    r"|^(?:outro|sign[\s-]?off|thank\s+you|closing)\b|^(?:summary|recap|conclusion|wrap[\s-]?up|key\s+takeaways?)$"
    r"|^(?:summary|recap)\s+of\b|^let'?s\s+summari[sz]e$",
    re.I,
)
_QUIZ_HEADING = re.compile(
    r"^(?:(?:quick|knowledge|self|review|practice|revision|recap)[\s-]+)?(?:quiz(?:zes)?|questions|check|exercises?"
    r"|problems|test\s+yourself|mcqs?)$|^quiz\b|^(?:practice|homework|assignment|try\s+(?:it|this)\s+yourself)$",
    re.I,
)
# a side remark that belongs to the concept before it: a misconception box, a note, a formula used as a heading
_ASIDE_HEADING = re.compile(r"\bmisconceptions?\b|\bcommon\s+(?:mistakes?|errors?|pitfalls?)\b|\bpitfalls?\b"
                            r"|^(?:note|remember|caution|tip|key\s+point)s?$", re.I)
_HOOK_HEADING = re.compile(r"^(?:the\s+)?(?:hook|motivation|warm[\s-]?up|why\s+(?:this|it)\s+matters)\b", re.I)
_INTRO_HEADING = re.compile(r"^(?:an?\s+)?(?:introduction|intro|overview)\b|\b(?:introduction|overview|basics)$", re.I)
_EXAMPLE_HEADING = re.compile(r"\b(?:worked\s+)?examples?\b|\bsolved\s+problems?\b|\billustrations?\b", re.I)
_ANALOGY_HEADING = re.compile(r"\banalog(?:y|ies)\b|\breal[\s-]?world\s+(?:understanding|example|view)|\bcompared\b"
                              r"|\bcomparison\b|\bversus\b|\bvs\.?\b|\bin\s+everyday\s+life\b", re.I)
_APPLICATION_HEADING = re.compile(r"\bapplications?\b|\bin\s+practice\b|\buses\s+of\b|\bcase\s+stud(?:y|ies)\b", re.I)
_WHY = re.compile(r"\b(?:helps?|used\s+(?:to|in|for|by|everywhere)|important|foundation|allows?|ensures?|because"
                  r"|essential|powerful|backbone|relies?)\b", re.I)
_CALLED = re.compile(r"\b(?:[Tt]his|[Ii]t|[Tt]hat)\s+(?:[a-z]+\s+)?(?:is|are)\s+called\s+([A-Z][\w'’ -]{2,40}?)\s*[.!]")
_DEFINITION_SENTENCE = re.compile(r"\b(?:is|are)\s+(?:called|defined\s+as|known\s+as)\b|\b(?:states?|means?)\s+that\b"
                                  r"|^(?:an?\s+)?[\w'’ -]{2,40}\s+is\s+an?\s+", re.I)


_SUMMARY_HEADING = re.compile(r"^(?:summary|recap|conclusion|wrap[\s-]?up|key\s+takeaways?)\b", re.I)


def _takeaway(title: str, candidates: Sequence[str], summary: Sequence[str]) -> str:
    """What a summary should say about a concept: a definition or law that names it (a key fact or a source
    sentence), else the source summary's sentence about it, else any definition ("" when there is none)."""
    words = [w for w in re.findall(r"[a-z’']{4,}", title.lower()) if w not in ("with", "from", "that", "this")]

    def names(s: str) -> bool:
        return bool(words) and any(w in s.lower() for w in words)

    prose = [c for c in candidates if c and latex_of(c) is None and len(c.split()) >= 5]
    defs = [c for c in prose if _DEFINITION_SENTENCE.search(c)]
    named = [c for c in defs if names(c)] or [s for s in summary if names(s)]
    return (named or defs or [""])[0]


def section_kind(heading: str) -> str:
    """Role of a source section from its heading: structural | quiz | hook | intro | example | analogy |
    application | aside | content."""
    t = title_of(heading).strip().rstrip("?.!:").strip()
    if not t:
        return "content"
    if _STRUCTURAL.search(t) or is_metadata_heading(heading):
        return "structural"
    if _QUIZ_HEADING.search(t):
        return "quiz"
    if _HOOK_HEADING.search(t):
        return "hook"
    if _ASIDE_HEADING.search(t) or latex_of(t) is not None or ("=" in t and not re.search(r"[A-Za-z]{4,}", t)):
        return "aside"
    if _APPLICATION_HEADING.search(t):
        return "application"
    if _EXAMPLE_HEADING.search(t):
        return "example"
    if _ANALOGY_HEADING.search(t):
        return "analogy"
    if _INTRO_HEADING.search(t):
        return "intro"
    return "content"


def concept_name(heading: str) -> str:
    """A concept's name from its heading: 'POSTULATE 1: CLOSURE PROPERTY' -> 'Closure Property',
    'What is Boolean algebra?' -> 'Boolean algebra', 'Boolean postulates introduction' -> 'Boolean postulates'."""
    t = title_of(heading)
    t = re.sub(r"^(?:postulate|law|theorem|rule|property|principle|step|part|lesson|topic|example|case)\s*\d+[a-z]?"
               r"\s*[:.\-–]\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:an?\s+)?(?:introduction|intro|overview)\s+(?:to|of)\s+(?:the\s+)?", "", t, flags=re.I)
    t = re.sub(r"\s+(?:introduction|overview|basics)$", "", t, flags=re.I)
    t = re.sub(r"^what\s+(?:is|are)\s+(?:an?\s+|the\s+)?", "", t, flags=re.I)
    t = t.strip().rstrip("?.!:").strip()
    return t or title_of(heading)


_COMMON_FIRST = {"the", "a", "an", "you", "one", "two", "three", "four", "five", "basic", "fundamental", "simple", "real",
                 "why", "how", "what", "types", "properties", "key", "main", "important", "common", "more"}


def natural_case(title: str, source_text: str = "") -> str:
    """'Fundamental Boolean Laws' -> 'Fundamental Boolean laws': a word is lower-cased when the source writes it
    in lower case somewhere, so names (Boolean, Ohm) keep their capitals."""
    words = title.split()
    if not words:
        return title
    lower = set(re.findall(r"\b[a-z][a-z'’-]+\b", source_text or ""))
    out = [words[0]]
    for w in words[1:]:
        bare = w.strip("()?,.:;")
        low = bare.lower()
        if bare and bare[0].isupper() and not bare.isupper() and (low in lower or low.rstrip("s") in lower
                                                                  or low in _SMALL_WORDS):
            w = w.replace(bare, bare.lower(), 1)
        out.append(w)
    return " ".join(out)


def focus_case(name: str, source_text: str = "") -> str:
    """``name`` as it reads inside a sentence: the first word is lower-cased when the source writes it in lower
    case ('Identity elements' -> 'identity elements'), names keep their capitals ('Boolean algebra')."""
    words = name.split()
    if not words:
        return name
    bare = words[0].strip("()?,.:;'’")
    lower = set(re.findall(r"\b[a-z][a-z'’-]+\b", source_text or ""))
    low = bare.lower()
    if bare and not bare.isupper() and (low in _COMMON_FIRST or low in lower or low.rstrip("s") in lower):
        words[0] = words[0].replace(bare, low, 1)
    return " ".join(words)


def mid_sentence(name: str) -> str:
    """``name`` as it reads inside a sentence ('Three basic logical operations' -> 'three basic …')."""
    first = name.split()[0] if name.split() else ""
    if first.lower() in _COMMON_FIRST and not first.isupper():
        return first.lower() + name[len(first):]
    return name


_QA_INLINE = re.compile(r"^(?:q(?:uestion)?\s*\d*\s*[:.)\-–]\s*)?(?P<q>.{4,300}?\?)\s*(?:answer|ans)\s*[:.\-–]\s*(?P<a>.+)$",
                        re.I)
_Q_LABEL = re.compile(r"^q(?:uestion)?\s*\d+\s*[:.)\-–]?\s*", re.I)
_A_LABEL = re.compile(r"^(?:answer|ans|solution)\s*\d*\s*[:.)\-–]\s*", re.I)


def qa_pairs(text: str, *, any_question: bool = True) -> list[tuple[str, str]]:
    """(question, answer) pairs of a quiz section ('Question 1:' / '...?' / 'Answer:' / '0 and 1.' or one line
    'Q1: ...? Answer: ...'). Outside a quiz (``any_question=False``) only labelled questions count."""
    pairs: list[tuple[str, str]] = []
    question: str | None = None
    want_answer = False
    for s in source_lines(text):
        inline = _QA_INLINE.match(s)
        if inline and (any_question or _Q_LABEL.match(s)):
            pairs.append((inline["q"].strip(), inline["a"].strip()))
            question, want_answer = None, False
            continue
        if want_answer and question:
            pairs.append((question, s))
            question, want_answer = None, False
            continue
        answer = _A_LABEL.match(s)
        if answer and question:
            rest = s[answer.end():].strip()
            if rest:
                pairs.append((question, rest))
                question = None
            else:
                want_answer = True
            continue
        labelled = bool(_Q_LABEL.match(s))
        body = _Q_LABEL.sub("", s).strip()
        if body.endswith("?") and (any_question or labelled):
            if question:
                pairs.append((question, ""))
            question = body
    if question:
        pairs.append((question, ""))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for q, a in pairs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append((q, a.strip()))
    return out


def source_questions(chunks: Sequence[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(question, answer, chunk id) for the source's own quiz / review questions."""
    out: list[tuple[str, str, str]] = []
    for c in chunks:
        quiz = section_kind(c.get("heading") or "") == "quiz"
        for q, a in qa_pairs(str(c.get("text") or ""), any_question=quiz):
            if all(q.lower() != x[0].lower() for x in out):
                out.append((q, a, c["id"]))
    return out[:20]


def split_question(point: str) -> tuple[str, str] | None:
    """'What is A + 0? Answer: A.' -> ('What is A + 0?', 'A')."""
    m = re.match(r"^(?P<q>.+?\?)\s*(?:answer|ans)\s*[:.\-–]\s*(?P<a>.+)$", point.strip(), re.I)
    if m:
        return m["q"].strip(), m["a"].strip().rstrip(".")
    return (point.strip(), "") if point.strip().endswith("?") else None


# ---------------------------------------------------------------------------
# concepts from the source (shared by the brief and by a plan made without one)
# ---------------------------------------------------------------------------


@dataclass
class SourceConcept:
    name: str
    parent: str = ""
    main: list[str] = field(default_factory=list)  # chunk ids that teach it
    examples: list[str] = field(default_factory=list)  # worked examples
    analogies: list[str] = field(default_factory=list)  # analogies, comparisons, everyday situations
    applications: list[str] = field(default_factory=list)
    parts: list[tuple[str, list[str]]] = field(default_factory=list)  # merged sibling sections (name, chunk ids)
    context: list[str] = field(default_factory=list)  # a short introduction that leads into it

    @property
    def refs(self) -> list[str]:
        return list(dict.fromkeys(self.context + self.main + self.examples + self.analogies + self.applications))


def _sections(chunks: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Consecutive chunks under the same heading form one section."""
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for c in chunks:
        heading = c.get("heading") or ""
        if out and out[-1][0] == heading:
            out[-1][1].append(c)
        else:
            out.append((heading, [c]))
    return out


def _merge(concepts: list[SourceConcept], start: int, end: int, name: str) -> None:
    group = concepts[start:end]
    merged = SourceConcept(name=name, parent=group[0].parent,
                           parts=[p for c in group for p in (c.parts or [(c.name, list(c.main))])])
    for c in group:
        merged.context += c.context
        merged.main += c.main
        merged.examples += c.examples
        merged.analogies += c.analogies
        merged.applications += c.applications
    concepts[start:end] = [merged]


def source_concepts(chunks: Sequence[dict[str, Any]], max_concepts: int = MAX_BRIEF_CONCEPTS) -> list[SourceConcept]:
    """The concepts a source teaches, in order, read from its section headings.

    Structural sections (objectives, title cards, bridges, quizzes, summaries, the hook) are not
    concepts. Examples, analogies and applications join the concept before them; an introduction too
    short to teach on its own joins the concept after it. When there are more than ``max_concepts``,
    runs of sibling sections under one parent heading are merged into a concept named after the parent
    (largest run first), then the tail is folded into the last concept.
    """
    concepts: list[SourceConcept] = []
    carry: list[str] = []
    for heading, cs in _sections(chunks):
        kind = section_kind(heading)
        if kind in ("structural", "quiz", "hook"):
            continue
        ids = [c["id"] for c in cs]
        text = "\n".join(str(c.get("text") or "") for c in cs)
        name = concept_name(heading)
        if kind in ("example", "analogy", "application") and concepts:
            getattr(concepts[-1], {"example": "examples", "analogy": "analogies", "application": "applications"}[kind]).extend(ids)
            continue
        if kind == "aside" and concepts:
            concepts[-1].main.extend(ids)
            continue
        if kind == "intro" and len(prose_sentences(text, 3)) < 2 and not formula_steps(text):
            carry += ids
            continue
        top_level = len((heading or "").split(">")) <= 1
        if top_level and not prose_sentences(text, 1) and not formula_steps(text) and "|" not in text:
            continue  # a title page or a subtitle line: nothing to teach
        called = _CALLED.search(clean_text(text))
        if called and (not name or re.match(r"^(?:why|how|when|where)\b", name, re.I)):
            name = called.group(1).strip()  # "Why simplify ...?" + "This process is called Boolean Minimization."
        if not name:
            first = prose_sentences(text, 1)
            name = " ".join((first[0] if first else f"Idea {len(concepts) + 1}").split()[:4]).rstrip(".,")
        parts = (heading or "").split(">")
        concepts.append(SourceConcept(name=name, parent=parts[-2].strip() if len(parts) >= 2 else "", main=ids,
                                      context=carry))
        carry = []
    if carry and concepts:
        concepts[-1].main += carry
    while len(concepts) > max_concepts:
        runs: list[tuple[int, int]] = []
        i = 0
        while i < len(concepts):
            j = i
            while j + 1 < len(concepts) and concepts[j + 1].parent and concepts[j + 1].parent == concepts[i].parent:
                j += 1
            if j > i and concepts[i].parent and not concepts[i].parts:
                runs.append((i, j + 1))
            i = j + 1
        if not runs:
            break
        start, end = max(runs, key=lambda r: (r[1] - r[0], -r[0]))
        _merge(concepts, start, end, concept_name(concepts[start].parent))
    if len(concepts) > max_concepts:
        _merge(concepts, max_concepts - 1, len(concepts), concepts[max_concepts - 1].name)
    return concepts


def _text_of(ids: Sequence[str], by_id: dict[str, dict[str, Any]]) -> str:
    return "\n".join(str(by_id[i].get("text") or "") for i in ids if i in by_id)


def _facts(ids: Sequence[str], by_id: dict[str, dict[str, Any]], limit: int = 6) -> list[tuple[str, str]]:
    """(fact, chunk id): the source's clean formula lines (as LaTeX) and definitions."""
    out: list[tuple[str, str]] = []
    for cid in ids:
        text = str(by_id.get(cid, {}).get("text") or "")
        for expr, _ in formula_steps(text):
            tex = latex_of(expr)
            if tex and re.search(r"[A-Za-z]", expr) and all(tex != f for f, _ in out):
                out.append((tex, cid))
        for s in prose_sentences(text, 6):
            if _DEFINITION_SENTENCE.search(s) and all(s != f for f, _ in out):
                out.append((s, cid))
                break
    return out[:limit]


# ---------------------------------------------------------------------------
# concept brief
# ---------------------------------------------------------------------------


def _example_line(ids: Sequence[str], by_id: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for cid in ids:
        chunk = by_id.get(cid) or {}
        name = concept_name(chunk.get("heading") or "")
        text = str(chunk.get("text") or "")
        steps = formula_steps(text)
        if len(steps) >= 2:
            body = "; ".join(expr for expr, _ in steps[:5])
        else:
            body = " ".join(prose_sentences(text, 2))
        if body:
            out.append(f"{name}: {body}"[:300] if name else body[:300])
    return out


def brief_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    """``GenBrief`` from the prompt's source chunks: teaching concepts in source order, facts, examples and
    the source's own questions with their answers (nothing structural or administrative)."""
    chunks = [c for c in (extract_json(prompt, "Source chunks") or []) if isinstance(c, dict) and c.get("id")]
    req = extract_json(prompt, "Request") or {}
    by_id = {c["id"]: c for c in chunks}
    source = "\n".join(str(c.get("text") or "") for c in chunks)
    limit = {"overview": 5, "deep": 12}.get(str(req.get("depth") or ""), MAX_BRIEF_CONCEPTS)
    found = source_concepts(chunks, limit)
    hook = [c for c in chunks if section_kind(c.get("heading") or "") == "hook"]
    hook_why = next((s for s in prose_sentences(_text_of([c["id"] for c in hook], by_id), 8) if _WHY.search(s)), "")
    concepts: list[dict[str, Any]] = []
    used: set[str] = set()
    for i, sc in enumerate(found):
        name = natural_case(sc.name, source)
        key = key_of(name, f"concept_{i + 1}")
        while key in used:
            key += "_x"
        used.add(key)
        main_text = _text_of(sc.main, by_id)
        sents = prose_sentences(main_text, 8)
        if sc.parts:
            points = []
            for part_name, ids in sc.parts:
                first = prose_sentences(_text_of(ids, by_id), 1)
                points.append(f"{natural_case(part_name, source)}: {first[0]}" if first else natural_case(part_name, source))
        else:
            points = [s for s in sents if len(s.split()) >= 5 and not s.endswith("?")][:4]
        why = next((s for s in sents if _WHY.search(s)), "") or (hook_why if i == 0 else "")
        facts = _facts(sc.main, by_id) or _facts(sc.refs, by_id)
        concepts.append({
            "key": key, "name": name, "why_it_matters": why[:300],
            "must_explain": [p[:300] for p in points[:6]] or [s[:300] for s in sents[:2]],
            "key_facts": [{"text": f, "source_refs": [cid]} for f, cid in facts],
            "examples": _example_line(sc.examples + sc.analogies + sc.applications, by_id)[:6],
            "prerequisites": [concepts[-1]["key"]] if concepts else [],
            "source_refs": sc.refs[:20],
        })
    questions = [{"text": f"{q} Answer: {a}" if a else q, "source_refs": [cid]} for q, a, cid in source_questions(chunks)]
    skipped = account_for_chunks(chunks, concepts, questions)
    tops = [title_of((c.get("heading") or "").split(">")[0]) for c in chunks if c.get("heading")]
    top = next((t for t in tops if t and section_kind(t) == "content"), "")
    many_parts = len({t for t in tops if t}) > 2
    if many_parts and len(concepts) > 1:  # several clips: name the arc, not the first clip
        topic = f"{concepts[0]['name']} to {mid_sentence(focus_case(concepts[-1]['name'], source))}"
    else:
        topic = top or (concepts[0]["name"] if concepts else "")
    return {
        "topic": topic,
        "concepts": concepts,
        "teaching_order": [c["key"] for c in concepts],
        "source_questions": questions,
        "skipped_chunks": skipped,
        "excluded": [],
        "notes": ("The source is split into several parts; teach it as one continuous lecture in the order above."
                  if many_parts else ""),
    }


_SCAFFOLD_HEADING = re.compile(
    r"\btitle\s+(?:card|screen)\b|^(?:opening\s+)?titles?$|\bbridge\s+to\b|^(?:coming\s+up|up\s+next)\b"
    r"|^next\s+(?:part|clip|video|episode)\b|^(?:outro|sign[\s-]?off|thank\s+you)\b|^in\s+this\s+(?:video|clip)$",
    re.I,
)


def skip_reason(chunk: dict[str, Any]) -> str | None:
    """Why a chunk holds no teaching content, or None: a lone title-page line ("Subject - Unit 1: X - Session 2")
    or a metadata heading with no sentence under it is administrative; a title card, a bridge to the next clip
    or a sign-off is scaffolding."""
    heading = chunk.get("heading") or ""
    text = clean_text(str(chunk.get("text") or ""))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    teaches = bool(prose_sentences(text, 1) or formula_steps(text))
    if len(lines) == 1 and parse_title_line(lines[0]) is not None:
        return "administrative"
    if _SCAFFOLD_HEADING.search(title_of(heading)) and not qa_pairs(text):
        return "scaffolding"
    if is_metadata_heading(heading) and not teaches:
        return "administrative"
    return None


def account_for_chunks(chunks: Sequence[dict[str, Any]], concepts: list[dict[str, Any]],
                       questions: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Make the brief account for every chunk: returns the skipped ones (``skip_reason``); every other chunk that
    no concept or question cites yet (objectives, a hook, a summary, a subtitle line) is cited by the concept
    nearest before it (the first concept for chunks before any), as a real brief would."""
    cited = {r for c in concepts for r in c["source_refs"]}
    cited |= {r for c in concepts for f in c["key_facts"] for r in f["source_refs"]}
    cited |= {r for q in questions for r in q["source_refs"]}
    order = {c["id"]: i for i, c in enumerate(chunks)}
    starts = [(min((order[r] for r in c["source_refs"] if r in order), default=len(order)), c) for c in concepts]
    skipped: list[dict[str, str]] = []
    for chunk in chunks:
        cid = chunk["id"]
        if cid in cited:
            continue
        reason = skip_reason(chunk)
        if reason:
            skipped.append({"chunk_id": cid, "reason": reason})
        elif concepts:
            before = [c for start, c in starts if start <= order[cid]]
            target = before[-1] if before else min(starts, key=lambda sc: sc[0])[1]
            target["source_refs"].append(cid)
    return skipped


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def find_acronyms(chunks: list[dict[str, Any]], limit: int = 10) -> list[str]:
    """Acronyms worth a pronunciation rule: upper-case tokens used inside normal sentences.

    Shouted headings ("BOARD DISPLAYS") are ignored, and so is any word that also appears in lower or
    title case ("BOARD" vs "board"): that is emphasis, not an acronym.
    """
    text = "\n".join(c.get("text", "") for c in chunks)
    plain_words = {w.lower() for w in re.findall(r"[A-Za-z][a-z]+", text)}
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        letters = [ch for ch in line if ch.isalpha()]
        if not letters or sum(ch.isupper() for ch in letters) / len(letters) > 0.5:
            continue
        for a in _ACRONYM.findall(line):
            if a not in _STOP_ACRONYMS and a.lower() not in plain_words:
                counts[a] += 1
    # Spelled out letter by letter only when that is how it is said: short, at most one vowel (BJT, LED,
    # PLC, CPU); words like NAND or MOSFET are pronounced as words and need no rule.
    spelled = [a for a in counts if len(a) <= 4 and sum(ch in _VOWELS for ch in a) <= 1 and (counts[a] >= 2 or
                                                                                           not set(a) & _VOWELS)]
    return sorted(spelled, key=lambda a: (-counts[a], a))[:limit]


def _brief_concepts(brief: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[tuple[dict[str, Any], SourceConcept]]:
    """Brief concepts in teaching order, each with its chunks sorted by role (from the chunk headings)."""
    raw = [c for c in brief.get("concepts") or [] if isinstance(c, dict) and (c.get("key") or c.get("name"))]
    order = [k for k in brief.get("teaching_order") or [] if isinstance(k, str)]
    raw.sort(key=lambda c: order.index(c.get("key")) if c.get("key") in order else len(order))
    out = []
    for bc in raw:
        refs = [r for r in bc.get("source_refs") or [] if r in by_id]
        refs += [r for f in bc.get("key_facts") or [] for r in (f.get("source_refs") or []) if r in by_id and r not in refs]
        # objectives, a hook, a summary or quiz section cited by the concept: the plan uses them in their own scenes
        framing = ("structural", "quiz", "hook")
        refs = [r for r in refs if section_kind(by_id[r].get("heading") or "") not in framing] or refs
        sc = SourceConcept(name=str(bc.get("name") or bc.get("key")))
        for r in refs:
            kind = section_kind(by_id[r].get("heading") or "")
            {"example": sc.examples, "analogy": sc.analogies, "application": sc.applications}.get(kind, sc.main).append(r)
        if not sc.main and refs:
            sc.main = [r for r in refs if r not in sc.examples][:2] or refs[:1]
        out.append((bc, sc))
    return out


def _bridge(prev: dict[str, Any] | None, cur: dict[str, Any], n: int) -> str:
    """One sentence that links ``cur`` to the scene before it (the plan's ``bridge_in``)."""
    if prev is None:
        return ""
    focus, pfocus = cur.get("_focus") or "the next idea", prev.get("_focus") or "what we just saw"
    typ, ptyp = cur["type"], prev["type"]
    if typ == "chapter_card":
        return f"With {mid_sentence(pfocus)} in place, we move on to {mid_sentence(focus)}."
    if typ == "quiz_checkpoint":
        return f"Before moving on, check what you now know about {mid_sentence(focus)}."
    if typ == "summary":
        return "Let us pull together everything we covered today."
    if typ == "recap":
        return "Before going further, recall what the previous session established."
    if typ == "simulation":
        return f"Now watch {mid_sentence(focus)} unfold one step at a time."
    if typ == "example":
        if cur.get("_analogy"):
            return f"An everyday situation makes {mid_sentence(focus)} easier to picture."
        return f"Let us put {mid_sentence(focus)} to work on a worked example."
    if cur.get("narrative_role") == "application":
        return f"Let us see {mid_sentence(focus)} at work in real engineering systems."
    if ptyp == "title":
        return f"Let us start with the first building block: {mid_sentence(focus)}."
    if ptyp == "quiz_checkpoint":
        return f"With that checked, we move on to {mid_sentence(focus)}."
    if ptyp == "chapter_card":
        return f"We begin this part with {mid_sentence(focus)}."
    if prev.get("concept_key") and prev.get("concept_key") == cur.get("concept_key"):
        whole = cur.get("_concept") or pfocus
        return f"To complete the picture of {mid_sentence(whole)}, we turn to {mid_sentence(focus)}."
    forms = ("With {p} in place, we turn to {c}.", "Building on {p}, we now look at {c}.",
             "From {p}, we move to {c}.", "Next, we connect {p} to {c}.")
    return forms[n % len(forms)].format(p=mid_sentence(pfocus), c=mid_sentence(focus))


def _objective(c: dict[str, Any]) -> tuple[str, str, str]:
    """(verb phrase, bloom, text) for a concept's learning objective."""
    focus = mid_sentence(c["_focus"])
    if c["_formula"]:
        return "Calculate", "apply", f"Calculate unknown quantities with {focus}"
    if re.search(r"\b(?:laws?|theorems?|postulates?|rules?|propert(?:y|ies)|principles?)\b", c["title"], re.I):
        return "Apply", "apply", f"State and apply {focus}"
    if c["_examples"] or c["_derivation"]:
        return "Use", "apply", f"Use {focus} in a worked example"
    return "Explain", "understand", f"Explain {focus}"


def _brief_questions(raw: Sequence[Any], chunks: Sequence[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(question, answer, chunk id) for the brief's ``source_questions`` ('...? Answer: ...')."""
    out: list[tuple[str, str, str]] = []
    for item in raw:
        text = str(item.get("text") or "") if isinstance(item, dict) else str(item)  # a GenBrief question or a string
        q, a = split_question(text) or (text.strip(), "")
        cid = next((c["id"] for c in chunks if q.split("?")[0][:40] in str(c.get("text") or "")), "")
        out.append((q, a, cid))
    return out


def _spread(items: Sequence[Any], n: int) -> list[Any]:
    """Up to ``n`` items spread evenly from first to last (a summary recalls the whole arc)."""
    if len(items) <= n:
        return list(items)
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def _join(names: Sequence[str]) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _question_score(question: str, concept: dict[str, Any]) -> int:
    words = {w for w in re.findall(r"[a-z]{4,}", question.lower())} - {"what", "which", "when", "does", "equal", "with"}
    text = (concept["title"] + " " + concept["_text"]).lower()
    score = sum(2 if w in concept["title"].lower() else 1 for w in words if w in text)
    for frag in re.findall(r"[A-Z0-9][^?]*?[=+·×][^?]*?(?=\s+(?:equal|equals)\b|\?|$)", question):
        frag = re.sub(r"\s+", " ", frag.replace("What is", "")).strip()
        if len(frag) >= 3 and frag in concept["_text"]:
            score += 4
    return score


def plan_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    req = extract_json(prompt, "Request") or {}
    chunks = [c for c in (extract_json(prompt, "Source chunks") or []) if isinstance(c, dict) and c.get("id")]
    brief = extract_json(prompt, "Concepts to teach") or {}
    templates = {t["name"]: t for t in (extract_json(prompt, "Animation templates") or [])}
    figures = extract_json(prompt, "Source figures") or []
    notes = extract_json(prompt, "Author's visual suggestions") or []
    allowed = set(req.get("allowed_scene_types") or ["title", "content", "example", "summary", "chapter_card", "recap"])
    panels = set(req.get("allowed_side_panels") or [])
    every = max(1, int(req.get("quiz_every_n_concepts") or 2))
    target = int(req.get("target_seconds") or 900)
    quizzes = bool(req.get("include_quizzes", True)) and "quiz_checkpoint" in allowed
    by_id = {c["id"]: c for c in chunks}
    summary_sentences = [x for c in chunks if _SUMMARY_HEADING.search(title_of(c.get("heading") or ""))
                         for x in prose_sentences(str(c.get("text") or ""), 8)]
    source = "\n".join(str(c.get("text") or "") for c in chunks)
    max_concepts = max(2, min(8, round(target / 75)))  # about one core concept per 75 s of lecture

    pairs = _brief_concepts(brief, by_id) if brief.get("concepts") else []
    if not pairs:
        pairs = [({}, sc) for sc in source_concepts(chunks, max_concepts)]
    if not pairs:
        pairs = [({"name": "Overview"}, SourceConcept(name="Overview", main=[c["id"] for c in chunks[:2]]))]
    while len(pairs) > max_concepts:  # fold the tail into the last concept that fits the time
        (bc, sc), (_, extra) = pairs[-2], pairs[-1]
        sc.main += extra.main
        sc.examples += extra.examples
        sc.analogies += extra.analogies
        sc.applications += extra.applications
        pairs = pairs[:-2] + [(bc, sc)]

    used: set[str] = set()
    concepts: list[dict[str, Any]] = []
    for i, (bc, sc) in enumerate(pairs):
        title = natural_case(str(bc.get("name") or sc.name), source)
        key = key_of(str(bc.get("key") or title), f"concept_{i + 1}")
        while key in used:
            key += "_x"
        used.add(key)
        text = _text_of(sc.main, by_id)
        sentences_ = prose_sentences(text, 8)
        facts = [str(f.get("text") or "") for f in bc.get("key_facts") or [] if isinstance(f, dict)]
        takeaway = _takeaway(title, facts + sentences_, summary_sentences)
        summary = (str(bc.get("why_it_matters") or "") or takeaway
                   or (sentences_[0] if sentences_ else title))
        worked = [cid for cid in sc.examples if len(formula_steps(_text_of([cid], by_id))) >= 2]
        derive = next((cid for cid in sc.examples + sc.main if derivation(_text_of([cid], by_id))), None)
        known = {c["key"] for c in concepts}
        deps = [key_of(d, d) for d in bc.get("prerequisites") or [] if isinstance(d, str)]
        analogies = sorted(sc.analogies, key=lambda r: -len(prose_sentences(_text_of([r], by_id))))
        concepts.append({
            "key": key, "title": title, "summary": shorten(summary, 200), "kind": "core",
            "depends_on": [d for d in deps if d in known] or ([concepts[-1]["key"]] if concepts and not bc else []),
            "_focus": focus_case(title, source), "_main": list(dict.fromkeys(sc.context + sc.main)),
            "_examples": worked or sc.examples, "_analogies": analogies,
            "_applications": sc.applications, "_text": _text_of(sc.refs, by_id) + "\n" + "\n".join(facts),
            "_formula": find_formula(text), "_derivation": derive, "_points": [str(p) for p in bc.get("must_explain") or []],
            "_takeaway": takeaway or summary,
        })
    objectives, miscs = [], []
    for c in concepts:
        _, bloom, text = _objective(c)
        objectives.append({"key": f"obj_{c['key']}"[:40], "text": text, "bloom": bloom, "concept_keys": [c["key"]]})
    for i, c in enumerate(concepts[:4]):
        statement, correction = misconception_for(c, concepts[i + 1] if i == 0 and len(concepts) > 1 else None)
        miscs.append({"key": f"mis_{c['key']}"[:40], "concept_key": c["key"], "statement": statement, "correction": correction})
    glossary = [{"written": a, "spoken": " ".join(a), "keep_in_english": True} for a in find_acronyms(chunks)]
    top = title_of((chunks[0].get("heading") or "").split(">")[0]) if chunks else ""
    session_title = (req.get("session_title") or brief.get("topic") or (top if section_kind(top) == "content" else "")
                     or (concepts[0]["title"] if concepts else "Today's lecture"))
    sim_template = "equation_steps" if "equation_steps" in templates and "simulation" in allowed else None
    sim_concept = next((c for c in concepts if c["_formula"]), None) or next((c for c in concepts if c["_derivation"]), None)
    if sim_template is None:
        sim_concept = None
    hook = [c["id"] for c in chunks if section_kind(c.get("heading") or "") == "hook"]
    questions = (_brief_questions(brief["source_questions"], chunks) if brief.get("source_questions")
                 else source_questions(chunks))
    note_ids = [(n.get("id"), n.get("near_chunk")) for n in notes if isinstance(n, dict) and n.get("id")]

    def scene(key: str, typ: str, goal: str, role: str, **kw: Any) -> dict[str, Any]:
        refs = kw.get("source_refs") or []
        visual = [i for i, near in note_ids if near and near in refs][:2] if typ not in ("quiz_checkpoint", "chapter_card",
                                                                                        "summary") else []
        return {"key": key, "type": typ, "goal": goal, "narrative_role": role, "est_seconds": WEIGHTS.get(typ, 45),
                "visual_note_ids": visual, **kw}

    def figure_panel(refs: list[str]) -> dict[str, Any]:
        fig = next((f for f in figures if any(f"Figure {f['id']}" in str(by_id.get(r, {}).get("text") or "")
                                              for r in refs)), None)
        if fig is not None and "figure" in panels:
            return {"side_panel_kind": "figure",
                    "visual_rationale": "The source diagram shows the arrangement the narration describes."}
        return {}

    assigned: dict[int, list[tuple[str, str, str]]] = {}
    for q, a, cid in questions:
        scores = [(_question_score(q + " " + a, c), -i) for i, c in enumerate(concepts)]
        best, neg = max(scores) if scores else (0, 0)
        if best > 0:
            assigned.setdefault(-neg, []).append((q, a, cid))

    chapters = []
    parts = [concepts[i:i + every] for i in range(0, len(concepts), every)]
    examples_left = MAX_EXAMPLE_SCENES
    for ci, part in enumerate(parts):
        ch_key = f"part_{ci + 1}"
        ch_title = part[0]["title"] if len(part) == 1 else f"{part[0]['title']} and {mid_sentence(part[-1]['title'])}"
        scenes: list[dict[str, Any]] = []
        if ci == 0:
            scenes.append(scene("cold_open", "title", f"Hook the learner with why {mid_sentence(session_title)} matters, "
                                                      "then introduce the session and its objectives.", "hook",
                                concept_key=part[0]["key"], key_points=[session_title],
                                source_refs=hook[:2] or part[0]["_main"][:1], _focus=session_title))
            if req.get("has_previous_session_summary") and "recap" in allowed:
                scenes.append(scene("recap_previous", "recap", "Recap the previous session and connect it to today.",
                                    "context", _focus="the previous session"))
        else:
            scenes.append(scene(f"{ch_key}_card", "chapter_card", f"Open part {ci + 1}: {ch_title}.", "transition",
                                key_points=[ch_title], _focus=ch_title))
        for c in part:
            main = c["_main"] or c["_examples"][:1]
            asides = [r for r in main if section_kind(by_id.get(r, {}).get("heading") or "") == "aside"]
            core = [r for r in main if r not in asides] or main
            halves = [core] if len(core) <= 2 else [core[: (len(core) + 1) // 2], core[(len(core) + 1) // 2:]]
            halves[-1] = halves[-1] + [r for r in asides if r not in halves[-1]]  # side remarks join the last part
            for hi, refs in enumerate(halves):
                names = [natural_case(concept_name(by_id[r].get("heading") or ""), source) for r in refs if r in by_id
                         and section_kind(by_id[r].get("heading") or "") not in ("intro", "aside")]
                names = list(dict.fromkeys(n for n in names if n))
                focus = c["_focus"] if hi == 0 else (focus_case(_join(names[:3]), source) or c["_focus"])
                points = (c["_points"][:3] if len(halves) == 1 else [p for p in c["_points"] if
                                                                     any(n.lower() in p.lower() for n in names)][:3])
                points = points or [shorten(s, 120) for s in prose_sentences(_text_of(refs, by_id), 3)]
                scenes.append(scene(f"{c['key']}_intro" if hi == 0 else f"{c['key']}_more", "content",
                                    f"Explain {mid_sentence(focus)}: start from a concrete situation, then give the "
                                    "precise statement from the source.", "concept",
                                    concept_key=c["key"], objective_keys=[f"obj_{c['key']}"[:40]], key_points=points,
                                    source_refs=refs[:4], _focus=focus, _concept=c["_focus"],
                                    misconception_keys=[m["key"] for m in miscs if m["concept_key"] == c["key"]] if hi == 0
                                    else [], **figure_panel(refs)))
            if sim_concept is c:  # animate the derivation before learners practise it
                refs = [c["_derivation"]] if c["_derivation"] and not c["_formula"] else c["_main"][:2]
                goal = (f"Animate how {formula_spoken(c['_formula'])} is rearranged and used, step by step."
                        if c["_formula"] else f"Animate the source's derivation for {mid_sentence(c['_focus'])}, one line per step.")
                scenes.append(scene(f"{c['key']}_animation", "simulation", goal, "concept", concept_key=c["key"],
                                    manim_template=sim_template, source_refs=refs, _focus=c["_focus"],
                                    visual_rationale="Seeing each line of the working appear in turn makes it easier to follow."))
            if "example" in allowed and examples_left > 0:
                worked = [r for r in c["_examples"] if r != c["_derivation"] or sim_concept is not c] or c["_examples"]
                if c["_formula"] or worked:
                    examples_left -= 1
                    refs = worked[:1] or c["_main"][:2]
                    scenes.append(scene(f"{c['key']}_example", "example",
                                        f"Work through the source's example of {mid_sentence(c['_focus'])} step by step, "
                                        "fading the last step.", "example", concept_key=c["key"],
                                        objective_keys=[f"obj_{c['key']}"[:40]], source_refs=refs, _focus=c["_focus"],
                                        key_points=[formula_spoken(c["_formula"])] if c["_formula"] else
                                        [e for e, _ in formula_steps(_text_of(refs, by_id))][:4]))
                elif c["_analogies"]:
                    examples_left -= 1
                    refs = c["_analogies"][:1]  # the richest everyday situation
                    scenes.append(scene(f"{c['key']}_example", "example",
                                        f"Use the source's everyday situation to make {mid_sentence(c['_focus'])} concrete.",
                                        "example", concept_key=c["key"], objective_keys=[f"obj_{c['key']}"[:40]],
                                        source_refs=refs, _focus=c["_focus"], _analogy=True,
                                        key_points=[shorten(s, 120) for s in prose_sentences(_text_of(refs, by_id), 3)]))
            if c["_applications"]:
                refs = c["_applications"][:2]
                scenes.append(scene(f"{c['key']}_applications", "content",
                                    f"Show where {mid_sentence(c['_focus'])} is used in real engineering systems.",
                                    "application", concept_key=c["key"], source_refs=refs, _focus=c["_focus"],
                                    key_points=[shorten(s, 120) for s in prose_sentences(_text_of(refs, by_id), 4)]))
        if quizzes:
            asked = [x for i, c in enumerate(concepts) if c in part for x in assigned.get(i, [])][:2]
            names = _join([mid_sentence(c["_focus"]) for c in part])
            scenes.append(scene(f"check_{part[-1]['key']}"[:40], "quiz_checkpoint", f"Retrieval check: {names}.", "check",
                                concept_key=part[-1]["key"], objective_keys=[f"obj_{c['key']}"[:40] for c in part],
                                misconception_keys=[m["key"] for m in miscs if m["concept_key"] in {c["key"] for c in part}],
                                key_points=[f"{q} Answer: {a}" if a else q for q, a, _ in asked],
                                source_refs=list(dict.fromkeys([x for c in part for x in c["_main"][:2]]
                                                               + [cid for _, _, cid in asked if cid])),
                                _focus=names))
        chapters.append({"key": ch_key, "title": ch_title, "concept_keys": [c["key"] for c in part], "scenes": scenes})
    chapters[-1]["scenes"].append(scene("summary", "summary", "Summarise the key ideas and look ahead.", "synthesis",
                                        key_points=[f"{c['title']}: {c['_takeaway']}" for c in _spread(concepts, 5)],
                                        _focus="today's ideas"))
    all_scenes = [s for ch in chapters for s in ch["scenes"]]
    for n, s in enumerate(all_scenes):
        s["bridge_in"] = _bridge(all_scenes[n - 1] if n else None, s, n)
    flexible = [s for s in all_scenes if s["type"] != "chapter_card"]
    fixed = sum(s["est_seconds"] for s in all_scenes if s["type"] == "chapter_card")
    factor = max(0.2, (target - fixed) / max(1, sum(s["est_seconds"] for s in flexible)))
    for s in flexible:
        s["est_seconds"] = int(min(240, max(20, round(s["est_seconds"] * factor))))
    for s in all_scenes:
        for k in [k for k in s if k.startswith("_")]:
            s.pop(k)
    for c in concepts:
        for k in [k for k in c if k.startswith("_")]:
            c.pop(k)
    return {
        "subject_name": req.get("subject_name") or "", "unit_name": req.get("unit_name") or "",
        "session_number": req.get("session_number") or "Session 1", "session_title": session_title,
        "learning_objectives": objectives, "concept_map": concepts, "misconceptions": miscs,
        "glossary_terms": glossary, "chapters": chapters,
        "notes": "Demo plan generated offline from the concept brief." if brief.get("concepts")
        else "Demo plan generated offline from the source headings.",
    }


# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------


def _scene_inputs(prompt: str) -> dict[str, Any]:
    this = extract_json(prompt, "This scene") or {}
    src = extract_json(prompt, "Relevant source") or []
    if isinstance(src, dict):  # {"note": ..., "chunks": [...]}
        src = src.get("chunks") or []
    lecture = extract_json(prompt, "Lecture") or {}
    concept = this.get("concept") or {}
    topic = concept.get("title") or lecture.get("session_title") or "this idea"
    cited = [c for c in src if c.get("id") in set(this.get("source_refs") or [])]
    text = "\n".join(c.get("text", "") for c in (cited or src))
    all_text = "\n".join(c.get("text", "") for c in src)
    nouns = subject_nouns_in(" ".join([lecture.get("session_title") or "", topic]), all_text)
    return {
        "this": this, "src": src, "cited": cited or src, "lecture": lecture, "topic": topic, "text": text,
        "sents": (prose_sentences(text, 10, nouns) + table_sentences(text))[:10]
        or [f"{topic} is an important idea in this course."],
        "nouns": nouns,
        "refs": [c["id"] for c in (cited or src)][:2],
        "figures": extract_json(prompt, "Figures") or [],
        "miscs": extract_json(prompt, "Misconceptions to target") or [],
        "other_miscs": extract_json(prompt, "Other known misconceptions") or [],
        "template": extract_json(prompt, "Animation template"),
        "next": (extract_json(prompt, "Neighbouring scenes") or {}).get("next") or {},
        "current": extract_json(prompt, "Current scene") or {},  # a rewrite keeps what already works
    }


def _beat(narration: str, board: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return {"narration": limit_words(speakable(narration)), "board": board, **kw}


def _plain_words(text: str) -> str:
    return " ".join(re.findall(r"\w+", clean_text(text).lower()))


def _refs_for(sentence: str, inp: dict[str, Any]) -> list[str]:
    """[the cited chunk that holds ``sentence`` (its first words)], else the scene's first cited chunk."""
    head = " ".join(_plain_words(sentence).split()[:6])
    for c in inp["cited"]:
        if head and head in _plain_words(str(c.get("text") or "")):
            return [str(c["id"])]
    return inp["refs"][:1]


def _definition(sentence: str) -> dict[str, Any] | None:
    """A definition board item for 'A postulate is a statement …' / 'X means …' (not 'These laws are used …')."""
    m = re.match(r"^(?:An?\s+|The\s+)?([A-Za-z][\w\s'’-]{1,40}?)\s+(?:(?:is|are)\s+(?=an?\s|the\s|called\s|defined\s)"
                 r"|refers to\s+|means\s+)(.+)$", board_safe(sentence))
    if not m or m.group(1).split()[0].lower() in ("this", "these", "that", "those", "it", "they", "there", "here"):
        return None
    return {"kind": "definition", "term": m.group(1).strip(), "text": shorten(m.group(2), 110)}


def _side_panel(inp: dict[str, Any], n_beats: int = 0) -> dict[str, Any] | None:
    kind = inp["this"].get("side_panel_kind")
    rationale = inp["this"].get("visual_rationale") or "It shows what the narration describes."
    if kind == "figure" and inp["figures"]:
        return {"kind": "figure", "figure_id": inp["figures"][0]["id"], "rationale": rationale,
                "title": shorten(inp["figures"][0].get("caption") or inp["topic"], 60), "show_from_beat": 2}
    if kind == "graph":
        return {"kind": "graph", "rationale": rationale, "graph_functions": [{"expr": "x^2", "label": "y = x^2"}],
                "graph_x_min": -3, "graph_x_max": 3}
    if kind == "skill_tree":
        return {"kind": "skill_tree"}
    if kind == "image":
        return {"kind": "image", "rationale": rationale, "image_prompt": f"A realistic photo illustrating {inp['topic'].lower()}"}
    if kind == "chart":
        return {"kind": "chart", "rationale": rationale, "chart_labels": ["A", "B", "C"],
                "chart_datasets": [{"label": inp["topic"][:40], "data": [3, 5, 2]}]}
    if kind == "terminal":
        return {"kind": "terminal", "rationale": rationale, "terminal_command": "python demo.py", "terminal_output": "done"}
    if kind == "quiz":
        return {"kind": "quiz", "rationale": rationale, "quiz_question": f"Is {inp['topic']} useful?",
                "quiz_options": ["Yes", "No"], "quiz_correct_index": 0}
    if kind == "model_3d":
        return {"kind": "model_3d", "rationale": rationale, "model_primitives": [{"shape": "sphere", "size": [1.0]}]}
    if kind == "gif":
        return {"kind": "gif", "rationale": rationale, "gif_query": inp["topic"][:40]}
    if kind == "manim" and inp["template"]:
        params = inp["template"].get("example_params") or {}
        steps = integrations.template_step_count(inp["template"]["name"], params) or 1
        show_from = max(1, n_beats - steps + 1)  # one animation step per remaining beat
        return {"kind": "manim", "rationale": rationale, "manim_params": params, "show_from_beat": show_from}
    return None


def _scene_title(inp: dict[str, Any]) -> str:
    """The scene's own focus: the sections it cites when they are a part of the concept (the concept itself when
    the scene opens it with the concept's introduction)."""
    source = inp["text"]
    kinds = [section_kind(c.get("heading") or "") for c in inp["cited"]]
    topic = inp["topic"]
    if "intro" in kinds:
        return topic
    names = [natural_case(concept_name(c.get("heading") or ""), source) for c, k in zip(inp["cited"], kinds, strict=True)
             if k not in ("structural", "quiz", "hook", "aside")]
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) >= 2:
        return _join(names[:3])
    if len(names) == 1 and names[0].lower() not in topic.lower() and topic.lower() not in names[0].lower():
        return names[0]
    return topic


def _goals(objectives: Sequence[str]) -> str:
    """'explain X, explain Y and apply Z' -> 'explain X and Y, and apply Z' (verbs said once); a compound verb
    counts as one: 'state and apply X', 'state and apply Y' -> 'state and apply X and Y'."""
    groups: list[tuple[str, list[str]]] = []
    for text in objectives:
        m = re.match(r"^(\w+\s+and\s+\w+|\w+)(?:\s+(.*))?$", text.strip())
        verb, rest = (m.group(1), m.group(2) or "") if m else (text, "")
        verb = verb.lower()
        if groups and groups[-1][0] == verb and rest:
            groups[-1][1].append(rest)
        else:
            groups.append((verb, [rest] if rest else []))
    phrases = [f"{verb} {_join(things)}".strip() for verb, things in groups]
    return phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + ", and " + phrases[-1]


def _title_scene(inp: dict[str, Any]) -> list[dict[str, Any]]:
    """Cold open: the source's hook (else a question), today's topic, then the objectives, stated once."""
    lecture, topic = inp["lecture"], inp["topic"]
    session = lecture.get("session_title") or topic
    hook_chunks = [c for c in inp["cited"] if section_kind(c.get("heading") or "") == "hook"]
    hook = prose_sentences("\n".join(c.get("text", "") for c in hook_chunks), 8, inp["nouns"]) if hook_chunks else []
    asks = [i for i, s in enumerate(hook) if s.endswith("?")]
    question = hook[asks[0]] if asks else ""
    opening = [s for i, s in enumerate(hook) if not s.endswith("?") and (not asks or i < asks[0])]
    after = next((s for i, s in enumerate(hook) if asks and i > asks[0] and not s.endswith("?")), "")
    beats: list[dict[str, Any]] = []
    heading: dict[str, Any] | None = {"kind": "heading", "text": shorten(session, 60)}
    if opening:
        beats.append(_beat(opening[0], heading))
        heading = None
        if len(opening) > 1 and not question:
            beats.append(_beat(opening[1]))
    subject = mid_sentence(session if len(session) <= 60 else topic)
    q = question or f"Here is a question to start with: where do you meet {subject} in everyday life?"
    beats.append(_beat(q, heading or {"kind": "callout_info", "text": shorten(question or f"Where do you meet "
                                                                                       f"{subject}?", 100)},
                       pause_after=1.5))
    if after:
        beats.append(_beat(after))
    beats.append(_beat(f"Today's session is all about {session}.", {"kind": "callout_info",
                                                                     "text": f"Today: {shorten(session, 70)}"}))
    texts = [o["text"] for o in lecture.get("objectives") or [] if o.get("text")]
    picked = [texts[i] for i in dict.fromkeys((0, len(texts) // 2, len(texts) - 1))] if texts else []
    if picked:  # the first, a middle and the last objective: the arc of the session
        goals = _goals([t[0].lower() + t[1:] for t in picked])
        beats.append(_beat(f"By the end of this session, you will be able to {goals}.", None))
    else:
        beats.append(_beat("By the end of this session, you will be able to explain the key ideas and use them."))
    return beats


_SYMBOL_LINES = ("Look at the board: these lines say the same thing in symbols.",
                 "Here is the same idea in symbols on the board.",
                 "On the board, read the rule in symbols, exactly as the source writes it.")


def _content(inp: dict[str, Any], title: str) -> list[dict[str, Any]]:
    """Bridge in, the source's own sentences (board: definitions and key phrases), the rule in symbols, the
    misconception to confront, then a line that sets up the next scene."""
    this = inp["this"]
    sents = inp["sents"]
    main_ref = _refs_for(sents[0], inp) if sents else inp["refs"][:1]  # the chunk that teaches, not its lead-in
    kept = next((b.get("narration") for b in inp["current"].get("beats") or [] if b.get("narration")), "")
    bridge = (this.get("bridge_in") or "").strip() or kept or f"Let's look at {mid_sentence(title)}."
    beats = [_beat(bridge, {"kind": "heading", "text": shorten(title, 60)}, source_refs=main_ref)]
    formula = find_formula(inp["text"])
    lines = [tex for expr, _ in formula_steps(inp["text"]) if (tex := latex_of(expr)) and re.search(r"[A-Za-z]", expr)]
    has_formula = bool(formula or lines)
    has_misc = bool(inp["miscs"])
    on_board = max(1, 3 - has_formula - has_misc)  # at most five board items in all
    for n, s in enumerate(sents[:5]):
        item = (_definition(s) or {"kind": "bullet", "text": key_phrase(s)}) if n < on_board else None
        beats.append(_beat(s, item, source_refs=_refs_for(s, inp),
                           pause_after=1.0 if item is not None and item["kind"] == "definition" else 0.0))
    if formula:
        beats.append(_beat(f"In symbols, {formula_spoken(formula)}.",
                           {"kind": "formula", "latex": formula_latex(formula), "text": f"{shorten(title, 40)} in symbols",
                            "variables": formula_variables(formula, inp["text"])},
                           pause_after=1.0, source_refs=main_ref))
    elif lines:
        beats.append(_beat(_SYMBOL_LINES[len(title) % len(_SYMBOL_LINES)],
                           {"kind": "formula", "latex": " \\qquad ".join(lines[:2]), "text": f"{shorten(title, 40)} in symbols"},
                           pause_after=1.0, source_refs=main_ref))
    if inp["miscs"]:
        m = inp["miscs"][0]
        belief = mid_sentence(focus_case(m["statement"].rstrip("."), inp["text"]))
        beats.append(_beat(f"Careful: many students believe that {belief}.",
                           {"kind": "misconception", "text": shorten(m["statement"], 120),
                            "justification": shorten(m["correction"], 160), "misconception_key": m["key"]}))
        beats.append(_beat(m["correction"]))
    nxt = inp["next"].get("type")
    closing = {"example": "Next, let us see it at work in an example.",
               "quiz_checkpoint": "Hold on to this, because a quick check comes next.",
               "simulation": "Next, watch it unfold step by step.",
               "summary": "That completes the last idea of today's session."}.get(
        nxt or "", "Keep this in mind, because the next idea builds on it.")
    beats.append(_beat(closing, highlight_steps=[2] if len([b for b in beats if b["board"]]) >= 2 else []))
    if this.get("side_panel_kind") == "figure" and len(beats) > 1:
        beats[1]["narration"] = limit_words("Look at the figure on the right. " + beats[1]["narration"])
    return beats


def _worked_steps(inp: dict[str, Any]) -> list[dict[str, Any]] | None:
    """A worked example from the source's formula lines, the last step faded (None: no such example)."""
    steps = [(expr, lead) for expr, lead in formula_steps(inp["text"]) if latex_of(expr)][:5]
    if len(steps) < 2:
        return None
    first = latex_of(steps[0][0])
    sents = inp["sents"]
    problem = next((s for s in sents if steps[0][0] in s), "")
    # the source's setup ("Consider a node where 5 A and 3 A enter ...") comes before its first formula line
    cut = inp["text"].find(steps[0][0])
    before = sents[:sents.index(problem)] if problem else [
        s for s in sents if 0 <= inp["text"].find(s[:25].rstrip(".")) < cut]
    setup = [s for s in before if "=" not in s and not s.endswith("?")][-2:]
    beats = [_beat(inp["this"].get("bridge_in") or "Let's work through an example from the source.",
                   {"kind": "callout_info", "text": f"Example: ${first}$"}, source_refs=inp["refs"][:1])]
    for s in setup:
        beats.append(_beat(s, source_refs=_refs_for(s, inp)))
    if problem:
        beats.append(_beat(problem, source_refs=_refs_for(problem, inp)))
    for i, (expr, lead) in enumerate(steps[1:], 2):
        why = lead or "from the previous line"
        if why.lower() in ("since", "because", "as"):
            why = "a rule we already know"
        elif why.lower() in ("therefore", "hence", "thus", "so"):
            why = "therefore"
        item = {"kind": "example_step", "text": f"${latex_of(expr)}$", "justification": shorten(why, 80)}
        if i == len(steps) and len(steps) >= 3:
            item["blank"] = True
            beats.append(_beat("Now try the last step yourself. Pause here and work it out.", item, pause_after=3.0))
            beats.append(_beat(f"The result is {speakable(expr)}.", fill_previous_blank=True))
        else:
            said = lead.rstrip(".") + ", we get" if lead else "This gives"
            beats.append(_beat(f"{said} {speakable(expr)}.", item))
    takeaway = next((s for s in reversed(inp["sents"]) if "=" not in s and s != problem and s not in setup), "")
    beats.append(_beat(takeaway or "Always check that the result is simpler and still means the same thing.",
                       {"kind": "takeaway", "text": key_phrase(takeaway) if takeaway else "Check every step"},
                       highlight_steps=[2]))
    return beats


_TRANSITIONAL = re.compile(r"^(?:let'?s|let\s+us|now\s+(?:consider|let)|consider\s+another|here\s+is)\b", re.I)


def _situation(inp: dict[str, Any]) -> list[dict[str, Any]]:
    """An example built from the source's everyday situation: its sentences as steps, the next-to-last one faded,
    and its conclusion as the takeaway."""
    topic = inp["topic"]
    sents = [s for s in inp["sents"] if not _TRANSITIONAL.search(s)] or inp["sents"]
    conclusion = sents[-1] if len(sents) >= 4 else ""
    steps = [s for s in sents if s != conclusion][:3]
    opening = inp["this"].get("bridge_in") or f"Let's apply {mid_sentence(topic)} to an example."
    beats = [_beat(opening, {"kind": "callout_info", "text": f"Example: {shorten(topic, 60)}"})]
    for i, s in enumerate(steps):
        item = {"kind": "example_step", "text": shorten(s, 100), "justification": "from the source"}
        if i == len(steps) - 1 and len(steps) >= 2:
            item.update(blank=True, justification="apply the idea")
            beats.append(_beat("What follows from this? Pause and think about the next step.", item, pause_after=3.0))
            beats.append(_beat(s, fill_previous_blank=True))
        else:
            beats.append(_beat(s, item))
    if len(steps) < 2:  # a worked example needs two steps
        beats.append(_beat("Pause and say in your own words what this shows.",
                           {"kind": "example_step", "text": "Say it in your own words", "justification": "check yourself"},
                           pause_after=2.0))
    if conclusion:
        beats.append(_beat(conclusion, {"kind": "takeaway", "text": key_phrase(conclusion)}))
    return beats


def _example(inp: dict[str, Any]) -> list[dict[str, Any]]:
    f = find_formula(inp["text"])
    if f:
        lhs, a, op, b = f
        va, vb = (12, 4) if op == "/" else (2, 5)
        res = va / vb if op == "/" else va * vb
        res_s = f"{res:g}"
        word = "divided by" if op == "/" else "times"
        sym = "\\div" if op == "/" else "\\times"
        return [
            _beat(f"Let's try an example. Suppose {a} is {va} and {b} is {vb}. What is {lhs}?",
                  {"kind": "callout_info", "text": f"Given: ${a} = {va}$, ${b} = {vb}$. Find ${lhs}$."}),
            _beat(f"Start from the relation: {formula_spoken(f)}.",
                  {"kind": "example_step", "text": f"${formula_latex(f)}$", "justification": "the relation we just learned"}),
            _beat(f"Now substitute the values yourself. Pause here and work out {lhs}.",
                  {"kind": "example_step", "text": f"${lhs} = {va} {sym} {vb} = {res_s}$", "blank": True,
                   "justification": "substitute the given values"}, pause_after=3.0),
            _beat(f"{va} {word} {vb} gives {res_s}, so {lhs} is {res_s}.", fill_previous_blank=True),
            _beat("Always check that the answer is sensible before you move on.",
                  {"kind": "takeaway", "text": "Substitute, compute, then sanity-check the result"}, highlight_steps=[2]),
        ]
    return _worked_steps(inp) or _situation(inp)


def board_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    inp = _scene_inputs(prompt)
    typ = inp["this"].get("type", "content")
    topic = inp["topic"]
    title = shorten(topic, 50)
    if typ == "title":
        beats = _title_scene(inp)
        title = shorten(inp["lecture"].get("session_title") or topic, 60)
    elif typ == "recap":
        points = inp["this"].get("key_points") or ["We built the foundations last time."]
        beats = [_beat(f"Last time, we saw this: {p}", {"kind": "takeaway", "text": shorten(p, 90)}) for p in points[:3]]
        beats.append(_beat("Today we build directly on that."))
        title = "Quick recap"
    elif typ == "example":
        beats = _example(inp)
        worked = bool(find_formula(inp["text"])) or any(b["board"] and "$" in b["board"].get("text", "") for b in beats)
        title = f"{'Worked example' if worked else 'Example'}: {shorten(topic, 40)}"
    elif typ in ("summary", "key_takeaway"):
        points = inp["this"].get("key_points") or [topic]
        beats = [_beat(f"Remember this. {p.rstrip('.')}.", {"kind": "takeaway", "text": shorten(p.split(':')[0], 90)})
                 for p in points[:5]]
        beats.append(_beat("That's the big picture. Try the practice problems on your companion sheet next."))
        title = "Key takeaways"
    elif inp["this"].get("narrative_role") == "application":
        title = shorten(f"{topic} in practice", 50)
        beats = _content(inp, title)
    else:
        title = shorten(_scene_title(inp), 50)
        beats = _content(inp, title)
    return {"title": title, "subtitle": None, "beats": beats, "side_panel": _side_panel(inp, len(beats))}


def chapter_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    this = extract_json(prompt, "This scene") or {}
    title = title_of(re.sub(r"^\d+\.\s*", "", this.get("chapter") or "")) or "Next part"
    narration = (this.get("bridge_in") or "").strip() or f"Up next: {title}."
    return {"title": shorten(title, 60), "chapter_label": this.get("chapter_label") or "Part 2",
            "beats": [{"narration": limit_words(speakable(narration), 30)}]}


def _source_question(inp: dict[str, Any]) -> tuple[str, str] | None:
    for point in inp["this"].get("key_points") or []:
        found = split_question(str(point))
        if found and found[1]:
            return found
    return None


def _about(sentences_: Sequence[str], topic: str) -> str | None:
    """The sentence that says most about ``topic`` (by its content words)."""
    words = {w for w in re.findall(r"[a-z]{4,}", topic.lower())} - {"with", "from", "that", "this"}
    scored = [(sum(w in s.lower() for w in words), -i, s) for i, s in enumerate(sentences_) if "?" not in s]
    best = max(scored, default=(0, 0, None))
    return best[2] if best[0] > 0 else None


def _checked_concept(question: str, inp: dict[str, Any]) -> str:
    """The lecture concept a question checks (most of its title words in the question), else the scene's topic."""
    words = set(re.findall(r"[a-z’']{4,}", question.lower()))
    best, score = inp["topic"], 0
    for c in inp["lecture"].get("concepts") or []:
        title = str(c.get("title") or "")
        hits = sum(w in words for w in re.findall(r"[a-z’']{4,}", title.lower()))
        if hits > score:
            best, score = title, hits
    return best


# a wrong answer of the same kind as a short correct one ("Electric charge" -> "Electric energy")
_CONTRASTS = {"charge": "energy", "energy": "charge", "current": "voltage", "voltage": "current", "zero": "one",
              "one": "zero", "series": "parallel", "parallel": "series", "increases": "decreases", "decreases": "increases",
              "true": "false", "false": "true", "and": "or", "or": "and", "power": "energy", "resistance": "current",
              "stress": "strain", "strain": "stress", "1": "0", "0": "1"}


def _contrast(answer: str) -> str:
    words = answer.rstrip(".").split()
    for i, w in enumerate(words):
        other = _CONTRASTS.get(w.lower())
        if other:
            swapped = other.capitalize() if w[:1].isupper() else other
            return " ".join([*words[:i], swapped, *words[i + 1:]])
    return ""


def quiz_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    inp = _scene_inputs(prompt)
    asked = _source_question(inp)
    topic = _checked_concept(asked[0], inp) if asked else inp["topic"]  # titled after what the question checks
    distractors: list[dict[str, Any]] = []
    pairs = qa_pairs("\n".join(c.get("text", "") for c in inp["src"]))
    if asked:  # the source's own question: its answer is correct, other answers from the source are distractors
        question, correct = asked[0], shorten(asked[1], 110)
        wh = question.split()[0].lower()
        others = [a.rstrip(".") for q, a in pairs if a and q != question]
        others.sort(key=lambda a: (next((q for q, x in pairs if x.rstrip(".") == a), "").split()[:1] != [wh.title()],
                                   abs(len(a) - len(correct))))
    else:
        question = f"Which statement about {mid_sentence(topic)} is correct?"
        correct = shorten(_about(inp["sents"], topic) or inp["sents"][0], 110)
        others = []
    seen = {correct.lower()}

    def add(text: str, why: str, key: str | None) -> None:
        text = shorten(text, 110)
        if text and text.lower() not in seen and len(distractors) < 3:
            seen.add(text.lower())
            distractors.append({"text": text, "why_wrong": shorten(why, 200), "misconception_key": key})

    for other in others:
        add(other, f"That answers a different question. The correct answer is {correct}.", None)
    if asked and (cand := _contrast(correct)):  # a wrong answer of the same kind reads better than a statement
        add(cand, f"Close, but check the idea again: the correct answer is {correct}.", None)
    if not asked:
        for m in inp["miscs"]:  # this quiz's own misconceptions first
            add(m["statement"], m["correction"], m["key"])
    generic = [
        (f"Only textbook examples need {mid_sentence(topic)}; real circuits never do",
         f"Real systems rely on {mid_sentence(topic)} too, within its limits."),
        (f"You can skip {mid_sentence(topic)} once you know the formulas",
         f"The formulas only make sense once {mid_sentence(topic)} is understood."),
    ]
    for text, why in generic:
        if len(distractors) < 2:
            add(text, why, None)
    for m in inp["other_miscs"]:
        if len(distractors) < 2:
            add(m["statement"], m["correction"], m["key"])
    tempting = distractors[0] if distractors else None
    if distractors and not asked:  # keep the answer from standing out by its length
        correct = shorten(correct, max(40, int(1.5 * max(len(d['text']) for d in distractors))))
    if asked:
        explanation = next((s for s in inp["sents"] if correct.lower() in s.lower() and len(correct) >= 3), "")
        explanation = shorten(explanation or f"The answer is {correct}.", 200)
        slip = (f"A common slip is to choose {tempting['text'].rstrip('.')}. {tempting['why_wrong']}" if tempting
                else "Well done if you got it.")
    else:
        explanation = shorten(_about(inp["sents"], topic) or inp["sents"][0], 200)
        slip = (f"A common slip is to think that {mid_sentence(tempting['text'].rstrip('.'))}. {tempting['why_wrong']}"
                if tempting else "Well done if you got it.")
    return {
        "title": f"Quick check: {shorten(topic, 40)}", "question": question, "correct": correct,
        "distractors": distractors, "explanation": explanation, "bloom": "remember" if asked else "understand",
        "countdown_seconds": 8,
        "question_beats": [{"narration": speakable(f"Quick check. {question} Take a moment to think before the answer appears.")}],
        "reveal_beats": [
            {"narration": limit_words(speakable(f"The correct answer is: {correct}."))},
            {"narration": limit_words(speakable(slip))},
        ],
        "source_refs": inp["refs"],
    }


def _object_steps(tpl: dict[str, Any]) -> bool:
    """True when the template's ``steps`` items are ``{latex, annotation}`` objects (the real library)."""
    schema = tpl.get("params_schema") or {}
    items = ((schema.get("properties") or {}).get("steps") or {}).get("items") or {}
    if "$ref" in items:
        items = (schema.get("$defs") or {}).get(items["$ref"].rsplit("/", 1)[-1], {})
    return "latex" in (items.get("properties") or {})


def _equation_animation(topic: str, f: tuple[str, str, str, str]) -> dict[str, Any]:
    """``equation_steps`` params + one beat per step: the formula, rearranged, substituted, solved."""
    lhs, a, op, b = f
    if op == "/":  # lhs = a / b  ->  a = lhs x b
        vl, vb = 3, 4
        res = f"{vl * vb:g}"
        latex = [f"{lhs} = \\frac{{{a}}}{{{b}}}", f"{a} = {lhs} \\times {b}", f"{a} = {vl} \\times {vb}", f"{a} = {res}"]
        words = [f"{lhs} equals {a} divided by {b}", f"multiply both sides by {b}: {a} equals {lhs} times {b}",
                 f"substitute {lhs} equals {vl} and {b} equals {vb}", f"so {a} is {res}"]
        notes = [topic[:100], f"Multiply both sides by {b}", f"Substitute {lhs} = {vl}, {b} = {vb}", f"{a} = {res}"]
    else:  # lhs = a x b  ->  a = lhs / b
        vl, vb = 12, 4
        res = f"{vl / vb:g}"
        latex = [f"{lhs} = {a} \\times {b}", f"{a} = \\frac{{{lhs}}}{{{b}}}", f"{a} = \\frac{{{vl}}}{{{vb}}}", f"{a} = {res}"]
        words = [f"{lhs} equals {a} times {b}", f"divide both sides by {b}: {a} equals {lhs} divided by {b}",
                 f"substitute {lhs} equals {vl} and {b} equals {vb}", f"so {a} is {res}"]
        notes = [topic[:100], f"Divide both sides by {b}", f"Substitute {lhs} = {vl}, {b} = {vb}", f"{a} = {res}"]
    narration = [
        f"Watch the formula on screen: {words[0]}. Keep your eye on {a}.",
        f"Now we want {a} on its own, so we {words[1]}.",
        f"Next, {words[2]}. Notice how only the letters change into numbers.",
        f"And the answer appears in the gold box: {words[3]}. Rearrange first, then substitute.",
    ]
    return {
        "title": f"Solving for {a}",
        "params": {"title": f"Solving for {a}", "steps": [{"latex": x, "annotation": n} for x, n in zip(latex, notes)],
                   "layout": "transform", "box_final": True},
        "beats": [{"narration": speakable(t), "visual_cue": f"The equation becomes ${x}$"} for t, x in zip(narration, latex)],
    }


def _derivation_animation(topic: str, chain: list[tuple[str, str]]) -> dict[str, Any]:
    """``equation_steps`` params + one beat per line of a derivation found in the source."""
    chain = chain[:6]
    latex = [latex_of(expr) or expr for expr, _ in chain]
    notes = [shorten(note, 100) if note else ("Start" if i == 0 else "Simplify") for i, (_, note) in enumerate(chain)]
    narration = []
    for i, (expr, note) in enumerate(chain):
        if i == 0:
            narration.append(f"Watch the expression on screen: {speakable(expr)}.")
        elif i == len(chain) - 1:
            narration.append(f"And the result appears in the gold box: {speakable(expr)}.")
        else:
            lead = note.rstrip(".") if note else "Next"
            joiner = "," if re.match(r"^(?:since|because|as|using|by)\b", lead, re.I) or not note else ", and"
            narration.append(f"{lead}{joiner} it becomes {speakable(expr)}.")
    title = shorten(f"Simplifying {chain[0][0].split('=')[0].strip()}", 60)
    return {
        "title": title,
        "params": {"title": title, "steps": [{"latex": x, "annotation": n} for x, n in zip(latex, notes)],
                   "layout": "transform", "box_final": True},
        "beats": [{"narration": speakable(t), "visual_cue": f"The expression becomes ${x}$"} for t, x in zip(narration, latex)],
    }


def simulation_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    inp = _scene_inputs(prompt)
    topic, sents = inp["topic"], inp["sents"]
    tpl = inp["template"]
    if tpl and "params" in getattr(schema, "model_fields", {}):
        if tpl.get("name") == "equation_steps" and _object_steps(tpl):
            formula = find_formula(inp["text"])
            if formula:
                return _equation_animation(topic, formula)
            chain = derivation(inp["text"])
            if chain:
                return _derivation_animation(topic, chain)
        params = tpl.get("example_params") or {}
        steps = integrations.template_step_count(tpl["name"], params) or 3
        beats = [{"narration": limit_words(speakable(sents[i % len(sents)] if i else f"Watch how {topic} unfolds, one step at a time.")),
                  "visual_cue": f"Step {i + 1} of the {tpl['name']} animation"} for i in range(steps)]
        return {"title": f"{shorten(topic, 40)} in motion", "beats": beats, "params": params}
    code = (
        "from manim import *\n\n\nclass Demo(AadhiScene):\n    def construct(self):\n"
        f"        title = Text({shorten(topic, 30)!r}, font_size=48)\n"
        "        self.wait_until_beat(0)\n        self.play(Write(title))\n"
        "        self.wait_until_beat(1)\n        self.play(title.animate.shift(UP * 2))\n        self.finish()\n"
    )
    return {"title": f"{shorten(topic, 40)} in motion", "code": code, "beats": [
        {"narration": f"Watch the title of {speakable(topic)} appear.", "visual_cue": "The title is written"},
        {"narration": "Now it moves up to make room for the details.", "visual_cue": "The title moves up"},
    ]}


def ai_video_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    inp = _scene_inputs(prompt)
    topic = inp["topic"]
    return {
        "title": shorten(topic, 50),
        "video_prompt": f"Documentary-style footage of an engineering laboratory in India showing {topic.lower()} "
                        "in action, slow dolly-in, soft daylight, shallow depth of field.",
        "rationale": f"Seeing {topic.lower()} in a real lab connects the idea to practice.",
        "fallback_image_prompt": f"A realistic photo of an engineering laboratory showing {topic.lower()}",
        "beats": [_beat(f"Look at how {topic} shows up in a real laboratory."), _beat(inp["sents"][0])],
    }


def interactive_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    inp = _scene_inputs(prompt)
    code = (
        "function setup() {\n  createCanvas(windowWidth, windowHeight);\n  textSize(24);\n}\n"
        "function draw() {\n  background('#1A0B2E');\n  const v = map(mouseX, 0, width, 0, 10);\n"
        "  fill('#F5C542');\n  rect(40, height / 2, v * 40, 30);\n  fill(255);\n"
        "  text('value: ' + v.toFixed(1), 40, height / 2 - 20);\n}\n"
    )
    return {"title": f"Explore {shorten(inp['topic'], 40)}", "p5_code": code,
            "beats": [_beat("Move your mouse from left to right and watch the bar grow."),
                      _beat("The bar length follows the value directly. That is the rule in action.")]}


# ---------------------------------------------------------------------------
# critic, translation, practice
# ---------------------------------------------------------------------------


def critique_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    return {"findings": [], "overall": "No problems found in this offline demo review."}


def translation_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    req = extract_json(prompt, "Request") or {}
    fields = extract_json(prompt, "Fields") or []
    m = re.search(r"\(([a-z]{2})-[A-Z]{2}\)", req.get("target_language", ""))
    native = NATIVE.get(m.group(1) if m else "en", "")
    return {"items": [{"path": f["path"], "text": f"{native}: {f['text']}" if native else f["text"]} for f in fields]}


def practice_responder(prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
    objectives = extract_json(prompt, "Objectives") or []
    formulas = extract_json(prompt, "Formulas") or []
    src = extract_json(prompt, "Source") or []
    refs = [c["id"] for c in src][:1]
    diffs = ["easy", "medium", "hard"]
    problems = []
    for i, o in enumerate(objectives[:6]):
        problems.append({
            "question": f"In your own words, {o['text'][0].lower() + o['text'][1:]}. Give one example from daily life.",
            "steps": ["Recall the key idea from the lecture.", "Connect it to a familiar device or situation.",
                      "State the conclusion clearly."],
            "final_answer": "A correct answer names the idea, applies it to the example and explains why.",
            "hints": ["Look at the key takeaways scene."], "difficulty": diffs[i % 3], "objective_keys": [o["key"]],
            "source_refs": refs,
        })
    for f in formulas[:2]:
        problems.append({
            "question": f"Use ${f['latex']}$ to solve a problem of your own choice and check the units.",
            "steps": ["Write the formula.", "Substitute values with units.", "Compute and check the result."],
            "final_answer": "Depends on the chosen values; units must be consistent.", "hints": ["Start from the formula."],
            "difficulty": "medium", "objective_keys": [], "source_refs": refs,
        })
    while len(problems) < 3:
        problems.append({"question": "Summarise the lecture in three sentences.", "steps": ["List the key ideas."],
                         "final_answer": "Any accurate three-sentence summary.", "hints": [],
                         "difficulty": diffs[len(problems) % 3], "objective_keys": [], "source_refs": refs})
    return {"problems": problems[:8]}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

RESPONDERS: dict[str, Responder] = {
    "GenBrief": brief_responder,
    "GenPlan": plan_responder,
    "GenBoardScene": board_responder,
    "GenChapterCard": chapter_responder,
    "GenQuiz": quiz_responder,
    "GenSimulationCode": simulation_responder,
    "GenAIVideo": ai_video_responder,
    "GenInteractive": interactive_responder,
    "GenCritique": critique_responder,
    "GenTranslation": translation_responder,
    "GenPractice": practice_responder,
}
_PREFIXES = (("GenSimulation_", simulation_responder), ("GenBoardScene_", board_responder))


def responder_for(schema_name: str) -> Responder | None:
    """Responder for a response-model name (dynamic template models matched by prefix)."""
    if schema_name in RESPONDERS:
        return RESPONDERS[schema_name]
    for prefix, fn in _PREFIXES:
        if schema_name.startswith(prefix):
            return fn
    return None


def schema_names() -> list[str]:
    """Every response-model name the pipeline may request (incl. one per Manim template)."""
    names = list(RESPONDERS)
    for t in integrations.list_templates():
        names += [f"GenSimulation_{t.name}", f"GenBoardScene_{t.name}"]
    return names


def register_fake_responders() -> None:
    """Register every responder on ``aadhi.providers.llm.fake.FakeLLM`` (no-op if absent)."""
    try:
        from ..providers.llm.fake import FakeLLM
    except ImportError:
        log.info("FakeLLM is not installed; fake responders not registered")
        return
    for name in schema_names():
        fn = responder_for(name)
        if fn is not None:
            FakeLLM.register(name, fn)


def ensure_fake_responders() -> None:
    """Register responders only for response models without one (keeps responders scripted by tests)."""
    try:
        from ..providers.llm.fake import FakeLLM
    except ImportError:
        return
    present = set(FakeLLM.registered())
    for name in schema_names():
        fn = responder_for(name)
        if fn is not None and name not in present:
            FakeLLM.register(name, fn)
