"""source_review: outline + roles, content inventory (formula symbols), findings + readiness, the teacher's
corrections (effective brief, ingest without set-aside parts), masking and the scene trace."""

from __future__ import annotations

import asyncio
import json
import pathlib
import time

import pytest

from aadhi.pipeline import source_review as sr
from aadhi.pipeline.base import (
    BriefConcept,
    BriefFact,
    ConceptBrief,
    ExcludedItem,
    IngestResult,
    SkippedChunk,
    VisualNote,
)
from aadhi.pipeline.chunking import chunk_markdown
from aadhi.pipeline.ingest import ingest_source
from aadhi.schemas.screenplay import Screenplay, SourceFigure
from tests.pipeline.dbutil import get_source, seed_project

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "source_review"


def read_fixture(job_ctx, name: str) -> IngestResult:
    """A fixture through the real ingest path (text decoding, scoping, chunking)."""
    data = (FIXTURES / f"{name}.txt").read_bytes()
    seeded = seed_project(job_ctx.assets.storage, data, "text/plain", f"{name}.txt")
    job_ctx.project_id = seeded.project_id
    return asyncio.run(ingest_source(job_ctx, get_source(seeded.source_id)))


def md_ingest(markdown: str, **kw) -> IngestResult:
    return IngestResult(markdown=markdown, chunks=chunk_markdown(markdown), source_mime="text/markdown",
                        detected_language=kw.pop("detected_language", "en-IN"), **kw)


def codes(report_or_findings) -> list[str]:
    findings = getattr(report_or_findings, "findings", report_or_findings)
    return [f.code for f in findings]


# --- outline and roles ----------------------------------------------------------------------------


def test_well_structured_source_reads_cleanly(job_ctx):
    ingest = read_fixture(job_ctx, "A_well_structured")
    report = sr.build_report(ingest, filename="A_well_structured.txt")
    assert report.outline.title == "Photosynthesis in Green Plants" and report.outline.title_source == "document"
    sections = [(s.title, s.role) for s in report.outline.sections]
    assert sections == [("Learning Objectives", "objectives"), ("What is Photosynthesis", "teaching"),
                        ("The Role of Chlorophyll", "teaching"), ("The Overall Equation", "teaching"),
                        ("Summary", "summary"), ("Review Questions", "questions")]
    assert report.findings == [] and report.readiness.verdict == "well_structured" and report.readiness.ready
    assert report.inventory.questions == 3 and report.inventory.formula_count == 1
    assert report.inventory.formulas[0].expression == "6CO2 + 6H2O → C6H12O6 + 6O2"
    assert report.inventory.formulas[0].undefined_symbols == []  # (CO2), (H2O), (C6H12O6), (O2) are explained
    assert "Every formula's symbols are explained." in report.strengths
    assert report.source_kind == "text" and report.file == "A_well_structured.txt"
    assert all(c.status == "teaching" for c in report.chunks) and not report.brief_available


def test_poorly_structured_source_gets_findings_with_reasons(job_ctx):
    report = sr.build_report(read_fixture(job_ctx, "B_poorly_structured"))
    for code in ("source.no_headings", "source.long_paragraph", "source.missing_figure", "source.near_duplicate"):
        assert code in codes(report), code
    assert report.readiness.verdict == "needs_reorganization" and not report.readiness.ready
    figure = next(f for f in report.findings if f.code == "source.missing_figure")
    assert "figure 3" in figure.message and figure.chunk_ids
    for f in report.findings:
        assert f.why and f.suggestion and f.id.startswith("f") and len(f.message) <= sr.MESSAGE_CHARS
    # objectives / examples / summary are never asked for: the planner writes them
    assert not any("objective" in f.message.lower() or "summary" in f.message.lower() for f in report.findings)


