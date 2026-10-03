"""Backend tests for lesson video exports (exports.py).

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
Uses a throwaway SQLite database and exports folder; the real projects.db is never touched.
Tests that need a real video file use ffmpeg to make one and are skipped without it.
"""
import datetime
import os
import subprocess
import unittest

from backend_env import FFMPEG, TMP, assert_isolated  # first: sets up the throwaway database

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402


def make_video(path, seconds=2, audio="tone"):
    """audio: 'tone', 'silence' (an audio track with nothing in it, like a muted tab) or None."""
    args = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={seconds}"]
    if audio == "tone":
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libopus"]
    elif audio == "silence":
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={seconds}", "-c:a", "libopus"]
    subprocess.run(args + ["-c:v", "libvpx-vp9", "-b:v", "200k", path], check=True)
    with open(path, "rb") as f:
        return f.read()


class ExportApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Refuse to run against anything but the throwaway database
        assert_isolated()
        cls.client = TestClient(server.app)
        db = database.SessionLocal()
        for name in ("alice", "bob"):
            if not db.query(models.User).filter(models.User.username == name).first():
                db.add(models.User(username=name, password_hash="unused"))
        db.commit()
        cls.alice = db.query(models.User).filter(models.User.username == "alice").first().id
        cls.bob = db.query(models.User).filter(models.User.username == "bob").first().id
        project = models.Project(user_id=cls.alice, subject_name="Strength of Materials", session_number="Lesson 1",
                                 session_title="Stress", json_data="{}")
        db.add(project)
        db.commit()
        cls.project_id = project.id
        db.close()
        if FFMPEG:
            cls.video = make_video(os.path.join(TMP, "lesson.webm"))
            cls.video_without_audio = make_video(os.path.join(TMP, "no-audio.webm"), audio=None)
            cls.muted_video = make_video(os.path.join(TMP, "muted.webm"), audio="silence")

    def auth(self, user="alice"):
        return {"Authorization": "Bearer " + server.create_access_token({"sub": user})}

    def create(self, user="alice", **body):
        body.setdefault("project_id", self.project_id if user == "alice" else None)
        res = self.client.post("/api/exports", json=body, headers=self.auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def patch(self, export_id, user="alice", **body):
        return self.client.patch(f"/api/exports/{export_id}", json=body, headers=self.auth(user))

    def upload(self, export_id, data, offset=0, user="alice"):
        return self.client.put(f"/api/exports/{export_id}/upload?offset={offset}&total={len(data) + offset}",
                               content=data, headers={**self.auth(user), "Content-Type": "application/octet-stream"})

    def to_uploading(self, export_id):
        for status in ("PREPARING", "RECORDING", "UPLOADING"):
            self.assertEqual(self.patch(export_id, status=status).status_code, 200)

    def completed_export(self, data=None, **create):
        export = self.create(**create)
        self.to_uploading(export["id"])
        data = data if data is not None else self.video
        self.assertEqual(self.upload(export["id"], data).status_code, 200)
        res = self.client.post(f"/api/exports/{export['id']}/complete",
                               json={"size": len(data), "mime_type": "video/webm;codecs=vp9,opus", "duration_seconds": 2},
                               headers=self.auth())
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def db_export(self, export_id):
        db = database.SessionLocal()
        try:
            return db.get(models.VideoExport, export_id)
        finally:
            db.close()

    # --- creation and status ----------------------------------------------------

    def test_export_requires_login(self):
        self.assertEqual(self.client.post("/api/exports", json={}).status_code, 401)
        self.assertEqual(self.client.get("/api/exports").status_code, 401)

    def test_create_export_for_own_project(self):
        export = self.create(title="")
        self.assertEqual(export["status"], "QUEUED")
        self.assertEqual(export["project_id"], self.project_id)
        self.assertEqual(export["title"], "Strength of Materials Lesson 1 Stress")
        self.assertNotIn("storage_key", export)
        record = self.db_export(export["id"])
        self.assertEqual(record.user_id, self.alice)

    def test_cannot_export_someone_elses_project(self):
        res = self.client.post("/api/exports", json={"project_id": self.project_id}, headers=self.auth("bob"))
        self.assertEqual(res.status_code, 404)

    def test_status_transitions(self):
        export = self.create()
        self.assertEqual(self.patch(export["id"], status="PREPARING", stage="Loading assets", progress=0.5).json()["progress"], 0.5)
        self.assertEqual(self.patch(export["id"], status="RECORDING").status_code, 200)
        # Only the server can mark an export done
        self.assertEqual(self.patch(export["id"], status="COMPLETED").status_code, 409)
        self.assertEqual(self.patch(export["id"], status="PROCESSING").status_code, 409)
        self.assertEqual(self.patch(export["id"], status="PREPARING").status_code, 409)
        cancelled = self.patch(export["id"], status="CANCELLED", error_message="Screen sharing was cancelled.").json()
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertIsNotNone(cancelled["completed_at"])
        self.assertEqual(self.patch(export["id"], status="RECORDING").status_code, 409)

    # --- upload, persistence, download ----------------------------------------

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_upload_persist_and_download(self):
        export = self.create(title="Strength of Materials — Lesson 01")
        self.to_uploading(export["id"])
        half = len(self.video) // 2
        self.assertEqual(self.upload(export["id"], self.video[:half]).json()["received"], half)
        # A resend at the wrong offset is refused with the resume point
        wrong = self.upload(export["id"], self.video[half:], offset=0)
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(wrong.json()["detail"]["received"], half)
        self.assertEqual(self.client.get(f"/api/exports/{export['id']}/upload", headers=self.auth()).json()["received"], half)
        self.assertEqual(self.upload(export["id"], self.video[half:], offset=half).json()["received"], len(self.video))

        done = self.client.post(f"/api/exports/{export['id']}/complete",
                                json={"size": len(self.video), "mime_type": "video/webm;codecs=vp9,opus"}, headers=self.auth()).json()
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(done["file_name"], "aadhi-eduengine-strength-of-materials-lesson-01.webm")
        self.assertEqual((done["width"], done["height"]), (320, 240))
        self.assertTrue(done["has_audio"])
        self.assertAlmostEqual(done["duration_seconds"], 2, delta=0.3)
        self.assertEqual(done["mime_type"], "video/webm;codecs=vp9,opus")

        record = self.db_export(export["id"])
        stored = os.path.join(exports.EXPORTS_DIR, record.storage_key)
        self.assertTrue(os.path.isfile(stored))
        self.assertEqual(record.storage_key, f"{self.alice}/{export['id']}.webm")
        self.assertEqual(os.path.getsize(stored), done["file_size"])
        self.assertFalse(os.path.exists(exports._tmp_path(export["id"])))

        res = self.client.get(f"/api/exports/{export['id']}/download", headers=self.auth())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "video/webm")
        self.assertIn('attachment; filename="aadhi-eduengine-strength-of-materials-lesson-01.webm"', res.headers["content-disposition"])
        with open(stored, "rb") as f:
            self.assertEqual(res.content, f.read())
        # Seeking in the preview player needs byte ranges
        part = self.client.get(f"/api/exports/{export['id']}/download", headers={**self.auth(), "Range": "bytes=0-99"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(len(part.content), 100)

        listed = self.client.get(f"/api/exports?project_id={self.project_id}", headers=self.auth()).json()["exports"]
        self.assertIn(export["id"], [e["id"] for e in listed])

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_signed_link_for_preview_and_download(self):
        export = self.completed_export()
        link = self.client.post(f"/api/exports/{export['id']}/link", headers=self.auth()).json()
        preview = self.client.get(link["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.headers["content-disposition"].startswith("inline"))
        self.assertTrue(self.client.get(link["download_url"]).headers["content-disposition"].startswith("attachment"))

        token = link["download_url"].split("token=")[1]
        other = self.completed_export()
        self.assertEqual(self.client.get(f"/api/exports/{other['id']}/download?token={token}").status_code, 403)
        # A download link is not a login
        self.assertEqual(self.client.get("/api/exports", headers={"Authorization": "Bearer " + token}).status_code, 401)
        expired = jwt.encode({"sub": "alice", "scope": "export", "eid": export["id"],
                              "exp": datetime.datetime.utcnow() - datetime.timedelta(minutes=1)}, server.SECRET_KEY, algorithm="HS256")
        self.assertEqual(self.client.get(f"/api/exports/{export['id']}/download?token={expired}").status_code, 401)
        self.assertEqual(self.client.get(f"/api/exports/{export['id']}/download").status_code, 401)

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_other_users_cannot_reach_an_export(self):
        export = self.completed_export()
        eid = export["id"]
        self.assertEqual(self.client.get(f"/api/exports/{eid}", headers=self.auth("bob")).status_code, 404)
        self.assertEqual(self.patch(eid, user="bob", status="FAILED").status_code, 404)
        self.assertEqual(self.client.get(f"/api/exports/{eid}/download", headers=self.auth("bob")).status_code, 404)
        self.assertEqual(self.client.post(f"/api/exports/{eid}/link", headers=self.auth("bob")).status_code, 404)
        self.assertEqual(self.upload(eid, b"x", user="bob").status_code, 404)
        self.assertNotIn(eid, [e["id"] for e in self.client.get("/api/exports", headers=self.auth("bob")).json()["exports"]])
        # A signed link minted for bob does not open alice's video either
        bob_token = jwt.encode({"sub": "bob", "scope": "export", "eid": eid,
                                "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5)}, server.SECRET_KEY, algorithm="HS256")
        self.assertEqual(self.client.get(f"/api/exports/{eid}/download?token={bob_token}").status_code, 404)

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_missing_file_is_reported(self):
        export = self.completed_export()
        os.remove(os.path.join(exports.EXPORTS_DIR, self.db_export(export["id"]).storage_key))
        res = self.client.get(f"/api/exports/{export['id']}/download", headers=self.auth())
        self.assertEqual(res.status_code, 410)
        self.assertIn("missing", res.json()["detail"])

    def test_download_before_completion_is_refused(self):
        export = self.create()
        self.assertEqual(self.client.get(f"/api/exports/{export['id']}/download", headers=self.auth()).status_code, 409)
        self.assertEqual(self.client.post(f"/api/exports/{export['id']}/link", headers=self.auth()).status_code, 409)

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_video_without_sound_is_flagged(self):
        # No audio track at all ("Also share tab audio" off), or a track of pure silence (muted tab)
        for data in (self.video_without_audio, self.muted_video):
            export = self.completed_export(data=data)
            self.assertFalse(export["has_audio"])
            self.assertIn("no sound", export["stage"])
        self.assertTrue(self.completed_export()["has_audio"])

    # --- failures and retry -----------------------------------------------------

    def test_upload_that_is_not_a_video_fails_cleanly(self):
        export = self.create()
        self.to_uploading(export["id"])
        junk = b"<html>not a video</html>" * 100
        self.upload(export["id"], junk)
        res = self.client.post(f"/api/exports/{export['id']}/complete", json={"size": len(junk)}, headers=self.auth())
        self.assertEqual(res.status_code, 400)
        record = self.db_export(export["id"])
        self.assertEqual(record.status, "FAILED")
        self.assertIn("not a WebM or MP4", record.error_message)
        self.assertFalse(os.path.exists(exports._tmp_path(export["id"])))

    def test_incomplete_upload_can_be_resumed(self):
        export = self.create()
        self.to_uploading(export["id"])
        self.upload(export["id"], b"\x1a\x45\xdf\xa3" + b"\0" * 100)
        res = self.client.post(f"/api/exports/{export['id']}/complete", json={"size": 1000}, headers=self.auth())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.db_export(export["id"]).status, "UPLOADING")
        self.assertEqual(self.client.get(f"/api/exports/{export['id']}/upload", headers=self.auth()).json()["received"], 104)

    def test_upload_only_while_uploading(self):
        export = self.create()
        self.assertEqual(self.upload(export["id"], b"data").status_code, 409)

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_retry_is_a_new_attempt_and_keeps_earlier_exports(self):
        done = self.completed_export()
        failed = self.create()
        self.patch(failed["id"], status="FAILED", error_message="Recording was interrupted.")
        retry = self.create(retry_of_id=failed["id"])
        self.assertNotEqual(retry["id"], failed["id"])
        self.assertEqual(retry["retry_of_id"], failed["id"])
        self.assertEqual(self.db_export(failed["id"]).status, "FAILED")
        self.assertEqual(self.db_export(done["id"]).status, "COMPLETED")
        self.assertEqual(self.client.get(f"/api/exports/{done['id']}/download", headers=self.auth()).status_code, 200)
        # Retrying someone else's export is refused
        res = self.client.post("/api/exports", json={"retry_of_id": failed["id"]}, headers=self.auth("bob"))
        self.assertEqual(res.status_code, 404)

    def test_generated_file_names_are_safe(self):
        export = self.create(title="../../etc/passwd <script>")
        self.assertEqual(exports.download_name(self.db_export(export["id"])), "aadhi-eduengine-etc-passwd-script.webm")
        self.assertEqual(exports.slugify("   "), "lesson")

    # --- cleanup -------------------------------------------------------------------

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_cleanup_is_conservative(self):
        done = self.completed_export()
        stale = self.create()
        self.to_uploading(stale["id"])
        self.upload(stale["id"], b"\x1a\x45\xdf\xa3partial")
        recent = self.create()
        self.to_uploading(recent["id"])
        self.upload(recent["id"], b"\x1a\x45\xdf\xa3partial")

        db = database.SessionLocal()
        db.get(models.VideoExport, stale["id"]).updated_at = datetime.datetime.utcnow() - datetime.timedelta(days=2)
        db.commit()
        tmp_dir = os.path.join(exports.EXPORTS_DIR, "tmp")
        orphan_old = os.path.join(tmp_dir, "0" * 32 + ".part")
        orphan_new = os.path.join(tmp_dir, "1" * 32 + ".part")
        for path in (orphan_old, orphan_new):
            with open(path, "wb") as f:
                f.write(b"x")
        two_hours_ago = (datetime.datetime.now() - datetime.timedelta(hours=2)).timestamp()
        os.utime(orphan_old, (two_hours_ago, two_hours_ago))

        exports.cleanup_exports(db, force=True)
        db.close()

        self.assertEqual(self.db_export(stale["id"]).status, "FAILED")
        self.assertIn("abandoned", self.db_export(stale["id"]).error_message)
        self.assertFalse(os.path.exists(exports._tmp_path(stale["id"])))
        self.assertEqual(self.db_export(recent["id"]).status, "UPLOADING")
        self.assertTrue(os.path.exists(exports._tmp_path(recent["id"])))
        self.assertFalse(os.path.exists(orphan_old))
        self.assertTrue(os.path.exists(orphan_new))
        self.assertTrue(os.path.isfile(os.path.join(exports.EXPORTS_DIR, self.db_export(done["id"]).storage_key)))
        os.remove(orphan_new)


if __name__ == "__main__":
    unittest.main()
