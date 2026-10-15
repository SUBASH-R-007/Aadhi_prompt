"""The ``recap_retrieval`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and
what makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot, the stored icons) and the files can
be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.make_recap_retrieval_fixtures           # write the files
    python -m tests.compose.dynamic.make_recap_retrieval_fixtures --check   # exit 1 when a file is stale

The four timelines cover what the B2 plan §G gallery asks of a template, in both formats, in a dark and a light family
each, with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there):

* ``recap_retrieval.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85, marker cue): the full 16:9 recap — a
  label, two retrieval prompts (the first answered by the beat that fills it, the second by an ``answer`` event), a
  free-recall prompt and three big-idea tiles, each with a stored icon; ``cue_term`` on a prompt's term and on a
  tile's.
* ``recap_retrieval.landscape.tamil.json`` (Paper / Crisp, light, ta-IN, Poppins + Anek): a Tamil recap with three
  tiles, then (entered by a crossfade) a mixed Tamil + English + ``$TeX$`` recap whose think time is too short for a
  ring and whose tiles carry no icon (so none is drawn, B2c review V12); ``cue_term`` on a Tamil and on an English
  term, and the lint-limit longest tile in wide capitals with a word longer than any line.
* ``recap_retrieval.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, underline cue, Poppins): the 9:16 recap —
  one retrieval prompt whose ring a ``countdown`` event times, a free-recall prompt and three stacked tiles with
  icons; ``cue_term`` on the prompt's term.
* ``recap_retrieval.portrait.hindi.json`` (Mint / Soft, light, hi-IN): a Hindi 9:16 recap, then (crossfade) the mixed
  Tamil + English + TeX recap as a reel.

Each file is at most about 10 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees every
scene and crossfade; the think times are therefore far shorter than §4.3's 25-40 s recap. The narration is placeholder
text, never the prompts or tiles verbatim (R-25)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, icons, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "recap_retrieval"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# The lint-limit tile (R-25: <= 8 words per item) in wide capitals, and a word longer than any tile is wide even at the
# R-29 floor.
# wide glyphs in a tile; short enough for the 9:16 tile (420 px, two lines at <= 44 px), measured on the page
WIDE_TILE = "MAXIMUM WATTAGE MEETS LOAD"
LONG_WORD_TILE = "Pneumonoultramicroscopicsilicovolcanoconiosis is one word"


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


def _item(item_id: str, kind: str, text: str, *, answer: str | None = None, level: str | None = None,
          hint: str | None = None) -> dict[str, Any]:
    """One board item. A ``question`` with an ``answer`` is a retrieval prompt (``blank``: the answer is withheld
    until a later beat fills it, §4.3); without one it is the free-recall prompt."""
    out: dict[str, Any] = {"id": item_id, "kind": kind, "text": text}
    if answer is not None:
        out["justification"] = answer
        out["blank"] = True
    if level is not None:
        out["level"] = level
    if hint is not None:
        out["icon_hint"] = hint
    return out


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None,
          fill: str | None = None) -> dict[str, Any]:
    speech_end = round(start + length - 0.4, 3)
    out = {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }
    if fill is not None:
        out["fill_item_id"] = fill
    return out


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int,
           transition: str) -> dict[str, Any]:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, fill | None, narration)], beat_len,
    events}."""
    sid = spec["id"]
    beats = []
    t = 0.5
    for k, (item, fill, text) in enumerate(spec["beats"]):
        beats.append(_beat(sid, k, t, spec["beat_len"], text, item, fill))
        t += spec["beat_len"]
    duration = round(t + 0.8, 3)
    family = pr.family_for(family_id)
    look_icons = icons.scene_icons(spec["items"])
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    return {
        "scene_id": sid, "index": index, "type": "recap", "title": spec["title"], "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(t - 0.5, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []),
        "look": {
            "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 7000 + index,
                           "frozen_at": None},
            "transition_in": {"kind": transition, "seconds": seconds},
            "icons": {k: v.model_dump(mode="json") for k, v in look_icons.items()},
        },
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
    """§4.3's 16:9 recap: a label, two retrieval prompts, a free-recall prompt and three tiles with icons."""
    items = [
        _item("h1", "heading", "Retrieval check"),
        _item("q1", "question", "Why does a bulb glow brighter on a [[higher voltage]]?",
              answer="More voltage pushes more current through the same resistance.", level="apply"),
        _item("q2", "question", "A heater draws $I = V / R$. What if $R$ doubles?",
              answer="The current halves, so the heat it gives out falls.", level="transfer"),
        _item("r1", "question", "Name the big ideas of this session.", level="recall"),
        _item("t1", "takeaway", "[[Voltage]] pushes charge round a closed loop", hint="battery"),
        _item("t2", "takeaway", "Resistance limits the current a loop carries", hint="shield"),
        _item("t3", "takeaway", "Power is the energy a load turns into work", hint="lightbulb"),
    ]
    beats = [
        ("h1", None, "Before we close, let us test what stuck."),
        ("q1", None, "Here is the first one. Think about it."),
        (None, "q1", "Here is what happens."),
        ("q2", None, "Now a harder one."),
        (None, None, "Take your time with that."),
        ("r1", None, "Last, say them out loud."),
        ("t1", None, "That was the first big idea."),
        ("t2", None, "That was the second."),
        ("t3", None, "And that was the third."),
    ]
    return {"id": sid, "title": "Retrieval check", "beat_len": beat_len, "items": items,
            "beats": [(a, b, c) for a, b, c in beats]}


