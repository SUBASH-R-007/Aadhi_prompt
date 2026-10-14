"""Generate ``THIRD_PARTY_NOTICES``: the licences of every third-party asset the app serves from ``web/vendor``
(DYNAMIC_VIDEO_DESIGN §4.12, §7.13: "``THIRD_PARTY_NOTICES`` is generated and includes the Apache NOTICE files").

    python scripts/gen_third_party_notices.py            # rewrite THIRD_PARTY_NOTICES
    python scripts/gen_third_party_notices.py --check    # exit 1 when it is out of date (CI "Generated files")

What is covered.

1. **Packages.** ``scripts/vendor.mjs`` copies the browser libraries and fonts into ``web/vendor`` from npm packages,
   and the icon sets' geometry (Lucide, Tabler; B2b) is normalised into ``aadhi/compose/dynamic/icon_index.json``,
   from which the resolver copies an icon into each timeline that shows it (design §4.12). All of them are runtime
   ``dependencies`` in ``package.json``. The notices list every package of the runtime dependency closure that
   ``package-lock.json`` lists (every entry not marked ``dev``), except type declarations (``@types/*``: never
   shipped): one section per package with its name, exact version, declared licence, the packages that require it,
   where its files are vendored when the face table (``aadhi/compose/dynamic/faces.py``) or the icon index
   (``aadhi/compose/dynamic/icons.py``) knows, and the full text of every licence-like file the package ships at its
   top level (``LICENSE*``, ``LICENCE*``, ``COPYING*``, ``NOTICE*``, ``COPYRIGHT*``; the Apache ``NOTICE`` files
   included). A package that ships no such file has its licence text read from the one place it keeps it
   (:data:`FALLBACK_SOURCES`); a package with neither fails generation (fail closed), and so does a package whose
   installed version differs from the lock (run ``npm ci``).

2. **What the vendored files contain** (:data:`VENDORED`, B2b review). A prebuilt browser bundle is built by its
   publisher, and it holds more than the package's own code: the packages the publisher bundled, from the publisher's
   own (dev) dependencies at the versions it built with, which never appear in our lock (``p5.min.js`` bundles
   ``core-js``, ``opentype.js``, ``libtess``, ...), and code it copies or ports from other projects (``three.core.js``
   holds a copy of ``earcut``). Every file ``vendor.mjs`` serves is in :data:`VENDORED` (a test walks ``web/vendor``),
   and each entry lists that third-party code (:class:`Component`): name, the version the bundle holds and the
   evidence for it, the licence, and its licence texts, committed verbatim in ``scripts/third_party_licences/`` (each
   one checked against the published package or source it names) or pointed at the package section of this file
   that holds the same text. A component is listed when the file holds it as a module of the bundle's module table,
   names it in a licence or copyright comment, or says it copies or ports its code; a comment that credits an
   algorithm or a book the code follows ("based on", "algorithm thanks to") copies no code and is recorded as a
   credit, not licensed code. Generation fails closed when the reviewed facts no longer hold: the package was updated
   (its version differs from the reviewed one), a component's evidence (:attr:`Component.markers`) is gone, p5's
   module table names a package no component accounts for, or the file carries a copyright statement the entry does
   not acknowledge. Then review the new bundle and update the table.

The texts are copied as UTF-8 with LF line ends, trailing spaces trimmed and the leading and trailing blank lines
dropped; nothing else is changed. The output is byte-deterministic (packages sorted by name) and needs
``node_modules`` (``npm ci``), which the CI job installs before its generated-files step.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "THIRD_PARTY_NOTICES"
LOCK = ROOT / "package-lock.json"
NODE_MODULES = ROOT / "node_modules"
LICENCE_DIR = ROOT / "scripts" / "third_party_licences"
GENERATOR = "python scripts/gen_third_party_notices.py"
LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)([-._].*)?$", re.IGNORECASE)
EXCLUDED_SCOPES = ("@types/",)  # type declarations: never bundled, never served
# Packages that ship no licence file, and where each keeps its licence text instead: (file, first line that starts
# the text, the line that ends it or None for the end of the file). Both are read from the installed package.
FALLBACK_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "seedrandom": ("README.md", "LICENSE (MIT)", None),
    "javascript-natural-sort": ("naturalSort.js", "/*", " */"),
}
RULE = "=" * 100
# A copyright statement in a vendored file: "Copyright" followed by (c), © or a year, or (c) / © followed by a year.
# Every one must be acknowledged by the file's entry (its package's own, a component's, or a credit's).
COPYRIGHT_STATEMENT = re.compile(r"(?i)\bcopyright\b\s*(?:\(c\)|©|\d{4})|\(c\)\s*\d{4}|©\s*\d{4}")


class NoticeError(RuntimeError):
    """The notices cannot be generated from what is installed (fail closed)."""


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    licence: str
    required_by: tuple[str, ...]
    direct: bool


@dataclass(frozen=True)
class Text:
    """One licence text of a component: a file of :data:`LICENCE_DIR` (verbatim, from ``source``), or the section of
    the package ``section`` in this file, which holds the same text."""

    title: str
    file: str | None = None
    section: str | None = None


@dataclass(frozen=True)
class Component:
    """Third-party code a vendored file contains, beyond its package's own."""

    name: str
    version: str  # the version the file holds ("" when neither the file nor its publisher states one)
    licence: str  # SPDX identifier
    contained: str  # how the file holds it, and the evidence for the version
    texts: tuple[Text, ...]
    markers: tuple[str, ...]  # evidence the file contains it: substrings, or regular expressions after "re:"
    modules: tuple[str, ...] = ()  # names the bundle's browserify module table requires it by (p5)
    notices: tuple[str, ...] = ()  # copyright statements for it the file itself carries


