"""Unit tests for aadhi.evals.metrics on synthetic screenplays (values computed by hand)."""

from __future__ import annotations

import json

import pytest

from aadhi.evals import metrics as M
from aadhi.pipeline.base import Issue
from aadhi.providers.base import Usage
from aadhi.schemas.manifest import AssetManifest, BeatAudio, MediaInfo, SceneAudio, SceneMedia
from aadhi.schemas.screenplay import Screenplay
from tests.evals.factories import lecture, lecture_dict, minimal_lecture, misconception_board_lecture


@pytest.fixture(scope="module")
def sp() -> Screenplay:
    return lecture()


@pytest.fixture(scope="module")
def metrics(sp: Screenplay) -> dict:
    return M.compute_metrics(sp, M.MetricInputs(target_minutes=10, chunk_ids=["c0001", "c0002", "c0003", "c0004"]))


# --- helpers ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("**bold** and *it*", "bold and it"),
        ("`code` [[keyword]] $V = IR$", "code keyword V = IR"),
        (r"costs \$5 and 2 \* 3", "costs $5 and 2 * 3"),
        ("", ""),
        (None, ""),
    ],
)
def test_plain_text_strips_rich_lite(raw, plain):
    assert M.plain_text(raw) == plain


def test_pct_and_percentile_edges():
    assert M.pct(1, 3) == 33.33
    assert M.pct(5, 0) is None
    assert M._p90([]) is None
    assert M._p90([5]) == 5
    assert M._p90(list(range(1, 11))) == 9
    assert M._mean([]) is None


def test_word_count_handles_indic_scripts_and_blank():
    assert M.word_count("ஓம் விதி மின்னோட்டம்") == 3
    assert M.word_count("  ") == 0
    assert M.word_count(None) == 0


# --- groups ------------------------------------------------------------------------------------------


def test_schema_and_structure(metrics):
    assert metrics["schema"] == {"valid": True, "error_count": 0, "errors": []}
    s = metrics["structure"]
    assert (s["scenes"], s["beats"], s["board_items"], s["chapters"]) == (8, 14, 8, 1)
    assert s["scene_types"] == {
        "ai_video": 1, "chapter_card": 1, "content": 2, "example": 1, "quiz_checkpoint": 1, "simulation": 1, "title": 1,
    }


def test_schema_check_reports_invalid_documents():
    data = lecture_dict()
    data["scenes"][3]["correct_index"] = 9  # quiz answer out of range
    out = M.schema_check(data)
    assert out["valid"] is False and out["error_count"] >= 1
    assert any("correct_index" in e for e in out["errors"])
    assert M.schema_check("not a screenplay")["valid"] is False


def test_board_fit(metrics):
    b = metrics["board"]
    assert b["board_scenes"] == 3  # the title scene has an empty board
    assert b["items_total"] == 8
    assert b["items_per_scene_mean"] == pytest.approx(2.667)
    assert b["items_per_scene_max"] == 4
    assert b["scenes_over_item_limit"] == 0
    assert b["chars_per_item_mean"] == pytest.approx(41.25)  # 330 visible chars / 8 items
    assert b["chars_per_item_max"] == 200
    assert b["items_over_char_limit"] == 1 and b["items_over_char_limit_pct"] == 12.5
    assert b["unrevealed_items_pct"] == 12.5  # bullet b1 is never revealed
    assert b["kinds"]["example_step"] == 3


def test_board_limits_and_tables_code():
    data = lecture_dict()
    board = data["scenes"][1]["board"]
    board += [{"id": f"x{i}", "kind": "bullet", "text": "short"} for i in range(4)]  # 8 items > limit 7
    board.append({"id": "t1", "kind": "table", "headers": ["a", "b"], "rows": [["1", "2"], ["3", "4"], ["5", "6"]]})
    data["scenes"][2]["board"].append({"id": "code1", "kind": "code", "language": "python", "code": "a=1\nb=2\n"})
    b = M.board_metrics(Screenplay.model_validate(data))
    assert b["scenes_over_item_limit"] == 1
    assert b["scenes_over_item_limit_pct"] == pytest.approx(33.33)
    assert b["table_rows_max"] == 3
    assert b["code_lines_max"] == 2


