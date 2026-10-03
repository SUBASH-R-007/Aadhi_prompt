"""Backend tests of the Intelligent Scene Composer (Phase 14): scene intent and content classification, priorities,
candidate templates and the chosen composition, hard constraints and soft preferences, validation and automatic repair,
the safe fallback, deterministic and AI-assisted modes (invalid, fabricated and failing model answers, the suggestion
kept with the lesson), user overrides and their priority, fingerprints and review, regeneration, media awareness,
timing and backward compatibility.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_composer.py" -v

The AI-assisted mode uses the composer's local stand-in model; no real provider, no network.
"""
import copy
import json
import time
import unittest
from unittest import mock

from backend_env import FFMPEG, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import cinematic as C  # noqa: E402
import composer as K  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import scene_intent as I  # noqa: E402
import server  # noqa: E402


def teacher(**extra):
    return {"presenter_id": "aadhi-teacher", "type": "illustrated", "enabled": True, "position": "right", "placement": "side", **extra}


SETTINGS = {"mode": "cinematic", "presenter_legacy": False, "presenter_id": "aadhi-teacher"}
SQUARE, WIDE, TALL = "a" * 32, "b" * 32, "c" * 32
ASSETS = {SQUARE: {"kind": "image", "url": "/s", "width": 900, "height": 900}, WIDE: {"kind": "image", "url": "/w", "width": 1200, "height": 800},
          TALL: {"kind": "image", "url": "/t", "width": 600, "height": 900}}


