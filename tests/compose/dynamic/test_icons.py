"""Item icons from ``icon_hint`` (aadhi.compose.dynamic.icons -> aadhi/compose/dynamic/icon_index.json; design §4.12,
§6.7; plan B2 §A step 2, §1.2 ``ResolvedLook.icons``).

* The sets: Lucide and Tabler exact-pinned in package.json and the lock as runtime dependencies, at the versions the
  module names, with their licence texts in the committed index.
* The committed index is current (``--check``), byte-deterministic across hash seeds, laid out one entry per line;
  ``--check`` fails on a stale or missing copy and exits 2 when nothing can be generated.
* Every indexed icon is canonical, inside the schema's allow-list and caps, and re-sanitises to itself; aliases and tags
  point at indexed icons; no tag stands for a crossed-out picture; the exclusions are the brands and the fills.
* Normalisation: path data re-spelled exactly (the SVG 2 grammar, strictly); each basic shape becomes its SVG 2
  equivalent path; hostile files (DTDs and entities, scripts, handlers, styles, transforms, references, foreign
  content, absurd numbers, oversize data) are refused; hostile stored icons are refused by ``sanitise_icon``.
* The match: normalisation and forms; the tier order (Lucide name, Tabler name, Lucide tag, Tabler tag) on a synthetic
  index; coverage of common teaching keywords and pinned results on the real one; no match is None; deterministic
  across hash seeds; pruning changes no result; a damaged index is an error, never a silent "no icon".
* The generator fails closed (synthetic packages): another version or licence, a metadata row without its file, an
  ambiguous unlisted file, a stale reviewed list.
* ``scene_icons``: item order, the per-scene cap, the ResolvedLook round trip; the run-time path imports no XML parser
  and loads the index quickly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.compose.dynamic import icons as ic
from aadhi.compose.dynamic.zones import LAYOUT
from aadhi.schemas.screenplay import BoardItem
from aadhi.schemas.timeline import ICON_PATH_MAX_CHARS, ICON_PATH_RE, ICONS_PER_SCENE_MAX, LookIcon, ResolvedLook

ROOT = Path(__file__).resolve().parents[3]
NODE_MODULES = ROOT / "node_modules"
needs_node_modules = pytest.mark.skipif(not NODE_MODULES.is_dir(), reason="node_modules is not installed (npm ci)")
SET_KEYS = ["package", "version", "licence", "licence_text", "icons", "aliases", "tags", "excluded"]


@pytest.fixture(scope="module")
def index() -> dict[str, Any]:
    return json.loads(ic.INDEX_PATH.read_bytes().decode("utf-8"))


# --- the sets and their pins -----------------------------------------------------------------------------------------


def test_the_sets_are_pinned_runtime_dependencies() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    assert [s.id for s in ic.SETS] == ["lucide", "tabler"] == list(ic.SET_ORDER)  # Lucide primary (§4.12)
    for s in ic.SETS:
        assert package["dependencies"][s.package] == s.version, s.package  # exact, never a range
        entry = lock[f"node_modules/{s.package}"]
        assert entry["version"] == s.version and entry["license"] == s.licence and not entry.get("dev"), s.package
        assert entry["integrity"].startswith("sha512-")
    assert {s.id: s.licence for s in ic.SETS} == {"lucide": "ISC", "tabler": "MIT"}  # §4.12 table


def test_the_index_carries_each_sets_licence(index: dict[str, Any]) -> None:
    for s in ic.SETS:
        entry = index["sets"][s.id]
        assert (entry["package"], entry["version"], entry["licence"]) == (s.package, s.version, s.licence)
        lines = entry["licence_text"].split("\n")
        for mark in s.licence_marks:
            assert mark in lines, (s.id, mark)
    lucide = index["sets"]["lucide"]["licence_text"]
    assert "Permission to use, copy, modify, and/or distribute this software" in lucide  # ISC
    assert "The following Lucide icons are derived from the Feather project:" in lucide  # + the Feather MIT notice
    assert "Permission is hereby granted, free of charge" in index["sets"]["tabler"]["licence_text"]


@needs_node_modules
def test_the_licence_texts_are_the_packages_files(index: dict[str, Any]) -> None:
    for s in ic.SETS:
        raw = (NODE_MODULES / s.package / "LICENSE").read_bytes().decode("utf-8")
        assert index["sets"][s.id]["licence_text"] == ic._clean(raw), s.id
        notices = (ROOT / "THIRD_PARTY_NOTICES").read_bytes().decode("utf-8")
        assert ic._clean(raw) in notices, s.id  # the same text the notices carry


# --- the committed index ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered() -> str:
    if not NODE_MODULES.is_dir():
        pytest.skip("node_modules is not installed (npm ci)")
    return ic.render_index()


def test_the_committed_index_is_current(rendered: str) -> None:
    assert ic.INDEX_PATH.read_bytes().decode("utf-8") == rendered
    assert ic.main(["--check"]) == 0


@needs_node_modules
def test_generation_is_byte_deterministic_across_hash_seeds(tmp_path: Path, rendered: str) -> None:
    digests = set()
    for seed in ("0", "1"):
        out = tmp_path / f"index-{seed}.json"
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-m", "aadhi.compose.dynamic.icons", "--path", str(out)], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=300, check=False)
        assert proc.returncode == 0, proc.stderr
        digests.add(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests == {hashlib.sha256(rendered.encode("utf-8")).hexdigest()}


def test_check_fails_on_a_stale_or_missing_copy_and_when_nothing_can_be_built(
        tmp_path: Path, fake_modules: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "icon_index.json"
    args = ["--path", str(target), "--node-modules", str(fake_modules)]
    assert ic.main(["--check", *args]) == 1  # missing
    assert ic.main(args) == 0 and f"wrote {ic.INDEX_NAME}" in capsys.readouterr().out
    assert ic.main(["--check", *args]) == 0
    target.write_bytes(target.read_bytes().replace(b'"science": "atom"', b'"science": "flask-conical"'))
    assert ic.main(["--check", *args]) == 1
    assert "is out of date (first difference at line" in capsys.readouterr().err
    assert ic.main(["--check", "--path", str(target), "--node-modules", str(tmp_path / "nowhere")]) == 2
    assert "cannot be generated" in capsys.readouterr().err


def test_the_index_layout(index: dict[str, Any]) -> None:
    text = ic.INDEX_PATH.read_bytes().decode("utf-8")
    assert "\r" not in text and text.endswith("}\n") and text.startswith('{\n"version": 1,\n')
    assert list(index) == ["version", "generator", "order", "style", "sets"]
    assert index["version"] == ic.INDEX_VERSION == 1 and index["generator"] == ic.GENERATOR
    assert index["order"] == ["lucide", "tabler"] and list(index["sets"]) == ["lucide", "tabler"]
    assert index["style"] == {"viewBox": [0, 0, 24, 24], "stroke_width": 2, "filled": False,
                              "stroke_linecap": "round", "stroke_linejoin": "round"}
    assert index["style"]["stroke_width"] == LAYOUT.icon["stroke_width"]  # the catalogue's icon stroke
    for s in ic.SET_ORDER:
        entry = index["sets"][s]
        assert list(entry) == SET_KEYS
        for key in ("icons", "aliases", "tags", "excluded"):
            assert list(entry[key]) == sorted(entry[key]), (s, key)
    # one icon / alias / tag / exclusion per line: a library update is a reviewable diff
    assert len(text.split("\n")) > sum(len(index["sets"][s][k]) for s in ic.SET_ORDER
                                       for k in ("icons", "aliases", "tags", "excluded"))


def test_every_indexed_icon_is_canonical_and_within_the_caps(index: dict[str, Any]) -> None:
    pattern = re.compile(ICON_PATH_RE)
    for s in ic.SET_ORDER:
        entry = index["sets"][s]
        for name, paths in entry["icons"].items():
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64, name
            assert 1 <= len(paths) <= 32, name
            for d in paths:
                assert len(d) <= ICON_PATH_MAX_CHARS and pattern.fullmatch(d), (name, d)
                assert re.fullmatch(r"[MmZzLlHhVvCcSsQqTtAa0-9. -]+", d), (name, d)  # no exponent, no comma
                assert ic.canonical_path(d) == d, (name, d)  # canonical: re-spelling is the identity
        for alias, target in entry["aliases"].items():
            assert target in entry["icons"] and alias not in entry["icons"], (s, alias)
        for tag, target in entry["tags"].items():
            assert target in entry["icons"], (s, tag)
            assert not ic.negates(target, tag), (s, tag, target)  # never a crossed-out picture for its tag
            assert ic.normalise_key(tag) == tag
    sample = {"name": "atom", "set": "lucide", "version": ic.SETS[0].version, "viewBox": [0, 0, 24, 24],
              "stroke_width": 2, "filled": False, "paths": index["sets"]["lucide"]["icons"]["atom"]}
    assert ic.sanitise_icon(sample) == LookIcon.model_validate(sample)


def test_the_exclusions_are_brands_and_fills(index: dict[str, Any]) -> None:
    lucide, tabler = index["sets"]["lucide"]["excluded"], index["sets"]["tabler"]["excluded"]
    fill = "a filled <{}> (one fill per icon cannot express it)"
    assert all(r == fill.format("circle") or r.startswith("alias of ") for r in lucide.values()), lucide
    assert {"palette", "tag", "tags", "chart-scatter"} <= set(lucide)  # their dots are fills (Chromium, see docstring)
    reasons = {r if not r.startswith("a filled <") else "fill" for r in tabler.values()}
    assert reasons == {"category Brand (trade marks)", "fill"}, reasons
    assert sum(r.startswith("category Brand") for r in tabler.values()) > 300
    assert not any(n.startswith("brand-") for n in index["sets"]["tabler"]["icons"])
    assert len(index["sets"]["lucide"]["icons"]) > 1800 and len(index["sets"]["lucide"]["aliases"]) > 250


@needs_node_modules
def test_every_library_icon_is_indexed_or_excluded() -> None:
    from aadhi.compose.dynamic.icons import _build_set

    for s in ic.SETS:
        built = _build_set(s, NODE_MODULES)  # unpruned
        files = {p.stem for p in (NODE_MODULES / s.package / s.svg_dir).glob("*.svg")}
        assert files == set(built["icons"]) | set(built["aliases"]) | set(built["excluded"]), s.id
        assert not set(built["icons"]) & set(built["excluded"])


# --- path data -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("d", "canonical"), [
    ("M12 2v4", "M12 2v4"),
    ("M 1,2 L 3,4", "M1 2L3 4"),
    ("m14 12 4 4 4-4", "m14 12 4 4 4-4"),  # implicit lineto repeats kept
    ("M2 16l4.039-9.69a.5.5 0 0 1 .923 0L11 16", "M2 16l4.039-9.69a.5 .5 0 0 1 .923 0L11 16"),
    ("M1 1a1 1 0 011 1", "M1 1a1 1 0 0 1 1 1"),  # compact arc flags
    ("M0.50 -0.0 L1e1 +2", "M.5 0L10 2"),  # exact values, no exponent, no sign noise
    ("M1.5.5", "M1.5 .5"),  # two numbers
    ("M0 0\tC1 2 3 4 5 6\nS7 8 9 10Q1 2 3 4T5 6Z", "M0 0C1 2 3 4 5 6S7 8 9 10Q1 2 3 4T5 6Z"),
    ("M0 0zm1 1h-2V-3", "M0 0zm1 1h-2V-3"),
    ("M-0.25-.5", "M-.25-.5"),
])
def test_path_data_is_respelled_exactly(d: str, canonical: str) -> None:
    assert ic.canonical_path(d) == canonical
    assert ic.canonical_path(canonical) == canonical
    assert [(c, a) for c, a in ic.parse_path(d)] == [(c, a) for c, a in ic.parse_path(canonical)]


@pytest.mark.parametrize("d", [
    "", "   ", "L1 2", "Z", "1 2", "M", "M1", "M1 2 3", "MZ", "M0 0Z1", "M0 0 L1",
    "M0 0A1 1 0 2 0 1 1",  # an arc flag is 0 or 1
    "M0 0A1 1 0 1", "M0 0C1 2 3 4 5",
    "M1e999 0", "M1e6 0", "M100001 0", "M1e 0", "M0 0x", "M0 0 url(#a)", "M0 0\" onload=\"alert(1)",
    "M0,,0", "M0 0,", "M0 0,L1 1", "M,0 0", "M0.0000001 0", "M1.00000000000000001 0", "M NaN 0", "M0 Infinity",
    "M1.0000000000000000000000000001 0",  # 29 digits: Decimal's 28-digit context would round it to 1 silently
    pytest.param("M0 0" + "L1 1" * 2000, id="over-4096"),  # over the 4,096-character cap once re-spelled
])
def test_bad_path_data_is_refused(d: str) -> None:
    with pytest.raises(ic.IconError):
        ic.canonical_path(d)


def test_numbers_are_spelled_canonically() -> None:
    assert [ic.fmt(Decimal(x)) for x in ("0", "-0", "0.5", "-0.50", "2.0", "1E+2", "12.034", "-12")] == [
        "0", "0", ".5", "-.5", "2", "100", "12.034", "-12"]


# --- shapes -> paths (SVG 2 §10) -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("tag", "attrs", "d"), [
    ("circle", {"cx": "12", "cy": "12", "r": "1"},
     "M13 12A1 1 0 0 1 12 13A1 1 0 0 1 11 12A1 1 0 0 1 12 11A1 1 0 0 1 13 12Z"),
    ("circle", {"r": ".5"}, "M.5 0A.5 .5 0 0 1 0 .5A.5 .5 0 0 1-.5 0A.5 .5 0 0 1 0-.5A.5 .5 0 0 1 .5 0Z"),
    ("ellipse", {"cx": "12", "cy": "5", "rx": "9", "ry": "3"},
     "M21 5A9 3 0 0 1 12 8A9 3 0 0 1 3 5A9 3 0 0 1 12 2A9 3 0 0 1 21 5Z"),
    ("rect", {"x": "3", "y": "4", "width": "18", "height": "16"}, "M3 4H21V20H3V4Z"),
    ("rect", {"width": "18", "height": "18", "x": "3", "y": "3", "rx": "2"},
     "M5 3H19A2 2 0 0 1 21 5V19A2 2 0 0 1 19 21H5A2 2 0 0 1 3 19V5A2 2 0 0 1 5 3Z"),
    ("rect", {"x": "2", "y": "2", "width": "4", "height": "2", "rx": "3", "ry": "5"},  # radii clamped to half sizes
     "M4 2H4A2 1 0 0 1 6 3V3A2 1 0 0 1 4 4H4A2 1 0 0 1 2 3V3A2 1 0 0 1 4 2Z"),
    ("rect", {"width": "4", "height": "4", "ry": "1"},  # rx "auto": ry
     "M1 0H3A1 1 0 0 1 4 1V3A1 1 0 0 1 3 4H1A1 1 0 0 1 0 3V1A1 1 0 0 1 1 0Z"),
    ("line", {"x1": "12", "x2": "12", "y1": "2", "y2": "6"}, "M12 2L12 6"),
    ("line", {"x2": "3"}, "M0 0L3 0"),
    ("polyline", {"points": "22 12 18 12 15 21"}, "M22 12L18 12 15 21"),
    ("polygon", {"points": "12,2 15,8 9,8"}, "M12 2L15 8 9 8Z"),
])
def test_each_shape_becomes_its_svg2_equivalent_path(tag: str, attrs: dict[str, str], d: str) -> None:
    assert ic.shape_path(tag, attrs) == d


@pytest.mark.parametrize(("tag", "attrs"), [
    ("circle", {"cx": "1", "cy": "1"}), ("circle", {"r": "0"}), ("circle", {"r": "-1"}),
    ("ellipse", {"rx": "1"}), ("rect", {"width": "0", "height": "1"}), ("rect", {"width": "1"}),
    ("rect", {"width": "1", "height": "1", "rx": "-1"}), ("polyline", {"points": "1 2"}),
    ("polyline", {"points": "1 2 3"}), ("polygon", {}), ("path", {}), ("circle", {"r": "1px"}),
    ("text", {"x": "1"}), ("circle", {"r": " 1e999"}),
])
def test_a_shape_that_does_not_draw_is_refused(tag: str, attrs: dict[str, str]) -> None:
    with pytest.raises(ic.IconError):
        ic.shape_path(tag, attrs)


# --- whole files -----------------------------------------------------------------------------------------------------

ROOT_ATTRS = ('xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
              'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"')


def svg(body: str, root: str = ROOT_ATTRS, head: str = "") -> str:
    return f"{head}<svg {root}>{body}</svg>"


def test_a_library_file_normalises() -> None:
    text = ("<!-- @license lucide-static v1.48.0 - ISC -->\n"
            + svg('\n  <circle cx="12" cy="12" r="1" />\n  <path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9Z" />\n',
                  root='class="lucide lucide-atom" ' + ROOT_ATTRS))
    icon = ic.normalise_svg(text, set_id="lucide", name="atom", version="1.48.0")
    assert icon.model_dump() == {
        "name": "atom", "set": "lucide", "version": "1.48.0", "viewBox": (0.0, 0.0, 24.0, 24.0), "stroke_width": 2.0,
        "filled": False, "paths": ["M13 12A1 1 0 0 1 12 13A1 1 0 0 1 11 12A1 1 0 0 1 12 11A1 1 0 0 1 13 12Z",
                                   "M20.2 20.2c2.04-2.03 .02-7.36-4.5-11.9Z"]}
    # Tabler: the invisible bounding path is dropped; a declaration is fine
    tabler = svg('<path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M9 16a5 5 0 1 1 6 0"/>',
                 head='<?xml version="1.0" encoding="UTF-8"?>\n')
    assert ic.normalise_svg(tabler, set_id="tabler", name="bulb", version="3.48.0").paths == ["M9 16a5 5 0 1 1 6 0"]


BILLION_LAUGHS = ('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
                  '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>' + svg('<path d="M0 0L1 1"/><desc>&c;</desc>'))


LINE = '<path d="M0 0L1 1"/>'
XLINK = 'xmlns:xlink="http://www.w3.org/1999/xlink"'
HOSTILE: dict[str, str] = {
    "billion-laughs": BILLION_LAUGHS,
    # an entity the XML parser would expand into a well-formed icon: only the refusal before parsing stops it
    "entity-in-attribute": '<!DOCTYPE svg [<!ENTITY d "M0 0L1 1">]>' + svg('<path d="&d;"/>'),
    "external-entity": '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + svg(LINE),
    "doctype": "<!doctype svg>" + svg(LINE),
    "stylesheet-pi": '<?xml-stylesheet href="https://evil.example/x.css"?>' + svg(LINE),
    "cdata": svg(LINE + "<![CDATA[<script>alert(1)</script>]]>"),
    "php-pi": svg("<?php echo 1; ?>" + LINE),
    "script": svg("<script>alert(1)</script>" + LINE),
    "onload": svg('<path d="M0 0L1 1" onload="alert(1)"/>'),
    "root-onload": svg(LINE, root=ROOT_ATTRS + ' onload="alert(1)"'),
    "style": svg('<path d="M0 0L1 1" style="stroke:red"/>'),
    "root-style": svg(LINE, root=ROOT_ATTRS + ' style="fill:red"'),
    "transform": svg('<path d="M0 0L1 1" transform="scale(9)"/>'),
    "opacity": svg('<path d="M0 0L1 1" opacity=".5"/>'),
    "stroke-width": svg('<path d="M0 0L1 1" stroke-width="9"/>'),
    "class": svg('<path d="M0 0L1 1" class="x"/>'),
    "group": svg(f"<g>{LINE}</g>"),
    "use": svg('<use href="#a"/>'),
    "use-xlink": svg(f'<use {XLINK} xlink:href="https://evil.example/s.svg#a"/>'),
    "xlink-javascript": svg(f'<path {XLINK} xlink:href="javascript:alert(1)" d="M0 0L1 1"/>'),
    "image-data-url": svg('<image href="data:image/png;base64,AAAA" width="24" height="24"/>'),
    "foreign-object": svg('<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'),
    "anchor": svg(f'<a href="javascript:alert(1)">{LINE}</a>'),
    "text": svg('<text x="1" y="1">hi</text>'),
    "text-in-path": svg('<path d="M0 0L1 1">text</path>'),
    "tail-text": svg(LINE + "tail text"),
    "foreign-namespace": svg('<x:path xmlns:x="urn:other" d="M0 0L1 1"/>'),
    "fill-url": svg('<path d="M0 0L1 1" fill="url(#g)"/>'),
    "stroke-red": svg('<path d="M0 0L1 1" stroke="red"/>'),
    "fill": svg('<path d="M0 0L1 1" fill="currentColor"/>'),  # not expressible in a stroke icon
    "filled-dot": svg('<circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>'),  # Chromium strokes it with a hole
    "fill-without-stroke": svg('<path stroke="none" fill="currentColor" d="M0 0L1 1Z"/>'),
    "javascript-d": svg('<path d="javascript:alert(1)"/>'),
    "33-elements": svg(LINE * 33),  # over 32 drawing elements
    "only-invisible": svg('<path stroke="none" fill="none" d="M0 0h24v24H0z"/>'),  # nothing visible
    "empty": svg(""),
    "viewbox-48": svg(LINE, root=ROOT_ATTRS.replace('viewBox="0 0 24 24"', 'viewBox="0 0 48 48"')),
    "stroke-width-1.5": svg(LINE, root=ROOT_ATTRS.replace('stroke-width="2"', 'stroke-width="1.5"')),
    "root-fill": svg(LINE, root=ROOT_ATTRS.replace('fill="none"', 'fill="currentColor"')),
    "no-linecap": svg(LINE, root=ROOT_ATTRS.replace(' stroke-linecap="round"', "")),
    "html-root": f'<html xmlns="http://www.w3.org/1999/xhtml">{LINE}</html>',
    "not-well-formed": svg('<path d="M0 0L1 1">'),
    "oversize": svg(LINE + "<!-- x -->" + " " * (ic.SVG_MAX_BYTES + 1)),
}


@pytest.mark.parametrize("text", list(HOSTILE.values()), ids=list(HOSTILE))
def test_hostile_or_foreign_files_are_refused(text: str) -> None:
    started = time.perf_counter()
    with pytest.raises(ic.IconError):
        ic.normalise_svg(text, set_id="lucide", name="x", version="1")
    assert time.perf_counter() - started < 1.0  # refused before any expansion


@pytest.mark.parametrize("text", [
    pytest.param(svg(f'<path {XLINK} xlink:href="javascript:alert(1)" d="M0 0L1 1"/>'), id="element"),
    pytest.param(svg(LINE, root=ROOT_ATTRS + f' {XLINK} xlink:href="https://evil.example/"'), id="root"),
    pytest.param(svg('<g><path d="M0 0L1 1"/></g>'), id="nested"),
    pytest.param(svg('<path d="M0 0L1 1">x</path>'), id="text"),
])
def test_the_reader_alone_refuses_foreign_markup(text: str) -> None:
    """parse_svg is also used on its own (the generator compares a deprecated file's elements with the listed icons'),
    so its own refusals hold without normalise_svg's attribute allow-lists."""
    with pytest.raises(ic.IconError):
        ic.parse_svg(text)


