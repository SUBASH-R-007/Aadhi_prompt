"""Hand-written screenplay + manifest covering every scene type (compose tests)."""

from __future__ import annotations

from typing import Any

from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS, AssetManifest, BeatAudio, MediaInfo, SceneAudio, SceneMedia
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import TimedWord

SPEECH = 1.0  # every synthetic beat speaks for exactly this long


def beat(bid: str, narration: str, **kw: Any) -> dict[str, Any]:
    return {"id": bid, "narration": narration, **kw}


def screenplay_dict() -> dict[str, Any]:
    return {
        "subject_name": "Basic Electrical Engineering",
        "unit_name": "Unit 2: DC Circuits",
        "session_number": "Session 3",
        "session_title": "Ohm's Law",
        "language": "en-IN",
        "learning_objectives": [{"id": "obj-1", "text": "Apply Ohm's law", "concept_ids": ["ohm"]}],
        "concept_map": [
            {"id": "basics", "title": "Charge and Current"},
            {"id": "ohm", "title": "Ohm's Law", "depends_on": ["basics"]},
        ],
        "figures": [{"id": "fig-1", "caption": "A resistor", "asset_key": "figure-aaa", "width": 640, "height": 480}],
        "scenes": [
            {"id": "s-title", "type": "title", "title": "Ohm's Law", "concept_id": "basics",
             "board": [{"id": "i1", "kind": "heading", "text": "Ohm's Law"}],
             "beats": [beat("s-title-b1", "Welcome to this session on Ohm's law.")]},
            {"id": "s-chapter", "type": "chapter_card", "title": "Current", "chapter_label": "Part 1",
             "concept_id": "basics", "beats": []},
            {"id": "s-board", "type": "content", "title": "The law", "concept_id": "ohm", "objective_ids": ["obj-1"],
             "board": [
                 {"id": "h", "kind": "heading", "text": "V = IR"},
                 {"id": "p1", "kind": "bullet", "text": "Voltage is proportional to current"},
                 {"id": "f1", "kind": "formula", "latex": "V = I R"},
                 {"id": "fig", "kind": "figure", "figure_id": "fig-1", "caption": "A resistor"},
                 {"id": "e1", "kind": "example_step", "text": "I = 2 A", "blank": True},
             ],
             "beats": [
                 beat("s-board-b1", "Voltage is proportional to current.", board_item_id="p1", pause_after=0.5),
                 beat("s-board-b2", "We write this as V equals I R.", board_item_id="f1", highlight_item_ids=["p1"]),
                 beat("s-board-b3", "Here is a resistor.", board_item_id="fig"),
                 beat("s-board-b4", "So the current is two amperes.", board_item_id="e1"),
                 beat("s-board-b5", "Let us fill in the step.", fill_item_id="e1", highlight_item_ids=["f1", "p1"]),
             ],
             "side_panel": {"kind": "figure", "figure_id": "fig-1", "rationale": "shows the part",
                            "show_from_beat_id": "s-board-b3"}},
            {"id": "s-sim", "type": "simulation", "title": "Animation", "concept_id": "ohm", "mascot_position": "right",
             "manim": {"template": "equation_steps", "params": {}},
             "beats": [beat("s-sim-b1", "Watch the current rise.", visual_cue="Current arrow grows"),
                       beat("s-sim-b2", "And the voltage doubles.", visual_cue="Voltage bar doubles")]},
            {"id": "s-sim-fallback", "type": "simulation", "title": "Fallback animation", "concept_id": "ohm",
             "manim": {"template": "equation_steps", "params": {}},
             "beats": [beat("s-simf-b1", "First the battery.", visual_cue="Battery appears with $5 label"),
                       beat("s-simf-b2", "Then nothing visual."),
                       beat("s-simf-b3", "Finally the bulb.", visual_cue="Bulb *glows*")]},
            {"id": "s-video", "type": "ai_video", "title": "Power lines", "concept_id": "ohm",
             "video_prompt": "Drone shot over power lines", "fallback_image_prompt": "power lines",
             "beats": [beat("s-video-b1", "Power lines carry current across the state.")]},
            {"id": "s-video-none", "type": "ai_video", "title": "Substation", "concept_id": "ohm",
             "video_prompt": "Slow pan across a substation at dusk",
             "beats": [beat("s-videon-b1", "A substation steps the voltage down.")]},
            {"id": "s-interactive", "type": "interactive", "title": "Try it", "concept_id": "ohm",
             "mascot_position": "hidden", "p5_code": "function setup(){createCanvas(100,100);}",
             "beats": [beat("s-int-b1", "Drag the slider to change the resistance.")]},
            {"id": "s-quiz", "type": "quiz_checkpoint", "title": "Check", "concept_id": "ohm",
             "question": "If R doubles at fixed V, what happens to I?",
             "options": ["It halves", "It doubles", "It stays the same"], "correct_index": 0,
             "feedback_wrong": ["", "No: I = V/R", "No: R changed"], "explanation": "I = V / R.",
             "countdown_seconds": 5,
             "beats": [beat("s-quiz-b1", "Here is a quick question.")],
             "reveal_beats": [beat("s-quiz-r1", "The current halves.")]},
            {"id": "s-noaudio", "type": "summary", "title": "Summary", "concept_id": "ohm",
             "board": [{"id": "t1", "kind": "takeaway", "text": "V = IR always"}],
             "beats": [beat("s-noaudio-b1", "To summarise, voltage equals current times resistance.",
                            board_item_id="t1")],
             "side_panel": {"kind": "gif", "gif_query": "electricity", "rationale": "fun"}},
        ],
    }


