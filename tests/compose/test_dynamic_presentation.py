"""Dynamic timelines (design §6.7): ``Timeline.presentation`` and ``TimedScene.look`` from a screenplay's presentation
(``aadhi.compose.presentation``, wired in ``compose.timeline``), behind the admin flag ``DYNAMIC_ENABLED``.

Classic timelines never change: without a presentation, or with the flag off, the builder's output is byte-identical.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.compose import presentation as pres
from aadhi.compose.captions import MAX_LINES
from aadhi.compose.dynamic import direct
from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import themes as th
from aadhi.compose.timeline import DYNAMIC_FPS, build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Chapter, Timeline

from .factories import make_manifest, screenplay_dict

HEX = re.compile(r"^#[0-9A-F]{6}$")


@pytest.fixture()
def flag_on() -> Settings:
    # Aadhi's appearances (DYNAMIC_AADHI_INTRO) are off here: these tests pin the B1/B2 resolver's choices per screenplay
    # scene; the appearances have their own tests (tests/compose/dynamic/test_presenter.py)
    return Settings(_env_file=None, render_fps=25, dynamic_enabled=True, dynamic_aadhi_intro=False)


@pytest.fixture()
def flag_off() -> Settings:
    return Settings(_env_file=None, render_fps=25)


def presentation(fmt: str = "landscape", **kw: Any) -> dict[str, Any]:
    from tests.schemas.v3_fixtures import PRESENTATION

    return {**PRESENTATION, "format": fmt, "hook": None, "theme_family": "midnight_indigo", "captions_burn": True,
            "music": {"source": "procedural", "provider": "procedural"}, **kw}


def dynamic_screenplay(fmt: str = "landscape", *, chapters: bool = True, looks: dict[str, Any] | None = None,
                       **kw: Any) -> Screenplay:
    data = screenplay_dict()
    data["presentation"] = presentation(fmt, **kw)
    if chapters:
        ids = [s["id"] for s in data["scenes"]]
        data["chapters"] = [{"id": "c1", "title": "Basics", "scene_ids": ids[:2]},
                            {"id": "c2", "title": "The law", "scene_ids": ids[2:6]},
                            {"id": "c3", "title": "Practice", "scene_ids": ids[6:]}]
    for scene in data["scenes"]:
        if looks and scene["id"] in looks:
            scene["look"] = looks[scene["id"]]
    return Screenplay.model_validate(data)


def build(sp: Screenplay, settings: Settings) -> Timeline:
    return build_timeline(sp, make_manifest(sp), settings=settings)


def dump(tl: Timeline) -> str:
    return json.dumps(tl.model_dump(mode="json"), sort_keys=True)


# --- classic invariance ------------------------------------------------------------------------------------------


def test_classic_timelines_are_byte_identical_with_the_flag_on(flag_on: Settings, flag_off: Settings) -> None:
    sp = Screenplay.model_validate(screenplay_dict())
    on, off = build(sp, flag_on), build(sp, flag_off)
    assert dump(on) == dump(off)
    assert on.presentation is None and all(s.look is None for s in on.scenes)
    assert on.intro is not None and on.fps == 25
    assert "presentation" not in on.model_dump(mode="json")


def test_word_anchored_cues_snap_to_the_frame_grid_of_the_video(flag_on: Settings, flag_off: Settings,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """A Dynamic timeline is 30 fps whatever ``RENDER_FPS`` says (25 here): its sync cues merge onto beat boundaries
    and each other within one frame of that grid, the one the video is captured on. Classic timelines keep
    ``RENDER_FPS`` (with the flag on, and for a presentation while the flag is off)."""
    from aadhi.compose import timeline as timeline_mod
    from aadhi.compose.sync import SyncOptions

    assert SyncOptions.from_settings(flag_on) == SyncOptions(fps=25)
    assert SyncOptions.from_settings(flag_on, fps=DYNAMIC_FPS) == SyncOptions(fps=30)
    seen: list[int] = []
    real = timeline_mod.scene_sync_cues

    def spy(beats: Any, board: Any, side_panel: Any, duration: float, options: SyncOptions) -> Any:
        seen.append(options.fps)
        return real(beats, board, side_panel, duration, options)

    monkeypatch.setattr(timeline_mod, "scene_sync_cues", spy)
    for sp, settings, fps in ((dynamic_screenplay(), flag_on, 30), (dynamic_screenplay("portrait"), flag_on, 30),
                              (Screenplay.model_validate(screenplay_dict()), flag_on, 25),
                              (dynamic_screenplay(), flag_off, 25)):
        seen.clear()
        tl = build(sp, settings)
        assert tl.fps == fps and seen and set(seen) == {fps}, (sp.presentation, settings.dynamic_enabled)


def test_with_the_flag_off_a_presentation_builds_as_before(flag_off: Settings) -> None:
    sp = dynamic_screenplay()
    tl = build(sp, flag_off)
    assert tl.presentation is None and all(s.look is None for s in tl.scenes)
    assert tl.intro is not None, "B0 behaviour: the classic intro, only the stage follows the format"
    assert (tl.width, tl.height) == (1920, 1080)


# --- the presentation ----------------------------------------------------------------------------------------------


def test_presentation_is_resolved(flag_on: Settings) -> None:
    tl = build(dynamic_screenplay(), flag_on)
    p = tl.presentation
    assert p is not None and p.style == "dynamic" and p.format == "landscape"
    assert p.content == pres.content_tokens("dark")
    assert set(p.content) == {"ink", "ink2", "entity:0", "entity:1", "entity:2", "warn", "ok", "mark", "focus"}
    assert all(HEX.match(v) for v in p.content.values())
    assert p.fonts.model_dump(mode="json") == {"display": "outfit", "body": "inter", "mono": "jetbrains_mono",
                                               "indic": "noto_sans"}  # B2b: the B2 form of B1's faces
    assert (p.personality, p.caption_style, p.cue_term_style) == ("crisp", "clean_pill", "underline")
    assert (p.captions_burn, p.sfx, p.motion) == (True, False, "calm")
    assert p.engine_version == pres.ENGINE_VERSION == 5  # the look-engine generation of the B2 resolver
    assert p.content_alt == pres.content_tokens("light"), "the other mode's palette (code editor surface)"
    assert (p.family, p.mode, p.motion_scale) == ("midnight_indigo", "dark", 0.85)  # Crisp: x0.85 (§4.8)
    assert p.entity_colors == {"V": 0, "I": 1, "R": 2}  # Presentation.entity_colors, lesson-wide (R-30)
    assert p.generated_seconds == pytest.approx(tl.total_duration)
    assert tl.intro is None, "a Dynamic timeline has no classic intro"
    assert tl.scenes[0].start == 0
    assert tl.fps == DYNAMIC_FPS == 30
    assert tl.schema_version == 3


def test_looks_of_every_scene(flag_on: Settings) -> None:
    tl = build(dynamic_screenplay(), flag_on)
    for s in tl.scenes:
        look = s.look
        # the director names a template the stage draws (presentation.TEMPLATES), board_generic as the fallback
        assert look is not None and look.template in pres.TEMPLATES
        # B1's five anchors first, then the colours the B1 stage derived at run time (plan §1.2)
        assert tuple(look.tokens) == ("struct.accent", "bg", "bg-alt", "bg-tint", "surface", "block.base",
                                      "block.highlight", "block.ink", "block.ink2", "scrim")
        assert all(HEX.match(v) for k, v in look.tokens.items() if k != "scrim")
        assert re.fullmatch(r"#[0-9A-F]{6}C7", look.tokens["scrim"])  # the bg at the stage's scrim alpha
        assert look.background["preset"] == ("colour_block" if s.type == "chapter_card" else "mesh_drift")
        assert look.background["params"]["tone"] in pres.TONES
        assert 0 <= look.background["seed"] < 2**31
        # R-55: a dense template (or a hold) freezes the background from the scene start, nothing else does
        dense = look.template in pres.DENSE_TEMPLATES or look.hold_reason is not None
        assert look.background["frozen_at"] == (0.0 if dense else None)
        assert look.transition_in["kind"] in pres.TRANSITIONS
    seeds = [s.look.background["seed"] for s in tl.scenes]
    assert len(set(seeds)) == len(seeds), "one composition per scene"
    kinds = [s.look.transition_in["kind"] for s in tl.scenes]
    assert kinds[0] == "cut"
    chapter = next(i for i, s in enumerate(tl.scenes) if s.type == "chapter_card")
    assert kinds[chapter] == "crossfade" and kinds[chapter + 1] == "crossfade", "full-frame colour changes fade (R-54)"
    assert kinds[2] == "crossfade", "a new chapter"
    assert kinds[3] == "cut", "hard cuts inside a chapter (R-23)"
    for s in tl.scenes:
        if s.look.transition_in["kind"] == "crossfade":
            assert 0.3 <= s.look.transition_in["seconds"] <= min(0.5, s.duration)
    # the structure accent rotates per chapter, >= 30 degrees apart (R-52)
    accents = []
    for s in tl.scenes:
        if not accents or accents[-1] != s.look.tokens["struct.accent"]:
            accents.append(s.look.tokens["struct.accent"])
    assert len(accents) == 3


def test_a_rebuild_never_re_rolls_and_the_json_round_trips(flag_on: Settings) -> None:
    sp = dynamic_screenplay("portrait")
    a, b = build(sp, flag_on), build(sp, flag_on)
    assert dump(a) == dump(b)
    again = Timeline.model_validate(a.model_dump(mode="json"))
    assert dump(again) == dump(a)
    assert preview_timeline(sp, make_manifest(sp), settings=flag_on).presentation is not None


def test_the_look_seed_and_epoch_change_the_compositions(flag_on: Settings) -> None:
    base = build(dynamic_screenplay(), flag_on)
    other = build(dynamic_screenplay(look_seed=99), flag_on)
    bumped = build(dynamic_screenplay(look_epoch=7), flag_on)
    seeds = [s.look.background["seed"] for s in base.scenes]
    assert seeds != [s.look.background["seed"] for s in other.scenes]
    assert seeds != [s.look.background["seed"] for s in bumped.scenes]


def test_the_teachers_look_is_honoured_within_what_b1_draws(flag_on: Settings) -> None:
    looks = {
        "s-board": {"template": "formula_focus", "background": "colour_block", "transition_in": "crossfade",
                    "hold_reason": "formula"},
        "s-sim": {"background": "shader_mesh", "transition_in": "whip"},
        "s-video": {"transition_in": "crossfade"},
    }
    tl = build(dynamic_screenplay(looks=looks), flag_on)
    by = {s.scene_id: s for s in tl.scenes}
    board = by["s-board"].look
    assert board.template == "board_generic", "unreleased templates fall back to board_generic"
    assert board.background["preset"] == "colour_block"
    assert board.hold_reason == "formula" and board.background["frozen_at"] == 0.0
    sim = by["s-sim"].look
    assert sim.background["preset"] == "mesh_drift"
    assert sim.transition_in["kind"] == "crossfade", "leaving a colour block always fades"
    assert by["s-video"].look.transition_in["kind"] == "crossfade"


def test_a_dense_template_freezes_its_background_throughout(flag_on: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-55 (§4.5): throughout formula_focus, worked_steps and code_walkthrough the background is frozen from the
    scene start, as a hold freezes it; a scene without a hold in any other template keeps its drifting background.
    This screenplay's boards are empty, so the director names ``board_generic`` for every scene; the rule under test
    is the resolver's, so the director's plan for the content scene is rewritten to formula_focus here (the resolver
    takes the template from the plan, §4.4)."""
    import dataclasses

    from aadhi.compose.dynamic import direct as dr

    real_plan = dr.plan

    def plan_with_a_formula(*a: Any, **kw: Any) -> Any:
        return [dataclasses.replace(p, template="formula_focus") if p.scene_id == "s-board" else p
                for p in real_plan(*a, **kw)]

    monkeypatch.setattr(dr, "plan", plan_with_a_formula)
    tl = build(dynamic_screenplay(), flag_on)
    by = {s.scene_id: s for s in tl.scenes}
    board = by["s-board"].look
    assert board.template == "formula_focus" and board.hold_reason is None
    assert board.background["frozen_at"] == 0.0, "frozen from the scene start"
    # the simulation scene is animation_stage's since step 4 (forced, and the template is implemented); it is not
    # dense, so its background still runs
    assert by["s-sim"].look.template == "animation_stage"
    assert by["s-sim"].look.background["frozen_at"] is None


