"""O17 (owner, 2026-10-17): a built lecture keeps the voice it was made with. ``GET /api/versions/{id}`` says which
(``voice``), the budget pre-check of a build counts that voice (a lecture made with the free Edge voice stays free to
rebuild and edit while OpenAI TTS is the paid default), and ``POST /api/versions/{id}/voice`` is the teacher's explicit
re-voice: a ``build_assets`` job with the new voice in its payload."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from aadhi.models import Job, ProjectVersion, UsageEvent, User, utcnow
from tests.api.factories import add_job, add_project

ANTHROPIC_PERSONAL = "sk-ant-api03-personal-key-AAAAAAAA-1111"
OPENAI_SERVER = "sk-proj-openai-server-key-HHHHHHHH-8888"


def narration(provider: str, voice: str, *scene_ids: str) -> dict[str, Any]:
    """A stored manifest whose ``scene_ids`` were narrated by ``provider`` / ``voice`` (two beats each)."""
    return {"language": "en-IN", "audio": {sid: {
        "scene_id": sid, "asset_key": f"scene_audio-{sid}", "provider": provider, "voice": voice, "language": "en-IN",
        "beats": [{"beat_id": f"{sid}-b1", "offset": 0.0, "speech_duration": 1.0, "spoken_text": "Twelve chars"},
                  {"beat_id": f"{sid}-b2", "offset": 1.2, "speech_duration": 1.0, "spoken_text": "Eight ch"}],
    } for sid in scene_ids}}


@pytest.fixture()
def voice_api(api):
    """OpenAI TTS is the server's voice, paid with the server's key; personal keys are on."""
    api.settings.stored_api_keys_enabled = True
    api.settings.user_api_keys_enabled = True
    api.settings.tts_provider = "openai"
    api.settings.openai_api_key = SecretStr(OPENAI_SERVER)
    return api


def lecture(api, user, manifest: dict[str, Any] | None, options: dict[str, Any] | None = None) -> ProjectVersion:
    with api.db() as db:
        _, version = add_project(db, db.get(User, user.id))
        row = db.get(ProjectVersion, version.id)
        row.generation_meta = {"options": {"llm_provider": "anthropic", **(options or {})}}
        row.asset_manifest = manifest
        db.commit()
    return version


def over_budget(api, user) -> None:
    with api.db() as db:
        db.get(User, user.id).daily_budget_usd = 1.0
        db.add(UsageEvent(user_id=user.id, provider="gemini", model="m", operation="llm", cost_usd=2.0,
                          created_at=utcnow()))
        db.commit()


def jobs_of(api, version: ProjectVersion) -> list[tuple[int, dict[str, Any]]]:
    """``(id, payload)`` of the version's jobs, oldest first."""
    with api.db() as db:
        rows = db.execute(select(Job).where(Job.version_id == version.id).order_by(Job.id)).scalars()
        return [(job.id, dict(job.payload or {})) for job in rows]


def test_the_version_says_which_voice_a_built_lecture_keeps_and_what_a_revoice_costs(voice_api):
    alice, c = voice_api.editor("alice")
    built = lecture(voice_api, alice, narration("edge", "en-IN-NeerjaNeural", "s1", "s2", "q1"))
    voice = c.get(f"/api/versions/{built.id}").json()["voice"]
    assert {k: voice[k] for k in ("provider", "voice", "provider_label", "characters")} == {
        "provider": "edge", "voice": "en-IN-NeerjaNeural", "provider_label": "Microsoft Edge (free)", "characters": 60}
    costs = voice["revoice_usd"]
    assert costs["edge"] == 0 and costs["openai"] > 0 and set(costs) == {"edge", "openai", "gemini", "elevenlabs"}
    # the voice each provider narrates with on "Default voice for the language" (the dialog's no-change test)
    assert {k: voice["default_voices"][k] for k in ("edge", "openai")} == {"edge": "en-IN-NeerjaNeural", "openai": "nova"}
    voice_api.settings.tts_voice = "en-IN-PrabhatNeural"  # an Edge TTS_VOICE applies to Edge only
    defaults = c.get(f"/api/versions/{built.id}").json()["voice"]["default_voices"]
    assert (defaults["edge"], defaults["openai"]) == ("en-IN-PrabhatNeural", "nova")
    assert c.get(f"/api/versions/{lecture(voice_api, alice, None).id}").json()["voice"] is None  # not narrated yet
    # Edge standing in next to OpenAI scenes (a key that was missing for a while): the lecture's voice is OpenAI's
    mixed = narration("openai", "nova", "s1", "s2")
    mixed["audio"].update(narration("edge", "en-IN-NeerjaNeural", "q1")["audio"])
    assert c.get(f"/api/versions/{lecture(voice_api, alice, mixed).id}").json()["voice"]["provider"] == "openai"


