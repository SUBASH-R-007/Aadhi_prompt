# B2b adversarial review (2026-10-14 .. 2026-10-15)

Review of Batch B2b ("assets": fonts with the face table, icons, `textfit.py`; plan `b2_plan_v2.md` §A step 2, §1.2,
§1.3, §1.11, §2 "Fonts", §D) as built in the commit `d93226d` ("WIP B2b"), i.e. `git diff c3a1e9b d93226d`. The
review's fixes are in `0e14281` ("WIP B2b review fix round 1", partial) and in the working tree after it. Binding
design sections: `docs/DYNAMIC_VIDEO_DESIGN.md` §4.6, §4.12, §6.7, §7.3, §10.3 B2, R-29.

B2b was built by three agents, one per part. Their contracts, licence findings, tests and open items are summarised in
the design's as-built notes (§4.6, §4.12, §6.7, §7.3, §7.4, §7.13, §10.3 B2 "Status (B2b)") and in
`docs/INTERFACES.md`:
- **Fonts:** the face table `faces.py`, the generated `dynamic-fonts.css` / `faces.gen.js`, `vendor_faces.mjs`,
  `THIRD_PARTY_NOTICES`, `TimelineFonts`, and `fonts.js` with `stageFontRequests`.
- **Icons:** the Lucide and Tabler pins, `icons.py`, and the committed `icon_index.json`.
- **Text fit:** `textfit.py`.

Before the review, each agent showed that its new tests can fail, using temporary mutations in scratch worktrees:
- **Fonts:** 22 mutations, plus one in real Chromium, all killed.
- **Icons:** 50, all killed. Three survived the first pass and exposed real test gaps, which were then fixed.
- **Text fit:** 40 killed. One survivor is equivalent (measuring per cluster instead of per run: no vendored face has a
  legacy `kern` table, which a test pins).

**Method.** Four independent lenses: the plan, licences, code, and a real-Chromium frame differential. Each finding
got one skeptic, or two for the most severe, asked to refute it or correct its severity. Then a fix round on what
survived, an audit of the fixes, a second fix round on the audit's findings, and a second audit.

**Not findings.** These are owner decisions or documented deviations:
- **Indic text** is not certified (`fits` None).
- **The 0.06 em per-word kerning floor.**
- **The icon tier order.** A Tabler name beats a Lucide tag; this became the owner's decision on 2026-10-14.
- **`PREFERRED` and `IGNORED_TAGS`.**
- **Excluded or deferred assets:** filled icons are excluded; Phosphor and Anek's axis rotation are left to W2.
- **The committed icon index**, used instead of icon files under `web/vendor`.
- **The classic faces** are described in the face table rather than generated from it.

**Owner decisions recorded after the review (2026-10-14).**
- **Indic text fit** will use HarfBuzz shaping (`uharfbuzz` + `fontTools` + `brotli`), done as the next step. Until
  then Indic fits are estimates and advisory.
- **Icon matching** takes an exact name first (either set), then Lucide tags, then Tabler tags. This is the order
  `icons.TIERS` already implements.

## Gates at review time (HEAD d93226d, clean tree; the code lens ran them)

- ruff clean. `npx tsc -p tsconfig.json` clean.
- Generated-file checks all pass: `gen_env_example`, `aadhi.schemas.generate`, `make_fixtures`,
  `gen_chromium_seccomp`, `aadhi.compose.dynamic.catalogue`, `aadhi.compose.dynamic.faces`,
  `aadhi.compose.dynamic.icons`, `gen_third_party_notices` and `gen_colour_vectors.mjs`.
- `npm test`: 1,252 tests, 1,251 pass, 1 skipped.
- Fast Python suite: 5,207 passed, 5 skipped, 0 failed (16:58, on a loaded machine).
- Slow Chromium tests: 49 passed. The files: `test_board_real_page`, `test_stage_layout`, `test_live_css`,
  `tests/compose/dynamic/test_preflight_stage`, `test_stage_fonts`, `test_icon_pixels` and `test_textfit_page`.
- A path correction: `HANDOFF.md`'s gate block names the right file, `tests/compose/dynamic/test_preflight_stage.py`;
  there is no `tests/determinism/test_preflight_stage.py`.

## What each lens verified

