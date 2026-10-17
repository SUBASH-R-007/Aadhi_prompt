"""Job handlers end to end with FakeJobContext + DB rows (project / version / source)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.db import compare_and_set, session_scope
from aadhi.jobs.base import AwaitingReview, FatalJobError, JobCancelled
from aadhi.models import Project, ProjectVersion
from aadhi.pipeline import fake_content, integrations, orchestrator, plan_state
from aadhi.pipeline.base import GenerationOptions, LecturePlan
from aadhi.providers.base import ProviderError
from aadhi.schemas.manifest import AssetManifest
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import asset_refs, get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch, real_timeline=True)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded}


def run(ctx: Any, handler, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.kind = kind
    ctx.payload = payload
    return asyncio.run(handler(ctx))


def generate(world: dict[str, Any], **payload: Any) -> dict[str, Any]:
    s = world["seeded"]
    body = {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1, **payload}
    return run(world["ctx"], orchestrator.generate_lecture, "generate_lecture", body)


def test_generate_lecture_end_to_end(world):
    result = generate(world)
    s = world["seeded"]
    assert result["version_id"] == s.version_id and set(result["issue_counts"]) == {"error", "warning", "info"}
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 2
    sp = Screenplay.model_validate(v.screenplay)
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert sp.scenes and set(manifest.media) == {sc.id for sc in sp.scenes}
    assert sp.companion_sheet.practice_problems and sp.source.filename == "ohm.md"
    assert v.issue_counts == {k: sum(1 for i in v.issues if i["severity"] == k) for k in ("error", "warning", "info")}
    meta = v.generation_meta
    for key in ("models", "prompt_versions", "stage_seconds", "ingest_key", "options", "source_document_id"):
        assert key in meta, key
    assert meta["prompt_versions"]["plan"] == "2" and "plan" not in meta  # plan only kept while in review
    assert {"ingest", "plan", "script", "validate", "companion", "assets", "timeline"} <= set(meta["stage_seconds"])
    if integrations.build_timeline_fn() is not None:
        assert v.has_timeline and v.built_revision == 2 and v.timeline["screenplay_revision"] == 2
    else:
        assert not v.has_timeline
    progress = [e for e in world["ctx"].events if e["type"] == "progress"]
    stages = list(dict.fromkeys(e["stage"] for e in progress))
    assert stages[:3] == ["ingest", "plan", "script"] and stages[-1] == "timeline"
    assert progress[-1]["progress"] == pytest.approx(1.0)
    assert [p["progress"] for p in progress] == sorted(p["progress"] for p in progress)
    refs = asset_refs(s.project_id)
    assert manifest.audio[sp.scenes[0].id].asset_key in refs
    with session_scope() as db:
        assert db.get(Project, s.project_id).current_version_id == s.version_id


def test_review_plan_pauses_then_resumes_with_teacher_edits(world):
    s = world["seeded"]
    opts = OPTIONS.model_copy(update={"review_plan": True}).model_dump(mode="json")
    with pytest.raises(AwaitingReview) as exc:
        generate(world, options=opts)
    state = exc.value.state
    v = get_version(s.version_id)
    assert v.status == "awaiting_review" and v.screenplay is None
    plan = plan_state.load_plan(v)
    assert isinstance(plan, LecturePlan) and plan_state.PLAN_KEY in v.generation_meta
    # the teacher edits a goal (what the API's POST /plan does), then approves
    data = plan.model_dump(mode="json")
    target = data["chapters"][0]["scenes"][1]
    target["goal"] = "TEACHER EDIT: explain with a water-pipe analogy."
    holder = ProjectVersion(id=v.id, generation_meta=dict(v.generation_meta))
    plan_state.save_plan(holder, LecturePlan.model_validate(data))
    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, v.id, {"status": "awaiting_review"},
                               {"status": "generating", "generation_meta": holder.generation_meta})
    n_plan_calls = len(world["providers"].llm.calls_for("GenPlan"))
    generate(world, options=opts, resume_state=state)
    v = get_version(s.version_id)
    assert v.status == "ready"
    assert len(world["providers"].llm.calls_for("GenPlan")) == n_plan_calls  # the approved plan is reused
    sp = Screenplay.model_validate(v.screenplay)
    assert sp.scene_by_id(target["id"]).intent.goal.startswith("TEACHER EDIT")


def test_regenerate_scene(world):
    generate(world)
    s = world["seeded"]
    sp = Screenplay.model_validate(get_version(s.version_id).screenplay)
    target = next(x for x in sp.scenes if x.type == "content")

    def responder(prompt, schema):
        out = fake_content.board_responder(prompt, schema)
        if "Teacher's instructions for this rewrite" in prompt:
            out["title"] = "Regenerated scene"
        return out

    world["providers"].llm.on("GenBoardScene", responder)
    n_tts = len(world["providers"].tts.texts)
    result = run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
                 {"scene_id": target.id, "instructions": "Use a cricket example.", "base_revision": 2})
    assert result == {"version_id": s.version_id, "scene_id": target.id}
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 3
    new_sp = Screenplay.model_validate(v.screenplay)
    assert new_sp.scene_by_id(target.id).title == "Regenerated scene"
    assert [x.id for x in new_sp.scenes] == [x.id for x in sp.scenes]
    history = v.generation_meta["scene_history"][target.id]
    assert history[0]["instructions"] == "Use a cricket example." and history[0]["scene"]["title"] == target.title
    from aadhi.pipeline.assets import scene_hash

    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert manifest.scene_hashes[target.id] == scene_hash(new_sp.scene_by_id(target.id), new_sp.lexicon)
    assert len(world["providers"].tts.texts) == n_tts  # same narration: every beat clip came from the cache
    if integrations.build_timeline_fn() is not None:
        assert v.built_revision == 3
    # a stale base revision is refused without writing
    result = run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
                 {"scene_id": target.id, "instructions": "again", "base_revision": 2})
    assert result["skipped"] == "revision_conflict" and get_version(s.version_id).revision == 3
    with pytest.raises(FatalJobError):
        run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
            {"scene_id": "missing", "instructions": "", "base_revision": 3})
    assert get_version(s.version_id).status == "ready"  # in-place job failure restores the status


def test_build_assets_after_edit_and_cas_conflict(world, monkeypatch):
    generate(world)
    s = world["seeded"]
    v = get_version(s.version_id)
    data = v.screenplay
    first = next(i for i, x in enumerate(data["scenes"]) if x["type"] == "content")
    data["scenes"][first]["beats"][0]["narration"] = "An edited first beat for this scene."
    with session_scope() as db:  # the API's PUT /screenplay
        assert compare_and_set(db, ProjectVersion, v.id, {"revision": 2}, {"screenplay": data, "revision": 3})
    n_tts = len(world["providers"].tts.texts)
    assert run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 3}) == {
        "version_id": s.version_id}
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 3
    assert world["providers"].tts.texts[n_tts:] == ["An edited first beat for this scene."]
    if integrations.build_timeline_fn() is not None:
        assert v.built_revision == 3
    manifest_before = v.asset_manifest

    real = orchestrator.build_assets_detailed

    async def concurrent_edit(*args, **kwargs):
        with session_scope() as db:  # someone saves an edit while assets are being built
            compare_and_set(db, ProjectVersion, s.version_id, {"revision": 3}, {"revision": 4})
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "build_assets_detailed", concurrent_edit)
    result = run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 3})
    assert result["skipped"] == "revision_conflict"
    v = get_version(s.version_id)
    assert v.revision == 4 and v.asset_manifest == manifest_before
    assert any("changed while this job ran" in e["message"] for e in world["ctx"].events if e["type"] == "log")


def test_a_revoice_job_narrates_in_the_new_voice_and_stores_it_while_a_build_keeps_the_voice(world, monkeypatch):
    """O17: an ordinary build of a built lecture keeps its voice whatever its options say; the build_assets job of the
    teacher's re-voice (payload ``voice``, POST /versions/{id}/voice) narrates every scene in the new voice and stores
    it in the version's options."""
    from tests.pipeline.fakes import FakeTTS

    generate(world)  # narrated by the offline voice ("fake")
    s = world["seeded"]
    voices = {"edge": FakeTTS(name="edge"), "fake": world["providers"].tts}
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: voices[name or "fake"])
    with session_scope() as db:  # options that now name Edge (as a stored lecture's might): no re-voice by themselves
        v = db.get(ProjectVersion, s.version_id)
        v.generation_meta = {**v.generation_meta, "options": {**v.generation_meta["options"], "tts_provider": "edge"}}
    n_fake = len(voices["fake"].texts)
    run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 2})
    v = get_version(s.version_id)
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert {a.provider for a in manifest.audio.values() if a.asset_key} == {"fake"}
    assert not voices["edge"].texts and len(voices["fake"].texts) == n_fake and not v.generation_meta["last_build"]["built"]
    assert any(i["code"] == "assets.voice_kept" for i in v.issues)
    revoice = {"tts_provider": "edge", "tts_voice": "en-IN-PrabhatNeural"}
    run(world["ctx"], orchestrator.build_assets_job, "build_assets",
        {"scene_ids": None, "base_revision": 2, "voice": revoice})
    v = get_version(s.version_id)
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert {(a.provider, a.voice) for a in manifest.audio.values() if a.asset_key} == {("edge", "en-IN-PrabhatNeural")}
    assert voices["edge"].texts and len(voices["fake"].texts) == n_fake and v.status == "ready"
    opts = v.generation_meta["options"]
    assert (opts["tts_provider"], opts["tts_voice"]) == ("edge", "en-IN-PrabhatNeural")
    assert opts["target_minutes"] == OPTIONS.target_minutes  # the other options are unchanged
    assert v.generation_meta["last_build"]["revoice"] == revoice
    assert not any(i["code"] == "assets.voice_kept" for i in v.issues)
    # a bad re-voice request fails the job without touching the lecture
    with pytest.raises(FatalJobError):
        run(world["ctx"], orchestrator.build_assets_job, "build_assets",
            {"scene_ids": None, "base_revision": 2, "voice": {"tts_provider": "nobody"}})
    assert get_version(s.version_id).asset_manifest == v.asset_manifest


