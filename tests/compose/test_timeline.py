"""build_timeline: every scene type, time base, quiz timing, fallbacks, estimation, chapters, captions."""

from __future__ import annotations

import pytest

from aadhi.compose.base import (
    INTRO_CARD_SECONDS,
    INTRO_LOGO_SECONDS,
    MASCOT_CLIPS,
    QUIZ_REVEAL_HOLD_SECONDS,
    SCENE_LEAD_SECONDS,
    SCENE_TAIL_SECONDS,
    SILENT_SCENE_SECONDS,
)
from aadhi.compose.captions import MAX_LINE_CHARS
from aadhi.compose.timeline import (
    FALLBACKS_META,
    STORAGE_KEYS_META,
    _plain_rich,
    build_timeline,
    default_branding,
    estimate_speech_seconds,
    ken_burns_for,
    timeline_asset_keys,
)
from aadhi.config import Settings
from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS
from aadhi.schemas.timeline import Timeline

from .factories import SPEECH, make_manifest, make_screenplay, screenplay_dict


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, render_fps=30)


@pytest.fixture()
def built(settings: Settings) -> Timeline:
    sp = make_screenplay()
    return build_timeline(sp, make_manifest(sp), settings=settings, version_id=7, revision=3)


def scene(tl: Timeline, sid: str):
    return next(s for s in tl.scenes if s.scene_id == sid)


# --- global invariants -------------------------------------------------------------------------


def test_timeline_invariants_and_roundtrip(built: Timeline) -> None:
    again = Timeline.model_validate(built.model_dump(mode="json"))  # validator passes
    assert again == built
    assert built.version_id == 7 and built.screenplay_revision == 3
    assert built.scenes[0].start == pytest.approx(built.intro.duration)
    for a, b in zip(built.scenes, built.scenes[1:], strict=False):
        assert b.start == pytest.approx(a.start + a.duration, abs=1e-3)
    last = built.scenes[-1]
    assert built.total_duration == pytest.approx(last.start + last.duration, abs=1e-3)
    assert [s.index for s in built.scenes] == list(range(len(built.scenes)))
    for s in built.scenes:
        assert s.duration > built.transition_seconds
        for b in s.beats:
            assert 0 <= b.start <= b.speech_end <= b.end <= s.duration + 1e-6
            for w in b.words:
                assert b.start - 1e-6 <= w.start <= w.end
            for c in b.captions:
                assert b.start - 1e-6 <= c.start < c.end <= b.end + 1e-6
        assert [b.index for b in s.beats] == list(range(len(s.beats)))


def test_urls_are_none_and_storage_keys_in_meta(built: Timeline) -> None:
    assert all(s.audio_url is None for s in built.scenes)
    for s in built.scenes:
        for ref in [s.media, s.poster, *(s.figures.values()), s.side_panel.media if s.side_panel else None]:
            if ref is not None and ref.asset_key:
                assert ref.url is None
    keys = built.meta[STORAGE_KEYS_META]
    assert keys["manim-sim"] == "assets/manim/manim-sim/m.mp4"
    assert keys["scene_audio-s-board"].startswith("assets/scene_audio/")
    assert built.meta["subject_name"] == "Basic Electrical Engineering"
    assert timeline_asset_keys(built) >= {"manim-sim", "figure-aaa", "image-still", "poster-1", "scene_audio-s-quiz"}


def test_deterministic(settings: Settings) -> None:
    sp = make_screenplay()
    a = build_timeline(sp, make_manifest(sp), settings=settings).model_dump_json()
    b = build_timeline(make_screenplay(), make_manifest(make_screenplay()), settings=settings).model_dump_json()
    assert a == b


def test_settings_fps_and_stage(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, render_fps=25, render_width=1280,
                                                                   render_height=720))
    assert tl.fps == 25 and (tl.width, tl.height) == (1920, 1080)
    assert tl.language == "en-IN" and tl.board_language == "en-IN"
    assert tl.concept_map[1].id == "ohm" and tl.learning_objectives[0].id == "obj-1"


# --- intro --------------------------------------------------------------------------------------


