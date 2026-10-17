"""Write the ``aadhi_intro`` / ``aadhi_recap`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what
a template fixture is): ``tests/compose/dynamic/fixtures/templates/aadhi_{intro,recap}.<format>[.<case>].json``.

Each fixture is a real Dynamic timeline built the way the integrated pipeline builds one (step 5's requests to
``compose/timeline.py``, ``dynamic/direct.py`` and ``compose/presentation.py``, which call
``aadhi/compose/dynamic/presenter.py``): the lecture's own scenes go through ``build_timeline`` (the synthetic narration
of ``tests/compose/factories.py``) with the synthesised appearances switched off, then the appearances are inserted
as the builder inserts them (``presenter.plan_appearances`` / ``intro_scene`` / ``recap_scene``), the director's plans
are taken for every scene (``direct.plan``, with ``presenter.plan_for`` for the synthesised ones), and the resolver's
looks are finished by ``presenter.apply_to_looks`` (the presenter, its layout, the brand wipe's ``accent_next`` and the
crossfade out of it). So the fixtures carry the pipeline's own tokens, backgrounds, timings and presenter placements.
The clips are the real ones (``video_template/aadhi_left_clean.mp4`` and ``aadhi_center_clean.mp4``): the slow
real-Chromium test ``tests/determinism/test_presenter_pages.py`` serves their frames from the presenter cache.

Cases (B2 plan §G: both formats, dark and light, every layout, the text cases):

* ``aadhi_intro.landscape.json``: ``fullframe`` on the studio wall, a dark family with Soft (midnight_indigo), the
  synthesised intro (chip and title, silent) before a list scene;
* ``.light``: ``split`` (the colour panel), a light family with Crisp (paper_ink), a Tamil lecture;
* ``.hook``: ``pip`` with a hook question (its large text), a dark family with Crisp (deep_teal), the question revealed
  at Aadhi's speech start and its keyword's ``cue_term`` (``<item>:term:0``): the shape a B4 screenplay's own presenter
  scene has (here the synthesised scene with a question added, so no narration is needed);
* ``.card``: ``card_left``, a light family with Soft (mint_studio), a long session title (92 characters, set whole
  in four lines: the intro never cuts it, step-5 review R5-5) and a chip that leaves the subject out;
* ``aadhi_intro.portrait.json``: the 9:16 card, the title as the headline set on frame 0 (midnight_indigo, Soft);
* ``aadhi_intro.portrait.hook.json``: the 9:16 card, a Hindi hook question as the headline and the title as its eyebrow
  (paper_ink, Crisp), the hook's keyword cued;
* ``aadhi_recap.landscape.json``: the intro, the synthesised 16:9 recap (Aadhi's card and the "Quick check" card with
  three empty slots) and the retrieval recap it opens (deep_teal, Soft); a recap fixture has no other scene, so the
  recap lies inside the first 300 frames the real-Chromium order gate renders;
* ``aadhi_recap.portrait.json``: the same in 9:16 (the prompt in the hook band; §3.4 keeps the 9:16 recap off by
  default, so this fixture inserts it itself: the shape a 9:16 screenplay's own presenter recap scene draws).

Run ``python -m tests.compose.dynamic.make_aadhi_fixtures`` to write them, ``--check`` to compare (exit 1 on a
difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from aadhi.compose.captions import normalize_cues, shift_cues
from aadhi.compose.dynamic import presenter
from aadhi.compose.dynamic.direct import ScenePlan
from aadhi.compose.dynamic.direct import plan as direct_plan
from aadhi.compose.presentation import resolve_looks, timeline_presentation
from aadhi.compose.timeline import build_chapters, build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import BoardItem, BoardItemKind, Screenplay
from aadhi.schemas.timeline import CaptionCue, TimedScene, Timeline, VisualEvent
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR
from tests.compose.factories import beat, make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

LEAD = 0.12  # R-17: a reveal starts 120 ms before its anchor (here: Aadhi's speech start)
CUE_AT = 2.6  # the hook keyword's cue_term, once the question has landed
CUE_DUR = 1.0

# --- the lecture's own scenes ------------------------------------------------------------------------------------------


def _list_scene(sid: str, lang: str) -> dict[str, Any]:
    text = {
        "en": ("Three facts about current", ["Current is a flow of charge", "It is measured in amperes",
                                             "It needs a closed loop"]),
        "ta": ("மின்னோட்டம் பற்றி மூன்று உண்மைகள்", ["மின்னோட்டம் என்பது மின்னூட்ட ஓட்டம்",
                                                   "இது ஆம்பியரில் அளக்கப்படுகிறது", "இதற்கு மூடிய சுற்று தேவை"]),
        "hi": ("धारा के बारे में तीन बातें", ["धारा आवेश का प्रवाह है", "इसे एम्पियर में मापते हैं",
                                          "इसके लिए बंद परिपथ चाहिए"]),
    }[lang]
    board = [{"id": f"b{k + 1}", "kind": "bullet", "text": line} for k, line in enumerate(text[1])]
    beats = [beat(f"{sid}-b{k + 1}", line, board_item_id=f"b{k + 1}") for k, line in enumerate(text[1])]
    return {"id": sid, "type": "content", "title": text[0], "concept_id": "ohm", "board": board, "beats": beats}


def _recap_scene(sid: str) -> dict[str, Any]:
    return {
        "id": sid, "type": "recap", "title": "Recap", "concept_id": "ohm",
        "board": [{"id": "q1", "kind": "question", "text": "What makes current flow?", "blank": True},
                  {"id": "t1", "kind": "takeaway", "text": "Voltage pushes"},
                  {"id": "t2", "kind": "takeaway", "text": "Current flows"},
                  {"id": "t3", "kind": "takeaway", "text": "Resistance limits"}],
        "beats": [beat(f"{sid}-b1", "What makes current flow? Think about it.", board_item_id="q1", pause_after=1.0),
                  beat(f"{sid}-b2", "Voltage pushes, current flows, resistance limits.", board_item_id="t1")],
    }


# (format, case) -> what the lecture is: family, personality, board language, intro_layout, metadata overrides, the
# hook question added to the intro (None: the silent synthesised intro), and whether a recap appearance is inserted
CASES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("aadhi_intro", "landscape", ""): {"family": "midnight_indigo", "personality": "soft", "lang": "en",
                                       "layout": "fullframe"},
    ("aadhi_intro", "landscape", "light"): {
        "family": "paper_ink", "personality": "crisp", "lang": "ta", "layout": "split",
        "meta": {"subject_name": "அடிப்படை மின் பொறியியல்", "unit_name": "மின்சுற்றுகள்", "session_number": "அமர்வு 2",
                 "session_title": "ஓமின் விதி: மின்னோட்டம் மற்றும் மின்தடை"}},
    ("aadhi_intro", "landscape", "hook"): {
        "family": "deep_teal", "personality": "crisp", "lang": "en", "layout": "pip",
        "hook": "Why doesn't a [[fuse]] melt every single day?"},
    ("aadhi_intro", "landscape", "card"): {
        "family": "mint_studio", "personality": "soft", "lang": "en", "layout": "card_left",
        "meta": {"subject_name": "Basic Electrical and Electronics Engineering for First Year Students",
                 "unit_name": "Unit 2: DC Circuits", "session_number": "Session 3",
                 "session_title": ("Ohm's Law, Kirchhoff's Laws and the Rules That Quietly Run Every Circuit in "
                                   "Your Whole House")}},
    ("aadhi_intro", "portrait", ""): {"family": "midnight_indigo", "personality": "soft", "lang": "en"},
    ("aadhi_intro", "portrait", "hook"): {
        "family": "paper_ink", "personality": "crisp", "lang": "hi",
        "meta": {"subject_name": "विद्युत अभियांत्रिकी", "unit_name": "परिपथ", "session_number": "सत्र 2",
                 "session_title": "ओम का नियम"},
        "hook": "[[फ़्यूज़]] रोज़ क्यों नहीं पिघलता?"},
    ("aadhi_recap", "landscape", ""): {"family": "deep_teal", "personality": "soft", "lang": "en",
                                       "layout": "card_left", "recap": True},
    ("aadhi_recap", "portrait", ""): {"family": "midnight_indigo", "personality": "crisp", "lang": "en",
                                      "recap": True},
}


def _screenplay(fmt: str, spec: dict[str, Any]) -> Screenplay:
    data = screenplay_dict()
    lang = spec["lang"]
    data["language"] = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}[lang]
    data["learning_objectives"] = []
    data.update(spec.get("meta", {}))
    family, personality = spec["family"], spec["personality"]
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": family,
                            "personality": personality, "hook_kind": "question",
                            "intro_layout": spec.get("layout"), "reel_card_x": None,
                            "cue_term_style": "marker" if personality == "soft" else "underline",
                            "asset_style": {"family": family, "personality": personality}, "captions_burn": True,
                            "aadhi_recap": True, "music": {"source": "procedural", "provider": "procedural"}}
    # a recap fixture opens straight on the retrieval recap, so the recap appearance lies inside the first 300 frames
    # the real-Chromium order gate renders (tests/determinism/test_template_fixtures.py FRAMES)
    scenes = [_recap_scene("s-recap")] if spec.get("recap") else [_list_scene("s-facts", lang)]
    data["scenes"] = scenes
    return Screenplay.model_validate(data)


def _with_hook(scene: TimedScene, question: str) -> TimedScene:
    """The intro with a hook question on its board (the shape of a B4 presenter scene's own board)."""
    item = BoardItem(id="hook", kind=BoardItemKind.question, text=question)
    return scene.model_copy(update={"board": [*scene.board, item]})


def build(template: str, fmt: str, case: str) -> dict[str, Any]:
    """The fixture timeline (JSON-ready)."""
    spec = CASES[(template, fmt, case)]
    sp = _screenplay(fmt, spec)
    settings = Settings(_env_file=None, dynamic_enabled=True, dynamic_aadhi_intro=False)
    base = build_timeline(sp, make_manifest(sp), settings=settings)
    ids = [s.id for s in sp.scenes]
    own = [s for s in base.scenes if presenter.appearance_of(s.scene_id, ids) is None]
    plan = presenter.plan_appearances(sp)
    assert plan is not None and plan.intro, (template, fmt, case)
    if spec.get("recap") and plan.recap_before is None:  # 9:16: the recap appearance is off by default (§3.4)
        plan = replace(plan, recap_before="s-recap")

    scenes: list[TimedScene] = []

    def add(scene: TimedScene) -> None:
        start = round(scenes[-1].start + scenes[-1].duration, 3) if scenes else 0.0
        scenes.append(scene.model_copy(update={"index": len(scenes), "start": start}))

    intro = presenter.intro_scene(sp, plan)
    add(_with_hook(intro, spec["hook"]) if spec.get("hook") else intro)
    for s in own:
        if s.scene_id == plan.recap_before:
            add(presenter.recap_scene(sp, plan, index=len(scenes), start=0.0))
        add(s)

    chapters = presenter.fold_opening_chapter(build_chapters(sp, scenes), scenes, plan)
    plans = direct_plan(sp, scenes, fmt, chapters)
    for i, s in enumerate(scenes):
        synth = presenter.plan_for(s, sp, fmt)  # type: ignore[arg-type]
        if synth is not None:
            plans[i] = ScenePlan(s.scene_id, synth[0], synth[1], (), 1.0, "structure", forced=True)
    looks = presenter.apply_to_looks(sp, scenes, resolve_looks(sp, scenes, chapters, plans))
    for s, look, p in zip(scenes, looks, plans, strict=True):
        s.look = look
        s.events = list(p.events)
    if spec.get("hook"):
        first = scenes[0]
        at = round(presenter.AUDIO_OFFSET[fmt] - LEAD, 3)  # type: ignore[index]
        first.events = [VisualEvent(t=at, kind="reveal", target="hook", anchored=True),
                        VisualEvent(t=CUE_AT, kind="cue_term", target="hook:term:0", dur=CUE_DUR, anchored=True)]

    captions: list[CaptionCue] = []
    for s in scenes:
        for b in s.beats:
            captions.extend(shift_cues(b.captions, s.start))
    total = round(scenes[-1].start + scenes[-1].duration, 3)
    data = base.model_dump(mode="json")
    data.update({
        "scenes": [s.model_dump(mode="json") for s in scenes],
        "chapters": [c.model_dump(mode="json") for c in chapters],
        "captions": [c.model_dump(mode="json") for c in normalize_cues(captions)],
        "total_duration": total,
        "presentation": timeline_presentation(sp.presentation, scenes, total).model_dump(mode="json"),  # type: ignore[arg-type]
    })
    out = Timeline.model_validate(data).model_dump(mode="json")
    names = {s["look"]["template"] for s in out["scenes"]}
    assert template in names, (template, fmt, case, names)
    return out


def fixtures() -> dict[Path, str]:
    """Every fixture file and its text."""
    out: dict[Path, str] = {}
    for template, fmt, case in CASES:
        name = f"{template}.{fmt}{'.' + case if case else ''}.json"
        out[FIXTURE_DIR / name] = json.dumps(build(template, fmt, case), indent=2, sort_keys=True,
                                             ensure_ascii=False) + "\n"
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
        print(f"stale aadhi fixtures: {', '.join(stale)} (run python -m tests.compose.dynamic.make_aadhi_fixtures)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