@dataclass(frozen=True)
class Vendored:
    """A file (or a glob of files) ``vendor.mjs`` copies from ``node_modules/<source>`` to ``web/vendor/<served>``."""

    package: str
    version: str  # the package version this entry was reviewed at
    source: str
    served: str
    components: tuple[Component, ...] = ()
    notices: tuple[str, ...] = ()  # the package's own copyright statements the file carries
    credits: tuple[tuple[str, str], ...] = ()  # (statement the file carries, why it is not licensed code)
    licence_file: tuple[str, str] | None = None  # (source, served): the licence file its publisher ships beside it
    module_table: bool = False  # a browserify bundle: every package its module table requires must be a component

    @property
    def is_glob(self) -> bool:
        return any(c in self.source for c in "*?[")


def _lic(title: str, file: str) -> Text:
    return Text(title, file=file)


def _see(title: str, package: str) -> Text:
    return Text(title, section=package)


_P5_LOCK = "p5's package-lock.json at its v1.11.11 tag"
_MATHJS_LOCK = "mathjs' package-lock.json at its v15.2.0 tag"
_MATHJAX_LOCK = "MathJax-src's package-lock.json at its 3.2.2 tag, from which the mathjax 3.2.2 package is built"
_APACHE = "Apache License 2.0 (the full text is in the mathjax section of this file)"

