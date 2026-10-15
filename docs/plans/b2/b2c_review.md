# B2c — vertical slice: build and review record (deadline mode)

Built on branch `b2c` (worktree `C:/t/b2c`, from `d63ff94`), merged into `overhaul/v2` on 2026-10-15.

**Built:** `web/js/dynamic/templates/_shared.js` (layout, text fitting, item reveals, the icon helper that re-sanitises
stored icons, media and TeX var-tint helpers), `web/js/dynamic/cues.js` (stage-driven `cue.term` marker/underline and
`cue.look_here` outline + dim, push only for push_ok templates and never over a clip), `state.js` item states, the
`ctx.motion` / `ctx.outgoing` context fields, and three pilot templates: `formula_focus`, `media_split`, `list_build`
(both formats, dark and light families, registered in `stage.js` and the catalogue; `presentation.TEMPLATES` is still
`("board_generic",)`, so no existing timeline changes until `direct.py`). Fixtures and generators under
`tests/compose/dynamic/`, slow Chromium gates `tests/determinism/test_template_pages.py`, `test_template_fixtures.py`,
`test_template_clips.py`, `test_formula_focus_page.py`. Reference frames: `docs/plans/b2/b2c_frames/index.html`
(`python -m tests.compose.dynamic.make_reference_frames`, manual).

**Review (deadline mode):** three lenses (design/visual, code and gates, no-visible-change differential), one skeptic
per finding, then ONE fix round (owner cap; the audit and docs agents were skipped for time). 18 findings survived:
15 fixed, 3 deferred (V8, V9, NVC-1; see the fixer's notes in the session record). The fix round also found and fixed a
preflight defect: `Finding.merge` kept the first sighting's status, so an overflow first seen mid-entrance never
blocked; a checked sighting now wins.

**Gates on the final b2c tree:** ruff, tsc, `git diff --check` clean; npm 1401 passed, 0 failed; fast suite 5330
passed, 0 failed; B2c slow Chromium gates 94 passed; other slow gates incl. the classic invariance differential 72
passed; no-visible-change differential on today's 12 timelines: 2,664 frames, 0 differences; all generated-file checks
pass (`lock_requirements --check` passes after the merge with B2b-H's dependency pins).

**Recorded readings and open items (from the builders):** cue.term draws on its own span rather than a `.dyn-cues`
layer; cue timings are not scaled by the personality; a cue without `dur` holds 2 s; a clip is never scaled (no push
over a clip, the clip entrance is a fade); 9:16 media share in media_split is 16-46% of the stage (owner decision);
formula_focus 9:16 may fall back to a one-row legend; the list_build current item uses a 2 px ink2 inset border (plan
§1.5) instead of §4.3's 3 px outline; R-25 (<= 3 visible items) is a lint concern; all template sizes are B2c
proposals for owner review via the reference frames.