@pytest.mark.parametrize("name", ["", "Atom", "a b", "../atom", "atom/", "atom.svg", "-atom", "atom-", "a" * 65])
def test_an_icon_name_is_a_plain_id(name: str) -> None:
    with pytest.raises(ic.IconError):
        ic.normalise_svg(svg('<path d="M0 0L1 1"/>'), set_id="lucide", name=name, version="1")


# --- stored icons: sanitised again -----------------------------------------------------------------------------------

GOOD = {"name": "atom", "set": "lucide", "version": "1.48.0", "viewBox": [0, 0, 24, 24], "stroke_width": 2,
        "filled": False, "paths": ["M 1,2 L 3,4"]}


@pytest.mark.parametrize("change", [
    {"paths": ['M0 0L1 1" onload="alert(1)']}, {"paths": ["M0 0L1 1<script>"]}, {"paths": ["url(#a)"]},
    {"paths": ["M1e999 0"]}, {"paths": ["M 1 2 3"]}, {"paths": ["MZ"]}, {"paths": ["M0 0A1 1 0 2 0 1 1"]},
    {"paths": []}, {"paths": ["M0 0L1 1"] * 33}, pytest.param({"paths": ["M0 0" + "L1 1" * 1100]}, id="over-4096"),
    {"paths": "M0 0L1 1"},
    {"name": "a b"}, {"name": "../x"}, {"viewBox": [0, 0, 0, 24]}, {"viewBox": [0, 0, float("inf"), 24]},
    {"stroke_width": 9}, {"stroke_width": float("nan")}, {"set": ""},
])
def test_hostile_stored_icons_are_refused(change: dict[str, Any]) -> None:
    assert ic.sanitise_icon({**GOOD, **change}) is None


