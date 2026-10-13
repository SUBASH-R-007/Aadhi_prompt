# Batch B2 working plan

Engineering plan for Batch B2 (visual system) of `docs/DYNAMIC_VIDEO_DESIGN.md`, written on 2026-10-11 and moved
here from a local scratchpad so any session can continue it.

- `b2_plan_v2.md` is the binding plan (sequence B2-0 .. B2g, the transform policy measured in the B2-0 spike, the
  frozen contracts §1.1-1.12, rules §2, gallery §G and the deviation register §D).
- `b2_plan.md` is v1; `b2crit_*.md` are the three critiques v2 answers.
- `b2a_workflow.js` holds the step prompts for B2a (A1 colour/themes, A2 zones/templates/catalogue/schema/preflight
  fixes/resolver, B JS consumers, adversarial review).
- `reads/*.md` are reports from a full code read made before B2.

These files still refer to their old location (`.../scratchpad/b2_plan_v2.md`, `.../scratchpad/reads/`); read those
paths as `docs/plans/b2/`.

Status (2026-10-13): B2-0 done. B2a step A1 (colour_math, themes + validator, Python/JS colour vectors) is
implemented in the working tree commit "WIP B2a A1"; its targeted tests pass, the full suites were not yet re-run.
Next: finish verifying A1, then A2, B and the review.
