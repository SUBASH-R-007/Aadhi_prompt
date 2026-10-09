"""Prompt preview: the exact prompts the model receives for a source file, built offline.

    python -m aadhi.pipeline.preview SOURCE [--options JSON|FILE] [--out DIR]
                                     [--stages brief,plan,scene] [--scene N|SCENE_ID]
                                     [--engine gemini|openai|anthropic]

For a PDF, DOCX, TXT or Markdown source this writes (with ``--out``) or prints:

* ``00_source_scope.md`` - what source scoping kept, set aside and removed, and which chunks the concept
  brief set aside as non-teaching material. Removed items appear as counts per category (set-aside
  chunks as counts per reason) only, never their values.
* ``01_brief.md`` - the concept-brief call (``brief.build_brief``).
* ``02_plan.md`` - the planning call (``plan.generate_plan``).
* ``03_scene_<id>.md`` - one scene-writer call (``script.write_scene``). By default this is the
  first teaching scene; ``--scene`` takes a 1-based position or a scene id.

Each prompt file starts with the model, temperature, response schema and prompt-file versions,
followed by the system prompt and the user prompt exactly as the stage passed them to the provider.
The provider also sends the response schema as the structured-output schema. The models are those
of the lecture's AI engine (``--engine``, else the options' ``llm_provider``): the preview shows what
that engine *would* be sent, while the calls themselves still go to the offline LLM.

No real model is ever called. The preview runs in an isolated temporary workspace with its own data
directory, SQLite database and local storage. Every provider is forced to ``fake`` and every API key
is blanked through environment variables, which take precedence over ``.env``. That environment is
set before any other ``aadhi`` module is imported (``aadhi`` and ``aadhi.pipeline`` themselves read no
settings). The real stages then run against the deterministic offline LLM, wrapped in a recorder
that keeps the first request of each call. So the prompts are produced by the same code paths a
lecture uses: ``build_brief_prompt``, ``build_plan_prompt`` and ``scene_prompt_sections`` plus
``write_scene``'s source note and task. One difference: the *content* that later prompts carry over
from earlier answers ("Concepts to teach", the outline, the scene's plan) comes from the offline
responders, which stand in for a real model's answer.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .base import ConceptBrief, GenerationOptions, IngestResult, LecturePlan
    from .scene_context import ScenePosition

STAGES = ("brief", "plan", "scene")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_BY_SUFFIX = {".pdf": "application/pdf", ".docx": DOCX_MIME, ".md": "text/markdown", ".markdown": "text/markdown",
                  ".txt": "text/plain", ".text": "text/plain"}
# Scene types that teach (the default scene to preview is the first of these after the opening).
TEACHING_SCENE_TYPES = ("content", "example", "simulation", "ai_video", "interactive")
CATEGORY_LABELS = {
    "person": "names, designations and contact details of the SME, authors, reviewers or faculty",
    "duration": "video, clip and segment durations, run times, word counts",
    "timecode": "start and end times of clips and segments",
    "admin": "course and subject codes, dates, versions, departments, institution boilerplate",
    "production_note": "recording and editing instructions",
    "structure": "clip scaffolding: per-clip title cards, 'bridge to next part', repeated objectives",
    "duplicate": "content repeated verbatim",
}
SKIP_LABELS = {
    "administrative": "document details: subject / unit / session lines, names, codes, dates",
    "production": "recording, editing, camera or animation directions",
    "scaffolding": "video packaging: title cards, bridges between clips, sign-offs",
    "duplicate": "a word-for-word repeat of another section",
    "off_topic": "material unrelated to the lecture's subject",
}
UNUSABLE_HASH = "!"  # the workspace user never signs in; "!" matches no password
API_KEY_VARS = ("GEMINI_API_KEY", "GEMINI_API_KEYS", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY",
                "GIPHY_API_KEY", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
# --engine choices (aadhi.providers.factory.LLM_ENGINES; not imported here: see the module docstring).
ENGINES = ("gemini", "openai", "anthropic")
ENGINE_LABELS = {"gemini": "Google Gemini", "openai": "OpenAI", "anthropic": "Anthropic Claude"}


def forced_environment(data_dir: Path) -> dict[str, str]:
    """Environment of the preview workspace: offline providers, no keys, its own data directory."""
    env = {
        "APP_ENV": "development",
        "DATA_DIR": str(data_dir),
        "DATABASE_URL": "",  # SQLite inside DATA_DIR
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_DIR": "",  # <DATA_DIR>/storage
        "LLM_PROVIDER": "fake",
        "TTS_PROVIDER": "fake",
        "IMAGE_PROVIDER": "fake",
        "VIDEO_PROVIDER": "fake",
        "WORKER_MODE": "inline",
        "MANIM_VISUAL_QA": "false",
        "ADMIN_PASSWORD": "",
    }
    env.update({name: "" for name in API_KEY_VARS})
    return env


def _reset_caches() -> None:
    """Drop cached settings, database engine and storage (they are rebuilt from the environment)."""
    from .. import config, db
    from ..storage import reset_storage_cache

    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()


@contextlib.contextmanager
def isolated_workspace() -> Iterator[Path]:
    """A temporary data directory + SQLite database + storage with offline providers.

    The previous environment and caches are restored afterwards, so the preview can also run inside
    another process (tests) without touching its database.
    """
    root = Path(tempfile.mkdtemp(prefix="aadhi-preview-"))
    forced = forced_environment(root / "data")
    saved = {k: os.environ.get(k) for k in forced}
    os.environ.update(forced)
    try:
        _reset_caches()
        from .. import db, models  # noqa: F401  (models registers the tables)

        db.Base.metadata.create_all(db.get_engine())
        yield root
    finally:
        try:
            _reset_caches()  # disposes the workspace engine before its files are removed
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            with contextlib.suppress(Exception):
                _reset_caches()
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# job context + recording LLM
# ---------------------------------------------------------------------------


@dataclass
class PreviewContext:
    """The parts of ``aadhi.jobs.base.JobContext`` that ingest, brief, plan and script use."""

    settings: Any
    assets: Any
    user_id: int | None = None
    project_id: int | None = None
    version_id: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def storage(self) -> Any:
        return self.assets.storage

    def session(self) -> Any:
        from ..db import session_scope

        return session_scope()

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.events.append({"level": level, "message": message})

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        return None

    def check_cancelled(self) -> None:
        return None

    def record_usage(self, usage: Any) -> float:
        return 0.0


@dataclass
class PromptCall:
    """The first request of one structured model call (re-asks happen inside the provider)."""

    schema: str
    model: str
    temperature: float | None
    system: str
    user: str
    files: list[str] = field(default_factory=list)


class RecordingLLM:
    """Wraps the offline fake LLM and records what each structured call sends."""

    def __init__(self, inner: Any) -> None:
        if getattr(inner, "name", "") != "fake":
            raise RuntimeError("the prompt preview runs only with the offline fake LLM")
        self.inner = inner
        self.name = inner.name
        self.calls: list[PromptCall] = []

    async def generate_json(self, *, model: str, system: str, prompt: str, schema: Any, files: Sequence[Any] = (),
                            temperature: float | None = None, **kwargs: Any) -> Any:
        self.calls.append(PromptCall(schema=schema.__name__, model=model, temperature=temperature, system=system,
                                     user=prompt, files=[str(getattr(f, "name", "") or "file") for f in files]))
        if temperature is not None:
            kwargs["temperature"] = temperature
        return await self.inner.generate_json(model=model, system=system, prompt=prompt, schema=schema, files=files,
                                              **kwargs)

    async def generate_text(self, **kwargs: Any) -> str:  # pragma: no cover - the pipeline never asks for text
        raise RuntimeError("the prompt preview records structured calls only")


@contextlib.contextmanager
def recording_llm(settings: Any) -> Iterator[RecordingLLM]:
    """Route the pipeline's LLM seam (``integrations.get_llm``) to a recorder for the duration."""
    from . import integrations

    original = integrations.get_llm
    recorder = RecordingLLM(original(settings))  # the default engine: forced to fake
    # Every engine a stage asks for (the lecture's llm_provider) is answered by the offline recorder.
    integrations.get_llm = lambda _settings, engine=None: recorder  # type: ignore[assignment]
    try:
        yield recorder
    finally:
        integrations.get_llm = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# running the stages
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    stage: str
    call: PromptCall | None = None
    prompt_files: tuple[str, ...] = ()
    note: str = ""  # why there is no call, or what the reader should know
    scene: Any = None  # ScenePosition (scene stage)
    engine: str | None = None  # the lecture's chosen AI engine (None = server default)


