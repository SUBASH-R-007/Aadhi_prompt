"""The one face table (aadhi.compose.dynamic.faces -> web/css/dynamic-fonts.css and web/js/dynamic/faces.gen.js;
design §4.6, §4.12; plan B2 §2 "Fonts", §1.3).

* The table: the §4.6 families, each vendored one pinned in package.json at the version the table names; the licence
  read from the package's LICENSE (OFL 1.1 text) and copied beside the B2b faces; the W2 faces listed, not vendored.
* The classic rows are web/vendor/fonts.css's and scripts/vendor.mjs's (FONTS list and range table), row for row and in
  declaration order, so the Python side sees what the page sees; the B2b rows' ranges are their packages' unicode.json.
* Every face file is present under web/vendor and Pillow reads it (FreeType, Layout.BASIC); a variable face's axes are
  the file's (Anek's wght default is 500, so a measurement must set it).
* What a timeline draws (``stage_roles``, ``stack``, ``css_stack``): the B1 form as the B1 stage drew it, the B2 form
  from the table, the resolver's fonts (``resolver_fonts``) the B1 stage's faces for every display id; the CSS
  strings are the ones web/tests/dynamic/fonts.test.js pins for fonts.js.
* CSS font matching for text fit (``match_weight``, ``face_for``): CSS Fonts 4 §5.2 weights, last-declared range.
* The generated files are current, deterministic, and ``--check`` fails on a stale copy.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import themes as th

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web"
VENDOR = WEB / "vendor"
NODE_MODULES = ROOT / "node_modules"
needs_vendor = pytest.mark.skipif(not (VENDOR / "fonts.css").is_file(), reason="web/vendor is not vendored (npm ci)")
needs_node_modules = pytest.mark.skipif(not NODE_MODULES.is_dir(), reason="node_modules is not installed (npm ci)")


# --- the table ------------------------------------------------------------------------------------------------------


def test_the_families_are_the_designs() -> None:
    assert fc.DISPLAY_FACES_R1 == th.DISPLAY_FACES_R1 == ("outfit", "poppins", "lexend")
    assert th.FACES is fc.FAMILIES
    vendored = {f.id for f in fc.FAMILIES.values() if f.vendored}
    assert vendored == {"outfit", "poppins", "lexend", "inter", "atkinson_hyperlegible_next", "jetbrains_mono",
                        *(f"noto_sans_{s}" for s in fc.INDIC_SCRIPTS), *(f"anek_{s}" for s in fc.INDIC_SCRIPTS)}
    assert {f.id for f in fc.FAMILIES.values() if f.css == "classic"} == {
        "outfit", "inter", "jetbrains_mono", *(f"noto_sans_{s}" for s in fc.INDIC_SCRIPTS)}
    for f in fc.FAMILIES.values():
        assert f.role in fc.ROLES
        if f.vendored:
            assert f.release == "R1" and f.licence == fc.OFL and f.package and f.version == fc.FONTSOURCE_VERSION, f.id
            assert f.requests and (bool(f.weights) != f.variable), f.id
            assert set(f.requests) <= set(f.weights) or f.variable, f.id
            assert (f.role == "indic") == (f.script is not None), f.id
        else:
            assert f.release == "W2" and f.role == "display" and f.package is None and not fc.faces_of(f.id), f.id
            assert f.licence.startswith("OFL-1.1 (believed"), f.id
    assert fc.FAMILIES["anek_tamil"].axes == (fc.Axis("wght", 100, 800, 500),)
    assert fc.FAMILIES["poppins"].subsets == ("latin", "latin-ext"), "Indic text is the Indic family's (§4.6)"
    for group, per_script in fc.INDIC_GROUPS.items():
        assert tuple(per_script) == fc.INDIC_SCRIPTS, group
        assert all(fc.FAMILIES[fid].vendored and fc.FAMILIES[fid].script == s for s, fid in per_script.items())
    assert fc.DEFAULT_FONTS == {"display": "outfit", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}


def test_every_vendored_package_is_pinned_exactly_in_package_json() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    for f in fc.FAMILIES.values():
        if f.vendored:
            assert package["dependencies"].get(f.package) == f.version, f.package  # exact, never a range
            assert lock["packages"][f"node_modules/{f.package}"]["version"] == f.version, f.package
            assert lock["packages"][f"node_modules/{f.package}"]["license"] == "OFL-1.1", f.package


def test_the_rows() -> None:
    assert len(fc.FACES) == len({(r.family, r.subset, r.weight) for r in fc.FACES}) == len({r.file for r in fc.FACES})
    for r in fc.FACES:
        f = fc.FAMILIES[r.family]
        assert r.file.startswith(f"{f.directory}/") and r.file.endswith(".woff2") and r.source.startswith(f.package)
        assert r.subset in f.subsets and fc.parse_unicode_range(r.unicode_range)
        assert r.weight == ((f.axes[0].minimum, f.axes[0].maximum) if f.variable else (r.weight[0], r.weight[0]))
    assert {r.file for r in fc.FACES if fc.FAMILIES[r.family].css == "dynamic"} == {
        "fonts/dynamic/anek_tamil/anek-tamil-tamil-wght-normal.woff2",
        "fonts/dynamic/anek_devanagari/anek-devanagari-devanagari-wght-normal.woff2",
        "fonts/dynamic/anek_telugu/anek-telugu-telugu-wght-normal.woff2",
        "fonts/dynamic/anek_kannada/anek-kannada-kannada-wght-normal.woff2",
        "fonts/dynamic/anek_malayalam/anek-malayalam-malayalam-wght-normal.woff2",
        *(f"fonts/dynamic/{d}/{stem}-{s}-{w}-normal.woff2" for d, stem, ws in (
            ("poppins", "poppins", (400, 600, 700, 900)), ("lexend", "lexend", (400, 600, 700, 900)),
            ("atkinson_hyperlegible_next", "atkinson-hyperlegible-next", (400, 600, 700)))
          for w in ws for s in ("latin", "latin-ext"))}
    with pytest.raises(ValueError):
        fc.parse_unicode_range("U+00FF-0000")
    with pytest.raises(ValueError):
        fc.parse_unicode_range("U+00??")
    assert fc.parse_unicode_range("U+0000-00FF, U+0131") == ((0, 0xFF), (0x131, 0x131))


def _vendor_mjs() -> str:
    return (ROOT / "scripts" / "vendor.mjs").read_text(encoding="utf-8")


def test_the_classic_rows_are_vendor_mjs_s_list_and_range_table() -> None:
    """vendor.mjs (FONTS and unicodeRange) writes web/vendor/fonts.css; the table describes that file exactly."""
    text = _vendor_mjs()
    fonts = re.findall(r"\{ pkg: '([\w-]+)', family: '([^']+)', weights: \[([\d, ]+)\] \}", text)
    classic = [f for f in fc.FAMILIES.values() if f.css == "classic"]
    assert sorted((p, n, tuple(int(w) for w in ws.split(","))) for p, n, ws in fonts) == sorted(
        (f.file_stem, f.name, f.weights) for f in classic)  # (the order across families is irrelevant to matching)
    table = dict(re.findall(r"^\s+'?([\w-]+)'?: '(U\+[^']+)',$", text, re.M))
    assert table == fc._CLASSIC_RANGES
    allowed = re.search(r"if \(!\[([^\]]+)\]\.includes\(subset\)\) continue;", text)
    assert allowed and {s.strip(" '") for s in allowed.group(1).split(",")} == {s for f in classic for s in f.subsets}


@needs_vendor
def test_the_classic_rows_are_web_vendor_fonts_css_in_declaration_order() -> None:
    css = (VENDOR / "fonts.css").read_text(encoding="utf-8")
    rows = re.findall(r"@font-face\{font-family:'([^']+)';font-style:normal;font-weight:(\d+);font-display:block;"
                      r"src:url\('\./([^']+)'\) format\('woff2'\);unicode-range:([^;]+);\}", css)
    assert len(rows) == css.count("@font-face")
    by_name = {f.name: f.id for f in fc.FAMILIES.values()}
    declared: dict[str, list[tuple[str, int, str]]] = {}
    for name, weight, file, ranges in rows:
        declared.setdefault(by_name[name], []).append((file, int(weight), ranges))
    for fid in declared:
        assert fc.FAMILIES[fid].css == "classic", fid
        assert [(r.file, r.weight[0], r.unicode_range) for r in fc.faces_of(fid)] == [
            (file, w, ranges) for file, w, ranges in declared[fid]], fid
    assert set(declared) == {f.id for f in fc.FAMILIES.values() if f.css == "classic"}


@needs_node_modules
def test_the_b2b_rows_are_their_packages() -> None:
    """The ranges are each package's unicode.json (vendor.mjs refuses a mismatch too); the sources exist; the installed
    version is the pinned one."""
    for f in fc.FAMILIES.values():
        if f.css != "dynamic":
            continue
        pkg = NODE_MODULES / f.package
        assert json.loads((pkg / "package.json").read_text(encoding="utf-8"))["version"] == f.version
        ranges = json.loads((pkg / "unicode.json").read_text(encoding="utf-8"))
        for r in fc.faces_of(f.id):
            assert ranges[r.subset] == r.unicode_range, r.file
            assert (NODE_MODULES / r.source).is_file(), r.source


@needs_node_modules
def test_every_vendored_package_s_licence_is_the_ofl() -> None:
    """§4.6 "verify each LICENSE file when vendoring": the package's own LICENSE holds the OFL 1.1 text and the
    family's copyright line, with no Reserved Font Name."""
    for f in fc.FAMILIES.values():
        if not f.vendored:
            continue
        text = (NODE_MODULES / f.package / "LICENSE").read_text(encoding="utf-8")
        assert fc.OFL_TEXT_MARK in text and "Copyright" in text.split("\n", 1)[0], f.package
        # no Reserved Font Name in the copyright statement (OFL §3: a modified version, such as Fontsource's
        # unicode-range subsets, could not keep a reserved name)
        header = text.split("This Font Software is licensed", 1)[0]
        assert header.strip() and "reserved" not in header.lower(), f.package


