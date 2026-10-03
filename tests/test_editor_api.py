"""Phase 19 — the Advanced Video Editor's saving route (editor_api.py): an edited lesson stored in place (the same history
entry) with a revision check, scene ids, the editor data contract, asset ownership, structural edits refused while media is
being generated, asset references kept current, editor data kept by /save-history, old lessons unaffected.

Run: python -m unittest discover -s tests -p "test_editor_api.py"
"""
import copy
import json
import time
import unittest
import uuid

from backend_env import assert_isolated, auth, ensure_user  # noqa: F401  first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import editor_api as E  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402


def scenes():
    return [{"type": "content", "title": f"Scene {n}", "html": f"<p>Point {n}</p>", "narration": f"Narration {n}."} for n in range(4)]


class ContractTest(unittest.TestCase):
    def test_scene_ids(self):
        s = scenes()
        s[2]["scene_id"] = "s-aaaaaaaaaaaa"
        s[3]["scene_id"] = "s-aaaaaaaaaaaa"  # a duplicate id
        self.assertEqual(E.ensure_ids(s), 3)
        ids = [x["scene_id"] for x in s]
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(all(E.SCENE_ID.fullmatch(x) for x in ids))
        self.assertEqual(ids[2], "s-aaaaaaaaaaaa", "a valid unique id is kept")
        self.assertEqual(E.ensure_ids(s), 0)

    def test_edit_data_is_cleaned_to_its_contract(self):
        self.assertIsNone(E.clean_edit(None))
        self.assertIsNone(E.clean_edit({"hidden": False, "narration_muted": False, "origin": "generated"}))
        self.assertEqual(E.clean_edit({"hidden": True, "min_seconds": 8.456, "captions": "off", "narration_muted": True,
                                       "origin": "duplicated", "from": "s-0123456789ab", "unknown": 1,
                                       "original": {"title": "T", "labels": ["a", "b"], "evil": "<script>"}}),
                         {"hidden": True, "min_seconds": 8.46, "captions": "off", "narration_muted": True, "origin": "duplicated",
                          "from": "s-0123456789ab", "original": {"title": "T", "labels": ["a", "b"]}})
        # what the editor model writes: labels as {text, at}, an empty original for a field that was absent
        self.assertEqual(E.clean_edit({"original": {"labels": ["F = force", {"text": "m = mass", "at": {"sync": 2}}, {"text": "a", "at": 3}],
                                                    "subtitle": ""}}),
                         {"original": {"labels": ["F = force", {"text": "m = mass", "at": {"sync": 2}}, {"text": "a", "at": 3}], "subtitle": ""}})
        for bad_label in ({"text": "x", "at": True}, {"text": "x", "evil": 1}, {"text": 5}, {"text": "x", "at": {"sync": "2"}}):
            with self.subTest(label=bad_label), self.assertRaises(E.Invalid):
                E.clean_edit({"original": {"labels": [bad_label]}})
        for bad in ({"hidden": "yes"}, {"min_seconds": 0}, {"min_seconds": 601}, {"min_seconds": float("inf")}, {"min_seconds": True},
                    {"min_seconds": "8"}, {"captions": "on"}, {"origin": "magic"}, {"from": "../x"}, {"original": {"title": 5}},
                    {"original": {"labels": ["x"] * 7}}, {"original": "x"}, "edit", [1]):
            with self.subTest(bad=bad), self.assertRaises(E.Invalid):
                E.clean_edit(bad)

    def test_editor_data_is_cleaned(self):
        self.assertEqual(E.clean_editor({"captions": {"visible": False}, "generated_order": ["s-0123456789ab", "s-0123456789ab"], "x": 1}, set()),
                         {"version": 1, "captions": {"visible": False}, "generated_order": ["s-0123456789ab"]})
        for bad in ({"captions": "off"}, {"captions": {"visible": "no"}}, {"generated_order": ["x"]}, {"generated_order": "s-0123456789ab"}, []):
            with self.subTest(bad=bad), self.assertRaises(E.Invalid):
                E.clean_editor(bad, set())
        self.assertIsNone(E.clean_lesson_editor({"captions": "bad"}))


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user("edna")
        cls.other = ensure_user("ezra")
        cls.client = TestClient(server.app)
        cls.headers = auth("edna")

    def save_lesson(self, items=None):
        r = self.client.post("/save-history", json={"subject_name": "Editor", "scenes": items if items is not None else scenes()}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def load(self, pid, headers=None):
        return self.client.get(f"/api/editor/{pid}", headers=headers or self.headers)

    def put(self, pid, body, headers=None):
        return self.client.put(f"/api/editor/{pid}", json=body, headers=headers or self.headers)

    def rows(self):
        with database.SessionLocal() as db:
            return db.query(models.Project).filter(models.Project.user_id == self.uid).count()

    def test_load_gives_ids_and_a_revision_and_save_is_in_place(self):
        pid = self.save_lesson()
        data = self.load(pid).json()
        self.assertEqual(len(data["scenes"]), 4)
        self.assertTrue(all(E.SCENE_ID.fullmatch(s["scene_id"]) for s in data["scenes"]))
        self.assertIsNone(data["editor"])
        before = self.rows()
        edited = data["scenes"]
        edited.insert(0, edited.pop(2))  # reorder
        edited[1]["edit"] = {"min_seconds": 9, "hidden": True}
        r = self.put(pid, {"expected_revision": data["revision"], "scenes": edited,
                           "editor": {"captions": {"visible": False}, "generated_order": [s["scene_id"] for s in data["scenes"]]}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotEqual(r.json()["revision"], data["revision"])
        self.assertEqual(self.rows(), before, "an edit is not a new history entry")
        again = self.load(pid).json()
        self.assertEqual([s["scene_id"] for s in again["scenes"]], [s["scene_id"] for s in edited])
        self.assertEqual(again["scenes"][1]["edit"], {"min_seconds": 9.0, "hidden": True})
        self.assertEqual(again["editor"]["captions"], {"visible": False})
        self.assertEqual(again["revision"], r.json()["revision"])
        saved = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()
        self.assertEqual(saved["scenes"][0]["title"], "Scene 2", "the lesson itself is what the editor saved")

    def test_a_stale_copy_is_refused(self):
        pid = self.save_lesson()
        data = self.load(pid).json()
        time.sleep(0.01)
        first = self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]})
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["revision"], first.json()["revision"])
        # another writer (a Visual Review decision, a generation that finished) also makes the copy stale
        with database.SessionLocal() as db:
            project = db.get(models.Project, pid)
            payload = json.loads(project.json_data)
            payload["scenes"][0]["visual_review"] = {"side": {"status": "approved"}}
            time.sleep(0.01)
            project.json_data = json.dumps(payload)
            db.commit()
        self.assertEqual(self.put(pid, {"expected_revision": first.json()["revision"], "scenes": data["scenes"]}).status_code, 409)

    def test_ownership_and_validation(self):
        pid = self.save_lesson()
        data = self.load(pid).json()
        self.assertEqual(self.load(pid, auth("ezra")).status_code, 404)
        self.assertEqual(self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]}, auth("ezra")).status_code, 404)
        self.assertEqual(self.load(999999).status_code, 404)
        self.assertEqual(self.client.get(f"/api/editor/{pid}").status_code, 401)
        good = data["scenes"]
        for name, change in (("no scenes", lambda s: []), ("too many", lambda s: s * 60),
                             ("no id", lambda s: [{k: v for k, v in s[0].items() if k != "scene_id"}] + s[1:]),
                             ("bad id", lambda s: [{**s[0], "scene_id": "../../etc"}] + s[1:]),
                             ("shared id", lambda s: [s[0], {**s[1], "scene_id": s[0]["scene_id"]}] + s[2:]),
                             ("bad edit", lambda s: [{**s[0], "edit": {"min_seconds": -1}}] + s[1:]),
                             ("not an object", lambda s: ["scene"] + s[1:])):
            with self.subTest(name=name):
                r = self.put(pid, {"expected_revision": data["revision"], "scenes": change(copy.deepcopy(good))})
                self.assertEqual(r.status_code, 422, (name, r.text))
        r = self.put(pid, {"expected_revision": data["revision"], "scenes": good, "editor": {"captions": "off"}})
        self.assertEqual(r.status_code, 422)

    def test_another_users_asset_is_refused_and_references_follow_the_lesson(self):
        mine, theirs = uuid.uuid4().hex, uuid.uuid4().hex
        with database.SessionLocal() as db:
            for asset_id, owner in ((mine, self.uid), (theirs, self.other)):
                db.add(models.Asset(id=asset_id, scope_key=f"user:{owner}", owner_id=owner, kind="image", source="upload", status="ready",
                                    file_name="x.png", mime_type="image/png", storage_volume="assets", storage_key=f"x/{asset_id}.png",
                                    file_size=10, width=10, height=10))
            db.commit()
        pid = self.save_lesson()
        data = self.load(pid).json()
        with_mine = copy.deepcopy(data["scenes"])
        with_mine[1]["html"] = f'<img src="asset:{mine}" alt="">'
        r = self.put(pid, {"expected_revision": data["revision"], "scenes": with_mine})
        self.assertEqual(r.status_code, 200, r.text)
        with database.SessionLocal() as db:
            refs = {x.asset_id for x in db.query(models.AssetReference).filter(models.AssetReference.project_id == pid).all()}
        self.assertIn(mine, refs, "an asset the edited lesson uses cannot be deleted")
        with_theirs = copy.deepcopy(with_mine)
        with_theirs[2]["html"] = f'<img src="asset:{theirs}" alt="">'
        self.assertEqual(self.put(pid, {"expected_revision": r.json()["revision"], "scenes": with_theirs}).status_code, 422)
        # removing the scene that used the asset removes the reference (the asset itself is never deleted)
        rev = self.load(pid).json()["revision"]
        removed = [s for s in with_mine if s["scene_id"] != with_mine[1]["scene_id"]]
        self.assertEqual(self.put(pid, {"expected_revision": rev, "scenes": removed}).status_code, 200)
        with database.SessionLocal() as db:
            self.assertIsNotNone(db.get(models.Asset, mine))
            refs = {x.asset_id for x in db.query(models.AssetReference).filter(models.AssetReference.project_id == pid).all()}
        self.assertNotIn(mine, refs)

    def test_structural_edits_wait_for_generation(self):
        pid = self.save_lesson()
        data = self.load(pid).json()
        rev = self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]}).json()["revision"]  # ids stored
        with database.SessionLocal() as db:
            db.add(models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="image",
                                          status="running", provider="maker", model="m", project_id=pid, scene_index=2, slot="side",
                                          request="{}"))
            db.commit()
        self.assertEqual(self.load(pid).json()["generating"], 1)
        reordered = copy.deepcopy(data["scenes"])
        reordered.reverse()
        r = self.put(pid, {"expected_revision": rev, "scenes": reordered})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["generating"], 1)
        props = copy.deepcopy(data["scenes"])
        props[0]["edit"] = {"min_seconds": 6}
        self.assertEqual(self.put(pid, {"expected_revision": rev, "scenes": props}).status_code, 200, "property edits are allowed")

    def test_an_old_lessons_first_save_only_adds_ids_even_while_generating(self):
        pid = self.save_lesson()
        with database.SessionLocal() as db:
            db.add(models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="image",
                                          status="queued", provider="maker", model="m", project_id=pid, scene_index=0, slot="side", request="{}"))
            db.commit()
        data = self.load(pid).json()
        self.assertEqual(self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]}).status_code, 200)

    def test_save_history_keeps_the_editor_data(self):
        r = self.client.post("/save-history", json={"subject_name": "Versioned", "scenes": scenes(),
                                                    "editor": {"captions": {"visible": False}, "generated_order": ["s-0123456789ab"]}},
                             headers=self.headers)
        saved = self.client.get(f"/api/projects/{r.json()['id']}", headers=self.headers).json()
        self.assertEqual(saved["editor"], {"version": 1, "captions": {"visible": False}, "generated_order": ["s-0123456789ab"]})
        r = self.client.post("/save-history", json={"subject_name": "Bad", "scenes": scenes(), "editor": {"captions": "bad"}}, headers=self.headers)
        self.assertNotIn("editor", self.client.get(f"/api/projects/{r.json()['id']}", headers=self.headers).json())


