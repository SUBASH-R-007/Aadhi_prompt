"""B3's own Dynamic fixtures (B3 plan §A B3-0 task 5, §3.2 "Frames: identical for a FIXED manifest").

Built from the templates ``overhaul/v2`` draws today, so B3 never waits on B2g's gallery. Each fixture is a folder of
``tests/fixtures/b3/``:

* ``lesson_16x9``: a 16:9 lesson that is a kitchen sink of today's templates (every implemented one), with a quiz
  (countdown and reveal) and chapter bumpers;
* ``reel_9x16``: a 9:16 Learning reel;
* ``dense_16x9``: a 16:9 lesson whose dense templates (formula, worked steps, code: ``TemplateSpec.dense``) fill at
  least 30% of the runtime, the case B3d's ``gaps_bookends`` music mode is for;
* ``long_16x9``: a 60-minute (or longer) 16:9 timeline for the chunked mix's memory bound (B3e), metadata only (no
  audio file exists for it);
* ``b2_era``: a stored B2-era Dynamic lecture (bare narrator, no calibration, ``MusicChoice(source="off")``), the
  legacy gate's (``tests/invariance/test_b3_legacy.py``).

Each of the first four holds ``screenplay.json``, ``manifest.json`` (the deterministic metadata manifest of
``tests/invariance/classic_fixture.synthetic_manifest``: 0.2 s + 0.3 s per word, synthetic media) and ``timeline.json``
(``build_timeline`` with the Dynamic mode on). The screenplay and manifest are the FIXED inputs; the timeline is what
today's code builds from them, and ``test_b3_fixtures.py`` rebuilds it and requires every field equal except ``music``
and ``sfx`` (the fields B3 adds), so an audio step never moves a frame of a fixed manifest.

``b2_era`` is split the same way, but this module never writes its inputs. ``screenplay.json``, ``options.json`` and
``manifest.json`` (the manifest the fake TTS built for it) stand for a lecture that is already stored: they were written
once (2026-10-17, on ``dd8c2d4``) and stay frozen forever. Only its DERIVED goldens are written here, from those frozen
inputs: ``timeline.json`` (``build_timeline``) and ``master_audio.json`` (the B1 master audio that the motion engine
builds for that timeline, every ffmpeg command and side file)::

    python -m tests.fixtures.b3.build            # write the four fixtures and b2_era's derived goldens
    python -m tests.fixtures.b3.build --check    # exit 1 when a stored timeline (or a b2_era golden) is not current

Regenerate after B2 changes what a Dynamic timeline holds (a template, ``direct.py``, ``ENGINE_VERSION``), with the
change named in the commit. The narration is placeholder text about one topic (waves), long enough to time.

The builder depends on no database and no environment. The settings are the code defaults with the Dynamic mode on
(``classic_fixture.pinned_settings``: no environment variable, no ``.env``). The administrator's runtime switch
(``aadhi.runtime_flags``, stored in whatever database ``DATABASE_URL`` names) is left out of every build
(:func:`no_runtime_flags`). An administrator who switched Dynamic off therefore cannot make ``--check`` fail or make the
builder write classic timelines, and no database file is opened or created.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import hashlib
import json
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent
NAMES = ("lesson_16x9", "reel_9x16", "dense_16x9", "long_16x9")
# The stored B2-era lecture: frozen inputs (never written here) and the goldens derived from them (written here).
LEGACY = "b2_era"
LEGACY_INPUTS = ("screenplay", "options", "manifest")
LEGACY_DERIVED = ("timeline", "master_audio")
ALL = (*NAMES, LEGACY)
LONG_MIN_SECONDS = 3600.0
LONG_ROUNDS = 9  # x the lesson's 22 scenes: within the 200-scene cap
DENSE_MIN_SHARE = 0.30


# --- screenplay pieces ----------------------------------------------------------------------------------------------


def _beat(bid: str, text: str, **kw: Any) -> dict[str, Any]:
    return {"id": bid, "narration": text, **kw}


def _presentation(fmt: str, **over: Any) -> dict[str, Any]:
    """B2's default presentation for this lecture (``authoring.default_presentation``), as a generation stores it."""
    from aadhi.compose.dynamic.authoring import default_presentation

    p = default_presentation(subject_name="Physics", unit_name="Unit 3: Waves", session_number="Session 4",
                             session_title="Wave speed", tts_provider="edge", language="en-IN", fmt=fmt, **over)
    return p.model_dump(mode="json")


