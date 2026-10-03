"""Phase 20 — lesson writes that never lose a user's edit: every in-place writer of a saved lesson (a finished background
result, Visual Review decisions, the lesson style, the direction) writes through editor_api.update_lesson (the editor's own
compare-and-set; the change is applied again to a lesson saved meanwhile, never written over it), and a background result
finds its scene by id when its request carries one (visuals.scene_for_run).

A "concurrent save" here is another save committed after a writer read the lesson and before it wrote: an editor PUT, a
review or a style choice made through the routes, injected at that exact moment.

Run: python -m unittest discover -s tests -p "test_lesson_writes.py"
"""
import copy
import datetime
import json
import os
import unittest
import uuid
from contextlib import contextmanager
from unittest import mock

from backend_env import assert_isolated, auth, ensure_user  # noqa: F401  first: throwaway database

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import cinematic as C  # noqa: E402
import database  # noqa: E402
import editor_api as E  # noqa: E402
import models  # noqa: E402
import presenters as P  # noqa: E402
import server  # noqa: E402
import visuals as V  # noqa: E402
from test_cinematic import CINE  # noqa: E402

USER = "wren"


def words():
    return " ".join(f"w{uuid.uuid4().hex[:7]}" for _ in range(5))  # vocabulary no other test uses


def chart(n):
    return {"type": "chart", "data": {"labels": ["a", "b"], "datasets": [{"label": f"d{n}", "data": [1, n]}]}}


def scenes():
    return [{"type": "content", "title": f"Scene {n}", "html": f"<p>Point {n}</p>", "narration": f"Narration {n}.", "side_panel": chart(n)}
            for n in range(4)]