@needs_vendor
def test_every_face_file_is_vendored_readable_and_licensed() -> None:
    from PIL import ImageFont

    for r in fc.FACES:
        path = fc.web_path(r)
        assert path == VENDOR / r.file and path.is_file(), r.file
        font = ImageFont.truetype(str(path), 48, layout_engine=ImageFont.Layout.BASIC)
        family = fc.FAMILIES[r.family]
        assert font.getname()[0].startswith(family.name), (r.file, font.getname())
        sample = {"latin": "A", "latin-ext": "Ā", "tamil": "த", "devanagari": "क", "telugu": "క",
                  "kannada": "ಕ", "malayalam": "ക"}[r.subset]
        assert r.covers(ord(sample)) and font.getlength(sample) > 0, r.file
        if family.variable:
            axes = font.get_variation_axes()
            assert [(a["name"], a["minimum"], a["maximum"], a["default"]) for a in axes] == [
                (b"Weight", ax.minimum, ax.maximum, ax.default) for ax in family.axes], r.file
    for f in fc.FAMILIES.values():
        if f.css == "dynamic":
            licence = VENDOR / f.directory / "LICENSE"
            assert licence.read_bytes() == (NODE_MODULES / f.package / "LICENSE").read_bytes(), f.id
            assert fc.OFL_TEXT_MARK in licence.read_text(encoding="utf-8")
            on_disk = {p.name for p in (VENDOR / f.directory).iterdir()}
            assert on_disk == {"LICENSE", *(Path(r.file).name for r in fc.faces_of(f.id))}, f.id