def test_technical_source_formula_symbols_and_code(job_ctx):
    report = sr.build_report(read_fixture(job_ctx, "C_technical"))
    formulas = report.inventory.formulas
    assert [f.expression for f in formulas] == ["F = m × a", "KE = ½ m v²"]  # exactly as written
    assert formulas[0].undefined_symbols == ["a"]  # "where F is ... and m is ..."
    assert formulas[1].undefined_symbols == ["KE", "v"]  # m was explained earlier; v² is v
    assert report.inventory.code_blocks == 2 and report.inventory.code_unexplained == 1
    unexplained = [f for f in report.findings if f.code == "source.code_unexplained"]
    assert len(unexplained) == 1 and unexplained[0].heading == "Computing the Area of a Circle"
    assert "source.missing_figure" not in codes(report)  # "Table 1: ..." is a caption, not a dangling reference
    assert report.readiness.verdict == "missing_context"


def test_tamil_source_roles_and_text_untouched(job_ctx):
    ingest = read_fixture(job_ctx, "D_tamil")
    report = sr.build_report(ingest)
    assert ingest.detected_language == "ta-IN"
    assert report.outline.title == "ஒளிச்சேர்க்கை"
    assert [s.role for s in report.outline.sections] == ["objectives", "teaching", "summary"]
    excerpts = " ".join(c.excerpt for c in report.chunks)
    assert "பச்சையம் இலைகளில் உள்ள பசுமை நிறமி ஆகும்." in excerpts  # not translated, not changed
    assert "source.term_before_definition" not in codes(report)  # English-only rule


def test_nested_sections_without_title_and_empty_heading():
    ingest = md_ingest(
        "## Cells\n\nA cell is the basic unit of life.\n\n### Nucleus\n\nThe nucleus is a structure that holds DNA.\n\n"
        "#### Nucleolus\n\nIt makes ribosomes.\n\n## Tissues\n\nTissues are groups of cells.\n\n## Organs\n\n"
        "## References\n")
    report = sr.build_report(ingest)
    assert report.outline.title == "" and report.outline.title_source == "not_provided"
    assert [s.title for s in report.outline.sections] == ["Cells", "Tissues", "Organs", "References"]
    cells = report.outline.sections[0]
    assert [t.title for t in cells.subtopics] == ["Nucleus"]  # the deeper heading stays inside its subtopic
    assert len(cells.chunk_ids) == 1 and len(cells.subtopics[0].chunk_ids) == 2
    assert report.outline.sections[3].role == "references"
    empty = [f for f in report.findings if f.code == "source.empty_section"]
    assert [f.heading for f in empty] == ["Organs"]  # an empty references heading is not a gap
    assert "source.no_title" in codes(report)


def test_repeated_sub_headings_own_their_own_parts():
    """A sub-heading repeated under every section ("Introduction") owns the text under it, not the first one."""
    ingest = md_ingest("## Unit 1\n\n### Introduction\n\nCharge flows in a closed loop of wire.\n\n"
                       "## Unit 2\n\n### Introduction\n\nMagnets attract iron and some other metals.\n")
    report = sr.build_report(ingest)
    units = report.outline.sections
    assert [(u.title, [t.chunk_ids for t in u.subtopics]) for u in units] == [
        ("Unit 1", [["c0001"]]), ("Unit 2", [["c0002"]])]
    assert "source.empty_section" not in codes(report)
    assert report.readiness.verdict != "incomplete"


def _topics(report, title: str) -> list:
    return [t for sec in report.outline.sections for t in [sec, *sec.subtopics] if t.title == title]


def test_same_path_headings_split_by_their_first_text():
    ingest = md_ingest("## Unit\n\n### Example\n\nA resistor of two ohms.\n\n### Example\n\nA bulb of sixty watts.\n")
    examples = _topics(sr.build_report(ingest), "Example")
    assert [t.chunk_ids for t in examples] == [["c0001"], ["c0002"]]
    long_a = " ".join(f"Sentence {n} about the first example and its resistor." for n in range(60))  # > 1,900 chars
    ingest = md_ingest(f"## Unit\n\n### Example\n\n{long_a}\n\n### Example\n\nA bulb of sixty watts.\n")
    first, second = _topics(sr.build_report(ingest), "Example")
    assert len(first.chunk_ids) >= 2, "the first example's continuation parts stay with it"
    assert second.chunk_ids == [ingest.chunks[-1].id]


