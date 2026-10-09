"""Jobs run with their owner's API keys: personal > server key saved in the Studio > environment.

Covers the provider actually built for a job, ``billed_to`` on usage rows, the daily budget ignoring
spend on a personal key (the job budget still counts it), redaction of decrypted keys in job events
and the ``personal_key_rejected`` outcome. No network: SDK clients are fakes.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from aadhi.credentials import save_credential
from aadhi.db import session_scope
from aadhi.jobs import context as context_mod
from aadhi.jobs.base import BudgetExceeded, FatalJobError, job_handler
from aadhi.jobs.context import DBJobContext
from aadhi.jobs.lease import Lease
from aadhi.jobs.queue import claim_next
from aadhi.models import UsageEvent, User, utcnow
from aadhi.providers.base import ProviderError, Usage
from aadhi.security.redaction import clear_registered_secrets

from ._helpers import add_job, get_job, job_events, make_user, wait_for

PERSONAL = "sk-ant-api03-job-personal-key-AAAAA-1111"
SERVER = "sk-ant-api03-job-server-key-BBBBBBBB-2222"
ENV = "sk-ant-api03-job-environment-key-CCC-3333"


@pytest.fixture()
def keys_env(app_env):
    app_env.stored_api_keys_enabled = True
    app_env.user_api_keys_enabled = True
    app_env.anthropic_api_key = SecretStr(ENV)
    return app_env


def save_key(user_id: int, api_key: str, *, scope: str = "user", provider: str = "anthropic") -> None:
    with session_scope() as db:
        save_credential(db, provider=provider, api_key=api_key, scope=scope, user=db.get(User, user_id))


def claimed_ctx(settings, *, user_id: int | None, payload: dict | None = None) -> DBJobContext:
    add_job("t_keys", user_id=user_id, payload=payload or {})
    with session_scope() as db:
        job_id, attempt = claim_next(db, "w1", ["t_keys"])
    return DBJobContext.open(Lease(job_id, "w1", attempt), settings, flush_interval=0.05)


def usage(provider: str) -> Usage:
    return Usage(provider=provider, model="m", operation="llm", input_tokens=10)


def test_open_resolves_the_job_owners_keys(keys_env):
    alice, bob, admin = make_user("alice"), make_user("bob"), make_user("root", role="admin")
    save_key(alice, PERSONAL)
    save_key(admin, SERVER, scope="server")
    a = claimed_ctx(keys_env, user_id=alice)
    b = claimed_ctx(keys_env, user_id=bob)
    system = claimed_ctx(keys_env, user_id=None)
    assert a.settings.anthropic_api_key.get_secret_value() == PERSONAL and a.key_sources["anthropic"] == "personal"
    assert b.settings.anthropic_api_key.get_secret_value() == SERVER and b.key_sources["anthropic"] == "server"
    assert system.key_sources["anthropic"] == "server"
    assert a.settings.max_cost_per_lecture_usd == keys_env.max_cost_per_lecture_usd  # everything else unchanged
    a.flush_sync()
    notes = [e for e in job_events(a.job_id) if "own API key" in e.message]
    assert len(notes) == 1 and "Anthropic Claude" in notes[0].message and PERSONAL not in notes[0].message
    keys_env.stored_api_keys_enabled = False
    off = claimed_ctx(keys_env, user_id=alice)
    assert off.settings is keys_env and off.key_sources["anthropic"] == "env"


def test_each_job_builds_its_provider_with_its_owners_key(keys_env, make_worker, monkeypatch):
    """A job started by a user with a personal key calls Claude with that key; another user's job
    (no personal key) uses the server key saved by an admin."""
    import anthropic

    from aadhi.pipeline import integrations

    built: list[str] = []
    lock = threading.Lock()

    class FakeAsyncAnthropic:
        def __init__(self, *, api_key, timeout, max_retries):
            with lock:
                built.append(api_key)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAsyncAnthropic)
    seen: dict[int, str] = {}

    @job_handler("t_keys")
    async def handler(ctx):
        llm = integrations.get_llm(ctx.settings, "anthropic")
        assert llm.name == "anthropic"
        await llm._caller.client()  # builds the SDK client exactly as a real call would
        seen[ctx.job_id] = ctx.settings.anthropic_api_key.get_secret_value()

    alice, bob, admin = make_user("alice"), make_user("bob"), make_user("root", role="admin")
    save_key(alice, PERSONAL)
    save_key(admin, SERVER, scope="server")
    ja, jb = add_job("t_keys", user_id=alice), add_job("t_keys", user_id=bob)
    worker = make_worker(["t_keys"])
    worker.start()
    wait_for(lambda: get_job(ja).status == "succeeded" and get_job(jb).status == "succeeded")
    assert seen == {ja: PERSONAL, jb: SERVER}
    assert sorted(built) == sorted([PERSONAL, SERVER])


def test_usage_is_billed_to_the_paying_key(keys_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.5)
    alice = make_user("alice")
    save_key(alice, PERSONAL)
    ctx = claimed_ctx(keys_env, user_id=alice, payload={"budget_usd": 100})
    ctx.record_usage(usage("anthropic"))  # personal key
    ctx.record_usage(usage("edge"))  # free voice: the server
    ctx.record_usage(usage("openai"))  # no personal OpenAI key
    assert ctx.flush_sync()
    with session_scope() as db:
        rows = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id).order_by(UsageEvent.id)).scalars()
        assert [(r.provider, r.billed_to) for r in rows] == [("anthropic", "user"), ("edge", "server"),
                                                             ("openai", "server")]
    assert get_job(ctx.job_id).cost_usd == pytest.approx(1.5)  # the job's cost includes everything


def test_daily_budget_ignores_personal_key_spend_but_the_job_budget_counts_it(keys_env, monkeypatch):
    alice = make_user("alice", daily_budget_usd=1.0)
    save_key(alice, PERSONAL)
    with session_scope() as db:  # earlier today: 0.9 on the server, 50 on alice's own key
        db.add(UsageEvent(user_id=alice, provider="gemini", model="m", operation="llm", cost_usd=0.9,
                          created_at=utcnow()))
        db.add(UsageEvent(user_id=alice, provider="anthropic", model="m", operation="llm", cost_usd=50.0,
                          billed_to="user", created_at=utcnow()))
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.4)
    ctx = claimed_ctx(keys_env, user_id=alice, payload={"budget_usd": 1.0})
    assert ctx.user_budget_usd == pytest.approx(1.0)
    ctx.record_usage(usage("anthropic"))  # 0.4 on her key: the daily budget (0.9 of 1.0) is not touched
    ctx.record_usage(usage("anthropic"))
    with pytest.raises(BudgetExceeded) as job_limit:  # 1.2 > the job budget of 1.0
        ctx.record_usage(usage("anthropic"))
    assert "Job cost" in str(job_limit.value)

    other = claimed_ctx(keys_env, user_id=alice, payload={"budget_usd": 100})
    with pytest.raises(BudgetExceeded) as daily:  # 0.9 + 0.4 server-billed > 1.0
        other.record_usage(usage("openai"))
    assert "Daily budget" in str(daily.value)


def test_budget_precheck_of_a_personal_gemini_key_ignores_the_daily_budget(keys_env):
    """An AI video (Veo) estimate paid with the user's own Gemini key is checked against the job
    budget only; the same estimate on the server key still needs daily budget."""
    alice, bob = make_user("alice", daily_budget_usd=1.0), make_user("bob", daily_budget_usd=1.0)
    save_key(alice, "AIzaSy-personal-gemini-key-DDDDDDDD-4444", provider="gemini")
    with session_scope() as db:  # both already spent 0.9 of their 1.0 on the server today
        for uid in (alice, bob):
            db.add(UsageEvent(user_id=uid, provider="openai", model="m", operation="llm", cost_usd=0.9,
                              created_at=utcnow()))
    own = claimed_ctx(keys_env, user_id=alice, payload={"budget_usd": 5.0})
    assert own.key_sources["gemini"] == "personal"
    own.ensure_budget(2.0, provider="veo")  # Veo is paid by the Gemini key: outside the daily budget
    with pytest.raises(BudgetExceeded) as daily:
        own.ensure_budget(2.0)  # no provider given: counted as server spend
    assert "Daily budget" in str(daily.value)
    with pytest.raises(BudgetExceeded) as job_limit:
        own.ensure_budget(5.5, provider="veo")  # the job budget still applies
    assert "Job cost" in str(job_limit.value)
    server = claimed_ctx(keys_env, user_id=bob, payload={"budget_usd": 5.0})
    with pytest.raises(BudgetExceeded):
        server.ensure_budget(2.0, provider="veo")


def test_over_the_daily_budget_own_key_and_free_usage_still_run(keys_env, monkeypatch):
    """Once the server-billed spend is over the daily budget (the normal state after hitting it: the
    check runs after the spend is recorded), usage on the user's own keys and free usage still pass;
    only a paid, server-billed increment trips the daily limit. The job budget still counts everything."""
    alice = make_user("alice", daily_budget_usd=1.0)
    save_key(alice, PERSONAL)
    save_key(alice, "AIzaSy-personal-gemini-key-DDDDDDDD-4444", provider="gemini")
    with session_scope() as db:  # the last server-billed call of the day went over the limit
        db.add(UsageEvent(user_id=alice, provider="openai", model="m", operation="llm", cost_usd=1.2,
                          created_at=utcnow()))
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.0 if u.provider == "edge" else 0.4)
    ctx = claimed_ctx(keys_env, user_id=alice, payload={"budget_usd": 1.0})
    ctx.record_usage(usage("anthropic"))  # on her own Claude key
    ctx.record_usage(usage("edge"))  # a free voice
    ctx.ensure_budget(0.5, provider="veo")  # an AI video on her own Gemini key
    ctx.ensure_budget(0.0)  # nothing more to spend
    assert ctx.budget_error is None
    with pytest.raises(BudgetExceeded) as daily:
        ctx.record_usage(usage("openai"))  # paid and server-billed: the daily limit applies
    assert "Daily budget" in str(daily.value)
    with pytest.raises(BudgetExceeded) as job_limit:
        ctx.record_usage(usage("anthropic"))  # 0.4 + 0.4 + 0.4 > the job budget of 1.0
    assert "Job cost" in str(job_limit.value)


def test_job_events_scrub_the_jobs_own_keys_without_the_shared_registry(keys_env):
    """Events are redacted with ``ctx.settings`` (the job's resolved keys), whatever the state of the
    process-wide registry (e.g. the key's slot was replaced by a newer save meanwhile)."""
    alice = make_user("alice")
    save_key(alice, PERSONAL)
    ctx = claimed_ctx(keys_env, user_id=alice)
    clear_registered_secrets()
    ctx.log(f"provider said: invalid x-api-key {PERSONAL}", "error", detail=[f"key {PERSONAL}"])
    ctx.progress("script", 0.2, f"retrying with {PERSONAL}")
    assert ctx.flush_sync()
    events = job_events(ctx.job_id)
    assert events and all(PERSONAL not in e.message and PERSONAL not in str(e.data) for e in events)


def test_job_errors_scrub_the_jobs_own_keys_without_the_shared_registry(keys_env, make_worker):
    @job_handler("t_keys")
    async def handler(ctx):
        clear_registered_secrets()  # e.g. the key's slot was replaced by a newer save while the job ran
        raise RuntimeError(f"provider said: invalid x-api-key {PERSONAL}")

    alice = make_user("alice")
    save_key(alice, PERSONAL)
    jid = add_job("t_keys", user_id=alice, max_attempts=1)
    worker = make_worker(["t_keys"])
    worker.start()
    wait_for(lambda: get_job(jid).status == "failed")
    job = get_job(jid)
    assert job.error and PERSONAL not in job.error and "[REDACTED]" in job.error
    assert all(PERSONAL not in e.message and PERSONAL not in str(e.data) for e in job_events(jid))


def test_fatal_job_errors_scrub_the_jobs_own_keys_without_the_shared_registry(keys_env, make_worker):
    @job_handler("t_keys")
    async def handler(ctx):
        clear_registered_secrets()  # e.g. the user deleted or replaced the key while the job ran
        raise FatalJobError(f"Could not read the source: invalid x-api-key {PERSONAL}", code="invalid_source")

    alice = make_user("alice")
    save_key(alice, PERSONAL)
    jid = add_job("t_keys", user_id=alice, max_attempts=1)
    worker = make_worker(["t_keys"])
    worker.start()
    wait_for(lambda: get_job(jid).status == "failed")
    job = get_job(jid)
    assert job.error_code == "invalid_source"
    assert job.error and PERSONAL not in job.error and "[REDACTED]" in job.error
    assert all(PERSONAL not in e.message and PERSONAL not in str(e.data) for e in job_events(jid))


def test_decrypted_keys_are_redacted_from_job_events(keys_env):
    alice = make_user("alice")
    save_key(alice, PERSONAL)
    ctx = claimed_ctx(keys_env, user_id=alice)
    ctx.log(f"provider said: invalid x-api-key {PERSONAL}", "error", detail=f"key {PERSONAL}")
    ctx.progress("script", 0.2, f"retrying with {PERSONAL}")
    assert ctx.flush_sync()
    events = job_events(ctx.job_id)
    assert events and all(PERSONAL not in e.message and PERSONAL not in str(e.data) for e in events)
    assert any("[REDACTED]" in e.message for e in events)


def _rejected(provider: str) -> FatalJobError:
    try:
        raise ProviderError(f"{provider}: request failed (HTTP 401): invalid x-api-key", status=401, provider=provider)
    except ProviderError as inner:
        try:
            raise FatalJobError(f"AI provider error: {inner}", code="provider") from inner
        except FatalJobError as outer:
            return outer


def test_rejected_personal_key_fails_the_job_clearly(keys_env, make_worker):
    @job_handler("t_keys")
    async def handler(ctx):
        await asyncio.sleep(0)
        raise _rejected("anthropic")

    alice, bob = make_user("alice"), make_user("bob")
    save_key(alice, PERSONAL)
    ja, jb = add_job("t_keys", user_id=alice), add_job("t_keys", user_id=bob)
    worker = make_worker(["t_keys"])
    worker.start()
    wait_for(lambda: get_job(ja).status == "failed" and get_job(jb).status == "failed")
    a, b = get_job(ja), get_job(jb)
    assert a.error_code == "personal_key_rejected" and a.attempts == 1  # never retried, never the server key
    assert "personal Anthropic Claude API key was rejected" in a.error
    assert b.error_code == "provider" and "personal" not in b.error  # bob ran on the environment key


def test_rejected_personal_key_is_not_retried_for_unwrapped_errors(keys_env, make_worker):
    @job_handler("t_keys")
    async def handler(ctx):
        raise ProviderError("anthropic: failed (HTTP 403)", status=403, provider="anthropic")

    alice = make_user("alice")
    save_key(alice, PERSONAL)
    jid = add_job("t_keys", user_id=alice)
    worker = make_worker(["t_keys"])
    worker.start()
    wait_for(lambda: get_job(jid).status == "failed")
    job = get_job(jid)
    assert job.error_code == "personal_key_rejected" and job.attempts == 1 and "HTTP 403" in job.error
