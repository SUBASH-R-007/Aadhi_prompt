"""Shared provider plumbing: retries, key pool, error construction, subprocess + ffprobe helpers."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from aadhi.providers._common import LoopLocal, aspect_dims, make_error, sanitize_host, stable_seed, style_prompt
from aadhi.providers._process import run_process
from aadhi.providers._retry import ErrorInfo, call_with_retries
from aadhi.providers.base import ProviderError, RateLimited
from aadhi.providers.gemini_common import KeyPool, _parse_retry_delay, classify_gemini_error, key_pool
from aadhi.providers.media import probe_media, sniff_extension

from .fakes import gemini_error


class Boom(Exception):
    pass


@pytest.mark.asyncio
async def test_call_with_retries_retries_only_retryable() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise Boom()
        return "ok"

    out = await call_with_retries(flaky, classify=lambda e: ErrorInfo(retry=isinstance(e, Boom)), max_attempts=5,
                                  base_delay=1.0, max_delay=10.0, sleep=sleep)
    assert out == "ok" and len(calls) == 3 and len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 2.0 and 2.0 <= sleeps[1] <= 3.0  # exponential + jitter

    calls.clear()

    async def fatal() -> None:
        calls.append(1)
        raise ValueError("no")

    with pytest.raises(ValueError):
        await call_with_retries(fatal, classify=lambda e: ErrorInfo(retry=False), max_attempts=5, sleep=sleep)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_after_and_fresh_key_waits() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def limited() -> None:
        raise Boom()

    with pytest.raises(Boom):
        await call_with_retries(limited, classify=lambda e: ErrorInfo(retry=True, rate_limited=True, retry_after=7.0),
                                max_attempts=2, max_delay=30.0, sleep=sleep)
    assert sleeps == [pytest.approx(7.0)]
    sleeps.clear()
    with pytest.raises(Boom):
        await call_with_retries(limited, classify=lambda e: ErrorInfo(retry=True, rate_limited=True),
                                max_attempts=2, has_fresh_key=lambda: True, sleep=sleep)
    assert sleeps == [0.2]


@pytest.mark.asyncio
async def test_lambda_returning_coroutine_is_awaited() -> None:
    async def value() -> int:
        return 42

    assert await call_with_retries(lambda: value(), classify=lambda e: ErrorInfo()) == 42


def test_key_pool_rotation_and_cooldown(monkeypatch) -> None:
    import aadhi.providers.gemini_common as gc

    now = [1000.0]
    monkeypatch.setattr(gc, "time", SimpleNamespace(monotonic=lambda: now[0]))  # module-local clock
    pool = KeyPool(["k1", "k2", "k3", "k1"])
    assert pool.size == 3 and pool.acquire() == "k1"
    pool.mark_rate_limited("k1", 30)
    assert pool.acquire() == "k2" and pool.has_fresh_key()
    pool.mark_rate_limited("k2", 30)
    pool.mark_rate_limited("k3", 5)
    assert not pool.has_fresh_key()
    assert pool.acquire() == "k3"  # all cooling: the one that recovers first
    now[0] += 31
    assert pool.acquire() == "k3" and pool.has_fresh_key()
    pool.mark_rate_limited("unknown")  # ignored
    with pytest.raises(ValueError):
        KeyPool([])
    assert key_pool(["a1", "b2"]) is key_pool(["a1", "b2"])


def test_gemini_error_classification() -> None:
    assert classify_gemini_error(gemini_error(429, "x", retry_delay="12s")) == ErrorInfo(
        retry=True, rate_limited=True, status=429, retry_after=12.0)
    assert classify_gemini_error(gemini_error(503, "x")).retry
    assert not classify_gemini_error(gemini_error(400, "x")).retry
    assert classify_gemini_error(TimeoutError()).retry
    assert classify_gemini_error(ConnectionResetError()).retry
    assert not classify_gemini_error(ProviderError("x")).retry
    assert not classify_gemini_error(KeyError("x")).retry
    assert _parse_retry_delay({"error": {"details": [{"retryDelay": "1.5s"}]}}) == 1.5
    assert _parse_retry_delay({"error": {"details": [{"retryDelay": "3600s"}]}}) == 3600.0  # not capped
    assert _parse_retry_delay({"error": {"details": [{"retryDelay": ".5s"}]}}) == 0.5
    assert _parse_retry_delay("garbage") is None and _parse_retry_delay({"error": {"details": "x"}}) is None


def test_make_error_redacts_and_truncates(make_settings) -> None:
    s = make_settings(gemini_api_key="AIzaSECRET-0123456789")
    err = make_error(RateLimited, "gemini", "call failed", settings=s, status=429, host="example.com",
                     detail="key AIzaSECRET-0123456789 " + "x" * 1000)
    text = str(err)
    assert isinstance(err, RateLimited) and err.status == 429 and err.provider == "gemini"
    assert text.startswith("gemini: call failed (HTTP 429, example.com): key [REDACTED]")
    assert len(text) < 400 and text.endswith("...")


def test_small_helpers(make_settings) -> None:
    assert sanitize_host("https://user:pw@api.example.com:8443/path?key=1") == "api.example.com"
    assert sanitize_host("api.example.com/x") == "api.example.com"
    assert aspect_dims("16:9") == (1280, 720) and aspect_dims("weird") == (1280, 720)
    assert stable_seed("x") == stable_seed("x") != stable_seed("y")
    s = make_settings()
    styled = style_prompt("  A  circuit ", s)
    assert styled.startswith("A circuit. ") and styled.endswith("no text")
    assert style_prompt(styled, s) == styled
    assert sniff_extension(b"RIFF....WAVE") == ".wav" and sniff_extension(b"\x00\x00\x00\x18ftypmp42") == ".mp4"
    assert sniff_extension(b"ID3") == ".mp3" and sniff_extension(b"???") == ".bin"


@pytest.mark.asyncio
async def test_loop_local_creates_one_object_per_loop() -> None:
    created: list[object] = []

    def factory() -> object:
        obj = object()
        created.append(obj)
        return obj

    cache: LoopLocal[object] = LoopLocal(factory)
    a = await cache.aget()
    assert cache.get() is a and await cache.aget() is a

    def other_loop() -> object:
        return asyncio.run(cache.aget())

    b = await asyncio.to_thread(other_loop)
    assert b is not a and len(created) == 2


@pytest.mark.asyncio
async def test_run_process_success_missing_and_timeout() -> None:
    res = await run_process([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
                            data=b"abc", timeout=30)
    assert res.returncode == 0 and res.stdout == b"ABC"
    with pytest.raises(ProviderError, match="not installed"):
        await run_process(["definitely-not-a-real-binary-xyz"], timeout=5)
    with pytest.raises(ProviderError, match="timed out"):
        await run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)


@pytest.mark.asyncio
async def test_probe_errors(app_env) -> None:
    with pytest.raises(ProviderError, match="empty"):
        await probe_media(b"", settings=app_env)
    with pytest.raises(ProviderError, match="ffprobe failed"):
        await probe_media(b"definitely not media", settings=app_env)
    missing = app_env.model_copy(update={"ffprobe_path": "no-such-ffprobe-binary"})
    with pytest.raises(ProviderError, match="not installed"):
        await probe_media(b"RIFFxxxxWAVE", settings=missing)


@pytest.mark.asyncio
async def test_retry_logging_never_includes_exception_text(caplog) -> None:
    import logging

    secret = "AIzaVERY-SECRET-KEY-123"

    async def failing() -> None:
        raise Boom(f"bad key {secret}")

    with caplog.at_level(logging.INFO, logger="aadhi.providers._retry"), pytest.raises(Boom):
        await call_with_retries(failing, classify=lambda e: ErrorInfo(retry=True, status=503), max_attempts=3,
                                sleep=lambda s: asyncio.sleep(0), label="gemini llm")
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2 and all("gemini llm: retrying after Boom HTTP 503" in m for m in messages)
    assert not any(secret in m for m in messages)


def test_key_pools_are_scoped() -> None:
    llm_pool, tts_pool = key_pool(["k1", "k2"], "llm"), key_pool(["k1", "k2"], "tts")
    assert llm_pool is not tts_pool
    llm_pool.mark_rate_limited("k1", 60)
    assert tts_pool.acquire() == "k1" and llm_pool.acquire() == "k2"



# --- review fixes: per-loop cache cleanup ---------------------------------------------------------


def test_loop_local_drops_entries_of_closed_loops() -> None:
    import gc
    import weakref

    class Client:  # like an httpx/openai client: once used, its connection pool references the loop
        loop: asyncio.AbstractEventLoop | None = None

    cache: LoopLocal[Client] = LoopLocal(Client)
    loops: list[weakref.ref] = []

    async def job(use_async: bool) -> None:
        loops.append(weakref.ref(asyncio.get_running_loop()))
        client = await cache.aget() if use_async else cache.get()
        assert client.loop is None  # a fresh client per loop
        client.loop = asyncio.get_running_loop()

    asyncio.run(job(True))  # the worker runs every job in a fresh asyncio.run()
    assert len(cache) == 1
    asyncio.run(job(False))
    assert len(cache) == 1  # the first (closed) loop's client was dropped on the next lookup
    asyncio.run(job(True))
    assert len(cache) == 1
    gc.collect()
    assert loops[0]() is None and loops[1]() is None  # closed loops (and their clients) are released


@pytest.mark.asyncio
async def test_loop_local_releases_real_httpx_clients() -> None:
    import gc
    import weakref

    from aadhi.providers._http import loop_local_client

    cache = loop_local_client(5.0)
    refs: list[weakref.ref] = []

    def run_job() -> None:
        async def job() -> None:
            refs.append(weakref.ref(await cache.aget()))

        asyncio.run(job())

    await asyncio.to_thread(run_job)
    await asyncio.to_thread(run_job)
    current = await cache.aget()  # this lookup purges the second job's closed loop
    gc.collect()
    assert refs[0]() is None and refs[1]() is None
    assert len(cache) == 1 and current is cache.get()
    await current.aclose()


# --- review fixes: redaction before truncation ----------------------------------------------------


def test_make_error_redacts_a_secret_straddling_the_cut(make_settings) -> None:
    key = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"
    s = make_settings(gemini_api_key=key)
    detail = "x" * 290 + " key " + key + " rejected"  # the key straddles the 300-character cut
    text = str(make_error(ProviderError, "gemini", "call failed", settings=s, detail=detail))
    assert key[:6] not in text and text.endswith("key [REDA...")  # redacted first, then truncated
    short = str(make_error(ProviderError, "gemini", "call failed", settings=s, detail="x" * 270 + " key " + key))
    assert key[:6] not in short and short.endswith("key [REDACTED]")


def test_make_error_removes_a_dangling_secret_prefix(make_settings) -> None:
    key = "el-secret-key-1234567890"
    s = make_settings(elevenlabs_api_key=key)
    # an upstream layer (e.g. a 2000-character body cap) cut the key in half
    text = str(make_error(ProviderError, "elevenlabs", "failed", settings=s, detail="bad key " + key[:15]))
    assert key[:6] not in text and text.endswith("bad key [REDACTED]")
    # ordinary text is untouched; no settings means no redaction at all
    assert str(make_error(ProviderError, "x", "failed", settings=s, detail="plain words")) == "x: failed: plain words"
    assert str(make_error(ProviderError, "x", "failed", settings=None, detail="y")) == "x: failed: y"


# --- review fixes: server-suggested delays ---------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_after_longer_than_max_delay_is_honoured() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def limited() -> None:
        raise Boom()

    with pytest.raises(Boom):
        await call_with_retries(limited, classify=lambda e: ErrorInfo(retry=True, rate_limited=True, retry_after=60.0),
                                max_attempts=2, max_delay=30.0, sleep=sleep)
    assert sleeps == [60.0]


@pytest.mark.asyncio
async def test_retry_after_beyond_ceiling_stops_retrying() -> None:
    calls: list[int] = []

    async def limited() -> None:
        calls.append(1)
        raise Boom()

    info = ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=3600.0)
    with pytest.raises(Boom):
        await call_with_retries(limited, classify=lambda e: info, max_attempts=4, sleep=lambda s: asyncio.sleep(0))
    assert len(calls) == 1  # waiting an hour is pointless: fail fast
    calls.clear()
    with pytest.raises(Boom):  # ...unless another key of the pool can be used immediately
        await call_with_retries(limited, classify=lambda e: info, max_attempts=3, has_fresh_key=lambda: True,
                                sleep=lambda s: asyncio.sleep(0))
    assert len(calls) == 3


def test_parse_retry_after_header_forms() -> None:
    from datetime import datetime, timezone

    from aadhi.providers._http import parse_retry_after

    now = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("45") == 45.0 and parse_retry_after(" 1.5 ") == 1.5
    assert parse_retry_after("Thu, 01 Oct 2026 12:02:00 GMT", now=now) == 120.0
    assert parse_retry_after("Thu, 01 Oct 2026 11:00:00 GMT", now=now) == 0.0  # in the past
    assert parse_retry_after("-5") == 0.0
    for bad in (None, "", "soon", "nan", "inf"):
        assert parse_retry_after(bad) is None


def test_openai_rate_limit_retry_after_parsing() -> None:
    import openai

    from aadhi.providers.openai_common import classify_openai_error

    from .fakes import openai_status_error

    err = openai_status_error(openai.RateLimitError, 429, "slow down", headers={"retry-after": "75"})
    assert classify_openai_error(err) == ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=75.0)
    quota = openai_status_error(openai.RateLimitError, 429, "billing", code="insufficient_quota")
    assert not classify_openai_error(quota).retry


# --- review fixes: SDK imports off the event loop --------------------------------------------------


@pytest.mark.asyncio
async def test_import_off_loop_uses_a_worker_thread_once(monkeypatch) -> None:
    import threading

    from aadhi.providers import _lazy

    monkeypatch.setattr(_lazy, "_READY", {})
    threads: list[int] = []
    real = _lazy.importlib.import_module

    def recording(name: str):
        threads.append(threading.get_ident())
        return real(name)

    monkeypatch.setattr(_lazy.importlib, "import_module", recording)
    assert _lazy.imported("json") is None
    first = await _lazy.import_off_loop("json")
    again = await _lazy.import_off_loop("json")
    assert first is again and _lazy.imported("json") is first
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    with pytest.raises(ImportError):
        await _lazy.import_off_loop("definitely_not_a_module_xyz")


@pytest.mark.asyncio
async def test_genai_types_and_transport_errors() -> None:
    import httpx

    from aadhi.providers.gemini_common import _transport_errors, genai_types

    types = await genai_types()
    assert types.__name__ == "google.genai.types" and hasattr(types, "GenerateContentConfig")
    errs = _transport_errors()
    assert httpx.TransportError in errs and ConnectionError in errs
    assert classify_gemini_error(httpx.ConnectError("refused")).retry
