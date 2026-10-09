"""Per-scene prompt context (shared by script, repair and regenerate).

A scene writer sees: a compact lecture summary, the outline with every scene's narrative role (so it
can bridge), its own plan (narrative role and ``bridge_in`` included), the previous/next scenes (goal,
role and bridge), the source chunks it cites ("Relevant source"; keyword matches when it cites none),
the chunks next to them under a separate "Nearby source (context only)" label with the scenes that
teach them (so the writer neither re-teaches a neighbour's material nor teaches ahead), figures, the
author's visual suggestions the scene uses (or that sit next to its source chunks), misconceptions to
target, glossary terms and — for simulations / Manim panels — the template's parameter schema.
A chapter card gets no source (its outline, bridge and key points are enough); a quiz gets only the
chunks it cites, without neighbours, and a note to ask only about what was taught so far; neither gets
figures or visual suggestions.
Nothing that source scoping excluded (``IngestResult.excluded``) is ever included, and the chunks the
concept brief set aside as non-teaching material (``LectureContext.skipped_chunk_ids``) are never
added as nearby context or keyword matches.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..schemas.screenplay import LexiconEntry, SourceFigure
from . import integrations
from .base import GenerationOptions, IngestResult, LecturePlan, PlannedScene, SourceChunk, VisualNote
from .prompting import json_section, language_label, section

SCENE_SOURCE_CHARS = 12_000
NEARBY_NOTE = ("Source next to this scene's own source, for continuity only. The scenes in `taught_in` teach it: do "
               "not teach it again here and do not teach ahead; at most refer back to it in a bridge.")
RELEVANT_NOTE = ("The source for this scene's goal and key points. `taught_in` lists other scenes that also use a "
                 "chunk: cover only this scene's part of it and leave theirs to them.")
QUIZ_SCOPE_NOTE = ("Ask only about what the scenes up to this one have taught (the Outline lists them in order). The "
                   "source may also hold later material or other questions: use it only to check the answer.")
# scene types that cite source without teaching it: a cold open hooks, a takeaway or summary restates
REFERRING_TYPES = ("quiz_checkpoint", "chapter_card", "recap", "summary", "title", "key_takeaway")
NO_SOURCE_FAMILIES = ("chapter_card",)  # the outline, bridge and key points are all a chapter card needs
SCENE_VISUAL_NOTES = 4  # author's visual suggestions per scene
SCENE_VISUAL_NOTE_CHARS = 900
NO_VISUAL_FAMILIES = ("chapter_card", "quiz_checkpoint")  # scene types without a visual to design
_STOP = {
    "this", "that", "with", "from", "have", "will", "your", "about", "their", "there", "which", "what", "when",
    "where", "into", "they", "them", "then", "than", "also", "such", "these", "those", "each", "learner",
    "learners", "scene", "explain", "understand", "show", "using", "used", "should",
}
_WORD = re.compile(r"[^\W\d_]{4,}", re.U)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP]


def shorten(text: str, limit: int) -> str:
    """``text`` (whitespace collapsed) cut to ``limit`` characters at a sentence, else word, boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def scene_visual_notes(notes: list[VisualNote], planned: PlannedScene, chunks: list[SourceChunk]) -> list[VisualNote]:
    """The author's visual suggestions for a scene: those the plan assigned, else those next to the
    chunks the scene cites (else next to the chunks picked for it)."""
    if not notes or planned.type in NO_VISUAL_FAMILIES:
        return []
    by_id = {v.id: v for v in notes}
    picked = [by_id[i] for i in planned.visual_note_ids if i in by_id]
    if not picked:
        near = set(planned.source_refs) or {c.id for c in chunks}
        picked = [v for v in notes if v.near_chunk_id and v.near_chunk_id in near]
    return picked[:SCENE_VISUAL_NOTES]


def relevant_chunks(chunks: list[SourceChunk], refs: list[str], query: str, budget: int = SCENE_SOURCE_CHARS,
                    skip: frozenset[str] | set[str] = frozenset()) -> list[SourceChunk]:
    """Cited chunks + their neighbours; keyword overlap when nothing is cited. Chunks in ``skip`` (set aside by
    the concept brief) are never added as a neighbour or a keyword match."""
    index = {c.id: i for i, c in enumerate(chunks)}
    picked: list[int] = []
    cited = [index[r] for r in refs if r in index]
    for i in cited:
        for j in (i, i - 1, i + 1):
            if 0 <= j < len(chunks) and j not in picked and (j == i or chunks[j].id not in skip):
                picked.append(j)
    if not cited:
        q = Counter(_words(query))
        if q:
            scored = []
            for i, c in enumerate(chunks):
                if c.id in skip:
                    continue
                words = Counter(_words(c.text + " " + " ".join(c.heading_path)))
                score = sum(min(n, words[w]) for w, n in q.items())
                if score:
                    scored.append((score, -i, i))
            picked = [i for _, _, i in sorted(scored, reverse=True)[:4]]
    out: list[SourceChunk] = []
    used = 0
    # cited chunks first (in document order), then neighbours, all within the budget
    order = sorted(picked, key=lambda j: (0 if j in cited else 1, j))
    for j in order:
        c = chunks[j]
        if used + len(c.text) > budget and out:
            continue
        out.append(c)
        used += len(c.text)
    return sorted(out, key=lambda c: index[c.id])