def test_portrait_reels_get_short_phrase_captions(flag_on: Settings) -> None:
    tl = build(dynamic_screenplay("portrait"), flag_on)
    assert (tl.width, tl.height) == (1080, 1920)
    cues = [c for s in tl.scenes for b in s.beats for c in b.captions]
    assert cues
    for c in cues:
        lines = c.text.split("\n")
        assert len(lines) <= MAX_LINES
        assert all(len(line) <= pres.PORTRAIT_CAPTION_LINE_CHARS for line in lines if " " in line)
        assert len(c.text.replace("\n", " ")) <= 2 * pres.PORTRAIT_CAPTION_LINE_CHARS + 1  # R-32: <= 32 chars
    landscape = build(dynamic_screenplay("landscape"), flag_on)
    classic = build(Screenplay.model_validate(screenplay_dict()), flag_on)
    assert [c.text for c in landscape.captions] == [c.text for c in classic.captions], "16:9 keeps the phrase captions"


def test_unknown_families_map_onto_an_r1_family_deterministically(flag_on: Settings) -> None:
    assert pres.family_for("teal_coral") == pres.family_for("teal_coral")
    assert pres.family_for("teal_coral") in pres.FAMILIES.values()
    light = build(dynamic_screenplay(theme_family="paper_ink"), flag_on)
    assert light.presentation.content == pres.content_tokens("light")