def _meta() -> dict[str, Any]:
    return {
        "subject_name": "Physics", "unit_name": "Unit 3: Waves", "session_number": "Session 4",
        "session_title": "Wave speed", "language": "en-IN", "board_language": "en-IN",
        "learning_objectives": [{"id": "obj-1", "text": "Use v = f lambda", "concept_ids": ["speed"]}],
        "concept_map": [{"id": "waves", "title": "What a wave is"},
                        {"id": "speed", "title": "Wave speed", "depends_on": ["waves"]}],
        "lexicon": [{"written": "SI", "spoken": "S I"}],
    }


def _title(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "title", "title": "Wave speed",
            "board": [{"id": f"{sid}-h", "kind": "heading", "text": "Wave speed"}],
            "beats": [_beat(f"{sid}-1", "Today we find out how fast a wave travels, and why.")]}


def _chapter(sid: str, number: int, title: str) -> dict[str, Any]:
    return {"id": sid, "type": "chapter_card", "title": title, "chapter_number": number,
            "beats": [_beat(f"{sid}-1", f"Part {number}: {title}.")]}


def _bullets(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "What a wave carries", "concept_id": "waves",
            "board": [{"id": f"{sid}-b1", "kind": "bullet", "text": "A wave carries energy from place to place"},
                      {"id": f"{sid}-b2", "kind": "bullet", "text": "The medium only moves back and forth"},
                      {"id": f"{sid}-b3", "kind": "bullet", "text": "Sound and light are both waves"}],
            "beats": [_beat(f"{sid}-1", "A wave carries energy from one place to another.", board_item_id=f"{sid}-b1"),
                      _beat(f"{sid}-2", "The medium itself only moves back and forth around where it was.",
                            board_item_id=f"{sid}-b2"),
                      _beat(f"{sid}-3", "Sound in air and light from the sun are both waves.",
                            board_item_id=f"{sid}-b3")]}


def _definition(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Wavelength", "concept_id": "speed",
            "board": [{"id": f"{sid}-d", "kind": "definition", "term": "Wavelength",
                       "text": "the distance between two neighbouring crests of a wave"}],
            "beats": [_beat(f"{sid}-1", "The wavelength is the distance between two neighbouring crests.",
                            board_item_id=f"{sid}-d"),
                      _beat(f"{sid}-2", "We write it with the Greek letter lambda and measure it in metres.")]}


def _terms(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Three words to know", "concept_id": "speed",
            "intent": {"narrative_role": "pretraining"},
            "board": [{"id": f"{sid}-t1", "kind": "definition", "term": "Frequency",
                       "text": "how many waves pass a point each second"},
                      {"id": f"{sid}-t2", "kind": "definition", "term": "Period",
                       "text": "the time one wave takes to pass"},
                      {"id": f"{sid}-t3", "kind": "definition", "term": "Amplitude",
                       "text": "how far the medium moves from rest"}],
            "beats": [_beat(f"{sid}-1", "Frequency is how many waves pass a point each second.",
                            board_item_id=f"{sid}-t1"),
                      _beat(f"{sid}-2", "The period is the time one wave takes to pass.", board_item_id=f"{sid}-t2"),
                      _beat(f"{sid}-3", "Amplitude is how far the medium moves from rest.",
                            board_item_id=f"{sid}-t3")]}


def _formula(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "The wave equation", "concept_id": "speed",
            "board": [{"id": f"{sid}-f", "kind": "formula", "latex": "v = f \\lambda",
                       "variables": [{"symbol_latex": "v", "meaning": "wave speed in metres per second"},
                                     {"symbol_latex": "f", "meaning": "frequency in hertz"},
                                     {"symbol_latex": "\\lambda", "meaning": "wavelength in metres"}]}],
            "beats": [_beat(f"{sid}-1", "Wave speed equals frequency times wavelength.", board_item_id=f"{sid}-f"),
                      _beat(f"{sid}-2", "Speed in metres per second, frequency in hertz, wavelength in metres."),
                      _beat(f"{sid}-3", "Double the frequency at the same speed and the wavelength halves.")]}


