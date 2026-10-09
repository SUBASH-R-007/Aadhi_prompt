"""Critic: deterministic grounding checks, then an LLM judge per chapter (prompts/critic.md).

The judge reviews one chapter at a time against the source chunks the chapter cites:
grounding, factual and formula errors, pedagogy (concrete before abstract, example and
non-example after definitions, bridging, visuals referenced in narration, misconceptions
targeted) and flow. It also sees the whole lecture's outline and the scene just before the
chapter, so it can judge the order of ideas, the bridges between scenes and repeated
introductions across the lecture. Flow findings carry a tag that selects their issue code
(``flow.order``, ``flow.bridge``, ``flow.repeated_intro``, ``flow.packaging``; untagged:
``flow.issue``); all of them are fixable. Every finding must quote the scene (``claim``); quotes
that are not found in the scene are discarded, and source ``evidence`` that cannot be found in the
cited chunks is dropped (factual errors that rely on it are downgraded to warnings).

A whole-lecture review also runs the optional AI terminology assistant (``aadhi.pipeline.quality.assist``,
``QUALITY_AI_TERMINOLOGY``, off by default): one call about the term pairs the deterministic lint cannot
decide, whose "yes" answers become ``terminology.ambiguous`` notes (info, not fixable).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import ProviderError, RateLimited
from ..schemas.screenplay import AIVideoScene, BoardScene, QuizScene, Screenplay, SimulationScene
from . import integrations
from .aio import gather_all
from .base import ConceptBrief, GenerationOptions, IngestResult, Issue
from .gen_models import GenCritique, GenFinding
from .prompting import join_sections, json_section, section, system_prompt
from .richlite import to_plain
from .scene_context import relevant_chunks

log = logging.getLogger(__name__)

CRITIC_PROMPTS = ("critic",)
CHAPTER_SOURCE_CHARS = 24_000
_CODE_BY_CATEGORY = {
    "grounding": "grounding.unsupported_claim",
    "factual": "factual.error",
    "formula": "formula.error",
    "pedagogy": "pedagogy.issue",
    "flow": "flow.issue",
    "clarity": "clarity.issue",
    "misconception": "misconception.reinforced",
}
FLOW_CODES = {
    "order": "flow.order",  # an idea is used before it is taught / illogical order
    "bridge": "flow.bridge",  # an abrupt start, no link to the previous scene
    "repeated_intro": "flow.repeated_intro",  # the session or its objectives introduced again, filler between parts
    "packaging": "flow.packaging",  # talk about the source's clips, segments, timings, document or authors
}
_FLOW_TAG = re.compile(
    r"^\s*(?:\[\s*(?P<a>order|bridge|repeated[ _-]?intro|packaging)\s*\]|(?P<b>order|bridge|repeated[ _-]?intro|packaging)\s*:)"
    r"\s*[:\-–—]?\s*",
    re.I,
)
OUTLINE_MAX = 120  # scenes listed in the critic's lecture outline


def critic_model(settings: Any, options: GenerationOptions | None = None) -> str:
    """The critic model of the lecture's engine (``options.llm_provider``, else the server default)."""
    return integrations.llm_model(settings, "critic", options)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", to_plain(text or "")).strip().lower()


def scene_text(scene: Any) -> str:
    """All learner-visible and spoken text of a scene (for quote verification)."""
    parts = [scene.title or "", scene.subtitle or ""]
    parts += [b.narration for b in scene.all_beats()]
    if isinstance(scene, BoardScene):
        for i in scene.board:
            parts += [i.text, i.term or "", i.latex or "", i.caption or "", i.justification or "", i.code or ""]
            parts += [c for r in (i.rows or []) for c in r] + list(i.headers or [])
    if isinstance(scene, QuizScene):
        parts += [scene.question, *scene.options, scene.explanation, *scene.feedback_wrong]
    if isinstance(scene, AIVideoScene):
        parts.append(scene.video_prompt)
    return " \n ".join(p for p in parts if p)


