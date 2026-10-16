# B2c — as-built design notes (the pattern every later template follows)

Written 2026-10-15 from the merged B2c code (`3f3899b`, merge `6ff938a`) for the sessions and agents that build the
remaining templates. `b2c_review.md` records what the review found; this file records **how the slice is built**, so a
new template can be written without reading all 8,000 lines of the slice. Binding sources stay
`docs/plans/b2/b2_plan_v2.md` §1.4–§1.7 and `docs/DYNAMIC_VIDEO_DESIGN.md` §4.2–§4.9; where this file and the code
disagree, the code is as built and this file is the error.

## 1. What the slice added

| File | What it is |
| --- | --- |
| `web/js/dynamic/templates/_shared.js` (797 lines) | the layer every template draws through: geometry, text fitting, motion, item states, scrims, icons, media, formula tints |
| `web/js/dynamic/cues.js` (673 lines) | the two cues (`cue.term`, `cue.look_here`), drawn by the **stage**, never by a template |
| `web/js/dynamic/state.js` (item states added) | item entrance times, item states, the background's frozen clock, the caption on screen — pure functions of (timeline, t) |
| `templates/formula_focus.js`, `media_split.js`, `list_build.js` | the three pilots (one per hard problem: MathJax + tints, clips + push, per-item build-up) |
| `aadhi/compose/dynamic/templates.py` | their `TemplateSpec`s with `implemented=True`, `slots=...`, `consumes=...` |
| `tests/compose/dynamic/*fixtures*.py`, `web/tests/dynamic/templates/*.test.js`, `tests/determinism/test_template_*.py` | the test shape a new template copies |

`presentation.TEMPLATES` is still `("board_generic",)`: the pilots draw only a timeline that names them, so nothing
existing changed. The demo phase's `direct.py` is what makes real lectures pick them.

## 2. The module shape (copy this)

A template is a module with a default export; `formula_focus.js` is the fullest example. Hooks (plan §1.4,
`templates/types.js`):

```js
export default {
  id: 'my_template',
  build(ctx) { ... },           // DOM once per scene: elements, groups, masks. No measurement.
  layout(ctx) { ... },          // after build, with the page laid out: fitLines / place / overflow reports
  update(ctx, t, state) { ... },// every frame, pure in t; everything painted through ctx.paint
  texts(ctx) { ... },           // what text went where, with overflow, for preflight (§7.12)
  focusRect(ctx, target) { ... },             // whole-pixel rect for cue.look_here (cues.js draws it)
  itemEl(ctx, itemId) { ... },                // the item's card body, .rt-k term spans kept, for cue.term
  dimGroups(ctx) { ... },                     // the groups a look_here dims
  anchor(ctx) { ... },                        // morph anchor (B2f; null is fine)
  mediaBox(scene, zones, mode, slot, fixes) { ... },  // static: a clip's box, sized from the fixes
  tex(scene, look, fmt) { ... },              // static: the LaTeX this scene needs, for the TeX pre-pass
}
```

Rules the source scan (`web/tests/dynamic/sourceScan.test.js`) enforces, and which cost a build agent a fix round if
broken: no clock of your own (`Date`, `performance`, `requestAnimationFrame`), no emphasis drawing (that is cues.js),
no `will-change` / `filter` of your own, TeX **only** through `ctx.renderTex`, catalogue geometry **only** through
`core.js` / `_shared.js`, never touch `.timeline`.

Three further invariants:

1. **Pure in t.** `update` must draw frame t the same whether the player arrived from t-1, from t+1 or cold (the
   1x/3x/reverse gate in real Chromium, `tests/determinism/test_template_pages.py`). Carry no state between frames;
   read everything from `(ctx, t, state)`.
2. **Never drop content.** A scene the template cannot lay out whole is handed to `board_generic` by a `fitsScene` /
   `drawsScene` predicate (see `formula_focus.fitsScene`), and what still does not fit is **reported** through
   `texts()` as overflow so preflight blocks with "Shorten text" — never silently clipped, and never faded out to make
   room (9:16 included: `list_build` and `formula_focus` keep every row that entered on screen).
