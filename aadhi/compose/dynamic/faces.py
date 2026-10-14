"""The Dynamic stage's type faces: ONE table (design §4.6 typography, §4.12 fonts and licences, §10.3 B2; plan B2 §2
"Fonts", §1.3 ``presentation.fonts``; b2crit_engineering "Fonts: classic invariance, Pillow measurement fidelity,
preload").

    python -m aadhi.compose.dynamic.faces            # rewrite web/css/dynamic-fonts.css and web/js/dynamic/faces.gen.js
    python -m aadhi.compose.dynamic.faces --check    # exit 1 when either is out of date (CI "Generated files")

What is here (data and pure functions; no I/O but the generator's own two files):

* **Families** (:data:`FAMILIES`, §4.6 table): every face the design names, keyed by the catalogue id that
  ``presentation.fonts`` stores (``Presentation.display_font`` is one of them): its CSS family name, role (display,
  body, mono, indic), release, the npm package and exact version it is vendored from, the licence read from that
  package's LICENSE file (``scripts/vendor.mjs`` refuses an OFL face whose LICENSE is not the OFL 1.1 text), the
  weights (static faces) or the ``wght`` axis (variable faces), the subsets and, for an Indic family, its script. The
  W2 display faces are listed with no package: not vendored, never drawn.
* **Faces** (:data:`FACES`): one row per ``@font-face`` rule, in declaration order (CSS Fonts 4 §4.5: among the rules of
  one family and weight whose unicode-ranges overlap, the LAST one declared is tried first, so the order matters to
  anything that resolves a character the way the page does). Two stylesheets declare them:
  - ``css == "classic"``: Inter, Outfit, JetBrains Mono and the Noto Sans Indic faces, vendored since B1 by
    ``scripts/vendor.mjs`` into ``web/vendor/fonts/`` and declared by its generated ``web/vendor/fonts.css`` (which
    ``tokens.css`` imports on every page). They are described here, row for row and range for range, so the Python
    side (text fit, the parity tests) sees exactly what the page sees; ``tests/compose/dynamic/test_faces.py`` pins
    every row against ``web/vendor/fonts.css`` and ``vendor.mjs``'s range table. Neither file changes: the classic
    renderer's ``video._copy_fonts`` copies ``web/vendor/fonts/*`` (files only, not sub-directories) into libass
    burn-ins, so its output stays byte-identical (plan §2 "Fonts", classic isolation).
  - ``css == "dynamic"``: the B2b faces (Poppins, Lexend, Atkinson Hyperlegible Next, Anek per Indic script), vendored
    by ``vendor.mjs`` from :data:`FACES` (through ``web/js/dynamic/faces.gen.js``) into ``web/vendor/fonts/dynamic/
    <family>/`` with the package's LICENSE beside them, and declared by the generated ``web/css/dynamic-fonts.css``,
    which the three pages that host the Dynamic stage link after ``dynamic.css`` (``render-dynamic.html``,
    ``index.html``, ``watch.html``). It declares new family names only (never Inter, Outfit, ...), so nothing a page
    already draws can resolve differently, and a declared face is fetched only when text asks for it.
* **The roles a timeline draws** (:func:`stage_roles`, :func:`stack`): exactly ``web/js/dynamic/fonts.js``. A
  ``presentation.fonts`` without an ``indic`` key is the B1 form (B1 and B2a timelines): the stage draws it as it
  always did (display ``outfit`` or ``inter``, any other id as Outfit; body Inter; mono JetBrains Mono; the Noto Sans
  Indic faces). One WITH ``indic`` is the B2 form (feature-detected, never by engine version, §7.3): each role is the
  family it names when that is a vendored family of the role, else the role's default. :func:`resolver_fonts` is what
  the resolver stores (``compose.presentation.timeline_presentation``): the B2 form naming exactly the faces the B1
  stage draws for the same presentation, so no look changes (B2b); the display-face rotation (§4.6, "excluding the
  last 3 the teacher used") is B2e's ``direct.py``.
* **Font requests** (``fonts.js`` ``stageFontRequests``): the render page loads every face of every family a stack
  holds before ``ready`` (§7.3). In the B2 form they are derived from the roles: each Latin family's
  :attr:`Family.requests` weights, and the Indic family of each script the timeline uses. For the fonts the resolver
  stores this is ``render/fonts.js`` ``fontRequests`` exactly when display is Outfit, and ``fontRequests`` without the
  Outfit faces when display is Inter (no stack holds Outfit then; the pixels are the same), both pinned by
  ``web/tests/dynamic/fonts.test.js``.
* **Character resolution for text fit** (:func:`face_for`, :func:`match_weight`): the CSS font matching a page does per
  family (the weight per CSS Fonts 4 §5.2, then the unicode-range among that weight's faces, last declared first), so
  ``textfit.py`` (B2b) measures every grapheme run with the face Chromium draws it with. A variable face's ``wght``
  axis default is the FILE's (Anek: 500, not Fontsource's CSS default 400), so a measurement must always set it.

Weights vendored for the B2b faces are the ones the stage asks for (``dynamic.css``: 400 body, 600 labels and 9:16
text, 700 titles and emphasis, 900 a bold run in a title): display faces 400/600/700/900, the body option 400/600/700,
Anek's variable ``wght`` 100-800 (one file per script). Only the Latin subsets of a Latin face are vendored (Poppins'
Devanagari subset is not: Indic text is drawn by the lesson's Indic family, §4.6 table), and only the script subset of
an Anek face (its Latin glyphs are never reached: a Latin body face precedes it in every stack).

Imported only inside the Dynamic gate (``compose.presentation`` imports it lazily); no Pillow here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel

FACES_VERSION = 1  # the shape of faces.gen.js (pinned by a test): bump when a key's meaning or shape changes
ROOT = Path(__file__).resolve().parents[3]
CSS_OUTPUT = ROOT / "web" / "css" / "dynamic-fonts.css"
JS_OUTPUT = ROOT / "web" / "js" / "dynamic" / "faces.gen.js"
CSS_NAME = "web/css/dynamic-fonts.css"
JS_NAME = "web/js/dynamic/faces.gen.js"
GENERATOR = "python -m aadhi.compose.dynamic.faces"

Role = Literal["display", "body", "mono", "indic"]
Release = Literal["R1", "W2"]
Script = Literal["tamil", "devanagari", "telugu", "kannada", "malayalam"]
Sheet = Literal["classic", "dynamic"]

ROLES: Final[tuple[Role, ...]] = ("display", "body", "mono", "indic")
# The Indic scripts the app supports (web/js/render/fonts.js SCRIPT_FONTS, the vendored Noto families), in the stacks'
# default order.
INDIC_SCRIPTS: Final[tuple[Script, ...]] = ("tamil", "devanagari", "telugu", "kannada", "malayalam")
# Language prefix -> script: web/js/render/fonts.js LANGUAGE_SCRIPTS, which the stage's stack order follows (the
# lecture's own script first; pinned against it by web/tests/dynamic/fonts.test.js).
LANGUAGE_SCRIPTS: Final[dict[str, Script]] = {
    "ta": "tamil", "hi": "devanagari", "mr": "devanagari", "te": "telugu", "kn": "kannada", "ml": "malayalam"}

OFL: Final = "OFL-1.1"
OFL_TEXT_MARK: Final = "SIL OPEN FONT LICENSE Version 1.1"  # vendor.mjs and the tests look for it in each LICENSE
OFL_BELIEVED: Final = "OFL-1.1 (believed; verify the LICENSE file when vendoring, §4.6)"
FONTSOURCE_VERSION: Final = "5.3.0"  # every @fontsource package is pinned to this exact version in package.json

# --- unicode ranges ---------------------------------------------------------------------------------------------------
# The classic stylesheet's ranges: scripts/vendor.mjs `unicodeRange()`, the table web/vendor/fonts.css is written from.
_CLASSIC_RANGES: Final[dict[str, str]] = {
    "latin": "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,"
             "U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD",
    "latin-ext": "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,"
                 "U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
    "tamil": "U+0964-0965,U+0B82-0BFA,U+200C-200D,U+20B9,U+25CC",
    "devanagari": "U+0900-097F,U+1CD0-1CF9,U+200C-200D,U+20A8,U+20B9,U+20F0,U+25CC,U+A830-A839,U+A8E0-A8FF,"
                  "U+11B00-11B09",
    "telugu": "U+0951-0952,U+0964-0965,U+0C00-0C7F,U+1CDA,U+1CF2,U+200C-200D,U+25CC",
    "kannada": "U+0951-0952,U+0964-0965,U+0C80-0CF3,U+1CD0,U+1CD2-1CD3,U+1CDA,U+1CF2,U+1CF4,U+200C-200D,U+20B9,U+25CC,"
               "U+A830-A835",
    "malayalam": "U+0307,U+0323,U+0951-0952,U+0964-0965,U+0D00-0D7F,U+1CDA,U+1CF2,U+200C-200D,U+20B9,U+25CC",
}
# The B2b faces' ranges: each package's own unicode.json (Fontsource 5.3.0), which vendor.mjs compares row by row
# before it copies a file (it refuses a mismatch). They equal the classic table but for Malayalam, where Fontsource
# also lists the North Indic fractions U+A830-A832.
_FONTSOURCE_RANGES: Final[dict[str, str]] = {
    **_CLASSIC_RANGES,
    "malayalam": _CLASSIC_RANGES["malayalam"] + ",U+A830-A832",
}


# --- families ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """A variable face's axis as the FILE defines it (``fvar``): what Pillow reports and what a measurement sets."""

    tag: str
    minimum: int
    maximum: int
    default: int


