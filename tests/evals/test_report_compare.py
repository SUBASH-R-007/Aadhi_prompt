"""report.json/report.md building and the compare diff."""

from __future__ import annotations

import json

import pytest

from aadhi.evals import compare as C
from aadhi.evals import report as RP
from aadhi.evals.metrics import MetricInputs, compute_metrics
from aadhi.evals.runner import FixtureResult, PipelineStages, RunConfig, StageRecord
from tests.evals.factories import lecture


def _result(name: str, status: str = "ok", **metric_overrides) -> FixtureResult:
    metrics = compute_metrics(lecture(), MetricInputs(target_minutes=10, chunk_ids=["c0001"]))
    for dotted, value in metric_overrides.items():
        group, key = dotted.split("__")
        metrics[group][key] = value
    return FixtureResult(
        name=name, source=f"{name}.pdf", kind="source", status=status,
        stages=[StageRecord("ingest", "ok", 0.5), StageRecord("plan", "ok" if status == "ok" else "failed", 1.0)],
        metrics=metrics if status == "ok" else {},
        failed_stage=None if status == "ok" else "plan",
        error=None if status == "ok" else "boom | with pipe\nand newline",
        options={"target_minutes": 10},
    )


def _report(tmp_path, results, name="run") -> dict:
    cfg = RunConfig(fixtures_dir=tmp_path, out_dir=tmp_path / name)
    stages = PipelineStages(functions={"ingest": print}, missing={"assets": "not importable"})
    settings_info = {"llm_provider": "fake", "llm_models": {"plan": "p"}, "tts_provider": "fake"}
    return RP.build_report(cfg, results, stages, settings_info, started=1_700_000_000.0, finished=1_700_000_012.5)


def test_build_report_summary_and_aggregate(tmp_path):
    results = [
        _result("a", **{"lint__errors": 2}),
        _result("b", **{"lint__errors": 4}),
        _result("c", status="failed"),
    ]
    report = _report(tmp_path, results)
    assert report["summary"]["fixtures"] == 3
    assert (report["summary"]["ok"], report["summary"]["failed"], report["summary"]["skipped"]) == (2, 1, 0)
    assert report["aggregate"]["lint.errors"] == 3.0  # mean over ok fixtures only
    assert report["aggregate"]["structure.scenes"] == 8.0
    assert report["duration_s"] == 12.5
    assert report["created_at"].startswith("2023-11-14T22:13:20")
    assert report["stages_available"]["ingest"] is True and report["stages_available"]["plan"] is False
    assert report["stages_missing"] == {"assets": "not importable"}
    assert report["config"]["provider"] == "fake"
    json.dumps(report)  # serialisable


def test_write_load_and_render_markdown(tmp_path):
    report = _report(tmp_path, [_result("ohm|law"), _result("broken", status="failed")])
    json_path, md_path = RP.write_report(tmp_path / "out", report)
    assert RP.load_report(tmp_path / "out") == RP.load_report(json_path)
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# Eval report: run")
    assert "ohm\\|law" in md  # pipes in names are escaped
    assert "boom \\| with pipe and newline" in md
    assert "## Headline metrics (mean over 1 ok fixtures)" in md
    assert "Objectives taught" in md and "`objectives.taught_pct`" in md
    assert "- Unavailable stages: assets (not importable)" in md
    assert not list((tmp_path / "out").glob("*.tmp"))


def test_load_report_rejects_other_json(tmp_path):
    (tmp_path / "x.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        RP.load_report(tmp_path / "x.json")


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [(None, "", "-"), (True, "", "yes"), (12.5, "%", "12.5%"), (3.0, "", "3"), (0.01234, "USD", "$0.0123"),
     (7, "%", "7%"), ("x", "", "x")],
)
def test_fmt_value(value, unit, text):
    assert RP.fmt_value(value, unit) == text


def test_md_cell_escapes():
    assert RP.md_cell("a|b\nc\\d") == "a\\|b c\\\\d"
    assert RP.md_cell(None) == ""


def test_git_commit_is_string_or_none(tmp_path):
    commit = RP.git_commit()
    assert commit is None or (isinstance(commit, str) and commit)
    assert RP.git_commit(tmp_path) is None  # not a git checkout


def test_describe_settings_has_no_secrets(app_env):
    info = RP.describe_settings(app_env)
    text = json.dumps(info)
    for secret in app_env.secret_values():
        assert secret not in text
    assert info["llm_provider"] == "fake"


def test_describe_settings_follows_the_run_engine(app_env):
    from aadhi.providers.factory import llm_models

    settings = app_env.model_copy(update={"llm_provider": "gemini", "anthropic_model_plan": "claude-plan-test"})
    info = RP.describe_settings(settings, engine="anthropic")  # --options '{"llm_provider": "anthropic"}'
    assert (info["llm_provider"], info["default_llm_provider"]) == ("anthropic", "gemini")
    assert info["llm_models"] == llm_models(settings, "anthropic")
    assert info["llm_models"]["plan"] == "claude-plan-test"
    for engine in (None, "", "klingon", 42):  # no (valid) run-wide engine: the server default
        fallback = RP.describe_settings(settings, engine=engine)  # type: ignore[arg-type]
        assert fallback["llm_provider"] == "gemini" and fallback["llm_models"]["plan"] == "gemini-2.5-pro"


