# Session handoff — Batch B2 (Dynamic video visual system)

Updated 2026-10-14, after the B2a review and docs pass. Paste the "Prompt for the next session" block below into a new
session to continue.

## Where things are

- Branch: `overhaul/v2` (local; it contains the cloud session's branch `origin/overhaul/v2-el9z66` and is 7 commits
  ahead of `origin/overhaul/v2`, not pushed). Never `main` without asking.
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
  - B2a review fixes (both rounds, plus the three source-scan gaps the last audit found, closed by the lead)
  - B2a docs pass (`docs/DYNAMIC_VIDEO_DESIGN.md`, `OPERATIONS.md`, `INTERFACES.md`, `API.md`, `ARCHITECTURE.md`,
    `docs/plans/b2/`)
- Done: B0, B1, B2-0 (transform policy, plan §P), **B2a** (A1, A2, B, the review). What B2a built and every deviation
  it realised: `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 "Status (B2a)" and the as-built notes it names. The review:
  `b2a_review.md`.
- Not done: B2b .. B2g; the open items below; the slow suite beyond the touched Chromium tests, the Docker image /
  container benchmark and the CI container job (never run).

## Open items carried forward

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
`accent_fade`), roadmap states, anchor kinds, icon caps, `motion_scale` of an unreleased personality.

For later B2 steps (from `reports/a2.json` and `reports/b.json`):
- B2b: `presentation.fonts.indic`; icons and `textfit.py`.
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
2. `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 (status and the as-built notes it names) + the sections the plan cites
3. `docs/plans/b2/b2_plan_v2.md` (binding: sequence §A, policy §P, contracts §1.1-1.12, rules §2, gallery §G,
   deviations §D)
4. `docs/plans/b2/b2a_review.md` and `docs/plans/b2/a1_review.md` (what was found and decided)
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
  tests/determinism/test_live_css.py tests/compose/dynamic/test_preflight_stage.py   # when the stage changes
npm test                                   # includes web/tests/dynamic/catalogue.test.js and the source scan
npx tsc -p tsconfig.json
ruff check aadhi tests scripts evals docker
python scripts/gen_env_example.py --check
python -m aadhi.schemas.generate --check
python evals/make_fixtures.py --check
python scripts/gen_chromium_seccomp.py --check
python -m aadhi.compose.dynamic.catalogue --check     # regenerate without --check after zones/templates/themes change
node scripts/gen_colour_vectors.mjs --check
```

Results on the B2a tree with the review fixes: ruff clean; all six generated-file checks pass; tsc clean; `npm test`
1236 tests, 1235 pass, 1 skipped; fast suite 4874 passed, 5 skipped, 0 failed; the four slow Chromium files 28 passed.

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
> Use Workflow orchestration for every substantive step. Start with a rollback commit (check `git status`; run the
> gates first). Then: list the "A2 proposal" values for the owner's review (they do not block B2b) → B2b (fonts with the face table and fonts.css, icons, textfit.py, presentation.fonts)
> exactly as plan §A step 2 and §2 "Fonts" say → B2c..B2g per the plan, each with its adversarial review and, when the
> stage changes, a frame-hash differential proving engine-1 timelines unchanged.