def test_intro_on(built: Timeline) -> None:
    intro = built.intro
    assert intro is not None
    # batch 2 review: the intro logo without the generator's sparkle mark (compose.base.LOGO_VIDEO)
    assert intro.logo_video_url == "/branding/logo_animation_clean.mp4"
    assert intro.background_url == "/branding/static_background.png"
    assert intro.logo_duration == INTRO_LOGO_SECONDS
    assert [(c.line1, c.line2) for c in intro.cards] == [
        ("Basic Electrical Engineering", "Unit 2: DC Circuits"), ("Session 3", "Ohm's Law")]
    assert [c.start for c in intro.cards] == [INTRO_LOGO_SECONDS, INTRO_LOGO_SECONDS + INTRO_CARD_SECONDS]
    assert intro.duration == pytest.approx(INTRO_LOGO_SECONDS + 2 * INTRO_CARD_SECONDS)


def test_intro_off_and_partial_cards(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings, include_intro=False)
    assert tl.intro is None and tl.scenes[0].start == 0.0
    with_intro = build_timeline(sp, make_manifest(sp), settings=settings)
    assert with_intro.total_duration == pytest.approx(tl.total_duration + with_intro.intro.duration, abs=1e-3)
    assert with_intro.captions[0].start == pytest.approx(tl.captions[0].start + with_intro.intro.duration, abs=1e-3)
    sp2 = make_screenplay(subject_name="", unit_name="")
    tl2 = build_timeline(sp2, make_manifest(sp2), settings=settings)
    assert len(tl2.intro.cards) == 1 and tl2.intro.cards[0].line1 == "Session 3"
    sp3 = make_screenplay(subject_name="", unit_name="Only unit")
    assert build_timeline(sp3, None, settings=settings).intro.cards[0].line1 == "Only unit"


# --- per scene type -----------------------------------------------------------------------------


def test_board_scene(built: Timeline) -> None:
    s = scene(built, "s-board")
    assert s.type == "content" and s.audio_offset == SCENE_LEAD_SECONDS
    assert s.audio_asset_key == "scene_audio-s-board"
    assert [b.board_item_id for b in s.beats] == ["p1", "f1", "fig", "e1", None]
    assert s.beats[4].fill_item_id == "e1" and s.beats[4].highlight_item_ids == ["f1", "p1"]
    b1, b2 = s.beats[0], s.beats[1]
    assert b1.start == pytest.approx(SCENE_LEAD_SECONDS)
    assert b1.speech_end == pytest.approx(SCENE_LEAD_SECONDS + SPEECH)
    assert b1.end == pytest.approx(b1.speech_end + 0.5)  # pause_after
    assert b2.start == pytest.approx(b1.end + INTER_BEAT_GAP_SECONDS)
    assert b1.words[0].start == pytest.approx(b1.start) and b1.words[0].text == "Voltage"
    assert b1.captions[0].text == "Voltage is proportional to current."
    assert b1.captions[-1].end == pytest.approx(b1.speech_end + 0.4)  # caption hold into the pause
    assert [i.id for i in s.board] == ["h", "p1", "f1", "fig", "e1"]
    assert s.figures["fig"].asset_key == "figure-aaa" and s.figures["fig"].kind == "image"
    assert s.layout.show_side_panel and not s.layout.fullscreen_media
    assert s.side_panel.show_at == pytest.approx(s.beats[2].start)
    assert s.side_panel.media.asset_key == "figure-aaa"  # from the SourceFigure
    assert s.objective_ids == ["obj-1"] and s.concept_id == "ohm"


def test_scene_duration_formula(built: Timeline) -> None:
    sp = make_screenplay()
    manifest = make_manifest(sp)
    title = scene(built, "s-title")
    assert title.duration == pytest.approx(SCENE_LEAD_SECONDS + manifest.audio["s-title"].duration + SCENE_TAIL_SECONDS)
    assert title.audio_duration == pytest.approx(manifest.audio["s-title"].duration)
    chapter = scene(built, "s-chapter")
    assert chapter.duration == SILENT_SCENE_SECONDS and chapter.beats == []
    assert chapter.chapter_label == "Part 1" and chapter.audio_asset_key is None


