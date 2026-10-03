"""Phase 18 — the Quality & Consistency Engine's core contract (quality.py): the finding format, repair rules (only an
approval-safe re-plan may be automatic), fingerprints (stale reports), the report, the preview / export comparison, the
read-only API. The check families have their own tests (test_quality_style / _media / _education / _timing).

Run: python -m unittest discover -s tests -p "test_quality.py"
"""
import copy
import json
import time
import unittest
from unittest import mock

from backend_env import assert_isolated, auth, ensure_user  # noqa: F401  first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import cinematic as C  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import quality as Q  # noqa: E402
import server  # noqa: E402
from test_cinematic import CINE, lesson  # noqa: E402


def planned(scenes=None, settings=CINE):
    scenes = lesson() if scenes is None else scenes
    for scene, plan in zip(scenes, C.compose_lesson(copy.deepcopy(scenes), settings)):
        if plan:
            scene["cinematic_plan"] = plan
    return scenes


def recomposed(scenes, settings=CINE):
    return C.compose_lesson(copy.deepcopy(scenes), settings)


class FindingContractTest(unittest.TestCase):
    def test_a_finding_carries_what_where_why_how_serious_and_how_to_repair(self):
        f = Q.issue("style.mixed_versions", "style", "error", "Scene 2 uses an older version of the style.", scene=1,
                    element="plan", evidence={"key": "version", "found": [1, 2]},
                    repair=Q.repair("auto", "presentation", {"type": "replan", "scenes": [1]}, "Re-plan the scene"))
        self.assertEqual(set(f), {"id", "rule", "dimension", "severity", "scene", "element", "message", "evidence", "repair",
                                  "repair_status"})
        self.assertEqual(f["repair"]["kind"], "auto")
        self.assertEqual(f["repair_status"], "available")
        self.assertEqual(f["id"], Q.issue("style.mixed_versions", "style", "error", "other words", scene=1, element="plan",
                                          evidence={"key": "version", "found": [1, 2]})["id"], "the id is stable: rule + where + key")
        plain = Q.issue("education.term_casing", "terminology", "notice", "x", scene=0)
        self.assertEqual((plain["repair"]["kind"], plain["repair_status"]), ("none", "not_repairable"))

    def test_malformed_findings_are_refused(self):
        for bad in (dict(rule="NoDot"), dict(dimension="vibes"), dict(severity="fatal"), dict(scene=-1), dict(scene=True),
                    dict(scene="2")):
            args = {"rule": "style.x", "dimension": "style", "severity": "notice", "scene": 0, **bad}
            with self.subTest(bad=bad), self.assertRaises(Q.Invalid):
                Q.issue(args["rule"], args["dimension"], args["severity"], "m", scene=args["scene"])

    def test_only_an_approval_safe_replan_may_be_automatic(self):
        Q.repair("auto", "timing", {"type": "replan", "scenes": [0]}, "Re-plan")
        for kind, klass, action in (("auto", "composition", {"type": "composition", "scene": 0, "overrides": {}}),
                                    ("auto", "presentation", {"type": "scene_style", "scene": 0, "overrides": {}}),
                                    ("auto", "content", {"type": "replan", "scenes": [0]}),
                                    ("auto", "visual", {"type": "replan", "scenes": [0]}),
                                    ("auto", "presenter", {"type": "replan", "scenes": [0]}),
                                    ("fix", "presentation", {"type": "replan"}), ("suggest", "magic", {"type": "replan"}),
                                    ("suggest", "content", {"type": "rewrite"})):
            with self.subTest(kind=kind, klass=klass, action=action), self.assertRaises(Q.Invalid):
                Q.repair(kind, klass, action, "x")
        s = Q.repair("suggest", "composition", {"type": "composition", "scene": 2, "overrides": {"presenter_size": "small"}}, "Make the presenter smaller")
        self.assertEqual(s["action"]["overrides"], {"presenter_size": "small"})

    def test_evidence_is_bounded_plain_data(self):
        f = Q.issue("style.x", "style", "notice", "<b>m</b>" * 200, evidence={"key": "k", "obj": object(), "deep": {"a": {"b": {"c": {"d": 1}}}},
                                                                              "long": "x" * 999, "many": list(range(50))})
        self.assertLessEqual(len(f["message"]), 400)
        self.assertEqual(len(f["evidence"]["long"]), 200)
        self.assertEqual(len(f["evidence"]["many"]), 12)
        json.dumps(f)  # JSON-safe


