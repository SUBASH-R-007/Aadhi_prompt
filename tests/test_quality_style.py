"""Phase 18 — the style / typography / colour / background checks (quality_style.py): the saved look against what Phase 17
resolves now (stale, changed outside the planner, missing, mixed styles and versions, the original look mixed with a
chosen style), scene style choices (intentional: info) and many scene accents, readability adjustments and the contrast
guard (code keywords on the code panel), plan validity mapped to dimensions, board text fit at the page's and the
style's text size, dense boards, title casing, heading levels, backgrounds (fallback, unexplained changes, explicit
choices, pictures behind a light style), the registry, determinism, no mutation and robustness.

Run: PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_quality_style.py"
"""
import copy
import json
import time
import unittest
from unittest import mock

from backend_env import assert_isolated  # noqa: F401  first: throwaway database

import cinematic as C  # noqa: E402
import composer as K  # noqa: E402
import quality as Q  # noqa: E402
import quality_style as QS  # noqa: E402
import styles as S  # noqa: E402
from test_cinematic import CINE, lesson  # noqa: E402
from test_sync_director import real_lesson  # noqa: E402
from test_visual_director import SETTINGS  # noqa: E402

STYLES = (None,) + S.FAMILIES  # None: a lesson that chose no style (the original look)
IMAGE = "a" * 32


def setUpModule():
    assert_isolated()


def styled(base, family=None, **overrides):
    settings = dict(base)
    if family:
        settings["style"] = family
    if overrides:
        settings["style_overrides"] = overrides
    return settings


def planned(scenes, settings, resolve=lambda _id: None):
    scenes = copy.deepcopy(scenes)
    for scene, plan in zip(scenes, C.compose_lesson(copy.deepcopy(scenes), settings, resolve)):
        if plan:
            scene["cinematic_plan"] = plan
    return scenes


def findings(scenes, settings):
    """This family's findings for a lesson (no memo: each call evaluates afresh)."""
    lesson = Q.Lesson(scenes, settings)
    out = []
    for i in range(lesson.count):
        out += QS.scene_checks(lesson, i)
    return out + QS.lesson_checks(lesson)


def only(found, rule):
    return [f for f in found if f["rule"] == rule]


def problems(found):
    return [(f["rule"], f["severity"], f["scene"]) for f in found if f["severity"] != "info"]


def layer(plan, layer_id):
    return next(l for l in plan["layers"] if l["id"] == layer_id)


def reviewed(scene, **overrides):
    scene["visual_review"] = {"composition": {"status": "changed", "overrides": overrides}}
    return scene


def picture(asset_id):
    return {"kind": "image", "url": "/assets/x.png", "width": 1600, "height": 900} if asset_id == IMAGE else None


class CleanLessonTest(unittest.TestCase):
    def test_clean_lessons_in_every_style_have_no_findings(self):
        for name, make, base in (("cinematic fixture", lesson, CINE), ("representative lesson", real_lesson, SETTINGS)):
            for family in STYLES:
                settings = styled(base, family)
                with self.subTest(lesson=name, style=family):
                    self.assertEqual(findings(planned(make(), settings), settings), [])

    def test_bounded_choices_stay_clean(self):
        # Phase 17's override example: Corporate Training with accent teal, large text and a plain background
        for family in S.FAMILIES:
            settings = styled(SETTINGS, family, accent="teal", text_size="large", background="plain")
            with self.subTest(style=family):
                self.assertEqual(problems(findings(planned(real_lesson(), settings), settings)), [])

    def test_the_family_is_part_of_the_report(self):
        settings = styled(SETTINGS, "academic")
        report = Q.evaluate_lesson(planned(real_lesson(), settings), settings)
        self.assertIn("quality_style", report["families"])
        self.assertEqual([f for f in report["issues"] if f["rule"].startswith("style.")], [])
        self.assertEqual(report["registry"]["style"]["id"], "academic@1")


