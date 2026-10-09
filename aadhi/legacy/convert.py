"""v1 lecture JSON -> validated v2 ``Screenplay``.

Accepted inputs: the v1 project document ``{subject_name, unit_name, session_number, session_title,
concept_map, scenes, companion_sheet}``, the v1 browser backup ``{subjectName, ..., conceptMapData,
slides}``, or a bare list of v1 scenes. Every problem that changes or drops content is reported in
the returned warnings; the result always validates as a ``Screenplay``.

Scene types: ``title/content/example/summary/recap/key-takeaway`` -> ``BoardScene``;
``chapter_card``; ``quiz_checkpoint``; ``simulation``/``visual`` -> ``SimulationScene`` (free-form
code); ``ai_video``; ``p5_simulation`` -> ``InteractiveScene``. Board HTML -> typed items
(``html_board``), narration -> beats (``narration``), side panels -> ``SidePanel`` (``panels``).

Lessons saved by the friend's fork of v1 (``origin/andryan``) are v1 lessons with extra keys, and convert the same
way. Their per-scene editor data ``edit`` maps where v2 has the same idea: ``edit.hidden`` -> ``hidden`` (the
scene is kept but skipped by the timeline) and ``edit.min_seconds`` -> ``min_seconds`` (clamped to 1..600 s).
Muted narration and captions turned off for a scene are not supported and are reported. Media they made or
uploaded (``video_asset_id``, ``manim_asset_id``, ``manim_video_url``, ``side_panel.video_asset_id``,
``uploaded_image_assets``, ``<img src="asset:...">``) is never read and is reported like v1's ``*_url`` keys.
Their presenter, style, visual-review and Studio data (``cinematic_plan``, ``presenter_plan``, ``visual_plan``,
``visual_review``, ``visual_direction``, ``source``, ``quality``, ``mascot_state``; lesson-level ``studio``,
``editor``, ``cinematic_style``, ``source_document``) is ignored, with one summary warning each.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ..schemas.screenplay import (
    CompanionSheet,
    Scene,
    SceneBase,
    Screenplay,
    assert_acyclic,
    slugify,
)
from .html_board import BoardExtraction, extract_board, parse_html, plain_text
from .narration import BeatDraft, build_beats, cap_beats, clean_narration
from .panels import adapt_manim_code, convert_side_panel

__all__ = ["V1_SCENE_TYPES", "convert_legacy", "is_legacy"]

V1_SCENE_TYPES = frozenset(
    {
        "title",
        "content",
        "example",
        "summary",
        "recap",
        "key-takeaway",
        "key_takeaway",
        "chapter_card",
        "quiz_checkpoint",
        "simulation",
        "visual",
        "ai_video",
        "p5_simulation",
    }
)
_BOARD_TYPES = {
    "title": "title",
    "content": "content",
    "example": "example",
    "summary": "summary",
    "recap": "recap",
    "key-takeaway": "key_takeaway",
    "key_takeaway": "key_takeaway",
}
_MASCOT_POSITIONS = {"left", "right", "center", "popup_bottom_left", "popup_bottom_right", "hidden"}
# --- mascot placement (v1 slips and v1's implicit rule, see _mascot_position) ---
_MASCOT_ALIASES = {"popup": "popup_bottom_left", "none": "hidden"}
_V1_MASCOT_MAX_HTML = 300  # v1 hid Aadhi on title/content scenes with more HTML than this (no aadhi_position)
_CODE_KEYS = ("manim_code", "simulation_code", "code")
_V1_MARKERS = (
    "html",
    "aadhi_position",
    "manim_code",
    "simulation_code",
    "reveal_narration",
    "visual_reasoning",
    "uploaded_images",
)
MAX_BOARD_ITEMS = 12
MAX_SCENES = 200
MAX_FIGURES = 200
_SCENE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Scene)
# --- lessons saved by the friend's fork (origin/andryan): see the module docstring ---
_FORK_MEDIA_KEYS = ("video_asset_id", "manim_asset_id", "manim_video_url")
_FORK_SCENE_KEYS = (
    "cinematic_plan", "presenter_plan", "visual_plan", "visual_review", "visual_direction", "source", "quality",
    "mascot_state",
)
_FORK_LESSON_KEYS = {
    "studio": "Studio records",
    "editor": "editor settings",
    "cinematic_style": "video style",
    "source_document": "prepared-source link",
}
MIN_SCENE_SECONDS = 1.0
MAX_SCENE_SECONDS = 600.0


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _scenes_of(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        scenes = data.get("scenes", data.get("slides"))
        return scenes if isinstance(scenes, list) else None
    return None


def is_legacy(data: Any) -> bool:
    """True if ``data`` looks like a v1 lecture (dict with v1 scenes, or a bare list of v1 scenes)."""
    if isinstance(data, dict) and isinstance(data.get("schema_version"), int) and data["schema_version"] >= 2:
        return False
    scenes = _scenes_of(data)
    if not scenes or not all(isinstance(s, dict) for s in scenes):
        return False
    if any(isinstance(s.get("beats"), list) for s in scenes):
        return False
    for s in scenes:
        if isinstance(s.get("narration"), str) or any(k in s for k in _V1_MARKERS):
            return True
        if s.get("type") in ("key-takeaway", "p5_simulation", "visual"):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _clip(value: Any, limit: int) -> str:
    text = _s(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _strip_rich(text: str) -> str:
    """Rich-lite -> plain text (keeps ``$math$``)."""
    text = re.sub(r"\*\*|\[\[|\]\]|`", "", text)
    text = re.sub(r"(?<!\\)\*", "", text)
    return text.replace("\\*", "*")


def _plain_html(value: Any) -> str:
    """Plain text from a v1 string that may contain inline HTML."""
    text = _s(value)
    if "<" in text and ">" in text:
        text = plain_text(parse_html(text))
    return re.sub(r"\s+", " ", text).strip()


class _Ctx:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.concepts: set[str] = set()
        self.figures: dict[str, dict[str, Any]] = {}
        self.misconceptions: list[dict[str, Any]] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _beats(drafts: list[BeatDraft], scene_id: str) -> list[dict[str, Any]]:
    """Beat dicts (no board reveals) with ids ``<scene>-b<n>``."""
    return [
        {"id": f"{scene_id}-b{n}", "narration": d.narration, "pause_after": d.pause_after}
        for n, d in enumerate(drafts, start=1)
    ]


def _simple_beats(text: Any, scene_id: str, ctx: _Ctx, where: str, *, max_beats: int, fallback: str = "") -> list[dict]:
    drafts = build_beats(_s(text), warnings=ctx.warnings, where=where)
    if not drafts and fallback:
        drafts = [BeatDraft(clean_narration(fallback)[:1400])]
    drafts = cap_beats(drafts, max_beats, ctx.warnings, where)
    return _beats(drafts, scene_id)


# ---------------------------------------------------------------------------
# Concept map
# ---------------------------------------------------------------------------


def _dependency_list(raw: Any, cid: str, ctx: _Ctx) -> list[Any]:
    """v1 ``depends_on`` as a list: a single id string is one dependency; other shapes are dropped."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [d for d in raw if isinstance(d, (str, int)) and not isinstance(d, bool)]
    ctx.warn(f"concept {cid!r}: dependencies in an unknown format ({type(raw).__name__}) dropped")
    return []