def test_a_revoice_of_a_version_without_options_of_its_own_stores_only_the_voice(world, monkeypatch):
    """O17-4: a version whose options come from the project's settings (none stored with it) is re-voiced without
    freezing the project's current options into it: it still has no options of its own, so a later change of the
    project's AI engine or image choices still applies to it; the voice it keeps is in its manifest."""
    from tests.pipeline.fakes import FakeTTS

    generate(world)
    s = world["seeded"]
    voices = {"edge": FakeTTS(name="edge"), "fake": world["providers"].tts}
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: voices[name or "fake"])
    with session_scope() as db:
        v = db.get(ProjectVersion, s.version_id)
        v.generation_meta = {k: val for k, val in v.generation_meta.items() if k != "options"}
        db.get(Project, s.project_id).settings = {**OPTIONS.model_dump(mode="json"), "target_minutes": 9}
    revoice = {"tts_provider": "edge", "tts_voice": "en-IN-PrabhatNeural"}
    run(world["ctx"], orchestrator.build_assets_job, "build_assets",
        {"scene_ids": None, "base_revision": 2, "voice": revoice})
    v = get_version(s.version_id)
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert {(a.provider, a.voice) for a in manifest.audio.values() if a.asset_key} == {("edge", "en-IN-PrabhatNeural")}
    assert "options" not in v.generation_meta and v.generation_meta["last_build"]["revoice"] == revoice
    n_edge = len(voices["edge"].texts)
    run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 2})
    assert len(voices["edge"].texts) == n_edge  # and it keeps the new voice
    assert AssetManifest.model_validate(get_version(s.version_id).asset_manifest).audio == manifest.audio


