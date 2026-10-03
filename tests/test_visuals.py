"""Backend tests for the visual router (visuals.py) and its use of the asset library.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
AI generation is switched off for all backend tests (backend_env); tests that need AI planning
turn it on for the planner only while every generator is patched to fail if it is ever called.
"""
import copy
import hashlib
import json
import os
import time
import unittest
import uuid
from contextlib import contextmanager
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import visuals  # noqa: E402

STATIC = os.path.join(os.path.dirname(TMP), "unused")  # placeholder; real static folder comes from the library


@contextmanager
def ai_planning_enabled():
    """AI may be *planned*; any real generation attempt fails the test."""
    boom = AssertionError("an AI provider or renderer was called during planning")
    with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1"}), \
            mock.patch.object(server.ai_media, "call_provider", side_effect=boom), \
            mock.patch.object(server.manim_service, "render", side_effect=boom), \
            mock.patch.object(server.manim_sandbox, "execute", side_effect=boom), \
            mock.patch("urllib.request.urlopen", side_effect=boom):
        yield


# ---- the Visual Director's routing preference (Phase 15): scene.visual_direction.route ---------------------------

ROUTE = {"prefer_existing": True, "preferred_media": "video", "match_terms": ["Photosynthesis", "chlorophyll"]}
HTML_ASSET = "a" * 32


def direction(route, version=1, **extra):
    """A scene's visual direction with only the parts the router reads (the rest is the Director's business)."""
    return {"version": version, "route": route, **extra}


