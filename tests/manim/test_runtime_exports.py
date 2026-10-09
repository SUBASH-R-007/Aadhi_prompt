"""The injected runtime narrows ``from manim import *`` to an allow-list and closes file/process paths.

These tests execute the assembled runtime in-process (``runtime`` fixture in conftest.py) and then
look at what scene code can reach.
"""

from __future__ import annotations

import ast
import builtins
import subprocess
import types
from typing import Any

import numpy
import pytest

from aadhi.manim.guard import FORBIDDEN_MANIM_NAMES, STAR_MODULES
from aadhi.manim.templates import registry
from aadhi.manim.templates._base import scene_file_source

# Exported by an unfiltered `from manim import *` (manim 0.21) and dangerous for scene code.
DANGEROUS = {
    "capture", "get_video_metadata", "get_dir_layout", "guarantee_empty_existence", "guarantee_existence",
    "modify_atime", "open_file", "write_to_movie", "ensure_executable", "seek_full_path_from_defaults",
    "get_full_raster_image_path", "get_full_sound_file_path", "add_extension_if_not_present", "register_font",
    "get_plugins", "list_plugins", "SVGMobject", "VMobjectFromSVGPath", "ImageMobject", "ImageMobjectFromCamera",
    "Typst", "MathTypst", "SceneFileWriter", "CairoRenderer", "Camera", "MovingCamera", "MultiCamera", "tempconfig",
    "TexTemplate", "TexTemplateLibrary", "TexFontTemplates", "logger", "console", "error_console", "cli_ctx_settings",
    "CONTEXT_SETTINGS", "frame", "version", "QUALITIES",
}
# Names scenes and templates rely on.
TEACHING = {
    "Circle", "Square", "Rectangle", "Dot", "Line", "Arrow", "Vector", "Arc", "Polygon", "VGroup", "Group", "Text",
    "MathTex", "Tex", "MarkupText", "Paragraph", "Code", "DecimalNumber", "Integer", "Axes", "NumberPlane",
    "NumberLine", "FunctionGraph", "ParametricFunction", "BarChart", "Table", "Matrix", "Graph", "DiGraph", "Brace",
    "SurroundingRectangle", "ValueTracker", "always_redraw", "Create", "Write", "FadeIn", "FadeOut", "Transform",
    "ReplacementTransform", "TransformMatchingTex", "GrowArrow", "Indicate", "Circumscribe", "MoveAlongPath",
    "LaggedStart", "AnimationGroup", "Succession", "Rotate", "Scene", "MovingCameraScene", "ThreeDScene", "UP",
    "DOWN", "LEFT", "RIGHT", "ORIGIN", "UL", "DR", "PI", "TAU", "DEGREES", "BLUE", "RED", "YELLOW", "WHITE",
    "ManimColor", "interpolate_color", "color_gradient", "smooth", "linear", "there_and_back", "rotate_vector",
    "normalize", "interpolate", "config", "np", "rate_functions", "BOLD", "NORMAL", "DEFAULT_FONT_SIZE",
    "SMALL_BUFF", "MED_SMALL_BUFF", "LARGE_BUFF", "ParsableManimColor",
}


def star_import() -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec("from manim import *", namespace)  # noqa: S102 - inspecting what scene code receives
    namespace.pop("__builtins__", None)
    return namespace


def test_unfiltered_manim_exports_the_dangerous_names() -> None:
    """Sanity check of the premise (manim 0.21): without the runtime these are all reachable."""
    exported = star_import()
    assert {"capture", "guarantee_empty_existence", "open_file", "SVGMobject", "utils", "camera"} <= set(exported)


def test_star_import_is_narrowed_by_the_runtime(runtime: dict[str, Any]) -> None:
    exported = star_import()
    modules = {name for name, value in exported.items() if isinstance(value, types.ModuleType)}
    assert modules == set(STAR_MODULES) == {"np", "rate_functions"}
    assert exported["np"] is numpy
    from manim.utils import rate_functions

    assert exported["rate_functions"] is rate_functions
    assert not DANGEROUS & set(exported), DANGEROUS & set(exported)
    assert not (FORBIDDEN_MANIM_NAMES - {"manim"}) & set(exported)
    missing = TEACHING - set(exported)
    assert not missing, missing


def test_hidden_names_are_removed_from_the_script_namespace(runtime: dict[str, Any]) -> None:
    for name in DANGEROUS | {"utils", "camera", "scene", "core", "renderer", "plugins", "typing", "color"}:
        assert name not in runtime, name
    for name in ("Circle", "MathTex", "config", "np", "rate_functions", "AadhiScene", "AADHI_GOLD", "BEAT_TIMES"):
        assert name in runtime, name