def test_a_stored_icon_is_returned_canonical() -> None:
    icon = ic.sanitise_icon({**GOOD, "onload": "alert(1)"})  # unknown keys never survive
    assert icon is not None and icon.paths == ["M1 2L3 4"] and "onload" not in icon.model_dump()
    assert ic.sanitise_icon(icon) == icon
    assert ic.sanitise_icon(None) is None and ic.sanitise_icon("M0 0") is None


# --- keys and forms --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "key"), [
    ("atom", "atom"), ("Light Bulb", "light-bulb"), ("  TEST_TUBE ", "test-tube"), ("pie/chart", "pie-chart"),
    ("ﬁre", "fire"), ("H2O", "h2o"), (2, "2"), ("a" * 24, "a" * 24),
    ("", None), ("  ", None), ("--", None), ("தமிழ்", None), ("naïve", None), ("a" * 25, None), (None, None),
    (True, None), (2.5, None), (["atom"], None),
])
def test_keys(raw: object, key: str | None) -> None:
    assert ic.normalise_key(raw) == key


def test_forms_are_tried_in_order() -> None:
    assert ic.hint_forms("atom") == ("atom",)
    assert ic.hint_forms("light-bulb") == ("light-bulb", "lightbulb")
    assert ic.hint_forms("atoms") == ("atoms", "atom")
    assert ic.hint_forms("batteries") == ("batteries", "battery", "batterie")
    assert ic.hint_forms("boxes") == ("boxes", "box", "boxe")
    assert ic.hint_forms("leaves") == ("leaves", "leaf", "leafe", "leave")
    assert ic.hint_forms("buses") == ("buses", "buse", "bus")
    assert ic.hint_forms("cases") == ("cases", "case", "cas")
    assert ic.hint_forms("test-tubes") == ("test-tubes", "testtubes", "test-tube", "testtube")
    for word in ("gas", "bus", "glass", "virus", "axis"):
        assert ic.hint_forms(word) == (word,), word