class FingerprintTest(unittest.TestCase):
    def test_deterministic_and_blind_to_timestamps_ai_records_and_notes(self):
        scenes = planned()
        lesson_ = Q.Lesson(scenes, CINE)
        fps = [Q.scene_fingerprint(lesson_, i) for i in range(lesson_.count)]
        again = Q.Lesson(copy.deepcopy(scenes), CINE)
        self.assertEqual(fps, [Q.scene_fingerprint(again, i) for i in range(again.count)])
        noisy = copy.deepcopy(scenes)
        noisy[1]["visual_review"] = {"composition": {"status": "approved", "fingerprint": noisy[1]["cinematic_plan"]["fingerprint"],
                                                     "reviewed_at": "2026-01-01T00:00:00"}}
        quiet = copy.deepcopy(noisy)
        quiet[1]["visual_review"]["composition"]["reviewed_at"] = "2027-05-05T00:00:00"
        quiet[1]["cinematic_plan"]["notes"] = ["a different note"]
        quiet[1]["cinematic_plan"]["composition"] = {**quiet[1]["cinematic_plan"].get("composition", {}), "ai": {"status": "ok"}}
        a, b = Q.Lesson(noisy, CINE), Q.Lesson(quiet, CINE)
        self.assertEqual(Q.scene_fingerprint(a, 1), Q.scene_fingerprint(b, 1))

    def test_changes_with_what_the_checks_depend_on(self):
        scenes = planned()
        base = Q.Lesson(scenes, CINE)
        fp = Q.scene_fingerprint(base, 1)
        for change in (lambda s: s[1].__setitem__("html", s[1].get("html", "") + "<p>more</p>"),
                       lambda s: s[1].__setitem__("narration", "Different words."),
                       lambda s: s[1]["cinematic_plan"].__setitem__("plan_hash", "x" * 16),
                       lambda s: s[1].__setitem__("visual_review", {"side": {"status": "removed", "fingerprint": "f"}}),
                       lambda s: s[1].__setitem__("presenter_plan", {"presenter_id": "someone-else"})):
            changed = copy.deepcopy(scenes)
            change(changed)
            self.assertNotEqual(Q.scene_fingerprint(Q.Lesson(changed, CINE), 1), fp)
        # the lesson's style is a lesson setting: every scene's checks depend on it
        self.assertNotEqual(Q.scene_fingerprint(Q.Lesson(scenes, {**CINE, "style": "academic"}), 1), fp)
        # another scene's change leaves this scene's fingerprint alone
        other = copy.deepcopy(scenes)
        other[3]["narration"] = "Something else entirely."
        self.assertEqual(Q.scene_fingerprint(Q.Lesson(other, CINE), 1), fp)

    def test_a_report_knows_when_it_is_stale(self):
        scenes = planned()
        report = Q.evaluate_lesson(scenes, CINE)
        self.assertFalse(Q.is_stale(report, scenes, CINE))
        changed = copy.deepcopy(scenes)
        changed[2]["narration"] = "New narration."
        self.assertTrue(Q.is_stale(report, changed, CINE))
        self.assertTrue(Q.is_stale(report, scenes, {**CINE, "style": "corporate_training"}))
        self.assertTrue(Q.is_stale({**report, "rules": "quality_rules@0"}, scenes, CINE), "other rules: stale")
        self.assertTrue(Q.is_stale(None, scenes, CINE))


