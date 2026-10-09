"""Real renders: every template through the subprocess sandbox + ffmpeg, timing exactness, self-healing.

Run with ``pytest tests/manim -m slow``. Needs manim, ffmpeg and (for MathTex) a LaTeX install.
"""

from __future__ import annotations

import copy
import time
from io import BytesIO
from pathlib import Path

import pytest

from aadhi.manim import media, render
from aadhi.manim.base import ManimError, ManimRenderRequest
from aadhi.manim.sandbox import SubprocessRunner
from aadhi.manim.templates import registry, validate_params
from aadhi.schemas.screenplay import ManimSpec

pytestmark = pytest.mark.slow

TOLERANCE = 0.15
FULLSCREEN_L = (854, 480)
PANEL_L = (480, 600)


@pytest.fixture(autouse=True)
def generous_timeout(job_ctx):
    """Correctness tests must not fail because a shared CI box is busy (timeouts have their own test)."""
    job_ctx.settings = job_ctx.settings.model_copy(update={"manim_timeout_seconds": 900})


def template_request(name: str, target: str = "fullscreen", language: str = "en-IN",
                     params: dict | None = None) -> ManimRenderRequest:
    params = copy.deepcopy(params or registry[name].example_params)
    model, problems = validate_params(name, params)
    assert not problems, problems
    steps = registry[name].step_count(model)
    beats = [round(0.4 + 1.3 * i, 3) for i in range(steps)]
    return ManimRenderRequest(spec=ManimSpec(template=name, params=params), beat_times=beats,
                              total_duration=round(beats[-1] + 1.5, 3), target=target, quality="l",
                              language=language, title=name)


def stored_file(ctx, result) -> Path:
    path = ctx.storage.local_path(result.storage_key)
    assert path is not None and path.is_file()
    return path


async def assert_video(ctx, result, req: ManimRenderRequest, size: tuple[int, int]) -> None:
    assert result.duration == pytest.approx(req.total_duration, abs=TOLERANCE)
    assert (result.width, result.height) == size
    info = await media.probe(stored_file(ctx, result), ctx.settings)
    assert info.duration == pytest.approx(req.total_duration, abs=TOLERANCE)
    assert (info.width, info.height) == size and info.fps == 15.0


@pytest.mark.parametrize("name", sorted(registry))
async def test_every_template_renders_with_exact_duration(job_ctx, name: str) -> None:
    req = template_request(name)
    result = await render.render_manim(job_ctx, req)
    assert not result.cached and not result.healed and result.final_spec == req.spec
    await assert_video(job_ctx, result, req, FULLSCREEN_L)


@pytest.mark.parametrize("name", ["circuit_basic", "block_diagram", "timeline_steps", "function_plot"])
async def test_panel_target_is_portrait(job_ctx, name: str) -> None:
    req = template_request(name, target="panel")
    result = await render.render_manim(job_ctx, req)
    await assert_video(job_ctx, result, req, PANEL_L)


async def test_second_render_is_a_cache_hit(job_ctx) -> None:
    req = template_request("wave")
    first = await render.render_manim(job_ctx, req)
    started = time.monotonic()
    second = await render.render_manim(job_ctx, req)
    assert second.cached and second.asset_key == first.asset_key and second.storage_key == first.storage_key
    assert time.monotonic() - started < 5


async def test_indic_labels_render(job_ctx) -> None:
    params = {
        "title": "ஓமின் விதி",
        "events": [
            {"marker": "படி 1", "title": "மின்னழுத்தம் V", "detail": "மின்கலம் 12 வோல்ட் தருகிறது"},
            {"marker": "चरण 2", "title": "प्रतिरोध R", "detail": "प्रतिरोध धारा का विरोध करता है"},
        ],
    }
    req = template_request("timeline_steps", language="ta-IN", params=params)
    result = await render.render_manim(job_ctx, req)
    await assert_video(job_ctx, result, req, FULLSCREEN_L)


