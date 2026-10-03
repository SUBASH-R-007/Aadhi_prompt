"""Phase 18 — the presenter and media checks of the Quality & Consistency Engine (quality_media.py): who presents each
scene against the lesson's presenter (a switch is never repaired, a clip chosen in Visual Review is the user's choice),
side flips and size jumps between scenes, Aadhi's studio, an AI presenter's clip, a composition out of date with its
presenter plan; the media a scene shows (missing / deleted / not yours / file gone blocks the export, wrong kind, empty
file, wrong shape, AI without provenance, not generated yet, a failed run, several generators); the registry; and
load_media, the server's read-only lookup (one query for the assets, nothing marked, ownership honoured, no URL, no
prompt).

Run from the repo root:
  PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_quality_media.py"

No browser, no sound, no AI provider: the checks are pure data; the lookup uses the throwaway database of backend_env.
"""
import copy
import datetime
import json
import os
import time
import unittest
import uuid

from backend_env import assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

import assets  # noqa: E402
import cinematic as C  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import quality as Q  # noqa: E402
import quality_media as QM  # noqa: E402
import server  # noqa: E402
import styles  # noqa: E402
from test_cinematic import CINE, lesson, teacher  # noqa: E402
from test_sync_director import real_lesson  # noqa: E402
from test_visual_director import SETTINGS as DIRECTOR, WIDE  # noqa: E402

LEGACY = {"mode": "cinematic"}  # Aadhi as the lesson says (the default)
AI = {"mode": "cinematic", "presenter_legacy": False, "presenter_id": "ai-teacher"}
A1, A2, A3, A4 = ("a1" * 16, "a2" * 16, "a3" * 16, "a4" * 16)


def setUpModule():
    assert_isolated()


def planned(scenes=None, settings=CINE):
    scenes = lesson() if scenes is None else copy.deepcopy(scenes)
    for scene, plan in zip(scenes, C.compose_lesson(copy.deepcopy(scenes), settings)):
        if plan:
            scene["cinematic_plan"] = plan
    return scenes


def check(scenes, settings=CINE, media=None):
    lesson_ = Q.Lesson(scenes, settings, media=media)
    return [f for i in range(lesson_.count) for f in QM.scene_checks(lesson_, i)] + QM.lesson_checks(lesson_)


def rule(findings, name):
    return [f for f in findings if f["rule"] == name]


def problems(findings):
    return [(f["rule"], f["severity"], f["scene"]) for f in findings if f["severity"] != "info"]


def entry(kind="image", **extra):
    base = {"kind": kind, "status": "ready", "exists": True, "width": 1600 if kind == "image" else 1920,
            "height": 900 if kind == "image" else 1080, "file_size": 2048, "mime": "image/png" if kind == "image" else "video/mp4",
            "source": "upload", "provider": None, "model": None, "has_provenance": False, "accessible": True}
    return {**base, **extra}


def media(assets_=None, runs=None):
    return {"checked": True, "assets": dict(assets_ or {}), "runs": list(runs or [])}


def side_scene(plan, **extra):
    """A content scene with a side visual (no composition needed: the media checks read the visual plan)."""
    return {"type": "content", "title": "Inside the leaf", "html": "<p>Chloroplasts.</p>", "narration": "Look.",
            "presenter_plan": teacher(), "visual_plan": {"side": plan}, **extra}