class ReportTest(unittest.TestCase):
    def test_the_report_shape_and_status_rules(self):
        report = Q.evaluate_lesson(planned(), CINE)
        for key in ("rules", "version", "fingerprint", "status", "summary", "dimensions", "scenes", "issues", "registry", "limitations"):
            self.assertIn(key, report)
        self.assertEqual(report["rules"], Q.RULES)
        self.assertEqual(list(report["dimensions"]), list(Q.DIMENSIONS))
        self.assertEqual(report["summary"]["scenes"], len(lesson()))
        self.assertIn("Pictures and clips were not looked up (save the lesson to check its files).", report["limitations"])
        counts = lambda **kw: {s: kw.get(s, 0) for s in Q.SEVERITIES}  # noqa: E731
        self.assertEqual(Q._status(counts()), "good")
        self.assertEqual(Q._status(counts(info=3)), "good")
        self.assertEqual(Q._status(counts(notice=1)), "review")
        self.assertEqual(Q._status(counts(warning=1, notice=2)), "review")
        self.assertEqual(Q._status(counts(error=1)), "attention")
        self.assertEqual(Q._status(counts(blocking=1, error=4)), "blocked")

    def test_findings_are_ordered_deduplicated_and_tagged_with_the_scene_fingerprint(self):
        scenes = planned()

        class Fam:
            @staticmethod
            def scene_checks(lesson, i):
                return [Q.issue("style.demo", "style", "notice", "n", scene=i)] if i == 2 else []

            @staticmethod
            def lesson_checks(lesson):
                return [Q.issue("style.demo", "style", "notice", "n", scene=2),  # the same finding twice: reported once
                        Q.issue("timing.demo", "timing", "error", "e", scene=0)]

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("fam", Fam)]):
            report = Q.evaluate_lesson(scenes, CINE)
        rules = [(f["rule"], f["scene"]) for f in report["issues"]]
        self.assertEqual(rules, [("timing.demo", 0), ("style.demo", 2)], "most serious first, one per id")
        self.assertEqual(report["issues"][1]["fingerprint"], report["scenes"][2]["fingerprint"])
        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["dimensions"]["timing"]["status"], "error")
        self.assertEqual(report["scenes"][2]["status"], "review")

    def test_a_broken_family_is_a_limitation_never_a_crash(self):
        class Broken:
            @staticmethod
            def scene_checks(lesson, i):
                raise RuntimeError("boom")

            @staticmethod
            def lesson_checks(lesson):
                raise KeyError("x")

            @staticmethod
            def registry(lesson):
                raise ValueError("y")

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("broken", Broken)]):
            report = Q.evaluate_lesson(planned(), CINE)
        self.assertEqual(report["issues"], [])
        self.assertTrue(any(lim.startswith("broken:") for lim in report["limitations"]))

    def test_unchanged_scenes_reuse_their_findings(self):
        scenes = planned()
        calls = []

        class Counting:
            @staticmethod
            def scene_checks(lesson, i):
                calls.append(i)
                return []

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("counting", Counting)]):
            Q.evaluate_lesson(scenes, CINE)
            first = len(calls)
            changed = copy.deepcopy(scenes)
            changed[4]["narration"] = "Only this scene changed."
            Q.evaluate_lesson(changed, CINE)
        self.assertEqual(first, len(scenes))
        self.assertEqual(calls[first:], [4], "only the changed scene is checked again")

    def test_the_input_is_never_changed(self):
        scenes = planned()
        before = copy.deepcopy(scenes)
        Q.evaluate_lesson(scenes, CINE, None, {"checked": True, "assets": {}, "runs": []}, recomposed(scenes))
        self.assertEqual(scenes, before)

    def test_a_large_lesson_is_quick(self):
        scenes = planned(lesson() * 4)  # 32 scenes
        Q._SCENE_MEMO.clear()
        t0 = time.perf_counter()
        Q.evaluate_lesson(scenes, CINE, None, None, recomposed(scenes))
        elapsed = time.perf_counter() - t0
        print(f"\n[quality] 32 scenes evaluated in {elapsed * 1000:.0f} ms")
        self.assertLess(elapsed, 3.0)


