"""Prompt preview (``python -m aadhi.pipeline.preview``): the exact prompts, built offline, with nothing excluded."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.pipeline import preview
from aadhi.pipeline.base import GenerationOptions
from tests.pipeline.fixtures.sme_sources import REPO, SAMPLE_TEMPLATE
from tests.pipeline.test_sme_end_to_end import SECRETS, sme_docx

PACKAGING = ("[0:10", "[0:00", "Estimated duration", "CLIP 1 SCRIPT", "TITLE CARD", "BRIDGE TO NEXT PART")
# script labels: the system prompts name them as things to look out for, the source content must not contain them
SCRIPT_LABELS = ("Aadhi speaks:", "BOARD displays", "COMING UP NEXT", "ANIMATION:", "ON SCREEN")
OPTIONS_FILE = REPO / "evals" / "fixtures" / "sample_template.options.json"


def _blocks(markdown: str) -> dict[str, str]:
    """The fenced system / user prompts of a preview file."""
    out = {}
    for title in ("System prompt", "User prompt"):
        m = re.search(rf"## {title}\n\n(`{{4,}})text\n(.*?)\n\1\n", markdown, re.S)
        assert m, title
        out[title] = m.group(2)
    return out


def _assert_clean(files: dict[str, str]) -> None:
    for name, text in files.items():
        for marker in PACKAGING:
            assert marker not in text, (marker, name)
        if name != "00_source_scope.md":
            user = _blocks(text)["User prompt"]
            for label in SCRIPT_LABELS:
                assert label not in user, (label, name)


def test_cli_writes_every_prompt_for_the_sme_template_without_its_packaging(tmp_path):
    out = tmp_path / "prompts"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "aadhi.pipeline.preview", str(SAMPLE_TEMPLATE), "--options", str(OPTIONS_FILE),
         "--out", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    names = sorted(p.name for p in out.iterdir())
    assert names[:3] == ["00_source_scope.md", "01_brief.md", "02_plan.md"]
    assert len(names) == 4 and names[3].startswith("03_scene_") and names[3] != "03_scene_cold_open.md"
    files = {n: (out / n).read_text(encoding="utf-8") for n in names}
    _assert_clean(files)
    brief, plan, scene = files["01_brief.md"], files["02_plan.md"], files[names[3]]
    assert "| Response schema | `GenBrief`" in brief and "| Temperature | 0.2 |" in brief and "| Model | `" in brief
    assert "| Response schema | `GenPlan`" in plan and "| Temperature | 0.5 |" in plan
    assert "| Response schema | `GenBoardScene" in scene and "| Temperature | 0.6 |" in scene
    assert "`style.md` v" in plan and "`plan.md` v" in plan and "`brief.md` v" in brief
    # the plan prompt carries the offline brief: real concepts, never the synthesiser's placeholders
    user = _blocks(plan)["User prompt"]
    assert "## Concepts to teach" in user and "Boolean postulates" in user and "Sample topic" not in user
    assert "## Author's visual suggestions" in user and "## About the source" in user
    scope = files["00_source_scope.md"]
    assert "| structure |" in scope and "| duration |" in scope and "| Source format | sme_script |" in scope
    assert "Digital System Design" in scope  # document metadata (title cards only) is shown, removed values are not
    assert proc.stdout.count(".md") == 4


def test_prompts_are_exactly_what_the_stages_send():
    """The recorded prompts equal the real prompt builders' output for the same inputs."""
    from aadhi.config import get_settings
    from aadhi.pipeline.brief import build_brief_prompt
    from aadhi.pipeline.plan import (
        PLAN_PROMPTS,
        TeacherMeta,
        build_plan_prompt,
        plan_context,
        scene_source_note,
        skipped_ids,
    )
    from aadhi.pipeline.prompting import join_sections, section, system_prompt
    from aadhi.pipeline.scene_context import LectureContext, positions, scene_prompt_sections

    options = GenerationOptions(target_minutes=10)
    with preview.isolated_workspace():
        result = asyncio.run(preview.collect(SAMPLE_TEMPLATE, options))
        settings = get_settings()
        ingest = result.ingest
        brief_call = result.stages["brief"].call
        assert (brief_call.system, brief_call.user) == build_brief_prompt(ingest, options)
        plan_call = result.stages["plan"].call
        assert plan_call.system == system_prompt(*PLAN_PROMPTS)
        assert plan_call.user == build_plan_prompt(ingest, options, plan_context(ingest, options, settings, result.brief),
                                                   TeacherMeta(**ingest.document_meta.model_dump()), brief=result.brief)
        stage = result.stages["scene"]
        lc = LectureContext(plan=result.plan, ingest=ingest, options=options, lexicon=result.lexicon,
                            figures=list(ingest.figures), skipped_chunk_ids=skipped_ids(result.brief))
        pos = next(p for p in positions(result.plan) if p.planned.id == stage.scene.planned.id)
        note = scene_source_note(ingest, False)
        expected = join_sections(*scene_prompt_sections(lc, pos), section("About the source", note) if note else "",
                                 section("Task", "Write this scene. Respond with JSON that matches the response schema."))
        assert stage.call.user == expected
        assert stage.call.system == system_prompt("style", "scene_board")
        assert pos.index > 0 and pos.planned.type == "content"  # the default: the first teaching scene
        rendered = preview.render(result)
    assert _blocks(rendered["01_brief.md"]) == {"System prompt": brief_call.system, "User prompt": brief_call.user}
    assert _blocks(rendered[f"03_scene_{pos.planned.id}.md"])["User prompt"] == expected