@dataclass(frozen=True)
class Family:
    """A type family (§4.6). ``css`` names the stylesheet that declares its faces (None: not vendored, W2).
    ``weights``: the static faces vendored (empty for a variable family, whose ``wght`` axis is in ``axes``).
    ``requests``: the weights the render page loads before ``ready`` (each is one ``document.fonts.load``; for a
    variable face any weight loads its one file). ``file_stem``: the package's file-name prefix."""

    id: str
    name: str  # CSS font-family
    role: Role
    release: Release
    licence: str
    note: str = ""
    package: str | None = None
    version: str | None = None
    css: Sheet | None = None
    file_stem: str = ""
    weights: tuple[int, ...] = ()
    axes: tuple[Axis, ...] = ()
    subsets: tuple[str, ...] = ()
    script: Script | None = None
    requests: tuple[int, ...] = ()

    @property
    def vendored(self) -> bool:
        return self.css is not None

    @property
    def variable(self) -> bool:
        return bool(self.axes)

    @property
    def directory(self) -> str:
        """Where its files live under ``web/vendor`` (a B2b family has a folder of its own, with its LICENSE)."""
        return "fonts" if self.css == "classic" else f"fonts/dynamic/{self.id}"


def _classic(fid: str, name: str, role: Role, stem: str, weights: tuple[int, ...], subsets: tuple[str, ...],
             script: Script | None = None, note: str = "") -> Family:
    """A family vendor.mjs has vendored since B1 (its FONTS list; web/vendor/fonts.css declares every weight)."""
    return Family(fid, name, role, "R1", OFL, note, f"@fontsource/{stem}", FONTSOURCE_VERSION, "classic", stem,
                  weights, (), subsets, script, weights)