def test_markdown_lists_fixture_engines_that_differ_from_the_run(tmp_path):
    claude, default = _result("claude"), _result("default")
    claude.llm_engine, default.llm_engine = "anthropic", "fake"
    md = RP.render_markdown(_report(tmp_path, [claude, default]))
    assert "- LLM engines per fixture: `anthropic` 1, `fake` 1" in md
    same = _result("same")
    same.llm_engine = "fake"
    assert "LLM engines per fixture" not in RP.render_markdown(_report(tmp_path, [same]))


# --- compare -------------------------------------------------------------------------------------


def test_diff_directions_and_ordering():
    a = {"lint.errors": 2.0, "objectives.taught_pct": 50.0, "structure.scenes": 8.0, "quiz.bloom.apply": 1.0,
         "only.in_a": 1.0, "pacing.words_total": 100.0}
    b = {"lint.errors": 1.0, "objectives.taught_pct": 40.0, "structure.scenes": 9.0, "quiz.bloom.apply": 1.0,
         "only.in_b": 2.0, "pacing.words_total": 120.0}
    diffs = {d.key: d for d in C.diff_metrics(a, b)}
    assert diffs["lint.errors"].verdict == "better" and diffs["lint.errors"].delta == -1.0
    assert diffs["objectives.taught_pct"].verdict == "worse"
    assert diffs["structure.scenes"].verdict == "changed"
    assert diffs["only.in_a"].verdict == "removed" and diffs["only.in_b"].verdict == "added"
    assert "quiz.bloom.apply" not in diffs  # unchanged non-headline metric dropped
    assert "quiz.bloom.apply" in {d.key for d in C.diff_metrics(a, b, include_all=True)}
    ordered = [d.key for d in C.diff_metrics(a, b)]
    assert ordered.index("structure.scenes") < ordered.index("lint.errors") < ordered.index("only.in_a")
    worse = C.regressions(C.diff_metrics(a, b))
    assert [d.key for d in worse] == ["objectives.taught_pct"]


def test_compare_reports_aggregate_and_fixture(tmp_path):
    a = _report(tmp_path, [_result("x", **{"lint__errors": 3})], name="base")
    b = _report(tmp_path, [_result("x", **{"lint__errors": 1})], name="cand")
    diffs = {d.key: d for d in C.compare_reports(a, b)}
    assert diffs["lint.errors"].verdict == "better"
    per_fixture = {d.key: d for d in C.compare_reports(a, b, fixture="x")}
    assert per_fixture["lint.errors"].delta == -2.0
    assert C.compare_reports(a, b, fixture="missing") == []
    same = C.compare_reports(a, a)
    assert all(d.verdict == "same" for d in same)


def test_format_diff_text_md_json():
    diffs = C.diff_metrics({"lint.errors": 2.0, "x.y": 1.0}, {"lint.errors": 1.5, "x.y": 3.0})
    text = C.format_diff(diffs, a_name="base", b_name="cand")
    lines = text.splitlines()
    assert lines[0].split() == ["metric", "base", "cand", "delta", "verdict"]
    assert any("Lint errors" in line and "-0.5" in line and "better" in line for line in lines)
    assert any(line.startswith("x.y") and "+2" in line and "changed" in line for line in lines)
    md = C.format_diff(diffs, fmt="md")
    assert md.splitlines()[1] == "|---|---:|---:|---:|---|"
    data = json.loads(C.format_diff(diffs, fmt="json"))
    assert {d["key"] for d in data} == {"lint.errors", "x.y"}
    assert C._num(None) == "-" and C._num(-0.0) == "0" and C._delta(None) == ""


def _entry(name: str, status: str, **kw) -> dict:
    return {"name": name, "status": status, "metrics": {}, **kw}


def test_compare_runs_means_only_over_fixtures_ok_in_both(tmp_path):
    a = _report(tmp_path, [_result("easy", **{"lint__errors": 0}), _result("hard", **{"lint__errors": 6})], "a")
    b = _report(tmp_path, [_result("easy", **{"lint__errors": 0}), _result("hard", status="failed")], "b")
    assert a["aggregate"]["lint.errors"] == 3.0 and b["aggregate"]["lint.errors"] == 0.0  # stored means differ
    cmp = C.compare_runs(a, b)
    assert cmp.common == ["easy"]
    lint = next(d for d in cmp.diffs if d.key == "lint.errors")
    assert (lint.a, lint.b, lint.verdict) == (0.0, 0.0, "same")  # no fake "better"
    assert [(c.name, c.a, c.b, c.verdict, c.b_failed_stage) for c in cmp.changes] == [
        ("hard", "ok", "failed", "regressed", "plan")]
    assert cmp.failed_increase == 1 and cmp.regressed()
    reasons = C.regression_summary(cmp)
    assert any("1 fixture(s) regressed" in r for r in reasons) and any("increased by 1" in r for r in reasons)
    assert C.compare_reports(a, b) == cmp.diffs
    assert not C.compare_runs(a, a).regressed()


