"""Phase 19 — the editor's playback timing on the backend (cinematic.py, quality_timing.py).

A scene's minimum duration (scene.edit.min_seconds, 0 < x ≤ 600) is presentation timing: the plan carries it as
plan["min_seconds"] (its "duration" stays the narration's estimate), validate_plan bounds it, and it is never part of the
input fingerprint, so a Visual Review approval is kept. The timing checks use max(min_seconds, the narration's time) as the
scene's play time (a long hold is no longer "too much to read"; a hold shorter than the narration is a notice, with no
repair); a muted narration plays like a silent scene; hidden scenes (scene.edit.hidden) are not played, so the checks across
scenes skip them. Lessons without editor data plan and check exactly as before.

Run: PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_editor_playback.py"
"""
import copy
import json
import unittest
from unittest import mock

from backend_env import assert_isolated  # first: throwaway database

import cinematic as C  # noqa: E402
import quality as Q  # noqa: E402
import quality_timing as T  # noqa: E402
from test_cinematic import CINE  # noqa: E402
from test_cinematic import lesson as cine_lesson  # noqa: E402
from test_quality_timing import FIXTURES, StrictTestCase, check, of, planned, problems  # noqa: E402
from test_sync_director import real_lesson  # noqa: E402
from test_visual_director import AT, SETTINGS  # noqa: E402
from test_visual_director import lesson as vd_lesson  # noqa: E402


def setUpModule():
    assert_isolated()


def edited(scene, **edit):
    return {**copy.deepcopy(scene), "edit": edit}


LONG_NARRATION = " ".join(["Plants turn light into sugar in their leaves every single day of the year."] * 6)


class PlanMinSecondsTest(unittest.TestCase):
    def setUp(self):
        self.scene = cine_lesson()[1]
        self.plain = C.compose_scene(copy.deepcopy(self.scene), 1, 8, CINE)

    def test_the_plan_carries_the_minimum_and_keeps_the_narration_duration(self):
        plan = C.compose_scene(edited(self.scene, min_seconds=12), 1, 8, CINE)
        self.assertEqual(plan["min_seconds"], 12.0)
        self.assertEqual(plan["duration"], self.plain["duration"])
        self.assertEqual(plan["duration"], C.estimate_seconds(self.scene["narration"]))
        self.assertEqual(C.validate_plan(plan), [])
        self.assertNotIn("min_seconds", self.plain)
        # the build_plan path itself (the Phase 13 decision) carries it too
        direct = C.build_plan(edited(self.scene, min_seconds=7.5), 1, 8, CINE)
        self.assertEqual(direct["min_seconds"], 7.5)
        self.assertEqual(C.build_plan(edited(self.scene, min_seconds=7.5), 1, 8, {**CINE, "mode": "classic"}), None)
        json.dumps(plan)

    def test_presentation_timing_never_touches_the_fingerprint_or_the_rendered_plan(self):
        plan = C.compose_scene(edited(self.scene, min_seconds=30), 1, 8, CINE)
        self.assertEqual(plan["fingerprint"], self.plain["fingerprint"])
        self.assertEqual(plan["plan_hash"], self.plain["plan_hash"])
        without = {k: v for k, v in plan.items() if k != "min_seconds"}
        self.assertEqual(without, self.plain)
        # the other editor flags are page playback only: the plan is exactly the generated scene's
        flags = C.compose_scene(edited(self.scene, hidden=True, narration_muted=True, captions="off", origin="generated"), 1, 8, CINE)
        self.assertEqual(flags, self.plain)

    def test_an_approval_is_kept_when_the_minimum_changes(self):
        fp = self.plain["fingerprint"]
        review = {"composition": {"status": "approved", "fingerprint": fp}}
        for hold in (3, 45.5, 600):
            with self.subTest(min_seconds=hold):
                plan = C.compose_scene({**edited(self.scene, min_seconds=hold), "visual_review": review}, 1, 8, CINE)
                self.assertEqual(plan["review_status"], "approved")
                self.assertNotIn("review_stale", plan)

    def test_only_a_valid_minimum_reaches_the_plan(self):
        for bad in (0, -1, 600.5, 1000, "10", True, False, None, float("nan"), float("inf"), [5], {"s": 5}, 0.001):
            with self.subTest(value=bad):
                self.assertIsNone(C.edit_min_seconds(edited(self.scene, min_seconds=bad)))
                self.assertNotIn("min_seconds", C.build_plan(edited(self.scene, min_seconds=bad), 1, 8, CINE))
        for good, kept in ((0.5, 0.5), (600, 600.0), (12.5, 12.5), (2.004, 2.0), (8, 8.0)):
            with self.subTest(value=good):
                self.assertEqual(C.edit_min_seconds(edited(self.scene, min_seconds=good)), kept)
        for odd in (None, {}, {"edit": None}, {"edit": "min_seconds=5"}, {"edit": [5]}, "scene"):
            with self.subTest(scene=odd):
                self.assertIsNone(C.edit_min_seconds(odd))

    def test_validate_plan_bounds_the_minimum(self):
        plan = copy.deepcopy(self.plain)
        self.assertEqual(C.validate_plan(plan), [])  # no minimum: nothing to check
        message = "the minimum duration must be more than 0 s and at most 600 s"
        for bad in (0, -2, 600.01, "5", True, None, float("nan"), [1]):
            with self.subTest(value=bad):
                self.assertIn(message, C.validate_plan({**plan, "min_seconds": bad}))
        for good in (0.1, 5, 600):
            with self.subTest(value=good):
                self.assertEqual(C.validate_plan({**plan, "min_seconds": good}), [])

    def test_old_lessons_plan_exactly_as_before(self):
        plans = C.compose_lesson(cine_lesson(), CINE)
        self.assertTrue(all(p is None or "min_seconds" not in p for p in plans))
        again = C.compose_lesson([edited(s, origin="generated") for s in cine_lesson()], CINE)
        self.assertEqual(again, plans)  # editor data other than playback timing changes nothing
        with_hold = C.compose_lesson([edited(s, min_seconds=20) if i == 2 else s for i, s in enumerate(cine_lesson())], CINE)
        for i, (a, b) in enumerate(zip(plans, with_hold)):
            with self.subTest(scene=i):
                if i == 2:
                    self.assertEqual(b["min_seconds"], 20.0)
                    b = {k: v for k, v in b.items() if k != "min_seconds"}
                self.assertEqual(a, b)


