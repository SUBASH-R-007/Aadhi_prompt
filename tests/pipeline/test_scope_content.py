"""source_scope regressions: teaching content that looks like administration or production is never removed.

Each case comes from a verifier finding: subject matter that the first version of source scoping stripped
(and that the brief scrubber and the leak lint then penalised).
"""

from __future__ import annotations

import io

import pytest

from aadhi.pipeline.base import ExcludedItem, IngestResult, SourceChunk
from aadhi.pipeline.brief import brief_from_gen, person_values
from aadhi.pipeline.docx_extract import docx_to_markdown
from aadhi.pipeline.gen_models import GenBrief
from aadhi.pipeline.ingest import text_to_markdown
from aadhi.pipeline.source_scope import scope_source
from aadhi.pipeline.validate import lint
from aadhi.schemas.screenplay import Screenplay


def kept(md: str, *texts: str, fmt: str | None = None, excluded: bool = False):
    res = scope_source(md)
    for text in texts:
        assert text in res.markdown, text
    if fmt:
        assert res.source_format == fmt
    if not excluded:
        assert not [e for e in res.excluded if e.category in ("person", "duration", "admin")], res.excluded
    return res


def sme(body: str) -> str:
    """``body`` inside one segment of an SME script."""
    return ("CLIP 1 SCRIPT - FOUNDATIONS\n\nSEGMENT 1 - BASICS [0:00 - 0:40]\n\nAadhi speaks:\n\nWe start with a simple idea.\n\n"
            f"SEGMENT 2 - THE IDEA [0:40 - 1:30]\n\nAadhi speaks:\n\n{body}\n")


def leaks(narration: str, ingest: IngestResult) -> list:
    sp = Screenplay.model_validate({"scenes": [{"id": "s1", "type": "content", "title": "Ideas",
                                                "beats": [{"id": "s1-b1", "narration": narration}]}]})
    return [i for i in lint(sp, ingest=ingest) if i.code == "content.admin_leak"]


def as_ingest(res) -> IngestResult:
    return IngestResult(markdown=res.markdown, chunks=[SourceChunk(id="c0001", text=res.markdown)], excluded=res.excluded)


# ---------------------------------------------------------------------------
# [blocker] "X by" keys are content unless they name a person in a header
# ---------------------------------------------------------------------------

BY_CASES = {
    "chemistry": ("# Ammonia\nFormula: NH3\nPrepared by: heating ammonium chloride with calcium hydroxide\n"
                  "Uses: fertilisers and refrigeration.\n",
                  "Prepared by: heating ammonium chloride", "Ammonia is prepared by heating ammonium chloride with lime."),
    "civics": ("# The Constitution of India\n\nApproved by: the Constituent Assembly\n\n"
               "The Constitution was approved by the Constituent Assembly on 26 November 1949.\n",
               "Approved by: the Constituent Assembly",
               "The Constitution was approved by the Constituent Assembly on 26 November 1949."),
    "accounting": ("# Internal control\n\nA purchase order passes through three people:\n\n- Prepared by: the purchase clerk\n"
                   "- Checked by: the stores manager\n- Approved by: the finance head\n\nSeparating duties prevents fraud.\n",
                   "Approved by: the finance head", "The purchase order is prepared by the purchase clerk."),
}


@pytest.mark.parametrize("name", list(BY_CASES))
def test_by_keys_with_content_values_are_kept(name):
    md, line, narration = BY_CASES[name]
    res = kept(md, line)
    assert person_values(res.excluded) == []
    assert leaks(narration, as_ingest(res)) == [], name


def test_voucher_table_after_a_heading_is_kept():
    import docx

    d = docx.Document()
    d.add_heading("Voucher system", level=1)
    table = d.add_table(rows=2, cols=3)
    for j, (k, v) in enumerate([("Prepared by", "Accounts clerk"), ("Checked by", "Chief accountant"),
                                ("Approved by", "Finance manager")]):
        table.cell(0, j).text, table.cell(1, j).text = k, v
    d.add_paragraph("Every voucher carries three signatures so that no single person controls a payment.")
    buf = io.BytesIO()
    d.save(buf)
    kept(docx_to_markdown(buf.getvalue()).markdown, "| Prepared by | Checked by | Approved by |",
         "| Accounts clerk | Chief accountant | Finance manager |")