def test_fixture_changes_verdicts():
    a = {"fixtures": [_entry("gone_ok", "ok"), _entry("gone_failed", "failed"), _entry("fixed", "failed"),
                      _entry("to_skip", "ok"), _entry("skip_fail", "skipped"), _entry("fail_skip", "failed"),
                      _entry("same", "ok")]}
    b = {"fixtures": [_entry("fixed", "ok"), _entry("to_skip", "skipped"), _entry("skip_fail", "failed"),
                      _entry("fail_skip", "skipped"), _entry("same", "ok"), _entry("new", "failed")]}
    verdicts = {c.name: c.verdict for c in C.fixture_changes(a, b)}
    assert verdicts == {"gone_ok": "regressed", "gone_failed": "removed", "fixed": "fixed", "to_skip": "regressed",
                        "skip_fail": "regressed", "fail_skip": "changed", "new": "added"}
    assert C.fixture_status(b, "gone_ok") == C.MISSING and C.fixture_status(b, "new") == "failed"
    cmp = C.compare_runs(a, b)
    assert cmp.summary_a == {"fixtures": 7, "ok": 3, "failed": 3, "skipped": 1}  # derived without a summary
    assert cmp.failed_increase == 0 and cmp.regressed()  # status regressions alone fail the gate


def test_new_failing_fixture_raises_failed_count(tmp_path):
    a = _report(tmp_path, [_result("x")], "a")
    b = _report(tmp_path, [_result("x"), _result("y", status="failed")], "b")
    cmp = C.compare_runs(a, b)
    assert [c.verdict for c in cmp.changes] == ["added"] and cmp.failed_increase == 1 and cmp.regressed()


def test_fixture_metrics_ignores_non_ok_fixtures(tmp_path):
    a = _report(tmp_path, [_result("x", **{"lint__errors": 2})], "a")
    failed_with_metrics = _result("x", **{"lint__errors": 1})
    failed_with_metrics.status, failed_with_metrics.failed_stage = "failed", "critic"
    b = _report(tmp_path, [failed_with_metrics], "b")
    assert b["fixtures"][0]["metrics"]  # a failed fixture can still carry screenplay metrics
    assert C.fixture_metrics(b, "x") == {}
    cmp = C.compare_runs(a, b, fixture="x")
    assert cmp.common == [] and {d.verdict for d in cmp.diffs} == {"removed"}
    assert [c.verdict for c in cmp.changes] == ["regressed"] and cmp.failed_increase == 0 and cmp.regressed()
    text = C.format_comparison(cmp, a_name="base", b_name="cand")
    assert "fixture x (not ok in both runs" in text.splitlines()[0]
    assert "x: ok -> failed at critic (regressed)" in text


def test_format_comparison_formats(tmp_path):
    a = _report(tmp_path, [_result("x", **{"lint__errors": 2})], "a")
    b = _report(tmp_path, [_result("x", **{"lint__errors": 1})], "b")
    cmp = C.compare_runs(a, b)
    text = C.format_comparison(cmp, a_name="base", b_name="cand")
    first, blank, header = text.splitlines()[:3]
    assert first == "base: 1 ok / 0 failed / 0 skipped; cand: 1 ok / 0 failed / 0 skipped; means over 1 fixture(s) ok in both runs"
    assert blank == "" and header.split()[:3] == ["metric", "base", "cand"]
    md = C.format_comparison(cmp, fmt="md")
    assert md.startswith("- A: 1 ok") and "|---|---:|---:|---:|---|" in md
    data = json.loads(C.format_comparison(cmp, fmt="json"))
    assert data["regressed"] is False and data["fixture_changes"] == [] and data["common_ok_fixtures"] == ["x"]
    assert {m["key"] for m in data["metrics"]} >= {"lint.errors"}


def test_report_total_cost_includes_failed_fixtures(tmp_path):
    ok = _result("ok")
    ok.cost_usd = 0.25
    failed = _result("broken", status="failed")
    failed.cost_usd = 0.75  # spent on planning before the fixture failed (no metrics)
    report = _report(tmp_path, [ok, failed])
    assert report["summary"]["total_cost_usd"] == 1.0
    assert report["fixtures"][1]["cost_usd"] == 0.75
    md = RP.render_markdown(report)
    assert "$0.7500" in md and "total cost $1.0000" in md
    legacy = {"metrics": {"cost": {"total_usd": 0.5}}}  # report written before cost_usd existed
    assert RP._fixture_cost(legacy) == 0.5
