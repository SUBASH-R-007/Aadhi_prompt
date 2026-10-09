"""Visual Review API: the per-scene view, sign-offs with fingerprint staleness, and the visual actions
(new AI version, choose from the library, remove, retry with paid confirmation). Authorisation: owner or admin
via ``load_version``; library items are only ever the requester's own."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.models import Asset, AssetRef, Job, ProjectVersion, User, VisualReview
from aadhi.pipeline.assets import CONFIRM_PAID_KEY, scene_hash
from aadhi.schemas.manifest import AssetManifest, MediaInfo, SceneMedia
from aadhi.schemas.screenplay import Screenplay
from tests.api.factories import add_job, add_project


def beat(i: str, text: str) -> dict[str, Any]:
    return {"id": i, "narration": text}


def screenplay_dict() -> dict[str, Any]:
    return {"session_title": "Transistors", "language": "en-IN", "scenes": [
        {"id": "s1", "type": "content", "title": "The transistor", "beats": [beat("s1-b1", "Here is a transistor.")],
         "board": [{"id": "s1-i1", "kind": "bullet", "text": "Three legs"}],
         "side_panel": {"kind": "image", "title": "A real one", "rationale": "shows the part",
                        "image_prompt": "a transistor on a breadboard"}},
        {"id": "s2", "type": "content", "title": "Numbers", "beats": [beat("s2-b1", "Look at the numbers.")],
         "side_panel": {"kind": "chart", "rationale": "data", "chart": {"labels": ["a", "b"],
                                                                         "datasets": [{"data": [1, 2]}]}}},
        {"id": "s3", "type": "content", "title": "Plain", "beats": [beat("s3-b1", "Just words here.")]},
        {"id": "lab", "type": "ai_video", "title": "Lab", "video_prompt": "a lathe cutting metal",
         "rationale": "real", "fallback_image_prompt": "a lathe", "beats": [beat("lab-b1", "See the lathe.")]},
        {"id": "sim", "type": "simulation", "title": "Animated", "manim": {"code": "class A(AadhiScene):\n    pass"},
         "beats": [beat("sim-b1", "Watch the dot.")]},
        {"id": "play", "type": "interactive", "title": "Play", "p5_code": "function setup(){}",
         "beats": [beat("play-b1", "Try the slider.")]},
        {"id": "q1", "type": "quiz_checkpoint", "title": "Check", "question": "Which?", "options": ["a", "b"],
         "correct_index": 0, "beats": [beat("q1-b1", "Which one?")], "reveal_beats": [beat("q1-r1", "It is a.")]},
    ]}


IMAGE_KEY, VIDEO_KEY, MANIM_KEY = "image-" + "a" * 40, "video-" + "b" * 40, "manim-" + "c" * 40


def _asset(db: Any, key: str, kind: str, mime: str, meta: dict[str, Any] | None = None) -> None:
    ext = mime.split("/")[1]
    db.add(Asset(key=key, kind=kind, storage_key=f"assets/{kind}/{key}/t.{ext}", mime=mime, size_bytes=1,
                 meta=meta or {}))


def _info(key: str, kind: str, source: str, mime: str) -> MediaInfo:
    ext = mime.split("/")[1]
    folder = {"image": "image", "veo": "video", "manim": "manim", "upload": "upload", "fallback": "image"}[source]
    return MediaInfo(asset_key=key, storage_key=f"assets/{folder}/{key}/t.{ext}", kind=kind, mime=mime, source=source)


def seed(api: Any, username: str = "alice", **kw: Any) -> tuple[Any, Any, Any, Any]:
    """A built version of a lecture with AI video on: s1 generated image, lab generated clip, sim animation; every
    hash current."""
    user, client = api.editor(username)
    sp = Screenplay.model_validate(screenplay_dict())
    with api.db() as db:
        project, version = add_project(db, user, screenplay=sp, **kw)
        _asset(db, IMAGE_KEY, "image", "image/png", {"provider": "pollinations", "model": "", "prompt": "x"})
        _asset(db, VIDEO_KEY, "video", "video/mp4", {"provider": "veo", "model": "veo-2.0-generate-001"})
        _asset(db, MANIM_KEY, "manim", "video/mp4")
        v = db.get(ProjectVersion, version.id)
        v.generation_meta = {"options": {"allow_ai_video": True}}  # the options the clip was built with
        v.set_manifest(AssetManifest(
            media={"s1": SceneMedia(scene_id="s1", side_panel=_info(IMAGE_KEY, "image", "image", "image/png")),
                   "s2": SceneMedia(scene_id="s2"), "s3": SceneMedia(scene_id="s3"),
                   "lab": SceneMedia(scene_id="lab", main=_info(VIDEO_KEY, "video", "veo", "video/mp4")),
                   "sim": SceneMedia(scene_id="sim", main=_info(MANIM_KEY, "video", "manim", "video/mp4")),
                   "play": SceneMedia(scene_id="play"), "q1": SceneMedia(scene_id="q1")},
            scene_hashes={s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}))
        db.commit()
    return user, client, project, version


def library_item(api: Any, user: Any, key: str, *, kind: str = "image", title: str = "transistor breadboard",
                 mime: str = "image/png") -> int:
    from aadhi import library

    with api.db() as db:
        if db.query(Asset.id).filter(Asset.key == key).scalar() is None:
            _asset(db, key, "upload", mime)
            db.flush()
        item = library.add_item(db, user_id=user.id, asset_key=key, kind=kind, source="upload", title=title)
        db.commit()
        return item.id


def scenes(r: Any) -> dict[str, dict[str, Any]]:
    assert r.status_code == 200, r.text
    return {s["scene_id"]: s for s in r.json()["scenes"]}


def payload_of(api: Any, job_id: int) -> dict[str, Any]:
    with api.db() as db:
        return dict(db.get(Job, job_id).payload)


def stored(api: Any, vid: int) -> Screenplay:
    with api.db() as db:
        return db.get(ProjectVersion, vid).get_screenplay()


def put_sp(client: Any, vid: int, data: dict[str, Any], revision: int) -> Any:
    return client.put(f"/api/versions/{vid}/screenplay", json={"screenplay": data, "revision": revision})


# --- the view --------------------------------------------------------------------------------------------


def test_every_scene_with_kind_source_status_and_actions(api):
    _, c, _, version = seed(api)
    r = c.get(f"/api/versions/{version.id}/visual-review")
    s = scenes(r)
    assert list(s) == ["s1", "s2", "s3", "lab", "sim", "play", "q1"] and s["lab"]["index"] == 3
    s1 = s["s1"]
    assert (s1["kind"], s1["source"], s1["status"], s1["provider"], s1["prompt"]) == (
        "image", "generated", "ready", "pollinations", "a transistor on a breadboard")
    assert s1["url"] == f"/media/assets/image/{IMAGE_KEY}/t.png" and s1["poster_url"] is None
    assert s1["review"] == {"state": "pending", "stale": False, "note": None, "updated_at": None}
    assert s1["actions"] == ["approve", "new_version", "choose_library", "upload", "remove"]
    assert s1["variant"] == 0 and s1["visual_source"] == "generated" and s1["findings"] == []
    assert (s["s2"]["kind"], s["s2"]["source"], s["s2"]["url"]) == ("chart", "builtin", None)
    assert (s["s3"]["kind"], s["s3"]["source"], s["s3"]["actions"]) == ("none", "none", ["choose_library", "upload"])
    lab = s["lab"]
    assert (lab["kind"], lab["source"], lab["provider"], lab["model"]) == (
        "video", "generated", "veo", "veo-2.0-generate-001")
    assert lab["actions"] == ["approve", "new_version", "choose_library", "upload"]  # its own clip: no remove
    assert (s["sim"]["kind"], s["sim"]["source"], s["sim"]["provider"]) == ("manim", "manim", None)
    assert (s["play"]["kind"], s["play"]["source"], s["play"]["status"]) == ("interactive", "builtin", "ready")
    assert (s["q1"]["kind"], s["q1"]["actions"]) == ("none", [])
    assert r.json()["summary"] == {"total": 5, "approved": 0, "pending": 5, "changed": 0, "removed": 0,
                                   "needs_attention": 0}
    assert r.json()["revision"] == 1  # the screenplay revision the scenes were read at (for the actions)


def test_statuses_stale_missing_fallback_failed_and_findings(api):
    _, c, _, version = seed(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        m = v.get_manifest()
        m.media["s1"] = SceneMedia(scene_id="s1", warnings=["Generated images are disabled; the image panel is hidden."])
        still = _info(IMAGE_KEY, "image", "fallback", "image/png")
        m.media["lab"] = SceneMedia(scene_id="lab", main=still, main_is_fallback=True,
                                    warnings=["AI video limit reached for this lecture; showing a still image instead."])
        m.media["sim"] = SceneMedia(scene_id="sim", warnings=["The animation could not be rendered; ..."])
        v.set_manifest(m)
        v.issues = [{"code": "manim.render_failed", "severity": "warning", "message": "The animation failed (timeout).",
                     "scene_id": "sim", "source": "manim", "fixable": False},
                    {"code": "lint.something", "severity": "info", "message": "not a media finding", "scene_id": "sim",
                     "source": "lint", "fixable": True}]
        db.commit()
    s = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    # settled causes (a server setting, the lecture's AI video limit): building again cannot help, so no retry;
    # the reason says what can
    assert (s["s1"]["status"], s["s1"]["status_reason"]) == (
        "missing", "Generated images are disabled; the image panel is hidden. Choose a picture or video from your "
                   "library, or upload one.")
    assert "retry" not in s["s1"]["actions"]
    assert s["lab"]["status"] == "fallback" and "limit" in s["lab"]["status_reason"]
    assert s["lab"]["source"] == "fallback" and "retry" not in s["lab"]["actions"]
    assert "new_version" in s["lab"]["actions"]  # over the lecture's AI video limit: a new version is a new still
    for sid in ("s1", "lab"):  # a stale page's retry is refused before anything is charged
        r = c.post(f"/api/versions/{version.id}/scenes/{sid}/visual", json={"action": "retry", "revision": 1})
        assert r.status_code == 409 and r.json()["code"] == "action_unavailable", sid
    with api.db() as db:
        assert db.query(Job).count() == 0
    assert s["sim"]["status"] == "failed" and s["sim"]["findings"] == [
        {"code": "manim.render_failed", "severity": "warning", "message": "The animation failed (timeout)."}]
    # an edit makes the scene stale (not built since)
    data = stored(api, version.id).model_dump(mode="json")
    data["scenes"][0]["side_panel"]["image_prompt"] = "a transistor in a radio"
    assert put_sp(c, version.id, data, 1).status_code == 200
    r = c.get(f"/api/versions/{version.id}/visual-review")
    assert scenes(r)["s1"]["status"] == "stale" and r.json()["summary"]["needs_attention"] == 3


def test_ambiguous_paid_clip_needs_a_confirmed_retry(api):
    _, c, _, version = seed(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.issues = [{"code": "video.ambiguous_submission", "severity": "warning", "message": "may be billed",
                     "scene_id": "lab", "source": "assets", "fixable": False}]
        db.commit()
    lab = scenes(c.get(f"/api/versions/{version.id}/visual-review"))["lab"]
    assert lab["status"] == "ambiguous" and lab["actions"][-2:] == ["retry", "confirm_paid_retry"]
    r = c.post(f"/api/versions/{version.id}/scenes/lab/visual", json={"action": "retry", "revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "confirm_paid_required"
    with api.db() as db:
        assert db.query(Job).count() == 0
    r = c.post(f"/api/versions/{version.id}/scenes/lab/visual",
               json={"action": "retry", "revision": 1, "confirm_paid": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 1 and body["job_id"] is not None
    assert payload_of(api, body["job_id"]) == {"scene_ids": ["lab"], "base_revision": 1, CONFIRM_PAID_KEY: ["lab"]}


# --- sign-off --------------------------------------------------------------------------------------------


def test_an_approval_survives_narration_edits_but_not_visual_edits(api):
    _, c, _, version = seed(api)
    r = c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "approved", "note": " Looks right "})
    assert r.status_code == 200, r.text
    review = r.json()["review"]
    assert review["state"] == "approved" and review["stale"] is False and review["note"] == "Looks right"
    assert review["updated_at"]
    with api.db() as db:
        assert db.get(ProjectVersion, version.id).revision == 1  # approving never touches the screenplay
    data = stored(api, version.id).model_dump(mode="json")
    data["scenes"][0]["beats"][0]["narration"] = "Here is a transistor, look closely."
    data["scenes"][0]["side_panel"]["title"] = "Another title"
    data["scenes"][0]["side_panel"]["rationale"] = "another reason"
    assert put_sp(c, version.id, data, 1).status_code == 200
    s1 = scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]
    assert s1["review"]["state"] == "approved" and not s1["review"]["stale"]
    data["scenes"][0]["side_panel"]["image_prompt"] = "a transistor in a radio"
    assert put_sp(c, version.id, data, 2).status_code == 200
    s1 = scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]
    assert s1["review"]["state"] == "pending" and s1["review"]["stale"] is True and s1["review"]["note"] == "Looks right"


def test_reset_keeps_or_clears_the_note_and_counts(api):
    _, c, _, version = seed(api)
    c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "approved", "note": "ok"})
    c.put(f"/api/versions/{version.id}/visual-review/s2", json={"state": "approved"})
    r = c.get(f"/api/versions/{version.id}/visual-review")
    assert r.json()["summary"]["approved"] == 2 and r.json()["summary"]["pending"] == 3
    r = c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "pending"})
    assert r.json()["review"] == {**r.json()["review"], "state": "pending", "note": "ok"}
    r = c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "pending", "note": None})
    assert r.json()["review"]["note"] is None
    with api.db() as db:
        assert db.query(VisualReview).filter(VisualReview.version_id == version.id).count() == 2


def test_sign_off_input_is_validated(api):
    _, c, _, version = seed(api)
    url = f"/api/versions/{version.id}/visual-review"
    assert c.put(f"{url}/q1", json={"state": "approved"}).json()["code"] == "no_visual"
    assert c.put(f"{url}/q1", json={"state": "pending"}).status_code == 200
    assert c.put(f"{url}/zzz", json={"state": "approved"}).status_code == 404
    assert c.put(f"{url}/s1", json={"state": "changed"}).status_code == 422  # only approved | pending
    assert c.put(f"{url}/s1", json={"state": "approved", "note": "x" * 301}).status_code == 422
    assert c.put(f"{url}/s1", json={"state": "approved", "extra": 1}).status_code == 422


# --- actions ---------------------------------------------------------------------------------------------


def test_new_version_bumps_the_variant_and_builds_only_that_scene(api):
    _, c, _, version = seed(api)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "new_version", "revision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2 and body["job_id"] is not None
    assert body["scene"]["variant"] == 1 and body["scene"]["status"] == "stale"
    assert body["scene"]["review"]["state"] == "changed" and not body["scene"]["review"]["stale"]
    assert stored(api, version.id).scenes[0].side_panel.variant == 1
    assert payload_of(api, body["job_id"]) == {"scene_ids": ["s1"], "base_revision": 2}
    # a build is running: the next action waits for it
    r = c.post(f"/api/versions/{version.id}/scenes/lab/visual", json={"action": "new_version", "revision": 2})
    assert r.status_code == 409 and r.json()["code"] == "version_busy"


def test_new_version_of_a_clip_and_where_none_applies(api):
    _, c, _, version = seed(api)
    url = f"/api/versions/{version.id}/scenes"
    for sid in ("s2", "s3", "sim", "q1"):
        r = c.post(f"{url}/{sid}/visual", json={"action": "new_version", "revision": 1})
        assert r.status_code == 409 and r.json()["code"] == "action_unavailable", sid
    r = c.post(f"{url}/lab/visual", json={"action": "new_version", "revision": 1})
    assert r.status_code == 200 and stored(api, version.id).scene_by_id("lab").variant == 1
    with api.db() as db:
        assert db.query(Job).count() == 1


def test_actions_need_the_current_revision(api):
    _, c, _, version = seed(api)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 7})
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict" and r.json()["current_revision"] == 1
    assert c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove"}).status_code == 422
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "paint", "revision": 1})
    assert r.status_code == 422


def test_actions_wait_for_a_running_job(api):
    user, c, project, version = seed(api)
    with api.db() as db:
        add_job(db, kind="build_assets", status="running", user=user, project=project,
                version=db.get(ProjectVersion, version.id))
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "version_busy"


def test_choose_from_the_library_sets_the_override_and_authorises_the_key(api):
    user, c, project, version = seed(api)
    key = "upload-" + "d" * 40
    item_id = library_item(api, user, key)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual",
               json={"action": "choose_library", "library_item_id": item_id, "revision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2 and body["job_id"] is None
    assert body["scene"]["review"]["state"] == "changed" and body["scene"]["status"] == "stale"
    panel = stored(api, version.id).scenes[0].side_panel
    assert panel.kind == "image" and panel.override_asset_key == key and panel.image_prompt  # prompt kept
    with api.db() as db:
        assert db.query(AssetRef).filter(AssetRef.project_id == project.id, AssetRef.asset_key == key).count() == 1
    # a scene without a panel gets a picture panel
    r = c.post(f"/api/versions/{version.id}/scenes/s3/visual",
               json={"action": "choose_library", "library_item_id": item_id, "revision": 2})
    assert r.status_code == 200, r.text
    s3 = stored(api, version.id).scene_by_id("s3")
    assert s3.side_panel.kind == "image" and s3.side_panel.override_asset_key == key
    # after a build the picked item shows as coming from the library
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        sp, m = v.get_screenplay(), v.get_manifest()
        m.media["s3"] = SceneMedia(scene_id="s3", side_panel=_info(key, "image", "upload", "image/png"))
        m.scene_hashes = {s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}
        v.set_manifest(m)
        db.commit()
    s3v = scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s3"]
    assert (s3v["kind"], s3v["source"], s3v["status"]) == ("image", "library", "ready")


def test_another_users_library_item_is_not_found(api):
    _, c, _, version = seed(api)
    bob, _ = api.editor("bob")
    item_id = library_item(api, bob, "upload-" + "e" * 40)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual",
               json={"action": "choose_library", "library_item_id": item_id, "revision": 1})
    assert r.status_code == 404
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual",
               json={"action": "choose_library", "library_item_id": 999999, "revision": 1})
    assert r.status_code == 404
    assert stored(api, version.id).scenes[0].side_panel.override_asset_key is None
    with api.db() as db:
        assert db.query(AssetRef).filter(AssetRef.asset_key == "upload-" + "e" * 40).count() == 0


def test_library_picks_must_fit_the_scene(api):
    user, c, _, version = seed(api)
    image_id = library_item(api, user, "upload-" + "f" * 40)
    video_id = library_item(api, user, "upload-" + "9" * 40, kind="video", title="lathe clip", mime="video/mp4")
    url = f"/api/versions/{version.id}/scenes"

    def choose(sid: str, item: int | None) -> Any:
        return c.post(f"{url}/{sid}/visual", json={"action": "choose_library", "library_item_id": item, "revision": 1})

    assert choose("sim", image_id).status_code == 422  # an animation scene takes a video
    assert choose("play", video_id).status_code == 422  # an interactive poster is a picture
    assert choose("q1", image_id).json()["code"] == "action_unavailable"
    assert choose("s1", None).status_code == 422
    r = choose("sim", video_id)
    assert r.status_code == 200, r.text
    assert stored(api, version.id).scene_by_id("sim").override_asset_key == "upload-" + "9" * 40
    assert r.json()["scene"]["kind"] == "video"


def test_remove_a_panel_or_an_override(api):
    user, c, _, version = seed(api)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 1})
    assert r.status_code == 200, r.text
    scene = r.json()["scene"]
    assert scene["kind"] == "none" and scene["review"]["state"] == "removed" and r.json()["job_id"] is None
    assert stored(api, version.id).scenes[0].side_panel is None
    summary = c.get(f"/api/versions/{version.id}/visual-review").json()["summary"]
    assert summary["removed"] == 1 and summary["total"] == 5
    r = c.post(f"/api/versions/{version.id}/scenes/lab/visual", json={"action": "remove", "revision": 2})
    assert r.status_code == 409 and r.json()["code"] == "action_unavailable"  # its own clip stays
    video_id = library_item(api, user, "upload-" + "8" * 40, kind="video", title="clip", mime="video/mp4")
    c.post(f"/api/versions/{version.id}/scenes/lab/visual",
           json={"action": "choose_library", "library_item_id": video_id, "revision": 2})
    r = c.post(f"/api/versions/{version.id}/scenes/lab/visual", json={"action": "remove", "revision": 3})
    assert r.status_code == 200 and r.json()["scene"]["review"]["state"] == "changed"
    assert stored(api, version.id).scene_by_id("lab").override_asset_key is None


def test_retry_builds_the_scene_without_touching_the_screenplay(api):
    _, c, _, version = seed(api)
    r = c.post(f"/api/versions/{version.id}/scenes/sim/visual", json={"action": "retry", "revision": 1})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1
    assert payload_of(api, r.json()["job_id"]) == {"scene_ids": ["sim"], "base_revision": 1}
    r = c.post(f"/api/versions/{version.id}/scenes/q1/visual", json={"action": "retry", "revision": 1})
    assert r.status_code == 409


# --- access ----------------------------------------------------------------------------------------------


def test_other_users_get_404_everywhere(api):
    _, _, _, version = seed(api)
    _, mallory = api.editor("mallory")
    base = f"/api/versions/{version.id}"
    assert mallory.get(f"{base}/visual-review").status_code == 404
    assert mallory.put(f"{base}/visual-review/s1", json={"state": "approved"}).status_code == 404
    r = mallory.post(f"{base}/scenes/s1/visual", json={"action": "new_version", "revision": 1})
    assert r.status_code == 404
    with api.db() as db:
        assert db.query(Job).count() == 0 and db.query(VisualReview).count() == 0


def test_mutations_need_the_csrf_header_and_a_session(api):
    _, c, _, version = seed(api)
    bare = api.client(browser=False)
    assert bare.get(f"/api/versions/{version.id}/visual-review").status_code == 401
    c.headers.pop("X-Aadhi-CSRF")
    r = c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "approved"})
    assert r.status_code == 403
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 1})
    assert r.status_code == 403


def test_an_admin_uses_only_their_own_library(api):
    user, _, _, version = seed(api)
    item_id = library_item(api, user, "upload-" + "7" * 40)
    api.user("root", role="admin")
    admin = api.login("root")
    assert admin.get(f"/api/versions/{version.id}/visual-review").status_code == 200  # existing admin access
    r = admin.post(f"/api/versions/{version.id}/scenes/s1/visual",
                   json={"action": "choose_library", "library_item_id": item_id, "revision": 1})
    assert r.status_code == 404  # alice's item is not the admin's
    # nor does the admin put their own library media into alice's lecture (like POST /api/library/{id}/attach)
    with api.db() as db:
        root = db.query(User).filter(User.username == "root").one()
    own = library_item(api, root, "upload-" + "8" * 40)
    r = admin.post(f"/api/versions/{version.id}/scenes/s1/visual",
                   json={"action": "choose_library", "library_item_id": own, "revision": 1})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    with api.db() as db:
        assert db.query(AssetRef).filter(AssetRef.asset_key == "upload-" + "8" * 40).count() == 0
    assert stored(api, version.id).scenes[0].side_panel.override_asset_key is None
    seen = scenes(admin.get(f"/api/versions/{version.id}/visual-review"))["s1"]["actions"]
    assert "choose_library" not in seen and "upload" not in seen and "approve" in seen
    mine = scenes(api.login("alice").get(f"/api/versions/{version.id}/visual-review"))["s1"]["actions"]
    assert "choose_library" in mine and "upload" in mine


def test_no_screenplay_is_a_conflict(api):
    user, c = api.editor("alice")
    with api.db() as db:
        _, version = add_project(db, user, built=False)
        db.get(ProjectVersion, version.id).screenplay = None
        db.commit()
    assert c.get(f"/api/versions/{version.id}/visual-review").json()["code"] == "no_screenplay"


# --- library suggestions badge and copying sign-offs -----------------------------------------------------


def test_library_suggestions_count_the_users_matching_items(api):
    user, c, _, version = seed(api)
    library_item(api, user, "upload-" + "6" * 40, title="transistor on a breadboard")
    s = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    assert s["s1"]["library_suggestions"] == 1
    assert s["s2"]["library_suggestions"] == 0 and s["q1"]["library_suggestions"] == 0
    _, other = api.editor("bob")
    assert other.get(f"/api/versions/{version.id}/visual-review").status_code == 404


def test_copy_reviews_to_a_duplicate(api):
    from aadhi import review

    _, c, project, version = seed(api)
    c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "approved", "note": "fine"})
    dup = c.post(f"/api/versions/{version.id}/duplicate", json={})
    assert dup.status_code == 201
    new_id = dup.json()["version"]["id"]
    # POST .../duplicate copies the sign-offs with the screenplay (review.copy_reviews)
    s1 = scenes(c.get(f"/api/versions/{new_id}/visual-review"))["s1"]
    assert s1["review"]["state"] == "approved" and s1["review"]["note"] == "fine"
    with api.db() as db:
        assert review.copy_reviews(db, version.id, new_id) == 0  # already there: copying again copies nothing
        assert review.copy_reviews(db, new_id, new_id) == 0
        db.commit()
    # the copy is independent of the original
    c.put(f"/api/versions/{new_id}/visual-review/s1", json={"state": "pending"})
    assert scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]["review"]["state"] == "approved"


@pytest.mark.parametrize("state", ["approved", "pending"])
def test_reviews_go_with_their_version(api, state):
    _, c, _, version = seed(api)
    c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": state})
    with api.db() as db:
        db.delete(db.get(ProjectVersion, version.id))
        db.commit()
        assert db.query(VisualReview).count() == 0


# --- new versions only when they can be made; bounded, limited and precise answers ----------------------


def test_a_new_version_is_offered_and_accepted_only_when_it_can_be_made(api):
    _, c, _, version = seed(api)
    api.settings.image_provider = "none"
    api.settings.video_provider = "none"
    s = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    assert "new_version" not in s["s1"]["actions"] and "new_version" not in s["lab"]["actions"]
    for sid in ("s1", "lab"):
        r = c.post(f"/api/versions/{version.id}/scenes/{sid}/visual", json={"action": "new_version", "revision": 1})
        assert r.status_code == 409 and r.json()["code"] == "action_unavailable", sid
    sp = stored(api, version.id)
    assert sp.scenes[0].side_panel.variant == 0 and sp.scene_by_id("lab").variant == 0
    with api.db() as db:
        assert db.query(Job).count() == 0 and db.get(ProjectVersion, version.id).revision == 1
    # the server can generate again, but the lecture opted out of generated pictures
    api.settings.image_provider = "fake"
    api.settings.video_provider = "fake"
    with api.db() as db:
        db.get(ProjectVersion, version.id).generation_meta = {
            "options": {"allow_ai_video": True, "allow_generated_images": False}}
        db.commit()
    s = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    assert "new_version" not in s["s1"]["actions"] and "new_version" in s["lab"]["actions"]


def test_a_new_version_that_could_not_be_made_reads_as_a_backup(api):
    _, c, _, version = seed(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        data = v.get_screenplay().model_dump(mode="json")
        data["scenes"][0]["side_panel"]["variant"] = 1
        sp = Screenplay.model_validate(data)
        v.set_screenplay(sp)
        m = v.get_manifest()
        m.media["s1"] = SceneMedia(scene_id="s1", side_panel=_info(IMAGE_KEY, "image", "image", "image/png"),
                                   warnings=["A new AI version cannot be made on this server; the earlier one is "
                                             "shown."])
        m.scene_hashes = {s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}
        v.set_manifest(m)
        db.commit()
    s1 = scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]
    assert (s1["status"], s1["variant"], s1["url"]) == ("fallback", 1, f"/media/assets/image/{IMAGE_KEY}/t.png")
    assert s1["status_reason"].startswith("A new AI version cannot be made on this server")
    assert "retry" not in s1["actions"]  # a server setting: building again cannot help


def test_an_approval_covers_only_the_request_the_teacher_saw(api):
    _, c, _, version = seed(api)
    assert c.get(f"/api/versions/{version.id}/visual-review").json()["revision"] == 1
    data = stored(api, version.id).model_dump(mode="json")
    data["scenes"][0]["side_panel"]["image_prompt"] = "a burnt transistor in a bin"
    assert put_sp(c, version.id, data, 1).status_code == 200  # changed elsewhere meanwhile
    url = f"/api/versions/{version.id}/visual-review/s1"
    r = c.put(url, json={"state": "approved", "revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict" and r.json()["current_revision"] == 2
    with api.db() as db:
        assert db.query(VisualReview).count() == 0
    r = c.put(url, json={"state": "approved", "revision": 2})
    assert r.status_code == 200 and r.json()["review"]["state"] == "approved"
    assert c.put(url, json={"state": "pending", "revision": 1}).status_code == 200  # undoing needs no check
    assert c.put(url, json={"state": "approved"}).status_code == 200  # without a revision: as before
    assert c.put(url, json={"state": "approved", "revision": 0}).status_code == 422


def test_review_writes_are_limited_per_user(api, monkeypatch):
    from aadhi.api.routers import visual_review as router

    monkeypatch.setattr(router, "REVIEW_WRITES_PER_MINUTE", 3)
    _, c, _, version = seed(api)
    url = f"/api/versions/{version.id}/visual-review/s1"
    assert [c.put(url, json={"state": s}).status_code for s in ("approved", "pending", "approved")] == [200] * 3
    r = c.put(url, json={"state": "pending"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 1})
    assert r.status_code == 429
    assert stored(api, version.id).scenes[0].side_panel is not None


def test_one_scenes_answer_counts_the_same_library_matches_as_the_review(api, monkeypatch):
    from aadhi import library

    monkeypatch.setattr(library, "MATCH_RECENT", 2)  # more items than the recent ones: the prefilter decides
    monkeypatch.setattr(library, "MATCH_OVERLAP", 3)
    user, c, _, version = seed(api)
    for i in range(6):
        library_item(api, user, f"upload-{i:040d}", title=f"transistor on a breadboard, take {i}")
    library_item(api, user, "upload-" + "f" * 40, title="lathe cutting metal")
    s = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    assert s["s1"]["library_suggestions"] == 3 and s["lab"]["library_suggestions"] == 1
    for sid in ("s1", "lab"):
        one = c.put(f"/api/versions/{version.id}/visual-review/{sid}", json={"state": "approved"}).json()
        assert one["library_suggestions"] == s[sid]["library_suggestions"], sid


def test_the_picture_a_scene_shows_is_not_its_library_match(api):
    from aadhi import library

    user, c, project, version = seed(api)
    with api.db() as db:  # the build saved the scene's own picture to the library
        library.record_generated(db, user_id=user.id, asset_key=IMAGE_KEY, kind="image", project_id=project.id,
                                 scene_id="s1", prompt="a transistor on a breadboard", provider="pollinations",
                                 model=None, settings=api.settings)
        db.commit()
    assert scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]["library_suggestions"] == 0
    found = c.get(f"/api/library/suggestions?version_id={version.id}").json()["scenes"]
    assert {x["scene_id"]: x["matches"] for x in found}["s1"] == []
    other = library_item(api, user, "upload-" + "c" * 40, title="transistor on a breadboard")
    assert scenes(c.get(f"/api/versions/{version.id}/visual-review"))["s1"]["library_suggestions"] == 1
    found = c.get(f"/api/library/suggestions?version_id={version.id}").json()["scenes"]
    assert [m["item"]["id"] for m in {x["scene_id"]: x["matches"] for x in found}["s1"]] == [other]


def test_a_chosen_visual_is_shown_before_its_build_and_a_removed_one_is_not(api):
    user, c, _, version = seed(api)
    key = "upload-" + "d" * 40
    item_id = library_item(api, user, key)
    s1 = c.post(f"/api/versions/{version.id}/scenes/s1/visual",
                json={"action": "choose_library", "library_item_id": item_id, "revision": 1}).json()["scene"]
    assert (s1["status"], s1["source"], s1["kind"], s1["provider"]) == ("stale", "library", "image", None)
    assert key in s1["url"] and "build the scene" in s1["status_reason"]
    clip = "upload-" + "9" * 40
    clip_id = library_item(api, user, clip, kind="video", title="lathe clip", mime="video/mp4")
    lab = c.post(f"/api/versions/{version.id}/scenes/lab/visual",
                 json={"action": "choose_library", "library_item_id": clip_id, "revision": 2}).json()["scene"]
    assert (lab["kind"], lab["source"], lab["provider"], lab["status"]) == ("video", "library", None, "stale")
    assert clip in lab["url"]
    with api.db() as db:  # built with the pick
        v = db.get(ProjectVersion, version.id)
        sp, m = v.get_screenplay(), v.get_manifest()
        m.media["lab"] = SceneMedia(scene_id="lab", main=_info(clip, "video", "upload", "video/mp4"))
        m.scene_hashes = {s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}
        v.set_manifest(m)
        db.commit()
    assert scenes(c.get(f"/api/versions/{version.id}/visual-review"))["lab"]["status"] == "ready"
    lab = c.post(f"/api/versions/{version.id}/scenes/lab/visual",
                 json={"action": "remove", "revision": 3}).json()["scene"]
    assert (lab["status"], lab["url"], lab["source"]) == ("stale", None, "none")  # the removed clip is not shown
