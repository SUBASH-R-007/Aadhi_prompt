"""LLM judge with a stub provider (never a real API)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aadhi.evals import judge as J
from aadhi.jobs.base import BudgetExceeded
from aadhi.providers.base import Usage
from tests.evals.factories import lecture


def _verdict(score: int = 4) -> J.JudgeVerdict:
    crit = {c: {"score": score, "justification": f"{c} ok"} for c in J.CRITERIA}
    return J.JudgeVerdict.model_validate({**crit, "strengths": ["clear"], "weaknesses": ["short"],
                                          "top_fixes": ["add a quiz"]})


class StubLLM:
    name = "stub"

    def __init__(self, *, fail_on: set[str] | None = None, cost_tokens: int = 1000, error: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on or set()
        self.cost_tokens = cost_tokens
        self.error = error

    async def generate_json(self, *, model, system, prompt, schema, temperature=0.4, on_usage=None, **kw):
        self.calls.append({"model": model, "system": system, "prompt": prompt, "schema": schema,
                           "temperature": temperature})
        if on_usage is not None:
            on_usage(Usage(provider="stub", model=model, operation="llm", input_tokens=self.cost_tokens))
        if self.error is not None and any(f in prompt for f in self.fail_on):
            raise self.error
        return _verdict(3 if "Ohm" in prompt else 5)

    async def generate_text(self, **kw):  # pragma: no cover - protocol completeness
        return ""


def _make_run(tmp_path) -> Any:
    run = tmp_path / "run"
    (run / "a").mkdir(parents=True)
    (run / "b").mkdir()
    report = {"name": "run", "fixtures": [
        {"name": "a", "status": "ok", "options": {"target_minutes": 10},
         "artifacts": ["ingest.json", "screenplay.json", "metrics.json"]},
        {"name": "b", "status": "failed", "failed_stage": "plan", "artifacts": ["ingest.json"]},
    ]}
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run / "a" / "screenplay.json").write_text(json.dumps(lecture().model_dump(mode="json")), encoding="utf-8")
    (run / "a" / "ingest.json").write_text(json.dumps({"markdown": "SOURCE: Ohm's law V = IR " + "z" * 50}),
                                          encoding="utf-8")
    return run


def test_judge_run_writes_scores_and_skips_missing(app_env, tmp_path):
    run = _make_run(tmp_path)
    llm = StubLLM()
    result = asyncio.run(J.judge_run(run, llm, app_env, model="judge-model", max_source_chars=30))
    assert [r["status"] for r in result["fixtures"]] == ["ok", "skipped"]
    ok = result["fixtures"][0]
    assert ok["overall"] == 3.0 and result["overall_mean"] == 3.0
    assert result["criteria_means"]["pacing"] == 3.0
    call = llm.calls[0]
    assert call["model"] == "judge-model" and call["temperature"] == 0.0 and call["schema"] is J.JudgeVerdict
    assert "Target duration: 10 minutes." in call["prompt"]
    assert "SOURCE: Ohm's law V = IR zzzzz" in call["prompt"] and "z" * 6 not in call["prompt"]  # excerpt cut
    assert (run / "judge.json").is_file()
    md = (run / "judge.md").read_text(encoding="utf-8")
    assert md.startswith("# Judge report: run") and "| a | 3.0 |" in md and "| b | skipped" in md
    assert json.loads((run / "judge.json").read_text(encoding="utf-8"))["provider"] == "stub"


def test_judge_only_scores_lectures_this_run_finished(app_env, tmp_path):
    run = _make_run(tmp_path)
    stale = json.dumps(lecture().model_dump(mode="json"))
    (run / "b" / "screenplay.json").write_text(stale, encoding="utf-8")  # left by an earlier run
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    report["fixtures"] += [
        {"name": "c", "status": "ok", "artifacts": ["metrics.json"]},  # file on disk, not written by this run
        {"name": "d", "status": "skipped", "failed_stage": "convert"},
        {"name": "e", "status": "ok", "artifacts": ["screenplay.json"]},  # listed but missing on disk
    ]
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (run / "c").mkdir()
    (run / "c" / "screenplay.json").write_text(stale, encoding="utf-8")
    llm = StubLLM()
    result = asyncio.run(J.judge_run(run, llm, app_env))
    by_name = {r["name"]: r for r in result["fixtures"]}
    assert by_name["a"]["status"] == "ok" and len(llm.calls) == 1
    assert by_name["b"] == {"name": "b", "status": "skipped", "error": "fixture failed at plan"}
    assert by_name["c"]["error"] == "this run wrote no screenplay.json"
    assert by_name["d"]["error"] == "fixture skipped at convert"
    assert by_name["e"]["error"] == "no screenplay.json"
    assert J._not_judgeable({"status": "ok", "artifacts": ["screenplay.json"]}) is None


def test_judge_only_filter_and_default_model(app_env, tmp_path):
    run = _make_run(tmp_path)
    llm = StubLLM()
    result = asyncio.run(J.judge_run(run, llm, app_env, only=["b"]))
    assert [r["name"] for r in result["fixtures"]] == ["b"]
    assert result["model"] == app_env.llm_model_critic and result["overall_mean"] is None
    assert llm.calls == []


def test_judge_failures_are_redacted_and_budget_stops(app_env, tmp_path):
    run = _make_run(tmp_path)
    secret = app_env.jwt_secret.get_secret_value()
    llm = StubLLM(fail_on={"SOURCE"}, error=RuntimeError(f"upstream echoed {secret}"))
    result = asyncio.run(J.judge_run(run, llm, app_env))
    failed = result["fixtures"][0]
    assert failed["status"] == "failed" and secret not in failed["error"] and "[REDACTED]" in failed["error"]

    budget_llm = StubLLM(fail_on={"SOURCE"}, error=BudgetExceeded("over"))
    result = asyncio.run(J.judge_run(run, budget_llm, app_env, budget_usd=0.01))
    assert result["fixtures"][0]["status"] == "failed" and "over" in result["fixtures"][0]["error"]
    assert len(result["fixtures"]) == 1  # judging stops after a budget failure


def test_render_outline_contents_and_truncation():
    outline = J.render_outline(lecture())
    assert outline.startswith("LECTURE: Basic Electrical Engineering")
    assert "[obj-ohm] (apply) Apply V = IR" in outline
    assert "formula: V = I R" in outline
    assert "B) 4 ohm [correct]" in outline
    assert "example_step [blank, filled later]" in outline
    assert "Animation: equation_steps" in outline and "Animation: free-form" not in outline
    assert "Panel: chart" in outline and "(chapter card: Part 2)" in outline
    assert "[reveals h1]" in outline
    short = J.render_outline(lecture(), max_chars=200)
    assert short.endswith("[... outline truncated ...]") and len(short) < 260


def test_overall_score_and_schema_is_llm_friendly():
    assert J.overall_score(_verdict(4)) == 4.0
    schema = json.dumps(J.JudgeVerdict.model_json_schema())
    for banned in ('"oneOf"', '"discriminator"', '"prefixItems"', '"additionalProperties": true'):
        assert banned not in schema
    with pytest.raises(ValueError):
        J.CriterionScore(score=6, justification="x")


def test_judge_schema_passes_provider_compat_check_when_available():
    llm_schema = pytest.importorskip("aadhi.providers.llm.schema")
    llm_schema.assert_llm_compatible(J.JudgeVerdict)


def test_criteria_means_empty():
    assert J.criteria_means([{"status": "failed"}]) == {}


def test_judge_default_model_is_the_engines_critic_model(app_env, tmp_path):
    run = _make_run(tmp_path)
    settings = app_env.model_copy(update={"anthropic_model_critic": "claude-critic-test"})
    llm = StubLLM()
    result = asyncio.run(J.judge_run(run, llm, settings, engine="anthropic"))
    assert result["model"] == "claude-critic-test" and llm.calls[0]["model"] == "claude-critic-test"
    assert result["provider"] == "stub"  # the provider's own name
    explicit = asyncio.run(J.judge_run(run, StubLLM(), settings, model="judge-model", engine="anthropic"))
    assert explicit["model"] == "judge-model"
