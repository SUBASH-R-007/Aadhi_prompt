"""``/api/admin/settings``: the administrator's runtime switches (``aadhi.runtime_flags``).

Today one switch, ``dynamic_enabled``. The tests pin the three things that make it worth having: an admin can turn
the Dynamic video mode on without touching the environment or restarting, the switch really reaches the gate that
decides whether a generated lecture is Dynamic (``compose.presentation.dynamic_enabled``), and a non-admin cannot
touch it. The environment keeps ruling while no switch is stored, which is the behaviour before this table.
"""

from __future__ import annotations

import pytest

from aadhi import runtime_flags
from aadhi.compose.presentation import dynamic_enabled
from aadhi.config import Settings

OFF = Settings(_env_file=None)
ON = Settings(_env_file=None, dynamic_enabled=True)


@pytest.fixture(autouse=True)
def _clean_flag_cache():
    """The flag cache lives for the process, so a test must never inherit another's value."""
    runtime_flags.invalidate()
    yield
    runtime_flags.invalidate()


def _admin(api):
    api.user("root", role="admin")
    return api.login("root")


def test_without_a_stored_switch_the_environment_rules(api) -> None:
    """Exactly the behaviour before the table existed, so an un-upgraded deployment is unchanged."""
    body = _admin(api).get("/api/admin/settings").json()
    assert body["dynamic_enabled"] == {"value": False, "source": "environment", "environment": False}
    assert dynamic_enabled(OFF) is False
    assert dynamic_enabled(ON) is True


def test_an_admin_switches_the_dynamic_video_mode_on_for_every_process(api) -> None:
    """The point of the switch: it reaches the gate, with no restart and no environment change."""
    admin = _admin(api)
    assert dynamic_enabled(OFF) is False

    body = admin.patch("/api/admin/settings", json={"dynamic_enabled": True}).json()
    assert body["dynamic_enabled"] == {"value": True, "source": "studio", "environment": False}
    # The gate a generation, a build, a serve and a render all read (compose.presentation.dynamic_enabled).
    assert dynamic_enabled(OFF) is True

    body = admin.patch("/api/admin/settings", json={"dynamic_enabled": False}).json()
    assert body["dynamic_enabled"]["value"] is False
    # The stored switch wins over the environment in both directions, so an admin can turn it off again.
    assert dynamic_enabled(ON) is False


def test_a_generated_lecture_follows_the_switch(api) -> None:
    """End to end: with the switch on, a newly assembled screenplay carries a Dynamic presentation."""
    from aadhi.pipeline.base import GenerationOptions, IngestResult
    from aadhi.pipeline.plan import LecturePlan, PlannedChapter, PlannedScene
    from aadhi.pipeline.script import assemble_screenplay

    plan = LecturePlan(session_title="Ohm's Law", subject_name="BEE", unit_name="Unit 2", session_number="3",
                       chapters=[PlannedChapter(id="p1", title="Ohm", scenes=[
                           PlannedScene(id="v", type="content", goal="Explain.", narrative_role="concept")])])

    def assembled():
        return assemble_screenplay(plan, [], IngestResult(markdown="# Ohm"), GenerationOptions(), [], None,
                                   settings=OFF)

    assert assembled().presentation is None  # the environment says off
    _admin(api).patch("/api/admin/settings", json={"dynamic_enabled": True})
    pres = assembled().presentation
    assert pres is not None and pres.style == "dynamic"
    assert pres.theme_family and pres.personality  # the rotated look (compose.dynamic.authoring)


def test_only_an_admin_may_read_or_change_the_switches(api) -> None:
    _, editor = api.editor("alice")
    assert editor.get("/api/admin/settings").status_code == 403
    assert editor.patch("/api/admin/settings", json={"dynamic_enabled": True}).status_code == 403
    assert runtime_flags.get("dynamic_enabled") is None


def test_an_unknown_switch_is_refused(api) -> None:
    """``extra='forbid'``: a typo must fail loudly rather than be stored and silently ignored."""
    assert _admin(api).patch("/api/admin/settings", json={"no_such_switch": True}).status_code == 422