def test_translate_job(world):
    generate(world)
    s = world["seeded"]
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1, source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    world["ctx"].version_id = new_id
    result = run(world["ctx"], orchestrator.translate_job, "translate",
                 {"source_version_id": s.version_id, "target_language": "ta-IN", "translate_board": False})
    assert result == {"version_id": new_id}
    v = get_version(new_id)
    assert v.status == "ready" and v.language == "ta-IN" and v.revision == 2
    sp = Screenplay.model_validate(v.screenplay)
    assert sp.language == "ta-IN" and sp.board_language == "en-IN"
    assert all(b.narration.startswith("தமிழ்: ") for b in sp.scenes[0].all_beats())
    assert v.generation_meta["source_version_id"] == s.version_id
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert manifest.language == "ta-IN"


def test_generate_failure_marks_version_failed(world):
    world["providers"].llm.on("GenPlan", lambda p, s: ProviderError("model unavailable", provider="fake"))
    with pytest.raises(FatalJobError) as exc:
        generate(world)
    assert exc.value.code == "provider"
    v = get_version(world["seeded"].version_id)
    assert v.status == "failed" and "model unavailable" in v.generation_meta["error"]["message"]


def test_cancellation_marks_failed(world):
    world["ctx"].cancelled = True
    with pytest.raises(JobCancelled):
        generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "failed" and v.generation_meta["error"]["message"] == "cancelled"


