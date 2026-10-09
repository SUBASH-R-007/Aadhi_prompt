"""Code blocks: what the player can colour, indentation and the languages a lesson mixes (any language).

* ``code.language_unhighlighted`` (warning) — a code item declares a language the player (and so the
  MP4) shows without syntax colours. Not fixable by a rewrite: the language of an example is the
  author's choice, and a rewrite must never switch it to get colours.
* ``code.mixed_indentation`` (warning, fixable) — a block indents some lines with tabs and others with
  spaces (uneven on screen; in Python it can change what the code means).
* ``code.mixed_languages`` (info) — the lesson shows code in several languages.

Line and block length are already ``board.item_too_long`` (80 characters, 18 lines). The coloured
languages mirror ``PRISM_LANGS`` in ``web/js/player/board.js`` (a test keeps the two equal).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ...schemas.screenplay import BoardItemKind, BoardScene
from .extract import Facts
from .report import Finding, lint_issue
from .text import upper_first

# web/js/player/board.js PRISM_LANGS: language (as written in BoardItem.language) -> Prism grammar
HIGHLIGHTED = {
    "python": "python", "py": "python", "c": "c", "cpp": "cpp", "c++": "cpp", "java": "java", "javascript": "javascript",
    "js": "javascript", "bash": "bash", "sh": "bash", "shell": "bash", "sql": "sql", "matlab": "matlab", "verilog": "verilog",
}
PLAIN = frozenset({"none", "text", "plaintext", "plain", "txt", "output", "console", "terminal", "log", "nohighlight"})
ALIASES = {"ts": "typescript", "cxx": "cpp", "cc": "cpp", "h": "c", "zsh": "bash", "cs": "csharp", "c#": "csharp",
           "rb": "ruby", "kt": "kotlin", "golang": "go", "html": "markup", "xml": "markup", "py3": "python",
           "python3": "python", "node": "javascript", "mjs": "javascript"}
NAMES = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "java": "Java", "c": "C", "cpp": "C++",
         "csharp": "C#", "sql": "SQL", "markup": "HTML", "css": "CSS", "bash": "shell", "ruby": "Ruby", "go": "Go",
         "kotlin": "Kotlin", "php": "PHP", "rust": "Rust", "swift": "Swift", "r": "R", "matlab": "MATLAB",
         "verilog": "Verilog", "vhdl": "VHDL", "scala": "Scala", "haskell": "Haskell", "perl": "Perl", "lua": "Lua"}
MAX_CODE_LINES = 400


def canonical(language: str | None) -> str | None:
    """The language a code item is in (aliases folded), or None for plain text / no language."""
    lang = (language or "").strip().lower()
    if not lang or lang in PLAIN:
        return None
    return HIGHLIGHTED.get(lang) or ALIASES.get(lang, lang)


def coloured(language: str | None) -> bool:
    return (language or "").strip().lower() in HIGHLIGHTED


def name_of(lang: str) -> str:
    return NAMES.get(lang, lang.upper() if len(lang) <= 3 else lang.title())


def mixed_indent(code: str) -> bool:
    """Whether some lines are indented with tabs and others with spaces (or one line mixes both)."""
    tabs = spaces = False
    for line in code.replace("\r\n", "\n").split("\n")[:MAX_CODE_LINES]:
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        if not lead or not line.strip():
            continue
        if "\t" in lead and " " in lead.replace("\t", ""):
            return True
        tabs = tabs or "\t" in lead
        spaces = spaces or (" " in lead and len(lead) >= 2)
    return tabs and spaces


def code_items(facts: Facts) -> list[tuple[int, Any]]:
    out = []
    for f in facts.scenes:
        if isinstance(f.scene, BoardScene):
            out += [(f.index, item) for item in f.scene.board if item.kind == BoardItemKind.code and item.code]
    return out


def languages(facts: Facts) -> OrderedDict[str, list[int]]:
    """{language: scene indexes} in order of first appearance (plain text left out)."""
    langs: OrderedDict[str, list[int]] = OrderedDict()
    for index, item in code_items(facts):
        lang = canonical(item.language)
        if lang:
            scenes = langs.setdefault(lang, [])
            if index not in scenes:
                scenes.append(index)
    return langs


def code_findings(facts: Facts) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    reported: set[tuple[int, str]] = set()
    indented: set[int] = set()
    for index, item in code_items(facts):
        lang = canonical(item.language)
        if lang and not coloured(item.language) and (index, lang) not in reported:
            reported.add((index, lang))
            supported = ", ".join(sorted({name_of(v) for v in HIGHLIGHTED.values()}))
            msg = (f"{upper_first(lesson.label(index))} shows {name_of(lang)} code, which the player and the video show "
                   f"without colours (coloured: {supported}). That is fine when it is the language you teach; "
                   "otherwise check the language name of the code item.")
            out.append(Finding(lint_issue("code.language_unhighlighted", msg, "warning", scene_id=lesson.scene_id(index))))
        if index not in indented and mixed_indent(item.code or ""):
            indented.add(index)
            msg = (f"The code in {lesson.label(index)} indents some lines with tabs and others with spaces, so its "
                   "indentation may look uneven (and in Python it can change what the code means). Use spaces only.")
            out.append(Finding(lint_issue("code.mixed_indentation", msg, "warning", scene_id=lesson.scene_id(index),
                                          fixable=True)))
    langs = languages(facts)
    if len(langs) >= 2:
        second = list(langs.values())[1]
        msg = ("The lesson shows code in several languages ("
               + ", ".join(f"{name_of(lang)} in {lesson.where(sc)}" for lang, sc in list(langs.items())[:4])
               + "): make sure learners know which language each example is in.")
        out.append(Finding(lint_issue("code.mixed_languages", msg, scene_id=lesson.scene_id(second[0]))))
    return out


def registry_entries(facts: Facts) -> list[dict[str, Any]]:
    out = []
    for lang, scenes in list(languages(facts).items())[:12]:
        out.append({"language": lang, "name": name_of(lang), "coloured": lang in set(HIGHLIGHTED.values()),
                    "scene_ids": [facts.lesson.scene_id(i) for i in scenes[:20]]})
    return out