@needs_vendor
def test_the_classic_burn_in_font_copy_is_unchanged() -> None:
    """video._copy_fonts copies the files of web/vendor/fonts (not its sub-directories) into libass burn-ins: the B2b
    faces live in fonts/dynamic/, so the classic copy holds exactly the classic faces (plan §2, classic isolation)."""
    files = {p.name for p in (VENDOR / "fonts").iterdir() if p.is_file()}
    assert files == {Path(r.file).name for r in fc.FACES if fc.FAMILIES[r.family].css == "classic"}
    assert (VENDOR / "fonts" / "dynamic").is_dir()


# --- what a timeline draws --------------------------------------------------------------------------------------------

B1_STACK_TAIL = ("'Noto Sans Tamil', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Kannada', "
                 "'Noto Sans Malayalam', sans-serif")


def test_b1_form_is_drawn_as_the_b1_stage_drew_it() -> None:
    for display, drawn in (("outfit", "outfit"), ("inter", "inter"), ("poppins", "outfit"), ("montserrat", "outfit"),
                           ("anek", "outfit"), (None, "outfit"), ("constructor", "outfit")):
        fonts = {"display": display, "body": "atkinson_hyperlegible_next", "mono": "x"}
        assert not fc.is_b2_fonts(fonts)
        assert fc.stage_roles(fonts) == {"display": drawn, "body": "inter", "mono": "jetbrains_mono",
                                         "indic": "noto_sans"}
    assert fc.css_stack({"display": "poppins"}, "display") == f"'Outfit', 'Inter', {B1_STACK_TAIL}"
    assert fc.css_stack({"display": "inter"}, "display") == f"'Inter', 'Inter', {B1_STACK_TAIL}"
    assert fc.css_stack(None, "body") == f"'Inter', {B1_STACK_TAIL}"
    assert fc.css_stack({}, "mono") == "'JetBrains Mono', ui-monospace, monospace"
    assert fc.css_stack({}, "body", "hi-IN").startswith("'Inter', 'Noto Sans Devanagari', 'Noto Sans Tamil', ")
    assert fc.stage_roles({"indic": 7}) == fc.stage_roles({})


