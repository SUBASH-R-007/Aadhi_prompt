"""Visual Review of scenes skipped in the video (``hidden``, batch 4): listed and marked, approvable and
replaceable, but nothing that builds them is offered or accepted (the build leaves them out), and they never
count as needing attention."""

from __future__ import annotations

from aadhi.models import Job, ProjectVersion
from aadhi.pipeline.assets import scene_hash
from aadhi.schemas.manifest import AssetManifest, SceneMedia
from aadhi.schemas.screenplay import Screenplay
from tests.api.test_visual_review import scenes, screenplay_dict, seed, stored


def hide(api, vid: int, *scene_ids: str, sim_failed: bool = False) -> None:
    """Hide scenes in the stored screenplay (revision and hashes unchanged: hiding never changes a scene hash);
    optionally make the animation of ``sim`` a failed render."""
    data = screenplay_dict()
    for s in data["scenes"]:
        if s["id"] in scene_ids:
            s["hidden"] = True
    sp = Screenplay.model_validate(data)
    with api.db() as db:
        v = db.get(ProjectVersion, vid)
        v.set_screenplay(sp)
        manifest = v.get_manifest()
        if sim_failed:
            manifest.media["sim"] = SceneMedia(scene_id="sim", warnings=["The animation could not be rendered."])
            v.issues = [{"code": "manim.render_failed", "severity": "warning", "message": "The animation failed.",
                         "scene_id": "sim", "source": "manim", "fixable": False}]
        v.set_manifest(AssetManifest(media=manifest.media, scene_hashes={s.id: scene_hash(s, sp.lexicon)
                                                                         for s in sp.scenes}))
        db.commit()


def test_a_hidden_scene_is_listed_and_marked_without_build_actions(api):
    _, c, _, version = seed(api)
    before = scenes(c.get(f"/api/versions/{version.id}/visual-review"))
    assert all(s["hidden"] is False for s in before.values())
    hide(api, version.id, "s1", "lab")
    r = c.get(f"/api/versions/{version.id}/visual-review")
    s = scenes(r)
    assert list(s) == ["s1", "s2", "s3", "lab", "sim", "play", "q1"]
    assert s["s1"]["hidden"] is True and s["lab"]["hidden"] is True and s["sim"]["hidden"] is False
    assert s["s1"]["actions"] == ["approve", "choose_library", "upload", "remove"]  # no new_version
    assert s["lab"]["actions"] == ["approve", "choose_library", "upload"]
    assert before["s1"]["actions"] == ["approve", "new_version", "choose_library", "upload", "remove"]


def test_paid_actions_on_a_hidden_scene_are_refused_before_anything_is_charged(api):
    _, c, _, version = seed(api)
    hide(api, version.id, "s1", "sim")
    for sid, action in (("s1", "new_version"), ("sim", "retry")):
        r = c.post(f"/api/versions/{version.id}/scenes/{sid}/visual", json={"action": action, "revision": 1})
        assert r.status_code == 409 and r.json()["code"] == "action_unavailable", (sid, r.text)
        assert "skipped in the video" in r.json()["detail"]
    with api.db() as db:
        assert db.query(Job).count() == 0
    assert stored(api, version.id).scenes[0].side_panel.variant == 0
    # approving and removing still work: they only edit the screenplay / the sign-off
    r = c.put(f"/api/versions/{version.id}/visual-review/s1", json={"state": "approved"})
    assert r.status_code == 200 and r.json()["review"]["state"] == "approved" and r.json()["hidden"] is True
    r = c.post(f"/api/versions/{version.id}/scenes/s1/visual", json={"action": "remove", "revision": 1})
    assert r.status_code == 200, r.text
    assert stored(api, version.id).scenes[0].side_panel is None and stored(api, version.id).scenes[0].hidden


def test_a_hidden_scene_never_needs_attention(api):
    _, c, _, version = seed(api)
    hide(api, version.id, sim_failed=True)
    shown = c.get(f"/api/versions/{version.id}/visual-review").json()
    sim = next(s for s in shown["scenes"] if s["scene_id"] == "sim")
    assert sim["status"] == "failed" and "retry" in sim["actions"] and shown["summary"]["needs_attention"] == 1
    hide(api, version.id, "sim", sim_failed=True)
    body = c.get(f"/api/versions/{version.id}/visual-review").json()
    sim = next(s for s in body["scenes"] if s["scene_id"] == "sim")
    assert sim["status"] == "failed" and sim["hidden"] is True and "retry" not in sim["actions"]
    assert body["summary"]["needs_attention"] == 0 and body["summary"]["total"] == shown["summary"]["total"]
