# Classic invariance harness

The classic mode (the Aadhi presenter tab: `canonicalize` defaults, the classic timeline, the state-screenshot
renderer) must not change while the Dynamic mode is built next to it. This directory holds the harness of
`docs/DYNAMIC_VIDEO_DESIGN.md` section 3.6. It blocks every batch from B0 on.

## What is checked

| Part | Where it runs | What it compares |
|---|---|---|
| Goldens (`test_goldens.py`, fast) | everywhere: Windows, the CI host, the container | Byte for byte, per fixture: the stored screenplay JSON, every `scene_hash`, the built and preview timelines, and every ffmpeg command line of the MP4 render with its filter graph and side files, also with the render dialog's caption options (`segments_burn.json`: burned-in captions; `segments_soft.json`: the soft `mov_text` track). Paths and cross-fade digests are normalised. Also the default `GenerationOptions` JSON. |
| Capture (`test_render_slow.py`, slow) | where Chromium and ffmpeg exist | The render page driven live reports the recorded states: times, keys, notices, and rects within 2 px. |
| Differential pixel test (`differential.py`) | the Docker `test` image in CI; locally on demand | The merge base and HEAD render the showcase fixture in the same container with the same media. Decoded frames and audio are compared with a tolerance. |
| Reproducibility (`test_render_slow.py`, slow) | where Chromium and ffmpeg exist | Two renders of the same checkout compare equal. This is the harness's self-check. |

Fixtures (`classic_fixture.py`):

- `showcase_ohms_law` is the v1 showcase converted by the importer. It is the fixture the design names, and it is the one that gets rendered.
- `classic_kitchen_sink` covers every scene type: a figure, a quiz with reveal beats, Manim and AI-video fallbacks, a scene without narration, a hidden scene and a held scene (`min_seconds`).

The asset stage is replaced by a deterministic manifest: each beat speaks for 0.2 s plus 0.3 s per word, with synthetic media. Goldens therefore never depend on a TTS provider or on the ffmpeg build.

### Tolerance of the pixel test

Both renders come from one machine and share every input, but the classic renderer is not bit-stable. These
variations were measured on Windows, over repeated captures and renders of the same code:

- Chromium's antialiasing of a few rounded panel corners varies by up to 15 pixels and 7 levels per screenshot.
- Once two encodes differ anywhere, x264's decisions spread tiny differences over the following frames: no pixel above 14 levels, and a mean difference of up to 0.005.
- The ducked-music tail of the mux varies in its last 0.1 s.

The comparison therefore allows this noise and nothing more:

- A **state screenshot** passes when it is identical. It also passes when at most 16 pixels differ by more than 12 levels over mid grey. Screenshots are lossless, so this is the tight check. It needs `--keep-states`, which CI passes. A state that exists on one side only always fails.
- A **frame** passes when it is identical. It also passes when at most 16 pixels differ by more than 24 of 255 and the mean difference stays at or below 0.05. With `--burn-captions` the mean limit is 0.5 (`BURN_MAX_MEAN_ABS`): that path re-encodes every frame under libass, and two renders of the same code already differed by a mean of up to 0.13 in the B0 audit. The 16-pixel limit is unchanged, so a real caption change still fails.
- **Audio** passes when it is identical. It also passes when no sample outside the last 0.5 s differs by more than 2.

A changed glyph or a 1 px shift changes hundreds of pixels by far more than 24 levels, and fails. To require
identical frames and states, pass `--exact`. On the first full run of the pre-B0 code against the B0 working tree,
the screenshots and frames differed only within this noise: 1 state and 500 of 9,701 frames.

## Running it

```sh
# goldens (seconds)
python -m pytest tests/invariance -m "not slow"
# live capture, branding probes, reproducibility (a few minutes; needs Chromium + ffmpeg + web/vendor)
python -m pytest tests/invariance -m slow
# the differential pixel test against any revision (read-only `git archive`; web/vendor copied from HEAD)
python tests/invariance/differential.py --base-ref origin/main --out OUT [--max-scenes 3]
# ... or against a checkout
python tests/invariance/differential.py --base-root ../base --out OUT
AADHI_INVARIANCE_BASE=origin/main python -m pytest tests/invariance -m slow -k differential
```

`OUT/summary.md` and `OUT/report.json` say what differs: the failing frames with their scene, the changed state
screenshots, the page states and the timeline. `OUT/frames/` holds base, head and diff pictures of the first failing
frames. Pass `--keep-states` to keep both renders' screenshots, and `--burn-captions` to render both sides with
burned-in captions. A render that fails (any exit code other than 0 and 2) fails the comparison: every run first
removes the previous run's reports, renders and frames from `OUT`, so they are never compared again.

## CI

The `container` job of `.github/workflows/ci.yml` builds the Docker `test` target, which is the runtime image plus
the test tools. It runs these steps against the checkout mounted at `/src`:

1. the fast suite, which includes the goldens;
2. the ffmpeg 5.1 feature checks: every test marked `container` (`tests/compose/test_ffmpeg_features.py` and the
   loudness measurements); the image sets `AADHI_EXPECT_FFMPEG=5.1`, so the ffmpeg version is pinned too;
3. the differential pixel test, merge base against HEAD, and a second one with `--burn-captions` when
   `aadhi/compose/{ffmpeg,captions,video}.py`, the npm lockfile (the fonts) or the `Dockerfile` changed;
4. the rest of the slow suite.

The job has no `if:` and no `continue-on-error`. To make it block merging, mark "Container suites (Docker test
image)" as a required check in the repository's branch protection settings.

The differential runs only when a renderer input changed: `aadhi/`, `web/`, `video_template/`, the requirements,
the npm lockfile, the `Dockerfile` or this directory.

## Changing the classic output on purpose

Some batches change the classic output on purpose. Such a batch declares the change, and the review approves it:

1. Add the `classic-change` label to the PR. The differential is then reported, not blocking. Its report is uploaded as an artifact.
   The job reads the label at run time (step `declared`), not from the event payload:
   - adding the label starts a fresh CI run, and "Re-run failed jobs" sees it too;
   - the push after the merge inherits the label of the merged PR;
   - a direct push without a PR declares the change with a reviewed `Classic-Change: approved` commit trailer in the
     pushed range.
2. Regenerate the goldens with the approved code, and commit them with the change. Re-record a capture only when the render page's states changed:

   ```sh
   python tests/invariance/render_classic.py --code-root . --out OUT --fixture showcase_ohms_law --capture-only
   cp OUT/capture.json tests/invariance/goldens/showcase_ohms_law/capture.json   # same for classic_kitchen_sink
   AADHI_UPDATE_GOLDENS=1 python -m pytest tests/invariance -m "not slow"
   ```

The golden diff in the PR is the record of what changed.
