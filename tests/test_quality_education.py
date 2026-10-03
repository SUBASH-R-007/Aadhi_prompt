"""Backend tests of the education family of the Quality & Consistency Engine (Phase 18): the terminology registry and its
checks (capitalisation in emphasised places, hyphen / space variants, abbreviations never spelled out or spelled out two
ways), concept names, formulas in every MathJax form (single $ included) and their symbols, code (colours, mixed
languages, indentation, long lines and blocks), diagram labels, AI pictures, the optional assistant (AI-assisted mode
only, a local stand-in model: valid, malformed then repaired, failing, slow, unavailable; never in automatic mode;
memoized), silence on the clean lessons in all four Phase 17 styles, determinism, no mutation and robustness.

Run from the repo root:
  PATH="$(pwd)/.venv/Scripts:$PATH" ./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_quality_education.py"

No server call, no browser, no real AI provider: the family is pure data over the lesson.
"""
import copy
import json
import os
import time
import unittest
from unittest import mock

from backend_env import assert_isolated  # first: throwaway database

import cinematic as C  # noqa: E402
import quality as Q  # noqa: E402
import quality_education as E  # noqa: E402
import scene_intent as SI  # noqa: E402
import styles  # noqa: E402
import visual_director as VD  # noqa: E402
from test_cinematic import CINE, lesson as cinematic_lesson  # noqa: E402
from test_sync_director import real_lesson  # noqa: E402
from test_visual_director import lesson as director_lesson, teacher  # noqa: E402

AI = {**CINE, "director": "ai", "composer_provider": "fake"}
FAKE_ENV = {"AI_FAKE_PROVIDER": "1", "FAKE_LLM_MODE": "ok", "FAKE_LLM_SECONDS": "0"}
SEVERITY = {  # every rule of the family and the one severity it may use
    "education.term_casing": {"notice"}, "education.term_variant": {"notice"},
    "education.abbreviation_undefined": {"notice"}, "education.abbreviation_conflict": {"warning"},
    "education.concept_naming": {"notice"}, "education.symbol_meaning": {"notice"},
    "education.formula_unexplained": {"notice"}, "education.formula_notation": {"notice"},
    "education.code_unhighlighted": {"warning", "notice"}, "education.code_tabs_spaces": {"notice"},
    "education.code_long_lines": {"warning"}, "education.code_too_long": {"warning"},
    "education.code_mixed_languages": {"info"}, "education.label_casing": {"notice"}, "education.asset_labels": {"notice"},
    "education.ai_visual": {"info"}, "education.assistant_abbreviation": {"notice"},
    "education.assistant_concept_name": {"notice"},
}


def setUpModule():
    assert_isolated()


def scene(title, html, narration="Let us look at this.", **extra):
    return {"type": "content", "title": title, "html": html, "narration": narration, "presenter_plan": teacher(), **extra}


def planned(scenes, settings=CINE):
    """The scenes as a saved lesson has them: each with its composition plan and its visual direction."""
    scenes = copy.deepcopy(scenes)
    plans = C.compose_lesson(scenes, settings)
    for s, plan in zip(scenes, plans):
        if plan:
            s["cinematic_plan"] = plan
    for s, direction in zip(scenes, VD.directions_for(scenes, settings)):
        if direction:
            s["visual_direction"] = direction
    return scenes


def run(scenes, settings=CINE, concept_map=None, plan_settings=CINE, assist=False):
    """Every finding of the family (scene checks, then lesson checks) and the lesson context. assist: the report asks for
    the optional assistant (the core sets lesson.assist from the request)."""
    lesson = Q.Lesson(planned(scenes, plan_settings) if plan_settings else scenes, settings, concept_map)
    lesson.assist = assist
    found = []
    for i in range(lesson.count):
        found += E.scene_checks(lesson, i)
    found += E.lesson_checks(lesson)
    return found, lesson


def rule(found, rule_id):
    return [f for f in found if f["rule"] == rule_id]


def problems(found):
    return [f for f in found if f["severity"] != "info"]


def filler(n):
    return [scene(f"Part {k}", f"<p>Plain words number {k}.</p>") for k in range(n)]


class CleanLessonsTest(unittest.TestCase):
    def test_clean_fixtures_are_silent_in_every_style(self):
        for name, make in (("cinematic", cinematic_lesson), ("director", director_lesson), ("real", real_lesson)):
            for style in (None,) + styles.FAMILIES:
                settings = {**CINE, **({"style": style} if style else {})}
                with self.subTest(lesson=name, style=style):
                    found, _lesson = run(make(), settings, plan_settings=settings)
                    self.assertEqual([], [(f["rule"], f["scene"], f["message"]) for f in problems(found)])

    def test_only_the_ai_video_is_noted_once_on_the_cinematic_fixture(self):
        found, _lesson = run(cinematic_lesson())
        self.assertEqual(["education.ai_visual"], [f["rule"] for f in found])
        self.assertEqual("info", found[0]["severity"])
        self.assertEqual(7, found[0]["scene"])

    def test_classic_mode_and_unplanned_scenes_are_read_too(self):
        found, _lesson = run(director_lesson(), {"mode": "classic"}, plan_settings=None)
        self.assertEqual([], problems(found))


