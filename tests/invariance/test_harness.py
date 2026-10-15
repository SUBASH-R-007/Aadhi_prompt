"""The invariance harness itself (fast, no ffmpeg or Chromium): the tolerance-aware frame and audio comparison,
the capture comparison, the report summary and the synthetic narration."""

from __future__ import annotations

import copy
import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from . import classic_fixture as fx
from . import goldens as g
from . import pixeldiff as pd

H, W = 36, 64


def frame(value: int = 100) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


# --- frames -------------------------------------------------------------------------------------------------------


def test_identical_frames_pass_even_when_exact() -> None:
    stats = pd.frame_stats(frame(), frame(), pixel_level=4)
    assert stats == {"max_abs": 0, "mean_abs": 0.0, "changed_pixels": 0}
    assert pd.frame_ok(stats, pd.Tolerance()) and pd.frame_ok(stats, pd.Tolerance(exact=True))


def test_invisible_wobble_is_within_the_tolerance_but_not_exact() -> None:
    b = frame()
    b[3, 5] = (103, 100, 98)  # one pixel, 3 levels: encoder noise
    stats = pd.frame_stats(frame(), b, pixel_level=4)
    assert stats["max_abs"] == 3 and stats["changed_pixels"] == 0
    assert pd.frame_ok(stats, pd.Tolerance())
    assert not pd.frame_ok(stats, pd.Tolerance(exact=True))


def test_a_changed_glyph_fails() -> None:
    b = frame()
    b[10:15, 20:24] = 255  # 20 pixels: a few strokes of a glyph
    stats = pd.frame_stats(frame(), b, pixel_level=4)
    assert stats["changed_pixels"] == 20 and not pd.frame_ok(stats, pd.Tolerance())


def test_a_handful_of_pixels_or_a_global_shift() -> None:
    b = frame()
    b[0, :16] = 200  # 16 isolated pixels: at the limit
    big = np.zeros((1080, 1920, 3), dtype=np.uint8)
    big2 = big.copy()
    big2[0, :16] = 200
    assert pd.frame_ok(pd.frame_stats(big, big2, pixel_level=4), pd.Tolerance())
    big2[1, 0] = 200  # the 17th
    assert not pd.frame_ok(pd.frame_stats(big, big2, pixel_level=4), pd.Tolerance())
    shifted = frame(101)  # every pixel one level brighter: a colour change, not noise
    stats = pd.frame_stats(frame(), shifted, pixel_level=4)
    assert stats["changed_pixels"] == 0 and stats["mean_abs"] == 1.0 and not pd.frame_ok(stats, pd.Tolerance())


# --- audio --------------------------------------------------------------------------------------------------------


def pcm(seconds: float, rate: int = 48_000) -> np.ndarray:
    n = int(seconds * rate) * 2
    return (np.sin(np.arange(n) / 7.0) * 8000).astype("<i2")


def test_audio_identical_and_different_lengths() -> None:
    a = pcm(2.0)
    assert pd.audio_compare(a, a.copy(), pd.Tolerance()) == {"samples": [96000, 96000], "ok": True,
                                                              "identical": True, "max_abs": 0}
    res = pd.audio_compare(a, a[:-2], pd.Tolerance())
    assert res["ok"] is False and res["reason"] == "different lengths"


def test_audio_tail_is_reported_not_judged() -> None:
    a = pcm(2.0)
    b = a.copy()
    b[-2 * 4800:] += 120  # the last 0.1 s: the ducked music tail of the classic mux
    res = pd.audio_compare(a, b, pd.Tolerance())
    assert res["ok"] and not res["identical"] and res["max_abs"] == 120 and res["max_abs_outside_tail"] == 0
    assert res["differs_from_s"] == pytest.approx(1.9, abs=0.001)
    assert not pd.audio_compare(a, b, pd.Tolerance(exact=True))["ok"]


def test_audio_change_in_the_body_fails() -> None:
    a = pcm(2.0)
    b = a.copy()
    b[48_000:48_010] += 40
    res = pd.audio_compare(a, b, pd.Tolerance())
    assert not res["ok"] and res["max_abs_outside_tail"] == 40 and res["differs_from_s"] == 0.5
    c = a.copy()
    c[48_000] += 2  # rounding-level difference: within audio_max_abs
    assert pd.audio_compare(a, c, pd.Tolerance())["ok"]


