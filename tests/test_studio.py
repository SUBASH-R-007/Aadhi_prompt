"""Backend tests of the Phase 20 End-to-End Studio (studio.py and its server.py wiring): durable lesson writing (a run of kind
"lesson_script": start, stages, completion, failures in plain words, cancel, interruption and recovery), the lessons list
and each lesson's derived workflow state, checkpoints written without losing a concurrent save, /save-history unchanged
(and keeping the Studio's data), lesson ownership on the generation endpoints and scene ids in the lesson batch.

Run from the repo root:
  PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_studio.py" -v

No real AI provider is called: lessons are written by the stand-in (AI_FAKE_PROVIDER=1 in the service's env), media
providers are the local fakes.
"""
import asyncio
import datetime
import json
import os
import shutil
import subprocess
import threading
import time
import unittest
import uuid
from unittest import mock

from backend_env import REPO, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_providers  # noqa: E402
import database  # noqa: E402
import editor_api  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import studio  # noqa: E402
from ai_media import AIMediaService  # noqa: E402
from ai_recovery import RecoveryManager  # noqa: E402

USER, OTHER = "stella", "oscar"
FIXTURES = os.path.join(REPO, "tests", "fixtures", "studio")
NODE = shutil.which("node")
PROMPT = "You write lessons as JSON."  # stands for the page's getSystemPrompt(); the stand-in does not read it