async def test_templates_fall_back_to_plain_text_without_latex(job_ctx, monkeypatch) -> None:
    monkeypatch.setattr(SubprocessRunner, "has_latex", lambda self: False)
    req = template_request("equation_steps")
    result = await render.render_manim(job_ctx, req)
    assert job_ctx.assets.get(result.asset_key).meta["latex"] is False
    await assert_video(job_ctx, result, req, FULLSCREEN_L)


SYNC_SCENE = """
from manim import *


class Sync(AadhiScene):
    def construct(self):
        colors = ["#FF0000", "#00FF00", "#0000FF"]
        for i, color in enumerate(colors):
            self.wait_until_beat(i)
            self.add(Square(side_length=30, fill_color=color, fill_opacity=1, stroke_width=0))
""".strip()


async def test_beats_change_the_picture_on_the_right_frame(job_ctx) -> None:
    """The visual state switches exactly at the (frame-rounded) beat times."""
    from PIL import Image

    beats = [0.6, 1.4, 2.25]
    req = ManimRenderRequest(spec=ManimSpec(code=SYNC_SCENE), beat_times=beats, total_duration=3.0, quality="l")
    result = await render.render_manim(job_ctx, req)
    assert not result.healed
    await assert_video(job_ctx, result, req, FULLSCREEN_L)
    probes = [0.5, 0.7, 1.3, 1.5, 2.15, 2.35, 2.9]
    frames = await media.extract_frames(stored_file(job_ctx, result), probes, job_ctx.settings, max_width=160)

    def dominant(png: bytes) -> str:
        r, g, b = Image.open(BytesIO(png)).convert("RGB").getpixel((80, 45))
        if max(r, g, b) < 80:
            return "background"
        return "rgb"[[r, g, b].index(max(r, g, b))]

    assert [dominant(f) for f in frames] == ["background", "r", "r", "g", "g", "b", "b"]


BROKEN = """
from manim import *


class Demo(AadhiScene):
    def construct(self):
        self.wait_until_beat(0)
        self.play(Create(Circl()), run_time=self.step_run_time(0))
        self.play_step(1, FadeIn(Square()))
""".strip()
FIXED = BROKEN.replace("Circl()", "Circle()")


async def test_freeform_code_is_repaired_and_rendered(job_ctx, scripted_llm) -> None:
    scripted_llm.queue("ManimCodeFix", {"diagnosis": "typo", "code": FIXED})
    req = ManimRenderRequest(spec=ManimSpec(code=BROKEN), beat_times=[0.3, 1.5], total_duration=3.2, quality="l")
    result = await render.render_manim(job_ctx, req)
    assert result.healed and result.final_spec.code == FIXED
    await assert_video(job_ctx, result, req, FULLSCREEN_L)
    prompt = scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]
    assert "line 7, in construct" in prompt and "NameError: name 'Circl' is not defined" in prompt
    again = await render.render_manim(job_ctx, req)
    assert again.cached and again.healed and again.final_spec.code == FIXED


async def test_runaway_code_times_out(job_ctx) -> None:
    job_ctx.settings = job_ctx.settings.model_copy(update={"manim_timeout_seconds": 6,
                                                           "manim_max_repair_attempts": 0})
    code = "from manim import *\n\n\nclass Spin(AadhiScene):\n    def construct(self):\n        while True:\n" \
           "            pass\n"
    req = ManimRenderRequest(spec=ManimSpec(code=code), beat_times=[0.5], total_duration=2.0, quality="l")
    started = time.monotonic()
    with pytest.raises(ManimError, match="timed out"):
        await render.render_manim(job_ctx, req)
    assert time.monotonic() - started < 30


@pytest.mark.parametrize("key", ["circuit_parallel", "graph_dfa", "bar_negative", "vf_ball", "timeline_8",
                                 "fp_asymptote", "tt_nand4"])
async def test_edge_case_variants_render(job_ctx, key: str) -> None:
    from tests.manim.variants import VARIANTS

    name, params = VARIANTS[key]
    req = template_request(name, params=params)
    result = await render.render_manim(job_ctx, req)
    await assert_video(job_ctx, result, req, FULLSCREEN_L)
