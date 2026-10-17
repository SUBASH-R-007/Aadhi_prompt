"""The outro_next fixtures are what ``tests/compose/dynamic/make_outro_next_fixtures.py`` builds (byte for byte), cover
a dark and a light family in each format and every layout the template draws, keep §4.3 / R-11's outro lengths
(16:9 6-10 s or exactly the end-screen 15 s, 9:16 3-5 s) and show every movement of the template inside the slow
1x / 3x / reverse gate's 300 frames (``tests/determinism/test_template_fixtures.py``). Their validity as template
fixtures is the shared validator's (``test_template_fixtures.py``).

It also holds outro_next's half of the CG-2 rule (B2c review): the template's Python ``fit`` must refuse every scene
its page's ``drawsScene`` refuses. The registry-wide gate (``test_templates.py``) scores every template with
``last=False``, where the positional "last" row that names outro_next never matches, so it cannot see this template;
the parity test below scores the outro's own cases with ``last=True`` against the real JS module in node, and binds as
soon as the template is registered (``implemented``: before that the director cannot pick it, so the rule cannot
bite).
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
from aadhi.schemas.screenplay import BoardScene, SceneBase, SimulationScene
from aadhi.schemas.timeline import MediaRef, ResolvedSidePanel, TimedScene
from tests.compose.dynamic import make_outro_next_fixtures as onf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES
FPS = 30
# outro_next.js: the statement's entrance (TEXT_ENTER) and, in 16:9, the next card after it (NEXT_AFTER + CARD_ENTER),
# at the slowest personality's scale (Soft, x1.15): everything has landed this long after the statement's reveal ...
SETTLED_AFTER_REVEAL = (0.4 + 0.5) * 1.15 + 0.05
# ... and the 9:16 call to action is gone by R-11's CTA_MAX
CTA_MAX = 3.0


def test_the_committed_fixtures_are_current() -> None:
    assert onf.stale() == [], "run: python -m tests.compose.dynamic.make_outro_next_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in onf.fixtures().items():
        (tmp_path / name).write_text(onf.render(tl), "utf-8", newline="\n")
    assert onf.stale(tmp_path) == []
    first = sorted(onf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(onf.fixtures())[1]).unlink()
    assert sorted(onf.stale(tmp_path)) == sorted(onf.fixtures())[:2]


def _motion_end(scene: TimedScene, fmt: str) -> float:
    """Scene-relative seconds by which outro_next has stopped moving in ``scene``."""
    reveals = [e.t for e in scene.events if e.kind == "reveal"]
    end = (max(reveals) if reveals else 0.0) + SETTLED_AFTER_REVEAL
    refs = scene.look.refs if scene.look is not None else None
    if fmt == "portrait" and refs is not None and refs.next is not None:
        end = max(end, CTA_MAX)
    return min(end, scene.duration)


@pytest.mark.parametrize("name", sorted(onf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = onf.fixtures()[name]
    assert tl.presentation is not None
    fmt = tl.presentation.format
    assert fixture_name(FIXTURE_DIR / name)[:2] == ("outro_next", fmt)
    bounds = scene_frame_bounds(tl)
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    for scene in tl.scenes:
        assert scene.look is not None and scene.look.template == "outro_next"
        assert (scene.start + _motion_end(scene, fmt)) * FPS <= GATE_FRAMES, (scene.scene_id, "moves past the gate")


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in onf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_keeps_the_outro_s_length() -> None:
    """§4.3 / R-11: 6-10 s, or exactly ``Duration.end_screen`` (15 s) with end-screen space and never a length between
    (16:9); 3-5 s (9:16). The end_screen variant is 16:9 only and always that exact length."""
    spec = tp.TEMPLATES["outro_next"]
    for name, tl in onf.fixtures().items():
        assert tl.presentation is not None
        fmt = tl.presentation.format
        duration = spec.duration[fmt]
        for scene in tl.scenes:
            assert scene.look is not None
            if scene.look.variant == "end_screen":
                assert fmt == "landscape" and scene.duration == duration.end_screen, (name, scene.scene_id)
            else:
                assert duration.min <= scene.duration <= (duration.max or 1e9), (name, scene.scene_id)


def test_the_cases_the_template_draws_are_all_there() -> None:
    """Both 16:9 variants; a next step and none; a kicker (the scene title above the item) and a statement that is the
    title; R-11's data (at most one takeaway / paragraph / heading, no panel); the events are the item's reveal only
    (``consumes``)."""
    seen: set[str] = set()
    for tl in onf.fixtures().values():
        assert tl.presentation is not None
        for scene in tl.scenes:
            assert scene.look is not None
            seen.add(f"variant:{scene.look.variant}")
            seen.add("next" if scene.look.refs is not None and scene.look.refs.next is not None else "no-next")
            assert len(scene.board) <= 1 and all(str(getattr(i.kind, "value", i.kind)) in ("takeaway", "paragraph", "heading")
                                              for i in scene.board)
            assert scene.side_panel is None and scene.media is None
            seen.add("item" if scene.board else "title-statement")
            if scene.board and scene.title.strip():
                seen.add("kicker")
            assert {e.kind for e in scene.events} <= {"reveal"}
            assert all(tp.TEMPLATES["outro_next"].consumes.accepts(e.kind, e.target) for e in scene.events)
    assert seen >= {"variant:next_up", "variant:end_screen", "variant:None", "next", "no-next", "item", "title-statement",
                    "kicker"}, seen


# --- fit <-> drawsScene (B2c review CG-2) ----------------------------------------------------------------------------

_BEAT = [{"id": "b1", "narration": "That is all for today."}]
_PANEL = {"image": {"kind": "image", "image_prompt": "a lamp"}, "gif": {"kind": "gif", "gif_query": "x"},
          "terminal": {"kind": "terminal", "terminal": {"command": "ls"}},
          "chart": {"kind": "chart", "title": "I = V / R", "chart": {"labels": ["a"], "datasets": [{"data": [1]}]}}}


def _board(*items: dict[str, Any], title: str = "What we learned", type: str = "summary",
           panel: str | None = None) -> BoardScene:
    data: dict[str, Any] = {"id": "s1", "type": type, "title": title, "beats": _BEAT, "board": list(items)}
    if panel:
        data["side_panel"] = _PANEL[panel]
    return BoardScene.model_validate(data)


def _item(kind: str, text: str = "Three ideas, one law.", n: int = 1, **kw: Any) -> dict[str, Any]:
    return {"id": f"i{n}", "kind": kind, "text": text, **kw}


def _cases() -> list[tuple[str, SceneBase]]:
    out: list[tuple[str, SceneBase]] = []
    for kind in ("takeaway", "paragraph", "heading"):
        out.append((kind, _board(_item(kind))))
        out.append((f"{kind} untitled", _board(_item(kind), title="")))
        for panel in _PANEL:
            out.append((f"{kind}+panel:{panel}", _board(_item(kind), panel=panel)))
    out += [
        ("title only", _board()),
        ("nothing", _board(title="")),  # (a takeaway / paragraph / heading always has text: the schema requires it)
        ("two items", _board(_item("takeaway"), _item("paragraph", n=2))),
        ("a bullet", _board(_item("bullet"))),
        ("a definition", _board(_item("definition", term="Ohm"))),
        ("a table", _board(_item("table", text="", headers=["A", "B"], rows=[["1", "2"]]))),
        ("a recap takeaway", _board(_item("takeaway"), type="recap")),
        ("a simulation", SimulationScene.model_validate({"id": "s1", "type": "simulation", "title": "T",
                                                         "manim": {"template": "x"}, "beats": _BEAT})),
    ]
    out += [(f"type {t}", _board(_item("takeaway"), type=t)) for t in ("content", "example", "key_takeaway", "title")]
    return out


def _timed(scene: SceneBase) -> TimedScene:
    """The scene as the stage receives it (``compose/timeline.py`` ``_build_scene``: a side panel is resolved, with a
    medium for the kinds that have one, and shown)."""
    panel = None
    if scene.side_panel is not None:
        media = None
        if scene.side_panel.kind in ("figure", "image", "manim", "gif"):
            kind = "video" if scene.side_panel.kind in ("manim", "gif") else "image"
            media = MediaRef(kind=kind, asset_key="a" * 64, fit="contain", width=1280, height=720)
        panel = ResolvedSidePanel(panel=scene.side_panel, media=media, show_at=0.0)
    media_ref = MediaRef(kind="video", asset_key="b" * 64, fit="contain") if isinstance(scene, SimulationScene) else None
    return TimedScene(scene_id=scene.id, index=0, type=scene.type, title=scene.title, start=0.0, duration=6.0,
                      board=list(getattr(scene, "board", []) or []), side_panel=panel, media=media_ref,
                      layout={"show_side_panel": scene.side_panel is not None})


def _draws_in_node(cases: list[tuple[str, SceneBase]], tmp_path: Path) -> dict[str, bool]:
    payload = tmp_path / "outro_parity.json"
    payload.write_text(json.dumps({
        "module": (ROOT_DIR / "web" / "js" / "dynamic" / "templates" / "outro_next.js").as_uri(),
        "scenes": [{"name": n, "scene": json.loads(_timed(s).model_dump_json())} for n, s in cases],
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
    return json.loads(proc.stdout)


def test_draws_scene_answers_the_cases(tmp_path: Path) -> None:
    """What the page draws, case by case (the JS side of the parity, pinned whether or not the template is
    registered): R-11's data and nothing else."""
    draws = _draws_in_node(_cases(), tmp_path)
    want_true = {"takeaway", "paragraph", "heading", "takeaway untitled", "paragraph untitled", "heading untitled",
                 "title only", "a recap takeaway", "type content", "type example",
                 "type key_takeaway", "type title"}
    for name, _scene in _cases():
        for fmt in ("landscape", "portrait"):
            assert draws[f"{name}|{fmt}"] == (name in want_true), (name, fmt)


def test_fit_refuses_what_the_page_refuses(tmp_path: Path) -> None:
    """``fit(last=True) > 0`` implies ``drawsScene`` (CG-2), and the outro's own shapes score 1.0 as the last scene."""
    spec = tp.TEMPLATES["outro_next"]
    if not spec.implemented:
        pytest.skip("outro_next is not registered yet: the director cannot pick it, so CG-2 cannot bite")
    cases = _cases()
    draws = _draws_in_node(cases, tmp_path)
    mismatches = [f"{name} {fmt}: fit={spec.fit(scene, fmt, last=True)} but drawsScene is false"
                  for name, scene in cases for fmt in ("landscape", "portrait")
                  if spec.fit(scene, fmt, last=True) > 0 and not draws[f"{name}|{fmt}"]]
    assert not mismatches, "\n".join(mismatches)
    for name, scene in cases:
        if name in ("takeaway", "paragraph untitled", "title only"):
            assert spec.fit(scene, "landscape", last=True) == tp.FIT_PREFERRED, name
            assert spec.fit(scene, "portrait", last=True) == tp.FIT_PREFERRED, name
        assert spec.fit(scene, "landscape") == 0, f"{name}: outro_next is the LAST scene's template only"
