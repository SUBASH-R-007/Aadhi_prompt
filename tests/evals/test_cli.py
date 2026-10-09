"""``python -m aadhi.evals`` command line."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aadhi.config import ROOT_DIR
from aadhi.evals import cli
from aadhi.evals import runner as R
from tests.evals.factories import lecture
from tests.evals.test_runner import make_stages


@pytest.fixture()
def fixtures(tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    (d / "lesson.txt").write_text("Ohm's law", encoding="utf-8")
    (d / "saved.json").write_text(json.dumps(lecture().model_dump(mode="json")), encoding="utf-8")
    return d


def _run(argv, monkeypatch, stages=None) -> int:
    monkeypatch.setattr(R, "load_stages", lambda: stages or make_stages())
    return cli.main(argv)


def test_list_committed_fixtures(capsys):
    assert cli.main(["list", "--fixtures", str(ROOT_DIR / "evals" / "fixtures")]) == 0
    out = capsys.readouterr().out
    for name in ("ohms_law", "logic_gates", "stress_strain", "sample_template", "legacy_ohms_law"):
        assert name in out
    assert '"target_minutes": 10' in out


def test_list_missing_dir_returns_2(tmp_path, capsys):
    assert cli.main(["list", "--fixtures", str(tmp_path / "nope")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_run_success_progress_and_report(app_env, fixtures, tmp_path, monkeypatch, capsys):
    out = tmp_path / "results" / "r1"
    code = _run(["run", "--fixtures", str(fixtures), "--out", str(out), "--options", '{"target_minutes": 6}'],
                monkeypatch)
    assert code == 0
    printed = capsys.readouterr().out
    assert "[lesson] starting (source: lesson.txt)" in printed
    assert "[lesson] ingest" in printed  # progress events are echoed
    assert "2 ok, 0 failed, 0 skipped" in printed and "report: " in printed
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["config"]["option_overrides"] == {"target_minutes": 6}
    assert report["fixtures"][0]["metrics"]["pacing"]["target_minutes"] == 6.0


def test_run_quiet_failure_and_skips(app_env, fixtures, tmp_path, monkeypatch, capsys):
    async def broken(ctx, plan, ingest, options):
        raise ValueError("bad output")

    code = _run(["run", "--fixtures", str(fixtures), "--out", str(tmp_path / "f"), "-q"], monkeypatch,
                make_stages(script=broken))
    assert code == 1
    out = capsys.readouterr().out
    assert "[lesson]" not in out and "lesson: failed at script: ValueError: bad output" in out

    code = _run(["run", "--fixtures", str(fixtures), "--out", str(tmp_path / "s"), "--only", "lesson", "-q"],
                monkeypatch, make_stages(ingest=None))
    assert code == 3
    captured = capsys.readouterr()
    assert "stage ingest unavailable" in captured.out and "nothing was evaluated" in captured.err


def test_run_unknown_fixture_and_bad_options(app_env, fixtures, tmp_path, monkeypatch, capsys):
    assert _run(["run", "--fixtures", str(fixtures), "--out", str(tmp_path / "x"), "--only", "nope"], monkeypatch) == 2
    assert "unknown fixtures" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--options", "{not json"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["run", "--options", "[1]"])
    with pytest.raises(SystemExit):
        cli.main(["run", "--budget-usd", "-1"])


def test_run_options_from_file(app_env, fixtures, tmp_path, monkeypatch):
    opts = tmp_path / "opts.json"
    opts.write_text(json.dumps({"target_minutes": 7}), encoding="utf-8")
    out = tmp_path / "o"
    assert _run(["run", "--fixtures", str(fixtures), "--out", str(out), "--options", f"@{opts}", "-q"],
                monkeypatch) == 0
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["config"]["option_overrides"] == {
        "target_minutes": 7}


def test_compare_and_regression_exit(app_env, fixtures, tmp_path, monkeypatch, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    assert _run(["run", "--fixtures", str(fixtures), "--out", str(a), "-q"], monkeypatch) == 0

    def worse_lint(screenplay, options=None, **kw):
        from aadhi.pipeline.base import Issue

        return [Issue(code="objective.untaught", severity="error", message="x")] * 3

    assert _run(["run", "--fixtures", str(fixtures), "--out", str(b), "-q"], monkeypatch,
                make_stages(lint=worse_lint)) == 0
    capsys.readouterr()
    assert cli.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "Lint errors" in out and "worse" in out and "headline metric(s) regressed" in out
    assert cli.main(["compare", str(a), str(b / "report.json"), "--fail-on-regression"]) == 1
    capsys.readouterr()
    assert cli.main(["compare", str(a), str(b), "--format", "md", "--fixture", "lesson"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("- a: 2 ok / 0 failed / 0 skipped; b: 2 ok / 0 failed / 0 skipped; fixture lesson")
    assert "| metric | a | b | delta | verdict |" in captured.out
    assert "regressed" not in captured.out and "headline metric(s) regressed" in captured.err  # md stays clean
    assert cli.main(["compare", str(a), str(b), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["regressed"] is True and data["common_ok_fixtures"] == ["lesson", "saved"]
    assert cli.main(["compare", str(a), str(tmp_path / "missing")]) == 2


def test_compare_gate_fails_when_a_fixture_newly_fails(app_env, tmp_path, monkeypatch, capsys):
    """Reviewer scenario: B drops the hard fixture (it fails), which must not look like an improvement."""
    from aadhi.pipeline.base import Issue

    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "easy.txt").write_text("easy", encoding="utf-8")
    (fx / "hard.txt").write_text("hard", encoding="utf-8")
    hard_issues = [Issue(code="objective.untaught", severity="error", message="x")] * 6

    def lint(screenplay, options=None, **kw):
        return hard_issues if screenplay.session_title == "hard" else []

    async def plan_a(ctx, ingest_result, options):
        from aadhi.pipeline.base import LecturePlan

        return LecturePlan(session_title=ingest_result.markdown)

    async def ingest(ctx, source_doc):
        from aadhi.pipeline.base import IngestResult

        return IngestResult(markdown=source_doc.filename.removesuffix(".txt"))

    async def script(ctx, plan_result, ingest_result, options):
        return lecture().model_copy(update={"session_title": plan_result.session_title})

    async def plan_b(ctx, ingest_result, options):
        if ingest_result.markdown == "hard":
            raise RuntimeError("planner broke")
        return await plan_a(ctx, ingest_result, options)

    a, b = tmp_path / "a", tmp_path / "b"
    common = {"ingest": ingest, "script": script, "lint": lint}
    assert _run(["run", "--fixtures", str(fx), "--out", str(a), "-q"], monkeypatch,
                make_stages(plan=plan_a, **common)) == 0
    assert _run(["run", "--fixtures", str(fx), "--out", str(b), "-q"], monkeypatch,
                make_stages(plan=plan_b, **common)) == 1
    capsys.readouterr()
    assert cli.main(["compare", str(a), str(b), "--fail-on-regression"]) == 1
    out = capsys.readouterr().out
    assert "means over 1 fixture(s) ok in both runs" in out
    assert "hard: ok -> failed at plan (regressed)" in out
    lint_row = next(line for line in out.splitlines() if line.startswith("Lint errors"))
    assert lint_row.split()[-1] == "same"  # easy vs easy, not 3 -> 0 "better"
    assert "1 fixture(s) regressed" in out and "failed fixtures increased by 1" in out
    assert cli.main(["compare", str(a), str(b), "--fixture", "hard", "--fail-on-regression"]) == 1
    assert "not ok in both runs" in capsys.readouterr().out
    assert cli.main(["compare", str(a), str(a), "--fail-on-regression"]) == 0


def test_judge_refuses_fake_provider_and_missing_report(app_env, tmp_path, capsys):
    assert cli.main(["judge", str(tmp_path)]) == 2
    assert "real LLM provider" in capsys.readouterr().err
    code = cli.main(["judge", str(tmp_path), "--allow-fake"])
    assert code == 2  # providers missing, or no report.json in an empty directory


def test_module_entry_point_help():
    proc = subprocess.run([sys.executable, "-m", "aadhi.evals", "--help"], capture_output=True, text=True,
                          cwd=ROOT_DIR, timeout=120, check=False)
    assert proc.returncode == 0
    assert "run" in proc.stdout and "compare" in proc.stdout and "judge" in proc.stdout


def test_judge_engine_flag(app_env, tmp_path, monkeypatch, capsys):
    from pydantic import SecretStr

    from aadhi.providers import factory
    from tests.evals.test_judge import StubLLM, _make_run

    run = _make_run(tmp_path)
    assert cli.main(["judge", str(run), "--engine", "anthropic"]) == 2  # no key in tests
    assert "anthropic engine is not configured" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["judge", str(run), "--engine", "fake"])
    app_env.anthropic_api_key = SecretStr("sk-ant-test-123456789")
    app_env.anthropic_model_critic = "claude-critic-test"
    asked: list[str | None] = []

    def get_llm(settings=None, engine=None):
        asked.append(engine)
        return StubLLM()

    monkeypatch.setattr(factory, "get_llm", get_llm)
    assert cli.main(["judge", str(run), "--engine", "anthropic"]) == 1  # fixture "b" failed in the run: skipped
    assert asked == ["anthropic"]
    assert json.loads((run / "judge.json").read_text(encoding="utf-8"))["model"] == "claude-critic-test"
