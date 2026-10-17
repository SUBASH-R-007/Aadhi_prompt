"""Write the ``kinetic_statement`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a template
fixture is): ``tests/compose/dynamic/fixtures/templates/kinetic_statement.<format>[.<case>].json``.

Each fixture is a real Dynamic timeline: the screenplay below goes through the timeline builder (``build_timeline``,
the synthetic narration of ``tests/compose/factories.py``: every beat speaks for one second, its words evenly spaced),
and the director (``direct.plan``) chooses every scene's template and writes its visual events (``events.py``: the
item's ``reveal`` on its beat, R-17, and a ``cue_term`` on each narrated mention of a ``[[term]]``, R-35), so the
looks, tokens, backgrounds and events are the pipeline's own. Two things are held out so every statement scene is
this template's: Aadhi's synthesised intro (``dynamic_aadhi_intro=False``; its own fixtures are make_aadhi_fixtures),
and ``outro_next`` (:func:`_statements_only`: a lecture's last closing takeaway, paragraph or heading is §4.4's
positional outro, which these statement cases are not about; outro_next has its own fixtures).

The statement is narrated verbatim (R-31: the on-screen text IS the caption) and split over several beats, so its
phrases land over a few seconds, each on its own spoken word (``kinetic_statement.js phraseTimes``).

Cases, each in both formats (B2 plan §G: both formats, a dark and a light family, both text entrances, Latin and two
Indic scripts, TeX, the R-25 limits and past them, a very long word, every part the template draws):

* ``kinetic_statement.<format>.json``: typical - a dark family with Soft (midnight_indigo: ``fade_rise``, marker
  cues). 16:9: a takeaway with a ``[[term]]`` (§4.3's twelve words), then a quote past them (thirteen words, scored
  FIT_STRETCHED) whose attribution the narration names. 9:16: the reel's Hook act (§2.3) - a ``hook_question`` and then
  the promise (``kinetic_statement``), set in the hook band the hook just held.
* ``.light``: a light family with Crisp (paper_ink: ``baseline_reveal``, underline cues). 16:9: a ``callout_warning``
  (its "Watch out" label) and a paragraph with two terms (so its cue names one: ``<item>:term:<n>``) and TeX the
  narration cannot say verbatim. 9:16: a Tamil takeaway that
  OPENS the reel (frame 0 is the feed cover: set whole, R-03), then a Hindi ``callout_tip``.
* ``.stress``: a dark family with Crisp (deep_teal). 16:9: a heading-kind statement holding one very long word, then
  an eighteen-word paragraph (MAX_WORDS, set in four lines at the floor). 9:16: a nine-word statement (MAX_WORDS) in
  wide capitals, then a quote with TeX and a two-word attribution.

Run ``python -m tests.compose.dynamic.make_kinetic_statement_fixtures`` to write them, ``--check`` to compare (exit 1
on a difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aadhi.compose.dynamic import direct
from aadhi.compose.dynamic import templates as tp
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR
from tests.compose.factories import beat, make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

TEMPLATE = "kinetic_statement"

Scene = dict[str, Any]


def _statement(sid: str, title: str, item: dict[str, Any], narration: list[str], *, hold: float) -> Scene:
    """A statement scene: one board item, narrated verbatim over ``narration``'s beats (the first reveals it), then a
    pause so the scene holds after the last phrase lands."""
    beats = [beat(f"{sid}-b{k + 1}", line, **({"board_item_id": item["id"]} if k == 0 else {}))
             for k, line in enumerate(narration)]
    beats[-1]["pause_after"] = hold
    return {"id": sid, "type": "content", "title": title, "concept_id": "ohm",
            "intent": {"narrative_role": "concept", "visual_intent": "explain"},
            "board": [item], "beats": beats}


def _hook(sid: str, title: str, question: str, *, hold: float) -> Scene:
    """The reel's hook (``hook_question``): one question, said as written."""
    return {"id": sid, "type": "content", "title": title, "concept_id": "ohm",
            "intent": {"narrative_role": "hook", "visual_intent": "hook"},
            "board": [{"id": "q", "kind": "question", "text": question}],
            "beats": [beat(f"{sid}-b1", question.replace("[[", "").replace("]]", ""), board_item_id="q",
                           pause_after=hold)]}


# --- 16:9 ------------------------------------------------------------------------------------------------------------

TYPICAL_LANDSCAPE = [
    _statement("ks-energy", "Conservation of energy",
               {"id": "t", "kind": "takeaway", "text": "Energy is never [[created]] or destroyed, only changed in form."},
               ["Energy is never created", "or destroyed,", "only changed in form."], hold=0.5),
    _statement("ks-curie", "Understanding",
               {"id": "qt", "kind": "quote", "caption": "Marie Curie",
                "text": "Nothing in life is to be feared, it is only to be understood."},
               ["Nothing in life is to be feared,", "it is only to be understood.", "Marie Curie."], hold=0.4),
]

LIGHT_LANDSCAPE = [
    _statement("ks-live", "Safety first",
               {"id": "w", "kind": "callout_warning", "text": "Never touch a [[live]] wire with wet hands."},
               ["Never touch a live wire", "with wet hands."], hold=0.8),
    _statement("ks-power", "Power and current",
               {"id": "p", "kind": "paragraph",
                "text": "[[Power]] grows with the square of the [[current]]: $P = I^2 R$."},
               ["Power grows with the square", "of the current:", "P equals I squared R."], hold=0.5),
]

