"""v1 importers: ``convert_legacy`` (v1 JSON -> v2 Screenplay) and ``import_legacy_db`` (v1 projects.db).

The ``import_legacy`` job handler lives in ``aadhi.legacy.jobs`` (loaded by ``aadhi.jobs.base``).
"""

from .convert import convert_legacy, is_legacy
from .legacy_db import import_legacy_db

__all__ = ["convert_legacy", "import_legacy_db", "is_legacy"]
