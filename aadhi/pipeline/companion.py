"""Companion sheet: derived study notes + LLM practice problems; Markdown / printable HTML.

Formulas and definitions are derived from board items (``source_item_id`` = ``scene/item``),
misconceptions from ``Screenplay.misconceptions`` (+ board misconception callouts); only the
practice problems are model-written (``prompts/practice.md``, validated against objectives and
source refs). Rendering is done by code: every text is escaped; TeX is shown as source in
``<code>`` (no scripts in the sheet).
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import ProviderError, RateLimited
from ..schemas.screenplay import (
    BoardItemKind,
    BoardScene,
    CompanionSheet,
    DefinitionEntry,
    FormulaEntry,
    MisconceptionEntry,
    PracticeProblem,
    Screenplay,
)
from . import integrations
from .base import ConceptBrief, GenerationOptions, IngestResult
from .gen_models import GenPractice
from .prompting import join_sections, json_section, language_label, section, system_prompt
from .richlite import escape_markdown_html, escape_tex_html, to_html, to_markdown, to_plain
from .scene_context import relevant_chunks

log = logging.getLogger(__name__)

PRACTICE_PROMPTS = ("practice",)
MIN_PROBLEMS, MAX_PROBLEMS = 3, 8


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _norm_tex(latex: str) -> str:
    return re.sub(r"\s+", "", latex or "")


def derive_formulas(sp: Screenplay) -> list[FormulaEntry]:
    out: list[FormulaEntry] = []
    seen: set[str] = set()
    concepts = {c.id: c.title for c in sp.concept_map}
    for s in sp.scenes:
        if not isinstance(s, BoardScene):
            continue
        for item in s.board:
            if item.kind != BoardItemKind.formula or not item.latex:
                continue
            key = _norm_tex(item.latex)
            if key in seen:
                continue
            seen.add(key)
            name = to_plain(item.text).split(":")[0].strip() if item.text else ""
            name = name if name and len(name) <= 80 else (concepts.get(s.concept_id or "") or s.title or "Formula")
            out.append(FormulaEntry(
                name=name[:240], latex=item.latex, description=to_plain(item.text)[:800],
                variables=list(item.variables), source_item_id=f"{s.id}/{item.id}",
            ))
    return out[:50]


def derive_definitions(sp: Screenplay) -> list[DefinitionEntry]:
    out: list[DefinitionEntry] = []
    seen: set[str] = set()
    for s in sp.scenes:
        if not isinstance(s, BoardScene):
            continue
        for item in s.board:
            if item.kind != BoardItemKind.definition:
                continue
            term = to_plain(item.term or "") or to_plain(item.text).split(":")[0][:60]
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            out.append(DefinitionEntry(term=term[:240], definition=to_plain(item.text)[:1200] or term,
                                       source_item_id=f"{s.id}/{item.id}"))
    return out[:50]


def derive_misconceptions(sp: Screenplay) -> list[MisconceptionEntry]:
    out = [MisconceptionEntry(misconception=m.statement[:800], correction=m.correction[:1200]) for m in sp.misconceptions]
    seen = {m.misconception.lower() for m in out}
    for s in sp.scenes:
        if not isinstance(s, BoardScene):
            continue
        for item in s.board:
            if item.kind == BoardItemKind.misconception and not item.misconception_id:
                text = to_plain(item.text)
                if text and text.lower() not in seen and item.justification:
                    seen.add(text.lower())
                    out.append(MisconceptionEntry(misconception=text[:800], correction=to_plain(item.justification)[:1200]))
    return out[:50]


# ---------------------------------------------------------------------------
# Practice problems (LLM)
# ---------------------------------------------------------------------------


def practice_prompt(sp: Screenplay, ingest: IngestResult, options: GenerationOptions,
                    formulas: list[FormulaEntry], definitions: list[DefinitionEntry],
                    skip: frozenset[str] = frozenset()) -> str:
    """The practice-problem prompt (``skip``: chunks the concept brief set aside, never used as source)."""
    refs = list(dict.fromkeys(r for s in sp.scenes for r in ((s.intent.source_refs if s.intent else []))))
    chunks = relevant_chunks(ingest.chunks, refs, sp.session_title, budget=15_000, skip=skip)
    examples = []
    for s in sp.scenes:
        if isinstance(s, BoardScene) and s.type == "example":
            steps = [to_plain(i.text) for i in s.board if i.kind == BoardItemKind.example_step]
            examples.append({"title": s.title, "steps": steps})
    return join_sections(
        json_section("Lecture", {
            "session_title": sp.session_title, "subject": sp.subject_name, "audience": options.audience,
            "depth": options.depth, "language": language_label(sp.board_language or sp.language),
        }),
        json_section("Objectives", [{"key": o.id, "text": o.text, "bloom": o.bloom} for o in sp.learning_objectives]),
        json_section("Formulas", [{"name": f.name, "latex": f.latex} for f in formulas]) if formulas else "",
        json_section("Definitions", [{"term": d.term, "definition": d.definition} for d in definitions]) if definitions else "",
        json_section("Worked examples in the lecture", examples[:6]) if examples else "",
        json_section("Source", [{"id": c.id, "text": c.text} for c in chunks]),
        section("Task", f"Write {MIN_PROBLEMS}-{MAX_PROBLEMS} practice problems with full worked solutions. "
                        "Respond with JSON that matches the response schema."),
    )


def practice_validator(sp: Screenplay, chunk_ids: set[str]):
    objectives = {o.id for o in sp.learning_objectives}
    calls = {"n": 0}

    def validate(out: GenPractice) -> list[str]:
        calls["n"] += 1
        hard: list[str] = []
        if not out.problems:
            hard.append(f"write {MIN_PROBLEMS}-{MAX_PROBLEMS} problems")
        for n, p in enumerate(out.problems, 1):
            if not p.question.strip() or not p.final_answer.strip():
                hard.append(f"problem {n}: needs a question and a final answer")
        if calls["n"] > 1:
            return hard
        soft: list[str] = []
        if not MIN_PROBLEMS <= len(out.problems) <= MAX_PROBLEMS:
            soft.append(f"write between {MIN_PROBLEMS} and {MAX_PROBLEMS} problems (got {len(out.problems)})")
        for n, p in enumerate(out.problems, 1):
            if not p.steps:
                soft.append(f"problem {n}: add the worked solution steps")
            bad = [k for k in p.objective_keys if k not in objectives]
            if bad:
                soft.append(f"problem {n}: unknown objective keys {bad}")
            bad_r = [r for r in p.source_refs if chunk_ids and r not in chunk_ids]
            if bad_r:
                soft.append(f"problem {n}: unknown source refs {bad_r}")
        covered = {k for p in out.problems for k in p.objective_keys}
        missing = [o.id for o in sp.learning_objectives if o.bloom != "remember" and o.id not in covered]
        if missing:
            soft.append(f"cover these objectives with at least one problem each: {missing}")
        if out.problems and len({p.difficulty for p in out.problems}) == 1 and len(out.problems) >= 3:
            soft.append("mix difficulties (easy, medium, hard)")
        return hard + soft

    return validate


def to_practice(out: GenPractice, sp: Screenplay, chunk_ids: set[str]) -> list[PracticeProblem]:
    objectives = {o.id for o in sp.learning_objectives}
    problems = []
    for n, p in enumerate(out.problems[:MAX_PROBLEMS], 1):
        problems.append(PracticeProblem(
            id=f"p{n}", question=p.question[:2400], steps=[s[:600] for s in p.steps][:12],
            final_answer=p.final_answer[:1200], hints=[h[:400] for h in p.hints][:4], difficulty=p.difficulty,
            objective_ids=[k for k in p.objective_keys if k in objectives][:6],
            source_refs=[r for r in p.source_refs if not chunk_ids or r in chunk_ids][:10],
        ))
    return problems


async def generate_practice(ctx: Any, sp: Screenplay, ingest: IngestResult, options: GenerationOptions,
                            formulas: list[FormulaEntry], definitions: list[DefinitionEntry],
                            skip: frozenset[str] = frozenset()) -> list[PracticeProblem]:
    """Model-written practice problems ([] when generation fails)."""
    chunk_ids = {c.id for c in ingest.chunks}
    llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    try:
        async with integrations.limit("llm"):
            out: GenPractice = await llm.generate_json(
                model=integrations.llm_model(ctx.settings, "script", options, override=options.llm_model_script),
                system=system_prompt(*PRACTICE_PROMPTS),
                prompt=practice_prompt(sp, ingest, options, formulas, definitions, skip),
                schema=GenPractice, temperature=0.5, on_usage=ctx.record_usage,
                validate=practice_validator(sp, chunk_ids), validation_retries=2,
            )
    except (JobCancelled, BudgetExceeded, RateLimited):
        raise
    except Exception as exc:  # noqa: BLE001 - provider errors and checker bugs: the sheet is still useful
        if not isinstance(exc, ProviderError):
            log.warning("practice generation raised %s", type(exc).__name__, exc_info=True)
        ctx.log(f"Practice problems could not be generated: {ctx.settings.redact(str(exc))[:200]}", "warning")
        return []
    return to_practice(out, sp, chunk_ids)


async def build_sheet(ctx: Any, screenplay: Screenplay, ingest: IngestResult, options: GenerationOptions, *,
                      brief: ConceptBrief | None = None) -> CompanionSheet:
    """Companion sheet for the screenplay (contract entry point); the chunks the concept ``brief`` set aside are
    never used as source for the practice problems."""
    formulas = derive_formulas(screenplay)
    definitions = derive_definitions(screenplay)
    skip = frozenset(brief.skipped_chunk_ids()) if brief is not None else frozenset()
    practice = await generate_practice(ctx, screenplay, ingest, options, formulas, definitions, skip)
    return CompanionSheet(key_formulas=formulas, definitions=definitions,
                          misconceptions=derive_misconceptions(screenplay), practice_problems=practice)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _md(text: str) -> str:
    return to_markdown(text or "")


def _cell(text: str) -> str:
    """Markdown table cell: pipes escaped, kept on one line."""
    return re.sub(r"\s+", " ", (text or "").replace("|", "\\|")).strip()


def _title(sp: Screenplay) -> str:
    return sp.session_title or sp.subject_name or "Lecture"


def render_markdown(screenplay: Screenplay) -> str:
    """Companion sheet as Markdown (tag-like ``<`` neutralised everywhere, TeX included; TeX in ``$$`` blocks)."""
    sp, sheet = screenplay, screenplay.companion_sheet
    lines: list[str] = [f"# {escape_markdown_html(_title(sp))}"]
    meta = " · ".join(x for x in (sp.subject_name, sp.unit_name, sp.session_number) if x)
    if meta:
        lines += ["", escape_markdown_html(meta)]
    if sp.learning_objectives:
        lines += ["", "## Learning objectives", ""] + [f"- {_md(o.text)}" for o in sp.learning_objectives]
    if sheet.key_formulas:
        lines += ["", "## Key formulas"]
        for f in sheet.key_formulas:
            lines += ["", f"### {escape_markdown_html(f.name)}"]
            if f.latex:
                lines += ["", "$$", escape_tex_html(f.latex), "$$"]
            if f.description and f.description != f.name:
                lines += ["", _md(f.description)]
            if f.variables:
                lines += ["", "| Symbol | Meaning | Unit |", "|---|---|---|"]
                lines += [f"| ${_cell(escape_tex_html(v.symbol_latex))}$ | {_cell(escape_markdown_html(v.meaning))} | "
                          f"{_cell(escape_markdown_html(v.unit))} |" for v in f.variables]
    if sheet.definitions:
        lines += ["", "## Definitions", ""]
        lines += [f"- **{escape_markdown_html(d.term)}** — {_md(d.definition)}" for d in sheet.definitions]
    if sheet.misconceptions:
        lines += ["", "## Common misconceptions", ""]
        for m in sheet.misconceptions:
            lines += [f"- **Misconception:** {_md(m.misconception)}", f"  **Actually:** {_md(m.correction)}"]
    if sheet.practice_problems:
        lines += ["", "## Practice problems", ""]
        for n, p in enumerate(sheet.practice_problems, 1):
            lines.append(f"{n}. {_md(p.question)} *({p.difficulty})*")
            for h in p.hints:
                lines.append(f"   - Hint: {_md(h)}")
        lines += ["", "## Answer key", ""]
        for n, p in enumerate(sheet.practice_problems, 1):
            lines.append(f"{n}. **{_md(p.final_answer)}**")
            lines += [f"   {k}. {_md(step)}" for k, step in enumerate(p.steps, 1)]
    if sheet.legacy_markdown and not (sheet.key_formulas or sheet.definitions or sheet.practice_problems):
        lines += ["", escape_markdown_html(sheet.legacy_markdown)]
    return "\n".join(lines).rstrip() + "\n"


_CSS = """
:root{--ink:#1d1430;--muted:#5d5470;--accent:#6b2bd6;--gold:#b98a14;--line:#e3dcef;--bg:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "Inter","Noto Sans",
"Noto Sans Tamil","Noto Sans Devanagari",system-ui,sans-serif}main{max-width:820px;margin:0 auto;padding:32px 24px 48px}
header{border-bottom:3px solid var(--accent);padding-bottom:12px;margin-bottom:20px}header .brand{color:var(--accent);
font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:12px}h1{margin:6px 0 4px;font-size:26px}
.meta{color:var(--muted)}h2{color:var(--accent);border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:28px}
h3{margin:18px 0 6px}code{font-family:"JetBrains Mono",Consolas,monospace;background:#f4f0fb;padding:1px 4px;border-radius:4px}
code.tex{color:#3b1d7a}.formula{display:block;padding:8px 12px;margin:6px 0;background:#f8f5fd;border-left:3px solid var(--gold);
white-space:pre-wrap;word-break:break-word}table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid var(--line);
padding:4px 8px;text-align:left}.mis{margin:10px 0;padding:8px 12px;border:1px solid var(--line);border-radius:8px}
.mis b{color:#a12a3a}.mis i{color:#1d7a46;font-style:normal;font-weight:600}ol li{margin:8px 0}.diff{color:var(--muted);
font-size:12px;text-transform:uppercase}.hint{color:var(--muted);font-size:13px}mark{background:#fff1c2}
.answers{page-break-before:always}footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
@media print{main{padding:0}h2{page-break-after:avoid}.mis,li{page-break-inside:avoid}}
"""


def _e(text: str | None) -> str:
    return html.escape(text or "", quote=True)


def render_html(screenplay: Screenplay) -> str:
    """Self-contained printable companion page (all text escaped; no scripts)."""
    sp, sheet = screenplay, screenplay.companion_sheet
    lang = _e((sp.board_language or sp.language or "en").split("-")[0])
    parts: list[str] = [
        "<!doctype html>", f'<html lang="{lang}">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(_title(sp))} — Companion sheet</title>", f"<style>{_CSS}</style>", "</head>", "<body><main>",
        "<header>", '<div class="brand">REC · Aadhi EduEngine · Companion sheet</div>', f"<h1>{_e(_title(sp))}</h1>",
    ]
    meta = " · ".join(x for x in (sp.subject_name, sp.unit_name, sp.session_number) if x)
    if meta:
        parts.append(f'<div class="meta">{_e(meta)}</div>')
    parts.append("</header>")
    if sp.learning_objectives:
        parts += ["<section>", "<h2>Learning objectives</h2>", "<ul>"]
        parts += [f"<li>{to_html(o.text)}</li>" for o in sp.learning_objectives]
        parts += ["</ul>", "</section>"]
    if sheet.key_formulas:
        parts += ["<section>", "<h2>Key formulas</h2>"]
        for f in sheet.key_formulas:
            parts.append(f"<h3>{_e(f.name)}</h3>")
            if f.latex:
                parts.append(f'<code class="tex formula">{_e(f.latex)}</code>')
            if f.description and f.description != f.name:
                parts.append(f"<p>{to_html(f.description)}</p>")
            if f.variables:
                parts.append("<table><tr><th>Symbol</th><th>Meaning</th><th>Unit</th></tr>")
                parts += [f'<tr><td><code class="tex">{_e(v.symbol_latex)}</code></td><td>{_e(v.meaning)}</td>'
                          f"<td>{_e(v.unit)}</td></tr>" for v in f.variables]
                parts.append("</table>")
        parts.append("</section>")
    if sheet.definitions:
        parts += ["<section>", "<h2>Definitions</h2>", "<dl>"]
        for d in sheet.definitions:
            parts += [f"<dt><strong>{_e(d.term)}</strong></dt>", f"<dd>{to_html(d.definition)}</dd>"]
        parts += ["</dl>", "</section>"]
    if sheet.misconceptions:
        parts += ["<section>", "<h2>Common misconceptions</h2>"]
        for m in sheet.misconceptions:
            parts.append(f'<div class="mis"><b>Misconception:</b> {to_html(m.misconception)}<br>'
                         f"<i>Actually:</i> {to_html(m.correction)}</div>")
        parts.append("</section>")
    if sheet.practice_problems:
        parts += ["<section>", "<h2>Practice problems</h2>", "<ol>"]
        for p in sheet.practice_problems:
            hints = "".join(f'<div class="hint">Hint: {to_html(h)}</div>' for h in p.hints)
            parts.append(f'<li>{to_html(p.question)} <span class="diff">{_e(p.difficulty)}</span>{hints}</li>')
        parts += ["</ol>", "</section>", '<section class="answers">', "<h2>Answer key</h2>", "<ol>"]
        for p in sheet.practice_problems:
            steps = "".join(f"<li>{to_html(s)}</li>" for s in p.steps)
            parts.append(f"<li><strong>{to_html(p.final_answer)}</strong>" + (f"<ol>{steps}</ol>" if steps else "") + "</li>")
        parts += ["</ol>", "</section>"]
    if sheet.legacy_markdown and not (sheet.key_formulas or sheet.definitions or sheet.practice_problems):
        parts += ["<section>", f'<pre style="white-space:pre-wrap">{_e(sheet.legacy_markdown)}</pre>', "</section>"]
    parts += ["<footer>Rajalakshmi Engineering College · generated with Aadhi EduEngine</footer>", "</main></body></html>"]
    return "\n".join(parts) + "\n"