def _concept_map(raw: Any, ctx: _Ctx) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    nodes: list[dict[str, Any]] = []
    for i, n in enumerate(raw):
        if not isinstance(n, dict):
            continue
        if len(nodes) >= 150:
            ctx.warn("concept map cut to 150 nodes")
            break
        cid = slugify(_s(n.get("id")) or _s(n.get("title")) or f"concept-{i + 1}", "concept")
        if any(x["id"] == cid for x in nodes):
            ctx.warn(f"duplicate concept id {cid!r} dropped")
            continue
        deps = [slugify(_s(d), "concept") for d in _dependency_list(n.get("depends_on"), cid, ctx) if _s(d)]
        nodes.append(
            {
                "id": cid,
                "title": _clip(_s(n.get("title")) or _s(n.get("label")) or cid, 160),
                "summary": _clip(n.get("summary"), 600),
                "depends_on": deps,
            }
        )
    ids = [x["id"] for x in nodes]
    known = set(ids)
    for x in nodes:
        bad = [d for d in x["depends_on"] if d not in known or d == x["id"]]
        if bad:
            ctx.warn(f"concept {x['id']!r}: unknown dependencies {bad} dropped")
        x["depends_on"] = list(dict.fromkeys(d for d in x["depends_on"] if d in known and d != x["id"]))[:20]
    try:
        assert_acyclic({x["id"]: x["depends_on"] for x in nodes})
    except ValueError:
        position = {cid: i for i, cid in enumerate(ids)}
        for x in nodes:  # keep only dependencies on earlier concepts (v1 lists them in teaching order)
            x["depends_on"] = [d for d in x["depends_on"] if position[d] < position[x["id"]]]
        ctx.warn("concept map had a dependency cycle; dependencies on later concepts were dropped")
    ctx.concepts = known
    return nodes


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def _mascot_position(raw: dict[str, Any], ctx: _Ctx, where: str) -> str | None:
    """v2 ``mascot_position`` of a v1 scene (None keeps the v2 default, ``left``).

    Explicit values are lower-cased and the common generator slips repaired (``popup`` ->
    ``popup_bottom_left``, ``none`` -> ``hidden``); unknown values warn and keep the default. Without
    ``aadhi_position`` v1's implicit rule applies: title/content scenes with at most 300 characters
    of HTML show Aadhi on the left, every other scene hides him, and ``force_background`` other than
    ``auto`` overrides that (``mascot`` shows him). ai_video scenes with no position or ``hidden``
    keep the default ``left``, as v1 forced Aadhi beside AI-video windows; other explicit positions are kept.
    """
    position = _s(raw.get("aadhi_position") or raw.get("mascot_position")).lower()
    position = _MASCOT_ALIASES.get(position, position)
    v1_type = _s(raw.get("type")).lower() or "content"
    if v1_type == "ai_video" and position in ("", "hidden"):
        return None  # v1 always kept Aadhi on the left beside AI videos, even when told 'hidden'
    if position:
        if position in _MASCOT_POSITIONS:
            return position
        ctx.warn(f"{where}: unknown mascot position {position!r}; using 'left'")
        return None
    html = raw.get("html")
    show = v1_type in ("title", "content") and len(html if isinstance(html, str) else "") <= _V1_MASCOT_MAX_HTML
    force = _s(raw.get("force_background")).lower()
    if force and force != "auto":
        show = force == "mascot"
    return "left" if show else "hidden"


