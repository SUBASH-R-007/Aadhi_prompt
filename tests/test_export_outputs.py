"""Backend tests for the extra outputs of a lesson export (Phase 7): WebVTT subtitles and chapters
from the recording's timeline, the MP4 copy made with ffmpeg, and the library assets of the videos.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
"""
import json
import os
import re
import subprocess
import unittest
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated  # first: sets up the throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import export_outputs as E  # noqa: E402
import exports  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import visuals  # noqa: E402

TIME = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")


def cues_of(vtt):
    """[(start, end, text)] parsed back from a WebVTT file."""
    out, blocks = [], vtt.split("\n\n")[1:]
    for block in blocks:
        lines = [line for line in block.split("\n") if line]
        if len(lines) < 3:
            continue
        a, b = lines[1].split(" --> ")
        seconds = lambda s: int(s[0:2]) * 3600 + int(s[3:5]) * 60 + float(s[6:])  # noqa: E731
        out.append((seconds(a), seconds(b), "\n".join(lines[2:])))
    return out


class SubtitleAndChapterTest(unittest.TestCase):
    def test_webvtt_from_narration_lines(self):
        timeline = E.clean_timeline({"cues": [
            {"start": 15.0, "end": 18.2, "text": "Stress is force divided by area."},
            {"start": 18.2, "end": 23.0, "text": "It tells us how hard a material is being pushed or pulled, and engineers check it everywhere."},
            {"start": 30.0, "end": 31.0, "text": "Short."}]})
        vtt = E.build_vtt(timeline["cues"], 60)
        self.assertTrue(vtt.startswith("WEBVTT\n\n1\n"))
        for block in vtt.split("\n\n")[1:]:
            if block.strip():
                self.assertRegex(block.split("\n")[1], TIME)
        cues = cues_of(vtt)
        self.assertEqual(cues[0], (15.0, 18.2, "Stress is force divided by area."))
        # A long line becomes readable pieces: at most two lines of 42 characters each
        long_parts = [c for c in cues if 18.2 <= c[0] < 23.0]
        self.assertGreaterEqual(len(long_parts), 2)
        for _, _, text in cues:
            lines = text.split("\n")
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(line) <= E.LINE_CHARS for line in lines), lines)
        self.assertEqual(" ".join(t.replace("\n", " ") for _, _, t in long_parts),
                         "It tells us how hard a material is being pushed or pulled, and engineers check it everywhere.")

    def test_cues_never_overlap_and_stay_inside_the_video(self):
        timeline = E.clean_timeline({"cues": [
            {"start": 5, "end": 12, "text": "First line runs long."},
            {"start": 8, "end": 9, "text": "Second starts before the first ends."},
            {"start": 3, "end": 4, "text": "Out of order."},
            {"start": 58, "end": 70, "text": "Runs past the end."},
            {"start": 61, "end": 62, "text": "Starts after the end."},
            {"start": 20, "text": "No end time."}]})
        cues = cues_of(E.build_vtt(timeline["cues"], 60))
        self.assertEqual([c[2] for c in cues][0], "Out of order.")
        for (s1, e1, _), (s2, _, _) in zip(cues, cues[1:]):
            self.assertLessEqual(e1, s2)
        self.assertTrue(all(s < e for s, e, _ in cues))
        self.assertLessEqual(max(e for _, e, _ in cues), 60)
        self.assertNotIn("Starts after the end.", [c[2] for c in cues])
        no_end = next(c for c in cues if c[2] == "No end time.")
        self.assertLessEqual(no_end[1] - no_end[0], 7.0)  # about reading time, not until the next line

    def test_special_characters_markup_and_mathematics(self):
        timeline = E.clean_timeline({"cues": [
            {"start": 1, "end": 3, "text": "Use <b>x < y</b> &amp; [SYNC] check σ = F / A [PAUSE:2]"},
            {"start": 3, "end": 5, "text": "Tom & Jerry's \"quote\" -> arrow"}]})
        cues = cues_of(E.build_vtt(timeline["cues"], 10))
        self.assertEqual(cues[0][2], "Use x &lt; y &amp; check σ = F / A")
        self.assertEqual(cues[1][2], "Tom &amp; Jerry's \"quote\" -&gt; arrow")

    def test_no_narration_gives_an_empty_valid_file(self):
        self.assertEqual(E.build_vtt([], 30), "WEBVTT\n")
        self.assertEqual(E.clean_timeline(None), {"scenes": [], "cues": []})
        self.assertEqual(E.clean_timeline({"cues": [{"start": 1, "end": 2, "text": "   "}, {"start": "x", "text": "bad"},
                                                    {"start": -1, "text": "negative"}, "junk"]})["cues"], [])

    def test_chapters_follow_the_scenes(self):
        scenes = E.clean_timeline({"scenes": [
            {"t": 14.6, "title": "Stress in <b>structures</b>"}, {"t": 21.1, "title": "What is stress?"},
            {"t": 21.5, "title": "Squeezing a block"},        # the scene before lasted under a second
            {"t": 44.0, "title": ""}, {"t": 70.0, "title": "Past the end"}]})["scenes"]
        chapters = E.build_chapters(scenes, 65)
        self.assertEqual(chapters[0], {"start": 0.0, "title": "Introduction"})
        self.assertEqual([c["title"] for c in chapters], ["Introduction", "Stress in structures", "Squeezing a block", "Scene 4"])
        starts = [c["start"] for c in chapters]
        self.assertEqual(starts, sorted(set(starts)))
        self.assertEqual(E.chapters_text(chapters), "00:00 Introduction\n00:14 Stress in structures\n00:21 Squeezing a block\n00:44 Scene 4\n")
        self.assertEqual(E.chapter_time(3725), "1:02:05")
        # A lesson whose first scene starts at once has no separate introduction
        self.assertEqual(E.build_chapters([{"t": 0.4, "title": "Start"}, {"t": 10, "title": "Next"}], 20)[0], {"start": 0.0, "title": "Start"})
        self.assertEqual(E.build_chapters([], 20), [{"start": 0.0, "title": "Introduction"}])

    # Phase 21 (known issue D): a first scene titled "Introduction" gave two "Introduction" chapters
    def chapters_of(self, scenes, duration=60):
        return E.build_chapters(E.clean_timeline({"scenes": scenes})["scenes"], duration)

    def assert_titles_unique(self, chapters, duration=60):
        """No title names two different moments, in the chapter list, its text and the MP4's metadata."""
        keys = [" ".join(c["title"].split()).casefold() for c in chapters]
        self.assertEqual(len(keys), len(set(keys)), chapters)
        text_titles = [line.split(" ", 1)[1] for line in E.chapters_text(chapters).splitlines()]
        self.assertEqual(len(text_titles), len(set(t.casefold() for t in text_titles)), text_titles)
        meta_titles = re.findall(r"^title=(.*)$", E.chapters_ffmetadata(chapters, duration), re.MULTILINE)
        self.assertEqual(len(meta_titles), len(set(t.casefold() for t in meta_titles)), meta_titles)

    def test_a_first_scene_called_introduction_gets_an_opening_chapter_before_it(self):
        for title in ("Introduction", "  introduction ", "INTRODUCTION", "<b>Introduction</b>"):
            chapters = self.chapters_of([{"t": 12.0, "title": title}, {"t": 30.0, "title": "Stress"}])
            self.assertEqual([c["title"].casefold() for c in chapters], ["opening", "introduction", "stress"], title)
            self.assertEqual(chapters[0], {"start": 0.0, "title": "Opening"})
            self.assertEqual([c["start"] for c in chapters], [0.0, 12.0, 30.0])  # not merged: the scene keeps its real start
            self.assert_titles_unique(chapters)
        # Called directly with uncleaned titles too
        raw = E.build_chapters([{"t": 5.0, "title": "  introduction "}], 20)
        self.assertEqual(raw, [{"start": 0.0, "title": "Opening"}, {"start": 5.0, "title": "introduction"}])
        self.assertEqual(E.chapters_text(raw), "00:00 Opening\n00:05 introduction\n")

    def test_the_opening_chapter_is_introduction_for_any_other_title(self):
        chapters = self.chapters_of([{"t": 9.5, "title": "Stress in structures"}, {"t": 20.0, "title": "Introductions to strain"}])
        self.assertEqual([c["title"] for c in chapters], ["Introduction", "Stress in structures", "Introductions to strain"])
        self.assert_titles_unique(chapters)
        # an empty title is "Scene N" (as the page itself names an untitled scene), so the intro stays "Introduction"
        untitled = self.chapters_of([{"t": 9.5, "title": ""}, {"t": 20.0, "title": "Scene 2"}])
        self.assertEqual([c["title"] for c in untitled], ["Introduction", "Scene 1", "Scene 2"])
        self.assert_titles_unique(untitled)

    def test_a_hidden_first_scene_compares_the_first_scene_that_was_recorded(self):
        # The editor's hidden first scene is never played, so never logged: the recording's first scene is the lesson's second
        chapters = self.chapters_of([{"t": 11.0, "title": "Introduction"}, {"t": 25.0, "title": "Forces"}])
        self.assertEqual([c["title"] for c in chapters], ["Opening", "Introduction", "Forces"])
        # "Introduction" further on is still a name the intro cannot take; "Opening" taken too gives the next free name
        later = self.chapters_of([{"t": 11.0, "title": "Forces"}, {"t": 25.0, "title": "introduction"}])
        self.assertEqual([c["title"] for c in later], ["Opening", "Forces", "introduction"])
        both = self.chapters_of([{"t": 11.0, "title": "Introduction"}, {"t": 25.0, "title": "Opening"}])
        self.assertEqual(both[0], {"start": 0.0, "title": "Lesson start"})
        self.assert_titles_unique(both)
        every = self.chapters_of([{"t": 5.0 + 5 * i, "title": t} for i, t in enumerate(["Introduction", "Opening", "Lesson start", "Opening 2"])])
        self.assertEqual(every[0]["title"], "Opening 3")
        self.assert_titles_unique(every)

    def test_long_titles_are_capped(self):
        long = "Introduction to the strength of materials " * 10
        chapters = self.chapters_of([{"t": 8.0, "title": long}])
        self.assertEqual(chapters[0]["title"], "Introduction")
        self.assertTrue(E.CHAPTER_TITLE - 5 <= len(chapters[1]["title"]) <= E.CHAPTER_TITLE)
        self.assertTrue(long.startswith(chapters[1]["title"]))
        raw = E.build_chapters([{"t": 8.0, "title": "x" * 500}], 20)
        self.assertEqual(len(raw[1]["title"]), E.CHAPTER_TITLE)
        # a title longer than the cap only because of spaces is still "Introduction"
        padded = E.build_chapters([{"t": 8.0, "title": "Introduction" + " " * 300}], 20)
        self.assertEqual(padded, [{"start": 0.0, "title": "Opening"}, {"start": 8.0, "title": "Introduction"}])

    def test_a_first_scene_starting_at_once_is_the_only_opening_chapter(self):
        self.assertEqual(self.chapters_of([{"t": 0.3, "title": "Introduction"}]), [{"start": 0.0, "title": "Introduction"}])
        single = self.chapters_of([{"t": 0.6, "title": "Introduction"}, {"t": 15.0, "title": "Stress"}])
        self.assertEqual(single, [{"start": 0.0, "title": "Introduction"}, {"start": 15.0, "title": "Stress"}])
        self.assert_titles_unique(single)

    def test_ffmpeg_chapter_metadata_is_escaped(self):
        meta = E.chapters_ffmetadata([{"start": 0.0, "title": "Intro"}, {"start": 12.5, "title": "a=b; #1 \\ x"}], 30)
        self.assertTrue(meta.startswith(";FFMETADATA1\n"))
        self.assertIn("START=12500\nEND=30000\ntitle=a\\=b\\; \\#1 \\\\ x", meta)