def test_header_values_are_masked_everywhere(tmp_path):
    source = tmp_path / "series_parallel.docx"
    source.write_bytes(sme_docx())
    files = preview.run_preview(source, options=GenerationOptions(target_minutes=8))
    assert set(files) >= {"00_source_scope.md", "01_brief.md", "02_plan.md"} and len(files) == 4
    for name, text in files.items():
        for secret in SECRETS:
            assert secret.lower() not in text.lower(), (secret, name)
    _assert_clean(files)
    scope = files["00_source_scope.md"]
    person = re.search(r"\| person \| (\d+) items? \|", scope)
    assert person and int(person.group(1)) >= 2  # the SME and the reviewer, counted, never named
    assert re.search(r"\| duration \| \d+ items? \|", scope) and re.search(r"\| timecode \| \d+ items? \|", scope)


def test_stages_scene_choice_and_errors(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Ohm's law\n\n## Voltage\n\nVoltage is the push that drives charge around a circuit. It is "
                      "measured in volts.\n\n## Current\n\nCurrent is the rate of flow of charge. It is measured in "
                      "amperes, and V = I × R links it to voltage.\n", encoding="utf-8")
    only_brief = preview.run_preview(source, stages=["brief"])
    assert sorted(only_brief) == ["00_source_scope.md", "01_brief.md"]
    opening = preview.run_preview(source, stages=["scene"], scene="1")
    assert sorted(opening) == ["00_source_scope.md", "03_scene_cold_open.md"]
    assert "| Scene | `cold_open` (title, hook), scene 1 of" in opening["03_scene_cold_open.md"]
    with pytest.raises(ValueError, match="no scene with id 'nope'; the plan's scenes are: cold_open"):
        preview.run_preview(source, scene="nope")
    with pytest.raises(ValueError, match="out of range"):
        preview.run_preview(source, scene="999")
    with pytest.raises(ValueError, match="unknown stage"):
        preview.run_preview(source, stages=["brief", "critic"])
    slides = tmp_path / "slides.pptx"
    slides.write_bytes(b"not a supported source")
    with pytest.raises(ValueError, match="unsupported source type"):
        preview.run_preview(slides)


def test_workspace_is_isolated_offline_and_restored(app_env):
    from aadhi.config import get_settings

    before = {k: os.environ.get(k) for k in ("DATA_DIR", "DATABASE_URL", "LLM_PROVIDER", "OPENAI_API_KEY")}
    data_dir = get_settings().data_dir
    with preview.isolated_workspace() as root:
        inside = get_settings()
        assert inside.llm_provider == "fake" and inside.data_dir == root / "data"
        assert all(os.environ.get(k) == "" for k in preview.API_KEY_VARS)  # blanked, so .env keys never apply
    assert {k: os.environ.get(k) for k in before} == before
    assert get_settings().data_dir == data_dir and not root.exists()
    with pytest.raises(RuntimeError, match="offline fake LLM"):
        preview.RecordingLLM(type("Real", (), {"name": "gemini"})())


