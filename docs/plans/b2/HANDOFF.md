# Session handoff — Batch B2 (Dynamic video visual system)

Updated 2026-10-13, at the end of a cloud session. Paste the "Prompt for the next session" block below into a new
session to continue.

## Where things are

- Branch: work is on `origin/overhaul/v2-el9z66`, the branch the cloud session was assigned. It is
  `overhaul/v2` plus 2 commits. To continue on `overhaul/v2`:
  `git fetch origin && git checkout overhaul/v2 && git merge --ff-only origin/overhaul/v2-el9z66 && git push origin overhaul/v2`
- Commits:
  - 3226e20 B1 complete
  - 54c72c5 rollback point
  - 7687c4e WIP B2a A1 (colour_math.py, themes.py + validator, scripts/gen_colour_vectors.mjs, tests/fixtures/colour_vectors.json)
  - 0031984 test_tooling platform-neutral fix
  - 52094f1 A1 review notes
- Done: B0, B1, B2-0 spike (policy in plan §P), B2a step A1 implemented. Full gates run on A1 (all green apart
  from one Linux-only test issue, now fixed). A1 adversarially reviewed: see `a1_review.md`.
- Not done:
  - the 7 open A1 findings (`a1_review.md`), the first of which needs an owner decision
  - B2a steps A2 and B
  - the B2a adversarial review, including a frame-hash differential proving no visible change for B1 timelines
  - B2b .. B2g
  - the slow suite, the Docker image/container benchmark and the CI container job (never run)

## Owner decision needed first (A1 finding 1)

`themes._block_set` picks the block text colours (`block.ink`/`block.ink2`) over the shown colours **and** their
30% hold-desaturated versions; the B1 stage (stage.js:166) uses the shown colours only. Once the stage reads stored
tokens (step B), deep_teal slots 0/3 flip from white to #14161C, and midnight_indigo 0/2/3/5 ink2 shift slightly.

- (a) Mirror stage.js exactly and report the held contrast as a validator finding. Keeps B2a "no visible change".
- (b) Keep the hold-safe rule and record each token change as deliberate.

## Binding docs (read in this order)

1. `docs/plans/b2/README.md` (paths ".../scratchpad/X" in the plan files mean `docs/plans/b2/X`)
2. `docs/DYNAMIC_VIDEO_DESIGN.md` §10.3 B2 (+ the sections the plan cites)
3. `docs/plans/b2/b2_plan_v2.md` (binding: sequence, §P policy, contracts §1.1-1.12, rules §2, gallery §G, deviations §D)
4. `docs/plans/b2/b2a_workflow.js` (exact step prompts for A1, A2, B and the three review lenses)
5. `docs/plans/b2/a1_review.md` (open findings)

## Owner rules

- Work must be perfect. Use Workflow orchestration (build -> integrate -> adversarial review -> one skeptic per
  finding -> fix -> audit) for every substantive step.
- Commit verified work in logical pieces on `overhaul/v2` only (never `main` without asking). Make a rollback
  commit before new work each session.
- Classic (Aadhi presenter) output must never change: the `tests/invariance` goldens must stay green.
- Never read `.env` or secrets. Never call paid APIs in tests (fakes only).
- If subagents fail with "organization has disabled Claude subscription access", report it; don't retry blindly.

## Open owner decisions outside B2

- H0 Gemini TTS hotfix on `main`: `gemini-2.5-flash-preview-tts` shuts down 2026-11-17 and `main` still uses it.
- Veo 3.1 preview may shut down 2026-10-22 (an Omni adapter exists on v2).

## Gates (run all before each commit)

```
pytest -m "not slow and not network" -q        # plus the slow suite before B2a is closed
npm test
npx tsc -p tsconfig.json
ruff check aadhi tests scripts evals docker
python scripts/gen_env_example.py --check
python -m aadhi.schemas.generate --check
python evals/make_fixtures.py --check
python scripts/gen_chromium_seccomp.py --check
```

Environment notes:
- Windows uses `.venv/Scripts/...`; Linux uses `.venv/bin/...`.
- Linux setup: `python3.11 -m venv .venv && pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt boto3`.
  manimpango needs `libpango1.0-dev libcairo2-dev`. `tests/core/test_s3.py` needs boto3.
- The fast suite takes about 11 min on Linux.

## Prompt for the next session

> Continue Batch B2 of the Dynamic video mode. Read docs/plans/b2/HANDOFF.md first, then the binding docs it lists.
> Ultracode is on: use Workflow orchestration for every substantive step. Start with a rollback commit, merge
> origin/overhaul/v2-el9z66 into overhaul/v2 if not done, then: [decision (a) or (b) for A1 finding 1] → fix the 7
> A1 findings in a1_review.md → B2a steps A2 and B exactly as in b2a_workflow.js → the B2a adversarial review
> (three lenses + frame-hash differential) → B2b..B2g per the plan.
