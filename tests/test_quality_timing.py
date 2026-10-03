"""Phase 18 — the timing family of the Quality & Consistency Engine (quality_timing.py).

The family checks the stored plans against Phases 13–16's own rules (it builds no timing): the narration's length, the
synchronization (valid, inside the narration, pointing at what the plan shows, not too busy), transitions, the camera and
motion (still when the motion is switched off, no restless switching or repetition), busy and empty scenes, captions and
reading time. Clean lessons stay silent in every style and motion level; each rule fires on a crafted problem; a scene's
own choice is information, not a problem; the checks are deterministic, read-only and never crash.

Run: PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_quality_timing.py"
"""
import copy
import json
import re
import time
import unittest
from unittest import mock

from backend_env import assert_isolated  # first: throwaway database

import cinematic as C  # noqa: E402
import quality as Q  # noqa: E402
import quality_timing as T  # noqa: E402
import styles  # noqa: E402
from test_cinematic import CINE  # noqa: E402
from test_cinematic import lesson as cine_lesson  # noqa: E402
from test_sync_director import real_lesson  # noqa: E402
from test_visual_director import AT, SETTINGS, teacher  # noqa: E402
from test_visual_director import lesson as vd_lesson  # noqa: E402


def setUpModule():
    assert_isolated()


def strict(checks, *args):
    """The family's rule runner without its safety net: a rule that breaks fails the test instead of staying silent."""
    out = []
    for check in checks:
        out += check(*args) or []
    return out


def planned(scenes, settings):
    """The lesson as saved after planning: each scene carries its plan (the input scenes are left untouched)."""
    scenes = copy.deepcopy(scenes)
    for scene, plan in zip(scenes, C.compose_lesson(copy.deepcopy(scenes), settings)):
        if plan:
            scene["cinematic_plan"] = plan
    return scenes


def check(scenes, settings):
    lesson = Q.Lesson(scenes, settings)
    return [f for i in range(lesson.count) for f in T.scene_checks(lesson, i)] + T.lesson_checks(lesson)


def of(found, rule, scene="any"):
    return [f for f in found if f["rule"] == rule and (scene == "any" or f["scene"] == scene)]


def problems(found):
    return [(f["rule"], f["severity"], f["scene"], f["message"]) for f in found if f["severity"] != "info"]


def sync_event(n, estimate, ratio, target=None, type_="formula_emphasis"):
    return {"id": f"x{n}", "type": type_, "target": target or {"layer": "board", "kind": "formula"},
            "at": {"segment": 0, "ratio": ratio}, "estimate": estimate, "duration": 1.0, "tolerance": 0.25, "priority": 1,
            "anchor": {"kind": "fraction", "value": 0.5}, "concept": None, "depends_on": [], "params": {}}


FIXTURES = (("Phase 13 lesson", cine_lesson, CINE), ("Phase 16 lesson", real_lesson, SETTINGS),
            ("Phase 15 lesson", vd_lesson, SETTINGS))
STYLES = (None,) + tuple(styles.FAMILIES)


class StrictTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(T, "_safely", strict)
        patcher.start()
        self.addCleanup(patcher.stop)


