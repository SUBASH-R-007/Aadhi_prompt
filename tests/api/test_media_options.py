"""A lecture's image provider choice (``GenerationOptions.image_provider``), ``/api/meta`` image
providers and media features with backup providers, and the budget pre-check for backups."""

from __future__ import annotations

import json
from types import SimpleNamespace

from pydantic import SecretStr

from aadhi.models import Job, Project
from aadhi.pipeline.base import GenerationOptions

TXT = ("ohm.txt", b"Ohm's law: V = I R. Current is the flow of charge.", "text/plain")


def create(c, options: dict | None = None):
    data = {"options": json.dumps(options)} if options is not None else {}
    return c.post("/api/projects", files={"file": TXT}, data=data)


def test_meta_lists_the_image_providers_a_lecture_may_choose(api):
    _, c = api.editor("alice")
    image = c.get("/api/meta").json()["image"]
    assert image["default_provider"] == "fake"
    assert image["providers"] == [
        {"id": "gemini", "label": "Google Gemini / Imagen", "configured": False, "paid": True},
        {"id": "pollinations", "label": "Pollinations", "configured": True, "paid": False},
    ]
    assert c.get("/api/meta").json()["generation_defaults"]["image_provider"] is None
    api.settings.gemini_api_key = SecretStr("AIzaTEST-123456789")
    assert c.get("/api/meta").json()["image"]["providers"][0]["configured"] is True
    api.settings.image_provider = "none"
    assert c.get("/api/meta").json()["image"] == {"default_provider": "none", "providers": []}


def test_a_chosen_image_provider_must_be_configured(api):
    _, c = api.editor("alice")
    refused = create(c, {"image_provider": "gemini"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == [{"loc": ["options", "image_provider"], "type": "provider_not_configured",
                                         "msg": "Image provider 'Google Gemini / Imagen' is not configured on this server."}]
    assert create(c, {"image_provider": "dalle"}).status_code == 422

    ok = create(c, {"image_provider": "pollinations"})
    assert ok.status_code == 201, ok.text
    with api.db() as db:
        assert db.get(Job, ok.json()["job"]["id"]).payload["options"]["image_provider"] == "pollinations"
        pid = ok.json()["project"]["id"]
        assert db.get(Project, pid).settings["image_provider"] == "pollinations"
    back = c.post(f"/api/projects/{pid}/regenerate", json={"options": {"image_provider": None}})
    assert back.status_code == 201, back.text
    with api.db() as db:
        assert db.get(Job, back.json()["job"]["id"]).payload["options"]["image_provider"] is None

    api.settings.image_provider = "none"
    off = create(c, {"image_provider": "pollinations"})
    assert off.status_code == 422
    assert off.json()["detail"][0]["msg"] == "Generated images are disabled on this server."


def test_media_features_count_a_configured_backup_provider(api):
    _, c = api.editor("alice")
    api.settings.image_provider = "gemini"  # no Gemini key anywhere
    api.settings.video_provider = "veo"
    features = c.get("/api/meta").json()["features"]
    assert features["generated_images"] is False and features["ai_video"] is False
    api.settings.image_fallback_providers = ["pollinations"]
    api.settings.video_fallback_providers = ["fake"]
    features = c.get("/api/meta").json()["features"]
    assert features["generated_images"] is True and features["ai_video"] is True


def test_budget_precheck_counts_a_gemini_backup_and_the_lectures_choice(app_env):
    from aadhi.api.generation import _media_billed_to_server

    def keys(gemini: str | None):
        return SimpleNamespace(source=lambda name: gemini if name == "gemini" else None)

    settings = app_env.model_copy(update={"tts_provider": "edge", "image_provider": "pollinations",
                                          "image_fallback_providers": [], "video_provider": "none"})
    free = GenerationOptions(allow_generated_images=True)
    assert _media_billed_to_server(settings, keys("server"), free) is False  # Pollinations only: free

    backup = settings.model_copy(update={"image_fallback_providers": ["gemini"]})
    assert _media_billed_to_server(backup, keys("server"), free) is True  # the backup may bill the server key
    assert _media_billed_to_server(backup, keys("personal"), free) is False  # ...or the teacher's own key
    assert _media_billed_to_server(backup, keys("server"), GenerationOptions(allow_generated_images=False)) is False

    chosen = GenerationOptions(allow_generated_images=True, image_provider="gemini")
    assert _media_billed_to_server(settings, keys("server"), chosen) is True
    off = settings.model_copy(update={"image_provider": "none"})
    assert _media_billed_to_server(off, keys("server"), chosen) is False  # images off: nothing is generated
