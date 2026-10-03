"""Backend tests of the Synchronization Director (Phase 16): narration positions ([PAUSE] segments, [SYNC] beats as
{segment, ratio}, sentences, mentions, the estimated clock), the plan of each scene kind (diagram, formula, code, timeline,
comparison, process, definition, summary, quiz, the camera's key moment), the rules (one primary emphasis at a time,
orphans removed, bounded counts, short scenes simplified, the end hold, no narration), what each presenter can really do,
the plan never copying the lesson's text, validation, the fingerprint (narration, direction, composition; never the speech
rate or the voice), robustness and determinism.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_sync_director.py" -v

No server, no browser, no AI or TTS provider: the director is pure data over the composed scene.
"""
import copy
import json
import time
import unittest
from unittest import mock

from backend_env import FFMPEG, assert_isolated, auth, ensure_user  # noqa: F401  first: throwaway database

import cinematic as C  # noqa: E402
import composer as K  # noqa: E402
import presenters  # noqa: E402
import sync_director as SD  # noqa: E402
import visual_director as VD  # noqa: E402
from test_visual_director import AT, NAMES, SETTINGS, WIDE, lesson, seen, teacher  # noqa: E402


def setUpModule():
    assert_isolated()


def real_lesson():
    """The representative lesson, with the diagram the Visual Router placed (as in a real lesson)."""
    scenes = lesson()
    scenes[AT["diagram"]]["visual_plan"] = {"side": seen(WIDE)}
    # the defined term marked up as the screenplay does (the page can only emphasise a marked-up term)
    scenes[AT["definition"]]["html"] = scenes[AT["definition"]]["html"].replace("Photosynthesis is", '<span class="keyword">Photosynthesis</span> is', 1)
    return scenes


def compose(scenes=None, settings=SETTINGS):
    return dict(zip(NAMES, C.compose_lesson(scenes or real_lesson(), settings)))


def events(sync, *types):
    return [e for e in sync["events"] if not types or e["type"] in types]


def where(e):
    return (e["at"]["segment"], e["at"]["ratio"])


def strings(value):
    """Every string in a plan (keys and values): what the plan could have copied from the lesson."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif isinstance(value, str):
        yield value


def resolve(scene, plan, adds, words=200):
    """Hand-made events through the director's resolution (orphans, conflicts, counts) on a ~77 s narration."""
    model = SD.narration_model(" ".join(["word"] * words))
    b = SD._Builder(model)
    for type_, target, seconds in adds:
        b.add(type_, target, SD.position_at(model, seconds), {"kind": "fraction", "value": 0.5})
    return SD._resolve(b, plan, VD.understand(scene, 0, 1, SETTINGS)), b.metrics


class NarrationTest(unittest.TestCase):
    def test_segments_split_at_pauses_exactly_as_the_presenters_module(self):
        text = "[PAUSE] Intro here. [PAUSE] Middle part. [PAUSE:3][PAUSE] The end. [pause:0.5]"
        model = SD.narration_model(text)
        parsed = presenters.parse_segments(text)
        self.assertEqual([(s["chars"], s["pause_after"]) for s in model["segments"]], [(len(p["text"]), p["pause_after"]) for p in parsed])
        self.assertEqual([s["pause_after"] for s in model["segments"]], [1.5, 4.5, 0.5])  # empty segments merge their pause
        first, second = model["segments"][:2]
        self.assertEqual(second["start_estimate"], round(first["estimate"] + 1.5, 2))  # the estimated clock counts the pauses

    def test_sync_beats_are_char_index_over_clean_length(self):
        # "Here is the law. " is 17 chars, " F equals m a. " 15 more; the clean text ([SYNC] removed) is 66 chars long
        model = SD.narration_model("Here is the law. [SYNC] F equals m a. [SYNC] Force is mass times acceleration.")
        self.assertEqual(model["segments"][0]["chars"], 66)
        self.assertEqual(model["beats"], [{"segment": 0, "ratio": round(17 / 66, 4)}, {"segment": 0, "ratio": round(32 / 66, 4)}])
        # per segment: "Now  this." (10 chars) has its beat at index 4
        self.assertEqual(SD.narration_model("Look. [PAUSE] Now [SYNC] this.")["beats"], [{"segment": 1, "ratio": 0.4}])

    def test_sentences_have_positions_and_a_decimal_is_not_an_end(self):
        model = SD.narration_model("Pi is about 3.14 in value. Next idea! Done?")
        self.assertEqual([s["ratio"] for s in model["sentences"]], [0.0, round(27 / 43, 4), round(38 / 43, 4)])
        self.assertEqual(model["sentences"][0]["text"], "Pi is about 3.14 in value.")

    def test_a_sentence_ending_in_a_number_ends_there(self):
        model = SD.narration_model("The answer is 42. Now look at the graph.")
        self.assertEqual([s["ratio"] for s in model["sentences"]], [0.0, round(18 / 40, 4)])

    def test_estimate_and_position_round_trip(self):
        model = SD.narration_model("One two three four five six. [PAUSE:2] Seven eight nine ten. [PAUSE] Eleven twelve thirteen.")
        for seg in model["segments"]:
            for share in (0.0, 0.3, 0.9):
                seconds = seg["start_estimate"] + share * seg["estimate"]
                self.assertAlmostEqual(SD.estimate_at(model, SD.position_at(model, seconds)), seconds, delta=0.01)
        self.assertEqual(SD.position_at(model, 999), {"segment": 2, "ratio": 1.0})
        self.assertEqual(SD.estimate_at(model, None), 0.0)

    def test_mention_finds_whole_words_case_insensitively_after_a_position(self):
        model = SD.narration_model("The Force is strong. A forceful push is still a force. [PAUSE] Force again.")
        first = SD.mention(model, "force")
        self.assertEqual(first, {"segment": 0, "ratio": round(4 / 54, 4)})
        self.assertEqual(SD.mention(model, "force", after={"segment": 0, "ratio": first["ratio"] + 0.01}),
                         {"segment": 0, "ratio": round(48 / 54, 4)})  # never inside "forceful"
        self.assertEqual(SD.mention(model, "FORCE", after={"segment": 1, "ratio": 0.0}), {"segment": 1, "ratio": 0.0})
        self.assertIsNone(SD.mention(model, "forc"))
        self.assertIsNone(SD.mention(model, "a"))  # too short to anchor anything


class SceneKindsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by = compose()
        cls.models = {name: SD.narration_model(s.get("narration")) for name, s in zip(NAMES, real_lesson())}

    def sync(self, name):
        return self.by[name]["sync"]

    def test_diagram_focus_at_the_look_cue_the_teacher_points_then_a_term_refocuses(self):
        focus = events(self.sync("diagram"), "diagram_focus")
        self.assertEqual((focus[0]["at"], focus[0]["anchor"]), ({"segment": 0, "ratio": 0.0}, {"kind": "sentence", "value": "look"}))
        point = events(self.sync("diagram"), "presenter_point")
        self.assertEqual([(p["at"], p["params"]["gesture"]) for p in point], [(focus[0]["at"], "point")])
        self.assertEqual(focus[1]["at"], SD.mention(self.models["diagram"], "chloroplast"))
        self.assertEqual(focus[1]["concept"], {"kind": "term", "index": 0})
        self.assertTrue(SD.later(focus[1]["at"], focus[0]["at"]))

    def test_formula_emphasis_labels_as_spoken_and_the_teacher_points_then_explains(self):
        sync, model = self.sync("formula"), self.models["formula"]
        formula = events(sync, "formula_emphasis")[0]
        self.assertEqual(formula["at"], model["beats"][0])
        labels = events(sync, "label_enter")
        self.assertEqual([e["target"]["item"] for e in labels], [0, 1, 2])
        self.assertEqual([e["at"] for e in labels], [SD.mention(model, w) for w in ("Force", "Mass", "Acceleration")])
        point, explain = events(sync, "presenter_point")[0], events(sync, "presenter_explain")[0]
        self.assertEqual(point["at"], formula["at"])
        self.assertIn(explain["at"], [{"segment": s["segment"], "ratio": s["ratio"]} for s in model["sentences"]])  # at a sentence
        self.assertGreaterEqual(explain["estimate"] - point["estimate"], 1.2)  # never in the same instant
        self.assertEqual([a["state"] for a in sync["attention"]], ["PRESENTER", "FORMULA", "PRESENTER"])

    def test_code_output_revealed_when_said_else_at_a_share_of_the_scene(self):
        reveal = events(self.sync("code"), "text_reveal")[0]
        self.assertEqual((reveal["target"], reveal["anchor"]), ({"layer": "board", "kind": "output"}, {"kind": "fraction", "value": 0.6}))
        scenes = real_lesson()
        scenes[AT["code"]]["narration"] = "A loop repeats a block of code. It prints the numbers zero to four."
        reveal = events(compose(scenes)["code"]["sync"], "text_reveal")[0]
        self.assertEqual((reveal["at"], reveal["anchor"]), ({"segment": 0, "ratio": round(32 / 67, 4)}, {"kind": "sentence", "value": "output"}))
        self.assertEqual([a["state"] for a in compose(scenes)["code"]["sync"]["attention"]], ["CODE", "RESULT"])

    def test_timeline_events_emphasised_when_their_dates_are_said(self):
        emphasis = events(self.sync("timeline"), "text_emphasis")
        self.assertEqual([e["target"] for e in emphasis], [{"layer": "board", "kind": "event", "index": n} for n in range(3)])
        self.assertEqual([e["at"] for e in emphasis], [SD.mention(self.models["timeline"], d) for d in ("1903", "1927", "1969")])

    def test_comparison_column_focused_only_when_its_side_is_named(self):
        self.assertEqual(events(self.sync("comparison")), [])  # "Let us compare." names neither side
        scenes = real_lesson()
        scenes[AT["comparison"]]["narration"] = "Plants make their own food. Animals eat other living things."
        emphasis = events(compose(scenes)["comparison"]["sync"], "text_emphasis")
        self.assertEqual([(e["target"]["kind"], e["target"]["index"], e["concept"]) for e in emphasis],
                         [("column", 0, {"kind": "side", "index": 0}), ("column", 1, {"kind": "side", "index": 1})])
        self.assertEqual(emphasis[1]["at"], {"segment": 0, "ratio": round(28 / 60, 4)})

    def test_process_steps_emphasised_when_named_unless_sync_reveals_them(self):
        emphasis = events(self.sync("process"), "text_emphasis")  # 3 [SYNC] for 4 steps: not enough
        self.assertEqual([e["target"] for e in emphasis], [{"layer": "board", "kind": "step", "index": n} for n in range(4)])
        self.assertEqual([e["at"] for e in emphasis], [SD.mention(self.models["process"], w) for w in ("collect", "filter", "chlorine", "store")])
        scenes = real_lesson()
        scenes[AT["process"]]["narration"] = "First [SYNC] we collect the water. [SYNC] Then we filter it. [SYNC] Next we add chlorine. [SYNC] Finally we store it."
        self.assertEqual(events(compose(scenes)["process"]["sync"], "text_emphasis"), [])

    def test_definition_term_emphasised_when_said(self):
        term = events(self.sync("definition"), "text_emphasis")[0]
        # "Here is the definition. " is 24 chars, then the [SYNC] and a space: "Photosynthesis" at 25 of 57
        self.assertEqual((term["target"], term["at"]), ({"layer": "board", "kind": "term", "index": 0}, {"segment": 0, "ratio": round(25 / 57, 4)}))

    def test_an_unmarked_term_is_never_emphasised(self):
        scenes = lesson()  # the definition's term is plain text there: the page could not find it, so nothing glows
        plan = compose(scenes)["definition"]
        self.assertFalse([e for e in events(plan["sync"], "text_emphasis") if e["target"].get("kind") == "term"])

    def test_summary_presenter_summarises_and_a_quiz_has_no_plan(self):
        summary = events(self.sync("summary"), "presenter_summarize")
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["params"], {"gesture": "open_hand", "expression": "encouraging"})
        self.assertNotIn("sync", self.by["quiz"])
        self.assertIsNone(SD.synchronize(real_lesson()[AT["quiz"]], self.by["quiz"]))

    def test_the_camera_starts_at_the_key_moment_or_its_planned_start(self):
        for name, key in (("formula", "formula_emphasis"), ("process", "text_emphasis"), ("timeline", "text_emphasis")):
            camera = events(self.sync(name), "camera_focus")[0]
            self.assertEqual(camera["at"], events(self.sync(name), key)[0]["at"], name)
            self.assertEqual(camera["anchor"], {"kind": "sentence", "value": "key moment"}, name)
            self.assertEqual(camera["params"]["to"], {k: self.by[name]["camera"]["to"][k] for k in ("x", "y", "w")}, name)
        intro = events(self.sync("intro"), "camera_focus")[0]  # nothing to wait for: when the composition planned it
        self.assertEqual(intro["anchor"], {"kind": "fraction", "value": "planned start"})
        self.assertAlmostEqual(intro["estimate"], self.by["intro"]["camera"]["start"], delta=0.05)