def test_the_resolver_names_exactly_the_faces_the_b1_stage_drew() -> None:
    for display in ("outfit", "inter", "poppins", "lexend", "montserrat", "jetbrains_mono", "anek", "nope"):
        stored = fc.resolver_fonts(display)
        assert fc.is_b2_fonts(stored) and list(stored) == ["display", "body", "mono", "indic"]
        assert stored == {"display": "inter" if display == "inter" else "outfit", "body": "inter",
                          "mono": "jetbrains_mono", "indic": "noto_sans"}
        for role in ("display", "body", "mono"):
            for lang in ("", "ta-IN", "hi", "ml-IN", "xx"):
                assert fc.css_stack(stored, role, lang) == fc.css_stack({"display": display}, role, lang)


def test_b2_form_draws_what_it_names_when_vendored_for_the_role() -> None:
    fonts = {"display": "poppins", "body": "atkinson_hyperlegible_next", "mono": "jetbrains_mono", "indic": "anek"}
    assert fc.stage_roles(fonts) == fonts
    assert fc.stack(fonts, "display", "hi-IN") == ("poppins", "atkinson_hyperlegible_next", "anek_devanagari",
                                                   "anek_tamil", "anek_telugu", "anek_kannada", "anek_malayalam")
    # the strings web/tests/dynamic/fonts.test.js pins for fonts.js
    assert fc.css_stack(fonts, "display", "hi-IN") == (
        "'Poppins', 'Atkinson Hyperlegible Next', 'Anek Devanagari', 'Anek Tamil', 'Anek Telugu', 'Anek Kannada', "
        "'Anek Malayalam', sans-serif")
    assert fc.css_stack(fonts, "mono") == "'JetBrains Mono', ui-monospace, monospace"
    assert fc.stack({"display": "inter", "indic": "noto_sans"}, "display")[:2] == ("inter", "inter")
    for bad in ("montserrat", "jetbrains_mono", "noto_sans_tamil", "anek_tamil", "nope", "constructor", "", 7, None):
        assert fc.stage_roles({"display": bad, "body": bad, "mono": bad, "indic": "x"}) == fc.DEFAULT_FONTS, bad
    assert fc.stage_roles({"display": "inter", "body": "inter", "mono": "inter", "indic": "anek"})["mono"] == (
        "jetbrains_mono")


