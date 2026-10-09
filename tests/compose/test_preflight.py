"""Render preflight: degradations the MP4 would show (silent scenes, fallback boards, title-only panels)."""

from __future__ import annotations

import pytest

from aadhi.compose.preflight import BLOCKING_REASONS, MESSAGES, has_blocking, render_preflight
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import MediaRef, ResolvedSidePanel

from .factories import make_manifest, make_screenplay, screenplay_dict


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def test_preflight_lists_fallbacks_and_panels_in_scene_order(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    items = render_preflight(tl)
    got = [(it["scene_id"], it["reason"]) for it in items]
    assert ("s-noaudio", "no_audio") in got
    assert ("s-sim-fallback", "simulation_without_media") in got
    assert ("s-video-none", "ai_video_without_media") in got
    assert ("s-noaudio", "panel_not_in_video") in got  # hotlinked GIF panel
    order = [s.scene_id for s in tl.scenes]
    assert [order.index(sid) for sid, _ in got] == sorted(order.index(sid) for sid, _ in got)
    silent = next(it for it in items if it["reason"] == "no_audio")
    scene = next(s for s in tl.scenes if s.scene_id == "s-noaudio")
    assert silent == {"scene_index": scene.index, "scene_id": "s-noaudio", "title": "Summary", "reason": "no_audio",
                      "blocking": True, "message": MESSAGES["no_audio"]}
    assert all(it["blocking"] == (it["reason"] in BLOCKING_REASONS) for it in items)
    assert has_blocking(items)


def test_preflight_clean_lecture_has_no_blocking_items(settings: Settings) -> None:
    data = screenplay_dict()
    data["scenes"] = [s for s in data["scenes"] if s["id"] in ("s-title", "s-chapter", "s-quiz")]
    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    assert render_preflight(tl) == [] and not has_blocking([])


def test_preflight_missing_panel_media_and_mismatched_audio(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    board = next(s for s in tl.scenes if s.scene_id == "s-board")
    missing = board.model_copy(update={"side_panel": ResolvedSidePanel(panel=board.side_panel.panel, media=None,
                                                                         show_at=board.side_panel.show_at)})
    hotlink = MediaRef(kind="gif", url="https://media.example/x.gif", render_in_mp4=False)
    video = next(s for s in tl.scenes if s.scene_id == "s-video").model_copy(update={"media": hotlink})
    scenes = [missing if s.scene_id == "s-board" else video if s.scene_id == "s-video" else s for s in tl.scenes]
    meta = {**tl.meta, "fallbacks": [{"scene_id": "s-title", "reason": "audio_mismatch"},
                                     {"scene_id": "s-title", "reason": "audio_mismatch"}, "junk", {"reason": "x"}]}
    items = render_preflight(tl.model_copy(update={"scenes": scenes, "meta": meta}))
    got = [(it["scene_id"], it["reason"], it["blocking"]) for it in items]
    assert got.count(("s-title", "audio_mismatch", True)) == 1  # duplicates collapsed, junk ignored
    assert ("s-board", "panel_media_missing", False) in got
    assert ("s-video", "media_not_in_video", False) in got
    assert got[0][0] == "s-title"


# --- quality items: scene findings are not crowded out by lecture-wide ones ---------------------


def _issue(code: str, severity: str, scene_id: str | None, n: int = 0) -> dict:
    from aadhi.pipeline.base import Issue

    return Issue(code=code, severity=severity, scene_id=scene_id, message=f"{code} {scene_id} {n}").model_dump(mode="json")


def test_lecture_wide_and_scene_findings_alternate_within_a_severity(settings: Settings) -> None:
    from aadhi.compose.preflight import QUALITY_MAX_ITEMS, quality_items

    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    board, title = "s-board", "s-title"
    lecture = [_issue("objective.unassessed", "warning", None, n) for n in range(15)]
    scenes = [_issue("board.item_too_long", "warning", board, 1), _issue("beat.too_long", "warning", title, 2)]
    errors = [_issue("factual.error", "error", board), _issue("lecture.broken", "error", None)]
    items = quality_items(lecture + scenes + errors, tl)
    got = [(it["severity"], it["scene_id"]) for it in items[:QUALITY_MAX_ITEMS]]
    # errors first (lecture-wide, then scene), then warnings alternating, scenes in scene order
    assert got[:6] == [("error", None), ("error", board), ("warning", None), ("warning", title), ("warning", None),
                       ("warning", board)]
    assert all(sid is None for _, sid in got[6:])
    assert items[-1]["message"].startswith(f"{len(lecture) + len(scenes) + len(errors) - QUALITY_MAX_ITEMS} more")


def test_scene_findings_alone_keep_scene_order_and_unknown_scenes_go_last(settings: Settings) -> None:
    from aadhi.compose.preflight import quality_items

    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    issues = [_issue("beat.too_long", "warning", "s-quiz"), _issue("beat.too_long", "warning", "gone-scene"),
              _issue("beat.too_long", "warning", "s-title")]
    assert [it["scene_id"] for it in quality_items(issues, tl)] == ["s-title", "s-quiz", "gone-scene"]
    assert [it["scene_id"] for it in quality_items(issues)] == ["s-quiz", "gone-scene", "s-title"]  # no timeline: as given


def test_a_hidden_scenes_lint_findings_are_not_listed_before_a_render(settings: Settings) -> None:
    from aadhi.compose.preflight import quality_items
    from aadhi.pipeline.validate import lint

    data = screenplay_dict()
    data["scenes"][2]["beats"][0]["narration"] = "Here is $V = IR$ written with markup."  # narration.markup: error
    shown = Screenplay.model_validate(data)
    assert any(it["code"] == "narration.markup" for it in quality_items(lint(shown), None))
    data["scenes"][2]["hidden"] = True
    hidden = Screenplay.model_validate(data)
    tl = build_timeline(hidden, make_manifest(hidden), settings=settings)
    assert not any(it["code"] == "narration.markup" or it["scene_id"] == "s-board"
                   for it in quality_items(lint(hidden), tl))


def test_critic_and_system_errors_of_a_hidden_scene_are_not_listed_before_a_render(settings: Settings) -> None:
    from aadhi.compose.preflight import quality_items

    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    critic = {**_issue("critic.factual", "error", "s-board"), "source": "critic"}
    system = {**_issue("translate.scene_failed", "error", "s-board"), "source": "system"}
    other = {**_issue("critic.factual", "error", "s-title"), "source": "critic"}
    issues = [critic, system, other]
    assert {it["scene_id"] for it in quality_items(issues, tl)} == {"s-board", "s-title"}  # nothing hidden: as before
    listed = quality_items(issues, tl, hidden={"s-board"})
    assert [(it["scene_id"], it["code"]) for it in listed] == [("s-title", "critic.factual")]
    # render_preflight passes ``hidden`` on to the quality items it appends
    from aadhi.compose.preflight import render_preflight as preflight

    assert not any(it.get("scene_id") == "s-board" and it["reason"] == "quality"
                   for it in preflight(tl, issues, hidden={"s-board"}))