class RulesTest(unittest.TestCase):
    def test_one_primary_emphasis_at_a_time(self):
        formula = lesson()[AT["formula"]]
        plan = {"layers": [{"id": "visual"}, {"id": "board"}]}
        kept, metrics = resolve(formula, plan, [("formula_emphasis", {"layer": "board", "kind": "formula"}, 2.0),
                                                ("visual_highlight", {"layer": "visual"}, 2.4)])
        self.assertEqual(([e["type"] for e in kept], metrics["conflicts"]), (["formula_emphasis"], 1))
        kept, metrics = resolve(formula, plan, [("visual_highlight", {"layer": "visual"}, 2.0), ("diagram_focus", {"layer": "visual"}, 2.4)])
        self.assertEqual((len(kept), metrics["conflicts"]), (2, 0))  # the same target: no conflict

    def test_a_higher_priority_emphasis_that_comes_second_still_wins(self):
        scenes = real_lesson()
        scenes[AT["formula"]].update(visual={"type": "diagram", "description": "forces on a box"}, visual_plan={"side": seen(WIDE)},
                                     narration="Notice [SYNC] F equals m a, so force is mass times acceleration and more words here to pad it out.")
        sync = compose(scenes)["formula"]["sync"]
        primary = events(sync, *SD.PRIMARY)
        self.assertIn("formula_emphasis", [e["type"] for e in primary])
        for a, b in zip(primary, primary[1:]):
            if a["target"] != b["target"]:
                self.assertGreaterEqual(b["estimate"] - a["estimate"], 0.8, (a["type"], b["type"]))

    def test_orphans_are_removed(self):
        scenes, by = real_lesson(), compose()
        bare = copy.deepcopy(by["formula"])
        bare["layers"] = [layer for layer in bare["layers"] if layer["id"] != "board"]
        direction = VD.direct_lesson(scenes, SETTINGS)[AT["formula"]]
        sync = SD.synchronize(scenes[AT["formula"]], bare, AT["formula"], 12, SETTINGS, direction, K.sync_capabilities(scenes[AT["formula"]], SETTINGS))
        self.assertFalse([e for e in sync["events"] if e["target"]["layer"] == "board"])
        self.assertGreaterEqual(sync["metrics"]["orphans_removed"], 1)
        scenes[AT["diagram"]].update(visual_review={"side": {"status": "removed"}},  # Visual Review removed the picture
                                     visual_plan={"side": {"source": "NONE", "media": "NONE", "selection": "removed"}})
        plan = compose(scenes)["diagram"]
        self.assertNotIn("visual", [layer["id"] for layer in plan["layers"]])
        self.assertFalse([e for e in plan.get("sync", {"events": []})["events"] if e["target"]["layer"] == "visual"])
        timeline = {"layers": [{"id": "board"}, {"id": "labels", "items": [{}, {}]}]}
        kept, metrics = resolve(lesson()[AT["timeline"]], timeline, [
            ("label_enter", {"layer": "labels", "item": 1}, 1), ("label_enter", {"layer": "labels", "item": 4}, 3),
            ("text_emphasis", {"layer": "board", "kind": "event", "index": 2}, 5), ("text_emphasis", {"layer": "board", "kind": "event", "index": 5}, 7),
            ("text_reveal", {"layer": "board", "kind": "output"}, 9), ("text_emphasis", {"layer": "board", "kind": ".keyword"}, 11),
            ("diagram_focus", {"layer": "visual"}, 13), ("presenter_point", {"layer": "presenter"}, 15)])
        self.assertEqual([(e["type"], e["target"].get("item", e["target"].get("index"))) for e in kept], [("label_enter", 1), ("text_emphasis", 2)])
        self.assertEqual(metrics["orphans_removed"], 6)

    def test_counts_are_bounded(self):
        plan = {"layers": [{"id": "board"}, {"id": "presenter"}, {"id": "labels", "items": [{}] * 10}]}
        adds = [("label_enter", {"layer": "labels", "item": n}, 1 + n) for n in range(10)]
        adds += [(t, {"layer": "presenter"}, 12 + n + k * 0.2) for n, t in enumerate(("presenter_point", "presenter_explain", "presenter_pause", "presenter_emphasis"))
                 for k in range(6)]
        adds += [("camera_focus", {"layer": "camera"}, 40 + n) for n in range(3)] + [("camera_return", {"layer": "camera"}, 50 + n) for n in range(3)]
        kept, _metrics = resolve(lesson()[AT["process"]], plan, adds)
        count = lambda t: sum(1 for e in kept if e["type"] == t)  # noqa: E731
        self.assertLessEqual(len(kept), SD.MAX_EVENTS)
        self.assertEqual((count("label_enter"), count("camera_focus"), count("camera_return")), (6, 1, 1))
        for p in compose().values():
            if p.get("sync"):
                self.assertLessEqual(len(p["sync"]["events"]), 24)
                self.assertLessEqual(len(p["sync"]["attention"]), 12)

    def test_a_short_scene_is_simplified_and_the_camera_goes_first(self):
        sync = compose()["diagram"]["sync"]  # "Look at this diagram. [SYNC] The chloroplast is green." (~3.5 s)
        self.assertEqual(sync["fallback"], "simplified")
        self.assertLessEqual(len(sync["events"]), 3)
        self.assertGreater(sync["metrics"]["resolved"], 3)
        self.assertEqual([e["type"] for e in sync["events"]], ["diagram_focus", "presenter_point", "diagram_focus"])
        scenes = real_lesson()
        scenes[AT["diagram"]]["narration"] += " It holds the chlorophyll that catches the light for the whole leaf, all day long."
        longer = compose(scenes)["diagram"]["sync"]
        self.assertIsNone(longer["fallback"])
        self.assertIn("camera_focus", [e["type"] for e in longer["events"]])  # with time, the camera move is kept

    def test_the_result_is_held_briefly_and_longer_for_beginners(self):
        general, beginner = compose(), compose(settings={**SETTINGS, "learner_level": "beginner"})
        for by in (general, beginner):
            self.assertTrue(all(0 <= p["sync"]["end_hold"] <= 0.7 for p in by.values() if p.get("sync")))
        self.assertGreater(beginner["formula"]["sync"]["end_hold"], general["formula"]["sync"]["end_hold"])
        self.assertGreater(general["code"]["sync"]["end_hold"], 0)  # a revealed result stays visible
        self.assertEqual(general["intro"]["sync"]["end_hold"], 0.0)  # nothing to hold

    def test_without_narration_the_estimated_clock_and_fallback_positions(self):
        scenes = real_lesson()
        scenes[AT["code"]]["narration"] = ""
        sync = compose(scenes)["code"]["sync"]
        self.assertEqual((sync["timing_source"], sync["fallback"]), ("estimate", "estimate"))
        self.assertEqual([e["anchor"]["kind"] for e in sync["events"]], ["fraction"])
        self.assertEqual(SD.validate(sync), [])

    def test_a_long_scene_with_a_slow_camera_move_keeps_its_sync_plan(self):
        scenes = real_lesson()
        scenes[AT["definition"]]["narration"] += " It is how a green leaf turns light, water and air into the food the whole plant lives on." * 2
        plan = compose(scenes)["definition"]
        if plan["camera"]["movement"] != "slow_zoom_in" or plan["camera"]["duration"] <= 10:
            self.skipTest("the composition no longer plans a slow zoom longer than 10 s here")
        self.assertIn("sync", plan)
        self.assertIn("text_emphasis", [e["type"] for e in plan["sync"]["events"]])


