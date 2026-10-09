"""source_scope: only teachable content reaches the model; metadata, admin data and production notes move out."""

from __future__ import annotations

import asyncio
import re

import pytest

from aadhi.pipeline.base import ExcludedItem, SourceChunk
from aadhi.pipeline.chunking import chunk_markdown, looks_like_heading
from aadhi.pipeline.docx_extract import docx_to_markdown
from aadhi.pipeline.ingest import ingest_source, text_to_markdown
from aadhi.pipeline.pdf_layout import starts_with_label
from aadhi.pipeline.source_scope import (
    classify_kv,
    resolve_visual_notes,
    scope_source,
    scope_summary,
    smart_title,
    strip_timecodes,
)
from aadhi.schemas.screenplay import SourceFigure
from tests.pipeline.dbutil import get_source, seed_project
from tests.pipeline.fixtures.sme_sources import (
    HEADER_VALUES,
    SME_SCRIPT_TXT,
    header_table_docx,
    header_table_pdf,
    sample_template_bytes,
)
from tests.pipeline.sources import SAMPLE_MARKDOWN, make_docx

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TIMECODE = re.compile(r"\d{1,2}:\d{2}\s*(?:-|–|—|to)\s*\d{1,2}:\d{2}")


def run_ingest(job_ctx, data: bytes, mime: str, filename: str):
    seeded = seed_project(job_ctx.assets.storage, data, mime, filename)
    job_ctx.project_id = seeded.project_id
    return asyncio.run(ingest_source(job_ctx, get_source(seeded.source_id)))


@pytest.fixture(scope="module")
def template():
    res = docx_to_markdown(sample_template_bytes())
    figures = [SourceFigure(id=im.figure_id, caption=im.caption) for im in res.images]
    return res.markdown, scope_source(res.markdown, figures=figures)


# ---------------------------------------------------------------------------
# the real SME template (6 clips)
# ---------------------------------------------------------------------------


def test_sme_template_metadata_and_format(template):
    _, scoped = template
    assert scoped.source_format == "sme_script"
    meta = scoped.document_meta
    assert meta.subject_name == "Digital System Design"
    assert meta.unit_name == "Logic Gates and Minimization Techniques"
    assert meta.session_number == "Session 2"
    assert meta.session_title == "Boolean Postulates, Laws, and Minimization of Boolean Expressions"
    md = scoped.markdown
    for label in ("SUBJECT NAME", "UNIT NAME", "SESSION 2"):
        assert label not in md


def test_sme_template_production_scaffolding_is_gone(template):
    raw, scoped = template
    md = scoped.markdown
    assert TIMECODE.search(raw) and not TIMECODE.search(md)
    assert "Estimated duration" in raw and "Estimated duration" not in md
    assert not re.search(r"\bCLIP\s+\d", md) and "SCRIPT -" not in md
    for gone in ("TITLE CARD", "BRIDGE TO NEXT PART", "COMING UP NEXT", "No farewell", "fade to black",
                 "WHAT THIS VIDEO WILL COVER", "ON SCREEN", "SOURCE IMAGE", "Aadhi speaks", "BOARD displays",
                 "BOARD Title", "ANIMATION", "DETAILED ANIMATION", "SEGMENT", "SCENE 1"):
        assert gone not in md, gone
    # per-clip title text is not content; the first clip's objectives are kept once
    assert md.count("Learning objectives") == 1
    assert "## Learning objectives" in md and "- Why minimization is important" in md


def test_sme_template_animation_moves_to_visual_notes(template):
    raw, scoped = template
    notes = [text for _, text in scoped.visual_notes_raw]
    assert len(notes) >= 20
    assert any("two switches" in n for n in notes) and "two switches" not in scoped.markdown
    assert any("pyramid" in n for n in notes) and "pyramid" not in scoped.markdown.lower()
    assert all(anchor for anchor, _ in scoped.visual_notes_raw)
    # per-clip title-card and bridge visuals are dropped, the first title card's idea is kept
    assert not any("COMING UP NEXT" in n for n in notes)
    assert any("golden glow over a dark digital circuit" in n for n in notes)
    assert not any("three gate symbols" in n for n in notes)  # clip 2's title card