# --- colour helpers --------------------------------------------------------------------------------------------------


def test_oklch_to_hex() -> None:
    assert pres.oklch_hex(1.0, 0.0, 0.0) == "#FFFFFF"
    assert pres.oklch_hex(0.0, 0.0, 0.0) == "#000000"
    assert pres.oklch_hex(0.62796, 0.25768, 29.234) == "#FF0000"
    assert HEX.match(pres.oklch_hex(0.9, 0.4, 140.0)), "out of gamut: the chroma is reduced"


def test_accent_rotation_keeps_neighbours_apart() -> None:
    for fam in pres.FAMILIES.values():
        hues = [pres.accent_hue(fam, k) for k in range(6)]
        for a, b in zip(hues, hues[1:]):
            d = abs(a - b) % 360
            assert min(d, 360 - d) >= 30


def test_content_inks_hold_contrast_on_every_background_tone() -> None:
    def lum(hex_: str) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for fam in pres.FAMILIES.values():
        ink = pres.content_tokens(fam.mode)
        for k in range(3):
            tokens = pres.structure_tokens(fam, pres.accent_hue(fam, k))
            for bg in ("bg", "bg-alt", "bg-tint", "surface"):
                assert ratio(ink["ink"], tokens[bg]) >= 7.0, (fam, bg)
                assert ratio(ink["ink2"], tokens[bg]) >= 4.5, (fam, bg)


def test_dynamic_enabled_reads_the_flag() -> None:
    assert pres.dynamic_enabled(Settings(_env_file=None, dynamic_enabled=True)) is True
    assert pres.dynamic_enabled(Settings(_env_file=None)) is False
    assert pres.dynamic_enabled(object()) is False
    with pytest.raises(ValueError):
        pres.resolve_looks(Screenplay.model_validate(screenplay_dict()), [])


# --- 9:16 phrase captions: cluster-safe splits, word sync, no chunk flashing by (R-32) ----------------------------

NARROW_SENTENCES = {
    "ta": "இந்த விதி ஜார்ஜ் ஓம் அவர்களால் கண்டுபிடிக்கப்பட்டது.",
    "hi": "यह नियम अंतर्राष्ट्रीयकरण पर लागू है।",
    "te": "ఈ నియమం విద్యుదయస్కాంతత్వాన్ని వివరిస్తుంది.",
    "ml": "ഈ നിയമം വൈദ്യുതകാന്തികതയെക്കുറിച്ചാണ്.",
    "en": "The resistance of a wire is temperature-dependent in metals.",
}


def _one_to_one_words(text: str, start: float = 1.0, step: float = 0.4) -> list[Any]:
    from aadhi.schemas.timeline import TimedWord

    return [TimedWord(text=w, start=round(start + k * step, 3), end=round(start + (k + 1) * step - 0.05, 3))
            for k, w in enumerate(text.split())]


