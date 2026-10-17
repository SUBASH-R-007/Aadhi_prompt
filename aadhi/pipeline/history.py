"""A teacher's rendered Dynamic lessons, newest first: the one history every rotation rule reads (design §5.3; B3 plan
§1.4, landed at L0 so B2 and B4 build on one reader instead of their own).

:func:`recent` answers "what did this teacher's last lessons look and sound like?" for voice rotation (R-39), music
(R-47), the personality, display font, caption style, hook kind, greeting and intro layout rotations (R-51) and course
pins. Read only at generation, translation, Fresh look, New colours and New layouts; never at rebuild.

**Interface (frozen).** ``recent(session, *, user_id, exclude_project_id, fmt, limit=10) -> list[HistoryEntry]``.
``HistoryEntry`` is a frozen dataclass; its fields and their meaning do not change after L0 (B3 plan §1.4). Read-only:
no write, no migration, existing tables only.

**What counts.** One entry per project (a lesson is a project, "rotation is per lesson, not per version"): the
project's newest version that has a ``Render`` in the completed status (``succeeded``) with a ``video_asset_key``. A
version that was never rendered (a draft, an unrendered duplicate), a failed or cancelled render, another teacher's
project, a deleted project and the project being generated (``exclude_project_id``) never count. Only Dynamic lessons
count (a version whose screenplay has a ``presentation``; owner decision O21: a classic lesson's voice is the
teacher's fixed choice, so classic lessons never enter rotation). With ``fmt``, only lessons of that format: when a
project's newest rendered version is of the other format, the project has no entry in this list.

**Order.** Newest completion first: ``Job.finished_at`` of the render's job, or ``Render.created_at`` when the render
has no job (job cleanup sets ``Render.job_id`` NULL) or its job has no finish time, so a cleaned job never hides a
render; ties by ``version_id``, descending. A version rendered more than once counts at its latest completion.

**Which screenplay.** The version's CURRENT screenplay: the fields read here are presentation fields that only
casting, Fresh look and pins change (an edit after rendering does not), so the current one is "what the lesson is
now". Revisit if B4 lets a teacher edit the voice without a re-cast.

**Cost.** At most 3 queries per call:

* the completed renders: narrow rows (ids and times), at most the newest ``ROWS_PER_LIMIT * limit`` rendered versions,
  so the rows read do not grow with the teacher's history;
* the screenplays of at most ``2 * limit`` projects (the JSON column), with the small stored generation options beside
  them;
* the music beds' ``Asset.meta`` (one ``IN`` query, only when a lesson names a bed).

Fewer than ``limit`` entries come back when more than ``limit`` classic or other-format projects are among the newest
``2 * limit``, or when a few projects were rendered so often that the newest rendered versions cover fewer than
``2 * limit`` projects. The bound is the cost rule, and rotation needs only the last few lessons.

**Narrator fields.** ``VoiceCast.narrator`` is a persona id (``"openai:marin"``, ``"edge:en-IN-NeerjaNeural"``,
``"openai:coral@en-IN"``) or, in a B2-era lesson, a bare voice id (``"nova"``, ``"en-IN-NeerjaNeural"``). A bare id
is read by B3 plan §1.2 rule 2. It names the voice of that id within the provider that narrated the lesson, which is
the version's stored ``GenerationOptions.tts_provider``, read the way the orchestrator's ``version_options`` reads it
(the version's ``generation_meta["options"]``, else the project's settings). Failing that, it names the id's unique
catalogue owner. The rule's last fallback, the server's narrating provider, needs the server's settings, which history
does not take: an id that is ambiguous and has no stored provider keeps no provider (the speaker is the id, with no
accent or gender).

Until the persona registry exists (B3a, ``providers/tts/personas.py``), the speaker, accent and gender come from the
voice catalogue (``providers/tts/voices.py``) by the persona registry's own rules. An Edge speaker is ``ms:<name>``
(the Neerja variants are one speaker) and an OpenAI one is ``openai:<voice>``. An Edge voice's accent is its locale,
and OpenAI's is ``en-US`` (``fable``: ``en-GB``; an ``@en-IN`` variant: ``en-IN``). B3a replaces the body of
:func:`narrator_of` with ``personas.resolve_cast_id(raw, provider_hint)``, with the same hint; the entry's fields keep
their meaning.

The music signature is ``(mood, key, bpm)`` from the bed asset's ``Asset.meta`` (written by B3d's intake), or None
(B2-era placeholder, music off, no bed, or a bed without that analysis).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Asset, Job, Project, ProjectVersion, Render
from ..schemas.screenplay import Presentation
from .base import GenerationOptions

log = logging.getLogger(__name__)

Format = Literal["landscape", "portrait"]
Gender = Literal["female", "male", "neutral"]

COMPLETED = "succeeded"  # Render.status of a finished render (aadhi/compose/video.py)
DEFAULT_LIMIT = 10
ROWS_PER_LIMIT = 10  # the first query reads the newest ROWS_PER_LIMIT * limit rendered versions at most

# A pin (``Presentation.pins``) and the presentation value it keeps.
_PIN_VALUES = {
    "theme": lambda p: p.theme_family,
    "personality": lambda p: p.personality,
    "display_font": lambda p: p.display_font,
    "caption_style": lambda p: p.caption_style,
    "voice": lambda p: p.voices.narrator,
    "music": lambda p: p.music.asset_key or p.music.source,
}


@dataclass(frozen=True)
class Narrator:
    """A cast narrator id read the persona registry's way (see the module docstring)."""

    persona: str
    speaker: str
    accent: str | None
    gender: Gender | None


