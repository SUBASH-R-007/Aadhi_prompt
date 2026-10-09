"""Formula symbols across the lesson, read from the typed legends (``BoardItem.variables``), any language.

* ``formula.symbol_conflict`` (warning) — one symbol given two meanings ("V" for voltage in scene 3 and
  for volume in scene 7). A meaning that contains the other is the same ("mass", "mass of the ball").
* ``formula.notation_mismatch`` (info) — one quantity written with two symbols ("v" and "V" for velocity).
* ``formula.legend_missing`` (info) — a formula without a legend whose letters are not explained in the
  scene (narration, board) or by another formula's legend.

Units are never compared (``FormulaVariable.unit`` is separate from the symbol), and the mathematics
itself is never judged. Symbols are read from LaTeX with bounded patterns (commands and text blocks
removed; sub- and superscripts belong to their letter).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from ...schemas.screenplay import BoardItemKind, BoardScene
from .extract import Facts
from .report import Finding, lint_issue
from .text import plural, quote

MAX_SYMBOLS = 40
MAX_DISTINCT_MEANINGS = 3  # meanings kept per symbol when checking for a conflict: the message names at most 3
MAX_LATEX = 1200
MAX_EXPLANATIONS = 300  # "X is …" candidates read per text
_TEX_TEXT = re.compile(r"\\(?:text|mathrm|operatorname|textbf|textit|mbox|unit|si)\s{0,4}\{[^{}]{0,200}\}")
_TEX_SCRIPT = re.compile(r"[_^](?:\{[^{}]{0,60}\}|\\?[A-Za-z0-9])")
_TEX_GREEK = re.compile(r"\\(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|lambda|rho|sigma|tau|phi|varphi|"
                        r"chi|psi|omega|Gamma|Delta|Theta|Lambda|Sigma|Phi|Psi)(?![A-Za-z])")
_TEX_COMMAND = re.compile(r"\\[A-Za-z]{1,30}")
_TEX_DIFFERENTIAL = re.compile(r"(?<![A-Za-z])d(?=[A-Za-z])")
_LETTER = re.compile(r"[A-Za-z]")
_MEANING_WORDS = re.compile(r"\w+")
_ARTICLES = frozenset({"the", "a", "an", "of"})
_SPACE = re.compile(r"\s+")
_OUTER_BRACES = re.compile(r"^\{([^{}]*)\}$")


def norm_symbol(symbol: str) -> str:
    """'{V}', ' V ' and 'V' are one symbol; 'V_{1}' and 'V_1' too (case and bold are kept: they are notation)."""
    s = _SPACE.sub("", symbol or "")[:60]
    m = _OUTER_BRACES.match(s)
    if m:
        s = m.group(1)
    return re.sub(r"_\{([^{}])\}", r"_\1", s)


def meaning_key(meaning: str) -> str:
    words = [plural(w) for w in _MEANING_WORDS.findall(str(meaning or "")[:200].casefold()) if w not in _ARTICLES]
    return " ".join(words)


def same_meaning(a: str, b: str) -> bool:
    wa, wb = set(a.split()), set(b.split())
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def base_letters(latex: str) -> list[str]:
    """The letters (and lower-case Greek letters) a formula uses as symbols, in order of appearance."""
    s = _TEX_TEXT.sub(" ", (latex or "")[:MAX_LATEX])
    s = _TEX_SCRIPT.sub(" ", s)
    found: list[str] = []
    for m in _TEX_GREEK.finditer(s):
        name = "\\" + m.group(1)
        if name not in found:
            found.append(name)
    s = _TEX_COMMAND.sub(" ", s)
    s = _TEX_DIFFERENTIAL.sub(" ", s)
    for ch in _LETTER.findall(s):
        if ch not in found:
            found.append(ch)
    return found[:12]


def _symbol_base(symbol_latex: str) -> str:
    """The letter a legend symbol explains ('V_{in}' -> 'V', '\\omega' -> '\\omega')."""
    s = norm_symbol(symbol_latex)
    m = _TEX_GREEK.match(s)
    if m:
        return "\\" + m.group(1)
    m = re.match(r"(?:\\[A-Za-z]{1,10}\{)?([A-Za-z])", s)
    return m.group(1) if m else s


def legends(facts: Facts) -> list[tuple[int, Any, Any]]:
    """(scene index, formula item, variable) for every legend entry of the lesson."""
    out = []
    for f in facts.scenes:
        if isinstance(f.scene, BoardScene):
            for item in f.scene.board:
                if item.kind == BoardItemKind.formula:
                    for v in item.variables:
                        out.append((f.index, item, v))
    return out


def symbol_meanings(facts: Facts) -> OrderedDict[str, OrderedDict[str, dict[str, Any]]]:
    """{symbol: {meaning key: {"meaning", "unit", "scenes"}}} from every legend (bounded)."""
    symbols: OrderedDict[str, OrderedDict[str, dict[str, Any]]] = OrderedDict()
    for index, _item, v in legends(facts):
        sym = norm_symbol(v.symbol_latex)
        mk = meaning_key(v.meaning)
        if not sym or not mk or (sym not in symbols and len(symbols) >= MAX_SYMBOLS):
            continue
        rec = symbols.setdefault(sym, OrderedDict()).setdefault(mk, {"meaning": v.meaning, "unit": v.unit, "scenes": []})
        if index not in rec["scenes"]:
            rec["scenes"].append(index)
    return symbols


_EXPLAIN_VERB = re.compile(r"\b(?:is|are|stands\s{1,3}for|represents|denotes|means)\b|[=:]")
_EXPLAINED = re.compile(r"(?<![\w\\])\\?([A-Za-z]{1,10})\s{0,3}$")


def explained_names(text: str) -> set[str]:
    """Letters and Greek names the text explains ("V is the voltage", "omega = angular speed"): anchored on the
    verb, then the word just before it (a 16-character window)."""
    out: set[str] = set()
    for n, m in enumerate(_EXPLAIN_VERB.finditer(text)):
        if n >= MAX_EXPLANATIONS:
            break
        found = _EXPLAINED.search(text[max(0, m.start() - 16):m.start()])
        if found:
            name = found.group(1)
            out.update({name} if len(name) == 1 else {"\\" + name, "\\" + name.lower()})
    return out


def formula_findings(facts: Facts) -> list[Finding]:
    out: list[Finding] = []
    lesson = facts.lesson
    symbols = symbol_meanings(facts)
    for sym, meanings in symbols.items():
        # Word sets are built once and at most MAX_DISTINCT_MEANINGS meanings are kept, so this is O(meanings) per
        # symbol (p18-adversarial-bounds). Matching stops at the first kept meaning (same_meaning: one word set
        # contains the other), so dropping the 4th and later distinct meanings changes no finding.
        distinct: list[tuple[frozenset[str], dict[str, Any]]] = []
        for mk, rec in meanings.items():
            words = frozenset(mk.split())
            for kept_words, kept in distinct:
                if words and kept_words and (words <= kept_words or kept_words <= words):
                    kept["scenes"].update(rec["scenes"])
                    break
            else:
                if len(distinct) < MAX_DISTINCT_MEANINGS:
                    distinct.append((words, {"meaning": rec["meaning"], "scenes": set(rec["scenes"])}))
        if len(distinct) >= 2:
            second = min(distinct[1][1]["scenes"])
            msg = (f"The symbol {quote(sym, 30)} stands for different things: "
                   + "; ".join(f"{quote(rec['meaning'])} in {lesson.where(rec['scenes'])}" for _w, rec in distinct[:3])
                   + ". If both are meant, say so when the second appears; otherwise use one meaning.")
            out.append(Finding(lint_issue("formula.symbol_conflict", msg, "warning", scene_id=lesson.scene_id(second))))
    quantities: OrderedDict[str, OrderedDict[str, set[int]]] = OrderedDict()
    for sym, meanings in symbols.items():
        for mk, rec in meanings.items():
            quantities.setdefault(mk, OrderedDict()).setdefault(sym, set()).update(rec["scenes"])
    for mk, syms in quantities.items():
        if len(syms) < 2:
            continue
        listed = list(syms.items())
        second = sorted(listed[1][1])
        meaning = symbols[listed[0][0]][mk]["meaning"]
        msg = (f"{quote(meaning)} is written with different symbols: "
               + "; ".join(f"{quote(s, 30)} in {lesson.where(sc)}" for s, sc in listed[:3])
               + ". Use one symbol for one quantity throughout the lesson.")
        out.append(Finding(lint_issue("formula.notation_mismatch", msg, scene_id=lesson.scene_id(second[0]))))
    explained = {_symbol_base(sym) for sym in symbols}
    for f in facts.scenes:
        if not isinstance(f.scene, BoardScene):
            continue
        names: set[str] | None = None
        for item in f.scene.board:
            if item.kind != BoardItemKind.formula or item.variables:
                continue
            letters = base_letters(item.latex or "")
            if len(letters) < 2:
                continue
            if names is None:
                names = explained_names(f"{f.visible} \n {f.narration}")
            here = names | explained_names(item.text)
            missing = [s for s in letters if s not in explained and s not in here][:6]
            if not missing:
                continue
            shown = ", ".join(quote(s, 20) for s in missing)
            msg = (f"A formula in {lesson.label(f.index)} uses {shown} without a legend, and the scene never says what "
                   f"{'they stand' if len(missing) > 1 else 'it stands'} for. Add a legend (symbol, meaning, unit) "
                   "so learners can read it.")
            out.append(Finding(lint_issue("formula.legend_missing", msg, scene_id=lesson.scene_id(f.index))))
            break  # one finding per scene
    return out


def registry_entries(facts: Facts) -> list[dict[str, Any]]:
    out = []
    for sym, meanings in symbol_meanings(facts).items():
        out.append({"symbol": sym, "meanings": [
            {"meaning": rec["meaning"], "unit": rec["unit"], "scene_ids": [facts.lesson.scene_id(i) for i in rec["scenes"][:12]]}
            for rec in list(meanings.values())[:4]]})
    return out
