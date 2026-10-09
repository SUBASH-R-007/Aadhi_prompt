"""Generation output + PlannedScene -> canonical ``Scene`` (all ids assigned here).

Deterministic rules
-------------------
* scene id = planned id; beats ``{scene}-b{n}`` (quiz reveal beats continue the numbering);
  board items ``{scene}-i{n}`` in order of appearance.
* ``fill_previous_blank`` fills the latest revealed, not yet filled blank ``example_step``.
* ``highlight_steps`` are 1-based numbers of board items already introduced.
* Quiz options are ``[correct] + distractors`` shuffled with a PRNG seeded by sha256(scene id);
  ``feedback_wrong`` and ``option_misconception_ids`` follow the shuffle.
* intent (goal, key points, source refs, narrative role, bridge), concept, objectives come from the
  plan; ``planned_from_scene`` reads them back, so a scene repaired or regenerated without the original
  plan keeps its place in the lecture's flow.

``strict=True`` reports every inconsistency as a problem (fed back to the model in the re-ask
loop); ``strict=False`` repairs what it can so a last attempt still yields a valid scene: dangling
references are dropped, board items beyond the schema limit are not added, beat lists are cut to
their limits and over-long texts / lists are truncated to the schema ``max_length`` (code and TeX
are never truncated — broken code is worse than a fallback scene).
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..schemas.screenplay import (
    BoardItem,
    BoardScene,
    ChapterCardScene,
    QuizScene,
    Scene,
    SidePanel,
    SimulationScene,
    slugify,
)
from .base import PlannedScene
from .gen_models import (
    GenAIVideo,
    GenBeat,
    GenBoardItem,
    GenChapterCard,
    GenInteractive,
    GenQuiz,
    GenSidePanel,
    scene_family,
)
from .richlite import narration_markup

SCENE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Scene)


def _max_len(model: type[BaseModel], name: str) -> int:
    """``max_length`` of a model field (read from the schema, never duplicated here)."""
    for meta in model.model_fields[name].metadata:
        value = getattr(meta, "max_length", None)
        if value is not None:
            return int(value)
    raise KeyError(f"{model.__name__}.{name} has no max_length")


MAX_BOARD_ITEMS = _max_len(BoardScene, "board")  # 12
MAX_BEATS = _max_len(BoardScene, "beats")  # 40
MAX_REVEAL_BEATS = _max_len(QuizScene, "reveal_beats")  # 10
MAX_CHAPTER_BEATS = _max_len(ChapterCardScene, "beats")  # 4
# Never truncated by lenient clamping (truncated code/TeX is broken) or structural lists (references).
_NO_TRUNCATE = frozenset({"id", "code", "p5_code", "latex", "symbol_latex", "expr"})
_STRUCTURAL_LISTS = frozenset({"beats", "reveal_beats", "board"})

_DEFAULT_MASCOT = {
    "title": "center",
    "chapter_card": "center",
    "quiz_checkpoint": "right",
    "simulation": "right",  # the popup clip is a full-frame close-up: media would be tiny
    "ai_video": "right",  # the popup clip is a full-frame close-up: media would be tiny
    "interactive": "right",  # the popup clip is a full-frame close-up: media would be tiny
}


class CanonicalizationError(ValueError):
    """The generation output cannot be turned into a valid canonical scene."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class CanonContext:
    chapter_id: str | None = None
    chapter_label: str | None = None
    known_misconceptions: set[str] | None = None
    known_figures: set[str] | None = None
    known_chunks: set[str] | None = None
    strict: bool = True
    problems: list[str] = field(default_factory=list)

    def problem(self, msg: str) -> None:
        self.problems.append(msg)


def child_id(scene_id: str, prefix: str, n: int) -> str:
    """``{scene}-{prefix}{n}``, shortened so it stays a valid 64-char slug."""
    suffix = f"-{prefix}{n}"
    return f"{scene_id[: 64 - len(suffix)]}{suffix}"