def _check_cluster_safe_cues(text: str, cues: list[Any], words: list[Any], width: int) -> None:
    import unicodedata

    pieces = [p for c in cues for line in c.text.split("\n") for p in line.split(" ") if p]
    assert "".join(pieces) == "".join(text.split()), "every piece of every word is shown once, in order"
    for c in cues:
        for line in c.text.split("\n"):
            assert len(line) <= width, line
            first = line[0]
            assert unicodedata.category(first)[0] != "M", f"line starts mid-cluster: {line!r}"
            assert first not in "\u200c\u200d", f"line starts with a joiner: {line!r}"
    # each piece: (the word it belongs to, whether it begins that word); a cut is never right after a virama
    starts: dict[int, tuple[int, bool]] = {}
    k = 0
    for wi, w in enumerate(text.split()):
        acc = ""
        while acc != w:
            if acc:
                assert unicodedata.combining(acc[-1]) != 9, f"cut after a virama in {w!r}"
                assert unicodedata.category(pieces[k][0])[0] != "M", f"cut before a vowel sign in {w!r}"
            starts[k] = (wi, acc == "")
            acc += pieces[k]
            k += 1
    # word sync: a cue that begins a word starts at that word's own TTS start
    k = 0
    for c in cues:
        wi, begins = starts[k]
        if begins:
            assert c.start == pytest.approx(words[wi].start, abs=1e-3), (c.text, c.start, words[wi].start)
        else:
            assert words[wi].start - 1e-3 <= c.start <= words[wi].end + 1e-3
        k += sum(len(line.split(" ")) for line in c.text.split("\n"))


@pytest.mark.parametrize("lang", sorted(NARROW_SENTENCES))
def test_narrow_captions_split_long_words_between_clusters_and_keep_word_timing(lang: str) -> None:
    from aadhi.compose.captions import split_caption

    text = NARROW_SENTENCES[lang]
    words = _one_to_one_words(text)
    width = pres.PORTRAIT_CAPTION_LINE_CHARS
    assert max(len(w) for w in text.split()) > width  # the sentence needs a split
    _check_cluster_safe_cues(text, split_caption(text, words, width=width, clusters=True), words, width)
    _check_cluster_safe_cues(text, pres.portrait_caption_cues(text, words, start=1.0, speech_end=words[-1].end,
                                                              hold_end=words[-1].end + 0.4), words, width)
    if lang == "en":
        cues = split_caption(text, words, width=width, clusters=True)
        lines = [line for c in cues for line in c.text.split("\n")]
        assert any(line.endswith("temperature-") for line in lines), lines  # cut after the hyphen


def test_classic_tokenizer_and_splitter_are_unchanged() -> None:
    from aadhi.compose.captions import split_caption, tokenize

    text = NARROW_SENTENCES["ta"]
    words = _one_to_one_words(text)
    assert tokenize(text, 16)[5] == "கண்டுபிடிக்கப்பட"  # the classic hard split (its default width never splits it)
    assert split_caption(text, words) == split_caption(text, words, clusters=False)
    assert [c.text for c in split_caption(text, words)] == [c.text for c in split_caption(text, words, width=42)]


def _showcase_portrait() -> Timeline:
    """The B1 acceptance showcase as a Dynamic 9:16 timeline (its synthetic narration timings, as the acceptance run)."""
    from tests.invariance import classic_fixture as fx

    sp = fx.load_screenplay("showcase_ohms_law")
    data = sp.model_dump(mode="json")
    data["presentation"] = presentation("portrait", look_seed=20261010)
    sp = Screenplay.model_validate(data)
    return build_timeline(sp, fx.synthetic_manifest(sp, fixture="showcase_ohms_law"),
                          settings=Settings(_env_file=None, dynamic_enabled=True, render_fps=30,
                                            dynamic_aadhi_intro=False))


def test_the_showcase_reel_never_plans_a_phrase_too_long_for_media_split() -> None:
    """B2 review F3: ``media_split``'s 9:16 phrase is two lines, and the director planned it for s3 (a 9-word bullet)
    and s13 (up to 22 words). The page draws them anyway and reports the excess, so Dynamic preflight **blocked**
    both 9:16 builds of the showcase (``text_overflow`` on the ``body`` slot) and no real portrait render of this
    lecture was possible. Those scenes are board_generic's now, which sets a board at its own sizes."""
    from aadhi.compose.dynamic.templates import MEDIA_SPLIT_PHRASE_WORDS, TEXT_KINDS, _phrase_text, _words

    tl = _showcase_portrait()
    planned = [s for s in tl.scenes if s.look is not None and s.look.template == "media_split"]
    for scene in planned:
        for item in scene.board or []:
            if str(getattr(item.kind, "value", item.kind)) in TEXT_KINDS:
                assert _words(_phrase_text(item)) <= MEDIA_SPLIT_PHRASE_WORDS, (scene.scene_id, item.id, item.text)
    # the two scenes the cap moved, named so a later layout change that frees them is noticed here
    moved = {s.scene_id: s.look.template for s in tl.scenes
             if s.scene_id in ("s3", "s13") and s.look is not None}
    assert moved == {"s3": "board_generic", "s13": "board_generic"}


def test_portrait_captions_never_flash_by() -> None:
    """R-32: a 9:16 chunk stays 1.5-3 s, 1.0 s at the very least; the greedy splitter left 42 of 172 chunks of the
    showcase under 1.0 s, the shortest a 0.31 s one-word orphan. What remains short are beats too fast for any
    grouping at these TTS rates (B2's kinetic.py merges across beats)."""
    tl = _showcase_portrait()
    cues = [(b, c) for s in tl.scenes for b in s.beats for c in b.captions]
    durations = [c.end - c.start for _, c in cues]
    assert len(cues) > 150
    assert min(durations) >= 0.6, min(durations)
    assert sum(d < 0.7 for d in durations) <= 3 and sum(d < 1.0 for d in durations) <= 12
    for beat in {id(b): b for b, _ in cues}.values():
        for c in beat.captions:
            lines = c.text.split("\n")
            assert len(lines) <= MAX_LINES and all(len(line) <= pres.PORTRAIT_CAPTION_LINE_CHARS for line in lines)
            assert len(c.text.replace("\n", " ")) <= 2 * pres.PORTRAIT_CAPTION_LINE_CHARS + 1
        if len(beat.captions) >= 2:
            last = beat.captions[-1]
            orphan = " " not in last.text.replace("\n", " ") and last.end - last.start < 1.0
            assert not orphan, (beat.beat_id, last.text, last.end - last.start)