@dataclass(frozen=True)
class HistoryEntry:
    """One rendered Dynamic lesson of the teacher (B3 plan §1.4). Frozen; ``pins`` is a read-only mapping."""

    project_id: int
    version_id: int
    fmt: Format
    finished_at: dt.datetime  # UTC
    course_key: str  # casefold(subject_name.strip())
    narrator_persona: str
    narrator_speaker: str
    narrator_accent: str | None
    narrator_gender: Gender | None
    music_source: str
    music_asset_key: str | None
    music_signature: tuple[str, str, int] | None  # (mood, key, bpm) from the bed asset's meta
    theme_family: str
    personality: str
    display_font: str
    caption_style: str
    hook_kind: str
    greeting_id: str | None
    intro_layout: str | None
    pins: Mapping[str, str | None]


def recent(session: Session, *, user_id: int, exclude_project_id: int | None, fmt: Format | None,
           limit: int = DEFAULT_LIMIT) -> list[HistoryEntry]:
    """The teacher's last ``limit`` rendered Dynamic lessons (of format ``fmt`` when given), newest first."""
    if limit <= 0:
        return []
    done = func.coalesce(Job.finished_at, Render.created_at)
    q = (select(Render.version_id, ProjectVersion.project_id, func.max(done).label("done"))
         .join(ProjectVersion, ProjectVersion.id == Render.version_id)
         .join(Project, Project.id == ProjectVersion.project_id)
         .outerjoin(Job, Job.id == Render.job_id)
         .where(Project.owner_id == user_id, Project.deleted_at.is_(None), Render.status == COMPLETED,
                Render.video_asset_key.is_not(None), Render.video_asset_key != "")
         .group_by(Render.version_id, ProjectVersion.project_id)
         .order_by(func.max(done).desc(), Render.version_id.desc())
         .limit(ROWS_PER_LIMIT * limit))
    if exclude_project_id is not None:
        q = q.where(Project.id != exclude_project_id)
    rows = [(int(v), int(p), _utc(d)) for v, p, d in session.execute(q).all() if d is not None]
    rows.sort(key=lambda r: (r[2], r[0]), reverse=True)
    newest: dict[int, tuple[int, dt.datetime]] = {}
    for version_id, project_id, at in rows:  # newest first: a project's first row is its newest rendered version
        newest.setdefault(project_id, (version_id, at))
    candidates = list(newest.items())[: 2 * limit]
    if not candidates:
        return []
    ids = [version_id for _, (version_id, _) in candidates]
    loaded = session.execute(select(ProjectVersion.id, ProjectVersion.screenplay,
                                    ProjectVersion.generation_meta["options"], Project.settings)
                             .join(Project, Project.id == ProjectVersion.project_id)
                             .where(ProjectVersion.id.in_(ids))).all()
    docs = {int(v): (doc, _stored_provider(options, settings)) for v, doc, options, settings in loaded}
    picked: list[tuple[int, int, dt.datetime, Presentation, str, str | None]] = []
    for project_id, (version_id, at) in candidates:
        doc, provider = docs.get(version_id, (None, None))
        parsed = _presentation(doc, version_id)
        if parsed is None:
            continue
        presentation, course = parsed
        if fmt is not None and presentation.format != fmt:
            continue
        picked.append((project_id, version_id, at, presentation, course, provider))
        if len(picked) == limit:
            break
    beds = sorted({p.music.asset_key for _, _, _, p, _, _ in picked if p.music.asset_key})
    meta = dict(session.execute(select(Asset.key, Asset.meta).where(Asset.key.in_(beds))).all()) if beds else {}
    return [_entry(project_id, version_id, at, p, course, meta, provider)
            for project_id, version_id, at, p, course, provider in picked]


