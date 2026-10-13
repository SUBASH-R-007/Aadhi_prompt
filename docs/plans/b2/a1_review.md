# B2a step A1 — adversarial review (2026-10-13)

Reviewed commit 7687c4e ("WIP B2a A1") with three independent lenses (Python/JS parity, validator + catalogue,
B1 invariance), then one skeptic per finding that tried to refute it. Nothing below has been fixed yet.

## Gates at review time (Linux cloud container, Python 3.11 venv, Node 22)

- ruff, gen_env_example --check, schemas.generate --check, make_fixtures --check, gen_chromium_seccomp --check: clean.
- `npx tsc -p tsconfig.json`: clean. `npm test`: 1184 pass, 1 skipped.
- `pytest -m "not slow and not network"`: 4705 passed, 1 failed. The failure was a Windows-only assumption in
  tests/evals/test_tooling.py (lock closure expected uvloop/colorama to be uninstalled), fixed in a follow-up
  commit. `test_chromium_check_launches_sandboxed_chromium` fails when run alone in the cloud container only
  (playwright 1.63 wants browser build 1243, the container ships 1194); it passed in the full run.
- Not run yet: the slow suite, the Docker image/container benchmark, the CI container job.

## Verified correct (parity lens, abridged)

The 33 Ottosson constants are identical. The cbrt, gamut-map, JS rounding, hue mix, compositing, contrast and
desaturate code match the JS. 1.38 M random cross-runtime probes gave 0 hex mismatches and a maximum float
difference of 2e-11. Mutation testing showed both parity tests can fail. colour.py and presentation.py keep no
duplicate maths, and their public names still work.

## Findings that survived the skeptic

1. **MAJOR — owner decision needed.** `themes._block_set` (themes.py ~428) picks `block.ink`/`block.ink2` over
   the shown block colours **plus their 30% hold-desaturated versions**. stage.js:166 (B1) picks over the shown
   colours only. Once step B makes the stage read stored tokens, these slots change on screen:
   - deep_teal/0, /3: ink #FFFFFF -> #14161C, ink2 #EAE7F2 -> #232636 (bumper/hook text turns dark)
   - midnight_indigo/0, /3: ink2 #F2E5EA -> #F6EDF1
   - midnight_indigo/2, /5: ink2 #F4E4E5 -> #F8EDEE
   - also on W2 families: forest_night/1, /4; cosmic/2, /4; bold_blocks/0, /3

   paper_ink/2's block base change (#C44323 -> #B05641) is the R-54 red lowering the plan requires (justified,
   but it must be listed). The two skeptics disagreed on the fix:
   (a) mirror stage.js exactly and move the held-contrast test into the validator as a reported finding
       (B2a "no visible change"), or
   (b) keep the hold-safe rule (B1's ink2 falls below 3:1 while held) and record every token change as
       deliberate.
   Either way: fix the colour_math.py docstring claim ("the colour the stage would have derived") and the DERIVED
   pins in tests/compose/dynamic/test_themes.py.
2. minor — Hue-separation check skips fixed-vs-fixed pairs (warn/ok/mark); the §4.7 validator text says every
   pair. Fix: delete the skip in `_validate_content` (today's worst value stays 30.2°, so no new findings).
3. minor — `test_the_studio_hook` (test_themes.py ~440) is a tautology. Replace it with one passing wall
   (#808080) and one failing wall (e.g. #7A7A7A), asserting the finding is absent or present.
4. minor — Personality rows: Editorial needs both entrances (highlighter_swipe, typewriter); Bouncy-soft's pop
   needs an icons-only restriction (§4.8: pop is never text). Chalk's "calm-focus" is fine (§5.9 defines it as lo-fi).
5. minor — gen_colour_vectors.mjs never mixes two greys (the filter (i*7+j*3)%4 drops every grey-grey pair) and
   has no hue difference just above 180°. Add explicit pairs at every p, regenerate, and add a Python spot check.
6. minor — colour.py docstring: say that oklch() hues now convert as (h*pi)/180, as color.js does, which can
   differ from B1's math.radians by one ULP. No threshold is affected.
7. minor — colour_math.source_over uses `1 - ta`; with int alphas it returns int 1. Use `1.0`.

## Refuted, or no change needed

- The forced-scrim colour_block row is computed on the red-lowered stored block. It is correct once step B paints
  `block.base/highlight`; carry that into step B as a contract.
- struct.accent stays saturated red in 3 slots. A2/B: take `refs.accent_next` and the intro wipe from the next
  scene's `block.base`, so the plan's per-transition red-flash test covers them.
- §4.7 band check (paper_ink/1 accent C 0.121 is a gamut limit kept from B1). It is not an A1 defect; optionally
  record it in the plan's deviation register.
- R-56 "≤ 5 theme colours" is explained in the validate() docstring. Optional: pin `len(STRUCTURE_KEYS) == 5`.
