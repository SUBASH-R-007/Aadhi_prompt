"""aadhi.stage: one lesson stage and the next step, derived from stored facts only."""

from __future__ import annotations

import pytest

from aadhi.stage import ACTIONS, STAGES, RenderFacts, StageFacts, derive, matches_current


def facts(**kw) -> StageFacts:
    base = {"project_id": 3, "version_id": 7, "status": "ready", "revision": 4, "built_revision": 4, "has_timeline": True}
    base.update(kw)
    return StageFacts(**base)


def stage_of(**kw) -> str:
    return derive(facts(**kw))[0]


@pytest.mark.parametrize(
    ("kind", "job_stage", "expected"),
    [
        ("generate_lecture", "", "reading"),
        ("generate_lecture", "ingest", "reading"),
        ("generate_lecture", "plan", "planning"),
        ("generate_lecture", "script", "writing"),
        ("generate_lecture", "validate", "writing"),
        ("generate_lecture", "companion", "writing"),
        ("generate_lecture", "assets", "building"),
        ("generate_lecture", "timeline", "building"),
        ("translate", "translate", "writing"),
        ("translate", "assets", "building"),
        ("regenerate_scene", "script", "writing"),
        ("build_assets", "", "building"),
    ],
)
def test_a_running_job_decides_the_stage(kind, job_stage, expected):
    stage, step = derive(facts(status="generating", job_kind=kind, job_status="running", job_stage=job_stage))
    assert stage == expected and step["action"] == "wait" and step["href"] is None and step["label"]


def test_a_queued_build_on_a_ready_version_reads_as_building():
    assert stage_of(job_kind="build_assets", job_status="queued") == "building"


def test_reviews_link_to_their_pages():
    stage, step = derive(facts(status="awaiting_review", review_stage="source", job_kind="generate_lecture",
                               job_status="awaiting_review"))
    assert stage == "source_review" and step == {"action": "review_source", "label": "Check how your notes were read",
                                                 "href": "#/p/3/v/7/source"}
    stage, step = derive(facts(status="awaiting_review", review_stage="plan"))
    assert stage == "plan_review" and step["action"] == "review_plan" and step["href"] == "#/p/3/v/7/plan"


def test_statuses_without_a_running_job():
    assert stage_of(status="generating") == "writing"
    assert stage_of(status="building") == "building"
    stage, step = derive(facts(status="failed", built_revision=None, has_timeline=False))
    assert stage == "failed" and step["action"] == "retry" and "safe" in step["label"]


def test_build_and_render_steps():
    stage, step = derive(facts(status="draft", built_revision=None, has_timeline=False))
    assert (stage, step["action"], step["label"]) == ("ready_to_build", "build", "Build the lesson")
    stage, step = derive(facts(revision=5, built_revision=4))
    assert (stage, step["action"], step["label"]) == ("ready_to_build", "build", "Build your latest changes")
    stage, step = derive(facts())
    assert (stage, step["action"]) == ("ready_to_render", "render")
    assert stage_of(job_kind="render_video", job_status="running") == "rendering"
    assert stage_of(job_kind="render_video", job_status="queued") == "rendering"


def test_videos_match_or_are_outdated():
    current = RenderFacts(latest_id=9, latest_status="succeeded", latest_built_revision=4, best_succeeded_revision=4,
                          succeeded=2)
    stage, step = derive(facts(renders=current))
    assert stage == "video_ready" and step == {"action": "watch", "label": "Watch, download or share the video",
                                               "href": "#/videos"}
    older = RenderFacts(latest_id=9, latest_status="succeeded", latest_built_revision=3, best_succeeded_revision=3,
                        succeeded=1)
    stage, step = derive(facts(renders=older))
    assert stage == "video_outdated" and step["action"] == "render"
    stage, step = derive(facts(revision=5, renders=older))  # edited since: build first
    assert stage == "video_outdated" and step["action"] == "build"
    # a newer failed render of this revision does not hide the matching video
    retry_failed = RenderFacts(latest_id=10, latest_status="failed", latest_built_revision=4,
                               best_succeeded_revision=4, succeeded=1)
    assert stage_of(renders=retry_failed) == "video_ready"


def test_a_failed_render_of_this_revision_asks_to_try_again():
    failed = RenderFacts(latest_id=3, latest_status="failed", latest_built_revision=4, succeeded=0)
    stage, step = derive(facts(renders=failed))
    assert stage == "failed" and step["action"] == "render"
    cancelled = RenderFacts(latest_id=3, latest_status="cancelled", latest_built_revision=4, succeeded=0)
    assert stage_of(renders=cancelled) == "ready_to_render"


def test_a_job_writing_the_version_wins_over_a_render():
    assert stage_of(job_kind="build_assets", job_status="running", renders=RenderFacts(succeeded=1,
                    best_succeeded_revision=4)) == "building"


def test_no_version():
    stage, step = derive(StageFacts(project_id=3, version_id=None))
    assert stage == "failed" and step["action"] == "open" and step["href"] == "#/p/3"


def test_every_answer_uses_the_documented_vocabulary():
    cases = [facts(), facts(status="failed"), facts(status="awaiting_review"), facts(status="draft"),
             facts(job_kind="generate_lecture", job_status="running", job_stage="plan")]
    for f in cases:
        stage, step = derive(f)
        assert stage in STAGES and step["action"] in ACTIONS and set(step) == {"action", "label", "href"}


def test_matches_current():
    assert matches_current(4, 4, 4)
    assert not matches_current(3, 4, 4)  # an older revision
    assert not matches_current(4, 4, 3)  # the timeline is stale
    assert not matches_current(None, 4, 4)
