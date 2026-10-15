"""HarfBuzz shaping of the vendored faces, as the Dynamic stage's Chromium shapes them (DYNAMIC_VIDEO_DESIGN §4.6
"Measured text fit"; B2 plan §1.11, §2 "Fonts"; owner decision 2026-10-14/15: text fit shapes every script with
HarfBuzz, ``uharfbuzz`` + ``fontTools`` + ``brotli``).

Chromium lays text out with HarfBuzz: a run of one face and one script is shaped with the face's GSUB and GPOS
(kerning, ligatures, contextual alternates, the Indic reordering and conjuncts, mark positioning) and takes the sum of
the shaped glyphs' advances. :func:`shape` does the same on the face file the page loads, so ``textfit`` measures the
text the page draws instead of bounding it (B2b's Pillow advances needed a 4% / 8% slack, a per-word floor and a
kerning table measured in Chromium for Latin text, and could not bound Indic shaping at all: §4.6 "As built (B2b)").

What is mirrored, and why each matters (tests/determinism/test_textfit_parity.py measures it against the real render
page, for every vendored face and weight the stage uses):

* **The face file.** ``web/vendor`` holds WOFF2 files and HarfBuzz reads only sfnt (TrueType / OpenType), so each file
  is decoded once per process with fontTools (``brotli`` is WOFF2's compression), with ``fontTools.ttLib.woff2
  .decompress``'s own steps (no bounding box or timestamp recomputed), and kept in memory (:func:`sfnt`).
* **The face's ``unicode-range``.** Chromium's HarfBuzz font finds a glyph for a character only when the
  ``@font-face`` rule's ``unicode-range`` holds it (``UnicodeRangeSet``), and the vendored subset files map more than
  their ranges (Outfit's ``latin`` file maps U+0301 and U+0102, the Indic files the space). Every font here is a
  HarfBuzz sub-font whose nominal-glyph lookup is the file's cmap restricted to the face's ranges (:func:`load_font`),
  so HarfBuzz's normalisation decides as the page's does: ``e`` + U+0301 is composed to ``é`` and drawn by the
  ``latin`` face, ``x`` + U+0301 has no glyph there and is left to the next face (``textfit.shape_text``: Chromium's
  font fallback), as on the page.
* **Variable axes**, set exactly as the page sets them: CSS ``font-weight`` drives ``wght`` (clamped to the face's
  descriptor range by ``textfit.face_key``), through the file's ``avar``; a variable face is never measured at its file
  default (Anek's is ``wght`` 500, where a page asks for 400). The file's axes must be the face table's
  (``faces.Family.axes``): a face with another axis (``opsz`` would follow the font size, ``font-optical-sizing:
  auto``) or none is a :class:`FaceError`, never measured with a guess.
* **Features.** HarfBuzz's defaults are Chromium's (``ccmp``, ``locl``, ``rlig``, ``mark``, ``mkmk``, ``calt``,
  ``clig``, ``liga``, ``rclt``, ``curs``, ``kern`` and the script shaper's own: the Indic ``nukt``, ``akhn``, ``rphf``,
  ``blwf``, ``half``, ``pstf``, ``pres``, ``abvs``, ``blws``, ``psts``, ``haln``, ...). ``dynamic.css`` asks for
  ``font-kerning: normal`` (``kern`` on) and ``font-variant-ligatures: common-ligatures`` (``liga``, ``clig`` on).
  Where ``letter-spacing`` is not 0 Chromium turns the optional ligatures off (CSS Text 3 §8.2: "UAs should not apply
  optional ligatures"); measured, it drops ``liga``, ``clig`` and ``calt`` (with any spacing, Inter draws ``2x3`` with
  a plain ``x`` and ``A*****`` without its alternates): :func:`features`. A slot drawn with ``font-variant-numeric:
  tabular-nums`` (board_generic's countdown ring, ``.dyn-ring-num``) shapes with ``tnum`` on (Inter 700 draws ``1``
  50% wider with it).
* **The shaper**: HarfBuzz's OpenType shaper (``ot``), named, as Chromium's HarfBuzz has only it. HarfBuzz otherwise
  takes its shaper list from the ``HB_SHAPER_LIST`` environment variable, and the Windows wheel also carries
  ``uniscribe`` and ``directwrite`` (which ignore the ``unicode-range`` sub-font) and every build ``fallback`` (no GSUB
  or GPOS): a width must not depend on the process environment.
* **Script runs and language** (``hb_buffer_set_script`` / ``_language``): a run is one face and one script
  (Chromium's ``RunSegmenter``: script runs, then font fallback inside them). The runs are Chromium's
  ``ScriptRunIterator``'s (:func:`script_runs`, a port of ``script_run_iterator.cc``): each character's script set is
  its Script_Extensions (``scx``) with Chromium's preference order (``ICUScriptData::GetScripts``: for a Common
  character with several, the lowest ICU script code that is not Latin goes first), a run continues while the
  intersection of the sets is not empty, a Common character keeps the run's script, a paired bracket takes its opening
  bracket's, and a run whose set is still several scripts at its end takes the first. So a word of digits and a
  NARROW NO-BREAK SPACE U+202F (``scx`` Latin, Mongolian) is a Mongolian run on the page (``1 000``, ``5 %`` written
  with it), with consequences for the letter-spacing (``textfit``: :func:`takes_letter_spacing`). The language is the page's
  ``lang`` (``render.js``: the board language, else ``en``) as BCP 47; HarfBuzz maps it to the OpenType language
  system, so a face's ``locl`` forms (Noto Sans Devanagari's Marathi) are the page's. Direction left to right.
* **One Unicode database for the text model** (:func:`category`, :func:`combining`, :func:`bidirectional`): Python's
  ``unicodedata`` (Unicode 14.0 in Python 3.11), with the characters the vendored faces draw that it does not know yet
  (:data:`NEWER_UCD`: Kannada's U+0CF3, a combining mark since Unicode 15; Devanagari Extended-A) given their current
  properties, as HarfBuzz and Chromium's ICU have them (tests/compose/dynamic/test_textfit.py checks that every
  character a vendored face draws is known). The default-ignorable characters are HarfBuzz's (:func:`ignorable`: the
  ones it hides) and ICU's (:func:`zero_width`: the ones Chromium gives no letter-spacing), not Python's ``Cf``.
* **Units.** The font's scale is :data:`UNITS_PER_EM` per em (an advance rounds to 1/65536 em; Chromium positions in
  16.16 pixels), so a width scales linearly with the font size, as the page draws it with ``--font-render-hinting=none``
  and ``text-rendering: geometricPrecision`` (unhinted, linear advances).

Fail closed (:class:`FaceError`): a file fontTools cannot decode, a decoded font in which HarfBuzz finds no glyphs or
no cmap, axes other than the table's, or a font HarfBuzz's OpenType shaper refuses. A missing dependency is an ``ImportError`` when this module is imported
(``textfit`` imports it; nothing imports either outside the Dynamic gate: tests/compose/test_dynamic_isolation.py).

Thread safety: the decoded faces and the fonts are cached per process and shared (the API answers previews from a
thread pool). They are built under :data:`_LOCK` and never changed after (a font's scale and variations are set before
it is cached); HarfBuzz faces and fonts may be shaped with from several threads, and every call has its own buffer.
"""

