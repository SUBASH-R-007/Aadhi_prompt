"""Rich-lite inline markup helpers (see ``aadhi.schemas.screenplay.RICH_LITE``).

Markup: ``**bold**``, ``*italic*``, ```code```, ``$latex$``, ``[[keyword]]``; ``\\$`` / ``\\*``
escape. Used to strip text for speech/captions, render the companion sheet safely (every
segment HTML-escaped) and mask protected spans during translation.
"""

from __future__ import annotations

import html
import re
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

SpanKind = Literal["text", "bold", "italic", "code", "math", "keyword"]


@dataclass(frozen=True)
class Span:
    kind: SpanKind
    text: str


# Bold is ``(?<![\w*])\*\*([^\s*](?:.*?[^\s])?)\*\*(?![\w*])`` and a keyword ``\[\[([^\]]+)\]\]``. As
# alternatives of one regex they scan to the end of the text for every opening marker that is never closed,
# which is quadratic on crafted input, so both are matched in two steps with the same result: the pattern
# finds the opening marker (``bopen`` / ``kopen``) and the closing one is looked up in positions found by one
# linear scan. A bold content ends at the first closing ``**`` (after a non-space character, not followed by
# a word character or ``*``) two or more characters after its first one, else right after it; a keyword at
# the next ``]``, which must be followed by another. The other alternatives stop at their next marker.
_TOKEN_RE = re.compile(
    r"(?P<esc>\\[$*])"
    r"|(?P<bopen>(?<![\w*])\*\*(?=[^\s*]))"
    r"|(?<![\w*])\*(?P<italic>[^*\s](?:[^*]*[^*\s])?)\*(?![\w*])"
    r"|`(?P<code>[^`]+)`"
    r"|(?<!\\)\$(?P<math>[^$]+?)(?<!\\)\$"
    r"|(?P<kopen>\[\[(?=[^\]]))",
    re.S,
)
_BOLD_CLOSE_RE = re.compile(r"(?=(?<=\S)\*\*(?![\w*]))")  # zero-width: every closing position
_BRACKET_RE = re.compile(r"\]")
_KINDS: tuple[SpanKind, ...] = ("italic", "code", "math")


def _tokens(text: str) -> Iterator[tuple[int, int, str, str]]:
    """``(start, end, kind, value)`` of each marker in order; ``kind`` ``esc`` has the escaped character."""
    closers: list[int] | None = None
    brackets: list[int] | None = None
    pos = 0
    while True:
        m = _TOKEN_RE.search(text, pos)
        if m is None:
            return
        if m.group("bopen") is not None:
            if closers is None:
                closers = [c.start() for c in _BOLD_CLOSE_RE.finditer(text)]
            first = m.end()  # the content's first character (non-space, not "*")
            # The optional tail of the content is greedy: the first closer two or more characters after
            # ``first`` wins; a one-character content only when no such closer exists.
            k = bisect_left(closers, first + 2)
            if k < len(closers):
                close = closers[k]
            elif closers and closers[-1] == first + 1:
                close = first + 1
            else:
                pos = m.start() + 1  # not bold; no other alternative starts with "**"
                continue
            yield m.start(), close + 2, "bold", text[first:close]
            pos = close + 2
            continue
        if m.group("kopen") is not None:
            if brackets is None:
                brackets = [b.start() for b in _BRACKET_RE.finditer(text)]
            k = bisect_left(brackets, m.end())  # the content's first character is not "]"
            if k < len(brackets) and text.startswith("]]", brackets[k]):
                yield m.start(), brackets[k] + 2, "keyword", text[m.end():brackets[k]]
                pos = brackets[k] + 2
            else:
                pos = m.start() + 1  # not a keyword; no other alternative starts with "["
            continue
        if m.group("esc"):
            yield m.start(), m.end(), "esc", m.group("esc")[1]
        else:
            kind = next(k for k in _KINDS if m.group(k) is not None)
            yield m.start(), m.end(), kind, m.group(kind)
        pos = m.end()