def test_cli_reports_usage_errors(tmp_path, capsys):
    assert preview.main([str(tmp_path / "missing.pdf")]) == 2
    assert "no such file" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        preview.main([str(SAMPLE_TEMPLATE), "--stages", "brief,repair"])
    assert exc.value.code == 2


def test_print_mode_writes_every_file_to_stdout(tmp_path, capsys):
    source = Path(tmp_path / "notes.txt")
    source.write_text("Resistance\n\nResistance is the opposition a material offers to the flow of current. "
                      "It is measured in ohms.\n", encoding="utf-8")
    assert preview.main([str(source), "--stages", "brief"]) == 0
    out = capsys.readouterr().out
    assert "<!-- ===== 00_source_scope.md ===== -->" in out and "<!-- ===== 01_brief.md ===== -->" in out
    assert "## User prompt" in out


def test_pdf_preview_sends_clean_teaching_content_only():
    """ohms_law.pdf: its title-page line is metadata, its ligatures are spelled out, the brief accounts for every chunk."""
    files = preview.run_preview(REPO / "evals" / "fixtures" / "ohms_law.pdf", stages=["brief", "plan"])
    scope = files["00_source_scope.md"]
    assert "| Subject | Basic Electrical and Electronics Engineering |" in scope
    assert "| Unit | Electric Circuits |" in scope and "| Session | Session 2 |" in scope
    assert "## Set aside by the concept brief" in scope and "| Concept brief |" in scope
    assert "0 neither (still shown to the planner)" in scope
    for name in ("01_brief.md", "02_plan.md"):
        user = _blocks(files[name])["User prompt"]
        assert not any(chr(c) in user for c in range(0xFB00, 0xFB07)), name  # no "veriﬁcation"
        assert "Experimental verification" in user
        chunks = re.search(r"## Source chunks\n```json\n(.*?)\n```", user, re.S).group(1)
        assert "Unit 1: Electric Circuits" not in chunks and "Session 2" not in chunks, name
    assert "Account for all " in _blocks(files["01_brief.md"])["User prompt"]


def test_scope_report_counts_set_aside_chunks_by_reason_without_their_text():
    from aadhi.pipeline.base import BriefConcept, ConceptBrief, IngestResult, SkippedChunk, SourceChunk

    secret = "Studio booking reference ZQX-77"
    chunks = [SourceChunk(id="c0001", heading_path=["Notes"], text=secret),
              SourceChunk(id="c0002", heading_path=["Ohm's law"], text="Ohm's law states that V = IR."),
              SourceChunk(id="c0003", heading_path=["Coming up"], text="Next time: power."),
              SourceChunk(id="c0004", heading_path=["Extra"], text="An aside.")]
    brief = ConceptBrief(concepts=[BriefConcept(key="ohm", name="Ohm's law", source_refs=["c0002"])],
                         teaching_order=["ohm"],
                         skipped_chunks=[SkippedChunk(chunk_id="c0001", reason="production"),
                                         SkippedChunk(chunk_id="c0003", reason="scaffolding")])
    ingest = IngestResult(markdown="x", chunks=chunks, source_format="notes")
    result = preview.Preview(source_name="notes.md", ingest=ingest, options=GenerationOptions(), brief=brief)
    assert result.brief_skipped == {"production": 1, "scaffolding": 1}
    scope = preview.render_scope(result)
    assert "| production | 1 |" in scope and "| scaffolding | 1 |" in scope
    assert "| Sections the concept brief set aside (never planned or narrated) | 2 |" in scope
    assert "Of 4 sections: 1 cited by a concept or a source question, 2 set aside, 1 neither" in scope
    assert "| c0001 | Notes | 31 |  | set aside (production) |" in scope and "| c0004 | Extra | 9 |  | not cited |" in scope
    assert secret not in scope and "Next time" not in scope  # counts and reasons only, never the text


# --- AI engine --------------------------------------------------------------------------------------

NOTES = ("# Ohm's law\n\n## Voltage\n\nVoltage is the push that drives charge around a circuit. It is measured in "
         "volts.\n\n## Current\n\nCurrent is the rate of flow of charge. It is measured in amperes, and V = I × R "
         "links it to voltage.\n")
