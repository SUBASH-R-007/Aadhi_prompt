"""Storage factory."""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from ..config import Settings, get_settings
from .base import Storage, validate_key

if TYPE_CHECKING:  # pragma: no cover
    from .assets import AssetStore

__all__ = ["Storage", "get_storage", "get_asset_store", "validate_key", "reset_storage_cache"]


def build_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        from .s3 import S3Storage  # optional dependency (boto3)

        return S3Storage(settings)
    from .local import LocalStorage

    return LocalStorage(settings.resolved_storage_dir, settings.media_url_prefix)


@lru_cache
def get_storage() -> Storage:
    return build_storage(get_settings())


_store_lock = threading.Lock()
_store: "AssetStore | None" = None


def get_asset_store() -> "AssetStore":
    """One AssetStore per process: its in-flight de-duplication only works when it is shared."""
    global _store
    from ..db import get_sessionmaker
    from .assets import AssetStore

    storage = get_storage()
    factory = get_sessionmaker()
    with _store_lock:
        if _store is None or _store.storage is not storage or _store.session_factory is not factory:
            _store = AssetStore(storage, factory)
        return _store


def reset_storage_cache() -> None:
    global _store
    get_storage.cache_clear()
    with _store_lock:
        _store = None
