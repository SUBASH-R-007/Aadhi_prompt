"""THIRD_PARTY_NOTICES (scripts/gen_third_party_notices.py; design §4.12, §7.13): generated from package-lock.json and
the installed packages' licence files, covering every asset web/vendor serves, with ``--check`` in CI.

* The committed file is current (needs node_modules: CI's Python job runs ``npm ci`` before its generated-files step).
* It covers every runtime package of the lock (and so every package scripts/vendor.mjs copies from, every font the
  face table vendors and both icon sets the icon index is built from), with each package's licence and NOTICE texts;
  type declarations are left out.
* What the vendored files contain (B2b review): every file web/vendor serves is reviewed in ``VENDORED``; the code a
  prebuilt bundle holds beyond its package (p5's browserify modules, the earcut copy in three, mathjs' bundled
  versions, ...) is listed with its licence texts, and the bundle's own licence file is quoted (math.js.LICENSE.txt).
* Generation fails closed: a package that is not installed, installed at another version, or that ships no licence
  file and has no fallback source; a fallback source without its marker; a vendored file whose reviewed facts no
  longer hold (missing evidence, an unlisted module, an unreviewed copyright statement, a package update).
* ``--check`` fails on a stale or missing copy.
"""

from __future__ import annotations

import dataclasses
import fnmatch
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
    # B2b review (B2b-CONF-1, LIC-1): the header no longer claims the lock covers what the bundles hold
    assert "A library's bundle can include code of the packages it depends on" not in " ".join(head.split())
    assert "third-party components inside the vendored files" in head


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


# --- what the vendored files contain (B2b review: B2b-CONF-1, LIC-1, LIC-2) ---------------------------------------

def _served(gen, rel: str):
    """The VENDORED entry (first match, as the generator claims files) or licence file a web/vendor path belongs to."""
    for entry in gen.VENDORED:
        if entry.licence_file is not None and entry.licence_file[1] == rel:
            return entry
        if fnmatch.fnmatchcase(rel, entry.served):
            return entry
    return None


@needs_node_modules
def test_every_served_file_is_reviewed(gen) -> None:
    """Every library file web/vendor serves has a VENDORED entry (a new vendor.mjs copy must be reviewed for the code
    it bundles), and every specific entry is served byte for byte from its node_modules source."""
    vendor = ROOT / "web" / "vendor"
    if not (vendor / "manifest.json").is_file():
        pytest.skip("web/vendor is not generated (npm run vendor)")
    files = sorted(p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file())
    assert "p5/p5.min.js" in files and "mathjs/math.js.LICENSE.txt" in files
    for rel in files:
        if any(fnmatch.fnmatchcase(rel, pattern) for pattern in gen.SERVED_ELSEWHERE):
            continue
        assert _served(gen, rel) is not None, f"web/vendor/{rel} is served but not reviewed in VENDORED"
    nm = ROOT / "node_modules"
    for entry in gen.VENDORED:
        pairs = [] if entry.is_glob else [(entry.source, entry.served)]
        pairs += [entry.licence_file] if entry.licence_file else []
        for source, served in pairs:
            assert (vendor / served).read_bytes() == (nm / source).read_bytes(), served
        if entry.is_glob:
            assert not entry.components and not entry.notices, entry.source  # a glob holds no reviewed code
        # a licence file the publisher ships beside the bundle must be served and quoted (LIC-2)
        elif (nm / f"{entry.source}.LICENSE.txt").is_file():
            assert entry.licence_file == (f"{entry.source}.LICENSE.txt", f"{entry.served}.LICENSE.txt"), entry.source


