"""Shared helpers of the quality checks: term normalisation, form classes, scene labels and bounded edits.

Every pattern here is anchored or bounded (no nested unbounded repetition), and every text a check
reads is cut to a fixed length first, so crafted input cannot stall a lint (``p18-adversarial-bounds``).
Ported from the fork's ``quality_education`` helpers (``group_key``, ``_sep_sig``, ``_loose``) and
adapted to typed screenplay positions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from ...schemas.screenplay import Screenplay
from ..richlite import mask, unmask

MAX_FORM = 40  # a term longer than this is a phrase, not a term
MAX_TERM_WORDS = 5
MAX_TITLE = 300
MAX_SCENE_TEXT = 4000  # board / narration text read per scene and kind (a long scene has ~2000; 200 stay fast)
MAX_FIELD = 1200  # BoardItem.text max_length: an edit never produces a longer field
MAX_GROUP_FORMS = 24  # distinct written forms of one term clustered pairwise (a real lesson has a handful)

HYPHENS = "-‐‑–"  # a hyphen, the typographic hyphens and an en dash written as a hyphen
SEPARATORS = re.compile("[" + HYPHENS + r"_\s]+")
_SEPARATORS_KEEP = re.compile("([" + HYPHENS + r"_\s]+)")
_NON_WORD = re.compile(r"[^\w]")
SMALL_WORDS = frozenset({"of", "and", "the", "for", "to", "in", "on", "a", "an", "by", "with", "de", "&", "or", "at"})
_LETTERS = re.compile(r"[^\W\d_]")
_TWO_LETTERS = re.compile(r"[^\W\d_]{2}")
_CLEAN_STRIP = " \t.,:;!?\"'()[]{}“”‘’*•-–—"


def cut(text: Any, limit: int) -> str:
    """``text`` as a string of at most ``limit`` characters (whitespace collapsed)."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text[: limit * 2]).strip()[:limit]


def plural(word: str) -> str:
    """A simple singular (enough to group 'chloroplasts' with 'chloroplast'; both forms map the same way)."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _plural_cased(token: str) -> str:
    low = token.lower()
    single = plural(low)
    if single == low:
        return token
    if low.endswith("ies") and single.endswith("y"):
        return token[:-3] + ("Y" if token[-3].isupper() else "y")
    return token[: len(single)]


def group_key(form: str) -> str:
    """A term's group: case-folded, hyphens / underscores / spaces collapsed, simple plurals made singular."""
    words = [_NON_WORD.sub("", w.casefold()) for w in SEPARATORS.split(str(form or "")[: MAX_FORM * 2])]
    return "".join(plural(w) for w in words if w)


def sep_sig(form: str) -> str:
    """The term as written apart from case: 'machine-learning', 'machine learning' and 'machinelearning' differ."""
    parts = _SEPARATORS_KEEP.split(str(form or "").strip()[: MAX_FORM * 2])
    out: list[str] = []
    for n, p in enumerate(parts):
        if n % 2:
            out.append("-" if any(h in p for h in HYPHENS) else ("_" if "_" in p else " "))
        else:
            out.append(plural(_NON_WORD.sub("", p.casefold())))
    return "".join(out)


def loose(form: str, heading: bool) -> str:
    """The term's capitalisation as it matters.

    The first word's first letter never counts (a sentence or a label starts with a capital), and in a
    heading-like place Title Case is a convention, not a different spelling; a heading set in capitals
    is a style compatible with any spelling (``"*"``).
    """
    form = str(form or "")[: MAX_FORM * 2]
    tokens = [t for t in SEPARATORS.split(form.strip()) if t]
    letters = "".join(_LETTERS.findall(form))
    if heading and letters and letters.isupper() and (len(tokens) >= 2 or len(letters) >= 7):
        return "*"
    words = [t for t in tokens if t[:1].isalpha() and t.lower() not in SMALL_WORDS]  # "Speed of Light" is Title Case
    title = heading and bool(words) and all(t[:1].isupper() for t in words)
    out: list[str] = []
    for n, t in enumerate(tokens):
        t = _NON_WORD.sub("", t)
        if t and (n == 0 or title) and t[:1].isalpha():
            t = t[:1].lower() + t[1:]
        out.append(_plural_cased(t) if t else t)
    return "".join(out)


STOP_TERMS = frozenset(group_key(w) for w in (
    "note", "tip", "important", "remember", "warning", "example", "hint", "key point", "definition", "answer",
    "question", "step", "summary", "recap", "fact", "caution", "yes", "no", "true", "false", "feature", "aspect",
    "property", "criteria", "basis", "point", "parameter", "output", "input", "result", "solution", "problem",
    "exercise", "key takeaway", "takeaway", "formula", "worked example",
))