def shuffle_order(scene_id: str, n: int) -> list[int]:
    """Deterministic permutation of ``range(n)`` seeded by sha256(scene_id)."""
    seed = int.from_bytes(hashlib.sha256(scene_id.encode("utf-8")).digest()[:8], "big")
    order = list(range(n))
    random.Random(seed).shuffle(order)
    return order


def format_validation_error(exc: ValidationError, limit: int = 8) -> list[str]:
    """Compact, model-readable messages for a pydantic ValidationError."""
    out: list[str] = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("function-after",))
        out.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid")))
    return out


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _opt(text: str | None) -> str | None:
    t = (text or "").strip()
    return t or None


def _code_language(lang: str | None) -> str | None:
    if not lang:
        return None
    v = re.sub(r"[^a-z0-9+#-]", "", lang.strip().lower().replace(" ", ""))[:20]
    return v or None


def _refs(refs: list[str], ctx: CanonContext, where: str) -> list[str]:
    out: list[str] = []
    for r in refs:
        r = (r or "").strip()
        if not r or r in out:
            continue
        if ctx.known_chunks is not None and r not in ctx.known_chunks:
            if ctx.strict:
                ctx.problem(f"{where}: unknown source ref {r!r} (use chunk ids such as c0003)")
            continue
        out.append(r)
    return out[:10]


def _misconception_id(key: str | None, ctx: CanonContext, where: str) -> str | None:
    if not key:
        return None
    mid = slugify(key)
    if ctx.known_misconceptions is not None and mid not in ctx.known_misconceptions:
        if ctx.strict:
            ctx.problem(f"{where}: unknown misconception key {key!r}")
        return None
    return mid


# ---------------------------------------------------------------------------
# Board items & beats
# ---------------------------------------------------------------------------


def _board_item(g: GenBoardItem, item_id: str, ctx: CanonContext, where: str) -> dict[str, Any]:
    kind = g.kind
    d: dict[str, Any] = {"id": item_id, "kind": kind, "text": g.text or ""}
    if g.source_refs:
        d["source_refs"] = _refs(g.source_refs, ctx, where)
    if kind == "definition":
        d["term"] = _opt(g.term)
    elif kind == "formula":
        d["latex"] = (g.latex or "").strip().strip("$").strip()
        d["variables"] = [v.model_dump() for v in g.variables[:8]]
    elif kind == "code":
        d["language"] = _code_language(g.code_language)
        d["code"] = g.code or ""
    elif kind == "table":
        headers = [h for h in (g.table_headers or [])]
        rows = [list(r) for r in (g.table_rows or [])]
        if headers and any(len(r) != len(headers) for r in rows):
            if ctx.strict:
                ctx.problem(f"{where}: every table row needs exactly {len(headers)} cells")
            rows = [(r + [""] * len(headers))[: len(headers)] for r in rows]
        d["headers"] = headers or None
        d["rows"] = rows or None
    elif kind == "figure":
        fid = slugify(g.figure_id) if g.figure_id else None
        if not fid or (ctx.known_figures is not None and fid not in ctx.known_figures):
            if ctx.strict:
                ctx.problem(f"{where}: unknown figure id {g.figure_id!r} (use an id from the figure list)")
            else:  # degrade to a caption line instead of a broken figure
                d.update(kind="paragraph", text=_clean(g.caption) or _clean(g.text) or "Figure")
                return d
        d["figure_id"] = fid
        d["caption"] = _opt(g.caption)
    elif kind == "example_step":
        d["justification"] = _opt(g.justification)
        d["blank"] = bool(g.blank)
    elif kind == "misconception":
        d["justification"] = _opt(g.justification)
        d["misconception_id"] = _misconception_id(g.misconception_key, ctx, where)
    if g.blank and kind != "example_step" and ctx.strict:
        ctx.problem(f"{where}: only example_step items can be blank")
    return d