class Crash(BaseException):
    """The simulated power cut: nothing in the worker catches it."""


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def env(**extra):
    return {"AI_FAKE_PROVIDER": "1", "AI_JOB_LEASE_SECONDS": "30", "AI_JOB_HEARTBEAT_SECONDS": "5", "AI_JOB_CONCURRENCY": "4",
            "AI_RECOVERY_MAX_ATTEMPTS": "3", **extra}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user(USER)
        cls.other_uid = ensure_user(OTHER)
        cls.client = TestClient(server.app)

    def setUp(self):
        # Every test starts with no active run left by another suite (a recovery sweep would take it)
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.status.in_(["queued", "running", "recovering", "cancel_requested"])).update(
                {"status": "cancelled", "lease_owner": None, "lease_expires_at": None}, synchronize_session=False)
            db.commit()

    # ---- helpers ----------------------------------------------------------------------------------------------------

    def start(self, client, body, user=USER, status=200):
        r = client.post("/api/studio/lessons", json={"system_prompt": PROMPT, **body}, headers=auth(user))
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def wait(self, client, run_id, user=USER):
        for _ in range(400):
            view = client.get(f"/api/studio/runs/{run_id}", headers=auth(user)).json()
            if view["status"] not in ("running", "recovering", "queued", "cancel_requested"):
                return view
            time.sleep(0.02)
        self.fail("the lesson writing did not finish")

    def write(self, body, service_env=None, user=USER):
        """A lesson written through the API by the stand-in: (run view, saved payload)."""
        with mock.patch.object(server.lesson_script_service, "env", service_env or env()), TestClient(server.app) as client:
            started = self.start(client, body, user=user)
            view = self.wait(client, started["run_id"], user=user)
        payload = self.payload(view["project_id"]) if view["project_id"] else None
        return view, payload

    def payload(self, pid):
        with database.SessionLocal() as db:
            return json.loads(db.get(models.Project, pid).json_data)

    def run_row(self, run_id):
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            db.expunge(run)
            return run

    def save(self, scenes, user=USER, **extra):
        r = self.client.post("/save-history", json={"subject_name": "Studio", "scenes": scenes, **extra}, headers=auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def state(self, pid, user=USER):
        r = self.client.get(f"/api/studio/lessons/{pid}", headers=auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def projects_of_run(self, run_id):
        with database.SessionLocal() as db:
            rows = db.query(models.Project).filter(models.Project.user_id == self.uid).all()
            return [p.id for p in rows if ((json.loads(p.json_data).get("studio") or {}).get("generated") or {}).get("run_id") == run_id]


class LessonWritingTest(Base):
    def test_a_lesson_is_written_saved_and_traced(self):
        style = {"style": "academic", "style_overrides": {"nonsense": 1}}
        view, payload = self.write({"text": fixture("photosynthesis.txt"), "names": {"session_title": "Plants and light", "unit_name": "Biology"},
                                    "provider": "fake", "cinematic_style": style})
        self.assertEqual((view["status"], view["stage"], view["message"]), ("completed", "Saving the lesson", None))
        self.assertEqual((payload["session_title"], payload["unit_name"], payload["subject_name"]), ("Plants and light", "Biology", "How Plants Make Food"))
        generated = payload["studio"]["generated"]
        self.assertEqual((payload["studio"]["version"], payload["studio"]["checkpoints"]), (1, {}))
        self.assertEqual((generated["run_id"], generated["provider"], generated["model"], generated["source"]), (view["run_id"], "fake", "stand-in", None))
        self.assertTrue(generated["at"].endswith("Z"))
        self.assertNotIn("source_document", payload)
        self.assertEqual(payload["cinematic_style"], {"style": "academic", "style_version": 1, "style_overrides": {}})  # cleaned
        scenes = payload["scenes"]
        self.assertTrue(all(editor_api.SCENE_ID.fullmatch(s["scene_id"]) for s in scenes))
        self.assertEqual(payload["editor"], {"version": 1, "generated_order": [s["scene_id"] for s in scenes]})
        self.assertTrue(all(s["source"]["origin"] == "source" and s["source"]["refs"] for s in scenes))
        self.assertIn("quiz_checkpoint", {s["type"] for s in scenes})
        run = self.client.get(f"/api/ai-media/runs/{view['run_id']}", headers=auth(USER)).json()  # also a run like the others
        self.assertEqual((run["kind"], run["status"], run["media_type"]), ("lesson_script", "completed", "text"))
        row = self.run_row(view["run_id"])
        self.assertIsNone(row.project_id)  # not a media run of the lesson
        self.assertEqual(json.loads(row.request)["text"], fixture("photosynthesis.txt"))

    @unittest.skipUnless(NODE, "needs Node (the page's extractor)")
    def test_a_lesson_from_a_prepared_source(self):
        script = ("const fs=require('fs');const S=require(process.argv[1]);"
                  "process.stdout.write(JSON.stringify(S.blocksFromText(fs.readFileSync(process.argv[2],'utf8'))));")
        out = subprocess.run([NODE, "-e", script, os.path.join(REPO, "sources.js"), os.path.join(FIXTURES, "newtons_second_law.txt")],
                             capture_output=True, check=True)
        blocks = json.loads(out.stdout.decode("utf-8"))
        name = f"newton-{uuid.uuid4().hex[:6]}.txt"
        doc = self.client.post("/api/source-documents", json={"file_name": name, "source_type": "txt", "blocks": blocks},
                               headers=auth(USER)).json()["document"]["document_id"]
        analysis = self.client.post(f"/api/source-documents/{doc}/analyze", json={"mode": "structural"}, headers=auth(USER)).json()["analysis_id"]
        source = {"document_id": doc, "analysis_id": analysis}
        handoff = self.client.get(f"/api/source-analyses/{analysis}/lesson-input", headers=auth(USER)).json()["text"]
        view, payload = self.write({"source": source, "provider": "gemini"})  # not set up here: the test server's stand-in writes
        self.assertEqual(view["status"], "completed")
        self.assertEqual(payload["source_document"], source)
        self.assertEqual(payload["studio"]["generated"]["source"], source)
        self.assertEqual(payload["studio"]["generated"]["provider"], "fake")
        self.assertEqual(json.loads(self.run_row(view["run_id"]).request)["text"], handoff)  # what the Lesson Director receives
        lines = handoff.split("\n")
        formula = next(s for s in payload["scenes"] if "formula-block" in (s.get("html") or ""))
        self.assertIn(next(i for i, l in enumerate(lines, 1) if "$$F = m \\times a$$" in l), formula["source"]["refs"])
        state = self.state(view["project_id"])
        self.assertEqual(state["source"], {**source, "file_name": name})
        # a newer upload of the same file makes the analysis stale: never used silently
        self.client.post("/api/source-documents", json={"file_name": name, "source_type": "txt",
                                                        "blocks": blocks + [{"type": "paragraph", "text": "An added line."}]}, headers=auth(USER))
        with mock.patch.object(server.lesson_script_service, "env", env()):
            stale = self.client.post("/api/studio/lessons", json={"system_prompt": PROMPT, "source": source}, headers=auth(USER))
        self.assertEqual(stale.status_code, 409)
        self.assertIn("newer version", stale.json()["detail"])
        with mock.patch.object(server.lesson_script_service, "env", env()):  # someone else's source: the same answer as none
            other = self.client.post("/api/studio/lessons", json={"system_prompt": PROMPT, "source": source}, headers=auth(OTHER))
        self.assertEqual(other.status_code, 404)

    def test_failures_are_plain_words_and_a_malformed_answer_is_repaired_once(self):
        for mode, status in (("fail", "failed"), ("malformed", "failed"), ("malformed_once", "completed")):
            with self.subTest(mode=mode):
                view, payload = self.write({"text": "Gravity is a force that pulls objects together."}, env(FAKE_LLM_MODE=mode))
                self.assertEqual(view["status"], status)
                if status == "failed":
                    self.assertEqual(view["message"], studio.FAILED_MESSAGE)
                    self.assertIsNone(view["project_id"])
                    self.assertEqual(self.projects_of_run(view["run_id"]), [])
                else:
                    self.assertTrue(payload["scenes"])

    def test_a_double_click_joins_the_lesson_being_written(self):
        with mock.patch.object(server.lesson_script_service, "env", env(FAKE_LLM_SECONDS="0.5")), TestClient(server.app) as client:
            first = self.start(client, {"text": "Gravity is a force that pulls objects together."})
            again = self.start(client, {"text": "Gravity is a force that pulls objects together."})
            self.assertEqual((again["run_id"], again["attached"]), (first["run_id"], True))
            listed = client.get("/api/studio/lessons", headers=auth(USER)).json()
            self.assertIn(first["run_id"], [r["run_id"] for r in listed["runs"]])  # the page can continue it
            self.assertEqual(self.wait(client, first["run_id"])["status"], "completed")
        self.assertEqual(len(self.projects_of_run(first["run_id"])), 1)

    def test_cancel_stops_the_writing_and_saves_nothing(self):
        with mock.patch.object(server.lesson_script_service, "env", env(FAKE_LLM_SECONDS="1")), TestClient(server.app) as client:
            started = self.start(client, {"text": "Friction is a force that slows things down."})
            stopped = client.post(f"/api/studio/runs/{started['run_id']}/cancel", headers=auth(USER))
            self.assertEqual(stopped.status_code, 200)
            self.assertEqual((stopped.json()["status"], stopped.json()["project_id"]), ("cancelled", None))
            self.assertIn("Your source is safe", stopped.json()["message"])
            again = client.post(f"/api/studio/runs/{started['run_id']}/cancel", headers=auth(USER))  # idempotent
            self.assertEqual((again.status_code, again.json()["status"]), (200, "cancelled"))
            time.sleep(1.2)  # the stand-in's answer arrives after the cancel: it is never saved
        self.assertEqual(self.projects_of_run(started["run_id"]), [])
        self.assertEqual(self.run_row(started["run_id"]).status, "cancelled")

    def test_ownership_validation_and_an_unset_writer(self):
        view, _payload = self.write({"text": "Light travels in straight lines."})
        rid, pid = view["run_id"], view["project_id"]
        for method, url, body in (("get", f"/api/studio/runs/{rid}", None), ("post", f"/api/studio/runs/{rid}/cancel", None),
                                  ("get", f"/api/studio/lessons/{pid}", None),
                                  ("put", f"/api/studio/lessons/{pid}/checkpoint", {"name": "lesson", "value": {}})):
            with self.subTest(url=url):
                r = getattr(self.client, method)(url, headers=auth(OTHER), **({"json": body} if body is not None else {}))
                self.assertEqual(r.status_code, 404)
                self.assertEqual(getattr(self.client, method)(url, **({"json": body} if body is not None else {})).status_code, 401)
        self.assertEqual(self.client.get("/api/studio/runs/not-a-run", headers=auth(USER)).status_code, 404)
        self.assertNotIn(pid, [x["project_id"] for x in self.client.get("/api/studio/lessons", headers=auth(OTHER)).json()["lessons"]])
        bad = [{"text": "x"}, {"system_prompt": "", "text": "x"}, {"system_prompt": "p" * 60001, "text": "x"},
               {"system_prompt": "p"}, {"system_prompt": "p", "text": "x", "source": {"document_id": "a" * 32, "analysis_id": "b" * 32}},
               {"system_prompt": "p", "text": "   "}, {"system_prompt": "p", "text": "x" * 262145},
               {"system_prompt": "p", "source": {"document_id": "../x", "analysis_id": 5}}, {"system_prompt": "p", "text": "x", "names": "n"},
               {"system_prompt": "p", "text": "x", "model": "bad model!"}, {"system_prompt": "p", "text": "x", "cinematic_style": "x"},
               {"system_prompt": "p", "text": "x", "provider": 7}]
        with mock.patch.object(server.lesson_script_service, "env", env()):
            for body in bad:
                with self.subTest(body=str(body)[:60]):
                    self.assertEqual(self.client.post("/api/studio/lessons", json=body, headers=auth(USER)).status_code, 422)
            self.assertEqual(self.client.post("/api/studio/lessons", content=b"[1,", headers={**auth(USER), "Content-Type": "application/json"}).status_code, 422)
            big = b'{"system_prompt": "p", "text": "' + b"x" * (studio.MAX_REQUEST_BYTES + 10) + b'"}'
            self.assertEqual(self.client.post("/api/studio/lessons", content=big, headers={**auth(USER), "Content-Type": "application/json"}).status_code, 413)
        # no writer set up (no key, not a test server): 503 in plain words, no secret, no provider detail
        with mock.patch.object(server.lesson_script_service, "env", {"AI_GENERATION_ENABLED": "1"}):
            r = self.client.post("/api/studio/lessons", json={"system_prompt": "p", "text": "x", "provider": "gemini"}, headers=auth(USER))
            self.assertEqual((r.status_code, r.json()["detail"]), (503, studio.NO_WRITER))
            self.assertEqual(self.client.post("/api/studio/lessons", json={"system_prompt": "p", "text": "x", "provider": "claude"},
                                              headers=auth(USER)).status_code, 422)
            self.assertEqual(self.client.get("/api/studio/lessons", headers=auth(USER)).json()["writer"],
                             {"available": False, "provider": None, "stand_in": False})
        with mock.patch.object(server.lesson_script_service, "env", {"AI_GENERATION_ENABLED": "1", "GEMINI_API_KEY": "k"}):
            self.assertEqual(self.client.get("/api/studio/lessons", headers=auth(USER)).json()["writer"],
                             {"available": True, "provider": "gemini", "stand_in": False})
        with mock.patch.object(server.lesson_script_service, "env", env()):
            self.assertEqual(self.client.get("/api/studio/lessons", headers=auth(USER)).json()["writer"],
                             {"available": True, "provider": "fake", "stand_in": True})


class RecoveryTest(Base):
    def run_service(self, service, text):
        async def go():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                run, _attached = service.start(db, user, {"system_prompt": PROMPT, "text": text, "source": None, "names": None,
                                                          "provider": "fake", "model": "stand-in", "cinematic_style": None})
                try:
                    await service.tasks[run.id]
                except Crash:
                    pass
                return run.id
        return asyncio.run(go())

    def expire(self, run_id):
        with database.SessionLocal() as db:  # the worker died: its lease runs out
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)})
            db.commit()

    def recover(self, service):
        media = AIMediaService(server.asset_library, server.ai_cache, ai_providers.ProviderRegistry(env=env(), video_mode=lambda: "fake", fake=True),
                               static_dir=server.STATIC_DIR, generation_enabled=lambda: False, env=env(), log=False)
        manager = RecoveryManager(media, env=env())
        manager.register("lesson_script", service)

        async def sweep():
            tasks = await manager.sweep()
            for task in tasks:
                await task
            return len(tasks)
        return asyncio.run(sweep())

    def test_an_interrupted_run_is_written_again_after_a_restart(self):
        first = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(first, "write", side_effect=Crash("power cut")):
            run_id = self.run_service(first, "Magnets attract iron. Magnets have two poles.")
        self.assertEqual((self.run_row(run_id).status, self.projects_of_run(run_id)), ("running", []))
        self.expire(run_id)
        second = studio.LessonScriptService(server.save_lesson, env=env(), log=False)  # a restarted server
        calls = []
        real = second.write
        with mock.patch.object(second, "write", side_effect=lambda request: (calls.append(1), real(request))[1]):
            self.assertEqual(self.recover(second), 1)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.recovery_count, len(calls)), ("completed", 1, 1))
        self.assertEqual(self.projects_of_run(run_id), [json.loads(run.detail)["project_id"]])

    def test_a_run_that_saved_its_lesson_finishes_without_writing_again(self):
        first = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        real_finish = first._finish

        def crash_on_completion(db, run_id, owner, state, **fields):
            if state == "completed":
                raise Crash("power cut after the save")
            return real_finish(db, run_id, owner, state, **fields)
        with mock.patch.object(first, "_finish", side_effect=crash_on_completion):
            run_id = self.run_service(first, "Sound is a wave that travels through air.")
        saved = self.projects_of_run(run_id)
        self.assertEqual((self.run_row(run_id).status, len(saved)), ("running", 1))
        self.expire(run_id)
        second = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(second, "write", side_effect=AssertionError("the model must not be asked again")):
            self.assertEqual(self.recover(second), 1)
        run = self.run_row(run_id)
        self.assertEqual((run.status, json.loads(run.detail)["project_id"]), ("completed", saved[0]))
        self.assertEqual(self.projects_of_run(run_id), saved)  # one lesson, not two

    def test_a_cancel_while_the_worker_is_gone_is_finished_by_recovery(self):
        first = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(first, "write", side_effect=Crash("power cut")):
            run_id = self.run_service(first, "Plants need water.")
        r = self.client.post(f"/api/studio/runs/{run_id}/cancel", headers=auth(USER))
        self.assertEqual(r.json()["status"], "cancel_requested")
        self.expire(run_id)
        second = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(second, "write", side_effect=AssertionError("never written after a cancel")):
            self.recover(second)
        self.assertEqual(self.run_row(run_id).status, "cancelled")
        self.assertEqual(self.projects_of_run(run_id), [])

    def saved_then_crashed(self, text):
        """A run that saved its lesson and died before it was marked finished: (run id, the lesson it saved)."""
        first = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        real_finish = first._finish

        def crash_on_completion(db, run_id, owner, state, **fields):
            if state == "completed":
                raise Crash("power cut after the save")
            return real_finish(db, run_id, owner, state, **fields)
        with mock.patch.object(first, "_finish", side_effect=crash_on_completion):
            run_id = self.run_service(first, text)
        saved = self.projects_of_run(run_id)
        self.assertEqual(len(saved), 1)
        return run_id, saved[0]

    def test_the_saved_lesson_is_found_however_many_lessons_were_saved_since(self):
        run_id, saved = self.saved_then_crashed("Rain falls from clouds. Clouds are made of water drops.")
        copy = self.payload(saved)  # a later version saved from it (it names the run too), then many other lessons
        later = self.client.post("/save-history", json=copy, headers=auth(USER)).json()["id"]
        for k in range(25):
            self.save([{"type": "content", "title": f"Other {k}", "narration": "x"}])
        self.expire(run_id)
        second = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(second, "write", side_effect=AssertionError("the model must not be asked again")):
            self.assertEqual(self.recover(second), 1)
        run = self.run_row(run_id)
        self.assertEqual((run.status, json.loads(run.detail)["project_id"]), ("completed", saved))  # the lesson it saved, not a copy
        self.assertEqual(sorted(self.projects_of_run(run_id)), [saved, later])  # and no second lesson written

    def test_a_cancel_after_the_save_keeps_the_saved_lesson(self):
        run_id, saved = self.saved_then_crashed("Snow is frozen water. Snow falls in winter.")
        self.assertEqual(self.client.post(f"/api/studio/runs/{run_id}/cancel", headers=auth(USER)).json()["status"], "cancel_requested")
        self.expire(run_id)
        second = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(second, "write", side_effect=AssertionError("never written again")):
            self.recover(second)
        view = self.client.get(f"/api/studio/runs/{run_id}", headers=auth(USER)).json()
        self.assertEqual((view["status"], view["project_id"]), ("completed", saved))  # the saved lesson is not hidden
        self.assertEqual(self.projects_of_run(run_id), [saved])

    def test_a_stand_in_run_is_never_written_on_a_server_without_the_stand_in(self):
        first = studio.LessonScriptService(server.save_lesson, env=env(), log=False)
        with mock.patch.object(first, "write", side_effect=Crash("power cut")):
            run_id = self.run_service(first, "Ice is frozen water.")
        self.expire(run_id)
        real_server = studio.LessonScriptService(server.save_lesson, env={"AI_GENERATION_ENABLED": "1", "GEMINI_API_KEY": "k"}, log=False)
        with mock.patch.object(real_server, "write", side_effect=AssertionError("a stand-in run is never written for real")):
            self.recover(real_server)
        run = self.run_row(run_id)
        self.assertEqual((run.status, run.error_message), ("failed", studio.FAILED_MESSAGE))
        self.assertEqual(self.projects_of_run(run_id), [])