class PresenterTest(unittest.TestCase):
    def test_the_drawn_teacher_acts_only_within_its_capabilities(self):
        caps = K.sync_capabilities(real_lesson()[AT["formula"]], SETTINGS)
        self.assertTrue(caps["acts"])
        acted = [e for p in compose().values() if p.get("sync") for e in events(p["sync"]) if e["type"].startswith("presenter")]
        self.assertTrue({"presenter_point", "presenter_explain", "presenter_summarize"} <= {e["type"] for e in acted})
        for e in acted:
            self.assertEqual(e["target"], {"layer": "presenter"})
            self.assertTrue(e["params"] and set(e["params"]) <= {"gesture", "expression"}, e)
            self.assertIn(e["params"].get("gesture", "none"), caps["gestures"])
            self.assertIn(e["params"].get("expression", "neutral"), caps["expressions"])

    def test_aadhi_and_an_ai_clip_never_get_gestures_they_cannot_show(self):
        legacy = compose(settings={"mode": "cinematic"})  # Aadhi, the mascot: only his narration states
        self.assertEqual(legacy["formula"]["presenter"]["type"], "mascot")
        states = K.sync_capabilities(real_lesson()[AT["formula"]], {"mode": "cinematic"})["states"]
        acted = [e for p in legacy.values() if p.get("sync") for e in events(p["sync"]) if e["type"].startswith("presenter")]
        self.assertTrue(acted)
        for e in acted:
            self.assertEqual(set(e["params"]), {"state"}, e)  # never a gesture or an expression
            self.assertIn(e["params"]["state"], states)
        self.assertNotIn("presenter_point", [e["type"] for e in acted])  # Aadhi cannot point
        self.assertGreater(legacy["diagram"]["sync"]["metrics"]["unsupported"], 0)
        self.assertEqual(len(events(legacy["diagram"]["sync"], "diagram_focus")), 2)  # the visual carries the emphasis
        self.assertIn("formula_emphasis", [e["type"] for e in legacy["formula"]["sync"]["events"]])
        scenes = real_lesson()
        clip = {**scenes[AT["diagram"]], "presenter_plan": teacher(type="ai_avatar", media=True)}
        caps = K.sync_capabilities(clip, SETTINGS)
        self.assertFalse(caps["acts"])  # a pre-rendered clip cannot change mid-scene
        on_screen = compose()["diagram"]  # a plan where the presenter is shown
        sync = SD.synchronize(clip, on_screen, AT["diagram"], 12, SETTINGS, VD.direct_lesson(scenes, SETTINGS)[AT["diagram"]], caps)
        self.assertFalse([e for e in sync["events"] if e["target"]["layer"] == "presenter"])
        self.assertEqual(sync["metrics"]["unsupported"], 1)
        self.assertIn("diagram_focus", [e["type"] for e in sync["events"]])

    def test_without_the_presenters_helpers_capabilities_are_still_checked(self):
        with mock.patch.object(presenters, "acting_for", None), mock.patch.object(presenters, "sync_capabilities", None):
            teacher_plan, aadhi_plan = compose()["formula"], compose(settings={"mode": "cinematic"})["formula"]
        self.assertEqual(events(teacher_plan["sync"], "presenter_point")[0]["params"], {"gesture": "point", "expression": "engaged"})
        self.assertFalse([e for e in aadhi_plan["sync"]["events"] if e["type"].startswith("presenter")])


