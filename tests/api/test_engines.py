"""AI engine choice per lecture: ``/api/meta`` engines, option validation, override policy, stored engines."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from aadhi.models import Job, Project, ProjectVersion
from aadhi.pipeline.base import GenerationOptions
from tests.api.factories import add_job, add_project

TXT = ("ohm.txt", b"Ohm's law: V = I R. Current is the flow of charge.", "text/plain")
ANTHROPIC_KEY = SecretStr("sk-ant-test-123456789")
GEMINI_KEY = SecretStr("AIzaTEST-123456789")


def create(c, options: dict | None = None):
    data = {"options": json.dumps(options)} if options is not None else {}
    return c.post("/api/projects", files={"file": TXT}, data=data)


def options_of(api, job_id: int) -> dict:
    with api.db() as db:
        return dict(db.get(Job, job_id).payload["options"])


def assert_engine_refused(r, label: str) -> None:
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation"
    assert body["detail"] == [{"loc": ["options", "llm_provider"], "type": "engine_not_configured",
                               "msg": f"AI engine '{label}' is not configured on this server."}]


# --- /api/meta ----------------------------------------------------------------------------------------


def test_meta_lists_engines_with_configuration_and_models(api):
    from aadhi.providers.factory import llm_models

    _, c = api.editor("alice")
    llm = c.get("/api/meta").json()["llm"]
    assert llm["provider"] == "fake" and llm["configured"] is True
    assert llm["models"] == llm_models(api.settings)
    assert [e["id"] for e in llm["engines"]] == ["fake", "gemini", "openai", "anthropic"]  # fake: test mode only
    assert {e["id"]: e["configured"] for e in llm["engines"]} == {"fake": True, "gemini": False, "openai": False,
                                                                 "anthropic": False}
    assert {e["id"]: e["label"] for e in llm["engines"]}["anthropic"] == "Anthropic Claude"
    for engine in llm["engines"]:
        assert set(engine) == {"id", "label", "configured", "models", "key_source"}
        assert engine["models"] == llm_models(api.settings, engine["id"])
        assert engine["key_source"] is None  # no key anywhere (fake has no key)

    api.settings.anthropic_api_key = ANTHROPIC_KEY
    api.settings.llm_model_plan = "gpt-5"  # a pre-engine .env: LLM_MODEL_PLAN configured OpenAI
    engines = {e["id"]: e for e in c.get("/api/meta").json()["llm"]["engines"]}
    assert engines["anthropic"]["configured"] is True and engines["gemini"]["configured"] is False
    assert engines["anthropic"]["key_source"] == "env" and engines["gemini"]["key_source"] is None
    assert engines["openai"]["models"]["plan"] == "gpt-5"
    assert engines["gemini"]["models"]["plan"] == "gemini-2.5-pro"
    assert engines["anthropic"]["models"] == {t: getattr(api.settings, f"anthropic_model_{t}")
                                              for t in ("plan", "script", "critic", "fast")}
    assert ANTHROPIC_KEY.get_secret_value() not in c.get("/api/meta").text


def test_meta_models_follow_the_default_engine(api):
    api.settings.llm_provider = "anthropic"
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    api.settings.anthropic_model_fast = "claude-fast-test"
    _, c = api.editor("alice")
    llm = c.get("/api/meta").json()["llm"]
    assert llm["provider"] == "anthropic" and llm["configured"] is True
    assert llm["models"]["fast"] == "claude-fast-test"
    assert c.get("/api/meta").json()["generation_defaults"]["llm_provider"] is None


# --- options entry points -----------------------------------------------------------------------------


def test_create_refuses_an_unconfigured_engine(api):
    _, c = api.editor("alice")
    assert_engine_refused(create(c, {"llm_provider": "anthropic"}), "Anthropic Claude")
    assert_engine_refused(create(c, {"llm_provider": "gemini"}), "Google Gemini")
    bad = create(c, {"llm_provider": "klingon"})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"][:2] == ["options", "llm_provider"]
    with api.db() as db:
        assert db.query(Project).count() == 0  # refused before anything was stored


def test_configured_engine_is_accepted_and_persisted(api):
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    _, c = api.editor("alice")
    r = create(c, {"llm_provider": "anthropic", "target_minutes": 10})
    assert r.status_code == 201, r.text
    assert options_of(api, r.json()["job"]["id"])["llm_provider"] == "anthropic"
    pid = r.json()["project"]["id"]
    with api.db() as db:
        assert db.get(Project, pid).settings["llm_provider"] == "anthropic"
    # regenerate without options keeps the project's engine; an explicit engine replaces it
    again = c.post(f"/api/projects/{pid}/regenerate", json={})
    assert again.status_code == 201, again.text
    assert options_of(api, again.json()["job"]["id"])["llm_provider"] == "anthropic"
    fake = c.post(f"/api/projects/{pid}/regenerate", json={"options": {"llm_provider": "fake"}})
    assert fake.status_code == 201 and options_of(api, fake.json()["job"]["id"])["llm_provider"] == "fake"
    back = c.post(f"/api/projects/{pid}/regenerate", json={"options": {"llm_provider": None}})
    assert back.status_code == 201 and options_of(api, back.json()["job"]["id"])["llm_provider"] is None
    with api.db() as db:
        assert db.get(Project, pid).settings["llm_provider"] is None


def test_project_detail_returns_the_stored_options_for_regenerate(api):
    """The Studio's "Regenerate lecture" form starts from these, so the lecture keeps its engine."""
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    api.settings.llm_model_allowlist = ["claude-sonnet-4-5"]
    api.user("root", role="admin")
    admin = api.login("root")
    created = create(admin, {"llm_provider": "anthropic", "language": "ta-IN", "target_minutes": 12,
                             "extra_instructions": "Use Indian grid examples.", "llm_model_script": "claude-sonnet-4-5"})
    assert created.status_code == 201, created.text
    pid = created.json()["project"]["id"]
    opts = admin.get(f"/api/projects/{pid}").json()["options"]
    assert set(opts) == set(GenerationOptions.model_fields)  # a complete GenerationOptions
    assert (opts["llm_provider"], opts["language"], opts["target_minutes"]) == ("anthropic", "ta-IN", 12)
    assert opts["extra_instructions"] == "Use Indian grid examples."
    assert opts["llm_model_script"] == "claude-sonnet-4-5"  # admins see their override
    # The same project owned by an editor: overrides are never shown to editors.
    alice = api.user("alice")
    with api.db() as db:
        db.get(Project, pid).owner_id = alice.id
        db.commit()
    editor = api.login("alice")
    editor_opts = editor.get(f"/api/projects/{pid}").json()["options"]
    assert editor_opts["llm_provider"] == "anthropic" and editor_opts["language"] == "ta-IN"
    assert editor_opts["llm_model_plan"] is None and editor_opts["llm_model_script"] is None
    # Posting the options back unchanged (what the Studio does) regenerates with the same engine and language.
    again = editor.post(f"/api/projects/{pid}/regenerate", json={"options": editor_opts})
    assert again.status_code == 201, again.text
    regen = options_of(api, again.json()["job"]["id"])
    assert (regen["llm_provider"], regen["language"], regen["target_minutes"]) == ("anthropic", "ta-IN", 12)