class PreviewExportTest(unittest.TestCase):
    """Preview and export play the same stored plan; the check compares it with what the current planning gives."""

    def test_a_current_lesson_has_no_preview_export_findings(self):
        scenes = planned()
        report = Q.evaluate_lesson(scenes, CINE, None, None, recomposed(scenes))
        self.assertEqual([f for f in report["issues"] if f["dimension"] == "preview_export"], [])

    def test_a_stale_plan_is_found_and_repairable_by_a_replan(self):
        scenes = planned()
        scenes[2]["cinematic_plan"]["camera"]["duration"] = round(scenes[2]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings  # planned with older settings
        report = Q.evaluate_lesson(scenes, CINE, None, None, recomposed(scenes))
        stale = [f for f in report["issues"] if f["rule"] == "core.stale_plan"]
        self.assertEqual([f["scene"] for f in stale], [2])
        self.assertEqual(stale[0]["severity"], "error")
        self.assertEqual(stale[0]["repair"]["kind"], "auto")
        self.assertEqual(stale[0]["repair"]["action"], {"type": "replan", "scenes": [2]})
        self.assertIn(stale[0]["repair"]["class"], ("presentation", "timing"))

    def test_a_style_switch_shows_as_a_stale_look_until_replanned(self):
        scenes = planned()
        academic = {**CINE, "style": "academic"}
        report = Q.evaluate_lesson(scenes, academic, None, None, recomposed(scenes, academic))
        # the style family owns a stale look (compared with Phase 17 directly); the core never reports it twice
        self.assertTrue(any(f["rule"] in ("style.stale_look", "style.mixed_styles") for f in report["issues"]))
        self.assertEqual([f for f in report["issues"] if f["rule"] == "core.stale_plan"], [],
                         "a style switch is one problem (the stale look), never also a stale layout or timing")
        replanned = planned(lesson(), academic)
        report = Q.evaluate_lesson(replanned, academic, None, None, recomposed(replanned, academic))
        self.assertEqual([f for f in report["issues"] if f["dimension"] == "preview_export"], [])

    def test_a_scene_without_a_plan_in_a_cinematic_lesson(self):
        scenes = planned()
        del scenes[3]["cinematic_plan"]
        report = Q.evaluate_lesson(scenes, CINE)
        self.assertEqual([f["scene"] for f in report["issues"] if f["rule"] == "core.no_plan"], [3])

    def test_classic_lessons_have_no_plan_findings(self):
        report = Q.evaluate_lesson(lesson(), {**CINE, "mode": "classic"})
        self.assertEqual([f for f in report["issues"] if f["dimension"] == "preview_export"], [])


class BrowserRoundTripTest(unittest.TestCase):
    """Plans the page stores went through the browser (JSON in JavaScript writes 1.0 as 1): the same plan, never stale."""

    def test_a_round_tripped_lesson_is_not_stale_and_keeps_its_fingerprints(self):
        def js_round_trip(value):
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, dict):
                return {k: js_round_trip(v) for k, v in value.items()}
            if isinstance(value, list):
                return [js_round_trip(v) for v in value]
            return value

        scenes = planned()
        stored = js_round_trip(copy.deepcopy(scenes))
        self.assertNotEqual(json.dumps(stored, sort_keys=True), json.dumps(scenes, sort_keys=True), "the round trip changes the text")
        report = Q.evaluate_lesson(stored, CINE, None, None, recomposed(scenes))
        self.assertEqual([f for f in report["issues"] if f["rule"] == "core.stale_plan"], [])
        a, b = Q.Lesson(scenes, CINE), Q.Lesson(stored, CINE)
        self.assertEqual([Q.scene_fingerprint(a, i) for i in range(a.count)], [Q.scene_fingerprint(b, i) for i in range(b.count)])
        self.assertFalse(Q.is_stale(Q.evaluate_lesson(scenes, CINE), stored, CINE))