def test_headings_after_a_form_feed_are_read_like_the_chunker_does():
    ingest = md_ingest("# Voltage\n\nVoltage pushes charge.\n\f## Current\n\nCurrent is the flow of charge.\n")
    assert [c.heading_path for c in ingest.chunks] == [["Voltage"], ["Voltage", "Current"]]
    (current,) = _topics(sr.build_report(ingest), "Current")
    assert current.chunk_ids == ["c0002"]


def test_many_repeated_example_headings_have_no_empty_subtopics():
    ingest = md_ingest("".join(f"## Topic {i}\n\n### Example\n\nWorked example number {i}.\n\n" for i in range(5000)))
    started = time.perf_counter()
    report = sr.build_report(ingest)
    assert time.perf_counter() - started < 20
    assert all(t.chunk_ids for sec in report.outline.sections for t in sec.subtopics)
    assert "source.empty_section" not in codes(report)


@pytest.mark.parametrize(("title", "role"), [
    ("Sources of Energy", "teaching"), ("Requirements of a good fuel", "teaching"),
    ("Outcomes of the French Revolution", "teaching"), ("Goals of monetary policy", "teaching"),
    ("Assessment of risk in bridges", "teaching"), ("Practice of medicine in ancient India", "teaching"),
    ("Conclusion of the war", "teaching"),
    ("In summary", "summary"), ("1.2 Summary", "summary"), ("x. Summary", "summary"), ("IV. Summary", "summary"),
    ("(iv) Exercises", "questions"), ("Practice questions", "questions"), ("Practice", "questions"),
    ("Sources", "references"), ("Goals: understand X", "objectives"), ("Learning objectives", "objectives"),
    ("Video lectures", "teaching"), ("Introduction to circuits", "teaching"),
])
def test_role_words(title, role):
    assert sr.role_of(title) == role


def test_a_long_teaching_section_named_sources_is_still_checked():
    body = " ".join(f"Coal, oil and gas are fossil fuels formed over {n} million years." for n in range(160))
    ingest = md_ingest(f"## Sources of Energy\n\n{body}\n\n## Uses of energy\n\nWe cook and travel with it.\n")
    report = sr.build_report(ingest)
    (sources,) = _topics(report, "Sources of Energy")
    assert sources.role == "teaching" and sr.role_of("Sources of Energy") == "teaching"
    assert any(f.code == "source.overloaded_section" and f.heading == "Sources of Energy" for f in report.findings)


def test_term_used_before_its_definition():
    ingest = md_ingest("## Basics\n\nEvery circuit has a resistor somewhere in it, and the current depends on it.\n\n"
                       "## Parts\n\nA resistor is a component that limits the current in a circuit.\n")
    report = sr.build_report(ingest)
    found = [f for f in report.findings if f.code == "source.term_before_definition"]
    assert len(found) == 1 and "resistor" in found[0].message.lower() and len(found[0].chunk_ids) == 2


# --- formulas -----------------------------------------------------------------------------------------


def test_ordinary_words_in_a_formula_are_not_symbols():
    assert sr.formula_parts("Rearranged to the form a = F / m") == (["a", "F", "m"], [])
    assert sr.formula_parts("No = n × A") == (["No", "n", "A"], [])  # a capitalised short word can be a symbol
    assert sr.formula_parts("6CO2 + 6H2O → C6H12O6 + 6O2") == ([], ["CO2", "H2O", "C6H12O6", "O2"])


def test_latex_formulas_and_symbol_definitions():
    ingest = md_ingest("## Energy\n\nEinstein's relation:\n\n$$E = m c^{2}$$\n\nwhere E is the energy, m the mass and "
                       "c the speed of light. The momentum is $p = m v$ for a body.\n")
    inventory = sr.analyze(ingest).inventory
    assert [f.expression for f in inventory.formulas] == ["E = m c^{2}", "p = m v"]
    assert inventory.formulas[0].undefined_symbols == []
    assert inventory.formulas[1].undefined_symbols == ["p", "v"]
    assert sr.latex_plain(r"\frac{\Delta x}{t} = \alpha_{1}").split() == ["Δ", "x", "t", "=", "α_1"]