class TerminologyTest(unittest.TestCase):
    def test_differently_capitalised_keywords_are_a_notice_naming_both_scenes(self):
        found, _lesson = run([
            scene("Learning from data", '<p>Today we meet <span class="keyword">Machine Learning</span> and its uses.</p>'),
            scene("Models", '<p>In practice, <span class="keyword">machine learning</span> builds models.</p>'),
            scene("Training", "<p>We train the model on examples.</p>"),
        ])
        hits = rule(found, "education.term_casing")
        self.assertEqual(1, len(hits))
        f = hits[0]
        self.assertEqual(("terminology", "notice"), (f["dimension"], f["severity"]))
        self.assertEqual({"Machine Learning": [0], "machine learning": [1]}, f["evidence"]["forms"])
        self.assertEqual(1, f["scene"])
        self.assertIn("Scene 1 (“Learning from data”)", f["message"])
        self.assertIn("Scene 2 (“Models”)", f["message"])
        self.assertEqual("none", f["repair"]["kind"])  # terminology is content: never rewritten

    def test_sentence_start_and_title_case_headers_are_not_casing_problems(self):
        found, _lesson = run([
            scene("Plants", "<p><strong>Photosynthesis</strong> makes sugar.</p>"),
            scene("Leaves", "<p>Leaves use <strong>photosynthesis</strong> every day.</p>"),
            scene("Two ways", "<table><tr><th>Machine Learning</th><th>Fixed Rules</th></tr><tr><td>learns</td><td>fixed</td></tr></table>"),
            scene("Models", '<p>Here <span class="keyword">machine learning</span> adapts.</p>'),
            scene("Light", "<h3>Speed of Light</h3><p>Nothing beats the <strong>speed of light</strong>.</p>"),
        ])
        self.assertEqual([], rule(found, "education.term_casing"))

    def test_hyphen_and_space_variants_are_a_notice(self):
        found, _lesson = run([
            scene("Intro", '<p>We study <span class="keyword">machine-learning</span> today.</p>'),
            scene("More", '<p>Then <span class="keyword">machine learning</span> again.</p>'),
            scene("Even more", '<p>Again <span class="keyword">machine learning</span> here.</p>'),
        ])
        hits = rule(found, "education.term_variant")
        self.assertEqual(1, len(hits))
        self.assertEqual({"machine-learning": [0], "machine learning": [1, 2]}, hits[0]["evidence"]["forms"])
        self.assertEqual(0, hits[0]["scene"])  # the variant fewer scenes use
        self.assertEqual([], rule(found, "education.term_casing"))

    def test_plurals_are_one_term(self):
        found, lesson = run([
            scene("Cells", '<p>The <span class="keyword">chloroplasts</span> are green.</p>'),
            scene("Cell", '<p>One <span class="keyword">chloroplast</span> is small.</p>'),
        ])
        self.assertEqual([], problems(found))
        group = next(t for t in E.registry(lesson)["terminology"] if t["key"] == "chloroplast")
        self.assertEqual({"chloroplasts": [0], "chloroplast": [1]}, {v["form"]: v["scenes"] for v in group["variants"]})


class AbbreviationTest(unittest.TestCase):
    def test_an_abbreviation_never_spelled_out_is_a_notice(self):
        found, lesson = run([scene("Photos", "<p>Computers sort photos.</p>"), scene("Sorting", "<p>We use ML to sort photos.</p>")])
        hits = rule(found, "education.abbreviation_undefined")
        self.assertEqual(1, len(hits))
        self.assertEqual((1, "ML", [1]), (hits[0]["scene"], hits[0]["evidence"]["term"], hits[0]["evidence"]["scenes"]))
        self.assertIn("Scene 2 (“Sorting”)", hits[0]["message"])
        self.assertEqual({"expansion": None, "how": None, "scenes": [1], "ways": 0}, E.registry(lesson)["abbreviations"]["ML"])

    def test_spelled_out_in_every_accepted_way_is_silent(self):
        cases = {
            "long form first": [scene("Intro", "<p>Machine Learning (ML) finds patterns.</p>"), scene("Use", "<p>ML sorts photos.</p>")],
            "short form first": [scene("Intro", "<p>ML (machine learning) finds patterns.</p>"), scene("Use", "<p>ML sorts photos.</p>")],
            "in the narration": [scene("Intro", "<p>Patterns.</p>", "Machine learning, or ML, stands out. ML stands for machine learning."),
                                 scene("Use", "<p>ML sorts photos.</p>")],
            "initials of a term": [scene("Intro", '<p>We meet <span class="keyword">machine learning</span>.</p>'),
                                   scene("Use", "<p>ML sorts photos.</p>")],
            "not abbreviations": [scene("Note", "<p>NOTE: chapter II says CO2 is OK. KEY TAKEAWAYS FOR TODAY</p>")],
        }
        for name, scenes in cases.items():
            with self.subTest(case=name):
                found, _lesson = run(scenes)
                self.assertEqual([], rule(found, "education.abbreviation_undefined"))
                self.assertEqual([], rule(found, "education.abbreviation_conflict"))
        _found, lesson = run(cases["long form first"])
        self.assertEqual({"expansion": "Machine Learning", "how": "spelled_out", "scenes": [0, 1], "ways": 1},
                         E.registry(lesson)["abbreviations"]["ML"])

    def test_an_abbreviation_spelled_out_two_ways_is_a_warning(self):
        found, _lesson = run([
            scene("Intro", "<p>Machine Learning (ML) finds patterns.</p>"),
            scene("Use", "<p>ML sorts photos.</p>"),
            scene("Statistics", "<p>Maximum Likelihood (ML) estimates a value.</p>"),
        ])
        hits = rule(found, "education.abbreviation_conflict")
        self.assertEqual(1, len(hits))
        f = hits[0]
        self.assertEqual(("terminology", "warning", 2), (f["dimension"], f["severity"], f["scene"]))
        self.assertEqual({"Machine Learning": [0], "Maximum Likelihood": [2]}, f["evidence"]["expansions"])
        self.assertEqual("none", f["repair"]["kind"])