def test_the_budget_precheck_treats_a_kept_free_voice_as_free(voice_api):
    """(e) Over budget, with her own Claude key: a lecture built on Edge is rebuilt, edited and retried without the
    server's OpenAI key paying for anything, so nothing refuses it; a lecture not narrated yet would be narrated by
    OpenAI on the server's key (refused), and so would a re-voice to OpenAI."""
    alice, c = voice_api.editor("alice")
    over_budget(voice_api, alice)
    assert c.put("/api/keys/anthropic", json={"api_key": ANTHROPIC_PERSONAL}).status_code == 200
    edge = lecture(voice_api, alice, narration("edge", "en-IN-NeerjaNeural", "s1", "s2", "q1"))
    assert c.post(f"/api/versions/{edge.id}/build", json={"scene_ids": None}).status_code == 202
    with voice_api.db() as db:
        for job in db.execute(select(Job).where(Job.version_id == edge.id)).scalars():
            job.status = "failed"
        db.commit()
    assert c.post(f"/api/versions/{edge.id}/scenes/s1/regenerate", json={}).status_code == 202
    with voice_api.db() as db:
        failed = add_job(db, kind="build_assets", status="failed", user=db.get(User, alice.id),
                         version=db.get(ProjectVersion, edge.id), payload={"scene_ids": None, "base_revision": 1})
        for job in db.execute(select(Job).where(Job.version_id == edge.id, Job.id != failed.id)).scalars():
            job.status = "failed"
        failed.project_id = db.get(ProjectVersion, edge.id).project_id
        db.commit()
    assert c.post(f"/api/jobs/{failed.id}/retry").status_code == 202  # the retry keeps the voice too
    new = lecture(voice_api, alice, None)
    refused = c.post(f"/api/versions/{new.id}/build", json={"scene_ids": None})
    assert refused.status_code == 429 and refused.json()["code"] == "budget"  # new: OpenAI on the server's key
    paid = c.post(f"/api/versions/{lecture(voice_api, alice, narration('edge', 'en-IN-NeerjaNeural', 's1')).id}/voice",
                  json={"tts_provider": "openai"})
    assert paid.status_code == 429 and paid.json()["code"] == "budget"
    # an OpenAI lecture whose key is gone narrates new scenes with the free Edge stand-in: free as well
    voice_api.settings.openai_api_key = SecretStr("")
    openai = lecture(voice_api, alice, narration("openai", "nova", "s1", "s2", "q1"))
    assert c.post(f"/api/versions/{openai.id}/build", json={"scene_ids": None}).status_code == 202


def test_a_kept_voice_without_its_key_is_counted_as_the_free_edge_stand_in(voice_api):
    """O17-2: a lecture that keeps Gemini, whose (personal) Gemini key is gone, on a server with an OpenAI key: the
    build narrates new scenes with the free Edge stand-in, never with TTS_PROVIDER, so the pre-check counts no server
    voice key and an over-budget teacher on her own Claude key may still build, edit and retry it."""
    voice_api.settings.gemini_api_key = SecretStr("")
    voice_api.settings.gemini_api_keys = []
    alice, c = voice_api.editor("alice")
    over_budget(voice_api, alice)
    assert c.put("/api/keys/anthropic", json={"api_key": ANTHROPIC_PERSONAL}).status_code == 200
    gemini = lecture(voice_api, alice, narration("gemini", "Kore", "s1", "s2", "q1"), {"tts_provider": "gemini"})
    assert c.post(f"/api/versions/{gemini.id}/build", json={"scene_ids": None}).status_code == 202
    with voice_api.db() as db:
        for job in db.execute(select(Job).where(Job.version_id == gemini.id)).scalars():
            job.status = "failed"
        db.commit()
    assert c.post(f"/api/versions/{gemini.id}/scenes/s1/regenerate", json={}).status_code == 202
    with voice_api.db() as db:
        failed = add_job(db, kind="build_assets", status="failed", user=db.get(User, alice.id),
                         version=db.get(ProjectVersion, gemini.id), payload={"scene_ids": None, "base_revision": 1})
        for job in db.execute(select(Job).where(Job.version_id == gemini.id, Job.id != failed.id)).scalars():
            job.status = "failed"
        failed.project_id = db.get(ProjectVersion, gemini.id).project_id
        db.commit()
    assert c.post(f"/api/jobs/{failed.id}/retry").status_code == 202


