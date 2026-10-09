"""Source documents with administrative data in the shapes real SME documents use (built in the tests).

Each builder returns ``(data, mime, secrets)``: ``secrets`` are header values that must never reach a
prompt, the screenplay or a job event. The teaching content of every document is ordinary RC-circuit
material, so the same "kept" checks apply to all of them.
"""

from __future__ import annotations

import io

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"
TXT = "text/plain"
MD = "text/markdown"

# teaching content shared by the documents (must survive scoping)
KEPT = (
    "When a capacitor is connected to a battery through a resistor, it charges gradually.",
    "The time constant tau = R x C sets how fast the capacitor charges.",
    "After one time constant the capacitor reaches about 63 percent of the supply voltage.",
)


def _script(clips: int = 1) -> list[tuple[str, str]]:
    """(style, text) lines of a small SME script; "h" = heading, "p" = paragraph."""
    lines: list[tuple[str, str]] = [
        ("h", "CLIP 1 SCRIPT - CHARGING A CAPACITOR"),
        ("h", "SEGMENT 1 - CHARGING A CAPACITOR [0:30 - 2:00]"),
        ("p", "Time: 0:40 – 1:10"),
        ("p", "Duration: 45 sec"),
        ("p", "Aadhi speaks:"),
        ("p", KEPT[0]),
        ("p", KEPT[1]),
        ("p", "ANIMATION: A capacitor fills with glowing charge while a timer runs."),
        ("h", "SEGMENT 2 - THE TIME CONSTANT [2:00 - 3:00]"),
        ("p", "Aadhi speaks:"),
        ("p", KEPT[2]),
    ]
    if clips >= 2:
        lines += [
            ("h", "CLIP 2 SCRIPT - DISCHARGING A CAPACITOR"),
            ("h", "SEGMENT 1 - DISCHARGING [0:00 - 1:00]"),
            ("p", "Aadhi speaks:"),
            ("p", "When the battery is removed, the capacitor discharges through the resistor."),
        ]
    return lines


def _docx(paragraphs: list[tuple[str, str]], tables: list[list[list[str]]] | None = None) -> bytes:
    import docx

    d = docx.Document()
    for rows in tables or []:
        width = max(len(r) for r in rows)
        table = d.add_table(rows=len(rows), cols=width)
        for i, row in enumerate(rows):
            if len(row) == 1:  # a merged title row ("VIDEO DETAILS")
                cell = table.cell(i, 0).merge(table.cell(i, width - 1))
                cell.text = row[0]
                continue
            for j, value in enumerate(row):
                table.cell(i, j).text = value
    for style, text in paragraphs:
        if style == "h":
            d.add_heading(text, level=2)
        else:
            d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# header tables with a serial-number column
# ---------------------------------------------------------------------------

SNO_ROWS = [
    ["S.No", "Particulars", "Details"],
    ["1", "Name of the SME", "Dr. Lakshmi Narayanan"],
    ["2", "Designation", "Assistant Professor"],
    ["3", "Department", "Electronics and Communication Engineering"],
    ["4", "Course Code & Name", "EC3352 & Circuit Theory"],
    ["5", "Video Duration", "11 min"],
    ["6", "Reviewed by", "Dr. Prakash Rao"],
    ["7", "E-mail", "lakshmi.n@svit.ac.in"],
]
SNO_SECRETS = ("Lakshmi", "Narayanan", "EC3352", "11 min", "Prakash", "lakshmi.n@svit.ac.in", "Particulars")


def sno_table_docx() -> tuple[bytes, str, tuple[str, ...]]:
    """An SME script whose header is an "S.No | Particulars | Details" table."""
    return _docx(_script(), tables=[SNO_ROWS]), DOCX, SNO_SECRETS


PDF_ROWS = [
    ["S.No", "Field", "Value"],
    ["1", "SME", "Dr. Meenakshi Rajan"],
    ["2", "Faculty ID", "SVIT-ECE-0457"],
    ["3", "Regulation", "R2021"],
    ["4", "Total Duration", "15 minutes"],
]
PDF_SECRETS = ("Meenakshi", "SVIT-ECE-0457", "R2021", "15 minutes")