def test_brief_scrubber_only_replaces_real_names():
    excluded = [ExcludedItem(category="person", text="Approved by: the Constituent Assembly"),
                ExcludedItem(category="person", text="Prepared by: heating ammonium chloride with calcium hydroxide"),
                ExcludedItem(category="person", text="SME Name: Dr. Ramesh Kumar, Assistant Professor")]
    assert person_values(excluded) == ["Ramesh Kumar"]
    ingest = IngestResult(markdown="x", chunks=[SourceChunk(id="c0001", text="x")], excluded=excluded)
    gen = GenBrief.model_validate({"topic": "The Constitution", "concepts": [{
        "key": "role_of_the_constituent_assembly", "name": "Role of the Constituent Assembly",
        "key_facts": [{"text": "The Constitution was approved by the Constituent Assembly on 26 November 1949.",
                       "source_refs": ["c0001"]}], "source_refs": ["c0001"]}], "teaching_order": []})
    brief = brief_from_gen(gen, ingest)
    assert brief.concepts[0].name == "Role of the Constituent Assembly"
    assert "approved by the Constituent Assembly" in brief.concepts[0].key_facts[0].text


# ---------------------------------------------------------------------------
# [major] visual / image labels in notes
# ---------------------------------------------------------------------------


def test_notes_visual_and_image_labels_are_content():
    md = ("# Multimedia elements\n\nAnimation: the rapid display of a sequence of images to create an illusion of movement.\n\n"
          "Animation principles: squash and stretch, anticipation and timing.\n\nOn-screen keyboard: a virtual keyboard "
          "shown on a touch screen.\n\n## Animation: types and techniques\n\nAnimation can be 2D or 3D.\n\n**Animation:**\n\n"
          "Animation is the technique of photographing successive drawings to create movement.\n\n"
          "Cel animation draws each frame by hand.\n")
    res = kept(md, "Animation: the rapid display", "Animation principles: squash", "On-screen keyboard: a virtual",
               "## Animation: types and techniques", "Animation is the technique of photographing",
               "Cel animation draws each frame by hand.", fmt="notes")
    assert res.visual_notes_raw == []
    kept("# Filtering\n\nSource image: the original image I(x, y) before filtering.\n\nA mean filter blurs it.\n",
         "Source image: the original image I(x, y)")
    kept("# Word processing\n\nInsert image: click Insert > Pictures and choose the file.\n\nThe picture appears at the cursor.\n",
         "Insert image: click Insert > Pictures")


# ---------------------------------------------------------------------------
# [major] data tables are not metadata tables
# ---------------------------------------------------------------------------


def test_data_tables_with_key_headers_are_kept():
    md = ("# University database\n\n| Faculty ID | Faculty name | Department |\n|---|---|---|\n| F01 | Dr. Rao | CSE |\n"
          "| F02 | Dr. Mehta | ECE |\n\n| Course code | Course name | Credits |\n|---|---|---|\n| CS101 | Data Structures | 4 |\n"
          "| CS102 | Database Systems | 3 |\n\nEach course is taught by one faculty member.\n")
    res = kept(md, "| F01 | Dr. Rao | CSE |", "| CS102 | Database Systems | 3 |")
    assert res.document_meta.subject_name == ""
    for header, rows in (("| Version | Year | Status |", "| 4.01 | 1999 | Superseded |\n| 5 | 2014 | Living standard |"),
                         ("| Version | Date | Status |", "| 1.0 | 01/02/2020 | Released |\n| 2.0 | 15/06/2021 | Beta |")):
        md = f"HTML versions\n\n{header}\n|---|---|---|\n{rows}\n\nHTML5 added native audio and video elements.\n"
        kept(md, rows.splitlines()[0], rows.splitlines()[1])


# ---------------------------------------------------------------------------
# [major] glossaries and example records are not header blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("md, texts", [
    ("# Organisational behaviour\n\nOrganisation: a group of people working towards shared goals\nDepartment: a unit of an "
     "organisation with one function\nDesignation: the official title of a job\nHOD: the head of a department\n",
     ["Department: a unit of an organisation", "Designation: the official title of a job"]),
    ("# Organisation chart\n\nRoles are written in a fixed format, for example:\n\nDepartment: Marketing\n"
     "Designation: Marketing Manager\nOrganisation: ABC Ltd\n\nEach box in the chart is one such role.\n",
     ["Department: Marketing", "Organisation: ABC Ltd"]),
    ("# Relations\n\nA tuple stores one record:\n\nEmployee ID: 101\nName: Ravi Kumar\nDesignation: Clerk\nDepartment: Accounts\n"
     "Email: ravi@example.com\nPhone: 98400 00000\n\nEach attribute holds one value.\n",
     ["Designation: Clerk", "Email: ravi@example.com", "Phone: 98400 00000"]),
    ("# Networks\n\nEmail: electronic mail sent over the Internet using SMTP\nPhone: a device that converts sound into electrical "
     "signals\nContact: the point where two conductors touch\n",
     ["Email: electronic mail sent over", "Contact: the point where two conductors touch"]),
    ("# Information theory\n\nThe founder of the field:\n\nName: Claude Shannon\nInstitution: MIT\nDepartment: Electrical "
     "Engineering\nYear: 1937\n", ["Institution: MIT", "Year: 1937"]),
    ("# Git\n\nBranch: a movable pointer to a commit\nVersion: 2.0 is a tagged release of the code\nStatus: the state of the "
     "working tree\n", ["Version: 2.0 is a tagged release"]),
    ("# CBCS\n\nA typical theory course:\n\nCredits: 4\nSemester: III\nYear: 2\n\nCredits add up to the degree total.\n",
     ["Credits: 4", "Semester: III"]),
])
def test_glossaries_and_records_are_kept(md, texts):
    kept(md, *texts)


