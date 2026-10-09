"""Job handlers: ``generate_lecture``, ``regenerate_scene``, ``build_assets``, ``translate``.

* Version writes are compare-and-set on ``revision`` (payload ``base_revision``): on a mismatch the
  job logs a warning and stops without writing (expensive work is cached in the asset store, so a
  retry or a new build is cheap). When the job context can fence its lease
  (``DBJobContext.assert_lease``), the fence runs in the same transaction as every version write.
* Statuses: generating -> (awaiting_review) -> building -> ready | failed. In-place jobs
  (regenerate_scene, build_assets) restore ``ready`` on failure when the version was built before.
  A failure the worker will retry (a graceful-shutdown release, a lost lease, a transient error with
  attempts left) leaves the status alone: the retry continues from it.
* A timeline that cannot be built keeps the previous one (now stale: ``built_revision`` is left
  unchanged) and records a ``timeline.failed`` issue. A scene-scoped ``build_assets`` (``scene_ids``) that
  reused other changed scenes as they were stores its timeline but leaves ``built_revision`` too, so the
  version stays ``timeline_stale`` (no render) until a full build.
* A retried job whose earlier attempt already saved the screenplay (``generation_meta['written_by']``
  names this job) resumes at the asset stage instead of stopping as a conflict.
* Every LLM call uses the lecture's AI engine (``GenerationOptions.llm_provider``, else ``LLM_PROVIDER``):
  regeneration, resumed and continued jobs read it from the version's stored options, and a
  translation uses its source version's engine.
* ``generation_meta`` records options, models (+ the engine as ``provider``), prompt versions, stage
  durations, cost, the ingest key (+ counts of what source scoping set aside, never the values), the
  concept brief, the plan while awaiting review and the last five versions of every regenerated scene. The
  screenplay as ``generate_lecture`` / ``translate`` wrote it is kept as a private snapshot blob
  (``generation_meta['generated_snapshot_key']``, ``aadhi.changes``); scene regeneration never replaces it.
* Before planning, ``brief.build_brief`` extracts the concepts to teach from the scoped source; the
  planner builds the lecture from it. A failed brief is logged and planning continues from the source.
  The chunks the brief set aside as non-teaching material reach neither the planner nor the scene
  writers, repair and regeneration included (the brief is kept in ``generation_meta['brief']``; a
  translation copies it from its source version, and older translations fall back to that version's).
* Document metadata found in the source (subject / unit / session) fills only the title-card fields
  the teacher left empty.
* Source review (job payload ``review_source``, off by default): after ingest and the concept brief the
  job pauses (``AwaitingReview`` stage ``source_review``, ``generation_meta['review_stage'] = 'source'``)
  so the teacher can check how the source was read and correct it (``source_review.SourceOverrides`` in
  ``generation_meta['source_overrides']``: parts to set aside, parts the brief set aside to restore,
  concept names). The continuation reuses the brief the teacher saw and applies the corrections
  (``source_review.effective_brief`` / ``without_chunks``) when they were made on the same extract; set-aside
  parts then reach no later stage, scene regeneration included. Without ``review_source`` nothing changes.
* DB access runs in threads (``asyncio.to_thread`` + ``ctx.session()``).
* A retried ``generate_lecture`` / ``translate`` job reuses the LLM stages its earlier attempt finished
  (brief + plan, scenes, critic + repair, companion sheet, translation) from job-scoped checkpoints
  (``checkpoint.StageCheckpoints``), keyed by every input that shapes them: it never pays for them twice.
  Scenes are checkpointed one by one as well (``checkpoint.SceneCheckpoints``), so an attempt that stopped
  while writing them keeps the scenes it finished.
* Still-wanted gate: before each LLM stage the job checks that the version still has the revision it
  works on, and during an asset build every paid generation (``GENERATION_CLAIM_KINDS``) first re-checks
  it (``storage.assets.generation_guard``, at most every few seconds). Once the revision changed the
  final compare-and-set cannot succeed, so the job stops at once as a revision conflict instead of
  paying for media (e.g. AI video of a deleted scene) that would be thrown away.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import inspect
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy import select

from ..jobs.base import AwaitingReview, BudgetExceeded, FatalJobError, JobCancelled, RetryableJobError, job_handler
from ..models import ProjectVersion
from ..providers.base import ProviderError, ProviderNotConfigured, RateLimited
from ..schemas.manifest import AssetManifest
from ..schemas.screenplay import CompanionSheet, LexiconEntry, Screenplay, SourceInfo
from ..storage.assets import GenerationGuard, generation_guard
from . import dbops, integrations, plan_state, source_review
from .assets import build_assets_detailed
from .base import PIPELINE_PROGRESS, ConceptBrief, GenerationOptions, IngestResult, Issue, LecturePlan
from .brief import BriefUnavailable, brief_model, build_brief
from .checkpoint import SceneCheckpoints, StageCheckpoints, stage_digest
from .companion import build_sheet
from .critic import critic_model, critique
from .ingest import IngestError, extract_key, ingest_source, load_ingest, load_version_ingest
from .plan import TeacherMeta, generate_plan, plan_model
from .prompting import prompt_versions
from .repair import RepairResult, repair_detailed, rewrite_scene
from .screenplay_ops import options_for, replace_scenes
from .script import ScriptResult, script_model, write_scenes_detailed
from .source_scope import excluded_counts
from .translate import TranslateResult, translate_model, translate_screenplay
from .validate import hidden_as_notes, hidden_ids, lint

log = logging.getLogger(__name__)

SCENE_HISTORY_LIMIT = 5
ASSET_SOURCES = ("assets", "manim")
GUARD_RECHECK_SECONDS = 5.0  # a paid generation re-reads the version's revision at most this often


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class Conflict(Exception):
    """The version changed under us (revision mismatch): stop without writing."""


class VersionChanged(JobCancelled):
    """The version changed while its assets were being built (raised by the generation guard). A
    ``JobCancelled``, so the asset builder passes it through instead of degrading one scene; the job then
    ends like a revision ``Conflict``."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="version_changed")


async def _db(ctx: Any, fn: Callable[..., Any], *args: Any) -> Any:
    def run() -> Any:
        with ctx.session() as db:
            return fn(db, *args)

    return await asyncio.to_thread(run)


async def _flush(ctx: Any) -> None:
    flush = getattr(ctx, "flush", None)
    if flush is not None:
        await flush()


async def _cas(ctx: Any, vid: int, expected: dict[str, Any], values: dict[str, Any]) -> bool:
    """Compare-and-set on the version, lease-fenced in the same transaction when the context supports it.

    ``DBJobContext.assert_lease(db)`` runs a fenced heartbeat first (raising ``JobCancelled`` with
    reason ``lease_lost`` if another worker owns the job now), so a stale worker can never overwrite
    a version.
    """
    await _flush(ctx)
    fence = getattr(ctx, "assert_lease", None)

    def run(db: Any) -> bool:
        if callable(fence):
            fence(db)
        return bool(dbops.cas_version(db, vid, expected, values))

    return bool(await _db(ctx, run))


def _redact(ctx: Any, text: str, limit: int = 300) -> str:
    return ctx.settings.redact(text)[:limit]