def seen(asset_id):
    return {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": asset_id, "selection": "matched"}


def mini_lesson():
    """The visual validation lesson: introduction, definition, diagram, formula, code, comparison, quiz, summary."""
    return [
        {"type": "content", "title": "Photosynthesis", "subtitle": "How plants make food", "html": "<p>Plants turn sunlight into sugar.</p>",
         "narration": "Welcome! Today we learn how plants make food.", "presenter_plan": teacher()},
        {"type": "content", "title": "What is photosynthesis?", "html": "<div class='definition'>Photosynthesis is the process by which green plants use sunlight to make glucose from carbon dioxide and water.</div>",
         "visual_plan": {"side": seen(WIDE)}, "narration": "Here is the definition. [SYNC] Photosynthesis is the process...", "presenter_plan": teacher()},
        {"type": "content", "title": "Inside a plant", "html": "<ul><li>Sunlight</li><li>Water</li><li>Carbon dioxide</li></ul>",
         "visual_plan": {"side": seen(SQUARE)}, "narration": "Look at this diagram. [SYNC] Sunlight. [SYNC] Water. [SYNC] Air.",
         "presenter_plan": teacher(), "composition": {"labels": ["Sunlight → Chlorophyll → Glucose"]}},
        {"type": "content", "title": "Newton's second law", "html": "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
         "narration": "Here is the law. [SYNC] F equals m a. [SYNC] Force is mass times acceleration.", "presenter_plan": teacher(),
         "composition": {"labels": [{"text": "F = Force"}, {"text": "m = Mass"}, {"text": "a = Acceleration"}]}},
        {"type": "content", "title": "Python for loop", "html": "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre><p>It prints 0 to 4.</p>",
         "narration": "A loop.", "presenter_plan": teacher()},
        {"type": "content", "title": "Plants vs Animals", "html": "<table><tr><th>Plants</th><th>Animals</th></tr><tr><td>Make food</td><td>Eat food</td></tr><tr><td>Release oxygen</td><td>Release carbon dioxide</td></tr></table>",
         "narration": "Let us compare.", "presenter_plan": teacher()},
        {"type": "quiz_checkpoint", "title": "Quick check", "question": "What does a plant make?", "options": ["Glucose", "Salt", "Iron"],
         "narration": "Quick question.", "presenter_plan": teacher()},
        {"type": "key-takeaway", "title": "Key takeaways", "html": "<ul class='takeaway-list'><li>Plants make glucose</li><li>Chlorophyll captures light</li><li>Oxygen is released</li></ul>",
         "narration": "Let us recap.", "presenter_plan": teacher()},
    ]


def compose(scenes=None, settings=None, **kw):
    return C.compose_lesson(scenes or mini_lesson(), {**SETTINGS, **(settings or {})}, ASSETS.get, **kw)


def layer(plan, layer_id):
    return next((l for l in plan["layers"] if l["id"] == layer_id), None)


class IntentTest(unittest.TestCase):
    def test_purposes_and_content(self):
        scenes = mini_lesson()
        intents = [I.scene_intent(s, i, len(scenes), SETTINGS, ASSETS.get) for i, s in enumerate(scenes)]
        self.assertEqual([it["purpose"] for it in intents],
                         ["intro", "definition", "diagram", "formula", "code", "comparison", "quiz", "summary"])
        self.assertTrue(intents[1]["content"]["definition"])
        self.assertTrue(intents[3]["content"]["formula"])
        self.assertTrue(intents[4]["content"]["code"])
        self.assertTrue(intents[5]["content"]["table"])
        self.assertEqual((intents[2]["visual"]["kind"], intents[2]["visual"]["orientation"], intents[2]["visual"]["aspect"]), ("image", "square", 1.0))
        self.assertEqual(intents[1]["visual"]["orientation"], "landscape")  # the asset's own size, from the library

    def test_classification_details(self):
        cases = {
            "process": {"type": "content", "title": "The cycle", "html": "<ol><li>Collect</li><li>Heat</li><li>Cool</li></ol>", "narration": "First collect, then heat."},
            "comparison": {"type": "content", "title": "Mitosis versus meiosis", "html": "<p>Two ways cells divide.</p>"},
            "summary": {"type": "summary", "title": "Wrap-up", "html": "<p>We learned a lot.</p>"},
            "recap": {"type": "recap", "title": "Previously", "html": "<ul><li>A</li></ul>"},
            "transition": {"type": "chapter_card", "title": "Part 2"},
            "demonstration": {"type": "simulation", "title": "Watch", "manim_code": "x"},
            "example": {"type": "example", "title": "Worked example", "html": "<p>Suppose a ball falls.</p>"},
        }
        for purpose, scene in cases.items():
            self.assertEqual(I.scene_intent(scene, 3, 9)["purpose"], purpose, purpose)
        two_lists = {"type": "content", "title": "Pros and cons", "html": "<ul><li>Fast</li></ul><ul><li>Costly</li></ul>"}
        self.assertEqual(I.scene_intent(two_lists, 2, 5)["purpose"], "comparison")

    def test_density_priority_and_weights(self):
        dense = {"type": "content", "title": "Everything", "html": "<p>" + " ".join(["word"] * 120) + "</p>"}
        it = I.scene_intent(dense, 2, 5)
        self.assertEqual(it["density"], "high")
        self.assertGreater(it["reading_seconds"], 30)
        formula = I.scene_intent(mini_lesson()[3], 3, 8, SETTINGS)
        self.assertEqual(formula["priority"][0], "formula")
        intro = I.scene_intent(mini_lesson()[0], 0, 8, SETTINGS)
        self.assertGreaterEqual(intro["weights"]["presenter"], 0.85)  # the presenter leads an introduction
        code = I.scene_intent(mini_lesson()[4], 4, 8, SETTINGS)
        self.assertLess(code["weights"]["presenter"], code["weights"]["text"])

    def test_presenter_availability_and_ambiguity(self):
        ai = {**mini_lesson()[2], "presenter_plan": teacher(presenter_id="ai-teacher", type="ai_avatar")}
        it = I.scene_intent(ai, 2, 8, {**SETTINGS, "presenter_id": "ai-teacher"}, ASSETS.get)
        self.assertEqual((it["presenter"]["enabled"], it["presenter"]["available"]), (True, False))  # no clip yet
        busy = {"type": "content", "title": "Forces", "html": "<div class='formula-block'>\\[F=ma\\]</div><p>Look at the diagram.</p>",
                "visual_plan": {"side": seen(WIDE)}}
        self.assertTrue(I.scene_intent(busy, 2, 5, SETTINGS, ASSETS.get)["ambiguous"])  # formula and diagram compete
        self.assertFalse(I.scene_intent(mini_lesson()[4], 4, 8, SETTINGS)["ambiguous"])

    def test_intent_is_stable_and_never_edits_the_scene(self):
        scene = mini_lesson()[2]
        before = copy.deepcopy(scene)
        a = I.scene_intent(scene, 2, 8, SETTINGS, ASSETS.get)
        self.assertEqual(scene, before)
        self.assertEqual(a["hash"], I.scene_intent(copy.deepcopy(scene), 2, 8, SETTINGS, ASSETS.get)["hash"])
        self.assertNotEqual(a["hash"], I.scene_intent({**scene, "html": "<p>Other</p>"}, 2, 8, SETTINGS, ASSETS.get)["hash"])


class ComposerTest(unittest.TestCase):
    def test_the_mini_lesson_is_composed_by_purpose(self):
        plans = compose()
        self.assertEqual([p["template"] for p in plans],
                         ["presenter_intro", "presenter_plus_visual", "diagram_focus", "formula_focus", "code_focus", "comparison", "quiz", "summary"])
        roles = [p["composition"]["decision"].get("presenter_role") for p in plans]
        self.assertEqual(roles[0], "dominant")        # introduction: the presenter leads
        self.assertEqual(roles[2], "small")           # a diagram: the presenter stays small
        self.assertIn(roles[4], ("hidden", "small"))   # code: the presenter out of the way
        self.assertEqual(plans[4]["camera"]["movement"], "static")
        self.assertEqual(plans[5]["camera"]["movement"], "static")  # a comparison keeps its symmetry still
        for p in plans:
            self.assertEqual(C.validate_plan(p), [], p["template"])
            self.assertEqual(p["warnings"], [], p["template"])
            self.assertIn(p["composition"]["source"], ("rules",))
            self.assertTrue(p["composition"]["reasons"])
            self.assertEqual(p["review_status"], "pending")  # compositions are never approved automatically

    def test_candidates_are_few_and_valid(self):
        scenes = mini_lesson()
        for i, s in enumerate(scenes):
            it = I.scene_intent(s, i, len(scenes), SETTINGS, ASSETS.get)
            candidates = K.rule_candidates(it)
            self.assertTrue(1 <= len(candidates) <= 4, it["purpose"])
            for d in candidates:
                self.assertIn(d["template"], C.TEMPLATES)
                self.assertIn(d["presenter_role"], K.PRESENTER_ROLES)

    def test_hard_constraint_readability(self):
        long_text = {"type": "content", "title": "A long explanation", "narration": "x",
                     "html": "".join(f"<p>Sentence number {n} explains another part of the idea in some detail.</p>" for n in range(9)),
                     "presenter_plan": teacher()}
        plan = C.compose_scene(long_text, 2, 5, SETTINGS)
        board = layer(plan, "board")
        self.assertGreaterEqual(K.text_scale(I.scene_intent(long_text, 2, 5, SETTINGS), board["box"], "body"), K.MIN_TEXT_SCALE)
        # the presenter gives way rather than the text shrinking
        self.assertIn(plan["composition"]["decision"].get("presenter_role"), ("small", "hidden"))

    def test_presenter_never_covers_content_and_subtitles_stay_clear(self):
        for p in compose():
            important = [l for l in p["layers"] if l["important"] and l["type"] not in ("subtitles", "presenter")]
            presenter = layer(p, "presenter")
            for l in important:
                self.assertFalse(C.overlap(l["box"], C.SUBTITLES), (p["template"], l["id"]))
                if presenter:
                    self.assertFalse(C.overlap(l["box"], presenter["box"]), (p["template"], l["id"]))

    def test_soft_preferences_consistency_and_variety(self):
        plans = compose(settings={"presenter_position": "left"}, scenes=[{**s, "presenter_plan": teacher(position="left")} for s in mini_lesson()])
        sides = {p["presenter"]["side"] for p in plans if p["presenter"]["shown"]}
        self.assertEqual(sides, {"left"})  # the presenter stays on the lesson's side
        # three identical explanation scenes in a row: the third may vary when an equal choice exists, never at random
        same = [{"type": "content", "title": f"Point {n}", "html": "<p>A short point.</p>", "narration": "x", "presenter_plan": teacher()} for n in range(4)]
        a = compose(scenes=same)
        b = compose(scenes=copy.deepcopy(same))
        self.assertEqual([p["plan_hash"] for p in a], [p["plan_hash"] for p in b])

    def test_automatic_repair_keeps_hard_constraints_over_a_user_choice(self):
        dense = {"type": "content", "title": "Loop", "narration": "x", "presenter_plan": teacher(),
                 "html": "<pre><code>" + "\n".join(f"running_total = running_total + measured_value_{n}  # reading {n}"
                                                    for n in range(9)) + "</code></pre>",  # ~62-character lines: too wide beside a full-size presenter
                 "visual_review": {"composition": {"status": "changed", "overrides": {"presenter_size": "dominant"}}}}
        plan = C.compose_scene(dense, 3, 6, SETTINGS)
        self.assertEqual(C.validate_plan(plan), [])
        self.assertNotEqual(plan["composition"]["decision"].get("presenter_role"), "dominant")
        self.assertTrue(any("could not be kept" in r for r in plan["composition"]["repairs"]), plan["composition"]["repairs"])

    def test_fallback_keeps_the_lesson_usable(self):
        huge = {"type": "content", "title": "Too much", "narration": "x", "presenter_plan": teacher(),
                "html": "<p>" + " ".join(["overflowing"] * 600) + "</p>"}
        plan = C.compose_scene(huge, 2, 5, SETTINGS)
        self.assertIsNotNone(plan)  # a scene that fits nowhere is still shown (the page's text fit then reports it)
        self.assertEqual(plan["composition"]["source"], "fallback")

    def test_deterministic_mode_never_calls_a_model(self):
        with mock.patch("source_documents.call_model", side_effect=AssertionError("no model in deterministic mode")), \
                mock.patch.object(K, "fake_compose_model", side_effect=AssertionError("no model in deterministic mode")):
            a = compose()
            b = compose()
        self.assertEqual([p["plan_hash"] for p in a], [p["plan_hash"] for p in b])

    def test_media_awareness(self):
        wide = {**mini_lesson()[2], "visual_plan": {"side": seen(WIDE)}, "composition": {}}
        tall = {**mini_lesson()[2], "visual_plan": {"side": seen(TALL)}, "composition": {}}
        wv = layer(C.compose_scene(wide, 2, 8, SETTINGS, ASSETS.get), "visual")["box"]
        tv = layer(C.compose_scene(tall, 2, 8, SETTINGS, ASSETS.get), "visual")["box"]
        self.assertGreater(wv["w"], tv["w"])  # a wide picture gets a wide box, a tall one a narrow box (never stretched)
        definition = compose()[1]
        v = layer(definition, "visual")["box"]
        self.assertLess(v["h"], 0.62)  # a wide picture in a column gets a card of its own shape
        self.assertAlmostEqual((v["w"] * 1280) / (v["h"] * 720), 1.5, delta=0.45)

    def test_timing(self):
        plans = compose()
        quiz, intro = plans[6], plans[0]
        self.assertGreater(layer(quiz, "presenter")["start"], layer(quiz, "board")["start"])  # the question first
        formula = plans[3]
        times = [i["at"] for i in layer(formula, "labels")["items"]]
        self.assertEqual(times, sorted(times))
        self.assertGreater(times[1] - times[0], 0.8)  # time to read each label
        self.assertIn("highlight", [e["event"] for e in formula["timeline"]])  # the formula is emphasised at its reveal
        self.assertGreater(intro["duration"], 0)

    def test_performance(self):
        scenes = [copy.deepcopy(s) for s in mini_lesson() for _ in range(25)]  # 200 scenes
        t0 = time.time()
        plans = compose(scenes=scenes)
        self.assertEqual(len(plans), 200)
        self.assertLess(time.time() - t0, 8.0)


class AiAssistTest(unittest.TestCase):
    def ambiguous(self):
        return {"type": "content", "title": "Forces on a ramp", "html": "<div class='formula-block'>\\[F = mg\\sin\\theta\\]</div><p>Look at the ramp.</p>",
                "visual_plan": {"side": seen(WIDE)}, "narration": "Look at the ramp. [SYNC] The force is m g sin theta.", "presenter_plan": teacher()}

    def lesson(self):
        scenes = mini_lesson()
        scenes.insert(3, self.ambiguous())
        return scenes

    def run_ai(self, mode="ok", scenes=None, force=None):
        env = {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_MODE": mode}
        calls = []
        real = K.fake_compose_model

        def counting(system, user, e):
            calls.append(user[:40])
            return real(system, user, e)
        with mock.patch.object(K, "fake_compose_model", side_effect=counting):
            plans = C.compose_lesson(scenes or self.lesson(), {**SETTINGS, "composer": "ai", "composer_provider": "fake"}, ASSETS.get,
                                     ai={"env": env, "force": force or []})
        return plans, calls

    def test_only_ambiguous_scenes_ask_the_model(self):
        plans, calls = self.run_ai()
        self.assertEqual(len(calls), 1)  # only the scene where the formula and the diagram compete
        ai = plans[3]["composition"]["ai"]
        self.assertEqual((ai["status"], ai["provider"]), ("ok", "fake"))
        self.assertEqual(plans[3]["composition"]["source"], "ai")
        self.assertEqual(C.validate_plan(plans[3]), [])
        self.assertNotIn("ai", plans[0]["composition"])

    def test_a_malformed_answer_gets_one_repair(self):
        plans, calls = self.run_ai("malformed_once")
        self.assertEqual(plans[3]["composition"]["ai"]["status"], "repaired")
        self.assertEqual(len(calls), 2)

    def test_invalid_fabricated_and_failing_answers_leave_the_rules_in_charge(self):
        for mode, status in (("malformed", "invalid"), ("fabricate", "invalid"), ("fail", "failed")):
            plans, _calls = self.run_ai(mode)
            c = plans[3]["composition"]
            self.assertEqual(c["ai"]["status"], status, mode)
            self.assertEqual(c["source"], "rules", mode)
            self.assertEqual(C.validate_plan(plans[3]), [], mode)
            self.assertNotIn("cinematic_explosion", json.dumps(plans[3]))
            self.assertNotIn("<script", json.dumps(plans[3]))

    def test_suggestions_are_validated_strictly(self):
        good = {"template": "diagram_focus", "presenter_role": "small", "visual_role": "dominant", "camera": "focus", "motion": "subtle",
                "presenter_side": "right", "text_priority": "low", "emphasis": ["visual"], "reasoning": "long chain of thought", "html": "<b>x</b>"}
        decision, ignored = K.validate_suggestion(good, None)
        self.assertEqual(decision["template"], "diagram_focus")
        self.assertEqual(ignored, ["html", "reasoning"])  # extra fields are never used (nor stored as reasons)
        for bad in ({**good, "template": "explode"}, {**good, "camera": "shake"}, {**good, "motion": "high"}, {**good, "emphasis": ["everything"]},
                    {**good, "presenter_role": "giant"}, "not an object"):
            with self.assertRaises(K.Invalid):
                K.validate_suggestion(bad, None)

    def test_unavailable_provider_and_the_kept_suggestion(self):
        plans = C.compose_lesson(self.lesson(), {**SETTINGS, "composer": "ai", "composer_provider": "gemini"}, ASSETS.get, ai={"env": {}})
        self.assertEqual(plans[3]["composition"]["ai"]["status"], "unavailable")
        self.assertEqual(plans[3]["composition"]["source"], "rules")
        first, calls = self.run_ai()
        scenes = self.lesson()
        for scene, plan in zip(scenes, first):
            scene["cinematic_plan"] = plan
        again, calls2 = self.run_ai(scenes=scenes)
        self.assertEqual(len(calls2), 0)  # the suggestion kept with the lesson is reused (no second call)
        self.assertEqual(again[3]["template"], first[3]["template"])
        forced, calls3 = self.run_ai(scenes=scenes, force=[3])
        self.assertEqual(len(calls3), 1)  # regenerating asks again

    def test_a_user_choice_beats_the_ai(self):
        scenes = self.lesson()
        scenes[3]["visual_review"] = {"composition": {"status": "changed", "overrides": {"template": "presenter_plus_visual"}}}
        plans, _calls = self.run_ai(scenes=scenes)
        self.assertEqual(plans[3]["template"], "presenter_plus_visual")
        self.assertEqual(plans[3]["composition"]["source"], "user")

    def many_ambiguous(self, n=8):
        return [{**self.ambiguous(), "title": f"Forces on ramp {k}"} for k in range(n)]

    def test_the_budget_holds_and_the_scenes_not_asked_say_so(self):
        started = time.time()
        with mock.patch.object(K, "AI_BUDGET_SECONDS", 0.5):
            plans = C.compose_lesson(self.many_ambiguous(), {**SETTINGS, "composer": "ai", "composer_provider": "fake"}, ASSETS.get,
                                     ai={"env": {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_SECONDS": "3"}})
        self.assertLess(time.time() - started, 2.5)  # a slow model is not waited for (it answers after 3 s)
        statuses = [p["composition"]["ai"]["status"] for p in plans]
        self.assertEqual(statuses, ["timeout"] * K.AI_CALLS_PER_LESSON + ["skipped"] * 2)
        self.assertTrue(all(p["composition"]["source"] == "rules" and C.validate_plan(p) == [] for p in plans))

    def test_regenerating_asks_about_that_scene_only(self):
        plans, calls = self.run_ai(scenes=self.many_ambiguous(), force=[7])
        self.assertEqual(len(calls), 1)  # the scene being regenerated, even past the sixth
        self.assertEqual(plans[7]["composition"]["ai"]["status"], "ok")
        self.assertTrue(all("ai" not in p["composition"] for p in plans[:7]))

    def test_an_ai_answer_stays_within_the_lesson_settings(self):
        answer = json.dumps({"template": "formula_focus", "presenter_role": "small", "visual_role": "hidden", "camera": "focus",
                             "motion": "moderate", "presenter_side": "left", "text_priority": "high", "emphasis": []})
        with mock.patch.object(K, "fake_compose_model", return_value=answer):
            plans = C.compose_lesson(self.lesson(), {**SETTINGS, "composer": "ai", "composer_provider": "fake"}, ASSETS.get,
                                     ai={"env": {"AI_FAKE_PROVIDER": "1"}})
        plan = plans[3]
        self.assertEqual((plan["composition"]["source"], plan["template"]), ("ai", "formula_focus"))
        self.assertIsNotNone(layer(plan, "visual"))  # the model never drops the scene's visual
        self.assertNotEqual(plan["motion"], "moderate")  # nor goes above the lesson's motion
        self.assertEqual(plan["presenter"]["side"], "right")  # nor moves the presenter off the lesson's side


class OverridesAndReviewTest(unittest.TestCase):
    def test_override_priority(self):
        scene = {**mini_lesson()[2], "composition": {"template": "presenter_plus_visual", "labels": ["x"]}}
        plan = C.compose_scene(scene, 2, 8, SETTINGS, ASSETS.get)
        self.assertEqual((plan["template"], plan["composition"]["source"]), ("presenter_plus_visual", "screenplay"))
        user = {**scene, "visual_review": {"composition": {"status": "changed", "overrides": {"template": "diagram_focus", "presenter_size": "hidden",
                                                                                           "motion": "none"}}}}
        plan = C.compose_scene(user, 2, 8, SETTINGS, ASSETS.get)
        self.assertEqual((plan["template"], plan["composition"]["source"], plan["presenter"]["shown"], plan["camera"]["movement"]),
                         ("diagram_focus", "user", False, "static"))
        self.assertEqual(sorted(plan["composition"]["locked"]), ["motion", "presenter_size", "template"])

    def test_fingerprint_includes_the_composer(self):
        scene = mini_lesson()[2]
        style = C._style(SETTINGS)
        presenter = C.presenter_context(scene, SETTINGS)
        fp = C.input_fingerprint(scene, style, presenter)
        with mock.patch.object(C, "COMPOSER_VERSION", C.COMPOSER_VERSION + 1):
            self.assertNotEqual(fp, C.input_fingerprint(scene, style, presenter))  # a new composer re-opens approvals

    def test_phase13_decision_and_classic_still_work(self):
        self.assertIsNone(C.compose_scene(mini_lesson()[0], 0, 8, {"mode": "classic"}))
        plan = C.build_plan(mini_lesson()[3], 3, 8, SETTINGS)  # the Phase 13 choice, the composer's last resort
        self.assertEqual((plan["template"], C.validate_plan(plan)), ("formula_focus", []))
        old = {"type": "content", "title": "Old", "html": "<p>x</p>", "narration": "y", "aadhi_position": "left"}
        plan = C.compose_scene(old, 1, 3, {"mode": "cinematic"})
        self.assertEqual((plan["presenter"]["type"], plan["presenter"]["side"]), ("mascot", "left"))
        self.assertEqual(plan["camera"]["movement"], "static")  # Aadhi on screen: still camera

    def test_a_chosen_layout_keeps_its_own_arrangement(self):
        scene = {**mini_lesson()[2], "composition": {"template": "presenter_plus_visual"}}  # a diagram scene: the rules would keep him small
        plan = C.compose_scene(scene, 2, 8, SETTINGS, ASSETS.get)
        v, b, p = layer(plan, "visual")["box"], layer(plan, "board")["box"], layer(plan, "presenter")["box"]
        self.assertEqual(plan["composition"]["decision"]["presenter_role"], "secondary")
        self.assertLessEqual(v["x"] + v["w"], b["x"] + 0.001)  # the visual, the text beside it, the presenter beside them
        self.assertLessEqual(b["x"] + b["w"], p["x"] + 0.001)
        sized = {**scene, "visual_review": {"composition": {"status": "changed", "overrides": {"presenter_size": "small"}}}}
        self.assertEqual(C.compose_scene(sized, 2, 8, SETTINGS, ASSETS.get)["composition"]["decision"]["presenter_role"], "small")

    def test_the_user_outranks_the_screenplay_and_says_which_choice_is_whose(self):
        scene = {**mini_lesson()[1], "composition": {"presenter_position": "hidden", "shot": "close", "focus": "board", "camera": "focus"}}
        plan = C.compose_scene(scene, 1, 8, SETTINGS, ASSETS.get)
        self.assertFalse(plan["presenter"]["shown"])
        self.assertEqual((plan["camera"]["shot"], plan["camera"]["target"]), ("close", "board"))  # the screenplay's shot and focus apply
        user = {**scene, "visual_review": {"composition": {"status": "changed", "overrides": {"presenter_position": "left", "motion": "none"}}}}
        plan = C.compose_scene(user, 1, 8, SETTINGS, ASSETS.get)
        self.assertEqual((plan["presenter"]["shown"], plan["presenter"]["side"]), (True, "left"))  # the user brings him back
        c = plan["composition"]
        self.assertEqual(c["chosen"], ["motion", "presenter_position"])  # "Automatic" is offered for these only
        self.assertTrue({"shot", "focus", "camera"} <= set(c["locked"]))

    def test_a_fallback_says_when_your_choices_were_set_aside(self):
        huge = {"type": "content", "title": "Too much", "narration": "x", "presenter_plan": teacher(),
                "html": "<p>" + " ".join(["overflowing"] * 600) + "</p>",
                "visual_review": {"composition": {"status": "changed", "overrides": {"presenter_size": "dominant"}}}}
        plan = C.compose_scene(huge, 2, 5, SETTINGS)
        self.assertEqual(plan["composition"]["source"], "fallback")
        self.assertTrue(any("your choices could not be kept" in r for r in plan["composition"]["repairs"]))

    def test_an_approval_reopens_when_the_ai_decides_instead(self):
        rules = compose(AiAssistTest().lesson())
        scenes = AiAssistTest().lesson()
        for i in (0, 3):
            scenes[i]["visual_review"] = {"composition": {"status": "approved", "fingerprint": rules[i]["fingerprint"]}}
        plans, _calls = AiAssistTest().run_ai(scenes=scenes)
        self.assertEqual(plans[3]["composition"]["source"], "ai")
        self.assertEqual((plans[3]["review_status"], plans[3].get("review_stale")), ("pending", True))
        self.assertEqual(plans[0]["review_status"], "approved")  # a scene the model did not decide keeps its approval


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("rhea")
        ensure_user("otto")
        cls.client = TestClient(server.app)

    def test_composer_status_and_settings(self):
        vocab = self.client.get("/api/cinematic", headers=auth("rhea")).json()
        self.assertEqual(vocab["composer"]["modes"], ["rules", "ai"])
        self.assertFalse(vocab["composer"]["ai_available"])  # no text model on the test server
        self.assertIn("comparison", vocab["templates"])
        for bad in ({"composer": "magic"}, {"composer_provider": "cloud"}, {"composer_model": "bad model!"}):
            r = self.client.post("/api/cinematic/plan", json={"scenes": [], "settings": {**SETTINGS, **bad}}, headers=auth("rhea"))
            self.assertEqual(r.status_code, 422, bad)
        r = self.client.post("/api/cinematic/plan", json={"scenes": mini_lesson(), "settings": {**SETTINGS, "composer": "ai"}}, headers=auth("rhea"))
        self.assertEqual(r.status_code, 200)  # AI-assisted without a model: the rules compose every scene
        self.assertTrue(all(p["composition"]["source"] == "rules" for p in r.json()["plans"]))

    def test_review_overrides_with_automatic(self):
        pid = self.client.post("/save-history", json={"subject_name": "K", "scenes": mini_lesson()}, headers=auth("rhea")).json()["id"]
        body = {"project_id": pid, "scene_index": 2, "settings": SETTINGS}
        r = self.client.post("/api/cinematic/review", json={**body, "action": "change", "overrides": {"presenter_size": "hidden", "motion": "none"}},
                             headers=auth("rhea")).json()
        self.assertEqual(r["review"]["overrides"], {"presenter_size": "hidden", "motion": "none"})
        self.assertFalse(r["plan"]["presenter"]["shown"])
        r = self.client.post("/api/cinematic/review", json={**body, "action": "change", "overrides": {"presenter_size": "auto"}}, headers=auth("rhea")).json()
        self.assertEqual(r["review"]["overrides"], {"motion": "none"})  # "Automatic" gives that choice back
        r = self.client.post("/api/cinematic/review", json={**body, "action": "change", "overrides": {"motion": "auto"}}, headers=auth("rhea")).json()
        self.assertIsNone(r["review"])  # nothing chosen any more: back to the automatic composition, pending
        self.assertEqual(r["plan"]["review_status"], "pending")
        self.assertEqual(self.client.post("/api/cinematic/review", json={**body, "action": "change", "overrides": {"presenter_size": "enormous"}},
                                          headers=auth("rhea")).status_code, 422)

    def test_regenerate_composition_only(self):
        scenes = mini_lesson()
        pid = self.client.post("/save-history", json={"subject_name": "R", "scenes": scenes}, headers=auth("rhea")).json()["id"]
        kept = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 3, "action": "keep", "settings": SETTINGS},
                                headers=auth("rhea")).json()
        self.assertEqual(kept["review"]["status"], "approved")
        scenes[3]["visual_review"] = {"composition": kept["review"]}
        with database.SessionLocal() as db:
            runs_before = db.query(models.AIGenerationRun).count()
        r = self.client.post("/api/cinematic/regenerate", json={"scenes": scenes, "scene_index": 3, "project_id": pid, "settings": SETTINGS},
                             headers=auth("rhea"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["review"])  # a regenerated composition needs a new look
        self.assertEqual(r.json()["plan"]["review_status"], "pending")
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.AIGenerationRun).count(), runs_before)  # no media was generated
        saved = self.client.get(f"/api/projects/{pid}", headers=auth("rhea")).json()["scenes"][3]
        self.assertEqual(saved["cinematic_plan"]["template"], "formula_focus")
        self.assertNotIn("composition", saved.get("visual_review") or {})
        self.assertEqual(self.client.post("/api/cinematic/regenerate", json={"scenes": scenes, "scene_index": 3, "project_id": pid, "settings": SETTINGS},
                                          headers=auth("otto")).status_code, 404)
        self.assertEqual(self.client.post("/api/cinematic/regenerate", json={"scenes": scenes, "scene_index": 99, "settings": SETTINGS},
                                          headers=auth("rhea")).status_code, 404)

    def test_a_review_keeps_the_ai_suggestion_and_approves_what_was_shown(self):
        scenes = AiAssistTest().lesson()
        shown = C.compose_lesson(scenes, {**SETTINGS, "composer": "ai", "composer_provider": "fake"}, lambda _id: None,  # as the server sees
                                 ai={"env": {"AI_FAKE_PROVIDER": "1"}})                                         # them: not in rhea's library
        for scene, plan in zip(scenes, shown):
            scene["cinematic_plan"] = plan
        self.assertEqual(shown[3]["composition"]["source"], "ai")
        pid = self.client.post("/save-history", json={"subject_name": "A", "scenes": scenes}, headers=auth("rhea")).json()["id"]
        settings = {**SETTINGS, "composer": "ai", "composer_provider": "fake"}
        with mock.patch.object(K, "fake_compose_model", side_effect=AssertionError("a review never asks the model")):
            r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 3, "action": "keep", "settings": settings},
                                 headers=auth("rhea"))
        self.assertEqual(r.status_code, 200, r.text)
        plan, review = r.json()["plan"], r.json()["review"]
        self.assertEqual((plan["composition"]["source"], plan["plan_hash"]), ("ai", shown[3]["plan_hash"]))  # what was shown is approved
        self.assertEqual(plan["composition"]["ai"]["status"], "ok")
        self.assertEqual((review["status"], review["fingerprint"], plan["review_status"]), ("approved", plan["fingerprint"], "approved"))


if __name__ == "__main__":
    unittest.main()