def test_sme_template_keeps_every_teaching_section(template):
    _, scoped = template
    md = scoped.markdown
    for text in (
        "THE SECRET LANGUAGE BEHIND COMPUTERS", "WHAT IS BOOLEAN ALGEBRA?", "English mathematician George Boole",
        "## BOOLEAN VARIABLES", "THREE BASIC LOGICAL OPERATIONS", "POSTULATE 1: CLOSURE PROPERTY",
        "POSTULATE 2: IDENTITY ELEMENTS", "POSTULATE 3: COMPLEMENTS", "A + A̅ = 1",
        "COMMUTATIVE LAW", "A + B = B + A", "ASSOCIATIVE LAW", "DISTRIBUTIVE LAW", "A + BC = (A + B)(A + C)",
        "IDEMPOTENT LAW", "NULL LAW", "DE MORGAN'S THEOREMS", "(A · B)̅ = A̅ + B̅",
        "REAL-WORLD ANALOGY", "WHY SIMPLIFY BOOLEAN EXPRESSIONS?", "EXAMPLE 1: BOOLEAN MINIMIZATION",
        "Y = A + AB", "EXAMPLE 2: BOOLEAN MINIMIZATION", "Y = A(B + B̅)", "REAL-WORLD ENGINEERING APPLICATIONS",
        "QUICK QUIZ", "Who developed Boolean Algebra?", "To reduce hardware complexity", "## SUMMARY",
        "Boolean minimization reduces circuit complexity",
    ):
        assert text in md, text
    # clips become chapter headings of one lecture; segments are sections under them
    assert "# BOOLEAN POSTULATES\n" in md and "## COMMUTATIVE LAW" in md
    # formulas the DOCX styled as bold "headings" are content lines, not sections
    assert "## A + B = B + A" not in md and "\nA + B = B + A\n" in md
    # narrator/board labels are dropped: their content reads as ordinary text
    assert "Explanation:" not in md and "Board points:" not in md
    assert "\nEvery second, your smartphone performs millions of calculations.\n" in md
    assert "- Boolean Logic = mathematical foundation of digital systems" in md
    assert "BOOLEAN ALGEBRA VS ORDINARY ARITHMETIC" in md  # a board title that adds to its heading stays
    assert "\nTHE SECRET LANGUAGE BEHIND COMPUTERS\n" not in md  # one that repeats it does not


def test_sme_template_figures_get_their_source_captions(template):
    _, scoped = template
    caps = {f.id: f.caption for f in scoped.figures}
    assert caps["fig-1"] == "George Boole: Founder of Digital Logic"
    assert "[Figure fig-7: De Morgan's Theorem: A Simplified Overview]" in scoped.markdown
    assert len(scoped.figures) == 8


def test_sme_template_exclusions_are_recorded(template):
    _, scoped = template
    by_cat: dict[str, list[ExcludedItem]] = {}
    for e in scoped.excluded:
        by_cat.setdefault(e.category, []).append(e)
        assert e.source == "rules" and len(e.text) <= 300
    assert len(by_cat["duration"]) == 5  # one "Estimated duration" per clip 2-6
    assert len(by_cat["timecode"]) == 1 and by_cat["timecode"][0].text.startswith("36 timecode")
    structure = [e.text for e in by_cat["structure"]]
    assert sum(t.startswith("Title card") for t in structure) == 6
    assert sum("BRIDGE" in t for t in structure) == 5
    assert any("clip headings" in t for t in structure)
    assert by_cat["production_note"]


def test_sme_template_visual_notes_resolve_to_chunks(template):
    _, scoped = template
    chunks = chunk_markdown(scoped.markdown)
    notes = resolve_visual_notes(scoped.visual_notes_raw, chunks)
    assert len(notes) == len(scoped.visual_notes_raw)
    assert [n.id for n in notes[:2]] == ["v0001", "v0002"]
    assert all(n.near_chunk_id for n in notes)
    by_id = {c.id: c for c in chunks}
    switches = next(n for n in notes if "two switches" in n.text)
    assert "BOOLEAN VARIABLES" in by_id[switches.near_chunk_id].heading_path
    # chunks of one lecture: chapter (clip) > section (segment)
    assert any(c.heading_path[:2] == ["FUNDAMENTAL BOOLEAN LAWS", "NULL LAW"] for c in chunks)


# ---------------------------------------------------------------------------
# header tables / key-value header blocks (DOCX, PDF, TXT) through ingest
# ---------------------------------------------------------------------------


