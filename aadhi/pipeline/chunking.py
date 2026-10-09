"""Markdown -> addressable source chunks; language detection; truncation.

Fenced code blocks (``codeblocks``) are one unit: their lines are never read as headings, tables or
page markers, blank lines and indentation are kept, and a block too long for one chunk is split at line
boundaries with the fence re-opened in every piece.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import SourceChunk
from .codeblocks import closes_fence, fence_open, strip_fenced

PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")
_EQUATION_RE = re.compile(r"[=≠≤≥⇒→↔]")

TARGET_CHARS = 1200
MAX_CHARS = 1900  # SourceChunk.text max_length is 2000


@dataclass
class _Block:
    kind: str  # page | heading | text | table | code
    text: str
    level: int = 0
    page: int | None = None


def _blocks(markdown: str) -> list[_Block]:
    out: list[_Block] = []
    para: list[str] = []
    table: list[str] = []
    code: list[str] = []
    fence: tuple[str, int] | None = None

    def flush() -> None:
        if para:
            out.append(_Block("text", "\n".join(para).strip()))
            para.clear()
        if table:
            out.append(_Block("table", "\n".join(table)))
            table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if fence is not None:  # inside a fenced code block: every line verbatim
            code.append(line)
            if closes_fence(line, *fence):
                out.append(_Block("code", "\n".join(code)))
                code, fence = [], None
            continue
        opened = fence_open(line)
        if opened is not None:
            flush()
            fence = opened[0], opened[1]
            code = [line]
            continue
        m = PAGE_MARKER_RE.fullmatch(line.strip())
        if m:
            flush()
            out.append(_Block("page", "", page=int(m.group(1))))
            continue
        h = _HEADING_RE.match(line)
        if h:
            flush()
            out.append(_Block("heading", h.group(2).strip(), level=len(h.group(1))))
            continue
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith("|"):
            if para:
                flush()
            table.append(line)
        else:
            if table:
                flush()
            para.append(line)
    flush()
    if fence is not None:  # unclosed (e.g. cut by truncation): close it
        out.append(_Block("code", "\n".join([*code, fence[0] * fence[1]])))
    return out


def _split_code(text: str, limit: int) -> list[str]:
    """A fenced block in pieces of at most ``limit`` chars, cut at line boundaries; every piece is fenced."""
    lines = text.split("\n")
    opener, closer = lines[0], lines[-1]
    room = max(80, limit - len(opener) - len(closer) - 2)
    pieces: list[str] = []
    cur: list[str] = []
    size = 0
    for line in lines[1:-1]:
        while len(line) > room:  # pathological: one line longer than a chunk
            if cur:
                pieces.append("\n".join([opener, *cur, closer]))
                cur, size = [], 0
            pieces.append("\n".join([opener, line[:room], closer]))
            line = line[room:]
        if cur and size + len(line) + 1 > room:
            pieces.append("\n".join([opener, *cur, closer]))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur or not pieces:
        pieces.append("\n".join([opener, *cur, closer]))
    return pieces


def _split_long(text: str, kind: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    if kind == "code":
        return _split_code(text, limit)
    if kind == "table":
        lines = text.split("\n")
        head, rows = lines[:2], lines[2:]
        pieces, cur = [], list(head)
        for r in rows:
            if sum(len(x) + 1 for x in cur) + len(r) > limit and len(cur) > 2:
                pieces.append("\n".join(cur))
                cur = list(head)
            cur.append(r[:limit - 200])
        pieces.append("\n".join(cur))
        return pieces
    pieces, cur = [], ""
    for sent in _SENTENCE_RE.split(text):
        while len(sent) > limit:  # pathological: no sentence breaks
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(sent[:limit])
            sent = sent[limit:]
        if cur and len(cur) + 1 + len(sent) > limit:
            pieces.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def chunk_markdown(markdown: str, target: int = TARGET_CHARS) -> list[SourceChunk]:
    """Split Markdown into ~``target``-char chunks that respect headings, pages and tables."""
    chunks: list[SourceChunk] = []
    stack: list[tuple[int, str]] = []
    page: int | None = None
    cur: list[str] = []
    cur_page: int | None = None
    cur_path: list[str] = []

    def flush() -> None:
        nonlocal cur, cur_page
        text = "\n\n".join(cur).strip()
        if text:
            n = len(chunks) + 1
            cid = f"c{n:04d}" if n < 10000 else f"c{n:05d}"
            chunks.append(SourceChunk(id=cid, page=cur_page, heading_path=list(cur_path), text=text[:2000]))
        cur = []
        cur_page = None

    for block in _blocks(markdown):
        if block.kind == "page":
            page = block.page
            continue
        if block.kind == "heading":
            flush()
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.text[:200]))
            continue
        for piece in _split_long(block.text, block.kind):
            if cur and sum(len(x) for x in cur) + len(piece) > target:
                flush()
            if not cur:
                cur_page = page
                cur_path = [t for _, t in stack]
            cur.append(piece)
    flush()
    return chunks


_SCRIPTS = (
    ("ta-IN", 0x0B80, 0x0BFF),
    ("hi-IN", 0x0900, 0x097F),
    ("te-IN", 0x0C00, 0x0C7F),
    ("kn-IN", 0x0C80, 0x0CFF),
    ("ml-IN", 0x0D00, 0x0D7F),
)


def detect_language(text: str, sample: int = 200_000) -> str | None:
    """Dominant script -> language code (Latin -> en-IN); None when there is no text."""
    counts = {code: 0 for code, _, _ in _SCRIPTS}
    latin = 0
    for ch in text[:sample]:
        o = ord(ch)
        if o < 128:
            if ch.isalpha():
                latin += 1
            continue
        for code, lo, hi in _SCRIPTS:
            if lo <= o <= hi:
                counts[code] += 1
                break
    total = latin + sum(counts.values())
    if total == 0:
        return None
    code, n = max(counts.items(), key=lambda kv: kv[1])
    if n >= 0.3 * total:
        return code
    return "en-IN" if latin else None


# Scripts narration cannot speak (``SUPPORTED_LANGUAGES`` covers Latin, Tamil, Devanagari, Telugu, Kannada and
# Malayalam). Only used to warn the teacher; ``detect_language`` never returns these.
_UNSUPPORTED_SCRIPTS = (
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi (Punjabi)", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F),
    ("Sinhala", 0x0D80, 0x0DFF),
    ("Thai", 0x0E00, 0x0E7F),
    ("Greek", 0x0370, 0x03FF),
    ("Cyrillic", 0x0400, 0x04FF),
    ("Hebrew", 0x0590, 0x05FF),
    ("Arabic", 0x0600, 0x06FF),
    ("Japanese", 0x3040, 0x30FF),
    ("Chinese", 0x4E00, 0x9FFF),
    ("Korean", 0xAC00, 0xD7AF),
)
_SUPPORTED_RANGES = tuple((lo, hi) for _, lo, hi in _SCRIPTS)


def unsupported_script(text: str, sample: int = 200_000) -> str | None:
    """Name of the script most of ``text`` is written in when narration cannot speak it (None otherwise).

    Fenced code is ignored. "Most" = the largest script by letters and at least 40 % of all letters, so
    Greek symbols in an English physics text or a Bengali quotation in a Tamil text never count.
    """
    counts: dict[str, int] = {}
    supported = 0
    for ch in strip_fenced(text[:sample]):
        o = ord(ch)
        if o < 128:
            if ch.isalpha():
                supported += 1
            continue
        if any(lo <= o <= hi for lo, hi in _SUPPORTED_RANGES):
            supported += 1
            continue
        for name, lo, hi in _UNSUPPORTED_SCRIPTS:
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return None
    name, n = max(counts.items(), key=lambda kv: kv[1])
    total = supported + sum(counts.values())
    return name if n > supported and n >= 0.4 * total else None


def looks_like_heading(text: str, *, bold: bool = False) -> bool:
    """Heuristic for section titles written as ordinary paragraphs (DOCX without heading styles, TXT).

    True for short lines that are ALL CAPS (>= 90% of letters) or entirely bold, with at least two
    words and no sentence or label punctuation at the end ("DETAILED VISUAL:" is a label, not a title).
    Equations ("A + B = B + A", "Y = A + AB") are content, never titles.
    """
    s = (text or "").strip()
    if not 3 <= len(s) <= 90 or s.endswith((".", ":", ";", ",")) or s.startswith(("-", "*", "|", "#")):
        return False
    if _EQUATION_RE.search(s):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4 or len(s.split()) < 2:
        return False
    upper = sum(1 for c in letters if c.isupper()) / len(letters)
    if upper >= 0.9:
        return True
    return bold and len(s) <= 80 and len(s.split()) <= 12


def truncate_markdown(markdown: str, max_chars: int) -> tuple[str, bool]:
    """Cut at the last paragraph break before ``max_chars``; returns (text, truncated)."""
    if len(markdown) <= max_chars:
        return markdown, False
    cut = markdown.rfind("\n\n", 0, max_chars)
    if cut < max_chars * 0.8:
        cut = max_chars
    return markdown[:cut].rstrip() + "\n", True


def page_count_from_markdown(markdown: str) -> int | None:
    pages = [int(m.group(1)) for m in PAGE_MARKER_RE.finditer(markdown)]
    return max(pages) if pages else None