class LessonStateTest(Base):
    def media_run(self, pid, status, scene_index=0, slot="main", request=None, error=None, user_id=None):
        uid = user_id or self.uid
        with database.SessionLocal() as db:
            run = models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{uid}", user_id=uid, media_type="video", status=status,
                                         project_id=pid, scene_index=scene_index, slot=slot, created_at=datetime.datetime.utcnow(),
                                         request=json.dumps(request or {"prompt": "x"}), error_message=error, attempts=0, recovery_count=0)
            db.add(run)
            db.commit()
            return run.id

    def export(self, pid, status="COMPLETED", fingerprint=None):
        export_id = uuid.uuid4().hex
        with database.SessionLocal() as db:
            now = datetime.datetime.utcnow()
            db.add(models.VideoExport(id=export_id, project_id=pid, user_id=self.uid, title="Studio", source="lesson", status=status,
                                      created_at=now, updated_at=now, completed_at=now if status == "COMPLETED" else None))
            if fingerprint:
                key = f"{self.uid}/{export_id}.timeline.json"
                path = exports.STORAGE.path(key)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"lesson": {"project_id": pid, "fingerprint": fingerprint, "revision": "r1"}, "scenes": [], "cues": []}, f)
                db.add(models.ExportOutput(export_id=export_id, kind="timeline", status="ready", storage_key=key))
            db.commit()
        return export_id

    def scenes(self):
        return [{"type": "ai_video", "scene_id": "s-aaaaaaaaaaa1", "title": "Bridge", "prompt": f"a bridge {uuid.uuid4().hex[:6]}",
                 "narration": "A bridge.", "source": {"origin": "source", "refs": [1], "coverage": 1.0}},
                {"type": "content", "scene_id": "s-aaaaaaaaaaa2", "title": "Cables", "html": "<p>Cables pull.</p>",
                 "side_panel": {"type": "image", "prompt": f"a cable {uuid.uuid4().hex[:6]}"}, "narration": "[SYNC] Cables pull.",
                 "source": {"origin": "ai", "refs": [], "coverage": 0.2}},
                {"type": "content", "scene_id": "s-aaaaaaaaaaa3", "title": "Hidden", "html": "<p>x</p>", "narration": "x",
                 "side_panel": {"type": "skill_tree"}, "edit": {"hidden": True}},
                {"type": "content", "scene_id": "s-aaaaaaaaaaa4", "title": "Edited", "html": "<p>y</p>", "narration": "y",
                 "side_panel": {"type": "skill_tree"}, "edit": {"original": {"title": "Before"}}}]

    def test_stages_follow_the_lessons_data(self):
        self.assertEqual(self.state(self.save([]))["stage"], "draft")
        pid = self.save(self.scenes(), editor={"version": 1, "generated_order": ["s-aaaaaaaaaaa2", "s-aaaaaaaaaaa1", "s-aaaaaaaaaaa3", "s-aaaaaaaaaaa4"]},
                        cinematic_style={"style": "academic"})
        state = self.state(pid)
        self.assertEqual(state["stage"], "review")
        self.assertEqual((state["scenes"], state["hidden"], state["origin"]), (4, 1, {"source": 1, "ai": 1, "edited": 1}))
        self.assertEqual(state["editor"], {"edited": 1, "moved": 1, "hidden": 1})
        self.assertEqual(state["style"], {"style": "academic", "version": 1})
        self.assertEqual((state["media"]["needed"], state["media"]["ready"]), (2, 0))
        self.assertEqual({(i["scene_index"], i["slot"], i["status"]) for i in state["media"]["items"]}, {(0, "main", "needed"), (1, "side", "needed")})
        # Visual Review's own counts: every scene visual it lists once the lesson is planned — the video, the picture and the two
        # drawn skill trees (browser check finding: only visuals to generate were counted, so the Studio disagreed with its chips)
        self.assertEqual(state["review"], {"approved": 0, "changed": 0, "pending": 4, "removed": 0})
        self.assertEqual(len(state["fingerprint"]), 64)
        self.assertEqual(state["revision"], self.client.get(f"/api/editor/{pid}", headers=auth(USER)).json()["revision"])

        running = self.media_run(pid, "queued", request={"prompt": "x", "scene_id": "s-aaaaaaaaaaa2"}, scene_index=0, slot="side")
        state = self.state(pid)
        self.assertEqual(state["stage"], "generating")  # found by its scene id: scene 2 (index 1) moved since it was asked for
        self.assertIn({"scene_index": 1, "scene_id": "s-aaaaaaaaaaa2", "slot": "side", "status": "generating", "run_id": running, "message": None},
                      state["media"]["items"])
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == running).update({"status": "failed", "error_message": "The provider failed."})
            db.commit()
        state = self.state(pid)
        self.assertEqual((state["stage"], state["media"]["failed"]), ("needs_attention", 1))
        self.assertIn("The provider failed.", [i["message"] for i in state["media"]["items"]])
        attention = self.media_run(pid, "needs_attention", slot="main", error="Retry or dismiss it.")
        self.assertEqual(self.state(pid)["media"]["attention"], 1)
        with database.SessionLocal() as db:  # both resolved: the user dismissed one and Visual Review chose another visual
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id.in_([running, attention])).update({"status": "cancelled"},
                                                                                                                  synchronize_session=False)
            db.commit()
        self.assertEqual(self.state(pid)["stage"], "review")

        put = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": {"status": "good", "counts": {"error": 0}}},
                              headers=auth(USER))
        self.assertEqual(put.status_code, 200, put.text)
        state = self.state(pid)
        self.assertEqual((state["stage"], state["quality"]), ("ready_to_export", {"status": "good", "counts": {"error": 0}, "stale": False}))
        self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": {"status": "blocked", "counts": {"blocking": 2}}},
                        headers=auth(USER))
        self.assertEqual(self.state(pid)["stage"], "needs_attention")
        self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": {"status": "review"}}, headers=auth(USER))

        exporting = self.export(pid, status="RECORDING")
        self.assertEqual(self.state(pid)["stage"], "exporting")
        with database.SessionLocal() as db:
            db.query(models.VideoExport).filter(models.VideoExport.id == exporting).update({"status": "FAILED"})
            db.commit()
        fingerprint = self.state(pid)["fingerprint"]
        self.export(pid, fingerprint="0" * 64)  # a video of an older version of the lesson
        state = self.state(pid)
        self.assertEqual((state["stage"], state["exports"]["count"], state["exports"]["latest"]["matches_lesson"]), ("ready_to_export", 2, False))
        done = self.export(pid, fingerprint=fingerprint)
        state = self.state(pid)
        self.assertEqual(state["stage"], "completed")
        self.assertEqual({k: state["exports"]["latest"][k] for k in ("id", "status", "matches_lesson")}, {"id": done, "status": "COMPLETED", "matches_lesson": True})
        listed = next(x for x in self.client.get("/api/studio/lessons", headers=auth(USER)).json()["lessons"] if x["project_id"] == pid)
        self.assertEqual({k: listed[k] for k in ("stage", "scene_count", "generating", "attention", "exported")},
                         {"stage": "completed", "scene_count": 4, "generating": 0, "attention": 0, "exported": True})

        # an edit in the editor changes what the video shows: the quality check is stale and the video no longer matches
        data = self.client.get(f"/api/editor/{pid}", headers=auth(USER)).json()
        data["scenes"][1]["title"] = "Cables (new)"
        saved = self.client.put(f"/api/editor/{pid}", json={"expected_revision": data["revision"], "scenes": data["scenes"], "editor": data["editor"]},
                                headers=auth(USER))
        self.assertEqual(saved.status_code, 200, saved.text)
        state = self.state(pid)
        self.assertEqual((state["stage"], state["quality"]["stale"], state["exports"]["latest"]["matches_lesson"]), ("review", True, False))

    # Phase 20 browser check finding: a visual the Visual Router can already show (a library match) was counted as still
    # needed, because the page's plans are not stored with the lesson. The lesson state now asks the router (it never generates).
    def test_a_visual_the_router_can_show_is_ready(self):
        from types import SimpleNamespace
        pid = self.save(self.scenes())
        with database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            scenes = json.loads(db.get(models.Project, pid).json_data)["scenes"]
            stored, _ = studio.media_state(db, user, pid, scenes)
            library_match = {(1, "side"): SimpleNamespace(requires_generation=False, asset_id="c" * 32, url=None)}
            routed, _ = studio.media_state(db, user, pid, scenes, planner=lambda *_: library_match)
            still_ai = {(1, "side"): SimpleNamespace(requires_generation=True, asset_id=None, url=None)}
            ai, _ = studio.media_state(db, user, pid, scenes, planner=lambda *_: still_ai)
            def broken(*_):
                raise RuntimeError("router down")
            failed, _ = studio.media_state(db, user, pid, scenes, planner=broken)
        self.assertEqual((stored["needed"], stored["ready"]), (2, 0))  # the video and the picture
        self.assertEqual((routed["needed"], routed["ready"]), (1, 1))  # the picture is a library match: ready
        self.assertEqual((ai["needed"], ai["ready"]), (2, 0))          # the router would still have to make it
        self.assertEqual((failed["needed"], failed["ready"]), (2, 0))  # a router failure: the stored counts, never an error
        # the server's own planner (the Visual Router with the library and the cache) runs for the lesson state
        state = self.state(pid)
        self.assertEqual(state["media"]["needed"] + state["media"]["ready"], 2)

    def test_the_fingerprint_ignores_volatile_parts(self):
        base = {"scenes": [{"type": "content", "title": "A", "visual_plan": {"main": {"asset_id": "a" * 32, "url": "/api/assets/x?token=abc"}},
                            "visual_review": {"main": {"status": "approved", "reviewed_at": "2026-01-01"}}, "video_url": "/api/assets/y/content?token=one",
                            "source": {"origin": "source", "refs": [1], "coverage": 1.0}}],
                "editor": {"version": 1}, "studio": {"version": 1, "checkpoints": {"lesson": {}}}, "subject_name": "S"}
        same = json.loads(json.dumps(base))
        same["scenes"][0]["visual_plan"]["main"]["url"] = "/api/assets/x?token=def"
        same["scenes"][0]["visual_review"]["main"]["reviewed_at"] = "2026-02-02"
        same["scenes"][0]["video_url"] = "/api/assets/y/content?token=two"
        same["scenes"][0]["source"] = {"origin": "ai", "refs": [], "coverage": 0.0}
        same["studio"] = None
        same["subject_name"] = "Other"
        # the direction the planner derives again whenever the lesson is played (browser check finding: a replay after an export
        # made the video "no longer match" with no edit)
        same["scenes"][0]["visual_direction"] = {"strategy": "explain", "camera_intent": "slow_zoom", "ai": {"at": "2026-03-03"}}
        self.assertEqual(studio.lesson_fingerprint(base), studio.lesson_fingerprint(same))
        for change in (lambda p: p["scenes"][0].update(title="B"), lambda p: p["editor"].update(captions={"visible": False}),
                       lambda p: p.update(cinematic_style={"style": "academic"}), lambda p: p["scenes"][0].update(edit={"hidden": True})):
            other = json.loads(json.dumps(base))
            change(other)
            self.assertNotEqual(studio.lesson_fingerprint(base), studio.lesson_fingerprint(other))