from __future__ import annotations

import io
import threading
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import uharfbuzz as hb
from fontTools import unicodedata as ucd
from fontTools.ttLib import TTFont

from . import faces as fc

UNITS_PER_EM: Final = 1 << 16  # the shaping scale: 1 em = 65536 units (an advance rounds to 1/65536 em)
DEFAULT_LANGUAGE: Final = "en"  # render.js: documentElement.lang = board_language || language || 'en'
COMMON: Final = "Zyyy"
INHERITED: Final = "Zinh"
# the optional ligatures and alternates Chromium turns off when letter-spacing is not 0 (measured: tests/determinism/
# test_textfit_parity.py); kern stays on (font-kerning: normal)
SPACED_OFF: Final = ("liga", "clig", "calt", "dlig", "hlig")
SHAPERS: Final = ("ot",)  # HarfBuzz's OpenType shaper, Chromium's; never HB_SHAPER_LIST's choice (module docstring)
# Chromium's cursive scripts (shape_result.cc IsCursiveScript): letter-spacing is not applied inside a run of one
# (CSS Text 3 §8.2.1), except after a character Chromium treats as a space
CURSIVE_SCRIPTS: Final = frozenset({"Arab", "Rohg", "Mand", "Mong", "Nkoo", "Phag", "Syrc"})