def test_large_sources_stay_fast():
    sections = []
    for i in range(400):
        para = " ".join(f"word{(i * 7 + j) % 997}" for j in range(160))
        sections.append(f"## Section {i}\n\n{para}.\n\nF{i % 9} = m × a{i % 5}\n\nwhere m is the mass.\n")
    markdown = "\n".join(sections)
    assert len(markdown) > 300_000
    ingest = md_ingest(markdown)
    started = time.perf_counter()
    report = sr.build_report(ingest)
    assert time.perf_counter() - started < 20
    assert len(report.findings) <= sr.MAX_FINDINGS and report.findings_total >= len(report.findings)
    assert len(report.inventory.formulas) <= sr.MAX_FORMULAS and report.inventory.formula_count == 400
    capped = [f for f in report.findings if f.code == "source.formula_undefined_symbols"]
    assert any("more like this" in f.message for f in capped)


# --- readiness ----------------------------------------------------------------------------------------


def test_readiness_follows_findings_marked_done(job_ctx):
    ingest = read_fixture(job_ctx, "C_technical")
    report = sr.build_report(ingest)
    warnings = [f.id for f in report.findings if f.severity == "warning"]
    assert warnings and not sr.readiness(report.findings).ready
    after = sr.readiness(report.findings, done=warnings)
    assert after.ready and after.warnings == 0
    assert sr.readiness(report.findings, done=[f.id for f in report.findings]).verdict == "well_structured"
    again = sr.build_report(ingest.model_copy(deep=True), analysis=sr.analyze(ingest))
    assert [f.id for f in again.findings] == [f.id for f in report.findings]  # stable ids ("mark as done" survives)


# --- what was set aside never shows --------------------------------------------------------------


def test_removed_values_are_never_shown():
    markdown = ("## Ohm's law prepared by Ramesh Kumar\n\nVoltage equals current times resistance, as Ramesh Kumar "
                "explains with V = I × R where V is the voltage.\n\n## Resistance\n\nResistance opposes the current.\n")
    ingest = md_ingest(markdown, excluded=[ExcludedItem(category="person", text="SME Name: Ramesh Kumar",
                                                        reason="author")],
                       warnings=["The SME Ramesh Kumar recorded this."])
    report = sr.build_report(ingest)
    dumped = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "Ramesh" not in dumped and "SME Name" not in dumped
    assert report.scope == [sr.ScopeCount(category="person", count=1, label=sr.SCOPE_LABELS["person"], source="rules")]
    assert report.outline.sections[0].title.startswith("(hidden")
    assert report.warnings == []


# --- the teacher's corrections --------------------------------------------------------------------

BRIEF_MD = ("## Voltage\n\nVoltage pushes charge around a circuit and is measured in volts.\n\n"
            "## Current\n\nCurrent is the rate of flow of charge. What is current?\n\n"
            "## Coming up next\n\nIn the next video we look at power.\n\n"
            "## Power\n\nPower is the rate of energy transfer, P = V × I. What is power?\n")


def brief_for(ingest: IngestResult) -> ConceptBrief:
    ids = [c.id for c in ingest.chunks]
    return ConceptBrief(
        topic="Circuits",
        concepts=[
            BriefConcept(key="voltage", name="Voltage", key_facts=[BriefFact(text="V in volts", source_refs=[ids[0]])],
                         source_refs=[ids[0]]),
            BriefConcept(key="current", name="Current", prerequisites=["voltage"], source_refs=[ids[1]]),
            BriefConcept(key="power", name="Power", prerequisites=["voltage", "current"], source_refs=[ids[3]],
                         key_facts=[BriefFact(text="P = V x I", source_refs=[ids[3]])]),
        ],
        teaching_order=["voltage", "current", "power"],
        source_questions=["What is current?", "What is power?"],
        source_question_refs=[ids[1], ids[3]],
        skipped_chunks=[SkippedChunk(chunk_id=ids[2], reason="scaffolding")],
    )