def split_scene_source(chunks: list[SourceChunk], refs: list[str]) -> tuple[list[SourceChunk], list[SourceChunk]]:
    """(own, nearby) of ``relevant_chunks``' result: the chunks the scene cites and their neighbours. A scene
    that cites nothing owns its keyword matches."""
    cited = set(refs)
    own = [c for c in chunks if c.id in cited]
    if not own:
        return list(chunks), []
    return own, [c for c in chunks if c.id not in cited]


def _source_item(c: SourceChunk, taught_in: list[str]) -> dict[str, Any]:
    item: dict[str, Any] = {"id": c.id, "page": c.page, "heading": " > ".join(c.heading_path), "text": c.text}
    if taught_in:
        item["taught_in"] = taught_in
    return item


@dataclass
class ScenePosition:
    planned: PlannedScene
    index: int  # 0-based position in the lecture
    total: int
    chapter_id: str
    chapter_title: str
    chapter_number: int
    prev: PlannedScene | None = None
    next: PlannedScene | None = None


def positions(plan: LecturePlan) -> list[ScenePosition]:
    """Every planned scene with its chapter and neighbours."""
    flat = [(n, ch, s) for n, ch in enumerate(plan.chapters, 1) for s in ch.scenes]
    out = []
    for i, (n, ch, s) in enumerate(flat):
        out.append(ScenePosition(
            planned=s, index=i, total=len(flat), chapter_id=ch.id, chapter_title=ch.title, chapter_number=n,
            prev=flat[i - 1][2] if i > 0 else None, next=flat[i + 1][2] if i + 1 < len(flat) else None,
        ))
    return out


@dataclass
class LectureContext:
    """Everything shared by the scene writers of one lecture."""

    plan: LecturePlan
    ingest: IngestResult
    options: GenerationOptions
    lexicon: list[LexiconEntry] = field(default_factory=list)
    figures: list[SourceFigure] = field(default_factory=list)
    skipped_chunk_ids: frozenset[str] = frozenset()  # set aside by the concept brief (non-teaching material)

    @property
    def chunk_ids(self) -> set[str]:
        return {c.id for c in self.ingest.chunks}

    @property
    def figure_ids(self) -> set[str]:
        return {f.id for f in self.figures}

    @property
    def misconception_ids(self) -> set[str]:
        return {m.id for m in self.plan.misconceptions}

    def taught_in(self, chunk_id: str, *, besides: str = "") -> list[str]:
        """Ids of the teaching scenes (other than ``besides``) that cite ``chunk_id``; quizzes, chapter cards,
        recaps and summaries only refer to material taught elsewhere."""
        return [s.id for s in self.plan.all_scenes() if s.id != besides and chunk_id in s.source_refs
                and s.type not in REFERRING_TYPES][:6]

    def lecture_summary(self) -> dict[str, Any]:
        p, o = self.plan, self.options
        return {
            "subject": p.subject_name, "unit": p.unit_name, "session": p.session_number, "session_title": p.session_title,
            "audience": o.audience, "depth": o.depth,
            "narration_language": language_label(o.language),
            "board_language": language_label(o.board_language or o.language),
            "objectives": [{"id": x.id, "text": x.text, "bloom": x.bloom} for x in p.learning_objectives],
            "concepts": [{"id": c.id, "title": c.title, "kind": c.kind} for c in p.concept_map][:60],
        }

    def outline(self) -> list[str]:
        lines = []
        for n, ch in enumerate(self.plan.chapters, 1):
            for s in ch.scenes:
                role = f", {s.narrative_role}" if s.narrative_role else ""
                lines.append(f"[{n}] {s.id} ({s.type}{role}): {shorten(s.goal, 160)}")
        if len(lines) > 80:
            lines = lines[:40] + ["…"] + lines[-39:]
        return lines


