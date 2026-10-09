"""Generate the PDF eval fixtures (small but realistic lecture notes) with PyMuPDF.

Usage:
    .venv/Scripts/python.exe evals/make_fixtures.py            # (re)write evals/fixtures/*.pdf + sidecars
    .venv/Scripts/python.exe evals/make_fixtures.py --check    # exit 1 if the committed files are stale
    .venv/Scripts/python.exe evals/make_fixtures.py --out DIR  # write somewhere else

Each fixture exercises a different ingest path: formulas + units table + measurement table (Ohm's
law), a truth table and Boolean algebra (logic gates), Greek symbols, subscripts and a numeric worked
example (stress and strain). Output is byte-for-byte reproducible (fixed metadata, no random ids), so
re-running the script never creates spurious git diffs.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 56
FIXED_DATE = "D:20260101000000Z"

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; color: #1d1d1f; }
h1 { font-size: 17pt; color: #3b0a63; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; color: #3b0a63; margin: 10pt 0 3pt 0; }
p { margin: 0 0 5pt 0; }
p.meta { color: #555; font-size: 9.5pt; }
p.formula { text-align: center; font-size: 13pt; font-weight: bold; margin: 6pt 0; }
table { border-collapse: collapse; margin: 4pt 0 6pt 0; }
th, td { border: 1px solid #333; padding: 3pt 7pt; font-size: 9.5pt; }
th { background-color: #ece4f5; }
li { margin-bottom: 2pt; }
"""
HEADER_CSS = "body { font-family: sans-serif; font-size: 8pt; color: #666; }"


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    title: str
    header: str
    pages: tuple[str, ...]
    options: dict[str, Any] = field(default_factory=dict)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


OHMS_LAW = FixtureSpec(
    name="ohms_law",
    title="Ohm's Law: The Rule That Runs Every Circuit",
    header="Rajalakshmi Engineering College - Basic Electrical and Electronics Engineering - Unit 1",
    options={
        "subject_name": "Basic Electrical and Electronics Engineering",
        "unit_name": "Electric Circuits",
        "session_number": "Session 2",
        "session_title": "Ohm's Law",
        "target_minutes": 10,
    },
    pages=(
        "<h1>Ohm's Law: The Rule That Runs Every Circuit</h1>"
        "<p class='meta'>Basic Electrical and Electronics Engineering - Unit 1: Electric Circuits - Session 2</p>"
        "<h2>1. Learning objectives</h2>"
        "<ul><li>Define voltage, current and resistance and state their SI units.</li>"
        "<li>State Ohm's law and apply V = IR to find an unknown quantity.</li>"
        "<li>Interpret a V-I graph to determine resistance.</li>"
        "<li>Distinguish ohmic from non-ohmic devices.</li></ul>"
        "<h2>2. Voltage, current and resistance</h2>"
        "<p><b>Electric current (I)</b> is the rate of flow of electric charge through a conductor, I = Q / t. "
        "Its SI unit is the ampere (A): one ampere is one coulomb of charge passing a point every second.</p>"
        "<p><b>Voltage (V)</b>, or potential difference, is the work done per unit charge to move charge "
        "between two points, V = W / Q. Its unit is the volt (V), equal to one joule per coulomb.</p>"
        "<p><b>Resistance (R)</b> is the opposition a material offers to the flow of current, measured in "
        "ohms (Ω). It depends on the material's resistivity ρ, the length L and the cross-sectional area A "
        "of the conductor: R = ρL / A. A longer wire resists more; a thicker wire resists less.</p>"
        + _table(
            ["Quantity", "Symbol", "SI unit", "Unit symbol"],
            [["Current", "I", "ampere", "A"], ["Voltage", "V", "volt", "V"],
             ["Resistance", "R", "ohm", "Ω"], ["Power", "P", "watt", "W"]],
        )
        + "<h2>3. Ohm's law</h2>"
        "<p>At constant temperature, the current through a conductor is directly proportional to the "
        "potential difference across its ends. The constant of proportionality is the resistance.</p>"
        "<p class='formula'>V = I × R</p>"
        "<p>Rearranged: I = V / R and R = V / I. Doubling the voltage across a fixed resistor doubles the "
        "current; doubling the resistance at a fixed voltage halves the current.</p>",
        "<h2>4. Experimental verification</h2>"
        "<p>A 20 Ω resistor was connected to a variable DC supply with an ammeter in series and a voltmeter "
        "in parallel. The readings were:</p>"
        + _table(
            ["Voltage V (V)", "Current I (A)", "V / I (Ω)"],
            [["2.0", "0.10", "20"], ["4.0", "0.20", "20"], ["6.0", "0.30", "20"], ["8.0", "0.40", "20"],
             ["10.0", "0.50", "20"]],
        )
        + "<p>The V-I graph is a straight line through the origin; its slope V / I = 20 Ω is the resistance.</p>"
        "<h2>5. Worked example</h2>"
        "<p>A 12 V battery is connected across a 4 Ω resistor. Find the current and the power dissipated.</p>"
        "<ol><li>Given: V = 12 V, R = 4 Ω.</li>"
        "<li>Ohm's law: I = V / R = 12 / 4 = 3 A.</li>"
        "<li>Power: P = V × I = 12 × 3 = 36 W (check: P = I²R = 9 × 4 = 36 W).</li></ol>"
        "<h2>6. A common misconception</h2>"
        "<p>Many students believe a resistor 'uses up' current, so less current leaves it than enters. In a "
        "series loop the current is the same everywhere. The resistor converts electrical energy into heat; "
        "that energy transfer appears as a drop in voltage across the resistor, not as a loss of current.</p>"
        "<h2>7. Limitations</h2>"
        "<p>Ohm's law holds for metallic conductors at constant temperature (ohmic devices). Filament lamps "
        "(resistance rises as the filament heats up), diodes and thermistors are non-ohmic: their V-I graphs "
        "are curved, so R = V / I changes with the operating point.</p>"
        "<h2>8. Practice</h2>"
        "<ol><li>A 230 V room heater draws 5 A. What is its resistance?</li>"
        "<li>What current flows through a 1 kΩ resistor connected to a 9 V battery?</li>"
        "<li>The current through a lamp doubles when the voltage is tripled. Is the lamp ohmic? Explain.</li></ol>",
    ),
)