class CleanLessonsTest(StrictTestCase):
    def test_clean_fixtures_are_silent_in_every_style_and_motion_level(self):
        for name, make, base in FIXTURES:
            for style in STYLES:
                for motion in ("subtle", "none"):
                    for transitions in ("fade", "slide"):
                        settings = {**base, "motion": motion, "transitions": transitions, **({"style": style} if style else {})}
                        with self.subTest(fixture=name, style=style, motion=motion, transitions=transitions):
                            found = check(planned(make(), settings), settings)
                            self.assertEqual(problems(found), [])
                            self.assertEqual(found, [])  # no scene made a choice of its own: nothing to note either

    def test_the_full_report_carries_the_family_without_problems(self):
        Q._SCENE_MEMO.clear()
        scenes = planned(real_lesson(), SETTINGS)
        report = Q.evaluate_lesson(scenes, SETTINGS)
        self.assertIn("quality_timing", report["families"])
        mine = [f for f in report["issues"] if f["rule"].startswith("timing.")]
        self.assertEqual([f for f in mine if f["severity"] != "info"], [])
        self.assertFalse([x for x in report["limitations"] if x.startswith("quality_timing")])
        for section in ("camera", "transitions", "pacing"):
            self.assertIn(section, report["registry"])

    def test_classic_lessons_have_no_plan_findings(self):
        settings = {**SETTINGS, "mode": "classic"}
        for name, make, _base in FIXTURES:
            with self.subTest(fixture=name):
                self.assertEqual(check(make(), settings), [])

    def test_the_registry(self):
        reg = T.registry(Q.Lesson(planned(real_lesson(), SETTINGS), SETTINGS))
        self.assertEqual(set(reg), {"camera", "transitions", "pacing"})
        self.assertEqual(reg["transitions"], {"lesson": "fade", "used": {"fade": 12}})
        self.assertEqual(sum(reg["camera"]["moves"].values()), 12)
        self.assertEqual(reg["camera"]["still_scenes"], reg["camera"]["moves"].get("static", 0))
        self.assertGreater(reg["camera"]["still_scenes"], 0)
        self.assertEqual(len(reg["pacing"]["scene_seconds"]), 12)
        self.assertTrue(all(s >= T.MIN_SCENE for s in reg["pacing"]["scene_seconds"]))
        self.assertAlmostEqual(reg["pacing"]["total_seconds"], sum(reg["pacing"]["scene_seconds"]), places=1)
        json.dumps(reg)  # plain data
        # a lesson without motion: the slide it asks for is shown as the fade the plans really use
        none = {**SETTINGS, "motion": "none", "transitions": "slide"}
        self.assertEqual(T.registry(Q.Lesson(planned(real_lesson(), none), none))["transitions"]["lesson"], "fade")
        self.assertEqual(T.registry(Q.Lesson([], SETTINGS))["pacing"], {"total_seconds": 0, "scene_seconds": []})