def _worked(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "example", "title": "Worked example", "concept_id": "speed",
            "board": [{"id": f"{sid}-h", "kind": "heading", "text": "A 440 hertz note in air"},
                      {"id": f"{sid}-s1", "kind": "example_step", "text": "Write what we know: f = 440 Hz, v = 343 m/s"},
                      {"id": f"{sid}-s2", "kind": "example_step", "text": "Rearrange: lambda = v / f"},
                      {"id": f"{sid}-s3", "kind": "example_step", "text": "Divide: 343 / 440 = 0.78 m"}],
            "beats": [_beat(f"{sid}-1", "Let us try a note of four hundred and forty hertz in air.",
                            board_item_id=f"{sid}-h"),
                      _beat(f"{sid}-2", "We know the frequency and the speed of sound, three hundred and forty three.",
                            board_item_id=f"{sid}-s1"),
                      _beat(f"{sid}-3", "Rearrange the equation so the wavelength is speed over frequency.",
                            board_item_id=f"{sid}-s2"),
                      _beat(f"{sid}-4", "Divide, and the wavelength comes out at about seventy eight centimetres.",
                            board_item_id=f"{sid}-s3")]}


def _code(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Computing it", "concept_id": "speed",
            "board": [{"id": f"{sid}-c", "kind": "code", "language": "python",
                       "code": "def wavelength(v, f):\n    return v / f\n\nprint(wavelength(343, 440))"}],
            "beats": [_beat(f"{sid}-1", "Here is the same calculation as a small Python function.",
                            board_item_id=f"{sid}-c"),
                      _beat(f"{sid}-2", "It takes the speed and the frequency and returns their ratio."),
                      _beat(f"{sid}-3", "Running it prints about zero point seven eight metres.")]}


def _myth(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "A common mix-up", "concept_id": "waves",
            "board": [{"id": f"{sid}-m", "kind": "misconception", "text": "A louder sound travels faster",
                       "justification": "Loudness changes the amplitude, not the speed"}],
            "beats": [_beat(f"{sid}-1", "Many people think a louder sound travels faster.", board_item_id=f"{sid}-m"),
                      _beat(f"{sid}-2", "It does not: loudness changes the amplitude, and the speed stays the same.")]}


def _hook(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Wave speed", "concept_id": "waves",
            "intent": {"narrative_role": "hook"},
            "board": [{"id": f"{sid}-q", "kind": "question", "text": "Why do you see lightning before the thunder?"}],
            "beats": [_beat(f"{sid}-1", "Why do you see the lightning before you hear the thunder?",
                            board_item_id=f"{sid}-q"),
                      _beat(f"{sid}-2", "By the end of this lesson you will be able to work it out.")]}


def _predict(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Predict", "concept_id": "speed",
            "intent": {"narrative_role": "predict"},
            "board": [{"id": f"{sid}-q", "kind": "question", "text": "What happens to the wavelength if f doubles?",
                       "blank": True}],
            "beats": [_beat(f"{sid}-1", "Before we go on: what happens to the wavelength if the frequency doubles?",
                            board_item_id=f"{sid}-q"),
                      _beat(f"{sid}-2", "Hold on to your guess. We will check it in a moment.")]}


def _recap(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "recap", "title": "Recall", "concept_id": "speed",
            "board": [{"id": f"{sid}-q1", "kind": "question", "text": "What does a wave carry?"},
                      {"id": f"{sid}-q2", "kind": "question", "text": "How do you find a wave's speed?"},
                      {"id": f"{sid}-t1", "kind": "takeaway", "text": "Energy, not matter"},
                      {"id": f"{sid}-t2", "kind": "takeaway", "text": "v = f lambda"},
                      {"id": f"{sid}-t3", "kind": "takeaway", "text": "The medium sets the speed"}],
            "beats": [_beat(f"{sid}-1", "Without looking back: what does a wave carry?", board_item_id=f"{sid}-q1"),
                      _beat(f"{sid}-2", "And how do you find its speed?", board_item_id=f"{sid}-q2"),
                      _beat(f"{sid}-3", "Energy, not matter.", board_item_id=f"{sid}-t1"),
                      _beat(f"{sid}-4", "Speed is frequency times wavelength.", board_item_id=f"{sid}-t2"),
                      _beat(f"{sid}-5", "And the medium sets the speed.", board_item_id=f"{sid}-t3")]}


