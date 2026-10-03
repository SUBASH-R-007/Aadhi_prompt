"""Backend tests of sandboxed Manim renders as durable jobs (Phase 10): the Phase 9 run / attempt / lease model,
recovery at every checkpoint, the cache (and the old pipeline's files), Visual Review, concurrency, output
checks, the API and the schema step.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_manim_jobs.py" -v

The orchestration tests use a stand-in sandbox (it "renders" by copying a prepared video) so crashes can be
placed exactly and repeated quickly; what the real sandbox refuses is tested in test_manim_sandbox.py, and one
test here renders for real through the whole path. A crash is an exception nothing catches (no tidying up);
the lease is then expired and a *new* service and recovery manager continue from the database alone.
"""
import asyncio
import datetime
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

import ai_providers  # noqa: E402
import ai_runs  # noqa: E402
import database  # noqa: E402
import manim_security as security  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import visuals  # noqa: E402
from ai_media import AIMediaService  # noqa: E402
from ai_recovery import RecoveryManager  # noqa: E402
from manim_jobs import ManimFailed, ManimService  # noqa: E402
from manim_sandbox import ManimSandbox, SandboxResult, SandboxUnavailable, Workspace, sweep_orphans  # noqa: E402


class Crash(BaseException):
    """The simulated power cut: nothing in the worker catches it."""


def code(tag=None, name="Lesson"):
    tag = tag or uuid.uuid4().hex[:8]
    return f'from manim import *\n\nclass {name}(Scene):\n    def construct(self):\n        self.play(Write(Text("{tag}")))\n'


class FakeRuntime:
    name = "stand-in"


