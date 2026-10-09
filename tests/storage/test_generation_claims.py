"""Cross-process generation claims, the generation guard, owner-scoped in-flight failures and the
canonical media identity (``aadhi.storage.assets``).

Separate ``AssetStore`` instances stand in for separate worker processes (each has its own in-flight
map, so only the ``asset_claims`` row can de-duplicate them); one test uses real processes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import multiprocessing
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

import aadhi.jobs.base  # noqa: F401  (registers JobCancelled / BudgetExceeded as owner-scoped)
from aadhi.db import get_sessionmaker, session_scope
from aadhi.jobs.base import BudgetExceeded, JobCancelled
from aadhi.models import Asset, AssetClaim, Job, utcnow
from aadhi.storage import get_storage
from aadhi.storage.assets import (
    AssetClaimTimeout,
    AssetStore,
    ClaimConfig,
    ClaimOwner,
    Produced,
    claim_owner,
    compute_key,
    generation_guard,
    media_key,
    normalize_prompt,
)

FAST = ClaimConfig(ttl_seconds=30, poll_seconds=0.02, max_wait_seconds=10)
VIDEO = b"\x00\x00\x00\x18ftypmp42 clip"


def make_store(config: ClaimConfig = FAST) -> AssetStore:
    """A new store = what another worker process would have (its own in-flight map)."""
    return AssetStore(get_storage(), get_sessionmaker(), claims=config)


def claims() -> list[AssetClaim]:
    with session_scope() as db:
        rows = list(db.execute(select(AssetClaim)).scalars())
        for r in rows:
            db.expunge(r)
        return rows


def add_claim(key: str, *, holder: str = "other", expires_in: float = 60.0, job_id: int | None = None,
              worker_id: str | None = "host:1:boot:t", attempt: int | None = None) -> None:
    now = utcnow()
    with session_scope() as db:
        db.add(AssetClaim(key=key, holder=holder, job_id=job_id, worker_id=worker_id, attempt=attempt, created_at=now,
                          expires_at=now + dt.timedelta(seconds=expires_in)))


def add_job(status: str = "running", locked_by: str | None = "host:1:boot:t", attempts: int = 1) -> int:
    with session_scope() as db:
        job = Job(kind="build_assets", status=status, locked_by=locked_by, attempts=attempts, max_attempts=2)
        db.add(job)
        db.flush()
        return job.id


def counting_producer(calls: list[str], name: str, *, hold: float = 0.0, data: bytes = VIDEO,
                      started: threading.Event | None = None):
    async def produce() -> Produced:
        calls.append(name)
        if started is not None:
            started.set()
        if hold:
            await asyncio.sleep(hold)
        return Produced(data=data, mime="video/mp4", duration_s=8.0)

    return produce


def run_in_thread(coro_factory) -> tuple[threading.Thread, dict[str, Any]]:
    """Run a coroutine in its own thread + event loop (like another worker's job thread)."""
    out: dict[str, Any] = {}

    def main() -> None:
        try:
            out["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            out["error"] = exc

    t = threading.Thread(target=main, daemon=True)
    t.start()
    return t, out


# --- canonical identity ---------------------------------------------------------------------------


def test_normalize_prompt_keeps_case_and_punctuation():
    assert normalize_prompt("  A  red\r\nball,\tbouncing!\n") == "A red ball, bouncing!"
    assert normalize_prompt("Café au lait") == "Café au lait"  # NFC
    assert normalize_prompt(None) == "" and normalize_prompt("") == ""
    once = normalize_prompt(" x   y ")
    assert normalize_prompt(once) == once == "x y"


def test_media_key_is_unchanged_for_normalized_prompts():
    prompt = "A copper wire glowing as current flows. clean educational illustration, no text"
    legacy = compute_key("image", {"prompt": prompt, "provider": "gemini", "model": "gemini-2.5-flash-image",
                                   "aspect": "16:9"})
    assert media_key("image", prompt, provider="gemini", model="gemini-2.5-flash-image", aspect="16:9") == legacy
    assert legacy == "image-f2a1b6080cd84c5c78a8fbb8c43b2f9f9b760493"  # golden: cached media must keep its key
    assert legacy == compute_key("image", {"aspect": "16:9", "model": "gemini-2.5-flash-image", "provider": "gemini",
                                           "prompt": prompt})  # key order never matters
    variant = "A copper  wire glowing\nas current flows.  clean educational illustration, no text "
    assert media_key("image", variant, provider="gemini", model="gemini-2.5-flash-image", aspect="16:9") == legacy
    video = media_key("video", "Electrons drifting", provider="veo", model="veo-2.0-generate-001", aspect="16:9")
    assert video == compute_key("video", {"prompt": "Electrons drifting", "provider": "veo",
                                          "model": "veo-2.0-generate-001", "aspect": "16:9"})
    with_params = media_key("video", "Electrons drifting", provider="veo", model="veo-2.0-generate-001",
                            aspect="16:9", params={"negative_prompt": "text"})
    assert with_params != video and media_key("video", "Electrons drifting", provider="veo",
                                               model="veo-2.0-generate-001", aspect="16:9", params={}) == video


def test_media_key_golden_value():
    """A fixed literal: changing ``compute_key``/``canonical_json`` would silently re-bill every cached clip."""
    key = "video-37721cc6029ef8fa0a38f73235e36317964c657f"
    assert media_key("video", "A ball rolling down a ramp", provider="fake", model="m", aspect="16:9") == key
    assert media_key("video", " A ball\nrolling  down a ramp ", provider="fake", model="m", aspect="16:9") == key


def test_first_existing_follows_the_order_and_skips_missing_blobs(app_env):
    store = make_store()
    a = store.put("image-aaa", "image", Produced(data=b"\x89PNG a", mime="image/png"))
    b = store.put("image-bbb", "image", Produced(data=b"\x89PNG b", mime="image/png"))
    assert store.first_existing(["image-missing", "image-bbb", "image-aaa"]).key == "image-bbb"
    assert store.first_existing(["image-aaa", "image-bbb"]).key == "image-aaa"
    assert store.first_existing([]) is None and store.first_existing(["image-missing", ""]) is None
    store.storage.delete(b.storage_key)
    assert store.first_existing(["image-bbb", "image-aaa"]).key == a.key  # blob gone: next candidate
    assert store.first_existing(["image-bbb"], verify_blob=False).key == "image-bbb"


# --- cross-process claims ---------------------------------------------------------------------------


def test_two_processes_generate_a_paid_asset_once(app_env):
    """Two stores (= two worker processes) ask for the same clip at once: one provider call."""
    calls: list[str] = []
    started = threading.Event()
    store_a, store_b = make_store(), make_store()
    t1, out1 = run_in_thread(lambda: store_a.get_or_create(
        "video-same", "video", counting_producer(calls, "a", hold=0.4, started=started)))
    assert started.wait(5)
    t2, out2 = run_in_thread(lambda: store_b.get_or_create("video-same", "video", counting_producer(calls, "b")))
    t1.join(10)
    t2.join(10)
    assert "error" not in out1 and "error" not in out2, (out1, out2)
    (asset1, created1), (asset2, created2) = out1["value"], out2["value"]
    assert calls == ["a"] and created1 and not created2
    assert asset1.storage_key == asset2.storage_key
    assert claims() == []  # released by the holder


def test_unpaid_kinds_take_no_claim(app_env):
    store = make_store()
    seen: list[int] = []

    async def produce() -> Produced:
        seen.append(len(claims()))
        return Produced(data=b"ID3 audio", mime="audio/mpeg")

    asset, created = asyncio.run(store.get_or_create("tts-x", "tts", produce))
    assert created and seen == [0]


def test_claim_row_names_the_job_lease(app_env):
    store = make_store()
    rows: list[AssetClaim] = []

    async def produce() -> Produced:
        rows.extend(claims())
        return Produced(data=VIDEO, mime="video/mp4")

    async def scenario():
        owner = ClaimOwner(worker_id="host:42:boot:job-thread", job_id=7, attempt=3)
        with claim_owner(owner):
            return await store.get_or_create("video-lease", "video", produce)

    asyncio.run(scenario())
    assert len(rows) == 1
    row = rows[0]
    assert (row.key, row.job_id, row.worker_id, row.attempt) == ("video-lease", 7, "host:42:boot:job-thread", 3)
    assert len(row.holder) == 32 and claims() == []


def test_waiter_reuses_the_holders_asset(app_env):
    """A live holder (running job with that lease, not expired): the waiter polls until the asset is there."""
    job_id = add_job()
    add_claim("video-wait", job_id=job_id, attempt=1)
    calls: list[str] = []
    store = make_store()

    async def scenario():
        task = asyncio.ensure_future(store.get_or_create("video-wait", "video", counting_producer(calls, "waiter")))
        await asyncio.sleep(0.3)
        assert not task.done()  # still waiting for the holder
        other = make_store()  # the holder finishes: stores the asset, then deletes its claim
        await asyncio.to_thread(other.put, "video-wait", "video", Produced(data=VIDEO, mime="video/mp4"))
        with session_scope() as db:
            db.execute(text("DELETE FROM asset_claims WHERE key = 'video-wait'"))
        return await asyncio.wait_for(task, 5)

    asset, created = asyncio.run(scenario())
    assert asset.key == "video-wait" and not created and calls == []


def test_expired_claim_of_a_crashed_holder_is_taken_over(app_env, caplog):
    add_claim("video-expired", expires_in=-5)  # a holder that stopped renewing (process died)
    calls: list[str] = []
    with caplog.at_level(logging.WARNING, logger="aadhi.storage.assets"):
        asset, created = asyncio.run(make_store().get_or_create("video-expired", "video",
                                                                counting_producer(calls, "me")))
    assert created and calls == ["me"] and claims() == []
    assert any("took over" in r.message for r in caplog.records)


@pytest.mark.parametrize("job_state", [
    {"status": "queued", "locked_by": None, "attempts": 1},  # requeued by the reaper / restart recovery
    {"status": "running", "locked_by": "host:1:boot:t", "attempts": 2},  # the next attempt runs now
    {"status": "failed", "locked_by": None, "attempts": 1},
    None,  # job row gone
])
def test_claim_of_a_job_whose_lease_is_gone_is_taken_over_at_once(app_env, job_state):
    job_id = add_job(**job_state) if job_state else 999_999
    add_claim("video-dead-job", job_id=job_id, attempt=1, expires_in=600)  # not expired yet
    calls: list[str] = []
    asset, created = asyncio.run(make_store().get_or_create("video-dead-job", "video",
                                                            counting_producer(calls, "me")))
    assert created and calls == ["me"] and claims() == []


def test_wait_is_bounded(app_env):
    add_claim("video-slow", expires_in=600)  # anonymous live holder that never finishes
    calls: list[str] = []
    store = make_store(ClaimConfig(ttl_seconds=30, poll_seconds=0.02, max_wait_seconds=0.2))
    with pytest.raises(AssetClaimTimeout, match="still generating"):
        asyncio.run(store.get_or_create("video-slow", "video", counting_producer(calls, "me")))
    assert calls == [] and [c.holder for c in claims()] == ["other"]  # the holder's claim is untouched


def test_waiting_honours_the_jobs_cancellation(app_env):
    add_claim("video-cancel", expires_in=600)
    flag = {"cancel": False}

    def check() -> None:
        if flag["cancel"]:
            raise JobCancelled("cancelled by user")

    async def scenario():
        with claim_owner(ClaimOwner(worker_id="w", job_id=None, check_cancelled=check)):
            task = asyncio.ensure_future(make_store().get_or_create("video-cancel", "video",
                                                                    counting_producer([], "me")))
        await asyncio.sleep(0.1)
        flag["cancel"] = True
        return await asyncio.wait_for(task, 5)

    with pytest.raises(JobCancelled):
        asyncio.run(scenario())
    assert [c.holder for c in claims()] == ["other"]


def test_producer_failure_releases_the_claim(app_env):
    async def broken() -> Produced:
        assert len(claims()) == 1
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        asyncio.run(make_store().get_or_create("video-broken", "video", broken))
    assert claims() == []
    # the next request (e.g. another process, after the failure) claims and generates normally
    asset, created = asyncio.run(make_store().get_or_create("video-broken", "video", counting_producer([], "again")))
    assert created


def test_holder_renews_its_claim_while_producing(app_env):
    store = make_store(ClaimConfig(ttl_seconds=0.4, poll_seconds=0.02, max_wait_seconds=10))
    calls: list[str] = []
    started = threading.Event()
    t1, out1 = run_in_thread(lambda: store.get_or_create(
        "video-renew", "video", counting_producer(calls, "holder", hold=1.2, started=started)))
    assert started.wait(5)
    first = claims()[0].expires_at
    other = make_store(ClaimConfig(ttl_seconds=0.4, poll_seconds=0.02, max_wait_seconds=10))
    t2, out2 = run_in_thread(lambda: other.get_or_create("video-renew", "video", counting_producer(calls, "waiter")))
    threading.Event().wait(0.8)  # longer than the ttl: only renewals keep the claim alive
    assert claims() and claims()[0].expires_at > first
    t1.join(10)
    t2.join(10)
    assert calls == ["holder"], (out1, out2)
    assert out1["value"][1] and not out2["value"][1]


def test_missing_claim_table_degrades_to_unclaimed_generation(app_env, caplog):
    with session_scope() as db:
        db.execute(text("DROP TABLE asset_claims"))
    calls: list[str] = []
    with caplog.at_level(logging.WARNING, logger="aadhi.storage.assets"):
        asset, created = asyncio.run(make_store().get_or_create("video-noclaims", "video",
                                                                counting_producer(calls, "me")))
    assert created and calls == ["me"]
    assert any("generation claims are unavailable" in r.message for r in caplog.records)


def test_claims_off_when_no_kinds_configured(app_env):
    store = make_store(ClaimConfig(kinds=frozenset(), poll_seconds=0.02))
    add_claim("video-off", expires_in=600)  # would block a claiming store
    asset, created = asyncio.run(store.get_or_create("video-off", "video", counting_producer([], "me")))
    assert created


def test_claim_config_from_settings(app_env):
    app_env.generation_claim_kinds = " video , image ,manim"
    app_env.generation_claim_ttl_seconds = 90
    cfg = ClaimConfig.from_settings(app_env)
    assert cfg.kinds == {"video", "image", "manim"} and cfg.ttl_seconds == 90
    app_env.generation_claim_kinds = ""
    assert ClaimConfig.from_settings(app_env).kinds == frozenset()


def test_postgres_claim_insert_is_on_conflict_do_nothing():
    stmt = (postgresql.insert(AssetClaim).values(key="k", holder="h", created_at=utcnow(), expires_at=utcnow())
            .on_conflict_do_nothing(index_elements=["key"]).returning(AssetClaim.key))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (key) DO NOTHING" in sql and "RETURNING asset_claims.key" in sql


def test_real_processes_share_one_generation(app_env, tmp_path):
    """Two spawned processes (own engines, own stores) on one SQLite database: one provider call."""
    from tests.storage._claim_proc import produce_video

    mp = multiprocessing.get_context("spawn")
    barrier = mp.Barrier(2)
    calls_file = tmp_path / "calls.txt"
    calls_file.write_text("", encoding="utf-8")
    results = [tmp_path / "r1.json", tmp_path / "r2.json"]
    procs = [mp.Process(target=produce_video, args=("video-procs", str(calls_file), str(r), barrier, 1.5))
             for r in results]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    out = [json.loads(Path(r).read_text(encoding="utf-8")) for r in results]
    assert len(calls_file.read_text(encoding="utf-8").split()) == 1  # one paid generation
    assert sorted(o["created"] for o in out) == [False, True]
    assert out[0]["storage_key"] == out[1]["storage_key"]
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Asset).where(Asset.key == "video-procs")).scalar_one() == 1
    assert claims() == []