def _assert_header_scoped(res, events):
    text = res.markdown + "\n".join(c.text + " ".join(c.heading_path) for c in res.chunks)
    for value in HEADER_VALUES:
        assert value not in text, value
    assert "Video Lecture Script" not in text
    assert "Ohm's law states that the current through a conductor is proportional" in text
    assert "V = I x R" in text and "4 ohm resistor" in text
    assert "The duration of the pulse is 2 ms" in text  # physics, not a video duration
    cats = {e.category for e in res.excluded}
    assert {"person", "duration", "admin"} <= cats
    recorded = " ".join(e.text for e in res.excluded)
    for value in ("Kavitha", "6 minutes", "EE3301", "12/03/2024", "Anand"):
        assert value in recorded, value
    # the job event says what kind of thing was ignored, never the values
    logs = " ".join(e["message"] for e in events if e["type"] == "log")
    assert "Ignored" in logs and "SME/author details" in logs
    for value in HEADER_VALUES:
        assert value not in logs


def test_header_table_docx_is_removed(job_ctx):
    res = run_ingest(job_ctx, header_table_docx(), DOCX, "script.docx")
    _assert_header_scoped(res, job_ctx.events)
    assert "|" not in res.markdown  # the header table is gone entirely


def test_header_table_pdf_is_removed(job_ctx):
    res = run_ingest(job_ctx, header_table_pdf(), "application/pdf", "script.pdf")
    _assert_header_scoped(res, job_ctx.events)
    assert "Word count" not in res.markdown and "Prepared by" not in res.markdown


def test_sme_text_script_through_ingest(job_ctx):
    res = run_ingest(job_ctx, SME_SCRIPT_TXT.read_bytes(), "text/plain", "script.txt")
    assert res.source_format == "sme_script"
    assert res.document_meta.subject_name == "Basic Electrical Engineering"
    assert res.document_meta.unit_name == "DC Circuits"
    assert (res.document_meta.session_number, res.document_meta.session_title) == ("Session 4", "Ohm's Law and Electrical Power")
    md = res.markdown
    for gone in ("Kavitha", "Associate Professor", "EE3301", "R2021", "6 minutes", "Estimated duration", "TITLE CARD",
                 "COMING UP NEXT", "Clean fade", "ANIMATION", "[0:", "(1:50"):
        assert gone not in md, gone
    for kept in ("Ohm's law states", "V = I x R", "Georg Simon Ohm in 1827", "I = V / R = 12 / 4 = 3 A",
                 "The duration of the pulse in the next experiment is 2 ms", "P = I^2 x R = V^2 / R",
                 "Question 2: What is the power used by a 230 V heater drawing 4 A?", "Answer: 920 W.",
                 "## SUMMARY", "# ELECTRICAL POWER"):
        assert kept in md, kept
    assert md.count("# ELECTRICAL POWER") == 1  # clip + same-named segment become one heading
    assert md.count("Learning objectives") == 1
    notes = res.visual_notes
    assert [n.id for n in notes] == [f"v{i:04d}" for i in range(1, len(notes) + 1)]
    assert all(n.near_chunk_id in {c.id for c in res.chunks} for n in notes)
    assert any("ammeter reading rise" in n.text for n in notes)
    assert not any("lightning icon" in n.text for n in notes)  # clip 2's title card
    assert any(e["message"].startswith("Read the source as a video script.") for e in job_ctx.events if e["type"] == "log")


def test_existing_sources_pass_through_unchanged():
    assert scope_source(SAMPLE_MARKDOWN).markdown == SAMPLE_MARKDOWN
    md = docx_to_markdown(make_docx()).markdown
    res = scope_source(md)
    assert res.markdown == md and res.excluded == [] and res.visual_notes_raw == []
    txt = text_to_markdown("UNIT 2 Electric circuits\n\n1. Introduction\nCurrent flows when a circuit is closed.\n")
    assert scope_source(txt).markdown == txt


