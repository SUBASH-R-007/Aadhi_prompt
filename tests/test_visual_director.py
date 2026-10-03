"""Backend tests of the AI Visual Director (Phase 15): scene understanding (purpose, steps, dated events, the two sides of
a comparison, formula symbols and their meanings, code and its output, key terms, learner level, presenter), the rules'
strategy per scene type, media awareness, the Visual Router's preference (no feedback loop), hard constraints and
repair, the user's choices, concept continuity, the AI-assisted mode (invalid, fabricated, failing, slow and kept
suggestions), the deterministic mode, the composition that follows a direction (Phases 13 and 14) and the API.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_visual_director.py" -v

The AI-assisted mode uses the director's local stand-in model; no real provider, no network.
"""
import copy
import json
import time
import unittest
from unittest import mock

from backend_env import FFMPEG, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import cinematic as C  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import visual_director as VD  # noqa: E402


def teacher(**extra):
    return {"presenter_id": "aadhi-teacher", "type": "illustrated", "enabled": True, "position": "right", "placement": "side", **extra}


SETTINGS = {"mode": "cinematic", "presenter_legacy": False, "presenter_id": "aadhi-teacher"}
WIDE = "b" * 32
PICTURES = ("diagram", "illustration", "chart", "image", "animation", "video", "simulation")
NAMES = ("intro", "definition", "explanation", "formula", "code", "diagram", "process", "comparison", "example", "timeline", "quiz", "summary")
AT = {name: i for i, name in enumerate(NAMES)}