P5_COMPONENTS: tuple[Component, ...] = (
    Component("core-js", "3.6.5", "MIT", f"browserify modules (the core-js/modules/* polyfills); the bundle states "
              f"version 3.6.5, as does {_P5_LOCK}",
              (_lic("LICENSE of the core-js 3.6.5 npm package", "core-js-3.6.5.txt"),),
              ('version:"3.6.5"', "Denis Pushkarev"), modules=("core-js",), notices=("© 2020 Denis Pushkarev",)),
    Component("regenerator-runtime", "0.13.3", "MIT", f"browserify module (regenerator-runtime/runtime); version from "
              f"{_P5_LOCK}", (_lic("LICENSE of the regenerator-runtime 0.13.3 npm package",
                                   "regenerator-runtime-0.13.3.txt"),),
              ("regeneratorRuntime",), modules=("regenerator-runtime",)),
    Component("opentype.js", "0.9.0", "MIT", f"browserify module (its prebuilt dist file); version from its banner and "
              f"{_P5_LOCK}", (_lic("LICENSE of the opentype.js 0.9.0 npm package", "opentype.js-0.9.0.txt"),),
              ('"opentype.js":',), modules=("opentype.js",)),
    Component("tiny-inflate", "", "MIT", "inlined in opentype.js 0.9.0's prebuilt file (its banner: \"Uses "
              "tiny-inflate by Devon Govett\"); opentype.js 0.9.0 requires tiny-inflate ^1.0.2 and its build names no "
              "version", (_lic("LICENSE of the tiny-inflate repository (github.com/foliojs/tiny-inflate; the 1.0.2 "
                               "npm package ships no licence file and declares MIT)", "tiny-inflate.txt"),),
              (r"re:this\.table=new Uint16Array\(16\),this\.trans=new Uint16Array\(288\)",)),
    Component("string.prototype.codepointat", "", "MIT", "inlined in opentype.js 0.9.0's prebuilt file (its banner: "
              "\"string.prototype.codepointat polyfill by Mathias Bynens\"); opentype.js 0.9.0 requires ^0.2.1",
              (_lic("LICENSE-MIT.txt of the string.prototype.codepointat 0.2.1 npm package",
                    "string.prototype.codepointat-0.2.1.txt"),),
              ("String.prototype.codePointAt||((",)),
    Component("libtess", "1.2.2", "SGI-B-2.0", f"browserify module; version from {_P5_LOCK}",
              (_lic("LICENSE of the libtess 1.2.2 npm package", "libtess-1.2.2.txt"),),
              ("GluTesselator",), modules=("libtess",)),
    Component("gifenc", "1.0.3", "MIT", f"browserify module; version from {_P5_LOCK}",
              (_lic("LICENSE.md of the gifenc 1.0.3 npm package", "gifenc-1.0.3.txt"),),
              ("GIFEncoder",), modules=("gifenc",)),
    Component("omggif", "1.0.10", "MIT", f"browserify module; version from {_P5_LOCK}",
              (_lic("licence header of omggif.js in the omggif 1.0.10 npm package (it ships no licence file)",
                    "omggif-1.0.10.txt"),),
              ("GifWriter", "GifReader"), modules=("omggif",)),
    Component("es6-promise", "4.2.8", "MIT", f"browserify module (es6-promise/auto); version from {_P5_LOCK}",
              (_lic("LICENSE of the es6-promise 4.2.8 npm package", "es6-promise-4.2.8.txt"),),
              ("ES6Promise",), modules=("es6-promise",)),
    Component("whatwg-fetch", "2.0.4", "MIT", f"browserify module; version from {_P5_LOCK}",
              (_lic("LICENSE of the whatwg-fetch 2.0.4 npm package", "whatwg-fetch-2.0.4.txt"),),
              ('"whatwg-fetch":',), modules=("whatwg-fetch",)),
    Component("fetch-jsonp", "1.1.3", "MIT", f"browserify module; version from {_P5_LOCK}",
              (_lic("the MIT License its 1.1.3 npm package declares (it ships no licence text)",
                    "fetch-jsonp-1.1.3.txt"),),
              ("jsonpCallback",), modules=("fetch-jsonp",)),
    Component("file-saver", "1.3.8", "MIT", f"browserify module (FileSaver.js 1.3.2); version from {_P5_LOCK}",
              (_lic("LICENSE.md of the file-saver 1.3.8 npm package", "file-saver-1.3.8.txt"),),
              ('"file-saver":',), modules=("file-saver",)),
    Component("buffer", "5.4.3", "MIT", f"browserify module (the browser 'buffer' built-in); version from {_P5_LOCK}",
              (_lic("LICENSE of the buffer 5.4.3 npm package", "buffer-5.4.3.txt"),),
              ("INSPECT_MAX_BYTES",), modules=("buffer",)),
    Component("base64-js", "1.3.1", "MIT", f"browserify module (required by buffer); version from {_P5_LOCK}",
              (_lic("LICENSE of the base64-js 1.3.1 npm package", "base64-js-1.3.1.txt"),),
              ('"base64-js":',), modules=("base64-js",)),
    Component("ieee754", "1.1.13", "BSD-3-Clause", f"browserify module (required by buffer); version from {_P5_LOCK}",
              (_lic("LICENSE of the ieee754 1.1.13 npm package", "ieee754-1.1.13.txt"),),
              (r"re:[{,]ieee754:\d+",), modules=("ieee754",)),
    Component("process", "0.11.10", "MIT", f"browserify module (the browser 'process' built-in, _process); version "
              f"from {_P5_LOCK}", (_lic("LICENSE of the process 0.11.10 npm package", "process-0.11.10.txt"),),
              (r"re:[{,]_process:\d+",), modules=("_process",)),
    Component("path-browserify", "0.0.1", "MIT", f"browserify module (the browser 'path' built-in, whose functions are "
              f"taken from Node.js v8.11.1); version from {_P5_LOCK}",
              (_lic("LICENSE of the path-browserify 0.0.1 npm package", "path-browserify-0.0.1.txt"),
               _lic("the Node.js notice its code carries", "node-path-notice.txt")),
              (r"re:[{,]path:\d+",), modules=("path",)),
    Component("stacktrace.js (error-stack-parser)", "", "MIT", "code p5's own source copies (\"Borrow from stacktracejs "
              "... with minor modifications\") with this notice; no version is named",
              (_lic("the notice p5's source reproduces for it", "stacktrace.js-p5.txt"),),
              ("opera#sourceloc", "_getErrorStackParser")),
    Component("Raphaël", "", "MIT", "code p5's own source copies (\"Portions of below code copyright 2008 Dmitry "
              "Baranovskiy (via MIT license)\"); no version is named",
              (_lic("license.txt of the raphael 2.3.0 npm package, the latest release", "raphael-2.3.0.txt"),),
              (r're:"M"===[\w$]+\[0\]\[0\]&&"R"===[\w$]+\[1\]\[0\]\.toUpperCase\(\)&&"Z"===',)),
    Component("Processing (line shader)", "", "LGPL-2.1", "a shader of the Processing project that p5 embeds with its "
              "own notice (Processing is p5's parent project; the same licence as p5)",
              (_see("GNU Lesser General Public License 2.1 (the full text is in this section, license.txt)", "p5"),),
              ("Part of the Processing project",),
              notices=("Copyright (c) 2012-15 The Processing Foundation", "Copyright (c) 2004-12 Ben Fry and Casey Reas",
                       "Copyright (c) 2001-04 Massachusetts Institute of Technology")),
)