class TimingRulesTest(StrictTestCase):
    def setUp(self):
        super().setUp()
        self.scenes = planned(real_lesson(), SETTINGS)

    def plan(self, name):
        return self.scenes[AT[name]]["cinematic_plan"]

    def test_a_moment_after_the_narration_ends(self):
        i = AT["formula"]
        self.plan("formula")["sync"]["events"][0]["estimate"] = 99.0
        f = of(check(self.scenes, SETTINGS), "timing.event_after_end")
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["scene"], f[0]["dimension"]), ("error", i, "timing"))
        self.assertEqual(f[0]["repair"]["kind"], "auto")
        self.assertEqual(f[0]["repair"]["action"], {"type": "replan", "scenes": [i]})
        self.assertEqual(f[0]["repair"]["class"], "timing")
        # a moment in a part of the narration that no longer exists
        scenes = planned(real_lesson(), SETTINGS)
        scenes[i]["cinematic_plan"]["sync"]["events"][1]["at"] = {"segment": 5, "ratio": 0.2}
        self.assertEqual(len(of(check(scenes, SETTINGS), "timing.event_after_end", i)), 1)

    def test_things_on_screen_after_the_scene_ends(self):
        i = AT["formula"]
        plan = self.plan("formula")
        plan.pop("sync")  # without synchronization the timeline drives the labels and highlights
        next(e for e in plan["timeline"] if e["layer"] == "labels")["at"] = 30.0
        f = of(check(self.scenes, SETTINGS), "timing.item_after_end", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"], f[0]["repair"]["class"]), ("error", "auto", "timing"))
        # with synchronization the timeline's labels wait for their moments, but an element's entrance still counts
        scenes = planned(real_lesson(), SETTINGS)
        presenter = T._layer(scenes[i]["cinematic_plan"], "presenter")
        found = check(scenes, SETTINGS)
        self.assertEqual(of(found, "timing.item_after_end"), [])
        presenter["start"] = 30.0
        self.assertEqual(len(of(check(scenes, SETTINGS), "timing.item_after_end", i)), 1)

    def test_a_planned_length_that_no_longer_matches_the_narration(self):
        i = AT["process"]
        self.plan("process")["duration"] = 40.0
        f = of(check(self.scenes, SETTINGS), "timing.duration_mismatch", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"], f[0]["repair"]["class"]), ("notice", "auto", "timing"))
        # what re-planning gives today is never reported (a re-plan could not change it): a pause longer than the
        # narration's own bound
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "narration": "Wait. [PAUSE:100] Done."}], SETTINGS)
        self.assertEqual(of(check(scenes, SETTINGS), "timing.duration_mismatch"), [])

    def test_a_damaged_synchronization(self):
        i = AT["diagram"]
        self.plan("diagram")["sync"]["version"] = 99
        self.scenes[AT["process"]]["cinematic_plan"]["sync"] = "not a plan"
        found = check(self.scenes, SETTINGS)
        f = of(found, "timing.sync_invalid")
        self.assertEqual({x["scene"] for x in f}, {i, AT["process"]})
        self.assertTrue(all(x["severity"] == "error" and x["repair"]["kind"] == "auto" for x in f))
        # nothing else is read from a damaged synchronization
        self.assertEqual(of(found, "timing.event_after_end") + of(found, "timing.orphan_target"), [])

    def test_moments_pointing_at_what_the_scene_does_not_show(self):
        formula = self.plan("formula")
        formula["sync"]["events"].append(sync_event(1, 1.0, 0.2, {"layer": "labels", "item": 7}, "label_enter"))
        formula["sync"]["events"].append(sync_event(2, 1.5, 0.3, {"layer": "board", "kind": "column", "index": 5}, "text_emphasis"))
        diagram = self.plan("diagram")
        diagram["layers"] = [l for l in diagram["layers"] if l["id"] != "visual"]  # the picture is gone, its moments stay
        explanation = self.plan("explanation")
        explanation["camera"] = {**explanation["camera"], "movement": "static"}  # a camera moment with no move to make
        found = check(self.scenes, SETTINGS)
        f = {x["scene"]: x for x in of(found, "timing.orphan_target")}
        self.assertEqual(set(f), {AT["formula"], AT["diagram"], AT["explanation"]})
        self.assertEqual(f[AT["formula"]]["evidence"]["targets"], ["board", "labels"])
        self.assertEqual(f[AT["diagram"]]["evidence"]["targets"], ["visual"])
        self.assertEqual(f[AT["explanation"]]["evidence"]["targets"], ["camera"])
        for x in f.values():
            self.assertEqual((x["severity"], x["repair"]["kind"], x["repair"]["class"]), ("error", "auto", "timing"))

    def test_too_many_moments_close_together(self):
        i = AT["formula"]
        self.plan("formula")["sync"]["events"] = [sync_event(n, round(0.4 * n, 2), round(0.07 * n, 4)) for n in range(10)]
        f = of(check(self.scenes, SETTINGS), "timing.motion_busy", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["dimension"], f[0]["evidence"]["moments"]), ("notice", "motion", 10))
        # a highlight and the presenter pointing at it are one moment
        self.plan("formula")["sync"]["events"] = [sync_event(n, 0.5 * (n // 3), 0.1 * (n // 3)) for n in range(9)]
        self.assertEqual(of(check(self.scenes, SETTINGS), "timing.motion_busy"), [])

    def test_long_caption_sentences(self):
        long = ("This sentence is deliberately much too long for two subtitle lines because it keeps going and going with "
                "many clauses, words and ideas in it.")
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "narration": long + " Short one."}], SETTINGS)
        f = of(check(scenes, SETTINGS), "timing.caption_long")
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["scene"], f[0]["repair"]["kind"]), ("notice", 1, "none"))
        self.assertEqual((f[0]["evidence"]["count"], f[0]["evidence"]["longest"]), (1, len(long)))

    def test_too_much_to_read_for_the_narration(self):
        board = "<p>" + " ".join(["Photosynthesis converts light energy into chemical energy stored in glucose."] * 8) + "</p>"
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "html": board, "narration": "Read this."}], SETTINGS)
        f = of(check(scenes, SETTINGS), "timing.reading_time", 1)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"]), ("warning", "none"))
        self.assertIn("too much to read in the time the narration gives", f[0]["message"])
        # the same board with narration long enough to read it is fine
        enough = " ".join(["Plants turn light into sugar in their leaves every day."] * 12)
        scenes = planned([vd_lesson()[0], {**vd_lesson()[2], "html": board, "narration": enough}], SETTINGS)
        self.assertEqual(of(check(scenes, SETTINGS), "timing.reading_time"), [])