def test_quiz_timing(built: Timeline) -> None:
    sp = make_screenplay()
    audio = make_manifest(sp).audio["s-quiz"]
    q = scene(built, "s-quiz")
    assert q.quiz is not None
    assert q.quiz.countdown_start == pytest.approx(SCENE_LEAD_SECONDS + audio.countdown_start)
    assert q.quiz.countdown_seconds == 5
    assert q.quiz.reveal_start == pytest.approx(q.quiz.countdown_start + 5)
    assert [b.phase for b in q.beats] == ["main", "reveal"]
    assert q.beats[1].start == pytest.approx(q.quiz.reveal_start)
    assert q.quiz.options[0] == "It halves" and q.quiz.correct_index == 0
    assert q.quiz.feedback_wrong == ["", "No: I = V/R", "No: R changed"]
    assert q.duration == pytest.approx(SCENE_LEAD_SECONDS + audio.duration + SCENE_TAIL_SECONDS
                                       + QUIZ_REVEAL_HOLD_SECONDS)


def test_quiz_estimated_layout(settings: Settings) -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, None, settings=settings)
    q = scene(tl, "s-quiz")
    main, reveal = q.beats
    assert q.quiz.countdown_start == pytest.approx(main.end + INTER_BEAT_GAP_SECONDS, abs=1e-3)
    assert reveal.start == pytest.approx(q.quiz.countdown_start + 5, abs=1e-3)
    assert q.quiz.reveal_start == pytest.approx(reveal.start, abs=1e-3)


def test_simulation_with_media(built: Timeline) -> None:
    s = scene(built, "s-sim")
    assert s.layout.fullscreen_media and s.layout.mascot_position == "right"
    assert s.media.kind == "video" and s.media.end_behavior == "freeze" and s.media.fit == "contain"
    assert s.media.asset_key == "manim-sim" and s.media.duration == 6.0
    assert [b.visual_cue for b in s.beats] == ["Current arrow grows", "Voltage bar doubles"]
    assert s.board == []


def test_simulation_fallback_board(built: Timeline) -> None:
    s = scene(built, "s-sim-fallback")
    assert s.media is None and not s.layout.fullscreen_media
    assert [i.kind.value for i in s.board] == ["bullet", "bullet"]
    assert s.board[0].text == "Battery appears with \\$5 label"  # rich-lite escaped
    assert s.board[1].text == "Bulb \\*glows\\*"
    assert [b.board_item_id for b in s.beats] == ["cue-1", None, "cue-2"]
    assert {"scene_id": "s-sim-fallback", "reason": "simulation_without_media"} in built.meta[FALLBACKS_META]


def test_plain_rich_escapes_every_rich_lite_special() -> None:
    bs = chr(92)
    raw = f"call `f` on [[x]] with $5 * 2 and C:{bs}dir"
    out = _plain_rich(raw)
    expected = f"call {bs}`f{bs}` on {bs}[{bs}[x{bs}]{bs}] with {bs}$5 {bs}* 2 and C:{bs}{bs}dir"
    assert out == expected
    # never longer than the limit and never cut inside an escape pair
    cut = _plain_rich("ab$$$$", limit=4)
    assert cut == f"ab{bs}$" and len(cut) <= 4
    assert _plain_rich(f"x{bs}", limit=2) == "x"  # the escaped backslash pair would exceed the limit
    assert _plain_rich("  plain text  ") == "plain text"
    assert len(_plain_rich("$" * 2000)) == 1200


def test_ai_video_fallback_still_has_ken_burns(built: Timeline) -> None:
    s = scene(built, "s-video")
    assert s.layout.fullscreen_media
    assert s.media.kind == "image" and s.media.fit == "cover"
    kb = s.media.ken_burns
    assert kb is not None and kb == ken_burns_for("s-video")
    assert kb.start.scale >= 1 and kb.end.scale >= 1 and abs(kb.end.scale - kb.start.scale) >= 0.08
    assert 0.4 <= kb.start.cx <= 0.6 and 0.4 <= kb.end.cy <= 0.6
    assert ken_burns_for("s-video") == ken_burns_for("s-video")
    assert ken_burns_for("other") != ken_burns_for("s-video")


def test_ai_video_without_media_becomes_board(built: Timeline) -> None:
    s = scene(built, "s-video-none")
    assert s.media is None and not s.layout.fullscreen_media
    assert [(i.kind.value, i.text) for i in s.board] == [("heading", "Substation"),
                                                         ("paragraph", "Slow pan across a substation at dusk")]


def test_ai_video_figure_fallback(settings: Settings) -> None:
    data = screenplay_dict()
    vid = next(s for s in data["scenes"] if s["id"] == "s-video-none")
    vid["fallback_figure_id"] = "fig-1"
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    s = scene(build_timeline(sp, make_manifest(sp), settings=settings), "s-video-none")
    assert s.media.asset_key == "figure-aaa" and s.media.ken_burns is not None and s.media.fit == "cover"
    assert s.layout.fullscreen_media


