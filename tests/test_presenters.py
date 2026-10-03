"""Backend tests of the AI Presenter / AI Teacher (Phase 12): profiles and capabilities, the Presenter Director
(roles, modes, styles, gestures, Visual Review decisions, safe composition, the Phase 15 visual direction's presenter
interaction), acting at a moment (Phase 16: what each presenter can change mid-scene), speech timelines, presenter requests
through the AI media layer (provider selection, cache identity, cache hit, New Version, assets and provenance,
recovery, cancellation, duplicates, fallback), the review endpoint, access control and backward compatibility.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_presenters.py" -v

Presenter clips are made by the local stand-in (a drawn teacher whose mouth follows the narration's loudness) in a
service built for the test; the server's own registry has no presenter provider, exactly like production here.
TTS is replaced by a local speech-like tone. No real provider, no network.
"""
import asyncio
import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
import uuid
from unittest import mock

from backend_env import FFMPEG, TMP, assert_isolated, auth, ensure_user  # first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import ai_providers  # noqa: E402
import ai_runs  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import presenters as P  # noqa: E402
import server  # noqa: E402
from ai_media import AIMediaService, GenerationFailed  # noqa: E402
from ai_providers import PRESENTER, MediaRequest  # noqa: E402
from ai_recovery import RecoveryManager  # noqa: E402


class Crash(BaseException):
    """The simulated power cut: nothing in the worker catches it."""


async def fake_tts(request, user):
    """A speech-like tone per text (loud and quiet parts), like the TTS files the page plays."""
    name = "audio_" + hashlib.sha256(f"{request.text}_{request.voice}_{request.tts_engine}".encode()).hexdigest()[:16] + ".wav"
    path = os.path.join(server.STATIC_DIR, name)
    if not os.path.exists(path):
        seconds = max(1.0, len(request.text) / 15)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
                        "-af", "volume='if(lt(mod(t,0.6),0.35),1,0.02)':eval=frame", path], check=True)
    return {"status": "success", "audio_url": f"/static/{name}?t=1"}


def scenes():
    return [
        {"type": "title", "title": "Photosynthesis", "narration": "Welcome! Today we learn how plants make food.", "aadhi_position": "center"},
        {"type": "content", "title": "The leaf", "narration": "Look at this diagram of a leaf.", "side_panel": {"type": "image", "prompt": "leaf"}, "aadhi_position": "left"},
        {"type": "content", "title": "Stages", "narration": "It happens in three stages.", "html": "<p>Light, water, sugar.</p>"},
        {"type": "content", "title": "Equation", "html": "<p>\\(6CO_2 + 6H_2O \\to C_6H_{12}O_6 + 6O_2\\)</p>", "narration": "Here is the equation."},
        {"type": "quiz_checkpoint", "question": "What do plants make?", "narration": "Quick question!"},
        {"type": "content", "title": "Code", "html": "<pre>for s in stages: print(s)</pre>", "narration": "A loop prints the stages."},
        {"type": "content", "title": "Summary", "narration": "In summary, plants use light. [PAUSE:1] They make glucose."},
        {"type": "simulation", "title": "Sim", "manim_code": "class A(Scene): pass", "narration": "Watch."},
    ]


TEACHER = P.BUILTIN["aadhi-teacher"]


class VocabularyTest(unittest.TestCase):
    def test_fallbacks(self):
        self.assertEqual(P.map_vocab("surprised", ["neutral", "friendly", "engaged"], P.EXPRESSION_FALLBACK, "neutral"),
                         ("engaged", "surprised shown as engaged"))
        self.assertEqual(P.map_vocab("friendly", ["neutral", "friendly"], P.EXPRESSION_FALLBACK, "neutral"), ("friendly", None))
        self.assertEqual(P.map_vocab("counting", ["none", "open_hand"], P.GESTURE_FALLBACK, "none"), ("open_hand", "counting shown as open_hand"))
        self.assertEqual(P.map_vocab("serious", ["happy"], P.EXPRESSION_FALLBACK, "neutral"), ("happy", "serious shown as happy"))
        self.assertEqual(P.map_vocab("point", [], P.GESTURE_FALLBACK, "none"), (None, None))  # a presenter without gestures

    def test_profiles_are_stable(self):
        a, b = P.appearance_hash(TEACHER), P.appearance_hash(dict(TEACHER))
        self.assertEqual(a, b)
        changed = {**TEACHER, "version": 2}
        self.assertNotEqual(P.appearance_hash(changed), a)  # identity changes only with the profile's version/appearance