# --- generation guard --------------------------------------------------------------------------------


def test_generation_guard_runs_before_paid_producers_only(app_env):
    seen: list[tuple[str, str]] = []
    calls: list[str] = []

    def guard(kind: str, key: str) -> None:
        seen.append((kind, key))
        if key == "video-unwanted":
            raise JobCancelled("not wanted any more", reason="version_changed")

    store = make_store()
    store.put("video-cached", "video", Produced(data=VIDEO, mime="video/mp4"))

    async def scenario():
        with generation_guard(guard):
            await store.get_or_create("tts-free", "tts", counting_producer(calls, "tts"))
            await store.get_or_create("video-cached", "video", counting_producer(calls, "cached"))
            await store.get_or_create("video-wanted", "video", counting_producer(calls, "wanted"))
            await store.get_or_create("video-unwanted", "video", counting_producer(calls, "unwanted"))

    with pytest.raises(JobCancelled, match="not wanted"):
        asyncio.run(scenario())
    assert seen == [("video", "video-wanted"), ("video", "video-unwanted")]  # not for tts, not for cached media
    assert calls == ["tts", "wanted"] and claims() == []  # refused before paying; its claim is released


def test_unguarded_production_skips_the_guard_but_keeps_the_claim(app_env):
    """A producer that only collects work already paid for (e.g. a resumed AI video operation)."""
    rows: list[int] = []

    def guard(kind: str, key: str) -> None:  # pragma: no cover - must not be called
        raise AssertionError("guard called")

    async def produce() -> Produced:
        rows.append(len(claims()))
        return Produced(data=VIDEO, mime="video/mp4")

    async def scenario():
        with generation_guard(guard):
            return await make_store().get_or_create("video-resume", "video", produce, guarded=False)

    asset, created = asyncio.run(scenario())
    assert created and rows == [1] and claims() == []


