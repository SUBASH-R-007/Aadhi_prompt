"""S3-compatible blob storage (AWS S3, Cloudflare R2, MinIO) implementing ``aadhi.storage.base.Storage``.

* The boto3 client is created lazily (boto3 is imported only when S3 is actually used), with
  standard-mode retries, timeouts and SigV4. ``S3_ENDPOINT_URL`` selects R2/MinIO (path-style
  addressing); objects live under ``S3_PREFIX``.
* ``put_*`` never overwrite: conditional ``PutObject`` with ``IfNoneMatch="*"`` (412 ->
  ``FileExistsError``). botocore retries timeouts and 5xx, so a 412 can also answer the retry of our
  OWN successful write whose response was lost: on 412 the object is HEAD-checked and accepted when
  its size and MD5 ETag equal what we sent. Stores that do not implement conditional writes fall
  back to a HEAD check. Files larger than ``SINGLE_PUT_MAX`` use a managed multipart upload after a
  HEAD check (keys are unique per production anyway, see ``aadhi.storage.assets``).
* ``public_url``: ``CDN_BASE_URL/<key>`` when a CDN is configured (its origin must map to
  ``<bucket>/<S3_PREFIX>``, i.e. the CDN path equals the storage key), otherwise the stable app path
  ``MEDIA_URL_PREFIX/<key>`` which the API's media route answers with a 302 to a fresh
  ``signed_url`` (so stored/cached URLs never expire).
* ``signed_url``: presigned GET (optionally ``Content-Disposition: attachment``); ``local_path``: None.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from ..config import Settings
from .base import validate_key

__all__ = ["S3Storage", "s3_media_origins"]

log = logging.getLogger(__name__)

SINGLE_PUT_MAX = 512 * 1024 * 1024
MAX_PRESIGN_SECONDS = 7 * 24 * 3600
_NOT_FOUND = {"404", "NoSuchKey", "NotFound", "NoSuchObject"}
_EXISTS = {"PreconditionFailed", "ConditionalRequestConflict"}
_UNSUPPORTED = {"NotImplemented", "NotSupported"}


def _origin(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


def s3_media_origins(settings: Settings) -> list[str]:
    """Origins presigned media URLs are served from (for the app CSP). Pure; no boto3 import."""
    if settings.s3_endpoint_url:
        origin = _origin(settings.s3_endpoint_url)
        return [origin] if origin else []
    if not settings.s3_bucket:
        return []
    bucket = settings.s3_bucket.lower()
    origins = [f"https://{bucket}.s3.amazonaws.com"]
    if settings.s3_region:
        origins.append(f"https://{bucket}.s3.{settings.s3_region.lower()}.amazonaws.com")
    return origins


def _error_info(exc: Exception) -> tuple[str, int]:
    response = getattr(exc, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
    return code, status


def _file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_disposition(download_name: str) -> str:
    ascii_name = (
        "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in download_name).strip() or "download"
    )
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download_name, safe='')}"


class S3Storage:
    """``Storage`` on S3. ``client`` may be injected (tests use a botocore ``Stubber``)."""

    name = "s3"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET must be set when STORAGE_BACKEND=s3")
        self.settings = settings
        self.bucket = settings.s3_bucket
        prefix = (settings.s3_prefix or "").strip().strip("/")
        if prefix:
            validate_key(prefix)
        self.prefix = f"{prefix}/" if prefix else ""
        self._client = client
        self._lock = threading.Lock()
        self._conditional_writes: bool | None = None

    # --- client ----------------------------------------------------------------
    @property
    def client(self) -> Any:
        """The (lazily created, thread-safe) boto3 S3 client."""
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._make_client()
        return self._client

    def _make_client(self) -> Any:
        import boto3  # optional dependency, imported only for the S3 backend
        from botocore.config import Config

        s = self.settings
        config = Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=120,
            max_pool_connections=32,
            s3={"addressing_style": "path" if s.s3_endpoint_url else "virtual"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        kwargs: dict[str, Any] = {"config": config}
        if s.s3_region:
            kwargs["region_name"] = s.s3_region
        if s.s3_endpoint_url:
            kwargs["endpoint_url"] = s.s3_endpoint_url
        key_id = s.s3_access_key_id.get_secret_value()
        secret = s.s3_secret_access_key.get_secret_value()
        if key_id and secret:  # otherwise the default credential chain (instance role, env, ...)
            kwargs["aws_access_key_id"] = key_id
            kwargs["aws_secret_access_key"] = secret
        return boto3.session.Session().client("s3", **kwargs)

    def _supports_conditional_put(self) -> bool:
        if self._conditional_writes is None:
            try:
                members = self.client.meta.service_model.operation_model("PutObject").input_shape.members
                self._conditional_writes = "IfNoneMatch" in members
            except Exception:  # noqa: BLE001  # pragma: no cover - unknown/old botocore: no conditional writes
                self._conditional_writes = False
        return self._conditional_writes

    def _object_key(self, key: str) -> str:
        validate_key(key)
        return self.prefix + key

    # --- writes ----------------------------------------------------------------
    def _head(self, okey: str) -> dict[str, Any] | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=okey)
        except Exception as exc:
            code, status = _error_info(exc)
            if status == 404 or code in _NOT_FOUND:
                return None
            raise

    def _is_our_object(self, okey: str, size: int, md5: Callable[[], str]) -> bool:
        """After a 412: True when the existing object is byte-identical to what we sent (same size
        and single-part MD5 ETag), i.e. a retried request hit our own already-stored write."""
        head = self._head(okey)
        if head is None or int(head.get("ContentLength", -1)) != size:
            return False
        etag = str(head.get("ETag") or "").strip().strip('"').lower()
        return bool(etag) and "-" not in etag and etag == md5()

    def _put_object(
        self, okey: str, body_factory: Any, content_type: str, *, size: int, md5: Callable[[], str]
    ) -> None:
        params = {"Bucket": self.bucket, "Key": okey, "ContentType": content_type}
        if self._supports_conditional_put():
            try:
                self.client.put_object(Body=body_factory(), IfNoneMatch="*", **params)
                return
            except Exception as exc:
                code, status = _error_info(exc)
                if status == 412 or code in _EXISTS:
                    if self._is_our_object(okey, size, md5):
                        log.info("conditional put of %s answered 412 after a retry; the object is ours", okey)
                        return
                    raise FileExistsError(f"refusing to overwrite storage object {okey!r}") from exc
                if status != 501 and code not in _UNSUPPORTED:
                    raise
                self._conditional_writes = False  # store without conditional writes: HEAD check below
        if self._head(okey) is not None:
            raise FileExistsError(f"refusing to overwrite storage object {okey!r}")
        self.client.put_object(Body=body_factory(), **params)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` at ``key`` (``FileExistsError`` if a different object already exists)."""
        blob = bytes(data)
        self._put_object(
            self._object_key(key),
            lambda: blob,
            content_type,
            size=len(blob),
            md5=lambda: hashlib.md5(blob, usedforsecurity=False).hexdigest(),
        )

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        """Upload a local file to ``key`` (``FileExistsError`` if the object already exists)."""
        okey = self._object_key(key)
        path = Path(path)
        if path.stat().st_size > SINGLE_PUT_MAX:
            if self._head(okey) is not None:
                raise FileExistsError(f"refusing to overwrite storage object {okey!r}")
            self.client.upload_file(str(path), self.bucket, okey, ExtraArgs={"ContentType": content_type})
            return
        opened: list[Any] = []

        def body() -> Any:
            for f in opened:
                f.close()
            f = open(path, "rb")  # noqa: SIM115 - closed below
            opened.append(f)
            return f

        try:
            self._put_object(okey, body, content_type, size=path.stat().st_size, md5=lambda: _file_md5(path))
        finally:
            for f in opened:
                f.close()

    # --- reads -----------------------------------------------------------------
    def get_bytes(self, key: str) -> bytes:
        """Object content (``FileNotFoundError`` if missing)."""
        okey = self._object_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=okey)
        except Exception as exc:
            code, status = _error_info(exc)
            if status == 404 or code in _NOT_FOUND:
                raise FileNotFoundError(key) from exc
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def download_to(self, key: str, path: Path) -> Path:
        """Stream the object into ``path`` atomically (temp file + replace). Returns ``path``."""
        okey = self._object_key(key)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=okey)
        except Exception as exc:
            code, status = _error_info(exc)
            if status == 404 or code in _NOT_FOUND:
                raise FileNotFoundError(key) from exc
            raise
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".dl-")
        body = response["Body"]
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    f.write(chunk)
            os.replace(tmp, path)
        finally:
            body.close()
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    def exists(self, key: str) -> bool:
        """True if the object exists (invalid keys -> False)."""
        try:
            okey = self._object_key(key)
        except ValueError:
            return False
        return self._head(okey) is not None

    def delete(self, key: str) -> None:
        """Delete the object (no error if it does not exist)."""
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def size(self, key: str) -> int:
        """Object size in bytes (``FileNotFoundError`` if missing)."""
        head = self._head(self._object_key(key))
        if head is None:
            raise FileNotFoundError(key)
        return int(head.get("ContentLength", 0))

    # --- URLs ------------------------------------------------------------------
    def public_url(self, key: str) -> str:
        """Stable URL: ``CDN_BASE_URL/<key>`` (the CDN origin maps to ``<bucket>/<S3_PREFIX>``), or the
        app's media path (302 -> presigned URL)."""
        validate_key(key)
        cdn = (self.settings.cdn_base_url or "").rstrip("/")
        if cdn:
            return f"{cdn}/{key}"
        return f"{(self.settings.media_url_prefix or '/media').rstrip('/')}/{key}"

    def signed_url(self, key: str, ttl_seconds: int, *, download_name: str | None = None) -> str:
        """Presigned GET valid for ``ttl_seconds`` (1 s .. 7 days), optionally as an attachment."""
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._object_key(key)}
        if download_name:
            params["ResponseContentDisposition"] = _content_disposition(download_name)
        ttl = min(max(int(ttl_seconds), 1), MAX_PRESIGN_SECONDS)
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def local_path(self, key: str) -> Path | None:
        """Remote backend: always None."""
        return None