3. **Measure at layout, paint per frame.** `place` / `fitLines` write styles directly at layout time, as a function of
   the laid-out page; anything that changes with t goes through `ctx.paint` under the frame key.

## 3. `_shared.js` — what to reach for

- **Geometry (plan §1.1, catalogue only):** `region(ctx, name)` (16:9 `full`, `left-55`, `right-45`, `centre-60`,
  `top-band`, `stage`; 9:16 `hook`, `headline`, `stage`, `tall`, `clip`, `text-above`, `text-below` — a name the format
  lacks throws), `gridSpan(first, last)`, `boxRect`, `insetBox`, `place(el, box)`, `SPACE` (8...128), `RADIUS`
  (card 24, media 24, chip 999, presenter 40).
- **Text:** `rich(ctx, text)` and `tex(ctx, host, latex, display)`; `richWords(ctx, host, text)` splits rich-lite into
  word spans that keep their markup **and their `[[term]]` `.rt-k` spans**, which is how `cue.term` finds a term — a
  template that builds text any other way silently loses term cues; `fitLines(el, spec, words, texFlags)` is the
  page's own fit (the largest size in 4 px steps whose line count fits, `board_generic`'s rule, mirrored by
  `textfit.py`); `slotRange(ctx, slot, spec)` applies preflight's fixes and the R-29 floor;
  `slotSpec(id, slot, format, variant)`.
- **Motion (§4.8):** `seconds(ctx, s)` scales a duration by the personality (**cue timings never use it**),
  `enterAt`, `paintRise` (fade + rise on whole pixels), `revealMask` + `paintTextEntrance` (the personality's text
  entrance), `group(cls, ...children)` for a dim group the stage owns.
- **Item states (plan §1.5; `state.js` `ITEM_STATES` = current / past / done / struck):** `itemStateParts`,
  `layoutStrike`, `paintItemState`; `STATE_WEIGHT` 700, `STATE_BORDER` from the catalogue, `STRIKE_DRAW` 0.3 s.
- **Scrims (§7.12):** `scrimmedBg(scrim, onBackground, ink)` — the composited colour the text is reported on.
- **Icons (plan §1.2):** `storedIcon` / `sanitiseIcon` draw `look.icons[item]` only after re-checking the stored icon
  against the schema (`ICON_PATH_RE`, at most 4096 chars per path, at most 32 paths, stroke at most 8), path data set
  with `setAttribute`, never `innerHTML`; `fill="none"`, round caps and joins, the stored stroke width.
- **Media (§7.7):** `mediaAspect`, `containRect`, `pushHeadroom` (a push_ok template's picture box), `paintMedia`.
- **Formula tints (§4.7, R-30):** `tintTex` wraps each symbol in a `\color` placeholder, `tintTargets` finds its SVG
  groups after MathJax, `paintTint` colours them per frame — entity colours come from `ctx.entityRole`, and a symbol
  with no slot stays `ink`.

## 4. Cues are not yours (plan §1.5, deviation 3)

`cues.js` turns `TimedScene.events` of kind `cue_term` / `look_here` into pixels for every template the same way; a
template contributes **geometry only** (`itemEl`, `focusRect`, `dimGroups`). Only events the template's `consumes`
declares — the kind *and* the target's form — are drawn; anything else is skipped, never guessed, so `board_generic`,
which declares no cue, is never given one.

- Timing: a cue starts at `event.t`, holds to `t + max(draw, dur)` (`CUE_HOLD` 2 s without `dur`), and leaves in 70 %
  of its draw time. `TERM_DRAW` 300 ms sweep; `OUTLINE_DRAW` 250 ms; `DIM_RAMP` at least 300 ms (R-54) to the
  catalogue's dim alpha 0.7. **The personality's duration scale does not apply to cues** (§4.1 principle 2).
- `cue.term` styles the term's own span (background layers under its glyphs; the marker in dark families switches the
  glyph ink exactly where the band has swept, 35 % marker under unchanged ink in light families, else a 4 px
  underline), cloned over every line of a wrapped term.
