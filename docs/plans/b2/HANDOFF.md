# Session handoff — Batch B2 (Dynamic video visual system)

Updated 2026-10-16 (deadline mode), after STEP 4 landed. NEXT: the five owner decisions listed below, then the
templates still unbuilt (`myth_fact`, `aadhi_intro`, `outro_next`, `code_walkthrough`, `chart_build`). To continue,
paste the "Prompt for the next session" block below into a new session.

## Where things are (2026-10-15, end of the first account's session)

**THE DEADLINE IS OVER (2026-10-16).** The demo was given on `ac89036` and the owner has no rushed deadline any
more. Deadline mode (one review pass, at most one fix round, no skeptics or audits) was a trade for those two days,
not a standing preference: **do not keep using it by default.** Push `overhaul/v2` to origin after each landed phase,
as before.

**The process from here is matched to risk, not applied flat** (decided 2026-10-16, the owner having left the choice
to the lead; the evidence is in the review records of the last three phases):

- **Additive work behind strong automatic gates — a new template is the case — keeps the lighter shape:** parallel
  builders writing only their own files, one integrator, ONE review pass, ONE fix round. The automatic gates here are
  genuinely load-bearing (the template contract, the source scan, real preflight on every fixture, and the 1x/3x/
  reverse frame-hash gate in real Chromium), and the single review round found 5 to 7 real defects in each of the
  last two phases, every one of them fixed at the root.
- **An AUDIT round is added whenever the fix round touched a shared file** (`_shared.js`, `cues.js`, `state.js`,
  `core.js`, `templates.py`, `direct.py`, `events.py`, `presentation.py`, the schema or the catalogue) **or changed
  anything already being drawn.** Every finding that escaped a review in this batch was of that kind (the demo
  phase's D1, D5 and D7 were all shared-file; B2b-H's audits found four more defects after its first fix round, two
  of which were rated blockers). A fix to one template's own file does not need it.
- **A skeptic is used for a finding whose fix would change output that already works** — there, a wrong finding
  costs a real regression, and in B2a the skeptics refuted 4 of 16 findings. A skeptic per finding, flat, is not
  worth it for a template nobody has rendered yet.
- **Prefer a gate over a review round.** The things that actually caught the worst defects of this batch were
  automatic: the fast suite caught a migration breaking two tests, and real preflight caught the 9:16 blocker the
  moment the demo renderer was made to run it. When a review finds a whole CLASS of defect, close it with a test
  rather than with more review rounds.

- **Branch:** `overhaul/v2`. Never `main` without asking.
- **Landed on `overhaul/v2`:** B0, B1, B2-0, **B2a** (`c3a1e9b`), **B2b** fonts/icons/textfit (`d63ff94`), **B2b-H**
  HarfBuzz-shaped text fit for every script, kerning table retired (`65e6f1d`). B2b-H's two rare open edge cases
  (ZWSP-joined flow text; an over-wide word starting with a hyphen) are recorded in `b2b_h_review.md` and accepted
  for the deadline: `textfit` is not wired into rendering yet.
- **B2c (vertical slice) is MERGED** into `overhaul/v2` (`3f3899b`, merge `6ff938a`, 2026-10-15): `templates/_shared.js`,
  `cues.js`, `state.js` additions, and the templates `formula_focus`, `media_split`, `list_build` (registered in
  `stage.js` and the catalogue; `presentation.TEMPLATES` still `("board_generic",)`, so no existing timeline changes
  until `direct.py`). Record: `docs/plans/b2/b2c_review.md` (one review round; 15 fixed, 3 deferred; all gates green;
  no-visible-change differential 2,664 frames, 0 differences). **As-built notes are written:**
  `docs/plans/b2/b2c_design_notes.md` (the shared layer, the module shape and the rules every later template copies -
  a template builder reads it first) and design 10.3 B2 "Status (B2c)". On the merged tree: ruff, tsc, all
  generated-file checks and npm (1402 passed) are green; the fast suite was re-run on the merged tree in the next
  session (result recorded with the demo phase).
