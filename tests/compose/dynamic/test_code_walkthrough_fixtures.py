"""The code_walkthrough fixtures and the template's data rule (design §4.3 code_walkthrough, deviation 13; the CG-2
rule that a template's Python ``fit`` refuses exactly what its page's ``drawsScene`` refuses).

* The fixtures are what ``tests/compose/dynamic/make_code_walkthrough_fixtures.py`` builds (byte for byte), cover a
  dark and a light family in each format, fit the slow 1x / 3x / reverse gate's 300 frames closely enough that the
  gate sees every scene (``tests/determinism/test_template_fixtures.py``), and every scene is one the page draws
  itself (``code_walkthrough.js drawsScene`` under node), never one it hands to board_generic. Their validity as
  template fixtures is the shared validator's (``test_template_fixtures.py``).
* The page's ``codeLines`` is Python's ``[line.expandtabs(4) for line in code.splitlines()]``, case for case, every
  boundary ``str.splitlines`` splits at included (run under node).
* Once ``templates.py`` registers the template (``implemented=True``, the integrator's change), its ``CODE_BOX`` /
  ``code_lines`` / ``code_fits`` mirror the page's ``codeBox`` / ``codeLines`` / ``codeFits`` exactly over a grid of
  programs on every side of every limit, and ``fit`` accepts every fixture scene. Before that the handshake only
  checks the page's side.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.config import ROOT_DIR
from aadhi.schemas.screenplay import Beat, BoardItem, BoardScene, SidePanel
from tests.compose.dynamic import make_code_walkthrough_fixtures as cwf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES
MODULE = (ROOT_DIR / "web" / "js" / "dynamic" / "templates" / "code_walkthrough.js").as_uri()
CORE = (ROOT_DIR / "web" / "js" / "dynamic" / "core.js").as_uri()


def _node(script: str, payload: Any, tmp_path: Path) -> Any:
    """Run ``script`` (an ES module reading ``input`` and writing JSON to stdout) under node with ``payload``."""
    path = tmp_path / "cw_in.json"
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    head = (
        "const fs = await import('node:fs/promises');\n"
        "const input = JSON.parse(await fs.readFile(process.argv[1], 'utf8'));\n"
        f"const cw = await import('{MODULE}');\n"
        f"const core = await import('{CORE}');\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", head + script, str(path)], cwd=ROOT_DIR,
                          capture_output=True, text=True, encoding="utf-8", timeout=180, check=False)
    if proc.returncode != 0:
        pytest.fail(f"node failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


# --- the fixtures ---------------------------------------------------------------------------------------------------


def test_the_committed_fixtures_are_current() -> None:
    assert cwf.stale() == [], "run: python -m tests.compose.dynamic.make_code_walkthrough_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in cwf.fixtures().items():
        (tmp_path / name).write_text(cwf.render(tl), "utf-8", newline="\n")
    assert cwf.stale(tmp_path) == []
    names = sorted(cwf.fixtures())
    data = json.loads((tmp_path / names[0]).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / names[0]).write_text(json.dumps(data), "utf-8")
    (tmp_path / names[1]).unlink()
    assert cwf.stale(tmp_path) == names[:2]


@pytest.mark.parametrize("name", sorted(cwf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = cwf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "code_walkthrough"
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "code_walkthrough" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in cwf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_look_carries_the_editor_surface_and_a_frozen_background() -> None:
    from aadhi.compose import presentation as pr
    from aadhi.compose.dynamic import themes

    for name, tl in cwf.fixtures().items():
        assert tl.presentation is not None
        family = pr.family_for(tl.presentation.family)
        for s in tl.scenes:
            assert s.look is not None
            assert s.look.tokens[cwf.CODE_SURFACE_TOKEN] == themes.code_surface(family), name
            assert s.look.background["frozen_at"] == 0.0, f"{name}: R-55, a dense template"


def test_every_fixture_scene_is_drawn_by_the_template_itself(tmp_path: Path) -> None:
    scenes = [{"name": f"{n}:{s.scene_id}", "fmt": tl.presentation.format,  # type: ignore[union-attr]
               "scene": json.loads(s.model_dump_json())}
              for n, tl in cwf.fixtures().items() for s in tl.scenes]
    out = _node("process.stdout.write(JSON.stringify(input.map((x) => [x.name, cw.drawsScene(x.scene, x.fmt)])));",
                scenes, tmp_path)
    assert [n for n, ok in out if not ok] == []
    assert len(out) == 7


# --- the data rule: the page and Python agree -------------------------------------------------------------------

_SEPARATORS = ["\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
_LINE_CASES = ["", "a", "a\n", "a\n\n", "\n", "\n\n", "x\r\n\r\ny", "a\n\r\n", "a\r\r\nb", "\tx", "ab\tc", "abcd\te",
               "a\t\tb", "தமிழ்\tx", "\U0001f600\tx", "  \t  x\t", "x\u000by\u000c"]


def test_code_lines_are_python_splitlines_and_expandtabs(tmp_path: Path) -> None:
    cases = [*_LINE_CASES, *[f"a{s}b" for s in _SEPARATORS], *[f"a{s}" for s in _SEPARATORS],
             *[f"{s}{s}b" for s in _SEPARATORS]]
    got = _node("process.stdout.write(JSON.stringify(input.map((c) => cw.codeLines(c))));", cases, tmp_path)
    want = [[line.expandtabs(cwf_tab()) for line in c.splitlines()] for c in cases]
    assert got == want


def cwf_tab() -> int:
    """code_walkthrough.js TAB (4); templates.py ``CODE_TAB`` once registered."""
    return int(getattr(tp, "CODE_TAB", 4))


def _programs() -> list[tuple[str, str]]:
    """Programs on every side of every limit: line counts around each format's capacity (Latin and Indic), lengths
    around each width (alone, beside a terminal), tabs, empty lines, an empty program."""
    out: list[tuple[str, str]] = [("empty", ""), ("blank", "\n"), ("one", "x = 1")]
    indic = "# கூட்டு"
    for n in range(4, 20):
        out.append((f"latin{n}", "\n".join(["x = 1"] * n)))
        out.append((f"indic{n}", "\n".join([indic] + ["x = 1"] * (n - 1))))
    for w in (27, 28, 29, 37, 38, 39, 59, 60, 61):
        out.append((f"wide{w}", "y = 0\n" + "x" * w))
        out.append((f"tabbed{w}", "x" * (w - 2) + "\t"))
    out.append(("crlf", "a\r\nb\r\n"))
    return out


def test_the_data_rule_mirrors_templates_py(tmp_path: Path) -> None:
    progs = _programs()
    payload = {"programs": [code for _, code in progs]}
    script = """
