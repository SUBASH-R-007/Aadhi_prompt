"""App factory: middleware order, docs, lifespan (tables / Alembic gate / admin / inline workers)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from aadhi.models import User


def test_middleware_order_security_headers_outermost(app_env):
    from starlette.middleware.cors import CORSMiddleware

    from aadhi.api.errors import UnhandledErrorMiddleware
    from aadhi.main import create_app
    from aadhi.security.bodylimit import BodySizeLimitMiddleware
    from aadhi.security.csrf import CSRFMiddleware
    from aadhi.security.headers import SecurityHeadersMiddleware

    plain = [m.cls for m in create_app(app_env).user_middleware]
    assert plain == [SecurityHeadersMiddleware, CSRFMiddleware, BodySizeLimitMiddleware, UnhandledErrorMiddleware]
    cors = create_app(app_env.model_copy(update={"cors_origins": ["https://a.example.com"]}))
    assert [m.cls for m in cors.user_middleware] == [
        SecurityHeadersMiddleware,
        CORSMiddleware,
        CSRFMiddleware,
        BodySizeLimitMiddleware,
        UnhandledErrorMiddleware,
    ]
    cors_kwargs = cors.user_middleware[1].kwargs
    assert cors_kwargs["allow_credentials"] is True
    assert cors_kwargs["allow_origins"] == ["https://a.example.com"]
    assert set(cors_kwargs["allow_headers"]) == {"X-Aadhi-CSRF", "Content-Type", "Authorization"}


def test_lifespan_creates_tables_and_bootstraps_admin(app_env):
    from aadhi.db import Base, get_engine, get_sessionmaker
    from aadhi.main import create_app

    Base.metadata.drop_all(get_engine())
    with TestClient(create_app(app_env)) as c:
        assert c.get("/healthz").status_code == 200
    with get_sessionmaker()() as db:
        assert db.execute(select(User).where(User.role == "admin")).scalars().first() is not None


def test_docs_only_outside_production(app_env):
    from aadhi.main import create_app

    with TestClient(create_app(app_env)) as c:
        assert c.get("/api/openapi.json").status_code == 200
        assert c.get("/api/docs").status_code == 200


def test_api_docs_page_carries_its_own_csp(app_env):
    """The app CSP (script-src 'self') would block Swagger UI's CDN bundle and inline bootstrap."""
    import base64
    import hashlib
    import re

    from aadhi.main import SWAGGER_CDN, create_app
    from aadhi.security.headers import build_csp

    with TestClient(create_app(app_env)) as c:
        r = c.get("/api/docs")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        csp = r.headers["content-security-policy"]
        directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
        script_src = directives["script-src"].split()
        assert SWAGGER_CDN in script_src and "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
        inline = re.findall(r"<script>(.*?)</script>", r.text, re.DOTALL)
        assert inline, "Swagger UI bootstraps with an inline script"
        for body in inline:  # each inline script is allowed by its exact hash, nothing else
            digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
            assert f"'sha256-{digest}'" in script_src
        for src in re.findall(r'<script src="([^"]+)"', r.text):
            assert src.startswith(SWAGGER_CDN + "/")
        assert directives["connect-src"] == "'self'" and directives["frame-ancestors"] == "'none'"
        assert "fastapi.tiangolo.com" not in r.text  # no third-party favicon request
        # Every other response keeps the app policy.
        assert c.get("/healthz").headers["content-security-policy"] == build_csp(app_env, "app")
        assert c.get("/api/openapi.json").headers["content-security-policy"] == build_csp(app_env, "app")


def test_prepare_database_creates_tables_only_for_development_sqlite(app_env, monkeypatch):
    from pydantic import SecretStr

    from aadhi import main
    from aadhi.db import Base

    calls: list[str] = []
    monkeypatch.setattr(main, "check_migrations", lambda engine, heads=None: calls.append("check_migrations"))
    monkeypatch.setattr(Base.metadata, "create_all", lambda *a, **kw: calls.append("create_all"))
    main.prepare_database(app_env)
    assert calls == ["create_all"]
    calls.clear()
    postgres = app_env.model_copy(update={"database_url": SecretStr("postgresql://aadhi:pw@db:5432/aadhi")})
    assert not postgres.is_sqlite and not postgres.is_production
    main.prepare_database(postgres)  # an Alembic-managed dev database: never create_all
    assert calls == ["check_migrations"]


