"""Backend tests of the professional video styling system (Phase 17, styles.py): the four versioned families and their
semantic tokens, legacy lessons (no style; the Phase 13 typography academic / modern) resolving to Cinematic Education@1
with today's literals, version pinning, the bounded user overrides, the scene's own Visual Review style choices, the
accessibility guard (WCAG contrast: accessibility beats decoration and the user's choice), the fingerprint, the CSS
variables' safety (no url / expression / anything executable), the catalog and options, the background words of the
legacy styles (cached AI backgrounds stay reusable), lesson_choice and performance.

Then the integration with the cinematic plans and the API (cinematic.py, server.py): plan.style.look, a style that
re-renders without re-opening approvals, Classic untouched, the new transitions, scene-level style overrides, the API's
validation and vocabulary, and the lesson's saved style.

Run from the repo root:  .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_styles.py" -v

No browser, no AI provider: styles are pure data; the API runs in-process on the throwaway database.
"""
import copy
import re
import time
import unittest
from unittest import mock

from backend_env import assert_isolated, auth, ensure_user  # noqa: F401  first: throwaway database

from fastapi.testclient import TestClient  # noqa: E402

import cinematic as C  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402
import styles as S  # noqa: E402
from test_cinematic import CINE, lesson  # noqa: E402

CSS_NAME = re.compile(r"^--st-[a-z0-9-]{1,40}$")
CSS_VALUE = re.compile(r"^[#(),.%\-\w\s'/:]{0,200}$")
DERIVED = {"code-scale", "formula-scale"}  # tokens resolve() derives from the code / formula size choices (not in BASE)
LOOK_KEYS = {"schema", "id", "family", "version", "label", "variant", "tone", "prefs", "overrides", "scene_overrides",
             "adjustments", "fingerprint", "legacy", "css"}
PREF_VALUES = {"tone": S.TONES, "emphasis": S.EMPHASIS_STYLES, "caption": S.CAPTION_STYLES, "presenter_frame": S.PRESENTER_FRAMES,
               "presenter_label": (True, False), "visual_frame": S.VISUAL_FRAMES, "pattern": S.BACKGROUND_PATTERNS,
               "motion": S.MOTION_LEVELS, "camera": S.CAMERA_PREFS, "transition": S.TRANSITION_PREFS}
TODAY_GRADIENT = ["#140a2e", "#2a1352", "#0d1b3d"]   # the page's own colours before Phase 17 (Phase 13 "academic")
MODERN_GRADIENT = ["#0b1224", "#1b2a4a", "#10233d"]  # Phase 13 "modern"


def setUpModule():
    assert_isolated()


def styled(family=None, scene=None, **overrides):
    settings = {"style_overrides": overrides}
    if family:
        settings["style"] = family
    return S.resolve(settings, scene)


def reviewed(status="changed", **overrides):
    """A scene whose Visual Review composition carries these overrides."""
    return {"type": "content", "title": "T", "html": "<p>x</p>",
            "visual_review": {"composition": {"status": status, "overrides": overrides}}}


def rgb_of(color):
    c = S.parse_color(color)
    return f"{c[0]}, {c[1]}, {c[2]}"


def alpha_of(color):
    return S.parse_color(color)[3]


def flat(*layers):
    """The opaque colour seen through colour layers stacked bottom first (the bottom one over black when translucent)."""
    seen = (0, 0, 0, 1.0)
    for value in layers:
        c = S.parse_color(value)
        seen = S.over(c, seen) if c[3] < 1 else c
    return S._hex(seen)


def readable_pairs(tokens, tone):
    """(foreground token, the opaque colour it is read on, minimum) for every text the page shows, computed here
    independently of styles._accessibility (each text on what it really sits on, translucent layers composited)."""
    surface = S._surface_ref(tokens)  # the board's surface over the background
    backdrop = flat(tokens["bg-2"])
    body = [(name, surface, S.MIN_CONTRAST) for name in ("text", "text-secondary", "text-muted", "accent-text", "date-text")]
    body += [
        ("callout-text", flat(tokens["bg-2"], tokens["surface-1"], tokens["callout-bg"]), S.MIN_CONTRAST),
        ("table-head-text", flat(tokens["bg-2"], tokens["surface-1"], tokens["table-bg"], tokens["table-head-bg"]), S.MIN_CONTRAST),
        ("output-text", flat(tokens["bg-2"], tokens["surface-1"], tokens["output-bg"]), S.MIN_CONTRAST),
        ("label-text", flat(tokens["bg-2"], tokens["label-bg"]), S.MIN_CONTRAST),
        ("on-accent", flat(tokens["bg-2"], tokens["accent"]), S.MIN_CONTRAST),
        ("heading", surface, S.MIN_CONTRAST_LARGE),
        ("title-text", backdrop, S.MIN_CONTRAST_LARGE),
    ]
    if alpha_of(tokens["caption-bg"]) >= 0.5:  # a box behind the captions
        body.append(("caption-text", flat(tokens["bg-2"], tokens["caption-bg"]), S.MIN_CONTRAST))
    else:  # shadowed text over the picture (the shadow is the dark reference)
        body.append(("caption-text", "#000000", S.MIN_CONTRAST))
    return body