def render_scene_for_review(scene: Any) -> dict[str, Any]:
    """Compact JSON view of a scene for the judge (beats numbered from 1)."""
    d: dict[str, Any] = {"id": scene.id, "type": scene.type, "title": scene.title}
    if scene.intent:
        d["goal"] = scene.intent.goal
    beats = []
    board = {i.id: i for i in scene.board} if isinstance(scene, BoardScene) else {}
    for n, b in enumerate(scene.all_beats(), 1):
        entry: dict[str, Any] = {"n": n, "narration": b.narration}
        if b.board_item_id and b.board_item_id in board:
            it = board[b.board_item_id]
            reveal = {"kind": it.kind.value, "text": it.text, "term": it.term, "latex": it.latex,
                      "justification": it.justification, "blank": it.blank or None}
            entry["reveals"] = {k: v for k, v in reveal.items() if v is not None and v != ""}
        if b.fill_item_id:
            entry["fills"] = b.fill_item_id
        if b.pause_after:
            entry["pause_after"] = b.pause_after
        if b.visual_cue:
            entry["visual_cue"] = b.visual_cue
        if b.source_refs:
            entry["source_refs"] = b.source_refs
        beats.append({k: v for k, v in entry.items() if v is not None})
    d["beats"] = beats
    if isinstance(scene, QuizScene):
        d["quiz"] = {"question": scene.question, "options": scene.options, "correct": scene.options[scene.correct_index],
                     "explanation": scene.explanation}
    if isinstance(scene, SimulationScene):
        d["animation"] = {"template": scene.manim.template, "params": scene.manim.params} if scene.manim.template else "free-form code"
    if scene.side_panel is not None:
        d["side_panel"] = {"kind": scene.side_panel.kind, "rationale": scene.side_panel.rationale}
    return d


_QUOTE_RE = re.compile(r"[\"“”„«»]([^\"“”„«»]{12,400})[\"“”„«»]")
MIN_QUOTE_WORDS = 4


def quoted_passages(text: str) -> list[str]:
    """Passages in double quotes that are long enough to be a claim about the source."""
    return [m.group(1).strip() for m in _QUOTE_RE.finditer(text or "") if len(m.group(1).split()) >= MIN_QUOTE_WORDS]


def _scene_refs(scene: Any) -> list[str]:
    refs = [r for b in scene.all_beats() for r in b.source_refs]
    refs += list(scene.intent.source_refs) if scene.intent else []
    if isinstance(scene, BoardScene):
        refs += [r for i in scene.board for r in i.source_refs]
    if isinstance(scene, QuizScene):
        refs += list(scene.source_refs)
    return list(dict.fromkeys(refs))


def _quote_issues(scene: Any, pool: list[str]) -> list[Issue]:
    """Quoted passages (narration and board) must occur verbatim in the source (``pool``: normalised chunks)."""
    texts: list[tuple[str | None, str]] = [(b.id, b.narration) for b in scene.all_beats()]
    if isinstance(scene, BoardScene):
        texts += [(None, i.text) for i in scene.board] + [(None, i.justification or "") for i in scene.board]
    out: list[Issue] = []
    for beat_id, text in texts:
        for quote in quoted_passages(text):
            if not any(_norm(quote) in p for p in pool):
                out.append(Issue(code="grounding.quote_not_found", severity="warning", scene_id=scene.id, beat_id=beat_id,
                                 source="critic", fixable=True,
                                 message=f'The quoted passage "{quote[:160]}" does not appear in the source; quote the '
                                         "notes exactly or paraphrase without quotation marks."))
    return out


def deterministic_issues(screenplay: Screenplay, ingest: IngestResult) -> list[Issue]:
    """Grounding checks that need no model.

    * quoted passages must occur verbatim in the source (``grounding.quote_not_found``, warning);
    * teaching scenes should cite at least one source chunk (``grounding.no_source``, info).
    """
    out: list[Issue] = []
    if not ingest.chunks:
        return out
    pool = [_norm(c.text) for c in ingest.chunks]
    for s in screenplay.scenes:
        out.extend(_quote_issues(s, pool))
        if s.type not in ("content", "example", "simulation"):
            continue
        if not _scene_refs(s):
            out.append(Issue(code="grounding.no_source", severity="info", scene_id=s.id, source="critic", fixable=False,
                             message="This teaching scene cites no source chunk; check its facts against the notes."))
    return out


def finding_code(f: GenFinding) -> tuple[str, str]:
    """(issue code, message) for a finding; a flow finding's leading tag ("[bridge] ...") picks its code."""
    msg = f.message.strip()
    if f.category != "flow":
        return _CODE_BY_CATEGORY.get(f.category, "critic.issue"), msg
    m = _FLOW_TAG.match(msg)
    if m is None:
        return "flow.issue", msg
    kind = re.sub(r"[ _-]+", "_", (m.group("a") or m.group("b")).lower())
    return FLOW_CODES[kind], msg[m.end():].strip() or msg


