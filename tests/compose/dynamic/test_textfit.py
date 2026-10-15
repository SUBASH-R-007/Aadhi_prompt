"""Measured text fit (aadhi.compose.dynamic.textfit, shaping; design §4.6, R-29; plan B2 §1.11, §2 "Fonts"; owner
decision 2026-10-14/15: HarfBuzz shaping for every script).

* **Every vendored face loads** through the shaping loader (fontTools decoding the WOFF2 file, HarfBuzz, a variable
  face's axes checked against the face table and set), and its shaped widths are pinned: a fontTools, brotli or
  HarfBuzz release that shapes differently fails here, wherever the fast suite runs (the container's included), before
  a timeline's text fit could change. HarfBuzz is integer arithmetic on the font's own tables, so the pins hold on
  every platform (B2b pinned Pillow's FreeType advances, which could differ on Debian).
* **Fail closed**: an unreadable face, one whose axes are not the table's, a missing ``brotli`` (no WOFF2 decoding) is
  a FaceError, a missing ``uharfbuzz`` an ImportError: never a fallback.
* What the page does, mirrored: the face's ``unicode-range`` filters its glyphs; kerning, ligatures and contextual
  alternates are shaped, and a letter-spacing turns the optional ligatures and the contextual alternates off; Chromium's
  font fallback per HarfBuzz cluster (Atkinson's ``ŶA`` kerned against the A its latin-ext face lacks); script runs and
  the page's language (Marathi forms); letter-spacing per HarfBuzz cluster. The widths the B2b review measured on the
  real page are reproduced within 0.05 px at 100 px.
* The page's text model: grapheme clusters, the word units of ``splitWords``, CSS whitespace, faces per cluster in the
  stage's stack order (CSS weight matching, last-declared range first, the cmap and the range holding the glyph).
* The conservative widths (0.5% slack and the layout unit), the sizes the page tries, the line limit and the preferred
  line count, the words the page may break (certainly, never, or either way at the minimum), the breaks of an
  over-wide word (after a hyphen first, never inside a cluster).
* What is certified: Latin, Indic, mixed text and combining sequences a vendored face draws get ``fits`` True / False;
  a character no vendored face draws, or a word that starts with a combining mark, gets None (an estimate only).
* No face kerns a space, or a character it has no glyph for, wider (what makes measuring flow text word by word
  conservative); deterministic across processes, hash seeds, caches and threads.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import shaping as sh
from aadhi.compose.dynamic import textfit as tf
from aadhi.compose.dynamic.templates import TEMPLATES
from tests.compose.dynamic.helpers import missing

ROOT = Path(__file__).resolve().parents[3]
B1 = {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}
B2 = fc.resolver_fonts("outfit")
SLOTS = TEMPLATES["board_generic"].slots
UNIT = sh.UNITS_PER_EM


@pytest.fixture(autouse=True)
def _vendored() -> None:
    if not all(fc.web_path(r).is_file() for r in fc.FACES):
        missing("web/vendor is not vendored (npm ci): the faces text fit measures with")


# --- every face, pinned ----------------------------------------------------------------------------------------------

SAMPLES = {"latin": "Hamburgefonstiv 0123", "latin-ext": "ĀăĆčĐęĞłŃőřŚşŤůŹž", "tamil": "தமிழ் மின்னோட்டம்",
           "devanagari": "विद्युत धारा", "telugu": "విద్యుత్ ప్రవాహం", "kannada": "ವಿದ್ಯುತ್ ಪ್ರವಾಹ",
           "malayalam": "വൈദ്യുത പ്രവാഹം"}
# HarfBuzz's shaped width of the face's sample (its words, each one run in the face, in 1/65536 em), at the face's
# weight; a variable face at wght 400 and 700. uharfbuzz 0.56.2 (HarfBuzz 14.5.0), fontTools 4.66.1, brotli 1.2.0.
PINNED = {
    "outfit-latin-300-normal.woff2": (640220,),
    "outfit-latin-ext-300-normal.woff2": (629733,),
    "outfit-latin-400-normal.woff2": (643956,),
    "outfit-latin-ext-400-normal.woff2": (630914,),
    "outfit-latin-700-normal.woff2": (671877,),
    "outfit-latin-ext-700-normal.woff2": (651298,),
    "outfit-latin-900-normal.woff2": (695993,),
    "outfit-latin-ext-900-normal.woff2": (669713,),
    "poppins-latin-400-normal.woff2": (707592,),
    "poppins-latin-ext-400-normal.woff2": (662242,),
    "poppins-latin-600-normal.woff2": (728497,),
    "poppins-latin-ext-600-normal.woff2": (681116,),
    "poppins-latin-700-normal.woff2": (740884,),
    "poppins-latin-ext-700-normal.woff2": (692123,),
    "poppins-latin-900-normal.woff2": (761463,),
    "poppins-latin-ext-900-normal.woff2": (710737,),
    "lexend-latin-400-normal.woff2": (698222,),
    "lexend-latin-ext-400-normal.woff2": (652279,),
    "lexend-latin-600-normal.woff2": (724239,),
    "lexend-latin-ext-600-normal.woff2": (676005,),
    "lexend-latin-700-normal.woff2": (736364,),
    "lexend-latin-ext-700-normal.woff2": (687210,),
    "lexend-latin-900-normal.woff2": (764871,),
    "lexend-latin-ext-900-normal.woff2": (713097,),
    "inter-latin-300-normal.woff2": (669408,),
    "inter-latin-ext-300-normal.woff2": (648480,),
    "inter-latin-400-normal.woff2": (681312,),
    "inter-latin-ext-400-normal.woff2": (657248,),
    "inter-latin-600-normal.woff2": (702976,),
    "inter-latin-ext-600-normal.woff2": (673888,),
    "inter-latin-700-normal.woff2": (713920,),
    "inter-latin-ext-700-normal.woff2": (682176,),
    "atkinson-hyperlegible-next-latin-400-normal.woff2": (633535,),
    "atkinson-hyperlegible-next-latin-ext-400-normal.woff2": (605421,),
    "atkinson-hyperlegible-next-latin-600-normal.woff2": (662307,),
    "atkinson-hyperlegible-next-latin-ext-600-normal.woff2": (627968,),
    "atkinson-hyperlegible-next-latin-700-normal.woff2": (675088,),
    "atkinson-hyperlegible-next-latin-ext-700-normal.woff2": (637731,),
    "jetbrains-mono-latin-400-normal.woff2": (747118,),
    "jetbrains-mono-latin-ext-400-normal.woff2": (668474,),
    "jetbrains-mono-latin-700-normal.woff2": (747118,),
    "jetbrains-mono-latin-ext-700-normal.woff2": (668474,),
    "noto-sans-tamil-latin-400-normal.woff2": (688980,),
    "noto-sans-tamil-latin-ext-400-normal.woff2": (626785,),
    "noto-sans-tamil-tamil-400-normal.woff2": (655030,),
    "noto-sans-tamil-latin-700-normal.woff2": (725156,),
    "noto-sans-tamil-latin-ext-700-normal.woff2": (654835,),
    "noto-sans-tamil-tamil-700-normal.woff2": (717815,),
    "noto-sans-devanagari-devanagari-400-normal.woff2": (233113,),
    "noto-sans-devanagari-latin-400-normal.woff2": (683472,),
    "noto-sans-devanagari-latin-ext-400-normal.woff2": (626785,),
    "noto-sans-devanagari-devanagari-700-normal.woff2": (258409,),
    "noto-sans-devanagari-latin-700-normal.woff2": (720897,),
    "noto-sans-devanagari-latin-ext-700-normal.woff2": (660535,),
    "noto-sans-telugu-latin-400-normal.woff2": (688980,),
    "noto-sans-telugu-latin-ext-400-normal.woff2": (626785,),
    "noto-sans-telugu-telugu-400-normal.woff2": (409271,),
    "noto-sans-telugu-latin-700-normal.woff2": (725156,),
    "noto-sans-telugu-latin-ext-700-normal.woff2": (654835,),
    "noto-sans-telugu-telugu-700-normal.woff2": (448332,),
    "noto-sans-kannada-kannada-400-normal.woff2": (400491,),
    "noto-sans-kannada-latin-400-normal.woff2": (688980,),
    "noto-sans-kannada-latin-ext-400-normal.woff2": (626785,),
    "noto-sans-kannada-kannada-700-normal.woff2": (435094,),
    "noto-sans-kannada-latin-700-normal.woff2": (688980,),
    "noto-sans-kannada-latin-ext-700-normal.woff2": (626785,),
    "noto-sans-malayalam-latin-400-normal.woff2": (688980,),
    "noto-sans-malayalam-latin-ext-400-normal.woff2": (626785,),
    "noto-sans-malayalam-malayalam-400-normal.woff2": (582155,),
    "noto-sans-malayalam-latin-700-normal.woff2": (725156,),
    "noto-sans-malayalam-latin-ext-700-normal.woff2": (654835,),
    "noto-sans-malayalam-malayalam-700-normal.woff2": (649789,),
    "anek-tamil-tamil-wght-normal.woff2": (637960, 667353),
    "anek-devanagari-devanagari-wght-normal.woff2": (243727, 259655),
    "anek-telugu-telugu-wght-normal.woff2": (423983, 443859),
    "anek-kannada-kannada-wght-normal.woff2": (373136, 397905),
    "anek-malayalam-malayalam-wght-normal.woff2": (561414, 590382),
}


def _weights(face: fc.Face) -> tuple[int, ...]:
    lo, hi = face.weight
    return (lo,) if lo == hi else (400, 700)


def _sample_units(key: tf.FaceKey, sample: str) -> int:
    return sum(sh.advance(key, part, sh.scripts(part)[0], "en") for part in sample.split())


def test_every_vendored_face_loads_and_shapes_as_pinned() -> None:
    assert {r.file.rsplit("/", 1)[1] for r in fc.FACES} == set(PINNED), "one pin per vendored face file"
    for face in fc.FACES:
        got = []
        for w in _weights(face):
            key = tf.face_key(face, w)
            font = tf.load_font(key)
            assert font.scale == (UNIT, UNIT), face.file
            assert key.ranges == face.ranges, face.file
            sample = SAMPLES[face.subset]
            assert all(tf.has_glyph(key, ch) for ch in sample if ch != " "), face.file
            got.append(_sample_units(key, sample))
        assert tuple(got) == PINNED[face.file.rsplit("/", 1)[1]], face.file
    # the decoded sfnt is what fontTools' own WOFF2 decoder gives
    from fontTools.ttLib import woff2

    path = sh.web_file(fc.faces_of("inter")[0])
    out = io.BytesIO()
    woff2.decompress(path, out)
    assert sh.sfnt(path) == out.getvalue()


def test_a_variable_face_is_measured_at_the_weight_the_page_asks_for() -> None:
    face = fc.faces_of("anek_tamil")[0]
    sample = SAMPLES["tamil"]
    at = {w: _sample_units(tf.face_key(face, w), sample) for w in (100, 400, 500, 700, 800)}
    file_default = sh.FaceKey(sh.web_file(face), 500, fc.FAMILIES["anek_tamil"].axes, face.ranges)
    assert sh.file_axes(sh.web_file(face)) == fc.FAMILIES["anek_tamil"].axes  # wght 100-800, file default 500
    assert _sample_units(file_default, sample) == at[500] != at[400], "the file's default is wght 500, not 400"
    assert at[100] < at[400] < at[700] < at[800]
    assert tf.load_font(tf.face_key(face, 400)).get_var_coords_design() == [400.0]
    # CSS clamps a weight outside the face's descriptor range to it
    assert tf.face_key(face, 900).wght == 800 and tf.face_key(face, 50).wght == 100
    assert tf.face_key(fc.faces_of("inter")[0], 700).wght is None  # a static face has no axis to set


def test_a_face_that_cannot_be_read_or_is_not_the_tables_fails_closed(tmp_path: Path) -> None:
    assert issubclass(tf.FaceError, tf.TextFitError) and tf.FaceError is sh.FaceError
    with pytest.raises(tf.FaceError, match="cannot read"):
        tf.load_font(tf.FaceKey(str(tmp_path / "missing.woff2"), None))
    broken = tmp_path / "broken.woff2"
    broken.write_bytes(b"wOF2" + b"\0" * 64)
    with pytest.raises(tf.FaceError, match="cannot read"):
        tf.load_font(tf.FaceKey(str(broken), None))
    anek = fc.faces_of("anek_tamil")[0]
    static = fc.faces_of("inter")[0]
    axes = fc.FAMILIES["anek_tamil"].axes
    with pytest.raises(tf.FaceError, match="variable axes"):  # a static file the table calls variable
        tf.load_font(tf.FaceKey(str(fc.web_path(static)), 400, axes))
    with pytest.raises(tf.FaceError, match="variable axes"):  # a variable file the table calls static
        tf.load_font(tf.FaceKey(str(fc.web_path(anek)), None))
    with pytest.raises(tf.FaceError, match="variable axes"):  # axes that differ from the file's
        tf.load_font(tf.FaceKey(str(fc.web_path(anek)), 400, (fc.Axis("wght", 100, 900, 400),)))
    with pytest.raises(tf.FaceError, match="set weight"):
        tf.load_font(tf.FaceKey(str(fc.web_path(anek)), None, axes))
    # and through measure(): a face file gone from web/vendor is an error, never another face
    with pytest.raises(tf.FaceError):
        tf.measure("Ohm", fonts=B1, role="display", weight=700, web_dir=tmp_path)


_NO_BROTLI = """
import sys
sys.modules["brotli"] = sys.modules["brotlicffi"] = None  # as if neither were installed
from aadhi.compose.dynamic import faces as fc, shaping as sh
try:
    sh.sfnt(sh.web_file(fc.faces_of("lexend")[0]))
