"""The ``predict`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and what
makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot) and the files can be rebuilt and
checked byte for byte::

    python -m tests.compose.dynamic.make_predict_fixtures           # write the files
    python -m tests.compose.dynamic.make_predict_fixtures --check   # exit 1 when a file is stale

The four timelines cover what the B2 plan §G gallery asks of a template, in both formats, in a dark and a light family
each, with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there). Every
scene is a ``predict``: one blank ``question`` (R-62: its answer lives in the NEXT explaining scene, never here), at
most a ``heading`` above it, "Pause and predict" and the think ring.

* ``predict.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85, marker cue): a 16:9 prediction with its
  heading label and a ``[[term]]`` taking ``cue_term``, its think time from design §1.4's formula (no ``countdown``
  event); then (entered by a crossfade) a prediction with TeX in the question whose ring a ``countdown`` event times,
  and whose look carries ``refs.prediction`` - the data the "Your prediction?" chip of the following explaining scene
  is built from (``predict.js buildChip``; the template itself never draws it, a host does, so the scene looks the
  same with it).
* ``predict.landscape.tamil.json`` (Paper / Crisp, light, ta-IN, Poppins + Anek): a Tamil prediction with no heading,
  then a mixed Tamil + English + ``$TeX$`` one in wide capitals.
* ``predict.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, underline cue, Poppins): the 9:16 prediction, its
  ring timed by a ``countdown`` event, with ``cue_term`` on the question's term.
* ``predict.portrait.hindi.json`` (Mint / Soft, light, hi-IN): a Hindi 9:16 prediction, then (crossfade) the mixed
  Tamil + English + TeX prediction as a reel.

Each file is at most about 10 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees every
scene and crossfade; the scenes are therefore shorter than §4.3's 6-12 s prediction and the think times are clamped to
what is left of their scene. The narration is placeholder text, never the question verbatim (R-25)."""

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

TEMPLATE = "predict"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# A question in wide capitals, inside R-25's 8 words per item and short enough for the 9:16 card at its floor
# (measured on the page: two lines at 56 px in the 540 px text box).
WIDE_QUESTION = "WILL THE LAMP GLOW BRIGHTER?"


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


def _item(item_id: str, kind: str, text: str, *, blank: bool = False, level: str | None = None) -> dict[str, Any]:
    """One board item. The ``question`` of a prediction is ``blank``: §4.3 withholds its answer until the explaining
    scene, so nothing in this scene ever fills it (R-62)."""
    out: dict[str, Any] = {"id": item_id, "kind": kind, "text": text}
    if blank:
        out["blank"] = True
    if level is not None:
        out["level"] = level
    return out


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None) -> dict[str, Any]:
    speech_end = round(start + length - 0.4, 3)
    return {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int,
           transition: str) -> dict[str, Any]:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, narration)], beat_len, hold, events,
    refs}."""
    sid = spec["id"]
    beats = []
    t = 0.5
    for k, (item, text) in enumerate(spec["beats"]):
        beats.append(_beat(sid, k, t, spec["beat_len"], text, item))
        t += spec["beat_len"]
    # the think hold after the last beat: the ring runs into it (R-16 "think"), so the scene is longer than its speech
    duration = round(t + spec.get("hold", 1.6), 3)
    family = pr.family_for(family_id)
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    look: dict[str, Any] = {
        "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
        # a prediction holds to think (R-16), and the resolver freezes the background of a scene with a hold
        # throughout (R-55, presentation.resolve_looks: frozen_at 0.0)
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 8000 + index, "frozen_at": 0.0},
        "transition_in": {"kind": transition, "seconds": seconds},
        "hold_reason": "think",
    }
    if spec.get("refs") is not None:
        look["refs"] = spec["refs"]
    return {
        "scene_id": sid, "index": index, "type": spec.get("type", "content"), "title": spec["title"],
        "start": round(start, 3), "duration": duration, "audio_offset": 0.5, "audio_duration": round(t - 0.5, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []), "look": look,
    }


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              specs: list[dict[str, Any]]) -> Timeline:
    scenes = []
    start = 0.0
    for i, spec in enumerate(specs):
        scene = _scene(spec, index=i, start=start, family_id=family_id, slot=i,
                       transition="cut" if i == 0 else "crossfade")
        scenes.append(scene)
        start = round(start + scene["duration"], 3)
    pres = _presentation(fmt, family_id, personality, cue_style, display)
    pres["generated_seconds"] = start
    width, height = SIZES[fmt]
    captions = [{"start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3), "text": c["text"]}
                for s in scenes for b in s["beats"] for c in b["captions"]]
    data = {
        "schema_version": 3, "language": LANGS[lang], "board_language": LANGS[lang], "fps": 30, "width": width,
        "height": height, "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                                   "session_number": "Session 3", "session_title": "Circuits"},
        "total_duration": start, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": captions, "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


def _cue(t: float, kind: str, target: str, dur: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


# --- the scenes -----------------------------------------------------------------------------------------------------


def _full(sid: str, beat_len: float) -> dict[str, Any]:
    """§4.3's 16:9 prediction: a heading label, one blank question with a key term, and the think ring."""
    items = [
        _item("h1", "heading", "Before we explain"),
        _item("q1", "question", "What happens to the current if the [[resistance]] doubles?",
              blank=True, level="apply"),
    ]
    beats = [
        ("h1", "Here is something to think about first."),
        ("q1", "Make your prediction before we work it out."),
    ]
    return {"id": sid, "title": "Predict the current", "beat_len": beat_len, "items": items, "beats": beats}


