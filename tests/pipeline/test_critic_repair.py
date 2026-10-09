"""critic (deterministic checks + scripted judge) and the repair loop."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.pipeline import fake_content
from aadhi.pipeline.base import Issue
from aadhi.pipeline.critic import critique, deterministic_issues, render_scene_for_review, verify_findings
from aadhi.pipeline.gen_models import GenFinding
from aadhi.pipeline.plan import generate_plan
from aadhi.pipeline.repair import repair, repair_detailed
from aadhi.pipeline.script import write_scenes_detailed
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import BoardScene, InteractiveScene, Screenplay, SidePanel, SimulationScene


@pytest.fixture()
def lecture(job_ctx, providers, sample_ingest, options):
    plan_res = asyncio.run(generate_plan(job_ctx, sample_ingest, options))
    sp = asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, sample_ingest, options)).screenplay
    content = next(s for s in sp.scenes if isinstance(s, BoardScene) and s.type == "content")
    return {"sp": sp, "plan": plan_res.plan, "content": content}


def test_deterministic_grounding_issue(sample_ingest):
    sp = Screenplay.model_validate({"scenes": [
        {"id": "a", "type": "content", "beats": [{"id": "a-b1", "narration": "No sources at all here."}]},
        {"id": "b", "type": "content", "beats": [{"id": "b-b1", "narration": "Cited.", "source_refs": ["c0001"]}]},
    ]})
    issues = deterministic_issues(sp, sample_ingest)
    assert [(i.code, i.scene_id, i.severity) for i in issues] == [("grounding.no_source", "a", "info")]


def test_verify_findings_quotes(lecture, sample_ingest):
    scene = lecture["content"]
    narration = scene.beats[1].narration
    chunk = sample_ingest.chunks[0]
    findings = [
        GenFinding(scene_id=scene.id, beat_number=2, category="factual", severity="error", claim=narration[:30],
                   evidence=chunk.text[:40], evidence_chunk=chunk.id, message="Wrong.", suggestion="Fix it."),
        GenFinding(scene_id=scene.id, category="factual", severity="error", claim=narration[:30],
                   evidence="this sentence is not in the source", message="Unsupported."),
        GenFinding(scene_id=scene.id, category="pedagogy", claim="words the scene never says", message="Hallucinated."),
        GenFinding(scene_id="ghost", category="flow", message="Unknown scene."),
    ]
    issues = verify_findings(findings, {scene.id: scene}, {chunk.id: chunk.text})
    assert len(issues) == 2
    first, second = issues
    assert first.code == "factual.error" and first.severity == "error" and first.beat_id == scene.beats[1].id
    assert "Suggestion: Fix it." in first.message and "(source:" in first.message
    assert second.severity == "warning"  # evidence could not be verified
    assert all(i.source == "critic" and i.fixable for i in issues)


def test_critique_with_scripted_judge(job_ctx, providers, lecture, sample_ingest, options):
    scene = lecture["content"]
    claim = scene.beats[1].narration[:25]

    def judge(prompt, schema):
        if f'"id": "{scene.id}"' not in prompt:
            return {"findings": []}
        if "## Problems with your previous answer" not in prompt:
            return {"findings": [{"scene_id": "not-a-scene", "category": "flow", "message": "x"}]}
        return {"findings": [{"scene_id": scene.id, "beat_number": 2, "category": "pedagogy", "severity": "warning",
                              "claim": claim, "message": "No example after the definition.",
                              "suggestion": "Add a concrete example."}]}

    providers.llm.on("GenCritique", judge)
    issues = asyncio.run(critique(job_ctx, lecture["sp"], sample_ingest, options))
    critic = [i for i in issues if i.source == "critic" and i.code == "pedagogy.issue"]
    assert len(critic) == 1 and critic[0].scene_id == scene.id
    calls = providers.llm.calls_for("GenCritique")
    assert len(calls) == len(lecture["sp"].chapters) + 1  # one re-ask for the bad scene id
    assert "## Source" in calls[0]["prompt"] and "Your role" in calls[0]["system"]
    only = asyncio.run(critique(job_ctx, lecture["sp"], sample_ingest, options, scene_ids={scene.id}))
    assert {i.scene_id for i in only if i.scene_id} <= {scene.id}


def test_critique_failure_is_info(job_ctx, providers, lecture, sample_ingest, options):
    providers.llm.on("GenCritique", lambda p, s: ProviderError("judge down", provider="fake"))
    issues = asyncio.run(critique(job_ctx, lecture["sp"], sample_ingest, options))
    assert {i.code for i in issues if i.source == "system"} == {"critic.unavailable"}


def test_repair_rewrites_failing_scenes_only(job_ctx, providers, lecture, sample_ingest, options):
    sp, scene = lecture["sp"], lecture["content"]

    def rewriting(prompt, schema):
        out = fake_content.board_responder(prompt, schema)
        if "## Issues to fix" in prompt and f'"id": "{scene.id}"' in prompt:
            out["title"] = "Rewritten scene"
        return out

    providers.llm.on("GenBoardScene", rewriting)
    issues = [Issue(code="factual.error", severity="error", scene_id=scene.id, source="critic", message="Wrong fact."),
              Issue(code="flow.issue", severity="info", scene_id=sp.scenes[0].id, source="critic", message="minor")]
    res = asyncio.run(repair_detailed(job_ctx, sp, issues, sample_ingest, options, plan=lecture["plan"]))
    assert res.repaired_scene_ids == [scene.id] and res.rounds == 1
    new = res.screenplay.scene_by_id(scene.id)
    assert new.title == "Rewritten scene" and new.id == scene.id and new.chapter_id == scene.chapter_id
    assert all(i.code != "factual.error" for i in res.issues)  # addressed by the rewrite
    assert any(i.code == "flow.issue" for i in res.issues)  # info issues stay
    others = [s for s in sp.scenes if s.id != scene.id]
    assert all(res.screenplay.scene_by_id(s.id) == s for s in others)
    call = [c for c in providers.llm.calls_for("GenBoardScene") if "## Issues to fix" in c["prompt"]][0]
    assert "## Current scene" in call["prompt"] and "Wrong fact." in call["prompt"]
    assert "Rewriting an existing scene" in call["system"]


def test_repair_is_bounded_and_keeps_original_on_failure(job_ctx, providers, lecture, sample_ingest, options):
    sp, scene = lecture["sp"], lecture["content"]

    def stubborn(prompt, schema):
        out = fake_content.board_responder(prompt, schema)
        if "## Issues to fix" in prompt:
            out["beats"][0]["narration"] = "Still uses $x$ markup in the narration."
        return out

    providers.llm.on("GenBoardScene", stubborn)
    issue = Issue(code="narration.markup", severity="warning", scene_id=scene.id, source="lint", message="markup")
    data = sp.model_dump(mode="json")
    idx = [s.id for s in sp.scenes].index(scene.id)
    data["scenes"][idx]["beats"][0]["narration"] = "Uses $y$ markup."
    broken = Screenplay.model_validate(data)
    res = asyncio.run(repair_detailed(job_ctx, broken, [issue], sample_ingest, options))
    assert res.rounds == 2  # never more than two rounds
    assert any(i.code == "narration.markup" and i.scene_id == scene.id for i in res.issues)

    providers.llm.on("GenBoardScene", lambda p, s: ProviderError("down", provider="fake"))
    kept = asyncio.run(repair(job_ctx, broken, [issue], sample_ingest, options))
    assert kept.scene_by_id(scene.id) == broken.scene_by_id(scene.id)


def test_repair_entry_point_keeps_set_aside_chunks_out_of_the_rewrite(job_ctx, providers, lecture, sample_ingest,
                                                                      options):
    from aadhi.pipeline.base import ConceptBrief, SkippedChunk

    sp, scene = lecture["sp"], lecture["content"]
    ids = [c.id for c in sample_ingest.chunks]
    own = [r for b in scene.all_beats() for r in b.source_refs] or ids[:1]
    near = next(ids[i + d] for i, cid in enumerate(ids) if cid in own for d in (1, -1)
                if 0 <= i + d < len(ids) and ids[i + d] not in own)
    marker = next(c.text for c in sample_ingest.chunks if c.id == near)[:60]
    issue = Issue(code="factual.error", severity="error", scene_id=scene.id, source="critic", message="Wrong fact.")

    def rewrites() -> list[str]:
        return [c["prompt"] for c in providers.llm.calls_for("GenBoardScene") if "## Issues to fix" in c["prompt"]]

    asyncio.run(repair(job_ctx, sp, [issue], sample_ingest, options))
    assert marker in rewrites()[-1]  # without a brief the neighbour is nearby context
    brief = ConceptBrief(skipped_chunks=[SkippedChunk(chunk_id=near, reason="production")])
    asyncio.run(repair(job_ctx, sp, [issue], sample_ingest, options, brief=brief))
    assert marker not in rewrites()[-1]


def test_render_scene_for_review(lecture):
    view = render_scene_for_review(lecture["content"])
    assert view["beats"][0]["n"] == 1 and "reveals" in view["beats"][0]


def test_quoted_passages_must_be_in_the_source(sample_ingest):
    from aadhi.pipeline.critic import quoted_passages

    assert quoted_passages('He said “Resistance is the opposition a material offers” and "too short".') == [
        "Resistance is the opposition a material offers"]
    sp = Screenplay.model_validate({"scenes": [
        {"id": "a", "type": "content", "beats": [
            {"id": "a-b1", "narration": 'The notes say "Resistance is the opposition a material offers to the flow '
                                        'of current" exactly.', "source_refs": ["c0001"]},
            {"id": "a-b2", "narration": 'Some claim "copper is the worst conductor ever made" which is invented.',
             "source_refs": ["c0001"]},
        ]},
    ]})
    issues = deterministic_issues(sp, sample_ingest)
    assert [(i.code, i.beat_id, i.severity, i.source, i.fixable) for i in issues] == [
        ("grounding.quote_not_found", "a-b2", "warning", "critic", True)]
    from aadhi.pipeline.validate import scenes_needing_repair

    assert list(scenes_needing_repair(issues)) == ["a"]  # a critic warning is repairable


# ---------------------------------------------------------------------------
# regressions (review round 2)
# ---------------------------------------------------------------------------


def _big_screenplay(n: int, chapters: list[dict] | None = None) -> Screenplay:
    return Screenplay.model_validate({
        "session_title": "Long lecture",
        "chapters": chapters or [],
        "scenes": [{"id": f"s{i}", "type": "content", "title": f"Scene {i}",
                    "beats": [{"id": f"s{i}-b1", "narration": f"Idea number {i} explained."}]} for i in range(n)],
    })


def test_plan_from_screenplay_splits_oversized_groups(job_ctx, providers, sample_ingest, options):
    from aadhi.pipeline.repair import rewrite_scene
    from aadhi.pipeline.screenplay_ops import plan_from_screenplay

    unchaptered = _big_screenplay(61)  # e.g. a legacy import or a teacher-edited screenplay
    plan = plan_from_screenplay(unchaptered)
    assert [len(c.scenes) for c in plan.chapters] == [60, 1] and [c.id for c in plan.chapters] == ["main", "main-2"]
    assert [s.id for s in plan.all_scenes()] == [s.id for s in unchaptered.scenes]
    chaptered = _big_screenplay(130, [{"id": "all", "title": "All", "scene_ids": [f"s{i}" for i in range(130)]}])
    assert [len(c.scenes) for c in plan_from_screenplay(chaptered).chapters] == [60, 60, 10]
    new, _ = asyncio.run(rewrite_scene(job_ctx, unchaptered, "s60", [], sample_ingest, options))
    assert new is not None and new.id == "s60" and new.chapter_id is None


def test_rewrite_keeps_teacher_settings(job_ctx, providers, lecture, sample_ingest, options):
    from aadhi.pipeline.repair import carry_over, rewrite_scene
    from aadhi.pipeline.script import FALLBACK_NOTE

    sp, scene = lecture["sp"], lecture["content"]
    edited = scene.model_copy(update={"notes": "Teacher: mention lab 3.", "mascot_position": "popup_bottom_left"})
    data = sp.model_dump(mode="json")
    data["scenes"][[s.id for s in sp.scenes].index(scene.id)] = edited.model_dump(mode="json")
    sp2 = Screenplay.model_validate(data)
    new, _ = asyncio.run(rewrite_scene(job_ctx, sp2, scene.id, [], sample_ingest, options, instructions="Shorter."))
    assert new.notes == "Teacher: mention lab 3." and new.mascot_position == "popup_bottom_left"
    assert new.chapter_id == scene.chapter_id
    # media overrides survive when the slot still exists
    sim = SimulationScene.model_validate({
        "id": "sim", "type": "simulation", "title": "Sim", "manim": {"code": "class A(AadhiScene):\n    pass"},
        "override_asset_key": "upload-abc", "notes": FALLBACK_NOTE,
        "side_panel": {"kind": "image", "image_prompt": "a cell", "override_asset_key": "upload-panel"},
        "beats": [{"id": "sim-b1", "narration": "Watch."}],
    })
    fresh = sim.model_copy(update={"override_asset_key": None, "notes": "Model note.", "side_panel": SidePanel(
        kind="image", image_prompt="a new cell"), "mascot_position": "left"})
    kept = carry_over(sim, fresh)
    assert kept.override_asset_key == "upload-abc" and kept.side_panel.override_asset_key == "upload-panel"
    assert kept.side_panel.image_prompt == "a new cell" and kept.notes == "Model note."  # fallback note replaced
    other_panel = fresh.model_copy(update={"side_panel": SidePanel(kind="chart", chart={
        "labels": ["a"], "datasets": [{"data": [1.0]}]})})
    assert carry_over(sim, other_panel).side_panel.override_asset_key is None  # different panel: no override
    play = InteractiveScene.model_validate({"id": "play", "type": "interactive", "title": "Play", "p5_code": "function setup(){}",
                                            "poster_override_asset_key": "upload-poster",
                                            "beats": [{"id": "play-b1", "narration": "Try it."}]})
    assert carry_over(play, play.model_copy(update={"poster_override_asset_key": None})).poster_override_asset_key == (
        "upload-poster")


def test_unexpected_critic_error_is_advisory(job_ctx, providers, lecture, sample_ingest, options):
    providers.llm.on("GenCritique", lambda p, s: AttributeError("judge bug"))
    issues = asyncio.run(critique(job_ctx, lecture["sp"], sample_ingest, options))
    assert {i.code for i in issues if i.source == "system"} == {"critic.unavailable"}