def test_portrait_caption_grouping_shares_a_fast_beat_instead_of_leaving_an_orphan(flag_on: Settings) -> None:
    from aadhi.compose.captions import split_caption

    text = "Welcome to this session on Ohm's law."  # 38 characters spoken in 1 s: two chunks are needed
    words = _one_to_one_words(text, start=0.5, step=1 / 7)
    greedy = split_caption(text, words, width=pres.PORTRAIT_CAPTION_LINE_CHARS)
    assert greedy[-1].text == "law." and greedy[-1].end - greedy[-1].start < 0.2  # the classic grouping's orphan
    cues = pres.portrait_caption_cues(text, words, start=0.5, speech_end=words[-1].end, hold_end=words[-1].end)
    assert len(cues) == 2 and all(len(c.text.split()) >= 3 for c in cues), cues
    assert min(c.end - c.start for c in cues) > 0.4
    roomy = _one_to_one_words("Now YOU are the engineer! Which of these is yours?", step=0.3)
    text2 = "Now YOU are the engineer! Which of these is yours?"
    cues2 = pres.portrait_caption_cues(text2, roomy, start=1.0, speech_end=roomy[-1].end, hold_end=roomy[-1].end + 0.4)
    assert all(c.end - c.start >= pres.PORTRAIT_CAPTION_MIN_SECONDS - 1e-6 for c in cues2), cues2
    tl = build(dynamic_screenplay("portrait"), flag_on)
    for scene in tl.scenes:  # the factory's fake TTS (1 s per beat): every chunk keeps at least two words or the line
        for beat in scene.beats:
            if len(beat.captions) >= 2:
                assert all(len(c.text.split()) >= 2 for c in beat.captions), [c.text for c in beat.captions]


# --- B2a: look-engine generation 2 (plan B2 §1.2, §1.3, §1.10; R-52, R-54) ------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
B1_LOOKS = json.loads((Path(__file__).parent / "fixtures" / "b1_looks.json").read_text("utf-8"))
B1_TOKEN_KEYS = ("struct.accent", "bg", "bg-alt", "bg-tint", "surface")
# the presentation fields after B1: B2a's five and B2c's text_entrance
B2A_PRESENTATION_KEYS = ("content_alt", "family", "mode", "motion_scale", "entity_colors", "text_entrance")
# what the demo phase's director decides, so the B1 golden no longer pins it (checked against the plan instead)
DIRECTED = frozenset({"template", "variant"})


def golden_build(cfg: dict[str, Any], settings: Settings) -> dict[str, Any]:
    sp = dynamic_screenplay(cfg["format"], chapters=cfg["chapters"], looks=cfg.get("looks"), **cfg["presentation"])
    return build(sp, settings).model_dump(mode="json")


@pytest.mark.parametrize("cfg", B1_LOOKS["configs"], ids=[c["name"] for c in B1_LOOKS["configs"]])
def test_b1_choices_are_kept(flag_on: Settings, cfg: dict[str, Any]) -> None:
    """Against what the B1 resolver itself resolved (``fixtures/b1_looks.json``, captured from commit 54c72c5): the
    five B1 tokens (value and order), background preset, tone and seed, transition and hold of every scene, and every
    B1 presentation value, are unchanged; what B2a adds is only added.

    Two deliberate changes:

    * a lecture without ``Screenplay.chapters`` (``no_screenplay_chapters``) changes accent and tone where its chapter
      list (chapter cards) changes chapter, which B1 ignored (compose-classic bug 4);
    * the demo phase's director (``dynamic.direct``, engine generation 3; step 4's four templates and the side panel
      of worked_steps / key_terms make it generation 4) decides the ``template`` and ``variant`` of
      every scene instead of naming ``board_generic`` everywhere, and a dense template it picks freezes the
      background from the scene start (R-55). Those three fields are checked against the director's own plan here;
      everything else still has to match B1 exactly."""
    tl = golden_build(cfg, flag_on)
    assert tl["chapters"] == cfg["chapters_list"]
    old_p, new_p = cfg["timeline_presentation"], tl["presentation"]
    # B2b: fonts in the B2 form (with ``indic``), naming the faces the B1 stage drew for B1's fonts (faces.py)
    assert new_p["fonts"] == {**old_p["fonts"], "indic": "noto_sans"}
    assert fc.stage_roles(new_p["fonts"]) == fc.stage_roles(old_p["fonts"])
    assert {k: v for k, v in new_p.items() if k not in B2A_PRESENTATION_KEYS} == {
        **old_p, "engine_version": pres.ENGINE_VERSION, "fonts": new_p["fonts"]}
    assert list(new_p)[:len(old_p)] == list(old_p), "B1's keys keep their order"
    assert [s["scene_id"] for s in tl["scenes"]] == [s["scene_id"] for s in cfg["scenes"]]
    family = pres.family_for(cfg["presentation"].get("theme_family", "midnight_indigo"))
    timed = Timeline.model_validate(tl)
    numbers = pres.chapter_indices(timed.scenes, timed.chapters)
    plans = {p.scene_id: p for p in direct.plan(
        dynamic_screenplay(cfg["format"], chapters=cfg["chapters"], looks=cfg.get("looks"), **cfg["presentation"]),
        timed.scenes, cfg["format"], timed.chapters)}
    for i, (old, new) in enumerate(zip(cfg["scenes"], tl["scenes"])):
        assert (new["start"], new["duration"]) == (old["start"], old["duration"])
        b1, look = old["look"], new["look"]
        assert set(look) - DIRECTED <= set(b1) and set(b1) - DIRECTED <= set(look), \
            "B2a's look fields stay out of the JSON at their defaults here"
        assert list(look["tokens"]) == [*B1_TOKEN_KEYS, *th.DERIVED_KEYS]
        assert look["tokens"] == th.scene_tokens(family, numbers[i])
        # the director's, not B1's (engine 3): the template, its variant and a dense template's frozen background
        decided = plans[new["scene_id"]]
        assert (look["template"], look.get("variant")) == (decided.template, decided.variant)
        dense = decided.template in pres.DENSE_TEMPLATES or look.get("hold_reason") is not None
        assert look["background"]["frozen_at"] == (0.0 if dense else None)
        if cfg["name"] == "no_screenplay_chapters":
            continue
        assert {k: look["tokens"][k] for k in B1_TOKEN_KEYS} == b1["tokens"], (cfg["name"], old["scene_id"])
        assert {k: v for k, v in look.items() if k not in DIRECTED and k != "tokens"} == {
            k: v for k, v in b1.items() if k not in DIRECTED and k != "tokens"}
        assert {k: v for k, v in look["background"].items() if k != "frozen_at"} == {
            k: v for k, v in b1["background"].items() if k != "frozen_at"}
    if cfg["name"] == "no_screenplay_chapters":
        # B1: one accent for the whole lecture, though its chapter list (and kicker) changes at the chapter card
        assert len({s["look"]["tokens"]["struct.accent"] for s in cfg["scenes"]}) == 1 and len(tl["chapters"]) == 2
        card = next(i for i, s in enumerate(tl["scenes"]) if s["type"] == "chapter_card")
        assert numbers == [0] * card + [1] * (len(numbers) - card)
        assert [s["look"]["background"]["params"]["tone"] for s in tl["scenes"]][card:card + 4] == [
            "bg", "bg-alt", "bg-tint", "bg"], "the tone steps restart with the chapter (§4.5)"
        for old, new in zip(cfg["scenes"], tl["scenes"]):  # nothing else changed
            keep = ("transition_in", "hold_reason")
            assert {k: new["look"][k] for k in keep} == {k: old["look"][k] for k in keep}
            assert new["look"]["background"]["seed"] == old["look"]["background"]["seed"]