def test_board_item_chars_excludes_latex_code_tables(sp):
    items = {i.id: i for s in sp.scenes if s.type == "content" for i in getattr(s, "board", [])}
    assert M.board_item_chars(items["h1"]) == len("Ohm's law")
    assert M.board_item_chars(items["f1"]) == len("voltage equals current times resistance")


def test_pacing(metrics):
    p = metrics["pacing"]
    assert p["beats"] == 14 and p["words_total"] == 170
    assert p["words_per_beat_max"] == 50
    assert p["beats_too_long"] == 1
    assert p["beats_too_short"] == 1  # the 2-word quiz reveal beat is exempt
    assert p["pause_seconds_total"] == 2.0
    # 7 narrated scenes x (0.5 lead + 0.8 tail) + 68 s speech + 2 s pause + 7 gaps x 0.15
    # + quiz countdown 8 + reveal hold 1 + silent chapter card 4 = 93.15 s
    assert p["est_minutes"] == pytest.approx(1.55)
    assert p["est_vs_target_ratio"] == pytest.approx(0.155)
    assert p["est_vs_target_pct_error"] == pytest.approx(84.5)


def test_estimate_minutes_uses_language_speaking_rate():
    en = M.estimate_minutes(minimal_lecture(150))  # 60 s speech + 1.3 s lead/tail
    assert en == pytest.approx(61.3 / 60)
    ta = M.estimate_minutes(minimal_lecture(150, language="ta-IN"))
    assert ta > en  # Tamil words are longer: fewer words per minute
    assert M.pacing_metrics(minimal_lecture(10), None)["est_vs_target_ratio"] is None


def test_objectives(metrics):
    o = metrics["objectives"]
    assert (o["total"], o["taught"], o["assessed"], o["practiced"]) == (3, 2, 1, 1)
    assert o["taught_pct"] == pytest.approx(66.67)
    assert o["untaught"] == ["obj-extra"]
    assert o["unassessed"] == ["obj-extra", "obj-power"]


def test_objective_assessed_through_concept_and_quiz_panel():
    data = lecture_dict()
    # obj-extra gets a concept that only a quiz panel scene covers.
    data["learning_objectives"][2]["concept_ids"] = ["c-basics"]
    data["scenes"][5]["concept_id"] = "c-basics"
    data["scenes"][5]["side_panel"] = {
        "kind": "quiz", "rationale": "retrieval", "quiz": {"question": "?", "options": ["a", "b"], "correct_index": 0},
    }
    o = M.objective_metrics(Screenplay.model_validate(data))
    assert "obj-extra" not in o["untaught"]
    assert "obj-extra" not in o["unassessed"]


def test_misconceptions(metrics):
    m = metrics["misconceptions"]
    assert (m["total"], m["targeted"], m["in_quiz"], m["on_board"]) == (2, 1, 1, 0)
    assert m["untargeted"] == ["m-orphan"]
    board = M.misconception_metrics(misconception_board_lecture())
    assert board["on_board"] == 1 and board["targeted"] == 1


def test_quiz(metrics):
    q = metrics["quiz"]
    assert q["quiz_scenes"] == 1 and q["core_concepts"] == 2
    assert q["quizzes_per_concept"] == 0.5
    assert q["max_concepts_between_quizzes"] == 2
    assert q["answer_positions"] == {"1": 1}
    assert q["answer_position_max_share_pct"] == 100.0
    assert q["distractors"] == 3
    assert q["distractors_with_misconception_pct"] == pytest.approx(33.33)
    assert q["wrong_feedback_coverage_pct"] == pytest.approx(33.33)
    assert q["higher_order_pct"] == 100.0
    assert q["quizzes_per_10_min"] == pytest.approx(1 / 0.155, rel=1e-3)


def test_quiz_metrics_without_quizzes():
    q = M.quiz_metrics(minimal_lecture(), 0.0)
    assert q["quiz_scenes"] == 0
    assert q["answer_position_max_share_pct"] is None
    assert q["quizzes_per_10_min"] is None