def _anek(script: Script) -> Family:
    """Anek for one Indic script (§4.6: variable, one family per script): the ``wght`` file of the script subset."""
    return Family(f"anek_{script}", f"Anek {script.capitalize()}", "indic", "R1", OFL,
                  "variable (wght 100-800, file default 500); the script subset only",
                  f"@fontsource-variable/anek-{script}", FONTSOURCE_VERSION, "dynamic", f"anek-{script}",
                  (), (Axis("wght", 100, 800, 500),), (script,), script, (400, 700))


_LATIN: Final = ("latin", "latin-ext")
_DISPLAY_WEIGHTS: Final = (400, 600, 700, 900)
_W2_DISPLAY: Final = (("montserrat", "Montserrat", ""), ("sora", "Sora", ""), ("space_grotesk", "Space Grotesk", ""),
                      ("bricolage_grotesque", "Bricolage Grotesque", ""), ("baloo_2", "Baloo 2", ""),
                      ("fraunces", "Fraunces", "Editorial"), ("dm_serif_display", "DM Serif Display", "Editorial"))

FAMILIES: Final[dict[str, Family]] = {f.id: f for f in (
    # display (R1 rotation: Outfit, Poppins, Lexend; §4.6)
    _classic("outfit", "Outfit", "display", "outfit", (300, 400, 700, 900), _LATIN),
    Family("poppins", "Poppins", "display", "R1", OFL, "Latin subsets only (its Devanagari subset is not vendored)",
           "@fontsource/poppins", FONTSOURCE_VERSION, "dynamic", "poppins", _DISPLAY_WEIGHTS, (), _LATIN, None,
           _DISPLAY_WEIGHTS),
    Family("lexend", "Lexend", "display", "R1", OFL, "", "@fontsource/lexend", FONTSOURCE_VERSION, "dynamic", "lexend",
           _DISPLAY_WEIGHTS, (), _LATIN, None, _DISPLAY_WEIGHTS),
    *(Family(fid, name, "display", "W2", OFL_BELIEVED, note) for fid, name, note in _W2_DISPLAY),
    # body ("High legibility": Atkinson Hyperlegible Next; §4.6, licence verified [CP S57] and in its LICENSE file)
    _classic("inter", "Inter", "body", "inter", (300, 400, 600, 700), _LATIN,
             note="also accepted as a display id (the B1 stage drew it)"),
    Family("atkinson_hyperlegible_next", "Atkinson Hyperlegible Next", "body", "R1", OFL, "the 'High legibility' body",
           "@fontsource/atkinson-hyperlegible-next", FONTSOURCE_VERSION, "dynamic", "atkinson-hyperlegible-next",
           (400, 600, 700), (), _LATIN, None, (400, 600, 700)),
    # mono
    _classic("jetbrains_mono", "JetBrains Mono", "mono", "jetbrains-mono", (400, 700), _LATIN),
    # Indic: the Noto Sans family per script (B1) and Anek per script (§4.6, licence verified [CP S58])
    *(_classic(f"noto_sans_{s}", f"Noto Sans {s.capitalize()}", "indic", f"noto-sans-{s}", (400, 700),
               (s, *_LATIN), s, "B1 Indic face") for s in INDIC_SCRIPTS),
    *(_anek(s) for s in INDIC_SCRIPTS),
)}

