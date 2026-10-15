"""The B2 template fixtures are valid (B2c; ``tests/compose/dynamic/template_fixtures.py`` says what each must be): a
Dynamic ``Timeline`` of the format in its name that draws its template, events inside their scenes on targets the
template consumes, dense templates frozen throughout (R-55), one fixture per format. The slow 1x / 3x / reverse gate
of the same files is ``tests/determinism/test_template_fixtures.py``."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.config import ROOT_DIR
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FORMATS, fixture_name, fixture_paths, load_fixture

PATHS = fixture_paths()


def fixture_problems(path: Path) -> list[str]:
    """What is wrong with one fixture (empty: valid)."""
    problems: list[str] = []
    try:
        template, fmt, _case = fixture_name(path)
        tl = Timeline.model_validate(load_fixture(path))
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        return [f"{path.name}: {e}"]
    p = tl.presentation
    if p is None or p.style != "dynamic" or p.format != fmt:
        problems.append(f"{path.name}: not a Dynamic {fmt} timeline")
    if (tl.width, tl.height) != ((1920, 1080) if fmt == "landscape" else (1080, 1920)):
        problems.append(f"{path.name}: a {fmt} timeline is {tl.width}x{tl.height}")
    spec = tp.TEMPLATES.get(template)
    if spec is None:
        return [*problems, f"{path.name}: no template {template!r} in the registry"]
    if not (ROOT_DIR / "web" / "js" / "dynamic" / "templates" / f"{template}.js").is_file():
        problems.append(f"{path.name}: web/js/dynamic/templates/{template}.js does not exist")
    drawn = [s for s in tl.scenes if s.look is not None and s.look.template == template]
    if not drawn:
        problems.append(f"{path.name}: no scene names {template!r} in look.template")
    for s in tl.scenes:
        try:
            s.check_inside()
        except ValueError as e:
            problems.append(f"{path.name}: {e}")
    for s in drawn:
        for e in s.events:
            if e.target is not None:
                try:
                    tp.parse_target(e.target)
                except ValueError as err:
                    problems.append(f"{path.name}: scene {s.scene_id}: {err}")
                    continue
            if e.kind in ("cue_term", "look_here") and not spec.consumes.accepts(e.kind, e.target):
                problems.append(f"{path.name}: scene {s.scene_id}: {template} does not draw {e.kind} on {e.target!r}")
        frozen = s.look.background.get("frozen_at") if s.look is not None else None
        if spec.dense and frozen != 0.0:
            problems.append(f"{path.name}: scene {s.scene_id}: a dense template's background is frozen from its start "
                            "(R-55): look.background.frozen_at = 0")
    return problems


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
def test_each_template_fixture_is_valid(path: Path) -> None:
    assert fixture_problems(path) == []


def test_every_template_with_a_fixture_has_one_per_format() -> None:
    formats: dict[str, set[str]] = defaultdict(set)
    for path in PATHS:
        template, fmt, _ = fixture_name(path)
        formats[template].add(fmt)
    for template, have in formats.items():
        want = set(tp.TEMPLATES[template].formats) if template in tp.TEMPLATES else set(FORMATS)
        assert have == want, f"{template}: fixtures for {sorted(have)}, the template renders {sorted(want)}"


def test_the_checks_catch_a_broken_fixture(tmp_path: Path) -> None:
    """Each check fails on a fixture that breaks it (a board_generic timeline stands in for a template's)."""
    from tests.compose.dynamic.helpers import dynamic_timeline

    good = dynamic_timeline("landscape").model_dump(mode="json")

    def problems(name: str, edit: Callable[[dict[str, Any]], None] | None = None) -> list[str]:
        data = copy.deepcopy(good)
        if edit is not None:
            edit(data)
        path = tmp_path / name
        path.write_text(json.dumps(data), "utf-8")
        return fixture_problems(path)

    def set_events(*events: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
        def edit(d: dict[str, Any]) -> None:
            d["scenes"][0]["events"] = list(events)
        return edit

    def named(template: str) -> Callable[[dict[str, Any]], None]:
        def edit(d: dict[str, Any]) -> None:
            d["scenes"][0]["look"]["template"] = template
        return edit

    assert problems("board_generic.landscape.json") == []
    assert any("is named" in p for p in problems("board_generic.json"))
    assert any("not a Dynamic portrait" in p for p in problems("board_generic.portrait.json"))
    assert any("no template" in p for p in problems("nope.landscape.json"))
    assert any("no scene names" in p for p in problems("list_build.landscape.json"))
    assert any("not a board item id" in p
               for p in problems("board_generic.landscape.a.json", set_events({"t": 1.0, "kind": "reveal", "target": "B 1"})))
    assert any("does not draw look_here" in p
               for p in problems("board_generic.landscape.b.json", set_events({"t": 1.0, "kind": "look_here", "target": "b2"})))
    assert any("outside the scene" in p
               for p in problems("board_generic.landscape.c.json", set_events({"t": 99.0, "kind": "reveal", "target": "b2"})))
    dense = problems("formula_focus.landscape.json", named("formula_focus"))
    assert any("frozen from its start" in p for p in dense), dense


def test_every_fixture_file_is_its_generator_s_current_output() -> None:
    """Every file in the fixture directory is written by one pilot's generator, byte for byte as it builds it today
    (``python -m tests.compose.dynamic.<generator>``; each also has ``--check``): a fixture edited by hand, a stale one
    after a pipeline change, or a stray file fails here."""
    from tests.compose.dynamic import list_build_fixtures as lbf
    from tests.compose.dynamic import make_formula_focus_fixtures as fff
    from tests.compose.dynamic import make_media_split_fixtures as msf

    generated: dict[str, str] = {p.name: text for p, text in fff.fixtures().items()}
    generated |= {p.name: text for p, text in msf.files().items()}
    generated |= {name: lbf.render(tl) for name, tl in lbf.fixtures().items()}
    assert sorted(generated) == sorted(p.name for p in PATHS)
    stale = [p.name for p in PATHS if p.read_text("utf-8") != generated[p.name]]
    assert stale == [], f"stale fixtures: {stale}"