class AuditFindingsTest(unittest.TestCase):
    """Defects found by the adversarial audit, each pinned."""

    def test_signed_links_never_make_a_plan_stale(self):
        scenes = planned()
        now = recomposed(scenes)
        for plan in now:
            if plan and isinstance(plan.get("background"), dict):
                plan["background"]["url"] = "/media/x?token=fresh"  # the same picture, a link signed later
        for scene in scenes:
            plan = scene.get("cinematic_plan")
            if plan and isinstance(plan.get("background"), dict):
                plan["background"]["url"] = "/media/x?token=old"
        report = Q.evaluate_lesson(scenes, CINE, None, None, now)
        self.assertEqual([f for f in report["issues"] if f["rule"] == "core.stale_plan"], [])

    def test_one_cause_one_finding(self):
        scenes = planned()

        class Fam:
            @staticmethod
            def lesson_checks(lesson):
                replan = Q.repair("auto", "timing", {"type": "replan", "scenes": [2]}, "Re-plan")
                same = Q.repair("suggest", "composition", {"type": "composition", "scene": 3, "overrides": {"presenter_size": "small"}}, "Smaller")
                return [Q.issue("timing.motion_x", "motion", "error", "a", scene=2, repair=replan),
                        Q.issue("timing.camera_x", "camera", "error", "b", scene=2, repair=replan),
                        Q.issue("style.text_x", "typography", "warning", "c", scene=3, repair=same),
                        Q.issue("timing.busy_x", "density", "notice", "d", scene=3, repair=same),
                        Q.issue("education.term_x", "terminology", "notice", "e", scene=3)]

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("fam", Fam)]):
            report = Q.evaluate_lesson(scenes, CINE)
        by_scene = {}
        for f in report["issues"]:
            by_scene.setdefault(f["scene"], []).append(f)
        self.assertEqual(len(by_scene[2]), 1, "two findings fixed by the same re-plan: one")
        self.assertEqual(by_scene[2][0]["evidence"]["also"], ["timing.motion_x"])
        self.assertEqual(sorted(f["rule"] for f in by_scene[3]), ["education.term_x", "style.text_x"], "the same suggestion once")
        self.assertEqual([f for f in by_scene[3] if f["rule"] == "style.text_x"][0]["evidence"]["also"], ["timing.busy_x"])

    def test_an_automatic_repair_never_takes_an_approval_away(self):
        scenes = planned()
        for i in (1, 2):
            scenes[i]["visual_review"] = {"composition": {"status": "approved", "fingerprint": scenes[i]["cinematic_plan"]["fingerprint"]}}
        for i in (1, 2, 3):
            scenes[i]["cinematic_plan"]["camera"]["duration"] = round(scenes[i]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)
        # the same settings: the re-plan keeps the approved scenes' inputs, so the repair stays automatic
        report = Q.evaluate_lesson(scenes, CINE, None, None, recomposed(scenes))
        kinds = {f["scene"]: f["repair"]["kind"] for f in report["issues"] if f["rule"] == "core.stale_plan"}
        self.assertEqual(kinds, {1: "auto", 2: "auto", 3: "auto"})
        # the lesson's motion changed since: re-planning would ask the approved scenes again -> a suggestion that says so
        still = {**CINE, "motion": "none"}
        report = Q.evaluate_lesson(scenes, still, None, None, recomposed(scenes, still))
        for f in report["issues"]:
            if f["repair"]["kind"] == "auto":
                self.assertFalse(set(f["repair"]["action"]["scenes"]) & {1, 2}, f["rule"])
        downgraded = [f for f in report["issues"] if f["repair"]["label"] == "Re-plan (the scene's approval will be asked again)"]
        self.assertTrue(downgraded)
        self.assertTrue(all(f["repair"]["kind"] == "suggest" and f["repair"]["class"] == "composition" for f in downgraded))

    def test_long_texts_are_bounded_and_the_input_untouched(self):
        scenes = planned()
        scenes[1]["narration"] = "word" * 20000
        scenes[2]["title"] = " " * 20000 + "x"
        before = copy.deepcopy(scenes)
        lesson_ = Q.Lesson(scenes, CINE)
        self.assertLessEqual(len(lesson_.scene(1)["narration"]), Q.TEXT_LIMITS["narration"])
        self.assertLessEqual(len(lesson_.scene(2)["title"]), Q.TEXT_LIMITS["title"])
        t0 = time.perf_counter()
        Q._SCENE_MEMO.clear()
        Q.evaluate_lesson(scenes, CINE)
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(scenes, before)

    def test_another_scenes_run_never_rechecks_this_scene(self):
        scenes = planned()
        media = {"checked": True, "assets": {}, "runs": [{"scene_index": 4, "slot": "main", "status": "running"}]}
        other = {**media, "runs": [{"scene_index": 4, "slot": "main", "status": "failed"}]}
        a, b = Q.Lesson(scenes, CINE, None, media), Q.Lesson(scenes, CINE, None, other)
        self.assertEqual(Q.scene_media_digest(a, 1), Q.scene_media_digest(b, 1))
        self.assertNotEqual(Q.scene_media_digest(a, 4), Q.scene_media_digest(b, 4))

    def test_the_assistant_runs_only_when_asked(self):
        self.assertFalse(Q.Lesson(planned(), CINE).assist)
        self.assertTrue(Q.Lesson(planned(), CINE, assist=True).assist)


