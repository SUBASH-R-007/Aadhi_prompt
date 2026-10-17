"""The default ``Screenplay.presentation`` of a newly generated lecture (compose/dynamic/authoring.py).

This is the seam that decides whether a generated lecture is a Dynamic video at all: ``build_timeline`` makes a
Dynamic timeline only for a screenplay that has a presentation, and only while ``DYNAMIC_ENABLED`` is on. The tests
pin both halves, that every default is a value the resolver really has, and that the look seed is a function of the
lecture rather than of the process.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from aadhi.compose.dynamic import catalogue as cat
from aadhi.compose.dynamic import faces, themes
from aadhi.compose.dynamic.authoring import (
    DEFAULTS,
    POOLS,
    default_presentation,
    look_seed_for,
    narration_choice,
    narrator_for,
    pick,
    rotated,
)
from aadhi.compose.dynamic.templates import TEMPLATES
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.pipeline.base import GenerationOptions, IngestResult
from aadhi.pipeline.plan import LecturePlan, PlannedChapter, PlannedScene
from aadhi.pipeline.script import assemble_screenplay
from aadhi.schemas.screenplay import Screenplay
from tests.compose.factories import make_manifest, screenplay_dict

ON = Settings(_env_file=None, dynamic_enabled=True)
OFF = Settings(_env_file=None)


def _plan(title: str = "Ohm's Law") -> LecturePlan:
    return LecturePlan(session_title=title, subject_name="Basic Electrical Engineering",
                       unit_name="Unit 2: DC Circuits", session_number="Session 3",
                       chapters=[PlannedChapter(id="part-1", title="Ohm's law", scenes=[
                           PlannedScene(id="voltage", type="content", goal="Explain voltage.",
                                        narrative_role="concept")])])


def _assembled(settings: Any | None) -> Screenplay:
    """A screenplay as ``write_scenes_detailed`` assembles it, with no scenes of its own to pay for."""
    return assemble_screenplay(_plan(), [], IngestResult(markdown="# Ohm"), GenerationOptions(), [], None,
                               settings=settings)


def test_every_value_a_lecture_can_get_is_one_the_resolver_really_has() -> None:
    """A renamed family, caption style or hook kind must fail here, not silently fall back in every lecture.

    Every pool value is checked, not just one default: the rotation can hand any of them to a real lecture.
    """
    catalogue = cat.catalogue()
    assert DEFAULTS["display_font"] in faces.DISPLAY_FACES_R1
    for family in POOLS["theme_family"]:
        assert family in themes.FAMILIES, family
        assert themes.FAMILIES[family].release == "R1", family
    assert {themes.FAMILIES[f].mode for f in POOLS["theme_family"]} == {"dark", "light"}, "rotate both modes"
    for personality in POOLS["personality"]:
        assert themes.PERSONALITIES[personality].release == "R1", personality
    for fmt in ("landscape", "portrait"):
        for hook in POOLS["hook_kind"]:
            assert hook in TEMPLATES["hook_question"].variants[fmt], (fmt, hook)
        for personality in POOLS["personality"]:
            assert themes.PERSONALITIES[personality].caption_style[fmt] in catalogue["captions"]
    # 'mystery' is deferred to B4 (deviation 16): rotating onto it would fall back to another variant.
    assert "mystery" not in POOLS["hook_kind"]
    pres = default_presentation(subject_name="Physics", narrator_voice="en-IN-NeerjaNeural")
    assert pres.style == "dynamic" and pres.format == "landscape"
    assert pres.asset_style.family == pres.theme_family and pres.asset_style.personality == pres.personality
    assert pres.music.source == "off"  # B3 owns the music plan
    assert pres.display_font == DEFAULTS["display_font"]  # pinned until the resolver rotates faces


def _looks(n: int = 240, epoch: int = 0) -> list[dict[str, str]]:
    return [rotated(look_seed_for("S", "U", str(i), f"T{i}"), epoch) for i in range(n)]


def test_the_look_rotates_so_two_lectures_do_not_look_alike() -> None:
    """Owner decision (2026-10-15): the family, the personality and the hook kind rotate on the look seed."""
    looks = _looks()
    for field in ("theme_family", "personality", "hook_kind"):
        got = {look[field] for look in looks}
        assert got == set(POOLS[field]), f"{field} never reached {set(POOLS[field]) - got}"
    # Each pool draws from its own stream, so they do not move together: every family occurs with both
    # personalities (if one stream drove both, half of these pairs would be missing).
    pairs = {(look["theme_family"], look["personality"]) for look in looks}
    assert len(pairs) == len(POOLS["theme_family"]) * len(POOLS["personality"]), sorted(pairs)


def test_the_rotation_is_stable_per_lecture_and_re_rolls_only_on_a_new_epoch() -> None:
    """A rebuild must never re-roll (§6.8); Fresh look / New colours bump ``look_epoch`` and must."""
    seed = look_seed_for("Basic Electrical Engineering", "Unit 2: DC Circuits", "Session 3", "Ohm's Law")
    assert [rotated(seed) for _ in range(3)] == [rotated(seed)] * 3
    assert pick("theme_family", seed) == pick("theme_family", seed)
    changed = [i for i in range(240) if _looks(240, 0)[i] != _looks(240, 1)[i]]
    assert changed, "no lecture's look changed when the epoch was bumped"


def test_the_caption_and_cue_styles_are_the_ones_the_personality_is_paired_with() -> None:
    """They are derived, not rolled: the design pairs them with the personality (themes.Personality)."""
    for fmt in ("landscape", "portrait"):
        for look in _looks(60, 0):
            row = themes.PERSONALITIES[look["personality"]]
            derived = rotated(look_seed_for("S", "U", "n", "t"), 0, fmt)
            paired = themes.PERSONALITIES[derived["personality"]]
            assert derived["caption_style"] == paired.caption_style[fmt]
            assert derived["cue_term_style"] == paired.cue_term_style
            assert row.release == "R1"
    # The pairing the old hardcoded defaults got wrong: a 9:16 Crisp lecture takes bold_outline, not clean_pill.
    assert themes.PERSONALITIES["crisp"].caption_style["portrait"] == "bold_outline"


def test_an_override_wins_and_the_asset_style_follows_it() -> None:
    """The demo renders a named family; the asset style must not be left on the rolled one."""
    pres = default_presentation(subject_name="S", unit_name="U", session_number="1", session_title="T",
                                theme_family="paper_ink", personality="soft")
    assert pres.theme_family == "paper_ink" and pres.personality == "soft"
    assert pres.asset_style.family == "paper_ink" and pres.asset_style.personality == "soft"


def test_the_look_seed_is_a_function_of_the_lecture_not_of_the_process() -> None:
    """SHA-256, never ``hash()``: the same lecture gets the same look in every worker and on every run.

    The constant is the point of the test — ``hash()`` is salted per process, so it could not satisfy it twice.
    """
    assert look_seed_for("Basic Electrical Engineering", "Unit 2: DC Circuits", "Session 3", "Ohm's Law") == 361366766
    same = [default_presentation(subject_name="S", unit_name="U", session_number="1", session_title="T").look_seed
            for _ in range(3)]
    assert len(set(same)) == 1
    other = default_presentation(subject_name="S", unit_name="U", session_number="1", session_title="Other").look_seed
    assert other != same[0]
    assert 0 <= same[0] < 2_000_000_000


def test_the_narrator_is_the_teachers_voice_else_the_languages_default() -> None:
    assert narrator_for("en-IN-PrabhatNeural", "edge", "en") == "en-IN-PrabhatNeural"
    assert narrator_for(" ", "edge", "ta") == "ta-IN-PallaviNeural"
    assert narrator_for(None, "edge", "hi") == "hi-IN-SwaraNeural"
    assert narrator_for(None, "gemini", "en") == "Kore"
    # A default presentation may never be the reason a generation fails.
    assert narrator_for(None, "no-such-provider", "en") == "en-IN-NeerjaNeural"


def test_the_narrator_follows_the_voice_the_build_will_use() -> None:
    """OpenAI TTS is the default voice: with an OpenAI key (the job's settings carry the owner's personal key) the
    narrator is OpenAI's voice for the language; without one the build narrates with the free Edge voice, and so does
    the narrator. An explicit choice of a configured provider wins, as in the asset build."""
    keyless = ON.model_copy(update={"tts_provider": "openai", "openai_api_key": SecretStr(""),
                                    "gemini_api_key": SecretStr(""), "gemini_api_keys": []})
    keyed = keyless.model_copy(update={"openai_api_key": SecretStr("sk-test-owner-key")})
    assert narration_choice(None, None, keyed) == (None, "openai")
    assert narration_choice("onyx", None, keyed) == ("onyx", "openai")
    assert narration_choice(None, None, keyless) == (None, "edge")
    assert narration_choice("onyx", "openai", keyless) == (None, "edge")  # never OpenAI's voice cast for Edge
    assert narration_choice("en-IN-PrabhatNeural", None, keyless) == ("en-IN-PrabhatNeural", "edge")
    # an Edge voice saved on "Server default" is never cast for OpenAI once the job has an OpenAI key
    assert narration_choice("en-IN-PrabhatNeural", None, keyed) == (None, "openai")
    assert narration_choice("nova", "edge", keyed) == (None, "edge")  # an OpenAI voice on a lecture that chose Edge
    assert narration_choice("en-IN-PrabhatNeural", "edge", keyed) == ("en-IN-PrabhatNeural", "edge")
    assert narration_choice("Puck", "gemini", keyless.model_copy(update={"gemini_api_key": SecretStr("k")})) == (
        "Puck", "gemini")

    def narrator(settings: Settings, **options: Any) -> str:
        plan = _plan()
        return assemble_screenplay(plan, [], IngestResult(markdown="# Ohm"), GenerationOptions(**options), [], None,
                                   settings=settings).presentation.voices.narrator

    assert narrator(keyed) == "nova"
    assert narrator(keyed, language="en-GB") == "nova"
    assert narrator(keyed, tts_voice="en-IN-PrabhatNeural") == "nova"
    assert narrator(keyless) == "en-IN-NeerjaNeural"
    assert narrator(keyless, language="ta-IN") == "ta-IN-PallaviNeural"
    assert narrator(keyless, tts_provider="openai", tts_voice="onyx") == "en-IN-NeerjaNeural"


def test_a_generated_lecture_is_dynamic_only_when_the_flag_is_on() -> None:
    """With the flag off the screenplay is classic, exactly as before this slice (the invariance goldens)."""
    assert _assembled(None).presentation is None
    assert _assembled(OFF).presentation is None
    pres = _assembled(ON).presentation
    assert pres is not None and pres.style == "dynamic"
    assert pres.voices.narrator  # VoiceCast.narrator may not be empty
    assert pres.look_seed == look_seed_for("Basic Electrical Engineering", "Unit 2: DC Circuits", "Session 3",
                                           "Ohm's Law")


def test_the_default_presentation_builds_a_dynamic_timeline_that_uses_the_new_templates() -> None:
    """End to end: the presentation a generation now writes really does reach the director and the templates."""
    data = screenplay_dict()
    data["presentation"] = default_presentation(
        subject_name=data["subject_name"], unit_name=data["unit_name"],
        session_number=data["session_number"], session_title=data["session_title"],
        narrator_voice="en-IN-NeerjaNeural").model_dump(mode="json")
    sp = Screenplay.model_validate(data)

    assert build_timeline(sp, make_manifest(sp), settings=OFF).presentation is None  # the flag still rules
    timeline = build_timeline(sp, make_manifest(sp), settings=ON)
    assert timeline.presentation is not None
    drawn = {s.look.template for s in timeline.scenes if s.look is not None}
    assert drawn <= set(TEMPLATES), f"a template the stage has not got: {drawn - set(TEMPLATES)}"
    # The point of the slice: a generated lecture reaches a B2 template, not board_generic everywhere.
    assert drawn - {"board_generic"}, f"only the B1 fallback was chosen: {drawn}"
    assert any(s.events for s in timeline.scenes), "the director wrote no visual events"
