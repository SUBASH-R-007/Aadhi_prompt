"""Backend tests for the rendered lesson export (Phase 22, renders.py): the render run, its lease and recovery decisions, the
cancel path, the reconcile of exports whose run ended without them, the registration as an ordinary lesson export, and the
page-settings whitelist.

A stand-in worker (a small Python script written below) plays render_worker.mjs's part: it reads the job from stdin, writes
the plan, the range videos (ffmpeg test pictures), the joined video and the render timeline, and reports JSON lines on
stderr. No browser is started. Uses the throwaway database of backend_env; the real projects.db is never touched.
Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
"""
import asyncio
import datetime
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import unittest
import uuid

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user  # first: sets up the throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_runs  # noqa: E402
import database  # noqa: E402
import export_outputs  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import renders  # noqa: E402
import server  # noqa: E402
from ai_runs import AttemptState, RunState  # noqa: E402

FAKE_DIR = os.path.join(TMP, "fake-render")
CONTROL = os.path.join(FAKE_DIR, "control.json")
SEEN = os.path.join(FAKE_DIR, "seen.jsonl")

# The stand-in worker. Control file: {"mode": ok | missing | page_error | hang | stop_after_range, "frames": per range}.
# It appends what it was given (never the token itself) and what it rendered to seen.jsonl.
FAKE_WORKER = textwrap.dedent(r'''
    import json, os, subprocess, sys, threading, time
    CONTROL = {control!r}
    SEEN = {seen!r}
    def emit(event):
        sys.stderr.write(json.dumps(event) + "\n"); sys.stderr.flush()
    def note(entry):
        with open(SEEN, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    job = json.loads(sys.stdin.readline())
    control = json.load(open(CONTROL, encoding="utf-8"))
    mode, frames = control.get("mode", "ok"), int(control.get("frames", 30))
    def listen():
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "cancel":
                emit({{"type": "error", "code": "cancelled", "message": "The render was cancelled."}})
                os._exit(5)
            if msg.get("type") == "token":
                note({{"token_refresh": len(msg.get("token") or "")}})
    threading.Thread(target=listen, daemon=True).start()
    ws = job["workspace"]
    note({{"argv": sys.argv[1:], "keys": sorted(k for k in job if k != "token"), "token_length": len(job.get("token") or ""),
           "missing": job["missingVisuals"], "settings": job.get("settings"), "projectId": job["projectId"],
           "fingerprint": job.get("fingerprint"), "base": job["base"], "temp": os.environ.get("TEMP"),
           "secret_in_env": any(k in os.environ for k in ("JWT_SECRET", "GEMINI_API_KEY", "OPENAI_API_KEY"))}})
    emit({{"type": "progress", "phase": "preparing", "message": "Opening the lesson"}})
    if mode == "missing":
        missing = [{{"index": 3, "title": "Tension in action", "reason": "no AI video yet"}}]
        emit({{"type": "missing", "missing": missing, "refused": True}})
        emit({{"type": "error", "code": "missing_visuals", "message": "Some scenes have no visual yet.", "missing": missing}})
        sys.exit(3)
    if mode == "page_error":
        emit({{"type": "error", "code": "page_error", "message": "The lesson page has no render mode."}})
        sys.exit(4)
    if mode == "silent":  # stops saying anything (a real hang)
        time.sleep(60)
        sys.exit(0)
    if mode == "keepalive":  # a long prepare that keeps reporting (narration made for a big lesson)
        end = time.time() + float(control.get("seconds", 5))
        while time.time() < end:
            time.sleep(0.5)
            emit({{"type": "progress", "phase": "preparing", "message": "Preparing the lesson: 3 of 140 ready (narration)…"}})
        mode = "ok"
    if mode == "spawn_child":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        note({{"child": child.pid}})
    plan_path = os.path.join(ws, "plan.json")
    ranges = [{{"index": i, "fromScene": i, "toScene": None if i == 2 else i}} for i in range(3)]
    if os.path.exists(plan_path):
        plan = json.load(open(plan_path, encoding="utf-8"))
    else:
        plan = {{"version": 1, "projectId": job["projectId"], "fingerprint": job.get("fingerprint"), "ranges": ranges,
                 "scenes": [{{"index": i, "title": "Scene %d" % (i + 1), "type": "content", "hidden": False}} for i in range(3)],
                 "lesson": None, "media": []}}
        os.makedirs(os.path.join(ws, "ranges"), exist_ok=True)
        json.dump(plan, open(plan_path, "w", encoding="utf-8"))
    emit({{"type": "plan", "ranges": plan["ranges"], "scenes": 3, "resumed": False}})
    if mode in ("hang", "spawn_child"):
        emit({{"type": "frames", "range": 0, "frames": 1, "scene": 0, "capturing": True}})
        while True:
            time.sleep(0.2)
            emit({{"type": "frames", "range": 0, "frames": 2, "scene": 0, "capturing": True}})
    rendered = []
    for r in plan["ranges"]:
        video = os.path.join(ws, "ranges", "range-%d.mp4" % r["index"])
        info = os.path.join(ws, "ranges", "range-%d.json" % r["index"])
        if os.path.exists(video) and os.path.exists(info):
            continue
        part = video + ".part.mp4"
        subprocess.run([job["ffmpeg"], "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                        "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "30",
                        "-fps_mode", "cfr", "-an", part], check=True)
        os.replace(part, video)
        t = 0.2
        json.dump({{**r, "frames": frames, "timeline": {{
            "scenes": [{{"t": 0, "title": "Scene %d" % (r["index"] + 1), "type": "content"}}],
            "cues": [{{"start": t, "end": t + 0.5, "text": "Line %d" % (r["index"] + 1)}}],
            "audio": [{{"t": t, "kind": "narration", "src": "/static/render-test-tone.wav?t=1", "offset": 0, "rate": 0.9,
                        "volume": 1, "end": t + 0.6}}],
            "notes": [{{"t": 0.1, "scene": r["index"], "text": "visual left out"}}] if r["index"] == 1 else []}}}},
                  open(info, "w", encoding="utf-8"))
        rendered.append(r["index"])
        emit({{"type": "range_done", "range": r["index"], "frames": frames}})
        if mode == "stop_after_range":
            note({{"rendered": rendered}})
            while True:
                time.sleep(0.2)
                emit({{"type": "frames", "range": 1, "frames": 1, "scene": 1, "capturing": True}})
    note({{"rendered": rendered}})
    lst = os.path.join(ws, "ranges", "list.txt")
    open(lst, "w").write("".join("file 'range-%d.mp4'\n" % r["index"] for r in plan["ranges"]))
    subprocess.run([job["ffmpeg"], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                    os.path.join(ws, "video.mp4")], check=True)
    total = frames * len(plan["ranges"])
    merged = {{"version": 1, "fps": 30, "frames": total, "duration": total / 30, "ranges": [], "scenes": [], "cues": [],
               "audio": [], "notes": [], "lesson": control.get("lesson")}}
    for i, r in enumerate(plan["ranges"]):
        tl = json.load(open(os.path.join(ws, "ranges", "range-%d.json" % r["index"]), encoding="utf-8"))["timeline"]
        off = i * frames / 30
        for key in ("scenes", "audio", "notes"):
            merged[key] += [{{**x, "t": x["t"] + off}} for x in tl[key]]
        merged["cues"] += [{{**c, "start": c["start"] + off, "end": c["end"] + off}} for c in tl["cues"]]
    json.dump(merged, open(os.path.join(ws, "timeline.full.json"), "w", encoding="utf-8"))
    emit({{"type": "done", "frames": total, "duration": total / 30}})
    sys.exit(0)
''')


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class RenderBase(unittest.TestCase):
    """A throwaway render service with the stand-in worker, a lesson of the user's, and helpers (no tests of its own)."""
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        os.makedirs(FAKE_DIR, exist_ok=True)
        cls.worker = os.path.join(FAKE_DIR, "fake_worker.py")
        with open(cls.worker, "w", encoding="utf-8") as f:
            f.write(FAKE_WORKER.format(control=CONTROL, seen=SEEN))
        cls.render_dir = os.path.join(TMP, "render")
        cls.env = {"RENDER_WORKER": cls.worker, "RENDER_DIR": cls.render_dir, "RENDER_STALL_SECONDS": "30",
                   "AI_JOB_HEARTBEAT_SECONDS": "1", "AI_JOB_LEASE_SECONDS": "60"}
        cls.user_id = ensure_user("renderer")
        cls.other_id = ensure_user("someone-else")
        # the narration file the stand-in's timeline names, in the server's STATIC_DIR
        cls.tone = os.path.join(server.STATIC_DIR, "render-test-tone.wav")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", cls.tone], check=True)
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.tone):
            os.remove(cls.tone)

    def setUp(self):
        self.control({"mode": "ok", "frames": 30})
        if os.path.exists(SEEN):
            os.remove(SEEN)
        self.tokens = []
        self.service = renders.RenderService(server.asset_library, register_asset=None, make_token=self.make_token,
                                             env=dict(self.env), log=False)
        self.spawned = []
        self.service.spawn = lambda run_id, owner, manager=False: self.spawned.append((run_id, owner))
        db = database.SessionLocal()
        project = models.Project(user_id=self.user_id, subject_name="Strength of Materials", session_number="Lesson 1",
                                 session_title="Stress", json_data=json.dumps({"scenes": [{"type": "title", "title": "Stress"}]}))
        db.add(project)
        db.commit()
        self.project_id = project.id
        db.close()

    def tearDown(self):
        # every test starts with no render active for the user (one at a time per user)
        db = database.SessionLocal()
        db.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "lesson_render",
                                                models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).update(
            {"status": RunState.CANCELLED, "lease_owner": None}, synchronize_session=False)
        db.commit()
        db.close()

    def start_again(self):
        self.spawned.clear()
        return self.start()

    def make_token(self, username, minutes, run_id):
        token = server._render_token(username, minutes, run_id)
        self.tokens.append(token)
        return token

    def control(self, value):
        with open(CONTROL, "w", encoding="utf-8") as f:
            json.dump(value, f)

    def seen(self):
        if not os.path.exists(SEEN):
            return []
        with open(SEEN, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def start(self, missing="refuse", page_settings=None):
        db = database.SessionLocal()
        try:
            user = db.get(models.User, self.user_id)
            project = db.get(models.Project, self.project_id)
            export, run = self.service.start(db, user, project, missing, page_settings or {}, "http://127.0.0.1:9729")
            owner = self.spawned[-1][1]
            return export.id, run.id, owner
        finally:
            db.close()

    def execute(self, run_id, owner, manager=False):
        return asyncio.run(self.service.execute(run_id, owner, manager=manager))

    def get(self, model, key):
        db = database.SessionLocal()
        try:
            return db.get(model, key)
        finally:
            db.close()

    def attempts(self, run_id):
        db = database.SessionLocal()
        try:
            return ai_runs.attempts_of(db, run_id)
        finally:
            db.close()

    def api_export(self, export_id, user="renderer"):
        res = self.client.get(f"/api/exports/{export_id}", headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def expire_lease(self, run_id, status=RunState.RUNNING):
        db = database.SessionLocal()
        try:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"status": status, "lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=5)})
            db.commit()
        finally:
            db.close()

    def claim(self, run_id):
        """What the recovery manager does with an interrupted render: claim it as recovering."""
        db = database.SessionLocal()
        try:
            owner = ai_runs.worker_token("recovery")
            run = db.get(models.AIGenerationRun, run_id)
            self.assertTrue(ai_runs.claim(db, run_id, owner, RunState.RECOVERING, [run.status], 60,
                                          recovery_count=(run.recovery_count or 0) + 1))
            return owner
        finally:
            db.close()

@unittest.skipUnless(FFMPEG and export_outputs.h264_encoder(), "needs ffmpeg with libx264")
class RenderRunTest(RenderBase):
    # ---- creating a render -------------------------------------------------------------------------------------------------

    def test_the_run_and_its_export_are_created_together(self):
        export_id, run_id, owner = self.start(page_settings={"tts": {"rate": 1.1}})
        export = self.get(models.VideoExport, export_id)
        run = self.get(models.AIGenerationRun, run_id)
        self.assertEqual((export.status, export.source, export.project_id), ("RECORDING", "lesson", self.project_id))
        self.assertEqual(export.title, "Strength of Materials Lesson 1 Stress")
        self.assertEqual((run.kind, run.status, run.request_hash, run.scope_key), ("lesson_render", RunState.RUNNING, export_id,
                                                                                   f"user:{self.user_id}"))
        self.assertIsNone(run.project_id)  # the editor would treat it as media being generated and refuse scene moves
        self.assertEqual(run.lease_owner, owner)
        self.assertGreater(run.lease_expires_at, datetime.datetime.utcnow())
        self.assertEqual(json.loads(run.request)["project_id"], self.project_id)
        # one active render per user
        with self.assertRaises(renders.RenderFailed) as busy:
            self.start()
        self.assertEqual(busy.exception.code, "busy")

    def test_the_route_checks_the_lesson_the_choice_and_the_settings(self):
        svc = server.render_service
        calls = []
        original = (svc.spawn, svc.availability)
        svc.spawn = lambda run_id, owner, manager=False: calls.append(run_id)
        svc.availability = lambda: (True, "")
        try:
            body = {"project_id": self.project_id, "missing_visuals": "refuse",
                    "page_settings": {"aadhi.cinematic": {"family": "classic"}, "aadhi_ai_visuals": "images", "rate": 0.9,
                                      "voice": "en-US-GuyNeural", "tts_engine": "default", "gemini_voice": "Puck"}}
            self.assertEqual(self.client.post("/api/exports/render", json=body, headers=auth("someone-else")).status_code, 404)
            self.assertEqual(self.client.post("/api/exports/render", json={**body, "missing_visuals": "maybe"},
                                              headers=auth("renderer")).status_code, 422)
            bad = self.client.post("/api/exports/render", json={**body, "page_settings": {"jwt_token": "x"}}, headers=auth("renderer"))
            self.assertEqual(bad.status_code, 422)
            self.assertIn("jwt_token", bad.json()["detail"])
            res = self.client.post("/api/exports/render", json=body, headers=auth("renderer"))
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["status"], "RECORDING")
            self.assertEqual(data["render"]["phase"], "preparing")
            self.assertEqual(data["render"]["frames_done"], 0)
            self.assertEqual(len(calls), 1)
            run = self.get(models.AIGenerationRun, data["render"]["run_id"])
            request = json.loads(run.request)
            self.assertEqual(request["page_settings"], {"storage": {"aadhi.cinematic": {"family": "classic"}, "aadhi_ai_visuals": "images"},
                                                        "tts": {"engine": "default", "voice": "en-US-GuyNeural", "geminiVoice": "Puck", "rate": 0.9}})
            self.assertTrue(request["base_url"].startswith("http://127.0.0.1:"))
            again = self.client.post("/api/exports/render", json=body, headers=auth("renderer"))
            self.assertEqual(again.status_code, 429)
            self.assertIn("already being rendered", again.json()["detail"])
            # the page may only cancel a render export
            refused = self.client.patch(f"/api/exports/{data['id']}", json={"status": "UPLOADING"}, headers=auth("renderer"))
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["detail"], exports.RENDER_ONLY_CANCEL)
            self.assertEqual(self.client.patch(f"/api/exports/{data['id']}", json={"stage": "x"}, headers=auth("renderer")).status_code, 409)
            cancelled = self.client.patch(f"/api/exports/{data['id']}", json={"status": "CANCELLED"}, headers=auth("renderer"))
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["status"], "CANCELLED")
            self.assertEqual(self.get(models.AIGenerationRun, run.id).status, RunState.CANCEL_REQUESTED)
            svc.availability = lambda: (False, "Rendering videos is switched off on this server. Use Record the screen instead.")
            off = self.client.post("/api/exports/render", json=body, headers=auth("renderer"))
            self.assertEqual(off.status_code, 503)
        finally:
            svc.spawn, svc.availability = original
            db = database.SessionLocal()
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.kind == "lesson_render",
                                                    models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).update(
                {"status": RunState.CANCELLED}, synchronize_session=False)
            db.commit()
            db.close()

    def test_page_settings_whitelist(self):
        clean = renders.clean_page_settings
        self.assertEqual(clean(None), {})
        self.assertEqual(clean({}), {})
        self.assertEqual(clean({"aadhi.presenter": {"mode": "teacher"}, "rate": 1}),
                         {"storage": {"aadhi.presenter": {"mode": "teacher"}}, "tts": {"rate": 1.0}})
        # measured as the page measures it (compact UTF-8): settings with accents near the limit are taken
        self.assertIn("storage", clean({"aadhi.cinematic": {"caption": "é" * 7000}}))
        for bad in ({"aadhi.studio.run": "x"}, {"aadhi_ai_visuals": "everything"}, {"rate": 2}, {"rate": True},
                    {"voice": "<script>"}, {"aadhi.cinematic": "classic"}, {"aadhi.cinematic": {"x": "y" * 17000}}, ["x"]):
            with self.assertRaises(ValueError, msg=bad):
                clean(bad)

    # ---- one render ----------------------------------------------------------------------------------------------------------

    def test_a_render_is_saved_as_an_ordinary_lesson_export(self):
        lesson = {"project_id": self.project_id, "fingerprint": "b" * 64, "revision": "2026-10-04T10:00:00"}
        self.control({"mode": "ok", "frames": 30, "lesson": lesson})
        export_id, run_id, owner = self.start(page_settings={"storage": {"aadhi.cinematic": {"a": 1}}, "tts": {"rate": 1.2}})
        self.assertEqual(self.execute(run_id, owner), export_id)
        export = self.get(models.VideoExport, export_id)
        self.assertEqual((export.status, export.format, export.mime_type), ("COMPLETED", "mp4", "video/mp4"))
        self.assertEqual(export.storage_key, f"{self.user_id}/{export_id}.mp4")
        self.assertTrue(exports.STORAGE.exists(export.storage_key))
        self.assertAlmostEqual(export.duration_seconds, 3.0, delta=0.05)
        self.assertTrue(export.has_audio)
        self.assertEqual(export_outputs.video_frames(exports.STORAGE.path(export.storage_key)), 90)
        run = self.get(models.AIGenerationRun, run_id)
        self.assertEqual(run.status, RunState.COMPLETED)
        self.assertIsNone(run.lease_owner)
        # the export API: Videos panel fields, the lesson link and the render's progress
        data = self.api_export(export_id)
        self.assertEqual(data["render"]["phase"], "done")
        self.assertEqual(data["render"]["frames_done"], 90)
        self.assertEqual(data["render"]["frames_total"], 90)
        self.assertEqual(data["render"]["notes"], [{"t": 1.1, "scene": 1, "text": "visual left out"}])
        self.assertEqual(data["lesson_fingerprint"], "b" * 64)
        self.assertEqual({k: v["status"] for k, v in data["outputs"].items()}, {"vtt": "ready", "chapters": "ready", "mp4": "pending"})
        db = database.SessionLocal()
        try:
            latest = exports.latest_lesson_export(db, self.user_id, self.project_id, completed=True)
        finally:
            db.close()
        self.assertEqual((latest["id"], latest["fingerprint"]), (export_id, "b" * 64))
        vtt = self.client.get(f"/api/exports/{export_id}/outputs/vtt", headers=auth("renderer")).text
        self.assertIn("Line 2", vtt)
        self.assertIn("00:00:01.200 --> 00:00:01.700", vtt)
        # the worker: a fixed argument list; the job (with the token) on stdin only, no secret in its environment
        first = self.seen()[0]
        self.assertEqual(first["argv"], [])
        self.assertGreater(first["token_length"], 20)
        self.assertFalse(first["secret_in_env"])
        self.assertTrue(first["temp"].startswith(self.render_dir))
        self.assertEqual(first["missing"], "refuse")
        self.assertIn("prerollMode", first["keys"])
        self.assertIn("layout", first["keys"])
        self.assertEqual(first["settings"], {"storage": {"aadhi.cinematic": {"a": 1}}, "tts": {"rate": 1.2}})
        self.assertRegex(first["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["base"], "http://127.0.0.1:9729")
        self.assertTrue(all(t not in json.dumps(first) for t in self.tokens))
        # the workspace keeps the timelines, not the videos
        ws = self.service.workspace(export_id)
        self.assertTrue(os.path.isfile(os.path.join(ws, "timeline.full.json")))
        self.assertFalse(os.path.exists(os.path.join(ws, "video.mp4")))
        self.assertFalse([n for n in os.listdir(os.path.join(ws, "ranges")) if n.endswith(".mp4")])

    def test_missing_visuals_refuse_the_render_with_the_scenes_named(self):
        self.control({"mode": "missing"})
        export_id, run_id, owner = self.start()
        self.execute(run_id, owner)
        data = self.api_export(export_id)
        self.assertEqual(data["status"], "FAILED")
        self.assertEqual(data["render"]["error_code"], "missing_visuals")
        self.assertEqual(data["render"]["missing"], [{"index": 3, "title": "Tension in action", "reason": "no AI video yet"}])
        self.assertIn('Scene 4 "Tension in action"', data["error_message"])
        self.assertIn("Render without these visuals", data["error_message"])
        self.assertEqual(self.get(models.AIGenerationRun, run_id).status, RunState.FAILED)
        self.assertFalse(os.path.exists(self.service.workspace(export_id)))

    def test_a_page_failure_fails_in_plain_words(self):
        self.control({"mode": "page_error"})
        export_id, run_id, owner = self.start()
        self.execute(run_id, owner)
        data = self.api_export(export_id)
        self.assertEqual((data["status"], data["render"]["error_code"]), ("FAILED", "page_error"))
        self.assertEqual(data["error_message"], "The lesson page has no render mode. Your lesson is unchanged; try again.")

    # ---- cancel --------------------------------------------------------------------------------------------------------------

    def test_cancel_from_the_videos_panel_stops_the_whole_worker(self):
        self.control({"mode": "spawn_child"})
        export_id, run_id, owner = self.start()

        async def scenario():
            task = asyncio.ensure_future(self.service.execute(run_id, owner))
            for _ in range(200):
                if any("child" in e for e in self.seen()):
                    break
                await asyncio.sleep(0.05)
            res = await asyncio.to_thread(self.client.patch, f"/api/exports/{export_id}", json={"status": "CANCELLED"},
                                          headers=auth("renderer"))
            self.assertEqual(res.status_code, 200)
            await asyncio.wait_for(task, 30)
        started = time.time()
        asyncio.run(scenario())
        self.assertLess(time.time() - started, 30)
        self.assertEqual(self.get(models.VideoExport, export_id).status, "CANCELLED")
        run = self.get(models.AIGenerationRun, run_id)
        self.assertEqual(run.status, RunState.CANCELLED)
        child = next(e["child"] for e in self.seen() if "child" in e)
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "a process the worker started outlived the cancelled render")
        self.assertFalse(os.path.exists(self.service.workspace(export_id)))

    def test_the_generic_run_cancel_stops_it_too(self):
        self.control({"mode": "hang"})
        export_id, run_id, owner = self.start()

        async def scenario():
            task = asyncio.ensure_future(self.service.execute(run_id, owner))
            self.service.tasks[run_id] = task
            await asyncio.sleep(1.5)
            db = database.SessionLocal()
            try:
                self.assertEqual(self.service.request_cancel(db, db.get(models.AIGenerationRun, run_id)), "cancelling")
            finally:
                db.close()
            await asyncio.wait_for(task, 30)
        asyncio.run(scenario())
        self.assertEqual(self.get(models.VideoExport, export_id).status, "CANCELLED")
        self.assertEqual(self.get(models.AIGenerationRun, run_id).status, RunState.CANCELLED)

    # ---- interruption and recovery --------------------------------------------------------------------------------------------

    def interrupt_after_first_range(self):
        """The server stops while range 0 is done and range 1 renders (the shutdown cancels the task: no flag)."""
        self.control({"mode": "stop_after_range", "frames": 30})
        export_id, run_id, owner = self.start()

        async def scenario():
            task = asyncio.ensure_future(self.service.execute(run_id, owner))
            for _ in range(400):
                if any("rendered" in e for e in self.seen()):
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.wait([task], timeout=30)
        asyncio.run(scenario())
        return export_id, run_id

    def test_a_restart_keeps_finished_ranges_and_renders_the_rest(self):
        export_id, run_id = self.interrupt_after_first_range()
        run = self.get(models.AIGenerationRun, run_id)
        export = self.get(models.VideoExport, export_id)
        self.assertEqual(run.status, RunState.RUNNING)  # given up at once (lease expired now), not failed
        self.assertLessEqual(run.lease_expires_at, datetime.datetime.utcnow())
        self.assertEqual(export.status, "RECORDING")
        self.assertEqual(export.stage, renders.PAUSED)
        ws = self.service.workspace(export_id)
        self.assertTrue(os.path.isfile(os.path.join(ws, "ranges", "range-0.mp4")))
        self.assertEqual(self.attempts(run_id)[-1].state, AttemptState.RENDERING)
        # recovery picks it up: only ranges 1 and 2 are rendered again, then mixed and saved
        self.control({"mode": "ok", "frames": 30})
        owner = self.claim(run_id)
        self.execute(run_id, owner, manager=True)
        export = self.get(models.VideoExport, export_id)
        self.assertEqual(export.status, "COMPLETED", export.error_message)
        self.assertEqual(self.seen()[-1], {"rendered": [1, 2]})
        attempts = self.attempts(run_id)
        self.assertEqual([a.state for a in attempts], [AttemptState.LOST, AttemptState.COMPLETED])
        self.assertEqual(json.loads(attempts[0].detail)["recovered_how"], "kept_ranges")
        self.assertEqual(json.loads(attempts[1].detail)["kept_ranges"], 1)
        self.assertEqual(self.api_export(export_id)["render"]["recovered"], 1)

    def test_a_lesson_changed_meanwhile_is_rendered_again_from_the_start(self):
        export_id, run_id = self.interrupt_after_first_range()
        db = database.SessionLocal()
        project = db.get(models.Project, self.project_id)
        project.json_data = json.dumps({"scenes": [{"type": "title", "title": "Stress, edited"}]})
        db.commit()
        db.close()
        self.control({"mode": "ok", "frames": 30})
        self.execute(run_id, self.claim(run_id), manager=True)
        self.assertEqual(self.get(models.VideoExport, export_id).status, "COMPLETED")
        self.assertEqual(self.seen()[-1], {"rendered": [0, 1, 2]})
        self.assertEqual(json.loads(self.attempts(run_id)[0].detail)["recovered_how"], "rendered_again_changed")

    def test_after_the_capture_a_restart_only_mixes_and_saves(self):
        self.control({"mode": "ok", "frames": 30})
        export_id, run_id, owner = self.start()
        mixes = []
        original_mix = export_outputs.mix_render
        # the first attempt captures, then "dies" while mixing
        def dying_mix(*args, **kwargs):
            mixes.append(1)
            raise asyncio.CancelledError()
        export_outputs.mix_render = dying_mix
        try:
            self.execute(run_id, owner)
        finally:
            export_outputs.mix_render = original_mix
        self.assertEqual(self.get(models.VideoExport, export_id).status, "RECORDING")
        captures = len([e for e in self.seen() if "rendered" in e])
        self.expire_lease(run_id)
        self.execute(run_id, self.claim(run_id), manager=True)
        self.assertEqual(self.get(models.VideoExport, export_id).status, "COMPLETED")
        self.assertEqual(len([e for e in self.seen() if "rendered" in e]), captures)  # nothing captured again
        self.assertEqual(json.loads(self.attempts(run_id)[0].detail)["recovered_how"], "mixed_again")

    def test_a_finished_mix_is_only_registered_after_a_restart(self):
        self.control({"mode": "ok", "frames": 30})
        export_id, run_id, owner = self.start()
        original = exports._complete_export
        def dying_register(*args, **kwargs):
            raise asyncio.CancelledError()
        exports._complete_export = dying_register
        try:
            self.execute(run_id, owner)
        finally:
            exports._complete_export = original
        self.assertEqual(self.attempts(run_id)[-1].state, AttemptState.REGISTERING)
        captures = len(self.seen())
        original_mix = export_outputs.mix_render
        export_outputs.mix_render = lambda *a, **k: self.fail("mixed again")
        try:
            self.expire_lease(run_id)
            self.execute(run_id, self.claim(run_id), manager=True)
        finally:
            export_outputs.mix_render = original_mix
        self.assertEqual(self.get(models.VideoExport, export_id).status, "COMPLETED")
        self.assertEqual(len(self.seen()), captures)
        self.assertEqual(json.loads(self.attempts(run_id)[-1].detail)["recovered_how"], "registered")

    def test_a_render_recovery_gave_up_on_does_not_stay_recording(self):
        export_id, run_id, _owner = self.start()
        db = database.SessionLocal()
        db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
            {"status": RunState.NEEDS_ATTENTION, "lease_owner": None, "error_category": "interrupted",
             "error_message": "This generation was interrupted 3 times"})
        db.commit()
        db.close()
        data = self.api_export(export_id)
        self.assertEqual(data["status"], "FAILED")
        self.assertEqual(data["error_message"], exports.RENDER_GAVE_UP)
        self.assertEqual(data["render"]["error_code"], "interrupted")
        self.assertEqual(self.get(models.AIGenerationRun, run_id).status, RunState.CANCELLED)  # nothing left to decide
        # the sweep settles the others (a run cancelled elsewhere)
        export2, run2, _ = self.start_again()
        db = database.SessionLocal()
        db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run2).update({"status": RunState.CANCELLED, "lease_owner": None})
        db.commit()
        self.assertGreaterEqual(self.service.settle(db), 1)
        db.close()
        self.assertEqual(self.get(models.VideoExport, export2).status, "CANCELLED")


    def test_the_sweep_removes_what_no_render_needs(self):
        jobs = os.path.join(self.render_dir, "jobs")
        frames = os.path.join(self.render_dir, "frames")
        export_id, run_id, owner = self.start()
        active_ws = self.service.workspace(export_id)
        os.makedirs(active_ws, exist_ok=True)
        gone = os.path.join(jobs, f"job-{uuid.uuid4().hex}")  # no export at all
        os.makedirs(gone)
        stale_set = os.path.join(frames, "0123456789abcdef")
        os.makedirs(stale_set)
        with open(os.path.join(stale_set, "manifest.json"), "w") as f:
            f.write("{}")
        fresh_set = os.path.join(frames, "fedcba9876543210")
        os.makedirs(fresh_set)
        with open(os.path.join(fresh_set, "manifest.json"), "w") as f:
            f.write("{}")
        old = time.time() - 30 * 86400
        for path in (gone, os.path.join(stale_set, "manifest.json")):
            os.utime(path, (old, old))
        db = database.SessionLocal()
        try:
            self.service.sweep(db)
        finally:
            db.close()
        self.assertTrue(os.path.isdir(active_ws))  # its render is still active
        self.assertFalse(os.path.exists(gone))
        self.assertFalse(os.path.exists(stale_set))
        self.assertTrue(os.path.isdir(fresh_set))

    def test_sounds_resolve_only_to_files_the_user_may_use(self):
        db = database.SessionLocal()
        try:
            resolve = self.service.resolver(db, self.user_id)
            self.assertEqual(resolve("http://127.0.0.1:9729/static/render-test-tone.wav?t=5"), os.path.abspath(self.tone))
            self.assertTrue(resolve("/video_template/logo_animation.mp4").endswith(os.path.join("video_template", "logo_animation.mp4")))
            for bad in ("/static/../server.py", "/static/", "https://example.com/a.mp3", "/api/assets/" + "0" * 32 + "/content",
                        "/etc/passwd", None):
                self.assertIsNone(resolve(bad), bad)
        finally:
            db.close()