def clean_form(text: Any) -> str:
    """A term as found in a place (whitespace collapsed, surrounding punctuation removed)."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text[: MAX_FORM * 4]).strip(_CLEAN_STRIP)


def term_ok(form: str) -> bool:
    """Whether a cleaned form reads as a term (short, has letters, not a stop word or a number)."""
    key = group_key(form)
    return (2 <= len(form) <= MAX_FORM and len(form.split()) <= MAX_TERM_WORDS and _TWO_LETTERS.search(form) is not None
            and len(key) >= 2 and key not in STOP_TERMS and not key.isdigit())


def form_classes(forms: Sequence[str], compatible: Callable[[str, str], bool],
                 scenes_of: Callable[[str], Iterable[int]]) -> list[dict[str, Any]]:
    """Forms clustered into compatible classes (in order of first appearance), each with its scene indexes.

    Only the MAX_GROUP_FORMS forms used in most scenes are clustered (ties: the first written), so the
    pairwise pass stays bounded however many spellings a crafted lesson invents (p18-adversarial-bounds);
    a group with fewer forms is clustered exactly as before."""
    where_of = {f: sorted(set(scenes_of(f))) for f in forms}
    if len(forms) > MAX_GROUP_FORMS:
        keep = set(sorted(range(len(forms)), key=lambda n: (-len(where_of[forms[n]]), n))[:MAX_GROUP_FORMS])
        forms = [f for n, f in enumerate(forms) if n in keep]
    classes: list[dict[str, Any]] = []
    for form in forms:
        where = where_of[form]
        for c in classes:
            if all(compatible(form, f) for f in c["forms"]):
                c["forms"].append(form)
                c["by_form"][form] = where
                c["scenes"] |= set(where)
                break
        else:
            classes.append({"forms": [form], "by_form": {form: where}, "scenes": set(where)})
    for c in classes:
        c["scenes"] = sorted(c["scenes"])
    return classes


def dominant(classes: Sequence[dict[str, Any]]) -> int:
    """The class most scenes use (ties: the first written); the others are the variants to look at."""
    return max(range(len(classes)), key=lambda n: (len(classes[n]["scenes"]), -n))


def latin_board(screenplay: Screenplay) -> bool:
    """Whether the board is written in English (casing and abbreviation rules are meaningless for Indic scripts)."""
    return (screenplay.board_language or screenplay.language or "en").lower().startswith("en")


def quote(text: Any, limit: int = 60) -> str:
    t = cut(text, limit + 1)
    return "“" + (t if len(t) <= limit else t[: limit - 1].rstrip() + "…") + "”"


class Lesson:
    """Scene order and plain-words scene labels for messages ("scene 3 (“Ohm's law”)")."""

    def __init__(self, screenplay: Screenplay) -> None:
        self.sp = screenplay
        self.index = {s.id: i for i, s in enumerate(screenplay.scenes)}

    def scene_id(self, index: int) -> str:
        return self.sp.scenes[index].id

    def label(self, index: int) -> str:
        scene = self.sp.scenes[index]
        name = scene.title or getattr(scene, "question", "") or ""
        return f"scene {index + 1}" + (f" ({quote(name, 50)})" if cut(name, 60) else "")

    def where(self, indexes: Iterable[int], limit: int = 5) -> str:
        scenes = sorted({i for i in indexes if isinstance(i, int) and not isinstance(i, bool)})
        if not scenes:
            return "the concept map"
        if len(scenes) == 1:
            return self.label(scenes[0])
        nums = [str(s + 1) for s in scenes[:limit]]
        more = len(scenes) - limit
        if more > 0:
            return "scenes " + ", ".join(nums) + f" and {more} more"
        return "scenes " + ", ".join(nums[:-1]) + " and " + nums[-1]

    def forms_text(self, classes: Sequence[dict[str, Any]]) -> str:
        """'“ML” in scene 2 (…); “ml” in scenes 4 and 5' for a message."""
        parts = []
        for c in classes[:4]:
            parts.append(" / ".join(quote(f) for f in c["forms"][:3]) + " in " + self.where(c["scenes"]))
        return "; ".join(parts)


def upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------
# Bounded, markup-aware edits (safe repairs)
# ---------------------------------------------------------------------------

def phrase_pattern(form: str) -> re.Pattern[str]:
    """Whole-word, case-sensitive pattern for ``form`` (its spaces match 1-4 blanks)."""
    words = [re.escape(w) for w in form.split()]
    edge = r"\w" + re.escape(HYPHENS)
    return re.compile(r"(?<![" + edge + "])" + r"[ \t]{1,4}".join(words) + r"(?![" + edge + "])")


def replace_outside_markup(text: str, pattern: re.Pattern[str], repl: str | Callable[[re.Match[str]], str],
                           count: int = 0) -> str | None:
    """``text`` with ``pattern`` replaced outside ``$math$`` and ```code``` spans, or None when it cannot be done
    safely (placeholder characters in the text, or the result would exceed the field limit)."""
    if not text or "⟦" in text or "⟧" in text or len(text) > MAX_FIELD:
        return None
    masked, originals = mask(text)
    replaced = pattern.sub(repl, masked, count=count)
    out = unmask(replaced, originals)
    if len(out) > MAX_FIELD:
        return None
    return out


def get_path(scene: Any, path: Sequence[Any]) -> Any:
    """The value at ``path`` inside a scene (attributes and list indexes), or None."""
    cur = scene
    for key in path:
        if cur is None:
            return None
        if isinstance(key, int):
            if not isinstance(cur, list | tuple) or not 0 <= key < len(cur):
                return None
            cur = cur[key]
        else:
            cur = getattr(cur, key, None)
    return cur
