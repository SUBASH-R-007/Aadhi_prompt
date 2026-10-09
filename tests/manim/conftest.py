"""Fixtures for Manim tests (scripted LLM, fake sandbox, fast media helpers, in-process runtime)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.manim.fakes import FakeRunner, ScriptedLLM, install_fast_media

RUNTIME_SCENE = (
    "from manim import *\n\nclass Demo(AadhiScene):\n    def construct(self):\n        self.play_step(0, Create(Circle()))\n"
)


@pytest.fixture()
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """A ScriptedLLM returned by ``aadhi.manim.llm.get_llm``."""
    from aadhi.manim import llm

    fake = ScriptedLLM()
    monkeypatch.setattr(llm, "get_llm", lambda settings, engine=None: fake)
    return fake


@pytest.fixture()
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """A FakeRunner returned by ``aadhi.manim.render.get_runner``."""
    from aadhi.manim import render

    runner = FakeRunner()
    monkeypatch.setattr(render, "get_runner", lambda settings: runner)
    return runner


@pytest.fixture()
def fast_media(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """ffmpeg helpers replaced by pure-Python fakes."""
    return install_fast_media(monkeypatch)


@pytest.fixture()
def make_request():
    """Factory for ManimRenderRequest with sensible defaults."""
    from aadhi.manim.base import ManimRenderRequest
    from aadhi.schemas.screenplay import ManimSpec

    def factory(template: str | None = None, params: dict | None = None, code: str | None = None,
                beats: int = 3, **kw: Any) -> ManimRenderRequest:
        spec = ManimSpec(template=template, params=params or {}, code=code)
        beat_times = kw.pop("beat_times", [0.5 + 2.0 * i for i in range(beats)])
        total = kw.pop("total_duration", (beat_times[-1] if beat_times else 0.0) + 2.0)
        return ManimRenderRequest(spec=spec, beat_times=beat_times, total_duration=total, quality=kw.pop("quality", "l"),
                                  **kw)

    return factory


@pytest.fixture()
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Namespace of an assembled scene script executed in-process (runtime + a tiny scene).

    The runtime patches manim globally (TeX writer, ``Code``, ``Scene.add_sound``, ``manim.__all__``);
    every patch is undone afterwards so other in-process users see the originals.
    """
    from aadhi.manim.aadhi_scene import assemble_script, build_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AADHI_TEX_FLAGS", "-disable-installer -disable-write18 not-a-flag")
    monkeypatch.setenv("AADHI_DVISVGM_FLAGS", "--miktex-disable-installer not-a-flag")
    cfg = build_config(beat_times=[0.5, 2.5], total_duration=6.0, target="fullscreen", has_latex=True)
    script = assemble_script(RUNTIME_SCENE, cfg)
    namespace: dict[str, Any] = {"__name__": "aadhi_runtime_test", "__file__": str(tmp_path / "scene.py")}
    try:
        exec(compile(script, str(tmp_path / "scene.py"), "exec"), namespace)  # noqa: S102 - testing the runtime
        yield namespace
    finally:
        _undo_runtime_patches(namespace)


def _undo_runtime_patches(ns: dict[str, Any]) -> None:
    import manim
    from manim import Code, Scene

    tfw = ns.get("_aadhi_tfw")
    if tfw is not None:
        for attr, original in (("generate_tex_file", "_aadhi_orig_generate_tex_file"),
                               ("make_tex_compilation_command", "_aadhi_orig_make_cmd"),
                               ("subprocess", "_aadhi_orig_tfw_subprocess")):
            if original in ns:
                setattr(tfw, attr, ns[original])
    if "_aadhi_orig_svg_et" in ns:
        ns["_aadhi_svg_mod"].ET = ns["_aadhi_orig_svg_et"]
    if "_aadhi_orig_raster_paths" in ns:
        ns["_aadhi_camera_mod"].get_full_raster_image_path, ns["_aadhi_image_mod"].get_full_raster_image_path = (
            ns["_aadhi_orig_raster_paths"]
        )
    if "_aadhi_orig_code_init" in ns:
        Code.__init__ = ns["_aadhi_orig_code_init"]
    if "_aadhi_orig_add_sound" in ns:
        Scene.add_sound = ns["_aadhi_orig_add_sound"]
    if "_aadhi_orig_manim_all" in ns:
        if ns["_aadhi_orig_manim_all"] is None:
            if hasattr(manim, "__all__"):
                del manim.__all__
        else:
            manim.__all__ = ns["_aadhi_orig_manim_all"]
