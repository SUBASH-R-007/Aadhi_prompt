"""Item icons from ``icon_hint``: the offline index, the deterministic match and the SVG normalisation (design §4.12
"Icon resolution is deterministic", §4.3 key_terms / list_build / recap; plan B2 §A step 2 "B2b assets", §1.2
``ResolvedLook.icons``; b2crit_design "Icons and entities in ResolvedLook").

    python -m aadhi.compose.dynamic.icons            # rewrite aadhi/compose/dynamic/icon_index.json from node_modules
    python -m aadhi.compose.dynamic.icons --check    # exit 1 when it is out of date (CI "Generated files"), 2 when it
                                                     # cannot be generated (node_modules missing or not the pins)

What is here:

* **The two adopted sets** (:data:`SETS`, §4.12 table): Lucide (primary, ISC with the MIT notice of its
  Feather-derived icons) and Tabler Icons (coverage gaps, MIT), exact-pinned npm packages (``lucide-static``,
  ``@tabler/icons``; runtime ``dependencies`` in ``package.json``, so ``THIRD_PARTY_NOTICES`` carries their licences).
  Both are 24 x 24 stroke icons (``stroke-width`` 2, round caps and joins: ``zones.LAYOUT.icon``). Phosphor, the
  design's third set, is W2 (§4.12) and not vendored.
* **The offline index** (``icon_index.json`` beside this module, generated here from the installed packages and
  committed): every usable icon's geometry and the keyword map. It is committed rather than built at vendor time so
  the resolver, the API image and CI never need ``node_modules`` or the network at run time, and a library update is a
  reviewed diff (``--check`` in CI). It holds each set's package, version and full licence text (the ISC and MIT terms
  ask that the notice travel with every copy). Nothing is served from ``web/vendor``: a timeline carries the geometry
  of its own icons (plan §1.2), so the page draws from the timeline, never from a library file.
* **Normalisation** (:func:`normalise_svg`, plan §1.2 "normalised to path d, capped, sanitised both sides"; b2crit
  Q5): an icon's SVG becomes a list of path ``d`` strings, one per drawing element, so the stage can stroke-draw them
  (§4.3 key_terms "the icon's strokes draw"). ``circle``, ``ellipse``, ``rect``, ``line``, ``polyline`` and ``polygon``
  are converted to the path the SVG 2 specification defines as their equivalent (§10.2-§10.7: same start point and
  direction, so a dash animation starts where the element's own would); every ``d`` is re-tokenised by a strict
  parser and written back in one canonical spelling (exact decimal values, no exponents, bounded magnitude), so only
  commands, digits, ``.``, ``-`` and spaces remain: the A2 schema's allow-list pattern
  (``timeline.ICON_PATH_RE``) and caps (32 paths of 4,096 characters) hold by construction and are checked again.
  Fail closed: an icon with anything else (another element, a transform, a style, an opacity, a nested group, any
  filled shape, an unexpected root style) is EXCLUDED from the index with its reason, never drawn approximately.
  ``LookIcon`` has one ``filled`` flag per icon, so a stroke icon with a filled dot cannot be expressed, not even a dot
  the round stroke covers on paper: Chromium strokes a circle narrower than the stroke with a hole in its middle, so a
  stroke-only copy of Lucide's palette, tag or scatter dots draws different pixels (measured; the hint then falls
  through to Tabler, whose palette and tag are pure strokes). Tabler's invisible 24 x 24 bounding path
  (``stroke="none" fill="none"``) is dropped. Tabler's ``Brand`` category is excluded (trade marks, never teaching
  icons). The text parser refuses DTDs, entities, processing instructions and CDATA before parsing (no entity
  expansion, no external fetch). Fidelity is measured in real Chromium (``tests/determinism/test_icon_pixels.py``):
  an icon drawn from its stored paths is pixel-identical to the library's own file, apart from anti-aliasing where a
  ``circle``, ``ellipse`` or rounded ``rect`` became arcs.
* **The match** (:func:`match`, :func:`icon_for`): ``icon_hint`` is one English keyword of at most 24 characters, never
  translated (§4.12, §6.7). It is normalised (NFKC, case-folded, ASCII letters and digits, separators to ``-``; anything
  else, e.g. a Tamil word, is no keyword and gets no icon) and tried in these forms, in order: as written, without
  hyphens ("light bulb" -> ``lightbulb``), then each English singular of its last word ("batteries" -> ``battery``,
  "boxes" -> ``box``, "atoms" -> ``atom``, "buses" -> ``bus``) as written and without hyphens. The tiers are tried in
  order, each with every form: (1) a Lucide icon NAMED so (or by a deprecated name Lucide still ships, ``home`` ->
  ``house``, ``pie-chart`` -> ``chart-pie``: an alias, matched by identical drawing elements when the index is built),
  (2) a Tabler icon named so, (3) a Lucide TAG, (4) a Tabler tag. An icon a set names by the hint is the hint's own
  picture, while a tag only marks a related one (Lucide tags ``lungs`` on its stethoscope; Tabler has the lungs), so
  Tabler's names are tried before Lucide's tags: Lucide is still the primary set at every tier, Tabler fills the gaps
  (§4.12 "falling back to Tabler"; this tier order is a B2b reading, for the owner's review). A tag several icons of one
  set carry is decided when the index is built: an icon whose name negates the tag (a word ``off`` or ``slash``, or a
  last word ``x``, that the tag lacks: ``accessible-off``) never stands for it; :data:`PREFERRED` (a short reviewed list
  for common teaching words, each a real candidate of that tag in the library's own metadata) picks the icon, else the
  one whose name contains the tag as a word, then the fewest name words, the tag earliest in the icon's own tag list,
  the shortest name, the first name. :data:`IGNORED_TAGS` drops the few tags whose every icon would mislead (a pun,
  unrelated pictures). Both lists are checked at generation (a stale or shadowed entry fails). No match means no icon
  (§4.12); the LLM never names icon files.
* **Determinism**: the index is byte-deterministic (sorted keys, one entry per line, LF), the match is dictionary
  lookups in a fixed order, and keys the earlier tiers always win are pruned from the later ones (proved equivalent to
  the unpruned index by ``tests/compose/dynamic/test_icons.py``).

What it returns: :func:`icon_for` gives a :class:`aadhi.schemas.timeline.LookIcon` (``{name, set, version, viewBox,
stroke_width, filled, paths}``), re-sanitised from the index on every use (a damaged index raises
:class:`IconIndexError`: an error, never a silent "no icon"); :func:`scene_icons` the ``ResolvedLook.icons`` map of a
scene's board items, capped at :data:`timeline.ICONS_PER_SCENE_MAX`; :func:`sanitise_icon` re-checks any stored icon
the same way. Filling ``look.icons`` is B2e's ``direct.py``; drawing them is the stage's (B2c ``templates/_shared.js``,
which sanitises again before drawing and must stroke with the stored ``stroke_width``, round caps and joins, no fill).

Imported only inside the Dynamic gate; the run-time path reads one JSON file and imports no XML parser.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

from aadhi.schemas.timeline import ICON_PATH_MAX_CHARS, ICON_PATH_RE, ICON_PATHS_MAX, ICONS_PER_SCENE_MAX, LookIcon

INDEX_VERSION: Final = 1  # the shape of icon_index.json (pinned by a test): bump when a key's meaning or shape changes
ROOT: Final = Path(__file__).resolve().parents[3]
INDEX_PATH: Final = Path(__file__).resolve().with_name("icon_index.json")
INDEX_NAME: Final = "aadhi/compose/dynamic/icon_index.json"
NODE_MODULES: Final = ROOT / "node_modules"
GENERATOR: Final = "python -m aadhi.compose.dynamic.icons"

SetId = Literal["lucide", "tabler"]
Via = Literal["name", "tag"]

HINT_MAX_CHARS: Final = 24  # BoardItem.icon_hint (screenplay.py, §6.7): one keyword, <= 24 characters
NAME_MAX_CHARS: Final = 64  # LookIcon.name
SVG_MAX_BYTES: Final = 64 * 1024  # an icon file; the largest adopted one is a few kB
# |number| in icon geometry: no overflow, no Infinity. Generous for a 24-unit box because an arc's radii may be far
# larger than the box (an almost straight arc: Tabler's spade and heart-check use 1541.72 and 8916.99).
COORD_MAX: Final = Decimal(100000)
DECIMALS_MAX: Final = 6  # digits after the point of any number (the sets use at most 3) ...
DIGITS_MAX: Final = 15  # ... and digits in all, so every value, sum and half stays exact in Decimal's 28 digits
_KEY_RE: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_NAME_RE: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NEGATIONS: Final = frozenset({"off", "slash"})  # a name word that turns the picture into its negation (bell-off) ...
NEGATION_LAST: Final = frozenset({"x"})  # ... or does so as the last word (circle-x, file-x; not x-ray)


@dataclass(frozen=True)
class IconSet:
    """One adopted icon set (§4.12). ``svg_dir`` holds one ``<name>.svg`` per icon inside the package; ``licence`` is
    the SPDX id the package declares and ``licence_marks`` the lines its LICENSE file must hold (checked when the index
    is generated, so a relicensed release fails closed)."""

    id: SetId
    package: str
    version: str
    licence: str
    licence_marks: tuple[str, ...]
    svg_dir: str
    metadata: str  # the file with the tags (Lucide: name -> [tag]; Tabler: name -> {category, tags, styles})


SETS: Final[tuple[IconSet, ...]] = (
    IconSet("lucide", "lucide-static", "1.48.0", "ISC",
            ("ISC License", "Copyright (c) 2026 Lucide Icons and Contributors",
             "The MIT License (MIT) (for the icons listed above)", "Copyright (c) 2013-present Cole Bemis"),
            "icons", "tags.json"),
    IconSet("tabler", "@tabler/icons", "3.48.0", "MIT", ("MIT License", "Copyright (c) 2020-2026 Paweł Kuna"),
            "icons/outline", "icons.json"),
)
SET_ORDER: Final[tuple[SetId, ...]] = tuple(s.id for s in SETS)  # Lucide first at every tier (§4.12)
TIERS: Final[tuple[tuple[SetId, Via], ...]] = (("lucide", "name"), ("tabler", "name"), ("lucide", "tag"),
                                               ("tabler", "tag"))
EXCLUDED_CATEGORIES: Final = {"tabler": frozenset({"Brand"})}  # trade marks

# The root of every adopted icon: a 24 x 24 stroke icon (both sets; zones.LAYOUT.icon["stroke_width"] is 2).
STYLE: Final = {"viewBox": (0.0, 0.0, 24.0, 24.0), "stroke_width": 2.0, "filled": False,
                "stroke_linecap": "round", "stroke_linejoin": "round"}


def _style_json() -> dict[str, Any]:
    """:data:`STYLE` as the index stores it."""
    return {**STYLE, "viewBox": list(STYLE["viewBox"])}


_ROOT_ATTRS: Final = {"width": None, "height": None, "viewBox": "0 0 24 24", "fill": "none", "stroke": "currentColor",
                      "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "class": None}

# A tag several icons of one set carry, decided for common teaching words (each a candidate of that tag in the set's own
# metadata: generation fails when a library update drops it). B2b proposal, for the owner's review.
PREFERRED: Final[Mapping[SetId, Mapping[str, str]]] = {
    "lucide": {
        "arrow": "move-right", "biology": "germ", "chat": "message-circle", "chemistry": "flask-conical",
        "electricity": "zap", "energy": "zap", "fire": "flame", "food": "utensils", "health": "heart-pulse",
        "light": "sun", "science": "microscope", "sound": "volume-2", "warning": "triangle-alert", "water": "droplet",
        "wave": "line-squiggle",
    },
    "tabler": {
        "chart": "chart-bar", "ice": "iceberg",
    },
}
# Tags whose every icon would mislead a lesson (a pun, or only icons of something else carry them): never matched,
# so the hint falls through to the next tier or gets no icon. Each must exist in the metadata (checked at generation).
# B2b proposal, for the owner's review.
IGNORED_TAGS: Final[Mapping[SetId, frozenset[str]]] = {
    "lucide": frozenset({"force"}),  # book-up-2 only (a pun)
    "tabler": frozenset({"algorithm", "evolution", "probability", "quiz"}),  # *-code, a ring, *-question pins
}


class IconError(ValueError):
    """An icon cannot be normalised (fail closed: it is excluded from the index, never drawn approximately)."""


class IconIndexError(RuntimeError):
    """The committed index is missing, damaged or not this module's (an error, never a silent "no icon")."""