STRESS_LANDSCAPE = [
    _statement("ks-longword", "One long word",
               {"id": "hd", "kind": "heading", "text": "Supercalifragilisticexpialidocious circuits still obey Ohm"},
               ["Supercalifragilisticexpialidocious circuits", "still obey Ohm."], hold=0.6),
    # MAX_WORDS["landscape"] (18) words: four lines at the 100 px floor (measured with textfit: 19-22 fit)
    _statement("ks-long", "Every word counts",
               {"id": "pp", "kind": "paragraph",
                "text": "A wire warms as current flows, and the warmth we feel is energy that the circuit gave away."},
               ["A wire warms as current flows,", "and the warmth we feel", "is energy that the circuit gave away."],
               hold=0.3),
]

# --- 9:16 ------------------------------------------------------------------------------------------------------------

TYPICAL_PORTRAIT = [
    _hook("ks-hook", "Why bulbs glow", "Why does a [[bulb]] glow?", hold=0.9),
    # the reel's promise (§2.3 Hook act: "then a 3-5 s promise"), eight words (§4.3's 9:16 limit)
    _statement("ks-promise", "The promise",
               {"id": "t", "kind": "takeaway", "text": "By the end, you will [[predict]] its current."},
               ["By the end, you will", "predict its current."], hold=0.4),
]

LIGHT_PORTRAIT = [
    # opens the reel: frame 0 is the feed cover, so the statement is set whole there (R-03)
    _statement("ks-tamil", "மின்னோட்டம்",
               {"id": "t", "kind": "takeaway", "text": "மின்னழுத்தம் கூடினால் [[மின்னோட்டம்]] கூடும்"},
               ["மின்னழுத்தம் கூடினால்", "மின்னோட்டம் கூடும்"], hold=0.4),
    _statement("ks-hindi", "प्रतिरोध",
               {"id": "c", "kind": "callout_tip", "text": "हर तार में [[प्रतिरोध]] होता है"},
               ["हर तार में", "प्रतिरोध होता है"], hold=0.4),
]

STRESS_PORTRAIT = [
    # MAX_WORDS["portrait"] (9) words, in wide capitals: past §4.3's eight, scored FIT_STRETCHED
    _statement("ks-caps", "Big idea",
               {"id": "t", "kind": "takeaway", "text": "MORE VOLTS MEAN MORE AMPS IN THE SAME WIRE"},
               ["More volts mean more amps", "in the same wire."], hold=0.3),
    _statement("ks-ohm", "Ohm's words",
               {"id": "qt", "kind": "quote", "caption": "Georg Ohm", "text": "Current follows $V / R$, always."},
               ["Current follows V over R, always.", "Georg Ohm."], hold=0.3),
]

# (16:9 scenes, 9:16 scenes, family, personality, board language)
CASES: dict[str, tuple[list[Scene], list[Scene], str, str, str]] = {
    "": (TYPICAL_LANDSCAPE, TYPICAL_PORTRAIT, "midnight_indigo", "soft", "en-IN"),
    "light": (LIGHT_LANDSCAPE, LIGHT_PORTRAIT, "paper_ink", "crisp", "en-IN"),
    "stress": (STRESS_LANDSCAPE, STRESS_PORTRAIT, "deep_teal", "crisp", "en-IN"),
}


@contextmanager
def _statements_only() -> Iterator[None]:
    """The director without ``outro_next``, so a case's last statement stays a kinetic_statement scene."""
    spec = dataclasses.replace(tp.TEMPLATES["outro_next"], implemented=False)
    built = {**tp.TEMPLATES, "outro_next": spec}
    saved = (tp.TEMPLATES, direct.TEMPLATES)
    tp.TEMPLATES = built  # type: ignore[misc]
    direct.TEMPLATES = built  # type: ignore[misc]
    try:
        yield
    finally:
        tp.TEMPLATES, direct.TEMPLATES = saved  # type: ignore[misc]


def build(case: str, fmt: str) -> dict[str, Any]:
    """The fixture timeline of ``case`` in ``fmt`` (JSON-ready)."""
    landscape, portrait, family, personality, language = CASES[case]
    data = screenplay_dict()
    data["language"] = language
    data["learning_objectives"] = []
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": family,
                            "personality": personality, "hook_kind": "question",
                            "cue_term_style": "marker" if personality == "soft" else "underline",
                            "asset_style": {"family": family, "personality": personality}, "captions_burn": True,
                            "music": {"source": "procedural", "provider": "procedural"}}
    data["scenes"] = json.loads(json.dumps(landscape if fmt == "landscape" else portrait))
    sp = Screenplay.model_validate(data)
    with _statements_only():
        tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True,
                                                                    dynamic_aadhi_intro=False))
    out = Timeline.model_validate(tl.model_dump(mode="json")).model_dump(mode="json")
    for s in out["scenes"]:  # the director chose what each scene is written for
        want = "hook_question" if s["scene_id"] == "ks-hook" else TEMPLATE
        assert s["look"]["template"] == want, (s["scene_id"], s["look"]["template"])
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
        print(f"stale kinetic_statement fixtures: {', '.join(stale)} (run python -m tests.compose.dynamic."
              "make_kinetic_statement_fixtures)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
