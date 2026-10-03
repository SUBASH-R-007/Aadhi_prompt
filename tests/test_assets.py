"""Backend tests for the asset library (assets.py, storage.py, media.py).

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py" -v
Uses the shared throwaway database and storage folder from backend_env; test files written into
static_videos/ (to test registering generator output in place) are removed afterwards.
"""
import datetime
import hashlib
import json
import os
import time
import unittest
import uuid
from unittest import mock

from backend_env import FFMPEG, REPO, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import assets  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
from storage import LocalStorage, StorageError  # noqa: E402

STATIC = os.path.join(REPO, "static_videos")


def read(path):
    with open(path, "rb") as f:
        return f.read()


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class AssetLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.client = TestClient(server.app)
        cls.library = server.asset_library
        cls.ids = {name: ensure_user(name) for name in ("alice", "bob", "carol")}
        media = os.path.join(TMP, "asset-media")
        os.makedirs(media, exist_ok=True)
        cls.files = {name: os.path.join(media, name) for name in
                     ("image.png", "image2.png", "audio.mp3", "video.mp4", "video2.mp4", "truncated.mp4")}
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48", "-frames:v", "1", cls.files["image.png"])
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48", "-frames:v", "1", cls.files["image2.png"])
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libmp3lame", cls.files["audio.mp3"])
        for name, pattern in (("video.mp4", "testsrc"), ("video2.mp4", "testsrc2")):
            ffmpeg("-f", "lavfi", "-i", f"{pattern}=size=160x120:rate=10:duration=1", "-f", "lavfi", "-i", "sine=duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.files[name])
        with open(cls.files["truncated.mp4"], "wb") as f:
            f.write(read(cls.files["video.mp4"])[:300])
        cls.static_files = []

    @classmethod
    def tearDownClass(cls):
        for path in cls.static_files:
            if os.path.exists(path):
                os.remove(path)

    # ---- helpers ----

    def fresh(self, kind):
        """A new file whose content no test has uploaded before (tests share one library)."""
        tag = uuid.uuid4().hex
        path = os.path.join(TMP, "asset-media", f"{tag}.{'png' if kind == 'image' else 'mp4'}")
        color = f"0x{tag[:6]}"
        if kind == "image":
            ffmpeg("-f", "lavfi", "-i", f"color=c={color}:size=64x48", "-frames:v", "1", path)
        else:
            ffmpeg("-f", "lavfi", "-i", f"color=c={color}:size=160x120:rate=10:duration=1",
                   "-f", "lavfi", "-i", f"sine=frequency={200 + int(tag[6:9], 16) % 800}:duration=1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)
        return path

    def upload(self, user, path_or_bytes, name=None, content_type="application/octet-stream"):
        data = read(path_or_bytes) if isinstance(path_or_bytes, str) else path_or_bytes
        name = name or (os.path.basename(path_or_bytes) if isinstance(path_or_bytes, str) else "file.bin")
        return self.client.post("/api/assets", files={"file": (name, data, content_type)}, headers=auth(user))

    def uploaded(self, user, path, name=None):
        res = self.upload(user, path, name)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["asset"]

    def row(self, asset_id):
        db = database.SessionLocal()
        try:
            return db.get(models.Asset, asset_id)
        finally:
            db.close()

    def blob_path(self, asset_id):
        return self.library.file_path(self.row(asset_id))

    def static_copy(self, src, prefix):
        name = f"test_{prefix}_{uuid.uuid4().hex[:8]}{os.path.splitext(src)[1]}"
        path = os.path.join(STATIC, name)
        with open(path, "wb") as f:
            f.write(read(src))
        self.static_files.append(path)
        return name, path

    def save_project(self, user, scenes):
        res = self.client.post("/save-history", json={"subject_name": "Asset test", "scenes": scenes}, headers=auth(user))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["id"]

    def content(self, asset_id, user=None, token=None, headers=None):
        url = f"/api/assets/{asset_id}/content" + (f"?token={token}" if token else "")
        return self.client.get(url, headers={**(auth(user) if user else {}), **(headers or {})})

    def link(self, user, asset_id):
        return self.client.post("/api/assets/resolve", json={"ids": [asset_id]}, headers=auth(user)).json()

    # ---- registration and metadata ---------------------------------------------------

    # ---- description and keywords (PATCH /api/assets/{id}) ------------------------------

    def patch(self, user, asset_id, body):
        return self.client.patch(f"/api/assets/{asset_id}", json=body, headers=auth(user))

    def test_describing_an_asset_with_a_description_and_keywords(self):
        asset = self.uploaded("alice", self.fresh("video"))
        self.assertEqual(asset["details"], {})  # uploads start undescribed
        res = self.patch("alice", asset["id"], {"description": "  A steel bridge\n  carrying heavy   traffic  "})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["details"], {"description": "A steel bridge carrying heavy traffic"})
        res = self.patch("alice", asset["id"], {"keywords": ["bridge", " traffic ", "structural   load"]})
        # Keywords added; the description (left out of the request) is kept
        self.assertEqual(res.json()["details"], {"description": "A steel bridge carrying heavy traffic",
                                                 "keywords": ["bridge", "traffic", "structural load"]})
        # Shown wherever the asset is listed or opened
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}", headers=auth("alice")).json()["details"]["keywords"],
                         ["bridge", "traffic", "structural load"])
        listed = self.client.get("/api/assets?scope=mine&limit=200", headers=auth("alice")).json()["assets"]
        self.assertEqual(next(a for a in listed if a["id"] == asset["id"])["details"]["description"], "A steel bridge carrying heavy traffic")

    def test_repeated_keywords_are_stored_once(self):
        asset = self.uploaded("alice", self.fresh("image"))
        res = self.patch("alice", asset["id"], {"keywords": ["Bridge", "bridge", " BRIDGE ", "", "  ", "load", "Load", "stress"]})
        self.assertEqual(res.json()["details"]["keywords"], ["Bridge", "load", "stress"])  # first spelling kept

    def test_empty_values_clear_the_fields_and_other_details_are_kept(self):
        asset = self.uploaded("alice", self.fresh("video"))
        with database.SessionLocal() as db:  # e.g. a generated video's prompt, kept by the library
            db.get(models.Asset, asset["id"]).details = json.dumps({"prompt": "a bridge at dusk"})
            db.commit()
        self.patch("alice", asset["id"], {"description": "Bridge at dusk", "keywords": ["bridge"]})
        res = self.patch("alice", asset["id"], {"description": "   ", "keywords": []})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["details"], {"prompt": "a bridge at dusk"})
        self.assertEqual(self.patch("alice", asset["id"], {}).json()["details"], {"prompt": "a bridge at dusk"})  # nothing to change

    def test_description_and_keyword_limits(self):
        asset = self.uploaded("alice", self.fresh("image"))
        lesson_text = ("A labelled cross-section of a reinforced concrete beam under a uniformly distributed load, "
                       "showing the compression zone, the neutral axis and the tension steel. ") * 6  # a long, normal description
        self.assertEqual(self.patch("alice", asset["id"], {"description": lesson_text}).status_code, 200)
        self.assertEqual(self.patch("alice", asset["id"], {"description": "x" * 1000}).status_code, 200)
        self.assertEqual(self.patch("alice", asset["id"], {"keywords": ["k" * 60] + [f"word{i}" for i in range(29)]}).status_code, 200)
        before = self.row(asset["id"]).details
        for body, reason in (({"description": "x" * 1001}, "1000 characters"),
                             ({"keywords": ["k" * 61]}, "60 characters"),
                             ({"keywords": [f"word{i}" for i in range(31)]}, "at most 30"),
                             ({"keywords": "bridge, load"}, None)):  # must be a list
            res = self.patch("alice", asset["id"], body)
            self.assertEqual(res.status_code, 422, body)
            if reason:
                self.assertIn(reason, res.json()["detail"])
        self.assertEqual(self.row(asset["id"]).details, before)  # a rejected edit changes nothing

    def test_only_your_own_assets_can_be_described(self):
        asset = self.uploaded("alice", self.fresh("image"))
        self.assertEqual(self.patch("bob", asset["id"], {"description": "mine now"}).status_code, 404)
        with database.SessionLocal() as db:
            system_id = db.query(models.Asset).filter(models.Asset.owner_id.is_(None)).first().id
            system_details = db.get(models.Asset, system_id).details
        self.assertEqual(self.patch("alice", system_id, {"description": "Aadhi waving"}).status_code, 403)
        self.assertEqual(self.row(system_id).details, system_details)
        self.client.delete(f"/api/assets/{asset['id']}", headers=auth("alice"))
        self.assertEqual(self.patch("alice", asset["id"], {"description": "too late"}).status_code, 404)
        self.assertEqual(self.patch("alice", "0" * 32, {"description": "x"}).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/assets/{asset['id']}", json={"description": "x"}).status_code, 401)
        self.assertIsNone(self.row(asset["id"]).details)  # nothing was written by any refused edit

    def test_login_required(self):
        self.assertEqual(self.client.get("/api/assets").status_code, 401)
        self.assertEqual(self.client.post("/api/assets", files={"file": ("a.png", b"x")}).status_code, 401)
        self.assertEqual(self.client.post("/api/assets/resolve", json={"ids": []}).status_code, 401)
        self.assertEqual(self.content("0" * 32).status_code, 401)

    def test_upload_registers_an_image_with_metadata_at_a_server_chosen_path(self):
        image = self.fresh("image")
        res = self.upload("alice", image, name="../../evil <x>.png", content_type="image/png")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        asset = body["asset"]
        self.assertEqual((asset["kind"], asset["mime_type"], asset["width"], asset["height"]), ("image", "image/png", 64, 48))
        self.assertEqual(asset["sha256"], hashlib.sha256(read(image)).hexdigest())
        self.assertEqual((asset["status"], asset["scope"], asset["owned"], asset["source"]), ("ready", "private", True, "upload"))
        self.assertEqual(asset["file_name"], "evil _x_.png")  # display name only
        self.assertNotIn("storage_key", asset)
        row = self.row(asset["id"])
        self.assertEqual(row.storage_volume, "assets")
        self.assertEqual(row.storage_key, f"blobs/{asset['sha256'][:2]}/{asset['sha256']}.png")
        self.assertTrue(os.path.isfile(self.blob_path(asset["id"])))
        self.assertTrue(os.path.abspath(self.blob_path(asset["id"])).startswith(os.path.abspath(os.environ["ASSETS_DIR"])))
        self.assertFalse(os.listdir(os.path.join(os.environ["ASSETS_DIR"], "tmp")))  # upload temp file cleaned up

    def test_audio_and_video_metadata(self):
        audio = self.uploaded("alice", self.files["audio.mp3"])
        self.assertEqual((audio["kind"], audio["mime_type"]), ("audio", "audio/mpeg"))
        self.assertAlmostEqual(audio["duration_seconds"], 1.0, delta=0.2)
        video = self.uploaded("alice", self.files["video.mp4"])
        self.assertEqual((video["kind"], video["mime_type"], video["width"], video["height"], video["has_audio"]),
                         ("video", "video/mp4", 160, 120, True))
        self.assertAlmostEqual(video["duration_seconds"], 1.0, delta=0.2)

    def test_invalid_files_never_enter_the_library(self):
        before = self.client.get("/api/assets?scope=mine", headers=auth("carol")).json()["total"]
        cases = [
            ("notes.txt", b"just some text, not media", "text/plain"),
            ("photo.png", b"<html><script>alert(1)</script></html>" * 5, "image/png"),  # MIME spoofing
            ("drawing.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
            ("empty.mp4", b"", "video/mp4"),
            ("broken.mp4", read(self.files["truncated.mp4"]), "video/mp4"),
        ]
        for name, data, content_type in cases:
            res = self.upload("carol", data, name=name, content_type=content_type)
            self.assertEqual(res.status_code, 400, f"{name}: {res.text}")
            self.assertIn("cannot be added", res.json()["detail"])
        self.assertEqual(self.client.get("/api/assets?scope=mine", headers=auth("carol")).json()["total"], before)

    # ---- deduplication ---------------------------------------------------------------

    def test_same_content_is_one_asset_whatever_its_name(self):
        image = self.fresh("image")
        first = self.upload("alice", image, name="first.png").json()
        second = self.upload("alice", image, name="renamed.png").json()
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(first["asset"]["id"], second["asset"]["id"])

    def test_same_name_different_content_is_not_merged(self):
        a = self.uploaded("carol", self.files["image.png"], name="same.png")
        b = self.uploaded("carol", self.files["image2.png"], name="same.png")
        self.assertNotEqual(a["id"], b["id"])

    def test_same_content_for_another_user_shares_the_stored_file(self):
        video = self.fresh("video")
        mine = self.uploaded("alice", video)
        theirs = self.uploaded("bob", video)
        self.assertNotEqual(mine["id"], theirs["id"])  # separate ownership...
        self.assertEqual(self.blob_path(mine["id"]), self.blob_path(theirs["id"]))  # ...one physical file

    # ---- listing and access control ---------------------------------------------------

    def test_listing_filters_and_shows_only_own_and_shared_assets(self):
        image = self.uploaded("carol", self.files["image.png"])
        audio = self.uploaded("carol", self.files["audio.mp3"])
        bobs = self.uploaded("bob", self.files["video.mp4"])
        listed = {a["id"]: a for a in self.client.get("/api/assets?limit=200", headers=auth("carol")).json()["assets"]}
        self.assertIn(image["id"], listed)
        self.assertIn(audio["id"], listed)
        self.assertNotIn(bobs["id"], listed)
        self.assertTrue(any(a["source"] == "mascot" and a["scope"] == "system" for a in listed.values()))

        kinds = {a["kind"] for a in self.client.get("/api/assets?kind=audio", headers=auth("carol")).json()["assets"]}
        self.assertEqual(kinds, {"audio"})
        system = self.client.get("/api/assets?scope=system", headers=auth("carol")).json()["assets"]
        self.assertTrue(system and all(a["scope"] == "system" for a in system))
        mascot = self.client.get("/api/assets?source=mascot&kind=video", headers=auth("carol")).json()["assets"]
        self.assertEqual({a["details"]["placement"] for a in mascot}, {"left", "right", "center", "popup", "hidden"})
        named = self.client.get("/api/assets?q=audio", headers=auth("carol")).json()["assets"]
        self.assertIn(audio["id"], [a["id"] for a in named])

    def test_system_assets_are_shared_and_protected(self):
        system = self.client.get("/api/assets?scope=system&kind=video&source=mascot", headers=auth("alice")).json()["assets"]
        left = next(a for a in system if a["details"]["placement"] == "left")
        self.assertEqual(self.row(left["id"]).storage_volume, "system")  # registered in place, not copied
        for user in ("alice", "bob"):
            res = self.content(left["id"], user=user, headers={"Range": "bytes=0-99"})
            self.assertEqual(res.status_code, 206)
            self.assertEqual(res.headers["content-type"], "video/mp4")
        res = self.client.delete(f"/api/assets/{left['id']}", headers=auth("alice"))
        self.assertEqual(res.status_code, 403)

    def test_another_user_cannot_reach_a_private_asset(self):
        asset = self.uploaded("alice", self.files["image.png"])
        aid = asset["id"]
        self.assertEqual(self.client.get(f"/api/assets/{aid}", headers=auth("bob")).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/assets/{aid}", headers=auth("bob")).status_code, 404)
        self.assertEqual(self.content(aid, user="bob").status_code, 404)
        self.assertEqual(self.link("bob", aid), {"assets": {}, "missing": [aid]})
        # A link issued to alice works without a login header, only for that asset, and is not a login
        url = self.link("alice", aid)["assets"][aid]["url"]
        token = url.split("token=")[1]
        self.assertEqual(self.client.get(url).status_code, 200)
        other = self.uploaded("alice", self.files["audio.mp3"])
        self.assertEqual(self.content(other["id"], token=token).status_code, 403)
        self.assertEqual(self.client.get("/api/assets", headers={"Authorization": "Bearer " + token}).status_code, 401)
        forged = jwt.encode({"sub": "bob", "scope": "asset", "rid": aid,
                             "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=5)}, server.SECRET_KEY, algorithm="HS256")
        self.assertEqual(self.content(aid, token=forged).status_code, 404)
        expired = jwt.encode({"sub": "alice", "scope": "asset", "rid": aid,
                              "exp": datetime.datetime.utcnow() - datetime.timedelta(minutes=5)}, server.SECRET_KEY, algorithm="HS256")
        self.assertEqual(self.content(aid, token=expired).status_code, 401)

    def test_content_is_served_safely(self):
        image = self.fresh("image")
        asset = self.uploaded("alice", image, name="Diagram 1.png")
        res = self.content(asset["id"], user="alice")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "image/png")
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        # Names with spaces are sent in the RFC 5987 form
        self.assertEqual(res.headers["content-disposition"], "inline; filename*=utf-8''Diagram%201.png")
        self.assertEqual(res.content, read(image))
        self.assertEqual(self.content(asset["id"], user="alice", headers={"Range": "bytes=0-9"}).status_code, 206)

    def test_resolve_gives_urls_for_usable_assets_only(self):
        mine = self.uploaded("alice", self.files["image.png"])
        bobs = self.uploaded("bob", self.files["image2.png"])
        ids = [mine["id"], "f" * 32, bobs["id"], "../../etc/passwd"]
        body = self.client.post("/api/assets/resolve", json={"ids": ids}, headers=auth("alice")).json()
        self.assertEqual(list(body["assets"]), [mine["id"]])
        self.assertTrue(body["assets"][mine["id"]]["url"].startswith(f"/api/assets/{mine['id']}/content?token="))
        self.assertEqual(body["missing"], ids[1:])

    # ---- missing files, deletion, references -------------------------------------

    def test_a_missing_file_is_reported_not_served(self):
        video = self.fresh("video")
        asset = self.uploaded("carol", video)
        os.remove(self.blob_path(asset["id"]))
        res = self.content(asset["id"], user="carol")
        self.assertEqual(res.status_code, 410)
        self.assertEqual(self.row(asset["id"]).status, "failed")
        self.assertEqual(self.link("carol", asset["id"])["missing"], [asset["id"]])
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}", headers=auth("carol")).json()["status"], "failed")
        # Registering the same content again repairs it
        again = self.uploaded("carol", video)
        self.assertEqual(again["id"], asset["id"])
        self.assertEqual(again["status"], "ready")
        self.assertEqual(self.content(asset["id"], user="carol").status_code, 200)

    def test_unknown_asset(self):
        self.assertEqual(self.client.get(f"/api/assets/{'a' * 32}", headers=auth("alice")).status_code, 404)
        self.assertEqual(self.client.get("/api/assets/not-an-id", headers=auth("alice")).status_code, 404)

    def test_deleting_an_unused_asset_removes_its_file(self):
        asset = self.uploaded("bob", self.fresh("video"), name="unused.mp4")
        path = self.blob_path(asset["id"])
        res = self.client.delete(f"/api/assets/{asset['id']}", headers=auth("bob"))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.row(asset["id"]).status, "deleted")
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}", headers=auth("bob")).status_code, 404)
        self.assertNotIn(asset["id"], [a["id"] for a in self.client.get("/api/assets?limit=200", headers=auth("bob")).json()["assets"]])
        # Nobody else stored this content, so its file goes too
        self.assertTrue(res.json()["file_removed"])
        self.assertFalse(os.path.exists(path))

    def test_deleting_one_owners_copy_keeps_the_shared_file(self):
        image = self.fresh("image")
        alice = self.uploaded("alice", image, name="shared.png")
        bob = self.uploaded("bob", image, name="shared.png")
        res = self.client.delete(f"/api/assets/{bob['id']}", headers=auth("bob"))
        self.assertEqual(res.json(), {"deleted": True, "file_removed": False})
        self.assertEqual(self.content(alice["id"], user="alice").status_code, 200)
        # Uploading it again brings the same asset back
        back = self.uploaded("bob", image)
        self.assertEqual(back["id"], bob["id"])
        self.assertEqual(back["status"], "ready")

    def test_an_asset_used_by_a_saved_lesson_is_kept(self):
        video = self.uploaded("alice", self.fresh("video"), name="lesson clip.mp4")
        project = self.save_project("alice", [{"type": "ai_video", "title": "Clip", "video_asset_id": video["id"]}])
        res = self.client.delete(f"/api/assets/{video['id']}", headers=auth("alice"))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"]["references"], 1)
        detail = self.client.get(f"/api/assets/{video['id']}", headers=auth("alice")).json()
        self.assertEqual(detail["used_in"], [{"project_id": project, "field": "scenes[0].video_asset_id", "title": "Asset test"}])
        self.assertFalse(detail["deletable"])
        self.assertTrue(os.path.isfile(self.blob_path(video["id"])))

    def test_someone_elses_lesson_cannot_pin_your_asset(self):
        asset = self.uploaded("carol", self.files["audio.mp3"], name="carol only.mp3")
        self.save_project("bob", [{"type": "content", "video_asset_id": asset["id"], "html": f'<img src="asset:{asset["id"]}">'}])
        self.assertEqual(self.client.get(f"/api/assets/{asset['id']}", headers=auth("carol")).json()["references"], 0)

    def test_lesson_references_are_found_everywhere_lessons_point_at_media(self):
        image = self.uploaded("alice", self.files["image.png"])
        video = self.uploaded("alice", self.files["video.mp4"])
        audio = self.uploaded("alice", self.files["audio.mp3"])
        legacy_name, legacy_path = self.static_copy(self.files["video2.mp4"], "legacy")
        scenes = [
            {"type": "ai_video", "video_asset_id": video["id"]},
            {"type": "simulation", "manim_asset_id": video["id"], "side_panel": {"type": "manim", "video_asset_id": audio["id"]}},
            {"type": "content", "html": f'<p>x</p><img src="asset:{image["id"]}" alt="d">',
             "uploaded_image_assets": {"img1": image["id"]}},
            {"type": "ai_video", "video_url": f"/static/{legacy_name}?t=123"},  # an older lesson, plain URL
        ]
        project = self.save_project("alice", scenes)
        db = database.SessionLocal()
        refs = {(r.field, r.asset_id) for r in db.query(models.AssetReference).filter(models.AssetReference.project_id == project)}
        legacy = db.query(models.Asset).filter(models.Asset.storage_volume == "static", models.Asset.storage_key == legacy_name,
                                               models.Asset.owner_id == self.ids["alice"]).first()
        db.close()
        self.assertIsNotNone(legacy)  # registered where it is...
        self.assertEqual(os.path.getsize(legacy_path), legacy.file_size)
        self.assertEqual(refs, {
            ("scenes[0].video_asset_id", video["id"]),
            ("scenes[1].manim_asset_id", video["id"]),
            ("scenes[1].side_panel.video_asset_id", audio["id"]),
            ("scenes[2].html", image["id"]),
            ("scenes[2].uploaded_image_assets.img1", image["id"]),
            ("scenes[3].video_url", legacy.id),
        })

    # ---- generator output and path safety -----------------------------------------

    def test_generator_output_is_registered_in_place_once(self):
        name, path = self.static_copy(self.files["video.mp4"], "manim")
        db = database.SessionLocal()
        try:
            first = self.library.attach(db, {"status": "success", "video_url": f"/static/{name}"}, "video_url",
                                        owner_id=self.ids["alice"], source="manim", details={"scene": "Test"})
            with mock.patch("assets.inspect_media", side_effect=AssertionError("re-read an unchanged file")):
                second = self.library.attach(db, {"video_url": f"/static/{name}?t=9"}, "video_url",
                                             owner_id=self.ids["alice"], source="manim")
                # Another user's registration of the same file reuses the checked metadata too
                third = self.library.attach(db, {"video_url": f"/static/{name}"}, "video_url",
                                            owner_id=self.ids["bob"], source="manim")
        finally:
            db.close()
        self.assertEqual(first["asset_id"], second["asset_id"])
        self.assertNotEqual(first["asset_id"], third["asset_id"])
        row = self.row(first["asset_id"])
        self.assertEqual((row.storage_volume, row.storage_key, row.source), ("static", name, "manim"))
        self.assertEqual(json.loads(row.details), {"scene": "Test"})
        # A failed registration never breaks the generator's own response
        with database.SessionLocal() as db:
            result = self.library.attach(db, {"video_url": "/static/does-not-exist.mp4"}, "video_url",
                                         owner_id=self.ids["alice"], source="manim")
        self.assertNotIn("asset_id", result)

    def test_upload_media_keeps_files_inside_its_folder_and_registers_them(self):
        res = self.client.post("/upload-media", files={"file": ("../../escape attempt.mp4", read(self.files["video.mp4"]), "video/mp4")},
                               headers=auth("alice"))
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        name = body["url"][len("/static/"):]
        self.static_files.append(os.path.join(STATIC, name))
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.endswith("_escape_attempt.mp4"))
        self.assertTrue(os.path.isfile(os.path.join(STATIC, name)))
        self.assertEqual(self.row(body["asset_id"]).source, "upload")

    def test_paths_are_always_server_controlled(self):
        storage = LocalStorage(TMP)
        for key in ("../x", "a/../../x", "/etc/passwd", "C:/Windows/win.ini", "a\\..\\b", "", "a//b"):
            with self.assertRaises(StorageError, msg=key):
                storage.path(key)
        with database.SessionLocal() as db:
            for url in ("/static/../server.py", "/static/sub/x.mp4", "/static/.env", "https://evil.test/x.mp4", "/etc/passwd"):
                self.assertIsNone(self.library.adopt_url(db, url, owner_id=self.ids["alice"], source="upload"), url)
            # A shared file linked by a user stays the one shared asset, never a private copy
            shared = self.library.adopt_url(db, "/video_template/aadhi_left.mp4", owner_id=self.ids["alice"], source="upload")
            self.assertIsNone(shared.owner_id)

    # ---- performance ---------------------------------------------------------------

    def test_listing_and_resolving_many_assets_reads_no_files(self):
        base = self.row(self.uploaded("alice", self.files["video.mp4"])["id"])
        db = database.SessionLocal()
        ids = []
        for i in range(300):
            asset = models.Asset(id=uuid.uuid4().hex, scope_key="user:%d" % self.ids["alice"], owner_id=self.ids["alice"],
                                 kind="video", source="upload", status="ready", file_name=f"bulk{i}.mp4", mime_type="video/mp4",
                                 storage_volume="assets", storage_key=f"bulk/{i}.mp4", file_size=base.file_size, sha256=base.sha256)
            db.add(asset)
            ids.append(asset.id)
        db.commit()
        db.close()
        with mock.patch("assets.inspect_media", side_effect=AssertionError("listing must not inspect files")), \
                mock.patch("assets.sha256_file", side_effect=AssertionError("listing must not hash files")):
            start = time.perf_counter()
            listed = self.client.get("/api/assets?limit=200", headers=auth("alice")).json()
            listing = time.perf_counter() - start
        self.assertEqual(len(listed["assets"]), 200)
        self.assertGreaterEqual(listed["total"], 300)
        self.assertLess(listing, 2.0)
        start = time.perf_counter()
        body = self.client.post("/api/assets/resolve", json={"ids": ids}, headers=auth("alice")).json()
        self.assertLess(time.perf_counter() - start, 3.0)
        self.assertEqual(len(body["missing"]), 300)  # rows without files: reported, never served
        with database.SessionLocal() as db:
            db.query(models.Asset).filter(models.Asset.storage_key.like("bulk/%")).delete(synchronize_session=False)
            db.commit()


if __name__ == "__main__":
    unittest.main()