@dataclass
class Preview:
    source_name: str
    ingest: IngestResult
    options: GenerationOptions
    brief: ConceptBrief | None = None
    plan: LecturePlan | None = None
    lexicon: list[Any] = field(default_factory=list)  # the scene writers' glossary (LexiconEntry)
    stages: dict[str, StageResult] = field(default_factory=dict)
    brief_excluded: dict[str, int] = field(default_factory=dict)

    @property
    def brief_skipped(self) -> dict[str, int]:
        """``{reason: n}`` of the chunks the concept brief set aside (counts only)."""
        out: dict[str, int] = {}
        for s in self.brief.skipped_chunks if self.brief is not None else []:
            out[s.reason] = out.get(s.reason, 0) + 1
        return out


def load_options(raw: str | None) -> GenerationOptions:
    """``GenerationOptions`` from inline JSON or a JSON file (None = defaults)."""
    from .base import GenerationOptions

    if not raw:
        return GenerationOptions()
    text = raw
    path = Path(raw)
    if not raw.lstrip().startswith("{") and path.is_file():
        text = path.read_text(encoding="utf-8")
    return GenerationOptions.model_validate(json.loads(text))


def with_engine(options: GenerationOptions, engine: str | None) -> GenerationOptions:
    """``options`` with ``llm_provider`` set to ``engine`` (None keeps the options' own choice)."""
    from .base import GenerationOptions

    if not engine:
        return options
    if engine not in ENGINES:
        raise ValueError(f"unknown AI engine {engine!r}; choose from {', '.join(ENGINES)}")
    return GenerationOptions.model_validate({**options.model_dump(mode="json"), "llm_provider": engine})