def _outro(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "summary", "title": "Next: sound", "concept_id": "speed",
            "board": [{"id": f"{sid}-t", "kind": "takeaway", "text": "Next time: how we hear sound"}],
            "beats": [_beat(f"{sid}-1", "Next time we use this to explain how we hear sound. See you then.",
                            board_item_id=f"{sid}-t")]}


def _chart(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Speed of sound in three media", "concept_id": "speed",
            "side_panel": {"kind": "chart", "rationale": "compare speeds",
                           "chart": {"chart_type": "bar", "labels": ["Air", "Water", "Steel"],
                                     "datasets": [{"label": "Speed of sound (m/s)",
                                                   "data": [343.0, 1480.0, 5960.0]}]}},
            "beats": [_beat(f"{sid}-1", "Sound moves at about three hundred and forty metres per second in air."),
                      _beat(f"{sid}-2", "In water it is four times faster, and in steel faster still.")]}


def _media(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Ripples on a pond", "concept_id": "waves",
            "board": [{"id": f"{sid}-p", "kind": "paragraph", "text": "Each ripple carries energy outwards."}],
            "side_panel": {"kind": "image", "image_prompt": "ripples spreading on a calm pond", "rationale": "real"},
            "beats": [_beat(f"{sid}-1", "Drop a stone in a pond and ripples spread out.", board_item_id=f"{sid}-p"),
                      _beat(f"{sid}-2", "The water stays where it is; the energy moves outwards.")]}


def _video(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "ai_video", "title": "A slinky", "concept_id": "waves",
            "video_prompt": "a slinky stretched on a table with a pulse travelling along it", "rationale": "motion",
            "fallback_image_prompt": "a stretched slinky",
            "beats": [_beat(f"{sid}-1", "Watch the pulse travel along the slinky while each coil stays put.")]}


def _simulation(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "simulation", "title": "A wave on a string", "concept_id": "speed",
            "manim": {"code": "class A(AadhiScene):\n    def construct(self):\n        self.finish()"},
            "beats": [_beat(f"{sid}-1", "Here a wave moves along a string.", visual_cue="wave"),
                      _beat(f"{sid}-2", "Each point only moves up and down.", visual_cue="dot")]}


def _interactive(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "interactive", "title": "Try it", "concept_id": "speed",
            "p5_code": "function setup(){ createCanvas(400, 200); }",
            "beats": [_beat(f"{sid}-1", "Move the slider and watch the wavelength change with the frequency.")]}


def _quiz(sid: str, seconds: int = 5) -> dict[str, Any]:
    return {"id": sid, "type": "quiz_checkpoint", "title": "Quick check", "concept_id": "speed",
            "question": "A wave has a frequency of 2 Hz and a wavelength of 3 m. How fast does it travel?",
            "options": ["1.5 m/s", "5 m/s", "6 m/s"], "correct_index": 2, "countdown_seconds": seconds,
            "explanation": "v = f lambda = 2 x 3 = 6 m/s.",
            "beats": [_beat(f"{sid}-1", "Try this one. Pause the video if you need more time.")],
            "reveal_beats": [_beat(f"{sid}-2", "Six metres per second: two times three.")]}


def _summary(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "summary", "title": "What we learned", "concept_id": "speed",
            "board": [{"id": f"{sid}-b1", "kind": "bullet", "text": "A wave carries energy, not matter"},
                      {"id": f"{sid}-b2", "kind": "bullet", "text": "v = f lambda"},
                      {"id": f"{sid}-b3", "kind": "bullet", "text": "Speed depends on the medium"}],
            "beats": [_beat(f"{sid}-1", "A wave carries energy, not matter.", board_item_id=f"{sid}-b1"),
                      _beat(f"{sid}-2", "Its speed is frequency times wavelength.", board_item_id=f"{sid}-b2"),
                      _beat(f"{sid}-3", "And that speed depends on the medium.", board_item_id=f"{sid}-b3")]}