# --- owner-scoped in-flight failures -------------------------------------------------------------------


@pytest.mark.parametrize("error", [JobCancelled("owner cancelled"), BudgetExceeded("owner over budget")])
def test_waiters_do_not_inherit_the_owners_own_stop(app_env, error):
    """Job B waits on job A's in-flight run; A is cancelled / over budget: B generates itself."""
    store = make_store()
    calls: list[str] = []

    async def scenario():
        gate = asyncio.Event()

        async def owner() -> Produced:
            calls.append("A")
            await gate.wait()
            raise error

        a = asyncio.ensure_future(store.get_or_create("video-owner", "video", owner))
        while not calls:
            await asyncio.sleep(0.01)
        b = asyncio.ensure_future(store.get_or_create("video-owner", "video", counting_producer(calls, "B")))
        await asyncio.sleep(0.1)
        gate.set()
        return await asyncio.gather(a, b, return_exceptions=True)

    first, second = asyncio.run(scenario())
    assert type(first) is type(error)
    asset, created = second
    assert created and calls == ["A", "B"] and not store._inflight


def test_waiters_still_share_a_provider_failure(app_env):
    store = make_store()
    calls: list[str] = []

    async def scenario():
        gate = asyncio.Event()

        async def owner() -> Produced:
            calls.append("A")
            await gate.wait()
            raise RuntimeError("provider refused the prompt")

        a = asyncio.ensure_future(store.get_or_create("video-shared-fail", "video", owner))
        while not calls:
            await asyncio.sleep(0.01)
        b = asyncio.ensure_future(store.get_or_create("video-shared-fail", "video", counting_producer(calls, "B")))
        await asyncio.sleep(0.1)
        gate.set()
        return await asyncio.gather(a, b, return_exceptions=True)

    first, second = asyncio.run(scenario())
    assert isinstance(first, RuntimeError) and isinstance(second, RuntimeError) and calls == ["A"]


