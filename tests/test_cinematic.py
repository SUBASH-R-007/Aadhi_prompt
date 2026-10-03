"""Backend tests of cinematic scene composition (Phase 13): the plan schema and its validation, normalized boxes and
safe areas, templates, the camera (moves only as far as important layers stay whole), transitions, timing, the
presenter / visual / background / text integration, formula and code preserved, fingerprints and stale reviews,
asset references, the optional AI background (cache identity, provider choice, recovery, fallback), the API and
backward compatibility.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_cinematic.py" -v

AI backgrounds are made by the local stand-in image provider in a service built for the test; the server's own
AI generation stays off. No real provider, no network.
"""
import asyncio
import copy
import datetime
import json
import os
import tempfile
import unittest
import uuid
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_providers  # noqa: E402
import ai_runs  # noqa: E402
import cinematic as C  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
from ai_media import AIMediaService, GenerationFailed  # noqa: E402
from ai_recovery import RecoveryManager  # noqa: E402


class Crash(BaseException):
    """The simulated power cut: nothing in the worker catches it."""


def teacher(enabled=True, position="right", placement="side", **extra):
    return {"presenter_id": "aadhi-teacher", "type": "illustrated", "enabled": enabled, "position": position,
            "placement": placement, **extra}


CINE = {"mode": "cinematic", "presenter_legacy": False, "presenter_id": "aadhi-teacher"}


def lesson():
    """Scenes A-F of the visual quality gate, plus a simulation and an AI video."""
    return [
        {"type": "content", "title": "Photosynthesis", "subtitle": "How plants make food", "html": "<p>Plants turn light into sugar.</p>",
         "narration": "Welcome! Today we learn how plants eat light.", "presenter_plan": teacher()},
        {"type": "content", "title": "Inside the leaf", "html": "<ul><li>Sunlight</li><li>Chlorophyll</li><li>Glucose</li></ul>",
         "side_panel": {"type": "image", "prompt": "leaf"}, "narration": "Look at this diagram. [SYNC] First sunlight. [SYNC] Then chlorophyll.",
         "presenter_plan": teacher(), "composition": {"labels": ["Sunlight → Chlorophyll → Glucose"]}},
        {"type": "content", "title": "The leaf up close", "html": "<p>Chloroplasts hold chlorophyll.</p>", "side_panel": {"type": "chart", "data": {}},
         "narration": "Zoom into the leaf.", "presenter_plan": teacher(placement="pip"), "composition": {"template": "diagram_focus"}},
        {"type": "content", "title": "Newton's second law", "html": "<div class='formula-block'>\\[F = ma\\]</div><p>Force = Mass × Acceleration</p>",
         "narration": "The law. [SYNC] F equals m a. [PAUSE:2] That is it.", "presenter_plan": teacher(placement="pip"),
         "composition": {"labels": [{"text": "F = Force"}, {"text": "m = Mass"}, {"text": "a = Acceleration", "at": {"sync": 1}}],
                         "emphasis": [{"target": "formula", "at": {"sync": 1}}]}},
        {"type": "content", "title": "Python for loop", "html": "<pre><code class='language-python'>for i in range(5):\n    print(i)</code></pre>",
         "narration": "Here is a loop.", "presenter_plan": teacher(enabled=False)},
        {"type": "quiz_checkpoint", "title": "Quick check", "question": "What is F?", "options": ["Force", "Speed"], "narration": "Your turn.",
         "presenter_plan": teacher()},
        {"type": "simulation", "title": "Watch", "manim_code": "class A(Scene): pass", "narration": "Watch.", "presenter_plan": teacher(enabled=False)},
        {"type": "ai_video", "title": "A forest", "prompt": "forest", "narration": "A forest.", "presenter_plan": teacher(enabled=False)},
    ]


def layer(plan, layer_id):
    return next((l for l in plan["layers"] if l["id"] == layer_id), None)