MATHJS_COMPONENTS: tuple[Component, ...] = (
    Component("@babel/runtime", "7.28.6", "MIT", f"bundled helpers; version from {_MATHJS_LOCK}",
              (_see("the same LICENSE as the @babel/runtime section", "@babel/runtime"),),
              ("@@toPrimitive must return a primitive value.",)),
    Component("complex.js", "2.4.2", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_lic("LICENSE of the complex.js 2.4.2 npm package", "complex.js-2.4.2.txt"),), ("Invalid Param",)),
    Component("decimal.js", "10.5.0", "MIT", "bundled; version from math.js.LICENSE.txt and " + _MATHJS_LOCK,
              (_see("the same LICENCE.md as the decimal.js section", "decimal.js"),), ("[DecimalError] ",)),
    Component("escape-latex", "1.2.0", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the escape-latex section", "escape-latex"),), ("textasciicircum{}",)),
    Component("fraction.js", "5.2.2", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the same LICENSE as the fraction.js section", "fraction.js"),), ("Division by Zero",)),
    Component("javascript-natural-sort", "0.7.1", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the javascript-natural-sort section", "javascript-natural-sort"),), ("^0x[0-9a-f]+$",)),
    Component("seedrandom", "3.0.5", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the seedrandom section", "seedrandom"),), ("4022871197",)),
    Component("tiny-emitter", "2.1.0", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the tiny-emitter section", "tiny-emitter"),), ("(this.e||(this.e={}))",)),
    Component("typed-function", "4.2.1", "MIT", f"bundled; version from {_MATHJS_LOCK}",
              (_see("the same LICENSE as the typed-function section", "typed-function"),),
              ("Argument is not a typed-function.",)),
)

MHCHEM = Component("mhchemparser", "4.1.1", "Apache-2.0", f"bundled; version from its banner and {_MATHJAX_LOCK}",
                   (_see(_APACHE, "mathjax"),), ("mhchemParser.ts", "Copyright (c) 2015-2021 Martin Hensel"),
                   notices=("Copyright (c) 2015-2021 Martin Hensel",))

VENDORED: tuple[Vendored, ...] = (
    Vendored("chart.js", "4.5.1", "chart.js/dist/chart.umd.min.js", "chartjs/chart.umd.min.js",
             (Component("@kurkle/color", "0.3.2", "MIT", "bundled; version from its banner in the file (the lock "
                        "installs another version for the chart.js package, see the @kurkle/color section)",
                        (_lic("LICENSE.md of the @kurkle/color 0.3.2 npm package", "kurkle-color-0.3.2.txt"),),
                        ("@kurkle/color v0.3.2",), notices=("(c) 2023 Jukka Kurkela",)),),
             notices=("(c) 2025 Chart.js Contributors",)),
    Vendored("three", "0.186.1", "three/build/three.module.js", "three/three.module.js",
             notices=("Copyright 2010-2026 Three.js Authors",)),
    Vendored("three", "0.186.1", "three/build/three.core.js", "three/three.core.js",
             (Component("earcut", "3.0.2", "ISC", "copied code (\"copy of mapbox/earcut version 3.0.2\")",
                        (_lic("LICENSE of the earcut 3.0.2 npm package", "earcut-3.0.2.txt"),),
                        ("copy of mapbox/earcut version 3.0.2",)),
              Component("paper.js", "", "MIT", "ported code (\"Port of paper.js' Path#getInteriorPoint()\"); no "
                        "version is named",
                        (_lic("LICENSE.txt of the paper 0.12.18 npm package, the latest release", "paper-0.12.18.txt"),),
                        ("Port of paper.js' Path#getInteriorPoint()",)),
              Component("Geometric Tools Engine (GteDistRaySegment.h)", "3.0.1", "BSL-1.0", "code taken from "
                        "GTEngine's GteDistRaySegment.h (\"from https://github.com/pmjoniak/GeometricTools/...\"), "
                        "File Version 3.0.1 in that repository",
                        (_lic("the notice of GteDistRaySegment.h", "geometrictools-notice.txt"),
                         _lic("Boost Software License 1.0 (boost.org/LICENSE_1_0.txt)", "boost-1.0.txt")),
                        ("GeometricTools/blob/master/GTEngine/Include/Mathematics/GteDistRaySegment.h",))),
             notices=("Copyright 2010-2026 Three.js Authors",),
             credits=(("(c) 2005 Elsevier Inc.", "Real-Time Collision Detection by Christer Ericson, the book whose "
                       "algorithm Triangle.closestPointToPoint follows (the comment: \"algorithm thanks to\")"),)),
    Vendored("mathjs", "15.2.0", "mathjs/lib/browser/math.js", "mathjs/math.js", MATHJS_COMPONENTS,
             licence_file=("mathjs/lib/browser/math.js.LICENSE.txt", "mathjs/math.js.LICENSE.txt")),
    Vendored("p5", "1.11.11", "p5/lib/p5.min.js", "p5/p5.min.js", P5_COMPONENTS, module_table=True),
    Vendored("dompurify", "3.4.16", "dompurify/dist/purify.min.js", "dompurify/purify.min.js",
             (Component("regenerator-runtime", "", "MIT", "helper code Babel inlined, which carries regenerator-"
                        "runtime's licence comment (\"license (MIT)\"); the comment names no version",
                        (_lic("the regenerator-runtime MIT License (LICENSE of its 0.13.3 npm package; the "
                              "copyright line is the comment's)", "regenerator-runtime-0.13.3.txt"),),
                        ("regenerator-runtime -- Copyright (c) 2014-present, Facebook, Inc.",),
                        notices=("Copyright (c) 2014-present, Facebook, Inc.",)),)),
    Vendored("marked", "18.0.14", "marked/lib/marked.umd.js", "marked/marked.umd.js",
             notices=("Copyright (c) 2018-2026, MarkedJS", "Copyright (c) 2011-2018, Christopher Jeffrey")),
    Vendored("prismjs", "1.30.0", "prismjs/prism.js", "prism/prism.js"),
    Vendored("prismjs", "1.30.0", "prismjs/components/prism-*.min.js", "prism/components/prism-*.min.js"),
    Vendored("prismjs", "1.30.0", "prismjs/themes/prism-tomorrow.css", "prism/prism-tomorrow.css",
             credits=(("Based on https://github.com/chriskempson/tomorrow-theme", "the colour scheme the stylesheet "
                       "names; it copies no code"),)),
    Vendored("mathjax", "3.2.2", "mathjax/es5/tex-svg.js", "mathjax/tex-svg.js",
             (Component("mj-context-menu", "0.6.1", "Apache-2.0", f"bundled (the context menu); version from "
                        f"{_MATHJAX_LOCK}; its package ships no licence file and declares Apache-2.0",
                        (_see(_APACHE, "mathjax"),), ("CtxtMenu_",)),
              Component("speech-rule-engine", "4.0.6", "Apache-2.0", "bundled (the parts the menu uses); the bundle "
                        f"states version 4.0.6, as does {_MATHJAX_LOCK}",
                        (_see(_APACHE, "mathjax"),), ('VERSION="4.0.6"', "speech-rule-engine@")))),
    Vendored("mathjax", "3.2.2", "mathjax/es5/ui/safe.js", "mathjax/ui/safe.js"),
    Vendored("mathjax", "3.2.2", "mathjax/es5/input/tex/extensions/mhchem.js", "mathjax/input/tex/extensions/mhchem.js",
             (MHCHEM,)),
    Vendored("mathjax", "3.2.2", "mathjax/es5/input/tex/extensions/all-packages.js",
             "mathjax/input/tex/extensions/all-packages.js", (MHCHEM,)),
    Vendored("mathjax", "3.2.2", "mathjax/es5/input/tex/extensions/*.js", "mathjax/input/tex/extensions/*.js"),
)
# Served files that are not library code: the face table covers web/vendor/fonts/ (and vendor.mjs' fonts.css), and
# the SRI manifest is ours.
SERVED_ELSEWHERE = ("fonts/*", "fonts.css", "manifest.json")


def _clean(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NoticeError(f"cannot read {path}: {exc}") from exc


def runtime_packages(lock: dict, direct: set[str]) -> list[Package]:
    """Every package of package-lock.json not marked dev (nor excluded), sorted by name, with its dependants."""
    entries = {path.removeprefix("node_modules/"): meta for path, meta in lock.get("packages", {}).items()
               if path.startswith("node_modules/") and not meta.get("dev")}
    for name in entries:
        if "node_modules/" in name:
            raise NoticeError(f"nested package {name}: two versions of one package are installed; extend the generator")
    out = []
    for name, meta in sorted(entries.items()):
        if name.startswith(EXCLUDED_SCOPES):
            continue
        parents = sorted(p for p, m in entries.items() if name in (m.get("dependencies") or {})
                         or name in (m.get("optionalDependencies") or {}) or name in (m.get("peerDependencies") or {}))
        out.append(Package(name, str(meta.get("version", "")), str(meta.get("license", "UNKNOWN")), tuple(parents),
                           name in direct))
    return out


def licence_texts(pkg: Package, node_modules: Path = NODE_MODULES) -> list[tuple[str, str]]:
    """(file name, cleaned text) of the package's licence files, or of its fallback source."""
    folder = node_modules / pkg.name
    manifest = folder / "package.json"
    if not manifest.is_file():
        raise NoticeError(f"{pkg.name} is not installed ({folder}): run npm ci")
    installed = json.loads(_read(manifest)).get("version")
    if installed != pkg.version:
        raise NoticeError(f"{pkg.name} {installed} is installed, package-lock.json says {pkg.version}: run npm ci")
    files = sorted((p for p in folder.iterdir() if p.is_file() and LICENCE_FILE.match(p.name)),
                   key=lambda p: p.name.lower())
    texts = [(p.name, _clean(_read(p))) for p in files]
    texts = [(n, t) for n, t in texts if t]
    if texts:
        return texts
    if pkg.name not in FALLBACK_SOURCES:
        raise NoticeError(f"{pkg.name} ships no licence file and has no fallback source in {GENERATOR}")
    fname, start, end = FALLBACK_SOURCES[pkg.name]
    lines = _read(folder / fname).replace("\r\n", "\n").split("\n")
    try:
        first = next(i for i, ln in enumerate(lines) if ln.startswith(start))
    except StopIteration as exc:
        raise NoticeError(f"{pkg.name}/{fname}: no line starting {start!r}") from exc
    last = len(lines)
    if end is not None:
        try:
            last = next(i for i in range(first + 1, len(lines)) if lines[i].startswith(end)) + 1
        except StopIteration as exc:
            raise NoticeError(f"{pkg.name}/{fname}: no line starting {end!r} after {start!r}") from exc
    text = _clean("\n".join(lines[first:last]))
    if not text:
        raise NoticeError(f"{pkg.name}/{fname}: empty licence text")
    return [(f"{fname} (licence section; the package ships no licence file)", text)]


# --- what the vendored files contain ---------------------------------------------------------------------------------

def has_marker(text: str, marker: str) -> bool:
    """Whether ``text`` holds the evidence ``marker`` (a substring, or a regular expression after ``re:``)."""
    if marker.startswith("re:"):
        return re.search(marker[3:], text) is not None
    return marker in text


_MODULE_START = re.compile(r"(?<=[{,])(\d+):\[function\([\w$]+,[\w$]+,[\w$]+\)\{")
_KEY = r'(?:"[^"]*"|[A-Za-z_$][\w$]*):(?:\d+|void 0)'
_MODULE_DEPS = re.compile(r"\},\{((?:" + _KEY + r"(?:," + _KEY + r")*)?)\}\]\s*,?\s*$")
_EMPTY_MODULE = re.compile(r"\d+:\[function\([\w$]+,[\w$]+,[\w$]+\)\{")


def browserify_packages(text: str) -> dict[str, set[str]]:
    """The packages a browserify bundle's module table requires, by the first part of the required name (scoped names
    keep their scope), mapped to the full names they are required by. Names that resolve to an empty module (the
    build stubbed them out) or to ``void 0`` (excluded) contain no code and are left out. Fails closed when the table
    cannot be read completely."""
    starts = [(m.start(), int(m.group(1))) for m in _MODULE_START.finditer(text)]
    if not starts:
        raise NoticeError("no browserify module table")
    end = text.find("},{},[", starts[-1][0])
    if end < 0:
        raise NoticeError("the browserify module table has no end")
    deps: dict[int, dict[str, int | None]] = {}
    empty: set[int] = set()
    for (pos, mod), nxt in zip(starts, [p for p, _ in starts[1:]] + [end], strict=True):
        segment = text[pos:nxt]
        found = _MODULE_DEPS.search(segment)
        if found is None:
            raise NoticeError(f"browserify module {mod}: its dependency map cannot be read")
        pairs = re.findall(r'(?:"([^"]*)"|([A-Za-z_$][\w$]*)):(\d+|void 0)', found.group(1) or "")
        deps[mod] = {(q or b): (None if v == "void 0" else int(v)) for q, b, v in pairs}
        if _EMPTY_MODULE.fullmatch(segment[:found.start()]):  # "N:[function(e,t,r){" and then "},{...}]"
            empty.add(mod)
    out: dict[str, set[str]] = {}
    for table in deps.values():
        for name, target in table.items():
            if name.startswith(".") or target is None or target in empty:
                continue
            parts = name.split("/")
            out.setdefault("/".join(parts[:2]) if name.startswith("@") else parts[0], set()).add(name)
    return out


def check_vendored(entry: Vendored, text: str, path: str) -> None:
    """Fail closed when the reviewed facts of ``entry`` do not hold for the file's ``text``."""
    for comp in entry.components:
        missing = [m for m in comp.markers if not has_marker(text, m)]
        if not comp.markers or missing:
            raise NoticeError(f"{path}: no evidence of {comp.name} any more ({missing or 'no markers'}); review the "
                              f"bundle and update VENDORED in {GENERATOR}")
    if entry.module_table:
        listed = {m for comp in entry.components for m in comp.modules}
        required = set(browserify_packages(text))
        if required != listed:
            raise NoticeError(f"{path}: its module table requires {sorted(required - listed)} that no component "
                              f"lists, and components list {sorted(listed - required)} it does not require; review "
                              f"the bundle and update VENDORED in {GENERATOR}")
    known = entry.notices + tuple(n for c in entry.components for n in c.notices) + tuple(s for s, _ in entry.credits)
    for m in COPYRIGHT_STATEMENT.finditer(text):
        if not any(text.startswith(k, m.start()) for k in known):
            raise NoticeError(f"{path}: an unreviewed copyright statement {text[m.start():m.start() + 80]!r}; review "
                              f"the bundle and update VENDORED in {GENERATOR}")


def vendored_files(entry: Vendored, node_modules: Path, claimed: set[str]) -> list[str]:
    """The node_modules-relative files of ``entry`` (a glob expands to the files no earlier entry claims)."""
    if not entry.is_glob:
        return [entry.source]
    folder = node_modules / Path(entry.source).parent
    if not folder.is_dir():
        raise NoticeError(f"{folder} is missing: run npm ci")
    return sorted(f"{Path(entry.source).parent.as_posix()}/{p.name}" for p in folder.iterdir()
                  if p.is_file() and fnmatch.fnmatchcase(p.name, Path(entry.source).name)
                  and f"{Path(entry.source).parent.as_posix()}/{p.name}" not in claimed)


def vendored_sections(packages: dict[str, Package], node_modules: Path = NODE_MODULES) -> dict[str, list[str]]:
    """package -> the lines its section gets for the files vendored from it: what each file contains, with the
    licence texts. Checks every entry against the installed files (fail closed)."""
    out: dict[str, list[str]] = {}
    claimed: set[str] = set()
    for entry in VENDORED:
        pkg = packages.get(entry.package)
        if pkg is None:
            raise NoticeError(f"VENDORED lists {entry.package}, which is not a runtime package of package-lock.json")
        if pkg.version != entry.version:
            raise NoticeError(f"{entry.package} {pkg.version} is installed, VENDORED was reviewed at {entry.version}: "
                              f"review its vendored files and update VENDORED in {GENERATOR}")
        for rel in vendored_files(entry, node_modules, claimed):
            claimed.add(rel)
            check_vendored(entry, _read(node_modules / rel), rel)
        lines = out.setdefault(entry.package, [])
        lines += ["", f"Vendored file: web/vendor/{entry.served} (from node_modules/{entry.source})"]
        if entry.components:
            lines.append("  Third-party code it contains (the licence texts follow):")
            for c in entry.components:
                lines.append(f"    {c.name} {c.version or '(version not stated)'} ({c.licence}): {c.contained}")
        else:
            lines.append("  Third-party code it contains: none beyond the package's own")
        for statement, why in entry.credits:
            lines.append(f"  Credited, no code copied: \"{statement}\": {why}")
        if entry.licence_file is not None:
            source, served = entry.licence_file
            lines += ["", f"--- {source} (the licence file the publisher ships beside the bundle; served as "
                          f"web/vendor/{served}) ---", "", _clean(_read(node_modules / source))]
        for c in entry.components:
            for t in c.texts:
                head = f"--- {c.name} {c.version}".rstrip() + f": {t.title} ---"
                if t.file is not None:
                    text = _clean(_read(LICENCE_DIR / t.file))
                    if not text:
                        raise NoticeError(f"{LICENCE_DIR / t.file} is empty")
                    lines += ["", head, "", text]
                else:
                    if t.section not in packages:
                        raise NoticeError(f"{c.name}: its licence text points at {t.section}, which has no section")
                    lines += ["", head]
    return out


def _vendored_fonts() -> dict[str, list[str]]:
    """package -> the font families the face table vendors from it, with where (faces.py, the one face table)."""
    sys.path.insert(0, str(ROOT))
    from aadhi.compose.dynamic import faces

    out: dict[str, list[str]] = {}
    for f in faces.FAMILIES.values():
        if f.vendored and f.package:
            sheet = "web/vendor/fonts.css" if f.css == "classic" else "web/css/dynamic-fonts.css"
            out.setdefault(f.package, []).append(f"font family '{f.name}': web/vendor/{f.directory}/ ({sheet})")
    return out


def _vendored_icons() -> dict[str, list[str]]:
    """package -> where the icon index vendors its geometry (aadhi/compose/dynamic/icons.py, the one icon index)."""
    sys.path.insert(0, str(ROOT))
    from aadhi.compose.dynamic import icons

    return {s.package: [f"icon set '{s.id}': SVG path data normalised into {icons.INDEX_NAME} ({icons.GENERATOR})"]
            for s in icons.SETS}


def render(lock_path: Path = LOCK, node_modules: Path = NODE_MODULES) -> str:
    """The text ``THIRD_PARTY_NOTICES`` must hold."""
    lock = json.loads(_read(lock_path))
    direct = set((lock.get("packages", {}).get("", {}).get("dependencies") or {}))
    packages = runtime_packages(lock, direct)
    where = _vendored_fonts()
    for package, places in _vendored_icons().items():
        where.setdefault(package, []).extend(places)
    by_name = {p.name: p for p in packages}
    contents = vendored_sections(by_name, node_modules)
    head = [
        "THIRD-PARTY NOTICES",
        "",
        f"Generated by `{GENERATOR}` from package-lock.json, the licence files of the installed",
        "packages and scripts/third_party_licences/; do not edit (`--check` runs in CI).",
        "",
        "Aadhi EduEngine serves these third-party libraries and fonts from web/vendor/ (copied there by",
        "scripts/vendor.mjs from the npm packages below, so the app runs with script-src 'self' and the render",
        "worker needs no internet access), and draws icons from the Lucide and Tabler sets, whose path data is",
        "normalised into aadhi/compose/dynamic/icon_index.json and copied into the timelines that show them.",
        "Every runtime package of the dependency tree is listed, with the full text of each licence and NOTICE",
        "file it ships. A library's prebuilt browser bundle also holds code of other projects: packages its",
        "publisher bundled, at the versions the publisher built with (which can differ from the versions",
        "installed here), and code it copies or ports. The section of the package that ships each vendored",
        "file lists that code with its version and licence text; comments that only credit an algorithm",
        "copy no code and are named as credits. Type declarations (@types/*) are not shipped and not listed.",
        "",
        f"{len(packages)} packages:",
    ]
    width = max(len(f"{p.name} {p.version}") for p in packages)
    head += [f"  {f'{p.name} {p.version}'.ljust(width)}  {p.licence}" for p in packages]
    bundled = sorted({(c.name, c.version, c.licence, e.package) for e in VENDORED for c in e.components},
                     key=lambda r: (r[0].lower(), r[1], r[3]))
    if bundled:
        head += ["", f"{len(bundled)} third-party components inside the vendored files (listed in the section of "
                     "the package named):"]
        width = max(len(f"{n} {v}".rstrip()) for n, v, _, _ in bundled)
        head += [f"  {f'{n} {v}'.rstrip().ljust(width)}  {lic} (in {pkg})" for n, v, lic, pkg in bundled]
    sections = []
    for p in packages:
        lines = [RULE, f"{p.name} {p.version}", RULE, f"Licence (declared): {p.licence}",
                 f"Package: https://www.npmjs.com/package/{p.name}/v/{p.version}"]
        lines.append("Dependency of: this app (package.json)" if p.direct else
                     f"Dependency of: {', '.join(p.required_by) or 'n/a'}")
        lines += [f"Vendored: {place}" for place in where.get(p.name, [])]
        for fname, text in licence_texts(p, node_modules):
            lines += ["", f"--- {fname} ---", "", text]
        lines += contents.get(p.name, [])
        sections.append("\n".join(lines))
    return "\n".join(head) + "\n\n" + "\n\n".join(sections) + "\n"


def first_difference(current: str | None, expected: str) -> int | None:
    if current == expected:
        return None
    if current is None:
        return 1
    a, b = current.split("\n"), expected.split("\n")
    for n, (x, y) in enumerate(zip(a, b, strict=False), start=1):
        if x != y:
            return n
    return min(len(a), len(b)) + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if THIRD_PARTY_NOTICES is out of date")
    parser.add_argument("--path", type=Path, default=OUTPUT, help=argparse.SUPPRESS)  # tests: check a copy
    args = parser.parse_args(argv)
    try:
        text = render()
    except NoticeError as exc:
        print(f"THIRD_PARTY_NOTICES cannot be generated: {exc}", file=sys.stderr)
        return 2
    try:
        current: str | None = args.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        current = None
    if args.check:
        line = first_difference(current, text)
        if line is None:
            return 0
        print(f"THIRD_PARTY_NOTICES is out of date (first difference at line {line}): run {GENERATOR}",
              file=sys.stderr)
        return 1
    if current != text:
        args.path.write_bytes(text.encode("utf-8"))
        print(f"wrote {args.path.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