def _tamil(sid: str, beat_len: float) -> dict[str, Any]:
    items = [
        _item("h1", "heading", "நினைவுச் சோதனை"),
        _item("q1", "question", "[[மின்னழுத்தம்]] எதைத் தள்ளுகிறது?",
              answer="அது மின்னூட்டத்தைத் தள்ளுகிறது.", level="apply"),
        _item("r1", "question", "முக்கியக் கருத்துகளைச் சொல்லுங்கள்.", level="recall"),
        _item("t1", "takeaway", "மின்னழுத்தம் மின்னூட்டத்தைத் தள்ளுகிறது", hint="battery"),
        _item("t2", "takeaway", "மின்தடை மின்னோட்டத்தைக் கட்டுப்படுத்துகிறது", hint="shield"),
        _item("t3", "takeaway", "விளக்கு ஆற்றலைப் பயன்படுத்துகிறது", hint="lightbulb"),
    ]
    beats = [
        ("h1", None, "முடிக்கும் முன் ஒரு சோதனை."),
        ("q1", None, "முதல் கேள்வி. யோசியுங்கள்."),
        (None, "q1", "இதோ பதில்."),
        ("r1", None, "இப்போது நீங்களே சொல்லுங்கள்."),
        ("t1", None, "முதல் கருத்து."),
        ("t2", None, "இரண்டாவது."),
        ("t3", None, "மூன்றாவது."),
    ]
    return {"id": sid, "title": "நினைவுச் சோதனை", "beat_len": beat_len, "items": items, "beats": beats}


def _mixed(sid: str, beat_len: float, *, reel: bool = False) -> dict[str, Any]:
    """Tamil, English terms and TeX in one recap, with the stress tiles and no icon at all (V12: none is drawn).
    ``reel``: a 9:16 tile holds 2 lines of at most 44 px, so the over-wide-word tile (which a 16:9 tile fits in three
    40 px lines) becomes a shorter one there; preflight would otherwise block the fixture, "Shorten text"."""
    items = [
        _item("q1", "question", "[[current]] என்றால் $I = V / R$ எப்படி?",
              answer="$R$ இரட்டித்தால் $I$ பாதி.", level="transfer"),
        _item("t1", "takeaway", WIDE_TILE),
        _item("t2", "takeaway", "ஒரு நீண்ட சொல்: ஓம்" if reel else LONG_WORD_TILE),
    ]
    beats = [
        ("q1", None, "ஒரு கேள்வி."),
        (None, "q1", "இதோ பதில்."),
        ("t1", None, "முதல் கருத்து."),
        ("t2", None, "இரண்டாவது."),
    ]
    return {"id": sid, "title": "ஓம் விதி", "beat_len": beat_len, "items": items, "beats": beats}


