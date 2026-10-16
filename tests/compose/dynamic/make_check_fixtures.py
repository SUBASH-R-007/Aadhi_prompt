"""The ``check`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and what makes
one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the personality's
motion scale and text entrance, the scene tokens of a palette slot) and the files can be rebuilt and checked byte for
byte::

    python -m tests.compose.dynamic.make_check_fixtures           # write the files
    python -m tests.compose.dynamic.make_check_fixtures --check    # exit 1 when a file is stale

Each file holds the two forms ``check`` draws (``web/js/dynamic/templates/check.js``), one scene each, entered by a
crossfade:

* a **quiz** scene (``TimedScene.quiz``), timed by the narration's own clock - the main beat, then
  ``countdown_start`` / ``countdown_seconds`` (§1.4's think time, which the planner stores), then ``reveal_start`` and
  the reveal beat. ``mcq`` with four options in 16:9 and ``true_false`` with two in 9:16, which is exactly what §4.3
  and R-06 allow each format (``templates.CHECK_MAX_OPTIONS``; more is ``board_generic``'s whole scene);
* a **board** scene with one blank ``question`` item (§4.3's ``free_recall`` / ``apply``, its answer in
  ``justification``), whose ring a ``countdown`` event times and whose reveal an ``answer`` event marks, with a
  ``reveal`` event on the question in one file so the entrance path through an event is drawn too.

The four timelines cover what the B2 plan §G gallery asks of a template, in both formats, in a dark and a light family
each, with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there):

* ``check.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85): the 16:9 four-option mcq and an English free
  recall.
* ``check.landscape.light.json`` (Paper / Crisp, light, ta-IN, Poppins + Anek): a Tamil true / false quiz and a mixed
  Tamil + English + ``$TeX$`` apply question, with the lint-limit longest option in wide capitals and a word longer
  than any line.
* ``check.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, Poppins): the reel's shape - a true / false quiz
  (R-06) and a free recall.
* ``check.portrait.hindi.json`` (Mint / Soft, light, hi-IN): a Hindi true / false quiz and a Hindi free recall.

Each file is at most about 10.2 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees both
scenes and the crossfade; the think times are therefore the schema's shortest (3 s) rather than §4.3's 15-30 s check.
The narration is placeholder text, never the question or the options verbatim (R-25)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "check"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# The lint-limit label (R-25: <= 8 words per item) in wide capitals, and a word longer than a 16:9 tile's text box
# (660 px) even at the R-29 floor: the page keeps every one of its lines and reports what does not fit (never clipped).
WIDE_LABEL = "MAXIMUM WATTAGE MEETS LOAD"
LONG_WORD_ANSWER = "Pneumonoultramicroscopicsilicovolcanoconiosis is the long word"


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str) -> dict[str, Any]:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than ``outfit``: the B2 fonts form naming that display face (and ``anek`` Indic faces with ``poppins``),
    which the resolver writes from B2e."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def _beat(sid: str, k: int, start: float, length: float, text: str, *, phase: str = "main",
          item: str | None = None, fill: str | None = None) -> dict[str, Any]:
    speech_end = round(start + length - 0.3, 3)
    out: dict[str, Any] = {
        "beat_id": f"{sid}-{k}", "index": k, "phase": phase, "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }
    if fill is not None:
        out["fill_item_id"] = fill
    return out


def _look(family_id: str, slot: int, index: int, transition: str, duration: float) -> dict[str, Any]:
    family = pr.family_for(family_id)
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    return {
        "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 8000 + index, "frozen_at": None},
        "transition_in": {"kind": transition, "seconds": seconds},
    }


def _quiz_scene(sid: str, *, index: int, start: float, family_id: str, slot: int, transition: str, title: str,
                question: str, options: list[str], correct: int, explanation: str, ask: str, reveal: str,
                events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A quiz scene: one main beat, the countdown (§1.4's think time, the schema's shortest 3 s), then the reveal
    beat. The template reads its whole clock from ``quiz``; events.py writes a quiz scene none (it has no board item
    to target), so ``events`` is only for the fixture that proves an event overrides that clock."""
    seconds = 3
    main = _beat(sid, 0, 0.5, 1.1, ask)
    countdown_start = main["end"]
    reveal_start = round(countdown_start + seconds, 3)
    reveal_beat = _beat(sid, 1, reveal_start, 0.9, reveal, phase="reveal")
    duration = round(reveal_beat["end"] + 0.5, 3)
    return {
        "scene_id": sid, "index": index, "type": "quiz_checkpoint", "title": title, "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(reveal_beat["end"] - 0.5, 3),
        "board": [], "beats": [main, reveal_beat], "events": list(events or []),
        "quiz": {
            "question": question, "options": options, "correct_index": correct,
            "feedback_wrong": ["" if i == correct else "Not quite." for i in range(len(options))],
            "explanation": explanation, "countdown_start": countdown_start, "countdown_seconds": seconds,
            "reveal_start": reveal_start,
        },
        "look": _look(family_id, slot, index, transition, duration),
    }