def narrator_of(raw: str, provider_hint: str | None = None) -> Narrator:
    """A stored ``VoiceCast.narrator`` (persona id or B2-era bare voice id) read the persona registry's way. A bare id
    resolves within ``provider_hint`` (the provider that narrated the lesson) when that provider has the voice, else
    to its unique catalogue owner (B3 plan §1.2 rule 2)."""
    from ..providers.tts.voices import CATALOGUE

    raw = raw.strip()
    if ":" in raw:
        provider, _, rest = raw.partition(":")
        voice, _, variant = rest.partition("@")
    else:
        owners = [name for name, voices in CATALOGUE.items() if name != "fake" and any(v["id"] == raw for v in voices)]
        if provider_hint in owners:
            owners = [provider_hint]
        provider, voice, variant = (owners[0] if len(owners) == 1 else ""), raw, ""
    entry = next((v for v in CATALOGUE.get(provider, ()) if v["id"] == voice), None)
    gender = str(entry["gender"]).lower() if entry is not None else None
    persona = f"{provider}:{voice}{'@' + variant if variant else ''}" if provider else raw
    return Narrator(persona=persona, speaker=_speaker(provider, voice), accent=_accent(provider, voice, variant),
                    gender=gender if gender in ("female", "male", "neutral") else None)  # type: ignore[arg-type]


def _speaker(provider: str, voice: str) -> str:
    if provider in ("edge", "azure"):
        name = voice.split("-", 2)[-1]
        for suffix in ("Neural", "Multilingual", "Expressive"):
            name = name.removesuffix(suffix)
        return f"ms:{name.lower()}"
    return f"{provider}:{voice}" if provider else voice


def _accent(provider: str, voice: str, variant: str) -> str | None:
    if variant:
        return variant
    if provider in ("edge", "azure"):
        parts = voice.split("-")
        return f"{parts[0]}-{parts[1]}" if len(parts) >= 3 else None
    if provider == "openai":
        return "en-GB" if voice == "fable" else "en-US"
    return None


def _stored_provider(options: Any, settings: Any) -> str | None:
    """The TTS provider that narrated a version, as stored: its ``generation_meta["options"]``, else the project's
    settings (the first that is a valid ``GenerationOptions``, as ``orchestrator.version_options`` reads them). None
    when neither names one (the server's ``TTS_PROVIDER`` narrated it)."""
    for raw in (options, settings):
        if raw and isinstance(raw, dict):
            try:
                return GenerationOptions.model_validate(raw).tts_provider
            except ValueError:
                continue
    return None


def _presentation(doc: Any, version_id: int) -> tuple[Presentation, str] | None:
    """The version's presentation and course key, or None for a classic (or unreadable) screenplay."""
    if not isinstance(doc, dict) or not isinstance(doc.get("presentation"), dict):
        return None
    try:
        presentation = Presentation.model_validate(doc["presentation"])
    except ValueError as exc:  # a presentation this code cannot read: not part of the rotation
        log.warning("history: version %s has an unreadable presentation (%s)", version_id, type(exc).__name__)
        return None
    return presentation, str(doc.get("subject_name") or "").strip().casefold()


def _signature(meta: Any) -> tuple[str, str, int] | None:
    if not isinstance(meta, dict):
        return None
    mood, key, bpm = meta.get("mood"), meta.get("key"), meta.get("bpm")
    if not (isinstance(mood, str) and mood and isinstance(key, str) and key):
        return None
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
        return None
    return mood, key, int(round(bpm))


def _entry(project_id: int, version_id: int, at: dt.datetime, p: Presentation, course: str,
           meta: dict[str, Any], provider: str | None) -> HistoryEntry:
    narrator = narrator_of(p.voices.narrator, provider)
    key = p.music.asset_key
    return HistoryEntry(
        project_id=project_id, version_id=version_id, fmt=p.format, finished_at=at, course_key=course,
        narrator_persona=narrator.persona, narrator_speaker=narrator.speaker, narrator_accent=narrator.accent,
        narrator_gender=narrator.gender, music_source=p.music.source, music_asset_key=key,
        music_signature=_signature(meta.get(key)) if key else None, theme_family=p.theme_family,
        personality=p.personality, display_font=p.display_font, caption_style=p.caption_style,
        hook_kind=p.hook_kind, greeting_id=p.greeting_id, intro_layout=p.intro_layout,
        pins=MappingProxyType({pin: _PIN_VALUES[pin](p) for pin in p.pins if pin in _PIN_VALUES}),
    )


def _utc(value: Any) -> dt.datetime | None:
    """A database time as an aware UTC datetime (SQLite returns naive UTC; a string from an aggregate is parsed)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if not isinstance(value, dt.datetime):
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)
