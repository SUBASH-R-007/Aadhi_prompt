"""GET /api/admin/media-providers: admins only, configuration only, no secrets."""

from __future__ import annotations

import httpx
import pytest

from tests.api.conftest import api  # noqa: F401  (the API harness fixture)

KEY = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"


def test_media_provider_status_is_admin_only_and_secret_free(api, monkeypatch):  # noqa: F811
    from aadhi.providers.base import ProviderUnavailable
    from aadhi.providers.health import provider_health

    api.settings.image_provider = "pollinations"
    api.settings.image_fallback_providers = ["gemini"]
    api.settings.video_provider = "veo"
    api.settings.gemini_api_key = type(api.settings.gemini_api_key)(KEY)
    monkeypatch.setattr(httpx.AsyncClient, "send", lambda *a, **k: pytest.fail("no provider call"))
    provider_health().note_failure("pollinations", "server", ProviderUnavailable("402", status=402), api.settings)

    _, editor = api.editor("alice")
    assert editor.get("/api/admin/media-providers").status_code == 403

    api.user("root", role="admin")
    admin = api.login("root")
    r = admin.get("/api/admin/media-providers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["name"] for p in body["image"]["providers"]] == ["pollinations", "gemini"]
    assert body["image"]["providers"][0]["state"] == "cooling" and body["image"]["providers"][1]["paid"] is True
    assert body["video"]["providers"][0]["name"] == "veo" and body["video"]["providers"][0]["state"] == "available"
    assert body["auth_cooldown_seconds"] == api.settings.media_provider_auth_cooldown_seconds
    assert KEY not in r.text and "AIza" not in r.text


def _cooldown_event(db, *, provider="pollinations", kind="image", scope="server", seconds=600, age=0.0):
    import datetime as dt

    from aadhi.models import JobEvent
    from tests.api.factories import add_job

    job = add_job(db)
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age)
    db.add(JobEvent(job_id=job.id, level="warning", stage="assets", created_at=created,
                    message=f"{kind.capitalize()} provider {provider} failed (payment or account problem); "
                            "trying the next configured provider.",
                    data={"provider": provider, "category": "unavailable", "scene_id": "a", "kind": kind,
                          "scope": scope, "cooldown_seconds": seconds}))
    db.commit()


def _status(api):  # noqa: F811
    api.user("root", role="admin")
    r = api.login("root").get("/api/admin/media-providers")
    assert r.status_code == 200, r.text
    return r.json()


def test_external_workers_cooldowns_come_from_their_job_events(api):  # noqa: F811
    api.settings.image_provider = "pollinations"
    api.settings.image_fallback_providers = ["gemini"]
    api.settings.gemini_api_key = type(api.settings.gemini_api_key)(KEY)
    api.settings.worker_mode = "external"
    with api.db() as db:
        _cooldown_event(db)
        _cooldown_event(db, provider="gemini", scope="user")  # one user's own key: never the server's state
        _cooldown_event(db, provider="veo", kind="video", seconds=60, age=120)  # long over
    body = _status(api)
    poll, gemini = body["image"]["providers"]
    assert poll["state"] == "cooling" and 590 <= poll["cooldown_seconds_left"] <= 600
    assert "payment or account refusal" in poll["reason"]
    assert gemini["state"] == "available"
    assert body["cooldowns_source"] == "recent worker job events"


def test_inline_workers_use_this_process_only(api):  # noqa: F811
    api.settings.image_provider = "pollinations"
    api.settings.worker_mode = "inline"
    with api.db() as db:
        _cooldown_event(db)
    body = _status(api)
    assert body["image"]["providers"][0]["state"] == "available"
    assert body["cooldowns_source"] == "this process"
