"""httpx helpers for REST providers (ElevenLabs, Pollinations, GIPHY).

``fetch_limited`` downloads media on a provider's behalf safely: https-only redirects re-checked
against a host allow-list, and a streamed size cap.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from ._common import LoopLocal
from ._retry import NOT_RETRYABLE, ErrorInfo


class HttpStatusError(Exception):
    """Non-2xx response (body kept for error details; never shown unredacted)."""

    def __init__(self, status: int, body: str = "", retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after = retry_after


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP-date); ``None`` if absent/invalid.

    The value is not capped: ``call_with_retries`` decides whether waiting that long is acceptable.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds != seconds or seconds in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0.0, seconds)


def retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` of ``response`` in seconds (see ``parse_retry_after``)."""
    return parse_retry_after(response.headers.get("retry-after"))


def raise_for_status(response: httpx.Response) -> None:
    """Raise ``HttpStatusError`` for non-2xx responses."""
    if response.status_code >= 400:
        try:
            body = response.text[:2000]
        except (UnicodeDecodeError, httpx.ResponseNotRead):
            body = ""
        raise HttpStatusError(response.status_code, body, retry_after_seconds(response))


def classify_http_error(exc: BaseException) -> ErrorInfo:
    """Retry 429/5xx/408, timeouts and transport failures."""
    if isinstance(exc, HttpStatusError):
        if exc.status == 429:
            return ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=exc.retry_after)
        if exc.status in (408, 409, 425) or exc.status >= 500:
            return ErrorInfo(retry=True, status=exc.status, retry_after=exc.retry_after)
        return ErrorInfo(retry=False, status=exc.status)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError, ConnectionError)):
        return ErrorInfo(retry=True)
    return NOT_RETRYABLE


class UnsafeRedirect(Exception):
    """A redirect left the allowed https hosts (never followed, never retried)."""


class ResponseTooLarge(Exception):
    """The response body exceeds the configured size cap (never retried)."""


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """``host`` equals an allowed domain or is a subdomain of one (case-insensitive)."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for domain in allowed:
        domain = domain.lower().strip().lstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


async def fetch_limited(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int] | None = None,
    max_bytes: int,
    allowed_hosts: Iterable[str],
    content_type_prefix: str = "",
    max_redirects: int = 3,
) -> tuple[bytes, str]:
    """GET ``url`` safely and return ``(body, content_type)``.

    Redirects are followed by hand (the client must not follow them itself): every hop must stay on
    https and on an allowed host (``host_allowed``), at most ``max_redirects`` hops. The body is
    streamed with a running cap (a ``Content-Length`` above ``max_bytes`` is refused before reading).
    Non-2xx answers raise ``HttpStatusError`` (so ``classify_http_error`` keeps deciding retries); a
    wrong content type is reported as HTTP 502 like before. ``UnsafeRedirect`` / ``ResponseTooLarge``
    are never retried.
    """
    allowed = tuple(allowed_hosts)
    current, query = url, dict(params or {})
    for _hop in range(max(0, max_redirects) + 1):
        async with client.stream("GET", current, params=query or None, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                target = urljoin(str(response.url), location)
                parts = urlsplit(target)
                if not location or parts.scheme != "https" or not host_allowed(parts.hostname or "", allowed):
                    raise UnsafeRedirect(f"redirect to {parts.scheme or '?'}://{parts.hostname or '?'} refused")
                current, query = target, {}  # the Location carries its own query
                continue
            if response.status_code >= 400:
                body = await _read_capped(response, 2000, truncate=True)
                raise HttpStatusError(response.status_code, body.decode("utf-8", "replace"),
                                      retry_after_seconds(response))
            ctype = response.headers.get("content-type", "")
            if content_type_prefix and not ctype.lower().startswith(content_type_prefix):
                raise HttpStatusError(502, f"unexpected content type ({ctype[:60]})")
            declared = response.headers.get("content-length")
            if declared and declared.strip().isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge(f"response of {int(declared)} bytes exceeds the {max_bytes}-byte limit")
            return await _read_capped(response, max_bytes), ctype
    raise UnsafeRedirect(f"more than {max_redirects} redirects")


async def _read_capped(response: httpx.Response, limit: int, *, truncate: bool = False) -> bytes:
    """Stream the body; past ``limit`` bytes either truncate (error bodies) or raise ``ResponseTooLarge``."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            if truncate:
                chunks.append(chunk[: max(0, limit - (size - len(chunk)))])
                break
            raise ResponseTooLarge(f"response exceeds the {limit}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def loop_local_client(timeout_s: float, **kwargs: object) -> LoopLocal[httpx.AsyncClient]:
    """Per-event-loop ``httpx.AsyncClient`` cache (clients are loop-bound)."""
    return LoopLocal(lambda: httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), **kwargs))  # type: ignore[arg-type]