class LookTest(unittest.TestCase):
    def test_a_tampered_look_css_is_an_error_with_an_automatic_replan(self):
        settings = styled(CINE, "academic")
        scenes = planned(lesson(), settings)
        scenes[2]["cinematic_plan"]["style"]["look"]["css"]["--st-text"] = "#000000"
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.look_css", "error", 2)])
        f = found[0]
        self.assertEqual((f["dimension"], f["repair"]["kind"], f["repair"]["class"]), ("style", "auto", "presentation"))
        self.assertEqual(f["repair"]["action"], {"type": "replan", "scenes": [2]})
        self.assertIn("Scene 3 (“The leaf up close”)", f["message"])
        # through the report too (the look's CSS is not part of the scene fingerprint: checked every time, never memoized)
        clean = planned(lesson(), settings)
        Q.evaluate_lesson(clean, settings)
        report = Q.evaluate_lesson(scenes, settings)
        self.assertEqual([(f["rule"], f["scene"]) for f in report["issues"] if f["rule"].startswith("style.")], [("style.look_css", 2)])

    def test_a_tampered_identity_or_scene_choice_made_after_planning_is_stale(self):
        settings = styled(CINE, "corporate_training")
        scenes = planned(lesson(), settings)
        scenes[3]["cinematic_plan"]["style"]["look"]["fingerprint"] = "0" * 16
        reviewed(scenes[1], style_accent="teal")  # chosen in Visual Review, the scene not re-planned since
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.stale_look", "error", 1), ("style.stale_look", "error", 3)])
        self.assertEqual([f["repair"]["action"]["scenes"] for f in only(found, "style.stale_look")], [[1], [3]])
        self.assertEqual([(f["rule"], f["severity"], f["scene"]) for f in only(found, "style.scene_style")], [("style.scene_style", "info", 1)])

    def test_a_lesson_style_changed_after_planning_is_one_lesson_finding(self):
        scenes = planned(lesson(), styled(CINE, "academic"))
        found = findings(scenes, styled(CINE, "corporate_training"))
        self.assertEqual(problems(found), [("style.stale_look", "error", None)])
        self.assertEqual(found[0]["repair"]["action"], {"type": "replan", "scenes": list(range(len(scenes)))})
        self.assertIn("Every scene", found[0]["message"])

    def test_mixed_styles_replan_the_scenes_that_differ(self):
        settings = styled(CINE, "corporate_training")
        scenes = planned(lesson(), styled(CINE, "academic"))
        newer = planned(lesson(), settings)
        for i in (0, 1, 2):
            scenes[i]["cinematic_plan"] = newer[i]["cinematic_plan"]
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.mixed_styles", "error", None)])
        f = found[0]
        self.assertEqual(f["repair"]["action"]["scenes"], [3, 4, 5, 6, 7])
        self.assertEqual(f["repair"]["kind"], "auto")
        self.assertIn("“Academic”", f["message"])
        self.assertIn("“Corporate Training”", f["message"])
        self.assertEqual(only(found, "style.stale_look"), [], "the mixed scenes are reported once, not again one by one")

    def test_mixed_versions(self):
        settings = styled(CINE, "academic")
        scenes = planned(lesson(), settings)
        look = scenes[4]["cinematic_plan"]["style"]["look"]
        look.update(id="academic@0", version=0)
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.mixed_styles", "error", None)])
        self.assertEqual(found[0]["repair"]["action"]["scenes"], [4])
        self.assertIn("version", found[0]["message"])

    def test_the_original_look_mixed_with_a_chosen_style(self):
        settings = styled(CINE, "cinematic_education")
        scenes = planned(lesson(), CINE)  # no style chosen: the original look (same id, legacy)
        chosen = planned(lesson(), settings)
        for i in (0, 1):
            scenes[i]["cinematic_plan"] = chosen[i]["cinematic_plan"]
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.legacy_mix", "error", None)])
        self.assertEqual(found[0]["repair"]["action"]["scenes"], [2, 3, 4, 5, 6, 7])
        self.assertIn("original look", found[0]["message"])

    def test_a_plan_missing_its_look(self):
        settings = styled(CINE, "academic")
        scenes = planned(lesson(), settings)
        del scenes[3]["cinematic_plan"]["style"]["look"]
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.missing_look", "error", 3)])
        self.assertEqual(found[0]["repair"]["action"], {"type": "replan", "scenes": [3]})
        # plans made before styles existed: silent while the lesson keeps its original look, one finding once a style is chosen
        old = planned(lesson(), CINE)
        for scene in old:
            del scene["cinematic_plan"]["style"]["look"]
        self.assertEqual(findings(old, CINE), [])
        found = findings(old, settings)
        self.assertEqual(problems(found), [("style.missing_look", "error", None)])
        self.assertEqual(found[0]["repair"]["action"]["scenes"], list(range(len(old))))