def test_generate_conflict_on_wrong_base(world):
    result = generate(world, base_revision=7)
    assert result["skipped"] == "revision_conflict"
    assert get_version(world["seeded"].version_id).status == "generating"


def test_handlers_registered():
    from aadhi.jobs.base import _REGISTRY

    for kind in ("generate_lecture", "regenerate_scene", "build_assets", "translate"):
        assert kind in _REGISTRY


def test_lost_lease_never_writes_the_version(world):
    """Version writes are fenced by ``ctx.assert_lease`` (DBJobContext) in the same transaction."""
    calls = {"n": 0}

    def assert_lease(db):
        calls["n"] += 1
        if calls["n"] >= 3:  # start + screenplay writes succeed, then another worker takes the job over
            raise JobCancelled("lease lost", reason="lease_lost")

    world["ctx"].assert_lease = assert_lease  # type: ignore[attr-defined]
    with pytest.raises(JobCancelled) as exc:
        generate(world)
    assert exc.value.reason == "lease_lost"
    v = get_version(world["seeded"].version_id)
    assert v.status == "building" and v.revision == 2 and not v.has_timeline and v.asset_manifest is None
    assert "error" not in v.generation_meta  # a stale worker does not mark the version failed either


def test_merge_asset_issues_replaces_lecture_level_ones():
    from aadhi.pipeline.base import Issue

    old = [
        Issue(code="assets.media_degraded", message="old a", scene_id="a", source="assets"),
        Issue(code="assets.media_degraded", message="old b", scene_id="b", source="assets"),
        Issue(code="assets.tts_fallback", message="old lecture-level", source="assets"),
        Issue(code="board.too_many_items", message="lint", scene_id="a", source="lint"),
    ]
    new = [Issue(code="assets.tts_fallback", message="new lecture-level", source="assets")]
    merged = orchestrator.merge_asset_issues(old, new, rebuilt={"a"})
    assert [i.message for i in merged] == ["old b", "new lecture-level"]


def test_fake_llm_gets_realistic_responders_without_overriding_scripted_ones(app_env, monkeypatch):
    fake_llm = pytest.importorskip("aadhi.providers.llm.fake")
    monkeypatch.setattr(fake_llm.FakeLLM, "_responders", {})

    def mine(prompt, schema):
        return {}

    fake_llm.FakeLLM.register("GenQuiz", mine)
    llm = integrations.get_llm(app_env)
    assert llm.name == "fake"
    assert fake_llm.FakeLLM._responder_for("GenQuiz") is mine
    assert fake_llm.FakeLLM._responder_for("GenPlan") is fake_content.plan_responder


def _fail_first_build(monkeypatch):
    from aadhi.jobs.base import RetryableJobError

    real = orchestrator.build_assets_detailed
    state = {"calls": 0}

    async def flaky(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RetryableJobError("TTS backend hiccup")
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "build_assets_detailed", flaky)
    return state