@unittest.skipUnless(FFMPEG and E.h264_encoder(), "needs ffmpeg with libx264")
class Mp4ConversionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.webm = os.path.join(TMP, "outputs-source.webm")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus",
                        cls.webm], check=True)

    def probe_json(self, path, extra=()):
        return json.loads(subprocess.run(["ffprobe", "-v", "error", *extra, "-show_entries", "stream=codec_type,codec_name:format=duration",
                                          "-of", "json", path], capture_output=True, text=True).stdout)

    def test_conversion_embeds_subtitles_and_chapters(self):
        vtt, meta, dst = (os.path.join(TMP, n) for n in ("conv.vtt", "conv.ffmeta", "conv.mp4"))
        with open(vtt, "w", encoding="utf-8") as f:
            f.write(E.build_vtt([{"start": 0.2, "end": 2.0, "text": "Hello σ & more"}], 3))
        with open(meta, "w", encoding="utf-8") as f:
            f.write(E.chapters_ffmetadata([{"start": 0.0, "title": "Intro"}, {"start": 1.5, "title": "Main"}], 3))
        before = open(self.webm, "rb").read()
        E.convert_to_mp4(self.webm, dst, duration=3, vtt_path=vtt, ffmeta_path=meta)
        info = self.probe_json(dst)
        self.assertTrue({"h264", "aac", "mov_text"} <= {s["codec_name"] for s in info["streams"]})  # (+ the chapter track)
        self.assertAlmostEqual(float(info["format"]["duration"]), 3, delta=0.5)
        chapters = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", dst], capture_output=True, text=True).stdout)["chapters"]
        self.assertEqual([c["tags"]["title"] for c in chapters], ["Intro", "Main"])
        self.assertEqual(open(self.webm, "rb").read(), before)  # the source is never touched
        self.assertFalse(os.path.exists(dst + ".part.mp4"))

    def test_a_failed_conversion_leaves_nothing_behind(self):
        broken, dst = os.path.join(TMP, "broken.webm"), os.path.join(TMP, "broken.mp4")
        with open(broken, "wb") as f:
            f.write(b"\x1a\x45\xdf\xa3 not really a video")
        with self.assertRaises(E.ConversionError):
            E.convert_to_mp4(broken, dst, duration=3)
        self.assertFalse(os.path.exists(dst) or os.path.exists(dst + ".part.mp4"))
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            with self.assertRaisesRegex(E.ConversionError, "longer than"):
                E.convert_to_mp4(self.webm, dst, duration=3, timeout=1)
        with mock.patch.object(E, "h264_encoder", return_value=None):
            with self.assertRaisesRegex(E.ConversionError, "no H.264 encoder"):
                E.convert_to_mp4(self.webm, dst, duration=3)
            self.assertIn("WebM video is complete", E.mp4_unavailable_reason())