const out = { box: {}, fits: [] };
for (const fmt of ['landscape', 'portrait']) out.box[fmt] = cw.codeBox(fmt, core.ZONES[fmt]);
for (const code of input.programs) {
  const row = {};
  for (const fmt of ['landscape', 'portrait']) for (const headed of [false, true]) for (const panel of [false, true]) {
    row[`${fmt}|${headed}|${panel}`] = cw.codeFits(code, fmt, core.ZONES[fmt], { headed, panel });
  }
  out.fits.push(row);
}
process.stdout.write(JSON.stringify(out));
"""
    page = _node(script, payload, tmp_path)
    # the page's own numbers (deviation 13: 16:9 <= 17 lines of <= 60 chars, 9:16 <= 10 of <= 28)
    assert page["box"]["landscape"] == {"linesPx": 740, "headedPx": 596, "pitch": 43, "pitchIndic": 52, "chars": 60,
                                        "charsPanel": 38}
    assert page["box"]["portrait"] == {"linesPx": 484, "headedPx": 388, "pitch": 48, "pitchIndic": 58, "chars": 28,
                                       "charsPanel": None}
    spec = tp.TEMPLATES["code_walkthrough"]
    if not spec.implemented:
        # the handshake: the integrator's templates.py change brings CODE_BOX / code_lines / code_fits with
        # `implemented=True`, and this test turns strict the moment it does
        return
    for fmt in ("landscape", "portrait"):
        box = tp.CODE_BOX[fmt]  # type: ignore[attr-defined]
        assert (box.lines_px, box.headed_px, box.pitch, box.pitch_indic, box.chars, box.chars_panel) == tuple(
            page["box"][fmt][k] for k in ("linesPx", "headedPx", "pitch", "pitchIndic", "chars", "charsPanel")), fmt
    mismatches = []
    for (name, code), row in zip(progs, page["fits"], strict=True):
        for key, ok in row.items():
            fmt, headed, panel = key.split("|")
            py = tp.code_fits(code, fmt, headed=headed == "true", panel=panel == "true")  # type: ignore[attr-defined]
            if py != ok:
                mismatches.append(f"{name} {key}: python {py}, page {ok}")
    assert mismatches == []


def _board(scene: Any, fmt: str) -> BoardScene:
    """The BoardScene the director scores, rebuilt from a fixture's timed scene (its items and its panel)."""
    items = [BoardItem.model_validate(i.model_dump(mode="json")) for i in scene.board]
    panel = SidePanel.model_validate(scene.side_panel.panel.model_dump(mode="json")) if scene.side_panel else None
    return BoardScene(id=scene.scene_id, scene_id=scene.scene_id, type="content", title=scene.title, board=items,
                      side_panel=panel, beats=[Beat(id="b0", narration="x", board_item_id=items[-1].id)])


def test_fit_accepts_every_fixture_scene_and_refuses_what_the_page_refuses() -> None:
    spec = tp.TEMPLATES["code_walkthrough"]
    if not spec.implemented:
        return  # the handshake (see test_the_data_rule_mirrors_templates_py)
    for name, tl in cwf.fixtures().items():
        assert tl.presentation is not None
        fmt = tl.presentation.format
        for s in tl.scenes:
            assert spec.fit(_board(s, fmt), fmt) == tp.FIT_PREFERRED, f"{name} {s.scene_id}"
    code = {"id": "c1", "kind": "code", "language": "python", "code": "x = 1"}
    term = {"kind": "terminal", "title": "Run", "terminal": {"command": "python a.py", "output": "1"}}
    long = {**code, "code": "\n".join(["x"] * 18)}
    for board, panel, fmt in (([long], None, "landscape"), ([code], term, "portrait"),
                              ([{**code, "code": "x" * 39}], term, "landscape"),
                              ([code], {"kind": "image", "title": "P", "image_prompt": "a"}, "landscape")):
        scene = BoardScene.model_validate({"id": "s", "type": "content", "title": "T", "board": board,
                                           "side_panel": panel, "beats": [{"id": "b", "narration": "x"}]})
        assert spec.fit(scene, fmt) == tp.FIT_NONE, (board, panel, fmt)  # type: ignore[arg-type]