class TransitionRulesTest(StrictTestCase):
    def test_a_moving_transition_without_motion(self):
        settings = {**SETTINGS, "motion": "none"}
        scenes = planned(real_lesson(), settings)
        i = AT["formula"]
        scenes[i]["cinematic_plan"]["transition"] = {"in": "slide", "out": "fade", "duration": C.TRANSITION_SECONDS["slide"]}
        found = check(scenes, settings)
        f = of(found, "timing.moving_transition", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"], f[0]["repair"]["class"]), ("error", "auto", "presentation"))
        self.assertEqual(of(found, "timing.transition_mismatch"), [])  # one finding for one problem
        # a scene whose own motion is "none" (the lesson moves) follows the same rule
        scenes = planned(real_lesson(), SETTINGS)
        plan = scenes[AT["code"]]["cinematic_plan"]
        plan["motion"] = "none"
        plan["transition"] = {"in": "wipe", "out": "fade", "duration": C.TRANSITION_SECONDS["wipe"]}
        f = of(check(scenes, SETTINGS), "timing.moving_transition", AT["code"])
        self.assertEqual(len(f), 1)
        self.assertIn("this scene has no motion", f[0]["message"])

    def test_a_transition_of_the_wrong_length(self):
        scenes = planned(real_lesson(), SETTINGS)
        scenes[2]["cinematic_plan"]["transition"]["duration"] = 1.5
        f = of(check(scenes, SETTINGS), "timing.transition_seconds", 2)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["evidence"]["expected"], f[0]["repair"]["kind"]), ("error", 0.5, "auto"))

    def test_a_scene_s_own_transition_is_information_and_three_kinds_are_noticed(self):
        scenes = real_lesson()
        scenes[AT["comparison"]]["composition"] = {"transition": "wipe"}  # the screenplay's choice
        scenes[AT["example"]]["visual_review"] = {"composition": {"status": "changed", "overrides": {"transition": "crossfade"}}}
        found = check(planned(scenes, SETTINGS), SETTINGS)
        f = {x["scene"]: x for x in of(found, "timing.transition_choice")}
        self.assertEqual(set(f), {AT["comparison"], AT["example"]})
        self.assertTrue(all(x["severity"] == "info" and x["repair"]["kind"] == "none" for x in f.values()))
        mix = of(found, "timing.transition_mix")
        self.assertEqual(len(mix), 1)
        self.assertEqual((mix[0]["severity"], mix[0]["scene"], mix[0]["evidence"]["kinds"]), ("notice", None, ["crossfade", "fade", "wipe"]))
        self.assertEqual(of(found, "timing.transition_mismatch"), [])
        # two kinds are not a mix
        scenes[AT["example"]].pop("visual_review")
        self.assertEqual(of(check(planned(scenes, SETTINGS), SETTINGS), "timing.transition_mix"), [])

    def test_an_unexplained_transition_and_a_choice_without_motion(self):
        scenes = planned(real_lesson(), SETTINGS)
        scenes[3]["cinematic_plan"]["transition"] = {"in": "cut", "out": "fade", "duration": 0.0}
        f = of(check(scenes, SETTINGS), "timing.transition_mismatch", 3)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"], f[0]["repair"]["class"]), ("notice", "auto", "presentation"))
        # a slide asked for in a lesson without motion plays as a fade, which is exactly right: nothing to report
        none = {**SETTINGS, "motion": "none"}
        scenes = real_lesson()
        scenes[3]["composition"] = {"transition": "slide"}
        self.assertEqual(check(planned(scenes, none), none), [])