**Plan.** Checked every B2b item against plan §A step 2, §1.2, §1.3, §1.11, §2 "Fonts" and §D, and against design
§4.6, §4.12, §6.7, §7.3 and §10.3 B2.

Fonts:
- The four families are vendored under `web/vendor/fonts/dynamic/<family>/`, each folder holding exactly its faces
  and the package's LICENSE.
- The classic fonts, `fonts.css` and `_copy_fonts` are untouched.
- `faces.py` is the single source and its `--check` runs in CI after `npm ci`. Every generated file is LF.
- The Dockerfile's web stage copies the modules `vendor.mjs` imports.
- `match_weight` follows CSS Fonts 4 §5.2 on all three branches.
- `resolver_fonts` names exactly B1's faces. `ENGINE_VERSION` is unchanged, and every possible B1 value satisfies
  the new `CatalogueId` constraint.
- `fonts.js`: the B1 form matches the old stage code, apart from the documented own-property lookup. For the
  resolver's faces, the B2 form gives the same stacks and requests as B1.
- `dynamic.css` and the stage's JS hard-code no family.

Text fit:
- Pillow `Layout.BASIC` reads all 77 face files. Variable axes are checked and always set, and an unreadable face
  raises `FaceError`.
- Clusters are resolved per the stage's stack, last-declared range first, with a cmap check: ħ falls back to Inter,
  and π is reported uncovered.
- `fitLines` is mirrored.
- The pinned advances run in the ubuntu Python job and in the container.

Icons:
- The pins, licences and index `--check` are in place.
- `LookIcon` entries keep A2's caps and pattern.
- Probes: `atom` → Lucide, `home` → Lucide `house`, `lungs` → Tabler, `photosynthesis` → none.
- The drawing helper's absence is correct: plan §A step 3 places it in B2c.

Notices: 36 packages with full texts. The gap the lens found is finding B2b-CONF-1.

**Licences.** Verified on `d93226d`.

Pins and packages:
- All 10 new npm packages are exact-pinned with registry `resolved` URLs, sha512 integrity and their declared licences.
- `npm ls` exits 0. No new package has an install script or dependencies.

Fonts and icons:
- Each of the 16 font packages' LICENSE holds the full OFL 1.1 text, with a copyright line and no Reserved Font Name.
  The name tables of the vendored Poppins, Lexend, Atkinson and Anek WOFF2 files (IDs 0, 7, 13, 14) agree, read with
  a brotli parser.
- Each vendored LICENSE is byte-equal to its package's, and every `url()` resolves.
- Lucide is ISC plus the Feather MIT section; Tabler is MIT. The index embeds both texts, equal to the cleaned package
  files.
- All 376 Tabler Brand icons are excluded. Lucide's `apple` is the fruit.

Vendoring:
- In a scratch worktree, `node scripts/vendor.mjs` produced a `web/vendor` tree SHA-256-identical to the repository's
  (140 files), twice.
- A minimal Docker-web-stage context (`package.json`, the lock, `vendor.mjs`, `vendor_faces.mjs`, `faces.gen.js`)
  vendors successfully.

Runtime: the CSP is unchanged (`font-src 'self'`), the new files are served with no-cache, and no system font can be
reached by a family id.

**Code.** Re-ran every gate (above). Checked:
- `fonts.js` against `faces.stage_roles` / `stack`, including W2 ids, unknown groups and prototype keys.
- The B2 request weights cover every weight `dynamic.css` uses.
- `TimelineFonts` round-trips B1's dict byte for byte.
- `vendor_faces.mjs` fails closed.
- `.gitattributes` forces LF on the four generated files.
- `icons.py`: the strict path parser (probed with exponents, 7 decimals, flags, commas, `z1`, `M 1 2 3` and `MZ`), the
  SVG 2 shape conversions, the pre-parse refusals and deterministic tiers.
- `textfit.py`: the axes, `FaceError`, the cmap check, conservative tracking, and that Indic and uncovered text is
  never certified.

**Differential (real Chromium).** Each side ran its own real app (`create_app` under uvicorn, the conftest `real_app`
settings, `DYNAMIC_ENABLED` on). Pages were opened with `stage_page.open_stage` and hashed with
`determinism.hash_frames` (CDP JPEG q92, the production capture).
- The base side was a worktree of `c3a1e9b` with its own `web/vendor`. It is SHA-1-identical to the repository's
  except for the 35 new files under `fonts/dynamic`.