class CheckpointTest(Base):
    def test_a_checkpoint_never_overwrites_a_concurrent_save(self):
        pid = self.save([{"type": "content", "scene_id": "s-bbbbbbbbbbb1", "title": "One", "html": "<p>1</p>", "narration": "1"}],
                        studio={"version": 1, "generated": {"run_id": "c" * 32, "provider": "fake", "model": "stand-in", "at": "2026-10-01T00:00:00Z"}})
        real = editor_api.update_lesson
        calls = {"mutate": 0}

        def editor_save_meanwhile():  # another save (the editor's) commits after the checkpoint read the lesson
            with database.SessionLocal() as db:
                project = db.get(models.Project, pid)
                payload = json.loads(project.json_data)
                payload["scenes"][0]["title"] = "Edited meanwhile"
                project.json_data, project.updated_at = json.dumps(payload), editor_api.next_stamp(project.updated_at)
                db.commit()

        def racing(db, project, mutate, attempts=4):
            def raced(payload):
                calls["mutate"] += 1
                if calls["mutate"] == 1:
                    editor_save_meanwhile()
                return mutate(payload)
            return real(db, project, raced, attempts)
        with mock.patch.object(editor_api, "update_lesson", racing):
            r = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "lesson", "value": {"approved": True, "note": "x" * 500}},
                                headers=auth(USER))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(calls["mutate"], 2)  # lost the first compare-and-set, applied again to the newer lesson
        payload = self.payload(pid)
        self.assertEqual(payload["scenes"][0]["title"], "Edited meanwhile")  # the concurrent save is kept
        checkpoint = payload["studio"]["checkpoints"]["lesson"]
        self.assertEqual((checkpoint["approved"], len(checkpoint["note"])), (True, 300))  # bounded
        self.assertEqual(checkpoint["lesson_fingerprint"], studio.lesson_fingerprint(payload))
        self.assertEqual(payload["studio"]["generated"]["run_id"], "c" * 32)  # how it was made stays
        self.assertEqual(r.json(), {"revision": self.client.get(f"/api/editor/{pid}", headers=auth(USER)).json()["revision"],
                                    "checkpoints": payload["studio"]["checkpoints"]})

    def test_checkpoint_values(self):
        pid = self.save([{"type": "content", "title": "One", "narration": "1"}])
        bad = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "everything", "value": {}}, headers=auth(USER))
        self.assertEqual(bad.status_code, 422)
        r = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": {
            "status": "perfect", "counts": {"error": 1, "warning": -3, "notice": "2"}, "fingerprint": "abc12345", "extra": {"a": {"b": {"c": 1}}}}},
            headers=auth(USER))
        quality = r.json()["checkpoints"]["quality"]
        self.assertNotIn("status", quality)  # not a Phase 18 status
        self.assertEqual((quality["counts"], quality["fingerprint"], quality["extra"]), ({"error": 1}, "abc12345", {"a": None}))  # two levels
        removed = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": None}, headers=auth(USER))
        self.assertEqual(removed.json()["checkpoints"], {})
        unchanged = self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "export", "value": None}, headers=auth(USER))
        self.assertEqual((unchanged.status_code, unchanged.json()["checkpoints"]), (200, {}))


