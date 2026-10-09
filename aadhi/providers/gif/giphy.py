"""GIPHY search (hotlinked GIFs with attribution; live player only)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ...config import Settings
from .._common import describe_exception, emit_usage, error_for_status, make_error
from .._http import HttpStatusError, classify_http_error, loop_local_client, raise_for_status
from .._retry import SleepFn, call_with_retries
from ..base import GifResult, ProviderError, ProviderNotConfigured, Usage, UsageSink

GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
GIPHY_HOST = "api.giphy.com"
RATINGS = frozenset({"g", "pg", "pg-13", "r"})
MAX_QUERY_CHARS = 50
MAX_RETRY_AFTER_S = 15.0  # GIFs are optional: give up rather than wait out a long rate limit


def _is_giphy_url(url: str) -> bool:
    """Only https URLs on giphy.com hosts are allowed (CSP ``img-src https://*.giphy.com``)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "giphy.com" or host.endswith(".giphy.com"))


def pick_result(items: list[Any]) -> GifResult | None:
    """First result with a usable ``downsized`` rendition on a GIPHY host."""
    for item in items:
        if not isinstance(item, dict):
            continue
        images = item.get("images") or {}
        for rendition in ("downsized", "downsized_medium", "fixed_height"):
            r = images.get(rendition) or {}
            url = str(r.get("url") or "")
            if not url or not _is_giphy_url(url):
                continue
            try:
                width, height = int(r.get("width") or 0), int(r.get("height") or 0)
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            link = str(item.get("url") or "")
            return GifResult(
                url=url,
                width=width,
                height=height,
                title=str(item.get("title") or "")[:200],
                attribution="Powered by GIPHY",
                link_url=link if _is_giphy_url(link) else "",
            )
    return None


class GiphyGif:
    """``GifProvider`` backed by the GIPHY search endpoint."""

    name = "giphy"

    def __init__(
        self,
        settings: Settings,
        *,
        sleep: SleepFn | None = None,
        max_attempts: int = 3,
        timeout_s: float = 20.0,
        search_url: str = GIPHY_SEARCH_URL,
    ) -> None:
        if not settings.giphy_api_key.get_secret_value():
            raise ProviderNotConfigured("giphy: GIPHY_API_KEY is not configured", provider=self.name)
        self.settings = settings
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._search_url = search_url
        self._clients = loop_local_client(timeout_s)

    async def _search(self, params: dict[str, str | int]) -> dict[str, Any]:
        client = await self._clients.aget()
        response = await client.get(self._search_url, params=params)
        raise_for_status(response)
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def search(self, query: str, *, rating: str = "g", on_usage: UsageSink | None = None) -> GifResult:
        """Best match for ``query``; raises ``ProviderError`` (status 404) when nothing suitable is found."""
        query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
        if not query:
            raise make_error(ProviderError, self.name, "empty query", settings=self.settings)
        rating = rating if rating in RATINGS else "g"
        params: dict[str, str | int] = {
            "api_key": self.settings.giphy_api_key.get_secret_value(),
            "q": query,
            "limit": 10,
            "offset": 0,
            "rating": rating,
            "lang": "en",
        }
        try:
            data = await call_with_retries(
                lambda: self._search(params),
                classify=classify_http_error,
                max_attempts=self._max_attempts,
                base_delay=1.0,
                max_delay=10.0,
                sleep=self._sleep,
                label="giphy",
                service="GIF service (GIPHY)",
                max_retry_after=MAX_RETRY_AFTER_S,
            )
        except ProviderError:
            raise
        except HttpStatusError as exc:
            raise make_error(error_for_status(exc.status), self.name, "search failed", settings=self.settings,
                             status=exc.status, host=GIPHY_HOST, detail=exc.body) from None
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise make_error(ProviderError, self.name, "search failed", settings=self.settings, host=GIPHY_HOST,
                             detail=describe_exception(exc)) from None
        await emit_usage(on_usage, Usage(provider="giphy", model="search", operation="gif", units=1))
        result = pick_result(list(data.get("data") or []))
        if result is None:
            raise make_error(ProviderError, self.name, "no suitable GIF found", settings=self.settings, status=404)
        return result
