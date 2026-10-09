"""Stage 1 — ingest: source file -> ``IngestResult`` (Markdown, chunks, figures).

* PDF: parsed by ``pdf_worker.py`` in a separate Python process (timeout, page cap, minimal
  environment, process-tree kill), then laid out by ``pdf_layout``. Scanned, maths-heavy or
  garbled PDFs set ``attach_original`` so planning reads the original natively when the provider
  can (``plan.require_grounding`` refuses a near-textless source the model cannot see at all).
* DOCX: mammoth -> HTML -> Markdown with embedded images (zip-bomb limits).
* TXT / MD: decoded as-is (numbered section lines become headings in plain text).

Extracted text is cleaned with ``textnorm.normalize_extracted_text``: ligatures are spelled out,
non-breaking and zero-width spaces become normal spaces / nothing and common mojibake is repaired
(targeted replacements only; never NFKC, which would turn "x²" into "x2").

Every source is then scoped (``source_scope.scope_source``) before truncation and chunking: the
document's metadata moves to ``document_meta``, administrative/production data (SME and reviewer
names, durations, timecodes, clip scaffolding, editing notes) to ``excluded`` and the author's
animation/visual directions to ``visual_notes``, so ``markdown``/``chunks`` hold teachable content
only. A job event summarises what was ignored (categories and counts, never the values).

Figures are stored as public ``figure`` assets (content-addressed by bytes) with an ``AssetRef``
for the project. The IngestResult JSON is stored as a private ``extract`` asset keyed by the
source hash + ingest settings (re-ingesting the same file is free) and its storage key is
recorded on ``SourceDocument.extracted_key``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..schemas.screenplay import SourceFigure
from ..storage.assets import Produced, bytes_key, compute_key
from . import dbops
from .base import ExcludedItem, IngestResult
from .chunking import (
    PAGE_MARKER_RE,
    chunk_markdown,
    detect_language,
    looks_like_heading,
    truncate_markdown,
    unsupported_script,
)
from .codeblocks import (
    closes_fence,
    code_language,
    dedent_lines,
    dense_code,
    escape_fence,
    fence_block,
    fence_open,
    fenced_segments,
    looks_like_code,
    text_fences,
)
from .docx_extract import DocxRejected, docx_to_markdown
from .pdf_layout import PdfLayout, build_layout
from .source_scope import (
    admin_key_line,
    front_matter_span,
    parse_title_line,
    resolve_visual_notes,
    scope_source,
    scope_summary,
    strip_title_admin,
)
from .textnorm import normalize_extracted_text

log = logging.getLogger(__name__)

# 3: provider-neutral warnings, readable-text check; 4: source scoping; 5: scoping v2; 6: ligatures / odd spaces /
# mojibake repaired, title-page subject-unit-session lines read as metadata, display equations are not PDF headings;
# 7: the SME's name and packaging inside the narration, generic mojibake repair, wrapped PDF sentences kept whole,
# the PDF title as session title, dropped PDF running headers recorded; 8: code kept verbatim in fenced blocks
# (TXT/MD fences and indented code, DOCX Code styles, PDF monospace lines), setext headings and "--- Page N ---"
# markers in TXT/MD, warnings for an unsupported script and for instruction-like text ("~~~" dividers in TXT/DOCX/PDF
# and indented prose stay text, code-heavy PDFs keep their listings as code). Versions generated from a
# version-7 extract keep reading it (``generation_meta["ingest_key"]``, see ``load_version_ingest``).
INGEST_VERSION = "8"
_FILE_TITLE = re.compile(r"\.(?:docx?|pdf|pptx?|txt|odt|rtf)$|^microsoft\s+(?:word|powerpoint)\b|^untitled\b", re.I)
WORKER_PATH = Path(__file__).resolve().parent / "pdf_worker.py"
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ENV_ALLOW = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "LANG", "LC_ALL", "HOME", "USERPROFILE")


class IngestError(Exception):
    """User-facing ingest failure (unreadable/encrypted/oversized source)."""


_NOT_TEXT = re.compile(r"<!--.*?-->|\[Figure [^\]]*\]|[#>*_`|\-]+|\s+", re.S)


def readable_chars(markdown: str) -> int:
    """Characters of real text: page comments, ``[Figure ...]`` markers and Markdown syntax excluded."""
    return len(_NOT_TEXT.sub("", markdown or ""))


def source_kind(mime: str, filename: str) -> str:
    """pdf | docx | markdown | text."""
    ext = Path(filename or "").suffix.lower()
    if mime == PDF_MIME or ext == ".pdf":
        return "pdf"
    if mime == DOCX_MIME or ext == ".docx":
        return "docx"
    if mime == "text/markdown" or ext in (".md", ".markdown"):
        return "markdown"
    return "text"


def decode_text(raw: bytes) -> str:
    """Decode a text upload (BOM-aware UTF-8/UTF-16, then cp1252 as a lossy fallback)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


