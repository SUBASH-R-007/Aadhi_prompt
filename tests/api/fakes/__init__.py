"""Fallback implementations of other areas' modules for the API tests.

The API imports other areas only through the names in docs/INTERFACES.md. While those areas were
written in parallel some modules did not exist yet; ``install()`` registers a minimal,
interface-faithful fake ONLY for a module that is *missing* (``ModuleNotFoundError`` naming that
module or one of its packages). A module that exists but fails to import is NOT replaced: the
error surfaces, so integration breakage is never masked. The pytest header lists any fakes used.

``AADHI_API_TEST_FAKES=all`` forces every fake (checks that the API relies only on the documented
interfaces); ``=none`` disables the fallback entirely.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

# (real module, fake module) in dependency order: fakes import other areas by their real names.
FAKES: tuple[tuple[str, str], ...] = (
    ("aadhi.auth.passwords", "tests.api.fakes.auth_passwords"),
    ("aadhi.auth.tokens", "tests.api.fakes.auth_tokens"),
    ("aadhi.auth.cookies", "tests.api.fakes.auth_cookies"),
    ("aadhi.auth.deps", "tests.api.fakes.auth_deps"),
    ("aadhi.auth.bootstrap", "tests.api.fakes.auth_bootstrap"),
    ("aadhi.security.client_ip", "tests.api.fakes.security_client_ip"),
    ("aadhi.security.headers", "tests.api.fakes.security_headers"),
    ("aadhi.security.csrf", "tests.api.fakes.security_csrf"),
    ("aadhi.security.bodylimit", "tests.api.fakes.security_bodylimit"),
    ("aadhi.security.ratelimit", "tests.api.fakes.security_ratelimit"),
    ("aadhi.security.uploads", "tests.api.fakes.security_uploads"),
    ("aadhi.usage.service", "tests.api.fakes.usage_service"),
    ("aadhi.legacy", "tests.api.fakes.legacy"),
    ("aadhi.jobs.queue", "tests.api.fakes.jobs_queue"),
    ("aadhi.jobs.events", "tests.api.fakes.jobs_events"),
    ("aadhi.pipeline.validate", "tests.api.fakes.pipeline_validate"),
    ("aadhi.pipeline.assets", "tests.api.fakes.pipeline_assets"),
    ("aadhi.pipeline.plan_state", "tests.api.fakes.pipeline_plan_state"),
    ("aadhi.pipeline.companion", "tests.api.fakes.pipeline_companion"),
    ("aadhi.compose.timeline", "tests.api.fakes.compose_timeline"),
    ("aadhi.compose.chapters", "tests.api.fakes.compose_chapters"),
)

INSTALLED: dict[str, str] = {}


def _missing(real: str, exc: ModuleNotFoundError) -> bool:
    """True when the error is about ``real`` itself (or a package on its path), not a dependency."""
    name = exc.name or ""
    return bool(name) and (real == name or real.startswith(name + "."))


def _ensure_package(name: str) -> types.ModuleType:
    """Import ``name`` or register an empty placeholder package for it."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if not _missing(name, exc):
            raise
        pkg = types.ModuleType(name)
        pkg.__path__ = []  # type: ignore[attr-defined]
        pkg.__doc__ = "placeholder package registered by tests/api/fakes"
        sys.modules[name] = pkg
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(_ensure_package(parent), child, pkg)
        return pkg


def _register(real: str, fake: types.ModuleType) -> None:
    parent, _, child = real.rpartition(".")
    sys.modules[real] = fake
    if parent:
        setattr(_ensure_package(parent), child, fake)


def install() -> dict[str, str]:
    """Register fakes for every missing module; returns {module: reason}."""
    mode = os.environ.get("AADHI_API_TEST_FAKES", "missing").strip().lower()
    if mode == "none":
        return {}
    for real, fake_name in FAKES:
        if real in INSTALLED:
            continue
        if mode != "all":
            try:
                importlib.import_module(real)
                continue
            except ModuleNotFoundError as exc:
                if not _missing(real, exc):
                    raise
                reason = f"missing ({exc.name})"
        else:
            reason = "forced by AADHI_API_TEST_FAKES=all"
        sys.modules.pop(real, None)
        _register(real, importlib.import_module(fake_name))
        INSTALLED[real] = reason
    return dict(INSTALLED)
