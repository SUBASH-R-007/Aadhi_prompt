"""DOCX -> Markdown (+ embedded images) via mammoth and a small HTML walker.

Paragraphs in a code style (Code, Source Code, HTML Preformatted; ``CODE_STYLE_MAP``) become one fenced
block per run of consecutive paragraphs, with their indentation and line breaks (soft breaks too) kept. A
"~~~~" divider paragraph is escaped so it never opens a fence.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field

from .chunking import looks_like_heading
from .codeblocks import code_language, dedent_lines, escape_fence, fence_block, tilde_divider

MAX_UNCOMPRESSED = 250 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_RATIO = 200
MIN_IMAGE_SIDE = 150
MIN_IMAGE_SHORT_SIDE = 48
MAX_IMAGES = 150
_CAPTION_RE = re.compile(r"^\s*(fig(?:ure)?\.?\s*\d+[\w.\-]*)", re.I)
_SRC_PREFIX = "aadhi-figure:"
# Word's code paragraph styles -> one <pre> per run of consecutive paragraphs (mammoth joins them with newlines)
CODE_STYLE_MAP = "\n".join(
    f"p[style-name='{name}'] => pre:separator('\\n')" for name in ("Code", "Source Code", "HTML Preformatted")
)


class DocxRejected(ValueError):
    """The DOCX is malformed or exceeds safety limits."""


@dataclass
class DocxImage:
    figure_id: str
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str
    caption: str = ""


@dataclass
class DocxResult:
    markdown: str
    images: list[DocxImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_zip(data: bytes) -> None:
    """Zip-bomb / sanity limits for OOXML packages."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocxRejected("not a valid .docx file") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise DocxRejected("the .docx has too many parts")
        total = 0
        for info in infos:
            total += info.file_size
            if info.file_size > 10 * 1024 * 1024 and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise DocxRejected("the .docx contains a suspiciously compressed part")
        if total > MAX_UNCOMPRESSED:
            raise DocxRejected("the .docx is too large when uncompressed")
        if "word/document.xml" not in {i.filename for i in infos}:
            raise DocxRejected("not a Word document")


def _normalise_image(raw: bytes) -> tuple[bytes, str, int, int] | None:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            if fmt in ("PNG", "JPEG", "WEBP", "GIF"):
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[fmt]
                return raw, mime, w, h
            im.load()
            buf = io.BytesIO()
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, format="PNG")
            return buf.getvalue(), "image/png", w, h
    except Exception:  # unsupported (EMF/WMF), corrupt or decompression bomb
        return None


class _Converter:
    def __init__(self) -> None:
        self.images: dict[str, DocxImage] = {}
        self.by_hash: dict[str, str] = {}
        self.skipped = 0

    def image_handler(self, image) -> dict[str, str]:  # noqa: ANN001 - mammoth image object
        with image.open() as f:
            raw = f.read()
        norm = _normalise_image(raw)
        if norm is None:
            self.skipped += 1
            return {"src": ""}
        data, mime, w, h = norm
        if max(w, h) < MIN_IMAGE_SIDE or min(w, h) < MIN_IMAGE_SHORT_SIDE:
            return {"src": ""}
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.by_hash:
            return {"src": _SRC_PREFIX + self.by_hash[digest]}
        if len(self.images) >= MAX_IMAGES:
            return {"src": ""}
        fid = f"fig-{len(self.images) + 1}"
        self.by_hash[digest] = fid
        self.images[fid] = DocxImage(fid, data, mime, w, h, digest, caption=(image.alt_text or "").strip()[:300])
        return {"src": _SRC_PREFIX + fid}


def _inline(node) -> str:  # noqa: ANN001 - bs4 node
    from bs4 import NavigableString, Tag

    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    name = node.name.lower()
    if name == "br":
        return " "
    if name == "img":
        return ""
    inner = "".join(_inline(c) for c in node.children)
    if not inner.strip():
        return inner
    if name in ("strong", "b"):
        return f"**{inner.strip()}**"
    if name in ("em", "i"):
        return f"*{inner.strip()}*"
    if name == "code":
        return f"`{inner}`"
    if name == "sup":
        return f"^{inner}"
    if name == "sub":
        return f"_{inner}"
    return inner


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _list_md(node, depth: int = 0) -> list[str]:  # noqa: ANN001
    lines: list[str] = []
    ordered = node.name.lower() == "ol"
    n = 0
    for li in node.find_all("li", recursive=False):
        n += 1
        nested = [c for c in li.find_all(["ul", "ol"], recursive=False)]
        for c in nested:
            c.extract()
        marker = f"{n}." if ordered else "-"
        lines.append(f"{'  ' * depth}{marker} {_clean(_inline(li))}")
        for c in nested:
            lines.extend(_list_md(c, depth + 1))
    return lines