def seen(asset_id):
    return {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": asset_id, "selection": "matched"}


def lesson():
    """The representative lesson: one scene of each of the twelve kinds the director must handle, in NAMES order."""
    return [
        {"type": "content", "title": "Photosynthesis", "html": "<p>Plants turn sunlight into sugar.</p>",
         "narration": "Welcome! Today we learn how plants make food.", "presenter_plan": teacher()},
        {"type": "content", "title": "What is photosynthesis?",
         "html": "<div class='definition'>Photosynthesis is the process by which green plants use <span class=\"keyword\">sunlight</span> "
                 "to make <span class=\"keyword\">glucose</span>.</div>",
         "narration": "Here is the definition. [SYNC] Photosynthesis is the process...", "presenter_plan": teacher()},
        {"type": "content", "title": "Why leaves are green", "html": "<p>Leaves hold a pigment that reflects green light and keeps the rest.</p>",
         "narration": "Leaves look green for a simple reason.", "presenter_plan": teacher()},
        {"type": "content", "title": "Newton's second law", "html": "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
         "narration": "Here is the law. [SYNC] F equals m a. [SYNC] Force is mass times acceleration.", "presenter_plan": teacher()},
        {"type": "content", "title": "Python for loop", "html": "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints 0 to 4.</p>",
         "narration": "A loop repeats a block of code.", "presenter_plan": teacher()},
        {"type": "content", "title": "Inside a plant", "html": "<p>The <span class=\"keyword\">chloroplast</span> holds chlorophyll.</p>",
         "visual": {"type": "diagram", "description": "A leaf cell"}, "narration": "Look at this diagram. [SYNC] The chloroplast is green.",
         "presenter_plan": teacher()},
        {"type": "content", "title": "How water is cleaned",
         "html": "<ol><li>Collect the water</li><li>Filter the sand</li><li>Add chlorine</li><li>Store it in tanks</li></ol>",
         "narration": "First we collect the water. [SYNC] Then we filter it. [SYNC] Next we add chlorine. [SYNC] Finally we store it in tanks for the town.",
         "presenter_plan": teacher()},
        {"type": "content", "title": "Plants vs Animals",
         "html": "<table><tr><th>Plants</th><th>Animals</th></tr><tr><td>Make food</td><td>Eat food</td></tr></table>",
         "narration": "Let us compare.", "presenter_plan": teacher()},
        {"type": "example", "title": "Worked example: a falling ball", "html": "<p>Suppose a ball falls from a table.</p>",
         "narration": "Let us work through an example.", "presenter_plan": teacher()},
        {"type": "content", "title": "History of flight",
         "html": "<ul><li>1903: the first powered flight</li><li>1927: across the Atlantic alone</li><li>1969: people land on the Moon</li></ul>",
         "narration": "Flight changed fast. [SYNC] In 1903 the first flight. [SYNC] In 1927 the Atlantic. [SYNC] In 1969 the Moon.", "presenter_plan": teacher()},
        {"type": "quiz_checkpoint", "title": "Quick check", "question": "What does a plant make?", "options": ["Glucose", "Salt", "Iron"],
         "narration": "Quick question.", "presenter_plan": teacher()},
        {"type": "key-takeaway", "title": "Key takeaways",
         "html": "<ul class='takeaway-list'><li>Plants make glucose</li><li>Chlorophyll captures light</li><li>Oxygen is released</li></ul>",
         "narration": "Let us recap. [SYNC] Plants make glucose. [SYNC] Chlorophyll captures light. [SYNC] Oxygen is released.", "presenter_plan": teacher()},
    ]


def scene(name, **extra):
    return {**lesson()[AT[name]], **extra}


def understand(s, index=3, count=12, settings=SETTINGS, titles=None):
    return VD.understand(s, index, count, settings, titles)


def direct(s, index=3, count=12, settings=SETTINGS):
    return VD.direct_scene(understand(s, index, count, settings), s)


def changed(s, **overrides):
    return {**s, "visual_review": {"direction": {"status": "changed", "overrides": overrides}}}


def layer(plan, layer_id):
    return next((l for l in plan["layers"] if l["id"] == layer_id), None)


class UnderstandingTest(unittest.TestCase):
    def test_the_representative_lesson_is_understood(self):
        scenes = lesson()
        us = [understand(s, i, len(scenes)) for i, s in enumerate(scenes)]
        self.assertEqual([u["purpose"] for u in us], ["intro", "definition", "explanation", "formula", "code", "diagram", "process",
                                                     "comparison", "example", "explanation", "quiz", "summary"])
        self.assertEqual(len(us[AT["process"]]["steps"]), 4)
        self.assertEqual([e["date"] for e in us[AT["timeline"]]["events"]], ["1903", "1927", "1969"])
        self.assertEqual(us[AT["comparison"]]["sides"], ["Plants", "Animals"])
        formula = us[AT["formula"]]["formula"]
        self.assertEqual(formula["symbols"], ["F", "m", "a"])
        self.assertEqual(formula["meanings"], {"F": "Force", "m": "Mass", "a": "Acceleration"})  # from the word equation
        code = us[AT["code"]]["code"]
        self.assertEqual((code["output"], code["output_text"]), (True, "It prints 0 to 4."))
        self.assertEqual(us[AT["definition"]]["definition_term"], "Photosynthesis")
        self.assertEqual(us[AT["definition"]]["terms"], ["Photosynthesis", "sunlight", "glucose"])  # the keyword spans
        self.assertEqual(us[AT["diagram"]]["visual"]["kind"], "diagram")
        self.assertTrue(all(u["steps"] == [] for i, u in enumerate(us) if i != AT["process"]))

    def test_learner_level_and_presenter_come_from_the_settings(self):
        s = scene("formula")
        self.assertEqual(understand(s, settings={**SETTINGS, "learner_level": "beginner"})["learner_level"], "beginner")
        self.assertEqual(understand(s, settings={**SETTINGS, "learner_level": "expert"})["learner_level"], "general")
        legacy = {**s, "aadhi_position": "hidden"}
        self.assertFalse(understand(legacy, settings={"mode": "cinematic"})["presenter"]["enabled"])   # legacy: Aadhi's position
        self.assertTrue(understand({**s, "aadhi_position": "left"}, settings={"mode": "cinematic"})["presenter"]["enabled"])
        self.assertTrue(understand(legacy, settings=SETTINGS)["presenter"]["enabled"])                  # a chosen presenter
        self.assertFalse(understand({**s, "presenter_plan": teacher(enabled=False)}, settings=SETTINGS)["presenter"]["enabled"])  # left out by Phase 12

    def test_understanding_never_edits_the_scene_and_its_hash_is_stable(self):
        scenes = lesson()
        before = copy.deepcopy(scenes)
        u = understand(scenes[AT["formula"]])
        VD.direct_lesson(scenes, SETTINGS)
        self.assertEqual(scenes, before)
        self.assertEqual(u["hash"], understand(copy.deepcopy(before[AT["formula"]]))["hash"])
        self.assertNotEqual(u["hash"], understand({**before[AT["formula"]], "html": "<p>Other</p>"})["hash"])

    def test_a_scene_whose_presenter_is_switched_off_is_directed_without_one(self):
        intro = scene("intro", presenter_plan=teacher(enabled=False))
        plan = direct(intro, index=0)
        self.assertNotEqual(plan["primary_visual"]["kind"], "presenter")
        self.assertEqual(plan["presenter"]["role"], "hidden")


class RulesTest(unittest.TestCase):
    def test_a_strategy_per_scene_type(self):
        plans = VD.direct_lesson(lesson(), SETTINGS)
        got = {name: (plans[i]["strategy"], plans[i]["primary_visual"]["kind"]) for i, name in enumerate(NAMES)}
        self.assertEqual(got, {
            "intro": ("concept_overview", "presenter"), "definition": ("definition_visual", "definition_card"),
            "explanation": ("concept_overview", "board_text"), "formula": ("formula_explanation", "formula"),
            "code": ("code_to_output", "code_output"), "diagram": ("conceptual_diagram", "diagram"), "process": ("step_by_step", "step_flow"),
            "comparison": ("side_by_side", "table"), "example": ("worked_example", "board_text"), "timeline": ("timeline", "timeline"),
            "quiz": ("question_focus", "question"), "summary": ("key_points", "key_points")})
        by = dict(zip(NAMES, plans))
        self.assertEqual(by["intro"]["presenter"]["role"], "dominant")
        self.assertEqual(by["diagram"]["presenter"], {"role": "guide", "interaction": "points_to_visual"})
        self.assertEqual(by["code"]["presenter"]["role"], "hidden")
        for name in ("code", "quiz", "comparison"):
            self.assertIn(by[name]["camera_intent"], ("static", "compare"), name)  # never a moving camera over reading tasks
        self.assertEqual((by["process"]["motion_intent"], by["process"]["timing"]["reveal"]), ("progressive_build", "progressive"))
        self.assertEqual((by["timeline"]["camera_intent"], by["timeline"]["motion_intent"]), ("follow_process", "sequence"))
        self.assertEqual((by["comparison"]["timing"]["reveal"], by["quiz"]["timing"]["reveal"]), ("together", "together"))
        self.assertEqual(by["quiz"]["timing"]["first"], "question")
        for p in plans:
            self.assertEqual(VD.validate_plan(p), [], p["strategy"])
            self.assertEqual(p["source"], "rules")
            self.assertTrue(VD.reasons_text(p), p["strategy"])

    def test_annotations_and_emphasis(self):
        by = dict(zip(NAMES, VD.direct_lesson(lesson(), SETTINGS)))
        self.assertEqual(by["formula"]["annotations"], [{"text": "F = Force", "kind": "variable", "ref": "F"},
                                                        {"text": "m = Mass", "kind": "variable", "ref": "m"},
                                                        {"text": "a = Acceleration", "kind": "variable", "ref": "a"}])
        self.assertEqual([(e["target"], e["ref"]) for e in by["formula"]["emphasis"]], [("formula", None), ("variable", "F"), ("variable", "m"), ("variable", "a")])
        # only labels that add meaning: a diagram's terms (often the asset's search keywords, or the board's own list) are noise
        self.assertEqual(by["diagram"]["annotations"], [])
        for name in NAMES:
            board = lesson()[AT[name]].get("html") or ""
            self.assertFalse(any(a["text"] in board for a in by[name]["annotations"]), name)  # never a repeat of the board
        targets = {name: [e["target"] for e in by[name]["emphasis"]] for name in NAMES}
        self.assertEqual(targets["code"], ["code", "output"])
        self.assertEqual(targets["diagram"], ["visual"])
        self.assertEqual(targets["quiz"], ["question"])
        self.assertEqual(by["definition"]["emphasis"], [{"target": "term", "ref": "Photosynthesis", "at": None}])
        self.assertTrue(all(by[n]["annotations"] == [] for n in ("intro", "code", "process", "quiz", "summary")))

    def test_learning_goals_read_naturally(self):
        by = dict(zip(NAMES, VD.direct_lesson(lesson(), SETTINGS)))
        goals = {name: by[name]["learning_goal"] for name in NAMES}
        self.assertEqual(goals["intro"], "meet Photosynthesis")
        self.assertEqual(goals["definition"], "know what Photosynthesis means")
        self.assertEqual(goals["process"], "follow the 4 steps in order")
        self.assertEqual(goals["comparison"], "tell Plants and Animals apart")
        self.assertEqual(goals["summary"], "remember the 3 key points")
        self.assertNotIn("Inside a plant", goals["diagram"])
        self.assertNotIn("Quick check", goals["quiz"])
        self.assertTrue(all("{" not in g and "  " not in g for g in goals.values()), goals)
        for title in ("Inside a plant", "Quick check", "What is photosynthesis?", "Key takeaways", "How water is cleaned"):
            self.assertIsNone(VD.concept_phrase(understand({"type": "content", "title": title, "html": "<p>x</p>"})), title)
        for title in ("Photosynthesis", "Newton's second law"):
            self.assertEqual(VD.concept_phrase(understand({"type": "content", "title": title, "html": "<p>x</p>"})), title)
        mapped = understand(scene("diagram", concept_id="c1"), titles={"c1": "Plant cells"})
        self.assertEqual(VD.concept_phrase(mapped), "Plant cells")  # the concept map's name, whatever the scene's title

    def test_a_generic_example_title_is_never_the_concept(self):
        u = understand(scene("example"), index=AT["example"])
        self.assertIsNone(VD.concept_phrase(u))
        self.assertNotIn("Worked example", direct(scene("example"), index=AT["example"])["learning_goal"])


class MediaAndRouteTest(unittest.TestCase):
    def test_a_visual_the_screenplay_asks_for_allows_a_diagram_strategy(self):
        for s in (scene("diagram"), scene("diagram", side_panel={"type": "image"}), scene("diagram", visual_plan={"side": seen(WIDE)})):
            plan = direct(s, index=AT["diagram"])
            self.assertIn(plan["primary_visual"]["kind"], PICTURES)
            self.assertEqual(plan["primary_visual"]["source"], "scene_visual")

    def test_no_picture_strategy_without_a_picture(self):
        nothing = scene("diagram", visual_plan={"side": {"source": "NONE", "media": "NONE", "selection": "none"}})
        removed = scene("diagram", visual_review={"side": {"status": "removed"}})
        for s in (nothing, removed):
            plan = direct(s, index=AT["diagram"])
            self.assertNotIn(plan["primary_visual"]["kind"], PICTURES)
            self.assertEqual(plan["primary_visual"]["source"], "board")
            self.assertEqual(VD.validate_plan(plan), [])
        mechanism = {"type": "content", "title": "How a heart pumps", "html": "<p>The heart pushes blood which flows through the body.</p>",
                     "narration": "The heart pushes blood. It flows and travels to the lungs.", "presenter_plan": teacher()}
        plan = direct(mechanism, index=2)
        self.assertEqual((plan["primary_visual"]["kind"], plan["visual_need"]), ("board_text", "wanted"))  # says a visual would help

    def test_a_router_plan_that_found_nothing_leaves_no_picture_strategy(self):
        found_nothing = {"source": "NONE", "media": "STATIC_IMAGE", "selection": "none", "would_require": "AI_IMAGE"}
        plan = direct(scene("diagram", visual_plan={"side": found_nothing}), index=AT["diagram"])
        self.assertNotIn(plan["primary_visual"]["kind"], PICTURES)

    def test_an_approved_visual_is_read_but_never_changes_the_route(self):
        approved = scene("diagram", visual_plan={"side": seen(WIDE)}, visual_review={"side": {"status": "approved", "asset_id": WIDE}})
        self.assertTrue(understand(approved, AT["diagram"])["approved_visual"])
        routes = [direct(s, index=AT["diagram"])["route"] for s in (
            scene("diagram"), approved, scene("diagram", visual_plan={"side": {"source": "NONE", "media": "NONE"}}),
            scene("diagram", visual_review={"side": {"status": "removed"}}))]
        self.assertEqual(routes[0], {"prefer_existing": True, "preferred_media": "image", "match_terms": ["Inside a plant", "chloroplast"]})
        self.assertTrue(all(r == routes[0] for r in routes), routes)  # routing and direction never feed each other

    def test_the_route_is_a_bounded_preference(self):
        by = dict(zip(NAMES, VD.direct_lesson(lesson(), SETTINGS)))
        self.assertTrue(all(by[n]["route"] is None for n in NAMES if n != "diagram"))  # only where the screenplay asks for a visual
        animation = {"type": "content", "title": "A falling ball", "html": "<p>The ball falls.</p>", "visual": {"type": "animation"}, "narration": "Watch."}
        self.assertEqual(direct(animation, 2)["route"]["preferred_media"], "video")
        self.assertEqual(direct(changed(animation, prefer="static"), 2)["route"]["preferred_media"], "image")
        main = {"type": "simulation", "title": "Orbits", "manim_code": "x", "narration": "Watch."}
        self.assertEqual(direct(main, 2)["route"]["preferred_media"], "video")
        keywords = "".join(f"<span class='keyword'>{k}</span> " for k in ("chlorophyll", "CHLOROPHYLL", "Light", "Water", "Carbon dioxide", "Glucose", "Oxygen", "Stomata"))
        busy = {"type": "content", "title": "Chlorophyll and the light reactions inside green leaves", "html": f"<p>{keywords}</p>",
                "visual": {"type": "diagram", "keywords": ["Chlorophyll", "Water"]}, "narration": "x"}
        route = direct(busy, 2)["route"]
        self.assertTrue(route["prefer_existing"])
        terms = route["match_terms"]
        self.assertLessEqual(len(terms), 6)
        self.assertTrue(all(len(t) <= 40 for t in terms), terms)
        self.assertEqual(len({VD._norm(t) for t in terms}), len(terms))
        self.assertEqual(sum(1 for t in terms if VD._norm(t) == "chlorophyll"), 1)
        self.assertFalse(set(route) - {"prefer_existing", "preferred_media", "match_terms"})  # never a provider, a prompt or a slot


class ConstraintsTest(unittest.TestCase):
    def test_hard_constraints_reject_what_the_scene_cannot_show(self):
        u = understand(scene("explanation"), AT["explanation"])
        base = VD.rule_candidates(u)[0]
        self.assertIn("the scene has no such visual", VD.check(u, dict(base, primary="diagram", source="scene_visual")))
        self.assertIn("fewer than two steps", VD.check(u, dict(base, primary="step_flow")))
        self.assertIn("fewer than three dated events", VD.check(u, dict(base, primary="timeline")))
        self.assertIn("outside the vocabulary", VD.check(u, dict(base, strategy="explode")))
        two_dates = {"type": "content", "title": "Two dates", "html": "<ul><li>1903: first flight</li><li>1969: the Moon</li></ul>", "narration": "x"}
        self.assertEqual(understand(two_dates)["events"], [])  # never a timeline of two
        silent = understand({"type": "content", "title": "A loop", "html": "<pre><code>for i in range(5):\n    total += i</code></pre>", "narration": "x"})
        self.assertEqual((silent["code"]["output"], silent["code"]["output_text"]), (False, None))
        self.assertIn("the scene does not say what the code produces", VD.check(silent, dict(base, primary="code_output")))
        self.assertNotIn("code_output", [c["primary"] for c in VD.rule_candidates(silent)])

    def test_repair_follows_the_scene(self):
        off = [direct({**s, "aadhi_position": "hidden"}, i, settings={"mode": "cinematic"}) for i, s in enumerate(lesson())]
        self.assertTrue(all(p["presenter"] == {"role": "hidden", "interaction": "none"} for p in off))
        self.assertNotEqual(off[0]["primary_visual"]["kind"], "presenter")  # no presenter: the intro is carried by something else
        dense = {"type": "content", "title": "Everything", "html": "<p>" + " ".join(["word"] * 120) + "</p>", "narration": "x", "presenter_plan": teacher()}
        plan = direct(dense, index=0)
        self.assertNotEqual(plan["presenter"]["role"], "dominant")
        self.assertEqual(plan["camera_intent"], "static")
        self.assertIn("dense_text", plan["reason_codes"])
        short = scene("process", narration="First collect, then heat.")
        plan = direct(short, AT["process"])
        self.assertEqual((plan["motion_intent"], plan["timing"]["reveal"]), ("reveal", "together"))  # no time to build four steps
        self.assertIn("short_scene", plan["reason_codes"])

    def test_the_fallback_when_nothing_fits(self):
        sim = {"type": "simulation", "title": "Watch", "manim_code": "x", "narration": "Watch.",
               "visual_plan": {"main": {"selection": "removed", "source": "NONE", "media": "NONE"}}}
        plan = direct(sim, 2)
        self.assertEqual((plan["source"], plan["strategy"], plan["primary_visual"]["kind"]), ("fallback", "concept_overview", "board_text"))
        self.assertEqual((plan["confidence"], plan["reason_codes"]), ("low", ["fallback"]))
        self.assertEqual(VD.validate_plan(plan), [])

    def test_validate_plan_catches_tampering(self):
        plan = VD.direct_lesson(lesson(), SETTINGS)[AT["formula"]]

        def tampered(**change):
            bad = {**copy.deepcopy(plan), **change}
            bad["fingerprint"] = VD.fingerprint(bad)
            return VD.validate_plan(bad)
        self.assertIn("explode is not allowed", tampered(strategy="explode"))
        self.assertIn("an annotation is malformed", tampered(annotations=[{"kind": "caption", "text": "x"}]))
        self.assertIn("an annotation is malformed", tampered(annotations=[{"kind": "variable", "text": "x" * 61}]))
        self.assertIn("an emphasis target is malformed", tampered(emphasis=[{"target": "everything"}]))
        self.assertEqual(VD.validate_plan({**plan, "fingerprint": "0" * 16}), ["the fingerprint does not match"])
        self.assertEqual(VD.validate_plan({**plan, "version": 0}), ["not a current visual direction"])
        self.assertEqual(VD.validate_plan("plan"), ["not a current visual direction"])


class UserChoicesTest(unittest.TestCase):
    def test_a_user_choice_wins_and_is_locked(self):
        plan = direct(changed(scene("formula"), presenter_role="hidden", camera_intent="static"))
        self.assertEqual((plan["source"], plan["locked"]), ("user", ["camera_intent", "presenter_role"]))
        self.assertEqual((plan["presenter"]["role"], plan["camera_intent"]), ("hidden", "static"))
        self.assertEqual(plan["reason_codes"][0], "user_choice")
        self.assertEqual(VD.validate_plan(plan), [])
        static = direct(changed(scene("process"), prefer="static"), AT["process"])
        self.assertEqual((static["motion_intent"], static["camera_intent"], static["timing"]["reveal"]), ("none", "static", "together"))

    def test_an_impossible_choice_is_repaired_and_said_so(self):
        plan = direct(changed(scene("formula"), primary_visual="diagram"))
        self.assertEqual((plan["primary_visual"]["kind"], plan["locked"]), ("formula", []))
        self.assertTrue(any("could not be kept" in n for n in plan["notes"]), plan["notes"])
        no_presenter = changed({**scene("formula"), "aadhi_position": "hidden"}, presenter_role="dominant")
        plan = direct(no_presenter, settings={"mode": "cinematic"})
        self.assertEqual(plan["presenter"]["role"], "hidden")
        self.assertTrue(any("no presenter" in n for n in plan["notes"]), plan["notes"])

    def test_automatic_gives_a_choice_back_and_bad_choices_are_refused(self):
        self.assertEqual(VD.user_overrides(changed(scene("formula"), presenter_role="auto", camera_intent="static")), {"camera_intent": "static"})
        self.assertEqual(direct(changed(scene("formula"), presenter_role="auto"))["source"], "rules")
        self.assertEqual(VD.user_overrides({**scene("formula"), "visual_review": {"direction": {"status": "approved", "overrides": {"prefer": "static"}}}}), {})
        self.assertEqual(VD.check_user_overrides({"strategy": "timeline", "prefer": "auto"}), {"strategy": "timeline", "prefer": "auto"})
        for bad in ({"colour": "red"}, {"strategy": "explode"}, {"presenter_role": "giant"}, {"prefer": "newest"}, "static"):
            with self.assertRaises(ValueError):
                VD.check_user_overrides(bad)


class ContinuityTest(unittest.TestCase):
    CONCEPTS = [{"id": "c1", "title": "Newton's second law"}, {"id": "c2", "title": "Friction"}]

    def test_a_concept_seen_before_keeps_its_representation(self):
        again = scene("formula", title="Newton's second law (contd.)", concept_id="c1",
                      html="<div class='formula-block'>\\[a = F/m\\]</div><p>Acceleration = Force / Mass</p>")
        plans = VD.direct_lesson([scene("intro"), scene("formula", concept_id="c1"), again, scene("formula", title="Friction", concept_id="c2")],
                                 SETTINGS, self.CONCEPTS)
        self.assertEqual(plans[1]["continuity"], {"concept": "id:c1", "from_scene": None, "kept": False})  # first time: nothing earlier
        self.assertEqual(plans[2]["continuity"], {"concept": "id:c1", "from_scene": 1, "kept": True})
        self.assertTrue({"concept_seen_before", "continuity_kept"} <= set(plans[2]["reason_codes"]))
        self.assertIn("Newton's second law", plans[2]["learning_goal"])  # the map's name, not "(contd.)"
        self.assertIsNone(plans[3]["continuity"]["from_scene"])  # another concept
        self.assertIsNone(plans[0]["continuity"]["from_scene"])

    def test_the_lesson_memory_is_bounded(self):
        scenes = [{"type": "content", "title": f"Idea {n}", "concept_id": f"k{n}", "html": "<p>A short point.</p>", "narration": "x"}
                  for n in range(VD.MEMORY_LIMIT + 2)]
        scenes += [dict(scenes[0]), dict(scenes[-1])]
        plans = VD.direct_lesson(scenes, SETTINGS)
        self.assertEqual(plans[-2]["continuity"]["from_scene"], 0)
        self.assertIsNone(plans[-1]["continuity"]["from_scene"])  # past the memory's limit: not remembered


class AiAssistTest(unittest.TestCase):
    def lesson(self):
        """intro, process, the diagram scene (where the rules are undecided: guide or demonstrator), quiz, summary."""
        scenes = lesson()
        return [scenes[AT[n]] for n in ("intro", "process", "diagram", "quiz", "summary")]

    def many_ambiguous(self, n=8):
        return [scene("diagram", title=f"Inside plant part {k}") for k in range(n)]

    def run_ai(self, mode="ok", scenes=None, force=None):
        env = {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_MODE": mode}
        calls = []
        real = VD.fake_direct_model

        def counting(system, user, e):
            calls.append(user[:40])
            return real(system, user, e)
        with mock.patch.object(VD, "fake_direct_model", side_effect=counting):
            plans = VD.direct_lesson(scenes or self.lesson(), {**SETTINGS, "director": "ai", "composer_provider": "fake"},
                                     ai={"env": env, "force": force or []})
        return plans, calls

    def test_only_ambiguous_scenes_ask_the_model(self):
        scenes = self.lesson()
        self.assertEqual([i for i, s in enumerate(scenes) if VD.ambiguous(understand(s, i, len(scenes)))], [2])
        plans, calls = self.run_ai()
        self.assertEqual(len(calls), 1)
        self.assertEqual((plans[2]["ai"]["status"], plans[2]["ai"]["provider"], plans[2]["source"]), ("ok", "fake", "ai"))
        self.assertIn("ai_suggestion", plans[2]["reason_codes"])
        self.assertEqual(VD.validate_plan(plans[2]), [])
        self.assertTrue(all("ai" not in p for i, p in enumerate(plans) if i != 2))

    def test_a_malformed_answer_gets_one_repair(self):
        plans, calls = self.run_ai("malformed_once")
        self.assertEqual(plans[2]["ai"]["status"], "repaired")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].startswith("TASK: REPAIR"))

    def test_invalid_fabricated_and_failing_answers_leave_the_rules_in_charge(self):
        rules = VD.direct_lesson(self.lesson(), SETTINGS)
        for mode, status in (("malformed", "invalid"), ("fabricate", "invalid"), ("fail", "failed")):
            plans, _calls = self.run_ai(mode)
            self.assertEqual((plans[2]["ai"]["status"], plans[2]["source"]), (status, "rules"), mode)
            self.assertEqual(plans[2]["fingerprint"], rules[2]["fingerprint"], mode)  # the rules' own direction
            self.assertEqual(VD.validate_plan(plans[2]), [], mode)
            text = json.dumps(plans)
            for fabricated in ("cinematic_explosion", "<img", "http", "rm -rf"):
                self.assertNotIn(fabricated, text, mode)

    def test_an_unavailable_provider_leaves_the_rules_in_charge(self):
        plans = VD.direct_lesson(self.lesson(), {**SETTINGS, "director": "ai", "composer_provider": "gemini"}, ai={"env": {}})
        self.assertEqual((plans[2]["ai"]["status"], plans[2]["source"]), ("unavailable", "rules"))

    def test_a_kept_suggestion_is_reused_and_regenerating_asks_again(self):
        first, _calls = self.run_ai()
        scenes = self.lesson()
        for s, plan in zip(scenes, first):
            s["visual_direction"] = plan
        again, calls = self.run_ai(scenes=scenes)
        self.assertEqual(len(calls), 0)  # the suggestion kept with the lesson (no second call)
        self.assertEqual((again[2]["source"], again[2]["fingerprint"]), ("ai", first[2]["fingerprint"]))
        _forced, calls = self.run_ai(scenes=scenes, force=[2])
        self.assertEqual(len(calls), 1)

    def test_regenerating_asks_about_that_scene_only(self):
        plans, calls = self.run_ai(scenes=self.many_ambiguous(), force=[7])
        self.assertEqual(len(calls), 1)  # the scene being regenerated, even past the sixth
        self.assertEqual(plans[7]["ai"]["status"], "ok")
        self.assertTrue(all("ai" not in p for p in plans[:7]))
        plans, calls = self.run_ai(force=[0])
        self.assertEqual((len(calls), list(i for i, p in enumerate(plans) if "ai" in p)), (1, [0]))  # even a scene the rules decided

    def test_the_budget_holds_and_the_scenes_not_asked_say_so(self):
        started = time.time()
        with mock.patch.object(VD, "AI_BUDGET_SECONDS", 0.5):
            plans = VD.direct_lesson(self.many_ambiguous(), {**SETTINGS, "director": "ai", "composer_provider": "fake"},
                                     ai={"env": {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_SECONDS": "3"}})
        self.assertLess(time.time() - started, 2.5)  # a slow model is not waited for (it answers after 3 s)
        self.assertEqual([p["ai"]["status"] for p in plans], ["timeout"] * VD.AI_CALLS_PER_LESSON + ["skipped"] * 2)
        self.assertTrue(all(p["source"] == "rules" and VD.validate_plan(p) == [] for p in plans))

    def test_suggestions_are_validated_strictly(self):
        good = {"strategy": "conceptual_diagram", "primary_visual": "diagram", "presenter_role": "guide", "camera_intent": "focus",
                "motion_intent": "highlight", "complexity": "simple", "emphasis": ["visual"], "reason_codes": ["diagram_available"],
                "reasoning": "long chain of thought", "html": "<b>x</b>"}
        decision, ignored = VD.validate_suggestion(good, ["board_text", "diagram"])
        self.assertEqual((decision["strategy"], decision["primary_visual"]), ("conceptual_diagram", "diagram"))
        self.assertEqual(ignored, ["html", "reasoning"])
        self.assertNotIn("reasoning", decision)
        _decision, ignored = VD.validate_suggestion({**good, **{f"extra_{n:02d}_" + "x" * 60: n for n in range(12)}})
        self.assertEqual(len(ignored), 10)
        self.assertTrue(all(len(k) <= 40 for k in ignored))
        bad_values = ({**good, "strategy": "explode"}, {**good, "camera_intent": "shake"}, {**good, "motion_intent": "wild"},
                      {**good, "presenter_role": "giant"}, {**good, "complexity": "extreme"}, {**good, "emphasis": ["everything"]},
                      {**good, "emphasis": ["visual", "board", "term", "step"]}, {**good, "reason_codes": ["user_choice"]},
                      {**good, "reason_codes": ["diagram_available", "look_cue", "little_text", "first_scene", "dense_text"]},
                      {**good, "emphasis": "visual"}, "not an object", ["a", "list"], None)
        for bad in bad_values:
            with self.assertRaises(VD.Invalid):
                VD.validate_suggestion(bad, None)
        with self.assertRaises(VD.Invalid):
            VD.validate_suggestion(good, ["board_text"])  # a visual the scene does not have


class DeterministicTest(unittest.TestCase):
    def test_deterministic_mode_never_calls_a_model(self):
        settings = {**SETTINGS, "composer_provider": "fake"}
        with mock.patch("source_documents.call_model", side_effect=AssertionError("no model in deterministic mode")), \
                mock.patch.object(VD, "fake_direct_model", side_effect=AssertionError("no model in deterministic mode")):
            a = VD.direct_lesson(lesson(), settings, ai={"env": {"AI_FAKE_PROVIDER": "1"}})
            b = VD.direct_lesson(lesson(), settings, ai={"env": {"AI_FAKE_PROVIDER": "1"}})
            stored = VD.directions_for(lesson(), {**settings, "director": "ai"})  # what the composer uses never asks either
            plans = C.compose_lesson(lesson(), settings)
        self.assertEqual(a, b)
        self.assertEqual([p["fingerprint"] for p in stored], [p["fingerprint"] for p in a])
        self.assertEqual(len(plans), len(a))


class IntegrationTest(unittest.TestCase):
    def test_the_composition_follows_the_direction(self):
        scenes = lesson()
        scenes[AT["diagram"]]["visual_plan"] = {"side": seen(WIDE)}  # as the Visual Router planned it
        plans = C.compose_lesson(scenes, SETTINGS)
        by = dict(zip(NAMES, plans))
        self.assertEqual(by["diagram"]["template"], "diagram_focus")
        roles = {name: layer(by[name], "board")["role"] for name in ("process", "timeline", "code", "summary")}
        self.assertEqual(roles, {"process": "steps", "timeline": "timeline", "code": "code_output", "summary": "key_points"})
        self.assertEqual(by["comparison"]["reveal"], "together")
        self.assertEqual(by["process"]["reveal"], "progressive")
        self.assertEqual(by["comparison"]["camera"]["movement"], "static")
        labels = layer(by["formula"], "labels")
        self.assertEqual([i["source"] for i in labels["items"]],
                         [{"kind": "direction", "field": "annotations", "index": n} for n in range(3)])
        self.assertTrue(all("text" not in item for item in labels["items"]))
        for s, p in zip(scenes, plans):
            self.assertEqual(C.validate_plan(p), [], p["template"])
            self.assertTrue(p["composition"]["direction"]["followed"], p["template"])
            text = json.dumps(p)
            self.assertNotIn(s["title"], text)  # the lesson's text stays in the scene (Phase 13's rule)
            self.assertNotIn("F = Force", text)
            self.assertNotIn("learning_goal", p["direction"])
            self.assertTrue({"strategy", "primary_kind", "presenter", "motion", "camera", "fingerprint"} <= set(p["direction"]))
        self.assertEqual(by["formula"]["direction"]["strategy"], "formula_explanation")

    def test_a_visual_not_routed_yet_is_carried_by_the_board_and_said_so(self):
        plan = C.compose_lesson(lesson(), SETTINGS)[AT["diagram"]]  # the screenplay asks for a diagram; no router plan yet
        self.assertEqual(plan["direction"]["primary_kind"], "diagram")
        self.assertFalse(plan["composition"]["direction"]["followed"])
        self.assertTrue(any("has none yet" in n for n in plan["direction"]["notes"]), plan["direction"]["notes"])
        self.assertEqual(C.validate_plan(plan), [])

    def test_a_user_composition_choice_still_beats_the_direction(self):
        scenes = lesson()
        scenes[AT["formula"]]["visual_review"] = {"composition": {"status": "changed", "overrides": {"template": "presenter_explanation"}}}
        plan = C.compose_lesson(scenes, SETTINGS)[AT["formula"]]
        self.assertEqual((plan["template"], plan["composition"]["source"]), ("presenter_explanation", "user"))
        self.assertFalse(plan["composition"]["direction"]["followed"])
        self.assertEqual(C.validate_plan(plan), [])

    def test_a_changed_direction_reopens_an_approved_composition(self):
        scenes = lesson()
        first = C.compose_lesson(scenes, SETTINGS)[AT["formula"]]
        scenes[AT["formula"]]["visual_review"] = {"composition": {"status": "approved", "fingerprint": first["fingerprint"]}}
        kept = C.compose_lesson(scenes, SETTINGS)[AT["formula"]]
        self.assertEqual(kept["review_status"], "approved")
        scenes[AT["formula"]]["visual_review"]["direction"] = {"status": "changed", "overrides": {"presenter_role": "hidden"}}
        reopened = C.compose_lesson(scenes, SETTINGS)[AT["formula"]]
        self.assertNotEqual(reopened["fingerprint"], first["fingerprint"])
        self.assertEqual((reopened["review_status"], reopened.get("review_stale")), ("pending", True))
        self.assertEqual((reopened["direction"]["source"], reopened["presenter"]["shown"]), ("user", False))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("vera")
        ensure_user("otto")
        cls.client = TestClient(server.app)

    def post(self, path, body, user="vera"):
        return self.client.post(path, json=body, headers=auth(user))

    def save(self, name):
        return self.post("/save-history", {"subject_name": name, "scenes": lesson()}).json()["id"]

    def saved_scenes(self, pid):
        return self.client.get(f"/api/projects/{pid}", headers=auth("vera")).json()["scenes"]

    def test_directions_and_settings(self):
        vocab = self.client.get("/api/cinematic", headers=auth("vera")).json()
        self.assertEqual(vocab["director"]["modes"], ["rules", "ai"])
        r = self.post("/api/cinematic/direction", {"scenes": lesson(), "settings": SETTINGS})
        self.assertEqual(r.status_code, 200, r.text)
        directions = r.json()["directions"]
        self.assertEqual(len(directions), 12)
        self.assertTrue(all(VD.validate_plan(d) == [] for d in directions))
        classic = self.post("/api/cinematic/direction", {"scenes": lesson(), "settings": {"mode": "classic"}}).json()
        self.assertEqual(classic["directions"], [None] * 12)
        for bad in ({"director": "magic"}, {"learner_level": "expert"}):
            self.assertEqual(self.post("/api/cinematic/direction", {"scenes": [], "settings": {**SETTINGS, **bad}}).status_code, 422, bad)
        plan = self.post("/api/cinematic/plan", {"scenes": lesson(), "settings": SETTINGS}).json()
        self.assertEqual([d["fingerprint"] for d in plan["directions"]], [d["fingerprint"] for d in directions])
        self.assertIsNone(self.post("/api/cinematic/plan", {"scenes": lesson(), "settings": {"mode": "classic"}}).json()["directions"])

    def test_directions_are_kept_with_the_saved_lesson(self):
        pid = self.save("D")
        self.assertEqual(self.post("/api/cinematic/direction", {"scenes": lesson(), "settings": SETTINGS, "project_id": pid}, "otto").status_code, 404)
        self.post("/api/cinematic/direction", {"scenes": lesson()[:3], "settings": SETTINGS, "project_id": pid})
        self.assertTrue(all("visual_direction" not in s for s in self.saved_scenes(pid)))  # the scenes do not line up: nothing saved
        directions = self.post("/api/cinematic/direction", {"scenes": lesson(), "settings": SETTINGS, "project_id": pid}).json()["directions"]
        self.assertEqual([s["visual_direction"] for s in self.saved_scenes(pid)], directions)

    def test_direction_review_change_automatic_and_reset(self):
        pid = self.save("R")
        body = {"project_id": pid, "scene_index": AT["formula"], "settings": SETTINGS}
        r = self.post("/api/cinematic/direction/review", {**body, "action": "change", "overrides": {"presenter_role": "hidden", "camera_intent": "static"}})
        self.assertEqual(r.status_code, 200, r.text)
        r = r.json()
        self.assertEqual((r["review"]["status"], r["review"]["overrides"]), ("changed", {"presenter_role": "hidden", "camera_intent": "static"}))
        self.assertEqual((r["direction"]["source"], r["direction"]["locked"]), ("user", ["camera_intent", "presenter_role"]))
        self.assertEqual(r["plan"]["direction"]["source"], "user")
        self.assertFalse(r["plan"]["presenter"]["shown"])
        saved = self.saved_scenes(pid)[AT["formula"]]
        self.assertEqual(saved["visual_review"]["direction"]["overrides"], {"presenter_role": "hidden", "camera_intent": "static"})
        self.assertEqual(saved["visual_direction"]["fingerprint"], r["direction"]["fingerprint"])
        r = self.post("/api/cinematic/direction/review", {**body, "action": "change", "overrides": {"presenter_role": "auto"}}).json()
        self.assertEqual(r["review"]["overrides"], {"camera_intent": "static"})  # "Automatic" gives that choice back
        r = self.post("/api/cinematic/direction/review", {**body, "action": "reset"}).json()
        self.assertIsNone(r["review"])
        self.assertEqual((r["direction"]["source"], r["direction"]["locked"]), ("rules", []))
        self.assertNotIn("direction", self.saved_scenes(pid)[AT["formula"]].get("visual_review") or {})
        for bad in ({"presenter_role": "giant"}, {"colour": "red"}):
            self.assertEqual(self.post("/api/cinematic/direction/review", {**body, "action": "change", "overrides": bad}).status_code, 422, bad)
        self.assertEqual(self.post("/api/cinematic/direction/review", {**body, "action": "change", "overrides": {"prefer": "static"}}, "otto").status_code, 404)

    def test_regenerate_direction_only(self):
        pid = self.save("G")
        with database.SessionLocal() as db:
            runs_before = db.query(models.AIGenerationRun).count()
        body = {"scenes": lesson(), "scene_index": AT["diagram"], "project_id": pid, "settings": SETTINGS}
        r = self.post("/api/cinematic/direction/regenerate", body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["direction"]["strategy"], "conceptual_diagram")
        self.assertEqual(VD.validate_plan(r.json()["direction"]), [])
        self.assertEqual(C.validate_plan(r.json()["plan"]), [])
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.AIGenerationRun).count(), runs_before)  # no media was generated
        saved = self.saved_scenes(pid)
        self.assertEqual(saved[AT["diagram"]]["visual_direction"]["fingerprint"], r.json()["direction"]["fingerprint"])
        self.assertEqual(self.post("/api/cinematic/direction/regenerate", body, "otto").status_code, 404)
        self.assertEqual(self.post("/api/cinematic/direction/regenerate", {**body, "scene_index": 99}).status_code, 404)


class ReviewFindingsTest(unittest.TestCase):
    """The defects the end-of-phase security audit and correctness review found, each pinned by a test."""
    AI = {**SETTINGS, "director": "ai", "composer_provider": "fake"}
    ENV = {"AI_FAKE_PROVIDER": "1"}

    def test_a_crafted_scene_cannot_stall_the_server(self):
        started = time.time()
        many = scene("diagram", visual={"type": "diagram", "keywords": [f"term {k}" for k in range(8000)]})
        spaces = scene("comparison", title="Comparison a" + " " * 40000 + "b")
        for s in (many, spaces):
            VD.direct_scene(understand(s), s)
        self.assertLess(time.time() - started, 2.0)
        self.assertLessEqual(len(understand(many)["terms"]), 8)

    def test_malformed_screenplay_fields_never_fail(self):
        for odd in ({"concept_id": ["c1"]}, {"concept_id": {"x": 1}}, {"visual": {"type": "diagram", "keywords": "Photosynthesis"}},
                    {"visual": {"type": "diagram", "keywords": 5}}):
            plan = direct(scene("diagram", **odd))
            self.assertEqual(VD.validate_plan(plan), [], odd)
            self.assertFalse(any(len(t) == 1 for t in (plan["route"] or {}).get("match_terms", [])), odd)  # never one term per letter

    def test_a_choice_outside_the_vocabulary_is_ignored(self):
        plan = direct(changed(scene("process"), presenter_role="small", strategy="explode", prefer="never"))
        self.assertEqual((plan["source"], plan["locked"]), ("rules", []))

    def test_a_forged_kept_suggestion_is_rebuilt_never_trusted(self):
        scenes = lesson()
        u = VD.understand(scenes[AT["diagram"]], AT["diagram"], 12, self.AI)
        key = VD.ai_key(u, "fake", "fake-director-1")
        forged = {"status": "ok", "key": key, "provider": "x" * 500, "model": "fake-director-1", "error": "e" * 5000, "ignored": {"a": 1},
                  "decision": {"strategy": "conceptual_diagram", "primary_visual": "diagram", "presenter_role": "guide", "camera_intent": "static",
                               "motion_intent": "highlight", "complexity": "simple", "emphasis": [], "reason_codes": [], "html": "<img onerror=x>"}}
        for decision in (forged["decision"], ["not", "a", "dict"], {"strategy": "diagram"}):
            scenes[AT["diagram"]]["visual_direction"] = {"ai": {**forged, "decision": decision}}
            plans = VD.direct_lesson(scenes, self.AI, ai={"env": self.ENV, "cached_only": True})
            ai = plans[AT["diagram"]].get("ai") or {}
            text = json.dumps(plans[AT["diagram"]])
            self.assertNotIn("onerror", text)
            self.assertLessEqual(len(ai.get("provider", "")), 20)
            self.assertLessEqual(len(ai.get("error", "")), 200)
            self.assertNotIn("ignored", ai)

    def test_a_stored_direction_is_never_reused_as_it_was_sent(self):
        scenes = lesson()
        plan = VD.direct_lesson(scenes, SETTINGS)[AT["process"]]
        scenes[AT["process"]]["visual_direction"] = {**plan, "notes": ["n" * 25000], "strategy_label": "<img onerror=x>", "source": "ai"}
        again = VD.directions_for(scenes, self.AI)[AT["process"]]
        self.assertNotIn("onerror", json.dumps(again))
        self.assertEqual(again["source"], "rules")
        self.assertTrue(all(len(n) < 300 for n in again["notes"]))

    def test_an_ai_suggestion_survives_the_router_and_presenter_steps(self):
        scenes = lesson()
        scenes[AT["diagram"]].pop("visual_plan", None)
        before = VD.direct_lesson(scenes, self.AI, ai={"env": self.ENV, "force": [AT["diagram"]]})
        scenes[AT["diagram"]]["visual_direction"] = before[AT["diagram"]]
        scenes[AT["diagram"]]["visual_plan"] = {"side": seen(WIDE)}  # the router has since placed a library picture
        after = VD.directions_for(scenes, self.AI)[AT["diagram"]]
        self.assertEqual(before[AT["diagram"]]["ai"]["key"], (after.get("ai") or {}).get("key"))
        self.assertEqual(after["ai"]["status"], "ok")
        nothing = {"source": "NONE", "media": "STATIC_IMAGE", "selection": "none", "would_require": "AI_IMAGE"}
        scenes[AT["diagram"]]["visual_plan"] = {"side": nothing}  # the router found nothing: a picture suggestion no longer applies
        gone = VD.directions_for(scenes, self.AI)[AT["diagram"]]
        self.assertNotIn(gone["primary_visual"]["kind"], PICTURES)

    def test_an_unavailable_model_is_still_said_after_the_plan(self):
        scenes = lesson()
        offline = {**SETTINGS, "director": "ai", "composer_provider": "gemini"}
        first = VD.direct_lesson(scenes, offline, ai={"env": {}})
        asked = [i for i, p in enumerate(first) if p.get("ai")]
        self.assertTrue(asked)
        for i, p in enumerate(first):
            scenes[i]["visual_direction"] = p
        again = VD.directions_for(scenes, offline)  # the composition's own rebuild (never asks a model)
        self.assertEqual([again[i]["ai"]["status"] for i in asked], ["unavailable"] * len(asked))
        self.assertTrue(all(p["source"] != "ai" for p in again))

    def test_a_strategy_must_fit_the_scene_content(self):
        # the acceptance audit: a "timeline" for four undated steps was accepted (from the model, or as a user's choice)
        process = scene("process")
        u = understand(process, index=AT["process"])
        timeline = VD._c("timeline", "step_flow", "board", "guide", "points_to_board", "follow_process", "sequence", [])
        self.assertIn("the scene's content does not support this strategy", VD.check(u, timeline))
        for rank, c in enumerate(VD.rule_candidates(u)):  # the rules' own candidates always fit
            self.assertEqual(VD.check(u, VD.repair(u, c)[0]), [], c["strategy"])
        answer = json.dumps({"strategy": "timeline", "primary_visual": "step_flow", "presenter_role": "guide", "camera_intent": "static",
                             "motion_intent": "sequence", "complexity": "simple", "emphasis": [], "reason_codes": []})
        with mock.patch.object(VD, "fake_direct_model", return_value=answer):
            plans = VD.direct_lesson(lesson(), self.AI, ai={"env": self.ENV, "force": [AT["process"]]})
        self.assertEqual((plans[AT["process"]]["strategy"], plans[AT["process"]]["source"]), ("step_by_step", "rules"))
        chosen = direct(changed(process, strategy="timeline"), index=AT["process"])
        self.assertEqual(chosen["strategy"], "step_by_step")
        self.assertTrue(any("could not be kept" in n for n in chosen["notes"]), chosen["notes"])

    def test_the_presenter_stays_small_beside_a_formula(self):
        # Rule G (and Phase 14): small when the visual evidence (here the formula) is primary
        self.assertEqual(direct(scene("formula"), index=AT["formula"])["presenter"], {"role": "guide", "interaction": "points_to_board"})
        plan = C.compose_lesson(lesson(), SETTINGS)[AT["formula"]]
        self.assertEqual((plan["template"], plan["composition"]["decision"]["presenter_role"]), ("formula_focus", "small"))

    def test_code_with_its_output_keeps_the_code_layout(self):
        line = "total_energy = kinetic_energy + potential_energy  # joules"
        code = "\n".join([line, "print(total_energy)"])
        s = {"type": "content", "title": "Energy in code", "html": f"<pre><code>{code}</code></pre><p>This prints 42.</p>",
             "narration": "Here is the code.", "presenter_plan": teacher()}
        plans = C.compose_lesson([lesson()[0], s], SETTINGS)
        self.assertEqual((plans[1]["template"], layer(plans[1], "board")["role"]), ("code_focus", "code_output"))
        self.assertNotEqual(plans[1]["composition"]["source"], "fallback")

    def test_a_still_choice_stops_the_camera(self):
        scenes = lesson()
        scenes[AT["definition"]] = changed(scenes[AT["definition"]], motion_intent="none")
        plan = C.compose_lesson(scenes, SETTINGS)[AT["definition"]]
        self.assertEqual((plan["camera"]["movement"], plan["motion"]), ("static", "none"))


if __name__ == "__main__":
    unittest.main()
