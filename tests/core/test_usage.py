"""Pricing estimates, usage recording, budgets and summaries."""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from aadhi.jobs.base import BudgetExceeded
from aadhi.models import Job, Project, UsageEvent, utcnow
from aadhi.providers.base import Usage
from aadhi.usage import pricing
from aadhi.usage.pricing import DEFAULT_PRICES, estimate_cost, load_prices
from aadhi.usage.service import (
    check_budget,
    record_usage,
    spent_today,
    usage_admin,
    usage_for_project,
    usage_for_user,
    user_daily_budget,
)


@pytest.fixture(autouse=True)
def _fresh_prices():
    pricing.reset_price_cache()
    yield
    pricing.reset_price_cache()


# --- pricing -----------------------------------------------------------------------


def test_llm_pricing_by_family(app_env):
    u = Usage("gemini", "gemini-2.5-flash", "llm", input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost(u, app_env) == pytest.approx(0.30 + 2.50)
    lite = Usage("gemini", "gemini-2.5-flash-lite-preview-06", "llm", input_tokens=1_000_000)
    assert estimate_cost(lite, app_env) == pytest.approx(0.10)  # longest prefix wins
    pro = Usage("gemini", "models/gemini-2.5-pro-002", "vision", input_tokens=2_000_000, output_tokens=100_000)
    assert estimate_cost(pro, app_env) == pytest.approx(2 * 1.25 + 0.1 * 10.0)
    gpt = Usage("openai", "gpt-4o-mini-2024-07-18", "llm", input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost(gpt, app_env) == pytest.approx(0.15 + 0.60)


def test_unknown_models_use_provider_defaults(app_env):
    assert estimate_cost(Usage("openai", "gpt-99", "llm", input_tokens=1_000_000), app_env) == pytest.approx(2.50)
    assert estimate_cost(Usage("mystery", "x", "llm", output_tokens=1_000_000), app_env) == pytest.approx(10.0)
    assert estimate_cost(Usage("fake", "fake-model", "llm", input_tokens=10**9), app_env) == 0.0
    # The fake provider reporting a real model name is priced like that model (realistic dev budgets).
    assert estimate_cost(Usage("fake", "gemini-2.5-flash", "llm", input_tokens=1_000_000), app_env) == pytest.approx(
        0.3
    )


def test_tts_image_video_gif(app_env):
    assert estimate_cost(Usage("edge", "en-IN-NeerjaNeural", "tts", characters=10**7), app_env) == 0.0
    assert estimate_cost(Usage("openai", "tts-1-hd", "tts", characters=1_000_000), app_env) == pytest.approx(30.0)
    assert estimate_cost(Usage("openai", "tts-1", "tts", characters=1_000_000), app_env) == pytest.approx(15.0)
    assert estimate_cost(Usage("elevenlabs", "eleven_multilingual_v2", "tts", characters=1000), app_env) == (
        pytest.approx(0.3)
    )
    assert estimate_cost(Usage("gemini", "gemini-2.5-flash-image", "image", units=3), app_env) == pytest.approx(0.117)
    assert estimate_cost(Usage("gemini", "gemini-2.5-flash-image", "image"), app_env) == pytest.approx(0.039)
    assert estimate_cost(Usage("pollinations", "flux", "image", units=5), app_env) == 0.0
    assert estimate_cost(Usage("veo", "veo-2.0-generate-001", "video", seconds=8), app_env) == pytest.approx(4.0)
    assert estimate_cost(Usage("giphy", "", "gif", units=1), app_env) == 0.0
    assert estimate_cost(Usage("x", "y", "telepathy", units=1), app_env) == 0.0


def test_negative_inputs_never_negative(app_env):
    u = Usage("gemini", "gemini-2.5-pro", "llm", input_tokens=-5_000_000, output_tokens=-1)
    assert estimate_cost(u, app_env) == 0.0


def test_pricing_file_override_and_reload(app_env, tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"llm": {"gemini-2.5-flash": {"input": 1.0, "output": 1.0}}}), encoding="utf-8")
    s = app_env.model_copy(update={"pricing_file": path})
    u = Usage("gemini", "gemini-2.5-flash", "llm", input_tokens=1_000_000)
    assert estimate_cost(u, s) == pytest.approx(1.0)
    assert load_prices(s)["llm"]["gemini-2.5-pro"] == DEFAULT_PRICES["llm"]["gemini-2.5-pro"]  # deep merge
    path.write_text(json.dumps({"llm": {"gemini-2.5-flash": {"input": 2.0, "output": 1.0}}}), encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    assert estimate_cost(u, s) == pytest.approx(2.0)
    assert DEFAULT_PRICES["llm"]["gemini-2.5-flash"]["input"] == 0.30  # defaults never mutated


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_invalid_pricing_file_falls_back(app_env, tmp_path, content):
    path = tmp_path / "prices.json"
    path.write_text(content, encoding="utf-8")
    s = app_env.model_copy(update={"pricing_file": path})
    assert load_prices(s) is DEFAULT_PRICES
    missing = app_env.model_copy(update={"pricing_file": tmp_path / "nope.json"})
    assert load_prices(missing) is DEFAULT_PRICES


def test_fake_job_context_uses_pricing(job_ctx):
    cost = job_ctx.record_usage(Usage("gemini", "gemini-2.5-pro", "llm", input_tokens=1_000_000))
    assert cost == pytest.approx(1.25)


# --- service -----------------------------------------------------------------------


def _event(db, user_id, cost, *, when=None, project_id=None, job_id=None, op="llm", provider="gemini", model="m"):
    ev = record_usage(
        db,
        Usage(provider, model, op, input_tokens=10),
        user_id=user_id,
        project_id=project_id,
        job_id=job_id,
        cost_usd=cost,
    )
    if when is not None:
        ev.created_at = when
    db.commit()
    return ev


def test_record_usage_and_spent_today(app_env, db_session, make_user):
    user = make_user("alice")
    ev = record_usage(
        db_session,
        Usage("gemini", "gemini-2.5-flash", "llm", input_tokens=5, output_tokens=7, meta={"stage": "plan"}),
        user_id=user.id,
        project_id=None,
        job_id=None,
        cost_usd=0.25,
    )
    db_session.commit()
    assert ev.id and ev.meta == {"stage": "plan"} and ev.output_tokens == 7
    _event(db_session, user.id, 1.0, when=utcnow() - dt.timedelta(days=2))
    _event(db_session, user.id, -3.0)  # negative costs are clamped
    assert spent_today(db_session, user.id) == pytest.approx(0.25)


def test_personal_key_spend_is_recorded_but_not_budgeted(app_env, db_session, make_user):
    user = make_user("alice", daily_budget_usd=1.0)
    own = record_usage(db_session, Usage("anthropic", "claude", "llm", input_tokens=5), user_id=user.id,
                       project_id=None, job_id=None, cost_usd=40.0, billed_to="user")
    odd = record_usage(db_session, Usage("gemini", "m", "llm"), user_id=user.id, project_id=None, job_id=None,
                       cost_usd=0.0, billed_to="somebody")
    db_session.commit()
    assert (own.billed_to, odd.billed_to) == ("user", "server")
    _event(db_session, user.id, 0.5)
    assert spent_today(db_session, user.id) == pytest.approx(0.5)  # the 40 USD on the user's own key are ignored
    check_budget(db_session, user_id=user.id, job_cost_so_far=0, extra_usd=0.5, settings=app_env)
    data = usage_for_user(db_session, user, 7, app_env)
    assert data["total_usd"] == pytest.approx(40.5) and data["own_key_usd"] == pytest.approx(40.0)
    assert data["today_usd"] == pytest.approx(0.5)
    admin = usage_admin(db_session, 7)
    assert admin["total_usd"] == pytest.approx(40.5) and admin["own_key_usd"] == pytest.approx(40.0)
    row = next(u for u in admin["users"] if u["username"] == "alice")
    assert row["own_key_usd"] == pytest.approx(40.0) and row["total_usd"] == pytest.approx(40.5)
    assert row["today_usd"] == pytest.approx(0.5)  # own-key spend is not counted toward the daily budget


def test_user_daily_budget(app_env, make_user):
    assert user_daily_budget(None, app_env) == app_env.daily_budget_usd_per_user
    assert user_daily_budget(make_user("a", daily_budget_usd=2.5), app_env) == 2.5
    assert user_daily_budget(make_user("b", daily_budget_usd=None), app_env) == 15.0


def test_check_budget_job_limit(app_env, db_session):
    check_budget(db_session, user_id=None, job_cost_so_far=4.0, extra_usd=1.0, settings=app_env)
    with pytest.raises(BudgetExceeded, match="budget"):
        check_budget(db_session, user_id=None, job_cost_so_far=4.0, extra_usd=1.01, settings=app_env)
    with pytest.raises(BudgetExceeded):
        check_budget(db_session, user_id=None, job_cost_so_far=0, extra_usd=0.6, settings=app_env, job_budget_usd=0.5)


def test_check_budget_user_daily(app_env, db_session, make_user):
    user = make_user("alice", daily_budget_usd=1.0)
    _event(db_session, user.id, 0.9)
    _event(db_session, user.id, 5.0, when=utcnow() - dt.timedelta(days=1, hours=1))  # yesterday: ignored
    check_budget(db_session, user_id=user.id, job_cost_so_far=0, extra_usd=0.1, settings=app_env)
    with pytest.raises(BudgetExceeded, match="Daily budget"):
        check_budget(db_session, user_id=user.id, job_cost_so_far=0, extra_usd=0.2, settings=app_env)


def test_usage_for_user(app_env, db_session, make_user):
    user = make_user("alice")
    other = make_user("bob")
    _event(db_session, user.id, 0.5, op="llm", provider="gemini", model="gemini-2.5-pro")
    _event(db_session, user.id, 0.25, op="tts", provider="openai", model="tts-1")
    _event(
        db_session,
        user.id,
        1.0,
        op="llm",
        provider="gemini",
        model="gemini-2.5-pro",
        when=utcnow() - dt.timedelta(days=3),
    )
    _event(db_session, user.id, 9.0, when=utcnow() - dt.timedelta(days=40))  # outside the window
    _event(db_session, other.id, 7.0)
    data = usage_for_user(db_session, user, 30, app_env)
    assert data["total_usd"] == pytest.approx(1.75) and data["own_key_usd"] == 0.0
    assert data["today_usd"] == pytest.approx(0.75)
    assert data["daily_budget_usd"] == 15.0
    assert [d["usd"] for d in data["by_day"]] == [pytest.approx(1.0), pytest.approx(0.75)]
    assert all(len(d["date"]) == 10 for d in data["by_day"])
    assert data["by_operation"][0] == {"operation": "llm", "usd": pytest.approx(1.5)}
    top = data["by_provider"][0]
    assert top["provider"] == "gemini" and top["model"] == "gemini-2.5-pro" and top["calls"] == 2
    assert usage_for_user(db_session, user, 1, app_env)["total_usd"] == pytest.approx(0.75)


def test_usage_for_project(app_env, db_session, make_user):
    user = make_user("alice")
    project = Project(owner_id=user.id, title="P")
    db_session.add(project)
    db_session.flush()
    job = Job(kind="generate_lecture", max_attempts=2, project_id=project.id, user_id=user.id)
    db_session.add(job)
    db_session.commit()
    _event(db_session, user.id, 0.5, project_id=project.id, job_id=job.id)
    _event(db_session, user.id, 0.25, project_id=project.id, job_id=job.id, op="tts")
    _event(db_session, user.id, 0.1, project_id=project.id)  # no job
    _event(db_session, user.id, 3.0)  # other project
    data = usage_for_project(db_session, project.id)
    assert data["total_usd"] == pytest.approx(0.85)
    by_job = {row["job_id"]: row for row in data["by_job"]}
    assert by_job[job.id]["kind"] == "generate_lecture" and by_job[job.id]["usd"] == pytest.approx(0.75)
    assert by_job[job.id]["created_at"].endswith("+00:00")
    assert by_job[None]["usd"] == pytest.approx(0.1)
    assert {r["operation"] for r in data["by_operation"]} == {"llm", "tts"}


def test_usage_admin(app_env, db_session, make_user):
    alice, bob, carol = make_user("alice"), make_user("bob"), make_user("carol")
    _event(db_session, alice.id, 2.0)
    _event(db_session, alice.id, 1.0, when=utcnow() - dt.timedelta(days=2))
    _event(db_session, bob.id, 0.5)
    _event(db_session, None, 4.0)  # system usage (no user)
    data = usage_admin(db_session, 30)
    rows = {r["username"]: r for r in data["users"]}
    assert rows["alice"]["total_usd"] == pytest.approx(3.0) and rows["alice"]["today_usd"] == pytest.approx(2.0)
    assert rows["bob"]["total_usd"] == pytest.approx(0.5)
    assert rows["carol"]["total_usd"] == 0.0 and rows["carol"]["user_id"] == carol.id
    assert data["users"][0]["username"] == "alice"
    assert data["total_usd"] == pytest.approx(7.5)
    assert db_session.query(UsageEvent).count() == 4