class ComposerTest(unittest.TestCase):
    def setUp(self):
        self.plans = C.compose_lesson(lesson(), CINE)

    def test_schema_and_every_plan_is_valid(self):
        for i, plan in enumerate(self.plans):
            self.assertEqual(C.validate_plan(plan), [], i)
            self.assertEqual(plan["warnings"], [], i)
            for key in ("version", "template", "layers", "camera", "transition", "duration", "timeline", "background",
                        "fingerprint", "plan_hash", "safe_areas", "presenter", "review_status"):
                self.assertIn(key, plan, key)
            for l in plan["layers"]:
                for key in ("id", "type", "box", "z", "visible", "opacity", "scale", "start", "end", "enter"):
                    self.assertIn(key, l, f"{i}:{l['id']}:{key}")
                self.assertTrue(C.valid_box(l["box"]), f"{i}:{l['id']} is on the normalized frame")
            zs = {l["type"]: l["z"] for l in plan["layers"]}
            self.assertLess(zs["background"], zs.get("visual", 21))
            self.assertLess(zs.get("board", 30), zs.get("presenter", 40))
            self.assertLess(zs.get("title", 60), zs["subtitles"])
        self.assertEqual([p["template"] for p in self.plans],
                         ["presenter_intro", "presenter_plus_visual", "diagram_focus", "formula_focus", "code_focus", "quiz",
                          "visual_focus", "visual_focus"])

    def test_safe_areas(self):
        for i, plan in enumerate(self.plans):
            important = [l for l in plan["layers"] if l["important"] and l["type"] != "subtitles"]
            for l in important:
                if l["type"] == "visual" and l["role"] == "full_canvas":
                    continue
                check = l.get("face") if l["type"] == "presenter" else l["box"]
                self.assertFalse(C.overlap(check, C.SUBTITLES), f"{i}:{l['id']} reaches the subtitles")
            content = [l["box"] for l in important if l["type"] in ("board", "visual", "label", "title")]
            for a in range(len(content)):
                for b in range(a + 1, len(content)):
                    self.assertFalse(C.overlap(content[a], content[b]), f"scene {i}: content overlaps")
            presenter = layer(plan, "presenter")
            if presenter:
                for other in content:
                    self.assertFalse(C.overlap(presenter["box"], other), f"scene {i}: the presenter covers content")

    def test_validation_catches_broken_plans(self):
        good = self.plans[1]
        self.assertEqual(C.validate_plan(good), [])
        cases = {
            "off the frame": lambda p: layer(p, "board")["box"].update(x=0.9, w=0.3),
            "overlap": lambda p: layer(p, "visual")["box"].update(x=layer(p, "board")["box"]["x"]),
            "subtitles": lambda p: layer(p, "board")["box"].update(y=0.7, h=0.25),
            "unknown type": lambda p: layer(p, "board").update(type="sticker"),
            "animation": lambda p: layer(p, "board").update(enter="spin"),
            "timing": lambda p: layer(p, "board").update(start=5, end=2),
            "opacity": lambda p: layer(p, "board").update(opacity=1.5),
            "template": lambda p: p.update(template="movie_trailer"),
            "camera": lambda p: p["camera"].update(movement="shake"),
            "zoom": lambda p: p["camera"].update(movement="slow_zoom_in", to={"x": 0.0, "y": 0.0, "w": 0.5}),
            "transition": lambda p: p["transition"].update(**{"in": "spiral"}),
            "duration": lambda p: p.update(duration=0),
        }
        for name, breaks in cases.items():
            plan = copy.deepcopy(good)
            breaks(plan)
            self.assertTrue(C.validate_plan(plan), name)
        self.assertEqual(C.validate_plan("nope"), ["not a plan"])

    def test_templates_in_every_configuration(self):
        base = {"type": "content", "title": "T", "html": "<p>Some text for the board.</p>", "narration": "One two three."}
        for template in C.TEMPLATES:
            for position in ("right", "left"):
                for enabled in (True, False):
                    for panel in (None, {"type": "image", "prompt": "x"}):
                        for labels in ([], ["A label"]):
                            scene = {**base, "presenter_plan": teacher(enabled=enabled, position=position),
                                     "composition": {"template": template, "labels": labels}}
                            if panel:
                                scene["side_panel"] = panel
                            plan = C.compose_scene(scene, 2, 5, CINE)
                            self.assertEqual(plan["template"], template)
                            self.assertEqual(C.validate_plan(plan), [], (template, position, enabled, panel, labels))
                            p = layer(plan, "presenter")
                            if p and position == "left" and p["placement"] != "pip":  # a small presenter sits under the board
                                self.assertLess(p["box"]["x"], 0.5)
                                board = layer(plan, "board") or layer(plan, "visual")
                                self.assertGreater(board["box"]["x"], p["box"]["x"] + p["box"]["w"] - 1e-6)  # mirrored

    def test_camera_keeps_important_layers_whole(self):
        for i, plan in enumerate(self.plans):
            cam = plan["camera"]
            self.assertIn(cam["movement"], C.CAMERA_MOVES)
            if cam["movement"] == "static":
                self.assertEqual((cam["from"], cam["to"]), (C.FULL, C.FULL))
                continue
            self.assertLessEqual(cam["scale"], C.MAX_SCALE + 1e-6, i)
            boxes = [(l.get("face") or l["box"]) for l in plan["layers"] if l["important"] and l["camera"]]
            title = layer(plan, "title")
            forbidden = [C.SUBTITLES] + ([title["box"]] if title and not title["camera"] else [])
            for step in range(6):  # the whole path, not just its ends
                k = step / 5
                f = {key: cam["from"][key] + (cam["to"][key] - cam["from"][key]) * k for key in "xyw"}
                self.assertTrue(C.framing_ok(f, boxes, forbidden), (i, step))
        self.assertEqual(self.plans[3]["camera"]["movement"], "focus")  # the formula
        self.assertEqual(self.plans[3]["camera"]["target"], "formula")
        self.assertEqual(self.plans[4]["camera"]["movement"], "static")  # code stays still
        self.assertEqual(self.plans[6]["camera"]["movement"], "static")  # a full-canvas visual

    def test_camera_moves_motion_off_and_mascot(self):
        scene = {"type": "content", "title": "T", "html": "<p>x</p>", "narration": "One two three.", "presenter_plan": teacher(enabled=False)}
        for move in C.CAMERA_MOVES:
            plan = C.compose_scene({**scene, "composition": {"camera": move}}, 1, 3, CINE)
            self.assertEqual(C.validate_plan(plan), [], move)
            self.assertIn(plan["camera"]["movement"], (move, "static"))
        pan = C.compose_scene({**scene, "composition": {"camera": "pan_right"}}, 1, 3, CINE)["camera"]
        self.assertEqual(pan["movement"], "pan_right")
        self.assertLess(pan["from"]["x"], pan["to"]["x"])  # the view travels right
        still = C.compose_scene({**scene, "composition": {"camera": "slow_zoom_in"}}, 1, 3, {**CINE, "motion": "none"})
        self.assertEqual(still["camera"]["movement"], "static")
        mascot = C.compose_scene({"type": "content", "title": "T", "html": "<p>x</p>", "aadhi_position": "right", "narration": "Hi."}, 1, 3,
                                 {"mode": "cinematic"})
        self.assertEqual((mascot["camera"]["movement"], mascot["background"]["type"]), ("static", "studio"))
        self.assertIn("studio clip", mascot["camera"]["note"])
        self.assertEqual(layer(mascot, "presenter")["box"], C.MASCOT_BOXES["right"])
        # no room: a board filling the whole content area cannot be zoomed into without cropping
        crowded = C.camera_plan("slow_zoom_in", "close", None, [{"x": 0.012, "y": 0.2, "w": 0.976, "h": 0.6}], [C.SUBTITLES], duration=10)
        self.assertEqual(crowded["movement"], "static")
        self.assertIn("without cropping", crowded["note"])

    def test_transitions_are_consistent(self):
        self.assertEqual({p["transition"]["in"] for p in self.plans}, {"fade"})
        slide = C.compose_lesson(lesson(), {**CINE, "transitions": "slide"})
        self.assertEqual({p["transition"]["in"] for p in slide}, {"slide"})
        calm = C.compose_lesson(lesson(), {**CINE, "transitions": "slide", "motion": "none"})
        self.assertEqual({p["transition"]["in"] for p in calm}, {"fade"})  # no sliding without motion
        scenes = lesson()
        scenes[2]["composition"]["transition"] = "cut"
        explicit = C.compose_lesson(scenes, CINE)
        self.assertEqual([p["transition"]["in"] for p in explicit].count("cut"), 1)  # only where the screenplay asks
        self.assertEqual(explicit[2]["transition"]["duration"], 0.0)

    def test_timing(self):
        self.assertEqual(C.estimate_seconds(""), 5.0)
        self.assertAlmostEqual(C.estimate_seconds("one two three four five six seven eight nine ten. [PAUSE:2]"), 10 / 2.6 + 2 + 0.8, places=1)
        self.assertEqual(C.estimate_seconds("hi"), 4.0)
        text = "Intro words here. [SYNC] First part. [PAUSE] [SYNC] Second."
        first, second = C.sync_seconds(text, 1), C.sync_seconds(text, 2)
        self.assertLess(first, second)
        self.assertIsNone(C.sync_seconds(text, 3))
        formula = self.plans[3]
        labels = layer(formula, "labels")
        self.assertEqual(labels["items"][2]["anchor"], {"sync": 1})  # followed by the page when the narration gets there
        self.assertEqual([e["at"] for e in formula["timeline"]], sorted(e["at"] for e in formula["timeline"]))
        self.assertIn("highlight", [e["event"] for e in formula["timeline"]])
        self.assertLessEqual(formula["camera"]["start"] + formula["camera"]["duration"], formula["duration"] + 3.6)

    def test_presenter_integration(self):
        intro = self.plans[0]
        self.assertEqual(layer(intro, "presenter")["placement"], "large")
        self.assertIsNone(layer(self.plans[4], "presenter"))  # the Director hid it
        self.assertIsNone(layer(self.plans[6], "presenter"))
        removed = C.compose_scene({**lesson()[1], "presenter_plan": teacher(enabled=False, review_status="removed")}, 1, 8, CINE)
        self.assertIsNone(layer(removed, "presenter"))
        left = C.compose_scene({**lesson()[1], "presenter_plan": teacher(position="left")}, 1, 8, CINE)
        self.assertEqual(layer(left, "presenter")["side"], "left")
        self.assertLess(layer(left, "presenter")["box"]["x"], 0.3)
        ai = C.compose_scene({**lesson()[1], "presenter_plan": {**teacher(), "presenter_id": "ai-teacher", "type": "ai_avatar"}}, 1, 8,
                             {**CINE, "presenter_id": "ai-teacher"})
        self.assertTrue(any("no presenter clip yet" in n for n in ai["notes"]))
        hidden = C.compose_scene({**lesson()[1], "composition": {"presenter_position": "hidden"}}, 1, 8, CINE)
        self.assertIsNone(layer(hidden, "presenter"))
        # A presenter plan for another presenter (stale) is not used: Aadhi's lesson placement is
        stale = C.compose_scene({**lesson()[1], "aadhi_position": "left"}, 1, 8, {**CINE, "presenter_id": "someone-else"})
        self.assertEqual((stale["presenter"]["type"], stale["presenter"]["side"]), ("mascot", "left"))

    def test_visual_integration(self):
        self.assertEqual(layer(self.plans[1], "visual")["source"], {"kind": "visual_plan", "slot": "side"})
        routed = {**lesson()[1], "visual_plan": {"side": {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": "a" * 32, "selection": "matched"}}}
        self.assertIsNotNone(layer(C.compose_scene(routed, 1, 8, CINE), "visual"))
        removed = {**lesson()[1], "visual_plan": {"side": {"source": "NONE", "media": "NONE", "selection": "removed"}}}
        plan = C.compose_scene(removed, 1, 8, CINE)
        self.assertIsNone(layer(plan, "visual"))  # removed in Visual Review: nothing takes its place
        self.assertEqual(plan["template"], "presenter_explanation")
        video = self.plans[7]
        self.assertEqual(layer(video, "board")["source"], {"kind": "visual_plan", "slot": "main"})
        self.assertAlmostEqual(layer(video, "board")["box"]["w"], layer(video, "board")["box"]["h"], places=3)  # 16:9 on screen
        self.assertEqual(layer(self.plans[6], "visual")["role"], "full_canvas")
        # Phase 14: a layout chosen explicitly keeps the router's visual when it can give it room (a quiz beside its visual)...
        kept = C.compose_scene({**lesson()[1], "composition": {"template": "quiz"}}, 1, 8, CINE)
        self.assertEqual((kept["template"], bool(layer(kept, "visual"))), ("quiz", True))
        # ...and says so when the chosen layout has no place for it (the explicit choice is respected, with a warning)
        dropped = C.compose_scene({**lesson()[1], "composition": {"template": "visual_focus"}}, 1, 8, CINE)
        self.assertEqual(dropped["template"], "visual_focus")
        self.assertIn("this template has no place for the scene's visual, so it is not shown", dropped["warnings"])

    def test_background(self):
        self.assertEqual({p["background"]["type"] for p in self.plans[:6]}, {"gradient"})
        self.assertEqual(self.plans[6]["background"]["type"], "canvas")
        solid = C.compose_scene(lesson()[1], 1, 8, {**CINE, "background": "solid"})
        self.assertEqual(solid["background"]["type"], "solid")
        found = {"b" * 32: {"kind": "image", "url": "/api/assets/b/content?token=t"}}
        image = C.compose_scene(lesson()[1], 1, 8, {**CINE, "background": "image", "background_asset_id": "b" * 32}, found.get)
        self.assertEqual((image["background"]["type"], image["background"]["asset_id"]), ("image", "b" * 32))
        missing = C.compose_scene(lesson()[1], 1, 8, {**CINE, "background": "image", "background_asset_id": "c" * 32}, found.get)
        self.assertEqual((missing["background"]["type"], missing["background"]["fallback"]), ("gradient", True))
        self.assertTrue(any("unavailable" in n for n in missing["notes"]))
        wrong_kind = C.compose_scene(lesson()[1], 1, 8, {**CINE, "background": "video", "background_asset_id": "b" * 32}, found.get)
        self.assertEqual(wrong_kind["background"]["type"], "gradient")
        ai = C.compose_scene(lesson()[1], 1, 8, {**CINE, "background": "ai"})
        self.assertTrue(any("no AI background" in n for n in ai["notes"]))
        remembered = C.compose_scene({**lesson()[1], "cinematic_plan": {"background": {"type": "image", "asset_id": "b" * 32, "generated": True}}},
                                     1, 8, {**CINE, "background": "ai"}, found.get)
        self.assertEqual(remembered["background"]["asset_id"], "b" * 32)  # the lesson's AI background made earlier
        mascot = C.compose_scene({"type": "content", "title": "T", "html": "<p>x</p>", "aadhi_position": "right"}, 1, 3,
                                 {"mode": "cinematic", "background": "gradient"})
        self.assertEqual(mascot["background"]["type"], "studio")
        self.assertTrue(any("filmed in his studio" in n for n in mascot["notes"]))

    def test_text_layers_point_to_the_lesson_and_never_change_it(self):
        scenes = lesson()
        before = copy.deepcopy(scenes)
        plans = C.compose_lesson(scenes, CINE)
        self.assertEqual(scenes, before)  # composing never edits the scene
        title = layer(plans[0], "title")
        self.assertEqual(title["source"], {"kind": "scene", "field": "title"})
        self.assertNotIn("Photosynthesis", json.dumps(plans[0]))  # the text is referenced, not copied
        labels = layer(plans[3], "labels")
        self.assertEqual([i["source"]["index"] for i in labels["items"]], [0, 1, 2])
        self.assertNotIn("F = Force", json.dumps(plans[3]))
        self.assertEqual(layer(plans[3], "board")["role"], "formula")  # MathJax renders it, from the scene's own TeX
        self.assertEqual(layer(plans[4], "board")["role"], "code")      # Prism renders it, from the scene's own code
        self.assertEqual(layer(plans[3], "board")["source"], {"kind": "scene", "field": "html"})
        cleaned, warnings = C.clean_composition({"template": "trailer", "camera": "shake", "labels": ["a"] * 8, "background": "neon",
                                                 "emphasis": [{"target": "everything"}]})
        self.assertEqual(len(cleaned["labels"]), 6)
        self.assertNotIn("template", cleaned)
        self.assertNotIn("emphasis", cleaned)
        self.assertEqual(len(warnings), 4)

    def test_fingerprint_and_stale_review(self):
        scene = lesson()[1]
        fp = C.compose_scene(scene, 1, 8, CINE)["fingerprint"]
        self.assertEqual(fp, C.compose_scene(copy.deepcopy(scene), 1, 8, CINE)["fingerprint"])
        moved = {**scene, "presenter_plan": teacher(position="left")}
        self.assertNotEqual(fp, C.compose_scene(moved, 1, 8, CINE)["fingerprint"])  # presenter position changed
        other_visual = {**scene, "visual_plan": {"side": {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": "d" * 32}}}
        self.assertNotEqual(fp, C.compose_scene(other_visual, 1, 8, CINE)["fingerprint"])  # visual changed
        self.assertNotEqual(fp, C.compose_scene(scene, 1, 8, {**CINE, "background": "solid"})["fingerprint"])
        approved = {**scene, "visual_review": {"composition": {"status": "approved", "fingerprint": fp}}}
        self.assertEqual(C.compose_scene(approved, 1, 8, CINE)["review_status"], "approved")
        stale = C.compose_scene({**moved, "visual_review": {"composition": {"status": "approved", "fingerprint": fp}}}, 1, 8, CINE)
        self.assertEqual((stale["review_status"], stale["review_stale"]), ("pending", True))  # never approve an old composition
        changed = C.compose_scene({**moved, "visual_review": {"composition": {"status": "changed", "fingerprint": fp,
                                                                              "overrides": {"template": "diagram_focus"}}}}, 1, 8, CINE)
        self.assertEqual((changed["review_status"], changed["review_stale"], changed["template"]), ("changed", True, "diagram_focus"))
        self.assertNotEqual(C.plan_hash(self.plans[1]), C.plan_hash(self.plans[2]))

    def test_review_overrides_come_first(self):
        scene = {**lesson()[1], "visual_review": {"composition": {"status": "changed", "overrides": {
            "template": "diagram_focus", "camera": "static", "transition": "crossfade", "visual_position": "right"}}}}
        plan = C.compose_scene(scene, 1, 8, CINE)
        self.assertEqual((plan["template"], plan["camera"]["movement"], plan["transition"]["in"], plan["reason"]),
                         ("diagram_focus", "static", "crossfade", "chosen in Visual Review"))
        self.assertEqual(C.check_overrides({"template": "quiz"}), {"template": "quiz"})
        for bad in ({"template": "trailer"}, {"colour": "red"}, {"camera": "shake"}):
            with self.assertRaises(Exception):
                C.check_overrides(bad)

    def test_classic_mode_and_old_scenes(self):
        self.assertIsNone(C.compose_scene(lesson()[0], 0, 1, {"mode": "classic"}))
        self.assertIsNone(C.compose_scene(lesson()[0], 0, 1, {}))
        old = {"type": "content", "title": "Old", "html": "<p>x</p>", "narration": "y"}  # no plans at all (a lesson from before)
        plan = C.compose_scene(old, 1, 3, {"mode": "cinematic"})
        self.assertEqual(C.validate_plan(plan), [])
        self.assertEqual(plan["presenter"]["type"], "mascot")

    def test_inspector_facts(self):
        facts = dict(C.inspector_facts(self.plans[3]))
        self.assertEqual(facts["Template"], "Formula focus")
        self.assertIn("focus", facts["Camera"])
        self.assertEqual(facts["Transition"], "fade")
        self.assertEqual(C.inspector_facts(None), [])


class BackgroundGenerationTest(unittest.TestCase):
    """The optional AI background through the AI media layer with the stand-in image provider."""

    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user("cora")
        cls.state = tempfile.mkdtemp(prefix="aadhi-background-jobs-", dir=TMP)
        cls.client = TestClient(server.app)

    def setUp(self):
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).update(
                {"status": "cancelled", "lease_owner": None, "lease_expires_at": None}, synchronize_session=False)
            db.commit()

    def service(self, **extra):
        env = {"AI_FAKE_STATE_DIR": self.state, "AI_JOB_LEASE_SECONDS": "30", "AI_JOB_HEARTBEAT_SECONDS": "5",
               "AI_JOB_CONCURRENCY": "4", "FAKE_POLL_SECONDS": "0.05", "AI_PROVIDER_RETRY_DELAY": "0.01", **extra}
        registry = ai_providers.ProviderRegistry(env=env, video_mode=lambda: "fake", fake=True)
        media = AIMediaService(server.asset_library, server.ai_cache, registry, static_dir=server.STATIC_DIR,
                               generation_enabled=lambda: True, env=env, log=False)
        media.is_wanted = server._still_wanted
        media.on_completed = server._lesson_after_generation
        return media, RecoveryManager(media, env=env)

    def generate(self, media, request):
        async def go():
            with database.SessionLocal() as db:
                outcome = await media.generate(db, db.get(models.User, self.uid), request)
                return {"asset": outcome.asset.id, "hit": outcome.cache_hit, "provider": outcome.provider}
        return asyncio.run(go())

    def test_prompt_and_cache_identity(self):
        media, _ = self.service()
        prompt = C.background_prompt("academic", "chalkboard")
        self.assertIn("deep indigo", prompt)
        self.assertIn("no text, no people", prompt)
        self.assertNotEqual(C.background_prompt("academic"), C.background_prompt("modern"))  # the style is part of the request
        wish = f"soft light {uuid.uuid4().hex[:6]}"
        first = self.generate(media, C.background_request("academic", wish))
        self.assertEqual((first["hit"], first["provider"]), (False, "fake"))
        again = self.generate(media, C.background_request("academic", wish))
        self.assertEqual((again["asset"], again["hit"]), (first["asset"], True))  # identical: reused, nothing new
        restyled = self.generate(media, C.background_request("modern", wish))
        self.assertNotEqual(restyled["asset"], first["asset"])  # another style: another background
        forced = self.generate(media, C.background_request("academic", wish, force=True))
        self.assertNotEqual(forced["asset"], first["asset"])
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, first["asset"])
            self.assertEqual((asset.kind, asset.status), ("image", "ready"))
            self.assertGreater(asset.width, asset.height)  # 16:9
            self.assertEqual(json.loads(asset.details)["generation"]["provider"], "fake")
            self.assertEqual(db.get(models.Asset, first["asset"]).status, "ready")  # the earlier version stays

    def test_explicit_provider_is_not_replaced(self):
        failing, _ = self.service(AI_FAKE_FAIL="fake:image:unavailable")
        with self.assertRaises(GenerationFailed):
            self.generate(failing, C.background_request("academic", f"x {uuid.uuid4().hex[:6]}", provider="fake", allow_fallback=False))
        fallback = self.generate(failing, C.background_request("academic", f"y {uuid.uuid4().hex[:6]}", provider="fake", allow_fallback=True))
        self.assertEqual(fallback["provider"], "fake-alt")  # only because fallback was allowed

    def test_recovery_finishes_without_a_second_call_and_lands_in_the_lesson(self):
        scenes = lesson()[:2]
        plans = C.compose_lesson(scenes, {**CINE, "background": "ai"})
        for scene, plan in zip(scenes, plans):
            scene["cinematic_plan"] = plan
        pid = self.client.post("/save-history", json={"subject_name": "Bg", "scenes": scenes}, headers=auth("cora")).json()["id"]
        old, _ = self.service(FAKE_JOB_SECONDS="1.5")
        request = C.background_request("academic", f"recover {uuid.uuid4().hex[:6]}", project_id=pid)

        async def start_and_crash():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                # the stand-in's pictures come back in one call (like most image APIs): cut off after the download
                with mock.patch.object(old, "_crash", side_effect=lambda p: (_ for _ in ()).throw(Crash(p)) if p == "after_download" else None):
                    _o, run = old.start_background(db, user, request)
                    try:
                        await old.tasks[run.id]
                    except Crash:
                        pass
                return run.id
        run_id = asyncio.run(start_and_crash())
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)})
            db.commit()
            self.assertEqual(db.get(models.AIGenerationRun, run_id).status, "running")  # interrupted
        new, manager = self.service(FAKE_JOB_SECONDS="1.5")

        async def recover():
            for task in await manager.sweep():
                await task
        asyncio.run(recover())
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            self.assertEqual((run.status, run.recovery_count, run.slot), ("completed", 1, "background"))
            self.assertEqual(json.loads(run.request)["purpose"], "cinematic-background")  # the request survived the restart
        # The picture was already downloaded when the server died: recovery finishes from that file, no second call
        self.assertEqual(new.provider_calls["image"], 0)
        saved = self.client.get(f"/api/projects/{pid}", headers=auth("cora")).json()
        background = saved["scenes"][0]["cinematic_plan"]["background"]
        self.assertEqual((background["type"], background["asset_id"]), ("image", run.asset_id))
        self.assertEqual(saved["cinematic"]["background_asset_id"], run.asset_id)
        # in use by the lesson: the library refuses to delete it
        self.client.post("/save-history", json={"id": pid, "subject_name": "Bg", "scenes": saved["scenes"]}, headers=auth("cora"))
        with database.SessionLocal() as db:
            server.asset_library.record_project_references(db, db.get(models.Project, pid), self.uid)
        self.assertEqual(self.client.delete(f"/api/assets/{run.asset_id}", headers=auth("cora")).status_code, 409)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("cora")
        ensure_user("otto")
        cls.client = TestClient(server.app)

    def test_vocabulary(self):
        body = self.client.get("/api/cinematic", headers=auth("cora")).json()
        self.assertEqual(set(body["templates"]), set(C.TEMPLATES))
        self.assertEqual(body["transitions"], list(C.TRANSITIONS))
        self.assertIn("subtitles", body["safe_areas"])
        self.assertEqual(self.client.get("/api/cinematic").status_code, 401)

    def test_plan_endpoint(self):
        r = self.client.post("/api/cinematic/plan", json={"scenes": lesson(), "settings": CINE}, headers=auth("cora"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["plans"]), 8)
        self.assertEqual(r.json()["style"]["transitions"], "fade")
        classic = self.client.post("/api/cinematic/plan", json={"scenes": lesson(), "settings": {"mode": "classic"}}, headers=auth("cora")).json()
        self.assertEqual(classic["plans"], [None] * 8)  # the existing renderer, untouched
        for bad in ({"mode": "movie"}, {"motion": "wild"}, {"transitions": "spiral"}, {"background": "neon"},
                    {"background_asset_id": "../etc"}, {"presenter_position": "top"}):
            self.assertEqual(self.client.post("/api/cinematic/plan", json={"scenes": [], "settings": {**CINE, **bad}},
                                              headers=auth("cora")).status_code, 422, bad)
        self.assertEqual(self.client.post("/api/cinematic/plan", json={"scenes": [{}] * 201, "settings": CINE}, headers=auth("cora")).status_code, 413)
        pid = self.client.post("/save-history", json={"subject_name": "P", "scenes": lesson()}, headers=auth("cora")).json()["id"]
        self.assertEqual(self.client.post("/api/cinematic/plan", json={"scenes": [], "settings": CINE, "project_id": pid},
                                          headers=auth("otto")).status_code, 404)

    def test_background_asset_needs_access(self):
        with open(os.path.join(TMP, "bg.png"), "wb") as f:
            f.write(b"")
        import subprocess
        path = os.path.join(TMP, f"bg_{uuid.uuid4().hex[:6]}.png")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x224466:size=320x180", "-frames:v", "1", path], check=True)
        with open(path, "rb") as f:
            asset = self.client.post("/api/assets", files={"file": ("bg.png", f, "image/png")}, headers=auth("cora")).json()["asset"]
        mine = self.client.post("/api/cinematic/plan", json={"scenes": lesson()[:2], "settings": {**CINE, "background": "image",
                                "background_asset_id": asset["id"]}}, headers=auth("cora")).json()["plans"][0]["background"]
        self.assertEqual((mine["type"], mine["asset_id"]), ("image", asset["id"]))
        self.assertIn("/content?token=", mine["url"])  # a signed link, never a storage path
        theirs = self.client.post("/api/cinematic/plan", json={"scenes": lesson()[:2], "settings": {**CINE, "background": "image",
                                  "background_asset_id": asset["id"]}}, headers=auth("otto")).json()["plans"][0]["background"]
        self.assertEqual(theirs["type"], "gradient")  # another user's picture is never shown

    def test_review_endpoint(self):
        pid = self.client.post("/save-history", json={"subject_name": "R", "scenes": lesson()}, headers=auth("cora")).json()["id"]
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "change",
                                                              "overrides": {"template": "diagram_focus"}, "settings": CINE}, headers=auth("cora"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["status"], r.json()["plan"]["template"]), ("changed", "diagram_focus"))
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "change",
                                                              "overrides": {"camera": "static"}, "settings": CINE}, headers=auth("cora")).json()
        self.assertEqual(r["review"]["overrides"], {"template": "diagram_focus", "camera": "static"})  # changes add up
        stored = self.client.get(f"/api/projects/{pid}", headers=auth("cora")).json()["scenes"][1]
        self.assertEqual(stored["cinematic_plan"]["template"], "diagram_focus")
        self.assertEqual(stored["visual_review"]["composition"]["fingerprint"], stored["cinematic_plan"]["fingerprint"])
        kept = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "keep", "settings": CINE},
                                headers=auth("cora")).json()
        self.assertEqual((kept["review"]["status"], kept["plan"]["template"]), ("changed", "diagram_focus"))  # keeping keeps the change
        plain = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 0, "action": "keep", "settings": CINE},
                                 headers=auth("cora")).json()
        self.assertEqual(plain["review"]["status"], "approved")
        reset = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "reset", "settings": CINE},
                                 headers=auth("cora")).json()
        self.assertEqual((reset["review"], reset["plan"]["template"]), (None, "presenter_plus_visual"))
        for body, code in (({"action": "dance"}, 422), ({"action": "change", "overrides": {"template": "trailer"}}, 422),
                           ({"action": "change", "overrides": {}}, 422), ({"action": "keep", "scene_index": 99}, 404)):
            payload = {"project_id": pid, "scene_index": 1, "settings": CINE, **body}
            self.assertEqual(self.client.post("/api/cinematic/review", json=payload, headers=auth("cora")).status_code, code, body)
        self.assertEqual(self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "keep", "settings": CINE},
                                          headers=auth("otto")).status_code, 404)  # another user's lesson

    def test_background_endpoint_refuses_when_ai_is_off(self):
        r = self.client.post("/api/cinematic/background", json={"prompt": "calm"}, headers=auth("cora"))
        self.assertEqual(r.status_code, 403)  # the server's AI generation is off in the tests: nothing is generated
        self.assertEqual(self.client.post("/api/cinematic/background", json={"typography": "comic"}, headers=auth("cora")).status_code, 422)
        self.assertEqual(self.client.post("/api/cinematic/background", json={"provider": "Bad Name!"}, headers=auth("cora")).status_code, 422)
        self.assertEqual(self.client.post("/api/cinematic/background", json={}).status_code, 401)

    def test_lessons_without_cinematic_plans_are_unchanged(self):
        plain = {"subject_name": "Plain", "scenes": [{"type": "content", "title": "x", "narration": "y"}]}
        pid = self.client.post("/save-history", json=plain, headers=auth("cora")).json()["id"]
        stored = self.client.get(f"/api/projects/{pid}", headers=auth("cora")).json()
        self.assertNotIn("cinematic_plan", stored["scenes"][0])
        self.assertEqual(self.client.post("/api/visuals/plan", json={"scenes": stored["scenes"]}, headers=auth("cora")).status_code, 200)
        self.assertEqual(self.client.get("/cinematic.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