class ProfileTest(unittest.TestCase):
    def test_four_families_resolve_with_ids_labels_and_descriptions(self):
        self.assertEqual(S.FAMILIES, ("academic", "cinematic_education", "children_education", "corporate_training"))
        self.assertEqual(S.DEFAULT_FAMILY, "cinematic_education")
        self.assertEqual(set(S.FAMILY_DEFS), set(S.FAMILIES))
        labels = set()
        for family in S.FAMILIES:
            look = S.resolve({"style": family})
            version = S.latest_version(family)
            self.assertEqual((look["family"], look["version"], look["id"]), (family, version, f"{family}@{version}"))
            self.assertEqual(look["schema"], S.SCHEMA)
            definition = S.FAMILY_DEFS[family][version]
            self.assertEqual(look["label"], definition["label"])
            self.assertTrue(isinstance(definition["description"], str) and len(definition["description"]) > 10, family)
            self.assertTrue(definition["background_words"], family)
            self.assertIn(look["tone"], S.TONES)
            self.assertEqual(look["tone"], look["prefs"]["tone"])
            self.assertIsNone(look["variant"])
            self.assertEqual(look["overrides"], {})
            self.assertEqual(look["scene_overrides"], {})
            labels.add(look["label"])
        self.assertEqual(len(labels), 4)
        self.assertEqual({f: S.resolve({"style": f})["tone"] for f in S.FAMILIES},
                         {"academic": "light", "cinematic_education": "dark", "children_education": "light", "corporate_training": "dark"})

    def test_family_tokens_cover_every_base_token_and_only_known_names(self):
        for family, versions in S.FAMILY_DEFS.items():
            for version, definition in versions.items():
                with self.subTest(family=family, version=version):
                    unknown = set(definition.get("tokens", {})) - set(S.BASE)
                    self.assertEqual(unknown, set(), "a family may only set tokens the page reads")
                    self.assertEqual(set(definition.get("prefs", {})) - set(S.BASE_PREFS), set())
                    for key, value in definition.get("prefs", {}).items():
                        self.assertIn(value, PREF_VALUES[key], key)
                    tokens = S.resolve({"style": family, "style_version": version})["tokens"]
                    self.assertEqual(set(tokens), set(S.BASE) | DERIVED, "every BASE token resolved, nothing missing")
                    self.assertTrue(all(isinstance(v, str) for v in tokens.values()))
        for variant in S.LEGACY_VARIANTS.values():
            self.assertEqual(set(variant["tokens"]) - set(S.BASE), set())
        for key, value in S.BASE_PREFS.items():
            self.assertIn(value, PREF_VALUES[key], key)
        self.assertEqual(set(S.BASE_PREFS), set(PREF_VALUES))

    def test_every_value_passes_css_variables(self):
        for family in S.FAMILIES:
            for overrides in ({}, {"accent": "teal", "background": "rich", "text_size": "larger", "caption_size": "large",
                                   "code_size": "large", "formula_size": "large", "motion": "low", "diagram_frame": "plain"},
                              {"background": "plain"}):
                with self.subTest(family=family, overrides=overrides):
                    look = styled(family, **overrides)
                    css = S.css_variables(look)
                    self.assertEqual(len(css), len(look["tokens"]))
                    for name, value in css.items():
                        self.assertRegex(name, CSS_NAME)
                        self.assertRegex(value, CSS_VALUE)
                        self.assertNotIn("url", value.lower())
                        self.assertNotIn("expression", value.lower())
                    self.assertEqual(S.plan_look({"style": family, "style_overrides": overrides})["css"], css)

    def test_default_family_is_todays_look_exactly(self):
        definition = S.FAMILY_DEFS["cinematic_education"][1]
        self.assertEqual((definition["tokens"], definition["prefs"]), ({}, {}))
        look = S.resolve({"style": "cinematic_education"})
        self.assertEqual({k: look["tokens"][k] for k in S.BASE}, S.BASE)  # every token is today's literal
        self.assertEqual({k: look["tokens"][k] for k in DERIVED}, {"code-scale": "1", "formula-scale": "1"})  # neutral
        self.assertEqual(look["adjustments"], [])
        self.assertEqual(look["prefs"], S.BASE_PREFS)
        self.assertEqual(S.resolve({})["tokens"], look["tokens"])  # no style at all: the same look
        css = S.plan_look({})["css"]
        self.assertEqual((css["--st-bg-1"], css["--st-bg-2"], css["--st-bg-3"]), tuple(TODAY_GRADIENT))
        self.assertEqual((css["--st-accent"], css["--st-heading"], css["--st-title-bar"]), ("#FFD700", "#FFD700", "#FFD700"))
        self.assertEqual(css["--st-font-title"], "'Outfit', sans-serif")
        self.assertEqual(css["--st-entrance-shift"], "1")
        self.assertEqual(css["--st-caption-size"], "1.8rem")
        # "auto": the page keeps its own entrance lengths (fades 0.5 s, slides 0.65 s) for today's look
        self.assertEqual(S.BASE["entrance-ms"], "auto")
        self.assertEqual(css["--st-entrance-ms"], "auto")
        self.assertEqual(S.plan_look({"style": "cinematic_education"})["css"]["--st-entrance-ms"], "auto")
        self.assertEqual(S.plan_look({"typography": "modern"})["css"]["--st-entrance-ms"], "auto")

    def test_other_families_give_numeric_entrance_lengths(self):
        for family in ("academic", "children_education", "corporate_training"):
            ms = S.resolve({"style": family})["tokens"]["entrance-ms"]
            self.assertRegex(ms, r"^\d{3,4}$", family)
            self.assertTrue(300 <= int(ms) <= 1000, family)
        self.assertEqual({f: S.resolve({"style": f})["tokens"]["entrance-ms"] for f in ("academic", "children_education", "corporate_training")},
                         {"academic": "500", "children_education": "650", "corporate_training": "450"})

    def test_legacy_flag_marks_lessons_without_a_style(self):
        for settings in ({}, None, {"typography": "academic"}, {"typography": "modern"}, {"style": None}, {"style": ""},
                         {"style_overrides": {"accent": "teal"}}):
            with self.subTest(settings=settings):
                self.assertIs(S.resolve(settings)["legacy"], True)
                self.assertIs(S.plan_look(settings)["legacy"], True)
        for family in S.FAMILIES:
            self.assertIs(S.resolve({"style": family})["legacy"], False, family)
            self.assertIs(S.plan_look({"style": family, "typography": "modern"})["legacy"], False, family)
        # the same look either way: legacy is not part of the fingerprint
        legacy, chosen = S.resolve({}), S.resolve({"style": "cinematic_education"})
        self.assertNotEqual(legacy["legacy"], chosen["legacy"])
        self.assertEqual(legacy["fingerprint"], chosen["fingerprint"])
        self.assertEqual(legacy["fingerprint"], S.fingerprint({**legacy, "legacy": False}))

    def test_legacy_lessons_resolve_to_cinematic_education_v1(self):
        today = S.resolve({"style": "cinematic_education"})["tokens"]
        for settings in ({}, None, {"typography": "academic"}, {"typography": None}, {"style": None, "typography": "academic"},
                         {"style": "", "typography": "academic"}, {"typography": "comic"}):
            with self.subTest(settings=settings):
                look = S.resolve(settings)
                self.assertEqual((look["id"], look["variant"]), ("cinematic_education@1", None))
                self.assertEqual(look["tokens"], today)
                self.assertEqual(look["adjustments"], [])
        modern = S.resolve({"typography": "modern"})
        self.assertEqual((modern["id"], modern["family"], modern["version"], modern["variant"]),
                         ("cinematic_education@1", "cinematic_education", 1, "modern"))
        t = modern["tokens"]
        self.assertEqual([t["bg-1"], t["bg-2"], t["bg-3"]], MODERN_GRADIENT)
        self.assertEqual((t["bg-solid"], t["title-bar"], t["font-title"]), ("#111a2e", "#7fd4ff", "'Inter', sans-serif"))
        changed = {k for k in S.BASE if t[k] != S.BASE[k]}
        self.assertEqual(changed, set(S.LEGACY_VARIANTS["modern"]["tokens"]))  # everything else is today's
        self.assertEqual(modern["adjustments"], [])
        self.assertEqual(modern["prefs"], S.BASE_PREFS)
        # Phase 13's palettes are these tokens (the background of an old lesson is unchanged)
        self.assertEqual(C.PALETTES["academic"]["gradient"], TODAY_GRADIENT)
        self.assertEqual(C.PALETTES["academic"]["solid"], S.BASE["bg-solid"])
        self.assertEqual(C.PALETTES["modern"]["gradient"], MODERN_GRADIENT)
        self.assertEqual(C.PALETTES["modern"]["solid"], t["bg-solid"])

    def test_a_chosen_style_wins_over_the_legacy_typography(self):
        self.assertEqual(S.resolve({"style": "academic", "typography": "modern"})["tokens"], S.resolve({"style": "academic"})["tokens"])
        self.assertIsNone(S.resolve({"style": "academic", "typography": "modern"})["variant"])

    def test_unknown_style_raises_invalid(self):
        for bad in ("neon", "Academic", "academic@1", "__class__", 5):
            with self.subTest(bad=bad), self.assertRaises(S.Invalid):
                S.resolve({"style": bad})
        self.assertTrue(issubclass(S.Invalid, ValueError))
        with self.assertRaises(S.Invalid):
            S.plan_look({"style": "neon"})

    def test_unknown_style_version_falls_back_to_the_latest(self):
        for version in (99, 0, -1, "1", 1.5, None):
            with self.subTest(version=version):
                look = S.resolve({"style": "academic", "style_version": version})
                self.assertEqual((look["id"], look["version"]), ("academic@1", 1))

    def test_version_pinning_keeps_old_tokens(self):
        v1 = S.FAMILY_DEFS["academic"][1]
        v2 = copy.deepcopy(v1)
        v2["tokens"].update({"bg-1": "#eef2f7", "accent": "#1d4e89", "heading": "#0f2a52"})
        v2["description"] = "Academic, revised"
        with mock.patch.dict(S.FAMILY_DEFS, {"academic": {1: v1, 2: v2}}):
            self.assertEqual(S.latest_version("academic"), 2)
            pinned = S.resolve({"style": "academic", "style_version": 1})
            latest = S.resolve({"style": "academic"})
            unknown = S.resolve({"style": "academic", "style_version": 7})
            self.assertEqual((pinned["id"], pinned["tokens"]["bg-1"], pinned["tokens"]["accent"]), ("academic@1", "#f4efe3", "#9b2335"))
            self.assertEqual((latest["id"], latest["tokens"]["bg-1"], latest["tokens"]["accent"]), ("academic@2", "#eef2f7", "#1d4e89"))
            self.assertEqual(unknown["id"], "academic@2")
            self.assertNotEqual(pinned["fingerprint"], latest["fingerprint"])
            self.assertEqual(S.resolve({"style": "academic", "style_version": 2})["fingerprint"], latest["fingerprint"])
            self.assertEqual(next(f for f in S.catalog() if f["id"] == "academic")["version"], 2)
            self.assertEqual(S.lesson_choice({"style": "academic", "style_version": 1})["style_version"], 1)
        self.assertEqual(S.latest_version("academic"), 1)  # the patch is gone
        self.assertEqual(S.resolve({"style": "academic"})["tokens"]["bg-1"], "#f4efe3")