@needs_node_modules
def test_the_bundled_code_is_listed_with_its_licences(gen) -> None:
    sections = _sections(gen.render())
    p5 = next(v for k, v in sections.items() if k.startswith("p5 "))
    # every package p5's browserify module table holds is a component with its licence text (B2b-CONF-1)
    table = gen.browserify_packages((ROOT / "node_modules" / "p5" / "lib" / "p5.min.js").read_text(encoding="utf-8"))
    assert set(table) == {"_process", "base64-js", "buffer", "core-js", "es6-promise", "fetch-jsonp", "file-saver",
                          "gifenc", "ieee754", "libtess", "omggif", "opentype.js", "path", "regenerator-runtime",
                          "whatwg-fetch"}
    entry = next(e for e in gen.VENDORED if e.source == "p5/lib/p5.min.js")
    assert entry.module_table and {m for c in entry.components for m in c.modules} == set(table)
    for marker in ("--- core-js 3.6.5: LICENSE of the core-js 3.6.5 npm package ---", "Copyright (c) 2014-2020 Denis "
                   "Pushkarev", "Copyright 2015, Google Inc. All Rights Reserved.", "(c) Dean McNamee",
                   "Copyright (c) 2017 Frederik De Bleser", "Copyright 2008 Fair Oaks Labs, Inc.",
                   "Copyright Joyent, Inc. and other Node contributors.", "Copyright (c) 2015-present Devon Govett",
                   "Copyright Mathias Bynens", "Copyright (c) 2017 Eric Wendelin and other contributors",
                   "Copyright (c) 2008-2010 Dmitry Baranovskiy", "Copyright (c) 2014-2016 GitHub, Inc.",
                   "Copyright © 2016 [Eli Grey][1].", "Copyright (c) 2017 Matt DesLauriers"):
        assert marker in p5, marker
    three = next(v for k, v in sections.items() if k.startswith("three "))
    assert "earcut 3.0.2 (ISC)" in three and "Copyright (c) 2024, Mapbox" in three  # LIC-1
    assert "Boost Software License - Version 1.0" in three and "Jürg Lehni & Jonathan Puckey" in three
    mathjax = next(v for k, v in sections.items() if k.startswith("mathjax "))
    assert all(f"{n} " in mathjax for n in ("mj-context-menu 0.6.1", "speech-rule-engine 4.0.6", "mhchemparser 4.1.1"))
    chart = next(v for k, v in sections.items() if k.startswith("chart.js "))
    assert "@kurkle/color 0.3.2 (MIT)" in chart and "Copyright (c) 2018-2021 Jukka Kurkela" in chart
    dompurify = next(v for k, v in sections.items() if k.startswith("dompurify "))
    assert "regenerator-runtime (version not stated) (MIT)" in dompurify
    # LIC-2: the bundle's own licence file is quoted, and the bundled versions are the bundle's, not the lock's
    mathjs = next(v for k, v in sections.items() if k.startswith("mathjs "))
    bundle_licence = (ROOT / "node_modules" / "mathjs" / "lib" / "browser" / "math.js.LICENSE.txt")
    assert gen._clean(bundle_licence.read_text(encoding="utf-8")) in mathjs
    assert "decimal.js 10.5.0 (MIT)" in mathjs and "decimal.js v10.5.0" in mathjs
    # every committed licence text is used, and every one a component names exists
    used = {t.file for e in gen.VENDORED for c in e.components for t in c.texts if t.file}
    assert used == {p.name for p in gen.LICENCE_DIR.iterdir()}


@needs_node_modules
def test_the_texts_taken_from_the_p5_bundle_are_its_comments(gen) -> None:
    """The notices p5's unminified build carries (the same build p5.min.js minifies) are the committed texts."""
    source = (ROOT / "node_modules" / "p5" / "lib" / "p5.js").read_text(encoding="utf-8")
    comments = "\n".join(re.sub(r"^\s*(//|\*)?\s?", "", ln).rstrip() for ln in source.split("\n"))
    for name in ("omggif-1.0.10.txt", "stacktrace.js-p5.txt", "node-path-notice.txt", "libtess-p5.txt"):
        assert gen._clean((gen.LICENCE_DIR / name).read_text(encoding="utf-8")) in comments, name
    # libtess (B2b review): the notice the bundled module carries, whose dates SGI-B 2.0 asks to be kept (the libtess
    # 1.2.2 package's LICENSE file says "Copyright 2012, Google Inc." and adds a paragraph the module does not carry)
    libtess = gen._clean((gen.LICENCE_DIR / "libtess-p5.txt").read_text(encoding="utf-8"))
    module = source[source.index("Copyright 2000, Silicon Graphics, Inc. All Rights Reserved."):]
    module = gen._clean(module[:module.index("*/")])
    assert libtess == "\n".join(ln.strip() for ln in module.split("\n")).strip()
    assert libtess.splitlines()[:2] == ["Copyright 2000, Silicon Graphics, Inc. All Rights Reserved.",
                                        "Copyright 2015, Google Inc. All Rights Reserved."]
    assert "including the dates of first publication" in libtess and "Except as contained" not in libtess


