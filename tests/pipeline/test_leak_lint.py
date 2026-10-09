"""validate.lint: source packaging / header leaks (content.admin_leak) and repeated intros."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.pipeline.base import ExcludedItem, GenerationOptions, IngestResult, SourceChunk, SourceMeta
from aadhi.pipeline.validate import header_values, lint, packaging_phrases, scenes_needing_repair
from aadhi.schemas.screenplay import Screenplay

SME = "Dr. Ramesh Kumar"


def beat(i: str, text: str, **kw: Any) -> dict[str, Any]:
    return {"id": i, "narration": text, **kw}


def scene(sid: str, narration: str = "Boolean variables take only two values.", typ: str = "content",
          title: str = "", **kw: Any) -> dict[str, Any]:
    return {"id": sid, "type": typ, "title": title or f"Scene {sid}", "beats": [beat(f"{sid}-b1", narration)], **kw}


def screenplay(scenes: list[dict[str, Any]], **kw: Any) -> Screenplay:
    return Screenplay.model_validate({"scenes": scenes, **kw})


def ingest(chunks: tuple[str, ...] = ("George Boole developed Boolean algebra in 1854.",),
           excluded: list[ExcludedItem] | None = None, **kw: Any) -> IngestResult:
    return IngestResult(
        markdown="\n\n".join(chunks),
        chunks=[SourceChunk(id=f"c{n:04d}", text=t) for n, t in enumerate(chunks, 1)],
        excluded=excluded if excluded is not None else [ExcludedItem(category="person", text=f"SME Name: {SME}")],
        **kw,
    )


def leaks(sp: Screenplay, ing: IngestResult | None = None) -> list:
    return [i for i in lint(sp, None, ingest=ing) if i.code == "content.admin_leak"]


@pytest.mark.parametrize("text,phrase", [
    ("In this video, we will learn what Boolean algebra is.", "in this video"),
    ("This clip shows the three basic operations.", "this clip"),
    ("As we saw in the segment on postulates, closure holds.", "in the segment"),
    ("Recall the end of the video, where we met AND.", "of the video"),
    ("We met this law back in clip 3.", "clip 3"),
    ("Segment 2 introduces the identity element.", "segment 2"),
    ("This is part 2 of the video on Boolean laws.", "part 2 of the video"),
    ("From 0:10 - 1:15 we look at the hook.", "0:10 - 1:15"),
    ("The estimated duration of this part is short.", "estimated duration"),
    ("Our subject matter expert explains the idea.", "subject matter expert"),
    ("The SME explains De Morgan's theorem.", "sme"),
    ("These notes were prepared by the department.", "prepared by"),
    ("The slides were reviewed by a senior professor.", "reviewed by"),
])
def test_each_packaging_pattern_is_an_error(text, phrase):
    issues = leaks(screenplay([scene("s1", text)]))
    assert len(issues) == 1, issues
    issue = issues[0]
    assert issue.severity == "error" and issue.fixable and issue.scene_id == "s1" and issue.beat_id == "s1-b1"
    assert f"'{phrase}'" in issue.message and "beat s1-b1" in issue.message


def test_clean_lecture_has_no_leak_or_intro_issue():
    sp = screenplay([
        scene("hook", "Every phone makes millions of logical decisions a second.", "title", "Boolean logic"),
        scene("s1", "George Boole showed that logic can be written as algebra. Pause the video and try one.",
              title="What is Boolean algebra?"),
        scene("s2", "Today we combine variables with AND, OR and NOT.", title="Three basic operations"),
    ])
    issues = lint(sp, GenerationOptions(target_minutes=3), ingest=ingest())
    assert not [i for i in issues if i.code.startswith("content.")], issues


def test_excluded_name_leak_is_flagged_without_quoting_it():
    sp = screenplay([
        scene("s1", f"Hello, I am {SME} and today we study Boolean algebra."),
        scene("s2", "Ramesh Kumar's favourite law is the distributive law."),  # honorific dropped: still a leak
        scene("s3", "Nothing personal here, only Boolean algebra."),
    ])
    issues = leaks(sp, ingest())
    assert [(i.scene_id, i.beat_id) for i in issues] == [("s1", "s1-b1"), ("s2", "s2-b1")]
    for i in issues:
        assert "author's name" in i.message and i.severity == "error" and i.fixable
        assert "Ramesh" not in i.message and "Kumar" not in i.message
    assert not leaks(sp)  # without the IngestResult only the packaging patterns are checked


def test_names_that_are_content_are_never_flagged():
    sp = screenplay([scene("s1", "Boolean algebra was developed by the English mathematician George Boole.")])
    assert leaks(sp, ingest()) == []
    # even if scoping wrongly excluded the name, it is content because the scoped source teaches it
    wrong = ingest(excluded=[ExcludedItem(category="person", text="George Boole"),
                             ExcludedItem(category="person", text=f"SME Name: {SME}")])
    assert leaks(sp, wrong) == []


def test_title_card_metadata_is_not_a_leak():
    sp = screenplay([scene("s1", "Welcome to Digital System Design 2024.", "title", "Digital System Design")],
                    subject_name="Digital System Design")
    ing = ingest(excluded=[ExcludedItem(category="admin", text="Subject Name: Digital System Design 2024")],
                 document_meta=SourceMeta(subject_name="Digital System Design 2024"))
    assert leaks(sp, ing) == []


def test_admin_values_only_codes_dates_and_contacts():
    excluded = [
        ExcludedItem(category="admin", text="Course Code: CS3351"),
        ExcludedItem(category="admin", text="Department: Electronics and Communication Engineering"),
        ExcludedItem(category="admin", text="Regulation: 2021"),
        ExcludedItem(category="person", text="Email: ramesh.kumar@example.edu"),
        ExcludedItem(category="person", text="Faculty"),
    ]
    sp = screenplay([
        scene("code", "This topic is part of CS3351."),
        scene("dept", "Electronics and communication engineering uses this daily."),
        scene("year", "The 2021 processors still use these gates."),
        scene("mail", "Write to ramesh.kumar@example.edu with doubts."),
        scene("word", "Our faculty of reasoning is logic."),
    ])
    issues = leaks(sp, ingest(excluded=excluded))
    assert {i.scene_id for i in issues} == {"code", "mail"}
    code = next(i for i in issues if i.scene_id == "code")
    assert "administrative detail" in code.message and "CS3351" not in code.message


def test_institutions_roles_and_house_names_are_not_tracked():
    excluded = [
        ExcludedItem(category="person", text="Department: Electronics and Communication Engineering"),
        ExcludedItem(category="person", text="Designation: Assistant Professor"),
        ExcludedItem(category="person", text="Narrator: Aadhi"),
        ExcludedItem(category="person", text="College Name: Rajalakshmi Engineering College"),
        ExcludedItem(category="person", text=f"SME Name: {SME}"),
    ]
    sp = screenplay([
        scene("s1", "Electronics and communication engineering students use this every day."),
        scene("s2", "Hi, I am Aadhi from Rajalakshmi Engineering College."),
        scene("s3", f"Thanks to {SME} for the notes."),
    ])
    assert [i.scene_id for i in leaks(sp, ingest(excluded=excluded))] == ["s3"]


def test_header_values():
    assert header_values(f"SME Name: {SME}") == [SME, "Ramesh Kumar"]
    assert {"Dr. A. Kumar", "A. Kumar", "Dr. B. Rao", "B. Rao"} <= set(
        header_values("Prepared by: Dr. A. Kumar, Reviewed by: Dr. B. Rao"))
    assert header_values("Faculty") == []
    assert header_values("Course Code: CS3351") == ["CS3351"]


def test_subject_matter_nouns_are_not_packaging():
    geometry = screenplay(
        [scene("s1", "The midpoint of the segment splits it into two equal parts. Segment 2 is longer.")],
        concept_map=[{"id": "line_segments", "title": "Line segments"}],
    )
    assert leaks(geometry) == []
    video = ingest(chunks=("A video frame is one still image. Each video has frames. Video coding removes redundancy.",),
                   excluded=[])
    sp = screenplay([scene("s1", "In this video stream, each frame differs only a little.")])
    assert leaks(sp, video) == []
    assert leaks(sp)  # without the source, "this video" is packaging
    # the same nouns in packaging phrases do not make them subject matter
    packaging_only = ingest(chunks=("In this video we learn logic. In this video we use AND. This video ends.",),
                            excluded=[])
    assert leaks(screenplay([scene("s1", "In this video we learn OR.")]), packaging_only)


def test_phrases_the_source_teaches_are_content():
    management = ingest(chunks=("SMEs (small and medium enterprises) employ most of India's workforce.",
                                "Soap is prepared by heating oil with an alkali."), excluded=[])
    sp = screenplay([scene("s1", "An SME often cannot afford a large factory."),
                     scene("s2", "Soap is prepared by saponification.")])
    assert leaks(sp, management) == []
    assert {i.scene_id for i in leaks(sp)} == {"s1", "s2"}  # without the source they read as packaging


def test_time_ranges_from_the_source_are_content():
    shop = ingest(chunks=("The street lights stay on from 6:00 - 18:00 in winter.",), excluded=[])
    sp = screenplay([scene("s1", "The controller keeps the lights on from 6:00 - 18:00.")])
    assert leaks(sp, shop) == []
    assert packaging_phrases("Hook [0:10 - 1:15]") == ["0:10 - 1:15"]


def test_leaks_in_board_quiz_and_titles():
    sp = screenplay([
        {"id": "b", "type": "content", "title": "Segment 3 - What is Boolean algebra?",
         "board": [{"id": "b-i1", "kind": "bullet", "text": "Prepared by the ECE team"}],
         "beats": [beat("b-b1", "Boolean algebra uses two values.", board_item_id="b-i1")]},
        {"id": "q", "type": "quiz_checkpoint", "title": "Quick check", "question": "Who presents this video?",
         "options": ["Aadhi", "Nobody", "A robot"], "correct_index": 0,
         "beats": [beat("q-b1", "Which one is right?")], "reveal_beats": [beat("q-b2", "Aadhi presents it.")]},
    ], session_title="Clip 1 Script - Boolean logic", chapters=[
        {"id": "ch1", "title": "Segment 1 - Basics", "scene_ids": ["b", "q"]},
    ])
    issues = leaks(sp)
    by_scene = {i.scene_id: i for i in issues}
    assert "the scene title" in by_scene["b"].message and "board item b-i1" in by_scene["b"].message
    assert by_scene["b"].beat_id is None
    assert "the quiz text" in by_scene["q"].message
    lecture = [i for i in issues if i.scene_id is None]
    assert len(lecture) == 2 and all(not i.fixable for i in lecture)
    assert any("session title" in i.message for i in lecture) and any("chapter title ch1" in i.message for i in lecture)


def test_admin_leak_scenes_are_selected_for_repair():
    sp = screenplay([scene("s1", "In this video we learn logic."), scene("s2")])
    targets = scenes_needing_repair(lint(sp))
    assert set(targets) == {"s1"} and targets["s1"][0].code == "content.admin_leak"


def test_lint_stays_backward_compatible():
    sp = screenplay([scene("s1", "In this clip we learn logic.")])
    assert [i.code for i in lint(sp, GenerationOptions(target_minutes=3)) if i.code.startswith("content.")] == \
        ["content.admin_leak"]
    ing = ingest()
    with_refs = screenplay([scene("s1", "Boolean values.", beats=[beat("s1-b1", "Two values.", source_refs=["c0009"])])])
    assert any(i.code == "source.unknown_ref" for i in lint(with_refs, ingest=ing))  # chunk ids taken from ingest


# ---------------------------------------------------------------------------
# content.duplicate_intro
# ---------------------------------------------------------------------------


def intro_issues(sp: Screenplay) -> list:
    return [i for i in lint(sp) if i.code == "content.duplicate_intro"]


def test_second_title_scene_is_a_duplicate_intro():
    sp = screenplay([scene("hook", typ="title", title="Boolean logic"), scene("s1"),
                     scene("clip2", typ="title", title="Boolean variables and operations")])
    issues = intro_issues(sp)
    assert [(i.scene_id, i.severity, i.fixable) for i in issues] == [("clip2", "warning", True)]
    assert "second opening title scene" in issues[0].message


def test_repeated_objectives_scene_is_a_duplicate_intro():
    sp = screenplay([
        scene("hook", typ="title", title="Boolean logic"),
        scene("obj", title="Learning objectives"),
        scene("s1", title="Boolean variables"),
        scene("obj2", title="What this video will cover"),
        {"id": "s2", "type": "content", "title": "Overview",
         "board": [{"id": "s2-i1", "kind": "heading", "text": "What you will learn"}],
         "beats": [beat("s2-b1", "Here is the list again.", board_item_id="s2-i1")]},
    ])
    issues = intro_issues(sp)
    assert [i.scene_id for i in issues] == ["obj2", "s2"]
    assert all("learning objectives already given in obj" in i.message for i in issues)


def test_near_identical_titles_are_flagged_but_not_chapter_cards_or_quizzes():
    sp = screenplay([
        scene("hook", typ="title", title="Boolean laws"),
        scene("a", title="The commutative law"),
        {"id": "card", "type": "chapter_card", "title": "The commutative law", "beats": []},
        scene("b", title="The Commutative Law!"),
        scene("c", title="The associative law"),
        {"id": "q1", "type": "quiz_checkpoint", "title": "Quick check", "question": "?", "options": ["a", "b"],
         "correct_index": 0, "beats": [beat("q1-b1", "Pick one now.")], "reveal_beats": [beat("q1-b2", "It is a.")]},
        {"id": "q2", "type": "quiz_checkpoint", "title": "Quick check", "question": "?", "options": ["a", "b"],
         "correct_index": 1, "beats": [beat("q2-b1", "Pick one now.")], "reveal_beats": [beat("q2-b2", "It is b.")]},
    ])
    issues = intro_issues(sp)
    assert [i.scene_id for i in issues] == ["b"] and "nearly repeats the title of a" in issues[0].message


def test_operating_system_objectives_are_content():
    sp = screenplay([scene("hook", typ="title", title="Operating systems"), scene("a", title="Objectives of an OS"),
                     scene("b", title="Design objectives of a kernel")])
    assert intro_issues(sp) == []


# ---------------------------------------------------------------------------
# verifier regressions: leaked header lines never switch the lint off; new packaging phrases; titles
# ---------------------------------------------------------------------------

LEAKED_TABLE = ("| S.No | Particulars | Details |\n|---|---|---|\n| 1 | Name of the SME | Dr. Lakshmi Narayanan |\n"
                "| 4 | Course Code | EC3352 |\n| 6 | Reviewed by | Dr. Prakash Rao |\n\n"
                "When a capacitor charges, the current falls as the voltage rises.")


def test_leaked_header_lines_do_not_make_packaging_content():
    assert packaging_phrases("Particulars Name of the SME; Details Dr. Lakshmi Narayanan.", source_text=LEAKED_TABLE) == ["sme"]
    assert packaging_phrases("This lecture was reviewed by Dr. Prakash Rao.", source_text=LEAKED_TABLE) == ["reviewed by"]
    credit = "This script was prepared by Dr. Harini Venkatesh for the e-content cell. Capacitors store charge."
    assert packaging_phrases("This lecture was prepared by Dr. Harini Venkatesh.", source_text=credit) == ["prepared by"]
    chemistry = "Ammonia is prepared by heating ammonium chloride with calcium hydroxide."
    assert packaging_phrases("Ammonia is prepared by heating ammonium chloride.", source_text=chemistry) == []


def test_header_values_that_got_past_scoping_are_tracked():
    ing = ingest(chunks=(LEAKED_TABLE,), excluded=[])
    sp = screenplay([scene("s1", "Dr. Lakshmi Narayanan explains how a capacitor charges."),
                     scene("s2", "This topic belongs to EC3352."),
                     scene("s3", "When a capacitor charges, the current falls.")])
    issues = leaks(sp, ing)
    assert {i.scene_id for i in issues} == {"s1", "s2"}
    assert all("Lakshmi" not in i.message and "EC3352" not in i.message for i in issues)


@pytest.mark.parametrize("text, phrase", [
    ("In the next video, we will see how the capacitor discharges.", "in the next video"),
    ("In the next part, we meet inductors.", "in the next part"),
    ("In the previous video, we learned about charging.", "in the previous video"),
    ("Thank you for watching.", "thank you for watching"),
    ("See you in the next video.", "see you in the next"),
    ("Welcome back! Let us continue.", "welcome back"),
    ("Welcome to video 7 of this course.", "video 7"),
    ("Remember that the video runs 11 minutes.", "video runs"),
])
def test_more_packaging_phrases(text, phrase):
    assert phrase in packaging_phrases(text), text


def test_subject_phrases_with_next_part_are_content():
    assert packaging_phrases("In the next part of the cycle the piston moves down.") == []
    source = "Thank you for watching. The capacitor charges through the resistor."
    assert packaging_phrases("Thank you for watching!", source_text=source) == ["thank you for watching"]


def test_admin_details_in_titles_are_flagged_without_quoting_them():
    sp = screenplay([scene("s1", "A capacitor charges through a resistor.", title="CHARGING A CAPACITOR (Duration: 90 sec)"),
                     scene("s2", "The LM741 is a general purpose op-amp.", title="The LM741 op-amp")],
                    subject_name="Ec3352 - Signals and Systems", unit_name="Unit III - Transients (R2021)",
                    session_title="RC Circuits | Dr. Ramesh Kumar")
    ing = ingest(chunks=("The LM741 op-amp has a gain of about 200000.",), excluded=[])
    issues = leaks(sp, ing)
    scene_issues = [i for i in issues if i.scene_id]
    assert [i.scene_id for i in scene_issues] == ["s1"] and "a video duration" in scene_issues[0].message
    lecture = [i.message for i in issues if i.scene_id is None]
    assert any(m.startswith("The subject name carries a course code") for m in lecture)
    assert any(m.startswith("The unit name carries a course code") for m in lecture)
    assert any(m.startswith("The session title carries a person's name") for m in lecture)
    for i in issues:
        assert "EC3352" not in i.message.upper() and "Ramesh" not in i.message and "90 sec" not in i.message
    typed = GenerationOptions(subject_name="Ec3352 - Signals and Systems", unit_name="Unit III - Transients (R2021)",
                              session_title="RC Circuits | Dr. Ramesh Kumar")
    assert not [i for i in lint(sp, typed, ingest=ing) if i.code == "content.admin_leak" and i.scene_id is None]


def test_only_person_names_and_contacts_are_tracked():
    excluded = [ExcludedItem(category="person", text="Approved by: the Constituent Assembly"),
                ExcludedItem(category="person", text="Prepared by: heating ammonium chloride"),
                ExcludedItem(category="person", text="Reviewed by: Dr. Prakash Rao, Assistant Professor")]
    sp = screenplay([scene("s1", "The Constitution was approved by the Constituent Assembly in 1949."),
                     scene("s2", "Ammonia forms on heating ammonium chloride with lime."),
                     scene("s3", "Dr. Prakash Rao checked these notes."),
                     scene("s4", "An assistant professor teaches this course.")])
    assert [i.scene_id for i in leaks(sp, ingest(excluded=excluded))] == ["s3"]


def test_title_code_check_needs_the_source():
    sp = screenplay([scene("s1", "The LM741 is a general purpose op-amp.", title="The LM741 op-amp")])
    assert leaks(sp) == []  # without the source a part number cannot be told from a course code


def test_a_header_name_is_tracked_even_when_the_narration_also_names_the_author():
    """The SME's name in a narration line used to make lint stop tracking it (it 'occurred in content')."""
    excluded = [ExcludedItem(category="person", text="SME Name: Dr. Meena Raghavan"),
                ExcludedItem(category="person", text="Reviewed by: Prof. K. Srinivasan")]
    chunk = ("Kirchhoff's current law: the currents into a node add up to zero.",
             "Dr. Meena Raghavan will now demonstrate this on the board.")
    ing = ingest(chunks=chunk, excluded=excluded)
    for narration in ("Dr. Meena Raghavan will now show you this.", "As Dr. Meena showed, charge is conserved.",
                      "Prof. Srinivasan checked the sign convention."):
        assert len(leaks(screenplay([scene("s1", narration)]), ing)) == 1, narration
    # a name of one word that the lesson itself uses is still content
    ing = ingest(chunks=("Raman scattering shifts the frequency of light.",),
                 excluded=[ExcludedItem(category="person", text="Author: Raman")])
    assert leaks(screenplay([scene("s1", "Raman scattering shifts the frequency of light.")]), ing) == []