class SaveHistoryTest(Base):
    def test_save_history_is_unchanged_and_keeps_the_studio_data(self):
        body = {"subject_name": "Plain", "unit_name": "U", "session_number": "Session 3", "session_title": "T", "concept_map": [],
                "scenes": [{"type": "content", "title": "One", "narration": "Hi."}], "companion_sheet": "# Sheet"}
        r = self.client.post("/save-history", json=body, headers=auth(USER))
        self.assertEqual((r.status_code, set(r.json())), (200, {"status", "id", "url"}))
        self.assertEqual(r.json()["url"], f"/?project_id={r.json()['id']}")
        self.assertEqual(self.payload(r.json()["id"]), body)  # exactly the listed keys, as before
        with database.SessionLocal() as db:
            row = db.get(models.Project, r.json()["id"])
            self.assertEqual((row.subject_name, row.unit_name, row.session_number, row.session_title), ("Plain", "U", "Session 3", "T"))
        studio_data = {"version": 7, "generated": {"run_id": "d" * 32, "provider": "fake", "model": "stand-in", "at": "2026-10-01T00:00:00Z",
                                                   "source": {"document_id": "e" * 32, "analysis_id": "f" * 32}, "secret": "x"},
                       "checkpoints": {"lesson": {"approved": True, "at": "2026-10-01T00:00:00Z", "lesson_fingerprint": "0" * 64},
                                       "nonsense": {"x": 1}}, "other": 1}
        kept = self.payload(self.save(body["scenes"], studio=studio_data))["studio"]
        self.assertEqual(kept, {"version": 1, "generated": {"run_id": "d" * 32, "provider": "fake", "model": "stand-in", "at": "2026-10-01T00:00:00Z",
                                                            "source": {"document_id": "e" * 32, "analysis_id": "f" * 32}},
                                "checkpoints": {"lesson": {"approved": True, "at": "2026-10-01T00:00:00Z", "lesson_fingerprint": "0" * 64}}})
        for garbage in ({"generated": {"run_id": "not-hex"}}, {}, {"checkpoints": {"nonsense": {}}}):
            with self.subTest(garbage=garbage):
                self.assertNotIn("studio", self.payload(self.save(body["scenes"], studio=garbage)))
        nan = self.client.post("/save-history", content=b'{"subject_name": "N", "scenes": [{"x": NaN}]}',
                               headers={**auth(USER), "Content-Type": "application/json"})
        self.assertEqual(nan.status_code, 422)