def test_scene_files_only_use_exported_names(runtime: dict[str, Any]) -> None:
    """Every free name in every template scene file resolves after the runtime narrowed the namespace."""
    available = set(star_import()) | set(runtime) | set(dir(builtins)) | {"PARAMS"}
    for name, template in registry.items():
        tree = ast.parse(scene_file_source(template.scene_file))  # type: ignore[attr-defined]
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, ast.alias):
                bound.add(node.asname or node.name.split(".")[0])
        loaded = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unresolved = loaded - bound - available
        assert not unresolved, f"{name}: {sorted(unresolved)}"


def test_code_refuses_files_but_renders_strings(runtime: dict[str, Any], tmp_path) -> None:
    secret = tmp_path / "secret.py"
    secret.write_text("TOKEN = 'abc'\n", encoding="utf-8")
    code_cls = runtime["Code"]
    with pytest.raises(ValueError, match="code_file"):
        code_cls(code_file=str(secret))
    with pytest.raises(ValueError, match="code_file"):
        code_cls(str(secret))  # positional first argument is code_file
    with pytest.raises(ValueError, match="code_file"):
        code_cls(**{"code" + "_file": str(secret)})  # computed keywords cannot bypass the runtime check
    mob = code_cls(code_string="print('hi')\n", language="python")
    assert len(mob.submobjects) > 0


def _external_svg(tmp_path) -> str:
    target = tmp_path / "outside" / "secret.svg"
    target.parent.mkdir()
    target.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>', encoding="utf-8")
    return str(target)


def test_svg_files_outside_manims_own_output_are_refused(runtime: dict[str, Any], tmp_path) -> None:
    """Two-argument super() or an overridden get_file_path cannot load a host SVG/XML file."""
    text_cls = runtime["Text"]
    svg = _external_svg(tmp_path)

    class SkipText(text_cls):
        def __init__(self, path):
            super(text_cls, self).__init__(path)  # reaches SVGMobject.__init__(file_name)

    class Override(text_cls):
        def get_file_path(self):
            from pathlib import Path

            return Path(svg)

    with pytest.raises(ValueError, match="SVG files are not allowed"):
        SkipText(svg)
    with pytest.raises(ValueError, match="SVG files are not allowed"):
        Override("hello again", font_size=20)
    label = text_cls("hello", font_size=20)  # manim's own SVG output still loads
    assert len(label.submobjects) == 5


def test_raster_image_files_are_refused(runtime: dict[str, Any]) -> None:
    import manim.camera.camera as camera_mod
    import manim.mobject.types.image_mobject as image_mod

    for fn in (camera_mod.get_full_raster_image_path, image_mod.get_full_raster_image_path):
        with pytest.raises(ValueError, match="image files are not allowed"):
            fn("C:/Users/someone/Pictures/secret.png")
    mob = runtime["Square"]()
    mob.color_using_background_image("secret.png")  # the guard rejects this; the camera refuses to read it
    displayer = camera_mod.BackgroundColoredVMobjectDisplayer.__new__(camera_mod.BackgroundColoredVMobjectDisplayer)
    displayer.file_name_to_pixel_array_map = {}
    with pytest.raises(ValueError, match="image files are not allowed"):
        displayer.get_background_array("secret.png")


def test_add_sound_is_refused(runtime: dict[str, Any]) -> None:
    scene = object.__new__(runtime["AadhiScene"])
    with pytest.raises(ValueError, match="sounds are not allowed"):
        scene.add_sound("narration.wav")


def test_dvisvgm_gets_the_installer_flag(runtime: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: calls.append(list(cmd)) or "done")
    proxy = runtime["_aadhi_tfw"].subprocess
    assert proxy.run(["dvisvgm", "--page=1", "x.dvi"], stdout=subprocess.DEVNULL) == "done"
    assert proxy.run(["latex", "-halt-on-error", "x.tex"]) == "done"
    assert calls == [["dvisvgm", "--miktex-disable-installer", "--page=1", "x.dvi"], ["latex", "-halt-on-error", "x.tex"]]
    assert proxy.DEVNULL == subprocess.DEVNULL and proxy.PIPE == subprocess.PIPE


def test_runtime_patches_are_undone_after_the_fixture() -> None:
    import manim
    import manim.camera.camera as camera_mod
    import manim.mobject.svg.svg_mobject as svg_mod
    import manim.utils.tex_file_writing as tfw
    from manim import Code, Scene
    from manim.utils.images import get_full_raster_image_path

    assert not hasattr(manim, "__all__")
    assert tfw.subprocess is subprocess
    assert svg_mod.ET.__name__ == "xml.etree.ElementTree"
    assert camera_mod.get_full_raster_image_path is get_full_raster_image_path
    assert Code.__init__.__qualname__ == "Code.__init__" and Scene.add_sound.__qualname__ == "Scene.add_sound"