def _common(raw: dict[str, Any], scene_id: str, ctx: _Ctx, where: str) -> dict[str, Any]:
    base: dict[str, Any] = {"id": scene_id, "title": _clip(_plain_html(raw.get("title")), 240)}
    subtitle = _clip(_plain_html(raw.get("subtitle")), 320)
    if subtitle:
        base["subtitle"] = subtitle
    position = _mascot_position(raw, ctx, where)
    if position:
        base["mascot_position"] = position
    concept = _s(raw.get("concept_id"))
    if concept:
        cid = slugify(concept, "concept")
        if cid in ctx.concepts:
            base["concept_id"] = cid
        else:
            ctx.warn(f"{where}: concept {concept!r} is not in the concept map; link removed")
    notes: list[str] = []
    if _s(raw.get("visual_reasoning")):
        notes.append("Visual reasoning (v1): " + _s(raw["visual_reasoning"]))
    for key in ("video_url", "image_url", "audio_url"):
        if _s(raw.get(key)):
            notes.append(f"v1 {key.replace('_', ' ')} not imported: {_s(raw[key])[:300]}")
            ctx.warn(f"{where}: existing v1 {key.replace('_', ' ')} was not imported; it will be regenerated")
    if notes:
        base["notes"] = _clip("\n".join(notes), 4000)
    side = raw.get("side_panel")
    if any(_s(raw.get(k)) for k in _FORK_MEDIA_KEYS) or (isinstance(side, dict) and _s(side.get("video_asset_id"))):
        ctx.warn(f"{where}: media made in the source app was not imported; it will be made again")
    panel = convert_side_panel(raw.get("side_panel"), scene_title=base["title"], warnings=ctx.warnings, where=where)
    if panel is not None:
        base["side_panel"] = panel
    return base