- The B2b side was the repository at `d93226d`, read-only.

Inputs:
- The QA fixture (`render_parity.dynamic_timeline`), dark and `paper_ink`, both formats: 4 timelines, 152 sampled
  frames each.
- The showcase lecture (`showcase_ohms_law.json`): `midnight_indigo` and `paper_ink` in both formats with display
  `outfit`; `poppins` (16:9), `lexend` (9:16) and `inter` (16:9); a Tamil board-language case (`ta-IN`, `paper_ink`,
  9:16) and a Hindi case (`hi-IN`, display `poppins`, 16:9). That is 9 timelines, 341 sampled frames each.

Every lecture was built three ways: by `c3a1e9b` (engine 2), by the real B1 code at `3226e20` (engine 1), and by the
B2b resolver. Frames were sampled in every scene: f0, f0+1, the transition window every 2 frames, the middle, f1-1
and f1-2, plus a uniform grid and the last frame.

| Comparison | Timelines | Result |
|---|---|---|
| (A) the same `c3a1e9b`-built JSON on the `c3a1e9b` stage and the B2b stage | 13 | 0 of 3,677 frame pairs differ |
| (B) the same `3226e20` B1-built JSON (engine 1) on both stages | 13 | 0 of 3,677 differ |
| (C) the `c3a1e9b` resolver and stage against the B2b resolver and stage, on the same inputs (`poppins` and `lexend` stored as `outfit`, Tamil, Hindi) | 13 | 0 of 3,677 differ |

In all, 11,031 frame pairs and 0 differences.
- **Parity of the page itself.** Stage info is equal for every pair, the `--dyn-font-*` stacks are equal string for
  string, and there were 0 page errors and 0 timer-guard trips.
- **Loaded fonts.** `document.fonts` differs only by the new `@font-face` entries, which stay `unloaded` before and
  after all frames, and by the Inter-display Outfit request (finding NVC-1).
- **Controls.** On the base side Outfit and Inter differ in 312 of 341 frames, so the capture sees a face change. Outfit
  and `poppins` differ in 0, because B1 drew Poppins as Outfit. A reverse-order capture of four B2b-built timelines gave
  the same 1,364 hashes.
- **Resolver JSON.** The QA fixtures are byte-identical. Every showcase build differs only by the added
  `fonts.indic = "noto_sans"`, plus `fonts.display` rewritten to `"outfit"` for `poppins` and `lexend` (intended). All
  39 built timelines round-trip byte-identically through the B2b `Timeline` model.

## Findings

10 findings: plan 3, licence 2, code 4, differential 1. None was refuted. The table gives the reviewer's severity, then
each skeptic's.