def test_an_openai_lecture_keeps_counting_as_paid(voice_api):
    alice, c = voice_api.editor("alice")
    over_budget(voice_api, alice)
    c.put("/api/keys/anthropic", json={"api_key": ANTHROPIC_PERSONAL})
    openai = lecture(voice_api, alice, narration("openai", "nova", "s1", "s2", "q1"), {"tts_provider": "edge"})
    refused = c.post(f"/api/versions/{openai.id}/build", json={"scene_ids": None})
    assert refused.status_code == 429 and refused.json()["code"] == "budget"  # its options' Edge does not count
    assert c.post(f"/api/versions/{openai.id}/voice", json={"tts_provider": "edge"}).status_code == 202  # free


def test_revoice_enqueues_a_build_with_the_new_voice(voice_api):
    """(c) The teacher's explicit re-voice: validated against the requester's keys and the narrating provider's
    voices, then a full build_assets job carrying the voice (the worker stores it in the version's options)."""
    alice, c = voice_api.editor("alice")
    version = lecture(voice_api, alice, narration("edge", "en-IN-NeerjaNeural", "s1", "s2", "q1"))
    r = c.post(f"/api/versions/{version.id}/voice", json={"tts_provider": "openai", "tts_voice": "onyx"})
    assert r.status_code == 202 and r.json()["job"]["kind"] == "build_assets"
    job_id, payload = jobs_of(voice_api, version)[-1]
    assert payload == {"scene_ids": None, "base_revision": 1, "voice": {"tts_provider": "openai", "tts_voice": "onyx"}}
    busy = c.post(f"/api/versions/{version.id}/voice", json={"tts_provider": "edge"})
    assert busy.status_code == 409 and busy.json()["code"] == "job_in_progress"
    with voice_api.db() as db:
        db.get(Job, job_id).status = "succeeded"
        db.commit()
    # "Server default" (no provider) with no voice: the server's provider narrates with its voice for the language
    assert c.post(f"/api/versions/{version.id}/voice", json={}).status_code == 202
    assert jobs_of(voice_api, version)[-1][1]["voice"] == {"tts_provider": None, "tts_voice": None}


@pytest.mark.parametrize(("body", "loc", "kind"), [
    ({"tts_provider": "gemini"}, "tts_provider", "provider_not_configured"),  # no Gemini key for her
    ({"tts_provider": "edge", "tts_voice": "nova"}, "tts_voice", "value_error"),  # an OpenAI voice sent to Edge
    ({"tts_voice": "en-IN-NeerjaNeural"}, "tts_voice", "value_error"),  # server default = OpenAI: not its voice
    ({"tts_provider": "openai", "tts_voice": "bad voice!"}, "tts_voice", "string_pattern_mismatch"),
    ({"tts_provider": "azure"}, "tts_provider", "literal_error"),
])
def test_revoice_refuses_a_voice_that_cannot_narrate(voice_api, body, loc, kind):
    alice, c = voice_api.editor("alice")
    version = lecture(voice_api, alice, narration("edge", "en-IN-NeerjaNeural", "s1"))
    r = c.post(f"/api/versions/{version.id}/voice", json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"][0]
    assert detail["loc"][-1] == loc and detail["type"] == kind
    assert not jobs_of(voice_api, version)


def test_revoice_needs_a_screenplay_and_is_the_owners(voice_api):
    alice, c = voice_api.editor("alice")
    _, bob = voice_api.editor("bob")
    with voice_api.db() as db:
        _, empty = add_project(db, db.get(User, alice.id), built=False)
        db.get(ProjectVersion, empty.id).screenplay = None
        db.commit()
    r = c.post(f"/api/versions/{empty.id}/voice", json={"tts_provider": "edge"})
    assert r.status_code == 409 and r.json()["code"] == "no_screenplay"
    version = lecture(voice_api, alice, narration("edge", "en-IN-NeerjaNeural", "s1"))
    assert bob.post(f"/api/versions/{version.id}/voice", json={"tts_provider": "edge"}).status_code == 404