def _register_board_side_products(ext: BoardExtraction, ctx: _Ctx, where: str) -> None:
    """Register source figures and misconceptions found on the board (links items to them).

    At most ``MAX_FIGURES`` figures are kept; figure items pointing at a dropped figure become
    caption paragraphs. A misconception whose statement or correction is empty once the rich-lite
    markup is stripped is not registered and its item stays a plain warning callout.
    """
    for i, item in enumerate(ext.items):
        if item.get("kind") != "figure":
            continue
        fid = item.get("figure_id")
        if fid in ctx.figures:
            continue
        if len(ctx.figures) < MAX_FIGURES:
            caption = next((f.get("caption", "") for f in ext.figures if f.get("id") == fid), item.get("caption") or "")
            ctx.figures[fid] = {"id": fid, "caption": caption}
            continue
        ctx.warn(f"{where}: more than {MAX_FIGURES} figures; figure {fid!r} kept as its caption only")
        ext.items[i] = {"kind": "paragraph", "text": _clip(item.get("caption") or fid or "Figure", 1200)}
    for m in ext.misconceptions:
        statement = _clip(_strip_rich(m["statement"]), 600).strip()
        correction = _clip(_strip_rich(m["correction"]), 800).strip()
        index = m["item"]
        if not (0 <= index < len(ext.items)):
            continue
        if not statement or not correction:
            item = ext.items[index]
            text = item.get("text") or statement or correction or "Common misconception"
            ext.items[index] = {"kind": "callout_warning", "text": text}
            ctx.warn(f"{where}: misconception without a usable statement and correction kept as a warning callout")
            continue
        mid = f"m{len(ctx.misconceptions) + 1}"
        ctx.misconceptions.append({"id": mid, "statement": statement, "correction": correction})
        ext.items[index]["misconception_id"] = mid


def _board_scenes(raw: dict[str, Any], scene_id: str, scene_type: str, ctx: _Ctx, where: str) -> list[dict[str, Any]]:
    base = _common(raw, scene_id, ctx, where)
    ext = extract_board(_s(raw.get("html")), scene_type=scene_type, warnings=ctx.warnings, where=where)
    uploaded = raw.get("uploaded_images")
    if isinstance(uploaded, dict) and uploaded:
        ctx.warn(f"{where}: {len(uploaded)} uploaded board image(s) must be re-uploaded as figures in v2")
    fork_uploads = raw.get("uploaded_image_assets")
    if isinstance(fork_uploads, (dict, list)) and fork_uploads and not (isinstance(uploaded, dict) and uploaded):
        ctx.warn(f"{where}: {len(fork_uploads)} uploaded board image(s) must be re-uploaded as figures in v2")
    drafts = build_beats(
        _s(raw.get("narration")),
        n_slots=len(ext.sync_slots),
        readable=lambda slot: ext.readable_text(ext.sync_slots[slot]),
        warnings=ctx.warnings,
        where=where,
    )
    if not drafts:
        fallback = base["title"] or next(
            (ext.readable_text(i) for i in range(len(ext.items)) if ext.readable_text(i)), ""
        )
        drafts = [BeatDraft(clean_narration(fallback)[:1400] or "Let's take a look.")]
        ctx.warn(f"{where}: scene had no narration; a short narration was added")
    # Map v1 sync slots to board item indexes; a slot whose element produced nothing reveals nothing.
    slot_item = {i: idx for i, idx in enumerate(ext.sync_slots) if idx is not None}
    for d in drafts:
        d.slot = slot_item.get(d.slot) if d.slot is not None else None  # now an item index
    items = ext.items
    parts: list[tuple[list[int], list[BeatDraft]]] = []
    if len(items) <= MAX_BOARD_ITEMS:
        parts.append((list(range(len(items))), drafts))
    else:
        ctx.warn(f"{where}: board had {len(items)} items; continued in extra scene(s) (max {MAX_BOARD_ITEMS} each)")
        groups = [list(range(i, min(i + MAX_BOARD_ITEMS, len(items)))) for i in range(0, len(items), MAX_BOARD_ITEMS)]
        group_of = {idx: g for g, members in enumerate(groups) for idx in members}
        beats_by_group: list[list[BeatDraft]] = [[] for _ in groups]
        current = 0
        for d in drafts:
            if d.slot is not None:
                current = max(current, group_of[d.slot])  # never move backwards in the board
                if group_of[d.slot] != current:
                    d.slot = None
            beats_by_group[current].append(d)
        for g, members in enumerate(groups):
            if not beats_by_group[g]:
                text = next((ext.readable_text(i) for i in members if ext.readable_text(i)), "") or base["title"]
                beats_by_group[g].append(BeatDraft(clean_narration(text)[:1400] or "Let's continue.", 0.0, None))
            parts.append((members, beats_by_group[g]))

    part_ids = [scene_id if p == 1 else f"{scene_id}-{p}" for p in range(1, len(parts) + 1)]
    item_ids: dict[int, str] = {
        idx: f"{part_ids[p]}-i{k}" for p, (members, _) in enumerate(parts) for k, idx in enumerate(members, start=1)
    }
    _register_board_side_products(ext, ctx, where)

    scenes: list[dict[str, Any]] = []
    for part_no, (members, part_drafts) in enumerate(parts, start=1):
        sid = part_ids[part_no - 1]
        part_drafts = cap_beats(part_drafts, 40, ctx.warnings, where)
        beats = []
        for n, d in enumerate(part_drafts, start=1):
            beat: dict[str, Any] = {"id": f"{sid}-b{n}", "narration": d.narration, "pause_after": d.pause_after}
            if d.slot is not None and d.slot in members:
                beat["board_item_id"] = item_ids[d.slot]
            beats.append(beat)
        board = []
        for idx in members:
            item = dict(items[idx])
            item["id"] = item_ids[idx]
            board.append(item)
        scene = dict(base, id=sid, type=scene_type, board=board, beats=beats)
        if part_no > 1:
            scene["title"] = _clip(f"{base['title']} (continued)" if base["title"] else "(continued)", 240)
            scene.pop("side_panel", None)
            scene.pop("notes", None)
        scenes.append(scene)
    return scenes