def _checked_item(d: dict[str, Any], ctx: CanonContext, where: str) -> dict[str, Any] | None:
    """Validate one board item: strict -> report problems; lenient -> degrade to a bullet or drop it."""
    try:
        BoardItem.model_validate(d)
        return d
    except ValidationError as exc:
        if ctx.strict:
            ctx.problem(f"{where}: board item ({d.get('kind')}): " + "; ".join(format_validation_error(exc, 2)))
            return d
    text = _clean(d.get("text") or d.get("term") or d.get("caption") or d.get("justification") or "")
    if not text:
        return None
    kind = "paragraph" if len(text) > 140 else "bullet"
    return {"id": d["id"], "kind": kind, "text": text[:1200], "source_refs": d.get("source_refs", [])}


def _beat_base(g: GenBeat, beat_id: str, ctx: CanonContext, where: str) -> dict[str, Any]:
    narration = _clean(g.narration)
    if not narration:
        ctx.problem(f"{where}: narration is empty")
    elif ctx.strict:
        found = narration_markup(narration)
        if found:
            ctx.problem(f"{where}: narration must be plain spoken text (found {', '.join(found)})")
    spoken = _clean(g.spoken) or None
    if spoken == narration:
        spoken = None
    return {
        "id": beat_id,
        "narration": narration or "…",
        "spoken": spoken,
        "pause_after": round(min(8.0, max(0.0, float(g.pause_after or 0.0))), 2),
        "visual_cue": _opt(g.visual_cue),
        "source_refs": _refs(g.source_refs, ctx, where),
    }


def _plain_beats(beats: list[GenBeat], scene_id: str, ctx: CanonContext, start: int = 1) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n, g in enumerate(beats, start):
        where = f"beat {n}"
        if ctx.strict and (g.board is not None or g.fill_previous_blank or g.highlight_steps):
            ctx.problem(f"{where}: this scene type has no board; leave board/fill/highlight empty")
        out.append(_beat_base(g, child_id(scene_id, "b", n), ctx, where))
    return out


def _board_beats(beats: list[GenBeat], scene_id: str, ctx: CanonContext) -> tuple[list[dict], list[dict]]:
    items: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    unfilled: list[str] = []
    for n, g in enumerate(beats, 1):
        where = f"beat {n}"
        b = _beat_base(g, child_id(scene_id, "b", n), ctx, where)
        revealed: str | None = None
        if g.board is not None and (ctx.strict or len(items) < MAX_BOARD_ITEMS):  # lenient: the beat stays, unboarded
            item_id = child_id(scene_id, "i", len(items) + 1)
            item = _checked_item(_board_item(g.board, item_id, ctx, where), ctx, where)
            if item is not None:
                revealed = item_id
                items.append(item)
                b["board_item_id"] = revealed
        if g.fill_previous_blank:
            candidates = [i for i in unfilled if i != revealed]
            if candidates:
                fill = candidates[-1]
                unfilled.remove(fill)
                b["fill_item_id"] = fill
            elif ctx.strict:
                ctx.problem(f"{where}: fill_previous_blank is set but no earlier blank example_step is waiting")
        if revealed and items[-1].get("blank"):
            unfilled.append(revealed)
        highlights: list[str] = []
        for step in g.highlight_steps:
            ok = isinstance(step, int) and 1 <= step <= len(items)
            target = items[step - 1]["id"] if ok else None
            if not ok or target == revealed:
                if ctx.strict:
                    ctx.problem(f"{where}: highlight step {step} must be an earlier board item (1..{len(items) - (1 if revealed else 0)})")
                continue
            if target not in highlights:
                highlights.append(target)
        if len(highlights) > 2:
            if ctx.strict:
                ctx.problem(f"{where}: highlight at most 2 items")
            highlights = highlights[:2]
        b["highlight_item_ids"] = highlights
        out.append(b)
    return items, out


# ---------------------------------------------------------------------------
# Side panels
# ---------------------------------------------------------------------------


