"""Schema v3 (Dynamic video, design §6.7 / §8.2; batch B0): inert fields, the reader-version marker, validators,
the ``scene_hash`` exclusions, ``repair.carry_over`` and the generated web schemas.

Classic invariance rests on two generic properties checked here for every model: each field that is not in the v2
baseline is a declared v3 field, and every declared v3 field is left out of the JSON at its default.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from aadhi.schemas import features, manifest, screenplay, timeline
from aadhi.schemas.features import (
    DYNAMIC_FEATURES,
    FEATURE_LABELS,
    READER_SCHEMA_VERSION,
    NewerDocumentError,
    check_reader_version,
    features_used,
    uses_v3,
)
from aadhi.schemas.manifest import AssetManifest, BeatAudio, MediaInfo, MusicAsset, SceneAudio
from aadhi.schemas.screenplay import BoardItem, Screenplay
from aadhi.schemas.timeline import Timeline
from tests.schemas.v3_fixtures import (
    FIXTURE,
    PRESENTATION,
    canonical_maximal,
    classic_screenplay_dict,
    maximal_screenplay_dict,
    with_feature,
)

# Field names of every schema model before B0 (the v2 contract, snapshot of the pre-B0 models). A field outside it
# must be a declared v3 field; a model outside it must exist only inside v3 fields.
V2_FIELDS: dict[str, set[str]] = {
    name: set(fields)
    for name, fields in json.loads((FIXTURE.parent / "v2_model_fields.json").read_text("utf-8")).items()
}

# Fields added to a v2 model after B0 that are not v3 features: classic bookkeeping a build writes only in a rare case,
# left out of the JSON at their default (so classic documents serialise exactly as before) and ignored by an older
# reader (``StrictModel`` ignores unknown keys), so they need no reader-version marker.
CLASSIC_ADDITIONS: dict[str, set[str]] = {
    # O17: the voice the Edge stand-in narrated a scene in place of (aadhi.schemas.manifest.built_voice)
    "SceneAudio": {"stand_in_for"},
}

# Models that exist only inside v3 fields (never in a classic document).
V3_ONLY_MODELS = {
    "FigurePin", "WordRange", "ClipRange", "Overlay", "VoiceCalibration", "VoiceCast", "MusicChoice", "HookRef",
    "AssetStyle", "Presentation", "PresenterSpec", "SceneLook", "ClipCaption", "ClipScene", "TimelinePresentation",
    "ResolvedLook", "VisualEvent", "KineticWord", "KineticChunk", "MusicCue", "MusicPlan", "SfxCue", "TimedOverlay",
    "MusicAsset",
    # B2a (plan B2 §1.2): parts of ResolvedLook
    "SlotFix", "LookFixes", "LookIcon", "RoadmapEntry", "RefAnchor", "RefPrediction", "RefNext", "LookRefs",
    # B2b (plan B2 §1.3): TimelinePresentation.fonts, typed
    "TimelineFonts",
}


def _models() -> list[type[BaseModel]]:
    out = []
    for module in (screenplay, timeline, manifest):
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__:
                out.append(value)
    return out


# --- classic invariance (generic) ----------------------------------------------------------------------------------


def test_every_new_field_is_a_declared_v3_field() -> None:
    """A field added to a v2 model must be listed in V3_FIELDS (so it is detected, marked and gated)."""
    for cls in _models():
        if cls.__name__ in V3_ONLY_MODELS or getattr(cls, "V3_MODEL", None):
            continue
        if cls is screenplay.StrictModel:
            continue
        assert cls.__name__ in V2_FIELDS, f"{cls.__name__} is new: list it in V3_ONLY_MODELS or the baseline"
        base = V2_FIELDS[cls.__name__]
        assert base <= set(cls.model_fields), f"{cls.__name__} lost v2 fields {sorted(base - set(cls.model_fields))}"
        new = set(cls.model_fields) - base - CLASSIC_ADDITIONS.get(cls.__name__, set())
        assert new <= set(cls.V3_FIELDS), f"{cls.__name__}: undeclared new fields {sorted(new - set(cls.V3_FIELDS))}"


def test_every_classic_addition_is_left_out_of_the_json_at_its_default_and_is_not_v3() -> None:
    models = {cls.__name__: cls for cls in _models()}
    for name, fields in CLASSIC_ADDITIONS.items():
        cls = models[name]
        for field in fields:
            assert field not in V2_FIELDS[name] and field not in cls.V3_FIELDS, (name, field)
            info = cls.model_fields[field]
            assert info.exclude_if is not None and info.exclude_if(info.get_default(call_default_factory=True)), (
                f"{name}.{field} is serialised at its default")
    audio = SceneAudio(scene_id="s", stand_in_for="openai:nova")
    assert audio.model_dump(mode="json")["stand_in_for"] == "openai:nova"
    assert "stand_in_for" not in SceneAudio(scene_id="s").model_dump(mode="json")
    assert AssetManifest(audio={"s": audio}).model_dump(mode="json")["schema_version"] == 2  # not a v3 document


def test_every_v3_field_is_left_out_of_the_json_at_its_default() -> None:
    for cls in _models():
        for name, feature in cls.V3_FIELDS.items():
            assert feature in DYNAMIC_FEATURES, (cls.__name__, name)
            info = cls.model_fields[name]
            assert info.exclude_if is not None, f"{cls.__name__}.{name} has no exclude_if"
            assert info.exclude_if(info.get_default(call_default_factory=True)) is True, f"{cls.__name__}.{name}"
        for name, values in cls.V3_VALUES.items():
            assert name in cls.model_fields and set(values.values()) <= set(DYNAMIC_FEATURES), (cls.__name__, name)
        assert cls.V3_MODEL in (None, *DYNAMIC_FEATURES), cls.__name__


def test_feature_names_and_labels_are_the_frozen_contract() -> None:
    assert DYNAMIC_FEATURES == ("presentation", "items_v3", "cuts", "overlays", "clip", "voice_override")
    assert set(FEATURE_LABELS) == set(DYNAMIC_FEATURES)
    assert READER_SCHEMA_VERSION == 3 and features.V3_SCHEMA_VERSION == 3 and features.CLASSIC_SCHEMA_VERSION == 2
    assert NewerDocumentError.code == "newer_document"
    assert not issubclass(NewerDocumentError, ValueError)  # pydantic must not wrap it into a ValidationError


def test_classic_screenplays_round_trip_unchanged_as_version_2() -> None:
    from aadhi.legacy.convert import convert_legacy
    from tests.compose.factories import screenplay_dict as compose_screenplay

    raw = json.loads((FIXTURE.parents[2] / "core" / "fixtures" / "showcase_ohms_law.json").read_text("utf-8"))
    docs = [classic_screenplay_dict(), compose_screenplay(), convert_legacy(raw)[0].model_dump(mode="json")]
    for doc in docs:
        sp = Screenplay.model_validate(doc)
        dumped = sp.model_dump(mode="json")
        assert dumped["schema_version"] == 2 and not uses_v3(sp) and features_used(sp) == {}
        assert Screenplay.model_validate(dumped).model_dump(mode="json") == dumped
        text = json.dumps(dumped)
        for key in ("presentation", "look", "colour_is_content", "visual_intent", "speaker", "answers", "cuts",
                    "overlays", "voice_override_asset_key", "sequence", "builds_on", "icon_hint", "pins", "level"):
            assert f'"{key}"' not in text, key


# --- the maximal Dynamic screenplay ---------------------------------------------------------------------------------


def test_maximal_fixture_file_is_current() -> None:
    assert json.loads(FIXTURE.read_text("utf-8")) == canonical_maximal(), (
        "rewrite it with: python -m tests.schemas.v3_fixtures"
    )


def _assert_subset(small: Any, big: Any, path: str = "") -> None:
    """Every value of ``small`` (the authored input) survives in ``big`` (the dump)."""
    if isinstance(small, dict):
        assert isinstance(big, dict), path
        for k, v in small.items():
            assert k in big, f"{path}.{k} dropped"
            _assert_subset(v, big[k], f"{path}.{k}")
    elif isinstance(small, list):
        assert isinstance(big, list) and len(big) == len(small), path
        for i, (a, b) in enumerate(zip(small, big, strict=True)):
            _assert_subset(a, b, f"{path}[{i}]")
    else:
        assert small == big or (isinstance(small, (int, float)) and float(small) == float(big)), f"{path}: {small!r}"


def test_maximal_screenplay_keeps_every_v3_field_through_round_trips() -> None:
    authored = maximal_screenplay_dict()
    sp = Screenplay.model_validate(authored)
    dumped = sp.model_dump(mode="json")
    _assert_subset({k: v for k, v in authored.items()}, dumped)
    again = Screenplay.model_validate(json.loads(json.dumps(dumped)))
    assert again.model_dump(mode="json") == dumped
    assert Screenplay.model_validate_json(again.model_dump_json()).model_dump(mode="json") == dumped
    assert dumped["schema_version"] == 3 and sp.schema_version == 3
    assert set(features_used(sp)) == set(DYNAMIC_FEATURES)


@pytest.mark.parametrize("feature", DYNAMIC_FEATURES)
def test_each_feature_is_detected_alone_and_marks_version_3(feature: str) -> None:
    sp = Screenplay.model_validate(with_feature(feature))
    assert set(features_used(sp)) == {feature}
    assert sp.schema_version == 3 and sp.model_dump(mode="json")["schema_version"] == 3


def test_new_enum_values_count_as_v3() -> None:
    doc = classic_screenplay_dict()
    doc["scenes"][0]["intent"] = {"goal": "g", "narrative_role": "recap"}
    sp = Screenplay.model_validate(doc)
    assert features_used(sp) == {"presentation": [("scenes", 0, "intent", "narrative_role")]}
    doc["scenes"][0]["intent"]["narrative_role"] = "check"  # already a v2 role
    assert not uses_v3(Screenplay.model_validate(doc))


def test_marker_follows_the_content() -> None:
    sp = Screenplay.model_validate(with_feature("cuts"))
    assert sp.schema_version == 3
    stripped = sp.model_dump(mode="json")
    del stripped["scenes"][0]["beats"][1]["cuts"]
    back = Screenplay.model_validate(stripped)  # a 3 without any v3 field is classic again
    assert back.schema_version == 2 and back.model_dump(mode="json")["schema_version"] == 2
    # a model_copy skips validation; the dump still marks the document
    classic = Screenplay.model_validate(classic_screenplay_dict())
    scene = classic.scenes[0]
    looked = scene.model_copy(update={"look": screenplay.SceneLook(template="board_generic")})
    copied = classic.model_copy(update={"scenes": [looked, *classic.scenes[1:]]})
    assert copied.schema_version == 2 and copied.model_dump(mode="json")["schema_version"] == 3
    assert "schema_version" not in copied.model_dump(mode="json", exclude={"schema_version"})


# --- newer documents --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", [4, "4", 4.0, 99])
def test_a_newer_screenplay_is_refused_never_stripped(version: Any) -> None:
    doc = {**classic_screenplay_dict(), "schema_version": version, "field_from_the_future": {"x": 1}}
    with pytest.raises(NewerDocumentError) as exc:
        Screenplay.model_validate(doc)
    assert exc.value.document == "screenplay" and exc.value.version == int(version) and exc.value.reader == 3
    with pytest.raises(NewerDocumentError):
        Screenplay.model_validate_json(json.dumps(doc))


def test_the_refusal_passes_through_validation_wrappers() -> None:
    """``validate_stored`` and ``except ValidationError`` never swallow it (pydantic does not wrap it)."""
    from aadhi.schemas.jsonsafe import validate_stored

    doc = {**classic_screenplay_dict(), "schema_version": 4}
    with pytest.raises(NewerDocumentError):
        validate_stored(Screenplay, doc)
    with pytest.raises(NewerDocumentError):
        try:
            Screenplay.model_validate(doc)
        except ValidationError:  # pragma: no cover - the point of the test
            pytest.fail("wrapped into a ValidationError")


def test_versions_up_to_the_reader_are_read() -> None:
    for version in (1, 2, 3, None, "x", True):
        check_reader_version({"schema_version": version}, "screenplay")
    check_reader_version(["not", "a", "mapping"], "screenplay")
    assert Screenplay.model_validate({**classic_screenplay_dict(), "schema_version": 3}).schema_version == 2


def test_a_newer_timeline_or_manifest_is_refused() -> None:
    with pytest.raises(NewerDocumentError) as exc:
        Timeline.model_validate({"schema_version": 4, "scenes": []})
    assert exc.value.document == "timeline"
    with pytest.raises(NewerDocumentError) as exc:
        AssetManifest.model_validate({"schema_version": 5})
    assert exc.value.document == "asset manifest"


# --- validators -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"kind": "stat", "text": "context"}, "requires 'value' and 'text'"),
        ({"kind": "stat", "value": "3"}, "requires 'value' and 'text'"),
        ({"kind": "pair", "text": "concept"}, "requires 'term' and 'text'"),
        ({"kind": "quote", "text": "words"}, "requires 'text' and 'caption'"),
        ({"kind": "question"}, "question board item requires 'text'"),
        ({"kind": "step"}, "step board item requires 'text'"),
        ({"kind": "bullet", "text": "x", "blank": True}, "only example_step and question items can be blank"),
        ({"kind": "bullet", "text": "x", "pins": [{"id": "p", "x": 0.1, "y": 0.2}]}, "only figure board items"),
        ({"kind": "stat", "value": "three", "text": "x"}, "should match pattern"),
        ({"kind": "bullet", "text": "x", "icon_hint": "x" * 25}, "at most 24 characters"),
    ],
)
def test_new_item_kinds_need_their_fields(item: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        BoardItem.model_validate({"id": "i1", **item})


def test_valid_new_items() -> None:
    for item in (
        {"kind": "stat", "value": "-40", "unit": "°C", "text": "where scales meet"},
        {"kind": "pair", "term": "Pressure", "text": "Voltage"},
        {"kind": "quote", "text": "Words.", "caption": "Someone"},
        {"kind": "question", "text": "Why?", "blank": True, "level": "apply"},
        {"kind": "step", "text": "First", "date": "1827"},
        {"kind": "figure", "figure_id": "f", "pins": [{"id": "p", "x": 0, "y": 1, "label": "here"}]},
    ):
        BoardItem.model_validate({"id": "i1", **item})


def test_a_blank_question_can_be_filled() -> None:
    doc = classic_screenplay_dict()
    doc["scenes"][0]["board"].append({"id": "q", "kind": "question", "text": "Why?", "blank": True})
    doc["scenes"][0]["beats"][1]["board_item_id"] = "q"
    doc["scenes"][0]["beats"].append({"id": "s1-b3", "narration": "Because.", "fill_item_id": "q"})
    Screenplay.model_validate(doc)


def test_answers_must_name_an_item_of_an_earlier_scene() -> None:
    doc = classic_screenplay_dict()
    doc["scenes"][1]["beats"][0]["answers"] = "s1/s1-i1"
    sp = Screenplay.model_validate(doc)
    assert sp.scenes[1].beats[0].answers == "s1/s1-i1"
    for bad in ("s2/s2-i1", "s3/s1-i1", "s1/nope", "q1/q1-b1"):  # same scene, later/unknown scene, unknown item
        doc["scenes"][1]["beats"][0]["answers"] = bad
        with pytest.raises(ValidationError):
            Screenplay.model_validate(doc)
    doc["scenes"][1]["beats"][0]["answers"] = "not a ref"
    with pytest.raises(ValidationError):
        Screenplay.model_validate(doc)


def test_cuts_overlays_and_clip_scenes_are_validated() -> None:
    def build(**beat: Any) -> dict[str, Any]:
        doc = classic_screenplay_dict()
        doc["scenes"][0]["beats"][0].update(beat)
        return doc

    with pytest.raises(ValidationError, match="end after it starts"):
        Screenplay.model_validate(build(cuts=[{"start": 3, "end": 3, "text": "x"}]))
    short = {"id": "o", "at_word": 0, "at_text": "This", "clip": {"asset_key": "k", "in_frame": 0, "out_frame": 29}}
    with pytest.raises(ValidationError, match="at least 30 frames"):
        Screenplay.model_validate(build(overlays=[short]))
    twice = {**short, "clip": {"asset_key": "k", "in_frame": 0, "out_frame": 30}}
    with pytest.raises(ValidationError, match="overlay ids must be unique"):
        Screenplay.model_validate(build(overlays=[twice, twice]))
    doc = with_feature("clip")
    doc["scenes"][1]["clip"]["out_frame"] = 14
    with pytest.raises(ValidationError, match="at least 15 frames"):
        Screenplay.model_validate(doc)
    doc = with_feature("clip")
    doc["scenes"][1]["focus"] = {"portrait": [0.5, 1.5]}
    with pytest.raises(ValidationError, match="within 0..1"):
        Screenplay.model_validate(doc)
    doc = with_feature("clip")
    doc["scenes"][1]["captions"] = [{"start": 2.0, "end": 1.0, "text": "x"}]
    with pytest.raises(ValidationError, match="end after it starts"):
        Screenplay.model_validate(doc)
    doc = with_feature("clip")
    doc["scenes"][1]["beats"] = [{"id": "c-b", "narration": "Look.", "board_item_id": "s1-i1"}]
    with pytest.raises(ValidationError, match="only board scenes may reference board items"):
        Screenplay.model_validate(doc)


def test_presentation_values_are_validated() -> None:
    for bad in (
        {"theme_family": "Teal Coral"},  # catalogue ids are slugs
        {"look_seed": -1},
        {"reel_card_x": 2000},
        {"entity_colors": {"V": 3}},
        {"pins": ["everything"]},
        {"hook": {"question_item": "s1", "kind": "question"}},
    ):
        doc = with_feature("presentation")
        doc["presentation"].update(bad)
        with pytest.raises(ValidationError):
            Screenplay.model_validate(doc)
    doc = with_feature("presentation")
    doc["presentation"]["pins"] = ["voice", "voice", "music"]
    assert Screenplay.model_validate(doc).presentation.pins == ["voice", "music"]


def test_clip_scenes_are_never_plannable() -> None:
    from aadhi.pipeline.base import PlannedScene

    PlannedScene.model_validate({"id": "s1", "type": "content", "goal": "g"})
    with pytest.raises(ValidationError, match="a plan cannot contain a 'clip' scene"):
        PlannedScene.model_validate({"id": "s1", "type": "clip", "goal": "g"})
    assert "clip" in screenplay.SCENE_TYPES and "clip" not in screenplay.PLANNABLE_SCENE_TYPES


# --- timeline and manifest ------------------------------------------------------------------------------------------


def _timeline() -> dict[str, Any]:
    from tests.api.factories import make_screenplay, make_timeline

    return make_timeline(make_screenplay()).model_dump(mode="json")


def test_timeline_v3_fields_mark_version_3_and_stay_inside_their_scene() -> None:
    base = _timeline()
    tl = Timeline.model_validate(base)
    assert tl.schema_version == 2 and tl.model_dump(mode="json") == base and not uses_v3(tl)
    doc = copy.deepcopy(base)
    scene = doc["scenes"][0]
    d = scene["duration"]
    scene["events"] = [{"t": 0.5, "kind": "reveal", "target": "s1-i1", "anchored": True}]
    scene["kinetic_captions"] = [{"start": 0.0, "end": 1.0, "words": [{"text": "Hi", "start": 0.0, "end": 0.5}],
                                  "lines": ["Hi"]}]
    scene["overlays"] = [{"id": "o1", "start": 0.2, "end": min(d, 1.2),
                          "media": {"kind": "video", "asset_key": "clip-k", "fit": "contain_blur",
                                    "end_behavior": "clamp", "trim_in": 1.0, "audio": True}}]
    scene["look"] = {"template": "board_generic", "tokens": {"bg": "#101820"},
                     "background": {"preset": "mesh_drift", "seed": 1}, "transition_in": {"kind": "cut", "seconds": 0}}
    tl = Timeline.model_validate(doc)
    dumped = tl.model_dump(mode="json")
    assert dumped["schema_version"] == 3 and set(features_used(tl)) == {"presentation", "overlays", "clip"}
    assert Timeline.model_validate(dumped).model_dump(mode="json") == dumped
    late = copy.deepcopy(doc)
    late["scenes"][0]["events"][0]["t"] = d + 1
    with pytest.raises(ValidationError, match="outside the scene"):
        Timeline.model_validate(late)
    late = copy.deepcopy(doc)
    late["scenes"][0]["overlays"][0]["end"] = d + 1
    with pytest.raises(ValidationError, match="outside the scene"):
        Timeline.model_validate(late)
    late = copy.deepcopy(doc)
    late["scenes"][0]["kinetic_captions"][0]["end"] = d + 1
    with pytest.raises(ValidationError, match="outside the scene"):
        Timeline.model_validate(late)


def test_a_dynamic_timeline_follows_its_format() -> None:
    doc = _timeline()
    doc["intro"] = None
    presentation = {
        "style": "dynamic", "format": "portrait", "content": {"ink": "#ffffff"}, "fonts": {"display": "outfit"},
        "personality": "crisp", "caption_style": "clean_pill", "cue_term_style": "marker", "captions_burn": True,
        "sfx": True, "motion": "standard", "generated_seconds": 12.5,
    }
    doc["presentation"] = presentation
    with pytest.raises(ValidationError, match="portrait timeline cannot be 1920x1080"):
        Timeline.model_validate(doc)
    doc["width"], doc["height"] = 1080, 1920
    doc["music"] = {"cues": [{"asset_key": "m", "start": 0, "end": 5, "fade_in": 1, "fade_out": 1}],
                    "duck": [[0.0, -12.0]], "mode": "bed"}
    doc["sfx"] = [{"t": 1.0, "sound": "pop", "gain_db": -18}]
    tl = Timeline.model_validate(doc)
    assert tl.schema_version == 3 and tl.model_dump(mode="json")["presentation"]["format"] == "portrait"


def test_manifest_v3_fields_are_inert_until_used() -> None:
    classic = AssetManifest(audio={"s1": SceneAudio(scene_id="s1", beats=[BeatAudio(beat_id="b", offset=0,
                                                                                     speech_duration=1)])},
                            media={})
    dumped = classic.model_dump(mode="json")
    assert dumped["schema_version"] == 2 and "music" not in dumped
    assert set(dumped["audio"]["s1"]["beats"][0]) >= {"beat_id"} and "persona" not in dumped["audio"]["s1"]["beats"][0]
    used = AssetManifest(
        audio={"s1": SceneAudio(scene_id="s1", lufs=-16.2, speaker_gains={"narrator": 0.0},
                                beats=[BeatAudio(beat_id="b", offset=0, speech_duration=1, persona="en-in-neerja",
                                                 words_exact=True)])},
        music=[MusicAsset(asset_key="m", provider="procedural", duration=60, loop_points=[(4.0, 36.0)])],
    )
    dumped = used.model_dump(mode="json")
    assert dumped["schema_version"] == 3
    assert AssetManifest.model_validate(dumped).model_dump(mode="json") == dumped
    assert MediaInfo(kind="video", mime="video/mp4", has_audio=True).model_dump(mode="json")["has_audio"] is True


# --- asset hashing and carry-over -------------------------------------------------------------------------------------


def test_look_and_overlays_never_change_the_scene_hash_but_cuts_do() -> None:
    from aadhi.pipeline.assets import scene_hash

    sp = Screenplay.model_validate(classic_screenplay_dict())
    scene = sp.scenes[0]
    h = scene_hash(scene)
    looked = scene.model_copy(update={"look": screenplay.SceneLook(template="board_generic", lock="pinned")})
    assert scene_hash(looked) == h
    overlay = screenplay.Overlay(id="o", at_word=0, at_text="This",
                                 clip=screenplay.ClipRange(asset_key="k", in_frame=0, out_frame=30))
    beats = list(scene.beats)
    beats[0] = beats[0].model_copy(update={"overlays": [overlay]})
    assert scene_hash(scene.model_copy(update={"beats": beats})) == h
    quiz = sp.scenes[-1]
    reveal = [quiz.reveal_beats[0].model_copy(update={"overlays": [overlay]})]
    assert scene_hash(quiz.model_copy(update={"reveal_beats": reveal})) == scene_hash(quiz)
    for update in ({"cuts": [screenplay.WordRange(start=0, end=1, text="This")]}, {"speaker": "student"},
                   {"voice_override_asset_key": "narration-k"}):
        beats = list(scene.beats)
        beats[0] = beats[0].model_copy(update=update)
        assert scene_hash(scene.model_copy(update={"beats": beats})) != h, update


def test_repair_carry_over_keeps_the_look() -> None:
    from aadhi.pipeline.repair import carry_over

    sp = Screenplay.model_validate(classic_screenplay_dict())
    current = sp.scenes[0].model_copy(update={"look": screenplay.SceneLook(template="versus", lock="settled")})
    rewritten = sp.scenes[0].model_copy(update={"title": "Rewritten"})
    kept = carry_over(current, rewritten)
    assert kept.look == current.look and kept.title == "Rewritten"
    assert carry_over(sp.scenes[0], rewritten).look is None


# --- generated web schemas --------------------------------------------------------------------------------------------


def test_web_schemas_are_generated_from_the_models() -> None:
    from aadhi.schemas import generate

    assert generate.stale_files() == [], "run: python -m aadhi.schemas.generate"
    assert generate.main(["--check"]) == 0
    sp_schema = json.loads((generate.SCHEMA_DIR / "screenplay.schema.json").read_text("utf-8"))
    assert "Presentation" in sp_schema["$defs"] and "ClipScene" in sp_schema["$defs"]
    assert set(screenplay.V3_BOARD_ITEM_KINDS) <= set(sp_schema["$defs"]["BoardItemKind"]["enum"])


def test_presentation_fixture_is_valid_on_its_own() -> None:
    p = screenplay.Presentation.model_validate(PRESENTATION)
    assert p.model_dump(mode="json")["hook"]["question_item"] == "s-hook/q-hook"
