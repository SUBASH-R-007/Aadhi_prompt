"""Dynamic render preflight (DYNAMIC_VIDEO_DESIGN §7.12): check what is deterministic before capture, fix what can be
fixed automatically, block only with an action the teacher can take.

**How.** The worker loads the stage once (``stage_page``; the timeline it holds is served to the page; media frames
are 1x1 stand-ins, text does not depend on them; the caption layer is the render's own, so caption boxes are drawn
and checked only when the render burns captions) and seeks the sampled frames: 24 spread frames (the centres of 24
equal parts), every scene's first frame, every scene's last frame (its fullest board: every item revealed; an
addition to §7.12's list), every beat's settled frame (``BEAT_SETTLE_S`` after the beat starts: the card it reveals
is in, and a card that later collapses is still on screen; also an addition, B1 integration) and, in 9:16, every
caption chunk's first frame (``TimedScene.kinetic_captions``; without them, every phrase caption's). At each
frame it reads ``aadhiStage.textBoxes()`` (``{x, y, w, h, role, critical, ink, bg, overflow}``, plus the optional
``scene``, ``slot``, ``size``, ``weight``, ``min`` and ``text`` this module reads when the stage sends them).

**Checks in B1.**

=============== ==================================================== ===============================================
Check           Rule                                                 On failure
=============== ==================================================== ===============================================
text_overflow   §4.6: a critical or caption box reports ``overflow`` Shrink the slot to its minimum size
                                                                     (``look.fixes.slots[slot].size``); still
                                                                     overflowing: block with "Shorten text".
                                                                     Decorative boxes only warn
safe_zones      R-59 (``zones.py``, the boxes the stage draws        Force the template's safe layout
                with): critical boxes inside the critical            (``look.fixes.safe = true``); still outside:
                area, caption boxes inside the caption band; with    block with "Shorten text"
                burned captions no other critical box may reach
                into the caption band (the caption covers it)
contrast        R-53 from the declared ink and background colours:   Force the scrim on
                4.5:1 body; 3:1 at >= 32 px, or >= 24 px bold;       (``look.fixes.slots[slot].scrim = true``);
                formula/entity text 4.5:1; captions 7:1 (target)     never blocks (a warning remains)
type_floor      R-29 from the declared size and weight: labels and   Warn only (the stage keeps these floors
                the chip >= 40 px (16:9) / >= 44 px (9:16); 9:16     itself: a warning means a stage regression)
                text at <= 64 px weight >= 600
=============== ==================================================== ===============================================

Remedies are applied to the served timeline (``apply_remedies``) and the affected scenes' frames are checked once
more on the reloaded page; the capture must use :attr:`PreflightResult.timeline`. A stage that ignores a remedy
simply leaves the problem in place, and the second round turns it into a block or a warning as above.

**The fixes channel** (plan B2 §1.2, deviation 2). Remedies go to ``look.fixes`` (``schemas.timeline.LookFixes``:
``{safe, slots: {slot: {size?, scrim?}}}``) and only there, a channel of their own: in B2 ``look.variant`` is
direct.py's choice (never ``"safe"``) and ``look.text_fit`` textfit.py's. B1 wrote them to ``look.variant = "safe"``
and ``look.text_fit[slot]``; the stage reads ``fixes`` first and those legacy fields second (``web/js/dynamic/core.js``
``fixesOf``, ``board_generic`` slotTuning), so an engine_version 1 timeline remedied before B2a keeps its remedies,
and a scene is already on its safe layout when either channel says so (:func:`look_is_safe`).

**Result.** :meth:`PreflightResult.summary` is the frozen preflight schema (``PREFLIGHT_SCHEMA``), stored in
``Render.options["qa"]["preflight"]`` (``container.motion_qa``; :meth:`PreflightResult.as_qa` when the render stops
at preflight).

Not checked here yet (they need later batches): duration profile and the fit step (B4), Aadhi time (B4), the 9:16
frame-0 hook band (B2 zones / B4), licences (B6), music under speech and master loudness (B3), caption re-chunking
(B2 ``kinetic.py``).
"""

from __future__ import annotations

import copy
import math
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...config import Settings
from ...jobs.base import FatalJobError
from ...schemas.timeline import Timeline
from ..base import timeline_stage
from ..motion.chunks import scene_frame_bounds, total_frames
from . import zones
from .colour import text_contrast
from .presenter import APPEARANCE_NAMES, appearance_of
from .stage_page import StageInfo, StagePage, StageScene, placeholder_frames, stage_browser

PREFLIGHT_SCHEMA = 1
SPREAD_FRAMES = 24
# A card enters 0.12 s before its beat and takes 0.5 s (board_generic): settled this long after the beat starts.
BEAT_SETTLE_S = 0.6
SAFE_VARIANT = "safe"  # B1's legacy channel, read only from B2a: look.variant of a scene forced onto its safe layout

