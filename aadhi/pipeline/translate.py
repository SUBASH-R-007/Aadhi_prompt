"""Translate a screenplay into another language (new version; ids preserved).

Per scene, the translatable fields (``TRANSLATABLE_FIELDS``) are sent as ``{path, text}`` items.
Narration is always translated; on-screen text (titles, board, quiz, panels, objectives,
companion sheet) only when ``translate_board``. Maths/code spans and ``keep_in_english`` lexicon
terms are masked as ``⟦n⟧`` placeholders and verified on the way back; protected fields (ids,
LaTeX, code, expressions, figure ids, Manim, p5) are never sent. The calls use the scene-writing
model of the source version's AI engine (``options``). A provider error keeps that scene's source
text (plus an issue), except a provider refusing the job owner's personal API key: that fails the
job (``personal_key_rejected``) instead of shipping an untranslated "translation".
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..credentials import personal_key_rejection
from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import ProviderError, RateLimited
from ..schemas.screenplay import Screenplay
from . import integrations
from .aio import gather_all
from .base import SUPPORTED_LANGUAGES, GenerationOptions, Issue
from .canonicalize import SCENE_ADAPTER, format_validation_error
from .gen_models import GenTranslation
from .prompting import join_sections, json_section, language_label, section, system_prompt
from .richlite import PLACEHOLDER_RE, markup_balance_problems, mask, placeholder_problems, unmask
from .scene_checks import language_problem

log = logging.getLogger(__name__)

TRANSLATE_PROMPTS = ("translate",)
BATCH_FIELDS = 120

_NARRATION = ("beats", "reveal_beats")
_BOARD_ITEM_FIELDS = ("text", "term", "caption", "justification")
_SCENE_VISIBLE = ("title", "subtitle", "chapter_label", "question", "explanation")


@dataclass
class FieldItem:
    path: str
    masked: str
    originals: list[str]


@dataclass
class TranslateResult:
    screenplay: Screenplay
    issues: list[Issue] = field(default_factory=list)


def _add(items: list[FieldItem], path: str, value: Any, keep: list[str]) -> None:
    if isinstance(value, str) and value.strip():
        masked, originals = mask(value, keep)
        if re.sub(r"⟦\d+⟧|\W", "", masked):  # nothing left to translate -> skip
            items.append(FieldItem(path, masked, originals))


def scene_fields(scene: dict[str, Any], translate_board: bool, keep: list[str]) -> list[FieldItem]:
    """Translatable fields of a scene dict (JSON mode) as path items."""
    items: list[FieldItem] = []
    for key in _NARRATION:
        for i, b in enumerate(scene.get(key) or []):
            _add(items, f"{key}[{i}].narration", b.get("narration"), keep)
            _add(items, f"{key}[{i}].spoken", b.get("spoken"), keep)
    if not translate_board:
        return items
    for key in _SCENE_VISIBLE:
        _add(items, key, scene.get(key), keep)
    for i, it in enumerate(scene.get("board") or []):
        for f in _BOARD_ITEM_FIELDS:
            _add(items, f"board[{i}].{f}", it.get(f), keep)
        for j, h in enumerate(it.get("headers") or []):
            _add(items, f"board[{i}].headers[{j}]", h, keep)
        for r, row in enumerate(it.get("rows") or []):
            for c, cell in enumerate(row):
                _add(items, f"board[{i}].rows[{r}][{c}]", cell, keep)
        for j, v in enumerate(it.get("variables") or []):
            _add(items, f"board[{i}].variables[{j}].meaning", v.get("meaning"), keep)
    for i, opt in enumerate(scene.get("options") or []):
        _add(items, f"options[{i}]", opt, keep)
    for i, fb in enumerate(scene.get("feedback_wrong") or []):
        _add(items, f"feedback_wrong[{i}]", fb, keep)
    panel = scene.get("side_panel") or {}
    _add(items, "side_panel.title", panel.get("title"), keep)
    chart = panel.get("chart") or {}
    for i, lab in enumerate(chart.get("labels") or []):
        _add(items, f"side_panel.chart.labels[{i}]", lab, keep)
    for i, ds in enumerate(chart.get("datasets") or []):
        _add(items, f"side_panel.chart.datasets[{i}].label", ds.get("label"), keep)
    for f in ("x_label", "y_label"):
        _add(items, f"side_panel.chart.{f}", chart.get(f), keep)
    quiz = panel.get("quiz") or {}
    _add(items, "side_panel.quiz.question", quiz.get("question"), keep)
    for i, opt in enumerate(quiz.get("options") or []):
        _add(items, f"side_panel.quiz.options[{i}]", opt, keep)
    _add(items, "side_panel.terminal.output", (panel.get("terminal") or {}).get("output"), keep)
    return items


def screenplay_fields(sp: dict[str, Any], keep: list[str]) -> list[FieldItem]:
    """Lecture-level on-screen/printed fields (objectives, concepts, misconceptions, chapters, companion)."""
    items: list[FieldItem] = []
    _add(items, "session_title", sp.get("session_title"), keep)
    for i, o in enumerate(sp.get("learning_objectives") or []):
        _add(items, f"learning_objectives[{i}].text", o.get("text"), keep)
    for i, m in enumerate(sp.get("misconceptions") or []):
        _add(items, f"misconceptions[{i}].statement", m.get("statement"), keep)
        _add(items, f"misconceptions[{i}].correction", m.get("correction"), keep)
    for i, c in enumerate(sp.get("concept_map") or []):
        _add(items, f"concept_map[{i}].title", c.get("title"), keep)
        _add(items, f"concept_map[{i}].summary", c.get("summary"), keep)
    for i, c in enumerate(sp.get("chapters") or []):
        _add(items, f"chapters[{i}].title", c.get("title"), keep)
    sheet = sp.get("companion_sheet") or {}
    for i, f in enumerate(sheet.get("key_formulas") or []):
        _add(items, f"companion_sheet.key_formulas[{i}].name", f.get("name"), keep)
        _add(items, f"companion_sheet.key_formulas[{i}].description", f.get("description"), keep)
    for i, d in enumerate(sheet.get("definitions") or []):
        _add(items, f"companion_sheet.definitions[{i}].term", d.get("term"), keep)
        _add(items, f"companion_sheet.definitions[{i}].definition", d.get("definition"), keep)
    for i, m in enumerate(sheet.get("misconceptions") or []):
        _add(items, f"companion_sheet.misconceptions[{i}].misconception", m.get("misconception"), keep)
        _add(items, f"companion_sheet.misconceptions[{i}].correction", m.get("correction"), keep)
    for i, p in enumerate(sheet.get("practice_problems") or []):
        _add(items, f"companion_sheet.practice_problems[{i}].question", p.get("question"), keep)
        _add(items, f"companion_sheet.practice_problems[{i}].final_answer", p.get("final_answer"), keep)
        for j, s in enumerate(p.get("steps") or []):
            _add(items, f"companion_sheet.practice_problems[{i}].steps[{j}]", s, keep)
        for j, h in enumerate(p.get("hints") or []):
            _add(items, f"companion_sheet.practice_problems[{i}].hints[{j}]", h, keep)
    return items


_PATH_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def set_path(doc: Any, path: str, value: str) -> None:
    """Set ``doc[...]`` at a path like ``board[2].rows[0][1]`` (raises KeyError/IndexError)."""
    tokens = [m.group(1) if m.group(1) is not None else int(m.group(2)) for m in _PATH_TOKEN.finditer(path)]
    if not tokens:
        raise KeyError(path)
    cur = doc
    for tok in tokens[:-1]:
        cur = cur[tok]
    cur[tokens[-1]] = value


def make_validator(items: list[FieldItem], target: str | None = None):
    """Paths, emptiness, placeholders and markup always; on the first pass also the target script.

    The script check (``scene_checks.language_problem``) catches a model that answers in the source
    language; it is asked again once, after that the answer is accepted as is.
    """
    wanted = {i.path: i for i in items}
    calls = {"n": 0}

    def validate(out: GenTranslation) -> list[str]:
        calls["n"] += 1
        problems: list[str] = []
        got = {}
        for it in out.items:
            if it.path in got:
                problems.append(f"{it.path}: translated twice")
            got[it.path] = it.text
        missing = [p for p in wanted if p not in got]
        if missing:
            problems.append(f"missing translations for {missing[:10]}")
        extra = [p for p in got if p not in wanted]
        if extra:
            problems.append(f"unknown paths {extra[:10]} (translate exactly the given paths)")
        for path, text in got.items():
            src = wanted.get(path)
            if src is None:
                continue
            if not text.strip():
                problems.append(f"{path}: translation is empty")
                continue
            for p in placeholder_problems(text, len(src.originals)) + markup_balance_problems(text):
                problems.append(f"{path}: {p} (keep every ⟦n⟧ placeholder exactly once)")
        if target and calls["n"] == 1 and not problems:
            untranslated = [path for path, text in got.items() if path in wanted
                            and language_problem(PLACEHOLDER_RE.sub(" ", text), target, path)]
            if untranslated:
                problems.append(f"these fields are not in {language_label(target)}: {untranslated[:10]} "
                                "(translate them; only the Keep in English terms stay in English)")
        return problems

    return validate


def translate_model(options: GenerationOptions | None, settings: Any) -> str:
    """The scene-writing model of the lecture's engine (an admin's ``llm_model_script`` override for it wins)."""
    return integrations.llm_model(settings, "script", options,
                                  override=options.llm_model_script if options is not None else None)


def translate_prompt(items: list[FieldItem], source_lang: str, target: str, keep: list[str], lexicon: list[dict],
                     what: str) -> str:
    return join_sections(
        json_section("Request", {
            "source_language": language_label(source_lang), "target_language": language_label(target), "content": what,
        }),
        json_section("Keep in English", keep) if keep else "",
        json_section("Glossary", lexicon) if lexicon else "",
        json_section("Fields", [{"path": i.path, "text": i.masked} for i in items]),
        section("Task", "Translate every field. Respond with JSON that matches the response schema."),
    )


async def _translate_items(ctx: Any, llm: Any, items: list[FieldItem], source_lang: str, target: str, keep: list[str],
                           lexicon: list[dict], what: str, model: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for start in range(0, len(items), BATCH_FIELDS):
        batch = items[start:start + BATCH_FIELDS]
        async with integrations.limit("llm"):
            res: GenTranslation = await llm.generate_json(
                model=model or translate_model(None, ctx.settings), system=system_prompt(*TRANSLATE_PROMPTS),
                prompt=translate_prompt(batch, source_lang, target, keep, lexicon, what), schema=GenTranslation,
                temperature=0.2, on_usage=ctx.record_usage, validate=make_validator(batch, target), validation_retries=2,
            )
        by_path = {i.path: i for i in batch}
        for it in res.items:
            if it.path in by_path:
                out[it.path] = unmask(it.text.strip(), by_path[it.path].originals)
    return out


async def translate_screenplay(
    ctx: Any,
    screenplay: Screenplay,
    target_language: str,
    *,
    translate_board: bool = False,
    progress: tuple[float, float] | None = None,
    options: GenerationOptions | None = None,
) -> TranslateResult:
    """Translated copy of ``screenplay`` (failures keep the source text of that scene + an issue).

    ``options``: the source version's options; their AI engine (and an admin's script-model override
    for it) makes the calls (None = the server default engine). A refused personal API key propagates.
    """
    if target_language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language {target_language!r}")
    source_lang = screenplay.language
    keep = sorted({e.written for e in screenplay.lexicon if e.keep_in_english})
    lexicon = [{"written": e.written, "spoken": e.spoken} for e in screenplay.lexicon][:60]
    llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    model = translate_model(options, ctx.settings)
    data = screenplay.model_dump(mode="json")
    issues: list[Issue] = []
    sem = asyncio.Semaphore(max(1, int(ctx.settings.llm_max_parallel)))
    done = {"n": 0}
    lo, hi = progress or (0.0, 1.0)
    total = len(data["scenes"]) + (1 if translate_board else 0)
    what_scene = "narration and on-screen text of one lecture scene" if translate_board else "narration of one lecture scene"

    async def one_scene(idx: int) -> None:
        scene = data["scenes"][idx]
        items = scene_fields(scene, translate_board, keep)
        if items:
            async with sem:
                ctx.check_cancelled()
                try:
                    values = await _translate_items(ctx, llm, items, source_lang, target_language, keep, lexicon, what_scene,
                                                    model)
                except (JobCancelled, BudgetExceeded, RateLimited):
                    raise
                except ProviderError as exc:
                    if personal_key_rejection(exc, getattr(ctx, "key_sources", None) or {}):
                        raise  # the owner's own key was refused: fail the job, don't ship untranslated text
                    issues.append(Issue(code="translate.scene_failed", severity="error", scene_id=scene["id"], source="system",
                                        fixable=False, message=f"Translation failed ({ctx.settings.redact(str(exc))[:160]}); "
                                        "this scene is still in the source language."))
                    values = {}
            candidate = _apply(scene, values)
            try:
                data["scenes"][idx] = SCENE_ADAPTER.validate_python(candidate).model_dump(mode="json")
            except ValidationError as exc:
                issues.append(Issue(code="translate.scene_failed", severity="error", scene_id=scene["id"], source="system",
                                    fixable=False, message="The translated scene was invalid ("
                                    + "; ".join(format_validation_error(exc, 2)) + "); the source text was kept."))
        done["n"] += 1
        ctx.progress("translate", lo + (hi - lo) * done["n"] / max(1, total), f"Translating scene {done['n']}/{total}")

    await gather_all(one_scene(i) for i in range(len(data["scenes"])))
    if translate_board:
        items = screenplay_fields(data, keep)
        if items:
            try:
                values = await _translate_items(ctx, llm, items, source_lang, target_language, keep, lexicon,
                                                "lecture objectives, concepts, misconceptions, chapter titles and study sheet",
                                                model)
                data = _apply(data, values)
            except (JobCancelled, BudgetExceeded, RateLimited):
                raise
            except ProviderError as exc:
                if personal_key_rejection(exc, getattr(ctx, "key_sources", None) or {}):
                    raise
                issues.append(Issue(code="translate.sheet_failed", severity="warning", source="system", fixable=False,
                                    message=f"Lecture-level texts were not translated ({ctx.settings.redact(str(exc))[:160]})."))
    data["language"] = target_language
    data["board_language"] = target_language if translate_board else (screenplay.board_language or source_lang)
    if data["board_language"] == target_language:
        data["board_language"] = None
    try:
        result = Screenplay.model_validate(data)
    except ValidationError as exc:  # lecture-level texts broke a limit: keep the source versions of those
        log.warning("translated screenplay invalid: %s", format_validation_error(exc, 3))
        base = screenplay.model_dump(mode="json")
        base.update(scenes=data["scenes"], language=data["language"], board_language=data["board_language"])
        result = Screenplay.model_validate(base)
        issues.append(Issue(code="translate.sheet_failed", severity="warning", source="system", fixable=False,
                            message="Some lecture-level texts were too long after translation and were kept in the source language."))
    return TranslateResult(result, issues)


def _apply(doc: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    import copy

    out = copy.deepcopy(doc)
    for path, text in values.items():
        try:
            set_path(out, path, text)
        except (KeyError, IndexError, TypeError):
            log.info("translation path %s no longer exists", path)
    return out
