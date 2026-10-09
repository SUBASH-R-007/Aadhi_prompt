"""Google image generation: Gemini image models (``generate_content`` with IMAGE output) or Imagen
(``generate_images``), chosen by ``IMAGE_MODEL``.

Usage is reported as soon as the billable response is in hand (before the safety check on the
response and before the bytes are decoded/verified), so a failure afterwards never hides spend.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from .._common import emit_usage, make_error, safe_attr, style_prompt
from .._retry import SleepFn
from ..base import ContentBlocked, ImageResult, ProviderError, Usage, UsageSink
from ..gemini_common import (
    ClientFactory,
    GeminiCaller,
    check_blocked,
    genai_types,
    response_parts,
    usage_from_response,
)
from .verify import inspect_generated

GEMINI_ASPECTS = frozenset({"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"})
IMAGEN_ASPECTS = frozenset({"1:1", "3:4", "4:3", "9:16", "16:9"})


class GeminiImage:
    """``ImageProvider`` for ``gemini-*-image`` and ``imagen-*`` models."""

    name = "gemini"
    paid = True
    max_prompt_chars = None

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.settings = settings
        self._caller = GeminiCaller(settings, timeout_s=180.0, client_factory=client_factory, sleep=sleep,
                                    max_attempts=max_attempts, scope="image")

    @property
    def aspects(self) -> frozenset[str]:
        """Aspect ratios the configured model serves natively (others are generated at 16:9)."""
        return IMAGEN_ASPECTS if self.settings.image_model.lower().startswith("imagen") else GEMINI_ASPECTS

    async def _imagen(self, model: str, prompt: str, aspect: str, on_usage: UsageSink | None) -> bytes:
        types = await genai_types()
        config = types.GenerateImagesConfig(number_of_images=1,
                                            aspect_ratio=aspect if aspect in IMAGEN_ASPECTS else "16:9")

        async def request(client: Any, _key: str) -> Any:
            return await client.aio.models.generate_images(model=model, prompt=prompt, config=config)

        response = await self._caller.call(request, what="image generation")
        images = safe_attr(response, "generated_images") or []
        data = safe_attr(images[0], "image", "image_bytes") if images else None
        if not data:  # filtered images are not billed
            reason = safe_attr(images[0], "rai_filtered_reason") if images else None
            raise make_error(ContentBlocked if reason or not images else ProviderError, self.name,
                             "image generation returned no image", settings=self.settings, detail=str(reason or ""))
        await emit_usage(on_usage, Usage(provider="gemini", model=model, operation="image", units=1))
        return bytes(data)

    async def _gemini(self, model: str, prompt: str, aspect: str, on_usage: UsageSink | None) -> bytes:
        types = await genai_types()
        config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect if aspect in GEMINI_ASPECTS else "16:9"),
        )

        async def request(client: Any, _key: str) -> Any:
            return await client.aio.models.generate_content(model=model, contents=prompt, config=config)

        response = await self._caller.call(request, what="image generation")
        blob_data, has_image = None, False
        for part in response_parts(response):
            blob = getattr(part, "inline_data", None)
            mime = str(getattr(blob, "mime_type", "") or "")
            if blob is not None and getattr(blob, "data", None) and mime.startswith("image/"):
                blob_data, has_image = blob.data, True
                break
        # Tokens are billed even when the answer is blocked or text-only: report before checking.
        await emit_usage(on_usage, usage_from_response(response, model=model, operation="image",
                                                       units=1 if has_image else 0))
        check_blocked(response, self.settings, "image generation")
        if not has_image:
            raise make_error(ProviderError, self.name, "model returned no image", settings=self.settings)
        return bytes(blob_data or b"")

    async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage: UsageSink | None = None) -> ImageResult:
        """Generate one image in the house style (``IMAGE_STYLE_SUFFIX``)."""
        styled = style_prompt(prompt, self.settings)
        if not styled:
            raise make_error(ProviderError, self.name, "empty prompt", settings=self.settings)
        model = self.settings.image_model
        if model.lower().startswith("imagen"):
            data = await self._imagen(model, styled, aspect, on_usage)
        else:
            data = await self._gemini(model, styled, aspect, on_usage)
        info, warnings = await inspect_generated(data, provider=self.name, aspect=aspect,
                                                 max_bytes=self.settings.ai_max_image_bytes)
        return ImageResult(data=data, mime=info.mime, width=info.width, height=info.height, warnings=warnings)