class AuditFindingsTest(RouteTest):
    """Defects found by the Phase 19 security / correctness audit, each pinned."""

    def test_a_review_decision_never_writes_over_another_scene(self):
        from test_cinematic import CINE
        pid = self.save_lesson()
        data = self.load(pid).json()
        rev = self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]}).json()["revision"]
        moved = copy.deepcopy(data["scenes"])
        moved.append(moved.pop(0))  # an unsaved move: the page's scene 3 is the server's scene 0
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 3, "action": "change", "scene": moved[3],
                                                              "overrides": {"camera": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 409, r.text)
        saved = self.load(pid).json()["scenes"]
        self.assertEqual([s["scene_id"] for s in saved], [s["scene_id"] for s in data["scenes"]], "nothing was overwritten")
        # the same scene at its own position: accepted, and the reply carries the lesson's new revision
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 0, "action": "change", "scene": data["scenes"][0],
                                                              "overrides": {"camera": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["revision"], self.load(pid).json()["revision"])
        self.assertNotEqual(r.json()["revision"], rev)

    def test_visual_reviews_regenerate_buttons_never_write_over_another_scene(self):
        # fact-check finding: the composition and direction "regenerate" routes also write the scene back by its position
        from test_cinematic import CINE
        pid = self.save_lesson()
        data = self.load(pid).json()
        self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]})
        moved = copy.deepcopy(data["scenes"])
        moved.append(moved.pop(0))  # an unsaved move: the page's scene 3 is the server's scene 0
        for route in ("/api/cinematic/regenerate", "/api/cinematic/direction/regenerate"):
            r = self.client.post(route, json={"project_id": pid, "scenes": moved, "scene_index": 3, "settings": CINE}, headers=self.headers)
            self.assertEqual(r.status_code, 409, f"{route}: {r.text}")
            saved = self.load(pid).json()["scenes"]
            self.assertEqual([s["scene_id"] for s in saved], [s["scene_id"] for s in data["scenes"]], f"{route}: nothing was overwritten")
            self.assertEqual(saved[0]["title"], data["scenes"][0]["title"])
        # the lesson as saved: accepted, and the reply carries the lesson's current revision
        for route in ("/api/cinematic/regenerate", "/api/cinematic/direction/regenerate"):
            current = self.load(pid).json()
            r = self.client.post(route, json={"project_id": pid, "scenes": current["scenes"], "scene_index": 1, "settings": CINE}, headers=self.headers)
            self.assertEqual(r.status_code, 200, f"{route}: {r.text}")
            self.assertEqual(r.json()["revision"], self.load(pid).json()["revision"], route)

    def test_numbers_sizes_and_overflow(self):
        pid = self.save_lesson()
        data = self.load(pid).json()
        rev = data["revision"]
        nan = copy.deepcopy(data["scenes"])
        nan[0]["weight"] = float("nan")
        r = self.client.put(f"/api/editor/{pid}", content=json.dumps({"expected_revision": rev, "scenes": nan}).encode(),
                            headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 422, r.text)
        huge = copy.deepcopy(data["scenes"])
        huge[0]["edit"] = {"min_seconds": 10 ** 400}
        self.assertEqual(self.put(pid, {"expected_revision": rev, "scenes": huge}).status_code, 422)
        big = copy.deepcopy(data["scenes"])
        big[0]["html"] = "x" * 300001
        self.assertEqual(self.put(pid, {"expected_revision": rev, "scenes": big}).status_code, 422)
        r = self.client.post("/save-history", content=json.dumps({"subject_name": "N", "scenes": [{"a": float("inf")}]}).encode(),
                             headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 422)

    def test_every_form_of_an_asset_reference_is_checked(self):
        theirs = uuid.uuid4().hex
        with database.SessionLocal() as db:
            db.add(models.Asset(id=theirs, scope_key=f"user:{self.other}", owner_id=self.other, kind="image", source="upload", status="ready",
                                file_name="x.png", mime_type="image/png", storage_volume="assets", storage_key=f"x/{theirs}.png",
                                file_size=10, width=10, height=10))
            db.commit()
        pid = self.save_lesson()
        data = self.load(pid).json()
        for form in (lambda s: s.__setitem__("html", f'<img src="asset:{theirs.upper()}">'),
                     lambda s: s.__setitem__("video_url", f"/api/assets/{theirs}/content?token=x"),
                     lambda s: s.__setitem__("presenter_plan", {"media_asset_id": theirs})):
            changed = copy.deepcopy(data["scenes"])
            form(changed[1])
            with self.subTest(scene=changed[1]):
                self.assertEqual(self.put(pid, {"expected_revision": data["revision"], "scenes": changed}).status_code, 422)

    def test_old_lessons_get_stable_ids_and_invalid_edit_data_is_dropped_on_load(self):
        pid = self.save_lesson()
        first, second = self.load(pid).json(), self.load(pid).json()
        self.assertEqual([s["scene_id"] for s in first["scenes"]], [s["scene_id"] for s in second["scenes"]], "the same ids on every load")
        broken = scenes()
        broken[0]["edit"] = {"min_seconds": -5}
        bad = self.save_lesson(broken)
        loaded = self.load(bad).json()
        self.assertNotIn("edit", loaded["scenes"][0])
        self.assertEqual(self.put(bad, {"expected_revision": loaded["revision"], "scenes": loaded["scenes"]}).status_code, 200)

    def test_an_old_lesson_says_its_ids_are_new_until_they_are_kept(self):
        # fact-check finding: the page keeps an old lesson's ids at once (before any edit), so a property edit made while media
        # is being generated is judged by id, not refused because the first save also carries an edit
        pid = self.save_lesson()
        with database.SessionLocal() as db:
            db.add(models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="image",
                                          status="running", provider="maker", model="m", project_id=pid, scene_index=1, slot="side", request="{}"))
            db.commit()
        data = self.load(pid).json()
        self.assertEqual(data["new_ids"], 4)
        rev = self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"], "editor": data["editor"]})
        self.assertEqual(rev.status_code, 200, rev.text)
        again = self.load(pid).json()
        self.assertEqual(again["new_ids"], 0)
        self.assertEqual([s["scene_id"] for s in again["scenes"]], [s["scene_id"] for s in data["scenes"]])
        props = copy.deepcopy(again["scenes"])
        props[2]["edit"] = {"min_seconds": 7}
        props[2]["title"], props[2]["edit"]["original"] = "Scene two, renamed", {"title": props[2]["title"]}
        self.assertEqual(self.put(pid, {"expected_revision": again["revision"], "scenes": props}).status_code, 200, "property edits are allowed")
        moved = copy.deepcopy(props)
        moved.reverse()
        self.assertEqual(self.put(pid, {"expected_revision": self.load(pid).json()["revision"], "scenes": moved}).status_code, 409)

    def test_the_first_save_exemption_only_adds_ids(self):
        pid = self.save_lesson()
        with database.SessionLocal() as db:
            db.add(models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type="image",
                                          status="running", provider="maker", model="m", project_id=pid, scene_index=0, slot="side", request="{}"))
            db.commit()
        data = self.load(pid).json()
        swapped = copy.deepcopy(data["scenes"])
        swapped[0], swapped[1] = swapped[1], swapped[0]
        swapped[0]["scene_id"], swapped[1]["scene_id"] = swapped[1]["scene_id"], swapped[0]["scene_id"]  # content moved under the same ids
        self.assertEqual(self.put(pid, {"expected_revision": data["revision"], "scenes": swapped}).status_code, 409)
        self.assertEqual(self.put(pid, {"expected_revision": data["revision"], "scenes": data["scenes"]}).status_code, 200)
        status = self.client.get(f"/api/editor/{pid}/status", headers=self.headers).json()
        self.assertEqual(status["generating"], 1)
        self.assertEqual(status["revision"], self.load(pid).json()["revision"])


if __name__ == "__main__":
    unittest.main()
