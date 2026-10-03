"""Phase 18 — the Quality & Consistency Engine integrated with the rest of the product, end to end through the API and the
real modules (quality.py and its families, cinematic / styles planning, Visual Review, the asset library, AI runs):

  1 style        a freshly planned lesson is clean in every Phase 17 style (and the original look); a style switch shows as a
                 stale look with an automatic re-plan until the lesson is re-planned; a switch never re-opens a composition
                 approval (same input fingerprints) and never generates anything
  2 approvals    the report never writes the saved lesson (json_data and row counts); a scene_style suggestion applied through
                 /api/cinematic/review keeps the scene approved, a composition suggestion makes it the user's change; in every
                 crafted problem lesson an automatic repair is only a re-plan of a derived plan (presentation / timing)
  3 repair loop  the page's automatic repair (re-plan, put the plans back) removes the finding, changes only that scene's
                 fingerprint and no educational text
  4 Visual Review a removed visual stays removed (nothing asks to restore it); another asset on a slot changes the scene's
                 fingerprint and makes a report stale, the slot's review record untouched
  5 media        a deleted visual file blocks the export, the asset row is not marked; a removed slot reports nothing
  6 preview/export  clean when freshly planned; a tampered plan is stale (automatic re-plan); recompose: false says so
  7 recovery     a report never creates / claims / changes AI generation runs and never calls the recovery manager
  8 determinism and performance (30 scenes through the API)
  9 security     report strings are data, evidence is plain JSON, 413 / 422 / 404

Run from the repo root:
  PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_quality_integration.py"

No browser, no sound, no AI provider: generation and every model call are patched to fail the test if reached; the API
runs in-process on the throwaway database of backend_env; the Visual Review and media tests make tiny files with ffmpeg.
"""
import copy
import datetime
import json
import os
import time
import unittest
import uuid
from contextlib import ExitStack, contextmanager
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user, ffmpeg  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_cache  # noqa: E402
import ai_media  # noqa: E402
import ai_providers  # noqa: E402
import ai_recovery  # noqa: E402
import ai_runs  # noqa: E402
import assets  # noqa: E402
import cinematic as C  # noqa: E402
import composer  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import quality as Q  # noqa: E402
import server  # noqa: E402
import source_documents  # noqa: E402
import styles as S  # noqa: E402
import sync_director  # noqa: E402
import visual_director  # noqa: E402
import visuals  # noqa: E402
from test_cinematic import CINE, lesson, teacher  # noqa: E402


def _importable(name):
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001 - a family still being written
        return False


FAMILY = {name: _importable(name) for name in Q.FAMILY_MODULES}
LEGACY = {"mode": "cinematic"}  # Aadhi as the lesson says, the original look
# every Phase 17 style, plus the original look (no style) with and without the legacy presenter
STYLES = [("legacy", LEGACY), ("original look", CINE)] + [(family, {**CINE, "style": family}) for family in S.FAMILIES]
PROBLEMS = ("warning", "error", "blocking")
STALE_LOOK = ("style.stale_look", "style.mixed_styles")
XSS = "<img src=x onerror=alert(1)>"
MEDIA_DIR = os.path.join(TMP, "quality-integration-media")


def setUpModule():
    assert_isolated()


# ---- helpers -------------------------------------------------------------------------------------------------------------

def apply_plans(scenes, data):
    """What the page does with a /api/cinematic/plan answer (index.html planLessonCinematic: AadhiCinematic.applyPlans and
    applyDirections): every plan and every direction put back on its scene."""
    for i, plan in enumerate(data["plans"]):
        if plan:
            scenes[i]["cinematic_plan"] = plan
        else:
            scenes[i].pop("cinematic_plan", None)
    for i, direction in enumerate(data.get("directions") or []):
        if direction:
            scenes[i]["visual_direction"] = direction
        else:
            scenes[i].pop("visual_direction", None)
    return scenes


def other_style(settings):
    return {**settings, "style": "children_education" if settings.get("style") == "academic" else "academic"}


def problems(report):
    return [(f["rule"], f["severity"], f["scene"]) for f in report["issues"] if f["severity"] in PROBLEMS]


def rules(report, *names):
    return [f for f in report["issues"] if f["rule"] in names]


def texts(scenes):
    """The educational content of a lesson (what no repair may change)."""
    return [(s.get("type"), s.get("title"), s.get("html"), s.get("narration"), s.get("composition")) for s in scenes]


def fingerprints(report):
    return [s["fingerprint"] for s in report["scenes"]]


def plain_json(test, value, where):
    """Evidence is data: plain JSON values only (no objects, no NaN)."""
    if isinstance(value, dict):
        for k, v in value.items():
            test.assertIsInstance(k, str, where)
            plain_json(test, v, where)
    elif isinstance(value, list):
        for v in value:
            plain_json(test, v, where)
    else:
        test.assertTrue(value is None or isinstance(value, (str, int, float, bool)), f"{where}: {value!r}")