LOGIC_GATES = FixtureSpec(
    name="logic_gates",
    title="Logic Gates and Universal Gates",
    header="Rajalakshmi Engineering College - Digital System Design - Unit 1",
    options={
        "subject_name": "Digital System Design",
        "unit_name": "Logic Gates and Boolean Algebra",
        "session_number": "Session 3",
        "session_title": "Logic Gates and Universal Gates",
        "target_minutes": 12,
    },
    pages=(
        "<h1>Logic Gates and Universal Gates</h1>"
        "<p class='meta'>Digital System Design - Unit 1: Logic Gates and Boolean Algebra - Session 3</p>"
        "<h2>1. Digital signals</h2>"
        "<p>A digital circuit works with two voltage levels: LOW (logic 0, about 0 V) and HIGH (logic 1, "
        "about 5 V in TTL or 3.3 V in modern CMOS). A <b>logic gate</b> is an electronic circuit whose output "
        "is a Boolean function of its inputs. Every digital system, from a calculator to a processor, is "
        "built from a handful of gate types.</p>"
        "<h2>2. Basic gates</h2>"
        + _table(
            ["Gate", "Boolean expression", "Output is 1 when"],
            [["AND", "Y = A · B", "all inputs are 1"], ["OR", "Y = A + B", "at least one input is 1"],
             ["NOT", "Y = A'", "the input is 0"], ["XOR", "Y = A ⊕ B = A'B + AB'", "the inputs differ"]],
        )
        + "<h2>3. Truth table of two-input gates</h2>"
        + _table(
            ["A", "B", "AND", "OR", "NAND", "NOR", "XOR", "XNOR"],
            [["0", "0", "0", "0", "1", "1", "0", "1"], ["0", "1", "0", "1", "1", "0", "1", "0"],
             ["1", "0", "0", "1", "1", "0", "1", "0"], ["1", "1", "1", "1", "0", "0", "0", "1"]],
        )
        + "<p>NAND and NOR are the complements of AND and OR: Y = (A · B)' and Y = (A + B)'.</p>",
        "<h2>4. Universal gates</h2>"
        "<p>NAND and NOR are called <b>universal gates</b> because any Boolean function can be built using "
        "only one of them. With NAND gates alone:</p>"
        "<ul><li>NOT: tie both inputs together, (A · A)' = A'.</li>"
        "<li>AND: a NAND followed by a NAND-inverter, ((A · B)')' = A · B.</li>"
        "<li>OR: invert each input first, (A' · B')' = A + B (by De Morgan's theorem).</li></ul>"
        "<h2>5. De Morgan's theorems</h2>"
        "<p class='formula'>(A · B)' = A' + B'      (A + B)' = A' · B'</p>"
        "<p>The complement of a product is the sum of the complements, and the complement of a sum is the "
        "product of the complements.</p>"
        "<h2>6. Worked example: XOR from four NAND gates</h2>"
        "<ol><li>N1 = (A · B)'</li><li>N2 = (A · N1)'</li><li>N3 = (B · N1)'</li><li>Y = (N2 · N3)'</li></ol>"
        "<p>Check A = 1, B = 0: N1 = 1, N2 = (1 · 1)' = 0, N3 = (0 · 1)' = 1, Y = (0 · 1)' = 1, which matches "
        "1 ⊕ 0 = 1.</p>"
        "<h2>7. Application: half adder</h2>"
        "<p>A half adder adds two bits: Sum = A ⊕ B and Carry = A · B. Adding 1 + 1 gives Sum 0, Carry 1, "
        "that is binary 10.</p>"
        "<h2>8. Common misconceptions</h2>"
        "<p>'XOR is just OR': the two differ when both inputs are 1 (OR gives 1, XOR gives 0). "
        "'NAND is the opposite of OR': NAND is the complement of AND; by De Morgan, (A · B)' = A' + B'.</p>"
        "<h2>9. Practice</h2>"
        "<ol><li>Write the truth table of a three-input AND gate.</li>"
        "<li>Implement Y = A + B using only NOR gates.</li></ol>",
    ),
)