def test_effective_brief_applies_corrections_and_is_idempotent():
    ingest = md_ingest(BRIEF_MD)
    ids = [c.id for c in ingest.chunks]
    brief = brief_for(ingest)
    ov = sr.SourceOverrides(excluded_chunk_ids=[ids[3]], restored_chunk_ids=[ids[2]],
                            concept_names={"current": "Electric current"})
    eff = sr.effective_brief(brief, ingest, ov)
    assert [c.key for c in eff.concepts] == ["voltage", "current"]  # power was taught only from the set-aside part
    assert eff.teaching_order == ["voltage", "current"]
    assert eff.concepts[1].name == "Electric current" and eff.concepts[1].key == "current"
    assert eff.skipped_chunks == [SkippedChunk(chunk_id=ids[3], reason=sr.TEACHER_SKIP_REASON)]  # ids[2] restored
    assert eff.source_questions == ["What is current?"] and eff.source_question_refs == [ids[1]]
    assert ids[3] not in eff.cited_chunk_ids()
    assert sr.effective_brief(eff, ingest, ov) == eff  # applying the corrections again changes nothing
    assert sr.effective_brief(brief, ingest, None) is brief


GEYSER_MD = ("## Power\n\nPower is the rate of energy transfer, measured in watts.\n\n"
             "## Worked example: the geyser\n\nA 2 kW geyser converts electrical energy into heat for "
             "every bucket of bath water.\n")


def test_prose_drawn_only_from_a_set_aside_part_never_reaches_the_planner():
    """A concept still taught from a kept part loses the examples, must_explain points and why_it_matters
    that came only from the part the teacher set aside."""
    from aadhi.pipeline import plan

    ingest = md_ingest(GEYSER_MD)
    ids = [c.id for c in ingest.chunks]
    brief = ConceptBrief(topic="Power", concepts=[BriefConcept(
        key="power", name="Power", source_refs=ids,
        why_it_matters="A 2 kW geyser heats bath water using electrical energy.",
        must_explain=["Power is a rate of energy transfer", "How the geyser converts electrical energy into heat"],
        examples=["A 2 kW geyser converts electrical energy into heat"],
        key_facts=[BriefFact(text="P is measured in watts", source_refs=[ids[0]])])],
        teaching_order=["power"])
    ov = sr.SourceOverrides(excluded_chunk_ids=[ids[1]])
    eff = sr.effective_brief(brief, ingest, ov)
    (power,) = eff.concepts
    assert power.must_explain == ["Power is a rate of energy transfer"]
    assert power.examples == [] and power.why_it_matters == ""
    payload = json.dumps(plan.brief_payload(eff, sr.without_chunks(ingest, [ids[1]])))
    assert "geyser" not in payload.lower()
    assert sr.effective_brief(eff, ingest, ov) == eff
    # a concept that never cited the set-aside part keeps its prose
    untouched = brief.model_copy(update={"concepts": [brief.concepts[0].model_copy(update={"source_refs": [ids[0]]})]})
    assert sr.effective_brief(untouched, ingest, ov).concepts[0].examples == brief.concepts[0].examples


def test_the_teachers_set_asides_and_the_briefs_skips_are_never_cut():
    """800 parts: the brief skips 400 as scaffolding, the teacher sets aside 200 more; all 600 stay skipped (a cut
    at 500 sent 100 of the brief's skips back to the planner)."""
    from types import SimpleNamespace

    from aadhi.pipeline import plan, plan_state

    ingest = md_ingest("".join(f"## Part {n}\n\nBody text of part number {n}.\n\n" for n in range(1, 801)))
    ids = [c.id for c in ingest.chunks]
    assert len(ids) == 800
    brief = ConceptBrief(topic="t", concepts=[BriefConcept(key="k", name="K", source_refs=ids[:20])],
                         teaching_order=["k"],
                         skipped_chunks=[SkippedChunk(chunk_id=c, reason="scaffolding") for c in ids[400:]])
    excluded = ids[100:300]
    ov = sr.SourceOverrides(excluded_chunk_ids=excluded)
    eff = sr.effective_brief(brief, ingest, ov)
    assert plan.skipped_ids(eff) == set(ids[100:300]) | set(ids[400:])
    payload = plan._chunk_payload(sr.without_chunks(ingest, excluded), plan.PLAN_CONTEXT_CHARS, plan.skipped_ids(eff))
    sent = {c["id"] for c in payload}
    assert not sent & set(ids[400:]) and not sent & set(excluded)
    assert sr.effective_brief(eff, ingest, ov) == eff
    version = SimpleNamespace(id=1, generation_meta={})
    plan_state.save_brief(version, eff)
    assert plan_state.load_brief(version) == eff  # 600 skips validate on load (ConceptBrief allows 1000)


