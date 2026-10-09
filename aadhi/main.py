"""FastAPI application factory.

``create_app(settings)`` validates the configuration, wires middlewares, exception handlers,
routers and static mounts. ``aadhi.main.app`` is created lazily on first access (PEP 562), so
importing this module has no side effects; ``uvicorn aadhi.main:app`` and
``from aadhi.main import app`` both work.

Middleware order (outermost first)::

    SecurityHeaders -> CORS (only with CORS_ORIGINS) -> CSRF -> BodySizeLimit -> UnhandledError -> routes

so every response — CSRF rejections, 413s and 500s included — carries the security headers and,
for allowed origins, CORS headers (browsers can then read the error envelope).

Lifespan: development/test on SQLite create missing tables (and add missing nullable / defaulted
columns, ``aadhi.dev_schema``); production (and any non-SQLite
database, e.g. a development Postgres) refuses to start unless the database is at the Alembic head,
so a missing migration is noticed in development rather than in production. Then the admin account is bootstrapped and, with
``WORKER_MODE=inline``, worker threads are started (and stopped on shutdown).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.middleware.cors import CORSMiddleware

from . import __version__
from .api.errors import UnhandledErrorMiddleware, install_exception_handlers
from .api.quota import DailyQuota
from .api.routers import (
    admin,
    analytics,
    auth,
    changes,
    exports,
    health,
    jobs,
    keys,
    library,
    meta,
    pages,
    projects,
    render_internal,
    renders,
    shares,
    uploads,
    usage,
    version_actions,
    versions,
    videos,
    visual_review,
)
from .api.routers.media import build_media_router
from .api.static import BrandingStaticFiles, WebStaticFiles
from .config import ROOT_DIR, Settings, get_settings
from .security.bodylimit import BodySizeLimitMiddleware
from .security.csrf import CSRFMiddleware
from .security.headers import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)

CSRF_EXEMPT_PATHS = ("/api/auth/login",)
CORS_ALLOW_HEADERS = ["X-Aadhi-CSRF", "Content-Type", "Authorization"]
CORS_EXPOSE_HEADERS = ["ETag", "Retry-After", "Content-Disposition", "Last-Event-ID"]
CORS_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

DOCS_PATH = "/api/docs"
OPENAPI_PATH = "/api/openapi.json"
# Swagger UI is not vendored: its page loads the bundle from this CDN (development only).
SWAGGER_CDN = "https://cdn.jsdelivr.net"
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)

ROUTERS = (
    health.router,
    auth.router,
    meta.router,
    projects.router,
    versions.router,
    version_actions.router,
    changes.router,
    visual_review.router,
    renders.router,
    videos.router,
    exports.router,
    jobs.router,
    uploads.router,
    library.router,
    shares.router,
    analytics.router,
    usage.router,
    keys.router,
    admin.router,
    render_internal.router,
    pages.router,
)


# --- startup helpers (blocking; run in a thread from the lifespan) -------------------------------


def configure_logging(settings: Settings) -> None:
    """``aadhi.logging_setup.configure_logging`` when available (redaction filter, levels)."""
    try:
        from .logging_setup import configure_logging as configure
    except ImportError:
        logging.basicConfig(level=getattr(logging, str(settings.log_level).upper(), logging.INFO))
        logger.warning("aadhi.logging_setup is unavailable: using basic logging without the redaction filter")
        return
    configure(settings)


def alembic_heads(root: Path = ROOT_DIR) -> set[str]:
    """Head revision(s) of the migration scripts (empty when no ``alembic/`` directory exists)."""
    script_dir = root / "alembic"
    if not script_dir.is_dir():
        return set()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = root / "alembic.ini"
    cfg = Config(str(ini)) if ini.is_file() else Config()
    cfg.set_main_option("script_location", str(script_dir))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def check_migrations(engine: Engine, heads: set[str] | None = None) -> None:
    """Production: the schema must be managed by Alembic and be at the head revision."""
    if not sa_inspect(engine).has_table("alembic_version"):
        raise RuntimeError(
            "Database schema is not initialised by Alembic (no alembic_version table). "
            "Run `alembic upgrade head` before starting the API; production never creates tables."
        )
    with engine.connect() as conn:
        current = {str(row[0]) for row in conn.execute(text("SELECT version_num FROM alembic_version"))}
    if not current:
        raise RuntimeError("Database has an empty alembic_version table. Run `alembic upgrade head`.")
    expected = alembic_heads() if heads is None else heads
    if expected and current != expected:
        raise RuntimeError(
            f"Database schema revision {sorted(current)} is not the migration head {sorted(expected)}. "
            "Run `alembic upgrade head` before starting this version of the API."
        )


def prepare_database(settings: Settings) -> None:
    """Create tables for development/test SQLite; otherwise require the Alembic head.

    ``create_all`` on an Alembic-managed database (e.g. a development Postgres) would quietly create
    tables that have no migration yet, hiding the gap until production refuses to start. On
    development SQLite, columns added to existing tables since the database was created are added in
    place when possible (``aadhi.dev_schema.add_missing_columns``): ``create_all`` never alters tables.
    """
    from . import models  # noqa: F401  (registers every table on Base.metadata)
    from .db import Base, get_engine
    from .dev_schema import add_missing_columns

    engine = get_engine()
    if settings.is_production or not settings.is_sqlite:
        check_migrations(engine)
    else:
        Base.metadata.create_all(engine)
        add_missing_columns(engine)


def bootstrap_admin(settings: Settings) -> None:
    """Create the first admin account if none exists (``aadhi.auth.bootstrap.ensure_admin``)."""
    from .auth.bootstrap import ensure_admin
    from .db import session_scope

    with session_scope() as db:
        ensure_admin(db, settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Database preparation, admin bootstrap and inline workers."""
    settings: Settings = app.state.settings
    await asyncio.to_thread(prepare_database, settings)
    await asyncio.to_thread(bootstrap_admin, settings)
    handle: Any = None
    stop: Any = None
    if settings.worker_mode == "inline":
        from .jobs.inline import start_inline_workers, stop_inline_workers

        handle = await asyncio.to_thread(start_inline_workers, settings)
        stop = stop_inline_workers
        logger.info("inline job workers started (%s threads)", settings.worker_concurrency)
    try:
        yield
    finally:
        if stop is not None:
            await asyncio.to_thread(stop, handle)
            logger.info("inline job workers stopped")


