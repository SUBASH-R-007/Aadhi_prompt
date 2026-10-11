"""Workers refuse documents written by a newer build (design §6.7, reader-version marker; B0).

A job whose version (or translation source) holds a screenplay, timeline or manifest with a ``schema_version`` above
``READER_SCHEMA_VERSION`` fails with ``error_code="newer_document"`` before its handler runs; a handler whose loader
meets one fails the same way at once (fatal, never retried).
"""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.jobs.base import job_handler
from aadhi.schemas.features import NewerDocumentError
from aadhi.schemas.screenplay import Screenplay
from tests.jobs._helpers import add_job, get_job, make_project, make_user, make_version, wait_for


def wait_status(job_id: int, *statuses: str, timeout: float = 10.0) -> Any:
    return wait_for(lambda: (j := get_job(job_id)).status in statuses and j, timeout=timeout)


def set_documents(version_id: int, **docs: Any) -> None:
    from aadhi.db import session_scope
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        version = db.get(ProjectVersion, version_id)
        for name, value in docs.items():
            setattr(version, name, value)


def _version(**docs: Any) -> tuple[int, int, int]:
    user = make_user()
    project = make_project(user)
    vid = make_version(project)
    if docs:
        set_documents(vid, **docs)
    return user, project, vid


def _screenplay(version: int) -> dict[str, Any]:
    from tests.api.factories import screenplay_dict

    return {**screenplay_dict(), "schema_version": version}


@pytest.mark.parametrize(
    ("column", "document"),
    [("screenplay", "screenplay"), ("timeline", "timeline"), ("asset_manifest", "asset manifest")],
)
def test_a_job_on_a_newer_document_fails_before_its_handler_runs(make_worker, column: str, document: str) -> None:
    calls: list[int] = []

    @job_handler("t_newer")
    async def handler(ctx):
        calls.append(ctx.job_id)
        return {"ok": True}

    value: dict[str, Any] = _screenplay(4) if column == "screenplay" else {"schema_version": 4}
    user, project, vid = _version(**{column: value})
    jid = add_job("t_newer", user_id=user, project_id=project, version_id=vid, max_attempts=3)
    make_worker(["t_newer"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "newer_document" and row.attempts == 1
    assert f"This {document} was saved by a newer version" in row.error
    assert calls == []


def test_the_translation_source_is_checked_too(make_worker) -> None:
    calls: list[int] = []

    @job_handler("t_translate")
    async def handler(ctx):
        calls.append(ctx.job_id)

    user, project, target = _version()
    source = make_version(project, number=2)
    set_documents(source, screenplay=_screenplay(5))
    jid = add_job("t_translate", user_id=user, project_id=project, version_id=target,
                  payload={"source_version_id": source})
    make_worker(["t_translate"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "newer_document" and "schema version 5" in row.error and calls == []


def test_current_documents_run_normally(make_worker) -> None:
    @job_handler("t_current")
    async def handler(ctx):
        return {"ran": True}

    user, project, vid = _version(screenplay=_screenplay(3), timeline={"schema_version": 2}, asset_manifest=None)
    jid = add_job("t_current", user_id=user, project_id=project, version_id=vid)
    jid2 = add_job("t_current")  # no version at all
    make_worker(["t_current"]).start()
    assert wait_status(jid, "succeeded").result == {"ran": True}
    assert wait_status(jid2, "succeeded").result == {"ran": True}


def test_a_loader_meeting_a_newer_document_fails_the_job_without_retry(make_worker) -> None:
    attempts: list[int] = []

    @job_handler("t_loader")
    async def handler(ctx):
        attempts.append(ctx.attempt)
        Screenplay.model_validate(_screenplay(9))  # e.g. a document carried in an asset or a payload

    jid = add_job("t_loader", max_attempts=3)
    make_worker(["t_loader"]).start()
    row = wait_status(jid, "failed")
    assert (row.error_code, row.attempts, attempts) == ("newer_document", 1, [1])
    assert str(NewerDocumentError("screenplay", 9)) == row.error


def test_newer_documents_reads_only_the_markers(app_env) -> None:
    from aadhi.db import session_scope
    from aadhi.jobs.reader_version import newer_document_refusal, newer_documents

    user, project, vid = _version(screenplay=_screenplay(2), timeline={"schema_version": 3})
    other = make_version(project, number=2)
    set_documents(other, asset_manifest={"schema_version": 6})
    with session_scope() as db:
        assert newer_documents(db, [vid]) == []
        found = newer_documents(db, [vid, other])
        assert [(e.document, e.version) for e in found] == [("asset manifest", 6)]
        assert newer_document_refusal(db, vid, {"source_version_id": other}) == str(found[0])
        assert newer_document_refusal(db, None, None) is None
        assert newer_document_refusal(db, vid, {"source_version_id": True}) is None  # not an id