_TXT_HEADING = re.compile(r"^(?:(?:chapter|unit|module|section|part)\b.{0,80}|\d+(?:\.\d+){0,3}\.?\s+[A-Z][^.!?]{1,80})$", re.I)
_SETEXT_UNDERLINE = re.compile(r"^[ \t]{0,3}(?:={3,}|-{3,})[ \t]*$")
_PAGE_LINE = re.compile(r"^-{2,}\s*page\s+(\d{1,5})\s*-{2,}$", re.I)  # "--- Page 3 ---" in pasted text
_NOT_SETEXT_TITLE = re.compile(r"^(?:[-*+•]\s|>|\||#|<!--|\[Figure\b)")
_INDENTED = re.compile(r"^(?: {4}|\t)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def _boundary(line: str | None) -> bool:
    """Start of the text, a blank line, a heading or a page marker: what may precede a one-line paragraph."""
    if line is None:
        return True
    s = line.strip()
    return not s or s.startswith(("#", "<!--")) or bool(_PAGE_LINE.match(s))


def _setext_title(line: str, previous: str | None) -> bool:
    """The text line of a setext heading ("Title" over "=====" / "-----"): a short one-line paragraph that is not
    a sentence, a list item or a table row (so a paragraph followed by a "---" rule stays a paragraph)."""
    s = line.strip()
    return (bool(s) and _boundary(previous) and len(s) <= 90 and len(s.split()) <= 12
            and not s.endswith((".", ",", ";", ":")) and not _NOT_SETEXT_TITLE.match(s)
            and not _INDENTED.match(line) and not _SETEXT_UNDERLINE.match(line))


def _indented_code(lines: list[str], i: int) -> int:
    """End index of an indented code run starting at ``i`` (0 = not code): 4-space / tab indented lines after a
    blank line, not continuing a list item, that read like code and are mostly code lines
    (``codeblocks.looks_like_code`` and ``dense_code``: indented prose that mentions "return" stays prose)."""
    if not _INDENTED.match(lines[i]) or not lines[i].strip() or not _boundary(lines[i - 1] if i else None):
        return 0
    prev = next((lines[k] for k in range(i - 1, -1, -1) if lines[k].strip()), "")
    if _LIST_ITEM.match(prev) or _INDENTED.match(prev):
        return 0  # a list item's continuation, or the tail of a longer indented passage
    j = i
    while j < len(lines) and (not lines[j].strip() or _INDENTED.match(lines[j])):
        j += 1
    while j > i and not lines[j - 1].strip():
        j -= 1
    body = dedent_lines(lines[i:j])
    return j if looks_like_code("\n".join(body)) and dense_code(body) else 0


def _structure_lines(text: str, *, plain: bool) -> list[str]:
    """Shared TXT / MD clean-up, outside fenced code and YAML front matter: setext headings become ATX headings,
    "--- Page N ---" lines page markers, indented code runs fenced blocks; with ``plain`` (TXT) numbered,
    Chapter/Unit and ALL-CAPS lines become headings too, and a "~~~~~~" divider line that encloses no code is
    escaped so it opens no fence (``codeblocks.text_fences``). Fenced blocks are copied verbatim."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    front = front_matter_span(lines)
    is_fence = text_fences(lines) if plain and "~~~" in text else None
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        keep = line.rstrip() if plain else line
        if front is not None and front[0] <= i <= front[1]:
            out.append(keep)
            i += 1
            continue
        opened = fence_open(line)
        if opened is not None and is_fence is not None and not is_fence(i, opened):  # a "~~~~~~" divider: text
            out.append(escape_fence(keep))
            i += 1
            continue
        if opened is not None:  # verbatim up to the closing fence (or the end)
            out.append(keep)
            i += 1
            while i < n:
                out.append(lines[i].rstrip() if plain else lines[i])
                i += 1
                if closes_fence(lines[i - 1], opened[0], opened[1]):
                    break
            continue
        s = line.strip()
        if m := _PAGE_LINE.match(s):
            out.append(f"<!-- page {int(m.group(1))} -->")
            i += 1
            continue
        previous = lines[i - 1] if i else None
        if i + 1 < n and _SETEXT_UNDERLINE.match(lines[i + 1]) and _setext_title(line, previous):
            out.append(f"{'#' if '=' in lines[i + 1] else '##'} {s}")
            i += 2
            continue
        if end := _indented_code(lines, i):
            body = dedent_lines(lines[i:end])
            out.extend(fence_block(body, code_language("\n".join(body))))
            i = end
            continue
        if plain and s and _TXT_HEADING.match(s) and len(s) <= 90:
            depth = min(3, 1 + s.split()[0].count(".")) if s[0].isdigit() else 1
            out.append(f"{'#' * depth} {s}")
        elif plain and s and looks_like_heading(s):
            out.append(f"## {s}")
        else:
            out.append(keep)
        i += 1
    return out


def text_to_markdown(text: str) -> str:
    """Plain text -> Markdown: normalised newlines; numbered, Chapter/Unit, ALL-CAPS and setext ("===" / "---"
    underlined) lines as headings; "--- Page N ---" as page markers; code kept verbatim in fenced blocks."""
    return "\n".join(_structure_lines(text, plain=True)).strip() + "\n"


def markdown_source(text: str) -> str:
    """A Markdown upload: normalised newlines, setext headings as ATX headings (``source_scope`` and ``chunking``
    read ATX only), "--- Page N ---" as page markers, indented code as fenced blocks; fences untouched."""
    return "\n".join(_structure_lines(text, plain=False)).strip() + "\n"


def close_open_fence(markdown: str) -> str:
    """``markdown`` with a fence left open at its end (a truncation cut inside a code block) closed."""
    segments = fenced_segments(markdown.rstrip("\n").split("\n"))
    if not segments or not segments[-1][0]:
        return markdown
    lines = segments[-1][1]
    opened = fence_open(lines[0])
    assert opened is not None
    if len(lines) > 1 and closes_fence(lines[-1], opened[0], opened[1]):
        return markdown
    return markdown.rstrip("\n") + "\n" + opened[0] * opened[1] + "\n"


# Text addressed to an AI model rather than to a learner (prompt-injection style). English only and easy to evade:
# a signal shown to the teacher, never a defence (the defences act on the model's output). Phrases that teaching
# material uses ordinarily ("run the following code", "you are now ready") are deliberately not matched.
INSTRUCTION_LIKE = re.compile(
    r"\b(?:(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|everything\s+)?(?:of\s+)?(?:the\s+|your\s+|my\s+)?"
    r"(?:previous|prior|above|earlier|preceding|original|system)\s+(?:instructions?|prompts?|rules|directions|messages?)"
    r"|(?:reveal|print|show|repeat|output|leak|display)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+|hidden\s+|initial\s+|secret\s+)?"
    r"(?:prompt|instructions)"
    r"|you\s+are\s+now\s+(?:an?\s+|the\s+)?(?:\w+\s+){0,2}(?:AI|assistant|chatbot|language\s+model|LLM|DAN)\b"
    r"|(?:new|updated)\s+(?:system\s+)?instructions?\s*:"
    r"|(?:as|to)\s+the\s+(?:AI|assistant|language\s+model)\s+(?:reading|processing)\s+this)",
    re.I,
)


def instruction_like_warning(markdown: str, extra: list[str] | None = None) -> str:
    """A warning naming how many lines of the source (and pages, when known) read like instructions to an AI; the
    lines themselves are never quoted. Fenced code is ignored; '' when there are none."""
    count = 0
    pages: list[int] = []
    page: int | None = None
    for code, lines in fenced_segments(markdown.split("\n")):
        if code:
            continue
        for line in lines:
            if m := PAGE_MARKER_RE.search(line):
                page = int(m.group(1))
            elif INSTRUCTION_LIKE.search(line):
                count += 1
                if page is not None and page not in pages:
                    pages.append(page)
    count += sum(1 for text in extra or () if INSTRUCTION_LIKE.search(text))
    if not count:
        return ""
    where = f" (page{'s' if len(pages) > 1 else ''} {', '.join(map(str, pages[:8]))})" if pages else ""
    return (f"{count} passage(s) in the source read like instructions to an AI (such as \"ignore the previous "
            f"instructions\"){where}. They are treated as ordinary source text and never followed; remove them unless "
            "they belong to the lesson.")


def _kill_tree(pid: int) -> None:
    import psutil

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)


def pdf_timeout_seconds(max_pages: int) -> float:
    return float(min(900, 90 + 1.2 * max_pages))


PDF_REAP_SECONDS = 10.0  # how long a killed PDF worker may take to be reaped


async def _stop_pdf_worker(proc: asyncio.subprocess.Process) -> None:
    """Kill the PDF worker's process tree and reap it, so its pipes and process handle are closed (and, on Windows,
    its files released) before the temp dir is removed; a worker that will not exit is logged, never waited on."""
    await asyncio.to_thread(_kill_tree, proc.pid)
    try:
        await asyncio.wait_for(proc.wait(), PDF_REAP_SECONDS)
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("the PDF worker (pid %s) did not exit after being killed", proc.pid)


async def run_pdf_worker(pdf: bytes, max_pages: int, timeout: float) -> tuple[dict[str, Any], Path]:
    """Run the PDF worker; returns (result dict, output dir). Caller removes ``out.parent``."""
    tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="aadhi-pdf-"))
    src, out = tmp / "source.pdf", tmp / "out"
    await asyncio.to_thread(src.write_bytes, pdf)
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(WORKER_PATH), str(src), str(out), str(max_pages),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=str(tmp),
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        await _stop_pdf_worker(proc)
        await asyncio.to_thread(shutil.rmtree, tmp, True)
        raise IngestError(f"reading the PDF took longer than {int(timeout)} s") from exc
    except asyncio.CancelledError:
        await _stop_pdf_worker(proc)
        await asyncio.to_thread(shutil.rmtree, tmp, True)
        raise
    if proc.returncode != 0:
        await asyncio.to_thread(shutil.rmtree, tmp, True)
        if proc.returncode == 3:
            raise IngestError("the PDF is password protected")
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise IngestError(f"cannot read the PDF ({detail[0][:200]})")
    data = await asyncio.to_thread((out / "result.json").read_text, encoding="utf-8")
    return json.loads(data), out


async def _store_figure(ctx: Any, figure_id: str, data: bytes, mime: str, w: int, h: int, page: int | None, caption: str) -> SourceFigure:
    key = bytes_key("figure", data)
    produced = Produced(data=data, mime=mime, width=w, height=h, meta={"page": page, "caption": caption[:300]})
    asset = await asyncio.to_thread(ctx.assets.put, key, "figure", produced, created_by=ctx.user_id)
    return SourceFigure(id=figure_id, caption=caption[:600], page=page, asset_key=asset.key, width=w, height=h)


async def _ingest_pdf(ctx: Any, raw: bytes) -> tuple[str, list[SourceFigure], int, list[str], bool, bool, PdfLayout]:
    settings = ctx.settings
    max_pages = settings.max_source_pages
    result, out = await run_pdf_worker(raw, max_pages, pdf_timeout_seconds(max_pages))
    try:
        layout = build_layout(result)
        figures: list[SourceFigure] = []
        for fig in layout.figures:
            data = await asyncio.to_thread((out / fig.file).read_bytes)
            figures.append(await _store_figure(ctx, fig.figure_id, data, fig.mime, fig.width, fig.height, fig.page, fig.caption))
    finally:
        await asyncio.to_thread(shutil.rmtree, out.parent, True)
    warnings: list[str] = []
    truncated = False
    attach = False
    if layout.page_count > layout.pages:
        truncated = True
        warnings.append(f"Only the first {layout.pages} of {layout.page_count} pages were used (page limit).")
    # Provider-neutral wording: whether the original actually reaches the model is decided (and
    # reported) at planning time, by provider and file size.
    if layout.pages and layout.scanned_pages / layout.pages >= 0.3:
        attach = True
        warnings.append(f"{layout.scanned_pages} page(s) look scanned; their text could not be extracted.")
    if layout.math_ratio >= 0.02:
        attach = True
        warnings.append("The PDF is maths-heavy; equations may not be extracted correctly as text.")
    if layout.bad_ratio >= 0.01:
        attach = True
        warnings.append("Some text in the PDF could not be decoded.")
    if layout.pages and readable_chars(layout.markdown) < 150 * layout.pages and not attach:
        attach = True
        warnings.append("The PDF contains little extractable text.")
    return layout.markdown, figures, layout.pages, warnings, attach, truncated, layout


def pdf_title(layout_title: str, markdown: str) -> str:
    """The PDF's own title for the title card: its first top-level heading (or, without one, its metadata title),
    unless that reads as an administrative line (a subject / unit / session line, a header field, a file name)."""
    heading = ""
    for line in markdown.splitlines():
        s = line.strip()
        if fence_open(s) is not None:
            break  # code before any heading: no title heading (a "# comment" in it is code)
        if s.startswith("# "):
            heading = s[2:].strip()
            break
        if s.startswith("#") or (s and not s.startswith("<!--") and len(s) > 200):
            break  # a section heading or body text first: the document has no title heading
    for candidate in (heading, layout_title):
        title = " ".join(normalize_extracted_text(candidate or "").split())
        title, _ = strip_title_admin(title)
        if not (3 <= len(title) <= 200) or not any(c.isalpha() for c in title):
            continue
        if _FILE_TITLE.search(title) or parse_title_line(title) is not None or admin_key_line(title):
            continue
        return title
    return ""


async def _ingest_docx(ctx: Any, raw: bytes) -> tuple[str, list[SourceFigure], list[str]]:
    try:
        res = await asyncio.to_thread(docx_to_markdown, raw)
    except DocxRejected as exc:
        raise IngestError(str(exc)) from exc
    figures = [await _store_figure(ctx, im.figure_id, im.data, im.mime, im.width, im.height, None, im.caption) for im in res.images]
    return res.markdown, figures, res.warnings


def extract_key(sha256: str, mime: str, settings: Any) -> str:
    """Content address of the IngestResult for a source (changes with ingest settings/version)."""
    return compute_key("extract", {
        "sha256": sha256, "mime": mime, "max_pages": settings.max_source_pages,
        "max_chars": settings.max_source_chars, "ingest": INGEST_VERSION,
    })


async def _build(ctx: Any, source: Any) -> IngestResult:
    raw = await asyncio.to_thread(ctx.storage.get_bytes, source.storage_key)
    kind = source_kind(source.mime, source.filename)
    figures: list[SourceFigure] = []
    warnings: list[str] = []
    attach = truncated = False
    title = ""
    pdf_excluded: list[ExcludedItem] = []
    pages: int | None = None
    if kind == "pdf":
        markdown, figures, pages, warnings, attach, truncated, layout = await _ingest_pdf(ctx, raw)
        title = pdf_title(layout.title, markdown)
        # the running header / footer lines and page numbers the layout dropped (shown in the scope report)
        pdf_excluded = [ExcludedItem(category="admin", text=t[:300], reason="running header, footer or page number")
                        for t in layout.removed]
    elif kind == "docx":
        markdown, figures, warnings = await _ingest_docx(ctx, raw)
    else:
        text = decode_text(raw)
        markdown = await asyncio.to_thread(markdown_source if kind == "markdown" else text_to_markdown, text)
    # ligatures ("veriﬁcation"), non-breaking / zero-width spaces and mojibake ("â€™"): targeted fixes, never NFKC
    markdown = normalize_extracted_text(markdown)
    figures = [f.model_copy(update={"caption": normalize_extracted_text(f.caption)}) if f.caption else f for f in figures]
    # teachable content only: metadata, admin/production data and visual directions move out
    scope = await asyncio.to_thread(scope_source, markdown, figures=figures)
    markdown, figures = scope.markdown, scope.figures
    warnings.extend(scope.warnings)
    meta = scope.document_meta
    # a PDF's own title names the session when the source has none (an SME script's first heading is its packaging)
    if (title and not meta.session_title and scope.source_format != "sme_script"
            and title.lower() not in (meta.subject_name.lower(), meta.unit_name.lower())):
        meta = meta.model_copy(update={"session_title": title[:300]})
    markdown, cut = truncate_markdown(markdown, ctx.settings.max_source_chars)
    if cut:
        truncated = True
        warnings.append(f"The source is very long; only the first {ctx.settings.max_source_chars:,} characters were used.")
        kept = set(re.findall(r"\[Figure ([a-z0-9\-]+)", markdown))
        figures = [f for f in figures if f.id in kept]
        markdown = close_open_fence(markdown)
    chunks = chunk_markdown(markdown)
    if readable_chars(markdown) == 0 and not attach:  # figure markers alone are not text
        raise IngestError("no readable text was found in the source")
    visual_notes = resolve_visual_notes(scope.visual_notes_raw, chunks, drop_unresolved=cut)
    if script := unsupported_script(markdown):
        warnings.append(f"The source is mostly written in {script} script, which narration does not support; the "
                        "lecture is narrated in the language chosen for it, so check names and terms against the source.")
    if warning := instruction_like_warning(markdown, [n.text for n in visual_notes]):
        warnings.append(warning)
    return IngestResult(
        markdown=markdown,
        chunks=chunks,
        document_meta=meta,
        visual_notes=visual_notes,
        excluded=(pdf_excluded + scope.excluded)[:500],
        source_format=scope.source_format,
        pages=pages,
        figures=figures,
        attach_original=attach,
        source_mime=source.mime,
        source_storage_key=source.storage_key,
        detected_language=detect_language(markdown),
        warnings=warnings,
        truncated=truncated,
    )


async def ingest_source(ctx: Any, source_doc: Any) -> IngestResult:
    """Ingest ``source_doc`` (SourceDocument row or snapshot) with caching by content."""
    key = extract_key(source_doc.sha256, source_doc.mime, ctx.settings)
    existing = await asyncio.to_thread(ctx.assets.get, key, verify_blob=True)
    if existing is not None:
        payload = await asyncio.to_thread(ctx.storage.get_bytes, existing.storage_key)
        result = IngestResult.model_validate_json(payload)
        result = result.model_copy(update={"source_storage_key": source_doc.storage_key, "source_mime": source_doc.mime})
        log.info("ingest cache hit for source %s", source_doc.id)
    else:
        result = await _build(ctx, source_doc)
        produced = Produced(
            data=result.model_dump_json().encode("utf-8"), mime="application/json",
            meta={"chunks": len(result.chunks), "figures": len(result.figures), "pages": result.pages},
        )
        existing = await asyncio.to_thread(ctx.assets.put, key, "extract", produced, created_by=ctx.user_id)

    def record() -> None:
        with ctx.session() as db:
            dbops.record_extract(db, source_doc.id, existing.storage_key, result.pages)
            dbops.ensure_asset_refs(db, source_doc.project_id, [key, *(f.asset_key for f in result.figures if f.asset_key)])

    await asyncio.to_thread(record)
    summary = scope_summary(result.excluded, sections=len(result.chunks), visual_notes=len(result.visual_notes),
                            source_format=result.source_format)
    if summary:  # categories and counts only: the excluded values never reach job events
        ctx.log(summary)
    return result


async def load_ingest(ctx: Any, extract_storage_key: str) -> IngestResult:
    """Load a stored IngestResult by its storage key (``SourceDocument.extracted_key``)."""
    payload = await asyncio.to_thread(ctx.storage.get_bytes, extract_storage_key)
    return IngestResult.model_validate_json(payload)


async def load_version_ingest(ctx: Any, ingest_key: Any) -> IngestResult | None:
    """The IngestResult a version was generated from, by the content-addressed extract key recorded in its
    ``generation_meta["ingest_key"]`` (None when unknown or no longer stored).

    ``SourceDocument.extracted_key`` names the *latest* extract of the source, which a newer
    ``INGEST_VERSION`` replaces (other chunk ids); a version keeps the chunks its ``source_refs`` point at."""
    if not isinstance(ingest_key, str) or not ingest_key:
        return None
    asset = await asyncio.to_thread(ctx.assets.get, ingest_key, verify_blob=True)
    if asset is None:
        return None
    try:
        return await load_ingest(ctx, asset.storage_key)
    except (OSError, ValueError, KeyError):
        log.info("stored extract %s unavailable", ingest_key[:24])
        return None
