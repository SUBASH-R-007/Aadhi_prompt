"""The committed kerning table still bounds what Chromium draws (aadhi/compose/dynamic/kerning.py; B2b review F1, F2).

* **The table holds.** Every Latin face is measured again in real Chromium, pair by pair: no pair may be drawn wider or
  narrower than the table's bounds for it. The triples, the chain characters and the open four-character runs of one
  ``latin`` face of each family (the characters lesson text is made of: digits, ASCII punctuation, the letters; Inter
  at 400, the body text's weight) are measured again: no context may add more or less than the table bounds it by,
  every chain character must be the table's and every open run must be in the table's cover. The other faces'
  triples (Inter's ``latin-ext`` alone take most of the 90 minutes a full measurement takes) are measured by the
  generator and its ``--check`` only.
* **What the table leaves unmeasured does not happen in the faces' text.** The table bounds a run by its pairs and
  triples and lists where four characters interact beyond them, or may (``kerning``'s module docstring: what remains
  assumed). Random runs of 4-12 characters of every face: around each character the face takes into a context, of
  the characters its contexts hold beside it (where the review found ``A*****`` drawn 0.24 em wider than its pairs
  and triples add up to, ``*:<H`` 0.144 em and ``10x1`` 0.116 em: F1, rounds 1 and 2), and of its context characters,
  letters and digits, and the review's own words: every run the table bounds is drawn within its bounds, both sides
  (the lower bound decides that the page certainly breaks an over-wide word: F2).

On the machine the table was made on the measurement is the table itself (``python -m aadhi.compose.dynamic.kerning
--check``); on another platform it may differ only in the conservative direction (a stored bound is never inside a
measured value).
"""

from __future__ import annotations

import asyncio
import base64
import os
import random
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import kerning as kn
from tests.compose.dynamic.helpers import missing

pytestmark = pytest.mark.slow
# The full re-measurement (every Latin face's pairs, the latin triples and 6000 random runs per face) takes about 8
# minutes, so it runs only on demand, like the generator's own --check (owner's rule: hour- and many-minute
# measurements stay out of CI, which runs `-m slow` in the container job): set AADHI_KERNING_REMEASURE=1.
REMEASURE_ENV = "AADHI_KERNING_REMEASURE"
PROBES = 6000  # random runs per face (each measured with and without letter-spacing)
SEED = 20261014
TRIPLES_OF = ("outfit-latin-700-normal.woff2", "poppins-latin-700-normal.woff2", "lexend-latin-700-normal.woff2",
              "inter-latin-400-normal.woff2", "atkinson-hyperlegible-next-latin-400-normal.woff2",
              "jetbrains-mono-latin-400-normal.woff2")
# the review's runs (Inter 400 and 600 drew them wider than the pairs and triples of the first fix round added up to),
# and words that must stay bounded
REVIEW_RUNS = ("A*****", "*****A", "A********", "!********", "2********3", "4|~***", "**:<H0?", "8]*-*}*]",
               "*:<H", "10x10", "20x9", "2x3x4x5", "**A**", "A**B**C", "*(A)*", "<==>", "<=>", "==>", "<->", "12⁄34",
               "1⁄2", "Supercalifragilisticexpialidocious", "AVAVAVAVAVAVAVAVAVAV", "efficient", "office", "ffiw",
               "fffi", "X-ray", "COVID-19", "12:30", "(DNA)", "example", "Maxwell")

_EXCESS_JS = """async ([b64, runs, spacing]) => {""" + kn._SETUP_JS + """
  try {
    return runs.map((r) => w(r) - [...r].reduce((s, ch) => s + w(ch), 0));
  } finally {
    document.fonts.delete(face);
  }
}"""


def _faces() -> list[fc.Face]:
    faces = list(kn.table_faces())
    if not all(fc.web_path(r).is_file() for r in faces):
        missing("web/vendor is not vendored (npm ci): the faces the table is measured on")
    return faces