# Every model setting pinned (environment beats .env), so the expected names do not depend on a local .env.
MODEL_ENV = {
    "LLM_MODEL_PLAN": "gemini-plan-test", "LLM_MODEL_SCRIPT": "gemini-script-test",
    "LLM_MODEL_CRITIC": "gemini-critic-test", "LLM_MODEL_FAST": "gemini-fast-test",
    "OPENAI_MODEL_PLAN": "gpt-plan-test", "OPENAI_MODEL_SCRIPT": "gpt-script-test",
    "OPENAI_MODEL_CRITIC": "gpt-critic-test", "OPENAI_MODEL_FAST": "gpt-fast-test",
    "ANTHROPIC_MODEL_PLAN": "claude-plan-test", "ANTHROPIC_MODEL_SCRIPT": "claude-script-test",
    "ANTHROPIC_MODEL_CRITIC": "claude-critic-test", "ANTHROPIC_MODEL_FAST": "claude-fast-test",
}


def _model(markdown: str) -> str:
    return re.search(r"^\| Model \| `([^`]+)` \|$", markdown, re.M).group(1)


def test_engine_constants_match_the_factory():
    from aadhi.providers.factory import LLM_ENGINE_LABELS, LLM_ENGINES

    assert preview.ENGINES == LLM_ENGINES
    assert preview.ENGINE_LABELS == {e: LLM_ENGINE_LABELS[e] for e in LLM_ENGINES}
    assert "ANTHROPIC_API_KEY" in preview.API_KEY_VARS
    assert preview.forced_environment(Path("x"))["ANTHROPIC_API_KEY"] == ""


def test_a_chosen_engine_previews_offline_with_that_engines_models(tmp_path, monkeypatch):
    """Options with llm_provider=anthropic: the fake LLM answers, the headers show the Claude models."""
    for name, value in MODEL_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-never-be-used-123456")
    source = tmp_path / "notes.md"
    source.write_text(NOTES, encoding="utf-8")
    options = GenerationOptions(target_minutes=6, llm_provider="anthropic")
    with preview.isolated_workspace():
        from aadhi.config import get_settings
        from aadhi.providers.factory import llm_configured, llm_models

        settings = get_settings()
        assert settings.llm_provider == "fake" and not llm_configured(settings, "anthropic")  # key blanked
        expected = llm_models(settings, "anthropic")
        result = asyncio.run(preview.collect(source, options))
        files = preview.render(result)
    assert expected == {"plan": "claude-plan-test", "script": "claude-script-test", "critic": "claude-critic-test",
                        "fast": "claude-fast-test"}
    calls = {stage: r.call for stage, r in result.stages.items()}
    assert (calls["brief"].model, calls["plan"].model, calls["scene"].model) == (
        expected["fast"], expected["plan"], expected["script"])
    scene_file = next(n for n in files if n.startswith("03_scene_"))
    assert [_model(files[n]) for n in ("01_brief.md", "02_plan.md", scene_file)] == [
        "claude-fast-test", "claude-plan-test", "claude-script-test"]
    assert "| AI engine | Anthropic Claude (`anthropic`; not called: the preview is offline) |" in files["02_plan.md"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-must-never-be-used-123456"  # restored afterwards


def test_engine_flag_overrides_the_options_engine(tmp_path, monkeypatch, capsys):
    for name, value in MODEL_ENV.items():
        monkeypatch.setenv(name, value)
    source = tmp_path / "notes.md"
    source.write_text(NOTES, encoding="utf-8")
    files = preview.run_preview(source, stages=["brief"], options=GenerationOptions(llm_provider="openai"),
                                engine="anthropic")
    assert _model(files["01_brief.md"]) == "claude-fast-test"
    default = preview.run_preview(source, stages=["brief"])
    assert "| AI engine | server default" in default["01_brief.md"]
    assert _model(default["01_brief.md"]) == "gemini-fast-test"  # LLM_MODEL_FAST, as configured
    openai = preview.run_preview(source, stages=["brief"], options=GenerationOptions(llm_provider="openai"))
    assert _model(openai["01_brief.md"]) == "gpt-fast-test"
    with pytest.raises(ValueError, match="unknown AI engine"):
        preview.run_preview(source, stages=["brief"], engine="fake")
    assert preview.main([str(source), "--stages", "brief", "--engine", "gemini"]) == 0
    assert "| Model | `gemini-fast-test` |" in capsys.readouterr().out  # LLM_MODEL_FAST names a Gemini model
    with pytest.raises(SystemExit) as exc:
        preview.main([str(source), "--engine", "klingon"])
    assert exc.value.code == 2