@unittest.skipUnless(FFMPEG and export_outputs.h264_encoder(), "needs ffmpeg with libx264")
class RenderReviewFixesTest(RenderBase):
    """The review's fixes (Phase 22): the watchdog, a failed mix, the worker's address, the frame cache, the slot wait."""

    def with_env(self, **values):
        self.service.env.update({k: str(v) for k, v in values.items()})

    def test_the_watchdog_stops_only_a_silent_worker(self):
        self.with_env(RENDER_STALL_SECONDS=5)
        self.control({"mode": "keepalive", "seconds": 9, "frames": 30})  # 9 s of preparing, never 5 s without a word
        export_id, run_id, owner = self.start()
        self.execute(run_id, owner)
        export = self.get(models.VideoExport, export_id)
        self.assertEqual(export.status, "COMPLETED", export.error_message)
        self.tearDown()
        self.spawned.clear()
        self.control({"mode": "silent"})
        export_id, run_id, owner = self.start()
        started = time.time()
        self.execute(run_id, owner)
        export = self.get(models.VideoExport, export_id)
        self.assertEqual(export.status, "FAILED")
        self.assertIn("stopped responding", export.error_message)
        self.assertLess(time.time() - started, 20)

    def test_a_failed_mix_keeps_the_frames_and_render_again_only_mixes(self):
        export_id, run_id, owner = self.start()
        original = export_outputs.mix_render
        export_outputs.mix_render = lambda *a, **k: (_ for _ in ()).throw(export_outputs.MixError("ffmpeg could not mix the sound (x)"))
        try:
            self.execute(run_id, owner)
        finally:
            export_outputs.mix_render = original
        data = self.api_export(export_id)
        self.assertEqual(data["status"], "FAILED")
        self.assertIn("Render again finishes it without rendering the frames again", data["error_message"])
        ws = self.service.workspace(export_id)
        self.assertTrue(os.path.isfile(os.path.join(ws, "video.mp4")))  # the hours of capture are kept
        captures = len([e for e in self.seen() if "rendered" in e])
        # Render again (a new export linked to the failed one) takes the frames over and only mixes and saves
        self.tearDown()
        db = database.SessionLocal()
        try:
            export2, run2 = self.service.start(db, db.get(models.User, self.user_id), db.get(models.Project, self.project_id), "refuse", {},
                                               "http://127.0.0.1:9729", retry_of_id=export_id)
            export2_id, run2_id = export2.id, run2.id
        finally:
            db.close()
        self.execute(run2_id, self.spawned[-1][1])
        self.assertEqual(self.get(models.VideoExport, export2_id).status, "COMPLETED")
        self.assertEqual(len([e for e in self.seen() if "rendered" in e]), captures)  # nothing captured again
        self.assertEqual(json.loads(self.attempts(run2_id)[0].detail)["recovered_how"], "adopted_capture")
        self.assertFalse(os.path.exists(ws))

    def test_the_worker_address_is_the_servers_own_never_the_host_header(self):
        svc = server.render_service
        original = (svc.spawn, svc.availability, dict(svc.env))
        svc.spawn = lambda run_id, owner, manager=False: None
        svc.availability = lambda: (True, "")
        try:
            svc.env.pop("RENDER_BASE_URL", None)
            res = self.client.post("/api/exports/render", json={"project_id": self.project_id},
                                   headers={**auth("renderer"), "Host": "attacker.example:6666"})
            self.assertEqual(res.status_code, 200, res.text)
            request = json.loads(self.get(models.AIGenerationRun, res.json()["render"]["run_id"]).request)
            self.assertEqual(request["base_url"], "http://127.0.0.1:80")  # the test server's own port, not 6666
            self.tearDown()
            svc.env["RENDER_BASE_URL"] = "http://127.0.0.1:9733"
            res = self.client.post("/api/exports/render", json={"project_id": self.project_id}, headers=auth("renderer"))
            request = json.loads(self.get(models.AIGenerationRun, res.json()["render"]["run_id"]).request)
            self.assertEqual(request["base_url"], "http://127.0.0.1:9733")
            self.assertFalse(hasattr(svc, "seen_base"))  # no address shared between renders
            self.assertEqual(svc._base_url({"base_url": "http://127.0.0.1:1"}), "http://127.0.0.1:9733")
            svc.env.pop("RENDER_BASE_URL")
            self.assertEqual(svc._base_url({"base_url": "http://127.0.0.1:1"}), "http://127.0.0.1:1")
        finally:
            svc.spawn, svc.availability = original[:2]
            svc.env.clear()
            svc.env.update(original[2])

    def test_the_frame_cache_keeps_to_its_size_least_recently_used_first(self):
        frames = os.path.join(self.render_dir, "frames")
        shutil.rmtree(frames, ignore_errors=True)
        now = time.time()
        made = {}
        for name, age_days in (("aaaaaaaaaaaaaaa1", 30), ("aaaaaaaaaaaaaaa2", 20), ("aaaaaaaaaaaaaaa3", 0)):
            folder = os.path.join(frames, name)
            os.makedirs(folder)
            with open(os.path.join(folder, "000001.jpg"), "wb") as f:
                f.write(b"x" * 400_000)
            with open(os.path.join(folder, "manifest.json"), "w") as f:
                f.write("{}")
            os.utime(os.path.join(folder, "manifest.json"), (now - age_days * 86400, now - age_days * 86400))
            made[name] = folder
        self.with_env(RENDER_FRAMES_MAX_GB=0.0006)  # about 640 KB: one set too many
        self.assertEqual(self.service.trim_frame_cache(), 2)  # the two oldest go; the one in use is protected however big
        self.assertFalse(os.path.exists(made["aaaaaaaaaaaaaaa1"]))
        self.assertFalse(os.path.exists(made["aaaaaaaaaaaaaaa2"]))
        self.assertTrue(os.path.isdir(made["aaaaaaaaaaaaaaa3"]))
        self.with_env(RENDER_MIN_FREE_GB=10 ** 9)  # far more than any disk: refused before anything starts
        with self.assertRaises(renders.RenderFailed) as low:
            self.service._check_disk()
        self.assertEqual(low.exception.code, "disk_full")
        self.assertIn("running out of disk space", low.exception.message)

    def test_a_render_waiting_for_its_slot_holds_no_thread_and_can_be_cancelled(self):
        self.control({"mode": "ok", "frames": 30})
        export_id, run_id, owner = self.start()
        self.assertTrue(self.service.slots.acquire(blocking=False))  # another render is running

        async def scenario():
            task = asyncio.ensure_future(self.service.execute(run_id, owner))
            self.service.tasks[run_id] = task
            for _ in range(60):
                await asyncio.sleep(0.1)
                if self.get(models.VideoExport, export_id).stage == renders.WAITING:
                    break
            self.assertEqual(self.get(models.VideoExport, export_id).stage, renders.WAITING)
            self.assertEqual(len(self.service.pool._threads), 0)  # waiting costs no thread
            res = await asyncio.to_thread(self.client.patch, f"/api/exports/{export_id}", json={"status": "CANCELLED"}, headers=auth("renderer"))
            self.assertEqual(res.status_code, 200)
            await asyncio.wait_for(task, 15)
        try:
            asyncio.run(scenario())
        finally:
            self.service.slots.release()
        self.assertEqual(self.get(models.VideoExport, export_id).status, "CANCELLED")
        self.assertEqual(self.get(models.AIGenerationRun, run_id).status, RunState.CANCELLED)
        self.assertEqual(self.seen(), [])  # the worker never started