def test_a_record_value_is_not_tracked_by_the_leak_lint():
    md = BY_CASES["chemistry"][0] + "\nThe email attribute of this tuple is ravi.kumar@example.com.\n"
    res = kept(md)
    assert leaks("The email attribute of this tuple is ravi.kumar@example.com.", as_ingest(res)) == []


# ---------------------------------------------------------------------------
# [major] subject durations; [major] front-matter dates and given data
# ---------------------------------------------------------------------------


def test_subject_durations_are_kept():
    pert = ("# Project scheduling\n\nActivity A\nEstimated duration: 4 hours\nPredecessor: none\n\nActivity B\n"
            "Estimated duration: 6 hours\nPredecessor: A\n\nExpected duration: 10 hours\n\nEstimated time: 3 hours for the "
            "review.\n\nThe critical path is A-B with a total duration of 10 hours.\n")
    res = kept(pert, "Estimated duration: 4 hours", "Estimated duration: 6 hours", "Expected duration: 10 hours",
               "Estimated time: 3 hours for the review.")
    assert leaks("The estimated duration of activity A is 4 hours.", as_ingest(res)) == []
    kept("# Sorting\n\nRun time: 2 s for 1,000,000 elements on a laptop.\n\nRunning time: 12 seconds for n = 10^7.\n",
         "Run time: 2 s for 1,000,000 elements", "Running time: 12 seconds for n = 10^7.")


@pytest.mark.parametrize("txt, texts", [
    ("The Revolt of 1857\n\nDate: 10 May 1857\nPlace: Meerut\nLeader: Mangal Pandey\n\n"
     "The sepoys at Meerut rebelled against the East India Company.\n", ["Date: 10 May 1857"]),
    ("George Boole\n\nBorn: 2 November 1815\nDate: 1854\nAuthor: George Boole\n\n"
     "Boole published The Laws of Thought, the basis of Boolean algebra.\n", ["Date: 1854", "Author: George Boole"]),
    ("Time: 5 s\nDuration: 10 s\nDistance: 20 m\n\nA body covers 20 m in 5 s, so its average speed is 4 m/s.\n",
     ["Time: 5 s", "Duration: 10 s"]),
])
def test_front_matter_needs_a_real_header(txt, texts):
    kept(text_to_markdown(txt), *texts)


def test_chapter_and_module_name_keys():
    res = kept("Chapter name: The Last Lesson\nAuthor: Alphonse Daudet\n\n# Summary\n\nAlphonse Daudet wrote the story "
               "about the last French lesson in Alsace.\n", "Author: Alphonse Daudet", excluded=True)
    assert not [e for e in res.excluded if e.category == "person"]
    assert person_values(res.excluded) == []
    res = kept("# Python modules\n\nModule name: math\n\nThe math module provides sqrt and floor.\n", "Module name: math")
    assert res.document_meta.unit_name == ""
    assert leaks("The Last Lesson was written by Alphonse Daudet.", as_ingest(
        scope_source("Chapter name: The Last Lesson\nAuthor: Alphonse Daudet\n\n# Summary\n\nA story.\n"))) == []


