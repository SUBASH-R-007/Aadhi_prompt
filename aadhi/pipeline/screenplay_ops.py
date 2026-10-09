"""Whole-screenplay operations: scene replacement and plan reconstruction."""

from __future__ import annotations

from typing import Any

from ..schemas.screenplay import Screenplay
from .base import ConceptBrief, GenerationOptions, IngestResult, LecturePlan, PlannedChapter
from .canonicalize import planned_from_scene
from .plan_rules import MAX_CHAPTER_SCENES, MAX_CHAPTERS
from .scene_context import LectureContext, ScenePosition, positions
from .timing import scene_seconds


def replace_scenes(screenplay: Screenplay, replacements: dict[str, Any]) -> Screenplay:
    """New, fully validated Screenplay with scenes replaced by id (order and ids preserved)."""
    data = screenplay.model_dump(mode="json")
    data["scenes"] = [
        replacements[s.id].model_dump(mode="json") if s.id in replacements else d
        for s, d in zip(screenplay.scenes, data["scenes"], strict=True)
    ]
    return Screenplay.model_validate(data)


def _chunked(cid: str, title: str, scenes: list[Any], used: set[str]) -> list[tuple[str, str, list[Any]]]:
    """``scenes`` split into groups of at most ``MAX_CHAPTER_SCENES`` (ids ``cid``, ``cid-2`` ...; unique)."""
    out = []
    for n, start in enumerate(range(0, len(scenes), MAX_CHAPTER_SCENES), 1):
        base = cid if n == 1 else f"{cid[:60]}-{n}"
        k, m = base, 2
        while k in used:
            k = f"{base[:56]}-x{m}"
            m += 1
        used.add(k)
        out.append((k, title, scenes[start:start + MAX_CHAPTER_SCENES]))
    return out


def plan_from_screenplay(screenplay: Screenplay) -> LecturePlan:
    """LecturePlan equivalent of a screenplay (for repair/regenerate without the original plan).

    A Screenplay chapter may hold up to 200 scenes but a PlannedChapter only 60, so large chapters
    (and the group of unchaptered scenes, e.g. from a legacy import) are split into consecutive
    groups; if that exceeds the 40-chapter limit the scenes are regrouped in screenplay order.
    Rewritten scenes keep their own ``chapter_id`` (``repair.rewrite_scene``), so the grouping only
    shapes the context the scene writer sees.
    """
    known_miscs = {m.id for m in screenplay.misconceptions}
    by_id = {s.id: s for s in screenplay.scenes}
    groups: list[tuple[str, str, list[Any]]] = []
    seen: set[str] = set()
    used: set[str] = set()

    def planned(scene: Any) -> Any:
        p = planned_from_scene(scene, est_seconds=int(scene_seconds(scene, screenplay.language)))
        return p.model_copy(update={"misconception_ids": [m for m in p.misconception_ids if m in known_miscs]})

    for ch in screenplay.chapters:
        scenes = [planned(by_id[x]) for x in dict.fromkeys(ch.scene_ids) if x in by_id and x not in seen]
        seen.update(p.id for p in scenes)
        if scenes:
            groups += _chunked(ch.id, ch.title, scenes, used)
    rest = [planned(s) for s in screenplay.scenes if s.id not in seen]
    if rest:
        cid = "main"
        while cid in used or any(c.id == cid for c in screenplay.chapters):
            cid += "-x"
        groups += _chunked(cid, screenplay.session_title[:240] or "Lecture", rest, used)
    # keep the screenplay's scene order
    order = {s.id: i for i, s in enumerate(screenplay.scenes)}
    groups.sort(key=lambda g: min(order[s.id] for s in g[2]))
    if len(groups) > MAX_CHAPTERS:  # pathological: regroup everything in order
        flat = sorted((s for g in groups for s in g[2]), key=lambda s: order[s.id])
        groups = _chunked("main", screenplay.session_title[:240] or "Lecture", flat, set())
    chapters = [PlannedChapter(id=cid, title=title, scenes=scenes) for cid, title, scenes in groups]
    return LecturePlan(
        subject_name=screenplay.subject_name, unit_name=screenplay.unit_name,
        session_number=screenplay.session_number, session_title=screenplay.session_title,
        learning_objectives=list(screenplay.learning_objectives), concept_map=list(screenplay.concept_map),
        misconceptions=list(screenplay.misconceptions), glossary_terms=[e.written for e in screenplay.lexicon],
        chapters=chapters,
    )


def options_for(screenplay: Screenplay, options: GenerationOptions) -> GenerationOptions:
    """Options aligned with the screenplay's languages."""
    return options.model_copy(update={"language": screenplay.language, "board_language": screenplay.board_language})


def lecture_context(screenplay: Screenplay, ingest: IngestResult, options: GenerationOptions,
                    plan: LecturePlan | None = None, brief: ConceptBrief | None = None) -> LectureContext:
    """Scene-writing context for an existing screenplay (``brief``: the concept brief it was planned from)."""
    return LectureContext(
        plan=plan or plan_from_screenplay(screenplay), ingest=ingest, options=options_for(screenplay, options),
        lexicon=list(screenplay.lexicon), figures=list(screenplay.figures),
        skipped_chunk_ids=frozenset(brief.skipped_chunk_ids()) if brief is not None else frozenset(),
    )


def position_of(lc: LectureContext, scene_id: str) -> ScenePosition:
    for pos in positions(lc.plan):
        if pos.planned.id == scene_id:
            return pos
    raise KeyError(scene_id)