# Indic group id (presentation.fonts.indic) -> script -> family: Noto Sans per script (B1's), or Anek per script.
INDIC_GROUPS: Final[dict[str, dict[Script, str]]] = {
    "noto_sans": {s: f"noto_sans_{s}" for s in INDIC_SCRIPTS},
    "anek": {s: f"anek_{s}" for s in INDIC_SCRIPTS},
}
DEFAULT_FONTS: Final[dict[Role, str]] = {"display": "outfit", "body": "inter", "mono": "jetbrains_mono",
                                         "indic": "noto_sans"}
# The display ids the B1 stage draws (stage.js B1 DISPLAY_FACES, now fonts.js B1_DISPLAY_FACES); any other id was drawn
# as Outfit. Pinned against the JS by web/tests/dynamic/fonts.test.js.
B1_DISPLAY: Final[tuple[str, ...]] = ("outfit", "inter")
DISPLAY_FACES_R1: Final = tuple(f.id for f in FAMILIES.values() if f.role == "display" and f.release == "R1")
# Generic fallbacks at the end of each stack (stage.js B1 fontStacks).
GENERIC: Final[dict[str, str]] = {"display": "sans-serif", "body": "sans-serif", "mono": "ui-monospace, monospace"}


# --- faces (@font-face rows) ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    """One ``@font-face`` rule. ``weight`` is ``(w, w)`` for a static face and the axis range for a variable one;
    ``file`` is relative to ``web/vendor``, ``source`` to ``node_modules``."""

    family: str
    subset: str
    weight: tuple[int, int]
    file: str
    source: str
    unicode_range: str

    @property
    def ranges(self) -> tuple[tuple[int, int], ...]:
        return parse_unicode_range(self.unicode_range)

    def covers(self, codepoint: int) -> bool:
        return any(lo <= codepoint <= hi for lo, hi in self.ranges)


def parse_unicode_range(text: str) -> tuple[tuple[int, int], ...]:
    """``U+0000-00FF,U+0131`` -> ``((0x0, 0xFF), (0x131, 0x131))``. ``ValueError`` on anything else (no wildcards: the
    tables never use them)."""
    out: list[tuple[int, int]] = []
    for part in text.split(","):
        p = part.strip()
        if not p.startswith("U+"):
            raise ValueError(f"not a unicode range: {part!r}")
        lo, _, hi = p[2:].partition("-")
        a, b = int(lo, 16), int(hi or lo, 16)
        if a > b or b > 0x10FFFF:
            raise ValueError(f"not a unicode range: {part!r}")
        out.append((a, b))
    return tuple(out)


def _rows(f: Family) -> list[Face]:
    """A family's faces in declaration order. Classic: vendor.mjs's order (per weight, the package's file names
    sorted; it reads them with readdirSync and filters). B2b: the same order (per weight, subsets by file name)."""
    if not f.vendored or f.package is None:
        return []
    ranges = _CLASSIC_RANGES if f.css == "classic" else _FONTSOURCE_RANGES
    out: list[Face] = []
    # (low, high, the file-name tag): a static weight per file, or the variable file's wght range
    weights: tuple[tuple[int, int, str], ...] = (
        ((f.axes[0].minimum, f.axes[0].maximum, "wght"),) if f.variable else tuple((w, w, str(w)) for w in f.weights))
    for lo, hi, tag in weights:
        names = sorted((f"{f.file_stem}-{s}-{tag}-normal.woff2", s) for s in f.subsets)
        for name, subset in names:
            out.append(Face(f.id, subset, (lo, hi), f"{f.directory}/{name}", f"{f.package}/files/{name}",
                            ranges[subset]))
    return out


FACES: Final[tuple[Face, ...]] = tuple(row for f in FAMILIES.values() for row in _rows(f))


def faces_of(family_id: str) -> tuple[Face, ...]:
    """The family's faces in declaration order (empty when unknown or not vendored)."""
    return tuple(r for r in FACES if r.family == family_id)


