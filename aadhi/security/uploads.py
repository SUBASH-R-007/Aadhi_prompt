"""Upload validation: extension + MIME + magic bytes allow-list and per-type deep checks.

Accepted: png, jpg/jpeg, gif, webp (``image``); mp4, webm (``video``); mp3, wav (``audio``);
pdf (``pdf``); docx (``docx``); json (``json``); txt (``text``); md/markdown (``markdown``).
Never accepted: html, svg, xml, js, executables, archives other than docx.

Deep checks:

* images: the declared format must match the magic bytes; Pillow opens it restricted to that format
  with ``Image.MAX_IMAGE_PIXELS = 50_000_000`` and ``DecompressionBombWarning`` promoted to an error,
  then ``verify()``; GIF/WebP/JPEG first frames are decoded (JPEG in draft mode). Polyglots are
  refused: the end of the image is found structurally (PNG ``IEND``, GIF block walk to the ``;``
  trailer, JPEG segment/scan walk to ``EOI``, WebP RIFF size) and only zero padding may follow
  (JPEG also allows the second-image / Motion Photo / Samsung trailers phones append, if they carry
  no markup); an image that is also a valid zip archive is always refused;
* mp4: ``ftyp`` with a video brand and a sane top-level box structure containing ``moov``/``moof``;
  webm: EBML header with ``webm`` doctype; mp3: ID3 tag or a valid MPEG audio frame header; wav:
  ``RIFF``/``WAVE`` with a sane ``fmt`` chunk; pdf: ``%PDF-`` at offset 0 and an ``%%EOF`` marker;
* docx: zip that contains ``word/document.xml`` and ``[Content_Types].xml``, no macros
  (``vbaProject.bin`` / macroEnabled content types), no encrypted/duplicate/path-traversal entries,
  declared uncompressed total <= 100 MB and compression ratio <= 100 (zipfile never inflates past
  an entry's declared size, so declared sizes bound the work done by later readers);
* json: strict UTF-8 + strict JSON (no NaN/Infinity); text/markdown: strict UTF-8, no NUL bytes, and
  not an HTML/SVG/XML document in disguise.
"""

from __future__ import annotations

import io
import re
import struct
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass

from PIL import Image

from .strict_json import loads_strict

__all__ = [
    "ALL_KINDS",
    "DOCX_MIME",
    "MAX_IMAGE_PIXELS",
    "UploadInfo",
    "UploadRejected",
    "safe_display_name",
    "sniff",
    "validate_upload",
]

MAX_IMAGE_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALL_KINDS = frozenset({"image", "video", "audio", "pdf", "docx", "json", "text", "markdown"})

# extension -> (kind, mime, canonical extension)
_EXT: dict[str, tuple[str, str, str]] = {
    "png": ("image", "image/png", "png"),
    "jpg": ("image", "image/jpeg", "jpg"),
    "jpeg": ("image", "image/jpeg", "jpg"),
    "gif": ("image", "image/gif", "gif"),
    "webp": ("image", "image/webp", "webp"),
    "mp4": ("video", "video/mp4", "mp4"),
    "webm": ("video", "video/webm", "webm"),
    "mp3": ("audio", "audio/mpeg", "mp3"),
    "wav": ("audio", "audio/wav", "wav"),
    "pdf": ("pdf", "application/pdf", "pdf"),
    "docx": ("docx", DOCX_MIME, "docx"),
    "json": ("json", "application/json", "json"),
    "txt": ("text", "text/plain", "txt"),
    "md": ("markdown", "text/markdown", "md"),
    "markdown": ("markdown", "text/markdown", "md"),
}

DOCX_MAX_UNCOMPRESSED = 100 * 1024 * 1024
DOCX_MAX_RATIO = 100
DOCX_MAX_ENTRIES = 10_000
_MP4_BRANDS = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"iso8",
        b"iso9",
        b"mp41",
        b"mp42",
        b"mp71",
        b"avc1",
        b"av01",
        b"dash",
        b"msnv",
        b"M4V ",
        b"M4VP",
        b"mmp4",
        b"f4v ",
        b"3gp4",
        b"3gp5",
        b"3gp6",
        b"3g2a",
        b"qt  ",
        b"MSNV",
        b"XAVC",
        b"cmfc",
        b"cmfs",
    }
)
_DISGUISED_MARKUP = re.compile(
    rb"^\s*<(?:!doctype\s+html|html[\s>]|svg[\s>]|\?xml|script[\s>]|body[\s>]|head[\s>])", re.IGNORECASE
)