def test_interactive(built: Timeline) -> None:
    s = scene(built, "s-interactive")
    assert s.layout.fullscreen_media and s.layout.mascot_position == "hidden"
    assert s.p5_code.startswith("function setup")
    assert s.poster.asset_key == "poster-1" and s.media is None


def test_gif_hotlink_panel(built: Timeline) -> None:
    s = scene(built, "s-noaudio")
    m = s.side_panel.media
    assert m.kind == "gif" and m.asset_key is None and not m.render_in_mp4
    assert m.url == "https://media.giphy.com/media/abc/giphy.gif"
    assert m.attribution == "Powered by GIPHY"


# --- estimation ---------------------------------------------------------------------------------


def test_scene_without_audio_is_estimated(built: Timeline) -> None:
    s = scene(built, "s-noaudio")
    assert s.audio_asset_key is None and s.audio_duration == 0.0
    b = s.beats[0]
    assert b.estimated
    assert b.speech_end - b.start == pytest.approx(estimate_speech_seconds(b.narration), abs=1e-3)
    assert b.words and b.words[-1].end == pytest.approx(b.speech_end, abs=2e-3)
    assert built.estimated
    assert {"scene_id": "s-noaudio", "reason": "no_audio"} in built.meta[FALLBACKS_META]
    assert estimate_speech_seconds("abc") == 0.6
    assert estimate_speech_seconds("x" * 100) == pytest.approx(6.5)


def test_all_audio_present_not_estimated(settings: Settings) -> None:
    sp = make_screenplay()
    data = screenplay_dict()
    data["scenes"] = [s for s in data["scenes"] if s["id"] != "s-noaudio"]
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    assert not tl.estimated


def test_audio_missing_a_beat_falls_back_with_reuse(settings: Settings) -> None:
    sp = make_screenplay()
    manifest = make_manifest(sp)
    audio = manifest.audio["s-board"]
    manifest.audio["s-board"] = audio.model_copy(update={"beats": audio.beats[:-1]})
    s = scene(build_timeline(sp, manifest, settings=settings), "s-board")
    assert s.audio_asset_key is None
    assert [b.estimated for b in s.beats] == [False, False, False, False, True]
    assert s.beats[0].speech_end - s.beats[0].start == pytest.approx(SPEECH)


def test_no_manifest_everything_estimated(settings: Settings) -> None:
    tl = build_timeline(make_screenplay(), None, settings=settings)
    assert tl.estimated
    assert all(b.estimated for s in tl.scenes for b in s.beats)
    assert scene(tl, "s-sim").media is None  # no manim yet -> board fallback
    Timeline.model_validate(tl.model_dump())


# --- captions & chapters ------------------------------------------------------------------------


def test_absolute_captions(built: Timeline) -> None:
    expected = []
    for s in built.scenes:
        for b in s.beats:
            expected += [(round(c.start + s.start, 3), c.text) for c in b.captions]
    got = [(c.start, c.text) for c in built.captions]
    assert [t for _, t in got] == [t for _, t in expected]
    for (a, _), (b, _) in zip(got, expected, strict=True):
        assert a == pytest.approx(b, abs=2e-3)
    for c in built.captions:
        assert all(len(ln) <= MAX_LINE_CHARS for ln in c.text.split("\n")) and c.text.count("\n") <= 1
    for a, b in zip(built.captions, built.captions[1:], strict=False):
        assert a.end <= b.start + 1e-9


def test_long_beat_caption_split(settings: Settings) -> None:
    data = screenplay_dict()
    long = ("Resistance opposes the flow of electric current, and its value depends on the material, the length "
            "and the cross-sectional area of the conductor. Longer wires resist more. Thicker wires resist less.")
    data["scenes"][0]["beats"][0]["narration"] = long
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    b = scene(build_timeline(sp, None, settings=settings), "s-title").beats[0]
    assert len(b.captions) >= 3
    assert " ".join(c.text.replace("\n", " ") for c in b.captions) == long


def test_chapters_from_chapter_cards(built: Timeline) -> None:
    assert [(c.start, c.title) for c in built.chapters] == [(0.0, "Introduction"),
                                                            (scene(built, "s-chapter").start, "Current")]


