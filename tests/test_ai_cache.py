"""Backend tests for the AI media cache (ai_cache.py) and the generators that use it (server.py).

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
No AI provider is ever reached: the AI media layer's provider call (server.ai_media.call_provider, the
only place a provider is asked for media) is replaced by a local fake that writes small media files,
and any network call (urllib) fails the test. AI generation stays off (backend_env) except inside
`providers()`.
"""
import asyncio
import hashlib
import json
import os
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402

import ai_cache  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402

PROMPT = "A slow-motion shot of a steel suspension bridge cable pulled taut under load"


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class AICacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.ids = {name: ensure_user(name) for name in ("ada", "ben", "cyd")}
        cls.written = []  # generated files in static_videos, removed afterwards

    @classmethod
    def tearDownClass(cls):
        for path in cls.written:
            if os.path.exists(path):
                os.remove(path)

    # ---- fakes -------------------------------------------------------------------------

    @contextmanager
    def providers(self, video="GEMINI_API", enabled=True, video_delay=0.0, image_colour=None):
        """Fake AI providers (calls are recorded); `video` is the AI server mode (veo by default)."""
        calls = {"video": [], "image": []}

        async def fake_video(prompt, provider, filepath):
            calls["video"].append((prompt, provider))
            if video_delay:
                await asyncio.sleep(video_delay)
            self.written.append(filepath)
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{uuid.uuid4().hex[:6]}:size=160x120:rate=10:duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", filepath)

        def fake_image(prompt, provider, dest):
            calls["image"].append((prompt, provider))
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{image_colour or uuid.uuid4().hex[:6]}:size=80x120", "-frames:v", "1", dest)
            return 4242

        async def fake_call(adapter, request, settings, dest, progress=None):
            if request.media_type == "video":
                return await fake_video(request.prompt, adapter.name, dest)
            return {"seed": fake_call.image(request.prompt, adapter.name, dest)}
        fake_call.image = fake_image  # a test may swap the image fake (see the fallback-image test)

        # A key must be configured for Veo to count as set up; this one is never sent anywhere
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1" if enabled else "0", "GEMINI_API_KEY": "test-not-a-real-key"}), \
                mock.patch.object(server, "ltx_pipeline", video), \
                mock.patch.object(server.ai_media, "call_provider", side_effect=fake_call), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("a real network call was made")):
            yield calls

    def video(self, user, prompt=PROMPT, status=200, **extra):
        res = self.client.post("/generate-ai-video", json={"prompt": prompt, **extra}, headers=auth(user))
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def image(self, user, prompt, status=200, **extra):
        res = self.client.post("/generate-ai-image", json={"prompt": prompt, **extra}, headers=auth(user))
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def unique(self, text=None):
        """A request of this test's own: words no other test uses, so the visual router's library match
        (which runs before the AI cache) cannot pick another test's asset."""
        words = " ".join(f"q{uuid.uuid4().hex[:7]}" for _ in range(6))
        return f"{text} {words}" if text else words

    def row(self, asset_id):
        with database.SessionLocal() as db:
            return db.get(models.Asset, asset_id)

    def entries(self, asset_id=None):
        with database.SessionLocal() as db:
            query = db.query(models.AIGeneration)
            if asset_id:
                query = query.filter(models.AIGeneration.asset_id == asset_id)
            return query.all()

    def plan(self, user, scenes, **options):
        res = self.client.post("/api/visuals/plan", json={"scenes": scenes, **options}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["plans"]

    # ---- 1-3, 10: miss, hit, no provider call -------------------------------------------

    def test_generate_once_then_reuse(self):
        prompt = self.unique()
        with self.providers() as calls:
            first = self.video("ada", prompt)
            second = self.video("ada", prompt)
            third = self.video("ada", "  " + prompt.replace(" ", "   \r\n ") + "\n")  # same request, other whitespace
        self.assertEqual((first["cache_hit"], first["generated"]), (False, True))
        self.assertEqual((second["cache_hit"], second["generated"], second["cached"]), (True, False, True))
        self.assertEqual({first["asset_id"], second["asset_id"], third["asset_id"]}, {first["asset_id"]})
        self.assertEqual(len(calls["video"]), 1)  # the hits never reached the provider
        self.assertEqual(second["video_url"], first["video_url"])
        asset = self.row(first["asset_id"])
        self.assertEqual((asset.kind, asset.source, asset.status), ("video", "ai-video", "ready"))
        details = json.loads(asset.details)
        self.assertEqual(details["generation"]["provider"], "veo")
        self.assertEqual(details["generation"]["model"], "veo-2.0-generate-001")
        self.assertEqual(details["generation"]["parameters"], {"aspect_ratio": "16:9", "person_generation": "ALLOW_ADULT"})
        self.assertNotIn(os.getenv("GEMINI_API_KEY") or "no-key-configured", asset.details)  # never a secret
        self.assertEqual(len(self.entries(first["asset_id"])), 1)

    # ---- 4-10: different generations never collide ---------------------------------------

    def test_different_requests_are_different_generations(self):
        prompt = self.unique()
        with self.providers() as calls:
            base = self.video("ada", prompt)["asset_id"]
            other_prompt = self.video("ada", prompt + " at night")["asset_id"]
            other_case = self.video("ada", prompt.upper())["asset_id"]  # case can change what a model draws
        with self.providers(video=object()) as ltx:  # a loaded LTX pipeline
            other_provider = self.video("ada", prompt)["asset_id"]
        veo_3 = {"model": "veo-3.0-generate-preview", "parameters": {"aspect_ratio": "16:9", "person_generation": "ALLOW_ADULT"}}
        with self.providers() as more, mock.patch.dict(ai_cache.PROVIDER_SETTINGS, {("video", "veo"): veo_3}):
            other_model = self.video("ada", prompt)["asset_id"]
        portrait = {"model": "veo-2.0-generate-001", "parameters": {"aspect_ratio": "9:16", "person_generation": "ALLOW_ADULT"}}
        with self.providers() as more2, mock.patch.dict(ai_cache.PROVIDER_SETTINGS, {("video", "veo"): portrait}):
            other_aspect = self.video("ada", prompt)["asset_id"]
        with self.providers() as images:
            as_image = self.image("ada", prompt)["asset_id"]
        ids = [base, other_prompt, other_case, other_provider, other_model, other_aspect, as_image]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(len(calls["video"]) + len(ltx["video"]) + len(more["video"]) + len(more2["video"]), 6)
        self.assertEqual(len(images["image"]), 1)
        self.assertEqual(self.row(as_image).kind, "image")

    def test_duration_and_resolution_are_part_of_the_identity(self):
        prompt = self.unique()
        ltx = ai_cache.PROVIDER_SETTINGS[("video", "ltx")]
        longer = {"model": ltx["model"], "parameters": {**ltx["parameters"], "num_frames": 321}}
        sharper = {"model": ltx["model"], "parameters": {**ltx["parameters"], "width": 1920, "height": 1088}}
        with self.providers(video=object()) as calls:
            base = self.video("ada", prompt)["asset_id"]
            with mock.patch.dict(ai_cache.PROVIDER_SETTINGS, {("video", "ltx"): longer}):
                longer_id = self.video("ada", prompt)["asset_id"]
            with mock.patch.dict(ai_cache.PROVIDER_SETTINGS, {("video", "ltx"): sharper}):
                sharper_id = self.video("ada", prompt)["asset_id"]
            again = self.video("ada", prompt)["asset_id"]
        self.assertEqual(len({base, longer_id, sharper_id}), 3)
        self.assertEqual(again, base)
        self.assertEqual(len(calls["video"]), 3)

    def test_canonical_identity(self):
        a = ai_cache.generation_identity("video", "veo", " A bridge,\r\n  at dawn.  ")
        b = ai_cache.generation_identity("video", "veo", "A bridge, at dawn.")
        self.assertEqual(a["prompt"], "A bridge, at dawn.")  # punctuation and case kept
        self.assertEqual(ai_cache.generation_hash(a), ai_cache.generation_hash(b))
        self.assertNotEqual(ai_cache.generation_hash(a), ai_cache.generation_hash(ai_cache.generation_identity("video", "veo", "A bridge at dawn")))
        # Key order never matters; JSON is compact and sorted
        reordered = dict(reversed(list(a.items())))
        self.assertEqual(ai_cache.identity_json(reordered), ai_cache.identity_json(a))
        self.assertTrue(ai_cache.identity_json(a).startswith('{"media_type":"video","model":'))
        self.assertEqual(len(ai_cache.generation_hash(a)), 64)  # SHA-256
        with self.assertRaises(ValueError):
            ai_cache.generation_identity("video", "offline", "x")

    # ---- 11-12: explicit regeneration ------------------------------------------------------

    def test_force_regenerate_makes_a_new_version_and_keeps_the_old_one(self):
        prompt = self.unique()
        with self.providers() as calls:
            old = self.video("ada", prompt)
            old_asset = self.row(old["asset_id"])
            old_path = server.asset_library.file_path(old_asset)
            old_bytes = sha256_of(old_path)
            project = self.client.post("/save-history", json={"subject_name": "Kept", "scenes": [
                {"type": "ai_video", "prompt": prompt, "video_asset_id": old["asset_id"]}]}, headers=auth("ada")).json()["id"]
            new = self.video("ada", prompt, force_regenerate=True)
            after = self.video("ada", prompt)
        self.assertEqual(len(calls["video"]), 2)
        self.assertEqual((new["cache_hit"], new["generated"]), (False, True))
        self.assertNotEqual(new["asset_id"], old["asset_id"])
        # The old asset is untouched and still what the saved lesson uses
        old_asset = self.row(old["asset_id"])
        self.assertEqual(old_asset.status, "ready")
        self.assertEqual(sha256_of(server.asset_library.file_path(old_asset)), old_bytes)
        self.assertNotEqual(self.row(new["asset_id"]).storage_key, old_asset.storage_key)
        used = self.client.get(f"/api/assets/{old['asset_id']}", headers=auth("ada")).json()["used_in"]
        self.assertTrue(any(u["project_id"] == project for u in used))
        # Later requests reuse the newest version
        self.assertEqual((after["asset_id"], after["cache_hit"]), (new["asset_id"], True))
        self.assertTrue(self.entries(new["asset_id"])[0].forced)

    # ---- 13-15: broken entries are misses ---------------------------------------------------

    def test_deleted_failed_and_missing_assets_are_not_reused(self):
        with self.providers() as calls:
            for breakage in ("deleted", "failed", "missing"):
                prompt = self.unique()
                first = self.video("ada", prompt)["asset_id"]
                if breakage == "deleted":
                    self.assertEqual(self.client.delete(f"/api/assets/{first}", headers=auth("ada")).status_code, 200)
                elif breakage == "failed":
                    with database.SessionLocal() as db:
                        db.get(models.Asset, first).status = "failed"
                        db.commit()
                else:
                    os.remove(server.asset_library.file_path(self.row(first)))
                second = self.video("ada", prompt)
                self.assertEqual((second["cache_hit"], second["generated"]), (False, True), breakage)
                self.assertNotEqual(second["asset_id"], first, breakage)
            self.assertEqual(self.row(first).status, "failed")  # the missing file was noticed
        self.assertEqual(len(calls["video"]), 6)

    # ---- 16-17: privacy and sharing -----------------------------------------------------------

    def test_another_users_generation_is_never_reused(self):
        prompt = self.unique()
        with self.providers() as calls:
            mine = self.video("ada", prompt)["asset_id"]
            theirs = self.video("ben", prompt)
        self.assertEqual((theirs["cache_hit"], theirs["generated"]), (False, True))
        self.assertNotEqual(theirs["asset_id"], mine)
        self.assertEqual(len(calls["video"]), 2)
        self.assertEqual(self.client.get(f"/api/assets/{mine}", headers=auth("ben")).status_code, 404)
        plan = self.plan("ben", [{"type": "ai_video", "title": "x", "prompt": prompt}])[0]
        self.assertNotEqual(plan.get("asset_id"), mine)

    def test_shared_generations_are_reused_by_everyone(self):
        prompt = self.unique("Aadhi waving at the class")
        with database.SessionLocal() as db:
            shared = db.query(models.Asset).filter(models.Asset.owner_id.is_(None), models.Asset.kind == "image").first()
            identity = ai_cache.generation_identity("image", "pollinations", prompt)
            server.ai_cache.record(db, "system", shared, identity)
            shared_id = shared.id
        with self.providers() as calls:
            for user in ("ada", "ben"):
                result = self.image(user, prompt)
                self.assertEqual((result["asset_id"], result["cache_hit"]), (shared_id, True))
        self.assertEqual(calls["image"], [])

    # ---- 18-19: the AI kill switch -----------------------------------------------------------

    def test_kill_switch_still_serves_cached_media_and_never_calls_a_provider(self):
        prompt, fresh = self.unique(), self.unique()
        with self.providers() as calls:
            made = self.video("ada", prompt)["asset_id"]
            image = self.image("ada", prompt)["asset_id"]
        with self.providers(enabled=False) as off:
            self.assertEqual(self.video("ada", prompt)["asset_id"], made)
            self.assertEqual(self.image("ada", prompt)["asset_id"], image)
            refused = self.client.post("/generate-ai-video", json={"prompt": fresh}, headers=auth("ada"))
            refused_image = self.client.post("/generate-ai-image", json={"prompt": fresh}, headers=auth("ada"))
            forced = self.client.post("/generate-ai-video", json={"prompt": prompt, "force_regenerate": True}, headers=auth("ada"))
            scenes = [{"type": "ai_video", "title": "x", "prompt": prompt}, {"type": "ai_video", "title": "y", "prompt": fresh}]
            # The AI cache step on its own (library matching off), then with everything on
            cache_only = self.plan("ada", scenes, prefer_existing_assets=False)
            planned = self.plan("ada", scenes)
        self.assertEqual((refused.status_code, refused_image.status_code, forced.status_code), (403, 403, 403))
        self.assertIn("turned off", refused.json()["detail"])
        self.assertEqual(off, {"video": [], "image": []})
        self.assertEqual(len(calls["video"]), 1)
        # The plan reuses the cached video even with AI off, and reports the other as needing AI
        self.assertEqual((cache_only[0]["source"], cache_only[0]["selection"], cache_only[0]["asset_id"], cache_only[0]["cache_hit"]),
                         ("AI_VIDEO", "cached", made, True))
        self.assertIn("/api/assets/", cache_only[0]["url"])
        self.assertEqual((planned[0]["asset_id"], planned[0]["requires_generation"]), (made, False))  # the same asset either way
        for plan in (cache_only[1], planned[1]):
            self.assertEqual((plan["source"], plan["would_require"]), ("NONE", "AI_VIDEO"))

    # ---- 20: one asset, many lessons -----------------------------------------------------------

    def test_one_cached_asset_serves_many_lessons(self):
        prompt = self.unique()
        scene = {"type": "ai_video", "title": "Cable", "prompt": prompt}
        with self.providers() as calls:
            generated = self.video("ada", prompt)["asset_id"]
            projects = []
            for name in ("Lesson A", "Lesson B", "Lesson C"):
                project = self.client.post("/save-history", json={"subject_name": name, "scenes": [dict(scene)]}, headers=auth("ada")).json()["id"]
                plan = self.plan("ada", [dict(scene)], project_id=project)[0]
                # Reused by the cache, or found even earlier by the library match on its prompt
                self.assertEqual((plan["asset_id"], plan["requires_generation"]), (generated, False))
                self.assertIn(plan["selection"], ("cached", "matched"))
                projects.append(project)
        self.assertEqual(len(calls["video"]), 1)
        used = {u["project_id"] for u in self.client.get(f"/api/assets/{generated}", headers=auth("ada")).json()["used_in"]}
        self.assertTrue(set(projects) <= used)
        self.assertEqual(len(self.entries(generated)), 1)  # one cache entry, one file, three lessons
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.Asset).filter(models.Asset.sha256 == self.row(generated).sha256,
                                                           models.Asset.owner_id == self.ids["ada"]).count(), 1)

    # ---- 21: content hash and generation hash --------------------------------------------------

    def test_content_hash_and_generation_hash_stay_separate(self):
        colour = uuid.uuid4().hex[:6]
        a, b = self.unique("a red square"), self.unique("a crimson square")
        with self.providers(image_colour=colour) as calls:  # the provider happens to return the same picture twice
            first = self.image("ada", a)["asset_id"]
            second = self.image("ada", b)["asset_id"]
        self.assertEqual(len(calls["image"]), 2)
        self.assertEqual(first, second)  # same bytes: one asset (content hash, Phase 3)
        asset = self.row(first)
        self.assertEqual(asset.sha256, sha256_of(server.asset_library.file_path(asset)))
        hashes = {e.generation_hash for e in self.entries(first)}
        self.assertEqual(hashes, {ai_cache.generation_hash(ai_cache.generation_identity("image", "pollinations", p)) for p in (a, b)})
        self.assertNotIn(asset.sha256, hashes)

    # ---- 22: identical requests at the same time -------------------------------------------------

    def test_concurrent_identical_requests_generate_once(self):
        prompt = self.unique()
        with self.providers(video_delay=1.0) as calls:
            waited_before = server.ai_cache.stats["waited"]
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: self.client.post("/generate-ai-video", json={"prompt": prompt},
                                                                   headers=auth("ada")).json(), range(3)))
        self.assertEqual(len(calls["video"]), 1)
        self.assertEqual(len({r["asset_id"] for r in results}), 1)
        self.assertEqual(sorted(r["cache_hit"] for r in results), [False, True, True])
        self.assertGreaterEqual(server.ai_cache.stats["waited"] - waited_before, 1)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.AIGenerationLock).count(), 0)  # every lock released

    def test_an_abandoned_lock_does_not_block_forever(self):
        prompt = self.unique()
        identity = ai_cache.generation_identity("video", "veo", prompt)
        key = f"user:{self.ids['ada']}:{ai_cache.generation_hash(identity)}"
        with database.SessionLocal() as db:
            db.add(models.AIGenerationLock(key=key, created_at=ai_cache.datetime.datetime.utcnow() - ai_cache.LOCK_STALE * 2))
            db.commit()
        with self.providers() as calls:
            self.assertTrue(self.video("ada", prompt)["generated"])
        self.assertEqual(len(calls["video"]), 1)

    # ---- 23-24: preview and export share one generation -------------------------------------------

    def test_preview_then_export_and_export_then_preview_reuse_one_asset(self):
        for order in ("preview first", "export first"):
            prompt = self.unique()
            scene = {"type": "ai_video", "title": "Cable", "prompt": prompt}
            with self.providers() as calls:
                if order == "preview first":
                    made = self.video("ada", prompt)["asset_id"]               # the user clicks Generate in the preview
                    for options in ({}, {"prefer_existing_assets": False}):   # export preparation plans the lesson
                        export_plan = self.plan("ada", [dict(scene)], **options)[0]
                        self.assertEqual((export_plan["asset_id"], export_plan["requires_generation"]), (made, False), order)
                    self.assertEqual(self.video("ada", prompt)["asset_id"], made)  # an export that asks again still reuses it
                else:
                    before = self.plan("ada", [dict(scene)])[0]
                    self.assertTrue(before["requires_generation"])
                    made = self.video("ada", prompt)["asset_id"]               # export preparation generates ("all" mode)
                    for options in ({}, {"prefer_existing_assets": False}):   # the preview later
                        preview_plan = self.plan("ada", [dict(scene)], **options)[0]
                        self.assertEqual((preview_plan["asset_id"], preview_plan["requires_generation"]), (made, False), order)
                    self.assertEqual(preview_plan["selection"], "cached")
            self.assertEqual(len(calls["video"]), 1, order)

    # ---- images, manual workflow, response shape, performance ---------------------------------------

    def test_ai_images_are_library_assets_with_signed_links(self):
        prompt = self.unique("a colourful coral reef")
        with self.providers() as calls:
            first = self.image("ada", prompt, subject_name="Biology")
            second = self.image("ada", prompt)
        self.assertEqual((first["cache_hit"], first["generated"], second["cache_hit"]), (False, True, True))
        self.assertEqual(len(calls["image"]), 1)
        asset = self.row(first["asset_id"])
        self.assertEqual((asset.kind, asset.source, asset.storage_volume), ("image", "ai-image", "assets"))
        self.assertEqual(json.loads(asset.details)["generation"]["seed"], 4242)
        self.assertRegex(first["url"], rf"^/api/assets/{asset.id}/content\?token=")
        self.assertEqual(self.client.get(first["url"]).status_code, 200)
        self.assertEqual(self.client.post("/generate-ai-image", json={"prompt": prompt}).status_code, 401)
        listed = self.client.get("/api/assets?source=ai-image&limit=200", headers=auth("ada")).json()["assets"]
        self.assertIn(asset.id, [a["id"] for a in listed])  # visible in the Asset Library

    def test_a_failed_image_falls_back_without_caching_it_as_the_request(self):
        prompt = self.unique("a diagram of a rare mineral")
        with self.providers() as calls:
            fake_call = server.ai_media.call_provider.side_effect
            real_fake = fake_call.image

            def failing_first(p, provider, dest):
                if p == ai_cache.normalize_text(prompt):
                    raise OSError("provider unavailable")
                return real_fake(p, provider, dest)

            fake_call.image = failing_first
            fallback = self.image("ada", prompt, subject_name="Geology")
            fake_call.image = real_fake
            retried = self.image("ada", prompt, subject_name="Geology")
        self.assertTrue(fallback["fallback"])
        self.assertEqual((retried["cache_hit"], retried["generated"]), (False, True))  # the real request is tried again
        self.assertNotEqual(retried["asset_id"], fallback["asset_id"])

    def test_manual_workflow_names_a_file_per_user_and_then_reuses_it(self):
        prompt = self.unique()
        with self.providers(video="MANUAL") as calls:
            asked = self.video("ada", prompt)
            other = self.video("ben", prompt)
            self.assertEqual(asked["status"], "manual_required")
            self.assertNotEqual(asked["filename"], other["filename"])  # nobody picks up another user's file
            path = os.path.join(server.STATIC_DIR, asked["filename"])
            self.written.append(path)
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
            placed = self.video("ada", prompt)
            again = self.video("ada", prompt)
            self.assertEqual(self.video("ben", prompt)["status"], "manual_required")
            self.assertEqual(self.client.post("/generate-ai-video", json={"prompt": prompt, "force_regenerate": True},
                                              headers=auth("ada")).status_code, 400)
        self.assertEqual((placed["status"], placed["cache_hit"], placed["generated"]), ("success", False, False))
        self.assertEqual((again["asset_id"], again["cache_hit"]), (placed["asset_id"], True))
        self.assertEqual(calls["video"], [])

    def test_requests_are_validated_and_the_stats_count_hits(self):
        self.assertEqual(self.client.post("/generate-ai-video", json={"prompt": "  "}, headers=auth("ada")).status_code, 400)
        self.assertEqual(self.client.post("/generate-ai-video", json={"prompt": "x"}).status_code, 401)
        prompt = self.unique()
        before = self.client.get("/api/ai-cache/stats", headers=auth("ada")).json()
        with self.providers():
            self.video("ada", prompt)
            self.video("ada", prompt)
        after = self.client.get("/api/ai-cache/stats", headers=auth("ada")).json()
        self.assertEqual(after["generated"] - before["generated"], 1)
        self.assertEqual(after["hits"] - before["hits"], 1)
        self.assertEqual(after["misses"] - before["misses"], 1)
        self.assertNotIn(prompt, json.dumps(after))  # counts only
        self.assertEqual(self.client.get("/api/ai-cache/stats").status_code, 401)

    def test_cache_lookup_is_one_indexed_query(self):
        with database.SessionLocal() as db:
            base = db.query(models.Asset).filter(models.Asset.owner_id.is_(None), models.Asset.kind == "image").first()
            for i in range(500):  # other users' entries
                db.add(models.AIGeneration(generation_hash=hashlib.sha256(f"noise{i}".encode()).hexdigest(), scope_key=f"user:{900000 + i}",
                                           asset_id=base.id, media_type="image", provider="pollinations", identity="{}"))
            db.commit()
        prompts = [self.unique() for _ in range(40)]
        queries = []
        listener = lambda *args: queries.append(1)  # noqa: E731
        event.listen(database.engine, "before_cursor_execute", listener)
        try:
            with database.SessionLocal() as db:
                start = time.perf_counter()
                server.ai_cache.lookup_many(db, self.ids["cyd"], {p: ai_cache.generation_identity("video", "veo", p) for p in prompts})
                elapsed = time.perf_counter() - start
        finally:
            event.remove(database.engine, "before_cursor_execute", listener)
        self.assertEqual(len(queries), 1)
        self.assertLess(elapsed, 0.5)
        with database.SessionLocal() as db:
            index_names = {row[1] for row in db.execute(text("PRAGMA index_list('ai_generations')")).fetchall()}
        self.assertIn("ix_ai_generation_lookup", index_names)


if __name__ == "__main__":
    unittest.main()