| Finding | Lens | Reported | Skeptic verdict | Outcome |
|---|---|---|---|---|
| `B2b-CONF-1`: `THIRD_PARTY_NOTICES` omits the third-party code inside `p5.min.js` (core-js, opentype.js, libtess under SGI-B 2.0, gifenc, omggif, es6-promise, whatwg-fetch, fetch-jsonp, file-saver ...), which comes from p5's own devDependencies and so has no lock entry; the header claimed bundled code was covered | plan | blocker | two skeptics: blocker, blocker (the MIT and SGI-B notices must go with every copy, and the minified bundle strips them) | fixed (with LIC-1) |
| `LIC-1`: the same gap, wider: p5, `three.core.js` (earcut 3.0.2 ISC) and MathJax (`mj-context-menu`) embed code no notice covers; the test compared only against the lock | licence | major | two skeptics: major, major (no regression, as no notices file existed before; but the delivered file is incomplete and claims completeness) | fixed: the reviewed `VENDORED` table of every served file and its bundled components, with committed licence texts in `scripts/third_party_licences/`; generation fails closed on version drift, missing evidence, an unlisted p5 module or an unacknowledged copyright statement; header corrected |
| `B2b-CONF-2`: `faces.stage_roles` / `stack`, and so `textfit.measure` / `fit_text` / `fit_slot`, read the schema's `TimelineFonts` (not a `Mapping`) as an empty B1 dict, so they silently measured Outfit/Inter/Noto (Poppins text came out about 10% narrower; non-conservative) | plan | major | two skeptics: minor (no caller yet; no type checker in CI), major (a fail-open in a public API B2e will call with exactly that value) | fixed (with F3): `faces.fonts_mapping` dumps a pydantic model with `exclude_none`, maps None to `{}` and raises `TypeError` for anything else; every faces and textfit entry point goes through it |
| `F3`: the same root cause, found by the code lens | code | minor | minor | fixed (as B2b-CONF-2) |
| `B2b-CONF-3`: `icons.scene_icons` read `id` / `icon_hint` with `getattr`, so dumped or stored dict items silently gave no icons | plan | minor | minor (no caller yet) | fixed: mappings and attribute objects are read; anything else raises `TypeError` |
| `LIC-2`: the notices named mathjs' bundled dependencies at the lock's versions (`decimal.js` 10.6.0) where the bundle holds others (10.5.0), and the bundle's banner pointed at `math.js.LICENSE.txt`, which was not served | licence | minor | minor (the licence terms are the same) | fixed: `vendor.mjs` copies `math.js.LICENSE.txt` beside `math.js`; the notices quote it and list the bundle's versions |
| `F1`: the conservative width added the 0.06 em kerning floor once per word, but positive kerning adds up per pair: `f'f'f'f'` (Outfit 700) is drawn 0.17 em wider than the bound, and a `board_generic` title of `f'` runs was certified `fits=True` while the page drew 4 lines with 118 px overflow | code | major | two skeptics: major, major (reproduced on the real page; plan §1.11 broken; the corpus had only single-pair words) | fixed in two rounds (below): a measured kerning table of pairs, context triples and unbounded runs |
| `F2`: `word_kinds` read "certainly broken" from the slack alone, but the page's kerning makes words up to 17% narrower: `AV`×10 was laid out broken at 88 px / 2 lines where the page keeps it whole at 56 px / 1 line, so the stored `{size, lines}` would be wrong | code | minor | minor (fits stays correct; the stored size does not) | fixed in two rounds (below): per-pair and per-context lower bounds |
| `F4`: misleading icons for common lesson words (`carbon` → a shopping cart, `plastic` → a credit card, `answer` → a phone, `stop` → `monitor-stop`, `C++` / `C#` → a music clef through the tag `c`); the teaching-word test only checked "not None" | code | minor | minor (the lists are owner territory, but the symbol collapse is a real gap) | fixed: symbol-bearing and one-character hints match nothing; 7 `PREFERRED` entries (6 Lucide, 1 Tabler) and 14 `IGNORED_TAGS` entries (8 Lucide, 6 Tabler) added (B2b proposals); all 200+ teaching words pinned to their expected icon |
| `NVC-1`: comments in `fonts.js` and `fonts.test.js` said the resolver's requests equal B1's "string for string"; for display `inter` they omit B1's four Outfit faces (no pixel effect, already tested) | differential | minor | minor | fixed: the comments now say the stacks are identical and the requests equal `fontRequests` for Outfit, and `fontRequests` without Outfit for Inter |

## Fix rounds and audits

**Round 1.** The first fixer was cut off by a network error after committing part of its work (`0e14281`). A second
fixer reviewed that commit, kept it, ran the mutation checks it had not run, and completed it.

B2b-CONF-1 and LIC-1 (the notices):
- `VENDORED` now covers every served `web/vendor` file, and a test walks `web/vendor` to enforce this.
- It lists 37 components:
  - `p5.min.js`: 15 bundled packages, plus code copied from stacktrace.js, Raphaël and Processing.
  - `three.core.js`: earcut, a paper.js port, and Geometric Tools code (BSL-1.0). An Elsevier book comment is recorded
    as a credit, not as licensed code.
  - MathJax: `mj-context-menu`, `speech-rule-engine` and `mhchemparser`.
  - Chart.js: the `@kurkle/color` 0.3.2 it bundles. DOMPurify: a `regenerator-runtime` helper.
  - mathjs: its bundled dependencies, at the bundle's versions.

LIC-2: `math.js.LICENSE.txt` is vendored and quoted.

B2b-CONF-2 and F3: `fonts_mapping`.

B2b-CONF-3: `scene_icons` accepts mappings.

F4: the hint refusals and the list additions; the index was regenerated.