class DirectorTest(unittest.TestCase):
    def plans(self, mode, **settings):
        return P.plan_lesson(scenes(), TEACHER, {"mode": mode, "position": "right", **settings})

    def test_automatic_plans_follow_what_each_scene_teaches(self):
        plans = self.plans("auto")
        summary = [(p["role"], p["enabled"], p["layout"], p.get("placement"), p.get("gesture")) for p in plans]
        self.assertEqual(summary, [
            ("intro", True, "right", "side", "welcome"),
            ("visual", True, "right", "side", "point"),        # "look at this diagram"
            ("explanation", True, "right", "side", "counting"),  # "three stages"
            ("math", True, "right", "pip", "point"),           # small: the equation gets the attention
            ("quiz", True, "right", "side", "point"),
            ("code", False, "hidden", None, "none"),
            ("summary", True, "right", "side", "open_hand"),
            ("technical", False, "hidden", None, "none")])
        for p in plans:
            if p["enabled"]:
                self.assertTrue(p["box"] and p["reason"])
                self.assertEqual(P.safe_box(p["layout"], p["placement"], True)[1], [])  # never over the content
        self.assertLess(plans[3]["box"]["w"], plans[2]["box"]["w"])
        json.dumps(plans)  # stored in the scene as it is

    def test_modes(self):
        self.assertTrue(all(not p["enabled"] for p in self.plans("off")))
        always = self.plans("always")
        self.assertTrue(all(p["enabled"] for p in always))
        self.assertEqual(always[5]["placement"], "pip")  # code: small instead of hidden
        lesson = self.plans("lesson")
        self.assertEqual([(p["position"], p["source"]) for p in lesson[:2]], [("center", "lesson"), ("left", "lesson")])

    def test_style_expression_and_user_overrides(self):
        self.assertEqual(self.plans("auto", style="formal")[2]["expression"], "serious")
        self.assertEqual(self.plans("auto", style="energetic")[2]["expression"], "happy")
        custom = self.plans("auto", expression="thinking", gesture="emphasis")[2]
        self.assertEqual((custom["expression"], custom["gesture"]), ("thinking", "emphasis"))

    def test_left_position_and_mascot_capabilities(self):
        left = P.plan_lesson(scenes(), TEACHER, {"mode": "auto", "position": "left"})
        self.assertEqual(left[0]["layout"], "left")
        self.assertEqual(P.safe_box("left", "side", True)[1], [])
        mascot = P.plan_lesson(scenes(), P.BUILTIN["aadhi"], {"mode": "auto"})
        self.assertEqual(mascot[2]["gesture_shown"], None)  # Aadhi's clips have no gestures: none is claimed
        self.assertNotIn("expression_shown", mascot[0])  # "happy": Aadhi has it
        self.assertEqual(mascot[1]["expression_shown"], "friendly")  # "engaged" -> its nearest, "friendly"
        self.assertIn("engaged shown as friendly", mascot[1]["fallbacks"])

    def test_review_decisions_come_first(self):
        s = scenes()
        s[0]["visual_review"] = {"presenter": {"status": "removed"}}
        s[2]["visual_review"] = {"presenter": {"status": "changed", "position": "left"}}
        s[3]["visual_review"] = {"presenter": {"status": "approved", "asset_id": "a" * 32}}
        plans = P.plan_lesson(s, TEACHER, {"mode": "auto"})
        self.assertEqual((plans[0]["enabled"], plans[0]["source"], plans[0]["reason"]), (False, "review", "removed in Visual Review"))
        self.assertEqual((plans[2]["position"], plans[2]["layout"]), ("left", "left"))
        self.assertEqual((plans[3]["media_asset_id"], plans[3]["review_status"]), ("a" * 32, "approved"))

    def test_safe_composition(self):
        for layout, placement in (("left", "side"), ("right", "side"), ("center", "side"), ("left", "pip"), ("right", "pip")):
            self.assertEqual(P.safe_box(layout, placement)[1], [], (layout, placement))
        self.assertTrue(P.safe_box("popup_bottom_left", "side")[1])  # the popup layouts have no free corner
        box = {"x": 0.3, "y": 0.2, "w": 0.3, "h": 0.3}
        self.assertTrue(any(P._overlap(box, z) for z in P.content_zones("right")))

    def test_scenes_without_presenter_keep_their_placement(self):
        old = {"type": "content", "title": "Old", "html": "<p>x</p>", "narration": "x"}  # no aadhi_position (older lessons)
        plan = P.plan_scene(old, 1, 2, P.BUILTIN["aadhi"], {})
        self.assertEqual((plan["source"], plan["position"], plan["layout"]), ("lesson", "left", "left"))
        video = P.plan_scene({"type": "ai_video", "prompt": "x"}, 1, 2, P.BUILTIN["aadhi"], {})
        self.assertEqual(video["placement"], "background")
        popup = P.plan_scene({"type": "content", "aadhi_position": "popup_bottom_right"}, 1, 2, TEACHER, {"mode": "lesson"})
        self.assertEqual((popup["layout"], popup["placement"]), ("right", "pip"))  # drawn presenters never use popup layouts


def directed(scene, interaction, role="guide", **extra):
    """The scene with a Phase 15 visual direction (visual_director.py, version 1) asking the presenter for an interaction."""
    return {**scene, "visual_direction": {"version": 1, "strategy": "concept_overview", "presenter": {"role": role, "interaction": interaction}, **extra}}


# A plain explanation (explaining / engaged / explaining): no counting or pointing words, no side visual
PLAIN = {"type": "content", "title": "Why leaves are green", "html": "<p>Chlorophyll absorbs red and blue light.</p>",
         "narration": "Chlorophyll absorbs red and blue light and reflects green."}
# Where and whether the presenter appears: the scene composer's (the direction's role), never changed by the interaction
PLACE = ("enabled", "position", "placement", "layout", "box", "source", "role", "review_status", "presenter_id", "type", "speech")


