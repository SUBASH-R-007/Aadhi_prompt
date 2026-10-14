"""Generate ``THIRD_PARTY_NOTICES``: the licences of every third-party asset the app serves from ``web/vendor``
(DYNAMIC_VIDEO_DESIGN §4.12, §7.13: "``THIRD_PARTY_NOTICES`` is generated and includes the Apache NOTICE files").

    python scripts/gen_third_party_notices.py            # rewrite THIRD_PARTY_NOTICES
    python scripts/gen_third_party_notices.py --check    # exit 1 when it is out of date (CI "Generated files")

What is covered. ``scripts/vendor.mjs`` copies the browser libraries and fonts into ``web/vendor`` from npm packages,
and the icon sets' geometry (Lucide, Tabler; B2b) is normalised into ``aadhi/compose/dynamic/icon_index.json``, from
which the resolver copies an icon into each timeline that shows it (design §4.12). All of them are runtime
``dependencies`` in ``package.json``; a library's bundle also holds code of the packages it depends on (``mathjs``'
browser build bundles ``complex.js``, ``decimal.js``, ``fraction.js``, ...; ``chart.js`` bundles ``@kurkle/color``). So
the notices cover every package of the runtime dependency closure that ``package-lock.json`` lists (every entry not
marked ``dev``), except type declarations (``@types/*``: never shipped): one section per package with its name, exact
version, declared licence, the packages that require it, where its files are vendored when the face table
(``aadhi/compose/dynamic/faces.py``) or the icon index (``aadhi/compose/dynamic/icons.py``) knows, and the full text of
every licence-like file the package ships at its top level (``LICENSE*``, ``LICENCE*``, ``COPYING*``, ``NOTICE*``,
``COPYRIGHT*``; the Apache ``NOTICE`` files included). A package that ships no such file has its licence text read from
the one place it keeps it (:data:`FALLBACK_SOURCES`); a package with neither fails generation (fail closed), and so
does a package whose installed version differs from the lock (run ``npm ci``).

The texts are copied as UTF-8 with LF line ends, trailing spaces trimmed and the leading and trailing blank lines
dropped; nothing else is changed. The output is byte-deterministic (packages sorted by name) and needs
``node_modules`` (``npm ci``), which the CI job installs before its generated-files step.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "THIRD_PARTY_NOTICES"
LOCK = ROOT / "package-lock.json"
NODE_MODULES = ROOT / "node_modules"
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


class NoticeError(RuntimeError):
    """The notices cannot be generated from what is installed (fail closed)."""


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    licence: str
    required_by: tuple[str, ...]
    direct: bool


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
    head = [
        "THIRD-PARTY NOTICES",
        "",
        f"Generated by `{GENERATOR}` from package-lock.json and the licence files of the installed",
        "packages; do not edit (`--check` runs in CI).",
        "",
        "Aadhi EduEngine serves these third-party libraries and fonts from web/vendor/ (copied there by",
        "scripts/vendor.mjs from the npm packages below, so the app runs with script-src 'self' and the render",
        "worker needs no internet access), and draws icons from the Lucide and Tabler sets, whose path data is",
        "normalised into aadhi/compose/dynamic/icon_index.json and copied into the timelines that show them.",
        "A library's bundle can include code of the packages it depends on, so every runtime package of the",
        "dependency tree is listed, with the full text of each licence and NOTICE file it ships. Type",
        "declarations (@types/*) are not shipped and not listed.",
        "",
        f"{len(packages)} packages:",
    ]
    width = max(len(f"{p.name} {p.version}") for p in packages)
    head += [f"  {f'{p.name} {p.version}'.ljust(width)}  {p.licence}" for p in packages]
    sections = []
    for p in packages:
        lines = [RULE, f"{p.name} {p.version}", RULE, f"Licence (declared): {p.licence}",
                 f"Package: https://www.npmjs.com/package/{p.name}/v/{p.version}"]
        lines.append("Dependency of: this app (package.json)" if p.direct else
                     f"Dependency of: {', '.join(p.required_by) or 'n/a'}")
        lines += [f"Vendored: {place}" for place in where.get(p.name, [])]
        for fname, text in licence_texts(p, node_modules):
            lines += ["", f"--- {fname} ---", "", text]
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
