"""THIRD_PARTY_NOTICES (scripts/gen_third_party_notices.py; design §4.12, §7.13): generated from package-lock.json and
the installed packages' licence files, covering every asset web/vendor serves, with ``--check`` in CI.

* The committed file is current (needs node_modules: CI's Python job runs ``npm ci`` before its generated-files step).
* It covers every runtime package of the lock (and so every package scripts/vendor.mjs copies from, every font the
  face table vendors and both icon sets the icon index is built from), with each package's licence and NOTICE texts;
  type declarations are left out.
* Generation fails closed: a package that is not installed, installed at another version, or that ships no licence
  file and has no fallback source; a fallback source without its marker.
* ``--check`` fails on a stale or missing copy.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import icons as ic

ROOT = Path(__file__).resolve().parents[2]
needs_node_modules = pytest.mark.skipif(not (ROOT / "node_modules").is_dir(), reason="node_modules is not installed")


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("aadhi_script_gen_third_party_notices",
                                                  ROOT / "scripts" / "gen_third_party_notices.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sections(text: str) -> dict[str, str]:
    """``name version`` -> the section's text."""
    parts = text.split("=" * 100 + "\n")
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


@needs_node_modules
def test_the_committed_file_is_current(gen) -> None:
    assert (ROOT / "THIRD_PARTY_NOTICES").read_bytes().decode("utf-8") == gen.render()
    assert gen.main(["--check"]) == 0


@needs_node_modules
def test_every_vendored_asset_is_covered(gen) -> None:
    text = gen.render()
    sections = _sections(text)
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    runtime = {p.removeprefix("node_modules/"): m["version"] for p, m in lock["packages"].items()
               if p.startswith("node_modules/") and not m.get("dev") and not p.startswith("node_modules/@types/")}
    assert set(sections) == {f"{n} {v}" for n, v in runtime.items()}
    assert not any(s.startswith("@types/") for s in sections) and not any(s.startswith("typescript ") for s in sections)
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    for name, version in package["dependencies"].items():
        assert f"{name} {version}" in sections, name
        assert "Dependency of: this app (package.json)" in sections[f"{name} {version}"]
    # every package scripts/vendor.mjs copies from (its copy() sources and its FONTS list)
    vendor = (ROOT / "scripts" / "vendor.mjs").read_text(encoding="utf-8")
    sources = {m.group(1) for m in re.finditer(r"copy\(`?'?((?:@[\w.-]+/)?[\w.-]+)/", vendor)}
    sources |= {f"@fontsource/{m.group(1)}" for m in re.finditer(r"\{ pkg: '([\w-]+)'", vendor)}
    sources |= {f.package for f in fc.FAMILIES.values() if f.css == "dynamic"}  # the face table's (vendor.mjs reads it)
    assert {"chart.js", "mathjax", "three", "@fontsource/inter", "@fontsource/poppins"} <= sources
    for name in sources:
        assert any(s.startswith(f"{name} ") for s in sections), name
    # every vendored font family says where it is vendored
    for f in fc.FAMILIES.values():
        if f.vendored:
            where = f"Vendored: font family '{f.name}': web/vendor/{f.directory}/"
            assert where in sections[f"{f.package} {f.version}"], f.id
    # ... and so does every icon set (B2b: its geometry lives in the icon index, copied into the timelines)
    for s in ic.SETS:
        assert package["dependencies"][s.package] == s.version, s.package
        where = f"Vendored: icon set '{s.id}': SVG path data normalised into {ic.INDEX_NAME} ({ic.GENERATOR})"
        assert where in sections[f"{s.package} {s.version}"], s.id
    head = text.split(f"{'=' * 100}\n", 1)[0]
    assert "draws icons from the Lucide and Tabler sets" in " ".join(head.split())