# ---------------------------------------------------------------------------
# false-positive guards: never strip real teaching content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, md, kept", [
    ("physics",
     "# Pulses\n\nThe duration of the pulse is 2 ms, so the energy delivered is small.\n\nDuration: 2 ms\n\nTime: 5 s\n\n"
     "Length: 2 m\n\nA longer pulse heats the resistor more.\n",
     ["The duration of the pulse is 2 ms", "Duration: 2 ms", "Time: 5 s", "Length: 2 m"]),
    ("history",
     "# The Revolt of 1857\n\nOn 10 May 1857 the sepoys at Meerut rebelled.\n\nDate: 10 May 1857\n\n"
     "Mangal Pandey is remembered as an early hero of the revolt.\n\nVersion: the British account differs.\n",
     ["On 10 May 1857", "Date: 10 May 1857", "Mangal Pandey", "Version: the British account differs."]),
    ("boole",
     "# Boolean algebra\n\nGeorge Boole introduced Boolean algebra in 1847.\n\nAuthor: George Boole\n\n"
     "Developed by: George Boole\n\nIn this session we will see why it matters.\n",
     ["George Boole introduced Boolean algebra in 1847.", "Author: George Boole", "Developed by: George Boole"]),
    ("dbms",
     "# Relations\n\nA tuple is one row of a relation, for example:\n\nEmployee ID: 101\nName: Ravi Kumar\n"
     "Department: Sales\n\n| Employee ID | Name | Department |\n|---|---|---|\n| 101 | Ravi | Sales |\n",
     ["Employee ID: 101", "Name: Ravi Kumar", "Department: Sales", "| 101 | Ravi | Sales |"]),
    ("literature",
     "Title: Gitanjali\nAuthor: Rabindranath Tagore\n\n# Summary\n\nGitanjali won the Nobel Prize in 1913.\n",
     ["Author: Rabindranath Tagore", "Gitanjali won the Nobel Prize in 1913."]),
    ("units table first",
     "| Quantity | Unit |\n|---|---|\n| Time | second |\n| Length | metre |\n\nTime is measured in seconds.\n",
     ["| Time | second |", "| Length | metre |"]),
    ("quiz and summary",
     "# Quiz\n\nQ1. What is the unit of resistance?\n\nAnswer: ohm\n\n# Summary\n\nV = IR links voltage and current.\n",
     ["Q1. What is the unit of resistance?", "Answer: ohm", "# Summary", "V = IR links voltage and current."]),
    ("regulation as content",
     "# Voltage regulation\n\nRegulation: the change in output voltage from no load to full load, as a percentage.\n",
     ["Regulation: the change in output voltage"]),
    ("notes heading timecode",
     "# Timetable\n\n## Morning session [9:00 - 10:30]\n\nLab work on diodes.\n",
     ["## Morning session [9:00 - 10:30]"]),
])
def test_teaching_content_is_never_stripped(name, md, kept):
    res = scope_source(md)
    for text in kept:
        assert text in res.markdown, (name, text)
    assert res.source_format != "sme_script"
    assert not [e for e in res.excluded if e.category in ("person", "duration")], name


def test_sme_script_content_that_looks_like_production():
    md = ("CLIP 1 SCRIPT - PROBABILITY AND PHYSICS\n\nSEGMENT 1 - CARD PROBABILITY [0:00 - 0:40]\n\nAadhi speaks:\n\n"
          "A deck has 52 cards, so P(ace) = 4/52.\n\nSEGMENT 2 - COMING UP WITH A SOLUTION [0:40 - 1:10]\n\n"
          "Aadhi speaks:\n\nWe list every outcome first.\n\nSEGMENT 3 - OBJECTIVES OF A COMPILER [1:10 - 1:40]\n\n"
          "Aadhi speaks:\n\nA compiler translates source code.\n\nSEGMENT 4 - ATOMS [1:40 - 2:10]\n\n"
          "Transition: the electron moves from n = 2 to n = 1 and emits a photon.\n\n"
          "Illustration 1: a ball thrown upward at 20 m/s rises for 2 s.\n\n"
          "Sound: a longitudinal wave that needs a medium.\n\n"
          "Note: the crocodile clip connects the probe to the reference frame.\n\n"
          "NOTE: No farewell. Clean fade to black.\n\nANIMATION: Electron drops to a lower orbit.\n")
    res = scope_source(md)
    assert res.source_format == "sme_script"
    for kept in ("## CARD PROBABILITY", "P(ace) = 4/52", "## COMING UP WITH A SOLUTION", "We list every outcome first.",
                 "## OBJECTIVES OF A COMPILER", "A compiler translates source code.", "Transition: the electron moves",
                 "Illustration 1: a ball thrown upward", "Sound: a longitudinal wave",
                 "Note: the crocodile clip connects the probe"):
        assert kept in res.markdown, kept
    assert "Clean fade" not in res.markdown and "Electron drops" not in res.markdown
    assert "Learning objectives" not in res.markdown


