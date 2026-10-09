"""PDF layout extraction worker (run in a subprocess by ``aadhi.pipeline.ingest``).

Standalone on purpose (stdlib + PyMuPDF only) so it can run as ``python -I pdf_worker.py``
with a minimal environment and be killed on timeout without affecting the job process.

Usage: ``python -I pdf_worker.py <input.pdf> <out_dir> <max_pages>``. Writes
``<out_dir>/result.json`` (pages -> blocks/lines with font sizes, tables, image metadata) and the
extracted images as ``<out_dir>/img-<n>.<ext>``. Exit codes: 0 ok, 2 unreadable, 3 encrypted.
A line set (at least 80 %) in a monospaced font also carries ``mono: true`` and ``lead`` (its leading
spaces); readers treat a missing key as False / 0.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import pymupdf
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz as pymupdf  # type: ignore[no-redef]

MIN_IMAGE_SIDE = 150  # longest side (px) for an embedded image to count as a figure
MIN_IMAGE_SHORT_SIDE = 48  # rules/separators are not figures
MAX_IMAGES = 150
MAX_IMAGE_PIXELS = 50_000_000
_MATH_FONT = re.compile(r"CMMI|CMSY|CMEX|CMR\d|Symbol|Math|STIX|MTExtra|MT Extra|Euclid", re.I)
# A fallback for fonts without the monospace flag. Not "Monotype..." (MonotypeCorsiva is a decorative title font)
# and not "code" inside a longer word (Barcode39, Code2000); Source Code / Fira Code / Cascadia Code are named.
_MONO_FONT = re.compile(r"mono(?!type)|courier|consol|menlo|inconsolata|typewriter|fixedsys|"
                        r"sourcecode|firacode|cascadiacode|(?<![a-z])code(?![a-z0-9])", re.I)
_MONO_FLAG = 8  # PyMuPDF span flag: monospaced font
_MATH_CHARS = re.compile(r"[Ͱ-Ͽ∀-⋿←-⇿⁰-₟⟀-⟯⦀-⫿]|[\U0001d400-\U0001d7ff]")


def _bbox(r: Any) -> list[float]:
    return [round(float(v), 2) for v in (r[0], r[1], r[2], r[3])]


def _page_blocks(page: Any) -> tuple[list[dict], int, int, int]:
    flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_DEHYPHENATE
    d = page.get_text("dict", flags=flags)
    blocks: list[dict] = []
    chars = math_chars = bad = 0
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        lines = []
        for ln in b.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            raw = "".join(s["text"] for s in ln["spans"])
            text = raw.strip()
            n = sum(len(s["text"]) for s in spans)
            size = max(spans, key=lambda s: len(s["text"]))["size"]
            bold_chars = sum(len(s["text"]) for s in spans if (s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower())
            mono_chars = sum(len(s["text"]) for s in spans
                             if (s.get("flags", 0) & _MONO_FLAG) or _MONO_FONT.search(s.get("font", "")))
            chars += len(text)
            math_chars += len(_MATH_CHARS.findall(text))
            math_chars += sum(len(s["text"]) for s in spans if _MATH_FONT.search(s.get("font", "")))
            bad += text.count("�") + text.count("(cid:")
            line = {"text": text, "size": round(float(size), 2), "bold": bold_chars >= 0.6 * max(n, 1),
                    "bbox": _bbox(ln["bbox"])}
            if mono_chars >= 0.8 * max(n, 1):  # a monospaced line: code (pdf_layout rebuilds its indentation)
                line["mono"] = True
                line["lead"] = len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip())  # leading spaces drawn as glyphs
            lines.append(line)
        if lines:
            blocks.append({"bbox": _bbox(b["bbox"]), "lines": lines})
    return blocks, chars, math_chars, bad


def _page_tables(page: Any) -> list[dict]:
    try:
        found = page.find_tables()
    except Exception:  # table detection is best effort
        return []
    out = []
    for t in getattr(found, "tables", []):
        try:
            rows = t.extract()
        except Exception:
            continue
        rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows if r]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            out.append({"bbox": _bbox(t.bbox), "rows": rows})
    return out


def _image_bytes(doc: Any, xref: int, smask: int) -> tuple[bytes, str] | None:
    info = doc.extract_image(xref)
    if not info:
        return None
    ext = (info.get("ext") or "").lower()
    if ext in ("png", "jpeg", "jpg") and not smask and not info.get("smask"):
        return info["image"], "image/png" if ext == "png" else "image/jpeg"
    pix = pymupdf.Pixmap(doc, xref)
    mask_xref = smask or info.get("smask") or 0
    if mask_xref:
        try:
            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, mask_xref))
        except Exception:
            pass
    if pix.colorspace is not None and pix.colorspace.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("png"), "image/png"


def _page_images(doc: Any, page: Any, out_dir: Path, seen_xref: set[int], seen_hash: set[str], counter: list[int]) -> tuple[list[dict], float]:
    images: list[dict] = []
    page_area = max(1.0, page.rect.width * page.rect.height)
    covered = 0.0
    for img in page.get_images(full=True):
        xref, smask, w, h = img[0], img[1], int(img[2]), int(img[3])
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for r in rects:
            covered += max(0.0, (r & page.rect).width * (r & page.rect).height)
        if xref in seen_xref or counter[0] >= MAX_IMAGES:
            continue
        seen_xref.add(xref)
        if max(w, h) < MIN_IMAGE_SIDE or min(w, h) < MIN_IMAGE_SHORT_SIDE or w * h > MAX_IMAGE_PIXELS:
            continue
        try:
            extracted = _image_bytes(doc, xref, smask)
        except Exception:
            continue
        if extracted is None:
            continue
        data, mime = extracted
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_hash:
            continue
        seen_hash.add(digest)
        counter[0] += 1
        name = f"img-{counter[0]}.{'png' if mime == 'image/png' else 'jpg'}"
        (out_dir / name).write_bytes(data)
        images.append({"file": name, "mime": mime, "width": w, "height": h, "sha256": digest,
                       "bbox": _bbox(rects[0]) if rects else None})
    return images, min(1.0, covered / page_area)


def extract(src: Path, out_dir: Path, max_pages: int) -> dict:
    doc = pymupdf.open(str(src))
    try:
        if doc.needs_pass:
            raise PermissionError("encrypted")
        result: dict[str, Any] = {"page_count": doc.page_count, "pages": [], "metadata": {}}
        meta = doc.metadata or {}
        result["metadata"] = {k: meta.get(k) or "" for k in ("title", "author", "subject")}
        seen_xref: set[int] = set()
        seen_hash: set[str] = set()
        counter = [0]
        for pno in range(min(doc.page_count, max_pages)):
            page = doc[pno]
            blocks, chars, math_chars, bad = _page_blocks(page)
            images, image_area = _page_images(doc, page, out_dir, seen_xref, seen_hash, counter)
            result["pages"].append({
                "number": pno + 1,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "blocks": blocks,
                "tables": _page_tables(page),
                "images": images,
                "image_area": round(image_area, 4),
                "chars": chars,
                "math_chars": math_chars,
                "bad_chars": bad,
            })
        return result
    finally:
        doc.close()


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: pdf_worker.py <input.pdf> <out_dir> <max_pages>", file=sys.stderr)
        return 64
    src, out_dir, max_pages = Path(argv[1]), Path(argv[2]), int(argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass
    try:
        result = extract(src, out_dir, max_pages)
    except PermissionError:
        print("the PDF is password protected", file=sys.stderr)
        return 3
    except Exception as exc:  # unreadable / corrupt
        print(f"cannot read PDF: {type(exc).__name__}", file=sys.stderr)
        return 2
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through a subprocess
    sys.exit(main(sys.argv))
