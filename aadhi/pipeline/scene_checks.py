"""Pedagogy and language checks on a generated scene (first validation pass of the scene writer).

These checks turn the writing rules of ``prompts/style.md`` and the family prompts into concrete,
model-readable problems that go back to the model in the re-ask loop:

* narration / board in the requested language (models often drift back to English);
* redundancy principle: a board item must not repeat its beat's narration word for word;
* quizzes: the correct option must not stand out by length;
* worked examples: steps as ``example_step`` items, later steps faded (blank + fill);
* signalling: when a visual appears (figure, side panel, animation, footage) the narration refers
  to it (English narration only; other languages are left to the critic).

All functions are pure and work on the LLM-facing generation models (``gen_models``).
"""

from __future__ import annotations

import re
from typing import Any

from .gen_models import scene_family
from .richlite import to_plain

# Unicode blocks of the Indic scripts we support (language prefix -> range).
_SCRIPTS: dict[str, tuple[int, int]] = {
    "ta": (0x0B80, 0x0BFF),
    "hi": (0x0900, 0x097F),
    "te": (0x0C00, 0x0C7F),
    "kn": (0x0C80, 0x0CFF),
    "ml": (0x0D00, 0x0D7F),
}
_LANGUAGE_NAMES = {"ta": "Tamil", "hi": "Hindi", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "en": "English"}
MIN_LETTERS_TO_JUDGE = 24
MIN_INDIC_SHARE = 0.35  # technical terms kept in English are fine; mostly-English text is not
MIN_LATIN_SHARE = 0.7
REDUNDANT_MIN_WORDS = 8
_REDUNDANT_KINDS = {"bullet", "paragraph", "takeaway", "callout_info", "callout_tip", "callout_warning"}
_VISUAL_WORDS = re.compile(
    r"\b(look|looking|see|seeing|notice|watch|watching|shown|shows?|picture|diagram|figure|graph|chart|image|"
    r"photo|animation|plot|table|screen|board|footage|clip|video|sketch|model|slider)\b",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", to_plain(text or "").lower()).strip()


def script_share(text: str, language: str) -> float | None:
    """Share of letters written in ``language``'s script (Latin for English); None if too little text."""
    prefix = (language or "en").split("-")[0].lower()
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < MIN_LETTERS_TO_JUDGE:
        return None
    if prefix in _SCRIPTS:
        lo, hi = _SCRIPTS[prefix]
        return sum(1 for ch in letters if lo <= ord(ch) <= hi) / len(letters)
    return sum(1 for ch in letters if ch.isascii()) / len(letters)


def language_problem(text: str, language: str, what: str) -> str | None:
    """A problem when ``text`` is clearly not written in ``language``."""
    share = script_share(text, language)
    if share is None:
        return None
    prefix = (language or "en").split("-")[0].lower()
    name = _LANGUAGE_NAMES.get(prefix, language)
    threshold = MIN_INDIC_SHARE if prefix in _SCRIPTS else MIN_LATIN_SHARE
    if share < threshold:
        return (f"{what} must be written in {name}; keep only glossary terms marked keep_in_english in English")
    return None


def _beats(out: Any) -> list[Any]:
    if hasattr(out, "question_beats"):
        return list(out.question_beats) + list(out.reveal_beats)
    return list(getattr(out, "beats", None) or [])


def _board_text(out: Any) -> str:
    parts = [getattr(out, "title", "") or ""]
    for b in _beats(out):
        item = getattr(b, "board", None)
        if item is not None:
            parts += [item.text or "", item.term or "", item.caption or "", item.justification or ""]
    if hasattr(out, "question_beats"):
        parts += [out.question, out.correct, out.explanation] + [d.text for d in out.distractors]
    return " ".join(p for p in parts if p)


def _language_problems(out: Any, language: str, board_language: str) -> list[str]:
    problems: list[str] = []
    narration = " ".join(b.narration for b in _beats(out))
    p = language_problem(narration, language, "narration")
    if p:
        problems.append(p)
    p = language_problem(to_plain(_board_text(out)), board_language, "on-screen text (title, board, quiz)")
    if p:
        problems.append(p)
    return problems


def _redundancy_problems(out: Any) -> list[str]:
    problems: list[str] = []
    for n, b in enumerate(_beats(out), 1):
        item = getattr(b, "board", None)
        if item is None or item.kind not in _REDUNDANT_KINDS:
            continue
        text = _norm(item.text)
        if len(text.split()) >= REDUNDANT_MIN_WORDS and text in _norm(b.narration):
            problems.append(f"beat {n}: the board item repeats the narration word for word; make it a short key "
                            "phrase (the narration explains, the board signals the structure)")
    return problems


def _quiz_problems(out: Any) -> list[str]:
    if not hasattr(out, "distractors") or not out.distractors:
        return []
    correct = len((out.correct or "").strip())
    longest = max(len((d.text or "").strip()) for d in out.distractors)
    if correct > 1.8 * max(1, longest) and correct - longest > 20:
        return ["the correct option is much longer than the distractors, so it stands out; make all options "
                "similar in length and detail"]
    return []


def _example_problems(planned: Any, out: Any, depth: str) -> list[str]:
    if planned.type != "example":
        return []
    steps = [b.board for b in _beats(out) if b.board is not None and b.board.kind == "example_step"]
    if len(steps) < 2:
        return ["a worked example shows its solution as example_step items, one step per beat, each with a "
                "justification"]
    if depth != "overview" and len(steps) >= 3 and not any(s.blank for s in steps):
        return ["fade the worked example: reveal one of the later steps with blank: true, invite the learner to try "
                "it with pause_after 2-4 s, then fill it in the next beat with fill_previous_blank: true"]
    return []


def _has_visual(planned: Any, out: Any) -> bool:
    if planned.type in ("simulation", "ai_video", "interactive"):
        return True
    panel = getattr(out, "side_panel", None)
    if panel is not None and getattr(panel, "kind", "skill_tree") != "skill_tree":
        return True
    return any(b.board is not None and b.board.kind == "figure" for b in _beats(out) if hasattr(b, "board"))


def _signalling_problems(planned: Any, out: Any, language: str) -> list[str]:
    if not (language or "en").lower().startswith("en") or not _has_visual(planned, out):
        return []
    narration = " ".join(b.narration for b in _beats(out))
    if narration and not _VISUAL_WORDS.search(narration):
        return ["the narration never refers to the visual; point the learner at it when it appears "
                "(for example 'Look at the diagram on the right' or 'Watch the current arrow')"]
    return []


def pedagogy_problems(planned: Any, out: Any, *, language: str = "en-IN", board_language: str | None = None,
                      depth: str = "standard") -> list[str]:
    """Language, redundancy, quiz, worked-example and signalling problems of a generated scene."""
    problems = _language_problems(out, language, board_language or language)
    if scene_family(planned.type) == "board":
        problems += _redundancy_problems(out)
    problems += _quiz_problems(out)
    problems += _example_problems(planned, out, depth)
    problems += _signalling_problems(planned, out, language)
    return problems