class CameraRulesTest(StrictTestCase):
    def test_a_camera_move_without_motion(self):
        none = {**SETTINGS, "motion": "none"}
        scenes = planned(real_lesson(), none)
        i = AT["explanation"]
        scenes[i]["cinematic_plan"]["camera"]["movement"] = "slow_zoom_in"
        f = of(check(scenes, none), "timing.camera_motion_off", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["dimension"], f[0]["repair"]["kind"], f[0]["repair"]["class"]),
                         ("error", "camera", "auto", "presentation"))
        # a camera moment in the synchronization would move a still camera all the same (and is not a second problem)
        scenes = planned(real_lesson(), none)
        plan = scenes[AT["process"]]["cinematic_plan"]
        plan["sync"]["events"].append({**sync_event(9, 1.0, 0.1, {"layer": "camera"}, "camera_focus"),
                                       "params": {"to": {"x": 0.0, "y": 0.0, "w": 0.95}, "duration": 2.0}})
        found = check(scenes, none)
        self.assertEqual(len(of(found, "timing.camera_motion_off", AT["process"])), 1)
        self.assertEqual(of(found, "timing.orphan_target"), [])
        # a scene of its own without motion keeps a still camera too
        scenes = planned(real_lesson(), SETTINGS)
        scenes[AT["intro"]]["cinematic_plan"]["motion"] = "none"
        f = of(check(scenes, SETTINGS), "timing.camera_motion_off", AT["intro"])
        self.assertEqual(len(f), 1)
        self.assertIn("this scene has no motion", f[0]["message"])

    def test_constant_camera_motion(self):
        scenes = [{"type": "content", "title": f"Idea {n}", "html": f"<p>Leaves hold a pigment number {n} that reflects green light.</p>",
                   "narration": f"Leaves look green for reason {n}.", "presenter_plan": teacher()} for n in range(7)]
        lesson = planned(scenes, SETTINGS)
        self.assertTrue(all(T._moving(s["cinematic_plan"]) for s in lesson))
        found = check(lesson, SETTINGS)
        f = of(found, "timing.camera_constant")
        self.assertEqual(len(f), 1)
        # info: the planner's own default moves the camera gently in most scenes (recorded, never counted as a problem)
        self.assertEqual((f[0]["severity"], f[0]["scene"], f[0]["evidence"]["count"]), ("info", 0, 7))
        self.assertEqual(f[0]["repair"]["kind"], "none")
        self.assertEqual(len(of(found, "timing.motion_repetition")), 1)  # the same layout and move, again and again
        # one still scene in the middle breaks the run
        lesson[3]["cinematic_plan"]["camera"]["movement"] = "static"
        self.assertEqual(of(check(lesson, SETTINGS), "timing.camera_constant"), [])

    def test_switching_camera_moves_without_a_reason(self):
        scenes = planned(real_lesson(), SETTINGS)
        scenes[1]["cinematic_plan"]["camera"]["movement"] = "pan_left"
        scenes[2]["cinematic_plan"]["camera"]["movement"] = "pan_right"
        f = {x["scene"]: x for x in of(check(scenes, SETTINGS), "timing.camera_switch")}
        self.assertEqual(set(f), {1, 2})
        self.assertEqual((f[2]["severity"], f[2]["evidence"]["previous"], f[2]["evidence"]["found"]), ("notice", "pan_left", "pan_right"))
        self.assertEqual(f[2]["repair"]["action"]["overrides"], {"camera": "static"})
        # a move the scene asked for has its reason
        scenes[2]["visual_review"] = {"composition": {"status": "changed", "overrides": {"camera": "pan_right"}}}
        self.assertEqual({x["scene"] for x in of(check(scenes, SETTINGS), "timing.camera_switch")}, {1})

    def test_a_moving_camera_over_code(self):
        scenes = planned(real_lesson(), SETTINGS)
        i = AT["code"]
        scenes[i]["cinematic_plan"]["camera"]["movement"] = "pan_left"
        f = of(check(scenes, SETTINGS), "timing.camera_reading", i)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"]), ("notice", "suggest"))
        self.assertEqual(f[0]["repair"]["action"], {"type": "composition", "scene": i, "overrides": {"camera": "static"}})
        # chosen for this scene: information
        scenes[i]["visual_review"] = {"composition": {"status": "changed", "overrides": {"camera": "pan_left"}}}
        f = of(check(scenes, SETTINGS), "timing.camera_reading", i)
        self.assertEqual([(x["severity"], x["repair"]["kind"]) for x in f], [("info", "none")])
        # a formula keeps its focus
        scenes = planned(real_lesson(), SETTINGS)
        self.assertEqual(scenes[AT["formula"]]["cinematic_plan"]["camera"]["movement"], "focus")
        scenes[AT["formula"]]["cinematic_plan"]["camera"]["movement"] = "slow_zoom_out"
        f = of(check(scenes, SETTINGS), "timing.camera_reading", AT["formula"])
        self.assertEqual(f[0]["repair"]["action"]["overrides"], {"camera": "focus"})