class VisualDirectionTest(unittest.TestCase):
    """The Presenter Director reads the Visual Director's semantic presenter intent (Phase 15): the interaction
    refines what the presenter does; the role is the composer's and never changes whether or where it appears."""

    AUTO = {"mode": "auto", "position": "right"}

    def plan(self, scene, profile=TEACHER, settings=None, index=1, count=4):
        return P.plan_scene(scene, index, count, profile, settings or self.AUTO)

    def acting(self, plan):
        return plan["behavior"], plan["expression"], plan["gesture"]

    def test_contract_matches_the_visual_director(self):
        import visual_director as VD
        self.assertEqual(P.DIRECTION_VERSION, VD.VERSION)
        self.assertEqual(P.DIRECTION_INTERACTIONS, VD.INTERACTIONS)
        self.assertEqual(P.DIRECTION_ROLES, VD.PRESENTER_ROLES)
        for behavior, expression, gesture, why in P.INTERACTION_ACTING.values():  # only the existing vocabulary
            self.assertIn(behavior, P.BEHAVIORS + (None,))
            self.assertIn(expression, P.EXPRESSIONS + (None,))
            self.assertIn(gesture, P.GESTURES + (None,))
            self.assertTrue(why)
        self.assertLessEqual(set(P.INTERACTION_ACTING), set(P.DIRECTION_INTERACTIONS))

    def test_each_interaction_for_the_teacher(self):
        base = self.plan(PLAIN)
        self.assertEqual(self.acting(base), ("explaining", "engaged", "explaining"))
        expected = {
            "introduces": ("welcoming", "happy", "welcome"),
            "explains": ("explaining", "engaged", "explaining"),     # the Director's default
            "points_to_visual": ("explaining", "engaged", "point"),
            "points_to_board": ("explaining", "engaged", "point"),
            "pauses_for_visual": ("listening", "engaged", "none"),
            "summarizes": ("concluding", "encouraging", "open_hand"),
            "asks": ("listening", "encouraging", "explaining"),
            "none": ("explaining", "engaged", "explaining"),
        }
        self.assertEqual(set(expected), set(P.DIRECTION_INTERACTIONS))
        for interaction, acting in expected.items():
            plan = self.plan(directed(PLAIN, interaction))
            self.assertEqual(self.acting(plan), acting, interaction)
            self.assertEqual({k: plan.get(k) for k in PLACE}, {k: base.get(k) for k in PLACE}, interaction)
            self.assertNotIn("fallbacks", plan)  # the teacher can show all of it
            if acting == self.acting(base):
                self.assertEqual(plan, base, interaction)  # nothing asked: the very same plan
            else:
                self.assertTrue(plan["reason"].startswith(base["reason"] + "; visual direction: "), plan["reason"])
        self.assertTrue(self.plan(directed(PLAIN, "points_to_visual"))["reason"].endswith("points to the visual"))

    def test_style_and_narration_still_refine_the_directed_choice(self):
        self.assertEqual(self.plan(directed(PLAIN, "introduces"), settings={**self.AUTO, "style": "formal"})["expression"], "friendly")
        counted = {**PLAIN, "narration": "In summary, there are three steps."}
        self.assertEqual(self.acting(self.plan(directed(counted, "summarizes"))), ("concluding", "encouraging", "counting"))
        self.assertEqual(self.plan(directed(counted, "points_to_board"))["gesture"], "point")  # pointing is not re-counted

    def test_the_mascot_chooses_nothing_it_cannot_show(self):
        aadhi = P.BUILTIN["aadhi"]
        caps = aadhi["capabilities"]
        base = self.plan(PLAIN, aadhi)
        for interaction in P.DIRECTION_INTERACTIONS:
            plan = self.plan(directed(PLAIN, interaction), aadhi)
            self.assertEqual(plan["gesture"], base["gesture"], interaction)  # Aadhi has no gestures: none is chosen
            self.assertIsNone(plan["gesture_shown"])
            if plan["expression"] != base["expression"]:
                self.assertIn(plan["expression"], caps["expressions"], interaction)
                self.assertNotIn("expression_shown", plan)
            self.assertEqual({k: plan.get(k) for k in PLACE}, {k: base.get(k) for k in PLACE}, interaction)
        self.assertEqual(self.acting(base), ("explaining", "engaged", "explaining"))
        expected = {  # Aadhi: no gestures at all, and of the expressions only neutral, friendly, thinking, happy
            "introduces": ("welcoming", "happy", "explaining"),
            "explains": ("explaining", "engaged", "explaining"),
            "points_to_visual": ("explaining", "engaged", "explaining"),
            "points_to_board": ("explaining", "engaged", "explaining"),
            "pauses_for_visual": ("listening", "engaged", "explaining"),
            "summarizes": ("concluding", "engaged", "explaining"),
            "asks": ("listening", "engaged", "explaining"),
            "none": ("explaining", "engaged", "explaining"),
        }
        self.assertEqual(set(expected), set(P.DIRECTION_INTERACTIONS))
        for interaction, acting in expected.items():
            plan = self.plan(directed(PLAIN, interaction), aadhi)
            self.assertEqual(self.acting(plan), acting, interaction)
            if acting == self.acting(base):
                self.assertEqual(plan, base, interaction)  # nothing Aadhi can show was asked: the very same plan
        self.assertEqual(self.acting(self.plan(directed(PLAIN, "introduces"), aadhi)), ("welcoming", "happy", "explaining"))
        asks = self.plan(directed(PLAIN, "asks"), aadhi)  # "encouraging" is not Aadhi's: the Director's own stays
        self.assertEqual((asks["behavior"], asks["expression"], asks.get("expression_shown")), ("listening", "engaged", "friendly"))
        # A presenter whose provider can show a few things: those, and nothing else
        limited = {**P.BUILTIN["ai-teacher"], "capabilities": {"expressions": ["neutral", "friendly", "engaged"], "gestures": ["none", "open_hand", "point"]}}
        self.assertEqual(self.plan(directed(PLAIN, "points_to_visual"), limited)["gesture"], "point")
        intro = self.plan(directed(PLAIN, "introduces"), limited)
        self.assertEqual(self.acting(intro), ("welcoming", "engaged", "explaining"))  # no "happy", no "welcome" here
        self.assertEqual(intro.get("gesture_shown"), "open_hand")  # the Director's own gesture, shown as before

    def test_explicit_choices_beat_the_direction(self):
        # The screenplay ("As the lesson says"): its placement and acting stay
        for scene in scenes():
            for interaction in P.DIRECTION_INTERACTIONS:
                for profile in (TEACHER, P.BUILTIN["aadhi"]):
                    lesson = {"mode": "lesson", "position": "right"}
                    self.assertEqual(self.plan(directed(scene, interaction, role="dominant"), profile, lesson),
                                     self.plan(scene, profile, lesson), (scene.get("title"), interaction))
        mood = {**PLAIN, "mascot_state": "thinking"}
        self.assertEqual(self.plan(directed(mood, "introduces"), settings={"mode": "lesson"})["behavior"], "thinking")
        # The user's own expression and gesture (Advanced settings)
        chosen = self.plan(directed(PLAIN, "points_to_visual"), settings={**self.AUTO, "expression": "serious", "gesture": "emphasis"})
        self.assertEqual((chosen["expression"], chosen["gesture"]), ("serious", "emphasis"))
        # Visual Review: removed stays removed, a moved presenter stays where the review put it
        removed = self.plan(directed({**PLAIN, "visual_review": {"presenter": {"status": "removed"}}}, "introduces", role="dominant"))
        self.assertEqual((removed["enabled"], removed["position"], removed["source"], removed["box"]), (False, "hidden", "review", None))
        moved = self.plan(directed({**PLAIN, "visual_review": {"presenter": {"status": "changed", "position": "left"}}}, "points_to_board", role="dominant"))
        self.assertEqual((moved["position"], moved["layout"], moved["source"], moved["review_status"]), ("left", "left", "review", "changed"))
        self.assertEqual(moved["gesture"], "point")  # the review chose where, not what the presenter does
        small = self.plan(directed({**PLAIN, "visual_review": {"presenter": {"status": "approved", "position": "pip"}}}, "introduces", role="dominant"))
        self.assertEqual((small["placement"], small["review_status"]), ("pip", "approved"))
        # Presenter off
        self.assertEqual(self.plan(directed(PLAIN, "introduces"), settings={"mode": "off"}), self.plan(PLAIN, settings={"mode": "off"}))

    def test_invalid_directions_are_ignored(self):
        base = self.plan(PLAIN)
        good = {"version": 1, "presenter": {"role": "guide", "interaction": "points_to_visual"}}
        self.assertEqual(self.plan({**PLAIN, "visual_direction": good})["gesture"], "point")
        bad = ["points_to_visual", ["points_to_visual"], None, 7,
               {**good, "version": 2}, {**good, "version": True}, {**good, "version": "1"}, {"presenter": good["presenter"]},
               {**good, "presenter": "points_to_visual"}, {**good, "presenter": None}, {**good, "presenter": ["guide", "points_to_visual"]},
               {**good, "presenter": {"interaction": "points_to_visual"}}, {**good, "presenter": {"role": "star", "interaction": "points_to_visual"}},
               {**good, "presenter": {"role": ["guide"], "interaction": "points_to_visual"}},
               {**good, "presenter": {"role": "hidden", "interaction": "points_to_visual"}},  # a hidden presenter does nothing
               {**good, "presenter": {"role": "guide", "interaction": "dance"}}, {**good, "presenter": {"role": "guide", "interaction": "point"}},
               {**good, "presenter": {"role": "guide", "interaction": "POINTS_TO_VISUAL"}}, {**good, "presenter": {"role": "guide", "interaction": None}},
               {**good, "presenter": {"role": "guide", "interaction": ["points_to_visual"]}},
               {**good, "presenter": {"role": "guide", "interaction": {"gesture": "point"}}}]
        for value in bad:
            self.assertEqual(self.plan({**PLAIN, "visual_direction": value}), base, value)
        extra = {**good, "presenter": {**good["presenter"], "gesture": "emphasis", "enabled": False, "position": "left"}}
        plan = self.plan({**PLAIN, "visual_direction": extra})  # only the interaction is read
        self.assertEqual((plan["gesture"], plan["enabled"], plan["position"]), ("point", True, "right"))

    def test_no_direction_keeps_plans_and_fingerprints(self):
        import cinematic as C
        for profile in (TEACHER, P.BUILTIN["aadhi"]):
            for mode in ("auto", "always", "lesson", "off"):
                settings = {"mode": mode, "position": "left"}
                plain = P.plan_lesson(scenes(), profile, settings)
                for interaction in ("explains", "none"):  # nothing asked: the same plans as without a direction
                    self.assertEqual(P.plan_lesson([directed(s, interaction) for s in scenes()], profile, settings), plain)
                self.assertTrue(all("fingerprint" not in p for p in plain))  # a presenter plan has no fingerprint of its own
        # The composition's fingerprint (Phase 13/14 review staleness) reads only whether, who and where: the directed
        # acting never re-opens a composition approval
        cine = {"presenter_legacy": False, "presenter_id": "aadhi-teacher", "presenter_position": "right"}
        for scene in scenes():
            before = P.plan_scene(scene, 1, 8, TEACHER, self.AUTO)
            for interaction in P.DIRECTION_INTERACTIONS:
                for role in P.DIRECTION_ROLES:
                    after_scene = directed(scene, interaction, role=role)
                    after = P.plan_scene(after_scene, 1, 8, TEACHER, self.AUTO)
                    ctx_before = C.presenter_context({**after_scene, "presenter_plan": before}, cine)
                    ctx_after = C.presenter_context({**after_scene, "presenter_plan": after}, cine)
                    self.assertEqual(ctx_after, ctx_before, (scene.get("title"), interaction, role))
                    self.assertEqual(C.input_fingerprint(after_scene, {}, ctx_after), C.input_fingerprint(after_scene, {}, ctx_before))

    def test_the_role_never_changes_whether_or_where(self):
        for profile in (TEACHER, P.BUILTIN["aadhi"]):
            for mode in ("auto", "always"):
                for position in ("left", "right"):
                    settings = {"mode": mode, "position": position}
                    plain = P.plan_lesson(scenes(), profile, settings)
                    for role in P.DIRECTION_ROLES:
                        for interaction in P.DIRECTION_INTERACTIONS:
                            plans = P.plan_lesson([directed(s, interaction, role=role) for s in scenes()], profile, settings)
                            for i, (a, b) in enumerate(zip(plans, plain)):
                                self.assertEqual({k: a.get(k) for k in PLACE}, {k: b.get(k) for k in PLACE}, (profile["id"], mode, role, interaction, i))
                                if not b["enabled"]:
                                    self.assertEqual(a, b)  # a presenter that does not appear does nothing
        code = self.plan(directed(scenes()[5], "introduces", role="dominant"))  # code needs the canvas: still hidden
        self.assertEqual((code["enabled"], code["position"], code["gesture"]), (False, "hidden", "none"))