def picture(asset_id=A1, **extra):
    return {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": asset_id, "selection": "matched", **extra}


class CleanLessonTest(unittest.TestCase):
    def test_the_clean_fixtures_have_no_problem_in_every_style(self):
        clean = media({WIDE: entry()})
        for name, scenes, base in (("cinematic", lesson(), CINE), ("director", real_lesson(), DIRECTOR), ("legacy", lesson(), LEGACY)):
            for family in (None,) + styles.FAMILIES:
                settings = dict(base, **({"style": family} if family else {}))
                composed = planned(scenes, settings)
                for looked_up in (None, clean):
                    with self.subTest(lesson=name, style=family, media=looked_up is not None):
                        self.assertEqual(problems(check(composed, settings, looked_up)), [])

    def test_a_classic_lesson_is_clean_too(self):
        for settings in ({**CINE, "mode": "classic"}, {"mode": "classic"}):
            self.assertEqual(problems(check(lesson(), settings, media())), [])

    def test_the_registry(self):
        scenes = planned()
        reg = QM.registry(Q.Lesson(scenes, CINE))
        self.assertEqual((reg["presenter"]["profile"], reg["presenter"]["type"]), ("aadhi-teacher", "illustrated"))
        self.assertEqual(reg["presenter"]["positions"], ["right", "right", "right", "right", "hidden", "right", "hidden", "hidden"])
        self.assertEqual(len(reg["presenter"]["roles"]), len(scenes))
        self.assertEqual(reg["presenter"]["roles"][4], "hidden")
        self.assertEqual(reg["presenter"]["switches"], [])
        self.assertEqual(reg["media"], {"sources": {}, "providers": [], "checked": False})
        legacy = QM.registry(Q.Lesson(planned(lesson(), LEGACY), LEGACY, media=media()))
        self.assertEqual((legacy["presenter"]["profile"], legacy["presenter"]["type"]), ("aadhi", "mascot"))
        self.assertTrue(legacy["media"]["checked"])
        json.dumps(reg)  # plain data


class PresenterTest(unittest.TestCase):
    def test_a_presenter_plan_for_another_presenter_is_an_unexpected_switch(self):
        scenes = lesson()
        scenes[1]["presenter_plan"] = teacher(presenter_id="ai-teacher", type="ai_avatar")
        found = check(planned(scenes), CINE)
        switch = rule(found, "presenter.unexpected_switch")
        self.assertEqual([f["scene"] for f in switch], [1])
        f = switch[0]
        self.assertEqual((f["severity"], f["repair"]["kind"], f["dimension"]), ("error", "none", "presenter"))
        self.assertEqual((f["evidence"]["expected"], f["evidence"]["found"], f["evidence"]["key"]), ("aadhi-teacher", "aadhi", "plan_for_other"))
        for words in ("Scene 2", "Aadhi Teacher", "AI Teacher", "Visual Review"):
            self.assertIn(words, f["message"])
        self.assertEqual(rule(found, "presenter.side_flip"), [], "Aadhi stands where the screenplay places him: no flip")
        reg = QM.registry(Q.Lesson(planned(scenes), CINE))
        self.assertEqual(reg["presenter"]["switches"], [{"scene": 1, "presenter_id": "aadhi"}])

    def test_a_scene_without_a_presenter_plan_and_a_classic_lesson(self):
        scenes = lesson()
        del scenes[0]["presenter_plan"]
        f = rule(check(planned(scenes), CINE), "presenter.unexpected_switch")
        self.assertEqual([(x["scene"], x["evidence"]["key"]) for x in f], [(0, "no_plan")])
        classic = {**CINE, "mode": "classic"}
        f = rule(check(scenes, classic), "presenter.unexpected_switch")
        self.assertEqual([(x["scene"], x["evidence"]["key"]) for x in f], [(0, "no_plan")], "classic lessons play the same plans")

    def test_a_hidden_presenter_is_never_a_switch(self):
        scenes = lesson()
        scenes[4]["presenter_plan"] = teacher(presenter_id="ai-teacher", enabled=False)
        scenes[4]["aadhi_position"] = "none"
        self.assertEqual(rule(check(planned(scenes), CINE), "presenter.unexpected_switch"), [])

    def test_a_composition_out_of_date_with_its_presenter_plan_is_replanned(self):
        scenes = planned()
        scenes[1]["presenter_plan"]["enabled"] = False  # taken out after the scene was composed
        f = rule(check(scenes, CINE), "presenter.plan_mismatch")
        self.assertEqual([(x["scene"], x["evidence"]["key"], x["severity"]) for x in f], [(1, "disabled", "error")])
        self.assertEqual(f[0]["repair"]["kind"], "auto")
        self.assertEqual(f[0]["repair"]["action"], {"type": "replan", "scenes": [1]})
        self.assertEqual(f[0]["repair"]["class"], "presentation")
        # composed for Aadhi, the lesson now presented by Aadhi Teacher: a re-plan, not an identity problem
        mascot_plans = planned(lesson(), LEGACY)
        found = check(mascot_plans, CINE)
        stale = rule(found, "presenter.plan_mismatch")
        self.assertTrue(stale and all(x["evidence"]["key"] == "other_presenter" and x["repair"]["kind"] == "auto" for x in stale))
        self.assertEqual(rule(found, "presenter.unexpected_switch"), [])

    def test_a_removal_in_visual_review_the_presenter_plan_missed(self):
        scenes = planned()
        scenes[0]["visual_review"] = {"presenter": {"status": "removed"}}  # the presenter plan still says enabled
        found = check(scenes, CINE)
        f = rule(found, "presenter.plan_mismatch")
        self.assertEqual([(x["scene"], x["evidence"]["key"], x["repair"]["kind"]) for x in f], [(0, "removed_in_review", "none")])
        self.assertEqual([x for x in rule(found, "presenter.intentional") if x["scene"] == 0], [], "no 'your choice' note while it shows")

    def test_a_clip_chosen_in_visual_review_is_intentional_otherwise_a_switch(self):
        scenes = lesson()
        scenes[0]["presenter_plan"] = teacher(media={"asset_id": A1, "presenter_id": "ai-teacher", "kind": "video"})
        scenes[0]["visual_review"] = {"presenter": {"status": "changed", "asset_id": A1}}
        found = check(planned(scenes), CINE)
        notes = rule(found, "presenter.intentional")
        self.assertEqual([(x["scene"], x["severity"], x["evidence"]["key"]) for x in notes], [(0, "info", "chosen_clip")])
        self.assertEqual(rule(found, "presenter.unexpected_switch"), [])
        del scenes[0]["visual_review"]
        f = rule(check(planned(scenes), CINE), "presenter.unexpected_switch")
        self.assertEqual([(x["scene"], x["evidence"]["key"], x["element"]) for x in f], [(0, "clip", "presenter clip")])

    def test_aadhi_is_filmed_in_his_studio(self):
        scenes = planned(lesson(), LEGACY)
        self.assertEqual(rule(check(scenes, LEGACY), "presenter.mascot_background"), [])
        shown = next(i for i, s in enumerate(scenes) if s["cinematic_plan"]["presenter"]["shown"])
        scenes[shown]["cinematic_plan"]["background"] = {"type": "gradient", "colors": ["#000", "#111", "#222"]}
        f = rule(check(scenes, LEGACY), "presenter.mascot_background")
        self.assertEqual([(x["scene"], x["severity"]) for x in f], [(shown, "error")])
        self.assertEqual(f[0]["repair"]["action"], {"type": "replan", "scenes": [shown]})

    def test_an_ai_presenter_without_its_clip(self):
        ai = {"presenter_id": "ai-teacher", "type": "ai_avatar", "enabled": True, "position": "right", "placement": "side"}
        scenes = [dict(s, presenter_plan=dict(ai)) for s in lesson()[:3]]
        scenes[2]["presenter_plan"]["media"] = {"asset_id": A1, "presenter_id": "ai-teacher", "kind": "video"}
        found = check(planned(scenes, AI), AI)
        warn = rule(found, "presenter.no_clip")
        self.assertEqual([(x["scene"], x["severity"]) for x in warn], [(0, "warning"), (1, "warning")])
        self.assertNotIn("did not finish", warn[0]["message"])
        failed = check(planned(scenes, AI), AI, media({A1: entry("video", source="ai-presenter", has_provenance=True, provider="p")},
                                                      [{"scene_index": 1, "slot": "presenter", "kind": "presenter", "status": "failed"}]))
        warn = {x["scene"]: x for x in rule(failed, "presenter.no_clip")}
        self.assertIn("did not finish", warn[1]["message"])
        self.assertEqual(warn[1]["evidence"]["found"], "failed")
        self.assertEqual(problems([x for x in failed if x["scene"] == 2]), [], "the scene with its clip is fine")

    def test_a_side_flip_nothing_explains_is_a_notice(self):
        scenes = lesson()
        scenes[1]["composition"] = {**scenes[1]["composition"], "presenter_position": "left"}
        scenes = planned(scenes)
        self.assertEqual(scenes[1]["cinematic_plan"]["presenter"]["side"], "left")
        found = check(scenes, CINE)
        self.assertEqual(rule(found, "presenter.side_flip"), [], "the screenplay asks for it")
        self.assertEqual([(x["scene"], x["evidence"]["key"]) for x in rule(found, "presenter.intentional")], [(1, "screenplay_position")])
        del scenes[1]["composition"]["presenter_position"]  # the plan still has the presenter on the left: nobody asked
        flips = rule(check(scenes, CINE), "presenter.side_flip")
        self.assertEqual([(x["scene"], x["severity"]) for x in flips], [(1, "notice")])
        self.assertEqual(flips[0]["repair"]["kind"], "suggest")
        self.assertEqual(flips[0]["repair"]["class"], "composition")
        self.assertEqual(flips[0]["repair"]["action"], {"type": "composition", "scene": 1, "overrides": {"presenter_position": "right"}})
        C.check_overrides(flips[0]["repair"]["action"]["overrides"])  # a valid Visual Review composition change
        scenes[1]["visual_review"] = {"composition": {"status": "changed", "overrides": {"presenter_position": "left"}}}
        found = check(scenes, CINE)
        self.assertEqual(rule(found, "presenter.side_flip"), [], "chosen in Visual Review")
        self.assertEqual([(x["scene"], x["evidence"]["key"]) for x in rule(found, "presenter.intentional")], [(1, "review_position")])

    def test_a_size_jump_between_scenes_with_the_same_role(self):
        scenes = planned(real_lesson(), DIRECTOR)
        roles = QM.registry(Q.Lesson(scenes, DIRECTOR))["presenter"]["roles"]
        a = next(i for i in range(len(roles) - 1) if roles[i] == roles[i + 1] != "hidden")
        self.assertEqual(rule(check(scenes, DIRECTOR), "presenter.scale_jump"), [])
        layer = next(l for l in scenes[a + 1]["cinematic_plan"]["layers"] if l["type"] == "presenter")
        layer["box"] = dict(layer["box"], h=round(layer["box"]["h"] * 0.6, 4))
        f = rule(check(scenes, DIRECTOR), "presenter.scale_jump")
        self.assertEqual([(x["scene"], x["severity"]) for x in f], [(a + 1, "notice")])
        self.assertEqual(f[0]["repair"]["kind"], "none", "a re-plan gives the same sizes: nothing to repair automatically")
        self.assertIn("smaller", f[0]["message"])

    def test_intentional_variations_are_one_note_per_scene(self):
        scenes = planned()
        scenes[3]["visual_review"] = {"presenter": {"status": "removed"},
                                      "composition": {"status": "changed", "overrides": {"presenter_size": "small"}}}
        scenes[3]["presenter_plan"] = dict(scenes[3]["presenter_plan"], enabled=False, review_status="removed")
        scenes[3]["composition"] = {**scenes[3]["composition"], "presenter_position": "hidden"}
        scenes = planned(scenes)
        found = check(scenes, CINE)
        notes = [x for x in found if x["scene"] == 3 and x["dimension"] == "presenter"]
        self.assertEqual([(x["rule"], x["severity"], x["evidence"]["key"]) for x in notes], [("presenter.intentional", "info", "removed")])
        self.assertEqual(problems(found), [])

    def test_never_more_than_one_info_per_scene_from_this_family(self):
        scenes = lesson()
        for i in (0, 1, 2, 3):
            scenes[i]["composition"] = {**(scenes[i].get("composition") or {}), "presenter_position": "left"}
            scenes[i]["visual_review"] = {"composition": {"status": "changed", "overrides": {"presenter_size": "dominant"}}}
        found = check(planned(scenes), CINE)
        per_scene = {}
        for x in found:
            if x["severity"] == "info" and x["scene"] is not None:
                per_scene[x["scene"]] = per_scene.get(x["scene"], 0) + 1
        self.assertTrue(per_scene and max(per_scene.values()) == 1, per_scene)


class MediaTest(unittest.TestCase):
    def test_media_is_silent_when_the_files_were_not_looked_up(self):
        scenes = [side_scene(picture())]
        self.assertEqual([f for f in check(scenes, CINE, None) if f["dimension"] == "media"], [])
        self.assertEqual([f for f in check(scenes, CINE, media()) if f["dimension"] == "media"], [], "an id not looked up: unknown")

    def test_a_shown_visual_that_is_gone_blocks_the_export(self):
        for why, asset in (("file_gone", entry(exists=False)), ("deleted", entry(status="deleted", accessible=False)),
                           ("unavailable", entry(accessible=False, kind=None, status=None)), ("failed", entry(status="failed"))):
            with self.subTest(why=why):
                f = rule(check([side_scene(picture())], CINE, media({A1: asset})), "media.missing")
                self.assertEqual([(x["scene"], x["severity"], x["evidence"]["found"], x["element"]) for x in f], [(0, "blocking", why, "side")])
                self.assertEqual(f[0]["repair"]["kind"], "none")
                for words in ("Scene 1", "picture", "empty panel", "Visual Review"):
                    self.assertIn(words, f[0]["message"])
        main = {"type": "ai_video", "title": "Forest", "prompt": "forest", "narration": "n",
                "visual_plan": {"main": {"source": "ASSET", "media": "VIDEO", "asset_id": A2, "selection": "explicit"}}}
        f = rule(check([main], CINE, media({A2: entry("video", exists=False)})), "media.missing")
        self.assertEqual([(x["element"], x["severity"]) for x in f], [("main", "blocking")])
        self.assertIn("video", f[0]["message"])

    def test_a_chosen_visual_the_router_found_unusable(self):
        plan = {"source": "NONE", "media": "STATIC_IMAGE", "selection": "reviewed", "asset_id": A1, "error": "asset_unavailable"}
        f = check([side_scene(plan)], CINE, media({A1: entry(status="deleted", accessible=False)}))
        self.assertEqual([(x["rule"], x["severity"]) for x in f if x["dimension"] == "media"], [("media.missing", "blocking")])
        f = check([side_scene(plan)], CINE, media({A1: entry()}))
        self.assertEqual([(x["rule"], x["severity"]) for x in f if x["dimension"] == "media"], [("media.plan_outdated", "notice")])

    def test_a_removed_or_absent_visual_is_not_checked(self):
        gone = media({A1: entry(exists=False)})
        for scene in (side_scene(picture(selection="removed", source="NONE")),
                      side_scene(picture(), visual_review={"side": {"status": "removed"}}),
                      side_scene({"source": "NONE", "media": "NONE", "selection": "none"}),
                      side_scene({"source": "PROCEDURAL", "media": "ANIMATION", "selection": "builtin", "renderer": "chart"})):
            self.assertEqual([f for f in check([scene], CINE, gone) if f["dimension"] == "media"], [], scene["visual_plan"])

    def test_the_kind_must_fit_the_slot(self):
        main = {"type": "ai_video", "title": "Forest", "narration": "n",
                "visual_plan": {"main": {"source": "ASSET", "media": "VIDEO", "asset_id": A1, "selection": "reviewed"}}}
        f = rule(check([main], CINE, media({A1: entry("image")})), "media.kind_mismatch")
        self.assertEqual([(x["element"], x["severity"], x["evidence"]["found"]) for x in f], [("main", "error", "image")])
        f = rule(check([side_scene(picture())], CINE, media({A1: entry("video")})), "media.kind_mismatch")
        self.assertEqual([(x["element"], x["evidence"]["expected"]) for x in f], [("side", ["image"])])
        clip = picture(media="VIDEO")
        self.assertEqual(rule(check([side_scene(clip)], CINE, media({A1: entry("video")})), "media.kind_mismatch"), [])
        f = rule(check([side_scene(clip)], CINE, media({A1: entry("audio")})), "media.kind_mismatch")
        self.assertIn("sound file", f[0]["message"])

    def test_an_empty_file(self):
        f = rule(check([side_scene(picture())], CINE, media({A1: entry(file_size=0)})), "media.empty_file")
        self.assertEqual([(x["scene"], x["severity"]) for x in f], [(0, "error")])
        self.assertEqual(rule(check([side_scene(picture())], CINE, media({A1: entry(file_size=None)})), "media.empty_file"), [])

    def test_a_shape_clearly_wrong_for_a_wide_place(self):
        main = {"type": "simulation", "title": "Watch", "manim_code": "x", "narration": "n",
                "visual_plan": {"main": {"source": "MANIM", "media": "ANIMATION", "asset_id": A1, "selection": "builtin", "renderer": "manim"}}}
        f = rule(check([main], CINE, media({A1: entry("video", width=640, height=640)})), "media.aspect")
        self.assertEqual([(x["severity"], x["evidence"]["found"]) for x in f], [("notice", 1.0)])
        self.assertEqual(rule(check([main], CINE, media({A1: entry("video", width=1280, height=720)})), "media.aspect"), [])
        self.assertEqual(rule(check([main], CINE, media({A1: entry("video", width=None, height=None)})), "media.aspect"), [])
        self.assertEqual(rule(check([side_scene(picture())], CINE, media({A1: entry(width=600, height=600)})), "media.aspect"), [],
                         "a side picture gets a card of its own shape")

    def test_an_ai_visual_without_provenance(self):
        plan = picture(source="AI_IMAGE", selection="cached")
        f = rule(check([side_scene(plan)], CINE, media({A1: entry(source="ai-image")})), "media.no_provenance")
        self.assertEqual([(x["severity"], x["repair"]["kind"]) for x in f], [("notice", "none")])
        made = entry(source="ai-image", has_provenance=True, provider="maker", model="m1")
        self.assertEqual(rule(check([side_scene(plan)], CINE, media({A1: made})), "media.no_provenance"), [])

    def test_a_visual_waiting_for_generation_or_whose_run_failed(self):
        planned_ai = {"source": "AI_IMAGE", "media": "STATIC_IMAGE", "selection": "planned", "requires_generation": True, "provider": "x"}
        off = {"source": "NONE", "media": "STATIC_IMAGE", "selection": "none", "would_require": "AI_IMAGE"}
        manim = {"type": "simulation", "title": "Watch", "manim_code": "x", "narration": "n",
                 "visual_plan": {"main": {"source": "MANIM", "media": "ANIMATION", "selection": "builtin", "renderer": "manim"}}}
        for scene, found in ((side_scene(planned_ai), "not_generated"), (side_scene(off), "not_generated"), (manim, "manim")):
            f = rule(check([scene], CINE, media()), "media.not_generated")
            self.assertEqual([(x["severity"], x["evidence"]["found"]) for x in f], [("warning", found)])
            self.assertIn("fallback", f[0]["message"])
        failed = media(runs=[{"scene_index": 0, "slot": "side", "kind": "image", "status": "needs_attention", "provider": "x", "model": None}])
        found = check([side_scene(planned_ai)], CINE, failed)
        self.assertEqual([(x["rule"], x["severity"]) for x in found if x["dimension"] == "media"], [("media.run_failed", "warning")])
        making = media(runs=[{"scene_index": 0, "slot": "side", "kind": "image", "status": "running"}])
        f = rule(check([side_scene(planned_ai)], CINE, making), "media.not_generated")
        self.assertIn("still being made", f[0]["message"])
        shown = media({A1: entry()}, runs=[{"scene_index": 0, "slot": "side", "kind": "image", "status": "failed"}])
        self.assertEqual([x for x in check([side_scene(picture())], CINE, shown) if x["dimension"] == "media"], [],
                         "a failed new version while the scene shows a good picture is not a problem")
        pending = check([side_scene(picture())], CINE, media({A1: entry(status="pending")}))
        self.assertEqual([(x["rule"], x["evidence"]["found"]) for x in pending if x["dimension"] == "media"], [("media.not_generated", "pending")])

    def test_ai_visuals_from_several_generators_are_recorded(self):
        one = side_scene(picture(A1, source="AI_IMAGE", selection="cached"))
        two = side_scene(picture(A2, source="AI_IMAGE", selection="cached"), title="Roots")
        looked_up = media({A1: entry(source="ai-image", has_provenance=True, provider="maker-one", model="m1"),
                           A2: entry(source="ai-image", has_provenance=True, provider="maker-two", model="m9")})
        f = rule(check([one, two], CINE, looked_up), "media.mixed_providers")
        self.assertEqual([(x["scene"], x["severity"], x["element"]) for x in f], [(None, "info", "image")])
        self.assertEqual(f[0]["evidence"]["found"], ["maker-one/m1", "maker-two/m9"])
        self.assertNotIn("maker", f[0]["message"], "no provider names in a message")
        reg = QM.registry(Q.Lesson([one, two], CINE, media=looked_up))
        self.assertEqual(reg["media"], {"sources": {"AI_IMAGE": 2}, "providers": ["maker-one", "maker-two"], "checked": True})
        same = media({A1: looked_up["assets"][A1], A2: dict(looked_up["assets"][A2], provider="maker-one", model="m1")})
        self.assertEqual(rule(check([one, two], CINE, same), "media.mixed_providers"), [])
        self.assertEqual(rule(check([one, two], CINE, None), "media.mixed_providers"), [], "only when the files were looked up")

    def test_the_presenter_clip_and_the_background_are_checked_too(self):
        scenes = lesson()[:2]
        scenes[0]["presenter_plan"] = teacher(media={"asset_id": A1, "presenter_id": "aadhi-teacher", "kind": "video"})
        scenes = planned(scenes)
        scenes[1]["cinematic_plan"]["background"] = {"type": "image", "asset_id": A2, "scrim": 0.55}
        gone = media({A1: entry("video", exists=False), A2: entry(status="deleted", accessible=False)})
        found = check(scenes, CINE, gone)
        clip = rule(found, "media.presenter_clip_missing")
        self.assertEqual([(x["scene"], x["severity"], x["repair"]["kind"]) for x in clip], [(0, "error", "none")])
        bg = rule(found, "media.background_missing")
        self.assertEqual([(x["scene"], x["severity"], x["repair"]["kind"]) for x in bg], [(1, "error", "auto")])
        self.assertEqual(bg[0]["repair"]["action"], {"type": "replan", "scenes": [1]})
        fine = media({A1: entry("video", source="ai-presenter", has_provenance=True, provider="p"), A2: entry(width=1600, height=1200)})
        found = check(scenes, CINE, fine)
        self.assertEqual([(x["rule"], x["scene"]) for x in found if x["severity"] != "info"], [("media.aspect", 1)], "a 4:3 backdrop")

    def test_a_failed_ai_background_is_left_to_the_style_family(self):
        scenes = planned(lesson()[:2])
        scenes[0]["cinematic_plan"]["background"] = {"type": "gradient", "colors": ["#000"] * 3, "fallback": True}
        runs = [{"scene_index": None, "slot": "background", "kind": "image", "status": "failed"}]
        self.assertEqual([f for f in check(scenes, CINE, media(runs=runs)) if f["dimension"] == "media"], [],
                         "the gradient shown instead is reported once, by the style family's background check")

    def test_never_suggests_regenerating(self):
        everything = [side_scene({"source": "AI_IMAGE", "media": "STATIC_IMAGE", "selection": "planned", "requires_generation": True}),
                      side_scene(picture(A1)), side_scene(picture(A2, source="AI_IMAGE")), side_scene(picture(A3))]
        looked_up = media({A1: entry(exists=False), A2: entry(source="ai-image"), A3: entry("video", file_size=0)},
                          runs=[{"scene_index": 0, "slot": "side", "status": "failed"}])
        found = [f for f in check(everything, CINE, looked_up) if f["dimension"] == "media"]
        self.assertTrue(found)
        for f in found:
            self.assertNotRegex(f["message"].lower(), r"regenerat|generate (it )?again|new version")
            self.assertIn(f["repair"]["kind"], ("none", "auto"))


class ContractTest(unittest.TestCase):
    def broken(self):
        scenes = planned()
        scenes[1]["presenter_plan"] = dict(scenes[1]["presenter_plan"], presenter_id="ai-teacher")
        scenes[2]["visual_plan"] = {"side": picture(A1)}
        scenes[3]["visual_plan"] = {"side": {"source": "AI_IMAGE", "media": "STATIC_IMAGE", "selection": "planned", "requires_generation": True}}
        return scenes, media({A1: entry(exists=False)})

    def test_deterministic_and_the_input_never_changes(self):
        scenes, looked_up = self.broken()
        before = (copy.deepcopy(scenes), copy.deepcopy(looked_up))
        first = check(scenes, CINE, looked_up)
        second = check(copy.deepcopy(scenes), CINE, copy.deepcopy(looked_up))
        self.assertEqual(first, second)
        self.assertEqual((scenes, looked_up), before)
        Q._SCENE_MEMO.clear()
        report = Q.evaluate_lesson(scenes, CINE, None, looked_up, C.compose_lesson(copy.deepcopy(scenes), CINE))
        self.assertEqual((scenes, looked_up), before)
        self.assertIn("quality_media", report["families"])
        mine = [f for f in report["issues"] if f["rule"].startswith(("presenter.", "media."))]
        Q._SCENE_MEMO.clear()
        again = Q.evaluate_lesson(copy.deepcopy(scenes), CINE, None, copy.deepcopy(looked_up),
                                  C.compose_lesson(copy.deepcopy(scenes), CINE))
        self.assertEqual([f for f in again["issues"] if f["rule"].startswith(("presenter.", "media."))], mine, "same findings, same ids")
        # a finding fixed by the same re-plan as another is folded into it (one cause, one finding): named in its evidence
        reported = {f["rule"] for f in mine} | {r for f in report["issues"] for r in f["evidence"].get("also", [])}
        self.assertLessEqual({"presenter.unexpected_switch", "presenter.plan_mismatch", "media.missing", "media.not_generated"}, reported)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("presenter", report["registry"])
        self.assertIn("media", report["registry"])
        for f in mine:  # the contract's message rules: 1-based scene numbers, no token or provider names
            if f["scene"] is not None:
                self.assertIn(f"Scene {f['scene'] + 1}", f["message"])
            self.assertNotRegex(f["message"], r"asset_id|presenter_plan|visual_plan|cinematic_plan|--|_[a-z]+_")

    def test_a_check_that_cannot_decide_does_not_crash(self):
        odd = [{"cinematic_plan": {"presenter": "x", "layers": "y", "background": 5}, "presenter_plan": {"media": "z", "enabled": "yes"},
                "visual_plan": {"main": 3, "side": {"asset_id": 7, "source": ["NONE"]}}, "visual_review": {"presenter": "no", "side": 1}},
               {"cinematic_plan": {"presenter": {"shown": True, "side": "up", "presenter_id": None},
                                   "layers": [{"type": "presenter", "box": {"x": "a"}}]}},
               None, "text", 42, {"type": None, "presenter_plan": {"presenter_id": 5, "type": "custom", "enabled": True}}]
        for settings in ({"presenter_legacy": "maybe", "presenter_id": 5}, CINE, AI, {}, None):
            for looked_up in (None, {"assets": [], "runs": "x"}, {"assets": {A1: "bad"}, "runs": [None, 3]}, media()):
                lesson_ = Q.Lesson(odd, settings, media=looked_up)
                for i in range(lesson_.count):
                    QM.scene_checks(lesson_, i)
                QM.lesson_checks(lesson_)
                json.dumps(QM.registry(lesson_))
        self.assertEqual(QM.referenced_asset_ids(odd), [])
        self.assertEqual(QM.referenced_asset_ids("nope"), [])

    def test_quick_for_a_large_lesson(self):
        scenes = planned(lesson() * 4)  # 32 scenes
        for i in range(0, 32, 3):
            scenes[i]["visual_plan"] = {"side": picture(A1)}
        looked_up = media({A1: entry()})
        t0 = time.perf_counter()
        lesson_ = Q.Lesson(scenes, CINE, media=looked_up)
        for i in range(lesson_.count):
            QM.scene_checks(lesson_, i)
        QM.lesson_checks(lesson_)
        QM.registry(lesson_)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.5, f"{elapsed * 1000:.0f} ms")


