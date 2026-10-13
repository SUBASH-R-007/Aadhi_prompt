# B2a adversarial review (2026-10-14)

Review of Batch B2a ("contracts and foundations") as committed: `git diff cb81a42 6de3ddf` (commits `56ed3e9` A1
review fixes, `6489b2d` A2, `ad7a07c` A2 reports, `6de3ddf` B). Binding plan: `b2_plan_v2.md`; step prompts:
`b2a_workflow.js`; the A1 review and the owner's block-ink decision: `a1_review.md`; the implementers' reports:
`reports/a2.json`, `reports/b.json`.

Method: four independent lenses (the plan, Python, JavaScript, and a real-Chromium frame differential); one skeptic
per finding, asked to refute it or correct its severity (two skeptics for the first finding); a fix round on what
survived; an audit of the fixes; a second fix round on the audit's findings; a second audit.

The owner decisions were not findings: the stored `block.ink` is the stage's own `inkOn` choice; the stored
`block.ink2` is hold-safe (it differs from the B1 stage in 7 palette slots: midnight_indigo 0/2, deep_teal 0,
forest_night 1, cosmic 2/4, bold_blocks 0); paper_ink slot 2's block is R-54 red-lowered; and the B1 chapter-detection
mismatch (`reads/compose-classic.md` bug 4) is fixed on purpose.

## Gates at review time (HEAD 6de3ddf, clean tree)

- ruff clean; `npx tsc -p tsconfig.json` clean.
- Generated-file checks all pass: `aadhi.compose.dynamic.catalogue --check`, `aadhi.schemas.generate --check`,
  `gen_colour_vectors.mjs --check`, `gen_env_example --check`, `make_fixtures --check`, `gen_chromium_seccomp --check`.
- `npm test`: 1231 tests, 1230 pass, 1 skipped.
- Fast Python suite: 4872 passed, 5 skipped, 1 failed. The failure was
  `tests/pipeline/test_quality.py::test_many_term_groups_and_undefined_abbreviations_stay_fast`, a 3.0 s wall-clock
  budget measured at 3.5-3.7 s while other reviewers loaded the machine; it is outside the B2a diff and passes alone
  (finding `fast-suite-timing-flake`).
- Slow Chromium tests (`test_board_real_page.py`, `test_stage_layout.py`, `test_live_css.py`,
  `test_preflight_stage.py`): 28 of 28.

## What each lens verified

**Plan.** Every part plan §1.1 lists is in the catalogue (zones with core.js's exact `ZONES`, every §2.5 region, the
grid, the end-screen half, the platform zones with `UNIVERSAL` computed as their intersection, R-29 floors per format,
the §4.6 type scale with the Indic minimums, layout tokens, caption metrics, cue colours, the template, background,
transition and personality registries, the class table, targets, roles, scrims, `studio_surface` null); it is
byte-deterministic, deep-frozen and checked in CI. The 41 `TemplateSpec`s match §4.3/§4.4 row by row (releases 23/17/1,
classes, formats, forced types, timings, variants, `dense`, `push_ok`, code limits, signal rows). The schema additions
of §1.2/§1.3 are present and left out at their defaults; the engine relabel, the serve rebuild and the three refusals
are as planned. The JS contract (§1.4, §1.9), the fixes channel (deviation 2), R-54's crossfade clamp, the chunk-key
background clock with `CHUNK_CACHE_VERSION` 3, classic isolation with its 300 ms budget, the TeX counter and the scan
rules are done. Deviation register items 1, 2, 4, 5, 21 and 22 are implemented as described.

**Python.** The catalogue renders to the same SHA-256 under three `PYTHONHASHSEED`s and equals the committed file
(LF forced by `.gitattributes`). A probe of 80,000 cases found 0 mismatches between `colour_math` and
`color.js`/`core.js` (mixes, desaturation, compositing with alpha, contrast, luminance, OKLCH conversions, near-greys
at the achromatic threshold, hues -180..540, translucent and 8-digit hex, p outside 0..1). Stored block tokens equal
the JS stage's derivation in every R1 slot 0-5 except exactly the owner-decision slots. A regression of 144 resolver
configurations at cb81a42 and at HEAD (9 family ids including W2 and unknown ones, both formats, crisp and soft,
chapter layouts, teacher cut/crossfade and background requests, holds) gave 0 differences outside the B2a additions.
Chapter detection, `crossfade_seconds`, `continues_from`, `palette_slot`, `motion_scale`, schema exclusion, the fixes
channel end to end (including the remedied `served` dict that is hashed into chunk keys, and the refresher), the serve
rebuild (304 first; the render job always builds fresh), `background_clock` and the classic isolation tests are
correct.