def test_languages_and_indic_order() -> None:
    assert fc.script_for_language("ta-IN") == "tamil" and fc.script_for_language("MR_in") == "devanagari"
    assert fc.script_for_language("sa") is None and fc.script_for_language(None) is None
    for group in fc.INDIC_GROUPS:
        assert fc.indic_families(group) == tuple(fc.INDIC_GROUPS[group][s] for s in fc.INDIC_SCRIPTS)
        for prefix, script in fc.LANGUAGE_SCRIPTS.items():
            order = fc.indic_families(group, prefix)
            assert order[0] == fc.INDIC_GROUPS[group][script] and len(set(order)) == len(fc.INDIC_SCRIPTS)


# --- CSS font matching (text fit) -------------------------------------------------------------------------------------


def _w(*ws: int) -> tuple[tuple[int, int], ...]:
    return tuple((w, w) for w in ws)


@pytest.mark.parametrize(("available", "desired", "matched"), [
    (_w(300, 400, 600, 700), 400, 400), (_w(300, 400, 600, 700), 500, 400),  # 400-500: up to 500, then down
    (_w(300, 600, 700), 450, 300), (_w(600, 700), 450, 600),  # ... then above 500
    (_w(400, 450, 600), 420, 450),  # ... the first one up to 500
    (_w(300, 400, 700, 900), 600, 700), (_w(300, 400, 700, 900), 800, 900), (_w(300, 400, 600, 700), 900, 700),
    (_w(300, 400, 600, 700), 200, 300), (_w(300, 400), 350, 300), (_w(400, 700), 100, 400),
])
def test_match_weight_follows_css_fonts_4(available: tuple[tuple[int, int], ...], desired: int, matched: int) -> None:
    assert fc.match_weight(available, desired) == (matched, matched)


def test_match_weight_with_a_variable_range() -> None:
    assert fc.match_weight(((100, 800),), 900) == (100, 800) and fc.match_weight(((100, 800),), 50) == (100, 800)
    assert fc.match_weight(((100, 800),), 600) == (100, 800) and fc.match_weight((), 400) is None


def test_face_for_resolves_like_the_page() -> None:
    latin = fc.face_for("inter", 400, ord("A"))
    assert latin is not None and latin.file == "fonts/inter-latin-400-normal.woff2"
    ext = fc.face_for("inter", 900, 0x100)
    assert ext is not None and ext.file == "fonts/inter-latin-ext-700-normal.woff2"  # 900 -> 700
    # U+0308 is in both Latin ranges: the LAST declared rule wins (CSS Fonts 4 §4.5)
    both = fc.face_for("outfit", 600, 0x308)
    assert both is not None and both.subset == "latin-ext" and both.weight == (700, 700)
    assert fc.face_for("inter", 400, 0x0BA4) is None  # Tamil: the next family in the stack
    danda = fc.face_for("noto_sans_devanagari", 700, 0x0964)
    assert danda is not None and danda.subset == "devanagari"
    anek = fc.face_for("anek_tamil", 900, 0x0BA4)
    assert anek is not None and anek.weight == (100, 800)
    assert fc.face_for("anek_tamil", 400, ord("A")) is None  # only the script subset is vendored
    assert fc.face_for("montserrat", 400, ord("A")) is None and fc.face_for("nope", 400, ord("A")) is None