except sh.FaceError as exc:
    print("FaceError", exc)
"""


def test_without_brotli_a_woff2_face_fails_closed() -> None:
    out = subprocess.run([sys.executable, "-c", _NO_BROTLI], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         check=False)
    assert out.returncode == 0 and out.stdout.startswith("FaceError") and "brotli" in out.stdout, (
        out.stdout, out.stderr[-2000:])


_NO_HARFBUZZ = """
import sys
sys.modules["uharfbuzz"] = None  # as if it were not installed
try:
    import aadhi.compose.dynamic.textfit
except ImportError as exc:
    print("ImportError", exc)
"""


def test_without_uharfbuzz_textfit_cannot_be_imported() -> None:
    out = subprocess.run([sys.executable, "-c", _NO_HARFBUZZ], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         check=False)
    assert out.returncode == 0 and out.stdout.startswith("ImportError") and "uharfbuzz" in out.stdout, (
        out.stdout, out.stderr[-2000:])


def test_glyph_presence_is_the_files_cmap_within_the_faces_range() -> None:
    inter = tf.face_key(fc.face_for("inter", 400, ord("A")), 400)
    tamil = tf.face_key(fc.face_for("noto_sans_tamil", 400, ord("க")), 400)
    poppins_ext = tf.face_key(fc.face_for("poppins", 700, ord("ħ")), 700)
    outfit = tf.face_key(fc.face_for("outfit", 700, ord("e")), 700)
    assert tf.has_glyph(inter, "A") and tf.has_glyph(inter, " ") and tf.has_glyph(tamil, "க")
    assert not tf.has_glyph(inter, "π") and not tf.has_glyph(inter, "க")
    # Poppins' latin-ext face is declared for U+0127 (its unicode-range) but its file has no glyph for it: the page
    # takes the next family of the stack (the body face), and so does text fit
    assert fc.face_for("poppins", 700, ord("ħ")) is not None and not tf.has_glyph(poppins_ext, "ħ")
    m = tf.measure("ħ", fonts={**B2, "display": "poppins"}, role="display", weight=700)
    (cluster,) = m.words[0].clusters
    assert cluster.key is not None and "inter-latin-ext-700" in cluster.key.path, cluster
    # Outfit's latin file maps U+0301 and the Tamil file the space, outside their ranges: the page finds no glyph
    assert ord("\u0301") in sh.cmap(outfit.path) and not tf.has_glyph(outfit, "\u0301")
    assert ord(" ") in sh.cmap(tamil.path) and not tf.has_glyph(tamil, " ")


# --- the page's text model -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "clusters"), [
    ("Ohm", ("O", "h", "m")),
    ("e\u0301a", ("e\u0301", "a")),  # a combining mark joins its base
    ("क्षेत्र", ("क्षे", "त्र")),  # conjuncts (consonant + virama + consonant) and vowel signs stay whole
    ("கொடு", ("கொ", "டு")),
    ("ஸ்ரீ", ("ஸ்ரீ",)),
    ("a\u200db", ("a\u200db",)),  # a ZWJ joins its neighbours
    ("x\u200cy", ("x\u200c", "y")),  # a ZWNJ ends the cluster it follows
    ("a\r\nb", ("a", "\r\n", "b")),
    ("\U0001f44d\U0001f3fd!", ("\U0001f44d\U0001f3fd", "!")),  # an emoji modifier
    ("", ()),
])
def test_grapheme_clusters(text: str, clusters: tuple[str, ...]) -> None:
    assert tf.graphemes(text) == clusters
    assert "".join(tf.graphemes(text)) == text


@pytest.mark.parametrize(("text", "words"), [
    ("  How   does it\twork? ", (("How", "   "), ("does", " "), ("it", "\t"), ("work?", " "))),
    ("10\u00a0V and 3\u202fA, 5\u2007m", (("10\u00a0V", " "), ("and", " "), ("3\u202fA,", " "),
                                                ("5\u2007m", ""))),
    ("foo\u200bbar", (("foo", "\u200b"), ("bar", ""))),  # a zero-width space: a break of no width
    ("a\ufeffb\u3000c", (("a", "\ufeff"), ("b", "\u3000"), ("c", ""))),  # JS \s: the BOM, the ideographic space
    ("", ()),
    ("   ", ()),
])
def test_word_units_are_split_words(text: str, words: tuple[tuple[str, str], ...]) -> None:
    assert tf.words(text) == words


def test_runs_take_the_script_around_their_common_characters() -> None:
    assert sh.scripts("(Ohm)") == ("Latn",) * 5  # punctuation takes the script after it, then before it
    assert sh.scripts("12") == ("Zyyy", "Zyyy")  # no strong script: Common
    assert sh.scripts("1க") == ("Taml", "Taml") and sh.scripts("க1") == ("Taml", "Taml")
    assert sh.scripts("a\u0301க\u200d") == ("Latn", "Latn", "Taml", "Taml")  # marks and ZWJ inherit
    assert sh.language_tag("") == sh.language_tag(None) == "en" and sh.language_tag("mr-IN") == "mr-IN"


@pytest.mark.parametrize(("text", "runs"), [
    # U+202F (scx Latin, Mongolian): Chromium's preference puts Mongolian first; a run with no strong script ends
    # Mongolian, a Latin letter anywhere in it makes it Latin
    ("1\u202f000", ((0, 5, "Mong"),)),
    ("5\u202f%", ((0, 3, "Mong"),)),
    ("1\u202f000km", ((0, 7, "Latn"),)),
    ("10\u202fkm", ((0, 5, "Latn"),)),
    ("about 1\u202f000", ((0, 11, "Latn"),)),
    ("1\u202f000 23", ((0, 8, "Mong"),)),
    # a Common character whose extensions exclude the run's script starts a run of its own
    ("a।", ((0, 1, "Latn"), (1, 2, "Beng"))),  # the danda's extensions: Bengali has the lowest ICU code
    ("க·", ((0, 1, "Taml"), (1, 2, "Copt"))),
    ("1˗", ((0, 2, "Thai"),)),  # Latin, Thai: Latin is never preferred
    # paired brackets: a closing bracket takes its opening bracket's script, resolved when its run ended
    ("a(க)", ((0, 2, "Latn"), (2, 3, "Taml"), (3, 4, "Latn"))),
    ("(க)a", ((0, 3, "Taml"), (3, 4, "Latn"))),
    # an Inherited mark with extensions gives them to the Common character before it
    ("1\u0308", ((0, 2, "Armn"),)),
    ("\u1cd1x", ((0, 2, "Latn"),)),
    # a Common character with one extension (U+1CF5: Bengali) is a preferred script for a run of Common ones
    ("1\u1cf5", ((0, 2, "Beng"),)),
    ("", ()),
])
def test_script_runs_are_chromiums(text: str, runs: tuple[tuple[int, int, str], ...]) -> None:
    """``shaping.script_runs`` is Chromium's ``ScriptRunIterator`` (script_run_iterator.cc), checked here on the cases
    its rules decide; the parity probe and tests/determinism/test_textfit_page.py measure the widths these runs give
    against the real page."""
    assert sh.script_runs(text) == runs
    assert sh.script_set("\u202f")[0] == "Mong" and sh.script_set("1") == ["Zyyy"]
    assert sh.script_set("ᳵ") == ["Zyyy", "Beng"]  # a Common character with one extension: a preferred script
    assert sh.script_set("\u0951")[:2] == ["Zinh", "Beng"]
    assert sh.script_set("ஃ")[0] == "Taml"  # the primary script first


def test_every_bracket_and_extension_a_vendored_face_draws_is_known() -> None:
    """The port's tables cover what can reach it from a certified word: every opening or closing bracket a vendored
    face draws is in ``shaping``'s Bidi_Paired_Bracket table, and every character a vendored face draws resolves its
    preferred script among the ICU codes the table lists."""
    drawn: set[int] = set()
    for face in fc.FACES:
        key = tf.face_key(face, face.weight[0])
        drawn |= {cp for cp in sh.cmap(key.path) if key.in_range(cp)}
    assert len(drawn) > 1500
    for cp in sorted(drawn):
        ch = chr(cp)
        if sh.category(ch) in ("Ps", "Pe") and unicodedata.mirrored(ch):
            assert cp in sh._BRACKETS or cp in sh._CLOSING, hex(cp)
        head = [s for s in sh.script_set(ch) if s not in (sh.COMMON, sh.INHERITED)][:1]
        assert all(s in sh._ICU_CODES for s in head), (hex(cp), sh.script_set(ch))
    assert sh._BRACKETS[0x28] == 0x29 and sh._CLOSING[0x5D] == 0x5B and sh._BRACKETS[0x298D] == 0x2990


def test_the_page_language_selects_the_faces_local_forms() -> None:
    """``lang`` reaches HarfBuzz as the page's (``render.js``: the board language): Noto Sans Devanagari and Anek draw
    Marathi's ल and श in their Marathi forms (``locl``), another width than Hindi's (the parity probe measures both)."""
    for indic in ("noto_sans", "anek"):
        fonts = {**B2, "indic": indic}
        hindi = tf.measure("ल श", fonts=fonts, role="body", weight=400, language="hi-IN")
        marathi = tf.measure("ल श", fonts=fonts, role="body", weight=400, language="mr-IN")
        assert [w.em for w in hindi.words] != [w.em for w in marathi.words], indic
        assert hindi.language == "hi-IN" and marathi.language == "mr-IN"
    assert tf.measure("Ohm", fonts=B1, role="display", weight=700).language == "en"


def test_letter_spacing_is_counted_per_harfbuzz_cluster() -> None:
    """Chromium adds the letter-spacing after every HarfBuzz cluster that draws something (measured in the parity
    probe): a Tamil consonant + virama + consonant is two clusters, the Telugu one in Noto Sans one, a ZWNJ none."""
    def spacings(text: str, language: str, role: str = "body") -> int:
        return tf.measure(text, fonts=B1, role=role, weight=600, language=language, tracking_em=0.02).words[0].spacings

    assert spacings("Ohm", "en") == 3
    assert spacings("க்ச", "ta-IN") == 2 and len(tf.graphemes("க்ச")) == 1
    assert spacings("గ్ఛ", "te-IN") == 1
    assert spacings("क\u200cष", "hi-IN") == 2  # the ZWNJ's own cluster takes none
    assert spacings("O\u00adh\u2060m", "en") == 3
    m = tf.measure("Typography", fonts=B1, role="display", weight=700, tracking_em=-0.015)
    assert m.width(m.words[0], 100, "raw") == pytest.approx(100 * (m.words[0].em - 0.015 * 10))


def test_default_ignorable_characters_take_no_width() -> None:
    for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad\ufe0f\u034f\u200f\u061c":
        assert tf.ignorable(ch), hex(ord(ch))
    # a format character that is not default-ignorable is drawn: U+0600 ARABIC NUMBER SIGN is no vendored face's, so
    # a word holding it is uncovered (B2b-H counted every Cf as ignorable and certified "1" + U+0600 at the 1's width)
    for ch in "a \u093e\u0301\u0600\u06dd\ufff9":
        assert not tf.ignorable(ch), hex(ord(ch))
    m = tf.measure("1\u0600", fonts=B1, role="display", weight=700)
    assert m.uncovered == ("\u0600",) and not m.reliable
    plain = tf.measure("Ohm", fonts=B1, role="display", weight=700)
    joined = tf.measure("O\u00adh\u2060m", fonts=B1, role="display", weight=700)
    assert joined.words[0].em == plain.words[0].em and joined.reliable


def test_the_default_ignorable_characters_are_the_ones_harfbuzz_hides() -> None:
    """``ignorable`` is HarfBuzz's own set: shaped by a face that has neither the character nor a space glyph in its
    range (Noto Sans Tamil's Tamil face), a character HarfBuzz hides is removed and any other is a .notdef; every code
    point of the planes text uses (the Tamil block, which the face draws, aside)."""
    import uharfbuzz as hb

    tamil = tf.face_key(fc.face_for("noto_sans_tamil", 400, ord("\u0b95")), 400)
    font = tf.load_font(tamil)
    planes = [*range(0x0, 0xB80), *range(0xC00, 0xD800), *range(0xE000, 0x20000), *range(0xE0000, 0xE1000)]
    hidden = []
    for cp in planes:
        buf = hb.Buffer()
        buf.add_codepoints([0x0B95, cp, 0x0B95])
        buf.direction, buf.script, buf.language = "ltr", "Taml", "ta"
        hb.shape(font, buf, {}, shapers=["ot"])
        if len(buf.glyph_infos) == 2 and all(i.codepoint for i in buf.glyph_infos):
            hidden.append(cp)
    assert hidden == [cp for cp in planes if tf.ignorable(chr(cp))]
    # Chromium's zero-width characters (no letter-spacing after them) add ICU's other default-ignorables, the form
    # feed, the carriage return and U+FFFC to them
    for ch in ("\u3164", "\ufffc", "\r", "\u200d", "\u00ad"):
        assert sh.zero_width(ch), hex(ord(ch))
    assert not sh.zero_width("\u0600") and not sh.zero_width("a") and not sh.zero_width("\u00a0")


def test_the_text_model_knows_every_character_a_vendored_face_draws() -> None:
    """One Unicode database: Python 3.11's ``unicodedata`` is Unicode 14.0, HarfBuzz's and Chromium's are newer; a
    character a vendored face draws that Python does not know (U+0CF3, a Kannada spacing mark since Unicode 15) takes
    its properties from ``shaping.NEWER_UCD``, so it joins its cluster and a word starting with it is not certified,
    as a word starting with another mark is not."""
    unknown = []
    for face in fc.FACES:
        key = tf.face_key(face, face.weight[0])
        for cp in sh.cmap(key.path):
            if key.in_range(cp):
                assert sh.category(chr(cp)) != "Cn", (face.file, hex(cp))
                if unicodedata.category(chr(cp)) == "Cn":
                    unknown.append(cp)
    assert set(unknown) <= set(sh.NEWER_UCD) and 0x0CF3 in unknown
    for cp in sh.NEWER_UCD:  # only what Python does not know, and in the script fontTools' newer data gives
        assert unicodedata.category(chr(cp)) in ("Cn", sh.NEWER_UCD[cp][0]), hex(cp)
    assert sh.script_of("\u0cf3") == "Knda" and tf.is_mark("\u0cf3")
    assert tf.graphemes("\u0c95\u0cf3\u0c95") == ("\u0c95\u0cf3", "\u0c95")
    lead = tf.measure("\u0cf3\u0c95", fonts=B1, role="display", weight=700, language="kn")
    assert not lead.reliable and tf.measure("\u0cbe\u0c95", fonts=B1, role="display", weight=700, language="kn").reliable is False
    assert tf.measure("\u0c95\u0cf3\u0c95", fonts=B1, role="display", weight=700, language="kn").reliable


def test_a_word_of_default_ignorable_characters_takes_no_width_and_is_certified() -> None:
    for text in ("ZWJ", "VS16", "WJ"):
        ch = {"ZWJ": chr(0x200D), "VS16": chr(0xFE0F), "WJ": chr(0x2060)}[text]
        m = tf.measure(ch, fonts=B1, role="display", weight=700)
        (word,) = m.words
        assert word.em == 0.0 and word.spacings == 0 and m.reliable and m.uncovered == (), text
    # a face that removes the glyph (no space glyph in its range to hide it with): one empty cluster, no width
    tamil = tf.face_key(fc.face_for("noto_sans_tamil", 400, ord("க")), 400)
    assert len(sh.shape(tamil, chr(0x200D), "Taml", "ta-IN").glyph_infos) == 0
    assert tf._clusters(tamil, chr(0x200D), 0, 1, "Taml", "ta-IN", False) == [(0, 1, 0, False, 0)]
    # HarfBuzz merges the removed ZWJ's cluster into the letter's, and Chromium reads the letter-spacing at the glyph's
    # cluster: the ZWJ's, which takes none (measured on the page with Noto Sans Tamil alone at +0.1 em: ZWJ + KA is
    # drawn without it, KA alone with it; behind Outfit, which hides the ZWJ itself, KA takes it)
    (only,) = tf._clusters(tamil, chr(0x200D) + "க", 0, 2, "Taml", "ta-IN", False)
    assert only[:2] == (0, 2) and only[2] > 0 and not only[3] and only[4] == 0
    assert [s.spaced for s in tf.shape_text("\u200dக", ("noto_sans_tamil",), 400, "ta-IN", True)] == [False]
    assert [s.spaced for s in tf.shape_text("க", ("noto_sans_tamil",), 400, "ta-IN", True)] == [True]
    display = tf.stack_for({**B1, "indic": "noto_sans"}, "display", "ta-IN")
    assert [s.spaced for s in tf.shape_text("\u200dக", display, 400, "ta-IN", True)] == [False, True]


def test_characters_before_the_first_glyphs_cluster_belong_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """HarfBuzz merges a removed glyph's cluster into its neighbour, so a shaped run starts at cluster 0 (every vendored
    face, every pair of default-ignorable characters before a letter: checked while building this step); were it ever
    to start later, the characters before it still belong to a cluster (none is dropped from the width or the face)."""
    from types import SimpleNamespace

    glyph = SimpleNamespace(cluster=1, codepoint=7)
    fake = SimpleNamespace(glyph_infos=[glyph], glyph_positions=[SimpleNamespace(x_advance=1000)])
    monkeypatch.setattr(tf.sh, "shape", lambda *a, **k: fake)
    key = tf.face_key(fc.faces_of("inter")[0], 400)
    assert tf._clusters(key, "xab", 1, 3, "Latn", "en", False) == [(1, 3, 1000, False, 2)]
    # and the page reads the letter-spacing at the glyph's cluster (the a), not at the cluster's first character (ZWJ)
    tf.clear_caches()
    try:
        (segment,) = tf.shape_text("\u200da", ("inter",), 400, "en", True)
        assert (segment.start, segment.end, segment.spaced) == (0, 2, True)
    finally:
        tf.clear_caches()


def test_a_face_harfbuzz_reads_as_no_font_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """fontTools refuses a damaged file first; should bytes it wrote still be no font to HarfBuzz (an empty face: no
    glyphs, no cmap), the face is refused too, never measured as zero widths."""
    sh.clear_caches()
    monkeypatch.setattr(sh, "sfnt", lambda path: bytes(64))
    try:
        with pytest.raises(tf.FaceError, match="no glyphs or no cmap"):
            sh.hb_face("damaged.woff2")
    finally:
        sh.hb_face.cache_clear()


def _files(text: str, fonts: dict, language: str = "en-IN", weight: int = 700, role: str = "display") -> list:
    m = tf.measure(text, fonts=fonts, role=role, weight=weight, language=language)
    return [c.key.path.replace("\\", "/").rsplit("/", 1)[1] if c.key else None for w in m.words for c in w.clusters]


def test_each_cluster_is_drawn_by_the_first_family_of_the_stage_stack_that_has_it() -> None:
    assert _files("Oł", B1) == ["outfit-latin-700-normal.woff2", "outfit-latin-ext-700-normal.woff2"]
    # the B1 form draws any display id but outfit / inter as Outfit (faces.stage_roles), the B2 form names it
    assert _files("O", {**B1, "display": "poppins"}) == ["outfit-latin-700-normal.woff2"]
    assert _files("O", {**B2, "display": "poppins"}) == ["poppins-latin-700-normal.woff2"]
    # U+0304 is in the ranges of both Latin faces of a weight: the last declared (latin-ext) is tried first (CSS
    # Fonts 4 §4.5); with a base only the latin face covers, the latin face draws the cluster
    assert _files("\u0304", B1) == ["outfit-latin-ext-700-normal.woff2"]
    assert _files("x\u0304", B1) == ["outfit-latin-700-normal.woff2"]
    # CSS weight matching per family: Outfit has no 600 face (700 is used), Inter has one; Noto Sans 600 -> 700
    assert _files("O", B1, weight=600) == ["outfit-latin-700-normal.woff2"]
    assert _files("O", B1, weight=600, role="body") == ["inter-latin-600-normal.woff2"]
    assert _files("க", B1, weight=600, role="body") == ["noto-sans-tamil-tamil-700-normal.woff2"]
    # Indic text: the lecture's own script first; a danda is in the Tamil and the Devanagari faces' ranges
    assert _files("க।", B1, "ta-IN") == ["noto-sans-tamil-tamil-700-normal.woff2"] * 2
    assert _files("क।", B1, "hi-IN") == ["noto-sans-devanagari-devanagari-700-normal.woff2"] * 2
    # the Anek group: its variable face, the weight set on its axis
    m = tf.measure("க", fonts={**B2, "indic": "anek"}, role="display", weight=600, language="ta-IN")
    (cluster,) = m.words[0].clusters
    assert "anek-tamil-tamil-wght" in cluster.key.path and cluster.key.wght == 600 and cluster.indic
    # mono: JetBrains Mono only
    assert _files("x=1", B1, role="mono", weight=400) == ["jetbrains-mono-latin-400-normal.woff2"] * 3
    assert tf.fallback_faces(("outfit",), 700)[0].path.endswith("outfit-latin-ext-700-normal.woff2")


def test_the_schema_model_measures_with_the_faces_it_names() -> None:
    """``fonts=timeline.presentation.fonts`` (a ``TimelineFonts`` model) measures the faces it names, exactly as its
    stored dict does; before the B2b review it measured the B1 defaults (Outfit, ~10% narrower than Poppins) and
    certified text the page overflows (B2b-CONF-2)."""
    from aadhi.schemas.timeline import TimelineFonts

    model = TimelineFonts(display="poppins", body="atkinson_hyperlegible_next", mono="jetbrains_mono", indic="anek")
    stored = model.model_dump(mode="json", exclude_none=True)
    a = tf.measure("Wide words everywhere", fonts=model, role="display", weight=700, language="ta-IN")
    b = tf.measure("Wide words everywhere", fonts=stored, role="display", weight=700, language="ta-IN")
    assert a.stack == b.stack and a.stack[:3] == ("poppins", "atkinson_hyperlegible_next", "anek_tamil")
    assert [a.width(w, 64) for w in a.words] == [b.width(w, 64) for w in b.words]
    kw = {"width": 700, "size": (56, 88), "max_lines": 2, "role": "display", "weight": 700}
    assert tf.fit_text("Wide words everywhere", fonts=model, **kw) == tf.fit_text("Wide words everywhere",
                                                                                   fonts=stored, **kw)
    with pytest.raises(TypeError, match="presentation.fonts must be"):
        tf.measure("x", fonts="poppins", role="display", weight=700)  # type: ignore[arg-type]


def test_a_character_no_vendored_face_draws_is_uncovered_and_never_certified() -> None:
    m = tf.measure("Area π r²", fonts=B1, role="display", weight=700)
    assert m.uncovered == ("π",) and not m.reliable
    r = tf.fit(m, width=1728, size=(72, 96), max_lines=2)
    assert r.fits is None and r.estimate is True and r.uncovered == ("π",)
    (cluster,) = [c for w in m.words for c in w.clusters if c.uncovered]
    assert cluster.em == tf.UNCOVERED_EM and cluster.key is None and not cluster.reliable
    with pytest.raises(ValueError, match="no font stack"):
        tf.measure("x", fonts=B1, role="indic", weight=400)


# --- shaping as the page shapes --------------------------------------------------------------------------------------


def _em(text: str, family: str = "outfit", weight: int = 700, tracking: float = 0.0) -> float:
    fonts = {"display": family, "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
    return tf.measure(text, fonts=fonts, role="display", weight=weight, tracking_em=tracking).words[0].em


def test_kerning_ligatures_and_contextual_alternates_are_shaped() -> None:
    # kerning (GPOS): AV is drawn narrower than its letters
    assert _em("AV") < _em("A") + _em("V") - 0.05
    # a ligature (Outfit's ffi is one glyph, one cluster) and a contextual alternate (Inter's x between digits is a
    # wider multiplication sign) ...
    assert _em("ffi") < _em("f") * 2 + _em("i") - 0.05
    assert _em("2x3", "inter", 400) > _em("2", "inter", 400) + _em("x", "inter", 400) + _em("3", "inter", 400) + 0.09
    # ... are optional: with a letter-spacing the page drops them (Chromium: liga, clig, calt off), kerning stays
    assert _em("2x3", "inter", 400, 0.02) == pytest.approx(_em("2", "inter", 400) + _em("x", "inter", 400)
                                                            + _em("3", "inter", 400), abs=1e-4)
    spaced = tf.measure("ffi", fonts=B1, role="display", weight=700, tracking_em=-0.015).words[0]
    assert spaced.spacings == 3 and spaced.em > _em("ffi") + 0.05  # three glyphs, no ligature
    assert _em("AV", tracking=-0.015) == _em("AV")
    assert sh.features(0.0) == {"kern": True, "liga": True, "clig": True}
    assert sh.features(-0.015) == sh.features(0.02) == {"kern": True, "liga": False, "clig": False, "calt": False,
                                                         "dlig": False, "hlig": False}


# Widths the real render page drew at 100 px (the B2b review's Chromium measurements, F1 and its two fix rounds):
# kerning that adds up per pair, contexts no pair shows (Inter's 2x3), contexts that chain along a run (Inter's *),
# which B2b bounded with a kerning table or left uncertified. Shaped, each is the page's width (and certified).
_PAGE_WIDTHS = [("outfit", 700, -0.015, "f'f'", 147.20), ("outfit", 700, -0.015, "f'f''", 170.70),
                ("outfit", 700, -0.015, "f'f'f'f'", 295.81), ("outfit", 700, -0.015, "f)f)f)", 231.61),
                ("atkinson_hyperlegible_next", 400, 0.0, "f'f'f'f'", 210.61),
                ("atkinson_hyperlegible_next", 600, 0.02, "f'f'f'f'", 254.61),
                ("inter", 400, 0.0, "2x3x4x5", 445.17), ("inter", 400, 0.0, "3x3", 189.70),
                ("inter", 400, 0.0, "1x1x1x1", 361.18), ("inter", 700, -0.015, "2x3x4x5", 420.90),
                ("inter", 600, 0.02, "2x3x4x5", 438.42),
                ("inter", 400, 0.0, "A*****", 388.14), ("inter", 400, 0.0, "*****A", 388.14),
                ("inter", 400, 0.0, "A********", 581.64), ("inter", 400, 0.0, "!********", 544.78),
                ("inter", 400, 0.0, "2********3", 638.78), ("inter", 400, 0.0, "4|~***", 357.53),
                ("inter", 400, 0.0, "**:<H0?", 412.50), ("inter", 400, 0.0, "8]*-*}*]", 416.95),
                ("inter", 600, 0.0, "A*****", 401.42), ("inter", 400, 0.0, "**A**", 320.26),
                ("inter", 400, 0.0, "A**B**C", 462.10), ("inter", 400, 0.0, "*(A)*", 270.95),
                ("inter", 700, -0.015, "**A**", 271.85),
                # Atkinson's latin-ext face kerns Ŷ against the A it has no glyph for (this step's probe)
                ("atkinson_hyperlegible_next", 700, 0.0, "ŶA", 129.8125),
                ("atkinson_hyperlegible_next", 700, 0.0, "AŶ", 132.8125),
                # the B2b-H review: words of digits and U+202F are Mongolian runs (no letter-spacing but after the
                # no-break space); a left-to-right mark changes nothing (the stage's CSS, this round's probe)
                ("outfit", 700, -0.015, "1\u202f000", 247.109375), ("outfit", 700, -0.015, "5\u202f%", 134.0),
                ("outfit", 700, -0.015, "100\u202f%", 249.0), ("outfit", 700, -0.015, "(1\u202f000)", 309.109375),
                ("outfit", 700, -0.015, "₹\u202f100", 237.640625), ("outfit", 700, -0.015, "A\u200eV", 132.203125),
                ("outfit", 700, -0.015, "1\u202f000\u00a0000", 464.40625), ("inter", 600, 0.02, "1\u202f000", 252.796875),
                ("inter", 600, 0.02, "(1\u202f000)", 327.40625), ("inter", 600, 0.02, "1\u202f000\u00a0000", 477.890625),
                ("inter", 600, 0.02, "A\u200eV", 142.0)]


@pytest.mark.parametrize(("family", "weight", "tracking", "word", "page"), _PAGE_WIDTHS)
def test_the_widths_the_page_drew_are_the_shaped_widths(family: str, weight: int, tracking: float, word: str,
                                                        page: float) -> None:
    fonts = {"display": family, "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
    m = tf.measure(word, fonts=fonts, role="display", weight=weight, tracking_em=tracking)
    (w,) = m.words
    assert m.reliable and w.reliable
    assert abs(m.width(w, 100, "raw") - page) < 0.05, (word, m.width(w, 100, "raw"), page)
    assert m.width(w, 100, "optimistic") <= page <= m.width(w, 100)
    r = tf.fit_text(word, width=1000.0, size=(40.0, 100.0), max_lines=1, role="display", weight=weight, fonts=fonts,
                    tracking_em=tracking)
    assert r.fits is True and r.reliable, r


def test_chromiums_font_fallback_is_mirrored() -> None:
    """A run is shaped face by face (Chromium's ``HarfBuzzShaper``): the clusters a face draws keep what its GPOS did
    beside the characters it has no glyph for, and a face whose range holds none of the characters left is skipped."""
    atkinson = {"display": "atkinson_hyperlegible_next", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
    pair = tf.measure("ŶA AŶ Ŷ A", fonts=atkinson, role="display", weight=700)
    ya, ay, y, a = (w.em for w in pair.words)
    assert ya == pytest.approx(y + a - 0.03, abs=1e-3) and ay == pytest.approx(y + a, abs=1e-9)
    segments = tf.shape_text("ŶA", ("atkinson_hyperlegible_next",), 700, "en")
    assert [s.key.path.rsplit("-", 3)[-3] for s in segments if s.key] == ["ext", "latin"]  # latin-ext tried first
    # a composed letter: e + U+0301 is drawn as é by the latin face (U+0301 is in no face's range), as on the page;
    # x + U+0301 has no precomposed letter: no face draws it (a system font: uncovered)
    composed = tf.measure("cafe\u0301", fonts=B1, role="display", weight=700)
    assert composed.reliable and composed.words[0].em == pytest.approx(_em("café"))
    assert tf.measure("x\u0301", fonts=B1, role="display", weight=700).uncovered == ("x\u0301",)
    # A + U+0306: Ă is in the latin-ext face, but neither A nor U+0306 is in its range, so the page never tries it
    assert tf.measure("A\u0306", fonts=B1, role="display", weight=700).uncovered == ("A\u0306",)


def test_no_face_kerns_a_space_or_a_missing_glyph_wider() -> None:
    """Text in normal flow is one text node on the page, shaped across its spaces; Python measures its words and spaces
    apart. That only over-estimates while no face draws a character wider beside a space, or beside a character it has
    no glyph for (Chromium keeps such kerning: :func:`test_chromiums_font_fallback_is_mirrored`), on either side. Every
    face, every character it draws."""
    for face in fc.FACES:
        for w in _weights(face):
            key = tf.face_key(face, w)
            neighbours = (" ", "a" if face.subset not in ("latin", "latin-ext") else "க")  # drawn or not: either way
            chars = [chr(cp) for cp in sorted(sh.cmap(key.path)) if cp > 0x20 and tf.has_glyph(key, chr(cp))
                     and sh.category(chr(cp))[0] not in "MZC" and not tf.ignorable(chr(cp))]
            for x in neighbours:
                ax = sh.advance(key, x, "Zyyy", "en")
                for ch in chars:
                    script = sh.scripts(ch)[0]
                    alone = sh.advance(key, ch, script, "en") + ax
                    assert sh.advance(key, ch + x, script, "en") <= alone, (face.file, w, ch, x)
                    assert sh.advance(key, x + ch, script, "en") <= alone, (face.file, w, x, ch)


def test_runs_scale_linearly() -> None:
    m = tf.measure("Hamburgefonstiv", fonts=B1, role="display", weight=700)
    (word,) = m.words
    key = word.clusters[0].key
    assert word.em == sh.advance(key, "Hamburgefonstiv", "Latn", "en") / UNIT
    assert word.em == pytest.approx(sum(c.em for c in word.clusters))
    assert m.width(word, 96, "raw") == pytest.approx(2 * m.width(word, 48, "raw"))
    assert m.width(word, 96, "raw") == pytest.approx(96 * word.em)


def test_conservative_and_optimistic_widths_add_and_take_the_margins() -> None:
    m = tf.measure("Typography", fonts=B1, role="display", weight=700, tracking_em=-0.015)
    (word,) = m.words
    track = -0.015 * 10
    assert m.width(word, 100, "raw") == pytest.approx(100 * (word.em + track))
    assert m.width(word, 100) == pytest.approx(100 * (word.em * (1 + tf.SLACK) + track) + tf.BOX_PX)
    assert m.width(word, 100, "optimistic") == pytest.approx(100 * (word.em * (1 - tf.SLACK) + track))
    # re-derived from the parity probe (0.03 px at 100 px): far below B2b's 4% / 8% and 0.06 em per word
    assert tf.SLACK == 0.005 and tf.BOX_PX == 1 / 64
    tamil = tf.measure("மின்", fonts=B1, role="display", weight=700, language="ta-IN")
    assert tamil.words[0].indic and tamil.reliable
    assert tamil.width(tamil.words[0], 100) == pytest.approx(100 * tamil.words[0].em * (1 + tf.SLACK) + tf.BOX_PX)


def test_a_title_of_kerning_pairs_is_not_certified_wider_than_the_page_draws_it() -> None:
    """board_generic's portrait title (B1 faces): the page draws 17 ``f'`` pairs per word on 4 lines at the minimum
    (overflow 118 px) and 12 pairs per word only from 76 px down (the B2b review's measurement, F1)."""
    spec = TEMPLATES["board_generic"].slots["title"]["portrait"]["default"]
    for pairs, fits, at_most in ((17, False, 56.0), (12, True, 76.0)):
        r = tf.fit_slot(" ".join(["f'" * pairs] * 2), spec, fonts=B1)
        assert r.reliable and r.fits is fits and r.size <= at_most, (pairs, r)


@pytest.mark.parametrize(("title", "kind"), [("AV" * 10, "either"), ("TY" * 10, "whole"), ("To" * 11, "whole"),
                                             ("Yo" * 11, "whole")])
def test_a_word_the_page_keeps_whole_is_not_read_as_certainly_broken(title: str, kind: str) -> None:
    """The page draws these kerned words whole at 56 px on one line (AVAV... 695 px of the 696 px column; the B2b
    review, F2): never read as certainly broken, so Python's size is the page's (tests/determinism/
    test_textfit_page.py)."""
    spec = TEMPLATES["board_generic"].slots["title"]["portrait"]["default"]
    m = tf.measure(title, fonts=B1, role=spec.face, weight=spec.weight, tracking_em=spec.tracking_em)
    assert tf.word_kinds(m, spec.w, spec.size[0]) == (kind,)
    assert m.width(m.words[0], spec.size[0], "optimistic") <= spec.w  # what the page drew is not ruled out
    r = tf.fit_slot(title, spec, fonts=B1)
    assert r.fits and r.size == 56.0, r


# Words wider than the box at every size: the page breaks them at every size and takes the sizes given
# (tests/determinism/test_textfit_page.py for the titles; B2b review F2, round 2): (fonts, slot, text, word kinds,
# size, line count)
_INTER = {"display": "inter", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
_LEXEND = {"display": "lexend", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
_URL = "See https://en.wikipedia.org/wiki/Electrochemistry_and_batteries_overview"
_OVER_WIDE = [(_INTER, "title", "Supercalifragilisticexpialidocious", ("break",), 84.0, 2),
              (_INTER, "title", "Electrochemistry_and_batteries_overview", ("break",), 64.0, 2),
              (_LEXEND, "title", "Supercalifragilisticexpialidocious", ("break",), 80.0, 2),
              (B1, "body", _URL, ("whole", "break"), 56.0, 5)]


@pytest.mark.parametrize(("fonts", "slot", "text", "kinds", "size", "lines"), _OVER_WIDE)
def test_a_word_wider_than_the_box_at_every_size_is_read_as_broken(fonts: dict[str, str], slot: str, text: str,
                                                                   kinds: tuple[str, ...], size: float,
                                                                   lines: int) -> None:
    spec = TEMPLATES["board_generic"].slots[slot]["portrait"]["default"]
    m = tf.measure(text, fonts=fonts, role=spec.face, weight=spec.weight, tracking_em=spec.tracking_em)
    assert m.reliable and tf.word_kinds(m, spec.w, spec.size[0]) == kinds
    assert m.width(m.words[-1], spec.size[0], "optimistic") > spec.w  # so the page breaks it at every size
    r = tf.fit_slot(text, spec, fonts=fonts)
    assert r.fits and r.size == size and len(r.lines) == lines, r
    assert r.stored() == {"size": size, "lines": lines}


def test_whitespace_between_words_is_as_wide_as_the_page_draws_it() -> None:
    space = tf.measure("a b", fonts=B1, role="display", weight=700).words[0].sep_em
    assert space > 0
    assert tf.measure("a \t\n  b", fonts=B1, role="display", weight=700).words[0].sep_em == space  # collapsed
    assert tf.measure("a\u200bb", fonts=B1, role="display", weight=700).words[0].sep_em == 0.0
    en = tf.measure("a\u2002b", fonts=B1, role="display", weight=700).words[0].sep_em  # an en space keeps its width
    assert en > space
    assert tf.measure("a b", fonts=B1, role="display", weight=700).words[-1].sep_em == 0.0


def test_a_space_takes_the_letter_spacing_when_it_is_positive() -> None:
    plain = tf.measure("a  b\u2002c", fonts=B1, role="body", weight=600)
    pos = tf.measure("a  b\u2002c", fonts=B1, role="body", weight=600, tracking_em=0.02)
    neg = tf.measure("a  b\u2002c", fonts=B1, role="body", weight=600, tracking_em=-0.015)
    assert [w.sep_units for w in pos.words] == [1, 1, 0]  # a collapsed run is one space; the en space one character
    for i in (0, 1):
        assert pos.sep(pos.words[i], 100, "raw") == pytest.approx(100 * (plain.words[i].sep_em + 0.02))
        assert neg.sep(neg.words[i], 100, "raw") == pytest.approx(100 * (plain.words[i].sep_em - 0.015))
        # conservative: a negative letter-spacing is not taken off a space (never under-estimated)
        assert neg.sep(neg.words[i], 100) == pytest.approx(100 * plain.words[i].sep_em * (1 + tf.SLACK) + tf.BOX_PX)
        assert neg.sep(neg.words[i], 100, "optimistic") == pytest.approx(
            100 * (plain.words[i].sep_em * (1 - tf.SLACK) - 0.015))
        assert pos.sep(pos.words[i], 100) == pytest.approx(100 * (plain.words[i].sep_em * (1 + tf.SLACK) + 0.02)
                                                           + tf.BOX_PX)
    # a zero-width space is a break of no width and takes no letter-spacing (Chromium: TreatAsZeroWidthSpace)
    zw = tf.measure("a\u200bb", fonts=B1, role="body", weight=600, tracking_em=0.02)
    assert zw.words[0].sep_units == zw.words[0].sep_flow_units == 0
    assert zw.sep(zw.words[0], 100, "raw") == zw.sep(zw.words[0], 100) == 0.0
    assert pos.sep(pos.words[-1], 100) == 0.0


def test_an_empty_text_fits_with_no_lines() -> None:
    for text in ("", "   ", "\u200b"):
        r = tf.fit_text(text, width=600, size=(40, 56), max_lines=2, role="display", weight=700, fonts=B1)
        assert (r.fits, r.size, r.lines) == (True, 56, ()), text


# --- fitting ---------------------------------------------------------------------------------------------------------


def test_the_sizes_the_page_tries() -> None:
    assert tf.sizes(72, 96) == (96, 92, 88, 84, 80, 76, 72)
    assert tf.sizes(56, 88) == (88, 84, 80, 76, 72, 68, 64, 60, 56)
    assert tf.sizes(40.32, 56) == (56, 52, 48, 44, 40.32)  # the minimum itself last
    assert tf.sizes(40, 40) == (40,)
    for bad in ((0, 10), (10, 5), (-1, 5)):
        with pytest.raises(ValueError):
            tf.sizes(*bad)
    with pytest.raises(ValueError):
        tf.sizes(10, 20, 0)


def _m(text: str) -> tf.Measured:
    return tf.measure(text, fonts=B1, role="display", weight=700, language="en-IN", tracking_em=-0.015)


def _one_line(m: tf.Measured, size: float) -> float:
    return sum(m.width(w, size) + (m.sep(w, size) if i + 1 < len(m.words) else 0) for i, w in enumerate(m.words))


def test_the_largest_size_whose_lines_fit_and_the_line_limit() -> None:
    m = _m("How does a battery push current through a circuit?")
    one_line = _one_line(m, 96)
    r = tf.fit(m, width=one_line + 1, size=(72, 96), max_lines=2)
    assert (r.size, len(r.lines), r.fits, r.estimate, r.reliable) == (96, 1, True, True, True)
    assert r.lines == ("How does a battery push current through a circuit?",) and r.widths[0] <= one_line + 1
    r = tf.fit(m, width=one_line - 1, size=(72, 96), max_lines=2)
    assert r.size == 96 and len(r.lines) == 2 and r.fits and max(r.widths) <= one_line - 1
    # narrower: the size steps down until two lines fit, or the minimum overflows
    r = tf.fit(m, width=one_line * 0.42, size=(72, 96), max_lines=2)
    assert r.fits and 72 <= r.size < 96 and len(r.lines) == 2
    assert not tf.layout(m, r.size + 4, one_line * 0.42, 2, 72).fits
    r = tf.fit(m, width=one_line * 0.2, size=(72, 96), max_lines=2)
    assert r.fits is False and r.estimate is False and r.size == 72 and len(r.lines) > 2
    # no line limit: always fits, at the largest size
    r = tf.fit(m, width=one_line * 0.2, size=(72, 96), max_lines=None)
    assert r.fits and r.size == 96
    # an unbounded box (a chip): one line
    r = tf.fit(m, width=None, size=(40, 40), max_lines=1)
    assert r.fits and r.lines == (m.text,) and r.width is None


def test_the_preferred_line_count_is_tried_first() -> None:
    spec = SLOTS["title"]["portrait"]["default"]  # 9:16: 88-56 px, 2 lines, one preferred (R-25)
    assert spec.prefer_lines == 1 and spec.max_lines == 2
    short = tf.fit_slot("Ohm's law", spec, fonts=B1, language="en-IN")
    assert short.fits and len(short.lines) == 1 and short.size == 88
    m = _m("Parallel circuits explained")
    one = tf.fit(m, width=spec.w, size=spec.size, max_lines=1)
    two = tf.fit(m, width=spec.w, size=spec.size, max_lines=2)
    both = tf.fit(m, width=spec.w, size=spec.size, max_lines=2, prefer_lines=1)
    assert one.fits and two.fits and two.size > one.size and len(two.lines) == 2
    assert (both.size, both.lines) == (one.size, one.lines), "one line at a smaller size wins over two larger"
    longer = _m("Parallel circuits explained side by side")
    p = tf.fit(longer, width=spec.w, size=spec.size, max_lines=2, prefer_lines=1)
    assert p.fits and len(p.lines) == 2, "one line cannot fit even at 56 px: the line limit decides"


def test_a_word_the_page_must_keep_whole_is_kept_whole() -> None:
    m = _m("Electromagnetism")
    (word,) = m.words
    lo, hi = 72.0, 96.0
    cons_lo, opt_lo = m.width(word, lo), m.width(word, lo, "optimistic")
    # certainly whole: even the conservative reading fits at the minimum; larger sizes that do not fit are refused
    width = cons_lo + 1
    assert tf.word_kinds(m, width, lo) == ("whole",)
    r = tf.fit(m, width=width, size=(lo, hi), max_lines=2)
    assert r.fits and r.size == lo and r.lines == ("Electromagnetism",)
    assert not tf.layout(m, 76, width, 2, lo).whole
    # certainly broken: even the favourable reading is wider at the minimum, so the page breaks it (lines of its own)
    width = opt_lo - 1
    assert tf.word_kinds(m, width, lo) == ("break",)
    r = tf.fit(m, width=width, size=(lo, hi), max_lines=3)
    assert r.fits and r.size == hi and len(r.lines) >= 2 and "".join(r.lines) == "Electromagnetism"
    assert all(w <= width for w in r.widths)
    # in between: the page may break it or keep it whole; above the minimum Python cannot be sure the page keeps it
    # whole, at the minimum both ways must fit
    width = (cons_lo + opt_lo) / 2
    assert tf.word_kinds(m, width, lo) == ("either",)
    assert not tf.layout(m, 76, width, 2, lo).whole
    r = tf.fit(m, width=width, size=(lo, hi), max_lines=2)
    assert r.fits and r.size == lo
    two = _m("Electromagnetism explained")
    r = tf.fit(two, width=width, size=(lo, hi), max_lines=2)
    assert r.fits is False, "broken, the word takes two lines and 'explained' a third"
    assert tf.fit(two, width=width, size=(lo, hi), max_lines=3).fits


def test_an_over_wide_word_breaks_after_a_hyphen_first_and_never_inside_a_cluster() -> None:
    m = _m("cause-and-effect-relationships")
    (word,) = m.words
    width = m.width(word, 72, "raw") * 0.7
    r = tf.fit(m, width=width, size=(72, 72), max_lines=3)
    assert r.lines[0].endswith("-") and "".join(r.lines) == word.text and all(w <= width for w in r.widths)
    # no hyphen: between clusters, as full as the line allows; each piece shaped on its own
    m = _m("Pneumonoultramicroscopicsilicovolcanoconiosis")
    (word,) = m.words
    width = m.width(word, 72, "raw") * 0.45
    r = tf.fit(m, width=width, size=(72, 72), max_lines=5)
    assert "".join(r.lines) == word.text and len(r.lines) == 3
    first = r.lines[0]
    assert m.width(word, 72, clusters=word.clusters[:len(first) + 1]) > width, "the first line is as full as it can be"
    em, spacings = m.shaped(first)
    assert r.widths[0] == pytest.approx(72 * (em * (1 + tf.SLACK) - 0.015 * spacings) + tf.BOX_PX, abs=1e-3)
    # a piece is shaped on its own, as the page shapes a line's part of a word: the A that ends "AVA" has no V after it
    # to kern against, as it has inside "AVAVAVAV"
    kerned = _m("AVAVAVAV")
    (word,) = kerned.words
    piece = word.clusters[:3]
    em, spacings = kerned.shaped("AVA")
    assert kerned.width(word, 100, "raw", piece) == pytest.approx(100 * (em - 0.015 * spacings))
    assert em > sum(c.em for c in piece) + 0.02  # not the sum of the clusters' shares of the whole word
    # a hyphen before a digit is not a break opportunity (UAX #14)
    m = _m("abcdefghij-1234567890")
    (word,) = m.words
    r = tf.fit(m, width=m.width(word, 72, "raw") * 0.75, size=(72, 72), max_lines=3)
    assert not r.lines[0].endswith("-")
    # Indic: pieces are whole clusters
    m = tf.measure("க்ஷேத்திரங்கள்கொண்டவை", fonts=B1, role="display", weight=700, language="ta-IN")
    (word,) = m.words
    r = tf.fit(m, width=m.width(word, 72, "optimistic") * 0.5, size=(72, 72), max_lines=4)
    assert len(r.lines) >= 2 and "".join(r.lines) == word.text
    clusters = [c.text for c in word.clusters]
    at = 0
    for line in r.lines:
        took: list[str] = []
        while "".join(took) != line:
            took.append(clusters[at])
            at += 1
            assert len("".join(took)) <= len(line), "a line ends inside a cluster"


def test_indic_mixed_and_combining_text_is_certified() -> None:
    """HarfBuzz shapes Indic text as the page does (the parity probe), so it is certified like Latin text; B2b gave it
    ``fits`` None (Pillow's advances could not bound the shaping)."""
    spec = SLOTS["title"]["landscape"]["default"]
    for text, language in (("மின்னோட்டம் என்றால் என்ன?", "ta-IN"), ("विद्युत धारा क्या है?", "hi-IN"),
                           ("Ohm விதி: V = IR", "ta-IN"), ("cafe\u0301", "en-IN"), ("x\u0304", "en-IN")):
        r = tf.fit_slot(text, spec, fonts=B1, language=language)
        assert r.reliable and r.fits is True, (text, r)
        assert spec.size[0] <= r.size <= spec.size[1] and r.lines, text
    long = " ".join(["மின்னோட்டம் என்றால் என்ன?"] * 6)
    r = tf.fit_slot(long, spec, fonts=B1, language="ta-IN")
    assert r.reliable and r.fits is False and r.size == spec.size[0], r
    # a word starting with a combining mark: HarfBuzz draws a dotted circle before it where it starts the page's text,
    # none after a space of the same text; its width depends on the slot's markup: an estimate only
    lone = tf.measure("\u0304x", fonts=B1, role="display", weight=700)
    assert [len(c.text) for c in lone.words[0].clusters] == [1, 1] and not lone.reliable and lone.uncovered == ()
    assert tf.fit_slot("\u0304x", spec, fonts=B1, language="en-IN").fits is None
    assert tf.measure("x \u0bbf", fonts=B1, role="display", weight=700, language="ta-IN").reliable is False


def test_fit_slot_reads_the_slot_and_a_narrower_box() -> None:
    kicker = SLOTS["kicker"]["landscape"]["default"]
    text = "Chapter 12: Electromagnetic induction and Faraday's law"
    full = tf.fit_slot(text, kicker, fonts=B1, language="en-IN")
    m = tf.measure(text, fonts=B1, role=kicker.face, weight=kicker.weight, language="en-IN",
                   tracking_em=kicker.tracking_em)
    assert full == tf.fit(m, width=kicker.w, size=kicker.size, max_lines=1)
    assert full.fits and full.width == kicker.w
    need = full.widths[0]
    assert tf.fit_slot(text, kicker, fonts=B1, language="en-IN", width=need + 0.5).fits
    narrow = tf.fit_slot(text, kicker, fonts=B1, language="en-IN", width=need - 0.5)
    assert narrow.fits is False and narrow.width == need - 0.5
    assert full.stored() == {"size": full.size, "lines": len(full.lines)}


def test_board_generic_titles_fit_and_overflow() -> None:
    for fmt in ("landscape", "portrait"):
        for variant in ("default", "quiz", "card"):
            spec = SLOTS["title"][fmt][variant]
            r = tf.fit_slot("How does a battery push current?", spec, fonts=B1, language="en-IN")
            assert r.fits is True and spec.size[0] <= r.size <= spec.size[1], (fmt, variant, r)
            long = "Why " + " ".join(["Warm Moist Monsoon Weather Worsens"] * 6)
            r = tf.fit_slot(long, spec, fonts=B1, language="en-IN")
            assert r.fits is False and r.size == spec.size[0], (fmt, variant, r)


# --- the B2b-H review (round 1): what the page does that B2b-H did not mirror -----------------------------------------


def test_a_cursive_run_takes_no_letter_spacing_but_after_a_space() -> None:
    """Chromium adds no letter-spacing inside a run of a cursive script (shape_result.cc ``IsCursiveScript``: Arabic,
    Hanifi Rohingya, Mandaic, Mongolian, N'Ko, Phags-pa, Syriac), except after a space. A word of digits and U+202F
    NARROW NO-BREAK SPACE is a Mongolian run on the page (review H-1: at the title's -0.015 em B2b-H subtracted the
    letter-spacing the page does not draw and certified titles 59 px past their box)."""
    def word(text: str, tracking: float = -0.015, role: str = "display", weight: int = 700) -> tf.Word:
        return tf.measure(text, fonts=B1, role=role, weight=weight, language="en-IN", tracking_em=tracking).words[0]

    for text in ("1\u202f000", "5\u202f%", "(1\u202f000)", "₹\u202f100", "1\u202f000\u202f000"):
        assert word(text).spacings == 0 and word(text).reliable, text
    assert word("1\u202f000\u00a0000").spacings == 1  # the no-break space is a space: it takes it, even there
    for text, n in (("10\u202fkm", 5), ("1\u202f000km", 7), ("a\u202fb", 3), ("1\u00a0000", 5), ("100", 3)):
        assert word(text).spacings == n, text  # a Latin letter makes the run Latin; no U+202F: Common
    assert sh.takes_letter_spacing("1", "Mong") is False and sh.takes_letter_spacing(" ", "Mong") is True
    assert sh.takes_letter_spacing("\u00a0", "Arab") is True and sh.takes_letter_spacing("\u200c", "Latn") is False
    assert sh.takes_letter_spacing("1", "Latn") and sh.CURSIVE_SCRIPTS == {"Arab", "Rohg", "Mand", "Mong", "Nkoo",
                                                                             "Phag", "Syrc"}


def test_a_title_of_spaced_numbers_is_not_certified_past_its_box() -> None:
    """The review's titles (board_generic, B1 faces, en-IN, U+202F groups): the page drew them on 3 lines at the size
    B2b-H certified for 2. Shaped as the page shapes them, the words are 3% wider, and Python's answer is no longer the
    one the page refuted (tests/determinism/test_textfit_page.py lays such titles out on the real page)."""
    n = "\u202f"
    portrait = f"reach from 300{n}000 1{n}000{n}000 to Earth 300{n}000"
    landscape = (f"take 300{n}000 eight from Earth light 149{n}600{n}000 1{n}000{n}000 6{n}371 300{n}000 across "
                 f"6{n}371 Why Earth about")
    for fmt, text, refuted in (("portrait", portrait, 56.0), ("landscape", landscape, 72.0)):
        spec = SLOTS["title"][fmt]["default"]
        r = tf.fit_slot(text, spec, fonts=B1, language="en-IN")
        assert r.reliable and not (r.fits and r.size >= refuted and len(r.lines) == 2), (fmt, r)
        m = tf.measure(text, fonts=B1, role=spec.face, weight=spec.weight, language="en-IN",
                       tracking_em=spec.tracking_em)
        for w in m.words:
            if n in w.text:
                assert w.spacings == 0 and m.width(w, 100, "raw") == pytest.approx(100 * w.em), w.text


def test_flow_text_takes_the_scripts_of_the_whole_text() -> None:
    """Flow text (a kicker, a subtitle) is one text node: its scripts are itemized over the whole text, so ``1 000``
    after ``about`` is Latin and letter-spaced, and ``23`` after ``1 000`` is Mongolian and not. Each word is read both
    ways, and the conservative width is the wider (the page widths measured on the real page with the stage's CSS,
    Inter 600 at 100 px: ``about 1 000 km`` 755.406 px at +0.02 em, 706.406 px at -0.015 em; ``1 000 23`` 405.906 /
    402.406 px)."""
    fonts = {**B1, "indic": "noto_sans"}
    for line, tracking, page in (("about 1\u202f000 km", 0.02, 755.406), ("about 1\u202f000 km", -0.015, 706.406),
                                 ("1\u202f000 23", 0.02, 405.906), ("1\u202f000 23", -0.015, 402.406),
                                 # an en space inside a Mongolian run takes no letter-spacing either (not a space to
                                 # Chromium's TreatAsSpace)
                                 ("1\u202f000\u2002000 7", 0.02, 585.5), ("1\u202f000\u2002000 7", -0.015, 582.0)):
        m = tf.measure(line, fonts=fonts, role="body", weight=600, language="en-IN", tracking_em=tracking)
        assert m.reliable
        flow = sum(100 * (em + tracking * n) for w in m.words for em, n in w.readings()[-1:])
        flow += sum(100 * (w.sep_em + tracking * (w.sep_flow_units or 0)) for w in m.words)
        assert abs(flow - page) < 0.02, (line, tracking, flow, page)
        assert sum(m.width(w, 100) + m.sep(w, 100) for w in m.words) >= page
        assert sum(m.width(w, 100, "optimistic") + m.sep(w, 100, "optimistic") for w in m.words) <= page
    about = tf.measure("about 1\u202f000 km", fonts=fonts, role="body", weight=600, tracking_em=0.02).words[1]
    assert (about.spacings, about.flow_spacings) == (0, 5) and len(about.readings()) == 2
    assert tf.measure("Ohm law", fonts=fonts, role="body", weight=600).words[0].readings() == ((
        tf.measure("Ohm", fonts=fonts, role="body", weight=600).words[0].em, 3),)


def test_a_piece_of_a_word_keeps_the_scripts_of_its_word() -> None:
    """The page itemizes a word once and shapes a line's part of it with that item's script: a piece ``000`` of a
    broken ``1 000 000 ...`` is still Mongolian (unspaced), not a Common run of its own."""
    m = tf.measure("1\u202f000\u202f000\u202f000", fonts=B1, role="display", weight=700, tracking_em=-0.015)
    (word,) = m.words
    piece = word.clusters[2:5]  # "000"
    assert "".join(c.text for c in piece) == "000"
    em, spacings = m.shaped("000")
    assert spacings == 3
    assert m.width(word, 100, "raw", piece) == pytest.approx(100 * em)  # no letter-spacing taken off
    assert m.width(word, 100, "conservative", piece) == pytest.approx(100 * em * (1 + tf.SLACK) + tf.BOX_PX)


@pytest.mark.parametrize("mark", ["\u200f", "\u061c", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066",
                                  "\u2067", "\u2068", "\u2069"])
def test_a_word_that_can_change_the_bidi_level_is_not_certified(mark: str) -> None:
    """The page resolves bidi before shaping: a right-to-left mark, an embedding, override or isolate inside a word
    can split it into runs shaped apart, which loses their kerning (review H-2: ``A`` U+200F ``V`` is drawn 141.219 px
    wide at 100 px, 9 px wider than B2b-H's certified width). Text fit does not model bidi, so such a word is an
    estimate; a left-to-right mark changes no level and stays certified (the page draws ``A`` U+200E ``V`` as ``AV``,
    132.203 px: :data:`_PAGE_WIDTHS`)."""
    spec = SLOTS["title"]["landscape"]["default"]
    m = tf.measure(f"Air A{mark}V", fonts=B1, role="display", weight=700, tracking_em=-0.015)
    assert m.words[0].reliable and not m.words[1].reliable and not m.reliable
    assert tf.splits_bidi(mark) and tf.fit_slot(f"Air A{mark}V", spec, fonts=B1).fits is None
    assert not tf.splits_bidi("\u200e") and tf.fit_slot("A\u200eV", spec, fonts=B1).fits is True


@pytest.mark.parametrize("sep", ["\u3000", "\u1680", "\u2028", "\u2029"])
def test_a_separator_no_vendored_face_draws_is_not_certified(sep: str) -> None:
    """Whitespace between words that no vendored face draws (the ideographic space, the Ogham space mark, the line
    and paragraph separators) is drawn with a system font, or breaks the line: an estimate (review HBH-R2: B2b-H added
    its 1 em guess and certified the text)."""
    spec = SLOTS["title"]["landscape"]["default"]
    m = tf.measure(f"a{sep}b", fonts=B1, role="body", weight=400)
    assert m.words[0].sep_uncovered == (sep,) and m.uncovered == (sep,) and not m.reliable
    r = tf.fit_slot(f"Ohm{sep}law", spec, fonts=B1)
    assert r.fits is None and r.estimate is True and r.uncovered == (sep,)
    covered = tf.measure("a\u2002b", fonts=B1, role="body", weight=400)  # an en space a vendored face draws
    assert covered.reliable and covered.words[0].sep_uncovered == ()


# Inter 700 with font-variant-numeric: tabular-nums (board_generic's ring, .dyn-ring-num), measured on the real page
# with the stage's CSS: at 100 px and at the ring's 40 px
_TABULAR = [("1", 64.65625, 25.859375), ("11", 129.296875, 51.71875), ("111", 193.953125, 77.578125),
            ("1111", 258.59375, 103.4375), ("10", 129.296875, 51.71875), ("2026", 258.59375, 103.4375)]


@pytest.mark.parametrize(("text", "at_100", "at_40"), _TABULAR)
def test_the_countdown_ring_is_measured_with_tabular_numerals(text: str, at_100: float, at_40: float) -> None:
    """The ring's number is drawn with ``font-variant-numeric: tabular-nums`` (``tnum``): its slot says so
    (``SlotSpec.tabular_nums``) and text fit shapes with it (reviews H-4 / HBH-R1: without it ``1111`` was certified
    at 69 px in the 96 px ring, where the page draws 103.4 px)."""
    ring = SLOTS["ring"]["landscape"]["default"]
    assert ring.tabular_nums and not SLOTS["title"]["landscape"]["default"].tabular_nums
    m = tf.measure(text, fonts=B1, role="body", weight=700, tabular=True)
    (word,) = m.words
    assert abs(m.width(word, 100, "raw") - at_100) < 0.05 and abs(m.width(word, 40, "raw") - at_40) < 0.05
    assert m.width(word, 40, "optimistic") <= at_40 <= m.width(word, 40)
    r = tf.fit_slot(text, ring, fonts=B1)
    assert r.reliable and r.fits is (at_40 <= ring.w), (text, r)
    assert sh.features(0.0, tabular=True) == {"kern": True, "liga": True, "clig": True, "tnum": True}


_FALLBACK_SHAPER = """
import json, os, sys
from aadhi.compose.dynamic import textfit as tf
B1 = {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}
out = [w.em for w in tf.measure("AVATAR office \\u0b95\\u0bcd\\u0bb7", fonts=B1, role="display", weight=700,
                                language="ta-IN").words]
print(json.dumps(out))
"""


def test_the_shaper_is_harfbuzzs_opentype_shaper_whatever_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """HarfBuzz reorders its shaper list from ``HB_SHAPER_LIST``; ``fallback`` skips GSUB and GPOS (no kerning, no
    ligatures, no conjuncts) and the Windows wheel's ``uniscribe`` ignores the ``unicode-range`` sub-font. Text fit
    names the ``ot`` shaper, Chromium's, so the widths are the same in any environment (review DEP-1); a shaper that
    fails is a FaceError, never another shaper's widths."""
    runs = []
    for value in (None, "fallback", "uniscribe,ot", "directwrite"):
        env = {k: v for k, v in os.environ.items() if k != "HB_SHAPER_LIST"}
        if value:
            env["HB_SHAPER_LIST"] = value
        out = subprocess.run([sys.executable, "-c", _FALLBACK_SHAPER], cwd=ROOT, capture_output=True, text=True,
                             env=env, timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-2000:]
        runs.append(json.loads(out.stdout.strip().splitlines()[-1]))
    assert runs[0] == runs[1] == runs[2] == runs[3], runs
    assert sh.SHAPERS == ("ot",)

    def refuse(*args: object, **kwargs: object) -> None:
        assert kwargs.get("shapers") == ["ot"]
        raise RuntimeError("all shapers failed")

    sh.clear_caches()
    monkeypatch.setattr(sh.hb, "shape", refuse)
    try:
        with pytest.raises(tf.FaceError, match="ot shaper"):
            sh.shape(tf.face_key(fc.faces_of("inter")[0], 400), "Ohm", "Latn", "en")
    finally:
        sh.clear_caches()


# --- determinism -----------------------------------------------------------------------------------------------------

_PROBE = """
import json
from aadhi.compose.dynamic import faces as fc, textfit as tf
from aadhi.compose.dynamic.templates import TEMPLATES
out = []
for fmt in ("landscape", "portrait"):
    for slot, variant in (("title", "default"), ("title", "card"), ("kicker", "quiz"), ("subtitle", "card")):
        spec = TEMPLATES["board_generic"].slots[slot][fmt][variant]
        for text, lang in (("Why Warm Moist Air Masses Make Monsoon Weather Worse Mid-Way", "en-IN"),
                           ("\\u0bae\\u0bbf\\u0ba9\\u0bcd\\u0ba9\\u0bcb\\u0b9f\\u0bcd\\u0b9f\\u0bae\\u0bcd Ohm", "ta-IN")):
            r = tf.fit_slot(text, spec, fonts=fc.resolver_fonts("poppins"), language=lang)
            out.append([r.size, list(r.lines), list(r.widths), r.fits, r.estimate])
print(json.dumps(out))
"""


def test_answers_are_deterministic_across_processes_hash_seeds_and_caches() -> None:
    runs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True, env=env,
                             timeout=120, check=False)
        assert out.returncode == 0, out.stderr[-2000:]
        runs.append(json.loads(out.stdout.strip().splitlines()[-1]))
    assert runs[0] == runs[1] == runs[2] and len(runs[0]) == 16
    assert all(fits is not None for *_, fits, _ in runs[0])  # the Tamil text is certified too
    spec = SLOTS["title"]["landscape"]["default"]
    text = "Why Warm Moist Air Masses Make Monsoon Weather Worse Mid-Way"
    before = tf.fit_slot(text, spec, fonts=B1)
    tf.clear_caches()
    assert tf.fit_slot(text, spec, fonts=B1) == before


def test_measuring_from_several_threads_gives_the_same_answers() -> None:
    """The API answers previews from a thread pool: the cached faces and fonts are shared (built under a lock)."""
    texts = ["How does a battery push current?", "மின்னோட்டம் என்றால் என்ன?", "विद्युत धारा क्या है?",
             "Ohm விதி: V = IR", "ŶA Rāma Łódź"]
    spec = SLOTS["title"]["portrait"]["default"]
    want = [tf.fit_slot(t, spec, fonts={**B2, "indic": "anek"}, language="ta-IN") for t in texts]
    tf.clear_caches()
    got: list[list[tf.FitResult]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            got.append([tf.fit_slot(t, spec, fonts={**B2, "indic": "anek"}, language="ta-IN") for t in texts])
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == [] and len(got) == 8 and all(g == want for g in got)