def sno_table_pdf() -> tuple[bytes, str, tuple[str, ...]]:
    """Notes as a PDF: a ruled "S.No | Field | Value" header table, a sign-off line, then content."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "RC Circuits", fontsize=18)
    widths = (50, 150, 220)
    for r, row in enumerate(PDF_ROWS):
        x = 72.0
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x, 90 + r * 22, x + widths[c], 90 + (r + 1) * 22)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y0 + 15), cell, fontsize=9)
            x += widths[c]
    y = 230.0
    page.insert_text((72, y), "Prepared by Dr. Meenakshi Rajan, AP/ECE", fontsize=10)
    y += 40
    page.insert_text((72, y), "Charging a capacitor", fontsize=16)
    y += 28
    for p in KEPT:
        page.insert_text((72, y), p, fontsize=10)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data, PDF, PDF_SECRETS


# ---------------------------------------------------------------------------
# key variants, separator-less labels, multi-line values
# ---------------------------------------------------------------------------

VARIANT_HEADER = [
    "Name of the Resource Person: Dr. Priyadharshini Mohan",
    "Content Developer: Mr. Vignesh Kumar",
    "Approved by\tDr. Balasubramanian Iyer",
    "Prepared by:",
    "Dr. Sowmiya Krishnan, Assistant Professor (Sr. G)",
    "Video Duration (approx.): 14 min",
    "Total Duration of the Video: 16 minutes",
    "Course Code & Name: EC3353 & Electronic Devices",
    "Year / Semester: II / IV",
    "Academic Year: 2024-25",
    "Version: 3.4",
    "This script was prepared by Dr. Harini Venkatesh for the e-content cell.",
]
VARIANT_SECRETS = ("Priyadharshini", "Vignesh", "Balasubramanian", "Sowmiya", "Harini", "14 min", "16 minutes",
                   "EC3353", "II / IV", "2024-25", "3.4")


def variants_docx() -> tuple[bytes, str, tuple[str, ...]]:
    return _docx([("p", line) for line in VARIANT_HEADER] + _script()), DOCX, VARIANT_SECRETS


UPPER_TXT = """SCRIPT PREPARED BY: DR. K. SURESHKUMAR
Reviewed and approved by: Dr. R. Venkataraman, HoD/MECH
Duration\t12 min

THERMODYNAMICS - FIRST LAW

The first law of thermodynamics states that energy can neither be created nor destroyed.
For a closed system, Q = dU + W, where Q is the heat supplied and W is the work done.
A gas heated at constant volume does no work, so all the heat raises its internal energy.
"""
UPPER_SECRETS = ("Sureshkumar", "SURESHKUMAR", "Venkataraman", "12 min")


def upper_txt() -> tuple[bytes, str, tuple[str, ...]]:
    return UPPER_TXT.encode("utf-8"), TXT, UPPER_SECRETS


FRONTMATTER_MD = """---
author: Dr. Anbarasi Selvam
date: 2024-07-01
duration: 9 min
---

# Heat transfer

> Prepared by: Dr. Anbarasi Selvam, Assistant Professor

_Reviewed by_ — Prof. Gnanasekar Pillai

<!-- SME: Dr. Anbarasi Selvam; video duration 9 min -->

**Duration:** 9 min

## Conduction

Conduction is the transfer of heat through a solid without any movement of the material.
Fourier's law says the heat flow rate is proportional to the temperature gradient.

## Convection

Convection carries heat by the movement of a fluid, such as warm air rising from a heater.

*Prepared by Dr. Anbarasi Selvam · Page 2 of 2*
"""
FRONTMATTER_SECRETS = ("Anbarasi", "Selvam", "Gnanasekar", "9 min")


def frontmatter_md() -> tuple[bytes, str, tuple[str, ...]]:
    return FRONTMATTER_MD.encode("utf-8"), MD, FRONTMATTER_SECRETS


# ---------------------------------------------------------------------------
# AV-table scripts, admin data inside meta lines and headings, per-clip scaffolding
# ---------------------------------------------------------------------------

AV_META = [
    ["VIDEO DETAILS"],
    ["SME Name", "Dr. Kalaiselvi Arumugam"],
    ["Course", "Engineering Physics"],
    ["Video Duration", "8 min"],
]
AV_ROWS = [
    ["Sl. No", "Time", "Visual", "Narration"],
    ["1", "0:00 - 0:10", "Title card with a swinging pendulum",
     "In this video, we will learn about simple harmonic motion."],
    ["2", "0:10 - 0:50", "A pendulum swings; its shadow traces a sine wave",
     "A body is in simple harmonic motion when its acceleration is proportional to its displacement and opposite in "
     "direction."],
    ["3", "0:50 - 1:30", "Spring-mass system oscillating",
     "A mass on a spring oscillates with period T = 2 pi root of m over k."],
    ["4", "1:30 - 2:10", "Graph of displacement against time",
     "The displacement follows a sine curve whose amplitude stays constant when there is no friction."],
    ["5", "2:10 - 2:20", "Coming up next card", "In the next video, we will study damped oscillations."],
]
AV_SECRETS = ("Kalaiselvi", "8 min", "VIDEO DETAILS", "In the next video", "damped oscillations")


def av_table_docx() -> tuple[bytes, str, tuple[str, ...]]:
    return _docx([], tables=[AV_META, AV_ROWS]), DOCX, AV_SECRETS


META_LINES_TXT = """SUBJECT NAME : EC3352 - SIGNALS AND SYSTEMS
UNIT NAME : UNIT III - TRANSIENTS (R2021)
SESSION 5 - RC Circuits | Dr. Ramesh Kumar (Duration: 15 min)

