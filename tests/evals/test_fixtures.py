"""Fixture discovery/options and the PDF fixture generator (evals/make_fixtures.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pymupdf
import pytest

from aadhi.config import ROOT_DIR
from aadhi.evals.fixtures import FixtureError, discover_fixtures, resolve_options

FIXTURES_DIR = ROOT_DIR / "evals" / "fixtures"


def _load_make_fixtures():
    path = ROOT_DIR / "evals" / "make_fixtures.py"
    spec = importlib.util.spec_from_file_location("aadhi_eval_make_fixtures", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def make_fixtures():
    return _load_make_fixtures()


def _touch(path: Path, text: str = "x") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- discovery -----------------------------------------------------------------------------------


def test_discover_kinds_sidecars_and_ignored_files(tmp_path):
    _touch(tmp_path / "b_lesson.txt", "Ohm's law")
    _touch(tmp_path / "a_notes.md", "# Notes")
    _touch(tmp_path / "saved.json", "{}")
    _touch(tmp_path / "a_notes.options.json", json.dumps({"target_minutes": 5}))
    for ignored in ("README.md", "_draft.txt", ".hidden.pdf", "image.png"):
        _touch(tmp_path / ignored)
    (tmp_path / "subdir.pdf").mkdir()
    fixtures = discover_fixtures(tmp_path)
    assert [f.name for f in fixtures] == ["a_notes", "b_lesson", "saved"]
    by_name = {f.name: f for f in fixtures}
    assert by_name["a_notes"].kind == "source" and by_name["a_notes"].mime == "text/markdown"
    assert by_name["b_lesson"].mime == "text/plain"
    assert by_name["saved"].kind == "screenplay"
    assert dict(by_name["a_notes"].options) == {"target_minutes": 5}
    assert dict(by_name["b_lesson"].options) == {}


def test_discover_only_filter_and_errors(tmp_path):
    _touch(tmp_path / "one.txt")
    _touch(tmp_path / "two.txt")
    assert [f.name for f in discover_fixtures(tmp_path, only=["two"])] == ["two"]
    with pytest.raises(FixtureError, match="unknown fixtures: nope"):
        discover_fixtures(tmp_path, only=["nope"])
    with pytest.raises(FixtureError, match="does not exist"):
        discover_fixtures(tmp_path / "missing")
    _touch(tmp_path / "one.md")
    with pytest.raises(FixtureError, match="share the name"):
        discover_fixtures(tmp_path)


def test_reserved_fixture_name_is_rejected(tmp_path):
    _touch(tmp_path / "Workspace.pdf")  # would collide with <out>/workspace of --keep-workspace
    with pytest.raises(FixtureError, match="reserved"):
        discover_fixtures(tmp_path)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_bad_sidecars_are_rejected(tmp_path, content):
    _touch(tmp_path / "lesson.txt")
    _touch(tmp_path / "lesson.options.json", content)
    with pytest.raises(FixtureError):
        discover_fixtures(tmp_path)


def test_resolve_options_merges_and_validates(tmp_path):
    _touch(tmp_path / "lesson.txt")
    _touch(tmp_path / "lesson.options.json", json.dumps({"target_minutes": 5, "language": "en-IN"}))
    (fx,) = discover_fixtures(tmp_path)
    opts = resolve_options(fx, {"target_minutes": 8, "include_quizzes": False})
    assert opts.target_minutes == 8 and opts.include_quizzes is False and opts.language == "en-IN"
    with pytest.raises(FixtureError, match="unknown option"):
        resolve_options(fx, {"target_minuts": 8})
    with pytest.raises(FixtureError, match="invalid options"):
        resolve_options(fx, {"target_minutes": 1})
    with pytest.raises(FixtureError, match="invalid options"):
        resolve_options(fx, {"language": "xx-XX"})


def test_committed_fixtures_are_discoverable_with_valid_options():
    fixtures = {f.name: f for f in discover_fixtures(FIXTURES_DIR)}
    assert {"ohms_law", "logic_gates", "stress_strain", "sample_template", "legacy_ohms_law"} <= set(fixtures)
    assert fixtures["sample_template"].kind == "source"
    assert fixtures["legacy_ohms_law"].kind == "screenplay"
    for fx in fixtures.values():
        resolve_options(fx)  # every sidecar validates


# --- generator -----------------------------------------------------------------------------------


def test_generator_writes_realistic_reproducible_pdfs(tmp_path, make_fixtures):
    first = make_fixtures.generate(tmp_path / "a")
    second = make_fixtures.generate(tmp_path / "b")
    assert [p.name for p in first] == [p.name for p in second]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes(), f"{a.name} is not reproducible"
    with pymupdf.open(tmp_path / "a" / "logic_gates.pdf") as doc:
        assert doc.page_count == 2
        assert doc.metadata["title"] == "Logic Gates and Universal Gates"
        tables = [t.extract() for t in doc[0].find_tables().tables]
        truth = [t for t in tables if t and len(t[0]) == 8]
        assert truth and truth[0][0][:3] == ["A", "B", "AND"] and len(truth[0]) == 5
    with pymupdf.open(tmp_path / "a" / "stress_strain.pdf") as doc:
        text = "".join(page.get_text() for page in doc)
        assert "σ = F / A" in text and "159.15" in text and "Page 2 of 2" in text
    with pymupdf.open(tmp_path / "a" / "ohms_law.pdf") as doc:
        text = "".join(page.get_text() for page in doc)
        assert "V = I × R" in text and "Ω" in text
    sidecar = json.loads((tmp_path / "a" / "ohms_law.options.json").read_text(encoding="utf-8"))
    assert sidecar["target_minutes"] == 10


def test_generator_rejects_overflowing_pages(tmp_path, make_fixtures):
    spec = make_fixtures.FixtureSpec(name="huge", title="Huge", header="h", pages=("<p>word</p>" * 2000,))
    with pytest.raises(ValueError, match="does not fit"):
        make_fixtures.build_pdf(spec, tmp_path / "huge.pdf")


def test_committed_pdf_fixtures_are_current(make_fixtures):
    assert make_fixtures.check(FIXTURES_DIR) == []


def test_generator_cli_check_and_out(tmp_path, make_fixtures, capsys):
    assert make_fixtures.main(["--out", str(tmp_path)]) == 0
    assert make_fixtures.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "ohms_law.options.json").write_text("{}", encoding="utf-8")
    assert make_fixtures.main(["--check", "--out", str(tmp_path)]) == 1
    assert "ohms_law.options.json" in capsys.readouterr().out


def test_check_tolerates_crlf_sidecars_but_not_changed_pdfs(tmp_path, make_fixtures):
    """A Windows clone with core.autocrlf=true checks the JSON sidecars out with CRLF."""
    make_fixtures.generate(tmp_path)
    for sidecar in tmp_path.glob("*.options.json"):
        sidecar.write_bytes(sidecar.read_bytes().replace(b"\n", b"\r\n"))
    assert make_fixtures.check(tmp_path) == []
    pdf = tmp_path / "ohms_law.pdf"
    pdf.write_bytes(pdf.read_bytes().replace(b"\n", b"\r\n"))  # PDFs stay byte-exact
    assert make_fixtures.check(tmp_path) == ["ohms_law.pdf"]