def test_report_builds_the_cited_and_skipped_sets_once(monkeypatch):
    ingest = md_ingest(BRIEF_MD)
    brief = brief_for(ingest)
    calls = {"cited": 0, "skipped": 0}
    cited, skipped = ConceptBrief.cited_chunk_ids, ConceptBrief.skipped_chunk_ids

    def count_cited(self):
        calls["cited"] += 1
        return cited(self)

    def count_skipped(self):
        calls["skipped"] += 1
        return skipped(self)

    monkeypatch.setattr(ConceptBrief, "cited_chunk_ids", count_cited)
    monkeypatch.setattr(ConceptBrief, "skipped_chunk_ids", count_skipped)
    big = md_ingest("".join(f"## Part {n}\n\nBody {n}.\n\n" for n in range(400)))
    sr.build_report(big, brief=brief_for(big))
    assert calls["cited"] <= 4 and calls["skipped"] <= 4  # not once per chunk (400)
    report = sr.build_report(ingest, brief=brief)
    assert [c.aadhi_status for c in report.chunks] == ["teaching", "teaching", "set_aside", "teaching"]


def test_prerequisites_on_dropped_concepts_are_removed():
    ingest = md_ingest(BRIEF_MD)
    ids = [c.id for c in ingest.chunks]
    eff = sr.effective_brief(brief_for(ingest), ingest, sr.SourceOverrides(excluded_chunk_ids=[ids[0]]))
    current = next(c for c in eff.concepts if c.key == "current")
    assert "voltage" not in [c.key for c in eff.concepts] and current.prerequisites == []
    power = next(c for c in eff.concepts if c.key == "power")
    assert power.prerequisites == ["current"]


def test_without_chunks_drops_parts_figures_and_notes():
    markdown = "## A\n\nAlpha text here.\n\n[Figure fig-1: Figure 1: a cell]\n\n## B\n\nBeta text here.\n"
    ingest = md_ingest(markdown, figures=[SourceFigure(id="fig-1", caption="Figure 1: a cell")],
                       visual_notes=[VisualNote(id="v0001", near_chunk_id="c0001", text="animate the cell")])
    out = sr.without_chunks(ingest, ["c0001"])
    assert [c.id for c in out.chunks] == ["c0002"] and out.figures == [] and out.visual_notes == []
    assert "Alpha" not in out.markdown and "Beta" in out.markdown
    assert sr.without_chunks(out, ["c0001"]) is out
    assert sr.without_chunks(ingest, []) is ingest