def _quiz_scene(raw: dict[str, Any], scene_id: str, ctx: _Ctx, where: str) -> dict[str, Any] | None:
    question = _clip(_plain_html(raw.get("question")), 600)
    options_in = raw.get("options") if isinstance(raw.get("options"), list) else []
    options_raw = [_clip(_plain_html(o), 240) for o in options_in]
    feedback_raw = raw.get("feedback_wrong") if isinstance(raw.get("feedback_wrong"), list) else []
    try:
        correct = int(raw.get("correct_index", 0))
    except (TypeError, ValueError):
        correct = -1
    pairs = [
        (o, _plain_html(feedback_raw[i]) if i < len(feedback_raw) else "", i == correct)
        for i, o in enumerate(options_raw)
        if o
    ]
    if not question or len(pairs) < 2:
        ctx.warn(f"{where}: quiz without a question or at least two options converted to a content scene")
        return None
    base = _common(raw, scene_id, ctx, where)
    if not any(c for _, _, c in pairs):
        ctx.warn(f"{where}: quiz correct_index out of range; first option marked correct")
        pairs[0] = (pairs[0][0], "", True)
    if len(pairs) > 5:
        keep = [p for p in pairs if p[2]] + [p for p in pairs if not p[2]][:4]
        pairs = [p for p in pairs if p in keep]
        ctx.warn(f"{where}: quiz cut to 5 options")
    seen: dict[str, int] = {}
    options = []
    for o, _, _ in pairs:
        key = o.strip().lower()
        seen[key] = seen.get(key, 0) + 1
        options.append(o if seen[key] == 1 else _clip(f"{o} ({seen[key]})", 240))
    if any(v > 1 for v in seen.values()):
        ctx.warn(f"{where}: duplicate quiz options were made distinct")
    correct_index = next(i for i, p in enumerate(pairs) if p[2])
    try:
        countdown = int(raw.get("countdown_seconds", 8))
    except (TypeError, ValueError):
        countdown = 8
    beats = _simple_beats(raw.get("narration"), scene_id, ctx, where, max_beats=40, fallback=question)
    reveal = build_beats(_s(raw.get("reveal_narration")), warnings=ctx.warnings, where=where)
    if not reveal:
        explanation = _plain_html(raw.get("explanation"))
        reveal = [BeatDraft(clean_narration(f"The answer is: {options[correct_index]}. {explanation}")[:1400])]
    reveal = cap_beats(reveal, 10, ctx.warnings, where)
    reveal_beats = [
        {"id": f"{scene_id}-r{n}", "narration": d.narration, "pause_after": d.pause_after}
        for n, d in enumerate(reveal, start=1)
    ]
    return dict(
        base,
        type="quiz_checkpoint",
        question=question,
        options=options,
        correct_index=correct_index,
        feedback_wrong=["" if p[2] else p[1] for p in pairs],
        explanation=_clip(_plain_html(raw.get("explanation")), 900),
        countdown_seconds=min(max(countdown, 3), 30),
        beats=beats,
        reveal_beats=reveal_beats,
    )


