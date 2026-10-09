"""Image verification with Pillow (format allow-list, dimensions, decompression-bomb guard) and the
soft quality checks shared by the media adapters.

Hard rejects (``InvalidMediaOutput``, a ``ProviderError``: the media chain may try the next provider):
empty or undecodable data, a format outside PNG/JPEG/WEBP/GIF, more than ``MAX_PIXELS`` pixels, fewer
than ``MIN_SIDE`` pixels on a side (generated images), more than ``max_bytes`` bytes. Soft findings (``media_warnings``)
keep the image but are shown to the teacher: one flat colour (a typical failed or placeholder
generation) or a shape far from the requested aspect ratio. They are heuristics, never rejections, so
a legitimate minimalist illustration is only flagged for a look.
"""

from __future__ import annotations

import asyncio
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from ..base import InvalidMediaOutput, ProviderError

_FORMAT_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
MAX_PIXELS = 40_000_000
MIN_SIDE = 32  # pixels; anything smaller is an error image or a tracking pixel, not an illustration
BLANK_TOLERANCE = 8  # grey levels: max - min at or below this on a downscaled copy = one flat colour
ASPECT_TOLERANCE = 0.08  # relative difference between the delivered and the requested aspect ratio


@dataclass
class ImageInfo:
    mime: str
    width: int
    height: int
    blank: bool = False  # one flat colour (see ``looks_blank``)


def _looks_blank(img: Image.Image) -> bool:
    """True when a small greyscale copy of ``img`` is (nearly) one flat colour."""
    small = img.convert("L")
    small.thumbnail((32, 32))
    low, high = small.getextrema()  # type: ignore[misc]
    return int(high) - int(low) <= BLANK_TOLERANCE


def looks_blank(data: bytes) -> bool | None:
    """Whether ``data`` decodes to a single flat colour; ``None`` when it cannot be decoded."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.size[0] * img.size[1] > MAX_PIXELS:
                    return None
                img.load()
                return _looks_blank(img)
    except Exception:  # noqa: BLE001 - a heuristic: unknown is not a finding
        return None


def inspect_image_sync(data: bytes, *, provider: str = "image", max_bytes: int | None = None,
                       min_side: int | None = None, check_blank: bool = False) -> ImageInfo:
    """Verify ``data`` is a PNG/JPEG/WEBP/GIF image and return its mime and size.

    Generation adapters pass ``max_bytes`` (``AI_MAX_IMAGE_BYTES``), ``min_side=MIN_SIDE`` and
    ``check_blank`` (one extra decode of a small thumbnail for ``ImageInfo.blank``).
    """
    if not data:
        raise InvalidMediaOutput(f"{provider}: empty image", provider=provider)
    if max_bytes is not None and len(data) > max_bytes:
        raise InvalidMediaOutput(f"{provider}: image of {len(data)} bytes exceeds the {max_bytes}-byte limit",
                                 provider=provider)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                fmt = (img.format or "").upper()
                width, height = img.size
                if width * height > MAX_PIXELS:
                    raise InvalidMediaOutput(f"{provider}: image too large ({width}x{height})", provider=provider)
                img.verify()
    except ProviderError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise InvalidMediaOutput(f"{provider}: response is not a valid image ({type(exc).__name__})",
                                 provider=provider) from None
    mime = _FORMAT_MIME.get(fmt)
    if mime is None:
        raise InvalidMediaOutput(f"{provider}: unsupported image format {fmt or 'unknown'}", provider=provider)
    if min_side is not None and min(width, height) < min_side:
        raise InvalidMediaOutput(f"{provider}: image too small ({width}x{height})", provider=provider)
    blank = bool(looks_blank(data)) if check_blank else False  # verify() leaves the image unusable: reopen
    return ImageInfo(mime=mime, width=int(width), height=int(height), blank=blank)


async def inspect_image(data: bytes, *, provider: str = "image", max_bytes: int | None = None,
                        min_side: int | None = None, check_blank: bool = False) -> ImageInfo:
    """Async wrapper (decoding runs in a worker thread)."""
    return await asyncio.to_thread(inspect_image_sync, data, provider=provider, max_bytes=max_bytes,
                                   min_side=min_side, check_blank=check_blank)


async def inspect_generated(data: bytes, *, provider: str, aspect: str, max_bytes: int) -> tuple[ImageInfo, list[str]]:
    """Hard checks for a generated image plus its soft quality findings (``media_warnings``)."""
    info = await inspect_image(data, provider=provider, max_bytes=max_bytes, min_side=MIN_SIDE, check_blank=True)
    return info, media_warnings(info.width, info.height, aspect, blank=info.blank)


def _ratio(aspect: str) -> float | None:
    try:
        w, h = (float(x) for x in (aspect or "").split(":", 1))
    except ValueError:
        return None
    return w / h if w > 0 and h > 0 else None


def media_warnings(width: int | None, height: int | None, aspect: str, *, blank: bool = False) -> list[str]:
    """Soft quality findings for delivered media (kept, but shown to the teacher)."""
    out: list[str] = []
    if blank:
        out.append("the generated picture is a single flat colour (it may be a failed or placeholder image)")
    wanted = _ratio(aspect)
    if wanted and width and height:
        got = width / height
        if abs(got - wanted) / wanted > ASPECT_TOLERANCE:
            out.append(f"the generated media is {width}x{height}, far from the requested {aspect} shape")
    return out