def test_grounding_with_and_without_chunks(sp, metrics):
    g = metrics["grounding"]
    assert g["beats"] == 13  # title beats make no claims
    assert g["beats_with_refs"] == 2 and g["beats_with_refs_pct"] == pytest.approx(15.38)
    assert g["refs_total"] == 6
    assert (g["chunks_total"], g["chunks_cited"], g["unknown_refs"]) == (4, 3, 1)
    assert g["unknown_ref_ids"] == ["c9999"]
    assert g["source_coverage_pct"] == 75.0
    no_chunks = M.grounding_metrics(sp, None)
    assert no_chunks["unknown_refs"] is None and no_chunks["source_coverage_pct"] is None


def test_media_and_manim(metrics, sp):
    media = metrics["media"]
    assert media["side_panels"] == {"chart": 1, "manim": 1}
    assert media["visual_scenes"] == 4
    assert (media["rationale_required"], media["rationale_present"]) == (3, 2)
    manim = metrics["manim"]
    assert (manim["specs"], manim["template"], manim["freeform"]) == (2, 1, 1)
    assert manim["template_ratio_pct"] == 50.0
    assert manim["unknown_templates"] is None
    assert M.manim_metrics(sp, ["function_plot"])["unknown_templates"] == 1
    assert M.manim_metrics(sp, ["equation_steps"])["unknown_templates"] == 0


def test_worked_examples(metrics):
    w = metrics["worked_examples"]
    assert (w["example_scenes"], w["steps"], w["blank_steps"], w["blank_filled"]) == (1, 3, 2, 1)
    assert w["blank_filled_pct"] == 50.0
    assert w["fills_after_pause_pct"] == 100.0


def test_lint_metrics_accepts_issues_and_dicts():
    issues = [
        Issue(code="board.too_many_items", severity="warning", message="x", scene_id="s1"),
        Issue(code="objective.untaught", severity="error", message="y", scene_id="s2"),
        {"code": "grounding.unsupported_claim", "severity": "error", "message": "z", "scene_id": "s2",
         "source": "critic"},
        {"code": "board.item_unrevealed", "severity": "info", "message": "i"},
    ]
    out = M.lint_metrics(issues, n_scenes=4)
    assert (out["total"], out["errors"], out["warnings"], out["infos"]) == (4, 2, 1, 1)
    assert out["scenes_with_errors"] == 1
    assert out["by_source"] == {"critic": 1, "lint": 3}
    assert list(out["by_code"]) == sorted(out["by_code"])
    assert out["per_scene"] == 1.0
    assert M.lint_metrics([], 0)["per_scene"] is None


def test_cost_metrics_groups_by_operation_stage_model():
    records = [
        M.UsageRecord(Usage("gemini", "gemini-2.5-pro", "llm", input_tokens=1000, output_tokens=200), 0.01, "plan"),
        M.UsageRecord(Usage("gemini", "gemini-2.5-flash", "llm", input_tokens=500, output_tokens=100), 0.002, "script"),
        M.UsageRecord(Usage("edge", "edge", "tts", characters=1200, seconds=60.0), 0.0, "assets"),
    ]
    c = M.cost_metrics(records)
    assert c["total_usd"] == pytest.approx(0.012)
    assert c["calls"] == 3
    assert (c["input_tokens"], c["output_tokens"], c["tts_characters"]) == (1500, 300, 1200)
    assert c["by_stage"] == {"assets": 0.0, "plan": 0.01, "script": 0.002}
    assert c["by_operation"]["llm"] == pytest.approx(0.012)
    assert set(c["by_model"]) == {"gemini/gemini-2.5-pro", "gemini/gemini-2.5-flash", "edge/edge"}
    assert M.cost_metrics(None, pricing_available=False)["pricing_available"] is False


