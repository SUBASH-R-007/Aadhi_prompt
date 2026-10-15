# B2b-H adversarial review (2026-10-15)

This is the review of step B2b-H: HarfBuzz-shaped text fit for every script, with the kerning table retired. B2b-H is
the step the owner added after B2b (design `docs/DYNAMIC_VIDEO_DESIGN.md` §4.6 "As built (B2b-H)", §10.3 B2 "Status
(B2b-H)"; plan `b2_plan_v2.md` §1.11 and §2 "Fonts").

- **What was reviewed:** the change on top of `d63ff94`.
- **Where it is:** the build and most of fix round 1 are the commit `87fa371` ("WIP B2b-H"). The rest of round 1 (one
  test assertion), rounds 2 and 3, and this docs pass are in the working tree, not committed.

## Owner decisions this step carries out

- **2026-10-14:** text fit uses HarfBuzz shaping, with three pinned Python dependencies: `uharfbuzz`, `fontTools`
  (WOFF2 decoding) and `brotli`.
- **2026-10-15:** shape every script, Latin too, provided a Chromium parity probe confirms that the shaped widths are
  what the page draws. If parity holds, retire the Chromium-measured kerning table (`kerning.py`, `kerning.json`, its
  tests and its OPERATIONS notes). If it does not hold for a script or face, keep the closest correct design for it
  and report the measured evidence.
- **Unchanged:** icon order is exact name first.
- **CI:** no many-minute check runs in CI. Long re-measurements are opt-in through an environment variable.

**Decision per script:** parity held for Latin, all five Indic scripts (Noto Sans and Anek; Devanagari as `hi-IN` and
`mr-IN`) and mixed text, in every vendored face and weight the stage draws. All of them are shaped and certified, and
the kerning table is retired.

## The build

- **New module.** `aadhi/compose/dynamic/shaping.py` decodes WOFF2 with fontTools and brotli and shapes with
  uharfbuzz. It mirrors the stage's `unicode-range` filter, variable axes, features (`kern`, `liga` and `clig` on;
  `liga`, `clig` and `calt` off when the letter-spacing is not 0), script and language.
- **Rewritten.** `textfit.py` adds Chromium's font fallback, including kerning against a glyph the face lacks, and
  letter-spacing per HarfBuzz cluster. The new margins are `SLACK` 0.5% and `BOX_PX` 1/64 px.
- **Kept from B2b:** word units, whitespace collapsing, `fitLines`, the either/unavoidable break rules, and
  `FitResult.stored()`.
- **Dependencies.** `requirements.txt` and `requirements.lock` gain `uharfbuzz` 0.56.2 (`<0.57`), `fonttools` 4.66.1
  and `brotli` 1.2.0.
- **Tests.**
  - `test_dynamic_isolation.py`: a classic build loads none of the three.
  - `test_textfit.py` (rewritten, 85 tests): shaped widths of all 77 face files pinned, the fail-closed cases and the
    Chromium quirks.
  - `test_textfit_page.py`: reworked.
  - `test_textfit_parity.py`: new, slow.
- **Deleted:** `kerning.py`, `kerning.json` (2.88 MB), `test_kerning.py` and `test_kerning_table.py`.
- **Mutations:** the builder ran 29 in a scratch worktree, plus 2 in real Chromium, and all were killed. Three
  survived the first pass (pieces not reshaped, characters before the first glyph's cluster, `hb_face`'s empty-face
  check); a test was added for each and the three were then killed. The 2 Chromium ones: keeping `calt` with spacing
  failed the probe with 358 violations, and `SLACK = BOX_PX = 0` failed the page corpus.

