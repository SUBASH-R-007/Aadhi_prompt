"""Content-addressed asset store on top of ``Storage`` + the ``assets`` table.

Every generated artifact (TTS clip, Manim video, image, render...) is keyed by a hash of
*everything that influenced it* (inputs + provider + model + template version). Re-running a
pipeline therefore reuses work, edits only regenerate what changed, and URLs are immutable.

* Blob paths are unique per production (``<ns>/<kind>/<key>/<random>.<ext>``): concurrent or
  retried producers never overwrite each other; the DB insert is the only arbiter and the loser
  deletes its own blob. The random segment also makes public URLs unguessable.
* ``get_or_create`` de-duplicates concurrent producers *within a process* across threads and
  event loops (inline workers each run their own loop). Producers that depend on a per-user API
  key pass an ``inflight_scope``, so they only share an in-flight run with callers paying the same
  way. A caller only inherits the in-flight owner's *outcome*: when the owner stopped for a reason
  of its own (its job was cancelled, lost its lease, ran out of budget; see
  ``register_owner_scoped``) the waiting callers run the producer themselves.
* Paid kinds (``GENERATION_CLAIM_KINDS``, default ``video,image``) are also generated once *across
  processes*: the in-process owner takes a claim row in ``asset_claims`` (``INSERT ... ON CONFLICT
  DO NOTHING``) before paying. Identical requests in other worker processes poll for the asset
  (every ``GENERATION_CLAIM_POLL_SECONDS``, honouring their job's cancellation, at most
  ``GENERATION_CLAIM_MAX_WAIT_SECONDS``) and reuse it. The holder renews its claim while it produces
  (``GENERATION_CLAIM_TTL_SECONDS``) and deletes it when done; a crashed holder's claim is dead once
  it expires or its job lease (``Job.locked_by`` + ``attempts``) is gone, and a waiter takes it
  over. Other kinds keep the old rule: duplicates across processes are possible but harmless (the
  DB insert decides). Without the table (or on a database error) generation proceeds unclaimed.
* A holder that started a paid long-running provider job (a Veo operation) records it on its claim row
  (``record_claim_operation``, called by ``providers.operations``). A job that takes over a dead claim with
  such a record takes the row over in place (one atomic update, so no third process can slip in) and finds
  the record with ``inherited_operation``: it polls the same operation instead of paying for a new one.
  A holder that stops without an outcome (cancelled, worker shutdown, lost lease, budget) while such an
  operation is outstanding (``submitting`` / ``submitted``) does not delete its row: it leaves it expired, with
  the record, for the next job to take over; a crashed holder leaves it behind anyway. A row nobody takes over
  is removed by the cleanup sweep (``jobs.cleanup``). Any other ending deletes the row as before.
* Before a paid producer runs, the caller's ``generation_guard`` (if any; set by the orchestrator)
  may refuse it, e.g. because the version being built changed meanwhile.
* ``normalize_prompt`` / ``media_key`` give the canonical identity of an AI image/video request, so
  whitespace or Unicode variants of one prompt share one cached asset; ``first_existing`` finds an
  earlier result among several candidate keys (e.g. other providers).
* Only allow-listed MIME types can be stored (no HTML/SVG/JS on the media origin).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import datetime as dt
import hashlib
import json
import logging
import os
import secrets
import socket
import threading
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..models import PUBLIC_ASSET_KINDS, Asset, AssetClaim, Job, utcnow
from .base import Storage

log = logging.getLogger(__name__)

# The ONLY mime types that may be stored. No text/html, image/svg+xml, javascript.
EXT_BY_MIME = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
    "application/json": "json",
    "text/vtt": "vtt",
    "application/x-subrip": "srt",
    "text/plain": "txt",
    "text/markdown": "md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
MIME_BY_EXT = {v: k for k, v in EXT_BY_MIME.items()}


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_key(kind: str, inputs: dict[str, Any], version: str = "1") -> str:
    """Deterministic content address for generated assets."""
    h = hashlib.sha256()
    h.update(f"{kind}|v{version}|".encode())
    h.update(canonical_json(inputs).encode("utf-8"))
    return f"{kind}-{h.hexdigest()[:40]}"


def bytes_key(kind: str, data: bytes) -> str:
    """Content address for uploaded bytes."""
    return f"{kind}-{hashlib.sha256(data).hexdigest()[:40]}"


def normalize_prompt(text: str | None) -> str:
    """Canonical form of a generation prompt: Unicode NFC, every run of whitespace (line breaks
    included) one space, trimmed. Case and punctuation are kept: they can change what a model makes.

    Idempotent, and the identity for prompts that are already normalised (almost all LLM output).
    """
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def media_key(
    kind: str, prompt: str, *, provider: str, model: str, aspect: str, params: dict[str, Any] | None = None
) -> str:
    """Content address of a generated image / video request.

    The prompt is ``normalize_prompt``-ed; ``params`` are the output-changing parameters an adapter
    sends beyond its defaults (e.g. a negative prompt or a duration) and are only part of the key when
    not empty. For a normalised prompt and no ``params`` the key is byte-identical to the
    ``compute_key(kind, {"prompt", "provider", "model", "aspect"})`` keys used so far, so media cached
    before stays valid.
    """
    inputs: dict[str, Any] = {"prompt": normalize_prompt(prompt), "provider": provider, "model": model,
                              "aspect": aspect}
    if params:
        inputs["params"] = params
    return compute_key(kind, inputs)


def ext_for(mime: str) -> str:
    try:
        return EXT_BY_MIME[mime]
    except KeyError as exc:
        raise ValueError(f"mime type {mime!r} is not allowed in storage") from exc


def storage_key_for(kind: str, key: str, mime: str) -> str:
    ns = "assets" if kind in PUBLIC_ASSET_KINDS else "private"
    return f"{ns}/{kind}/{key}/{secrets.token_urlsafe(12).replace('_', 'x').replace('-', 'y')}.{ext_for(mime)}"


@dataclass
class Produced:
    """What a producer returns to ``AssetStore.get_or_create``."""

    data: bytes | None = None
    path: Path | None = None  # alternative to data for large files
    mime: str = "application/octet-stream"
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# --- in-flight ownership: whose failure may be shared --------------------------------------------

_OWNER_SCOPED: tuple[type[BaseException], ...] = (asyncio.CancelledError,)
# Provider operation statuses that may still be running (and be billed) at the provider: mirror
# ``providers.operations`` SUBMITTING / SUBMITTED (storage must not import providers).
_OUTSTANDING_OPERATIONS = ("submitting", "submitted")


def register_owner_scoped(*types: type[BaseException]) -> None:
    """Exceptions that describe the in-flight *owner* rather than the work (``aadhi.jobs.base``
    registers ``JobCancelled`` and ``BudgetExceeded``): callers waiting on that owner's run are not
    handed them and run the producer themselves instead."""
    global _OWNER_SCOPED
    _OWNER_SCOPED = tuple(dict.fromkeys((*_OWNER_SCOPED, *types)))


# --- cross-process generation claims -------------------------------------------------------------

HOSTNAME = (socket.gethostname() or "localhost").replace(":", "_")[:64]
_RAPID_RETRIES = 5  # claim row vanished / taken over this many times in a row: wait one poll interval


class AssetClaimTimeout(RuntimeError):
    """Another worker process is still generating this asset after ``max_wait_seconds``. The asset
    builders treat it like any transient media failure (a still image now, retried on the next build)."""


@dataclass(frozen=True)
class ClaimOwner:
    """Who would hold a generation claim: the running job's lease (set by the worker for the job's
    task, ``claim_owner``) — or nobody, then claims are anonymous and only expire."""

    worker_id: str
    job_id: int | None = None
    attempt: int | None = None
    check_cancelled: Callable[[], None] | None = None  # raises to stop waiting (JobContext.check_cancelled)


@dataclass(frozen=True)
class ClaimConfig:
    """``GENERATION_CLAIM_*`` settings (see ``aadhi.config``)."""

    kinds: frozenset[str] = frozenset({"video", "image"})
    ttl_seconds: float = 120.0
    poll_seconds: float = 2.0
    max_wait_seconds: float = 1200.0

    @classmethod
    def from_settings(cls, settings: Any) -> ClaimConfig:
        raw = getattr(settings, "generation_claim_kinds", "video,image")
        kinds = raw.split(",") if isinstance(raw, str) else list(raw or [])
        return cls(
            kinds=frozenset(k.strip() for k in kinds if k and k.strip()),
            ttl_seconds=float(getattr(settings, "generation_claim_ttl_seconds", 120)),
            poll_seconds=float(getattr(settings, "generation_claim_poll_seconds", 2.0)),
            max_wait_seconds=float(getattr(settings, "generation_claim_max_wait_seconds", 1200)),
        )


# (kind, key) -> None, called in a worker thread right before a paid producer runs; raises to refuse.
GenerationGuard = Callable[[str, str], None]

_CLAIM_OWNER: contextvars.ContextVar[ClaimOwner | None] = contextvars.ContextVar("aadhi_claim_owner", default=None)
_GENERATION_GUARD: contextvars.ContextVar[GenerationGuard | None] = contextvars.ContextVar(
    "aadhi_generation_guard", default=None
)


@contextmanager
def claim_owner(owner: ClaimOwner | None) -> Iterator[None]:
    """Run the block (and the tasks it creates: they copy the context) as ``owner``."""
    token = _CLAIM_OWNER.set(owner)
    try:
        yield
    finally:
        _CLAIM_OWNER.reset(token)


@contextmanager
def generation_guard(guard: GenerationGuard | None) -> Iterator[None]:
    """Paid producers started inside the block (and its tasks) first call ``guard(kind, key)``."""
    token = _GENERATION_GUARD.set(guard)
    try:
        yield
    finally:
        _GENERATION_GUARD.reset(token)


@dataclass
class _Claim:
    key: str
    holder: str
    inherited: dict[str, Any] | None = None  # provider operation recorded by the stopped holder we took over from


# Claims held by the running task (and the tasks it creates) while their producer runs: (key, store, claim).
_ACTIVE_CLAIMS: contextvars.ContextVar[tuple[tuple[str, AssetStore, _Claim], ...]] = contextvars.ContextVar(
    "aadhi_active_claims", default=()
)


def _active_claim(key: str) -> tuple[AssetStore, _Claim] | None:
    for k, store, claim in reversed(_ACTIVE_CLAIMS.get()):
        if k == key:
            return store, claim
    return None


def holds_claim(key: str) -> bool:
    """Whether the running producer holds the generation claim on ``key`` (cheap: no database access)."""
    return _active_claim(key) is not None


def inherited_operation(key: str) -> dict[str, Any] | None:
    """The provider operation recorded by the stopped holder whose claim on ``key`` the running producer took
    over (None: no claim on ``key`` is held here, or the previous holder recorded none)."""
    found = _active_claim(key)
    return dict(found[1].inherited) if found is not None and found[1].inherited else None


def record_claim_operation(key: str, operation: dict[str, Any]) -> bool:
    """Store ``operation`` (JSON, no secrets) on the claim on ``key`` that the running producer holds, so a job
    taking the claim over later can resume it. Blocking; best effort: False when no claim on ``key`` is held
    here (no claims for this kind, claims unavailable, or the claim was taken over) or on a database error."""
    found = _active_claim(key)
    if found is None:
        return False
    store, claim = found
    try:
        return store._set_claim_operation(claim, operation)
    except SQLAlchemyError as exc:
        log.warning("asset %s: could not record the provider operation on the claim: %s", key, type(exc).__name__)
        return False


def _dialect_insert(dialect: str) -> Callable[..., Any] | None:
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    return None


class AssetStore:
    def __init__(
        self, storage: Storage, session_factory: sessionmaker[Session], *, claims: ClaimConfig | None = None
    ) -> None:
        self.storage = storage
        self.session_factory = session_factory
        self._inflight: dict[str, concurrent.futures.Future] = {}
        self._inflight_lock = threading.Lock()
        self._claims = claims  # None: read GENERATION_CLAIM_* from the settings on every use
        self._claims_broken = False  # logged once when the claim table is unusable

    # --- lookups ---------------------------------------------------------------
    def get(self, key: str, *, verify_blob: bool = False) -> Asset | None:
        with self.session_factory() as db:
            asset = db.execute(select(Asset).where(Asset.key == key)).scalar_one_or_none()
        if asset is not None and verify_blob and not self.storage.exists(asset.storage_key):
            return None
        return asset

    def get_many(self, keys: Iterable[str]) -> dict[str, Asset]:
        keys = list({k for k in keys if k})
        if not keys:
            return {}
        out: dict[str, Asset] = {}
        with self.session_factory() as db:
            for i in range(0, len(keys), 500):
                for a in db.execute(select(Asset).where(Asset.key.in_(keys[i : i + 500]))).scalars():
                    out[a.key] = a
        return out

    def first_existing(self, keys: Iterable[str], *, verify_blob: bool | None = None) -> Asset | None:
        """The first of ``keys`` (in order) that has a stored asset, or None. One query; blobs are
        verified on the local backend by default (like ``get_or_create``), a missing blob is skipped."""
        ordered = list(dict.fromkeys(k for k in keys if k))
        found = self.get_many(ordered)
        verify = self.storage.name == "local" if verify_blob is None else verify_blob
        for key in ordered:
            asset = found.get(key)
            if asset is not None and (not verify or self.storage.exists(asset.storage_key)):
                return asset
        return None

    def url_for(self, storage_key: str) -> str:
        """Pure: stable public URL for a storage key (no DB, no network)."""
        return self.storage.public_url(storage_key)

    def url(self, asset_or_key: Asset | str) -> str:
        asset = self.get(asset_or_key) if isinstance(asset_or_key, str) else asset_or_key
        if asset is None:
            raise KeyError(f"unknown asset {asset_or_key!r}")
        return self.storage.public_url(asset.storage_key)

    def local_copy(self, key: str, dest_dir: Path) -> Path:
        """Return a local file for the asset (no copy for the local backend)."""
        asset = self.get(key)
        if asset is None:
            raise KeyError(f"unknown asset {key!r}")
        lp = self.storage.local_path(asset.storage_key)
        if lp is not None and lp.exists():
            return lp
        dest = Path(dest_dir) / f"{key}.{asset.storage_key.rsplit('.', 1)[-1]}"
        if dest.exists():
            return dest
        return self.storage.download_to(asset.storage_key, dest)

    # --- writes ----------------------------------------------------------------
    def put(self, key: str, kind: str, produced: Produced, *, created_by: int | None = None) -> Asset:
        """Idempotent insert. The DB row is the arbiter; a losing writer deletes its own blob."""
        existing = self.get(key, verify_blob=True)
        if existing is not None:
            return existing
        storage_key = storage_key_for(kind, key, produced.mime)
        if produced.data is not None:
            self.storage.put_bytes(storage_key, produced.data, produced.mime)
            size = len(produced.data)
        elif produced.path is not None:
            self.storage.put_file(storage_key, Path(produced.path), produced.mime)
            size = Path(produced.path).stat().st_size
        else:
            raise ValueError("Produced needs data or path")
        values = dict(
            kind=kind,
            storage_key=storage_key,
            mime=produced.mime,
            size_bytes=size,
            duration_s=produced.duration_s,
            width=produced.width,
            height=produced.height,
            meta=produced.meta or {},
            created_by=created_by,
        )
        with self.session_factory() as db:
            row = db.execute(select(Asset).where(Asset.key == key)).scalar_one_or_none()
            if row is None:
                row = Asset(key=key, **values)
                db.add(row)
                try:
                    db.commit()
                    db.refresh(row)
                    return row
                except IntegrityError:  # a concurrent writer won
                    db.rollback()
                    self._delete_quietly(storage_key)
                    return db.execute(select(Asset).where(Asset.key == key)).scalar_one()
            # Row exists but its blob is missing: point it at the new blob, refresh all metadata.
            old_key = row.storage_key
            for k, v in values.items():
                setattr(row, k, v)
            db.commit()
            db.refresh(row)
            if old_key != storage_key:
                self._delete_quietly(old_key)
            return row

    def _delete_quietly(self, storage_key: str) -> None:
        try:
            self.storage.delete(storage_key)
        except Exception:  # pragma: no cover - best effort
            pass

    async def get_or_create(
        self,
        key: str,
        kind: str,
        producer: Callable[[], Awaitable[Produced]],
        *,
        created_by: int | None = None,
        inflight_scope: str = "",
        guarded: bool = True,
    ) -> tuple[Asset, bool]:
        """Return ``(asset, created)``. Concurrent callers in this process run the producer once;
        for paid kinds (``GENERATION_CLAIM_KINDS``) identical requests in other processes too.

        ``guarded=False`` skips the caller's ``generation_guard`` (the claim still applies): for a
        producer that only collects work already paid for (e.g. resuming a submitted AI video operation).

        Callers whose producer depends on a per-user API key pass ``inflight_scope`` (e.g.
        ``"user:3"``) so one user's provider outcome (e.g. a rejected personal key) is never handed to
        another user's job. Only the in-flight sharing is scoped: the stored asset is still keyed by
        ``key`` alone (when two scopes produce at once, the DB insert decides; see ``put``). Across
        processes only the finished asset is shared: a waiter whose claim holder failed generates itself.
        """
        # Blob verification is cheap on local disk; on S3 it would be a HEAD per lookup, so it is
        # skipped there (use `python -m aadhi.cli verify-assets` offline instead).
        verify = self.storage.name == "local"
        slot = f"{key}#{inflight_scope}" if inflight_scope else key
        while True:
            existing = await asyncio.to_thread(self.get, key, verify_blob=verify)
            if existing is not None:
                return existing, False
            with self._inflight_lock:
                fut = self._inflight.get(slot)
                owner = fut is None
                if owner:
                    fut = concurrent.futures.Future()
                    fut.aadhi_job_id = _current_job_id()  # type: ignore[attr-defined]
                    self._inflight[slot] = fut
            assert fut is not None
            if not owner:
                try:
                    asset = await _wait_shared(fut)
                except _OWNER_SCOPED as exc:
                    if _retry_after_owner_stop(fut, exc):  # the owner stopped for its own reasons: try ourselves
                        continue
                    raise  # our own stop (we are cancelled, or our job is), or the owner's that is ours too
                return asset, False
            try:
                asset, created = await self._produce(key, kind, producer, created_by, verify, guarded)
                _settle(fut, result=asset)
                return asset, created
            except BaseException as exc:
                _settle(fut, error=exc)
                raise
            finally:
                with self._inflight_lock:
                    self._inflight.pop(slot, None)

    async def _produce(
        self,
        key: str,
        kind: str,
        producer: Callable[[], Awaitable[Produced]],
        created_by: int | None,
        verify: bool,
        guarded: bool = True,
    ) -> tuple[Asset, bool]:
        """In-process owner: claim paid kinds across processes, then run the producer and store the result."""
        config = self._claims if self._claims is not None else self._settings_claims()
        if kind not in config.kinds:
            produced = await producer()
            return await asyncio.to_thread(self.put, key, kind, produced, created_by=created_by), True
        claim = await self._acquire_claim(key, config, verify)
        if isinstance(claim, Asset):
            return claim, False  # another process produced it while we waited
        renewer: asyncio.Future[None] | None = None
        token = _ACTIVE_CLAIMS.set((*_ACTIVE_CLAIMS.get(), (key, self, claim))) if claim is not None else None
        stopped = False  # the producer stopped without an outcome (cancel, shutdown, lost lease, budget, a kill)
        try:
            guard = _GENERATION_GUARD.get() if guarded else None
            if guard is not None:
                await asyncio.to_thread(guard, kind, key)
            if claim is not None:
                renewer = asyncio.ensure_future(self._renew_loop(claim, config.ttl_seconds))
            produced = await producer()
            return await asyncio.to_thread(self.put, key, kind, produced, created_by=created_by), True
        except BaseException as exc:
            stopped = isinstance(exc, _OWNER_SCOPED) or not isinstance(exc, Exception)
            raise
        finally:
            if token is not None:
                _ACTIVE_CLAIMS.reset(token)
            if renewer is not None:
                renewer.cancel()
            if claim is not None:
                if stopped:
                    await self._stop_claim_quietly(claim)
                else:
                    await self._release_claim_quietly(claim)

    @staticmethod
    def _settings_claims() -> ClaimConfig:
        from ..config import get_settings

        return ClaimConfig.from_settings(get_settings())

    # --- cross-process claims ----------------------------------------------------------------------
    async def _acquire_claim(self, key: str, config: ClaimConfig, verify: bool) -> _Claim | Asset | None:
        """Claim ``key`` for this caller, or wait for the holder's asset. Returns the claim, the asset
        another process produced meanwhile, or None when claims are unavailable (generate unclaimed)."""
        owner = _CLAIM_OWNER.get()
        holder = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, config.max_wait_seconds)
        rapid = 0
        while True:
            try:
                state, value = await asyncio.to_thread(self._claim_step, key, holder, owner, config.ttl_seconds, verify)
            except SQLAlchemyError as exc:
                self._claims_unavailable(exc)
                return None
            if state == "asset":
                return value
            if state == "claimed":
                claim = _Claim(key, holder, inherited=value if isinstance(value, dict) else None)
                # The previous holder may have stored the asset just before releasing its claim.
                existing = await asyncio.to_thread(self.get, key, verify_blob=verify)
                if existing is not None:
                    await self._release_claim_quietly(claim)
                    return existing
                return claim
            if state == "retry" and rapid < _RAPID_RETRIES:  # released or taken over: claim again at once
                rapid += 1
                continue
            rapid = 0
            if owner is not None and owner.check_cancelled is not None:
                owner.check_cancelled()
            if loop.time() >= deadline:
                raise AssetClaimTimeout(
                    f"another job is still generating this {key.split('-', 1)[0]}; "
                    f"gave up waiting after {config.max_wait_seconds:.0f}s"
                )
            await asyncio.sleep(max(0.01, config.poll_seconds))

    def _claim_step(
        self, key: str, holder: str, owner: ClaimOwner | None, ttl: float, verify: bool
    ) -> tuple[str, Any]:
        """One claim attempt (blocking): ``("asset", a)`` | ``("claimed", None)`` | ``("claimed", operation)``
        (a dead holder's claim that recorded a provider operation, taken over in place) | ``("retry", None)``
        (the holder's claim was dead and removed, or vanished) | ``("wait", None)`` (live holder)."""
        existing = self.get(key, verify_blob=verify)
        if existing is not None:
            return "asset", existing
        now = utcnow()
        values: dict[str, Any] = {
            "key": key,
            "holder": holder,
            "job_id": owner.job_id if owner is not None else None,
            "worker_id": (owner.worker_id if owner is not None else f"{HOSTNAME}:{os.getpid()}:-:anonymous")[:160],
            "attempt": owner.attempt if owner is not None else None,
            "created_at": now,
            "expires_at": now + dt.timedelta(seconds=ttl),
        }
        with self.session_factory() as db:
            if self._insert_claim(db, values):
                db.commit()
                return "claimed", None
            row = db.execute(
                select(
                    AssetClaim.holder,
                    AssetClaim.expires_at,
                    AssetClaim.job_id,
                    AssetClaim.worker_id,
                    AssetClaim.attempt,
                    AssetClaim.operation,
                    (AssetClaim.expires_at <= now).label("expired"),
                ).where(AssetClaim.key == key)
            ).first()
            if row is None:
                db.rollback()
                return "retry", None
            dead = bool(row.expired)
            if not dead and row.job_id is not None:
                job = db.execute(
                    select(Job.status, Job.locked_by, Job.attempts).where(Job.id == row.job_id)
                ).first()
                dead = job is None or not (
                    job.status == "running" and job.locked_by == row.worker_id and job.attempts == row.attempt
                )
            if not dead:
                db.rollback()
                return "wait", None
            if isinstance(row.operation, dict) and row.operation:
                # The stopped holder started a paid provider job: take its row over in place (exactly the row we
                # judged, in one statement, so nobody else can claim in between) and keep the record for resuming.
                taken = db.execute(
                    update(AssetClaim)
                    .where(AssetClaim.key == key, AssetClaim.holder == row.holder,
                           AssetClaim.expires_at == row.expires_at)
                    .values(holder=values["holder"], job_id=values["job_id"], worker_id=values["worker_id"],
                            attempt=values["attempt"], created_at=values["created_at"],
                            expires_at=values["expires_at"])
                    .execution_options(synchronize_session=False)
                )
                db.commit()
                if not taken.rowcount:
                    return "retry", None
                log.warning("asset %s: took over the generation claim of a stopped holder (job %s) with its "
                            "provider operation", key, row.job_id)
                return "claimed", dict(row.operation)
            # Take over a dead claim: delete exactly the row we judged (a renewal in between keeps it).
            db.execute(
                delete(AssetClaim)
                .where(AssetClaim.key == key, AssetClaim.holder == row.holder, AssetClaim.expires_at == row.expires_at)
                .execution_options(synchronize_session=False)
            )
            db.commit()
        log.warning("asset %s: took over the generation claim of a stopped holder (job %s)", key, row.job_id)
        return "retry", None

    @staticmethod
    def _insert_claim(db: Session, values: dict[str, Any]) -> bool:
        """``INSERT ... ON CONFLICT DO NOTHING``; True when this row was inserted."""
        dialect_insert = _dialect_insert(db.get_bind().dialect.name)
        if dialect_insert is not None:
            stmt = dialect_insert(AssetClaim).values(**values).on_conflict_do_nothing(index_elements=["key"])
            return db.execute(stmt.returning(AssetClaim.key)).scalar_one_or_none() is not None
        try:  # other dialects: savepoint + IntegrityError
            with db.begin_nested():
                db.execute(insert(AssetClaim).values(**values))
            return True
        except IntegrityError:
            return False

    def _renew_claim(self, claim: _Claim, ttl: float) -> bool:
        with self.session_factory() as db:
            result = db.execute(
                update(AssetClaim)
                .where(AssetClaim.key == claim.key, AssetClaim.holder == claim.holder)
                .values(expires_at=utcnow() + dt.timedelta(seconds=ttl))
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return bool(result.rowcount)

    async def _renew_loop(self, claim: _Claim, ttl: float) -> None:
        """Keep the claim alive while the producer runs (every ttl/4)."""
        interval = max(0.05, ttl / 4)
        while True:
            await asyncio.sleep(interval)
            try:
                kept = await asyncio.to_thread(self._renew_claim, claim, ttl)
            except Exception as exc:  # noqa: BLE001 - retried on the next tick; the claim may expire meanwhile
                log.warning("asset %s: could not renew the generation claim: %s", claim.key, type(exc).__name__)
                continue
            if not kept:
                log.warning("asset %s: the generation claim was taken over by another worker", claim.key)
                return

    def _set_claim_operation(self, claim: _Claim, operation: dict[str, Any]) -> bool:
        """Record ``operation`` on ``claim``'s row (only while this holder still owns it). Blocking."""
        with self.session_factory() as db:
            result = db.execute(
                update(AssetClaim)
                .where(AssetClaim.key == claim.key, AssetClaim.holder == claim.holder)
                .values(operation=dict(operation))
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return bool(result.rowcount)

    def _release_claim(self, claim: _Claim) -> None:
        with self.session_factory() as db:
            db.execute(
                delete(AssetClaim)
                .where(AssetClaim.key == claim.key, AssetClaim.holder == claim.holder)
                .execution_options(synchronize_session=False)
            )
            db.commit()

    async def _release_claim_quietly(self, claim: _Claim) -> None:
        """Release in a thread that a second cancellation cannot interrupt; a claim left behind
        expires on its own (or dies with its job's lease)."""
        try:
            await asyncio.shield(asyncio.to_thread(self._release_claim, claim))
        except Exception as exc:  # noqa: BLE001 - best effort, never mask the producer's outcome
            log.info("asset %s: generation claim not released (%s); it will expire", claim.key, type(exc).__name__)

    def _expire_claim_keeping_operation(self, claim: _Claim) -> bool:
        """Expire ``claim``'s row now, keeping it, when it records an outstanding provider operation (True);
        otherwise change nothing (False). Blocking."""
        with self.session_factory() as db:
            operation = db.execute(
                select(AssetClaim.operation).where(AssetClaim.key == claim.key, AssetClaim.holder == claim.holder)
            ).scalar_one_or_none()
            if not (isinstance(operation, dict) and operation.get("status") in _OUTSTANDING_OPERATIONS):
                db.rollback()
                return False
            result = db.execute(
                update(AssetClaim)
                .where(AssetClaim.key == claim.key, AssetClaim.holder == claim.holder)
                .values(expires_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return bool(result.rowcount)

    async def _stop_claim_quietly(self, claim: _Claim) -> None:
        """The producer stopped without an outcome: a provider operation it left outstanding (it may be running and
        billed at the provider) stays on the claim row, expired, so the next job takes it over in place and polls it
        instead of paying again. Without one the claim is released as usual. Never raises."""
        try:
            if await asyncio.shield(asyncio.to_thread(self._expire_claim_keeping_operation, claim)):
                log.info("asset %s: claim left expired with its provider operation for the next job", claim.key)
                return
        except Exception as exc:  # noqa: BLE001 - best effort: fall back to releasing the claim
            log.info("asset %s: could not keep the provider operation on the claim (%s)", claim.key,
                     type(exc).__name__)
        await self._release_claim_quietly(claim)

    def _claims_unavailable(self, exc: Exception) -> None:
        if not self._claims_broken:
            self._claims_broken = True
            log.warning("generation claims are unavailable (%s); paid media is generated without them",
                        type(exc).__name__)


async def _wait_shared(fut: concurrent.futures.Future) -> Asset:
    """Wait for the in-flight owner's result. Shielded: a waiter that is cancelled never cancels the
    shared future (which would make the owner's ``set_result`` fail and lose its finished asset)."""
    inner = asyncio.wrap_future(fut)
    inner.add_done_callback(lambda f: f.cancelled() or f.exception())  # retrieved: no "never retrieved" noise
    return await asyncio.shield(inner)


def _settle(fut: concurrent.futures.Future, *, result: Any = None, error: BaseException | None = None) -> None:
    """Publish the owner's outcome to the waiters (a no-op if the future is already settled)."""
    try:
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(result)
    except concurrent.futures.InvalidStateError:
        pass


def _owner_failed(fut: concurrent.futures.Future) -> bool:
    """True when the in-flight owner's future failed with an owner-scoped exception (whether the waiter
    itself is being stopped is the caller's question: ``_retry_after_owner_stop``)."""
    if not fut.done() or fut.cancelled():
        return False
    return isinstance(fut.exception(), _OWNER_SCOPED)


def _current_job_id() -> int | None:
    owner = _CLAIM_OWNER.get()
    return owner.job_id if owner is not None else None


def _retry_after_owner_stop(fut: concurrent.futures.Future, exc: BaseException) -> bool:
    """A waiter that got an owner-scoped exception runs the producer itself only when that exception is the
    owner's (not the waiter's own cancellation, e.g. ``gather_all`` cancelling sibling scenes or a worker
    shutdown: then it stops too, never starting paid work for a job that is ending) and the owner's stop
    is not the waiter's job's own (the same job cancelled or over budget)."""
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        return False
    if not _owner_failed(fut) or exc is not fut.exception():
        return False
    if isinstance(exc, asyncio.CancelledError):
        return True  # that owner task was cancelled on its own (e.g. a per-scene timeout); we were not
    mine, theirs = _current_job_id(), getattr(fut, "aadhi_job_id", None)
    return mine is None or theirs is None or mine != theirs