class QualityTimingMinSecondsTest(StrictTestCase):
    def lesson(self, scenes, settings=SETTINGS):
        return Q.Lesson(scenes, settings)

    def test_the_scene_time_is_at_least_the_minimum(self):
        scenes = planned([vd_lesson()[0], vd_lesson()[2]], SETTINGS)
        base = T.scene_seconds(self.lesson(scenes), 1)
        self.assertEqual(base, T.narration_scene_seconds(self.lesson(scenes), 1))
        scenes[1]["edit"] = {"min_seconds": base + 20}
        self.assertEqual(T.scene_seconds(self.lesson(scenes), 1), round(base + 20, 2))
        self.assertEqual(T.narration_scene_seconds(self.lesson(scenes), 1), base)  # the narration is never changed
        scenes[1]["edit"] = {"min_seconds": 1}  # shorter than the narration: the narration decides
        self.assertEqual(T.scene_seconds(self.lesson(scenes), 1), base)
        # a scene without narration: Phase 13's 5 s, or the minimum when longer
        silent = [{"type": "content", "title": "Quiet", "html": "<p>Look.</p>"}]
        self.assertEqual(T.scene_seconds(self.lesson(silent), 0), C.estimate_seconds(""))
        silent[0]["edit"] = {"min_seconds": 9}
        self.assertEqual(T.scene_seconds(self.lesson(silent), 0), 9.0)
        # a muted narration plays like a silent scene
        scenes[1]["edit"] = {"narration_muted": True}
        self.assertEqual(T.scene_seconds(self.lesson(scenes), 1), C.estimate_seconds(""))
        scenes[1]["edit"] = {"narration_muted": True, "min_seconds": 14}
        self.assertEqual(T.scene_seconds(self.lesson(scenes), 1), 14.0)

    def test_a_long_hold_gives_time_to_read(self):
        board = "<p>" + " ".join(["Photosynthesis converts light energy into chemical energy stored in glucose."] * 8) + "</p>"
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "html": board, "narration": "Read this."}], SETTINGS)
        self.assertEqual(len(of(check(scenes, SETTINGS), "timing.reading_time", 1)), 1)
        scenes[1]["edit"] = {"min_seconds": 120}
        found = check(scenes, SETTINGS)
        self.assertEqual(of(found, "timing.reading_time"), [])
        self.assertEqual(of(found, "timing.duration_mismatch"), [])  # the plan's duration still follows the narration
        self.assertEqual(of(found, "timing.hold_shorter"), [])

    def test_a_hold_shorter_than_the_narration_is_a_notice(self):
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "narration": LONG_NARRATION}], SETTINGS)
        unedited = check(scenes, SETTINGS)
        scenes[1]["edit"] = {"min_seconds": 4}
        found = check(scenes, SETTINGS)
        f = of(found, "timing.hold_shorter", 1)
        self.assertEqual(len(f), 1)
        needs = T.narration_seconds(self.lesson(scenes), 1)
        self.assertEqual((f[0]["severity"], f[0]["dimension"], f[0]["element"]), ("notice", "timing", "duration"))
        self.assertEqual((f[0]["repair"]["kind"], f[0]["repair_status"]), ("none", "not_repairable"))
        self.assertIn("the scene is set to 4.0 s but its narration needs about", f[0]["message"])
        self.assertIn(T._secs(needs), f[0]["message"])
        self.assertTrue(f[0]["message"].endswith("it plays until the narration ends."))
        self.assertEqual(f[0]["evidence"], {"key": "hold_shorter", "min_seconds": 4.0, "narration_seconds": round(needs, 2)})
        self.assertEqual(problems([x for x in found if x["rule"] != "timing.hold_shorter"]), problems(unedited))  # nothing else
        # a hold as long as the narration, a muted narration or no narration at all: nothing to say
        for edit in ({"min_seconds": needs + 1}, {"min_seconds": 4, "narration_muted": True}):
            with self.subTest(edit=edit):
                scenes[1]["edit"] = edit
                self.assertEqual(of(check(scenes, SETTINGS), "timing.hold_shorter"), [])
        quiet = [{"type": "content", "title": "Quiet", "html": "<p>Look.</p>", "edit": {"min_seconds": 2}}]
        self.assertEqual(of(check(quiet, {**SETTINGS, "mode": "classic"}), "timing.hold_shorter"), [])

    def test_things_on_screen_during_the_hold_are_seen(self):
        scenes = planned(real_lesson(), SETTINGS)
        i = AT["formula"]
        plan = scenes[i]["cinematic_plan"]
        plan.pop("sync", None)  # without synchronization the timeline drives the labels
        next(e for e in plan["timeline"] if e["layer"] == "labels")["at"] = 30.0
        self.assertEqual(len(of(check(scenes, SETTINGS), "timing.item_after_end", i)), 1)
        scenes[i]["edit"] = {"min_seconds": 40}
        self.assertEqual(of(check(scenes, SETTINGS), "timing.item_after_end"), [])

    def test_the_full_report_reads_the_minimum(self):
        Q._SCENE_MEMO.clear()
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "narration": LONG_NARRATION}], SETTINGS)
        before = Q.evaluate_lesson(copy.deepcopy(scenes), SETTINGS)
        scenes[1]["edit"] = {"min_seconds": 3}
        after = Q.evaluate_lesson(scenes, SETTINGS)
        self.assertEqual([f for f in before["issues"] if f["rule"] == "timing.hold_shorter"], [])
        self.assertEqual(len([f for f in after["issues"] if f["rule"] == "timing.hold_shorter"]), 1)
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])  # the report knows the lesson changed
        self.assertFalse([x for x in after["limitations"] if x.startswith("quality_timing")])


