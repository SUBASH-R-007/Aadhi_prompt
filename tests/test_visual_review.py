"""Backend tests for Visual Review (Phase 6): POST /api/visuals/review and the visual router's
ReviewDecisionRule (visuals.py), with the asset library (Phase 3) and the AI cache (Phase 5).

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
No AI provider is reached: the provider functions are fakes (or fail the test if called) and any
network call fails the test. "Export" below is what export preparation does: plan the saved lesson
again (with AI allowed) after reloading it.
"""
import asyncio
import json
import os
import uuid
from contextlib import contextmanager
from unittest import mock
import unittest

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402


def words():
    return " ".join(f"r{uuid.uuid4().hex[:7]}" for _ in range(5))  # vocabulary no other test uses


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class VisualReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.ids = {name: ensure_user(name) for name in ("rita", "sam")}
        cls.media = os.path.join(TMP, "review-media")
        os.makedirs(cls.media, exist_ok=True)
        cls.written = []

    @classmethod
    def tearDownClass(cls):
        for path in cls.written:
            if os.path.exists(path):
                os.remove(path)

    # ---- helpers -------------------------------------------------------------------------

    def asset(self, user, kind="video", description=None):
        path = os.path.join(self.media, f"{uuid.uuid4().hex}.{'png' if kind == 'image' else 'mp3' if kind == 'audio' else 'mp4'}")
        colour = uuid.uuid4().hex[:6]
        if kind == "image":
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{colour}:size=64x48", "-frames:v", "1", path)
        elif kind == "audio":
            ffmpeg("-f", "lavfi", "-i", f"sine=frequency={200 + int(colour[:3], 16) % 900}:duration=1", "-c:a", "libmp3lame", path)
        else:
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{colour}:size=160x120:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
        with open(path, "rb") as f:
            res = self.client.post("/api/assets", files={"file": (os.path.basename(path), f.read(), "application/octet-stream")}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        asset = res.json()["asset"]
        if description:
            self.assertEqual(self.client.patch(f"/api/assets/{asset['id']}", json={"description": description}, headers=auth(user)).status_code, 200)
        return asset

    def lesson(self, user, scenes):
        res = self.client.post("/save-history", json={"subject_name": "Review", "scenes": scenes}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def load(self, user, project):
        return self.client.get(f"/api/projects/{project}", headers=auth(user)).json()["scenes"]

    def review(self, user, project, scene_index, slot, action, status=200, **extra):
        res = self.client.post("/api/visuals/review", json={"project_id": project, "scene_index": scene_index, "slot": slot,
                                                            "action": action, **extra}, headers=auth(user))
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def plan(self, user, scenes, **options):
        res = self.client.post("/api/visuals/plan", json={"scenes": scenes, **options}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["plans"]

    def export_plan(self, user, project):
        """What the export uses: the saved lesson, reloaded and planned with AI allowed."""
        return self.plan(user, self.load(user, project), allow_ai_generation=True)

    @staticmethod
    def slot(plans, scene_index, slot):
        return next(p for p in plans if p["scene_index"] == scene_index and p["slot"] == slot)

    @contextmanager
    def no_generation(self):
        """Any provider call fails the test."""
        boom = AssertionError("an AI provider was called")
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1"}), \
                mock.patch.object(server.ai_media, "call_provider", side_effect=boom), \
                mock.patch("urllib.request.urlopen", side_effect=boom):
            yield

    @contextmanager
    def providers(self, enabled=True):
        calls = {"video": 0, "image": 0}

        async def fake_video(prompt, provider, filepath):
            calls["video"] += 1
            self.written.append(filepath)
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{uuid.uuid4().hex[:6]}:size=160x120:rate=10:duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", filepath)

        async def fake_call(adapter, request, settings, dest, progress=None):
            if request.media_type != "video":
                raise AssertionError("unexpected image generation")
            return await fake_video(request.prompt, adapter.name, dest)

        # A key must be configured for Veo to count as set up; this one is never sent anywhere
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1" if enabled else "0", "GEMINI_API_KEY": "test-not-a-real-key"}), \
                mock.patch.object(server, "ltx_pipeline", "GEMINI_API"), \
                mock.patch.object(server.ai_media, "call_provider", side_effect=fake_call), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("a real network call was made")):
            yield calls

    # ---- 1-4: the four states -------------------------------------------------------------

    def test_new_visuals_start_pending(self):
        topic = words()
        scenes = [{"type": "ai_video", "title": topic, "prompt": topic},
                  {"type": "content", "title": "Data", "side_panel": {"type": "chart", "data": {"labels": ["a"], "datasets": []}}}]
        with self.no_generation():
            plans = self.plan("rita", scenes)
        self.assertEqual({p["review_status"] for p in plans}, {"pending"})
        self.assertEqual(self.slot(plans, 1, "side")["source"], "PROCEDURAL")

    def test_keep_approves_the_current_visual_without_generating(self):
        topic = words()
        clip = self.asset("rita", description=topic)
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic}])
        with self.no_generation():
            result = self.review("rita", project, 0, "main", "keep")
        self.assertEqual((result["review"]["status"], result["review"]["asset_id"]), ("approved", clip["id"]))
        plan = result["plans"]["main"]
        self.assertEqual((plan["review_status"], plan["selection"], plan["asset_id"]), ("approved", "approved", clip["id"]))
        self.assertIn("reviewed_at", result["review"])

    def test_choosing_an_asset_marks_the_visual_changed(self):
        topic = words()
        self.asset("rita", description=topic)  # what the router would pick
        chosen = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic}])
        with self.no_generation():
            result = self.review("rita", project, 0, "main", "choose", asset_id=chosen["id"])
        plan = result["plans"]["main"]
        self.assertEqual((plan["source"], plan["selection"], plan["asset_id"], plan["review_status"]),
                         ("ASSET", "reviewed", chosen["id"], "changed"))
        saved = self.load("rita", project)[0]
        self.assertEqual(saved["visual_review"]["main"]["asset_id"], chosen["id"])
        self.assertEqual(saved["visual_plan"]["main"]["asset_id"], chosen["id"])

    def test_removing_leaves_the_scene_without_a_visual(self):
        topic = words()
        project = self.lesson("rita", [{"type": "content", "title": topic, "side_panel": {"type": "image", "prompt": topic}}])
        with self.no_generation():
            plan = self.review("rita", project, 0, "side", "remove")["plans"]["side"]
        self.assertEqual((plan["source"], plan["selection"], plan["review_status"]), ("NONE", "removed", "removed"))
        self.assertFalse(plan["requires_generation"])

    # ---- 5-9: decisions hold through replanning, export and reload --------------------------------

    def test_a_chosen_asset_survives_replanning(self):
        topic = words()
        chosen = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic}])
        self.review("rita", project, 0, "main", "choose", asset_id=chosen["id"])
        # Later: a perfect library match appears and the lesson itself names another video
        better = self.asset("rita", description=topic)
        scenes = self.load("rita", project)
        scenes[0]["video_asset_id"] = better["id"]
        for options in ({}, {"allow_ai_generation": True}, {"prefer_existing_assets": False}):
            plan = self.slot(self.plan("rita", scenes, **options), 0, "main")
            self.assertEqual((plan["asset_id"], plan["selection"]), (chosen["id"], "reviewed"), options)

    def test_export_respects_removed_approved_and_changed_visuals(self):
        a, b, c = words(), words(), words()
        approved = self.asset("rita", description=b)
        chosen = self.asset("rita", kind="image")
        project = self.lesson("rita", [
            {"type": "ai_video", "title": a, "prompt": a},                                   # removed
            {"type": "ai_video", "title": b, "prompt": b},                                   # approved (a library match)
            {"type": "content", "title": c, "side_panel": {"type": "image", "prompt": c}},   # changed
        ])
        with self.no_generation():
            self.review("rita", project, 0, "main", "remove")
            self.review("rita", project, 1, "main", "keep")
            self.review("rita", project, 2, "side", "choose", asset_id=chosen["id"])
            self.asset("rita", description=b + " " + b)  # a better match after the approval
            plans = self.export_plan("rita", project)
        removed, kept, changed = self.slot(plans, 0, "main"), self.slot(plans, 1, "main"), self.slot(plans, 2, "side")
        self.assertEqual((removed["selection"], removed["requires_generation"]), ("removed", False))  # not generated at export
        self.assertEqual((kept["asset_id"], kept["review_status"]), (approved["id"], "approved"))
        self.assertEqual((changed["asset_id"], changed["media"], changed["review_status"]), (chosen["id"], "STATIC_IMAGE", "changed"))

    def test_review_survives_save_and_reload(self):
        topic = words()
        chosen = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic},
                                       {"type": "content", "title": "x", "side_panel": {"type": "chart", "data": {}}}])
        self.review("rita", project, 0, "main", "choose", asset_id=chosen["id"])
        self.review("rita", project, 1, "side", "keep")
        reloaded = self.load("rita", project)
        self.assertEqual((reloaded[0]["visual_review"]["main"]["status"], reloaded[1]["visual_review"]["side"]["status"]), ("changed", "approved"))
        plans = self.plan("rita", reloaded)
        self.assertEqual((self.slot(plans, 0, "main")["asset_id"], self.slot(plans, 1, "side")["review_status"]), (chosen["id"], "approved"))
        # Saving the page's copy as a new snapshot keeps the decisions too
        copy = self.lesson("rita", reloaded)
        self.assertEqual(self.slot(self.export_plan("rita", copy), 0, "main")["asset_id"], chosen["id"])

    # ---- 10-12: access and failures -------------------------------------------------------------

    def test_another_users_private_asset_cannot_be_chosen(self):
        topic = words()
        theirs = self.asset("sam")
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic}])
        res = self.review("rita", project, 0, "main", "choose", status=404, asset_id=theirs["id"])
        self.assertEqual(res["detail"], "Asset not found.")
        self.assertNotIn("visual_review", self.load("rita", project)[0])
        # Even written into the lesson by hand, the router does not show it
        scenes = self.load("rita", project)
        scenes[0]["visual_review"] = {"main": {"status": "changed", "asset_id": theirs["id"]}}
        plan = self.slot(self.plan("rita", scenes), 0, "main")
        self.assertEqual((plan["error"], plan["source"]), ("asset_unavailable", "NONE"))
        self.assertNotIn("url", plan)
        # And another user cannot review this lesson at all
        self.review("sam", project, 0, "main", "remove", status=404)

    def test_a_shared_asset_can_be_chosen(self):
        with database.SessionLocal() as db:
            shared = db.query(models.Asset).filter(models.Asset.owner_id.is_(None), models.Asset.kind == "image",
                                                   models.Asset.source == "mascot").first().id
        project = self.lesson("rita", [{"type": "content", "title": words(), "side_panel": {"type": "chart", "data": {}}}])
        plan = self.review("rita", project, 0, "side", "choose", asset_id=shared)["plans"]["side"]
        self.assertEqual((plan["source"], plan["asset_id"], plan["media"]), ("SYSTEM_ASSET", shared, "STATIC_IMAGE"))

    def test_a_missing_chosen_asset_is_reported_and_recoverable(self):
        topic = words()
        chosen = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": topic, "prompt": topic}])
        self.review("rita", project, 0, "main", "choose", asset_id=chosen["id"])
        with database.SessionLocal() as db:
            os.remove(server.asset_library.file_path(db.get(models.Asset, chosen["id"])))
        plan = self.slot(self.export_plan("rita", project), 0, "main")
        self.assertEqual((plan["error"], plan["review_status"], plan["selection"]), ("asset_unavailable", "changed", "reviewed"))
        self.assertIn("unavailable", plan["reason"])
        # Recover: choose another visual, or go back to the automatic choice
        self.review("rita", project, 0, "main", "choose", status=409, asset_id=chosen["id"])  # the broken one is refused
        replacement = self.asset("rita")
        self.assertEqual(self.review("rita", project, 0, "main", "choose", asset_id=replacement["id"])["plans"]["main"]["asset_id"], replacement["id"])
        reset = self.review("rita", project, 0, "main", "reset")
        self.assertIsNone(reset["review"])
        self.assertEqual(reset["plans"]["main"]["review_status"], "pending")
        self.assertNotIn("visual_review", self.load("rita", project)[0])

    # ---- 13-18: AI cache and generation -------------------------------------------------------------

    def test_ai_off_still_uses_cached_visuals_and_blocks_new_ones(self):
        topic = words()
        project = self.lesson("rita", [{"type": "ai_video", "title": "Clip", "prompt": topic}])
        with self.providers() as calls:
            made = self.client.post("/generate-ai-video", json={"prompt": topic}, headers=auth("rita")).json()["asset_id"]
        with self.providers(enabled=False) as off:
            kept = self.review("rita", project, 0, "main", "keep", allow_ai_generation=False)
            refused = self.client.post("/generate-ai-video", json={"prompt": topic, "force_regenerate": True}, headers=auth("rita"))
            after = self.slot(self.export_plan("rita", project), 0, "main")
        self.assertEqual(calls["video"], 1)
        self.assertEqual(off, {"video": 0, "image": 0})
        self.assertEqual((kept["review"]["status"], kept["review"]["asset_id"]), ("approved", made))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual((after["asset_id"], after["review_status"]), (made, "approved"))
        self.assertIn(after["source"], ("AI_VIDEO", "ASSET"))  # the cached video, or the same asset found by its prompt
        self.assertEqual(self.load("rita", project)[0]["visual_review"]["main"]["asset_id"], made)  # unchanged by the refusal

    def test_a_new_version_is_a_new_asset_and_the_old_one_stays(self):
        topic = words()
        project = self.lesson("rita", [{"type": "ai_video", "title": "Clip", "prompt": topic}])
        other = self.lesson("rita", [{"type": "ai_video", "title": "Other lesson", "prompt": topic}])
        with self.providers() as calls:
            first = self.client.post("/generate-ai-video", json={"prompt": topic}, headers=auth("rita")).json()
            again = self.client.post("/generate-ai-video", json={"prompt": topic}, headers=auth("rita")).json()  # cached
            self.review("rita", project, 0, "main", "keep", asset_id=first["asset_id"])
            self.review("rita", other, 0, "main", "keep", asset_id=first["asset_id"])
            new = self.client.post("/generate-ai-video", json={"prompt": topic, "force_regenerate": True}, headers=auth("rita")).json()
            changed = self.review("rita", project, 0, "main", "choose", asset_id=new["asset_id"])
        self.assertEqual(calls["video"], 2)  # the first generation and the new version; the cached request made none
        self.assertEqual((again["asset_id"], again["cache_hit"]), (first["asset_id"], True))
        self.assertNotEqual(new["asset_id"], first["asset_id"])
        self.assertEqual((changed["plans"]["main"]["asset_id"], changed["review"]["status"]), (new["asset_id"], "changed"))
        with database.SessionLocal() as db:
            old = db.get(models.Asset, first["asset_id"])
            self.assertEqual(old.status, "ready")
            self.assertTrue(server.asset_library.file_exists(old))
        self.assertEqual(self.slot(self.export_plan("rita", other), 0, "main")["asset_id"], first["asset_id"])  # the other lesson keeps it

    def test_review_decisions_never_generate(self):
        topic = words()
        scenes = [{"type": "ai_video", "title": "A", "prompt": topic},
                  {"type": "content", "title": "B", "side_panel": {"type": "image", "prompt": topic}}]
        project = self.lesson("rita", scenes)
        chosen = self.asset("rita", kind="image")
        with self.no_generation():
            pending = self.slot(self.export_plan("rita", project), 0, "main")
            self.assertTrue(pending["requires_generation"])  # planned, never generated by reviewing
            self.review("rita", project, 0, "main", "keep")
            self.review("rita", project, 1, "side", "choose", asset_id=chosen["id"])
            self.review("rita", project, 1, "side", "remove")
            self.review("rita", project, 1, "side", "reset")
            plans = self.export_plan("rita", project)
        self.assertEqual(self.slot(plans, 0, "main")["review_status"], "approved")
        self.assertTrue(self.slot(plans, 0, "main")["requires_generation"])  # approving a planned visual does not make it

    # ---- 19-20: sharing and compatibility ------------------------------------------------------------

    def test_several_scenes_can_use_the_same_asset(self):
        shared_clip = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": words(), "prompt": words()},
                                       {"type": "ai_video", "title": words(), "prompt": words()}])
        for i in (0, 1):
            self.review("rita", project, i, "main", "choose", asset_id=shared_clip["id"])
        plans = self.export_plan("rita", project)
        self.assertEqual({self.slot(plans, i, "main")["asset_id"] for i in (0, 1)}, {shared_clip["id"]})
        used = {u["field"] for u in self.client.get(f"/api/assets/{shared_clip['id']}", headers=auth("rita")).json()["used_in"]
                if u["project_id"] == project}
        self.assertTrue({"scenes[0].visual_review.main", "scenes[1].visual_review.main"} <= used)

    def test_lessons_without_review_metadata_still_work(self):
        scenes = [{"type": "ai_video", "title": "Old", "prompt": "anything", "video_url": "https://cdn.example.org/old.mp4"},
                  {"type": "content", "title": "Old picture", "html": "<p>x</p>", "side_panel": {"type": "skill_tree"}},
                  {"type": "quiz_checkpoint", "question": "?"}]
        plans = self.plan("rita", scenes, allow_ai_generation=False)
        self.assertEqual({p["review_status"] for p in plans}, {"pending"})
        self.assertEqual(self.slot(plans, 0, "main")["source"], "UPLOADED_ASSET")
        self.assertFalse(any(p.get("error") for p in plans))

    # ---- lesson edits, validation, search --------------------------------------------------------------

    def test_editing_a_scene_makes_an_approval_pending_but_keeps_explicit_choices(self):
        topic, new_topic = words(), words()
        chosen = self.asset("rita", kind="image")
        project = self.lesson("rita", [{"type": "content", "title": "A", "side_panel": {"type": "chart", "data": {"labels": ["x"]}}},
                                       {"type": "content", "title": "B", "side_panel": {"type": "image", "prompt": topic}}])
        self.review("rita", project, 0, "side", "keep")
        self.review("rita", project, 1, "side", "choose", asset_id=chosen["id"])
        scenes = self.load("rita", project)
        scenes[0]["side_panel"]["data"] = {"labels": ["x", "y"]}  # the teacher edits the chart
        scenes[1]["side_panel"]["prompt"] = new_topic                # and the picture request
        plans = self.plan("rita", scenes)
        chart, picture = self.slot(plans, 0, "side"), self.slot(plans, 1, "side")
        self.assertEqual((chart["review_status"], chart["review_stale"]), ("pending", True))
        self.assertEqual((picture["asset_id"], picture["review_status"], picture["review_stale"]), (chosen["id"], "changed", True))
        # A generated video or a resolved link is not an edit
        video = self.lesson("rita", [{"type": "ai_video", "title": "V", "prompt": topic}])
        self.review("rita", video, 0, "main", "keep")
        scenes = self.load("rita", video)
        scenes[0]["video_url"] = "/static/made-later.mp4"
        self.assertFalse(self.slot(self.plan("rita", scenes), 0, "main")["review_stale"])

    def test_the_pages_copy_of_the_scene_is_saved_with_the_decision(self):
        topic = words()
        clip = self.asset("rita")
        project = self.lesson("rita", [{"type": "ai_video", "title": "Clip", "prompt": topic}])
        page_scene = {"type": "ai_video", "title": "Clip", "prompt": topic, "video_asset_id": clip["id"]}  # generated in the preview
        result = self.review("rita", project, 0, "main", "keep", scene=page_scene)
        self.assertEqual(result["review"]["asset_id"], clip["id"])
        self.assertEqual(self.load("rita", project)[0]["video_asset_id"], clip["id"])

    def test_requests_are_validated(self):
        project = self.lesson("rita", [{"type": "content", "title": "x", "html": "<p>no side panel</p>"},
                                       {"type": "ai_video", "title": "v", "prompt": words()},
                                       {"type": "content", "title": "y", "side_panel": {"type": "chart", "data": {}}}])
        image, audio = self.asset("rita", kind="image"), self.asset("rita", kind="audio")
        self.review("rita", project, 1, "html", "keep", status=422)
        self.review("rita", project, 1, "main", "approve", status=422)
        self.review("rita", project, 7, "main", "keep", status=404)
        self.review("rita", 999999, 1, "main", "keep", status=404)
        self.review("rita", project, 0, "side", "keep", status=404)            # that scene has no side visual
        self.review("rita", project, 1, "main", "choose", status=422)          # no asset
        self.review("rita", project, 1, "main", "choose", status=422, asset_id=image["id"])  # a picture cannot replace a video scene
        self.review("rita", project, 2, "side", "choose", status=422, asset_id=audio["id"])
        self.assertEqual(self.client.post("/api/visuals/review", json={"project_id": project, "scene_index": 1, "slot": "main",
                                                                       "action": "keep"}).status_code, 401)

    def test_the_library_search_finds_descriptions_and_keywords(self):
        tag = uuid.uuid4().hex[:10]
        described = self.asset("rita", kind="image", description=f"a suspension bridge {tag}")
        keyworded = self.asset("rita", kind="image")
        self.client.patch(f"/api/assets/{keyworded['id']}", json={"keywords": [f"cable-{tag}"]}, headers=auth("rita"))
        found = lambda q: {a["id"] for a in self.client.get(f"/api/assets?q={q}", headers=auth("rita")).json()["assets"]}  # noqa: E731
        self.assertIn(described["id"], found(tag))
        self.assertIn(keyworded["id"], found(f"cable-{tag}"))
        self.assertEqual(found(f"nothing-{tag}"), set())
        self.assertNotIn(described["id"], {a["id"] for a in self.client.get(f"/api/assets?q={tag}", headers=auth("sam")).json()["assets"]})


if __name__ == "__main__":
    unittest.main()