# --- diagnostics and the directory comparison ----------------------------------------------------------------------


def test_state_and_capture_diagnostics() -> None:
    assert pd.compare_states({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == {
        "changed": ["b"], "only_base": [], "only_head": ["c"]}
    cap = g.read_capture(fx.RENDER_FIXTURE)
    other = copy.deepcopy(cap)
    other["scenes"]["3"][0]["key"] = "s3-changed"
    res = pd.compare_captures(cap, other)
    assert res["changed_scenes"] == ["3"] and res["intro_equal"] and res["blocked_media_equal"]


def _side(tmp: Path, name: str, frames: list[str], audio: str = "a1", **extra: object) -> Path:
    side = tmp / name
    side.mkdir()
    report = {"ok": True, "fixture": "showcase_ohms_law", "width": 64, "height": 36, "rate": "30/1",
              "settings": {"fps": 30}, "max_scenes": None, "frame_sha256": frames, "audio_sha256": audio,
              "states": {"s000_0000.png": "x"}, "timeline_sha256": "t", "intro_duration": 1.0,
              "scene_starts": [{"scene_id": "s1", "start": 1.0, "duration": 9.0}], **extra}
    (side / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (side / "capture.json").write_text("{}", encoding="utf-8")
    return side


def test_compare_dirs_equal_and_failed_sides(tmp_path: Path) -> None:
    base, head = _side(tmp_path, "base", ["f0", "f1"]), _side(tmp_path, "head", ["f0", "f1"])
    report = pd.compare_dirs(base, head, tol=pd.Tolerance(), ffmpeg="ffmpeg-not-needed")
    assert report["ok"] and report["identical"] and report["frames"]["identical"] == 2
    assert "passed" in pd.markdown(report)
    (head / "report.json").write_text(json.dumps({"ok": False, "error": "boom"}), encoding="utf-8")
    report = pd.compare_dirs(base, head, tol=pd.Tolerance(), ffmpeg="ffmpeg-not-needed")
    assert not report["ok"] and "boom" in report["problems"][0] and "FAILED" in pd.markdown(report)


def test_compare_dirs_frame_count_and_size(tmp_path: Path) -> None:
    base, head = _side(tmp_path, "base", ["f0", "f1"]), _side(tmp_path, "head", ["f0"], width=32)
    report = pd.compare_dirs(base, head, tol=pd.Tolerance(), ffmpeg="ffmpeg-not-needed")
    assert not report["ok"]
    assert any("frame count" in p for p in report["problems"]) and any("width" in p for p in report["problems"])
    assert pd.scene_at(json.loads((base / "report.json").read_text()), 0.5) == "intro"
    assert pd.scene_at(json.loads((base / "report.json").read_text()), 2.0) == "s1"


# --- capture comparison --------------------------------------------------------------------------------------------


def test_compare_capture_tolerates_rect_wobble_only() -> None:
    cap = g.read_capture(fx.RENDER_FIXTURE)
    assert g.compare_capture(cap, copy.deepcopy(cap)) == []
    index, n = next((i, k) for i, states in cap["scenes"].items() for k, s in enumerate(states) if s["media_rect"])
    wobble = copy.deepcopy(cap)
    wobble["scenes"][index][n]["media_rect"]["x"] += 1.5
    assert g.compare_capture(cap, wobble) == []
    moved = copy.deepcopy(cap)
    moved["scenes"][index][n]["media_rect"]["x"] += 6
    assert any("media_rect" in p for p in g.compare_capture(cap, moved))
    retimed = copy.deepcopy(cap)
    retimed["scenes"][index][-1]["t"] += 0.04
    assert any(" t " in p for p in g.compare_capture(cap, retimed))
    fewer = copy.deepcopy(cap)
    fewer["scenes"][index].pop()
    assert any("states recorded" in p for p in g.compare_capture(cap, fewer))


# --- synthetic narration -------------------------------------------------------------------------------------------


def test_narration_wav_matches_its_layout() -> None:
    sp = fx.load_screenplay(fx.RENDER_FIXTURE)
    scene = fx.visible_scenes(sp)[0]
    layout = fx.narration_layout(scene)
    data = fx.narration_wav(layout, seed=3)
    assert data == fx.narration_wav(layout, seed=3)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == fx.AUDIO_RATE and w.getnchannels() == 1
        assert w.getnframes() == round(layout.duration * fx.AUDIO_RATE)
    assert layout.beats[0].offset == 0.0 and all(b.speech > 0 for b in layout.beats)


def test_quiz_layout_puts_the_countdown_before_the_reveal() -> None:
    sp = fx.load_screenplay("classic_kitchen_sink")
    quiz = next(s for s in sp.scenes if s.id == "s-quiz")
    layout = fx.narration_layout(quiz)
    reveal = next(b for b in layout.beats if b.phase == "reveal")
    assert layout.countdown_seconds == quiz.countdown_seconds
    assert reveal.offset == pytest.approx(layout.countdown_start + quiz.countdown_seconds, abs=1e-6)


def test_degraded_assets_and_hidden_scenes() -> None:
    sp = fx.load_screenplay("classic_kitchen_sink")
    m = fx.synthetic_manifest(sp, fixture="classic_kitchen_sink")
    assert "s-hidden" not in m.audio and "s-hidden" not in m.media and "s-hidden" not in m.scene_hashes
    assert "s-noaudio" not in m.audio
    assert m.media["s-sim-fallback"].main is None and m.media["s-video-none"].main is None
    assert m.media["s-sim"].main.source == "manim" and m.media["s-video"].main.source == "veo"
    assert m.media["s-board"].figures["fig"].asset_key == "figure-aaa"
    assert m.media["s-noaudio"].side_panel.external_url == fx.GIF_URL


# --- state screenshots ---------------------------------------------------------------------------------------------


def _rgba(alpha: int = 255) -> np.ndarray:
    im = np.zeros((H, W, 4), dtype=np.uint8)
    im[..., :3] = (60, 20, 100)
    im[..., 3] = alpha
    return im


def test_state_stats_weigh_pixels_by_alpha() -> None:
    a, b = _rgba(), _rgba()
    b[0, 0, 3] = 250  # an antialiased corner: 5 alpha levels over grey
    stats = pd.state_stats(a, b, level=12.0)
    assert stats["changed_pixels"] == 0 and 0 < stats["max_abs"] < 12
    clear_a, clear_b = _rgba(0), _rgba(0)
    clear_b[..., :3] = 255  # colour under full transparency never shows
    assert pd.state_stats(clear_a, clear_b, level=0.0)["max_abs"] == 0.0
    big = _rgba()
    big[5:10, 5:10, :3] = 255
    assert pd.state_stats(_rgba(), big, level=12.0)["changed_pixels"] == 25
    assert pd.state_stats(_rgba(), np.zeros((2, 2, 4), np.uint8), level=12.0)["changed_pixels"] == H * W


def test_judge_states(tmp_path: Path) -> None:
    from PIL import Image

    for side in ("base", "head"):
        (tmp_path / side / "states").mkdir(parents=True)
    wobble, glyph = _rgba(), _rgba()
    wobble[0, 0, 3] = 250
    glyph[5:10, 5:10, :3] = 255
    for name, a, b in (("s000_0000.png", _rgba(), wobble), ("s001_0000.png", _rgba(), glyph)):
        Image.fromarray(a, "RGBA").save(tmp_path / "base" / "states" / name)
        Image.fromarray(b, "RGBA").save(tmp_path / "head" / "states" / name)
    diff = {"changed": ["s000_0000.png", "s001_0000.png"], "only_base": [], "only_head": ["s002_0000.png"]}
    res = pd.judge_states(tmp_path / "base", tmp_path / "head", diff, pd.Tolerance())
    assert res["pixels_checked"] and res["within_tolerance"] == 1
    assert [f["state"] for f in res["failing"]] == ["s002_0000.png", "s001_0000.png"]
    exact = pd.judge_states(tmp_path / "base", tmp_path / "head", {**diff, "only_head": []}, pd.Tolerance(exact=True))
    assert exact["within_tolerance"] == 0 and len(exact["failing"]) == 2
    unchecked = pd.judge_states(tmp_path / "nowhere", tmp_path / "head", {**diff, "only_head": []}, pd.Tolerance())
    assert not unchecked["pixels_checked"] and unchecked["failing"] == []


# --- differential.py: never a stale verdict -----------------------------------------------------------------------


def _stale_run(out: Path) -> None:
    """What an earlier, passing run left in ``out``: both sides' reports and renders, a verdict and frames."""
    for side in ("base", "head"):
        (out / side / "states").mkdir(parents=True)
        (out / side / "report.json").write_text(json.dumps({"ok": True, "frames": ["f1"], "audio_sha256": "a1"}),
                                                encoding="utf-8")
        (out / side / "capture.json").write_text("{}", encoding="utf-8")
        (out / side / "render.mp4").write_bytes(b"old")
    (out / "frames").mkdir()
    (out / "report.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (out / "summary.md").write_text("passed", encoding="utf-8")


@pytest.mark.parametrize("declared", [False, True])
def test_differential_fails_when_a_render_fails_whatever_an_earlier_run_left(tmp_path: Path, monkeypatch,
                                                                             declared: bool) -> None:
    from . import differential as diff

    out = tmp_path / "out"
    _stale_run(out)
    monkeypatch.setattr(diff, "ensure_vendor", lambda *_: "own")
    monkeypatch.setattr(diff, "resolve_ffmpeg", lambda *_: "ffmpeg")
    monkeypatch.setattr(diff, "render_side", lambda side, *_: 0 if side == "base" else 137)  # head: OOM-killed

    def no_compare(*_: object, **__: object) -> dict:
        raise AssertionError("compared the outputs of a failed render")

    monkeypatch.setattr(diff.pixeldiff, "compare_dirs", no_compare)
    argv = ["--base-root", str(tmp_path / "base"), "--head-root", str(tmp_path), "--out", str(out)]
    assert diff.main(argv + (["--declared-change"] if declared else [])) == 1  # never a declared change either
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False and report["renders"] == {"base": 0, "head": 137}
    assert report["problems"] == ["the head render exited with 137"]
    assert "FAILED" in (out / "summary.md").read_text(encoding="utf-8") and not (out / "frames").exists()


def test_differential_clears_a_sides_earlier_outputs_before_rendering(tmp_path: Path, monkeypatch) -> None:
    from . import differential as diff

    out = tmp_path / "out"
    _stale_run(out)

    class Done:
        returncode = 1  # e.g. "web/vendor is missing": render_classic.py stops before writing anything

    monkeypatch.setattr(diff.subprocess, "run", lambda *_, **__: Done())
    args = diff.parse_args(["--base-root", str(tmp_path / "base"), "--head-root", str(tmp_path), "--out", str(out)])
    assert diff.render_side("head", tmp_path, args) == 1
    assert not [p for p in (out / "head").iterdir()]  # report, capture, render and states of the earlier run: gone
    assert (out / "base" / "report.json").is_file()  # the other side is untouched


def test_burned_caption_renders_get_their_own_mean_limit_but_keep_the_pixel_limit(tmp_path: Path) -> None:
    from . import differential as diff

    base = ["--base-root", str(tmp_path), "--out", str(tmp_path / "out")]
    plain = diff.parse_args(base)
    assert plain.max_mean_abs == pd.Tolerance().max_mean_abs  # the plain path keeps its measured limit
    burn = diff.parse_args([*base, "--burn-captions"])
    assert burn.max_mean_abs == diff.BURN_MAX_MEAN_ABS > plain.max_mean_abs
    assert (burn.pixel_level, burn.max_changed_pixels) == (plain.pixel_level, plain.max_changed_pixels)
    assert diff.parse_args([*base, "--burn-captions", "--max-mean-abs", "0.2"]).max_mean_abs == 0.2  # explicit wins
    assert diff.parse_args([*base, "--burn-captions", "--exact"]).max_mean_abs == pd.Tolerance().max_mean_abs
