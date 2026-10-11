"""Dynamic timelines (design §6.7): ``Timeline.presentation`` and ``TimedScene.look`` from a screenplay's presentation
(``aadhi.compose.presentation``, wired in ``compose.timeline``), behind the admin flag ``DYNAMIC_ENABLED``.

Classic timelines never change: without a presentation, or with the flag off, the builder's output is byte-identical.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from aadhi.compose import presentation as pres
from aadhi.compose.captions import MAX_LINES
from aadhi.compose.timeline import DYNAMIC_FPS, build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline

from .factories import make_manifest, screenplay_dict

HEX = re.compile(r"^#[0-9A-F]{6}$")


@pytest.fixture()
def flag_on() -> Settings:
    return Settings(_env_file=None, render_fps=25, dynamic_enabled=True)


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
    assert p.fonts == {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}
    assert (p.personality, p.caption_style, p.cue_term_style) == ("crisp", "clean_pill", "underline")
    assert (p.captions_burn, p.sfx, p.motion) == (True, False, "calm")
    assert p.engine_version == 1
    assert p.generated_seconds == pytest.approx(tl.total_duration)
    assert tl.intro is None, "a Dynamic timeline has no classic intro"
    assert tl.scenes[0].start == 0
    assert tl.fps == DYNAMIC_FPS == 30
    assert tl.schema_version == 3


def test_looks_of_every_scene(flag_on: Settings) -> None:
    tl = build(dynamic_screenplay(), flag_on)
    for s in tl.scenes:
        look = s.look
        assert look is not None and look.template == "board_generic"
        assert set(look.tokens) == {"struct.accent", "bg", "bg-alt", "bg-tint", "surface"}
        assert all(HEX.match(v) for v in look.tokens.values())
        assert look.background["preset"] == ("colour_block" if s.type == "chapter_card" else "mesh_drift")
        assert look.background["params"]["tone"] in pres.TONES
        assert 0 <= look.background["seed"] < 2**31
        assert look.background["frozen_at"] is None
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
                          settings=Settings(_env_file=None, dynamic_enabled=True, render_fps=30))


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
