# Batch B2 working plan

Engineering plan for Batch B2 (visual system) of `docs/DYNAMIC_VIDEO_DESIGN.md`, written on 2026-10-11 and moved
here from a local scratchpad so any session can continue it.

- `b2_plan_v2.md` is the binding plan (sequence B2-0 .. B2g, the transform policy measured in the B2-0 spike, the
  frozen contracts §1.1-1.12, rules §2, gallery §G and the deviation register §D).
- `b2_plan.md` is v1; `b2crit_*.md` are the three critiques v2 answers.
- `b2a_workflow.js` holds the step prompts for B2a (A1 colour/themes, A2 zones/templates/catalogue/schema/preflight
  fixes/resolver, B JS consumers, adversarial review).
- `reads/*.md` are reports from a full code read made before B2.
- `a1_review.md` is the review of step A1 and the owner's block-ink decision; `reports/a2.json` and `reports/b.json`
  are the implementers' reports of steps A2 and B (contracts, token changes, tests, open items, doc notes);
  `b2a_review.md` is the B2a review (four lenses, every finding and its outcome, the differential numbers, the gates).
- `HANDOFF.md` is the session handoff: where things stand, the gates and the next steps.

These files still refer to their old location (`.../scratchpad/b2_plan_v2.md`, `.../scratchpad/reads/`); read those
paths as `docs/plans/b2/`.

Status (2026-10-13): B2-0 done. B2a step A1 (colour_math, themes + validator, Python/JS colour vectors) is
implemented in the working tree commit "WIP B2a A1"; its targeted tests pass, the full suites were not yet re-run.
Next: finish verifying A1, then A2, B and the review.

Status (2026-10-13, cloud session): the full gates were run on A1 and A1 was adversarially reviewed. Results and
the open findings are in `a1_review.md`. Finding 1 needs an owner decision. Next: fix the A1 findings, then A2,
B and the B2a review as written in `b2a_workflow.js`. Its paths are Windows paths; on Linux use `.venv/bin/...`
(a venv built from python3.11 plus `pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt
boto3`; manimpango needs libpango1.0-dev and libcairo2-dev).

Status (2026-10-14): **B2a done and reviewed.** A1's findings were fixed with the owner's hybrid block-ink rule
(`56ed3e9`); A2 (`6489b2d`, reports `ad7a07c`) and B (`6de3ddf`) are committed. The B2a review (`b2a_review.md`)
had 16 findings: 4 refuted, 11 fixed, 1 deferred to B2c; its last audit left 3 minor gaps in the source scan's guards
open. The fixes of both review rounds are in the working tree, not yet committed, at the time this line was written.
The as-built notes are in `docs/DYNAMIC_VIDEO_DESIGN.md` (§10.3 B2 and the sections it names). Next: commit the review
fixes and these docs, then B2b (assets), then B2c..B2g (`HANDOFF.md`).
