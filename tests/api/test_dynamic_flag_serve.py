"""``DYNAMIC_ENABLED`` takes effect when a timeline is served (Studio / watch player, public share, render page).

The render job rebuilds the timeline with the current flag, so a stored timeline built under the other value must
be served rebuilt the same way: otherwise the live player keeps the Dynamic stage after the admin switched it off (the
flag would be no kill switch) while the MP4 comes out classic, or the reverse.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from aadhi.models import ProjectVersion
from tests.api.conftest import Api
from tests.api.factories import add_project, screenplay_dict


@contextmanager
def running(app_env: Any, *, dynamic: bool) -> Iterator[Api]:
    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    settings = app_env.model_copy(update={"dynamic_enabled": dynamic})
    reset_rate_limits()
    harness = Api(app=create_app(settings), settings=settings)
    lifespan = harness.client(browser=False)
    with lifespan:
        yield harness
        for c in harness.clients:
            if c is not lifespan:
                c.close()
    reset_rate_limits()


def _screenplay(*, presentation: bool) -> Any:
    from aadhi.schemas.screenplay import Screenplay
    from tests.schemas.v3_fixtures import PRESENTATION

    data = screenplay_dict()
    if presentation:
        data["presentation"] = {**PRESENTATION, "format": "landscape", "hook": None, "theme_family": "midnight_indigo",
                                "music": {"source": "procedural", "provider": "procedural"}}
    return Screenplay.model_validate(data)


def _store(harness: Api, *, built_dynamic: bool, presentation: bool = True, stale: bool = False) -> tuple[int, int]:
    """A version whose stored timeline was built with ``DYNAMIC_ENABLED == built_dynamic``; ``(project, version)``."""
    from sqlalchemy import select

    from aadhi.compose.timeline import build_timeline
    from aadhi.models import User

    with harness.db() as db:
        existing = db.execute(select(User).where(User.username == "alice")).scalar_one_or_none()
    alice = existing if existing is not None else harness.user("alice")
    sp = _screenplay(presentation=presentation)
    with harness.db() as db:
        project, version = add_project(db, alice, screenplay=sp, built=False)
        row = db.get(ProjectVersion, version.id)
        settings = harness.settings.model_copy(update={"dynamic_enabled": built_dynamic})
        row.set_timeline(build_timeline(sp, None, settings=settings, version_id=row.id, revision=1))
        row.built_revision = 1
        if stale:
            row.revision = 2
        db.commit()
        return project.id, row.id


def _style(timeline: dict[str, Any]) -> str:
    return (timeline.get("presentation") or {}).get("style") or "classic"


def _render_token(vid: int, **claims: Any) -> str:
    from aadhi.auth.tokens import create_scoped_token

    return create_scoped_token("render", {"vid": vid, **claims}, 600)


@pytest.mark.parametrize(("built", "flag"), [(True, False), (False, True)])
def test_the_served_timeline_follows_the_current_flag(app_env, built: bool, flag: bool) -> None:
    with running(app_env, dynamic=flag) as harness:
        pid, vid = _store(harness, built_dynamic=built)
        with harness.db() as db:  # the stored document keeps the engine it was built for
            assert _style(db.get(ProjectVersion, vid).timeline) == ("dynamic" if built else "classic")
        c = harness.login("alice")
        player = c.get(f"/api/versions/{vid}/timeline")
        assert player.status_code == 200, player.text
        served = player.json()
        assert _style(served) == ("dynamic" if flag else "classic")
        assert (served.get("intro") is not None) is (not flag), "a classic lecture has its intro, a Dynamic one none"
        assert served["fps"] == (30 if flag else harness.settings.render_fps)
        share = c.post(f"/api/projects/{pid}/shares", json={"expires_in_days": 7})
        assert share.status_code == 201, share.text
        watch = harness.client(browser=False).get(f"/api/public/watch/{share.json()['token']}")
        assert watch.status_code == 200 and _style(watch.json()["timeline"]) == _style(served)
        # the render page (an old token without include_intro reads the served timeline as well)
        page = harness.client(browser=False).get("/api/render/timeline",
                                                 headers={"Authorization": f"Bearer {_render_token(vid)}"})
        assert page.status_code == 200 and _style(page.json()) == _style(served)
        rebuilt = harness.client(browser=False).get(
            "/api/render/timeline", headers={"Authorization": f"Bearer {_render_token(vid, include_intro=False)}"})
        assert rebuilt.status_code == 200 and _style(rebuilt.json()) == _style(served)


def test_what_is_served_is_what_the_render_job_builds(app_env) -> None:
    from aadhi.compose.timeline import build_timeline

    with running(app_env, dynamic=False) as harness:
        _, vid = _store(harness, built_dynamic=True)
        served = harness.login("alice").get(f"/api/versions/{vid}/timeline").json()
        with harness.db() as db:
            row = db.get(ProjectVersion, vid)
            job = build_timeline(row.get_screenplay(), row.get_manifest(), settings=harness.settings, version_id=vid,
                                 revision=row.revision)  # as video._load_job builds it
        assert _style(served) == _style(job.model_dump(mode="json")) == "classic"
        assert [s["scene_id"] for s in served["scenes"]] == [s.scene_id for s in job.scenes]
        assert served["total_duration"] == pytest.approx(job.total_duration)


def test_the_etag_changes_with_the_flag(app_env) -> None:
    with running(app_env, dynamic=True) as harness:
        _, vid = _store(harness, built_dynamic=True)
        on = harness.login("alice").get(f"/api/versions/{vid}/timeline")
        assert on.status_code == 200
        etag = on.headers["etag"]
        assert harness.login("alice").get(f"/api/versions/{vid}/timeline",
                                          headers={"If-None-Match": etag}).status_code == 304
    with running(app_env, dynamic=False) as harness:
        c = harness.login("alice")
        off = c.get(f"/api/versions/{vid}/timeline", headers={"If-None-Match": etag})
        assert off.status_code == 200 and off.headers["etag"] != etag  # a cached Dynamic copy is refreshed
        assert _style(off.json()) == "classic"


def test_stored_timelines_that_match_are_served_as_built(app_env) -> None:
    with running(app_env, dynamic=True) as harness:
        _, classic = _store(harness, built_dynamic=False, presentation=False)  # no presentation: classic either way
        _, dynamic = _store(harness, built_dynamic=True)
        c = harness.login("alice")
        assert _style(c.get(f"/api/versions/{classic}/timeline").json()) == "classic"
        assert _style(c.get(f"/api/versions/{dynamic}/timeline").json()) == "dynamic"
        # a stale classic build of a screenplay that gained a presentation keeps its look until it is rebuilt
        _, stale = _store(harness, built_dynamic=False, stale=True)
        assert _style(c.get(f"/api/versions/{stale}/timeline").json()) == "classic"


def test_turning_the_flag_off_stops_the_dynamic_stage_even_for_a_stale_build(app_env) -> None:
    with running(app_env, dynamic=False) as harness:
        _, vid = _store(harness, built_dynamic=True, stale=True)
        served = harness.login("alice").get(f"/api/versions/{vid}/timeline")
        assert served.status_code == 200 and _style(served.json()) == "classic"


# --- the look-engine generation (plan B2 §1.3): an older Dynamic build is served rebuilt -------------------------

B1_TOKEN_KEYS = ("struct.accent", "bg", "bg-alt", "bg-tint", "surface")
B2A_PRESENTATION_KEYS = ("content_alt", "family", "mode", "motion_scale", "entity_colors")
MARKER_SEED = 7  # written into the stored document only: a rebuild replaces it with the real seed


def _as_b1(doc: dict[str, Any]) -> dict[str, Any]:
    """The document the B1 resolver stored: engine_version 1, five tokens per look, no B2a presentation field."""
    out = copy.deepcopy(doc)
    out["presentation"] = {k: v for k, v in out["presentation"].items() if k not in B2A_PRESENTATION_KEYS}
    out["presentation"]["engine_version"] = 1
    for s in out["scenes"]:
        s["look"]["tokens"] = {k: s["look"]["tokens"][k] for k in B1_TOKEN_KEYS}
    return out


def _store_engine(harness: Api, *, engine: int | None, stale: bool = False) -> tuple[int, int, dict[str, Any]]:
    """A version whose stored Dynamic timeline is of look-engine generation ``engine`` (None: a classic build of a
    screenplay without a presentation), marked so that only a rebuild loses the mark; ``(project, version, doc)``."""
    pid, vid = _store(harness, built_dynamic=engine is not None, presentation=engine is not None, stale=stale)
    with harness.db() as db:
        row = db.get(ProjectVersion, vid)
        doc = copy.deepcopy(row.timeline)
        if engine is None:
            doc["meta"]["marker"] = MARKER_SEED
        else:
            doc = _as_b1(doc) if engine == 1 else doc
            doc["presentation"]["engine_version"] = engine
            doc["scenes"][0]["look"]["background"]["seed"] = MARKER_SEED
        row.timeline = doc
        db.commit()
    return pid, vid, doc


def _marked(served: dict[str, Any]) -> bool:
    if served.get("presentation") is None:
        return served["meta"].get("marker") == MARKER_SEED
    return served["scenes"][0]["look"]["background"]["seed"] == MARKER_SEED


def test_an_older_look_engine_is_served_rebuilt(app_env) -> None:
    """A stored engine_version 1 timeline of the current revision is served as the render job would build it now
    (engine 2, with the derived structure colours), on every serving path; the stored document is left alone."""
    from aadhi.compose.dynamic import themes
    from aadhi.compose.presentation import ENGINE_VERSION
    from aadhi.compose.timeline import build_timeline

    with running(app_env, dynamic=True) as harness:
        pid, vid, stored = _store_engine(harness, engine=1)
        c = harness.login("alice")
        served = c.get(f"/api/versions/{vid}/timeline").json()
        assert served["presentation"]["engine_version"] == ENGINE_VERSION == 2 and not _marked(served)
        assert all(tuple(s["look"]["tokens"]) == themes.SCENE_TOKEN_KEYS for s in served["scenes"])
        with harness.db() as db:
            row = db.get(ProjectVersion, vid)
            assert row.timeline == stored  # served rebuilt, never written back
            job = build_timeline(row.get_screenplay(), row.get_manifest(), settings=harness.settings,
                                 version_id=vid, revision=row.revision).model_dump(mode="json")
        assert [s["look"] for s in served["scenes"]] == [s["look"] for s in job["scenes"]]
        assert served["presentation"] == job["presentation"]
        share = c.post(f"/api/projects/{pid}/shares", json={"expires_in_days": 7}).json()["token"]
        watch = harness.client(browser=False).get(f"/api/public/watch/{share}").json()["timeline"]
        page = harness.client(browser=False).get("/api/render/timeline",
                                                 headers={"Authorization": f"Bearer {_render_token(vid)}"}).json()
        for other in (watch, page):
            assert other["presentation"]["engine_version"] == 2 and not _marked(other)


@pytest.mark.parametrize(("engine", "stale"), [(2, False), (3, False), (1, True), (2, True), (None, False)])
def test_other_builds_are_served_as_stored(app_env, engine: int | None, stale: bool) -> None:
    """Served exactly as stored: a current-generation build; one of a newer generation (the stage refuses it with
    timeline_invalid rather than a downgrade here); a stale build of the previous revision (served as built, as with
    the flag); a classic build (nothing Dynamic is looked at)."""
    with running(app_env, dynamic=True) as harness:
        _, vid, stored = _store_engine(harness, engine=engine, stale=stale)
        served = harness.login("alice").get(f"/api/versions/{vid}/timeline").json()
        assert _marked(served)
        if engine is not None:
            assert served["presentation"]["engine_version"] == engine
            assert [s["look"]["tokens"] for s in served["scenes"]] == [s["look"]["tokens"] for s in stored["scenes"]]


def test_the_etag_changes_with_the_look_engine(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy that raises ENGINE_VERSION refreshes every cached copy (304 before, 200 with the rebuilt timeline
    after), though the version row did not change."""
    from aadhi.api import timelines

    with running(app_env, dynamic=True) as harness:
        _, vid, _ = _store_engine(harness, engine=2)
        c = harness.login("alice")
        first = c.get(f"/api/versions/{vid}/timeline")
        etag = first.headers["etag"]
        assert _marked(first.json())
        assert c.get(f"/api/versions/{vid}/timeline", headers={"If-None-Match": etag}).status_code == 304
        monkeypatch.setattr(timelines, "ENGINE_VERSION", 3)
        after = c.get(f"/api/versions/{vid}/timeline", headers={"If-None-Match": etag})
        assert after.status_code == 200 and after.headers["etag"] != etag
        assert not _marked(after.json()), "the stored generation 2 is now older: rebuilt"
