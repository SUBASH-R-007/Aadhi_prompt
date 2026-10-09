# Eval harness

`python -m aadhi.evals` measures lecture quality so prompt, model and pipeline changes can be
compared with numbers instead of impressions. It runs the real pipeline stages on a fixed set of
source documents, computes deterministic metrics, writes a report, diffs two reports, and can
optionally ask an LLM judge to score the lectures against a rubric.

Code: `aadhi/evals/` (runner, metrics, report, compare, judge, an in-process `JobContext`).
Fixtures: `evals/fixtures/`. Tests: `tests/evals/`.

## Quick start

```bash
# offline, deterministic, free (fake LLM/TTS/image providers)
.venv/Scripts/python.exe -m aadhi.evals run --provider fake --out evals/results/baseline

# real providers from your environment / .env (costs money; capped per fixture)
.venv/Scripts/python.exe -m aadhi.evals run --provider configured --budget-usd 1.50 --out evals/results/gemini-flash-v3

# what changed?
.venv/Scripts/python.exe -m aadhi.evals compare evals/results/baseline evals/results/gemini-flash-v3

# optional rubric scores by an LLM judge (real provider only; --engine gemini|openai|anthropic)
.venv/Scripts/python.exe -m aadhi.evals judge evals/results/gemini-flash-v3 --budget-usd 0.50

.venv/Scripts/python.exe -m aadhi.evals list      # fixtures and their options
```

## What `run` does

For every fixture, inside an **isolated workspace** (its own `DATA_DIR`, SQLite database and local
storage; the developer database and storage are never touched):

| Fixture type | Stages |
|---|---|
| source document (`.pdf`, `.docx`, `.txt`, `.md`) | ingest -> plan -> script -> lint -> critic [-> repair -> relint] -> companion [-> assets] |
| screenplay (`.json`: v2 `Screenplay` or v1 legacy lecture) | convert (v1 via `aadhi.legacy`) -> lint [-> assets] |

* Stages are the production functions from `aadhi.pipeline` (`ingest.ingest_source`,
  `plan.make_plan`, `script.write_scenes`, `validate.lint`, `critic.critique`, `repair.repair`,
  `companion.build_sheet`, `assets.build_assets`), called with `aadhi.evals.context.EvalJobContext`
  instead of a worker's job context. Missing modules are reported, not fatal.
* `--provider fake` sets every provider to `fake` and blanks every API key for the run, even if
  `.env` holds real keys. `--provider configured` uses your configuration as is.
* Each stage is timed and its cost attributed (`cost.by_stage`). The first failing stage stops that
  fixture; others continue. Errors are redacted.
* `--budget-usd` (default `MAX_COST_PER_LECTURE_USD`) stops a fixture once its spend exceeds the
  budget. Spend is reported per fixture (`cost_usd`) and in `summary.total_cost_usd` even when the
  fixture failed before it produced a screenplay (and therefore has no metrics).
* `--timeout SECONDS` bounds wall-clock time per fixture: a stage still running at the deadline is
  cancelled and the fixture fails at *that* stage with "timed out". A synchronous stage running in a
  worker thread cannot be interrupted; its result is discarded and the run moves on, but the thread
  finishes in the background (the process exits once it does).
* Each fixture's output directory is emptied before the fixture runs, so re-running into the same
  `--out` never leaves artifacts of an earlier run behind. An artifact that cannot be written (or a
  non-JSON value in event data) becomes a warning on that fixture; it never aborts the run.

| Flag | Meaning |
|---|---|
| `--fixtures DIR` | fixture directory (default `evals/fixtures`) |
| `--out DIR` | output directory (default `evals/results/<UTC timestamp>-<provider>`) |
| `--provider fake\|configured` | see above (default `fake`) |
| `--assets` | also run the asset stage (fake TTS in fake mode; Manim disabled unless `--render-manim`) |
| `--repair` | run the repair stage on lint + critic issues, then lint again (`repair.*` metrics) |
| `--only NAME` | run selected fixtures (repeatable) |
| `--options JSON` / `--options @file.json` | `GenerationOptions` overrides for every fixture; `{"llm_provider": "anthropic"}` picks the AI engine with `--provider configured` (`--provider fake` blanks every key, so a paid engine fails as not configured) |
| `--keep-workspace` | keep the eval database/storage under `<out>/workspace/` for inspection |
| `-q` | no progress output |

Exit codes: `0` all evaluated fixtures ok, `1` at least one fixture failed, `2` usage error (bad
fixture directory, unknown fixture, invalid options), `3` nothing was evaluated (every fixture was
skipped because pipeline modules are missing).