def _board_scene(sid: str, *, index: int, start: float, family_id: str, slot: int, transition: str, title: str,
                 question: str, answer: str, ask: str, tell: str, events: list[dict[str, Any]],
                 heading: str | None = None) -> dict[str, Any]:
    """A board check (§4.3's free recall / apply): one blank ``question`` item whose answer is its
    ``justification``, asked by the first beat and filled by the second, with an optional ``heading`` item - the
    label the template draws above the block (a quiz scene's label is its title instead)."""
    items: list[dict[str, Any]] = []
    if heading is not None:
        items.append({"id": "h1", "kind": "heading", "text": heading})
    items.append({"id": "q1", "kind": "question", "text": question, "justification": answer, "blank": True,
                  "level": "apply"})
    first = _beat(sid, 0, 0.5, 0.9, ask, item="h1" if heading is not None else "q1")
    second = _beat(sid, 1, 3.0, 0.8, tell, fill="q1")
    duration = round(second["end"] + 0.4, 3)
    return {
        "scene_id": sid, "index": index, "type": "content", "title": title, "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(second["end"] - 0.5, 3),
        "board": items, "beats": [first, second], "events": events,
        "look": _look(family_id, slot, index, transition, duration),
    }


def _event(t: float, kind: str, target: str, dur: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              scenes: list[dict[str, Any]]) -> Timeline:
    total = round(sum(s["duration"] for s in scenes), 3)
    pres = _presentation(fmt, family_id, personality, cue_style, display)
    pres["generated_seconds"] = total
    width, height = SIZES[fmt]
    captions = [{"start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3), "text": c["text"]}
                for s in scenes for b in s["beats"] for c in b["captions"]]
    data = {
        "schema_version": 3, "language": LANGS[lang], "board_language": LANGS[lang], "fps": 30, "width": width,
        "height": height, "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                                   "session_number": "Session 3", "session_title": "Circuits"},
        "total_duration": total, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": captions, "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


def _build(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
           specs: list[dict[str, Any]]) -> Timeline:
    """One timeline from the scene specs (each ``{"kind": "quiz" | "board", ...}``), laid end to end."""
    scenes: list[dict[str, Any]] = []
    start = 0.0
    for i, spec in enumerate(specs):
        kind = spec.pop("kind")
        make = _quiz_scene if kind == "quiz" else _board_scene
        scene = make(spec.pop("id"), index=i, start=start, family_id=family_id, slot=i,
                     transition="cut" if i == 0 else "crossfade", **spec)
        scenes.append(scene)
        start = round(start + scene["duration"], 3)
    return _timeline(fmt, lang, family_id, personality, cue_style, display, scenes)


# --- the scenes -----------------------------------------------------------------------------------------------------


def _mcq() -> dict[str, Any]:
    """§4.3's 16:9 mcq: four options, the correct one not first (the canonicaliser shuffles them)."""
    return {
        "kind": "quiz", "id": "ck-l-mcq", "title": "Quick check",
        "question": "Double the voltage across a fixed [[resistance]]. What does the current do?",
        "options": ["It stays the same", "It doubles", "It halves", "It is used up faster"],
        "correct": 1,
        "explanation": "Ohm's law says $I = V / R$: double $V$ with $R$ fixed and $I$ doubles.",
        "ask": "Here is one to think about. Pause now if you need more time.",
        "reveal": "It doubles. Double the push, double the flow.",
    }


def _free_recall() -> dict[str, Any]:
    """§4.3's free recall: no options, the answer revealed in place."""
    return {
        "kind": "board", "id": "ck-l-recall", "title": "Your turn",
        "question": "Name the quantity a [[resistor]] limits in a closed loop.",
        "answer": "The current, which the resistance holds back for a given voltage.",
        "ask": "Say it out loud before you read on.", "tell": "Here is the answer.",
        "events": [_event(1.5, "countdown", "q1", 1.5), _event(3.0, "answer", "q1")],
    }


def _tamil_true_false() -> dict[str, Any]:
    """The one quiz fixture with events of its own: a ``reveal`` on ``option:1`` (that option enters off the 100 ms
    stagger) and an ``answer`` on the correct ``option:0`` 200 ms before the quiz's own ``reveal_start`` - the pairs
    ``templates.py`` declares check consumes, which only a hand-written plan can produce today."""
    return {
        "kind": "quiz", "id": "ck-ta-tf", "title": "சோதனை",
        "question": "[[மின்னழுத்தம்]] இரட்டித்தால் மின்னோட்டமும் இரட்டிக்கும்.",
        "options": ["சரி", "தவறு"], "correct": 0,
        "explanation": "மின்தடை மாறாதபோது $I = V / R$ என்பதால் சரி.",
        "ask": "இதைப் பற்றி யோசியுங்கள்.", "reveal": "சரி என்பதே பதில்.",
        "events": [_event(0.8, "reveal", "option:1"), _event(4.4, "answer", "option:0")],
    }


def _mixed_apply() -> dict[str, Any]:
    """Tamil, English terms and TeX in one question, the lint-limit label in wide capitals (a `heading` item, which
    the template draws as the label) and the stress answer (a word longer than any line)."""
    return {
        "kind": "board", "id": "ck-ta-apply", "title": "ஓம் விதி", "heading": WIDE_LABEL,
        "question": "ஒரு [[current]] $I = V / R$ இல் $R$ இரட்டித்தால் என்ன ஆகும்?",
        "answer": LONG_WORD_ANSWER,
        "ask": "ஒரு படி கணக்கு.", "tell": "இதோ பதில்.",
        "events": [_event(0.4, "reveal", "q1"), _event(1.4, "countdown", "q1", 1.4), _event(3.0, "answer", "q1")],
    }


def _reel_true_false() -> dict[str, Any]:
    """R-06: a reel's check is free recall or true / false only, so 9:16 draws two options."""
    return {
        "kind": "quiz", "id": "ck-p-tf", "title": "Quick check",
        "question": "A closed loop is needed before any [[current]] flows.",
        "options": ["True", "False"], "correct": 0,
        "explanation": "True: charge must have a way back to the source.",
        "ask": "True or false? Pause now if you need more time.", "reveal": "True. The loop has to close.",
    }


def _reel_recall() -> dict[str, Any]:
    return {
        "kind": "board", "id": "ck-p-recall", "title": "Your turn",
        "question": "What does a [[resistor]] limit?",
        "answer": "The current in the loop.",
        "ask": "Say it out loud.", "tell": "Here it is.",
        "events": [_event(1.5, "countdown", "q1", 1.5), _event(3.0, "answer", "q1")],
    }


def _hindi_true_false() -> dict[str, Any]:
    return {
        "kind": "quiz", "id": "ck-hi-tf", "title": "जाँच",
        "question": "[[बंद परिपथ]] के बिना धारा नहीं बहती।",
        "options": ["सही", "गलत"], "correct": 0,
        "explanation": "सही: आवेश को लौटने का रास्ता चाहिए।",
        "ask": "सही या गलत?", "reveal": "सही है।",
    }


def _hindi_recall() -> dict[str, Any]:
    return {
        "kind": "board", "id": "ck-hi-recall", "title": "आपकी बारी",
        "question": "[[प्रतिरोध]] किसे सीमित करता है?",
        "answer": "परिपथ की धारा को।",
        "ask": "अब आप उत्तर बोलिए।", "tell": "यही है उत्तर।",
        "events": [_event(1.5, "countdown", "q1", 1.5), _event(3.0, "answer", "q1")],
    }


def fixtures() -> dict[str, Timeline]:
    """Every check fixture by file name. Each fits the slow gate's 300 frames closely enough that the gate sees both
    scenes and the crossfade between them."""
    return {
        f"{TEMPLATE}.landscape.json": _build("landscape", "en", "deep_teal", "crisp", "marker", "outfit",
                                             [_mcq(), _free_recall()]),
        f"{TEMPLATE}.landscape.light.json": _build("landscape", "ta", "paper_ink", "crisp", "marker", "poppins",
                                                   [_tamil_true_false(), _mixed_apply()]),
        f"{TEMPLATE}.portrait.json": _build("portrait", "en", "midnight_indigo", "soft", "underline", "poppins",
                                            [_reel_true_false(), _reel_recall()]),
        f"{TEMPLATE}.portrait.hindi.json": _build("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                  [_hindi_true_false(), _hindi_recall()]),
    }


def render(tl: Timeline) -> str:
    """The file text of a fixture (``Timeline.model_dump(mode="json")``, sorted keys, 1-space indent, final newline)."""
    return json.dumps(tl.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def stale(directory: Path = FIXTURE_DIR) -> list[str]:
    """The fixture files that are missing or differ from what :func:`fixtures` builds."""
    out = []
    for name, tl in fixtures().items():
        path = directory / name
        if not path.is_file() or path.read_text("utf-8") != render(tl):
            out.append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a fixture file is stale")
    args = parser.parse_args(argv)
    if args.check:
        bad = stale()
        for name in bad:
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_check_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
