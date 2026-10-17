"""Write the ``hook_question`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a template
fixture is): ``tests/compose/dynamic/fixtures/templates/hook_question.<format>[.<case>].json``.

Each fixture is a real Dynamic timeline: the screenplay below goes through the timeline builder (``build_timeline``,
the synthetic narration of ``tests/compose/factories.py``), with the resolver allowed to draw ``hook_question`` (it
draws only ``board_generic`` until B2e's direct.py), so the looks, tokens and backgrounds are the pipeline's own. On
top, the visual events events.py (B2d) will write: the question's ``reveal`` on its beat (R-17: 120 ms before the
anchor word) and one ``cue_term`` on its keyword (R-26: at most one new cue per 2 s), as ``<item>`` in one case and
``<item>:term:<n>`` in another. The scene's ``look.variant`` is written here, as direct.py will write it from
``Presentation.hook_kind`` (§6.5): a real lesson has one hook kind, but a fixture carries several variants, so one
slow Chromium gate covers them all.

Cases, each in both formats (B2 plan §G text cases; 9:16 keeps R-25's eight words and the hook band's room, so a few
reels carry a shorter wording of the same hook — ``REEL``):

* ``hook_question.<format>.json``: typical — a dark family with the Soft personality (midnight_indigo, marker cues),
  the ``question`` variant, §4.6's measured eight-word hook with a ``[[term]]``, a ``?`` that pops, a ``cue_term`` on
  the whole item;
* ``.light``: a light family with Crisp (paper_ink, baseline reveal, underline cues), the ``true_false`` variant (the
  "True or false?" pill), a statement with no ``?`` at all, an eyebrow (the scene's ``heading``) in 16:9, Tamil in
  9:16, TeX in 16:9, and a ``cue_term`` on the item's first term;
* ``.stress``: a dark family with Crisp (deep_teal), four short scenes covering the remaining variants and the text
  cases: ``stakes`` at R-25's limits in wide glyphs with an eyebrow, ``challenge`` as one very long word,
  ``mystery`` in Tamil (deviation 16: it draws as a plain question until B4) and ``question`` in Hindi.

Run ``python -m tests.compose.dynamic.make_hook_question_fixtures`` to write them, ``--check`` to compare (exit 1 on a
difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pres
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline, VisualEvent
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR
from tests.compose.factories import beat, make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

TEMPLATE = "hook_question"
LEAD = 0.12  # R-17: a reveal starts 120 ms before its anchor word
# A cue holds CUE_HOLD (2 s) without `dur` (cues.js); the term cue is placed this long before the scene ends, so it is
# inside the scene (TimedScene.check_inside) and well after the words have landed (the hook is set in under 1.5 s).
CUE_BEFORE_END = 1.4
CUE_DUR = 1.0

Scene = dict[str, Any]


def _hook_scene(sid: str, title: str, question: str, narration: list[str], *, heading: str | None = None,
                hold: float = 2.0, term: str | None = None) -> Scene:
    """One hook scene: role ``hook``, one ``question`` item (and at most the opening scene's ``heading``), the beat
    that reveals it, and a pause so the scene holds after the last word (§4.3: >= 0.8 s). ``term``: the cue_term
    target written for it (``q`` or ``q:term:0``; None: no cue)."""
    board: list[dict[str, Any]] = []
    if heading is not None:
        board.append({"id": "h", "kind": "heading", "text": heading})
    board.append({"id": "q", "kind": "question", "text": question})
    beats = [beat(f"{sid}-b{k + 1}", line, **({"board_item_id": "q"} if k == 0 else {}))
             for k, line in enumerate(narration)]
    beats[-1]["pause_after"] = hold
    return {"id": sid, "type": "content", "title": title, "concept_id": "ohm",
            "intent": {"narrative_role": "hook", "visual_intent": "hook"},
            "board": board, "beats": beats, "look": {"template": TEMPLATE},
            # read back by _events / build (not part of the screenplay: popped before validation)
            "_term": term}


# R-31 (the on-screen text IS the caption for this template): the narration says the hook.
TYPICAL = [_hook_scene(
    "hq-moon", "Why the Moon stays up", "Why doesn't the [[Moon]] fall down to Earth?",
    ["Why doesn't the Moon fall down to Earth?"], term="q")]

LIGHT = [_hook_scene(
    "hq-filament", "Current and voltage", "Current in a [[filament]] grows with $V$",
    ["Current in a filament grows with V.", "True or false?"], heading="Ohm's law", hold=1.2,
    term="q:term:0")]

STRESS = [
    # Wide glyphs (W and M) under an eyebrow. Six words, not R-25's eight: measured on the real page, eight wide
    # words do not fit the 16:9 hook box at the 112 px floor (it is 1036 x 470 for three lines), and the template
    # reports the overflow so preflight blocks. R-25's count is a lint concern; the box is the page's.
    _hook_scene("hq-lint", "What warm wires cost", "WHY WARM WIRES WASTE WHOLE WATTS",
                ["Why warm wires waste whole watts."], heading="Stakes", hold=1.8),
    _hook_scene("hq-longword", "One long word", "Supercalifragilisticexpialidocious?",
                ["Can you say this word?"], hold=1.8),
    _hook_scene("hq-tamil", "மின்சாரப் பாதை", "மின்சாரம் எப்படி வீட்டை அடைகிறது?",
                ["மின்சாரம் எப்படி வீட்டை அடைகிறது?"], hold=1.8),
    _hook_scene("hq-hindi", "बिजली का सफ़र", "बिजली घर तक कैसे पहुँचती है?",
                ["बिजली घर तक कैसे पहुँचती है?"], hold=1.8),
]

# The 9:16 wording of a hook whose 16:9 one is too long for R-25's eight words or the hook band's room (the same hook,
# shortened: the band holds four 88-104 px lines, less the pill's or the eyebrow's row). scene id -> (question,
# heading | None to drop it)
REEL: dict[str, tuple[str, str | None]] = {
    # a 9:16 true_false statement in an Indic script holds two lines at the hook's floor under the pill's row
    "hq-filament": ("மின்னோட்டம் [[கூடும்]]", None),
    "hq-lint": ("WHY WARM WIRES WASTE WATTS", "Stakes"),
}
# look.variant per scene (§4.3 / §6.5 hook kinds; direct.py writes it in B2e)
VARIANTS: dict[str, str] = {
    "hq-moon": "question", "hq-filament": "true_false", "hq-lint": "stakes", "hq-longword": "challenge",
    "hq-tamil": "mystery", "hq-hindi": "question",
}

# (scenes, family, personality, board language, Presentation.hook_kind)
CASES: dict[str, tuple[list[Scene], str, str, str, str]] = {
    "": (TYPICAL, "midnight_indigo", "soft", "en-IN", "question"),
    "light": (LIGHT, "paper_ink", "crisp", "en-IN", "true_false"),
    "stress": (STRESS, "deep_teal", "crisp", "en-IN", "stakes"),
}


@contextmanager
def _hook_question_resolved() -> Iterator[None]:
    """Let the resolver draw hook_question (presentation.TEMPLATES; B2e's direct.py will choose it)."""
    saved = pres.TEMPLATES
    pres.TEMPLATES = ("board_generic", TEMPLATE)
    try:
        yield
    finally:
        pres.TEMPLATES = saved


def _events(scene: Any, term: str | None) -> list[VisualEvent]:
    """The events events.py (B2d) writes for a hook_question scene: the question's reveal on its beat, and the
    keyword's cue_term once the hook is set."""
    out = [VisualEvent(t=round(max(0.0, scene.beats[0].start - LEAD), 3), kind="reveal", target="q", anchored=True)]
    if term is not None:
        at = round(max(0.0, float(scene.duration) - CUE_BEFORE_END), 3)
        out.append(VisualEvent(t=at, kind="cue_term", target=term, dur=CUE_DUR, anchored=True))
    return sorted(out, key=lambda e: e.t)


def build(case: str, fmt: str) -> dict[str, Any]:
    """The fixture timeline of ``case`` in ``fmt`` (JSON-ready)."""
    scenes, family, personality, language, hook_kind = CASES[case]
    data = screenplay_dict()
    data["language"] = language
    data["learning_objectives"] = []
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": family,
                            "personality": personality, "hook_kind": hook_kind,
                            "cue_term_style": "marker" if personality == "soft" else "underline",
                            "asset_style": {"family": family, "personality": personality}, "captions_burn": True,
                            "music": {"source": "procedural", "provider": "procedural"}}
    data["scenes"] = json.loads(json.dumps(scenes))
    terms: dict[str, str | None] = {}
    for scene in data["scenes"]:
        terms[scene["id"]] = scene.pop("_term")
        if fmt == "portrait" and scene["id"] in REEL:
            question, heading = REEL[scene["id"]]
            scene["board"] = ([{"id": "h", "kind": "heading", "text": heading}] if heading is not None else []) + [
                {"id": "q", "kind": "question", "text": question}]
            if "[[" not in question:
                terms[scene["id"]] = None
    sp = Screenplay.model_validate(data)
    with _hook_question_resolved():
        # a template fixture: without Aadhi's synthesised appearances (step 5; their own fixtures are make_aadhi_fixtures)
        tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True,
                                                                    dynamic_aadhi_intro=False))
    for s in tl.scenes:
        s.events = _events(s, terms.get(s.scene_id))
        if s.look is not None:
            s.look.variant = VARIANTS[s.scene_id]
    out = Timeline.model_validate(tl.model_dump(mode="json")).model_dump(mode="json")
    for s in out["scenes"]:  # every scene is drawn by the template, in the variant the case gives it
        assert s["look"]["template"] == TEMPLATE, s["scene_id"]
        assert s["look"]["variant"] == VARIANTS[s["scene_id"]], s["scene_id"]
    return out


def fixtures() -> dict[Path, str]:
    """Every fixture file and its text."""
    out: dict[Path, str] = {}
    for case in CASES:
        for fmt in ("landscape", "portrait"):
            name = f"{TEMPLATE}.{fmt}{'.' + case if case else ''}.json"
            out[FIXTURE_DIR / name] = json.dumps(build(case, fmt), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    args = ap.parse_args(argv)
    stale = []
    for path, text in fixtures().items():
        if args.check:
            if not path.is_file() or path.read_text("utf-8") != text:
                stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
    if stale:
        print(f"stale hook_question fixtures: {', '.join(stale)} (run python -m tests.compose.dynamic."
              "make_hook_question_fixtures)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