class SceneStyleTest(unittest.TestCase):
    def test_a_scene_accent_chosen_in_visual_review_is_info(self):
        settings = styled(SETTINGS, "academic")
        scenes = real_lesson()
        reviewed(scenes[2], style_accent="teal", style_background="plain")
        found = findings(planned(scenes, settings), settings)
        self.assertEqual(problems(found), [])
        info = only(found, "style.scene_style")
        self.assertEqual([(f["severity"], f["scene"]) for f in info], [("info", 2)])
        self.assertEqual((info[0]["evidence"]["accent"], info[0]["evidence"]["background"]), ("teal", "plain"))
        self.assertEqual(info[0]["repair"]["kind"], "none")

    def test_many_different_scene_accents_are_a_notice_with_a_suggestion(self):
        settings = styled(SETTINGS, "corporate_training")
        scenes = real_lesson()
        for i, accent in ((1, "teal"), (3, "coral"), (6, "violet")):
            reviewed(scenes[i], style_accent=accent)
        found = findings(planned(scenes, settings), settings)
        notices = only(found, "style.many_accents")
        self.assertEqual([(f["severity"], f["scene"], f["dimension"]) for f in notices],
                         [("notice", 1, "colour"), ("notice", 3, "colour"), ("notice", 6, "colour")])
        for f in notices:
            self.assertEqual(f["repair"]["kind"], "suggest")
            self.assertEqual(f["repair"]["class"], "presentation")
            self.assertEqual(f["repair"]["action"], {"type": "scene_style", "scene": f["scene"], "overrides": {"style_accent": "auto"}})
        self.assertEqual(len(only(found, "style.scene_style")), 3)
        # the same accent on three scenes is one deliberate highlight, not many
        same = real_lesson()
        for i in (1, 3, 6):
            reviewed(same[i], style_accent="teal")
        self.assertEqual(only(findings(planned(same, settings), settings), "style.many_accents"), [])