def verify_findings(findings: list[GenFinding], scenes: dict[str, Any], chunk_text: dict[str, str]) -> list[Issue]:
    """Keep findings whose quotes check out; convert them to Issues (source=critic)."""
    out: list[Issue] = []
    for f in findings:
        scene = scenes.get(f.scene_id)
        if scene is None:
            continue
        if f.claim.strip() and _norm(f.claim) not in _norm(scene_text(scene)):
            log.info("critic finding dropped: claim not found in scene %s", f.scene_id)
            continue
        severity = f.severity
        evidence_ok = False
        if f.evidence.strip():
            pool = [chunk_text[f.evidence_chunk]] if f.evidence_chunk in chunk_text else list(chunk_text.values())
            evidence_ok = any(_norm(f.evidence) in _norm(t) for t in pool)
        if f.category in ("factual", "formula", "grounding") and severity == "error" and f.evidence.strip() and not evidence_ok:
            severity = "warning"
        beat_id = None
        beats = scene.all_beats()
        if f.beat_number is not None and 1 <= f.beat_number <= len(beats):
            beat_id = beats[f.beat_number - 1].id
        code, msg = finding_code(f)
        if f.claim.strip():
            msg += f' — "{f.claim.strip()[:160]}"'
        if f.evidence.strip() and evidence_ok:
            msg += f' (source: "{f.evidence.strip()[:160]}")'
        if f.suggestion.strip():
            msg += f" Suggestion: {f.suggestion.strip()}"
        out.append(Issue(code=code, severity=severity, message=msg[:1500],
                         scene_id=scene.id, beat_id=beat_id, source="critic", fixable=True))
    return out


def chapter_groups(screenplay: Screenplay) -> list[tuple[str, list[Any]]]:
    """(chapter title, scenes) in order; scenes without a chapter form their own group."""
    by_id = {s.id: s for s in screenplay.scenes}
    groups: list[tuple[str, list[Any]]] = []
    seen: set[str] = set()
    for ch in screenplay.chapters:
        scenes = [by_id[x] for x in ch.scene_ids if x in by_id]
        seen.update(s.id for s in scenes)
        if scenes:
            groups.append((ch.title, scenes))
    rest = [s for s in screenplay.scenes if s.id not in seen]
    if rest:
        groups.append(("Other scenes", rest))
    return groups


def lecture_outline(screenplay: Screenplay, reviewed: set[str]) -> list[str]:
    """One line per scene of the whole lecture ("n. id (type): title"); scenes under review end with " *"."""
    lines = [f"{n}. {s.id} ({s.type}): {s.title or '-'}{' *' if s.id in reviewed else ''}"
             for n, s in enumerate(screenplay.scenes, 1)]
    if len(lines) > OUTLINE_MAX:
        keep = {i for i, s in enumerate(screenplay.scenes) if s.id in reviewed}
        lines = [line for i, line in enumerate(lines) if i in keep or i < 20 or i >= len(lines) - 20]
    return lines


def scene_before(screenplay: Screenplay, scenes: list[Any]) -> dict[str, Any] | None:
    """The scene just before the first reviewed one (to judge the bridge into this chapter)."""
    index = {s.id: i for i, s in enumerate(screenplay.scenes)}
    first = min((index[s.id] for s in scenes if s.id in index), default=0)
    if first <= 0:
        return None
    prev = screenplay.scenes[first - 1]
    beats = prev.all_beats()
    return {"id": prev.id, "type": prev.type, "title": prev.title, "last_beat": beats[-1].narration if beats else ""}