@contextmanager
def racing(concurrent, times=1):
    """Inside the block, `concurrent()` (another save) commits right after a writer read the lesson and before its own
    write, `times` times: the writer's first write(s) lose the compare-and-set and it must apply its change again."""
    real = E.update_lesson
    calls = {"mutate": 0, "concurrent": 0}

    def update(db, project, mutate, attempts=4):
        def raced(payload):
            calls["mutate"] += 1
            if calls["concurrent"] < times:
                calls["concurrent"] += 1
                concurrent()
            return mutate(payload)
        return real(db, project, raced, attempts)

    with mock.patch.object(E, "update_lesson", update):
        yield calls


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user(USER)
        cls.client = TestClient(server.app)
        cls.headers = auth(USER)

    # ---- helpers --------------------------------------------------------------------------------------------------

    def old_lesson(self, items=None):
        """A saved lesson as /save-history keeps it (scenes without stored ids: never saved by the editor)."""
        r = self.client.post("/save-history", json={"subject_name": "Writes", "scenes": items if items is not None else scenes()},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def lesson(self, items=None):
        """A saved lesson whose scenes have stored scene ids (as after the editor's first save)."""
        pid = self.old_lesson(items)
        data = self.editor(pid)
        self.assertEqual(self.client.put(f"/api/editor/{pid}", json={"expected_revision": data["revision"], "scenes": data["scenes"]},
                                         headers=self.headers).status_code, 200)
        return pid

    def editor(self, pid):
        r = self.client.get(f"/api/editor/{pid}", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def editor_save(self, pid, change):
        """An editor save (PUT /api/editor/{id}) of the lesson as loaded now, with `change(scenes)` applied."""
        data = self.editor(pid)
        edited = copy.deepcopy(data["scenes"])
        change(edited)
        r = self.client.put(f"/api/editor/{pid}", json={"expected_revision": data["revision"], "scenes": edited, "editor": data["editor"]},
                            headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["revision"]

    def payload(self, pid):
        with database.SessionLocal() as db:
            return json.loads(db.get(models.Project, pid).json_data)

    def revision(self, pid):
        return self.editor(pid)["revision"]

    def asset(self, kind="video", source="upload", details=None):
        """A ready library asset of this user with a (tiny) file in storage."""
        asset_id = uuid.uuid4().hex
        with database.SessionLocal() as db:
            asset = models.Asset(id=asset_id, scope_key=f"user:{self.uid}", owner_id=self.uid, kind=kind, source=source, status="ready",
                                 file_name=f"clip-{asset_id[:6]}.{'mp4' if kind == 'video' else 'png'}",
                                 mime_type="video/mp4" if kind == "video" else "image/png", storage_volume="assets",
                                 storage_key=f"writes/{asset_id}.{'mp4' if kind == 'video' else 'png'}", file_size=4, width=160, height=90,
                                 duration_seconds=1.0 if kind == "video" else None, details=json.dumps(details or {}))
            db.add(asset)
            db.commit()
            path = server.asset_library.file_path(asset)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"test")
        return asset_id

    def store(self, pid, scene_index, **extra):
        """A finished background result for a scene attached as server.py does (visuals.store_scene_plans)."""
        with database.SessionLocal() as db:
            user = db.get(models.User, self.uid)
            project = db.get(models.Project, pid)
            plans = V.store_scene_plans(db, user, server.asset_library, project, scene_index, server._asset_link(user), **extra)
            return None if plans is None else [p.to_dict() for p in plans]

    def make_run(self, pid, scene_index, slot, request=None, **fields):
        """A background run of this user for a scene (not stored: what the attach and the checks read)."""
        return models.AIGenerationRun(id=uuid.uuid4().hex, user_id=self.uid, project_id=pid, scene_index=scene_index, slot=slot,
                                      forced=False, request=json.dumps(request or {}), **fields)


class UpdateLessonTest(Base):
    def test_a_save_committed_between_load_and_write_is_kept(self):
        pid = self.lesson()
        calls, revisions = [], []

        def mutate(payload):
            calls.append(1)
            if len(calls) == 1:  # the editor saves after this writer read the lesson
                revisions.append(self.editor_save(pid, lambda s: s[0].update(edit={"min_seconds": 9})))
            payload["scenes"][1]["title"] = "From the writer"
            return True

        with database.SessionLocal() as db:
            revision = E.update_lesson(db, db.get(models.Project, pid), mutate)
        self.assertEqual(len(calls), 2, "the change was applied again to the newer lesson")
        saved = self.payload(pid)
        self.assertEqual(saved["scenes"][0]["edit"], {"min_seconds": 9.0}, "the editor's save is kept")
        self.assertEqual(saved["scenes"][1]["title"], "From the writer")
        self.assertEqual(revision, self.revision(pid))
        self.assertNotEqual(revision, revisions[0])

    def test_nothing_is_written_when_nothing_changed(self):
        pid = self.lesson()
        before = self.revision(pid)
        with database.SessionLocal() as db:
            self.assertIsNone(E.update_lesson(db, db.get(models.Project, pid), lambda payload: False))
        self.assertEqual(self.revision(pid), before)

    def test_it_gives_up_with_a_409_rather_than_overwrite(self):
        pid = self.lesson()
        calls = []

        def always_raced(payload):
            calls.append(1)
            self.editor_save(pid, lambda s: s[2].update(title=f"Editor {len(calls)}"))
            payload["scenes"][2]["title"] = "Never written"
            return True

        with database.SessionLocal() as db, self.assertRaises(E.LessonChanged) as caught:
            E.update_lesson(db, db.get(models.Project, pid), always_raced, attempts=3)
        self.assertEqual(len(calls), 3)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["revision"], self.revision(pid))
        self.assertIn("nothing was overwritten", caught.exception.detail["message"])
        self.assertEqual(self.payload(pid)["scenes"][2]["title"], "Editor 3")

    def test_a_refusal_inside_mutate_writes_nothing(self):
        pid = self.lesson()
        before = self.revision(pid)

        def refuse(payload):
            payload["scenes"][0]["title"] = "half done"
            raise HTTPException(status_code=409, detail="no")

        with database.SessionLocal() as db, self.assertRaises(HTTPException):
            E.update_lesson(db, db.get(models.Project, pid), refuse)
        self.assertEqual((self.revision(pid), self.payload(pid)["scenes"][0]["title"]), (before, "Scene 0"))

    def test_revisions_always_move_forward_even_within_one_clock_tick(self):
        later = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        self.assertEqual(E.next_stamp(later), later + datetime.timedelta(microseconds=1))
        self.assertIsInstance(E.next_stamp(None), datetime.datetime)
        pid = self.lesson()
        for writer in ("update_lesson", "editor"):
            with self.subTest(writer=writer):
                with database.SessionLocal() as db:  # the stored stamp is "now or later" (as two writes in one tick)
                    db.query(models.Project).filter(models.Project.id == pid).update({models.Project.updated_at: later}, synchronize_session=False)
                    db.commit()
                stale = self.revision(pid)
                if writer == "update_lesson":
                    with database.SessionLocal() as db:
                        revision = E.update_lesson(db, db.get(models.Project, pid), lambda p: p.update(touched=writer) or True)
                else:
                    revision = self.editor_save(pid, lambda s: s[3].update(title="Edited"))
                self.assertNotEqual(revision, stale)
                self.assertGreater(datetime.datetime.fromisoformat(revision), later)
                later = datetime.datetime.fromisoformat(revision)


class SceneForRunTest(Base):
    def test_by_id_else_by_position(self):
        ids = [{"scene_id": "s-aaaaaaaaaaaa"}, {"scene_id": "s-bbbbbbbbbbbb"}, {"scene_id": "s-cccccccccccc"}]
        self.assertEqual(V.scene_for_run(ids, {"scene_id": "s-cccccccccccc", "scene_index": 0}), 2)
        self.assertIsNone(V.scene_for_run(ids, {"scene_id": "s-dddddddddddd", "scene_index": 0}), "a removed scene gets nothing")
        self.assertEqual(V.scene_for_run(ids, {"scene_index": 1}), 1, "a run made before scene ids: its position")
        no_ids = [{"title": "a"}, {"title": "b"}]
        self.assertEqual(V.scene_for_run(no_ids, {"scene_id": "s-aaaaaaaaaaaa", "scene_index": 1}), 1, "a lesson whose ids are not stored yet")
        for bad in ({"scene_index": 5}, {"scene_index": -1}, {"scene_index": True}, {"scene_index": "1"}, {}, None):
            self.assertIsNone(V.scene_for_run(ids, bad), bad)
        self.assertIsNone(V.scene_for_run([None, "x"], {"scene_index": 0}))

    def test_the_run_request_carries_the_scene(self):
        run = models.AIGenerationRun(scene_index=3, request=json.dumps({"prompt": "p", "scene_id": "s-aaaaaaaaaaaa"}))
        self.assertEqual(V.run_request(run), {"prompt": "p", "scene_id": "s-aaaaaaaaaaaa", "scene_index": 3})
        run = models.AIGenerationRun(scene_index=1, request=json.dumps({"prompt": "p"}), detail=json.dumps({"scene_id": "s-bbbbbbbbbbbb"}))
        self.assertEqual(V.run_request(run)["scene_id"], "s-bbbbbbbbbbbb", "or in the run's detail")
        self.assertEqual(V.run_request(models.AIGenerationRun(scene_index=2)), {"scene_index": 2})
        self.assertEqual(V.run_request(models.AIGenerationRun(request="not json")), {})


class BackgroundResultTest(Base):
    def test_a_finished_result_keeps_an_editor_edit_committed_just_before(self):
        pid = self.lesson()

        def edit():
            self.editor_save(pid, lambda s: (s[1].update(edit={"min_seconds": 7, "hidden": True}), s[2].update(title="Renamed")))

        with racing(edit) as calls:
            plans = self.store(pid, 1)
        self.assertEqual(calls["concurrent"], 1)
        self.assertGreaterEqual(calls["mutate"], 2)
        self.assertEqual({p["slot"] for p in plans}, {"side"})
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[1]["edit"], {"min_seconds": 7.0, "hidden": True}, "the editor's property edit is kept")
        self.assertEqual(saved[2]["title"], "Renamed")
        self.assertEqual(saved[1]["visual_plan"]["side"]["source"], "PROCEDURAL", "and the result is attached")

    def test_a_finished_result_never_overwrites_a_visual_chosen_in_visual_review(self):
        topic = words()
        chosen = self.asset(details={"description": "a hand-picked clip"})
        items = scenes()
        items[0] = {"type": "ai_video", "title": topic, "prompt": topic, "narration": "Watch this."}
        pid = self.lesson(items)

        def choose():
            r = self.client.post("/api/visuals/review", json={"project_id": pid, "scene_index": 0, "slot": "main", "action": "choose",
                                                               "asset_id": chosen}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)

        with racing(choose):
            self.store(pid, 0, fill_slot="main")
        scene = self.payload(pid)["scenes"][0]
        self.assertEqual(scene["visual_review"]["main"]["status"], "changed", "the decision is kept")
        self.assertEqual(scene["visual_review"]["main"]["asset_id"], chosen)
        self.assertEqual(scene["visual_plan"]["main"]["asset_id"], chosen, "the visual the user chose is what the lesson shows")
        with database.SessionLocal() as db:  # and a run for it continued later is not wanted any more
            self.assertEqual(V.still_wanted(db, self.make_run(pid, 0, "main", {"prompt": topic}))[0], False)

    def test_a_shown_visual_is_not_planned_over(self):
        topic = words()
        shown = self.asset(details={"description": "already shown"})
        items = scenes()
        items[0] = {"type": "ai_video", "title": topic, "prompt": topic,
                    "visual_plan": {"main": {"source": "UPLOADED_ASSET", "asset_id": shown, "selection": "reviewed"}}}
        pid = self.lesson(items)
        before = self.revision(pid)
        self.assertIsNone(self.store(pid, 0, fill_slot="main"))
        self.assertEqual(self.payload(pid)["scenes"][0]["visual_plan"]["main"]["asset_id"], shown)
        self.assertEqual(self.revision(pid), before, "nothing written")

    def test_a_presenter_clip_keeps_an_editor_edit_and_a_removal(self):
        pid = self.lesson()
        clip = self.asset(source="ai-presenter", details={"presenter": {"presenter_id": "ai-teacher", "lip_sync": True}})
        sid = self.editor(pid)["scenes"][2]["scene_id"]
        link = lambda asset: f"/api/assets/{asset.id}/content"  # noqa: E731

        with racing(lambda: self.editor_save(pid, lambda s: s[2].update(edit={"min_seconds": 5}))):
            with database.SessionLocal() as db:
                P.store_generated_clip(db, self.make_run(pid, 2, "presenter", {"scene_id": sid}, asset_id=clip), server.asset_library, link)
        scene = self.payload(pid)["scenes"][2]
        self.assertEqual(scene["edit"], {"min_seconds": 5.0})
        self.assertEqual(scene["presenter_plan"]["media"]["asset_id"], clip)
        # a removal in Visual Review committed while another clip finishes: the removal wins
        other = self.asset(source="ai-presenter")

        def remove():
            r = self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 3, "action": "remove",
                                                                  "settings": {"presenter_id": "aadhi-teacher"}}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)

        with racing(remove):
            with database.SessionLocal() as db:
                P.store_generated_clip(db, self.make_run(pid, 3, "presenter", asset_id=other), server.asset_library, link)
        scene = self.payload(pid)["scenes"][3]
        self.assertEqual(scene["visual_review"]["presenter"]["status"], "removed")
        self.assertNotEqual(((scene.get("presenter_plan") or {}).get("media") or {}).get("asset_id"), other)

    def test_a_style_chosen_while_another_scenes_result_attaches_stays(self):
        pid = self.lesson()
        choice = {"style": "academic", "style_version": 1, "style_overrides": {"accent": "teal"}}

        def style():
            r = self.client.post("/api/cinematic/style", json={"project_id": pid, **choice}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)

        with racing(style):
            self.store(pid, 3)
        saved = self.payload(pid)
        self.assertEqual(saved["cinematic_style"], choice)
        self.assertEqual(saved["scenes"][3]["visual_plan"]["side"]["source"], "PROCEDURAL")
        # the other way round: a background picture attached while the style is being written
        with database.SessionLocal() as db:
            project = db.get(models.Project, pid)
            payload = json.loads(project.json_data)
            payload["scenes"][0]["cinematic_plan"] = {"background": {"fallback": True}, "style": {"background": "ai"}}
            project.json_data = json.dumps(payload)
            db.commit()
        picture = self.asset(kind="image", source="ai-image")

        def attach():
            with database.SessionLocal() as db:
                self.assertTrue(C.store_generated_background(db, self.make_run(pid, None, "background", asset_id=picture), None))

        with racing(attach):
            r = self.client.post("/api/cinematic/style", json={"project_id": pid, "style": None}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        saved = self.payload(pid)
        self.assertNotIn("cinematic_style", saved)
        self.assertEqual(saved["scenes"][0]["cinematic_plan"]["background"]["asset_id"], picture, "the background is kept")
        self.assertEqual(saved["cinematic"]["background_asset_id"], picture)
        self.assertEqual(r.json(), {"cinematic_style": None, "revision": self.revision(pid)})

    def test_a_moved_scenes_result_lands_on_that_scene_by_id(self):
        topic = words()
        items = scenes()
        items[2] = {"type": "ai_video", "title": topic, "prompt": topic}
        pid = self.lesson(items)
        sid = self.editor(pid)["scenes"][2]["scene_id"]
        self.editor_save(pid, lambda s: s.insert(0, s.pop(2)))  # the editor moves it first
        with database.SessionLocal() as db:
            self.assertEqual(V.still_wanted(db, self.make_run(pid, 2, "main", {"prompt": topic, "scene_id": sid})), (True, ""))
            self.assertEqual(V.still_wanted(db, self.make_run(pid, 2, "main", {"prompt": topic}))[0], False, "by position: another scene")
        plans = self.store(pid, 2, scene_id=sid)
        self.assertEqual({p["slot"] for p in plans}, {"main"})
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[0]["scene_id"], sid)
        self.assertIn("main", saved[0]["visual_plan"])
        self.assertNotIn("visual_plan", saved[2], "the scene now at the old position is untouched")
        # moved while the result attaches (asked by position): the retry finds the same scene by the id it read first
        sid1 = saved[1]["scene_id"]
        with racing(lambda: self.editor_save(pid, lambda s: s.append(s.pop(1)))):
            self.store(pid, 1)
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[3]["scene_id"], sid1)
        self.assertIn("side", saved[3]["visual_plan"])
        self.assertNotIn("visual_plan", saved[1])
        # removed: nothing is attached anywhere, and it is not generated
        self.editor_save(pid, lambda s: s.pop(0))
        before = self.payload(pid)
        self.assertIsNone(self.store(pid, 0, scene_id=sid))
        self.assertEqual(self.payload(pid), before)
        with database.SessionLocal() as db:
            self.assertEqual(V.still_wanted(db, self.make_run(pid, 0, "main", {"prompt": topic, "scene_id": sid}))[0], False)

    def test_a_moved_scenes_presenter_clip_lands_by_id(self):
        pid = self.lesson()
        sid = self.editor(pid)["scenes"][1]["scene_id"]
        self.editor_save(pid, lambda s: s.append(s.pop(1)))
        clip = self.asset(source="ai-presenter")
        with database.SessionLocal() as db:
            self.assertEqual(P.presenter_still_wanted(db, self.make_run(pid, 1, "presenter", {"scene_id": sid})), (True, ""))
            P.store_generated_clip(db, self.make_run(pid, 1, "presenter", {"scene_id": sid}, asset_id=clip), server.asset_library, None)
        saved = self.payload(pid)["scenes"]
        self.assertEqual((saved[3]["scene_id"], saved[3]["presenter_plan"]["media"]["asset_id"]), (sid, clip))
        self.assertNotIn("presenter_plan", saved[1])
        # moved while the clip attaches (asked by position): the retry finds the same scene by the id it read first
        sid0, other = saved[0]["scene_id"], self.asset(source="ai-presenter")
        with racing(lambda: self.editor_save(pid, lambda s: s.append(s.pop(0)))):
            with database.SessionLocal() as db:
                P.store_generated_clip(db, self.make_run(pid, 0, "presenter", asset_id=other), server.asset_library, None)
        saved = self.payload(pid)["scenes"]
        self.assertEqual((saved[3]["scene_id"], saved[3]["presenter_plan"]["media"]["asset_id"]), (sid0, other))
        self.assertNotIn("presenter_plan", saved[0])
        self.editor_save(pid, lambda s: s.pop(2))
        with database.SessionLocal() as db:
            self.assertEqual(P.presenter_still_wanted(db, self.make_run(pid, 1, "presenter", {"scene_id": sid}))[0], False)


