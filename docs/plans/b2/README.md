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
- `b2b_review.md` is the B2b review (the same shape; it also summarises what the three B2b build agents reported, whose
  reports were not kept as files).
- `b2b_h_review.md` is the B2b-H review (HarfBuzz text fit: three lenses, every finding with its skeptics' verdicts
  and outcome, three audited fix rounds, the parity probe numbers, the gates and the two findings still open).
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

Status (2026-10-15): **B2b built and reviewed; not yet committed as final.** The build is `d93226d` ("WIP B2b"); the
review's first fix round is partly in `0e14281` (its fixer was cut off by a network error; a second fixer completed
it) and the second round and this docs pass are in the working tree. B2b vendored Poppins, Lexend, Atkinson
Hyperlegible Next and Anek (five scripts) with the face table `faces.py` and its generated `dynamic-fonts.css` /
`faces.gen.js`; filled `presentation.fonts {display, body, mono, indic}` with B1's faces; added `THIRD_PARTY_NOTICES`
(generated, covering the code inside the vendored bundles); pinned Lucide and Tabler with the committed icon index and
`icons.py`; and built `textfit.py` with a measured kerning table (Latin certified; Indic, uncovered and unbounded runs
estimated only). The review (`b2b_review.md`) had 10 findings, none refuted, all fixed in two audited rounds; a
real-Chromium differential found 0 of 11,031 frame pairs changed. Owner decisions (2026-10-14): Indic text fit moves to
HarfBuzz shaping in the next step; icon matching is exact name (either set), then Lucide tags, then Tabler tags. As-built
notes: `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 "Status (B2b)" and the sections it names. Next: commit B2b, then the
HarfBuzz text-fit step, then B2c (the vertical slice) (`HANDOFF.md`).

Status (2026-10-15): **B2b committed** (`f923487` fixes, `d63ff94` docs). **B2b-H built and reviewed, but not yet
committed as final.**
- **What B2b-H did:** applied the owner decisions of 2026-10-14/15. Text fit shapes every script with HarfBuzz
  (`uharfbuzz` + `fontTools` + `brotli`, new pinned dependencies) on the vendored faces, as Chromium does. A slow parity
  probe against the real render page showed parity for every face, weight and script, within 0.054 px. So Indic,
  Latin and mixed text are certified, the margins are 0.5% + 1/64 px instead of 4% / 8%, and the kerning table is
  retired.
- **Where it is:** the build and most of fix round 1 are in `87fa371` ("WIP B2b-H"). Fix rounds 2 and 3 and this docs
  pass are in the working tree.
- **The review** (`b2b_h_review.md`): 12 findings, none refuted, all fixed (the two documentation findings by this docs pass). The audits found 4 more, of which 2 are
  still open: a line break before a zero-width-space-joined word in flow text, and an over-wide word that starts with
  a hyphen. Both can certify fewer lines than the page draws, and they must be fixed before B2b-H is committed as
  final. textfit is not wired into rendering yet, so nothing drawn changed.
- **Next:** close those two, commit B2b-H, then B2c (the vertical slice, plan §A step 3). A WIP B2c from another
  session is on the branch `b2c` (`HANDOFF.md`).
