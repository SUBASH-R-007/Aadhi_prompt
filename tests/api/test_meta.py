"""``GET /api/meta``."""

from __future__ import annotations


def test_meta_shape(api):
    _, c = api.editor("alice")
    r = c.get("/api/meta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "2.0.0"
    assert {"code": "en-IN", "label": "English (India)"} in body["languages"]
    assert {lang["code"] for lang in body["languages"]} >= {"ta-IN", "hi-IN"}
    assert body["tts"]["default_provider"] == "fake"
    assert isinstance(body["tts"]["providers"], list)
    assert body["llm"]["provider"] == "fake" and body["llm"]["configured"] is True
    assert set(body["llm"]["models"]) == {"plan", "script", "critic", "fast"}
    assert body["llm"]["override_allowlist"] == []
    assert body["api_keys"] == {"enabled": False, "personal_enabled": False}  # switched off in tests
    features = body["features"]
    assert features["storage"] == "local" and features["gifs"] is False
    assert features["manim"] is True and isinstance(features["render"], bool)
    assert body["limits"]["upload_max_mb"] == api.settings.upload_max_mb
    assert body["limits"]["max_source_chars"] == api.settings.max_source_chars  # the paste box's limit
    assert body["limits"]["daily_budget_usd"] == api.settings.daily_budget_usd_per_user
    assert body["generation_defaults"]["language"] == "en-IN"
    assert body["generation_defaults"]["target_minutes"] == 15
    # every option the form edits, also those stored options leave out at their default
    assert body["generation_defaults"]["prefer_library_visuals"] is False
    for t in body["manim_templates"]:
        assert set(t) == {"name", "title", "description", "steps_hint", "params_schema", "example_params"}


def test_meta_allowlist_only_for_admins(api):
    api.settings.llm_model_allowlist = ["gemini-2.5-pro"]
    _, editor = api.editor("alice")
    assert editor.get("/api/meta").json()["llm"]["override_allowlist"] == []
    api.user("root", role="admin")
    assert api.login("root").get("/api/meta").json()["llm"]["override_allowlist"] == ["gemini-2.5-pro"]


def test_meta_user_budget_override(api):
    from aadhi.models import User

    alice, c = api.editor("alice")
    with api.db() as db:
        db.get(User, alice.id).daily_budget_usd = 2.5
        db.commit()
    assert c.get("/api/meta").json()["limits"]["daily_budget_usd"] == 2.5


def test_meta_api_key_switches(api):
    _, c = api.editor("alice")
    api.settings.stored_api_keys_enabled = True
    assert c.get("/api/meta").json()["api_keys"] == {"enabled": True, "personal_enabled": True}
    api.settings.user_api_keys_enabled = False
    assert c.get("/api/meta").json()["api_keys"] == {"enabled": True, "personal_enabled": False}


def test_meta_features_follow_the_users_keys(api):
    """A personal Gemini key enables Gemini images for that user (the same key powers them)."""
    from aadhi.credentials import save_credential
    from aadhi.models import User

    api.settings.stored_api_keys_enabled = True
    api.settings.image_provider = "gemini"
    alice, c = api.editor("alice")
    _, bob = api.editor("bob")
    with api.db() as db:
        save_credential(db, provider="gemini", api_key="AIzaPersonalGeminiKey-0123456789", scope="user",
                        user=db.get(User, alice.id))
        db.commit()
    assert c.get("/api/meta").json()["features"]["generated_images"] is True
    assert bob.get("/api/meta").json()["features"]["generated_images"] is False


def test_manim_sandbox_check_is_admin_only_and_rate_limited(api, monkeypatch):
    from aadhi.manim import health

    probes = []

    def fake(settings):
        probes.append(settings.manim_sandbox)
        return health.SandboxHealth(available=True, sandbox=settings.manim_sandbox, isolated=False,
                                    isolation="audit hook only", manim_version="0.19.0", manim_version_ok=True,
                                    latex=False, network_denied=True, env_clean=True, probe_ran=True)

    monkeypatch.setattr(health, "sandbox_health", fake)
    _, editor = api.editor("alice")
    assert editor.get("/api/admin/manim/sandbox").status_code == 403
    assert probes == []
    api.user("root", role="admin")
    admin = api.login("root")
    r = admin.get("/api/admin/manim/sandbox")
    assert r.status_code == 200 and r.json()["sandbox"] == "subprocess" and r.json()["network_denied"] is True
    assert ":\\" not in r.text and "/tmp" not in r.text  # no host paths
    from aadhi.api.routers.meta import MANIM_CHECK_PER_MINUTE

    codes = [admin.get("/api/admin/manim/sandbox").status_code for _ in range(MANIM_CHECK_PER_MINUTE)]
    assert codes[-1] == 429 and len(probes) == MANIM_CHECK_PER_MINUTE