# ---------------------------------------------------------------------------
# [major] SME label and production regexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "Ohm's law says: the current through a conductor is proportional to the voltage across it.",
    "Kirchhoff explains: the currents entering a node add up to zero.",
    "Boole says: every logical statement is either true or false.",
    "Board of directors: the body elected by shareholders to run a company.",
    "Board meeting: a formal meeting of the directors.",
    "Note: a pinhole camera forms an inverted image.",
    "Note: a text editor such as Notepad saves plain text files.",
    "Note: music is a periodic sound wave with a definite pitch.",
    "(Points A and B lie on the same line.)",
    "[The camera lens focuses light on the sensor.]",
    "(The music of the spheres was Pythagoras' idea.)",
    "Graphics: the GPU turns triangles into pixels.",
    "Visual cortex: the part of the brain that processes sight.",
    "Shot noise: random fluctuation of current in a diode.",
    "Footage: recorded film or video material.",
    "Unit 1: the input unit accepts data.",
    "Module 3: arithmetic logic unit.",
    "Sequence 1: 2, 4, 8, 16 is geometric.",
    "Part 2: the denominator is never zero.",
])
def test_sme_lines_that_look_like_labels_are_content(line):
    res = scope_source(sme(line))
    assert res.source_format == "sme_script"
    assert line in res.markdown, line
    assert not res.visual_notes_raw


# ---------------------------------------------------------------------------
# [major] SME segment titles; [major] times inside narration
# ---------------------------------------------------------------------------


def test_sme_segment_titles_that_are_content():
    md = ("CLIP 1 SCRIPT - DIGITAL SYSTEMS\n\nSEGMENT 1 - LEARNING OBJECTIVES [0:00 - 0:20]\n\nAadhi speaks:\n\n"
          "By the end you will design a Moore machine.\n\n"
          "SEGMENT 2 - TITLE SCREENS [0:20 - 0:40]\n\nAadhi speaks:\n\nTitle screen design is the first thing a player sees.\n\n"
          "Contrast makes text readable.\n\n"
          "CLIP 2 SCRIPT - STATE MACHINES AND ENGINES\n\nSEGMENT 1 - OUTCOMES [0:00 - 0:30]\n\nAadhi speaks:\n\n"
          "Tossing a coin has two outcomes: heads and tails. Each outcome has probability one half.\n\n"
          "SEGMENT 2 - TRANSITION TO THE NEXT STATE [0:30 - 1:00]\n\nAadhi speaks:\n\n"
          "In a Moore machine the next state depends on the present state and the input.\n\n"
          "SEGMENT 3 - NEXT PART OF THE CYCLE [1:00 - 1:30]\n\nAadhi speaks:\n\n"
          "In the next part of the cycle the piston moves down and draws in the fuel mixture.\n")
    kept(md, "## TRANSITION TO THE NEXT STATE", "the next state depends on the present state", "## NEXT PART OF THE CYCLE",
         "In the next part of the cycle the piston moves down", "## OUTCOMES", "Tossing a coin has two outcomes",
         "Title screen design is the first thing a player sees.", "Contrast makes text readable.", fmt="sme_script")


@pytest.mark.parametrize("line", [
    "The train leaves at 10:15 and arrives at 11:45, so the journey runs 10:15 - 11:45.",
    "A shop is open from 9:00 to 17:30 on weekdays.",
    "The capacitor is fully charged after five time constants (5 seconds).",
    "The half-life of this isotope is very short (30 s).",
])
def test_times_in_sme_narration_are_kept(line):
    res = scope_source(sme(line))
    assert line in res.markdown


def test_bold_narration_keeps_its_duration():
    md = sme("**The capacitor is fully charged after five time constants (5 seconds).**")
    assert "(5 seconds)" in scope_source(md).markdown


# ---------------------------------------------------------------------------
# [major] content tables are not AV script tables; [major] drama and film notes are not SME scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", [
    "| Format | Visual quality | Audio quality |\n|---|---|---|\n| MP4 | High | AAC, good |\n| AVI | Medium | PCM, very good |\n"
    "| FLV | Low | MP3, fair |",
    "| Component | Graphics card | Audio card |\n|---|---|---|\n| Function | Renders images | Converts sound |\n"
    "| Interface | PCIe | PCIe or USB |",
])
def test_content_tables_are_not_script_tables(table):
    res = kept(f"# Multimedia\n\n{table}\n\nEach format trades size for quality.\n", *table.splitlines()[2:], fmt="notes")
    assert res.visual_notes_raw == []


def test_drama_and_film_notes_are_notes():
    drama = ("# The Proposal by Anton Chekhov\n\n## Scene 1 - Lomov arrives\n\nLomov says: I have come to ask for your "
             "daughter's hand.\n\nChubukov says: My dear fellow, I am delighted!\n\n## Scene 2 - The quarrel\n\nNatalya says: "
             "The Oxen Meadows are ours!\n\nLomov says: They are mine!\n")
    kept(drama, "Lomov says: I have come to ask", "Chubukov says: My dear fellow", fmt="notes")
    film = ("# Film language\n\n## Shot 1 - Establishing shot\n\nNarration: a wide view of the city at dawn.\n\n"
            "## Shot 2 - Close-up\n\nNarration: the hero's face fills the frame.\n\n## Shot 3 - Cutaway\n\nNarration: a clock "
            "ticks on the wall.\n")
    kept(film, "Narration: a wide view of the city", fmt="notes")