def _issues_values(issues: list[Issue], hidden: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Issues stored at their real severity; those of ``hidden`` scenes counted as notes (``hidden_as_notes``)."""
    dumped = [i.model_dump(mode="json") for i in issues]
    return {"issues": dumped, "issue_counts": dbops.issue_counts(hidden_as_notes(dumped, hidden))}


def _parse_issues(raw: list[dict[str, Any]]) -> list[Issue]:
    out = []
    for r in raw:
        try:
            out.append(Issue.model_validate(r))
        except ValidationError:
            continue
    return out


def merge_asset_issues(old: list[Issue], new_asset: list[Issue], rebuilt: set[str]) -> list[Issue]:
    """Keep previous asset issues of scenes that were reused, add the new ones.

    Lecture-level asset issues (no scene id) always come from the latest build only.
    """
    kept = [i for i in old if i.source in ASSET_SOURCES and i.scene_id is not None and i.scene_id not in rebuilt]
    return kept + new_asset


class StageClock:
    def __init__(self) -> None:
        self.durations: dict[str, float] = {}
        self._stage: str | None = None
        self._t0 = 0.0

    def start(self, stage: str) -> None:
        self.stop()
        self._stage, self._t0 = stage, time.monotonic()

    def stop(self) -> None:
        if self._stage is not None:
            self.durations[self._stage] = round(self.durations.get(self._stage, 0.0) + time.monotonic() - self._t0, 3)
            self._stage = None


def _cost(ctx: Any) -> float | None:
    v = getattr(ctx, "cost_usd", None)
    return round(float(v), 4) if isinstance(v, int | float) else None


def _require_version(ctx: Any) -> int:
    if ctx.version_id is None:
        raise FatalJobError("this job has no version", code="bad_request")
    return int(ctx.version_id)


def version_options(meta: dict[str, Any], project: dbops.ProjectSnapshot | None) -> GenerationOptions:
    """The options the version was generated with (generation_meta), else the project's."""
    for raw in (meta.get("options"), project.settings if project else None):
        if raw:
            try:
                return GenerationOptions.model_validate(raw)
            except ValidationError:
                continue
    return GenerationOptions()


async def _timeline(ctx: Any, sp: Screenplay, manifest: AssetManifest, vid: int, revision: int) -> tuple[Any | None, list[Issue]]:
    fn = integrations.build_timeline_fn()
    if fn is None:
        ctx.log("The timeline builder is not installed yet; assets are ready but the version has no timeline.", "warning")
        return None, []
    try:
        tl = await asyncio.to_thread(lambda: fn(sp, manifest, settings=ctx.settings, version_id=vid, revision=revision))
    except (JobCancelled, BudgetExceeded):
        raise
    except Exception as exc:  # noqa: BLE001 - a timeline bug must not lose the generated assets
        msg = _redact(ctx, f"{type(exc).__name__}: {exc}", 200)
        log.exception("timeline build failed for version %s", vid)
        return None, [Issue(code="timeline.failed", severity="error", source="system", fixable=False,
                            message=f"The timeline could not be built ({msg}).")]
    return tl, []


def _final_values(manifest: AssetManifest, timeline: Any, revision: int, issues: list[Issue], meta: dict[str, Any],
                  *, complete: bool = True, hidden: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Final version write. Without a new timeline the previous one (if any) is kept untouched. ``complete``
    false (a scene-scoped build that reused changed scenes as they were): the new timeline is stored, but the
    revision does not count as built (``timeline_stale`` stays, so a render waits for a full build)."""
    values: dict[str, Any] = {
        "asset_manifest": manifest.model_dump(mode="json"),
        "status": "ready",
        "generation_meta": meta,
        **_issues_values(issues, hidden),
    }
    if timeline is not None:
        values.update(timeline=timeline.model_dump(mode="json"), has_timeline=True)
        if complete:
            values["built_revision"] = revision
    return values


async def _mark_failed(ctx: Any, vid: int, message: str, statuses: tuple[str, ...] = ("generating", "building", "awaiting_review"),
                       status: str = "failed") -> None:
    fence = getattr(ctx, "assert_lease", None)

    def run(db: Any) -> None:
        if callable(fence):  # a worker that lost its lease must not touch the version
            fence(db)
        v = dbops.load_version(db, vid)
        meta = dict(v.generation_meta) if v else {}
        meta["error"] = {"message": message[:500], "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        dbops.set_status_if(db, vid, statuses, {"status": status, "generation_meta": meta})

    try:
        await _flush(ctx)
        await _db(ctx, run)
    except Exception:  # noqa: BLE001 - best effort while already failing
        log.exception("could not mark version %s as %s", vid, status)


def _max_attempts(ctx: Any) -> int:
    value = getattr(ctx, "max_attempts", None)
    return int(value) if isinstance(value, int) and value > 0 else int(ctx.settings.job_max_attempts)


def _cancel_reason(ctx: Any, exc: JobCancelled) -> str:
    """The effective stop reason (the context's view wins: a user's cancel beats a shutdown)."""
    reason = getattr(ctx, "cancel_reason", None)
    return reason if isinstance(reason, str) and reason else exc.reason


def _will_retry(ctx: Any) -> bool:
    """True when the worker will run this job again after a generic failure (mirrors its outcome rules)."""
    if getattr(ctx, "cancel_requested", False) is True or getattr(ctx, "budget_error", None):
        return False
    return ctx.attempt < _max_attempts(ctx)


async def _guard(ctx: Any, vid: int, work: Callable[[], Awaitable[dict[str, Any]]], *, on_fail_status: str = "failed",
                 restore_statuses: tuple[str, ...] = ("generating", "building", "awaiting_review")) -> dict[str, Any]:
    """Run ``work`` translating errors into job errors and version statuses.

    The version is only marked failed (or restored) when the job really ends: a graceful-shutdown
    release or a lost lease requeue/hand over the job, and generic errors are retried by the worker
    while attempts remain, so those leave the status for the next attempt.
    """
    try:
        return await work()
    except (Conflict, VersionChanged) as exc:
        ctx.log(f"The version changed while this job ran ({exc}); nothing was overwritten.", "warning")
        log.warning("version %s: revision conflict, job stopped without writing", vid)
        return {"version_id": vid, "skipped": "revision_conflict"}
    except AwaitingReview:
        raise
    except JobCancelled as exc:
        if _cancel_reason(ctx, exc) not in ("lease_lost", "shutdown"):
            await _mark_failed(ctx, vid, "cancelled", restore_statuses, on_fail_status)
        raise
    except RateLimited as exc:
        if ctx.attempt >= _max_attempts(ctx):
            await _mark_failed(ctx, vid, "provider rate limit", restore_statuses, on_fail_status)
        raise RetryableJobError(_redact(ctx, f"provider rate limited: {exc}")) from exc
    except RetryableJobError:
        if ctx.attempt >= _max_attempts(ctx):
            await _mark_failed(ctx, vid, "transient failure", restore_statuses, on_fail_status)
        raise
    except BudgetExceeded:
        await _mark_failed(ctx, vid, "budget exceeded", restore_statuses, on_fail_status)
        raise
    except IngestError as exc:
        await _mark_failed(ctx, vid, str(exc), restore_statuses, on_fail_status)
        raise FatalJobError(f"Could not read the source: {exc}", code="ingest") from exc
    except ProviderNotConfigured as exc:
        await _mark_failed(ctx, vid, "provider not configured", restore_statuses, on_fail_status)
        raise FatalJobError(_redact(ctx, f"AI provider is not configured: {exc}"), code="provider") from exc
    except ProviderError as exc:
        await _mark_failed(ctx, vid, _redact(ctx, str(exc)), restore_statuses, on_fail_status)
        raise FatalJobError(_redact(ctx, f"AI provider error: {exc}"), code="provider") from exc
    except FatalJobError as exc:
        await _mark_failed(ctx, vid, str(exc), restore_statuses, on_fail_status)
        raise
    except Exception as exc:
        if _will_retry(ctx):
            ctx.log(_redact(ctx, f"This attempt failed ({type(exc).__name__}: {exc}); the job will be retried."), "warning")
        else:
            await _mark_failed(ctx, vid, _redact(ctx, f"{type(exc).__name__}: {exc}"), restore_statuses, on_fail_status)
        raise


async def _version_brief(ctx: Any, snap: dbops.VersionSnapshot) -> ConceptBrief | None:
    """The concept brief a version's lecture was built from.

    A version keeps its own (``generation_meta['brief']``). A translation made before translations
    carried it over, and a copy of such a translation, have none: they fall back to the brief of the
    version they were translated from (``generation_meta['source_version_id']``), as long as that
    version was built from the same source document (chunk ids are only meaningful for it).
    """
    current: dbops.VersionSnapshot | None = snap
    seen: set[int] = set()
    doc = snap.generation_meta.get("source_document_id")
    while current is not None and current.id not in seen and len(seen) < 8:
        seen.add(current.id)
        brief = plan_state.load_brief(current)
        if brief is not None:
            return brief
        src_id = current.generation_meta.get("source_version_id")
        if not isinstance(src_id, int) or isinstance(src_id, bool):
            return None
        current = await _db(ctx, dbops.load_version, src_id)
        if current is None or current.project_id != snap.project_id or (
                current.generation_meta.get("source_document_id") != doc):
            return None
    return None


async def _load_ingest(ctx: Any, meta: dict[str, Any], project_id: int) -> IngestResult:
    """The IngestResult the version was generated from (its own extract, else the source's latest stored extract,
    else re-ingest, else empty). The version's own extract (``ingest_key``) comes first: a newer INGEST_VERSION
    re-chunks the source, and the version's ``source_refs`` point at the chunks it was written from."""
    own = await load_version_ingest(ctx, meta.get("ingest_key"))
    if own is not None:
        return own
    source = await _db(ctx, dbops.load_source, meta.get("source_document_id"), project_id)
    if source is None:
        source = await _db(ctx, dbops.load_source, None, project_id)
    if source is not None and source.extracted_key:
        try:
            return await load_ingest(ctx, source.extracted_key)
        except (OSError, ValueError, KeyError):
            log.info("stored extract for source %s unavailable; re-ingesting", source.id)
    if source is not None:
        try:
            return await ingest_source(ctx, source)
        except IngestError as exc:
            ctx.log(f"The source could not be re-read ({exc}); continuing without it.", "warning")
    return IngestResult(markdown="")


def _accepts_kw(fn: Callable[..., Any], name: str) -> bool:
    """True when ``fn`` takes keyword argument ``name`` (stages gain parameters over time)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _lint(sp: Screenplay, options: GenerationOptions, ingest: IngestResult) -> list[Issue]:
    """Lint with exact chunk refs and the ingest (source-leak checks) when the linter takes it."""
    chunk_ids = {c.id for c in ingest.chunks} or None
    if _accepts_kw(lint, "ingest"):
        return lint(sp, options, chunk_ids=chunk_ids, ingest=ingest)
    return lint(sp, options, chunk_ids=chunk_ids)


def teacher_meta(project: Any, ingest: IngestResult) -> TeacherMeta:
    """Project metadata; the source's own subject / unit / session fill only the empty fields."""
    doc = ingest.document_meta
    return TeacherMeta(
        (getattr(project, "subject_name", None) or "") or doc.subject_name,
        (getattr(project, "unit_name", None) or "") or doc.unit_name,
        (getattr(project, "session_number", None) or "") or doc.session_number,
        (getattr(project, "session_title", None) or "") or doc.session_title,
    )


async def _concept_brief(ctx: Any, ingest: IngestResult, options: GenerationOptions) -> ConceptBrief | None:
    """The concept brief, or None (logged) when it cannot be built: the lecture never fails because of it."""
    try:
        return await build_brief(ctx, ingest, options)
    except (JobCancelled, BudgetExceeded):
        raise
    except BriefUnavailable as exc:
        ctx.log(f"Planning directly from the source ({exc}).")
    except Exception as exc:  # noqa: BLE001 - the brief is an aid; planning works without it
        log.warning("concept brief failed: %s", type(exc).__name__, exc_info=True)
        ctx.log(_redact(ctx, f"The concepts could not be extracted first ({type(exc).__name__}: {exc}); "
                             "the lecture is planned directly from the source.", 400), "warning")
    return None


def _teacher_scoped(ingest: IngestResult, meta: dict[str, Any]) -> IngestResult:
    """``ingest`` without the parts the teacher set aside in the source review (when they were made on this
    version's extract and every one of them is in ``ingest``); unchanged otherwise."""
    ov = source_review.overrides_for(meta, meta.get("ingest_key"))
    if ov is None or not ov.excluded_chunk_ids:
        return ingest
    if not set(ov.excluded_chunk_ids) <= {c.id for c in ingest.chunks}:
        return ingest  # another extract: the ids name other text
    return source_review.without_chunks(ingest, ov.excluded_chunk_ids)


async def _pause_for_source_review(ctx: Any, vid: int, base: int, meta: dict[str, Any], brief: ConceptBrief | None,
                                   options: GenerationOptions, clock: StageClock, source_id: int,
                                   progress: float) -> NoReturn:
    """Store the brief the teacher will check, mark the version awaiting its source review and pause the job."""
    holder = SimpleNamespace(generation_meta=meta)
    plan_state.save_brief(holder, brief)
    meta = holder.generation_meta
    meta[source_review.REVIEW_STAGE_KEY] = source_review.REVIEW_STAGE_SOURCE
    meta.update(models={"brief": brief_model(options, ctx.settings),
                        "provider": integrations.llm_engine(options, ctx.settings)},
                options=options.model_dump(mode="json"))
    clock.stop()
    meta["stage_seconds"] = clock.durations
    if not await _cas(ctx, vid, {"revision": base}, {"status": "awaiting_review", "generation_meta": meta}):
        raise Conflict("revision changed while the source was read")
    ctx.progress("plan", progress, "Ready for you to check how the source was read")
    raise AwaitingReview("Aadhi has read the source. Check how it was read before the lecture is planned.", state={
        "stage": source_review.RESUME_STAGE, "version_id": vid, "base_revision": base, "source_document_id": source_id,
    })


async def _plan(ctx: Any, ingest: IngestResult, options: GenerationOptions, teacher: TeacherMeta,
                brief: ConceptBrief | None) -> Any:
    if brief is not None and _accepts_kw(generate_plan, "brief"):
        return await generate_plan(ctx, ingest, options, teacher, brief=brief)
    return await generate_plan(ctx, ingest, options, teacher)


# ---------------------------------------------------------------------------
# still-wanted gate
# ---------------------------------------------------------------------------


def _revision_of(db: Any, vid: int) -> int | None:
    return db.execute(select(ProjectVersion.revision).where(ProjectVersion.id == vid)).scalar_one_or_none()


async def _ensure_current(ctx: Any, vid: int, revision: int, before: str) -> None:
    """Stop (as a revision conflict) before an expensive stage when the version no longer has ``revision``."""
    current = await _db(ctx, _revision_of, vid)
    if current != revision:
        raise Conflict(f"revision {current} != {revision} before {before}")


class _ProductionGuard:
    """``generation_guard`` for an asset build of ``revision``: a paid generation is refused
    (``VersionChanged``) once the version's revision changed. Thread-safe; reads the revision at most
    every ``GUARD_RECHECK_SECONDS``."""

    def __init__(self, ctx: Any, vid: int, revision: int) -> None:
        self.ctx, self.vid, self.revision = ctx, vid, revision
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._changed: str | None = None
        self.clock: Callable[[], float] = time.monotonic

    def __call__(self, kind: str, key: str) -> None:
        with self._lock:
            if self._changed is not None:
                raise VersionChanged(self._changed)
            if self._checked_at is not None and self.clock() - self._checked_at < GUARD_RECHECK_SECONDS:
                return
        with self.ctx.session() as db:
            current = _revision_of(db, self.vid)
        with self._lock:
            if current != self.revision:
                self._changed = f"revision {current} != {self.revision} while assets were built; {kind} not generated"
                raise VersionChanged(self._changed)
            self._checked_at = self.clock()


def production_guard(ctx: Any, vid: int, revision: int) -> GenerationGuard:
    return _ProductionGuard(ctx, vid, revision)


async def _build_assets_guarded(ctx: Any, vid: int, revision: int, sp: Screenplay, options: GenerationOptions,
                                **kwargs: Any) -> Any:
    """``build_assets_detailed`` with paid generations gated on the version still being at ``revision``."""
    with generation_guard(production_guard(ctx, vid, revision)):
        return await build_assets_detailed(ctx, sp, options, **kwargs)


# ---------------------------------------------------------------------------
# LLM stage checkpoints (what a retried job reuses; see checkpoint.py)
# ---------------------------------------------------------------------------


def _dump(model: Any) -> Any:
    return None if model is None else model.model_dump(mode="json")


def _stage_inputs(ctx: Any, vid: int, base: int, options: GenerationOptions, meta: dict[str, Any],
                  teacher: TeacherMeta, source: Any) -> dict[str, Any]:
    """Everything (besides earlier stage outputs) that shapes the LLM stages of a lecture generation."""
    settings = ctx.settings
    return {
        "version_id": vid, "base_revision": base, "options": options.model_dump(mode="json"),
        "engine": integrations.llm_engine(options, settings),
        "models": {"brief": brief_model(options, settings), "plan": plan_model(options, settings),
                   "script": script_model(options, settings), "critic": critic_model(settings, options)},
        "prompt_versions": prompt_versions(), "ingest_key": meta.get("ingest_key"),
        "source": {"id": source.id, "sha256": source.sha256}, "teacher": dataclasses.asdict(teacher),
    }


def _plan_data(brief: ConceptBrief | None, plan: LecturePlan, seed: list[LexiconEntry], notes: list[str]) -> dict[str, Any]:
    return {"brief": _dump(brief), "plan": _dump(plan), "lexicon": [e.model_dump(mode="json") for e in seed],
            "notes": list(notes)}


def _plan_from(data: dict[str, Any] | None) -> tuple[ConceptBrief | None, LecturePlan, list[LexiconEntry], list[str]] | None:
    if data is None:
        return None
    try:
        brief = ConceptBrief.model_validate(data["brief"]) if data.get("brief") is not None else None
        return (brief, LecturePlan.model_validate(data["plan"]),
                [LexiconEntry.model_validate(e) for e in data.get("lexicon") or []], [str(n) for n in data.get("notes") or []])
    except (KeyError, TypeError, ValidationError):
        return None


def _script_data(script: ScriptResult) -> dict[str, Any]:
    return {"screenplay": _dump(script.screenplay), "issues": [i.model_dump(mode="json") for i in script.issues],
            "fallback_scene_ids": list(script.fallback_scene_ids), "plan": _dump(script.plan),
            "source_attached": script.source_attached}


def _script_from(data: dict[str, Any] | None) -> ScriptResult | None:
    if data is None:
        return None
    try:
        return ScriptResult(
            Screenplay.model_validate(data["screenplay"]), [Issue.model_validate(i) for i in data.get("issues") or []],
            [str(x) for x in data.get("fallback_scene_ids") or []],
            plan=LecturePlan.model_validate(data["plan"]) if data.get("plan") is not None else None,
            source_attached=bool(data.get("source_attached")),
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _repair_data(rep: RepairResult) -> dict[str, Any]:
    return {"screenplay": _dump(rep.screenplay), "issues": [i.model_dump(mode="json") for i in rep.issues],
            "repaired_scene_ids": list(rep.repaired_scene_ids), "rounds": rep.rounds}


def _repair_from(data: dict[str, Any] | None) -> RepairResult | None:
    if data is None:
        return None
    try:
        return RepairResult(Screenplay.model_validate(data["screenplay"]),
                            [Issue.model_validate(i) for i in data.get("issues") or []],
                            [str(x) for x in data.get("repaired_scene_ids") or []], int(data.get("rounds") or 0))
    except (KeyError, TypeError, ValueError):
        return None


def _sheet_from(data: dict[str, Any] | None) -> CompanionSheet | None:
    if data is None:
        return None
    try:
        return CompanionSheet.model_validate(data["sheet"])
    except (KeyError, TypeError, ValidationError):
        return None


def _translation_from(data: dict[str, Any] | None) -> TranslateResult | None:
    if data is None:
        return None
    try:
        return TranslateResult(Screenplay.model_validate(data["screenplay"]),
                               [Issue.model_validate(i) for i in data.get("issues") or []])
    except (KeyError, TypeError, ValidationError):
        return None


def _reused(ctx: Any, ckpt: StageCheckpoints, stage: str, what: str) -> None:
    if stage in ckpt.reused:
        ctx.log(f"Reusing {what} from this job's previous attempt (not generated again).", stage=stage)


# ---------------------------------------------------------------------------
# shared tail: assets -> timeline -> final write; same-job resume
# ---------------------------------------------------------------------------

WRITER_KEY = "written_by"
MetaUpdate = Callable[[dict[str, Any], Any, StageClock], None]


def writer_mark(ctx: Any, revision: int) -> dict[str, Any]:
    """Who wrote ``revision`` (stored in generation_meta so a retry of the same job can resume)."""
    return {"job_id": ctx.job_id, "kind": ctx.kind, "revision": revision}


def written_by_this_job(ctx: Any, snap: dbops.VersionSnapshot, base: int | None) -> bool:
    """True when this job (an earlier attempt) already saved the current revision and then failed.

    The worker retries a job with the same id and payload; without this check the retry would see
    ``revision != base_revision`` and stop as a conflict, leaving the version half built. ``base``
    None (payload without a base revision) only requires that the current revision is ours.
    """
    mark = snap.generation_meta.get(WRITER_KEY) or {}
    return bool(
        ctx.job_id
        and snap.screenplay is not None
        and (base is None or snap.revision == base + 1)
        and mark.get("job_id") == ctx.job_id
        and mark.get("kind") == ctx.kind
        and mark.get("revision") == snap.revision
    )


async def _build_and_finish(
    ctx: Any,
    vid: int,
    sp: Screenplay,
    options: GenerationOptions,
    revision: int,
    issues: list[Issue],
    meta: dict[str, Any],
    clock: StageClock,
    *,
    previous: AssetManifest | None,
    progress: tuple[float, float],
    done_message: str,
    meta_update: MetaUpdate,
) -> Any:
    """Build assets (incrementally from ``previous``) and the timeline, then write the final state (CAS)."""
    lo, hi = progress
    clock.start("assets")
    ctx.progress("assets", lo, "Building narration and media")
    build = await _build_assets_guarded(ctx, vid, revision, sp, options, previous=previous, progress=(lo, hi))
    clock.start("timeline")
    ctx.progress("timeline", hi, "Building the timeline")
    timeline, tl_issues = await _timeline(ctx, sp, build.manifest, vid, revision)
    clock.stop()
    kept = [i for i in issues if i.source not in ASSET_SOURCES and i.code != "timeline.failed"]
    final_issues = kept + merge_asset_issues(issues, build.issues, set(build.built)) + tl_issues
    meta_update(meta, build, clock)
    values = _final_values(build.manifest, timeline, revision, final_issues, meta, hidden=hidden_ids(sp))
    if not await _cas(ctx, vid, {"revision": revision}, values):
        raise Conflict("revision changed while assets were built")
    ctx.progress("timeline", 1.0, done_message)
    return final_issues


async def _resume_build(ctx: Any, vid: int, snap: dbops.VersionSnapshot, *, progress: tuple[float, float],
                        done_message: str) -> list[Issue]:
    """Continue a retried job whose earlier attempt saved the screenplay: assets + timeline only."""
    sp = snap.get_screenplay()
    assert sp is not None
    project = await _db(ctx, dbops.load_project, snap.project_id)
    options = options_for(sp, version_options(snap.generation_meta, project))
    meta = dict(snap.generation_meta)
    meta.pop("error", None)
    if not await _cas(ctx, vid, {"revision": snap.revision}, {"status": "building"}):
        raise Conflict("revision changed before the build resumed")
    ctx.log("Resuming after a retry: the script is already saved, so only the assets are built.")
    clock = StageClock()

    def update(m: dict[str, Any], build: Any, c: StageClock) -> None:
        m.setdefault("resumed_builds", []).append({"at": _now(), "built": len(build.built), "reused": len(build.reused),
                                                   "stage_seconds": c.durations, "cost_usd": _cost(ctx)})
        m["resumed_builds"] = m["resumed_builds"][-10:]

    return await _build_and_finish(ctx, vid, sp, options, snap.revision, _parse_issues(snap.issues), meta, clock,
                                   previous=snap.get_manifest(), progress=progress, done_message=done_message,
                                   meta_update=update)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _record_generated_snapshot(ctx: Any, meta: dict[str, Any], document: dict[str, Any]) -> None:
    """Keep the screenplay exactly as this job wrote it (``aadhi.changes``: what the teacher changed since, and
    reverting a scene) as a private snapshot blob; its key goes into ``meta`` and is saved with the screenplay.
    Only ``generate_lecture`` and ``translate`` call it. Best effort: a storage failure leaves the lecture without
    a snapshot (its changes read as unavailable) and never fails the job."""
    from ..changes import SNAPSHOT_META_KEY, store_snapshot

    try:
        meta[SNAPSHOT_META_KEY] = await asyncio.to_thread(store_snapshot, ctx.assets, document,
                                                          created_by=getattr(ctx, "user_id", None))
    except Exception as exc:  # noqa: BLE001 - the lecture matters more than its change record
        meta.pop(SNAPSHOT_META_KEY, None)
        log.warning("version %s: the generated screenplay could not be kept (%s)", ctx.version_id, type(exc).__name__)


# ---------------------------------------------------------------------------
# generate_lecture
# ---------------------------------------------------------------------------


@job_handler("generate_lecture")
async def generate_lecture(ctx: Any) -> dict[str, Any]:
    """ingest -> plan [-> review] -> script -> validate -> companion -> assets -> timeline."""
    vid = _require_version(ctx)
    return await _guard(ctx, vid, lambda: _generate(ctx, vid))


def _counts(issues: list[Issue], hidden: frozenset[str] = frozenset()) -> dict[str, int]:
    return dbops.issue_counts(hidden_as_notes([i.model_dump(mode="json") for i in issues], hidden))


async def _generate(ctx: Any, vid: int) -> dict[str, Any]:
    payload = dict(ctx.payload or {})
    snap = await _db(ctx, dbops.load_version, vid)
    if snap is None:
        raise FatalJobError("version not found", code="not_found")
    base = int(payload.get("base_revision") or snap.revision)
    if snap.revision != base:
        if written_by_this_job(ctx, snap, base):
            issues = await _resume_build(ctx, vid, snap, progress=PIPELINE_PROGRESS["assets"], done_message="Lecture ready")
            await _db(ctx, dbops.set_current_version_if_unset, snap.project_id, vid)
            return {"version_id": vid, "issue_counts": _counts(issues, hidden_ids(snap.screenplay))}
        raise Conflict(f"revision {snap.revision} != base {base}")
    project = await _db(ctx, dbops.load_project, snap.project_id)
    try:
        options = GenerationOptions.model_validate(payload.get("options") or (project.settings if project else {}) or {})
    except ValidationError as exc:
        raise FatalJobError("invalid generation options", code="bad_request") from exc
    resume = payload.get("resume_state") or {}
    after_source_review = resume.get("stage") == source_review.RESUME_STAGE
    clock = StageClock()
    settings = ctx.settings
    meta: dict[str, Any] = dict(snap.generation_meta)
    meta.pop("error", None)
    start: dict[str, Any] = {"status": "generating"}
    if after_source_review:  # the teacher checked the source: the version no longer waits for it
        meta.pop(source_review.REVIEW_STAGE_KEY, None)
        start["generation_meta"] = dict(meta)
    if not await _cas(ctx, vid, {"revision": base}, start):
        raise Conflict("revision changed before start")

    # 1 ingest ------------------------------------------------------------------------
    clock.start("ingest")
    lo, hi = PIPELINE_PROGRESS["ingest"]
    ctx.progress("ingest", lo, "Reading the source")
    source_id = payload.get("source_document_id") or resume.get("source_document_id")
    source = await _db(ctx, dbops.load_source, source_id, snap.project_id)
    if source is None:
        raise FatalJobError("the source document was not found", code="not_found")
    ingest = await ingest_source(ctx, source)
    for w in ingest.warnings:
        ctx.log(w, "warning")
    meta.update(source_document_id=source.id, ingest_key=extract_key(source.sha256, source.mime, settings),
                ingest={"chunks": len(ingest.chunks), "figures": len(ingest.figures), "pages": ingest.pages,
                        "attach_original": ingest.attach_original, "truncated": ingest.truncated,
                        "detected_language": ingest.detected_language, "source_format": ingest.source_format,
                        "visual_notes": len(ingest.visual_notes), "excluded": excluded_counts(ingest.excluded)})
    ctx.progress("ingest", hi, f"Read {len(ingest.chunks)} sections and {len(ingest.figures)} figures")
    ctx.check_cancelled()
    # the teacher's corrections from a source review (only on the extract they were made on)
    full_ingest = ingest
    overrides: source_review.SourceOverrides | None = None
    reviewed_brief: ConceptBrief | None = None
    if resume:
        if snap.generation_meta.get("ingest_key") == meta["ingest_key"]:
            overrides = source_review.overrides_for(meta, meta["ingest_key"])
            if after_source_review:
                reviewed_brief = plan_state.load_brief(SimpleNamespace(generation_meta=snap.generation_meta))
        elif after_source_review:
            ctx.log("The source was read again since you checked it, so your corrections could not be applied; the "
                    "concepts are extracted again.", "warning")
    if overrides is not None and overrides.excluded_chunk_ids:
        ingest = source_review.without_chunks(full_ingest, overrides.excluded_chunk_ids)
        ctx.log(f"Leaving out the {len(full_ingest.chunks) - len(ingest.chunks)} part(s) of the source you set aside.")
    review_source = payload.get("review_source") is True and not resume

    # 2 concept brief + plan ------------------------------------------------------------
    clock.start("plan")
    lo, hi = PIPELINE_PROGRESS["plan"]
    teacher = teacher_meta(project, ingest)
    ckpt = StageCheckpoints(ctx)  # a retry of this job reuses the LLM stages it already paid for
    inputs = _stage_inputs(ctx, vid, base, options, meta, teacher, source)
    if overrides is not None:  # absent otherwise: every other generation keeps its stage digests
        inputs["source_overrides"] = overrides.model_dump(mode="json", exclude={"updated_at"})
    plan_resume = bool(resume) and not after_source_review  # a source review comes before any plan
    plan = plan_state.load_plan(SimpleNamespace(generation_meta=snap.generation_meta, id=vid)) if plan_resume else None
    seed = plan_state.load_lexicon_seed(SimpleNamespace(generation_meta=snap.generation_meta)) if plan_resume else []
    brief: ConceptBrief | None = plan_state.load_brief(SimpleNamespace(generation_meta=snap.generation_meta)) if plan else None
    if plan is None:
        await _ensure_current(ctx, vid, base, "planning")
        plan_in = stage_digest("plan", inputs)
        reused = _plan_from(await ckpt.load("plan", plan_in))
        if reused is not None:
            brief, plan, seed, notes = reused
            _reused(ctx, ckpt, "plan", "the concept brief and the plan")
        else:
            ctx.progress("plan", lo, "Extracting the concepts to teach")
            if reviewed_brief is not None:
                brief = reviewed_brief  # the concepts the teacher checked
            else:
                brief = await _concept_brief(ctx, ingest, options)
            if brief is not None and overrides is not None:
                brief = source_review.effective_brief(brief, full_ingest, overrides)
            if brief is not None:
                aside = len(brief.skipped_chunks)
                ctx.log(f"Found {len(brief.concepts)} concepts to teach"
                        + (f"; set aside {aside} section(s) that hold no teaching content." if aside else "."))
            if review_source:
                await _pause_for_source_review(ctx, vid, base, meta, brief, options, clock, source.id,
                                               lo + (hi - lo) / 6)
            ctx.check_cancelled()
            ctx.progress("plan", lo + (hi - lo) / 3, "Planning the lecture")
            result = await _plan(ctx, ingest, options, teacher, brief)
            plan, seed, notes = result.plan, result.lexicon, result.notes[:50]
            await ckpt.save("plan", plan_in, _plan_data(brief, plan, seed, notes))
        holder = SimpleNamespace(generation_meta=meta)
        plan_state.save_brief(holder, brief)
        meta = holder.generation_meta
        meta["plan_notes"] = notes
        if options.review_plan:
            holder = SimpleNamespace(generation_meta=meta)
            plan_state.save_plan(holder, plan, lexicon=seed)
            meta = holder.generation_meta
            meta.update(models={"plan": plan_model(options, settings), "brief": brief_model(options, settings),
                                "provider": integrations.llm_engine(options, settings)},
                        options=options.model_dump(mode="json"))
            clock.stop()
            meta["stage_seconds"] = clock.durations
            if not await _cas(ctx, vid, {"revision": base}, {"status": "awaiting_review", "generation_meta": meta}):
                raise Conflict("revision changed during planning")
            ctx.progress("plan", hi, "Plan ready for review")
            raise AwaitingReview("The lecture plan is ready for review.", state={
                "stage": "plan_review", "version_id": vid, "base_revision": base, "source_document_id": source.id,
            })
    lexicon = plan_state.lexicon_for_plan(plan, seed)
    n_scenes = len(plan.all_scenes())
    ctx.progress("plan", hi, f"Planned {n_scenes} scenes in {len(plan.chapters)} chapters")
    ctx.check_cancelled()

    # 3 script -----------------------------------------------------------------------
    clock.start("script")
    info = SourceInfo(filename=source.filename, mime=source.mime, pages=ingest.pages or source.page_count, sha256=source.sha256)
    await _ensure_current(ctx, vid, base, "writing the scenes")
    script_in = stage_digest("script", inputs, _dump(plan), [e.model_dump(mode="json") for e in lexicon], _dump(brief),
                             info.model_dump(mode="json"))
    script_data = await ckpt.load("script", script_in)
    script = _script_from(script_data)
    if script is None:
        scenes_ckpt = SceneCheckpoints(ckpt, script_in)  # an attempt that stopped mid-way keeps its finished scenes
        script = await write_scenes_detailed(ctx, plan, ingest, options, lexicon=lexicon, source=info,
                                             progress=PIPELINE_PROGRESS["script"], brief=brief,
                                             checkpoints=scenes_ckpt)
        if scenes_ckpt.reused:
            ctx.log(f"Reusing {len(scenes_ckpt.reused)} of the {len(script.screenplay.scenes)} scenes written by this "
                    "job's previous attempt (not generated again).", stage="script")
        script_data = _script_data(script)
        await ckpt.save("script", script_in, script_data)
    else:
        _reused(ctx, ckpt, "script", f"the {len(script.screenplay.scenes)} written scenes")
    sp = script.screenplay
    plan = script.plan or plan  # clamped to the Screenplay limits
    ctx.check_cancelled()

    # 4 validate: lint -> critic -> repair ---------------------------------------------
    clock.start("validate")
    lo, hi = PIPELINE_PROGRESS["validate"]
    ctx.progress("validate", lo, "Checking the script")
    await _ensure_current(ctx, vid, base, "checking the script")
    validate_in = stage_digest("validate", inputs, script_data)
    rep_data = await ckpt.load("validate", validate_in)
    rep = _repair_from(rep_data)
    if rep is None:
        critic_issues = await critique(ctx, sp, ingest, options, brief=brief)
        rep = await repair_detailed(ctx, sp, _lint(sp, options, ingest) + critic_issues + script.issues, ingest,
                                    options, plan=plan, progress=(lo, hi), brief=brief)
        rep_data = _repair_data(rep)
        await ckpt.save("validate", validate_in, rep_data)
    else:
        _reused(ctx, ckpt, "validate", "the reviewed and repaired script")
    sp, issues = rep.screenplay, rep.issues
    meta["repaired_scenes"] = rep.repaired_scene_ids
    ctx.progress("validate", hi, f"Repaired {len(rep.repaired_scene_ids)} scene(s)")
    ctx.check_cancelled()

    # 5 companion ---------------------------------------------------------------------
    clock.start("companion")
    lo, hi = PIPELINE_PROGRESS["companion"]
    ctx.progress("companion", lo, "Writing the companion sheet")
    companion_in = stage_digest("companion", inputs, rep_data)
    sheet = _sheet_from(await ckpt.load("companion", companion_in))
    if sheet is None:
        sheet = await build_sheet(ctx, sp, ingest, options, brief=brief)
        await ckpt.save("companion", companion_in, {"sheet": sheet.model_dump(mode="json")})
    else:
        _reused(ctx, ckpt, "companion", "the companion sheet")
    sp = sp.model_copy(update={"companion_sheet": sheet})
    ctx.progress("companion", hi, "Companion sheet ready")

    # write screenplay ------------------------------------------------------------------
    clock.stop()
    new_rev = base + 1
    meta.pop(plan_state.PLAN_KEY, None)  # the plan is only kept while awaiting review
    meta.pop(plan_state.LEXICON_KEY, None)
    meta.update(
        options=options.model_dump(mode="json"),
        models={"brief": brief_model(options, settings), "plan": plan_model(options, settings),
                "script": script_model(options, settings), "critic": critic_model(settings, options),
                "provider": integrations.llm_engine(options, settings)},
        prompt_versions=prompt_versions(),
        stage_seconds=clock.durations,
        cost_usd=_cost(ctx),
        fallback_scenes=script.fallback_scene_ids,
        **{WRITER_KEY: writer_mark(ctx, new_rev)},
    )
    if ckpt.reused:
        meta["reused_stages"] = list(ckpt.reused)  # LLM stages taken from this job's previous attempt
    else:
        meta.pop("reused_stages", None)
    document = sp.model_dump(mode="json")
    await _record_generated_snapshot(ctx, meta, document)
    ok = await _cas(ctx, vid, {"revision": base}, {
        "screenplay": document, "revision": new_rev, "status": "building", "language": sp.language,
        "generation_meta": meta, **_issues_values(issues, hidden_ids(sp)),
    })
    if not ok:
        raise Conflict("revision changed before the screenplay was saved")

    # 6 assets + 7 timeline ---------------------------------------------------------------
    def update(m: dict[str, Any], build: Any, c: StageClock) -> None:
        m.update(stage_seconds=c.durations, cost_usd=_cost(ctx), assets={"built": len(build.built), "reused": len(build.reused)})

    a_lo, _ = PIPELINE_PROGRESS["assets"]
    t_lo, _ = PIPELINE_PROGRESS["timeline"]
    final = await _build_and_finish(ctx, vid, sp, options, new_rev, issues, meta, clock, previous=None,
                                    progress=(a_lo, t_lo), done_message="Lecture ready", meta_update=update)
    await _db(ctx, dbops.set_current_version_if_unset, snap.project_id, vid)
    return {"version_id": vid, "issue_counts": _counts(final, hidden_ids(sp))}


# ---------------------------------------------------------------------------
# regenerate_scene
# ---------------------------------------------------------------------------


@job_handler("regenerate_scene")
async def regenerate_scene(ctx: Any) -> dict[str, Any]:
    """Rewrite one scene (teacher instructions), keep its id, then rebuild stale assets + timeline."""
    vid = _require_version(ctx)
    snap = await _db(ctx, dbops.load_version, vid)
    if snap is None:
        raise FatalJobError("version not found", code="not_found")
    restore = "ready" if snap.built_revision is not None else "failed"
    return await _guard(ctx, vid, lambda: _regenerate(ctx, vid, snap), on_fail_status=restore,
                        restore_statuses=("generating", "building"))


async def _regenerate(ctx: Any, vid: int, snap: dbops.VersionSnapshot) -> dict[str, Any]:
    payload = dict(ctx.payload or {})
    scene_id = str(payload.get("scene_id") or "")
    instructions = str(payload.get("instructions") or "")[:4000]
    base = int(payload.get("base_revision") or snap.revision)
    if snap.revision != base:
        if written_by_this_job(ctx, snap, base):
            await _resume_build(ctx, vid, snap, progress=(0.45, 0.95), done_message="Scene regenerated")
            return {"version_id": vid, "scene_id": scene_id}
        raise Conflict(f"revision {snap.revision} != base {base}")
    sp = snap.get_screenplay()
    if sp is None:
        raise FatalJobError("this version has no screenplay yet", code="bad_request")
    try:
        old_scene = sp.scene_by_id(scene_id)
    except KeyError as exc:
        raise FatalJobError(f"scene {scene_id!r} not found", code="not_found") from exc
    project = await _db(ctx, dbops.load_project, snap.project_id)
    options = options_for(sp, version_options(snap.generation_meta, project))
    if not await _cas(ctx, vid, {"revision": base}, {"status": "generating"}):
        raise Conflict("revision changed before start")
    clock = StageClock()
    clock.start("script")
    ctx.progress("script", 0.05, "Rewriting the scene")
    ingest = await _load_ingest(ctx, snap.generation_meta, snap.project_id)
    ingest = _teacher_scoped(ingest, snap.generation_meta)  # parts set aside in a source review stay out
    brief = await _version_brief(ctx, snap)  # its set-aside chunks stay out of the rewrite's context
    old_issues = _parse_issues(snap.issues)
    scene_issues = [i for i in old_issues if i.scene_id == scene_id and i.source in ("lint", "critic")]
    new_scene, _ = await rewrite_scene(ctx, sp, scene_id, scene_issues, ingest, options, instructions=instructions,
                                       brief=brief)
    if new_scene is None:
        raise FatalJobError("The scene could not be regenerated; please try again with different instructions.",
                            code="generation_failed")
    sp2 = replace_scenes(sp, {scene_id: new_scene})
    errors = [i for i in _lint(sp2, options, ingest) if i.scene_id == scene_id and i.severity == "error"]
    if errors:  # one repair round for the new scene
        ctx.progress("validate", 0.3, "Fixing the rewritten scene")
        fixed, _ = await rewrite_scene(ctx, sp2, scene_id, errors, ingest, options, instructions=instructions,
                                       brief=brief)
        if fixed is not None:
            sp2 = replace_scenes(sp2, {scene_id: fixed})
    clock.start("validate")
    ctx.check_cancelled()
    critic_issues = await critique(ctx, sp2, ingest, options, scene_ids={scene_id}, brief=brief)
    kept = [i for i in old_issues if i.source != "lint" and i.scene_id != scene_id]
    issues = _lint(sp2, options, ingest) + kept + critic_issues
    meta = dict(snap.generation_meta)
    meta.pop("error", None)
    history = dict(meta.get("scene_history") or {})
    entry = {"at": _now(), "instructions": instructions, "revision": base, "scene": old_scene.model_dump(mode="json")}
    history[scene_id] = ([entry] + list(history.get(scene_id) or []))[:SCENE_HISTORY_LIMIT]
    meta["scene_history"] = history
    new_rev = base + 1
    meta[WRITER_KEY] = writer_mark(ctx, new_rev)
    clock.stop()
    if not await _cas(ctx, vid, {"revision": base}, {
        "screenplay": sp2.model_dump(mode="json"), "revision": new_rev, "status": "building", "generation_meta": meta,
        **_issues_values(issues, hidden_ids(sp2)),
    }):
        raise Conflict("revision changed before the scene was saved")

    def update(m: dict[str, Any], build: Any, c: StageClock) -> None:
        regen = list(m.get("regenerations") or [])
        regen.append({"scene_id": scene_id, "at": _now(), "stage_seconds": c.durations, "cost_usd": _cost(ctx)})
        m["regenerations"] = regen[-20:]

    await _build_and_finish(ctx, vid, sp2, options, new_rev, issues, meta, clock, previous=snap.get_manifest(),
                            progress=(0.45, 0.95), done_message="Scene regenerated", meta_update=update)
    return {"version_id": vid, "scene_id": scene_id}


# ---------------------------------------------------------------------------
# build_assets
# ---------------------------------------------------------------------------


@job_handler("build_assets")
async def build_assets_job(ctx: Any) -> dict[str, Any]:
    """(Re)build assets for stale/selected scenes and the timeline of an edited version."""
    vid = _require_version(ctx)
    snap = await _db(ctx, dbops.load_version, vid)
    if snap is None:
        raise FatalJobError("version not found", code="not_found")
    restore = "ready" if snap.built_revision is not None else "failed"
    return await _guard(ctx, vid, lambda: _build(ctx, vid, snap), on_fail_status=restore, restore_statuses=("building",))


async def _build(ctx: Any, vid: int, snap: dbops.VersionSnapshot) -> dict[str, Any]:
    payload = dict(ctx.payload or {})
    base = int(payload.get("base_revision") or snap.revision)
    if snap.revision != base:
        raise Conflict(f"revision {snap.revision} != base {base}")
    sp = snap.get_screenplay()
    if sp is None:
        raise FatalJobError("this version has no screenplay to build", code="bad_request")
    project = await _db(ctx, dbops.load_project, snap.project_id)
    options = options_for(sp, version_options(snap.generation_meta, project))
    scene_ids = payload.get("scene_ids")
    if scene_ids is not None:
        known = {s.id for s in sp.scenes}
        scene_ids = [str(s) for s in scene_ids if str(s) in known]
    if not await _cas(ctx, vid, {"revision": base}, {"status": "building"}):
        raise Conflict("revision changed before start")
    clock = StageClock()
    clock.start("assets")
    ctx.progress("assets", 0.02, "Building assets")
    build = await _build_assets_guarded(ctx, vid, base, sp, options, previous=snap.get_manifest(),
                                        scene_ids=scene_ids, progress=(0.02, 0.92))
    clock.start("timeline")
    ctx.progress("timeline", 0.93, "Building the timeline")
    timeline, tl_issues = await _timeline(ctx, sp, build.manifest, vid, base)
    clock.stop()
    old = _parse_issues(snap.issues)
    issues = [i for i in old if i.source not in ASSET_SOURCES and i.code != "timeline.failed"]
    issues += merge_asset_issues(old, build.issues, set(build.built)) + tl_issues
    meta = dict(snap.generation_meta)
    meta.pop("error", None)
    meta["last_build"] = {"at": _now(), "built": build.built, "reused": len(build.reused),
                          "stage_seconds": clock.durations, "cost_usd": _cost(ctx)}
    # A scene-scoped build that reused changed scenes as they were is not a build of this revision.
    final = _final_values(build.manifest, timeline, base, issues, meta, complete=not build.manifest.stale_scenes,
                          hidden=hidden_ids(sp))
    if not await _cas(ctx, vid, {"revision": base}, final):
        raise Conflict("revision changed while assets were built")
    ctx.progress("timeline", 1.0, "Build complete")
    return {"version_id": vid}


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


@job_handler("translate")
async def translate_job(ctx: Any) -> dict[str, Any]:
    """Fill a new version with a translation of the source version, then build it."""
    vid = _require_version(ctx)
    return await _guard(ctx, vid, lambda: _translate(ctx, vid))


async def _translate(ctx: Any, vid: int) -> dict[str, Any]:
    payload = dict(ctx.payload or {})
    snap = await _db(ctx, dbops.load_version, vid)
    if snap is None:
        raise FatalJobError("version not found", code="not_found")
    base_raw = payload.get("base_revision")  # the API's translate payload has none: the new version is ours
    base = int(base_raw) if base_raw is not None else snap.revision
    if written_by_this_job(ctx, snap, None if base_raw is None else base):
        await _resume_build(ctx, vid, snap, progress=(0.45, 0.95), done_message="Translation ready")
        return {"version_id": vid}
    if snap.revision != base:
        raise Conflict(f"revision {snap.revision} != base {base}")
    src = await _db(ctx, dbops.load_version, int(payload.get("source_version_id") or 0))
    if src is None or src.project_id != snap.project_id:
        raise FatalJobError("source version not found", code="not_found")
    source_sp = src.get_screenplay()
    if source_sp is None:
        raise FatalJobError("the source version has no screenplay", code="bad_request")
    target = str(payload.get("target_language") or "")
    translate_board = bool(payload.get("translate_board"))
    project = await _db(ctx, dbops.load_project, snap.project_id)
    base_opts = version_options(src.generation_meta, project)
    try:
        options = GenerationOptions.model_validate({
            **base_opts.model_dump(mode="json"), "language": target,
            "board_language": target if translate_board else (source_sp.board_language or source_sp.language),
            "tts_voice": payload.get("tts_voice") or None,
        })
    except ValidationError as exc:
        raise FatalJobError(f"unsupported target language {target!r}", code="bad_request") from exc
    if not await _cas(ctx, vid, {"revision": base}, {"status": "generating"}):
        raise Conflict("revision changed before start")
    clock = StageClock()
    clock.start("translate")
    ckpt = StageCheckpoints(ctx)  # a retry of this job reuses the translation it already paid for
    translate_in = stage_digest("translate", {
        "version_id": vid, "base_revision": base, "source_version_id": src.id, "target": target,
        "translate_board": translate_board, "options": options.model_dump(mode="json"),
        "engine": integrations.llm_engine(options, ctx.settings), "model": translate_model(options, ctx.settings),
        "prompt_versions": prompt_versions(["translate"]),
    }, source_sp.model_dump(mode="json"))
    res = _translation_from(await ckpt.load("translate", translate_in))
    if res is None:
        res = await translate_screenplay(ctx, source_sp, target, translate_board=translate_board, progress=(0.02, 0.45),
                                         options=options)
        await ckpt.save("translate", translate_in, {"screenplay": _dump(res.screenplay),
                                                    "issues": [i.model_dump(mode="json") for i in res.issues]})
    else:
        _reused(ctx, ckpt, "translate", "the translation")
    sp = res.screenplay
    ingest = await _load_ingest(ctx, src.generation_meta, snap.project_id)
    issues = _lint(sp, options, ingest) + res.issues
    clock.stop()
    new_rev = base + 1
    meta = {
        "options": options.model_dump(mode="json"), "source_version_id": src.id,
        "source_document_id": src.generation_meta.get("source_document_id"),
        "translated_from": source_sp.language, "translate_board": translate_board,
        "models": {"translate": translate_model(options, ctx.settings),
                   "provider": integrations.llm_engine(options, ctx.settings)},
        "prompt_versions": prompt_versions(["translate"]), "stage_seconds": clock.durations, "cost_usd": _cost(ctx),
        WRITER_KEY: writer_mark(ctx, new_rev),
    }
    brief = await _version_brief(ctx, src)  # regenerating a scene here keeps set-aside chunks out too
    if brief is not None:
        meta[plan_state.BRIEF_KEY] = brief.model_dump(mode="json", exclude_defaults=True)
    document = sp.model_dump(mode="json")
    await _record_generated_snapshot(ctx, meta, document)
    if not await _cas(ctx, vid, {"revision": base}, {
        "screenplay": document, "revision": new_rev, "status": "building", "language": target,
        "generation_meta": meta, **_issues_values(issues, hidden_ids(sp)),
    }):
        raise Conflict("revision changed before the translation was saved")

    def update(m: dict[str, Any], build: Any, c: StageClock) -> None:
        m.update(stage_seconds=c.durations, cost_usd=_cost(ctx))

    await _build_and_finish(ctx, vid, sp, options, new_rev, issues, meta, clock, previous=None, progress=(0.45, 0.95),
                            done_message="Translation ready", meta_update=update)
    return {"version_id": vid}
