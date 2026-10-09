"""Run pipeline stages over eval fixtures and collect metrics.

Per source fixture: ingest -> brief -> plan -> script -> lint -> critic [-> repair -> lint] -> companion
[-> assets]. The concept brief is built and handed to planning, scene writing, the critic, repair and
the companion sheet exactly as the orchestrator does (so eval prompts never hold a chunk it set aside,
as production prompts never do): a brief that cannot be built (``BriefUnavailable``, a provider error) is recorded
and the lecture is planned directly from the source; its metrics (concepts, chunks cited / set aside
by reason, coverage) land under ``metrics["brief"]``. Per screenplay fixture (v2 JSON or v1 legacy):
[convert] -> lint [-> assets]. Every
stage is timed and its cost attributed; the first failing stage stops that fixture (metrics are
still computed for whatever screenplay exists). ``--timeout`` bounds each fixture: a stage still
running at the deadline is cancelled and reported as timed out (a synchronous stage running in a
worker thread cannot be interrupted; its result is discarded). Each fixture's output directory is
emptied first, so artifacts always belong to the current run. Stage functions are resolved from
``aadhi.pipeline`` by the names in ``aadhi/pipeline/base.py``; missing modules are reported, not
fatal, so the harness works while the pipeline is under construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select

from ..config import Settings
from ..jobs.base import BudgetExceeded, JobCancelled
from ..pipeline.base import ConceptBrief, GenerationOptions, IngestResult, Issue, LecturePlan
from ..schemas.manifest import AssetManifest
from ..schemas.screenplay import CompanionSheet, Screenplay
from ..storage.assets import Produced, bytes_key
from .context import EvalJobContext
from .fixtures import Fixture, FixtureError, discover_fixtures, resolve_options
from .metrics import MetricInputs, brief_metrics, compute_metrics, schema_check
from .workspace import ProviderMode, Workspace, isolated_workspace

logger = logging.getLogger(__name__)

HARNESS_VERSION = "1"
EVAL_USERNAME = "eval-harness"
STAGE_ORDER = ("convert", "ingest", "brief", "plan", "script", "lint", "critic", "repair", "relint", "companion",
               "assets")
REQUIRED_SOURCE_STAGES = ("ingest", "plan", "script")
MAX_ERROR_CHARS = 2000

# stage -> (module, attribute) as specified in aadhi/pipeline/base.py and docs/INTERFACES.md
STAGE_FUNCTIONS: dict[str, tuple[str, str]] = {
    "ingest": ("aadhi.pipeline.ingest", "ingest_source"),
    "brief": ("aadhi.pipeline.brief", "build_brief"),
    "plan": ("aadhi.pipeline.plan", "make_plan"),
    "script": ("aadhi.pipeline.script", "write_scenes"),
    "lint": ("aadhi.pipeline.validate", "lint"),
    "critic": ("aadhi.pipeline.critic", "critique"),
    "repair": ("aadhi.pipeline.repair", "repair"),
    "companion": ("aadhi.pipeline.companion", "build_sheet"),
    "assets": ("aadhi.pipeline.assets", "build_assets"),
}
HELPER_FUNCTIONS: dict[str, tuple[str, str]] = {
    "render_markdown": ("aadhi.pipeline.companion", "render_markdown"),
    "register_fakes": ("aadhi.pipeline.fake_content", "register_fake_responders"),
    "is_legacy": ("aadhi.legacy", "is_legacy"),
    "convert_legacy": ("aadhi.legacy", "convert_legacy"),
    "template_registry": ("aadhi.manim.templates", "registry"),
}

StageStatus = Literal["ok", "failed", "skipped", "unavailable"]
FixtureStatus = Literal["ok", "failed", "skipped"]


# --- stage resolution ----------------------------------------------------------------------------


def import_attr(module: str, attr: str) -> tuple[Any | None, str | None]:
    """``(object, None)`` or ``(None, reason)``; never raises for missing/broken modules."""
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        return None, f"{module} not importable (missing module {exc.name})"
    except Exception as exc:  # a half-written module must not take the harness down
        return None, f"{module} failed to import: {type(exc).__name__}: {str(exc)[:300]}"
    obj = getattr(mod, attr, None)
    if obj is None:
        return None, f"{module}.{attr} not found"
    return obj, None


@dataclass
class PipelineStages:
    """Callables for every stage (``None`` when unavailable) plus optional helpers."""

    functions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    helpers: dict[str, Any] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)

    def get(self, stage: str) -> Callable[..., Any] | None:
        return self.functions.get(stage)

    def helper(self, name: str) -> Any | None:
        return self.helpers.get(name)

    def known_templates(self) -> list[str] | None:
        registry = self.helpers.get("template_registry")
        return sorted(registry) if isinstance(registry, dict) else None


def load_stages() -> PipelineStages:
    """Resolve the pipeline functions (call after the workspace environment is in place)."""
    stages = PipelineStages()
    for stage, (module, attr) in STAGE_FUNCTIONS.items():
        fn, reason = import_attr(module, attr)
        if fn is None:
            stages.missing[stage] = reason or "unavailable"
        else:
            stages.functions[stage] = fn
    for name, (module, attr) in HELPER_FUNCTIONS.items():
        obj, _reason = import_attr(module, attr)
        if obj is not None:
            stages.helpers[name] = obj
    return stages


# --- results -------------------------------------------------------------------------------------


@dataclass
class StageRecord:
    name: str
    status: StageStatus
    seconds: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class FixtureResult:
    name: str
    source: str
    kind: str
    status: FixtureStatus = "ok"
    failed_stage: str | None = None
    error: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    llm_engine: str | None = None  # the AI engine this fixture's LLM stages used (options, else LLM_PROVIDER)
    stages: list[StageRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    seconds: float = 0.0
    cost_usd: float = 0.0  # everything this fixture spent, including stages that failed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StageFailed(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


class StageTimedOut(Exception):
    """The fixture deadline passed while a stage was running."""


class StageUnavailable(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


@dataclass
class RunConfig:
    """Options of one ``python -m aadhi.evals run`` invocation."""

    fixtures_dir: Path
    out_dir: Path
    provider: ProviderMode = "fake"
    assets: bool = False
    repair: bool = False
    only: tuple[str, ...] = ()
    option_overrides: dict[str, Any] = field(default_factory=dict)
    budget_usd: float | None = None  # per fixture; default MAX_COST_PER_LECTURE_USD
    timeout_s: float | None = None  # per fixture wall clock
    keep_workspace: bool = False
    render_manim: bool = False

    @property
    def name(self) -> str:
        return self.out_dir.name

    def describe(self) -> dict[str, Any]:
        return {
            "fixtures_dir": self.fixtures_dir.as_posix(),
            "provider": self.provider,
            "assets": self.assets,
            "repair": self.repair,
            "only": list(self.only),
            "option_overrides": self.option_overrides,
            "budget_usd": self.budget_usd,
            "timeout_s": self.timeout_s,
            "render_manim": self.render_manim,
        }


@dataclass
class _State:
    ingest: IngestResult | None = None
    brief: ConceptBrief | None = None
    brief_attempted: bool = False  # the brief stage ran (its metrics are reported even when it failed)
    plan: LecturePlan | None = None
    screenplay: Screenplay | None = None
    draft: Screenplay | None = None
    lint_issues: list[Any] = field(default_factory=list)
    critic_issues: list[Any] = field(default_factory=list)
    issues_before_repair: list[Any] = field(default_factory=list)
    manifest: AssetManifest | None = None
    raw: Any = None  # screenplay fixture JSON


# --- helpers -----------------------------------------------------------------------------------


def describe_error(exc: BaseException, settings: Settings) -> str:
    """One-line, redacted, bounded description of an exception."""
    text = f"{type(exc).__name__}: {exc}".replace("\r", " ").strip()
    return settings.redact(text)[:MAX_ERROR_CHARS]


def _coerce(value: Any, model: type[BaseModel]) -> Any:
    if value is None or isinstance(value, model):
        return value
    if isinstance(value, dict):
        return model.model_validate(value)
    return value


def _issue_list(value: Any) -> list[Any]:
    return [Issue.model_validate(i) if isinstance(i, dict) else i for i in (value or [])]


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_dump(v) for v in value]
    return value


def _write_json(path: Path, data: Any) -> None:
    # default=str: event/usage payloads are free-form (sets, paths, enums); never lose a run over them.
    text = json.dumps(_dump(data), indent=2, ensure_ascii=False, sort_keys=False, default=str)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _reset_dir(path: Path) -> None:
    """Empty ``path`` (artifacts of an earlier run into the same --out) and recreate it. Blocking."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Await async stage functions; run sync ones in a worker thread (they may do I/O)."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    value = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(value):
        value = await value
    return value