@pytest.mark.parametrize("display", ["outfit", "inter", "poppins", "lexend", "montserrat", "jetbrains_mono"])
def test_fonts_name_the_faces_the_b1_stage_drew(flag_on: Settings, display: str) -> None:
    """B2b (plan §1.3): ``presentation.fonts`` is the B2 form (``indic`` set), naming exactly the faces the B1 stage drew
    for B1's ``{display: display_font, body, mono}`` (a display id the B1 stage did not draw is stored as the Outfit it
    drew instead), so no look changes; the stage reads the B2 form through the face table (web/js/dynamic/fonts.js)."""
    tl = build(dynamic_screenplay(display_font=display), flag_on)
    assert tl.presentation is not None
    fonts = tl.presentation.fonts.model_dump(mode="json")
    b1 = {"display": display, "body": "inter", "mono": "jetbrains_mono"}  # what the B1 resolver stored
    assert fonts == fc.resolver_fonts(display) and fc.is_b2_fonts(fonts) and not fc.is_b2_fonts(b1)
    assert fonts["display"] == ("inter" if display == "inter" else "outfit")
    for role in ("display", "body", "mono"):
        for lang in ("en-IN", "ta-IN", "hi-IN"):
            assert fc.css_stack(fonts, role, lang) == fc.css_stack(b1, role, lang)