def test_a_cancelled_waiter_is_not_retried(app_env):
    store = make_store()

    async def scenario():
        gate = asyncio.Event()
        calls: list[str] = []

        async def owner() -> Produced:
            calls.append("A")
            await gate.wait()
            return Produced(data=VIDEO, mime="video/mp4")

        a = asyncio.ensure_future(store.get_or_create("video-waiter-cancel", "video", owner))
        while not calls:
            await asyncio.sleep(0.01)
        b = asyncio.ensure_future(store.get_or_create("video-waiter-cancel", "video", counting_producer(calls, "B")))
        await asyncio.sleep(0.05)
        b.cancel()
        with pytest.raises(asyncio.CancelledError):
            await b
        gate.set()
        asset, created = await a
        return calls, created

    calls, created = asyncio.run(scenario())
    assert calls == ["A"] and created


def test_waiters_stop_with_their_cancelled_siblings_instead_of_generating(app_env):
    """One job's scenes A and B share a key (A owns it, B waits); a third scene fails, so gather_all cancels
    them all: B must stop too, not swallow its cancellation and run the producer."""
    from aadhi.pipeline.aio import gather_all

    store = make_store(ClaimConfig(kinds=frozenset(), poll_seconds=0.02))
    calls: list[str] = []

    async def scenario():
        gate = asyncio.Event()

        async def owner() -> Produced:
            calls.append("A")
            await gate.wait()
            return Produced(data=VIDEO, mime="video/mp4")

        async def failing() -> None:
            while len(calls) < 1:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)  # B is waiting on A by now
            raise RuntimeError("another scene failed")

        with pytest.raises(RuntimeError):
            await gather_all([store.get_or_create("tts-shared", "tts", owner),
                              store.get_or_create("tts-shared", "tts", counting_producer(calls, "B")), failing()])

    asyncio.run(scenario())
    assert calls == ["A"] and not store._inflight