_BUNDLE = ('!function(){}({1:[function(e,t,r){var a=e("lib-a"),b=e("@scope/b/x");t.exports=1},{"lib-a":2,'
           '"@scope/b/x":3,"./local":4,stub:5,gone:void 0}],2:[function(e,t,r){t.exports=2},{}],'
           '3:[function(e,t,r){t.exports=3},{}],4:[function(e,t,r){t.exports=4},{}],5:[function(e,t,r){},{}]},{},[1]);')


def test_browserify_packages_reads_the_module_table(gen) -> None:
    assert gen.browserify_packages(_BUNDLE) == {"lib-a": {"lib-a"}, "@scope/b": {"@scope/b/x"}}
    for broken in ("no table at all", _BUNDLE.replace("},{},[1]", ""), _BUNDLE.replace(',{"lib-a"', ',{lib a"')):
        with pytest.raises(gen.NoticeError):
            gen.browserify_packages(broken)


def test_check_vendored_fails_closed(gen) -> None:
    comp = gen.Component("lib-a", "1.0.0", "MIT", "bundled", (gen.Text("t", file="x"),), ("t.exports=2",),
                         modules=("lib-a",), notices=("Copyright (c) 2020 A",))
    entry = gen.Vendored("pkg", "1.0.0", "pkg/a.js", "pkg/a.js", (comp,), notices=("(c) 2021 Pkg",),
                         credits=(("(c) 2005 Book", "an algorithm"),))
    gen.check_vendored(entry, _BUNDLE + "/* Copyright (c) 2020 A; (c) 2021 Pkg; (c) 2005 Book */", "a.js")
    with pytest.raises(gen.NoticeError, match="no evidence of lib-a"):
        gen.check_vendored(entry, _BUNDLE.replace("t.exports=2", "t.exports=9"), "a.js")
    with pytest.raises(gen.NoticeError, match="unreviewed copyright statement"):
        gen.check_vendored(entry, _BUNDLE + "/* Copyright (c) 2022 Someone Else */", "a.js")
    for statement in ("© 2019 X", "(c) 1999 X", "Copyright 2001 X", "COPYRIGHT © X"):
        with pytest.raises(gen.NoticeError, match="unreviewed copyright statement"):
            gen.check_vendored(entry, _BUNDLE + statement, "a.js")
    table = dataclasses.replace(entry, module_table=True)
    with pytest.raises(gen.NoticeError, match=re.escape("requires ['@scope/b'] that no component lists")):
        gen.check_vendored(table, _BUNDLE, "a.js")
    both = dataclasses.replace(table, components=(comp, dataclasses.replace(comp, name="b", modules=("@scope/b",))))
    gen.check_vendored(both, _BUNDLE, "a.js")
    with pytest.raises(gen.NoticeError, match="no evidence"):
        gen.check_vendored(dataclasses.replace(entry, components=(dataclasses.replace(comp, markers=()),)), "", "a.js")


@needs_node_modules
def test_a_package_update_requires_a_new_review(gen, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = next(e for e in gen.VENDORED if e.package == "three")
    stale = tuple(dataclasses.replace(e, version="0.0.1") if e is entry else e for e in gen.VENDORED)
    monkeypatch.setattr(gen, "VENDORED", stale)
    with pytest.raises(gen.NoticeError, match="VENDORED was reviewed at 0.0.1"):
        gen.render()


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


def test_generation_fails_closed(gen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen, "VENDORED", ())  # synthetic packages: no vendored files to review
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
