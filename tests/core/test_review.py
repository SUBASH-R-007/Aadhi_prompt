"""aadhi.review without HTTP: the visual of each scene type, fingerprints (what changes them and what never
does), statuses, sources, actions, sign-off staleness and the screenplay edits of the actions."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aadhi import review
from aadhi.api.errors import ApiException
from aadhi.schemas.manifest import MediaInfo, SceneMedia
from aadhi.schemas.screenplay import Screenplay


def beat(i: str, text: str = "Some narration.") -> dict[str, Any]:
    return {"id": i, "narration": text}


BASE: dict[str, Any] = {"language": "en-IN", "figures": [{"id": "fig-1", "caption": "c", "asset_key": "figure-1"}],
                        "scenes": [
    {"id": "img", "type": "content", "title": "Image", "beats": [beat("img-b1")],
     "side_panel": {"kind": "image", "title": "T", "rationale": "R", "image_prompt": "a resistor"}},
    {"id": "fig", "type": "example", "title": "Figure", "beats": [beat("fig-b1")],
     "side_panel": {"kind": "figure", "figure_id": "fig-1"}},
    {"id": "chart", "type": "summary", "title": "Chart", "beats": [beat("chart-b1")],
     "side_panel": {"kind": "chart", "chart": {"labels": ["a"], "datasets": [{"data": [1]}]}}},
    {"id": "panelmanim", "type": "content", "title": "PM", "beats": [beat("panelmanim-b1")],
     "side_panel": {"kind": "manim", "manim": {"code": "class A(AadhiScene):\n    pass"}}},
    {"id": "teaser", "type": "content", "title": "Quiz teaser", "beats": [beat("teaser-b1")],
     "side_panel": {"kind": "quiz", "quiz": {"question": "Q?", "options": ["a", "b"], "correct_index": 0}}},
    {"id": "gif", "type": "content", "title": "Gif", "beats": [beat("gif-b1")],
     "side_panel": {"kind": "gif", "gif_query": "sparks"}},
    {"id": "titlecard", "type": "title", "title": "Welcome", "beats": [beat("titlecard-b1")]},
    {"id": "lab", "type": "ai_video", "title": "Lab", "video_prompt": "a lathe", "beats": [beat("lab-b1")]},
    {"id": "sim", "type": "simulation", "title": "Sim", "manim": {"code": "class B(AadhiScene):\n    pass"},
     "beats": [beat("sim-b1")]},
    {"id": "play", "type": "interactive", "title": "Play", "p5_code": "function setup(){}",
     "beats": [beat("play-b1")]},
    {"id": "card", "type": "chapter_card", "title": "Part 2", "beats": []},
]}


def sp_of(data: dict[str, Any] | None = None) -> Screenplay:
    return Screenplay.model_validate(data or BASE)


def edited(path: list[Any], value: Any) -> Screenplay:
    data = copy.deepcopy(BASE)
    node: Any = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return sp_of(data)


def slot(sp: Screenplay, sid: str) -> review.VisualSlot:
    return review.visual_slot(sp, sp.scene_by_id(sid))


def fp(sp: Screenplay, sid: str) -> str:
    return review.fingerprint(slot(sp, sid))


def test_the_visual_of_each_scene_type():
    sp = sp_of()
    kinds = {s.id: (slot(sp, s.id).kind, slot(sp, s.id).field) for s in sp.scenes}
    assert kinds == {
        "img": ("image", "side_panel"), "fig": ("figure", "side_panel"), "chart": ("chart", "side_panel"),
        "panelmanim": ("manim", "side_panel"), "teaser": ("none", None), "gif": ("image", "side_panel"),
        "titlecard": ("none", None), "lab": ("video", "main"), "sim": ("manim", "main"),
        "play": ("interactive", "poster"), "card": ("none", None)}
    assert slot(sp, "fig").figure_key == "figure-1" and slot(sp, "img").prompt == "a resistor"
    assert slot(sp, "lab").prompt == "a lathe"


@pytest.mark.parametrize(("sid", "path", "value"), [
    ("img", ["scenes", 0, "side_panel", "image_prompt"], "a capacitor"),
    ("img", ["scenes", 0, "side_panel", "override_asset_key"], "upload-x"),
    ("img", ["scenes", 0, "side_panel", "variant"], 1),
    ("fig", ["scenes", 1, "side_panel", "figure_id"], "fig-2"),
    ("fig", ["figures", 0, "asset_key"], "figure-2"),  # the figure's image itself
    ("chart", ["scenes", 2, "side_panel", "chart", "datasets", 0, "data"], [2]),
    ("panelmanim", ["scenes", 3, "side_panel", "manim", "code"], "class C(AadhiScene):\n    pass"),
    ("lab", ["scenes", 7, "video_prompt"], "a drill"),
    ("lab", ["scenes", 7, "variant"], 3),
    ("lab", ["scenes", 7, "override_asset_key"], "upload-y"),
    ("sim", ["scenes", 8, "manim", "code"], "class D(AadhiScene):\n    pass"),
    ("play", ["scenes", 9, "p5_code"], "function draw(){}"),
    ("play", ["scenes", 9, "poster_override_asset_key"], "upload-z"),
])
def test_the_fingerprint_changes_with_the_visual(sid, path, value):
    data = copy.deepcopy(BASE)
    data["figures"].append({"id": "fig-2", "caption": "", "asset_key": "figure-2"})
    base = sp_of(data)
    node: Any = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    other = sp_of(data)
    assert fp(base, sid) != fp(other, sid)
    assert len(fp(base, sid)) == review.FINGERPRINT_LENGTH


@pytest.mark.parametrize("path", [
    ["scenes", 0, "beats", 0, "narration"],
    ["scenes", 0, "title"],
    ["scenes", 0, "notes"],
    ["scenes", 0, "side_panel", "title"],
    ["scenes", 0, "side_panel", "rationale"],
    ["scenes", 7, "beats", 0, "narration"],
    ["scenes", 7, "rationale"],
    ["scenes", 8, "beats", 0, "narration"],
])
def test_the_fingerprint_ignores_narration_titles_and_rationales(path):
    other = edited(path, "Something else entirely.")
    sid = BASE["scenes"][path[1]]["id"]
    assert fp(sp_of(), sid) == fp(other, sid)


def test_the_fingerprint_is_deterministic_and_variant_zero_is_the_default():
    assert fp(sp_of(), "img") == fp(sp_of(), "img")
    assert fp(edited(["scenes", 0, "side_panel", "variant"], 0), "img") == fp(sp_of(), "img")


def _media(field: str, **info: Any) -> SceneMedia:
    defaults = {"asset_key": "image-1", "storage_key": "assets/image/image-1/t.png", "kind": "image",
                "mime": "image/png", "source": "image"}
    return SceneMedia(scene_id="x", **{field: MediaInfo(**{**defaults, **info})})


def test_statuses():
    sp = sp_of()
    img, lab = slot(sp, "img"), slot(sp, "lab")
    ambiguous = [{"code": "video.ambiguous_submission", "message": "m"}]
    assert review.visual_status(lab, None, ambiguous, True)[0] == "ambiguous"  # first, even when stale
    assert review.visual_status(img, _media("side_panel"), [], True)[0] == "stale"
    assert review.visual_status(img, _media("side_panel"), [], False) == ("ready", "")
    assert review.visual_status(img, SceneMedia(scene_id="x", warnings=["w1"]), [], False) == ("missing", "w1")
    failed = [{"code": "assets.media_degraded", "message": "Panel failed: boom"}]
    assert review.visual_status(img, SceneMedia(scene_id="x"), failed, False) == ("failed", "Panel failed: boom")
    still = SceneMedia(scene_id="x", main=MediaInfo(kind="image", mime="image/png", source="fallback",
                                                    asset_key="image-2"), main_is_fallback=True,
                       warnings=["AI video limit reached; showing a still image instead."])
    assert review.visual_status(lab, still, [], False)[0] == "fallback"
    assert review.visual_status(slot(sp, "chart"), None, [], False) == ("ready", "")
    assert review.visual_status(slot(sp, "card"), None, [], False) == ("ready", "")
    chosen = slot(edited(["scenes", 0, "side_panel", "override_asset_key"], "upload-x"), "img")
    assert review.visual_status(chosen, None, [], False)[0] == "missing"
    assert review.visual_status(chosen, _media("side_panel"), [], False)[0] == "fallback"  # generated one shown
    assert review.visual_status(chosen, _media("side_panel", asset_key="upload-x", source="upload"), [], False)[0] \
        == "ready"
    # a picture chosen for a video scene is shown as a slow pan (main_is_fallback): still the teacher's choice
    picked = slot(edited(["scenes", 7, "override_asset_key"], "upload-p"), "lab")
    panned = SceneMedia(scene_id="x", main=MediaInfo(asset_key="upload-p", kind="image", mime="image/png",
                                                     source="upload"), main_is_fallback=True)
    assert review.visual_status(picked, panned, [], False) == ("ready", "")


def test_sources():
    sp = sp_of()
    img, play = slot(sp, "img"), slot(sp, "play")
    assert review.visual_source(img, _media("side_panel"), {}) == "generated"
    up = _media("side_panel", asset_key="upload-1", source="upload")
    assert review.visual_source(img, up, {}) == "upload"
    assert review.visual_source(img, up, {"upload-1": "library"}) == "library"
    assert review.visual_source(img, None, {}) == "none"
    assert review.visual_source(slot(sp, "chart"), None, {}) == "builtin"
    assert review.visual_source(play, None, {}) == "builtin"
    assert review.visual_source(slot(sp, "sim"), _media("main", kind="video", source="manim"), {}) == "manim"
    assert review.visual_source(slot(sp, "fig"), _media("side_panel", source="figure"), {}) == "figure"
    assert review.visual_source(slot(sp, "lab"), _media("main", source="fallback"), {}) == "fallback"


def test_actions():
    sp = sp_of()

    def acts(sid: str, status: str = "ready") -> list[str]:
        scene = sp.scene_by_id(sid)
        return review.visual_actions(scene, review.visual_slot(sp, scene), status)

    assert acts("img") == ["approve", "new_version", "choose_library", "upload", "remove"]
    assert acts("chart") == ["approve", "choose_library", "upload", "remove"]
    assert acts("teaser") == [] and acts("titlecard") == [] and acts("card") == []
    assert acts("lab", "ambiguous") == ["approve", "new_version", "choose_library", "upload", "retry",
                                        "confirm_paid_retry"]
    assert acts("sim", "failed") == ["approve", "choose_library", "upload", "retry"]
    assert "new_version" not in acts("fig") and "retry" not in acts("chart", "stale")
    at_max = edited(["scenes", 0, "side_panel", "variant"], 99)
    assert not review.can_new_version(at_max.scenes[0])


def test_sign_off_staleness():
    class Row:
        def __init__(self, state: str, fingerprint: str) -> None:
            self.state, self.fingerprint, self.note, self.updated_at = state, fingerprint, None, None

    assert review.review_view(None, "f")["state"] == "pending"
    assert review.review_view(Row("approved", "f"), "f") == {"state": "approved", "stale": False, "note": None,
                                                            "updated_at": None}
    assert review.review_view(Row("approved", "f"), "g")["state"] == "pending"  # approved an earlier request
    assert review.review_view(Row("approved", "f"), "g")["stale"] is True
    assert review.review_view(Row("changed", "f"), "g") == {"state": "changed", "stale": True, "note": None,
                                                           "updated_at": None}
    assert review.review_view(Row("pending", "f"), "g")["stale"] is False


def test_summary_counts_only_visuals_and_removed_ones():
    items = [
        {"kind": "image", "status": "ready", "review": {"state": "approved", "stale": False}},
        {"kind": "video", "status": "fallback", "review": {"state": "pending", "stale": False}},
        {"kind": "none", "status": "ready", "review": {"state": "removed", "stale": False}},
        {"kind": "none", "status": "ready", "review": {"state": "pending", "stale": False}},
        {"kind": "chart", "status": "ready", "review": {"state": "changed", "stale": True}},
    ]
    assert review.summary(items) == {"total": 4, "approved": 1, "pending": 1, "changed": 1, "removed": 1,
                                     "needs_attention": 2}


def test_action_edits():
    sp = sp_of()
    img = sp.scene_by_id("img")
    newer = review.new_version_scene(img)
    assert newer.side_panel.variant == 1 and img.side_panel.variant == 0
    assert review.new_version_scene(sp.scene_by_id("lab")).variant == 1
    with pytest.raises(ApiException) as exc:
        review.new_version_scene(sp.scene_by_id("chart"))
    assert exc.value.code == "action_unavailable"
    # choose: the panel keeps its words; a chart panel becomes a picture panel; a video picks a manim panel
    chosen = review.chosen_scene(img, "upload-1", "image")
    assert chosen.side_panel.override_asset_key == "upload-1" and chosen.side_panel.image_prompt == "a resistor"
    chart = review.chosen_scene(sp.scene_by_id("chart"), "upload-1", "image")
    assert (chart.side_panel.kind, chart.side_panel.override_asset_key, chart.side_panel.chart) == (
        "image", "upload-1", None)
    clip = review.chosen_scene(img, "upload-2", "video")
    assert clip.side_panel.kind == "manim" and clip.side_panel.override_asset_key == "upload-2"
    assert review.chosen_scene(sp.scene_by_id("play"), "upload-1", "image").poster_override_asset_key == "upload-1"
    for sid, kind, code in (("sim", "image", "validation"), ("play", "video", "validation"),
                            ("teaser", "image", "action_unavailable"), ("titlecard", "image", "action_unavailable")):
        with pytest.raises(ApiException) as exc:
            review.chosen_scene(sp.scene_by_id(sid), "upload-1", kind)
        assert exc.value.code == code, sid
    # remove
    assert review.removed_scene(img).side_panel is None and review.removed_state(img) == "removed"
    with pytest.raises(ApiException):
        review.removed_scene(sp.scene_by_id("lab"))
    lab = review.chosen_scene(sp.scene_by_id("lab"), "upload-2", "video")
    assert review.removed_scene(lab).override_asset_key is None and review.removed_state(lab) == "changed"
    # every edit is a valid screenplay again
    assert review.with_scene(sp, "chart", chart).scene_by_id("chart").side_panel.kind == "image"


def test_a_new_version_only_when_it_can_be_generated():
    sp = sp_of()
    img, lab = sp.scene_by_id("img"), sp.scene_by_id("lab")
    both, images, nothing = frozenset({"image", "video"}), frozenset({"image"}), frozenset()
    assert review.can_new_version(img) and review.can_new_version(img, None, both)  # unchecked / available
    assert not review.can_new_version(img, None, nothing) and not review.can_new_version(img, None, {"video"})
    assert review.can_new_version(lab, None, both)
    clip = SceneMedia(scene_id="lab", main=MediaInfo(asset_key="video-1", kind="video", mime="video/mp4",
                                                     source="veo"))
    still = SceneMedia(scene_id="lab", main=MediaInfo(asset_key="image-2", kind="image", mime="image/png",
                                                      source="fallback"), main_is_fallback=True)
    # AI video off for the lecture: only a new still, never in place of a clip made earlier
    assert not review.can_new_version(lab, clip, images) and review.can_new_version(lab, still, images)
    assert not review.can_new_version(lab, still, nothing)
    capped = still.model_copy(update={"warnings": ["AI video limit reached for this lecture; showing a still image "
                                                   "instead."]})
    assert review.can_new_version(lab, capped, both) and not review.can_new_version(lab, capped, {"video"})
    figured = edited(["scenes", 7, "fallback_figure_id"], "fig-1").scene_by_id("lab")
    assert not review.can_new_version(figured, still, images)  # its backup is a figure: nothing new to make
    acts = review.visual_actions(img, review.visual_slot(sp, img), "ready", generate=nothing)
    assert "new_version" not in acts
    with pytest.raises(ApiException) as exc:
        review.new_version_scene(img, generate=nothing)
    assert exc.value.code == "action_unavailable" and "switched off" in str(exc.value.detail)


def test_generation_available_follows_the_lecture_and_the_server(app_env):
    from aadhi.pipeline.base import GenerationOptions

    on = app_env.model_copy(update={"image_provider": "fake", "video_provider": "fake"})
    assert review.generation_available(GenerationOptions(allow_ai_video=True), on) == {"image", "video"}
    assert review.generation_available(GenerationOptions(), on) == {"image"}  # AI video is the lecture's opt-in
    assert review.generation_available(GenerationOptions(allow_generated_images=False), on) == frozenset()
    off = on.model_copy(update={"image_provider": "none", "video_provider": "none"})
    assert review.generation_available(GenerationOptions(allow_ai_video=True), off) == frozenset()


def test_settled_causes_offer_no_retry():
    sp = sp_of()
    img, lab = sp.scene_by_id("img"), sp.scene_by_id("lab")
    hidden = SceneMedia(scene_id="img", warnings=["Generated images are disabled; the image panel is hidden."])
    capped = SceneMedia(scene_id="lab", main=MediaInfo(asset_key="image-2", kind="image", mime="image/png",
                                                       source="fallback"), main_is_fallback=True,
                        warnings=["AI video limit reached for this lecture; showing a still image instead."])
    failed = capped.model_copy(update={"warnings": ["AI video failed (timeout); showing a still image instead."]})
    assert review.settled(hidden) and review.settled(capped) and not review.settled(failed)
    assert not review.settled(None)

    def acts(scene: Any, status: str, media: SceneMedia | None) -> list[str]:
        return review.visual_actions(scene, review.visual_slot(sp, scene), status, media=media)

    assert "retry" not in acts(img, "missing", hidden) and "retry" not in acts(lab, "fallback", capped)
    assert "retry" in acts(lab, "fallback", failed)  # a transient failure may go away
    assert "retry" in acts(img, "stale", hidden)  # stale: building does change it


def test_accepted_kinds_follow_the_scene_not_the_shown_media():
    sp = sp_of()
    picked = edited(["scenes", 8, "override_asset_key"], "upload-v")
    assert review.visual_slot(picked, picked.scene_by_id("sim")).kind == "video"
    assert review.accepted_kinds(picked.scene_by_id("sim")) == ["video"]  # an animation takes a video only
    assert review.accepted_kinds(sp.scene_by_id("play")) == ["image"]
    assert review.accepted_kinds(sp.scene_by_id("lab")) == ["image", "video"]
    assert review.accepted_kinds(sp.scene_by_id("img")) == ["image", "video"]
    assert review.accepted_kinds(sp.scene_by_id("teaser")) == [] and review.accepted_kinds(
        sp.scene_by_id("titlecard")) == []


def test_shown_keys_name_the_media_each_scene_shows():
    from aadhi.schemas.manifest import AssetManifest

    sp = sp_of()
    manifest = AssetManifest(media={
        "img": _media("side_panel", asset_key="image-9"),
        "lab": SceneMedia(scene_id="lab", main=MediaInfo(asset_key="video-1", kind="video", mime="video/mp4",
                                                         source="veo")),
        "chart": SceneMedia(scene_id="chart"),
    })
    assert review.shown_keys(sp, manifest) == {"img": "image-9", "lab": "video-1"}
    assert review.shown_keys(sp, None) == {}
