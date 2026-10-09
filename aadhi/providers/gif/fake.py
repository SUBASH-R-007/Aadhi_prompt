"""Offline GIF provider: a tiny animated GIF generated locally and returned as a ``data:`` URL
(allowed by the app CSP ``img-src data:``), so the live player works without network access."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io

from PIL import Image, ImageDraw

from ...config import Settings
from .._common import emit_usage
from ..base import GifResult, Usage, UsageSink

WIDTH, HEIGHT, FRAMES = 160, 90, 6


def render_gif(query: str) -> bytes:
    """Deterministic looping GIF: a dot moving across a background tinted by the query."""
    digest = hashlib.sha256(query.encode("utf-8")).digest()
    bg = (digest[0] // 2 + 60, digest[1] // 2 + 40, digest[2] // 2 + 90)
    fg = (255 - bg[0] // 2, 220, 120)
    frames = []
    for i in range(FRAMES):
        img = Image.new("RGB", (WIDTH, HEIGHT), bg)
        d = ImageDraw.Draw(img)
        x = 20 + i * (WIDTH - 40) // max(1, FRAMES - 1)
        d.ellipse([x - 12, HEIGHT // 2 - 12, x + 12, HEIGHT // 2 + 12], fill=fg)
        frames.append(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=16))
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)
    return buf.getvalue()


class FakeGif:
    """Offline ``GifProvider``."""

    name = "fake"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    async def search(self, query: str, *, rating: str = "g", on_usage: UsageSink | None = None) -> GifResult:
        """Deterministic data-URL GIF for ``query``."""
        data = await asyncio.to_thread(render_gif, query or "")
        await emit_usage(on_usage, Usage(provider="fake", model="fake-gif", operation="gif", units=1))
        return GifResult(
            url="data:image/gif;base64," + base64.b64encode(data).decode("ascii"),
            width=WIDTH,
            height=HEIGHT,
            title=(query or "")[:200],
            attribution="Offline placeholder GIF",
            link_url="",
        )
