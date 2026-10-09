"""Source code inside teaching material: fenced blocks, code detection and a language hint.

Programming sources carry code whose indentation and line breaks are its meaning. Every ingest
path therefore hands code to the rest of the pipeline as a Markdown fenced block (```` ```lang ````):

* TXT / MD: existing ```` ``` ```` / ``~~~`` fences are kept verbatim (in TXT a ``~~~`` line only when it
  names a language or encloses code: a "~~~~~~" divider stays text), and an indented run (4 spaces or a tab,
  after a blank line) that reads like code and is mostly code lines becomes one;
* DOCX: paragraphs in a Code / Source Code / HTML Preformatted style (soft line breaks kept);
* PDF: consecutive monospaced lines, indentation rebuilt from their x positions.

DOCX and PDF output never has a ``~~~`` fence: a paragraph that would open one is escaped (``\\~~~``).

Inside a fence nothing is read as a heading, list, table, page marker, label or admin line
(``chunking``, ``source_scope``, ``ingest.text_to_markdown``); a block is kept in one chunk when it fits.
"""

from __future__ import annotations

import re
import textwrap
from bisect import bisect_right
from collections.abc import Callable, Iterable
from itertools import accumulate

_FENCE_OPEN = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*$")


def _line_pattern(pattern: str, flags: int = 0) -> re.Pattern[str]:
    """``pattern`` compiled with re.M, its line-start ``^\\s*`` written as ``^[^\\S\\n]*`` (spaces and tabs, never a
    newline): the same matches, without a quadratic scan over a long run of blank lines."""
    return re.compile(pattern.replace(r"^\s*", r"^[^\S\n]*"), re.M | flags)


# Signals of source code (two are needed; a single line needs three): ported from the friend's
# source_analysis.CODE_SIGNALS, plus C declarations and preprocessor lines. Uploads are matched while the job
# holds the GIL, so every pattern must run in linear time: the for(;;) signal stops each segment at the next ";"
# (the same lines as ``for\s*\(.*;.*;.*\)``, which backtracked in cubic time on a line of ";"), C qualifiers and
# Python's "if ...:" stay on one line and an HTML tag ends before the next "<" (no rescan of the rest per start).
CODE_SIGNALS = tuple(_line_pattern(p) for p in (
    r"^\s*def \w+\(.*\):", r"^\s*class \w+[(:]", r"\bprint\(", r"console\.log\(", r"^\s*#\s*include\b", r"^\s*#\s*define\b",
    r"^\s*import [\w.]+", r"^\s*from [\w.]+ import\b", r"\breturn\b.*;?$", r"^\s*for \w+ in .+:",
    r"^\s*for\s*\([^;\n]*;[^;\n]*;[^\n]*\)",
    r"^\s*while\s*\(.+\)", r"\bpublic static\b", r"^\s*SELECT\b.+\bFROM\b", r";\s*$", r"^\s*[{}]\s*$", r"=>",
    r"^\s*(?:let|const|var)\s+\w+\s*=", r"^\s*if\s*\(.+\)\s*\{?", r"^\s*elif\b|^\s*else\s*:", r"\bprintf\s*\(|\bscanf\s*\(",
    r"^\s*(?:(?:unsigned|signed|static|const)[^\S\n]+)*(?:int|float|double|char|void|long|short|bool)\s+\*?\w+\s*[=;(\[,]",
))

# CODE_SIGNALS with every "\s" kept inside one line ("[^\S\n]": the same characters on a line, which holds no
# newline), so no match can span lines: a block of lines is matched once per pattern (C speed) and each match is
# credited to the line it starts on, which gives exactly the lines a per-line ``search`` would find (``text_fences``).
_BLOCK_SIGNALS = tuple(re.compile(p.pattern.replace(r"\s", r"[^\S\n]"), p.flags) for p in CODE_SIGNALS)

# Language hints: the language whose patterns match most, when that is unambiguous.
_LANGUAGES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (name, tuple(_line_pattern(p, flags) for p in patterns)) for name, flags, patterns in (
        ("python", 0, (r"^\s*def \w+\(.*\):\s*$", r"^\s*(?:import [\w.]+|from [\w.]+ import\b)", r"\bprint\(",
                       r"^\s*elif\b", r"^\s*(?:if|for|while|else|try|except)\b[^;{\n]*:\s*$", r"\b(?:None|True|False)\b")),
        ("c", 0, (r"^\s*#\s*include\s*[<\"]", r"\bprintf\s*\(", r"\bscanf\s*\(", r"\bint\s+main\s*\(", r"^\s*#\s*define\b",
                  r"\bmalloc\s*\(")),
        ("cpp", 0, (r"\bstd::", r"\bcout\s*<<", r"\bcin\s*>>", r"#\s*include\s*<iostream>", r"\busing namespace\b")),
        ("java", 0, (r"\bpublic\s+(?:static\s+)?(?:class|void)\b", r"System\.out\.print", r"\bString\[\]\s+\w+")),
        ("javascript", 0, (r"console\.log\(", r"=>", r"^\s*(?:let|const|var)\s+\w+\s*=", r"\bfunction\s+\w+\s*\(")),
        ("sql", re.I, (r"^\s*SELECT\b.+\bFROM\b", r"^\s*(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|CREATE\s+TABLE|DELETE\s+FROM)\b")),
        ("html", re.I, (r"</?(?:div|p|span|html|body|head|ul|li|table|a)\b[^<>]*>",)),
    )
)