# --- what a timeline draws (web/js/dynamic/fonts.js) --------------------------------------------------------------


#: ``presentation.fonts`` as callers hold it: the stored JSON dict, the schema's ``TimelineFonts`` model
#: (``timeline.presentation.fonts``), or None.
Fonts = Mapping[str, Any] | BaseModel | None


def fonts_mapping(fonts: Fonts) -> Mapping[str, Any]:
    """``presentation.fonts`` as the stored dict: a mapping as it is, a pydantic model (``TimelineFonts``) dumped with
    its unset roles left out (as the timeline JSON stores it), None as ``{}``. Any other value is a caller error and
    raises ``TypeError``, never a silent fallback to the default faces (the text fit would measure other faces than
    the page draws; B2b review B2b-CONF-2)."""
    if fonts is None:
        return {}
    if isinstance(fonts, Mapping):
        return fonts
    if isinstance(fonts, BaseModel):
        return fonts.model_dump(mode="json", exclude_none=True)
    raise TypeError(f"presentation.fonts must be a mapping, a TimelineFonts model or None, not {type(fonts).__name__}")


def is_b2_fonts(fonts: Fonts) -> bool:
    """Whether ``presentation.fonts`` is the B2 form (it names an ``indic`` group): fonts.js ``isB2Fonts``."""
    return isinstance(fonts_mapping(fonts).get("indic"), str)