@needs_node_modules
def test_the_licence_texts_are_the_packages(gen) -> None:
    sections = _sections(gen.render())
    nm = ROOT / "node_modules"
    mathjs = next(v for k, v in sections.items() if k.startswith("mathjs "))
    assert "--- NOTICE ---" in mathjs and gen._clean((nm / "mathjs" / "NOTICE").read_text(encoding="utf-8")) in mathjs
    p5 = next(v for k, v in sections.items() if k.startswith("p5 "))
    assert "Licence (declared): LGPL-2.1" in p5 and "GNU LESSER GENERAL PUBLIC LICENSE" in p5
    dompurify = next(v for k, v in sections.items() if k.startswith("dompurify "))
    assert "--- LICENSE ---" in dompurify and "--- LICENSE-MPL ---" in dompurify
    for f in fc.FAMILIES.values():
        if f.vendored:
            section = sections[f"{f.package} {f.version}"]
            assert "Licence (declared): OFL-1.1" in section and fc.OFL_TEXT_MARK in section, f.package
            assert gen._clean((nm / f.package / "LICENSE").read_text(encoding="utf-8")) in section, f.package
    for s in ic.SETS:  # the icon sets' licences: ISC (+ the Feather MIT notice) and MIT, the texts the index carries
        section = sections[f"{s.package} {s.version}"]
        assert f"Licence (declared): {s.licence}" in section, s.package
        licence = gen._clean((nm / s.package / "LICENSE").read_text(encoding="utf-8"))
        assert licence in section and all(mark in licence.splitlines() for mark in s.licence_marks), s.package
    seedrandom = next(v for k, v in sections.items() if k.startswith("seedrandom "))
    assert "README.md (licence section" in seedrandom and "Copyright 2019 David Bau." in seedrandom
    sort = next(v for k, v in sections.items() if k.startswith("javascript-natural-sort "))
    assert "Released under MIT license" in sort and "module.exports" not in sort
    text = gen.render()
    assert "\r" not in text and text.endswith("\n") and not text.endswith("\n\n")
    assert all(line == line.rstrip() for line in text.split("\n"))


def _fake_tree(tmp_path: Path, packages: dict[str, dict]) -> tuple[Path, Path]:
    lock = {"packages": {"": {"dependencies": {n: p["version"] for n, p in packages.items()}}}}
    nm = tmp_path / "node_modules"
    for name, p in packages.items():
        lock["packages"][f"node_modules/{name}"] = {"version": p["version"], "license": "MIT"}
        folder = nm / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version": p.get("installed", p["version"])}), "utf-8")
        for fname, text in p.get("files", {}).items():
            (folder / fname).write_bytes(text.encode("utf-8"))  # exact bytes (no newline translation)
    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(lock), "utf-8")
    return path, nm


def test_generation_fails_closed(gen, tmp_path: Path) -> None:
    ok = {"version": "1.0.0", "files": {"LICENSE": "MIT\r\ntext  \n\n"}}
    lock, nm = _fake_tree(tmp_path / "a", {"a": ok})
    text = gen.render(lock, nm)
    assert "--- LICENSE ---\n\nMIT\ntext\n" in text  # CRLF and trailing spaces normalised, nothing else changed
    for name, broken, why in (
        ("b", {"version": "1.0.0", "installed": "1.0.1", "files": {"LICENSE": "x"}}, "1.0.1 is installed"),
        ("c", {"version": "1.0.0", "files": {"README.md": "no licence here"}}, "ships no licence file"),
        ("d", {"version": "1.0.0", "files": {"LICENSE": "  \n"}}, "ships no licence file"),
        ("seedrandom", {"version": "3.0.5", "files": {"README.md": "no section"}}, "no line starting 'LICENSE (MIT)'"),
    ):
        lock, nm = _fake_tree(tmp_path / name, {name: broken})
        with pytest.raises(gen.NoticeError, match=re.escape(why)):
            gen.render(lock, nm)
    lock, nm = _fake_tree(tmp_path / "missing", {"e": ok})
    (nm / "e" / "package.json").unlink()
    with pytest.raises(gen.NoticeError, match="not installed"):
        gen.render(lock, nm)


def test_check_fails_on_a_stale_or_missing_copy(gen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(gen, "render", lambda: "NOTICES\nline two\n")
    copy = tmp_path / "THIRD_PARTY_NOTICES"
    assert gen.main(["--check", "--path", str(copy)]) == 1  # missing
    assert gen.main(["--path", str(copy)]) == 0 and copy.read_bytes() == b"NOTICES\nline two\n"
    assert gen.main(["--check", "--path", str(copy)]) == 0
    copy.write_bytes(b"NOTICES\nline 2\n")
    assert gen.main(["--check", "--path", str(copy)]) == 1
    assert "first difference at line 2" in capsys.readouterr().err

    def broken() -> str:
        raise gen.NoticeError("x is not installed")

    monkeypatch.setattr(gen, "render", broken)
    assert gen.main(["--check", "--path", str(copy)]) == 2
    assert "cannot be generated: x is not installed" in capsys.readouterr().err
