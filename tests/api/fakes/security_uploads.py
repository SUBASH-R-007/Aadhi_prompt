"""Fake aadhi.security.uploads: extension + magic-byte allow-list."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePath


@dataclass
class UploadInfo:
    mime: str
    ext: str
    kind: str


class UploadRejected(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


TYPES = {
    "pdf": ("application/pdf", "pdf", lambda d: d.startswith(b"%PDF-")),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
        lambda d: d.startswith(b"PK\x03\x04"),
    ),
    "txt": ("text/plain", "text", _is_text),
    "md": ("text/markdown", "markdown", _is_text),
    "json": ("application/json", "json", lambda d: _is_text(d) and _json_ok(d)),
    "png": ("image/png", "image", lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    "jpg": ("image/jpeg", "image", lambda d: d.startswith(b"\xff\xd8\xff")),
    "jpeg": ("image/jpeg", "image", lambda d: d.startswith(b"\xff\xd8\xff")),
    "webp": ("image/webp", "image", lambda d: d[:4] == b"RIFF" and d[8:12] == b"WEBP"),
    "gif": ("image/gif", "image", lambda d: d[:6] in (b"GIF87a", b"GIF89a")),
    "mp4": ("video/mp4", "video", lambda d: d[4:8] == b"ftyp"),
    "webm": ("video/webm", "video", lambda d: d.startswith(b"\x1a\x45\xdf\xa3")),
}


def _json_ok(data: bytes) -> bool:
    try:
        json.loads(data.decode("utf-8-sig"))
    except ValueError:
        return False
    return True


def validate_upload(filename: str, data: bytes, *, allowed_kinds: set[str], max_bytes: int) -> UploadInfo:
    if len(data) > max_bytes:
        raise UploadRejected(413, "too_large", "File too large.")
    ext = PurePath(filename or "").suffix.lower().lstrip(".")
    spec = TYPES.get(ext)
    if spec is None:
        raise UploadRejected(415, "unsupported_type", f"File type .{ext} is not allowed.")
    mime, kind, check = spec
    if kind not in allowed_kinds:
        raise UploadRejected(415, "unsupported_type", f"{kind} files are not allowed here.")
    if not check(data):
        raise UploadRejected(415, "unsupported_type", "File content does not match its extension.")
    return UploadInfo(mime=mime, ext=ext, kind=kind)


def safe_display_name(filename: str) -> str:
    name = PurePath((filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]+", "_", name).strip(" .")
    return name[:255] or "file"