# --- the Unicode database the text model uses (module docstring) ------------------------------------------------------

# code point -> (general category, canonical combining class, bidi class) of the characters the vendored faces draw
# that Python's unicodedata (Unicode 14.0 in Python 3.11) does not know (Unicode 15: U+0CF3 KANNADA SIGN COMBINING
# ANUSVARA ABOVE RIGHT, a spacing mark; Devanagari Extended-A U+11B00..11B09, punctuation). A face that draws another
# character Python does not know fails tests/compose/dynamic/test_textfit.py (add it here, from the UCD).
NEWER_UCD: Final[Mapping[int, tuple[str, int, str]]] = MappingProxyType({
    0x0CF3: ("Mc", 0, "L"), **{cp: ("Po", 0, "L") for cp in range(0x11B00, 0x11B0A)}})
# HarfBuzz's default-ignorable characters (hb-unicode.hh is_default_ignorable; the ones it hides: no glyph drawn, no
# width), Unicode's Default_Ignorable_Code_Point less the Hangul fillers and U+1BCA0..1BCA3, which it draws
_HB_IGNORABLE: Final = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x17B4, 0x17B5), (0x180B, 0x180F),
                        (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
                        (0xFFF0, 0xFFF8), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
# ICU's Default_Ignorable_Code_Point (DerivedCoreProperties.txt), which Chromium's IsDefaultIgnorable reads (below
# U+0100 it takes only the soft hyphen)
_DEFAULT_IGNORABLE: Final = (*_HB_IGNORABLE, (0x115F, 0x1160), (0x3164, 0x3164), (0xFFA0, 0xFFA0), (0x1BCA0, 0x1BCA3))
# CSS letter-spacing: Chromium's TreatAsSpace (applied even inside a cursive run) and the characters its
# TreatAsZeroWidthSpace adds to the default-ignorable ones (never letter-spaced)
_TREAT_AS_SPACE: Final = frozenset(" \t\n\u00a0")
_ZERO_WIDTH_EXTRA: Final = frozenset("\f\r\ufffc\u200c\u200d")


def _in(ranges: tuple[tuple[int, int], ...], cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in ranges)


def category(ch: str) -> str:
    """The character's general category (``unicodedata``, with :data:`NEWER_UCD`)."""
    newer = NEWER_UCD.get(ord(ch))
    return newer[0] if newer else unicodedata.category(ch)


def combining(ch: str) -> int:
    """The character's canonical combining class (9: a virama)."""
    newer = NEWER_UCD.get(ord(ch))
    return newer[1] if newer else unicodedata.combining(ch)


def bidirectional(ch: str) -> str:
    """The character's bidi class."""
    newer = NEWER_UCD.get(ord(ch))
    return newer[2] if newer else unicodedata.bidirectional(ch)


def ignorable(ch: str) -> bool:
    """A default-ignorable character HarfBuzz hides (no glyph, no width): ZWJ, ZWNJ, the soft hyphen, the BOM, the
    bidi controls, variation selectors, ... (:data:`_HB_IGNORABLE`). Not every ``Cf`` is one: U+0600 ARABIC NUMBER
    SIGN is drawn."""
    return _in(_HB_IGNORABLE, ord(ch))


def zero_width(ch: str) -> bool:
    """Chromium's ``TreatAsZeroWidthSpace``: a character after which the page adds no letter-spacing (ICU's
    default-ignorable characters, the form feed, the carriage return, U+FFFC, ZWJ and ZWNJ)."""
    cp = ord(ch)
    return ch in _ZERO_WIDTH_EXTRA or (cp == 0xAD if cp < 0x100 else _in(_DEFAULT_IGNORABLE, cp))


def takes_letter_spacing(ch: str, script: str) -> bool:
    """Whether the page adds the letter-spacing after a HarfBuzz cluster that starts with ``ch`` in a run of ``script``
    (Chromium's ``ShapeResultSpacing::ComputeSpacing``): not after a zero-width character, and inside a cursive
    script's run (:data:`CURSIVE_SCRIPTS`) only after a space (``TreatAsSpace``)."""
    if zero_width(ch):
        return False
    return script not in CURSIVE_SCRIPTS or ch in _TREAT_AS_SPACE


_LOCK: Final = threading.RLock()


class TextFitError(RuntimeError):
    """Text fit cannot measure (fail closed); ``textfit`` re-exports it."""


class FaceError(TextFitError):
    """A vendored face cannot be read, or is not what the face table says (its variable axes): fail closed."""


@dataclass(frozen=True)
class FaceKey:
    """One measurable face: its file, its ``@font-face`` rule's ``unicode-range`` (``ranges``: the characters the page
    finds a glyph for in it; empty: the whole cmap) and, for a variable face, the ``wght`` it is set to and the axes
    the face table says the file has (checked when the font is loaded)."""

    path: str
    wght: int | None
    axes: tuple[fc.Axis, ...] = ()
    ranges: tuple[tuple[int, int], ...] = ()

    def in_range(self, codepoint: int) -> bool:
        return not self.ranges or any(lo <= codepoint <= hi for lo, hi in self.ranges)


@lru_cache(maxsize=128)
def sfnt(path: str) -> bytes:
    """The face file as an in-memory sfnt: a WOFF2 file decoded with fontTools (``woff2.decompress``'s steps: no
    bounding box or timestamp recomputed, tables in the canonical order); any other font as fontTools reads it.
    :class:`FaceError` when it is no font fontTools can read."""
    with _LOCK:
        try:
            font = TTFont(path, recalcBBoxes=False, recalcTimestamp=False)
            font.flavor = None
            font.flavorData = None
            out = io.BytesIO()
            font.save(out, reorderTables=True)
        except ImportError as exc:  # fontTools reads WOFF2 only with brotli: a broken install, never a guess
            raise FaceError(f"cannot read the face {path}: {exc} (brotli decodes WOFF2)") from exc
        except Exception as exc:  # noqa: BLE001 - fontTools raises many kinds on a damaged file: all fail closed
            raise FaceError(f"cannot read the face {path}: {type(exc).__name__}: {exc}") from exc
        return out.getvalue()


@lru_cache(maxsize=128)
def hb_face(path: str) -> hb.Face:
    """The HarfBuzz face of a face file (:func:`sfnt`). A decoded font with no glyph or an empty cmap is a
    :class:`FaceError` (HarfBuzz reads any bytes, as an empty face when they are no font)."""
    with _LOCK:
        face = hb.Face(hb.Blob(sfnt(path)))
        if face.glyph_count <= 1 or not face.unicodes:
            raise FaceError(f"cannot read the face {path}: HarfBuzz finds no glyphs or no cmap in it")
        return face


def file_axes(path: str) -> tuple[fc.Axis, ...]:
    """The file's variable axes (``fvar``), spelt as the face table spells them."""
    return tuple(fc.Axis(a.tag, round(a.min_value), round(a.max_value), round(a.default_value))
                 for a in hb_face(path).axis_infos)


@lru_cache(maxsize=128)
def cmap(path: str) -> frozenset[int]:
    """The code points the file's cmap maps (a ``unicode-range`` can promise more, and a subset file map more)."""
    return frozenset(hb_face(path).unicodes)


def has_glyph(key: FaceKey, ch: str) -> bool:
    """Whether the page finds a glyph for ``ch`` in the face: its cmap maps it and its ``unicode-range`` holds it."""
    return key.in_range(ord(ch)) and ord(ch) in cmap(key.path)


@lru_cache(maxsize=256)
def _glyphs(key: FaceKey) -> Mapping[int, int]:
    """code point -> glyph id for every character the page finds a glyph for in the face (:func:`has_glyph`)."""
    base = _base_font(key)
    out: dict[int, int] = {}
    for cp in sorted(cmap(key.path)):
        if key.in_range(cp):
            gid = base.get_nominal_glyph(cp)
            if gid:
                out[cp] = gid
    return out


@lru_cache(maxsize=256)
def _base_font(key: FaceKey) -> hb.Font:
    """The file's font at :data:`UNITS_PER_EM` with its axes checked and ``wght`` set (its cmap unrestricted)."""
    with _LOCK:
        have = file_axes(key.path)
        if have != tuple(key.axes):
            raise FaceError(f"{key.path}: variable axes {have} are not the face table's {tuple(key.axes)}")
        font = hb.Font(hb_face(key.path))
        font.scale = (UNITS_PER_EM, UNITS_PER_EM)
        if key.axes:
            if key.wght is None:
                raise FaceError(f"{key.path}: a variable face is measured at a set weight, never its file default")
            font.set_variations({"wght": float(key.wght)})
            if [round(v, 3) for v in font.get_var_coords_design()] != [float(key.wght)]:  # pragma: no cover
                raise FaceError(f"{key.path}: wght {key.wght} was not set")
        return font


@lru_cache(maxsize=256)
def load_font(key: FaceKey) -> hb.Font:
    """The face ready to shape as the page shapes it: :func:`_base_font` (scale, axes checked, ``wght`` set) as a
    HarfBuzz sub-font whose nominal-glyph lookup holds only what :func:`has_glyph` holds (the module docstring:
    Chromium's ``unicode-range`` filter); advances, kerning and variations are the parent's."""
    with _LOCK:
        parent = _base_font(key)
        glyphs = _glyphs(key)
        funcs = hb.FontFuncs()
        funcs.set_nominal_glyph_func(lambda _font, cp, _data: glyphs.get(cp, 0), None)
        font = hb.Font(parent)
        font.funcs = funcs
        return font


def features(tracking_em: float, tabular: bool = False) -> dict[str, bool]:
    """The OpenType features the page shapes with beyond HarfBuzz's defaults (module docstring): ``kern``, ``liga``
    and ``clig`` on (``font-kerning: normal``, ``font-variant-ligatures: common-ligatures``); with a letter-spacing
    other than 0 the optional ligatures and the contextual alternates off, as Chromium does; ``tnum`` on for a slot
    drawn with ``font-variant-numeric: tabular-nums``."""
    out = {"kern": True, **{tag: False for tag in SPACED_OFF}} if tracking_em != 0 else {
        "kern": True, "liga": True, "clig": True}
    if tabular:
        out["tnum"] = True
    return out


# --- script runs: Chromium's ScriptRunIterator ------------------------------------------------------------------------

# ICU's UScriptCode values (uscript.h), which Chromium's preference order compares; a script not listed sorts after
# every listed one (only the order among a character's Script_Extensions matters, and every character a vendored face
# draws has one of the long-standing low codes among its extensions)
_ICU_CODES: Final[Mapping[str, int]] = {tag: n for n, tag in enumerate((
    "Zyyy", "Zinh", "Arab", "Armn", "Beng", "Bopo", "Cher", "Copt", "Cyrl", "Dsrt", "Deva", "Ethi", "Geor", "Goth",
    "Grek", "Gujr", "Guru", "Hani", "Hang", "Hebr", "Hira", "Knda", "Kana", "Khmr", "Laoo", "Latn", "Mlym", "Mong",
    "Mymr", "Ogam", "Ital", "Orya", "Runr", "Sinh", "Syrc", "Taml", "Telu", "Thaa", "Thai", "Tibt", "Cans", "Yiii",
    "Tglg", "Hano", "Buhd", "Tagb", "Brai", "Cprt", "Limb", "Linb", "Osma", "Shaw", "Tale", "Ugar", "Hrkt", "Bugi",
    "Glag", "Khar", "Sylo", "Talu", "Tfng", "Xpeo", "Bali", "Batk", "Blis", "Brah", "Cham", "Cirt", "Cyrs", "Egyd",
    "Egyh", "Egyp", "Geok", "Hans", "Hant", "Hmng", "Hung", "Inds", "Java", "Kali", "Latf", "Latg", "Lepc", "Lina",
    "Mand", "Maya", "Mero", "Nkoo", "Orkh", "Perm", "Phag", "Phnx", "Plrd", "Roro", "Sara", "Syre", "Syrj", "Syrn",
    "Teng", "Vaii", "Visp", "Xsux", "Zxxx", "Zzzz", "Cari", "Jpan", "Lana", "Lyci", "Lydi", "Olck", "Rjng", "Saur",
    "Sgnw", "Sund", "Moon", "Mtei", "Armi", "Avst", "Cakm", "Kore", "Kthi", "Mani", "Phli", "Phlp", "Phlv", "Prti",
    "Samr", "Tavt", "Zmth", "Zsym", "Bamu", "Lisu", "Nkgb", "Sarb", "Bass", "Dupl", "Elba", "Gran", "Kpel", "Loma",
    "Mend", "Merc", "Narb", "Nbat", "Palm", "Sind", "Wara", "Afak", "Jurc", "Mroo", "Nshu", "Shrd", "Sora", "Takr",
    "Tang", "Wole", "Hluw", "Khoj", "Tirh", "Aghb", "Mahj", "Ahom", "Hatr", "Modi", "Mult", "Pauc", "Sidd", "Adlm",
    "Bhks", "Marc", "Newa", "Osge", "Hanb", "Jamo", "Zsye", "Gonm", "Soyo", "Zanb", "Dogr", "Gong", "Maka", "Medf",
    "Rohg", "Sogd", "Sogo", "Elym", "Hmnp", "Nand", "Wcho", "Chrs", "Diak", "Kits", "Yezi", "Cpmn", "Ougr", "Tnsa",
    "Toto", "Vith", "Kawi", "Nagm"))}
_UNLISTED: Final = len(_ICU_CODES)
# Unicode's Bidi_Paired_Bracket pairs (BidiBrackets.txt): opening bracket -> closing bracket
_BRACKETS: Final[Mapping[int, int]] = {o: c for o, c in (
    (0x28, 0x29), (0x5B, 0x5D), (0x7B, 0x7D), (0x0F3A, 0x0F3B), (0x0F3C, 0x0F3D), (0x169B, 0x169C), (0x2045, 0x2046),
    (0x207D, 0x207E), (0x208D, 0x208E), (0x2308, 0x2309), (0x230A, 0x230B), (0x2329, 0x232A),
    *((cp, cp + 1) for cp in range(0x2768, 0x2776, 2)), (0x27C5, 0x27C6), *((cp, cp + 1) for cp in range(0x27E6, 0x27F0, 2)),
    *((cp, cp + 1) for cp in range(0x2983, 0x298D, 2)), (0x298D, 0x2990), (0x298F, 0x298E),
    *((cp, cp + 1) for cp in range(0x2991, 0x2999, 2)), (0x29D8, 0x29D9), (0x29DA, 0x29DB), (0x29FC, 0x29FD),
    *((cp, cp + 1) for cp in range(0x2E22, 0x2E2A, 2)), *((cp, cp + 1) for cp in range(0x2E55, 0x2E5D, 2)),
    *((cp, cp + 1) for cp in range(0x3008, 0x3012, 2)), *((cp, cp + 1) for cp in range(0x3014, 0x301C, 2)),
    *((cp, cp + 1) for cp in range(0xFE59, 0xFE5F, 2)), (0xFF08, 0xFF09), (0xFF3B, 0xFF3D), (0xFF5B, 0xFF5D),
    (0xFF5F, 0xFF60), (0xFF62, 0xFF63))}
_CLOSING: Final[Mapping[int, int]] = {c: o for o, c in _BRACKETS.items()}
_MAX_BRACKETS: Final = 32  # script_run_iterator.h kMaxBrackets


def script_of(ch: str) -> str:
    """The character's Unicode script (ISO 15924, fontTools' copy of Scripts.txt)."""
    return str(ucd.script(ch))


def _icu(tag: str) -> int:
    return _ICU_CODES.get(tag, _UNLISTED)


def script_set(ch: str) -> list[str]:
    """The character's script set in Chromium's priority order (``ICUScriptData::GetScripts``): its
    Script_Extensions in ICU's order (ascending script code), the primary script first; a Common character with one
    extension is ``[Common, it]`` (a preferred script); for a Common character with several, and after Inherited for
    an Inherited one, the lowest code that is not Latin first (Chromium's swap loop, reproduced as it runs)."""
    primary = script_of(ch)
    if primary in ("Hira", "Kana"):
        return ["Hira"]  # GetScriptForOpenType: every kana is one category
    dst = sorted({str(s) for s in ucd.script_extension(ch)}, key=lambda t: (_icu(t), t))
    if primary == dst[0]:
        return dst
    if primary not in (COMMON, INHERITED):
        if primary in dst:
            i = dst.index(primary)
            dst[0], dst[i] = dst[i], dst[0]
        else:
            dst.append(primary)
            dst[0], dst[-1] = dst[-1], dst[0]
        return dst
    if primary == COMMON:
        if len(dst) == 1:
            return [COMMON, *dst]
        for i in range(1, len(dst)):
            if dst[0] == "Latn" or _icu(dst[i]) < _icu(dst[0]):
                dst[0], dst[i] = dst[i], dst[0]
        return dst
    dst.append(dst[0])  # Inherited with extensions: Inherited first, the preferred extension next
    dst[0] = INHERITED
    for i in range(2, len(dst)):
        if dst[1] == "Latn" or _icu(dst[i]) < _icu(dst[1]):
            dst[1], dst[i] = dst[i], dst[1]
    return dst


def _weak(tag: str) -> bool:
    return tag in (COMMON, INHERITED)  # USCRIPT_COMMON, USCRIPT_INHERITED: the codes <= USCRIPT_INHERITED


@lru_cache(maxsize=65536)
def script_runs(text: str) -> tuple[tuple[int, int, str], ...]:
    """``text``'s script runs as Chromium itemizes one text (``ScriptRunIterator``, script_run_iterator.cc, ported
    step by step: ``Consume``, ``MergeSets``, ``Fetch`` / ``FetchNextCharacter``'s Inherited rule, ``OpenBracket`` /
    ``CloseBracket`` / ``FixupStack``, ``ResolveCurrentScript`` with the common preferred script): ``(start, end,
    script)``, ISO 15924. The fast path of ``Consume`` (a precomputed bitmap) changes nothing it decides, so it is not
    ported; ``FixScriptsByEastAsianWidth`` (full-width brackets) is not ported: no vendored face draws one."""
    n = len(text)
    if not n:
        return ()
    sets = [script_set(ch) for ch in text]
    runs: list[tuple[int, int, str]] = []
    current = [COMMON]
    preferred = COMMON
    brackets: list[list[Any]] = []  # [opening bracket code point, script]
    fixup = 0
    start = 0

    def resolve() -> str:
        return preferred if current[0] == COMMON else current[0]

    def fix(resolved: str, exclude_last: bool) -> None:
        nonlocal fixup
        count = min(fixup, len(brackets))
        if count <= 0:
            return
        top = len(brackets) - 1
        if exclude_last:
            top -= 1
            count -= 1
            fixup = 1
        else:
            fixup = 0
        for k in range(count):
            brackets[top - k][1] = resolved

    def merge(nxt: list[str]) -> bool:
        nonlocal current, preferred
        if not nxt or not current:
            return False
        priority, rest = current[0], current[1:]
        if _weak(nxt[0]):
            if len(nxt) == 2 and _weak(priority) and preferred == COMMON:
                preferred = nxt[1]
            return True
        if _weak(priority):
            current = list(nxt)
            return True
        have = priority in nxt
        if not rest:
            return have
        candidates = nxt
        if not have:
            priority, candidates = nxt[0], nxt[1:]
            have = priority in rest
        out = [priority] if have else []
        if candidates:
            out += [s for s in rest if s in candidates]
        if out:
            current = out
            return True
        return False

    for i in range(n):
        nxt = list(sets[i])
        # FetchNextCharacter's Inherited rule: the character before an Inherited one with extensions takes those
        # extensions when its own set starts with Common (the first character is fetched without it)
        if i + 1 < n and sets[i + 1][0] == INHERITED and len(sets[i + 1]) > 1:
            if nxt[0] == COMMON:
                nxt = sets[i + 1][1:]
            sets[i + 1] = [INHERITED]
        cp = ord(text[i])
        if cp in _BRACKETS:
            if len(brackets) == _MAX_BRACKETS:
                brackets.pop(0)
                if fixup == _MAX_BRACKETS:
                    fixup -= 1
            brackets.append([cp, COMMON])
            fixup += 1
        elif cp in _CLOSING and brackets:
            target = _CLOSING[cp]
            for depth in range(len(brackets) - 1, -1, -1):
                if brackets[depth][0] == target:
                    if brackets[depth][1] != COMMON:
                        nxt = [brackets[depth][1]]
                    popped = len(brackets) - 1 - depth
                    del brackets[depth + 1:]
                    fixup = max(0, fixup - popped)
                    break
        if not merge(nxt):
            resolved = resolve()
            runs.append((start, i, resolved))
            fix(resolved, cp in _BRACKETS)
            current = nxt
            start = i
    runs.append((start, n, resolve()))
    return tuple(runs)


def scripts(text: str) -> tuple[str, ...]:
    """Each character's run script (:func:`script_runs`)."""
    return tuple(s for a, b, s in script_runs(text) for _ in range(a, b))


def language_tag(language: str | None) -> str:
    """The page's ``lang`` for a board language (``render.js``: the board language, else ``en``)."""
    text = str(language or "").strip()
    return text or DEFAULT_LANGUAGE


def shape(key: FaceKey, text: str, script: str, language: str, spaced: bool = False,
          tabular: bool = False) -> hb.Buffer:
    """``text`` shaped by one face as one run, as the page shapes it: HarfBuzz's OpenType shaper (:data:`SHAPERS`) on
    the face as the page loads it (:func:`load_font`) with the page's features (``spaced``: a letter-spacing other than
    0; ``tabular``: ``font-variant-numeric: tabular-nums``), ``script`` (ISO 15924) and ``language`` (BCP 47), left to
    right. Default-ignorable characters (ZWJ, ZWNJ, the soft hyphen, variation selectors) are passed as the page
    passes them: they steer the shaping and take no width. The buffer holds the glyphs (``glyph_infos``: glyph id, 0
    for a character the face has no glyph for, and cluster) and their ``glyph_positions`` (advances in
    1/:data:`UNITS_PER_EM` em). :class:`FaceError` when the OpenType shaper refuses the font."""
    font = load_font(key)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = script
    buf.language = language
    try:
        hb.shape(font, buf, features(1.0 if spaced else 0.0, tabular), shapers=list(SHAPERS))
    except RuntimeError as exc:  # every named shaper failed: never another shaper's widths
        raise FaceError(f"{key.path}: HarfBuzz's {'/'.join(SHAPERS)} shaper cannot shape with it: {exc}") from exc
    return buf


@lru_cache(maxsize=65536)
def advance(key: FaceKey, text: str, script: str, language: str, spaced: bool = False, tabular: bool = False) -> int:
    """The shaped advance of ``text`` drawn by one face as one run (:func:`shape`), in 1/:data:`UNITS_PER_EM` em."""
    if not text:
        return 0
    return sum(p.x_advance for p in shape(key, text, script, language, spaced, tabular).glyph_positions)


def clear_caches() -> None:
    """Drop the decoded faces, fonts, script runs and shaped advances (tests; a process normally keeps them)."""
    for f in (sfnt, hb_face, cmap, _glyphs, _base_font, load_font, advance, script_runs):
        f.cache_clear()


def web_file(face: fc.Face, web_dir: Path | str | None = None) -> str:
    """The face file's path as a :class:`FaceKey` holds it."""
    return str(fc.web_path(face, web_dir))
