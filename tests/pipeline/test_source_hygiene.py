"""Source hygiene: extracted-text clean-up (ligatures, odd spaces, mojibake), title-page metadata lines and
display equations in PDFs. Checked on unit inputs and on the eval fixture PDFs."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.config import ROOT_DIR
from aadhi.pipeline import ingest as ingest_mod
from aadhi.pipeline.ingest import ingest_source
from aadhi.pipeline.pdf_layout import is_display_equation, label_starts_paragraph, starts_with_label
from aadhi.pipeline.source_scope import parse_title_line, scope_source
from aadhi.pipeline.textnorm import normalize_extracted_text
from tests.pipeline.dbutil import get_source, seed_project

PDF = "application/pdf"
FIXTURES = ROOT_DIR / "evals" / "fixtures"
LIGATURES = tuple(chr(c) for c in range(0xFB00, 0xFB07))
BODY = "\n\n## 1. Voltage\n\nVoltage is the work done per unit charge to move charge between two points in a circuit.\n"


def run_ingest(job_ctx, data: bytes, mime: str, filename: str):
    seeded = seed_project(job_ctx.assets.storage, data, mime, filename)
    job_ctx.project_id = seeded.project_id
    return asyncio.run(ingest_source(job_ctx, get_source(seeded.source_id)))


# ---------------------------------------------------------------------------
# ligatures, spaces, mojibake
# ---------------------------------------------------------------------------


def test_ligatures_spaces_and_mojibake_are_repaired():
    assert normalize_extracted_text("Experimental veriﬁcation of the ﬂow; a stiﬀer, eﬃcient baﬄe") == (
        "Experimental verification of the flow; a stiffer, efficient baffle")
    assert normalize_extracted_text("10 kΩ resistor, con­duc​tor﻿") == "10 kΩ resistor, conductor"
    assert normalize_extracted_text("Ohmâ€™s law â€“ â€œV = IRâ€\u009d at 25Â°C, 3 Ã— 4") == (
        "Ohm’s law – “V = IR” at 25°C, 3 × 4")
    assert normalize_extracted_text("a bare â€ quote") == "a bare ” quote"


def test_no_nfkc_superscripts_symbols_and_joiners_are_kept():
    text = "x² + y³ = r², ½ of 10⁻⁶, Ω, µ, σ = E ε, ℃"
    assert normalize_extracted_text(text) == text
    malayalam = "ന്‍മ"  # a zero-width joiner shapes Indic text: it stays
    assert normalize_extracted_text(malayalam) == malayalam
    assert normalize_extracted_text("") == ""


def test_text_upload_is_cleaned_before_chunking(job_ctx):
    md = ("# Ohm’s law\n\nThe experimental veriﬁcation of Ohmâ€™s law uses a 20 Ω resistor and measures the "
          "current at several voltages. The V–I graph is a straight line through the origin.\n")
    res = run_ingest(job_ctx, md.encode("utf-8"), "text/markdown", "ohm.md")
    text = " ".join(c.text for c in res.chunks)
    assert "verification of Ohm’s law" in text and "20 Ω" in text
    assert not any(ch in res.markdown for ch in (*LIGATURES, " ", "â€"))


def test_ingest_version_was_bumped():
    assert int(ingest_mod.INGEST_VERSION) >= 6


# ---------------------------------------------------------------------------
# title-page lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line, fields", [
    ("Basic Electrical and Electronics Engineering - Unit 1: Electric Circuits - Session 2",
     ("Basic Electrical and Electronics Engineering", "Electric Circuits", "Session 2", "")),
    ("Subject: Basic Electrical Engineering | Unit 2 | Session 3", ("Basic Electrical Engineering", "Unit 2", "Session 3", "")),
    ("Course: Strength of Materials, Module 4, Lecture 2", ("Strength of Materials", "Module 4", "Session 2", "")),
    ("Digital System Design – Unit 1 – Session 3", ("Digital System Design", "Unit 1", "Session 3", "")),
    ("Unit 1 - Electric Circuits - Session 2 - Ohm's law", ("", "Electric Circuits", "Session 2", "Ohm's law")),
])
def test_title_lines_are_parsed(line, fields):
    tl = parse_title_line(line)
    assert tl is not None
    assert (tl.subject_name, tl.unit_name, tl.session_number, tl.session_title) == fields


@pytest.mark.parametrize("line", [
    "Unit 3: Logic gates",  # one field: a heading, handled by the key-value rules
    "Module name: math",
    "Session 2 - Boolean postulates",
    "Ohm's law states that V = IR, Unit 1, Session 2.",  # a sentence
    "Physics, Chemistry, Biology",
    "Voltage, current and resistance",
    "Units: volt, ampere, ohm",
    "CBSE Class 10 - Physics - Chapter 12",  # two unlabelled names: not sure which is the subject
])
def test_content_is_never_a_title_line(line):
    assert parse_title_line(line) is None


def test_a_title_line_becomes_metadata_and_leaves_the_content():
    line = "Basic Electrical and Electronics Engineering - Unit 1: Electric Circuits - Session 2"
    for md in (f"# Ohm's Law\n\n{line}{BODY}", f"{line}\n\n# Ohm's Law{BODY}"):
        res = scope_source(md)
        meta = res.document_meta
        assert (meta.subject_name, meta.unit_name, meta.session_number) == (
            "Basic Electrical and Electronics Engineering", "Electric Circuits", "Session 2")
        assert line not in res.markdown and "Voltage is the work done" in res.markdown
    res = scope_source("Subject: Basic Electrical Engineering | Unit 2 | Session 3\n\n# Ohm's Law" + BODY)
    assert (res.document_meta.subject_name, res.document_meta.unit_name) == ("Basic Electrical Engineering", "Unit 2")
    assert "Subject:" not in res.markdown


def test_codes_terms_and_names_in_a_title_line_are_excluded():
    res = scope_source("# Basic Electrical Engineering\n\nEE3301 - Basic Electrical Engineering - Unit II - Session 4 - "
                       "II Year - Dr. A. Kumar" + BODY)
    assert res.document_meta.session_number == "Session 4" and res.document_meta.unit_name == "Unit II"
    excluded = {(e.category, e.text) for e in res.excluded}
    assert ("admin", "Course code: EE3301") in excluded and ("person", "Name in a title: Dr. A. Kumar") in excluded
    for value in ("EE3301", "Kumar", "II Year"):
        assert value not in res.markdown


def test_a_repeated_title_line_is_a_running_header_and_a_body_line_stays_content():
    line = "Digital System Design - Unit 1 - Session 3"
    md = (f"# Logic gates\n\n{line}\n\n## 1. Gates\n\nA logic gate is an electronic circuit whose output is a Boolean "
          f"function of its inputs.\n\n{line}\n\n## 2. Universal gates\n\nNAND and NOR gates are called universal gates "
          "because any Boolean function can be built from either.\n")
    res = scope_source(md)
    assert line not in res.markdown and res.document_meta.session_number == "Session 3"
    assert ("admin", line) in {(e.category, e.text) for e in res.excluded}
    body_only = ("# Logic gates\n\n## 1. Gates\n\nA logic gate is an electronic circuit whose output is a Boolean function "
                 "of its inputs. It has one or more inputs.\n\nLater: see Digital System Design - Unit 2 - Session 4.\n\n"
                 "Digital System Design - Unit 2 - Session 4\n")
    res = scope_source(body_only)
    assert "Digital System Design - Unit 2 - Session 4\n" in res.markdown and res.document_meta.session_number == ""


# ---------------------------------------------------------------------------
# PDFs: display equations and the eval fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("V = I × R", True), ("(A · B)' = A' + B' (A + B)' = A' · B'", True), ("σ = E ε", True), ("E = mc²", True),
    ("Power = Voltage × Current", False), ("3. Ohm's law", False), ("Ohm's Law: The Rule That Runs Every Circuit", False),
])
def test_display_equations_are_not_headings(text, expected):
    assert is_display_equation(text) is expected


EXPECTED_META = {
    "ohms_law.pdf": ("Basic Electrical and Electronics Engineering", "Electric Circuits", "Session 2"),
    "logic_gates.pdf": ("Digital System Design", "Logic Gates and Boolean Algebra", "Session 3"),
    "stress_strain.pdf": ("Strength of Materials", "Simple Stresses and Strains", "Session 1"),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_META))
def test_eval_fixture_pdfs_are_clean_teaching_content(job_ctx, name):
    res = run_ingest(job_ctx, (FIXTURES / name).read_bytes(), PDF, name)
    meta = res.document_meta
    assert (meta.subject_name, meta.unit_name, meta.session_number) == EXPECTED_META[name]
    # the title-page line is metadata only: no chunk holds it, and the first chunk teaches something
    assert not any(" - Unit " in c.text or " - Session " in c.text for c in res.chunks)
    assert len(res.chunks[0].text.split()) >= 5
    assert not any(ch in res.markdown for ch in LIGATURES)
    for c in res.chunks:  # a display equation never becomes the parent section of later headings
        assert not any(is_display_equation(h) for h in c.heading_path), c.heading_path
    if name == "ohms_law.pdf":
        text = " ".join(c.text for c in res.chunks)
        assert "Experimental verification" in " ".join(" > ".join(c.heading_path) for c in res.chunks)
        assert "V = I × R" in text and "potential difference" in text


# ---------------------------------------------------------------------------
# PDFs: wrapped sentences, the title, what the layout dropped
# ---------------------------------------------------------------------------

WRAPPED = [  # (end of one line, the wrapped next line): one sentence, never two paragraphs
    ("When an external force acts on a body, internal resisting forces develop. Stress (σ) is the internal",
     "resisting force per unit area: σ = F / A"),
    ("Electric current (I) is the rate of flow of electric charge through a conductor, I = Q / t. Its SI unit is the",
     "ampere (A): one ampere is one coulomb of charge passing a point every second."),
    ("A 20 Ω resistor was connected to a variable DC supply with an ammeter in series and a voltmeter in",
     "parallel. The readings were:"),
    ("NAND and NOR are called universal gates because any Boolean function can be built using only one",
     "of them. With NAND gates alone:"),
    ("'XOR is just OR': the two differ when both inputs are 1 (OR gives 1, XOR gives 0). 'NAND is the",
     "opposite of OR': NAND is the complement of AND; by De Morgan, (A · B)' = A' + B'."),
]


@pytest.mark.parametrize("previous, line", WRAPPED)
def test_a_wrapped_line_with_an_early_colon_continues_its_sentence(previous, line):
    assert starts_with_label(line) or ":" in line
    assert not label_starts_paragraph(previous, line)


@pytest.mark.parametrize("previous, line", [
    ("Department: EEE", "Course Code: EE2101"),  # a header block
    ("a wrapped line without a full stop", "SME Name: Dr. X"),  # a known header key
    ("a wrapped line without a full stop", "Aadhi speaks:"),  # a script label
    ("The readings were:", "Voltage (V): 2, 4, 6"),  # a new line after a finished sentence
])
def test_header_fields_and_script_labels_still_start_a_paragraph(previous, line):
    assert label_starts_paragraph(previous, line)


PDF_CHECKS = {
    "ohms_law.pdf": ("Ohm's Law: The Rule That Runs Every Circuit",
                     ["Its SI unit is the ampere (A): one ampere", "a voltmeter in parallel. The readings were:"]),
    "logic_gates.pdf": ("Logic Gates and Universal Gates",
                        ["using only one of them. With NAND gates alone:", "'NAND is the opposite of OR'"]),
    "stress_strain.pdf": ("", ["Stress (σ) is the internal resisting force per unit area: σ = F / A"]),
}


@pytest.mark.parametrize("name", sorted(PDF_CHECKS))
def test_eval_pdfs_keep_wrapped_definitions_whole_and_report_what_layout_dropped(job_ctx, name):
    title, sentences = PDF_CHECKS[name]
    res = run_ingest(job_ctx, (FIXTURES / name).read_bytes(), PDF, name)
    text = "\n".join(c.text for c in res.chunks)
    for sentence in sentences:
        assert sentence in text, sentence
    # the PDF's own title names the session (stress_strain's title is its unit name, which already shows)
    assert res.document_meta.session_title == title
    # the running header and the page footer are reported as removed, never part of the content
    dropped = [e.text for e in res.excluded if e.category == "admin" and e.reason.startswith("running header")]
    assert any(t.startswith("Page ") for t in dropped) and any("Engineering College" in t for t in dropped)
    assert "Engineering College" not in text


def test_a_pdf_title_that_is_an_admin_line_or_a_file_name_is_not_a_session_title():
    from aadhi.pipeline.ingest import pdf_title

    assert pdf_title("", "<!-- page 1 -->\n\n# Ohm's Law: The Rule That Runs Every Circuit\n\n## 1. Voltage\n") == (
        "Ohm's Law: The Rule That Runs Every Circuit")
    assert pdf_title("Microsoft Word - ohm.docx", "## 1. Voltage\n") == ""
    assert pdf_title("", "# Basic Electrical Engineering - Unit 1: Electric Circuits - Session 2\n") == ""
    assert pdf_title("", "# Course Code: EE3301\n") == ""
    assert pdf_title("Kirchhoff's laws", "## 1. Nodes\n\nA node is a point.\n") == "Kirchhoff's laws"


SYMBOLS = ("R = 4 Ω, σ = F/A, μ = 2, π d²/4, ΔL, ε, ρ, θ, λ, V ≤ 5, √2, ∞, A → B, 10⁻³, ≈, ≠, ∑ I = 0, "
           "α, β, γ, ω, Φ, ∠30°, ±5 %, ∂V/∂t, ∫ f dx")


def _as_cp1252(text: str, *, lenient: bool = True) -> str:
    """``text`` saved as UTF-8 and read back as Windows-1252 (a lenient reader keeps undefined bytes as C1
    controls; a strict one replaces them)."""
    out = []
    for b in text.encode("utf-8"):
        try:
            out.append(bytes([b]).decode("cp1252"))
        except UnicodeDecodeError:
            out.append(chr(b) if lenient else "�")
    return "".join(out)


def test_greek_letters_and_maths_symbols_survive_mojibake():
    garbled = _as_cp1252(SYMBOLS)
    assert "Î©" in garbled and "Ïƒ" in garbled and "â‰¤" in garbled
    assert normalize_extracted_text(garbled) == SYMBOLS
    assert normalize_extracted_text(_as_cp1252(SYMBOLS, lenient=False)) == SYMBOLS  # ρ and ⁻ lost a byte
    # a partial sequence the table knows, next to a whole one
    assert normalize_extracted_text("Ohmâ€™s law: R = 4 Î©") == "Ohm’s law: R = 4 Ω"


@pytest.mark.parametrize("text", [
    "Àlvaro, Ébène, naïve café, Ångström at 25°C, x² + y³, ½ of 10⁻⁶",
    "Âmbito, Ãngulo, Îles, Ïle",
    "Τα ελληνικά: ΑΒΓ, σ = E ε",
    "தமிழ் ന്‍മ हिन्दी",
])
def test_correct_text_with_accented_letters_is_untouched(text):
    assert normalize_extracted_text(text) == text