class OverrideTest(unittest.TestCase):
    def test_check_overrides_accepts_every_option_and_drops_default(self):
        for key, values in S.OVERRIDE_OPTIONS.items():
            self.assertEqual(values[0], "default", key)
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(S.check_overrides({key: value}), {} if value == "default" else {key: value})
        everything = {key: values[-1] for key, values in S.OVERRIDE_OPTIONS.items()}
        self.assertEqual(S.check_overrides(everything), everything)
        self.assertEqual(S.check_overrides(None), {})
        self.assertEqual(S.check_overrides({}), {})
        self.assertEqual(set(S.OVERRIDE_OPTIONS), {"accent", "text_size", "motion", "background", "caption_size", "code_size",
                                                   "formula_size", "diagram_frame"})

    def test_check_overrides_rejects_unknown_keys_values_and_non_objects(self):
        for bad in ({"colour": "red"}, {"accent": "neon"}, {"accent": "#ff0000"}, {"accent": "url(x)"}, {"text_size": 2},
                    {"text_size": "huge"}, {"motion": "none"}, {"background": "image"}, {"diagram_frame": "fancy"},
                    {"accent": None}, {"accent": ["teal"]}, {1: "teal"}, "gold", ["accent"], 5, True):
            with self.subTest(bad=bad), self.assertRaises(S.Invalid):
                S.check_overrides(bad)
        with self.assertRaises(S.Invalid):  # a scene may change only its accent and background
            S.check_overrides({"text_size": "large"}, S.SCENE_OVERRIDE_KEYS)
        with self.assertRaises(S.Invalid):  # never passed through by resolve either
            S.resolve({"style": "academic", "style_overrides": {"accent": "neon"}})
        self.assertEqual(S.resolve({"style_overrides": {"accent": "neon"}}, overrides={"accent": "teal"})["overrides"],
                         {"accent": "teal"})  # explicit overrides are used instead of the settings' ones

    def test_accent_overrides_are_consistent_for_dark_and_light_tones(self):
        for family in S.FAMILIES:
            plain = styled(family)["tokens"]
            tone = styled(family)["tone"]
            for name, by_tone in S.ACCENTS.items():
                with self.subTest(family=family, accent=name):
                    fill, text, on = by_tone[tone]
                    look = styled(family, accent=name)
                    t = look["tokens"]
                    rgb = rgb_of(fill)
                    self.assertEqual(look["overrides"], {"accent": name})
                    self.assertEqual((t["accent"], t["accent-text"], t["on-accent"]), (fill, text, on))
                    self.assertEqual((t["accent-rgb"], t["focus-rgb"], t["accent-2-rgb"]), (rgb, rgb, rgb))
                    self.assertEqual((t["title-bar"], t["chart-1"], t["date-text"]), (fill, fill, text))
                    self.assertEqual(t["label-border"], f"rgba({rgb}, 0.5)")
                    self.assertEqual(t["formula-border"], f"rgba({rgb}, 0.3)")
                    if tone == "dark":  # headings take the accent on a dark style
                        self.assertEqual((t["heading"], t["table-head-text"]), (text, text))
                        self.assertEqual(t["surface-border"], f"rgba({rgb}, 0.22)")
                    else:  # a light style keeps its own headings
                        self.assertEqual((t["heading"], t["table-head-text"]), (plain["heading"], plain["table-head-text"]))
                    self.assertEqual(look["adjustments"], [])  # every built-in accent is readable as designed
                    self.assertEqual(t["text"], plain["text"])  # the accent never touches the body text

    def test_text_size_scales_within_bounds(self):
        for family in S.FAMILIES:
            base = float(styled(family)["tokens"]["text-scale"])
            for size, factor in S.TEXT_SIZES.items():
                with self.subTest(family=family, size=size):
                    scale = float(styled(family, text_size=size)["tokens"]["text-scale"])
                    self.assertAlmostEqual(scale, min(S.MAX_TEXT_SCALE, max(S.MIN_TEXT_SCALE, base * factor)), places=3)
                    self.assertTrue(S.MIN_TEXT_SCALE <= scale <= S.MAX_TEXT_SCALE)
            self.assertGreater(float(styled(family, text_size="larger")["tokens"]["text-scale"]), base)
        self.assertEqual(styled("children_education", text_size="large")["tokens"]["text-scale"], "1.166")
        tokens = S.FAMILY_DEFS["children_education"][1]["tokens"]
        with mock.patch.dict(tokens, {"text-scale": "1.2"}):
            self.assertEqual(styled("children_education", text_size="larger")["tokens"]["text-scale"], "1.3")  # capped
        with mock.patch.dict(tokens, {"text-scale": "0.5"}):
            self.assertEqual(styled("children_education")["tokens"]["text-scale"], "0.9")  # never unreadably small

    def test_background_density(self):
        for family in S.FAMILIES:
            subtle = styled(family)["tokens"]
            self.assertEqual(styled(family, background="subtle")["tokens"], subtle)  # "subtle" is the default
            plain = styled(family, background="plain")["tokens"]
            rich = styled(family, background="rich")["tokens"]
            with self.subTest(family=family):
                self.assertEqual((plain["bg-glow-1"], plain["bg-glow-2"], plain["bg-pattern"]), ("transparent", "transparent", "none"))
                for glow in ("bg-glow-1", "bg-glow-2"):
                    self.assertEqual(S.parse_color(rich[glow])[:3], S.parse_color(subtle[glow])[:3])
                    self.assertAlmostEqual(alpha_of(rich[glow]), round(min(1.0, alpha_of(subtle[glow]) * 1.5), 3), places=3)
                    self.assertGreater(alpha_of(rich[glow]), alpha_of(subtle[glow]))
                pattern = styled(family)["prefs"]["pattern"]
                if pattern == "none":
                    self.assertEqual((subtle["bg-pattern"], rich["bg-pattern"]), ("none", "none"))
                else:
                    first_alpha = lambda value: float(re.search(r"rgba\([^)]*,\s*([0-9.]+)\)", value).group(1))  # noqa: E731
                    self.assertGreater(first_alpha(rich["bg-pattern"]), first_alpha(subtle["bg-pattern"]))
                # content is never touched by the background choice
                for key in ("text", "heading", "surface-1", "accent", "bg-1", "bg-2"):
                    self.assertEqual((plain[key], rich[key]), (subtle[key], subtle[key]), key)
        self.assertTrue(styled("academic")["tokens"]["bg-pattern"].startswith("repeating-linear-gradient"))   # paper
        self.assertTrue(styled("children_education")["tokens"]["bg-pattern"].startswith("radial-gradient"))   # dots
        self.assertTrue(styled("corporate_training")["tokens"]["bg-pattern"].startswith("linear-gradient"))   # grid

    def test_caption_code_and_formula_sizes(self):
        for family in S.FAMILIES:
            self.assertEqual(styled(family)["tokens"]["caption-size"], "1.8rem")
            self.assertEqual(styled(family, caption_size="standard")["tokens"]["caption-size"], "1.8rem")
            self.assertEqual(styled(family, caption_size="large")["tokens"]["caption-size"], "2.16rem")
            self.assertEqual(styled(family)["tokens"]["code-scale"], "1")
            self.assertEqual(styled(family, code_size="large")["tokens"]["code-scale"], "1.12")
            self.assertEqual(styled(family, formula_size="large")["tokens"]["formula-scale"], "1.15")
            self.assertEqual(styled(family, formula_size="standard")["tokens"]["formula-scale"], "1")

    def test_motion_low_is_a_plain_fade(self):
        low = styled("cinematic_education", motion="low")
        self.assertEqual((low["tokens"]["entrance-shift"], low["prefs"]["motion"]), ("0", "low"))
        self.assertEqual(styled("cinematic_education")["tokens"]["entrance-shift"], "1")
        # Academic prefers low motion: plain fades, unless the user asks for the style's movement
        self.assertEqual(styled("academic")["tokens"]["entrance-shift"], "0")
        standard = styled("academic", motion="standard")
        self.assertEqual((standard["tokens"]["entrance-shift"], standard["prefs"]["motion"]), ("0.5", "standard"))
        self.assertEqual(styled("children_education")["tokens"]["entrance-shift"], "1.2")
        self.assertEqual(styled("children_education", motion="low")["tokens"]["entrance-shift"], "0")

    def test_diagram_frame_changes_the_visual_frame_preference(self):
        for family in S.FAMILIES:
            for frame in S.VISUAL_FRAMES:
                look = styled(family, diagram_frame=frame)
                self.assertEqual(look["prefs"]["visual_frame"], frame)
                self.assertEqual(look["tokens"], styled(family)["tokens"])  # a preference, not a colour
            # the overrides never change the style's other preferences
            prefs = styled(family, diagram_frame="plain", accent="teal", background="rich")["prefs"]
            self.assertEqual({k: v for k, v in prefs.items() if k != "visual_frame"},
                             {k: v for k, v in styled(family)["prefs"].items() if k != "visual_frame"})


