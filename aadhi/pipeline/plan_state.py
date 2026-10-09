"""The lecture plan kept on a ProjectVersion while it awaits teacher review.

Stored in ``generation_meta['plan']`` (+ ``generation_meta['lexicon_seed']``). The concept brief
the plan was built from lives in ``generation_meta['brief']``: it is kept after the lecture is
written (small JSON, useful to audit what the lecture was asked to teach) and lets the plan-review
resume path continue without re-extracting it. The dict is always replaced as a whole (JSON
columns are never mutated in place); callers commit.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ..schemas.screenplay import LexiconEntry
from .base import ConceptBrief, LecturePlan

log = logging.getLogger(__name__)

PLAN_KEY = "plan"
LEXICON_KEY = "lexicon_seed"
BRIEF_KEY = "brief"


def load_plan(version: Any) -> LecturePlan | None:
    """The stored plan, or None (missing or invalid)."""
    meta = getattr(version, "generation_meta", None) or {}
    raw = meta.get(PLAN_KEY)
    if not raw:
        return None
    try:
        return LecturePlan.model_validate(raw)
    except ValidationError:
        log.warning("stored plan on version %s is invalid", getattr(version, "id", "?"))
        return None


def save_plan(version: Any, plan: LecturePlan, *, lexicon: list[LexiconEntry] | None = None) -> None:
    """Replace ``generation_meta`` with a copy holding ``plan`` (and the lexicon seed when given)."""
    meta = dict(getattr(version, "generation_meta", None) or {})
    meta[PLAN_KEY] = plan.model_dump(mode="json")
    if lexicon is not None:
        meta[LEXICON_KEY] = [e.model_dump(mode="json") for e in lexicon]
    version.generation_meta = meta


def load_lexicon_seed(version: Any) -> list[LexiconEntry]:
    """Lexicon entries saved with the plan ([] when absent)."""
    meta = getattr(version, "generation_meta", None) or {}
    out: list[LexiconEntry] = []
    for raw in meta.get(LEXICON_KEY) or []:
        try:
            out.append(LexiconEntry.model_validate(raw))
        except ValidationError:
            continue
    return out


def lexicon_for_plan(plan: LecturePlan, seed: list[LexiconEntry]) -> list[LexiconEntry]:
    """Seed entries whose term is still in the (possibly teacher-edited) glossary."""
    terms = {t.strip().lower() for t in plan.glossary_terms}
    return [e for e in seed if e.written.strip().lower() in terms] if terms else list(seed)


def load_brief(version: Any) -> ConceptBrief | None:
    """The stored concept brief, or None (missing or invalid)."""
    meta = getattr(version, "generation_meta", None) or {}
    raw = meta.get(BRIEF_KEY)
    if not raw:
        return None
    try:
        return ConceptBrief.model_validate(raw)
    except ValidationError:
        log.warning("stored concept brief on version %s is invalid", getattr(version, "id", "?"))
        return None


def save_brief(version: Any, brief: ConceptBrief | None) -> None:
    """Replace ``generation_meta`` with a copy holding ``brief`` (``None`` removes it)."""
    meta = dict(getattr(version, "generation_meta", None) or {})
    if brief is None:
        meta.pop(BRIEF_KEY, None)
    else:
        meta[BRIEF_KEY] = brief.model_dump(mode="json", exclude_defaults=True)
    version.generation_meta = meta