def _side_panel(
    g: GenSidePanel | None, planned: PlannedScene, beat_ids: list[str], ctx: CanonContext
) -> dict[str, Any] | None:
    kind = planned.side_panel_kind
    if kind is None:
        return None  # the plan decides about visuals; unplanned panels are ignored
    if g is None:
        if kind == "skill_tree":
            return {"kind": "skill_tree", "rationale": planned.visual_rationale}
        if ctx.strict:
            ctx.problem(f"side_panel: the plan asks for a {kind!r} side panel; fill side_panel with kind {kind!r}")
        return None
    if g.kind != kind and ctx.strict:
        ctx.problem(f"side_panel: kind must be {kind!r} (planned), got {g.kind!r}")
    d: dict[str, Any] = {
        "kind": kind,
        "title": _opt(g.title),
        "rationale": _clean(g.rationale) or planned.visual_rationale,
    }
    if g.show_from_beat is not None:
        if 1 <= g.show_from_beat <= len(beat_ids):
            d["show_from_beat_id"] = beat_ids[g.show_from_beat - 1]
        elif ctx.strict:
            ctx.problem(f"side_panel.show_from_beat must be between 1 and {len(beat_ids)}")
    if kind == "figure":
        fid = slugify(g.figure_id) if g.figure_id else None
        if fid and ctx.known_figures is not None and fid not in ctx.known_figures:
            if ctx.strict:
                ctx.problem(f"side_panel: unknown figure id {g.figure_id!r}")
            fid = None
        d["figure_id"] = fid
    elif kind == "image":
        d["image_prompt"] = _opt(g.image_prompt)
    elif kind == "chart":
        if g.chart_labels and g.chart_datasets:
            d["chart"] = {
                "chart_type": g.chart_type or "bar",
                "labels": g.chart_labels,
                "datasets": [ds.model_dump() for ds in g.chart_datasets],
                "x_label": _opt(g.chart_x_label),
                "y_label": _opt(g.chart_y_label),
            }
    elif kind == "graph":
        x_min = g.graph_x_min if g.graph_x_min is not None else -5.0
        x_max = g.graph_x_max if g.graph_x_max is not None else 5.0
        graph: dict[str, Any] = {
            "functions": [f.model_dump() for f in g.graph_functions],
            "points": [p.model_dump() for p in g.graph_points],
            "x_range": (x_min, x_max),
            "x_label": _opt(g.graph_x_label),
            "y_label": _opt(g.graph_y_label),
        }
        if g.graph_y_min is not None and g.graph_y_max is not None:
            graph["y_range"] = (g.graph_y_min, g.graph_y_max)
        d["graph"] = graph
    elif kind == "model_3d":
        if g.model_primitives:
            d["model_3d"] = {
                "primitives": [
                    {"shape": p.shape, "position": (p.x, p.y, p.z), "size": p.size or [1.0], "color": p.color,
                     "label": p.label}
                    for p in g.model_primitives
                ],
                "auto_rotate": g.model_auto_rotate,
            }
    elif kind == "manim":
        params = getattr(g, "manim_params", None)
        if planned.manim_template and params is not None:
            dumped = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
            d["manim"] = {"template": planned.manim_template, "params": dumped}
        elif g.manim_code:
            d["manim"] = {"code": g.manim_code}
    elif kind == "terminal":
        if g.terminal_command:
            d["terminal"] = {"command": g.terminal_command, "output": g.terminal_output or ""}
    elif kind == "quiz":
        if g.quiz_question and g.quiz_options and g.quiz_correct_index is not None:
            d["quiz"] = {"question": g.quiz_question, "options": g.quiz_options, "correct_index": g.quiz_correct_index}
    elif kind == "gif":
        d["gif_query"] = _opt(g.gif_query)
    return d


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


def intent_of(planned: PlannedScene) -> dict[str, Any]:
    """The scene's ``intent`` from its plan: what it is for and how it follows from the scene before it."""
    return {"goal": planned.goal, "key_points": list(planned.key_points), "source_refs": list(planned.source_refs),
            "narrative_role": planned.narrative_role, "bridge_in": planned.bridge_in[:300]}


