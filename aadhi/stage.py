"""One plain lesson stage and the next step, derived purely from what is stored (no second state machine).

``derive(facts)`` reads the version's status and review stage, whether its timeline is built from the current
revision, the job running on it and a summary of its renders, and answers with one of ``STAGES`` and a
``next_step`` (``{"action", "label", "href"}``). ``label`` is in plain words; ``href`` is a Studio route when the
step is a page (``#/p/1/v/2/plan``), else null (the action is a button on the project page: ``build`` ->
``POST /api/versions/{vid}/build``, ``render`` -> ``POST /api/versions/{vid}/render``, ``retry`` -> retry the
failed job, ``wait`` -> nothing to do). Rules, first match wins:

1. a version-mutating job queued or running on the version: ``reading`` / ``planning`` / ``writing`` /
   ``building`` from the job's kind and stage (the version waiting for a review comes first);
2. awaiting review: ``source_review`` or ``plan_review``;
3. status ``generating`` / ``building`` without a running job: ``writing`` / ``building``; ``failed``: ``failed``;
4. a render queued or running: ``rendering``;
5. timeline not built from the current revision: ``video_outdated`` when an earlier video exists, else
   ``ready_to_build`` (next step: build);
6. built: ``video_ready`` when a finished video was made from this revision; ``failed`` when the latest render of
   this revision failed; ``video_outdated`` when only older videos exist; else ``ready_to_render``.

A video "matches the current script" when its ``built_revision`` equals the version's ``revision`` and the
timeline is not stale (``matches_current``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STAGES = (
    "reading",
    "source_review",
    "planning",
    "plan_review",
    "writing",
    "ready_to_build",
    "building",
    "ready_to_render",
    "rendering",
    "video_ready",
    "video_outdated",
    "failed",
)
ACTIONS = ("wait", "review_source", "review_plan", "build", "render", "watch", "retry", "open")
WRITING_JOB_STAGES = frozenset({"script", "validate", "companion", "translate"})
BUILDING_JOB_STAGES = frozenset({"assets", "timeline"})
ACTIVE = ("queued", "running")


@dataclass(frozen=True)
class RenderFacts:
    """A version's renders, summarised: the latest one and the newest revision a finished video was made from."""

    latest_id: int | None = None
    latest_status: str | None = None
    latest_built_revision: int | None = None
    best_succeeded_revision: int | None = None  # highest built_revision among succeeded renders
    succeeded: int = 0


@dataclass(frozen=True)
class StageFacts:
    project_id: int
    version_id: int | None
    status: str | None = None  # ProjectVersion.status
    review_stage: str | None = None  # "source" | "plan" while awaiting review
    revision: int = 1
    built_revision: int | None = None
    has_timeline: bool = False
    job_kind: str | None = None  # the version's active job (version-mutating first, else a render)
    job_status: str | None = None
    job_stage: str = ""
    renders: RenderFacts = RenderFacts()


def matches_current(built_revision: int | None, revision: int, version_built_revision: int | None) -> bool:
    """A render matches the current script: made from this revision and the timeline is not stale."""
    return built_revision is not None and built_revision == revision and version_built_revision == revision


def _step(action: str, label: str, href: str | None = None) -> dict[str, Any]:
    return {"action": action, "label": label, "href": href}


def _job_stage(kind: str, stage: str) -> str:
    if kind == "build_assets" or stage in BUILDING_JOB_STAGES:
        return "building"
    if kind == "generate_lecture":
        if stage == "plan":
            return "planning"
        if stage in WRITING_JOB_STAGES:
            return "writing"
        return "reading"  # queued, or reading the source
    return "writing"  # translate, regenerate_scene


WAIT_LABELS = {
    "reading": "Aadhi is reading your notes",
    "planning": "Aadhi is planning the lesson",
    "writing": "Aadhi is writing the lesson",
    "building": "Aadhi is preparing the voice and pictures",
    "rendering": "The video is being made",
}


def derive(facts: StageFacts) -> tuple[str, dict[str, Any]]:
    """``(stage, next_step)`` of a version (see the module docstring)."""
    pid, vid = facts.project_id, facts.version_id
    project_href = f"#/p/{pid}"
    if vid is None or facts.status is None:
        return "failed", _step("open", "Open the lecture to start again", project_href)
    job_active = facts.job_status in ACTIVE
    mutating = job_active and facts.job_kind not in (None, "render_video")
    if facts.status == "awaiting_review":
        if facts.review_stage == "source":
            return "source_review", _step("review_source", "Check how your notes were read", f"#/p/{pid}/v/{vid}/source")
        return "plan_review", _step("review_plan", "Review the lesson plan", f"#/p/{pid}/v/{vid}/plan")
    if mutating:
        stage = _job_stage(str(facts.job_kind), facts.job_stage or "")
        return stage, _step("wait", WAIT_LABELS[stage])
    if facts.status == "generating":
        return "writing", _step("wait", WAIT_LABELS["writing"])
    if facts.status == "building":
        return "building", _step("wait", WAIT_LABELS["building"])
    if facts.status == "failed":
        return "failed", _step("retry", "Something went wrong. Your notes are safe: try again")
    if job_active and facts.job_kind == "render_video":
        return "rendering", _step("wait", WAIT_LABELS["rendering"])
    renders = facts.renders
    built = facts.has_timeline and facts.built_revision is not None and facts.built_revision == facts.revision
    if not built:
        if renders.succeeded:
            return "video_outdated", _step("build", "Build your changes, then make the video again")
        never = facts.built_revision is None and not facts.has_timeline
        return "ready_to_build", _step("build", "Build the lesson" if never else "Build your latest changes")
    if renders.best_succeeded_revision is not None and renders.best_succeeded_revision == facts.revision:
        return "video_ready", _step("watch", "Watch, download or share the video", "#/videos")
    if renders.latest_status == "failed" and renders.latest_built_revision == facts.revision:
        return "failed", _step("render", "The video could not be made. Try again")
    if renders.succeeded:
        return "video_outdated", _step("render", "Make the video again with your changes")
    return "ready_to_render", _step("render", "Make the video")