- **The DEMO PHASE landed (2026-10-15).** `direct.py` (which template draws each scene) and `events.py` (the scene's
  visual events) are wired into the build; six more R1 templates are registered (`definition_card`, `chapter_bumper`,
  `hook_question`, `key_terms`, `worked_steps`, `recap_retrieval`), so ten R1 templates draw; `ENGINE_VERSION` is 3,
  so stored Dynamic timelines rebuild when served. Review: 7 findings (5 major, 2 minor), all fixed at the root.
  Record: design §10.3 B2 "Status (B2 demo phase)", `final_videos/demo_b2/README.md` (the four showcase MP4s, the
  per-scene template table and why the rest still fall back), `docs/plans/b2/demo_frames/index.html` (304 reference
  frames over all nine templates, for the owner's review).
- **A GENERATED lecture is now Dynamic, and the mode is a switch in the Studio.** `compose/dynamic/authoring.py`
  writes a default `presentation` for a newly generated lecture (before it, only an edit or an import could, so a
  fresh lecture was always classic), and the look ROTATES on the lecture's seed: theme family, personality and hook
  kind, with the caption and cue styles derived from the personality (owner decision, 2026-10-15). The mode itself is
  no longer environment-only: `app_settings` + `aadhi/runtime_flags.py` + `GET/PATCH /api/admin/settings` + a
  **Video mode** switch on the Studio's Administration page. `compose.presentation.dynamic_enabled` is the single
  place the switch and `DYNAMIC_ENABLED` are merged. It governs what is generated NEXT; existing lectures keep their
  style.
- **STEP 4 LANDED (2026-10-16).** `full_bleed_media`, `animation_stage`, `check`, `predict` and the shared
  side-panel layer are built and registered; fourteen of the 23 R1 templates now draw. **The showcase lecture now draws 11 of its
  16 scenes with a B2 template in 16:9** (it was 4), 6 in 9:16. The review found a BLOCKER: the 9:16 builds could
  not be rendered at all (Dynamic preflight returned two blocking `text_overflow` findings), and the demo renderer
  had been skipping preflight entirely, so earlier portrait MP4s were rendered with overflowing text and an
  unreadable 196x110 chart stand-in. Both fixed; the renderer now runs `run_preflight` + `raise_if_blocked`.
  Record: design §10.3 B2 "Status (B2 step 4)".
- **OWNER DECISIONS WAITING (they block nothing; each is a layout question only the owner can settle):**
  1. **No chart path exists at all.** A `graph` / `chart` / `model_3d` panel is drawn as its authored TITLE on a card,
     not as a plot, so the worked example's graph is a card reading "I = V / R (straight line!)". `chart_build` is
     the template that would draw it (Chart.js is vendored and B2-0 measured it order-independent).
  2. **`mcq` in 9:16** is 16:9 only (§4.3, R-06 gives a reel two options): a reel quiz falls back today.
  3. **A side panel in 9:16** is drawn in 16:9 only: a picture or terminal card above a step stack would push the
     steps under R-29's floor and block preflight.
  4. **`s5`** carries two definitions plus a `misconception` (which is `myth_fact`'s data), so no template claims it:
     may `key_terms` carry a misconception card?
  5. **Aadhi is in no Dynamic video.** `aadhi_intro` / `aadhi_recap` are unbuilt and the classic intro is skipped for
     a Dynamic timeline, so the presenter never appears. `aadhi_intro` is what would bring him back.
- **What the demo still falls back on (the owner's re-prioritised next step, 2026-10-15).** 12 of the showcase's 16
  scenes still draw `board_generic`. The owner reordered step 4 to the templates that convert the most scenes:
  **1. `full_bleed_media`** (3 `ai_video` scenes), **2. `animation_stage`** (a `simulation`), **3. side-panel support
  in `worked_steps` / `key_terms`** (3 more scenes; both refuse a scene carrying a panel today), then **4. `check`**
  (the quiz checkpoint) and **5. `predict`**, and only then `myth_fact`, `aadhi_intro`, `outro_next`,
  `code_walkthrough`, `chart_build`.
- **Worktree hygiene:** the B2c worktree is **removed** (`C:/t/b2c` is gone; nothing is registered but the main
  checkout). For the next one, remember `C:/t/<wt>/node_modules` is a JUNCTION to the main repo's `node_modules`
  while `web/vendor` is a real copy: remove the junction with `cmd /c rmdir` BEFORE
  `git worktree remove --force` (never let a junction be deleted recursively).
- **The DEMO PHASE is integrated (2026-10-15).** `docs/plans/b2/demo_workflow.js` ran: six template builders plus
  the Director in parallel, then the integrator. What landed on `overhaul/v2`:
  - **`direct.py` + `events.py` wired in.** `compose/presentation.py resolve_looks` takes the template and variant
    from `dynamic.direct.plan`, `compose/timeline.py` puts the plan's events on each timed scene, and
    `presentation.TEMPLATES` is now the implemented R1 set. **`ENGINE_VERSION` is 3** (presentation.py and
    `web/js/dynamic/engine.js`), so a stored generation-2 Dynamic timeline is rebuilt at serve time.
  - **Six more templates registered:** chapter_bumper, definition_card, hook_question, key_terms, recap_retrieval,
    worked_steps (stage.js `TEMPLATES`, `templates.py` implemented/slots/consumes, catalogue regenerated). The
    catalogue's `SlotSpec` gained `tabular_nums`.
  - **A CG-2 parity rule extended:** a template's Python `fit` must refuse what its JS `drawsScene` refuses. The
    demo render caught `worked_steps` being planned and then drawn by board_generic; side-panel guards were added to
    `_fit_hook_question`, `_fit_worked_steps`, `_fit_key_terms` and `_fit_definition_card`.
  - **Demo MP4s** of the showcase lecture (16:9 + 9:16, one dark and one light family) in `final_videos/demo_b2/`
    with a README naming the template of every scene; reference frames for every implemented template in
    `docs/plans/b2/demo_frames/index.html`.
  - **Still board_generic in the demo:** `ai_video` (needs `full_bleed_media`), `simulation` (`animation_stage`),
    `quiz_checkpoint` (`check`), and the worked examples and content scenes that carry a side panel no built
    template draws. Those are the next step (the user's priority list: aadhi_intro, outro_next, myth_fact,
    check/predict, code_walkthrough, chart_build, then B2d kinetic captions and B2f backgrounds/transitions).
- **The workflow script that produced it:** `docs/plans/b2/demo_workflow.js` (pass
  `args: {base: <HEAD after the B2c merge>}`): direct.py + events.py wire the built templates into real lectures
  (template chosen by `TemplateSpec.fit` among implemented R1 templates; events generated from the screenplay),
  six more templates built in parallel (definition_card, chapter_bumper, hook_question, key_terms, worked_steps,
  recap_retrieval), an integrator that registers them, bumps ENGINE_VERSION, runs the gates and renders DEMO MP4s of
  the showcase lecture (dark + light family, 16:9 + 9:16) into `final_videos/demo_b2/`, then one review and one fix.
- **Session restarts kill background workflows and watchers.** Before a long run, make a WIP commit. To resume a run
  in a new session, copy its folder `~/.claude/projects/<proj>/<old-session>/subagents/workflows/<run-id>` into the
  new session's `subagents/workflows/`, add a "continue from WIP commit X" note to the interrupted agent's prompt in
  the script, and call Workflow with `resumeFromRunId`.
- **Never run:** the Docker image build, the container benchmark, the CI container job, the slow suite beyond the
  Chromium files in the gate list.

## Owner decisions

**2026-10-15, deadline:** about 2 days remain; build a WORKING DEMO (new templates in real rendered lectures) with the
lighter process described in "Where things are". Push `overhaul/v2` after each landed phase.


**2026-10-14/15, text fit (B2b-H; carried out):**
- **2026-10-14:** text fit uses HarfBuzz shaping, with three pinned Python dependencies: `uharfbuzz`, `fontTools`
  (WOFF2 decoding) and `brotli`.
- **2026-10-15:** shape every script, Latin too, if a Chromium parity probe confirms the shaped widths match what the
  page draws, and then retire the kerning table.
  - Parity held for every face, weight and script: Latin, all five Indic scripts and mixed text, within 0.054 px.
  - So all scripts are shaped and certified, and `kerning.py` / `kerning.json` and their tests are deleted.
  - B2b's Indic rule ("advisory, never store an Indic size") is **lifted**. B2d and B2e treat a certified Indic fit
    like a Latin one. B2e writes `text_fit` from certified `FitResult.stored()` only, in any script.
- **No many-minute check in CI.** The extended parity probe is opt-in (`AADHI_TEXTFIT_PARITY_FULL=1`, 5.5–7.5 minutes).

**2026-10-14:**
- **Icon matching:** exact name first (either set), then Lucide tags, then Tabler tags. This is `icons.TIERS` as built.

## Open items carried forward

From the B2b-H review (`b2b_h_review.md`; design §4.6 "As built (B2b-H)", §10.3 B2 "Status (B2b-H)"):
1. **Blocks the B2b-H commit.** The third audit found two cases where text is certified with fewer lines than the
   page draws. The audit rated both blockers. textfit is unwired, so nothing drawn is affected.
   - **ZWSP-BREAK.** Flow text with a zero-width space between two words. The join gain is counted only when both
     words share a line, but Chromium also counts it for a line that ends at the ZWSP and breaks one word earlier.
     Atkinson 700 `xx q`+ZWSP+`j xx` at 200 px: Python 2 lines, the page 3. Fix: count the positive gain at a line end
     too, and add a page line-break case to the parity suite.
   - **HYPHEN-LEAD.** An over-wide word whose piece starts with a hyphen. `_pieces` never takes the break after a
     leading hyphen (UAX #14 HY), so the page draws one more line. Inter 700 `go -abcdefgh` at 300 px: Python 3 lines,
     the title 4. Also fix the textfit docstring's claim that a break after a hyphen "only gives the page fewer lines".
2. **Linux unmeasured.** The parity probe and every Chromium measurement ran on Windows only. The container job has
   never run, and the Docker image was not built. The 0.5% slack is meant to cover Linux advance rounding, but this is
   not measured.
3. **Python notices gap (owner).** `THIRD_PARTY_NOTICES` covers npm and `web/vendor` only. `uharfbuzz` (Apache-2.0;
   HarfBuzz's "Old MIT" text is missing from its wheel), `fontTools` (MIT), `brotli` (MIT) and the image's other
   Python packages have no notices (design §4.12).
4. **Pins to watch.** `uharfbuzz` is bounded `<0.57`. Re-run the extended probe before raising it, and after a
   Playwright (Chromium) bump.
5. **Not certified, by design.** A lint must treat these as estimates:
   - characters in no vendored subset (Greek, maths symbols such as π, Ω, √; vendoring Inter's Greek and symbol subsets
     would fix this and the Windows/Debian system-font difference: owner or B2f);
   - words starting with a non-ignorable combining mark;
   - words holding bidi controls;
   - non-plain whitespace (en, em or thin spaces, the BOM, a space before a ZWSP);
   - ZWSP chains that are unbounded or longer than 24 words.
6. **Flow text is measured word by word.** That is conservative: up to 125 px on a long 148 px line, while the real
   slots are 40–56 px. B2d's chunker may want exact line shaping.
7. **Stored size of an over-wide word** can be one 4 px step below the page's, never above. For the owner or B2e:
   store only the line count for broken words?
8. **Licence texts (from B2b).** The bundled-component texts in `scripts/third_party_licences/` other than omggif,
   stacktrace.js, the Node `path` notice and libtess were never compared with their published packages. That needs a
   download, so it needs the user's permission.
9. **Housekeeping.**
   - `test_faces.py` still opens every face with Pillow, which is now only a readability check.
   - Large leftovers under `C:/t` from earlier agents (`full`, `pq`, `rv_*`, logs and others) can be deleted. Keep
     `C:/t/b2c` (the other session's worktree).

Closed since the B2b handoff:
- B2b's item 1: the Debian WOFF2 check no longer applies to text fit (HarfBuzz pins are platform-independent).
- B2b's items 2, 4 and 7: everything is shaped, and the kerning table is retired.
- The stale worktrees `b2b_vis_base` and `nvc_base` are no longer registered.

For later B2 steps, from B2b:
- **B2c:**
  - The icon helper in `templates/_shared.js`. It must sanitise again before drawing: `ICON_PATH_RE` and the caps from
    `web/schemas/timeline.schema.json`; `d` set via `setAttribute`, never `innerHTML`; the stored stroke width; round
    caps and joins; `fill="none"`; explicit display and vertical-align in `dynamic.css`.
  - Slot geometry:
    - `board_generic`'s kicker box is 30 px narrower than its slot's `w` (the pip plus the gap); `fit_slot` takes a
      `width` override meanwhile.
    - An over-long kicker is cut with an ellipsis, and `texts()` reports no overflow for it.
    - The card text slot's `w` is the card's outer width; its text box is w - 1.52 em - 6 px.
    - `.dyn-subtitle` has no `overflow-wrap`.
    - `SlotSpec` has no size step.
- **B2d:** wire `textfit` into the caption chunker and preflight. Certified fits (any script) may block; `fits=None`
  is advisory. B2b-H lifted the Indic-only rule.
- **B2e:**
  - `direct.py` fills `look.icons` with `icons.scene_icons(board_items)`.
  - It rotates the display face; until then the resolver stores B1's faces.
  - It writes `text_fit` from certified `FitResult.stored()` only: Indic included since B2b-H, never an estimate.
  - It decides whether `RefAnchor.icon` (a `CatalogueId`) names an icon that is also put in `look.icons`.
  - The live player (`web/js/player/fonts.js` `ensureFonts`) must preload `stageFontRequests` for Dynamic timelines
    once a non-B1 face is named.
  - textfit does not measure rich-lite markup or TeX in quiz questions; callers pass plain text.

From the B2a review (`b2a_review.md`):
1. (Closed) The three source-scan gaps the last audit found are fixed (`b2a_review.md`, "Closed after the
   workflow").
2. B2c: how a split 9:16 misconception's fact card is reached by `cue.term` (a part form in the §1.7 grammar or an
   `itemEl` change); pinned today by a `stage.test.js` test.
3. Catalogue shape changed inside version 1 (`captions.<style>.colours`, `duration.<fmt>.end_screen`); bump
   `CATALOGUE_VERSION` only if the owner asks.
4. `reports/b.json` still names `markReady` / `conversionsAfterReady` (now `markDeclared` /
   `conversionsAfterDeclared`, counting from before frame 0).

Owner reviews (values marked "A2 proposal, pending owner review" in the code; list in the design §10.3 B2): the 16:9
named regions, `media_split`'s variant names, the `diagram` hold list, the spacing steps, icon sizes 96/320, the Indic
heading line height, `key_terms`' 9:16 timing, the in-chapter palette-slot crossfade (interim until B2e's
`accent_fade`), roadmap states, anchor kinds, icon caps, `motion_scale` of an unreleased personality. From B2b
(design §4.12; the two lists are marked "B2b proposal, for the owner's review" in `icons.py`): `PREFERRED` (21 Lucide, 3 Tabler tag
tie-breaks), `IGNORED_TAGS` (9 Lucide, 10 Tabler), the negation rule, the English singular rules, and the refusal of
symbol-bearing and one-character hints.

For later B2 steps (from `reports/a2.json` and `reports/b.json`):
- B2c: `ctx.motion`, `ctx.outgoing`; `media_split` sizes its `mediaBox` from the fixes (`board_generic`'s ignores
  them: a clip can show up to about 3% larger than extracted when a title is capped, as in B1).
- B2d: `events.py` never emits the reserved target ids `panel` / `option`; map B1's `var:<n>` sync parts to symbols;
  R-16 holds (a look_here walk or the ring per hold).
- B2e: `direct.py` picks the class among `signals()` (incl. the two cross-class alternatives), writes `palette_slot`
  (9:16 acts / segments), `accent_fade` and `accent_prev`, `refs`, `icons`; never gives a scene under 0.3 s a colour
  block.
- B2f: the code-editor surface token (`themes.code_surface`) into the timeline; `anchor()` / the morph; presenter
  crop and cache; R-55 luminance-ratio advisories on `mesh_drift` (midnight_indigo 1.54, deep_teal 1.60);
  `studio_surface`; the W2 transition names `paper_slide` / `wipe`.
- B2g: `stage_page.py` asserts `aadhiStage.texConversions() == 0` on every gallery fixture.
- B1 gaps still open: a video figure or poster has no planned frames in render mode; the live watch page shows its
  generic error for a newer-engine timeline (a "reload" message would fit better).

## Binding docs (read in this order)

1. `docs/plans/b2/README.md` (paths ".../scratchpad/X" in the plan files mean `docs/plans/b2/X`)
2. `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 (the B2a, B2b and B2b-H status blocks and the as-built notes they name) +
   the sections the plan cites
3. `docs/plans/b2/b2_plan_v2.md` (binding: sequence §A, policy §P, contracts §1.1-1.12, rules §2, gallery §G,
   deviations §D)
4. `docs/plans/b2/b2b_h_review.md`, `b2b_review.md`, `b2a_review.md` and `a1_review.md` (what was found and
   decided)
5. `docs/plans/b2/reports/*.json` (exact contracts of A2 and B), `docs/plans/b2/b2a_workflow.js` (the step-prompt style
   to reuse for B2b..)

## Owner rules

- Work must be perfect. Use Workflow orchestration (build -> integrate -> adversarial review -> fix) for every
  substantive step, with the audit and skeptic rounds added where "Where things are" says they are worth it.
- Commit verified work in logical pieces on `overhaul/v2` only (never `main` without asking). Make a rollback
  commit before new work each session.
- Classic (Aadhi presenter) output must never change: the `tests/invariance` goldens must stay green.
- B1 timelines (engine_version 1) must keep drawing exactly as before; prove it with a frame-hash differential when the
  stage changes (`b2a_review.md` has the harness description).
- Never read `.env` or secrets. Never call paid APIs in tests (fakes only).
- No many-minute measurement in CI: hour- or many-minute re-measurements are opt-in through an environment variable
  and documented in `docs/OPERATIONS.md` (today: `AADHI_TEXTFIT_PARITY_FULL`).
- If subagents fail with "organization has disabled Claude subscription access", report it; don't retry blindly.

## Open owner decisions outside B2

- H0 Gemini TTS hotfix on `main`: `gemini-2.5-flash-preview-tts` shuts down 2026-11-17 and `main` still uses it.
- Veo 3.1 preview may shut down 2026-10-22 (an Omni adapter exists on v2).

## Gates (run all before each commit)

```
pytest -m "not slow and not network" -q --basetemp=C:/t/<label>   # ~13-18 min; run in the background
pytest -m slow tests/determinism/test_board_real_page.py tests/determinism/test_stage_layout.py \
  tests/determinism/test_live_css.py tests/compose/dynamic/test_preflight_stage.py \
  tests/determinism/test_stage_fonts.py tests/determinism/test_icon_pixels.py \
  tests/determinism/test_textfit_page.py tests/determinism/test_textfit_parity.py   # when the stage, fonts or textfit change
npm test                                   # includes web/tests/dynamic/catalogue.test.js and the source scan
npx tsc -p tsconfig.json
ruff check aadhi tests scripts evals docker
python scripts/gen_env_example.py --check
python -m aadhi.schemas.generate --check
python evals/make_fixtures.py --check
python scripts/gen_chromium_seccomp.py --check
python -m aadhi.compose.dynamic.catalogue --check     # regenerate without --check after zones/templates/themes change
python -m aadhi.compose.dynamic.faces --check         # regenerate after faces.py changes, then npm run vendor
python -m aadhi.compose.dynamic.icons --check         # needs node_modules; regenerate after an icon-set bump
python scripts/gen_third_party_notices.py --check     # needs node_modules; regenerate after any npm dependency change
node scripts/gen_colour_vectors.mjs --check
python scripts/lock_requirements.py --check           # after a requirements change
# not in CI (5.5-7.5 min, Chromium): AADHI_TEXTFIT_PARITY_FULL=1 pytest -m slow tests/determinism/test_textfit_parity.py -s
#   after a change to a vendored face, uharfbuzz/fonttools/brotli, Playwright, or textfit/shaping
```

Results on the B2b-H tree after fix round 3 (HEAD `87fa371` plus the working tree; the third audit reproduced them):
- ruff is clean, all ten generated-file checks above pass, and tsc is clean.
- `npm test`: 1252 tests, 1251 pass, 1 skipped.
- Fast suite: 5399 passed, 5 skipped, 0 failed (16-18 min).
- Slow Chromium gate: 58 passed in about 5 min. It covered `test_textfit_page`, `test_textfit_parity`,
  `test_stage_fonts`, `test_board_real_page`, `test_stage_layout`, `test_live_css` and `test_preflight_stage`.
  `test_icon_pixels` was not re-run, because the icons have not changed since B2b.
- Extended parity probe: last run after round 2, 5 passed in 456 s (3.87 M widths; Latin max 0.0522 px, Indic
  0.0324 px).

Environment notes:
- Windows uses `.venv/Scripts/...` (the global Python lacks the dependencies); Linux uses `.venv/bin/...`.
  pytest-xdist is not installed. Keep basetemp short (`C:/t/<label>`; `C:/t` exists).
- Git worktrees for differentials: `git worktree add --detach C:/t/<name> <rev>`, then link the ignored folders with
  `cmd /c mklink /J <wt>\web\vendor <repo>\web\vendor` (same for `node_modules`); remove the junctions with
  `cmd /c rmdir <junction>` BEFORE `git worktree remove --force`. A real-app run needs a real copy of `web/vendor`
  (the static mount refuses a junction that resolves outside `web/`).
- Linux setup: `python3.11 -m venv .venv && pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt boto3`.
  manimpango needs `libpango1.0-dev libcairo2-dev`. `tests/core/test_s3.py` needs boto3.
- About 10 GB free on C: — delete large temporary output.

## Prompt for the next session

> Continue the Dynamic video mode (Aadhi EduEngine), repository `C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt`,
> branch `overhaul/v2`. Read `docs/plans/b2/HANDOFF.md` first ("Where things are" and "Owner decisions"), then
> `docs/plans/b2/README.md`, `b2_plan_v2.md` §A/§1/§2, and `b2c_review.md` / `b2c_design_notes.md` if they exist.
>
> DEADLINE MODE: about 2 days remain (target ~2026-10-17). The owner chose a WORKING DEMO: real generated lectures must
> render with the new Dynamic templates. Use the lighter process: parallel build agents that write only their own
> files plus one integrator, ONE review pass, at most ONE fix round, no skeptic loops. Do not stall; push
> `overhaul/v2` to origin after each landed phase and report briefly.
>
> 1. `git fetch`; `git status`; `git log --oneline -5` on `overhaul/v2` and `git -C C:/t/b2c log --oneline -5`.
>    Make a WIP rollback commit of anything uncommitted.
> 2. If B2c is not merged yet: in the worktree `C:/t/b2c` (branch `b2c`), make sure the review fixes are complete
>    (run `npm test`, `npx tsc -p tsconfig.json`, ruff, the generated-file checks and the B2c slow tests
>    `tests/determinism/test_template_pages.py test_template_fixtures.py test_template_clips.py`), fix what fails,
>    write `docs/plans/b2/b2c_review.md` and `b2c_design_notes.md` if missing, commit "B2c: ...", then in the main repo
>    `git merge b2c` into `overhaul/v2` (resolve conflicts; the main tree has the B2b-H HarfBuzz commit `65e6f1d`),
>    paste the B2c design notes into `docs/DYNAMIC_VIDEO_DESIGN.md`, run the full gates on the merged tree (fast suite
>    in the background, npm test, tsc, ruff, generated-file checks, the slow template/stage tests), commit, push. Remove
>    the worktree (first `cmd /c rmdir C:\t\b2c\node_modules`, then `git worktree remove --force C:/t/b2c`).
> 3. Launch the demo phase: Workflow with `scriptPath: docs/plans/b2/demo_workflow.js`,
>    `args: {base: "<HEAD after step 2>"}`. When it returns: check the demo MP4s in `final_videos/demo_b2/` (extract a
>    few stills with ffmpeg and look at them), fix any blocker yourself, run the gates, commit, push, report.
> 4. Then, as time allows, in priority order: the remaining R1 templates the demo needs (aadhi_intro, outro_next,
>    myth_fact, check/predict, code_walkthrough, chart_build), kinetic captions (B2d), backgrounds/transitions (B2f).
>    Keep classic (Aadhi presenter) output unchanged (tests/invariance) and every frame deterministic.
>
> Environment: use `.venv/Scripts/python.exe` (pytest-xdist is not installed; `--basetemp=C:/t/<label>`); never read
> `.env`; never call paid APIs in tests; no many-minute checks in CI.