class TextAndValidationTest(unittest.TestCase):
    def test_the_plan_never_copies_the_lessons_text(self):
        scenes = real_lesson()
        by = compose(scenes)
        for i, (name, s) in enumerate(zip(NAMES, scenes)):
            if not by[name].get("sync"):
                continue
            u = VD.understand(s, i, len(scenes), SETTINGS)
            words = [s["title"], *u["terms"], *(e["date"] for e in u["events"] or []), *(u["sides"] or []), *(u["steps"] or []),
                     *(m for m in ((u["formula"] or {}).get("meanings") or {}).values())]
            found = [w for w in words if len(w) >= 4 for t in strings(by[name]["sync"]) if w.lower() in t.lower()]
            self.assertEqual(found, [], name)
            for e in by[name]["sync"]["events"]:
                for ref in (e["concept"], e["anchor"].get("ref")):
                    if ref is not None:
                        self.assertEqual(set(ref), {"kind", "index"}, name)
                        self.assertIn(ref["kind"], SD.CONCEPT_KINDS)
                        self.assertIsInstance(ref["index"], int)

    def test_validate_rejects_what_the_page_must_never_play(self):
        sync = compose()["formula"]["sync"]
        self.assertEqual(SD.validate(sync), [])

        def tampered(change, type_="formula_emphasis"):
            bad = copy.deepcopy(sync)
            change(bad, next(e for e in bad["events"] if e["type"] == type_))
            return SD.validate(bad)
        self.assertIn("a concept is not a reference", tampered(lambda s, e: e.update(concept={"kind": "formula", "index": 0, "text": "F = ma"})))
        self.assertIn("a concept is not a reference", tampered(lambda s, e: e.update(concept={"kind": "Force", "index": 0})))
        self.assertIn("an anchor is malformed", tampered(lambda s, e: e.update(anchor={"kind": "concept", "ref": {"kind": "term", "index": "Force"}})))
        self.assertIn("an event type is unknown", tampered(lambda s, e: e.update(type="explode")))
        self.assertIn("an event position is malformed", tampered(lambda s, e: e.update(at={"segment": 0, "ratio": 1.5})))
        self.assertIn("an event position is malformed", tampered(lambda s, e: e.update(at={"segment": -1, "ratio": 0.2})))
        self.assertIn("a board target kind is unknown", tampered(lambda s, e: e.update(target={"layer": "board", "kind": ".keyword"})))
        self.assertIn("a camera target is out of range", tampered(lambda s, e: e["params"].update(to={"x": 0, "y": 0, "w": 0.3}), "camera_focus"))
        self.assertIn("a camera target is out of range", tampered(lambda s, e: e["params"].update(to={"x": 1.5, "y": 0, "w": 0.9}), "camera_focus"))
        self.assertIn("presenter params are malformed", tampered(lambda s, e: e["params"].update(state="dancing"), "presenter_point"))
        self.assertIn("presenter params are malformed", tampered(lambda s, e: e["params"].update(pose="<img>"), "presenter_point"))
        self.assertIn("presenter params are malformed", tampered(lambda s, e: e.update(params={}), "presenter_point"))
        self.assertIn("a dependency is missing", tampered(lambda s, e: e.update(depends_on=["e99"])))
        self.assertIn("end_hold is out of range", tampered(lambda s, e: s.update(end_hold=0.8)))
        self.assertEqual(tampered(lambda s, e: s.update(events=[e] * 25)), ["events malformed or too many"])
        self.assertEqual(SD.validate({**sync, "version": 0}), ["not a current synchronization plan"])