def test_a_waiter_of_the_same_job_does_not_pay_again_after_the_owners_budget_stop(app_env):
    from aadhi.pipeline.aio import gather_all

    store = make_store()
    calls: list[str] = []

    async def scenario():
        gate = asyncio.Event()

        async def owner() -> Produced:
            calls.append("A")
            await gate.wait()
            raise BudgetExceeded("job budget exceeded")

        async def release() -> None:
            await asyncio.sleep(0.1)
            gate.set()

        with claim_owner(ClaimOwner(worker_id="w", job_id=7)), pytest.raises(BudgetExceeded):
            await gather_all([store.get_or_create("video-budget", "video", owner),
                              store.get_or_create("video-budget", "video", counting_producer(calls, "B")),
                              release()])

    asyncio.run(scenario())
    assert calls == ["A"] and not store._inflight


def test_cancelled_waiters_never_take_over_another_processes_claim(app_env):
    """A waits on another process's live claim, B waits on A; a sibling fails: nobody keeps polling until the
    claim expires to run the paid producer for a job that already failed."""
    import time

    from aadhi.pipeline.aio import gather_all

    store = make_store(ClaimConfig(ttl_seconds=30, poll_seconds=0.02, max_wait_seconds=10))
    add_claim("video-held", expires_in=1.5)
    calls: list[str] = []

    async def scenario() -> float:
        async def failing() -> None:
            await asyncio.sleep(0.2)
            raise RuntimeError("another scene failed")

        start = time.monotonic()
        with pytest.raises(RuntimeError):
            await gather_all([store.get_or_create("video-held", "video", counting_producer(calls, "A-PAID")),
                              store.get_or_create("video-held", "video", counting_producer(calls, "B-PAID")),
                              failing()])
        return time.monotonic() - start

    elapsed = asyncio.run(scenario())
    assert calls == [] and elapsed < 1.2 and not store._inflight