@dataclass
class UploadInfo:
    """Validated upload. ``width``/``height`` are set for images."""

    mime: str
    ext: str
    kind: str  # image|video|audio|pdf|docx|json|text|markdown
    width: int | None = None
    height: int | None = None


class UploadRejected(Exception):
    """The upload is not acceptable. ``status_code``/``code`` map onto the API error envelope
    (413 ``too_large``, 415 ``unsupported_type``, 422 ``invalid_upload``)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _unsupported(message: str) -> UploadRejected:
    return UploadRejected(415, "unsupported_type", message)


def _invalid(message: str) -> UploadRejected:
    return UploadRejected(422, "invalid_upload", message)


# ---------------------------------------------------------------------------
# Magic bytes
# ---------------------------------------------------------------------------


def _is_mp3_frame(h: bytes) -> bool:
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return False
    version = (h[1] >> 3) & 0x3
    layer = (h[1] >> 1) & 0x3
    bitrate = (h[2] >> 4) & 0xF
    rate = (h[2] >> 2) & 0x3
    return version != 1 and layer != 0 and bitrate not in (0, 0xF) and rate != 3


def _mp3_ok(data: bytes) -> bool:
    if data[:3] == b"ID3":
        if len(data) < 10 or data[3] not in (2, 3, 4) or any(b & 0x80 for b in data[6:10]):
            return False
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        offset = 10 + size + (10 if data[5] & 0x10 else 0)
        # Allow a little padding between the tag and the first frame.
        window = data[offset : offset + 4096]
        return any(_is_mp3_frame(window[i : i + 4]) for i in range(max(len(window) - 3, 0)))
    return _is_mp3_frame(data[:4])


def sniff(data: bytes) -> str | None:
    """Detect a binary container from magic bytes (canonical extension or ``zip``), else None."""
    head = bytes(data[:64])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3" and b"\x42\x82" in head and b"webm" in head:
        return "webm"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if _mp3_ok(bytes(data[:16384])):
        return "mp3"
    return None


# ---------------------------------------------------------------------------
# Per-kind deep checks
# ---------------------------------------------------------------------------

_PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "gif": "GIF", "webp": "WEBP"}


def _truncated(name: str) -> UploadRejected:
    return _invalid(f"The {name} file is truncated")


def _png_end(data: bytes) -> int:
    """Offset just past the ``IEND`` chunk."""
    pos = 8
    n = len(data)
    for _ in range(100_000):
        if pos + 8 > n:
            raise _truncated("PNG")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > n:
            raise _truncated("PNG")
        if ctype == b"IEND":
            return end
        pos = end
    raise _invalid("The PNG file has too many chunks")


def _gif_skip_sub_blocks(data: bytes, pos: int) -> int:
    n = len(data)
    while True:  # every iteration advances by at least one byte
        if pos >= n:
            raise _truncated("GIF")
        size = data[pos]
        pos += 1
        if size == 0:
            return pos
        pos += size


def _gif_end(data: bytes) -> int:
    """Offset just past the ``;`` trailer, found by walking the block structure (a trailing ``;``
    byte alone proves nothing: appended JavaScript can end with one). A GIF that stops cleanly at a
    block boundary without a trailer ends at EOF."""
    n = len(data)
    if n < 13:
        raise _truncated("GIF")
    pos = 13
    if data[10] & 0x80:  # global colour table
        pos += 3 * (2 << (data[10] & 0x07))
    for _ in range(1_000_000):
        if pos == n:
            return n
        if pos > n:
            raise _truncated("GIF")
        block = data[pos]
        if block == 0x3B:
            return pos + 1
        if block == 0x21:  # extension: introducer, label, data sub-blocks
            pos = _gif_skip_sub_blocks(data, pos + 2)
        elif block == 0x2C:  # image descriptor (+ local colour table), LZW code size, data sub-blocks
            if pos + 10 > n:
                raise _truncated("GIF")
            flags = data[pos + 9]
            pos += 10
            if flags & 0x80:
                pos += 3 * (2 << (flags & 0x07))
            pos = _gif_skip_sub_blocks(data, pos + 1)
        else:
            raise _invalid("The GIF file structure is invalid")
    raise _invalid("The GIF file has too many blocks")


def _jpeg_scan_end(data: bytes, pos: int) -> int:
    """End of entropy-coded data starting at ``pos``: the next marker that is not a restart marker."""
    n = len(data)
    while True:
        i = data.find(b"\xff", pos)
        if i < 0 or i + 1 >= n:
            return n
        following = data[i + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:  # stuffed byte / restart marker
            pos = i + 2
            continue
        return i


def _jpeg_end(data: bytes) -> int:
    """Offset just past the end-of-image marker (``FFD9``), walking segments and scans like a decoder
    (extraneous bytes between segments are skipped, as libjpeg does)."""
    n = len(data)
    pos = 2  # after SOI
    while pos < n:
        if data[pos] != 0xFF:
            pos = data.find(b"\xff", pos)
            if pos < 0:
                break
        while pos < n and data[pos] == 0xFF:  # fill bytes
            pos += 1
        if pos >= n:
            break
        marker = data[pos]
        pos += 1
        if marker == 0xD9:
            return pos
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            continue
        if pos + 2 > n:
            break
        length = (data[pos] << 8) | data[pos + 1]
        if length < 2:
            raise _invalid("The JPEG file structure is invalid")
        pos += length
        if marker == 0xDA:  # start of scan: entropy-coded data up to the next marker
            pos = _jpeg_scan_end(data, pos)
    raise _invalid("The JPEG file is truncated (no end-of-image marker)")


def _webp_end(data: bytes) -> int:
    """``8 + RIFF size`` (the RIFF header declares the whole file)."""
    (size,) = struct.unpack("<I", data[4:8])
    end = 8 + size
    if end > len(data):
        raise _truncated("WebP")
    return end


_IMAGE_END = {"png": _png_end, "gif": _gif_end, "jpg": _jpeg_end, "webp": _webp_end}
_IMAGE_NAME = {"png": "PNG", "gif": "GIF", "jpg": "JPEG", "webp": "WebP"}
_MARKUP_IN_TRAILER = re.compile(
    rb"<\s*(?:!doctype|html|head|body|script|svg|iframe|object|embed|meta|\?xml)[\s>/]", re.IGNORECASE
)


def _benign_jpeg_trailer(trailer: bytes) -> bool:
    """Data phones legitimately append after a JPEG: a second JPEG (MPF / Ultra HDR gain map / depth
    map), a Motion Photo MP4, or Samsung's ``SEFT`` metadata trailer."""
    if trailer[4:8] == b"ftyp":  # MP4 box header: 4-byte size (often starting with NULs) + "ftyp"
        return True
    return trailer.lstrip(b"\x00").startswith(b"\xff\xd8\xff") or trailer.rstrip(b"\x00").endswith(b"SEFT")