def tokenize(text: str) -> list[Span]:
    """Split rich-lite text into spans (unknown/unbalanced markers stay literal text)."""
    spans: list[Span] = []
    buf: list[str] = []
    pos = 0
    for start, end, kind, val in _tokens(text or ""):
        buf.append(text[pos:start])
        pos = end
        if kind == "esc":
            buf.append(val)
            continue
        if buf and "".join(buf):
            spans.append(Span("text", "".join(buf)))
        buf = []
        spans.append(Span(kind, val))  # type: ignore[arg-type]
    buf.append((text or "")[pos:])
    rest = "".join(buf)
    if rest:
        spans.append(Span("text", rest))
    return spans


_TEX_WORDS = {
    r"\times": " times ", r"\cdot": " times ", r"\div": " divided by ", r"\pm": " plus or minus ",
    r"\leq": " less than or equal to ", r"\le": " less than or equal to ", r"\geq": " greater than or equal to ",
    r"\ge": " greater than or equal to ", r"\neq": " not equal to ", r"\approx": " approximately ",
    r"\infty": " infinity ", r"\Omega": "ohm", r"\omega": "omega", r"\alpha": "alpha", r"\beta": "beta",
    r"\theta": "theta", r"\pi": "pi", r"\Delta": "delta", r"\delta": "delta", r"\mu": "mu", r"\lambda": "lambda",
    r"\sigma": "sigma", r"\rho": "rho",
}


def tex_to_plain(latex: str) -> str:
    """Rough readable text for a TeX fragment (captions/plain exports, not speech synthesis)."""
    s = latex
    for k in sorted(_TEX_WORDS, key=len, reverse=True):
        s = s.replace(k, _TEX_WORDS[k])
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\(?:mathrm|text|mathbf|operatorname)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[A-Za-z]+", " ", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def to_plain(text: str) -> str:
    """Strip rich-lite markup (math rendered as rough plain text)."""
    out: list[str] = []
    for sp in tokenize(text):
        out.append(tex_to_plain(sp.text) if sp.kind == "math" else sp.text)
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


def to_html(text: str) -> str:
    """Safe HTML for rich-lite text: every segment escaped; TeX shown as escaped source in <code>."""
    parts: list[str] = []
    for sp in tokenize(text):
        esc = html.escape(sp.text, quote=True)
        if sp.kind == "text":
            parts.append(esc)
        elif sp.kind == "bold":
            parts.append(f"<strong>{esc}</strong>")
        elif sp.kind == "italic":
            parts.append(f"<em>{esc}</em>")
        elif sp.kind == "code":
            parts.append(f"<code>{esc}</code>")
        elif sp.kind == "math":
            parts.append(f'<code class="tex">{esc}</code>')
        else:
            parts.append(f"<mark>{esc}</mark>")
    return "".join(parts)


def to_markdown(text: str) -> str:
    """Rich-lite -> Markdown (keywords become bold; everything else is Markdown-compatible)."""
    parts: list[str] = []
    for sp in tokenize(text):
        if sp.kind == "text":
            parts.append(escape_markdown_html(sp.text))
        elif sp.kind == "bold":
            parts.append(f"**{escape_markdown_html(sp.text)}**")
        elif sp.kind == "italic":
            parts.append(f"*{escape_markdown_html(sp.text)}*")
        elif sp.kind == "code":
            parts.append(f"`{sp.text}`")  # code spans are literal in Markdown (no HTML)
        elif sp.kind == "math":
            parts.append(f"${escape_tex_html(sp.text)}$")
        else:
            parts.append(f"**{escape_markdown_html(sp.text)}**")
    return "".join(parts)


_TAGLIKE = re.compile(r"<(?=[A-Za-z/!?])")