def fence_open(line: str) -> tuple[str, int, str] | None:
    """``(char, length, info)`` when ``line`` opens a fence (```` ``` ```` or ``~~~``, any indentation)."""
    m = _FENCE_OPEN.match(line)
    if not m:
        return None
    fence, info = m.group("fence"), m.group("info").strip()
    if fence[0] == "`" and "`" in info:  # ```inline``` code on one line is not a fence
        return None
    return fence[0], len(fence), info


def closes_fence(line: str, char: str, length: int) -> bool:
    """True when ``line`` closes a fence opened with ``length`` x ``char``."""
    m = _FENCE_CLOSE.match(line)
    return bool(m) and m.group("fence")[0] == char and len(m.group("fence")) >= length


def fenced_segments(lines: Iterable[str]) -> list[tuple[bool, list[str]]]:
    """Consecutive ``(is_code, lines)`` runs; a fence's lines (its markers included) form one code run.
    An unclosed fence runs to the end."""
    out: list[tuple[bool, list[str]]] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        if fence is None:
            opened = fence_open(line)
            if opened is not None:
                fence = opened[0], opened[1]
                out.append((True, [line]))
                continue
            if out and not out[-1][0]:
                out[-1][1].append(line)
            else:
                out.append((False, [line]))
        else:
            out[-1][1].append(line)
            if closes_fence(line, *fence):
                fence = None
    return out


def outside_fences(text: str, fn: Callable[[str], str]) -> str:
    """``text`` with ``fn`` applied to everything except fenced code blocks (which stay verbatim)."""
    if "```" not in text and "~~~" not in text:
        return fn(text)
    return "\n".join(
        "\n".join(lines) if code else fn("\n".join(lines)) for code, lines in fenced_segments(text.split("\n"))
    )


def strip_fenced(text: str) -> str:
    """``text`` without its fenced code blocks (for prose-only checks: language, instruction-like text)."""
    if "```" not in text and "~~~" not in text:
        return text
    return "\n".join("\n".join(lines) for code, lines in fenced_segments(text.split("\n")) if not code)


def looks_like_code(text: str) -> bool:
    """At least two code signals (three for a single line)."""
    hits = sum(1 for p in CODE_SIGNALS if p.search(text))
    return hits >= 2 and ("\n" in text.strip() or hits >= 3)


_CODE_PUNCT = re.compile(r"[(){}\[\]=<>]|::|->|\+\+|//|/\*|\w\.\w+\(")
_COMMENT_LINE = re.compile(r"^\s*(?:#|//|/\*|\*|--|;|%|<!--)")


def _prose_line(line: str) -> bool:
    """Five or more words, nearly all plain words, and no code punctuation: a sentence, not a statement."""
    words = line.split()
    if len(words) < 5 or _CODE_PUNCT.search(line):
        return False
    plain = sum(1 for w in words if w.strip(".,;:!?'\"").replace("-", "").replace("'", "").isalpha())
    return plain >= 0.8 * len(words)


def dense_code(lines: list[str]) -> bool:
    """Most statement lines carry code punctuation or a code signal, and fewer than half read as sentences (comment
    lines count for neither). Guards indented-run detection against prose that happens to contain "return" or a
    line ending in ";" (``looks_like_code`` needs only two signals anywhere in the run)."""
    stmts = [ln for ln in lines if ln.strip() and not _COMMENT_LINE.match(ln)]
    if not stmts:
        return True
    prose = sum(1 for ln in stmts if _prose_line(ln))
    code = sum(1 for ln in stmts if _CODE_PUNCT.search(ln) or any(p.search(ln) for p in CODE_SIGNALS))
    return 2 * prose < len(stmts) and 2 * code >= len(stmts)


def escape_fence(line: str) -> str:
    """``line`` with a backslash before its fence marker, so it stays text (``\\~~~`` opens no fence)."""
    body = line.lstrip(" \t")
    return line[:len(line) - len(body)] + "\\" + body


def tilde_divider(line: str) -> bool:
    """True when ``line`` would open a ``~~~`` fence. DOCX / PDF extractors write only ```` ``` ```` fences, so in
    their output such a line is decoration (a "~~~~~~" divider), never code."""
    opened = fence_open(line)
    return opened is not None and opened[0] == "~"


_LANGUAGE_TAG = re.compile(r"[\w+#.-]{1,24}|\{[^{}]{1,40}\}")
TILDE_BODY_LINES = 40  # the lines of a "~~~" block read to tell code from a decorated passage
# Body lines read for unlabeled "~~~" fences per document (at most 40 per candidate fence: 10,000 candidates or more).
# Beyond it a further unlabeled "~~~" line stays text, as a divider does, so hostile input cannot make ingest slow.
TILDE_SCAN_BUDGET = 400_000
_BULK_LINES = 8  # a block with at least this many lines whose signals are unknown is matched in one pass per signal