# --- the generated files ----------------------------------------------------------------------------------------------


def test_the_committed_files_are_current() -> None:
    """The CI step (``--check``): run ``python -m aadhi.compose.dynamic.faces`` after changing the table."""
    assert fc.CSS_OUTPUT.read_bytes().decode("utf-8") == fc.render_css()
    assert fc.JS_OUTPUT.read_bytes().decode("utf-8") == fc.render_js()
    assert fc.main(["--check"]) == 0


def test_the_css_declares_the_b2b_faces_only() -> None:
    css = fc.render_css()
    rules = [ln for ln in css.split("\n") if ln.startswith("@font-face")]
    assert len(rules) == sum(1 for r in fc.FACES if fc.FAMILIES[r.family].css == "dynamic") == 27
    for name in ("Inter", "Outfit", "JetBrains Mono", "Noto Sans"):
        assert f"font-family:'{name}" not in css  # the classic families stay in web/vendor/fonts.css
    assert ("@font-face{font-family:'Anek Tamil';font-style:normal;font-weight:100 800;font-display:block;"
            "src:url('../vendor/fonts/dynamic/anek_tamil/anek-tamil-tamil-wght-normal.woff2') format('woff2');"
            "unicode-range:U+0964-0965,U+0B82-0BFA,U+200C-200D,U+20B9,U+25CC;}") in rules
    assert all("font-display:block" in r and "url('../vendor/fonts/dynamic/" in r for r in rules)
    assert css.endswith(";}\n") and "\r" not in css


def test_the_js_table_shape() -> None:
    data = fc.table()
    assert fc.FACES_VERSION == 1 and data["version"] == 1 and tuple(data) == fc.JS_TOP_LEVEL_KEYS
    assert data["b1_display"] == ["outfit", "inter"] and data["scripts"] == list(fc.INDIC_SCRIPTS)
    assert set(data["families"]) == set(fc.FAMILIES) and len(data["faces"]) == len(fc.FACES)
    assert data["families"]["anek_tamil"]["axes"] == [{"tag": "wght", "min": 100, "max": 800, "default": 500}]
    assert data["families"]["montserrat"]["dir"] is None
    assert data["families"]["poppins"]["dir"] == "fonts/dynamic/poppins"
    text = fc.render_js()
    assert text.startswith("// generated by python -m aadhi.compose.dynamic.faces — do not edit\n// @ts-check\n")
    assert "export const FACES = Object.freeze(freezeInside({" in text and text.endswith("}));\n")
    assert max(len(line) for line in text.split("\n")) <= 120


def test_generation_is_byte_deterministic(tmp_path: Path) -> None:
    outputs = []
    for seed in ("0", "1", "4242"):
        out = tmp_path / seed
        out.mkdir()
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-m", "aadhi.compose.dynamic.faces", "--dir", str(out)], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=120, check=False)
        assert proc.returncode == 0, proc.stderr
        outputs.append(((out / "dynamic-fonts.css").read_bytes(), (out / "faces.gen.js").read_bytes()))
    assert outputs[0] == outputs[1] == outputs[2] == (fc.render_css().encode(), fc.render_js().encode())


def test_check_fails_on_a_stale_or_missing_copy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fc.main(["--dir", str(tmp_path)]) == 0 and "wrote web/css/dynamic-fonts.css" in capsys.readouterr().out
    assert fc.main(["--check", "--dir", str(tmp_path)]) == 0
    js = tmp_path / "faces.gen.js"
    js.write_bytes(js.read_bytes().replace(b'"default": 500', b'"default": 400'))
    assert fc.main(["--check", "--dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "web/js/dynamic/faces.gen.js is out of date" in err and "dynamic-fonts.css" not in err
    (tmp_path / "dynamic-fonts.css").unlink()
    assert fc.main(["--check", "--dir", str(tmp_path)]) == 1
    assert "web/css/dynamic-fonts.css is out of date (first difference at line 1)" in capsys.readouterr().err