def make_video(path, seconds=3):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus",
                    path], check=True)
    with open(path, "rb") as f:
        return f.read()


TIMELINE = {"scenes": [{"t": 0.8, "title": "Stress"}, {"t": 1.9, "title": "Loads & forces"}],
            "cues": [{"start": 0.9, "end": 1.8, "text": "Stress is force over area."}, {"start": 1.9, "end": 2.8, "text": "Loads < limits & safe."}]}


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ExportOutputsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        with database.SessionLocal() as db:
            for name in ("olga", "pete"):
                if not db.query(models.User).filter(models.User.username == name).first():
                    db.add(models.User(username=name, password_hash="unused"))
            db.commit()
        cls.video = make_video(os.path.join(TMP, "outputs-lesson.webm"))

    def auth(self, user="olga"):
        return {"Authorization": "Bearer " + server.create_access_token({"sub": user})}

    def export(self, timeline=TIMELINE, user="olga"):
        exp = self.client.post("/api/exports", json={"title": "Outputs lesson"}, headers=self.auth(user)).json()
        for status in ("PREPARING", "RECORDING", "UPLOADING"):
            self.client.patch(f"/api/exports/{exp['id']}", json={"status": status}, headers=self.auth(user))
        self.client.put(f"/api/exports/{exp['id']}/upload?offset=0&total={len(self.video)}", content=self.video,
                        headers={**self.auth(user), "Content-Type": "application/octet-stream"})
        body = {"size": len(self.video), "mime_type": "video/webm;codecs=vp9,opus", "duration_seconds": 3}
        if timeline is not None:
            body["timeline"] = timeline
        res = self.client.post(f"/api/exports/{exp['id']}/complete", json=body, headers=self.auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def get(self, export_id, user="olga"):
        return self.client.get(f"/api/exports/{export_id}", headers=self.auth(user)).json()

    def download(self, export_id, kind, user="olga"):
        return self.client.get(f"/api/exports/{export_id}/outputs/{kind}", headers=self.auth(user))

    def test_subtitles_chapters_and_mp4_come_with_the_export(self):
        exp = self.export()
        self.assertEqual({k: v["status"] for k, v in exp["outputs"].items() if k != "mp4"}, {"vtt": "ready", "chapters": "ready"})
        latest = self.get(exp["id"])  # the MP4 is made in the background (already done under TestClient)
        mp4_ready = bool(E.h264_encoder())
        self.assertEqual(latest["outputs"]["mp4"]["status"], "ready" if mp4_ready else "unavailable")
        vtt = self.download(exp["id"], "vtt")
        self.assertEqual(vtt.status_code, 200)
        self.assertTrue(vtt.headers["content-type"].startswith("text/vtt"))
        self.assertIn("attachment", vtt.headers["content-disposition"])
        self.assertEqual([c[2] for c in cues_of(vtt.text)], ["Stress is force over area.", "Loads &lt; limits &amp; safe."])
        chapters = self.download(exp["id"], "chapters").text
        self.assertEqual(chapters, "00:00 Stress\n00:01 Loads & forces\n")
        if mp4_ready:
            mp4 = self.download(exp["id"], "mp4")
            self.assertEqual((mp4.status_code, mp4.headers["content-type"]), (200, "video/mp4"))
            path = os.path.join(TMP, "downloaded.mp4")
            with open(path, "wb") as f:
                f.write(mp4.content)
            streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", path],
                                                capture_output=True, text=True).stdout)["streams"]
            self.assertTrue({"h264", "aac", "mov_text"} <= {s["codec_name"] for s in streams})
        # The WebM is still the main download, unchanged by the conversion
        webm = self.client.get(f"/api/exports/{exp['id']}/download", headers=self.auth())
        self.assertEqual((webm.status_code, webm.headers["content-type"], len(webm.content)), (200, "video/webm", latest["file_size"]))
        self.assertEqual(latest["format"], "webm")

    def test_signed_links_and_access(self):
        exp = self.export()
        link = self.client.post(f"/api/exports/{exp['id']}/link", headers=self.auth()).json()
        self.assertTrue({"vtt", "chapters"} <= set(link["output_urls"]))
        self.assertEqual(self.client.get(link["output_urls"]["vtt"]).status_code, 200)  # the signed link alone is enough
        self.assertEqual(self.download(exp["id"], "vtt", user="pete").status_code, 404)
        self.assertEqual(self.client.get(f"/api/exports/{exp['id']}/outputs/vtt").status_code, 401)
        self.assertEqual(self.download(exp["id"], "timeline").status_code, 404)  # internal file, never offered
        self.assertEqual(self.download(exp["id"], "../../etc").status_code, 404)

    def test_a_failed_mp4_keeps_the_webm_and_can_be_retried(self):
        with mock.patch.object(E, "convert_to_mp4", side_effect=E.ConversionError("simulated encoder crash")):
            exp = self.export()
        failed = self.get(exp["id"])
        self.assertEqual(failed["status"], "COMPLETED")
        self.assertEqual(failed["outputs"]["mp4"]["status"], "failed")
        self.assertIn("WebM video is complete", failed["outputs"]["mp4"]["error"])
        self.assertEqual(self.client.get(f"/api/exports/{exp['id']}/download", headers=self.auth()).status_code, 200)
        self.assertEqual(self.download(exp["id"], "vtt").status_code, 200)
        self.assertEqual(self.download(exp["id"], "mp4").status_code, 409)
        retried = self.client.post(f"/api/exports/{exp['id']}/outputs/mp4", headers=self.auth())
        self.assertEqual(retried.status_code, 200, retried.text)
        after = self.get(exp["id"])["outputs"]["mp4"]["status"]
        self.assertEqual(after, "ready" if E.h264_encoder() else "unavailable")
        if after == "ready":
            self.assertEqual(self.client.post(f"/api/exports/{exp['id']}/outputs/mp4", headers=self.auth()).status_code, 409)
        self.assertEqual(self.client.post(f"/api/exports/{exp['id']}/outputs/mp4", headers=self.auth("pete")).status_code, 404)

    def test_no_encoder_means_no_mp4_but_a_complete_export(self):
        with mock.patch.object(E, "mp4_unavailable_reason", return_value="This server's ffmpeg has no H.264 encoder (libx264), so no MP4 can be made; the WebM video is complete."):
            exp = self.export()
        mp4 = self.get(exp["id"])["outputs"]["mp4"]
        self.assertEqual(mp4["status"], "unavailable")
        self.assertIn("no H.264 encoder", mp4["error"])
        self.assertEqual(self.client.get(f"/api/exports/{exp['id']}/download", headers=self.auth()).status_code, 200)

    def test_older_clients_without_a_timeline_still_get_valid_files(self):
        exp = self.export(timeline=None)
        self.assertEqual(self.download(exp["id"], "vtt").text, "WEBVTT\n")
        self.assertEqual(self.download(exp["id"], "chapters").text, "00:00 Introduction\n")
        self.assertIn(self.get(exp["id"])["outputs"]["mp4"]["status"], ("ready", "unavailable"))

    def test_a_lesson_opening_with_an_introduction_scene_has_one_introduction_chapter(self):
        exp = self.export(timeline={"scenes": [{"t": 1.2, "title": "Introduction"}, {"t": 2.2, "title": "Loads"}], "cues": []})
        self.assertEqual(self.download(exp["id"], "chapters").text, "00:00 Opening\n00:01 Introduction\n00:02 Loads\n")

    def test_finished_videos_are_library_assets_but_never_scene_visuals(self):
        exp = self.export()
        with database.SessionLocal() as db:
            rows = {r.kind: r for r in db.query(models.ExportOutput).filter(models.ExportOutput.export_id == exp["id"])}
            webm_asset = db.get(models.Asset, rows["webm"].asset_id)
            self.assertEqual((webm_asset.source, webm_asset.kind, webm_asset.storage_volume), ("export", "video", "exports"))
            self.assertEqual(json.loads(webm_asset.details)["export_id"], exp["id"])
            if rows["mp4"].status == "ready":
                self.assertEqual(db.get(models.Asset, rows["mp4"].asset_id).source, "export")
            user_id = db.query(models.User).filter(models.User.username == "olga").first().id
            self.assertNotIn(webm_asset.id, {a.id for a in visuals.load_candidates(db, user_id)})
        listed = self.client.get("/api/assets?source=export", headers=self.auth()).json()["assets"]
        self.assertIn(webm_asset.id, [a["id"] for a in listed])
        self.assertEqual(self.client.get(f"/api/assets/{webm_asset.id}/content", headers=self.auth()).status_code, 200)
        self.assertEqual(self.client.get(f"/api/assets/{webm_asset.id}", headers=self.auth("pete")).status_code, 404)

    def test_export_history_lists_outputs_in_one_query(self):
        for _ in range(3):
            self.export()
        from sqlalchemy import event
        queries = []
        listener = lambda *a: queries.append(1)  # noqa: E731
        event.listen(database.engine, "before_cursor_execute", listener)
        try:
            listed = self.client.get("/api/exports", headers=self.auth()).json()["exports"]
        finally:
            event.remove(database.engine, "before_cursor_execute", listener)
        self.assertTrue(all("outputs" in e for e in listed))
        self.assertLess(len(queries), 10)