class GenerationSecurityTest(Base):
    def test_generation_endpoints_refuse_another_users_lesson(self):
        theirs = self.save([{"type": "content", "title": "Theirs"}], user=OTHER)
        mine = self.save([{"type": "content", "title": "Mine"}])
        for url, body in (("/generate-ai-video", {"prompt": "a bridge", "wait": False}), ("/generate-ai-image", {"prompt": "a bridge"}),
                          ("/render", {"code": "from manim import *", "wait": False})):
            with self.subTest(url=url):
                r = self.client.post(url, json={**body, "project_id": theirs, "scene_index": 0, "slot": "main"}, headers=auth(USER))
                self.assertEqual((r.status_code, r.json()["detail"]), (404, "Lesson not found."))
                self.assertEqual(self.client.post(url, json={**body, "project_id": 999999}, headers=auth(USER)).status_code, 404)
        # the user's own lesson passes the check (here AI generation is switched off: refused for that reason, not 404)
        r = self.client.post("/generate-ai-video", json={"prompt": f"a bridge {uuid.uuid4().hex}", "project_id": mine, "scene_index": 0, "slot": "main"},
                             headers=auth(USER))
        self.assertNotEqual(r.status_code, 404)

    def test_scene_ids_travel_with_generation_requests(self):
        pid = self.save([{"type": "ai_video", "scene_id": "s-ccccccccccc1", "title": "Bridge", "prompt": f"a bridge {uuid.uuid4().hex[:8]}"},
                         {"type": "ai_video", "title": "No id", "prompt": f"a dam {uuid.uuid4().hex[:8]}"},
                         {"type": "ai_video", "scene_id": "s-ccccccccccc3", "title": "Cable", "prompt": f"a cable {uuid.uuid4().hex[:8]}"}])
        fake = ai_providers.ProviderRegistry(env=env(), video_mode=lambda: "fake", fake=True)
        with mock.patch.dict(os.environ, {"AI_GENERATION_ENABLED": "1"}), mock.patch.object(server.ai_media, "registry", fake), \
                mock.patch.object(server, "ai_registry", fake), mock.patch.object(server.ai_media, "wake", None):
            batch = self.client.post(f"/api/ai-media/lessons/{pid}/generate", json={"media": "video"}, headers=auth(USER))
            self.assertEqual(batch.status_code, 200, batch.text)
            runs = {r["scene_index"]: self.run_row(r["run_id"]) for r in batch.json()["runs"]}
            self.assertEqual(sorted(runs), [0, 1, 2])
            self.assertEqual(json.loads(runs[0].request)["scene_id"], "s-ccccccccccc1")
            self.assertNotIn("scene_id", json.loads(runs[1].request))  # a scene without its own id: found by position, as before
            self.assertEqual(json.loads(runs[2].request)["scene_id"], "s-ccccccccccc3")
            # a page request names the scene too (a malformed id is ignored)
            for scene_id, kept in (("s-ccccccccccc3", "s-ccccccccccc3"), ("../../x", None)):
                r = self.client.post("/generate-ai-video", json={"prompt": f"another cable {uuid.uuid4().hex}", "wait": False, "project_id": pid,
                                                                 "scene_index": 2, "slot": "main", "scene_id": scene_id}, headers=auth(USER))
                self.assertEqual(r.status_code, 202, r.text)
                self.assertEqual(json.loads(self.run_row(r.json()["run_id"]).request).get("scene_id"), kept)
                self.client.post(f"/api/ai-media/runs/{r.json()['run_id']}/cancel", headers=auth(USER))

    def test_a_finished_result_is_attached_to_its_scene_by_id(self):
        pid = self.save([{"type": "content", "title": "A"}])

        def run(**fields):
            values = {"id": uuid.uuid4().hex, "user_id": self.uid, "project_id": pid, "scene_index": 0, "slot": "main", "forced": False,
                      "request": json.dumps({"prompt": "x", "scene_id": "s-ddddddddddd1"}), "detail": None, **fields}
            return mock.Mock(**values)
        with mock.patch.object(server, "store_scene_plans") as store, database.SessionLocal() as db:
            server._lesson_after_generation(db, run())
            server._lesson_after_generation(db, run(scene_index=None))  # a scene id is enough
            server._lesson_after_generation(db, run(scene_index=None, request=json.dumps({"prompt": "x"})))  # neither: nothing to attach
            server._lesson_after_generation(db, run(forced=True))  # a New AI Version is applied by the user
        self.assertEqual(store.call_count, 2)
        for call in store.call_args_list:
            self.assertEqual((call.kwargs["scene_id"], call.kwargs["fill_slot"]), ("s-ddddddddddd1", "main"))

    def test_a_render_names_its_scene(self):
        pid = self.save([{"type": "simulation", "scene_id": "s-eeeeeeeeeee1", "title": "Sim", "manim_code": "x"}])
        refused = server.ManimFailed("sandbox_unavailable")
        with mock.patch.object(server.manim_service, "render", side_effect=refused) as render:
            for scene_id, kept in (("s-eeeeeeeeeee1", "s-eeeeeeeeeee1"), ("not-an-id", None)):
                self.client.post("/render", json={"code": "from manim import *", "project_id": pid, "scene_index": 0, "slot": "main",
                                                  "scene_id": scene_id}, headers=auth(USER))
                self.assertEqual(render.call_args.kwargs["scene_id"], kept)
        # the render's run keeps it, so the finished render finds its scene by id (visuals.run_request)
        import visuals
        from manim_jobs import ManimService

        class Sandbox:
            def runtime(self):
                return object()
        service = ManimService(server.asset_library, server.ai_cache, Sandbox(), static_dir=server.STATIC_DIR, env=env(), log=False)
        code = f'from manim import *\n\nclass Lesson(Scene):\n    def construct(self):\n        self.play(Write(Text("{uuid.uuid4().hex[:8]}")))\n'

        async def go():
            with database.SessionLocal() as db, mock.patch.object(service, "spawn"):
                _outcome, run = await service.render(db, db.get(models.User, self.uid), code, profile="preview", project_id=pid,
                                                     scene_index=0, slot="main", wait=False, scene_id="s-eeeeeeeeeee1")
                return run.id
        run = self.run_row(asyncio.run(go()))
        self.assertEqual((json.loads(run.request)["scene_id"], visuals.run_request(run)["scene_id"]), ("s-eeeeeeeeeee1", "s-eeeeeeeeeee1"))
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run.id).update({"status": "cancelled", "lease_owner": None})
            db.commit()