class MotionRulesTest(StrictTestCase):
    def test_moving_entrances_without_motion(self):
        none = {**SETTINGS, "motion": "none"}
        scenes = planned(real_lesson(), none)
        scenes[2]["cinematic_plan"]["motion"] = "subtle"
        T._layer(scenes[5]["cinematic_plan"], "visual")["enter"] = "scale_in"
        f = {x["scene"]: x for x in of(check(scenes, none), "timing.motion_not_still")}
        self.assertEqual(set(f), {2, 5})
        self.assertEqual(f[5]["evidence"]["layers"], ["visual"])
        for x in f.values():
            self.assertEqual((x["severity"], x["repair"]["kind"], x["repair"]["class"]), ("error", "auto", "presentation"))

    def test_the_same_layout_and_move_three_times(self):
        scenes = planned(real_lesson(), SETTINGS)
        self.assertEqual([(scenes[i]["cinematic_plan"]["template"], scenes[i]["cinematic_plan"]["camera"]["movement"]) for i in (1, 2)],
                         [("presenter_explanation", "slow_zoom_in")] * 2)
        plan = scenes[3]["cinematic_plan"]
        plan["template"], plan["camera"]["movement"] = "presenter_explanation", "slow_zoom_in"
        f = of(check(scenes, SETTINGS), "timing.motion_repetition")
        self.assertEqual([(x["scene"], x["severity"]) for x in f], [(3, "notice")])
        # the same with the layout chosen for that scene: information
        scenes[3]["visual_review"] = {"composition": {"status": "changed", "overrides": {"template": "presenter_explanation"}}}
        self.assertEqual([x["severity"] for x in of(check(scenes, SETTINGS), "timing.motion_repetition")], ["info"])

    def test_a_scene_s_own_motion_level_is_information(self):
        scenes = real_lesson()
        scenes[2]["visual_review"] = {"composition": {"status": "changed", "overrides": {"motion": "none"}}}
        found = check(planned(scenes, SETTINGS), SETTINGS)
        f = of(found, "timing.motion_choice", 2)
        self.assertEqual([(x["severity"], x["evidence"]["value"]) for x in f], [("info", "none")])
        self.assertEqual(problems(found), [])