NO_ACTING = {"acts": False, "gestures": [], "expressions": [], "states": []}


class SyncActingTest(unittest.TestCase):
    """Acting at a moment (Phase 16 synchronization): what each presenter can really change mid-scene
    (sync_capabilities), and a semantic action as only those values, or None (acting_for)."""

    TEACHER_ACTING = {
        "introduce": {"gesture": "welcome", "expression": "happy", "state": None},
        "point": {"gesture": "point", "expression": None, "state": None},           # the face stays as it is
        "explain": {"gesture": "explaining", "expression": "engaged", "state": None},
        "pause": {"gesture": "none", "expression": None, "state": None},
        "summarize": {"gesture": "open_hand", "expression": "encouraging", "state": None},
        "emphasis": {"gesture": "emphasis", "expression": "engaged", "state": None},
    }
    AADHI_ACTING = {  # only his narration states; what needs a gesture he cannot do
        "introduce": {"gesture": None, "expression": None, "state": "talking"},
        "point": None,
        "explain": {"gesture": None, "expression": None, "state": "explaining"},
        "pause": {"gesture": None, "expression": None, "state": "thinking"},
        "summarize": {"gesture": None, "expression": None, "state": "explaining"},
        "emphasis": None,
    }
    PROVIDER_CAPS = {"speech": "lip_sync", "lip_sync": True, "expressions": ["neutral", "happy"], "gestures": ["none", "point"],
                     "provider": "fake-presenter"}

    def test_capabilities_per_presenter_type(self):
        teacher = P.sync_capabilities("illustrated")
        self.assertEqual(teacher, {"acts": True, "gestures": list(P.GESTURES), "expressions": list(P.EXPRESSIONS), "states": []})
        self.assertEqual(P.sync_capabilities("illustrated", TEACHER), teacher)
        self.assertEqual(P.sync_capabilities(None, TEACHER), teacher)  # the profile's own type
        self.assertEqual(P.sync_capabilities("illustrated", {**TEACHER, "capabilities": None}), teacher)  # the built-in teacher's
        aadhi = P.sync_capabilities("mascot", P.BUILTIN["aadhi"])
        self.assertEqual(aadhi, {"acts": True, "gestures": [], "expressions": [], "states": ["talking", "explaining", "thinking", "idle"]})
        self.assertEqual(P.sync_capabilities("mascot"), aadhi)  # his per-scene expressions are clips: none at a moment
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mascot.js"), encoding="utf-8") as f:
            mascot_states = re.search(r"const STATES = \[([^\]]*)\]", f.read()).group(1)
        self.assertLessEqual(set(aadhi["states"]), set(re.findall(r"'(\w+)'", mascot_states)))  # MascotController's own states
        # A pre-rendered clip cannot change mid-scene, whatever its provider can show in a clip
        for kind, profile in (("ai_avatar", {**P.BUILTIN["ai-teacher"], "capabilities": self.PROVIDER_CAPS}), ("ai_avatar", P.BUILTIN["ai-teacher"]),
                              ("custom", {"type": "custom", "capabilities": self.PROVIDER_CAPS}), ("custom", None), ("ai_avatar", None)):
            self.assertEqual(P.sync_capabilities(kind, profile), NO_ACTING, kind)
        self.assertEqual(P.sync_capabilities(None, {**P.BUILTIN["ai-teacher"], "capabilities": self.PROVIDER_CAPS}), NO_ACTING)
        for unknown in (None, "", "robot", "ILLUSTRATED", "hidden", 7, ["illustrated"], {"type": "illustrated"}):
            self.assertEqual(P.sync_capabilities(unknown), NO_ACTING, unknown)

    def test_a_drawn_profile_limits_what_it_can_do(self):
        few = {"type": "illustrated", "capabilities": {"expressions": ["happy", "neutral", "angry"], "gestures": ["point", "dance"]}}
        self.assertEqual(P.sync_capabilities("illustrated", few),  # vocabulary order; nothing outside the vocabulary
                         {"acts": True, "gestures": ["point"], "expressions": ["neutral", "happy"], "states": []})
        self.assertEqual(P.sync_capabilities("illustrated", {"type": "illustrated", "capabilities": {"gestures": [], "expressions": []}}), NO_ACTING)
        self.assertEqual(P.sync_capabilities("illustrated", {"type": "illustrated", "capabilities": {"gestures": "point", "expressions": None}}), NO_ACTING)
        before = json.dumps(P.BUILTIN, sort_keys=True)
        caps = P.sync_capabilities("illustrated")
        caps["gestures"].append("dance")
        P.sync_capabilities("mascot")["states"].append("dancing")
        self.assertEqual(json.dumps(P.BUILTIN, sort_keys=True), before)  # fresh lists: nothing shared
        self.assertEqual(P.sync_capabilities("illustrated")["gestures"], list(P.GESTURES))
        self.assertEqual(P.sync_capabilities("mascot")["states"], list(P.SYNC_MASCOT_STATES))

    def test_actions_per_presenter_type(self):
        self.assertEqual(set(P.ACTION_ACTING), set(P.SYNC_ACTIONS))
        self.assertEqual(set(self.TEACHER_ACTING), set(P.SYNC_ACTIONS))
        teacher, aadhi = P.sync_capabilities("illustrated", TEACHER), P.sync_capabilities("mascot", P.BUILTIN["aadhi"])
        for action in P.SYNC_ACTIONS:
            self.assertEqual(P.acting_for(action, teacher), self.TEACHER_ACTING[action], action)
            self.assertEqual(P.acting_for(action, aadhi), self.AADHI_ACTING[action], action)
            for caps in (P.sync_capabilities("ai_avatar", {**P.BUILTIN["ai-teacher"], "capabilities": self.PROVIDER_CAPS}),
                         P.sync_capabilities("custom"), P.sync_capabilities("robot")):
                self.assertIsNone(P.acting_for(action, caps), action)  # an AI clip never acts: the visual gets the moment
        json.dumps([P.acting_for(a, c) for a in P.SYNC_ACTIONS for c in (teacher, aadhi)])
        # The existing vocabularies, and the Phase 15 interaction acting where one fits
        for expression, gesture, state in P.ACTION_ACTING.values():
            self.assertIn(expression, P.EXPRESSIONS + (None,))
            self.assertIn(gesture, P.GESTURES)
            self.assertIn(state, P.SYNC_MASCOT_STATES + (None,))
        for action, interaction in (("introduce", "introduces"), ("point", "points_to_visual"), ("pause", "pauses_for_visual"), ("summarize", "summarizes")):
            self.assertEqual(P.ACTION_ACTING[action][:2], P.INTERACTION_ACTING[interaction][1:3], action)
        self.assertEqual(P.ACTION_ACTING["explain"][:2], (P.ROLE_RULES["explanation"]["expression"], P.ROLE_RULES["explanation"]["gesture"]))

    def test_only_what_the_presenter_can_do_never_a_stand_in(self):
        few = P.sync_capabilities("illustrated", {"type": "illustrated", "capabilities": {"expressions": ["neutral", "friendly"], "gestures": ["none", "open_hand"]}})
        self.assertIsNone(P.acting_for("point", few))  # no pointing hand: not an open hand instead
        self.assertIsNone(P.acting_for("introduce", few))
        self.assertIsNone(P.acting_for("emphasis", few))
        self.assertIsNone(P.acting_for("explain", few))
        self.assertEqual(P.acting_for("summarize", few), {"gesture": "open_hand", "expression": None, "state": None})  # no "encouraging"
        self.assertEqual(P.acting_for("pause", few), {"gesture": "none", "expression": None, "state": None})
        for kind in ("illustrated", "mascot", "ai_avatar", "custom", "robot"):
            caps = P.sync_capabilities(kind)
            for action in P.SYNC_ACTIONS:
                acting = P.acting_for(action, caps)
                if acting is None:
                    continue
                self.assertTrue(caps["acts"])
                self.assertEqual(set(acting), {"gesture", "expression", "state"})
                for key, vocab in (("gesture", "gestures"), ("expression", "expressions"), ("state", "states")):
                    self.assertIn(acting[key], caps[vocab] + [None], (kind, action, key))
        teacher = P.sync_capabilities("illustrated")
        for action in (None, "", 3, "POINT", "dance", "points_to_visual", ["point"]):  # an interaction is not an action
            self.assertIsNone(P.acting_for(action, teacher), action)
        for caps in (None, [], "illustrated", {}, {**teacher, "acts": False}, {**teacher, "acts": "yes"}, {**teacher, "acts": 1},
                     {"acts": True, "gestures": "point", "expressions": "happy", "states": "thinking"}):
            self.assertIsNone(P.acting_for("point", caps), caps)

    def test_plans_without_acting_are_unchanged(self):
        known = {"presenter_id", "profile_version", "type", "role", "speech", "source", "version", "enabled", "position", "placement",
                 "reason", "behavior", "expression", "gesture", "layout", "review_status", "box", "composition_note", "media_asset_id",
                 "expression_shown", "gesture_shown", "fallbacks"}
        before = json.dumps(P.BUILTIN, sort_keys=True)
        for profile in (TEACHER, P.BUILTIN["aadhi"], P.BUILTIN["ai-teacher"]):
            for mode in P.MODES:
                plans = P.plan_lesson(scenes(), profile, {"mode": mode, "position": "right"})
                for p in plans:
                    self.assertLessEqual(set(p), known)  # a scene's plan has no acting of its own: that is the sync plan's
                    caps = P.sync_capabilities(p["type"], profile)
                    [P.acting_for(a, caps) for a in P.SYNC_ACTIONS]
                self.assertEqual(P.plan_lesson(scenes(), profile, {"mode": mode, "position": "right"}), plans)
        self.assertEqual(json.dumps(P.BUILTIN, sort_keys=True), before)


