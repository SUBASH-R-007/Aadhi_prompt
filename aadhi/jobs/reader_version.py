"""Job refusal of documents written by a newer build (design §6.7, the reader-version marker).

A worker on an older image must never process a version whose screenplay, timeline or asset manifest a newer
build wrote: it would read the document without the fields it does not know and could write the stripped document
back. Two layers keep that from happening:

* ``newer_document_refusal`` runs before the handler (``aadhi.jobs.worker``): it reads only the ``schema_version`` of
  the job's version (and of the version named by ``payload["source_version_id"]``, which ``translate`` reads) with
  one SQL query, and returns the refusal message when one is above ``READER_SCHEMA_VERSION``. The job then fails
  with ``error_code="newer_document"`` without running, so nothing about the version changes.
* Every loader also raises ``aadhi.schemas.features.NewerDocumentError`` (the models check the marker before
  validating); the worker maps it to the same fatal ``newer_document`` failure, never a retry.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProjectVersion
from ..schemas.features import READER_SCHEMA_VERSION, NewerDocumentError

log = logging.getLogger(__name__)

NEWER_DOCUMENT = NewerDocumentError.code  # job error_code

_DOCUMENTS = (
    ("screenplay", ProjectVersion.screenplay),
    ("timeline", ProjectVersion.timeline),
    ("asset manifest", ProjectVersion.asset_manifest),
)


def _version_ids(version_id: int | None, payload: dict[str, Any] | None) -> list[int]:
    ids = [version_id] if isinstance(version_id, int) else []
    source = (payload or {}).get("source_version_id")
    if isinstance(source, int) and not isinstance(source, bool) and source not in ids:
        ids.append(source)
    return ids


def newer_documents(db: Session, version_ids: list[int]) -> list[NewerDocumentError]:
    """The documents of ``version_ids`` whose ``schema_version`` is above what this build reads (one query)."""
    if not version_ids:
        return []
    columns = [column["schema_version"].as_integer() for _, column in _DOCUMENTS]
    rows = db.execute(select(ProjectVersion.id, *columns).where(ProjectVersion.id.in_(version_ids))).all()
    found: list[NewerDocumentError] = []
    for row in rows:
        for (name, _), value in zip(_DOCUMENTS, row[1:], strict=True):
            if isinstance(value, int) and value > READER_SCHEMA_VERSION:
                found.append(NewerDocumentError(name, value))
    return found


def newer_document_refusal(db: Session, version_id: int | None, payload: dict[str, Any] | None) -> str | None:
    """The user-facing refusal when the job would read a document from a newer build, else None.

    Best effort: a database error here is logged and the job runs (the loaders still refuse newer documents)."""
    try:
        found = newer_documents(db, _version_ids(version_id, payload))
    except Exception as exc:  # noqa: BLE001 - the model-level refusal still protects the job
        log.warning("could not check the schema version of version %s: %s", version_id, type(exc).__name__)
        return None
    return str(found[0]) if found else None