class RenderHelpersTest(unittest.TestCase):
    def test_missing_message_names_the_scenes(self):
        text = renders.missing_message([{"index": 1, "title": "Loads"}, "Scene 9: no video", {"index": None}])
        self.assertTrue(text.startswith('Not rendered: 3 scenes have no visual yet (Scene 2 "Loads"; Scene 9: no video; A scene).'))
        self.assertIn("1 scene has", renders.missing_message([{"index": 0, "title": "A"}]))

    def test_render_view_never_shows_paths_or_tokens(self):
        run = models.AIGenerationRun(id="r" * 32, status=RunState.RUNNING, recovery_count=0,
                                     detail=json.dumps({"phase": "capturing", "stage": "Rendering the video: scene 2 of 5",
                                                        "frames_done": 120, "frames_total": None, "workspace": "C:/x"}))
        view = exports.render_view(run)
        self.assertEqual(view, {"run_id": "r" * 32, "phase": "capturing", "frames_done": 120, "frames_total": None,
                                "message": "Rendering the video: scene 2 of 5", "error_code": None, "missing": [], "recovered": 0,
                                "notes": []})
        self.assertIsNone(exports.render_view(None))

    def test_the_layout_decides_which_rates_plan_the_pages(self):
        layout = renders.render_layout
        self.assertEqual(layout({}, {}), {"kind": "classic", "key": "classic"})
        self.assertEqual(layout({"storage": {"aadhi.cinematic": {"mode": "classic", "style": "academic"}}}, {}), {"kind": "classic", "key": "classic"})
        cine = {"storage": {"aadhi.cinematic": {"mode": "cinematic", "style": "academic"}}}
        self.assertEqual(layout(cine, {}), {"kind": "cinematic", "key": "cinematic:academic"})
        # the lesson's own style family comes first
        self.assertEqual(layout(cine, {"cinematic_style": {"style": "corporate_training"}}), {"kind": "cinematic", "key": "cinematic:corporate_training"})
        self.assertEqual(layout({"storage": {"aadhi.cinematic": {"mode": "cinematic", "style": "../x"}}}, {}), {"kind": "cinematic", "key": "cinematic"})

    def test_one_seed_per_lesson(self):
        # every page of a render, a retry and a later render of the same lesson draw the same "random" particles
        self.assertEqual(renders.RenderService.seed(12), renders.RenderService.seed(12))
        self.assertNotEqual(renders.RenderService.seed(12), renders.RenderService.seed(13))
        self.assertTrue(0 <= renders.RenderService.seed(12) < 2147483647)

    def test_settings_defaults(self):
        cfg = renders.settings({})
        self.assertEqual((cfg["ranges"], cfg["max_concurrent"], cfg["max_per_user"], cfg["quality"]), (3, 1, 1, 92))
        self.assertEqual(cfg["dir"], os.path.join(exports.EXPORTS_DIR, "render"))
        self.assertEqual(renders.settings({"RENDER_JPEG_QUALITY": "80"})["quality"], 92)  # never below 92 (condition 7)
        self.assertEqual(cfg["preroll"], "lesson")  # whole-lesson pre-roll; one-scene pre-roll at safe seams is the fallback
        self.assertEqual(renders.settings({"RENDER_PREROLL": "scene"})["preroll"], "scene")
        self.assertEqual(renders.settings({"RENDER_PREROLL": "anything"})["preroll"], "lesson")


if __name__ == "__main__":
    unittest.main()