def test_generate_retry_resumes_after_the_screenplay_was_saved(world, monkeypatch):
    from aadhi.jobs.base import RetryableJobError

    ctx = world["ctx"]
    ctx.job_id = 41
    state = _fail_first_build(monkeypatch)
    with pytest.raises(RetryableJobError):
        generate(world)
    s = world["seeded"]
    v = get_version(s.version_id)
    assert v.status == "building" and v.revision == 2 and v.generation_meta["written_by"]["job_id"] == 41
    llm_calls = len(world["providers"].llm.calls)
    ctx.attempt = 2  # the worker retries the same job with the same payload
    result = generate(world)
    assert result["version_id"] == s.version_id and "skipped" not in result
    assert len(world["providers"].llm.calls) == llm_calls  # no re-planning / re-writing
    assert state["calls"] == 2
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 2 and v.asset_manifest is not None
    if integrations.build_timeline_fn() is not None:
        assert v.has_timeline and v.built_revision == 2
    assert any("Resuming after a retry" in e["message"] for e in ctx.events if e["type"] == "log")
    # a different job finding a newer revision still stops as a conflict
    ctx.job_id = 99
    assert generate(world)["skipped"] == "revision_conflict"


def test_regenerate_and_translate_retries_resume(world, monkeypatch):
    from aadhi.jobs.base import RetryableJobError

    generate(world)
    s = world["seeded"]
    ctx = world["ctx"]
    sp = Screenplay.model_validate(get_version(s.version_id).screenplay)
    target = next(x for x in sp.scenes if x.type == "content")
    ctx.job_id = 7
    _fail_first_build(monkeypatch)
    payload = {"scene_id": target.id, "instructions": "Shorter please.", "base_revision": 2}
    with pytest.raises(RetryableJobError):
        run(ctx, orchestrator.regenerate_scene, "regenerate_scene", payload)
    assert get_version(s.version_id).status == "building"
    n = len(world["providers"].llm.calls)
    ctx.attempt = 2
    assert run(ctx, orchestrator.regenerate_scene, "regenerate_scene", payload) == {"version_id": s.version_id,
                                                                                    "scene_id": target.id}
    assert len(world["providers"].llm.calls) == n
    assert get_version(s.version_id).status == "ready"

    # translate (payload without base_revision, as the API sends it)
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1, source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    ctx.version_id, ctx.job_id, ctx.attempt = new_id, 8, 1
    _fail_first_build(monkeypatch)
    body = {"source_version_id": s.version_id, "target_language": "hi-IN", "translate_board": False}
    with pytest.raises(RetryableJobError):
        run(ctx, orchestrator.translate_job, "translate", body)
    n = len(world["providers"].llm.calls)
    ctx.attempt = 2
    assert run(ctx, orchestrator.translate_job, "translate", body) == {"version_id": new_id}
    assert len(world["providers"].llm.calls) == n
    v = get_version(new_id)
    assert v.status == "ready" and v.revision == 2 and v.language == "hi-IN"


# ---------------------------------------------------------------------------
# regressions (review round 2)
# ---------------------------------------------------------------------------