def _tex(sid: str, beat_len: float) -> dict[str, Any]:
    """A prediction with TeX in the question, and the chip's data on its look (``refs.prediction``)."""
    items = [_item("q1", "question", "If $V$ stays the same and $R$ doubles, what does $I$ do?",
                   blank=True, level="transfer")]
    beats = [("q1", "Say it out loud before the next scene.")]
    return {
        "id": sid, "title": "Predict the ratio", "beat_len": beat_len, "items": items, "beats": beats,
        # the "Your prediction?" chip the following explaining scene carries (§4.3; a host calls predict.js buildChip)
        "refs": {"prediction": {"text": "If $V$ stays the same and $R$ doubles, what does $I$ do?",
                                "resolve_t": 2.4}},
    }


def _tamil(sid: str, beat_len: float) -> dict[str, Any]:
    items = [_item("q1", "question", "[[மின்தடை]] இரட்டித்தால் மின்னோட்டம் என்ன ஆகும்?", blank=True, level="apply")]
    beats = [("q1", "பதில் சொல்வதற்கு முன் யோசியுங்கள்.")]
    return {"id": sid, "title": "முன்னுரை", "beat_len": beat_len, "items": items, "beats": beats}


def _mixed(sid: str, beat_len: float, *, reel: bool = False) -> dict[str, Any]:
    """Tamil, an English term and TeX in one question; the reel gets the wide-capitals question instead, which a 9:16
    card holds in two lines at its floor (preflight would block a longer one with "Shorten text")."""
    text = WIDE_QUESTION if reel else "[[current]] எப்படி மாறும், $R$ இரட்டித்தால்?"
    items = [_item("q1", "question", text, blank=True, level="transfer")]
    beats = [("q1", "இதை முதலில் யூகியுங்கள்." if not reel else "Guess before we show it.")]
    return {"id": sid, "title": "ஓம் விதி", "beat_len": beat_len, "items": items, "beats": beats}


def _reel(sid: str, beat_len: float) -> dict[str, Any]:
    """§4.3's 9:16 prediction: the question card, "Pause and predict" and the ring in the text column."""
    items = [
        _item("h1", "heading", "Predict first"),
        _item("q1", "question", "Does a thinner wire carry [[more current]]?", blank=True, level="apply"),
    ]
    beats = [
        ("h1", "One question before the answer."),
        ("q1", "Pause here and decide."),
    ]
    return {"id": sid, "title": "Predict the wire", "beat_len": beat_len, "items": items, "beats": beats}


def _hindi(sid: str, beat_len: float) -> dict[str, Any]:
    items = [_item("q1", "question", "[[प्रतिरोध]] दोगुना हो तो धारा का क्या होगा?", blank=True, level="apply")]
    beats = [("q1", "उत्तर से पहले सोचिए।")]
    return {"id": sid, "title": "पहले सोचिए", "beat_len": beat_len, "items": items, "beats": beats}


def fixtures() -> dict[str, Timeline]:
    """Every predict fixture by file name. Each fits the slow gate's 300 frames closely enough that the gate sees
    every scene, the crossfades included."""
    land = _full("pd-l-full", 1.3)         # 0.5 + 2x1.3 + 1.6 = 4.7 s
    # the term takes cue.term; the think ring is design §1.4's formula (no countdown event), clamped to the scene
    land["events"] = [_cue(1.9, "cue_term", "q1:term:0", 1.2)]
    tex = _tex("pd-l-tex", 1.4)            # 0.5 + 1.4 + 2.0 = 3.9 s
    tex["hold"] = 2.0
    # the ring is timed by a countdown event (it starts and ends where the event says, not at the entrance)
    tex["events"] = [_cue(1.5, "countdown", "q1", 2.0)]

    tamil = _tamil("pd-ta-pred", 1.4)      # 3.9 s
    tamil["hold"] = 2.0
    tamil["events"] = [_cue(1.4, "cue_term", "q1:term:0", 1.0)]
    mixed_ta = _mixed("pd-ta-mixed", 1.3)  # 3.4 s
    mixed_ta["hold"] = 1.6
    mixed_ta["events"] = [_cue(1.2, "cue_term", "q1", 1.0)]

    reel = _reel("pd-p-reel", 1.5)         # 0.5 + 2x1.5 + 2.0 = 5.5 s
    reel["hold"] = 2.0
    reel["events"] = [_cue(2.3, "countdown", "q1", 2.6), _cue(2.4, "cue_term", "q1:term:0", 1.2)]

    hindi = _hindi("pd-hi-pred", 1.4)      # 3.9 s
    hindi["hold"] = 2.0
    hindi["events"] = [_cue(1.4, "cue_term", "q1:term:0", 1.0)]
    mixed_hi = _mixed("pd-hi-mixed", 1.3, reel=True)   # 3.4 s
    mixed_hi["hold"] = 1.6
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit",
                                                [land, tex]),
        f"{TEMPLATE}.landscape.tamil.json": _timeline("landscape", "ta", "paper_ink", "crisp", "marker", "poppins",
                                                      [tamil, mixed_ta]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "poppins",
                                               [reel]),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                     [hindi, mixed_hi]),
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a fixture file is stale")
    args = parser.parse_args(argv)
    if args.check:
        bad = stale()
        for name in bad:
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_predict_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