def _vendored(fid: Any, roles: tuple[Role, ...]) -> bool:
    f = FAMILIES.get(fid) if isinstance(fid, str) else None
    return f is not None and f.vendored and f.role in roles


def stage_roles(fonts: Fonts) -> dict[Role, str]:
    """The family id the stage draws each role with, for a stored ``presentation.fonts``.

    B1 form (no ``indic``): display ``outfit`` or ``inter`` as named, any other id (or none) Outfit; body Inter, mono
    JetBrains Mono, Indic the Noto Sans faces, whatever else the dict says (the B1 stage read ``display`` only).
    B2 form: display any vendored display or body family; body a vendored body family; mono a vendored mono family;
    indic a known group; each else its default (:data:`DEFAULT_FONTS`)."""
    src = fonts_mapping(fonts)
    if not is_b2_fonts(src):
        display = src.get("display")
        return {"display": display if display in B1_DISPLAY else "outfit", "body": "inter", "mono": "jetbrains_mono",
                "indic": "noto_sans"}
    indic = src.get("indic")
    return {
        "display": src["display"] if _vendored(src.get("display"), ("display", "body")) else DEFAULT_FONTS["display"],
        "body": src["body"] if _vendored(src.get("body"), ("body",)) else DEFAULT_FONTS["body"],
        "mono": src["mono"] if _vendored(src.get("mono"), ("mono",)) else DEFAULT_FONTS["mono"],
        "indic": indic if isinstance(indic, str) and indic in INDIC_GROUPS else DEFAULT_FONTS["indic"],
    }


def resolver_fonts(display_font: str) -> dict[str, str]:
    """``TimelinePresentation.fonts`` as the resolver stores it (B2b): the B2 form naming exactly the families the B1
    stage draws for a presentation whose ``display_font`` is this id (an id it did not draw becomes ``outfit``, what it
    drew), so a rebuilt timeline looks the same. B2e's direct.py chooses the display face (§4.6 rotation)."""
    roles = stage_roles({"display": display_font})
    return {"display": roles["display"], "body": roles["body"], "mono": roles["mono"], "indic": roles["indic"]}


def script_for_language(language: str | None) -> Script | None:
    """``"ta-IN"`` -> ``"tamil"`` (render/fonts.js ``scriptForLanguage``)."""
    prefix = str(language or "").lower().replace("_", "-").split("-")[0]
    return LANGUAGE_SCRIPTS.get(prefix)


def indic_families(group: str, language: str | None = "") -> tuple[str, ...]:
    """The group's families in stack order: the lecture's own script first, then the others in INDIC_SCRIPTS order."""
    per_script = INDIC_GROUPS[group]
    own = script_for_language(language)
    order = ((own,) if own else ()) + tuple(s for s in INDIC_SCRIPTS if s != own)
    return tuple(per_script[s] for s in order)


def stack(fonts: Fonts, role: Literal["display", "body", "mono"],
          language: str | None = "") -> tuple[str, ...]:
    """The family ids of a CSS stack in the stage's order (fonts.js ``fontStacks``): display = display face, body face,
    Indic; body = body face, Indic; mono = the mono face (the generic fallbacks follow in CSS, :data:`GENERIC`)."""
    r = stage_roles(fonts)
    indic = indic_families(r["indic"], language)
    if role == "display":
        return (r["display"], r["body"], *indic)
    if role == "body":
        return (r["body"], *indic)
    return (r["mono"],)


def css_stack(fonts: Fonts, role: Literal["display", "body", "mono"],
              language: str | None = "") -> str:
    """The CSS ``font-family`` value fonts.js writes (``'Outfit', 'Inter', 'Noto Sans Tamil', ..., sans-serif``)."""
    names = ", ".join(f"'{FAMILIES[fid].name}'" for fid in stack(fonts, role, language))
    return f"{names}, {GENERIC[role]}"


# --- CSS font matching (text fit) -----------------------------------------------------------------------------------


