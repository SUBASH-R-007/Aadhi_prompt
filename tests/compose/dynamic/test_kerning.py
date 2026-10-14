"""The measured pair-kerning table (aadhi/compose/dynamic/kerning.py, kerning.json; B2b review F1, F2): what the fast
suite can check without Chromium. tests/determinism/test_kerning_table.py measures it again in Chromium.

* The committed table covers exactly the face table's Latin faces, in its canonical spelling, and holds the pairs the
  review measured on the page (Outfit ``f'`` and ``(j``, Atkinson ``qj``, Inter ``ïï``); no pair holds the space.
* Loading fails closed on a missing, damaged or stale table; :func:`kerning.covers` reports a measurement the table
  does not bound.
* The run-time path reads JSON only (no Chromium, no Playwright).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import kerning as kn

ROOT = Path(__file__).resolve().parents[3]


def test_the_table_covers_the_latin_faces_in_its_canonical_spelling() -> None:
    table = kn.load()
    assert set(table) == {kn.face_name(f) for f in kn.table_faces()}
    assert {fc.FAMILIES[f.family].role for f in kn.table_faces()} == {"display", "body", "mono"}
    raw = kn.TABLE_PATH.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    assert raw.decode("utf-8") == kn.render(data["faces"]) and b"\r" not in raw
    for name, face in table.items():
        assert face.most_negative <= 0, name
        assert all(v > kn.EPSILON_MILLI / 1000 for v in face.positive.values()), name
        assert not any(" " in pair for pair in face.positive), name  # no face kerns the space
    # the review's page measurements, and the faces without kerning
    assert table["outfit-latin-700-normal.woff2"].positive["f'"] >= 0.07
    assert table["outfit-latin-700-normal.woff2"].positive["(j"] >= 0.098
    assert table["atkinson-hyperlegible-next-latin-700-normal.woff2"].positive["qj"] >= 0.15
    assert table["inter-latin-400-normal.woff2"].positive["ïï"] >= 0.16
    assert table["outfit-latin-700-normal.woff2"].most_negative <= -0.1  # AV, To, Yo ...
    assert all(not table[kn.face_name(f)].positive for f in fc.faces_of("poppins"))
    assert table["outfit-latin-700-normal.woff2"].bound("o", "o") == kn.EPSILON_MILLI / 1000


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "kerning.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loading_fails_closed(tmp_path: Path) -> None:
    good = json.loads(kn.TABLE_PATH.read_text(encoding="utf-8"))
    name = "outfit-latin-700-normal.woff2"

    def broken(change) -> dict:
        data = json.loads(json.dumps(good))
        change(data)
        return data

    cases = {
        "missing": None,
        "not json": "{",
        "version": broken(lambda d: d.update(version=2)),
        "epsilon": broken(lambda d: d.update(epsilon=kn.EPSILON_MILLI + 1)),
        "a face missing": broken(lambda d: d["faces"].pop(name)),
        "a face too many": broken(lambda d: d["faces"].update({"x.woff2": d["faces"][name]})),
        "odd pairs": broken(lambda d: d["faces"][name]["positive"].update({"50": "abc"})),
        "at the epsilon": broken(lambda d: d["faces"][name]["positive"].update({str(kn.EPSILON_MILLI): "ab"})),
        "spelling": broken(lambda d: d["faces"][name]["positive"].update({"050": "ab"})),
        "positive floor": broken(lambda d: d["faces"][name].update(most_negative=3)),
        "no floor": broken(lambda d: d["faces"][name].pop("most_negative")),
    }
    for why, data in cases.items():
        path = tmp_path / why.replace(" ", "_") / "kerning.json"
        path.parent.mkdir()
        if data is not None:
            path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        with pytest.raises(kn.KerningError):
            kn.load(path)
    ok = tmp_path / "ok"
    ok.mkdir()
    assert kn.load(_write(ok, good)) == kn.load()


def test_covers_reports_what_the_table_does_not_bound() -> None:
    table = kn.load()
    name = "outfit-latin-700-normal.woff2"
    face = table[name]
    fine = {name: {"most_negative": round(face.most_negative * 1000), "positive": {"99": "(j", "1": "oo"}}}
    assert kn.covers(table, fine) == []
    assert kn.covers(table, {name: {"most_negative": -999, "positive": {}}})  # narrower than the floor
    assert kn.covers(table, {name: {"most_negative": 0, "positive": {"100": "(j"}}})  # wider than its bound
    assert kn.covers(table, {name: {"most_negative": 0, "positive": {"2": "oo"}}})  # an unlisted pair over epsilon
    assert kn.covers(table, {"x.woff2": {"most_negative": 0, "positive": {}}})


def test_the_run_time_path_reads_json_only() -> None:
    code = ("import sys; from aadhi.compose.dynamic import kerning as kn; kn.load(); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('playwright', 'PIL')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


def test_a_row_rounds_up_and_refuses_a_kerned_space() -> None:
    row = kn._row("x.woff2", {"ab": 1.95, "cd": 0.85, "ef": 0.95, "gh": 37.0, "ij": -3.0}, -12.34)
    assert row == {"most_negative": -13, "positive": {"2": "ef", "3": "ab", "38": "gh"}}
    with pytest.raises(kn.KerningError, match="kerns the space"):
        kn._row("x.woff2", {"a ": 5.0}, 0.0)