def test_scanned_pdf_without_a_pdf_reading_provider_fails_before_any_paid_call(job_ctx, monkeypatch, fast_audio):
    import pymupdf

    from tests.pipeline.fakes import png_bytes

    providers = install_providers(monkeypatch)
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.insert_image(page.rect, stream=png_bytes(600, 800, "scanned-notes"))
    seeded = seed_project(job_ctx.assets.storage, doc.tobytes(), "application/pdf", "scan.pdf",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    body = {"source_document_id": seeded.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1}
    with pytest.raises(FatalJobError) as exc:
        run(job_ctx, orchestrator.generate_lecture, "generate_lecture", body)
    assert exc.value.code == "ingest" and "OCR" in str(exc.value)
    assert providers.llm.calls == []  # refused before planning
    v = get_version(seeded.version_id)
    assert v.status == "failed" and "OCR" in v.generation_meta["error"]["message"]


def test_shutdown_release_leaves_the_version_for_the_retry(world):
    ctx = world["ctx"]

    def stopping() -> None:
        raise JobCancelled("worker stopping", reason="shutdown")

    ctx.check_cancelled = stopping  # type: ignore[method-assign]
    with pytest.raises(JobCancelled):
        generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "generating" and "error" not in v.generation_meta
    ctx.cancel_reason = "cancelled"  # the user's cancel wins over the shutdown (DBJobContext.cancel_reason)
    with pytest.raises(JobCancelled):
        generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "failed" and v.generation_meta["error"]["message"] == "cancelled"


def test_generic_error_marks_failed_only_on_the_last_attempt(world, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("critic crashed")

    monkeypatch.setattr(orchestrator, "critique", broken)
    ctx = world["ctx"]
    ctx.attempt = 1  # job_max_attempts is 2: the worker will retry
    with pytest.raises(RuntimeError):
        generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "generating" and "error" not in v.generation_meta
    assert any("will be retried" in e["message"] for e in ctx.events if e["type"] == "log")
    ctx.attempt = ctx.settings.job_max_attempts
    with pytest.raises(RuntimeError):
        generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "failed" and "critic crashed" in v.generation_meta["error"]["message"]


def test_failed_timeline_keeps_the_previous_one(world, monkeypatch):
    if integrations.build_timeline_fn() is None:
        pytest.skip("timeline builder not installed")
    generate(world)
    s = world["seeded"]
    before = get_version(s.version_id)
    assert before.has_timeline and before.built_revision == 2

    def broken_builder(*a, **k):
        raise ValueError("timeline bug")

    monkeypatch.setattr(integrations, "build_timeline_fn", lambda: broken_builder)
    assert run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 2}) == {
        "version_id": s.version_id}
    v = get_version(s.version_id)
    assert v.status == "ready" and v.has_timeline and v.timeline == before.timeline and v.built_revision == 2
    assert any(i["code"] == "timeline.failed" for i in v.issues)
    values = orchestrator._final_values(AssetManifest(), None, 5, [], {})
    assert not {"timeline", "has_timeline", "built_revision"} & set(values)


def test_a_scene_scoped_build_that_skips_changed_scenes_does_not_count_as_built(world):
    """A one-scene build (Visual Review "New AI version" / "Retry", the editor's scene rebuild) stores its manifest and
    timeline, but while another changed scene was reused as it was, the revision is not built: ``timeline_stale``
    stays (no render) until a full build."""
    generate(world)
    s = world["seeded"]
    v = get_version(s.version_id)
    data = v.screenplay
    contents = [i for i, x in enumerate(data["scenes"]) if x["type"] == "content"]
    a, b = data["scenes"][contents[0]]["id"], data["scenes"][contents[1]]["id"]
    data["scenes"][contents[0]]["beats"][0]["narration"] = "An edited first beat that is not built yet."
    data["scenes"][contents[1]]["beats"][0]["narration"] = "An edited beat of the scene that is built now."
    with session_scope() as db:  # the API's PUT /screenplay
        assert compare_and_set(db, ProjectVersion, v.id, {"revision": 2}, {"screenplay": data, "revision": 3})
    before = get_version(s.version_id)
    run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": [b], "base_revision": 3})
    v = get_version(s.version_id)
    manifest = AssetManifest.model_validate(v.asset_manifest)
    assert v.status == "ready" and manifest.stale_scenes == [a] and v.generation_meta["last_build"]["built"] == [b]
    assert v.built_revision == before.built_revision == 2 and v.timeline_stale
    if integrations.build_timeline_fn() is not None:
        assert v.timeline != before.timeline  # the rebuilt scene is in the stored timeline
    run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 3})
    v = get_version(s.version_id)
    assert AssetManifest.model_validate(v.asset_manifest).stale_scenes == []
    if integrations.build_timeline_fn() is not None:
        assert v.built_revision == 3 and not v.timeline_stale