class FakeSandbox:
    """Renders by copying a prepared video of the profile's size into the workspace (optionally waiting on a gate)."""

    def __init__(self, fixtures, root):
        self.fixtures = fixtures
        self.root = root
        self.calls = 0
        self.gate = None
        self.saw_cancel = False
        self.running = 0
        self.max_running = 0
        self.unavailable = False
        self.produce = None  # (lim) -> path of the "rendered" file, to return a wrong one
        self.lock = threading.Lock()

    def runtime(self):
        if self.unavailable:
            raise SandboxUnavailable("no secure runtime in this test")
        return FakeRuntime()

    def prepare(self, job_id, run_id, attempt_id, code, scene, lim, audit_hook=True):
        ws = Workspace(self.root, job_id).create(run_id, attempt_id)
        with open(ws.file("source", "scene.py"), "w", encoding="utf-8") as f:
            f.write(code)
        return ws, {"scene": scene}

    def execute(self, ws, job, lim, cancelled=lambda: False, on_start=None):
        with self.lock:
            self.calls += 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if on_start:
                on_start({"runtime": "stand-in"})
            while self.gate is not None and not self.gate.wait(0.05):
                if cancelled():
                    self.saw_cancel = True
                    return SandboxResult(False, "cancelled", "Rendering was cancelled.", metrics={"seconds": 0})
            source = self.produce(lim) if self.produce else self.fixtures[(lim["width"], lim["height"])]
            out = ws.file("output", "scene.mp4")
            shutil.copyfile(source, out)
            return SandboxResult(True, output=out, metrics={"seconds": 0.1, "cpu_seconds": 0.1, "peak_memory_mb": 50,
                                                            "processes_left": 0})
        finally:
            with self.lock:
                self.running -= 1

    def sweep(self, is_active, older_than=600):
        return sweep_orphans(self.root, is_active, older_than)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ManimJobsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.uid = ensure_user("manny")
        cls.other = ensure_user("mona")
        cls.fixtures = {}
        folder = tempfile.mkdtemp(prefix="aadhi-manim-fixtures-", dir=TMP)
        for name in security.PROFILES:
            lim = security.limits(name)
            path = os.path.join(folder, f"{name}.mp4")
            ffmpeg("-f", "lavfi", "-i", f"testsrc=size={lim['width']}x{lim['height']}:rate={lim['fps']}:duration=1",
                   "-pix_fmt", "yuv420p", path)
            cls.fixtures[(lim["width"], lim["height"])] = path
        cls.wrong = os.path.join(folder, "wrong.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1", "-pix_fmt", "yuv420p", cls.wrong)
        cls.not_video = os.path.join(folder, "text.mp4")
        with open(cls.not_video, "w") as f:
            f.write("not a video at all, just text pretending")

    @classmethod
    def tearDownClass(cls):
        with database.SessionLocal() as db:
            for asset in db.query(models.Asset).filter(models.Asset.storage_volume == "static", models.Asset.source == "manim"):
                path = os.path.join(server.STATIC_DIR, asset.storage_key)
                if os.path.exists(path):
                    os.remove(path)

    def setUp(self):
        # Every test starts with no active render left by another one
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE) + ["needs_attention"])).update(
                {"status": "cancelled", "lease_owner": None, "lease_expires_at": None}, synchronize_session=False)
            db.commit()

    # ---- a "server": media layer, Manim service, recovery manager ------------------------------------------------

    def env(self, **extra):
        return {"AI_JOB_LEASE_SECONDS": "30", "AI_JOB_HEARTBEAT_SECONDS": "5", "AI_JOB_CONCURRENCY": "4",
                "AI_RECOVERY_MAX_ATTEMPTS": "3", "MANIM_MAX_CONCURRENT": "2", **extra}

    def instance(self, sandbox=None, **extra):
        env = self.env(**extra)
        registry = ai_providers.ProviderRegistry(env=env, video_mode=lambda: "fake", fake=True)
        media = AIMediaService(server.asset_library, server.ai_cache, registry, static_dir=server.STATIC_DIR,
                               generation_enabled=lambda: False, env=env, log=False)
        sandbox = sandbox or FakeSandbox(self.fixtures, os.path.join(TMP, "fake-sandbox"))
        service = ManimService(server.asset_library, server.ai_cache, sandbox, static_dir=server.STATIC_DIR, env=env, log=False)
        service.is_wanted = visuals.still_wanted
        service.on_completed = server._lesson_after_generation
        manager = RecoveryManager(media, env=env)
        manager.register("manim", service)
        return service, manager, sandbox

    def run_async(self, coro):
        return asyncio.run(coro)

    def render(self, service, source, profile="preview", **kw):
        async def go():
            with database.SessionLocal() as db:
                outcome = await service.render(db, db.get(models.User, kw.pop("uid", self.uid)), source, profile=profile, **kw)
                if isinstance(outcome, tuple):
                    return outcome
                return {"asset": outcome.asset.id, "cache_hit": outcome.cache_hit, "run": outcome.run_id}
        return self.run_async(go())

    def crash_at(self, service, source, point, profile="preview"):
        """A render that dies at `point`; returns the run id with its lease expired (as after a restart)."""
        def crash(p):
            if p == point:
                raise Crash(p)
        with mock.patch.object(service, "_crash", side_effect=crash):
            with self.assertRaises(Crash):
                self.render(service, source, profile)
        with database.SessionLocal() as db:
            run = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "manim").order_by(
                models.AIGenerationRun.created_at.desc()).first()
            self.assertIn(run.status, ai_runs.OWNED)
            run_id = run.id
        self.expire(run_id)
        return run_id

    @staticmethod
    def expire(run_id):
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)})
            db.commit()

    def recover(self, manager):
        async def go():
            tasks = await manager.sweep()
            for task in tasks:
                await task
            return len(tasks)
        return self.run_async(go())

    @staticmethod
    def run_row(run_id):
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            db.expunge(run)
            attempts = [(a.state, json.loads(a.detail or "{}").get("recovered_how"), a.asset_id) for a in ai_runs.attempts_of(db, run_id)]
            return run, attempts

    @staticmethod
    def assets_for(fingerprint_prefix):
        with database.SessionLocal() as db:
            return db.query(models.Asset).filter(models.Asset.source == "manim",
                                                 models.Asset.storage_key.like(f"manim_{fingerprint_prefix}%")).count()

    def fp(self, source, profile="preview"):
        lim = security.limits(profile)
        fixed = security.fix_code(source)
        return security.fingerprint(fixed, lim, security.check_source(fixed, lim)[0])[0][:16]

    # ---- rendering and the cache -----------------------------------------------------------------------------------

    def test_render_registers_an_asset_and_is_cached(self):
        service, _m, sandbox = self.instance()
        source = code()
        first = self.render(service, source)
        self.assertFalse(first["cache_hit"])
        run, attempts = self.run_row(first["run"])
        self.assertEqual((run.status, run.kind, run.provider), ("completed", "manim", "manim"))
        self.assertEqual([a[0] for a in attempts], ["completed"])
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, first["asset"])
            gen = json.loads(asset.details)["generation"]
            self.assertEqual((asset.kind, asset.source, asset.width, asset.height), ("video", "manim", 854, 480))
            self.assertEqual((gen["provider"], gen["profile"], gen["runtime"]), ("manim", "preview", "stand-in"))
            self.assertEqual(db.query(models.AIGeneration).filter(models.AIGeneration.asset_id == asset.id).count(), 1)
        again = self.render(service, source)
        self.assertEqual((again["asset"], again["cache_hit"]), (first["asset"], True))
        self.assertEqual(sandbox.calls, 1)
        # CRLF line endings are the same code; another profile is another render
        self.assertEqual(self.render(service, source.replace("\n", "\r\n"))["asset"], first["asset"])
        standard = self.render(service, source, profile="standard")
        self.assertNotEqual(standard["asset"], first["asset"])
        self.assertEqual(sandbox.calls, 2)
        forced = self.render(service, source, force=True)
        self.assertNotEqual(forced["asset"], first["asset"])  # a forced render makes a new version
        self.assertEqual(sandbox.calls, 3)

    def test_old_pipeline_videos_are_reused(self):
        service, _m, sandbox = self.instance()
        source = code(name="OldScene")
        fixed = security.fix_code(source)
        legacy = os.path.join(server.STATIC_DIR, f"OldScene_{hashlib.sha256(fixed.encode()).hexdigest()[:16]}.mp4")
        shutil.copyfile(self.fixtures[(1280, 720)], legacy)
        try:
            first = self.render(service, source, profile="standard")
            self.assertTrue(first["cache_hit"])
            self.assertEqual(sandbox.calls, 0)
            self.assertEqual(service.stats["legacy_reused"], 1)
            with database.SessionLocal() as db:
                asset = db.get(models.Asset, first["asset"])
                self.assertEqual(asset.storage_key, os.path.basename(legacy))
            self.assertEqual(self.render(service, source, profile="standard")["asset"], first["asset"])
            self.assertEqual(service.stats["legacy_reused"], 1)  # the second time from the cache entry
            self.render(service, source, profile="preview")  # another profile: not the old file
            self.assertEqual(sandbox.calls, 1)
        finally:
            with database.SessionLocal() as db:
                db.query(models.AIGeneration).filter(models.AIGeneration.asset_id.in_(
                    db.query(models.Asset.id).filter(models.Asset.storage_key == os.path.basename(legacy)))).delete(synchronize_session=False)
                db.query(models.Asset).filter(models.Asset.storage_key == os.path.basename(legacy)).delete()
                db.commit()
            os.remove(legacy)

    def test_rejections_and_unavailable(self):
        service, _m, sandbox = self.instance()
        with self.assertRaises(ManimFailed) as caught:
            self.render(service, "import os\n" + code())
        self.assertEqual((caught.exception.category, caught.exception.status), ("unsafe_code", 422))
        with self.assertRaises(ManimFailed) as caught:
            self.render(service, code(), profile="8k")
        self.assertEqual(caught.exception.category, "invalid_code")
        sandbox.unavailable = True
        with self.assertRaises(ManimFailed) as caught:
            self.render(service, code())
        self.assertEqual((caught.exception.category, caught.exception.status), ("sandbox_unavailable", 503))
        self.assertEqual(caught.exception.message, "Secure Manim execution is unavailable in this deployment.")
        self.assertEqual(sandbox.calls, 0)

    def test_output_is_validated_before_it_becomes_an_asset(self):
        service, _m, sandbox = self.instance()
        for produce, category in ((lambda lim: self.wrong, "resolution_limit"), (lambda lim: self.not_video, "invalid_output")):
            with self.subTest(category):
                sandbox.produce = produce
                source = code()
                with self.assertRaises(ManimFailed) as caught:
                    self.render(service, source)
                self.assertEqual(caught.exception.category, category)
                self.assertEqual(self.assets_for(self.fp(source)), 0)
                with database.SessionLocal() as db:
                    run = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "manim").order_by(
                        models.AIGenerationRun.created_at.desc()).first()
                    self.assertEqual((run.status, run.error_category), ("failed", category))

    # ---- recovery at every checkpoint -----------------------------------------------------------------------------

    def test_crash_before_the_render_starts(self):
        service, _m, _s = self.instance()
        source = code()
        run_id = self.crash_at(service, source, "manim_rendering")
        service2, manager2, sandbox2 = self.instance()
        self.assertEqual(self.recover(manager2), 1)
        run, attempts = self.run_row(run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual([(a[0], a[1]) for a in attempts], [("lost", "rendered_again"), ("completed", None)])
        self.assertEqual(sandbox2.calls, 1)
        self.assertEqual(self.assets_for(self.fp(source)), 1)

    def test_crash_during_the_render(self):
        service, _m, sandbox = self.instance()
        source = code()

        def crash_in_sandbox(info):
            raise Crash("during render")
        with mock.patch.object(service, "_started", side_effect=crash_in_sandbox):
            with self.assertRaises(Crash):
                self.render(service, source)
        with database.SessionLocal() as db:
            run = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "manim").order_by(
                models.AIGenerationRun.created_at.desc()).first()
            run_id = run.id
            self.assertEqual(ai_runs.open_attempt(db, run_id).state, "rendering")
        self.expire(run_id)
        _s2, manager2, sandbox2 = self.instance()
        self.recover(manager2)
        run, attempts = self.run_row(run_id)
        self.assertEqual((run.status, run.recovery_count), ("completed", 1))
        self.assertEqual([a[1] for a in attempts], ["rendered_again", None])
        self.assertEqual(self.assets_for(self.fp(source)), 1)
        # The explanation the page shows
        view = self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth("manny")).json()
        self.assertIn("rendered again from the start", view["explanation"])
        self.assertEqual((view["kind"], view["prompt"]), ("manim", None))  # the code is not echoed back

    def test_crash_after_the_render_registers_without_rendering_again(self):
        service, _m, _s = self.instance()
        source = code()
        run_id = self.crash_at(service, source, "manim_after_render")
        _s2, manager2, sandbox2 = self.instance()
        self.recover(manager2)
        run, attempts = self.run_row(run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual([(a[0], a[1]) for a in attempts], [("completed", "registered_output")])
        self.assertEqual(sandbox2.calls, 0)  # nothing rendered twice
        self.assertEqual(self.assets_for(self.fp(source)), 1)

    def test_crash_after_registration_finalizes_without_a_duplicate(self):
        service, _m, _s = self.instance()
        source = code()
        run_id = self.crash_at(service, source, "manim_after_register")
        with database.SessionLocal() as db:
            asset_id = ai_runs.open_attempt(db, run_id).asset_id
            self.assertIsNotNone(asset_id)
        _s2, manager2, sandbox2 = self.instance()
        self.recover(manager2)
        run, attempts = self.run_row(run_id)
        self.assertEqual((run.status, run.asset_id), ("completed", asset_id))
        self.assertEqual([(a[0], a[1]) for a in attempts], [("completed", "finalized_asset")])
        self.assertEqual(sandbox2.calls, 0)
        self.assertEqual(self.assets_for(self.fp(source)), 1)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(models.AIGeneration).filter(models.AIGeneration.asset_id == asset_id).count(), 1)
        self.recover(manager2)  # finalizing again changes nothing
        self.assertEqual(self.assets_for(self.fp(source)), 1)

    def test_repeated_interruptions_stop_as_needs_attention(self):
        service, _m, _s = self.instance()
        run_id = self.crash_at(service, code(), "manim_rendering")
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update({"recovery_count": 3})
            db.commit()
        _s2, manager2, sandbox2 = self.instance()
        self.recover(manager2)
        run, _a = self.run_row(run_id)
        self.assertEqual(run.status, "needs_attention")
        self.assertEqual(sandbox2.calls, 0)

    # ---- leases, managers, cancellation --------------------------------------------------------------------------

    def test_a_worker_that_lost_its_lease_stops_its_render(self):
        service, _m, sandbox = self.instance(AI_JOB_HEARTBEAT_SECONDS="0.2")
        sandbox.gate = threading.Event()
        source = code()

        async def go():
            with database.SessionLocal() as db:
                task = asyncio.ensure_future(service.render(db, db.get(models.User, self.uid), source, profile="preview"))
                while sandbox.running == 0:
                    await asyncio.sleep(0.05)
                with database.SessionLocal() as other:  # another worker took the run over
                    run = other.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "manim").order_by(
                        models.AIGenerationRun.created_at.desc()).first()
                    other.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run.id).update({"lease_owner": "someone-else"})
                    other.commit()
                    run_id = run.id
                with self.assertRaises(ManimFailed):
                    await asyncio.wait_for(task, 10)
                return run_id
        run_id = self.run_async(go())
        self.assertTrue(sandbox.saw_cancel)  # the render itself was stopped
        run, _a = self.run_row(run_id)
        self.assertEqual((run.status, run.lease_owner), ("running", "someone-else"))  # the old worker wrote nothing
        self.assertEqual(self.assets_for(self.fp(source)), 0)

    def test_two_managers_render_a_queued_run_once(self):
        service, _m, _s = self.instance()
        source = code()
        with database.SessionLocal() as db:  # a queued run (as a background request leaves it when nobody claimed it)
            request = {"media_type": "video", "prompt": security.fix_code(source), "profile": "preview", "scene": "Lesson",
                       "fingerprint": "x", "force_regenerate": False, "project_id": None, "scene_index": None, "slot": None}
            run = models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="video",
                                         kind="manim", status="queued", requested_provider="manim", request=json.dumps(request),
                                         request_hash=uuid.uuid4().hex, created_at=datetime.datetime.utcnow(), recovery_count=0,
                                         attempts=0, detail="{}")
            db.add(run)
            db.commit()
            run_id = run.id
        _a, manager_a, sandbox_a = self.instance()
        _b, manager_b, sandbox_b = self.instance()

        async def both():
            started = await asyncio.gather(manager_a.sweep(), manager_b.sweep())
            tasks = [t for group in started for t in group]
            await asyncio.gather(*tasks)
            return len(tasks)
        self.assertEqual(self.run_async(both()), 1)
        self.assertEqual(sandbox_a.calls + sandbox_b.calls, 1)
        run, _att = self.run_row(run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(self.assets_for(self.fp(source)), 1)

    def test_cancel_stops_the_render(self):
        service, _m, sandbox = self.instance()
        sandbox.gate = threading.Event()
        source = code()

        async def go():
            with database.SessionLocal() as db:
                _outcome, run = await service.render(db, db.get(models.User, self.uid), source, profile="preview", wait=False)
                while sandbox.running == 0:
                    await asyncio.sleep(0.05)
                self.assertEqual(service.request_cancel(db, db.get(models.AIGenerationRun, run.id)), "cancelling")
                await asyncio.wait_for(service.tasks.get(run.id) or asyncio.sleep(0), 10)
                return run.id
        run_id = self.run_async(go())
        self.assertTrue(sandbox.saw_cancel)
        run, attempts = self.run_row(run_id)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual([a[0] for a in attempts], ["cancelled"])
        self.assertEqual(self.assets_for(self.fp(source)), 0)

    def test_identical_requests_share_one_render(self):
        service, _m, sandbox = self.instance()
        sandbox.gate = threading.Event()
        source = code()

        async def go():
            with database.SessionLocal() as db1, database.SessionLocal() as db2:
                first = asyncio.ensure_future(service.render(db1, db1.get(models.User, self.uid), source, profile="preview",
                                                             project_id=1, scene_index=0, slot="main"))
                while sandbox.running == 0:
                    await asyncio.sleep(0.05)
                second = asyncio.ensure_future(service.render(db2, db2.get(models.User, self.uid), source, profile="preview"))
                await asyncio.sleep(0.3)
                sandbox.gate.set()
                a, b = await asyncio.gather(first, second)
                return a.asset.id, b.asset.id
        a, b = self.run_async(go())
        self.assertEqual(a, b)
        self.assertEqual(sandbox.calls, 1)
        self.assertEqual(service.stats["attached"], 1)

    def test_concurrency_limits(self):
        service, _m, sandbox = self.instance(MANIM_MAX_CONCURRENT="3", MANIM_MAX_PER_USER="1")
        sandbox.gate = threading.Event()

        async def go():
            with database.SessionLocal() as db1, database.SessionLocal() as db2:
                tasks = [asyncio.ensure_future(service.render(db, db.get(models.User, self.uid), code(), profile="preview"))
                         for db in (db1, db2)]
                await asyncio.sleep(0.6)
                running = sandbox.running
                sandbox.gate.set()
                await asyncio.gather(*tasks)
                return running
        self.assertEqual(self.run_async(go()), 1)  # the user's second render waited its turn
        self.assertEqual((sandbox.max_running, sandbox.calls), (1, 2))
        capped, _m2, sandbox2 = self.instance(MANIM_MAX_QUEUED_PER_USER="1")
        sandbox2.gate = threading.Event()

        async def refused():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                _o, run = await capped.render(db, user, code(), profile="preview", wait=False)
                try:
                    with self.assertRaises(ManimFailed) as caught:
                        await capped.render(db, user, code(), profile="preview")
                    return caught.exception
                finally:
                    sandbox2.gate.set()
                    await capped.tasks.get(run.id, asyncio.sleep(0))
        failure = self.run_async(refused())
        self.assertEqual((failure.category, failure.status), ("busy", 429))

    # ---- Visual Review and the visual router -----------------------------------------------------------------------

    def lesson(self, scene):
        with database.SessionLocal() as db:
            project = models.Project(user_id=self.uid, subject_name="Physics", json_data=json.dumps({"scenes": [scene]}))
            db.add(project)
            db.commit()
            return project.id

    def queued_for(self, project_id, source, profile="preview"):
        with database.SessionLocal() as db:
            request = {"media_type": "video", "prompt": security.fix_code(source), "profile": profile, "scene": "Lesson",
                       "fingerprint": "x", "force_regenerate": False, "project_id": project_id, "scene_index": 0, "slot": "main"}
            run = models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="video",
                                         kind="manim", status="queued", requested_provider="manim", request=json.dumps(request),
                                         request_hash=uuid.uuid4().hex, created_at=datetime.datetime.utcnow(), recovery_count=0,
                                         attempts=0, detail="{}", project_id=project_id, scene_index=0, slot="main")
            db.add(run)
            db.commit()
            return run.id

    def test_visual_review_decides_before_recovery_renders(self):
        source = code()
        cases = [({"type": "simulation", "manim_code": source, "visual_review": {"main": {"status": "removed"}}}, "removed in Visual Review"),
                 ({"type": "simulation", "manim_code": code()}, "code changed")]
        for scene, why in cases:
            with self.subTest(why):
                run_id = self.queued_for(self.lesson(scene), source)
                _s, manager, sandbox = self.instance()
                self.recover(manager)
                run, _a = self.run_row(run_id)
                self.assertEqual((run.status, run.error_category), ("cancelled", "not_wanted"))
                self.assertEqual(sandbox.calls, 0)

    def test_a_finished_render_lands_in_the_lesson_and_the_router_reuses_it(self):
        """The page renders with the default (standard) profile; so does the lesson update and the router."""
        source = code()
        project_id = self.lesson({"type": "simulation", "title": "Forces", "manim_code": source})
        run_id = self.queued_for(project_id, source, profile="standard")
        _s, manager, sandbox = self.instance()
        self.recover(manager)
        run, _a = self.run_row(run_id)
        self.assertEqual(run.status, "completed")
        with database.SessionLocal() as db:
            stored = json.loads(db.get(models.Project, project_id).json_data)["scenes"][0]["visual_plan"]["main"]
            self.assertEqual((stored["source"], stored["asset_id"], stored["cache_hit"]), ("MANIM", run.asset_id, True))
            user = db.get(models.User, self.uid)
            plans = visuals.plan_lesson(db, user, server.asset_library, [{"type": "simulation", "manim_code": source},
                                                                         {"type": "simulation", "manim_code": code()}],
                                        visuals.PlanOptions(), lambda a: f"/a/{a.id}", server.ai_cache, None)
        self.assertEqual((plans[0].asset_id, plans[0].url, plans[0].cache_hit), (run.asset_id, f"/a/{run.asset_id}", True))
        self.assertIsNone(plans[1].asset_id)  # other code: rendered when the lesson plays
        self.assertEqual(sandbox.calls, 1)

    # ---- workspaces ---------------------------------------------------------------------------------------------------

    def test_orphan_workspaces_are_swept(self):
        service, _m, sandbox = self.instance()
        root = sandbox.root
        orphan = Workspace(root, uuid.uuid4().hex).create("gone", "no-such-attempt")
        marker = orphan.file(".aadhi-sandbox.json")
        with open(marker, encoding="utf-8") as f:
            data = json.load(f)
        data["created"] -= 3600
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(data, f)
        fresh = Workspace(root, uuid.uuid4().hex).create("new", "other")  # too young to judge
        stranger = os.path.join(root, "jobs", "not-a-job")
        os.makedirs(stranger, exist_ok=True)
        with database.SessionLocal() as db:
            service.sweep(db)
        self.assertFalse(os.path.exists(orphan.path))
        self.assertTrue(os.path.exists(fresh.path))
        self.assertTrue(os.path.exists(stranger))  # only folders the sandbox made itself are ever removed
        fresh.remove()
        shutil.rmtree(stranger)

    # ---- API --------------------------------------------------------------------------------------------------------

    def test_api(self):
        fake = FakeSandbox(self.fixtures, os.path.join(TMP, "fake-sandbox-api"))
        with mock.patch.object(server.manim_service, "sandbox", fake):
            source = code()
            r = self.client.post("/render", json={"code": source, "profile": "preview"}, headers=auth("manny"))
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(set(data) >= {"status", "video_url", "asset_id", "cached", "cache_hit", "run_id"}, True)
            self.assertTrue(data["video_url"].startswith("/static/manim_"))
            again = self.client.post("/render", json={"code": source, "profile": "preview"}, headers=auth("manny")).json()
            self.assertEqual((again["cached"], again["asset_id"]), (True, data["asset_id"]))
            bad = self.client.post("/render", json={"code": "import subprocess\n" + source}, headers=auth("manny"))
            self.assertEqual((bad.status_code, bad.headers["x-error-category"], bad.json()["category"]), (422, "unsafe_code", "unsafe_code"))
            self.assertNotIn("subprocess", bad.json()["detail"])
            slot = self.client.post("/render", json={"code": source, "slot": "corner"}, headers=auth("manny"))
            self.assertEqual(slot.status_code, 422)
            # Another user never sees the run
            self.assertEqual(self.client.get(f"/api/ai-media/runs/{data['run_id']}", headers=auth("mona")).status_code, 404)
            listed = self.client.get("/api/ai-media/runs", headers=auth("manny")).json()["runs"]
            mine = next(r for r in listed if r["run_id"] == data["run_id"])
            self.assertEqual((mine["kind"], mine["prompt"], mine["profile"]), ("manim", None, "preview"))
            # In the background: 202 with the run, then its result
            queued = self.client.post("/render", json={"code": code(), "profile": "preview", "wait": False}, headers=auth("manny"))
            self.assertEqual(queued.status_code, 202)
            fake.unavailable = True
            down = self.client.post("/render", json={"code": code()}, headers=auth("manny"))
            self.assertEqual((down.status_code, down.headers["x-error-category"]), (503, "sandbox_unavailable"))
            self.assertEqual(down.json()["detail"], "Secure Manim execution is unavailable in this deployment.")
        status = self.client.get("/api/manim/sandbox", headers=auth("manny"))
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(set(body["profiles"]), {"preview", "standard", "high_quality"})
        self.assertNotIn("reason", body)
        self.assertNotIn(os.path.expanduser("~").lower(), json.dumps(body).lower())  # no host path
        self.assertEqual(self.client.get("/api/manim/sandbox").status_code, 401)

    @unittest.skipUnless(os.name == "nt" or shutil.which("docker"), "no secure runtime")
    def test_a_real_render_end_to_end(self):
        sandbox = ManimSandbox({**os.environ})
        try:
            sandbox.runtime()
        except SandboxUnavailable as e:
            self.skipTest(str(e))
        service, _m, _s = self.instance(sandbox=sandbox)
        source = code("real render")
        result = self.render(service, source)
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, result["asset"])
            gen = json.loads(asset.details)["generation"]
            self.assertEqual((asset.width, asset.height, asset.kind), (854, 480, "video"))
            self.assertIn(gen["runtime"], ("windows-appcontainer", "docker"))
            self.assertGreater(gen["peak_memory_mb"], 0)
        self.assertEqual(os.listdir(os.path.join(sandbox.root, "jobs")), [])  # the workspace was removed

    # ---- schema --------------------------------------------------------------------------------------------------------

    def test_schema_step_adds_the_kind_column(self):
        path = os.path.join(TMP, f"phase9-{uuid.uuid4().hex[:6]}.db")
        engine = create_engine("sqlite:///" + path.replace("\\", "/"))
        columns = ", ".join(f"{name} {kind}" for name, kind in models.RUN_COLUMN_ADDITIONS.items() if name != "kind")
        with engine.begin() as conn:
            conn.execute(text(f"CREATE TABLE ai_generation_runs (id VARCHAR(32) PRIMARY KEY, scope_key VARCHAR(64), status VARCHAR(20), {columns})"))
            conn.execute(text("INSERT INTO ai_generation_runs (id, scope_key, status) VALUES ('old', 'user:1', 'completed')"))
        added = database.ensure_schema(engine, {"ai_generation_runs": models.RUN_COLUMN_ADDITIONS}, models.RUN_INDEXES)
        self.assertIn("ai_generation_runs.kind", added)
        self.assertIn("kind", {c["name"] for c in inspect(engine).get_columns("ai_generation_runs")})
        with engine.begin() as conn:
            self.assertEqual(conn.execute(text("SELECT id, kind, status FROM ai_generation_runs")).all(), [("old", None, "completed")])
        self.assertEqual(database.ensure_schema(engine, {"ai_generation_runs": models.RUN_COLUMN_ADDITIONS}, models.RUN_INDEXES), [])
        engine.dispose()
        # An old run (no kind) is AI media for the recovery manager
        _s, manager, _sb = self.instance()
        self.assertIs(manager.executor_for(models.AIGenerationRun(kind=None)), manager.media)


if __name__ == "__main__":
    unittest.main()
