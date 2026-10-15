"""Serving timelines: URL resolution at serve time + ETags.

Stored timelines hold asset keys only; URLs are filled per request by
``aadhi.compose.timeline.resolve_urls``. With S3 and no CDN the URLs are presigned and expire,
so the ETag then carries a time bucket (half the presign TTL) to force a refresh in time.

The ETag is derived from cheap columns only (no serialising/hashing of the multi-megabyte
document per poll): the version id, ``revision``, ``built_revision``, ``has_timeline`` and
``updated_at`` (bumped by every write to the version row, ``onupdate``), plus the URL-shaping
settings. Any write that can change the timeline therefore changes the tag; unrelated writes only
cost a full response.

``DYNAMIC_ENABLED`` applies when a timeline is served (:func:`current_timeline`), like ``MASCOT_CUES``: the render
job rebuilds the timeline with the current flag, so a stored timeline built under the other value is served rebuilt
the same way (the player never shows the other engine's look than the MP4, and turning the flag off stops the Dynamic
stage at once, without rebuilding every lecture). The look-engine generation (``TimelinePresentation.engine_version``,
``compose.presentation.ENGINE_VERSION``; plan B2 §1.3) applies the same way: the render job always builds with the
current resolver, so a stored Dynamic timeline of an older generation is served rebuilt, and the generation is part of
the ETag so a player's cached copy is refreshed after a deploy that raises it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections.abc import Mapping
from typing import Any

from ..compose.base import RETIRED_BRANDING_FILES, RETIRED_MASCOT_CLIPS
from ..compose.presentation import ENGINE_VERSION
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
    (applied when served, see :func:`resolve_timeline`), ``DYNAMIC_ENABLED`` and the look-engine generation
    ``ENGINE_VERSION`` (both applied by :func:`current_timeline`); nothing here loads or serialises the (deferred)
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
            f"dyn={bool(getattr(settings, 'dynamic_enabled', False))}",
            f"engine={ENGINE_VERSION}",
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


def built_dynamic(stored: Mapping[str, Any] | Timeline | None) -> bool:
    """True when ``stored`` was built as a Dynamic timeline (``presentation.style == "dynamic"``)."""
    pres = stored.get("presentation") if isinstance(stored, Mapping) else getattr(stored, "presentation", None)
    style = pres.get("style") if isinstance(pres, Mapping) else getattr(pres, "style", None)
    return style == "dynamic"


def built_engine(stored: Mapping[str, Any] | Timeline | None) -> int:
    """The look-engine generation a Dynamic ``stored`` timeline was built with (``presentation.engine_version``); 1,
    the schema default and B1's value, when it is missing or not a positive integer."""
    pres = stored.get("presentation") if isinstance(stored, Mapping) else getattr(stored, "presentation", None)
    value = pres.get("engine_version") if isinstance(pres, Mapping) else getattr(pres, "engine_version", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def current_timeline(version: ProjectVersion, settings: Settings) -> dict[str, Any] | Timeline | None:
    """The version's stored timeline, or the one the render job would build now when ``DYNAMIC_ENABLED`` changed
    since the build, or when a Dynamic build is of an older look-engine generation than this server's
    ``ENGINE_VERSION`` (the MP4 always rebuilds with the current flag and resolver, so the player must not show
    another engine or an older look).

    A build of the current revision is rebuilt (``build_timeline`` is pure) only when its engine differs from what
    the flag and the screenplay ask for now, or when it is Dynamic, still wanted Dynamic, and its ``engine_version``
    is below ``ENGINE_VERSION`` (one above it is served as built: the stage refuses a generation it does not know);
    a stale build is served as built, except a Dynamic one while the flag is off (the flag is the kill switch of the
    Dynamic stage). The common classic case loads nothing more.
    """
    stored = version.timeline
    if stored is None:
        return None
    flag = bool(getattr(settings, "dynamic_enabled", False))
    was_dynamic = built_dynamic(stored)
    if not was_dynamic and not flag:
        return stored
    if version.built_revision != version.revision and not (was_dynamic and not flag):
        return stored
    raw = version.screenplay
    wanted = flag and isinstance(raw, Mapping) and raw.get("presentation") is not None
    if wanted == was_dynamic and not (was_dynamic and built_engine(stored) < ENGINE_VERSION):
        return stored
    from ..compose.timeline import build_timeline
    from .routers.versions import safe_manifest, safe_screenplay

    screenplay = safe_screenplay(version)
    if screenplay is None:
        return stored
    return build_timeline(screenplay, safe_manifest(version), settings=settings, version_id=version.id,
                          revision=version.revision)


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
