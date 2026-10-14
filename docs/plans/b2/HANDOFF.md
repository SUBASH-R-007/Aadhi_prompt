# Session handoff — Batch B2 (Dynamic video visual system)

Updated 2026-10-15, after the B2b review and docs pass. Paste the "Prompt for the next session" block below into a new
session to continue.

## Where things are

- Branch: `overhaul/v2` (local; it contains the cloud session's branch `origin/overhaul/v2-el9z66` and is 7 commits
  ahead of `origin/overhaul/v2` by 11 commits at 0e14281, not pushed). Never `main` without asking.
- Commits since B1:
  - 3226e20 B1 complete
  - 54c72c5 rollback point
  - 7687c4e WIP B2a A1 (colour_math.py, themes.py + validator, colour vectors; B2 plan docs)
  - 0031984 test_tooling platform-neutral fix
  - 52094f1 A1 review notes
  - cb81a42 this handoff (first version)
  - 56ed3e9 B2a A1: the seven review findings fixed; hybrid block-ink rule (owner decision)
  - 6489b2d B2a A2: zones, template registry, generated catalogue, schema, fixes channel, resolver tokens
  - ad7a07c B2a A2: the agents' reports (`reports/a2.json`, `reports/b.json`)
  - 6de3ddf B2a B: the stage reads the catalogue, stored tokens and the fixes channel
  - fe6bc2f B2a review fixes (both rounds, plus the three source-scan gaps the last audit found, closed by the lead)
  - c3a1e9b B2a docs pass (`docs/DYNAMIC_VIDEO_DESIGN.md`, `OPERATIONS.md`, `INTERFACES.md`, `API.md`,
    `ARCHITECTURE.md`, `docs/plans/b2/`)
  - d93226d WIP B2b: fonts (face table, vendored faces, notices), icons (Lucide + Tabler, offline index), `textfit.py`
  - 0e14281 WIP B2b review fix round 1 (partial: its fixer was cut off by a network error; a second fixer reviewed it
    and completed the round in the working tree)
- **Uncommitted in the working tree** (as of this handoff):
  - the rest of round 1 and all of round 2 of the B2b review fixes: `kerning.py` / `kerning.json` version 3,
    `textfit.py`, `gen_third_party_notices.py`, `THIRD_PARTY_NOTICES`, their tests, and the libtess notice
    `scripts/third_party_licences/libtess-p5.txt` (untracked), which replaces the deleted `libtess-1.2.2.txt`;
  - this docs pass: `docs/DYNAMIC_VIDEO_DESIGN.md`, `OPERATIONS.md`, `INTERFACES.md`, `docs/plans/b2/README.md`,
    `HANDOFF.md` and `b2b_review.md` (untracked).

  All gates pass on this tree (below). Commit it next, as one B2b commit or as the fixes and the docs separately.
- Done: B0, B1, B2-0 (transform policy, plan §P), **B2a** (A1, A2, B, the review), and **B2b** (fonts with the face
  table, icons, `textfit.py`, `THIRD_PARTY_NOTICES`, the review; not yet committed as final). What each built and every
  deviation it realised: `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 "Status (B2a)" and "Status (B2b)" and the as-built
  notes they name. The reviews: `b2a_review.md`, `b2b_review.md`.
- Not done: the HarfBuzz text-fit step (owner decision, next), B2c .. B2g, and the open items below. Never run: the
  slow suite beyond the Chromium files in the gate list, the Docker image build, the container benchmark and the CI
  container job.

## Owner decisions (2026-10-14, recorded in B2b)

- **Indic text fit uses HarfBuzz shaping** (`uharfbuzz` + `fontTools` + `brotli`: three new Python dependencies), done
  as the step after B2b.
  - Until then `textfit` gives Indic text `fits=None`, an estimate. B2d's caption chunker and B2e's lints must treat
    Indic fits as advisory, never blocking.
  - B2e must never store an Indic size in `text_fit`, because the stage reads it as a size cap.
  - The same step may replace the Latin kerning table (`kerning.json`) with shaped measurement. That is a proposal,
    not a decision.
- **Icon matching:** exact name first (either set), then Lucide tags, then Tabler tags. This is `icons.TIERS` as built.

## Open items carried forward

From the B2b review (`b2b_review.md`; design §10.3 B2 "Status (B2b)"):
1. **Debian WOFF2 check.** Pillow reads every vendored WOFF2 on Windows (Pillow 12.3.0, FreeType 2.14.3).
   - The pinned advances in `tests/compose/dynamic/test_textfit.py` run in the container's fast suite, which has never
     run. If they fail, pin per platform, or have `vendor.mjs` store TTF copies for measurement.
   - The slow probes (`test_textfit_page`, `test_kerning_table`, `test_icon_pixels`) have not run on Linux either, and
     `kerning.json` holds Windows Chromium measurements.
2. **Not certified until HarfBuzz:** Indic text, combining sequences, characters no vendored face draws, and Latin
   runs the kerning table does not bound (Inter: `step-by-step`, `f(x)`, `[1-3]`, `(a+b)`, `10x10`; Outfit: `ffiw`).
3. **Stored size of an over-wide word** can be one 4 px step below the page's, never above. For the owner or B2e:
   should only the line count be stored for broken words?
4. **Glyphs wider than Pillow's advance** outside the slot weights: Lexend 900 `″` +5.7%, Lexend 700 `″` +3.8%, Inter
   latin-ext 700 `Ƀ` +3.2%. textfit does not measure weight 900 today.
5. **Uncovered characters.** Greek and maths symbols (π, Ω, √, →) are in no vendored subset, so Chromium draws them
   with a system font that differs between Windows and Debian, a determinism risk carried over from B1. Vendoring
   Inter's Greek and symbol subsets would fix both (owner or B2f).
6. **Licence texts.** Those in `scripts/third_party_licences/` other than omggif, stacktrace.js, the Node `path`
   notice and libtess were read, but never compared with their published packages. That comparison needs a download,
   so it needs the user's permission.
7. **Kerning table cost.** 2.88 MB committed, and regeneration takes 85-90 min in Chromium. Its `--check` is not in
   CI; the slow `test_kerning_table.py` re-measures instead.
8. **Housekeeping.**
   - Stale worktrees `C:/t/b2b_vis_base` and `C:/t/nvc_base` are still registered with git. `b2b_vis_base` has a
     `node_modules` junction into the repo, so run `cmd /c rmdir` on the junctions before `git worktree remove --force`.
   - Large leftovers under `C:/t` (`rv_fast` about 239 MB, `pq` about 129 MB, and others) can be deleted.

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
- **B2d:** wire `textfit` into the caption chunker and preflight, with Indic fits advisory.
- **B2e:**
  - `direct.py` fills `look.icons` with `icons.scene_icons(board_items)`.
  - It rotates the display face; until then the resolver stores B1's faces.
  - It writes `text_fit` from certified `FitResult.stored()` only.
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
2. `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 (the B2a and B2b status blocks and the as-built notes they name) + the
   sections the plan cites