def test_override_problems_and_validation():
    ingest = md_ingest(BRIEF_MD)
    ids = [c.id for c in ingest.chunks]
    brief = brief_for(ingest)
    assert sr.override_problems(sr.SourceOverrides(excluded_chunk_ids=[ids[0]]), ingest, brief) == []
    bad = sr.SourceOverrides(excluded_chunk_ids=["c0099"], restored_chunk_ids=[ids[0]], concept_names={"nope": "X"})
    msgs = " ".join(p["msg"] for p in sr.override_problems(bad, ingest, brief))
    assert "c0099" in msgs and "Only parts Aadhi set aside" in msgs and "Unknown concepts: nope" in msgs
    everything = sr.SourceOverrides(excluded_chunk_ids=ids)
    assert "Keep at least one part" in sr.override_problems(everything, ingest, brief)[0]["msg"]
    with_aadhis = sr.SourceOverrides(excluded_chunk_ids=[ids[0], ids[1], ids[3]])  # ids[2] is set aside by the brief
    assert "Keep at least one part" in sr.override_problems(with_aadhis, ingest, brief)[0]["msg"]
    restored = sr.SourceOverrides(excluded_chunk_ids=[ids[0], ids[1], ids[3]], restored_chunk_ids=[ids[2]])
    assert sr.override_problems(restored, ingest, brief) == []
    with pytest.raises(ValueError):
        sr.SourceOverrides(excluded_chunk_ids=["../etc"])
    with pytest.raises(ValueError):
        sr.SourceOverrides(concept_names={"power": " "})
    assert sr.SourceOverrides(concept_names={"power": "  Electric   power "}).concept_names == {"power": "Electric power"}


def test_overrides_only_apply_to_their_extract():
    ov = sr.SourceOverrides(ingest_key="extract-a", excluded_chunk_ids=["c0001"]).model_dump(mode="json")
    meta = {sr.OVERRIDES_KEY: ov}
    assert sr.overrides_for(meta, "extract-a") is not None
    assert sr.overrides_for(meta, "extract-b") is None and sr.overrides_for({}, "extract-a") is None
    assert sr.load_overrides({sr.OVERRIDES_KEY: {"excluded_chunk_ids": ["bad id"]}}) is None


def test_report_statuses_concepts_and_accounting():
    ingest = md_ingest(BRIEF_MD)
    ids = [c.id for c in ingest.chunks]
    brief = brief_for(ingest)
    ov = sr.SourceOverrides(excluded_chunk_ids=[ids[3]], restored_chunk_ids=[ids[2]],
                            concept_names={"current": "Electric current"})
    report = sr.build_report(ingest, brief=brief, overrides=ov)
    status = {c.id: (c.status, c.aadhi_status, c.reason) for c in report.chunks}
    assert status[ids[0]] == ("teaching", "teaching", "")
    assert status[ids[2]] == ("restored", "set_aside", "scaffolding")
    assert status[ids[3]] == ("set_aside_by_you", "teaching", "")
    acc = report.accounting
    assert (acc.chunks, acc.cited, acc.set_aside_by_you, acc.restored) == (4, 2, 1, 1)
    rows = {c.key: c for c in report.concepts}
    assert rows["current"].name == "Electric current" and rows["current"].original_name == "Current"
    assert rows["power"].dropped and rows["current"].headings == ["Current"]
    assert report.overrides == ov and report.brief_available and report.inventory.source_questions == 1
    plain = sr.build_report(ingest, brief=brief)
    assert {c.id: c.status for c in plain.chunks}[ids[2]] == "set_aside"
    assert plain.accounting.skipped_by_reason == {"scaffolding": 1}


def test_scene_trace_lists_the_parts_each_scene_cites():
    ingest = md_ingest(BRIEF_MD)
    sp = Screenplay.model_validate({"session_title": "Circuits", "scenes": [
        {"id": "s1", "type": "content", "title": "Voltage", "board": [
            {"id": "s1-i1", "kind": "bullet", "text": "Volts", "source_refs": ["c0001"]}],
         "beats": [{"id": "s1-b1", "narration": "Voltage pushes charge.", "source_refs": ["c0001", "c0042"]}]},
        {"id": "s2", "type": "content", "title": "Made up", "board": [], "beats": [
            {"id": "s2-b1", "narration": "Something else."}]},
    ]})
    trace = sr.build_report(ingest, screenplay=sp).scenes
    assert [(t.scene_id, t.chunk_ids, t.headings, t.unknown_refs) for t in trace] == [
        ("s1", ["c0001"], ["Voltage"], 1), ("s2", [], [], 0)]


# --- inline formulas inside sentences (REVIEW_VERSION 3) ------------------------------------------------------


