"""Deterministic offline image generator: gradient + soft shapes derived from the prompt (no text)."""

from __future__ import annotations

import asyncio
import hashlib
import io

from PIL import Image, ImageDraw

from ...config import Settings
from .._common import ASPECT_DIMS, aspect_dims, emit_usage
from ..base import ImageResult, Usage, UsageSink


def _palette(digest: bytes) -> list[tuple[int, int, int]]:
    return [(digest[i] // 2 + 40, digest[i + 1] // 2 + 30, digest[i + 2] // 2 + 60) for i in range(0, 15, 3)]


def render_png(prompt: str, width: int, height: int) -> bytes:
    """Gradient background with a few translucent circles/rectangles; same prompt -> same bytes."""
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    colors = _palette(digest)
    top, bottom = colors[0], colors[1]
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(5):
        b = digest[16 + i * 3 : 19 + i * 3]
        cx, cy = b[0] / 255 * width, b[1] / 255 * height
        r = (0.08 + b[2] / 255 * 0.22) * min(width, height)
        color = (*colors[(i + 2) % len(colors)], 110)
        if i % 2 == 0:
            od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        else:
            od.rounded_rectangle([cx - r, cy - r * 0.6, cx + r, cy + r * 0.6], radius=int(r * 0.2), fill=color)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


class FakeImage:
    """Offline ``ImageProvider``."""

    name = "fake"
    paid = False
    aspects = frozenset(ASPECT_DIMS)
    max_prompt_chars = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage: UsageSink | None = None) -> ImageResult:
        """PNG at the aspect's size (16:9 -> 1280x720)."""
        width, height = aspect_dims(aspect)
        data = await asyncio.to_thread(render_png, prompt or "", width, height)
        await emit_usage(on_usage, Usage(provider="fake", model="fake-image", operation="image", units=1))
        return ImageResult(data=data, mime="image/png", width=width, height=height)