STRESS_STRAIN = FixtureSpec(
    name="stress_strain",
    title="Simple Stresses and Strains",
    header="Rajalakshmi Engineering College - Strength of Materials - Unit 2",
    options={
        "subject_name": "Strength of Materials",
        "unit_name": "Simple Stresses and Strains",
        "session_number": "Session 1",
        "session_title": "Stress, Strain and Hooke's Law",
        "target_minutes": 12,
    },
    pages=(
        "<h1>Simple Stresses and Strains</h1>"
        "<p class='meta'>Strength of Materials - Unit 2: Simple Stresses and Strains - Session 1</p>"
        "<h2>1. Stress</h2>"
        "<p>When an external force acts on a body, internal resisting forces develop. <b>Stress (σ)</b> is the "
        "internal resisting force per unit area: σ = F / A. Its SI unit is the pascal (1 Pa = 1 N/m²); "
        "engineers usually work in MPa, where 1 MPa = 1 N/mm².</p>"
        "<ul><li><b>Tensile stress</b>: the force pulls the body and tends to lengthen it.</li>"
        "<li><b>Compressive stress</b>: the force pushes the body and tends to shorten it.</li>"
        "<li><b>Shear stress (τ)</b>: the force acts parallel to the area, τ = F / A.</li></ul>"
        "<h2>2. Strain</h2>"
        "<p><b>Strain (ε)</b> is the deformation per unit original length: ε = ΔL / L<sub>0</sub>. Being a ratio "
        "of two lengths, strain has no unit; it is often quoted in microstrain (10<sup>-6</sup>).</p>"
        "<h2>3. Hooke's law and Young's modulus</h2>"
        "<p>Within the elastic limit, stress is directly proportional to strain:</p>"
        "<p class='formula'>σ = E ε</p>"
        "<p>The constant E is the modulus of elasticity (Young's modulus). A larger E means a stiffer material: "
        "it stretches less under the same stress.</p>"
        + _table(
            ["Material", "Young's modulus E (GPa)", "Typical use"],
            [["Structural steel", "200", "beams, columns, rods"], ["Aluminium alloy", "70", "aircraft frames"],
             ["Copper", "117", "electrical conductors"], ["Concrete", "30", "foundations, slabs"],
             ["Timber (along grain)", "12", "roof trusses"]],
        ),
        "<h2>4. Stress-strain curve of mild steel</h2>"
        "<p>A tensile test pulls a standard specimen until it breaks. The curve shows: the <b>proportional "
        "limit</b> (end of the straight line), the <b>elastic limit</b> (largest stress with full recovery), "
        "the <b>yield point</b> (strain grows with little extra stress), the <b>ultimate tensile strength</b> "
        "(maximum stress) and finally <b>fracture</b> after necking.</p>"
        "<h2>5. Worked example</h2>"
        "<p>A steel rod 2 m long and 20 mm in diameter carries an axial tensile load of 50 kN. "
        "Take E = 200 GPa. Find the stress, the strain and the elongation.</p>"
        "<ol><li>Area: A = π d² / 4 = π × 20² / 4 = 314.16 mm².</li>"
        "<li>Stress: σ = F / A = 50 000 N / 314.16 mm² = 159.15 N/mm² = 159.15 MPa.</li>"
        "<li>Strain: ε = σ / E = 159.15 / 200 000 = 7.96 × 10<sup>-4</sup>.</li>"
        "<li>Elongation: ΔL = ε L<sub>0</sub> = 7.96 × 10<sup>-4</sup> × 2000 mm = 1.59 mm.</li></ol>"
        "<p>The stress is below the yield stress of mild steel (about 250 MPa), so the rod returns to its "
        "original length when the load is removed.</p>"
        "<h2>6. Common misconceptions</h2>"
        "<p>'Stress is the same as force': the same force produces a much larger stress on a thin rod than on "
        "a thick one. 'A stiffer material is always stronger': stiffness (E) and strength (ultimate stress) are "
        "different properties; glass is stiff but brittle, while rubber is flexible but tough.</p>"
        "<h2>7. Practice</h2>"
        "<ol><li>An aluminium bar 1.5 m long with a 25 mm × 25 mm square section carries a tensile load of "
        "40 kN. Take E = 70 GPa. Find the stress, strain and elongation.</li>"
        "<li>Why is strain dimensionless while stress is not?</li></ol>",
    ),
)