class IndexBuildError(RuntimeError):
    """The index cannot be generated from what is installed (node_modules missing, another version, licence changed)."""


# --- numbers and path data ------------------------------------------------------------------------------------------

_NUMBER_RE: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_WSP: Final = frozenset(" \t\r\n\f")  # SVG 2 wsp (and form feed, which the path grammar also allows)
_ARITY: Final = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}
_COMMANDS: Final = frozenset("".join(_ARITY) + "".join(_ARITY).lower())
_FLAG_POSITIONS: Final = (3, 4)  # an arc's large-arc and sweep flags: one character, 0 or 1 (SVG 2 path grammar)


def _skip_wsp(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _WSP:
        pos += 1
    return pos


def _number(text: str) -> Decimal:
    """An SVG number as an exact decimal, bounded: finite, |v| <= COORD_MAX, at most DECIMALS_MAX decimals."""
    if not _NUMBER_RE.fullmatch(text):
        raise IconError(f"not a number: {text[:40]!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - the pattern admits only valid literals
        raise IconError(f"not a number: {text[:40]!r}") from exc
    if not value.is_finite() or abs(value) > COORD_MAX:
        raise IconError(f"number out of range: {text[:40]!r}")
    if len(value.as_tuple().digits) > DIGITS_MAX:  # before normalize(), which rounds to the context's 28 digits
        raise IconError(f"number too precise: {text[:40]!r}")
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > DECIMALS_MAX:
        raise IconError(f"number too precise: {text[:40]!r}")
    return value


def fmt(value: Decimal) -> str:
    """The canonical spelling of a number: exact, plain notation, no trailing zeros, no leading zero before the point
    (``0.5`` -> ``.5``, ``-0.5`` -> ``-.5``, ``2.0`` -> ``2``, ``-0`` -> ``0``)."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    if text.startswith("0."):
        text = text[1:]
    elif text.startswith("-0."):
        text = "-" + text[2:]
    return text


def _join(numbers: Sequence[Decimal]) -> str:
    """Numbers after a command letter: a space between two, none before a minus sign (unambiguous both ways)."""
    out = ""
    for i, n in enumerate(numbers):
        s = fmt(n)
        out += s if i == 0 or s.startswith("-") else " " + s
    return out


def parse_path(d: str) -> list[tuple[str, list[Decimal]]]:
    """Path data -> [(command, args)] by the SVG 2 path grammar (strict: an error anywhere rejects the whole path,
    where a browser would draw up to the error). The first command is a moveto; every command has whole groups of its
    arguments (implicit repeats kept); arc flags are single 0/1 characters; numbers as :func:`_number`."""
    if not isinstance(d, str):
        raise IconError("path data is not text")
    if len(d) > 4 * ICON_PATH_MAX_CHARS:
        raise IconError("path data too long")
    n = len(d)
    out: list[tuple[str, list[Decimal]]] = []
    pos = _skip_wsp(d, 0)
    while pos < n:
        cmd = d[pos]
        if cmd not in _COMMANDS:
            raise IconError(f"unexpected {d[pos:pos + 12]!r} in path data")
        if not out and cmd not in "Mm":
            raise IconError("path data must start with a moveto")
        arity = _ARITY[cmd.upper()]
        pos = _skip_wsp(d, pos + 1)
        args: list[Decimal] = []
        while pos < n and d[pos] not in _COMMANDS:
            if arity == 0:
                raise IconError("closepath takes no numbers")
            if cmd in "Aa" and len(args) % 7 in _FLAG_POSITIONS:
                if d[pos] not in "01":
                    raise IconError("an arc flag must be 0 or 1")
                args.append(Decimal(d[pos]))
                pos += 1
            else:
                m = _NUMBER_RE.match(d, pos)
                if not m:
                    raise IconError(f"unexpected {d[pos:pos + 12]!r} in path data")
                args.append(_number(m.group(0)))
                pos = m.end()
            pos = _skip_wsp(d, pos)  # comma-wsp: wsp* (',' wsp*)?, and a comma needs a number after it
            if pos < n and d[pos] == ",":
                pos = _skip_wsp(d, pos + 1)
                if pos >= n or d[pos] in _COMMANDS:
                    raise IconError("a comma not followed by a number")
        if arity and (not args or len(args) % arity):
            raise IconError(f"command {cmd!r} needs groups of {arity} numbers, got {len(args)}")
        out.append((cmd, args))
    if not out:
        raise IconError("empty path data")
    return out


def canonical_path(d: str) -> str:
    """Path data in the one spelling the index and the timeline store (:func:`parse_path`, then each command letter
    followed by its numbers, see :func:`_join`). Meaning-preserving: the same commands, the same exact values."""
    text = "".join(cmd + _join(args) for cmd, args in parse_path(d))
    if len(text) > ICON_PATH_MAX_CHARS:
        raise IconError(f"path data longer than {ICON_PATH_MAX_CHARS} characters")
    if not re.fullmatch(ICON_PATH_RE, text):  # pragma: no cover - holds by construction
        raise IconError("path data outside the schema's allow-list")
    return text


# --- SVG elements -> path data (SVG 2 §10: the equivalent path of each basic shape) -------------------------------

_SHAPE_ATTRS: Final = {
    "path": frozenset({"d"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}),
}
_PAINT_ATTRS: Final = frozenset({"fill", "stroke"})
_PAINTS: Final = frozenset({"none", "currentColor"})
_ZERO: Final = Decimal(0)
_SVG_NS: Final = "{http://www.w3.org/2000/svg}"


def _coord(attrs: Mapping[str, str], key: str, default: Decimal | None = _ZERO) -> Decimal:
    raw = attrs.get(key)
    if raw is None:
        if default is None:
            raise IconError(f"missing {key}")
        return default
    return _number(raw.strip())


def _points(text: str) -> list[Decimal]:
    nums: list[Decimal] = []
    pos = _skip_wsp(text, 0)
    while pos < len(text):
        m = _NUMBER_RE.match(text, pos)
        if not m:
            raise IconError(f"unexpected {text[pos:pos + 12]!r} in points")
        nums.append(_number(m.group(0)))
        pos = _skip_wsp(text, m.end())
        if pos < len(text) and text[pos] == ",":
            pos = _skip_wsp(text, pos + 1)
            if pos >= len(text):
                raise IconError("a comma not followed by a number")
    if len(nums) < 4 or len(nums) % 2:
        raise IconError("points needs at least two whole x,y pairs")
    return nums


def _arc(rx: Decimal, ry: Decimal, x: Decimal, y: Decimal) -> str:
    return "A" + _join([rx, ry, _ZERO, _ZERO, Decimal(1), x, y])


def shape_path(tag: str, attrs: Mapping[str, str]) -> str:
    """The path data equivalent to one basic shape (SVG 2 §10.2 rect, §10.3 circle, §10.4 ellipse, §10.5 line, §10.6
    polyline, §10.7 polygon), absolute coordinates. A shape the specification does not render (a zero or negative radius
    or size) is refused rather than dropped: the sets have none, so one appearing means the file changed."""
    if tag == "path":
        raw = attrs.get("d")
        if raw is None:
            raise IconError("a path without d")
        return canonical_path(raw)
    if tag in ("circle", "ellipse"):
        cx, cy = _coord(attrs, "cx"), _coord(attrs, "cy")
        if tag == "circle":
            rx = ry = _coord(attrs, "r", None)
        else:
            rx, ry = _coord(attrs, "rx", None), _coord(attrs, "ry", None)
        if rx <= 0 or ry <= 0:
            raise IconError(f"a {tag} without a positive radius")
        text = ("M" + _join([cx + rx, cy]) + _arc(rx, ry, cx, cy + ry) + _arc(rx, ry, cx - rx, cy)
                + _arc(rx, ry, cx, cy - ry) + _arc(rx, ry, cx + rx, cy) + "Z")
    elif tag == "rect":
        x, y = _coord(attrs, "x"), _coord(attrs, "y")
        w, h = _coord(attrs, "width", None), _coord(attrs, "height", None)
        if w <= 0 or h <= 0:
            raise IconError("a rect without a positive size")
        rx_raw, ry_raw = attrs.get("rx"), attrs.get("ry")
        rx = _coord(attrs, "rx") if rx_raw is not None else (_coord(attrs, "ry") if ry_raw is not None else _ZERO)
        ry = _coord(attrs, "ry") if ry_raw is not None else rx  # "auto": the other radius (SVG 2 §10.2)
        if rx < 0 or ry < 0:
            raise IconError("a rect with a negative corner radius")
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        rounded = rx > 0 and ry > 0
        text = "M" + _join([x + rx, y]) + "H" + _join([x + w - rx])
        if rounded:
            text += _arc(rx, ry, x + w, y + ry)
        text += "V" + _join([y + h - ry])
        if rounded:
            text += _arc(rx, ry, x + w - rx, y + h)
        text += "H" + _join([x + rx])
        if rounded:
            text += _arc(rx, ry, x, y + h - ry)
        text += "V" + _join([y + ry])
        if rounded:
            text += _arc(rx, ry, x + rx, y)
        text += "Z"
    elif tag == "line":
        text = "M" + _join([_coord(attrs, "x1"), _coord(attrs, "y1")]) + "L" + _join(
            [_coord(attrs, "x2"), _coord(attrs, "y2")])
    elif tag in ("polyline", "polygon"):
        raw = attrs.get("points")
        if raw is None:
            raise IconError(f"a {tag} without points")
        nums = _points(raw)
        text = "M" + _join(nums[:2]) + "L" + _join(nums[2:]) + ("Z" if tag == "polygon" else "")
    else:
        raise IconError(f"unsupported element <{tag[:20]}>")
    return canonical_path(text)


def _visible_paths(elements: Iterable[tuple[str, Mapping[str, str]]]) -> list[str]:
    """The path data of the drawing elements, in document order, under the paint rules of the module docstring."""
    paths: list[str] = []
    for tag, attrs in elements:
        allowed = _SHAPE_ATTRS.get(tag)
        if allowed is None:
            raise IconError(f"unsupported element <{tag[:20]}>")
        extra = sorted(set(attrs) - allowed - _PAINT_ATTRS)
        if extra:
            raise IconError(f"unsupported attribute {extra[0][:30]!r} on <{tag}>")
        fill, stroke = attrs.get("fill", "none"), attrs.get("stroke", "currentColor")
        if fill not in _PAINTS or stroke not in _PAINTS:
            raise IconError(f"unsupported paint on <{tag}>")
        if stroke == "none" and fill == "none":
            continue  # invisible (Tabler's 24 x 24 bounding path)
        if fill != "none":
            # Not even a dot whose diameter is under the stroke width: geometrically the round stroke covers it, but
            # Chromium strokes such a circle with a hole in its middle (the inner offset curve turns inside out), so a
            # stroke-only copy would not draw the same pixels (measured: Lucide's palette, tag and scatter dots).
            raise IconError(f"a filled <{tag}> (one fill per icon cannot express it)")
        paths.append(shape_path(tag, attrs))
    if not paths:
        raise IconError("no drawing element")
    if len(paths) > ICON_PATHS_MAX:
        raise IconError(f"more than {ICON_PATHS_MAX} drawing elements")
    return paths


_FORBIDDEN_MARKUP: Final = re.compile(r"<!(?!--)|<\?(?!xml\s)|<\?xml\s.*?\?>.*<\?", re.DOTALL)


def parse_svg(text: str) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """The root's attributes and the drawing elements of one icon file (fail closed). DTDs, entity declarations,
    CDATA and processing instructions (other than a leading XML declaration) are refused before the XML parser runs,
    so no entity is ever expanded and nothing is fetched; nested elements, text content and foreign namespaces are
    refused after it."""
    from xml.etree import ElementTree as ET  # generation and tests only: the run-time path never parses SVG

    if not isinstance(text, str):
        raise IconError("an icon file is text")
    if len(text.encode("utf-8")) > SVG_MAX_BYTES:
        raise IconError("icon file too large")
    if _FORBIDDEN_MARKUP.search(text):
        raise IconError("DTD, entity, CDATA or processing instruction in an icon file")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs and entities are refused above; the input is a pinned package
    except (ET.ParseError, ValueError) as exc:
        raise IconError(f"not well-formed XML: {exc}") from exc
    if root.tag != f"{_SVG_NS}svg":
        raise IconError("the root is not an SVG element")
    if (root.text or "").strip():
        raise IconError("text inside the svg element")
    elements: list[tuple[str, dict[str, str]]] = []
    for el in root:
        if not isinstance(el.tag, str) or not el.tag.startswith(_SVG_NS):
            raise IconError("an element outside the SVG namespace")
        if len(el) or (el.text or "").strip() or (el.tail or "").strip():
            raise IconError(f"content inside <{el.tag.removeprefix(_SVG_NS)[:20]}>")
        if any(not k.isascii() or k.startswith("{") for k in el.attrib):
            raise IconError("a namespaced attribute")
        elements.append((el.tag.removeprefix(_SVG_NS), dict(el.attrib)))
    if any(k.startswith("{") for k in root.attrib):
        raise IconError("a namespaced attribute on the root")
    return dict(root.attrib), elements


def normalise_svg(text: str, *, set_id: SetId, name: str, version: str) -> LookIcon:
    """One icon file of an adopted set -> the ``ResolvedLook.icons`` entry (fail closed, see the module docstring)."""
    if not _NAME_RE.fullmatch(name) or len(name) > NAME_MAX_CHARS:
        raise IconError(f"icon name {name[:80]!r} is not a plain id")
    attrs, elements = parse_svg(text)
    for key, value in attrs.items():
        if key not in _ROOT_ATTRS:
            raise IconError(f"unsupported root attribute {key[:30]!r}")
        expected = _ROOT_ATTRS[key]
        if expected is not None and value.strip() != expected:
            raise IconError(f"root {key}={value[:40]!r}, expected {expected!r}")
    for key in ("viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"):
        if key not in attrs:
            raise IconError(f"root {key} missing")
    paths = _visible_paths(elements)
    return LookIcon(name=name, set=set_id, version=version, viewBox=STYLE["viewBox"],  # type: ignore[arg-type]
                    stroke_width=STYLE["stroke_width"], filled=False, paths=paths)  # type: ignore[arg-type]


def sanitise_icon(value: object) -> LookIcon | None:
    """Any icon value (a stored timeline's, the index's) -> a :class:`LookIcon` whose every path passed the strict
    parser and is in canonical form, or None. The schema's pattern and caps first (``LookIcon``), then the parser:
    a value the pattern admits but the grammar does not (``M1e999 0``, ``M 1 2 3``, ``MZ``) is refused here."""
    try:
        icon = value if isinstance(value, LookIcon) else LookIcon.model_validate(value)
        paths = [canonical_path(p) for p in icon.paths]
    except (ValueError, TypeError):  # pydantic's ValidationError and IconError are ValueErrors
        return None
    if not _NAME_RE.fullmatch(icon.name) or not all(math.isfinite(v) for v in (*icon.viewBox, icon.stroke_width)):
        return None
    return LookIcon.model_validate({**icon.model_dump(), "paths": paths})


# --- keywords --------------------------------------------------------------------------------------------------------


def normalise_key(text: object) -> str | None:
    """A hint or tag -> its key (``Light Bulb`` -> ``light-bulb``), or None when it is no ASCII keyword of at most
    :data:`HINT_MAX_CHARS` characters (before and after normalisation)."""
    if not isinstance(text, str | int) or isinstance(text, bool):
        return None
    raw = str(text)
    if len(raw) > HINT_MAX_CHARS:
        return None
    s = unicodedata.normalize("NFKC", raw).casefold()
    if not s.isascii():
        return None
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s or len(s) > HINT_MAX_CHARS or not _KEY_RE.fullmatch(s):
        return None
    return s


def _singulars(key: str) -> list[str]:
    """English singulars of the key's last word, most specific rule first; stems shorter than 3 letters are never
    produced (``gas`` stays ``gas``)."""
    words = key.split("-")
    last, head = words[-1], words[:-1]
    out: list[str] = []

    def stem(s: str) -> None:
        if len(s) >= 3 and s.isalpha():
            out.append("-".join([*head, s]))

    if last.endswith("ies"):
        stem(last[:-3] + "y")  # batteries -> battery
    if last.endswith("ves"):
        stem(last[:-3] + "f")  # leaves -> leaf
        stem(last[:-3] + "fe")  # knives -> knife
    if re.search(r"(?:ch|sh|ss|x|z)es$", last):
        stem(last[:-2])  # boxes -> box, glasses -> glass
    if last.endswith("s") and not re.search(r"(?:ss|us|is)$", last):
        stem(last[:-1])  # atoms -> atom, cases -> case
    if last.endswith("ses") and not last.endswith("sses"):
        stem(last[:-2])  # buses -> bus, gases -> gas (after "case": cases -> case first)
    return out


def hint_forms(key: str) -> tuple[str, ...]:
    """The forms a key is tried in: as written, without hyphens, then each singular as written and without hyphens."""
    forms: list[str] = []
    for f in (key, key.replace("-", "")):
        if f not in forms:
            forms.append(f)
    for s in _singulars(key):
        for f in (s, s.replace("-", "")):
            if f not in forms:
                forms.append(f)
    return tuple(forms)


# --- the index -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IconMatch:
    """Which icon a hint resolved to and how (for tests and the gallery's badges)."""

    set: SetId
    name: str
    via: Via
    form: str


@dataclass(frozen=True)
class _SetIndex:
    version: str
    icons: Mapping[str, tuple[str, ...]]  # name -> path data
    aliases: Mapping[str, str]  # a deprecated name the package still ships -> the icon's name
    tags: Mapping[str, str]  # tag key -> the icon's name


def _load(path: Path) -> dict[SetId, _SetIndex]:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise IconIndexError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION or data.get("order") != list(SET_ORDER):
        raise IconIndexError(f"{path.name} is not an index of version {INDEX_VERSION} (run {GENERATOR})")
    if data.get("style") != _style_json():
        raise IconIndexError(f"{path.name}: the icon style differs from this module's (run {GENERATOR})")
    sets = data.get("sets")
    out: dict[SetId, _SetIndex] = {}
    for s in SETS:
        entry = sets.get(s.id) if isinstance(sets, dict) else None
        if not isinstance(entry, dict) or entry.get("package") != s.package or entry.get("version") != s.version:
            raise IconIndexError(f"{path.name} does not hold {s.package} {s.version} (run {GENERATOR})")
        icons, aliases, tags = entry.get("icons"), entry.get("aliases"), entry.get("tags")
        if not (isinstance(icons, dict) and all(isinstance(v, list) and all(isinstance(p, str) for p in v)
                                                for v in icons.values())
                and all(isinstance(m, dict) and all(isinstance(v, str) for v in m.values()) for m in (aliases, tags))):
            raise IconIndexError(f"{path.name}: {s.id}'s icons, aliases or tags are malformed")
        out[s.id] = _SetIndex(s.version, MappingProxyType({k: tuple(v) for k, v in icons.items()}),
                              MappingProxyType(dict(aliases)), MappingProxyType(dict(tags)))  # type: ignore[arg-type]
    return out


@functools.lru_cache(maxsize=4)
def load_index(path: Path = INDEX_PATH) -> dict[SetId, _SetIndex]:
    """The committed index (read once per process)."""
    return _load(path)


def match(hint: object, *, path: Path = INDEX_PATH) -> IconMatch | None:
    """The icon an ``icon_hint`` names (module docstring: forms within tiers), or None."""
    if not isinstance(hint, str):  # a hint is text (a number is a key only as a Tabler tag)
        return None
    key = normalise_key(hint)
    if key is None:
        return None
    index = load_index(path)
    forms = hint_forms(key)
    for set_id, via in TIERS:
        s = index[set_id]
        for form in forms:
            if via == "name":
                name: str | None = form if form in s.icons else s.aliases.get(form)
            else:
                name = s.tags.get(form)
            if name is not None:
                return IconMatch(set_id, name, via, form)
    return None


def icon_for(hint: object, *, path: Path = INDEX_PATH) -> LookIcon | None:
    """The ``ResolvedLook.icons`` entry for an ``icon_hint``, or None when nothing matches (§4.12)."""
    m = match(hint, path=path)
    if m is None:
        return None
    s = load_index(path)[m.set]
    paths = s.icons.get(m.name)
    if paths is None:
        raise IconIndexError(f"{INDEX_NAME}: {m.set} tag maps to the missing icon {m.name!r}")
    icon = sanitise_icon({"name": m.name, "set": m.set, "version": s.version, "viewBox": list(STYLE["viewBox"]),
                          "stroke_width": STYLE["stroke_width"], "filled": False, "paths": list(paths)})
    if icon is None:
        raise IconIndexError(f"{INDEX_NAME}: {m.set} icon {m.name!r} fails sanitisation")
    return icon


def scene_icons(items: Iterable[Any], *, path: Path = INDEX_PATH) -> dict[str, LookIcon]:
    """``ResolvedLook.icons`` of one scene: board item id -> icon, for the items (objects with ``id`` and
    ``icon_hint``, e.g. ``BoardItem``) whose hint matches, in item order, at most
    :data:`timeline.ICONS_PER_SCENE_MAX` (the first ones; an item id seen twice keeps its first icon)."""
    out: dict[str, LookIcon] = {}
    for item in items:
        if len(out) >= ICONS_PER_SCENE_MAX:
            break
        item_id, hint = getattr(item, "id", None), getattr(item, "icon_hint", None)
        if not isinstance(item_id, str) or item_id in out or hint is None:
            continue
        icon = icon_for(hint, path=path)
        if icon is not None:
            out[item_id] = icon
    return out


# --- generation (needs node_modules) ---------------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IndexBuildError(f"cannot read {path}: {exc}") from exc


def _clean(text: str) -> str:
    """A licence text as THIRD_PARTY_NOTICES spells it: LF line ends, trailing spaces trimmed, outer blank lines
    dropped (scripts/gen_third_party_notices.py ``_clean``)."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def negates(name: str, key: str) -> bool:
    """True when the icon ``name`` shows the negation of what ``key`` asks for: a negating word the key lacks."""
    words, key_words = name.split("-"), set(key.split("-"))
    negating = (set(words) & NEGATIONS) | ({words[-1]} & NEGATION_LAST)
    return bool(negating - key_words)


def _tag_rank(tag: str, name: str, position: int) -> tuple[Any, ...]:
    words = name.split("-")
    return (tag not in words and tag != name, len(words), position, len(name), name)


def _metadata(s: IconSet, folder: Path) -> tuple[dict[str, list[object]], dict[str, str]]:
    """name -> raw tags, and the names excluded by category (Tabler's brands)."""
    raw = json.loads(_read_text(folder / s.metadata))
    tags: dict[str, list[object]] = {}
    excluded: dict[str, str] = {}
    if s.id == "lucide":
        for name, t in raw.items():
            tags[name] = list(t)
        return tags, excluded
    for name, meta in raw.items():
        if "outline" not in (meta.get("styles") or {}):
            continue
        if meta.get("category") in EXCLUDED_CATEGORIES.get(s.id, ()):
            excluded[name] = f"category {meta.get('category')} (trade marks)"
            continue
        tags[name] = list(meta.get("tags") or [])
    return tags, excluded


def _build_set(s: IconSet, node_modules: Path) -> dict[str, Any]:
    folder = node_modules / s.package
    manifest = folder / "package.json"
    if not manifest.is_file():
        raise IndexBuildError(f"{s.package} is not installed ({folder}): run npm ci")
    meta = json.loads(_read_text(manifest))
    if meta.get("version") != s.version:
        raise IndexBuildError(f"{s.package} {meta.get('version')} is installed, {GENERATOR} pins {s.version}")
    if meta.get("license") != s.licence:
        raise IndexBuildError(f"{s.package} declares licence {meta.get('license')!r}, expected {s.licence!r}")
    licence_text = _clean(_read_text(folder / "LICENSE"))
    for mark in s.licence_marks:
        if mark not in licence_text.split("\n"):
            raise IndexBuildError(f"{s.package}/LICENSE lacks the line {mark!r}: read the new licence first")
    tags_raw, excluded = _metadata(s, folder)
    svg_dir = folder / s.svg_dir
    files = sorted(p.stem for p in svg_dir.glob("*.svg"))
    if not files:
        raise IndexBuildError(f"{svg_dir}: no icons")
    # Every metadata row has its file. A file without a row is a deprecated name the package still ships (Lucide keeps
    # e.g. home.svg for house): it becomes an alias of the one listed icon with the very same drawing elements; a file
    # that matches none or several, or whose name is no plain id, is an error (a release breaking this is read first).
    missing = sorted((set(tags_raw) | set(excluded)) - set(files))
    if missing:
        raise IndexBuildError(f"{s.package}: {s.metadata} lists icons without a file ({missing[:5]})")
    icons: dict[str, list[str]] = {}
    by_source: dict[tuple[Any, ...], list[str]] = {}  # the drawing elements as the file writes them -> listed names
    unlisted: dict[str, tuple[Any, ...]] = {}
    for name in files:
        if name in excluded:
            continue
        text = _read_text(svg_dir / f"{name}.svg")
        try:
            source = tuple((tag, tuple(sorted(attrs.items()))) for tag, attrs in parse_svg(text)[1])
        except IconError as exc:
            raise IndexBuildError(f"{s.package}: {name}.svg cannot be read: {exc}") from exc
        if name not in tags_raw:
            if not _NAME_RE.fullmatch(name) or len(name) > NAME_MAX_CHARS:
                raise IndexBuildError(f"{s.package}: unlisted file {name[:80]!r} is not a plain id")
            unlisted[name] = source
            continue
        by_source.setdefault(source, []).append(name)
        try:
            icons[name] = list(normalise_svg(text, set_id=s.id, name=name, version=s.version).paths)
        except IconError as exc:
            excluded[name] = str(exc)
    aliases: dict[str, str] = {}
    for name, source in unlisted.items():
        same = by_source.get(source, [])
        if len(same) != 1:
            raise IndexBuildError(
                f"{s.package}: unlisted file {name}.svg matches {len(same)} listed icons ({same[:3]})")
        if same[0] in icons:
            aliases[name] = same[0]
        else:
            excluded[name] = f"alias of {same[0]}: {excluded[same[0]]}"
    candidates: dict[str, list[tuple[tuple[Any, ...], str]]] = {}
    for name in icons:
        seen: set[str] = set()
        for position, tag in enumerate(tags_raw.get(name, [])):
            key = normalise_key(tag)
            if key is None or key in seen or negates(name, key):  # a crossed-out picture never stands for its tag
                continue
            seen.add(key)
            candidates.setdefault(key, []).append((_tag_rank(key, name, position), name))
    for key in IGNORED_TAGS.get(s.id, frozenset()):
        if candidates.pop(key, None) is None:
            raise IndexBuildError(f"IGNORED_TAGS {s.id} {key!r} is not a tag any more")
    tags: dict[str, str] = {}
    for key, cands in candidates.items():
        tags[key] = min(cands)[1]
    for key, name in PREFERRED.get(s.id, {}).items():
        if name not in {n for _, n in candidates.get(key, [])}:
            raise IndexBuildError(f"PREFERRED {s.id} {key!r} -> {name!r} is not a candidate of that tag any more")
        tags[key] = name
    return {"package": s.package, "version": s.version, "licence": s.licence, "licence_text": licence_text,
            "icons": icons, "aliases": aliases, "tags": tags, "excluded": excluded}


def build_index(node_modules: Path = NODE_MODULES, *, prune: bool = True) -> dict[str, Any]:
    """The index as plain data. ``prune`` drops what the tier order makes unreachable (module docstring): a Lucide tag
    that is a name of either set, a Tabler name that is a Lucide name, a Tabler tag that is a name of either set or a
    Lucide tag, and then a Tabler icon no remaining key reaches."""
    sets = {s.id: _build_set(s, node_modules) for s in SETS}
    lucide, tabler = sets["lucide"], sets["tabler"]
    if prune:
        lucide_names = set(lucide["icons"]) | set(lucide["aliases"])
        names = lucide_names | set(tabler["icons"]) | set(tabler["aliases"])
        lucide["tags"] = {k: v for k, v in lucide["tags"].items() if k not in names}
        tabler["tags"] = {k: v for k, v in tabler["tags"].items() if k not in names and k not in lucide["tags"]}
        tabler["aliases"] = {k: v for k, v in tabler["aliases"].items() if k not in lucide_names}
        reached = ({n for n in tabler["icons"] if n not in lucide_names} | set(tabler["tags"].values())
                   | set(tabler["aliases"].values()))
        tabler["icons"] = {n: p for n, p in tabler["icons"].items() if n in reached}
        # The reviewed lists must stay meaningful: a preference or an ignored tag that an earlier tier always shadows
        # is dead.
        for set_id, data in sets.items():
            earlier = names | (set(lucide["tags"]) if set_id == "tabler" else set())
            dead = sorted(k for k in PREFERRED.get(set_id, {}) if k not in data["tags"])
            dead += sorted(k for k in IGNORED_TAGS.get(set_id, frozenset()) if k in earlier)
            if dead:
                raise IndexBuildError(f"PREFERRED / IGNORED_TAGS {set_id} {dead}: an earlier tier always wins")
    out: dict[str, Any] = {"version": INDEX_VERSION, "generator": GENERATOR, "order": list(SET_ORDER),
                           "style": _style_json(), "sets": {}}
    for s in SETS:
        data = sets[s.id]
        out["sets"][s.id] = {k: data[k] for k in ("package", "version", "licence", "licence_text")}
        out["sets"][s.id].update({k: dict(sorted(data[k].items())) for k in _MAPS})
    return out


_MAPS: Final = ("icons", "aliases", "tags", "excluded")  # a set's maps, in the index's order


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def render_index(node_modules: Path = NODE_MODULES, *, data: Mapping[str, Any] | None = None) -> str:
    """The text ``icon_index.json`` must hold: keys in a fixed order, one icon / tag / exclusion per line, LF, UTF-8
    (``data``: an index :func:`build_index` made, e.g. unpruned for a test)."""
    if data is None:
        data = build_index(node_modules)
    lines = ["{",f'"version": {data["version"]},', f'"generator": {_dump(data["generator"])},',
             f'"order": {_dump(data["order"])},', f'"style": {_dump(data["style"])},', '"sets": {']
    for i, s in enumerate(SETS):
        entry = data["sets"][s.id]
        lines.append(f"{_dump(s.id)}: {{")
        for key in ("package", "version", "licence", "licence_text"):
            lines.append(f"{_dump(key)}: {_dump(entry[key])},")
        for j, key in enumerate(_MAPS):
            items = list(entry[key].items())
            lines.append(f"{_dump(key)}: {{")
            lines += [f"{_dump(k)}: {_dump(v)}{',' if n < len(items) - 1 else ''}" for n, (k, v) in enumerate(items)]
            lines.append("}" + ("," if j < len(_MAPS) - 1 else ""))
        lines.append("}" + ("," if i < len(SETS) - 1 else ""))
    lines += ["}", "}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from .catalogue import first_difference

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if icon_index.json is out of date")
    parser.add_argument("--path", type=Path, default=INDEX_PATH, help=argparse.SUPPRESS)  # tests: check a copy
    parser.add_argument("--node-modules", type=Path, default=NODE_MODULES, help=argparse.SUPPRESS)  # tests
    args = parser.parse_args(argv)
    try:
        text = render_index(args.node_modules)
    except (IndexBuildError, OSError, ValueError) as exc:
        print(f"{INDEX_NAME} cannot be generated: {exc}", file=sys.stderr)
        return 2
    try:
        current: str | None = args.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        current = None
    if args.check:
        line = first_difference(current, text)
        if line is None:
            return 0
        print(f"{INDEX_NAME} is out of date (first difference at line {line}): run {GENERATOR}", file=sys.stderr)
        return 1
    if current != text:
        args.path.write_bytes(text.encode("utf-8"))
        print(f"wrote {INDEX_NAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