def _with_chapters(data: dict[str, Any], groups: list[tuple[str, str, list[str]]]) -> dict[str, Any]:
    data["chapters"] = [{"id": cid, "title": title, "scene_ids": ids} for cid, title, ids in groups]
    return data


# --- the fixtures' screenplays --------------------------------------------------------------------------------------


def lesson_screenplay() -> dict[str, Any]:
    scenes = [_hook("hook"), _title("title"), _chapter("ch1", 1, "What a wave is"), _bullets("carries"),
              _media("pond"), _myth("myth"), _video("slinky"), _chapter("ch2", 2, "Wave speed"),
              _definition("lambda"), _terms("terms"), _formula("equation"), _predict("predict"), _worked("worked"),
              _chart("speeds"), _simulation("string"), _interactive("play"), _code("code"), _quiz("check"),
              _recap("recap"),
              _summary("summary"), _outro("outro")]
    data = {**_meta(), "scenes": scenes, "presentation": _presentation("landscape")}
    return _with_chapters(data, [
        ("c1", "What a wave is", ["hook", "title", "ch1", "carries", "pond", "myth", "slinky"]),
        ("c2", "Wave speed", ["ch2", "lambda", "terms", "equation", "predict", "worked", "speeds", "string", "play",
                              "code", "check", "recap", "summary", "outro"])])


def reel_screenplay() -> dict[str, Any]:
    scenes = [_hook("hook"), _bullets("carries"), _definition("lambda"), _formula("equation"),
              _quiz("check", seconds=3), _outro("outro")]
    data = {**_meta(), "scenes": scenes, "presentation": _presentation("portrait", reel_profile="learning")}
    return data


def dense_screenplay() -> dict[str, Any]:
    scenes = [_title("title"), _formula("equation"), _worked("worked"), _bullets("carries"), _code("code"),
              _formula("equation-2"), _worked("worked-2"), _quiz("check"), _summary("summary")]
    scenes = [_renamed(s, s["id"]) for s in scenes]
    data = {**_meta(), "scenes": scenes, "presentation": _presentation("landscape")}
    return data


def long_screenplay() -> dict[str, Any]:
    """The lesson's scenes repeated (new ids) with every narration said three times, so the timeline lasts at least
    LONG_MIN_SECONDS within the screenplay's 200-scene cap."""
    base = lesson_screenplay()
    rounds = LONG_ROUNDS
    scenes: list[dict[str, Any]] = []
    for k in range(rounds):
        for s in base["scenes"]:
            scene = _renamed(s, f"{s['id']}-r{k}")
            for b in [*scene.get("beats", []), *scene.get("reveal_beats", [])]:
                b["narration"] = " ".join([b["narration"]] * 3)
            scenes.append(scene)
    data = {**_meta(), "scenes": scenes, "presentation": _presentation("landscape")}
    data["chapters"] = [{"id": f"c{k}", "title": f"Round {k + 1}",
                         "scene_ids": [f"{s['id']}-r{k}" for s in base["scenes"]]} for k in range(rounds)]
    return data


def _renamed(scene: dict[str, Any], new_id: str) -> dict[str, Any]:
    """A deep copy of ``scene`` under ``new_id``, its beat and board ids prefixed alike (ids stay unique)."""
    s = copy.deepcopy(scene)
    old = s["id"]
    s["id"] = new_id

    def ren(value: str) -> str:
        return new_id + value[len(old):] if value.startswith(old) else f"{new_id}-{value}"

    for b in [*s.get("beats", []), *s.get("reveal_beats", [])]:
        b["id"] = ren(b["id"])
        if b.get("board_item_id"):
            b["board_item_id"] = ren(b["board_item_id"])
    for item in s.get("board", []):
        item["id"] = ren(item["id"])
    return s