class FingerprintTest(unittest.TestCase):
    def test_the_fingerprint_follows_narration_direction_and_composition(self):
        base = compose()["formula"]["sync"]["fingerprint"]
        self.assertRegex(base, r"^[0-9a-f]{16}$")
        changes = {"narration": {"narration": lesson()[AT["formula"]]["narration"] + " Remember it."},
                   "direction": {"visual_review": {"direction": {"status": "changed", "overrides": {"camera_intent": "static"}}}},
                   "composition": {"visual_review": {"composition": {"status": "changed", "overrides": {"template": "presenter_explanation"}}}}}
        for what, change in changes.items():
            scenes = real_lesson()
            scenes[AT["formula"]].update(change)
            self.assertNotEqual(compose(scenes)["formula"]["sync"]["fingerprint"], base, what)
        s, plan = lesson()[AT["formula"]], compose()["formula"]
        fp = lambda plan=plan, direction={"fingerprint": "d1"}: SD.fingerprint(s, plan, direction, {"acts": True})  # noqa: E731
        self.assertNotEqual(fp(), fp(direction={"fingerprint": "d2"}))  # each input on its own
        self.assertNotEqual(fp(), fp(plan={**plan, "plan_hash": "0" * 16}))

    def test_speech_rate_and_voice_change_nothing(self):
        base = compose()
        other = compose(settings={**SETTINGS, "speech_rate": 1.6, "rate": 0.7, "voice": "en-GB-RyanNeural", "tts_engine": "edge"})
        for name in NAMES:
            self.assertEqual(base[name].get("sync"), other[name].get("sync"), name)  # same fingerprint, same positions
        for p in base.values():
            for e in (p.get("sync") or {"events": []})["events"]:
                self.assertEqual(set(e["at"]), {"segment", "ratio"})  # media positions: the page times them from the audio
        self.assertNotIn("rate", json.dumps([p.get("sync") for p in base.values()]))


class RobustnessTest(unittest.TestCase):
    def test_odd_input_never_raises(self):
        plan = compose()["formula"]
        odd = [({}, {}), ({"type": "content", "narration": ""}, plan), ({"type": "content", "narration": "[PAUSE] [PAUSE:2]"}, {"layers": []}),
               ({"type": "content", "narration": 12345}, {"layers": None}), ({"type": "content", "narration": "a b", "visual_direction": "x"}, {"layers": [{"id": "presenter"}]}),
               ({"type": "content", "narration": "a b c", "composition": {"labels": ["x"]}},
                {"layers": [{"id": "labels", "items": [{"source": {"kind": "screenplay", "index": 7}}, {"source": {"kind": "direction", "index": "x"}}, {}]}]})]
        for scene, p in odd:
            for caps in (None, {"acts": True}, {"acts": True, "gestures": ["point"], "expressions": ["engaged"]}):
                sync = SD.synchronize(scene, p, 0, 1, SETTINGS, None, caps)
                self.assertEqual(SD.validate(sync), [], scene)
        self.assertEqual(SD.synchronize({}, {})["timing_source"], "estimate")
        self.assertIsNone(SD.synchronize("not a scene", plan))
        self.assertIsNone(SD.synchronize(lesson()[0], None))

    def test_a_huge_narration_is_bounded_and_fast(self):
        for text in (("word " * 7000)[:30000], "Look at this [SYNC] step. " * 1200, "a b [PAUSE] " * 2500):
            s = {**lesson()[AT["process"]], "narration": text}
            started = time.perf_counter()
            plan = C.compose_lesson([s], SETTINGS)[0]
            sync = SD.synchronize(s, plan, 0, 1, SETTINGS, None, K.sync_capabilities(s, SETTINGS))
            self.assertLess(time.perf_counter() - started, 1.0)
            self.assertEqual(C.validate_plan(plan), [])
            self.assertLessEqual(len(sync["events"]), SD.MAX_EVENTS)
            self.assertLessEqual(sum(seg["chars"] for seg in sync["narration"]["segments"]), 20000)  # the director reads at most 20k chars

    def test_a_narration_of_many_pauses_gives_a_bounded_plan(self):
        s = {**lesson()[AT["example"]], "narration": "a b [PAUSE] " * 3000}
        sync = C.compose_lesson([lesson()[0], s], SETTINGS)[1].get("sync")
        self.assertLessEqual(len(sync["narration"]["segments"]), 200)
        self.assertLess(len(json.dumps(sync)), 64000)

    def test_a_failing_director_leaves_the_scene_composed_without_sync(self):
        with mock.patch.object(SD, "synchronize", side_effect=RuntimeError("boom")):
            plans = C.compose_lesson(real_lesson(), SETTINGS)
        with mock.patch.object(SD, "validate", return_value=["an event type is unknown"]):
            rejected = C.compose_lesson(real_lesson(), SETTINGS)
        for group in (plans, rejected):
            self.assertEqual(len(group), 12)
            self.assertTrue(all("sync" not in p for p in group))
            self.assertTrue(all(C.validate_plan(p) == [] for p in group))