**JS.** A jsdom differential of cb81a42's `web/js` (git archive) against HEAD over 32 B1-shaped timelines (the
fixture, legacy remedies, TeX in formula, rich text and quiz, a video panel and a figure, all 12 R1 palette slots as
5-token looks with a title scrim), render mode, every 3rd frame, both formats: 12,800 frames, 0 differences in frame
key, `stage.el.outerHTML` or `mediaPlan(0, frames)`; a live-mode comparison of the media timeline gave 218 samples, 0
differences. Engine-2 looks with stored tokens differ from B1 only for deep_teal/0, midnight_indigo/0, midnight_indigo/2
and paper_ink/2, frames 300-432 (the colour-block chapter card and the crossfade out of it), both formats: exactly the
owner decision. Code reads of `makeRoles`, `fixesOf` and the stub stage, `mediaBoxOf`, the template hooks, TeX
pre-conversion, the engine refusal, determinism and the classic render network policy found no defect beyond the
findings below. All new tests can fail.

**Differential (real Chromium).** Detached worktrees of cb81a42 (B1) and 6de3ddf (B2a) ran the real app
(`create_app` under uvicorn, `DYNAMIC_ENABLED=true`) and the real `/render-dynamic` page, hashing CDP JPEG q92 frames
(`determinism.hash_frames`); a control (the same stage and timeline twice) gave 366/366 identical frames; page errors
and timer-guard trips were 0.

| Comparison | Timelines | Result |
|---|---|---|
| (a) the same engine-1 JSON on the B1 stage and the B2a stage | QA fixture dark and paper_ink, both formats (366 frames each); the showcase in midnight_indigo, deep_teal, paper_ink and mint_studio, both formats, plus a variant without chapters (256 sampled frames each) | 3,768 of 3,768 identical |
| (b) B2a-built engine-2 JSON on the B2a stage against (a) on the B1 stage | QA fixture (synthetic, engine 1) | 4 × 366 identical |
| | deep_teal, mint_studio (both formats) | 256 / 256 identical |
| | midnight_indigo (both formats) | 32 of 256 differ: scene 9 (chapter card, slot 2) and the crossfade out of it; only the kicker/subtitle box, max delta 21; owner decision ink2 `#F4E4E5` → `#F8EDEE` |
| | paper_ink (both formats) | 33 of 256 differ: the same scene, the whole block (centre pixel (193,68,38) → (173,86,66)); owner decision, R-54 block `#C44323` → `#B05641` |
| | no-chapter variant (midnight_indigo, 16:9) | 217 of 256 differ from the crossfade into s4 on: the accent changes at s4 and s10; the chapter-detection fix |
| Extra 1: all scenes on colour blocks, slots 0-7, served unchanged to both stages (418 frames each) | mint_studio, deep_teal, midnight_indigo, paper_ink | differences only in the owner-decision residues (deep_teal 0; midnight_indigo 0 and 2; paper_ink 2) and the crossfades out of them |
| Extra 2: preflight remedies on scenes 1, 3 and 9 | midnight showcase, both formats | B1's legacy channel: 256/256 identical on both stages (a control shows 82 remedied frames differ from an unremedied run); the fixes channel on engine 2 equals B1's legacy rendering except the 33 owner-decision frames |

The JSON of a B2a-built showcase differs from the B1 build in 86 fields per timeline, all additions (five block and
scrim tokens per scene; `content_alt`, `family`, `mode`, `motion_scale`, `entity_colors`) plus `engine_version` 1 → 2;
no anchor token, tone, seed or transition changed, and no scene is short enough to hit the crossfade clamp. The Python
lens separately compared every 3rd frame of B1/engine-1 timelines (the QA fixture and resolver-built 10-scene
lectures in midnight_indigo, deep_teal and paper_ink, both formats) on the base and the HEAD stage in real Chromium:
0 of 2,204 frames differ.

## Findings

16 findings: plan 9, Python 3, JS 4, differential 0. Severities are the reviewer's, then each skeptic's.