_UNKNOWN = -2  # ``text_fences``: a line whose code signals were not matched yet


def _line_signals(chunk: list[str]) -> list[int]:
    """The CODE_SIGNALS bit mask of every line of ``chunk`` (-1: a blank line), each signal matched once over the
    whole chunk (``_BLOCK_SIGNALS``): the same masks as ``search`` line by line, at a fraction of the calls."""
    text = "\n".join(chunk)
    offsets = list(accumulate((len(line) + 1 for line in chunk), initial=0))
    bits = [0] * len(chunk)
    for k, pattern in enumerate(_BLOCK_SIGNALS):
        for m in pattern.finditer(text):
            bits[bisect_right(offsets, m.start()) - 1] |= 1 << k
    return [b if line.strip() else -1 for b, line in zip(bits, chunk)]


def text_fences(lines: list[str]) -> Callable[[int, tuple[str, int, str]], bool]:
    """For plain text: ``is_fence(i, opened)`` for a fence opened at ``lines[i]``. A ```` ``` ```` line always opens
    one; a ``~~~`` line only when the fence is closed and names a language or encloses code: its first
    ``TILDE_BODY_LINES`` lines carry two code signals (three for a single line), as in ``looks_like_code``.
    Otherwise it is a "~~~~~~" divider and stays text.

    Linear in the text however many dividers it has: the closing line is found by jumping between closing lines
    of increasing length (precomputed once), each line's code signals are matched once and only where a "~~~" fence
    needs them (a run of unknown lines in one pass per signal), and at most ``TILDE_SCAN_BUDGET`` body lines are read
    per document."""
    n = len(lines)
    signals: list[int] = [_UNKNOWN] * n  # line -> bit mask of the CODE_SIGNALS it matches (-1: a blank line)
    budget = [TILDE_SCAN_BUDGET]

    def learn(first: int, stop: int) -> None:
        """Fill in the signals of ``lines[first:stop]`` that are not known yet."""
        todo = [first + k for k, bits in enumerate(signals[first:stop]) if bits == _UNKNOWN]
        if len(todo) < _BULK_LINES:
            for j in todo:
                line = lines[j]
                signals[j] = sum(1 << k for k, p in enumerate(CODE_SIGNALS) if p.search(line)) if line.strip() else -1
            return
        start, end = todo[0], todo[-1] + 1  # known lines in between are matched again: same result
        signals[start:end] = _line_signals(lines[start:end])

    def encloses_code(i: int, end: int) -> bool:
        first, stop = i + 1, min(end, i + 1 + TILDE_BODY_LINES)
        if budget[0] < stop - first:
            return False  # hostile input: this "~~~" stays text (see TILDE_SCAN_BUDGET)
        budget[0] -= stop - first
        if _UNKNOWN in signals[first:stop]:
            learn(first, stop)
        found = rows = 0
        for bits in signals[first:stop]:
            if bits >= 0:
                found |= bits
                rows += 1
        hits = found.bit_count()
        return hits >= 2 and (rows > 1 or hits >= 3)

    closing = [0] * n  # length of the "~~~" run of a line that can close a tilde fence, else 0
    for j, line in enumerate(lines):
        if line.lstrip(" \t").startswith("~~~") and (m := _FENCE_CLOSE.match(line)) and m.group("fence")[0] == "~":
            closing[j] = len(m.group("fence"))
    following: list[int | None] = [None] * n  # the next closing line after j
    longer: dict[int, int | None] = {}  # closing line j -> the next closing line longer than it
    nxt: int | None = None
    stack: list[int] = []
    for j in range(n - 1, -1, -1):
        following[j] = nxt
        if closing[j]:
            while stack and closing[stack[-1]] <= closing[j]:
                stack.pop()
            longer[j] = stack[-1] if stack else None
            stack.append(j)
            nxt = j

    def is_fence(i: int, opened: tuple[str, int, str]) -> bool:
        char, length, info = opened
        if char == "`":
            return True
        end = following[i]
        while end is not None and closing[end] < length:  # every closing line in between is shorter still
            end = longer[end]
        if end is None:
            return False
        return bool(_LANGUAGE_TAG.fullmatch(info)) or encloses_code(i, end)

    return is_fence


def code_language(text: str) -> str:
    """A language hint for a code block ('' when unsure)."""
    scores = [(sum(1 for p in patterns if p.search(text)), name) for name, patterns in _LANGUAGES]
    scores.sort(reverse=True)
    best, name = scores[0]
    if best == 0 or (len(scores) > 1 and scores[1][0] == best):
        return ""
    return name


def fence_block(lines: list[str], language: str = "") -> list[str]:
    """``lines`` as a fenced block (a longer fence when the code itself contains ```` ``` ````)."""
    longest = max((len(m) for line in lines for m in re.findall(r"`{3,}", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", *lines, fence]


def dedent_lines(lines: list[str]) -> list[str]:
    """Common leading whitespace removed; blank lines kept (empty) and trailing whitespace dropped."""
    text = textwrap.dedent("\n".join(line.rstrip() for line in lines))
    return text.split("\n")