Gates on the build:
- ruff, `tsc` and all ten generated-file checks pass (B2b's nine plus `lock_requirements --check`).
- `npm test`: 1,252 tests, 1,251 pass, 1 skipped.
- Fast suite: 5,289 passed, 5 skipped, 0 failed. The touched files were re-run on the final tree.
- Slow Chromium gate: 55 passed in 4.5 min.
- Extended probe: 3 passed in 192 s.

## Method

- **Lenses.** Three independent lenses:
  - **accuracy:** real Chromium, adversarial corpora;
  - **deps:** pins, wheels, Docker, licences, isolation;
  - **code:** an exact copy of the change in a scratch worktree, every gate, and a read of the code.
- **Skeptics.** Each surviving finding got one skeptic, or two for the most severe. Each skeptic was asked to refute
  it or correct its severity.
- **Fix rounds.** Three fix rounds followed, each audited.

## What each lens verified

**Accuracy.** Re-ran the probe and `test_textfit_page` in real Chromium and built adversarial probes in a scratch
worktree:
- **Builder's numbers reproduced exactly:**
  - Latin: 82,260 widths, max 0.0510 px, median 0.0067 px.
  - Indic: 252,660 widths, max 0.0321 px.
  - Flow lines: Python − page 1.708 to 124.815 px, never negative.
- **Adversarial corpus within tolerance,** apart from the findings below. It had 18,579 certified Latin widths over
  every style the stage uses, and every Indic style in Noto Sans and Anek. It covered:
  - ligatures and ZWJ;
  - numerals and symbols (½, ₹, °C, ², ±, ×);
  - punctuation runs;
  - combining sequences and default-ignorables;
  - conjunct-heavy and ZWNJ/ZWJ Indic words in all five scripts (क्ष, स्त्री, राष्ट्र, ஸ்ரீ, క్ష్మ, ರ್ಕ್ಷ್ಯ, ന്റെ, chillus);
  - mixed-script words (`APIகள்`, `हिंदीEnglish`).
- **Mechanisms confirmed:**
  - Chromium drops `liga`, `clig` and `calt` with spacing.
  - Letter-spacing is applied per HarfBuzz cluster.
  - Anek's `wght` is clamped, and the axes check fails closed.
  - The fallback order and `unicode-range` filter hold (Atkinson `ŶA`).
  - Uncovered characters stay `fits=None` (№, ﬁ, ₂, π, √).
- **Python fits => page overflow 0** on `board_generic`, except the U+202F titles (H-1).
- **Not verified:** Linux Chromium (no Docker daemon; WSL has no Playwright browser).

**Deps.** All read-only:
- **Lock.** The lock differs from `d63ff94` only by the three pins. `lock_requirements.py --check` passes with no
  `PLATFORM_PINS` entry.
- **Wheels.** `pip download --only-binary=:all: --python-version 3.11` finds every pinned version for
  manylinux2014_x86_64 and win_amd64:
  - `uharfbuzz` 0.56.2 cp310-abi3 (manylinux2014 / _2_17 / _2_28);
  - `fonttools` 4.66.1 cp311 and py3-none-any;
  - `brotli` 1.2.0 cp311.

  None has a required dependency.
- **HarfBuzz version.** The Linux and Windows `uharfbuzz` binaries both embed HarfBuzz 14.5.0.
- **brotli.** 1.2.0 is the release that fixes the 2025 brotli decompression-bomb CVE.
- **Installs.** The Dockerfile's pydeps stage runs `pip install -c requirements.lock -r requirements.txt boto3`, and
  `ci.yml` and `slow.yml` install with the lock.
- **Licences** (from wheel metadata): Apache-2.0 for `uharfbuzz` (only its own LICENSE), MIT for `fonttools` (LICENSE
  and LICENSE.external), MIT for `brotli`.
- **Notices.** The notices generator covers npm and `web/vendor` only, so recording the Python notices gap for the
  owner is the right call.
- **Isolation and leftovers.** Isolation passes. No code references the kerning files any more.
- **Determinism inputs.** Shaping reads only the `web/vendor` WOFF2 files: no PIL, raqm, system fonts or fontconfig.

**Code.** An exact copy of the uncommitted change was checked in `C:/t/rvh`, with all files byte-compared:
- **Gates:** ruff, the ten generated-file checks, `tsc` and `npm test` pass. Fast suite: 5,291 passed, 5 skipped.
  Slow Chromium gate: 57 passed. The probe numbers reproduce exactly.
- **Retirement complete.** No reference remains in `aadhi`, `tests`, `scripts`, `.github` or `docker` (docs aside).
- **No visible change.** The diff is empty for `web/`, `presentation.py` and the schemas, and nothing outside textfit
  imports textfit or shaping.
- **Code read:** checked the WOFF2 decoding (equal to fontTools' decoder), the RLock-guarded caches, the `unicode-range`
  sub-font (no vendored face has a cmap format 14 subtable, so variation sequences cannot bypass it), the axes check,
  `features()`, the fail-closed paths, the fallback, the margins and the pieces.

## Findings

Twelve findings survived to the skeptics, and none was refuted. Severity is the lens's, then the skeptics'.

| ID | Finding | Lens | Severity (lens → skeptics) | Skeptic notes | Outcome |
|---|---|---|---|---|---|
| H-1 | A word of Common characters plus U+202F NARROW NO-BREAK SPACE (`1 000`, `5 %`, `(1 000)`, `₹ 100`) gets no letter-spacing on the page (Script_Extensions make it a Mongolian run, and a cursive run takes no spacing), yet textfit subtracted the title's −0.015 em per cluster. At 100 px, Outfit `1 000` draws 247.11 px against a conservative 240.85. 12 of 12 real U+202F titles were certified and overflowed on the page | accuracy | blocker → blocker, blocker | Both skeptics reproduced it. Only negative tracking under-estimates. NBSP, figure space and Latin-letter controls match | **Fixed (round 1):** `shaping.script_runs`, a port of Chromium's `ScriptRunIterator` (scx, brackets, Inherited); `takes_letter_spacing` (cursive scripts take spacing only after a space); flow text read both alone and inside the whole text (`Word.readings`), conservative = wider. Tests: the review's titles and the page widths at both tracking signs; the U+202F cases added to the probe and the page corpus |
| H-2 | textfit does not model bidi: a strong RTL mark inside a Latin word splits Chromium's run and loses the kerning across it. Outfit `A`U+200F`V` draws 141.22 px against a certified 132.89 | accuracy | major → major, major | Reproduced for RLM and RLO. RLE did **not** reproduce (corrected); LRM and the isolates match; ALM matches only by accident | **Fixed (round 1):** `splits_bidi` makes any word holding a bidi class R, AL or an embedding, override or isolate (or their terminators) uncertified. This is uncertified, not modelled, so some controls that match are uncertified too (safe). LRM stays certified (page width pinned) |
| H-3 | The probes measured under `white-space: pre`; the stage draws `normal` (titles) or `nowrap`. Under `pre`, Chromium breaks kerning and ligatures across a ZWNJ: `A`ZWNJ`V` 141.22 against 132.20 | accuracy | minor → minor | Reproduced. Nothing is certified wrongly today: only the probe's fidelity is affected | **Fixed (round 1):** the probes measure under `nowrap` (`pre` only for a lone space), and ZWNJ/ZWJ-in-Latin cases were added. Mutating back to `pre` fails the probe |
| H-4 | The countdown ring (`.dyn-ring-num`, `tabular-nums`) was measured without `tnum`. Inter 700 `1111` at 40 px draws 103.4 px in a 96 px box, while textfit certified 69.3 px | accuracy | minor → minor | Reproduced. Realistic countdowns still fit | **Fixed with HBH-R1** |
| H-5 | OPERATIONS still documented the deleted kerning table, and the new opt-in `AADHI_TEXTFIT_PARITY_FULL` was undocumented. The `requirements.txt` comment named the wrong probe file | accuracy | minor → minor | Confirmed | **Round 1:** the `requirements.txt` comment fixed. **Docs pass:** the rest closed here (OPERATIONS, INTERFACES, design) |
| DEP-1 | `hb.shape` used HarfBuzz's default shaper list, which `HB_SHAPER_LIST` reorders. `fallback` gives unshaped widths, still certified; on Windows, `uniscribe` ignores the `unicode-range` sub-font | deps | major → minor, minor | Reproduced. An obscure debug variable that nothing sets; in the samples the error went the wide way | **Fixed (round 1):** `shapers=['ot']` (`SHAPERS`); its `RuntimeError` becomes a `FaceError`. A subprocess test runs `HB_SHAPER_LIST` = fallback, uniscribe and directwrite: widths are identical |
| DEP-2 | The `requirements.txt` comment said "imported only inside the Dynamic gate", which is false for brotli: `httpx`, `urllib3` and `aiohttp` import it eagerly and then advertise `br`. The isolation test would read a future classic `httpx` import as a Dynamic leak | deps | minor → minor | Confirmed. brotli is new in this change, so the HTTP side effect is new | **Fixed (round 1):** comment corrected; the isolation test excuses brotli only beside an HTTP client, and never excuses `uharfbuzz` or `fontTools`. The side effect is recorded in OPERATIONS and design §4.6 / §7.13 (docs pass) |
| DEP-3 | textfit used Python 3.11's Unicode 14.0 `unicodedata`, while HarfBuzz and Chromium use newer data. U+0CF3 (a combining mark since Unicode 15, drawn by Noto Sans Kannada) split `ಕೳ` into two clusters, and a word starting with it was certified | deps | minor → minor | Reproduced | **Fixed (round 1):** one Unicode database (`shaping.category`, `combining`, `bidirectional` with `NEWER_UCD`). A test checks that every character a vendored face draws is known |
| HBH-R1 | The same `tnum` gap as H-4, seen by the code lens: every ring reading is under-measured by 16–50% | code | major → major, major | Reproduced to the thousandth of a px. Not a blocker: unwired, and the slot is not critical | **Fixed (round 1):** `SlotSpec.tabular_nums` (set for the ring), threaded through `fit_slot`, `measure`, `shape_text` and `shaping` into `features()` → `tnum`. A parity probe test was added (max 0.013 px) |
| HBH-R2 | A separator no vendored face draws (U+3000, U+1680, U+2028, U+2029) was still certified at the 1.0 em estimate, because `_separator` dropped the `uncovered` flag | code | major → major, major | Reproduced; `m.uncovered` was even empty | **Fixed (round 1):** `Word.sep_uncovered`, included in `reliable` and `Measured.uncovered`. A test asserts no vendored face draws any of the four |
| HBH-R3 | The probe did not measure every face and weight the stage draws: display 400/600 (Outfit 400, Poppins and Lexend 400/600; a quote's text, a stat's unit) and Anek at the `wght` 800 a 900 bold run is clamped to | code | minor → minor | Confirmed. The lens measured them itself: 31,856 widths, max 0.0336 px, so parity holds | **Fixed (round 1):** styles added to the probe; the docstring's claim is now true |
| HBH-R4 | The same documentation remainder as H-5, plus INTERFACES, the design and the Python licences / notices gap | code | minor → minor | Confirmed | **Docs pass:** closed here |

## Fix rounds and audits

**Round 1.**
- **Code.** The ten code findings were fixed. Most of this round is in `87fa371`; the fixer reviewed and kept it, and
  added one assertion (no vendored face draws the four separators).
- **Docs.** H-5 and HBH-R4 were left to this docs pass, because docs were out of bounds for the fixer.
- **Gates.** The fast suite had 5,347 passed and 5 skipped. The slow Chromium gate passed 21 + 38. The extended probe
  passed 4 in 328 s. The default probe gave Latin 109,118 widths, max 0.0500 px; Indic 303,192 widths, max 0.0321 px;
  tabular 210 widths, max 0.0130 px.
- **Mutations.** 8 fast and 1 in real Chromium, all killed.

**Audit 1.** It confirmed every round-1 fix at the root, and found two problems that predated round 1:
- **An uncovered cluster inside a grapheme was certified** (major, from the build). `_measure_word` took a grapheme's
  `uncovered` flag only from its first HarfBuzz cluster. `T` + ि + U+0301 draws 199.86 px on the page against a
  certified 156.91. A page probe found 25 certified words wider and 77 narrower than their bounds, all of this shape.
- **Spaces around a non-collapsible space** (major, from B2b). A separator holding any collapsible whitespace was
  counted as one space. But `x` space ZWSP space `y` draws both spaces: 167.05 px against 139.66.

  H-5 was still open (docs).

**Round 2.**
- **Uncovered graphemes.** A grapheme is now uncovered when any shaped segment overlapping it is. A guard was added
  for words shaped inside the whole text (`Word.flow_uncovered`).
- **Whitespace.** `collapse()` lays whitespace out as Chromium does, which fixed three related defects found and
  measured while fixing it:
  - a form feed or vertical tab is drawn (about 0.9 em), not collapsed;
  - a line feed next to a ZWSP is removed, together with its spaces;
  - line breaking at non-plain whitespace was unmodelled (an en, em or thin space is not hung at a line's end, and
    `space ZWSP` before an over-wide word gives an extra line). Such text is now uncertified (`plain_separator`,
    `Word.sep_plain`, `Measured.lead_plain`).
- **East Asian rule.** Chromium's East Asian segment-break rule is not modelled. A test pins that no vendored face draws
  a wide character.
- **Docs.** The probe docstring's run times are now measured.
- **Gates.** Fast suite: 5,392 passed. Slow Chromium gate: 57 passed. Extended probe: 5 passed in 456 s (Latin
  2,203,799 widths, max 0.0522 px; Indic 1,667,556 widths, max 0.0324 px).
- **Mutations.** 18, all killed.

**Audit 2.** It confirmed both fixes. An independent page probe over random words in three configurations (Latin,
the five Indic scripts, ZWJ, ZWNJ, marks, NBSP, NNBSP, CGJ, U+25CC) found 5,439 certified words, none outside
[optimistic, conservative]. Another 23 separators matched within 0.01 px. It found one blocker, which predated round 2
but survived it:
- **Shaping across a zero-width space in flow text.** HarfBuzz skips U+200B, so the page kerns and ligates two words
  joined only by it, while textfit shaped them apart. Pairs drawn wider than certified, out of 85 × 85: Atkinson 400 had
  194 (q|j +12.57 px), Inter 400 had 73, and Outfit 700 had 78. `fit_text('q\u200bj', width=82.84, ...)` (Atkinson 400) returned fits=True
  while the page draws 94.91 px.

**Round 3.**
- **The fix.**
  - `joins()` detects whitespace that is only zero-width spaces.
  - Each ZWSP-joined pair is shaped as one text; what it draws beyond the two words is `Word.join_em` /
    `join_spacings`. `Measured.sep(conservative)` adds its positive part when the two words share a line.
  - In chains of three or more, every sub-run is shaped. The text is certified only if no sub-run draws wider than the
    pairs add up to (`Measured.joins_bounded`). Inter's case forms reach across two ZWSPs: `A`ZWSP`-`ZWSP`*`.
  - Chains longer than `JOIN_WORDS` (24) are not certified.
- **Tests.** The finding's cases now reach a conservative width at least the page's, for example Atkinson `q`ZWSP`j`
  95.42 against 94.91. A new slow parity test, `test_words_joined_by_zero_width_spaces_are_drawn_shaped_as_one`, was
  added.
- **Gates.** Fast suite: 5,399 passed. Slow Chromium gate: 58 passed. Joins: 8,227 certified, 9 not, 0 violations.
- **Mutations.** 7, all killed. The `join_spacings` term of the chain bound could not be exercised.

**Audit 3.** It confirmed the fix within a line: a HarfBuzz oracle over 2,700 certified random texts found 0 cases
where the single-line conservative width was too narrow. It found two problems, which are **open**:

| ID | Problem | Audit severity | Evidence (real render page, stage flow CSS, 100 px) |
|---|---|---|---|
| ZWSP-BREAK | Only partly fixed. The join gain is counted only when both words share a line, but Chromium measures a candidate line that ends at the ZWSP with the joined shaping. When word + gain does not fit, the page breaks one opportunity earlier. Suggested fix: count the positive gain in the conservative width of a word followed by a joined word at a line end too, and add a page line-break case to the parity suite | blocker (the workflow's summary lists it as major) | Atkinson 700 −0.015 em, `xx q`ZWSP`j xx` at W = 200: Python certifies 2 lines (`xx q` / `j xx`), the page draws 3 (`xx ` / `q`ZWSP`j ` / `xx`). Outfit 900, `xx (`ZWSP`j xx` at W = 160.83: the same. Control: Atkinson 400 at 0 em, where the gain fits, gives 2 lines on both |
| HYPHEN-LEAD | Pre-existing (the B2b `_pieces`). An over-wide word's break after a hyphen that is a piece's first cluster is never taken (the check reads `cl[end-1]` only for `end >= start+2`). Chromium breaks there (UAX #14 HY, preferred to `overflow-wrap: anywhere`), in titles and in flow text, so the page draws more lines than Python certifies. The textfit docstring's "a break after a hyphen only gives the page fewer lines" is wrong for a leading hyphen, and the hyphen test has no word that starts with one | blocker (the workflow's summary lists it as major) | Inter 700 −0.015 em, `go -abcdefgh` at W = 300: Python gives 3 lines (`go` / `-abcd` / `efgh`), the title draws 4. Atkinson 700, `q -c)d t )da` at W = 98.1: Python 6, the page 7. Inter 700, `f-c q x bjf -ddx -b` at W = 126.27: Python 8, the page 9 |

The workflow ended after round 3, so neither problem has been fixed or audited. **B2b-H must not be committed as
final until both are fixed** (a fourth round with its audit, or an owner decision). Until then the text fit can
certify a line count the page exceeds:
- for flow text holding a zero-width space between two words;
- for an over-wide word with a hyphen at a piece's start.

textfit is still unwired, so nothing drawn is affected.

## Probe numbers

Residual = page inline-block width − textfit's raw shaped width (px). The probe ran on Windows 11 with Playwright 1.63
Chromium, on the real `/render-dynamic` page with the stage's CSS and stacks and `lang` set per text.

| Run | Latin | Indic | Other |
|---|---|---|---|
| Build, default (each style at its smallest and largest slot size; about 50 s then) | 82,260 widths; max 0.0510, median 0.0067; beyond the layout unit ≤ 2.25e-4 of the width | 252,660; max 0.0321, median 0.0076; ≤ 1.06e-4 | flow lines: Python − page 1.7–124.8 px over 268 lines (median 16.7) |
| Build, extended (`AADHI_TEXTFIT_PARITY_FULL=1`, every 4 px size, 6,000 random runs per Latin style; 192 s) | 1,953,627; max 0.0531, median 0.0086; ≤ 5.28e-4 | 1,010,640; max 0.0322, median 0.0077; ≤ 1.84e-4 | — |
| Round 1, default (display 400/600 and Anek 800 added) | 109,118; max 0.0500, median 0.0070; ≤ 3.65e-4 | 303,192; max 0.0321 | tabular numerals: 210 widths, max 0.0130 |
| Round 2, extended (456 s) | 2,203,799; max 0.0522 | 1,667,556; max 0.0324 | 3.87 M widths |
| Round 3, default | max 0.0510 | max 0.0321 | flow lines 0.93–124.8 px; ZWSP joins: 8,227 certified, 9 not, 0 violations, 243 drawn wider than apart |

Per style, at the smallest and largest slot size (build, default corpus; max |residual| in px):

| Style | Small | Large |
|---|---|---|
| Outfit 700 −0.015 em | 0.0143 @50 (median 0.0060) | 0.0469 @148 (median 0.0085) |
| Poppins / Lexend / Inter / Atkinson 700 −0.015 em | 0.0157 / 0.0146 / 0.0146 / 0.0147 @50 | 0.0496 / 0.0490 / 0.0466 / 0.0501 @148 |
| Outfit / Poppins / Lexend / Inter / Atkinson 900 | 0.0149 / 0.0147 / 0.0138 / 0.0146 / 0.0147 | 0.0468 / 0.0510 / 0.0466 / 0.0470 / 0.0470 |
| Inter 400 / 600 / 700 (body) | 0.0151 @39 / 0.0117 @40 / 0.0117 @40 | 0.0137 @52 / 0.0273 @56 / 0.0137 @44 |
| Inter 600 +0.02 em (kicker) | 0.0148 @40 | 0.0154 @44 |
| Atkinson 400 / 600 / 700 (body); 600 +0.02 em | 0.0165 / 0.0159 / 0.0162; 0.0162 | 0.0173 / 0.0175 / 0.0179; 0.0164 |
| JetBrains Mono 400 and 700 | 0.0110 @40 | 0.0229 @56 |
| Indic, every style (default) | 0.0128–0.0205 | (0.0322 in the extended run) |

Medians are 0.001–0.010 px everywhere.

**What the probe established:**
- **Ligatures.** With letter-spacing ≠ 0, Chromium drops `liga`, `clig` and `calt`.
- **Letter-spacing.** It is applied once per HarfBuzz cluster that draws something. A ZWNJ-only cluster gets none, and
  a cursive run (U+202F numbers) gets none except after a space.
- **Kerning against a missing glyph.** Chromium keeps it: Atkinson 700 `ŶA` is 129.81 px and `AŶ` 132.81 px at 100 px.
- **`unicode-range`.** Glyph lookups are filtered by it. Before this was mirrored, residuals reached 10 px (`Ă`) and
  5 px (Atkinson mixed `latin` / `latin-ext`).
- **Marathi.** `locl` forms are reproduced: ल is 0.678 em in `hi-IN` and 0.699 em in `mr-IN` (Noto Sans Devanagari).
- **Indic coverage.** All 9,487 Indic syllables are drawn by their vendored face, so all are certified.
- **Kerning across spaces.** Faces kern across spaces only to make them narrower; a fast test checks every face and
  character. That keeps word-by-word flow measurement conservative.

**Margins derived from these numbers.** `SLACK` is 0.5% of the shaped advance and `BOX_PX` is 1/64 px. The largest
residual beyond the layout unit is 5.28e-4 of the width (0.053%), so the slack is about 9 times what this machine
needs. It is kept for Linux Chromium's advances and for future Chromium or HarfBuzz releases. It replaces B2b's 4% /
8% slack, its 0.06 em floor and its kerning table.

## Gates after round 3 (the final tree; the third audit reproduced them)

- ruff (`aadhi tests scripts evals docker`): clean.
- `npx tsc -p tsconfig.json`: exit 0.
- `npm test`: 1,252 tests, 1,251 pass, 1 skipped. The audit's worktree had 2 skipped: a test that needs `.venv`.
- All ten generated-file checks pass: `gen_env_example`, `aadhi.schemas.generate`, `make_fixtures`,
  `gen_chromium_seccomp`, `dynamic.catalogue`, `dynamic.faces`, `dynamic.icons`, `gen_third_party_notices`,
  `gen_colour_vectors.mjs` and `lock_requirements`.
- Fast suite (`-m "not slow and not network"`): 5,399 passed, 5 skipped, 0 failed (16–18 min).
- Slow Chromium gate: 58 passed in about 5 minutes. It ran `test_textfit_page`, `test_textfit_parity`,
  `test_stage_fonts`, `test_board_real_page`, `test_stage_layout`, `test_live_css` and
  `tests/compose/dynamic/test_preflight_stage`.
- Extended parity probe: last run after round 2 (5 passed, 456 s). Round 3's new test has no extended mode.
- Not run: the Docker image build, the CI container job, and any Linux Chromium measurement.

## Open after the review

**Text fit**
- **The two audit-3 problems above** (ZWSP-BREAK, HYPHEN-LEAD). They block committing B2b-H as final.
- **Flow text** (kicker, subtitle, body) is measured word by word. The page kerns across spaces only to make them
  narrower, so Python's line count is conservative: by up to 125 px on a long 148 px line, while the real slots are
  40–56 px. B2d's chunker may want exact line shaping.
- **Over-wide words.** Python's stored size can still be one 4 px step below the page's. Should only the line count
  be stored for broken words (owner or B2e)?
- **Not certified by design:**
  - characters in no vendored subset (Greek, maths symbols; vendoring Inter's Greek and symbol subsets would fix
    this);
  - words starting with a non-ignorable combining mark;
  - bidi controls;
  - non-plain whitespace (including `5 %` written with a thin space).

  Modelling Chromium's line breaking for typographic spaces is possible later if the owner wants such text certified.
- **`test_faces.py`** still opens every face with Pillow, which is now only a readability check.

**Platform**
- Linux Chromium is unmeasured. The probe runs in the container's slow job, which has never run. The 0.5% slack is
  meant to cover Linux advance rounding, but that is not measured. The Docker image was not built.

**Dependencies and licences**
- **Pins to watch.** `uharfbuzz` is bounded `<0.57`. Re-run the extended probe before raising it, and after a
  Playwright (Chromium) bump.
- **Notices (owner).** `THIRD_PARTY_NOTICES` covers npm and `web/vendor` only. There are no notices for `uharfbuzz`
  (Apache-2.0; HarfBuzz's "Old MIT" text is not in its wheel), `fontTools` (MIT) or `brotli` (MIT), nor for the
  image's other Python packages.

**Next**
1. Fix ZWSP-BREAK and HYPHEN-LEAD, audit the fixes and run the gates.
2. Commit B2b-H: code, tests and these docs.
3. B2c, the vertical slice (plan §A step 3).
