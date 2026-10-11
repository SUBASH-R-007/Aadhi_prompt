"""Media frames for the stage: ``mediaPlan()`` validation, the ``/__frames/...`` registry and key resolution (§7.7),
plus the capture options that enter the chunk cache key."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.compose.dynamic.stage_page import StageInfo, StageScene
from aadhi.compose.motion.engine import hashable_timeline, is_dynamic, video_media_keys
from aadhi.compose.motion.media import (
    MAX_PLAN_ENTRIES,
    MediaPlanError,
    parse_media_plan,
    resolve_media_file,
)
from aadhi.compose.motion.stage import FrameRegistry, StageOptions, scene_mismatch
from aadhi.compose.screenshot import CHROMIUM_ARGS

from .fixtures import CLIP_KEY, dynamic_timeline

STAGE = (1920, 1080)


def _entry(**kw):
    out = {"key": "video-abc", "kind": "video", "srcFps": 30, "frames": [3, 1, 2], "size": [960, 540]}
    out.update(kw)
    return out


def test_parse_media_plan_validates_and_merges() -> None:
    plan = parse_media_plan([_entry(), _entry(frames=[2, 7], size=[1280, 300]),
                             _entry(key="aadhi_left_clean.mp4", kind="aadhi", srcFps=24, frames=[0])], stage=STAGE)
    assert [r.key for r in plan] == ["aadhi_left_clean.mp4", "video-abc"]
    clip = plan[1]
    assert clip.frames == (1, 2, 3, 7) and clip.size == (1280, 540) and clip.src_fps == 30.0
    big = parse_media_plan([_entry(size=[5000.4, 9000])], stage=STAGE)[0]
    assert big.size == (1920, 1080)  # never above the stage size
    assert parse_media_plan([_entry(frames=[])], stage=STAGE) == [] and parse_media_plan(None, stage=STAGE) == []
    assert len(plan[1].slug) == 16 and plan[1].slug.isalnum()


@pytest.mark.parametrize("bad", [
    {"key": "../../etc/passwd"}, {"key": "a/b"}, {"key": ""}, {"key": 5},
    {"kind": "image"}, {"srcFps": 0}, {"srcFps": 500}, {"srcFps": True}, {"srcFps": float("nan")},
    {"frames": [1.5]}, {"frames": [-1]}, {"frames": "1,2"}, {"frames": [True]},
    {"size": [0, 10]}, {"size": [10]}, {"size": "1x1"},
])
def test_parse_media_plan_rejects_contract_violations(bad: dict) -> None:
    with pytest.raises(MediaPlanError):
        parse_media_plan([_entry(**bad)], stage=STAGE)


def test_frames_key_and_source_asset() -> None:
    """The real stage names the frames ``v-<hash>-<w>x<h>`` and the source in ``asset_key`` (web/js/dynamic/media.js)."""
    plan = parse_media_plan([_entry(key="v-0123abcd-960x540", asset_key="video-abc", frames=[4]),
                             _entry(key="v-0123abcd-960x540", asset_key="video-abc", frames=[5])], stage=STAGE)
    assert len(plan) == 1 and plan[0].key == "v-0123abcd-960x540" and plan[0].asset == "video-abc"
    assert plan[0].frames == (4, 5)
    assert parse_media_plan([_entry()], stage=STAGE)[0].asset == "video-abc"  # no asset_key: the key is the source
    with pytest.raises(MediaPlanError):
        parse_media_plan([_entry(key="v-1-2x2", asset_key="../x")], stage=STAGE)
    with pytest.raises(MediaPlanError):
        parse_media_plan([_entry(key="v-1-2x2", asset_key="video-a"), _entry(key="v-1-2x2", asset_key="video-b")],
                         stage=STAGE)


def test_parse_media_plan_limits() -> None:
    with pytest.raises(MediaPlanError):
        parse_media_plan({"key": "x"}, stage=STAGE)
    with pytest.raises(MediaPlanError):
        parse_media_plan([_entry(key=f"video-{i}") for i in range(MAX_PLAN_ENTRIES + 1)], stage=STAGE)
    with pytest.raises(MediaPlanError):
        parse_media_plan([_entry(), _entry(srcFps=24)], stage=STAGE)  # one key at two rates


def test_frame_registry_paths() -> None:
    assert FrameRegistry.parse_path("/__frames/c3/video-abc/42.jpg") == ("video-abc", 42)
    assert FrameRegistry.parse_path("/__frames/video-abc/7.jpg") == ("video-abc", 7)  # the chunk segment is optional
    assert FrameRegistry.parse_path("/__frames/c0/aadhi_left_clean.mp4/0.jpg") == ("aadhi_left_clean.mp4", 0)
    assert FrameRegistry.parse_path("/__frames/c0/video%3Aabc/1.jpg") == ("video:abc", 1)
    assert FrameRegistry.parse_path("/__frames/v-0123abcd-960x540/000012.jpg") == ("v-0123abcd-960x540", 12)
    for bad in ("/__frames/c0/%2e%2e%2fsecret/1.jpg", "/__frames/../x/1.jpg", "/__frames/c0/video-abc/x.jpg",
                "/__frames/c0/video-abc/1.png", "/__frames/a/b/c/1.jpg", "/__frames//1.jpg", "/media/x/1.jpg",
                "/__frames/c0/video abc/1.jpg", "/__frames/c0/video-abc/12345678.jpg"):
        assert FrameRegistry.parse_path(bad) is None, bad


def test_frame_registry_serves_only_the_current_chunk(tmp_path: Path) -> None:
    reg = FrameRegistry()
    f = tmp_path / "f_000001.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    reg.set({("video-abc", 1): f})
    assert reg("/__frames/c0/video-abc/1.jpg") == f and reg.served == 1
    assert reg("/__frames/c0/video-abc/2.jpg") is None and reg.missing == 1
    assert reg("/__frames/c0/../../1.jpg") is None
    reg.clear()
    assert reg("/__frames/c0/video-abc/1.jpg") is None


def test_resolve_media_file(tmp_path: Path) -> None:
    (tmp_path / "aadhi_left_clean.mp4").write_bytes(b"x")
    local = {"video-abc": tmp_path / "clip.mp4"}
    assert resolve_media_file("video-abc", local=local, branding_dir=tmp_path) == tmp_path / "clip.mp4"
    assert resolve_media_file("aadhi_left_clean.mp4", local={}, branding_dir=tmp_path) == (
        tmp_path / "aadhi_left_clean.mp4").resolve()
    for key in ("missing.mp4", "..", "../aadhi_left_clean.mp4", "a/b.mp4", ".hidden"):
        assert resolve_media_file(key, local={}, branding_dir=tmp_path) is None


def test_stage_options_flags_and_fingerprint() -> None:
    opts = StageOptions()
    assert opts.chromium_args() == list(CHROMIUM_ARGS)
    assert "--run-all-compositor-stages-before-draw" not in opts.chromium_args()  # spins ~3.3 cores idle (§7.5)
    webgl = StageOptions(webgl=True)
    assert webgl.chromium_args()[-2:] == ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    assert opts.capture_fingerprint()["quality"] == 92
    assert opts.capture_fingerprint() != StageOptions(layers={"captions": True, "stage": True}).capture_fingerprint()


def test_scene_mismatch() -> None:
    info = StageInfo(320, 180, 30, 210, (StageScene(1, 90, 210), StageScene(0, 0, 90)))
    assert scene_mismatch(info, [(0, 90), (90, 210)]) is None
    assert scene_mismatch(info, [(0, 91), (91, 210)]) is not None
    assert scene_mismatch(StageInfo(320, 180, 30, 210, ()), [(0, 210)]) is None  # no scene list: nothing to compare


def test_timeline_helpers() -> None:
    tl = dynamic_timeline([3.0, 3.0], media_scene=1)
    assert is_dynamic(tl) and video_media_keys(tl) == [CLIP_KEY]
    classic = tl.model_copy(update={"presentation": None})
    assert not is_dynamic(classic)
    data = tl.model_dump(mode="json")
    data["scenes"][1]["media"]["url"] = "https://cdn.example/x?sig=1"
    data["scenes"][0]["audio_url"] = "https://cdn.example/a?sig=2"
    stripped = hashable_timeline(data)
    assert "url" not in stripped["scenes"][1]["media"] and "audio_url" not in stripped["scenes"][0]
    assert stripped["scenes"][1]["media"]["asset_key"] == CLIP_KEY
    assert hashable_timeline(data) == hashable_timeline(tl.model_dump(mode="json"))  # presigned links never matter
