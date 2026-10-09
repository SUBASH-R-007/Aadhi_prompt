"""Turn the PDF worker's raw layout (``pdf_worker.py``) into Markdown.

* reading order: top-to-bottom, with two-column pages read column by column;
* headings from font-size statistics (larger than body text -> ``#``/``##``/``###``; short bold
  lines -> ``###``); a display equation set in a large font ("V = I × R") stays a text line, so it
  never becomes the parent section of the headings after it;
* running headers/footers and page numbers removed (``PdfLayout.removed`` lists them, once each);
* tables (PyMuPDF ``find_tables``) as Markdown tables; text inside tables not duplicated;
* figures placed where they appear as ``[Figure <id>: <caption>]`` (caption from nearby
  "Figure n"/"Fig. n" text);
* a line that starts with a short label ("SME Name: ...", "Video duration: ...", "ANIMATION: ...")
  starts a new paragraph, so header fields and script labels stay separate lines for
  ``source_scope`` instead of being merged into one paragraph; a wrapped sentence whose next line
  merely contains an early colon ("... per unit area: σ = F / A") stays one paragraph
  (``label_starts_paragraph``);
* consecutive monospaced lines (``mono`` from the worker) become one fenced code block, their
  indentation rebuilt from the leading spaces and the x position (a monospaced character is about
  0.6 em wide); they are never headings, labels or joined sentences. A document set mostly in a
  monospaced font with no regular proportional body text, or whose monospaced lines do not look like code (a
  typewriter-style file), is read as ordinary text; a code-heavy one (a lab record: a short aim in a
  proportional font, then long listings) keeps its listings as code;
* a "~~~~" divider paragraph is escaped so it never opens a fence;
* ``<!-- page N -->`` markers before every page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .codeblocks import CODE_SIGNALS, code_language, dedent_lines, escape_fence, fence_block, tilde_divider

_BULLET_RE = re.compile(r"^\s*([•●◦▪■‣⁃∙·\-–*])\s+")
_CAPTION_RE = re.compile(r"^\s*(fig(?:ure)?\.?\s*\d+[\w.\-]*)", re.I)
_PAGE_NO_RE = re.compile(r"^(page\s*)?#+(\s*(of|/)\s*#+)?$", re.I)
_LABEL_LINE_RE = re.compile(r"^[A-Za-z][\w .&/()'’\-]{0,40}?\s*:(?:\s|$)")
_RELATION_RE = re.compile(r"[=≠≈≡≤≥⇒⇔]")
_WORD_RE = re.compile(r"[^\W\d_]{3,}")


def is_display_equation(text: str) -> bool:
    """A line that is an equation rather than a title: a relation sign and at most one word of three or more
    letters ("V = I × R", "(A · B)' = A' + B'", "σ = E ε"; not "Power = Voltage × Current")."""
    return bool(_RELATION_RE.search(text)) and len(_WORD_RE.findall(text)) < 2


def starts_with_label(text: str) -> bool:
    """True for lines like "SME Name: ..." / "Aadhi speaks:" (a short key of at most six words)."""
    m = _LABEL_LINE_RE.match(text)
    return bool(m) and len(m.group(0).split()) <= 6


_SENTENCE_END = re.compile(r"[.!?:;][\"'’”)\]]*$")


def known_label(text: str) -> bool:
    """A header field or an SME script label ("SME Name: ...", "Aadhi speaks:", "ANIMATION: ...", "NOTE: ...")."""
    from .source_scope import is_script_label, key_info  # lazy: source_scope is large

    m = _LABEL_LINE_RE.match(text)
    if not m:
        return False
    key = m.group(0).rstrip(" :")
    return key_info(key) is not None or is_script_label(text)


def label_starts_paragraph(previous: str, text: str) -> bool:
    """Whether a label line starts a new paragraph instead of continuing a wrapped sentence.

    A known header field or script label always does. Any other "Key: value" line only when the previous
    line ended a sentence (or was itself a label line) and the key starts with a capital: "resisting force per
    unit area: σ = F / A" after "Stress (σ) is the internal" is the same sentence, wrapped."""
    if not starts_with_label(text):
        return False
    if known_label(text):
        return True
    prev = previous.rstrip()
    return text[:1].isupper() and (not prev or bool(_SENTENCE_END.search(prev)) or starts_with_label(prev))