F1:
- The fixer extended the partial commit's pair table after finding it still not conservative. Inter's contextual
  alternates widen glyphs by their neighbours, which no pair shows: `x` between digits becomes `×`, and `*` widens
  before punctuation or capitals.
- `kerning.json` became version 2, adding every ordered triple of every Latin face (measured with 12 Chromium contexts
  in parallel, at both letter-spacings).
- `Cluster.reliable` now refuses a lone combining mark.

F2: the optimistic reading subtracts a face-wide most negative adjustment per pair.

NVC-1: comments only.

Round 1 gates:
- 39 temporary mutations, all killed; each file was restored byte for byte.
- Fast suite: 5,274 passed, 5 skipped.
- Slow Chromium: 51 passed.
- `npm test`: 1,251 pass, 1 skipped.
- The full `kerning --check` passed (47 min).

**Audit 1** confirmed eight findings fixed with tests that fail without the fix. The notices table matches p5's module
table by strict set equality. The `wicked-good-xpath` / `xmldom` / `commander` names in `tex-svg.js` are run-time CDN
names, not bundled code. It found three problems:

1. **F1, major.** For Inter, the body face of every timeline, pairs and triples do not bound longer contexts. Inter's
   alternates chain through runs of `*` and punctuation. Measured in Chromium against Python's conservative width
   (page px vs bound, all `reliable=True`):
   - `A*****` 388.14 vs 364.06.
   - `A********` 581.64 vs 520.67, 0.61 em over.
   - `2********3` 638.78 vs 608.78.
   - `**:<H0?` 412.50 vs 405.64.

   34 of 20,436 random 4–10-character strings in Inter's alphabet were over. Outfit, Atkinson, Lexend and Poppins
   gave 0.
2. **F2, major (a regression the fix introduced).** Arrow ligatures (Inter `<=>`, `==>` at −0.644 em) dominated the
   face-wide most negative adjustment. That made Inter's per-pair lower bound about −0.66 em (it was −0.37) and
   Lexend's −0.24 to −0.31 em. Every long Inter word's optimistic width was then ≤ 0, so no word was ever read as
   broken. `Supercalifragilisticexpialidocious` in an Inter title was fitted at 56 px / 2 lines, where the page takes
   at least 80 px.
3. **B2b-CONF-1, minor.** The libtess notice was the npm package's ("Copyright 2012, Google Inc.", with an extra SGI
   paragraph). The libtess module inside p5's build reads "Copyright 2015, Google Inc.", and SGI-B 2.0 requires the
   dates of first publication. The test compared only the SGI line.

**Round 2.**

F1:
- `kerning.py` was rewritten to table version 3, and `kerning.json` regenerated in Chromium (85 min, now 2.88 MB).
- A run's bounds are the sums of its pairs' bounds and its context triples' excess D(abc) = E(abc) − E(ab) − E(bc),
  each stored both ways.
- **Chain characters:** the non-alphanumeric middles of context triples; in Inter `()*+-:<=>@[]{}~±×÷–—•⁄−`, in Lexend
  `⁄`. A run of 4 or more with two of them among any four neighbouring characters is never bounded. This covers chains
  no fixed-length measurement can find, such as `*:<H`, which measures +0.144 em while every one of its triples
  measures 0.
- **Open runs:** every near triple is measured again with each character before and after it. An interacting
  four-character run is stored as open, directly or through a covering triple: `10x1`, whose `x` is a `×` where
  `0x1`'s is not; Outfit `ffiw`.
- `Word.reliable` now requires the run to be bounded, so such words are estimates (`fits` None). All 8 of the audit's
  words are now uncertified.
- **Still certified in Inter 400:** `2x3x4`, `X-ray`, `COVID-19`, `12:30`, `(DNA)`, `example`.

F2: the face-wide most negative adjustment is gone. The table stores per-pair lower bounds (an unlisted pair is
−1/1000 em) and per-context lower bounds. Results:
- Inter `Supercalifragilisticexpialidocious`: lower bound −0.071 em, read as broken, fitted at 80 px / 2 lines.
- Lexend: 76 / 2.
- The audit's body-slot URL: 56 px / 5 lines.
- On the real page, Python's size for an over-wide word is now at most one 4 px step below the page's: Inter 80 vs 84,
  Lexend 76 vs 80, Outfit 64 vs 68, and the long-word case 56 vs 56.