def plan_of(template="comparison", movement="slow_zoom_in", transition="fade"):
    return {"template": template, "camera": {"movement": movement},
            "transition": {"in": transition, "out": "fade", "duration": C.TRANSITION_SECONDS[transition]}}


def scenes_of(*plans):
    return [{"type": "content", "title": f"Scene {n}", "html": "<p>Text.</p>", "cinematic_plan": p} for n, p in enumerate(plans)]


CINEMATIC = {"mode": "cinematic"}


class HiddenScenesTest(StrictTestCase):
    def test_a_hidden_scene_is_not_between_its_neighbours(self):
        lesson = Q.Lesson(scenes_of(plan_of(), plan_of(), plan_of()), CINEMATIC)
        self.assertEqual(T.played(lesson), [0, 1, 2])
        self.assertEqual(T._runs(T.played(lesson), lambda p, c: True), [[0, 1, 2]])
        scenes = scenes_of(plan_of(), plan_of(), plan_of())
        scenes[1]["edit"] = {"hidden": True}
        lesson = Q.Lesson(scenes, CINEMATIC)
        self.assertEqual(T.played(lesson), [0, 2])
        self.assertTrue(T.hidden(lesson, 1))
        self.assertEqual(T._runs(T.played(lesson), lambda p, c: c - p == 1), [[0], [2]])

    def test_repetition_counts_the_scenes_that_play(self):
        scenes = scenes_of(*(plan_of() for _ in range(3)))
        f = T._repetition(Q.Lesson(scenes, CINEMATIC))
        self.assertEqual([(x["rule"], x["scene"], x["evidence"]["count"]) for x in f], [("timing.motion_repetition", 2, 3)])
        scenes[1]["edit"] = {"hidden": True}
        self.assertEqual(T._repetition(Q.Lesson(scenes, CINEMATIC)), [])
        scenes = scenes_of(*(plan_of() for _ in range(4)))
        scenes[1]["edit"] = {"hidden": True}
        f = T._repetition(Q.Lesson(scenes, CINEMATIC))
        self.assertEqual([(x["scene"], x["evidence"]["count"]) for x in f], [(3, 3)])  # the third scene that plays

    def test_a_hidden_still_scene_gives_the_viewer_no_rest(self):
        moving = [plan_of() for _ in range(5)]
        moving.insert(3, plan_of(movement="static"))
        scenes = scenes_of(*moving)
        self.assertEqual(T._camera_constant(Q.Lesson(scenes, CINEMATIC)), [])  # the still scene breaks the run
        scenes[3]["edit"] = {"hidden": True}
        f = T._camera_constant(Q.Lesson(scenes, CINEMATIC))
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["evidence"]["from"], f[0]["evidence"]["to"], f[0]["evidence"]["count"]), (0, 5, 5))
        self.assertNotEqual(f[0]["evidence"]["middle"], 3)  # a scene that plays

    def test_camera_switches_and_transition_kinds_follow_the_played_order(self):
        scenes = scenes_of(plan_of(movement="pan_left"), plan_of(movement="pan_right"), plan_of(movement="pan_left"))
        self.assertEqual([x["scene"] for x in T._camera_switch(Q.Lesson(scenes, CINEMATIC))], [1, 2])
        scenes[1]["edit"] = {"hidden": True}
        self.assertEqual(T._camera_switch(Q.Lesson(scenes, CINEMATIC)), [])
        kinds = scenes_of(plan_of(transition="fade"), plan_of(transition="crossfade"), plan_of(transition="slide"))
        self.assertEqual(len(T._transition_mix(Q.Lesson(kinds, CINEMATIC))), 1)
        kinds[2]["edit"] = {"hidden": True}
        self.assertEqual(T._transition_mix(Q.Lesson(kinds, CINEMATIC)), [])

    def test_dense_runs_skip_hidden_scenes(self):
        scenes = scenes_of(plan_of(), plan_of(), plan_of())
        lesson = Q.Lesson(scenes, CINEMATIC)
        with mock.patch.object(T, "_intent", lambda _lesson, i: {"density": "high" if i in (0, 2) else "low"}):
            self.assertEqual(T._density_consecutive(lesson), [])
            scenes[1]["edit"] = {"hidden": True}
            f = T._density_consecutive(Q.Lesson(scenes, CINEMATIC))
        self.assertEqual([(x["scene"], x["evidence"]["from"], x["evidence"]["to"], x["evidence"]["count"]) for x in f], [(2, 0, 2, 2)])

    def test_the_registry_counts_only_the_scenes_that_play(self):
        scenes = planned(real_lesson(), SETTINGS)
        full = T.registry(Q.Lesson(scenes, SETTINGS))["pacing"]
        scenes[2]["edit"] = {"hidden": True}
        part = T.registry(Q.Lesson(scenes, SETTINGS))["pacing"]
        self.assertEqual(part["scene_seconds"][2], 0.0)
        self.assertEqual(len(part["scene_seconds"]), len(full["scene_seconds"]))
        self.assertAlmostEqual(part["total_seconds"], full["total_seconds"] - full["scene_seconds"][2], places=1)


class OldLessonsUnchangedTest(StrictTestCase):
    def test_lessons_without_editor_data_check_exactly_as_before(self):
        for name, make, base in FIXTURES:
            with self.subTest(fixture=name):
                scenes = planned(make(), base)
                lesson = Q.Lesson(scenes, base)
                self.assertEqual(T.played(lesson), list(range(lesson.count)))
                for i in range(lesson.count):
                    self.assertEqual(T.scene_seconds(lesson, i), T.narration_scene_seconds(lesson, i))
                    self.assertIsNone(T.hold_seconds(lesson, i))
                self.assertEqual(check(scenes, base), [])
                # editor data that does not change playback (an id, its origin) changes no finding either
                tagged = [{**s, "scene_id": f"s-{n:012x}", "edit": {"origin": "generated"}} for n, s in enumerate(scenes)]
                self.assertEqual(check(tagged, base), [])


if __name__ == "__main__":
    unittest.main()