def make_screenplay(**overrides: Any) -> Screenplay:
    data = screenplay_dict()
    data.update(overrides)
    return Screenplay.model_validate(data)


def scene_audio(scene_id: str, beat_ids: list[str], *, narrations: dict[str, str] | None = None,
                pauses: dict[str, float] | None = None, quiz_countdown: tuple[int, int] | None = None,
                reveal_ids: tuple[str, ...] = (), key: str | None = None) -> SceneAudio:
    """Synthetic narration layout: SPEECH seconds per beat, INTER_BEAT_GAP, pauses, countdown."""
    narrations = narrations or {}
    pauses = pauses or {}
    beats: list[BeatAudio] = []
    t = 0.0
    countdown_start = None
    for bid in beat_ids:
        if bid in reveal_ids and countdown_start is None and quiz_countdown:
            countdown_start = t
            t += quiz_countdown[1]
        text = narrations.get(bid, f"spoken {bid}")
        tokens = text.split()
        step = SPEECH / max(1, len(tokens))
        words = [TimedWord(text=w, start=round(t + k * step, 3), end=round(t + (k + 1) * step, 3))
                 for k, w in enumerate(tokens)]
        beats.append(BeatAudio(beat_id=bid, phase="reveal" if bid in reveal_ids else "main", offset=round(t, 3),
                               speech_duration=SPEECH, pause_after=pauses.get(bid, 0.0), spoken_text=text,
                               words=words))
        t += SPEECH + pauses.get(bid, 0.0) + INTER_BEAT_GAP_SECONDS
    duration = round(t - INTER_BEAT_GAP_SECONDS, 3)
    return SceneAudio(scene_id=scene_id, asset_key=key or f"scene_audio-{scene_id}",
                      storage_key=f"assets/scene_audio/{key or 'scene_audio-' + scene_id}/x.mp3", mime="audio/mpeg",
                      duration=duration, beats=beats, provider="fake", voice="fake",
                      countdown_start=countdown_start, countdown_seconds=quiz_countdown[1] if quiz_countdown else None)


def make_manifest(sp: Screenplay, *, skip_audio: tuple[str, ...] = ("s-noaudio",)) -> AssetManifest:
    audio: dict[str, SceneAudio] = {}
    for scene in sp.scenes:
        if scene.id in skip_audio or not scene.all_beats():
            continue
        main = [b.id for b in scene.beats]
        reveal = [b.id for b in getattr(scene, "reveal_beats", [])]
        narr = {b.id: b.narration for b in scene.all_beats()}
        pauses = {b.id: b.pause_after for b in scene.all_beats()}
        audio[scene.id] = scene_audio(
            scene.id, main + reveal, narrations=narr, pauses=pauses,
            quiz_countdown=(1, scene.countdown_seconds) if scene.type == "quiz_checkpoint" else None,
            reveal_ids=tuple(reveal))
    media = {
        "s-board": SceneMedia(scene_id="s-board", figures={
            "fig": MediaInfo(asset_key="figure-aaa", storage_key="assets/figure/figure-aaa/f.png", kind="image",
                             mime="image/png", width=640, height=480, source="figure")}),
        "s-sim": SceneMedia(scene_id="s-sim", main=MediaInfo(
            asset_key="manim-sim", storage_key="assets/manim/manim-sim/m.mp4", kind="video", mime="video/mp4",
            duration=6.0, width=1280, height=720, source="manim")),
        "s-video": SceneMedia(scene_id="s-video", main_is_fallback=True, main=MediaInfo(
            asset_key="image-still", storage_key="assets/image/image-still/i.png", kind="image", mime="image/png",
            width=1024, height=576, source="fallback")),
        "s-interactive": SceneMedia(scene_id="s-interactive", poster=MediaInfo(
            asset_key="poster-1", storage_key="assets/poster/poster-1/p.png", kind="image", mime="image/png",
            source="poster")),
        "s-noaudio": SceneMedia(scene_id="s-noaudio", side_panel=MediaInfo(
            kind="gif", mime="image/gif", external_url="https://media.giphy.com/media/abc/giphy.gif", source="gif")),
    }
    return AssetManifest(audio=audio, media=media)