def _convert_scene(raw: dict[str, Any], scene_id: str, ctx: _Ctx) -> list[dict[str, Any]]:
    v1_type = _s(raw.get("type")).lower() or "content"
    where = f"scene {scene_id} ({v1_type})"
    if v1_type in _BOARD_TYPES:
        return _board_scenes(raw, scene_id, _BOARD_TYPES[v1_type], ctx, where)
    if v1_type == "chapter_card":
        base = _common(raw, scene_id, ctx, where)
        beats = _simple_beats(raw.get("narration"), scene_id, ctx, where, max_beats=4)
        label = _clip(_plain_html(raw.get("chapter_label")), 60) or "Part"
        return [dict(base, type="chapter_card", chapter_label=label, beats=beats)]
    if v1_type == "quiz_checkpoint":
        quiz = _quiz_scene(raw, scene_id, ctx, where)
        if quiz is not None:
            return [quiz]
        fallback = dict(raw, html=f"<p>{_s(raw.get('question'))}</p>" if _s(raw.get("question")) else "")
        return _board_scenes(fallback, scene_id, "content", ctx, where)
    if v1_type in ("simulation", "visual"):
        # v1 stored the animation under any of these keys (index.html: manim_code || simulation_code || code)
        source = next((raw[k] for k in _CODE_KEYS if isinstance(raw.get(k), str) and raw[k].strip()), "")
        code, rebased = adapt_manim_code(source)
        if not code.strip() or len(code) > 20000:
            ctx.warn(f"{where}: simulation without usable Manim code converted to a content scene")
            return _board_scenes(dict(raw, html=raw.get("html") or ""), scene_id, "content", ctx, where)
        base = _common(raw, scene_id, ctx, where)
        beats = _simple_beats(
            raw.get("narration"), scene_id, ctx, where, max_beats=40, fallback=base["title"] or "Watch."
        )
        ctx.warn(
            f"{where}: v1 Manim code imported as free-form code"
            + ("" if rebased else " (its class is not a plain Scene subclass, so it was not re-based on AadhiScene)")
            + "; it is not synchronised to the beats -- prefer a template or review it"
        )
        return [dict(base, type="simulation", manim={"code": code}, beats=beats)]
    if v1_type == "ai_video":
        base = _common(raw, scene_id, ctx, where)
        prompt = _s(raw.get("prompt") or raw.get("video_prompt")) or base["title"] or "Real-world footage"
        if len(prompt) > 1200:
            ctx.warn(f"{where}: video prompt shortened to 1200 characters")
        beats = _simple_beats(
            raw.get("narration"), scene_id, ctx, where, max_beats=40, fallback=base["title"] or "Watch."
        )
        return [
            dict(
                base,
                type="ai_video",
                video_prompt=_clip(prompt, 1200),
                fallback_image_prompt=_clip(prompt, 800),
                beats=beats,
            )
        ]
    if v1_type == "p5_simulation":
        code = _s(raw.get("p5_code"))
        if not code or len(code) > 20000:
            ctx.warn(f"{where}: interactive scene without usable p5 code converted to a content scene")
            return _board_scenes(raw, scene_id, "content", ctx, where)
        base = _common(raw, scene_id, ctx, where)
        beats = _simple_beats(
            raw.get("narration"), scene_id, ctx, where, max_beats=40, fallback=base["title"] or "Try it."
        )
        ctx.warn(f"{where}: interactive p5 sketch runs only in the web player (MP4 renders show a poster)")
        return [dict(base, type="interactive", p5_code=code, beats=beats)]
    ctx.warn(f"{where}: unknown v1 scene type converted to a content scene")
    return _board_scenes(raw, scene_id, "content", ctx, where)