def _table_md(node) -> str:  # noqa: ANN001
    rows = []
    for tr in node.find_all("tr"):
        cells = [_clean(_inline(td)).replace("|", "\\|") for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(c or f"Col{i + 1}" for i, c in enumerate(rows[0])) + " |", "|" + "|".join("---" for _ in range(width)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _all_bold(node) -> bool:  # noqa: ANN001 - bs4 node
    from bs4 import NavigableString

    texts = [s for s in node.find_all(string=True) if isinstance(s, NavigableString) and s.strip()]
    return bool(texts) and all(s.find_parent(["strong", "b"]) is not None for s in texts)


def _pseudo_heading_level(node, has_headings: bool) -> int:  # noqa: ANN001 - bs4 node
    """Heading level for a paragraph that is styled like a title (0 = ordinary paragraph)."""
    plain = _clean(node.get_text(" "))
    bold = _all_bold(node)
    if not looks_like_heading(plain, bold=bold):
        return 0
    if has_headings:  # real heading styles exist: such paragraphs are minor sub-headings
        return 4
    return 2 if sum(c.isupper() for c in plain) >= 0.9 * max(1, sum(c.isalpha() for c in plain)) else 3


def html_to_markdown(html: str, images: dict[str, DocxImage]) -> str:
    """Convert mammoth's HTML to Markdown with ``[Figure <id>: caption]`` markers."""
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html, "html.parser")
    top = [c for c in soup.children if isinstance(c, Tag)]
    has_headings = soup.find(re.compile(r"^h[1-6]$")) is not None
    parts: list[str] = []
    for idx, node in enumerate(top):
        name = node.name.lower()
        figs = [img.get("src", "")[len(_SRC_PREFIX):] for img in node.find_all("img") if str(img.get("src", "")).startswith(_SRC_PREFIX)]
        if re.fullmatch(r"h[1-6]", name):
            text = _clean(_inline(node))
            if text:
                parts.append(f"{'#' * int(name[1])} {text}")
        elif name in ("ul", "ol"):
            parts.append("\n".join(_list_md(node)))
        elif name == "table":
            md = _table_md(node)
            if md:
                parts.append(md)
        elif name == "pre":  # code style: verbatim text (no emphasis markers), indentation kept
            for br in node.find_all("br"):  # a Word soft line break (Shift+Enter) inside the paragraph: a new line
                br.replace_with("\n")
            lines = dedent_lines(node.get_text().split("\n"))
            while lines and not lines[-1].strip():
                lines.pop()
            while lines and not lines[0].strip():
                lines.pop(0)
            if lines:
                parts.append("\n".join(fence_block(lines, code_language("\n".join(lines)))))
        else:
            text = _clean(_inline(node))
            level = _pseudo_heading_level(node, has_headings) if name == "p" and text else 0
            if level:
                parts.append(f"{'#' * level} {_clean(node.get_text(' '))}")
            elif text:  # a "~~~~" divider paragraph is decoration, never a fence (code comes from code styles)
                parts.append(escape_fence(text) if tilde_divider(text) else text)
        for fid in dict.fromkeys(figs):
            img = images.get(fid)
            if img is None:
                continue
            if not img.caption:
                for neighbour in (top[idx + 1] if idx + 1 < len(top) else None, top[idx - 1] if idx else None):
                    if neighbour is not None:
                        t = _clean(neighbour.get_text(" "))
                        if _CAPTION_RE.match(t):
                            img.caption = t[:300]
                            break
            caption = f": {img.caption}" if img.caption else ""
            parts.append(f"[Figure {fid}{caption}]")
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def docx_to_markdown(data: bytes) -> DocxResult:
    """Convert a .docx to Markdown with figures (raises DocxRejected)."""
    import mammoth

    check_zip(data)
    conv = _Converter()
    try:
        result = mammoth.convert_to_html(io.BytesIO(data), convert_image=mammoth.images.img_element(conv.image_handler),
                                         style_map=CODE_STYLE_MAP)
    except DocxRejected:
        raise
    except Exception as exc:
        raise DocxRejected(f"cannot read the .docx ({type(exc).__name__})") from exc
    markdown = html_to_markdown(result.value, conv.images)
    used = set(re.findall(r"\[Figure (fig-\d+)", markdown))
    images = [img for fid, img in conv.images.items() if fid in used]
    warnings: list[str] = []
    if conv.skipped:
        warnings.append(f"{conv.skipped} embedded image(s) in an unsupported format were skipped")
    return DocxResult(markdown=markdown, images=images, warnings=warnings)
