"""Text -> speakable text for TTS (captions keep the written form).

Pipeline order (``ARCHITECTURE`` §6): ``normalize_for_speech(apply_lexicon(beat.spoken or beat.narration))``.

``normalize_for_speech``:
1. rich-lite markup is removed (``**bold**``, ``*italic*``, `` `code` ``, ``[[keyword]]``), ``$latex$``
   spans are spoken (``mathspeech``), stray raw LaTeX (``\\frac{a}{b}``, ``\\alpha``, ``x^2``, ``V_1``)
   is spoken too; units after numbers in raw LaTeX (``4.7\\,k\\Omega`` -> "4.7 kilo ohms",
   ``5\\,\\mu F`` -> "5 micro F") and subscript labels (``V_{out}`` -> "V sub out") read naturally;
2. symbols become words: ``= + − ± × ÷ · ≈ ≠ ≤ ≥ → ∞ √ % °C`` Greek letters, ``Ω`` after a number
   ("ohms"), currency;
3. whitespace is collapsed.

Words come from per-language tables (en, ta, hi; other languages use English words).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .mathspeech import latex_to_speech, subscript_text
from .speech_words import PREFIX_KEYS, UNICODE_GREEK, greek, table, templates

_DOLLAR = "\ue000"  # private-use placeholders for escaped characters
_STAR = "\ue001"

_MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MATH_INLINE = re.compile(r"(?<!\\)\$(?!\s)([^$\n]+?)(?<!\s)\$")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_CODE = re.compile(r"`([^`\n]*)`")
_KEYWORD = re.compile(r"\[\[(.+?)\]\]")
_RAW_LATEX = re.compile(r"\\[A-Za-z]+(?:\s*\[[^\]]*\])?(?:\s*\{(?:[^{}]|\{[^{}]*\})*\})*")
_POWER = re.compile(r"(?<![\w\\])([A-Za-z0-9]+|\([^()\s]{1,20}\))\^(\{[^{}]{1,40}\}|-?[A-Za-z0-9]+)")
_SUBSCRIPT = re.compile(r"(?<![\w\\])([A-Za-z])_(\{[^{}]{1,20}\}|[A-Za-z0-9]{1,3})(?![\w])")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿⁱ", "0123456789-+ni")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋ₙᵢ", "0123456789+-ni")
_SUPER_RUN = re.compile(r"([A-Za-z0-9)])([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿⁱ]+)")
_SUB_RUN = re.compile(r"([A-Za-z)])([₀₁₂₃₄₅₆₇₈₉₊₋ₙᵢ]+)")
_OHMS = re.compile(r"(\d)\s*([kKMGm\u00b5\u03bc]?)\s*[\u03a9\u2126]")  # \u2126 = ohm sign
# Raw LaTeX units after a number (outside $...$): 10\Omega, 4.7\,k\Omega, 2.2\,\text{k}\Omega, 5\,\mu F
_LATEX_GAP = r"\s*(?:(?:\\[,;:! ]|~)\s*)*"
_RAW_OHMS = re.compile(
    r"(\d)" + _LATEX_GAP
    + r"(\\(?:text|textrm|mathrm|mbox)\s*\{\s*(?:[kKMGmnp\u00b5\u03bc]|\\mu)\s*\}|\\mu(?![A-Za-z])|[kKMGmnp\u00b5\u03bc])?"
    + _LATEX_GAP + r"\\Omega(?![A-Za-z])"
)
_RAW_MICRO_UNIT = re.compile(r"(\d)" + _LATEX_GAP + r"\\mu(?![A-Za-z])\s*(?=[A-Za-z])")
_PREFIX_LETTER = re.compile(r"\\mu|[kKMGmnp\u00b5\u03bc]")
_MICRO_UNIT = re.compile(r"(\d)\s*[µμ](?=[A-Za-z])")
_CELSIUS = re.compile(r"\s*°\s*C\b")
_FAHRENHEIT = re.compile(r"\s*°\s*F\b")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?])")
_WS = re.compile(r"\s+")
_NUMBER = r"\d+(?:,\d+)*(?:\.\d+)?"


def _spoken_math(latex: str, language: str) -> str:
    return f" {latex_to_speech(latex, language)} "


def _strip_markup(text: str, language: str) -> str:
    text = text.replace("\\$", _DOLLAR).replace("\\*", _STAR)
    text = _MATH_BLOCK.sub(lambda m: _spoken_math(m.group(1), language), text)
    text = _MATH_INLINE.sub(lambda m: _spoken_math(m.group(1), language), text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    text = _KEYWORD.sub(r"\1", text)
    return text


def _raw_units(text: str, language: str) -> str:
    r"""Units after numbers in raw LaTeX, before generic macro handling turns ``\Omega`` into "omega"."""
    w = table(language)

    def ohms(m: re.Match[str]) -> str:
        prefix = ""
        if m.group(2):
            found = _PREFIX_LETTER.search(m.group(2))
            letter = found.group(0) if found else ""
            key = "prefix_u" if letter == "\\mu" else PREFIX_KEYS.get(letter, "")
            prefix = f"{w[key]} " if key else ""
        return f"{m.group(1)} {prefix}{w['ohms']} "

    text = _RAW_OHMS.sub(ohms, text)
    return _RAW_MICRO_UNIT.sub(lambda m: f"{m.group(1)} {w['prefix_u']} ", text)


def _subscript(m: re.Match[str], language: str) -> str:
    sub = m.group(2).strip("{}")
    if len(sub) >= 2 and sub.isascii() and sub.isalpha():
        sub = subscript_text(sub)  # "out" stays a word, "ij" becomes "i j"
    return " " + templates(language)["sub"].format(base=m.group(1), sub=sub) + " "


def _raw_maths(text: str, language: str) -> str:
    text = _raw_units(text, language)
    text = _RAW_LATEX.sub(lambda m: _spoken_math(m.group(0), language), text)
    text = _SUPER_RUN.sub(lambda m: _spoken_math(f"{m.group(1)}^{{{m.group(2).translate(_SUPERSCRIPTS)}}}",
                                                 language), text)
    text = _SUB_RUN.sub(lambda m: f"{m.group(1)} {' '.join(m.group(2).translate(_SUBSCRIPTS))} ", text)
    text = _POWER.sub(lambda m: _spoken_math(m.group(0), language), text)
    text = _SUBSCRIPT.sub(lambda m: _subscript(m, language), text)
    return text


def _symbols(text: str, language: str) -> str:
    w = table(language)
    g = greek(language)

    def word(key: str) -> str:
        return f" {w[key]} "

    text = _CELSIUS.sub(word("degrees_celsius"), text)
    text = _FAHRENHEIT.sub(word("degrees_fahrenheit"), text)
    text = text.replace("°", word("degrees"))
    text = _OHMS.sub(lambda m: f"{m.group(1)} {w[PREFIX_KEYS[m.group(2)]] + ' ' if m.group(2) else ''}{w['ohms']} ", text)
    text = _MICRO_UNIT.sub(lambda m: f"{m.group(1)} {w['prefix_u']} ", text)
    for ch, name in UNICODE_GREEK.items():
        if ch in text:
            text = text.replace(ch, f" {g.get(name, name)} ")
    text = re.sub(r"₹\s*(" + _NUMBER + ")", lambda m: f"{m.group(1)} {w['rupees']}", text)
    text = re.sub(_DOLLAR + r"\s*(" + _NUMBER + ")", lambda m: f"{m.group(1)} {w['dollars']}", text)
    text = text.replace(_DOLLAR, word("dollar")).replace("₹", f" {w['rupees']} ")
    multi = (
        ("<=", "le"), (">=", "ge"), ("!=", "not_equal"), ("==", "equals"), ("=>", "implies"), ("->", "leads_to"),
        ("<->", "iff"),
    )
    for sym, key in sorted(multi, key=lambda x: -len(x[0])):
        text = text.replace(f" {sym} ", word(key))
    single = {
        "±": "plus_minus", "∓": "minus_plus", "×": "times", "÷": "divided_by", "⋅": "times", "≈": "approx_equal",
        "≅": "approx_equal", "≠": "not_equal", "≤": "le", "⩽": "le", "≥": "ge", "⩾": "ge", "→": "leads_to",
        "⟶": "leads_to", "⇒": "implies", "⟹": "implies", "⇔": "iff", "↔": "iff", "∞": "infinity", "∑": "sum",
        "∫": "integral", "∂": "partial", "∇": "nabla", "∝": "proportional", "≡": "equivalent", "∈": "element_of",
        "∉": "not_element_of", "−": "minus", "≪": "much_lt", "≫": "much_gt", "∠": "angle", "⊥": "perpendicular",
        "∥": "parallel", "%": "percent", "=": "equals", "&": "and",
    }
    for sym, key in single.items():
        if sym in text:
            text = text.replace(sym, word(key))
    text = re.sub(r"√\s*(\(([^()]{1,40})\)|[\w.]+)",
                  lambda m: " " + templates(language)["sqrt"].format(x=m.group(2) or m.group(1)) + " ", text)
    text = text.replace("√", " " + templates(language)["sqrt"].format(x="").strip() + " ")
    text = re.sub(r"(?<=[\w)])\s*·\s*(?=[\w(])", word("times"), text)
    text = re.sub(r"(?<=[\w)])\s*\+\s*(?=[\w(])", word("plus"), text)
    text = re.sub(r"(?<=\+)\+", f" {w['plus']}", text)  # C++ -> C plus plus
    text = re.sub(r"(?<=\w)\+", word("plus"), text)
    text = re.sub(r"\+(?=\d)", word("plus"), text)
    text = re.sub(r"(?<![\w.])-(?=\d)", f"{w['minus']} ", text)
    text = re.sub(r"(?<=\b[A-Za-z0-9])\s+-\s+(?=[A-Za-z0-9]\b)", word("minus"), text)
    text = re.sub(r"(?<=\d)\s+-\s+(?=\d)", word("minus"), text)
    text = re.sub(r"(?:(?<=[\d)])|(?<=\b[A-Za-z]))\s*\*\s*(?=[\d(]|[A-Za-z]\b)", word("times"), text)
    text = re.sub(r"(?<=\s)<(?=\s)", w["lt"], text)
    text = re.sub(r"(?<=\s)>(?=\s)", w["gt"], text)
    text = re.sub(r"~\s*(?=\d)", f"{w['approximately']} ", text)
    return text


def _cleanup(text: str) -> str:
    text = text.replace(_STAR, " star ")
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)  # markdown headings
    text = re.sub(r"\[\[|\]\]|\*+|`+|(?<!\w)_+|_+(?!\w)", " ", text)
    text = text.replace("\\", " ")
    text = _WS.sub(" ", text)
    text = _SPACE_PUNCT.sub(r"\1", text)
    return text.strip()


def normalize_for_speech(text: str, language: str) -> str:
    """Speakable version of ``text`` for a TTS voice in ``language`` (BCP-47, e.g. ``ta-IN``)."""
    if not text:
        return ""
    out = _strip_markup(text, language)
    out = _raw_maths(out, language)
    out = _symbols(out, language)
    return _cleanup(out)


# --- lexicon -----------------------------------------------------------------------------

_WORD_CHARS = r"\w\u0300-\u036F\u0900-\u0DFF"


def _entry_fields(entry: Any) -> tuple[str, str, str | None]:
    if isinstance(entry, dict):
        return str(entry.get("written", "")), str(entry.get("spoken", "")), entry.get("language")
    return str(entry.written), str(entry.spoken), getattr(entry, "language", None)


def _language_matches(entry_lang: str | None, language: str) -> bool:
    if not entry_lang:
        return True
    a = entry_lang.replace("_", "-").lower()
    b = (language or "").replace("_", "-").lower()
    if a == b:
        return True
    return "-" not in a and a == b.split("-")[0]


def _norm_key(text: str) -> str:
    key = _WS.sub(" ", text.strip())
    return key.casefold() if key.isascii() else key


def apply_lexicon(text: str, lexicon: Iterable[Any], language: str) -> str:
    """Replace written forms with spoken forms (pronunciation rules) in one pass.

    Longest written form first; whole words only; case-insensitive for ASCII terms, exact for
    non-ASCII terms. Entries with ``language=None`` apply to every language; ``"ta"`` matches
    ``"ta-IN"``. Replacements are never re-processed by other entries.
    """
    if not text:
        return text
    mapping: dict[str, str] = {}
    for entry in lexicon or ():
        written, spoken, lang = _entry_fields(entry)
        if not written.strip() or not _language_matches(lang, language):
            continue
        mapping.setdefault(_norm_key(written), spoken)
    if not mapping:
        return text
    alts = []
    for key in sorted(mapping, key=lambda k: (-len(k), k)):
        pattern = r"\s+".join(re.escape(part) for part in key.split(" "))
        alts.append(f"(?i:{pattern})" if key.isascii() else pattern)
    regex = re.compile(rf"(?<![{_WORD_CHARS}])(?:{'|'.join(alts)})(?![{_WORD_CHARS}])")

    def repl(m: re.Match[str]) -> str:
        return mapping.get(_norm_key(m.group(0)), m.group(0))

    return regex.sub(repl, text)
