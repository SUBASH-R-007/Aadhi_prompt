"""Pollinations image generation (HTTP GET API, no key).

The legacy free endpoint has been seen answering HTTP 402 Payment Required: that (and 401/403) is a
``ProviderUnavailable`` (not retried, the media chain moves on to a configured backup and puts this
provider in a long cooldown). The image is downloaded with ``fetch_limited``: redirects only to https
hosts of ``pollinations.ai``, body streamed with the ``AI_MAX_IMAGE_BYTES`` cap.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from ...config import Settings
from .._common import (
    ASPECT_DIMS,
    aspect_dims,
    describe_exception,
    emit_usage,
    error_for_status,
    make_error,
    stable_seed,
    style_prompt,
)
from .._http import HttpStatusError, classify_http_error, fetch_limited, loop_local_client
from .._retry import SleepFn, call_with_retries
from ..base import ImageResult, ProviderError, Usage, UsageSink
from .verify import inspect_generated

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt/"
POLLINATIONS_HOST = "image.pollinations.ai"
POLLINATIONS_DOMAINS = ("pollinations.ai",)  # redirects may only go to https hosts under these domains
MAX_PROMPT_CHARS = 1500


class PollinationsImage:
    """``ImageProvider`` using ``GET https://image.pollinations.ai/prompt/{prompt}``."""

    name = "pollinations"
    paid = False
    aspects = frozenset(ASPECT_DIMS)
    max_prompt_chars = MAX_PROMPT_CHARS

    def __init__(
        self,
        settings: Settings,
        *,
        sleep: SleepFn | None = None,
        max_attempts: int = 3,
        timeout_s: float = 120.0,
        base_url: str = POLLINATIONS_BASE_URL,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        base_host = urlsplit(self._base_url).hostname or POLLINATIONS_HOST
        self._allowed_hosts = (*POLLINATIONS_DOMAINS, base_host)
        self._clients = loop_local_client(timeout_s, follow_redirects=False)

    async def _fetch(self, url: str, params: dict[str, str | int]) -> bytes:
        client = await self._clients.aget()
        data, _ctype = await fetch_limited(client, url, params=params, max_bytes=self.settings.ai_max_image_bytes,
                                           allowed_hosts=self._allowed_hosts, content_type_prefix="image/")
        return data

    async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage: UsageSink | None = None,
                       variant: int = 0) -> ImageResult:
        """Generate an image; the seed is derived from the prompt so results are reproducible. A new version
        (``variant`` above 0, Visual Review) derives another seed, so the same prompt gives another picture;
        at 0 the seed is exactly as before."""
        styled = style_prompt(prompt, self.settings)[:MAX_PROMPT_CHARS]
        if not styled:
            raise make_error(ProviderError, self.name, "empty prompt", settings=self.settings)
        width, height = aspect_dims(aspect)
        url = self._base_url + quote(styled, safe="")
        seed = stable_seed(styled) if variant <= 0 else stable_seed(f"{styled}#{int(variant)}")
        params: dict[str, str | int] = {"width": width, "height": height, "nologo": "true", "seed": seed}
        try:
            data = await call_with_retries(
                lambda: self._fetch(url, params),
                classify=classify_http_error,
                max_attempts=self._max_attempts,
                base_delay=2.0,
                max_delay=30.0,
                sleep=self._sleep,
                label="pollinations",
                service="image service (Pollinations)",
            )
        except ProviderError:
            raise
        except HttpStatusError as exc:
            message = ("the image endpoint asks for payment or an account" if exc.status == 402
                       else "image generation failed")
            raise make_error(error_for_status(exc.status), self.name, message,
                             settings=self.settings, status=exc.status, host=POLLINATIONS_HOST,
                             detail=exc.body) from None
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise make_error(ProviderError, self.name, "image generation failed", settings=self.settings,
                             host=POLLINATIONS_HOST, detail=describe_exception(exc)) from None
        await emit_usage(on_usage, Usage(provider="pollinations", model="default", operation="image", units=1))
        info, warnings = await inspect_generated(data, provider=self.name, aspect=aspect,
                                                 max_bytes=self.settings.ai_max_image_bytes)
        return ImageResult(data=data, mime=info.mime, width=info.width, height=info.height, warnings=warnings)