def test_label_removals_are_reported():
    res = scope_source(sme("A capacitor stores charge."))
    assert any("Aadhi speaks:" in e.text and e.category == "structure" for e in res.excluded)


# ---------------------------------------------------------------------------
# [minor] notes heading times; [minor] copyright sentences
# ---------------------------------------------------------------------------


def test_notes_heading_times_are_kept():
    kept("# Saponification\n\n## Heat the oil (10 minutes)\n\nWarm it gently.\n\n## Add the alkali (5 minutes)\n\nStir.\n\n"
         "## Cure the soap (2 min)\n\nLeave it.\n", "## Heat the oil (10 minutes)", "## Cure the soap (2 min)")
    res = scope_source("# Time problems\n\n## Train A (10:15 - 11:45)\n\nFind the journey time.\n\n## Train B (09:00 - 12:30)\n\n"
                       "Find the journey time.\n")
    assert "(10:15 - 11:45)" in res.markdown and "(09:00 - 12:30)" in res.markdown and not res.excluded
    video = scope_source("# Diodes\n\n## Forward bias (0:00 - 1:10)\n\nIt conducts.\n\n## Reverse bias (1:10 - 2:00)\n\nIt blocks.\n")
    assert "## Forward bias\n" in video.markdown and [e.category for e in video.excluded] == ["timecode"]


def test_copyright_sentences_are_content():
    kept("# Intellectual property\n\nThe phrase \"All rights reserved\" was once required by the Buenos Aires Convention "
         "of 1910.\n\n© is the copyright symbol; it is followed by the year and the owner's name.\n\n"
         "Copyright 1957 Act of India protects literary works.\n", "The phrase \"All rights reserved\" was once required",
         "© is the copyright symbol", "Copyright 1957 Act of India protects literary works.")
    res = scope_source("# Diodes\n\nA diode conducts in one direction.\n\n© 2025 Example College. All rights reserved.\n\n"
                       "All rights reserved.\n")
    assert "©" not in res.markdown and "rights" not in res.markdown


# ---------------------------------------------------------------------------
# [major] over-stripping of tier-1 keys in the body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "Regulation: 4.5% for this transformer at full load.",
    "File name: marks.csv",
    "Session ID: a random token the server stores in a cookie.",
    "Course ID: CS101",
    "Document ID: every document has a unique _id field.",
])
def test_body_lines_with_admin_like_keys_are_kept(line):
    res = kept(f"# Worked example\n\nThe values below come from the lab.\n\n{line}\n\nUse them in the next step.\n", line)
    assert leaks(f"{line.split(':', 1)[1].strip()} is the value to use.", as_ingest(res)) == []


def test_quality_approval_block_is_kept():
    kept("# Quality management\n\nEvery procedure is controlled:\n\n- Prepared by: the process owner\n"
         "- Reviewed by: the quality manager\n- Approved by: the plant head\n", "Reviewed by: the quality manager")


@pytest.mark.parametrize("md", [
    "# Student record\n\nName: Ravi Kumar\nRoll No: 21\nEmail: ravi@x.com\nPhone: 98400 00000\n\nA record stores one student.\n",
    "# Relations\n\nA relation stores tuples.\n\nName: Ravi Kumar\nEmail: ravi@example.com\nPhone: 98400 00000\n",
])
def test_records_next_to_the_title_or_at_the_end_are_kept(md):
    kept(md, "Email: ravi@", "Phone: 98400 00000", "Name: Ravi Kumar")


def test_sme_board_credit_for_a_scientist_is_kept():
    kept(sme("BOARD displays:\n\nDeveloped by: George Boole\n\nYear: 1854"), "Developed by: George Boole", "Year: 1854")


def test_label_lines_whose_value_is_content_stay_with_it():
    kept("# Kinematics\n\nA ball is dropped and we time it.\n\nDuration:\n\n5 s\n\nDistance: 122 m\n\n"
         "So the average speed is 24.4 m/s here.\n", "Duration:\n\n5 s")
    kept("# Ammonia\n\nPrepared by:\n\n- heating ammonium chloride with calcium hydroxide\n\nIt is a pungent gas.\n",
         "Prepared by:\n\n- heating ammonium chloride")