def mime_for(path: Path) -> str:
    mime = MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported source type {path.suffix or '(none)'}; use PDF, DOCX, TXT or MD")
    return mime


async def _ingest(ctx: PreviewContext, path: Path) -> IngestResult:
    """Store the file in the workspace and run the real ingest (extraction + source scoping)."""
    import hashlib

    from ..models import Project, SourceDocument, User
    from ..storage.assets import bytes_key, storage_key_for
    from .ingest import ingest_source

    data = await asyncio.to_thread(path.read_bytes)
    mime = mime_for(path)
    key = storage_key_for("source", bytes_key("source", data), mime)
    await asyncio.to_thread(ctx.storage.put_bytes, key, data, mime)

    def seed() -> Any:
        with ctx.session() as db:
            user = User(username="preview", password_hash=UNUSABLE_HASH, role="editor")
            db.add(user)
            db.flush()
            project = Project(owner_id=user.id, title=path.stem[:200])
            db.add(project)
            db.flush()
            src = SourceDocument(project_id=project.id, filename=path.name[:255], mime=mime, size_bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest(), storage_key=key)
            db.add(src)
            db.flush()
            db.expunge(src)
            return user.id, project.id, src

    ctx.user_id, ctx.project_id, source = await asyncio.to_thread(seed)
    return await ingest_source(ctx, source)


def default_scene(positions: Sequence[ScenePosition]) -> ScenePosition:
    """The first teaching scene after the opening (else the first scene)."""
    for pos in positions:
        if pos.index > 0 and pos.planned.type in TEACHING_SCENE_TYPES:
            return pos
    return positions[0]