# --- the match: tier order on a synthetic index ---------------------------------------------------------------------


def _index_file(tmp_path: Path, entries: dict[str, dict[str, Any]], **top: Any) -> Path:
    data: dict[str, Any] = {"version": ic.INDEX_VERSION, "generator": ic.GENERATOR, "order": list(ic.SET_ORDER),
                            "style": ic._style_json(), "sets": {}}
    for s in ic.SETS:
        entry = {"package": s.package, "version": s.version, "licence": s.licence, "licence_text": "x",
                 "icons": {}, "aliases": {}, "tags": {}, "excluded": {}}
        entry.update(entries.get(s.id, {}))
        data["sets"][s.id] = entry
    data.update(top)
    path = tmp_path / f"index-{hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


P = ["M0 0L1 1"]


def test_the_tier_order(tmp_path: Path) -> None:
    path = _index_file(tmp_path, {
        "lucide": {"icons": {"atom": P, "house": P, "flask-conical": P, "stethoscope": P, "lightbulb": P},
                   "aliases": {"home": "house"},
                   "tags": {"lungs": "stethoscope", "science": "flask-conical", "idea": "lightbulb"}},
        "tabler": {"icons": {"atom": P, "atoms": P, "lungs": P, "bulb": P, "teacher-board": P, "flask": P},
                   "tags": {"idea": "bulb", "teacher": "teacher-board", "science": "flask"}},
    })

    def m(hint: str) -> tuple[str, str, str, str] | None:
        r = ic.match(hint, path=path)
        return None if r is None else (r.set, r.name, r.via, r.form)

    assert m("atom") == ("lucide", "atom", "name", "atom")  # both sets name it: Lucide (primary)
    assert m("atoms") == ("lucide", "atom", "name", "atom")  # a Lucide name in any form beats a Tabler name
    assert m("home") == ("lucide", "house", "name", "home")  # a deprecated Lucide name is a name
    assert m("lungs") == ("tabler", "lungs", "name", "lungs")  # a Tabler NAME beats a Lucide TAG
    assert m("idea") == ("lucide", "lightbulb", "tag", "idea")  # a Lucide tag beats a Tabler tag
    assert m("teacher") == ("tabler", "teacher-board", "tag", "teacher")  # Tabler fills the gap
    assert m("light bulb") == ("lucide", "lightbulb", "name", "lightbulb")  # the hyphen-free form
    assert m("photosynthesis") is None and m("") is None and m(None) is None  # type: ignore[arg-type]
    icon = ic.icon_for("lungs", path=path)
    assert icon is not None and (icon.set, icon.name, icon.version) == ("tabler", "lungs", ic.SETS[1].version)