@pytest.mark.parametrize("family", ["midnight_indigo", "deep_teal", "paper_ink", "mint_studio"])
def test_looks_store_every_structure_colour(flag_on: Settings, family: str) -> None:
    """``look.tokens`` = ``themes.scene_tokens(family, chapter)``: B1's anchors first (pinned in test_themes), then the
    colours the stage derived at run time (plan §1.2)."""
    tl = build(dynamic_screenplay(theme_family=family), flag_on)
    numbers = pres.chapter_indices(tl.scenes, tl.chapters)
    assert numbers == [0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
    fam = th.FAMILIES[family]
    for s, k in zip(tl.scenes, numbers):
        assert list(s.look.tokens.items()) == list(th.scene_tokens(fam, k).items())
        assert {key: s.look.tokens[key] for key in B1_TOKEN_KEYS} == pres.structure_tokens(fam, pres.accent_hue(fam, k))
    if family == "paper_ink":  # R-54: the block of palette slot 2 is red-lowered in the stored tokens
        assert tl.scenes[6].look.tokens["block.base"] == "#B05641" != tl.scenes[6].look.tokens["struct.accent"]


def test_chapter_indices_follow_the_kicker_rule() -> None:
    def scene(start: float) -> Any:
        return SimpleNamespace(start=start)

    chapters = [Chapter(start=0.0, title="a"), Chapter(start=6.3, title="b"), Chapter(start=24.05, title="c")]
    starts = [0.0, 2.3, 6.2989, 6.2991, 6.3, 23.0, 24.05, 30.0]
    assert pres.chapter_indices([scene(s) for s in starts], chapters) == [0, 0, 0, 1, 1, 1, 2, 2]  # 1 ms tolerance
    assert pres.chapter_indices([scene(1.0)], []) == [0]
    assert pres.chapter_indices([scene(1.0)], [Chapter(start=5.0, title="late")]) == [0]


def test_chapters_from_concepts_drive_the_looks(flag_on: Settings) -> None:
    """No screenplay chapters and no chapter card: build_chapters falls back to concept changes, and so do the looks
    (B1 kept one accent; compose-classic bug 4)."""
    data = dynamic_screenplay(chapters=False).model_dump(mode="json")
    next(s for s in data["scenes"] if s["type"] == "chapter_card")["hidden"] = True
    tl = build(Screenplay.model_validate(data), flag_on)
    assert [c.title for c in tl.chapters] == ["Charge and Current", "Ohm's Law"]
    board = next(i for i, s in enumerate(tl.scenes) if s.scene_id == "s-board")
    assert tl.chapters[1].start == tl.scenes[board].start
    accents = [s.look.tokens["struct.accent"] for s in tl.scenes]
    assert accents[:board] == [accents[0]] * board and accents[board:] == [accents[board]] * (len(accents) - board)
    assert accents[0] != accents[board]
    assert tl.scenes[board].look.transition_in == {"kind": "crossfade", "seconds": 0.45}, "a new chapter fades"
    assert tl.scenes[board].look.background["params"]["tone"] == "bg"
    assert tl.scenes[board + 1].look.transition_in["kind"] == "cut"


def test_the_palette_slot_sets_the_accent(flag_on: Settings) -> None:
    """SceneLook.palette_slot (plan §1.10, written by direct.py) picks the scene's colours; without it the chapter
    number does. A change of accent inside a chapter cross-fades like a chapter boundary unless the teacher asked for a
    transition; the tone sequence stays per chapter."""
    looks = {"s-sim": {"palette_slot": 0}, "s-video": {"palette_slot": 4},
             "s-quiz": {"palette_slot": 0, "transition_in": "cut"}}
    tl = build(dynamic_screenplay(looks=looks), flag_on)
    fam = th.FAMILIES["midnight_indigo"]
    by = {s.scene_id: s.look for s in tl.scenes}
    assert by["s-sim"].tokens == th.scene_tokens(fam, 0) != th.scene_tokens(fam, 1)
    assert by["s-sim-fallback"].tokens == th.scene_tokens(fam, 1), "no slot: the chapter number"
    assert by["s-video"].tokens == th.scene_tokens(fam, 4) == th.scene_tokens(fam, 1)  # the accent period is 3
    assert by["s-sim"].transition_in == {"kind": "crossfade", "seconds": 0.45}  # the accent changes mid-chapter
    assert by["s-sim-fallback"].transition_in == {"kind": "crossfade", "seconds": 0.45}  # ... and back
    assert by["s-video"].transition_in == {"kind": "cut", "seconds": 0.0}  # same accent: a hard cut (R-23)
    assert by["s-quiz"].tokens == th.scene_tokens(fam, 0) and by["s-quiz"].transition_in["kind"] == "cut"
    assert [by[k].background["params"]["tone"] for k in ("s-board", "s-sim", "s-sim-fallback", "s-video")] == [
        "bg", "bg-alt", "bg-tint", "bg"]
    plain = build(dynamic_screenplay(), flag_on)
    assert [s.look.background for s in plain.scenes] == [s.look.background for s in tl.scenes]


def test_continues_from_is_kept_for_the_scene_just_before(flag_on: Settings) -> None:
    looks = {"s-sim": {"continues_from": "s-board"}, "s-video": {"continues_from": "s-board"},
             "s-title": {"continues_from": "s-quiz"}}
    tl = build(dynamic_screenplay(looks=looks), flag_on)
    by = {s.scene_id: s.look for s in tl.scenes}
    assert by["s-sim"].continues_from == "s-board"
    assert by["s-video"].continues_from is None and by["s-title"].continues_from is None  # not the scene before
    dumped = tl.model_dump(mode="json")["scenes"]
    assert dumped[3]["look"]["continues_from"] == "s-board" and "continues_from" not in dumped[5]["look"]


def _stage_crossfade(seconds: float, duration: float) -> float:
    """web/js/dynamic/transitions.js transitionOf for a crossfade: [0.3, 0.5], at most the scene; 0 = a cut."""
    asked = seconds if seconds > 0 else 0.45
    d = min(max(asked, 0.3), 0.5, max(0.0, duration))
    return d if d > 0 else 0.0


def test_crossfades_last_at_least_300_ms() -> None:
    """R-54 (and §4.9's 300-500 ms): the stored seconds are what the stage draws, which is what it drew from B1's
    value, so nothing on screen changes; a scene too short for 300 ms gets a cut."""
    js = (ROOT / "web" / "js" / "dynamic" / "transitions.js").read_text("utf-8")
    consts = dict(re.findall(r"export const (CROSSFADE_\w+) = ([0-9.]+);", js))
    assert (float(consts["CROSSFADE_MIN"]), float(consts["CROSSFADE_MAX"]), float(consts["CROSSFADE_DEFAULT"])) == (
        pres.CROSSFADE_MIN_SECONDS, pres.CROSSFADE_MAX_SECONDS, pres.CROSSFADE_SECONDS)
    assert [pres.crossfade_seconds(d) for d in (0.3, 0.5, 0.599, 0.6, 0.7, 0.9, 30.0)] == [
        0.3, 0.3, 0.3, 0.3, 0.35, 0.45, 0.45]
    for d in (0.0, 0.1, 0.299, -1.0, float("nan")):
        assert pres.crossfade_seconds(d) is None, d
    for k in range(30, 400):
        d = k / 100
        b1 = round(min(pres.CROSSFADE_SECONDS, d / 2), 3)  # B1: half the scene, even under 300 ms
        new = pres.crossfade_seconds(d)
        assert new is not None and pres.CROSSFADE_MIN_SECONDS <= new <= min(pres.CROSSFADE_MAX_SECONDS, d)
        assert _stage_crossfade(new, d) == pytest.approx(new) == pytest.approx(_stage_crossfade(b1, d)), d


def test_a_scene_too_short_for_a_fade_gets_a_cut(flag_on: Settings) -> None:
    from aadhi.compose.base import SCENE_TRANSITION_SECONDS

    sp = dynamic_screenplay()
    tl = build(sp, flag_on)
    short = {"s-chapter": 0.5, "s-board": 0.55, "s-video-none": 0.25}
    scenes = [s.model_copy(update={"duration": short.get(s.scene_id, s.duration)}) for s in tl.scenes]
    looks = pres.resolve_looks(sp, scenes, tl.chapters)
    by = {s.scene_id: lk for s, lk in zip(scenes, looks)}
    assert by["s-chapter"].transition_in == {"kind": "crossfade", "seconds": 0.3}  # into the colour block: B1 0.25
    assert by["s-board"].transition_in == {"kind": "crossfade", "seconds": 0.3}  # out of it
    assert by["s-video-none"].transition_in == {"kind": "cut", "seconds": 0.0}  # a new chapter, 0.25 s long
    assert pres.resolve_looks(sp, scenes) == looks, "the chapters default to build_chapters'"
    # unreachable in real timelines: every scene build_timeline makes holds a 300 ms fade
    assert SCENE_TRANSITION_SECONDS + 0.1 >= pres.CROSSFADE_MIN_SECONDS
    assert min(s.duration for s in tl.scenes) >= SCENE_TRANSITION_SECONDS + 0.1 - 1e-9


@pytest.mark.parametrize(("family", "personality", "stage", "scale"), [
    ("midnight_indigo", "crisp", "crisp", 0.85), ("midnight_indigo", "soft", "soft", 1.15),
    ("midnight_indigo", "cinematic", "soft", 1.15), ("paper_ink", "editorial", "crisp", 0.85),
    ("deep_teal", "no_such_one", "crisp", 0.85), ("mint_studio", "bouncy_soft", "soft", 1.15)])
def test_motion_scale_follows_the_personality_the_stage_draws(flag_on: Settings, family: str, personality: str,
                                                              stage: str, scale: float) -> None:
    """§4.8: the duration scale of the personality; an unreleased (or unknown) one is drawn as the family's R1
    default (§4.7 table: "Cinematic -> Soft in R1", "Editorial -> Crisp in R1"), and scaled as it."""
    fam = th.FAMILIES[family]
    assert pres.stage_personality(personality, fam) == stage
    assert pres.motion_scale(personality, fam) == scale == th.PERSONALITIES[stage].duration_scale
    p = build(dynamic_screenplay(theme_family=family, personality=personality), flag_on).presentation
    assert p.personality == personality and p.motion_scale == scale  # the requested id is kept, as in B1
    # B2c: the text entrance of the same personality (§4.8: Crisp revealed from the baseline, Soft fade + rise)
    assert p.text_entrance == pres.text_entrance(personality, fam) == th.PERSONALITIES[stage].text_entrances[0]
    assert p.text_entrance == {"crisp": "baseline_reveal", "soft": "fade_rise"}[stage]


def test_presentation_carries_the_other_mode_family_and_entities(flag_on: Settings) -> None:
    for family, mode in (("midnight_indigo", "dark"), ("deep_teal", "dark"), ("paper_ink", "light"),
                         ("mint_studio", "light")):
        p = build(dynamic_screenplay(theme_family=family), flag_on).presentation
        assert (p.family, p.mode) == (family, mode)
        assert p.content == th.content_tokens(mode) and p.content_alt == th.content_alt_tokens(mode)
        assert p.content_alt == th.content_tokens("light" if mode == "dark" else "dark")
    mapped = build(dynamic_screenplay(theme_family="teal_coral"), flag_on).presentation
    assert mapped.family == pres.family_for("teal_coral").id and mapped.family in th.RELEASED_FAMILIES
    none = build(dynamic_screenplay(entity_colors={}), flag_on)
    assert none.presentation.entity_colors == {}
    assert "entity_colors" not in none.model_dump(mode="json")["presentation"]


def test_the_resolver_draws_only_what_the_stage_implements() -> None:
    from aadhi.compose.dynamic import templates

    # the resolver draws exactly the implemented R1 templates; direct.candidate is the gate
    assert set(pres.TEMPLATES) == {t for t, s in templates.TEMPLATES.items() if s.implemented and s.release == "R1"}
    assert set(pres.TEMPLATES) <= set(templates.IMPLEMENTED)
    assert pres.DEFAULT_TEMPLATE == templates.FALLBACK_TEMPLATE
    assert pres.DENSE_TEMPLATES == tuple(k for k, s in templates.TEMPLATES.items() if s.dense)
    assert pres.BACKGROUNDS == tuple(k for k, b in templates.BACKGROUNDS.items() if b.implemented)
    assert pres.TRANSITIONS == tuple(k for k, t in templates.TRANSITIONS.items() if t.implemented)
    assert templates.TRANSITIONS["crossfade"].seconds == (pres.CROSSFADE_MIN_SECONDS, pres.CROSSFADE_MAX_SECONDS)
    assert pres.TONES == th.TONES


def test_a_b1_timeline_reads_and_writes_back_byte_identically(flag_on: Settings) -> None:
    """A timeline the B1 resolver built (engine_version 1: five tokens, no B2a field) is unchanged by the B2a schema:
    every new field is left out of the JSON at its default."""
    from aadhi.schemas.timeline import ResolvedLook, TimelinePresentation

    data = build(dynamic_screenplay("portrait"), flag_on).model_dump(mode="json")
    data["presentation"] = {k: v for k, v in data["presentation"].items() if k not in B2A_PRESENTATION_KEYS}
    data["presentation"]["engine_version"] = 1
    for s in data["scenes"]:
        s["look"]["tokens"] = {k: s["look"]["tokens"][k] for k in B1_TOKEN_KEYS}
    text = json.dumps(data, ensure_ascii=False)
    assert json.dumps(Timeline.model_validate(json.loads(text)).model_dump(mode="json"), ensure_ascii=False) == text
    for model, names in ((TimelinePresentation, B2A_PRESENTATION_KEYS),
                         (ResolvedLook, ("fixes", "icons", "refs", "accent_fade", "continues_from"))):
        for name in names:
            info = model.model_fields[name]
            assert info.exclude_if is not None and info.exclude_if(info.get_default(call_default_factory=True)), name