def match_weight(available: tuple[tuple[int, int], ...], desired: int) -> tuple[int, int] | None:
    """CSS Fonts 4 §5.2 (font-weight step) over a family's face weights (``(w, w)`` static, ``(lo, hi)`` variable): a
    range holding ``desired`` wins; else for 400-500 the weights above it up to 500 ascending, then below it
    descending, then above 500 ascending; below 400 descending first, then ascending; above 500 ascending first, then
    descending. None when ``available`` is empty."""
    ranges = sorted(set(available))
    if not ranges:
        return None
    for r in ranges:
        if r[0] <= desired <= r[1]:
            return r

    def up(limit: float = float("inf")) -> tuple[int, int] | None:
        c = [r for r in ranges if desired < r[0] <= limit]
        return min(c, key=lambda r: r[0]) if c else None

    def down() -> tuple[int, int] | None:
        c = [r for r in ranges if r[1] < desired]
        return max(c, key=lambda r: r[1]) if c else None

    if 400 <= desired <= 500:
        return up(500) or down() or up()
    if desired < 400:
        return down() or up()
    return up() or down()


def face_for(family_id: str, weight: int, codepoint: int) -> Face | None:
    """The face of ``family_id`` a page draws ``codepoint`` at ``weight`` with: the matched weight (CSS Fonts 4 §5.2),
    then the LAST declared face of that weight whose unicode-range holds the character (§4.5). None when no face of
    the family is declared for it (the page then tries the next family in the stack). Whether the file really has the
    glyph is the measurer's check (a face's range can promise more than its cmap holds)."""
    rows = faces_of(family_id)
    matched = match_weight(tuple(r.weight for r in rows), weight)
    for r in reversed(rows):
        if r.weight == matched and r.covers(codepoint):
            return r
    return None


def web_path(face: Face, web_dir: Path | str | None = None) -> Path:
    """The face's file on disk (under ``web_dir/vendor``; the repository's ``web/`` by default)."""
    return Path(web_dir if web_dir is not None else ROOT / "web") / "vendor" / face.file


# --- the generated files ----------------------------------------------------------------------------------------------

_CSS_HEADER = f"""/* generated by {GENERATOR} — do not edit.
   The Dynamic stage's B2b faces (DYNAMIC_VIDEO_DESIGN §4.6, §4.12; B2 plan §2 "Fonts"): every @font-face of the faces
   aadhi/compose/dynamic/faces.py declares in this sheet, vendored by scripts/vendor.mjs (from web/js/dynamic/
   faces.gen.js) into web/vendor/fonts/dynamic/<family>/ with each package's LICENSE beside them. Only new family
   names: Inter, Outfit, JetBrains Mono and the Noto Sans Indic faces stay in web/vendor/fonts.css (tokens.css imports
   it), so nothing a page draws today resolves differently, and a face is fetched only when text asks for it.
   font-display: block, as the classic faces (the render page loads every face it uses before ready, §7.3). */
"""


def _weight_css(face: Face) -> str:
    lo, hi = face.weight
    return str(lo) if lo == hi else f"{lo} {hi}"


def render_css() -> str:
    """The text ``web/css/dynamic-fonts.css`` must hold."""
    lines = [
        f"@font-face{{font-family:'{FAMILIES[r.family].name}';font-style:normal;font-weight:{_weight_css(r)};"
        f"font-display:block;src:url('../vendor/{r.file}') format('woff2');unicode-range:{r.unicode_range};}}"
        for r in FACES if FAMILIES[r.family].css == "dynamic"]
    return _CSS_HEADER + "\n".join(lines) + "\n"


