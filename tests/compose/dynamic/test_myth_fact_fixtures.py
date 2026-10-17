"""The myth_fact fixtures are what ``tests/compose/dynamic/make_myth_fact_fixtures.py`` builds (byte for byte), cover a
dark and a light family in each format, carry the events the template draws, fit the slow 1x / 3x / reverse gate's
300 frames closely enough that the gate sees every scene (``tests/determinism/test_template_fixtures.py``), and every
scene in them is one ``_fit_myth_fact`` takes - including the showcase lecture's s5 (owner decision 3). Also pinned
here: the Python side of the CG-2 rule against ``myth_fact.js`` (its ``SUPPORT_MAX`` is ``MYTH_FACT_SUPPORT_MAX``,
and the misconception scenes the fit takes are scenes the page draws), and ``key_terms`` staying pure. Their validity
as template fixtures is the shared validator's (``test_template_fixtures.py``)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.config import ROOT_DIR
from aadhi.schemas.screenplay import BoardScene
from aadhi.schemas.timeline import TimedScene
from tests.compose.dynamic import make_myth_fact_fixtures as mff
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES
JS = ROOT_DIR / "web" / "js" / "dynamic" / "templates" / "myth_fact.js"
BEAT = [{"id": "b1", "narration": "Placeholder narration."}]


def board_scene(timed: TimedScene) -> BoardScene:
    """The screenplay scene a fixture's timed scene stands for (its board and its side panel): what ``fit`` reads."""
    data: dict[str, Any] = {"id": timed.scene_id, "type": "content", "title": timed.title or "T", "beats": BEAT,
                            "board": [i.model_dump(mode="json") for i in timed.board]}
    if timed.side_panel is not None:
        data["side_panel"] = timed.side_panel.panel.model_dump(mode="json")
    return BoardScene.model_validate(data)


def test_the_committed_fixtures_are_current() -> None:
    assert mff.stale() == [], "run: python -m tests.compose.dynamic.make_myth_fact_fixtures"


def test_files_and_fixtures_agree() -> None:
    assert {p.name: text for p, text in mff.files().items()} == {
        name: mff.render(tl) for name, tl in mff.fixtures().items()}
    assert all(p.parent == FIXTURE_DIR for p in mff.files())


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in mff.fixtures().items():
        (tmp_path / name).write_text(mff.render(tl), "utf-8", newline="\n")
    assert mff.stale(tmp_path) == []
    first, second = sorted(mff.fixtures())[:2]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / second).unlink()
    assert mff.stale(tmp_path) == [first, second]


