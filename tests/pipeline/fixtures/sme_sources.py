"""SME-style source documents built inside the tests (python-docx / PyMuPDF) + the sample template."""

from __future__ import annotations

import io
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
REPO = FIXTURES.parents[2]
SAMPLE_TEMPLATE = REPO / "evals" / "fixtures" / "sample_template.docx"  # a real SME video script (6 clips)
SME_SCRIPT_TXT = FIXTURES / "sme_script_ohm.txt"

# header fields that must never reach the lecture (and the values the tests look for)
HEADER_ROWS = [
    ("SME Name", "Dr. Kavitha Raman"),
    ("Department", "Electrical and Electronics Engineering"),
    ("Video Duration", "6 minutes"),
    ("Reviewed by", "Prof. S. Anand"),
    ("Date", "12/03/2024"),
    ("Version", "2.1"),
    ("Course Code", "EE3301"),
]
HEADER_VALUES = ["Kavitha", "Electrical and Electronics Engineering", "6 minutes", "Anand", "12/03/2024", "EE3301"]

CONTENT = [
    ("Ohm's Law", [
        "Ohm's law states that the current through a conductor is proportional to the voltage across it.",
        "In symbols, V = I x R, where R is the resistance of the conductor.",
        "The duration of the pulse is 2 ms, so the charge delivered is small.",
    ]),
    ("Worked example", [
        "A 12 V battery drives current through a 4 ohm resistor, so the current is 3 A.",
    ]),
]


def sample_template_bytes() -> bytes:
    return SAMPLE_TEMPLATE.read_bytes()


def header_table_docx() -> bytes:
    """An SME document whose first block is a header TABLE, followed by teaching content."""
    import docx

    d = docx.Document()
    d.add_heading("Video Lecture Script", level=1)
    table = d.add_table(rows=len(HEADER_ROWS), cols=2)
    for i, (k, v) in enumerate(HEADER_ROWS):
        table.cell(i, 0).text = k
        table.cell(i, 1).text = v
    for title, paras in CONTENT:
        d.add_heading(title, level=2)
        for p in paras:
            d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def header_table_pdf() -> bytes:
    """The same document as a PDF: a ruled header table, content, then sign-off lines."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Video Lecture Script", fontsize=18)
    for r, (k, v) in enumerate(HEADER_ROWS):
        for c, cell in enumerate((k, v)):
            rect = pymupdf.Rect(72 + c * 220, 90 + r * 22, 72 + (c + 1) * 220, 90 + (r + 1) * 22)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y0 + 15), cell, fontsize=9)
    y = 280.0
    for title, paras in CONTENT:
        page.insert_text((72, y), title, fontsize=16)
        y += 28
        for p in paras:
            page.insert_text((72, y), p, fontsize=10)
            y += 16
        y += 16
    page.insert_text((72, y), "Prepared by: Dr. Kavitha Raman", fontsize=10)
    page.insert_text((72, y + 16), "Word count: 650", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data
