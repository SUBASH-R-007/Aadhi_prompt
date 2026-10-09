"""Serving timelines: URL resolution at serve time + ETags.

Stored timelines hold asset keys only; URLs are filled per request by
``aadhi.compose.timeline.resolve_urls``. With S3 and no CDN the URLs are presigned and expire,
so the ETag then carries a time bucket (half the presign TTL) to force a refresh in time.

The ETag is derived from cheap columns only (no serialising/hashing of the multi-megabyte
document per poll): the version id, ``revision``, ``built_revision``, ``has_timeline`` and
``updated_at`` (bumped by every write to the version row, ``onupdate``), plus the URL-shaping
settings. Any write that can change the timeline therefore changes the tag; unrelated writes only
cost a full response.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from typing import Any

from ..compose.base import RETIRED_BRANDING_FILES, RETIRED_MASCOT_CLIPS
from ..config import Settings
from ..models import ProjectVersion
from ..schemas.jsonsafe import json_safe, validate_stored
from ..schemas.timeline import Timeline
from ..storage.assets import AssetStore
from ..storage.base import is_public_key
from .util import as_utc


def signed_urls(settings: Settings) -> bool:
    """True when served URLs are short-lived presigned URLs."""
    return settings.storage_backend == "s3" and not settings.cdn_base_url


def media_url(store: AssetStore, settings: Settings, storage_key: str | None) -> str | None:
    """A streamable URL of a public stored file, served like timeline media: its capability URL, or a short-lived
    presigned one with S3 and no CDN. None for a private key, or when no URL can be made (a preview link only)."""
    if not storage_key or not is_public_key(storage_key):
        return None
    try:
        if signed_urls(settings):
            return store.storage.signed_url(storage_key, int(settings.s3_presign_ttl_seconds))
        return store.url_for(storage_key)
    except Exception:  # noqa: BLE001 - a preview link only
        return None


def _stamp(value: dt.datetime | None) -> str:
    utc = as_utc(value)
    return utc.isoformat(timespec="microseconds") if utc is not None else "-"


def timeline_etag(version: ProjectVersion, settings: Settings, *, now: float | None = None) -> str:
    """``"r{revision}-b{built_revision}-{hash12}"`` (+ ``-t{bucket}`` for presigned URLs).

    ``hash12`` covers the version id, ``has_timeline``, ``updated_at``, the settings that shape
    resolved URLs, the serve-time branding substitutions (``RETIRED_MASCOT_CLIPS`` and
    ``RETIRED_BRANDING_FILES``: cached copies naming a retired file are refreshed) and ``MASCOT_CUES``
    (applied when served, see :func:`resolve_timeline`); nothing here loads or serialises the (deferred)
    timeline document.
    """
    identity = "|".join(
        (
            str(version.id),
            str(bool(version.has_timeline)),
            _stamp(version.updated_at),
            settings.storage_backend,
            settings.cdn_base_url or "",
            settings.media_url_prefix or "",
            ",".join(f"{a}>{b}" for a, b in sorted(RETIRED_MASCOT_CLIPS.items())),
            ",".join(f"{a}>{b}" for a, b in sorted(RETIRED_BRANDING_FILES.items())),
            f"cues={settings.mascot_cues}",
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    tag = f"r{version.revision}-b{version.built_revision}-{digest}"
    if signed_urls(settings):
        window = max(60, int(settings.s3_presign_ttl_seconds) // 2)
        tag += f"-t{int((now if now is not None else time.time()) // window)}"
    return f'"{tag}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison for If-None-Match."""
    if not if_none_match:
        return False
    target = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        c = candidate.strip()
        if c == "*" or c.removeprefix("W/") == target:
            return True
    return False


def resolve_timeline(stored: dict[str, Any] | Timeline, store: AssetStore, settings: Settings) -> dict[str, Any]:
    """Validated timeline with every URL filled in, as JSON-ready dict.

    A timeline stored before NaN/Infinity and lone surrogates were refused is still served (non-finite
    numbers read as 0, lone surrogates as U+FFFD; ``aadhi.schemas.jsonsafe``) instead of a 500.
    ``MASCOT_CUES`` is applied here, to every lecture at once (no rebuild needed).
    """
    from ..compose.timeline import resolve_urls

    timeline = stored if isinstance(stored, Timeline) else validate_stored(Timeline, stored)
    resolved = resolve_urls(timeline, store, signed=signed_urls(settings))
    # MASCOT_CUES applies when the timeline is served, like the retired-clip substitution. The MP4 rebuilds
    # with the current setting, so a stored timeline built under the other value must not differ from it.
    for scene in resolved.scenes:
        scene.layout.mascot_cues = settings.mascot_cues
    return json_safe(resolved.model_dump(mode="json"))