| Finding | Lens | Reported | Skeptic verdict | Outcome |
|---|---|---|---|---|
| `js-class-table-test-missing`: plan §1.8 asks for a JS test of the 19-class table; only the Python one existed | plan | major | not refuted; one skeptic minor (the data is pinned transitively by the catalogue check and the node load test), one major (a plan requirement is missing) | fixed: `catalogue.test.js` holds a literal copy of §4.2's table and checks ids, members, claims, 41 templates, and that each template is in exactly its own class (a moved member fails it) |
| `owner-review-values-unflagged`: values the A2 report lists for owner review looked like design values in the code | plan | minor | minor (the 8 px unit itself is the plan's; only the steps are A2's) | fixed: each marked in place "A2 proposal, pending owner review; not in the design" (media_split variant names, the `diagram` hold list, the spacing steps, icon sizes, Indic heading line height, key_terms' 9:16 reading); `resolve_looks` calls the in-chapter palette-slot crossfade interim until B2e's `accent_fade`; the catalogue header lists them |
| `geometry-only-guard-incomplete`: caption colours sat inside `captions.*.metrics`, and the scan was a short deny-list | plan | minor | minor (nothing leaks today) | fixed: colours moved to `captions.<style>.colours`; the scan follows every `CATALOGUE` member path against an allow-list (`CATALOGUE_RUNTIME`) and refuses aliases, value uses, computed keys, re-exports and dynamic imports; round 2 narrowed `end_screen` to its `box` |
| `outro-duration-range`: `outro_next` stored 6-15 s where §4.3 allows 6-10 s or 15 s | plan | minor | minor | fixed: `Duration.end_screen`; `outro_next` 16:9 is (6, 10, end_screen 15); invalid combinations raise; `key_terms`' 9:16 reading commented |
| `var-target-doc-contradiction`: the grammar comment called `var:<n>` an index example | plan | minor | minor | fixed: the comment states `<symbol>` is never an index and how B1's `var:<n>` maps (B2d); `f1:var:0` pinned as symbol `0` |
| `stale-init-docstring-dual-channel` | Python | minor | minor | fixed: `__init__.py` says preflight writes `look.fixes` only |
| `themes-docstrings-point-at-moved-js` | Python | minor | minor (`colour_math.py` needed no change) | fixed: `themes.py` points at `core.js` `makeRoles` / `blockInk2` |
| `fast-suite-timing-flake`: a 3.0 s wall-clock budget fails under load (outside the diff) | Python | minor | minor (CPU time alone would not fix it) | fixed: `_fastest` best-of-3 in `tests/pipeline/test_quality.py`, budgets unchanged (a machine saturated for the whole test still fails it) |
| `tex-counter-blind-to-first-frame`: the counter started after `seek(0)`, so a template's undeclared first-frame TeX was never counted, and richtext's own cache bypassed it | JS | minor | minor | fixed: `markDeclared()` before the stage is built (renamed from `markReady`), one-scene tests; new scan rule `tex`; round 2 closed aliases, renamed destructures, dynamic imports, `renderTex: null` and direct MathJax |
| `itemel-split-misconception`: in 9:16 no target reaches a misconception's fact card | JS | minor | minor (unreachable in B2a) | **deferred to B2c**: documented in `board_generic` `itemEl`, pinned in both formats by a `stage.test.js` test |
| `scan-template-style-gaps`: `setAttributeNS`, the Typed OM and attributes in markup were not scanned | JS | minor | minor | fixed: all three, plus `toggleAttribute`, `setAttributeNode`, unreadable markup sinks; round 2 matched names case-insensitively too |
| `stale-rendertex-docs` | JS | minor | minor | fixed: `stage.js` and `types.js` describe the render page's counted converter |
| `mystery-variant-deferred-but-listed` | plan | minor | **refuted**: not implemented, so nothing can choose it; recorded as an open item already | none (noted in the design's §4.3 as-built) |
| `consumes-vs-holds-inconsistent` | plan | minor | **refuted**: R-16 belongs to `events.py` (B2d); `consumes` lists what the current stage draws | none |
| `presenter-transition-shape-stale` | plan | minor | **refuted**: the comments match today's producer and consumer; the new shapes are B2f's | none |
| `template-contract-hooks-optional` | plan | minor | **refuted**: the plan sets no required hooks, every optional hook the stage calls is guarded, the contract is recorded with its `?`s | none |

## Fix rounds and audits

**Round 1** fixed 11 findings and deferred one (above), touching 19 files (Python modules and their tests, the
generated catalogue, `core.js`, `render.js`, `stage.js`, `board_generic.js`, `types.js` and four JS test files). The
audit confirmed each fix (with a test that fails without it) and the deferral, and found three problems:
- markup attribute names in upper or mixed case (`FILTER=`) were missed (the name went through the camelCase reading);
- `end_screen` was allowed whole, so `CATALOGUE.end_screen.landscape.seconds`, a duration, passed the scan;
- the `tex` rule missed an alias (`const r = renderRich`), a renamed destructure of a dynamic import, and
  `renderTex: null`.

**Round 2** fixed all three in `web/tests/dynamic/sourceScan.test.js` only (names read both as camelCase keys and
lower-cased, at every sink; `['end_screen', '*', 'box']`; helpers only ever called, dynamic imports of `richtext.js` /
`libs.js` refused, the converter option must be the context's and the last word on it); each fix was reverted once to
show its test fails. The audit confirmed them and found three more gaps, **closed by the lead after the workflow**
(see "Closed after the workflow" below):
1. `MARKUP_ATTR` needs whitespace before an attribute name, but the HTML tokenizer also starts one after a `/` in the
   tag or straight after a quoted value: `<rect/filter="url(#a)"/>` and `<rect x="0"filter="url(#a)"/>` apply the
   filter with no finding. Suggested: lead with `[\s/"']` and add must-catch cases.
2. `isContextConverter` accepts `.renderTex` read off any object (`opts.renderTex`, `this.renderTex`,
   `window.renderTex`), which can be undefined at run time and fall back to richtext's uncounted MathJax cache; the
   scan's header says "`ctx.renderTex` (or a `renderTex` destructured from the context)". Suggested: require the
   template's context identifier, or list the gap among the limits.
3. A TeX helper re-exported under another name (`export { renderRich as rr } from '../../player/richtext.js'`) and
   called from another template escapes the rule (only import declarations are checked for renames). Suggested:
   refuse such an export specifier in templates.

The audit's text rates each of the three minor; the workflow's summary carries them as unresolved with severity
"major". No code reaches any of them today (`board_generic` passes `ctx.renderTex` at all four calls and writes no
markup attributes).

### Closed after the workflow (lead, 2026-10-14)

All three are fixed in `web/tests/dynamic/sourceScan.test.js`, each with must-catch cases, and each fix was reverted
once in a scratch copy to show a test fails without it:
1. `MARKUP_ATTR` leads with `[\s/"']`, so an attribute after `/` or straight after a quoted value is read
   (`<text/text-decoration=…>`, `<text x="0"text-decoration=…>`).
2. `isContextConverter` requires the object to be the template's context, `ctx` (`ctx.renderTex`,
   `ctx['renderTex']`) or the bare destructured name `renderTex`; `opts.renderTex`, `this.renderTex`,
   `window.renderTex` and `({}).renderTex` are refused. A context not named `ctx` is listed among the limits.
3. An export specifier in a template that re-exports a TeX helper under another name (with or without a module) is
   refused.

`sourceScan.test.js` 34/34; `npm test`, `tsc` and ruff pass after the change.

## Gates after the fixes (working tree: HEAD 6de3ddf plus the uncommitted fixes in 19 files)

- ruff: all checks passed. The six generated-file checks pass (the catalogue was regenerated in round 1).
- `npx tsc -p tsconfig.json`: exit 0.
- `npm test`: 1236 tests, 1235 pass, 0 fail, 1 skipped (`sourceScan.test.js` alone 34/34).
- Fast Python suite (`-m "not slow and not network"`): 4874 passed, 5 skipped (platform-only), 264 deselected, 0
  failed (598 s).
- Slow Chromium tests (the four files above): 28 passed.
- Every modified file is LF; `git diff --check` is clean; no worktree or temporary output left.

The fixes of both rounds and the three closed gaps are committed after this review (see HANDOFF.md).

## Open after the review

- The three scan gaps of round 2 (above).
- B2c: how a split 9:16 misconception's fact card is reached (a part form in the §1.7 grammar or an `itemEl` change).
- Catalogue shape changed inside version 1 (`captions.<style>.colours` split from `metrics`;
  `templates.*.duration.<fmt>.end_screen`), with `CATALOGUE_VERSION` left at 1 because v1 was never released or
  stored; a bump is a one-line change plus the two version pins if the owner wants one.
- `render.js` `pageTexRenderer`: `markReady` / `conversionsAfterReady` are now `markDeclared` /
  `conversionsAfterDeclared`; `reports/b.json` still uses the old names and the narrower meaning (it is kept as the
  implementer wrote it).
- The `test_quality` budgets: best-of-3 removes flakes from load spikes, not from a machine saturated for the whole
  test (6.5-7 s against 3 s, before and after).
- The owner's review of the "A2 proposal" values (design §10.3 B2 lists them).