SCREENPLAYS = {"lesson_16x9": lesson_screenplay, "reel_9x16": reel_screenplay, "dense_16x9": dense_screenplay,
               "long_16x9": long_screenplay}


# --- files ----------------------------------------------------------------------------------------------------------

# The fields B3 adds to a timeline (``Timeline.music`` in B3d, ``Timeline.sfx`` in B3f): the only ones an audio step
# may change on a fixed manifest (B3 plan §3.2).
B3_TIMELINE_FIELDS = ("music", "sfx")
# long_16x9 is metadata for the mix's memory bound, an hour of it: only its screenplay is stored (compact), its
# manifest is the deterministic synthetic one and its timeline is pinned by a digest, not a 2 MB file.
COMPACT = ("long_16x9",)


def settings(**overrides: Any) -> Any:
    """The Dynamic mode on, everything else at the code defaults: no ``.env`` and no environment variable."""
    from tests.invariance import classic_fixture as fx

    return fx.pinned_settings(dynamic_enabled=True, **overrides)


@contextlib.contextmanager
def no_runtime_flags() -> Iterator[None]:
    """Run the block without the administrator's runtime switches (``aadhi.runtime_flags``): no stored flag overrides
    the settings, and no database is read (or created) to look for one. ``dynamic_enabled`` merges the stored switch
    with the settings through ``runtime_flags.get``, which calls ``runtime_flags.flags``. A fixture is a Dynamic
    lecture, whatever an administrator switched in the database that the shell happens to point at."""
    from aadhi import runtime_flags

    original = runtime_flags.flags

    def none_stored(*, refresh: bool = False) -> dict[str, Any]:
        return {}

    runtime_flags.flags = none_stored  # type: ignore[assignment]
    try:
        yield
    finally:
        runtime_flags.flags = original  # type: ignore[assignment]


def timeline_of(sp: Any, manifest: Any) -> dict[str, Any]:
    """``build_timeline`` of ``sp`` and ``manifest`` with the Dynamic mode on, independent of any database."""
    from aadhi.compose.timeline import build_timeline

    with no_runtime_flags():
        return build_timeline(sp, manifest, settings=settings()).model_dump(mode="json")


def path(name: str, doc: str) -> Path:
    return FIXTURE_DIR / name / f"{doc}.json"