class RouteRepliesTest(Base):
    def test_review_and_style_routes_reply_with_the_new_revision(self):
        pid = self.lesson()
        r = self.client.post("/api/visuals/review", json={"project_id": pid, "scene_index": 1, "slot": "side", "action": "remove"},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["status"], r.json()["plans"]["side"]["selection"]), ("removed", "removed"))
        self.assertEqual(r.json()["revision"], self.revision(pid))
        r = self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "move", "position": "left",
                                                              "settings": {"presenter_id": "aadhi-teacher", "mode": "auto"}}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["status"], r.json()["plan"]["position"], r.json()["revision"]), ("changed", "left", self.revision(pid)))
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 0, "action": "change",
                                                             "overrides": {"camera": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["status"], r.json()["revision"]), ("changed", self.revision(pid)))
        self.assertIn("plan", r.json())
        r = self.client.post("/api/cinematic/direction/review", json={"project_id": pid, "scene_index": 3, "action": "change",
                                                                       "overrides": {"camera_intent": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["overrides"], r.json()["revision"]), ({"camera_intent": "static"}, self.revision(pid)))
        self.assertIn("direction", r.json())
        choice = {"style": "academic", "style_version": 1, "style_overrides": {}}
        r = self.client.post("/api/cinematic/style", json={"project_id": pid, **choice}, headers=self.headers)
        self.assertEqual(r.json(), {"cinematic_style": choice, "revision": self.revision(pid)})
        current = self.editor(pid)
        r = self.client.post("/api/cinematic/regenerate", json={"project_id": pid, "scenes": current["scenes"], "scene_index": 1, "settings": CINE},
                             headers=self.headers)
        self.assertEqual((r.status_code, r.json()["revision"]), (200, self.revision(pid)))
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[1]["visual_review"]["side"]["status"], "removed", "every decision is in the lesson")
        self.assertEqual(saved[2]["visual_review"]["presenter"]["status"], "changed")
        self.assertEqual(saved[0]["visual_review"]["composition"]["overrides"], {"camera": "static"})
        self.assertEqual(saved[3]["visual_review"]["direction"]["overrides"], {"camera_intent": "static"})

    def test_a_review_decision_keeps_an_editor_save_committed_meanwhile(self):
        pid = self.lesson()
        for route, body in (("/api/visuals/review", {"scene_index": 1, "slot": "side", "action": "remove"}),
                            ("/api/presenters/review", {"scene_index": 1, "action": "remove", "settings": {"presenter_id": "aadhi-teacher"}}),
                            ("/api/cinematic/review", {"scene_index": 1, "action": "change", "overrides": {"camera": "static"}, "settings": CINE}),
                            ("/api/cinematic/direction/review", {"scene_index": 1, "action": "change", "overrides": {"camera_intent": "static"},
                                                                 "settings": CINE})):
            with self.subTest(route=route):
                title = f"Edited during {route}"
                with racing(lambda: self.editor_save(pid, lambda s: s[2].update(title=title))):
                    r = self.client.post(route, json={"project_id": pid, **body}, headers=self.headers)
                self.assertEqual(r.status_code, 200, r.text)
                saved = self.payload(pid)["scenes"]
                self.assertEqual(saved[2]["title"], title, "the editor's save is kept")
                self.assertIn("visual_review", saved[1])
                self.assertEqual(r.json()["revision"], self.revision(pid))

    def test_a_scene_moved_meanwhile_is_never_written_over(self):
        # the Phase 19 identity check also holds when the editor moves the scene between the review's read and its write
        pid = self.lesson()
        page = self.editor(pid)["scenes"]
        with racing(lambda: self.editor_save(pid, lambda s: s.append(s.pop(0)))):
            r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 0, "action": "change", "scene": page[0],
                                                                 "overrides": {"camera": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 409, r.text)
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[3]["scene_id"], page[0]["scene_id"])
        self.assertTrue(all("visual_review" not in s for s in saved), "nothing was written")

    def test_directions_keep_an_editor_save_and_skip_a_moved_scene(self):
        pid = self.lesson()
        page = self.editor(pid)["scenes"]
        with racing(lambda: self.editor_save(pid, lambda s: s[0].update(edit={"min_seconds": 4}))):
            r = self.client.post("/api/cinematic/direction", json={"project_id": pid, "scenes": page, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        saved = self.payload(pid)["scenes"]
        self.assertEqual(saved[0]["edit"], {"min_seconds": 4.0})
        self.assertEqual([s["visual_direction"] for s in saved], r.json()["directions"])
        # the page's copy is out of date (two scenes swapped in the editor): those two are left alone, the rest is kept
        swapped = self.editor_save(pid, lambda s: s.__setitem__(slice(1, 3), [s[2], s[1]]))
        self.assertTrue(swapped)
        before = self.payload(pid)["scenes"]
        stale = [{**s, "title": s["title"] + " (new)"} for s in page]
        r = self.client.post("/api/cinematic/direction", json={"project_id": pid, "scenes": stale, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        after = self.payload(pid)["scenes"]
        self.assertNotEqual(r.json()["directions"][1], before[1]["visual_direction"], "the stale copy's scene 1 is another scene")
        self.assertEqual([after[1]["visual_direction"], after[2]["visual_direction"]],
                         [before[1]["visual_direction"], before[2]["visual_direction"]])
        self.assertEqual(after[0]["visual_direction"], r.json()["directions"][0])


class PageCopyTest(Base):
    """A review sent with the page's (older) copy of the scene never reverts what the saved scene has (audit M1)."""

    FIX = "Cells are the smallest units of life (the teacher's fix)."

    def fix_in_the_editor(self, pid, index=0):
        """Tab B: the editor saves a narration fix, a new title and editor data on one scene."""
        self.editor_save(pid, lambda s: s[index].update(narration=self.FIX, title="Cells, fixed", edit={"min_seconds": 6}))

    def assert_fix_kept(self, pid, index=0):
        scene = self.payload(pid)["scenes"][index]
        self.assertEqual((scene["narration"], scene["title"], scene.get("edit")), (self.FIX, "Cells, fixed", {"min_seconds": 6.0}))
        return scene

    def test_no_review_route_reverts_an_editor_save_made_since_the_page_loaded(self):
        for route, body, recorded in (
                ("/api/visuals/review", {"slot": "side", "action": "remove"}, "side"),
                ("/api/presenters/review", {"action": "remove", "settings": {"presenter_id": "aadhi-teacher"}}, "presenter"),
                ("/api/cinematic/review", {"action": "change", "overrides": {"camera": "static"}, "settings": CINE}, "composition"),
                ("/api/cinematic/direction/review", {"action": "change", "overrides": {"camera_intent": "static"}, "settings": CINE}, "direction")):
            with self.subTest(route=route):
                pid = self.lesson()
                page = self.editor(pid)["scenes"]  # tab A (Visual Review) holds this copy of the lesson
                self.fix_in_the_editor(pid)
                r = self.client.post(route, json={"project_id": pid, "scene_index": 0, "scene": page[0], "scenes": page, **body},
                                     headers=self.headers)
                self.assertEqual(r.status_code, 200, r.text)
                scene = self.assert_fix_kept(pid)
                self.assertIn(recorded, scene["visual_review"], "the decision is recorded")
                self.assertEqual(r.json()["revision"], self.revision(pid))
        # the composition's "decide again" button writes the scene back too
        pid = self.lesson()
        page = self.editor(pid)["scenes"]
        self.fix_in_the_editor(pid)
        r = self.client.post("/api/cinematic/regenerate", json={"project_id": pid, "scenes": page, "scene_index": 0, "settings": CINE},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("cinematic_plan", self.assert_fix_kept(pid))

    def test_the_media_the_page_made_still_persists(self):
        topic = words()
        clip = self.asset(details={"description": "made in the preview"})
        items = scenes()
        items[0] = {"type": "ai_video", "title": "Cells", "prompt": topic, "narration": "Cells."}
        items[1]["side_panel"] = {"type": "gif", "query": "mitosis"}
        pid = self.lesson(items)
        page = self.editor(pid)["scenes"]
        page[0].update(video_url=f"/api/assets/{clip}/content", video_asset_id=clip)  # generated in the preview
        page[1]["side_panel"]["gif_url"] = "https://media.example/mitosis.gif"     # found by the page's GIF search
        self.fix_in_the_editor(pid)
        r = self.client.post("/api/visuals/review", json={"project_id": pid, "scene_index": 0, "slot": "main", "action": "keep",
                                                           "scene": page[0]}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["review"]["asset_id"], clip, "the visual the page shows is what was approved")
        scene = self.assert_fix_kept(pid)
        self.assertEqual((scene["video_asset_id"], scene["visual_plan"]["main"]["asset_id"]), (clip, clip))
        r = self.client.post("/api/visuals/review", json={"project_id": pid, "scene_index": 1, "slot": "side", "action": "keep",
                                                           "scene": page[1]}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        side = self.payload(pid)["scenes"][1]["side_panel"]
        self.assertEqual((side["gif_url"], side["query"]), ("https://media.example/mitosis.gif", "mitosis"))

    def test_an_older_copy_never_drops_a_result_attached_since(self):
        pid = self.lesson()
        page = self.editor(pid)["scenes"]
        shown, clip = self.asset(), self.asset(source="ai-presenter")
        with database.SessionLocal() as db:  # attached in the background after the page loaded the lesson
            P.store_generated_clip(db, self.make_run(pid, 2, "presenter", asset_id=clip), server.asset_library, None)
            project = db.get(models.Project, pid)
            payload = json.loads(project.json_data)
            payload["scenes"][2]["visual_plan"] = {"side": {"source": "UPLOADED_ASSET", "asset_id": shown, "selection": "matched"}}
            project.json_data = json.dumps(payload)
            db.commit()
        page[2]["presenter_plan"] = {"enabled": True, "type": "ai_avatar", "presenter_id": "ai-teacher"}  # the page's plan: no clip yet
        page[2]["visual_plan"] = {"side": {"source": "NONE", "selection": "none"}}
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 2, "action": "change", "scene": page[2],
                                                             "overrides": {"camera": "static"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        scene = self.payload(pid)["scenes"][2]
        self.assertEqual(scene["presenter_plan"]["media"]["asset_id"], clip)
        self.assertEqual(scene["visual_plan"]["side"]["asset_id"], shown)
        self.assertEqual(scene["visual_review"]["composition"]["overrides"], {"camera": "static"})

    def test_the_merge_rules(self):
        saved = {"scene_id": "s-aaaaaaaaaaaa", "type": "content", "title": "New", "narration": "Fixed.", "edit": {"min_seconds": 5},
                 "side_panel": {"type": "gif", "query": "new query"}, "visual_review": {"side": {"status": "approved"}},
                 "visual_plan": {"main": {"asset_id": "a" * 32}}, "presenter_plan": {"media": {"asset_id": "b" * 32}},
                 "cinematic_plan": {"background": {"type": "image", "asset_id": "c" * 32}, "template": "old"}}
        sent = {"scene_id": "s-aaaaaaaaaaaa", "type": "content", "title": "Old", "narration": "Typo.", "extra": "page only",
                "side_panel": {"type": "gif", "query": "old query", "gif_url": "g.gif"}, "video_url": "/v.mp4", "video_asset_id": "d" * 32,
                "visual_review": {"main": {"status": "removed"}}, "visual_plan": {"main": {"selection": "none"}, "side": {"selection": "x"}},
                "presenter_plan": {"enabled": False}, "cinematic_plan": {"background": {"fallback": True}, "template": "new"},
                "visual_direction": {"strategy": "s"}}
        before = (copy.deepcopy(saved), copy.deepcopy(sent))
        scene = E.reviewed_scene(saved, sent)
        self.assertEqual((saved, sent), before, "neither argument is changed")
        self.assertEqual((scene["title"], scene["narration"], scene["edit"], scene["side_panel"]["query"]),
                         ("New", "Fixed.", {"min_seconds": 5}, "new query"), "content and editor data: the saved scene's")
        self.assertNotIn("extra", scene)
        self.assertEqual(scene["visual_review"], {"side": {"status": "approved"}, "main": {"status": "removed"}})
        self.assertEqual(scene["visual_plan"], {"main": {"asset_id": "a" * 32}, "side": {"selection": "x"}})
        self.assertEqual(scene["presenter_plan"], {"enabled": False, "media": {"asset_id": "b" * 32}})
        self.assertEqual(scene["cinematic_plan"], {"background": {"type": "image", "asset_id": "c" * 32}, "template": "new"})
        self.assertEqual(scene["visual_direction"], {"strategy": "s"})
        self.assertEqual((scene["video_url"], scene["video_asset_id"], scene["side_panel"]["gif_url"]), ("/v.mp4", "d" * 32, "g.gif"))
        old = {k: v for k, v in saved.items() if k != "scene_id"}
        self.assertEqual(E.reviewed_scene(old, sent), sent, "a lesson never saved by the editor: the page's copy, as before")
        self.assertEqual(E.reviewed_scene(None, sent), sent)


class NumbersTest(Base):
    """A lesson never stores NaN / Infinity through an in-place write (audit L1): it would no longer load."""

    def raw(self, body, placeholder="1234.5678"):
        return json.dumps(body).replace(placeholder, "NaN")

    def test_update_lesson_refuses_nan_and_writes_nothing(self):
        pid = self.lesson()
        before = self.revision(pid)
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=bad), database.SessionLocal() as db, self.assertRaises(HTTPException) as caught:
                E.update_lesson(db, db.get(models.Project, pid), lambda p: p["scenes"][0].update(weight=bad) or True)
            self.assertEqual((caught.exception.status_code, caught.exception.detail), (422, "The lesson contains numbers that cannot be saved."))
        self.assertEqual(self.revision(pid), before)

    def test_a_review_with_nan_in_the_scene_is_refused_and_the_lesson_stays_readable(self):
        old, new = self.old_lesson(), self.lesson()
        page = self.editor(new)["scenes"][0]
        for pid, scene in ((old, {**scenes()[0], "duration": 1234.5678}),  # an old lesson: the page's copy is taken as it is
                           (new, {**page, "visual_review": {"composition": {"weight": 1234.5678}}})):  # a merged record
            with self.subTest(project=pid):
                before = self.revision(pid)
                r = self.client.post("/api/visuals/review", content=self.raw({"project_id": pid, "scene_index": 0, "slot": "side",
                                                                              "action": "remove", "scene": scene}),
                                     headers={**self.headers, "Content-Type": "application/json"})
                self.assertEqual((r.status_code, r.json()["detail"]), (422, "The lesson contains numbers that cannot be saved."))
                self.assertEqual(self.revision(pid), before, "nothing written")
                with database.SessionLocal() as db:
                    self.assertNotIn("NaN", db.get(models.Project, pid).json_data)
                self.assertEqual(self.client.get(f"/api/projects/{pid}", headers=self.headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