- `cue.look_here` outlines `focusRect(ctx, target)` with a 4 px `focus` outline outset by at least 8 px in the Slot's
  `.dyn-cues` layer, clamped to the zone holding the target, and dims every `dimGroups` element that holds neither the
  target nor a `FocusRect.keep` element. On a `push_ok` spec it is a camera push (`PUSH_SCALE` 3 %) instead — **never
  in a scene that shows a clip** (`showsClip`: measured order-dependent, `test_template_clips.py`), and never under
  reduced motion in the live player.

So: declare `consumes` honestly in `templates.py`, return whole-pixel rects from `focusRect`, make every text run go
through `richWords`, and the cues work. Do not draw emphasis yourself — the source scan fails the build.

## 5. Registering a template

1. `web/js/dynamic/templates/<id>.js` — the module.
2. `web/js/dynamic/stage.js` — `import` it and add it to `TEMPLATES`.
3. `aadhi/compose/dynamic/templates.py` — its `TemplateSpec`: `implemented=True`, `release="R1"`, the semantic class,
   durations, `consumes={kind: (target forms,)}`, `holds`, `dense`, `push_ok`, `variants`, and `slots=_<id>_slots()`
   mirroring the JS layout constants (a jsdom test pins the two against each other, so the catalogue and the page can
   never drift). `dense=True` also makes the resolver freeze the background at 0 (R-55) and drop SFX (R-49).
4. Regenerate the catalogue (`python -m aadhi.compose.dynamic.catalogue`) — `--check` runs in CI.
5. Tests: `web/tests/dynamic/templates/<id>.test.js` (contract + slot parity, through `_harness.js`), a **new** fixture
   generator under `tests/compose/dynamic/` (never edit another template's), and a fixture per format in
   `tests/determinism/test_template_pages.py` (the 1x/3x/reverse real-Chromium gate) with zero preflight blocks.

## 6. Readings and open items B2c left (do not re-litigate; follow them)

- `cue.term` draws on its own span rather than in a `.dyn-cues` layer (a marker must sit under its glyphs, and a layer
  above cannot put it there without copying the paragraph's line layout) — plan v1 §1.4's form.
- A cue without `dur` holds 2 s; cue timings are not scaled by the personality.
- A clip is never scaled: no push over a clip, and a clip's entrance is a fade.
- 9:16 `media_split` gives the medium 16–46 % of the stage (owner decision). Two readings were added after the
  step-4 review (F3), because the showcase lecture could not be rendered in 9:16 at all: the reel phrase is capped at
  `templates.py MEDIA_SPLIT_PHRASE_WORDS` = 6 words per text item (measured with `textfit`: six words of average
  length fit the two 48–64 px lines of the 588 px column at the slot's minimum, seven do not), so a longer scene is
  `board_generic`'s whole instead of blocking preflight with `text_overflow`; and `media_split.js MEDIA_MIN_SHARE`
  = 0.4 floors the medium's share of the `stage` region, so a phrase that still does not fit grows over the medium
  (on its own scrim, and reported as overflow) instead of collapsing it to an unreadable stand-in. Whether the reel
  should instead get a three-line phrase is an owner decision.
- `formula_focus` 9:16 may fall back to a one-row legend; R-59's 696 px text column wins over §4.3's 768 px (the
  768 px is the card, deviation 13).
- `list_build`'s current item uses a 2 px `ink2` inset border (plan §1.5), not §4.3's 3 px outline.
- R-25 (at most 3 visible items) is a lint concern, not a template one.
- Every template size in the slice is a **B2c proposal pending the owner's review** through the reference frames
  (`docs/plans/b2/b2c_frames/index.html`, `python -m tests.compose.dynamic.make_reference_frames`, manual).
- Deferred by the review: V8, V9 and NVC-1 (`b2c_review.md`).
- The fix round also found and fixed a preflight defect worth knowing: `Finding.merge` kept the first sighting's
  status, so an overflow first seen mid-entrance never blocked; a checked sighting now wins.
