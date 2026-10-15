"""The ``import_legacy`` job's JSON import follows the schema v3 rules of ``POST /api/projects/import`` (design §6.7;
B0): a newer screenplay is refused (``NewerDocumentError``) and Dynamic-video features this server does not enable
fail the job with ``feature_unavailable``."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aadhi.jobs.base import FatalJobError
from aadhi.legacy.jobs import import_legacy_job
from aadhi.models import ProjectVersion
from aadhi.schemas.features import NewerDocumentError
from aadhi.storage.assets import Produced, bytes_key
from tests.schemas.v3_fixtures import classic_screenplay_dict, with_feature


def _stage(job_ctx: Any, asset_store: Any, user_id: int, doc: dict[str, Any]) -> None:
    data = json.dumps(doc).encode("utf-8")
    key = bytes_key("upload", data)
    asset_store.put(key, "upload", Produced(data=data, mime="application/json"))
    job_ctx.payload, job_ctx.user_id = {"json_asset_key": key}, user_id


def test_json_import_job_refuses_disabled_features(job_ctx, asset_store, make_user, db_session) -> None:
    user = make_user("alice")
    _stage(job_ctx, asset_store, user.id, with_feature("cuts"))
    with pytest.raises(FatalJobError) as exc:
        asyncio.run(import_legacy_job(job_ctx))
    assert exc.value.code == "feature_unavailable" and "cuts" in str(exc.value)
    job_ctx.settings.dynamic_features = ["cuts"]
    (pid,) = asyncio.run(import_legacy_job(job_ctx))["project_ids"]
    version = db_session.query(ProjectVersion).filter_by(project_id=pid).one()
    assert version.screenplay["schema_version"] == 3


def test_json_import_job_refuses_a_newer_screenplay(job_ctx, asset_store, make_user) -> None:
    user = make_user("bob")
    _stage(job_ctx, asset_store, user.id, {**classic_screenplay_dict(), "schema_version": 4})
    with pytest.raises(NewerDocumentError):
        asyncio.run(import_legacy_job(job_ctx))