@pytest.mark.parametrize("name", sorted(mff.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = mff.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "myth_fact"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "myth_fact" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in mff.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_fixture_scene_is_one_the_registry_sends_to_myth_fact() -> None:
    """Every scene is myth_fact's preferred fit in its format (the shape, the panel and the measured caps), and the
    §4.4 ``misconception`` row is the one that names it."""
    spec = tp.TEMPLATES["myth_fact"]
    for name, tl in mff.fixtures().items():
        assert tl.presentation is not None
        fmt = tl.presentation.format
        for timed in tl.scenes:
            scene = board_scene(timed)
            assert spec.fit(scene, fmt) == tp.FIT_PREFERRED, f"{name}: {timed.scene_id}"
            assert "misconception" in [r.id for r in tp.signals(scene)], f"{name}: {timed.scene_id}"


def test_the_showcase_scene_s5_is_myth_facts_in_16_9_and_board_generics_in_9_16() -> None:
    """Owner decision 3: the showcase lecture's s5 (two definitions, then the misconception, beside an image panel)
    is drawn by myth_fact in 16:9. In 9:16 it carries a panel and definitions, which a reel's column cannot hold beside
    the two cards (myth_fact.js drawsScene), so it stays board_generic's."""
    from tests.invariance import classic_fixture as fx

    sp = fx.load_screenplay("showcase_ohms_law")
    s5 = next(s for s in sp.scenes if s.id == "s5")
    kinds = [str(getattr(i.kind, "value", i.kind)) for i in s5.board]
    assert kinds == ["definition", "definition", "misconception"]
    fits = {f: {t: s.fit(s5, f) for t, s in tp.TEMPLATES.items() if s.fit(s5, f) > 0} for f in ("landscape", "portrait")}
    assert fits["landscape"] == {"myth_fact": tp.FIT_PREFERRED, "board_generic": tp.FIT_FALLBACK}
    assert fits["portrait"] == {"board_generic": tp.FIT_FALLBACK}
    assert [r.id for r in tp.signals(s5)] == ["misconception"]


def test_key_terms_stays_pure() -> None:
    """Owner decision 3: a scene carrying a misconception is never key_terms' (its fit refuses it in both formats)."""
    items = [{"id": "d1", "kind": "definition", "term": "Voltage", "text": "The push"},
             {"id": "d2", "kind": "definition", "term": "Current", "text": "The flow"},
             {"id": "m1", "kind": "misconception", "text": "Current is used up", "justification": "It returns"}]
    for role in (None, "pretraining"):
        data: dict[str, Any] = {"id": "s", "type": "content", "title": "T", "beats": BEAT, "board": items}
        if role:
            data["intent"] = {"narrative_role": role}
        scene = BoardScene.model_validate(data)
        for fmt in ("landscape", "portrait"):
            assert tp.TEMPLATES["key_terms"].fit(scene, fmt) == 0, (role, fmt)  # type: ignore[arg-type]


def test_support_max_is_the_pages() -> None:
    """``MYTH_FACT_SUPPORT_MAX`` mirrors ``myth_fact.js SUPPORT_MAX`` (the CG-2 rule's count)."""
    m = re.search(r"export const SUPPORT_MAX = Object\.freeze\(\{ landscape: (\d+), portrait: (\d+) \}\);",
                  JS.read_text(encoding="utf-8"))
    assert m, "myth_fact.js SUPPORT_MAX moved"
    assert dict(tp.MYTH_FACT_SUPPORT_MAX) == {"landscape": int(m.group(1)), "portrait": int(m.group(2))}


def _misconception_scenes() -> list[tuple[str, BoardScene]]:
    """Misconception scenes around the page's limits: counts, headings, other kinds, every panel kind, missing
    corrections, and text at and past the caps."""
    def item(kind: str, n: int = 0, **kw: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"id": f"{kind[:4]}{n}", "kind": kind, "text": "A short line"}
        if kind == "misconception":
            base["justification"] = "The short correction"
        if kind == "definition":
            base["term"] = "Term"
        base.update(kw)
        return base

    from tests.compose.dynamic.test_templates import PANELS

    panels: dict[str, dict[str, Any]] = {
        **PANELS, "chart_titled": {"kind": "chart", "title": "A chart",
                                   "chart": {"labels": ["a"], "datasets": [{"data": [1]}]}},
    }
    shapes: list[tuple[str, list[dict[str, Any]]]] = [
        ("alone", [item("misconception")]),
        ("heading", [item("heading"), item("misconception")]),
        ("one-def", [item("definition"), item("misconception")]),
        ("two-defs", [item("definition", 0), item("definition", 1), item("misconception")]),
        ("three-defs", [item("definition", 0), item("definition", 1), item("definition", 2), item("misconception")]),
        ("heading-two-defs", [item("heading"), item("definition", 0), item("definition", 1), item("misconception")]),
        ("bullet", [item("bullet"), item("misconception")]),
        ("two-myths", [item("misconception", 0), item("misconception", 1)]),
        ("no-correction", [item("misconception", justification=None)]),
        ("long-myth", [item("misconception", text="word " * 40)]),
        ("long-fact", [item("misconception", justification="word " * 40)]),
        ("tamil", [item("misconception", text="மின்னோட்டம் சுற்றில் செலவாகிவிடும்",
                        justification="அதே மின்னோட்டம் திரும்புகிறது")]),
    ]
    out: list[tuple[str, BoardScene]] = []
    for name, items in shapes:
        base = {"id": "s", "type": "content", "title": "T", "beats": BEAT, "board": items}
        out.append((name, BoardScene.model_validate(base)))
        for pname, panel in panels.items():
            out.append((f"{name}+{pname}", BoardScene.model_validate({**base, "side_panel": panel})))
    return out


def _draws_in_node(scenes: list[tuple[str, BoardScene]], tmp_path: Path) -> dict[str, bool]:
    """``myth_fact.js drawsScene`` per scene and format, on the TimedScene the stage receives (test_templates.py's
    probe: a panel of a medium kind gets a resolved medium, as a real lecture's would)."""
    from tests.compose.dynamic.test_templates import timed

    payload = tmp_path / "myth_in.json"
    payload.write_text(json.dumps({
        "module": JS.as_uri(),
        "scenes": [{"name": n, "scene": json.loads(timed(s).model_dump_json())} for n, s in scenes],
    }), encoding="utf-8")
    script = """
const fs = await import('node:fs/promises');
const input = JSON.parse(await fs.readFile(process.argv[1], 'utf8'));
const mod = await import(input.module);
const out = {};
for (const s of input.scenes) for (const fmt of ['landscape', 'portrait']) out[`${s.name}|${fmt}`] = !!mod.drawsScene(s.scene, fmt);
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", script, str(payload)], cwd=ROOT_DIR,
                          capture_output=True, text=True, encoding="utf-8", timeout=180, check=False)
    if proc.returncode != 0:
        pytest.fail(f"node could not evaluate drawsScene: {proc.stderr[-2000:]}")
    out: dict[str, bool] = json.loads(proc.stdout)
    return out


def test_fit_refuses_every_scene_the_page_refuses(tmp_path: Path) -> None:
    """CG-2 for myth_fact: ``fit > 0`` implies ``drawsScene``, over the misconception shapes, every panel kind and
    both formats (test_templates.py's gate walks the other shapes). The reverse is ordinary - the fit is stricter
    where text would not fit - but the plain cases are pinned too, so the parity is not vacuous."""
    scenes = _misconception_scenes()
    draws = _draws_in_node(scenes, tmp_path)
    spec = tp.TEMPLATES["myth_fact"]
    taken = 0
    for name, scene in scenes:
        for fmt in ("landscape", "portrait"):
            fit = spec.fit(scene, fmt)  # type: ignore[arg-type]
            if fit > 0:
                taken += 1
                assert draws[f"{name}|{fmt}"], f"{name} {fmt}: fit={fit} but drawsScene is false"
    assert taken >= 20, taken
    expect = {
        ("alone", "landscape"): True, ("alone", "portrait"): True, ("heading", "landscape"): True,
        ("heading", "portrait"): False, ("one-def", "landscape"): True, ("one-def", "portrait"): False,
        ("two-defs", "landscape"): True, ("three-defs", "landscape"): False, ("heading-two-defs", "landscape"): False,
        ("bullet", "landscape"): False, ("two-myths", "landscape"): False, ("no-correction", "landscape"): False,
        ("long-myth", "landscape"): False, ("long-fact", "portrait"): False, ("tamil", "portrait"): True,
        ("two-defs+image", "landscape"): True, ("two-defs+terminal", "landscape"): True,
        # a chart or graph beside the cards is a real plot (owner decision 1: the shared panel helper draws it through
        # the chart engine), with or without the title the helper used to need
        ("alone+chart", "landscape"): True, ("alone+chart_titled", "landscape"): True,
        ("alone+graph", "landscape"): True, ("alone+graph_titled", "landscape"): True,
        ("alone+gif", "landscape"): True, ("alone+model_3d", "landscape"): False,
        ("alone+skill_tree", "landscape"): False, ("alone+image", "portrait"): False,
    }
    got = {key: spec.fit(dict(scenes)[key[0]], key[1]) > 0 for key in expect}  # type: ignore[arg-type]
    assert got == expect