def assert_contract(test, report, count):
    """Every finding of every report: well formed, and an automatic repair is only an approval-safe re-plan of a derived
    plan (presentation / timing); suggestions carry overrides /api/cinematic/review accepts; content is never automatic."""
    for f in report["issues"]:
        where = f"{f.get('rule')} (scene {f.get('scene')})"
        test.assertRegex(f["rule"], r"^[a-z_]+\.[a-z0-9_]+$", where)
        test.assertIn(f["severity"], Q.SEVERITIES, where)
        test.assertIn(f["dimension"], Q.DIMENSIONS, where)
        test.assertTrue(f["scene"] is None or (isinstance(f["scene"], int) and 0 <= f["scene"] < count), where)
        rep, action = f["repair"], f["repair"]["action"]
        test.assertIn(rep["kind"], Q.REPAIR_KINDS, where)
        test.assertIn(action["type"], Q.ACTION_TYPES, where)
        if rep["kind"] == "auto":
            test.assertIn(rep["class"], ("presentation", "timing"), where)
            test.assertEqual(action["type"], "replan", where)
            test.assertTrue(action["scenes"], where)
            test.assertTrue(all(isinstance(i, int) and 0 <= i < count for i in action["scenes"]), where)
        if action["type"] == "replan":
            test.assertEqual(rep["kind"], "auto", where)
        if action["type"] in ("scene_style", "composition"):
            test.assertEqual(rep["kind"], "suggest", where)
            test.assertTrue(isinstance(action["scene"], int) and 0 <= action["scene"] < count, where)
            test.assertIsInstance(action["overrides"], dict, where)
            test.assertTrue(action["overrides"], where)
            try:
                C.check_overrides(action["overrides"])  # what /api/cinematic/review accepts
            except Exception as e:  # noqa: BLE001
                test.fail(f"{where}: the suggestion's overrides are refused by Visual Review: {e}")
            if action["type"] == "scene_style":
                test.assertLessEqual(set(action["overrides"]), {"style_accent", "style_background"}, where)
            else:
                test.assertEqual(rep["class"], "composition", where)
                test.assertFalse(any(str(k).startswith("style_") for k in action["overrides"]), where)
        if rep["kind"] == "none":
            test.assertEqual((rep["class"], action["type"], f["repair_status"]), (None, "none", "not_repairable"), where)
        else:
            test.assertEqual(f["repair_status"], "available", where)
            test.assertIn(rep["class"], Q.REPAIR_CLASSES, where)
        if rep["class"] in ("content", "visual", "presenter", "composition"):
            test.assertNotEqual(rep["kind"], "auto", where)
        plain_json(test, f["evidence"], where)
        json.dumps(f, allow_nan=False)