CLIP 1 SCRIPT - CHARGING A CAPACITOR (Video No: 07)

SEGMENT 1 - CHARGING A CAPACITOR [0:30 - 2:00] (Duration: 90 sec)
Aadhi speaks:
When a capacitor is connected to a battery through a resistor, it charges gradually.
The time constant tau = R x C sets how fast the capacitor charges.

SEGMENT 2 - THE TIME CONSTANT [2:00 - 3:00]
Aadhi speaks:
After one time constant the capacitor reaches about 63 percent of the supply voltage.
"""
META_LINES_SECRETS = ("EC3352", "R2021", "Ramesh", "15 min", "90 sec", "Video No", "Duration:")


def meta_lines_txt() -> tuple[bytes, str, tuple[str, ...]]:
    return META_LINES_TXT.encode("utf-8"), TXT, META_LINES_SECRETS


FLOW_TXT = """CLIP 1 SCRIPT - CHARGING A CAPACITOR

SEGMENT 1 - TITLE SLIDE [0:00 - 0:10]
ON SCREEN: CHARGING A CAPACITOR

SEGMENT 2 - CHARGING [0:10 - 1:30]
Aadhi speaks:
When a capacitor is connected to a battery through a resistor, it charges gradually.
The time constant tau = R x C sets how fast the capacitor charges.

SEGMENT 3 - WHAT'S NEXT? [1:30 - 1:40]
Aadhi speaks:
In the next video, we will see how the capacitor discharges.

SEGMENT 4 - THANK YOU [1:40 - 1:45]
Aadhi speaks:
Thank you for watching.

CLIP 2 SCRIPT - DISCHARGING A CAPACITOR

SEGMENT 1 - WELCOME BACK [0:00 - 0:20]
Aadhi speaks:
Welcome back! In the previous video, we learned how a capacitor charges.

SEGMENT 2 - RECAP OF PREVIOUS CLIP [0:20 - 0:40]
Aadhi speaks:
In the last clip we saw that the capacitor charges through the resistor.

SEGMENT 3 - DISCHARGING [0:40 - 1:40]
Aadhi speaks:
When the battery is removed, the capacitor discharges through the resistor.
After one time constant the capacitor reaches about 63 percent of the supply voltage.

SEGMENT 4 - CLOSING [1:40 - 1:50]
Aadhi speaks:
That is all for this clip. See you in the next video.
"""
FLOW_SECRETS = ("Welcome back", "In the next video", "In the previous video", "Thank you for watching", "next video",
                "Title Slide", "What's next")


def flow_txt() -> tuple[bytes, str, tuple[str, ...]]:
    return FLOW_TXT.encode("utf-8"), TXT, FLOW_SECRETS


NOTE_HEADER = ["SME Name: Dr. Kavitha Ramanujam", "Department: Physics", "Video Duration: 6 min"]
NOTE_SCRIPT = [
    ("h", "CLIP 1 SCRIPT - LIGHT"),
    ("h", "SEGMENT 1 - REFLECTION [0:00 - 1:00]"),
    ("p", "Aadhi speaks:"),
    ("p", "Light bounces off a smooth surface so that the angle of incidence equals the angle of reflection."),
    ("p", "NOTE: Dr. Kavitha will record this segment on Monday."),
    ("p", "Georg Simon Ohm published his law in 1827, long before optics was unified."),
    ("p", "The duration of the pulse is 2 ms, so the flash is very short."),
]
NOTE_SECRETS = ("Kavitha", "Ramanujam", "record", "6 min")


def note_docx() -> tuple[bytes, str, tuple[str, ...]]:
    return _docx([("p", line) for line in NOTE_HEADER] + NOTE_SCRIPT), DOCX, NOTE_SECRETS


ALL = {
    "sno_table.docx": sno_table_docx,
    "sno_table.pdf": sno_table_pdf,
    "variants.docx": variants_docx,
    "upper.txt": upper_txt,
    "frontmatter.md": frontmatter_md,
    "av_table.docx": av_table_docx,
    "meta_lines.txt": meta_lines_txt,
    "flow.txt": flow_txt,
    "note.docx": note_docx,
}
