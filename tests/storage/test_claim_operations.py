"""Provider operations on generation claims (``asset_claims.operation``): the holder records its paid provider
job on its claim, and a job that takes over a stopped holder's claim inherits the record (atomically)."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from sqlalchemy import select

from aadhi.db import get_sessionmaker, session_scope
from aadhi.models import AssetClaim, utcnow
from aadhi.storage import get_storage
from aadhi.storage.assets import (
    AssetStore,
    ClaimConfig,
    Produced,
    _Claim,
    holds_claim,
    inherited_operation,
    record_claim_operation,
)

FAST = ClaimConfig(ttl_seconds=30, poll_seconds=0.02, max_wait_seconds=5)
VIDEO = b"\x00\x00\x00\x18ftypmp42 clip"
OPERATION = {"asset_key": "video-op", "scope": "", "provider": "veo", "model": "veo-x", "status": "submitted",
             "operation": "operations/op-1", "key_fingerprint": "fp-server"}


def make_store() -> AssetStore:
    return AssetStore(get_storage(), get_sessionmaker(), claims=FAST)


def claim_rows() -> list[AssetClaim]:
    with session_scope() as db:
        rows = list(db.execute(select(AssetClaim)).scalars())
        for r in rows:
            db.expunge(r)
        return rows


def dead_claim(key: str, operation: dict[str, Any] | None, holder: str = "stopped") -> None:
    now = utcnow()
    with session_scope() as db:
        db.add(AssetClaim(key=key, holder=holder, job_id=None, worker_id="host:1:-:anonymous", attempt=None,
                          created_at=now - dt.timedelta(minutes=5), expires_at=now - dt.timedelta(seconds=5),
                          operation=operation))


def test_the_holder_records_its_operation_on_the_claim(app_env):
    store = make_store()
    seen: dict[str, Any] = {}

    async def produce() -> Produced:
        seen["holds"] = holds_claim("video-rec")
        seen["inherited"] = inherited_operation("video-rec")
        seen["recorded"] = await asyncio.to_thread(record_claim_operation, "video-rec", {**OPERATION, "status": "submitting"})
        seen["row"] = claim_rows()[0].operation
        seen["other"] = await asyncio.to_thread(record_claim_operation, "video-other", OPERATION)
        return Produced(data=VIDEO, mime="video/mp4")

    asyncio.run(store.get_or_create("video-rec", "video", produce))
    assert seen["holds"] and seen["inherited"] is None  # a fresh claim inherits nothing
    assert seen["recorded"] and seen["row"]["status"] == "submitting"
    assert seen["other"] is False  # no claim on that key is held here
    assert claim_rows() == []  # released with its record when the producer ends
    assert record_claim_operation("video-rec", OPERATION) is False and not holds_claim("video-rec")


def test_a_taker_inherits_the_stopped_holders_operation(app_env, caplog):
    dead_claim("video-op", OPERATION)
    calls: list[dict[str, Any] | None] = []

    async def produce() -> Produced:
        calls.append(inherited_operation("video-op"))
        row = claim_rows()[0]
        assert row.holder != "stopped" and row.operation == OPERATION  # taken over in place, record kept
        return Produced(data=VIDEO, mime="video/mp4")

    asset, created = asyncio.run(make_store().get_or_create("video-op", "video", produce))
    assert created and calls == [OPERATION] and claim_rows() == []
    assert any("with its provider operation" in r.message for r in caplog.records)


def test_a_claim_without_an_operation_is_taken_over_as_before(app_env):
    dead_claim("video-plain", None)
    calls: list[Any] = []

    async def produce() -> Produced:
        calls.append(inherited_operation("video-plain"))
        return Produced(data=VIDEO, mime="video/mp4")

    asyncio.run(make_store().get_or_create("video-plain", "video", produce))
    assert calls == [None] and claim_rows() == []


def test_only_one_taker_inherits_a_stopped_holders_operation(app_env):
    """The takeover is one conditional update: a second process that judged the same dead row loses."""
    dead_claim("video-race", OPERATION)
    first, second = make_store(), make_store()
    assert first._claim_step("video-race", "holder-1", None, 30, True) == ("claimed", OPERATION)
    assert second._claim_step("video-race", "holder-2", None, 30, True) == ("wait", None)  # now a live claim
    (row,) = claim_rows()
    assert row.holder == "holder-1" and row.operation == OPERATION and row.expires_at.replace(
        tzinfo=row.expires_at.tzinfo or dt.timezone.utc) > utcnow()


def test_a_holder_whose_claim_was_taken_over_cannot_overwrite_the_record(app_env):
    dead_claim("video-fenced", OPERATION, holder="old-holder")
    store = make_store()
    assert store._claim_step("video-fenced", "new-holder", None, 30, True)[0] == "claimed"
    assert store._set_claim_operation(_Claim("video-fenced", "old-holder"), {**OPERATION, "status": "failed"}) is False
    assert claim_rows()[0].operation == OPERATION
    assert store._set_claim_operation(_Claim("video-fenced", "new-holder"), {**OPERATION, "status": "completed"})
    assert claim_rows()[0].operation["status"] == "completed"


def _expired(row: AssetClaim) -> bool:
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=dt.timezone.utc)
    return expires <= utcnow()


def _stop(store: AssetStore, key: str, record: dict[str, Any] | None, error: BaseException) -> None:
    """A producer that records ``record`` (if any) on its claim, then stops with ``error``."""

    async def produce() -> Produced:
        if record is not None:
            assert await asyncio.to_thread(record_claim_operation, key, record)
        raise error

    try:
        asyncio.run(store.get_or_create(key, "video", produce))
    except BaseException as exc:  # noqa: BLE001 - the error we raised
        assert exc is error or isinstance(exc, type(error))
    else:
        raise AssertionError("the producer should have stopped")


def test_a_holder_stopped_with_an_outstanding_operation_leaves_it_on_an_expired_claim(app_env):
    """Cancelled (a worker shutdown, a lost lease) after Veo accepted the clip: the row stays, expired, with the
    record, so the next job takes it over and resumes the paid operation instead of paying again."""
    for key, status in (("video-stop-submitted", "submitted"), ("video-stop-submitting", "submitting")):
        _stop(make_store(), key, {**OPERATION, "asset_key": key, "status": status}, asyncio.CancelledError())
        (row,) = [r for r in claim_rows() if r.key == key]
        assert row.operation["status"] == status and _expired(row)
    calls: list[Any] = []

    async def produce() -> Produced:
        calls.append(inherited_operation("video-stop-submitted"))
        return Produced(data=VIDEO, mime="video/mp4")

    asyncio.run(make_store().get_or_create("video-stop-submitted", "video", produce))
    assert calls == [{**OPERATION, "asset_key": "video-stop-submitted"}]
    assert [r.key for r in claim_rows()] == ["video-stop-submitting"]


def test_owner_scoped_stops_keep_the_record_but_ordinary_failures_and_finished_records_release(app_env):
    from aadhi.jobs.base import JobCancelled

    _stop(make_store(), "video-lease", {**OPERATION, "asset_key": "video-lease"}, JobCancelled("lost", reason="lease_lost"))
    assert [r.key for r in claim_rows()] == ["video-lease"]
    for n, (record, error) in enumerate([
        (None, asyncio.CancelledError()),  # nothing was submitted: nothing to resume
        ({**OPERATION, "status": "completed"}, asyncio.CancelledError()),
        ({**OPERATION, "status": "failed"}, asyncio.CancelledError()),
        ({**OPERATION, "status": "abandoned"}, JobCancelled("cancelled")),
        ({**OPERATION, "status": "submitted"}, RuntimeError("provider error")),  # an outcome known in a live process
    ]):
        key = f"video-release-{n}"
        _stop(make_store(), key, {**record, "asset_key": key} if record else None, error)
        assert key not in [r.key for r in claim_rows()], (record, error)


def test_operation_store_prefers_an_inherited_submitted_operation_over_a_finished_own_record(app_env):
    """OperationStore.get: an outstanding own record always wins; a finished one gives way to a submitted
    operation inherited with the claim (same scope, another operation name)."""
    from types import SimpleNamespace

    from aadhi.providers.operations import PAYLOAD_KEY, OperationStore

    key = "video-op"
    inherited = {**OPERATION, "operation": "operations/op-7"}
    dead_claim(key, inherited)
    seen: dict[str, Any] = {}

    def own(status: str, operation: str = "") -> OperationStore:
        record = {**OPERATION, "status": status, "operation": operation, "job_id": 2}
        return OperationStore(SimpleNamespace(payload={PAYLOAD_KEY: {key: record}}, job_id=0))

    async def produce() -> Produced:
        for status in ("pending", "completed", "failed", "abandoned"):
            seen[status] = own(status).get(key, "").operation
        for status in ("submitting", "submitted", "ambiguous", "lost"):
            seen[status] = own(status, "operations/op-2").get(key, "").status
        seen["same"] = own("failed", "operations/op-7").get(key, "").status  # the stale copy of its own record
        seen["scope"] = own("failed").get(key, "user:5")
        seen["none"] = OperationStore(SimpleNamespace(payload={}, job_id=0)).get(key, "").operation
        return Produced(data=VIDEO, mime="video/mp4")

    asyncio.run(make_store().get_or_create(key, "video", produce))
    assert [seen[s] for s in ("pending", "completed", "failed", "abandoned")] == ["operations/op-7"] * 4
    assert [seen[s] for s in ("submitting", "submitted", "ambiguous", "lost")] == [
        "submitting", "submitted", "ambiguous", "lost"]
    assert seen["same"] == "failed" and seen["scope"] is None and seen["none"] == "operations/op-7"