@dataclass
class PdfFigure:
    file: str
    mime: str
    width: int
    height: int
    sha256: str
    page: int
    caption: str = ""
    figure_id: str = ""


@dataclass
class PdfLayout:
    markdown: str
    figures: list[PdfFigure] = field(default_factory=list)
    pages: int = 0
    page_count: int = 0
    scanned_pages: int = 0
    math_ratio: float = 0.0
    bad_ratio: float = 0.0
    title: str = ""
    removed: list[str] = field(default_factory=list)  # running headers / footers and page numbers that were dropped


@dataclass
class _Element:
    y: float
    x: float
    col: int
    text: str
    code: list[dict] | None = None  # the monospaced lines of a code element (rendered after merging)


MAX_MONO_SHARE = 0.5  # above this share of the text in a monospaced font, monospace alone says nothing about code
MIN_PROSE_SHARE = 0.1  # ... unless this share is set in a regular proportional font (a lab record's aim and steps)
MIN_CODE_LINE_SHARE = 0.2  # ... and this share of the monospaced lines carry a code signal (prose: 0-10 %)


def _mono_is_code(pages: list[dict]) -> bool:
    """Monospaced lines are code, unless the document is mostly monospaced (a typewriter-style file). A document
    with long listings stays code when it has regular proportional body text and its monospaced lines look like
    code."""
    total = mono = prose = 0
    mono_lines: list[str] = []
    for p in pages:
        for b in p["blocks"]:
            for ln in b["lines"]:
                n = len(ln["text"])
                total += n
                if ln.get("mono"):
                    mono += n
                    mono_lines.append(ln["text"])
                elif not ln.get("bold"):
                    prose += n
    if not total or mono <= MAX_MONO_SHARE * total:
        return True
    if prose < MIN_PROSE_SHARE * total:
        return False
    hits = sum(1 for t in mono_lines if any(s.search(t) for s in CODE_SIGNALS))
    return hits >= MIN_CODE_LINE_SHARE * len(mono_lines)


def _code_markdown(lines: list[dict]) -> str:
    """Monospaced lines as a fenced block: indentation = leading spaces + x offset / (0.6 x font size)."""
    left = min(ln["bbox"][0] for ln in lines)
    rows = []
    for ln in lines:
        width = 0.6 * float(ln.get("size") or 10.0)
        indent = int(ln.get("lead") or 0) + max(0, round((ln["bbox"][0] - left) / width))
        rows.append(" " * indent + ln["text"])
    body = dedent_lines(rows)
    return "\n".join(fence_block(body, code_language("\n".join(body))))


def _merge_code(els: list[_Element]) -> list[_Element]:
    """Adjacent code elements of one column (PyMuPDF often splits a listing into blocks) become one."""
    out: list[_Element] = []
    for e in els:
        if e.code is not None and out and out[-1].code is not None and out[-1].col == e.col:
            out[-1].code.extend(e.code)
        else:
            out.append(e)
    for e in out:
        if e.code is not None:
            e.text = _code_markdown(e.code)
    return out


def _body_size(pages: list[dict]) -> float:
    sizes: Counter[float] = Counter()
    for p in pages:
        for b in p["blocks"]:
            for ln in b["lines"]:
                sizes[round(ln["size"] * 2) / 2] += len(ln["text"])
    return sizes.most_common(1)[0][0] if sizes else 11.0


def _heading_levels(pages: list[dict], body: float) -> dict[float, int]:
    total = 0
    by_size: Counter[float] = Counter()
    for p in pages:
        for b in p["blocks"]:
            for ln in b["lines"]:
                total += len(ln["text"])
                by_size[round(ln["size"] * 2) / 2] += len(ln["text"])
    candidates = [s for s, n in by_size.items() if s >= body + max(1.0, body * 0.12) and n <= 0.2 * max(total, 1)]
    return {s: i + 1 for i, s in enumerate(sorted(candidates, reverse=True)[:3])}


