"""Lesson quality and consistency checks (education rule family): deterministic, pure and bounded.

Called by ``aadhi.pipeline.validate.lint`` (so every PUT screenplay, POST /lint and pipeline lint
includes them). Everything is read from the typed screenplay; nothing is ever rewritten here, no model
is called, and every finding is an ordinary lint ``Issue`` (``source="lint"``), so saving is never
blocked. Codes and severities:

==============================================  ========  ================================================
code                                            severity  what
==============================================  ========  ================================================
``terminology.variant``                         info      a term written with hyphen / space / joined
``terminology.casing``                          info      a term capitalised differently in emphasised places
``terminology.abbreviation_undefined``          info      an abbreviation never spelled out
``terminology.abbreviation_late``               info      spelled out only after its first use
``terminology.abbreviation_conflict``           warning   spelled out two different ways
``concept.naming_variant``                      info      one concept titled with one name written two ways
``figure.caption_variant``                      info      one figure shown with different captions
``formula.symbol_conflict``                     warning   one legend symbol with two meanings
``formula.notation_mismatch``                   info      one quantity with two symbols
``formula.legend_missing``                      info      a formula without a legend, symbols unexplained
``code.language_unhighlighted``                 warning   code in a language the player does not colour
``code.mixed_indentation``                      warning   tabs and spaces mixed (fixable by a rewrite)
``code.mixed_languages``                        info      code in several languages
``title.casing_mixed``                          info      a title styled unlike the others of its level
``pacing.dense_run``                            info      dense boards one after another
``board.reading_time``                          info      more to read than the scene gives time for
==============================================  ========  ================================================

Hidden scenes (``SceneBase.hidden``, skipped in the video) are left out of the pacing run and of the abbreviation
first-use and definition checks, which are about what plays; checks of a scene's own content still run on them and
``validate.lint_hidden`` keeps their findings as notes.

Terminology, abbreviation, concept-name, caption and title checks run for English boards only
(``board_language``, else ``language``): casing means nothing in Indic scripts, and a translated version
inherits its source's terms. Only ``code.mixed_indentation`` is ``fixable``; none is an error, so no
check here ever selects a scene for a paid rewrite (``validate.scenes_needing_repair``), and
``AUTHOR_CODES`` are never handed to a rewrite at all (``repair.rewrite_scene``).

``quality_report`` adds what the editor needs beyond the issues: safe repairs (exact field edits,
applied by the Studio as an undoable edit) and the lesson's consistency registry.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from ...schemas.screenplay import Screenplay
from ..base import Issue
from . import abbreviations, code, formulas
from .extract import Facts, extract, scenes_of
from .pacing import pacing_findings
from .report import Finding, repair_entry
from .terms import concept_findings, concept_names, figure_captions, figure_findings, term_findings
from .text import latin_board
from .titles import title_findings

log = logging.getLogger(__name__)

REPORT_VERSION = 1
MAX_REGISTRY_TERMS = 40
QUALITY_CODES = frozenset({
    "terminology.variant", "terminology.casing", "terminology.abbreviation_undefined", "terminology.abbreviation_late",
    "terminology.abbreviation_conflict", "concept.naming_variant", "figure.caption_variant", "formula.symbol_conflict",
    "formula.notation_mismatch", "formula.legend_missing", "code.language_unhighlighted", "code.mixed_indentation",
    "code.mixed_languages", "title.casing_mixed", "pacing.dense_run", "board.reading_time",
})
# The author's content: a scene rewrite (repair round or "Regenerate scene") never gets these as "issues to fix",
# so it cannot rename a term, switch a code example's language or change a formula's notation on its own. The
# sync lint's ``formula.variable_beat_invalid`` is about a field only the teacher sets (``FormulaVariable.beat_id``).
AUTHOR_CODES = frozenset({
    "terminology.variant", "terminology.casing", "terminology.abbreviation_undefined", "terminology.abbreviation_late",
    "terminology.abbreviation_conflict", "terminology.ambiguous", "concept.naming_variant", "figure.caption_variant",
    "formula.symbol_conflict", "formula.notation_mismatch", "code.language_unhighlighted", "code.mixed_languages",
    "title.casing_mixed", "formula.variable_beat_invalid",
})

T = TypeVar("T")


def _safely(name: str, make: Callable[[], T], default: T) -> T:
    """A check that cannot decide stays silent; the other checks (and the lint) still report."""
    try:
        return make()
    except Exception:  # noqa: BLE001 - a quality check must never break lint, saving or generation
        log.warning("quality check %s failed", name, exc_info=True)
        return default


def analyse(screenplay: Screenplay) -> tuple[Facts, list[Finding], OrderedDict[str, dict[str, Any]]]:
    """(facts, findings, abbreviation analysis) of a screenplay."""
    facts = extract(screenplay)
    findings: list[Finding] = []
    analysed: OrderedDict[str, dict[str, Any]] = OrderedDict()
    if latin_board(screenplay):
        term_out, reported = _safely("terminology", lambda: term_findings(facts), ([], set()))
        findings += term_out
        findings += _safely("concepts", lambda: concept_findings(facts, reported), [])
        findings += _safely("figures", lambda: figure_findings(facts), [])
        analysed = _safely("abbreviations", lambda: abbreviations.analyse(facts), OrderedDict())
        findings += _safely("abbreviations", lambda: abbreviations.abbreviation_findings(facts, analysed), [])
        findings += _safely("titles", lambda: title_findings(facts), [])
    findings += _safely("formulas", lambda: formulas.formula_findings(facts), [])
    findings += _safely("code", lambda: code.code_findings(facts), [])
    findings += _safely("pacing", lambda: pacing_findings(facts), [])
    return facts, findings, analysed


Analysis = tuple[Facts, list[Finding], OrderedDict[str, dict[str, Any]]]  # what ``analyse`` returns
_COMPUTE: Any = object()  # "no analysis given": compute it
COMPUTE: Any = _COMPUTE  # pass to ``validate.lint(quality=...)`` to mean "compute it" (None there: the analysis failed)


def analysis_of(screenplay: Screenplay) -> Analysis | None:
    """``analyse`` that never raises: None when extraction itself failed (logged). One analysis can serve both
    ``lint_quality`` and ``quality_report`` (POST /lint), so the checks run once per request."""
    try:
        return analyse(screenplay)
    except Exception:  # noqa: BLE001 - extraction itself failed: the rest of the lint still runs
        log.warning("quality checks failed", exc_info=True)
        return None


def lint_quality(screenplay: Screenplay, analysis: Analysis | None = _COMPUTE) -> list[Issue]:
    """The quality family's lint issues (pure, bounded; never raises). ``analysis``: ``analysis_of(screenplay)``
    when the caller has it already."""
    if analysis is _COMPUTE:
        analysis = analysis_of(screenplay)
    return [] if analysis is None else [f.issue for f in analysis[1]]


def registry(facts: Facts, analysed: OrderedDict[str, dict[str, Any]]) -> dict[str, Any]:
    """The lesson's consistency registry: terms with their written forms, concept names, abbreviations,
    formula symbols, code languages and figure captions, each traceable to scene ids (bounded)."""
    sid = facts.lesson.scene_id
    groups = list(facts.groups.items())
    ranked = sorted(range(len(groups)), key=lambda n: (-len(scenes_of([o for occ in groups[n][1].values() for o in occ])), n))
    terms = []
    for n in sorted(ranked[:MAX_REGISTRY_TERMS]):
        key, forms = groups[n]
        listed = [{"form": f, "scene_ids": [sid(i) for i in scenes_of(occ)[:12]], "where": sorted({o.position for o in occ})}
                  for f, occ in list(forms.items())[:6]]
        main = max(listed, key=lambda v: len(v["scene_ids"]))
        terms.append({"term": main["form"], "key": key[:40], "forms": listed})
    concepts = [{"id": c["id"], "title": c["map_title"] or (c["names"][0][0] if c["names"] else None),
                 "names": list(dict.fromkeys(name for name, _i in c["names"]))[:6],
                 "scene_ids": [sid(i) for i in c["scenes"][:20]]}
                for c in list(concept_names(facts).values())[:MAX_REGISTRY_TERMS]]
    figures = []
    for figure_id, shown in list(figure_captions(facts).items())[:MAX_REGISTRY_TERMS]:
        captions: OrderedDict[str, list[str]] = OrderedDict()
        for caption, index in shown:
            ids = captions.setdefault(caption, [])
            if sid(index) not in ids:
                ids.append(sid(index))
        figures.append({"figure_id": figure_id, "captions": [{"caption": c, "scene_ids": s[:12]}
                                                             for c, s in list(captions.items())[:4]]})
    return {
        "terms": terms,
        "concepts": concepts,
        "abbreviations": abbreviations.registry_entries(facts, analysed),
        "symbols": formulas.registry_entries(facts),
        "code_languages": code.registry_entries(facts),
        "figures": figures,
    }


def quality_report(screenplay: Screenplay, analysis: Analysis | None = _COMPUTE) -> dict[str, Any]:
    """Safe repairs and the consistency registry for the Studio (``POST /api/versions/{vid}/lint`` ``quality``).

    ``repairs`` name the issue they fix by ``code``, ``scene_id`` and ``message`` (the issue in the same
    response); each carries exact ``edits`` (``scene_id``, ``path`` inside the scene, ``before``, ``after``).
    ``analysis``: ``analysis_of(screenplay)`` when the caller has it already (None: it failed, already logged).
    """
    if analysis is None:
        return {"version": REPORT_VERSION, "repairs": [], "registry": {}}
    try:
        facts, findings, analysed = analyse(screenplay) if analysis is _COMPUTE else analysis
        repairs = [r for r in (repair_entry(f) for f in findings) if r is not None]
        reg = _safely("registry", lambda: registry(facts, analysed), {})
    except Exception:  # noqa: BLE001 - the report is advisory; the lint issues are unaffected
        log.warning("quality report failed", exc_info=True)
        return {"version": REPORT_VERSION, "repairs": [], "registry": {}}
    return {"version": REPORT_VERSION, "repairs": repairs, "registry": reg}