class ConceptTest(unittest.TestCase):
    def test_one_concept_titled_two_ways_is_a_notice(self):
        cmap = [{"id": "ml", "title": "Machine Learning"}]
        found, _lesson = run([
            scene("Machine Learning", "<p>Computers learn.</p>", concept_id="ml"),
            scene("Machine-learning (contd.)", "<p>More learning.</p>", concept_id="ml"),
        ], concept_map=cmap)
        hits = rule(found, "education.concept_naming")
        self.assertEqual(1, len(hits))
        self.assertEqual(("education", "notice", 1), (hits[0]["dimension"], hits[0]["severity"], hits[0]["scene"]))
        self.assertEqual({"Machine Learning": [0], "Machine-learning": [1]}, hits[0]["evidence"]["forms"])

    def test_the_same_title_without_a_concept_id_is_one_concept(self):
        found, _lesson = run([scene("Machine Learning", "<p>Learn.</p>"), scene("Machine-learning (contd.)", "<p>More.</p>")])
        self.assertEqual(1, len(rule(found, "education.concept_naming")))
        found, _lesson = run([scene("Machine Learning", "<p>Learn.</p>"), scene("Machine learning (contd.)", "<p>More.</p>")])
        self.assertEqual([], rule(found, "education.concept_naming"))  # sentence case and Title Case are both fine


class FormulaTest(unittest.TestCase):
    def test_inline_dollar_formulas_are_found_where_the_board_counts_miss_them(self):
        html = "<p>Momentum is $p = mv$ for a moving body.</p>"
        self.assertEqual([], SI.board_facts(html)["tex"])
        found, lesson = run([scene("Momentum", html, "Momentum depends on how heavy and how fast.")])
        reg = E.registry(lesson)["formulas"]
        self.assertEqual((1, [0]), (reg["count"], reg["scenes"]))
        hits = rule(found, "education.formula_unexplained")
        self.assertEqual(1, len(hits))
        self.assertEqual(["p", "m", "v"], hits[0]["evidence"]["symbols"])
        self.assertEqual(("formulas", 0), (hits[0]["dimension"], hits[0]["scene"]))

    def test_every_mathjax_form_is_found(self):
        forms = ["$$E = mc^2$$", "\\[E = mc^2\\]", "\\(E = mc^2\\)", "$E = mc^2$", "<div class='math-block'>E = mc²</div>"]
        for form in forms:
            with self.subTest(form=form):
                _found, lesson = run([scene("Energy", f"<p>{form}</p>", "where E is energy, m is mass and c is the speed of light")])
                self.assertEqual(1, E.registry(lesson)["formulas"]["count"])
        _found, lesson = run([scene("Prices", "<p>It costs $5 and $10 at most.</p>")])
        self.assertEqual(0, E.registry(lesson)["formulas"]["count"])  # prices are not formulas

    def test_symbols_explained_in_narration_or_labels_are_silent(self):
        found, _lesson = run([
            scene("Momentum", "<p>$p = mv$</p>", "Here p is momentum, m is mass and v is velocity."),
            scene("Speed", "<p>$s = d / t$</p>", "Look.", composition={"labels": ["s = Speed", "t = Time"]}),
        ])
        self.assertEqual([], rule(found, "education.formula_unexplained"))

    def test_one_symbol_with_two_meanings_is_a_notice_with_both_scenes(self):
        found, _lesson = run([
            scene("Speed", "<p>$v = s/t$</p>", "Look.", composition={"labels": ["v = Velocity", "s = Distance", "t = Time"]}),
            scene("Density", "<p>$k = m/v$</p>", "Look.", composition={"labels": ["k = Density", "m = Mass", "v = Volume"]}),
        ])
        hits = rule(found, "education.symbol_meaning")
        self.assertEqual(1, len(hits))
        self.assertEqual("v", hits[0]["evidence"]["value"])
        self.assertEqual({"Velocity": [0], "Volume": [1]}, hits[0]["evidence"]["meanings"])
        self.assertEqual(1, hits[0]["scene"])

    def test_two_symbols_for_one_named_quantity_is_a_notice(self):
        found, _lesson = run([
            scene("Speed", "<p>$v = s/t$</p>", "Look.", composition={"labels": ["v = Velocity", "s = Distance", "t = Time"]}),
            scene("Speed again", "<p>$V = s/t$</p>", "Look.", composition={"labels": ["V = Velocity", "s = Distance", "t = Time"]}),
        ])
        hits = rule(found, "education.formula_notation")
        self.assertEqual(1, len(hits))
        self.assertEqual({"v": [0], "V": [1]}, hits[0]["evidence"]["symbols"])
        self.assertEqual([], rule(found, "education.symbol_meaning"))

    def test_notation_of_operators_is_never_judged(self):
        found, _lesson = run([
            scene("Law", "<p>\\(F = ma\\)</p>", "Look.", composition={"labels": ["F = Force", "m = Mass", "a = Acceleration"]}),
            scene("Law again", "<p>\\(F = m \\cdot a\\)</p>", "Look.", composition={"labels": ["F = Force", "m = Mass", "a = Acceleration"]}),
        ])
        self.assertEqual([], problems(found))


