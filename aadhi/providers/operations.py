"""Durable checkpoints of paid long-running provider jobs (Veo operations).

Used by the asset producer (``aadhi.pipeline.assets``) for resumable providers (``resumable = True``).
A record per (asset key, in-flight scope) lives in the running job's payload under
``"provider_operations"``: in memory (``ctx.payload``) and in the ``jobs`` row, written in a
lease-fenced transaction (``ctx.assert_lease``). A worker restart (graceful release, crash, reaper)
retries the *same* job row, which reloads its payload, so the next attempt finds the record:

* ``submitted`` with an operation name: the provider accepted the job, so the next attempt polls that
  operation (``ResumableVideoProvider.resume``) instead of paying for a new one.
* ``submitting``: the request was sent, but the process stopped before the provider's answer was saved.
  It may already be billed, so a paid job is not submitted again automatically (``ambiguous``): the
  scene shows its still image with an issue, and an explicit rebuild of the scene (a new job) generates
  it again.
* ``pending``: saved, but no request is outstanding (a provider that reports its sends, ``reports_sending``,
  marks the record ``submitting`` only around each request and back to ``pending`` when the provider
  answered with an error, e.g. a 429 before a retry wait): a restart submits normally.
* ``completed`` / ``failed`` / ``abandoned``: finished in a live process; nothing to resume.
* ``lost`` / ``ambiguous``: needs a person (see above).

Records hold no secret: the key is identified by ``sha256(key)[:16]`` only, and the operation name is
never shown in the UI. Job payloads are not returned by the API. A retry of a failed job copies its
payload, so it may resume too.

Another job (a later rebuild, another lecture asking for the same clip) sees a record only through the
asset's generation claim: every save is also written to the claim row the producer holds
(``storage.assets.record_claim_operation``). When that job takes over the claim of a stopped holder it
inherits the record (``storage.assets.inherited_operation``), and ``get`` returns it when the job pays the same
way (same scope) and has no record of its own (``submitted`` is resumed, so nothing is paid again; ``submitting``
/ ``lost`` still need a person, ``ambiguous``; anything else is generated normally), or has only a finished one
(pending / completed / failed / abandoned, e.g. after a 429) while the inherited one is a submitted operation
it can resume.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
from dataclasses import asdict, dataclass, fields
from typing import Any

from ..storage.assets import holds_claim, inherited_operation, record_claim_operation

log = logging.getLogger(__name__)

PAYLOAD_KEY = "provider_operations"
PENDING = "pending"
SUBMITTING = "submitting"
SUBMITTED = "submitted"
COMPLETED = "completed"
FAILED = "failed"
ABANDONED = "abandoned"
LOST = "lost"
AMBIGUOUS = "ambiguous"
MAX_RECORDS = 64  # per job; finished records are dropped first
_FINISHED = (PENDING, COMPLETED, FAILED, ABANDONED)  # nothing of this job's own is outstanding

_PERSIST_LOCK = threading.Lock()  # one read-modify-write of a job payload at a time in this process


@dataclass
class OperationRecord:
    asset_key: str
    scope: str  # in-flight scope: "" (server key) or "user:<id>" (personal key)
    provider: str
    model: str
    status: str
    operation: str = ""  # provider operation name (not a secret; never shown in the UI)
    key_fingerprint: str = ""  # sha256(api key)[:16]; never the key
    job_id: int = 0
    attempt: int = 0
    usage_recorded: bool = False  # the (estimated) charge of this operation was already reported
    updated_at: str = ""

    @property
    def ident(self) -> str:
        return f"{self.asset_key}#{self.scope}" if self.scope else self.asset_key

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> OperationRecord | None:
        if not isinstance(data, dict):
            return None
        names = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in data.items() if k in names})
        except TypeError:
            return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _trim(records: dict[str, Any]) -> dict[str, Any]:
    if len(records) <= MAX_RECORDS:
        return records
    active = (SUBMITTING, SUBMITTED, AMBIGUOUS, LOST)
    keep = sorted(records.items(), key=lambda kv: (
        (kv[1] or {}).get("status") in active, str((kv[1] or {}).get("updated_at") or "")), reverse=True)
    return dict(keep[:MAX_RECORDS])


class OperationStore:
    """The operation records of one running job (``ctx``: a ``JobContext``)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()

    def _records(self) -> dict[str, Any]:
        payload = getattr(self.ctx, "payload", None)
        if not isinstance(payload, dict):
            return {}
        records = payload.get(PAYLOAD_KEY)
        return records if isinstance(records, dict) else {}

    def get(self, asset_key: str, scope: str) -> OperationRecord | None:
        """This job's record for ``asset_key`` (paid as ``scope``), unless it is finished and the generation claim
        this job took over carries another paid operation it can resume (see the module docstring); else the
        record inherited with a taken-over claim when it was paid the same way; else None."""
        ident = f"{asset_key}#{scope}" if scope else asset_key
        with self._lock:
            own = OperationRecord.from_json(self._records().get(ident))
        if own is not None and own.status not in _FINISHED:
            return own  # this job's own outstanding or unresolved submission comes first
        inherited = OperationRecord.from_json(inherited_operation(asset_key))
        if inherited is not None and inherited.asset_key == asset_key and inherited.scope == scope:
            if own is None:
                return inherited
            if inherited.status == SUBMITTED and inherited.operation and inherited.operation != own.operation:
                return inherited  # another job's billed operation: resume it instead of paying again
        return own

    def new(self, asset_key: str, scope: str, provider: str, model: str) -> OperationRecord:
        return OperationRecord(asset_key=asset_key, scope=scope, provider=provider, model=model, status=SUBMITTING,
                               job_id=int(getattr(self.ctx, "job_id", 0) or 0),
                               attempt=int(getattr(self.ctx, "attempt", 0) or 0))

    async def save(self, record: OperationRecord) -> None:
        """Store ``record`` in the job payload (memory, then the jobs row, lease-fenced).

        A database failure is logged and the record stays in memory only (the job goes on: it already
        paid). A lost lease (``JobCancelled``) propagates."""
        record.updated_at = _now()
        data = record.to_json()
        with self._lock:
            payload = getattr(self.ctx, "payload", None)
            if isinstance(payload, dict):
                records = dict(self._records())
                records[record.ident] = data
                payload[PAYLOAD_KEY] = _trim(records)
        # On the asset's generation claim too (best effort, never raises): a job that takes the claim over
        # after this one stopped resumes the operation instead of paying for another.
        if holds_claim(record.asset_key):
            await asyncio.to_thread(record_claim_operation, record.asset_key, data)
        if int(getattr(self.ctx, "job_id", 0) or 0) <= 0 or not hasattr(self.ctx, "session"):
            return  # in-memory contexts (tests, evals) have no job row
        await asyncio.to_thread(self._persist, record.ident, data)

    def _persist(self, ident: str, data: dict[str, Any]) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        from ..models import Job

        try:
            with _PERSIST_LOCK, self.ctx.session() as db:
                assert_lease = getattr(self.ctx, "assert_lease", None)
                if assert_lease is not None:
                    assert_lease(db)  # fenced: a worker that lost the job never overwrites the new owner's state
                job = db.get(Job, int(self.ctx.job_id))
                if job is None:
                    return
                payload = dict(job.payload or {})
                records = dict(payload.get(PAYLOAD_KEY) or {})
                records[ident] = data
                payload[PAYLOAD_KEY] = _trim(records)
                job.payload = payload
        except SQLAlchemyError:
            log.warning("could not save the provider operation checkpoint of job %s", self.ctx.job_id, exc_info=True)

    def checkpoint(self, record: OperationRecord) -> JobCheckpoint:
        return JobCheckpoint(self, record)


class JobCheckpoint:
    """``aadhi.providers.base.OperationCheckpoint`` backed by an ``OperationStore`` record."""

    def __init__(self, store: OperationStore, record: OperationRecord) -> None:
        self.store = store
        self.record = record

    async def submitted(self, operation: str, key_fingerprint: str) -> None:
        self.record.status = SUBMITTED
        self.record.operation = operation
        self.record.key_fingerprint = key_fingerprint
        await self.store.save(self.record)

    async def usage_recorded(self) -> None:
        self.record.usage_recorded = True
        await self.store.save(self.record)

    async def sending(self) -> None:
        """A request that may create the paid job is about to be sent (an answer never saved is ambiguous)."""
        self.record.status = SUBMITTING
        await self.store.save(self.record)

    async def not_sent(self) -> None:
        """The provider answered the request with an error: no job was created, nothing is outstanding."""
        if self.record.status == SUBMITTING:
            self.record.status = PENDING
            await self.store.save(self.record)