def _common(planned: PlannedScene, out: Any, ctx: CanonContext) -> dict[str, Any]:
    title = _clean(getattr(out, "title", ""))
    if not title and planned.type != "chapter_card":
        if ctx.strict:
            ctx.problem("title: give the scene a short title")
        else:
            title = planned.goal.split(".")[0][:80]
    return {
        "id": planned.id,
        "type": planned.type,
        "concept_id": planned.concept_id,
        "chapter_id": ctx.chapter_id,
        "title": title,
        "subtitle": _opt(getattr(out, "subtitle", None)),
        "mascot_position": getattr(out, "mascot_position", None) or _DEFAULT_MASCOT.get(planned.type, "left"),
        "objective_ids": list(planned.objective_ids),
        "intent": intent_of(planned),
        "notes": (getattr(out, "teacher_notes", "") or "")[:4000],
    }


def _limit(beats: list[Any], limit: int, ctx: CanonContext) -> list[Any]:
    """Lenient mode: cut a beat list to its schema limit (strict mode reports it via validation)."""
    return beats if ctx.strict else beats[:limit]


def _board_scene(planned: PlannedScene, out: Any, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    items, beats = _board_beats(_limit(list(out.beats), MAX_BEATS, ctx), planned.id, ctx)
    if not beats:
        ctx.problem("beats: write at least one beat")
    d["board"] = items
    d["beats"] = beats
    panel = _checked_panel(_side_panel(getattr(out, "side_panel", None), planned, [b["id"] for b in beats], ctx), ctx)
    if panel is not None:
        d["side_panel"] = panel
    return d


def _checked_panel(d: dict[str, Any] | None, ctx: CanonContext) -> dict[str, Any] | None:
    """Validate the side panel: strict -> report problems; lenient -> drop an invalid panel."""
    if d is None:
        return None
    try:
        SidePanel.model_validate(d)
        return d
    except ValidationError as exc:
        if ctx.strict:
            ctx.problem("side_panel: " + "; ".join(format_validation_error(exc, 3)))
            return d
        return None


def _chapter_scene(planned: PlannedScene, out: GenChapterCard, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    beats = list(out.beats)
    if len(beats) > MAX_CHAPTER_BEATS:
        if ctx.strict:
            ctx.problem("beats: a chapter card has at most 2 short beats")
        beats = beats[:MAX_CHAPTER_BEATS]
    d["beats"] = _plain_beats(beats, planned.id, ctx)
    d["chapter_label"] = (_clean(out.chapter_label) or ctx.chapter_label or "")[:60] or "Part 1"
    if not d["title"]:
        d["title"] = planned.goal[:120] or d["chapter_label"]
    return d


def _quiz_scene(planned: PlannedScene, out: GenQuiz, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    correct = _clean(out.correct)
    distractors = [x for x in out.distractors if _clean(x.text)]
    if not 1 <= len(distractors) <= 4:
        if ctx.strict:
            ctx.problem(f"distractors: give 2-3 plausible wrong options (got {len(distractors)})")
        distractors = distractors[:4]
    seen = {correct.lower()}
    uniq = []
    for x in distractors:
        key = _clean(x.text).lower()
        if key in seen:
            if ctx.strict:
                ctx.problem(f"distractors: option {x.text!r} duplicates another option")
            continue
        seen.add(key)
        uniq.append(x)
    raw_options = [correct] + [_clean(x.text) for x in uniq]
    raw_feedback = [""] + [_clean(x.why_wrong) for x in uniq]
    raw_misc = [None] + [_misconception_id(x.misconception_key, ctx, f"distractor {i + 1}") for i, x in enumerate(uniq)]
    order = shuffle_order(planned.id, len(raw_options))
    d["question"] = _clean(out.question)
    d["options"] = [raw_options[i] for i in order]
    d["correct_index"] = order.index(0)
    d["feedback_wrong"] = [raw_feedback[i] for i in order]
    d["option_misconception_ids"] = [raw_misc[i] for i in order]
    d["explanation"] = _clean(out.explanation)
    d["bloom"] = out.bloom
    d["countdown_seconds"] = int(min(30, max(3, out.countdown_seconds or 8)))
    if not out.question_beats:
        ctx.problem("question_beats: write at least one beat that poses the question")
    if not out.reveal_beats:
        ctx.problem("reveal_beats: write at least one beat that reveals and explains the answer")
    question_beats = _limit(list(out.question_beats), MAX_BEATS, ctx)
    d["beats"] = _plain_beats(question_beats, planned.id, ctx)
    d["reveal_beats"] = _plain_beats(_limit(list(out.reveal_beats), MAX_REVEAL_BEATS, ctx), planned.id, ctx,
                                     start=len(question_beats) + 1)
    d["source_refs"] = _refs(list(out.source_refs) or list(planned.source_refs), ctx, "source_refs")
    return d


def _simulation_scene(planned: PlannedScene, out: Any, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    d["beats"] = _plain_beats(_limit(list(out.beats), MAX_BEATS, ctx), planned.id, ctx)
    params = getattr(out, "params", None)
    if params is not None and planned.manim_template:
        dumped = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
        d["manim"] = {"template": planned.manim_template, "params": dumped}
    elif getattr(out, "code", None):
        d["manim"] = {"code": out.code}
    else:
        ctx.problem("simulation needs template params or code")
    if ctx.strict:
        for n, b in enumerate(out.beats, 1):
            if not _clean(b.visual_cue):
                ctx.problem(f"beat {n}: describe the animation step in visual_cue")
                break
    return d


def _ai_video_scene(planned: PlannedScene, out: GenAIVideo, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    d["beats"] = _plain_beats(_limit(list(out.beats), MAX_BEATS, ctx), planned.id, ctx)
    d["video_prompt"] = (out.video_prompt or "").strip()
    d["rationale"] = _clean(out.rationale) or planned.visual_rationale
    d["fallback_image_prompt"] = _opt(out.fallback_image_prompt)
    fid = slugify(out.fallback_figure_id) if out.fallback_figure_id else None
    if fid and ctx.known_figures is not None and fid not in ctx.known_figures:
        if ctx.strict:
            ctx.problem(f"fallback_figure_id: unknown figure id {out.fallback_figure_id!r}")
        fid = None
    d["fallback_figure_id"] = fid
    return d


def _interactive_scene(planned: PlannedScene, out: GenInteractive, ctx: CanonContext) -> dict[str, Any]:
    d = _common(planned, out, ctx)
    d["beats"] = _plain_beats(_limit(list(out.beats), MAX_BEATS, ctx), planned.id, ctx)
    d["p5_code"] = out.p5_code or ""
    return d


_BUILDERS = {
    "board": _board_scene,
    "chapter": _chapter_scene,
    "quiz": _quiz_scene,
    "simulation": _simulation_scene,
    "ai_video": _ai_video_scene,
    "interactive": _interactive_scene,
}


def build_scene_dict(planned: PlannedScene, out: BaseModel, ctx: CanonContext) -> dict[str, Any]:
    """Canonical scene as a plain dict (problems collected in ``ctx.problems``)."""
    return _BUILDERS[scene_family(planned.type)](planned, out, ctx)


def clamp_to_limits(data: dict[str, Any], exc: ValidationError) -> bool:
    """Truncate the over-long strings / lists that ``exc`` reports, in place; True when anything changed.

    Lenient mode only. Code, TeX and ids are never truncated; beat and board lists are cut
    structurally by the builders (cutting them here could orphan references).
    """
    changed = False
    for err in exc.errors():
        if err.get("type") not in ("string_too_long", "too_long"):
            continue
        limit = (err.get("ctx") or {}).get("max_length")
        loc = list(err.get("loc") or ())
        if loc and loc[0] == data.get("type"):  # discriminated-union tag
            loc = loc[1:]
        names = [x for x in loc if isinstance(x, str)]
        if limit is None or not loc or not names or names[-1] in _NO_TRUNCATE:
            continue
        parent: Any = data
        for part in loc[:-1]:
            if isinstance(parent, dict) and part in parent:
                parent = parent[part]
            elif isinstance(parent, list) and isinstance(part, int) and 0 <= part < len(parent):
                parent = parent[part]
            else:
                parent = None
                break
        key = loc[-1]
        if isinstance(parent, dict) and key in parent:
            value = parent[key]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            value = parent[key]
        else:
            continue
        if isinstance(value, str) and len(value) > limit:
            parent[key] = value[:limit].rstrip() or value[:limit]
            changed = True
        elif isinstance(value, list) and len(value) > limit and key not in _STRUCTURAL_LISTS:
            parent[key] = value[:limit]
            changed = True
    return changed


def canonicalize_scene(
    planned: PlannedScene,
    out: BaseModel,
    *,
    chapter_id: str | None = None,
    chapter_label: str | None = None,
    known_misconceptions: set[str] | None = None,
    known_figures: set[str] | None = None,
    known_chunks: set[str] | None = None,
    strict: bool = True,
) -> Any:
    """Return the canonical, validated Scene. Raises CanonicalizationError with all problems."""
    ctx = CanonContext(
        chapter_id=chapter_id,
        chapter_label=chapter_label,
        known_misconceptions=known_misconceptions,
        known_figures=known_figures,
        known_chunks=known_chunks,
        strict=strict,
    )
    data = build_scene_dict(planned, out, ctx)
    if ctx.problems:
        raise CanonicalizationError(ctx.problems)
    for _ in range(3):  # lenient: clamp what the schema reports, then validate again
        try:
            return SCENE_ADAPTER.validate_python(data)
        except ValidationError as exc:
            if strict or not clamp_to_limits(data, exc):
                raise CanonicalizationError(format_validation_error(exc)) from exc
    try:
        return SCENE_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise CanonicalizationError(format_validation_error(exc)) from exc


def scene_problems(planned: PlannedScene, out: BaseModel, **kwargs: Any) -> list[str]:
    """All canonicalisation problems for ``out`` ([] = it canonicalises cleanly)."""
    try:
        canonicalize_scene(planned, out, strict=True, **kwargs)
    except CanonicalizationError as exc:
        return exc.problems
    return []


# ---------------------------------------------------------------------------
# Reverse: canonical scene -> PlannedScene (repair / regenerate)
# ---------------------------------------------------------------------------


def planned_from_scene(scene: Any, est_seconds: int | None = None) -> PlannedScene:
    """Reconstruct the PlannedScene a canonical scene was written from (its narrative role and bridge included,
    so a regenerated scene still continues from its neighbour)."""
    intent = scene.intent
    template = None
    if isinstance(scene, SimulationScene):
        template = scene.manim.template
    elif scene.side_panel is not None and scene.side_panel.manim is not None:
        template = scene.side_panel.manim.template
    miscs: list[str] = []
    if isinstance(scene, QuizScene):
        miscs = [m for m in scene.option_misconception_ids if m]
    elif isinstance(scene, BoardScene):
        miscs = [i.misconception_id for i in scene.board if i.misconception_id]
    goal = intent.goal if intent and intent.goal else (scene.title or scene.type)
    return PlannedScene(
        id=scene.id,
        type=scene.type,
        concept_id=scene.concept_id,
        objective_ids=list(scene.objective_ids),
        goal=goal[:600],
        key_points=list(intent.key_points) if intent else [],
        source_refs=list(intent.source_refs) if intent else [],
        side_panel_kind=scene.side_panel.kind if scene.side_panel else None,
        visual_rationale=scene.side_panel.rationale if scene.side_panel else getattr(scene, "rationale", ""),
        manim_template=template,
        misconception_ids=list(dict.fromkeys(miscs)),
        est_seconds=int(min(600, max(5, est_seconds or 45))),
        narrative_role=intent.narrative_role if intent else None,
        bridge_in=intent.bridge_in if intent else "",
    )