def test_prepare_database_adds_columns_missing_from_an_older_development_db(app_env):
    from sqlalchemy import inspect

    from aadhi import main
    from aadhi.db import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE usage_events DROP COLUMN billed_to")
    main.prepare_database(app_env)
    columns = {c["name"]: c for c in inspect(engine).get_columns("usage_events")}
    assert "billed_to" in columns and columns["billed_to"]["nullable"] is False
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO usage_events (provider, model, operation, input_tokens, output_tokens, characters, seconds, "
            "units, cost_usd, meta, created_at) VALUES ('x', '', 'llm', 0, 0, 0, 0, 0, 0, '{}', '2026-10-04')"
        )
        assert conn.exec_driver_sql("SELECT billed_to FROM usage_events").scalar_one() == "server"


def _production(app_env, tmp_path, **extra):
    return app_env.model_copy(
        update={
            "app_env": "production",
            "base_url": "https://lectures.example.edu",
            "manim_allow_freeform": False,
            "storage_local_dir": tmp_path / "storage",
            "worker_mode": "external",
            "allow_fake_providers": True,  # the test environment runs the offline stand-in providers
            **extra,
        }
    )


def test_production_refuses_unsafe_configuration(app_env, tmp_path):
    from aadhi.main import create_app

    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        create_app(_production(app_env, tmp_path, base_url="http://insecure.example.edu"))


def test_production_disables_api_docs(app_env, tmp_path):
    from aadhi.main import create_app

    app = create_app(_production(app_env, tmp_path))
    assert app.openapi_url is None and app.docs_url is None
    c = TestClient(app)  # no lifespan: only routing is exercised
    assert c.get("/api/openapi.json").status_code == 404
    assert c.get("/api/docs").status_code == 404


def test_production_lifespan_refuses_unmigrated_database(app_env, tmp_path, monkeypatch):
    from aadhi import main

    monkeypatch.setattr(main, "alembic_heads", lambda root=None: {"head1"})
    app = main.create_app(_production(app_env, tmp_path))
    with pytest.raises(RuntimeError, match="alembic"):
        with TestClient(app):
            pass


def test_check_migrations(tmp_path):
    from aadhi.main import check_migrations

    engine = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        check_migrations(engine, heads={"abc"})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    with pytest.raises(RuntimeError, match="empty alembic_version"):
        check_migrations(engine, heads={"abc"})
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO alembic_version VALUES ('old')"))
    with pytest.raises(RuntimeError, match="not the migration head"):
        check_migrations(engine, heads={"abc"})
    check_migrations(engine, heads={"old"})
    check_migrations(engine, heads=set())  # no migration scripts: presence is enough
    engine.dispose()


def _real_inline():
    from tests.api import fakes

    if "aadhi.jobs.queue" in fakes.INSTALLED:
        pytest.skip("aadhi.jobs.inline needs the real job queue")
    import aadhi.jobs.inline as inline

    return inline


def test_inline_workers_start_and_stop_with_the_app(app_env, monkeypatch):
    inline = _real_inline()
    from aadhi.main import create_app

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        inline, "start_inline_workers", lambda settings: calls.append(("start", settings.worker_mode)) or "H"
    )
    monkeypatch.setattr(inline, "stop_inline_workers", lambda handle: calls.append(("stop", handle)))
    with TestClient(create_app(app_env.model_copy(update={"worker_mode": "inline"}))):
        assert calls == [("start", "inline")]
    assert calls == [("start", "inline"), ("stop", "H")]


def test_external_worker_mode_starts_nothing(app_env, monkeypatch):
    inline = _real_inline()
    from aadhi.main import create_app

    monkeypatch.setattr(inline, "start_inline_workers", lambda settings: pytest.fail("must not start"))
    with TestClient(create_app(app_env)):
        pass


def test_module_level_app_is_lazy_and_cached(app_env):
    import aadhi.main as main

    assert main.app is main.app
    with pytest.raises(AttributeError):
        main.not_a_thing  # noqa: B018


def test_server_shim_reexports_the_app(app_env):
    import aadhi.main as main
    import server

    assert server.app is main.app


def test_server_main_runs_uvicorn_with_the_app_logging(app_env, monkeypatch):
    import runpy

    import uvicorn

    from aadhi.config import ROOT_DIR

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8765")
    runpy.run_path(str(ROOT_DIR / "server.py"), run_name="__main__")
    # log_config=None: uvicorn keeps the app's redacting handler instead of installing its own.
    assert calls == [(("aadhi.main:app",), {"host": "127.0.0.1", "port": 8765, "log_config": None})]
