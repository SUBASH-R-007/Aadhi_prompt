"""The eval runner with injected stage functions, plus an end-to-end run of the real pipeline
(fake providers) that is skipped until the pipeline modules are importable."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from aadhi.config import ROOT_DIR
from aadhi.evals import runner as R
from aadhi.evals.runner import PipelineStages, RunConfig, import_attr, load_stages, run_eval
from aadhi.pipeline.base import BriefConcept, ConceptBrief, IngestResult, Issue, LecturePlan, SkippedChunk, SourceChunk
from aadhi.providers.base import Usage
from aadhi.schemas.manifest import AssetManifest, BeatAudio, SceneAudio
from aadhi.schemas.screenplay import CompanionSheet, PracticeProblem, Screenplay
from tests.evals.factories import lecture, lecture_dict

FIXTURES_DIR = ROOT_DIR / "evals" / "fixtures"


class Calls(list):
    def names(self) -> list[str]:
        return [c[0] for c in self]


def make_stages(calls: Calls | None = None, **overrides: Any) -> PipelineStages:
    """Fake stage functions mirroring the contract signatures in aadhi/pipeline/base.py."""
    calls = calls if calls is not None else Calls()

    async def ingest(ctx, source_doc):
        calls.append(("ingest", source_doc.filename, source_doc.mime))
        assert ctx.storage.exists(source_doc.storage_key)
        ctx.progress("ingest", 0.05, "reading source")
        ctx.record_usage(Usage("fake", "fake-llm", "llm", input_tokens=100))
        chunks = [SourceChunk(id=f"c000{i}", text=f"chunk {i}") for i in (1, 2, 3)]
        return IngestResult(markdown="<!-- page 1 -->\n# Ohm", chunks=chunks, pages=1, source_mime=source_doc.mime)

    async def brief(ctx, ingest_result, options):
        calls.append(("brief", len(ingest_result.chunks)))
        return ConceptBrief(topic="Ohm's law", teaching_order=["ohm"],
                            concepts=[BriefConcept(key="ohm", name="Ohm's law", source_refs=["c0001", "c0002"])],
                            skipped_chunks=[SkippedChunk(chunk_id="c0003", reason="administrative")])

    async def plan(ctx, ingest_result, options, *, brief=None):
        calls.append(("plan", options.target_minutes))
        calls.append(("plan_brief", [c.key for c in brief.concepts] if brief is not None else None))
        return LecturePlan(session_title=options.session_title or "Ohm")

    async def script(ctx, plan_result, ingest_result, options, *, brief=None):
        calls.append(("script",))
        calls.append(("script_brief", sorted(brief.skipped_chunk_ids()) if brief is not None else None))
        return lecture()

    def lint(screenplay, options):
        calls.append(("lint", len(screenplay.scenes)))
        return [Issue(code="board.item_too_long", message="long", scene_id="s-ohm")]

    def skipped(brief):
        return sorted(brief.skipped_chunk_ids()) if brief is not None else None

    async def critic(ctx, screenplay, ingest_result, options, *, brief=None):
        calls.append(("critic",))
        calls.append(("critic_brief", skipped(brief)))
        return [Issue(code="grounding.unsupported_claim", severity="error", message="?", source="critic",
                      scene_id="s-ohm")]

    async def repair(ctx, screenplay, issues, ingest_result, options, *, brief=None):
        calls.append(("repair", len(issues)))
        calls.append(("repair_brief", skipped(brief)))
        return screenplay.model_copy(update={"session_title": "Repaired"})

    async def companion(ctx, screenplay, ingest_result, options, *, brief=None):
        calls.append(("companion",))
        calls.append(("companion_brief", skipped(brief)))
        return CompanionSheet(practice_problems=[
            PracticeProblem(id="pp1", question="Find I", final_answer="3 A", objective_ids=["obj-ohm"])
        ])

    async def assets(ctx, screenplay, options):
        calls.append(("assets",))
        audio = {
            s.id: SceneAudio(scene_id=s.id, asset_key=f"scene_audio-{s.id}", duration=10.0,
                             beats=[BeatAudio(beat_id=b.id, offset=0.0, speech_duration=2.0) for b in s.all_beats()])
            for s in screenplay.scenes if s.all_beats()
        }
        return AssetManifest(audio=audio)

    functions = {"ingest": ingest, "brief": brief, "plan": plan, "script": script, "lint": lint, "critic": critic,
                 "repair": repair, "companion": companion, "assets": assets}
    functions.update({k: v for k, v in overrides.items() if v is not None})
    for k in [k for k, v in overrides.items() if v is None]:
        functions.pop(k, None)
    missing = {k: f"{k} not importable (test)" for k in R.STAGE_FUNCTIONS if k not in functions}
    helpers = {"render_markdown": lambda sp: f"# Companion for {sp.session_title}\n"}
    return PipelineStages(functions=functions, helpers=helpers, missing=missing)


@pytest.fixture()
def fixtures(tmp_path) -> Path:
    d = tmp_path / "fixtures"
    d.mkdir()
    (d / "lesson.txt").write_text("Ohm's law: V = IR.", encoding="utf-8")
    (d / "lesson.options.json").write_text(json.dumps({"target_minutes": 5}), encoding="utf-8")
    (d / "saved.json").write_text(json.dumps(lecture().model_dump(mode="json")), encoding="utf-8")
    return d


def _cfg(fixtures: Path, tmp_path: Path, **kw: Any) -> RunConfig:
    return RunConfig(fixtures_dir=fixtures, out_dir=tmp_path / "out" / "run-a", **kw)


def _fx(report: dict, name: str) -> dict:
    return next(f for f in report["fixtures"] if f["name"] == name)


def _stage_names(fx: dict) -> list[str]:
    return [s["name"] for s in fx["stages"]]


def test_run_eval_end_to_end_with_injected_stages(app_env, fixtures, tmp_path):
    calls = Calls()
    cfg = _cfg(fixtures, tmp_path)
    report = run_eval(cfg, stages=make_stages(calls))
    assert report["summary"] == {"fixtures": 2, "ok": 2, "failed": 0, "skipped": 0,
                                 "total_cost_usd": report["summary"]["total_cost_usd"]}
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok" and lesson["error"] is None
    assert _stage_names(lesson) == ["ingest", "brief", "plan", "script", "lint", "critic", "companion"]
    assert all(s["status"] == "ok" for s in lesson["stages"])
    assert lesson["options"] == {"target_minutes": 5}
    assert calls[0] == ("ingest", "lesson.txt", "text/plain")
    assert ("plan", 5) in calls
    # the brief goes to planning and scene writing, as in the orchestrator
    assert ("brief", 3) in calls and ("plan_brief", ["ohm"]) in calls and ("script_brief", ["c0003"]) in calls
    # ...and to the critic and the companion sheet, so their prompts never hold a set-aside chunk either
    assert ("critic_brief", ["c0003"]) in calls and ("companion_brief", ["c0003"]) in calls
    assert lesson["metrics"]["brief"] == {
        "available": True, "concepts": 1, "source_questions": 0, "chunks": 3, "cited_chunks": 2, "skipped_chunks": 1,
        "skipped_by_reason": {"administrative": 1}, "unaccounted_chunks": 0, "coverage": 0.6667,
    }
    assert report["aggregate"]["brief.coverage"] == 0.6667
    m = lesson["metrics"]
    assert m["lint"]["total"] == 2 and m["lint"]["by_source"] == {"critic": 1, "lint": 1}
    assert m["grounding"]["chunks_total"] == 3
    assert m["pacing"]["target_minutes"] == 5.0
    assert m["objectives"]["practiced"] == 1  # companion sheet merged into the screenplay
    assert m["source"]["chunks"] == 3 and m["cost"]["calls"] == 1
    assert m["cost"]["by_stage"].keys() == {"ingest"}
    out = cfg.out_dir / "lesson"
    for name in ("ingest.json", "brief.json", "plan.json", "screenplay.json", "issues.json", "metrics.json",
                 "events.json", "usage.json", "companion.md"):
        assert (out / name).is_file(), name
    saved_sp = Screenplay.model_validate_json((out / "screenplay.json").read_text(encoding="utf-8"))
    assert saved_sp.companion_sheet.practice_problems[0].id == "pp1"
    usage = json.loads((out / "usage.json").read_text(encoding="utf-8"))
    assert usage[0]["stage"] == "ingest" and usage[0]["input_tokens"] == 100
    saved = _fx(report, "saved")
    assert _stage_names(saved) == ["convert", "lint", "critic"]
    assert saved["stages"][-1]["status"] == "skipped"
    assert saved["metrics"]["grounding"]["unknown_refs"] is None  # no source chunks for screenplays
    assert report["aggregate"]["structure.scenes"] == 8.0
    assert (cfg.out_dir / "report.json").is_file() and (cfg.out_dir / "report.md").is_file()
    md = (cfg.out_dir / "report.md").read_text(encoding="utf-8")
    assert "- Concept brief: 1 concepts; 2 of 3 chunks cited, 1 set aside (administrative 1), 0 neither" in md
    assert json.loads((cfg.out_dir / "report.json").read_text(encoding="utf-8"))["name"] == "run-a"


def test_failed_stage_is_recorded_redacted_and_others_continue(app_env, fixtures, tmp_path):
    async def script(ctx, plan_result, ingest_result, options):
        raise RuntimeError("provider said key=abcdef123456 nope")

    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages(script=script))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "failed" and lesson["failed_stage"] == "script"
    assert "abcdef123456" not in lesson["error"] and "[REDACTED]" in lesson["error"]
    assert lesson["stages"][-1]["status"] == "failed"
    assert lesson["metrics"] == {}
    assert _fx(report, "saved")["status"] == "ok"
    assert report["summary"]["failed"] == 1
    # aggregates only use ok fixtures
    assert report["aggregate"]["structure.scenes"] == 8.0


def test_missing_required_stage_skips_and_optional_stage_is_reported(app_env, fixtures, tmp_path):
    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages(plan=None))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "skipped" and lesson["failed_stage"] == "plan"
    assert report["stages_missing"]["plan"].startswith("plan not importable")
    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages(critic=None, companion=None))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok"
    statuses = {s["name"]: s["status"] for s in lesson["stages"]}
    assert statuses["critic"] == "unavailable" and statuses["companion"] == "unavailable"
    assert report["stages_available"]["critic"] is False


def test_repair_relints_and_records_before_after(app_env, fixtures, tmp_path):
    calls = Calls()
    lint_results = iter([[Issue(code="board.item_too_long", message="x")], []])

    def lint(screenplay, options):
        calls.append(("lint",))
        return next(lint_results)

    cfg = _cfg(fixtures, tmp_path, repair=True, only=("lesson",))
    report = run_eval(cfg, stages=make_stages(calls, lint=lint))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok"
    assert _stage_names(lesson) == ["ingest", "brief", "plan", "script", "lint", "critic", "repair", "relint",
                                    "companion"]
    assert ("repair", 2) in calls and ("repair_brief", ["c0003"]) in calls  # repair rewrites without set-aside chunks
    assert lesson["metrics"]["repair"] == {"issues_before": 2, "errors_before": 1, "issues_after": 1}
    assert (cfg.out_dir / "lesson" / "screenplay.draft.json").is_file()
    assert (cfg.out_dir / "lesson" / "issues.before_repair.json").is_file()
    final = json.loads((cfg.out_dir / "lesson" / "screenplay.json").read_text(encoding="utf-8"))
    assert final["session_title"] == "Repaired"


def test_lint_receives_chunk_ids_when_it_accepts_them(app_env, fixtures, tmp_path):
    received: list[Any] = []

    def lint(screenplay, options=None, *, chunk_ids=None):
        received.append(chunk_ids)
        return []

    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages(lint=lint))
    assert _fx(report, "lesson")["status"] == "ok"
    assert {"c0001", "c0002", "c0003"} in received  # source fixture: chunk ids of the ingest result
    assert None in received  # screenplay fixture: no source, no chunk ids
    assert R.accepts_kwarg(lint, "chunk_ids") and not R.accepts_kwarg(lambda sp, o: [], "chunk_ids")
    assert R.accepts_kwarg(lambda *a, **kw: [], "chunk_ids") and not R.accepts_kwarg(None, "chunk_ids")


def test_assets_stage_adds_audio_metrics(app_env, fixtures, tmp_path):
    cfg = _cfg(fixtures, tmp_path, assets=True)
    report = run_eval(cfg, stages=make_stages())
    for name in ("lesson", "saved"):
        fx = _fx(report, name)
        assert fx["status"] == "ok"
        assert "assets" in _stage_names(fx)
        assert fx["metrics"]["audio"]["missing_audio_scenes"] == 0
        assert (cfg.out_dir / name / "manifest.json").is_file()


def test_timeout_cancels_the_running_stage(app_env, fixtures, tmp_path):
    async def hung_ingest(ctx, source_doc):
        await asyncio.sleep(30)  # e.g. a provider call that never answers
        return IngestResult(markdown="x")

    cfg = _cfg(fixtures, tmp_path, timeout_s=0.1, only=("lesson",))
    report = run_eval(cfg, stages=make_stages(ingest=hung_ingest))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "failed" and lesson["failed_stage"] == "ingest"  # blamed on the slow stage
    assert "timed out after 0.1s" in lesson["error"]
    assert lesson["seconds"] < 10 and lesson["stages"][-1]["status"] == "failed"


def test_timeout_also_bounds_sync_stages_in_threads(app_env, fixtures, tmp_path):
    def slow_sync_ingest(ctx, source_doc):
        time.sleep(1.5)  # cannot be interrupted; its result is discarded
        return IngestResult(markdown="x")

    report = run_eval(_cfg(fixtures, tmp_path, timeout_s=0.1, only=("lesson",)),
                      stages=make_stages(ingest=slow_sync_ingest))
    lesson = _fx(report, "lesson")
    assert lesson["failed_stage"] == "ingest" and "timed out" in lesson["error"]
    assert lesson["seconds"] < 1.4


def test_timeout_inside_a_stage_is_not_reported_as_the_deadline(app_env, fixtures, tmp_path):
    async def ingest(ctx, source_doc):
        raise TimeoutError("provider read timeout")

    report = run_eval(_cfg(fixtures, tmp_path, timeout_s=30, only=("lesson",)), stages=make_stages(ingest=ingest))
    lesson = _fx(report, "lesson")
    assert lesson["failed_stage"] == "ingest" and "provider read timeout" in lesson["error"]
    assert "eval fixture timed out" not in lesson["error"]


def test_deadline_passed_between_stages_fails_the_next_stage(app_env, fixtures, tmp_path):
    def ingest(ctx, source_doc):
        ctx.deadline = 0.0  # pretend the wall clock ran out right after this stage
        return IngestResult(markdown="x")

    report = run_eval(_cfg(fixtures, tmp_path, timeout_s=30, only=("lesson",)), stages=make_stages(ingest=ingest))
    lesson = _fx(report, "lesson")
    assert lesson["failed_stage"] == "brief" and "timed out" in lesson["error"]  # a deadline still stops the fixture


def test_a_failed_brief_never_fails_the_fixture(app_env, fixtures, tmp_path):
    calls = Calls()

    async def broken_brief(ctx, ingest_result, options):
        raise RuntimeError("brief model overloaded")

    report = run_eval(_cfg(fixtures, tmp_path, only=("lesson",)), stages=make_stages(calls, brief=broken_brief))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok"
    statuses = {s["name"]: s["status"] for s in lesson["stages"]}
    assert statuses["brief"] == "failed" and statuses["plan"] == "ok"
    assert ("plan_brief", None) in calls and ("script_brief", None) in calls  # planned directly from the source
    assert any("planned directly from the source" in w for w in lesson["warnings"])
    assert lesson["metrics"]["brief"] == {"available": False}


def test_an_unavailable_brief_is_a_skipped_stage(app_env, fixtures, tmp_path):
    from aadhi.pipeline.brief import BriefUnavailable

    async def no_brief(ctx, ingest_result, options):
        raise BriefUnavailable("the source has too little readable text for a concept brief")

    report = run_eval(_cfg(fixtures, tmp_path, only=("lesson",)), stages=make_stages(brief=no_brief))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok"
    assert {s["name"]: s["status"] for s in lesson["stages"]}["brief"] == "skipped"
    md = (tmp_path / "out" / "run-a" / "report.md").read_text(encoding="utf-8")
    assert "- Concept brief: not built (planned directly from the source)" in md
    # without a brief stage at all, nothing is reported for it
    report = run_eval(_cfg(fixtures, tmp_path, only=("lesson",)), stages=make_stages(brief=None))
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok" and "brief" not in lesson["metrics"]
    assert {s["name"]: s["status"] for s in lesson["stages"]}["brief"] == "unavailable"


def test_budget_exceeded_stops_the_fixture(app_env, fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr("aadhi.evals.context.load_pricing", lambda: (lambda usage, settings: 0.75))
    report = run_eval(_cfg(fixtures, tmp_path, budget_usd=0.5, only=("lesson",)), stages=make_stages())
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "failed" and lesson["failed_stage"] == "ingest"
    assert "BudgetExceeded" in lesson["error"]
    assert lesson["metrics"] == {}  # no screenplay, no metrics ...
    assert lesson["cost_usd"] == 0.75  # ... but the money spent is still reported
    assert report["summary"]["total_cost_usd"] == 0.75


def test_rerun_into_same_out_dir_leaves_no_stale_artifacts(app_env, fixtures, tmp_path):
    from aadhi.evals.judge import judge_run

    cfg = _cfg(fixtures, tmp_path, only=("lesson",))
    run_eval(cfg, stages=make_stages())
    out = cfg.out_dir / "lesson"
    assert (out / "screenplay.json").is_file() and (out / "companion.md").is_file()
    (out / "notes.txt").write_text("left over by hand", encoding="utf-8")

    async def broken_plan(ctx, ingest_result, options):
        raise RuntimeError("planner down")

    report = run_eval(cfg, stages=make_stages(plan=broken_plan))
    lesson = _fx(report, "lesson")
    assert lesson["failed_stage"] == "plan"
    assert sorted(p.name for p in out.iterdir()) == sorted(lesson["artifacts"])  # only this run's files
    for stale in ("screenplay.json", "plan.json", "issues.json", "companion.md", "notes.txt"):
        assert not (out / stale).exists(), stale

    class NeverCalled:
        name = "stub"

        async def generate_json(self, **kw):  # pragma: no cover - must not be reached
            raise AssertionError("judge must not score a fixture this run did not produce")

    judged = asyncio.run(judge_run(cfg.out_dir, NeverCalled(), app_env))
    assert [(r["name"], r["status"]) for r in judged["fixtures"]] == [("lesson", "skipped")]
    assert "failed at plan" in judged["fixtures"][0]["error"]


def test_non_json_event_data_does_not_abort_the_run(app_env, fixtures, tmp_path):
    async def ingest(ctx, source_doc):
        ctx.log("parsed", "info", pages={2, 1}, path=Path("x") / "y.pdf", nested={"t": (1, {3})})
        ctx.record_usage(Usage("fake", "fake-llm", "llm", input_tokens=5, meta={"ids": {"b"}}))
        return IngestResult(markdown="x", chunks=[SourceChunk(id="c0001", text="x")])

    cfg = _cfg(fixtures, tmp_path)
    report = run_eval(cfg, stages=make_stages(ingest=ingest))
    assert {f["name"]: f["status"] for f in report["fixtures"]} == {"lesson": "ok", "saved": "ok"}
    events = json.loads((cfg.out_dir / "lesson" / "events.json").read_text(encoding="utf-8"))
    logged = next(e for e in events if e.get("message") == "parsed")
    assert logged["data"]["pages"] == [1, 2] and logged["data"]["nested"] == {"t": [1, [3]]}
    assert logged["data"]["path"] == str(Path("x") / "y.pdf")
    usage = json.loads((cfg.out_dir / "lesson" / "usage.json").read_text(encoding="utf-8"))
    assert usage[0]["meta"]["ids"] == "{'b'}"  # json default=str
    assert (cfg.out_dir / "report.json").is_file()


def test_artifact_write_failure_becomes_a_warning(app_env, fixtures, tmp_path, monkeypatch):
    real = R._write_json

    def flaky(path, data):
        if path.name == "plan.json":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(R, "_write_json", flaky)
    cfg = _cfg(fixtures, tmp_path)
    report = run_eval(cfg, stages=make_stages())
    lesson = _fx(report, "lesson")
    assert lesson["status"] == "ok" and "plan.json" not in lesson["artifacts"]
    assert any(w.startswith("plan.json not written: OSError: disk full") for w in lesson["warnings"])
    assert (cfg.out_dir / "lesson" / "screenplay.json").is_file() and _fx(report, "saved")["status"] == "ok"
    assert (cfg.out_dir / "report.json").is_file()


def test_unexpected_artifact_crash_keeps_other_fixtures(app_env, fixtures, tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("out dir vanished")

    monkeypatch.setattr(R, "_write_artifacts", boom)
    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages())
    assert [f["status"] for f in report["fixtures"]] == ["ok", "ok"]
    assert all(any("artifacts not written" in w for w in f["warnings"]) for f in report["fixtures"])


def test_fake_mode_isolates_settings_and_restores_env(app_env, fixtures, tmp_path, monkeypatch):
    from aadhi.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key-should-not-be-visible")
    get_settings.cache_clear()
    seen: dict[str, Any] = {}

    async def ingest(ctx, source_doc):
        s = get_settings()
        seen.update(provider=s.llm_provider, key=os.environ["OPENAI_API_KEY"], data_dir=s.data_dir,
                    ctx_settings=ctx.settings is s)
        return IngestResult(markdown="x")

    cfg = _cfg(fixtures, tmp_path, only=("lesson",))
    run_eval(cfg, stages=make_stages(ingest=ingest))
    assert seen["provider"] == "fake" and seen["key"] == "" and seen["ctx_settings"]
    assert app_env.data_dir not in seen["data_dir"].parents and seen["data_dir"] != app_env.data_dir
    assert os.environ["OPENAI_API_KEY"] == "sk-real-key-should-not-be-visible"
    assert get_settings().data_dir == app_env.data_dir


def test_legacy_fixture_needs_legacy_module(app_env, tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    legacy = {"session_title": "Old", "scenes": [{"type": "content", "board_html": "<p>x</p>"}]}
    (d / "old.json").write_text(json.dumps(legacy), encoding="utf-8")
    report = run_eval(RunConfig(fixtures_dir=d, out_dir=tmp_path / "o1"), stages=make_stages())
    old = _fx(report, "old")
    assert old["status"] == "skipped" and old["failed_stage"] == "convert"

    def convert(data):
        return lecture(), ["board_html dropped"]

    stages = make_stages()
    stages.helpers.update(is_legacy=lambda data: "schema_version" not in data, convert_legacy=convert)
    report = run_eval(RunConfig(fixtures_dir=d, out_dir=tmp_path / "o2"), stages=stages)
    old = _fx(report, "old")
    assert old["status"] == "ok" and old["warnings"] == ["board_html dropped"]
    assert old["metrics"]["structure"]["scenes"] == 8


def test_invalid_screenplay_fixture_reports_schema_errors(app_env, tmp_path):
    d = tmp_path / "fx"
    d.mkdir()
    bad = lecture_dict()
    bad["schema_version"] = 2
    bad["scenes"][3]["correct_index"] = 7
    (d / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    report = run_eval(RunConfig(fixtures_dir=d, out_dir=tmp_path / "o"), stages=make_stages())
    fx = _fx(report, "bad")
    assert fx["status"] == "failed" and fx["failed_stage"] == "convert"
    assert fx["metrics"]["schema"]["valid"] is False


def test_report_names_the_engine_the_fixtures_used(app_env, fixtures, tmp_path):
    from aadhi.providers.factory import llm_models

    # a fixture sidecar picks Claude; the other fixture keeps the default engine
    (fixtures / "lesson.options.json").write_text(json.dumps({"target_minutes": 5, "llm_provider": "anthropic"}),
                                                  encoding="utf-8")
    cfg = _cfg(fixtures, tmp_path)
    report = run_eval(cfg, stages=make_stages())
    assert (_fx(report, "lesson")["llm_engine"], _fx(report, "saved")["llm_engine"]) == ("anthropic", "fake")
    assert report["settings"]["llm_provider"] == "fake"
    md = (cfg.out_dir / "report.md").read_text(encoding="utf-8")
    assert "- LLM engines per fixture: `anthropic` 1, `fake` 1" in md

    # a run-wide --options engine labels the whole run with that engine and its models
    cfg = RunConfig(fixtures_dir=fixtures, out_dir=tmp_path / "out" / "run-b", option_overrides={"llm_provider": "anthropic"})
    report = run_eval(cfg, stages=make_stages())
    settings = report["settings"]
    assert (settings["llm_provider"], settings["default_llm_provider"]) == ("anthropic", "fake")
    assert settings["llm_models"] == llm_models(app_env, "anthropic")
    assert {f["llm_engine"] for f in report["fixtures"]} == {"anthropic"}
    md = (cfg.out_dir / "report.md").read_text(encoding="utf-8")
    assert f"LLM `anthropic` (plan={settings['llm_models']['plan']}" in md
    assert "LLM engines per fixture" not in md


def test_bad_options_fail_only_that_fixture(app_env, fixtures, tmp_path):
    (fixtures / "lesson.options.json").write_text(json.dumps({"target_minutes": 1}), encoding="utf-8")
    report = run_eval(_cfg(fixtures, tmp_path), stages=make_stages())
    assert _fx(report, "lesson")["status"] == "failed"
    assert "invalid options" in _fx(report, "lesson")["error"]
    assert _fx(report, "saved")["status"] == "ok"


def test_keep_workspace_keeps_database_with_results(app_env, fixtures, tmp_path):
    cfg = _cfg(fixtures, tmp_path, keep_workspace=True, only=("lesson",))
    run_eval(cfg, stages=make_stages())
    db_path = cfg.out_dir / "workspace" / "eval.db"
    assert db_path.is_file()
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        status, screenplay = con.execute("SELECT status, screenplay FROM project_versions").fetchone()
        filename = con.execute("SELECT filename FROM source_documents").fetchone()[0]
    finally:
        con.close()
    assert status == "draft" and json.loads(screenplay)["scenes"]
    assert filename == "lesson.txt"


def test_import_attr_and_load_stages(monkeypatch):
    assert import_attr("aadhi.evals.metrics", "plain_text")[0] is not None
    obj, reason = import_attr("aadhi.does_not_exist_xyz", "x")
    assert obj is None and "not importable" in reason
    obj, reason = import_attr("json", "nope")
    assert obj is None and "not found" in reason
    monkeypatch.setattr(R, "STAGE_FUNCTIONS", {"lint": ("aadhi.evals.metrics", "plain_text"),
                                               "plan": ("aadhi.nope_module", "make_plan")})
    stages = load_stages()
    assert "lint" in stages.functions and "plan" in stages.missing
    assert stages.known_templates() is None or isinstance(stages.known_templates(), list)


# --- real pipeline (fake providers) ------------------------------------------------------------------


def _pipeline_ready() -> bool:
    """Stage modules plus the provider factory they call (fake providers) must be importable."""
    needed = [R.STAGE_FUNCTIONS[s] for s in ("ingest", "plan", "script", "lint")]
    needed.append(("aadhi.providers.factory", "get_llm"))
    return all(import_attr(module, attr)[0] is not None for module, attr in needed)


@pytest.mark.skipif(not _pipeline_ready(), reason="aadhi.pipeline stages / provider factory not importable yet")
def test_real_pipeline_on_fixtures_with_fake_provider(app_env, tmp_path):
    cfg = RunConfig(fixtures_dir=FIXTURES_DIR, out_dir=tmp_path / "real", provider="fake",
                    only=("ohms_law", "logic_gates", "stress_strain"))
    report = run_eval(cfg)
    for fx in report["fixtures"]:
        assert fx["status"] == "ok", f"{fx['name']} {fx['failed_stage']}: {fx['error']}"
        m = fx["metrics"]
        assert m["schema"]["valid"] is True
        assert m["structure"]["scenes"] > 0 and m["pacing"]["words_total"] > 0
        # The fake provider reports real model names, so pricing and stage attribution apply.
        assert m["cost"]["calls"] > 0 and m["cost"]["total_usd"] > 0
        assert set(m["cost"]["by_stage"]) >= {"brief", "plan", "script"}
        # the brief accounts for every chunk of the fixture PDFs; their title-page line became metadata
        brief = m["brief"]
        assert brief["available"] and brief["concepts"] > 0 and brief["unaccounted_chunks"] == 0
        assert brief["coverage"] == 1.0 and brief["skipped_chunks"] == 0
        ingest = json.loads((cfg.out_dir / fx["name"] / "ingest.json").read_text(encoding="utf-8"))
        meta = ingest["document_meta"]
        assert meta["subject_name"] and meta["unit_name"] and meta["session_number"].startswith("Session ")
        assert not any(" - Session " in c["text"] for c in ingest["chunks"])
        assert not any(chr(c) in ingest["markdown"] for c in range(0xFB00, 0xFB07))  # no ligatures
        assert fx["cost_usd"] == pytest.approx(m["cost"]["total_usd"], abs=1e-6)
    assert report["summary"]["total_cost_usd"] > 0
    assert (cfg.out_dir / "report.md").read_text(encoding="utf-8").startswith("# Eval report")


@pytest.mark.slow
@pytest.mark.skipif(not _pipeline_ready(), reason="aadhi.pipeline stages / provider factory not importable yet")
def test_real_pipeline_with_assets_fake_tts(app_env, tmp_path):
    cfg = RunConfig(fixtures_dir=FIXTURES_DIR, out_dir=tmp_path / "assets", provider="fake", assets=True,
                    only=("ohms_law",))
    report = run_eval(cfg)
    fx = report["fixtures"][0]
    assert fx["status"] == "ok", f"{fx['failed_stage']}: {fx['error']}"
    assert fx["metrics"]["audio"]["narrated_scenes"] > 0
