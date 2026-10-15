"""Batch-4 regression list, jobs and checks (design §10.3, B0 item 11): one test for each fix listed under "Batch 4:
review fixes" in ``docs/INTEGRATION_ANDRYAN.md`` that lives in a job or a pure check, each from an angle the batch's own
tests do not take. The route fixes are in ``tests/api/test_batch4_regressions.py``; the Studio fixes in
``web/tests/studio/batch4Regressions.test.js``."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from aadhi.db import session_scope
from aadhi.models import ProjectVersion
from aadhi.pipeline import orchestrator
from aadhi.pipeline import source_review as sr
from aadhi.pipeline.base import IngestResult
from aadhi.pipeline.chunking import chunk_markdown
from aadhi.pipeline.validate import hidden_as_notes
from aadhi.schemas.screenplay import Screenplay
from tests.api.factories import screenplay_dict
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers


def stored_issue(code: str, severity: str, source: str, scene_id: str | None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "source": source, "scene_id": scene_id, "fixable": False,
            "message": f"{code} on {scene_id}"}


def seed_hidden_lecture(job_ctx: Any, issues: list[dict[str, Any]] | None = None) -> int:
    """A ready lecture (revision 1) whose second scene ``s2`` is skipped in the video; returns the version id."""
    data = screenplay_dict()
    data["scenes"][1]["hidden"] = True
    seeded = seed_project(job_ctx.assets.storage, b"## Ohm\n\nV = I x R.\n", "text/markdown", "ohm.md",
                          version_status="ready", screenplay=data)
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    if issues is not None:
        with session_scope() as db:
            db.get(ProjectVersion, seeded.version_id).set_issues(issues)
    return seeded.version_id


# --- "every issue of a hidden scene ... is served and counted as a note (... the orchestrator's issue_counts) and
# stored at its real severity" --------------------------------------------------------------------------------------


def test_a_build_job_stores_a_hidden_scenes_issues_as_they_are_and_counts_them_as_notes(job_ctx, monkeypatch,
                                                                                          fast_audio):
    install_providers(monkeypatch, real_timeline=True)
    vid = seed_hidden_lecture(job_ctx, [stored_issue("critic.factual", "error", "critic", "s2"),
                                        stored_issue("system.translate_failed", "error", "system", "s2"),
                                        stored_issue("critic.clarity", "warning", "critic", "s1")])
    job_ctx.kind, job_ctx.payload = "build_assets", {"scene_ids": None, "base_revision": 1}
    assert asyncio.run(orchestrator.build_assets_job(job_ctx)) == {"version_id": vid}
    v = get_version(vid)
    assert v.status == "ready" and v.revision == 1
    stored = {(i["code"], i["scene_id"]): i["severity"] for i in v.issues}
    assert stored[("critic.factual", "s2")] == "error" and stored[("system.translate_failed", "s2")] == "error"
    assert stored[("critic.clarity", "s1")] == "warning"
    served = hidden_as_notes(v.issues, {"s2"})
    assert v.issue_counts == {sev: sum(1 for i in served if i["severity"] == sev) for sev in ("error", "warning", "info")}
    assert v.issue_counts["error"] == sum(1 for i in v.issues if i["severity"] == "error" and i["scene_id"] != "s2")
    assert v.issue_counts["warning"] >= 1  # the shown scene's warning still counts


# --- "preflight items, render warnings and the job log carry scene_number (the place in the screenplay, as the editor
# numbers scenes; scene_index stays the timeline place)" ------------------------------------------------------------


def test_the_render_job_numbers_scenes_as_the_editor_does_in_its_log(job_ctx):
    from aadhi.compose import video
    from aadhi.compose.timeline import build_timeline

    vid = seed_hidden_lecture(job_ctx)
    with session_scope() as db:
        v = db.get(ProjectVersion, vid)
        v.set_timeline(build_timeline(v.get_screenplay(), None, settings=job_ctx.settings, version_id=vid, revision=1))
        v.built_revision = 1
    job = video._load_job(job_ctx, video.RenderPayload(render_id=1, include_intro=False), vid)
    assert job.numbers == {"s1": 1, "s2": 2, "q1": 3}  # the hidden s2 is counted, as in the scene list
    items = video.render_warnings(job.timeline, numbers=job.numbers)
    quiz = next(it for it in items if it["scene_id"] == "q1")
    assert (quiz["scene_index"], quiz["scene_number"]) == (1, 3)  # second in the video, third in the editor
    video._log_warnings(job_ctx, items)
    lines = [e["message"] for e in job_ctx.events if e["type"] == "log"]
    assert any(m.startswith("Scene 3 (Check yourself): ") for m in lines), lines
    assert any(m.startswith("Scene 1 (Scene 1): ") for m in lines)
    assert not any(m.startswith("Scene 2 ") for m in lines), "never the timeline place of the quiz"
    older = [{k: v for k, v in it.items() if k != "scene_number"} for it in items]  # items without numbers
    job_ctx.events.clear()
    video._log_warnings(job_ctx, older)
    assert any(e["message"].startswith("Scene 2 (Check yourself): ") for e in job_ctx.events)
    job_ctx.events.clear()
    video._log_warnings(job_ctx, [{"message": "Something to check.", "reason": "x"}])  # no number, title or id
    assert [e["message"] for e in job_ctx.events if e["type"] == "log"] == ["A scene: Something to check."]


# --- "POST /lint with a failing quality analysis runs it once (quality.COMPUTE sentinel)" ---------------------------


def test_lint_tells_a_failed_quality_analysis_from_one_not_given(monkeypatch):
    from aadhi.pipeline import quality, validate

    sp = Screenplay.model_validate({"scenes": [
        {"id": "s1", "type": "content", "title": "Learning", "board": [
            {"id": "s1-i1", "kind": "bullet", "text": "Today we meet [[Machine Learning]] and its uses."}],
         "beats": [{"id": "s1-b1", "narration": "This is a short clean beat.", "board_item_id": "s1-i1"}]},
        {"id": "s2", "type": "content", "title": "Models", "board": [
            {"id": "s2-i1", "kind": "bullet", "text": "In practice, [[machine learning]] builds models."}],
         "beats": [{"id": "s2-b1", "narration": "This is a short clean beat.", "board_item_id": "s2-i1"}]},
    ]})
    calls: list[int] = []
    real = quality.analyse

    def counted(screenplay: Screenplay):
        calls.append(1)
        return real(screenplay)

    monkeypatch.setattr(quality, "analyse", counted)
    assert quality.COMPUTE is not None and validate.lint.__kwdefaults__["quality"] is quality.COMPUTE
    computed = validate.lint(sp)
    assert len(calls) == 1 and any(i.code.startswith("terminology.") for i in computed)
    assert [i.model_dump() for i in validate.lint(sp, quality=quality.COMPUTE)] == [i.model_dump() for i in computed]
    assert len(calls) == 2
    analysis = quality.analysis_of(sp)
    assert len(calls) == 3
    given = validate.lint(sp, quality=analysis)
    assert len(calls) == 3 and [i.model_dump() for i in given] == [i.model_dump() for i in computed]  # not run again
    failed = validate.lint(sp, quality=None)  # None: the analysis ran and failed, so it is not run again
    assert len(calls) == 3
    assert not [i for i in failed if i.code in quality.QUALITY_CODES]
    assert [i.model_dump() for i in failed] == [i.model_dump() for i in computed if i.code not in quality.QUALITY_CODES]
    assert quality.lint_quality(sp, None) == [] and len(calls) == 3


# --- "Source report: no implicit products with an exponent or quantities with units as inline formulas, and no
# backtracking on long digit runs" -------------------------------------------------------------------------------------


def md_ingest(markdown: str) -> IngestResult:
    return IngestResult(markdown=markdown, chunks=chunk_markdown(markdown), source_mime="text/markdown",
                        detected_language="en-IN")


def test_the_source_report_counts_only_explicit_inline_formulas_and_reads_long_numbers_in_linear_time():
    assert sr.REVIEW_VERSION == "3"  # cached reports of the older rules are not served
    ingest = md_ingest(
        "## Energy\n\nEinstein showed that E = mc² for a body at rest, where m is the mass.\n\n"
        "## Circles\n\nThe area of a circle is A = πr², where r is the radius.\n\n"
        "## Units\n\nOn the bill 1 kWh = 3.6 × 10⁶ J of energy, and on the road 1 km/h = 5/18 m/s exactly.\n\n"
        "## Motion\n\nThen F = ma by the second law, and the momentum p = m × v of the cart.\n")
    inventory = sr.analyze(ingest).inventory
    assert [f.expression for f in inventory.formulas] == ["p = m × v"], inventory.formulas
    assert inventory.formula_count == 1
    # number runs with separators: one parse each, so a line without a relation after them is read in linear time
    lines = [
        "x = " + "1.2." * 1200,
        "x = " + "1,2," * 1200,
        "x = " + ",".join(["123"] * 1200),
        "x = " + "12.34" * 900,
        "x = " + "1,23.4" * 800,
        " + ".join(["12"] * 1200) + " =",
        "x = " + "a^2 + " * 800,
        "1" + "=1." * 1600,
    ]
    start = time.perf_counter()
    for line in lines:
        sr.inline_formulas(line)
    assert time.perf_counter() - start < 1.0
    big = md_ingest("## Data\n\n" + "\n\n".join(f"The serial is {'9' * 4000} and x = 2 here." for _ in range(20)) + "\n")
    start = time.perf_counter()
    sr.analyze(big)
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize(("line", "expected"), [
    ("The force is F = m × a on the cart.", [("F = m × a", "F = m × a")]),
    ("The area is A = π × r^2 for a circle.", [("A = π × r^2", "A = π × r^2")]),  # an explicit product with an exponent
    ("Then A = πr^2 for the circle.", []),  # the same product written implicitly
    ("Remember that 3 m/s = 10.8 km/h when converting.", []),  # quantities with units
    ("Total = 1,234,567 + 2,345,678 = 3,580,245 marks.", []),  # long words and numbers only: never a formula
])
def test_inline_formula_rules(line: str, expected: list[tuple[str, str]]):
    assert sr.inline_formulas(line) == expected