- Kerned words the page keeps whole (`AV`×10) still match exactly.

B2b-CONF-1: `scripts/third_party_licences/libtess-p5.txt` is the libtess module's header from
`node_modules/p5/lib/p5.js`, verbatim ("Copyright 2000, Silicon Graphics, Inc.", "Copyright 2015, Google Inc.", no
"Except as contained" paragraph). It replaces `libtess-1.2.2.txt` (deleted in the working tree). The test now compares
it with p5's source.

The page tests now model all of Python's margins (the negative kerning and the negative-tracking space width that
Python leaves out). An Inter display font set was added to the slot and F2 page tests.

Round 2 gates:
- 6 temporary mutations were caught by the fast tests; the 2 F2 mutations were also caught by the slow page test.
- Fast suite: 5,289 passed, 5 skipped (8:47).
- Slow Chromium: 56 passed (11:22).
- `npm test`: 1,251 pass, 1 skipped.
- ruff, `tsc` and all nine generated-file checks pass.
- The kerning table's own `--check` (another 85 min) was not run separately.

**Audit 2** found nothing. It re-ran every gate:
- Fast suite: 5,289 passed, 5 skipped, 292 deselected, 0 failed (11:25).
- Slow Chromium: 56 passed (12:16).

Instead of the 85-minute `--check`, it re-measured five faces in Chromium (`outfit-latin-700`, `lexend-latin-700`,
`atkinson-latin-400`, `jetbrains-mono-latin-400`, and `inter-latin-400` with its near-triple runs). The rows were
byte-identical to the committed table (433 s).

It compared 870,839 single-word Chromium measurements with `tf.measure`. They covered Inter 400/600/700, Outfit 700,
Lexend 700, Poppins 700 and Atkinson 400/600, plus latin-ext faces, at tracking 0, −0.015 and +0.02, over:
- targeted chains;
- realistic tokens (URLs, hyphenated words, `f(x)`, `10x10`, arrows);
- random ASCII, Latin-1 and Latin-A runs;
- digit, hex and fraction runs.

0 certified words were drawn wider than the conservative width, and 0 narrower than the optimistic width.

Mutation checks in a scratch worktree:
- **Killed:** dropping the chain rule, ignoring open triples, ignoring open four-runs, word reliability ignoring the
  bound (13 failures), and dropping the context `up` sums, then the `down` sums.
- **One survivor:** clamping a run's lower bound to 0. That only matters in the never-observed "always wider" case, so
  it is not a defect.

The libtess text matches p5's module header line for line, and the new margin term in the page test is a correct upper
bound.

The audit made one observation outside the round, not a defect where textfit measures. A few glyphs are drawn wider
in Chromium than Pillow's advance:
- Lexend 900 `″` (+5.7%).
- Lexend 700 `″` (+3.8%).
- Inter latin-ext 700 `Ƀ` (+3.2%).

At the slot weights (title 700, body 400/600) these stay inside the 4% slack. Weight 900 is used only for rich-text
bold runs, which textfit does not measure today.

No visible change after the fixes:
- The only `web/` source changes in the fix rounds are comments (`git diff d93226d -- web/` without comment lines is
  empty).
- `web/vendor` gains `mathjs/math.js.LICENSE.txt`, which no page loads.
- `textfit` and `kerning` have no production importer.
- The resolver is unchanged.

So the differential above still stands for the fixed tree.

## Gates after the fixes (HEAD 0e14281 plus the round-2 working tree)

- ruff: all checks passed. `npx tsc -p tsconfig.json`: exit 0.
- Nine generated-file checks pass: `gen_env_example`, `aadhi.schemas.generate`, `make_fixtures`,
  `gen_chromium_seccomp`, `aadhi.compose.dynamic.catalogue`, `aadhi.compose.dynamic.faces`,
  `aadhi.compose.dynamic.icons`, `gen_third_party_notices` and `gen_colour_vectors.mjs`. CI runs the faces, icons and
  notices checks after `npm ci`.
- `npm test`: 1,252 tests, 1,251 pass, 0 fail, 1 skipped.
- Fast Python suite: 5,289 passed, 5 skipped, 0 failed.
- Slow Chromium tests: 56 passed. The files: `test_board_real_page`, `test_stage_layout`, `test_live_css`,
  `tests/compose/dynamic/test_preflight_stage`, `test_stage_fonts`, `test_icon_pixels`, `test_textfit_page` and
  `test_kerning_table`.