def test_chapters_from_screenplay_chapters(settings: Settings) -> None:
    data = screenplay_dict()
    data["chapters"] = [{"id": "c1", "title": "Foundations", "scene_ids": ["s-title", "s-chapter"]},
                        {"id": "c2", "title": "The Law", "scene_ids": ["s-board"]},
                        {"id": "c3", "title": "Practice", "scene_ids": []}]
    data["scenes"][8]["chapter_id"] = "c3"  # quiz joins chapter c3 via chapter_id
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    assert [(c.title, c.start) for c in tl.chapters] == [
        ("Introduction", 0.0), ("Foundations", scene(tl, "s-title").start), ("The Law", scene(tl, "s-board").start),
        ("Practice", scene(tl, "s-quiz").start)]
    no_intro = build_timeline(sp, make_manifest(sp), settings=settings, include_intro=False)
    assert no_intro.chapters[0].title == "Foundations" and no_intro.chapters[0].start == 0.0


@pytest.mark.parametrize(("first_title", "opening"), [("Introduction", "Opening"), ("  introduction ", "Opening"),
                                                      ("Basics", "Introduction")])
def test_opening_chapter_never_duplicates_a_real_introduction(settings: Settings, first_title: str,
                                                              opening: str) -> None:
    data = screenplay_dict()
    data["chapters"] = [{"id": "c1", "title": first_title, "scene_ids": ["s-title", "s-chapter"]},
                        {"id": "c2", "title": "The Law", "scene_ids": ["s-board"]}]
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)  # the intro delays the first chapter
    assert [c.title for c in tl.chapters] == [opening, " ".join(first_title.split()), "The Law"]
    assert tl.chapters[0].start == 0.0 and tl.chapters[1].start == scene(tl, "s-title").start
    data["chapters"].append({"id": "c3", "title": "Opening", "scene_ids": ["s-quiz"]})
    tl2 = build_timeline(Screenplay.model_validate(data), make_manifest(sp), settings=settings)
    assert tl2.chapters[0].title == ("Lesson start" if opening == "Opening" else "Introduction")


def test_chapters_from_concept_changes(settings: Settings) -> None:
    data = screenplay_dict()
    data["scenes"] = [s for s in data["scenes"] if s["type"] != "chapter_card"]
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=settings)
    assert [(c.title, c.start) for c in tl.chapters] == [
        ("Introduction", 0.0), ("Charge and Current", scene(tl, "s-title").start),
        ("Ohm's Law", scene(tl, "s-board").start)]
    data["scenes"] = [{**s, "concept_id": None} for s in data["scenes"]]
    data["concept_map"] = []
    data["learning_objectives"] = []
    for s in data["scenes"]:
        s["objective_ids"] = []
    sp2 = Screenplay.model_validate(data)
    assert [c.title for c in build_timeline(sp2, None, settings=settings).chapters] == ["Introduction"]


# --- branding -----------------------------------------------------------------------------------


def test_default_branding(settings: Settings, built: Timeline) -> None:
    b = default_branding(settings)
    assert set(b.mascot_clips) == set(MASCOT_CLIPS)
    assert b.mascot_clips["left"] == "/branding/aadhi_left_clean.mp4"  # bar-free re-encode of aadhi_left.mp4
    assert b.mascot_clips["popup_bottom_right"] == "/branding/aadhi_popup_clean.mp4"  # watermark-free re-encode
    assert b.static_background_url == "/branding/static_background.png" and b.bgm_url == "/branding/bgm.mp3"
    assert b.tick_url is None and b.ding_url is None and b.bgm_volume == pytest.approx(0.06)
    assert built.branding == b
    custom = b.model_copy(update={"bgm_url": None})
    sp = make_screenplay()
    assert build_timeline(sp, None, settings=settings, branding=custom).branding.bgm_url is None


def test_empty_screenplay(settings: Settings) -> None:
    sp = make_screenplay(scenes=[], chapters=[])
    tl = build_timeline(sp, None, settings=settings)
    assert tl.scenes == [] and tl.total_duration == pytest.approx(tl.intro.duration)
    assert [c.title for c in tl.chapters] == ["Introduction"] and tl.captions == []
    bare = build_timeline(sp, None, settings=settings, include_intro=False)
    assert bare.total_duration == 0.0 and bare.intro is None
