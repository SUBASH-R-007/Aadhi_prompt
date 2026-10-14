"""Measured text fit (aadhi.compose.dynamic.textfit; design §4.6, R-29; plan B2 §1.11, §2 "Fonts").

* **Every vendored face loads** through text fit's own loader (Pillow FreeType reading the WOFF2 file, the basic
  layout, a variable face's axes checked against the face table and set), and its advance widths are pinned: a
  FreeType or Pillow that measures differently (the Docker image's Debian build, the CI runner's) fails here, before a
  timeline's text fit could change (the plan's "CI tests load every face and pin advance widths"; the Pillow WOFF2
  check of design §4.6 runs wherever this suite does, the container's fast suite included).
* **Fail closed**: an unreadable face, or one whose axes are not the table's, is a FaceError, never a fallback.
* The page's text model: grapheme clusters (Indic conjuncts whole), the word units of ``splitWords``, CSS whitespace,
  letter-spacing per cluster counted so it never under-estimates, faces per cluster in the stage's stack order (CSS
  weight matching, last-declared range first, the cmap really holding the glyph), runs measured per face.
* The conservative widths (4% / 8% slack and the kerning floor), the sizes the page tries, the line limit and the
  preferred line count, the words the page may break (certainly, never, or either way at the minimum), the breaks of
  an over-wide word (after a hyphen first, never inside a cluster).
* What is certified: Latin text gets ``fits`` True / False; text with an Indic cluster, a combining sequence or a
  character no vendored face draws gets None (an estimate only).
* Deterministic: the same answers after the caches are dropped and under other hash seeds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import ImageFont

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import textfit as tf
from aadhi.compose.dynamic.templates import TEMPLATES
from tests.compose.dynamic.helpers import missing

ROOT = Path(__file__).resolve().parents[3]
B1 = {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}
B2 = fc.resolver_fonts("outfit")
SLOTS = TEMPLATES["board_generic"].slots


@pytest.fixture(autouse=True)
def _vendored() -> None:
    if not all(fc.web_path(r).is_file() for r in fc.FACES):
        missing("web/vendor is not vendored (npm ci): the faces text fit measures with")


# --- every face, pinned ----------------------------------------------------------------------------------------------

SAMPLES = {"latin": "Hamburgefonstiv 0123", "latin-ext": "ĀăĆčĐęĞłŃőřŚşŤůŹž", "tamil": "தமிழ் மின்னோட்டம்",
           "devanagari": "विद्युत धारा", "telugu": "విద్యుత్ ప్రవాహం", "kannada": "ವಿದ್ಯುತ್ ಪ್ರವಾಹ",
           "malayalam": "വൈദ്യുത പ്രവാഹം"}
# Pillow's advance of the face's sample at REF_PX (2048 px: whole pixels), at the face's weight; a variable face at
# wght 400 and 700. Measured with Pillow 12.3.0 / FreeType 2.14.3 (no raqm) on the dev machine.
PINNED = {
    "outfit-latin-300-normal.woff2": (20616,),
    "outfit-latin-ext-300-normal.woff2": (19983,),
    "outfit-latin-400-normal.woff2": (20702,),
    "outfit-latin-ext-400-normal.woff2": (20010,),
    "outfit-latin-700-normal.woff2": (21512,),
    "outfit-latin-ext-700-normal.woff2": (20627,),
    "outfit-latin-900-normal.woff2": (22234,),
    "outfit-latin-ext-900-normal.woff2": (21188,),
    "poppins-latin-400-normal.woff2": (22657,),
    "poppins-latin-ext-400-normal.woff2": (20695,),
    "poppins-latin-600-normal.woff2": (23252,),
    "poppins-latin-ext-600-normal.woff2": (21286,),
    "poppins-latin-700-normal.woff2": (23586,),
    "poppins-latin-ext-700-normal.woff2": (21631,),
    "poppins-latin-900-normal.woff2": (24139,),
    "poppins-latin-ext-900-normal.woff2": (22211,),
    "lexend-latin-400-normal.woff2": (22463,),
    "lexend-latin-ext-400-normal.woff2": (20764,),
    "lexend-latin-600-normal.woff2": (23332,),
    "lexend-latin-ext-600-normal.woff2": (21502,),
    "lexend-latin-700-normal.woff2": (23739,),
    "lexend-latin-ext-700-normal.woff2": (21854,),
    "lexend-latin-900-normal.woff2": (24689,),
    "lexend-latin-ext-900-normal.woff2": (22665,),
    "inter-latin-300-normal.woff2": (21579,),
    "inter-latin-ext-300-normal.woff2": (20567,),
    "inter-latin-400-normal.woff2": (21961,),
    "inter-latin-ext-400-normal.woff2": (20841,),
    "inter-latin-600-normal.woff2": (22578,),
    "inter-latin-ext-600-normal.woff2": (21361,),
    "inter-latin-700-normal.woff2": (22889,),
    "inter-latin-ext-700-normal.woff2": (21620,),
    "atkinson-hyperlegible-next-latin-400-normal.woff2": (20469,),
    "atkinson-hyperlegible-next-latin-ext-400-normal.woff2": (19078,),
    "atkinson-hyperlegible-next-latin-600-normal.woff2": (21395,),
    "atkinson-hyperlegible-next-latin-ext-600-normal.woff2": (19799,),
    "atkinson-hyperlegible-next-latin-700-normal.woff2": (21804,),
    "atkinson-hyperlegible-next-latin-ext-700-normal.woff2": (20109,),
    "jetbrains-mono-latin-400-normal.woff2": (24580,),
    "jetbrains-mono-latin-ext-400-normal.woff2": (20893,),
    "jetbrains-mono-latin-700-normal.woff2": (24580,),
    "jetbrains-mono-latin-ext-700-normal.woff2": (20893,),
    "noto-sans-tamil-latin-400-normal.woff2": (22083,),
    "noto-sans-tamil-latin-ext-400-normal.woff2": (19689,),
    "noto-sans-tamil-tamil-400-normal.woff2": (21308,),
    "noto-sans-tamil-latin-700-normal.woff2": (23211,),
    "noto-sans-tamil-latin-ext-700-normal.woff2": (20568,),
    "noto-sans-tamil-tamil-700-normal.woff2": (23366,),
    "noto-sans-devanagari-devanagari-400-normal.woff2": (8799,),
    "noto-sans-devanagari-latin-400-normal.woff2": (21911,),
    "noto-sans-devanagari-latin-ext-400-normal.woff2": (19689,),
    "noto-sans-devanagari-devanagari-700-normal.woff2": (9610,),
    "noto-sans-devanagari-latin-700-normal.woff2": (23074,),
    "noto-sans-devanagari-latin-ext-700-normal.woff2": (20745,),
    "noto-sans-telugu-latin-400-normal.woff2": (22131,),
    "noto-sans-telugu-latin-ext-400-normal.woff2": (19689,),
    "noto-sans-telugu-telugu-400-normal.woff2": (16235,),
    "noto-sans-telugu-latin-700-normal.woff2": (23259,),
    "noto-sans-telugu-latin-ext-700-normal.woff2": (20568,),
    "noto-sans-telugu-telugu-700-normal.woff2": (17452,),
    "noto-sans-kannada-kannada-400-normal.woff2": (18273,),
    "noto-sans-kannada-latin-400-normal.woff2": (22141,),
    "noto-sans-kannada-latin-ext-400-normal.woff2": (19689,),
    "noto-sans-kannada-kannada-700-normal.woff2": (19781,),
    "noto-sans-kannada-latin-700-normal.woff2": (22141,),
    "noto-sans-kannada-latin-ext-700-normal.woff2": (19689,),
    "noto-sans-malayalam-latin-400-normal.woff2": (22184,),
    "noto-sans-malayalam-latin-ext-400-normal.woff2": (19689,),
    "noto-sans-malayalam-malayalam-400-normal.woff2": (21417,),
    "noto-sans-malayalam-latin-700-normal.woff2": (23312,),
    "noto-sans-malayalam-latin-ext-700-normal.woff2": (20568,),
    "noto-sans-malayalam-malayalam-700-normal.woff2": (23678,),
    "anek-tamil-tamil-wght-normal.woff2": (21023, 21940),
    "anek-devanagari-devanagari-wght-normal.woff2": (9044, 9567),
    "anek-telugu-telugu-wght-normal.woff2": (17572, 18277),
    "anek-kannada-kannada-wght-normal.woff2": (18244, 19394),
    "anek-malayalam-malayalam-wght-normal.woff2": (20360, 21360),
}



def _weights(face: fc.Face) -> tuple[int, ...]:
    lo, hi = face.weight
    return (lo,) if lo == hi else (400, 700)


def test_every_vendored_face_loads_with_the_basic_layout_and_measures_as_pinned() -> None:
    assert {r.file.rsplit("/", 1)[1] for r in fc.FACES} == set(PINNED), "one pin per vendored face file"
    for face in fc.FACES:
        got = []
        for w in _weights(face):
            key = tf.face_key(face, w)
            font = tf.load_face(key)
            assert font.layout_engine == ImageFont.Layout.BASIC, face.file  # never raqm (platform-dependent)
            assert font.size == tf.REF_PX
            sample = SAMPLES[face.subset]
            assert all(tf.has_glyph(key, ch) for ch in sample if ch != " "), face.file
            got.append(int(font.getlength(sample)))
        assert tuple(got) == PINNED[face.file.rsplit("/", 1)[1]], face.file


def test_a_variable_face_is_measured_at_the_weight_the_page_asks_for() -> None:
    face = fc.faces_of("anek_tamil")[0]
    sample = SAMPLES["tamil"]
    at = {w: tf.load_face(tf.face_key(face, w)).getlength(sample) for w in (100, 400, 500, 700, 800)}
    file_default = ImageFont.truetype(str(fc.web_path(face)), tf.REF_PX, layout_engine=ImageFont.Layout.BASIC)
    assert file_default.getlength(sample) == at[500] != at[400], "the file's default is wght 500, not the page's 400"
    assert at[100] < at[400] < at[700] < at[800]
    # CSS clamps a weight outside the face's descriptor range to it
    assert tf.face_key(face, 900).wght == 800 and tf.face_key(face, 50).wght == 100
    assert tf.face_key(fc.faces_of("inter")[0], 700).wght is None  # a static face has no axis to set


def test_a_face_that_cannot_be_read_or_is_not_the_tables_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(tf.FaceError, match="cannot read"):
        tf.load_face(tf.FaceKey(str(tmp_path / "missing.woff2"), None))
    broken = tmp_path / "broken.woff2"
    broken.write_bytes(b"wOF2" + b"\0" * 64)
    with pytest.raises(tf.FaceError, match="cannot read"):
        tf.load_face(tf.FaceKey(str(broken), None))
    anek = fc.faces_of("anek_tamil")[0]
    static = fc.faces_of("inter")[0]
    axes = fc.FAMILIES["anek_tamil"].axes
    with pytest.raises(tf.FaceError, match="variable axes"):  # a static file the table calls variable
        tf.load_face(tf.FaceKey(str(fc.web_path(static)), 400, axes))
    with pytest.raises(tf.FaceError, match="variable axes"):  # a variable file the table calls static
        tf.load_face(tf.FaceKey(str(fc.web_path(anek)), None))
    with pytest.raises(tf.FaceError, match="variable axes"):  # axes that differ from the file's
        tf.load_face(tf.FaceKey(str(fc.web_path(anek)), 400, (fc.Axis("wght", 100, 900, 400),)))
    with pytest.raises(tf.FaceError, match="set weight"):
        tf.load_face(tf.FaceKey(str(fc.web_path(anek)), None, axes))
    # and through measure(): a face file gone from web/vendor is an error, never another face
    with pytest.raises(tf.FaceError):
        tf.measure("Ohm", fonts=B1, role="display", weight=700, web_dir=tmp_path)


def test_glyph_presence_is_the_files_cmap_not_the_css_range() -> None:
    inter = tf.face_key(fc.face_for("inter", 400, ord("A")), 400)
    tamil = tf.face_key(fc.face_for("noto_sans_tamil", 400, ord("க")), 400)
    poppins_ext = tf.face_key(fc.face_for("poppins", 700, ord("ħ")), 700)
    assert tf.has_glyph(inter, "A") and tf.has_glyph(inter, " ") and tf.has_glyph(tamil, "க")
    assert not tf.has_glyph(inter, "π") and not tf.has_glyph(inter, "க") and not tf.has_glyph(inter, tf.NOTDEF_PROBE)
    # Poppins' latin-ext face is declared for U+0127 (its unicode-range) but its file has no glyph for it: the page
    # takes the next family of the stack (the body face), and so does text fit
    assert fc.face_for("poppins", 700, ord("ħ")) is not None and not tf.has_glyph(poppins_ext, "ħ")
    m = tf.measure("ħ", fonts={**B2, "display": "poppins"}, role="display", weight=700)
    (cluster,) = m.words[0].clusters
    assert cluster.key is not None and "inter-latin-ext-700" in cluster.key.path, cluster


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
    ("10\u00a0V and 3\u202fA, 5\u2007m", (("10\u00a0V", " "), ("and", " "), ("3\u202fA,", " "), ("5\u2007m", ""))),
    ("foo\u200bbar", (("foo", "\u200b"), ("bar", ""))),  # a zero-width space: a break of no width
    ("a\ufeffb\u3000c", (("a", "\ufeff"), ("b", "\u3000"), ("c", ""))),  # JS \s: the BOM, the ideographic space
    ("", ()),
    ("   ", ()),
])
def test_word_units_are_split_words(text: str, words: tuple[tuple[str, str], ...]) -> None:
    assert tf.words(text) == words


def test_letter_spacing_is_counted_so_it_never_under_estimates() -> None:
    clusters = tf.graphemes("धारा\u200d")  # 2 clusters, 4 drawn code points (the ZWJ is not drawn)
    assert tf.tracking_count(clusters, -0.015) == 2  # negative: once per cluster (what Chromium does)
    assert tf.tracking_count(clusters, 0.02) == 4  # positive: once per code point, never fewer than the page
    assert tf.tracking_count(tf.graphemes("Ohm"), 0.02) == tf.tracking_count(tf.graphemes("Ohm"), -0.015) == 3


def test_default_ignorable_characters_take_no_width() -> None:
    for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad\ufe0f\u034f":
        assert tf.ignorable(ch), hex(ord(ch))
    for ch in "a \u093e\u0301":
        assert not tf.ignorable(ch)
    plain = tf.measure("Ohm", fonts=B1, role="display", weight=700)
    joined = tf.measure("O\u00adh\u2060m", fonts=B1, role="display", weight=700)
    assert joined.words[0].em == plain.words[0].em and joined.reliable


def _files(text: str, fonts: dict, language: str = "en-IN", weight: int = 700, role: str = "display") -> list:
    m = tf.measure(text, fonts=fonts, role=role, weight=weight, language=language)
    return [c.key.path.replace("\\", "/").rsplit("/", 1)[1] if c.key else None for w in m.words for c in w.clusters]


def test_each_cluster_is_drawn_by_the_first_family_of_the_stage_stack_that_has_it() -> None:
    assert _files("Oł", B1) == ["outfit-latin-700-normal.woff2", "outfit-latin-ext-700-normal.woff2"]
    # the B1 form draws any display id but outfit / inter as Outfit (faces.stage_roles), the B2 form names it
    assert _files("O", {**B1, "display": "poppins"}) == ["outfit-latin-700-normal.woff2"]
    assert _files("O", {**B2, "display": "poppins"}) == ["poppins-latin-700-normal.woff2"]
    # CSS weight matching per family: Outfit has no 600 face (700 is used), Inter has one; Noto Sans 600 -> 700
    # U+0304 is in the ranges of both Latin faces of a weight: the last declared (latin-ext) is tried first (CSS
    # Fonts 4 §4.5); with a base only the latin face covers, the latin face draws the cluster
    assert _files("\u0304", B1) == ["outfit-latin-ext-700-normal.woff2"]
    assert _files("x\u0304", B1) == ["outfit-latin-700-normal.woff2"]
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


def test_a_character_no_vendored_face_draws_is_uncovered_and_never_certified() -> None:
    m = tf.measure("Area π r²", fonts=B1, role="display", weight=700)
    assert m.uncovered == ("π",) and not m.reliable
    r = tf.fit(m, width=1728, size=(72, 96), max_lines=2)
    assert r.fits is None and r.estimate is True and r.uncovered == ("π",)
    (cluster,) = [c for w in m.words for c in w.clusters if c.uncovered]
    assert cluster.em == tf.UNCOVERED_EM and cluster.key is None
    with pytest.raises(ValueError, match="no font stack"):
        tf.measure("x", fonts=B1, role="indic", weight=400)


def test_runs_of_one_face_are_measured_together_and_scale_linearly() -> None:
    m = tf.measure("Hamburgefonstiv", fonts=B1, role="display", weight=700)
    (word,) = m.words
    key = word.clusters[0].key
    assert word.em == tf.advance_em(key, "Hamburgefonstiv")
    assert m.width(word, 96, "raw") == pytest.approx(2 * m.width(word, 48, "raw"))
    assert m.width(word, 96, "raw") == pytest.approx(96 * word.em)


def test_pillow_applies_no_kerning_to_the_vendored_faces() -> None:
    # none of the vendored WOFF2 files has a legacy 'kern' table (their kerning is GPOS, which only shaping applies),
    # so a run measures as the sum of its characters: every kerning difference is the slack's and the floor's to cover
    for family in ("outfit", "poppins", "lexend", "inter", "atkinson_hyperlegible_next", "jetbrains_mono"):
        for weight in (400, 700):
            fonts = {"display": family, "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
            role = "mono" if family == "jetbrains_mono" else "display"
            m = tf.measure("AVATAR WAVE To f) T.", fonts=fonts, role=role, weight=weight)
            assert m.stack[0] == family
            for w in m.words:
                assert w.em == pytest.approx(sum(c.em for c in w.clusters), abs=1e-12), (family, weight, w.text)


def test_conservative_widths_add_the_slack_and_the_kerning_floor() -> None:
    m = tf.measure("Typography", fonts=B1, role="display", weight=700, tracking_em=-0.015)
    (word,) = m.words
    track = -0.015 * 10
    assert m.width(word, 100, "raw") == pytest.approx(100 * (word.em + track))
    assert m.width(word, 100) == pytest.approx(100 * (word.em * 1.04 + tf.KERN_FLOOR_EM + track))
    assert m.width(word, 100, "optimistic") == pytest.approx(100 * (word.em * 0.96 + track))
    assert word.slack == tf.SLACK_LATIN == 0.04 and not word.indic
    # the floor is the measured worst positive kerning beyond 4% (0.046 em, tests/determinism/test_textfit_page.py)
    assert tf.KERN_FLOOR_EM == 0.06
    indic = tf.measure("மின்", fonts=B1, role="display", weight=700, language="ta-IN").words[0]
    assert indic.slack == tf.SLACK_INDIC == 0.08 and indic.indic and not indic.reliable


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
        assert pos.sep(pos.words[i], 100) == pytest.approx(100 * (plain.words[i].sep_em + 0.02))
        assert neg.sep(neg.words[i], 100) == pytest.approx(100 * plain.words[i].sep_em)  # never under-estimated
    zw = tf.measure("a\u200bb", fonts=B1, role="body", weight=600, tracking_em=0.02)
    assert zw.words[0].sep_units == 1 and zw.sep(zw.words[0], 100) == pytest.approx(2.0)
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
    m = _m("Series and parallel circuits")
    one = tf.fit(m, width=spec.w, size=spec.size, max_lines=1)
    two = tf.fit(m, width=spec.w, size=spec.size, max_lines=2)
    both = tf.fit(m, width=spec.w, size=spec.size, max_lines=2, prefer_lines=1)
    assert one.fits and two.fits and two.size > one.size and len(two.lines) == 2
    assert (both.size, both.lines) == (one.size, one.lines), "one line at a smaller size wins over two larger"
    longer = _m("Series and parallel circuits compared side by side")
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
    r = tf.fit(m, width=width, size=(lo, hi), max_lines=2)
    assert r.fits and r.size == hi and len(r.lines) == 2 and "".join(r.lines) == "Electromagnetism"
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
    width = m.width(word, 72, "optimistic") * 0.7
    r = tf.fit(m, width=width, size=(72, 72), max_lines=3)
    assert r.lines[0].endswith("-") and "".join(r.lines) == word.text and all(w <= width for w in r.widths)
    # no hyphen: between clusters, as full as the line allows
    m = _m("Pneumonoultramicroscopicsilicovolcanoconiosis")
    (word,) = m.words
    width = m.width(word, 72, "optimistic") * 0.45
    r = tf.fit(m, width=width, size=(72, 72), max_lines=5)
    assert "".join(r.lines) == word.text and len(r.lines) == 3
    first = r.lines[0]
    assert m.width(word, 72, clusters=word.clusters[:len(first) + 1]) > width, "the first line is as full as it can be"
    # a hyphen before a digit is not a break opportunity (UAX #14)
    m = _m("abcdefghij-1234567890")
    (word,) = m.words
    r = tf.fit(m, width=m.width(word, 72, "optimistic") * 0.75, size=(72, 72), max_lines=3)
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


def test_indic_and_combining_text_is_estimated_never_certified() -> None:
    spec = SLOTS["title"]["landscape"]["default"]
    for text, language in (("மின்னோட்டம் என்றால் என்ன?", "ta-IN"), ("विद्युत धारा क्या है?", "hi-IN"),
                           ("Ohm விதி: V = IR", "ta-IN"), ("cafe\u0301", "en-IN")):
        r = tf.fit_slot(text, spec, fonts=B1, language=language)
        assert r.fits is None and not r.reliable and r.estimate in (True, False), text
        assert spec.size[0] <= r.size <= spec.size[1] and r.lines, text
    # a precomposed accent is one drawn character: certified
    assert tf.fit_slot("caf\u00e9", spec, fonts=B1, language="en-IN").fits is True
    # Indic letters with no sign (one code point each) are still drawn by an Indic face: not certified either
    bare = tf.measure("கடல", fonts=B1, role="display", weight=700, language="ta-IN")
    assert [len(c.drawn) for c in bare.words[0].clusters] == [1, 1, 1] and not bare.reliable
    # a combining sequence a vendored Latin face draws (U+0304 is in its range): covered, but shaped, not certified
    macron = tf.measure("x\u0304", fonts=B1, role="display", weight=700)
    assert macron.uncovered == () and not macron.words[0].indic and not macron.reliable


def test_fit_slot_reads_the_slot_and_a_narrower_box() -> None:
    kicker = SLOTS["kicker"]["landscape"]["default"]
    text = "Chapter 12: Electromagnetic induction and Faraday's law"
    full = tf.fit_slot(text, kicker, fonts=B1, language="en-IN")
    m = tf.measure(text, fonts=B1, role=kicker.face, weight=kicker.weight, tracking_em=kicker.tracking_em)
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
                           ("\u0bae\u0bbf\u0ba9\u0bcd\u0ba9\u0bcb\u0b9f\u0bcd\u0b9f\u0bae\u0bcd Ohm", "ta-IN")):
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
    spec = SLOTS["title"]["landscape"]["default"]
    text = "Why Warm Moist Air Masses Make Monsoon Weather Worse Mid-Way"
    before = tf.fit_slot(text, spec, fonts=B1)
    tf.clear_caches()
    assert tf.fit_slot(text, spec, fonts=B1) == before
