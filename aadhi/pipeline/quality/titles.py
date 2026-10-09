"""Title casing (English boards only): ``title.casing_mixed`` (info).

Scene titles are compared with each other (teaching scenes; the opening title card and chapter cards
are other heading levels) and chapter titles with each other. A title whose style (sentence case,
Title Case, capitals) differs from most titles of its level is noted. Ported from the fork's
``quality_style.title_casing`` (first word, acronyms and internal capitals give no signal).
"""

from __future__ import annotations

import re
from collections import Counter

from .extract import Facts, plain
from .report import Finding, lint_issue
from .text import cut, quote, upper_first

TEXT_LIMIT = 200
MINOR_WORDS = frozenset("a an the and but or nor for so yet as at by in of on to up vs via per with from into over onto than "
                        "off out".split())
ORDER = ("sentence", "title", "upper")  # ties: the first wins
WORDS = {"sentence": ("capitalises only its first word", "capitalise only the first word"),
         "title": ("capitalises every main word", "capitalise every main word"),
         "upper": ("is written in capital letters", "are written in capital letters")}
_SEGMENTS = re.compile(r"[:;!?.—–]|\s-\s")  # fixed-width alternatives only: linear
_WORD = re.compile(r"[^\W\d_][\w'’-]{0,60}")


def title_casing(title: object) -> str | None:
    """'sentence' | 'title' | 'upper' | None (cannot tell: one word, proper nouns only, or an ambiguous mixture)."""
    text = cut(plain(title[: 4 * TEXT_LIMIT]) if isinstance(title, str) else "", TEXT_LIMIT)
    tokens = _WORD.findall(text)
    if len([t for t in tokens if len(t) >= 2]) >= 2 and all(t.isupper() for t in tokens) and sum(len(t) for t in tokens) >= 6:
        return "upper"
    caps = lower = 0
    for segment in _SEGMENTS.split(text):
        for word in _WORD.findall(segment)[1:]:  # the first word (also after a colon) is capitalised in every style
            if len(word) < 2 or word.isupper() or word[1:] != word[1:].lower():
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


def _compare(facts: Facts, found: list[tuple[str, int | None, str]], what: str) -> list[Finding]:
    """``found``: (style, scene index or None, title)."""
    styles = Counter(style for style, _i, _t in found)
    if len(styles) < 2:
        return []
    usual = max(ORDER, key=lambda c: (styles.get(c, 0), -ORDER.index(c)))
    out = []
    for style, index, title in found:
        if style == usual:
            continue
        where = facts.lesson.label(index) if index is not None else f"the chapter title {quote(title)}"
        msg = (f"{upper_first(where)} {WORDS[style][0]}, unlike most {what} in the lesson, which "
               f"{WORDS[usual][1]}.")
        out.append(Finding(lint_issue("title.casing_mixed", msg,
                                      scene_id=facts.lesson.scene_id(index) if index is not None else None)))
    return out


def title_findings(facts: Facts) -> list[Finding]:
    scenes = []
    for f in facts.scenes:
        if f.scene.type in ("title", "chapter_card"):
            continue
        style = title_casing(f.scene.title)
        if style:
            scenes.append((style, f.index, f.scene.title))
    chapters = []
    for ch in facts.sp.chapters:
        style = title_casing(ch.title)
        if style:
            chapters.append((style, None, ch.title))
    return _compare(facts, scenes, "scene titles") + _compare(facts, chapters, "chapter titles")
