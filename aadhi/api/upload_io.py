"""Reading multipart uploads and probing media metadata (blocking: call from sync endpoints)."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from ..config import Settings
from .errors import ApiException

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024
FFPROBE_TIMEOUT_SECONDS = 20
FFPROBE_FORMATS = {"video/mp4": "mov", "video/webm": "matroska"}


def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an uploaded file fully, refusing more than ``max_bytes`` (413 ``too_large``)."""
    buf = io.BytesIO()
    file.file.seek(0)
    while True:
        chunk = file.file.read(CHUNK)
        if not chunk:
            break
        buf.write(chunk)
        if buf.tell() > max_bytes:
            raise ApiException(413, "too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    data = buf.getvalue()
    if not data:
        raise ApiException(
            422, "validation", [{"loc": ["body", "file"], "msg": "The file is empty.", "type": "file.empty"}]
        )
    return data


@dataclass
class MediaProbe:
    """Measured media metadata (all optional)."""

    width: int | None = None
    height: int | None = None
    duration_s: float | None = None


def probe_image(data: bytes) -> MediaProbe:
    """Image dimensions from the header (decompression bombs -> 413, garbage -> 415)."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Image.DecompressionBombError:
        raise ApiException(413, "too_large", "Image dimensions are too large.") from None
    except (OSError, ValueError, SyntaxError, EOFError):
        raise ApiException(415, "unsupported_type", "The image could not be read.") from None
    return MediaProbe(width=int(width), height=int(height))


def probe_video(data: bytes, mime: str, settings: Settings) -> MediaProbe:
    """Duration and size via ffprobe (best effort: missing ffprobe -> empty probe)."""
    fmt = FFPROBE_FORMATS.get(mime)
    if fmt is None:
        return MediaProbe()
    fd, tmp = tempfile.mkstemp(suffix=".bin", prefix="aadhi-probe-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        cmd = [
            settings.ffprobe_path,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            fmt,
            "-show_entries",
            "format=duration:stream=width,height,codec_type",
            "-of",
            "json",
            "-i",
            str(Path(tmp)),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("ffprobe failed for an upload: %s", type(exc).__name__)
            return MediaProbe()
        if proc.returncode != 0:
            raise ApiException(415, "unsupported_type", "The video could not be read.")
        try:
            info = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            return MediaProbe()
        probe = MediaProbe()
        try:
            duration = float(info.get("format", {}).get("duration"))
            probe.duration_s = round(duration, 3) if duration > 0 else None
        except (TypeError, ValueError):
            pass
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video" and stream.get("width"):
                probe.width = int(stream["width"])
                probe.height = int(stream.get("height") or 0) or None
                break
        return probe
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - best effort
            pass