SPECS: tuple[FixtureSpec, ...] = (OHMS_LAW, LOGIC_GATES, STRESS_STRAIN)


def build_pdf(spec: FixtureSpec, path: Path) -> int:
    """Render ``spec`` to ``path`` (reproducibly); returns the page count."""
    doc = pymupdf.open()
    try:
        total = len(spec.pages)
        for number, html in enumerate(spec.pages, start=1):
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            page.insert_htmlbox(pymupdf.Rect(MARGIN, 24, PAGE_W - MARGIN, 44), spec.header, css=HEADER_CSS)
            spare, scale = page.insert_htmlbox(
                pymupdf.Rect(MARGIN, 56, PAGE_W - MARGIN, PAGE_H - 52), html, css=CSS, scale_low=1
            )
            if spare < 0 or scale != 1:
                raise ValueError(f"{spec.name}: page {number} content does not fit; split it")
            page.insert_htmlbox(
                pymupdf.Rect(MARGIN, PAGE_H - 40, PAGE_W - MARGIN, PAGE_H - 22),
                f"<p style='text-align:right'>Page {number} of {total}</p>",
                css=HEADER_CSS,
            )
        doc.set_metadata(
            {
                "title": spec.title,
                "author": "Aadhi EduEngine eval fixtures (synthetic)",
                "subject": spec.options.get("subject_name", ""),
                "keywords": "eval fixture",
                "creator": "evals/make_fixtures.py",
                "producer": "PyMuPDF",
                "creationDate": FIXED_DATE,
                "modDate": FIXED_DATE,
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pdf.tmp")
        doc.save(tmp, garbage=4, deflate=True, no_new_id=True, reproducible=True)
    finally:
        doc.close()
    tmp.replace(path)
    return total


def sidecar_text(spec: FixtureSpec) -> str:
    """Content of ``<name>.options.json``."""
    return json.dumps(spec.options, indent=2, sort_keys=True) + "\n"


def generate(out_dir: Path) -> list[Path]:
    """Write every PDF + options sidecar into ``out_dir``; returns the written paths."""
    written: list[Path] = []
    for spec in SPECS:
        pdf = out_dir / f"{spec.name}.pdf"
        build_pdf(spec, pdf)
        sidecar = out_dir / f"{spec.name}.options.json"
        sidecar.write_text(sidecar_text(spec), encoding="utf-8", newline="\n")
        written += [pdf, sidecar]
    return written


def _same_content(current: Path, fresh: Path) -> bool:
    """PDFs compare byte for byte; text sidecars ignore CRLF vs LF (git autocrlf checkouts)."""
    if fresh.suffix == ".pdf":
        return current.read_bytes() == fresh.read_bytes()
    return current.read_bytes().replace(b"\r\n", b"\n") == fresh.read_bytes().replace(b"\r\n", b"\n")


def check(out_dir: Path) -> list[str]:
    """Names of committed fixture files that differ from a fresh generation."""
    stale: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aadhi-fixtures-") as tmp:
        for fresh in generate(Path(tmp)):
            current = out_dir / fresh.name
            if not current.is_file() or not _same_content(current, fresh):
                stale.append(fresh.name)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=FIXTURES_DIR, help="output directory")
    parser.add_argument("--check", action="store_true", help="only verify the committed fixtures are current")
    args = parser.parse_args(argv)
    if args.check:
        stale = check(args.out)
        if stale:
            print("stale fixtures (run evals/make_fixtures.py): " + ", ".join(stale))
            return 1
        print("fixtures are up to date")
        return 0
    for path in generate(args.out):
        print(f"wrote {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
