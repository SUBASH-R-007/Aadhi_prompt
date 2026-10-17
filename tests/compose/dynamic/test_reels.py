"""The reel (9:16) adaptations of owner decision 4 - the Python side (``aadhi/compose/dynamic/reels.py``): the
strongest distractor of a reel check, and the side panel's own beat before a worked example's steps or the key
terms. The page side is pinned by ``web/tests/dynamic/templates/reels.test.js``; the constants are pinned to the JS
here, by reading ``_shared.js`` / ``check.js`` (one source of truth per number, two languages)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aadhi.compose.dynamic import reels
from aadhi.schemas.timeline import TimedBeat, TimedScene, VisualEvent

ROOT = Path(__file__).resolve().parents[3]
SHARED = (ROOT / "web" / "js" / "dynamic" / "templates" / "_shared.js").read_text(encoding="utf-8")


def _js_number(name: str) -> float:
    m = re.search(rf"export const {name} = ([0-9.]+);", SHARED)
    assert m, f"{name} not found in _shared.js"
    return float(m.group(1))


def test_the_beat_constants_mirror_the_page() -> None:
    assert reels.REEL_BEAT_MIN == _js_number("REEL_BEAT_MIN")
    assert reels.REEL_BEAT_MAX == _js_number("REEL_BEAT_MAX")
    assert reels.REEL_BEAT_EXIT == _js_number("REEL_BEAT_EXIT")
    assert reels.REEL_STAGGER == _js_number("REEL_STAGGER")
    assert reels.REEL_BEAT_START == _js_number("PANEL_DELAY")  # the panel enters with the scene, as in 16:9
    from aadhi.compose.dynamic import events
    assert reels.REVEAL_LEAD == events.REVEAL_LEAD and reels.INITIAL_DELAY == events.INITIAL_DELAY
    assert reels.CUE_AFTER_ENTER == events.TERM_AFTER_ENTER
    assert reels.KIND_ORDER == events._KIND_ORDER


# --- (a) the strongest distractor ------------------------------------------------------------------------------------


def test_strongest_distractor_tiers() -> None:
    opts = ["stays", "doubles", "halves", "used up"]
    # 3. no signal: the first wrong option in stored order
    assert reels.strongest_distractor(opts, 1) == 0
    assert reels.strongest_distractor(opts, 0) == 1
    # 2. a written rationale wins over position
    assert reels.strongest_distractor(opts, 1, ["", "", "the inverse trap", "the classic error"]) == 2
    assert reels.strongest_distractor(opts, 1, ["  ", "", "", "x"]) == 3, "whitespace is no rationale"
    # 1. a tagged misconception wins over a rationale
    assert reels.strongest_distractor(opts, 1, ["a", "", "b", "c"], [None, None, None, "current-used-up"]) == 3
    # the correct option's own tag or rationale never counts
    assert reels.strongest_distractor(opts, 1, ["", "why", "", ""], [None, "m", None, None]) == 0
    assert reels.strongest_distractor(["only"], 0) is None


def test_reel_options_keep_the_stored_order() -> None:
    assert reels.reel_options(["a", "b"], 1) == [0, 1], "two options are drawn as they are"
    assert reels.reel_options(["a", "b", "c", "d"], 3) == [0, 3]
    assert reels.reel_options(["a", "b", "c", "d"], 0, ["", "", "x", ""]) == [0, 2]
    assert reels.reel_options(["a", "b", "c", "d", "e"], 2, None, [None, None, None, None, "m"]) == [2, 4]


# --- (b) the panel beat ---------------------------------------------------------------------------------------------

E = reels.Entrance


def test_window_ends_at_the_first_anchored_entrance_within_the_bounds() -> None:
    start = reels.REEL_BEAT_START
    # the narration reveals the first item at 3.1 s: the panel holds until then
    assert reels.reel_window([E("a", 3.1, 0, True), E("b", 5.0, 1, True)], 20.0) == (start, 3.1)
    # too early: never shorter than REEL_BEAT_MIN
    assert reels.reel_window([E("a", 0.38, 0, True)], 20.0) == (start, start + reels.REEL_BEAT_MIN)
    # an unanchored item waits: never longer than REEL_BEAT_MAX
    assert reels.reel_window([E("h", 0.35, 0, False), E("a", 9.0, 1, True)], 20.0) == (start, start + reels.REEL_BEAT_MAX)
    # nothing waits: the panel holds until the first anchored item, however late
    assert reels.reel_window([E("a", 9.0, 0, True)], 20.0) == (start, 9.0)
    # nothing anchored at all
    assert reels.reel_window([E("h", 0.35, 0, False)], 20.0) == (start, start + reels.REEL_BEAT_MAX)
    # a short scene: the content keeps at least REEL_BEAT_MIN of it
    assert reels.reel_window([E("a", 9.0, 0, True)], 5.0) == (start, 5.0 - reels.REEL_BEAT_MIN)
    assert reels.reel_window([E("a", 9.0, 0, True)], 1.0) == (start, start), "never before its own start"


def test_entrances_defer_what_the_beat_holds_back_in_order() -> None:
    xs = [E("h", 0.35, 0, False), E("s1", 0.38, 1, True), E("s2", 4.0, 2, True), E("s3", 2.2, 3, True)]
    got = reels.reel_entrances(xs, 2.2)
    assert got == {"h": 2.2, "s1": 2.2 + reels.REEL_STAGGER, "s3": 2.2, "s2": 4.0}
    # float noise at the boundary is the same instant, never a deferral
    assert reels.reel_entrances([E("a", 2.2 - 1e-9, 0, True)], 2.2) == {"a": 2.2 - 1e-9}


@pytest.mark.parametrize(("first", "lead"), [(0.0, 1.82), (1.0, 0.82), (1.82, 0.0), (5.0, 0.0), (0.3004, 1.52)])
def test_lead_brings_the_first_anchored_reveal_to_the_beat_minimum(first: float, lead: float) -> None:
    got = reels.reel_lead(first, scene_lead=0.5)
    assert got == pytest.approx(lead, abs=1e-9)
    anchored = round(0.5 + got + first, 3) - reels.REVEAL_LEAD   # the timeline rounds beat starts to ms
    assert anchored >= reels.REEL_BEAT_START + reels.REEL_BEAT_MIN - reels.EPS
    assert reels.reel_lead(None, scene_lead=0.5) == 0.0


def _scene(beats: list[tuple[str | None, float]], board: list[str], *, duration: float = 20.0,
           events: list[VisualEvent] | None = None) -> TimedScene:
    from aadhi.schemas.screenplay import BoardItem

    return TimedScene(
        scene_id="s", index=0, type="example", title="T", start=0.0, duration=duration,
        board=[BoardItem(id=i, kind="example_step" if i.startswith("s") else "heading", text=i) for i in board],
        beats=[TimedBeat(beat_id=f"b{k}", index=k, start=t, speech_end=t + 1, end=t + 1, narration="x",
                         board_item_id=item) for k, (item, t) in enumerate(beats)],
        events=events or [],
    )


def test_item_entrances_mirror_state_js() -> None:
    scene = _scene([("s1", 3.0), ("s2", 6.0)], ["h", "s1", "s2", "s3"],
                   events=[VisualEvent(t=5.5, kind="reveal", target="s2")])
    got = {x.id: (x.enter, x.anchored) for x in reels.item_entrances(scene)}
    assert got["s1"] == (pytest.approx(2.88), True)
    assert got["s2"] == (5.5, True), "a reveal event wins over the beat"
    assert got["h"] == (reels.INITIAL_DELAY, False) and got["s3"] == (reels.INITIAL_DELAY + reels.INITIAL_STAGGER, False)


def test_reel_events_write_the_beat_and_the_deferred_reveals() -> None:
    accept_all = lambda kind, target: True  # noqa: E731
    scene = _scene([(None, 0.5), ("s1", 3.0), ("s2", 6.0)], ["h", "s1", "s2"])
    events = [VisualEvent(t=2.88, kind="reveal", target="s1", anchored=True),
              VisualEvent(t=5.88, kind="reveal", target="s2", anchored=True),
              VisualEvent(t=1.0, kind="cue_term", target="h", anchored=True),       # before h is on screen: dropped
              VisualEvent(t=3.5, kind="cue_term", target="h", anchored=True),       # after: kept
              VisualEvent(t=1.0, kind="look_here", target="panel", anchored=True),  # inside the beat: kept
              VisualEvent(t=7.0, kind="look_here", target="panel", anchored=True)]  # the panel has gone: dropped
    out = reels.reel_events(scene, events, accepts=accept_all)
    pairs = [(e.kind, e.target, e.t) for e in out]
    assert ("reveal", "panel", 0.2) in pairs
    panel = next(e for e in out if e.target == "panel" and e.kind == "reveal")
    assert panel.dur == pytest.approx(2.68)   # the beat ends at s1's anchored entrance (2.88)
    assert ("reveal", "h", 2.88) in pairs, "the unanchored heading enters as the beat ends"
    assert ("reveal", "s1", 2.88) in pairs and ("reveal", "s2", 5.88) in pairs, "anchored reveals keep their word"
    assert ("cue_term", "h", 1.0) not in pairs and ("cue_term", "h", 3.5) in pairs
    assert ("look_here", "panel", 1.0) in pairs and ("look_here", "panel", 7.0) not in pairs
    assert [e.t for e in out] == sorted(e.t for e in out)
    # the gate: only the kinds and forms the template consumes are written
    only_items = lambda kind, target: target != "panel"  # noqa: E731
    assert all(e.target != "panel" or e.kind != "reveal" for e in reels.reel_events(scene, events, accepts=only_items))


def test_reel_lead_for_reads_the_first_revealing_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace as NS

    scene = NS(board=[NS(id="h"), NS(id="s1")], side_panel=object())
    beats = [NS(beat=NS(board_item_id=None), start=0.0), NS(beat=NS(board_item_id="s1"), start=0.4),
             NS(beat=NS(board_item_id="elsewhere"), start=0.1)]
    monkeypatch.setattr(reels, "reel_panel_scene", lambda s, fmt: True)
    assert reels.reel_lead_for(scene, beats, fmt="portrait", scene_lead=0.5) == pytest.approx(1.42)  # type: ignore[arg-type]
    assert reels.reel_lead_for(scene, beats[:1], fmt="portrait", scene_lead=0.5) == 0.0, "no revealing beat"  # type: ignore[arg-type]
    monkeypatch.setattr(reels, "reel_panel_scene", lambda s, fmt: False)
    assert reels.reel_lead_for(scene, beats, fmt="portrait", scene_lead=0.5) == 0.0  # type: ignore[arg-type]


def test_reel_panel_scene_is_9_16_with_a_panel_only() -> None:
    from types import SimpleNamespace as NS

    assert reels.reel_panel_scene(NS(side_panel=object()), "landscape") is False  # type: ignore[arg-type]
    assert reels.reel_panel_scene(NS(side_panel=None), "portrait") is False  # type: ignore[arg-type]
