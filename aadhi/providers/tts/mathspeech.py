"""LaTeX(-ish) maths -> spoken words (``\\frac{a}{b}`` -> "a over b", ``x^2`` -> "x squared" ...).

A small tokenizer + recursive-descent reader covering the macros used in lecture boards. Unknown
macros are spoken by name. Engineering notation is read naturally: ``V_{out}`` -> "V sub out",
``4.7\\,\\text{k}\\Omega`` -> "4.7 kilo ohms", ``5\\,\\mu F`` -> "5 micro F".

``latex_to_speech`` never raises: nesting deeper than ``MAX_DEPTH`` (or any unexpected parser
failure) falls back to the fragment with its markup characters removed. Lookahead (``^2``/``^3``
detection) scans tokens without parsing, so the work stays linear in the input size.
"""

from __future__ import annotations

import logging
import re

from .speech_words import PREFIX_KEYS, greek, table, templates

log = logging.getLogger(__name__)

MAX_DEPTH = 64  # nested arguments/groups before giving up on structure (recursion guard)

_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\d+(?:\.\d+)?|\s+|.", re.DOTALL)
_NUMERIC = re.compile(r"\d+(?:\.\d+)?|\.\d+")

_SYMBOL_CMDS = {
    "times": "times", "cdot": "times", "ast": "times", "div": "divided_by", "pm": "plus_minus",
    "mp": "minus_plus", "le": "le", "leq": "le", "leqslant": "le", "ge": "ge", "geq": "ge", "geqslant": "ge",
    "neq": "not_equal", "ne": "not_equal", "approx": "approx_equal", "simeq": "approx_equal",
    "sim": "approximately", "equiv": "equivalent", "propto": "proportional", "to": "leads_to",
    "rightarrow": "leads_to", "longrightarrow": "leads_to", "Rightarrow": "implies", "implies": "implies",
    "Longrightarrow": "implies", "iff": "iff", "Leftrightarrow": "iff", "infty": "infinity", "sum": "sum",
    "prod": "product", "int": "integral", "iint": "integral", "oint": "integral", "partial": "partial",
    "nabla": "nabla", "in": "element_of", "notin": "not_element_of", "subset": "subset", "subseteq": "subset",
    "cup": "union", "cap": "intersection", "forall": "for_all", "exists": "exists", "angle": "angle",
    "perp": "perpendicular", "parallel": "parallel", "ll": "much_lt", "gg": "much_gt", "lt": "lt", "gt": "gt",
    "ldots": "dots", "cdots": "dots", "dots": "dots", "degree": "degrees", "circ": "degrees",
    "prime": "prime",
}
_FUNCTIONS = {
    "sin", "cos", "tan", "sec", "csc", "cot", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "log", "ln",
    "exp", "lim", "max", "min", "det", "gcd", "mod", "bmod", "pmod",
}
_BIG_OPS = {"sum": "op_sum", "prod": "op_product", "int": "op_integral", "iint": "op_integral", "oint": "op_integral"}
_SILENT = {
    "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "displaystyle", "textstyle",
    "limits", "nolimits", "quad", "qquad", "hfill", "mathstrut", "rm", "bf", "it", "cal",
}
_TEXT_ARG = {
    "text", "textrm", "textbf", "textit", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathcal",
    "operatorname", "mbox", "boldsymbol", "mathbb", "underline", "displaystyle", "emph",
}
_UPRIGHT = {"\\text", "\\textrm", "\\mathrm", "\\mbox", "\\rm", "\\mathit", "\\textit"}  # \text{k}\Omega
_CHAR_WORDS = {
    "=": "equals", "+": "plus", "-": "minus", "−": "minus", "*": "times", "×": "times", "·": "times",
    "/": "over", "<": "lt", ">": "gt", "±": "plus_minus", "≤": "le", "≥": "ge", "≠": "not_equal",
    "≈": "approx_equal", "→": "leads_to", "∞": "infinity", "!": "factorial", "'": "prime", "%": "percent",
}
_OHM_TOKENS = {"\\Omega", "\u03a9", "\u2126"}  # \Omega, Greek capital omega, ohm sign
_MICRO_TOKENS = {"\\mu", "\u00b5", "\u03bc"}  # \mu, micro sign, Greek small mu