@contextmanager
def forbidden(targets):
    """Each (owner, name) recorded and refused if called. The calls are recorded because a refusal can be swallowed by a
    broad handler on its way (the test then fails on the record, never passes by accident)."""
    calls = []

    def refuse(name):
        def call(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")
        return call

    with ExitStack() as stack:
        for owner, name in targets:
            if hasattr(owner, name):
                stack.enter_context(mock.patch.object(owner, name, side_effect=refuse(f"{getattr(owner, '__name__', owner)}.{name}")))
        yield calls


def no_generation():
    """No media generation, no provider, no model, no network."""
    targets = [(ai_media.AIMediaService, n) for n in ("resolve", "start_background", "open_run", "spawn", "start_batch", "call_provider")]
    targets += [(ai_providers.AIProvider, "produce"), (ai_providers.SyncProvider, "generate"), (ai_providers.JobProvider, "submit"),
                (composer, "ask_model"), (visual_director, "ask_model"), (sync_director, "ask_alignment"),
                (source_documents, "call_model")]
    if FAMILY["quality_education"]:
        import quality_education
        targets.append((quality_education, "ask_terms"))
    import urllib.request
    targets.append((urllib.request, "urlopen"))
    return forbidden(targets)


def no_marking():
    """The calls that mutate while they look (quality contract: never mark_missing / usable_asset / AICache._valid)."""
    return forbidden([(assets.AssetLibrary, "mark_missing"), (visuals.RoutingContext, "usable_asset"), (ai_cache.AICache, "_valid")])


def no_recovery():
    """Nothing of the recovery manager and nothing that claims, transitions or leases a run."""
    manager = [(ai_recovery.RecoveryManager, n) for n, v in vars(ai_recovery.RecoveryManager).items()
               if callable(v) and not n.startswith("__")]
    runs = [(ai_runs, n) for n in ("transition", "update_owned", "claim", "renew", "release", "new_attempt", "open_attempt",
                                   "set_attempt", "due_runs")]
    return forbidden(manager + runs)


class Api(unittest.TestCase):
    """The API as the page uses it (cinematic.js CinematicApi, index.html's composition adapter)."""
    user = "qi_ivy"
    other = "qi_jude"

    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user(cls.user)
        cls.other_uid = ensure_user(cls.other)
        cls.client = TestClient(server.app)
        cls.headers = auth(cls.user)

    def post(self, url, body, status=200, headers=None):
        r = self.client.post(url, json=body, headers=self.headers if headers is None else headers)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def plan(self, scenes, settings, pid=None):
        return self.post("/api/cinematic/plan", {"scenes": scenes, "settings": settings, **({"project_id": pid} if pid else {})})

    def fresh(self, settings, scenes=None):
        """A lesson planned the way the page plans it."""
        scenes = lesson() if scenes is None else scenes
        return apply_plans(copy.deepcopy(scenes), self.plan(scenes, settings))

    def replan(self, scenes, settings, pid=None):
        """The page's automatic repair (index.html qualityRepair = planLessonCinematic): the lesson re-planned with the
        same settings, every plan and direction put back."""
        return apply_plans(copy.deepcopy(scenes), self.plan(scenes, settings, pid))

    def quality(self, scenes, settings, pid=None, headers=None, **extra):
        body = {"scenes": scenes, "settings": settings, **({"project_id": pid} if pid else {}), **extra}
        report = self.post("/api/quality/lesson", body, headers=headers)
        assert_contract(self, report, len(scenes))
        return report

    def save(self, scenes, name="Quality integration"):
        return self.post("/save-history", {"subject_name": name, "scenes": scenes})["id"]

    def load(self, pid):
        r = self.client.get(f"/api/projects/{pid}", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["scenes"]

    def review(self, pid, index, settings, action, slides=None, **extra):
        """A composition decision the way the page sends it (the scene and the lesson it is composed in)."""
        body = {"project_id": pid, "scene_index": index, "action": action, "settings": settings, **extra}
        if slides is not None:
            body.update(scene=slides[index], scenes=slides)
        return self.post("/api/cinematic/review", body)

    def visual(self, pid, index, slot, action, **extra):
        return self.post("/api/visuals/review", {"project_id": pid, "scene_index": index, "slot": slot, "action": action, **extra})

    def asset(self, kind="image"):
        """A tiny real file in the user's library (16:9)."""
        os.makedirs(MEDIA_DIR, exist_ok=True)
        path = os.path.join(MEDIA_DIR, f"{uuid.uuid4().hex}.{'png' if kind == 'image' else 'mp4'}")
        colour = uuid.uuid4().hex[:6]
        if kind == "image":
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{colour}:size=160x90", "-frames:v", "1", path)
        else:
            ffmpeg("-f", "lavfi", "-i", f"color=c=0x{colour}:size=160x90:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
        with open(path, "rb") as f:
            r = self.client.post("/api/assets", files={"file": (os.path.basename(path), f.read(), "application/octet-stream")},
                                 headers=self.headers)
        os.remove(path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["asset"]

    @staticmethod
    def delete_file(asset_id):
        with database.SessionLocal() as db:
            os.remove(server.asset_library.file_path(db.get(models.Asset, asset_id)))

    @staticmethod
    def asset_row(asset_id):
        with database.SessionLocal() as db:
            a = db.get(models.Asset, asset_id)
            return (a.status, a.error_message, a.updated_at, a.deleted_at, a.details, a.file_size)

    def saved_state(self, pid):
        """What a report must never change: the saved lesson and every row count around it."""
        with database.SessionLocal() as db:
            project = db.get(models.Project, pid)
            return {"json_data": project.json_data, "updated_at": project.updated_at,
                    "projects": db.query(models.Project).count(), "assets": db.query(models.Asset).count(),
                    "references": db.query(models.AssetReference).count(), "runs": db.query(models.AIGenerationRun).count()}

    @staticmethod
    def settings_dump(settings):
        """The settings exactly as the quality API reads them (CinematicSettings with its defaults)."""
        return C.CinematicSettings(**settings).model_dump()

    def media_of(self, scenes, pid):
        import quality_media
        with database.SessionLocal() as db:
            return quality_media.load_media(db, self.uid, scenes, pid)


# ---- 1 style -------------------------------------------------------------------------------------------------------------

@unittest.skipUnless(FAMILY["quality_style"], "the style family is not available")
class StyleTest(Api):
    def test_a_freshly_planned_lesson_is_clean_in_every_style(self):
        for name, settings in STYLES:
            with self.subTest(style=name):
                scenes = self.fresh(settings)
                pid = self.save(scenes, f"Clean {name}")
                report = self.quality(scenes, settings, pid)
                self.assertEqual(problems(report), [])
                self.assertIn(report["status"], ("good", "review"))
                self.assertEqual(report["summary"]["auto_repairable"], 0)
                self.assertEqual(rules(report, *STALE_LOOK, "core.stale_plan", "core.no_plan"), [])
                self.assertEqual(report["families"], [n for n in Q.FAMILY_MODULES if FAMILY[n]])
                self.assertEqual(report["registry"]["style"]["family"], settings.get("style") or "cinematic_education")

    def test_a_style_switch_is_a_stale_look_until_the_lesson_is_replanned(self):
        for name, settings in STYLES:
            switched = other_style(settings)
            with self.subTest(style=name, to=switched["style"]):
                scenes = self.fresh(settings)
                pid = self.save(scenes, f"Switch {name}")
                planned = [i for i, s in enumerate(scenes) if s.get("cinematic_plan")]
                report = self.quality(scenes, switched, pid)
                stale = rules(report, *STALE_LOOK)
                self.assertTrue(stale, "the style family flags the look the scenes were styled with")
                for f in stale:
                    self.assertEqual((f["dimension"], f["severity"]), ("style", "error"))
                    self.assertEqual((f["repair"]["kind"], f["repair"]["class"], f["repair"]["action"]["type"]),
                                     ("auto", "presentation", "replan"))
                self.assertEqual(sorted({i for f in stale for i in f["repair"]["action"]["scenes"]}), planned)
                self.assertNotEqual(report["status"], "good")
                # the page's repair: the lesson re-planned with the new style, the plans put back
                repaired = self.replan(scenes, switched, pid)
                after = self.quality(repaired, switched, pid)
                self.assertEqual(rules(after, *STALE_LOOK, "core.stale_plan"), [])
                self.assertEqual(problems(after), [])
                self.assertEqual(after["summary"]["auto_repairable"], 0)
                self.assertEqual(texts(repaired), texts(scenes))

    def test_a_style_switch_is_one_problem_reported_once(self):
        """The style family owns a stale look (quality.py _core_checks: 'the style family owns a stale look: it compares it
        with Phase 17 directly'; test_quality: 'the core never reports it twice'). A style-only switch changes no layout
        (test_styles: same template, layers, camera, transition, timeline): the preview / export comparison must not report
        the same switch again, scene by scene, as a stale layout."""
        for name, settings in STYLES:
            switched = other_style(settings)
            with self.subTest(style=name, to=switched["style"]):
                scenes = self.fresh(settings)
                report = self.quality(scenes, switched)
                self.assertTrue(rules(report, *STALE_LOOK))
                self.assertEqual([(f["scene"], f["evidence"].get("found")) for f in rules(report, "core.stale_plan")], [],
                                 "a style-only switch reported again as a stale layout")

    def test_a_style_switch_keeps_composition_approvals_and_generates_nothing(self):
        for name, settings in STYLES:
            switched = other_style(settings)
            with self.subTest(style=name, to=switched["style"]):
                pid = self.save(self.fresh(settings), f"Approved {name}")
                for i in (1, 3):
                    self.assertEqual(self.review(pid, i, settings, "keep")["review"]["status"], "approved")
                slides = self.load(pid)
                approvals = {i: copy.deepcopy(slides[i]["visual_review"]["composition"]) for i in (1, 3)}
                inputs = [(s.get("cinematic_plan") or {}).get("fingerprint") for s in slides]
                with no_generation() as calls:
                    # the lesson's style switched the way the page saves it (the scenes are not touched)
                    r = self.post("/api/cinematic/style", {"project_id": pid, "style": switched["style"]})
                    self.assertEqual(r["cinematic_style"]["style"], switched["style"])
                    self.assertEqual(self.load(pid), slides)
                    report = self.quality(slides, switched, pid)
                    self.assertTrue(rules(report, *STALE_LOOK))
                    data = self.plan(slides, switched, pid)
                    repaired = apply_plans(copy.deepcopy(slides), data)
                    after = self.quality(repaired, switched, pid)
                self.assertEqual(calls, [], "nothing generated, no model asked")
                self.assertEqual([(p or {}).get("fingerprint") for p in data["plans"]], inputs, "the same input fingerprints")
                for i, review in approvals.items():
                    plan = data["plans"][i]
                    self.assertEqual(plan["fingerprint"], review["fingerprint"])
                    self.assertEqual(plan["review_status"], "approved")
                    self.assertNotIn("review_stale", plan)
                    self.assertEqual(repaired[i]["visual_review"]["composition"], review)
                    self.assertEqual(plan["style"]["look"]["family"], switched["style"])
                self.assertEqual(problems(after), [])
                self.assertEqual(self.load(pid), slides, "the saved scenes and their approvals are unchanged")


# ---- 2 approvals ---------------------------------------------------------------------------------------------------------

class ApprovalTest(Api):
    def test_running_the_report_changes_nothing_saved(self):
        pid = self.save(self.fresh(CINE), "Read only")
        self.assertEqual(self.review(pid, 1, CINE, "keep")["review"]["status"], "approved")
        slides = self.load(pid)
        before = self.saved_state(pid)
        sent = copy.deepcopy(slides)
        reports = [self.quality(slides, CINE, pid, recompose=recompose) for recompose in (True, False, True, True)]
        self.assertEqual(self.saved_state(pid), before, "no write, no new history entry, no row anywhere")
        self.assertEqual(slides, sent)
        self.assertEqual(reports[0], reports[2])
        self.assertEqual(self.load(pid)[1]["visual_review"]["composition"]["status"], "approved")
        # quality data kept with a scene (e.g. the last report) is not part of what the composition is made from, nor of what
        # the scene's checks read: the approval holds and the scene's quality fingerprint is the same
        noted = copy.deepcopy(slides)
        noted[1]["quality"] = {"status": reports[0]["status"], "fingerprint": reports[0]["fingerprint"], "issues": reports[0]["issues"][:3]}
        plan = self.plan(noted, CINE, pid)["plans"][1]
        self.assertEqual(plan["fingerprint"], slides[1]["visual_review"]["composition"]["fingerprint"])
        self.assertEqual(plan["review_status"], "approved")
        self.assertNotIn("review_stale", plan)
        self.assertEqual(fingerprints(self.quality(noted, CINE, pid)), fingerprints(reports[0]))
        self.assertEqual(self.saved_state(pid), before)

    @unittest.skipUnless(FAMILY["quality_style"], "the style family is not available")
    def test_a_scene_style_suggestion_through_review_keeps_the_scene_approved(self):
        pid = self.save(self.fresh(CINE), "Accents")
        for i, accent in ((0, "teal"), (1, "gold"), (2, "coral")):
            self.review(pid, i, CINE, "keep")
            r = self.review(pid, i, CINE, "change", overrides={"style_accent": accent})
            self.assertEqual((r["review"]["status"], r["review"]["overrides"]), ("approved", {"style_accent": accent}))
        slides = self.load(pid)
        report = self.quality(slides, CINE, pid)
        suggestion = next(f for f in report["issues"] if f["scene"] == 1 and f["repair"]["action"]["type"] == "scene_style")
        self.assertEqual((suggestion["rule"], suggestion["repair"]["kind"]), ("style.many_accents", "suggest"))
        action = suggestion["repair"]["action"]
        # applied the way the page does (index.html qualityApply): a composition review "change" with the overrides
        r = self.review(pid, action["scene"], CINE, "change", slides=slides, overrides=action["overrides"])
        self.assertEqual(r["review"]["status"], "approved", "a scene's style is not its layout (Phase 17)")
        self.assertNotIn("style_accent", r["review"].get("overrides") or {})
        self.assertEqual(r["plan"]["style"]["look"]["scene_overrides"], {})
        self.assertEqual(r["plan"]["fingerprint"], slides[1]["cinematic_plan"]["fingerprint"], "the layout is the same")
        after = self.quality(self.load(pid), CINE, pid)
        self.assertNotIn(suggestion["id"], [f["id"] for f in after["issues"]])
        self.assertEqual([f for f in after["issues"] if f["scene"] == 1 and f["rule"].startswith("style.")], [])
        self.assertEqual(problems(after), [])

    @unittest.skipUnless(FAMILY["quality_media"], "the presenter family is not available")
    def test_a_composition_suggestion_through_review_becomes_the_users_change(self):
        scenes = lesson()
        scenes[2]["presenter_plan"] = teacher(placement="pip", position="left")  # the presenter changes sides, unexplained
        pid = self.save(self.fresh(CINE, scenes), "Side flip")
        self.assertEqual(self.review(pid, 2, CINE, "keep")["review"]["status"], "approved")
        slides = self.load(pid)
        report = self.quality(slides, CINE, pid)
        suggestion = next(f for f in report["issues"] if f["scene"] == 2 and f["repair"]["action"]["type"] == "composition")
        self.assertEqual((suggestion["repair"]["kind"], suggestion["repair"]["class"]), ("suggest", "composition"))
        action = suggestion["repair"]["action"]
        r = self.review(pid, action["scene"], CINE, "change", slides=slides, overrides=action["overrides"])
        self.assertEqual((r["review"]["status"], r["review"]["overrides"]), ("changed", action["overrides"]),
                         "a composition suggestion is the user's own change in Visual Review")
        after = self.quality(self.load(pid), CINE, pid)
        self.assertNotIn(suggestion["id"], [f["id"] for f in after["issues"]])

    def crafted(self):
        """Problem lessons of every kind: (name, scenes, settings)."""
        base = self.fresh(CINE)
        out = [("a style switch", base, {**CINE, "style": "academic"}),
               ("motion switched off after planning", base, {**CINE, "motion": "none"}),
               ("another transition after planning", base, {**CINE, "transitions": "wipe"})]
        damaged = copy.deepcopy(base)
        damaged[2]["cinematic_plan"]["camera"]["duration"] = round(damaged[2]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings
        damaged[1]["cinematic_plan"]["sync"] = {"version": 1, "events": "broken"}
        damaged[3]["cinematic_plan"]["transition"]["duration"] = 2.5
        damaged[4]["cinematic_plan"]["style"]["look"]["css"]["--st-accent"] = "#010203"
        del damaged[5]["cinematic_plan"]
        out.append(("damaged plans", damaged, CINE))
        mixed = copy.deepcopy(base)
        mixed[2]["cinematic_plan"] = self.fresh({**CINE, "style": "corporate_training"})[2]["cinematic_plan"]
        out.append(("one scene in another style", mixed, CINE))
        flipped = lesson()
        flipped[2]["presenter_plan"] = teacher(placement="pip", position="left")
        flipped[4]["presenter_plan"] = {**teacher(), "presenter_id": "ai-teacher", "type": "ai_avatar"}
        flipped[2]["title"] = "The Leaf Up Close"
        flipped[3]["narration"] = ("This sentence goes on and on about forces and masses and accelerations so that it is far "
                                   "longer than two subtitle lines could ever show.")
        out.append(("presenter and content", self.fresh(CINE, flipped), CINE))
        accents = lesson()
        for i, accent in ((0, "teal"), (1, "gold"), (2, "coral"), (3, "crimson")):
            accents[i]["visual_review"] = {"composition": {"status": "approved", "overrides": {"style_accent": accent}}}
        out.append(("many scene accents", self.fresh({**CINE, "style": "academic"}, accents), {**CINE, "style": "academic"}))
        return out

    def test_every_automatic_repair_is_an_approval_safe_replan(self):
        seen = {"auto": 0, "scene_style": 0, "composition": 0, "none": 0}
        for name, scenes, settings in self.crafted():
            with self.subTest(lesson=name):
                report = self.quality(scenes, settings)  # assert_contract checks every finding
                self.assertNotEqual(problems(report) + [f for f in report["issues"] if f["severity"] == "notice"], [],
                                    "a crafted problem lesson has findings")
                for f in report["issues"]:
                    rep = f["repair"]
                    seen["auto" if rep["kind"] == "auto" else rep["action"]["type"] if rep["kind"] == "suggest" else "none"] += 1
                    if rep["kind"] == "auto":
                        self.assertIn(rep["class"], ("presentation", "timing"))
                        self.assertEqual(rep["action"]["type"], "replan")
        self.assertTrue(all(seen.values()), f"every kind of repair was exercised: {seen}")


# ---- 3 the repair loop ---------------------------------------------------------------------------------------------------

class RepairLoopTest(Api):
    SETTINGS = {**CINE, "style": "academic"}

    def cases(self):
        """(name, scene, tamper, rules the tamper must raise): each an automatic, approval-safe repair."""
        other = self.fresh({**CINE, "style": "children_education"})
        # a stale layout: the stored camera move differs from what the planning gives now (a hash alone is not a layout)
        out = [("a stale layout", 2, lambda s: s[2]["cinematic_plan"]["camera"].__setitem__(
                    "duration", round(s[2]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)), ("core.stale_plan",)),
               # the timing family's own finding (sync_invalid) is fixed by the same re-plan: folded into the stale plan (its
               # evidence names it), one cause, one finding
               ("a damaged synchronization", 1, lambda s: s[1]["cinematic_plan"].__setitem__("sync", {"version": 1, "events": "broken"}),
                ("core.stale_plan",))]
        if FAMILY["quality_style"]:
            out.append(("a scene styled with another style", 3, lambda s: s[3].__setitem__("cinematic_plan", copy.deepcopy(other[3]["cinematic_plan"])),
                        ("style.mixed_styles",)))
        return out

    def test_the_pages_repair_removes_the_finding_and_changes_only_that_scene(self):
        base = self.fresh(self.SETTINGS)
        pid = self.save(base, "Repair loop")
        for name, target, tamper, expected in self.cases():
            with self.subTest(case=name):
                tampered = copy.deepcopy(base)
                tamper(tampered)
                report = self.quality(tampered, self.SETTINGS, pid)
                found = rules(report, *expected)
                self.assertEqual(sorted({f["rule"] for f in found}), sorted(expected))
                if name == "a damaged synchronization" and FAMILY["quality_timing"]:
                    self.assertIn("timing.sync_invalid", found[0]["evidence"].get("also", []))
                auto = [f for f in report["issues"] if f["repair"]["kind"] == "auto"]
                self.assertTrue({f["id"] for f in found} <= {f["id"] for f in auto}, "each is automatically repairable")
                self.assertEqual({i for f in auto for i in f["repair"]["action"]["scenes"]}, {target}, "only that scene is re-planned")
                self.assertEqual(report["summary"]["auto_repairable"], len(auto))
                with no_generation() as calls:
                    repaired = self.replan(tampered, self.SETTINGS, pid)
                self.assertEqual(calls, [])
                for i, (a, b) in enumerate(zip(tampered, repaired)):
                    if i != target:
                        self.assertEqual(a, b, f"scene {i}: the re-plan gives the same plan")
                after = self.quality(repaired, self.SETTINGS, pid)
                self.assertEqual(rules(after, *expected), [])
                self.assertEqual(problems(after), [])
                self.assertEqual(after["summary"]["auto_repairable"], 0)
                self.assertFalse({f["id"] for f in found} & {f["id"] for f in after["issues"]})
                before_fp, after_fp = fingerprints(report), fingerprints(after)
                self.assertNotEqual(after_fp[target], before_fp[target], "the repaired scene's fingerprint changed")
                self.assertEqual([fp for i, fp in enumerate(after_fp) if i != target],
                                 [fp for i, fp in enumerate(before_fp) if i != target], "the other scenes are unchanged")
                self.assertEqual(after_fp, fingerprints(self.quality(base, self.SETTINGS, pid)), "back to the lesson as planned")
                self.assertEqual(texts(repaired), texts(base), "no educational text changed")
                self.assertEqual([(s.get("visual_review"), s.get("visual_plan"), s.get("presenter_plan")) for s in repaired],
                                 [(s.get("visual_review"), s.get("visual_plan"), s.get("presenter_plan")) for s in base])


# ---- 4 / 5 Visual Review and media -----------------------------------------------------------------------------------------

def about_slot(report, index, slot):
    """Findings about one visual slot of a scene: its media, the slot itself, or a composition change of the visual."""
    out = []
    for f in report["issues"]:
        action = f["repair"]["action"]
        touches = action.get("type") == "composition" and action.get("scene") == index and \
            any(str(k).startswith("visual") for k in action.get("overrides") or {})
        if (f["scene"] == index and (f["dimension"] == "media" or f["element"] == slot or f["evidence"].get("slot") == slot)) or touches:
            out.append(f)
    return out


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class VisualReviewTest(Api):
    def chosen(self, name):
        """A saved lesson whose scene 2 shows a library picture chosen in Visual Review, composed for it."""
        pid = self.save(self.fresh(CINE), name)
        picture = self.asset()
        r = self.visual(pid, 1, "side", "choose", asset_id=picture["id"])
        self.assertEqual((r["review"]["status"], r["plans"]["side"]["asset_id"]), ("changed", picture["id"]))
        return pid, picture, self.replan(self.load(pid), CINE, pid)

    def test_a_removed_visual_stays_removed_and_nothing_asks_to_restore_it(self):
        pid, picture, _slides = self.chosen("Removed")
        r = self.visual(pid, 1, "side", "remove")
        self.assertEqual(r["review"]["status"], "removed")
        slides = self.replan(self.load(pid), CINE, pid)  # the page re-plans the composition after a visual decision
        self.assertFalse(any(l["type"] == "visual" for l in slides[1]["cinematic_plan"]["layers"]))
        record = copy.deepcopy(slides[1]["visual_review"])
        self.delete_file(picture["id"])  # even with its file gone
        before = self.saved_state(pid)
        report = self.quality(slides, CINE, pid)
        self.assertEqual(about_slot(report, 1, "side"), [])
        self.assertEqual(problems(report), [])
        self.assertEqual(self.saved_state(pid), before)
        self.assertEqual(self.load(pid)[1]["visual_review"], record)
        # an older copy of the scene that still names the removed picture: removed in Visual Review, still nothing
        for older in ({"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": picture["id"], "selection": "removed"},
                      {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": picture["id"], "selection": "reviewed"}):
            copy_ = copy.deepcopy(slides)
            copy_[1]["visual_plan"]["side"] = older
            with self.subTest(older=older["selection"]):
                self.assertEqual(about_slot(self.quality(copy_, CINE, pid), 1, "side"), [])
        # an automatic repair keeps it removed
        again = self.replan(slides, CINE, pid)
        self.assertFalse(any(l["type"] == "visual" for l in again[1]["cinematic_plan"]["layers"]))
        self.assertEqual(again[1]["visual_review"], record)

    @unittest.skipUnless(FAMILY["quality_media"], "the media family is not available")
    def test_another_asset_changes_the_scenes_fingerprint_and_the_report_is_stale(self):
        pid, _first, slides = self.chosen("Changed")
        settings = self.settings_dump(CINE)
        report = self.quality(slides, CINE, pid)
        self.assertEqual(problems(report), [])
        self.assertFalse(Q.is_stale(report, slides, settings, None, self.media_of(slides, pid), []))
        second = self.asset()
        r = self.visual(pid, 1, "side", "choose", asset_id=second["id"])
        record = r["review"]
        changed = self.load(pid)
        self.assertEqual(changed[1]["visual_review"]["side"], record)
        media = self.media_of(changed, pid)
        self.assertTrue(Q.is_stale(report, changed, settings, None, media, []), "the report no longer describes the lesson")
        before = self.saved_state(pid)
        with no_marking() as calls:
            after = self.quality(changed, CINE, pid)
        self.assertEqual(calls, [])
        self.assertFalse(Q.is_stale(after, changed, settings, None, media, []))
        self.assertNotEqual(after["fingerprint"], report["fingerprint"])
        self.assertNotEqual(fingerprints(after)[1], fingerprints(report)[1])
        self.assertEqual(fingerprints(after)[:1] + fingerprints(after)[2:], fingerprints(report)[:1] + fingerprints(report)[2:])
        self.assertEqual(self.saved_state(pid), before)
        self.assertEqual(self.load(pid)[1]["visual_review"]["side"], record, "the slot's review record is untouched")
        replanned = self.replan(changed, CINE, pid)
        self.assertEqual(replanned[1]["visual_review"]["side"], record)
        self.assertEqual(problems(self.quality(replanned, CINE, pid)), [])

    @unittest.skipUnless(FAMILY["quality_media"], "the media family is not available")
    def test_a_deleted_visual_file_blocks_the_export_and_the_asset_is_not_marked(self):
        pid, picture, slides = self.chosen("Deleted")
        clean = self.quality(slides, CINE, pid)
        self.assertEqual([f for f in clean["issues"] if f["dimension"] == "media" and f["severity"] != "info"], [])
        self.assertTrue(clean["registry"]["media"]["checked"])
        self.delete_file(picture["id"])
        row = self.asset_row(picture["id"])
        before = self.saved_state(pid)
        with no_marking() as calls, no_generation() as generated:
            report = self.quality(slides, CINE, pid)
            again = self.quality(slides, CINE, pid)
        self.assertEqual((calls, generated), ([], []))
        missing = rules(report, "media.missing")
        self.assertEqual([(f["scene"], f["severity"], f["evidence"].get("slot")) for f in missing], [(1, "blocking", "side")])
        self.assertEqual(missing[0]["repair"]["kind"], "none", "a replacement is the user's choice")
        self.assertEqual((report["status"], report["dimensions"]["media"]["status"]), ("blocked", "blocking"))
        self.assertEqual(again, report)
        self.assertEqual(self.asset_row(picture["id"]), row, "the asset row is read, never marked")
        self.assertEqual(row[0], "ready")
        self.assertEqual(self.saved_state(pid), before)
        # removed in Visual Review, the missing file is nobody's problem any more
        self.visual(pid, 1, "side", "remove")
        removed = self.replan(self.load(pid), CINE, pid)
        self.assertEqual(about_slot(self.quality(removed, CINE, pid), 1, "side"), [])
        self.assertEqual(self.asset_row(picture["id"]), row)


# ---- 6 preview / export --------------------------------------------------------------------------------------------------

class PreviewExportTest(Api):
    def test_preview_and_export_agree_until_a_plan_is_tampered(self):
        scenes = self.fresh(CINE)
        pid = self.save(scenes, "Preview export")
        report = self.quality(scenes, CINE, pid)
        self.assertEqual((report["dimensions"]["preview_export"]["status"], report["dimensions"]["preview_export"]["issues"]), ("pass", 0))
        self.assertFalse(any("not recomputed" in lim for lim in report["limitations"]))
        tampered = copy.deepcopy(scenes)
        tampered[2]["cinematic_plan"]["camera"]["duration"] = round(tampered[2]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings
        stale = self.quality(tampered, CINE, pid)
        found = rules(stale, "core.stale_plan")
        self.assertEqual([(f["scene"], f["severity"], f["dimension"]) for f in found], [(2, "error", "preview_export")])
        self.assertEqual(found[0]["repair"], {"kind": "auto", "class": "presentation", "action": {"type": "replan", "scenes": [2]},
                                              "label": found[0]["repair"]["label"]})
        self.assertEqual(stale["dimensions"]["preview_export"]["status"], "error")
        off = self.quality(tampered, CINE, pid, recompose=False)
        self.assertEqual(rules(off, "core.stale_plan"), [])
        self.assertIn("The plans were not recomputed (the preview / export comparison used the stored plans only).", off["limitations"])
        self.assertNotEqual(off["fingerprint"], stale["fingerprint"], "a report without the comparison is another report")
        self.assertEqual(problems(self.quality(self.replan(tampered, CINE, pid), CINE, pid)), [])


# ---- 7 recovery ----------------------------------------------------------------------------------------------------------

@unittest.skipUnless(FAMILY["quality_media"], "the media family is not available")
class RecoveryTest(Api):
    def runs(self, pid):
        table = models.AIGenerationRun.__table__
        with database.SessionLocal() as db:
            rows = db.query(models.AIGenerationRun).filter(models.AIGenerationRun.project_id == pid).order_by(models.AIGenerationRun.id).all()
            return [tuple(getattr(r, c.name) for c in table.columns) for r in rows], db.query(models.AIGenerationRun).count()

    def test_a_report_never_touches_generation_runs_or_recovery(self):
        scenes = lesson()
        scenes[1]["visual_plan"] = {"side": {"source": "AI_IMAGE", "media": "STATIC_IMAGE", "selection": "auto", "requires_generation": True}}
        scenes[7]["visual_plan"] = {"main": {"source": "AI_VIDEO", "media": "VIDEO", "selection": "auto", "requires_generation": True}}
        scenes = self.fresh(CINE, scenes)
        pid = self.save(scenes, "Runs")
        now = datetime.datetime.utcnow()
        past = now - datetime.timedelta(minutes=5)

        def run(status, minutes, scene, slot, media_type, **extra):
            return models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{self.uid}", user_id=self.uid, media_type=media_type,
                                          status=status, provider="fake-image", model="m1", project_id=pid, scene_index=scene, slot=slot,
                                          request=json.dumps({"prompt": "a run of the lesson"}),
                                          created_at=now - datetime.timedelta(minutes=minutes), **extra)
        with database.SessionLocal() as db:
            db.add_all([run("queued", 30, 7, "main", "video", lease_owner="gone-worker", lease_expires_at=past),  # recovery would take it
                        run("failed", 20, 7, "main", "video", error_message="the provider failed"),
                        run("running", 10, 1, "side", "image", lease_owner="gone-worker", lease_expires_at=past, heartbeat_at=past),
                        run("needs_attention", 5, None, "background", "image")])
            db.commit()
        before = self.runs(pid)
        with no_recovery() as calls, no_generation() as generated:
            report = self.quality(scenes, CINE, pid)
        self.assertEqual((calls, generated), ([], []), "no recovery manager, no run claimed or leased, nothing generated")
        self.assertEqual(self.runs(pid), before, "the same runs, the same statuses, leases and attempts")
        # the runs were read: the failed video and the picture still being made are reported
        self.assertEqual([(f["scene"], f["evidence"].get("slot"), f["evidence"].get("found")) for f in rules(report, "media.run_failed")],
                         [(7, "main", "failed")])
        making = rules(report, "media.not_generated")
        self.assertEqual([(f["scene"], f["evidence"].get("slot"), f["evidence"].get("found")) for f in making], [(1, "side", "making")])
        self.assertTrue(all(f["repair"]["kind"] == "none" for f in rules(report, "media.run_failed", "media.not_generated")),
                        "never a regeneration")


# ---- 8 determinism and performance ---------------------------------------------------------------------------------------

class DeterminismTest(Api):
    def problem_lesson(self):
        scenes = lesson()
        scenes[2]["presenter_plan"] = teacher(placement="pip", position="left")
        scenes[2]["title"] = "The Leaf Up Close"
        scenes = self.fresh(CINE, scenes)
        scenes[3]["cinematic_plan"]["camera"]["duration"] = round(scenes[3]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings
        scenes[1]["cinematic_plan"]["sync"] = {"version": 1, "events": "broken"}
        return scenes

    def test_the_same_lesson_gives_the_same_report(self):
        scenes = self.problem_lesson()
        pid = self.save(scenes, "Determinism")
        body = {"scenes": scenes, "settings": {**CINE, "style": "academic"}, "project_id": pid}
        answers = []
        for clear in (True, False, True):
            if clear:
                Q._SCENE_MEMO.clear()  # computed afresh, then reused from the per-scene memo
            r = self.client.post("/api/quality/lesson", json=copy.deepcopy(body), headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            answers.append(r)
        first = answers[0].json()
        self.assertGreaterEqual(len([f for f in first["issues"] if f["severity"] != "info"]), 3)
        for r in answers[1:]:
            self.assertEqual(r.json(), first)
            self.assertEqual([f["id"] for f in r.json()["issues"]], [f["id"] for f in first["issues"]], "the same ids in the same order")
            self.assertEqual(r.text, answers[0].text, "byte for byte")
        self.assertEqual(len({f["id"] for f in first["issues"]}), len(first["issues"]), "one finding per id")

    def test_thirty_scenes_through_the_api_in_under_two_seconds(self):
        scenes = [dict(s, title=f"{s['title']} {n + 1}") for n in range(4) for s in lesson()][:30]
        scenes = self.fresh(CINE, scenes)
        pid = self.save(scenes, "Thirty scenes")
        Q._SCENE_MEMO.clear()
        t0 = time.perf_counter()
        report = self.quality(scenes, CINE, pid)
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        self.quality(scenes, CINE, pid)
        warm = time.perf_counter() - t0
        print(f"\n[quality integration] 30 scenes through the API: {cold * 1000:.0f} ms (then {warm * 1000:.0f} ms)")
        self.assertEqual(report["summary"]["scenes"], 30)
        self.assertLess(cold, 2.0)
        self.assertLess(warm, 2.0)


# ---- 9 security ----------------------------------------------------------------------------------------------------------

class SecurityTest(Api):
    def test_report_strings_are_data(self):
        scenes = lesson()
        scenes[1]["title"] = XSS
        scenes[3]["narration"] = '<script>alert("x")</script> "quoted" & more.'
        scenes = self.fresh(CINE, scenes)
        scenes[1]["cinematic_plan"]["camera"]["duration"] = round(scenes[1]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings
        r = self.client.post("/api/quality/lesson", json={"scenes": scenes, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.headers["content-type"].startswith("application/json"))
        report = r.json()
        assert_contract(self, report, len(scenes))
        stale = rules(report, "core.stale_plan")
        self.assertEqual([f["scene"] for f in stale], [1])
        self.assertIn(XSS, stale[0]["message"], "the title stays in the message as text (the page escapes it)")
        for f in report["issues"]:
            plain_json(self, f["evidence"], f["rule"])
        json.dumps(report, allow_nan=False)

    def test_oversized_lessons_bad_settings_and_other_users_lessons_are_refused(self):
        pid = self.save(self.fresh(CINE), "Mine")
        self.post("/api/quality/lesson", {"scenes": [{"type": "content", "title": f"S{i}"} for i in range(201)], "settings": CINE,
                                          "project_id": pid}, status=413)
        ok = self.quality([{"type": "content", "title": f"S{i}"} for i in range(200)], CINE, pid, recompose=False)
        self.assertEqual(ok["summary"]["scenes"], 200)
        for bad in ({"style": "neon"}, {"style_overrides": {"accent": "neon"}}, {"style_overrides": {"glitter": "on"}},
                    {"style_version": 0}, {"transitions": "spiral"}, {"motion": "wild"}, {"presenter_position": "top"},
                    {"director": "magic"}, {"composer_provider": "evil"}, {"background_asset_id": "../x"}):
            with self.subTest(bad=bad):
                self.post("/api/quality/lesson", {"scenes": lesson()[:2], "settings": {**CINE, **bad}, "project_id": pid}, status=422)
        self.post("/api/quality/lesson", {"scenes": "not a lesson", "settings": CINE}, status=422)
        self.post("/api/quality/lesson", {"scenes": lesson()[:2], "settings": CINE, "project_id": pid}, status=404, headers=auth(self.other))
        self.post("/api/quality/lesson", {"scenes": lesson()[:2], "settings": CINE, "project_id": 99999999}, status=404)
        r = self.client.post("/api/quality/lesson", json={"scenes": lesson()[:2], "settings": CINE, "project_id": pid})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
