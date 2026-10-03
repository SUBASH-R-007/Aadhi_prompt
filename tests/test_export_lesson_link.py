"""Backend tests for the link between a lesson export and its lesson (Phase 20): export_outputs.clean_timeline keeps
{lesson: {project_id, fingerprint, revision}} only when valid, the stored timeline starts with it, only the export's own
lesson is kept, listings return it as lesson_fingerprint (also filtered by ?project_id=), and
exports.latest_lesson_export gives the Studio a lesson's newest export with the fingerprint it was recorded from.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_export*.py" -v
Uses the throwaway database and exports folder of backend_env; the real projects.db is never touched.
"""
import datetime
import hashlib
import json
import os
import unittest
import uuid

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: sets up the throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import export_outputs as E  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402

FP = hashlib.sha256(b"the lesson as recorded").hexdigest()
FP_LATER = hashlib.sha256(b"the lesson after an edit").hexdigest()
REVISION = "2026-10-02T09:15:00.123456"
TIMELINE = {"scenes": [{"t": 0.8, "title": "Stress"}, {"t": 1.9, "title": "Loads & forces"}],
            "cues": [{"start": 0.9, "end": 1.8, "text": "Stress is force over area."}, {"start": 1.9, "end": 2.8, "text": "Loads < limits."}]}


def link(project_id, fingerprint=FP, revision=REVISION):
    return {"project_id": project_id, "fingerprint": fingerprint, "revision": revision}


class CleanLessonLinkTest(unittest.TestCase):
    def test_a_valid_link_is_kept_first_and_the_rest_is_cleaned_as_before(self):
        raw = dict(TIMELINE, lesson=dict(link(7), extra="dropped"))
        cleaned = E.clean_timeline(raw)
        self.assertEqual(cleaned["lesson"], link(7))  # unknown keys are not kept
        self.assertEqual(list(cleaned), ["lesson", "scenes", "cues"])  # the stored file starts with the link
        self.assertTrue(json.dumps(cleaned).startswith(exports.LESSON_OPENING))
        without = E.clean_timeline(dict(TIMELINE))
        self.assertEqual({k: v for k, v in cleaned.items() if k != "lesson"}, without)
        self.assertNotIn("lesson", without)
        self.assertEqual(E.clean_timeline(None), {"scenes": [], "cues": []})
        self.assertEqual(E.clean_timeline({"lesson": link(3)}), {"lesson": link(3), "scenes": [], "cues": []})
        self.assertEqual(E.clean_timeline({"lesson": link(3, revision="")})["lesson"]["revision"], "")
        self.assertEqual(E.clean_timeline({"lesson": link(3, revision="r" * 40)})["lesson"]["revision"], "r" * 40)

    def test_invalid_links_are_dropped(self):
        bad = [
            None, "7", [7, FP, REVISION], {},
            link(0), link(-3), link(True), link("7"), link(7.0), link(None),
            link(7, fingerprint=FP.upper()), link(7, fingerprint=FP[:63]), link(7, fingerprint=FP + "0"),
            link(7, fingerprint="g" * 64), link(7, fingerprint=None), link(7, fingerprint=int(FP[:8], 16)),
            link(7, revision="r" * 41), link(7, revision=None), link(7, revision=5), link(7, revision="2026\n10"),
            {"project_id": 7, "fingerprint": FP},  # no revision
        ]
        for value in bad:
            with self.subTest(value=value):
                cleaned = E.clean_timeline(dict(TIMELINE, lesson=value))
                self.assertNotIn("lesson", cleaned)
                self.assertEqual(cleaned, E.clean_timeline(dict(TIMELINE)))
                self.assertIsNone(E.clean_lesson_link(value))