class RenderMixTest(unittest.TestCase):
    """Phase 22: the sound of a server-rendered lesson, rebuilt from the sounds its page logged (export_outputs.mix_render)."""

    NARRATION = {"t": 0.5, "kind": "narration", "src": "/static/a.wav?t=1", "rate": 0.9, "offset": 0}
    TICK = {"t": 1.0, "kind": "sfx", "synth": {"type": "sine", "freq": 880, "gain": 0.15, "attack": 0.02, "floor": 0.0067, "duration": 0.15}}

    def test_only_sounds_that_can_be_mixed_are_kept_with_their_length(self):
        sounds = E.clean_sounds([
            {"t": 1, "kind": "beep", "src": "/static/x.wav"}, {"t": -1, "kind": "narration", "src": "/static/x.wav"},
            {"t": 9, "kind": "narration", "src": "/static/x.wav"}, {"t": 2, "kind": "narration"}, {"t": 2, "kind": "sfx"},
            {"t": 0.2, "kind": "logo", "src": "/video_template/logo_animation.mp4", "end": 5.7},
            {"t": 3, "kind": "video-voice", "src": "/static/v.mp4", "duration": 2, "offset": 1.5},
            {"t": 4, "kind": "narration", "src": "/static/n.wav", "clipDuration": 2.5, "offset": 0.5, "rate": 0.8},
            {"t": 1, "kind": "music", "src": "/video_template/bgm.mp3", "volume": 0.01},
            {"t": 1, "kind": "narration", "src": "/static/r.wav", "rate": 9, "volume": -2}, self.TICK], 8.0)
        self.assertEqual([(s["kind"], s["t"]) for s in sounds],
                         [("logo", 0.2), ("narration", 1.0), ("music", 1.0), ("sfx", 1.0), ("video-voice", 3.0), ("narration", 4.0)])
        logo, fast, music, tick, voice, narration = sounds
        self.assertAlmostEqual(logo["length"], 5.5)
        self.assertEqual((fast["rate"], fast["volume"], fast["length"]), (4.0, 0.0, None))  # clamped; plays until the next one
        self.assertEqual((music["loop"], music["volume"], music["length"]), (True, 0.01, None))
        self.assertEqual((tick["src"], tick["length"]), (None, 0.15))
        self.assertEqual((voice["offset"], voice["length"]), (1.5, 2.0))
        self.assertAlmostEqual(narration["length"], 2.5)  # (2.5 s clip - 0.5 s offset) at 0.8

    def test_sounds_of_one_kind_follow_each_other_and_effects_that_overlap_get_their_own_lane(self):
        narration = [{"t": 1.0, "kind": "narration", "src": "/static/a.wav"}, {"t": 3.0, "kind": "narration", "src": "/static/b.wav", "end": 4.0}]
        effects = [{"t": 1.0, "kind": "sfx", "synth": {"freq": 440, "duration": 1.0}}, {"t": 1.5, "kind": "sfx", "synth": {"freq": 660, "duration": 0.5}},
                   {"t": 2.5, "kind": "sfx", "synth": {"freq": 880, "duration": 0.2}}]
        lanes = E.sound_lanes(E.clean_sounds(narration + effects, 5.0), 240000)
        self.assertEqual([[(p["start"], p["length"]) for p in lane] for lane in lanes],
                         [[(48000, 96000), (144000, 48000)],          # the first narration stops where the next begins
                          [(48000, 48000), (120000, 9600)],           # the effects that do not overlap share a lane
                          [(72000, 24000)]])

    def test_the_mix_graph_is_exact_and_deterministic(self):
        lanes = E.sound_lanes(E.clean_sounds([self.NARRATION, self.TICK], 2.0), 96000)
        graph = E.mix_graph(lanes, 96000, {"/static/a.wav?t=1": "mix/s1.wav"})
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        silence = "anullsrc=r=48000:cl=stereo,atrim=end_sample={},aformat=sample_fmts=fltp:channel_layouts=stereo[{}]"
        expected = ";\n".join([
            silence.format(96000, "bed"),
            silence.format(24000, "l0g0"),
            f"amovie=mix/s1.wav,asetpts=PTS-STARTPTS,atempo=0.9,{fmt},apad=whole_len=72000,atrim=end_sample=72000,asetpts=N/SR/TB[l0p0]",
            "[l0g0][l0p0]concat=n=2:v=0:a=1[lane0]",
            silence.format(48000, "l1g0"),
            "aevalsrc=exprs='if(lt(t,0.02),0.15*t/0.02,0.15*pow(0.0067,(t-0.02)/0.13))*sin(2*PI*(880*t))':s=48000:c=stereo:d=0.15,"
            f"{fmt},apad=whole_len=7200,atrim=end_sample=7200,asetpts=N/SR/TB[l1p0]",
            silence.format(40800, "l1end"),
            "[l1g0][l1p0][l1end]concat=n=3:v=0:a=1[lane1]",
            "[bed][lane0][lane1]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,atrim=end_sample=96000[aout]"]) + "\n"
        self.assertEqual(graph, expected)
        self.assertEqual(E.mix_graph(lanes, 96000, {"/static/a.wav?t=1": "mix/s1.wav"}), graph)
        # a sound whose file is not on the server is left out; nothing at all is just the silent bed
        self.assertNotIn("amovie", E.mix_graph(lanes, 96000, {}))
        self.assertEqual(E.mix_graph([], 30, {}), silence.format(30, "bed") + ";\n[bed]anull[aout]\n")

    def test_looped_music_at_its_volume_and_slow_or_fast_speeds(self):
        music = E.clean_sounds([{"t": 0, "kind": "music", "src": "/m.mp3", "volume": 0.01, "offset": 2}], 4.0)
        graph = E.mix_graph(E.sound_lanes(music, 192000), 192000, {"/m.mp3": "mix/s1.mp3"})
        self.assertIn("amovie=mix/s1.mp3,aloop=loop=-1:size=2147483647,atrim=start=2,asetpts=PTS-STARTPTS,volume=0.01,", graph)
        self.assertEqual(E._atempo(0.3), ["atempo=0.5", "atempo=0.6"])
        self.assertEqual(E._atempo(3.0), ["atempo=2", "atempo=1.5"])
        self.assertEqual(E._atempo(1.0), [])

    def test_sound_effects_are_synthesised_from_the_page_oscillators(self):
        ding = E.clean_synth({"type": "sine", "freq": 1200, "gain": 0.3, "attack": 0.05, "floor": 0.033, "duration": 1.0})
        self.assertEqual(E.synth_expression(ding), "if(lt(t,0.05),0.3*t/0.05,0.3*pow(0.033,(t-0.05)/0.95))*sin(2*PI*(1200*t))")
        whoosh = E.clean_synth({"type": "sine", "freq": 800, "freqEnd": 100, "sweep": 0.3, "gain": 0.5, "attack": 0.1, "floor": 0.02, "duration": 0.4})
        self.assertEqual(E.synth_expression(whoosh), "if(lt(t,0.1),0.5*t/0.1,0.5*pow(0.02,(t-0.1)/0.3))*sin(2*PI*(if(lt(t,0.3),"
                                                     "-115.415603*(exp(-6.931472*t)-1),100.988653+100*(t-0.3))))")
        square = E.clean_synth({"type": "square", "freq": 440, "duration": 0.2})
        self.assertEqual(E.synth_expression(square), "if(lt(t,0.01),0.3*t/0.01,0.3*pow(0.01,(t-0.01)/0.19))*if(gte(sin(2*PI*(440*t)),0),1,-1)")
        # the whoosh's lowpass sweep (2000 to 200 Hz) as one filter at the sweep's middle
        whoosh_filtered = E.clean_synth({"type": "sine", "freq": 800, "freqEnd": 100, "duration": 0.4,
                                         "filter": {"type": "lowpass", "freq": 2000, "freqEnd": 200}})
        self.assertEqual(whoosh_filtered["filter"], {"type": "lowpass", "freq": 632.4555320336759})
        graph = E.mix_graph(E.sound_lanes(E.clean_sounds([{"t": 0, "kind": "sfx", "synth": {"type": "sine", "freq": 800, "freqEnd": 100,
                                                                                          "duration": 0.4, "filter": {"type": "lowpass", "freq": 2000, "freqEnd": 200}}}], 1.0), 48000), 48000, {})
        self.assertIn(":d=0.4,lowpass=f=632.455532,aresample=48000", graph)
        self.assertNotIn("filter", E.clean_synth({"freq": 100, "duration": 1, "filter": {"type": "notch", "freq": 50}}))
        self.assertIsNone(E.clean_synth({"type": "sine", "freq": 0, "duration": 1}))
        self.assertEqual(E.clean_synth({"type": "noise", "freq": 100, "duration": 99})["type"], "sine")
        self.assertEqual(E.clean_synth({"freq": 100, "duration": 99})["duration"], 10.0)

    @unittest.skipUnless(FFMPEG and E.h264_encoder(), "needs ffmpeg with libx264")
    def test_the_final_video_has_the_frames_the_sound_subtitles_and_chapters(self):
        work = os.path.join(TMP, "render-mix")
        os.makedirs(work, exist_ok=True)
        video = os.path.join(work, "video.mp4")
        tone = os.path.join(work, "tone.wav")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30", "-frames:v", "60",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-an", video], check=True)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", tone], check=True)
        timeline = {"frames": 60, "scenes": [{"t": 0, "title": "Stress"}, {"t": 1.2, "title": "Strain"}],
                    "cues": [{"start": 1.0, "end": 1.5, "text": "Force over area."}],
                    "audio": [{"t": 1.0, "kind": "narration", "src": "/static/tone.wav", "rate": 1, "offset": 0},
                              {"t": 0.1, "kind": "narration", "src": "/static/gone.wav"}]}
        out = os.path.join(work, "final.mp4")
        result = E.mix_render(video, timeline, out, lambda src: tone if src == "/static/tone.wav" else None, work)
        self.assertEqual((result["frames"], result["sounds"], result["skipped"], result["has_subtitles"]), (60, 2, ["/static/gone.wav"], True))
        self.assertEqual(E.video_frames(out), 60)
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,sample_rate:format=duration",
                                           "-show_chapters", "-of", "json", out], capture_output=True, text=True).stdout)
        kinds = {s["codec_type"]: s["codec_name"] for s in probe["streams"]}
        self.assertEqual((kinds["video"], kinds["audio"], kinds["subtitle"]), ("h264", "aac", "mov_text"))
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.03)
        self.assertEqual([c["tags"]["title"] for c in probe["chapters"]], ["Stress", "Strain"])

        def loudness(start):
            res = subprocess.run([FFMPEG, "-hide_banner", "-ss", str(start), "-t", "0.2", "-i", out, "-map", "0:a", "-af", "volumedetect",
                                  "-f", "null", "-"], capture_output=True, text=True)
            return float(re.search(r"mean_volume: (-?[\d.]+) dB", res.stderr).group(1))
        self.assertLess(loudness(0.3), -60)   # silence before the narration (the missing file is left out)
        self.assertGreater(loudness(1.1), -30)  # the narration exactly where the page started it
        self.assertLess(loudness(1.7), -60)
        self.assertFalse(os.path.exists(os.path.join(work, "mix")))
        # one source without sound (a clip with no audio track, a damaged narration file) is left out, never the whole mix
        silent_clip = os.path.join(work, "silent.mp4")
        broken = os.path.join(work, "broken.wav")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=30", "-frames:v", "10", "-an", silent_clip], check=True)
        with open(broken, "wb") as f:
            f.write(b"RIFF\x00\x00\x00\x00WAVEnot really")
        files = {"/static/tone.wav": tone, "/static/silent.mp4": silent_clip, "/static/broken.wav": broken}
        voiced = {**timeline, "audio": [{"t": 0.2, "kind": "video-voice", "src": "/static/silent.mp4"},
                                        {"t": 0.6, "kind": "narration", "src": "/static/broken.wav"},
                                        {"t": 1.0, "kind": "narration", "src": "/static/tone.wav"}]}
        kept = E.mix_render(video, voiced, os.path.join(work, "voiced.mp4"), files.get, work)
        self.assertEqual(sorted(kept["skipped"]), ["/static/broken.wav", "/static/silent.mp4"])
        self.assertGreater(loudness(1.1), -30)
        # a video that does not match its timeline is refused
        with self.assertRaises(E.MixError):
            E.mix_render(video, {**timeline, "frames": 90}, os.path.join(work, "bad.mp4"), lambda src: tone, work)
        self.assertFalse(os.path.exists(os.path.join(work, "bad.mp4")))


if __name__ == "__main__":
    unittest.main()