class DensityRulesTest(StrictTestCase):
    def test_an_overloaded_scene(self):
        scenes = cine_lesson()
        scenes[1]["composition"] = {"labels": ["Sunlight", "Chlorophyll", "Glucose", "Oxygen"]}
        lesson = planned(scenes, CINE)
        self.assertEqual({k: v for k, v in T._shown(lesson[1]["cinematic_plan"]).items() if k != "presenter"},
                         {"board": True, "visual": True, "full_canvas": False, "labels": 4})
        f = of(check(lesson, CINE), "timing.density_overloaded", 1)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["dimension"]), ("warning", "density"))
        self.assertIn("shows a lot at once", f[0]["message"])
        self.assertEqual((f[0]["repair"]["kind"], f[0]["repair"]["class"], f[0]["repair"]["action"]["type"]), ("suggest", "composition", "composition"))
        overrides = f[0]["repair"]["action"]["overrides"]
        C.check_overrides(overrides)  # a change Visual Review accepts
        self.assertIn(overrides, ({"presenter_size": "small"}, {"visual_size": "side_panel"}, {"presenter_size": "hidden"}))

    def test_a_dense_board_beside_a_large_presenter_and_two_dense_scenes_in_a_row(self):
        board = "<p>" + " ".join(["Photosynthesis converts light energy into chemical energy stored in glucose."] * 10) + "</p>"
        narration = " ".join(["Plants turn light into sugar in their leaves every single day."] * 12)
        dense = [{"type": "content", "title": f"Dense {n}", "html": board, "narration": narration, "presenter_plan": teacher()} for n in range(2)]
        lesson = planned([vd_lesson()[0]] + dense, SETTINGS)
        found = check(lesson, SETTINGS)
        f = {x["scene"]: x for x in of(found, "timing.density_overloaded")}
        self.assertEqual(set(f), {1, 2})
        self.assertEqual(f[1]["repair"]["action"]["overrides"], {"presenter_size": "small"})
        pair = of(found, "timing.density_consecutive")
        self.assertEqual([(x["scene"], x["severity"], x["evidence"]["count"]) for x in pair], [(2, "notice", 2)])
        # a dense board on its own (the presenter away) is the composer's answer to density, not a busy scene
        code = "<pre><code>" + "\n".join(f"total = total + value_{n}  # add the next value" for n in range(22)) + "</code></pre>"
        lesson = planned([vd_lesson()[0], {**vd_lesson()[4], "html": code, "narration": narration}], SETTINGS)
        self.assertEqual(lesson[1]["cinematic_plan"]["composition"]["intent"]["density"], "high")
        self.assertEqual(of(check(lesson, SETTINGS), "timing.density_overloaded"), [])

    def test_almost_nothing_on_screen_for_a_long_narration(self):
        narration = " ".join(["word"] * 70) + "."
        lesson = planned([vd_lesson()[0], {"type": "content", "title": "Pause", "html": "<p>Think.</p>", "narration": narration,
                                            "presenter_plan": teacher()}], SETTINGS)
        f = of(check(lesson, SETTINGS), "timing.density_sparse", 1)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["repair"]["kind"]), ("notice", "none"))
        # with a picture beside it, the scene is not empty
        lesson[1]["cinematic_plan"]["layers"].append({"id": "visual", "type": "visual", "box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3}})
        self.assertEqual(of(check(lesson, SETTINGS), "timing.density_sparse"), [])


class ContractTest(unittest.TestCase):
    def crafted(self):
        """One lesson with a problem for most rules (built once per test, deterministically)."""
        none = {**SETTINGS, "motion": "none"}
        scenes = real_lesson()
        scenes[AT["comparison"]]["composition"] = {"transition": "wipe"}
        scenes[AT["example"]]["visual_review"] = {"composition": {"status": "changed", "overrides": {"transition": "crossfade"}}}
        lesson = planned(scenes, none)
        lesson[AT["formula"]]["cinematic_plan"]["sync"]["events"][0]["estimate"] = 99.0
        lesson[AT["explanation"]]["cinematic_plan"]["camera"]["movement"] = "slow_zoom_in"
        lesson[AT["code"]]["cinematic_plan"]["transition"] = {"in": "slide", "out": "fade", "duration": 0.55}
        lesson[AT["process"]]["cinematic_plan"]["duration"] = 40.0
        lesson[AT["diagram"]]["cinematic_plan"]["sync"]["version"] = 99
        lesson[AT["timeline"]]["narration"] += " " + "This sentence is long on purpose, " * 5 + "and it ends here."
        return lesson, none

    def test_deterministic(self):
        lesson, settings = self.crafted()
        first, second = check(copy.deepcopy(lesson), settings), check(copy.deepcopy(lesson), settings)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(len({f["id"] for f in first}), len(first))  # one finding per problem
        rules = {f["rule"] for f in first}
        for rule in ("timing.event_after_end", "timing.camera_motion_off", "timing.moving_transition", "timing.duration_mismatch",
                     "timing.sync_invalid", "timing.caption_long", "timing.transition_choice"):
            self.assertIn(rule, rules)
        Q._SCENE_MEMO.clear()
        a = Q.evaluate_lesson(copy.deepcopy(lesson), settings)
        Q._SCENE_MEMO.clear()
        b = Q.evaluate_lesson(copy.deepcopy(lesson), settings)
        c = Q.evaluate_lesson(copy.deepcopy(lesson), settings)  # the per-scene findings reused
        ids = [[f["id"] for f in r["issues"]] for r in (a, b, c)]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[1], ids[2])
        self.assertEqual(a["registry"], b["registry"])

    def test_read_only(self):
        lesson, settings = self.crafted()
        before, settings_before = copy.deepcopy(lesson), copy.deepcopy(settings)
        check(lesson, settings)
        T.registry(Q.Lesson(lesson, settings))
        Q.evaluate_lesson(lesson, settings)
        self.assertEqual(lesson, before)
        self.assertEqual(settings, settings_before)

    def test_a_moment_edited_without_a_new_fingerprint_is_still_seen(self):
        # the synchronization's moments are read by the lesson checks (run every time), never from the per-scene memo
        scenes = planned(real_lesson(), SETTINGS)
        clean = Q.evaluate_lesson(scenes, SETTINGS)
        self.assertFalse([f for f in clean["issues"] if f["rule"] == "timing.event_after_end"])
        scenes[AT["formula"]]["cinematic_plan"]["sync"]["events"][0]["estimate"] = 99.0
        again = Q.evaluate_lesson(scenes, SETTINGS)
        self.assertEqual([f["scene"] for f in again["issues"] if f["rule"] == "timing.event_after_end"], [AT["formula"]])

    def test_findings_follow_the_contract(self):
        lesson, settings = self.crafted()
        found = check(lesson, settings)
        extra = cine_lesson()
        extra[1]["composition"] = {"labels": ["Sunlight", "Chlorophyll", "Glucose", "Oxygen"]}
        found += check(planned(extra, CINE), CINE)
        self.assertTrue(found)
        for f in found:
            self.assertTrue(f["rule"].startswith("timing."), f["rule"])
            self.assertIn(f["dimension"], ("timing", "camera", "motion", "density"))
            self.assertRegex(f["message"], r"^(Scene \d+|Scenes \d+–\d+|This lesson)")
            if isinstance(f["scene"], int):
                self.assertIn(f"Scene{'s' if f['message'].startswith('Scenes') else ''} {f['scene'] + 1}", f["message"])
            self.assertFalse(re.search(r"_|\bsync|\blayer|timeline|\bevent|template|estimate|fingerprint|\bNone\b|token", f["message"], re.I),
                             f["message"])
            json.dumps(f["evidence"])
            rep = f["repair"]
            if rep["kind"] == "auto":
                self.assertIn(rep["class"], ("presentation", "timing"))
                self.assertEqual(rep["action"], {"type": "replan", "scenes": [f["scene"]]})
            elif rep["kind"] == "suggest":
                self.assertEqual(rep["class"], "composition")
                C.check_overrides(rep["action"]["overrides"])
            else:
                self.assertEqual(rep["action"], {"type": "none"})
            if f["severity"] == "info":
                self.assertEqual(rep["kind"], "none")

    def test_odd_data_never_crashes(self):
        odd = [None, "scene", {"cinematic_plan": "x"},
               {"type": "content", "narration": 12345, "html": 7,
                "cinematic_plan": {"duration": "long", "motion": 3, "camera": "zoom", "transition": 5, "layers": [None, 4, {"id": "labels", "items": "x"}],
                                   "timeline": [None, 3, {"at": "soon"}], "sync": {"version": 1, "events": "x", "end_hold": 0}}},
               {"type": "content", "narration": "Hello. [PAUSE:abc] [SYNC] There.", "visual_review": {"composition": "x"},
                "composition": {"transition": ["slide"], "camera": {"x": 1}},
                "cinematic_plan": {"duration": -1, "template": "nope", "camera": {"movement": "fly", "start": "x"},
                                   "transition": {"in": "slide", "out": None, "duration": "x"},
                                   "layers": [{"id": "visual", "role": None, "start": "x", "enter": 5}],
                                   "sync": {"version": 1, "events": [None, 5, {"type": "camera_focus", "at": "x", "estimate": "x"}], "end_hold": 0.2}}},
               {"type": "content", "narration": "Fine.", "cinematic_plan": {"sync": {"version": 1, "events": [
                   {"id": "a", "type": "label_enter", "target": {"layer": "labels", "item": True}, "at": {"segment": 0, "ratio": 0.5},
                    "estimate": 0.2, "duration": 1, "anchor": {"kind": "segment"}, "depends_on": []}], "end_hold": 0.0}}}]
        for settings in ({**SETTINGS, "motion": "none", "transitions": "zoom"}, SETTINGS, {"mode": "cinematic", "motion": 5, "transitions": None}, {}):
            lesson = Q.Lesson(copy.deepcopy(odd), settings)
            found = [f for i in range(lesson.count) for f in T.scene_checks(lesson, i)] + T.lesson_checks(lesson)
            self.assertIsInstance(found, list)
            self.assertIsInstance(T.registry(lesson), dict)
            report = Q.evaluate_lesson(copy.deepcopy(odd), settings)
            self.assertFalse([x for x in report["limitations"] if x.startswith("quality_timing")], report["limitations"])
        self.assertEqual(T.scene_checks(Q.Lesson([], SETTINGS), 0), [])
        self.assertEqual(T.lesson_checks(Q.Lesson([], SETTINGS)), [])

    def test_fast_for_a_long_lesson(self):
        scenes = planned((real_lesson() * 3)[:30], SETTINGS)
        started = time.perf_counter()
        lesson = Q.Lesson(scenes, SETTINGS)
        [T.scene_checks(lesson, i) for i in range(lesson.count)]
        T.lesson_checks(lesson)
        T.registry(lesson)
        self.assertLess(time.perf_counter() - started, 0.3)


if __name__ == "__main__":
    unittest.main()