class RecordedLessonTest(unittest.TestCase):
    """exports.recorded_lesson reads the link from the start of a stored timeline file and tolerates anything else."""

    def write(self, name, text, mode="w"):
        key = f"lesson-link-tests/{name}"
        path = exports.STORAGE.path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
            f.write(text)
        return key

    def test_reads_the_link_and_tolerates_missing_old_and_corrupt_files(self):
        long_timeline = dict(TIMELINE, cues=[{"start": i, "end": i + 0.5, "text": f"Line {i} of a long lesson."} for i in range(2000)])
        key = self.write("long.timeline.json", json.dumps(E.clean_timeline(dict(long_timeline, lesson=link(11)))))
        self.assertGreater(os.path.getsize(exports.STORAGE.path(key)), 10 * exports.LESSON_HEAD_BYTES)
        self.assertEqual(exports.recorded_lesson(key), link(11))  # only the start of the file is needed
        self.assertIsNone(exports.recorded_lesson(self.write("old.timeline.json", json.dumps(E.clean_timeline(TIMELINE)))))
        self.assertIsNone(exports.recorded_lesson("lesson-link-tests/missing.timeline.json"))
        self.assertIsNone(exports.recorded_lesson("../outside.json"))
        self.assertIsNone(exports.recorded_lesson(None))
        self.assertIsNone(exports.recorded_lesson(self.write("cut.timeline.json", '{"lesson": {"project_id": 11, "finger')))
        self.assertIsNone(exports.recorded_lesson(self.write("binary.timeline.json", b"\x00\xff\x1a\x45" * 300, mode="wb")))
        self.assertIsNone(exports.recorded_lesson(self.write("empty.timeline.json", "")))
        # A link at the start is checked again, never trusted as written
        self.assertIsNone(exports.recorded_lesson(self.write("bad.timeline.json", json.dumps({"lesson": link(11, fingerprint="x" * 64), "scenes": []}))))


class LessonLinkApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.owner = ensure_user("lena_link")
        cls.other = ensure_user("omar_link")
        with database.SessionLocal() as db:
            projects = [models.Project(user_id=cls.owner, subject_name="Mechanics", session_number="Lesson 1", session_title=title,
                                       json_data="{}") for title in ("Stress", "Strain", "Torsion")]
            projects.append(models.Project(user_id=cls.other, subject_name="Biology", session_title="Cells", json_data="{}"))
            db.add_all(projects)
            db.commit()
            cls.lesson, cls.second, cls.third, cls.others_lesson = (p.id for p in projects)
        cls.clock = datetime.datetime.utcnow() - datetime.timedelta(hours=1)

    def next_time(self):
        type(self).clock += datetime.timedelta(seconds=1)  # created in a known order
        return type(self).clock

    def finished(self, project_id, lesson_link=None, source="lesson", user_id=None):
        """A finished export made through the server's own finishing step (no video file needed for the timeline)."""
        with database.SessionLocal() as db:
            export = models.VideoExport(id=uuid.uuid4().hex, project_id=project_id, user_id=user_id or self.owner,
                                        title="Linked lesson", source=source, status="PROCESSING", created_at=self.next_time())
            db.add(export)
            db.commit()
            timeline = dict(TIMELINE, lesson=lesson_link) if lesson_link is not None else dict(TIMELINE)
            exports._complete_export(db, export, "webm", f"{export.user_id}/{export.id}.webm", 1234, None, "video/webm", 3.0, timeline)
            exports._output(db, export.id, "mp4").status = "unavailable"  # no background MP4 work left behind
            db.commit()
            return export.id

    def unfinished(self, project_id, status, source="lesson", updated=None):
        with database.SessionLocal() as db:
            export = models.VideoExport(id=uuid.uuid4().hex, project_id=project_id, user_id=self.owner, title="Linked lesson",
                                        source=source, status=status, created_at=self.next_time(), updated_at=updated or datetime.datetime.utcnow())
            db.add(export)
            db.commit()
            return export.id

    def timeline_file(self, export_id, user_id=None):
        return exports.STORAGE.path(f"{user_id or self.owner}/{export_id}.timeline.json")

    def latest(self, project_id, user_id=None, **options):
        with database.SessionLocal() as db:
            return exports.latest_lesson_export(db, user_id or self.owner, project_id, **options)

    def listed(self, query="", user="lena_link"):
        res = self.client.get("/api/exports" + query, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["exports"]

    def test_the_timeline_output_keeps_the_link_of_the_exports_own_lesson(self):
        export_id = self.finished(self.lesson, link(self.lesson))
        with open(self.timeline_file(export_id), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith(exports.LESSON_OPENING))
        stored = json.loads(text)
        self.assertEqual(stored["lesson"], link(self.lesson))
        self.assertEqual([s["title"] for s in stored["scenes"]], ["Stress", "Loads & forces"])  # subtitles and chapters as before
        with database.SessionLocal() as db:
            kinds = {o.kind: o.status for o in db.query(models.ExportOutput).filter(models.ExportOutput.export_id == export_id)}
        self.assertEqual((kinds["vtt"], kinds["chapters"], kinds["timeline"]), ("ready", "ready", "ready"))
        got = self.client.get(f"/api/exports/{export_id}", headers=auth("lena_link")).json()
        self.assertEqual(got["lesson_fingerprint"], FP)
        self.assertNotIn("timeline", got["outputs"])  # still an internal file, never offered

    def test_a_link_to_another_lesson_a_manual_recording_or_an_invalid_link_is_not_kept(self):
        cases = [
            self.finished(self.lesson, link(self.second)),          # another of the user's lessons
            self.finished(self.lesson, link(self.others_lesson)),   # someone else's lesson
            self.finished(None, link(self.lesson)),                 # an export with no lesson
            self.finished(self.lesson, link(self.lesson), source="manual"),
            self.finished(self.lesson, link(self.lesson, fingerprint="F" * 64)),
        ]
        for export_id in cases:
            with self.subTest(export_id=export_id):
                with open(self.timeline_file(export_id), encoding="utf-8") as f:
                    stored = json.load(f)
                self.assertNotIn("lesson", stored)
                self.assertEqual(len(stored["cues"]), 2)  # the rest of the timeline is unchanged
                self.assertIsNone(self.client.get(f"/api/exports/{export_id}", headers=auth("lena_link")).json()["lesson_fingerprint"])

    def test_listing_by_lesson_carries_the_fingerprint_and_ownership_is_unchanged(self):
        matched = self.finished(self.third, link(self.third, fingerprint=FP))
        older = self.finished(self.third, link(self.third, fingerprint=FP_LATER))
        plain = self.finished(self.third)  # recorded before Phase 20: no link
        elsewhere = self.finished(self.second, link(self.second))
        failed = self.unfinished(self.third, "FAILED")
        mine = self.listed(f"?project_id={self.third}")
        self.assertEqual([e["id"] for e in mine], [failed, plain, older, matched])  # newest first, this lesson only
        self.assertEqual({e["id"]: e["lesson_fingerprint"] for e in mine}, {failed: None, plain: None, older: FP_LATER, matched: FP})
        self.assertTrue(all(e["project_id"] == self.third for e in mine))
        everything = {e["id"]: e for e in self.listed()}
        self.assertTrue({matched, older, plain, elsewhere, failed} <= set(everything))
        self.assertEqual(everything[elsewhere]["lesson_fingerprint"], FP)
        # Someone else sees none of it, by lesson or one by one
        self.assertEqual(self.listed(f"?project_id={self.third}", user="omar_link"), [])
        self.assertNotIn(matched, [e["id"] for e in self.listed(user="omar_link")])
        self.assertEqual(self.client.get(f"/api/exports/{matched}", headers=auth("omar_link")).status_code, 404)
        with database.SessionLocal() as db:
            self.assertIsNone(exports.latest_lesson_export(db, self.other, self.third))

    def test_reading_the_fingerprints_of_a_listing_is_bounded(self):
        ids = [self.finished(self.second, link(self.second)) for _ in range(3)]
        with database.SessionLocal() as db:
            rows = [db.get(models.VideoExport, i) for i in reversed(ids)]
            outputs = exports.outputs_by_export(db, ids)
            self.assertEqual(len(exports.recorded_fingerprints(rows, outputs)), 3)
            self.assertEqual(list(exports.recorded_fingerprints(rows, outputs, limit=2)), [ids[2], ids[1]])  # the newest ones

    def test_latest_lesson_export_reads_the_fingerprint_of_the_newest_export(self):
        with database.SessionLocal() as db:
            project = models.Project(user_id=self.owner, subject_name="Mechanics", session_title="Bending", json_data="{}")
            db.add(project)
            db.commit()
            lesson = project.id
        self.assertIsNone(self.latest(lesson))
        done = self.finished(lesson, link(lesson))
        latest = self.latest(lesson)
        self.assertEqual(set(latest), {"id", "status", "completed_at", "fingerprint"})
        self.assertEqual((latest["id"], latest["status"], latest["fingerprint"]), (done, "COMPLETED", FP))
        self.assertTrue(latest["completed_at"].endswith("+00:00"))
        # A newer attempt that did not finish is the latest; the newest finished one is still there to compare
        cancelled = self.unfinished(lesson, "CANCELLED")
        self.unfinished(lesson, "RECORDING", source="manual")  # a manual recording is not an export of the lesson
        self.assertEqual(self.latest(lesson), {"id": cancelled, "status": "CANCELLED", "completed_at": None, "fingerprint": None})
        self.assertEqual(self.latest(lesson, completed=True)["fingerprint"], FP)
        # One that stopped reporting long ago counts as failed (as the next listing records it)
        abandoned = self.unfinished(lesson, "RECORDING", updated=datetime.datetime.utcnow() - exports.STALE_AFTER - datetime.timedelta(minutes=5))
        self.assertEqual((self.latest(lesson)["id"], self.latest(lesson)["status"]), (abandoned, "FAILED"))
        active = self.unfinished(lesson, "UPLOADING")
        self.assertEqual((self.latest(lesson)["id"], self.latest(lesson)["status"]), (active, "UPLOADING"))
        # Missing or damaged timeline files never break it: the fingerprint is just unknown
        os.remove(self.timeline_file(done))
        self.assertEqual(self.latest(lesson, completed=True), {"id": done, "status": "COMPLETED",
                                                               "completed_at": latest["completed_at"], "fingerprint": None})
        with open(self.timeline_file(done), "w", encoding="utf-8") as f:
            f.write('{"lesson": {"project_id": ')
        self.assertIsNone(self.latest(lesson, completed=True)["fingerprint"])
        self.assertIsNone(self.latest(lesson, user_id=self.other))  # never another user's lesson

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_complete_through_the_api_keeps_the_link_sent_with_the_timeline(self):
        path = os.path.join(TMP, "lesson-link.webm")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
               "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", path)
        with open(path, "rb") as f:
            video = f.read()
        headers = auth("lena_link")
        exp = self.client.post("/api/exports", json={"project_id": self.lesson, "title": "Linked"}, headers=headers).json()
        for status in ("PREPARING", "RECORDING", "UPLOADING"):
            self.assertEqual(self.client.patch(f"/api/exports/{exp['id']}", json={"status": status}, headers=headers).status_code, 200)
        self.client.put(f"/api/exports/{exp['id']}/upload?offset=0&total={len(video)}", content=video,
                        headers={**headers, "Content-Type": "application/octet-stream"})
        body = {"size": len(video), "mime_type": "video/webm;codecs=vp9,opus", "duration_seconds": 3,
                "timeline": dict(TIMELINE, lesson=link(self.lesson))}
        res = self.client.post(f"/api/exports/{exp['id']}/complete", json=body, headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["status"], res.json()["lesson_fingerprint"]), ("COMPLETED", FP))
        with open(self.timeline_file(exp["id"]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["lesson"], link(self.lesson))
        self.assertEqual(self.client.get(f"/api/exports/{exp['id']}/outputs/chapters", headers=headers).text,
                         "00:00 Stress\n00:01 Loads & forces\n")
        latest = self.latest(self.lesson, completed=True)
        self.assertEqual((latest["id"], latest["fingerprint"]), (exp["id"], FP))


if __name__ == "__main__":
    unittest.main()