- Kerning table regenerated from the final code; its `--check` (85 min, not in CI) not re-run; 5 faces re-measured
  byte-identical by audit 2.

## Open after the review

**Text fit**
- The HarfBuzz step for Indic text fit (owner decision). The kerning table's remaining assumption is that no four or
  five characters interact beyond what its near triples and chain rule find. The random and targeted probes found no
  case; shaped measurement could replace the table for Latin too (a proposal).
- Text that is no longer certified until then:
  - Inter runs with two chain characters among four: `step-by-step`, `f(x)`, `[1-3]`, `(a+b)`.
  - Inter and Lexend digits around a fraction slash, and `10x10`.
  - Outfit `fff`, `ffiw`, `ffjj` and `tttt`.
  - `test_textfit_page`'s hyphenated title in Inter display.
- For a word wider than the box at every size, Python's stored size can be one 4 px step below the page's. B2e stores
  `text_fit.size` as a cap, so such a title would draw one step smaller; storing only the line count for broken words
  is an option for the owner or B2e.
- The few glyphs wider than Pillow's advance at weights textfit does not measure (audit 2's observation).

**Platform**
- The Debian WOFF2 check, the container job and the Linux runs of the slow probes; the kerning table holds Windows
  Chromium measurements.

**Licences**
- The committed bundled-component licence texts other than omggif, stacktrace.js, the Node `path` notice and libtess
  were read but not compared with their published packages (that needs a download, which needs the user's permission).
  The generator's docstring claims they were, a claim from the interrupted first fix attempt.

**Owner review**
- The icon lists (`PREFERRED`, `IGNORED_TAGS`), the negation and singular rules, and the refusal of symbol-bearing and
  one-character hints.

**Not run**
- The Docker image build (its web stage now needs `vendor_faces.mjs` and `faces.gen.js`, which `test_tooling` pins),
  the CI container job, and the kerning generator's `--check`.

**Housekeeping**
- Worktrees from earlier sessions are still registered with git: `C:/t/b2b_vis_base` and `C:/t/nvc_base` (both
  `c3a1e9b`). `b2b_vis_base` has a `node_modules` junction into the repository, so remove the junctions with
  `cmd /c rmdir` before `git worktree remove --force`.
- Large leftovers under `C:/t` from earlier agents (`rv_fast`, `pq`, and others) can be deleted once they are no longer
  needed.

## After this review: B2b-H (2026-10-15)

The text-fit part of this review was followed by B2b-H. It applies the owner decisions of 2026-10-14 (HarfBuzz) and
2026-10-15 (shape every script if a Chromium parity probe confirms parity, then retire the kerning table). Its review
record is `b2b_h_review.md`; its as-built note is design §4.6 "As built (B2b-H)".

For the items above:
- **Retired.** The kerning table, its generator and its tests (`test_kerning.py`, `test_kerning_table.py` and
  `AADHI_KERNING_REMEASURE`). Also retired: the Pillow measurement, the 4% / 8% slack and the 0.06 em floor. The page
  widths this review measured are now fast-suite parity cases, and its `REVIEW_RUNS` are part of the parity probe.
- **"Open after the review / Text fit": resolved, except one item.**
  - Indic text is certified.
  - The Latin runs this review left uncertified are no longer excluded, because the kerning-table bound that left them
    out is gone. `step-by-step`, `f(x)`, `10x10` and Outfit's `ffiw` are tested as certified. The rest are certified
    under the same rule, unless they hit one of the B2b-H exclusions in design §4.6.
  - Glyphs wider than Pillow's advance no longer matter, because text fit measures shaped widths.
  - **Still open:** for an over-wide word, Python's stored size can be one 4 px step below the page's.
- **"Open after the review / Platform": partly resolved.** The Debian WOFF2 check no longer applies to text fit:
  fontTools and HarfBuzz read the faces the same way everywhere. The Linux runs of the slow probes, now including the
  parity probe, are still open.
- **"Not run": partly resolved.** The kerning generator's `--check` no longer exists. The Docker image build and the
  container job are still open.