def dumps(obj: Any, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def load(name: str, doc: str) -> Any:
    return json.loads(path(name, doc).read_text(encoding="utf-8"))


def frame_view(timeline: dict[str, Any]) -> dict[str, Any]:
    """A timeline dump without the fields B3 adds: what must stay equal for a fixed manifest."""
    return {k: v for k, v in timeline.items() if k not in B3_TIMELINE_FIELDS}


def digest(timeline: dict[str, Any]) -> dict[str, Any]:
    view = frame_view(timeline)
    text = json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"total_duration": view["total_duration"], "scenes": len(view["scenes"]),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def load_screenplay(name: str) -> Any:
    from aadhi.schemas.screenplay import Screenplay

    return Screenplay.model_validate(load(name, "screenplay"))


def load_manifest(name: str) -> Any:
    """The fixture's FIXED manifest (the stored one; long_16x9's synthetic one, derived from its stored screenplay)."""
    from aadhi.schemas.manifest import AssetManifest
    from tests.invariance import classic_fixture as fx

    if name in COMPACT:
        return fx.synthetic_manifest(load_screenplay(name))
    return AssetManifest.model_validate(load(name, "manifest"))


def rebuilt_timeline(name: str) -> dict[str, Any]:
    """The timeline today's code builds from the stored (fixed) screenplay and manifest."""
    return timeline_of(load_screenplay(name), load_manifest(name))


def load_timeline(name: str) -> Any:
    """The stored timeline (long_16x9: rebuilt, then checked against its digest)."""
    from aadhi.schemas.timeline import Timeline

    if name in COMPACT:
        data = rebuilt_timeline(name)
        if digest(data) != load(name, "timeline_digest"):
            raise ValueError(f"{name}: the rebuilt timeline does not match its digest (run build --check)")
        return Timeline.model_validate(data)
    return Timeline.model_validate(load(name, "timeline"))


def documents(name: str) -> dict[str, Any]:
    """The JSON documents of one fixture, built from its screenplay function (``b2_era``: its derived goldens only,
    built from its frozen inputs)."""
    from aadhi.schemas.screenplay import Screenplay
    from tests.invariance import classic_fixture as fx

    if name == LEGACY:
        return legacy_documents()
    sp = Screenplay.model_validate(SCREENPLAYS[name]())
    manifest = fx.synthetic_manifest(sp)
    timeline = timeline_of(sp, manifest)
    if name in COMPACT:
        return {"screenplay": sp.model_dump(mode="json"), "timeline_digest": digest(timeline)}
    return {"screenplay": sp.model_dump(mode="json"), "manifest": manifest.model_dump(mode="json"),
            "timeline": timeline}


def write(name: str) -> None:
    """Write fixture ``name`` (``b2_era``: only its derived goldens; its frozen inputs are never written)."""
    for doc, value in documents(name).items():
        p = path(name, doc)
        p.parent.mkdir(parents=True, exist_ok=True)
        compact = name in COMPACT and doc == "screenplay"
        p.write_text(dumps(value, compact=compact), encoding="utf-8", newline="\n")


def stale(name: str) -> list[str]:
    """What differs, for the fields B3 must not change, between the stored timeline and the one today's code builds
    from the stored screenplay and manifest (``b2_era``: each derived golden, whole)."""
    if name == LEGACY:
        return legacy_stale()
    docs =("screenplay", "timeline_digest") if name in COMPACT else ("screenplay", "manifest", "timeline")
    missing = [d for d in docs if not path(name, d).is_file()]
    if missing:
        return [f"{name}: missing {', '.join(missing)}"]
    rebuilt = rebuilt_timeline(name)
    if name in COMPACT:
        return [] if digest(rebuilt) == load(name, "timeline_digest") else [f"{name}/timeline_digest.json"]
    return [] if frame_view(rebuilt) == frame_view(load(name, "timeline")) else [f"{name}/timeline.json"]


# --- b2_era: frozen inputs, derived goldens -------------------------------------------------------------------------


def legacy_inputs() -> tuple[Any, Any, Any]:
    """The stored B2-era lecture: its screenplay, generation options and manifest, exactly as frozen."""
    from aadhi.pipeline.base import GenerationOptions
    from aadhi.schemas.manifest import AssetManifest
    from aadhi.schemas.screenplay import Screenplay

    missing = [d for d in LEGACY_INPUTS if not path(LEGACY, d).is_file()]
    if missing:
        raise FileNotFoundError(f"{LEGACY}: the frozen input(s) {missing} are missing; restore them from git (they "
                                "stand for a lecture already stored and are never regenerated)")
    return (Screenplay.model_validate(load(LEGACY, "screenplay")),
            GenerationOptions.model_validate(load(LEGACY, "options")),
            AssetManifest.model_validate(load(LEGACY, "manifest")))


def legacy_timeline() -> dict[str, Any]:
    """The timeline today's code builds from the frozen B2-era screenplay and manifest."""
    sp, _, manifest = legacy_inputs()
    return timeline_of(sp, manifest)


def legacy_documents() -> dict[str, Any]:
    """``b2_era``'s derived goldens, from its frozen inputs: the timeline and its B1 master audio."""
    from aadhi.schemas.timeline import Timeline

    timeline = legacy_timeline()
    return {"timeline": timeline, "master_audio": master_audio(Timeline.model_validate(timeline))}


def legacy_stale() -> list[str]:
    missing = [d for d in (*LEGACY_INPUTS, *LEGACY_DERIVED) if not path(LEGACY, d).is_file()]
    if missing:
        return [f"{LEGACY}: missing {', '.join(missing)}"]
    return [f"{LEGACY}/{doc}.json" for doc, value in legacy_documents().items() if value != load(LEGACY, doc)]


def master_audio(timeline: Any) -> dict[str, Any]:
    """The B1 master audio of ``timeline`` as the motion engine builds it (``engine._build_master``): every ffmpeg
    command and side file. ffmpeg is replaced by a recorder (the filter-script flag is Debian's ffmpeg 5.1 one), and
    the local folders are replaced by ``<work>`` / ``<branding>``, so the document is the same on every machine."""
    from aadhi.compose.base import BGM

    with tempfile.TemporaryDirectory(prefix="b3-master-") as tmp, no_runtime_flags():
        branding = Path(tmp) / "branding"
        branding.mkdir()
        (branding / BGM).write_bytes(b"bgm")
        return asyncio.run(_master_commands(timeline, Path(tmp) / "work", branding))


async def _master_commands(timeline: Any, work: Path, branding: Path) -> dict[str, Any]:
    from aadhi.compose.ffmpeg import filter_script_flag
    from aadhi.compose.motion import engine
    from aadhi.compose.motion.chunks import scene_frame_bounds, total_frames
    from aadhi.compose.motion.encode import MasterAudioSpec
    from aadhi.compose.video import branding_file
    from tests.invariance import goldens

    master_settings = goldens.settings_for_goldens(branding_dir=str(branding), render_bgm=True)
    work.mkdir(parents=True, exist_ok=True)  # the engine's run creates it before the audio task starts
    fps = int(timeline.fps)
    bounds = scene_frame_bounds(timeline, fps)
    total = total_frames(bounds)
    inputs = work / "inputs"
    preset = {s.audio_asset_key: inputs / f"{s.audio_asset_key}.mp3" for s in timeline.scenes if s.audio_asset_key}
    files = engine._Files(None, master_settings, inputs, preset)
    bgm = branding_file(master_settings, timeline.branding.bgm_url) if master_settings.render_bgm else None
    if bgm is None:
        raise RuntimeError("the branding BGM must be part of the B1 master")
    master = MasterAudioSpec(audio_list="audio.ffconcat", total_seconds=total / fps, bgm=str(bgm),
                             bgm_volume=timeline.branding.bgm_volume)
    recorded: list[dict[str, Any]] = []

    async def run_cmd(args: list[str], side: dict[str, str], **_: Any) -> None:
        recorded.append({"args": list(args), "files": dict(side)})

    original = engine._run_cmd
    engine._run_cmd = run_cmd  # type: ignore[assignment]
    try:
        out = await engine._build_master(timeline, bounds, files, work=work, settings=master_settings,
                                         script_flag=filter_script_flag((5, 1)), master=master, check_cancelled=None)
    finally:
        engine._run_cmd = original  # type: ignore[assignment]
    concat = (work / master.audio_list).read_text(encoding="utf-8")
    doc = {"output": out, "audio_list": concat, "commands": recorded}
    roots = {str(work.resolve()): "<work>", str(work): "<work>", str(branding.resolve()): "<branding>",
             str(branding): "<branding>"}
    return _placeholders(doc, roots)


def _placeholders(value: Any, roots: dict[str, str]) -> Any:
    """``value`` with each local root replaced by its placeholder and the path after it in ``/`` form."""
    if isinstance(value, dict):
        return {k: _placeholders(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_placeholders(v, roots) for v in value]
    if isinstance(value, str):
        for root, name in roots.items():
            for spelling in (root, root.replace("\\", "/")):
                if spelling in value:
                    head, _, tail = value.partition(spelling)
                    value = head + name + tail.replace("\\", "/")
        return value
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write or check B3's Dynamic fixtures (tests/fixtures/b3).")
    ap.add_argument("--check", action="store_true", help="exit 1 when a stored timeline or golden is stale")
    ap.add_argument("names", nargs="*", help=f"fixtures (default: all of {', '.join(ALL)}; {LEGACY}: its derived "
                                             "goldens only)")
    args = ap.parse_args(argv)
    names = args.names or list(ALL)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        ap.error(f"unknown fixture(s) {unknown}; choose from {', '.join(ALL)}")
    if args.check:
        bad = [s for n in names for s in stale(n)]
        for s in bad:
            print(f"stale: {s}", file=sys.stderr)
        return 1 if bad else 0
    for n in names:
        write(n)
        print(f"wrote {n}" + (" (derived goldens only; its frozen inputs are never written)" if n == LEGACY else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
