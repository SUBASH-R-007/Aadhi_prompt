"""The measured kerning table (aadhi/compose/dynamic/kerning.py, kerning.json; B2b review F1, F2): what the fast suite
can check without Chromium. tests/determinism/test_kerning_table.py measures it again in Chromium.

* The committed table covers exactly the face table's Latin faces, in its canonical spelling, and holds what the
  review measured on the page: the positive pairs (Outfit ``f'`` and ``(j``, Atkinson ``qj``, Inter ``ïï``), the
  contexts no pair shows (Inter's ``x`` between digits drawn as ``×``), the contexts that chain along a run and are
  never bounded (Inter's ``*`` beside capitals and punctuation, its arrows, digits before a fraction slash: F1), and
  pair bounds below per pair, so a long word's lower bound is not a face-wide worst case times its pairs (F2); no
  pair, triple or open run holds the space.
* A run's bounds are its pairs' and triples' bounds summed; a run of four or more characters holding an open context,
  or two chain characters among four neighbouring characters, is not bounded; the open runs' cover
  (:func:`kerning.cover`) holds every open run; the near triples (:func:`kerning.near_triples`) hold a context's
  exceptions.
* Loading fails closed on a missing, damaged or stale table; :func:`kerning.covers` reports a measurement the table
  does not bound.
* The run-time path reads JSON only (no Chromium, no Playwright).
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import kerning as kn

ROOT = Path(__file__).resolve().parents[3]
EPS = kn.EPSILON_MILLI / 1000
INTER = tuple(f"inter-latin-{w}-normal.woff2" for w in (300, 400, 600, 700))


def test_the_table_covers_the_latin_faces_in_its_canonical_spelling() -> None:
    table = kn.load()
    assert set(table) == {kn.face_name(f) for f in kn.table_faces()}
    assert {fc.FAMILIES[f.family].role for f in kn.table_faces()} == {"display", "body", "mono"}
    raw = kn.TABLE_PATH.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert raw.decode("utf-8") == kn.render(data["faces"]) and b"\r" not in raw
    assert data["version"] == kn.VERSION == 3
    for name, face in table.items():
        assert all(v > EPS for v in face.positive.values()), name
        assert all(v < -EPS for v in face.negative.values()), name
        assert all(v != 0 for v in [*face.up.values(), *face.down.values()]), name
        assert all(face.up.get(t, 0.0) >= face.down.get(t, 0.0) for t in {*face.up, *face.down}), name
        seqs = [*face.positive, *face.negative, *face.up, *face.down, *face.open, *face.open4, *face.chain]
        assert not any(" " in s for s in seqs), name  # no face kerns the space or takes it into a context
        assert not any(kn.is_mark(ch) for s in [*face.up, *face.down, *face.open, *face.open4] for ch in s), name
        # every open four-character run is held by the cover once: no listed run holds a listed triple
        assert not any(q[:3] in face.open or q[1:] in face.open for q in face.open4), name
        assert face.chain == kn.chain_characters({*face.up, *face.down}), name


def test_the_review_measurements_are_in_the_table() -> None:
    table = kn.load()
    outfit = table["outfit-latin-700-normal.woff2"]
    # the pairs the review measured on the page (F1), and pairs that are not listed
    assert outfit.upper("f", "'") >= 0.07 and outfit.upper("(", "j") >= 0.098
    assert table["atkinson-hyperlegible-next-latin-700-normal.woff2"].upper("q", "j") >= 0.15
    assert table["inter-latin-400-normal.woff2"].upper("ï", "ï") >= 0.16
    assert outfit.upper("o", "o") == EPS and outfit.lower("o", "o") == -EPS
    # below, per pair (F2): AV, To, Yo are kerned in; a pair with no kerning is bounded by the epsilon
    assert outfit.lower("A", "V") <= -0.09 and outfit.lower("T", "o") <= -0.11 and outfit.lower("Y", "o") <= -0.14
    assert all(not table[kn.face_name(f)].positive and not table[kn.face_name(f)].negative
               for f in fc.faces_of("poppins"))  # Poppins' files carry no kerning
    # contexts no pair shows: Inter draws the x of 2x3 as a wider multiplication sign; 0x1 (a hexadecimal prefix) and
    # a letter's x are drawn as written
    for name in INTER:
        inter = table[name]
        assert all(inter.context(a, "x", b)[0] >= 0.09 for a in "123456789" for b in "0123456789"), name
        assert inter.context("0", "x", "1") == (0.0, 0.0) and inter.context("a", "x", "e") == (0.0, 0.0), name
        # contexts that chain along a run (F1): Inter's asterisk beside capitals and punctuation, its arrows, a digit
        # before a fraction slash
        for run in ("A*****", "*****A", "A********", "!********", "2********3", "4|~***", "**:<H0?", "8]*-*}*]",
                    "<==>", "12⁄34", "*:<H", "10x10", "20x9"):
            assert not inter.bounded(run), (name, run)
        for run in ("2x3x4", "(DNA)", "12:30", "X-ray", "COVID-19", "0x1F", "example", "Maxwell"):
            assert inter.bounded(run), (name, run)
        assert set("*:<-()") <= inter.chain and not any(ch.isalnum() for ch in inter.chain), name
    # Outfit's ffi ligature kerns with a w after it: that one run is open, the ligature itself is bounded
    assert "ffiw" in outfit.open4 and "ffi" not in outfit.open
    assert outfit.bounded("efficient") and outfit.bounded("office") and not outfit.bounded("ffiw")
    assert not outfit.bounded("ffff") and outfit.bounded("fff")  # three characters are measured whole
    assert not table["lexend-latin-700-normal.woff2"].bounded("10⁄2")
    # F2: a long word's lower bound is its own pairs', not the face's worst pair times its pairs
    for name in INTER:
        hi, lo = table[name].bounds("Supercalifragilisticexpialidocious")
        assert -0.3 < lo < 0 < hi, (name, lo, hi)


def test_a_runs_bounds_are_its_pairs_and_triples() -> None:
    face = kn.FaceKerning(positive={"ab": 0.02}, negative={"bc": -0.03}, up={"abc": 0.05, "bcd": -0.002},
                          down={"abc": 0.004, "bcd": -0.01}, open=frozenset({"xyz"}), open4=frozenset({"pqrs"}),
                          chain=frozenset("*:"))
    hi, lo = face.bounds("abcd")
    assert hi == pytest.approx(0.02 + EPS + EPS + 0.05 - 0.002)  # ab, bc (unlisted above), cd; abc, bcd
    assert lo == pytest.approx(-EPS - 0.03 - EPS + 0.004 - 0.01)
    assert face.bounds("a") == (0.0, 0.0) and face.bounds("") == (0.0, 0.0)
    assert face.bounds("ab") == pytest.approx((0.02, -EPS))
    # a run of four or more holding an open triple or run is not bounded; three characters are measured whole
    assert face.bounded("xyz") and face.bounded("abcd") and face.bounded("pqr")
    assert not face.bounded("axyz") and not face.bounded("xyza") and not face.bounded("pqrs")
    assert not face.bounded("apqrsb") and face.bounded("pqrt")
    # two chain characters among four neighbouring characters: never bounded; further apart, or in three, they are
    assert not face.bounded("a*:b") and not face.bounded("*ab:") and not face.bounded("ab*c:d")
    assert face.bounded("*abc:") and face.bounded("*:a") and face.bounded("a*bcd")


def test_chain_characters_and_near_triples() -> None:
    contexts = {"2x3", "4x5", "A*B", "C:D", "ffi", "a̲b"}
    assert kn.chain_characters(contexts) == {"*", ":", "̲"}  # not letters or digits
    near = kn.near_triples(contexts, {"*", ":"}, "a2345x*: ")
    # every context, and its middle between the characters its contexts have on either side
    assert {"2x3", "4x5", "2x5", "4x3", "A*B", "C:D", "ffi"} <= set(near)
    # a letter or digit middle: one of them on one side and any character on the other (Inter's 0x1 exception)
    assert {"ax3", "2xa", "axa"} - set(near) == {"axa"} and {"2x:", ":x5", "*x3"} <= set(near)
    # not for a chain character's middle; never two chain characters, a mark or the space
    assert "a*B" not in near and "f*B" not in near
    assert not any(sum(ch in "*:" for ch in t) >= 2 or " " in t or "̲" in t for t in near)
    assert near == sorted(set(near))


def test_the_cover_holds_every_open_run() -> None:
    outfit = ["ffff", "fffi", "fffj", "fffl", "ffiw", "ffjj", "tttt"]  # Outfit 700's open runs
    assert kn.cover(outfit) == (["fff"], ["ffiw", "ffjj", "tttt"])
    assert kn.cover(outfit, minimum=8) == ([], sorted(outfit))
    assert kn.cover([]) == ([], [])
    rng = random.Random(7)
    for _ in range(200):
        runs = {"".join(rng.choice("ab*-") for _ in range(4)) for _ in range(rng.randrange(1, 40))}
        triples, rest = kn.cover(sorted(runs))
        assert kn.cover(sorted(runs, reverse=True)) == (triples, rest)  # the input order does not matter
        assert set(rest) <= runs and triples == sorted(set(triples)) and rest == sorted(rest)
        for q in runs:
            assert q in rest or q[:3] in triples or q[1:] in triples, (runs, q)
        assert not any(q[:3] in triples or q[1:] in triples for q in rest)


def test_loading_fails_closed(tmp_path: Path) -> None:
    good = json.loads(kn.TABLE_PATH.read_text(encoding="utf-8"))
    name = "outfit-latin-700-normal.woff2"

    def broken(change) -> dict:
        data = json.loads(json.dumps(good))
        change(data)
        return data

    def row(**kw) -> object:
        return broken(lambda d: d["faces"][name].update(**kw))

    cases = {
        "missing": None,
        "not json": "{",
        "version": broken(lambda d: d.update(version=kn.VERSION + 1)),
        "version 2": broken(lambda d: d.update(version=2)),
        "epsilon": broken(lambda d: d.update(epsilon=kn.EPSILON_MILLI + 1)),
        "a face missing": broken(lambda d: d["faces"].pop(name)),
        "a face too many": broken(lambda d: d["faces"].update({"x.woff2": d["faces"][name]})),
        "a key missing": broken(lambda d: d["faces"][name].pop("open4")),
        "a key too many": row(most_negative=-100),
        "odd pairs": row(positive={"50": "abc"}),
        "at the epsilon": row(positive={str(kn.EPSILON_MILLI): "ab"}),
        "a negative positive": row(positive={"-5": "ab"}),
        "negative at the epsilon": row(negative={str(-kn.EPSILON_MILLI): "ab"}),
        "a positive negative": row(negative={"5": "ab"}),
        "spelling": row(positive={"050": "ab"}),
        "a pair twice": row(positive={"50": "abab"}),
        "a zero context": row(up={"0": "abc"}),
        "a broken triple": row(up={"50": "abcd"}),
        "a triple twice": row(down={"-50": "abc", "-51": "abc"}),
        "an open run of three": row(open4="abc"),
        "open unsorted": row(open="xyzabc"),
        "open twice": row(open="abcabc"),
        "open not a string": row(open=["abc"]),
        "open as characters": row(open=["a", "b", "c"]),
        "a group not a string": row(positive={"50": ["a", "b"]}),
        "groups not a mapping": row(up=["abc"]),
        "a row not a mapping": broken(lambda d: d["faces"].update({name: "row"})),
        "chain unsorted": row(chain=":*"),
        "chain twice": row(chain="**"),
        "no chain": broken(lambda d: d["faces"][name].pop("chain")),
    }
    for why, data in cases.items():
        path = tmp_path / why.replace(" ", "_") / "kerning.json"
        path.parent.mkdir()
        if data is not None:
            path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        with pytest.raises(kn.KerningError):
            kn.load(path)
            pytest.fail(why)
    ok = tmp_path / "ok"
    ok.mkdir()
    path = ok / "kerning.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    assert kn.load(path) == kn.load()


def test_covers_reports_what_the_table_does_not_bound() -> None:
    table = kn.load()
    name = "outfit-latin-700-normal.woff2"
    face = table[name]
    up = face.upper("(", "j") * 1000
    t = sorted(face.up)[0]
    hi, lo = (v * 1000 for v in face.context(*t))
    fine = kn.Measurement(pairs={"(j": (up - 0.2, up - 0.3), "oo": (0.9, -0.9)}, contexts={t: (hi - 0.1, lo + 0.1)},
                          runs={"ffiw", "ffff"})
    assert kn.covers(table, {name: fine}) == []
    inter = "inter-latin-400-normal.woff2"
    lo = table[inter].context("1", "x", "0")[1] * 1000
    assert kn.covers(table, {inter: kn.Measurement(contexts={"1x0": (lo, lo)})}) == []
    problems = {
        "a context below its bound": {inter: kn.Measurement(contexts={"1x0": (lo, lo - 1)})},
        "a pair wider than its bound": {name: kn.Measurement(pairs={"(j": (up + 1, up)})},
        "a pair narrower than its bound": {name: kn.Measurement(pairs={"oo": (0.0, -1.5)})},
        "an unlisted pair over the epsilon": {name: kn.Measurement(pairs={"oo": (1.5, 0.0)})},
        "an unlisted context": {name: kn.Measurement(contexts={"ooo": (0.3, 0.3)})},
        "an open run outside the cover": {name: kn.Measurement(runs={"oooo"})},
        "a chain character the table lacks": {name: kn.Measurement(contexts={"a*b": (0.0, 0.0)})},
        "a face the table lacks": {"x.woff2": kn.Measurement()},
    }
    for why, measured in problems.items():
        assert kn.covers(table, measured), why
    # a narrower space is not stored (it only narrows the page's line): not a problem
    assert kn.covers(table, {name: kn.Measurement(pairs={"A ": (0.0, -40.0)})}) == []


def test_the_run_time_path_reads_json_only() -> None:
    code = ("import sys; from aadhi.compose.dynamic import kerning as kn; kn.load(); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('playwright', 'PIL')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


def test_a_row_rounds_outwards_and_refuses_the_space() -> None:
    m = kn.Measurement(pairs={"ab": (1.95, 1.0), "cd": (0.85, -0.85), "ef": (0.95, -0.95), "gh": (37.0, -12.34),
                              "A ": (0.5, -30.0)},
                       contexts={"abc": (113.6, 0.2), "def": (-0.05, -0.05), "ghi": (-1.2, -3.0), "jkl": (0.9, -0.9)},
                       runs={"ffff", "fffi", "fffj", "fffl", "ffiw", "ffjj", "tttt"})
    assert kn._row("x.woff2", m) == {
        "positive": {"2": "ef", "3": "ab", "38": "gh"}, "negative": {"-13": "gh", "-2": "ef"},
        "up": {"-1": "ghi", "1": "defjkl", "114": "abc"}, "down": {"-1": "defjkl", "-4": "ghi"},
        "open": "fff", "open4": "ffiwffjjtttt", "chain": ""}
    assert kn._row("x.woff2", kn.Measurement(contexts={"A*B": (5.0, 5.0), "1:2": (3.0, 3.0)}))["chain"] == "*:"
    assert list(kn._row("x.woff2", m)) == sorted(kn.ROW_KEYS)
    assert kn.parse_row(kn._row("x.woff2", m)).bounds("abc") == pytest.approx((0.003 + EPS + 0.114, -EPS - EPS + 0.0))
    for pairs, contexts, runs, match in (({"a ": (5.0, 0.0)}, {}, set(), "kerns the space"),
                                         ({}, {"a b": (0.2, 0.2)}, set(), "into a context"),
                                         ({}, {}, {"ab c"}, "into an open run")):
        with pytest.raises(kn.KerningError, match=match):
            kn._row("x.woff2", kn.Measurement(pairs=pairs, contexts=contexts, runs=runs))