### Output layout

```
evals/results/<name>/
  report.json            machine-readable: config, non-secret settings, git commit, per-fixture metrics, means
  report.md              summary table, headline metric means, per-fixture details
  <fixture>/
    ingest.json plan.json screenplay.json [screenplay.draft.json] issues.json [issues.before_repair.json]
    [manifest.json] [companion.md] events.json usage.json metrics.json
  [workspace/]           only with --keep-workspace
  [judge.json judge.md]  after `judge`
```

`settings.llm_provider` / `settings.llm_models` in `report.json` (and the LLM line of `report.md`)
name the engine of a run-wide `--options` `llm_provider`, else `LLM_PROVIDER`
(`settings.default_llm_provider`); every fixture also records the engine its LLM stages used
(`llm_engine`, from its options), and `report.md` lists those engines when they differ from the run's.

`evals/results/` is git-ignored. To keep a baseline, copy its `report.json` somewhere permanent or
attach `report.md` to the pull request that changes prompts or models.

## Metrics (`aadhi/evals/metrics.py`)

All metrics are pure functions of the screenplay plus context (issues, source chunk ids, asset
manifest, usage). Percentages are 0-100 and `null` when the denominator is 0. Headline metrics (in
`report.md` and at the top of `compare`) have a direction; the rest are context.

| Group | Key metrics | Better |
|---|---|---|
| `schema` | `valid`, `error_count` (re-validates the JSON document) | valid |
| `structure` | scenes, chapters, beats, board items, scene types | - |
| `lint` | `errors`, `warnings`, `infos`, `by_code`, `by_source` (lint vs critic), `scenes_with_errors` | lower |
| `board` | `items_per_scene_mean/p90/max`, `scenes_over_item_limit_pct` (> 7 items), `chars_per_item_mean/p90/max`, `items_over_char_limit_pct` (> 140 visible chars), `unrevealed_items_pct`, `table_rows_max`, `code_lines_max` | lower over-limit |
| `pacing` | `words_per_beat_mean/p90/max`, `beats_too_long_pct` (> 45 words), `beats_too_short_pct` (< 4 words, chapter cards and quiz reveals exempt), `est_minutes`, `est_vs_target_pct_error` | lower error |
| `objectives` | `taught_pct` (in a non-quiz scene's `objective_ids` or via its concept), `assessed_pct` (quiz scene or quiz panel), `practiced_pct` (companion practice problems), `untaught`, `unassessed` | higher |
| `misconceptions` | `targeted_pct` (quiz distractor or misconception board item), `in_quiz_pct`, `untargeted` | higher |
| `quiz` | `quizzes_per_concept`, `quizzes_per_10_min`, `max_concepts_between_quizzes`, `answer_position_max_share_pct` (answer-position skew), `distractors_with_misconception_pct`, `wrong_feedback_coverage_pct`, `higher_order_pct` (Bloom apply+) | see keys |
| `grounding` | `beats_with_refs_pct`, `board_items_with_refs_pct`, `unknown_refs` (refs that are not source chunk ids), `source_coverage_pct` (chunks cited / chunks) | higher, fewer unknown |
| `media` | panel kinds, `visual_scenes_pct`, `rationale_coverage_pct` (non-skill-tree panels and AI-video scenes with a rationale) | higher rationale |
| `manim` | `template_ratio_pct` (templates vs free-form code), `templates_used`, `unknown_templates` | higher |
| `worked_examples` | `blank_steps`, `blank_filled_pct`, `fills_after_pause_pct` (faded steps get thinking time) | higher |
| `cost` | `total_usd`, `by_stage`, `by_operation`, `by_model`, tokens, TTS characters | lower |
| `audio` (with `--assets`) | `audio_minutes`, `runtime_minutes_est`, measured `speech_wpm`, `words_estimated_pct`, `missing_audio_scenes`, `fallback_media` | fewer missing/fallbacks |
| `source` | pages, chunks, figures, characters, truncated | - |
| `repair` (with `--repair`) | issues before/after (after = re-lint + the pre-repair critic issues; the critic is not re-run, to keep eval cost flat) | lower after |

Duration estimates (before TTS) use language speaking rates (English 150 wpm, Hindi 135, Telugu
110, Tamil/Kannada 105, Malayalam 100) plus the timeline's scene lead/tail, inter-beat gaps, pauses,
quiz countdowns and silent chapter cards. Thresholds are independent of the lint rules: lint
decides what a teacher sees; metrics compare whole lectures over time.

## Comparing runs

```bash
python -m aadhi.evals compare A B                       # run means, headline metrics first
python -m aadhi.evals compare A B --fixture ohms_law    # one fixture
python -m aadhi.evals compare A B --all --format md     # every numeric metric as a markdown table
python -m aadhi.evals compare A B --fail-on-regression  # exit 1 on any regression (CI gate)
```

The output starts with both runs' ok/failed/skipped counts and every fixture whose status changed
(`hard: ok -> failed at plan (regressed)`), then the metric table. Each row shows A, B, the delta and
a verdict: `better` / `worse` for metrics with a direction, `changed` otherwise, `added` / `removed`
when a metric exists in only one run.

* Run means are recomputed over the fixtures that are `ok` in **both** runs. A fixture that newly
  fails therefore cannot leave the candidate's mean and make it look better.
* `--fixture NAME` compares one fixture; a fixture that is not `ok` in a run contributes no metrics
  (its metrics are partial), and the header says so.
* `--fail-on-regression` exits 1 when a headline metric got worse, any fixture's status got worse
  (`ok` -> `skipped` -> `failed`, or an `ok` fixture missing from B), or more fixtures failed in B
  (for example a new fixture that fails).
* `--format md` / `--format json` keep stdout clean (regression reasons go to stderr); JSON contains
  `metrics`, `fixture_changes`, `common_ok_fixtures`, `summary` and `regressed`.

## The LLM judge (optional)

`judge` scores every lecture of a run 1-5 on accuracy, grounding, clarity, structure, pedagogy,
engagement, assessment and pacing, with justifications, strengths, weaknesses and the most
valuable fixes (`judge.json`, `judge.md`). The overall score is the unweighted mean computed by the
harness. It runs on `--engine gemini|openai|anthropic` (default `LLM_PROVIDER`; that engine's API key
must be set) with the engine's critic model (`LLM_MODEL_CRITIC` / `OPENAI_MODEL_CRITIC` /
`ANTHROPIC_MODEL_CRITIC`, or `--model`), uses the source excerpt from `ingest.json` as ground
truth, and refuses to run with the fake provider. Only fixtures with status `ok` whose report entry
lists `screenplay.json` among this run's artifacts are judged; others are reported as `skipped` with
the reason, so a lecture from an earlier run in the same directory is never scored. Judge scores are not deterministic: compare them
only between runs judged by the same model, and treat them as a complement to the metrics.