@pytest.mark.parametrize("damage", [
    {"version": 2}, {"order": ["tabler", "lucide"]}, {"style": {**ic._style_json(), "stroke_width": 1.5}},
    {"sets": {}}, {"sets": "x"},
])
def test_a_damaged_index_is_an_error(tmp_path: Path, damage: dict[str, Any]) -> None:
    path = _index_file(tmp_path, {"lucide": {"icons": {"atom": P}}}, **damage)
    with pytest.raises(ic.IconIndexError):
        ic.match("atom", path=path)


@pytest.mark.parametrize("entry", [
    {"version": "0.0.1", "icons": {"atom": P}},
    {"icons": {"atom": "M0 0L1 1"}}, {"icons": {"atom": [1]}}, {"icons": ["atom"]}, {"tags": {"x": ["atom"]}},
    {"aliases": None},
])
def test_a_malformed_set_is_an_error(tmp_path: Path, entry: dict[str, Any]) -> None:
    path = _index_file(tmp_path, {"lucide": entry})
    with pytest.raises(ic.IconIndexError):
        ic.load_index(path)


def test_a_dangling_or_unsafe_entry_is_an_error_not_a_silent_none(tmp_path: Path) -> None:
    path = _index_file(tmp_path, {"lucide": {"icons": {"atom": ['M0 0" onload="x']}, "tags": {"science": "flask"}}})
    with pytest.raises(ic.IconIndexError, match="fails sanitisation"):
        ic.icon_for("atom", path=path)
    with pytest.raises(ic.IconIndexError, match="missing icon"):
        ic.icon_for("science", path=path)
    with pytest.raises(ic.IconIndexError):
        ic.match("atom", path=tmp_path / "missing.json")


# --- the match on the real index -------------------------------------------------------------------------------------

TEACHING_WORDS = """
science idea money time energy growth physics chemistry biology earth water electricity heat speed graph cell dna
plant sun computer code network book learn question warning check people family health heart brain history map world
music art food virus bacteria magnet light sound rocket planet moon star atom molecule experiment microscope telescope
calculator ruler compass clock calendar trophy target key lock shield cloud rain fire tree leaf flower fish bird dog cat
car train plane ship bicycle house home school hospital factory bank shop coin chart trend arrow link puzzle gear tool
battery wifi phone camera video image file folder search list grid layers box globe flag user users teacher student
pencil notebook lightbulb bulb thermometer scale balance flask beaker pill syringe bone eye ear hand smile recycle wind
mountain ocean volcano formula equation fraction percent geometry triangle circle angle vector matrix statistics dice
database server cpu robot internet email message chat goal success summary exam test reading writing language
translate dictionary library law government vote tax savings city village farm wheat soil gene lungs blood tooth
muscle skeleton vaccine medicine doctor exercise sleep temperature pressure mass weight volume wave frequency circuit
motor engine fuel solar nuclear climate weather storm snow ice desert forest island war peace king castle palette tag
""".split()


