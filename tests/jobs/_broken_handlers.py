"""A handler module whose import fails (a missing optional dependency) — for resolve_kinds tests."""

from __future__ import annotations

import aadhi_jobs_test_missing_dependency  # type: ignore[import-not-found]  # noqa: F401