def _reel(sid: str, beat_len: float) -> dict[str, Any]:
    """§4.3's 9:16 recap: one question, free recall, three stacked tiles."""
    items = [
        _item("h1", "heading", "Retrieval check"),
        _item("q1", "question", "What does a [[closed loop]] let charge do?",
              answer="Travel right round, back to the source.", level="apply"),
        _item("r1", "question", "Say the big ideas out loud.", level="recall"),
        _item("t1", "takeaway", "Voltage pushes charge", hint="battery"),
        _item("t2", "takeaway", "Resistance limits current", hint="shield"),
        _item("t3", "takeaway", "A load turns energy into work", hint="lightbulb"),
    ]
    beats = [
        ("h1", None, "One quick check before we finish."),
        ("q1", None, "Think about this one."),
        (None, "q1", "Here is the answer."),
        ("r1", None, "Now say them yourself."),
        ("t1", None, "The first idea."),
        ("t2", None, "The second."),
        ("t3", None, "The third."),
    ]
    return {"id": sid, "title": "Retrieval check", "beat_len": beat_len, "items": items, "beats": beats}


def _hindi(sid: str, beat_len: float) -> dict[str, Any]:
    items = [
        _item("h1", "heading", "स्मरण जाँच"),
        _item("q1", "question", "[[बंद परिपथ]] में आवेश क्या कर पाता है?",
              answer="वह पूरा चक्कर लगाकर लौट आता है।", level="apply"),
        _item("r1", "question", "मुख्य विचार बोलकर बताइए।", level="recall"),
        _item("t1", "takeaway", "विद्युत दाब आवेश को धकेलता है"),
        _item("t2", "takeaway", "प्रतिरोध धारा को सीमित करता है"),
        _item("t3", "takeaway", "भार ऊर्जा को कार्य में बदलता है"),
    ]
    beats = [
        ("h1", None, "अंत में एक जाँच।"),
        ("q1", None, "इस पर सोचिए।"),
        (None, "q1", "यह है उत्तर।"),
        ("r1", None, "अब आप बताइए।"),
        ("t1", None, "पहला विचार।"),
        ("t2", None, "दूसरा।"),
        ("t3", None, "तीसरा।"),
    ]
    return {"id": sid, "title": "स्मरण जाँच", "beat_len": beat_len, "items": items, "beats": beats}


def fixtures() -> dict[str, Timeline]:
    """Every recap_retrieval fixture by file name. Each fits the slow gate's 300 frames closely enough that the gate
    sees every scene, the crossfades included."""
    land = _full("rr-l-full", 0.95)  # 9.85 s
    # q2 is answered by an `answer` event (no beat fills it; the template takes the event over its think default), and
    # the terms of a prompt and of a tile take cue.term
    land["events"] = [_cue(1.4, "cue_term", "q1:term:0", 1.2), _cue(4.3, "answer", "q2"),
                      _cue(7.4, "cue_term", "t1", 1.0)]

    tamil = _tamil("rr-ta-recap", 0.7)  # 6.2 s
    tamil["events"] = [_cue(1.1, "cue_term", "q1:term:0", 1.0)]
    mixed_ta = _mixed("rr-ta-mixed", 0.55)  # 3.5 s (its think time is too short for a ring)
    mixed_ta["events"] = [_cue(0.8, "cue_term", "q1", 0.9)]

    reel = _reel("rr-p-reel", 1.1)  # 9.0 s
    # the ring is timed by a countdown event (it starts and ends where the event says, not at the fill)
    reel["events"] = [_cue(1.5, "countdown", "q1", 1.4), _cue(1.6, "cue_term", "q1:term:0", 1.2)]

    hindi = _hindi("rr-hi-recap", 0.8)  # 6.9 s
    hindi["events"] = [_cue(1.2, "cue_term", "q1:term:0", 1.0)]
    mixed_hi = _mixed("rr-hi-mixed", 0.5, reel=True)  # 3.3 s
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit", [land]),
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_recap_retrieval_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
