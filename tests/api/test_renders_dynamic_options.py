"""Render options of a Dynamic lecture (DYNAMIC_VIDEO_DESIGN §2.6, §7.1): the caption policy reaches the job.

``POST /render`` without ``burn_captions`` used to store ``false``, so ``compose.motion.job.caption_modes`` never fell
back to the lecture's ``presentation.captions_burn`` (schema default: burned). Now an unset field is left out of the
job payload for a lecture the motion engine renders, an explicit choice is kept either way, and a classic render
stores ``false`` exactly as before. ``GET /render/preflight`` tells the Studio's render dialog which kind of render it
is (``presentation``), so it can pre-check "Burn captions" and leave out the classic intro option.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.api.routers.renders import RenderBody
from aadhi.models import Job, Render
from tests.api.factories import add_project, screenplay_dict


def _dynamic_screenplay(**presentation: Any) -> Any:
    """A valid screenplay with a Dynamic presentation (``tests.schemas.v3_fixtures.PRESENTATION``: portrait, captions
    not burned) and ``presentation`` overrides; a key set to None is left out (the schema default applies)."""
    from aadhi.schemas.screenplay import Screenplay
    from tests.schemas.v3_fixtures import PRESENTATION

    pres = {**PRESENTATION, "hook": None, "theme_family": "midnight_indigo",
            "music": {"source": "procedural", "provider": "procedural"}, **presentation}
    data = screenplay_dict()
    data["presentation"] = {k: v for k, v in pres.items() if v is not None}
    return Screenplay.model_validate(data)


def _version(api: Any, user: Any, screenplay: Any = None) -> int:
    with api.db() as db:
        _, version = add_project(db, user, screenplay=screenplay)
        return version.id


def _stored(api: Any, response: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(Render.options, job payload)`` of an accepted ``POST /render``."""
    assert response.status_code == 202, response.text
    with api.db() as db:
        row = db.get(Render, response.json()["render"]["id"])
        job = db.get(Job, response.json()["job"]["id"])
        return dict(row.options), dict(job.payload)


# --- RenderBody.job_options -------------------------------------------------------------------------------------------


def test_job_options_leave_an_unset_burn_choice_to_a_dynamic_render_only():
    assert RenderBody().job_options() == {"burn_captions": False, "include_intro": True}, "classic: as before"
    assert RenderBody().job_options(dynamic=True) == {"include_intro": True}
    for choice in (False, True):
        expected = {"burn_captions": choice, "include_intro": True}
        assert RenderBody(burn_captions=choice).job_options(dynamic=True) == expected
        assert RenderBody(burn_captions=choice).job_options() == expected
    assert RenderBody(soft_subtitles=False).job_options(dynamic=True) == {"include_intro": True, "soft_subtitles": False}


def test_an_omitted_choice_reaches_the_motion_job_as_the_presentation_default():
    """The payload ``job_options`` writes is what ``caption_modes`` reads (the render job's Dynamic branch)."""
    from aadhi.compose.motion.job import caption_modes

    for burn in (True, False):
        timeline = SimpleNamespace(captions=[object()], scenes=[], presentation=SimpleNamespace(captions_burn=burn))
        payload = {"render_id": 1, **RenderBody().job_options(dynamic=True)}
        assert caption_modes(payload, timeline)[0] is burn, "the lecture's caption policy"
        for choice in (False, True):
            explicit = {"render_id": 1, **RenderBody(burn_captions=choice).job_options(dynamic=True)}
            assert caption_modes(explicit, timeline)[0] is choice, "the teacher's choice wins"


def test_a_classic_render_without_a_choice_still_stays_clean_whatever_render_burn_captions_says(app_env):
    """Classic invariance: the payload keeps ``burn_captions: false``, so RENDER_BURN_CAPTIONS does not start applying
    to requests that never sent the field."""
    from aadhi.compose.video import RenderPayload

    settings = app_env.model_copy(update={"render_burn_captions": True})
    payload = RenderPayload.from_dict({"render_id": 1, **RenderBody().job_options()}, settings)
    assert payload.burn_captions is False


# --- POST /render -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
def test_render_request_stores_the_teachers_choice_and_omits_an_unset_one_for_dynamic(api, flag):
    api.settings.dynamic_enabled = flag
    api.settings.render_max_per_user = 0  # three renders at once
    user, c = api.editor("alice")
    sp = _dynamic_screenplay()
    omitted, off, on = (_version(api, user, sp) for _ in range(3))
    options, payload = _stored(api, c.post(f"/api/versions/{omitted}/render", json={}))
    if flag:  # the motion engine renders it: the job burns as presentation.captions_burn says
        assert options == {"include_intro": True}
        assert "burn_captions" not in payload
    else:  # DYNAMIC_ENABLED off: the lecture renders classic, exactly as before
        assert options == {"burn_captions": False, "include_intro": True}
        assert payload["burn_captions"] is False
    for vid, choice in ((off, False), (on, True)):
        options, payload = _stored(api, c.post(f"/api/versions/{vid}/render", json={"burn_captions": choice}))
        assert options["burn_captions"] is choice and payload["burn_captions"] is choice


def test_a_classic_lecture_without_a_choice_stores_false(api):
    api.settings.dynamic_enabled = True  # the flag alone makes nothing Dynamic: the lecture has no presentation
    user, c = api.editor("alice")
    options, payload = _stored(api, c.post(f"/api/versions/{_version(api, user)}/render", json={}))
    assert options == {"burn_captions": False, "include_intro": True}
    assert payload == {"render_id": payload["render_id"], "burn_captions": False, "include_intro": True}


def test_render_rejects_a_non_boolean_burn_choice(api):
    user, c = api.editor("alice")
    vid = _version(api, user)
    assert c.post(f"/api/versions/{vid}/render", json={"burn_captions": "yes please"}).status_code == 422
    assert c.post(f"/api/versions/{vid}/render", json={"burn_captions": None}).status_code == 202  # = not sent


# --- GET /render/preflight: presentation ------------------------------------------------------------------------------


def test_preflight_says_which_render_the_lecture_gets(api):
    user, c = api.editor("alice")
    classic = _version(api, user)
    reel = _version(api, user, _dynamic_screenplay())  # portrait, captions_burn false
    lesson = _version(api, user, _dynamic_screenplay(format="landscape", captions_burn=None))  # schema default: burned

    def presentation(vid: int) -> Any:
        r = c.get(f"/api/versions/{vid}/render/preflight")
        assert r.status_code == 200, r.text
        return r.json()["presentation"]

    api.settings.dynamic_enabled = True
    assert presentation(classic) is None
    assert presentation(reel) == {"style": "dynamic", "format": "portrait", "captions_burn": False}
    assert presentation(lesson) == {"style": "dynamic", "format": "landscape", "captions_burn": True}
    api.settings.dynamic_enabled = False  # the kill switch: every lecture renders classic
    assert [presentation(v) for v in (classic, reel, lesson)] == [None, None, None]
