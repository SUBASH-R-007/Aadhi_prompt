"""Pair kerning of the vendored Latin faces, measured once in Chromium (B2b review F1, F2; DYNAMIC_VIDEO_DESIGN §4.6,
B2 plan §1.11): what Pillow's basic layout does not apply and the page does.

The page shapes text with HarfBuzz (``font-kerning: normal``, common ligatures, ``text-rendering:
geometricPrecision``: dynamic.css), so a pair of characters drawn by one face is drawn wider or narrower than its two
advances. Pillow's basic layout applies none of it (no GPOS; none of the vendored faces has a legacy ``kern`` table,
pinned by tests/compose/dynamic/test_textfit.py). The review measured what a per-word allowance misses: positive
adjustments are not rare (Outfit ``(j`` +0.098 em, Atkinson ``qj`` +0.154 em, Inter ``ïï`` +0.166 em and its
contextual ``<–`` +0.187 em), and they add up per pair (``f'f'f'f'``), so ``textfit`` bounds them **per pair**:

* ``positive``: every pair of characters a face draws whose adjustment exceeds :data:`EPSILON_MILLI` (rounded up, plus
  :data:`MARGIN_MILLI`), in 1/1000 em; a pair not listed is bounded by :data:`EPSILON_MILLI`.
* ``most_negative``: the face's most negative pair adjustment (rounded down, less the margin): the most a pair can draw
  narrower, which bounds how narrow the page can draw a word (``textfit``'s optimistic reading, from which it decides
  that the page certainly breaks an over-wide word).

How it is measured (:func:`measure`, needs Chromium and ``web/vendor``): each Latin face file (not the Indic faces,
whose text ``textfit`` never certifies) is loaded into a blank page as its own ``FontFace``; the characters are the
ones its ``unicode-range`` holds and its cmap maps (``textfit.has_glyph``, the same test ``textfit`` resolves a cluster
with), the space included; for every ordered pair ``ab`` the canvas width of ``ab`` less the widths of ``a`` and
``b`` at :data:`MEASURE_PX` px, with the page's kerning, ligature and rendering settings, once with no letter-spacing
(ligatures on) and once with letter-spacing (Chromium then drops the optional ligatures, as on a slot with tracking);
the larger adjustment of the two is kept for ``positive``, the smaller for ``most_negative``. The launch flags are the
render worker's (``CHROMIUM_ARGS``, ``DETERMINISM_ARGS``). No vendored face kerns the space (checked by the
generator): a word's kerning ends at its edges.

    python -m aadhi.compose.dynamic.kerning            # measure and write aadhi/compose/dynamic/kerning.json
    python -m aadhi.compose.dynamic.kerning --check    # exit 1 when the committed table differs from a measurement

The committed table is read at run time (:func:`load`, JSON only: no Chromium); the slow test
tests/determinism/test_kerning_table.py measures again and checks the table still bounds every pair (on any
platform: a stored value is never below a measured one), and tests/determinism/test_textfit_page.py checks the bound
on the real render page. Regenerate after a face changes (``faces.py``, ``npm run vendor``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from . import faces as fc

VERSION: Final = 1
TABLE_PATH: Final = Path(__file__).with_name("kerning.json")
TABLE_NAME: Final = "aadhi/compose/dynamic/kerning.json"
GENERATOR: Final = "python -m aadhi.compose.dynamic.kerning"
MEASURE_PX: Final = 1000  # 1 px = 1/1000 em
EPSILON_MILLI: Final = 1  # pairs adjusted by at most this (1/1000 em) are not listed; each is bounded by it
MARGIN_MILLI: Final = 0.1  # added before rounding up (subtracted before rounding down): float noise between machines
LETTER_SPACING: Final = ("0px", "10px")  # without tracking (ligatures on), and with it (Chromium drops them)


class KerningError(RuntimeError):
    """The kerning table is missing, damaged or not the face table's, or cannot be measured (fail closed)."""


@dataclass(frozen=True)
class FaceKerning:
    """One face's measured pairs, in em: ``positive`` (pair -> adjustment above :data:`EPSILON_MILLI`) and
    ``most_negative`` (<= 0)."""

    positive: Mapping[str, float]
    most_negative: float

    def bound(self, a: str, b: str) -> float:
        """The most the pair ``ab`` can draw wider than its two advances, in em."""
        return self.positive.get(a + b, EPSILON_MILLI / 1000)