# R-59 from zones.py (the one source of the zones; core.js draws with the same boxes): (y0, y1, x0, x1) in stage px.
# A box must lie inside the union of its bands, and every band its vertical extent overlaps must hold its
# horizontal extent.
CRITICAL_BANDS: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    fmt: tuple(box.band() for box in zones.ZONES[fmt].critical) for fmt in zones.FORMATS}
CAPTION_BANDS: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    fmt: (zones.ZONES[fmt].caption.band(),) for fmt in zones.FORMATS}
ZONE_TOLERANCE_PX = 1.0  # DOM rects are fractional
OVERFLOW_TOLERANCE_PX = 0.5
# R-29 floors from zones.TYPE_FLOORS: "never < 36" / "never < 40"; labels >= 40 px (16:9), >= 44 px (9:16)
MIN_TEXT_PX = {fmt: zones.TYPE_FLOORS[fmt].text_min for fmt in zones.FORMATS}
MIN_LABEL_PX = {fmt: float(zones.TYPE_FLOORS[fmt].label_min) for fmt in zones.FORMATS}
LABEL_ROLES = frozenset({"label", "chip"})
_PORTRAIT_FLOORS = zones.TYPE_FLOORS["portrait"]
if _PORTRAIT_FLOORS.heavy_max_px is None or _PORTRAIT_FLOORS.heavy_min_weight is None:  # pragma: no cover
    raise RuntimeError("zones.TYPE_FLOORS['portrait'] lacks R-29's weight rule")
PORTRAIT_HEAVY_BELOW_PX = float(_PORTRAIT_FLOORS.heavy_max_px)  # R-29 (9:16): text at <= 64 px ...
PORTRAIT_MIN_WEIGHT = float(_PORTRAIT_FLOORS.heavy_min_weight)  # ... is at least weight 600

# R-53
CONTRAST_BODY = 4.5
CONTRAST_LARGE = 3.0
CONTRAST_CAPTION_TARGET = 7.0
LARGE_REGULAR_PX = 32.0
LARGE_BOLD_PX = 24.0
BOLD_WEIGHT = 700.0
CAPTION_ROLES = frozenset({"caption", "captions"})
STRICT_ROLES = frozenset({"formula", "symbol", "entity"})  # 4.5:1 at any size

CHECKS = ("text_overflow", "safe_zones", "contrast", "type_floor")
REMEDY_FOR = {"text_overflow": "shrink", "safe_zones": "safe_variant", "contrast": "scrim"}
ACTION_SHORTEN_TEXT = "Shorten text"

_MESSAGES = {
    ("text_overflow", "block"): "The text does not fit its box ({slot}) even at the smallest size.",
    ("text_overflow", "warn"): "A decorative text ({slot}) does not fit its box.",
    ("text_overflow", "fixed"): "The text ({slot}) was set smaller so that it fits its box.",
    ("safe_zones", "block"): "The text ({slot}) runs outside the safe area of the frame, where player controls, "
                             "platform buttons or the caption band would cover it.",
    ("safe_zones", "fixed"): "The scene was switched to its safe layout so that the text ({slot}) stays clear of "
                             "player controls and platform buttons.",
    ("contrast", "warn"): "The text ({slot}) is hard to read on its background ({ratio}:1; {required}:1 needed).",
    ("contrast", "fixed"): "A background scrim was added behind the text ({slot}) so that it is easy to read.",
    ("type_floor", "warn"): "The text ({slot}) is smaller or lighter than the type floor of this format (R-29).",
}


# ---------------------------------------------------------------------------
# Text boxes
# ---------------------------------------------------------------------------


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _text(v: Any, limit: int = 60) -> str:
    return " ".join(str(v).split())[:limit] if isinstance(v, str) else ""


@dataclass(frozen=True)
class TextBox:
    """One entry of ``aadhiStage.textBoxes()`` (stage px)."""

    x: float
    y: float
    w: float
    h: float
    role: str = "text"
    critical: bool = False
    ink: str | None = None
    bg: str | None = None
    overflow: float = 0.0  # px the text overflows its box (``True`` = 1 px; 0 = fits)
    scene: int | None = None  # scene index the box belongs to (else the frame's scene)
    slot: str | None = None  # the ``ResolvedLook.text_fit`` key of the box (else its role)
    size: float | None = None  # font size, px
    weight: float | None = None
    min_size: float | None = None  # the slot's smallest allowed size, px
    text: str = ""

    @classmethod
    def parse(cls, raw: Any) -> TextBox | None:
        """A box from the page's dict, or None when its geometry is unusable."""
        if not isinstance(raw, dict):
            return None
        x, y, w, h = (_number(raw.get(k)) for k in ("x", "y", "w", "h"))
        if x is None or y is None or w is None or h is None or w < 0 or h < 0:
            return None
        overflow = raw.get("overflow")
        if overflow is True:
            spill = 1.0
        else:
            spill = max(0.0, _number(overflow) or 0.0)
        scene = raw.get("scene")
        role = _text(raw.get("role"), 40) or "text"
        return cls(
            x=x, y=y, w=w, h=h, role=role.lower(), critical=raw.get("critical") is True,
            ink=raw.get("ink") if isinstance(raw.get("ink"), str) else None,
            bg=raw.get("bg") if isinstance(raw.get("bg"), str) else None,
            overflow=spill,
            scene=scene if isinstance(scene, int) and not isinstance(scene, bool) and scene >= 0 else None,
            slot=_text(raw.get("slot"), 60) or None, size=_number(raw.get("size")), weight=_number(raw.get("weight")),
            min_size=_number(raw.get("min")), text=_text(raw.get("text")))

    @property
    def key(self) -> str:
        """The slot the remedies address (``slot``, else ``role``)."""
        return self.slot or self.role

    @property
    def is_caption(self) -> bool:
        return self.role in CAPTION_ROLES

    def rect(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.w, 1), round(self.h, 1)]