class SceneOverrideTest(unittest.TestCase):
    def test_only_style_keys_of_an_approved_or_changed_review_apply(self):
        self.assertEqual(S.scene_overrides(reviewed("changed", style_accent="teal")), {"accent": "teal"})
        self.assertEqual(S.scene_overrides(reviewed("approved", style_background="plain")), {"background": "plain"})
        self.assertEqual(S.scene_overrides(reviewed("changed", style_accent="teal", style_background="rich", template="quiz",
                                                    camera="static", accent="crimson")), {"accent": "teal", "background": "rich"})
        self.assertEqual(S.scene_overrides(reviewed("changed", style_accent="default")), {})
        for status in ("pending", "rejected", None, "", "CHANGED"):
            with self.subTest(status=status):
                self.assertEqual(S.scene_overrides(reviewed(status, style_accent="teal")), {})
        for scene in (None, {}, "scene", [], {"visual_review": None}, {"visual_review": "x"}, {"visual_review": {"composition": "x"}},
                      {"visual_review": {"composition": {"status": "changed", "overrides": "teal"}}},
                      {"visual_review": {"composition": {"status": "changed"}}}, {"visual_review": {"presenter": {"status": "changed"}}}):
            with self.subTest(scene=scene):
                self.assertEqual(S.scene_overrides(scene), {})

    def test_invalid_scene_values_are_ignored_not_raised(self):
        for overrides in ({"style_accent": "neon"}, {"style_accent": 5}, {"style_accent": ["teal"]}, {"style_background": "image"},
                          {"style_text_size": "large"}, {"style_motion": "low"}, {"style_": "x"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(S.scene_overrides(reviewed("changed", **overrides)), {})
                look = styled("academic", scene=reviewed("changed", **overrides))  # never raises
                self.assertEqual(look["scene_overrides"], {})
                self.assertEqual(look["fingerprint"], styled("academic")["fingerprint"])
        # a key a scene may not change is never applied
        mixed = S.scene_overrides(reviewed("changed", style_accent="teal", style_text_size="larger"))
        self.assertNotIn("text_size", mixed)
        self.assertEqual(mixed, {"accent": "teal"})  # the valid choice is kept: each bad key is dropped on its own
        self.assertEqual(S.scene_overrides(reviewed("changed", style_accent="neon", style_background="plain")), {"background": "plain"})

    def test_a_scene_override_wins_over_the_lessons_value_and_changes_the_fingerprint(self):
        lesson_only = styled("corporate_training", accent="gold", background="rich")
        scene = reviewed("changed", style_accent="teal", style_background="plain")
        both = styled("corporate_training", scene=scene, accent="gold", background="rich")
        teal = S.ACCENTS["teal"]["dark"][0]
        self.assertEqual((both["tokens"]["accent"], both["tokens"]["title-bar"]), (teal, teal))
        self.assertEqual((both["tokens"]["bg-glow-1"], both["tokens"]["bg-pattern"]), ("transparent", "none"))
        self.assertEqual(both["overrides"], {"accent": "gold", "background": "rich"})  # the lesson's choice is kept as is
        self.assertEqual(both["scene_overrides"], {"accent": "teal", "background": "plain"})
        self.assertNotEqual(both["fingerprint"], lesson_only["fingerprint"])
        self.assertEqual(lesson_only["tokens"]["accent"], S.ACCENTS["gold"]["dark"][0])
        pending = styled("corporate_training", scene=reviewed("pending", style_accent="teal"), accent="gold", background="rich")
        self.assertEqual(pending["fingerprint"], lesson_only["fingerprint"])
        self.assertEqual(pending["tokens"], lesson_only["tokens"])
        light = styled("academic", scene=reviewed("approved", style_accent="teal"))
        self.assertEqual(light["tokens"]["accent"], S.ACCENTS["teal"]["light"][0])  # the scene's tone picks the shade
        self.assertEqual(S.plan_look({"style": "academic"}, reviewed("changed", style_accent="sky"))["scene_overrides"], {"accent": "sky"})


class AccessibilityTest(unittest.TestCase):
    def test_contrast_matches_known_wcag_values(self):
        self.assertEqual(S.contrast("#000", "#fff"), 21.0)
        self.assertEqual(S.contrast("#000000", "#ffffff"), 21.0)
        self.assertAlmostEqual(S.contrast("#777", "#fff"), 4.48, delta=0.01)
        self.assertEqual(S.contrast("#fff", "#777"), S.contrast("#777", "#fff"))  # symmetric
        self.assertEqual(S.contrast("#123456", "#123456"), 1.0)
        self.assertAlmostEqual(S.contrast("#767676", "#ffffff"), 4.54, delta=0.01)  # the classic AA grey
        # alpha is composited: half-black over white is mid grey; a translucent background lies over black
        self.assertEqual(S.contrast("rgba(0, 0, 0, 0.5)", "#ffffff"), S.contrast("#808080", "#ffffff"))
        self.assertEqual(S.contrast("#ffffff", "rgba(255, 255, 255, 0.5)"), S.contrast("#ffffff", "#808080"))
        for bad in (("none", "#fff"), ("#fff", "linear-gradient(red, blue)"), ("red", "#fff"), (None, "#fff")):
            self.assertIsNone(S.contrast(*bad))
        self.assertEqual(S.parse_color("#abc"), (170, 187, 204, 1.0))
        self.assertEqual(S.parse_color("rgba(1, 2, 3, 0.5)"), (1, 2, 3, 0.5))
        self.assertEqual(S.parse_color("transparent")[3], 0.0)
        self.assertIsNone(S.parse_color("url(x)"))

    def test_ensure_contrast_moves_a_failing_colour_and_keeps_a_passing_one(self):
        fixed, changed = S.ensure_contrast("#777777", "#ffffff", S.MIN_CONTRAST)
        self.assertTrue(changed)
        self.assertGreaterEqual(S.contrast(fixed, "#ffffff"), S.MIN_CONTRAST)
        self.assertLess(S.luminance(S.parse_color(fixed)), S.luminance(S.parse_color("#777777")))  # darker on white
        lighter, changed = S.ensure_contrast("#444444", "#000000", S.MIN_CONTRAST)
        self.assertTrue(changed)
        self.assertGreaterEqual(S.contrast(lighter, "#000000"), S.MIN_CONTRAST)
        self.assertGreater(S.luminance(S.parse_color(lighter)), S.luminance(S.parse_color("#444444")))  # lighter on black
        self.assertEqual(S.ensure_contrast("#333333", "#ffffff", S.MIN_CONTRAST), ("#333333", False))
        self.assertEqual(S.ensure_contrast("rgba(255, 255, 255, 0.95)", "#281352", S.MIN_CONTRAST), ("rgba(255, 255, 255, 0.95)", False))
        self.assertEqual(S.ensure_contrast("none", "#ffffff", S.MIN_CONTRAST), ("none", False))
        self.assertEqual(S.ensure_contrast("#888888", "#ffffff", 22), ("#000000", True))  # impossible: the extreme

    def test_every_family_accent_and_size_is_readable_without_adjustments(self):
        bases = [{"style": f} for f in S.FAMILIES] + [{"typography": "modern"}]
        checked = 0
        for base in bases:
            for accent in (None,) + tuple(S.ACCENTS):
                for size in S.TEXT_SIZES:
                    for density in S.BACKGROUND_DENSITY:
                        overrides = {"text_size": size, "background": density, **({"accent": accent} if accent else {})}
                        look = S.resolve({**base, "style_overrides": overrides})
                        with self.subTest(base=base, overrides=overrides):
                            self.assertEqual(look["adjustments"], [])
                            for name, on, minimum in readable_pairs(look["tokens"], look["tone"]):
                                ratio = S.contrast(look["tokens"][name], on)
                                self.assertIsNotNone(ratio, name)
                                self.assertGreaterEqual(ratio, minimum, f"{name} {look['tokens'][name]} on {on}")
                        checked += 1
        self.assertEqual(checked, 5 * 8 * 3 * 3)

    def test_scene_accents_are_readable_too(self):
        for family in S.FAMILIES:
            for accent in S.ACCENTS:
                look = styled(family, scene=reviewed("changed", style_accent=accent))
                self.assertEqual(look["adjustments"], [], (family, accent))

    def test_a_bad_token_is_adjusted_and_reported(self):
        academic = S.FAMILY_DEFS["academic"][1]["tokens"]
        with mock.patch.dict(academic, {"text": "#f4f4f4", "heading": "#fbfbfb"}):  # near the white surface
            look = S.resolve({"style": "academic"})
            surface = S._surface_ref(look["tokens"])
            self.assertIn("body text made darker or lighter to stay readable", look["adjustments"])
            self.assertIn("headings made darker or lighter to stay readable", look["adjustments"])
            self.assertNotEqual(look["tokens"]["text"], "#f4f4f4")
            self.assertGreaterEqual(S.contrast(look["tokens"]["text"], surface), S.MIN_CONTRAST)
            self.assertGreaterEqual(S.contrast(look["tokens"]["heading"], surface), S.MIN_CONTRAST_LARGE)
            planned = S.plan_look({"style": "academic"})
            self.assertEqual(planned["adjustments"], look["adjustments"])
            self.assertEqual(planned["css"]["--st-text"], look["tokens"]["text"])  # the page gets the readable colour
        corporate = S.FAMILY_DEFS["corporate_training"][1]["tokens"]
        with mock.patch.dict(corporate, {"text": "#1a2333"}):  # near the dark surface: made lighter
            look = S.resolve({"style": "corporate_training"})
            self.assertIn("body text made darker or lighter to stay readable", look["adjustments"])
            self.assertGreater(S.luminance(S.parse_color(look["tokens"]["text"])), S.luminance(S.parse_color("#1a2333")))
            self.assertGreaterEqual(S.contrast(look["tokens"]["text"], S._surface_ref(look["tokens"])), S.MIN_CONTRAST)
        self.assertEqual(S.resolve({"style": "academic"})["adjustments"], [])

    def test_accessibility_beats_the_users_accent(self):
        weak = {"dark": S.ACCENTS["gold"]["dark"], "light": ("#ffe066", "#ffe066", "#ffffff")}  # pale yellow on white paper
        with mock.patch.dict(S.ACCENTS, {"gold": weak}):
            look = styled("academic", accent="gold")
            t = look["tokens"]
            surface = S._surface_ref(t)
            # the accent also draws bullets, ticks and step numbers (non-text marks, 3:1): accessibility beats the user's
            # pale fill too (Phase 17 review), and the text drawn on the adjusted fill is checked against it
            self.assertNotEqual(t["accent"], "#ffe066")
            self.assertGreaterEqual(S.contrast(t["accent"], surface), S.MIN_CONTRAST_LARGE)
            self.assertIn("bullets, ticks and step numbers made darker or lighter to stay readable", look["adjustments"])
            self.assertNotEqual(t["accent-text"], "#ffe066")  # its text is made readable
            self.assertGreaterEqual(S.contrast(t["accent-text"], surface), S.MIN_CONTRAST)
            self.assertGreaterEqual(S.contrast(t["on-accent"], t["accent"]), S.MIN_CONTRAST)
            self.assertIn("highlighted words made darker or lighter to stay readable", look["adjustments"])
            self.assertIn("text on the accent made darker or lighter to stay readable", look["adjustments"])


class FingerprintTest(unittest.TestCase):
    def test_deterministic_and_independent_of_ordering(self):
        a = S.resolve({"style": "academic", "style_overrides": {"accent": "teal", "text_size": "large", "background": "rich"}})
        b = S.resolve({"style_overrides": {"background": "rich", "text_size": "large", "accent": "teal"}, "style": "academic"})
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertRegex(a["fingerprint"], r"^[0-9a-f]{16}$")
        self.assertEqual(a["fingerprint"], S.resolve(copy.deepcopy({"style": "academic", "style_overrides": {
            "accent": "teal", "text_size": "large", "background": "rich"}}))["fingerprint"])
        self.assertEqual(S.fingerprint({"family": "x", "overrides": {"a": 1, "b": 2}, "version": 1}),
                         S.fingerprint({"version": 1, "overrides": {"b": 2, "a": 1}, "family": "x"}))
        self.assertEqual(a["fingerprint"], S.fingerprint(a))
        scene_a = reviewed("changed", style_accent="sky", style_background="plain")
        scene_b = reviewed("changed", style_background="plain", style_accent="sky")
        self.assertEqual(styled("academic", scene=scene_a)["fingerprint"], styled("academic", scene=scene_b)["fingerprint"])

    def test_changes_with_family_variant_overrides_and_scene(self):
        prints = {S.resolve({"style": f})["fingerprint"] for f in S.FAMILIES}
        prints.add(S.resolve({"typography": "modern"})["fingerprint"])
        self.assertEqual(len(prints), 5)
        self.assertEqual(S.resolve({})["fingerprint"], S.resolve({"style": "cinematic_education"})["fingerprint"])
        base = styled("academic")["fingerprint"]
        seen = {base}
        for key, values in S.OVERRIDE_OPTIONS.items():
            for value in values:
                fp = styled("academic", **{key: value})["fingerprint"]
                if value == "default":
                    self.assertEqual(fp, base, key)  # "default" is not a choice
                else:
                    self.assertNotIn(fp, seen, (key, value))
                    seen.add(fp)
        self.assertNotEqual(styled("academic", scene=reviewed("changed", style_accent="teal"))["fingerprint"], base)
        self.assertNotEqual(styled("academic", scene=reviewed("changed", style_accent="teal"))["fingerprint"],
                            styled("academic", accent="teal")["fingerprint"])  # a scene's choice is not the lesson's

    def test_not_changed_by_irrelevant_settings(self):
        base = S.resolve({"style": "academic", "style_overrides": {"accent": "teal"}})["fingerprint"]
        noisy = {"style": "academic", "style_overrides": {"accent": "teal", "motion": "default"}, "mode": "cinematic",
                 "typography": "modern", "motion": "none", "transitions": "slide", "background": "solid",
                 "background_asset_id": "a" * 32, "emphasis": "subtle", "presenter_id": "aadhi-teacher", "presenter_legacy": False,
                 "presenter_position": "left", "composer": "ai", "director": "ai", "learner_level": "advanced"}
        self.assertEqual(S.resolve(noisy)["fingerprint"], base)
        self.assertEqual(S.resolve({"motion": "none", "transitions": "wipe", "background": "image"})["fingerprint"],
                         S.resolve({})["fingerprint"])
        self.assertEqual(S.resolve({"style": "academic", "style_version": 99})["fingerprint"],
                         S.resolve({"style": "academic"})["fingerprint"])  # an unknown version resolves to the same look
        # a scene without a review (or with only layout changes) does not change the look's identity
        self.assertEqual(styled("academic", scene={"type": "content", "title": "Other"})["fingerprint"], styled("academic")["fingerprint"])
        self.assertEqual(styled("academic", scene=reviewed("changed", template="quiz"))["fingerprint"], styled("academic")["fingerprint"])


class SecurityTest(unittest.TestCase):
    UNSAFE = ("url(https://evil.example/x.png)", "URL(x)", "uRl(data:x)", "expression(alert(1))", "EXPRESSION(1)",
              "red; background: blue", "red}", "}body{color:red", "<script>", "</style>", "a\\62 c", '"quoted"', "red !important",
              "x" * 201, "@import 'x'", "{", "a\nb;",
              # the allowlist (security audit): no image or reference function, ASCII only, quotes only around font names
              "image-set('https://evil.example/x.png' 1x)", "-webkit-image-set('//evil/x' 1x)", "cross-fade('//e/a.png', '//e/b.png', 50%)",
              "src('//e/x')", "var(--x)", "attr(title)", "env(safe-area-inset-top)", "paint(x)", "element(#x)", "(1px)",
              "\uff55\uff52\uff4c(x)", "red\u2028", "a\tb", "'Inter", "'a1'")

    def test_css_variables_rejects_unsafe_values(self):
        for value in self.UNSAFE:
            with self.subTest(value=value), self.assertRaises(S.Invalid):
                S.css_variables({"tokens": {"radius-sm": value}})
        # through the resolution too: a bad BASE or family token is refused, never sent to the page
        for value in ("url(javascript:alert(1))", "expression(alert(1))", "1px; }", "<b>"):
            with self.subTest(base=value), mock.patch.dict(S.BASE, {"radius-sm": value}):
                with self.assertRaises(S.Invalid):
                    S.plan_look({})
                with self.assertRaises(S.Invalid):
                    S.catalog()
            with self.subTest(family=value), mock.patch.dict(S.FAMILY_DEFS["academic"][1]["tokens"], {"heading-case": value}):
                with self.assertRaises(S.Invalid):
                    S.plan_look({"style": "academic"})
        self.assertEqual(S.plan_look({})["css"]["--st-radius-sm"], "6px")  # restored

    def test_css_variables_rejects_unsafe_names(self):
        for name in ("Bad", "bad_name", "bad name", "", "a" * 41, "café", "x;y", "x:y", "x}", "--st-x)", 5):
            with self.subTest(name=name), self.assertRaises((S.Invalid, TypeError)):
                S.css_variables({"tokens": {name: "#ffffff"}})
        with mock.patch.dict(S.BASE, {"Evil_Token": "#ffffff"}), self.assertRaises(S.Invalid):
            S.plan_look({})

    def test_safe_values_pass(self):
        good = {"a": "clamp(0.8rem, 1.1vw, 1.2rem)", "b": "'Outfit', sans-serif", "c": "rgba(1, 2, 3, 0.5)",
                "d": "0 0 / 26px 26px", "e": "cubic-bezier(0.22, 1, 0.36, 1)", "f": "", "g": "drop-shadow(0 8px 18px rgba(0, 0, 0, 0.45))"}
        self.assertEqual(S.css_variables({"tokens": good}), {f"--st-{k}": v for k, v in good.items()})
        self.assertEqual(S.css_variables({}), {})


class CatalogTest(unittest.TestCase):
    def test_catalog_lists_the_four_families_in_order(self):
        catalog = S.catalog()
        self.assertEqual([f["id"] for f in catalog], list(S.FAMILIES))
        for entry in catalog:
            with self.subTest(family=entry["id"]):
                self.assertEqual(set(entry), {"id", "version", "label", "description", "tone", "prefs", "css", "preview"})
                look = S.resolve({"style": entry["id"]})
                self.assertEqual(entry["css"], S.css_variables(look))
                self.assertEqual(set(entry["preview"]), {"bg-1", "bg-2", "surface-1", "text", "heading", "accent", "title-text"})
                self.assertEqual(entry["preview"], {k: look["tokens"][k] for k in entry["preview"]})
                self.assertEqual((entry["label"], entry["tone"], entry["prefs"]), (look["label"], look["tone"], look["prefs"]))
                self.assertTrue(all(CSS_NAME.match(k) for k in entry["css"]))
        self.assertEqual(next(f for f in catalog if f["id"] == "academic")["preview"]["bg-1"], "#f4efe3")

    def test_options_match_the_override_vocabulary(self):
        options = S.options()
        self.assertEqual(options, {**{k: list(v) for k, v in S.OVERRIDE_OPTIONS.items()}, "scene": ["accent", "background"]})
        self.assertEqual(options["accent"], ["default", "gold", "coral", "crimson", "teal", "sky", "violet", "emerald"])
        self.assertEqual(options["diagram_frame"], ["default", "panel", "card", "rounded", "plain"])
        self.assertEqual(set(options["scene"]) <= set(S.OVERRIDE_OPTIONS), True)

    def test_background_words_of_legacy_styles_keep_cached_backgrounds_reusable(self):
        self.assertEqual(S.background_words({"typography": "academic"}), C.BACKGROUND_STYLE_WORDS["academic"])
        self.assertEqual(S.background_words({"typography": "modern"}), C.BACKGROUND_STYLE_WORDS["modern"])
        self.assertEqual(S.background_words({}), C.BACKGROUND_STYLE_WORDS["academic"])
        self.assertEqual(S.background_words({"style": "cinematic_education"}), C.BACKGROUND_STYLE_WORDS["academic"])
        # the request (and so the AI cache identity) of the default family is exactly the legacy one
        self.assertEqual(C.background_prompt("academic", "calm", words=S.background_words({"style": "cinematic_education"})),
                         C.background_prompt("academic", "calm"))
        words = {S.background_words({"style": f}) for f in S.FAMILIES}
        self.assertEqual(len(words), 4)
        self.assertNotEqual(C.background_prompt("academic", words=S.background_words({"style": "academic"})), C.background_prompt("academic"))

    def test_lesson_choice_cleans_what_a_lesson_keeps(self):
        self.assertEqual(S.lesson_choice({"style": "children_education", "style_version": 1,
                                          "style_overrides": {"accent": "teal", "text_size": "default"}}),
                         {"style": "children_education", "style_version": 1, "style_overrides": {"accent": "teal"}})
        self.assertEqual(S.lesson_choice({"style": "academic"}), {"style": "academic", "style_version": 1, "style_overrides": {}})
        self.assertEqual(S.lesson_choice({"style": "academic", "style_version": 99})["style_version"], 1)
        self.assertEqual(S.lesson_choice({"style": "academic", "style_version": True})["style_version"], 1)
        self.assertEqual(S.lesson_choice({"style": "academic", "style_overrides": {"accent": "neon"}})["style_overrides"], {})
        mixed = S.lesson_choice({"style": "academic", "style_overrides": {"accent": "teal", "glitter": "on"}})
        self.assertNotIn("glitter", mixed["style_overrides"])  # an unknown key is never stored
        self.assertEqual(mixed["style_overrides"], {"accent": "teal"})  # the valid choices are kept
        self.assertEqual(mixed["style"], "academic")
        for bad in (None, "academic", [], {}, {"style": "neon"}, {"style": None}, {"style_overrides": {"accent": "teal"}}):
            self.assertIsNone(S.lesson_choice(bad), bad)


class TypeRobustnessTest(unittest.TestCase):
    """A saved lesson's JSON may carry anything (security audit): the resolver raises Invalid or falls back, never a TypeError."""

    def test_odd_types_resolve_or_raise_invalid(self):
        for settings in ({"style": ["academic"]}, {"style": {"a": 1}}, {"style": 5}):
            with self.subTest(settings=settings), self.assertRaises(S.Invalid):
                S.resolve(settings)
        for version in (True, 1.0, "1", [1], {"v": 1}, -3, 10 ** 9):
            with self.subTest(version=version):
                self.assertEqual(S.resolve({"style": "academic", "style_version": version})["id"], "academic@1")
        for typography in ({"a": 1}, ["modern"], 5):
            with self.subTest(typography=typography):
                self.assertEqual(S.resolve({"typography": typography})["id"], "cinematic_education@1")
        self.assertIsNone(S.parse_color("rgba(1,2,3,1.2.3)"))
        self.assertIsNone(S.parse_color("rgba(1,2,3,.)"))
        self.assertEqual(S.parse_color("rgba(1, 2, 3, .5)"), (1, 2, 3, 0.5))


class ReviewFindingsTest(unittest.TestCase):
    """Defects found by the end-of-phase correctness review, each pinned."""

    def test_the_contrast_fix_moves_toward_the_end_that_can_read(self):
        # a mid-grey background (luminance between 0.18 and 0.4): white cannot reach 4.5, black can
        for fg, bg in (("#9a9a9a", "#8a8a8a"), ("#777777", "#7a7a7a"), ("#ffffff", "#b07a20")):
            with self.subTest(fg=fg, bg=bg):
                fixed, changed = S.ensure_contrast(fg, bg, S.MIN_CONTRAST)
                self.assertTrue(changed)
                self.assertGreaterEqual(S.contrast(fixed, bg), S.MIN_CONTRAST)

    def test_a_lesson_without_a_style_keeps_its_original_look_even_with_stray_overrides(self):
        legacy = S.resolve({"style_overrides": {"accent": "teal", "background": "plain"}})
        self.assertEqual(legacy["overrides"], {})
        self.assertEqual(legacy["tokens"]["accent"], S.BASE["accent"])
        self.assertEqual(legacy["fingerprint"], S.resolve({})["fingerprint"])
        styled_ = S.resolve({"style": "cinematic_education", "style_overrides": {"accent": "teal"}})
        self.assertEqual(styled_["overrides"], {"accent": "teal"})  # with a chosen style they apply (and are saved)

    def test_marks_and_dates_on_their_badge_are_checked(self):
        for family in S.FAMILIES:
            for accent in (None,) + tuple(S.ACCENTS):
                with self.subTest(family=family, accent=accent):
                    t = styled(family, **({"accent": accent} if accent else {}))["tokens"]
                    surface = S._surface_ref(t)
                    badge = S._hex(S.over(S._rgba(t["accent-2-rgb"], 0.16), S.parse_color(surface)))
                    self.assertGreaterEqual(S.contrast(t["accent"], surface), S.MIN_CONTRAST_LARGE)
                    self.assertGreaterEqual(S.contrast(t["date-text"], badge), S.MIN_CONTRAST)
                    self.assertGreaterEqual(S.contrast(t["on-accent"], t["accent"]), S.MIN_CONTRAST)
                    # keywords inside code sit on the code panel, dark in every style (visual QA: a light style's accent did not read)
                    code = S.parse_color(t["code-bg"])
                    code_bg = S._hex(S.over(code, S.parse_color(surface))) if code[3] < 1 else t["code-bg"]
                    self.assertGreaterEqual(S.contrast(t["code-accent"], code_bg), S.MIN_CONTRAST)


class PerformanceTest(unittest.TestCase):
    def test_two_hundred_scene_looks_resolve_quickly(self):
        settings = {"style": "children_education", "style_overrides": {"accent": "violet", "text_size": "large", "background": "rich"}}
        scenes = [reviewed("changed", style_accent=list(S.ACCENTS)[i % 7]) if i % 3 == 0 else {"type": "content", "title": str(i)}
                  for i in range(200)]
        started = time.perf_counter()
        looks = [S.plan_look(settings, scene) for scene in scenes]
        elapsed = time.perf_counter() - started
        print(f"\n[styles] 200 scene looks resolved in {elapsed * 1000:.1f} ms")
        self.assertEqual(len(looks), 200)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(looks[0]["scene_overrides"], {"accent": "gold"})
        self.assertEqual(looks[1]["scene_overrides"], {})


# ---- integration: the cinematic plans and the API (cinematic.py, server.py) ------------------------------------------

class PlanIntegrationTest(unittest.TestCase):
    """plan.style.look on every cinematic plan (contract: phase17_contract.md, "Server -> page")."""

    @classmethod
    def setUpClass(cls):
        cls.plain = C.compose_lesson(lesson(), CINE)

    def test_a_lesson_without_style_keeps_todays_plans(self):
        explicit = C.compose_lesson(lesson(), {**CINE, "style": None, "style_version": None, "style_overrides": None})
        self.assertEqual([p["fingerprint"] for p in explicit], [p["fingerprint"] for p in self.plain])
        self.assertEqual([p["plan_hash"] for p in explicit], [p["plan_hash"] for p in self.plain])
        self.assertEqual(explicit, self.plain)
        self.assertEqual(C.compose_lesson(lesson(), {**CINE, "style_overrides": {}}), self.plain)
        for i, plan in enumerate(self.plain):
            with self.subTest(scene=i):
                look = plan["style"]["look"]
                self.assertEqual(set(look), LOOK_KEYS)
                self.assertEqual((look["id"], look["variant"], look["tone"]), ("cinematic_education@1", None, "dark"))
                self.assertIs(look["legacy"], True)
                self.assertEqual(look["css"]["--st-entrance-ms"], "auto")
                self.assertEqual(look, S.plan_look(CINE))
                self.assertEqual(set(plan["style"]), set(C.DEFAULT_STYLE) | {"look"})  # Phase 13 keys unchanged
                self.assertEqual({k: plan["style"][k] for k in C.DEFAULT_STYLE}, C.DEFAULT_STYLE)
                self.assertEqual(C.validate_plan(plan), [])
                self.assertEqual(plan["warnings"], [])
        for plan in self.plain[:6]:
            self.assertEqual(plan["background"], {"type": "gradient", "palette": "academic", "colors": TODAY_GRADIENT})

    def test_legacy_modern_typography_keeps_its_palette(self):
        plans = C.compose_lesson(lesson(), {**CINE, "typography": "modern"})
        for plan in plans[:6]:
            self.assertEqual(plan["background"], {"type": "gradient", "palette": "modern", "colors": MODERN_GRADIENT})
            look = plan["style"]["look"]
            self.assertEqual((look["id"], look["variant"]), ("cinematic_education@1", "modern"))
            self.assertEqual(look["css"]["--st-title-bar"], "#7fd4ff")
        solid = C.compose_lesson(lesson()[:2], {**CINE, "background": "solid"})[0]["background"]
        self.assertEqual((solid["type"], solid["color"]), ("solid", "#1a1036"))

    def test_academic_restyles_without_reopening_approvals(self):
        tokens = S.resolve({"style": "academic"})["tokens"]
        academic = C.compose_lesson(lesson(), {**CINE, "style": "academic"})
        for i, (before, after) in enumerate(zip(self.plain, academic)):
            with self.subTest(scene=i):
                look = after["style"]["look"]
                self.assertEqual((look["id"], look["family"], look["tone"]), ("academic@1", "academic", "light"))
                self.assertIs(look["legacy"], False)
                self.assertEqual(look["css"]["--st-bg-1"], tokens["bg-1"])
                self.assertEqual(look["css"]["--st-bg-1"], "#f4efe3")
                self.assertEqual(after["fingerprint"], before["fingerprint"])  # approvals stay: a style change re-renders
                self.assertNotEqual(look["fingerprint"], before["style"]["look"]["fingerprint"])
                # the composition is decided elsewhere: same template, layers, camera and transition
                for key in ("template", "layers", "camera", "transition", "timeline", "duration"):
                    self.assertEqual(after[key], before[key], key)
                # a style never changes the lesson's motion / transitions / background settings
                self.assertEqual({k: after["style"][k] for k in C.DEFAULT_STYLE}, {k: before["style"][k] for k in C.DEFAULT_STYLE})
                self.assertEqual(C.validate_plan(after), [])
        for plan in academic[:6]:
            self.assertEqual(plan["background"]["type"], "gradient")
            self.assertEqual(plan["background"]["colors"], [tokens["bg-1"], tokens["bg-2"], tokens["bg-3"]])
            self.assertNotEqual(plan["plan_hash"], self.plain[academic.index(plan)]["plan_hash"])  # it re-renders
        solid = C.compose_lesson(lesson()[:2], {**CINE, "style": "academic", "background": "solid"})[0]["background"]
        self.assertEqual((solid["type"], solid["color"]), ("solid", tokens["bg-solid"]))
        # an approval made before the restyle is still an approval afterwards
        scenes = lesson()
        scenes[1]["visual_review"] = {"composition": {"status": "approved", "fingerprint": self.plain[1]["fingerprint"]}}
        restyled = C.compose_lesson(scenes, {**CINE, "style": "corporate_training", "style_overrides": {"accent": "teal", "text_size": "large"}})
        self.assertEqual(restyled[1]["review_status"], "approved")
        self.assertNotIn("review_stale", restyled[1])
        self.assertEqual(restyled[1]["style"]["look"]["overrides"], {"accent": "teal", "text_size": "large"})

    def test_a_style_preference_never_changes_the_lessons_transitions_or_motion(self):
        for family in ("children_education", "corporate_training", "academic"):
            plans = C.compose_lesson(lesson(), {**CINE, "style": family})
            self.assertEqual({p["transition"]["in"] for p in plans}, {"fade"}, family)  # prefs.transition only pre-fills the panel
            self.assertEqual([p["motion"] for p in plans], [p["motion"] for p in self.plain], family)
            self.assertEqual([p["camera"]["movement"] for p in plans], [p["camera"]["movement"] for p in self.plain], family)

    def test_classic_mode_has_no_plan(self):
        for settings in ({"mode": "classic", "style": "academic"}, {"style": "children_education"}, {}):
            self.assertEqual(C.compose_lesson(lesson(), settings), [None] * 8, settings)
        self.assertIsNone(C.compose_scene(lesson()[0], 0, 1, {"mode": "classic", "style": "academic",
                                                              "style_overrides": {"accent": "teal"}}))

    def test_new_transitions_and_motion_none(self):
        for name, seconds in (("soft_fade", 0.8), ("zoom", 0.6), ("wipe", 0.6)):
            with self.subTest(transition=name):
                self.assertIn(name, C.TRANSITIONS)
                self.assertEqual(C.TRANSITION_SECONDS[name], seconds)
                plans = C.compose_lesson(lesson(), {**CINE, "transitions": name})
                self.assertEqual({p["transition"]["in"] for p in plans}, {name})
                self.assertEqual({p["transition"]["out"] for p in plans}, {name})
                self.assertEqual({p["transition"]["duration"] for p in plans}, {seconds})
                self.assertEqual([C.validate_plan(p) for p in plans], [[]] * 8)
                still = C.compose_lesson(lesson(), {**CINE, "transitions": name, "motion": "none"})
                expected = "soft_fade" if name == "soft_fade" else "fade"  # zoom and wipe move: a fade without motion
                self.assertEqual({p["transition"]["in"] for p in still}, {expected})
                self.assertEqual({p["transition"]["out"] for p in still}, {expected})
        self.assertEqual(set(C.TRANSITIONS), {"cut", "fade", "soft_fade", "crossfade", "slide", "zoom", "wipe"})
        self.assertEqual(C.TRANSITION_SECONDS, {"cut": 0.0, "fade": 0.5, "soft_fade": 0.8, "crossfade": 0.6, "slide": 0.55,
                                                "zoom": 0.6, "wipe": 0.6})
        self.assertEqual(set(S.TRANSITION_PREFS), set(C.TRANSITIONS))  # every preferred transition is one the plans know

    def test_a_scene_style_override_from_visual_review(self):
        scenes = lesson()
        scenes[1]["visual_review"] = {"composition": {"status": "changed", "overrides": {"style_accent": "teal"}}}
        plans = C.compose_lesson(scenes, CINE)
        look = plans[1]["style"]["look"]
        self.assertEqual(look["scene_overrides"], {"accent": "teal"})
        self.assertEqual(look["overrides"], {})
        self.assertEqual(look["css"]["--st-accent"], S.ACCENTS["teal"]["dark"][0])
        self.assertNotEqual(look["fingerprint"], plans[0]["style"]["look"]["fingerprint"])
        self.assertEqual([p["style"]["look"]["scene_overrides"] for i, p in enumerate(plans) if i != 1], [{}] * 7)
        self.assertEqual(plans[1]["review_status"], "changed")
        self.assertEqual((plans[1]["template"], plans[1]["fingerprint"]), (self.plain[1]["template"], self.plain[1]["fingerprint"]))
        self.assertEqual(C.validate_plan(plans[1]), [])
        # the scene wins over the lesson; a pending review changes nothing
        lesson_gold = C.compose_lesson(scenes, {**CINE, "style": "academic", "style_overrides": {"accent": "gold"}})
        self.assertEqual(lesson_gold[1]["style"]["look"]["css"]["--st-accent"], S.ACCENTS["teal"]["light"][0])
        self.assertEqual(lesson_gold[0]["style"]["look"]["css"]["--st-accent"], S.ACCENTS["gold"]["light"][0])
        scenes[1]["visual_review"]["composition"]["status"] = "pending"
        self.assertEqual(C.compose_lesson(scenes, CINE)[1]["style"]["look"]["scene_overrides"], {})
        scenes[1]["visual_review"] = {"composition": {"status": "changed", "overrides": {"style_background": "plain"}}}
        plain_bg = C.compose_lesson(scenes, CINE)[1]["style"]["look"]
        self.assertEqual((plain_bg["scene_overrides"], plain_bg["css"]["--st-bg-glow-1"]), ({"background": "plain"}, "transparent"))
        self.assertEqual(C.check_overrides({"style_accent": "teal", "style_background": "rich"}),
                         {"style_accent": "teal", "style_background": "rich"})
        self.assertEqual(C.check_overrides({"style_accent": "auto", "style_background": "default"}),
                         {"style_accent": "auto", "style_background": "default"})
        for bad in ({"style_accent": "neon"}, {"style_background": "image"}, {"style_text_size": "large"}):
            with self.assertRaises(Exception):
                C.check_overrides(bad)


class ApiIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert_isolated()
        ensure_user("stella")
        ensure_user("otto")
        cls.client = TestClient(server.app)
        cls.headers = auth("stella")

    def plan(self, settings, scenes=None):
        return self.client.post("/api/cinematic/plan", json={"scenes": lesson() if scenes is None else scenes, "settings": settings},
                                headers=self.headers)

    def test_vocabulary_lists_the_looks(self):
        body = self.client.get("/api/cinematic", headers=self.headers).json()
        looks = body["looks"]
        self.assertEqual(looks["default"], "cinematic_education")
        self.assertEqual(len(looks["families"]), 4)
        self.assertEqual([f["id"] for f in looks["families"]], list(S.FAMILIES))
        for family in looks["families"]:
            self.assertTrue({"id", "version", "label", "description", "tone", "prefs", "css", "preview"} <= set(family))
        self.assertEqual(looks["options"], S.options())
        self.assertEqual(set(body["transitions"]), {"cut", "fade", "soft_fade", "crossfade", "slide", "zoom", "wipe"})

    def test_plan_endpoint_carries_the_look(self):
        r = self.plan({**CINE, "style": "academic", "style_overrides": {"accent": "teal"}})
        self.assertEqual(r.status_code, 200, r.text)
        plans = r.json()["plans"]
        self.assertEqual(len(plans), 8)
        look = plans[0]["style"]["look"]
        self.assertEqual((look["id"], look["overrides"], look["css"]["--st-accent"]), ("academic@1", {"accent": "teal"},
                                                                                       S.ACCENTS["teal"]["light"][0]))
        legacy = self.plan(CINE).json()["plans"][0]["style"]["look"]
        self.assertEqual(legacy["id"], "cinematic_education@1")
        pinned = self.plan({**CINE, "style": "academic", "style_version": 99})
        self.assertEqual(pinned.status_code, 200, pinned.text)  # an unknown (newer) version: the latest this server knows
        self.assertEqual(pinned.json()["plans"][0]["style"]["look"]["version"], 1)
        classic = self.plan({"mode": "classic", "style": "academic"}).json()
        self.assertEqual(classic["plans"], [None] * 8)
        for name in ("soft_fade", "zoom", "wipe"):
            r = self.plan({**CINE, "transitions": name}, lesson()[:2])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["plans"][0]["transition"]["in"], name)

    def test_plan_endpoint_rejects_an_unknown_style_or_a_bad_override(self):
        for bad in ({"style": "neon"}, {"style": "Academic"}, {"style_overrides": {"accent": "neon"}},
                    {"style_overrides": {"glitter": "on"}}, {"style_overrides": {"accent": "url(x)"}}, {"style_overrides": "teal"},
                    {"style_overrides": ["accent"]}, {"style_version": "latest"}, {"style_version": 0}, {"transitions": "spiral"}):
            with self.subTest(bad=bad):
                r = self.client.post("/api/cinematic/plan", json={"scenes": [], "settings": {**CINE, **bad}}, headers=self.headers)
                self.assertEqual(r.status_code, 422, r.text)

    def test_background_endpoint_rejects_an_unknown_style(self):
        self.assertEqual(self.client.post("/api/cinematic/background", json={"style": "neon"}, headers=self.headers).status_code, 422)
        # a known style passes validation and reaches the AI gate (generation is off in the tests: 403, nothing generated)
        self.assertEqual(self.client.post("/api/cinematic/background", json={"style": "academic"}, headers=self.headers).status_code, 403)

    def test_background_request_of_the_default_family_is_the_legacy_one(self):
        # same prompt = same AI cache identity: backgrounds generated before Phase 17 stay reusable
        for wish in ("", "calm light", "  soft   focus "):
            legacy = C.background_request("academic", wish)
            styled_request = C.background_request("academic", wish, words=S.background_words({"style": "cinematic_education"}))
            self.assertEqual(styled_request.prompt, legacy.prompt)
        modern = C.background_request("modern", "x", words=S.background_words({"typography": "modern"}))
        self.assertEqual(modern.prompt, C.background_request("modern", "x").prompt)
        prompts = {C.background_request("academic", "x", words=S.background_words({"style": f})).prompt for f in S.FAMILIES}
        self.assertEqual(len(prompts), 4)  # each family asks for its own background
        for prompt in prompts:
            self.assertIn("no text, no people", prompt)

    def test_review_endpoint_accepts_scene_style_choices(self):
        pid = self.client.post("/save-history", json={"subject_name": "Styled", "scenes": lesson()}, headers=self.headers).json()["id"]
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "change",
                                                              "overrides": {"style_accent": "teal"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["plan"]["style"]["look"]["scene_overrides"], {"accent": "teal"})
        self.assertEqual(r.json()["review"]["overrides"], {"style_accent": "teal"})
        for bad in ({"style_accent": "neon"}, {"style_background": "image"}, {"style_text_size": "large"}):
            payload = {"project_id": pid, "scene_index": 1, "action": "change", "overrides": bad, "settings": CINE}
            self.assertEqual(self.client.post("/api/cinematic/review", json=payload, headers=self.headers).status_code, 422, bad)
        # choices add up; "auto" gives the accent back to the lesson and keeps the other choice
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "change",
                                                              "overrides": {"style_background": "plain"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.json()["plan"]["style"]["look"]["scene_overrides"], {"accent": "teal", "background": "plain"})
        r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": "change",
                                                              "overrides": {"style_accent": "auto"}, "settings": CINE}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["review"]["overrides"], {"style_background": "plain"})
        self.assertEqual(r.json()["plan"]["style"]["look"]["scene_overrides"], {"background": "plain"})
        stored = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()["scenes"][1]
        self.assertEqual(stored["cinematic_plan"]["style"]["look"]["scene_overrides"], {"background": "plain"})
        # a malformed saved review record (security audit: a 500) is ignored, the new choice is kept
        broken = {**lesson()[1], "visual_review": {"composition": {"status": "changed", "overrides": ["a"]}}}
        for action in ("change", "keep"):
            r = self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "action": action, "scene": broken,
                                                                  "overrides": {"style_accent": "teal"}, "settings": CINE}, headers=self.headers)
            self.assertEqual(r.status_code, 200, (action, r.text))
        self.assertEqual(r.json()["review"].get("status"), "approved")

    def test_a_style_only_change_keeps_an_approved_layout_approved(self):
        pid = self.client.post("/save-history", json={"subject_name": "Approved", "scenes": lesson()}, headers=self.headers).json()["id"]
        post = lambda body: self.client.post("/api/cinematic/review", json={"project_id": pid, "scene_index": 1, "settings": CINE, **body},
                                             headers=self.headers)
        r = post({"action": "keep"})
        self.assertEqual(r.json()["review"]["status"], "approved")
        approved_scene = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()["scenes"][1]
        r = post({"action": "change", "overrides": {"style_accent": "teal"}, "scene": approved_scene})
        self.assertEqual((r.json()["review"]["status"], r.json()["review"].get("overrides")), ("approved", {"style_accent": "teal"}))
        self.assertEqual(r.json()["plan"]["style"]["look"]["scene_overrides"], {"accent": "teal"})
        styled_scene = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()["scenes"][1]
        r = post({"action": "change", "overrides": {"style_accent": "auto"}, "scene": styled_scene})  # undo: still approved
        self.assertEqual(r.json()["review"]["status"], "approved")
        self.assertNotIn("overrides", r.json()["review"])
        # a layout change is still a change
        r = post({"action": "change", "overrides": {"camera": "static"}, "scene": styled_scene})
        self.assertEqual(r.json()["review"]["status"], "changed")
        # Keep on an approval that carries the scene's style (e.g. after it went stale) keeps those choices
        accented = {**lesson()[1], "visual_review": {"composition": {"status": "approved", "overrides": {"style_accent": "teal"}}}}
        r = post({"action": "keep", "scene": accented})
        self.assertEqual((r.json()["review"]["status"], r.json()["review"].get("overrides")), ("approved", {"style_accent": "teal"}))
        self.assertEqual(r.json()["plan"]["style"]["look"]["scene_overrides"], {"accent": "teal"})

    def projects(self, user="stella"):
        with database.SessionLocal() as db:
            uid = db.query(models.User.id).filter(models.User.username == user).scalar()
            return db.query(models.Project).filter(models.Project.user_id == uid).count()

    def test_style_route_keeps_the_style_with_the_same_lesson(self):
        scenes = lesson()[:3]
        pid = self.client.post("/save-history", json={"subject_name": "Restyle", "scenes": scenes}, headers=self.headers).json()["id"]
        before = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()
        count = self.projects()
        r = self.client.post("/api/cinematic/style", json={"project_id": pid, "style": "academic", "style_version": 1,
                                                             "style_overrides": {"accent": "teal", "text_size": "default"}}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        choice = {"style": "academic", "style_version": 1, "style_overrides": {"accent": "teal"}}
        # Phase 20: the reply also gives the lesson's new revision (the write is compare-and-set; the editor stays current)
        self.assertEqual(set(r.json()), {"cinematic_style", "revision"})
        self.assertEqual(r.json()["cinematic_style"], choice)
        self.assertEqual(r.json()["revision"], self.client.get(f"/api/editor/{pid}", headers=self.headers).json()["revision"])
        self.assertEqual(self.projects(), count)  # in place: no new history row
        after = self.client.get(f"/api/projects/{pid}", headers=self.headers).json()
        self.assertEqual(after["cinematic_style"], choice)
        self.assertEqual({k: v for k, v in after.items() if k != "cinematic_style"}, before)  # nothing else touched
        # an unknown (newer) version is kept as the latest this server knows; null removes the style (the original look)
        r = self.client.post("/api/cinematic/style", json={"project_id": pid, "style": "corporate_training", "style_version": 7},
                             headers=self.headers)
        self.assertEqual(r.json()["cinematic_style"], {"style": "corporate_training", "style_version": 1, "style_overrides": {}})
        r = self.client.post("/api/cinematic/style", json={"project_id": pid, "style": None}, headers=self.headers)
        self.assertEqual((r.status_code, r.json()["cinematic_style"], set(r.json())), (200, None, {"cinematic_style", "revision"}))
        self.assertNotIn("cinematic_style", self.client.get(f"/api/projects/{pid}", headers=self.headers).json())
        self.assertEqual(self.projects(), count)

    def test_style_route_refuses_other_lessons_and_bad_choices(self):
        pid = self.client.post("/save-history", json={"subject_name": "Mine", "scenes": lesson()[:1]}, headers=self.headers).json()["id"]
        for user, project in (("otto", pid), ("stella", 999999)):
            r = self.client.post("/api/cinematic/style", json={"project_id": project, "style": "academic"}, headers=auth(user))
            self.assertEqual(r.status_code, 404, (user, project))
        for bad in ({"style": "neon"}, {"style": "academic", "style_version": 0}, {"style": "academic", "style_version": 1001},
                    {"style": "academic", "style_version": "latest"}, {"style": "academic", "style_overrides": {"accent": "neon"}},
                    {"style": "academic", "style_overrides": {"glitter": "on"}}, {"style": "academic", "style_overrides": "teal"}):
            with self.subTest(bad=bad):
                r = self.client.post("/api/cinematic/style", json={"project_id": pid, **bad}, headers=self.headers)
                self.assertEqual(r.status_code, 422, r.text)
        self.assertNotIn("cinematic_style", self.client.get(f"/api/projects/{pid}", headers=self.headers).json())  # nothing stored
        self.assertEqual(self.client.post("/api/cinematic/style", json={"project_id": pid, "style": "academic"}).status_code, 401)

    def test_save_history_keeps_a_cleaned_cinematic_style_and_drops_invalid_ones(self):
        # server.py stores styles.lesson_choice(cinematic_style): an unknown family is not stored, an invalid override
        # is dropped (the family kept), an unknown version becomes the latest; a lesson without one is unchanged
        def saved(style, **extra):
            body = {"subject_name": "S", "scenes": [{"type": "content", "title": "x", "narration": "y"}], **extra}
            if style is not ...:
                body["cinematic_style"] = style
            r = self.client.post("/save-history", json=body, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            return self.client.get(f"/api/projects/{r.json()['id']}", headers=self.headers).json()

        kept = saved({"style": "children_education", "style_version": 1, "style_overrides": {"accent": "teal", "motion": "default"}})
        self.assertEqual(kept["cinematic_style"], {"style": "children_education", "style_version": 1, "style_overrides": {"accent": "teal"}})
        self.assertEqual(saved({"style": "academic", "style_version": 99})["cinematic_style"]["style_version"], 1)
        self.assertEqual(saved({"style": "academic", "style_overrides": {"accent": "neon"}})["cinematic_style"],
                         {"style": "academic", "style_version": 1, "style_overrides": {}})
        self.assertNotIn("cinematic_style", saved({"style": "neon"}))
        self.assertNotIn("cinematic_style", saved({"style": "<script>"}))
        self.assertNotIn("cinematic_style", saved(None))
        self.assertNotIn("cinematic_style", saved(...))  # an old page that sends nothing
        # any type in the saved JSON (security audit: a list or object family gave a 500): never stored, never a 500
        for odd in ({"style": ["academic"]}, {"style": {"a": 1}}, {"style": "academic", "style_version": [1]},
                    {"style": "academic", "style_overrides": ["accent"]}):
            with self.subTest(odd=odd):
                kept = saved(odd).get("cinematic_style")
                self.assertTrue(kept is None or kept == {"style": "academic", "style_version": 1, "style_overrides": {}}, kept)

    def test_save_history_rejects_a_non_object_cinematic_style_with_422(self):
        for bad in ("academic", ["academic"], 5):
            r = self.client.post("/save-history", json={"subject_name": "S", "scenes": [], "cinematic_style": bad}, headers=self.headers)
            self.assertEqual(r.status_code, 422, bad)


if __name__ == "__main__":
    unittest.main()