class FinalReviewFindingsTest(unittest.TestCase):
    """Defects found by the final security / correctness review, each pinned."""

    def test_odd_but_valid_plans_never_break_the_report(self):
        scenes = planned()
        scenes[0]["cinematic_plan"]["sync"] = {"events": 5}
        scenes[1]["cinematic_plan"]["sync"] = {"events": True}
        deep = {}
        cur = deep
        for _ in range(600):
            cur["x"] = {}
            cur = cur["x"]
        scenes[2]["cinematic_plan"]["sync"] = {"events": [{"id": "e", "type": "label_enter", "target": deep}]}
        scenes[3]["cinematic_plan"]["transition"] = {"in": "fade", "duration": float("inf")}
        report = Q.evaluate_lesson(scenes, CINE, None, None, recomposed(planned()))
        json.dumps(report, allow_nan=False)  # what the API sends: valid JSON, no infinite numbers
        self.assertEqual(report["rules"], Q.RULES)

    def test_a_failed_check_is_not_reused_without_its_note(self):
        scenes = planned()
        calls = {"n": 0}

        class Flaky:
            @staticmethod
            def scene_checks(lesson, i):
                calls["n"] += 1
                raise RuntimeError("boom")

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("flaky", Flaky)]):
            first = Q.evaluate_lesson(scenes, CINE)
            second = Q.evaluate_lesson(scenes, CINE)
        self.assertTrue(any(lim.startswith("flaky:") for lim in first["limitations"]))
        self.assertTrue(any(lim.startswith("flaky:") for lim in second["limitations"]), "the note shows every time")
        self.assertEqual(calls["n"], 2 * len(scenes), "a failed result is never kept")

    def test_scene_checks_stop_at_their_budget(self):
        scenes = planned()

        class Slow:
            @staticmethod
            def scene_checks(lesson, i):
                return []

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("slow", Slow)]), mock.patch.object(Q, "SCENE_CHECK_BUDGET", -1):
            report = Q.evaluate_lesson(scenes, CINE)
        self.assertTrue(any("could not be checked in time" in lim for lim in report["limitations"]))


