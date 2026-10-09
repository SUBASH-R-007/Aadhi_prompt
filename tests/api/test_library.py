"""``/api/library``: browse, add, edit, delete, attach, suggestions, AI describe; the upload hook; isolation."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from aadhi import credentials as creds
from aadhi import library
from aadhi.models import Asset, AssetRef, LibraryItem, UsageEvent, User
from aadhi.providers.base import Usage
from tests.api.factories import add_project, screenplay_dict, tiny_png

ITEM_KEYS = {"id", "asset_key", "kind", "title", "description", "keywords", "source", "mime", "size_bytes", "width",
             "height", "duration_s", "url", "poster_url", "created_at", "updated_at", "last_used_at", "used_in",
             "prompt", "provider", "model"}
UPLOAD_KEYS = {"asset_key", "url", "mime", "kind", "width", "height"}


def png(width: int = 3, height: int = 2) -> bytes:
    return tiny_png(width, height)


def upload(c, project_id: int, *, name="plant_cell.png", data=None, purpose="side_panel"):
    return c.post("/api/uploads", files={"file": (name, png() if data is None else data, "application/octet-stream")},
                  data={"purpose": purpose, "project_id": str(project_id)})


def add(c, *, name="heart.png", data=None, **fields):
    return c.post("/api/library", files={"file": (name, png() if data is None else data, "application/octet-stream")},
                  data={k: v for k, v in fields.items() if v is not None})


def setup(api, name="alice", **kw):
    user, c = api.editor(name)
    with api.db() as db:
        project, version = add_project(db, user, **kw)
    return user, c, project, version


def image_scene_screenplay(prompt: str, title: str = "Plant cell") -> dict:
    sp = screenplay_dict(quiz=False)
    sp["scenes"][0]["side_panel"] = {"kind": "image", "title": title, "image_prompt": prompt, "rationale": "Shows it."}
    return sp


# --- the upload hook -------------------------------------------------------------------------------------


def test_upload_joins_the_uploaders_library_and_keeps_its_response(api):
    _, c, project, _ = setup(api)
    r = upload(c, project.id)
    assert r.status_code == 201, r.text
    assert set(r.json()) == UPLOAD_KEYS  # unchanged shape
    body = c.get("/api/library").json()
    assert body["total"] == 1
    view = body["items"][0]
    assert set(view) == ITEM_KEYS
    assert (view["asset_key"], view["kind"], view["source"], view["title"]) == (
        r.json()["asset_key"], "image", "upload", "plant cell")
    assert (view["width"], view["height"], view["mime"], view["used_in"]) == (3, 2, "image/png", 1)
    assert view["url"] == r.json()["url"] and view["last_used_at"] is not None
    assert c.get(view["url"]).content == png()
    again = upload(c, project.id, name="renamed.png")  # same bytes: one item, title kept
    assert again.json()["asset_key"] == view["asset_key"]
    assert [i["title"] for i in c.get("/api/library").json()["items"]] == ["plant cell"]


def test_a_library_failure_never_fails_the_upload(api, monkeypatch):
    _, c, project, _ = setup(api)

    def broken(*args, **kwargs):
        raise RuntimeError("library down")

    monkeypatch.setattr(library, "add_item", broken)
    r = upload(c, project.id)
    assert r.status_code == 201
    with api.db() as db:
        assert db.execute(select(AssetRef.asset_key)).scalars().all() == [r.json()["asset_key"]]
        assert db.execute(select(LibraryItem)).first() is None


# --- browse ----------------------------------------------------------------------------------------------


def test_list_filters_search_and_pagination(api):
    _, c, project, _ = setup(api)
    assert add(c, name="kidney.png", data=png(4, 4), keywords="nephron, urine").status_code == 201
    assert add(c, name="heart.png", data=png(5, 5), description="Four chambers").status_code == 201
    upload(c, project.id, name="lung.png", data=png(6, 6))
    body = c.get("/api/library").json()
    assert body["total"] == 3 and [i["title"] for i in body["items"]] == ["lung", "heart", "kidney"]
    assert [i["title"] for i in c.get("/api/library?q=nephrons").json()["items"]] == ["kidney"]
    assert [i["title"] for i in c.get("/api/library?q=chamber").json()["items"]] == ["heart"]
    assert c.get("/api/library?kind=video").json() == {"items": [], "total": 0}
    assert c.get("/api/library?source=upload&limit=1&offset=1").json()["items"][0]["title"] == "heart"
    for bad in ("limit=0", "limit=201", "offset=-1", "kind=audio", "source=web"):
        r = c.get(f"/api/library?{bad}")
        assert r.status_code == 422 and r.json()["code"] == "validation", bad


def test_used_in_counts_only_my_own_lectures(api):
    alice, a, project, _ = setup(api)
    bob, b, bob_project, _ = setup(api, "bob")
    key = upload(a, project.id).json()["asset_key"]
    upload(b, bob_project.id)  # the same bytes in bob's lecture
    with api.db() as db:
        second, _ = add_project(db, db.get(User, alice.id), title="Second")
        db.add(AssetRef(project_id=second.id, asset_key=key))
        db.commit()
    assert a.get("/api/library").json()["items"][0]["used_in"] == 2
    assert b.get("/api/library").json()["items"][0]["used_in"] == 1


# --- add without a lecture -------------------------------------------------------------------------------


def test_add_to_library_validates_like_uploads(api):
    _, c = api.editor("alice")
    r = add(c, name="heart.png", title="  Human heart ", description="Four chambers", keywords="heart, Heart, valve")
    assert r.status_code == 201, r.text
    view = r.json()
    assert set(view) == ITEM_KEYS
    assert (view["title"], view["description"], view["keywords"], view["used_in"]) == (
        "Human heart", "Four chambers", ["heart", "valve"], 0)
    assert view["source"] == "upload" and view["last_used_at"] is None
    with api.db() as db:
        asset = db.execute(select(Asset).where(Asset.key == view["asset_key"])).scalar_one()
        assert asset.kind == "upload" and asset.meta == {"purpose": "library"}
        assert db.execute(select(AssetRef)).first() is None  # no lecture uses it yet
    assert add(c, name="x.html", data=b"<!doctype html><script>alert(1)</script>").status_code == 415
    assert add(c, name="x.svg", data=b"<svg xmlns='http://www.w3.org/2000/svg'/>").status_code == 415
    assert add(c, name="x.pdf", data=b"%PDF-1.4 ...").status_code == 415
    too_long = add(c, title="t" * 121)
    assert too_long.status_code == 422 and too_long.json()["detail"][0]["loc"] == ["body", "title"]
    too_many = add(c, keywords=",".join(f"k{i}" for i in range(21)))
    assert too_many.status_code == 422 and too_many.json()["detail"][0]["loc"] == ["body", "keywords"]
    api.settings.upload_max_mb = 1
    big = add(c, data=png() + b"\0" * (1024 * 1024 + 10))
    assert big.status_code == 413


def test_adding_a_kept_file_again_applies_only_the_typed_words(api):
    _, c = api.editor("alice")
    first = add(c, title="Heart", description="Four chambers").json()
    second = add(c, name="other.png", description="", keywords="valve").json()
    assert second["id"] == first["id"]
    assert (second["title"], second["description"], second["keywords"]) == ("Heart", "Four chambers", ["valve"])


# --- edit and delete -------------------------------------------------------------------------------------


def test_patch_edits_the_teachers_words(api):
    _, c = api.editor("alice")
    item = add(c).json()
    r = c.patch(f"/api/library/{item['id']}", json={"title": " Heart  diagram ", "keywords": ["Valve", "valve, aorta"]})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["keywords"], r.json()["description"]) == ("Heart diagram", ["Valve", "aorta"], "")
    assert c.patch(f"/api/library/{item['id']}", json={"description": "Pumps blood"}).json()["title"] == "Heart diagram"
    assert c.get("/api/library?q=aorta").json()["total"] == 1
    cleared = c.patch(f"/api/library/{item['id']}", json={"keywords": [], "description": ""}).json()
    assert (cleared["keywords"], cleared["description"]) == ([], "")
    for body in ({"title": ""}, {"title": "x" * 121}, {"description": "d" * 1001}, {"keywords": ["k" * 41]},
                 {"keywords": [f"k{i}" for i in range(21)]}, {"owner": 2}):
        bad = c.patch(f"/api/library/{item['id']}", json=body)
        assert bad.status_code == 422 and bad.json()["code"] == "validation", body
    surrogate = c.patch(f"/api/library/{item['id']}", content=b'{"title": "Heart\\ud800"}',
                        headers={"Content-Type": "application/json"})  # a lone surrogate is never stored
    assert surrogate.status_code == 422 and surrogate.json()["code"] == "validation"
    assert c.patch("/api/library/999999", json={"title": "x"}).status_code == 404


def test_delete_removes_the_item_only(api):
    _, c, project, version = setup(api)
    key = upload(c, project.id).json()["asset_key"]
    item_id = c.get("/api/library").json()["items"][0]["id"]
    assert c.delete(f"/api/library/{item_id}").status_code == 204
    assert c.get("/api/library").json()["total"] == 0
    assert c.delete(f"/api/library/{item_id}").status_code == 404
    sp = image_scene_screenplay("A plant cell")
    sp["scenes"][0]["side_panel"]["override_asset_key"] = key
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1})
    assert r.status_code == 200, r.text  # the lecture can still use the media


def test_mutations_need_the_csrf_header(api):
    _, c = api.editor("alice")
    item = add(c).json()
    plain = api.login("alice", browser=False)
    assert plain.patch(f"/api/library/{item['id']}", json={"title": "x"}).status_code == 403
    assert plain.delete(f"/api/library/{item['id']}").status_code == 403


# --- isolation -------------------------------------------------------------------------------------------


def test_other_users_and_admins_never_see_or_touch_my_items(api):
    _, a, project, version = setup(api)
    _, b = api.editor("bob")
    api.user("root", role="admin")
    admin = api.login("root")
    mine = add(a, title="Alice heart", keywords="secret words").json()
    theirs = add(b, title="Bob pump").json()  # identical bytes: same asset, separate item
    assert theirs["asset_key"] == mine["asset_key"] and theirs["id"] != mine["id"]
    assert theirs["keywords"] == [] and theirs["title"] == "Bob pump"
    for client in (b, admin):
        assert mine["id"] not in [i["id"] for i in client.get("/api/library").json()["items"]]
        assert client.get("/api/library?q=secret").json()["total"] == 0
        for method, path, body in (
            ("PATCH", f"/api/library/{mine['id']}", {"title": "pwned"}),
            ("DELETE", f"/api/library/{mine['id']}", None),
            ("POST", f"/api/library/{mine['id']}/attach", {"project_id": project.id}),
            ("POST", f"/api/library/{mine['id']}/describe", None),
        ):
            r = client.request(method, path, json=body)
            assert r.status_code in (404, 403), (method, path, r.status_code)
            if r.status_code == 403:
                assert path.endswith("/describe") and r.json()["code"] == "feature_disabled"
    assert a.get("/api/library").json()["items"][0]["title"] == "Alice heart"
    assert b.get(f"/api/library/suggestions?version_id={version.id}").status_code == 404


# --- attach ----------------------------------------------------------------------------------------------


def test_attach_makes_the_media_usable_in_my_lecture(api):
    alice, c, project, version = setup(api)
    item = add(c, title="Plant cell").json()
    sp = image_scene_screenplay("A plant cell")
    sp["scenes"][0]["side_panel"]["override_asset_key"] = item["asset_key"]
    refused = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1})
    assert refused.status_code == 422  # not attached yet: the key is unknown to the lecture
    r = c.post(f"/api/library/{item['id']}/attach", json={"project_id": project.id})
    assert r.status_code == 200 and r.json() == {"asset_key": item["asset_key"]}
    assert c.post(f"/api/library/{item['id']}/attach", json={"project_id": project.id}).status_code == 200
    ok = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1})
    assert ok.status_code == 200, ok.text
    view = c.get("/api/library").json()["items"][0]
    assert view["used_in"] == 1 and view["last_used_at"] is not None
    with api.db() as db:
        assert db.execute(select(AssetRef.project_id)).scalars().all() == [project.id]


def test_attach_only_to_my_own_lectures(api):
    alice, a, alice_project, _ = setup(api)
    bob, b, bob_project, _ = setup(api, "bob")
    api.user("root", role="admin")
    admin = api.login("root")
    item = add(b).json()
    assert b.post(f"/api/library/{item['id']}/attach", json={"project_id": alice_project.id}).status_code == 404
    admin_item = add(admin, data=png(7, 7)).json()
    r = admin.post(f"/api/library/{admin_item['id']}/attach", json={"project_id": alice_project.id})
    assert r.status_code == 404  # admins too: the contract is "the user's own lecture"
    assert b.post(f"/api/library/{item['id']}/attach", json={"project_id": 999999}).status_code == 404
    assert b.post(f"/api/library/{item['id']}/attach", json={}).status_code == 422
    with api.db() as db:
        assert db.execute(select(AssetRef)).first() is None


# --- suggestions -----------------------------------------------------------------------------------------


def test_suggestions_match_my_items_to_the_versions_scenes(api):
    alice, a, project, version = setup(api, screenplay=None)
    sp = image_scene_screenplay("A plant cell with its nucleus and cell wall")
    assert a.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1}).status_code == 200
    add(a, title="Plant cell", keywords="nucleus, cell wall")
    add(a, name="river.png", data=png(9, 9), title="River delta")
    _, b = api.editor("bob")
    add(b, name="bob.png", data=png(8, 8), title="Plant cell", keywords="nucleus")
    r = a.get(f"/api/library/suggestions?version_id={version.id}")
    assert r.status_code == 200, r.text
    scenes = r.json()["scenes"]
    assert [s["scene_id"] for s in scenes] == ["s1"]
    matches = scenes[0]["matches"]
    assert [m["item"]["title"] for m in matches] == ["Plant cell"]
    assert set(matches[0]["item"]) == ITEM_KEYS and matches[0]["score"] >= library.SUGGESTION_THRESHOLD
    assert a.get("/api/library/suggestions").status_code == 422
    assert a.get("/api/library/suggestions?version_id=999999").status_code == 404


def test_suggestions_are_rate_limited(api, monkeypatch):
    from aadhi.api.routers import library as router

    monkeypatch.setattr(router, "SUGGESTIONS_PER_MINUTE", 2)
    _, a, _, version = setup(api)
    assert [a.get(f"/api/library/suggestions?version_id={version.id}").status_code for _ in range(3)] == [200, 200, 429]


# --- AI describe -----------------------------------------------------------------------------------------


@pytest.fixture()
def fake_describe():
    from aadhi.providers.llm.fake import FakeLLM

    calls: list[dict] = []

    def responder(prompt, schema, images=(), system=""):
        calls.append({"prompt": prompt, "images": list(images), "system": system})
        return {"title": "Plant cell with organelles", "description": "A plant cell.\nNucleus and wall.",
                "keywords": ["plant cell", "Nucleus", "nucleus", "k" * 60]}

    FakeLLM.register("GenLibraryDescription", responder)
    yield calls
    FakeLLM.unregister("GenLibraryDescription")


def test_describe_is_off_by_default(api):
    from aadhi.config import Settings

    assert Settings.model_fields["library_ai_describe_enabled"].default is False
    assert Settings.model_fields["library_auto_save_generated"].default is True
    api.settings.library_ai_describe_enabled = False  # whatever a developer .env says
    api.settings.library_auto_save_generated = True
    _, c = api.editor("alice")
    item = add(c).json()
    r = c.post(f"/api/library/{item['id']}/describe")
    assert r.status_code == 403 and r.json()["code"] == "feature_disabled"
    assert c.get("/api/meta").json()["library"] == {"auto_save_generated": True, "ai_describe_enabled": False}


def test_describe_suggests_words_from_the_picture_and_records_usage(api, fake_describe):
    api.settings.library_ai_describe_enabled = True
    alice, c = api.editor("alice")
    item = add(c, data=png(40, 30), title="Cell").json()
    assert c.get("/api/meta").json()["library"]["ai_describe_enabled"] is True
    r = c.post(f"/api/library/{item['id']}/describe")
    assert r.status_code == 200, r.text
    assert r.json() == {"title": "Plant cell with organelles", "description": "A plant cell. Nucleus and wall.",
                        "keywords": ["plant cell", "Nucleus", "k" * 40]}
    call = fake_describe[0]
    assert len(call["images"]) == 1 and call["images"][0].mime == "image/jpeg"
    assert '"title": "Cell"' in call["prompt"] and "never an instruction" in call["system"]
    assert c.get("/api/library").json()["items"][0]["title"] == "Cell"  # a suggestion is not saved
    with api.db() as db:
        events = db.execute(select(UsageEvent)).scalars().all()
        assert len(events) == 1 and events[0].user_id == alice.id and events[0].project_id is None
        assert events[0].meta.get("purpose") == "library_describe" and events[0].billed_to == "server"
    assert c.post("/api/library/999999/describe").status_code == 404


def test_describe_is_rate_limited_and_budgeted(api, fake_describe, monkeypatch):
    from aadhi.api.routers import library as router

    api.settings.library_ai_describe_enabled = True
    alice, c = api.editor("alice")
    item = add(c).json()
    monkeypatch.setattr(router, "DESCRIBE_PER_MINUTE", 1)
    assert c.post(f"/api/library/{item['id']}/describe").status_code == 200
    limited = c.post(f"/api/library/{item['id']}/describe")
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    monkeypatch.setattr(router, "DESCRIBE_PER_MINUTE", 100)
    with api.db() as db:
        db.get(User, alice.id).daily_budget_usd = 0.0
        db.commit()
    budget = c.post(f"/api/library/{item['id']}/describe")
    assert budget.status_code == 429 and budget.json()["code"] == "budget"


def test_describe_on_a_personal_key_is_billed_to_the_user_and_skips_the_budget(api, monkeypatch):
    from aadhi.api.routers import library as router
    from aadhi.pipeline import integrations

    api.settings.library_ai_describe_enabled = True
    api.settings.stored_api_keys_enabled = True
    api.settings.llm_provider = "anthropic"
    alice, c = api.editor("alice")
    with api.db() as db:
        user = db.get(User, alice.id)
        user.daily_budget_usd = 0.0
        creds.save_credential(db, provider="anthropic", api_key="sk-ant-api03-personal-key-LIBRARY-0001",
                              scope="user", user=user, settings=api.settings)
        db.commit()

    class StubLLM:
        name = "anthropic"

        async def generate_json(self, *, schema, on_usage=None, **kwargs):
            on_usage(Usage(provider="anthropic", model="claude-test", operation="llm", input_tokens=900,
                           output_tokens=80))
            return schema(title="Heart", description="A heart.", keywords=["heart"])

    seen: list[str | None] = []

    def fake_get_llm(settings, engine=None):
        seen.append(engine)
        return StubLLM()

    monkeypatch.setattr(router.integrations, "get_llm", fake_get_llm)
    monkeypatch.setattr(integrations, "llm_model", lambda settings, tier, options=None, **kw: "claude-test")
    item = add(c).json()
    r = c.post(f"/api/library/{item['id']}/describe")
    assert r.status_code == 200, r.text
    assert seen == ["anthropic"]
    with api.db() as db:
        event = db.execute(select(UsageEvent)).scalar_one()
        assert event.billed_to == "user" and event.provider == "anthropic"


def test_describe_failures_are_one_clear_answer(api, monkeypatch):
    from aadhi.api.routers import library as router
    from aadhi.providers.base import ProviderError, ProviderNotConfigured

    api.settings.library_ai_describe_enabled = True
    _, c = api.editor("alice")
    item = add(c).json()

    class Failing:
        name = "fake"

        async def generate_json(self, **kwargs):
            raise ProviderError("upstream exploded with key=sk-secret-value-123456789", status=500, provider="fake")

    monkeypatch.setattr(router.integrations, "get_llm", lambda settings, engine=None: Failing())
    r = c.post(f"/api/library/{item['id']}/describe")
    assert r.status_code == 502 and r.json()["code"] == "describe_failed" and "sk-secret" not in r.text

    def missing(settings, engine=None):
        raise ProviderNotConfigured("no key", provider="anthropic")

    monkeypatch.setattr(router.integrations, "get_llm", missing)
    r = c.post(f"/api/library/{item['id']}/describe")
    assert r.status_code == 503 and r.json()["code"] == "unavailable"


# --- admin statistics --------------------------------------------------------------------------------------


def test_media_cache_stats_are_for_admins_only(api):
    _, c = api.editor("alice")
    add(c)
    assert c.get("/api/admin/media-cache").status_code == 403
    api.user("root", role="admin")
    admin = api.login("root")
    body = admin.get("/api/admin/media-cache?days=7").json()
    assert body["days"] == 7 and set(body["kinds"]) == {"image", "video"}
    assert body["library"] == {"items": 1, "by_source": {"upload": 1, "generated": 0, "figure": 0}}
    assert admin.get("/api/admin/media-cache?days=0").status_code == 422


# --- video (ffmpeg) ----------------------------------------------------------------------------------------


@pytest.mark.slow
def test_video_items_are_probed_and_described_from_a_frame(api, fake_describe, tmp_path):
    import shutil
    import subprocess

    ffmpeg = shutil.which(api.settings.ffmpeg_path)
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=purple:s=64x48:d=2", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(clip)], check=True, timeout=60)
    api.settings.library_ai_describe_enabled = True
    _, c = api.editor("alice")
    r = add(c, name="mitosis_timelapse.mp4", data=clip.read_bytes())
    assert r.status_code == 201, r.text
    view = r.json()
    assert (view["kind"], view["title"], view["width"], view["height"]) == ("video", "mitosis timelapse", 64, 48)
    assert abs(view["duration_s"] - 2.0) < 0.3
    assert c.get("/api/library?kind=video").json()["total"] == 1
    assert c.post(f"/api/library/{view['id']}/describe").status_code == 200
    frame = fake_describe[0]["images"][0]
    assert frame.mime == "image/png" and frame.data.startswith(b"\x89PNG")
    from PIL import Image

    with Image.open(io.BytesIO(frame.data)) as img:
        assert img.size == (64, 48)


def test_suggestions_for_an_unreadable_screenplay_are_empty(api):
    from aadhi.models import ProjectVersion

    _, a, _, version = setup(api)
    with api.db() as db:
        db.get(ProjectVersion, version.id).screenplay = {"scenes": "not a list"}
        db.commit()
    r = a.get(f"/api/library/suggestions?version_id={version.id}")
    assert r.status_code == 200 and r.json() == {"scenes": []}


def test_list_by_several_sources(api):
    alice, a, project, _ = setup(api)
    assert add(a, name="mine.png", title="Mine").status_code == 201
    with api.db() as db:  # a generated picture of one of alice's lectures
        db.add(Asset(key="image-" + "1" * 40, kind="image", storage_key="assets/image/x/1.png", mime="image/png",
                     size_bytes=1, meta={}))
        db.flush()
        library.add_item(db, user_id=alice.id, asset_key="image-" + "1" * 40, kind="image", source="generated",
                         title="Made", origin_project_id=project.id)
        db.commit()
    both = a.get("/api/library?source=upload,figure").json()
    assert both["total"] == 1 and [i["title"] for i in both["items"]] == ["Mine"]
    assert a.get("/api/library?source=generated,upload").json()["total"] == 2
    assert a.get("/api/library?source=upload,").json()["total"] == 1  # empty parts are ignored
    r = a.get("/api/library?source=upload,secret")
    assert r.status_code == 422 and r.json()["code"] == "validation"