class ColourTest(unittest.TestCase):
    def test_readability_adjustments_are_a_notice_in_plain_words(self):
        with mock.patch.dict(S.FAMILY_DEFS["academic"][1]["tokens"], {"heading": "#f4efe3"}):  # ivory headings on white
            settings = styled(CINE, "academic")
            found = findings(planned(lesson(), settings), settings)
        self.assertEqual(problems(found), [("style.readability_adjusted", "notice", None)])
        self.assertIn("headings made darker or lighter to stay readable", found[0]["message"])
        self.assertEqual(found[0]["dimension"], "colour")

    def test_code_keywords_must_read_on_the_code_panel(self):
        # the Phase 17 regression: dark crimson keywords on the dark code panel (1.9:1). The style's guard is switched off
        # while it resolves (planning and the comparison alike): the style itself gives the colour, so re-planning cannot
        # help and no repair is offered
        with self.unguarded(**{"code-accent": "#9b2335"}):
            settings = styled(CINE, "academic")
            found = findings(planned(lesson(), settings), settings)
        self.assertEqual(problems(found), [("style.low_contrast", "error", 4)], "only the scene that shows code")
        f = found[0]
        self.assertEqual((f["dimension"], f["evidence"]["target"], f["evidence"]["stale"]), ("code", "code-accent", False))
        self.assertEqual((f["repair"]["kind"], f["repair_status"]), ("none", "not_repairable"))
        self.assertIn("highlighted words in code", f["message"])
        self.assertIn("re-planning cannot help", f["message"])

    def test_unreadable_saved_colours_that_are_stale_offer_a_replan(self):
        with self.unguarded(**{"code-accent": "#9b2335"}):
            scenes = planned(lesson(), styled(CINE, "academic"))
        found = findings(scenes, styled(CINE, "academic"))  # the style now gives readable colours
        low = only(found, "style.low_contrast")
        self.assertEqual([(f["scene"], f["evidence"]["stale"]) for f in low], [(4, True)])
        self.assertEqual((low[0]["repair"]["kind"], low[0]["repair"]["action"]), ("auto", {"type": "replan", "scenes": [4]}))
        self.assertEqual(problems(only(found, "style.look_css")), [("style.look_css", "error", None)])

    def test_body_text_failing_everywhere_is_one_lesson_finding(self):
        with self.unguarded(text="#dddddd"):
            settings = styled(CINE, "academic")
            found = findings(planned(lesson(), settings), settings)
        low = only(found, "style.low_contrast")
        self.assertEqual([(f["scene"], f["evidence"]["target"], f["dimension"], f["repair"]["kind"]) for f in low],
                         [(None, "text", "colour", "none")])
        self.assertIn("body text", low[0]["message"])

    def test_every_builtin_style_and_accent_reads(self):
        for family in S.FAMILIES:
            for accent in ("default",) + tuple(S.ACCENTS):
                for background in S.BACKGROUND_DENSITY:
                    look = S.plan_look(styled({}, family, accent=accent, background=background))
                    with self.subTest(style=family, accent=accent, background=background):
                        self.assertEqual(QS.unreadable(look["css"], look["tone"]), ((), []))
        for typography in ("academic", "modern"):
            look = S.plan_look({"typography": typography})
            self.assertEqual(QS.unreadable(look["css"], look["tone"]), ((), []))

    def unguarded(self, **tokens):
        """Academic with these token values and the style's readability guard switched off while it resolves (the check
        still uses the real guard: quality_style bound it at import)."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(mock.patch.object(S, "_accessibility", return_value=[]))
        stack.enter_context(mock.patch.dict(S.FAMILY_DEFS["academic"][1]["tokens"], tokens))
        return stack


class PlanValidityTest(unittest.TestCase):
    def setUp(self):
        self.settings = styled(CINE, "academic")
        self.scenes = planned(lesson(), self.settings)

    def broken(self, i, change):
        scenes = copy.deepcopy(self.scenes)
        change(scenes[i]["cinematic_plan"])
        found = only(findings(scenes, self.settings), "style.invalid_plan")
        for f in found:
            self.assertEqual((f["severity"], f["repair"]["kind"], f["repair"]["action"]), ("error", "auto", {"type": "replan", "scenes": [i]}))
        return [(f["dimension"], f["scene"]) for f in found]

    def test_text_in_the_subtitle_band_is_typography(self):
        self.assertEqual(self.broken(0, lambda p: layer(p, "board")["box"].update(y=0.78)), [("typography", 0)])

    def test_a_camera_that_zooms_too_far_is_camera(self):
        def zoom(p):
            p["camera"].update(movement="slow_zoom_in", **{"to": {"x": 0.2, "y": 0.2, "w": 0.5}})
        self.assertEqual(self.broken(1, zoom), [("camera", 1)])

    def test_motion_timing_and_damage(self):
        self.assertEqual(self.broken(2, lambda p: p["transition"].update({"in": "spin"})), [("motion", 2)])
        self.assertEqual(self.broken(3, lambda p: p.update(duration=0)), [("timing", 3)])
        self.assertEqual(self.broken(4, lambda p: p.update(template="mystery")), [("preview_export", 4)])
        self.assertEqual(self.broken(5, lambda p: p.update(layers=[1, 2])), [("preview_export", 5)])

    def test_a_presenter_over_the_content(self):
        def cover(p):
            layer(p, "presenter")["box"].update(x=layer(p, "board")["box"]["x"])
        self.assertIn(("typography", 0), self.broken(0, cover))


class TypographyTest(unittest.TestCase):
    def words(self, n):
        return "<p>" + " ".join(["leaves"] * n) + "</p>"

    def fit(self, scene, scale=1.0):
        board = layer(scene["cinematic_plan"], "board")
        facts = Q.Lesson([scene], {}).facts(0)
        style = board["role"] if board["role"] in C.BOARD_STYLES else C.TEMPLATES[scene["cinematic_plan"]["template"]]["board"]
        needed, available, _ws = K.text_need({"type": scene["type"], "board": facts}, board["box"], style)
        return available / (needed * scale)

    def test_too_much_text_with_a_presenter_suggests_a_smaller_presenter(self):
        settings = styled(SETTINGS, "academic")
        scenes = planned(real_lesson(), settings)
        scenes[2]["html"] = self.words(400)  # the board's text grew after the scene was planned
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.text_overflow", "warning", 2)])
        f = found[0]
        self.assertEqual((f["dimension"], f["repair"]["kind"], f["repair"]["class"]), ("typography", "suggest", "composition"))
        self.assertEqual(f["repair"]["action"], {"type": "composition", "scene": 2, "overrides": {"presenter_size": "small"}})
        self.assertLess(f["evidence"]["fit"], K.MIN_TEXT_SCALE)

    def test_too_much_text_with_nothing_to_move_asks_to_split_the_scene(self):
        settings = styled(CINE, "corporate_training")
        scenes = planned(lesson(), settings)  # scene 5: the code scene, no presenter, no picture
        scenes[4]["html"] = "<pre><code>" + "\n".join(f"print({n})" for n in range(80)) + "</code></pre>"
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.text_overflow", "warning", 4)])
        self.assertEqual(found[0]["repair"]["kind"], "none")
        self.assertIn("split the scene", found[0]["message"])

    def test_text_that_fits_at_the_page_size_but_not_at_the_style_size(self):
        settings = styled(SETTINGS, "children_education", text_size="larger")  # 1.06 x 1.2
        scale = float(S.resolve(settings)["tokens"]["text-scale"])
        scenes = planned(real_lesson(), settings)
        n = next(n for n in range(10, 600, 2) if 0.86 <= self.fit(dict(scenes[2], html=self.words(n))) < 1.0)
        scenes[2]["html"] = self.words(n)
        self.assertLess(self.fit(scenes[2], scale), K.MIN_TEXT_SCALE)
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.tiny_text", "warning", 2)])
        self.assertEqual(found[0]["evidence"]["text_scale"], scale)
        self.assertEqual(found[0]["repair"]["action"]["overrides"], {"presenter_size": "small"})
        # the same board at the style's standard text size fits (it is only a full board)
        standard = styled(SETTINGS, "children_education")
        scenes = planned(real_lesson(), standard)
        scenes[2]["html"] = self.words(n)
        rules = {f["rule"] for f in findings(scenes, standard)}
        self.assertFalse(rules & {"style.tiny_text", "style.text_overflow"}, rules)

    def test_a_wall_of_words_is_a_notice(self):
        settings = styled(SETTINGS, "academic")
        scenes = planned(real_lesson(), settings)
        board = layer(scenes[2]["cinematic_plan"], "board")["box"]
        n = next(n for n in range(40, 600) if n / (board["w"] * board["h"]) >= QS.DENSE_PER_AREA)
        scenes[2]["html"] = self.words(n)
        self.assertGreaterEqual(self.fit(scenes[2]), K.MIN_TEXT_SCALE)
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.dense_board", "notice", 2)])
        self.assertEqual(found[0]["dimension"], "density")

    def test_title_casing_mixed_across_the_lesson(self):
        titles = ["Inside The Leaf", "How Plants Make Food", "Light And Water Together", "The sun gives energy",
                  "WHY LEAVES ARE GREEN", "Photosynthesis", "DNA and RNA"]
        scenes = [{"type": "content", "title": t, "html": "<p>x</p>"} for t in titles]
        found = findings(scenes, {"mode": "classic"})
        self.assertEqual([(f["rule"], f["severity"], f["scene"], f["evidence"]["found"]) for f in found],
                         [("style.title_casing", "notice", 3, "sentence"), ("style.title_casing", "notice", 4, "upper")])
        self.assertEqual(found[0]["repair"]["kind"], "none")
        self.assertIn("capitalises only its first word", found[0]["message"])
        for title, expected in (("Newton's second law", "sentence"), ("Plants vs Animals", None), ("Worked example: a falling ball", "sentence"),
                                ("Inside the Leaf", None), ("The Water Cycle In Nature", "title"), ("DNA", None), ("", None),
                                ("Using the iPhone camera", "sentence"), (None, None), (42, None)):
            with self.subTest(title=title):
                self.assertEqual(QS.title_casing(title), expected)

    def test_heading_levels_differ_between_scenes_of_the_same_kind(self):
        scenes = [{"type": "content", "title": "a", "html": "<h2>One</h2><p>x</p>"},
                  {"type": "content", "title": "b", "html": "<h2>Two</h2><h3>Part</h3>"},
                  {"type": "content", "title": "c", "html": "<h3>Three</h3><p>x</p>"},
                  {"type": "example", "title": "d", "html": "<h4>Example</h4>"},
                  {"type": "content", "title": "e", "html": "<p>no heading</p>"}]
        found = findings(scenes, {"mode": "classic"})
        self.assertEqual([(f["rule"], f["scene"], f["evidence"]["expected"], f["evidence"]["found"]) for f in found],
                         [("style.heading_levels", 2, "h2", "h3")])
        self.assertIn("smaller heading", found[0]["message"])


class BackgroundTest(unittest.TestCase):
    def test_a_lesson_background_that_fell_back_everywhere_is_one_warning(self):
        settings = styled(CINE, "academic") | {"background": "image", "background_asset_id": "c" * 32}
        found = findings(planned(lesson(), settings), settings)
        self.assertEqual(problems(found), [("style.background_fallback", "warning", None)])
        f = found[0]
        self.assertEqual((f["dimension"], f["repair"]["kind"]), ("background", "none"))
        self.assertEqual(f["evidence"]["scenes"], [1, 2, 3, 4, 5, 6, 8], "every scene but the full-canvas one")
        self.assertEqual(f["evidence"]["wanted"], "image")

    def test_a_scene_background_that_fell_back_is_a_warning_on_that_scene(self):
        settings = styled(CINE, "academic")
        scenes = lesson()
        scenes[2]["composition"]["background"] = {"type": "image", "asset_id": "d" * 32}  # not in the library
        found = findings(planned(scenes, settings, picture), settings)
        self.assertEqual(problems(found), [("style.background_fallback", "warning", 2)])
        self.assertEqual((found[0]["evidence"]["wanted"], found[0]["evidence"]["from"]), ("image", "scene"))

    def test_an_explicit_background_choice_is_info(self):
        settings = styled(CINE, "corporate_training")
        scenes = lesson()
        scenes[2]["composition"]["background"] = "solid"
        found = findings(planned(scenes, settings), settings)
        self.assertEqual(problems(found), [])
        self.assertEqual([(f["rule"], f["scene"]) for f in found], [("style.background_choice", 2)])

    def test_an_unexplained_background_change_is_one_notice(self):
        settings = styled(CINE, "corporate_training")
        scenes = planned(lesson(), settings)
        scenes[2]["cinematic_plan"]["background"] = {"type": "solid", "palette": "corporate_training", "color": "#111a2e"}
        found = findings(scenes, settings)
        self.assertEqual(problems(found), [("style.background_change", "notice", 2)], "the lone odd scene, not the way back")
        self.assertIn("a plain colour", found[0]["message"])

    def test_a_picture_behind_a_light_style_is_info(self):
        settings = styled(CINE, "academic")
        scenes = lesson()
        scenes[1]["composition"]["background"] = {"type": "image", "asset_id": IMAGE}
        found = findings(planned(scenes, settings, picture), settings)
        self.assertEqual(problems(found), [])
        self.assertEqual(sorted((f["rule"], f["scene"]) for f in found),
                         [("style.background_choice", 1), ("style.picture_background_light", 1)])
        dark = styled(CINE, "corporate_training")
        self.assertEqual(only(findings(planned(scenes, dark, picture), dark), "style.picture_background_light"), [])

    def test_aadhi_in_his_studio_explains_the_change(self):
        settings = styled(CINE, "cinematic_education")
        scenes = lesson()
        scenes[2]["presenter_plan"] = {"presenter_id": "aadhi", "type": "mascot", "enabled": True, "position": "right"}
        found = findings(planned(scenes, settings), settings)
        self.assertEqual(problems(found), [])
        self.assertEqual({f["rule"] for f in found}, {"style.background_presenter"})


class RegistryTest(unittest.TestCase):
    def test_registry_sections(self):
        settings = styled(CINE, "corporate_training", accent="teal", caption_size="large")
        scenes = lesson()
        reviewed(scenes[1], style_accent="coral")
        lesson_ = Q.Lesson(planned(scenes, settings), settings)
        reg = QS.registry(lesson_)
        self.assertEqual(set(reg), {"style", "captions", "background"})
        self.assertEqual(reg["style"], {"id": "corporate_training@1", "family": "corporate_training", "version": 1, "legacy": False,
                                        "tone": "dark", "accent": "teal", "scene_accents": {"1": "coral"}})
        self.assertEqual(reg["captions"], {"style": "shadow", "size": "large"})
        self.assertEqual(reg["background"], {"kinds": ["canvas", "gradient"], "counts": {"canvas": 1, "gradient": 7}})
        json.dumps(reg)
        legacy = QS.registry(Q.Lesson(lesson(), {"mode": "classic"}))
        self.assertEqual((legacy["style"]["id"], legacy["style"]["legacy"], legacy["background"]["kinds"]), ("cinematic_education@1", True, []))


class RobustnessTest(unittest.TestCase):
    def troubled(self):
        settings = styled(SETTINGS, "academic")
        scenes = real_lesson()
        for i, accent in ((1, "teal"), (3, "coral"), (6, "violet")):
            reviewed(scenes[i], style_accent=accent)
        scenes = planned(scenes, settings)
        scenes[0]["title"] = "WHY PLANTS MATTER"
        scenes[2]["html"] = "<p>" + "leaves " * 400 + "</p>"
        scenes[4]["cinematic_plan"]["style"]["look"]["css"]["--st-heading"] = "#ffffff"
        scenes[5]["cinematic_plan"]["background"] = {"type": "solid", "color": "#000000"}
        layer(scenes[7]["cinematic_plan"], "board")["box"]["y"] = 0.8
        return scenes, settings

    def test_deterministic_findings_and_ids(self):
        scenes, settings = self.troubled()
        first, second = findings(scenes, settings), findings(copy.deepcopy(scenes), dict(settings))
        self.assertEqual(first, second)
        self.assertGreaterEqual(len({f["rule"] for f in first}), 6)
        self.assertEqual(len({f["id"] for f in first}), len(first), "one finding per problem")
        a = [f for f in Q.evaluate_lesson(scenes, settings)["issues"] if f["rule"].startswith("style.")]
        b = [f for f in Q.evaluate_lesson(copy.deepcopy(scenes), settings)["issues"] if f["rule"].startswith("style.")]
        self.assertEqual(a, b)

    def test_the_input_is_never_changed(self):
        scenes, settings = self.troubled()
        before, settings_before = copy.deepcopy(scenes), copy.deepcopy(settings)
        lesson_ = Q.Lesson(scenes, settings)
        for i in range(lesson_.count):
            QS.scene_checks(lesson_, i)
        QS.lesson_checks(lesson_)
        QS.registry(lesson_)
        Q.evaluate_lesson(scenes, settings)
        self.assertEqual(scenes, before)
        self.assertEqual(settings, settings_before)

    def test_odd_data_never_crashes(self):
        settings = styled(CINE, "academic")
        scenes = planned(lesson(), settings)
        scenes[0]["cinematic_plan"]["style"] = {"look": {"css": [1, 2], "id": None}}
        scenes[1]["cinematic_plan"]["background"] = "image"
        scenes[2]["cinematic_plan"]["layers"] = "x"
        scenes[3]["cinematic_plan"]["style"]["look"] = "academic"
        scenes[4]["cinematic_plan"]["presenter"] = None
        scenes[5]["title"] = 123
        scenes[6]["html"] = ["not", "html"]
        scenes[7]["visual_review"] = {"composition": {"status": "changed", "overrides": ["style_accent"]}}
        oddities = scenes + ["not a scene", None, {"cinematic_plan": {"layers": [None]}}]
        for s in (settings, {"mode": "cinematic", "style": "neon"}, {"mode": "cinematic", "style": ["x"], "typography": {}}, None):
            with self.subTest(settings=s):
                found = findings(oddities, s)
                self.assertTrue(all(isinstance(f, dict) and f["rule"].startswith("style.") for f in found))
                QS.registry(Q.Lesson(oddities, s))
        report = Q.evaluate_lesson(oddities, settings)
        self.assertFalse([l for l in report["limitations"] if l.startswith("quality_style")])

    def test_a_classic_lesson_has_no_plan_findings(self):
        settings = styled(CINE, "academic")
        scenes = planned(lesson(), settings)
        scenes[2]["cinematic_plan"]["style"]["look"]["css"]["--st-text"] = "#000000"
        self.assertEqual(findings(scenes, dict(settings, mode="classic")), [])

    def test_adversarial_text_is_checked_in_linear_time(self):
        settings = styled(CINE, "academic")
        base = planned(lesson(), settings)
        texts = {"spaces between words": "a" + " " * 20000 + "b", "spaces": " " * 20000, "one long word": "a" * 20000,
                 "punctuation": "!?.:;" * 4000, "dashes": " - " * 7000, "colons after words": "word " * 2000 + ":" * 10000,
                 "capitals": "AB " * 7000, "headings": "<h2>x</h2>" * 2000}
        started = time.perf_counter()
        self.assertIsNone(QS.title_casing("a" + " " * 20000 + "b " * 5000))
        self.assertLess(time.perf_counter() - started, 0.2)
        for name, text in texts.items():
            for field in ("title", "html", "narration"):
                scenes = copy.deepcopy(base)
                scenes[1][field] = text
                lesson_ = Q.Lesson(scenes, settings)
                for check in (lambda: QS.scene_checks(lesson_, 1), lambda: QS.lesson_checks(lesson_), lambda: QS.registry(lesson_)):
                    started = time.perf_counter()
                    check()
                    with self.subTest(text=name, field=field):
                        self.assertLess(time.perf_counter() - started, 0.2)

    def test_thirty_scenes_are_checked_quickly(self):
        settings = styled(SETTINGS, "children_education")
        scenes = planned((real_lesson() * 3)[:30], settings)
        started = time.perf_counter()
        found = findings(scenes, settings)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(problems(found), [])


if __name__ == "__main__":
    unittest.main()