def pick_scene(positions: Sequence[ScenePosition], wanted: str | None) -> ScenePosition:
    """Scene by 1-based position or id (``None`` = ``default_scene``). Raises ``ValueError``."""
    if not positions:
        raise ValueError("the plan has no scenes")
    if not wanted:
        return default_scene(positions)
    if wanted.strip().isdigit():
        n = int(wanted)
        if not 1 <= n <= len(positions):
            raise ValueError(f"--scene {n} is out of range: the plan has {len(positions)} scenes")
        return positions[n - 1]
    for pos in positions:
        if pos.planned.id == wanted.strip():
            return pos
    ids = ", ".join(p.planned.id for p in positions[:60])
    raise ValueError(f"no scene with id {wanted!r}; the plan's scenes are: {ids}")


async def collect(path: Path, options: GenerationOptions, *, stages: Sequence[str] = STAGES,
                  scene: str | None = None) -> Preview:
    """Ingest ``path`` and record the brief, plan and scene-writer prompts (inside ``isolated_workspace``)."""
    from ..config import get_settings
    from ..storage import get_asset_store
    from .brief import BRIEF_PROMPTS, BriefUnavailable, brief_model, build_brief, build_brief_prompt
    from .gen_models import scene_family
    from .plan import PLAN_PROMPTS, TeacherMeta, generate_plan, original_attachment, skipped_ids
    from .plan_rules import fit_plan
    from .plan_state import lexicon_for_plan
    from .scene_context import LectureContext, positions
    from .script import FAMILY_PROMPTS, write_scene

    settings = get_settings()
    if settings.llm_provider != "fake":  # pragma: no cover - guarded by forced_environment
        raise RuntimeError("the prompt preview must run with LLM_PROVIDER=fake")
    ctx = PreviewContext(settings=settings, assets=get_asset_store())
    ingest = await _ingest(ctx, path)
    out = Preview(source_name=path.name, ingest=ingest, options=options)
    want = set(stages)
    engine = options.llm_provider  # stages pick that engine's models; the recorder answers every engine
    with recording_llm(settings) as llm:
        # 1. concept brief (planning works without one, as in the orchestrator)
        brief_stage = StageResult("brief", prompt_files=BRIEF_PROMPTS, engine=engine)
        n = len(llm.calls)
        try:
            out.brief = await build_brief(ctx, ingest, options)
        except BriefUnavailable as exc:
            brief_stage.note = (f"No concept-brief call is made for this source ({exc}); the planner works "
                                "directly from the source chunks. The prompt below is what the call would send.")
        except Exception as exc:  # noqa: BLE001 - mirrors the orchestrator: the brief is an aid
            brief_stage.note = f"The offline brief failed ({type(exc).__name__}); the planner works without it."
        if len(llm.calls) > n:
            brief_stage.call = llm.calls[n]
        else:
            system, user = build_brief_prompt(ingest, options)
            brief_stage.call = PromptCall(schema="GenBrief", model=brief_model(options, settings), temperature=None,
                                          system=system, user=user)
        if out.brief is not None:
            from .source_scope import excluded_counts

            out.brief_excluded = excluded_counts(out.brief.excluded)
        out.stages["brief"] = brief_stage
        if not want & {"plan", "scene"}:
            return out

        # 2. plan
        n = len(llm.calls)
        result = await generate_plan(ctx, ingest, options, TeacherMeta(), brief=out.brief)
        out.stages["plan"] = StageResult("plan", call=llm.calls[n], prompt_files=PLAN_PROMPTS, engine=engine)
        if "scene" not in want:
            out.plan = result.plan
            return out

        # 3. one scene writer, set up exactly as script.write_scenes_detailed does
        plan, _ = fit_plan(result.plan)
        out.plan, out.lexicon = plan, lexicon_for_plan(plan, result.lexicon)
        lc = LectureContext(plan=plan, ingest=ingest, options=options, lexicon=list(out.lexicon),
                            figures=list(ingest.figures), skipped_chunk_ids=skipped_ids(out.brief))
        pos = pick_scene(positions(plan), scene)
        files = await original_attachment(ctx, ingest, llm, purpose="scene writers")
        n = len(llm.calls)
        await write_scene(ctx, lc, pos, llm=llm, files=files)
        family = scene_family(pos.planned.type)
        out.stages["scene"] = StageResult("scene", call=llm.calls[n] if len(llm.calls) > n else None,
                                          prompt_files=("style", FAMILY_PROMPTS[family]), scene=pos, engine=engine)
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _fence(text: str) -> str:
    """A backtick fence longer than any backtick run inside ``text``."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(4, longest + 1)


def _block(title: str, text: str) -> str:
    fence = _fence(text)
    return f"## {title}\n\n{fence}text\n{text}\n{fence}\n"


def _table(rows: Sequence[tuple[str, str]]) -> str:
    lines = ["| | |", "|---|---|"]
    lines += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in rows]
    return "\n".join(lines) + "\n"


def _versions(names: Sequence[str]) -> str:
    from .prompting import load_prompt

    return ", ".join(f"`{n}.md` v{load_prompt(n).version}" for n in names)


STAGE_TITLES = {
    "brief": ("Concept brief", "aadhi.pipeline.brief.build_brief"),
    "plan": ("Lecture plan", "aadhi.pipeline.plan.generate_plan"),
    "scene": ("Scene writer", "aadhi.pipeline.script.write_scene"),
}
CARRIED_OVER = {
    "plan": ("The \"Concepts to teach\" section holds the concept brief. In this preview it comes from the offline "
             "brief responder; in a real run it holds the brief the model wrote in the previous call."),
    "scene": ("The lecture summary, outline and \"This scene\" come from the plan. In this preview they come from the "
              "offline plan responder; in a real run they hold the model's plan."),
}


def render_prompt(stage: StageResult) -> str:
    """One prompt file: header (model, temperature, schema, prompt files), system prompt, user prompt."""
    title, fn = STAGE_TITLES[stage.stage]
    call = stage.call
    assert call is not None
    temp = "(not sent)" if call.temperature is None else f"{call.temperature:g}"
    if stage.engine:
        engine = f"{ENGINE_LABELS.get(stage.engine, stage.engine)} (`{stage.engine}`; not called: the preview is offline)"
    else:
        engine = "server default (`LLM_MODEL_*` models; choose one with `--engine`)"
    rows = [
        ("Stage", f"{title} (`{fn}`)"),
        ("AI engine", engine),
        ("Model", f"`{call.model}`"),
        ("Temperature", temp),
        ("Response schema", f"`{call.schema}` (sent as the structured-output schema)"),
        ("Prompt files", _versions(stage.prompt_files)),
        ("Files attached", ", ".join(call.files) or "none"),
        ("Size", f"system {len(call.system):,} characters, user {len(call.user):,} characters "
                 f"(about {(len(call.system) + len(call.user)) // 4:,} tokens)"),
    ]
    if stage.scene is not None:
        p = stage.scene.planned
        role = f", {p.narrative_role}" if p.narrative_role else ""
        rows.insert(1, ("Scene", f"`{p.id}` ({p.type}{role}), scene {stage.scene.index + 1} of {stage.scene.total}, "
                                 f"chapter {stage.scene.chapter_number}"))
    notes = [n for n in (stage.note, CARRIED_OVER.get(stage.stage, "")) if n]
    parts = [f"# Prompt preview: {title.lower()}\n", _table(rows)]
    if notes:
        parts.append("\n".join(f"> {n}" for n in notes) + "\n")
    parts.append(_block("System prompt", call.system))
    parts.append(_block("User prompt", call.user))
    return "\n".join(parts)


def render_brief_accounting(preview: Preview) -> str:
    """The "Set aside by the concept brief" section: counts per reason and cited / set-aside totals (no text)."""
    brief = preview.brief
    assert brief is not None
    known = [c.id for c in preview.ingest.chunks]
    skipped = preview.brief_skipped
    cited = brief.cited_chunk_ids()
    aside = brief.skipped_chunk_ids()
    out = ["## Set aside by the concept brief\n",
           "Sections the concept brief judged to hold no teaching content. The planner and the scene writers never "
           "see them; values are never shown, only counts per reason.\n"]
    if skipped:
        lines = ["| Reason | Sections | What it covers |", "|---|---|---|"]
        for reason, n in sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {reason} | {n} | {SKIP_LABELS.get(reason, '')} |")
        out.append("\n".join(lines) + "\n")
    else:
        out.append("Nothing.\n")
    n_cited = sum(1 for c in known if c in cited)
    n_aside = sum(1 for c in known if c in aside)
    out.append(f"Of {len(known)} sections: {n_cited} cited by a concept or a source question, {n_aside} set aside, "
               f"{len(known) - n_cited - n_aside} neither (still shown to the planner).\n")
    return "\n".join(out)


def render_scope(preview: Preview) -> str:
    """``00_source_scope.md``: what the model gets from the source and what it never sees (counts only)."""
    from .plan import excluded_matcher
    from .source_scope import excluded_counts

    ing = preview.ingest
    meta = ing.document_meta
    chars = sum(len(c.text) for c in ing.chunks)
    rows = [
        ("Source", f"`{preview.source_name}`"),
        ("Source format", ing.source_format),
        ("Pages", str(ing.pages) if ing.pages else "-"),
        ("Teaching sections (chunks) sent to the model", f"{len(ing.chunks)} ({chars:,} characters)"),
        ("Figures", str(len(ing.figures))),
        ("Author's visual suggestions (hints for visuals, never narrated)", str(len(ing.visual_notes))),
        ("Original file attached to the model", "only with Gemini" if ing.attach_original else "no"),
    ]
    if preview.brief is not None:
        rows.insert(4, ("Sections the concept brief set aside (never planned or narrated)",
                        str(sum(preview.brief_skipped.values()))))
    parts = [f"# Source scope: {preview.source_name}\n",
             "What the model receives from this source, and what it never sees.\n", _table(rows)]
    meta_rows = [("Subject", meta.subject_name), ("Unit", meta.unit_name), ("Session", meta.session_number),
                 ("Session title", meta.session_title)]
    meta_rows = [(k, v) for k, v in meta_rows if v]
    parts.append("## Document metadata\n\nUsed only to fill title-card fields the teacher left empty; never taught.\n")
    parts.append(_table(meta_rows) if meta_rows else "None found.\n")
    counts = excluded_counts(ing.excluded)
    parts.append("## Removed before any model call\n\nValues are masked: they never reach a prompt, the lecture or "
                 "this report.\n")
    if counts:
        lines = ["| Category | Removed | What it covers |", "|---|---|---|"]
        for cat, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {cat} | {n} item{'s' if n != 1 else ''} | {CATEGORY_LABELS.get(cat, '')} |")
        parts.append("\n".join(lines) + "\n")
    else:
        parts.append("Nothing.\n")
    if preview.brief_excluded:
        items = ", ".join(f"{cat}: {n} item{'s' if n != 1 else ''}" for cat, n in sorted(preview.brief_excluded.items()))
        parts.append(f"Also set aside by the concept brief: {items}.\n")
    brief = preview.brief
    if brief is not None:
        parts.append(render_brief_accounting(preview))
    if ing.chunks:
        with_brief = brief is not None
        lines = ["| Chunk | Heading | Characters | Visual suggestions |" + (" Concept brief |" if with_brief else ""),
                 "|---|---|---|---|" + ("---|" if with_brief else "")]
        near: dict[str, int] = {}
        for v in ing.visual_notes:
            if v.near_chunk_id:
                near[v.near_chunk_id] = near.get(v.near_chunk_id, 0) + 1
        reasons = {s.chunk_id: s.reason for s in brief.skipped_chunks} if brief is not None else {}
        cited = brief.cited_chunk_ids() if brief is not None else set()
        for c in ing.chunks:
            heading = " > ".join(c.heading_path).replace("|", "/") or "-"
            row = f"| {c.id} | {heading} | {len(c.text):,} | {near.get(c.id, 0) or ''} |"
            if with_brief:
                status = f"set aside ({reasons[c.id]})" if c.id in reasons else "cited" if c.id in cited else "not cited"
                row += f" {status} |"
            lines.append(row)
        parts.append("## Source sections (chunks)\n\n" + "\n".join(lines) + "\n")
    leaks = excluded_matcher(ing, preview.brief)
    warnings = [w for w in ing.warnings if w.strip() and not leaks(w)]
    if warnings:
        parts.append("## Ingest notes\n\n" + "\n".join(f"- {w}" for w in warnings) + "\n")
    if preview.plan is not None:
        scenes = preview.plan.all_scenes()
        parts.append(f"## Offline plan used for the scene preview\n\n{len(scenes)} scenes in "
                     f"{len(preview.plan.chapters)} chapters (from the offline responders, not a real model).\n")
    return "\n".join(parts)


def render(preview: Preview, stages: Sequence[str] = STAGES) -> dict[str, str]:
    """``{file name: Markdown}`` for the requested stages (+ ``00_source_scope.md``)."""
    files = {"00_source_scope.md": render_scope(preview)}
    numbers = {"brief": "01", "plan": "02", "scene": "03"}
    for stage in STAGES:
        result = preview.stages.get(stage)
        if stage not in stages or result is None or result.call is None:
            continue
        name = f"{numbers[stage]}_{stage}.md"
        if stage == "scene" and result.scene is not None:
            name = f"03_scene_{result.scene.planned.id}.md"
        files[name] = render_prompt(result)
    return files


def run_preview(source: str | Path, *, options: GenerationOptions | None = None, stages: Sequence[str] = STAGES,
                scene: str | None = None, engine: str | None = None) -> dict[str, str]:
    """Build the preview files for ``source`` in an isolated, offline workspace (no real model call).

    ``engine`` (gemini | openai | anthropic) overrides the options' ``llm_provider``: the prompts show
    that engine's models, while the calls still go to the offline LLM.
    """
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    mime_for(path)
    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise ValueError(f"unknown stage(s) {bad}; choose from {', '.join(STAGES)}")
    with isolated_workspace():
        from .base import GenerationOptions

        opts = with_engine(options or GenerationOptions(), engine)
        preview = asyncio.run(collect(path, opts, stages=stages, scene=scene))
        return render(preview, stages)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m aadhi.pipeline.preview",
        description="Show the exact prompts the model receives for a source file (offline; no model is called).",
    )
    p.add_argument("source", help="the source document (PDF, DOCX, TXT or MD)")
    p.add_argument("--options", help="GenerationOptions as inline JSON or a path to a JSON file")
    p.add_argument("--out", help="write one Markdown file per prompt into this directory (default: print them)")
    p.add_argument("--stages", default=",".join(STAGES), help="comma-separated subset of brief,plan,scene")
    p.add_argument("--scene", help="scene to preview: 1-based position or scene id (default: first teaching scene)")
    p.add_argument("--engine", choices=ENGINES,
                   help="AI engine whose models the prompts show (default: the options' llm_provider); "
                        "no model is called either way")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in STAGES]
    if bad or not stages:
        parser.error(f"--stages takes a comma-separated subset of {','.join(STAGES)} (got {args.stages!r})")
    try:
        with isolated_workspace():
            options = with_engine(load_options(args.options), args.engine)
            path = Path(args.source)
            if not path.is_file():
                raise FileNotFoundError(f"no such file: {path}")
            files = render(asyncio.run(collect(path, options, stages=stages, scene=args.scene)), stages)
    except (FileNotFoundError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not dump a traceback
        from .ingest import IngestError

        if isinstance(exc, IngestError):
            print(f"error: could not read the source: {exc}", file=sys.stderr)
            return 1
        raise
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (out / name).write_text(text, encoding="utf-8")
            print(out / name)
        return 0
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    for name, text in files.items():
        print(f"<!-- ===== {name} ===== -->\n")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