def test_audio_metrics_from_manifest():
    sp = minimal_lecture(150)
    manifest = AssetManifest(
        audio={
            "s1": SceneAudio(
                scene_id="s1", asset_key="scene_audio-1", duration=62.0,
                beats=[BeatAudio(beat_id="s1-b1", offset=0.0, speech_duration=60.0, words_estimated=True)],
            )
        },
        media={"s1": SceneMedia(scene_id="s1", main=MediaInfo(kind="image", mime="image/png", source="fallback"),
                                warnings=["fell back"])},
        stale_scenes=["s1"],
    )
    a = M.audio_metrics(sp, manifest)
    assert a["missing_audio_scenes"] == 0
    assert a["audio_minutes"] == pytest.approx(62 / 60, abs=1e-3)
    assert a["speech_wpm"] == 150.0
    assert a["words_estimated_pct"] == 100.0
    assert (a["stale_scenes"], a["media_warnings"], a["fallback_media"]) == (1, 1, 1)
    assert a["runtime_minutes_est"] == pytest.approx((62 + 1.3) / 60, abs=1e-3)
    empty = M.audio_metrics(sp, AssetManifest())
    assert empty["missing_audio_scenes"] == 1 and empty["speech_wpm"] is None


def test_compute_metrics_is_deterministic_and_json_serialisable(sp):
    inputs = M.MetricInputs(target_minutes=10, chunk_ids=["c0001"], extra={"source": {"pages": 2}})
    first = json.dumps(M.compute_metrics(sp, inputs), sort_keys=True)
    second = json.dumps(M.compute_metrics(lecture(), inputs), sort_keys=True)
    assert first == second
    assert json.loads(first)["source"] == {"pages": 2}
    assert "audio" not in json.loads(first)


def test_flatten_and_get_metric(metrics):
    flat = M.flatten_metrics(metrics)
    assert flat["schema.valid"] == 1.0
    assert flat["structure.scenes"] == 8.0
    assert flat["lint.total"] == 0.0
    assert "objectives.untaught" not in flat  # lists are dropped
    assert "metrics_version" not in flat  # strings are dropped
    assert M.get_metric(metrics, "quiz.quiz_scenes") == 1
    assert M.get_metric(metrics, "quiz.nope") is None
    assert M.get_metric(metrics, "structure.scenes.deeper") is None


def test_headline_metrics_exist_in_output(metrics):
    flat = M.flatten_metrics(metrics)
    missing = [m.key for m in M.HEADLINE_METRICS if m.key not in flat and M.get_metric(metrics, m.key) is not None]
    assert missing == []
    assert all(d in ("higher", "lower") for d in M.METRIC_DIRECTIONS.values())


def test_brief_metrics_count_cited_set_aside_and_unaccounted_chunks():
    from aadhi.evals.metrics import METRIC_DIRECTIONS, brief_metrics
    from aadhi.pipeline.base import BriefConcept, BriefFact, ConceptBrief, SkippedChunk

    assert brief_metrics(None, ["c0001"]) == {"available": False}
    brief = ConceptBrief(
        concepts=[BriefConcept(key="ohm", name="Ohm's law", source_refs=["c0002"],
                               key_facts=[BriefFact(text="V = IR", source_refs=["c0003"])])],
        teaching_order=["ohm"], source_questions=["What is R?"], source_question_refs=["c0005"],
        skipped_chunks=[SkippedChunk(chunk_id="c0001", reason="administrative"),
                        SkippedChunk(chunk_id="c0006", reason="scaffolding"),
                        SkippedChunk(chunk_id="c0099", reason="duplicate")],  # not a chunk of this source
    )
    m = brief_metrics(brief, ["c0001", "c0002", "c0003", "c0004", "c0005", "c0006"])
    assert m == {"available": True, "concepts": 1, "source_questions": 1, "chunks": 6, "cited_chunks": 3,
                 "skipped_chunks": 2, "skipped_by_reason": {"administrative": 1, "scaffolding": 1},
                 "unaccounted_chunks": 1, "coverage": 0.5}
    assert brief_metrics(brief, [])["coverage"] is None
    assert METRIC_DIRECTIONS["brief.unaccounted_chunks"] == "lower"
