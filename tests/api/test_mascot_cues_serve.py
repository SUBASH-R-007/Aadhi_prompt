"""MASCOT_CUES takes effect when a timeline is served (watch/share player, editor preview, render page).

The MP4 rebuilds its timeline with the current setting, so a timeline stored under the other value must be
served with the current one too: otherwise the player and the MP4 disagree about the cue bubble.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from aadhi.models import ProjectVersion, Render
from tests.api.conftest import Api
from tests.api.factories import add_project, make_timeline


@contextmanager
def running(app_env: Any, *, mascot_cues: bool) -> Iterator[Api]:
    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    settings = app_env.model_copy(update={"mascot_cues": mascot_cues})
    reset_rate_limits()
    harness = Api(app=create_app(settings), settings=settings)
    lifespan = harness.client(browser=False)
    with lifespan:
        yield harness
        for c in harness.clients:
            if c is not lifespan:
                c.close()
    reset_rate_limits()


def _store(harness: Api, *, stored_cues: bool) -> int:
    """A built version whose stored timeline was made under ``stored_cues``; returns its id."""
    alice = harness.user("alice")
    with harness.db() as db:
        _, version = add_project(db, alice)
        row = db.get(ProjectVersion, version.id)
        timeline = make_timeline(row.get_screenplay(), version_id=row.id, revision=1)
        for scene in timeline.scenes:
            scene.layout.mascot_cues = stored_cues
        row.set_timeline(timeline)
        db.add(Render(version_id=row.id, status="running"))
        db.commit()
        return row.id


def _render_token(vid: int) -> str:
    from aadhi.auth.tokens import create_scoped_token

    return create_scoped_token("render", {"vid": vid}, 600)


def _cue_flags(timeline: dict[str, Any]) -> list[Any]:
    assert timeline["scenes"], "the fixture timeline has scenes"
    return [scene["layout"].get("mascot_cues", "absent") for scene in timeline["scenes"]]


@pytest.mark.parametrize(("stored_cues", "setting", "served"), [(True, False, False), (False, True, "absent")])
def test_served_timeline_follows_the_current_mascot_cues_setting(app_env, stored_cues, setting, served):
    with running(app_env, mascot_cues=setting) as harness:
        vid = _store(harness, stored_cues=stored_cues)
        with harness.db() as db:  # the stored document keeps the value it was built with
            stored = db.get(ProjectVersion, vid).timeline
            assert all(s["layout"].get("mascot_cues", True) is stored_cues for s in stored["scenes"])
        player = harness.login("alice").get(f"/api/versions/{vid}/timeline")
        assert player.status_code == 200, player.text
        assert set(_cue_flags(player.json())) == {served}  # watch page / Studio player
        render = harness.client(browser=False).get(
            "/api/render/timeline", headers={"Authorization": f"Bearer {_render_token(vid)}"})
        assert render.status_code == 200, render.text
        assert set(_cue_flags(render.json())) == {served}  # the render page the MP4 is captured from