def legacy_fingerprint(scene, slot):
    """slot_fingerprint as it was before the Visual Director existed (Phase 6), so stored reviews are compared with it."""
    if slot == "main":
        wanted = {k: scene.get(k) for k in ("type", "prompt", "manim_code", "simulation_code", "code", "visual_svg", "svg",
                                            "p5_code", "visual")}
    else:
        panel = scene.get("side_panel")
        wanted = {"type": scene.get("type"), "title": scene.get("title"), "visual": scene.get("visual"),
                  "side_panel": {k: v for k, v in panel.items() if k not in ("video_url", "video_asset_id")}
                  if isinstance(panel, dict) else None}
    return hashlib.sha256(json.dumps(wanted, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def lesson_scenes():
    """Every kind of slot: fixed media (main visuals, panels, typed intents, a built-in renderer) and one open side visual."""
    return [
        {"type": "ai_video", "title": "Leaf", "prompt": "a leaf turning sunlight into sugar",
         "visual": {"concept": "photosynthesis in a leaf", "keywords": ["leaf", "sunlight"]}},
        {"type": "content", "title": "Chloroplast", "visual": {"concept": "a chloroplast", "description": "inside a chloroplast"},
         "html": f'<p>Look: <img src="asset:{HTML_ASSET}"></p>'},
        {"type": "content", "title": "Glucose", "side_panel": {"type": "image", "prompt": "glucose molecules"}},
        {"type": "content", "title": "Rates", "side_panel": {"type": "chart", "data": {}}},
        {"type": "content", "title": "Stomata", "visual": {"type": "video", "concept": "stomata opening"}},
        {"type": "content", "title": "Sunflowers", "visual": {"type": "image", "concept": "a sunflower field"}},
        {"type": "simulation", "title": "Model", "manim_code": "class M(Scene): pass"},
    ]


FIXED_SLOTS = ((0, "main"), (2, "side"), (3, "side"), (4, "side"), (5, "side"), (6, "main"))
OPEN_SLOT = (1, "side")


class VisualDirectionPreferenceTest(unittest.TestCase):
    """The preference as data: validated, placed on the right slots, kept out of prompts and old fingerprints."""

    def directed(self, route, scenes=None):
        scenes = scenes if scenes is not None else lesson_scenes()
        for scene in scenes:
            scene["visual_direction"] = direction(route)
        return {(r.scene_index, r.slot): r for r in visuals.requests_from_scenes(scenes)}

    def test_the_route_preference_is_validated(self):
        pref = visuals.route_preference
        self.assertEqual(pref({"visual_direction": direction(ROUTE)}), ROUTE)
        self.assertIsNone(pref({}))
        for bad in (None, "route", ["x"], {"route": ROUTE}, direction(ROUTE, version=2), direction(ROUTE, version="1"),
                    direction(ROUTE, version=True), direction(None), direction("prefer existing"), direction({}),
                    direction({"prefer_existing": "yes", "preferred_media": "hologram", "match_terms": "Photosynthesis"}),
                    direction({"prefer_existing": 1, "preferred_media": ["image"], "match_terms": [7, None, "", "   ", "x" * 41]}),
                    direction({"provider": "veo", "prompt": "draw a leaf", "slot": "extra", "model": "x"})):
            self.assertIsNone(pref({"visual_direction": bad}), bad)
        # Only the parts within the contract are kept: no other field, at most 6 terms of at most 40 characters
        mixed = direction({"prefer_existing": False, "preferred_media": "Video", "provider": "veo", "prompt": "a leaf",
                           "match_terms": [" Photosynthesis ", "Photosynthesis", 3, "x" * 41, "Calvin cycle", "", "Stroma"]})
        self.assertEqual(pref({"visual_direction": mixed}), {"match_terms": ["Photosynthesis", "Calvin cycle"]})
        self.assertEqual(pref({"visual_direction": direction({"preferred_media": "image", "match_terms": ["x" * 40]})}),
                         {"preferred_media": "image", "match_terms": ["x" * 40]})

    def test_the_preference_reaches_main_and_side_slots_but_never_creates_one_or_fixes_their_media(self):
        plain = visuals.requests_from_scenes(lesson_scenes())
        options, off = visuals.PlanOptions(), visuals.PlanOptions(prefer_existing_assets=False)
        for media, wanted in (("video", visuals.MediaType.VIDEO), ("image", visuals.MediaType.STATIC_IMAGE)):
            route = dict(ROUTE, preferred_media=media)
            by_slot = self.directed(route)
            self.assertEqual(list(by_slot), [(r.scene_index, r.slot) for r in plain])  # no slot is created
            # The one side visual whose media the screenplay leaves open takes the preferred media for matching
            self.assertEqual(by_slot[OPEN_SLOT].preference, route)
            self.assertEqual(visuals.wanted_media(by_slot[OPEN_SLOT], options), wanted)
            # Every slot whose media the screenplay fixes keeps it (main visuals, panels, typed intents, renderers)
            without_media = {k: v for k, v in route.items() if k != "preferred_media"}
            for key in FIXED_SLOTS:
                self.assertEqual(by_slot[key].preference, without_media, key)
                self.assertEqual(visuals.wanted_media(by_slot[key], options), by_slot[key].media, key)
            self.assertIsNone(by_slot[(1, f"html:{HTML_ASSET}")].preference)  # an explicit asset in the text: untouched
            # What feeds prompts, matching words and the AI cache is exactly what it was
            for request in plain:
                other = by_slot[(request.scene_index, request.slot)]
                self.assertEqual((request.media, request.concept, request.description, request.keywords, request.generation_prompt,
                                  request.renderer, request.explicit_asset_id, request.existing_url, request.manim_source),
                                 (other.media, other.concept, other.description, other.keywords, other.generation_prompt,
                                  other.renderer, other.explicit_asset_id, other.existing_url, other.manim_source))
                self.assertNotIn("chlorophyll", json.dumps([other.concept, other.description, other.keywords, other.generation_prompt]))
        # The lesson's own preferred_media_type still decides where it is given
        video = self.directed(ROUTE)[OPEN_SLOT]
        self.assertEqual(visuals.wanted_media(video, visuals.PlanOptions(preferred_media_type="image")), visuals.MediaType.STATIC_IMAGE)
        # prefer_existing: the slot's own reuse-first switch; a route without it never turns reuse off
        self.assertTrue(visuals.prefers_existing(video, off))
        self.assertFalse(visuals.prefers_existing(plain[0], off))
        self.assertTrue(visuals.prefers_existing(self.directed({"match_terms": ["leaf"]})[(0, "main")], options))
        self.assertFalse(visuals.prefers_existing(self.directed({"match_terms": ["leaf"]})[(0, "main")], off))

    def test_without_a_direction_requests_and_fingerprints_are_exactly_as_before(self):
        scenes = lesson_scenes()
        plain = visuals.requests_from_scenes(scenes)
        for request in plain:
            self.assertIsNone(request.preference)
            if request.slot in ("main", "side"):
                self.assertEqual(request.fingerprint, legacy_fingerprint(scenes[request.scene_index], request.slot), request.slot)
        # A direction without a usable route (none, another version, malformed) changes nothing at all
        for unusable in ({"version": 1, "strategy": "show_process"}, direction(None), direction(ROUTE, version=2), "garbage",
                         direction({"provider": "veo", "preferred_media": "hologram"})):
            variant = lesson_scenes()
            for scene in variant:
                scene["visual_direction"] = copy.deepcopy(unusable)
            self.assertEqual(visuals.requests_from_scenes(variant), plain, unusable)
        # A route never changes a slot's fingerprint (Phase 15 review): it only orders library matches, and counting it
        # would re-open every approval when a lesson switches between the Classic and cinematic styles
        first = self.directed(ROUTE)
        again = self.directed(json.loads(json.dumps(ROUTE, sort_keys=True)))
        for request in plain:
            key = (request.scene_index, request.slot)
            self.assertEqual(first[key].fingerprint, again[key].fingerprint)
            self.assertEqual(first[key].fingerprint, request.fingerprint, key)
            if request.slot in ("main", "side"):
                self.assertIsNotNone(first[key].preference, key)  # the preference is still carried to the rules


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class VisualRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.ids = {name: ensure_user(name) for name in ("vera", "otto")}
        cls.media_dir = os.path.join(TMP, "visual-media")
        os.makedirs(cls.media_dir, exist_ok=True)
        cls.static_files = []

    @classmethod
    def tearDownClass(cls):
        for path in cls.static_files:
            if os.path.exists(path):
                os.remove(path)

    # ---- helpers ----

    def fresh(self, kind):
        tag = uuid.uuid4().hex
        path = os.path.join(self.media_dir, f"{tag}.{'png' if kind == 'image' else 'mp4'}")
        if kind == "image":
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{tag[:6]}:size=64x48", "-frames:v", "1", path)
        else:
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{tag[:6]}:size=160x120:rate=10:duration=1", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", path)
        return path

    def asset(self, user, kind="video", name=None, description=None, keywords=None):
        path = self.fresh(kind)
        with open(path, "rb") as f:
            res = self.client.post("/api/assets", files={"file": (name or os.path.basename(path), f.read(), "application/octet-stream")},
                                   headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        asset = res.json()["asset"]
        if description is not None or keywords is not None:
            body = {k: v for k, v in (("description", description), ("keywords", keywords)) if v is not None}
            upd = self.client.patch(f"/api/assets/{asset['id']}", json=body, headers=auth(user))
            self.assertEqual(upd.status_code, 200, upd.text)
            asset = upd.json()
        return asset

    def plan(self, user, scenes, **options):
        res = self.client.post("/api/visuals/plan", json={"scenes": scenes, **options}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def slot(self, body, scene_index, slot):
        found = [p for p in body["plans"] if p["scene_index"] == scene_index and p["slot"] == slot]
        self.assertEqual(len(found), 1, body["plans"])
        return found[0]

    def row(self, asset_id):
        with database.SessionLocal() as db:
            return db.get(models.Asset, asset_id)

    def static_file(self, src, ext="mp4"):
        name = f"test_visual_{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(server.asset_library.volumes["static"].root, name)
        with open(src, "rb") as f, open(path, "wb") as out:
            out.write(f.read())
        self.static_files.append(path)
        return name

    # ---- explicit assets -----------------------------------------------------------------

    def test_explicit_asset_wins_over_a_better_match(self):
        chosen = self.asset("vera", description="something unrelated")
        self.asset("vera", description="bridge carrying heavy traffic across a river")
        scenes = [{"type": "ai_video", "title": "Bridge", "prompt": "a bridge carrying heavy traffic", "video_asset_id": chosen["id"]}]
        plan = self.slot(self.plan("vera", scenes), 0, "main")
        self.assertEqual((plan["source"], plan["selection"], plan["asset_id"]), ("ASSET", "explicit", chosen["id"]))
        self.assertTrue(plan["url"].startswith(f"/api/assets/{chosen['id']}/content?token="))
        self.assertFalse(plan["requires_generation"])

    def test_every_kind_of_explicit_reference_is_honoured(self):
        video = self.asset("vera")
        image = self.asset("vera", kind="image")
        side = self.asset("vera")
        scenes = [
            {"type": "simulation", "manim_code": "class A(Scene): pass", "manim_asset_id": video["id"]},  # explicit beats Manim
            {"type": "content", "html": f'<img src="asset:{image["id"]}">', "uploaded_image_assets": {"img1": image["id"]},
             "side_panel": {"type": "manim", "manim_code": "class B(Scene): pass", "video_asset_id": side["id"]}},
        ]
        body = self.plan("vera", scenes)
        self.assertEqual(self.slot(body, 0, "main")["asset_id"], video["id"])
        self.assertEqual(self.slot(body, 1, f"html:{image['id']}")["asset_id"], image["id"])
        self.assertEqual(self.slot(body, 1, "uploaded:img1")["asset_id"], image["id"])
        self.assertEqual(self.slot(body, 1, "side")["asset_id"], side["id"])
        self.assertTrue(all(p["selection"] == "explicit" and p["source"] == "ASSET" for p in body["plans"]))

    def test_shared_aadhi_assets_can_be_named_and_matched(self):
        with database.SessionLocal() as db:
            poster = db.query(models.Asset).filter(models.Asset.owner_id.is_(None), models.Asset.kind == "image",
                                                   models.Asset.source == "mascot").first()
        named = self.slot(self.plan("otto", [{"type": "content", "uploaded_image_assets": {"a": poster.id}}]), 0, "uploaded:a")
        self.assertEqual((named["source"], named["asset_id"]), ("SYSTEM_ASSET", poster.id))
        matched = self.slot(self.plan("otto", [{"type": "content", "title": "Meet the mascot",
                                                "visual": {"concept": "Aadhi the mascot", "type": "image"}}]), 0, "side")
        self.assertEqual((matched["source"], matched["selection"]), ("SYSTEM_ASSET", "matched"))
        # A picture of Aadhi, not the still of the empty background Aadhi is hidden from (no_aadhi.jpg)
        details = json.loads(self.row(matched["asset_id"]).details)
        self.assertEqual(details["role"], "poster")
        self.assertNotEqual(details["placement"], "hidden")

    def test_negated_words_do_not_match(self):
        self.assertEqual(visuals.terms("no_aadhi"), [])
        self.assertEqual(visuals.terms("a lab without the people"), ["lab"])
        self.assertEqual(visuals.terms("Not a diagram, a photo of Bridges"), ["bridg"])

    def test_an_unusable_explicit_asset_is_reported_not_replaced(self):
        match_text = "wind turbine blades turning in a storm"
        deleted = self.asset("vera", description=match_text)
        failed = self.asset("vera", description=match_text)
        missing = self.asset("vera", description=match_text)
        self.asset("vera", description=match_text)  # a perfectly matching alternative that must NOT be used
        self.client.delete(f"/api/assets/{deleted['id']}", headers=auth("vera"))
        with database.SessionLocal() as db:
            db.get(models.Asset, failed["id"]).status = "failed"
            db.commit()
            missing_path = server.asset_library.file_path(db.get(models.Asset, missing["id"]))
        os.remove(missing_path)
        scenes = [{"type": "ai_video", "title": "Turbine", "prompt": match_text, "video_asset_id": a["id"]} for a in (deleted, failed, missing)]
        body = self.plan("vera", scenes)
        for i, why in enumerate(("not available", "status: failed", "missing from storage")):
            plan = self.slot(body, i, "main")
            self.assertEqual((plan["source"], plan["error"], plan["asset_id"]), ("NONE", "asset_unavailable", scenes[i]["video_asset_id"]))
            self.assertIn(why, plan["reason"])
        self.assertEqual(self.row(missing["id"]).status, "failed")

    def test_someone_elses_private_asset_cannot_be_named_or_matched(self):
        secret = self.asset("vera", description="lighthouse beam sweeping over a stormy sea")
        named = self.slot(self.plan("otto", [{"type": "ai_video", "prompt": "x", "video_asset_id": secret["id"]}]), 0, "main")
        self.assertEqual((named["source"], named["error"]), ("NONE", "asset_unavailable"))
        self.assertNotIn("url", named)
        self.assertIn("not available", named["reason"])  # same wording as deleted: nothing leaked
        auto = self.slot(self.plan("otto", [{"type": "ai_video", "title": "Lighthouse beam sweeping over a stormy sea",
                                             "prompt": "lighthouse beam sweeping over a stormy sea"}]), 0, "main")
        self.assertNotEqual(auto.get("asset_id"), secret["id"])
        self.assertNotIn(auto["source"], ("ASSET",))

    # ---- library matching ------------------------------------------------------------------

    def test_library_match_on_the_concept(self):
        clip = self.asset("vera", description="A ferry crossing a frozen fjord at first light")
        scene = {"type": "ai_video", "title": "Ferry crossing a frozen fjord", "prompt": "cinematic shot of a ferry crossing a frozen fjord"}
        plan = self.slot(self.plan("vera", [scene], debug=True), 0, "main")
        self.assertEqual((plan["source"], plan["selection"], plan["asset_id"]), ("ASSET", "matched", clip["id"]))
        self.assertGreaterEqual(plan["score"], visuals.MATCH_THRESHOLD)
        self.assertTrue(any("library match" in line for line in plan["debug"]))

    def test_library_match_on_keywords_not_filenames(self):
        clip = self.asset("vera", name="IMG_0042.mp4", keywords=["steel cable", "tension", "suspension bridge"])
        decoy = self.asset("vera", name="steel_cable_tension.mp4")  # only its file name matches
        scene = {"type": "ai_video", "title": "Cable tension", "prompt": "steel cable under tension",
                 "visual": {"concept": "steel cable tension", "keywords": ["cable", "tension"]}}
        plan = self.slot(self.plan("vera", [scene]), 0, "main")
        self.assertEqual(plan["asset_id"], clip["id"])
        self.assertNotEqual(plan["asset_id"], decoy["id"])

    def test_a_weak_match_is_not_used(self):
        self.asset("vera", description="bridge")  # shares one word with the request
        scene = {"type": "ai_video", "title": "Bridge collapse in an earthquake", "prompt": "a bridge collapsing during an earthquake"}
        with ai_planning_enabled():
            plan = self.slot(self.plan("vera", [scene], debug=True), 0, "main")
        self.assertEqual(plan["source"], "AI_VIDEO")
        self.assertTrue(any("below the threshold" in line or "no video asset matches" in line for line in plan["debug"]))

    def test_images_only_match_images_and_videos_only_videos(self):
        self.asset("vera", kind="image", description="photograph of a red volcano erupting at night")
        scene = {"type": "ai_video", "title": "Red volcano erupting at night", "prompt": "a red volcano erupting at night"}
        plan = self.slot(self.plan("vera", [scene], allow_ai_generation=False), 0, "main")
        self.assertNotIn(plan["source"], ("ASSET", "SYSTEM_ASSET"))

    # ---- built-in visuals, Manim, no visual --------------------------------------------------

    def test_built_in_renderers_are_chosen_from_the_scenes_own_data(self):
        scenes = [
            {"type": "content", "side_panel": {"type": "chart", "chart_type": "bar", "data": {"labels": ["A"], "datasets": []}}},
            {"type": "content", "side_panel": {"type": "graph"}},
            {"type": "content", "side_panel": {"type": "3d_model"}},
            {"type": "p5_simulation", "p5_code": "function setup(){}"},
            {"type": "visual", "visual_svg": "<svg></svg>"},
            {"type": "content", "side_panel": {"type": "gif", "query": "celebrate"}},
            {"type": "content", "html": "<p>\\[\\sigma = F/A\\]</p>", "visual": {"concept": "stress formula", "type": "equation"}},
        ]
        body = self.plan("vera", scenes)
        got = [(p["source"], p.get("renderer")) for p in body["plans"]]
        self.assertEqual(got, [("PROCEDURAL", "chart"), ("PROCEDURAL", "graph"), ("PROCEDURAL", "3d_model"), ("PROCEDURAL", "p5"),
                               ("PROCEDURAL", "svg"), ("EXTERNAL_MEDIA", "gif"), ("PROCEDURAL", "mathjax")])

    def test_manim_code_uses_the_existing_pipeline_without_rendering(self):
        with ai_planning_enabled():  # rendering (Phase 10: the Manim service and its sandbox) fails the test if called
            plan = self.slot(self.plan("vera", [{"type": "simulation", "title": "Derive it", "manim_code": "class D(Scene): pass"}]), 0, "main")
        self.assertEqual((plan["source"], plan["renderer"], plan["requires_generation"]), ("MANIM", "manim", False))

    def test_scenes_that_need_no_external_visual(self):
        body = self.plan("vera", [{"type": "quiz_checkpoint", "question": "Q?"}, {"type": "content", "html": "<p>Text only</p>"},
                                  {"type": "content", "visual": {"type": "none"}}])
        self.assertEqual(body["plans"], [])  # nothing to route: no slots are created

    # ---- AI fallback and cost control --------------------------------------------------------

    def test_ai_is_only_planned_and_only_as_a_last_resort(self):
        scenes = [
            {"type": "ai_video", "title": "Glacier calving into the ocean", "prompt": "a glacier calving into the ocean"},
            {"type": "content", "title": "Coral reef", "side_panel": {"type": "image", "prompt": "a colourful coral reef"}},
        ]
        with ai_planning_enabled():
            body = self.plan("vera", scenes)
        video, image = self.slot(body, 0, "main"), self.slot(body, 1, "side")
        self.assertEqual((video["source"], video["provider"], video["requires_generation"]), ("AI_VIDEO", "manual", True))
        self.assertEqual((image["source"], image["provider"], image["requires_generation"]), ("AI_IMAGE", "pollinations", True))
        self.assertNotIn("url", video)

    def test_ai_can_be_switched_off_per_request_and_for_the_server(self):
        scene = {"type": "ai_video", "title": "Aurora over mountains", "prompt": "aurora over mountains"}
        with ai_planning_enabled():
            off = self.slot(self.plan("vera", [scene], allow_ai_generation=False), 0, "main")
        self.assertEqual((off["source"], off["would_require"], off["requires_generation"]), ("NONE", "AI_VIDEO", False))
        server_off = self.slot(self.plan("vera", [scene]), 0, "main")  # AI_GENERATION_ENABLED=0 in tests
        self.assertEqual((server_off["source"], server_off["would_require"]), ("NONE", "AI_VIDEO"))
        self.assertIn("server", server_off["reason"])
        # And the generators themselves refuse, so nothing can reach a paid provider
        self.assertEqual(self.client.post("/generate-ai-video", json={"prompt": "x"}, headers=auth("vera")).status_code, 403)
        self.assertEqual(self.client.get(f"/get-image?prompt=never-cached-{uuid.uuid4().hex}").status_code, 403)

    def test_preferring_existing_assets_can_be_turned_off(self):
        self.asset("vera", description="rotating wind turbine at sunset over hills")
        scene = {"type": "ai_video", "title": "Rotating wind turbine at sunset", "prompt": "rotating wind turbine at sunset over hills"}
        with ai_planning_enabled():
            plan = self.slot(self.plan("vera", [scene], prefer_existing_assets=False), 0, "main")
        self.assertEqual(plan["source"], "AI_VIDEO")

    def test_planning_a_whole_lesson_never_generates(self):
        scenes = [{"type": "ai_video", "title": f"Topic {i}", "prompt": f"unique subject {uuid.uuid4().hex}"} for i in range(5)]
        scenes += [{"type": "simulation", "manim_code": "class X(Scene): pass"},
                   {"type": "content", "side_panel": {"type": "image", "prompt": f"new {uuid.uuid4().hex}"}}]
        with ai_planning_enabled():  # every generator raises if called
            body = self.plan("vera", scenes)
        self.assertEqual(body["summary"], {"AI_VIDEO": 5, "MANIM": 1, "AI_IMAGE": 1})

    # ---- older lessons ------------------------------------------------------------------------

    def test_older_lessons_with_plain_urls_keep_their_media(self):
        name = self.static_file(self.fresh("video"))
        image_name = self.static_file(self.fresh("image"), ext="png")
        scenes = [
            {"type": "ai_video", "prompt": "anything", "video_url": f"/static/{name}?t=1"},
            {"type": "ai_video", "prompt": "anything", "video_url": "https://cdn.example.org/clip.mp4"},
            {"type": "content", "html": f'<img src="/static/{image_name}"><img src="/static/gone-diagram.png">'},
        ]
        body = self.plan("vera", scenes, allow_ai_generation=False)
        first, second = self.slot(body, 0, "main"), self.slot(body, 1, "main")
        self.assertEqual((first["source"], first["selection"], first["url"]), ("UPLOADED_ASSET", "existing", f"/static/{name}?t=1"))
        self.assertEqual(second["source"], "UPLOADED_ASSET")
        self.assertEqual(self.slot(body, 2, f"html:/static/{image_name}")["source"], "UPLOADED_ASSET")
        gone = self.slot(body, 2, "html:/static/gone-diagram.png")
        self.assertEqual((gone["source"], gone["error"]), ("NONE", "media_missing"))

    def test_an_older_url_whose_file_is_gone_is_routed_again(self):
        clip = self.asset("vera", description="meteor shower streaking across the night sky")
        scene = {"type": "ai_video", "title": "Meteor shower", "prompt": "meteor shower streaking across the night sky",
                 "video_url": "/static/deleted-long-ago.mp4"}
        plan = self.slot(self.plan("vera", [scene]), 0, "main")
        self.assertEqual((plan["source"], plan["asset_id"]), ("ASSET", clip["id"]))

    # ---- preview and export see the same plan -------------------------------------------

    def test_a_matched_asset_stays_chosen_until_it_is_unusable(self):
        first = self.asset("vera", description="snow avalanche rushing down a mountain slope")
        scene = {"type": "ai_video", "title": "Avalanche", "prompt": "snow avalanche rushing down a mountain slope"}
        plan = self.slot(self.plan("vera", [scene]), 0, "main")
        self.assertEqual(plan["asset_id"], first["id"])
        # A later, even better match does not change what the lesson shows (preview == export)
        better = self.asset("vera", description="snow avalanche rushing down a mountain slope",
                            keywords=["avalanche", "snow", "mountain", "slope"])
        scene["visual_plan"] = {"main": plan}
        kept = self.slot(self.plan("vera", [scene]), 0, "main")
        self.assertEqual((kept["asset_id"], kept["selection"]), (first["id"], "matched"))
        self.assertIn("earlier plan", kept["reason"])
        # Once it is gone, the router chooses again
        self.client.delete(f"/api/assets/{first['id']}", headers=auth("vera"))
        again = self.slot(self.plan("vera", [scene]), 0, "main")
        self.assertEqual(again["asset_id"], better["id"])

    def test_the_same_lesson_plans_the_same_way_every_time(self):
        self.asset("vera", description="lava flowing into the sea with steam")
        scenes = [{"type": "ai_video", "title": "Lava meets the sea", "prompt": "lava flowing into the sea with steam"},
                  {"type": "content", "side_panel": {"type": "chart", "data": {}}}]
        a, b = self.plan("vera", scenes), self.plan("vera", scenes)
        strip = lambda body: [{k: v for k, v in p.items() if k != "url"} for p in body["plans"]]  # links carry fresh tokens
        self.assertEqual(strip(a), strip(b))

    # ---- references and asset descriptions ---------------------------------------------

    def test_assets_chosen_by_the_router_are_recorded_as_used_once(self):
        clip = self.asset("vera", description="hot air balloons rising at dawn")
        scene = {"type": "ai_video", "title": "Balloons", "prompt": "hot air balloons rising at dawn"}
        plan = self.slot(self.plan("vera", [scene]), 0, "main")
        scene["visual_plan"] = {"main": plan}
        res = self.client.post("/save-history", json={"subject_name": "Plans", "scenes": [scene]}, headers=auth("vera"))
        project = res.json()["id"]
        detail = self.client.get(f"/api/assets/{clip['id']}", headers=auth("vera")).json()
        self.assertEqual([u["field"] for u in detail["used_in"] if u["project_id"] == project], ["scenes[0].visual_plan.main"])
        self.assertEqual(self.client.delete(f"/api/assets/{clip['id']}", headers=auth("vera")).status_code, 409)

    def test_planning_for_a_saved_lesson_marks_the_chosen_assets_as_used(self):
        first = self.asset("vera", description="paper lanterns floating on a river at night")
        scene = {"type": "ai_video", "title": "Lanterns", "prompt": "paper lanterns floating on a river at night"}
        project = self.client.post("/save-history", json={"subject_name": "Refs", "scenes": [scene]}, headers=auth("vera")).json()["id"]
        uses = lambda asset: [u["field"] for u in self.client.get(f"/api/assets/{asset['id']}", headers=auth("vera")).json()["used_in"]
                              if u["project_id"] == project]
        self.plan("vera", [scene], project_id=project)
        self.plan("vera", [scene], project_id=project)  # planning again adds no duplicate
        self.assertEqual(uses(first), ["scenes[0].visual_plan.main"])
        # When the scene's choice changes, the earlier asset is released
        second = self.asset("vera", kind="video")
        scene["video_asset_id"] = second["id"]
        self.plan("vera", [scene], project_id=project)
        self.assertEqual(uses(first), [])
        self.assertEqual(uses(second), ["scenes[0].visual_plan.main"])
        # Only your own lessons
        res = self.client.post("/api/visuals/plan", json={"scenes": [scene], "project_id": project}, headers=auth("otto"))
        self.assertEqual(res.status_code, 404)

    def test_side_panels_and_the_preferred_media_type(self):
        clip = self.asset("vera", description="honeybees building a honeycomb")
        still = self.asset("vera", kind="image", description="honeybees building a honeycomb")
        scene = {"type": "content", "title": "Honeybees", "visual": {"concept": "honeybees building a honeycomb"}}
        image = self.slot(self.plan("vera", [scene]), 0, "side")
        video = self.slot(self.plan("vera", [scene], preferred_media_type="video"), 0, "side")
        self.assertEqual((image["asset_id"], image["media"]), (still["id"], "STATIC_IMAGE"))
        self.assertEqual((video["asset_id"], video["media"]), (clip["id"], "VIDEO"))
        # A side panel never plans an AI video: without a matching library video it gets an AI still
        wanted = {"type": "content", "title": "Glass blowing", "visual": {"type": "video", "concept": f"glass blowing {uuid.uuid4().hex}"}}
        with ai_planning_enabled():
            plan = self.slot(self.plan("vera", [wanted]), 0, "side")
        self.assertEqual((plan["source"], plan["media"]), ("AI_IMAGE", "STATIC_IMAGE"))
        # With prefer_existing_assets off, an earlier match is not kept either
        scene["visual_plan"] = {"side": image}
        with ai_planning_enabled():
            fresh = self.slot(self.plan("vera", [scene], prefer_existing_assets=False), 0, "side")
        self.assertEqual(fresh["source"], "AI_IMAGE")

    def test_an_upload_is_matched_once_it_is_described(self):
        user = "wren"
        ensure_user(user)
        clip = self.asset(user)  # uploaded with no description or keywords
        scene = {"type": "ai_video", "title": "Bridge under heavy traffic", "prompt": "Show a bridge experiencing heavy traffic load"}
        before = self.slot(self.plan(user, [scene]), 0, "main")
        self.assertNotEqual(before.get("asset_id"), clip["id"])
        res = self.client.patch(f"/api/assets/{clip['id']}", headers=auth(user),
                                json={"description": "Bridge carrying heavy traffic", "keywords": ["bridge", "traffic", "load", "stress"]})
        self.assertEqual(res.status_code, 200, res.text)
        after = self.slot(self.plan(user, [scene], debug=True), 0, "main")
        self.assertEqual((after["source"], after["selection"], after["asset_id"]), ("ASSET", "matched", clip["id"]))
        self.assertGreaterEqual(after["score"], visuals.MATCH_THRESHOLD)

    def test_describing_an_asset(self):
        clip = self.asset("vera")
        res = self.client.patch(f"/api/assets/{clip['id']}", json={"description": "  Tidal waves  ", "keywords": ["wave", " wave ", "tide"]},
                                headers=auth("vera"))
        self.assertEqual(res.json()["details"], {"description": "Tidal waves", "keywords": ["wave", "tide"]})
        self.assertEqual(self.client.patch(f"/api/assets/{clip['id']}", json={"description": "x"}, headers=auth("otto")).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/assets/{clip['id']}", json={"keywords": ["k"] * 31}, headers=auth("vera")).status_code, 422)
        with database.SessionLocal() as db:
            system = db.query(models.Asset).filter(models.Asset.owner_id.is_(None)).first().id
        self.assertEqual(self.client.patch(f"/api/assets/{system}", json={"description": "x"}, headers=auth("vera")).status_code, 403)

    def test_plan_endpoint_validation(self):
        self.assertEqual(self.client.post("/api/visuals/plan", json={"scenes": []}).status_code, 401)
        self.assertEqual(self.client.post("/api/visuals/plan", json={"scenes": [{}] * 401}, headers=auth("vera")).status_code, 413)
        self.assertEqual(self.client.post("/api/visuals/plan", json={"scenes": [], "preferred_media_type": "hologram"},
                                          headers=auth("vera")).status_code, 422)
        body = self.plan("vera", [{"type": "content", "side_panel": {"type": "chart"}}], debug=True)
        self.assertTrue(body["plans"][0]["debug"])
        self.assertNotIn("debug", self.plan("vera", [{"type": "content", "side_panel": {"type": "chart"}}])["plans"][0])

    # ---- the Visual Director's preference (Phase 15) -------------------------------------------

    def test_visual_direction_match_terms_settle_a_tie_but_never_make_a_match(self):
        concept = "mangrove roots filtering tidal seawater"
        pollen = self.asset("vera", kind="image", description=concept, keywords=["pollen"])
        salt = self.asset("vera", kind="image", description=concept, keywords=["salt crystals"])
        scene = {"type": "content", "title": "Mangroves", "visual": {"concept": concept}}
        plain = self.slot(self.plan("vera", [scene], allow_ai_generation=False), 0, "side")
        self.assertEqual(plain["score"], 1.0)  # both cover the request fully: a tie
        self.assertIn(plain["asset_id"], (pollen["id"], salt["id"]))
        for terms, expected in ((["Pollen grains"], pollen), (["Salt crystals", "Mangrove"], salt)):
            directed = dict(scene, visual_direction=direction({"prefer_existing": True, "match_terms": terms}))
            plan = self.slot(self.plan("vera", [directed], allow_ai_generation=False), 0, "side")
            self.assertEqual((plan["selection"], plan["asset_id"]), ("matched", expected["id"]), terms)
        # Terms alone match nothing: they only ever add to a match the request already has
        unrelated = {"type": "content", "title": "Quokka", "visual": {"concept": f"quokka smiling {uuid.uuid4().hex[:8]}"},
                     "visual_direction": direction({"prefer_existing": True, "match_terms": ["Pollen", "salt crystals", concept]})}
        alone = self.slot(self.plan("vera", [unrelated], allow_ai_generation=False), 0, "side")
        self.assertNotIn(alone.get("asset_id"), (pollen["id"], salt["id"]))
        self.assertEqual(alone["source"], "NONE")

    def test_visual_direction_preferred_media_decides_only_where_the_media_is_open(self):
        concept = "sea otters cracking shells on their bellies"
        clip = self.asset("vera", description=concept)
        still = self.asset("vera", kind="image", description=concept)
        scene = {"type": "content", "title": "Sea otters", "visual": {"concept": concept}}

        def side(scene, route, **options):
            body = self.plan("vera", [dict(scene, visual_direction=direction(route))], allow_ai_generation=False, **options)
            return self.slot(body, 0, "side")

        self.assertEqual(side(scene, {"prefer_existing": True, "preferred_media": "image"})["asset_id"], still["id"])
        video = side(scene, {"prefer_existing": True, "preferred_media": "video"})
        self.assertEqual((video["asset_id"], video["media"]), (clip["id"], "VIDEO"))
        # The lesson's own preferred_media_type still decides when it is given
        self.assertEqual(side(scene, {"preferred_media": "video"}, preferred_media_type="image")["asset_id"], still["id"])
        # A visual whose media the screenplay fixes keeps it
        typed = dict(scene, visual={"type": "image", "concept": concept})
        self.assertEqual(side(typed, {"prefer_existing": True, "preferred_media": "video"})["asset_id"], still["id"])
        panel = {"type": "content", "title": "Sea otters", "side_panel": {"type": "image", "prompt": concept}}
        fixed = side(panel, {"prefer_existing": True, "preferred_media": "video"})
        self.assertEqual((fixed["asset_id"], fixed["media"]), (still["id"], "STATIC_IMAGE"))
        chart = side({"type": "content", "title": "Otters", "side_panel": {"type": "chart", "data": {}}}, {"preferred_media": "video"})
        self.assertEqual((chart["source"], chart["renderer"]), ("PROCEDURAL", "chart"))
        # And a side visual never becomes an AI video
        wanted = {"type": "content", "title": "Glass", "visual": {"concept": f"molten glass {uuid.uuid4().hex}"},
                  "visual_direction": direction({"prefer_existing": True, "preferred_media": "video"})}
        with ai_planning_enabled():
            plan = self.slot(self.plan("vera", [wanted]), 0, "side")
        self.assertEqual((plan["source"], plan["media"]), ("AI_IMAGE", "STATIC_IMAGE"))

    def test_visual_direction_prefer_existing_reuses_first_but_never_turns_ai_on(self):
        concept = "kelp forest swaying in ocean currents"
        clip = self.asset("vera", description=concept)
        scene = {"type": "ai_video", "title": "Kelp forest swaying", "prompt": concept}
        directed = dict(scene, visual_direction=direction({"prefer_existing": True, "match_terms": ["Kelp forest"]}))
        with ai_planning_enabled():
            plain = self.slot(self.plan("vera", [scene], prefer_existing_assets=False), 0, "main")
            reused = self.slot(self.plan("vera", [directed], prefer_existing_assets=False), 0, "main")
        self.assertEqual(plain["source"], "AI_VIDEO")
        self.assertEqual((reused["source"], reused["selection"], reused["asset_id"]), ("ASSET", "matched", clip["id"]))
        # It never switches AI generation on: off for the lesson, or for the server
        new = {"type": "ai_video", "title": "Narwhal", "prompt": f"narwhal tusk {uuid.uuid4().hex}",
               "visual_direction": direction({"prefer_existing": True, "preferred_media": "video", "match_terms": ["Narwhal"]})}
        with ai_planning_enabled():
            off = self.slot(self.plan("vera", [new], allow_ai_generation=False, prefer_existing_assets=False), 0, "main")
        self.assertEqual((off["source"], off["would_require"], off["requires_generation"]), ("NONE", "AI_VIDEO", False))
        server_off = self.slot(self.plan("vera", [new]), 0, "main")  # AI_GENERATION_ENABLED=0 in tests
        self.assertEqual((server_off["source"], server_off["would_require"], server_off["requires_generation"]),
                         ("NONE", "AI_VIDEO", False))

    def test_visual_direction_never_reaches_a_prompt_or_the_ai_cache(self):
        marker = "zqxvw"  # a word no prompt or concept here contains
        scenes = [{"type": "ai_video", "title": "Tardigrade", "prompt": f"a tardigrade walking on moss {uuid.uuid4().hex}"},
                  {"type": "content", "title": "Water bear", "side_panel": {"type": "image", "prompt": f"a water bear {uuid.uuid4().hex}"}},
                  {"type": "content", "title": "Moss", "visual": {"concept": f"moss cushion {uuid.uuid4().hex}"}}]
        route = {"prefer_existing": True, "preferred_media": "video", "match_terms": [f"{marker} tardigrade", marker]}

        def planned(scenes):
            prompts, identities = [], []
            ai_plan, lookup_many = visuals.RoutingContext.ai_plan, server.ai_cache.lookup_many

            def spy_plan(ctx, media_type, prompt, generation_possible):
                prompts.append((media_type, prompt, generation_possible))
                return ai_plan(ctx, media_type, prompt, generation_possible)

            def spy_lookup(db, user_id, wanted):
                identities.append(sorted((list(key), identity) for key, identity in wanted.items()))
                return lookup_many(db, user_id, wanted)

            with ai_planning_enabled(), mock.patch.object(visuals.RoutingContext, "ai_plan", spy_plan), \
                    mock.patch.object(server.ai_cache, "lookup_many", spy_lookup):
                body = self.plan("vera", scenes)
            return [{k: v for k, v in p.items() if k != "url"} for p in body["plans"]], prompts, identities

        plain = planned(scenes)
        directed = planned([dict(s, visual_direction=direction(route)) for s in scenes])
        self.assertEqual(len(set(plain[1])), 3)  # three AI requests (routing, then the AI cache step)
        self.assertTrue(plain[2] and plain[2][0])
        self.assertEqual(directed, plain)  # the same plans, generation prompts and AI cache identities
        self.assertNotIn(marker, json.dumps([directed[1], directed[2]], default=str))

    def test_a_review_of_a_directed_scene_stays_current_and_undirected_scenes_keep_their_fingerprint(self):
        concept = f"tide pool anemones {uuid.uuid4().hex[:6]}"
        plain = {"type": "content", "title": "Tide pools", "visual": {"concept": concept}}
        directed = dict(plain, visual_direction=direction({"prefer_existing": True, "match_terms": ["Anemone"]}))
        project = self.client.post("/save-history", json={"subject_name": "Directed", "scenes": [plain, directed]},
                                   headers=auth("vera")).json()["id"]
        for index in (0, 1):
            res = self.client.post("/api/visuals/review", json={"project_id": project, "scene_index": index, "slot": "side",
                                                                "action": "keep"}, headers=auth("vera"))
            self.assertEqual(res.status_code, 200, res.text)
        scenes = self.client.get(f"/api/projects/{project}", headers=auth("vera")).json()["scenes"]
        self.assertEqual(scenes[1]["visual_direction"], directed["visual_direction"])
        self.assertEqual(scenes[0]["visual_review"]["side"]["fingerprint"], legacy_fingerprint(plain, "side"))
        self.assertEqual(scenes[1]["visual_review"]["side"]["fingerprint"], legacy_fingerprint(plain, "side"))  # the route is not part of it
        body = self.plan("vera", scenes)
        for index in (0, 1):
            plan = self.slot(body, index, "side")
            self.assertEqual((plan["review_status"], plan["review_stale"]), ("approved", False), index)
        # The approval stays current with or without the direction: switching between the Classic and cinematic styles
        # (which adds or drops the direction) never re-opens a visual the user approved
        undirected = self.slot(self.plan("vera", [{k: v for k, v in scenes[1].items() if k != "visual_direction"}]), 0, "side")
        self.assertEqual((undirected["review_status"], undirected["review_stale"]), ("approved", False))

    # ---- performance -------------------------------------------------------------------------

    def test_planning_is_fast_and_does_not_query_per_asset(self):
        base = self.row(self.asset("otto", description="base clip")["id"])
        with database.SessionLocal() as db:
            for i in range(600):
                db.add(models.Asset(id=uuid.uuid4().hex, scope_key=f"user:{self.ids['otto']}", owner_id=self.ids["otto"], kind="video",
                                    source="upload", status="ready", file_name=f"bulk{i}.mp4", mime_type="video/mp4",
                                    storage_volume="assets", storage_key=f"bulk/{i}.mp4", file_size=base.file_size,
                                    sha256=base.sha256, details=json.dumps({"description": f"bulk clip number {i} of a quarry truck"})))
            db.commit()
        scenes = [{"type": "ai_video", "title": f"Harbour crane {i}", "prompt": "harbour crane lifting containers"} for i in range(60)]
        queries = []
        listener = lambda *args: queries.append(1)
        event.listen(database.engine, "before_cursor_execute", listener)
        try:
            start = time.perf_counter()
            body = self.plan("otto", scenes, allow_ai_generation=False)
            elapsed = time.perf_counter() - start
        finally:
            event.remove(database.engine, "before_cursor_execute", listener)
        self.assertEqual(len(body["plans"]), 60)
        self.assertLess(elapsed, 3.0)
        self.assertLess(len(queries), 15, f"{len(queries)} queries for 60 scenes and 600 assets")
        with database.SessionLocal() as db:
            db.query(models.Asset).filter(models.Asset.file_name.like("bulk%")).delete(synchronize_session=False)
            db.commit()


if __name__ == "__main__":
    unittest.main()
