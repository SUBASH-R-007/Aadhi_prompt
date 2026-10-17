"""The reel fixtures of owner decision 4 (2026-10-16): the 9:16 cases the decision added to three landed templates, as
template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and what makes one valid), so the
slow real-Chromium gates run on them - the 1x / 3x / reverse frame hashes (``tests/determinism/
test_template_fixtures.py``) and real Dynamic preflight with zero blocks and zero ``type_floor`` findings
(``tests/determinism/test_template_pages.py``)::

    python -m tests.compose.dynamic.make_reels_fixtures           # write the files
    python -m tests.compose.dynamic.make_reels_fixtures --check    # exit 1 when a file is stale

* ``check.portrait.reel.json`` (Teal / Crisp, dark): a four-option quiz with a written rationale on one wrong option -
  the reel draws the correct answer and that option (the strongest distractor, tier 2) - and a three-option quiz with
  no rationale at all, which draws the answer and the first wrong option (tier 3).
* ``worked_steps.portrait.reel.json`` (Paper / Crisp, light): a worked example with a heading, the formula card, three
  steps and a **terminal** side panel - the panel's own full-stage beat before the steps, timed as
  ``compose/timeline.py`` times it with the reel lead (``reels.reel_lead``: the first narrated step's reveal at
  2.2 s) and with the events ``reels.reel_events`` writes (the beat's ``reveal`` on ``panel``, the heading's
  deferred ``reveal``).
* ``key_terms.portrait.reel.json`` (Indigo / Soft, dark): three terms after a **graph** panel whose authored title is
  drawn (the Dynamic engine has no plot path yet: the chart_build hook), with NO planned panel event - the page's own
  ``reelWindow`` fallback.

A picture panel needs a served medium, which these fixtures do not carry; the jsdom tests
(``web/tests/dynamic/templates/reels.test.js``) draw one. Each file stays inside the slow gate's 300 frames. The
narration is placeholder text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, reels, themes
from aadhi.schemas.timeline import TimedScene, Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

SIZE = (1080, 1920)
SCENE_LEAD = 0.5  # compose/base.py SCENE_LEAD_SECONDS


def _presentation(family_id: str, personality: str, cue_style: str) -> dict[str, Any]:
    family = pr.family_for(family_id)
    return {
        "style": "dynamic", "format": "portrait", "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": faces.resolver_fonts("outfit"), "personality": personality, "caption_style": "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def _beat(sid: str, k: int, start: float, length: float, text: str, *, item: str | None = None,
          phase: str = "main") -> dict[str, Any]:
    speech_end = round(start + length - 0.3, 3)
    return {
        "beat_id": f"{sid}-{k}", "index": k, "phase": phase, "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }


def _look(template: str, family_id: str, slot: int, index: int, transition: str, duration: float, *,
          dense: bool) -> dict[str, Any]:
    family = pr.family_for(family_id)
    return {
        "template": template, "tokens": themes.scene_tokens(family, slot),
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 9000 + index,
                       "frozen_at": 0.0 if dense else None},
        "transition_in": {"kind": transition, "seconds": pr.crossfade_seconds(duration) if transition == "crossfade"
                          else 0.0},
    }


def _timeline(lang: str, family_id: str, personality: str, cue_style: str, scenes: list[dict[str, Any]]) -> Timeline:
    total = round(sum(s["duration"] for s in scenes), 3)
    pres = _presentation(family_id, personality, cue_style)
    pres["generated_seconds"] = total
    captions = [{"start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3), "text": c["text"]}
                for s in scenes for b in s["beats"] for c in b["captions"]]
    data = {
        "schema_version": 3, "language": lang, "board_language": lang, "fps": 30, "width": SIZE[0], "height": SIZE[1],
        "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                 "session_number": "Session 3", "session_title": "Circuits"},
        "total_duration": total, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": captions, "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


# --- (a) the reel check -----------------------------------------------------------------------------------------------


def _quiz(sid: str, *, index: int, start: float, options: list[str], correct: int, feedback: list[str],
          question: str, explanation: str) -> dict[str, Any]:
    seconds = 3
    main = _beat(sid, 0, 0.5, 1.1, "Here is one to think about. Pause now if you need more time.")
    countdown_start = main["end"]
    reveal_start = round(countdown_start + seconds, 3)
    reveal = _beat(sid, 1, reveal_start, 0.9, "Here is the answer.", phase="reveal")
    duration = round(reveal["end"] + 0.5, 3)
    return {
        "scene_id": sid, "index": index, "type": "quiz_checkpoint", "title": "Quick check", "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(reveal["end"] - 0.5, 3),
        "board": [], "beats": [main, reveal], "events": [],
        "quiz": {"question": question, "options": options, "correct_index": correct, "feedback_wrong": feedback,
                 "explanation": explanation, "countdown_start": countdown_start, "countdown_seconds": seconds,
                 "reveal_start": reveal_start},
        "look": _look("check", "deep_teal", index, index, "cut" if index == 0 else "crossfade", duration, dense=False),
    }


def _check() -> Timeline:
    first = _quiz("ck-r-four", index=0, start=0.0,
                  question="Double the voltage across a fixed [[resistance]]. What does the current do?",
                  options=["It stays the same", "It doubles", "It halves", "It is used up faster"], correct=1,
                  feedback=["", "", "", "Current is never used up: it always comes home."],
                  explanation="It doubles: $I = V / R$.")
    second = _quiz("ck-r-three", index=1, start=first["duration"],
                   question="Which one limits the current in a loop?",
                   options=["A switch", "A battery", "A resistor"], correct=2, feedback=["", "", ""],
                   explanation="A resistor sets how much flows.")
    return _timeline("en-IN", "deep_teal", "crisp", "marker", [first, second])


# --- (b) the panel beat -----------------------------------------------------------------------------------------------


def _panel_scene(template: str, sid: str, *, family_id: str, items: list[dict[str, Any]], revealed: list[str],
                 panel: dict[str, Any], beat_len: float, planned: bool, dense: bool) -> dict[str, Any]:
    """One 9:16 scene with a side panel, timed as compose/timeline.py times it with the reel lead: the narration's
    first beat reveals ``revealed[0]``, so the lead (``reels.reel_lead``) moves it to the beat's minimum end. With
    ``planned`` the events are ``reels.reel_events``' (the beat's reveal on ``panel``, the deferred reveals), else
    none (the page's own ``reelWindow``)."""
    lead = reels.reel_lead(0.0, scene_lead=SCENE_LEAD)
    t = SCENE_LEAD + lead
    beats = []
    for k, item in enumerate(revealed):
        beats.append(_beat(sid, k, t, beat_len, f"Placeholder narration {k + 1}.", item=item))
        t = round(t + beat_len, 3)
    duration = round(t + 0.8, 3)
    scene: dict[str, Any] = {
        "scene_id": sid, "index": 0, "type": "content", "title": "Reel", "start": 0.0, "duration": duration,
        "audio_offset": round(SCENE_LEAD + lead, 3), "audio_duration": round(t - SCENE_LEAD - lead, 3),
        "board": items, "beats": beats, "events": [],
        "side_panel": {"panel": panel, "show_at": 0.0},
        "look": _look(template, family_id, 0, 0, "cut", duration, dense=dense),
    }
    if planned:
        # what events.py writes for the beats (a reveal per item a beat reveals, REVEAL_LEAD before it), then
        # reels.reel_events, as direct.plan calls it for this scene
        scene["events"] = [{"t": round(max(0.0, b["start"] - reels.REVEAL_LEAD), 3), "kind": "reveal",
                            "target": b["board_item_id"], "anchored": True} for b in beats]
        timed = TimedScene.model_validate(scene)
        accepts = lambda kind, target: True  # noqa: E731 - the post-integration consumes (reveal on item and panel)
        scene["events"] = [e.model_dump(mode="json", exclude_none=True)
                           for e in reels.reel_events(timed, list(timed.events), accepts=accepts)]
    return scene


def _worked_steps() -> Timeline:
    items = [
        {"id": "h1", "kind": "heading", "text": "A fan on 230 V"},
        {"id": "f1", "kind": "formula", "latex": "I = \\frac{V}{R}"},
        {"id": "s1", "kind": "example_step", "text": "$I = 230 / 115$", "justification": "Ohm's law"},
        {"id": "s2", "kind": "example_step", "text": "$I = 2\\ \\mathrm{A}$"},
        {"id": "s3", "kind": "example_step", "text": "Check the units"},
    ]
    panel = {"kind": "terminal", "title": "Python check",
             "terminal": {"command": "python ohm.py 230 115", "output": "I = 2.0 A\nok"}}
    scene = _panel_scene("worked_steps", "ws-r-term", family_id="paper_ink", items=items,
                         revealed=["s1", "s2", "s3"], panel=panel, beat_len=1.6, planned=True, dense=True)
    return _timeline("en-IN", "paper_ink", "crisp", "underline", [scene])


def _key_terms() -> Timeline:
    items = [
        {"id": "h1", "kind": "heading", "text": "Three words first"},
        {"id": "p1", "kind": "definition", "term": "Voltage", "text": "the push on charge"},
        {"id": "p2", "kind": "definition", "term": "Current", "text": "the flow of charge"},
        {"id": "p3", "kind": "definition", "term": "Resistance", "text": "what slows the flow"},
    ]
    panel = {"kind": "graph", "title": "I = V / R (a straight line)",
             "graph": {"functions": [{"expr": "x/2"}], "x_label": "V", "y_label": "I"}}
    scene = _panel_scene("key_terms", "kt-r-graph", family_id="midnight_indigo", items=items,
                         revealed=["p1", "p2", "p3"], panel=panel, beat_len=1.6, planned=False, dense=False)
    return _timeline("en-IN", "midnight_indigo", "soft", "marker", [scene])


def fixtures() -> dict[str, Timeline]:
    """Every reel fixture by file name (each inside the slow gate's 300 frames)."""
    return {
        "check.portrait.reel.json": _check(),
        "worked_steps.portrait.reel.json": _worked_steps(),
        "key_terms.portrait.reel.json": _key_terms(),
    }


def render(tl: Timeline) -> str:
    """The file text of a fixture (``Timeline.model_dump(mode="json")``, sorted keys, 1-space indent, final newline)."""
    return json.dumps(tl.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def stale(directory: Path = FIXTURE_DIR) -> list[str]:
    """The fixture files that are missing or differ from what :func:`fixtures` builds."""
    return [name for name, tl in fixtures().items()
            if not (directory / name).is_file() or (directory / name).read_text("utf-8") != render(tl)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a fixture file is stale")
    args = parser.parse_args(argv)
    if args.check:
        bad = stale()
        for name in bad:
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_reels_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
