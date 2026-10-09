"""EvalJobContext, CostMeter and isolated_workspace."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from sqlalchemy import inspect, select

from aadhi.evals.context import CostMeter, EvalJobContext, load_pricing
from aadhi.evals.workspace import isolated_workspace, workspace_env
from aadhi.jobs.base import BudgetExceeded, JobCancelled, JobContext
from aadhi.providers.base import Usage
from aadhi.storage.assets import Produced, compute_key


@pytest.fixture()
def ctx(app_env, asset_store):
    from aadhi.db import get_sessionmaker

    return EvalJobContext(settings=app_env, assets=asset_store, session_factory=get_sessionmaker())


def _usage(**kw) -> Usage:
    return Usage(provider="fake", model="fake-1", operation="llm", **kw)


def test_implements_job_context_protocol(ctx, asset_store):
    assert isinstance(ctx, JobContext)
    assert ctx.storage is asset_store.storage
    assert ctx.worker_id.startswith("eval:")
    assert ctx.attempt == 1


def test_progress_is_monotonic_clamped_and_redacted(ctx):
    ctx.progress("plan", 0.5, "calling api with key=abc123secret")
    ctx.progress("plan", 0.2)
    ctx.progress("script", 7.0)
    progress = [e["progress"] for e in ctx.events if e["type"] == "progress"]
    assert progress == [0.5, 0.5, 1.0]
    assert "abc123secret" not in ctx.events[0]["message"]
    assert "[REDACTED]" in ctx.events[0]["message"]
    assert all(e["t"] >= 0 for e in ctx.events)


def test_log_normalises_level_redacts_and_echoes(ctx, app_env):
    seen = []
    ctx.echo = seen.append
    secret = app_env.jwt_secret.get_secret_value()
    ctx.set_stage("critic")
    ctx.log(f"leaked {secret}", level="weird", detail=f"token={secret}", n=3)
    event = ctx.events[-1]
    assert event["level"] == "info" and event["stage"] == "critic"
    assert secret not in event["message"] and secret not in event["data"]["detail"]
    assert event["data"]["n"] == 3
    assert seen == [event]


def test_log_data_is_made_json_safe_and_redacted(ctx, app_env):
    import json
    from pathlib import Path

    secret = app_env.jwt_secret.get_secret_value()
    ctx.log("x", pages={3, 1, 2}, pair=(1, "a"), path=Path("a") / "b", deep={"k": [{"s": {f"v {secret}"}}]},
            ok=True, ratio=0.5, none=None, obj=object())
    data = ctx.events[-1]["data"]
    json.dumps(data)  # never raises
    assert data["pages"] == [1, 2, 3] and data["pair"] == [1, "a"] and data["path"] == str(Path("a") / "b")
    assert data["ok"] is True and data["ratio"] == 0.5 and data["none"] is None
    assert data["obj"].startswith("<object object")
    assert secret not in json.dumps(data) and "[REDACTED]" in data["deep"]["k"][0]["s"][0]


def test_jsonable_bounds_depth(app_env):
    from aadhi.evals.context import jsonable

    value: list = []
    cur = value
    for _ in range(20):
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    out = jsonable(value, app_env)
    depth = 0
    while isinstance(out, list) and out:
        out, depth = out[0], depth + 1
    assert isinstance(out, str) and depth <= 7


def test_broken_echo_does_not_break_the_pipeline(ctx):
    def boom(_event):
        raise RuntimeError("printer down")

    ctx.echo = boom
    ctx.log("still fine")
    assert ctx.events[-1]["message"] == "still fine"


def test_cancel_and_deadline(ctx):
    ctx.check_cancelled()
    ctx.deadline = time.monotonic() - 1
    with pytest.raises(JobCancelled) as exc:
        ctx.check_cancelled()
    assert exc.value.reason == "timeout"
    ctx.deadline = None
    ctx.cancel()
    with pytest.raises(JobCancelled) as exc:
        ctx.check_cancelled()
    assert exc.value.reason == "cancelled"


def test_usage_is_attributed_to_the_current_stage(ctx):
    ctx.set_stage("plan")
    ctx.record_usage(_usage(input_tokens=10))
    ctx.set_stage("script")
    ctx.record_usage(_usage(output_tokens=5))
    assert [r.stage for r in ctx.usage_records] == ["plan", "script"]
    assert ctx.cost_usd >= 0.0


def test_cost_meter_budget_and_estimate(app_env):
    meter = CostMeter(app_env, budget_usd=1.0, price=lambda usage, settings: 0.4)
    assert meter.record(_usage()) == 0.4
    assert meter.record(_usage()) == 0.4
    with pytest.raises(BudgetExceeded):
        meter.ensure(0.5)
    meter.ensure(0.1)
    with pytest.raises(BudgetExceeded):
        meter.record(_usage())
    assert len(meter.records) == 3  # the over-budget call is still accounted
    assert meter.total_usd == pytest.approx(1.2)
    sink = CostMeter(app_env, price=lambda usage, settings: 0.25)
    assert sink(_usage()) == 0.25  # usable directly as a provider on_usage callback


def test_cost_meter_swallows_pricing_errors(app_env):
    def bad_price(usage, settings):
        raise KeyError("unknown model")

    meter = CostMeter(app_env, price=bad_price)
    assert meter.record(_usage()) == 0.0
    unlimited = CostMeter(app_env, price=lambda u, s: 100.0)
    unlimited.ensure(1e9)  # no budget -> never raises
    assert unlimited.record(_usage()) == 100.0


def test_load_pricing_returns_callable_or_none():
    price = load_pricing()
    assert price is None or callable(price)


def test_session_commits_and_rolls_back(ctx):
    from aadhi.models import User

    with ctx.session() as db:
        db.add(User(username="ctx-user", password_hash="x"))
    with pytest.raises(RuntimeError):
        with ctx.session() as db:
            db.add(User(username="ctx-rollback", password_hash="x"))
            raise RuntimeError("boom")
    with ctx.session() as db:
        names = set(db.execute(select(User.username)).scalars())
    assert "ctx-user" in names and "ctx-rollback" not in names


def test_flush_is_a_noop(ctx):
    assert asyncio.run(ctx.flush()) is None


# --- workspace -----------------------------------------------------------------------------------


def test_workspace_env_fake_blanks_every_key(tmp_path):
    env = workspace_env(tmp_path, "fake")
    assert env["LLM_PROVIDER"] == env["TTS_PROVIDER"] == "fake"
    assert env["GEMINI_API_KEY"] == env["OPENAI_API_KEY"] == env["ANTHROPIC_API_KEY"] == env["ELEVENLABS_API_KEY"] == ""
    assert env["MANIM_SANDBOX"] == "disabled"
    assert "LLM_PROVIDER" not in workspace_env(tmp_path, "configured", render_manim=True)
    assert "MANIM_SANDBOX" not in workspace_env(tmp_path, "configured", render_manim=True)


def test_isolated_workspace_overrides_and_restores(app_env, tmp_path, monkeypatch):
    from aadhi import db
    from aadhi.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak-1234")
    get_settings.cache_clear()
    before_data_dir = os.environ["DATA_DIR"]
    root = tmp_path / "ws"
    with isolated_workspace(root, provider="fake") as ws:
        settings = get_settings()
        assert settings is ws.settings
        assert settings.llm_provider == "fake"
        assert settings.openai_api_key.get_secret_value() == ""
        assert settings.manim_sandbox == "disabled"
        assert settings.data_dir == root.resolve() / "data"
        assert settings.resolved_storage_dir == root.resolve() / "storage"
        assert inspect(db.get_engine()).has_table("projects")
        store = ws.asset_store()
        produced = Produced(data=b"{}", mime="application/json")
        asset = store.put(compute_key("intermediate", {"a": 1}), "intermediate", produced)
        assert store.storage.exists(asset.storage_key)
    assert os.environ["DATA_DIR"] == before_data_dir
    assert os.environ["OPENAI_API_KEY"] == "sk-must-not-leak-1234"
    restored = get_settings()
    assert restored.data_dir == app_env.data_dir
    assert (root / "eval.db").exists()  # explicit roots are kept


def test_configured_workspace_keeps_configured_providers(app_env, tmp_path, monkeypatch):
    from aadhi.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with isolated_workspace(tmp_path / "ws", provider="configured", render_manim=True) as ws:
        assert ws.settings.llm_provider == "openai"
        assert ws.settings.manim_sandbox == "subprocess"
    get_settings.cache_clear()


def test_temporary_workspace_is_removed_unless_kept(app_env):
    with isolated_workspace() as ws:
        root = ws.root
        assert root.exists()
    assert not root.exists()
    with isolated_workspace(keep=True) as ws:
        kept = ws.root
    assert kept.exists()
    import shutil

    shutil.rmtree(kept, ignore_errors=True)