# Subscript labels spoken as one word ("V sub out", not "V sub o u t").
_SUBSCRIPT_WORDS = {
    "in", "eq", "th", "on", "up", "min", "max", "off", "out", "ref", "avg", "sat", "eff", "tot", "net", "sec",
    "pri", "rev", "fwd", "act", "app", "mid", "low", "high", "peak", "load", "total", "ideal", "rated",
}
_INDEX_LETTERS = frozenset("ijklmn")  # a_{ij}, T_{ijk}: matrix/tensor indices stay letter by letter
_VOWELS = frozenset("aeiouy")


def subscript_text(letters: str) -> str:
    """Spoken form of an all-letter subscript: a label word ("out", "max", "CE") or spaced letters
    for index lists and unpronounceable clusters ("i j", "r m s")."""
    if len(letters) < 2:
        return letters
    low = letters.lower()
    if low in _SUBSCRIPT_WORDS or letters.isupper():
        return letters
    if len(letters) == 2 or set(low) <= _INDEX_LETTERS or not (set(low) & _VOWELS):
        return " ".join(letters)
    return letters


class _TooDeep(Exception):
    """Nesting exceeded ``MAX_DEPTH``."""


class _Reader:
    def __init__(self, latex: str, language: str) -> None:
        self.toks = [t for t in _TOKEN.findall(latex or "")]
        self.i = 0
        self.depth = 0
        self.words = table(language)
        self.tpl = templates(language)
        self.greek = greek(language)

    # --- token helpers ------------------------------------------------------------
    def _skip_ws(self, j: int) -> int:
        while j < len(self.toks) and self.toks[j].isspace():
            j += 1
        return j

    def _tok(self, j: int) -> str | None:
        return self.toks[j] if 0 <= j < len(self.toks) else None

    def peek(self) -> str | None:
        self.i = self._skip_ws(self.i)
        return self._tok(self.i)

    def next(self) -> str | None:
        tok = self.peek()
        if tok is not None:
            self.i += 1
        return tok

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise _TooDeep

    def _brace_span(self, j: int) -> tuple[list[str], int] | None:
        """Tokens inside the brace group starting at ``j`` and the index after its ``}`` (scan only)."""
        if self._tok(j) != "{":
            return None
        depth, out = 0, []
        for k in range(j, len(self.toks)):
            tok = self.toks[k]
            if tok == "{":
                depth += 1
                if depth == 1:
                    continue
            elif tok == "}":
                depth -= 1
                if depth == 0:
                    return out, k + 1
            out.append(tok)
        return out, len(self.toks)

    def raw_group(self) -> str:
        """Raw text of the next argument, without consuming or parsing it (recognises ^2, ^3 ...)."""
        j = self._skip_ws(self.i)
        span = self._brace_span(j)
        if span is None:
            return self._tok(j) or ""
        return "".join(span[0]).strip().strip("{}").strip()

    # --- grammar ------------------------------------------------------------------
    def group(self) -> str:
        """Tokens up to the matching ``}`` (the ``{`` was consumed)."""
        parts = self.seq(stop="}")
        if self.peek() == "}":
            self.i += 1
        return parts

    def arg(self) -> str:
        self._enter()
        try:
            tok = self.next()
            if tok is None:
                return ""
            if tok == "{":
                return self.group()
            if tok.startswith("\\"):
                return self.command(tok[1:])
            return self.char(tok)
        finally:
            self.depth -= 1

    def text_arg(self) -> str:
        """Verbatim argument of ``\\text{...}``-like macros (words, not maths)."""
        span = self._brace_span(self._skip_ws(self.i))
        if span is None:
            return self.arg()
        inner, self.i = span
        return " ".join("".join(inner).split())

    def optional(self) -> str | None:
        if self.peek() != "[":
            return None
        self.i += 1
        parts: list[str] = []
        while (tok := self.peek()) is not None and tok != "]":
            parts.append(self.atom())
        if self.peek() == "]":
            self.i += 1
        return " ".join(p for p in parts if p)

    def seq(self, stop: str | None = None) -> str:
        out: list[str] = []
        prev_numeric = False
        while (tok := self.peek()) is not None and tok != stop:
            if tok == "}" and stop is None:
                self.i += 1
                continue
            if prev_numeric:
                unit = self.unit_after_number()
                if unit:
                    out.append(unit)
                    prev_numeric = False
                    continue
            base = self.atom()
            word = self.scripts(base)
            if word:
                out.append(word)
            prev_numeric = bool(_NUMERIC.fullmatch(base or ""))  # "10^3 \Omega" -> "10 cubed ohms"
        return " ".join(out)

    def _prefix_at(self, j: int) -> tuple[str | None, int]:
        """SI prefix at token ``j`` (``k``, ``M``, ``\\mu``, ``\\text{k}`` ...): ``(words key, next j)``."""
        tok = self._tok(j)
        if tok is None:
            return None, j
        if tok in _MICRO_TOKENS:
            return "prefix_u", j + 1
        if tok in PREFIX_KEYS:
            return PREFIX_KEYS[tok], j + 1
        if tok in _UPRIGHT:
            span = self._brace_span(self._skip_ws(j + 1))
            if span is not None:
                inner = [t for t in span[0] if not t.isspace()]
                if len(inner) == 1 and (inner[0] in PREFIX_KEYS or inner[0] in _MICRO_TOKENS):
                    key = "prefix_u" if inner[0] in _MICRO_TOKENS else PREFIX_KEYS[inner[0]]
                    return key, span[1]
        return None, j

    def unit_after_number(self) -> str | None:
        """After a number: ``[prefix]\\Omega`` -> "[kilo] ohms"; ``\\mu`` before a unit letter -> "micro".

        Consumes the recognised tokens; returns ``None`` (consuming nothing) otherwise.
        """
        start = self._skip_ws(self.i)
        key, j = self._prefix_at(start)
        after = self._skip_ws(j)
        if self._tok(after) in _OHM_TOKENS:
            self.i = after + 1
            ohms = self.words["ohms"]
            return f"{self.words[key]} {ohms}" if key else ohms
        nxt = self._tok(after)
        if key == "prefix_u" and nxt is not None and len(nxt) == 1 and nxt.isalpha():
            self.i = j  # the unit letter is read next ("5 micro F")
            return self.words["prefix_u"]
        return None

    def subscript(self) -> str:
        """Argument of ``_``: an all-letter brace group is a label ("out"), else spoken normally."""
        span = self._brace_span(self._skip_ws(self.i))
        if span is not None:
            letters = "".join(t for t in span[0] if not t.isspace())
            if len(letters) >= 2 and letters.isascii() and letters.isalpha():
                self.i = span[1]
                return subscript_text(letters)
        return self.arg()

    def scripts(self, base: str) -> str:
        """Apply any ``^``/``_`` that follow ``base``."""
        while (tok := self.peek()) in ("^", "_"):
            self.i += 1
            if tok == "_":
                sub = self.subscript()
                base = self.tpl["sub"].format(base=base, sub=sub).strip()
                continue
            raw_norm = self.raw_group().replace(" ", "")
            exp = self.arg()
            if raw_norm == "2":
                base = self.tpl["squared"].format(base=base)
            elif raw_norm == "3":
                base = self.tpl["cubed"].format(base=base)
            elif raw_norm in ("\\circ", "\\degree", "o"):
                base = self.tpl["degrees_of"].format(base=base)
            elif raw_norm == "T":
                base = f"{base} {self.words['transpose']}"
            elif raw_norm in ("\\prime", "'"):
                base = f"{base} {self.words['prime']}"
            elif raw_norm in ("*", "\\ast"):
                base = f"{base} {self.words['star']}"
            else:
                base = self.tpl["power"].format(base=base, exp=exp)
            base = base.strip()
        return base

    def atom(self) -> str:
        self._enter()
        try:
            tok = self.next()
            if tok is None:
                return ""
            if tok == "{":
                return self.group()
            if tok.startswith("\\"):
                return self.command(tok[1:])
            return self.char(tok)
        finally:
            self.depth -= 1

    def char(self, tok: str) -> str:
        if tok in ("(", ")", "[", "]", "|", "&", "{", "}", "$", "~"):
            return ""
        if tok in (",", ";", ":"):
            return ","
        key = _CHAR_WORDS.get(tok)
        if key:
            return self.words.get(key, key)
        return tok

    def command(self, name: str) -> str:
        w = self.words
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, den = self.arg(), self.arg()
            return self.tpl["frac"].format(num=num, den=den)
        if name == "sqrt":
            n = (self.optional() or "").strip()
            x = self.arg()
            if n == "3":
                return self.tpl["cbrt"].format(x=x)
            if n and n != "2":
                return self.tpl["root"].format(n=n, x=x)
            return self.tpl["sqrt"].format(x=x)
        if name in _BIG_OPS or name == "lim":
            return self.big_operator(name)
        if name in _TEXT_ARG:
            return self.text_arg() if name.startswith("text") or name in ("mathrm", "operatorname", "mbox") else self.arg()
        if name in ("vec", "overrightarrow"):
            return f"{w['vector']} {self.arg()}"
        if name in ("hat", "widehat"):
            return f"{self.arg()} {w['hat']}"
        if name in ("bar", "overline"):
            return f"{self.arg()} {w['bar']}"
        if name in ("dot",):
            return f"{self.arg()} {w['dot']}"
        if name in _SILENT:
            if name in ("left", "right") and self.peek() in ("(", ")", "[", "]", "|", ".", "\\{", "\\}"):
                self.i += 1
            return ""
        if name in _SYMBOL_CMDS:
            return w.get(_SYMBOL_CMDS[name], name)
        if name in _FUNCTIONS:
            return w.get(f"fn_{name.lstrip('bp')}" if name in ("bmod", "pmod") else f"fn_{name}", name)
        if name in self.greek:
            return self.greek[name]
        return name

    def big_operator(self, name: str) -> str:
        r"""``\sum_{i=1}^{n}`` -> "sum from i equals 1 to n of"; ``\lim_{x \to 0}`` -> "limit as x tends to 0 of"."""
        w = self.words
        lo = hi = ""
        while (tok := self.peek()) in ("_", "^"):
            self.i += 1
            value = self.arg()
            if tok == "_":
                lo = value
            else:
                hi = value
        if name == "lim":
            if lo:
                return self.tpl["lim"].format(lo=lo.replace(w["leads_to"], w["tends_to"]))
            return w["fn_lim"]
        op = w.get(_BIG_OPS[name], name)
        if lo and hi:
            return self.tpl["big_range"].format(op=op, lo=lo, hi=hi)
        if lo or hi:
            return self.tpl["big_lower"].format(op=op, lo=lo or hi)
        return w[_SYMBOL_CMDS[name]]

    def escaped(self, ch: str) -> str:
        return {"%": self.words["percent"], "$": self.words["dollar"], "&": self.words["and"]}.get(ch, "")


