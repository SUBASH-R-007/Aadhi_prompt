"""Abbreviations (English boards only): never spelled out, spelled out late, or spelled out two ways.

An abbreviation is used where a learner reads it (board, titles, quiz, panel labels; code and maths
left out). It counts as spelled out when the lesson writes or says "Full Name (FN)", "FN (Full Name)",
"FN stands for / is short for / means Full Name", when a definition item's term is the abbreviation and
its text starts with the full name, or when a multi-word term of the lesson has its initials. Capitals
that are style are not abbreviations: a title set in capitals, runs of capital words, common words
written in capitals for emphasis, and any word the lesson also writes or says in ordinary case.

* ``terminology.abbreviation_undefined`` (info) — never spelled out anywhere.
* ``terminology.abbreviation_late`` (info) — spelled out only after the scene that first shows it; a safe
  repair writes "Full Name (FN)" at that first use on the board.
* ``terminology.abbreviation_conflict`` (warning) — spelled out two different ways ("ML" for machine
  learning and for maximum likelihood).

Hidden scenes (skipped in the video) count neither as a use nor as spelling an abbreviation out: an abbreviation
spelled out only in a hidden scene is never spelled out in the video.

Bounded: every pattern has fixed-width or bounded parts and is anchored on capitals or a parenthesis;
the long form before "(FN)" is read word by word backwards from a 400-character window.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from .extract import Facts, SceneFacts
from .report import Finding, Repair, lint_issue
from .text import SMALL_WORDS, group_key, quote, replace_outside_markup

ABBR = re.compile(r"(?<![\w\\$])([A-Z]{2,6})s?(?!\w)")
CAPS_RUN = re.compile(r"\b[A-Z]{2,40}(?:[ \t]{1,4}(?:[-–—:&][ \t]{1,4})?[A-Z][A-Z'’]{1,40}){2,}\b")  # a heading in capitals
ORDINARY_WORD = re.compile(r"(?<![A-Za-z])[A-Za-z][a-z]{1,40}(?![A-Za-z])")  # a word not written in capitals
ROMAN = re.compile(r"X{0,3}(?:IX|IV|V?I{0,3})")  # 'Part II', 'Chapter XIV'
PAREN_ABBR = re.compile(r"\([ \t]{0,4}([A-Z]{2,6})s?[ \t]{0,4}\)")
ABBR_AFTER = re.compile(r"(?<!\w)([A-Z]{2,6})s?[ \t]{0,4}\([ \t]{0,4}([A-Za-z][^()\n]{2,80}?)[ \t]{0,4}\)")
ABBR_STANDS = re.compile(
    r"(?<!\w)([A-Z]{2,6})s?[ \t]{0,4},?[ \t]{0,4}(?:which[ \t]{1,4})?(?:stands[ \t]{1,4}for|is[ \t]{1,4}short[ \t]{1,4}for|"
    r"short[ \t]{1,4}for|is[ \t]{1,4}an?[ \t]{1,4}(?:abbreviation|acronym)[ \t]{1,4}(?:of|for)|means)[ \t]{1,4}"
    r"(?:the[ \t]{1,4})?([A-Za-z][\w'’\- \t]{2,80})")
WORDS = re.compile(r"(?<![\w'’-])[A-Za-z][\w'’-]{0,40}(?![\w'’-])")
_WORD_CHARS = re.compile(r"[\w'’-]")
_PARTS = re.compile(r"[-’']|(?<=[a-z])(?=[A-Z])")
# Every maximal run of 2-6 capitals: each abbreviation the three definition patterns capture is one, so a text
# none of whose runs is a collected abbreviation needs no pattern scan (linear, whatever was collected).
_CAP_RUN = re.compile(r"(?<![A-Z])[A-Z]{2,6}(?![A-Z])")

MAX_ABBREVIATIONS = 30
MAX_DEFINITION_SCANS = 400  # "(FN)" anchors examined per lesson

NOT_ABBREVIATIONS = frozenset({
    "NOTE", "TIP", "TIPS", "KEY", "STEP", "STEPS", "YES", "NO", "AND", "OR", "NOT", "THE", "TRUE", "FALSE", "NULL", "NONE",
    "END", "IF", "THEN", "ELSE", "FOR", "TO", "OF", "IN", "ON", "AT", "BY", "IS", "IT", "BE", "DO", "GO", "UP", "WE", "US",
    "AN", "AS", "ALL", "NEW", "NOW", "WHY", "HOW", "WHAT", "WHO", "LET", "VS", "TODO", "WOW", "HINT", "QUIZ", "CHECK",
    "STOP", "START", "DONE", "ANSWER", "RECAP", "INPUT", "OUTPUT", "ERROR", "INFO", "TOTAL", "SELECT", "FROM", "WHERE",
    "JOIN", "GROUP", "ORDER", "INSERT", "UPDATE", "DELETE", "CREATE", "TABLE", "INTO", "VALUES", "LIKE", "LIMIT", "HAVING",
    "UNION", "INNER", "OUTER", "LEFT", "RIGHT", "GET", "POST", "PUT", "PATCH", "NAN", "MAX", "MIN", "SUM", "AVG", "COUNT",
    # known to everyone (spelling them out would be odd)
    "OK", "TV", "USA", "UK", "UN", "EU", "AM", "PM", "AD", "BC", "BCE", "CE", "ID", "PDF", "URL", "USB", "GPS", "DVD", "FAQ",
    "KB", "MB", "GB", "TB", "PC", "CPU", "RAM", "LED", "LCD", "AC", "DC", "SI", "WIFI", "HTML", "CSS", "SQL", "API",
    "NOTES", "FAQS", "DIY", "ASAP", "AKA", "ETC", "IE", "EG",
    # ordinary words written in capitals for emphasis ("HIGH resistance, LESS current")
    "HIGH", "LOW", "MORE", "LESS", "MOST", "LEAST", "SAME", "ONLY", "NEVER", "ALWAYS", "VERY", "MUST", "BIG", "SMALL",
    "FAST", "SLOW", "DOWN", "OFF", "OUT", "BOTH", "EACH", "ONE", "TWO", "THREE", "ZERO", "NEXT", "LAST", "FIRST", "YOU",
    "YOUR", "OUR", "THIS", "THAT", "BUT", "WITH", "SO", "LOOK", "WAIT", "WRONG", "GOOD", "BAD", "EQUAL",
})


def usable(abbr: str) -> bool:
    return abbr not in NOT_ABBREVIATIONS and not ROMAN.fullmatch(abbr)


def _parts_of(word: str) -> list[str]:
    return [p for p in _PARTS.split(word) if p]


def _initials_options(words: list[str]) -> tuple[str, str, str]:
    """The initials a multi-word name can be abbreviated to (non-empty ``words``)."""
    significant = [w for w in words if w.lower() not in SMALL_WORDS]
    return ("".join(w[0] for w in significant), "".join(w[0] for w in words),
            "".join(p[0] for w in significant for p in _parts_of(w)))


def initials_ok(words: list[str], abbr: str) -> bool:
    abbr = abbr.upper()
    words = [w for w in words if w]
    return bool(words) and any(o and o.upper() == abbr for o in _initials_options(words))


def _initials_index(facts: Facts) -> dict[str, str]:
    """Initials -> the first multi-word term form (group order) that has them; one pass over the groups."""
    index: dict[str, str] = {}
    for forms in facts.groups.values():
        # forms written somewhere that plays (the concept map, or a scene that is not hidden)
        shown = [form for form, occ in forms.items()
                 if any(o.scene is None or not facts.scenes[o.scene].scene.hidden for o in occ)]
        if not shown:
            continue
        form = shown[0]
        parts = re.split(r"[-_\s]+", form)
        words = [w for w in parts if w]
        if len(parts) < 2 or not words:
            continue
        for o in _initials_options(words):
            if o:
                index.setdefault(o.upper(), form)
    return index


def _expansion_before(words: list[str], abbr: str) -> str | None:
    for start in range(len(words) - 1, -1, -1):
        candidate = words[start:]
        if candidate[0].lower() in SMALL_WORDS:
            continue
        if len(candidate) > len(abbr) * 2 + 1:
            break
        if initials_ok(candidate, abbr):
            return " ".join(candidate)
    return None


def _expansion_after(words: list[str], abbr: str) -> str | None:
    for end in range(1, min(len(words), len(abbr) * 2 + 1) + 1):
        if words[end - 1].lower() in SMALL_WORDS:
            continue
        if initials_ok(words[:end], abbr):
            return " ".join(words[:end])
    return None


def words_before(text: str, end: int, limit: int = 8) -> list[str]:
    """Up to ``limit`` words written right before ``end``, separated by spaces or tabs only (bounded window)."""
    window = text[max(0, end - 400):end]
    i = len(window)
    blanks = 0
    while i > 0 and window[i - 1] in " \t" and blanks < 4:
        i -= 1
        blanks += 1
    words: list[str] = []
    while i > 0 and len(words) < limit:
        j = i
        while j > 0 and _WORD_CHARS.match(window[j - 1]) and i - j <= 41:
            j -= 1
        word = window[j:i]
        if not word or not ("A" <= word[0] <= "Z" or "a" <= word[0] <= "z") or (j > 0 and _WORD_CHARS.match(window[j - 1])):
            break
        words.append(word)
        i = j
        gap = 0
        while i > 0 and window[i - 1] in " \t" and gap < 4:
            i -= 1
            gap += 1
        if gap == 0:
            break
    words.reverse()
    return words


def _visible_without_caps_runs(text: str) -> str:
    return CAPS_RUN.sub(" ", text)


def _reading_text(f: SceneFacts) -> str:
    """What a learner reads, minus a title set in capitals (a style, not abbreviations) and capital runs."""
    text, title = f.visible, f.scene.title or ""
    if title and title.upper() == title and any(ch.isalpha() for ch in title):
        text = text.replace(title, " ", 1)
    return _visible_without_caps_runs(text)


def known_words(facts: Facts) -> set[str]:
    """Words the lesson also writes or says in ordinary case ("energy", "Ohm"): the same word in capitals
    elsewhere is emphasis ("HIGH resistance"), not an abbreviation (real ones, like BJT, never appear so)."""
    out: set[str] = set()
    for f in facts.scenes:
        for text in (f.visible, f.narration):
            out.update(w.lower() for w in ORDINARY_WORD.findall(text))
    return out


def analyse(facts: Facts) -> OrderedDict[str, dict[str, Any]]:
    """{abbr: {"scenes": [first-use order], "definitions": [{"expansion", "confident", "scenes"}], "initials"}}."""
    uses: OrderedDict[str, list[int]] = OrderedDict()
    ordinary: set[str] | None = None
    for f in (f for f in facts.scenes if not f.scene.hidden):  # what plays (hidden scenes are skipped)
        for abbr in dict.fromkeys(ABBR.findall(_reading_text(f))):
            if usable(abbr):
                if ordinary is None:
                    ordinary = known_words(facts)
                if abbr.lower() in ordinary:
                    continue
                if abbr not in uses and len(uses) >= MAX_ABBREVIATIONS:
                    continue  # only the first MAX_ABBREVIATIONS (first-use order) are reported: never scan later ones
                scenes = uses.setdefault(abbr, [])
                if f.index not in scenes:
                    scenes.append(f.index)
    if not uses:
        return OrderedDict()
    defs: dict[str, OrderedDict[tuple[str, bool], dict[str, Any]]] = {}

    def define(abbr: str, expansion: str | None, confident: bool, scene: int | None) -> None:
        if abbr not in uses:
            return
        entry = defs.setdefault(abbr, OrderedDict())
        key = group_key(expansion) if expansion else ""
        record = entry.setdefault((key, confident), {"expansion": expansion, "confident": confident, "scenes": []})
        if scene is not None and scene not in record["scenes"]:
            record["scenes"].append(scene)

    texts: list[tuple[int | None, str]] = [(f.index, facts.explain_text(f)) for f in facts.scenes if not f.scene.hidden]
    texts += [(None, f"{c.title} \n {c.summary}"[:800]) for c in facts.sp.concept_map]
    scans = 0
    for scene, text in texts:
        if uses.keys().isdisjoint(_CAP_RUN.findall(text)):  # cheap test before the pattern scans
            continue
        for m in PAREN_ABBR.finditer(text):
            if m.group(1) not in uses:
                continue
            scans += 1
            if scans > MAX_DEFINITION_SCANS:
                break
            found = _expansion_before(words_before(text, m.start()), m.group(1))
            define(m.group(1), found, bool(found), scene)
        for m in ABBR_AFTER.finditer(text):
            if m.group(1) not in uses:
                continue
            words = WORDS.findall(m.group(2))
            if initials_ok(words, m.group(1)):
                define(m.group(1), " ".join(words), True, scene)
            elif len(words) >= 2:
                define(m.group(1), None, False, scene)
        for m in ABBR_STANDS.finditer(text):
            if m.group(1) not in uses:
                continue
            found = _expansion_after(WORDS.findall(m.group(2)), m.group(1))
            define(m.group(1), found, bool(found), scene)
    for f in facts.scenes:  # a definition item whose term is the abbreviation
        if f.scene.hidden:
            continue
        for term, body in f.definitions:
            abbr = term.strip()
            if abbr in uses:
                found = _expansion_after(WORDS.findall(body[:200]), abbr)
                if found:
                    define(abbr, found, True, f.index)
    out: OrderedDict[str, dict[str, Any]] = OrderedDict()
    by_initials: dict[str, str] | None = None  # built once, on the first abbreviation that needs it
    for abbr, scenes in list(uses.items())[:MAX_ABBREVIATIONS]:
        initials = None
        if abbr not in defs:
            if by_initials is None:
                by_initials = _initials_index(facts)
            initials = by_initials.get(abbr)
        out[abbr] = {"scenes": scenes, "definitions": list((defs.get(abbr) or {}).values()), "initials": initials}
    return out


def _confident(a: dict[str, Any]) -> OrderedDict[str, dict[str, Any]]:
    confident: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for d in a["definitions"]:
        if d["confident"] and d["expansion"]:
            rec = confident.setdefault(group_key(d["expansion"]), {"expansion": d["expansion"], "scenes": []})
            rec["scenes"] += [s for s in d["scenes"] if s not in rec["scenes"]]
    return confident


def _first_use_repair(facts: Facts, abbr: str, expansion: str, index: int) -> Repair | None:
    """'Full Name (FN)' at the first bare use of the abbreviation on scene ``index``'s board, outside capital
    runs (a heading set in capitals is style, not a use; detection skips it the same way)."""
    pattern = re.compile(r"(?<![\w\\$])" + re.escape(abbr) + r"(?!\w)(?![ \t]{0,4}\()")
    for path, before in facts.scenes[index].fields:
        if path[0] != "board" or not pattern.search(_visible_without_caps_runs(before)):
            continue
        done = False
        runs: list[tuple[int, int]] | None = None

        def first_outside_caps_run(m: re.Match[str]) -> str:
            nonlocal done, runs
            if done:
                return m.group(0)
            if runs is None:  # capital runs of the text being replaced (the masked field)
                runs = [(r.start(), r.end()) for r in CAPS_RUN.finditer(m.string)]
            if any(a <= m.start() < b for a, b in runs):
                return m.group(0)
            done = True
            return f"{expansion} ({abbr})"

        after = replace_outside_markup(before, pattern, first_outside_caps_run)
        if after is not None and after != before:
            return Repair(label=f"Spell it out here: {quote(expansion + ' (' + abbr + ')')}",
                          edits=[{"scene_id": facts.lesson.scene_id(index), "path": list(path), "before": before,
                                  "after": after}])
    return None


def abbreviation_findings(facts: Facts, analysed: OrderedDict[str, dict[str, Any]]) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    for abbr, a in analysed.items():
        confident = _confident(a)
        if len(confident) >= 2:
            ways = list(confident.values())
            second = sorted(ways[1]["scenes"])
            msg = (f"{quote(abbr)} is spelled out in two different ways: "
                   + "; ".join(f"{quote(w['expansion'])} in {lesson.where(w['scenes'])}" for w in ways[:3])
                   + ". Learners may be confused: keep one meaning, or use a different abbreviation for the other.")
            scene = second[0] if second else (a["scenes"][0] if a["scenes"] else None)
            out.append(Finding(lint_issue("terminology.abbreviation_conflict", msg, "warning",
                                          scene_id=lesson.scene_id(scene) if scene is not None else None)))
            continue
        if not a["definitions"] and not a["initials"]:
            msg = (f"{quote(abbr)} is used in {lesson.where(a['scenes'])} but never spelled out in the lesson. "
                   "Spell it out once, the first time it appears.")
            out.append(Finding(lint_issue("terminology.abbreviation_undefined", msg, scene_id=lesson.scene_id(a["scenes"][0]))))
            continue
        if len(confident) == 1 and a["scenes"]:
            way = next(iter(confident.values()))
            first_use = min(a["scenes"])
            spelled = [s for s in way["scenes"] if s is not None]
            if spelled and min(spelled) > first_use:
                msg = (f"{quote(abbr)} is first shown in {lesson.label(first_use)} but only spelled out "
                       f"({quote(way['expansion'])}) in {lesson.where(spelled)}. Spell it out the first time it appears.")
                out.append(Finding(lint_issue("terminology.abbreviation_late", msg, scene_id=lesson.scene_id(first_use)),
                                   _first_use_repair(facts, abbr, way["expansion"], first_use)))
    return out


def registry_entries(facts: Facts, analysed: OrderedDict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for abbr, a in analysed.items():
        confident = _confident(a)
        if confident:
            expansion, how = next(iter(confident.values()))["expansion"], "spelled_out"
        elif a["definitions"]:
            expansion, how = None, "spelled_out"
        elif a["initials"]:
            expansion, how = a["initials"], "initials"
        else:
            expansion, how = None, None
        out.append({"abbreviation": abbr, "expansion": expansion, "how": how, "ways": len(confident),
                    "scene_ids": [facts.lesson.scene_id(i) for i in a["scenes"][:12]]})
    return out