def escape_markdown_html(text: str) -> str:
    """Neutralise tag-like ``<`` so Markdown renderers never see raw HTML (``a < b`` is kept)."""
    return _TAGLIKE.sub("&lt;", text or "")


def escape_tex_html(tex: str) -> str:
    """Neutralise tag-like ``<`` inside TeX emitted into Markdown (math is not a Markdown construct).

    A space after ``<`` stops HTML (``< script`` is not a tag) while the maths renders the same
    (TeX ignores spaces in math mode), unlike ``&lt;``, which math renderers would show verbatim.
    """
    return _TAGLIKE.sub("< ", tex or "")


# --- narration markup detection -------------------------------------------------------------

_NARRATION_MARKUP = (
    ("bold/italic asterisks", re.compile(r"\*")),
    ("backticks", re.compile(r"`")),
    ("dollar-sign maths", re.compile(r"\$")),
    ("keyword brackets", re.compile(r"\[\[|\]\]")),
    ("LaTeX commands", re.compile(r"\\[A-Za-z]+")),
    ("HTML tags", re.compile(r"</?[A-Za-z][^>]*>")),
    ("Markdown headings", re.compile(r"(?m)^\s*#{1,6}\s")),
    ("curly braces", re.compile(r"[{}]")),
    ("caret/underscore maths", re.compile(r"\w\^\w|\b\w_\{?\w")),
)


def narration_markup(text: str) -> list[str]:
    """Kinds of markup found in spoken text (empty list = clean)."""
    return [label for label, rx in _NARRATION_MARKUP if rx.search(text or "")]


# --- masking for translation -----------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"⟦(\d+)⟧")


def mask(text: str, protect_terms: list[str] | None = None) -> tuple[str, list[str]]:
    """Replace math/code spans and protected terms with ``⟦n⟧``; returns (masked, originals)."""
    originals: list[str] = []

    def keep(raw: str) -> str:
        originals.append(raw)
        return f"⟦{len(originals) - 1}⟧"

    masked = re.sub(r"(?<!\\)\$[^$]+?(?<!\\)\$|`[^`]+`", lambda m: keep(m.group(0)), text or "")
    for term in sorted({t for t in (protect_terms or []) if t.strip()}, key=len, reverse=True):
        pattern = re.compile(rf"(?<![\w⟦]){re.escape(term)}(?![\w⟧])", re.I)
        masked = pattern.sub(lambda m: keep(m.group(0)), masked)
    return masked, originals


def unmask(text: str, originals: list[str]) -> str:
    """Inverse of ``mask``."""
    return PLACEHOLDER_RE.sub(lambda m: originals[int(m.group(1))] if int(m.group(1)) < len(originals) else "", text)


def placeholder_problems(translated: str, n: int) -> list[str]:
    """Problems with ``⟦n⟧`` placeholders in a translation of a text that had ``n`` of them."""
    found = [int(x) for x in PLACEHOLDER_RE.findall(translated or "")]
    problems: list[str] = []
    missing = sorted(set(range(n)) - set(found))
    if missing:
        problems.append("missing placeholders " + ", ".join(f"⟦{i}⟧" for i in missing))
    extra = sorted({i for i in found if i >= n})
    if extra:
        problems.append("unknown placeholders " + ", ".join(f"⟦{i}⟧" for i in extra))
    dup = sorted({i for i in found if found.count(i) > 1})
    if dup:
        problems.append("repeated placeholders " + ", ".join(f"⟦{i}⟧" for i in dup))
    return problems


def markup_balance_problems(text: str) -> list[str]:
    """Unbalanced rich-lite markers (after translation)."""
    problems: list[str] = []
    stripped = text.replace("\\*", "").replace("\\$", "")
    if stripped.count("**") % 2:
        problems.append("unbalanced ** markers")
    if stripped.count("[[") != stripped.count("]]"):
        problems.append("unbalanced [[ ]] markers")
    return problems