def _template_block(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    for t in integrations.list_templates():
        if t.name == name:
            return {"name": t.name, "title": t.title, "when_to_use": t.description, "steps": t.steps_hint,
                    "params_schema": t.params_schema, "example_params": t.example_params}
    return None


def scene_prompt_sections(lc: LectureContext, pos: ScenePosition) -> list[str]:
    """Context sections for one scene writer (the caller adds the task section)."""
    s = pos.planned
    plan = lc.plan
    concepts = {c.id: c for c in plan.concept_map}
    objectives = {x.id: x for x in plan.learning_objectives}
    miscs = {m.id: m for m in plan.misconceptions}
    query = " ".join([s.goal, *s.key_points, concepts[s.concept_id].title if s.concept_id in concepts else ""])
    pool = lc.ingest.chunks
    if s.type == "quiz_checkpoint" and not s.source_refs:  # keyword matches only among what was taught so far
        taught = {r for p in plan.all_scenes()[:pos.index] for r in p.source_refs}
        pool = [c for c in lc.ingest.chunks if c.id in taught] or pool
    chunks = [] if s.type in NO_SOURCE_FAMILIES else relevant_chunks(pool, list(s.source_refs), query,
                                                                      skip=lc.skipped_chunk_ids)
    chunk_text = " ".join(c.text for c in chunks)
    figs = sorted(lc.figures, key=lambda f: (f"Figure {f.id}" not in chunk_text, f.page or 0))[:40]
    this = {
        "type": s.type,
        "narrative_role": s.narrative_role,
        "position": f"scene {pos.index + 1} of {pos.total}",
        "is_cold_open": pos.index == 0,
        "is_last_scene": pos.index == pos.total - 1,
        "chapter": f"{pos.chapter_number}. {pos.chapter_title}",
        "chapter_label": f"Part {pos.chapter_number}",
        "bridge_in": s.bridge_in or None,
        "goal": s.goal,
        "key_points": s.key_points,
        "concept": {"id": s.concept_id, "title": concepts[s.concept_id].title, "summary": concepts[s.concept_id].summary}
        if s.concept_id in concepts else None,
        "objectives": [objectives[o].text for o in s.objective_ids if o in objectives],
        "source_refs": s.source_refs,
        "side_panel_kind": s.side_panel_kind,
        "visual_rationale": s.visual_rationale,
        "manim_template": s.manim_template,
        "est_seconds": s.est_seconds,
        "target_words": int(s.est_seconds * 2.3),
    }
    neighbours = {
        "previous": {"type": pos.prev.type, "narrative_role": pos.prev.narrative_role, "goal": pos.prev.goal,
                     "key_points": [shorten(k, 160) for k in pos.prev.key_points[:4]],
                     "bridge_in": pos.prev.bridge_in or None} if pos.prev else None,
        "next": {"type": pos.next.type, "narrative_role": pos.next.narrative_role, "goal": pos.next.goal,
                 "bridge_in": pos.next.bridge_in or None} if pos.next else None,
    }
    target_miscs = [
        {"key": m.id, "statement": m.statement, "correction": m.correction}
        for m in (miscs[k] for k in s.misconception_ids if k in miscs)
    ]
    sections = [
        json_section("Lecture", lc.lecture_summary()),
        section("Outline", "\n".join(lc.outline())),
        json_section("This scene", this),
        json_section("Neighbouring scenes", neighbours),
    ]
    if target_miscs:
        sections.append(json_section("Misconceptions to target", target_miscs))
    if s.type == "quiz_checkpoint":
        others = [
            {"key": m.id, "statement": m.statement, "correction": m.correction}
            for m in plan.misconceptions if m.id not in s.misconception_ids
        ][:8]
        if others:
            sections.append(json_section("Other known misconceptions", others))
    own, nearby = split_scene_source(chunks, list(s.source_refs))
    if s.type == "quiz_checkpoint":
        nearby = []  # a quiz checks what was taught: no context from the neighbouring (later) sections
    if own:
        sections.append(json_section("Relevant source", {
            "note": RELEVANT_NOTE,
            "chunks": [_source_item(c, lc.taught_in(c.id, besides=s.id)) for c in own],
        }))
    if s.type == "quiz_checkpoint":
        sections.append(section("Quiz scope", QUIZ_SCOPE_NOTE))
    if nearby:
        sections.append(json_section("Nearby source (context only)", {
            "note": NEARBY_NOTE,
            "chunks": [_source_item(c, lc.taught_in(c.id, besides=s.id)) for c in nearby],
        }))
    if figs and s.type not in NO_VISUAL_FAMILIES:
        sections.append(json_section("Figures", [{"id": f.id, "page": f.page, "caption": f.caption} for f in figs]))
    notes = scene_visual_notes(lc.ingest.visual_notes, s, chunks)
    if notes:
        sections.append(json_section("Author's visual suggestions", [
            {"id": v.id, "near_chunk": v.near_chunk_id, "text": shorten(v.text, SCENE_VISUAL_NOTE_CHARS)} for v in notes
        ]))
    terms = [e for e in lc.lexicon if e.written.lower() in chunk_text.lower()] or lc.lexicon[:20]
    if terms:
        sections.append(json_section("Glossary", [
            {"written": e.written, "spoken": e.spoken, "keep_in_english": e.keep_in_english} for e in terms[:40]
        ]))
    tpl = _template_block(s.manim_template)
    if tpl:
        sections.append(json_section("Animation template", tpl))
    if lc.options.extra_instructions.strip():
        sections.append(section("Teacher's extra instructions", lc.options.extra_instructions))
    return sections