class SpeechTest(unittest.TestCase):
    def test_segments_match_the_page(self):
        self.assertEqual(P.parse_segments("One. [PAUSE] Two. [PAUSE:2.5] Three."),
                         [{"text": "One.", "pause_after": 1.5}, {"text": "Two.", "pause_after": 2.5}, {"text": "Three.", "pause_after": 0.0}])
        self.assertEqual(P.parse_segments("[PAUSE:1] [PAUSE:2] Only."), [{"text": "Only.", "pause_after": 0.0}])
        self.assertEqual(P.parse_segments("A. [PAUSE:1][PAUSE:2]"), [{"text": "A.", "pause_after": 3.0}])

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_timeline(self):
        path_of = server._static_path
        timeline = asyncio.run(P.speech_timeline("In summary, plants use light. [PAUSE:1] They make glucose.", "v", "default",
                                                 lambda t, v, e: fake_tts(server.AudioRequest(text=t, voice=v, tts_engine=e), None), path_of))
        first, second = timeline["segments"]
        self.assertAlmostEqual(second["start"], first["duration"] + 1.0, places=2)
        self.assertEqual(second["envelope_start"], int(round(first["duration"] * 25)) + 25)
        self.assertAlmostEqual(len(timeline["envelope"]) / 25, timeline["duration"], delta=0.1)
        self.assertGreater(max(timeline["envelope"]), 0.5)  # loud parts
        self.assertEqual(min(timeline["envelope"]), 0.0)    # the pause
        other = asyncio.run(P.speech_timeline("Something else.", "v", "default",
                                              lambda t, v, e: fake_tts(server.AudioRequest(text=t, voice=v, tts_engine=e), None), path_of))
        self.assertNotEqual(other["audio_sha256"], timeline["audio_sha256"])
        self.assertFalse(any("?" in s["audio_url"] for s in timeline["segments"]))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class GenerationTest(unittest.TestCase):
    """Presenter clips through the AI media layer with the stand-in presenter provider."""

    @classmethod
    def setUpClass(cls):
        assert_isolated()
        cls.uid = ensure_user("petra")
        ensure_user("otto")
        cls.state = tempfile.mkdtemp(prefix="aadhi-presenter-jobs-", dir=TMP)
        cls.client = TestClient(server.app)
        cls.tts = mock.patch.object(server, "_generate_audio", side_effect=fake_tts)
        cls.tts.start()

    @classmethod
    def tearDownClass(cls):
        cls.tts.stop()
        with database.SessionLocal() as db:
            for asset in db.query(models.Asset).filter(models.Asset.source == "ai-presenter"):
                path = os.path.join(server.STATIC_DIR, asset.storage_key)
                if os.path.exists(path):
                    os.remove(path)

    def setUp(self):
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.status.in_(list(ai_runs.ACTIVE))).update(
                {"status": "cancelled", "lease_owner": None, "lease_expires_at": None}, synchronize_session=False)
            db.commit()

    def service(self, **extra):
        env = {"AI_FAKE_STATE_DIR": self.state, "AI_JOB_LEASE_SECONDS": "30", "AI_JOB_HEARTBEAT_SECONDS": "5",
               "AI_JOB_CONCURRENCY": "4", "FAKE_PRESENTER_POLL_SECONDS": "0.05", "AI_PROVIDER_RETRY_DELAY": "0.01", **extra}
        registry = ai_providers.ProviderRegistry(env=env, video_mode=lambda: "fake", fake=True)
        media = AIMediaService(server.asset_library, server.ai_cache, registry, static_dir=server.STATIC_DIR,
                               generation_enabled=lambda: True, env=env, log=False)
        media.is_wanted = server._still_wanted
        media.on_completed = server._lesson_after_generation
        return media, RecoveryManager(media, env=env)

    def request(self, narration=None, plan=None, profile=None, **kw):
        narration = narration or f"Presenter test {uuid.uuid4().hex[:6]}: plants make sugar."
        profile = profile or P.BUILTIN["ai-teacher"]
        timeline = asyncio.run(P.speech_timeline(narration, "v", "default",
                                                 lambda t, v, e: fake_tts(server.AudioRequest(text=t, voice=v, tts_engine=e), None), server._static_path))
        plan = plan or {"behavior": "explaining", "expression": "engaged", "gesture": "open_hand"}
        return P.presenter_request(profile, plan, timeline, narration, **kw)

    def generate(self, media, request):
        async def go():
            with database.SessionLocal() as db:
                outcome = await media.generate(db, db.get(models.User, self.uid), request)
                return {"asset": outcome.asset.id, "hit": outcome.cache_hit, "provider": outcome.provider, "warnings": outcome.warnings}
        return asyncio.run(go())

    def jobs(self):
        return [f for f in os.listdir(self.state) if f.startswith("fake-presenter-")]

    def test_capabilities_are_reported_honestly(self):
        media, _ = self.service()
        view = P.provider_view(media.registry)
        self.assertEqual([p["name"] for p in view["providers"]], ["fake-presenter"])
        caps = view["providers"][0]["capabilities"]
        self.assertEqual((caps["lip_sync"], caps["transparent_background"], caps["reference_image"]), (True, False, False))
        real = ai_providers.ProviderRegistry(env={}, video_mode=lambda: None)
        real_view = P.provider_view(real)
        self.assertEqual((real_view["providers"], real_view["available"]), ([], False))
        self.assertIn("isn't configured yet", real_view["message"])
        self.assertIn("veo", [p["name"] for p in real_view["not_presenter_capable"]])  # makes video, cannot speak our narration
        teacher = P.profile_view(P.BUILTIN["ai-teacher"], real)
        self.assertEqual((teacher["available"], teacher["capabilities"]), (False, None))
        drawn = P.profile_view(P.BUILTIN["aadhi-teacher"], real)
        self.assertEqual((drawn["available"], drawn["capabilities"]["speech"], drawn["capabilities"]["lip_sync"]), (True, "audio_envelope", False))

    def test_generation_cache_new_version_and_provenance(self):
        media, _ = self.service()
        request = self.request(plan={"behavior": "explaining", "expression": "surprised", "gesture": "counting"})
        first = self.generate(media, request)
        self.assertEqual((first["hit"], first["provider"]), (False, "fake-presenter"))
        self.assertEqual(sorted(first["warnings"]), ["counting shown as explaining", "surprised shown as engaged"])
        with database.SessionLocal() as db:
            asset = db.get(models.Asset, first["asset"])
            details = json.loads(asset.details)
            self.assertEqual((asset.source, asset.kind, asset.width, asset.height), ("ai-presenter", "video", 480, 800))
            self.assertAlmostEqual(asset.duration_seconds, request.duration_seconds, delta=0.2)
            self.assertEqual(details["presenter"]["presenter_id"], "ai-teacher")
            self.assertTrue(details["presenter"]["lip_sync"])
            self.assertEqual(details["generation"]["parameters"]["audio"], request.presenter["speech"]["audio_sha256"])
            self.assertNotIn("envelope", json.dumps(details))  # the asset keeps provenance, not the speech data
        again = self.generate(media, self.request(narration=request.prompt, plan={"behavior": "explaining", "expression": "surprised", "gesture": "counting"}))
        self.assertEqual((again["asset"], again["hit"]), (first["asset"], True))  # the same request: no second clip
        forced = self.request(narration=request.prompt, plan={"behavior": "explaining", "expression": "surprised", "gesture": "counting"}, force=True)
        new = self.generate(media, forced)
        self.assertNotEqual(new["asset"], first["asset"])
        with database.SessionLocal() as db:
            self.assertEqual(db.get(models.Asset, first["asset"]).status, "ready")  # the earlier version stays

    def test_cache_identity(self):
        media, _ = self.service()
        base = self.request(narration="Identity test: plants.")
        candidate = media.registry.select(base).usable[0]
        identity = lambda r: media.identity(candidate, r)[1]  # noqa: E731
        self.assertEqual(identity(base), identity(self.request(narration="Identity test: plants.")))
        self.assertNotEqual(identity(base), identity(self.request(narration="Identity test: animals.")))  # other speech
        self.assertNotEqual(identity(base), identity(self.request(narration="Identity test: plants.",
                                                                  plan={"behavior": "explaining", "expression": "happy", "gesture": "open_hand"})))
        moved = self.request(narration="Identity test: plants.")
        moved_plan = {"behavior": "explaining", "expression": "engaged", "gesture": "open_hand", "position": "left"}
        self.assertEqual(identity(base), identity(self.request(narration="Identity test: plants.", plan=moved_plan)))  # position: layout only
        other_profile = {**P.BUILTIN["ai-teacher"], "version": 2}
        self.assertNotEqual(identity(base), identity(self.request(narration="Identity test: plants.", profile=other_profile)))
        self.assertEqual(identity(moved), identity(base))

    def test_unsupported_and_explicit_providers(self):
        media, _ = self.service()
        real, _ = self.service()
        real.registry = ai_providers.ProviderRegistry(env={}, video_mode=lambda: None)
        with self.assertRaises(GenerationFailed) as caught:
            self.generate(real, self.request())
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("isn't configured yet", caught.exception.message)
        with self.assertRaises(GenerationFailed) as caught:
            self.generate(media, self.request(provider="fake"))  # a video stand-in: cannot present
        self.assertEqual(caught.exception.status, 422)
        with self.assertRaises(GenerationFailed) as caught:
            self.generate(media, self.request(provider="nobody"))
        self.assertEqual(caught.exception.status, 400)
        failing, _ = self.service(AI_FAKE_FAIL="fake-presenter:presenter:unavailable")
        with self.assertRaises(GenerationFailed):
            self.generate(failing, self.request(provider="fake-presenter", allow_fallback=False))  # no silent substitute

    def test_recovery_resumes_the_same_job(self):
        old, _ = self.service(FAKE_PRESENTER_JOB_SECONDS="1.5")
        request = self.request()

        async def start_and_crash():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                with mock.patch.object(old, "_crash", side_effect=lambda p: (_ for _ in ()).throw(Crash(p)) if p == "after_job_saved" else None):
                    _o, run = old.start_background(db, user, request)
                    try:
                        await old.tasks[run.id]
                    except Crash:
                        pass
                return run.id
        run_id = asyncio.run(start_and_crash())
        with database.SessionLocal() as db:
            db.query(models.AIGenerationRun).filter(models.AIGenerationRun.id == run_id).update(
                {"lease_expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)})
            db.commit()
            job_before = db.get(models.AIGenerationRun, run_id).provider_job_id
        jobs_before = len(self.jobs())
        new, manager = self.service(FAKE_PRESENTER_JOB_SECONDS="1.5")

        async def recover():
            for task in await manager.sweep():
                await task
        asyncio.run(recover())
        with database.SessionLocal() as db:
            run = db.get(models.AIGenerationRun, run_id)
            self.assertEqual((run.status, run.recovery_count, run.provider_job_id, run.media_type), ("completed", 1, job_before, "presenter"))
            self.assertEqual(json.loads(run.request)["presenter"]["presenter_id"], "ai-teacher")  # the request survived
        self.assertEqual(len(self.jobs()), jobs_before)  # no second provider job

    def test_cancellation_and_duplicates(self):
        media, _ = self.service(FAKE_PRESENTER_JOB_SECONDS="30")
        request = self.request()
        identical = self.request(narration=request.prompt)

        async def go():
            with database.SessionLocal() as db:
                user = db.get(models.User, self.uid)
                _o, run = media.start_background(db, user, request)
                _o2, twin = media.start_background(db, user, identical)
                await asyncio.sleep(0.4)
                answer = media.request_cancel(db, db.get(models.AIGenerationRun, run.id))
                try:
                    await asyncio.wait_for(media.tasks.get(run.id) or asyncio.sleep(0), 10)
                except Exception:  # noqa: BLE001
                    pass
                db.expire_all()
                return run.id, twin.id, answer, db.get(models.AIGenerationRun, run.id).status
        run_id, twin_id, answer, status = asyncio.run(go())
        self.assertEqual(run_id, twin_id)  # the identical request attached to the running one
        self.assertEqual((answer, status), ("cancelling", "cancelled"))
        self.assertGreaterEqual(getattr(media.registry.get("fake-presenter"), "cancelled", 0), 1)  # the provider's job was cancelled

    def test_generated_clip_lands_in_the_lesson_unless_removed(self):
        media, _ = self.service()
        pid = self.client.post("/save-history", json={"subject_name": "P", "scenes": scenes()}, headers=auth("petra")).json()["id"]
        first = self.generate(media, self.request(project_id=pid, scene_index=2))
        saved = self.client.get(f"/api/projects/{pid}", headers=auth("petra")).json()
        self.assertEqual(saved["scenes"][2]["presenter_plan"]["media"]["asset_id"], first["asset"])
        refs = self.client.get(f"/api/assets/{first['asset']}", headers=auth("petra")).json()
        self.assertTrue(refs.get("used_in"))  # in use: it cannot be deleted while the lesson shows it
        self.assertEqual(self.client.delete(f"/api/assets/{first['asset']}", headers=auth("petra")).status_code, 409)
        with database.SessionLocal() as db:  # removed in review: recovery would not make it
            project = db.get(models.Project, pid)
            payload = json.loads(project.json_data)
            payload["scenes"][3]["visual_review"] = {"presenter": {"status": "removed"}}
            project.json_data = json.dumps(payload)
            db.commit()
            run = models.AIGenerationRun(project_id=pid, scene_index=3, user_id=self.uid, slot="presenter")
            self.assertEqual(P.presenter_still_wanted(db, run)[0], False)
            run.scene_index = 2
            self.assertEqual(P.presenter_still_wanted(db, run)[0], True)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("petra")
        ensure_user("otto")
        cls.client = TestClient(server.app)
        cls.tts = mock.patch.object(server, "_generate_audio", side_effect=fake_tts)
        cls.tts.start()

    @classmethod
    def tearDownClass(cls):
        cls.tts.stop()

    def test_presenters_list(self):
        body = self.client.get("/api/presenters", headers=auth("petra")).json()
        self.assertEqual([p["id"] for p in body["profiles"][:3]], ["aadhi", "aadhi-teacher", "ai-teacher"])
        ai = body["profiles"][2]
        self.assertEqual(ai["available"], False)  # no presenter provider on this server
        self.assertIn("isn't configured yet", ai["unavailable_reason"])
        self.assertEqual(body["vocabulary"]["expressions"], list(P.EXPRESSIONS))
        self.assertNotIn("key", json.dumps(body).lower().replace("keep", ""))  # never a secret
        self.assertEqual(self.client.get("/api/presenters").status_code, 401)

    def test_plan_endpoint_and_validation(self):
        r = self.client.post("/api/presenters/plan", json={"scenes": scenes(), "settings": {"presenter_id": "aadhi-teacher", "mode": "auto"}}, headers=auth("petra"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["plans"]), 8)
        for bad in ({"mode": "sometimes"}, {"style": "loud"}, {"position": "top"}, {"expression": "angry"}, {"gesture": "wave"}):
            r = self.client.post("/api/presenters/plan", json={"scenes": scenes(), "settings": {"presenter_id": "aadhi-teacher", **bad}}, headers=auth("petra"))
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(self.client.post("/api/presenters/plan", json={"scenes": scenes(), "settings": {"presenter_id": "nobody"}},
                                          headers=auth("petra")).status_code, 404)

    def test_generate_refuses_honestly(self):
        r = self.client.post("/api/presenters/generate", json={"scene": scenes()[1], "settings": {"presenter_id": "ai-teacher"}}, headers=auth("petra"))
        self.assertEqual(r.status_code, 503)  # no presenter provider configured: nothing faked
        self.assertIn("isn't configured yet", r.json()["detail"])
        r = self.client.post("/api/presenters/generate", json={"scene": scenes()[1], "settings": {"presenter_id": "aadhi-teacher"}}, headers=auth("petra"))
        self.assertEqual(r.status_code, 422)  # drawn by the page: nothing to generate
        r = self.client.post("/api/presenters/generate", json={"scene": {"title": "x"}, "settings": {"presenter_id": "ai-teacher"}}, headers=auth("petra"))
        self.assertIn(r.status_code, (422, 503))

    def test_speech_endpoint(self):
        r = self.client.post("/api/presenters/speech", json={"text": "Hello there. [PAUSE:1] Again."}, headers=auth("petra"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["segments"]), 2)
        self.assertEqual(self.client.post("/api/presenters/speech", json={"text": "  "}, headers=auth("petra")).status_code, 422)

    def test_review_endpoint_and_access(self):
        pid = self.client.post("/save-history", json={"subject_name": "R", "scenes": scenes()}, headers=auth("petra")).json()["id"]
        settings = {"presenter_id": "aadhi-teacher", "mode": "auto"}
        r = self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "move", "position": "left", "settings": settings}, headers=auth("petra"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["review"]["status"], r.json()["plan"]["position"], r.json()["plan"]["layout"]), ("changed", "left", "left"))
        r = self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "remove", "settings": settings}, headers=auth("petra")).json()
        self.assertEqual((r["review"]["status"], r["plan"]["enabled"]), ("removed", False))
        stored = self.client.get(f"/api/projects/{pid}", headers=auth("petra")).json()["scenes"][2]
        self.assertEqual(stored["visual_review"]["presenter"]["status"], "removed")
        self.assertFalse(stored["presenter_plan"]["enabled"])
        r = self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "reset", "settings": settings}, headers=auth("petra")).json()
        self.assertEqual((r["review"], r["plan"]["enabled"]), (None, True))
        self.assertEqual(self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "choose", "settings": settings},
                                          headers=auth("petra")).status_code, 422)  # choose needs a clip or picture
        self.assertEqual(self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "keep", "settings": settings},
                                          headers=auth("otto")).status_code, 404)  # another user's lesson
        self.assertEqual(self.client.post("/api/presenters/review", json={"project_id": pid, "scene_index": 2, "action": "dance", "settings": settings},
                                          headers=auth("petra")).status_code, 422)

    def test_custom_profiles(self):
        r = self.client.post("/api/presenters/profiles", json={"name": "Ms Rao", "description": "A science teacher", "palette": ["#112233", "bad"]}, headers=auth("petra"))
        self.assertEqual(r.status_code, 200)
        profile = r.json()
        self.assertEqual((profile["type"], profile["appearance"]["palette"], profile["available"]), ("custom", ["#112233"], False))
        listed = [p["id"] for p in self.client.get("/api/presenters", headers=auth("petra")).json()["profiles"]]
        self.assertIn(profile["id"], listed)
        self.assertNotIn(profile["id"], [p["id"] for p in self.client.get("/api/presenters", headers=auth("otto")).json()["profiles"]])
        self.assertEqual(self.client.post("/api/presenters/plan", json={"scenes": scenes(), "settings": {"presenter_id": profile["id"]}},
                                          headers=auth("otto")).status_code, 404)
        self.assertEqual(self.client.post("/api/presenters/profiles", json={"name": "x", "reference_asset_id": "b" * 32}, headers=auth("petra")).status_code, 422)
        self.assertEqual(self.client.delete(f"/api/presenters/profiles/{profile['id']}", headers=auth("otto")).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/presenters/profiles/{profile['id']}", headers=auth("petra")).status_code, 200)

    def test_lessons_without_presenters_are_unchanged(self):
        plain = {"subject_name": "Plain", "scenes": [{"type": "content", "title": "x", "narration": "y", "visual_plan": {}}]}
        pid = self.client.post("/save-history", json=plain, headers=auth("petra")).json()["id"]
        stored = self.client.get(f"/api/projects/{pid}", headers=auth("petra")).json()
        self.assertNotIn("presenter_plan", stored["scenes"][0])
        visuals = self.client.post("/api/visuals/plan", json={"scenes": stored["scenes"]}, headers=auth("petra"))
        self.assertEqual(visuals.status_code, 200)


if __name__ == "__main__":
    unittest.main()