@pytest.mark.skipif(os.environ.get(REMEASURE_ENV) != "1", reason=f"on demand: set {REMEASURE_ENV}=1 (about 8 minutes)")
def test_the_table_bounds_every_pair_and_the_triples_and_runs_chromium_draws() -> None:
    faces = _faces()
    table = kn.load()
    assert set(TRIPLES_OF) <= set(table)
    measured = kn.measure(triples_of=TRIPLES_OF)
    assert set(measured) == set(table)
    # Inter's and Lexend's chains, Outfit's ligatures were measured; the faces without contexts have none
    assert all(measured[n].runs and measured[n].contexts for n in TRIPLES_OF if n.startswith(("outfit-", "lexend-",
                                                                                              "inter-")))
    assert measured["inter-latin-400-normal.woff2"].chain and measured["lexend-latin-700-normal.woff2"].chain == "⁄"
    assert not any(measured[n].contexts for n in TRIPLES_OF if n.startswith(("poppins-", "atkinson-", "jetbrains-")))
    assert kn.covers(table, measured) == []
    assert len(faces) == len(table)


def _probes(face: fc.Face, k: kn.FaceKerning, rng: random.Random) -> list[str]:
    chars = [ch for ch in kn.characters(face) if ch != " " and not kn.is_mark(ch)]
    contexts = [*k.up, *k.down]
    hot = sorted({ch for s in [*contexts, *k.open, *k.open4, *k.chain] for ch in s}) or chars
    words = [ch for ch in chars if ch.isalnum()]
    beside: dict[str, set[str]] = {}
    for a, b, c in contexts:
        beside.setdefault(b, set()).update((a, c))
    runs: set[str] = set()
    for b, near in sorted(beside.items()):  # around each character the face takes into a context
        pool = sorted(near)
        for _ in range(max(40, PROBES // (2 * len(beside)))):
            runs.add("".join(b if rng.random() < 0.3 else rng.choice(pool) if rng.random() < 0.8 else rng.choice(chars)
                             for _ in range(rng.randint(4, 7))))
    while len(runs) < PROBES:
        pick = rng.random()
        pool = hot if pick < 0.6 else words if pick < 0.8 else chars
        runs.add("".join(rng.choice(pool) if rng.random() < 0.8 else rng.choice(chars)
                         for _ in range(rng.randint(4, 12))))
    return sorted(runs | {r for r in REVIEW_RUNS if all(ch in chars for ch in r)})


async def _excess(faces: list[tuple[fc.Face, list[str]]]) -> dict[tuple[str, str], list[float]]:
    from playwright.async_api import async_playwright

    from aadhi.compose.dynamic.stage_page import DETERMINISM_ARGS
    from aadhi.compose.screenshot import CHROMIUM_ARGS

    out: dict[tuple[str, str], list[float]] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[*CHROMIUM_ARGS, *DETERMINISM_ARGS])
        try:
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body></body></html>")
            for face, runs in faces:
                b64 = base64.b64encode(Path(fc.web_path(face)).read_bytes()).decode("ascii")
                for spacing in kn.LETTER_SPACING:
                    got: list[float] = []
                    for i in range(0, len(runs), 4000):
                        got += await page.evaluate(_EXCESS_JS, [b64, runs[i:i + 4000], spacing])
                    out[(kn.face_name(face), spacing)] = got
        finally:
            await browser.close()
    return out


def test_every_run_the_table_bounds_is_drawn_within_its_bounds() -> None:
    faces = _faces()
    table = kn.load()
    rng = random.Random(SEED)
    probes = [(f, _probes(f, table[kn.face_name(f)], rng)) for f in faces]
    excess = asyncio.run(_excess(probes))
    seen = {"bounded": 0, "open": 0}
    problems = []
    for face, runs in probes:
        name = kn.face_name(face)
        k = table[name]
        for spacing in kn.LETTER_SPACING:
            for run, e in zip(runs, excess[(name, spacing)], strict=True):
                if not k.bounded(run):
                    seen["open"] += 1
                    continue
                seen["bounded"] += 1
                hi, lo = k.bounds(run)
                if not lo * 1000 <= e <= hi * 1000:
                    problems.append((name, spacing, run, round(e, 2), round(lo * 1000), round(hi * 1000)))
    assert problems == [], problems[:20]
    # both kinds were met: Inter's chains are open, most runs of every face are bounded
    assert seen["bounded"] > len(faces) * PROBES and seen["open"] > PROBES, seen
    inter = table["inter-latin-400-normal.woff2"]
    assert not any(inter.bounded(r) for r in REVIEW_RUNS[:11])
    assert all(inter.bounded(r) for r in ("2x3x4x5", "X-ray", "COVID-19", "12:30", "(DNA)", "example", "Maxwell"))
