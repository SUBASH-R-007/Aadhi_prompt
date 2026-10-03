"""Backend tests for the AI media layer (ai_media.py, Phase 8) through the API: provider choice, the
Phase 5 cache with several providers, fallback, retries, output validation, provenance, background
generations, the router and Visual Review.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
No real provider exists here: the layer runs with the two local stand-ins ("fake" preferred, "fake-alt"
the backup; ffmpeg-made clips and pictures), their failures are switched on per test (AI_FAKE_FAIL), and
any outside network connection fails the test.
"""
import io
import json
import os
import socket
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from unittest import mock

from backend_env import FFMPEG, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_cache  # noqa: E402
import ai_providers  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
from ai_media import AIMediaService  # noqa: E402

_real_connect = socket.socket.connect


def _guarded_connect(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if host in ("127.0.0.1", "::1"):  # the event loop's own socket pair on Windows
        return _real_connect(sock, address)
    raise AssertionError(f"a real network connection was attempted ({host})")


def words():
    return " ".join(f"m{uuid.uuid4().hex[:7]}" for _ in range(6))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class AIMediaLayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.client.__enter__()  # one event loop for all requests, so background generations keep running
        cls.ids = {name: ensure_user(name) for name in ("mia", "noa")}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        with database.SessionLocal() as db:  # the generated clips of this throwaway database
            for asset in db.query(models.Asset).filter(models.Asset.storage_volume == "static",
                                                       models.Asset.storage_key.like("ai_video_%")):
                path = os.path.join(server.STATIC_DIR, asset.storage_key)
                if os.path.exists(path):
                    os.remove(path)

    # ---- the layer with stand-in providers --------------------------------------------------------

    @contextmanager
    def layer(self, enabled=True, **env):
        """The AI media layer with only the local stand-ins; `env` configures them (AI_FAKE_FAIL, FAKE_ENABLED, ...)."""
        settings = {"FAKE_POLL_SECONDS": "0.01", "FAKE_ALT_POLL_SECONDS": "0.01", "FAKE_TIMEOUT": "5", "FAKE_ALT_TIMEOUT": "5", **env}
        registry = ai_providers.ProviderRegistry(env=settings, video_mode=lambda: "fake", fake=True)
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1" if enabled else "0", "AI_PROVIDER_RETRY_DELAY": "0.01",
                                          "AI_PROVIDER_MAX_RETRY_WAIT": "5"}), \
                mock.patch.object(server.ai_media, "registry", registry), mock.patch.object(server, "ai_registry", registry), \
                mock.patch.object(server.ai_media, "log_enabled", False), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("a real network call was made")), \
                mock.patch.object(socket.socket, "connect", _guarded_connect):
            yield registry

    def video(self, user, prompt, status=200, **extra):
        res = self.client.post("/generate-ai-video", json={"prompt": prompt, **extra}, headers=auth(user))
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def image(self, user, prompt, status=200, **extra):
        res = self.client.post("/generate-ai-image", json={"prompt": prompt, **extra}, headers=auth(user))
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def made(self, asset_id):
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, asset_id)
            return json.loads(asset.details)["generation"], asset

    def entries(self, asset_id):
        with database.SessionLocal() as db:
            return [(e.provider, e.model, e.generation_hash) for e in
                    db.query(models.AIGeneration).filter(models.AIGeneration.asset_id == asset_id)]

    def run_row(self, run_id):
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            return run, json.loads(run.detail or "{}")

    def calls(self):
        return dict(server.ai_media.calls_by_provider)

    def delta(self, before, name):
        return self.calls().get(name, 0) - before.get(name, 0)

    def counts(self):
        with database.SessionLocal() as db:
            return db.query(models.Asset).count(), db.query(models.AIGeneration).count()

    def stray_files(self):
        return {f for f in os.listdir(server.STATIC_DIR) if f.startswith("ai_video_")}

    # ---- cache test matrix ------------------------------------------------------------------------

    def test_existing_cache_identities_are_unchanged(self):
        """Default requests have exactly the Phase 5 identities, so media cached before Phase 8 is still reused."""
        registry = ai_providers.ProviderRegistry(env={"GEMINI_API_KEY": "k"}, video_mode=lambda: "veo", ltx_pipeline=lambda: object())
        prompt = words()
        for media_type, name in (("video", "veo"), ("video", "ltx"), ("image", "pollinations")):
            candidate = ai_providers.Candidate(name, True, provider=registry.get(name))
            identity = AIMediaService.identity(candidate, ai_providers.MediaRequest(media_type=media_type, prompt=prompt))[0]
            self.assertEqual(identity, ai_cache.generation_identity(media_type, name, prompt))

    def test_same_provider_model_and_request_is_a_cache_hit(self):
        prompt = words()
        with self.layer():
            before = self.calls()
            first = self.video("mia", prompt)
            second = self.video("mia", prompt)
        self.assertEqual((first["generated"], first["provider"], first["model"]), (True, "fake", "fake-video-1"))
        self.assertNotIn("fallback_from", first)
        self.assertEqual((second["cache_hit"], second["asset_id"], second["provider"]), (True, first["asset_id"], "fake"))
        self.assertEqual(self.delta(before, "fake"), 1)
        made, asset = self.made(first["asset_id"])
        self.assertEqual((made["provider"], made["model"], made["run_id"], made["attempts"]), ("fake", "fake-video-1", first["run_id"], 1))
        self.assertIsInstance(made["generation_ms"], int)
        self.assertEqual((asset.kind, asset.source, asset.status), ("video", "ai-video", "ready"))
        run, detail = self.run_row(first["run_id"])
        self.assertEqual((run.status, run.provider, run.asset_id, run.generation_hash), ("completed", "fake", first["asset_id"], made["hash"]))
        self.assertTrue(run.provider_job_id.startswith("fake-"))  # the provider's job, polled to completion
        self.assertEqual(detail["selection"][0]["provider"], "fake")

    def test_provider_model_and_parameters_are_part_of_the_identity(self):
        prompt = words()
        with self.layer():
            before = self.calls()
            base = self.video("mia", prompt)["asset_id"]
            other_provider = self.video("mia", prompt, provider="fake-alt")["asset_id"]
            other_model = self.video("mia", prompt, model="fake-video-2")["asset_id"]
            portrait = self.video("mia", prompt, aspect_ratio="9:16")["asset_id"]
            longer = self.video("mia", prompt, duration_seconds=6)["asset_id"]
            again = [self.video("mia", prompt, **extra)["asset_id"] for extra in
                     ({}, {"provider": "fake-alt"}, {"model": "fake-video-2"}, {"aspect_ratio": "9:16"}, {"duration_seconds": 6})]
        ids = [base, other_provider, other_model, portrait, longer]
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(again, ids)  # each is reused for exactly its own request
        self.assertEqual((self.delta(before, "fake"), self.delta(before, "fake-alt")), (4, 1))
        self.assertEqual(self.entries(other_provider)[0][:2], ("fake-alt", "fake-alt-video-1"))
        self.assertEqual(self.entries(other_model)[0][:2], ("fake", "fake-video-2"))
        made, asset = self.made(portrait)
        self.assertEqual((made["parameters"]["width"] < made["parameters"]["height"], asset.width < asset.height), (True, True))
        self.assertEqual(self.made(longer)[0]["parameters"]["seconds"], 6)
        self.assertAlmostEqual(self.made(longer)[1].duration_seconds, 6, delta=0.2)

    def test_concurrent_identical_requests_generate_once(self):
        prompt = words()
        with self.layer():
            before = self.calls()
            with ThreadPoolExecutor(max_workers=3) as pool:
                answers = list(pool.map(lambda _: self.video("mia", prompt), range(3)))
        self.assertEqual(len({a["asset_id"] for a in answers}), 1)
        self.assertEqual(self.delta(before, "fake"), 1)

    def test_force_regenerate_makes_a_new_version_with_the_provider_policy(self):
        prompt = words()
        with self.layer():
            old = self.video("mia", prompt)
            fresh = self.video("mia", prompt, force_regenerate=True)
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable"):
            backup = self.video("mia", prompt, force_regenerate=True)
        self.assertEqual(len({old["asset_id"], fresh["asset_id"], backup["asset_id"]}), 3)
        self.assertEqual((backup["provider"], backup["fallback_from"]), ("fake-alt", "fake"))
        for answer in (old, fresh, backup):  # every version kept, file and all
            self.assertTrue(os.path.exists(os.path.join(server.STATIC_DIR, self.made(answer["asset_id"])[1].storage_key)))
        with database.SessionLocal() as db:
            forced = {e.asset_id: e.forced for e in db.query(models.AIGeneration).filter(
                models.AIGeneration.asset_id.in_([old["asset_id"], fresh["asset_id"], backup["asset_id"]]))}
        self.assertEqual(forced, {old["asset_id"]: False, fresh["asset_id"]: True, backup["asset_id"]: True})

    def test_a_failed_provider_falls_back_and_provenance_names_the_real_one(self):
        prompt = words()
        stats = dict(server.ai_media.stats)
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable") as registry:
            before = self.calls()
            made = self.video("mia", prompt)
            self.assertEqual(registry.state("fake")[0], "temporarily_unavailable")
            again = self.video("mia", prompt)  # the backup's result is reused, not made twice
        self.assertEqual((made["provider"], made["fallback_from"], made["requested_provider"], made["generated"]),
                         ("fake-alt", "fake", "fake", True))
        generation, _asset = self.made(made["asset_id"])
        self.assertEqual((generation["provider"], generation["model"], generation["fallback_from"], generation["requested_provider"]),
                         ("fake-alt", "fake-alt-video-1", "fake", "fake"))
        entry = self.entries(made["asset_id"])
        self.assertEqual(entry[0][:2], ("fake-alt", "fake-alt-video-1"))  # cached under the identity of who made it
        self.assertEqual(entry[0][2], ai_cache.generation_hash(ai_cache.generation_identity("video", "fake-alt", prompt)))
        self.assertEqual((again["cache_hit"], again["asset_id"], again["provider"], again["fallback_from"]),
                         (True, made["asset_id"], "fake-alt", "fake"))
        self.assertEqual((self.delta(before, "fake"), self.delta(before, "fake-alt")), (2, 1))  # one retry of fake, one backup
        run, detail = self.run_row(made["run_id"])
        self.assertEqual([(a["provider"], a["category"]) for a in detail["attempts"]],
                         [("fake", "unavailable"), ("fake", "unavailable"), ("fake-alt", None)])
        self.assertEqual((run.attempts, run.fallback_from, run.status), (3, "fake", "completed"))
        self.assertEqual(server.ai_media.stats["retries"] - stats["retries"], 1)
        self.assertEqual(server.ai_media.stats["fallbacks"] - stats["fallbacks"], 1)

    def test_an_earlier_backup_result_is_reused_when_the_preferred_provider_fails(self):
        prompt = words()
        with self.layer():
            backup = self.video("mia", prompt, provider="fake-alt")
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable"):
            before = self.calls()
            answer = self.video("mia", prompt)
        self.assertEqual((answer["cache_hit"], answer["asset_id"], answer["fallback_from"]), (True, backup["asset_id"], "fake"))
        self.assertEqual((self.delta(before, "fake"), self.delta(before, "fake-alt")), (2, 0))

    def test_a_healthy_preferred_provider_is_not_replaced_by_a_backup_result(self):
        prompt = words()
        with self.layer():
            backup = self.video("mia", prompt, provider="fake-alt")["asset_id"]
            before = self.calls()
            preferred = self.video("mia", prompt)
        self.assertNotEqual(preferred["asset_id"], backup)
        self.assertEqual((preferred["provider"], self.delta(before, "fake")), ("fake", 1))

    def test_with_ai_generation_off_any_earlier_result_still_serves(self):
        prompt = words()
        with self.layer():
            backup = self.video("mia", prompt, provider="fake-alt")["asset_id"]
        with self.layer(enabled=False):
            before = self.calls()
            served = self.video("mia", prompt)
            self.video("mia", words(), status=403)
        self.assertEqual((served["cache_hit"], served["asset_id"]), (True, backup))
        self.assertEqual(self.calls(), before)

    # ---- fallback test matrix -----------------------------------------------------------------------

    def test_an_unavailable_or_unsupported_preferred_provider_is_skipped(self):
        with self.layer(FAKE_ENABLED="0"):
            before = self.calls()
            skipped = self.video("mia", words())
        self.assertEqual((skipped["provider"], skipped["fallback_from"], self.delta(before, "fake")), ("fake-alt", "fake", 0))
        with self.layer() as registry:
            images_only = ai_providers.Capabilities(media_types=("image",), execution="sync")
            with mock.patch.object(registry.get("fake"), "capabilities", return_value=images_only):
                before = self.calls()
                unsupported = self.video("mia", words())
        self.assertEqual((unsupported["provider"], self.delta(before, "fake")), ("fake-alt", 0))

    def test_timeouts_are_retried_within_bounds_then_the_backup_is_used(self):
        with self.layer(AI_FAKE_FAIL="fake:video:timeout"):
            answer = self.video("mia", words())
        _run, detail = self.run_row(answer["run_id"])
        self.assertEqual([(a["provider"], a["category"]) for a in detail["attempts"]],
                         [("fake", "timeout"), ("fake", "timeout"), ("fake-alt", None)])
        with self.layer(AI_FAKE_FAIL="fake:video:slow", FAKE_TIMEOUT="0.3") as registry:
            slow = self.video("mia", words())
            cancelled = getattr(registry.get("fake"), "cancelled", 0)
        _run, detail = self.run_row(slow["run_id"])
        # A provider job that ran out of time is not submitted again (it may still be billed): straight to the backup
        self.assertEqual([(a["provider"], a["category"]) for a in detail["attempts"]], [("fake", "timeout"), ("fake-alt", None)])
        self.assertEqual((slow["provider"], cancelled), ("fake-alt", 1))

    def test_rate_limits_wait_then_move_on(self):
        with self.layer(AI_FAKE_FAIL="fake:video:rate_limited"):
            started = time.time()
            answer = self.video("mia", words())
        self.assertGreaterEqual(time.time() - started, 0.2)  # the provider's Retry-After (0.2 s) was honoured
        self.assertEqual(answer["provider"], "fake-alt")
        with self.layer(AI_FAKE_FAIL="fake:video:rate_limited,fake-alt:video:rate_limited"):
            res = self.client.post("/generate-ai-video", json={"prompt": words()}, headers=auth("mia"))
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.headers["Retry-After"], "1")
        self.assertIn("busy", res.json()["detail"])

    def test_a_refused_request_is_neither_retried_nor_sent_elsewhere(self):
        counts, files = self.counts(), self.stray_files()
        with self.layer(AI_FAKE_FAIL="fake:video:rejected"):
            before = self.calls()
            refused = self.client.post("/generate-ai-video", json={"prompt": words()}, headers=auth("mia"))
        self.assertEqual(refused.status_code, 422)
        self.assertIn("refused", refused.json()["detail"])
        self.assertEqual((self.delta(before, "fake"), self.delta(before, "fake-alt")), (1, 0))
        self.assertEqual((self.counts(), self.stray_files()), (counts, files))

    def test_when_every_provider_fails_nothing_is_left_behind(self):
        counts, files = self.counts(), self.stray_files()
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable,fake-alt:video:unavailable"):
            res = self.client.post("/generate-ai-video", json={"prompt": words()}, headers=auth("mia"))
            runs = self.client.get("/api/ai-media/runs?limit=1", headers=auth("mia")).json()["runs"]
        self.assertEqual(res.status_code, 502)
        self.assertIn("fake: ", res.json()["detail"])
        self.assertIn("fake-alt: ", res.json()["detail"])
        self.assertEqual((self.counts(), self.stray_files()), (counts, files))  # no asset, no cache entry, no file
        self.assertEqual((runs[0]["status"], runs[0]["error"]["category"], runs[0]["error"]["http_status"]), ("failed", "unavailable", 502))
        self.assertEqual([t["provider"] for t in runs[0]["tried"]], ["fake", "fake", "fake-alt", "fake-alt"])

    def test_unusable_output_is_rejected_and_blank_output_is_flagged(self):
        stats = dict(server.ai_media.stats)
        with self.layer(AI_FAKE_FAIL="fake:video:invalid_output,fake:image:blank"):
            video = self.video("mia", words())
            picture = self.image("mia", words())
        self.assertEqual((video["provider"], video["fallback_from"]), ("fake-alt", "fake"))
        self.assertEqual(server.ai_media.stats["invalid_outputs"] - stats["invalid_outputs"], 1)
        self.assertEqual((picture["provider"], picture["warnings"]), ("fake", ["looks blank (one flat colour)"]))
        self.assertEqual(self.made(picture["asset_id"])[0]["warnings"], ["looks blank (one flat colour)"])

    def test_explicit_provider_choices(self):
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable"):
            before = self.calls()
            self.video("mia", words(), status=502, provider="fake")  # not silently replaced
            self.assertEqual(self.delta(before, "fake-alt"), 0)
            allowed = self.video("mia", words(), provider="fake", allow_fallback=True)
            self.assertEqual((allowed["provider"], allowed["fallback_from"]), ("fake-alt", "fake"))
            self.assertIn("no AI provider called", self.video("mia", words(), status=400, provider="nope")["detail"])
            self.assertIn("cannot make", self.video("mia", words(), status=422, provider="fake-alt", aspect_ratio="4:5")["detail"])
            self.video("mia", words(), status=422, provider="Bad Name!")
            self.video("mia", words(), status=422, aspect_ratio="wide")

    # ---- background generations, status, runs --------------------------------------------------------

    def follow(self, run_id, user="mia", timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth(user)).json()
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(0.1)
        self.fail("the background generation did not finish")

    def test_background_generation_is_followed_through_its_run(self):
        prompt = words()
        with self.layer():
            started = self.client.post("/generate-ai-video", json={"prompt": prompt, "wait": False}, headers=auth("mia"))
            self.assertEqual(started.status_code, 202, started.text)
            run_id = started.json()["run_id"]
            self.assertEqual(self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth("noa")).status_code, 404)
            done = self.follow(run_id)
            cached = self.client.post("/generate-ai-video", json={"prompt": prompt, "wait": False}, headers=auth("mia"))
            listed = self.client.get("/api/ai-media/runs", headers=auth("mia")).json()["runs"]
        result = done["result"]
        self.assertEqual((done["status"], done["provider"], result["status"], result["generated"]), ("completed", "fake", "success", True))
        self.assertTrue(result["video_url"].startswith("/static/ai_video_"))
        self.assertEqual((cached.status_code, cached.json()["cache_hit"], cached.json()["asset_id"]), (200, True, result["asset_id"]))
        self.assertIn(run_id, [r["run_id"] for r in listed])
        self.assertTrue(done["background"])
        self.assertIsInstance(done["generation_ms"], int)

    def test_background_generation_can_be_cancelled(self):
        with self.layer(AI_FAKE_FAIL="fake:video:slow", FAKE_TIMEOUT="60") as registry:
            run_id = self.client.post("/generate-ai-video", json={"prompt": words(), "wait": False}, headers=auth("mia")).json()["run_id"]
            time.sleep(0.3)
            cancel = self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("mia"))
            done = self.follow(run_id)
            again = self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("mia"))
            cancelled = getattr(registry.get("fake"), "cancelled", 0)
        self.assertEqual(cancel.status_code, 202)
        self.assertEqual((done["status"], done["error"]["category"]), ("cancelled", "cancelled"))
        self.assertEqual(cancelled, 1)  # the provider job was cancelled too
        self.assertEqual(again.status_code, 409)

    def test_an_interrupted_run_is_reported_not_left_running(self):
        import datetime
        run_id = uuid.uuid4().hex
        with database.SessionLocal() as db:
            old = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
            db.add(models.AIGenerationRun(id=run_id, scope_key=f"user:{self.ids['mia']}", user_id=self.ids["mia"], media_type="video",
                                          status="running", created_at=old, started_at=old, heartbeat_at=old, instance="gone"))
            db.commit()
        run = self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth("mia")).json()
        self.assertEqual((run["status"], run["error"]["category"], run["error"]["http_status"]), ("failed", "interrupted", 503))

    def test_provider_status_is_safe(self):
        secret = "AIzaSyTHISMUSTNEVERLEAK0000000000000"
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": secret, "GEMINI_IMAGE_ENABLED": "1"}), \
                mock.patch.object(socket.socket, "connect", _guarded_connect):
            res = self.client.get("/api/ai-media/providers", headers=auth("mia"))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(secret, res.text)
        states = {p["name"]: p["state"] for p in res.json()["providers"]}
        self.assertEqual((states["gemini-image"], states["veo"]), ("available", "disabled"))
        self.assertEqual(self.client.get("/api/ai-media/providers").status_code, 401)
        with self.layer(FAKE_ENABLED="0"):
            body = self.client.get("/api/ai-media/providers", headers=auth("mia")).json()
        self.assertEqual({p["name"]: p["state"] for p in body["providers"]}, {"fake": "disabled", "fake-alt": "available"})
        self.assertEqual(body["order"], {"image": ["fake", "fake-alt"], "video": ["fake", "fake-alt"]})

    def test_errors_and_logs_never_contain_secrets_or_prompts(self):
        prompt = "a private lesson prompt " + words()
        leak = "https://api.example/v1?key=AIzaSyLEAKLEAKLEAKLEAK000000000000&sig=abc"

        async def failing(adapter, request, settings, dest, progress=None):
            raise RuntimeError(f"upstream said {leak}")
        out = io.StringIO()
        with self.layer(), mock.patch.object(server.ai_media, "call_provider", side_effect=failing), \
                mock.patch.object(server.ai_media, "log_enabled", True), redirect_stdout(out):
            res = self.client.post("/generate-ai-video", json={"prompt": prompt}, headers=auth("mia"))
        self.assertEqual(res.status_code, 502)
        for text in (res.text, out.getvalue()):
            self.assertNotIn("AIzaSyLEAK", text)
            self.assertNotIn("sig=abc", text)
        self.assertNotIn(prompt, out.getvalue())
        self.assertIn('"event": "provider_failed"', out.getvalue())

    # ---- the router and Visual Review -------------------------------------------------------------------

    def plan(self, user, scenes, **options):
        res = self.client.post("/api/visuals/plan", json={"scenes": scenes, "prefer_existing_assets": False, **options}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["plans"]

    def test_the_router_plans_through_the_media_layer(self):
        topic = words()
        scene = {"type": "ai_video", "title": topic, "prompt": topic}
        with self.layer():
            planned = self.plan("mia", [scene])[0]
        self.assertEqual((planned["provider"], planned["model"], planned["requires_generation"]), ("fake", "fake-video-1", True))
        with self.layer(FAKE_ENABLED="0"):
            backup = self.plan("mia", [scene])[0]
            made = self.video("mia", topic)
            cached = self.plan("mia", [scene])[0]
        self.assertEqual((backup["provider"], backup["fallback_from"]), ("fake-alt", "fake"))
        self.assertIn("fake cannot be used now", backup["reason"])
        self.assertEqual((cached["selection"], cached["asset_id"], cached["provider"], cached["model"], cached["fallback_from"]),
                         ("cached", made["asset_id"], "fake-alt", "fake-alt-video-1", "fake"))
        with self.layer(FAKE_ENABLED="0", FAKE_ALT_ENABLED="0"):
            nothing = self.plan("mia", [{"type": "ai_video", "title": "t", "prompt": words()}])[0]
        self.assertEqual((nothing["source"], nothing["would_require"]), ("NONE", "AI_VIDEO"))
        self.assertIn("no AI video provider can be used now", nothing["reason"])
        side = {"type": "content", "title": "x", "side_panel": {"type": "image", "prompt": words()}}
        with self.layer(AI_FAKE_FAIL="fake:image:blank"):
            self.image("mia", side["side_panel"]["prompt"])
            flagged = next(p for p in self.plan("mia", [side]) if p["slot"] == "side")
        self.assertEqual((flagged["selection"], flagged["quality_warnings"]), ("cached", ["looks blank (one flat colour)"]))

    def test_visual_review_new_versions_follow_the_provider_policy(self):
        topic = words()
        scenes = [{"type": "ai_video", "title": topic, "prompt": topic}]
        project = self.client.post("/save-history", json={"subject_name": "Phase 8 review", "scenes": scenes}, headers=auth("mia")).json()["id"]
        with self.layer():
            first = self.video("mia", topic)
            kept = self.client.post("/api/visuals/review", headers=auth("mia"), json={
                "project_id": project, "scene_index": 0, "slot": "main", "action": "keep", "asset_id": first["asset_id"]}).json()
        with self.layer(AI_FAKE_FAIL="fake:video:unavailable"):
            newer = self.video("mia", topic, force_regenerate=True)
            chosen = self.client.post("/api/visuals/review", headers=auth("mia"), json={
                "project_id": project, "scene_index": 0, "slot": "main", "action": "choose", "asset_id": newer["asset_id"]}).json()
        self.assertEqual((kept["plans"]["main"]["provider"], kept["plans"]["main"]["review_status"]), ("fake", "approved"))
        main = chosen["plans"]["main"]
        self.assertEqual((main["asset_id"], main["source"], main["provider"], main["fallback_from"], main["review_status"]),
                         (newer["asset_id"], "AI_VIDEO", "fake-alt", "fake", "changed"))
        self.assertTrue(os.path.exists(os.path.join(server.STATIC_DIR, self.made(first["asset_id"])[1].storage_key)))  # old version kept


if __name__ == "__main__":
    unittest.main()