def test_project_detail_options_fall_back_for_invalid_stored_settings(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, _ = add_project(db, alice)
        db.get(Project, project.id).settings = {"target_minutes": 999, "llm_provider": "klingon"}  # legacy junk
        db.commit()
    opts = c.get(f"/api/projects/{project.id}").json()["options"]
    assert opts["llm_provider"] is None and opts["target_minutes"] == 15  # validated defaults, like a regenerate


def test_regenerate_refuses_a_stored_engine_that_lost_its_key(api):
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    _, c = api.editor("alice")
    pid = create(c, {"llm_provider": "anthropic"}).json()["project"]["id"]
    api.settings.anthropic_api_key = SecretStr("")
    assert_engine_refused(c.post(f"/api/projects/{pid}/regenerate", json={}), "Anthropic Claude")
    ok = c.post(f"/api/projects/{pid}/regenerate", json={"options": {"llm_provider": "fake"}})
    assert ok.status_code == 201, ok.text


def test_another_engine_works_when_the_default_engine_has_no_key(api):
    api.settings.llm_provider = "gemini"  # default engine without a key
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    _, c = api.editor("alice")
    meta = c.get("/api/meta").json()["llm"]
    assert meta["configured"] is False
    assert {e["id"]: e["configured"] for e in meta["engines"]}["anthropic"] is True
    assert_engine_refused(create(c), "Google Gemini")  # null = the server default engine
    r = create(c, {"llm_provider": "anthropic"})
    assert r.status_code == 201, r.text
    assert options_of(api, r.json()["job"]["id"])["llm_provider"] == "anthropic"


def test_admin_overrides_must_belong_to_the_lectures_engine(api):
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    api.settings.gemini_api_key = GEMINI_KEY
    api.settings.llm_model_allowlist = ["gemini-2.5-pro", "gpt-5", "claude-sonnet-4-5"]
    api.user("root", role="admin")
    c = api.login("root")
    r = create(c, {"llm_provider": "anthropic", "llm_model_plan": "gpt-5", "llm_model_script": "claude-sonnet-4-5"})
    opts = options_of(api, r.json()["job"]["id"])
    assert (opts["llm_model_plan"], opts["llm_model_script"]) == (None, "claude-sonnet-4-5")
    r = create(c, {"llm_provider": "gemini", "llm_model_plan": "gemini-2.5-pro", "llm_model_script": "claude-sonnet-4-5"})
    opts = options_of(api, r.json()["job"]["id"])
    assert (opts["llm_model_plan"], opts["llm_model_script"]) == ("gemini-2.5-pro", None)
    # the offline engine (the test default) accepts any allow-listed model
    r = create(c, {"llm_model_plan": "gpt-5", "llm_model_script": "claude-sonnet-4-5"})
    opts = options_of(api, r.json()["job"]["id"])
    assert (opts["llm_model_plan"], opts["llm_model_script"]) == ("gpt-5", "claude-sonnet-4-5")
    # editors never keep overrides
    _, editor = api.editor("alice")
    r = create(editor, {"llm_provider": "anthropic", "llm_model_script": "claude-sonnet-4-5"})
    assert options_of(api, r.json()["job"]["id"])["llm_model_script"] is None


def test_sanitize_options_policy(app_env):
    from aadhi.api.errors import ApiException
    from aadhi.api.generation import parse_options, sanitize_options
    from aadhi.models import User

    dev = app_env.model_copy(update={"app_env": "development", "llm_provider": "gemini", "gemini_api_key": GEMINI_KEY,
                                     "llm_model_allowlist": ["gemini-2.5-pro", "gpt-5", "custom-model"]})
    admin = User(role="admin")
    with pytest.raises(ApiException) as exc:  # the offline engine is never selectable outside tests
        sanitize_options(parse_options({"llm_provider": "fake"}), admin, dev)
    assert exc.value.status_code == 422 and exc.value.detail[0]["msg"] == (
        "AI engine 'Offline test engine' is not configured on this server.")
    kept = sanitize_options(parse_options({"llm_model_plan": "gemini-2.5-pro", "llm_model_script": "custom-model"}),
                            admin, dev)
    assert (kept.llm_model_plan, kept.llm_model_script) == ("gemini-2.5-pro", None)  # unknown family: dropped
    with pytest.raises(ApiException):
        sanitize_options(parse_options({"llm_provider": "openai", "llm_model_plan": "gpt-5"}), admin, dev)


# --- jobs on an existing version ----------------------------------------------------------------------


def stored_engine_version(api, owner, engine: str):
    """Project + built version 1 generated with ``engine`` (as recorded in generation_meta)."""
    with api.db() as db:
        project, version = add_project(db, owner)
        db.get(ProjectVersion, version.id).generation_meta = {"options": {"llm_provider": engine}}
        db.commit()
    return project, version


def test_version_jobs_need_the_versions_engine(api):
    alice, c = api.editor("alice")
    _, version = stored_engine_version(api, alice, "anthropic")
    regen = c.post(f"/api/versions/{version.id}/scenes/s2/regenerate", json={})
    assert regen.status_code == 409 and regen.json()["code"] == "engine_not_configured"
    assert regen.json()["engine"] == "anthropic" and "Anthropic Claude" in regen.json()["detail"]
    translate = c.post(f"/api/versions/{version.id}/translate", json={"target_language": "ta-IN"})
    assert translate.status_code == 409 and translate.json()["code"] == "engine_not_configured"
    with api.db() as db:
        assert db.query(ProjectVersion).count() == 1  # no translation version was created
        assert db.query(Job).count() == 0
    _, other = stored_engine_version(api, alice, "anthropic")
    assert c.post(f"/api/versions/{other.id}/build", json={}).status_code == 202  # building assets needs no engine

    api.settings.anthropic_api_key = ANTHROPIC_KEY
    assert c.post(f"/api/versions/{version.id}/scenes/s2/regenerate", json={}).status_code == 202
    translate = c.post(f"/api/versions/{version.id}/translate", json={"target_language": "ta-IN"})
    assert translate.status_code == 202, translate.text


def test_versions_without_a_stored_engine_use_the_default(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        _, version = add_project(db, alice)  # generation_meta without options: the project's, else the default
    assert c.post(f"/api/versions/{version.id}/scenes/s2/regenerate", json={}).status_code == 202
    api.settings.llm_provider = "gemini"
    _, fresh = stored_engine_version(api, alice, "openai")
    r = c.post(f"/api/versions/{fresh.id}/translate", json={"target_language": "hi-IN"})
    assert r.status_code == 409 and r.json()["engine"] == "openai"


def test_plan_approval_needs_the_versions_engine(api):
    alice, c = api.editor("alice")
    project, version = stored_engine_version(api, alice, "anthropic")
    with api.db() as db:
        db.get(ProjectVersion, version.id).status = "awaiting_review"
        db.commit()
        add_job(db, kind="generate_lecture", status="awaiting_review", user=alice, project=project, version=version,
                payload={"options": {"llm_provider": "anthropic"}}, result={"stage": "plan_review"})
    r = c.post(f"/api/versions/{version.id}/approve-plan")
    assert r.status_code == 409 and r.json()["code"] == "engine_not_configured"
    api.settings.anthropic_api_key = ANTHROPIC_KEY
    r = c.post(f"/api/versions/{version.id}/approve-plan")
    assert r.status_code == 202, r.text