def inside_bands(box: TextBox, bands: Iterable[tuple[float, float, float, float]], *,
                 tolerance: float = ZONE_TOLERANCE_PX) -> bool:
    """True when ``box`` lies inside the union of ``bands`` ((y0, y1, x0, x1)), each band it overlaps holding its
    horizontal extent (an empty box always fits)."""
    if box.w < 1 or box.h < 1:
        return True
    top, bottom, left, right = box.y, box.y + box.h, box.x, box.x + box.w
    covered = 0.0
    for y0, y1, x0, x1 in bands:
        overlap = min(bottom, y1) - max(top, y0)
        if overlap <= tolerance:
            continue
        if left < x0 - tolerance or right > x1 + tolerance:
            return False
        covered += overlap
    return covered >= box.h - tolerance


def overlaps_bands(box: TextBox, bands: Iterable[tuple[float, float, float, float]], *,
                   tolerance: float = ZONE_TOLERANCE_PX) -> bool:
    """True when ``box`` covers part of any of ``bands`` ((y0, y1, x0, x1)) by more than ``tolerance`` both ways."""
    if box.w < 1 or box.h < 1:
        return False
    for y0, y1, x0, x1 in bands:
        if (min(box.y + box.h, y1) - max(box.y, y0) > tolerance
                and min(box.x + box.w, x1) - max(box.x, x0) > tolerance):
            return True
    return False


def below_type_floor(box: TextBox, fmt: str) -> bool:
    """R-29 from the size and weight the stage declared (a box without them is not judged)."""
    if box.size is None:
        return False
    if box.role in LABEL_ROLES and box.size + 0.01 < MIN_LABEL_PX.get(fmt, MIN_LABEL_PX["landscape"]):
        return True
    return (fmt == "portrait" and box.weight is not None and box.size <= PORTRAIT_HEAVY_BELOW_PX
            and box.weight + 0.01 < PORTRAIT_MIN_WEIGHT)


def contrast_required(box: TextBox) -> float:
    """The R-53 minimum (or, for captions, the target) contrast of ``box``."""
    if box.is_caption:
        return CONTRAST_CAPTION_TARGET
    if box.role in STRICT_ROLES:
        return CONTRAST_BODY
    if box.size is not None:
        bold = (box.weight or 400.0) >= BOLD_WEIGHT
        if box.size >= LARGE_REGULAR_PX or (bold and box.size >= LARGE_BOLD_PX):
            return CONTRAST_LARGE
    return CONTRAST_BODY


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """One problem with one slot of one scene (merged over every frame it was seen at)."""

    check: str
    scene_index: int | None
    slot: str
    role: str
    frame: int
    box: list[float]
    remedy: str | None = None
    ratio: float | None = None
    required: float | None = None
    overflow: float | None = None
    frames: int = 1
    status: str = "open"  # open -> fixed | block | warn
    text: str = ""

    @property
    def ident(self) -> tuple[str, int | None, str]:
        return self.check, self.scene_index, self.slot

    def merge(self, other: Finding) -> None:
        self.frames += other.frames
        # a decorative sighting (``warn`` at once: text not checked, such as critical text mid-entrance, which the
        # stage reports as not critical until it is fully shown) never masks a checked one of the same slot: the
        # checked sighting's status and remedy rule, and a warn sighting's missing remedy never clears a checked
        # one's (B2c review fix round: a 9:16 phrase overflowing at its minimum was first seen mid-entrance, so the
        # merged finding stayed a warning and preflight never blocked it)
        if self.status == "warn" and other.status != "warn":
            self.status, self.remedy, self.frame, self.box = other.status, other.remedy, other.frame, other.box
            self.ratio = other.ratio if other.ratio is not None else self.ratio
            if other.overflow is not None:
                self.overflow = max(other.overflow, self.overflow or 0.0)
            return
        if other.status == "warn" and self.status != "warn":
            if other.overflow is not None and (self.overflow is None or other.overflow > self.overflow):
                self.overflow = other.overflow
            return
        if other.ratio is not None and (self.ratio is None or other.ratio < self.ratio):
            self.ratio, self.frame, self.box = other.ratio, other.frame, other.box
        if other.overflow is not None and (self.overflow is None or other.overflow > self.overflow):
            self.overflow = other.overflow
        if self.remedy is not None and other.remedy is None:
            self.remedy = None  # the slot is already at its limit somewhere: no remedy for it