class CodeTest(unittest.TestCase):
    def code(self, body, cls="language-python"):
        attr = f' class="{cls}"' if cls else ""
        return f"<pre><code{attr}>{body}</code></pre>"

    def test_a_language_the_page_cannot_colour_is_a_warning(self):
        found, _lesson = run([scene("Java", self.code("public class A {\n  int x = 1;\n}", "language-java"))])
        hits = rule(found, "education.code_unhighlighted")
        self.assertEqual(1, len(hits))
        self.assertEqual(("code", "warning", "java", "declared"), (hits[0]["dimension"], hits[0]["severity"],
                                                                    hits[0]["evidence"]["value"], hits[0]["evidence"]["how"]))
        self.assertIn("without colours", hits[0]["message"])

    def test_code_that_does_not_name_its_language_is_a_notice(self):
        found, _lesson = run([scene("Loop", self.code("def f(x):\n    print(x)", None))])
        hits = rule(found, "education.code_unhighlighted")
        self.assertEqual(1, len(hits))
        self.assertEqual(("notice", "python", "guessed"), (hits[0]["severity"], hits[0]["evidence"]["value"], hits[0]["evidence"]["how"]))

    def test_coloured_and_plain_code_is_silent(self):
        found, _lesson = run([
            scene("Python", self.code("for i in range(3):\n    print(i)")),
            scene("Output", self.code("0\n1\n2", "language-text")),
        ])
        self.assertEqual([], found)

    def test_mixed_languages_are_info(self):
        found, lesson = run([
            scene("Python", self.code("print(1)")),
            scene("JavaScript", self.code("console.log(1);", "language-js")),
        ])
        hits = rule(found, "education.code_mixed_languages")
        self.assertEqual(1, len(hits))
        self.assertEqual(("info", 1), (hits[0]["severity"], hits[0]["scene"]))
        self.assertEqual({"python": [0], "javascript": [1]}, hits[0]["evidence"]["languages"])
        self.assertEqual([{"language": "python", "scenes": [0], "coloured": True}, {"language": "javascript", "scenes": [1], "coloured": True}],
                         E.registry(lesson)["code_languages"])

    def test_tabs_mixed_with_spaces_long_lines_and_long_blocks(self):
        long_line = "result = compute_the_total_of_everything(first_value, second_value, third)"
        self.assertGreater(len(long_line), E.CODE_MAX_COLUMNS)
        found, _lesson = run([
            scene("Tabs", self.code("if True:\n\tprint(1)\nif True:\n    print(2)")),
            scene("Wide", self.code(long_line + "\nprint(result)")),
            scene("Tall", self.code("\n".join(f"x{k} = {k}" for k in range(25)))),
        ])
        tabs, wide, tall = (rule(found, r) for r in ("education.code_tabs_spaces", "education.code_long_lines", "education.code_too_long"))
        self.assertEqual([(0, "notice")], [(f["scene"], f["severity"]) for f in tabs])
        self.assertEqual([(1, "warning")], [(f["scene"], f["severity"]) for f in wide])
        self.assertEqual((1, len(long_line), 70), (wide[0]["evidence"]["lines"], wide[0]["evidence"]["longest"], wide[0]["evidence"]["limit"]))
        self.assertEqual([(2, "warning", 25)], [(f["scene"], f["severity"], f["evidence"]["lines"]) for f in tall])

    def test_code_findings_are_scene_checks(self):
        lesson = Q.Lesson([scene("Java", self.code("class A {}", "language-java"))], CINE)
        self.assertEqual(["education.code_unhighlighted"], [f["rule"] for f in E.scene_checks(lesson, 0)])