def table_faces() -> tuple[fc.Face, ...]:
    """The faces the table covers: every vendored face of a family that is not Indic (declaration order)."""
    return tuple(f for f in fc.FACES if fc.FAMILIES[f.family].vendored and fc.FAMILIES[f.family].role != "indic")


def face_name(face_or_path: fc.Face | str | Path) -> str:
    """The table's key for a face: its file name (``inter-latin-400-normal.woff2``)."""
    path = face_or_path.file if isinstance(face_or_path, fc.Face) else face_or_path
    return Path(str(path).replace("\\", "/")).name


@lru_cache(maxsize=4)
def load(path: Path = TABLE_PATH) -> Mapping[str, FaceKerning]:
    """The committed table: face file name -> :class:`FaceKerning`. Fails closed (:class:`KerningError`) when the
    file is missing or damaged, or its faces are not exactly :func:`table_faces`."""
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KerningError(f"{TABLE_NAME} cannot be read ({exc}): run {GENERATOR}") from exc
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("epsilon") != EPSILON_MILLI:
        raise KerningError(f"{TABLE_NAME} is not a version {VERSION} table (epsilon {EPSILON_MILLI}): run {GENERATOR}")
    faces = data.get("faces")
    want = {face_name(f) for f in table_faces()}
    if not isinstance(faces, dict) or set(faces) != want:
        raise KerningError(f"{TABLE_NAME} does not cover exactly the face table's faces: run {GENERATOR}")
    out: dict[str, FaceKerning] = {}
    for name, row in faces.items():
        try:
            negative = int(row["most_negative"])
            positive: dict[str, float] = {}
            for milli, pairs in row["positive"].items():
                value = int(milli)
                chars = list(pairs)
                if value <= EPSILON_MILLI or not chars or len(chars) % 2 or str(value) != milli:
                    raise ValueError(f"bad entry {milli!r}")
                for i in range(0, len(chars), 2):
                    positive[chars[i] + chars[i + 1]] = value / 1000
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise KerningError(f"{TABLE_NAME}: face {name} is damaged ({exc}): run {GENERATOR}") from exc
        if negative > 0:
            raise KerningError(f"{TABLE_NAME}: face {name}'s most negative adjustment is positive")
        out[name] = FaceKerning(MappingProxyType(positive), negative / 1000)
    return MappingProxyType(out)


# --- measurement (Chromium; the generator and the slow test) ---------------------------------------------------------

_PAIRS_JS = """async ([b64, chars, spacing, floor]) => {
  const face = new FontFace('KernProbe', `url(data:font/woff2;base64,${b64})`);
  await face.load();
  document.fonts.add(face);
  try {
    const c = document.createElement('canvas').getContext('2d');
    c.font = `${PX}px KernProbe`;
    c.fontKerning = 'normal';
    c.textRendering = 'geometricPrecision';
    c.letterSpacing = spacing;
    const one = new Map(chars.map((a) => [a, c.measureText(a).width]));
    const out = [];
    let min = 0;
    for (const a of chars) {
      const wa = one.get(a);
      for (const b of chars) {
        const d = c.measureText(a + b).width - wa - one.get(b);
        if (d < min) min = d;
        if (d > floor) out.push([a + b, d]);
      }
    }
    return { out, min };
  } finally {
    document.fonts.delete(face);
  }
}""".replace("${PX}", str(MEASURE_PX))


def characters(face: fc.Face, web_dir: Path | str | None = None) -> list[str]:
    """The characters a face's pairs are measured for: its ``unicode-range`` and cmap (``textfit.has_glyph``), from
    the space up, without default-ignorable characters (they take no width and form no pair: ``textfit``)."""
    from . import textfit as tf

    key = tf.face_key(face, face.weight[0], web_dir)
    out = []
    for lo, hi in face.ranges:
        for cp in range(max(lo, 0x20), hi + 1):
            ch = chr(cp)
            if not tf.ignorable(ch) and tf.has_glyph(key, ch):
                out.append(ch)
    return list(dict.fromkeys(out))


def _inputs(web_dir: Path | str | None) -> list[tuple[str, str, list[str]]]:
    """(face name, the file as base64, its characters) of every :func:`table_faces` face, read before Chromium starts."""
    out = []
    for face in table_faces():
        path = fc.web_path(face, web_dir)
        try:
            b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        except OSError as exc:
            raise KerningError(f"cannot read {path} (npm run vendor): {exc}") from exc
        chars = characters(face, web_dir)
        if face.covers(0x20) != (" " in chars):
            raise KerningError(f"{face_name(face)}: its range and its cmap disagree on the space")
        out.append((face_name(face), b64, chars))
    return out