def build_critic_prompt(screenplay: Screenplay, title: str, scenes: list[Any], ingest: IngestResult,
                        skip: frozenset[str] = frozenset()) -> tuple[str, dict[str, str]]:
    """The reviewer's prompt and the source text it may quote (``skip``: chunks the concept brief set aside)."""
    refs = list(dict.fromkeys(
        r for s in scenes for r in ([*s.intent.source_refs] if s.intent else []) + [r for b in s.all_beats() for r in b.source_refs]
    ))
    query = " ".join(s.title + " " + (s.intent.goal if s.intent else "") for s in scenes)
    chunks = relevant_chunks(ingest.chunks, refs, query, budget=CHAPTER_SOURCE_CHARS, skip=skip)
    miscs = [{"key": m.id, "statement": m.statement, "correction": m.correction} for m in screenplay.misconceptions]
    before = scene_before(screenplay, scenes)
    prompt = join_sections(
        json_section("Lecture", {
            "session_title": screenplay.session_title, "subject": screenplay.subject_name,
            "objectives": [o.text for o in screenplay.learning_objectives],
        }),
        section("Lecture outline", "\n".join(lecture_outline(screenplay, {s.id for s in scenes}))),
        section("Chapter", title),
        json_section("Scene before this chapter", before) if before else "",
        json_section("Scenes", [render_scene_for_review(s) for s in scenes]),
        json_section("Known misconceptions", miscs) if miscs else "",
        json_section("Source", [{"id": c.id, "page": c.page, "text": c.text} for c in chunks]),
        section("Task", "Review these scenes. Report only real problems, each with a verbatim claim quote. "
                        "Respond with JSON that matches the response schema."),
    )
    return prompt, {c.id: c.text for c in chunks}


async def critique_chapter(ctx: Any, llm: Any, screenplay: Screenplay, title: str, scenes: list[Any], ingest: IngestResult,
                           skip: frozenset[str] = frozenset(), options: GenerationOptions | None = None) -> list[Issue]:
    prompt, chunk_text = build_critic_prompt(screenplay, title, scenes, ingest, skip)
    ids = {s.id for s in scenes}

    def validate(out: GenCritique) -> list[str]:
        bad = sorted({f.scene_id for f in out.findings if f.scene_id not in ids})
        return [f"unknown scene ids {bad}; use the ids of the scenes under review"] if bad else []

    try:
        async with integrations.limit("llm"):
            out: GenCritique = await llm.generate_json(
                model=critic_model(ctx.settings, options), system=system_prompt(*CRITIC_PROMPTS), prompt=prompt,
                schema=GenCritique, temperature=0.2, on_usage=ctx.record_usage, validate=validate, validation_retries=1,
            )
    except (JobCancelled, BudgetExceeded, RateLimited):
        raise
    except Exception as exc:  # noqa: BLE001 - provider errors and checker bugs: the review is advisory
        if not isinstance(exc, ProviderError):
            log.warning("critic review of %r raised %s", title, type(exc).__name__, exc_info=True)
        ctx.log(f"Critic review of '{title}' failed: {ctx.settings.redact(str(exc))[:200]}", "warning")
        return [Issue(code="critic.unavailable", severity="info", source="system", fixable=False,
                      message=f"The automatic review of chapter '{title}' could not run.")]
    return verify_findings(out.findings, {s.id: s for s in scenes}, chunk_text)


async def critique(ctx: Any, screenplay: Screenplay, ingest: IngestResult, options: GenerationOptions | None = None,
                   *, scene_ids: set[str] | None = None, brief: ConceptBrief | None = None) -> list[Issue]:
    """Deterministic checks + LLM judge per chapter (optionally only chapters containing ``scene_ids``). The chunks
    the concept ``brief`` set aside are never added to a chapter's source context."""
    skip = frozenset(brief.skipped_chunk_ids()) if brief is not None else frozenset()
    issues = deterministic_issues(screenplay, ingest)
    if scene_ids is not None:
        issues = [i for i in issues if i.scene_id in scene_ids]
    else:  # whole-lecture review: the optional AI terminology assistant (QUALITY_AI_TERMINOLOGY, off by default)
        from .quality.assist import terminology_assist

        issues += await terminology_assist(ctx, screenplay, options)
    groups = chapter_groups(screenplay)
    if scene_ids is not None:
        groups = [(t, [s for s in ss if s.id in scene_ids]) for t, ss in groups]
        groups = [(t, ss) for t, ss in groups if ss]
    if not groups:
        return issues
    llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    sem = asyncio.Semaphore(max(1, int(ctx.settings.llm_max_parallel)))

    async def one(title: str, scenes: list[Any]) -> list[Issue]:
        async with sem:
            ctx.check_cancelled()
            return await critique_chapter(ctx, llm, screenplay, title, scenes, ingest, skip, options)

    results = await gather_all(one(t, ss) for t, ss in groups)
    return issues + [i for r in results for i in r]