def test_inline_formulas_in_sentences_are_counted_and_checked():
    ingest = md_ingest(
        "## Ohm's law\n\nVoltage is measured in volts (V). For an ohmic conductor the current is proportional to the "
        "voltage across it: V = I x R. Doubling the voltage doubles the current.\n\n"
        "## Worked example\n\nA 12 V battery drives a 4 ohm resistor, so the current is I = V / R = 12 / 4 = 3 A.\n\n"
        "## Energy\n\nEinstein showed that E = mc² for a body at rest, where E is the energy and m is the mass.\n")
    inventory = sr.analyze(ingest).inventory
    assert [(f.expression, f.undefined_symbols) for f in inventory.formulas] == [
        ("V = I x R", ["I", "R"]),  # (V) is explained; a spaced "x" is a multiplication sign, not a symbol
        ("I = V / R = 12 / 4 = 3", ["I", "R"]),  # one formula for the whole chain; the unit "A" is left out
        # "E = mc²" is an implicit product (with an exponent): not counted, like "F = ma"
    ]
    assert inventory.formula_count == 2 and inventory.formulas_with_undefined_symbols == 2


def test_prose_and_code_are_not_inline_formulas():
    for line in (
        "In this case a = b, so the two sides are equal.",  # no operator: an equality in prose
        "We set x = 5 and continue with the next step.",
        "The Total = 5 + 3 = 8 marks for this question.",  # long words are never operands
        "Remember that 1 + 1 = 2 in ordinary arithmetic.",  # no symbol
        "Speed = distance / time for uniform motion.",
        "Use `a = b + c` in Python to add the two numbers.",  # inline code
        "See https://example.com/?a=b+c for the details.",  # a URL
        "It is = to the value + one more, said the teacher.",  # ordinary words
        "if (count == total + 1) { return; }",  # a code line
        "Then F = ma by the second law.",  # implicit products are not counted (conservative)
        "Einstein showed that E = mc² for a body at rest.",  # ... with an exponent too
        "The area of a circle is A = πr², where r is the radius.",
        "Remember that 1 km/h = 5/18 m/s when solving problems.",  # the units of a quantity
        "The unit used on bills is 1 kWh = 3.6 × 10⁶ J of energy.",
    ):
        assert sr.inline_formulas(line) == [], line
    assert sr.inline_formulas("The density is ρ = m/V for a solid.") == [("ρ = m/V", "ρ = m/V")]  # still a formula
    ingest = md_ingest("## Code\n\nThe loop adds one each time.\n\n```python\ntotal = total + 1\n```\n\n"
                       "| a = b + c | x |\n|---|---|\n| 1 | 2 |\n")
    assert sr.analyze(ingest).inventory.formula_count == 0  # fenced code and tables


def test_inline_formulas_do_not_backtrack_catastrophically():
    lines = [
        "Revenue = " + " + ".join(str(1_200_000 + 13_579 * i) for i in range(11)),
        "Total = " + " + ".join(str(10_000 + 1_111 * i) for i in range(30)),
        "x = 2 and " + "9" * 4980 + " books",
        "y = " + " + ".join(["12"] * 1200) + " z",
        "Total = " + " + ".join(["123456789"] * 490),
        "So x = 5 while the serial is " + "4817" * 750 + " units.",
    ]
    start = time.perf_counter()
    for line in lines:
        sr.inline_formulas(line)
    assert time.perf_counter() - start < 1.0
    assert sr.inline_formulas("P = 2 x 1,234,567 W") == [("P = 2 x 1,234,567", "P = 2 × 1,234,567")]


def test_a_formula_line_is_counted_once():
    ingest = md_ingest("## Law\n\nThe law reads:\n\nV = I × R\n\nwhere V is the voltage, I the current and R the "
                       "resistance.\n")
    inventory = sr.analyze(ingest).inventory
    assert [(f.expression, f.undefined_symbols) for f in inventory.formulas] == [("V = I × R", [])]
    assert sr.inline_formulas("மின்னழுத்தம் V = I × R ஆகும் என்று நாம் படித்தோம்.") == [("V = I × R", "V = I × R")]