def plain_text(latex: str) -> str:
    """Fallback rendering: macro names as words, markup characters removed."""
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", latex or "")
    text = re.sub(r"[{}\\^_$&~]", " ", text)
    return " ".join(text.split())


def latex_to_speech(latex: str, language: str = "en") -> str:
    """Spoken rendering of a LaTeX fragment (no surrounding ``$``). Never raises."""
    try:
        reader = _Reader(latex, language)
        # Escaped single characters (\%, \$, \,) are tokens of length 2 starting with a backslash.
        toks: list[str] = []
        for tok in reader.toks:
            if len(tok) == 2 and tok.startswith("\\") and not tok[1].isalpha():
                spoken = reader.escaped(tok[1])
                toks.append(f" {spoken} " if spoken else " ")
            elif tok == "~":  # non-breaking space
                toks.append(" ")
            else:
                toks.append(tok)
        reader.toks = toks
        text = reader.seq()
    except (_TooDeep, RecursionError):
        text = plain_text(latex)
    except Exception:  # noqa: BLE001 - speech must never fail because of odd board text
        log.warning("mathspeech: could not parse a maths fragment; speaking it as plain text", exc_info=True)
        text = plain_text(latex)
    return re.sub(r"\s+", " ", re.sub(r"\s+,", ",", text)).strip(" ,")