class AuditFixesTest(Base):
    """Limits and edge cases: concurrent lesson writing, request bodies, the lessons list's cost, checkpoint fingerprints."""

    def test_two_lessons_at_once_per_user_written_in_their_own_threads(self):
        threads = []
        service = server.lesson_script_service
        real = service.write

        def write(request):
            threads.append(threading.current_thread().name)
            return real(request)
        with mock.patch.object(service, "env", env(FAKE_LLM_SECONDS="0.6")), mock.patch.object(service, "write", side_effect=write), \
                TestClient(server.app) as client:
            first = self.start(client, {"text": "Copper conducts electricity."})
            second = self.start(client, {"text": "Rubber does not conduct electricity."})
            busy = client.post("/api/studio/lessons", json={"system_prompt": PROMPT, "text": "Glass is an insulator."}, headers=auth(USER))
            self.assertEqual((busy.status_code, busy.json()["detail"]), (429, studio.BUSY))
            joined = self.start(client, {"text": "Copper conducts electricity."})  # the same request still joins its run
            self.assertEqual((joined["run_id"], joined["attached"]), (first["run_id"], True))
            other = self.start(client, {"text": "Glass is an insulator."}, user=OTHER)  # another user is not limited by this one
            for run_id, user in ((first["run_id"], USER), (second["run_id"], USER), (other["run_id"], OTHER)):
                self.assertEqual(self.wait(client, run_id, user=user)["status"], "completed")
            again = self.start(client, {"text": "Glass is an insulator."})  # free again
            self.assertEqual((again["attached"], self.wait(client, again["run_id"])["status"]), (False, "completed"))
        self.assertTrue(threads and all(name.startswith("lesson-writer") for name in threads))  # never the shared executor

    def test_a_body_without_a_length_stops_at_the_limit(self):
        chunk, read = b"x" * 65536, {"n": 0}

        async def receive():
            read["n"] += 1
            if read["n"] == 1:
                return {"type": "http.request", "body": b'{"system_prompt": "p", "text": "', "more_body": True}
            if read["n"] > 2000:  # 128 MB if it were all read
                return {"type": "http.request", "body": b'"}', "more_body": False}
            return {"type": "http.request", "body": chunk, "more_body": True}
        sent = []

        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/studio/lessons", "raw_path": b"/api/studio/lessons", "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json"), (b"authorization", auth(USER)["Authorization"].encode())],
                 "client": ("test", 1), "server": ("test", 80)}
        with mock.patch.object(server.lesson_script_service, "env", env()):
            asyncio.run(server.app(scope, receive, send))
        self.assertEqual(next(m["status"] for m in sent if m["type"] == "http.response.start"), 413)
        self.assertLessEqual(read["n"], studio.MAX_REQUEST_BYTES // len(chunk) + 3)  # stopped at the limit, not at the end

    def test_characters_that_cannot_be_stored_are_refused_in_plain_words(self):
        with mock.patch.object(server.lesson_script_service, "env", env()):
            for body in ('{"system_prompt": "p", "text": "Water is wet. \\ud800 Ice is cold."}', '{"system_prompt": "p\\udfff", "text": "Water."}',
                         '{"system_prompt": "p", "text": "Water.", "names": {"session_title": "\\udc00"}}'):
                with self.subTest(body=body):
                    r = self.client.post("/api/studio/lessons", content=body.encode(), headers={**auth(USER), "Content-Type": "application/json"})
                    self.assertEqual((r.status_code, r.json()["detail"]), (422, studio.UNREADABLE))

    def test_the_lessons_list_derives_a_lesson_again_only_when_it_changed(self):
        pids = [self.save([{"type": "ai_video", "scene_id": f"s-fffffffffff{k}", "title": f"Clip {k}", "prompt": f"a river {uuid.uuid4().hex[:6]}"}])
                for k in range(3)]
        listed = lambda: {x["project_id"]: x for x in self.client.get("/api/studio/lessons", headers=auth(USER)).json()["lessons"]}
        first = listed()
        derived = []
        real = studio.lesson_state

        def counting(db, user, project, library=None):
            derived.append(project.id)
            return real(db, user, project, library)
        with mock.patch.object(studio, "lesson_state", side_effect=counting):
            self.assertEqual(listed(), first)
            self.assertEqual(derived, [])  # nothing changed: no lesson read again
            self.client.put(f"/api/studio/lessons/{pids[0]}/checkpoint", json={"name": "quality", "value": {"status": "good"}}, headers=auth(USER))
            with database.SessionLocal() as db:  # a generation starts for another lesson
                db.add(models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="video", status="queued",
                                              project_id=pids[1], scene_index=0, slot="main", created_at=datetime.datetime.utcnow(),
                                              request=json.dumps({"prompt": "x"}), attempts=0, recovery_count=0))
                db.commit()
            now = listed()
            self.assertEqual(sorted(derived), sorted(pids[:2]))  # only the two that changed
            self.assertEqual((now[pids[0]]["stage"], now[pids[1]]["stage"], now[pids[1]]["generating"]), ("ready_to_export", "generating", 1))
            self.assertEqual(now[pids[2]], first[pids[2]])
            with mock.patch.object(studio, "_summary_get", return_value=None):  # the same answer as deriving every lesson
                self.assertEqual(listed(), now)

    def test_a_checkpoint_is_for_the_lesson_the_page_checked(self):
        pid = self.save([{"type": "content", "title": "One", "narration": "1"}])
        fingerprint = self.state(pid)["fingerprint"]
        put = lambda value: self.client.put(f"/api/studio/lessons/{pid}/checkpoint", json={"name": "quality", "value": value}, headers=auth(USER))
        saved = put({"status": "good", "fingerprint": fingerprint}).json()["checkpoints"]["quality"]
        self.assertEqual(saved["lesson_fingerprint"], fingerprint)
        self.assertEqual((self.state(pid)["stage"], self.state(pid)["quality"]["stale"]), ("ready_to_export", False))
        older = "1" * 64  # checked on a copy of the lesson that has changed since: kept, and stale at once
        self.assertEqual(put({"status": "good", "fingerprint": older}).json()["checkpoints"]["quality"]["lesson_fingerprint"], older)
        self.assertEqual((self.state(pid)["stage"], self.state(pid)["quality"]["stale"]), ("review", True))
        report = put({"status": "good", "fingerprint": "abcdef0123456789"}).json()["checkpoints"]["quality"]  # a Phase 18 report's own
        self.assertEqual((report["fingerprint"], report["lesson_fingerprint"]), ("abcdef0123456789", fingerprint))


