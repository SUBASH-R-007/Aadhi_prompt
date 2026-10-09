# Prompts: how they are built, what the model never sees, how to inspect and change them

Every model call in the lecture pipeline sends a **system prompt** (fixed instructions from
`aadhi/pipeline/prompts/*.md`) and a **user prompt** (Markdown sections, most of them holding one
fenced JSON block, built from the source and the plan). The provider also sends the **response
schema** (a `gen_models` class) as the structured-output schema. Nothing else is sent, except the
original file in one case (see "Attachments" below).

To see the exact prompts for a document without calling any model:

```bash
.venv/Scripts/python.exe -m aadhi.pipeline.preview path/to/source.docx --out prompt-preview
```

See [Previewing prompts](#previewing-prompts) below for the details.

## Stages and their calls

| Stage | Code | System prompt (files, in order) | Response schema | Model tier | Temperature |
|---|---|---|---|---|---|
| Concept brief | `brief.build_brief` | `brief.md` | `GenBrief` | `fast` (an admin's `llm_model_plan` override wins) | 0.2 |
| Plan | `plan.generate_plan` | `style.md` + `plan.md` | `GenPlan` | `plan` | 0.5 |
| Scene writer (one call per scene, in parallel) | `script.write_scene` | `style.md` + `scene_<family>.md` | `GenBoardScene`, `GenChapterCard`, `GenQuiz`, `GenSimulation_<template>` / `GenSimulationCode`, `GenAIVideo`, `GenInteractive` | `script` | 0.6 |
| Critic | `critic.critique` | `critic.md` | `GenCritique` | `critic` | 0.2 |
| Repair / regenerate a scene | `repair.rewrite_scene` | `style.md` + `scene_<family>.md` + `repair.md` | the scene's schema | `script` | 0.6 |
| Companion sheet | `companion.build_sheet` | `practice.md` | `GenPractice` | `script` | 0.5 |
| Translation | `translate.translate_screenplay` | `translate.md` | `GenTranslation` | `script` | 0.2 |
| AI terminology check (optional, `QUALITY_AI_TERMINOLOGY`; whole-lecture review only) | `quality.assist.terminology_assist` | `quality_terms.md` | `GenTermAnswers` | `fast` | 0.0 |
| Library item description (optional, `LIBRARY_AI_DESCRIBE_ENABLED`; one item, on request, never stored until the teacher saves it) | `library.describe` | `library_describe.md` | `GenLibraryDescription` | `fast` of the default engine | 0.2 |

Every call goes to the lecture's AI engine (`GenerationOptions.llm_provider`, else `LLM_PROVIDER`),
and the tier names that engine's model (`aadhi.providers.factory.llm_models`): Gemini
`LLM_MODEL_<TIER>`, OpenAI `OPENAI_MODEL_<TIER>`, Claude `ANTHROPIC_MODEL_<TIER>`. Admin overrides
(`llm_model_plan` / `llm_model_script`) only apply when they belong to that engine. Claude never
receives a temperature (current Claude models reject sampling parameters); its depth is
`ANTHROPIC_EFFORT`.

The scene families are `board` (title, content, example, summary, key_takeaway, recap), `chapter`,
`quiz`, `simulation`, `ai_video` and `interactive` (`script.FAMILY_PROMPTS`).

**Instructions inside the source.** Every system prompt of a stage that reads the source (`style.md`
for the plan, scene writers and repair, `brief.md`, `critic.md`, `practice.md`) says that text inside
the source document is teaching content, never instructions: requests or commands in it (to ignore
the rules, change the response format, write about something else) are treated as material and not
followed. Ingest still warns the teacher about such text (counts and pages, never the text).
`quality_terms.md` says the same of the term pairs it receives, and `library_describe.md` of a library
item's words and picture. Current versions: `style.md` 3, `brief.md` 4, `critic.md` 4, `practice.md` 2,
`quality_terms.md` 1, `library_describe.md` 1.

**AI terminology check.** Off by default. One call per generated lecture, at most 8 pairs from the
board and titles (an abbreviation never spelled out with a term it might stand for, or two names for
one concept); the answers are validated (only listed pair numbers) and a confident "same" becomes a
`terminology.ambiguous` note (`source=critic`, never fixable, never handed to a rewrite). Budget stops
and cancellations propagate; any other failure only logs. A single-scene review never calls it.

**Source review.** The source check before planning (`review_source`) changes no prompt: the
teacher's corrections edit the concept brief and remove the set-aside chunks from the ingest before
the plan is written, and set-aside chunks are recorded as skipped with reason `off_topic`.
`PROMPT_VERSION`s are unchanged by it. For scanned or maths-heavy PDFs the original file still goes
to the writers (see Attachments), so a set-aside page can still be visible there.

When an answer fails validation (schema errors, or the stage's own checks such as unknown chunk ids,
a missing bridge or a board that is too long), the provider re-asks within the same call. It sends
the previous answer and the list of problems as follow-up turns (`providers/llm/structured.py`).
The first request is always the one shown by the preview.

## What each user prompt contains

All sections are built by pure functions, so the preview and the tests can rebuild them:
`brief.build_brief_prompt`, `plan.build_plan_prompt` and `scene_context.scene_prompt_sections`
(plus the source note and the task that `script.write_scene` appends).

**Concept brief** (`build_brief_prompt`)

1. `Request`: audience, depth, narration language, the source format, and how many visual
   suggestions were set aside.
2. `About the source`: for an SME video script, a note that its scaffolding was removed. Also the
   ingest warnings, and the attachment note when the original file goes along.
3. `Source chunks`: the scoped teaching content (`id`, heading path, text).
4. `Task`: write the brief and account for all N chunk ids.

The brief must account for every chunk (see [Chunk accounting](#chunk-accounting-what-the-brief-sets-aside)):
each chunk is cited by a concept, a key fact or a source question, or listed in `skipped_chunks`
with a reason.

**Plan** (`build_plan_prompt`)

1. `Request`: audience, depth, `target_minutes`, languages, quiz settings and the allowed scene types
   and side panels. It also carries the teacher's subject, unit, session and session title. Empty
   fields are filled from the source's own header (`IngestResult.document_meta`) and are used for
   title cards only.
2. `Previous session summary` and `Teacher's extra instructions`, when given.
3. `About the source`: the source format note (an SME script becomes "one continuous session", and
   a point may appear twice in a chunk, as narration and as a board line), that the length comes
   from `target_minutes`, how many sections the concept brief set aside as non-teaching material (a
   count only), and the ingest warnings (any that mention a removed value are dropped).
4. `Concepts to teach`: the concept brief (topic, teaching order, and each concept with
   `must_explain`, key facts, examples, prerequisites and chunk refs), plus the source's quiz
   questions.
5. `Animation templates` and `Source figures`.
6. `Author's visual suggestions`: the SME's ANIMATION / VISUAL directions with ids. These are ideas
   for visuals, never narration.
7. `Source chunks` and `Task`. With a brief, `Source chunks` leaves out the chunks it set aside: the
   cited chunks stay, and so does any chunk the brief neither cited nor skipped (a safety net).
   Without a brief (it failed, or `BriefUnavailable`) every chunk is shown.

**Scene writer** (`scene_prompt_sections` + `write_scene`)

1. `Lecture`: metadata, objectives and concepts.
2. `Outline`: every scene with its type, narrative role and goal (cut at a sentence or word boundary),
   so the writer can bridge.
3. `This scene`: the scene's plan, with its narrative role, `bridge_in`, goal, key points, concept,
   objectives, chunk refs, the planned visual and its target length.
4. `Neighbouring scenes`: goal, role and bridge of the previous and next scenes.
5. `Misconceptions to target` (and `Other known misconceptions` for quizzes).
6. `Relevant source`: `{note, chunks}` with the chunks the scene cites (keyword matches when it cites
   none). A chunk that another teaching scene also cites carries `taught_in` with those scene ids;
   the note says to cover only this scene's part of it and leave theirs to them.
7. `Nearby source (context only)`: the chunks just before and after the cited ones, each with
   `taught_in`, under a note that says they are for continuity only: the writer neither re-teaches
   a neighbour's material nor teaches ahead. Cold opens (`title`), key takeaways, quizzes, chapter
   cards, recaps and summaries never count as teaching a chunk. Chunks the concept brief set aside
   never appear here, nor among the keyword matches of a scene that cites nothing
   (`LectureContext.skipped_chunk_ids`; repair and regeneration read the brief back from
   `generation_meta['brief']`, which a translation copies from its source version).
8. `Figures`, `Author's visual suggestions` (those the plan chose, else those next to the cited
   chunks), `Glossary`, `Animation template` and `Teacher's extra instructions`.
9. `About the source`: for SME scripts, "treat its narration as raw material" and "a point may
   appear twice".
10. `Task`.

A chapter card gets no source at all: the outline, its bridge and key points are all it needs. A
quiz gets only the chunks it cites, no nearby source, and a `Quiz scope` note: ask only about what
the scenes up to this one taught (a quiz that cites nothing matches keywords only among chunks
earlier scenes cite). Neither gets `Figures` or `Author's visual suggestions`. The critic sees board
reveals without empty fields.

Repair and regeneration add `Current scene`, `Issues to fix` and
`Teacher's instructions for this rewrite`.

## What the model never sees

Source scoping (`source_scope.scope_source`, run by `ingest` before chunking) splits the uploaded
document into teaching content, metadata, visual suggestions and excluded items:

| Kept out of every prompt | Where it goes instead |
|---|---|
| SME / author / reviewer names, designations, departments, contact details | `IngestResult.excluded` (`person`, `admin`) |
| The SME's or reviewer's name inside the narration: a presenter cue or self-reference ("Dr. X will now demonstrate this on the board", "Hello, I am X") is dropped, any other mention of the full name (or an honorific with part of it) becomes "the instructor" | `excluded` (`production_note`, `person`) |
| Video, clip and segment durations, word counts, segment timing lines ("Duration: 45 sec"), and narration that states the video's length ("This video is about 7 minutes long", "In this 10-minute video") | `excluded` (`duration`) |
| Timecodes such as `[0:10 - 1:15]` | `excluded` (`timecode`) |
| Course codes, regulations, semesters, dates, versions, institution boilerplate, "page x of y", copyright lines, and the running headers, footers and page numbers a PDF's layout drops | `excluded` (`admin`) |
| Recording and editing directions ("fade to black", "NOTE: Dr. X will record this segment", "NOTE TO EDITOR:", "EDITOR NOTE:", "PRODUCTION NOTE:", "SFX:", and ALL-CAPS "CAMERA:", "CUT TO:", "LOWER THIRD:", "B-ROLL:"), the presenter's self-introduction | `excluded` (`production_note`) |
| Per-clip title cards, recaps at the start of a later clip, repeated "what this video will cover" blocks, "bridge to next part" / "what's next" segments, sign-offs and greetings ("Thank you for watching", "Don't forget to like and subscribe!", "Hope you enjoyed this video", "Welcome back, everyone!"), pointers to other videos ("Welcome to video 2 of the series"), clip labels, narrator and board labels | `excluded` (`structure`) |
| In SME narration, the "In this video, we will learn X" lead-in ("We will learn X" stays) and clip pointers ("as we saw in Clip 1" becomes "as we saw earlier") | `excluded` (`structure`) |
| A board line or bullet that repeats a line of the same section word for word (SME scripts) | `excluded` (`duplicate`) |
| ANIMATION / VISUAL / ON SCREEN directions (SME scripts; ALL-CAPS labels in notes) | `IngestResult.visual_notes`: listed as suggestions, never narrated |
| Subject, unit and session lines (with codes, durations and names cut out of them), including a combined title-page line in any format ("Basic Electrical Engineering - Unit 1: Electric Circuits - Session 2", "Subject: X \| Unit 2 \| Session 3", "Course: X, Module 4, Lecture 2"), and a PDF's own title (not for SME scripts) | `IngestResult.document_meta`: fills empty title-card fields only |

Before scoping, extracted text is cleaned (`textnorm.normalize_extracted_text`): ligatures such as
`ﬁ` in "veriﬁcation" (U+FB00–U+FB06) are spelled out, non-breaking and zero-width spaces become
normal spaces or nothing, and mojibake is repaired: every complete UTF-8 sequence read as
Windows-1252 is decoded back when its bytes are valid UTF-8 ("Î©" → "Ω", "Ïƒ" → "σ", "â‰¤" → "≤",
"10â\x81»Â³" → "10⁻³"), then a table fixes sequences that lost a byte ("â€�", "Ï�").
Correct accented text ("café", "Ångström") is untouched, and full NFKC is never applied because it
would turn "x²" into "x2". In PDFs, a display equation set in a large font ("V = I × R") stays a
text line instead of becoming a heading that would swallow the sections after it, and a wrapped
line with an early colon ("resisting force per unit area: σ = F / A") continues its sentence: only a
known header field or script label, or a capitalised "Key:" line after a finished sentence, starts
a new paragraph (`pdf_layout.label_starts_paragraph`).

Header data is recognised in the shapes SME documents use:

- `Key: value` lines, `Key - value`, and separator-less lines in a header ("Prepared by Dr. X",
  "Approved by<TAB>Dr. Y", "Total running time 15 minutes"); a key on its own line with its value on
  the next ("Prepared by:" / "Dr. X, Assistant Professor").
- Key families rather than exact strings: "Name of the Resource Person", "Content Developer",
  "Faculty Name & Designation", "Video Duration (approx.)", "Total Duration of the Video", "Course
  Code & Name", "Year / Semester", "Reviewed and approved by" ...
- Title-page lines that combine subject, unit and session (`source_scope.parse_title_line`): split at
  `|`, a spaced dash or commas; every part must be a subject, a unit ("Unit 1: Title", "Module 4"),
  a session ("Session 2", "Lecture 2") or an administrative fragment (a code, a semester, a name),
  and at least two of subject / unit / session must be present. Only in the front matter or right
  under the document's title; the same line repeated later is dropped as a running header.
- Header tables: row-wise (a serial-number column such as `S.No` and a `Particulars | Details` /
  `Field | Value` header row are skipped, a merged "VIDEO DETAILS" title row is dropped), or
  column-wise with exactly one data row in the front matter.
- YAML front matter, `> quoted` and `_Label_ —` lines, HTML comments, and page footers such as
  "Prepared by Dr. X · Page 2 of 2".
- AV script tables (`Visual | Narration [| Time]`): the narration is kept, the visual column becomes
  visual suggestions, and title-card, "coming up next" and sign-off rows are dropped.

Scoping is conservative: only header-shaped data goes, never teaching content.

- A key counts only with a value of its shape: a person key needs a person's name ("Approved by: the
  Constituent Assembly" and "Prepared by: heating ammonium chloride" are content), a duration key a
  video length ("Duration: 2 ms", "Estimated duration: 4 hours" are content), a code key a code
  ("Regulation: 4.5%" is content).
- Ambiguous keys (Department, Email, Date, Version, Semester, Duration, Author, Name ...) count only
  in a header context: the front matter, a metadata table, a sign-off at the end, or next to an
  unambiguous key such as "SME Name". A block after "for example:", a glossary whose values read as
  definitions, and a multi-row data table (faculty, courses, versions) are content.
- In SME scripts, only narrator labels ("Aadhi speaks:", "Narration:") and real BOARD / ANIMATION
  labels are labels: "Ohm's law says: …", "Board of directors: …", "Visual cortex: …" stay. Times in
  narration ("from 9:00 to 17:30", "(5 seconds)") stay; only bracketed ranges such as `[0:10 - 1:15]`
  are removed. Segments titled "TRANSITION TO THE NEXT STATE", "NEXT PART OF THE CYCLE" or "OUTCOMES"
  are content.
- A document is read as an SME script only with an SME-specific marker (CLIP n SCRIPT, a narrator
  label, a BOARD or ANIMATION label, a bracketed timecode range, or an AV script table); drama and
  film-studies notes stay notes.

Excluded values are never sent, **not even as "things to avoid"**: no prompt lists the SME's name,
so no prompt can leak it. Job events and the preview report categories and counts only. The concept
brief's own `excluded` list, and brief notes that mention a removed value, are not passed to the
planner either (`plan.brief_payload`, `plan.about_source`). Lint checks the finished lecture
deterministically (`validate.lint_source_leaks`, code `content.admin_leak`):

- no packaging talk: "in this video", clip or segment numbers, timecodes, "estimated duration",
  "in the next video", "welcome back", "thank you for watching", "video 7". A phrase is content only
  when the prose of the scoped source uses it itself; header lines and table rows never count, and
  "prepared by" counts only where the source does not follow it with a person's name;
- none of the removed header values, nor the values of header fields that got past scoping (a
  leaked "| 1 | Name of the SME | Dr. X |" row is still tracked); only person names and contact
  details are tracked for people. A value that the scoped source also uses is content (a scientist
  the lesson is about), except a name of two or more words removed from a person field, and the
  honorific with each part of it ("Dr. Meena"): those are always tracked;
- no duration, clip number, course code or person's name in the subject, unit, session, chapter or
  scene titles (fields the teacher typed are not checked).

The repair stage rewrites any scene that leaks.

People, dates, durations, departments and versions that belong to the subject itself (George Boole,
the year a law was published, the duration of a pulse, an activity's duration in a schedule, a
protocol version) are content and stay. The brief, critic and repair prompts say the same.

### Chunk accounting: what the brief sets aside

Source scoping is deterministic and conservative, so a chunk can still hold nothing but packaging
(a leftover header line, a recording note, a "coming up next" paragraph). The concept brief is the
second, semantic filter. `brief.md` (v3) asks the model to account for every chunk id:

- **cited**: in the `source_refs` of a concept, of one of its `key_facts`, or of a source question
  (`GenBrief.source_questions` are `{text, source_refs}`; `ConceptBrief.source_questions` stays a
  list of strings and `ConceptBrief.source_question_refs` keeps the chunk ids). Learning objectives,
  an overview, a hook, a summary and a recap are cited from the concepts they cover;
- **skipped**: in `skipped_chunks` with a reason: `administrative`, `production`, `scaffolding`,
  `duplicate` or `off_topic`. Teaching content is never skipped; when in doubt the model cites.

`brief.brief_problems` feeds back, on the first pass: chunks neither cited nor skipped, unknown or
contradictory skips, and skipped chunks that read like teaching prose (`teaching_signal`: a formula
or an expression such as "R1 + R2 + R3", a definition — "X is the …", "X: the …", "is defined as" —,
a law, theorem, principle or rule statement, an example with numbers, or three or more explanatory
sentences), asking the model to reconsider.

`brief_from_gen` (`skipped_from_gen`) then decides deterministically, whatever the model answered on
any pass: it keeps only known ids that the brief does not also cite; it ignores, and logs, a skip of
a chunk with a definition, law, formula or worked example (the strong signals, without the prose
check); it honours a `duplicate` skip only when the chunk's text is (nearly) the same as, or
contained in, a cited chunk or one already set aside; and it ignores all skips when they would set
aside more than half of the source's text (`MAX_SKIPPED_SHARE`). An admin line with "=" is then kept:
that fails open, and source scoping and the lint leak checks still apply to it.

With a brief, the skipped chunks reach neither the planner (`Source chunks`; "About the source" says
how many were set aside, never which or why), nor the scene writers (nearby context and keyword
matches), nor the critic, repair, regeneration and the companion sheet. A translation copies the
brief into the new version's `generation_meta['brief']`, and a version without one (an older
translation, or a copy of it) falls back to the brief of the version it was translated from, so a
scene regenerated there is gated too. The eval harness hands the brief to the same stages
(`runner._brief_kwargs`). A plan that cites a skipped chunk gets a re-ask, and
`plan_rules.normalize_plan` drops the ref with a note. Without a brief nothing is gated. The preview
(`00_source_scope.md`) and the eval harness (`metrics["brief"]`) report the counts per reason only.

### Attachments

When ingest marks a PDF as scanned, maths-heavy or partly undecodable (`attach_original`), the
original file is attached to the brief, plan and scene calls, **for Gemini only**. The model can
then see whatever the file shows, so the attachment note tells it to ignore administrative and
production details. OpenAI and Claude work from the scoped text only. The preview never
attaches files.

## Flow: one continuous lecture

An SME script written as several clips becomes one lecture:

- **The brief** lists concepts in dependency order and leaves out clip boundaries, repeated
  introductions and bridges.
- **The plan** has one cold-open `title` scene, which states the objectives once. Later chapters
  open with a short `chapter_card` only. Every scene has a `narrative_role` (hook, context, concept,
  example, practice, check, application, synthesis, transition) and a one-sentence `bridge_in` from
  the scene before it. Repetition is merged, and the source's quiz questions are placed after the
  concepts they check. `plan_rules.flow_problems` asks the model to fix flow problems, and
  `normalize_plan` enforces one opening and a role for every scene.
- **Scene writers** see the outline, their neighbours and their bridge. They continue from the
  previous scene instead of re-introducing the session.
- **Repair and regeneration** keep the flow: every scene's `intent` stores its `narrative_role` and
  `bridge_in` (`canonicalize.intent_of`), and `canonicalize.planned_from_scene` restores them when the
  plan is rebuilt from the screenplay, so a regenerated scene still bridges from its neighbour.
- **Lint** reports a second opening, a second objectives scene and near-duplicate titles
  (`content.duplicate_intro`).

## Previewing prompts

```bash
.venv/Scripts/python.exe -m aadhi.pipeline.preview SOURCE [--options JSON|FILE] [--out DIR]
                                                   [--stages brief,plan,scene] [--scene N|SCENE_ID]
                                                   [--engine gemini|openai|anthropic]
```

| File | Content |
|---|---|
| `00_source_scope.md` | Source format, document metadata, removed items (category and count, values masked), the sections the concept brief set aside (count per reason), and every chunk with its visual suggestions and whether the brief cited or set it aside |
| `01_brief.md` | Concept-brief call |
| `02_plan.md` | Planning call |
| `03_scene_<id>.md` | One scene-writer call: the first teaching scene by default, or `--scene 1` for the cold open, `--scene 7`, `--scene boolean_postulates_intro` |

Each prompt file starts with a table: AI engine, model, temperature, response schema, prompt-file
versions, attachments and size. The system prompt and the user prompt follow verbatim, in fenced blocks.
Without `--out`, the files are printed to stdout. `--options` takes inline JSON or a file such as
`evals/fixtures/sample_template.options.json`. Example:

```bash
.venv/Scripts/python.exe -m aadhi.pipeline.preview evals/fixtures/sample_template.docx \
    --options evals/fixtures/sample_template.options.json --out prompt-preview
```

How it works, and what to keep in mind:

- **No real model is ever called.** The preview runs in a temporary workspace with its own
  `DATA_DIR`, SQLite database and local storage. Every provider is forced to `fake` and every API
  key is blanked through environment variables, which take precedence over `.env`, before any
  other `aadhi` module is imported. The workspace is deleted afterwards.
- **The prompts are exact.** The real stages (`ingest_source`, `build_brief`, `generate_plan`,
  `write_scene`) run against the deterministic offline LLM, wrapped in a recorder that keeps the
  first request of each call. `tests/pipeline/test_preview.py` checks the recordings against the
  pure prompt builders.
- **Carried-over content is a stand-in.** Sections that carry over an earlier answer ("Concepts to
  teach" in the plan prompt, and the outline and "This scene" in a scene prompt) come from the
  offline responders (`fake_content`). Instructions, the source and the structure are real; a real
  model's brief and plan would differ in content.
- **Model names** are those of the lecture's AI engine (`--engine`, else the options'
  `llm_provider`) resolved from your settings (`LLM_MODEL_*`, `OPENAI_MODEL_*`,
  `ANTHROPIC_MODEL_*`), so the header shows what a real run with that engine would use; without
  either it shows `LLM_MODEL_*`. The *AI engine* row names the engine. Whatever the engine, the
  calls still go to the offline LLM.

## Offline content (`LLM_PROVIDER=fake`)

`aadhi.pipeline.fake_content` answers every response model deterministically from the prompt, so
`scripts/dev_demo.py`, the tests and `python -m aadhi.evals run --provider fake` produce a coherent
lecture from the scoped source:

- concepts come from teaching headings, never from "Learning objectives", "Title card", "Bridge to
  next part", "Quick quiz" or "Summary";
- every chunk is accounted for: a lone title-page line or a metadata section is skipped as
  administrative, a title card or a bridge to the next clip as scaffolding, question sections are
  cited by their questions, and everything else by the nearest concept;
- examples, analogies and applications join their concept;
- facts come from the source's formula lines and definitions;
- one cold open uses the source's hook and states the objectives once (a compound verb such as
  "state and apply" is said once: "state and apply X and Y");
- quizzes use the source's own questions and answers, are titled after the concept the question
  checks, and prefer a wrong answer of the same kind ("Electric energy" for "Electric charge");
- every scene gets a narrative role and a bridge;
- a worked example starts with the source's setup ("Consider a node where 5 A and 3 A enter ...")
  before its first formula line;
- the summary states each concept's definition or law (a key fact or source sentence that names it),
  never its history;
- a step-by-step animation is built from a formula or derivation in the source;
- narration comes from real source sentences, with recording talk, objective statements ("We will
  learn ...") and sign-offs filtered out, and each beat cites the chunk that holds its sentence.

## Changing a prompt

1. Edit `aadhi/pipeline/prompts/<name>.md` and **bump its `<!-- PROMPT_VERSION: n -->` header**.
   - The versions used are recorded in `ProjectVersion.generation_meta["prompt_versions"]`.
   - The concept-brief cache key includes the brief prompt's version, so cached briefs are rebuilt.
   - `test_prompts_flow.py` and `test_brief.py` pin the current versions; update them in the same
     change.
   - Keep the calm, specific tone: no shouting, no threats. `test_brief.py` checks this for
     `brief.md`.
2. If you add a section or a field, change the builder (`build_brief_prompt`,
   `build_plan_prompt`, `scene_prompt_sections`) and the response model in `gen_models.py`, and
   keep the offline responder in step: `fake_content` reads the sections back with
   `prompting.extract_json`.
3. Inspect the result with the preview (above) on `evals/fixtures/sample_template.docx` and a PDF
   fixture.
4. Run the tests:

   ```bash
   .venv/Scripts/python.exe -m pytest tests/pipeline -q -p no:cacheprovider -m "not slow"
   ```

   - `test_prompts_flow.py` and `test_brief.py` check what each prompt contains and never contains.
   - `test_preview.py` checks the preview output and that it is exact.
   - `test_sme_end_to_end.py` checks an SME script end to end: no leaks, one opening, bridges,
     source quizzes.
   - `test_scope_leaks.py`, `test_scope_content.py` and `test_leak_end_to_end.py` check source
     scoping against real SME document shapes (header tables, key variants, front matter, AV
     tables, per-clip scaffolding) and against teaching content that only looks administrative.
5. Measure with the eval harness ([EVALS.md](EVALS.md)): `python -m aadhi.evals run --provider fake`
   for a free smoke run. Use `--provider configured --budget-usd ...` and `compare` against the
   previous report to see whether lectures actually improved.