def test_header_lines_in_plain_notes():
    md = ("SME Name: Dr. A. Kumar\nDesignation: Associate Professor\nDepartment: ECE\nVideo Duration: 8 min\n"
          "Date: 01/02/2025\n\n# Diodes\n\nA diode conducts current in one direction only.\n\nPage 1 of 3\n\n"
          "© 2025 Example College. All rights reserved.\n\nPrepared by: Dr. A. Kumar\n")
    res = scope_source(md)
    assert res.markdown == "# Diodes\n\nA diode conducts current in one direction only.\n"
    assert [e.category for e in res.excluded] == ["person", "person", "person", "duration", "admin", "admin", "admin",
                                                 "person"]


def test_front_matter_faculty_block_and_sign_off():
    md = "Faculty: Dr. A. Kumar\nDepartment: ECE\n\n# Diodes\n\nA diode conducts in one direction.\n"
    assert scope_source(md).markdown == "# Diodes\n\nA diode conducts in one direction.\n"
    tail = ("# Diodes\n\nA diode conducts in one direction.\n\nFaculty: Dr. A. Kumar\nDesignation: Professor\n"
            "College: Example College\n")
    assert "Kumar" not in scope_source(tail).markdown  # a block of three admin fields is a sign-off


def test_column_wise_header_table_and_pairs_row():
    md = ("| SME Name | Department | Video Duration |\n|---|---|---|\n| Dr. Rao | ECE | 5 min |\n\n"
          "| Subject | Digital Electronics | Unit | 2 |\n|---|---|---|---|\n\n# Gates\n\nAn AND gate outputs 1 only when both inputs are 1.\n")
    res = scope_source(md)
    assert res.markdown == "# Gates\n\nAn AND gate outputs 1 only when both inputs are 1.\n"
    assert res.document_meta.subject_name == "Digital Electronics" and res.document_meta.unit_name == "Unit 2"
    assert {e.category for e in res.excluded} == {"person", "duration"}


def test_av_script_table():
    md = ("SEGMENT 1 - OHM'S LAW [0:00 - 0:40]\n\n| Scene | Visual | Narration | Duration |\n|---|---|---|---|\n"
          "| 1 | A battery and a resistor glow | Ohm's law says V = IR for a metallic conductor. | 0:20 |\n"
          "| 2 | Ammeter needle rises | Doubling the voltage doubles the current. | 0:20 |\n\n"
          "Aadhi speaks:\n\nResistance limits the current.\n\nANIMATION: Charges slow down in the resistor.\n")
    res = scope_source(md)
    assert res.source_format == "sme_script"
    assert "Ohm's law says V = IR for a metallic conductor." in res.markdown
    assert "Doubling the voltage doubles the current." in res.markdown
    assert "battery and a resistor glow" not in res.markdown and "|" not in res.markdown
    notes = " ".join(t for _, t in res.visual_notes_raw)
    assert "Ammeter needle rises" in notes and "Charges slow down" in notes
    assert any(e.category == "duration" for e in res.excluded)


def test_bare_visual_label_does_not_swallow_narration():
    md = ("SEGMENT 1 - DIODES [0:00 - 0:30]\n\nAadhi speaks:\n\nA diode conducts in one direction.\n\n"
          "DETAILED ANIMATION / VISUAL:\n\nShow a diode with arrows that fade in.\n\n"
          "Reverse bias blocks the current almost completely.\n\nANIMATION: Arrows stop at the junction.\n")
    res = scope_source(md)
    assert "Reverse bias blocks the current almost completely." in res.markdown
    assert "Show a diode" not in res.markdown


def test_timecode_variants():
    for heading in ("WHAT IS A DIODE? [0:10 - 1:15]", "WHAT IS A DIODE? (0:45-1:00)", "WHAT IS A DIODE? [0:10 – 1:15]",
                    "WHAT IS A DIODE? [0:10 â€“ 1:15]", "WHAT IS A DIODE? [00:01:10 - 00:02:00]",
                    "WHAT IS A DIODE? (30 sec)", "WHAT IS A DIODE? 0:10 - 1:15"):
        text, found = strip_timecodes(heading)
        assert text == "WHAT IS A DIODE?" and found, heading
    assert strip_timecodes("The pulse lasts (2 ms) at 10:30 today")[0] == "The pulse lasts (2 ms) at 10:30 today"
    md = "# Diodes\n\n## Forward bias [0:10 - 1:15]\n\nIt conducts.\n\n## Reverse bias [1:15 - 2:00]\n\nIt blocks.\n"
    res = scope_source(md)
    assert "## Forward bias\n" in res.markdown and "## Reverse bias\n" in res.markdown
    assert [e.category for e in res.excluded] == ["timecode"]


