"""Optional LLM-as-judge scoring of generated lectures (real providers only).

Deterministic metrics catch structural problems; the judge estimates what they cannot (accuracy,
clarity, engagement) with a fixed rubric. Scores are 1-5 per criterion; the overall score is the
mean computed here, never by the model. Judge output is advisory and non-deterministic: compare
judge results only between runs judged with the same model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..jobs.base import BudgetExceeded
from ..providers.base import LLMProvider
from ..schemas.screenplay import BoardScene, ChapterCardScene, QuizScene, Screenplay, SimulationScene
from .context import CostMeter
from .metrics import plain_text
from .report import md_cell

logger = logging.getLogger(__name__)

JUDGE_VERSION = "1"
JUDGE_JSON = "judge.json"
JUDGE_MD = "judge.md"
CRITERIA = ("accuracy", "grounding", "clarity", "structure", "pedagogy", "engagement", "assessment", "pacing")

JUDGE_SYSTEM = """You are an experienced engineering professor reviewing a lecture video script \
written for undergraduate students. Score it against the rubric using the source excerpt as ground \
truth. Be strict and specific: a 5 means you would publish it unchanged, a 3 means usable after \
edits, a 1 means it would mislead or lose students. Justify every score with concrete scene \
references. Do not reward length."""

RUBRIC = """Rubric (score each 1-5):
- accuracy: statements, formulas, units and worked numbers are correct.
- grounding: claims are supported by the source excerpt; nothing important is invented or contradicted.
- clarity: explanations suit the stated audience; new terms are defined before use; board text is concise.
- structure: logical order, prerequisites before use, clear signposting and summaries.
- pedagogy: worked examples (with faded steps), retrieval practice, misconceptions explicitly addressed.
- engagement: motivating hooks, real-world relevance, conversational narration.
- assessment: quiz questions test the learning objectives; distractors reflect real misconceptions.
- pacing: amount of material fits the target duration; no rushed or padded sections."""


class CriterionScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=1, le=5)
    justification: str = Field(max_length=800)


class JudgeVerdict(BaseModel):
    """LLM response schema (no unions, ids or open dicts: provider JSON-schema compatible)."""

    model_config = ConfigDict(extra="ignore")

    accuracy: CriterionScore
    grounding: CriterionScore
    clarity: CriterionScore
    structure: CriterionScore
    pedagogy: CriterionScore
    engagement: CriterionScore
    assessment: CriterionScore
    pacing: CriterionScore
    strengths: list[str] = Field(default_factory=list, max_length=5)
    weaknesses: list[str] = Field(default_factory=list, max_length=5)
    top_fixes: list[str] = Field(default_factory=list, max_length=5)


def overall_score(verdict: JudgeVerdict) -> float:
    """Unweighted mean of the criterion scores (1-5)."""
    return round(sum(getattr(verdict, c).score for c in CRITERIA) / len(CRITERIA), 3)


def _board_line(item: Any) -> str:
    kind = item.kind.value
    if kind == "formula":
        return f"formula: {item.latex}" + (f" ({plain_text(item.text)})" if item.text else "")
    if kind == "definition":
        return f"definition: {plain_text(item.term or '')}: {plain_text(item.text)}"
    if kind == "table":
        rows = "; ".join(" | ".join(r) for r in (item.rows or [])[:6])
        return f"table: {' | '.join(item.headers or [])} :: {rows}"
    if kind == "code":
        return f"code ({item.language}): {(item.code or '').strip()[:300]}"
    if kind == "figure":
        return f"figure {item.figure_id}: {plain_text(item.caption or '')}"
    extra = f" [why: {plain_text(item.justification)}]" if item.justification else ""
    blank = " [blank, filled later]" if item.blank else ""
    return f"{kind}{blank}: {plain_text(item.text)}{extra}"


def render_outline(sp: Screenplay, max_chars: int = 60_000) -> str:
    """Compact plain-text rendering of a screenplay for the judge prompt."""
    lines = [
        f"LECTURE: {sp.subject_name} / {sp.unit_name} / {sp.session_number}: {sp.session_title} ({sp.language})",
        "OBJECTIVES:",
        *(f"- [{o.id}] ({o.bloom}) {o.text}" for o in sp.learning_objectives),
        "MISCONCEPTIONS TO ADDRESS:",
        *(f"- [{m.id}] {m.statement} -> {m.correction}" for m in sp.misconceptions),
        "SCENES:",
    ]
    for n, s in enumerate(sp.scenes, start=1):
        meta = ", ".join(x for x in (f"concept {s.concept_id}" if s.concept_id else "",
                                     f"objectives {','.join(s.objective_ids)}" if s.objective_ids else "") if x)
        lines.append(f"## {n}. [{s.type}] {s.title}" + (f" ({meta})" if meta else ""))
        if isinstance(s, ChapterCardScene):
            lines.append(f"(chapter card: {s.chapter_label})")
        if isinstance(s, BoardScene) and s.board:
            lines.append("Board:")
            lines.extend(f"  - {_board_line(i)}" for i in s.board)
        if isinstance(s, SimulationScene):
            spec = s.manim.template or "free-form code"
            lines.append(f"Animation: {spec}")
        if s.side_panel is not None:
            panel = s.side_panel
            lines.append(f"Panel: {panel.kind} - {panel.title or ''} (why: {panel.rationale or '-'})")
        if isinstance(s, QuizScene):
            opts = " ".join(
                f"{chr(65 + i)}) {o}{' [correct]' if i == s.correct_index else ''}" for i, o in enumerate(s.options)
            )
            lines.append(f"Quiz ({s.bloom}): {s.question} {opts}")
        lines.append("Narration:")
        for b in s.all_beats():
            cue = f" [reveals {b.board_item_id}]" if b.board_item_id else ""
            lines.append(f"  > {b.narration}{cue}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... outline truncated ...]"
    return text


def build_prompt(sp: Screenplay, source_excerpt: str, target_minutes: int | None) -> str:
    """User prompt for one lecture."""
    target = f"Target duration: {target_minutes} minutes.\n" if target_minutes else ""
    return (
        f"{RUBRIC}\n\n{target}"
        f"SOURCE EXCERPT (ground truth, may be truncated):\n<<<\n{source_excerpt}\n>>>\n\n"
        f"LECTURE SCRIPT:\n<<<\n{render_outline(sp)}\n>>>\n\n"
        "Return the scores, three to five strengths and weaknesses, and the most valuable fixes."
    )


def _not_judgeable(entry: dict[str, Any]) -> str | None:
    """Why a report entry must not be judged (``None`` = judge it).

    Only lectures this run produced and finished count: a failed fixture's directory may hold a
    partial screenplay, and judging files the report does not list could score an older run.
    """
    status = entry.get("status")
    if status != "ok":
        stage = entry.get("failed_stage")
        return f"fixture {status}" + (f" at {stage}" if stage else "")
    if "screenplay.json" not in (entry.get("artifacts") or []):
        return "this run wrote no screenplay.json"
    return None


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


async def judge_fixture(
    llm: LLMProvider,
    *,
    model: str,
    screenplay: Screenplay,
    source_excerpt: str,
    target_minutes: int | None,
    meter: CostMeter,
) -> JudgeVerdict:
    """Score one lecture."""
    return await llm.generate_json(
        model=model,
        system=JUDGE_SYSTEM,
        prompt=build_prompt(screenplay, source_excerpt, target_minutes),
        schema=JudgeVerdict,
        temperature=0.0,
        on_usage=meter,
    )


async def judge_run(
    run_dir: Path,
    llm: LLMProvider,
    settings: Settings,
    *,
    model: str | None = None,
    budget_usd: float | None = None,
    only: Iterable[str] = (),
    max_source_chars: int = 12_000,
    engine: str | None = None,
) -> dict[str, Any]:
    """Judge every fixture that finished ok in this run (see ``_not_judgeable``); writes judge.json + judge.md.

    ``model`` defaults to the critic model of ``engine`` (the AI engine ``llm`` belongs to; None =
    ``LLM_PROVIDER``).
    """
    run_dir = Path(run_dir)
    report = await asyncio.to_thread(_read_json, run_dir / "report.json")
    wanted = set(only)
    meter = CostMeter(settings, budget_usd=budget_usd if budget_usd is not None else settings.max_cost_per_lecture_usd)
    if not model:
        from ..providers.factory import llm_models

        model = llm_models(settings, engine)["critic"]
    results: list[dict[str, Any]] = []
    for fx in report.get("fixtures", []):
        name = fx["name"]
        if wanted and name not in wanted:
            continue
        reason = _not_judgeable(fx)
        sp_path = run_dir / name / "screenplay.json"
        if reason is None and not sp_path.is_file():
            reason = "no screenplay.json"
        if reason is not None:
            results.append({"name": name, "status": "skipped", "error": reason})
            continue
        sp = Screenplay.model_validate(await asyncio.to_thread(_read_json, sp_path))
        ingest_path = run_dir / name / "ingest.json"
        excerpt = ""
        if ingest_path.is_file():
            excerpt = str((await asyncio.to_thread(_read_json, ingest_path)).get("markdown", ""))[:max_source_chars]
        meter.stage = f"judge:{name}"
        try:
            verdict = await judge_fixture(
                llm,
                model=model,
                screenplay=sp,
                source_excerpt=excerpt or "(no source: judge accuracy from general knowledge)",
                target_minutes=(fx.get("options") or {}).get("target_minutes"),
                meter=meter,
            )
        except BudgetExceeded as exc:
            results.append({"name": name, "status": "failed", "error": str(exc)})
            break
        except Exception as exc:
            results.append({"name": name, "status": "failed", "error": settings.redact(f"{type(exc).__name__}: {exc}")})
            continue
        results.append({
            "name": name,
            "status": "ok",
            "overall": overall_score(verdict),
            "verdict": verdict.model_dump(mode="json"),
        })
    scored = [r["overall"] for r in results if r.get("status") == "ok"]
    out = {
        "judge_version": JUDGE_VERSION,
        "run": report.get("name"),
        "model": model,
        "provider": getattr(llm, "name", engine or settings.llm_provider),
        "overall_mean": round(sum(scored) / len(scored), 3) if scored else None,
        "criteria_means": criteria_means(results),
        "cost_usd": round(meter.total_usd, 6),
        "fixtures": results,
    }
    await asyncio.to_thread(_write_outputs, run_dir, out)
    return out


def render_judge_markdown(result: dict[str, Any]) -> str:
    """Markdown table of judge scores."""
    lines = [
        f"# Judge report: {md_cell(result.get('run'))}",
        "",
        f"- Model `{result.get('provider')}/{result.get('model')}`, cost ${result.get('cost_usd', 0):.4f},"
        f" overall mean {result.get('overall_mean')}",
        "",
        "| Fixture | Overall | " + " | ".join(c.capitalize() for c in CRITERIA) + " |",
        "|---|---:|" + "---:|" * len(CRITERIA),
    ]
    for r in result.get("fixtures", []):
        if r.get("status") != "ok":
            lines.append(f"| {md_cell(r['name'])} | {r['status']}: {md_cell(r.get('error'))} |" + " |" * len(CRITERIA))
            continue
        v = r["verdict"]
        scores = " | ".join(str(v[c]["score"]) for c in CRITERIA)
        lines.append(f"| {md_cell(r['name'])} | {r['overall']} | {scores} |")
    for r in result.get("fixtures", []):
        if r.get("status") != "ok":
            continue
        v = r["verdict"]
        lines += ["", f"## {md_cell(r['name'])}", ""]
        for c in CRITERIA:
            lines.append(f"- **{c}** {v[c]['score']}/5: {md_cell(v[c]['justification'])}")
        for key, label in (("strengths", "Strengths"), ("weaknesses", "Weaknesses"), ("top_fixes", "Top fixes")):
            if v.get(key):
                lines.append(f"- {label}: " + "; ".join(md_cell(x) for x in v[key]))
    return "\n".join(lines) + "\n"


def _write_outputs(run_dir: Path, result: dict[str, Any]) -> None:
    (run_dir / JUDGE_JSON).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / JUDGE_MD).write_text(render_judge_markdown(result), encoding="utf-8")


def criteria_means(results: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Mean score per criterion over judged fixtures."""
    ok = [r["verdict"] for r in results if r.get("status") == "ok"]
    if not ok:
        return {}
    return {c: round(sum(v[c]["score"] for v in ok) / len(ok), 3) for c in CRITERIA}