def _store_dynamic(world: dict[str, Any]) -> tuple[dict[str, Any], str, str, str]:
    """The generated lecture with v3 annotations (as a Dynamic lecture, or a newer build's document after a rollback,
    stores them): a ``recap`` role on one content scene, a ``recap`` role with a ``visual_intent`` on another, and a
    teacher-inserted clip scene. Returns (stored document, annotated scene id, scene to regenerate, clip scene id)."""
    s = world["seeded"]
    v = get_version(s.version_id)
    data = v.screenplay
    contents = [i for i, x in enumerate(data["scenes"]) if x["type"] == "content"]
    annotated, target = data["scenes"][contents[0]], data["scenes"][contents[1]]
    annotated["intent"]["narrative_role"] = "recap"
    target["intent"].update(narrative_role="recap", visual_intent="summarize")
    clip = {"id": "lab-clip", "type": "clip", "title": "Lab demo", "chapter_id": annotated["chapter_id"],
            "beats": [], "clip": {"asset_key": "clip-lab-demo", "in_frame": 0, "out_frame": 300}}
    data["scenes"].insert(contents[0] + 1, clip)
    chapter = next(c for c in data["chapters"] if c["id"] == annotated["chapter_id"])
    chapter["scene_ids"].insert(chapter["scene_ids"].index(annotated["id"]) + 1, clip["id"])
    data = Screenplay.model_validate(data).model_dump(mode="json")
    assert data["schema_version"] == 3
    with session_scope() as db:  # stored as a newer build (or the API with DYNAMIC_FEATURES) writes it
        assert compare_and_set(db, ProjectVersion, v.id, {"revision": 2}, {"screenplay": data, "revision": 3})
    return data, annotated["id"], target["id"], clip["id"]


def test_plan_from_screenplay_leaves_out_clip_scenes_and_maps_v3_roles():
    """Design §6.7 / §8.2: a plan holds no clip scene and only classic roles; both maximal documents plan."""
    import json
    from pathlib import Path

    from aadhi.pipeline.screenplay_ops import plan_from_screenplay
    from aadhi.schemas.screenplay import V3_NARRATIVE_ROLES

    root = Path(__file__).resolve().parents[1]
    for path in (root / "core" / "fixtures" / "transform_survival_dynamic.json",
                 root / "schemas" / "fixtures" / "screenplay_v3_maximal.json"):
        sp = Screenplay.model_validate(json.loads(path.read_text(encoding="utf-8")))
        clips = {x.id for x in sp.scenes if x.type == "clip"}
        assert clips and any(x.intent and x.intent.narrative_role in V3_NARRATIVE_ROLES for x in sp.scenes), path.name
        planned = [p for ch in plan_from_screenplay(sp).chapters for p in ch.scenes]
        assert [p.id for p in planned] == [x.id for x in sp.scenes if x.id not in clips], path.name
        assert not {p.narrative_role for p in planned} & set(V3_NARRATIVE_ROLES)


def test_regenerate_scene_in_a_lecture_with_a_clip_scene_and_v3_roles(world):
    """Regenerating any scene works in a lecture with a clip scene or a v3 narrative role (the rollback floor):
    the other scenes, the clip scene among them, come back unchanged, and the rewritten scene keeps its own v3
    role and visual intent."""
    generate(world)
    s = world["seeded"]
    data, annotated, target, clip = _store_dynamic(world)
    result = run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
                 {"scene_id": target, "instructions": "Use a cricket example.", "base_revision": 3})
    assert result == {"version_id": s.version_id, "scene_id": target}
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 4
    out = v.screenplay
    assert [x["id"] for x in out["scenes"]] == [x["id"] for x in data["scenes"]]
    for before, after in zip(data["scenes"], out["scenes"], strict=True):
        if before["id"] != target:
            assert after == before, before["id"]  # the clip scene and the other v3 role among them
    rewritten = next(x for x in out["scenes"] if x["id"] == target)
    assert rewritten["intent"]["narrative_role"] == "recap" and rewritten["intent"]["visual_intent"] == "summarize"
    assert out["schema_version"] == 3
    assert next(x for x in out["scenes"] if x["id"] == annotated)["intent"]["narrative_role"] == "recap"
    assert next(x for x in out["scenes"] if x["id"] == clip)["type"] == "clip"


def test_regenerate_refuses_a_clip_scene(world):
    generate(world)
    s = world["seeded"]
    _, _, _, clip = _store_dynamic(world)
    n = len(world["providers"].llm.calls)
    with pytest.raises(FatalJobError) as exc:
        run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
            {"scene_id": clip, "instructions": "", "base_revision": 3})
    assert exc.value.code == "bad_request"
    v = get_version(s.version_id)
    assert v.revision == 3 and v.status == "ready" and len(world["providers"].llm.calls) == n
