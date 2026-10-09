"""Sliding-window rate limiting (imperative + dependency forms)."""

from __future__ import annotations

import threading
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aadhi.auth.deps import get_current_user
from aadhi.auth.tokens import create_session_token
from aadhi.models import User
from aadhi.security.ratelimit import RateLimited, SlidingWindowLimiter, check_rate_limit, rate_limit

from .conftest import add_error_handler


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_sliding_window_basic():
    clock = Clock()
    lim = SlidingWindowLimiter(clock=clock)
    assert [lim.hit("k", [(3, 60)]) for _ in range(3)] == [None, None, None]
    wait = lim.hit("k", [(3, 60)])
    assert wait == pytest.approx(60.0)
    clock.t += 30
    assert lim.hit("k", [(3, 60)]) == pytest.approx(30.0)  # refusals do not count
    clock.t += 30.01
    assert lim.hit("k", [(3, 60)]) is None
    assert lim.hit("other", [(3, 60)]) is None


def test_sliding_window_is_sliding():
    clock = Clock()
    lim = SlidingWindowLimiter(clock=clock)
    for dt in (0, 20, 40):
        clock.t = 1000 + dt
        assert lim.hit("k", [(3, 60)]) is None
    clock.t = 1061  # first hit left the window, the other two are still inside
    assert lim.hit("k", [(3, 60)]) is None
    assert lim.hit("k", [(3, 60)]) == pytest.approx(19.0)


def test_minute_and_hour_limits():
    clock = Clock()
    lim = SlidingWindowLimiter(clock=clock)
    limits = [(2, 60.0), (3, 3600.0)]
    assert lim.hit("k", limits) is None
    assert lim.hit("k", limits) is None
    assert lim.hit("k", limits) == pytest.approx(60.0)
    clock.t += 61
    assert lim.hit("k", limits) is None
    clock.t += 61
    wait = lim.hit("k", limits)
    assert wait == pytest.approx(3600 - 122, abs=0.01)


def test_no_limits_is_noop():
    lim = SlidingWindowLimiter()
    assert all(lim.hit("k", []) is None for _ in range(100))
    check_rate_limit("b", "k")  # no limits -> no exception


def test_lru_bound():
    lim = SlidingWindowLimiter(max_keys=3)
    for k in "abcd":
        lim.hit(k, [(1, 60)])
    assert len(lim._hits) == 3 and "a" not in lim._hits


def test_thread_safety():
    lim = SlidingWindowLimiter()
    allowed = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if lim.hit("shared", [(100, 60)]) is None:
                with lock:
                    allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(allowed) == 100


def test_check_rate_limit_raises_429():
    for _ in range(2):
        check_rate_limit("login", "alice|1.2.3.4", per_minute=2)
    with pytest.raises(RateLimited) as exc:
        check_rate_limit("login", "alice|1.2.3.4", per_minute=2)
    assert exc.value.status_code == 429 and exc.value.code == "rate_limited"
    assert int(exc.value.headers["Retry-After"]) >= 1
    check_rate_limit("login", "bob|1.2.3.4", per_minute=2)  # other key unaffected
    check_rate_limit("signup", "alice|1.2.3.4", per_minute=2)  # other bucket unaffected


def _app() -> FastAPI:
    app = FastAPI()
    add_error_handler(app)

    @app.get("/ip", dependencies=[Depends(rate_limit("ip_bucket", per_minute=2, key="ip"))])
    def by_ip():
        return {"ok": True}

    @app.get("/user")
    def by_user(
        user: Annotated[User, Depends(get_current_user)],
        _rl: Annotated[None, Depends(rate_limit("user_bucket", per_hour=2, key="user"))],
    ):
        return {"id": user.id}

    @app.get("/default", dependencies=[Depends(rate_limit("default_bucket"))])
    def default():
        return {"ok": True}

    return app


def test_dependency_by_ip(app_env):
    c = TestClient(_app())
    assert [c.get("/ip").status_code for _ in range(3)] == [200, 200, 429]
    r = c.get("/ip")
    assert r.json()["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1


def test_dependency_by_user(app_env, make_user):
    alice, bob = make_user("alice"), make_user("bob")
    c = TestClient(_app())
    ha = {"Authorization": f"Bearer {create_session_token(alice, app_env)}"}
    hb = {"Authorization": f"Bearer {create_session_token(bob, app_env)}"}
    assert [c.get("/user", headers=ha).status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/user", headers=hb).status_code == 200  # same IP, different user


def test_dependency_default_uses_api_limit(app_env, monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "3")
    from aadhi.config import get_settings

    get_settings.cache_clear()
    c = TestClient(_app())
    assert [c.get("/default").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_dependency_uses_app_settings(app_env):
    """Default limits and TRUSTED_PROXIES come from app.state.settings, not the global settings."""
    from aadhi.config import Settings
    from aadhi.security.ratelimit import reset_rate_limits

    app = _app()
    app.state.settings = Settings(app_env="test", api_rate_limit_per_minute=2)
    c = TestClient(app, client=("10.9.9.9", 4321))
    assert [c.get("/default").status_code for _ in range(3)] == [200, 200, 429]
    reset_rate_limits()
    # Untrusted peer: X-Forwarded-For is ignored, every request is the same client.
    codes = [c.get("/ip", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    reset_rate_limits()
    app.state.settings = Settings(app_env="test", trusted_proxies=["10.9.9.0/24"])
    # Trusted peer (per the APP's settings): X-Forwarded-For identifies distinct clients.
    codes = [c.get("/ip", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(5)]
    assert codes == [200] * 5


def test_invalid_key_kind():
    with pytest.raises(ValueError):
        rate_limit("x", key="session")  # type: ignore[arg-type]