_JS_PREAMBLE = f"""// generated by {GENERATOR} — do not edit
// @ts-check
/**
 * The Dynamic stage's type faces (DYNAMIC_VIDEO_DESIGN §4.6, §4.12; B2 plan §2 "Fonts", §1.3): the one face table of
 * aadhi/compose/dynamic/faces.py as data. `{GENERATOR} --check` (CI) fails when this file
 * differs.
 *
 * Readers: web/js/dynamic/fonts.js (the stacks and the render page's font requests from presentation.fonts) and
 * scripts/vendor.mjs (which copies every face whose family has css "dynamic", with its LICENSE, into
 * web/vendor/fonts/dynamic/). `faces` is every @font-face rule the pages declare for the stage's families, in
 * declaration order: css "classic" rows are web/vendor/fonts.css's (vendor.mjs's own FONTS list, unchanged since B1),
 * css "dynamic" rows web/css/dynamic-fonts.css's. A face's `file` is relative to web/vendor, its `source` to
 * node_modules; `weight` is [w, w] (static) or the variable axis range; `unicode_range` the CSS descriptor's items
 * (joined with commas it is the descriptor). Frozen all the way down.
 */

/**
 * Freeze every object and array inside `value` (the caller freezes `value` itself).
 * @template T
 * @param {{T}} value
 * @returns {{T}}
 */
function freezeInside(value) {{
  for (const v of Object.values(/** @type {{object}} */ (value))) {{
    if (v !== null && typeof v === 'object') Object.freeze(freezeInside(v));
  }}
  return value;
}}

export const FACES = Object.freeze(freezeInside("""

_JS_POSTAMBLE = "));\n"
JS_TOP_LEVEL_KEYS: Final = ("version", "b1_display", "defaults", "generic", "scripts", "languages", "indic", "families",
                            "faces")


def table() -> dict[str, Any]:
    """The face table as plain data (the object ``FACES`` in faces.gen.js holds)."""
    data: dict[str, Any] = {
        "version": FACES_VERSION,
        "b1_display": list(B1_DISPLAY),
        "defaults": dict(DEFAULT_FONTS),
        "generic": dict(GENERIC),
        "scripts": list(INDIC_SCRIPTS),
        "languages": dict(LANGUAGE_SCRIPTS),
        "indic": {g: dict(m) for g, m in INDIC_GROUPS.items()},
        "families": {f.id: {
            "name": f.name, "role": f.role, "release": f.release, "licence": f.licence, "vendored": f.vendored,
            "css": f.css, "package": f.package, "version": f.version, "dir": f.directory if f.vendored else None,
            "weights": list(f.weights),
            "axes": [{"tag": a.tag, "min": a.minimum, "max": a.maximum, "default": a.default} for a in f.axes],
            "subsets": list(f.subsets), "script": f.script, "requests": list(f.requests),
        } for f in FAMILIES.values()},
        "faces": [{"family": r.family, "subset": r.subset, "weight": list(r.weight), "file": r.file,
                   "source": r.source, "unicode_range": r.unicode_range.split(",")} for r in FACES],
    }
    if tuple(data) != JS_TOP_LEVEL_KEYS:
        raise AssertionError("faces.gen.js top-level keys drifted from JS_TOP_LEVEL_KEYS")
    return data


def render_js() -> str:
    """The text ``web/js/dynamic/faces.gen.js`` must hold (laid out like catalogue.gen.js: keys sorted, lists in their
    declared order, 120 columns)."""
    from . import catalogue as cg  # the catalogue's canonical JSON layout (lazy: the table itself needs none of it)

    opening = _JS_PREAMBLE.rsplit("\n", 1)[1]
    return f"{_JS_PREAMBLE}{cg._js(cg._plain(table(), 'faces'), 0, len(opening))}{_JS_POSTAMBLE}"


OUTPUTS: Final = ((CSS_NAME, CSS_OUTPUT, render_css), (JS_NAME, JS_OUTPUT, render_js))


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    from .catalogue import first_difference

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a generated file is out of date")
    parser.add_argument("--dir", type=Path, default=None, help=argparse.SUPPRESS)  # tests: check copies in a folder
    args = parser.parse_args(argv)
    stale = 0
    for name, default, render in OUTPUTS:
        path = default if args.dir is None else args.dir / Path(name).name
        text = render()
        if args.check:
            line = first_difference(_read(path), text)
            if line is not None:
                print(f"{name} is out of date (first difference at line {line}): run {GENERATOR}", file=sys.stderr)
                stale += 1
        elif _read(path) != text:
            path.write_bytes(text.encode("utf-8"))
            print(f"wrote {name}")
    return 1 if stale else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
