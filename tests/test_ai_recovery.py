"""Backend tests for recovery and resumability (Phase 9): durable runs, leases, attempt history, the recovery
manager, idempotent finalization, billing safety, cancellation, lesson batches, Visual Review, export
finishing and the schema step.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v

A crash is simulated where it matters: the worker is stopped at a crash point (an exception nothing
catches, so no state is tidied up), its lease is left to expire, and a *new* media service and recovery
manager (a restarted server: new instance, no memory of the old one) continue from the database alone.
Only the local stand-in providers exist; their jobs live in files, like a provider's own servers, so they
survive the "restart". No real provider and no outside network.
"""
import asyncio
import datetime
import json
import os
import shutil
import socket
import tempfile
import unittest
import uuid
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

import ai_providers  # noqa: E402
import ai_runs  # noqa: E402
import database  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import visuals  # noqa: E402
from ai_media import AIMediaService  # noqa: E402
from ai_recovery import RecoveryManager  # noqa: E402

_real_connect = socket.socket.connect


def _guarded_connect(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if host in ("127.0.0.1", "::1"):  # the event loop's own socket pair on Windows
        return _real_connect(sock, address)
    raise AssertionError(f"a real network connection was attempted ({host})")


class Crash(BaseException):
    """The simulated power cut: nothing in the worker catches it."""


def words():
    return " ".join(f"r{uuid.uuid4().hex[:7]}" for _ in range(6))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class RecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.uid = ensure_user("rex")
        cls.other = ensure_user("ria")
        cls.state = tempfile.mkdtemp(prefix="aadhi-fake-jobs-", dir=TMP)

    @classmethod
    def tearDownClass(cls):
        with database.SessionLocal() as db:
            for asset in db.query(models.Asset).filter(models.Asset.storage_volume == "static",
                                                       models.Asset.storage_key.like("ai_video_%")):
                path = os.path.join(server.STATIC_DIR, asset.storage_key)
                if os.path.exists(path):
                    os.remove(path)
        shutil.rmtree(cls.state, ignore_errors=True)

    def setUp(self):
        self.guard = mock.patch.object(socket.socket, "connect", _guarded_connect)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    # ---- a "server": media layer + recovery manager with the stand-ins --------------------------------------

    def env(self, **extra):
        return {"FAKE_POLL_SECONDS": "0.05", "FAKE_ALT_POLL_SECONDS": "0.05", "AI_FAKE_STATE_DIR": self.state,
                "AI_PROVIDER_RETRY_DELAY": "0.01", "AI_JOB_LEASE_SECONDS": "30", "AI_JOB_HEARTBEAT_SECONDS": "5",
                "AI_JOB_CONCURRENCY": "4", "AI_RECOVERY_MAX_ATTEMPTS": "3", **extra}

    def server_instance(self, **extra):
        env = self.env(**extra)
        registry = ai_providers.ProviderRegistry(env=env, video_mode=lambda: "fake", fake=True)
        media = AIMediaService(server.asset_library, server.ai_cache, registry, static_dir=server.STATIC_DIR,
                               generation_enabled=lambda: True, env=env, log=False)
        media.is_wanted = visuals.still_wanted
        media.on_completed = server._lesson_after_generation
        return media, RecoveryManager(media, env=env)

    def request(self, prompt=None, media_type="video", **extra):
        return ai_providers.MediaRequest(media_type=media_type, prompt=prompt or words(), **extra)

    def start_and_crash(self, media, request, point):
        """Starts a background generation that dies at `point`; returns the run id (lease expired, as after a restart)."""
        async def go():
            db = database.SessionLocal()
            try:
                user = db.get(models.User, self.uid)
                with mock.patch.object(media, "_crash", side_effect=lambda p: self._maybe_crash(p, point)):
                    _outcome, run = media.start_background(db, user, request)
                    try:
                        await media.tasks[run.id]
                    except Crash:
                        pass
                return run.id
            finally:
                db.close()
        run_id = asyncio.run(go())
        self.expire(run_id)
        return run_id

    @staticmethod
    def _maybe_crash(point, wanted):
        if point == wanted:
            raise Crash(point)

    @staticmethod
    def expire(run_id):
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)})
            db.commit()

    def recover(self, manager, rounds=1):
        async def go():
            for _ in range(rounds):
                tasks = await manager.sweep()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(go())

    def run_row(self, run_id):
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            db.expunge(run)
            return run

    def attempts(self, run_id):
        with database.SessionLocal() as db:
            rows = ai_runs.attempts_of(db, run_id)
            for row in rows:
                db.expunge(row)
            return rows

    def assets_with_key(self, key_prefix):
        with database.SessionLocal() as db:
            return db.query(models.Asset).filter(models.Asset.storage_key.like(f"{key_prefix}%")).count()

    def entries(self, asset_id):
        with database.SessionLocal() as db:
            return db.query(models.AIGeneration).filter(models.AIGeneration.asset_id == asset_id).count()

    def submitted(self, media, name="fake"):
        return getattr(media.registry.get(name), "submitted", 0)

    # ---- state machine, leases, schema -----------------------------------------------------------------------

    def test_state_changes_are_guarded_and_leases_exclusive(self):
        media, _ = self.server_instance()
        with database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            run, attached = media.open_run(db, user, self.request(), owner=None, leased=False)
            self.assertFalse(attached)
            self.assertTrue(ai_runs.claim(db, run.id, "A", ai_runs.RunState.RUNNING, [ai_runs.RunState.QUEUED], 30))
            self.assertFalse(ai_runs.claim(db, run.id, "B", ai_runs.RunState.RUNNING, [ai_runs.RunState.QUEUED], 30))
            self.assertFalse(ai_runs.claim(db, run.id, "B", ai_runs.RunState.RECOVERING, [ai_runs.RunState.RUNNING], 30))  # lease valid
            with self.assertRaises(AssertionError):  # not a transition of the state machine
                ai_runs.transition(db, run.id, ai_runs.RunState.QUEUED, from_states=(ai_runs.RunState.COMPLETED,))
            self.assertFalse(ai_runs.transition(db, run.id, ai_runs.RunState.COMPLETED, from_states=(ai_runs.RunState.RUNNING,), owner="B"))
            self.expire(run.id)
            self.assertTrue(ai_runs.claim(db, run.id, "B", ai_runs.RunState.RECOVERING, [ai_runs.RunState.RUNNING], 30))
            # The old worker wakes up: it can neither renew, nor write, nor finish
            self.assertFalse(ai_runs.renew(db, run.id, "A", 30))
            with self.assertRaises(ai_runs.LeaseLost):
                ai_runs.update_owned(db, run.id, "A", provider="x")
            self.assertFalse(ai_runs.transition(db, run.id, ai_runs.RunState.FAILED, from_states=ai_runs.OWNED, owner="A"))
            # Completed and failed can never both happen
            self.assertTrue(ai_runs.transition(db, run.id, ai_runs.RunState.COMPLETED, from_states=ai_runs.OWNED, owner="B"))
            self.assertFalse(ai_runs.transition(db, run.id, ai_runs.RunState.FAILED, from_states=ai_runs.OWNED, owner="B"))
            self.assertEqual(db.get(models.AIGenerationRun, run.id).status, "completed")

    def test_schema_step_adds_phase9_columns_to_a_phase8_table(self):
        path = os.path.join(TMP, f"old-{uuid.uuid4().hex[:6]}.db")
        engine = create_engine("sqlite:///" + path.replace("\\", "/"))
        with engine.begin() as conn:  # the Phase 8 table, with a row
            conn.execute(text("CREATE TABLE ai_generation_runs (id VARCHAR(32) PRIMARY KEY, scope_key VARCHAR(40) NOT NULL, "
                              "media_type VARCHAR(10) NOT NULL, status VARCHAR(20) NOT NULL, attempts INTEGER NOT NULL, "
                              "forced BOOLEAN NOT NULL, created_at DATETIME)"))
            conn.execute(text("INSERT INTO ai_generation_runs VALUES ('r1', 'user:1', 'video', 'completed', 1, 0, NULL)"))
        added = database.ensure_schema(engine, {"ai_generation_runs": models.RUN_COLUMN_ADDITIONS}, models.RUN_INDEXES)
        self.assertEqual(sorted(a.split(".")[1] for a in added), sorted(models.RUN_COLUMN_ADDITIONS))
        columns = {c["name"] for c in inspect(engine).get_columns("ai_generation_runs")}
        self.assertTrue(set(models.RUN_COLUMN_ADDITIONS) <= columns)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id, status, recovery_count, allow_duplicate FROM ai_generation_runs")).fetchone()
        self.assertEqual(tuple(row), ("r1", "completed", 0, 0))  # kept, new columns with their defaults
        self.assertIn("ix_ai_run_status_lease", {i["name"] for i in inspect(engine).get_indexes("ai_generation_runs")})
        self.assertEqual(database.ensure_schema(engine, {"ai_generation_runs": models.RUN_COLUMN_ADDITIONS}, models.RUN_INDEXES), [])
        engine.dispose()

    # ---- restart during a provider job -------------------------------------------------------------------------

    def test_restart_while_the_provider_job_runs_resumes_the_same_job(self):
        old, _ = self.server_instance(FAKE_JOB_SECONDS="1.5")
        run_id = self.start_and_crash(old, self.request(), "after_job_saved")
        before = self.attempts(run_id)
        self.assertEqual([(a.state, bool(a.provider_job_id)) for a in before], [("submitted", True)])
        self.assertEqual(self.submitted(old), 1)
        new, manager = self.server_instance(FAKE_JOB_SECONDS="1.5")
        self.recover(manager)
        run = self.run_row(run_id)
        after = self.attempts(run_id)
        self.assertEqual((run.status, run.recovery_count, run.provider_job_id), ("completed", 1, before[0].provider_job_id))
        self.assertEqual(self.submitted(new), 0)  # no second provider job
        self.assertEqual([(a.state, a.recovered, a.provider_job_id) for a in after], [("completed", 1, before[0].provider_job_id)])
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, run.asset_id)
            self.assertEqual((asset.status, asset.kind, asset.source, asset.width, asset.height), ("ready", "video", "ai-video", 640, 360))
            self.assertAlmostEqual(asset.duration_seconds, 4, delta=0.2)
        view = self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth("rex")).json()
        self.assertIn("provider's job was picked up again", view["explanation"])
        self.assertEqual(view["history"][0]["recovered"], 1)

    def test_restart_after_the_download_registers_the_file_without_fetching_again(self):
        old, _ = self.server_instance()
        run_id = self.start_and_crash(old, self.request(), "after_download")
        attempt = self.attempts(run_id)[0]
        self.assertEqual(attempt.state, "registering")
        self.assertTrue(os.path.exists(attempt.output_path))
        new, manager = self.server_instance()
        with mock.patch.object(new, "resume_provider", side_effect=AssertionError("fetched again")):
            self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, self.submitted(new)), ("completed", 0))
        self.assertEqual(self.entries(run.asset_id), 1)

    def test_restart_during_or_after_registration_never_duplicates(self):
        for point in ("after_register", "after_cache"):
            old, _ = self.server_instance()
            run_id = self.start_and_crash(old, self.request(), point)
            attempt = self.attempts(run_id)[0]
            self.assertTrue(attempt.asset_id, point)
            key = os.path.basename(attempt.output_path)
            new, manager = self.server_instance()
            self.recover(manager)
            self.recover(manager)  # finalizing again finds nothing to do
            run = self.run_row(run_id)
            self.assertEqual((run.status, run.asset_id), ("completed", attempt.asset_id), point)
            self.assertEqual(self.assets_with_key(key), 1, point)   # one asset
            self.assertEqual(self.entries(run.asset_id), 1, point)  # one cache entry
            self.assertEqual(self.submitted(new), 0, point)

    def test_images_recovered_after_registration_are_not_duplicated(self):
        old, _ = self.server_instance()
        run_id = self.start_and_crash(old, self.request(media_type="image"), "after_register")
        attempt = self.attempts(run_id)[0]
        new, manager = self.server_instance()
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.asset_id), ("completed", attempt.asset_id))
        self.assertFalse(os.path.exists(attempt.output_path))  # the work file is gone, the library keeps its copy
        self.assertEqual(self.entries(run.asset_id), 1)

    # ---- billing safety ---------------------------------------------------------------------------------------

    def test_an_ambiguous_paid_submission_needs_attention_and_is_never_resubmitted_by_itself(self):
        old, _ = self.server_instance(FAKE_PAID="1")
        run_id = self.start_and_crash(old, self.request(), "before_job_saved")
        self.assertEqual(self.submitted(old), 1)  # the provider accepted it, but its answer was never saved
        new, manager = self.server_instance(FAKE_PAID="1")
        self.recover(manager, rounds=2)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.error_category, self.submitted(new)), ("needs_attention", "ambiguous_submission", 0))
        self.assertEqual([a.state for a in self.attempts(run_id)], ["ambiguous"])
        view = self.client.get(f"/api/ai-media/runs/{run_id}", headers=auth("rex")).json()
        self.assertIn("No duplicate generation was started", view["explanation"])
        self.assertEqual(view["actions"], ["retry", "dismiss"])
        self.assertEqual(self.client.post(f"/api/ai-media/runs/{run_id}/resolve", json={"action": "retry"}, headers=auth("ria")).status_code, 404)
        # A person decides to retry: now it is generated (once)
        retried = self.client.post(f"/api/ai-media/runs/{run_id}/resolve", json={"action": "retry"}, headers=auth("rex")).json()
        self.assertEqual(retried["status"], "queued")
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, self.submitted(new)), ("completed", 1))
        self.assertEqual([a.state for a in self.attempts(run_id)], ["ambiguous", "completed"])  # history kept

    def test_an_ambiguous_free_submission_is_simply_tried_again(self):
        old, _ = self.server_instance()
        run_id = self.start_and_crash(old, self.request(), "before_job_saved")
        new, manager = self.server_instance()
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, self.submitted(new)), ("completed", 1))
        self.assertEqual([a.state for a in self.attempts(run_id)], ["lost", "completed"])

    def test_a_job_the_provider_lost(self):
        for paid, expected in (("1", "needs_attention"), ("0", "completed")):
            old, _ = self.server_instance(FAKE_PAID=paid, AI_FAKE_FAIL="fake:video:vanish")
            run_id = self.start_and_crash(old, self.request(), "after_job_saved")
            new, manager = self.server_instance(FAKE_PAID=paid)
            self.recover(manager)
            run = self.run_row(run_id)
            self.assertEqual(run.status, expected, paid)
            self.assertEqual(self.attempts(run_id)[0].state, "lost")
            if paid == "1":
                self.assertEqual((run.error_category, self.submitted(new)), ("job_lost", 0))
            else:
                self.assertEqual(self.submitted(new), 1)

    def test_a_job_that_failed_on_the_provider_falls_back_after_the_restart(self):
        old, _ = self.server_instance(FAKE_JOB_SECONDS="5")
        run_id = self.start_and_crash(old, self.request(), "after_job_saved")
        attempt = self.attempts(run_id)[0]
        job_file = os.path.join(self.state, f"{attempt.provider_job_id}.json")
        with open(job_file, encoding="utf-8") as f:
            job = json.load(f)
        job["kind"] = "jobfail"  # the provider's side failed while the server was down
        with open(job_file, "w", encoding="utf-8") as f:
            json.dump(job, f)
        new, manager = self.server_instance()
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.provider, run.fallback_from), ("completed", "fake-alt", "fake"))
        self.assertEqual((self.submitted(new, "fake"), self.submitted(new, "fake-alt")), (0, 1))  # A never resubmitted
        self.assertEqual([(a.provider, a.state) for a in self.attempts(run_id)], [("fake", "failed"), ("fake-alt", "completed")])

    def test_a_hiccup_while_following_a_job_resumes_it_instead_of_submitting_again(self):
        media, _ = self.server_instance()
        real_poll = ai_providers.FakeProvider.poll
        calls = {"n": 0}

        def flaky(provider, job):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ai_providers.ProviderError(ai_providers.Failure.UNAVAILABLE, "connection reset", provider=provider.name)
            return real_poll(provider, job)

        async def go():
            with database.SessionLocal() as db:
                return await media.generate(db, db.get(models.User, self.uid), self.request())
        with mock.patch.object(ai_providers.FakeProvider, "poll", flaky):
            outcome = asyncio.run(go())
        self.assertTrue(outcome.generated)
        self.assertEqual(self.submitted(media), 1)  # one provider job, followed again after the hiccup

    # ---- cache first, attaching, concurrency ------------------------------------------------------------------

    def test_recovery_checks_the_cache_first(self):
        prompt = words()
        old, _ = self.server_instance()
        run_id = self.start_and_crash(old, self.request(prompt), "before_job_saved")
        other, _ = self.server_instance()
        # The same visual, made meanwhile for another place (a request of its own, not attached to the run)
        made = asyncio.run(self._generate(other, self.request(prompt, slot="side")))
        new, manager = self.server_instance()
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.cache_hit, run.asset_id), ("completed", True, made))
        self.assertEqual(self.submitted(new), 0)

    async def _generate(self, media, request):
        with database.SessionLocal() as db:
            return (await media.generate(db, db.get(models.User, self.uid), request)).asset.id

    def test_an_identical_request_attaches_to_the_interrupted_run(self):
        prompt = words()
        old, _ = self.server_instance(FAKE_JOB_SECONDS="1")
        run_id = self.start_and_crash(old, self.request(prompt), "after_job_saved")
        new, manager = self.server_instance(FAKE_JOB_SECONDS="1")

        async def both():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                _outcome, run = new.start_background(db, user, self.request(prompt))
                tasks = await manager.sweep()
                await asyncio.gather(*tasks)
                return run.id
        attached = asyncio.run(both())
        self.assertEqual(attached, run_id)
        self.assertEqual((self.run_row(run_id).status, self.submitted(new)), ("completed", 0))

    def test_two_recovery_managers_take_a_run_once(self):
        old, _ = self.server_instance(FAKE_JOB_SECONDS="0.5")
        run_id = self.start_and_crash(old, self.request(), "after_job_saved")
        one, manager_one = self.server_instance(FAKE_JOB_SECONDS="0.5")
        two, manager_two = self.server_instance(FAKE_JOB_SECONDS="0.5")

        async def race():
            a, b = await asyncio.gather(manager_one.sweep(), manager_two.sweep())
            await asyncio.gather(*a, *b)
            return len(a) + len(b)
        self.assertEqual(asyncio.run(race()), 1)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.recovery_count), ("completed", 1))
        self.assertEqual(self.submitted(one) + self.submitted(two), 0)

    # ---- cancellation ------------------------------------------------------------------------------------------

    def test_cancelling_while_the_worker_is_gone(self):
        old, _ = self.server_instance(FAKE_JOB_SECONDS="30")
        run_id = self.start_and_crash(old, self.request(), "after_job_saved")
        answer = self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("rex"))
        self.assertEqual((answer.status_code, answer.json()["status"]), (202, "cancelling"))
        self.assertEqual(self.run_row(run_id).status, "cancel_requested")
        self.assertEqual(self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("ria")).status_code, 404)
        new, manager = self.server_instance(FAKE_JOB_SECONDS="30")
        self.recover(manager, rounds=2)
        run = self.run_row(run_id)
        attempt = self.attempts(run_id)[0]
        self.assertEqual((run.status, attempt.state, json.loads(attempt.detail)["provider_cancel"]), ("cancelled", "cancelled", "cancelled"))
        with open(os.path.join(self.state, f"{attempt.provider_job_id}.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["cancelled"])  # the provider's job was stopped
        self.assertEqual(self.submitted(new), 0)
        self.recover(manager)
        self.assertEqual(self.run_row(run_id).status, "cancelled")  # never restarts by itself

    def test_cancelling_a_queued_run_stops_it_before_anything_is_sent(self):
        media, manager = self.server_instance()
        with database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            _batch, runs = media.start_batch(db, user, [self.request()])
            run_id = runs[0].id
        answer = self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("rex")).json()
        self.assertEqual(answer["status"], "cancelled")
        self.recover(manager)
        self.assertEqual((self.run_row(run_id).status, self.submitted(media), self.attempts(run_id)), ("cancelled", 0, []))

    def test_a_run_interrupted_too_often_stops_for_a_person(self):
        old, _ = self.server_instance(FAKE_JOB_SECONDS="30")
        run_id = self.start_and_crash(old, self.request(), "after_job_saved")
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update({"recovery_count": 3})
            db.commit()
        new, manager = self.server_instance()
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.error_category, self.submitted(new)), ("needs_attention", "interrupted", 0))

    def test_temporary_provider_trouble_during_recovery_backs_off(self):
        old, _ = self.server_instance()
        run_id = self.start_and_crash(old, self.request(), "before_job_saved")
        new, manager = self.server_instance(AI_FAKE_FAIL="fake:video:unavailable,fake-alt:video:unavailable")
        self.recover(manager)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.recovery_count), ("queued", 2))
        self.assertGreater(run.not_before, datetime.datetime.utcnow())
        before = self.submitted(new)
        self.recover(manager)  # not due yet: nothing is tried again at once
        self.assertEqual((self.run_row(run_id).status, self.submitted(new)), ("queued", before))

    # ---- lessons: batches, resuming, Visual Review ------------------------------------------------------------------

    def lesson(self, count):
        topics = [words() for _ in range(count)]
        scenes = [{"type": "ai_video", "title": t, "prompt": t} for t in topics]
        project = self.client.post("/save-history", json={"subject_name": "Phase 9 lesson", "scenes": scenes}, headers=auth("rex")).json()["id"]
        return project, topics

    def batch(self, project, media):
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1"}), \
                mock.patch.object(server.ai_media, "registry", media.registry), mock.patch.object(server, "ai_registry", media.registry):
            res = self.client.post(f"/api/ai-media/lessons/{project}/generate", json={"media": "video"}, headers=auth("rex"))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def stored_plan(self, project, index):
        with database.SessionLocal() as db:
            scenes = json.loads(db.get(models.Project, project).json_data)["scenes"]
        return (scenes[index].get("visual_plan") or {}).get("main") or {}

    def test_a_lesson_batch_resumes_after_a_restart_without_redoing_finished_scenes(self):
        project, topics = self.lesson(4)
        old, old_manager = self.server_instance(AI_JOB_CONCURRENCY="1", FAKE_JOB_SECONDS="1")
        batch = self.batch(project, old)
        self.assertEqual([r["scene_index"] for r in sorted(batch["runs"], key=lambda r: r["scene_index"])], [0, 1, 2, 3])
        runs = {r["scene_index"]: r["run_id"] for r in batch["runs"]}
        self.recover(old_manager)  # scene 1 of 4 made (one worker)
        first = self.run_row(runs[0])
        self.assertEqual(first.status, "completed")
        self.assertEqual(self.stored_plan(project, 0).get("asset_id"), first.asset_id)  # kept in the lesson at once

        async def second_then_crash():  # the next scene starts and the server dies while its job runs
            with mock.patch.object(old, "_crash", side_effect=lambda p: self._maybe_crash(p, "after_job_saved")):
                tasks = await old_manager.sweep()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(second_then_crash())
        crashed = next(i for i in (1, 2, 3) if self.run_row(runs[i]).status == "running")
        self.expire(runs[crashed])
        self.assertEqual(self.submitted(old), 2)
        new, manager = self.server_instance(AI_JOB_CONCURRENCY="4", FAKE_JOB_SECONDS="1")
        self.recover(manager, rounds=3)
        final = {i: self.run_row(runs[i]) for i in runs}
        self.assertEqual({i: r.status for i, r in final.items()}, {0: "completed", 1: "completed", 2: "completed", 3: "completed"})
        self.assertEqual(final[0].asset_id, first.asset_id)  # the finished scene is untouched
        self.assertEqual(final[crashed].recovery_count, 1)
        self.assertEqual(self.submitted(new), 2)  # only the two scenes never started (the crashed one resumed its job)
        for i in runs:
            self.assertEqual(self.stored_plan(project, i).get("asset_id"), final[i].asset_id)
        again = self.batch(project, new)  # nothing left to make
        self.assertEqual((again["runs"], again["already_available"]), ([], 4))

    def test_visual_review_decisions_survive_recovery(self):
        project, topics = self.lesson(3)
        media, manager = self.server_instance()
        batch = self.batch(project, media)
        runs = {r["scene_index"]: r["run_id"] for r in batch["runs"]}
        library_clip = os.path.join(TMP, f"clip-{uuid.uuid4().hex[:6]}.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", library_clip)
        with open(library_clip, "rb") as f:
            clip_id = self.client.post("/api/assets", files={"file": ("clip.mp4", f, "video/mp4")}, headers=auth("rex")).json()["asset"]["id"]
        review = lambda body: self.client.post("/api/visuals/review", json={"project_id": project, "scene_index": body[0], "slot": "main", **body[1]},  # noqa: E731
                                               headers=auth("rex"))
        self.assertEqual(review((0, {"action": "remove"})).status_code, 200)
        self.assertEqual(review((1, {"action": "choose", "asset_id": clip_id})).status_code, 200)
        self.recover(manager)
        states = {i: self.run_row(runs[i]) for i in runs}
        self.assertEqual((states[0].status, states[0].error_category), ("cancelled", "not_wanted"))
        self.assertIn("removed in Visual Review", states[0].error_message)
        self.assertEqual((states[1].status, states[1].error_category), ("cancelled", "not_wanted"))
        self.assertEqual(states[2].status, "completed")  # the scene nobody changed is made
        self.assertEqual(self.submitted(media), 1)
        self.assertEqual(self.stored_plan(project, 0).get("selection"), "removed")
        self.assertEqual(self.stored_plan(project, 1).get("asset_id"), clip_id)
        # A New AI Version the user asked for is still made, even though another visual is approved
        with database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            forced = self.request(topics[1], force_regenerate=True, project_id=project, scene_index=1, slot="main")
            _b, (run,) = media.start_batch(db, user, [forced])
            run_id = run.id
        self.recover(manager)
        self.assertEqual(self.run_row(run_id).status, "completed")

    # ---- housekeeping, privacy, export finishing ----------------------------------------------------------------------

    def test_old_finished_runs_are_cleaned_up_and_active_ones_kept(self):
        media, manager = self.server_instance()
        long_ago = datetime.datetime.utcnow() - datetime.timedelta(days=200)
        ids = {}
        with database.SessionLocal() as db:
            for status in ("completed", "failed", "needs_attention", "running"):
                rid = uuid.uuid4().hex
                ids[status] = rid
                db.add(models.AIGenerationRun(id=rid, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="video", status=status,
                                              attempts=0, forced=False, created_at=long_ago, finished_at=long_ago,
                                              lease_owner="x" if status == "running" else None,
                                              lease_expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1)))
                db.add(models.AIGenerationAttempt(id=uuid.uuid4().hex, run_id=rid, number=1, provider="fake", state="completed"))
            db.add(models.AIGenerationLock(key=f"user:{self.uid}:{uuid.uuid4().hex}", created_at=long_ago))
            db.commit()
        manager.last_cleanup = None
        self.recover(manager)
        with database.SessionLocal() as db:
            present = {s: db.get(models.AIGenerationRun, rid) is not None for s, rid in ids.items()}
            orphans = db.query(models.AIGenerationAttempt).filter(models.AIGenerationAttempt.run_id.in_([ids["completed"], ids["failed"]])).count()
            stale_locks = db.query(models.AIGenerationLock).filter(models.AIGenerationLock.created_at < long_ago + datetime.timedelta(days=1)).count()
        self.assertEqual(present, {"completed": False, "failed": False, "needs_attention": True, "running": True})
        self.assertEqual((orphans, stale_locks), (0, 0))

    def test_runs_are_private(self):
        media, _ = self.server_instance()
        with database.SessionLocal() as db:
            _b, (run,) = media.start_batch(db, db.get(models.User, self.uid), [self.request()])
            run_id = run.id
        for method, path in (("get", f"/api/ai-media/runs/{run_id}"), ("post", f"/api/ai-media/runs/{run_id}/cancel")):
            self.assertEqual(getattr(self.client, method)(path, headers=auth("ria")).status_code, 404)
        self.assertNotIn(run_id, [r["run_id"] for r in self.client.get("/api/ai-media/runs?active=true", headers=auth("ria")).json()["runs"]])
        mine = self.client.get("/api/ai-media/runs?active=true", headers=auth("rex")).json()["runs"]
        self.assertIn(run_id, [r["run_id"] for r in mine])
        self.client.post(f"/api/ai-media/runs/{run_id}/cancel", headers=auth("rex"))

    def test_export_finishing_is_recovered_after_a_restart(self):
        def interrupted_export(moved):
            with database.SessionLocal() as db:
                export = models.VideoExport(id=uuid.uuid4().hex, user_id=self.uid, title="Recovered lesson", status="PROCESSING",
                                            source="lesson")
                db.add(export)
                db.commit()
                export_id = export.id
            tmp = exports._tmp_path(export_id)
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                   "-c:v", "libvpx", "-b:v", "200k", "-c:a", "libopus", "-shortest", "-f", "webm", tmp)
            size = os.path.getsize(tmp)
            body = type("B", (), {"size": size, "mime_type": "video/webm;codecs=vp8,opus", "duration_seconds": 2.0,
                                  "timeline": {"scenes": [{"t": 0.2, "title": "Start"}], "cues": [{"start": 0.3, "end": 1.5, "text": "Hello."}]}})()
            exports._save_completion(export_id, body, "webm")
            if moved:  # the crash came after the file was moved into place
                key, _size, _info = exports.finalise_upload(export_id, self.uid, "webm")
            return export_id
        pending_before = None
        moved_id, unmoved_id = interrupted_export(True), interrupted_export(False)
        with database.SessionLocal() as db:
            lost = models.VideoExport(id=uuid.uuid4().hex, user_id=self.uid, title="Lost", status="PROCESSING", source="lesson")
            db.add(lost)
            db.commit()
            lost_id = lost.id
        with mock.patch.object(exports, "make_mp4") as make_mp4:
            counts = exports.recover_exports()
        self.assertIsNone(pending_before)
        self.assertEqual((counts["finished"], counts["failed"]), (2, 1))
        with database.SessionLocal() as db:
            for export_id in (moved_id, unmoved_id):
                export = db.get(models.VideoExport, export_id)
                self.assertEqual((export.status, export.format), ("COMPLETED", "webm"))
                self.assertTrue(os.path.exists(exports.STORAGE.path(export.storage_key)))
                kinds = {o.kind: o.status for o in db.query(models.ExportOutput).filter(models.ExportOutput.export_id == export_id)}
                self.assertEqual((kinds.get("vtt"), kinds.get("chapters"), kinds.get("mp4")), ("ready", "ready", "pending"))
                self.assertFalse(os.path.exists(exports._completion_path(export_id)))
            self.assertEqual(db.get(models.VideoExport, lost_id).status, "FAILED")
        self.assertEqual(make_mp4.call_count, counts["mp4"])
        self.assertGreaterEqual(counts["mp4"], 2)
        with mock.patch.object(exports, "make_mp4"):
            again = exports.recover_exports()  # nothing is finished twice
        self.assertEqual((again["finished"], again["failed"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