def test_misread_layout_falls_back_instead_of_losing_the_source():
    body = "\n\n".join(f"Aadhi speaks:\n\nSentence {i} explains how a capacitor stores charge between two plates."
                       for i in range(30))
    md = f"SCENE 1 - TITLE CARD [0:00 - 0:10]\n\nANIMATION: Title glows.\n\nBOARD Title: CAPACITORS\n\n{body}\n"
    res = scope_source(md)
    assert "Sentence 29 explains how a capacitor stores charge" in res.markdown
    assert res.warnings


def test_scope_summary_never_contains_values():
    excluded = [ExcludedItem(category="person", text="SME Name: Dr. Secret Person", reason="x"),
                ExcludedItem(category="duration", text="Video Duration: 6 minutes", reason="x"),
                ExcludedItem(category="timecode", text="3 timecode(s), e.g. [0:10 - 1:15]", reason="x")]
    text = scope_summary(excluded, sections=12, visual_notes=4, source_format="sme_script")
    assert text == ("Read the source as a video script. Ignored 3 non-teaching item(s) (SME/author details, durations, "
                    "timecodes); kept 12 content section(s) and 4 visual suggestion(s).")
    assert "Secret" not in text and "6 minutes" not in text
    assert scope_summary([], sections=3, visual_notes=0, source_format="notes") == ""


def test_classify_kv_tiers_and_values():
    assert classify_kv("SME Name : Dr. X").tier == 1
    assert classify_kv("**Estimated duration:** 3.5-4 minutes").category == "duration"
    assert classify_kv("Video No. - 4").category == "admin"
    assert classify_kv("Duration: 2 ms") is None  # not a video length
    assert classify_kv("Version: the British account") is None
    assert classify_kv("Department: Sales").tier == 2
    assert classify_kv("Author: Tagore").tier == 3
    assert classify_kv("Ohm's law: V = IR") is None
    assert classify_kv("UNIT NAME : LOGIC GATES").meta_field == "unit_name"
    assert smart_title("LOGIC GATES AND CMOS DESIGN") == "Logic Gates and CMOS Design"
    assert smart_title("Ohm's Law") == "Ohm's Law"


def test_resolve_visual_notes_follows_document_order():
    chunks = [SourceChunk(id="c0001", heading_path=["Example"], text="first"),
              SourceChunk(id="c0002", heading_path=["Other"], text="middle"),
              SourceChunk(id="c0003", heading_path=["Example"], text="second")]
    notes = resolve_visual_notes([("Example", "a"), ("Other", "b"), ("Example", "c"), ("missing", "d")], chunks)
    assert [n.near_chunk_id for n in notes] == ["c0001", "c0002", "c0003", None]
    assert len(resolve_visual_notes([("missing", "d")], chunks, drop_unresolved=True)) == 0


def test_a_segment_titled_like_its_clip_gets_its_own_visual_suggestion():
    kcl = "KIRCHHOFF’S CURRENT LAW"
    chunks = [SourceChunk(id="c0001", heading_path=[kcl, "INTRODUCTION"], text="objectives"),
              SourceChunk(id="c0002", heading_path=[kcl, "HOOK: WHERE DOES THE CURRENT GO?"], text="hook"),
              SourceChunk(id="c0003", heading_path=[kcl, kcl], text="the law"),
              SourceChunk(id="c0004", heading_path=["KIRCHHOFF’S VOLTAGE LAW"], text="kvl")]
    raw = [("INTRODUCTION", "Bullets appear one by one."), (kcl, "Charges arrive at a node from two wires."),
           ("KIRCHHOFF’S VOLTAGE LAW", "A charge travels around a loop.")]
    assert [n.near_chunk_id for n in resolve_visual_notes(raw, chunks)] == ["c0001", "c0003", "c0004"]
    # an anchor that is only ever an ancestor heading still finds the first chunk under it
    assert resolve_visual_notes([(kcl, "x")], chunks[:2])[0].near_chunk_id == "c0001"


def test_layout_helpers():
    assert not looks_like_heading("A + B = B + A") and not looks_like_heading("Y = A + AB")
    assert looks_like_heading("DE MORGAN'S THEOREMS")
    assert starts_with_label("SME Name: Dr. X") and starts_with_label("Aadhi speaks:")
    assert not starts_with_label("The current, as we saw in the last section of the chapter, is: I")