class DeterministicTest(unittest.TestCase):
    def test_the_same_lesson_gives_identical_sync_plans(self):
        a = [p.get("sync") for p in C.compose_lesson(real_lesson(), SETTINGS)]
        b = [p.get("sync") for p in C.compose_lesson(real_lesson(), SETTINGS)]
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(sum(1 for x in a if x), 11)  # every scene but the quiz
        self.assertTrue(all(SD.validate(x) == [] for x in a if x))


class AiAlignmentTest(unittest.TestCase):
    """AI-assisted alignment (optional): only targets the rules could not place, enum ids and sentence numbers only."""
    AI = {**SETTINGS, "director": "ai", "composer_provider": "fake"}

    def unnamed(self):
        scenes = real_lesson()
        scenes[AT["comparison"]]["narration"] = "The first kind makes its own food. The second kind must eat to live."
        return scenes

    def plans(self, scenes, mode="ok", settings=None):
        return C.compose_lesson(scenes, settings or self.AI, ai={"env": {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_MODE": mode}})

    def test_only_targets_the_rules_could_not_place_are_asked(self):
        scenes = self.unnamed()
        calls = []
        real = SD.fake_align_model
        with mock.patch.object(SD, "fake_align_model", side_effect=lambda s, u, e: calls.append(u) or real(s, u, e)):
            plans = self.plans(scenes)
        asked = [c for c in calls if c.startswith("TASK: ALIGN")]
        self.assertTrue(asked and all('"side:' in c or '"step:' in c or '"output"' in c or '"visual"' in c for c in asked))
        self.assertNotIn("<script", json.dumps(plans))
        comparison = plans[AT["comparison"]]["sync"]
        self.assertEqual(comparison["ai"]["status"], "ok")
        self.assertTrue(any(e["anchor"].get("ref") == {"kind": "side", "index": 0} for e in comparison["events"]))
        self.assertEqual(SD.validate(comparison), [])

    def test_invalid_failing_and_unavailable_answers_leave_the_rules_in_charge(self):
        rules = C.compose_lesson(self.unnamed(), SETTINGS)[AT["comparison"]]["sync"]["events"]
        for mode, status in (("malformed_once", "repaired"), ("malformed", "invalid"), ("fabricate", "invalid"), ("fail", "failed")):
            sync = self.plans(self.unnamed(), mode)[AT["comparison"]]["sync"]
            self.assertEqual(sync["ai"]["status"], status, mode)
            self.assertEqual(SD.validate(sync), [], mode)
            if status != "repaired":
                self.assertEqual([e["type"] for e in sync["events"]], [e["type"] for e in rules], mode)
            self.assertNotIn("document.body", json.dumps(sync))
        offline = C.compose_lesson(self.unnamed(), {**SETTINGS, "director": "ai", "composer_provider": "gemini"}, ai={"env": {}})
        self.assertEqual(offline[AT["comparison"]]["sync"]["ai"]["status"], "unavailable")

    def test_a_kept_alignment_is_reused_and_validated_again(self):
        scenes = self.unnamed()
        first = self.plans(scenes)
        for scene, plan in zip(scenes, first):
            scene["cinematic_plan"] = plan
        with mock.patch.object(SD, "fake_align_model", side_effect=AssertionError("reused, not asked")):
            again = self.plans(scenes)
        self.assertTrue(again[AT["comparison"]]["sync"]["ai"].get("status") in ("ok", "repaired"))
        scenes[AT["comparison"]]["cinematic_plan"]["sync"]["ai"]["alignments"] = [{"target": "document.body", "sentence": 99}]
        with mock.patch.object(SD, "fake_align_model", side_effect=lambda s, u, e: SD.fake_align_model.__wrapped__(s, u, e)
                               if hasattr(SD.fake_align_model, "__wrapped__") else json.dumps({"alignments": []})):
            forged = self.plans(scenes)
        self.assertNotIn("document.body", json.dumps(forged[AT["comparison"]]["sync"]))

    def test_a_regeneration_asks_its_own_scene_first_and_skipped_scenes_are_asked_later(self):
        scenes = self.unnamed()
        scenes[AT["code"]]["narration"] = "A loop repeats a block of code. Each pass uses the next number in turn."  # its output unnamed
        env = {"env": {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_MODE": "ok"}}
        with mock.patch.object(SD.VD, "AI_CALLS_PER_LESSON", 1):
            first = self.plans(scenes)
        status = {i: p["sync"]["ai"]["status"] for i, p in enumerate(first) if p and "ai" in (p.get("sync") or {})}
        asked = [i for i, s in status.items() if s in ("ok", "repaired")]
        skipped = [i for i, s in status.items() if s == "skipped"]
        self.assertEqual(len(asked), 1, status)
        self.assertTrue(skipped, status)
        for scene, plan in zip(scenes, first):
            scene["cinematic_plan"] = plan
        calls = []
        real = SD.fake_align_model
        with mock.patch.object(SD.VD, "AI_CALLS_PER_LESSON", 1), \
                mock.patch.object(SD, "fake_align_model", side_effect=lambda s, u, e: calls.append(u) or real(s, u, e)):
            out = SD.align_lesson(scenes, self.AI, {**env, "force_align": [skipped[-1]]})
        self.assertIn(out[skipped[-1]]["status"], ("ok", "repaired"), "the regenerated scene is asked, never skipped by the cap")
        self.assertNotIn("cached", out[skipped[-1]])
        self.assertTrue(out[asked[0]].get("cached"), "the other scenes keep their answers")
        self.assertEqual(len([c for c in calls if c.startswith("TASK: ALIGN")]), 1, "only the regenerated scene is asked")
        for i in skipped[:-1]:
            self.assertEqual((out[i]["status"], out[i].get("cached")), ("skipped", True), "shown, not asked meanwhile")
        # a scene the cap skipped is not stuck: a later composition asks it
        with mock.patch.object(SD.VD, "AI_CALLS_PER_LESSON", 1):
            later = SD.align_lesson(scenes, self.AI, env)
        self.assertIn(later[skipped[0]]["status"], ("ok", "repaired"))
        self.assertTrue(later[asked[0]].get("cached"))

    def test_automatic_mode_never_asks_a_model(self):
        with mock.patch.object(SD, "fake_align_model", side_effect=AssertionError("no model in Automatic mode")),                 mock.patch("source_documents.call_model", side_effect=AssertionError("no model in Automatic mode")):
            plans = C.compose_lesson(self.unnamed(), SETTINGS)
        self.assertTrue(all("ai" not in (p.get("sync") or {}) for p in plans if p))

    def test_validate_alignment_accepts_only_listed_targets_and_sentences(self):
        with self.assertRaises(SD.Invalid):
            SD.validate_alignment({"alignments": [{"target": "side:5", "sentence": 0}]}, ["side:0"], 2)
        with self.assertRaises(SD.Invalid):
            SD.validate_alignment({"alignments": [{"target": "side:0", "sentence": 7}]}, ["side:0"], 2)
        with self.assertRaises(SD.Invalid):
            SD.validate_alignment("prose", ["side:0"], 2)
        self.assertEqual(SD.validate_alignment({"alignments": [{"target": "side:0", "sentence": 1}], "extra": "x"}, ["side:0"], 2),
                         [("side:0", 1)])


class ReviewFindingsTest(unittest.TestCase):
    """The defects the end-of-phase security audit and correctness review found, each pinned by a test."""

    def scene(self, **kw):
        return {"type": "content", "title": "x", "presenter_plan": teacher(), **kw}

    def test_a_short_scene_keeps_every_label(self):
        formula = self.scene(title="Newton", html="<div class='formula-block'>\[F = ma\]</div>",
                             narration="Look at the cart: force equals mass times acceleration, so a push speeds it up.",
                             composition={"labels": ["F = force", "m = mass", "a = acceleration"]})
        plan = C.compose_lesson([self.scene(html="<p>a</p>", narration="Hi."), formula], SETTINGS)[1]
        labels = [e for e in plan["sync"]["events"] if e["type"] == "label_enter"]
        self.assertEqual(sorted(e["target"]["item"] for e in labels), [0, 1, 2])

    def test_a_feature_table_keeps_its_second_side(self):
        table = self.scene(title="Mitosis vs meiosis", html="<table><tr><th>Feature</th><th>Mitosis</th><th>Meiosis</th></tr>"
                           "<tr><td>Cells</td><td>2</td><td>4</td></tr></table>",
                           narration="First, mitosis makes two cells from one. Much later in the story, meiosis makes four cells instead.")
        sync = C.compose_lesson([self.scene(html="<p>a</p>", narration="Hi."), table], SETTINGS)[1]["sync"]
        columns = sorted(e["target"]["index"] for e in sync["events"] if e["type"] == "text_emphasis")
        self.assertEqual(columns, [1, 2])
        self.assertEqual(sync["metrics"]["orphans_removed"], 0)

    def test_the_term_index_is_the_one_the_page_counts(self):
        cases = (("<div class='definition'><b>Velocity</b> is the rate of change of <strong>displacement</strong>.</div>", None),
                 ("<p><strong>speed</strong></p><div class='definition'><strong>Velocity</strong> is <strong>speed</strong> with a direction.</div>", 0),
                 ("<p><strong>mass</strong></p><div class='definition'><strong>Weight</strong> is the pull on a mass.</div>", 0))
        for html, expected in cases:
            s = self.scene(title="Velocity", html=html, narration="Here it is. [SYNC] Velocity is speed with a direction, and weight pulls a mass.")
            sync = C.compose_lesson([self.scene(html="<p>a</p>", narration="Hi."), s], SETTINGS)[1]["sync"]
            terms = [e["target"]["index"] for e in sync["events"] if e["target"].get("kind") == "term"]
            self.assertEqual(terms[:1], [] if expected is None else [expected], html)

    def test_crafted_narrations_are_linear(self):
        started = time.time()
        for narration in ("." * 19999 + "x", "!?" * 9999 + "a", "Hi [PAUSE:" + "9" * 400 + "] there."):
            SD.narration_model(narration)
            C.compose_lesson([self.scene(html="<p>a</p>", narration=narration)], SETTINGS)
        self.assertLess(time.time() - started, 2.0)

    def test_a_malformed_kept_record_never_drops_the_other_scenes(self):
        scenes = real_lesson()
        scenes[AT["comparison"]]["narration"] = "The first kind makes its own food. The second kind must eat to live."
        scenes[AT["definition"]]["cinematic_plan"] = {"sync": "x"}
        out = SD.align_lesson(scenes, {**SETTINGS, "director": "ai", "composer_provider": "fake"}, {"env": {"AI_FAKE_PROVIDER": "1"}})
        self.assertIn(AT["comparison"], out)
        self.assertEqual(out[AT["comparison"]]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
