"""The generated stage catalogue (aadhi.compose.dynamic.catalogue -> web/js/dynamic/catalogue.gen.js; B2 plan §1.1,
deviation register 1).

* Its schema: the top-level keys and version are pinned; its zones are core.js's, its registries the Python ones.
* The committed file is current, generation is byte-deterministic (twice, and across hash seeds), and ``--check``
  fails on a modified, truncated or missing copy, naming the file.
* The file is a valid ES module whose ``CATALOGUE`` is frozen all the way down and holds exactly the generated data
  (checked with node when it is installed: CI's Python job has it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.compose.dynamic import catalogue as cat
from aadhi.compose.dynamic import templates as tp
from aadhi.compose.dynamic import themes as th
from aadhi.compose.dynamic import zones as zn

from .test_zones import CORE_JS_ZONES

ROOT = Path(__file__).resolve().parents[3]


def test_the_schema_is_pinned() -> None:
    data = cat.catalogue()
    assert cat.CATALOGUE_VERSION == 1 and data["version"] == 1 and data["zones_version"] == zn.ZONES_VERSION == 1
    assert tuple(data) == cat.TOP_LEVEL_KEYS == (
        "version", "zones_version", "zones", "regions", "grid", "end_screen", "platforms", "type", "layout",
        "captions", "cues", "templates", "semantic_classes", "targets", "roles", "backgrounds", "transitions",
        "personalities", "scrims", "studio_surface", "families", "content")
    assert data["zones"] == CORE_JS_ZONES  # what core.js draws with today, value for value
    assert set(data["regions"]["landscape"]) == {"full", "left-55", "right-45", "centre-60", "top-band", "stage"}
    assert set(data["regions"]["portrait"]) == {"hook", "headline", "stage", "tall", "text-above", "text-below", "clip"}
    assert data["grid"] == {"landscape": {"box": {"x0": 96, "y0": 54, "x1": 1824, "y1": 912}, "columns": 12,
                                          "gutter": 24, "column_width": 122}}
    assert data["end_screen"] == {"landscape": {"box": {"x0": 960, "y0": 0, "x1": 1920, "y1": 1080}, "seconds": 15.0}}
    assert set(data["platforms"]) == {"yt_shorts", "meta_reels", "tiktok", "universal"}
    assert data["platforms"]["universal"]["safe"] == [{"x0": 120, "y0": 288, "x1": 888, "y1": 840},
                                                     {"x0": 120, "y0": 840, "x1": 780, "y1": 1248}]
    assert set(data["type"]) == {"floors", "scale", "caps"} and data["type"]["floors"]["portrait"]["text_min"] == 40
    assert set(data["layout"]) == {"spacing", "radius", "border", "outline", "shadow", "icon", "alpha"}
    assert data["layout"]["alpha"] == {"dim": 0.7, "scrim": 0xC7 / 255, "scrim_min": 0.55, "scrim_hex": "c7"}
    assert data["layout"]["alpha"]["scrim"] >= data["layout"]["alpha"]["scrim_min"]  # §4.5: a scrim is >= 55%


def test_the_registries_are_the_python_ones() -> None:
    data = cat.catalogue()
    assert set(data["templates"]) == set(tp.TEMPLATES) and len(data["templates"]) == 41
    keys = {"class", "representational", "release", "implemented", "formats", "variants", "forced_for", "duration",
            "holds", "dense", "push_ok", "slots", "consumes"}
    assert all(set(t) == keys for t in data["templates"].values())
    assert {t for t, v in data["templates"].items() if v["implemented"]} == set(tp.IMPLEMENTED) >= {"board_generic"}
    bg = data["templates"]["board_generic"]
    assert bg["slots"]["title"]["portrait"]["default"] == {
        "w": 696, "max_lines": 2, "size": [56, 88], "role": "title", "face": "display", "weight": 700,
        "line_height": 1.06, "tracking_em": -0.015, "max_words": None, "max_chars": None, "prefer_lines": 1,
        "critical": True}
    assert bg["consumes"] == {"kinds": ["reveal", "fill"], "targets": ["item"]}
    assert data["templates"]["thumbnail"]["formats"] == ["landscape"]
    assert data["templates"]["clip_scene"]["duration"]["portrait"] == {
        "min": 0.5, "max": None, "per_item": False, "end_screen": None}
    assert data["templates"]["outro_next"]["duration"] == {
        "landscape": {"min": 6.0, "max": 10.0, "per_item": False, "end_screen": 15.0},
        "portrait": {"min": 3.0, "max": 5.0, "per_item": False, "end_screen": None}}
    assert {c: v["templates"] for c, v in data["semantic_classes"].items()} == {
        c: list(r.templates) for c, r in tp.SEMANTIC_CLASSES.items()}
    assert data["targets"] == {"forms": list(tp.TARGET_FORMS), "reserved": ["option", "panel"]}
    assert data["roles"] == {"colour": list(tp.ROLES), "block": list(th.DERIVED_KEYS)}
    assert {b for b, v in data["backgrounds"].items() if v["implemented"]} == {"mesh_drift", "colour_block"}
    assert {t for t, v in data["transitions"].items() if v["implemented"]} == {"cut", "crossfade"}
    assert set(data["personalities"]) == set(th.PERSONALITIES)
    assert data["personalities"]["soft"]["caption_style"] == {"landscape": "clean_pill", "portrait": "clean_pill"}
    released = {s for s, v in data["captions"].items() if v["metrics"] is not None}
    assert released == {"clean_pill", "bold_outline"} and data["captions"]["bold_outline"]["metrics"]["outline_px"] == 6
    # plan §1.1: caption-style metrics are geometry (the stage may read them); the colours sit apart (gallery only)
    for sid, style in data["captions"].items():
        assert set(style) == {"release", "look", "metrics", "colours"}, sid
        assert (style["metrics"] is None) == (style["colours"] is None), sid
        if style["metrics"] is not None:
            assert set(style["metrics"]) == {"padding_x", "padding_y", "radius_px", "outline_px", "word_spacing_em",
                                             "size", "weight", "line_height"}, sid
            assert set(style["colours"]) == {"ink", "backdrop", "backdrop_alpha", "outline", "key_ink"}, sid
    assert data["captions"]["clean_pill"]["colours"] == {
        "ink": th.CAPTION_INK, "backdrop": th.CAPTION_PILL, "backdrop_alpha": th.CAPTION_PILL_ALPHA, "outline": None,
        "key_ink": th.CAPTION_MARK}
    assert data["cues"] == {m: {"marker_ink": c.marker_ink, "marker_alpha": c.marker_alpha, "underline": c.underline}
                            for m, c in th.CUE_COLOURS.items()}
    assert data["scrims"] == th.forced_scrims() and set(data["scrims"]) == set(th.PRESETS_MIRRORED)
    assert data["studio_surface"] is None and th.STUDIO_WALL is None  # B2f measures it; never a guessed value
    assert set(data["families"]) == set(th.FAMILIES)
    for fid, family in data["families"].items():
        f = th.FAMILIES[fid]
        assert family["slots"] == [th.scene_tokens(f, k) for k in range(f.accent_period)]
        assert all(list(tokens) == list(th.SCENE_TOKEN_KEYS) for tokens in family["slots"])
    assert data["content"] == {"dark": th.content_tokens("dark"), "light": th.content_tokens("light")}


def test_the_committed_file_is_current() -> None:
    """The CI step (``--check``) in the test suite too: run ``python -m aadhi.compose.dynamic.catalogue`` after
    changing zones.py, templates.py or themes.py."""
    assert cat.read() == cat.render(), "web/js/dynamic/catalogue.gen.js is out of date"
    assert cat.main(["--check"]) == 0


def test_the_file_layout() -> None:
    text = cat.render()
    lines = text.split("\n")
    assert lines[0] == "// generated by python -m aadhi.compose.dynamic.catalogue — do not edit" == cat.HEADER
    assert lines[1] == "// @ts-check" and "export const CATALOGUE = Object.freeze(freezeInside({" in lines
    assert text.endswith("}));\n") and "\r" not in text and lines[-1] == ""
    assert max(len(line) for line in lines) <= cat.WIDTH
    assert "GEOMETRY ONLY" in text


def test_generation_is_byte_deterministic(tmp_path: Path) -> None:
    assert cat.render() == cat.render()
    outputs = []
    for seed in ("0", "1", "4242"):  # set or dict order must never leak into the bytes
        out = tmp_path / f"catalogue-{seed}.js"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-m", "aadhi.compose.dynamic.catalogue", "--path", str(out)],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
        assert proc.returncode == 0, proc.stderr
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1] == outputs[2] == cat.render().encode("utf-8")


def test_check_fails_on_a_modified_truncated_or_missing_copy(tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    copy = tmp_path / "catalogue.gen.js"
    assert cat.main(["--path", str(copy)]) == 0 and copy.exists()  # writes a missing file
    assert "wrote" in capsys.readouterr().out
    assert cat.main(["--check", "--path", str(copy)]) == 0
    assert cat.main(["--path", str(copy)]) == 0 and capsys.readouterr().out == ""  # current: nothing written
    text = copy.read_text(encoding="utf-8")
    line = text.split("\n").index('      "caption": {"x0": 120, "x1": 780, "y0": 1040, "y1": 1240},') + 1
    copy.write_bytes(text.replace('"y1": 1240}', '"y1": 1241}').encode("utf-8"))
    assert cat.main(["--check", "--path", str(copy)]) == 1
    err = capsys.readouterr().err
    assert str(copy) in err and f"line {line}" in err and "python -m aadhi.compose.dynamic.catalogue" in err
    copy.write_bytes(text[:-1].encode("utf-8"))  # only the final newline gone
    assert cat.main(["--check", "--path", str(copy)]) == 1
    copy.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))  # CRLF line endings
    assert cat.main(["--check", "--path", str(copy)]) == 1
    copy.unlink()
    assert cat.main(["--check", "--path", str(copy)]) == 1
    assert "line 1" in capsys.readouterr().err


def test_check_from_the_command_line_names_the_committed_file() -> None:
    proc = subprocess.run([sys.executable, "-m", "aadhi.compose.dynamic.catalogue", "--check"], cwd=ROOT,
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr


def test_first_difference() -> None:
    assert cat.first_difference("a\nb", "a\nb") is None
    assert cat.first_difference(None, "a") == 1
    assert cat.first_difference("a\nx\nc", "a\nb\nc") == 2
    assert cat.first_difference("a\nb", "a\nb\nc") == 3


def test_only_plain_data_is_serialised() -> None:
    assert cat._plain({"a": (1, 2.5, None, True, "x"), "b": {"c": []}}) == {"a": [1, 2.5, None, True, "x"],
                                                                          "b": {"c": []}}
    for bad in ({"a": object()}, {"a": float("nan")}, {1: "x"}, {"a": {1, 2}}, {"a": zn.Box(0, 0, 1, 1)}):
        with pytest.raises(TypeError):
            cat._plain(bad)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed (CI's Python job installs it)")
@pytest.mark.parametrize("source", ["committed", "rendered"])
def test_the_module_loads_in_node_frozen_all_the_way_down(source: str, tmp_path: Path) -> None:
    """The committed file and what the generator writes now (so a generator change is checked before it is
    committed) are ES modules whose CATALOGUE is frozen at every level and holds exactly ``catalogue()``."""
    path = ROOT / "web" / "js" / "dynamic" / "catalogue.gen.js"
    if source == "rendered":
        path = tmp_path / "catalogue.gen.js"
        path.write_bytes(cat.render().encode("utf-8"))
    script = """
const { CATALOGUE } = await import(process.argv[1]);
let objects = 0;
const unfrozen = [];
const walk = (v, path) => {
  if (v === null || typeof v !== 'object') return;
  objects += 1;
  if (!Object.isFrozen(v)) unfrozen.push(path);
  for (const [k, x] of Object.entries(v)) walk(x, `${path}.${k}`);
};
walk(CATALOGUE, 'CATALOGUE');
process.stdout.write(JSON.stringify({ objects, unfrozen, data: CATALOGUE }));
"""
    url = path.as_uri()
    proc = subprocess.run(["node", "--input-type=module", "-e", script, url], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["unfrozen"] == [] and out["objects"] > 500
    assert out["data"] == cat.catalogue()  # the module holds exactly the generated data (numbers round-trip)
