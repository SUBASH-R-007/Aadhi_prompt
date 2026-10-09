"""Durable render workspace: content-addressed segment keys, cache store/reuse, sweep (no ffmpeg)."""

from __future__ import annotations

import os
import time
import wave
from pathlib import Path

import pytest

from aadhi.compose.ffmpeg import Command, SegmentSpec, StateFrame, build_segment, video_input
from aadhi.compose.workspace import (
    RenderWorkspace,
    file_digest,
    free_gb,
    segment_key,
    stat_digest,
    sweep_legacy_temp,
    sweep_workspaces,
    wav_frames,
    work_root,
    workspace_ids,
)
from aadhi.config import Settings


def write_wav(path: Path, frames: int) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00\x00\x00" * frames)
    return path


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")


def test_work_root_default_and_override(settings: Settings, tmp_path: Path) -> None:
    assert work_root(settings) == (tmp_path / "data" / "render-work").resolve()
    custom = settings.model_copy(update={"render_work_dir": str(tmp_path / "scratch")})
    assert work_root(custom) == (tmp_path / "scratch").resolve()
    assert free_gb(tmp_path / "does" / "not" / "exist") > 0


def _spec(state: str, clip: str) -> SegmentSpec:
    spec = SegmentSpec(width=640, height=360, fps=30, frames=60, background=video_input(clip, loop=True, audio=False),
                       background_kind="video", blank_state="frames/blank.png")
    spec.states = [StateFrame(state, 0.0)]
    return spec


def test_segment_key_ignores_attempt_paths_and_names_but_not_content() -> None:
    a = build_segment(_spec("frames/s003_0000.png", "/w/attempt-1/inputs/clip.mp4"), name="s003")
    b = build_segment(_spec("frames/s003_0000.png", "/w/attempt-2/inputs/clip.mp4"), name="s007")
    digests_a = {"/w/attempt-1/inputs/clip.mp4": "asset:clip", "frames/s003_0000.png": "png:aaa",
                 "frames/blank.png": "png:blank"}
    digests_b = {"/w/attempt-2/inputs/clip.mp4": "asset:clip", "frames/s003_0000.png": "png:aaa",
                 "frames/blank.png": "png:blank"}
    key = segment_key(a, "s003", digests_a, {"ffmpeg": (8, 1)})
    assert len(key) == 32 and key == segment_key(b, "s007", digests_b, {"ffmpeg": (8, 1)})
    assert key != segment_key(b, "s007", {**digests_b, "frames/s003_0000.png": "png:bbb"}, {"ffmpeg": (8, 1)})
    assert key != segment_key(b, "s007", digests_b, {"ffmpeg": (7, 0)})
    other = build_segment(_spec("frames/s003_0000.png", "/w/attempt-2/inputs/clip.mp4"), name="s007")
    other.args[other.args.index("-crf") + 1] = "30"
    assert key != segment_key(other, "s007", digests_b, {"ffmpeg": (8, 1)})
    assert segment_key(Command(["-i", "x"]), "n", {}) == segment_key(Command(["-i", "x"]), "m", {})