def test_common_teaching_keywords_have_icons() -> None:
    missing = [w for w in TEACHING_WORDS if ic.icon_for(w) is None]
    assert missing == []
    assert len(TEACHING_WORDS) > 200


@pytest.mark.parametrize(("hint", "expected"), [
    ("atom", ("lucide", "atom", "name")), ("Atoms", ("lucide", "atom", "name")),
    ("batteries", ("lucide", "battery", "name")), ("light bulb", ("lucide", "lightbulb", "name")),
    ("TEST_TUBE", ("lucide", "test-tube", "name")), ("home", ("lucide", "house", "name")),
    ("pie chart", ("lucide", "chart-pie", "name")), ("buses", ("lucide", "bus", "name")),
    ("idea", ("lucide", "lightbulb", "tag")), ("energy", ("lucide", "zap", "tag")),
    ("science", ("lucide", "microscope", "tag")), ("warning", ("lucide", "triangle-alert", "tag")),
    ("chemistry", ("lucide", "flask-conical", "tag")), ("time", ("lucide", "clock", "tag")),
    ("lungs", ("tabler", "lungs", "name")), ("flask", ("tabler", "flask", "name")),
    ("palette", ("tabler", "palette", "name")),  # Lucide's palette has filled dots: excluded, Tabler fills the gap
    ("tag", ("tabler", "tag", "name")), ("teacher", ("tabler", "chalkboard-teacher", "tag")),
    ("chart", ("tabler", "chart-bar", "tag")), ("ice", ("tabler", "iceberg", "tag")),
    ("force", ("tabler", "magnetic", "tag")),  # Lucide's only "force" tag is a pun (IGNORED_TAGS)
    ("accessible", ("tabler", "disabled-2", "tag")),  # never accessible-off
])
def test_pinned_matches(hint: str, expected: tuple[str, str, str]) -> None:
    m = ic.match(hint)
    assert m is not None and (m.set, m.name, m.via) == expected


@pytest.mark.parametrize("hint", ["photosynthesis", "zzzz", "quiz", "algorithm", "தமிழ்", "naïve", "", "   ",
                                  "x" * 25, None, 3, ["atom"]])
def test_no_match_is_no_icon(hint: object) -> None:
    assert ic.match(hint) is None and ic.icon_for(hint) is None


def test_an_icon_is_a_valid_look_entry() -> None:
    icon = ic.icon_for("atom")
    assert icon is not None
    assert (icon.name, icon.set, icon.version, icon.viewBox, icon.stroke_width, icon.filled) == (
        "atom", "lucide", "1.48.0", (0.0, 0.0, 24.0, 24.0), 2.0, False)
    look = ResolvedLook.model_validate({"template": "list_build", "tokens": {}, "background": {},
                                        "transition_in": {}, "icons": {"i1": icon.model_dump(mode="json")}})
    again = ResolvedLook.model_validate_json(look.model_dump_json())
    assert again.icons["i1"] == icon and json.loads(look.model_dump_json())["icons"]["i1"]["paths"] == icon.paths
    assert ic.icon_for("atom") == icon and ic.icon_for("atom") is not icon  # a fresh model every call


HINTS_FOR_SEEDS = [*TEACHING_WORDS, "atoms", "batteries", "light bulb", "pie chart", "TEST_TUBE", "zzzz"]
_SEED_PROBE = """
import json, sys
from aadhi.compose.dynamic import icons as ic
hints = json.loads(sys.stdin.read())
print(json.dumps([None if (m := ic.match(h)) is None else [m.set, m.name, m.via, m.form] for h in hints]))
"""


def test_the_match_is_deterministic_across_hash_seeds() -> None:
    results = []
    for seed in ("0", "1", "4242"):
        proc = subprocess.run([sys.executable, "-c", _SEED_PROBE], cwd=ROOT, input=json.dumps(HINTS_FOR_SEEDS),
                              env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True,
                              timeout=120, check=False)
        assert proc.returncode == 0, proc.stderr
        results.append(json.loads(proc.stdout))
    here = [None if (m := ic.match(h)) is None else [m.set, m.name, m.via, m.form] for h in HINTS_FOR_SEEDS]
    assert results[0] == results[1] == results[2] == here


@needs_node_modules
def test_pruning_changes_no_result(tmp_path: Path) -> None:
    full, pruned = ic.build_index(prune=False), ic.build_index(prune=True)  # both fresh: the generator is under test
    path, pruned_path = tmp_path / "unpruned.json", tmp_path / "pruned.json"
    path.write_text(ic.render_index(data=full), encoding="utf-8")
    pruned_path.write_text(ic.render_index(data=pruned), encoding="utf-8")
    keys: set[str] = set()
    for s in ic.SET_ORDER:
        for k in ("icons", "aliases", "tags"):
            keys |= set(full["sets"][s][k])
    hints = sorted(keys | {f"{k}s" for k in keys} | {k.replace("-", " ") for k in keys})
    hints = [h for h in hints if len(h) <= ic.HINT_MAX_CHARS]
    assert len(hints) > 20000
    for h in hints:
        assert ic.match(h, path=pruned_path) == ic.match(h, path=path), h
    for s in ic.SET_ORDER:  # pruning did something, in both sets
        assert sum(map(len, (pruned["sets"][s][k] for k in ("icons", "tags")))) < sum(
            map(len, (full["sets"][s][k] for k in ("icons", "tags")))), s


def test_the_reviewed_lists_are_live(index: dict[str, Any]) -> None:
    for s, prefs in ic.PREFERRED.items():
        for tag, name in prefs.items():
            assert index["sets"][s]["tags"].get(tag) == name, (s, tag)
            m = ic.match(tag)
            assert m is not None and (m.set, m.name, m.via) == (s, name, "tag"), (s, tag)
    for s, ignored in ic.IGNORED_TAGS.items():
        for tag in ignored:
            assert tag not in index["sets"][s]["tags"], (s, tag)