def evaluate_box(box: TextBox, *, fmt: str, frame: int, scene_index: int | None,
                 safe: bool = False, captions: bool = False) -> tuple[list[Finding], bool]:
    """The findings of one box (``fmt``: ``landscape``/``portrait``) and whether its contrast could be measured.
    ``safe``: the scene is already on its safe layout (:func:`look_is_safe`), so it gets no zone remedy;
    ``captions``: captions are burned, so their band is taken (in 9:16 it lies inside the lower critical band)."""
    found: list[Finding] = []
    checked = box.critical or box.is_caption

    def add(check: str, remedy: str | None, **extra: Any) -> None:
        found.append(Finding(check=check, scene_index=scene_index, slot=box.key, role=box.role, frame=frame,
                             box=box.rect(), remedy=remedy, text=box.text, **extra))

    if box.overflow > OVERFLOW_TOLERANCE_PX:
        if not checked:
            add("text_overflow", None, overflow=round(box.overflow, 1), status="warn")
        else:
            floor = box.min_size if box.min_size is not None else MIN_TEXT_PX.get(fmt, MIN_TEXT_PX["landscape"])
            at_floor = box.size is not None and box.size <= floor + 0.01
            add("text_overflow", None if at_floor else "shrink", overflow=round(box.overflow, 1))
    if checked:
        bands = CAPTION_BANDS if box.is_caption else CRITICAL_BANDS
        outside = not inside_bands(box, bands.get(fmt, bands["landscape"]))
        covered = captions and not box.is_caption and overlaps_bands(
            box, CAPTION_BANDS.get(fmt, CAPTION_BANDS["landscape"]))
        if outside or covered:
            add("safe_zones", None if safe else "safe_variant")
    if below_type_floor(box, fmt):
        add("type_floor", None, status="warn")
    measured = False
    if checked:
        ratio = text_contrast(box.ink, box.bg)
        measured = ratio is not None
        required = contrast_required(box)
        if ratio is not None and ratio + 1e-9 < required:
            add("contrast", "scrim", ratio=round(ratio, 2), required=required)
    return found, measured