class HiddenSceneTest(unittest.TestCase):
    """Phase 19: a scene hidden in the editor is not played: its findings never count as something to review."""

    def test_a_hidden_scenes_findings_are_info(self):
        scenes = planned()

        class Fam:
            @staticmethod
            def scene_checks(lesson, i):
                return [Q.issue("timing.reading_x", "timing", "warning", "too much to read", scene=i)] if i == 2 else []

        Q._SCENE_MEMO.clear()
        with mock.patch.object(Q, "_families", return_value=[("fam", Fam)]):
            shown = Q.evaluate_lesson(scenes, CINE)
            scenes[2]["edit"] = {"hidden": True}
            hidden = Q.evaluate_lesson(scenes, CINE)
        self.assertEqual(shown["status"], "review")
        self.assertEqual([(f["severity"], f["evidence"].get("was")) for f in hidden["issues"]], [("info", "warning")])
        self.assertEqual(hidden["status"], "good")


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("quinn")
        ensure_user("rhea")
        cls.client = TestClient(server.app)
        cls.headers = auth("quinn")

    def post(self, body, headers=None):
        return self.client.post("/api/quality/lesson", json=body, headers=self.headers if headers is None else headers)

    def test_the_report_and_its_errors(self):
        scenes = planned()
        r = self.post({"scenes": scenes, "settings": CINE})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rules"], Q.RULES)
        self.assertEqual(self.client.post("/api/quality/lesson", json={"scenes": scenes, "settings": CINE}).status_code, 401)
        self.assertEqual(self.post({"scenes": [{}] * 201, "settings": CINE}).status_code, 413)
        self.assertEqual(self.post({"scenes": scenes, "settings": {**CINE, "style": "neon"}}).status_code, 422)
        self.assertEqual(self.client.get("/api/quality", headers=self.headers).json()["rules"], Q.RULES)

    def test_read_only_nothing_is_saved_and_another_users_lesson_is_refused(self):
        scenes = planned()
        pid = self.client.post("/save-history", json={"subject_name": "Q", "scenes": scenes}, headers=self.headers).json()["id"]

        def snapshot():
            with database.SessionLocal() as db:
                rows = db.query(models.Project).count()
                data = db.query(models.Project.json_data).filter(models.Project.id == pid).scalar()
                return rows, data

        before = snapshot()
        r = self.post({"scenes": scenes, "settings": CINE, "project_id": pid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(snapshot(), before, "no new history entry, the saved lesson unchanged")
        self.assertEqual(self.post({"scenes": scenes, "settings": CINE, "project_id": pid}, headers=auth("rhea")).status_code, 404)

    def test_the_stored_plans_compared_with_the_current_planning(self):
        scenes = planned()
        scenes[1]["cinematic_plan"]["camera"]["duration"] = round(scenes[1]["cinematic_plan"]["camera"]["duration"] + 1.5, 2)  # a layout from older settings
        r = self.post({"scenes": scenes, "settings": CINE})
        self.assertTrue(any(f["rule"] == "core.stale_plan" and f["scene"] == 1 for f in r.json()["issues"]))
        r = self.post({"scenes": scenes, "settings": CINE, "recompose": False})
        self.assertFalse(any(f["rule"] == "core.stale_plan" for f in r.json()["issues"]))
        self.assertIn("The plans were not recomputed (the preview / export comparison used the stored plans only).", r.json()["limitations"])

    def test_a_lesson_too_large_to_check(self):
        scenes = [{"type": "content", "title": "x", "html": "<p>" + "a" * 20000 + "</p>", "narration": "b" * 10000}] * 200
        self.assertEqual(self.post({"scenes": scenes, "settings": CINE, "recompose": False}).status_code, 413)

    def test_nothing_is_generated(self):
        with mock.patch("ai_media.AIMediaService.generate", side_effect=AssertionError("no generation"), create=True), \
                mock.patch("source_documents.call_model", side_effect=AssertionError("no model in automatic mode")):
            r = self.post({"scenes": planned(), "settings": CINE})
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