# --- scene_icons -----------------------------------------------------------------------------------------------------


def test_scene_icons_follow_item_order_and_the_cap() -> None:
    items = [BoardItem(id="b1", kind="bullet", text="x", icon_hint="atom"),
             BoardItem(id="b2", kind="bullet", text="x"),
             BoardItem(id="b3", kind="bullet", text="x", icon_hint="photosynthesis"),
             BoardItem(id="b4", kind="bullet", text="x", icon_hint="lungs"),
             SimpleNamespace(id="b1", icon_hint="lungs"),  # an id seen twice keeps its first icon
             SimpleNamespace(id=None, icon_hint="atom"), SimpleNamespace(icon_hint="atom")]
    out = ic.scene_icons(items)
    assert list(out) == ["b1", "b4"] and out["b1"].name == "atom" and out["b4"].name == "lungs"
    many = [SimpleNamespace(id=f"i{k}", icon_hint=w) for k, w in enumerate(TEACHING_WORDS[:ICONS_PER_SCENE_MAX + 8])]
    capped = ic.scene_icons(many)
    assert list(capped) == [f"i{k}" for k in range(ICONS_PER_SCENE_MAX)]
    ResolvedLook.model_validate({"template": "x", "tokens": {}, "background": {}, "transition_in": {},
                                 "icons": {k: v.model_dump() for k, v in capped.items()}})


# --- run-time cost and isolation -------------------------------------------------------------------------------------

_RUNTIME_PROBE = """
import sys, time
import aadhi.schemas.timeline  # the module's one dependency, imported before the baseline
before = set(sys.modules)
from aadhi.compose.dynamic import icons as ic
c0 = time.process_time()
assert ic.icon_for("atom") is not None
cpu = time.process_time() - c0
print(sorted(m for m in set(sys.modules) - before if m.startswith(("xml", "pyexpat"))), cpu)
"""


def test_the_runtime_path_reads_json_only_and_is_quick() -> None:
    proc = subprocess.run([sys.executable, "-c", _RUNTIME_PROBE], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    modules, cpu = proc.stdout.strip().rsplit(" ", 1)
    assert modules == "[]"  # no XML parser on the run-time path
    assert float(cpu) < 0.5, cpu  # the first lookup loads the index (~25 ms here; the preview budget is 300 ms)


# --- the generator on synthetic packages ----------------------------------------------------------------------------

LUCIDE_LICENCE = ("ISC License\n\nCopyright (c) 2026 Lucide Icons and Contributors\n\nPermission to use ...\n\n---\n\n"
                  "The MIT License (MIT) (for the icons listed above)\n\nCopyright (c) 2013-present Cole Bemis\n")
TABLER_LICENCE = "MIT License\n\nCopyright (c) 2020-2026 Paweł Kuna\n\nPermission is hereby granted ...\n"
FAKE_LUCIDE = {
    "atom": ('<circle cx="12" cy="12" r="1"/><path d="M1 1L2 2"/>', ["science", "physics"]),
    "flask-conical": ('<path d="M3 3L4 4"/>', ["science", "chemistry", "lab"]),
    "bell": ('<path d="M5 5L6 6"/>', ["alarm", "ring"]),
    "bell-off": ('<path d="M5 5L6 7"/>', ["alarm", "quiet", "mute"]),
    "palette": ('<circle cx="7" cy="7" r=".5" fill="currentColor"/><path d="M1 1L9 9"/>', ["art"]),
    "house": ('<path d="M7 7L8 8"/>', ["home", "building"]),
}
FAKE_LUCIDE_ALIASES = {"home": "house", "palette-old": "palette"}
FAKE_TABLER = {
    "atom": ('<path d="M1 1L3 3"/>', ["science"], "Science"),
    "lungs": ('<path d="M2 2L3 3"/>', ["organ", "breath"], "Health"),
    "brand-github": ('<path d="M2 2L5 5"/>', ["code"], "Brand"),
    "dice-1": ('<path d="M1 1L2 2" fill="currentColor"/>', ["game"], "Games"),
    "mute": ('<path d="M4 4L5 5"/>', ["quiet", 2], "Media"),
}


def _write_fake(root: Path, *, lucide_version: str = "1.48.0", lucide_licence: str = "ISC",
                lucide_text: str = LUCIDE_LICENCE, extra_tags: dict[str, list[str]] | None = None,
                aliases: dict[str, str] | None = None) -> Path:
    lucide = root / "lucide-static"
    (lucide / "icons").mkdir(parents=True)
    (lucide / "package.json").write_text(json.dumps({"version": lucide_version, "license": lucide_licence}))
    (lucide / "LICENSE").write_text(lucide_text, encoding="utf-8")
    tags = {n: t for n, (_, t) in FAKE_LUCIDE.items()}
    tags.update(extra_tags or {})
    (lucide / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    for n, (body, _) in FAKE_LUCIDE.items():
        (lucide / "icons" / f"{n}.svg").write_text(f"<!-- @license -->\n{svg(body)}", encoding="utf-8")
    for alias, target in (FAKE_LUCIDE_ALIASES if aliases is None else aliases).items():
        body = FAKE_LUCIDE[target][0] if target in FAKE_LUCIDE else target
        (lucide / "icons" / f"{alias}.svg").write_text(svg(body), encoding="utf-8")
    tabler = root / "@tabler" / "icons"
    (tabler / "icons" / "outline").mkdir(parents=True)
    (tabler / "package.json").write_text(json.dumps({"version": "3.48.0", "license": "MIT"}))
    (tabler / "LICENSE").write_text(TABLER_LICENCE, encoding="utf-8")
    meta = {n: {"name": n, "category": c, "tags": t, "styles": {"outline": {}}} for n, (_, t, c) in FAKE_TABLER.items()}
    (tabler / "icons.json").write_text(json.dumps(meta), encoding="utf-8")
    for n, (body, _, _) in FAKE_TABLER.items():
        (tabler / "icons" / "outline" / f"{n}.svg").write_text(
            svg('<path stroke="none" d="M0 0h24v24H0z" fill="none"/>' + body), encoding="utf-8")
    return root


@pytest.fixture()
def no_reviewed_lists(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(ic, "PREFERRED", {"lucide": {}, "tabler": {}})
    monkeypatch.setattr(ic, "IGNORED_TAGS", {"lucide": frozenset(), "tabler": frozenset()})
    yield


@pytest.fixture()
def fake_modules(tmp_path: Path, no_reviewed_lists: None) -> Path:
    return _write_fake(tmp_path / "node_modules")


def test_the_generator_on_synthetic_packages(fake_modules: Path) -> None:
    data = ic.build_index(fake_modules)
    lucide, tabler = data["sets"]["lucide"], data["sets"]["tabler"]
    assert list(lucide["icons"]) == ["atom", "bell", "bell-off", "flask-conical", "house"]
    assert lucide["icons"]["atom"] == ["M13 12A1 1 0 0 1 12 13A1 1 0 0 1 11 12A1 1 0 0 1 12 11A1 1 0 0 1 13 12Z",
                                       "M1 1L2 2"]
    assert lucide["aliases"] == {"home": "house"}  # a deprecated file, matched by identical source
    assert lucide["excluded"] == {"palette": "a filled <circle> (one fill per icon cannot express it)",
                                  "palette-old": "alias of palette: a filled <circle> (one fill per icon cannot "
                                                 "express it)"}
    # tags: a crossed-out icon never stands for a tag (bell-off is not "alarm"; "quiet" and "mute" have no entry);
    # the tag a name owns is pruned ("home" is a Lucide name); ties go to the name containing the tag, then fewest words
    assert lucide["tags"] == {"building": "house", "chemistry": "flask-conical", "lab": "flask-conical",
                              "physics": "atom", "ring": "bell", "science": "atom", "alarm": "bell"}
    assert set(tabler["excluded"]) == {"brand-github", "dice-1"}
    assert tabler["excluded"]["brand-github"] == "category Brand (trade marks)"
    # Tabler keeps what Lucide does not cover: its names (lungs, mute) and its tags no earlier tier owns; its atom is
    # shadowed by Lucide's and reached by nothing else, so pruned
    assert list(tabler["icons"]) == ["lungs", "mute"]
    assert tabler["tags"] == {"2": "mute", "breath": "lungs", "organ": "lungs", "quiet": "mute"}
    assert tabler["aliases"] == {}


def test_tie_breaks(tmp_path: Path, no_reviewed_lists: None) -> None:
    root = _write_fake(tmp_path / "nm", extra_tags={})
    data = ic.build_index(root, prune=False)
    assert data["sets"]["lucide"]["tags"]["science"] == "atom"  # fewest words (atom vs flask-conical)
    assert "quiet" not in data["sets"]["lucide"]["tags"]  # only bell-off carries it: no entry


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"lucide_version": "1.49.0"}, "is installed"),
    ({"lucide_licence": "MIT"}, "declares licence"),
    ({"lucide_text": "ISC License\n\nCopyright (c) 2026 Somebody\n"}, "lacks the line"),
    ({"extra_tags": {"ghost": ["x"]}}, "without a file"),
    ({"aliases": {"stray": '<path d="M9 9L9 1"/>'}}, "matches 0 listed icons"),
    ({"aliases": {"broken": '<path d="M9 9L9 1"/><g/>'}}, "matches 0 listed icons"),
    ({"aliases": {"evil": "<script/>"}}, "matches 0 listed icons"),
    ({"aliases": {"Old_House": "house"}}, "is not a plain id"),
])
def test_the_generator_fails_closed(tmp_path: Path, no_reviewed_lists: None, kwargs: dict[str, Any],
                                    message: str) -> None:
    root = _write_fake(tmp_path / "nm", **kwargs)
    with pytest.raises(ic.IndexBuildError, match=message):
        ic.build_index(root)