3. `docs/plans/b2/b2_plan_v2.md` (binding: sequence §A, policy §P, contracts §1.1-1.12, rules §2, gallery §G,
   deviations §D)
4. `docs/plans/b2/b2b_review.md`, `docs/plans/b2/b2a_review.md` and `docs/plans/b2/a1_review.md` (what was found and
   decided)
5. `docs/plans/b2/reports/*.json` (exact contracts of A2 and B), `docs/plans/b2/b2a_workflow.js` (the step-prompt style
   to reuse for B2b..)

## Owner rules

- Work must be perfect. Use Workflow orchestration (build -> integrate -> adversarial review -> one skeptic per
  finding -> fix -> audit) for every substantive step.
- Commit verified work in logical pieces on `overhaul/v2` only (never `main` without asking). Make a rollback
  commit before new work each session.
- Classic (Aadhi presenter) output must never change: the `tests/invariance` goldens must stay green.
- B1 timelines (engine_version 1) must keep drawing exactly as before; prove it with a frame-hash differential when the
  stage changes (`b2a_review.md` has the harness description).
- Never read `.env` or secrets. Never call paid APIs in tests (fakes only).
- If subagents fail with "organization has disabled Claude subscription access", report it; don't retry blindly.

## Open owner decisions outside B2

- H0 Gemini TTS hotfix on `main`: `gemini-2.5-flash-preview-tts` shuts down 2026-11-17 and `main` still uses it.
- Veo 3.1 preview may shut down 2026-10-22 (an Omni adapter exists on v2).

## Gates (run all before each commit)

```
pytest -m "not slow and not network" -q --basetemp=C:/t/<label>   # ~10-12 min; run in the background
pytest -m slow tests/determinism/test_board_real_page.py tests/determinism/test_stage_layout.py \
  tests/determinism/test_live_css.py tests/compose/dynamic/test_preflight_stage.py \
  tests/determinism/test_stage_fonts.py tests/determinism/test_icon_pixels.py \
  tests/determinism/test_textfit_page.py tests/determinism/test_kerning_table.py   # when the stage, fonts or textfit change
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
# not in CI (85-90 min, Chromium): python -m aadhi.compose.dynamic.kerning [--check], after a vendored Latin face changes
```

Results on the B2b tree with both review rounds (HEAD 0e14281 plus the working tree):
- ruff clean; all nine generated-file checks pass; tsc clean.
- `npm test`: 1252 tests, 1251 pass, 1 skipped.
- Fast suite: 5289 passed, 5 skipped, 0 failed.
- The eight slow Chromium files: 56 passed.
- The kerning table was regenerated from the final code. Its own `--check` was not re-run; the second audit
  re-measured five faces and matched the committed rows byte for byte.

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

> Continue Batch B2 of the Dynamic video mode. Read docs/plans/b2/HANDOFF.md first, then the binding docs it lists.
> Use Workflow orchestration for every substantive step. Start by checking `git status`: if the B2b review fixes and
> docs are still uncommitted, run the gates and commit them (B2b); that commit is also the rollback point. Otherwise
> make a rollback commit. Then:
> 1. The HarfBuzz text-fit step (owner decision 2026-10-14: `uharfbuzz` + `fontTools` + `brotli`). Measure Indic text
>    shaped, so Indic fits can be certified, and decide whether shaped measurement replaces the Latin kerning table.
>    It gets its own review.
> 2. B2c, the vertical slice (`_shared.js` with the icon helper, `state.js`, stage-driven `cues.js`, `formula_focus`,
>    `media_split`, `list_build`), exactly as plan §A step 3 says.
> 3. B2d..B2g per the plan.
>
> Each step gets its adversarial review and, when the stage changes, a frame-hash differential proving existing
> timelines unchanged. Put the owner-review lists in front of the owner (the "A2 proposal" values and the B2b icon
> lists); they do not block the next steps.
