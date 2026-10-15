"""Screenplay and timed scenes for the Director's tests (``test_direct.py``, ``test_events.py``).

Small builders only, written for these two tests: a board scene with its beats, the matching ``TimedScene`` with
word timings and sync cues, and a ``Screenplay`` with a ``Presentation`` around them. Nothing here is shared with
another template's fixtures (B2c's rule: never edit another generator), and nothing here is rendered.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from aadhi.schemas.screenplay import (
    Beat,
    BoardItem,
    BoardScene,
    ChapterCardScene,
    FormulaVariable,
    Presentation,
    SceneIntent,
    SceneLook,
    Screenplay,
    SidePanel,
)
from aadhi.schemas.timeline import Chapter, SyncCue, TimedBeat, TimedScene, TimedWord

PRESENTATION: dict[str, Any] = {
    "style": "dynamic",
    "format": "landscape",
    "look_seed": 1234567,
    "look_epoch": 0,
    "layout_epoch": 0,
    "theme_family": "midnight_indigo",
    "personality": "crisp",
    "display_font": "outfit",
    "caption_style": "band",
    "cue_term_style": "marker",
    "hook_kind": "question",
    "asset_style": {"family": "midnight_indigo", "personality": "crisp"},
    "voices": {"narrator": "en-in-neerja"},
    "music": {"source": "procedural", "provider": "procedural"},
}


# The smallest payload each side-panel kind these tests use needs (``SidePanel._payload``).
PANEL_PAYLOAD: dict[str, dict[str, Any]] = {
    "figure": {"figure_id": "fig"},
    "image": {"image_prompt": "a picture of a wave"},
    "chart": {"chart": {"chart_type": "bar", "labels": ["a", "b"],
                        "datasets": [{"label": "one", "data": [1.0, 2.0]}]}},
    "graph": {"graph": {"functions": [{"expr": "x^2"}]}},
    "terminal": {"terminal": {"command": "python wave.py", "output": "340\n680"}},
    "skill_tree": {},
}


def presentation(**over: Any) -> Presentation:
    return Presentation.model_validate({**PRESENTATION, **over})


def item(item_id: str, kind: str, **over: Any) -> BoardItem:
    """One board item; ``kind``'s required fields are filled with something plausible."""
    data: dict[str, Any] = {"id": item_id, "kind": kind}
    if kind in ("heading", "bullet", "paragraph", "takeaway", "question", "step", "misconception", "example_step",
                "callout_info", "callout_tip", "callout_warning", "quote"):
        data["text"] = over.pop("text", f"{item_id} text")
    if kind == "definition":
        data["term"] = over.pop("term", f"{item_id} term")
        data["text"] = over.pop("text", f"{item_id} means something")
    if kind == "quote":
        data["caption"] = over.pop("caption", "Someone")
    if kind == "formula":
        data["latex"] = over.pop("latex", "v = f \\lambda")
    if kind == "code":
        data["code"] = over.pop("code", "print(1)")
        data["language"] = over.pop("language", "python")
    if kind == "stat":
        data["value"] = over.pop("value", "42")
        data["text"] = over.pop("text", "of the mass")
    if kind == "figure":
        data["figure_id"] = over.pop("figure_id", "fig")
    variables = over.pop("variables", None)
    if variables is not None:
        data["variables"] = [FormulaVariable(symbol_latex=s, meaning=m) for s, m in variables]
    return BoardItem.model_validate({**data, **over})


def board_scene(scene_id: str, items: Sequence[BoardItem], *, beats: Sequence[tuple[str, str, str | None]] = (),
                scene_type: str = "content", look: SceneLook | None = None, panel: str | None = None,
                intent: str | None = None, role: str | None = None, sequence: str | None = None) -> BoardScene:
    """A board scene. ``beats``: ``(beat id, narration, revealed item id or None)`` in order; without any, one beat
    that reveals nothing (so every item is on the board from the start)."""
    rows = list(beats) or [("b0", "Narration of the scene.", None)]
    data: dict[str, Any] = {
        "id": scene_id,
        "type": scene_type,
        "board": [i.model_dump() for i in items],
        "beats": [Beat(id=bid, narration=text, board_item_id=reveal).model_dump() for bid, text, reveal in rows],
    }
    if panel is not None:
        data["side_panel"] = SidePanel.model_validate({"kind": panel, **PANEL_PAYLOAD[panel]}).model_dump()
    if look is not None:
        data["look"] = look.model_dump()
    if intent is not None or role is not None:
        data["intent"] = SceneIntent(visual_intent=intent, narrative_role=role).model_dump()  # type: ignore[arg-type]
    if sequence is not None:
        data["sequence"] = sequence
    return BoardScene.model_validate(data)


def chapter_card(scene_id: str, title: str = "Chapter one") -> ChapterCardScene:
    return ChapterCardScene.model_validate(
        {"id": scene_id, "type": "chapter_card", "title": title, "chapter_number": 1,
         "beats": [Beat(id="c0", narration="Chapter one.").model_dump()]})


def words(text: str, start: float, *, per: float = 0.4) -> list[TimedWord]:
    """One ``TimedWord`` per whitespace-separated word of ``text``, ``per`` seconds each from ``start``."""
    out: list[TimedWord] = []
    at = start
    for w in text.split():
        out.append(TimedWord(text=w, start=round(at, 3), end=round(at + per, 3)))
        at += per
    return out


def timed(scene: BoardScene | ChapterCardScene, *, index: int = 0, start: float = 0.0, duration: float = 20.0,
          beat_times: Sequence[tuple[float, float]] = (), sync: Iterable[SyncCue] = (),
          panel: Any = None) -> TimedScene:
    """The ``TimedScene`` of ``scene``: one ``TimedBeat`` per beat with word timings from its narration.

    ``beat_times``: ``(start, speech_end)`` per beat; without them the beats run back to back from 0, four seconds
    each. ``sync``: the scene's word-anchored cues (``compose.sync``), which ``events.py`` reads."""
    beats: list[TimedBeat] = []
    for i, beat in enumerate(scene.beats):
        if i < len(beat_times):
            at, speech_end = beat_times[i]
        else:
            at, speech_end = 4.0 * i, 4.0 * i + 3.5
        beats.append(TimedBeat(beat_id=beat.id, index=i, start=at, speech_end=speech_end, end=speech_end,
                               narration=beat.narration, board_item_id=beat.board_item_id,
                               fill_item_id=beat.fill_item_id, words=words(beat.narration, at)))
    data: dict[str, Any] = {
        "scene_id": scene.id, "index": index, "type": scene.type, "title": scene.title, "start": start,
        "duration": duration, "board": [i.model_dump() for i in getattr(scene, "board", [])],
        "beats": [b.model_dump() for b in beats], "sync_cues": [c.model_dump() for c in sync],
    }
    if panel is not None:
        data["side_panel"] = panel
    return TimedScene.model_validate(data)


def screenplay(scenes: Sequence[BoardScene | ChapterCardScene], **over: Any) -> Screenplay:
    return Screenplay.model_validate({
        "language": "en-IN",
        "board_language": "en-IN",
        "subject_name": "Physics",
        "unit_name": "Waves",
        "session_number": "1",
        "session_title": "Wave speed",
        "scenes": [s.model_dump() for s in scenes],
        "presentation": presentation(**over).model_dump(),
    })


def chapters(*starts: tuple[float, str]) -> list[Chapter]:
    return [Chapter(start=at, title=title) for at, title in starts]