def _check_image_trailer(data: bytes, ext: str) -> None:
    """Reject polyglots: data appended after the image's end marker (zero padding is fine; JPEG may
    carry a benign phone trailer without markup), and any image that is also a valid zip archive."""
    name = _IMAGE_NAME[ext]
    trailer = data[_IMAGE_END[ext](data) :]
    if trailer.strip(b"\x00"):
        benign = ext == "jpg" and _benign_jpeg_trailer(trailer) and not _MARKUP_IN_TRAILER.search(trailer)
        if not benign:
            raise _invalid(f"The {name} file has unexpected data after its end marker")
    if zipfile.is_zipfile(io.BytesIO(data)):
        raise _invalid(f"The {name} file also contains a zip archive")


def _check_image(data: bytes, ext: str) -> tuple[int, int]:
    if sniff(data) != ext:
        raise _unsupported(f"The file content is not a valid .{ext} image")
    fmt = _PIL_FORMAT[ext]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[fmt]) as im:
                width, height = im.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise _invalid("The image dimensions are too large")
                im.verify()
            if ext in ("gif", "webp", "jpg"):
                with Image.open(io.BytesIO(data), formats=[fmt]) as im:
                    if ext == "jpg":
                        im.draft("RGB", (max(1, width // 8), max(1, height // 8)))
                    im.load()
    except UploadRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _invalid("The image dimensions are too large") from exc
    except Exception as exc:
        raise _invalid("The image file is corrupt or not a supported image") from exc
    _check_image_trailer(data, ext)
    return width, height


def _check_mp4(data: bytes) -> None:
    if sniff(data) != "mp4":
        raise _unsupported("The file content is not a valid MP4 video")
    pos, n, seen = 0, len(data), set()
    for _ in range(10_000):
        if pos == n:
            break
        if pos + 8 > n:
            raise _invalid("The MP4 file is truncated")
        size, btype = struct.unpack(">I4s", data[pos : pos + 8])
        if not all(32 <= b < 127 for b in btype):
            raise _invalid("The MP4 file structure is invalid")
        header = 8
        if size == 1:
            if pos + 16 > n:
                raise _invalid("The MP4 file is truncated")
            (size,) = struct.unpack(">Q", data[pos + 8 : pos + 16])
            header = 16
        elif size == 0:
            size = n - pos
        if size < header:
            raise _invalid("The MP4 file structure is invalid")
        seen.add(btype)
        pos += size
        if pos > n:  # last box truncated (e.g. partial mdat): tolerate only for media data
            if btype not in (b"mdat", b"free", b"skip"):
                raise _invalid("The MP4 file is truncated")
            break
    if not ({b"moov", b"moof"} & seen):
        raise _invalid("The MP4 file has no movie header")


def _check_webm(data: bytes) -> None:
    if sniff(data) != "webm":
        raise _unsupported("The file content is not a valid WebM video")


def _check_audio(data: bytes, ext: str) -> None:
    if ext == "mp3":
        if sniff(data) != "mp3":
            raise _unsupported("The file content is not a valid MP3 file")
        return
    if sniff(data) != "wav":
        raise _unsupported("The file content is not a valid WAV file")
    pos = 12
    while pos + 8 <= min(len(data), 1 << 20):
        cid, size = struct.unpack("<4sI", data[pos : pos + 8])
        if cid == b"fmt ":
            if size < 16 or pos + 8 + 16 > len(data):
                break
            fmt_tag, channels, rate = struct.unpack("<HHI", data[pos + 8 : pos + 16])
            if fmt_tag in (1, 3, 0xFFFE) and 1 <= channels <= 8 and 8000 <= rate <= 192_000:
                return
            break
        pos += 8 + size + (size & 1)
    raise _invalid("The WAV file has no valid audio format header")


def _check_pdf(data: bytes) -> None:
    if sniff(data) != "pdf" or not re.match(rb"%PDF-[12]\.\d", data[:8]):
        raise _unsupported("The file content is not a valid PDF")
    if b"%%EOF" not in data[-32768:]:
        raise _invalid("The PDF file is truncated (no end-of-file marker)")


def _check_docx(data: bytes) -> None:
    if sniff(data) != "zip":
        raise _unsupported("The file content is not a valid DOCX document")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise _invalid("The DOCX file is corrupt") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > DOCX_MAX_ENTRIES:
            raise _invalid("The DOCX file has too many parts")
        names: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            lowered = name.lower()
            if name in names:
                raise _invalid("The DOCX file has duplicate parts")
            names.add(name)
            if name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/") or ":" in name:
                raise _invalid("The DOCX file has invalid part names")
            if info.flag_bits & 0x1:
                raise _invalid("Encrypted DOCX files are not supported")
            if "vbaproject" in lowered or lowered.endswith("vbadata.xml"):
                raise _invalid("Macro-enabled documents are not accepted")
            if info.file_size < 0 or info.compress_size < 0:
                raise _invalid("The DOCX file is corrupt")
            if info.file_size > 0 and info.compress_size == 0:
                raise _invalid("The DOCX file is corrupt")
            if info.file_size > 1024 * 1024 and info.file_size > DOCX_MAX_RATIO * max(info.compress_size, 1):
                raise _invalid("The DOCX file is suspiciously compressed (possible zip bomb)")
            total += info.file_size
        if total > DOCX_MAX_UNCOMPRESSED:
            raise _invalid("The DOCX file expands to more than 100 MB")
        if total > DOCX_MAX_RATIO * len(data):
            raise _invalid("The DOCX file is suspiciously compressed (possible zip bomb)")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise _unsupported("The file is a zip archive but not a Word (.docx) document")
        ct_info = zf.getinfo("[Content_Types].xml")
        if ct_info.file_size > 1024 * 1024:
            raise _invalid("The DOCX file is corrupt")
        try:
            content_types = zf.read(ct_info)
        except Exception as exc:
            raise _invalid("The DOCX file is corrupt") from exc
        if b"macroenabled" in content_types.lower() or b"vbaproject" in content_types.lower():
            raise _invalid("Macro-enabled documents are not accepted")


def _decode_text(data: bytes, what: str) -> str:
    raw = bytes(data)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if b"\x00" in raw:
        raise _invalid(f"The {what} file contains NUL bytes (binary content)")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _invalid(f"The {what} file is not valid UTF-8 text") from exc


def _check_json(data: bytes) -> None:
    text = _decode_text(data, "JSON")
    try:
        loads_strict(text)  # no NaN/Infinity literals and no overflowing numbers such as 1e400
    except (ValueError, RecursionError) as exc:
        raise _invalid("The JSON file is not valid JSON") from exc


def _check_text(data: bytes, what: str) -> None:
    if sniff(data) not in (None, "mp3"):  # a binary container is never text ("mp3" sniff is weak)
        raise _unsupported(f"The file is a binary file, not {what}")
    text = _decode_text(data, what)
    if _DISGUISED_MARKUP.match(text.encode("utf-8")[:512]):
        raise _unsupported(f"HTML/SVG/XML documents are not accepted as {what}")
    if any(unicodedata.category(c) == "Cc" and c not in "\t\n\r\f" for c in text[:200_000]):
        raise _invalid(f"The {what} file contains control characters")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extension(filename: str) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip().rstrip(". ")
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def validate_upload(filename: str, data: bytes, *, allowed_kinds: set[str], max_bytes: int) -> UploadInfo:
    """Validate an upload and return its canonical ``UploadInfo`` (raises ``UploadRejected``).

    ``allowed_kinds`` ⊆ {image, video, audio, pdf, docx, json, text, markdown}; ``max_bytes`` is the
    size cap for this endpoint/purpose.
    """
    unknown = set(allowed_kinds) - ALL_KINDS
    if unknown:
        raise ValueError(f"unknown upload kinds {sorted(unknown)}")
    size = len(data)
    if size > max_bytes:
        raise UploadRejected(413, "too_large", f"The file is too large (limit {max(1, max_bytes // (1024 * 1024))} MB)")
    if size == 0:
        raise _invalid("The file is empty")
    ext = _extension(filename)
    entry = _EXT.get(ext)
    if entry is None:
        raise _unsupported(f"Files of type '.{ext or '?'}' are not accepted")
    kind, mime, canonical = entry
    if kind not in allowed_kinds:
        raise _unsupported(f"'.{ext}' files are not accepted here")
    width = height = None
    if kind == "image":
        width, height = _check_image(bytes(data), canonical)
    elif canonical == "mp4":
        _check_mp4(bytes(data))
    elif canonical == "webm":
        _check_webm(bytes(data))
    elif kind == "audio":
        _check_audio(bytes(data), canonical)
    elif kind == "pdf":
        _check_pdf(bytes(data))
    elif kind == "docx":
        _check_docx(bytes(data))
    elif kind == "json":
        _check_json(bytes(data))
    else:
        _check_text(bytes(data), "Markdown" if kind == "markdown" else "text")
    return UploadInfo(mime=mime, ext=canonical, kind=kind, width=width, height=height)


_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED_STEMS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_display_name(filename: str, max_length: int = 120) -> str:
    """A harmless display name for a user-supplied filename (never used as a storage path).

    Drops directory parts, normalises Unicode (NFKC), removes control/bidi/path characters, collapses
    whitespace, avoids Windows reserved names and limits the length (keeping the extension).
    """
    name = unicodedata.normalize("NFKC", filename or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf", "Co", "Cs"))
    name = _UNSAFE_NAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".").strip()
    if not name:
        return "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.strip().lower() in _RESERVED_STEMS:
        stem = f"_{stem}"
    ext = ext[:16]
    room = max_length - (len(ext) + 1 if ext else 0)
    stem = stem[: max(room, 1)].rstrip(" .") or "file"
    return f"{stem}.{ext}" if ext else stem