class LoadMediaTest(unittest.TestCase):
    """load_media against the throwaway database: one query for the assets, a plain file check, nothing marked."""

    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.alice, cls.bob = ensure_user("qm_alice"), ensure_user("qm_bob")
        cls.library = server.asset_library
        cls.made = []
        with database.SessionLocal() as db:
            project = models.Project(user_id=cls.alice, subject_name="Quality media", json_data=json.dumps({"scenes": []}))
            other = models.Project(user_id=cls.alice, subject_name="Another lesson", json_data=json.dumps({"scenes": []}))
            db.add_all([project, other])
            db.commit()
            cls.pid, cls.other_pid = project.id, other.id
        cls.own = cls.add(cls.alice)
        cls.gone = cls.add(cls.alice, file=False)
        cls.deleted = cls.add(cls.alice, status="deleted")
        cls.bobs = cls.add(cls.bob)
        cls.shared = cls.add(None)
        cls.ai = cls.add(cls.alice, source="ai-image", details={"prompt": "a secret prompt about green leaves",
                                                                "generation": {"provider": "fake-image", "model": "m1", "hash": "f" * 64}})
        cls.ai_bare = cls.add(cls.alice, source="ai-image", details={"prompt": "another secret prompt"})
        cls.clip = cls.add(cls.alice, kind="video", source="ai-presenter",
                           details={"generation": {"provider": "fake-presenter", "model": None}, "presenter": {"presenter_id": "ai-teacher"}})
        cls.unknown = uuid.uuid4().hex

    @classmethod
    def tearDownClass(cls):
        for path in cls.made:
            if os.path.exists(path):
                os.remove(path)

    @classmethod
    def add(cls, owner, kind="image", status="ready", file=True, source="upload", details=None):
        key = f"quality-media/{uuid.uuid4().hex}.{'png' if kind == 'image' else 'mp4'}"
        path = cls.library.volumes["assets"].path(key)
        if file:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"\x89PNG not really" if kind == "image" else b"\x00\x00\x00\x18ftypmp42")
            cls.made.append(path)
        asset_id = uuid.uuid4().hex
        with database.SessionLocal() as db:
            db.add(models.Asset(id=asset_id, scope_key=assets.scope_key(owner), owner_id=owner, kind=kind, source=source, status=status,
                                file_name="x.png", mime_type="image/png" if kind == "image" else "video/mp4", storage_volume="assets",
                                storage_key=key, file_size=1234, width=1600, height=900, details=json.dumps(details) if details else None))
            db.commit()
        return asset_id

    def scenes(self):
        return [
            {"type": "content", "title": "One", "visual_plan": {"side": picture(self.own), "main": picture(self.gone)},
             "visual_review": {"side": {"status": "changed", "asset_id": self.deleted}}},
            {"type": "content", "title": "Two", "visual_plan": {"side": picture(self.bobs)},
             "presenter_plan": {"presenter_id": "ai-teacher", "media": {"asset_id": self.clip, "url": "/api/assets/x/content?token=t"}}},
            {"type": "content", "title": "Three", "visual_plan": {"side": picture(self.ai, url="https://example.invalid/a.png")},
             "cinematic_plan": {"background": {"type": "image", "asset_id": self.shared}}},
            {"type": "content", "title": "Four", "visual_plan": {"side": picture(self.ai_bare), "main": picture(self.unknown)}},
        ]

    def snapshot(self):
        ids = [self.own, self.gone, self.deleted, self.bobs, self.shared, self.ai, self.ai_bare, self.clip]
        with database.SessionLocal() as db:
            rows = db.query(models.Asset.id, models.Asset.status, models.Asset.error_message, models.Asset.updated_at,
                            models.Asset.details).filter(models.Asset.id.in_(ids)).order_by(models.Asset.id).all()
            refs = db.query(models.AssetReference).count()
        return [tuple(r) for r in rows], refs, sorted((p, os.path.exists(p)) for p in self.made)

    def load(self, project_id=None, **kw):
        with database.SessionLocal() as db:
            result = QM.load_media(db, self.alice, self.scenes(), project_id, **kw)
            self.assertFalse(db.dirty or db.new or db.deleted, "nothing staged in the session")
        return result

    def test_reads_the_metadata_without_marking_anything(self):
        before = self.snapshot()
        result = self.load(self.pid)
        self.assertEqual(self.snapshot(), before, "statuses, files and references unchanged (the missing file is not marked)")
        a = result["assets"]
        self.assertTrue(result["checked"])
        self.assertEqual(sorted(a), sorted([self.own, self.gone, self.deleted, self.bobs, self.shared, self.ai, self.ai_bare, self.clip,
                                            self.unknown]))
        self.assertEqual(a[self.own], {"kind": "image", "status": "ready", "exists": True, "width": 1600, "height": 900,
                                       "file_size": 1234, "mime": "image/png", "source": "upload", "provider": None, "model": None,
                                       "has_provenance": False, "accessible": True})
        self.assertEqual((a[self.gone]["status"], a[self.gone]["exists"], a[self.gone]["accessible"]), ("ready", False, True))
        self.assertEqual((a[self.deleted]["status"], a[self.deleted]["accessible"]), ("deleted", False))
        self.assertEqual((a[self.shared]["accessible"], a[self.shared]["exists"]), (True, True), "shared assets are usable")
        self.assertEqual((a[self.ai]["provider"], a[self.ai]["model"], a[self.ai]["has_provenance"]), ("fake-image", "m1", True))
        self.assertEqual((a[self.ai_bare]["provider"], a[self.ai_bare]["has_provenance"]), (None, False))
        self.assertEqual((a[self.clip]["kind"], a[self.clip]["source"]), ("video", "ai-presenter"))
        with database.SessionLocal() as db:
            self.assertEqual(db.get(models.Asset, self.gone).status, "ready")

    def test_honours_ownership(self):
        a = self.load()["assets"]
        blank = {"kind": None, "status": None, "exists": False, "width": None, "height": None, "file_size": None, "mime": None,
                 "source": None, "provider": None, "model": None, "has_provenance": False, "accessible": False}
        self.assertEqual(a[self.bobs], blank, "another user's asset reads exactly like an unknown id: nothing leaks")
        self.assertEqual(a[self.unknown], blank)
        with database.SessionLocal() as db:
            as_bob = QM.load_media(db, self.bob, self.scenes(), None)["assets"]
        self.assertTrue(as_bob[self.bobs]["accessible"])
        self.assertEqual(as_bob[self.own], blank)

    def test_no_urls_prompts_or_secrets(self):
        text = json.dumps(self.load(self.pid))
        for leak in ("secret prompt", "prompt", "http", "/api/", "token", "quality-media/", "storage", "url", "example.invalid", "hash"):
            self.assertNotIn(leak, text)

    def test_the_lessons_runs_latest_per_scene_slot(self):
        now = datetime.datetime.utcnow()

        def run(status, minutes, *, scene=1, slot="side", user=None, project=None, kind=None, media_type="image", provider="maker"):
            return models.AIGenerationRun(id=uuid.uuid4().hex, scope_key=f"user:{user or self.alice}", user_id=user or self.alice,
                                          media_type=media_type, status=status, provider=provider, model="m1", kind=kind,
                                          project_id=project or self.pid, scene_index=scene, slot=slot,
                                          request=json.dumps({"prompt": "a secret prompt for the run"}),
                                          created_at=now - datetime.timedelta(minutes=minutes))
        with database.SessionLocal() as db:
            db.add_all([run("failed", 30), run("completed", 20),                         # retried: the latest counts
                        run("needs_attention", 10, scene=2, slot="main", media_type="video"),
                        run("failed", 5, scene=0, slot="presenter", media_type="presenter"),
                        run("failed", 4, scene=None, slot="background"),
                        run("failed", 3, scene=3, slot="main", kind="manim", media_type="video", provider="manim"),
                        run("failed", 2, scene=4, slot=None, kind="document_analysis", media_type="text"),
                        run("failed", 1, scene=1, slot="side", user=self.bob),                # another user's
                        run("failed", 1, scene=1, slot="side", project=self.other_pid)])      # another lesson's
            db.commit()
        runs = self.load(self.pid)["runs"]
        self.assertEqual([(r["scene_index"], r["slot"], r["kind"], r["status"]) for r in runs],
                         [(None, "background", "image", "failed"), (0, "presenter", "presenter", "failed"), (1, "side", "image", "completed"),
                          (2, "main", "video", "needs_attention"), (3, "main", "manim", "failed")])
        self.assertEqual(set(runs[0]), {"scene_index", "slot", "kind", "status", "provider", "model"})
        self.assertNotIn("secret", json.dumps(runs))
        self.assertEqual(self.load(None)["runs"], [], "no saved lesson: no runs")

    def test_one_query_for_the_assets_and_one_for_the_runs(self):
        statements = []

        def count(conn, cursor, statement, *args):
            statements.append(statement)
        event.listen(database.engine, "before_cursor_execute", count)
        try:
            self.load(self.pid)
            with_runs = len(statements)
            statements.clear()
            self.load(None)
            without = len(statements)
        finally:
            event.remove(database.engine, "before_cursor_execute", count)
        self.assertEqual((with_runs, without), (2, 1))
        with database.SessionLocal() as db:
            self.assertEqual(QM.load_media(db, self.alice, [], None), {"checked": True, "assets": {}, "runs": []})

    def test_the_servers_library_is_used_by_default(self):
        self.assertEqual(self.load(None), self.load(None, library=self.library))
        self.assertIs(QM._library(), server.asset_library)

    def test_the_report_flags_a_missing_picture_without_marking_it(self):
        client = TestClient(server.app)
        scenes = planned(lesson()[:3])
        scenes[1]["visual_plan"] = {"side": picture(self.gone)}
        before = self.snapshot()
        r = client.post("/api/quality/lesson", json={"scenes": scenes, "settings": CINE, "project_id": self.pid}, headers=auth("qm_alice"))
        self.assertEqual(r.status_code, 200, r.text)
        missing = [f for f in r.json()["issues"] if f["rule"] == "media.missing"]
        self.assertEqual([(f["scene"], f["severity"]) for f in missing], [(1, "blocking")])
        self.assertEqual(r.json()["status"], "blocked")
        self.assertTrue(r.json()["registry"]["media"]["checked"])
        self.assertEqual(self.snapshot(), before, "the lookup marked nothing")


if __name__ == "__main__":
    unittest.main()