def accepts_kwarg(fn: Callable[..., Any] | None, name: str) -> bool:
    """True if ``fn`` takes a keyword argument ``name`` (optional extras such as lint's ``chunk_ids``)."""
    if fn is None:
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _lint_kwargs(fn: Callable[..., Any] | None, ingest: IngestResult | None) -> dict[str, Any]:
    if ingest is not None and accepts_kwarg(fn, "chunk_ids"):
        return {"chunk_ids": {c.id for c in ingest.chunks}}
    return {}


class _Recorder:
    """Runs stages for one fixture and records timing, cost and failures."""

    def __init__(
        self, ctx: EvalJobContext, result: FixtureResult, stages: PipelineStages, timeout_s: float | None = None
    ) -> None:
        self.ctx = ctx
        self.result = result
        self.stages = stages
        self.timeout_s = timeout_s

    async def run(
        self,
        name: str,
        *args: Any,
        required: bool = True,
        fn: Callable[..., Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        fn = fn or self.stages.get(name)
        if fn is None:
            reason = self.stages.missing.get(name, "unavailable")
            self.result.stages.append(StageRecord(name, "unavailable", error=reason))
            if required:
                raise StageUnavailable(name, reason)
            return None
        self.ctx.set_stage(name)
        cost0 = self.ctx.cost_usd
        t0 = time.perf_counter()
        try:
            self.ctx.check_cancelled()  # deadline / cancel before the stage starts
            value = await self._bounded(fn, *args, **(kwargs or {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = describe_error(exc, self.ctx.settings)
            self.result.stages.append(
                StageRecord(name, "failed", round(time.perf_counter() - t0, 3), round(self.ctx.cost_usd - cost0, 6),
                            message)
            )
            logger.debug("eval stage %s failed", name, exc_info=True)
            raise StageFailed(name, message) from exc
        self.result.stages.append(
            StageRecord(name, "ok", round(time.perf_counter() - t0, 3), round(self.ctx.cost_usd - cost0, 6))
        )
        return value

    async def _bounded(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one stage, cancelling it when the fixture deadline passes."""
        deadline = self.ctx.deadline
        if deadline is None:
            return await _call(fn, *args, **kwargs)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            async with asyncio.timeout(remaining) as scope:
                return await _call(fn, *args, **kwargs)
        except TimeoutError as exc:
            if scope.expired():  # our deadline, not a timeout raised inside the stage
                limit = f" after {self.timeout_s:g}s" if self.timeout_s else ""
                raise StageTimedOut(f"eval fixture timed out{limit} while this stage was running") from exc
            raise

    def skip(self, name: str, reason: str) -> None:
        self.result.stages.append(StageRecord(name, "skipped", error=reason))


# --- database rows -------------------------------------------------------------------------------


@dataclass
class _Rows:
    user_id: int
    project_id: int
    version_id: int
    source_doc: Any | None  # detached SourceDocument (expire_on_commit=False)


def _create_rows(ws: Workspace, fixture: Fixture, options: GenerationOptions, store: Any) -> _Rows:
    """User + project + version (+ stored source document) for one fixture. Blocking: run in a thread."""
    from ..models import AssetRef, Project, ProjectVersion, SourceDocument, User

    asset = None
    data = b""
    if fixture.kind == "source":
        data = fixture.path.read_bytes()
        asset = store.put(bytes_key("source", data), "source", Produced(data=data, mime=fixture.mime))
    with ws.session_factory() as db:
        user = db.execute(select(User).where(User.username == EVAL_USERNAME)).scalar_one_or_none()
        if user is None:
            # "!" is not a bcrypt hash, so this account can never log in.
            user = User(username=EVAL_USERNAME, password_hash="!", role="editor")  # noqa: S106
            db.add(user)
            db.flush()
        project = Project(
            owner_id=user.id,
            title=fixture.name,
            subject_name=options.subject_name or "",
            unit_name=options.unit_name or "",
            session_number=options.session_number or "",
            session_title=options.session_title or "",
            language=options.language,
            settings=options.model_dump(mode="json"),
            next_version_number=2,
        )
        db.add(project)
        db.flush()
        version = ProjectVersion(
            project_id=project.id, number=1, status="generating", language=options.language, created_by=user.id
        )
        db.add(version)
        db.flush()
        project.current_version_id = version.id
        source = None
        if asset is not None:
            source = SourceDocument(
                project_id=project.id,
                filename=fixture.path.name,
                mime=fixture.mime,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=asset.storage_key,
            )
            db.add(source)
            db.add(AssetRef(project_id=project.id, asset_key=asset.key))
        db.commit()
        return _Rows(user.id, project.id, version.id, source)


def _save_version(ws: Workspace, version_id: int, state: _State, ok: bool) -> None:
    """Store the outcome on the eval version (inspectable with --keep-workspace). Blocking."""
    from ..models import ProjectVersion

    with ws.session_factory() as db:
        version = db.get(ProjectVersion, version_id)
        if version is None:  # pragma: no cover - created above
            return
        version.set_screenplay(state.screenplay)
        version.set_manifest(state.manifest)
        version.set_issues([*state.lint_issues, *state.critic_issues])
        version.status = "draft" if ok and state.screenplay is not None else "failed"
        db.commit()


# --- pipelines -----------------------------------------------------------------------------------


def _brief_kwargs(fn: Callable[..., Any] | None, brief: ConceptBrief | None) -> dict[str, Any]:
    """``{"brief": brief}`` when there is one and the stage takes it (as the orchestrator's ``_plan``)."""
    return {"brief": brief} if brief is not None and accepts_kwarg(fn, "brief") else {}


async def _brief_stage(rec: _Recorder, state: _State, options: GenerationOptions) -> None:
    """The concept brief, as the orchestrator builds it: an aid that never fails the lecture.

    ``BriefUnavailable`` (too little text, no concepts) is recorded as a skipped stage, any other error as a
    failed stage plus a warning; planning then works from the source. A deadline, a cancellation or the
    budget still stops the fixture.
    """
    try:
        value = await rec.run("brief", rec.ctx, state.ingest, options, required=False)
    except StageFailed as exc:
        cause = exc.__cause__
        if isinstance(cause, StageTimedOut | BudgetExceeded | JobCancelled):
            raise
        state.brief_attempted = True
        record = rec.result.stages[-1]
        if type(cause).__name__ == "BriefUnavailable":
            record.status = "skipped"
        rec.result.warnings.append(f"concept brief not built ({exc.message[:300]}); planned directly from the source")
        return
    if rec.stages.get("brief") is not None:
        state.brief_attempted = True
        state.brief = _coerce(value, ConceptBrief)


async def _source_pipeline(
    rec: _Recorder, state: _State, cfg: RunConfig, options: GenerationOptions, rows: _Rows
) -> None:
    ctx = rec.ctx
    state.ingest = _coerce(await rec.run("ingest", ctx, rows.source_doc), IngestResult)
    await _brief_stage(rec, state, options)
    plan_kw = _brief_kwargs(rec.stages.get("plan"), state.brief)
    state.plan = _coerce(await rec.run("plan", ctx, state.ingest, options, kwargs=plan_kw), LecturePlan)
    script_kw = _brief_kwargs(rec.stages.get("script"), state.brief)
    state.screenplay = _coerce(await rec.run("script", ctx, state.plan, state.ingest, options, kwargs=script_kw),
                               Screenplay)
    lint_fn = rec.stages.get("lint")
    lint_kw = _lint_kwargs(lint_fn, state.ingest)
    state.lint_issues = _issue_list(await rec.run("lint", state.screenplay, options, required=False, kwargs=lint_kw))
    critic_kw = _brief_kwargs(rec.stages.get("critic"), state.brief)
    state.critic_issues = _issue_list(
        await rec.run("critic", ctx, state.screenplay, state.ingest, options, required=False, kwargs=critic_kw)
    )
    if cfg.repair:
        state.issues_before_repair = [*state.lint_issues, *state.critic_issues]
        repair_kw = _brief_kwargs(rec.stages.get("repair"), state.brief)
        repaired = await rec.run(
            "repair", ctx, state.screenplay, state.issues_before_repair, state.ingest, options, required=False,
            kwargs=repair_kw,
        )
        if repaired is not None:
            # Only lint is re-run: a second critic pass would double the LLM cost of every eval.
            state.draft, state.screenplay = state.screenplay, _coerce(repaired, Screenplay)
            relint = await rec.run("relint", state.screenplay, options, required=False, fn=lint_fn, kwargs=lint_kw)
            state.lint_issues = _issue_list(relint)
    companion_kw = _brief_kwargs(rec.stages.get("companion"), state.brief)
    sheet = await rec.run("companion", ctx, state.screenplay, state.ingest, options, required=False,
                          kwargs=companion_kw)
    sheet = _coerce(sheet, CompanionSheet)
    if isinstance(sheet, CompanionSheet):
        state.screenplay = state.screenplay.model_copy(update={"companion_sheet": sheet})
    if cfg.assets:
        manifest = await rec.run("assets", ctx, state.screenplay, options, required=False)
        state.manifest = _coerce(manifest, AssetManifest)


def _looks_legacy(data: Any) -> bool:
    return isinstance(data, list) or (isinstance(data, dict) and "schema_version" not in data and "scenes" in data)


async def _screenplay_pipeline(
    rec: _Recorder, state: _State, cfg: RunConfig, options: GenerationOptions, fixture: Fixture
) -> None:
    raw = json.loads(await asyncio.to_thread(fixture.path.read_text, encoding="utf-8"))
    state.raw = raw
    is_legacy = rec.stages.helper("is_legacy")
    convert = rec.stages.helper("convert_legacy")
    legacy = is_legacy(raw) if callable(is_legacy) else _looks_legacy(raw)
    if legacy:
        if not callable(convert):
            rec.result.stages.append(StageRecord("convert", "unavailable", error="aadhi.legacy not importable"))
            raise StageUnavailable("convert", "aadhi.legacy not importable (needed for v1 lectures)")
        converted = await rec.run("convert", raw, fn=convert)
        sp, warnings = converted if isinstance(converted, tuple) else (converted, [])
        rec.result.warnings.extend(str(w) for w in warnings)
        state.screenplay = _coerce(sp, Screenplay)
    else:
        state.screenplay = await rec.run("convert", raw, fn=Screenplay.model_validate)
    state.lint_issues = _issue_list(await rec.run("lint", state.screenplay, options, required=False))
    rec.skip("critic", "screenplay fixture has no source document")
    if cfg.assets:
        manifest = await rec.run("assets", rec.ctx, state.screenplay, options, required=False)
        state.manifest = _coerce(manifest, AssetManifest)


# --- per fixture -----------------------------------------------------------------------------------


async def _write_artifacts(out: Path, result: FixtureResult, state: _State, ctx: EvalJobContext | None,
                           stages: PipelineStages) -> None:
    """Write every artifact that exists; a file that cannot be written becomes a warning."""
    files: dict[str, Any] = {
        "ingest.json": state.ingest,
        "brief.json": state.brief,
        "plan.json": state.plan,
        "screenplay.draft.json": state.draft,
        "screenplay.json": state.screenplay,
        "manifest.json": state.manifest,
    }
    issues = [*state.lint_issues, *state.critic_issues]
    if state.screenplay is not None or issues:
        files["issues.json"] = issues
    if state.issues_before_repair:
        files["issues.before_repair.json"] = state.issues_before_repair
    if ctx is not None:
        files["events.json"] = ctx.events
        files["usage.json"] = [
            {**asdict(r.usage), "cost_usd": r.cost_usd, "stage": r.stage} for r in ctx.usage_records
        ]
    files["metrics.json"] = result.metrics
    for name, value in files.items():
        if value is None:
            continue
        try:
            await asyncio.to_thread(_write_json, out / name, value)
        except Exception as exc:  # one bad artifact must not lose the run's report
            result.warnings.append(f"{name} not written: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        result.artifacts.append(name)
    render_md = stages.helper("render_markdown")
    if state.screenplay is not None and callable(render_md):
        try:
            md = render_md(state.screenplay)
            await asyncio.to_thread(_write_text, out / "companion.md", md)
            result.artifacts.append("companion.md")
        except Exception as exc:  # companion rendering is a convenience, not a metric
            result.warnings.append(f"companion.md not written: {type(exc).__name__}")


def _metrics(
    state: _State, ctx: EvalJobContext | None, options: GenerationOptions | None, stages: PipelineStages
) -> dict[str, Any]:
    if state.screenplay is None:
        return {"schema": schema_check(state.raw)} if state.raw is not None else {}
    extra: dict[str, Any] = {}
    if state.issues_before_repair:
        extra["repair"] = {
            "issues_before": len(state.issues_before_repair),
            "errors_before": sum(1 for i in state.issues_before_repair if getattr(i, "severity", "") == "error"),
            "issues_after": len(state.lint_issues) + len(state.critic_issues),  # relint + pre-repair critic
        }
    if state.ingest is not None:
        extra["source"] = {
            "pages": state.ingest.pages,
            "chunks": len(state.ingest.chunks),
            "figures": len(state.ingest.figures),
            "chars": len(state.ingest.markdown),
            "truncated": state.ingest.truncated,
            "attach_original": state.ingest.attach_original,
        }
        if state.brief_attempted:
            extra["brief"] = brief_metrics(state.brief, [c.id for c in state.ingest.chunks])
    inputs = MetricInputs(
        issues=[*state.lint_issues, *state.critic_issues],
        target_minutes=options.target_minutes if options else None,
        chunk_ids=[c.id for c in state.ingest.chunks] if state.ingest is not None else None,
        manifest=state.manifest,
        usage=ctx.usage_records if ctx else None,
        pricing_available=bool(ctx and ctx.meter and ctx.meter.pricing_available),
        known_templates=stages.known_templates(),
        extra=extra,
    )
    return compute_metrics(state.screenplay, inputs)


def _llm_engine(options: GenerationOptions, settings: Settings) -> str:
    """The AI engine a fixture's LLM stages use (the pipeline's own rule, ``integrations.llm_engine``)."""
    from ..pipeline.integrations import llm_engine

    return llm_engine(options, settings)


async def run_fixture(
    fixture: Fixture,
    cfg: RunConfig,
    ws: Workspace,
    stages: PipelineStages,
    *,
    echo: Callable[[str, dict[str, Any]], None] | None = None,
) -> FixtureResult:
    """Run one fixture end to end; never raises for pipeline failures (they are recorded)."""
    started = time.perf_counter()
    result = FixtureResult(name=fixture.name, source=fixture.path.name, kind=fixture.kind)
    out = cfg.out_dir / fixture.name
    try:
        await asyncio.to_thread(_reset_dir, out)
    except OSError as exc:  # artifact writes will fail and be reported as warnings
        result.warnings.append(f"output directory not reset: {type(exc).__name__}: {str(exc)[:200]}")
    state = _State()
    ctx: EvalJobContext | None = None
    options: GenerationOptions | None = None
    try:
        options = resolve_options(fixture, cfg.option_overrides)
        result.options = options.model_dump(mode="json", exclude_defaults=True)
        result.llm_engine = _llm_engine(options, ws.settings)
        if fixture.kind == "source":
            missing = [s for s in REQUIRED_SOURCE_STAGES if stages.get(s) is None]
            if missing:
                raise StageUnavailable(missing[0], stages.missing.get(missing[0], "unavailable"))
        store = ws.asset_store()
        rows = await asyncio.to_thread(_create_rows, ws, fixture, options, store)
        ctx = EvalJobContext(
            settings=ws.settings,
            assets=store,
            session_factory=ws.session_factory,
            user_id=rows.user_id,
            project_id=rows.project_id,
            version_id=rows.version_id,
            payload={
                "source_document_id": getattr(rows.source_doc, "id", None),
                "options": options.model_dump(mode="json"),
                "base_revision": 1,
            },
            budget_usd=cfg.budget_usd if cfg.budget_usd is not None else ws.settings.max_cost_per_lecture_usd,
            deadline=time.monotonic() + cfg.timeout_s if cfg.timeout_s else None,
            echo=(lambda ev: echo(fixture.name, ev)) if echo else None,
        )
        rec = _Recorder(ctx, result, stages, timeout_s=cfg.timeout_s)
        if fixture.kind == "source":
            await _source_pipeline(rec, state, cfg, options, rows)
        else:
            await _screenplay_pipeline(rec, state, cfg, options, fixture)
        await asyncio.to_thread(_save_version, ws, rows.version_id, state, True)
    except StageUnavailable as exc:
        result.status, result.failed_stage, result.error = "skipped", exc.stage, exc.reason
    except StageFailed as exc:
        result.status, result.failed_stage, result.error = "failed", exc.stage, exc.message
    except FixtureError as exc:
        result.status, result.error = "failed", str(exc)
    except Exception as exc:  # harness-level problem (DB, storage): record and keep going
        logger.exception("eval fixture %s crashed", fixture.name)
        result.status, result.error = "failed", describe_error(exc, ws.settings)
    try:
        result.metrics = _metrics(state, ctx, options, stages)
    except Exception as exc:
        result.warnings.append(f"metrics failed: {describe_error(exc, ws.settings)}")
        if result.status == "ok":
            result.status, result.error = "failed", f"metrics failed: {type(exc).__name__}"
    if ctx is not None:
        result.cost_usd = round(ctx.cost_usd, 6)
    result.seconds = round(time.perf_counter() - started, 3)
    try:
        await _write_artifacts(out, result, state, ctx, stages)
    except Exception as exc:  # e.g. the output directory vanished: keep the remaining fixtures running
        logger.warning("eval fixture %s: artifacts not written: %s", fixture.name, type(exc).__name__)
        result.warnings.append(f"artifacts not written: {describe_error(exc, ws.settings)}")
    return result


async def run_fixtures(
    fixtures: Iterable[Fixture],
    cfg: RunConfig,
    ws: Workspace,
    stages: PipelineStages,
    *,
    echo: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[FixtureResult]:
    """Run fixtures sequentially (stages parallelise internally; results stay comparable)."""
    results: list[FixtureResult] = []
    for fixture in fixtures:
        if echo:
            echo(fixture.name, {"type": "fixture", "message": f"starting ({fixture.kind}: {fixture.path.name})"})
        result = await run_fixture(fixture, cfg, ws, stages, echo=echo)
        if echo:
            echo(fixture.name, {"type": "fixture", "message": f"{result.status} in {result.seconds:.1f}s"})
        results.append(result)
    return results


def run_eval(
    cfg: RunConfig,
    *,
    stages: PipelineStages | None = None,
    echo: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run every fixture in an isolated workspace and write ``report.json`` + ``report.md``.

    Returns the report dict. ``stages`` may be injected (tests); by default the real pipeline
    modules are imported inside the workspace environment.
    """
    from .report import build_report, describe_settings, write_report

    fixtures = discover_fixtures(cfg.fixtures_dir, cfg.only)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    root = cfg.out_dir / "workspace" if cfg.keep_workspace else None
    started = time.time()
    with isolated_workspace(root, provider=cfg.provider, render_manim=cfg.render_manim, keep=cfg.keep_workspace) as ws:
        resolved = stages if stages is not None else load_stages()
        register = resolved.helper("register_fakes")
        if cfg.provider == "fake" and callable(register):
            register()
        results = asyncio.run(run_fixtures(fixtures, cfg, ws, resolved, echo=echo))
        override = cfg.option_overrides.get("llm_provider")
        settings_info = describe_settings(ws.settings, engine=override if isinstance(override, str) else None)
    report = build_report(cfg, results, resolved, settings_info, started=started, finished=time.time())
    write_report(cfg.out_dir, report)
    return report