class DiagramTest(unittest.TestCase):
    def test_a_label_written_differently_from_the_board_is_a_notice(self):
        found, _lesson = run([scene("Genes", '<p>Deoxyribonucleic acid (DNA): the <span class="keyword">DNA</span> molecule.</p>',
                                    composition={"labels": ["Dna", "Gene"]})])
        hits = rule(found, "education.label_casing")
        self.assertEqual(1, len(hits))
        self.assertEqual(("diagrams", 0, "Dna", "DNA"), (hits[0]["dimension"], hits[0]["scene"], hits[0]["evidence"]["found"],
                                                         hits[0]["evidence"]["expected"]))
        found, _lesson = run([scene("Genes", '<p>Deoxyribonucleic acid (DNA): the <span class="keyword">DNA</span> molecule.</p>',
                                    composition={"labels": ["DNA", "Gene"]})])
        self.assertEqual([], problems(found))

    def test_one_picture_with_different_labels_is_a_notice(self):
        side = {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": "a" * 32, "selection": "matched"}
        found, _lesson = run([
            scene("Cell", "<p>A cell.</p>", visual_plan={"side": dict(side)}, composition={"labels": ["Nucleus", "Membrane"]}),
            scene("Cell again", "<p>The cell.</p>", visual_plan={"side": dict(side)}, composition={"labels": ["Cell wall"]}),
            scene("Cell once more", "<p>Cells.</p>", visual_plan={"side": dict(side)}, composition={"labels": ["Nucleus", "Membrane"]}),
        ])
        hits = rule(found, "education.asset_labels")
        self.assertEqual(1, len(hits))
        self.assertEqual(("a" * 32, 1, [[0, 2], [1]]), (hits[0]["evidence"]["target"], hits[0]["scene"], hits[0]["evidence"]["scenes"]))

    def test_ai_pictures_are_one_info_for_the_lesson(self):
        made = {"source": "AI_IMAGE", "media": "STATIC_IMAGE", "selection": "planned", "requires_generation": True}
        found, _lesson = run([
            scene("Forest", "<p>A forest.</p>", visual_plan={"side": made}),
            {"type": "ai_video", "title": "River", "prompt": "river", "narration": "A river.", "presenter_plan": teacher(enabled=False)},
            scene("Removed", "<p>Gone.</p>", visual_plan={"side": {**made, "selection": "removed"}}),
        ])
        hits = rule(found, "education.ai_visual")
        self.assertEqual(1, len(hits))
        self.assertEqual(("info", 0, [0, 1]), (hits[0]["severity"], hits[0]["scene"], hits[0]["evidence"]["scenes"]))
        self.assertIn("not checked", hits[0]["message"])


def dna_lesson():
    return [scene("Genes", '<p>The <span class="keyword">deoxyribonucleic acid</span> molecule carries genes.</p>'),
            scene("Copies", "<p>DNA is copied before a cell divides.</p>")]


def concept_lesson():
    return [scene("Neural networks", "<p>Layers of units.</p>", concept_id="nn"),
            scene("Artificial neural nets", "<p>Units that learn.</p>", concept_id="nn")]


CONCEPT_MAP = [{"id": "nn", "title": "Neural networks"}]


class AssistantTest(unittest.TestCase):
    def setUp(self):
        E._AI_MEMO.clear()

    def tearDown(self):
        E._AI_MEMO.clear()

    def assisted(self, scenes=None, mode="ok", concept_map=None, **env):
        with mock.patch.dict(os.environ, {**FAKE_ENV, "FAKE_LLM_MODE": mode, **env}):
            return run(scenes or dna_lesson(), AI, concept_map, assist=True)

    def test_the_ambiguous_pairs_are_ids_and_words_only(self):
        _found, lesson = run(dna_lesson(), AI)
        pairs = E.ambiguous_pairs(lesson)
        self.assertEqual([("p1", "abbreviation", "DNA", "deoxyribonucleic acid")], [(p["id"], p["kind"], p["a"], p["b"]) for p in pairs])

    def test_a_valid_answer_becomes_a_notice_suggested_by_the_assistant(self):
        found, lesson = self.assisted()
        hits = rule(found, "education.assistant_abbreviation")
        self.assertEqual(1, len(hits))
        f = hits[0]
        self.assertEqual(("terminology", "notice", 1), (f["dimension"], f["severity"], f["scene"]))
        self.assertEqual("suggested by the assistant", f["evidence"]["source"])
        self.assertEqual(["DNA", "deoxyribonucleic acid"], f["evidence"]["pair"])
        self.assertIn("Suggested by the assistant", f["message"])
        reg = E.registry(lesson)
        self.assertEqual({"status": "ok", "pairs": 1, "same": 1}, reg["terminology_assistant"])
        self.assertEqual({"expansion": "deoxyribonucleic acid", "how": "assistant", "scenes": [1], "ways": 0}, reg["abbreviations"]["DNA"])
        self.assertEqual(1, len(rule(found, "education.abbreviation_undefined")))  # the rules' own finding stays

    def test_two_names_of_one_concept(self):
        found, _lesson = self.assisted(concept_lesson(), concept_map=CONCEPT_MAP)
        hits = rule(found, "education.assistant_concept_name")
        self.assertEqual(1, len(hits))
        self.assertEqual(("education", "notice", ["Neural networks", "Artificial neural nets"]),
                         (hits[0]["dimension"], hits[0]["severity"], hits[0]["evidence"]["pair"]))

    def test_a_malformed_answer_is_repaired_once(self):
        found, lesson = self.assisted(mode="malformed_once")
        self.assertEqual("repaired", E.registry(lesson)["terminology_assistant"]["status"])
        self.assertEqual("repaired", rule(found, "education.assistant_abbreviation")[0]["evidence"]["status"])

    def test_failures_leave_the_rules_unchanged(self):
        automatic, _lesson = run(dna_lesson(), CINE)
        expected = sorted((f["rule"], f["id"]) for f in automatic)
        for mode, status in (("malformed", "invalid"), ("fabricate", "invalid"), ("fail", "failed")):
            E._AI_MEMO.clear()
            with self.subTest(mode=mode):
                found, lesson = self.assisted(mode=mode)
                self.assertEqual(status, E.registry(lesson)["terminology_assistant"]["status"])
                self.assertEqual(expected, sorted((f["rule"], f["id"]) for f in found))
                self.assertFalse(any(f["severity"] in ("error", "blocking") for f in found))

    def test_a_slow_model_times_out_within_the_budget(self):
        with mock.patch.object(E, "AI_BUDGET_SECONDS", 0.3):
            started = time.time()
            found, lesson = self.assisted(FAKE_LLM_SECONDS="2")
            self.assertLess(time.time() - started, 1.8)
        self.assertEqual("timeout", E.registry(lesson)["terminology_assistant"]["status"])
        self.assertEqual([], rule(found, "education.assistant_abbreviation"))

    def test_no_model_available(self):
        with mock.patch.dict(os.environ, {"AI_FAKE_PROVIDER": "0"}):
            found, lesson = run(dna_lesson(), AI, assist=True)
        self.assertEqual("unavailable", E.registry(lesson)["terminology_assistant"]["status"])
        self.assertEqual(1, len(rule(found, "education.abbreviation_undefined")))

    def test_never_asked_in_automatic_mode(self):
        with mock.patch.dict(os.environ, FAKE_ENV), mock.patch.object(E, "ask_terms") as ask,                 mock.patch.object(E, "fake_terms_model") as fake, mock.patch("source_documents.call_model") as call:
            for settings in (CINE, {**CINE, "director": "rules"}, {**AI, "director": "rules"}):
                found, lesson = run(dna_lesson(), settings, assist=True)
                self.assertNotIn("terminology_assistant", E.registry(lesson))
                self.assertEqual([], rule(found, "education.assistant_abbreviation"))
        ask.assert_not_called()
        fake.assert_not_called()
        call.assert_not_called()

    def test_an_ordinary_report_never_asks_even_in_ai_director_mode(self):
        with mock.patch.dict(os.environ, FAKE_ENV), mock.patch.object(E, "ask_terms") as ask,                 mock.patch("source_documents.call_model") as call:
            found, lesson = run(dna_lesson(), AI)  # no assist on the request
            self.assertNotIn("terminology_assistant", E.registry(lesson))
            found_classic, _l = run(dna_lesson(), {**AI, "mode": "classic"}, plan_settings=None, assist=True)  # not cinematic
            report = Q.evaluate_lesson(planned(dna_lesson()), AI)  # the core's report, as an ordinary request makes it
        ask.assert_not_called()
        call.assert_not_called()
        self.assertEqual([], rule(found, "education.assistant_abbreviation"))
        self.assertEqual([], rule(found_classic, "education.assistant_abbreviation"))
        self.assertEqual([], [f for f in report["issues"] if f["rule"].startswith("education.assistant_")])
        self.assertEqual(1, len(rule(found, "education.abbreviation_undefined")))  # the rules still report

    def test_a_failure_is_not_kept_so_a_later_request_retries(self):
        with mock.patch.object(E, "ask_terms", wraps=E.ask_terms) as ask:
            _found, failed = self.assisted(mode="fail")
            found, lesson = self.assisted(mode="ok")
        self.assertEqual(2, ask.call_count)
        self.assertEqual("ok", E.registry(lesson)["terminology_assistant"]["status"])
        self.assertEqual(1, len(rule(found, "education.assistant_abbreviation")))
        with mock.patch.object(E, "ask_terms", wraps=E.ask_terms) as ask:
            self.assisted(mode="fail")  # a kept answer is reused: no new call
        ask.assert_not_called()

    def test_one_call_per_lesson_and_memoized(self):
        with mock.patch.dict(os.environ, FAKE_ENV), mock.patch.object(E, "ask_terms", wraps=E.ask_terms) as ask:
            first, _l1 = run(dna_lesson(), AI, assist=True)
            second, _l2 = run(dna_lesson(), AI, assist=True)
        self.assertEqual(1, ask.call_count)
        self.assertEqual([f["id"] for f in first], [f["id"] for f in second])

    def test_nothing_ambiguous_asks_nothing(self):
        with mock.patch.dict(os.environ, FAKE_ENV), mock.patch.object(E, "ask_terms") as ask:
            _found, lesson = run(director_lesson(), AI, assist=True)
        ask.assert_not_called()
        self.assertEqual("skipped", E.registry(lesson)["terminology_assistant"]["status"])

    def test_answers_are_validated(self):
        self.assertEqual({"p1": True}, E.validate_answers({"answers": [{"id": "p1", "same": True}]}, ["p1", "p2"]))
        for bad in ({"answers": [{"id": "p9", "same": True}]}, {"answers": [{"id": "p1", "same": "yes"}]}, {"answers": "p1"},
                    {"answers": [{"id": ["p1"], "same": True}]}, [], {"answers": [{"id": "p1", "same": True}] * 3}):
            with self.subTest(bad=bad):
                with self.assertRaises(E.Invalid):
                    E.validate_answers(bad, ["p1", "p2"])


class ContractTest(unittest.TestCase):
    def crafted(self):
        """A lesson where every rule fires at least once."""
        side = {"source": "ASSET", "media": "STATIC_IMAGE", "asset_id": "c" * 32, "selection": "matched"}
        return [
            scene("Machine Learning", '<p>Today <span class="keyword">Machine Learning</span> and ML (machine learning) appear.</p>'
                  '<pre><code class="language-java">class A {}</code></pre>', visual_plan={"side": dict(side)},
                  composition={"labels": ["Nucleus"]}),
            scene("Machine-learning (contd.)", '<p>Then <span class="keyword">machine learning</span> and '
                  '<span class="keyword">machine-learning</span>. Maximum Likelihood (ML) too. We use GPU power.</p>'
                  "<pre><code class='language-python'>print(1)</code></pre>", visual_plan={"side": dict(side)},
                  composition={"labels": ["Cell wall"]}),
            scene("Speed", "<p>$v = s/t$ and $q = r$</p>", composition={"labels": ["v = Velocity", "s = Distance", "t = Time", "Machine LEARNING"]}),
            scene("Density", "<p>$k = m/v$ and $V = s/t$</p>", composition={"labels": ["k = Density", "m = Mass", "v = Volume", "V = Velocity"]}),
            {"type": "ai_video", "title": "A forest", "prompt": "forest", "narration": "A forest."},
        ]

    def test_every_finding_follows_the_contract(self):
        found, lesson = run(self.crafted())
        fired = {f["rule"] for f in found}
        for expected in ("education.term_casing", "education.term_variant", "education.abbreviation_undefined",
                         "education.abbreviation_conflict", "education.concept_naming", "education.symbol_meaning",
                         "education.formula_unexplained", "education.formula_notation", "education.code_unhighlighted",
                         "education.code_mixed_languages", "education.asset_labels", "education.ai_visual"):
            self.assertIn(expected, fired)
        for f in found:
            with self.subTest(rule=f["rule"], scene=f["scene"]):
                self.assertIn(f["rule"], SEVERITY)
                self.assertIn(f["severity"], SEVERITY[f["rule"]])
                self.assertIn(f["dimension"], ("terminology", "education", "formulas", "code", "diagrams"))
                self.assertEqual(("none", "not_repairable"), (f["repair"]["kind"], f["repair_status"]))
                self.assertNotIn("Scene 0", f["message"])  # 1-based for people
                self.assertTrue(f["scene"] is None or 0 <= f["scene"] < lesson.count)
                self.assertEqual(f, Q.issue(f["rule"], f["dimension"], f["severity"], f["message"], scene=f["scene"],
                                            element=f["element"], evidence=f["evidence"]))  # well-formed and stable
        self.assertEqual(len(found), len({f["id"] for f in found}))  # one finding per real problem

    def test_deterministic(self):
        first, l1 = run(self.crafted())
        second, l2 = run(self.crafted())
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(E.registry(l1), sort_keys=True), json.dumps(E.registry(l2), sort_keys=True))

    def test_the_input_is_never_changed(self):
        scenes = planned(self.crafted())
        settings = copy.deepcopy(AI)
        cmap = [{"id": "ml", "title": "Machine Learning"}]
        before = copy.deepcopy((scenes, settings, cmap))
        E._AI_MEMO.clear()
        with mock.patch.dict(os.environ, FAKE_ENV):
            lesson = Q.Lesson(scenes, settings, cmap)
            lesson.assist = True
            for i in range(lesson.count):
                E.scene_checks(lesson, i)
            E.lesson_checks(lesson)
            E.registry(lesson)
        E._AI_MEMO.clear()
        self.assertEqual(before, (scenes, settings, cmap))

    def test_what_cannot_be_read_never_crashes(self):
        odd = [None, 3, "text", {}, {"html": None, "title": None}, {"html": 42, "title": ["x"], "narration": {"a": 1}},
               {"html": "<pre><code class='language-'>", "title": "Open"}, {"html": "<div class='formula-block'>\\[", "title": "Broken"},
               {"html": "<p>$$ never closed and \\( neither", "composition": {"labels": [None, 5, {"text": None}, {"x": 1}]}},
               {"html": "<h2>" + "A" * 5000 + "</h2>" + "<p>ML " * 500, "composition": "labels", "visual_plan": {"side": "x"}},
               {"type": "quiz_checkpoint", "question": "What is F?", "options": ["Force", None, 3]},
               {"html": "<strong>" + "x-" * 400 + "</strong><span class='keyword'>(((</span>", "concept_id": 7,
                "visual_direction": {"concept": {"terms": "not a list"}}, "cinematic_plan": {"layers": "x"}}]
        lesson = Q.Lesson(odd, {**CINE, "director": "ai"}, [{"id": None}, "x", {"id": "a", "title": 5}])
        lesson.assist = True
        for i in range(lesson.count):
            self.assertIsInstance(E.scene_checks(lesson, i), list)
        with mock.patch.dict(os.environ, {"AI_FAKE_PROVIDER": "0"}):
            self.assertIsInstance(E.lesson_checks(lesson), list)
            self.assertIsInstance(E.registry(lesson), dict)
        self.assertEqual([], E.lesson_checks(Q.Lesson([], CINE)))

    def test_adversarial_text_stays_fast(self):
        bad = {"long word": "ML " + "a" * 20000, "spaces": "ML" + " " * 20000 + "(x)", "marks": "ML [A:" * 5000,
               "brackets": "ML " + "(" * 5000, "capitals": "ML " + "A" * 20000, "capital words": "ML " + "AB " * 7000,
               "dollars": "$a" * 5000, "blank lines": "x:" + "\n" * 5000, "means": "ML" + " " * 5000 + "means " + "a " * 5000}
        for name, text in bad.items():
            for where in ("html", "narration", "title", "label", "code"):
                s = scene("T", "<p>ML here</p>", "ML is used.")
                if where == "html":
                    s["html"] = "<p>" + text + "</p>"
                elif where == "code":
                    s["html"] = "<pre><code>" + text + "</code></pre>"
                elif where == "label":
                    s["composition"] = {"labels": [text]}
                else:
                    s[where] = text
                with self.subTest(text=name, where=where):
                    lesson = Q.Lesson([s, scene("U", "<p>ML again</p>")], AI)
                    lesson.facts(0)
                    lesson.understanding(0)  # the core's own derivations are not timed here
                    started = time.time()
                    E.scene_checks(lesson, 0)
                    E.lesson_checks(lesson)
                    E.registry(lesson)
                    self.assertLess(time.time() - started, 0.2)

    def test_the_report_runs_the_family_and_stays_fast(self):
        scenes = []
        for n in range(3):
            for s in director_lesson()[:10]:
                scenes.append({**s, "title": f"{s['title']} {n}"})
        scenes = planned(scenes)
        report = Q.evaluate_lesson(scenes, CINE)
        self.assertIn("quality_education", report["families"])
        self.assertFalse(any(l.startswith("quality_education") for l in report["limitations"]))
        for section in ("terminology", "concepts", "abbreviations", "formulas", "code_languages", "diagram_labels"):
            self.assertIn(section, report["registry"])
        self.assertLessEqual(len(report["registry"]["terminology"]), E.MAX_GROUPS)
        self.assertEqual([], [f for f in report["issues"] if f["rule"].startswith("education.") and f["severity"] != "info"])
        started = time.time()
        lesson = Q.Lesson(scenes, CINE)
        for i in range(lesson.count):
            E.scene_checks(lesson, i)
        E.lesson_checks(lesson)
        E.registry(lesson)
        self.assertLess(time.time() - started, 0.5)  # 30 scenes: the family's share of the report's 1 s


if __name__ == "__main__":
    unittest.main()


class PrismBundleTest(unittest.TestCase):
    """The page loads Prism's default bundle (HTML/XML/SVG, CSS, JavaScript) plus Python: those are coloured."""

    def test_html_and_css_are_highlighted_by_the_page(self):
        import quality_education as QE
        for lang in ("html", "xml", "svg", "css", "python", "javascript", "js", "py"):
            with self.subTest(lang=lang):
                self.assertIn(QE.LANG_ALIASES.get(lang, lang), QE.HIGHLIGHTED)
        self.assertNotIn(QE.LANG_ALIASES.get("cpp", "cpp"), QE.HIGHLIGHTED)