def _row(name: str, best: Mapping[str, float], lowest: float) -> dict[str, Any]:
    """One face's table row from its measured pairs (rounded up, with the margin) and lowest adjustment (down)."""
    positive: dict[int, list[str]] = {}
    for pair, d in best.items():
        milli = math.ceil(d + MARGIN_MILLI)
        if milli > EPSILON_MILLI:
            if " " in pair:
                raise KerningError(f"{name} kerns the space ({pair!r} +{d:.1f}/1000 em): textfit assumes no face "
                                   "does; extend it")
            positive.setdefault(milli, []).append(pair)
    return {"most_negative": math.floor(lowest - MARGIN_MILLI),
            "positive": {str(k): "".join(sorted(v)) for k, v in sorted(positive.items())}}


async def _measure_async(inputs: list[tuple[str, str, list[str]]]) -> dict[str, dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - a dev dependency
        raise KerningError("playwright is not installed: the table is measured in Chromium") from exc
    from ..screenshot import CHROMIUM_ARGS
    from .stage_page import DETERMINISM_ARGS

    floor = EPSILON_MILLI - MARGIN_MILLI - 0.5  # a superset of what is kept (kept: rounded up above EPSILON_MILLI)
    out: dict[str, dict[str, Any]] = {}
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, args=[*CHROMIUM_ARGS, *DETERMINISM_ARGS])
        except Exception as exc:  # noqa: BLE001 - any launch failure: no measurement
            raise KerningError(f"Chromium cannot start: {str(exc).splitlines()[0][:200]}") from exc
        try:
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body></body></html>")
            for name, b64, chars in inputs:
                best: dict[str, float] = {}
                lowest = 0.0
                for spacing in LETTER_SPACING:
                    got = await page.evaluate(_PAIRS_JS, [b64, chars, spacing, floor])
                    lowest = min(lowest, float(got["min"]))
                    for pair, d in got["out"]:
                        best[pair] = max(best.get(pair, d), d)
                out[name] = _row(name, best, lowest)
        finally:
            await browser.close()
    return out


def measure(web_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Measure every :func:`table_faces` face in Chromium: face file name -> ``{most_negative, positive}`` (1/1000 em;
    ``positive`` maps an adjustment to the pairs it applies to, concatenated)."""
    return asyncio.run(_measure_async(_inputs(web_dir)))


def render(faces: Mapping[str, Mapping[str, Any]]) -> str:
    """The table's text: one face per line, sorted keys, UTF-8, LF (byte-deterministic)."""
    head = {"version": VERSION, "generator": GENERATOR, "unit": "1/1000 em", "epsilon": EPSILON_MILLI}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + ['  "faces": {']
    names = sorted(faces)
    for i, name in enumerate(names):
        row = json.dumps(faces[name], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        lines.append(f"    {json.dumps(name)}: {row}{',' if i + 1 < len(names) else ''}")
    return "\n".join([*lines, "  }", "}"]) + "\n"


def covers(table: Mapping[str, FaceKerning], measured: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Where a fresh measurement is not bounded by the table (empty: the table still holds): a pair measured wider than
    its bound, or a face measured narrower than its ``most_negative``."""
    problems = []
    for name, row in measured.items():
        k = table.get(name)
        if k is None:
            problems.append(f"{name}: not in the table")
            continue
        if row["most_negative"] < round(k.most_negative * 1000):
            problems.append(f"{name}: most negative {row['most_negative']} < {round(k.most_negative * 1000)}")
        for milli, pairs in row["positive"].items():
            chars = list(pairs)
            for i in range(0, len(chars), 2):
                pair = chars[i] + chars[i + 1]
                if int(milli) > round(k.bound(*pair) * 1000):
                    problems.append(f"{name}: {pair!r} +{milli} > {round(k.bound(*pair) * 1000)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed table differs")
    parser.add_argument("--path", type=Path, default=TABLE_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        text = render(measure())
    except KerningError as exc:
        print(f"{TABLE_NAME} cannot be measured: {exc}", file=sys.stderr)
        return 2
    try:
        current: str | None = args.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        current = None
    if args.check:
        if current == text:
            return 0
        print(f"{TABLE_NAME} is out of date: run {GENERATOR}", file=sys.stderr)
        return 1
    if current != text:
        args.path.write_bytes(text.encode("utf-8"))
        print(f"wrote {TABLE_NAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