## Fixtures

| Fixture | Exercises |
|---|---|
| `ohms_law.pdf` | definitions, a formula, a units table, a measurement table (chart material), a worked example, a misconception, limitations |
| `logic_gates.pdf` | an 8-column truth table, Boolean algebra, De Morgan's theorems, a NAND-only XOR worked example |
| `stress_strain.pdf` | Greek symbols, sub/superscripts, a materials table, a numeric worked example with units |
| `sample_template.docx` | a real v1 teacher script (DOCX ingest with images) |
| `legacy_ohms_law.json` | the v1 showcase lecture, converted by `aadhi.legacy` (baseline for v1 vs v2) |

* Options: `<name>.options.json` next to a fixture holds `GenerationOptions` overrides (subject,
  session, `target_minutes`...). Unknown keys are rejected so a typo cannot silently change a run.
  Run-wide `--options` override sidecars.
* The PDFs are generated by `evals/make_fixtures.py` (PyMuPDF, byte-for-byte reproducible). Edit the
  content there, then `python evals/make_fixtures.py`; `python evals/make_fixtures.py --check` (and
  `tests/evals/test_fixtures.py`) fail if the committed PDFs are stale.
* Add a fixture by dropping a document (and optionally a sidecar) into `evals/fixtures/`. Keep
  fixtures small (1-3 pages): they run on every eval and in CI. Use a different language with a
  sidecar such as `{"language": "ta-IN"}`.

## Typical workflow for a prompt or model change

1. `run --provider fake` on `main` and on your branch: structural regressions (schema, lint, board
   fit, coverage of objectives and misconceptions) show up for free.
2. `run --provider configured --budget-usd ...` on both, `compare` them, and look at the worst
   fixture in `report.md`.
3. Optionally `judge` both runs with the same model.
4. Paste the `compare --format md` table into the pull request.

The weekly `slow` workflow (`.github/workflows/slow.yml`) runs the harness with fake providers and
uploads `report.json`/`report.md` as an artifact.