def test_digests(tmp_path: Path) -> None:
    f = tmp_path / "a.png"
    f.write_bytes(b"abc")
    assert file_digest(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert stat_digest(f).startswith("file:a.png:3:")


def test_store_reuse_and_finish(settings: Settings) -> None:
    ws = RenderWorkspace.open(settings, 42, attempt=1)
    assert ws.root.name == "render-42" and ws.attempt_dir.parent == ws.root and ws.segments_dir.is_dir()
    assert ws.prepare({"timeline": "t1", "ffmpeg": [8, 1]}) is False  # new workspace
    (ws.attempt_dir / "s000.mp4").write_bytes(b"mp4-bytes")
    write_wav(ws.attempt_dir / "s000.wav", 1600)
    assert ws.cached("k1", frames=30, samples=1600) is None
    assert ws.store("k1", "s000", frames=30, samples=1600)
    assert ws.cached("k1", frames=30, samples=1600) == ws.segments_dir / "k1.mp4"
    assert ws.cached("k1", frames=31, samples=1600) is None and ws.cached("k1", frames=30, samples=1601) is None
    ws.finish(keep=True)
    assert not ws.attempt_dir.exists() and ws.root.exists()

    again = RenderWorkspace.open(settings, 42, attempt=2)
    assert again.attempt_dir != ws.attempt_dir
    assert again.prepare({"timeline": "t1", "ffmpeg": [8, 1]}) is True  # resumable
    again.adopt("k1", "s005")
    assert (again.attempt_dir / "s005.mp4").read_bytes() == b"mp4-bytes"
    assert wav_frames(again.attempt_dir / "s005.wav") == 1600
    assert again.prepare({"timeline": "t2", "ffmpeg": [8, 1]}) is False  # changed lecture: cache cleared
    assert again.cached("k1", frames=30, samples=1600) is None and again.segments_dir.is_dir()
    again.finish(keep=False)
    assert not again.root.exists()


def test_cache_ignores_segments_without_marker(settings: Settings) -> None:
    ws = RenderWorkspace.open(settings, 7, attempt=1)
    (ws.segments_dir / "k.mp4").write_bytes(b"x")
    write_wav(ws.segments_dir / "k.wav", 10)
    assert ws.cached("k", frames=1, samples=10) is None  # a crash before the .ok marker
    (ws.segments_dir / "k.ok").write_text("not json", encoding="utf-8")
    assert ws.cached("k", frames=1, samples=10) is None
    assert wav_frames(ws.segments_dir / "missing.wav") is None


def test_sweep_keeps_active_and_recent_failures(settings: Settings, tmp_path: Path) -> None:
    root = work_root(settings)
    for rid in (1, 2, 3, 4, 5, 6):
        (root / f"render-{rid}" / "segments").mkdir(parents=True)
    (root / "not-a-workspace").mkdir()
    old = time.time() - 48 * 3600
    for rid in (4, 6):
        os.utime(root / f"render-{rid}", (old, old))
    statuses = {1: "running", 2: "succeeded", 3: "failed", 4: "failed", 5: None, 6: "cancelled"}
    removed = sweep_workspaces(root, statuses, keep_failed_hours=24, exclude=[1])
    assert sorted(p.name for p in removed) == ["render-2", "render-4", "render-5", "render-6"]
    assert sorted(workspace_ids(root)) == [1, 3]
    assert (root / "not-a-workspace").exists()
    assert sweep_workspaces(tmp_path / "nowhere", {}, keep_failed_hours=1) == []


def test_sweep_legacy_temp_dirs(tmp_path: Path) -> None:
    fresh, stale, other = tmp_path / "aadhi-render-9-abc", tmp_path / "aadhi-render-3-def", tmp_path / "keep-me"
    for d in (fresh, stale, other):
        d.mkdir()
    old = time.time() - 30 * 3600
    os.utime(stale, (old, old))
    os.utime(other, (old, old))
    removed = sweep_legacy_temp(max_age_hours=24, temp_dir=tmp_path)
    assert removed == [stale] and fresh.exists() and other.exists()


def test_parity_eval_cli_keeps_renders_inside_its_workspace(tmp_path, monkeypatch) -> None:
    """A developer .env with RENDER_WORK_DIR / RENDER_BASE_URL must not reach the eval: its render sweeps the
    work root, which would delete a real render's workspace."""
    import importlib.util

    from aadhi.config import ROOT_DIR

    spec = importlib.util.spec_from_file_location("aadhi_eval_render_parity_env", ROOT_DIR / "evals" / "render_parity.py")
    assert spec and spec.loader
    parity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parity)
    env = dict(os.environ, RENDER_WORK_DIR=str(tmp_path / "real-renders"), RENDER_BASE_URL="http://api:8000")
    monkeypatch.setattr(os, "environ", env)
    parity.configure_env(tmp_path / "eval", 8765)
    assert env["RENDER_WORK_DIR"] == str(tmp_path / "eval" / "render-work") and env["RENDER_BASE_URL"] == ""
    monkeypatch.undo()
    for key in ("RENDER_WORK_DIR", "RENDER_BASE_URL"):  # and the suite never sees a developer's value either
        assert os.environ[key] == ""