if __name__ == "__main__":
    unittest.main()


class ReviewCountsTest(unittest.TestCase):
    """Phase 20 browser check finding: the Studio's Visual Review counts skipped the scene visuals with nothing to generate
    (library choices), so they disagreed with Visual Review's own chips. They now count what Visual Review lists."""

    def test_the_counts_are_visual_reviews_own(self):
        scenes = [
            {"visual_plan": {"main": {"source": "LIBRARY", "asset_id": "a" * 32, "review_status": "changed"}}},   # a library choice
            {"visual_plan": {"side": {"source": "AI_IMAGE", "asset_id": "b" * 32}}, "visual_review": {"side": {"status": "approved"}}},
            {"visual_plan": {"main": {"source": "SKILL_TREE"}, "side": {"source": "NONE"}},
             "visual_review": {"side": {"status": "removed"}}},                                                    # drawn; removed
            {"title": "no visual plan yet"},
            {"visual_plan": {"main": {"source": "AI_VIDEO", "review_status": "bogus"}}, "visual_review": {"main": {"status": "bogus"}}},
            "not a scene",
        ]
        self.assertEqual(studio.review_counts(scenes), {"pending": 2, "approved": 1, "changed": 1, "removed": 1})



class PlannerCacheTest(Base):
    """Phase 21 (performance audit): the Visual Router's plans for the lesson state are kept while the lesson's scenes, the
    visuals the router may choose from and the AI cache are unchanged (planning a large lesson measured up to ~1 s, and the
    Studio asks every few seconds while media is made); any change plans again."""

    def test_the_plans_are_reused_until_the_lesson_or_the_library_changes(self):
        scenes = [{"type": "content", "scene_id": "s-bbbbbbbbbbb1", "title": "Cables", "html": "<p>Cables pull.</p>",
                   "side_panel": {"type": "image", "prompt": f"a cable {uuid.uuid4().hex[:6]}"}, "narration": "Cables pull."}]
        server._STUDIO_PLANS.clear()
        calls = []
        real = __import__("visuals").plan_lesson

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)
        with mock.patch("visuals.plan_lesson", side_effect=counting), database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            first = server._studio_planner(db, user, scenes)
            again = server._studio_planner(db, user, scenes)
            self.assertIs(first, again)
            self.assertEqual(len(calls), 1, "unchanged: planned once")
            changed = json.loads(json.dumps(scenes))
            changed[0]["side_panel"]["prompt"] += " at dusk"
            server._studio_planner(db, user, changed)
            self.assertEqual(len(calls), 2, "the lesson changed: planned again")
            db.add(models.Asset(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", owner_id=self.uid, kind="image", source="upload",
                                status="ready", file_name="cable.png", mime_type="image/png", storage_volume="assets",
                                storage_key=f"{self.uid}/cable.png"))
            db.commit()
            server._studio_planner(db, user, scenes)
            self.assertEqual(len(calls), 3, "a new visual in the library: planned again")