# --- factory ---------------------------------------------------------------------------------


def docs_csp(html: str) -> str:
    """CSP for the Swagger UI page: its CDN bundle plus exactly the page's own inline script(s).

    The app CSP (``script-src 'self'``) would block the CDN bundle; the security headers middleware
    never overrides a CSP the endpoint set, so this page carries its own, narrow policy.
    """
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii") + "'"
        for m in _INLINE_SCRIPT.finditer(html)
    )
    return (
        f"default-src 'none'; script-src {SWAGGER_CDN} {hashes}; style-src {SWAGGER_CDN} 'unsafe-inline'; "
        f"img-src 'self' data:; font-src {SWAGGER_CDN} data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def swagger_ui_page() -> HTMLResponse:
    """``GET /api/docs`` (development only): Swagger UI for ``/api/openapi.json``."""
    page = get_swagger_ui_html(
        openapi_url=OPENAPI_PATH,
        title="Aadhi EduEngine - API docs",
        swagger_favicon_url="data:,",
    )
    html = bytes(page.body).decode("utf-8")
    return HTMLResponse(
        html,
        headers={"Content-Security-Policy": docs_csp(html), "Cache-Control": "no-store"},
    )


def _add_middlewares(app: FastAPI, settings: Settings) -> None:
    # add_middleware() wraps: the LAST one added is the OUTERMOST.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings, exempt_paths=CSRF_EXEMPT_PATHS)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=CORS_METHODS,
            allow_headers=CORS_ALLOW_HEADERS,
            expose_headers=CORS_EXPOSE_HEADERS,
            max_age=600,
        )
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application (no I/O happens until the lifespan starts)."""
    settings = settings or get_settings()
    settings.validate_for_runtime()
    configure_logging(settings)
    docs = not settings.is_production
    app = FastAPI(
        title="Aadhi EduEngine",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,  # served by swagger_ui_page (own CSP) below
        redoc_url=None,
        openapi_url=OPENAPI_PATH if docs else None,
    )
    app.state.settings = settings
    app.state.asset_store = None
    app.state.analytics_quota = DailyQuota()
    install_exception_handlers(app)
    if docs:
        app.add_api_route(DOCS_PATH, swagger_ui_page, methods=["GET"], include_in_schema=False)
    _add_middlewares(app, settings)
    for router in ROUTERS:
        app.include_router(router)
    if settings.media_url_prefix.startswith("/"):
        app.include_router(build_media_router(settings.media_url_prefix))
    app.mount("/web", WebStaticFiles(directory=settings.web_dir, check_dir=False), name="web")
    app.mount("/branding", BrandingStaticFiles(directory=settings.branding_dir, check_dir=False), name="branding")
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazily build the module-level ``app`` on first access."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