def _validate_scene(scene: dict[str, Any], raw: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    try:
        return _SCENE_ADAPTER.validate_python(scene).model_dump(mode="json")
    except ValidationError as exc:
        problem = exc.errors()[0].get("msg", "invalid") if exc.errors() else "invalid"
        ctx.warn(f"scene {scene['id']}: could not be converted faithfully ({problem}); imported as narration only")
    title = _clip(_plain_html(raw.get("title")), 240)
    drafts = cap_beats(build_beats(_s(raw.get("narration") or raw.get("question"))), 40)
    if not drafts:
        drafts = [BeatDraft(clean_narration(title)[:1400] or "Imported scene.")]
    minimal = {"id": scene["id"], "type": "content", "title": title, "board": [], "beats": _beats(drafts, scene["id"])}
    return _SCENE_ADAPTER.validate_python(minimal).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Editor data of lessons saved by the friend's fork
# ---------------------------------------------------------------------------


def _hold_seconds(value: Any, ctx: _Ctx, where: str) -> float | None:
    """``edit.min_seconds`` as v2's ``min_seconds`` (1..600 s), or None when unusable (reported)."""
    try:
        number = math.inf if isinstance(value, bool) or not isinstance(value, (int, float)) else float(value)
    except OverflowError:  # an integer too large for a float
        number = 1e9
    if not math.isfinite(number) or number <= 0:
        ctx.warn(f"{where}: minimum duration from the source app is not a usable number; ignored")
        return None
    seconds = round(number, 2)
    clamped = min(max(seconds, MIN_SCENE_SECONDS), MAX_SCENE_SECONDS)
    if clamped != seconds:
        ctx.warn(f"{where}: minimum duration {seconds:g} s set to {clamped:g} s (v2 allows "
                 f"{MIN_SCENE_SECONDS:g} to {MAX_SCENE_SECONDS:g} s)")
    return clamped


def _apply_edit(raw: dict[str, Any], scenes: list[dict[str, Any]], ctx: _Ctx, where: str) -> list[dict[str, Any]]:
    """Carry a fork scene's ``edit`` over to its converted scene(s): ``hidden`` on every part, ``min_seconds`` on
    the last part. Without v2 support for hidden scenes a hidden scene is left out (reported), never shown."""
    edit = raw.get("edit")
    if not isinstance(edit, dict) or not scenes:
        return scenes
    if edit.get("hidden") is True:
        if "hidden" not in SceneBase.model_fields:  # pragma: no cover - schemas older than hidden scenes
            ctx.warn(f"{where}: hidden in the source app; not imported")
            return []
        for scene in scenes:
            scene["hidden"] = True
        ctx.warn(f"{where}: hidden in the source app; imported as a hidden scene")
    if edit.get("min_seconds") is not None and "min_seconds" in SceneBase.model_fields:
        seconds = _hold_seconds(edit["min_seconds"], ctx, where)
        if seconds is not None:
            scenes[-1]["min_seconds"] = seconds
            if len(scenes) > 1:
                ctx.warn(f"{where}: minimum duration applied to the last of its {len(scenes)} parts")
    if edit.get("narration_muted") is True:
        ctx.warn(f"{where}: narration was muted in the source app; v2 speaks it (edit or hide the scene)")
    if edit.get("captions") == "off":
        ctx.warn(f"{where}: captions were turned off for this scene in the source app; v2 shows them")
    return scenes


def _fork_summary(meta: dict[str, Any], scenes_raw: list[Any], ctx: _Ctx) -> None:
    """One warning per kind of fork data that v2 does not use (lesson settings, per-scene plans)."""
    lesson = [label for key, label in _FORK_LESSON_KEYS.items() if meta.get(key)]
    if lesson:
        ctx.warn(f"the source app's {', '.join(lesson)} were not imported")
    with_plans = sum(1 for s in scenes_raw if isinstance(s, dict) and any(s.get(k) for k in _FORK_SCENE_KEYS))
    if with_plans:
        ctx.warn(f"{with_plans} scene(s) carried presenter, style or review data from the source app that v2 does "
                 "not use; it was not imported")
    originals = sum(1 for s in scenes_raw if isinstance(s, dict) and isinstance(s.get("edit"), dict)
                    and s["edit"].get("original"))
    if originals:
        ctx.warn(f"{originals} scene(s) kept the source app's record of the generated text; it was not imported")


# ---------------------------------------------------------------------------
# Chapters, companion, entry point
# ---------------------------------------------------------------------------


def _chapters(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not any(s["type"] == "chapter_card" for s in scenes):
        return []
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    cards = 0
    for s in scenes:
        if s["type"] == "chapter_card" and len(chapters) < 40:
            cards += 1
            title = _clip(s.get("title") or s.get("chapter_label") or f"Part {cards}", 160)
            current = {"id": f"ch{cards}", "title": title, "scene_ids": []}
            chapters.append(current)
        elif current is None:
            current = {"id": "ch0", "title": "Introduction", "scene_ids": []}
            chapters.append(current)
        if len(current["scene_ids"]) < 200:
            current["scene_ids"].append(s["id"])
            s["chapter_id"] = current["id"]
    return chapters


def _companion(raw: Any, ctx: _Ctx) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        if len(raw) > 60000:
            ctx.warn("companion sheet shortened to 60000 characters")
        return {"legacy_markdown": raw[:60000]}
    if isinstance(raw, dict):
        try:
            return CompanionSheet.model_validate(raw).model_dump(mode="json")
        except ValidationError:
            pass
    ctx.warn("companion sheet in an unknown format; kept as text")
    return {"legacy_markdown": json.dumps(raw, ensure_ascii=False, indent=2)[:60000]}


def convert_legacy(data: dict | list) -> tuple[Screenplay, list[str]]:
    """Convert a v1 lecture (dict or bare scene list) into ``(Screenplay, warnings)``.

    Raises ``ValueError`` only if ``data`` has no usable scene list.
    """
    scenes_raw = _scenes_of(data)
    if scenes_raw is None:
        raise ValueError("not a v1 lecture: expected a list of scenes or an object with 'scenes'")
    meta: dict[str, Any] = data if isinstance(data, dict) else {}
    ctx = _Ctx()
    concept_map = _concept_map(meta.get("concept_map", meta.get("conceptMapData")), ctx)
    _fork_summary(meta, scenes_raw, ctx)

    scenes: list[dict[str, Any]] = []
    for n, raw in enumerate(scenes_raw, start=1):
        if not isinstance(raw, dict):
            ctx.warn(f"scene {n}: not an object; skipped")
            continue
        sid = f"s{n}"
        try:
            converted = _convert_scene(raw, sid, ctx)
        except Exception as exc:  # noqa: BLE001 - one malformed v1 scene must not sink the whole import
            ctx.warn(f"scene {sid}: conversion error ({type(exc).__name__}); imported as narration only")
            converted = [{"id": sid, "type": "content", "title": "", "board": [], "beats": []}]
        scenes.extend(_apply_edit(raw, [_validate_scene(s, raw, ctx) for s in converted], ctx, f"scene {sid}"))
    if len(scenes) > MAX_SCENES:
        ctx.warn(f"lecture cut to {MAX_SCENES} scenes")
        scenes = scenes[:MAX_SCENES]
    chapters = _chapters(scenes)

    def meta_str(*keys: str, limit: int) -> str:
        for k in keys:
            if _s(meta.get(k)):
                return _clip(_plain_html(meta[k]), limit)
        return ""

    doc = {
        "schema_version": 2,
        "subject_name": meta_str("subject_name", "subjectName", limit=240),
        "unit_name": meta_str("unit_name", "unitName", limit=240),
        "session_number": meta_str("session_number", "sessionNumber", limit=60) or "Session 1",
        "session_title": meta_str("session_title", "sessionTitle", limit=300),
        "language": "en-IN",
        "concept_map": concept_map,
        "misconceptions": ctx.misconceptions[:60],
        "chapters": chapters,
        "scenes": scenes,
        "companion_sheet": _companion(meta.get("companion_sheet"), ctx),
        "figures": list(ctx.figures.values())[:200],
    }
    if len(ctx.misconceptions) > 60:
        ctx.warn("only the first 60 misconceptions were kept")
        keep = {m["id"] for m in ctx.misconceptions[:60]}
        for s in scenes:
            for item in s.get("board", []):
                if item.get("misconception_id") and item["misconception_id"] not in keep:
                    item["misconception_id"] = None
    return _validate_document(doc, ctx), ctx.warnings


def _validate_document(doc: dict[str, Any], ctx: _Ctx) -> Screenplay:
    """Validate the assembled document. Every scene already validated on its own, so a failure here
    is a cross-reference problem: retry once without chapters, concept/misconception links and
    figures (keeping all scene content) rather than losing the whole lecture."""
    try:
        return Screenplay.model_validate(doc)
    except ValidationError as exc:
        problem = exc.errors()[0].get("msg", "invalid") if exc.errors() else "invalid"
        ctx.warn(
            f"lecture links could not be validated ({problem[:200]}); chapters, concept map, misconceptions "
            "and figure references were dropped"
        )
    for scene in doc["scenes"]:
        scene.pop("chapter_id", None)
        scene.pop("concept_id", None)
        if scene.get("option_misconception_ids"):
            scene["option_misconception_ids"] = []
        for item in scene.get("board") or []:
            item["misconception_id"] = None
    doc.update(chapters=[], concept_map=[], misconceptions=[], figures=[])
    return Screenplay.model_validate(doc)
