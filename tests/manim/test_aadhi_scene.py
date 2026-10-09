"""Script assembly and the injected AadhiScene runtime (pure helpers; no rendering)."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.manim.aadhi_scene import (
    END_MARKER,
    SCRIPT_FONTS,
    assemble_script,
    build_config,
    frame_pixels,
    runtime_source,
    scene_class_name,
    user_line_offset,
)

SCENE = "from manim import *\n\nclass Demo(AadhiScene):\n    def construct(self):\n        self.play_step(0, Create(Circle()))\n"


def test_frame_pixels() -> None:
    assert frame_pixels("fullscreen", "l") == (854, 480)
    assert frame_pixels("fullscreen", "h") == (1920, 1080)
    assert frame_pixels("panel", "m") == (720, 900)
    assert frame_pixels("panel", "x") == (720, 900)  # unknown quality -> medium


def test_build_config_rounds_and_is_literal() -> None:
    cfg = build_config(beat_times=[0.12345, 1.98765], total_duration=5.55555, target="panel", language="ta-IN",
                       has_latex=False, background="#000000", quality="l")
    assert cfg["beat_times"] == [0.123, 1.988] and cfg["total_duration"] == 5.556
    assert (cfg["pixel_width"], cfg["pixel_height"]) == (480, 600)
    assert cfg["has_latex"] is False and cfg["script_fonts"]["tamil"][0] == "Noto Sans Tamil"
    assert ast.literal_eval(repr(cfg)) == cfg
    assert set(SCRIPT_FONTS) >= {"latin", "tamil", "devanagari", "telugu", "kannada", "malayalam"}


@pytest.mark.parametrize(
    ("beats", "total"),
    [([0.0, float("nan")], 5.0), ([0.0], float("nan")), ([float("inf")], 5.0), ([0.0], float("inf"))],
)
def test_build_config_rejects_non_finite_times(beats: list[float], total: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        build_config(beat_times=beats, total_duration=total)


def test_assemble_script_tracks_user_lines() -> None:
    cfg = build_config(beat_times=[0.5], total_duration=3)
    script = assemble_script(SCENE, cfg)
    offset = user_line_offset(script)
    lines = script.splitlines()
    assert lines[offset] == "from manim import *"  # first scene line follows the marker
    assert lines[offset - 1] == END_MARKER
    header = ast.literal_eval(lines[1].split("=", 1)[1].strip())
    assert header["user_line_offset"] == offset
    assert runtime_source() in script
    ast.parse(script)
    assert user_line_offset("no marker") == 0


def test_scene_class_name() -> None:
    assert scene_class_name(SCENE) == "Demo"
    assert scene_class_name("class X(Scene):\n    pass\n") is None
    assert scene_class_name("class (") is None


def test_module_attribute_source() -> None:
    from aadhi.manim import aadhi_scene

    assert "class AadhiScene(MovingCameraScene)" in aadhi_scene.AADHI_SCENE_SOURCE
    with pytest.raises(AttributeError):
        aadhi_scene.DOES_NOT_EXIST  # noqa: B018


# --- runtime helpers (executed in-process from the assembled script) ------------------------------


@pytest.mark.parametrize(
    ("tex", "plain"),
    [
        (r"I = \frac{V}{R}", "I = V/R"),
        (r"x^{2} + y_{1}", "x² + y₁"),
        (r"x^2 - y_i", "x² - y_i"),
        (r"\alpha + \beta \leq \pi", "α + β ≤ π"),
        (r"\sqrt{2}", "√2"),
        (r"\text{speed} = \frac{d}{t}", "speed = d/t"),
        (r"\frac{a+b}{c}", "(a+b)/c"),
        (r"\left( a \right)", "( a )"),
        (r"F = m \cdot a", "F = m · a"),
        (r"e^{i\pi}", "e^(iπ)"),
        ("", " "),
    ],
)
def test_tex_to_plain(runtime: dict[str, Any], tex: str, plain: str) -> None:
    assert runtime["aadhi_tex_to_plain"](tex) == plain


@pytest.mark.parametrize(
    ("tex", "parts"),
    [
        ("a = b + c", ["a", "=", "b", "+", "c"]),
        (r"\frac{a+b}{c} = d", [r"\frac{a+b}{c}", "=", "d"]),
        (r"I = \frac{V}{R}", ["I", "=", r"\frac{V}{R}"]),
        (r"V = I \times R", ["V", "=", "I", r"\times", "R"]),
        (r"e^{-x}", [r"e^{-x}"]),
        (r"\left( a + b \right)", [r"\left( a + b \right)"]),
        (r"-x", ["-", "x"]),
    ],
)
def test_tex_parts(runtime: dict[str, Any], tex: str, parts: list[str]) -> None:
    assert runtime["aadhi_tex_parts"](tex) == parts


def test_script_detection_and_fonts(runtime: dict[str, Any]) -> None:
    script_of = runtime["aadhi_script_of"]
    assert script_of("Ohm's law") == "latin"
    assert script_of("ஓமின் விதி") == "tamil"
    assert script_of("ओम का नियम V = IR") == "devanagari"
    assert script_of("ఓం నియమం") == "telugu"
    assert script_of("ഓം നിയമം") == "malayalam"
    font = runtime["aadhi_font_for"]("ஓமின் விதி")
    assert isinstance(font, str)  # '' when no candidate font is installed (Pango falls back)
    assert runtime["_aadhi_pick_font"](["Definitely Not A Font 123"]) == ""


def test_tex_runtime_check_blocks_injection(runtime: dict[str, Any]) -> None:
    calls: list[str] = []
    runtime["_aadhi_orig_generate_tex_file"] = lambda expr, env=None, tpl=None: calls.append(expr) or Path("x.tex")
    tfw = runtime["_aadhi_tfw"]
    marker = r"\special{dvisvgm:raw <g id='unique000'>}V = IR\special{dvisvgm:raw </g>}"
    assert tfw.generate_tex_file(marker, "align*") == Path("x.tex")
    assert calls == [marker]
    for evil in (r"\input{/etc/passwd}", r"^^5cinput{x}", r"\special{ps: x}", r"\csname input\endcsname"):
        with pytest.raises(ValueError, match="forbidden"):
            tfw.generate_tex_file(evil, "align*")
    with pytest.raises(ValueError, match="forbidden"):
        tfw.generate_tex_file("x", "filecontents")
    from manim import TexTemplate

    custom = TexTemplate(preamble=r"\usepackage{xcolor}")
    with pytest.raises(ValueError, match="custom TeX templates"):
        tfw.generate_tex_file("x", None, custom)


def test_tex_flags_are_inserted(runtime: dict[str, Any]) -> None:
    cmd = runtime["_aadhi_tfw"].make_tex_compilation_command("latex", ".dvi", Path("a.tex"), Path("out"))
    assert cmd[:3] == ["latex", "-disable-installer", "-disable-write18"]
    assert "not-a-flag" not in cmd


def _scene(runtime: dict[str, Any], beats: list[float], total: float, steps: int, now: float = 0.0):
    scene = object.__new__(runtime["AadhiScene"])
    scene.BEAT_TIMES = beats
    scene.TOTAL_DURATION = total
    scene.step_total = steps
    scene.renderer = SimpleNamespace(time=now, num_plays=1)
    return scene


def test_beat_timing_math(runtime: dict[str, Any]) -> None:
    s = _scene(runtime, [0.5, 2.5], 6.0, 2)
    assert s.beat_time(0) == 0.5 and s.beat_time(1) == 2.5
    assert s.beat_window(0) == pytest.approx(2.0) and s.beat_window(1) == pytest.approx(3.5)
    s.declare_steps(4)  # two extra steps share the tail after the last beat
    assert s.beat_time(2) == pytest.approx(2.5 + 3.5 / 3) and s.beat_time(3) == pytest.approx(2.5 + 7.0 / 3)
    none = _scene(runtime, [], 8.0, 4)
    assert [none.beat_time(i) for i in range(4)] == pytest.approx([0.0, 2.0, 4.0, 6.0])
    nearly = _scene(runtime, [0.5, 2.5], 6.0, 2, now=2.3)
    assert nearly.step_run_time(0, preferred=5.0) == pytest.approx(0.75 * 0.2)  # only 0.2 s of the window left
    late = _scene(runtime, [0.5, 2.5], 6.0, 2, now=2.6)
    assert late.step_run_time(0, preferred=5.0) == pytest.approx(late.frame_dt)  # behind schedule -> 1 frame
    on_time = _scene(runtime, [0.5, 2.5], 6.0, 2, now=0.5)
    assert on_time.step_run_time(0, preferred=5.0) == pytest.approx(1.5)  # 75% of the 2 s window
    assert on_time.step_run_time(0, preferred=0.4) == pytest.approx(0.4)


def test_theme_and_layout_helpers(runtime: dict[str, Any]) -> None:
    s = _scene(runtime, [0.5], 3.0, 1)
    s.uses_title = s.uses_notes = False
    assert s.color("gold") == "#FFD700" and s.color("nope") == "#FFFFFF"
    assert s.palette(9) == runtime["AADHI_PALETTE"][1]
    cx, cy, w, h = s.content_box()
    fw, fh = s.frame_size()
    assert w == pytest.approx(fw - 2 * s.MARGIN) and h == pytest.approx(fh - 2 * s.MARGIN)
    _, cy2, _, h2 = s.content_box(title=True, notes=True)
    assert h2 == pytest.approx(h - s.TITLE_ZONE - s.NOTE_ZONE)
    from manim import Rectangle

    big = Rectangle(width=40, height=2)
    s.fit_to_safe_area(big)
    assert big.width == pytest.approx(w) and big.get_center()[1] == pytest.approx(cy)
    small = Rectangle(width=1, height=1)
    s.fit_to_safe_area(small, grow=True, max_grow=1.5)
    assert small.width == pytest.approx(1.5)


def _counting_scene(runtime: dict[str, Any], beats: list[float], total: float, *, updaters: bool = False,
                    mobjects: list | None = None):
    """A bare AadhiScene whose ``wait`` counts frames exactly like manim's Cairo renderer.

    Frozen waits write ``int(d / dt)`` frames; waits with updaters iterate ``np.arange(0, d, dt)``.
    ``renderer.time`` advances by ``frames * dt`` (CairoRenderer.add_frame).
    """
    import numpy as np

    scene = _scene(runtime, beats, total, len(beats))
    scene.renderer.camera = SimpleNamespace(use_z_index=False)
    scene.renderer.num_plays = 0
    scene.always_update_mobjects = False
    scene.updaters = [lambda dt: None] if updaters else []
    scene.mobjects = list(mobjects or [])
    scene.frames = []

    def wait(duration, frozen_frame=None):
        dt = scene.frame_dt
        duration = max(duration, dt)  # Scene.validate_run_time
        n = int(duration / dt) if frozen_frame else len(np.arange(0, duration, dt))
        scene.renderer.time += n * dt
        scene.renderer.num_plays += 1
        scene.frames.append((n, bool(frozen_frame)))

    scene.wait = wait
    return scene


@pytest.mark.parametrize("updaters", [False, True])
@pytest.mark.parametrize("frames", [1, 2, 3, 7, 15, 60, 61, 599])
def test_hold_frames_is_exact_on_both_wait_paths(runtime: dict[str, Any], updaters: bool, frames: int) -> None:
    scene = _counting_scene(runtime, [0.0], 20.0, updaters=updaters)
    scene.hold_frames(frames)
    assert scene.frames == [(frames, not updaters)]
    assert scene.now() == pytest.approx(frames * scene.frame_dt)
    scene.hold_frames(0)
    scene.hold_frames(-3)
    assert len(scene.frames) == 1


def test_is_static_detects_time_based_updaters(runtime: dict[str, Any]) -> None:
    from manim import Dot

    dot = Dot()
    assert _counting_scene(runtime, [0.0], 1.0, mobjects=[dot]).is_static()
    dot.add_updater(lambda m, dt: m.shift(dt * 0))
    assert not _counting_scene(runtime, [0.0], 1.0, mobjects=[dot]).is_static()
    assert not _counting_scene(runtime, [0.0], 1.0, updaters=True).is_static()


@pytest.mark.parametrize("updaters", [False, True])
def test_beats_and_finish_land_on_exact_frames(runtime: dict[str, Any], updaters: bool) -> None:
    beats = [0.3, 1.234, 2.0, 3.71]
    total = 5.55
    scene = _counting_scene(runtime, beats, total, updaters=updaters)
    dt = scene.frame_dt
    for i, beat in enumerate(beats):
        scene.wait_until_beat(i)
        assert scene.now() == pytest.approx(round(beat / dt) * dt), f"beat {i}"
        scene.renderer.time += 7 * dt  # an animation of 7 frames
    scene.finish()
    assert scene.now() == pytest.approx(round(total / dt) * dt)
    frames = sum(n for n, _ in scene.frames) + 7 * len(beats)
    assert frames == round(total / dt)
    scene.finish()  # idempotent
    assert scene.now() == pytest.approx(round(total / dt) * dt)


def test_late_beats_do_not_wait_and_empty_scene_gets_a_frame(runtime: dict[str, Any]) -> None:
    scene = _counting_scene(runtime, [0.5, 1.0], 2.0)
    scene.renderer.time = 1.5  # behind schedule
    scene.wait_until_beat(1)
    assert scene.frames == []
    empty = _counting_scene(runtime, [], 0.0)
    empty.finish()
    assert empty.frames == [(1, True)]