def test_an_unreadable_or_ambiguous_unlisted_file_fails(tmp_path: Path, no_reviewed_lists: None) -> None:
    root = _write_fake(tmp_path / "nm")
    (root / "lucide-static" / "icons" / "dup.svg").write_text(svg('<path d="M1 1L2 2"/>'), encoding="utf-8")
    (root / "lucide-static" / "tags.json").write_text(json.dumps(
        {**{n: t for n, (_, t) in FAKE_LUCIDE.items()}, "dup": []}), encoding="utf-8")
    (root / "lucide-static" / "icons" / "dup-old.svg").write_text(svg('<path d="M1 1L2 2"/>'), encoding="utf-8")
    (root / "lucide-static" / "icons" / "atom.svg").write_text(svg('<path d="M1 1L2 2"/>'), encoding="utf-8")
    with pytest.raises(ic.IndexBuildError, match="matches 2 listed icons"):
        ic.build_index(root)
    (root / "lucide-static" / "icons" / "dup-old.svg").write_text("<!DOCTYPE x>" + svg(""), encoding="utf-8")
    with pytest.raises(ic.IndexBuildError, match="cannot be read"):
        ic.build_index(root)


@pytest.mark.parametrize(("preferred", "ignored", "message"), [
    ({"lucide": {"science": "bell"}, "tabler": {}}, {}, "not a candidate"),
    ({"lucide": {"nothing": "atom"}, "tabler": {}}, {}, "not a candidate"),
    ({"lucide": {}, "tabler": {}}, {"lucide": frozenset({"nothing"})}, "is not a tag"),
    ({"lucide": {}, "tabler": {"science": "atom"}}, {}, "an earlier tier always wins"),  # Lucide owns "science"
    ({"lucide": {}, "tabler": {}}, {"tabler": frozenset({"science"})}, "an earlier tier always wins"),
    ({"lucide": {"home": "house"}, "tabler": {}}, {}, "an earlier tier always wins"),  # a Lucide name owns "home"
])
def test_stale_or_shadowed_reviewed_entries_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 preferred: dict[str, dict[str, str]],
                                                 ignored: dict[str, frozenset[str]], message: str) -> None:
    root = _write_fake(tmp_path / "nm")
    monkeypatch.setattr(ic, "PREFERRED", preferred)
    monkeypatch.setattr(ic, "IGNORED_TAGS", {"lucide": frozenset(), "tabler": frozenset(), **ignored})
    with pytest.raises(ic.IndexBuildError, match=message):
        ic.build_index(root)


def test_a_reviewed_entry_decides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_fake(tmp_path / "nm")
    monkeypatch.setattr(ic, "PREFERRED", {"lucide": {"science": "flask-conical"}, "tabler": {}})
    monkeypatch.setattr(ic, "IGNORED_TAGS", {"lucide": frozenset({"physics"}), "tabler": frozenset({"organ"})})
    data = ic.build_index(root)
    assert data["sets"]["lucide"]["tags"]["science"] == "flask-conical"
    assert "physics" not in data["sets"]["lucide"]["tags"] and "organ" not in data["sets"]["tabler"]["tags"]