def merge_findings(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per (check, scene, slot), in first-seen order."""
    out: dict[tuple[str, int | None, str], Finding] = {}
    for f in findings:
        if f.ident in out:
            out[f.ident].merge(f)
        else:
            out[f.ident] = copy.copy(f)
    return list(out.values())


def evaluate_boxes(boxes: Iterable[Any], *, fmt: str, frame: int, info: StageInfo | None = None,
                   safe_scenes: Collection[int] = frozenset(),
                   captions: bool = False) -> tuple[list[Finding], Counter[str]]:
    """Findings and counts (``boxes``, ``malformed``, ``contrast_measured``) of the raw boxes seen at ``frame``.
    ``safe_scenes``: the indices of the scenes already on their safe layout (:func:`safe_scenes`)."""
    stats: Counter[str] = Counter()
    found: list[Finding] = []
    frame_scene = info.scene_at(frame) if info is not None else None
    for raw in boxes:
        box = raw if isinstance(raw, TextBox) else TextBox.parse(raw)
        if box is None:
            stats["malformed"] += 1
            continue
        stats["boxes"] += 1
        scene_index = box.scene if box.scene is not None else (frame_scene.index if frame_scene else None)
        items, measured = evaluate_box(box, fmt=fmt, frame=frame, scene_index=scene_index,
                                       safe=scene_index is not None and scene_index in safe_scenes,
                                       captions=captions)
        stats["contrast_measured"] += int(measured)
        found += items
    return found, stats


# ---------------------------------------------------------------------------
# Remedies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Remedy:
    """An automatic fix on one scene's ``look`` (:func:`apply_remedies`): ``shrink`` (``fixes.slots[slot].size``),
    ``safe_variant`` (``fixes.safe``) or ``scrim`` (``fixes.slots[slot].scrim``)."""

    kind: str
    scene_index: int
    slot: str = ""
    size: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "scene_index": self.scene_index}
        if self.slot:
            out["slot"] = self.slot
        if self.size is not None:
            out["size"] = self.size
        return out


def plan_remedies(findings: Iterable[Finding], *, fmt: str, min_sizes: dict[tuple[int, str], float] | None = None,
                  has_look: Callable[[int], bool] | None = None) -> list[Remedy]:
    """The remedies for the open findings that have one (one per scene and slot; one safe variant per scene).
    ``min_sizes``: the smallest size each (scene, slot) reported (a size that is not positive is ignored: the format's
    floor is used, as for a slot without one, so ``fixes.slots[slot].size`` is always a valid size); ``has_look``:
    scenes without a look get none."""
    out: dict[tuple[str, int, str], Remedy] = {}
    floor = float(MIN_TEXT_PX.get(fmt, MIN_TEXT_PX["landscape"]))
    for f in findings:
        if f.status != "open" or f.remedy is None or f.scene_index is None:
            continue
        if has_look is not None and not has_look(f.scene_index):
            continue
        if f.remedy == "safe_variant":
            out.setdefault(("safe_variant", f.scene_index, ""), Remedy("safe_variant", f.scene_index))
        elif f.remedy == "shrink":
            size = (min_sizes or {}).get((f.scene_index, f.slot))
            size = size if size is not None and size > 0 else floor
            out.setdefault(("shrink", f.scene_index, f.slot), Remedy("shrink", f.scene_index, f.slot, size))
        elif f.remedy == "scrim":
            out.setdefault(("scrim", f.scene_index, f.slot), Remedy("scrim", f.scene_index, f.slot))
    return list(out.values())


def look_is_safe(look: Any) -> bool:
    """True when a served ``look`` dict is already on the template's safe layout: ``fixes.safe`` (the fixes channel)
    or B1's legacy ``variant == "safe"``."""
    if not isinstance(look, Mapping):
        return False
    fixes = look.get("fixes")
    return (isinstance(fixes, Mapping) and fixes.get("safe") is True) or look.get("variant") == SAFE_VARIANT


def safe_scenes(timeline: Mapping[str, Any]) -> frozenset[int]:
    """The indices of the scenes of a served timeline dict whose look is on its safe layout (:func:`look_is_safe`)."""
    scenes = timeline.get("scenes") if isinstance(timeline, Mapping) else None
    if not isinstance(scenes, list):
        return frozenset()
    return frozenset(n for n, scene in enumerate(scenes)
                     if isinstance(scene, Mapping) and look_is_safe(scene.get("look")))


def _entry(table: dict[str, Any], key: str) -> dict[str, Any]:
    """``table[key]`` as a dict (a missing or malformed entry is replaced by an empty one)."""
    value = table.get(key)
    if not isinstance(value, dict):
        value = table[key] = {}
    return value


def _fixes(look: dict[str, Any]) -> dict[str, Any]:
    """The look's ``fixes`` in its canonical shape ``{safe, slots}`` (``schemas.timeline.LookFixes``): created when
    absent, a malformed part replaced, what is there kept."""
    fixes = look.get("fixes")
    if not isinstance(fixes, dict):
        fixes = look["fixes"] = {}
    if not isinstance(fixes.get("safe"), bool):
        fixes["safe"] = False
    if not isinstance(fixes.get("slots"), dict):
        fixes["slots"] = {}
    return fixes


def apply_remedies(timeline: dict[str, Any], remedies: Iterable[Remedy]) -> dict[str, Any]:
    """A copy of the (served) timeline dict with the remedies written into each scene's ``look``. Scenes without a
    look are left alone.

    Each remedy is written to the fixes channel only (see the module docstring): ``look.fixes = {safe, slots: {slot:
    {size?, scrim?}}}`` with ``safe = true``, ``slots[slot].size`` or ``slots[slot].scrim = true``. ``look.variant``
    and ``look.text_fit`` are never touched (a remedied scene's variant stays what direct.py chose). Other keys already
    in ``fixes`` (an earlier fix) are kept."""
    out = copy.deepcopy(timeline)
    scenes = out.get("scenes") if isinstance(out, dict) else None
    if not isinstance(scenes, list):
        return out
    for r in remedies:
        if not 0 <= r.scene_index < len(scenes) or not isinstance(scenes[r.scene_index], dict):
            continue
        look = scenes[r.scene_index].get("look")
        if not isinstance(look, dict):
            continue
        if r.kind == "safe_variant":
            _fixes(look)["safe"] = True
            continue
        if r.kind == "shrink" and r.size is not None:
            values: dict[str, Any] = {"size": r.size}
        elif r.kind == "scrim":
            values = {"scrim": True}
        else:
            continue
        _entry(_fixes(look)["slots"], r.slot).update(values)
    return out


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def sample_frames(info: StageInfo, timeline: Timeline | None = None, *, spread: int = SPREAD_FRAMES) -> list[int]:
    """The frames preflight seeks (sorted, unique): ``spread`` spread frames, every scene's first and last frame, every
    beat's settled frame and, for a portrait timeline, the first frame of every shown caption chunk (of every phrase
    caption when the timeline has no caption chunks)."""
    n = info.frames
    frames: set[int] = {min(n - 1, (2 * k + 1) * n // (2 * spread)) for k in range(spread)} if n > 0 else set()
    for sc in info.scenes:
        frames.update((sc.f0, sc.f1 - 1))
    if timeline is None:
        return sorted(f for f in frames if 0 <= f < n)
    by_index = {sc.index: sc for sc in info.scenes}

    def at(sc: StageScene, seconds: float) -> int:
        return min(max(sc.f0 + math.ceil(seconds * info.fps - 1e-6), sc.f0), sc.f1 - 1)

    for scene in timeline.scenes:
        sc = by_index.get(scene.index)
        if sc is not None:
            frames.update(at(sc, beat.start + BEAT_SETTLE_S) for beat in scene.beats)
    portrait = timeline.presentation is not None and timeline.presentation.format == "portrait"
    if portrait:
        chunked = False
        for scene in timeline.scenes:
            sc = by_index.get(scene.index)
            if sc is None:
                continue
            for chunk in scene.kinetic_captions:
                chunked = True
                if not chunk.blank:
                    frames.add(at(sc, chunk.start))
        if not chunked:  # B1: phrase captions (Timeline.captions, absolute times) until kinetic.py (B2)
            frames.update(min(n - 1, math.ceil(c.start * info.fps - 1e-6)) for c in timeline.captions)
    return sorted(f for f in frames if 0 <= f < n)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def _message(f: Finding) -> str:
    template = _MESSAGES.get((f.check, f.status), "{slot}: " + f.check.replace("_", " "))
    ratio = f"{f.ratio:.1f}" if f.ratio is not None else "?"
    required = f"{f.required:.1f}" if f.required is not None else "?"
    return template.format(slot=f.slot, ratio=ratio, required=required)


@dataclass
class PreflightResult:
    """What preflight found and did (see the module docstring)."""

    format: str
    frames: int
    fps: int
    sampled: list[int] = field(default_factory=list)
    rounds: int = 1
    seconds: float = 0.0
    webgl: bool | None = None
    findings: list[Finding] = field(default_factory=list)
    remedies: list[Remedy] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    scenes: dict[int, tuple[str, str]] = field(default_factory=dict)  # index -> (scene_id, title)
    timeline: dict[str, Any] | None = None  # the served timeline with the remedies applied (capture uses it)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "block"]

    @property
    def ok(self) -> bool:
        """No blocking finding."""
        return not self.blocking

    def items(self) -> list[dict[str, Any]]:
        """The findings as teacher-facing items (scene order; blocks first within a scene)."""
        rank = {"block": 0, "warn": 1, "fixed": 2}
        ordered = sorted(self.findings, key=lambda f: (f.scene_index if f.scene_index is not None else 10**9,
                                                      rank.get(f.status, 3), CHECKS.index(f.check)
                                                      if f.check in CHECKS else 9))
        out = []
        for f in ordered:
            sid, title = self.scenes.get(f.scene_index, (None, "")) if f.scene_index is not None else (None, "")
            item: dict[str, Any] = {
                "check": f.check, "status": f.status, "blocking": f.status == "block",
                "scene_index": f.scene_index, "scene_id": sid, "title": title, "slot": f.slot, "role": f.role,
                "frame": f.frame, "t": round(f.frame / max(1, self.fps), 3), "frames": f.frames, "box": f.box,
                "remedy": f.remedy if f.status == "fixed" else None,
                "action": ACTION_SHORTEN_TEXT if f.status == "block" else None,
                "message": _message(f),
            }
            if f.ratio is not None:
                item["ratio"], item["required"] = f.ratio, f.required
            if f.overflow is not None:
                item["overflow"] = f.overflow
            if f.text:
                item["text"] = f.text
            out.append(item)
        return out

    def check_status(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for check in CHECKS:
            mine = [f for f in self.findings if f.check == check]
            statuses = {f.status for f in mine}
            status = next((s for s in ("block", "warn", "fixed") if s in statuses), "pass")
            out[check] = {"status": status, "count": len(mine)}
        return out

    def summary(self) -> dict[str, Any]:
        """The preflight schema (``PREFLIGHT_SCHEMA``), JSON-serialisable."""
        items = self.items()
        return {
            "schema": PREFLIGHT_SCHEMA,
            "ok": self.ok,
            "format": self.format,
            "frames": self.frames,
            "fps": self.fps,
            "sampled": len(self.sampled),
            "rounds": self.rounds,
            "seconds": round(self.seconds, 2),
            "webgl": self.webgl,
            "checks": self.check_status(),
            "items": items,
            "remedies": [r.as_dict() for r in self.remedies],
            "stats": dict(self.stats),
            "problems": [i["message"] + f" ({i['action']})" for i in items if i["blocking"]],
            "warnings": [i["message"] for i in items if i["status"] == "warn"],
        }

    def as_qa(self) -> dict[str, Any]:
        """``Render.options["qa"]`` when the render stops at preflight (no MP4 was made)."""
        summary = self.summary()
        return {"ok": self.ok, "engine": "motion", "stage": "preflight", "schema": PREFLIGHT_SCHEMA,
                "preflight": summary, "problems": list(summary["problems"]), "warnings": list(summary["warnings"])}


#: What the teacher does about a block on a synthesised Aadhi appearance: its text is not a scene's (the intro sets the
#: lecture's session title, else its unit or subject name; ``presenter.intro_title``), so "Shorten text" would send
#: them looking for a scene that is not in the editor.
ACTION_SHORTEN_INTRO = "Shorten the lecture's session title (or the unit name Aadhi's intro shows)"


def where_of(scene_ids: Sequence[str | None], numbers: Mapping[str, int]) -> list[str]:
    """How the teacher finds each scene of ``scene_ids`` (in order, each once): "scene N" by the editor's numbering
    (``numbers``: screenplay id -> its 1-based position, hidden scenes counted, as ``api.routers.renders.scene_numbers``
    and the render job's ``numbers`` give it), or "Aadhi's intro" / "Aadhi's recap" for an appearance the timeline
    builder synthesised (``presenter.appearance_of``: no screenplay scene, so no number). Never the timeline position:
    every Dynamic timeline opens with the synthesised intro (and a 16:9 one may hold the recap), so that is one or two
    past the editor's number (step-5 review R5-3). An id in neither is named by itself (never in a built timeline)."""
    out: list[str] = []
    for sid in scene_ids:
        if not sid:
            continue
        kind = _synthesised(sid, numbers)
        label = (APPEARANCE_NAMES[kind] if kind is not None else f"scene {numbers[sid]}" if sid in numbers
                 else f"scene {sid}")
        if label not in out:
            out.append(label)
    return out


def _synthesised(scene_id: str, numbers: Mapping[str, int]) -> str | None:
    """``intro`` / ``recap`` for a synthesised Aadhi appearance (no screenplay scene has its id), else None."""
    return None if scene_id in numbers else appearance_of(scene_id, numbers)


def playing_numbers(result: PreflightResult) -> dict[str, int]:
    """The editor's numbers as far as the timeline alone tells them, for a caller without the screenplay's: each scene
    by its position among the scenes that play, the synthesised appearances not counted (a hidden scene, which the
    editor counts and the timeline leaves out, is the one difference)."""
    out: dict[str, int] = {}
    for index in sorted(result.scenes):
        sid = result.scenes[index][0]
        if sid and appearance_of(sid, ()) is None:
            out[sid] = len(out) + 1
    return out


class PreflightBlocked(FatalJobError):
    """The render cannot start: a preflight check failed and its remedy did not fix it (teacher action needed).
    ``numbers``: the editor's scene numbers (:func:`where_of`; without them, :func:`playing_numbers`), so the message
    names the scene the teacher sees, and a block on Aadhi's synthesised intro asks for a shorter session title."""

    def __init__(self, result: PreflightResult, numbers: Mapping[str, int] | None = None) -> None:
        known = dict(numbers) if numbers is not None else playing_numbers(result)
        blocking = [i for i in result.items() if i["blocking"]]
        names = where_of([i["scene_id"] for i in blocking], known)  # items() is in scene order
        where = f" ({', '.join(names)})" if names else ""
        message = "preflight failed"
        if blocking:
            first = blocking[0]
            intro = bool(first["scene_id"]) and _synthesised(str(first["scene_id"]), known) == "intro"
            action = ACTION_SHORTEN_INTRO if intro else first["action"]
            message = f"The video cannot be rendered yet{where}: {first['message']} {action} and render again."
        super().__init__(message, code="preflight_blocked")
        self.result = result


def raise_if_blocked(result: PreflightResult, numbers: Mapping[str, int] | None = None) -> None:
    """Raise :class:`PreflightBlocked` (a FatalJobError, code ``preflight_blocked``) when anything blocks; ``numbers``
    as there."""
    if not result.ok:
        raise PreflightBlocked(result, numbers)


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _finalise(first: list[Finding], second: list[Finding] | None, planned: set[tuple[int, str, str]]) -> list[Finding]:
    """Statuses after the remedies: a remedied finding seen again is escalated (zones/overflow block, contrast warns),
    one gone is ``fixed``; findings without a remedy are escalated at once; new findings of the second round too.
    One finding per (check, scene, slot): the second round re-scans every frame of a touched scene, so it sees that
    scene's warnings and unremedied findings again; those are the first round's, never a second item."""

    def escalate(f: Finding) -> Finding:
        if f.status == "open":
            f.status = "warn" if f.check == "contrast" else "block"
        return f

    final: list[Finding] = []
    again = {f.ident: f for f in (second or [])}
    for f in first:
        later = again.pop(f.ident, None)  # every first-round finding claims its second-round sighting
        if f.status != "open":
            final.append(f)
            continue
        remedied = f.remedy is not None and f.scene_index is not None and (
            (f.remedy, f.scene_index, "" if f.remedy == "safe_variant" else f.slot) in planned)
        if not remedied or second is None:
            final.append(escalate(f))
        elif later is not None:
            later.remedy = f.remedy
            final.append(escalate(later))
        else:
            f.status = "fixed"
            final.append(f)
    for f in again.values():  # new in the second round (the remedy caused it, or a frame shows it only now)
        f.remedy = None
        final.append(escalate(f))
    return final


async def _scan(page: StagePage, frames: Sequence[int], info: StageInfo, *, fmt: str,
                safe: Collection[int], check_cancelled: Callable[[], None] | None,
                stats: Counter[str], captions: bool = False) -> tuple[list[Finding], dict[tuple[int, str], float]]:
    found: list[Finding] = []
    min_sizes: dict[tuple[int, str], float] = {}
    for frame in frames:
        if check_cancelled:
            check_cancelled()
        await page.seek(frame)
        raw = await page.text_boxes()
        items, counts = evaluate_boxes(raw, fmt=fmt, frame=frame, info=info, safe_scenes=safe, captions=captions)
        stats.update(counts)
        stats["frames"] += 1
        found += items
        frame_scene = info.scene_at(frame)
        for entry in raw:
            box = TextBox.parse(entry)
            scene = box.scene if box is not None and box.scene is not None else (
                frame_scene.index if frame_scene else None)
            if box is not None and box.min_size is not None and scene is not None:
                min_sizes[(scene, box.key)] = box.min_size
    return merge_findings(found), min_sizes


async def run_preflight(
    settings: Settings,
    timeline: Timeline,
    *,
    token: str,
    served_timeline: dict[str, Any] | None = None,
    base_url: str | None = None,
    media_origins: Sequence[str] = (),
    check_cancelled: Callable[[], None] | None = None,
    ready_timeout_s: float = 120.0,
    call_timeout_s: float = 60.0,
    log: Callable[[str], None] | None = None,
    captions: bool | None = None,
) -> PreflightResult:
    """Run the preflight of a Dynamic ``timeline`` (see the module docstring).

    ``served_timeline``: the timeline exactly as the page should receive it (resolved URLs); default
    ``timeline.model_dump(mode="json")``. ``token``: the scoped render token for the page's fragment. ``captions``:
    whether the render burns captions (default: ``presentation.captions_burn``): burned captions take their band.
    The page draws exactly that caption layer (``setLayers`` after every load, as the capture's pages do), so the
    caption boxes checked are the ones the MP4 will show: none when nothing is burned.
    Raises
    ``ValueError`` for a classic timeline and :class:`~aadhi.compose.dynamic.stage_page.StageError` when the page
    breaks its contract; a teacher-fixable problem never raises here (see :func:`raise_if_blocked`).
    """
    if timeline.presentation is None:
        raise ValueError("preflight runs on Dynamic timelines only (this one has no presentation)")
    started = time.monotonic()
    fmt = timeline.presentation.format
    size = timeline_stage(timeline)
    fps = int(timeline.fps)
    # The frame count the engine captures and the page reports (motion.chunks.scene_frame_bounds; web/js/dynamic/core.js
    # stageInfo): every scene keeps at least one frame, so a scene shorter than half a frame at the end of the lecture
    # makes it one more than the rounded duration.
    frames_needed = total_frames(scene_frame_bounds(timeline, fps))
    served = served_timeline if served_timeline is not None else timeline.model_dump(mode="json")
    burned = bool(timeline.presentation.captions_burn) if captions is None else bool(captions)
    has_look = {s.index: s.look is not None for s in timeline.scenes}
    result = PreflightResult(format=fmt, frames=frames_needed, fps=fps,
                             scenes={s.index: (s.scene_id, s.title) for s in timeline.scenes})
    stats: Counter[str] = Counter()
    async with stage_browser(settings, size=size, base_url=base_url, media_origins=media_origins,
                             call_timeout_s=call_timeout_s, check_cancelled=check_cancelled) as browser:
        page = await browser.open(token=token, served_timeline=served, frames=placeholder_frames,
                                  ready_timeout_s=ready_timeout_s)
        # A loaded page draws captions as the presentation says (render mode); the render's own choice wins.
        await page.set_layers(captions=burned, stage=True)
        info = await page.info()
        info.check(size=size, fps=fps, frames=frames_needed)
        result.webgl = page.webgl
        result.sampled = sample_frames(info, timeline)
        first, min_sizes = await _scan(page, result.sampled, info, fmt=fmt, safe=safe_scenes(served),
                                       check_cancelled=check_cancelled, stats=stats, captions=burned)
        remedies = plan_remedies(first, fmt=fmt, min_sizes=min_sizes, has_look=lambda i: has_look.get(i, False))
        second: list[Finding] | None = None
        if remedies:
            if log:
                log(f"Preflight: applying {len(remedies)} automatic fix(es) and checking again")
            served = apply_remedies(served, remedies)
            info = await page.load(served)
            await page.set_layers(captions=burned, stage=True)  # a reload resets the layers to the presentation's
            info.check(size=size, fps=fps, frames=frames_needed)
            touched = {r.scene_index for r in remedies}
            again = [f for f in result.sampled if (sc := info.scene_at(f)) is not None and sc.index in touched]
            seen, _ = await _scan(page, again, info, fmt=fmt, safe=safe_scenes(served),
                                  check_cancelled=check_cancelled, stats=stats, captions=burned)
            second = [f for f in seen if f.scene_index in touched]  # other scenes' boxes (transitions) are final
            result.rounds = 2
        planned = {(r.kind, r.scene_index, r.slot) for r in remedies}
        result.findings = _finalise(first, second, planned)
        result.remedies = remedies
        browser.check_policy()
        if page.guard_trips:
            stats["guard_trips"] += len(page.guard_trips)
    result.timeline = served
    result.stats = dict(stats)
    result.seconds = time.monotonic() - started
    return result
