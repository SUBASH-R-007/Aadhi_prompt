"""source_scope regressions: administrative data in the shapes real SME documents use never reaches the model.

Each test names the verifier finding it covers. The end-to-end checks (every prompt and the screenplay)
are in ``test_leak_end_to_end.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from aadhi.pipeline.docx_extract import docx_to_markdown
from aadhi.pipeline.ingest import ingest_source, text_to_markdown
from aadhi.pipeline.source_scope import classify_kv, scope_source, smart_title, strip_title_admin
from tests.pipeline.dbutil import get_source, seed_project
from tests.pipeline.fixtures import leak_sources as L

CONTENT = ("When a capacitor is connected to a battery through a resistor, it charges gradually.\n\n"
           "The time constant tau = R x C sets how fast the capacitor charges.\n")


def ingest(job_ctx, builder):
    data, mime, secrets = builder()
    seeded = seed_project(job_ctx.assets.storage, data, mime, f"source.{mime.rsplit('/', 1)[-1][:4]}")
    job_ctx.project_id = seeded.project_id
    return asyncio.run(ingest_source(job_ctx, get_source(seeded.source_id))), secrets


def model_text(res) -> str:
    """Everything of an IngestResult that a prompt can carry."""
    return "\n".join([res.markdown, *(c.text + " " + " ".join(c.heading_path) for c in res.chunks),
                      *(v.text for v in res.visual_notes), *(f.caption for f in res.figures), *res.warnings])


def assert_clean(res, secrets) -> None:
    text = model_text(res).lower()
    for secret in secrets:
        assert secret.lower() not in text, secret


# ---------------------------------------------------------------------------
# [blocker] serial-number header tables (DOCX and PDF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [L.sno_table_docx, L.sno_table_pdf], ids=["docx", "pdf"])
def test_serial_number_header_tables_are_removed(job_ctx, builder):
    res, secrets = ingest(job_ctx, builder)
    assert_clean(res, secrets)
    assert "|" not in res.markdown
    for kept in L.KEPT[:2]:
        assert kept in res.markdown
    recorded = " ".join(e.text for e in res.excluded)
    assert ("Lakshmi" in recorded) or ("Meenakshi" in recorded)  # recorded for the teacher and the leak lint


def test_serial_column_and_key_value_header_rows():
    for header in ("| S.No | Particulars | Details |", "| Sl. No | Field | Value |", "| # | Item | Description |",
                   "| No. | Parameter | Value |"):
        md = (f"{header}\n|---|---|---|\n| 1 | Name of the SME | Dr. Lakshmi Narayanan |\n| 2 | Video Duration | 11 min |\n"
              f"| 3 | Course Code | EC3352 |\n\n# Capacitors\n\n{CONTENT}")
        res = scope_source(md)
        assert res.markdown == f"# Capacitors\n\n{CONTENT}", header
        assert {e.category for e in res.excluded} == {"person", "duration", "admin"}
    # roman / bare serials without a header cell
    md = (f"| i | SME Name | Dr. Lakshmi Narayanan |\n| ii | Reviewed by | Dr. Prakash Rao |\n| iii | Video Duration | 11 min |"
          f"\n\n# Capacitors\n\n{CONTENT}")
    assert "Lakshmi" not in scope_source(md).markdown


# ---------------------------------------------------------------------------
# [major] key variants, separator-less labels (four contexts)
# ---------------------------------------------------------------------------

VARIANT_LINES = [
    "Name of the Resource Person: Dr. Priya Mohan", "Resource Person: Dr. Priya Mohan",
    "Content Developer: Mr. Vignesh Kumar", "Content Writer: Ms. Anu Raj", "Developed by: Dr. Priya Mohan",
    "Created by: Dr. Priya Mohan", "Handled by: Dr. Priya Mohan", "Course Faculty: Dr. Priya Mohan",
    "Faculty Name & Designation: Dr. Priya Mohan, AP/ECE", "SME Name with Designation: Dr. Priya Mohan, Professor",
    "Name of the Faculty with Designation: Dr. Priya Mohan, AP", "Instructional Designer: Ms. Anu Raj",
    "Video Duration (approx.): 10 min", "Duration (in minutes): 12", "Total Duration of the Video: 15 Minutes",
    "Estimated Video Duration: 12 min", "Total Video Length: 12 min", "Expected Video Length: 12 minutes",
    "Duration of this clip: 2 min", "Course Code & Name: EC3352 & Circuit Theory",
    "Subject Code & Name: EC3352 & Circuit Theory", "Course Code/Name: EC3352 / Circuit Theory",
    "Year / Semester: II / IV", "Approved by\tDr. Balasubramanian Iyer", "Prepared by Dr. Meenakshi Rajan, AP/ECE",
    "Total running time 15 minutes", "Duration\t12 min", "Script prepared by: Dr. K. Sureshkumar",
    "Reviewed and approved by: Dr. R. Venkataraman, HoD/MECH",
]
CONTEXTS = {
    "front matter": "{line}\n\n# RC circuits\n\n" + CONTENT,
    "after the title heading": "# RC circuits\n\n{line}\n\n## Charging\n\n" + CONTENT,
    "next to an SME line": "# RC circuits\n\n" + CONTENT + "\nSME Name: Dr. Kavitha Raman\n{line}\n\n## Discharging\n\n"
                           "The capacitor discharges through the resistor when the battery is removed.\n",
    "both": "# RC circuits\n\nSME Name: Dr. Kavitha Raman\n{line}\n\n## Charging\n\n" + CONTENT,
}


@pytest.mark.parametrize("context", list(CONTEXTS))
@pytest.mark.parametrize("line", VARIANT_LINES)
def test_administrative_key_variants(line, context):
    res = scope_source(CONTEXTS[context].format(line=line))
    value = line.replace("\t", " ").split(":")[-1].split(" by ")[-1].strip()
    assert line.split("\t")[0].split(":")[0] not in res.markdown, (line, context)
    assert value.split(",")[0].split("&")[0].strip() not in res.markdown, (line, context)
    assert "The time constant tau = R x C" in res.markdown
    assert res.excluded


def test_multi_line_header_value():
    md = f"Prepared by:\n\nDr. Sowmiya Krishnan, Assistant Professor (Sr. G)\n\n# RC circuits\n\n{CONTENT}"
    res = scope_source(md)
    assert "Sowmiya" not in res.markdown and res.markdown.startswith("# RC circuits")
    assert any("Sowmiya" in e.text and e.category == "person" for e in res.excluded)  # the lint can track it
    duration = scope_source(f"Video Duration:\n\n12 min\n\n# RC circuits\n\n{CONTENT}")
    assert "12 min" not in duration.markdown


def test_whole_variant_document(job_ctx):
    res, secrets = ingest(job_ctx, L.variants_docx)
    assert_clean(res, secrets)
    upper, upper_secrets = ingest(job_ctx, L.upper_txt)
    assert_clean(upper, upper_secrets)
    assert "Q = dU + W" in upper.markdown


# ---------------------------------------------------------------------------
# [major] administrative data inside subject / unit / session lines and headings
# ---------------------------------------------------------------------------


def test_admin_fragments_in_meta_lines_and_headings():
    res = scope_source(text_to_markdown(L.META_LINES_TXT))
    meta = res.document_meta
    assert (meta.subject_name, meta.unit_name, meta.session_number, meta.session_title) == (
        "Signals and Systems", "Unit III - Transients", "Session 5", "RC Circuits")
    for secret in L.META_LINES_SECRETS:
        assert secret not in res.markdown, secret
    assert "# CHARGING A CAPACITOR\n" in res.markdown
    recorded = " ".join(e.text for e in res.excluded)
    for value in ("EC3352", "R2021", "Ramesh Kumar", "Video No: 07"):
        assert value in recorded, value


@pytest.mark.parametrize("title, codes, clean", [
    ("RC Circuits | Dr. Ramesh Kumar (Duration: 15 min)", False, "RC Circuits"),
    ("CHARGING A CAPACITOR (Video No: 07)", False, "CHARGING A CAPACITOR"),
    ("Capacitors - 12 min", False, "Capacitors"),
    ("Capacitors by Dr. Ramesh Kumar", False, "Capacitors"),
    ("EC3352 - SIGNALS AND SYSTEMS", True, "SIGNALS AND SYSTEMS"),
    ("CIRCUIT THEORY (EC3352)", True, "CIRCUIT THEORY"),
    ("UNIT III - TRANSIENTS (R2021)", True, "UNIT III - TRANSIENTS"),
    ("OP-AMPS (LM741)", False, "OP-AMPS (LM741)"),  # a part number in a segment title is content
    ("WORLD WAR II (1939)", True, "WORLD WAR II (1939)"),  # a year is content
    ("FREE FALL - 2 s", False, "FREE FALL - 2 s"),
])
def test_strip_title_admin(title, codes, clean):
    assert strip_title_admin(title, codes=codes)[0] == clean


def test_smart_title_keeps_codes_and_roman_numerals():
    assert smart_title("CIRCUIT THEORY (EC3352)") == "Circuit Theory (EC3352)"
    assert smart_title("UNIT III - TRANSIENTS") == "Unit III - Transients"
    assert smart_title("RC CIRCUITS: VI CHARACTERISTICS") == "RC Circuits: VI Characteristics"
    assert smart_title("MIXED SIGNALS") == "Mixed Signals"


# ---------------------------------------------------------------------------
# [major] Markdown shapes: YAML front matter, quotes, _labels_, HTML comments, footers
# ---------------------------------------------------------------------------


def test_markdown_header_shapes():
    res = scope_source(L.FRONTMATTER_MD)
    for secret in L.FRONTMATTER_SECRETS:
        assert secret not in res.markdown, secret
    assert "---" not in res.markdown and "<!--" not in res.markdown and "Page 2" not in res.markdown
    assert res.markdown.startswith("# Heat transfer\n\n## Conduction")
    assert "Fourier's law says the heat flow rate is proportional" in res.markdown
    assert "Convection carries heat" in res.markdown
    cats = [e.category for e in res.excluded]
    assert cats.count("person") >= 3 and "duration" in cats
    # an ordinary quotation and page markers stay
    quote = scope_source("# Ideas\n\n> Imagination is more important than knowledge.\n\n<!-- page 2 -->\n\nMore text here.\n")
    assert "> Imagination is more important" in quote.markdown and "<!-- page 2 -->" in quote.markdown


# ---------------------------------------------------------------------------
# [major] per-clip scaffolding variants
# ---------------------------------------------------------------------------


def _two_clips(title: str, body: str, *, where: str = "start") -> str:
    seg = f"SEGMENT 2 - {title} [0:20 - 0:40]\nAadhi speaks:\n{body}\n\n"
    first = ("CLIP 1 SCRIPT - CHARGING\n\nSEGMENT 1 - CHARGING [0:00 - 0:20]\nAadhi speaks:\n"
             "When a capacitor is connected to a battery through a resistor, it charges gradually.\n\n")
    second = ("CLIP 2 SCRIPT - DISCHARGING\n\n" + (seg if where == "start" else "") + "SEGMENT 3 - DISCHARGING [0:40 - 1:00]\n"
              "Aadhi speaks:\nWhen the battery is removed, the capacitor discharges through the resistor.\n\n")
    if where == "end":
        first += seg
    return first + second


@pytest.mark.parametrize("title", ["RECAP OF PREVIOUS CLIP", "QUICK RECAP", "PREVIOUSLY", "RECAP", "WHAT WE LEARNED IN CLIP 1",
                                   "WELCOME BACK"])
def test_recap_at_the_start_of_a_later_clip_is_dropped(title):
    res = scope_source(_two_clips(title, "Welcome back! Last time we saw how a capacitor charges through a resistor."))
    assert "Last time we saw" not in res.markdown and title not in res.markdown
    assert "discharges through the resistor" in res.markdown
    assert any(e.reason == "recap of the previous clip" for e in res.excluded)


@pytest.mark.parametrize("title", ["WHAT'S NEXT?", "TRANSITION", "LOOKING AHEAD", "TEASER", "CONNECTING TO THE NEXT CONCEPT",
                                   "TITLE SLIDE", "TITLE ANIMATION", "OPENING SEQUENCE", "LOGO ANIMATION", "CLOSING",
                                   "THANK YOU", "CLOSING REMARKS", "WRAP UP"])
def test_bridges_cards_and_outros_are_dropped(title):
    res = scope_source(_two_clips(title, "That is all for this clip. See you in the next video.", where="end"))
    assert title not in res.markdown and "See you" not in res.markdown
    assert "discharges through the resistor" in res.markdown


def test_bare_intro_segment_is_a_title_card_only_when_it_holds_a_title():
    card = scope_source(_two_clips("INTRO", "CAPACITOR DISCHARGE", where="end"))
    assert "INTRO" not in card.markdown and "CAPACITOR DISCHARGE" not in card.markdown
    taught = scope_source(_two_clips("INTRO", "A capacitor stores energy in the electric field between its plates.",
                                     where="end"))
    assert "## INTRO" in taught.markdown and "stores energy in the electric field" in taught.markdown


def test_flow_script_becomes_one_lecture():
    res = scope_source(text_to_markdown(L.FLOW_TXT))
    for gone in ("TITLE SLIDE", "WHAT'S NEXT", "THANK YOU", "WELCOME BACK", "RECAP", "CLOSING", "next video",
                 "previous video", "Thank you for watching", "last clip"):
        assert gone not in res.markdown, gone
    for kept in ("## CHARGING", "## DISCHARGING", L.KEPT[0], L.KEPT[2]):
        assert kept in res.markdown, kept


# ---------------------------------------------------------------------------
# [major] AV-table scripts
# ---------------------------------------------------------------------------


def test_av_table_script():
    data, _, secrets = L.av_table_docx()
    res = scope_source(docx_to_markdown(data).markdown)
    assert res.source_format == "sme_script"
    for secret in secrets:
        assert secret not in res.markdown, secret
    assert "In this video, we will learn" not in res.markdown  # the title-card row
    assert "acceleration is proportional to its displacement" in res.markdown
    assert "T = 2 pi root of m over k" in res.markdown
    notes = " ".join(t for _, t in res.visual_notes_raw)
    assert "Spring-mass system oscillating" in notes and "Coming up next" not in notes
    assert res.document_meta.subject_name == "Engineering Physics"


# ---------------------------------------------------------------------------
# [minor] segment-level timing residue; [major] recording note naming the author
# ---------------------------------------------------------------------------


def test_segment_timing_lines_are_dropped():
    data, _, _ = L.sno_table_docx()
    res = scope_source(docx_to_markdown(data).markdown)
    assert "Time:" not in res.markdown and "Duration: 45 sec" not in res.markdown
    assert res.markdown.count("Time") == 0


def test_note_naming_the_author_is_dropped(job_ctx):
    res, secrets = ingest(job_ctx, L.note_docx)
    assert_clean(res, ("Kavitha", "record", "Ramanujam"))
    assert "Georg Simon Ohm published his law in 1827" in res.markdown  # people of the subject stay
    assert "The duration of the pulse is 2 ms" in res.markdown
    assert any(e.category == "production_note" for e in res.excluded)


def test_classify_kv_values_by_family():
    assert classify_kv("Duration (in minutes): 12").category == "duration"
    assert classify_kv("Prepared by: heating ammonium chloride with calcium hydroxide") is None
    assert classify_kv("Approved by: the Constituent Assembly") is None
    assert classify_kv("Regulation: R2021").category == "admin"
    assert classify_kv("Regulation: 4.5%") is None
    assert classify_kv("Running time: 12 seconds for n = 10^7.") is None
    assert classify_kv("Estimated duration: 4 hours") is None


def test_header_block_label_and_notes_sign_off():
    res = scope_source(f"VIDEO DETAILS:\nSME Name: Dr. X Kumar\nDepartment: ECE\nDuration: 10 min\n\n# RC circuits\n\n{CONTENT}"
                       "\nThank you for watching.\n")
    assert res.markdown == f"# RC circuits\n\n{CONTENT}"
    assert [e.category for e in res.excluded] == ["admin", "person", "person", "duration", "structure"]
    sentence = "# Etiquette\n\nThank you for watching closely how people greet each other.\n"
    assert scope_source(sentence).markdown == sentence


def test_self_introduction_naming_the_author_is_dropped_but_namesakes_stay():
    md = ("SME Name: Dr. Kumar Newton\n\nCLIP 1 SCRIPT - LAWS\n\nSEGMENT 1 - FORCE [0:00 - 0:30]\n\nAadhi speaks:\n\n"
          "Thanks to Newton's laws, we can predict motion.\n\nHello, I am Dr. Kumar Newton and I teach physics.\n")
    res = scope_source(md)
    assert "Thanks to Newton's laws, we can predict motion." in res.markdown
    assert "Hello, I am" not in res.markdown


def test_bare_objectives_lead_in_is_dropped_but_objectives_stay():
    md = ("CLIP 1 SCRIPT - SERIES\n\nSEGMENT 1 - WHAT THIS VIDEO WILL COVER [0:00 - 0:20]\n\nAadhi speaks:\n\n"
          "In this video, we will learn:\n\n- Series resistors\n\nIn this video, we will learn how resistors combine.\n")
    res = scope_source(md)
    assert "In this video" not in res.markdown  # the lead-in goes, the objective stays
    assert "- Series resistors" in res.markdown and "We will learn how resistors combine." in res.markdown


# ---------------------------------------------------------------------------
# [major] the SME's name inside the narration; [major] narration-level packaging
# ---------------------------------------------------------------------------

NAMED_HEADER = ("| S.No | Particulars | Details |\n|---|---|---|\n| 1 | SME Name | Dr. Meena Raghavan |\n"
                "| 2 | Video Duration | 14 minutes |\n| 3 | Reviewed by | Prof. K. Srinivasan |\n\n")


def named_sme(*lines: str) -> str:
    body = "\n\n".join(lines)
    return (NAMED_HEADER + "CLIP 1 SCRIPT - KIRCHHOFF'S CURRENT LAW\n\nSEGMENT 1 - KCL [0:00 - 1:30]\n\nAadhi speaks:\n\n"
            "A node is a point where two or more circuit elements meet.\n\n" + body + "\n")


@pytest.mark.parametrize("line", [
    "Dr. Meena Raghavan will now demonstrate this on the board with three wires meeting at a node.",
    "Dr. Meena will now demonstrate this on the board.",
    "Hello, I am Meena, and I teach circuit theory.",
    "Meena will now show you the junction on the board.",
    "Prof. Srinivasan explains the sign convention on the board.",
    "Your instructor, Dr. Raghavan, will guide you through it.",
])
def test_presenter_cues_naming_the_author_are_dropped(line):
    res = scope_source(named_sme(line, "Charge cannot pile up at a node."))
    assert "Meena" not in res.markdown and "Raghavan" not in res.markdown and "Srinivasan" not in res.markdown
    assert "Charge cannot pile up at a node." in res.markdown
    assert any(e.category == "production_note" for e in res.excluded)


def test_other_mentions_of_the_author_become_the_instructor_but_namesakes_stay():
    res = scope_source(named_sme(
        "As Dr. Meena Raghavan explained in the lab, charge cannot pile up at a node.",
        "Meena Raghavan's notes add a second example.",
        "The Raghavan constant and Raman scattering are not part of this lesson.",
    ))
    assert "As the instructor explained in the lab, charge cannot pile up at a node." in res.markdown
    assert "The instructor's notes add a second example." in res.markdown
    assert "The Raghavan constant and Raman scattering" in res.markdown  # a surname alone may be someone else
    assert [e.text for e in res.excluded if e.reason == "the author's name in the source text"] == [
        "Name in the text: Dr. Meena Raghavan", "Name in the text: Meena Raghavan"]


def test_a_footer_credit_also_covers_the_narration_above_it():
    md = ("# Kirchhoff's laws\n\nA node is a point where wires meet. Dr. Meena Raghavan will now draw one.\n\n"
          "Prepared by Dr. Meena Raghavan · Page 2 of 2\n")
    res = scope_source(md)
    assert "Meena" not in res.markdown and "A node is a point where wires meet." in res.markdown


@pytest.mark.parametrize("line", [
    "This video is about 7 minutes long.",
    "This video is about 7 minutes long, so keep a notebook ready while we work through KCL.",
    "This session runs for 14 minutes.",
    "CAMERA: zoom in on the board.",
    "CUT TO: whiteboard close-up.",
    "LOWER THIRD: Kirchhoff's Current Law",
    "NOTE TO EDITOR: insert b-roll of a junction box here.",
    "EDITOR NOTE: add the formula as a caption.",
    "B-ROLL: close-up of a junction box.",
    "SFX: soft click.",
    "Production note: record this part twice.",
    "Don't forget to like and subscribe!",
    "If this helped, like, share and subscribe to our channel.",
    "Hope you enjoyed this video.",
    "That's all for this video. Don't forget to like and subscribe!",
    "Welcome to video 2 of the network analysis series.",
    "Hi students, welcome to the first video in this series on network analysis.",
    "Welcome back, everyone!",
])
def test_narration_packaging_never_reaches_the_content(line):
    res = scope_source(named_sme(line, "Charge cannot pile up at a node."))
    content = res.markdown + " ".join(t for _, t in res.visual_notes_raw)
    core = line.split(":", 1)[-1].strip()[:20] if line.isupper() or ":" in line[:20] else line[:20]
    assert core not in content, line
    assert "Charge cannot pile up at a node." in res.markdown
    assert not res.visual_notes_raw  # production directions are never visual suggestions


def test_narration_lead_ins_and_clip_pointers_are_rewritten():
    res = scope_source(named_sme(
        "In this video, we will learn Kirchhoff's Current Law and how to apply it at a node.",
        "In this 10-minute video, we will look at KCL.",
        "As we saw in Clip 1, charge is conserved.",
        "In the previous video, we met the node.",
    ))
    md = res.markdown
    assert "We will learn Kirchhoff's Current Law and how to apply it at a node." in md
    assert "We will look at KCL." in md and "10-minute" not in md
    assert "As we saw earlier, charge is conserved." in md and "Earlier, we met the node." in md
    assert "In this video" not in md and "Clip 1" not in md
    assert any(e.category == "duration" and e.reason == "video length in the narration" for e in res.excluded)


def test_label_lookalikes_and_namesakes_in_notes_stay():
    kept = ["Camera: a device that records images on a sensor.", "Lower third: the bottom part of the frame.",
            "In this video codec, frames are predicted from earlier frames.",
            "This video is about how capacitors charge.",
            "In part 2 of the proof, the load doubles.", "In the first part of the experiment, we heat the wire.",
            "Each class lasts 50 minutes in this timetable.", "The segment lasts 5 seconds before the signal repeats.",
            "Part 2 of the series circuit carries the same current."]
    res = scope_source(named_sme(*kept))
    for line in kept:
        assert line in res.markdown, line
    ram = scope_source("SME Name: Dr. Ram Kumar\n\nCLIP 1 SCRIPT - MEMORY\n\nSEGMENT 1 - RAM [0:00 - 0:30]\n\n"
                       "Aadhi speaks:\n\nRAM can show its contents to the processor in a few nanoseconds.\n")
    assert "RAM can show its contents" in ram.markdown


# ---------------------------------------------------------------------------
# [minor] narration repeated word for word as board lines
# ---------------------------------------------------------------------------


def test_board_lines_that_repeat_the_narration_are_dropped_once():
    md = named_sme(
        "Unlike ordinary algebra, Boolean Algebra deals with only two values:", "0 means FALSE", "1 means TRUE",
        "Y = A + AB", "Therefore:", "Y = A",
        "BOARD displays:", "**0 means FALSE**", "- 1 means TRUE", "**Using the Absorption Law:**", "**Therefore:**",
        "**Y = A**", "Question 1:", "What is A + 0?", "Answer:", "A.", "Question 2:", "What is A · 1?", "Answer:", "A.",
        "- Q1: What is A + 0? Answer: A.",
    )
    res = scope_source(md)
    body = res.markdown.split("## KCL", 1)[1]
    assert body.count("0 means FALSE") == 1 and body.count("1 means TRUE") == 1
    assert body.count("Y = A\n") == 1 and body.count("Therefore:") == 1
    assert "**Using the Absorption Law:**" in body  # a board line that says something new stays
    assert body.count("Answer:") == 2 and body.count("\nA.\n") == 2  # two questions with the same answer
    assert "Q1:" not in body
    dup = [e for e in res.excluded if e.category == "duplicate"]
    assert len(dup) == 1 and dup[0].text.startswith("5 board line(s)")


def test_notes_keep_repeated_lines():
    md = "# Derivation\n\nY = A + AB\n\nUsing absorption:\n\nY = A\n\nCheck again:\n\nY = A\n"
    assert scope_source(md).markdown.count("Y = A\n") == 2


def test_plan_and_scene_prompts_say_a_point_may_appear_twice():
    from aadhi.pipeline.base import IngestResult
    from aadhi.pipeline.plan import about_source, scene_source_note

    ingest = IngestResult(markdown="x", source_format="sme_script")
    assert "may appear twice" in about_source(ingest, False) and "may appear twice" in scene_source_note(ingest, False)
    notes = IngestResult(markdown="x", source_format="notes")
    assert "twice" not in about_source(notes, False) and "twice" not in scene_source_note(notes, False)