def _band_key(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _running_lines(pages: list[dict]) -> set[str]:
    counts: Counter[str] = Counter()
    for p in pages:
        h = p["height"] or 1.0
        keys = set()
        for b in p["blocks"]:
            for ln in b["lines"]:
                y0, y1 = ln["bbox"][1], ln["bbox"][3]
                if y1 < 0.08 * h or y0 > 0.92 * h:
                    keys.add(_band_key(ln["text"]))
        counts.update(keys)
    threshold = max(2, int(0.4 * len(pages) + 0.5))
    return {k for k, n in counts.items() if n >= threshold}


def _in_band(ln: dict, h: float) -> bool:
    return ln["bbox"][3] < 0.08 * h or ln["bbox"][1] > 0.92 * h


def _overlap_ratio(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return (w * h) / area


def _table_markdown(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    norm = [[(c or "").replace("|", "\\|") for c in (r + [""] * (width - len(r)))] for r in rows]
    header = [c or f"Col{i + 1}" for i, c in enumerate(norm[0])]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


class _PageWriter:
    def __init__(self, page: dict, body: float, levels: dict[float, int], running: set[str],
                 removed: dict[str, str] | None = None, *, code: bool = True) -> None:
        self.page = page
        self.code = code  # monospaced lines are code (False for a typewriter-style document: ``_mono_is_code``)
        self.removed = removed if removed is not None else {}
        self.body = body
        self.levels = levels
        self.running = running
        self.w = page["width"] or 612.0
        self.h = page["height"] or 792.0
        self.two_col = self._two_columns()

    def _two_columns(self) -> bool:
        blocks = self.page["blocks"]
        if len(blocks) < 6:
            return False
        left = sum(1 for b in blocks if b["bbox"][2] <= 0.55 * self.w)
        right = sum(1 for b in blocks if b["bbox"][0] >= 0.45 * self.w)
        return left >= 3 and right >= 3 and (left + right) >= 0.6 * len(blocks)

    def _col(self, bbox: list[float]) -> int:
        return 1 if self.two_col and bbox[0] >= 0.45 * self.w else 0

    def _heading_level(self, ln: dict, n_lines: int) -> int | None:
        text = ln["text"].strip()
        if not text or len(text) > 120 or _CAPTION_RE.match(text) or _PAGE_NO_RE.match(_band_key(text)):
            return None
        if is_display_equation(text):
            return None
        lvl = self.levels.get(round(ln["size"] * 2) / 2)
        if lvl:
            return lvl
        if ln["bold"] and n_lines <= 2 and len(text) <= 80 and ln["size"] >= self.body - 0.5:
            if not text.endswith((".", ",", ";")) and any(c.isalpha() for c in text):
                return min(3, max(self.levels.values(), default=1) + 1) if self.levels else 3
        return None

    def _block_elements(self, block: dict) -> list[_Element]:
        lines = []
        for ln in block["lines"]:
            key = _band_key(ln["text"])
            if _in_band(ln, self.h) and (key in self.running or _PAGE_NO_RE.match(key)):
                self.removed.setdefault(key, ln["text"].strip())
            else:
                lines.append(ln)
        if not lines:
            return []
        out: list[_Element] = []
        col = self._col(block["bbox"])
        paragraph: list[str] = []
        items: list[str] = []
        code: list[dict] = []

        def flush() -> None:
            if paragraph:
                y = block["bbox"][1]
                joined = " ".join(paragraph)  # a "~~~~" divider is decoration: only code lines become fences
                out.append(_Element(y, block["bbox"][0], col, escape_fence(joined) if tilde_divider(joined) else joined))
                paragraph.clear()
            if items:
                out.append(_Element(block["bbox"][1], block["bbox"][0], col, "\n".join(f"- {i}" for i in items)))
                items.clear()
            if code:
                out.append(_Element(code[0]["bbox"][1], code[0]["bbox"][0], col, "", code=list(code)))
                code.clear()

        for idx, ln in enumerate(lines):
            text = ln["text"].strip()
            if self.code and ln.get("mono"):  # code: no heading, label or sentence rules
                if paragraph or items:
                    flush()
                code.append(ln)
                continue
            if code:
                flush()
            level = self._heading_level(ln, len(lines)) if idx == 0 or not paragraph else None
            if level:
                flush()
                out.append(_Element(ln["bbox"][1], ln["bbox"][0], col, f"{'#' * level} {text}"))
                continue
            previous = paragraph[-1] if paragraph else (items[-1] if items else "")
            if (paragraph or items) and label_starts_paragraph(previous, text):
                flush()  # "SME Name: ..." / "Video duration: ..." / "ANIMATION: ..." start their own paragraph
            m = _BULLET_RE.match(text)
            if m:
                if paragraph:
                    flush()
                items.append(text[m.end():].strip())
            elif items and not paragraph:
                items[-1] = f"{items[-1]} {text}"
            else:
                paragraph.append(text)
        flush()
        return out

    def elements(self, figures: list[PdfFigure], page_images: list[dict]) -> list[_Element]:
        tables = self.page.get("tables", [])
        els: list[_Element] = []
        for b in self.page["blocks"]:
            if any(_overlap_ratio(b["bbox"], t["bbox"]) >= 0.5 for t in tables):
                continue
            els.extend(self._block_elements(b))
        for t in tables:
            els.append(_Element(t["bbox"][1], t["bbox"][0], self._col(t["bbox"]), _table_markdown(t["rows"])))
        for fig, img in zip(figures, page_images, strict=True):
            bbox = img.get("bbox")
            caption = f": {fig.caption}" if fig.caption else ""
            marker = f"[Figure {fig.figure_id}{caption}]"
            if bbox:
                els.append(_Element(bbox[1], bbox[0], self._col(bbox), marker))
            else:
                els.append(_Element(self.h, 0.0, 1 if self.two_col else 0, marker))
        if self.two_col:
            els.sort(key=lambda e: (e.col, e.y, e.x))
        else:
            els.sort(key=lambda e: (round(e.y / 3), e.x))
        return _merge_code(els)

    def caption_for(self, img: dict) -> str:
        bbox = img.get("bbox")
        if not bbox:
            return ""
        best: tuple[float, str] | None = None
        for b in self.page["blocks"]:
            text = " ".join(ln["text"] for ln in b["lines"]).strip()
            if not _CAPTION_RE.match(text):
                continue
            below = b["bbox"][1] - bbox[3]
            above = bbox[1] - b["bbox"][3]
            if -5 <= below <= 120:
                dist = below
            elif -5 <= above <= 60:
                dist = above + 30  # prefer captions below the figure
            else:
                continue
            if best is None or dist < best[0]:
                best = (dist, text)
        return best[1][:300] if best else ""


def build_layout(result: dict[str, Any]) -> PdfLayout:
    """Markdown + figures from the worker's ``result.json``."""
    pages: list[dict] = result.get("pages", [])
    body = _body_size(pages)
    levels = _heading_levels(pages, body)
    running = _running_lines(pages)
    md_parts: list[str] = []
    figures: list[PdfFigure] = []
    total_chars = sum(p.get("chars", 0) for p in pages)
    math_chars = sum(p.get("math_chars", 0) for p in pages)
    bad_chars = sum(p.get("bad_chars", 0) for p in pages)
    scanned = sum(1 for p in pages if p.get("chars", 0) < 40 and p.get("image_area", 0) > 0.5)
    removed: dict[str, str] = {}
    code = _mono_is_code(pages)
    for p in pages:
        writer = _PageWriter(p, body, levels, running, removed, code=code)
        page_figs: list[PdfFigure] = []
        for k, img in enumerate(p.get("images", []), 1):
            fig = PdfFigure(
                file=img["file"], mime=img["mime"], width=img["width"], height=img["height"],
                sha256=img["sha256"], page=p["number"], caption=writer.caption_for(img),
                figure_id=f"fig-p{p['number']}-{k}",
            )
            page_figs.append(fig)
        figures.extend(page_figs)
        body_md = "\n\n".join(e.text for e in writer.elements(page_figs, p.get("images", [])))
        md_parts.append(f"<!-- page {p['number']} -->\n\n{body_md}".rstrip())
    return PdfLayout(
        markdown="\n\n".join(md_parts).strip() + "\n",
        figures=figures,
        pages=len(pages),
        page_count=int(result.get("page_count", len(pages))),
        scanned_pages=scanned,
        math_ratio=(math_chars / total_chars) if total_chars else 0.0,
        bad_ratio=(bad_chars / total_chars) if total_chars else 0.0,
        title=(result.get("metadata") or {}).get("title", "") or "",
        removed=[t for t in removed.values() if t][:50],
    )
